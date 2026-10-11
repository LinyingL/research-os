"""The exchange: where proposals and their outcomes travel (DESIGN.md 12.2,
12.3; task V8 phase 3).

A proposal is an object (`researchos.proposals`); this module only stores
and lists it. Phase 3 has one implementation, a local directory
(`LocalExchange`), laid out as the shared drive of 12.2 will be, so that
phase 4 can point the same code at the OneDrive folder:

::

    <exchange>/
        提议/<member id>/<proposal id>/      the member's area
            proposal.toml                    what it is (researchos.proposals)
            files/<project-relative path>    text content of added/changed files
            ready.toml                       written LAST: the proposal is complete
        提议/<member id>/blobs/<sha256>      large or binary content, once by content
        已发布/outcomes/<proposal id>/<outcome id>/   the PI's answers
            outcome.toml
            ready.toml

**Written once, under a name used once, never edited in place** (12.2).
A proposal or an outcome is a new directory written under a temporary,
hidden name (`.<id>.tmp-<token>`), every file created exclusively and
fsynced, then renamed to its final name; `ready.toml` -- naming the
SHA-256 of the main file -- is created last, exclusively. A reader ignores
any directory without it (reported as half-written) and refuses one whose
`ready.toml` does not match. A blob is written to a temporary name and
hard-linked to `blobs/<sha256>`; an existing blob is never rewritten.

**Nothing unexpected is read.** In an area, only names of the exact
shapes Research OS writes are read (a member id; a proposal id; an outcome
id); anything else -- a sync service's conflict copy (`pr-… 2`,
`proposal-MacBook.toml`), a stray file -- is reported and never opened.
Inside a proposal, only `proposal.toml`, `ready.toml` and the files the
proposal lists are read.

The exchange directory is configured per project on each machine -- the
`shared` key of the project's entry in `<home>/projects.json` (phase 4;
`configure` / `configured`; phase 3's `<home>/exchanges.json` is still
read) -- and is never inside the project folder it serves, nor inside any
other project: a proposal's files would otherwise be scanned into the
official index before anyone approved them (12.4, "not a node until
approved"). Nor is it Research OS's home or a top-level folder (10.2).

**Signatures** (phase 4, 12.5; `researchos.signing`). A proposal or an
outcome may carry `<main file>.sig` beside its main file, inside the same
write-once directory. An exchange opened for a project (`for_project`)
carries a `Verifier` built from that project's `team.toml` (and, on a
member's machine, the PI key pinned at joining): every proposal read is
given its `check`, and an outcome whose signature does not verify, or that
is not the PI's, is reported and never counted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from researchos import paths, people, signing
from researchos.records import files as record_files

PROPOSALS_AREA = "提议"
PUBLISHED_AREA = "已发布"
OUTCOMES_DIRNAME = "outcomes"
BLOBS_DIRNAME = "blobs"
FILES_DIRNAME = "files"
PROPOSAL_FILE = "proposal.toml"
OUTCOME_FILE = "outcome.toml"
READY_FILE = "ready.toml"
PROPOSAL_SIG = PROPOSAL_FILE + signing.SIG_SUFFIX
OUTCOME_SIG = OUTCOME_FILE + signing.SIG_SUFFIX
CONFIG_FILENAME = "exchanges.json"

PROPOSAL_ID_RE = re.compile(r"^pr-[0-9a-f]{32}$")
OUTCOME_ID_RE = re.compile(r"^o-[0-9a-f]{32}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 1024 * 1024
_ZERO = bytes(_CHUNK)


class ExchangeError(Exception):
    """The exchange could not be read or written as asked. `code`:
    `not_configured`, `inside_project`, `unreachable`, `exists`,
    `no_such_proposal`, `invalid`, `half_written`, `content`, `bad_location`."""

    def __init__(self, code: str, message: str, *, message_zh: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self._zh = message_zh

    @property
    def message_zh(self) -> str:
        if self._zh:
            return self._zh
        return {
            "not_configured": "这个项目还没有设置交换目录（公盘）",
            "inside_project": "交换目录不能放在项目文件夹里",
            "unreachable": "交换目录当前无法访问",
            "exists": "同名的提议或答复已经存在",
            "no_such_proposal": "找不到这份提议",
            "invalid": "这份提议的文件格式不对",
            "half_written": "这份提议还没有写完",
            "content": "提议里的文件内容与记录的指纹不符",
            "bad_location": "公盘文件夹不能选在这里",
        }.get(self.code, "交换目录出错")


# -- the per-machine setting ----------------------------------------------------------


def _config_path() -> Path:
    return paths.researchos_home() / CONFIG_FILENAME


def _load_config() -> dict[str, str]:
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ExchangeError("unreachable", f"{_config_path()} cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise ExchangeError("unreachable", f"{_config_path()} is not a JSON object")
    return {str(k): str(v) for k, v in data.items()}


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _enclosing_project(directory: Path) -> Path | None:
    """The project folder `directory` lies in (it or an ancestor holds a
    project identity), or None."""
    current = directory.resolve()
    for folder in (current, *current.parents):
        for name in (paths.PROJECT_DIRNAME, paths.LEGACY_PROJECT_DIRNAME):
            try:
                if (folder / name / "project.toml").is_file():
                    return folder
            except OSError:
                continue
    return None


def check_location(directory: str | Path, project_root: str | Path | None) -> Path:
    """The exchange directory as an absolute path; refused inside the
    project (module docstring) and the project inside it, inside any other
    project, in Research OS's home, and at a top-level folder (10.2)."""
    directory = Path(directory).expanduser()
    if not directory.is_absolute():
        directory = directory.resolve()
    if project_root is not None and (_inside(directory, Path(project_root)) or _inside(Path(project_root), directory)):
        raise ExchangeError(
            "inside_project",
            f"the exchange {directory} and the project {project_root} must not contain one another: a proposal's "
            f"files would be scanned into the project before they were approved",
        )
    enclosing = _enclosing_project(directory)
    if enclosing is not None:
        raise ExchangeError("inside_project", f"the exchange {directory} lies inside the project {enclosing}")
    from researchos import addproject  # noqa: PLC0415 -- addproject imports the webapp, which imports this

    canonical = addproject._canonical_if_exists(directory) or str(directory.resolve())
    refused = addproject._location_refusal(str(directory), canonical)
    if refused is not None and refused.refusal is not None:
        raise ExchangeError("bad_location", f"{directory}: {refused.refusal.detail or refused.refusal.code}",
                            message_zh="公盘文件夹不能选在这里：" + refused.refusal.message)
    return directory


def configure(project_id: str, directory: str | Path, project_root: str | Path | None = None) -> Path:
    """Remember the exchange (the shared folder) of `project_id` on this
    machine, in the project's entry of `projects.json` -- registering
    `project_root` there first when it is not listed yet."""
    from researchos.webapp import registry  # noqa: PLC0415 -- the webapp imports this module

    directory = check_location(directory, project_root)
    if not registry.set_shared(project_id, str(directory)):
        if project_root is None:
            raise ExchangeError("not_configured", f"project {project_id} is not registered on this machine; "
                                                  f"name its folder to set its shared folder")
        registry.register(Path(project_root), project_id)
        if not registry.set_shared(project_id, str(directory)):
            raise ExchangeError("unreachable", f"{registry.registry_path()} cannot be written now")
    return directory


def configured(project_id: str) -> Path | None:
    from researchos.webapp import registry  # noqa: PLC0415

    entry = registry.find(project_id)
    value = (entry or {}).get("shared") or _load_config().get(project_id)
    return Path(value) if value else None


def for_project(project_id: str, project_root: str | Path | None = None, *, override: str | Path | None = None) -> "LocalExchange":
    """The exchange this machine uses for `project_id`: `override` when
    given (a command's --exchange), else the configured one."""
    directory = Path(override) if override is not None else configured(project_id)
    if directory is None:
        raise ExchangeError(
            "not_configured",
            f"no exchange is set for project {project_id} on this machine: "
            f"researchos exchange set DIR [PATH] (or --exchange DIR)",
        )
    return LocalExchange(check_location(directory, project_root), verifier=verifier_for(project_root, project_id))


def verifier_for(project_root: str | Path | None, project_id: str | None = None) -> "signing.Verifier | None":
    """The keys a reader of this project's exchange trusts (module
    docstring): the team's, the PI's replaced by the one this machine pinned
    when it joined (`researchos.share`)."""
    if project_root is None:
        return None
    team = people.read_team(project_root)
    keys = {m.id: m.key for m in team.members}
    pi = team.pi.id if team.pi is not None else None
    if project_id:
        from researchos import share  # noqa: PLC0415 -- share imports this module

        pinned = share.pinned_pi(project_id)
        if pinned is not None:
            pi = pinned["pi"]
            keys[pi] = pinned.get("pi_key")
    return signing.Verifier(keys, pi)


# -- writing once ------------------------------------------------------------------------


def _fsync_dir(directory: Path) -> None:
    record_files._fsync_dir(directory)


def _create(path: Path, data: bytes) -> None:
    """Create `path` exclusively with `data`, fsynced (never overwrites)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record_files.write_new_file(path, data)


def copy_file(source: Path, target: Path) -> str:
    """Copy `source` to a NEW file `target` (exclusive create), keeping
    runs of zero bytes as holes so a sparse file stays sparse; fsynced.
    Returns the SHA-256 of what was copied."""
    digest = hashlib.sha256()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        size = 0
        with open(source, "rb") as src:
            while True:
                chunk = src.read(_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                if chunk == _ZERO[: len(chunk)]:
                    os.lseek(fd, len(chunk), os.SEEK_CUR)
                    continue
                view = memoryview(chunk)
                while view:
                    view = view[os.write(fd, view):]
        os.ftruncate(fd, size)
        os.fsync(fd)
    finally:
        os.close(fd)
    return digest.hexdigest()


def sha256_of(path: Path) -> tuple[str, int]:
    """(sha256, size) of a file, read in chunks."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _ready_bytes(kind: str, object_id: str, main: bytes) -> bytes:
    return (
        "# 写完的标记：没有这个文件的目录还没有写完，Research OS 不读。\n"
        f'{kind} = "{object_id}"\n'
        f'sha256 = "{hashlib.sha256(main).hexdigest()}"\n'
    ).encode("utf-8")


def _write_dir_once(final: Path, contents: Iterable[tuple[str, bytes | Path]], ready: bytes,
                    expect: Mapping[str, str] | None = None) -> None:
    """The write-once discipline of the module docstring. `expect` maps a
    content path to the SHA-256 its copy must have: a source that changed
    while it was copied stops the write, and the unfinished temporary
    directory -- never published, never read -- is removed."""
    if final.exists() or final.is_symlink():
        raise ExchangeError("exists", f"{final} already exists; nothing on the exchange is written twice")
    try:
        final.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExchangeError("unreachable", f"cannot create {final.parent}: {exc}") from exc
    tmp = final.parent / f".{final.name}.tmp-{secrets.token_hex(6)}"
    tmp.mkdir()
    try:
        for rel, source in contents:
            target = tmp / rel
            if not _inside(target, tmp):
                raise ExchangeError("invalid", f"{rel!r} would be written outside {final}")
            if isinstance(source, Path):
                got = copy_file(source, target)
                if expect is not None and rel in expect and got != expect[rel]:
                    raise ExchangeError("content", f"{source} changed while it was copied (sha256 {got}, expected "
                                                   f"{expect[rel]}); nothing published")
            else:
                _create(target, source)
        _fsync_dir(tmp)
        if final.exists():
            raise ExchangeError("exists", f"{final} appeared while it was being written")
        os.rename(tmp, final)
    except BaseException:
        if tmp.exists():
            import shutil  # noqa: PLC0415

            shutil.rmtree(tmp, ignore_errors=True)
        raise
    _fsync_dir(final.parent)
    _create(final / READY_FILE, ready)
    _fsync_dir(final)


def put_blob_once(store: Path, sha: str, source: Path) -> Path:
    """`source` into `store` under its SHA-256, once: written to a
    temporary name, verified, then hard-linked to `<store>/<sha>`; an
    existing blob of the right size is kept as it is and never rewritten."""
    if not SHA_RE.match(sha):
        raise ExchangeError("invalid", f"not a content hash: {sha!r}")
    store.mkdir(parents=True, exist_ok=True)
    target = store / sha
    if target.exists():
        if target.stat().st_size != source.stat().st_size:
            raise ExchangeError("content", f"{target} exists with another size; it is never rewritten")
        return target
    tmp = store / f".{sha}.tmp-{secrets.token_hex(6)}"
    try:
        got = copy_file(source, tmp)
        if got != sha:
            raise ExchangeError("content", f"{source} changed while it was copied (sha256 {got}, expected {sha})")
        try:
            os.link(tmp, target)
        except FileExistsError:
            pass
        except OSError:
            if not target.exists():
                os.rename(tmp, target)
        _fsync_dir(store)
    finally:
        if tmp.exists():
            tmp.unlink()
    return target


# -- reading --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Problem:
    """Something in the exchange Research OS did not read, and why:
    `half_written` (no ready.toml), `unexpected` (a name of a shape it never
    writes -- a sync conflict copy, a stray file), `invalid`, `mismatch`
    (ready.toml does not match), `unverified` (a signature that does not
    verify, or an outcome not by the PI)."""

    kind: str
    path: str
    detail: str

    def payload(self) -> dict[str, str]:
        return {"kind": self.kind, "path": self.path, "detail": self.detail}


@dataclass(frozen=True)
class StoredProposal:
    id: str
    member: str
    directory: Path
    data: Mapping[str, Any]
    text: str
    blobs: Path
    raw: bytes = b""
    signature: bytes | None = None
    check: "signing.Check | None" = None

    def content_path(self, entry: Mapping[str, Any]) -> Path:
        """Where the content of one listed file is: under `files/` (text)
        or the member's blob store (large or binary)."""
        if entry.get("store") == "blob":
            sha = str(entry.get("sha256") or "")
            if not SHA_RE.match(sha):
                raise ExchangeError("invalid", f"not a content hash: {sha!r}")
            path, inside = self.blobs / sha, self.blobs
        else:
            path, inside = self.directory / FILES_DIRNAME / str(entry.get("path") or ""), self.directory / FILES_DIRNAME
        # Read only what the member wrote there: never through a link.
        if not _inside(path, inside):
            raise ExchangeError("invalid", f"{path} points outside the proposal; not read")
        step = inside
        for part in path.relative_to(inside).parts:
            step = step / part
            if step.is_symlink():
                raise ExchangeError("invalid", f"{step} is a link; not read")
        return path


@dataclass(frozen=True)
class Outcome:
    id: str
    proposal: str
    data: Mapping[str, Any]
    check: "signing.Check | None" = None

    @property
    def act(self) -> str:
        return str(self.data.get("act"))


@dataclass
class Listing:
    proposals: list[StoredProposal] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)


def _read_ready(directory: Path, kind: str, object_id: str, main: bytes) -> Problem | None:
    ready = directory / READY_FILE
    try:
        data = tomllib.loads(ready.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Problem("half_written", str(directory), "no ready.toml: still being written, or never finished")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return Problem("invalid", str(ready), f"ready.toml cannot be read: {exc}")
    if data.get(kind) != object_id or data.get("sha256") != hashlib.sha256(main).hexdigest():
        return Problem("mismatch", str(ready), f"ready.toml does not name this {kind} as written")
    return None


def _read_signature(path: Path) -> bytes | None:
    """A signature file's bytes, or None when there is none (a link is
    never followed)."""
    try:
        if path.is_symlink():
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return data if len(data) < 64 * 1024 else None


def _unexpected(directory: Path, allowed: set[str]) -> list[Problem]:
    found = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return found
    for name in names:
        if name in allowed or (name.startswith(".") and ".tmp-" in name):
            continue
        found.append(Problem("unexpected", str(directory / name), "not written by Research OS (a sync conflict copy?); not read"))
    return found


class LocalExchange:
    """The exchange as a local (or synced) directory -- the module docstring's
    layout. Every method reads or writes only under `root`."""

    def __init__(self, root: str | Path, *, verifier: "signing.Verifier | None" = None) -> None:
        self.root = Path(root)
        self.verifier = verifier

    # areas
    def member_area(self, member: str) -> Path:
        if not people.is_member_id(member):
            raise ExchangeError("invalid", f"not a member id: {member!r}")
        return self.root / PROPOSALS_AREA / member

    def outcomes_area(self, proposal_id: str) -> Path:
        if not PROPOSAL_ID_RE.match(proposal_id or ""):
            raise ExchangeError("invalid", f"not a proposal id: {proposal_id!r}")
        return self.root / PUBLISHED_AREA / OUTCOMES_DIRNAME / proposal_id

    def _require_root(self) -> None:
        if not self.root.is_dir():
            raise ExchangeError("unreachable", f"the exchange {self.root} is not a directory that can be reached now")

    # proposals
    def write_proposal(self, member: str, proposal_id: str, proposal_bytes: bytes,
                       texts: Mapping[str, Path | bytes], blobs: Mapping[str, Path],
                       signature: bytes | None = None) -> StoredProposal:
        """Write one proposal: `texts` (project-relative path -> source) go
        under `files/`, `blobs` (sha256 -> source) to the member's blob
        store first; the member's signature beside proposal.toml;
        `ready.toml` last."""
        self._require_root()
        if not PROPOSAL_ID_RE.match(proposal_id):
            raise ExchangeError("invalid", f"not a proposal id: {proposal_id!r}")
        area = self.member_area(member)
        area.mkdir(parents=True, exist_ok=True)
        for sha, source in blobs.items():
            self.put_blob(member, sha, source)
        contents: list[tuple[str, bytes | Path]] = [(PROPOSAL_FILE, proposal_bytes)]
        if signature is not None:
            contents.append((PROPOSAL_SIG, signature))
        contents += [(f"{FILES_DIRNAME}/{rel}", source) for rel, source in texts.items()]
        _write_dir_once(area / proposal_id, contents, _ready_bytes("proposal", proposal_id, proposal_bytes))
        return self.open_proposal(proposal_id, member=member)

    def put_blob(self, member: str, sha: str, source: Path) -> Path:
        """`source` into the member's blob store under its SHA-256, once:
        an existing blob of the right size is kept as it is."""
        return put_blob_once(self.member_area(member) / BLOBS_DIRNAME, sha, source)

    def _read_proposal_dir(self, member: str, directory: Path) -> tuple[StoredProposal | None, list[Problem]]:
        problems = _unexpected(directory, {PROPOSAL_FILE, PROPOSAL_SIG, READY_FILE, FILES_DIRNAME})
        try:
            raw = (directory / PROPOSAL_FILE).read_bytes()
        except FileNotFoundError:
            return None, problems + [Problem("half_written", str(directory), "no proposal.toml")]
        except OSError as exc:
            return None, problems + [Problem("invalid", str(directory), f"proposal.toml cannot be read: {exc}")]
        problem = _read_ready(directory, "proposal", directory.name, raw)
        if problem is not None:
            return None, problems + [problem]
        try:
            text = raw.decode("utf-8")
            data = tomllib.loads(text)
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            return None, problems + [Problem("invalid", str(directory / PROPOSAL_FILE), str(exc))]
        if data.get("id") != directory.name or data.get("by") != member:
            return None, problems + [Problem("invalid", str(directory / PROPOSAL_FILE),
                                             "its id or its author does not match where it is stored")]
        signature = _read_signature(directory / PROPOSAL_SIG)
        check = self.verifier.check(member, raw, signature) if self.verifier is not None else None
        stored = StoredProposal(directory.name, member, directory, data, text, self.member_area(member) / BLOBS_DIRNAME,
                                raw=raw, signature=signature, check=check)
        return stored, problems

    def list_proposals(self, member: str | None = None) -> Listing:
        """Every complete proposal (of `member`, or of everyone), and what
        was not read and why."""
        listing = Listing()
        area = self.root / PROPOSALS_AREA
        if not self.root.is_dir():
            raise ExchangeError("unreachable", f"the exchange {self.root} cannot be reached now")
        if not area.is_dir():
            return listing
        for name in sorted(os.listdir(area)):
            path = area / name
            if not people.is_member_id(name) or not path.is_dir():
                if not (name.startswith(".") and ".tmp-" in name):
                    listing.problems.append(Problem("unexpected", str(path), "not a member's area; not read"))
                continue
            if member is not None and name != member:
                continue
            for entry in sorted(os.listdir(path)):
                sub = path / entry
                if entry == BLOBS_DIRNAME or (entry.startswith(".") and ".tmp-" in entry):
                    continue
                if not PROPOSAL_ID_RE.match(entry) or not sub.is_dir():
                    listing.problems.append(Problem("unexpected", str(sub), "not a proposal Research OS wrote; not read"))
                    continue
                stored, problems = self._read_proposal_dir(name, sub)
                listing.problems.extend(problems)
                if stored is not None:
                    listing.proposals.append(stored)
        return listing

    def open_proposal(self, proposal_id: str, *, member: str | None = None) -> StoredProposal:
        if not PROPOSAL_ID_RE.match(proposal_id or ""):
            raise ExchangeError("no_such_proposal", f"not a proposal id: {proposal_id!r}")
        area = self.root / PROPOSALS_AREA
        members = [member] if member is not None else (
            sorted(n for n in os.listdir(area) if people.is_member_id(n)) if area.is_dir() else []
        )
        for name in members:
            directory = area / name / proposal_id
            if directory.is_dir():
                stored, problems = self._read_proposal_dir(name, directory)
                if stored is None:
                    problem = problems[-1]
                    raise ExchangeError("half_written" if problem.kind == "half_written" else "invalid",
                                        f"proposal {proposal_id}: {problem.detail}")
                return stored
        raise ExchangeError("no_such_proposal", f"no proposal {proposal_id} in {self.root}")

    # outcomes
    def write_outcome(self, proposal_id: str, outcome_id: str, outcome_bytes: bytes,
                      signature: bytes | None = None) -> Outcome:
        self._require_root()
        if not OUTCOME_ID_RE.match(outcome_id):
            raise ExchangeError("invalid", f"not an outcome id: {outcome_id!r}")
        area = self.outcomes_area(proposal_id)
        contents: list[tuple[str, bytes | Path]] = [(OUTCOME_FILE, outcome_bytes)]
        if signature is not None:
            contents.append((OUTCOME_SIG, signature))
        _write_dir_once(area / outcome_id, contents, _ready_bytes("outcome", outcome_id, outcome_bytes))
        return Outcome(outcome_id, proposal_id, tomllib.loads(outcome_bytes.decode("utf-8")))

    def outcomes(self, proposal_id: str) -> tuple[list[Outcome], list[Problem]]:
        """The complete outcomes of a proposal, in the order of their `at`
        (for reading only: whether a proposal is decided never depends on
        the order)."""
        area = self.outcomes_area(proposal_id)
        found: list[Outcome] = []
        problems: list[Problem] = []
        if not area.is_dir():
            return found, problems
        for name in sorted(os.listdir(area)):
            path = area / name
            if name.startswith(".") and ".tmp-" in name:
                continue
            if not OUTCOME_ID_RE.match(name) or not path.is_dir():
                problems.append(Problem("unexpected", str(path), "not an outcome Research OS wrote; not read"))
                continue
            problems += _unexpected(path, {OUTCOME_FILE, OUTCOME_SIG, READY_FILE})
            try:
                raw = (path / OUTCOME_FILE).read_bytes()
            except OSError:
                problems.append(Problem("half_written", str(path), "no outcome.toml"))
                continue
            problem = _read_ready(path, "outcome", name, raw)
            if problem is not None:
                problems.append(problem)
                continue
            try:
                data = tomllib.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                problems.append(Problem("invalid", str(path / OUTCOME_FILE), str(exc)))
                continue
            if data.get("id") != name or data.get("proposal") != proposal_id:
                problems.append(Problem("invalid", str(path / OUTCOME_FILE), "does not name this outcome and proposal"))
                continue
            check = None
            if self.verifier is not None:
                if self.verifier.pi is not None and data.get("by") != self.verifier.pi:
                    problems.append(Problem("unverified", str(path / OUTCOME_FILE),
                                            f"not written by the PI ({self.verifier.pi}); not counted"))
                    continue
                check = self.verifier.check(str(data.get("by")), raw, _read_signature(path / OUTCOME_SIG))
                if not check.trusted:
                    problems.append(Problem("unverified", str(path / OUTCOME_FILE),
                                            f"{check.state}: {check.message_zh}; not counted"))
                    continue
            found.append(Outcome(name, proposal_id, data, check))
        found.sort(key=lambda o: str(o.data.get("at") or ""))
        return found, problems

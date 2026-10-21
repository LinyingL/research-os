"""The before-picture, the after-run comparison and what is protected
(DESIGN.md 17.5, 17.6).

**The before-picture.** Before an agent's program starts, `take` records
every file of the project folder -- size, modification time, a SHA-256 for
files up to `HASH_LIMIT` -- and copies every file up to `COPY_LIMIT` into
the run's folder (`COPY_BUDGET` per run at most). After the run the same
function takes the after-picture and `compare` names what was created,
modified and deleted ("a touched file with the same content is not
changed": two entries with the same hash are the same file whatever their
modification times). The copies are what 撤回本次运行 restores from (17.6).

What `take` never does: follow a symbolic link (a link is recorded by its
target and compared by it), open a file that is only in the cloud (marked
`dataless`; reading it would download it), open anything but a regular file
(a FIFO would block), or walk into `.git/`, `.researchos/backups/` and the
usual caches. Hidden files and folders are otherwise included on purpose:
`.researchos/*.toml` is exactly what an agent must not touch (17.5 rule 1).
A project with more than `FILE_LIMIT` files is recorded up to that many and
marked `partial`: a partial picture cannot show every creation, and the
caller says so.

**What is protected** (17.5 rules 1, 3, 5), decided from the before-picture:
the folders `.researchos`, `.rce` and `.git`; every data file that existed
before the run; the attempt table (its path read from `attempts.toml`) and
`research/tasks.json`. `protected` is also what the hook enforces during the
run (`guard`), and `violations` is the after-run check that catches what the
hook cannot see (OpenCode has no hook; a script can overwrite a file behind
Claude Code's back). Git's state is its `HEAD` and branch; `.git/` itself is
not walked.
"""

from __future__ import annotations

import hashlib
import logging
import os
import posixpath
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

from researchos import names, paths

logger = logging.getLogger(__name__)

SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv", ".ipynb_checkpoints",
                       ".Rproj.user", "_cache", ".mypy_cache", ".pytest_cache", ".tox", ".cache"})
SKIP_PATHS = frozenset({f"{names.PROJECT_DIRNAME}/backups", f"{names.LEGACY_PROJECT_DIRNAME}/backups"})
COPY_LIMIT = 16 * 1024 * 1024
COPY_BUDGET = 1024 * 1024 * 1024
#: DESIGN 17.5: a SHA-256 for files up to 64 MB; only the first 16 MB are also copied.
HASH_LIMIT = 64 * 1024 * 1024
FILE_LIMIT = 50_000
DATA_SUFFIXES = frozenset({".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".feather", ".rds", ".rdata", ".dta",
                           ".sav", ".json", ".h5", ".pkl"})
TASKS_PATH = "research/tasks.json"
PROTECTED_DIRS = (names.PROJECT_DIRNAME, names.LEGACY_PROJECT_DIRNAME, ".git")
GIT_TIMEOUT_S = 10
_CHUNK = 1024 * 1024


# -- git ------------------------------------------------------------------------------------------


def _git(root: Path, *args: str) -> str | None:
    """One git answer (stdout stripped), or None on any failure."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    argv = ["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args]
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=GIT_TIMEOUT_S,
                              env=env, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace").strip() or None


def git_state(root: Path) -> dict[str, str | None] | None:
    """`{"head", "branch"}` of the project's git repository (either may be
    None: no commit yet, detached head, git unavailable); None when
    `root/.git` does not exist."""
    root = Path(root)
    if not os.path.lexists(root / ".git"):
        return None
    return {"head": _git(root, "rev-parse", "HEAD"), "branch": _git(root, "symbolic-ref", "-q", "--short", "HEAD")}


# -- entries ----------------------------------------------------------------------------------------


def _hash_and_copy(full: str, size: int, copy_dest: str | None) -> tuple[str | None, bool, int]:
    """Read `full` once: its SHA-256 (None when unreadable or not hashed),
    whether it was copied to `copy_dest`, and the bytes copied."""
    digest = hashlib.sha256()
    out = None
    copied = False
    try:
        with open(full, "rb") as src:
            if copy_dest is not None:
                os.makedirs(os.path.dirname(copy_dest), exist_ok=True)
                out = open(copy_dest, "wb")  # noqa: SIM115 -- closed in the finally below
            total = 0
            while True:
                chunk = src.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
                if out is not None:
                    if total > COPY_LIMIT:  # grew while being read: not a faithful copy
                        out.close()
                        out = None
                        _unlink_quietly(copy_dest)
                        copy_dest = None
                    else:
                        out.write(chunk)
            if out is not None:
                out.close()
                out = None
                shutil.copystat(full, copy_dest)  # type: ignore[arg-type]
                copied = True
        return digest.hexdigest(), copied, total if copied else 0
    except OSError:
        if out is not None:
            out.close()
        if copy_dest is not None:
            _unlink_quietly(copy_dest)
        return None, False, 0


def _unlink_quietly(path: str | None) -> None:
    if path is not None:
        try:
            os.unlink(path)
        except OSError:
            pass


def _entry(full: str, st: os.stat_result, *, copy_dest: str | None = None,
           budget_left: int = 0) -> tuple[dict[str, Any], int]:
    """The recorded entry for one path (from `lstat`) and the bytes copied.
    Links are recorded by target and never followed; a regular file in the
    cloud is marked and never opened."""
    entry: dict[str, Any] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    if stat.S_ISLNK(st.st_mode):
        try:
            entry["link"] = os.readlink(full)
        except OSError:
            entry["link"] = ""
        return entry, 0
    if not stat.S_ISREG(st.st_mode):
        return entry, 0
    if paths.is_dataless(full):
        entry["dataless"] = True
        return entry, 0
    if st.st_size > HASH_LIMIT:
        return entry, 0
    want_copy = copy_dest is not None and st.st_size <= COPY_LIMIT and st.st_size <= budget_left
    digest, copied, nbytes = _hash_and_copy(full, st.st_size, copy_dest if want_copy else None)
    if digest is not None:
        entry["sha256"] = digest
    if copied:
        entry["copied"] = True
    return entry, nbytes


def fingerprint(root: Path, rel: str) -> dict[str, Any] | None:
    """One file's entry as `take` records it (with a hash when small enough),
    or None when it is missing or `rel` is not a path inside `root`."""
    if not isinstance(rel, str) or not rel or posixpath.isabs(rel) or ".." in rel.split("/"):
        return None
    full = os.path.join(root, *rel.split("/"))
    try:
        st = os.lstat(full)
    except OSError:
        return None
    if stat.S_ISDIR(st.st_mode):
        return None
    return _entry(full, st)[0]


def take(root: Path, copy_to: Path | None = None) -> dict[str, Any]:
    """The before- (or after-) picture of the project folder; see the module
    docstring. Returns `{"files": {rel: entry}, "partial", "copied_bytes",
    "not_copied", "git"}` where an entry is `{"size", "mtime_ns"}` plus
    `sha256`, `dataless`, `link` and `copied` as they apply. Copies go to
    `copy_to/<rel>`."""
    root = Path(root)
    base = str(root)
    copy_base = str(copy_to) if copy_to is not None else None
    files: dict[str, dict[str, Any]] = {}
    copied_bytes = 0
    not_copied = 0
    partial = False

    def add(rel: str, full: str) -> bool:
        """Record one path; False when the file limit was reached."""
        nonlocal copied_bytes, not_copied
        if len(files) >= FILE_LIMIT:
            return False
        try:
            st = os.lstat(full)
        except OSError:
            return True  # vanished while walking
        dest = os.path.join(copy_base, *rel.split("/")) if copy_base is not None else None
        entry, nbytes = _entry(full, st, copy_dest=dest, budget_left=COPY_BUDGET - copied_bytes)
        copied_bytes += nbytes
        if stat.S_ISREG(st.st_mode) and not entry.get("copied"):
            not_copied += 1
        files[rel] = entry
        return True

    stopped = False
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        reldir = os.path.relpath(dirpath, base).replace(os.sep, "/")
        reldir = "" if reldir == "." else reldir
        keep = []
        links = []
        for name in sorted(dirnames):
            rel = f"{reldir}/{name}" if reldir else name
            if name in SKIP_DIRS or rel in SKIP_PATHS:
                continue
            if os.path.islink(os.path.join(dirpath, name)):
                links.append(name)  # a link to a folder is one entry, never walked
            else:
                keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames + links):
            rel = f"{reldir}/{name}" if reldir else name
            if not add(rel, os.path.join(dirpath, name)):
                partial = stopped = True
                break
        if stopped:
            break
    return {"files": files, "partial": partial, "copied_bytes": copied_bytes, "not_copied": not_copied,
            "git": git_state(root)}


# -- comparing -----------------------------------------------------------------------------------------


def same(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """Whether two entries are the same file: both absent; or the same link
    target; or equal hashes; else equal size and modification time."""
    if a is None or b is None:
        return a is None and b is None
    if "link" in a or "link" in b:
        return "link" in a and "link" in b and a["link"] == b["link"]
    if a.get("sha256") and b.get("sha256"):
        return a["sha256"] == b["sha256"]
    return a.get("size") == b.get("size") and a.get("mtime_ns") == b.get("mtime_ns")


def _file_map(picture: Mapping[str, Any]) -> Mapping[str, Any]:
    """The `{rel: entry}` of a `take` result (a bare map is accepted too)."""
    inner = picture.get("files")
    return inner if isinstance(inner, dict) and "partial" in picture else picture


def compare(before: Mapping[str, Any], after: Mapping[str, Any], *, exclude: Iterable[str] = ()) -> dict[str, list[str]]:
    """`{"created", "modified", "deleted"}` (sorted rel paths) between two
    pictures, ignoring the paths in `exclude`."""
    old, new = _file_map(before), _file_map(after)
    skip = set(exclude)
    created = sorted(r for r in new if r not in old and r not in skip)
    deleted = sorted(r for r in old if r not in new and r not in skip)
    modified = sorted(r for r in new if r in old and r not in skip and not same(old[r], new[r]))
    return {"created": created, "modified": modified, "deleted": deleted}


# -- protection ------------------------------------------------------------------------------------------


def attempts_table(root: Path) -> str | None:
    """The attempt table's path relative to the project, read from
    `attempts.toml`; None when there is no readable config or the file it
    names is outside the project. Never raises."""
    try:
        from researchos.ingest import attempts  # noqa: PLC0415 -- heavy; only the engine needs it

        cfg_path = attempts.config_path(root)
        if paths.is_dataless(cfg_path):
            return None
        name = attempts.load_config(Path(root)).file
        if not isinstance(name, str) or not name.strip() or "\0" in name:
            return None
        candidate = Path(name.strip())
        if candidate.is_absolute():
            try:
                candidate = candidate.resolve().relative_to(Path(root).resolve())
            except (OSError, ValueError):
                return None
        rel = posixpath.normpath(candidate.as_posix())
        if rel in (".", "") or rel == ".." or rel.startswith("../") or posixpath.isabs(rel):
            return None
        return rel
    except Exception:  # noqa: BLE001 -- documented: any trouble reading the config means "unknown"
        return None


def protected(root: Path, before: Mapping[str, Any]) -> dict[str, Any]:
    """What an agent must not change (17.5 rules 1, 3, 5), from the
    before-picture: `{"dirs", "files", "data", "records", "root"}`. `files`
    is `data` plus `records` (the attempt table and `research/tasks.json`,
    listed even if absent: creating them is a write too)."""
    records = [r for r in (attempts_table(root), TASKS_PATH) if r]
    records = list(dict.fromkeys(records))
    data = sorted(rel for rel in _file_map(before)
                  if posixpath.splitext(rel)[1].lower() in DATA_SUFFIXES and rel not in records)
    return {"dirs": list(PROTECTED_DIRS), "files": sorted({*data, *records}), "data": data,
            "records": records, "root": str(root)}


def violations(changes: Mapping[str, Any], prot: Mapping[str, Any], *, git_before: Any, git_after: Any,
               allowed: Mapping[str, set[str]], after: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    """The out-of-bounds changes among `changes` (a `compare` result), each
    `{"path", "reason"}`: `data_changed` / `data_deleted` (a data file that
    existed before), `record_changed` / `record_deleted` (the attempt table,
    `research/tasks.json`), `researchos_changed` (anything under
    `.researchos` or `.rce`) and `git_changed` (path `.git`: HEAD or branch
    differ, or the repository is gone).

    `allowed` maps a record's path to the hashes it may legitimately end
    with (`research/tasks.json` as the agent tools wrote it). The hash of a
    changed record is looked up in `after` (a `take` result) when given,
    else read from the project folder named in `prot["root"]`; a record
    whose hash cannot be determined is a violation."""
    dirs = {d.casefold() for d in prot.get("dirs", PROTECTED_DIRS) if d != ".git"}
    data = set(prot.get("data", ()))
    records = set(prot.get("records", ()))
    found: list[dict[str, str]] = []

    def after_sha(rel: str) -> str | None:
        if after is not None:
            entry = _file_map(after).get(rel)
        elif prot.get("root"):
            entry = fingerprint(Path(prot["root"]), rel)
        else:
            entry = None
        return entry.get("sha256") if entry else None

    for kind in ("created", "modified", "deleted"):
        for rel in changes.get(kind, ()):
            if rel.split("/", 1)[0].casefold() in dirs:
                found.append({"path": rel, "reason": "researchos_changed"})
            elif rel in records:
                if kind == "deleted":
                    found.append({"path": rel, "reason": "record_deleted"})
                else:
                    sha = after_sha(rel)
                    if sha is None or sha not in allowed.get(rel, ()):
                        found.append({"path": rel, "reason": "record_changed"})
            elif rel in data:
                if kind == "deleted":
                    found.append({"path": rel, "reason": "data_deleted"})
                elif kind == "modified":
                    found.append({"path": rel, "reason": "data_changed"})
    if git_before is not None and git_after != git_before:
        found.append({"path": ".git", "reason": "git_changed"})
    return found

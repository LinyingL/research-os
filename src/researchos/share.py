"""The shared drive (公盘): publishing, joining, syncing and the team
(DESIGN.md 12.1, 12.2, 12.3, 12.5; task V8 phase 4).

The shared folder is a path the PI chooses -- a OneDrive folder shared with
the team, or any synced folder -- set per machine with `researchos share
set` (the `shared` key of the project's entry in `~/.researchos/projects.json`,
never in the project). It is the exchange of `researchos.exchange`, and
holds:

::

    <shared>/
        已发布/                                 written only by the PI's Research OS
            snapshots/<seq>/
                manifest.toml                   every file of the official version
                manifest.toml.sig               the PI's signature (12.5)
                files/<path>                    text files changed since the previous
                                                snapshot, and the record files
                ready.toml                      written LAST
            blobs/<sha256>                      large or binary files, once by content
            outcomes/<proposal id>/<outcome id>/   the PI's answers, signed
        提议/<member id>/<proposal id>/         each member's own proposals, signed
        提议/<member id>/blobs/<sha256>

**Written once, never edited in place.** Every snapshot is a new directory
written under a temporary name in the same parent, its files written and
fsynced, renamed into place, `ready.toml` created last; a reader ignores
anything without it and reports -- never reads -- a name of a shape Research
OS does not write (a sync service's conflict copy). Nothing published is
ever modified; the deletions are the PI's own: 「清理旧文件」 (`prune_blobs`,
12.2), blobs no snapshot from a chosen date on references, and the repair
of a blob found damaged (`repair_blobs`, 12.12), which the next publish
writes again -- each shown first, removed only when confirmed. A member's
area `提议/<member id>/` is made once by the PI's `team add` (or the first
publish after the shared folder is set), never touched again.

**One shared folder, one project.** A folder whose snapshots name another
project is refused for publishing, and a join that finds snapshots of more
than one project asks which (`project`); a manifest's project id must have
the shape of one (it names a folder on the member's machine).

**Notes, link judgments and hand-drawn links travel without approval**
(12.4, 12.12). The links -- added, and removed as explicit removal
entries -- go in the same packet and are applied to the official
`mappings.toml` by `researchos.ingest.mappings.apply_received`; canvas
arrangements never travel (a member's own is kept at sync). A member's
`notes.toml` and `judgements.toml` are append-only ledgers, never merged
as files: 「同步」 writes the member's own new entries -- signed, once -- to
their area as a packet (`提议/<member>/n-…/entries.toml`), and the PI's
Research OS appends them to the official ledgers when it publishes
(`collect_entries`), each with the member's `by` and `recovered_from`
naming the member's entry, so it is carried once. On the member's side the
official ledger replaces theirs only once it holds every entry theirs
does; until then theirs is kept, and nothing is put beside it.

**A snapshot's manifest** (TOML)::

    format = 1
    project = "<project id>"
    seq = 4
    prev_seq = 3                       # the previous good snapshot
    prev_manifest_sha256 = "…"         # its manifest.toml's SHA-256: a chain
    at = "2026-10-11T18:00:00+02:00"
    by = "<the PI's member id>"
    [[file]]
    path = "steps/16-build.py"
    size = 120
    sha256 = "…"
    store = "snapshot"                 # under snapshots/<in>/files/<path>
    in = 2
    [[file]]
    path = "data/big.bin"
    size = 209715200
    sha256 = "…"
    store = "blob"                     # under blobs/<sha256>

It lists every file of the official version: the files a scan reads and
the record files under `.researchos/` (identity, the attempt-table
configuration, mappings, team, ledgers, cards) -- not the index, which is
not in the project, and not `.researchos/backups/`. A text file (UTF-8, no
NUL, at most 5 MB) is stored in the snapshot that first carried its
content; a record file is carried in every snapshot; any other file goes
once into `blobs/`. A file that is only in the cloud on the PI's machine is
never read: publishing is refused, naming it.

**Trust** (12.5, `researchos.signing`). The PI signs each manifest and
each outcome; a member signs each proposal. A member's Research OS trusts
the PI's key it found in the FIRST published snapshot when it joined
(trust on first use -- the fingerprint is shown to compare with the PI out
of band) and from then on accepts a snapshot only if it is the PI's, its
signature verifies under that key, its `team.toml` still names that key
for the PI, and it links by hash to the last good snapshot. A snapshot that
fails any of this is refused, reported, and skipped; the last good one is
kept.

**Joining and syncing** (12.3). `join` materialises a working copy from
the latest good snapshot (every file verified by hash; written under a
temporary name and renamed into place at the end) and records its base
(`researchos.proposals.write_base`) and the trust (`<home>/working/<project
id>/share.json`). `sync` brings it to the latest good snapshot: a file the
member has not touched is updated; one the member changed that the official
version also changed is left as the member's, and the official one is put
beside it as `<name>.official.<ext>` and listed -- never a merge.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from researchos import exchange as ex_mod
from researchos import history, paths, people, signing
from researchos.exchange import ExchangeError, Problem
from researchos.records import files as record_files
from researchos.records.identity import IdentityState, read_identity
from researchos.records.situation import READ_NOW, write_guard

logger = logging.getLogger(__name__)

SNAPSHOTS_DIRNAME = "snapshots"
MANIFEST_FILE = "manifest.toml"
MANIFEST_SIG = MANIFEST_FILE + signing.SIG_SUFFIX
STATE_FILENAME = "share.json"
CACHE_FILENAME = "share-fingerprints.json"
FORMAT = 1
STORE_SNAPSHOT = "snapshot"
STORE_BLOB = "blob"
OFFICIAL_INFIX = ".official"
#: The record files a snapshot carries (besides `variables/`).
RECORD_FILES = ("project.toml", "attempts.toml", "mappings.toml", "team.toml", "judgements.toml", "notes.toml",
                "decisions.toml", "canvas.json")
VARIABLES_DIRNAME = "variables"
_SEQ_RE = re.compile(r"^[1-9][0-9]{0,8}$")
_TMP_SEQ_RE = re.compile(r"^\.([1-9][0-9]{0,8})\.tmp-")
_MANIFEST_HEADER = "# 正式版本的一个快照：每个文件的路径、大小和指纹。由 PI 的 Research OS 写入并签名，写成后不再改动。\n"
_TEAM_HEADER = "# 项目成员。由 PI 的 Research OS 写入（researchos team init / team add），只追加，不改写。\n"

MESSAGES = {
    "not_configured": "这个项目还没有设置公盘文件夹（researchos share set）",
    "unreachable": "公盘文件夹当前无法访问",
    "no_team": "这个项目还没有成员名单：PI 先运行 researchos team init",
    "team_unreadable": "成员名单（team.toml）当前无法读取",
    "not_pi": "只有 PI 可以发布快照、管理成员",
    "no_me": "请先设置“我是谁”（researchos me set）",
    "team_exists": "这个项目已经有成员名单了",
    "member_exists": "这位成员已经在名单里",
    "invalid": "内容不对",
    "dataless": "有文件只在云端：先下载，才能发布",
    "waiting": "公盘上有文件还在从云端下载，稍后再同步",
    "nothing_published": "公盘里还没有已发布的快照",
    "untrusted": "公盘上的快照都无法验证，没有可以使用的版本",
    "not_empty": "工作副本要放在一个空文件夹里",
    "not_joined": "这个文件夹不是从公盘加入的工作副本",
    "cannot_sign": "无法签名",
    "content": "公盘上的文件内容与快照记录的指纹不符",
    "other_project": "这个公盘文件夹里已经有另一个项目的快照：每个项目用自己的公盘文件夹",
    "ambiguous_project": "这个公盘文件夹里有不止一个项目的快照，请指明要加入哪一个",
    "damaged": "公盘上有一个已发布的文件损坏了（内容与它的名字不符）：PI 先修复（researchos share repair），下次发布会重写它",
    "not_in_team": "你还不是这个项目的成员：请 PI 把你加入团队，然后同步",
    "changed_since_shown": "公盘上的内容在你查看之后变了：请重新查看，再加入",
}

#: The ledgers whose entries a member's sync carries, without approval
#: (12.4): file name -> TOML table.
TRAVELLING = {"notes.toml": "note", "judgements.toml": "judgement"}
#: 12.12: hand-drawn links travel the same way (`researchos.ingest.mappings`,
#: "a member's links travel"): the packet's `[[mapping]]` (added) and
#: `[[mapping_removed]]` (removed) tables.
MAPPINGS_NAME = "mappings.toml"
PACKET_ADDED, PACKET_REMOVED = "mapping", "mapping_removed"
#: Record files a member's own changes travel in packets, never in a proposal.
TRAVELLING_FILES = frozenset({*TRAVELLING, MAPPINGS_NAME})
#: 12.12: canvas arrangements do not travel: each person keeps their own.
CANVAS_NAME = "canvas.json"
_PACKET_HEADER = ("# 成员的笔记、连接判断和手工连线：不需要批准，PI 的 Research OS 收进正式版本时保留 by。"
                  "由 Research OS 写入，写成后不再改动。\n")


class ShareError(Exception):
    """Nothing (more) was written. `code` names the cause (`MESSAGES`);
    `details` carries lists the page shows."""

    def __init__(self, code: str, message: str, *, message_zh: str | None = None,
                 details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message_zh = message_zh or MESSAGES.get(code, "公盘操作没有完成")
        self.details = dict(details or {})


def _now_iso(now: Callable[[], datetime] | None = None) -> str:
    moment = (now or (lambda: datetime.now().astimezone()))()
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.isoformat(timespec="seconds")


def _me() -> str:
    try:
        member = people.current_member_id()
    except people.PeopleError as exc:
        raise ShareError("no_me", str(exc), message_zh=exc.message_zh) from exc
    if member is None:
        raise ShareError("no_me", "who you are is not set on this machine: researchos me set --name N --email E")
    return member


def _project_id(root: Path) -> str:
    got = read_identity(root)
    if got.state is not IdentityState.PRESENT or got.identity is None:
        raise ShareError("invalid", f"{root} has no readable project identity", message_zh="项目身份文件无法读取")
    return got.identity.id


def _signing_refused(exc: signing.SigningError) -> ShareError:
    return ShareError("cannot_sign", str(exc), message_zh=exc.message_zh)


# -- where things are ---------------------------------------------------------------------------


def published_dir(shared: Path) -> Path:
    return Path(shared) / ex_mod.PUBLISHED_AREA


def snapshots_dir(shared: Path) -> Path:
    return published_dir(shared) / SNAPSHOTS_DIRNAME


def blobs_dir(shared: Path) -> Path:
    return published_dir(shared) / ex_mod.BLOBS_DIRNAME


def shared_for(project_id: str, project_root: Path | None, override: str | Path | None = None) -> Path:
    """The shared folder this machine uses for the project (checked where
    it may be, `researchos.exchange.check_location`)."""
    try:
        return ex_mod.for_project(project_id, project_root, override=override).root
    except ExchangeError as exc:
        raise ShareError("not_configured" if exc.code == "not_configured" else "invalid", str(exc),
                         message_zh=exc.message_zh) from exc


# -- paths in a manifest ------------------------------------------------------------------------


def clean_path(rel: Any) -> str:
    """A manifest path: project-relative, normalised, no hidden part except
    a leading `.researchos/` naming a record file or the variable cards.
    Raises ValueError."""
    if not isinstance(rel, str) or not rel or any(ch in rel for ch in "\x00\r\n\\"):
        raise ValueError(f"not a project-relative path: {rel!r}")
    if posixpath.normpath(rel) != rel or rel.startswith("/") or rel == ".." or rel.startswith("../"):
        raise ValueError(f"not a clean project-relative path: {rel!r}")
    parts = rel.split("/")
    hidden = [i for i, part in enumerate(parts) if part.startswith(".")]
    if hidden:
        if hidden != [0] or parts[0] != paths.PROJECT_DIRNAME or len(parts) < 2:
            raise ValueError(f"{rel!r}: a hidden path is not part of a snapshot")
        if not ((len(parts) == 2 and parts[1] in RECORD_FILES) or (parts[1] == VARIABLES_DIRNAME and len(parts) > 2)):
            raise ValueError(f"{rel!r} is not a record file a snapshot carries")
    return rel


def is_record(rel: str) -> bool:
    return rel.startswith(paths.PROJECT_DIRNAME + "/")


# -- the official version's files ----------------------------------------------------------------


def _record_rels(root: Path) -> list[str]:
    project_dir = paths.project_dir(root)
    found = [f"{paths.PROJECT_DIRNAME}/{name}" for name in RECORD_FILES
             if (project_dir / name).is_file() and not (project_dir / name).is_symlink()]
    variables = project_dir / VARIABLES_DIRNAME
    if variables.is_dir() and not variables.is_symlink():
        for dirpath, dirnames, filenames in os.walk(variables, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                full = Path(dirpath) / name
                if name.startswith(".") or full.is_symlink():
                    continue
                found.append(f"{paths.PROJECT_DIRNAME}/{full.relative_to(project_dir).as_posix()}")
    return found


def snapshot_rels(root: Path) -> list[str]:
    """Every path a snapshot of `root` lists (module docstring)."""
    from researchos import proposals  # noqa: PLC0415 -- proposals imports this module lazily

    rels = set(proposals.project_files(root)) | set(_record_rels(root))
    out = []
    for rel in sorted(rels):
        try:
            out.append(clean_path(rel))
        except ValueError:
            logger.info("not in a snapshot: %s", rel)
    return out


def official_files(root: Path, project_id: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """({path: {sha256, size}}, [paths only in the cloud]). A file whose
    size, modification time and inode are what the last look saw is not
    read again (`<home>/graphs/<id>/share-fingerprints.json`)."""
    cache_path = paths.index_dir(project_id) / CACHE_FILENAME
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    files: dict[str, dict[str, Any]] = {}
    dataless: list[str] = []
    fresh: dict[str, list[Any]] = {}
    for rel in snapshot_rels(root):
        full = root / rel
        try:
            st = full.lstat()
        except OSError:
            continue
        if not full.is_file() or full.is_symlink():
            continue
        if paths.is_dataless(full):
            dataless.append(rel)
            continue
        stamp = f"{st.st_size}:{st.st_mtime_ns}:{st.st_ino}"
        cached = cache.get(rel)
        if isinstance(cached, list) and len(cached) == 2 and cached[0] == stamp:
            sha, size = cached[1], st.st_size
        else:
            sha, size = ex_mod.sha256_of(full)
        files[rel] = {"sha256": sha, "size": size}
        fresh[rel] = [stamp, sha]
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        record_files.durable_write(cache_path, (json.dumps(fresh, ensure_ascii=False) + "\n").encode("utf-8"))
    except OSError as exc:  # a cache, never a reason to fail
        logger.info("fingerprint cache not written: %s", exc)
    return files, dataless


# -- reading snapshots -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    seq: int
    directory: Path
    raw: bytes
    data: Mapping[str, Any]
    signature: bytes | None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def files(self) -> dict[str, dict[str, Any]]:
        return {f["path"]: dict(f) for f in self.data.get("file", [])}

    def payload(self) -> dict[str, Any]:
        return {"seq": self.seq, "at": self.data.get("at"), "by": self.data.get("by"), "sha256": self.sha256,
                "files": len(self.data.get("file", [])), "signed": self.signature is not None,
                "note": self.data.get("note")}


def validate_manifest(data: Mapping[str, Any], seq: int) -> None:
    """A manifest as read from the shared drive. Raises ValueError."""
    if data.get("format") != FORMAT:
        raise ValueError(f"format {data.get('format')!r} is not one this Research OS reads")
    if data.get("seq") != seq:
        raise ValueError(f"seq {data.get('seq')!r} is not the snapshot's number {seq}")
    for name in ("project", "at"):
        if not isinstance(data.get(name), str) or not data[name]:
            raise ValueError(f"{name!r} must be text")
    from researchos.records.lock import PROJECT_ID_RE  # noqa: PLC0415

    if not PROJECT_ID_RE.match(data["project"]):
        # It names a folder on every member's machine (working/<id>/).
        raise ValueError(f"'project' is not a project id: {data['project']!r}")
    if not people.is_member_id(data.get("by")):
        raise ValueError("'by' must name the PI")
    prev_seq, prev_sha = data.get("prev_seq"), data.get("prev_manifest_sha256")
    if (prev_seq is None) != (prev_sha is None):
        raise ValueError("prev_seq and prev_manifest_sha256 come together")
    if prev_seq is not None and (not isinstance(prev_seq, int) or not 0 < prev_seq < seq
                                 or not ex_mod.SHA_RE.match(str(prev_sha))):
        raise ValueError("the previous snapshot is not named properly")
    files = data.get("file", [])
    if not isinstance(files, list):
        raise ValueError("files are written as [[file]] tables")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("files are written as [[file]] tables")
        rel = clean_path(item.get("path"))
        if rel in seen:
            raise ValueError(f"{rel} is listed twice")
        seen.add(rel)
        if not isinstance(item.get("size"), int) or item["size"] < 0 or not ex_mod.SHA_RE.match(str(item.get("sha256"))):
            raise ValueError(f"{rel}: a file carries its size and sha256")
        if item.get("store") == STORE_SNAPSHOT:
            if not isinstance(item.get("in"), int) or not 0 < item["in"] <= seq:
                raise ValueError(f"{rel}: 'in' must name a snapshot up to this one")
        elif item.get("store") != STORE_BLOB:
            raise ValueError(f"{rel}: store must be snapshot or blob")


def read_snapshots(shared: Path) -> tuple[list[Snapshot], list[Problem]]:
    """Every complete snapshot, by seq, and what was not read and why."""
    shared = Path(shared)
    if not shared.is_dir():
        raise ShareError("unreachable", f"the shared folder {shared} cannot be reached now")
    area = snapshots_dir(shared)
    found: list[Snapshot] = []
    problems: list[Problem] = []
    if not area.is_dir():
        return found, problems
    for name in sorted(os.listdir(area)):
        path = area / name
        if name.startswith(".") and ".tmp-" in name:
            continue
        if not _SEQ_RE.match(name) or not path.is_dir() or path.is_symlink():
            problems.append(Problem("unexpected", str(path), "not a snapshot Research OS wrote (a sync conflict copy?); not read"))
            continue
        problems += ex_mod._unexpected(path, {MANIFEST_FILE, MANIFEST_SIG, ex_mod.READY_FILE, ex_mod.FILES_DIRNAME})
        try:
            raw = (path / MANIFEST_FILE).read_bytes()
        except FileNotFoundError:
            problems.append(Problem("half_written", str(path), "no manifest.toml"))
            continue
        except OSError as exc:
            problems.append(Problem("invalid", str(path), f"manifest.toml cannot be read: {exc}"))
            continue
        problem = ex_mod._read_ready(path, "snapshot", name, raw)
        if problem is not None:
            problems.append(problem)
            continue
        try:
            data = tomllib.loads(raw.decode("utf-8"))
            validate_manifest(data, int(name))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as exc:
            problems.append(Problem("invalid", str(path / MANIFEST_FILE), str(exc)))
            continue
        found.append(Snapshot(int(name), path, raw, data, ex_mod._read_signature(path / MANIFEST_SIG)))
    found.sort(key=lambda s: s.seq)
    return found, problems


def content_path(shared: Path, entry: Mapping[str, Any]) -> Path:
    """Where a manifest entry's content is published; never through a link."""
    if entry.get("store") == STORE_BLOB:
        base, path = blobs_dir(shared), blobs_dir(shared) / str(entry["sha256"])
    else:
        base = snapshots_dir(shared) / str(entry["in"]) / ex_mod.FILES_DIRNAME
        path = base / str(entry["path"])
    if not ex_mod._inside(path, base):
        raise ShareError("invalid", f"{path} points outside the shared folder; not read")
    step = base.parent
    for part in path.relative_to(base.parent).parts:
        step = step / part
        if step.is_symlink():
            raise ShareError("invalid", f"{step} is a link; not read")
    return path


def _read_content(shared: Path, entry: Mapping[str, Any]) -> bytes:
    path = content_path(shared, entry)
    if paths.is_dataless(path):
        raise ShareError("waiting", f"{path} is still in the cloud")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ShareError("content", f"{path} cannot be read: {exc}") from exc
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ShareError("content", f"{path} does not have the content the manifest names")
    return data


def snapshot_team(shared: Path, snap: Snapshot) -> tuple[people.Member, ...] | None:
    """The team a snapshot carries (its `.researchos/team.toml`), verified
    by hash; None when it carries none or it cannot be read."""
    entry = snap.files.get(f"{paths.PROJECT_DIRNAME}/{people.TEAM_FILENAME}")
    if entry is None:
        return None
    try:
        return people.parse_team(_read_content(shared, entry).decode("utf-8"))
    except (ShareError, UnicodeDecodeError, people.PeopleError):
        return None


@dataclass
class Chain:
    good: list[Snapshot] = field(default_factory=list)
    refused: list[dict[str, Any]] = field(default_factory=list)
    checks: dict[int, signing.Check] = field(default_factory=dict)

    @property
    def last_good(self) -> Snapshot | None:
        return self.good[-1] if self.good else None


def verify_chain(shared: Path, snaps: Iterable[Snapshot], project_id: str, pi: str, pi_key: str | None) -> Chain:
    """Walk the snapshots in order, keeping those that are the PI's, verify
    under `pi_key`, still name that key for the PI, and link by hash to the
    last good one (module docstring, "Trust"). Every other is refused with
    its reason and skipped."""
    chain = Chain()
    key = signing.parse_public_key(pi_key) if pi_key else None
    for snap in snaps:
        last = chain.last_good
        reason: tuple[str, str] | None = None
        check = None
        if snap.data.get("project") != project_id:
            reason = ("wrong_project", "这个快照属于另一个项目")
        elif snap.data.get("by") != pi:
            reason = ("not_pi", f"这个快照不是 PI（{pi}）写的")
        else:
            check = signing.check(snap.raw, snap.signature, key, signer=pi)
            if not check.trusted:
                reason = (check.state, check.message_zh)
            elif last is None and snap.data.get("prev_seq") is not None:
                reason = ("chain", "这个快照接在一个无法验证或不存在的快照后面")
            elif last is not None and (snap.data.get("prev_seq") != last.seq
                                       or snap.data.get("prev_manifest_sha256") != last.sha256):
                reason = ("chain", f"这个快照没有接在上一个可信的快照（第 {last.seq} 个）后面")
            else:
                team = snapshot_team(shared, snap)
                lead = next((m for m in team or () if m.role == "pi"), None)
                if lead is None or lead.id != pi:
                    reason = ("pi_changed", "这个快照里的成员名单换了 PI，或无法读取")
                elif (signing.parse_public_key(lead.key) if lead.key else None) != key:
                    reason = ("pi_key_changed", "这个快照里 PI 的公钥和加入时信任的不一致")
        if reason is not None:
            chain.refused.append({"seq": snap.seq, "state": reason[0], "reason": reason[1], "path": str(snap.directory)})
            continue
        chain.good.append(snap)
        if check is not None:
            chain.checks[snap.seq] = check
    return chain


def _next_seq(shared: Path) -> int:
    area = snapshots_dir(shared)
    highest = 0
    if area.is_dir():
        for name in os.listdir(area):
            m = _SEQ_RE.match(name) or _TMP_SEQ_RE.match(name)
            if m is not None:
                highest = max(highest, int(m.group(1) if m.re is _TMP_SEQ_RE else name))
    return highest + 1


# -- publishing (the PI) -----------------------------------------------------------------------------


def _require_pi_team(root: Path) -> tuple[str, people.TeamRead]:
    me = _me()
    team = people.read_team(root)
    if team.state == "absent":
        raise ShareError("no_team", f"{team.path} does not exist: researchos team init")
    if team.state != "present":
        raise ShareError("team_unreadable", f"{team.path} cannot be read ({team.state}: {team.error})")
    if team.pi is None or team.pi.id != me:
        raise ShareError("not_pi", f"only the PI ({team.pi.id if team.pi else 'none'}) publishes; you are {me}")
    return me, team


def publish(
    official_root: str | Path,
    *,
    shared: str | Path | None = None,
    note: str | None = None,
    expected_id: Any = READ_NOW,
    timeout: float | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """「发布」: write the official version as snapshot seq+1 (module
    docstring) -- unless it is what the last good snapshot already says.
    The PI only, under the project lock (nothing in the project is
    written)."""
    root = Path(official_root)
    pid = _project_id(root)
    me, team = _require_pi_team(root)
    shared_dir = shared_for(pid, root, shared)
    if not shared_dir.is_dir():
        raise ShareError("unreachable", f"the shared folder {shared_dir} cannot be reached now")
    listed_key = team.pi.key if team.pi is not None else None
    try:
        signing.preflight(me, listed_key)
    except signing.SigningError as exc:
        raise _signing_refused(exc) from exc
    others = sorted(projects_in(shared_dir) - {pid})
    if others:
        raise ShareError("other_project", f"{shared_dir} holds snapshots of another project ({', '.join(others)}); "
                                          f"each project publishes to its own shared folder", details={"projects": others})
    collected = collect_entries(root, shared=shared_dir, expected_id=expected_id, timeout=timeout, now=now)
    with write_guard(root, expected_id, timeout=timeout):
        done = _publish_locked(root, pid, me, listed_key, shared_dir, note=note, now=now)
    done["collected"] = collected
    # 12.12: a member added before the shared folder was set gets their
    # area now -- once; an area already there is never touched.
    done["areas"] = [a for a in _member_areas(people.read_team(root).members, shared_dir) if a["state"] != "exists"]
    return done


def projects_in(shared: Path) -> set[str]:
    """The projects the complete snapshots in a shared folder name."""
    try:
        snaps, _problems = read_snapshots(shared)
    except ShareError:
        return set()
    return {str(s.data.get("project")) for s in snaps}


def _same_files(published: Mapping[str, Mapping[str, Any]], files: Mapping[str, Mapping[str, Any]]) -> bool:
    if set(published) != set(files):
        return False
    return all(published[r]["sha256"] == files[r]["sha256"] and published[r]["size"] == files[r]["size"] for r in files)


def _publish_locked(root: Path, pid: str, me: str, listed_key: str | None, shared: Path, *,
                    note: str | None, now: Callable[[], datetime] | None) -> dict[str, Any]:
    from researchos import proposals  # noqa: PLC0415

    files, dataless = official_files(root, pid)
    if dataless:
        raise ShareError("dataless", f"only in the cloud, not read: {', '.join(dataless)}; download them, then publish",
                         details={"files": dataless})
    snaps, _problems = read_snapshots(shared)
    chain = verify_chain(shared, snaps, pid, me, listed_key)
    last = chain.last_good
    restored = _restore_blobs(root, shared, last, files)
    if last is not None and _same_files(last.files, files):
        return {"published": False, "seq": last.seq, "sha256": last.sha256, "reason": "unchanged",
                "restored": restored, "warnings": warnings()}
    seq = _next_seq(shared)
    entries: list[dict[str, Any]] = []
    carry: dict[str, Path] = {}
    blobs: dict[str, Path] = {}
    new_content: list[str] = []
    for rel in sorted(files):
        fp = files[rel]
        prev = last.files.get(rel) if last is not None else None
        unchanged = prev is not None and prev["sha256"] == fp["sha256"] and prev["size"] == fp["size"]
        if unchanged and not (is_record(rel) and prev["store"] == STORE_SNAPSHOT):
            entries.append({k: prev[k] for k in ("path", "size", "sha256", "store", "in") if k in prev})
            continue
        if not unchanged and not is_record(rel):
            new_content.append(rel)
        if proposals._text_of(root / rel, fp["size"]) is not None:
            entries.append({"path": rel, "size": fp["size"], "sha256": fp["sha256"], "store": STORE_SNAPSHOT, "in": seq})
            carry[rel] = root / rel
        else:
            entries.append({"path": rel, "size": fp["size"], "sha256": fp["sha256"], "store": STORE_BLOB})
            blobs[fp["sha256"]] = root / rel
    data: dict[str, Any] = {
        "format": FORMAT, "project": pid, "seq": seq,
        "prev_seq": last.seq if last is not None else None,
        "prev_manifest_sha256": last.sha256 if last is not None else None,
        "at": _now_iso(now), "by": me, "note": note.strip() if isinstance(note, str) and note.strip() else None,
        "file": entries,
    }
    raw = proposals.emit(data, _MANIFEST_HEADER)
    validate_manifest(tomllib.loads(raw.decode("utf-8")), seq)
    try:
        signature = signing.sign_as(me, listed_key, raw)
    except signing.SigningError as exc:
        raise _signing_refused(exc) from exc
    try:
        for sha, source in blobs.items():
            ex_mod.put_blob_once(blobs_dir(shared), sha, source)
        contents: list[tuple[str, bytes | Path]] = [(MANIFEST_FILE, raw)]
        if signature is not None:
            contents.append((MANIFEST_SIG, signature))
        contents += [(f"{ex_mod.FILES_DIRNAME}/{rel}", source) for rel, source in carry.items()]
        expect = {f"{ex_mod.FILES_DIRNAME}/{rel}": files[rel]["sha256"] for rel in carry}
        ex_mod._write_dir_once(snapshots_dir(shared) / str(seq), contents,
                               ex_mod._ready_bytes("snapshot", str(seq), raw), expect=expect)
    except ExchangeError as exc:
        if exc.code == "damaged":
            raise ShareError("damaged", str(exc)) from exc
        raise ShareError("content" if exc.code == "content" else "unreachable", str(exc),
                         message_zh=exc.message_zh) from exc
    except OSError as exc:
        raise ShareError("unreachable", f"could not publish: {exc}") from exc
    _record_published(root, pid, files, new_content, me, seq)
    return {"published": True, "seq": seq, "sha256": hashlib.sha256(raw).hexdigest(), "files": len(entries),
            "carried": sorted(carry), "blobs": sorted(blobs), "changed": new_content, "signed": signature is not None,
            "restored": restored, "refused": chain.refused, "warnings": warnings(listed_key)}


def _restore_blobs(root: Path, shared: Path, last: "Snapshot | None", files: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """12.12: a blob the PI removed as damaged (`repair_blobs`) is written
    again -- once, by content, like any blob -- from the official file that
    still holds that content, so the last good snapshot is whole again.
    A blob that is there is never touched."""
    if last is None:
        return []
    restored = []
    area = blobs_dir(shared)
    for rel, entry in sorted(last.files.items()):
        sha = entry.get("sha256")
        if entry.get("store") != STORE_BLOB or (files.get(rel) or {}).get("sha256") != sha:
            continue
        target = area / str(sha)
        if target.exists() or target.is_symlink() or sha in restored:
            continue
        try:
            ex_mod.put_blob_once(area, str(sha), root / rel)
        except ExchangeError as exc:
            raise ShareError("damaged" if exc.code == "damaged" else "content", str(exc)) from exc
        except OSError as exc:
            raise ShareError("unreachable", f"could not write {target}: {exc}") from exc
        restored.append(str(sha))
    return restored


def _record_published(root: Path, pid: str, files: Mapping[str, Mapping[str, Any]], rels: list[str], me: str,
                      seq: int) -> None:
    """12.6: a version first seen when publishing -- the PI's own direct
    change, which no scan or approval recorded yet -- enters the file's
    history with who = the PI. A version already there (an approved one,
    say) keeps its own attribution. Contained: the snapshot is published
    whatever this says."""
    from researchos.records import notes as notes_mod  # noqa: PLC0415

    if not rels:
        return
    try:
        with history.Store.open(pid) as store:
            for rel in rels:
                latest = store.latest(notes_mod.normalize_node(rel))
                if latest is not None and latest.sha256 == files[rel]["sha256"]:
                    continue
                history.record_file(store, root, rel, source="publish", who=me, note=f"快照 {seq}")
    except Exception:  # noqa: BLE001
        logger.exception("published versions of %s not recorded in the history store", root)


def warnings(listed_key: str | None = "unknown") -> list[str]:
    out = []
    if signing.tool() is None:
        out.append(signing.NO_TOOL_WARNING)
    elif listed_key is None:
        out.append("成员名单里 PI 没有公钥：快照没有签名，只能依靠公盘的共享权限")
    return out


# -- the member's trust and base ------------------------------------------------------------------


def _state_path(project_id: str) -> Path:
    from researchos import proposals  # noqa: PLC0415

    return proposals.base_dir(project_id) / STATE_FILENAME


def read_state(project_id: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_state_path(project_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ShareError("not_joined", f"{_state_path(project_id)} cannot be read: {exc}") from exc
    return data if isinstance(data, dict) else None


def _write_state(project_id: str, state: Mapping[str, Any]) -> None:
    path = _state_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    record_files.durable_write(path, (json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def pinned_pi(project_id: str) -> dict[str, Any] | None:
    """The PI (and key) this machine trusted when it joined, or None."""
    try:
        state = read_state(project_id)
    except ShareError:
        return None
    if not state or not state.get("pi"):
        return None
    return {"pi": state["pi"], "pi_key": state.get("pi_key")}


def official_copies(project_id: str) -> set[str]:
    """The `<name>.official.<ext>` files sync put beside the member's own
    (never proposed back as the member's work)."""
    try:
        state = read_state(project_id) or {}
    except ShareError:
        return set()
    return {str(rel) for rel in state.get("official_copies", [])}


def _official_name(rel: str, suffix: str = "") -> str:
    directory, name = posixpath.split(rel)
    stem, ext = posixpath.splitext(name)
    if not stem:
        stem, ext = name, ""
    return posixpath.join(directory, f"{stem}{OFFICIAL_INFIX}{suffix}{ext}")


def _confined(root: Path, rel: str) -> Path:
    target = root / rel
    record_files.ensure_dir_within(root, target.parent)
    step = root
    for part in Path(rel).parts:
        step = step / part
        if step.is_symlink():
            raise ShareError("invalid", f"{step} is a link; not written through")
    return target


def _install(shared: Path, entry: Mapping[str, Any], root: Path, rel: str, *, exclusive: bool = False) -> Path:
    """Write a manifest entry's content to `root/rel`, verified by hash,
    atomically (replacing, or -- `exclusive` -- never over a file)."""
    source = content_path(shared, entry)
    target = _confined(root, rel)
    tmp = record_files.temp_path_for(target)
    try:
        got = ex_mod.copy_file(source, tmp)
        if got != entry["sha256"]:
            raise ShareError("content", f"{source} does not have the content the manifest names (sha256 {got})")
        if exclusive:
            os.link(tmp, target)
        else:
            os.replace(tmp, target)
        record_files._fsync_dir(target.parent)
    finally:
        if tmp.exists():
            tmp.unlink()
    return target


def _waiting(shared: Path, entries: Iterable[Mapping[str, Any]]) -> list[str]:
    out = []
    for entry in entries:
        path = content_path(shared, entry)
        if not path.exists():
            raise ShareError("content", f"{path} is missing from the shared folder",
                             message_zh="公盘上缺少快照里列出的文件")
        if paths.is_dataless(path):
            paths._request_download(path)
            out.append(str(entry["path"]))
    return out


def _texts_for_base(shared: Path, root: Path, files: Mapping[str, Mapping[str, Any]],
                    extra: Mapping[str, Path] | None = None) -> dict[str, Path]:
    """A verified source for each text file of a manifest, for the base's
    own copy (`researchos.proposals.write_base`)."""
    texts: dict[str, Path] = {}
    for rel, entry in files.items():
        if entry.get("store") != STORE_SNAPSHOT:
            continue
        for candidate in ((extra or {}).get(rel), root / rel, content_path(shared, entry)):
            if candidate is None:
                continue
            try:
                if candidate.is_file() and ex_mod.sha256_of(candidate)[0] == entry["sha256"]:
                    texts[rel] = candidate
                    break
            except OSError:
                continue
    return texts


def _base_files(snap: Snapshot) -> dict[str, dict[str, Any]]:
    return {rel: {"sha256": e["sha256"], "size": e["size"]} for rel, e in snap.files.items()}


# -- joining (a member) -------------------------------------------------------------------------------


def _join_target(shared: str | Path, folder: str | Path, project: str | None) -> dict[str, Any]:
    """What joining `shared` into `folder` would start from, read only:
    every refusal of `join` before anything is written (who I am, the
    folder, the snapshots, which project, the chain), and the latest good
    snapshot with the PI found in the first one."""
    me = _me()
    shared_dir = Path(shared).expanduser()
    if not shared_dir.is_absolute():
        shared_dir = shared_dir.resolve()
    dest = Path(folder).expanduser()
    if not dest.is_absolute():
        dest = dest.resolve()
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise ShareError("not_empty", f"{dest} is not an empty folder; a working copy starts in an empty one")
    try:
        ex_mod.check_location(shared_dir, dest)
        _check_working_location(dest)
    except ExchangeError as exc:
        raise ShareError("invalid", str(exc), message_zh=exc.message_zh) from exc
    snaps, problems = read_snapshots(shared_dir)
    if not snaps:
        raise ShareError("nothing_published", f"no complete snapshot in {snapshots_dir(shared_dir)}",
                         details={"problems": [p.payload() for p in problems]})
    named = sorted({str(s.data["project"]) for s in snaps})
    if project is not None:
        if project not in named:
            raise ShareError("nothing_published", f"no snapshot of project {project} in {snapshots_dir(shared_dir)} "
                                                  f"(found: {', '.join(named)})", details={"projects": named})
        snaps = [s for s in snaps if s.data["project"] == project]
    elif len(named) > 1:
        raise ShareError("ambiguous_project", f"{shared_dir} holds snapshots of {len(named)} projects "
                                              f"({', '.join(named)}); name the one to join (--project)",
                         details={"projects": named})
    first = snaps[0]
    pid = str(first.data["project"])
    founders = snapshot_team(shared_dir, first)
    lead = next((m for m in founders or () if m.role == "pi"), None)
    if lead is None:
        raise ShareError("untrusted", f"snapshot {first.seq} carries no readable team.toml naming a PI")
    chain = verify_chain(shared_dir, snaps, pid, lead.id, lead.key)
    target = chain.last_good
    if target is None:
        raise ShareError("untrusted", "no snapshot in the shared folder verifies", details={"refused": chain.refused})
    return {"me": me, "shared": shared_dir, "dest": dest, "pid": pid, "lead": lead, "chain": chain, "target": target,
            "problems": problems}


def join_preview(shared: str | Path, folder: str | Path, *, project: str | None = None) -> dict[str, Any]:
    """What 「加入」 would write, before it writes anything (12.3, 12.5): the
    new folder, the snapshot it starts from (number, files, total size),
    the project, and the PI with the fingerprint of the key that will be
    trusted from then on -- to compare with the PI before joining."""
    found = _join_target(shared, folder, project)
    target, lead, shared_dir = found["target"], found["lead"], found["shared"]
    files = target.files
    team = snapshot_team(shared_dir, target) or ()
    me = found["me"]
    return {
        "shared": str(shared_dir), "folder": str(found["dest"]), "exists": found["dest"].exists(),
        "project": found["pid"], "seq": target.seq, "at": target.data.get("at"), "note": target.data.get("note"),
        "files": len(files), "size": sum(int(e.get("size") or 0) for e in files.values()),
        "pi": lead.id, "pi_name": lead.name, "pi_fingerprint": signing.fingerprint(lead.key) if lead.key else None,
        "role": next((m.role for m in team if m.id == me), "none"), "me": me,
        "team": [{"id": m.id, "name": m.name, "role": m.role} for m in team],
        "waiting": _waiting(shared_dir, files.values()), "refused": found["chain"].refused,
        "problems": [p.payload() for p in found["problems"]], "warnings": warnings(lead.key),
    }


def join(shared: str | Path, folder: str | Path, *, now: Callable[[], datetime] | None = None,
         project: str | None = None, expect_pi: str | None = None) -> dict[str, Any]:
    """「加入」: a working copy in `folder` (new, or empty) from the latest
    good snapshot; the PI's key from the first snapshot is trusted from now
    on (module docstring) -- its fingerprint is in the answer, to compare
    with the PI. A folder with snapshots of more than one project is
    joined only with `project` named. `join_preview` says the same before;
    with `expect_pi` (the PI key fingerprint it showed), a folder whose PI
    key is no longer that one is refused, nothing written."""
    from researchos import proposals  # noqa: PLC0415

    found = _join_target(shared, folder, project)
    me, shared_dir, dest, pid = found["me"], found["shared"], found["dest"], found["pid"]
    lead, chain, target, problems = found["lead"], found["chain"], found["target"], found["problems"]
    if expect_pi is not None and (signing.fingerprint(lead.key) if lead.key else "") != expect_pi:
        raise ShareError("changed_since_shown", "the PI key in the shared folder is not the one shown; nothing written")
    files = target.files
    waiting = _waiting(shared_dir, files.values())
    if waiting:
        raise ShareError("waiting", f"still downloading: {', '.join(waiting)}", details={"files": waiting})
    stage = dest.parent / f".{dest.name}.joining-{secrets.token_hex(6)}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage.mkdir()
    try:
        for rel, entry in sorted(files.items()):
            _install(shared_dir, entry, stage, rel)
        if dest.exists():
            dest.rmdir()  # empty, checked above
        os.rename(stage, dest)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    base = proposals.write_base(pid, dest.resolve(), _base_files(target),
                                texts=_texts_for_base(shared_dir, dest, files), official=None)
    state = {
        "format": FORMAT, "project": pid, "shared": str(shared_dir), "working_copy": str(dest.resolve()),
        "pi": lead.id, "pi_key": signing.parse_public_key(lead.key) if lead.key else None,
        "pi_fingerprint": signing.fingerprint(lead.key) if lead.key else None,
        "trusted_on_first_join": _now_iso(now), "seq": target.seq, "manifest_sha256": target.sha256,
        "synced": _now_iso(now), "official_copies": [],
    }
    _write_state(pid, state)
    try:
        ex_mod.configure(pid, shared_dir, dest)
    except ExchangeError as exc:
        logger.warning("the shared folder was not remembered for %s: %s", pid, exc)
    team = snapshot_team(shared_dir, target) or ()
    role = next((m.role for m in team if m.id == me), "none")
    return {
        "working_copy": str(dest.resolve()), "project": pid, "seq": target.seq, "files": len(files),
        "pi": lead.id, "pi_fingerprint": state["pi_fingerprint"], "role": role, "base": base.snapshot,
        "trust": "first_use", "refused": chain.refused, "problems": [p.payload() for p in problems],
        "warnings": warnings(lead.key),
    }


def _check_working_location(dest: Path) -> None:
    from researchos import addproject  # noqa: PLC0415

    canonical = addproject._canonical_if_exists(dest) or str(dest.resolve())
    refused = addproject._location_refusal(str(dest), canonical)
    if refused is not None and refused.refusal is not None:
        raise ExchangeError("bad_location", f"{dest}: {refused.refusal.detail}", message_zh=refused.refusal.message)
    enclosing = ex_mod._enclosing_project(dest.parent) if dest.parent.exists() else None
    if enclosing is not None:
        raise ExchangeError("inside_project", f"{dest} would lie inside the project {enclosing}",
                            message_zh=f"这个文件夹在项目「{enclosing.name}」里面")


# -- syncing (a member) -----------------------------------------------------------------------------------


def _joined(root: Path) -> tuple[str, dict[str, Any]]:
    pid = _project_id(root)
    state = read_state(pid)
    if not state or Path(state.get("working_copy", "")) != root.resolve():
        raise ShareError("not_joined", f"{root} was not joined from a shared folder on this machine (researchos share join)")
    return pid, state


def available(root: str | Path) -> dict[str, Any]:
    """What the shared folder offers a joined working copy, read only: the
    latest good snapshot and what was refused."""
    root = Path(root)
    pid, state = _joined(root)
    shared = Path(state["shared"])
    snaps, problems = read_snapshots(shared)
    chain = verify_chain(shared, snaps, pid, state["pi"], state.get("pi_key"))
    have = next((s for s in chain.good if s.seq == state["seq"]), None)
    return {"state": state, "chain": chain, "problems": problems, "have": have, "snaps": snaps}


def sync_preview(working_copy: str | Path) -> dict[str, Any]:
    """What 「同步」 would do, before it does anything (read only): the
    snapshot it brings the copy to, the files it would update or remove,
    and the files both sides changed -- left as the member's, the official
    one put beside as `.official` (never a merge). Record files that travel
    (notes, judgments, links) are said apart: their entries are kept."""
    from researchos import proposals  # noqa: PLC0415

    root = Path(working_copy)
    seen = available(root)
    state, chain = seen["state"], seen["chain"]
    pid = state["project"]
    target = chain.last_good
    out: dict[str, Any] = {"from": state["seq"], "to": state["seq"], "update": [], "remove": [], "beside": [],
                           "kept": [], "records": [], "refused": chain.refused,
                           "problems": [p.payload() for p in seen["problems"]]}
    if seen["have"] is None or seen["have"].sha256 != state.get("manifest_sha256"):
        out["untrusted"] = True
        return out
    if target is None or target.seq <= state["seq"]:
        out["up_to_date"] = True
        return out
    base = proposals.read_base(pid)
    if base is None:
        raise ShareError("not_joined", f"the base of {pid} is missing on this machine")
    out.update(to=target.seq, at=target.data.get("at"), note=target.data.get("note"), by=target.data.get("by"))
    tfiles = target.files
    for rel in sorted(set(base.files) | set(tfiles)):
        b = (base.files.get(rel) or {}).get("sha256")
        entry = tfiles.get(rel)
        t = entry["sha256"] if entry is not None else None
        if t == b:
            continue
        local = proposals.fingerprint(root / rel)
        lsha = local.get("sha256") if local else None
        if local is not None and local.get("dataless"):
            out["problems"].append({"kind": "dataless", "path": rel, "detail": "这个文件只在云端，不会比较也不会更新"})
            continue
        if lsha == t:
            continue
        if lsha == b:
            out["update" if entry is not None else "remove"].append(rel)
        elif rel == f"{paths.PROJECT_DIRNAME}/{CANVAS_NAME}":
            out["kept"].append({"path": rel, "reason": "画布上的摆放是每个人自己的"})
        elif entry is None:
            out["kept"].append({"path": rel, "reason": "正式版本删除了这个文件；你改过它，所以留着你的"})
        elif _travelling(rel) or rel == f"{paths.PROJECT_DIRNAME}/{MAPPINGS_NAME}":
            out["records"].append(rel)
        else:
            out["beside"].append({"path": rel, "official_copy": _official_name(rel)})
    return out


def sync(working_copy: str | Path, *, expected_id: Any = READ_NOW, timeout: float | None = None,
         now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """「同步」 (module docstring): the working copy brought to the latest
    good snapshot; the member's own changes are never overwritten. Under
    the project lock."""
    from researchos.records.lock import project_lock  # noqa: PLC0415

    root = Path(working_copy)
    pid, _state = _joined(root)
    if expected_id is not READ_NOW and expected_id != pid:
        raise ShareError("not_joined", f"{root} no longer carries the project {expected_id}; nothing synced")
    # The project lock, without the index's home check: a working copy is
    # this machine's by the share state above, whether or not it has been
    # opened (and indexed) here yet.
    with project_lock(root, pid, timeout=timeout):
        return _sync_locked(root, now)


def _sync_locked(root: Path, now: Callable[[], datetime] | None) -> dict[str, Any]:
    from researchos import proposals  # noqa: PLC0415

    seen = available(root)
    state, chain = seen["state"], seen["chain"]
    pid = state["project"]
    shared = Path(state["shared"])
    out: dict[str, Any] = {
        "from": state["seq"], "seq": state["seq"], "updated": [], "removed": [], "kept_yours": [], "conflicts": [],
        "problems": [p.payload() for p in seen["problems"]], "refused": chain.refused, "warnings": warnings(state.get("pi_key")),
    }
    if seen["have"] is None or seen["have"].sha256 != state.get("manifest_sha256"):
        raise ShareError("untrusted", f"snapshot {state['seq']}, which this copy was made from, no longer verifies in "
                                      f"{shared}; nothing synced", message_zh="这个工作副本所依据的快照在公盘上已经无法验证，没有同步",
                         details={"refused": chain.refused})
    base = proposals.read_base(pid)
    if base is None:
        raise ShareError("not_joined", f"the base of {pid} is missing on this machine")
    # 12.4: the member's own notes and link judgments go with every sync.
    out["entries_sent"] = _send_entries(root, pid, state, base, shared, out["problems"], now)
    if out["entries_sent"]:
        _write_state(pid, state)  # what was sent is never sent again
    target = chain.last_good
    if target is None or target.seq <= state["seq"]:
        out["up_to_date"] = True
        return out
    out["notes_waiting"] = []
    tfiles = target.files
    needed = []
    plan: list[tuple[str, str, Mapping[str, Any] | None]] = []
    for rel in sorted(set(base.files) | set(tfiles)):
        b = (base.files.get(rel) or {}).get("sha256")
        entry = tfiles.get(rel)
        t = entry["sha256"] if entry is not None else None
        if t == b:
            continue
        local = proposals.fingerprint(root / rel)
        if local is not None and local.get("dataless"):
            out["problems"].append({"kind": "dataless", "path": rel, "detail": "这个文件只在云端，没有比较也没有更新"})
            continue
        lsha = local.get("sha256") if local else None
        if lsha == t:
            continue
        if rel == f"{paths.PROJECT_DIRNAME}/{CANVAS_NAME}" and lsha != b and local is not None:
            # 12.12: the member's own arrangement stays theirs; nothing beside it.
            out["kept_yours"].append({"path": rel, "reason": "画布上的摆放是每个人自己的，不随同步改变"})
            continue
        ledger = _travelling(rel)
        carried = None
        if entry is not None and lsha != b:
            if ledger:
                carried = _ledger_carried(root, rel, shared, entry)
            elif rel == f"{paths.PROJECT_DIRNAME}/{MAPPINGS_NAME}":
                carried = _mappings_carried(root, rel, shared, entry, base, state)
        if lsha == b:
            plan.append(("update" if entry is not None else "remove", rel, entry))
        elif entry is None:
            out["kept_yours"].append({"path": rel, "reason": "正式版本删除了这个文件；你改过它，所以留着你的"})
        elif carried == "carried":
            # Every entry of yours is in the official ledger now: it replaces
            # yours, nothing lost (module docstring).
            plan.append(("update", rel, entry))
        elif carried == "waiting":
            out["notes_waiting"].append(rel)
            continue
        else:
            plan.append(("conflict", rel, entry))
        if entry is not None:
            needed.append(entry)
    waiting = _waiting(shared, needed)
    if waiting:
        raise ShareError("waiting", f"still downloading: {', '.join(waiting)}", details={"files": waiting})
    copies = set(state.get("official_copies", []))
    extra: dict[str, Path] = {}
    for action, rel, entry in plan:
        if action == "remove":
            try:
                (root / rel).unlink()
            except FileNotFoundError:
                pass
            out["removed"].append(rel)
        elif action == "update":
            _install(shared, entry, root, rel)
            out["updated"].append(rel)
            if rel == f"{paths.PROJECT_DIRNAME}/{MAPPINGS_NAME}":
                state["sent_mappings"] = {}  # what was sent is in the official file now, or was received
        else:
            placed = _place_official_copy(shared, entry, root, rel, target.seq)
            if placed is None:
                out["problems"].append({"kind": "official_copy", "path": rel,
                                        "detail": "旁边已经有同名的 .official 文件，正式版本的这一版没有放下"})
                continue
            copies.add(placed)
            extra[rel] = root / placed
            item: dict[str, Any] = {"path": rel, "official_copy": placed, "reason": "你和正式版本都改了这个文件：留着你的，正式版本的放在旁边"}
            item.update(_difference(root / rel, root / placed, entry))
            out["conflicts"].append(item)
    proposals.write_base(pid, root.resolve(), _base_files(target), texts=_texts_for_base(shared, root, tfiles, extra),
                         official=None)
    state.update(seq=target.seq, manifest_sha256=target.sha256, synced=_now_iso(now),
                 official_copies=sorted(c for c in copies if (root / c).exists()))
    _write_state(pid, state)
    out["seq"] = target.seq
    return out


def _place_official_copy(shared: Path, entry: Mapping[str, Any], root: Path, rel: str, seq: int) -> str | None:
    for candidate in (_official_name(rel), _official_name(rel, f"-{seq}")):
        existing = proposals_fingerprint(root / candidate)
        if existing is None:
            _install(shared, entry, root, candidate, exclusive=True)
            return candidate
        if existing.get("sha256") == entry["sha256"]:
            return candidate
    return None


def proposals_fingerprint(path: Path) -> dict[str, Any] | None:
    from researchos import proposals  # noqa: PLC0415

    return proposals.fingerprint(path)


def _difference(mine: Path, official: Path, entry: Mapping[str, Any]) -> dict[str, Any]:
    from researchos import proposals  # noqa: PLC0415

    try:
        size = mine.stat().st_size
    except OSError:
        size = None
    a, b = proposals._text_of(mine, size), proposals._text_of(official, entry["size"])
    if a is not None and b is not None:
        diff, truncated = history.unified(a, b, "yours", "official")
        return {"diff": diff, "truncated": truncated}
    try:
        sa = history.data_summary(mine, str(mine.name))
        sb = history.data_summary(official, str(mine.name))
    except OSError:
        sa = sb = None
    return {"summary": history.table_sentence(sa, sb) if (sa or sb) else None}


# -- notes and link judgments (12.4: no approval) ------------------------------------------------------------


def _travelling(rel: str) -> str | None:
    prefix = paths.PROJECT_DIRNAME + "/"
    name = rel[len(prefix):] if rel.startswith(prefix) else None
    return name if name in TRAVELLING else None


def _schema(name: str):
    from researchos.records import ledger as ledger_mod  # noqa: PLC0415
    from researchos.records import notes as notes_mod  # noqa: PLC0415

    return notes_mod.NOTES_SCHEMA if name == "notes.toml" else ledger_mod.JUDGEMENT_SCHEMA


def _parse(text: str | None, name: str):
    from researchos.records import ledger as ledger_mod  # noqa: PLC0415

    if text is None:
        return None
    try:
        return ledger_mod.parse_ledger(text.removeprefix("\ufeff"), _schema(name))
    except ledger_mod.LedgerInvalidError:
        return None


def _represented(ledger) -> set[str]:
    """The entry ids a ledger holds, itself or as the copy of one."""
    return {e.id for e in ledger.entries} | {str(e.get("recovered_from")) for e in ledger.entries
                                               if isinstance(e.get("recovered_from"), str)}


def _ledger_carried(root: Path, rel: str, shared: Path, entry: Mapping[str, Any]) -> str | None:
    """"carried" when the official ledger holds every entry of the
    member's, "waiting" when some of theirs has not reached it yet, None
    when either cannot be read (then the file is treated like any other)."""
    name = _travelling(rel)
    try:
        mine = _parse((root / rel).read_text(encoding="utf-8"), name)
        official = _parse(_read_content(shared, entry).decode("utf-8"), name)
    except (OSError, UnicodeDecodeError, ShareError):
        return None
    if mine is None or official is None:
        return None
    return "carried" if {e.id for e in mine.entries} <= _represented(official) else "waiting"


def _mappings_carried(root: Path, rel: str, shared: Path, entry: Mapping[str, Any], base,
                      state: Mapping[str, Any]) -> str | None:
    """`_ledger_carried` for the hand-drawn links (12.12): "carried" when
    each of the member's own changes since their base is in the official
    file -- or went in a packet the official file says it received (the PI
    may have undone it since: that is the PI's to do) -- "waiting" when
    some has not reached it yet; None when either file cannot be read."""
    from researchos.ingest import mappings as mappings_ingest  # noqa: PLC0415

    try:
        me = people.current_member_id()
        mine = (root / rel).read_text(encoding="utf-8")
        official = _read_content(shared, entry).decode("utf-8")
    except (OSError, UnicodeDecodeError, ShareError, people.PeopleError):
        return None
    if me is None:
        return None
    added, removed = mappings_ingest.member_changes(root, base.text(rel), mine, me)
    now_there = {(k[0], mappings_ingest._written_to(k[2], k[1]), k[2])
                 for k in mappings_ingest._entries_by_key(root, official)}
    received = mappings_ingest.received_packets(official)
    sent = state.get("sent_mappings") or {}
    for op, items in (("add", added), ("remove", removed)):
        for item in items:
            key = (item["from"], item["to"], item["type"])
            reflected = (key in now_there) if op == "add" else (key not in now_there)
            if not reflected and sent.get(mappings_ingest.change_key(op, item)) not in received:
                return "waiting"
    return "carried"


def _send_entries(root: Path, pid: str, state: dict[str, Any], base, shared: Path, problems: list[dict[str, Any]],
                  now: Callable[[], datetime] | None) -> int:
    """The member's own new ledger entries and link changes, written once
    to their area (module docstring); `state["sent_entries"]` and
    `state["sent_mappings"]` remember what was sent."""
    from researchos.ingest import mappings as mappings_ingest  # noqa: PLC0415
    from researchos.records import ledger as ledger_mod  # noqa: PLC0415

    try:
        me = people.current_member_id()
    except people.PeopleError:
        me = None
    if me is None:
        return 0
    sent = set(state.get("sent_entries") or [])
    sent_mappings = dict(state.get("sent_mappings") or {})
    blocks: list[bytes] = []
    ids: list[str] = []
    links: dict[str, list[dict[str, str]]] = {PACKET_ADDED: [], PACKET_REMOVED: []}
    link_keys: list[str] = []
    rel = f"{paths.PROJECT_DIRNAME}/{MAPPINGS_NAME}"
    try:
        mine_text = (root / rel).read_text(encoding="utf-8")
    except FileNotFoundError:
        mine_text = None
    except (OSError, UnicodeDecodeError) as exc:
        mine_text = None
        problems.append({"kind": "entries_not_sent", "path": rel, "detail": f"无法读取：{exc}"})
    if mine_text is not None or base.text(rel) is not None:
        added, removed = mappings_ingest.member_changes(root, base.text(rel), mine_text, me)
        for op, items, table in (("add", added, PACKET_ADDED), ("remove", removed, PACKET_REMOVED)):
            for item in items:
                key = mappings_ingest.change_key(op, item)
                if key in sent_mappings:
                    continue
                links[table].append({**item, "by": me})
                link_keys.append(key)
    for name in TRAVELLING:
        rel = f"{paths.PROJECT_DIRNAME}/{name}"
        try:
            mine = _parse((root / rel).read_text(encoding="utf-8"), name)
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError) as exc:
            problems.append({"kind": "entries_not_sent", "path": rel, "detail": f"无法读取：{exc}"})
            continue
        if mine is None:
            problems.append({"kind": "entries_not_sent", "path": rel, "detail": "记录文件无法读取，没有发送"})
            continue
        known = _parse(base.text(rel), name)
        known_ids = _represented(known) if known is not None else set()
        for e in mine.entries:
            if e.id in known_ids or e.id in sent or e.get("by") != me:
                continue
            data = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in e.data.items()}
            blocks.append(ledger_mod.emit_entry(_schema(name), data))
            ids.append(e.id)
    if not blocks and not link_keys:
        return 0
    from researchos import proposals  # noqa: PLC0415

    packet_id = "n-" + secrets.token_hex(16)
    head = proposals.emit({"format": FORMAT, "id": packet_id, "project": pid, "by": me, "at": _now_iso(now),
                           PACKET_ADDED: links[PACKET_ADDED], PACKET_REMOVED: links[PACKET_REMOVED]},
                          _PACKET_HEADER)
    raw = head + b"".join(blocks)
    try:
        tomllib.loads(raw.decode("utf-8"))
        team = people.read_team(root)
        key = next((m.key for m in team.members if m.id == me), None)
        signature = signing.sign_as(me, key, raw)
        ex_mod.LocalExchange(shared).write_packet(me, packet_id, raw, signature)
    except (tomllib.TOMLDecodeError, signing.SigningError, ExchangeError, OSError) as exc:
        problems.append({"kind": "entries_not_sent", "path": str(shared),
                         "detail": f"笔记和连接判断没有发送：{getattr(exc, 'message_zh', None) or exc}"})
        return 0
    state["sent_entries"] = sorted(sent | set(ids))
    state["sent_mappings"] = {**sent_mappings, **{key: packet_id for key in link_keys}}
    return len(ids) + len(link_keys)


def collect_entries(official_root: str | Path, *, shared: Path | None = None, expected_id: Any = READ_NOW,
                    timeout: float | None = None, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """The PI's side: every member's packets appended to the official
    ledgers (module docstring) -- each entry once (`recovered_from`), with
    the member's `by`; a packet not signed by its member, of another
    project, or an entry not the member's own, is reported and not taken.
    Contained: publishing goes on whatever this says."""
    from researchos.records import judgements as judgements_mod  # noqa: PLC0415
    from researchos.records import ledger as ledger_mod  # noqa: PLC0415
    from researchos.records import notes as notes_mod  # noqa: PLC0415

    root = Path(official_root)
    out: dict[str, Any] = {"appended": 0, "problems": []}
    try:
        pid = _project_id(root)
        team = people.read_team(root)
        shared_dir = Path(shared) if shared is not None else shared_for(pid, root)
        ex = ex_mod.LocalExchange(shared_dir, verifier=ex_mod.verifier_for(root, pid))
        members = [m.id for m in team.members if m.role == "member"]
        incoming: list[tuple[str, str, dict[str, Any]]] = []
        links: list[tuple[str, str, Mapping[str, Any]]] = []
        out["links"] = {"added": 0, "removed": 0, "packets": 0}
        for member in members:
            packets, problems = ex.packets(member)
            out["problems"] += [p.payload() for p in problems]
            for packet_id, _raw, data, check in sorted(packets, key=lambda p: (str(p[2].get("at") or ""), p[0])):
                if check is not None and not check.trusted:
                    out["problems"].append({"kind": "unverified", "path": packet_id, "detail": check.message_zh})
                    continue
                if data.get("project") != pid:
                    out["problems"].append({"kind": "wrong_project", "path": packet_id, "detail": "属于另一个项目"})
                    continue
                for name, table in TRAVELLING.items():
                    for entry in data.get(table) or []:
                        incoming.append((name, member, entry))
                if data.get(PACKET_ADDED) or data.get(PACKET_REMOVED):
                    links.append((packet_id, member, data))
        if not incoming and not links:
            return out
        with write_guard(root, expected_id, human=True, timeout=timeout) as held:
            identity = read_identity(root).identity
            conn = notes_mod._open_index(identity)
            try:
                for name in TRAVELLING:
                    batch = [(m, e) for n, m, e in incoming if n == name]
                    if not batch:
                        continue
                    schema = _schema(name)
                    path = paths.project_dir(root) / name
                    for member, entry in batch:
                        loaded, decision = (notes_mod.assess(conn, root, identity) if name == "notes.toml"
                                            else judgements_mod.assess(conn, root, identity))
                        if not decision.may_write:
                            out["problems"].append({"kind": "ledger_untrusted", "path": name,
                                                    "detail": f"{decision.reason}: 成员的记录没有收进来"})
                            break
                        ledger = loaded.ledger
                        renamed = {str(e.get("recovered_from")): e.id for e in (ledger.entries if ledger else ())
                                   if isinstance(e.get("recovered_from"), str)}
                        if ledger is not None and str(entry.get("id")) in _represented(ledger):
                            continue
                        try:
                            if not isinstance(entry, dict) or entry.get("by") != member:
                                raise ValueError("an entry carried from a member's area is that member's own")
                            ledger_mod._validate_engine_fields(schema, entry)
                            schema.validate(entry)
                        except ValueError as exc:
                            out["problems"].append({"kind": "invalid", "path": name, "detail": str(exc)})
                            continue
                        fields = {k: v for k, v in entry.items() if k not in ("id", "seq", "at", "settles")}
                        fields["recovered_from"] = str(entry["id"])
                        if entry.get("at") is not None:
                            fields["recovered_at"] = str(entry["at"])
                        if entry.get("undoes"):
                            fields["undoes"] = renamed.get(str(entry["undoes"]), str(entry["undoes"]))
                        try:
                            ledger_mod.append(path, schema, fields, lock=held, project_root=root,
                                              create=decision.may_create, now=now)
                        except ledger_mod.LedgerWriteRefused as exc:
                            out["problems"].append({"kind": "not_taken", "path": name, "detail": str(exc)})
                            continue
                        if loaded.state is record_files.RecordState.ABSENT:
                            record_files.snapshot_if_first_change_today(root, path, now=now)
                        out["appended"] += 1
                notes_mod.apply_notes(conn, root, identity=identity)
                if links:
                    _collect_links(conn, root, links, out, now)
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.exception("collecting members' notes into %s failed", root)
        out["problems"].append({"kind": "failed", "path": str(root), "detail": str(exc)})
    return out


def _collect_links(conn, root: Path, links: list[tuple[str, str, Mapping[str, Any]]], out: dict[str, Any],
                   now: Callable[[], datetime] | None) -> None:
    """The members' link packets into the official mappings.toml, oldest
    first, each once (`researchos.ingest.mappings.apply_received`); then
    the index follows the file. The caller holds the project lock."""
    from researchos.ingest import mappings as mappings_ingest  # noqa: PLC0415

    changed = False
    for packet_id, member, data in sorted(links, key=lambda item: (str(item[2].get("at") or ""), item[0])):
        try:
            done = mappings_ingest.apply_received(root, packet_id, member, list(data.get(PACKET_ADDED) or []),
                                                  list(data.get(PACKET_REMOVED) or []), at=_now_iso(now))
        except (mappings_ingest.MappingsWriteError, mappings_ingest.MappingsFileError) as exc:
            out["problems"].append({"kind": "links_not_taken", "path": MAPPINGS_NAME,
                                    "detail": f"{packet_id}: {exc}"})
            break  # the file cannot be written now: every later packet waits for the next publish
        out["problems"] += [{"kind": "invalid", "path": MAPPINGS_NAME, "detail": f"{packet_id}: {p}"}
                            for p in done["problems"]]
        if done["applied"]:
            changed = True
            out["links"]["packets"] += 1
            out["links"]["added"] += done["added"]
            out["links"]["removed"] += done["removed"]
    if changed:
        try:
            mappings_ingest.ingest_mappings(conn, root)
        except mappings_ingest.MappingsFileError as exc:  # the watcher's next look tries again
            logger.info("mappings not re-read after collecting: %s", exc)


# -- pruning old blobs (the PI, 12.2) -----------------------------------------------------------------------


def prune_blobs(official_root: str | Path, before: str, *, shared: str | Path | None = None,
                apply: bool = False) -> dict[str, Any]:
    """「清理旧文件」: the blobs in 已发布/blobs that no snapshot dated
    `before` (YYYY-MM-DD) or later references -- the latest good snapshot is
    always kept whole. Lists them; removes them only with `apply`. The PI
    only."""
    root = Path(official_root)
    pid = _project_id(root)
    me, team = _require_pi_team(root)
    try:
        cutoff = datetime.strptime(before, "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError) as exc:
        raise ShareError("invalid", f"--before takes a date, YYYY-MM-DD, got {before!r}") from exc
    shared_dir = shared_for(pid, root, shared)
    snaps, problems = read_snapshots(shared_dir)
    chain = verify_chain(shared_dir, snaps, pid, me, team.pi.key if team.pi else None)
    if chain.last_good is None:
        raise ShareError("nothing_published", f"no verified snapshot in {shared_dir}; nothing is pruned")
    keep: set[str] = set()
    # Every complete snapshot from the cutoff on counts (verified or not):
    # a blob is removed only when nothing recent could name it.
    for snap in snaps:
        if str(snap.data.get("at") or "")[:10] >= cutoff or snap.seq == chain.last_good.seq:
            keep |= {e["sha256"] for e in snap.data.get("file", []) if e.get("store") == STORE_BLOB}
    if problems:
        raise ShareError("invalid", "the shared folder holds snapshots Research OS could not read; nothing is pruned",
                         details={"problems": [p.payload() for p in problems]})
    area = blobs_dir(shared_dir)
    found = []
    if area.is_dir():
        for name in sorted(os.listdir(area)):
            path = area / name
            if ex_mod.SHA_RE.match(name) and name not in keep and path.is_file() and not path.is_symlink():
                found.append({"sha256": name, "size": path.stat().st_size})
    removed = []
    if apply:
        for item in found:
            try:
                (area / item["sha256"]).unlink()
                removed.append(item["sha256"])
            except OSError as exc:
                logger.warning("blob %s not removed: %s", item["sha256"], exc)
    return {"before": cutoff, "kept": len(keep), "prunable": found, "bytes": sum(i["size"] for i in found),
            "removed": removed, "applied": apply}


# -- repairing a damaged blob (the PI, 12.12) ----------------------------------------------------------------


def repair_blobs(official_root: str | Path, *, shared: str | Path | None = None, apply: bool = False) -> dict[str, Any]:
    """`researchos share repair`: every published blob (`已发布/blobs/`) whose
    content is not the SHA-256 it is named after -- a sync service's damage
    -- listed with the snapshots that name it and whether the official
    version still holds that content (then the next publish writes it
    again, `_restore_blobs`). A dry run unless `apply`, which removes those
    blobs and nothing else: no snapshot, no outcome, no member's area is
    touched. A blob still in the cloud is not read and not removed. The PI
    only."""
    root = Path(official_root)
    pid = _project_id(root)
    _require_pi_team(root)
    shared_dir = shared_for(pid, root, shared)
    if not shared_dir.is_dir():
        raise ShareError("unreachable", f"the shared folder {shared_dir} cannot be reached now")
    snaps, _problems = read_snapshots(shared_dir)
    named: dict[str, list[int]] = {}
    for snap in snaps:
        for entry in snap.data.get("file", []):
            if entry.get("store") == STORE_BLOB:
                named.setdefault(str(entry["sha256"]), []).append(snap.seq)
    files, _dataless = official_files(root, pid)
    held = {e["sha256"] for e in files.values()}
    area = blobs_dir(shared_dir)
    damaged, unchecked = [], []
    if area.is_dir():
        for name in sorted(os.listdir(area)):
            path = area / name
            if not ex_mod.SHA_RE.match(name) or path.is_symlink() or not path.is_file():
                continue
            if paths.is_dataless(path):
                unchecked.append(name)
                continue
            try:
                got, size = ex_mod.sha256_of(path)
            except OSError as exc:
                damaged.append({"sha256": name, "size": None, "unreadable": str(exc)})
                continue
            if got != name:
                damaged.append({"sha256": name, "size": size})
    for item in damaged:
        item["snapshots"] = sorted(set(named.get(item["sha256"], [])))
        item["restorable"] = item["sha256"] in held
    removed = []
    if apply:
        for item in damaged:
            try:
                (area / item["sha256"]).unlink()
                removed.append(item["sha256"])
            except OSError as exc:
                item["not_removed"] = str(exc)
        if removed:
            ex_mod._fsync_dir(area)
    return {"damaged": damaged, "unchecked": unchecked, "removed": removed, "applied": apply}


# -- the team (the PI) ------------------------------------------------------------------------------------


def _member_block(member: str, name: str, role: str, key: str | None) -> str:
    from researchos.records.ledger import toml_string  # noqa: PLC0415

    lines = ["[[member]]", f"id = {toml_string(member)}", f"name = {toml_string(name)}", f"role = {toml_string(role)}"]
    if key:
        lines.append(f"key = {toml_string(key)}")
    return "\n".join(lines) + "\n"


def team_init(root: str | Path, *, expected_id: Any = READ_NOW, timeout: float | None = None) -> dict[str, Any]:
    """`researchos team init`: `.researchos/team.toml` with me as the PI and
    my public key (made on first need; without ssh-keygen, no key -- said
    so). Created exclusively: an existing team file is never replaced."""
    root = Path(root)
    got = people.read_me()
    if got.me is None:
        _me()
        raise ShareError("no_me", f"{got.path} cannot be read")
    me = got.me
    path = people.team_path(root)
    key = None
    warning = []
    if signing.tool() is not None:
        try:
            key = signing.ensure_key()
        except signing.SigningError as exc:
            raise _signing_refused(exc) from exc
    else:
        warning.append(signing.NO_TOOL_WARNING)
    text = _TEAM_HEADER + "\n" + _member_block(me.id, me.name, "pi", key)
    members = people.parse_team(text)
    with write_guard(root, expected_id, human=True, timeout=timeout):
        if path.exists() or path.is_symlink() or people.read_team(root).state != "absent":
            raise ShareError("team_exists", f"{path} already exists; it is never replaced")
        record_files.ensure_dir_within(root, path.parent)
        tmp = record_files.temp_path_for(path)
        try:
            record_files.write_new_file(tmp, text.encode("utf-8"))
            os.link(tmp, path)  # exclusive: never over a team file that appeared meanwhile
        except FileExistsError as exc:
            raise ShareError("team_exists", f"{path} appeared meanwhile; it is never replaced") from exc
        finally:
            if tmp.exists():
                tmp.unlink()
        record_files._fsync_dir(path.parent)
    return {"path": str(path), "pi": me.id, "key": key, "fingerprint": signing.fingerprint(key) if key else None,
            "members": [m.payload() for m in members], "warnings": warning}


def edit_rights_sentence(member: str, name: str) -> dict[str, str]:
    """What the PI is told when a member's area is made (12.12): give that
    member edit rights to it in OneDrive -- the sharing permission is what
    keeps each area the member's own (12.2)."""
    area = f"{ex_mod.PROPOSALS_AREA}/{member}/"
    return {
        "sentence": f"已在公盘建好{name}（{member}）的提议区 {area}。请在 OneDrive 里把这个文件夹的编辑权限给{name}；"
                    f"{ex_mod.PUBLISHED_AREA}/ 只给查看权限。",
        "sentence_en": f"The shared folder now has {name}'s ({member}) proposal area {area}. In OneDrive, give "
                       f"{name} edit rights to that folder, and view-only rights to {ex_mod.PUBLISHED_AREA}/.",
    }


def ensure_member_area(shared: Path, member: str) -> str:
    """`提议/<member id>/`, made once (12.12): an empty folder created under
    a temporary name and renamed into place. "created", or "exists" -- an
    area already there is never touched, whatever it holds."""
    area = ex_mod.LocalExchange(shared).member_area(member)
    if area.exists() or area.is_symlink():
        return "exists"
    parent = area.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = parent / f".{member}.tmp-{secrets.token_hex(6)}"
    tmp.mkdir()
    try:
        if area.exists() or area.is_symlink():
            return "exists"
        try:
            os.rename(tmp, area)
        except OSError:
            if area.exists():
                return "exists"  # appeared meanwhile, and holds something: not ours to replace
            raise
    finally:
        if tmp.exists():
            tmp.rmdir()
    ex_mod._fsync_dir(parent)
    return "created"


def _member_areas(members: Iterable[people.Member], shared: Path | None) -> list[dict[str, Any]]:
    """Every member's area on the shared folder, made where missing; the
    ones made now carry the sentence for the PI. Nothing when the shared
    folder is not set or cannot be reached (said so)."""
    out = []
    for m in members:
        if m.role != "member":
            continue
        if shared is None or not shared.is_dir():
            out.append({"member": m.id, "state": "not_configured" if shared is None else "unreachable"})
            continue
        try:
            state = ensure_member_area(shared, m.id)
        except (OSError, ExchangeError) as exc:
            out.append({"member": m.id, "state": "failed", "detail": str(exc)})
            continue
        item: dict[str, Any] = {"member": m.id, "state": state,
                                "area": str(shared / ex_mod.PROPOSALS_AREA / m.id)}
        if state == "created":
            item.update(edit_rights_sentence(m.id, m.name))
        out.append(item)
    return out


def team_add(root: str | Path, member: str, name: str, key_text: str, *, role: str = "member",
             expected_id: Any = READ_NOW, timeout: float | None = None) -> dict[str, Any]:
    """`researchos team add`: one `[[member]]` appended to team.toml (the
    file is only ever appended to), by the PI -- and the member's empty
    area `提议/<member id>/` made once on the shared folder, with the
    sentence telling the PI to give that member edit rights to it (12.12).
    When the shared folder is not set yet, the area is made by the first
    publish after it is (`publish`)."""
    root = Path(root)
    me, team = _require_pi_team(root)
    try:
        member_id = people.member_id_for(member)
    except people.PeopleError as exc:
        raise ShareError("invalid", str(exc), message_zh=exc.message_zh) from exc
    if role not in people.ROLES or role == "pi":
        raise ShareError("invalid", "a member added is a member; a team has exactly one pi", message_zh="只能添加成员（member）")
    try:
        key = signing.parse_public_key(key_text)
    except signing.SigningError as exc:
        raise ShareError("invalid_key", str(exc), message_zh=exc.message_zh) from exc
    path = people.team_path(root)
    with write_guard(root, expected_id, human=True, timeout=timeout):
        got = record_files.read_record(path)
        if got.state is not record_files.RecordState.PRESENT:
            raise ShareError("team_unreadable", f"{path} cannot be read now")
        current = people.parse_team(got.text or "")
        if any(m.id == member_id for m in current):
            raise ShareError("member_exists", f"{member_id} is already in {path}")
        block = _member_block(member_id, name, role, key)
        try:
            people.parse_team((got.text or "") + ("" if (got.text or "").endswith("\n") else "\n") + "\n" + block)
        except people.PeopleError as exc:
            raise ShareError("invalid", str(exc), message_zh=exc.message_zh) from exc
        try:
            record_files.append_bytes(path, ("\n" + block).encode("utf-8"), expected_old=got.data)
        except record_files.RecordFileError as exc:
            raise ShareError("team_unreadable", str(exc)) from exc
    shared = ex_mod.configured(_project_id(root))
    areas = _member_areas([m for m in people.read_team(root).members if m.id == member_id], shared)
    area = areas[0] if areas else {"member": member_id, "state": "failed"}
    out = {"path": str(path), "member": member_id, "name": name, "role": role, "fingerprint": signing.fingerprint(key),
           "added_by": me, "area": area}
    if area["state"] == "created":
        out.update(sentence=area["sentence"], sentence_en=area["sentence_en"])
    elif area["state"] == "exists":
        told = edit_rights_sentence(member_id, name)
        out.update(sentence=told["sentence"].replace("已在公盘建好", "公盘上已经有"),
                   sentence_en=told["sentence_en"].replace("now has", "already has"))
    else:
        out.update(sentence=f"公盘文件夹还没有设置或现在无法访问：设置好以后，下次发布时会为{name}建好提议区 "
                            f"{ex_mod.PROPOSALS_AREA}/{member_id}/，那时再在 OneDrive 里把它的编辑权限给{name}。",
                   sentence_en=f"The shared folder is not set or cannot be reached now: the next publish makes "
                               f"{ex_mod.PROPOSALS_AREA}/{member_id}/ for {name}; give {name} edit rights to it then.")
    return out


def my_key() -> dict[str, Any]:
    """`researchos me key`: this machine's public key (made on first need),
    for the PI to add with `researchos team add`."""
    try:
        key = signing.ensure_key()
    except signing.SigningError as exc:
        raise _signing_refused(exc) from exc
    return {"key": key, "fingerprint": signing.fingerprint(key), "path": str(signing.key_path()) + ".pub"}


# -- status and the inbox ----------------------------------------------------------------------------------


def status(root: str | Path) -> dict[str, Any]:
    """`GET /api/share` / `researchos share show`: role, shared folder, last
    snapshot, verification state, what is pending (reads only)."""
    from researchos import proposals  # noqa: PLC0415

    root = Path(root)
    pid = _project_id(root)
    role, me, team = proposals.role_of(root)
    is_pi = team.state == "present" and team.pi is not None and team.pi.id == me
    shared = ex_mod.configured(pid)
    local = signing.local_public_key()
    out: dict[str, Any] = {
        "project": pid, "role": "pi" if is_pi else role, "me": me, "shared": str(shared) if shared else None,
        "reachable": bool(shared and shared.is_dir()), "ssh_keygen": signing.tool() is not None,
        "my_key": {"fingerprint": signing.fingerprint(local)} if local else None,
        "team": [{**m.payload(), "fingerprint": signing.fingerprint(m.key) if m.key else None} for m in team.members],
        "warnings": [],
    }
    if is_pi:
        out["warnings"] = warnings(team.pi.key)
        if team.pi.key and local is not None and signing.parse_public_key(team.pi.key) != local:
            out["warnings"].append("这台电脑上的密钥和成员名单里 PI 的公钥不一致：无法签名发布")
        if out["reachable"]:
            snaps, problems = read_snapshots(shared)
            chain = verify_chain(shared, snaps, pid, me, team.pi.key)
            last = chain.last_good
            out["last_snapshot"] = last.payload() if last else None
            out["verification"] = (chain.checks[last.seq].state if last and last.seq in chain.checks else None)
            out["refused"] = chain.refused
            out["problems"] = [p.payload() for p in problems]
            files, dataless = official_files(root, pid)
            published = last.files if last else {}
            changed = sorted(r for r in set(files) | set(published)
                             if (files.get(r) or {}).get("sha256") != (published.get(r) or {}).get("sha256"))
            out["pending_publish"] = {"count": len(changed), "files": changed[:200], "dataless": dataless}
            if role == "pi":
                try:
                    listed = proposals.listing(root)
                    out["inbox"] = sum(1 for p in listed["proposals"] if p["state"] == "pending")
                except Exception as exc:  # noqa: BLE001 -- a status never fails on the inbox
                    out["inbox_error"] = str(exc)
        return out
    try:
        state = read_state(pid)
    except ShareError:
        state = None
    if state and Path(state.get("working_copy", "")) == root.resolve():
        out["joined"] = {k: state.get(k) for k in ("seq", "pi", "pi_fingerprint", "trusted_on_first_join", "synced",
                                                    "official_copies")}
        out["warnings"] = warnings(state.get("pi_key"))
        if out["reachable"]:
            seen = available(root)
            chain = seen["chain"]
            last = chain.last_good
            out["last_snapshot"] = last.payload() if last else None
            out["verification"] = (chain.checks[last.seq].state if last and last.seq in chain.checks else None)
            out["behind"] = bool(last and last.seq > state["seq"])
            out["refused"] = chain.refused
            out["problems"] = [p.payload() for p in seen["problems"]]
    return out


def inbox_marker(root: Path) -> tuple[str, ...] | None:
    """What the server's watcher compares to notice a new proposal or
    outcome: the names of complete proposal directories in members' areas
    (a listing, nothing opened). None when this is not the PI's project
    with a reachable shared folder."""
    try:
        pid = _project_id(root)
        team = people.read_team(root)
        me = people.current_member_id()
    except Exception:  # noqa: BLE001
        return None
    if team.state != "present" or team.pi is None or team.pi.id != me:
        return None
    shared = ex_mod.configured(pid)
    area = shared / ex_mod.PROPOSALS_AREA if shared else None
    if area is None or not area.is_dir():
        return None
    names = []
    for member in sorted(os.listdir(area)):
        folder = area / member
        if not people.is_member_id(member) or not folder.is_dir():
            continue
        for name in sorted(os.listdir(folder)):
            if ex_mod.PROPOSAL_ID_RE.match(name) and (folder / name / ex_mod.READY_FILE).exists():
                names.append(f"{member}/{name}")
    return tuple(names)

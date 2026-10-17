"""Proposals and approval (DESIGN.md 12.3, 12.4; task V8 phase 3).

In a project with members, a member's work reaches the official version
only through a proposal the PI approves. A proposal is an object -- a
directory written once to the exchange (`researchos.exchange`): phase 3
creates, stores and decides it on one machine; phase 4 moves the same
directory through the shared drive. A single-person project (no team file,
or only its PI, 12.1) has none of this: changes are official when they are
made, as before, and every function here refuses with `single_person`.

The member's side
-----------------

A member's working copy was made from an official snapshot. Its **base**
-- the manifest of that snapshot (every file: path, size, SHA-256) and the
text of its text files -- is kept on the member's machine, in
`<home>/working/<project id>/` (`base.json`, `blobs/<sha256>`). Phase 3
makes it with `start_working_copy` (one machine, the official folder read
directly); phase 4's sync writes the same base from a published snapshot.
Its snapshot id is the manifest's hash (`snapshot_id`).

`make_proposal(working_copy, kind, sentence, ...)` computes, without
writing anything, what differs from the base: the attempt-table row a new
attempt adds or the verdict a row changes, a variable card's new version
file, and the files added, changed or removed (`.researchos/` and the
attempt table itself excepted: the table travels as rows). **What it
carries depends on its kind** (12.13): a verdict change its row only; a
new attempt its row and the files linked to that attempt (its step
scripts, named by the row's description, and the files attached to it
with 「关联文件」); a files proposal the files the member chose (all that
differ, unless `only` names them); a variable version its version file.
`only` -- the member's own choice -- replaces the kind's default; `add`
and `leave` add files to it or untick them. Every file that differs is in the preview's
`candidates`, with whether it is carried, whether it is linked to the
attempt, and whether it was part of one of the member's returned
proposals (`returned`). What it does not carry -- a file not chosen,
another new row, a change outside the table's rows -- is listed in
`not_carried`, so nothing is dropped silently. The member sees it (the
CLI's dry run) before `write_proposal` writes it.

The kinds, and what each carries besides its files:

- `attempt_new` -- the new attempt-table row's fields;
- `verdict_change` -- the attempt number, the old and new verdict and
  result; when the new verdict abandons the route (a marker listed in
  `attempts.toml`'s `abandoned_verdicts`, or ☠ always) the result and a
  reason are REQUIRED (12.4);
- `variable_version` -- the card id and the version file's bytes, and the
  member's own answer to "was the output built with this definition"
  (`attested`, 9.11), which the PI's confirmation records as theirs;
- `files` -- only files;
- `route_link` -- a 「源自」 line between two official attempts drawn or
  removed on the route map (14.2): `op` (`add` | `remove`), the child, the
  parent and the member's note; no files. Approving writes it to the
  official `mappings.toml` through the same writer and rules as a line the
  PI draws (a missing end or a circle then is a conflict, nothing applied).

A new attempt may also name the official attempts it grew out of
(`parents`); approving it adds those lines once its row is written.

A file is carried as text under the proposal's `files/` (UTF-8, at most
5 MB) or as a blob by content (large or binary), always with the base
fingerprint it was changed from.

The PI's side
-------------

`what_changes(proposal, official_root)` says exactly what approving would
do, reading only: the attempt-table diff (the map editor's own preview),
each file's line diff or data summary, the variable version that would be
confirmed, and the **conflicts** -- a file (or the attempt row, or the
card) changed on both sides since the base. Research OS never merges two
people's edits of one file: a proposal with a conflict cannot be approved
until the member proposes again against a newer snapshot.

`approve(root, id, note=...)` -- only by the PI's Research OS (me.toml's id
is the team's `pi`) -- applies, in this order, under the project lock with
the write-time identity re-check (9.4, 9.7):

1. files copied in from the proposal, verified by hash; a replaced file is
   kept under `.researchos/backups/approved/<proposal>/` (a hard link: the
   bytes are not copied) and every version goes to the history store
   (12.6) with `who` = the member and `approved_by` = the PI;
2. the attempt row appended or updated through the map editor's write path
   (`researchos.webapp.mapedit.apply_edit`: backup, atomic write,
   re-ingest);
3. a variable version written as the card's next version file (created
   exclusively, never over a file) and confirmed through the card's own
   confirm path (`researchos.records.cards.confirm`), attested from the
   proposal, its log entry carrying `by`, `approved_by`, `proposal`, note;
4. on an abandonment, the attempt's evidence frozen (12.4): its step
   scripts and the outputs they wrote, by fingerprint, and their text into
   the history store;
5. one entry in `.researchos/decisions.toml` (`researchos.records.decisions`)
   with `by` = the member, `approved_by` = the PI and the note --
   `abandoned` with the result, reason and evidence, else `approved`;
6. the outcome, written to the exchange last.

Nothing is applied until everything can be: every file's content is
first copied beside its target and verified by hash, and every target's
folder made, before the first file is placed -- a proposal whose content
does not match, or whose path cannot be placed, is refused with nothing
applied. From the first change on, the approval keeps a journal
(`.researchos/backups/approved/<proposal>.journal`, one line per step,
written BEFORE the step and again after it) so that an approval
interrupted between steps -- a killed process, a full disk, a shared drive
gone offline before the outcome -- is finished by approving again: what
the journal says was applied is not applied twice and is not mistaken for
a change on the official side (a variable version file it wrote, a table
row it added), and once the decision is recorded approving again only
writes the outcome. A proposal whose approval has begun can no longer be
returned (`approval_started`): it is approved again, never half-returned.
`return_` (with a reason) and `discuss` (a note to the member; the
proposal stays open) write an outcome and nothing else.

Both sides changed: a file changed in the official version since the
proposal's base is a conflict -- Research OS never merges two people's
edits of one file (12.3). The attempt table conflicts by ROW (12.12): a
new attempt only when the number it takes is taken in the official table
now; a verdict change only when its row changed there since the base (the
base table found by `table_base_sha256` in the history store or a
published snapshot, read with the attempts parser). The PI editing other
rows blocks neither. An abandonment's reason is
written into the attempt table's result cell (「放弃理由：…」) unless the
row already says it, and into the decision with the evidence.

Not a node until approved
-------------------------

A pending proposal lives only in the exchange, which may not lie inside
the project (`researchos.exchange.check_location`), so no scan and no
reader of the official index -- tree, lineage, canvas, trace, citations,
variables -- ever sees it. The one reader that does is `listing`: the
proposer's 「我的提议」 and the PI's inbox; anyone else sees none.
"""

from __future__ import annotations

import contextlib
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

from researchos import db, history, paths, people, signing
from researchos import exchange as exchange_mod
from researchos.exchange import ExchangeError, LocalExchange, StoredProposal
from researchos.records import decisions as decisions_mod
from researchos.records import files as record_files
from researchos.records import ledger as ledger_mod
from researchos.records.identity import IdentityState, read_identity
from researchos.records.situation import READ_NOW, index_db_path, write_guard

logger = logging.getLogger(__name__)

KINDS = ("attempt_new", "verdict_change", "variable_version", "files", "route_link")
ROUTE_OPS = ("add", "remove")
ACTIONS = ("added", "changed", "removed")
STORES = ("text", "blob")
OUTCOME_ACTS = ("approved", "returned", "discussed")
FINAL_ACTS = ("approved", "returned")
#: The default abandonment marker (12.4: "or the default ☠️"); matched
#: without its variation selector, so ☠ and ☠️ both count.
DEFAULT_ABANDON_MARKER = "☠"
TEXT_LIMIT = history.TEXT_LIMIT
FORMAT = 1
MAX_SENTENCE = 2000
WORKING_DIRNAME = "working"
BASE_FILENAME = "base.json"
APPROVED_BACKUPS = "approved"
ROW_FIELDS = ("date", "description", "variables", "result", "verdict")
#: The record files a working copy starts with (besides `variables/`).
_RECORD_FILES = ("project.toml", "attempts.toml", "mappings.toml", "team.toml", "judgements.toml", "notes.toml",
                 "decisions.toml", "canvas.json", "papers.toml")

_PROPOSAL_HEADER = "# 一份提议：成员工作副本里的改动，PI 批准后才进入正式版本。由 Research OS 写入，写成后不再改动。\n"
_OUTCOME_HEADER = "# PI 对一份提议的答复。由 Research OS 写入，写成后不再改动。\n"
_VERSION_FILE_RE = re.compile(r"^\.researchos/variables/([^/]+)/v([1-9][0-9]*)\.toml$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ATTEMPT_ID_RE = re.compile(r"^attempt:[^\x00-\x1f]+#[^\x00-\x1f#]+$")

#: Product language (8.8) for every refusal.
MESSAGES = {
    "single_person": "这是单人项目：改动直接生效，不需要提议和批准",
    "team_unreadable": "成员文件（team.toml）当前无法读取，无法判断谁是 PI",
    "no_me": "请先设置“我是谁”（researchos me set）",
    "not_pi": "只有 PI 可以批准或退回提议",
    "pi_changes_directly": "PI 直接修改正式版本，不需要提议",
    "not_member": "你不在这个项目的成员名单里",
    "not_in_team": "你还不是这个项目的成员：请 PI 把你加入团队，然后同步",
    "no_base": "这个工作副本还没有记录它来自哪个正式版本",
    "bad_kind": "不支持的提议类型",
    "nothing": "和正式版本相比没有可以提议的改动",
    "ambiguous": "有不止一处符合的改动，请指明是哪一个",
    "reason_required": "放弃一条路线时，结果和理由都必须填写",
    "no_attempt_table": "找不到尝试表（attempts.toml）",
    "attested_required": "请回答：现有的输出文件是否按这个定义生成（是 / 否 / 不确定）",
    "changed_since_shown": "工作副本在你查看之后又有改动，请重新查看",
    "decided": "这份提议已经有了结论",
    "not_approvable": "这份提议现在不能批准",
    "not_yours": "这份提议不是你的",
    "invalid": "提议的内容不对",
    "wrong_project": "这份提议属于另一个项目",
    "empty_reason": "请写明退回的理由",
    "empty_text": "请写下要讨论的内容",
    "cannot_sign": "无法签名",
    "approval_started": "这份提议的批准已经开始应用，不能再退回：请再次批准以完成",
    # A 「源自」 line (14.2), proposed by a member against the official version they synced to.
    "bad_route": "请指明这条连线从哪个尝试连到哪个尝试",
    "self_link": "一个尝试不能源自它自己",
    "circle": "这条连线会绕成一个圈：它要连的来处本身就源自它",
    "attempt_not_found": "连线的一端不在你上次同步的正式版本里：请同步后再试",
    "line_exists": "这条连线已经在正式版本里了",
    "no_such_line": "正式版本里没有这条连线",
}


class ProposalRefused(Exception):
    """Nothing was written. `code` names the cause (`MESSAGES`); `details`
    carries what the page shows (conflicts, problems)."""

    def __init__(self, code: str, message: str, *, message_zh: str | None = None,
                 details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message_zh = message_zh or MESSAGES.get(code, "提议没有完成")
        self.details = dict(details or {})


def _now_iso(now: Callable[[], datetime] | None = None) -> str:
    moment = (now or (lambda: datetime.now().astimezone()))()
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.isoformat(timespec="seconds")


# -- people ------------------------------------------------------------------------------


def _me_id() -> str:
    try:
        member = people.current_member_id()
    except people.PeopleError as exc:
        raise ProposalRefused("no_me", str(exc), message_zh=exc.message_zh) from exc
    if member is None:
        raise ProposalRefused("no_me", "who you are is not set on this machine: researchos me set --name N --email E")
    return member


def _not_in_team(me: str | None) -> ProposalRefused:
    return ProposalRefused("not_in_team", f"{me or 'you'} is not in this project's team yet: the PI adds you "
                                          f"(researchos team add), then you sync")


def joined_not_pi(root: str | Path, me: str | None) -> bool:
    """`root` is a working copy this machine joined from a shared folder,
    and `me` is not the PI it trusted: its official version is the PI's,
    whatever its team file says -- a copy whose team does not list me yet
    is not a project of one (12.12)."""
    from researchos import share  # noqa: PLC0415 -- share imports this module

    root = Path(root)
    try:
        state = share.read_state(_project_id(root))
    except (ProposalRefused, share.ShareError):
        return False
    if not state or Path(str(state.get("working_copy", ""))) != root.resolve():
        return False
    return me is None or state.get("pi") != me


def _current_me() -> str | None:
    try:
        return people.current_member_id()
    except people.PeopleError:
        return None


def team_of(root: str | Path) -> people.TeamRead:
    """The team, refused unless it is a team project that can be read."""
    team = people.read_team(root)
    mode = people.team_mode(team)
    if mode == "single":
        if joined_not_pi(root, _current_me()):
            raise _not_in_team(_current_me())
        raise ProposalRefused("single_person", "a single-person project has no proposals: changes are official when made")
    if mode == "unknown":
        raise ProposalRefused("team_unreadable", f"{team.path} cannot be read ({team.state}: {team.error})")
    return team


def role_of(root: str | Path) -> tuple[str, str | None, people.TeamRead]:
    """("pi" | "member" | "none" | "single" | "unknown", my id, team)."""
    team = people.read_team(root)
    mode = people.team_mode(team)
    try:
        me = people.current_member_id()
    except people.PeopleError:
        me = None
    if mode == "single" and joined_not_pi(root, me):
        return "none", me, team  # 12.12: not yet in the team, not a project of one
    if mode != "team":
        return mode, me, team
    if me is None:
        return "none", None, team
    member = next((m for m in team.members if m.id == me), None)
    return (member.role if member is not None else "none"), me, team


def _require_pi(root: Path) -> str:
    team = team_of(root)
    me = _me_id()
    if team.pi is None or team.pi.id != me:
        raise ProposalRefused("not_pi", f"only the PI ({team.pi.id if team.pi else 'none'}) approves or returns; you are {me}")
    return me


def _require_member(root: Path) -> str:
    team = team_of(root)
    me = _me_id()
    member = next((m for m in team.members if m.id == me), None)
    if member is None:
        raise _not_in_team(me)
    if member.role == "pi":
        raise ProposalRefused("pi_changes_directly", "the PI changes the official version directly; proposals are for members")
    return me


def _project_id(root: Path) -> str:
    got = read_identity(root)
    if got.state is not IdentityState.PRESENT or got.identity is None:
        raise ProposalRefused("invalid", f"{root} has no readable project identity", message_zh="项目身份文件无法读取")
    return got.identity.id


# -- manifests -----------------------------------------------------------------------------


def _version_file_rels(root: Path) -> list[str]:
    from researchos.records import variables as V  # noqa: PLC0415

    found = []
    for directory in V.card_dirs(root) or []:
        for number, path in sorted(V._version_numbers(directory).items()):
            found.append(f"{paths.PROJECT_DIRNAME}/{V.VARIABLES_DIRNAME}/{directory.name}/{path.name}")
    return found


def project_files(root: str | Path) -> list[str]:
    """The files a snapshot's manifest lists for proposals: every file of
    the inventory walk (hidden and tool directories left out, as a scan
    does) and the variable cards' version files."""
    from researchos.ingest import files as files_ingest  # noqa: PLC0415

    root = Path(root)
    return [rel for rel, _category in files_ingest.iter_files(root)] + _version_file_rels(root)


def fingerprint(path: Path) -> dict[str, Any] | None:
    """{sha256, size} of a file; {dataless, size} for one only in the
    cloud (never downloaded); None when it is not there."""
    try:
        st = path.lstat()
    except OSError:
        return None
    if not path.is_file() or path.is_symlink():
        return None
    if paths.is_dataless(path):
        return {"sha256": None, "size": st.st_size, "dataless": True}
    sha, size = exchange_mod.sha256_of(path)
    return {"sha256": sha, "size": size}


def manifest(root: str | Path) -> dict[str, dict[str, Any]]:
    root = Path(root)
    out = {}
    for rel in project_files(root):
        got = fingerprint(root / rel)
        if got is not None:
            out[rel] = got
    return out


def snapshot_id(files: Mapping[str, Mapping[str, Any]]) -> str:
    """The id of an official version as of a manifest (phase 4's snapshots
    replace it with their own)."""
    rows = [[rel, entry.get("size"), entry.get("sha256")] for rel, entry in sorted(files.items())]
    return "sha256:" + hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def _is_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _text_of(path: Path, size: int | None) -> str | None:
    if size is None or size > TEXT_LIMIT:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data.decode("utf-8") if _is_text(data) else None


# -- the member's base ---------------------------------------------------------------------


def base_dir(project_id: str) -> Path:
    from researchos.records.lock import PROJECT_ID_RE  # noqa: PLC0415

    if not isinstance(project_id, str) or not PROJECT_ID_RE.match(project_id):
        # A folder name on this machine: never taken from anything else.
        raise ProposalRefused("invalid", f"not a project id: {project_id!r}", message_zh="项目编号不对")
    return paths.researchos_home() / WORKING_DIRNAME / project_id


@dataclass(frozen=True)
class Base:
    project: str
    snapshot: str
    working_copy: str
    official: str | None
    made: str
    files: Mapping[str, Mapping[str, Any]]

    def text(self, rel: str) -> str | None:
        entry = self.files.get(rel)
        sha = entry.get("sha256") if entry else None
        if not sha or not entry.get("text"):
            return None
        try:
            return (base_dir(self.project) / exchange_mod.BLOBS_DIRNAME / sha).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None


def read_base(project_id: str) -> Base | None:
    try:
        data = json.loads((base_dir(project_id) / BASE_FILENAME).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ProposalRefused("no_base", f"the base of {project_id} cannot be read: {exc}") from exc
    return Base(project=data["project"], snapshot=data["snapshot"], working_copy=data["working_copy"],
                official=data.get("official"), made=data.get("made", ""), files=data.get("files", {}))


def write_base(project_id: str, working_copy: Path, files: Mapping[str, Mapping[str, Any]], *,
               texts: Mapping[str, Path], official: str | None = None) -> Base:
    """Record a working copy's base: the manifest it was made from, and the
    text of its text files (for the member's own diffs)."""
    directory = base_dir(project_id)
    blobs = directory / exchange_mod.BLOBS_DIRNAME
    blobs.mkdir(parents=True, exist_ok=True)
    stored: dict[str, dict[str, Any]] = {}
    for rel, entry in files.items():
        item = dict(entry)
        source = texts.get(rel)
        if source is not None and entry.get("sha256"):
            target = blobs / entry["sha256"]
            if not target.exists():
                tmp = blobs / f".{entry['sha256']}.tmp-{secrets.token_hex(6)}"
                exchange_mod.copy_file(source, tmp)
                os.replace(tmp, target)
            item["text"] = True
        stored[rel] = item
    # The snapshot id is over the files `manifest` lists (the record files
    # a published snapshot also carries left out), so a base made by
    # joining compares equal to the official folder it came from.
    listed = {rel: e for rel, e in files.items() if not rel.startswith(paths.PROJECT_DIRNAME + "/") or _VERSION_FILE_RE.match(rel)}
    base = {
        "format": FORMAT, "project": project_id, "snapshot": snapshot_id(listed), "working_copy": str(working_copy),
        "official": official, "made": _now_iso(), "files": stored,
    }
    record_files.durable_write(directory / BASE_FILENAME,
                               (json.dumps(base, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    got = read_base(project_id)
    assert got is not None
    return got


def start_working_copy(official_root: str | Path, working_copy: str | Path, *, exchange: str | Path | None = None) -> Base:
    """Make a member's working copy from the official folder and record its
    base -- the single-machine stand-in for joining from a published
    snapshot (phase 4). `working_copy` must not exist or be empty. The
    project's files and its record files (identity, attempt-table config,
    mappings, team, ledgers, cards) are copied; backups are not."""
    official, dest = Path(official_root), Path(working_copy)
    pid = _project_id(official)
    if dest.exists() and any(dest.iterdir()):
        raise ProposalRefused("invalid", f"{dest} is not empty; a working copy starts in an empty folder",
                              message_zh="工作副本要放在一个空文件夹里")
    dest.mkdir(parents=True, exist_ok=True)
    files = manifest(official)
    texts: dict[str, Path] = {}
    for rel, entry in files.items():
        if entry.get("dataless"):
            raise ProposalRefused("invalid", f"{rel} is still in the cloud; download it first",
                                  message_zh="有文件还在云端，请先下载")
        exchange_mod.copy_file(official / rel, dest / rel)
        if _text_of(official / rel, entry.get("size")) is not None:
            texts[rel] = official / rel
    project_dir = paths.project_dir(official)
    for name in _RECORD_FILES:
        source = project_dir / name
        if source.is_file() and not (dest / paths.PROJECT_DIRNAME / name).exists():
            exchange_mod.copy_file(source, dest / paths.PROJECT_DIRNAME / name)
    variables = project_dir / "variables"
    if variables.is_dir():
        for path in sorted(variables.rglob("*")):
            target = dest / paths.PROJECT_DIRNAME / "variables" / path.relative_to(variables)
            if path.is_file() and not path.is_symlink() and not target.exists():
                exchange_mod.copy_file(path, target)
    if exchange is not None:
        exchange_mod.configure(pid, exchange, dest)
    return write_base(pid, dest.resolve(), files, texts=texts, official=str(official.resolve()))


# -- the attempt table ---------------------------------------------------------------------


def _attempts_config(root: Path):
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    try:
        return attempts_ingest.load_config(root)
    except attempts_ingest.AttemptsConfigError:
        return None


def abandon_markers(root: str | Path) -> list[str]:
    """The verdict markers that mean "abandoned": `abandoned_verdicts` from
    `attempts.toml` when declared, and ☠ always."""
    config = _attempts_config(Path(root))
    declared = config.abandoned_verdicts if config is not None else None
    markers = [DEFAULT_ABANDON_MARKER]
    if isinstance(declared, list):
        markers += [m for m in declared if isinstance(m, str) and m.strip()]
    return list(dict.fromkeys(markers))


def is_abandonment(verdict: str, markers: Iterable[str]) -> bool:
    return any(marker in (verdict or "") for marker in markers)


def _rows(text: str | None, config) -> dict[str, dict[str, str]] | None:
    """{number: fields} of the attempt table in `text`, None when it cannot
    be read."""
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    if text is None or config is None:
        return None
    try:
        parsed = attempts_ingest.parse_attempts_table(text, config.heading, config.columns)
    except attempts_ingest.AttemptsConfigError:
        return None
    rows: dict[str, dict[str, str]] = {}
    for row in parsed:
        rows.setdefault(row.number, {k: getattr(row, k) for k in ROW_FIELDS})
    return rows


def _outside_rows(text: str | None) -> list[str]:
    return [line for line in (text or "").splitlines() if not line.strip().startswith("|")]


def _table_rows(root: Path, config) -> dict[str, dict[str, str]]:
    try:
        return _rows((root / config.file).read_text(encoding="utf-8"), config) or {}
    except (OSError, UnicodeDecodeError):
        return {}


# -- 「源自」 lines between attempts (14.2) ------------------------------------------------


def _line_number(value: Any, table: str) -> str:
    """One end of a 「源自」 line as its number in the attempt table `table`:
    given as the node id (`attempt:<table>#<n>`), `<table>#<n>` or the number."""
    text = value.strip().removeprefix("attempt:") if isinstance(value, str) else ""
    file, sep, number = text.rpartition("#")
    if not number.strip():
        raise ProposalRefused("bad_route", f"{value!r} does not name an attempt")
    if sep and file != table:
        raise ProposalRefused("attempt_not_found", f"{value!r} is not a row of the attempt table {table}")
    return number.strip()


def _mappings_text(root: Path, base: Base | None = None) -> str | None:
    """The mappings file as the official version has it: the base's copy
    for a working copy (the copy's own when the base has none), else the file."""
    from researchos.ingest import mappings as mappings_ingest  # noqa: PLC0415

    rel = mappings_ingest.MAPPINGS_RELATIVE_PATH
    text = base.text(rel) if base is not None else None
    if text is None:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
    return text


def _lines_in(root: Path, text: str | None) -> dict[str, set[str]]:
    """{child: the attempts it grew out of}, each as `<table>#<n>`."""
    from researchos.ingest import mappings as mappings_ingest  # noqa: PLC0415

    out: dict[str, set[str]] = {}
    for child, parent, kind in mappings_ingest._entries_by_key(root, text):
        if kind == mappings_ingest.DERIVES_FROM:
            out.setdefault(child, set()).add(parent)
    return out


def _route_of(root: Path, config, base: Base, route: Any) -> dict[str, str]:
    """A `route_link` proposal's [route], checked against the official
    version the copy was synced to: both ends rows of its table, no line to
    itself, and -- to add -- not there yet and closing no circle; to remove,
    there."""
    from researchos.ingest import mappings as mappings_ingest  # noqa: PLC0415

    if config is None:
        raise ProposalRefused("no_attempt_table", "no attempt table is configured (.researchos/attempts.toml)")
    if not isinstance(route, Mapping) or route.get("op") not in ROUTE_OPS:
        raise ProposalRefused("bad_route", "a route_link proposal names its op (add or remove), the child and the parent")
    child, parent = (_line_number(route.get(k), config.file) for k in ("child", "parent"))
    if child == parent:
        raise ProposalRefused("self_link", f"attempt {child} cannot grow out of itself")
    rows = _rows(base.text(config.file), config) or {}
    missing = [n for n in (child, parent) if n not in rows]
    if missing:
        raise ProposalRefused("attempt_not_found", f"row {', '.join(missing)} is not in the official attempt table "
                                                   f"this copy was synced to")
    c, p = f"{config.file}#{child}", f"{config.file}#{parent}"
    lines = _lines_in(root, _mappings_text(root, base))
    if route["op"] == "add" and p in lines.get(c, ()):
        raise ProposalRefused("line_exists", f"#{child} already grows out of #{parent} in the official version")
    if route["op"] == "add" and mappings_ingest._reaches(lines, p, c):
        raise ProposalRefused("circle", f"#{parent} already grows out of #{child}: the line would close a circle")
    if route["op"] == "remove" and p not in lines.get(c, ()):
        raise ProposalRefused("no_such_line", f"the official version has no line #{child} grew out of #{parent}")
    out = {"op": route["op"], "child": "attempt:" + c, "parent": "attempt:" + p}
    note = " ".join(str(route.get("note") or "").split())
    if note:
        out["note"] = note
    return out


def _parents_of(config, rows: Mapping[str, Any], number: str, parents: Iterable[Any]) -> list[str]:
    """What a new attempt grew out of (14.2), as node ids: official rows only."""
    out: list[str] = []
    for value in parents:
        parent = _line_number(value, config.file)
        if parent == number:
            raise ProposalRefused("self_link", f"attempt {number} cannot grow out of itself")
        if parent not in rows:
            raise ProposalRefused("attempt_not_found", f"row {parent} is not in the official attempt table")
        if f"attempt:{config.file}#{parent}" not in out:
            out.append(f"attempt:{config.file}#{parent}")
    return out


def parent_words(attempt: Mapping[str, Any], lang: str = "zh") -> list[str]:
    """The lines approving a new attempt adds (14.2: what it grew out of),
    each said as approving a line says it, in `lang` (13.6)."""
    from researchos import settings  # noqa: PLC0415

    child = str(attempt.get("number") or "")
    return [settings.say("批准后加上连线：#{child} 源自 #{parent}", "Approving adds the line: #{child} grew out of #{parent}",
                         lang, child=child, parent=str(p).rpartition("#")[2]) for p in attempt.get("parents") or []]


def _route_summary(route: Any) -> dict[str, Any] | None:
    if not isinstance(route, Mapping):
        return None
    return {**route, "child_number": str(route.get("child", "")).rpartition("#")[2],
            "parent_number": str(route.get("parent", "")).rpartition("#")[2]}


def _apply_line(root: Path, op: str, child: str, parent: str, note: str | None, *, by: str) -> bool:
    """One 「源自」 line into the official mappings.toml (14.2) through the
    writer a line the PI draws goes through, `by` the member; its ends were
    found in the attempt table, so it is written even while the index lags.
    False when the file already says so."""
    from researchos.ingest import mappings as mappings_ingest  # noqa: PLC0415

    try:
        if op == "add":
            mappings_ingest.add_mapping(root, child, parent, mappings_ingest.DERIVES_FROM, note=note, by=by,
                                        check_index=False)
        else:
            mappings_ingest.delete_mapping(root, child, parent, mappings_ingest.DERIVES_FROM)
    except mappings_ingest.MappingsWriteError as exc:
        if exc.code in ("duplicate", "not_found"):
            return False
        raise ProposalRefused("not_approvable", f"the line {child} -> {parent} was not written ({exc}); approve again "
                              f"once mappings.toml can be written",
                              message_zh="连线没有写进正式版本的连线记录（mappings.toml），处理后请再次批准") from exc
    return True


# -- making a proposal (the member) ---------------------------------------------------------


@dataclass
class Draft:
    """A proposal computed, not yet written. `data` is proposal.toml's
    content without `id` and `at`; `texts` / `blobs` the working-copy
    files whose content it carries."""

    data: dict[str, Any]
    texts: dict[str, Path] = field(default_factory=dict)
    blobs: dict[str, Path] = field(default_factory=dict)
    preview: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Ties a write to the dry run that was shown (9.12: an answer
        belongs to the question that was shown)."""
        return hashlib.sha256(json.dumps(self.data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _file_changes(root: Path, base: Base, work: Mapping[str, Mapping[str, Any]], *, skip: set[str],
                  only: set[str] | None) -> list[dict[str, Any]]:
    changes = []
    for rel in sorted(set(base.files) | set(work)):
        if rel.startswith(paths.PROJECT_DIRNAME + "/") or rel in skip:
            continue
        if only is not None and rel not in only:
            continue
        old, new = base.files.get(rel), work.get(rel)
        if new is not None and new.get("dataless"):
            raise ProposalRefused("invalid", f"{rel} is still in the cloud; download it before proposing",
                                  message_zh="有文件还在云端，请先下载")
        if old is None and new is not None:
            action = "added"
        elif old is not None and new is None:
            action = "removed"
        elif old is not None and new is not None and old.get("sha256") != new.get("sha256"):
            action = "changed"
        else:
            continue
        item: dict[str, Any] = {"path": rel, "action": action}
        if new is not None:
            item.update(sha256=new["sha256"], size=new["size"])
            item["store"] = "text" if _text_of(root / rel, new["size"]) is not None else "blob"
        if old is not None:
            item.update(base_sha256=old.get("sha256"), base_size=old.get("size"))
        changes.append(item)
    return changes


def attempt_files(root: Path, config, number: str, description: str) -> set[str]:
    """The files linked to attempt `number` in the copy as it is (12.13):
    the step scripts its description names, resolved in `steps_dir` the
    way the attempts ingest does (never guessed), and the files a
    `belongs_to` mapping attaches to it (「关联文件」). Reads only."""
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415
    from researchos.ingest import mappings as mappings_ingest  # noqa: PLC0415

    found: set[str] = set()
    steps_dir = getattr(config, "steps_dir", None)
    if steps_dir:
        refs = attempts_ingest._extract_step_refs(description or "")
        names, _broken = attempts_ingest._resolve_step_files(root / steps_dir, refs)
        found |= {posixpath.normpath(f"{steps_dir.rstrip('/')}/{name}") for name in names}
    try:
        text = (root / mappings_ingest.MAPPINGS_RELATIVE_PATH).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = None
    target = f"{config.file}#{number}"
    for frm, to, kind in mappings_ingest._entries_by_key(root, text):
        if kind == mappings_ingest.ATTEMPT_LINK and to == target:
            found.add(frm)
    return found


def _returned_paths(root: Path, pid: str, me: str) -> dict[str, list[str]]:
    """Each file that was part of one of my returned proposals, with those
    proposals' ids (12.13: flagged when it is proposed again). Nothing when
    the shared folder cannot be read now."""
    out: dict[str, list[str]] = {}
    try:
        ex = exchange_mod.for_project(pid, root)
        found = ex.list_proposals(me).proposals
        for stored in found:
            if stored.data.get("project") != pid or state_of(ex, stored, found)["state"] != "returned":
                continue
            for item in stored.data.get("file") or []:
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    out.setdefault(item["path"], []).append(stored.id)
    except Exception:  # noqa: BLE001 -- no shared folder now: nothing flagged, nothing wrong
        return {}
    return out


def _pick(candidates: list[str], given: str | None, what: str) -> str:
    if given is not None:
        if given not in candidates:
            raise ProposalRefused("nothing", f"{what} {given!r} is not among the changes ({', '.join(candidates) or 'none'})")
        return given
    if not candidates:
        raise ProposalRefused("nothing", f"no {what} differs from the official version")
    if len(candidates) > 1:
        raise ProposalRefused("ambiguous", f"more than one {what} differs ({', '.join(candidates)}); name one")
    return candidates[0]


def make_proposal(
    working_copy: str | Path,
    kind: str,
    sentence: str,
    *,
    number: str | None = None,
    result: str | None = None,
    reason: str | None = None,
    card: str | None = None,
    attested: str | None = None,
    only: Iterable[str] | None = None,
    supersedes: str | None = None,
    add: Iterable[str] | None = None,
    leave: Iterable[str] | None = None,
    route: Mapping[str, Any] | None = None,
    parents: Iterable[str] | None = None,
) -> Draft:
    """What a proposal of `kind` from `working_copy` would carry (module
    docstring). Reads only. `route` is a `route_link`'s {op, child,
    parent, note}; `parents` what a new attempt grew out of. A line says
    what it is, so a `route_link` needs no sentence of its own."""
    root = Path(working_copy)
    if kind not in KINDS:
        raise ProposalRefused("bad_kind", f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
    if not isinstance(sentence, str) or len(sentence) > MAX_SENTENCE or (not sentence.strip() and kind != "route_link"):
        raise ProposalRefused("invalid", "a proposal says what it is in a sentence of your own",
                              message_zh="请用一句话说明这份提议")
    me = _require_member(root)
    pid = _project_id(root)
    base = read_base(pid)
    if base is None or Path(base.working_copy) != root.resolve():
        raise ProposalRefused("no_base", f"{root} has no recorded base for project {pid} on this machine")
    work = manifest(root)
    config = _attempts_config(root)
    table = config.file if config is not None else None
    data: dict[str, Any] = {
        "format": FORMAT, "project": pid, "by": me, "kind": kind, "sentence": sentence.strip(), "base": base.snapshot,
    }
    if supersedes:
        if not exchange_mod.PROPOSAL_ID_RE.match(supersedes):
            raise ProposalRefused("invalid", f"not a proposal id: {supersedes!r}")
        data["supersedes"] = supersedes
    not_carried: list[dict[str, str]] = []
    preview: dict[str, Any] = {}
    linked: set[str] = set()
    if kind == "route_link":
        data["route"] = _route_of(root, config, base, route)

    # The attempt table travels as rows.
    if table is not None and (table in base.files or table in work):
        base_text = base.text(table)
        work_text = _text_of(root / table, work.get(table, {}).get("size"))
        base_rows, work_rows = _rows(base_text, config), _rows(work_text, config)
        if base_rows is not None and work_rows is not None:
            new_rows = [n for n in work_rows if n not in base_rows]
            changed = [n for n in work_rows if n in base_rows and work_rows[n] != base_rows[n]]
            removed = [n for n in base_rows if n not in work_rows]
            carried: set[str] = set()
            table_base = (base.files.get(table) or {}).get("sha256")
            if kind == "attempt_new":
                chosen = _pick(new_rows, number, "new attempt row")
                data["attempt"] = {"number": chosen, **work_rows[chosen], "table_base_sha256": table_base}
                if parents:
                    data["attempt"]["parents"] = _parents_of(config, base_rows, chosen, parents)
                if is_abandonment(work_rows[chosen]["verdict"], abandon_markers(root)):
                    # 12.4 / 14.3: a route abandoned from the start keeps its basis as one abandoned later does.
                    said = (reason or "").strip()
                    if not work_rows[chosen]["result"].strip() or not said:
                        raise ProposalRefused("reason_required", f"the new attempt {chosen} is abandoned: it needs its "
                                                                 f"result in the row and a reason (--reason)")
                    data["attempt"].update(reason=said, abandons="yes")
                carried.add(chosen)
                linked = attempt_files(root, config, chosen, work_rows[chosen].get("description") or "")
            elif kind == "verdict_change":
                verdicts = [n for n in changed if work_rows[n]["verdict"] != base_rows[n]["verdict"]]
                chosen = _pick(verdicts, number, "attempt verdict")
                old, new = base_rows[chosen], work_rows[chosen]
                verdict: dict[str, Any] = {"number": chosen, "old": old["verdict"], "new": new["verdict"],
                                           "old_result": old["result"], "result": new["result"] if result is None else result,
                                           "table_base_sha256": table_base}
                if reason is not None and reason.strip():
                    verdict["reason"] = reason.strip()
                if is_abandonment(new["verdict"], abandon_markers(root)):
                    if not verdict["result"].strip() or not verdict.get("reason"):
                        raise ProposalRefused("reason_required", f"abandoning attempt {chosen} needs its result and a "
                                                                 f"reason (--result, --reason)")
                    verdict["abandons"] = "yes"
                data["verdict"] = verdict
                carried.add(chosen)
                others = [k for k in ROW_FIELDS if k not in ("verdict", "result") and old[k] != new[k]]
                if others:
                    not_carried.append({"path": table, "reason": f"row {chosen}: changes to {', '.join(others)} are not "
                                                                 f"part of a verdict change"})
            for n in new_rows + changed + removed:
                if n not in carried:
                    what = "new" if n in new_rows else ("removed" if n in removed else "changed")
                    not_carried.append({"path": table, "reason": f"row {n} ({what}) is not part of this proposal"})
            if _outside_rows(base_text) != _outside_rows(work_text):
                not_carried.append({"path": table, "reason": "changes outside the table's rows are not proposed"})
        elif kind in ("attempt_new", "verdict_change"):
            raise ProposalRefused("no_attempt_table", f"the attempt table {table} cannot be read in the base or the copy")
    elif kind in ("attempt_new", "verdict_change"):
        raise ProposalRefused("no_attempt_table", "no attempt table is configured (.researchos/attempts.toml)")

    # A variable card's new version file.
    version_changes = sorted(
        rel for rel in set(work) | set(base.files)
        if _VERSION_FILE_RE.match(rel) and (base.files.get(rel) or {}).get("sha256") != (work.get(rel) or {}).get("sha256")
    )
    if kind == "variable_version":
        cards = sorted({_VERSION_FILE_RE.match(r).group(1) for r in version_changes if r in work})
        chosen_card = _pick(cards, card, "variable card")
        rels = [r for r in version_changes if r in work and _VERSION_FILE_RE.match(r).group(1) == chosen_card]
        rel = max(rels, key=lambda r: int(_VERSION_FILE_RE.match(r).group(2)))
        from researchos.records import variables as V  # noqa: PLC0415

        if attested not in V.ATTESTED:
            raise ProposalRefused("attested_required", f"attested must be one of {', '.join(V.ATTESTED)}")
        base_numbers = [int(_VERSION_FILE_RE.match(r).group(2)) for r in base.files
                        if _VERSION_FILE_RE.match(r) and _VERSION_FILE_RE.match(r).group(1) == chosen_card]
        data["variable"] = {
            "card": chosen_card, "version": int(_VERSION_FILE_RE.match(rel).group(2)), "file": rel,
            "sha256": work[rel]["sha256"], "size": work[rel]["size"], "attested": attested,
            "base_version": max(base_numbers, default=0),
        }
        version_changes = [r for r in version_changes if r != rel]
    not_carried += [{"path": r, "reason": "variable versions travel in a variable_version proposal"} for r in version_changes]

    skip = {table} if table is not None else set()
    from researchos import share  # noqa: PLC0415 -- share imports this module

    copies = {rel for rel in share.official_copies(pid) if rel in work and rel not in base.files}
    skip |= copies
    not_carried += [{"path": rel, "reason": "the official version's copy that sync put beside yours"} for rel in sorted(copies)]
    pinned = share.read_state(pid) if share.pinned_pi(pid) is not None else None
    if pinned and Path(str(pinned.get("working_copy", ""))) == root.resolve():
        data["base_seq"] = pinned.get("seq")
    for name in _RECORD_FILES:
        rel = f"{paths.PROJECT_DIRNAME}/{name}"
        mine = fingerprint(root / rel)
        if (mine or {}).get("sha256") == (base.files.get(rel) or {}).get("sha256"):
            continue
        reason = ("notes, link judgments and hand-drawn links travel with every sync (researchos share sync), "
                  "without approval" if name in share.TRAVELLING_FILES
                  else "canvas arrangements do not travel: each person keeps their own" if name == share.CANVAS_NAME
                  else "Research OS's own record files are not part of a proposal")
        not_carried.append({"path": rel, "reason": reason})
    candidates = _file_changes(root, base, work, skip=skip, only=None)
    if only is not None:
        chosen_paths = set(only)
    elif kind == "attempt_new":
        chosen_paths = linked
    elif kind == "files":
        chosen_paths = {c["path"] for c in candidates}
    else:  # a verdict change carries its row only; a variable version its version file
        chosen_paths = set()
    # The member's own choice on top of the kind's: files added, files unticked.
    chosen_paths = (set(chosen_paths) | set(add or ())) - set(leave or ())
    files = [c for c in candidates if c["path"] in chosen_paths]
    if kind == "files" and not files:
        raise ProposalRefused("nothing", "no file differs from the official version" if not candidates
                              else "no file is chosen for this proposal")
    not_carried += [{"path": c["path"], "reason": "not chosen for this proposal"}
                    for c in candidates if c["path"] not in chosen_paths]
    data["file"] = files
    if not_carried:
        data["not_carried"] = not_carried

    draft = Draft(data=data)
    for item in files:
        if item["action"] == "removed":
            continue
        if item["store"] == "text":
            draft.texts[item["path"]] = root / item["path"]
        else:
            draft.blobs[item["sha256"]] = root / item["path"]
    if "variable" in data:
        draft.texts[data["variable"]["file"]] = root / data["variable"]["file"]
    preview["files"] = [_member_file_preview(root, base, item) for item in files]
    returned = _returned_paths(root, pid, me) if candidates else {}
    preview["candidates"] = [
        {**{k: c.get(k) for k in ("path", "action", "size", "store")}, "chosen": c["path"] in chosen_paths,
         "linked": c["path"] in linked, "returned": returned.get(c["path"], [])}
        for c in candidates
    ]
    draft.preview = preview
    return draft


def _member_file_preview(root: Path, base: Base, item: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(item)
    if item.get("store") == "text" or item["action"] == "removed":
        old = base.text(item["path"]) or ""
        new = _text_of(root / item["path"], item.get("size")) if item["action"] != "removed" else ""
        if new is not None and (item["action"] == "added" or base.text(item["path"]) is not None):
            out["diff"], out["truncated"] = history.unified(old, new, f"a/{item['path']}", f"b/{item['path']}")
    return out


# -- proposal.toml ---------------------------------------------------------------------------


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        raise ProposalRefused("invalid", "true/false is not written in a proposal")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):  # a list of scalars, inside a table too (a new attempt's `parents`)
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    return ledger_mod.toml_string(str(value))


def emit(data: Mapping[str, Any], header: str = _PROPOSAL_HEADER) -> bytes:
    """A proposal (or an outcome) as TOML: scalars, then tables, then
    arrays of tables; read back and compared before it is used."""
    lines = [header.rstrip("\n")]
    tables, arrays = [], []
    for key, value in data.items():
        if value is None or (isinstance(value, list) and not value):
            continue
        if isinstance(value, Mapping):
            tables.append((key, value))
        elif isinstance(value, list) and value and all(isinstance(v, Mapping) for v in value):
            arrays.append((key, value))
        elif isinstance(value, list):
            lines.append(f"{ledger_mod.toml_key(key)} = [" + ", ".join(_scalar(v) for v in value) + "]")
        else:
            lines.append(f"{ledger_mod.toml_key(key)} = {_scalar(value)}")
    for key, table in tables:
        lines += ["", f"[{ledger_mod.toml_key(key)}]"]
        lines += [f"{ledger_mod.toml_key(k)} = {_scalar(v)}" for k, v in table.items() if v is not None]
    for key, items in arrays:
        for item in items:
            lines += ["", f"[[{ledger_mod.toml_key(key)}]]"]
            lines += [f"{ledger_mod.toml_key(k)} = {_scalar(v)}" for k, v in item.items() if v is not None]
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    back = tomllib.loads(raw.decode("utf-8"))
    expected = json.loads(json.dumps(_strip_none(data), ensure_ascii=False))
    if back != expected:  # pragma: no cover -- emitter guard
        raise ProposalRefused("invalid", "the proposal would not read back as written; nothing written")
    return raw


def _strip_none(value: Any) -> Any:
    """What `emit` writes: no None, no empty list, at any depth."""
    if isinstance(value, Mapping):
        return {k: _strip_none(v) for k, v in value.items() if v is not None and v != []}
    if isinstance(value, list):
        return [_strip_none(v) for v in value]
    return value


def _clean_rel(rel: Any) -> str:
    if not isinstance(rel, str) or not rel or any(ch in rel for ch in "\x00\r\n"):
        raise ValueError(f"not a project-relative path: {rel!r}")
    norm = posixpath.normpath(rel)
    if norm != rel or posixpath.isabs(rel) or rel.startswith("../") or rel == ".." or "\\" in rel:
        raise ValueError(f"not a clean project-relative path: {rel!r}")
    if any(part.startswith(".") for part in rel.split("/")):
        raise ValueError(f"{rel!r}: hidden files and Research OS's own folder are not carried as files")
    return rel


def _text_field(table: Mapping[str, Any], name: str, *, required: bool = True) -> None:
    value = table.get(name)
    if value is None and not required:
        return
    if not isinstance(value, str):
        raise ValueError(f"{name!r} must be text, got {value!r}")


def validate(data: Mapping[str, Any]) -> None:
    """A proposal as read from the exchange: every field the PI's side relies
    on. Raises ValueError naming the problem."""
    if data.get("format") != FORMAT:
        raise ValueError(f"format {data.get('format')!r} is not one this Research OS reads")
    if not exchange_mod.PROPOSAL_ID_RE.match(str(data.get("id") or "")):
        raise ValueError("'id' is not a proposal id")
    if not people.is_member_id(data.get("by")):
        raise ValueError("'by' must name the member")
    if data.get("kind") not in KINDS:
        raise ValueError(f"'kind' must be one of {', '.join(KINDS)}")
    for name in ("project", "sentence", "base", "at"):
        _text_field(data, name)
    kind = data["kind"]
    if kind == "attempt_new":
        attempt = data.get("attempt")
        if not isinstance(attempt, dict):
            raise ValueError("an attempt_new proposal carries [attempt]")
        for name in ("number", *ROW_FIELDS):
            _text_field(attempt, name)
        if "parents" in attempt and (not isinstance(attempt["parents"], list)
                                     or not all(_ATTEMPT_ID_RE.match(str(p)) for p in attempt["parents"])):
            raise ValueError("[attempt] parents names attempts as attempt:<table>#<number>")
        _text_field(attempt, "reason", required=False)
        if attempt.get("abandons") == "yes" and (not str(attempt.get("reason") or "").strip()
                                                 or not attempt["result"].strip()):
            raise ValueError("an abandonment carries its result and reason")
    if kind == "route_link":
        route = data.get("route")
        if not isinstance(route, dict) or route.get("op") not in ROUTE_OPS:
            raise ValueError("a route_link proposal carries [route] with op add or remove")
        ends = [route.get("child"), route.get("parent")]
        if not all(_ATTEMPT_ID_RE.match(str(e)) for e in ends) or ends[0] == ends[1]:
            raise ValueError("[route] names two attempts as attempt:<table>#<number>")
        _text_field(route, "note", required=False)
        if data.get("file"):
            raise ValueError("a route_link proposal carries no files")
    if kind == "verdict_change":
        verdict = data.get("verdict")
        if not isinstance(verdict, dict):
            raise ValueError("a verdict_change proposal carries [verdict]")
        for name in ("number", "old", "new", "old_result", "result"):
            _text_field(verdict, name)
        _text_field(verdict, "reason", required=False)
        if verdict.get("abandons") == "yes" and (not str(verdict.get("reason") or "").strip() or not verdict["result"].strip()):
            raise ValueError("an abandonment carries its result and reason")
    if kind == "variable_version":
        variable = data.get("variable")
        if not isinstance(variable, dict):
            raise ValueError("a variable_version proposal carries [variable]")
        m = _VERSION_FILE_RE.match(str(variable.get("file") or ""))
        if m is None or m.group(1) != variable.get("card"):
            raise ValueError("[variable] file must be the card's version file")
        if not _SHA_RE.match(str(variable.get("sha256") or "")):
            raise ValueError("[variable] sha256 is not a content hash")
        from researchos.records import variables as V  # noqa: PLC0415

        if variable.get("attested") not in V.ATTESTED:
            raise ValueError("[variable] attested must be yes, no or unknown")
        if not isinstance(variable.get("base_version"), int) or variable["base_version"] < 0:
            raise ValueError("[variable] base_version must be a number")
    files = data.get("file", [])
    if not isinstance(files, list):
        raise ValueError("files are written as [[file]] tables")
    seen = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("files are written as [[file]] tables")
        rel = _clean_rel(item.get("path"))
        if rel in seen:
            raise ValueError(f"{rel} is listed twice")
        seen.add(rel)
        if item.get("action") not in ACTIONS:
            raise ValueError(f"{rel}: action must be one of {', '.join(ACTIONS)}")
        if item["action"] != "removed":
            if not _SHA_RE.match(str(item.get("sha256") or "")) or not isinstance(item.get("size"), int):
                raise ValueError(f"{rel}: a file carries its sha256 and size")
            if item.get("store") not in STORES:
                raise ValueError(f"{rel}: store must be text or blob")
        if item["action"] != "added" and not _SHA_RE.match(str(item.get("base_sha256") or "")):
            raise ValueError(f"{rel}: a changed or removed file names the fingerprint it was changed from")
    if kind == "files" and not files:
        raise ValueError("a files proposal carries at least one file")


def write_proposal(
    working_copy: str | Path,
    draft: Draft,
    *,
    exchange: LocalExchange | None = None,
    expected: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> StoredProposal:
    """Write `draft` to the exchange -- once, `ready.toml` last. The working
    copy's files are checked against the draft first (`changed_since_shown`
    otherwise); `expected` is the dry run's `fingerprint` when one was
    shown."""
    root = Path(working_copy)
    if expected is not None and expected != draft.fingerprint:
        raise ProposalRefused("changed_since_shown", "the proposal differs from the one shown; look at it again")
    pid = draft.data["project"]
    ex = exchange or exchange_mod.for_project(pid, root)
    for rel, source in list(draft.texts.items()):
        expected_sha = (draft.data.get("variable") or {}).get("sha256") if rel == (draft.data.get("variable") or {}).get("file") \
            else next(i["sha256"] for i in draft.data["file"] if i["path"] == rel)
        if exchange_mod.sha256_of(source)[0] != expected_sha:
            raise ProposalRefused("changed_since_shown", f"{rel} changed since it was shown; look again")
    proposal_id = "pr-" + secrets.token_hex(16)
    data = {"format": FORMAT, "id": proposal_id, "at": _now_iso(now), **{k: v for k, v in draft.data.items() if k != "format"}}
    raw = emit(data)
    validate(tomllib.loads(raw.decode("utf-8")))
    signature = _signature(root, draft.data["by"], raw)
    texts = {rel: source for rel, source in draft.texts.items()}
    try:
        return ex.write_proposal(draft.data["by"], proposal_id, raw, texts, draft.blobs, signature=signature)
    except ExchangeError:
        raise
    except OSError as exc:
        raise ExchangeError("unreachable", f"could not write the proposal: {exc}") from exc


def _listed_key(root: Path, member: str) -> str | None:
    team = people.read_team(root)
    return next((m.key for m in team.members if m.id == member), None)


def _signature(root: Path, member: str, raw: bytes) -> bytes | None:
    """`member`'s signature over `raw` when the team lists their key (12.5,
    `researchos.signing`); refused, nothing written, when it cannot be made."""
    try:
        return signing.sign_as(member, _listed_key(root, member), raw)
    except signing.SigningError as exc:
        raise ProposalRefused("cannot_sign", str(exc), message_zh=exc.message_zh) from exc


def _preflight(root: Path, member: str) -> None:
    try:
        signing.preflight(member, _listed_key(root, member))
    except signing.SigningError as exc:
        raise ProposalRefused("cannot_sign", str(exc), message_zh=exc.message_zh) from exc


# -- reading proposals and their state --------------------------------------------------------


def _parsed(stored: StoredProposal) -> dict[str, Any]:
    data = dict(stored.data)
    try:
        validate(data)
    except ValueError as exc:
        raise ProposalRefused("invalid", f"proposal {stored.id}: {exc}") from exc
    return data


def state_of(ex: LocalExchange, stored: StoredProposal, siblings: Iterable[StoredProposal] = ()) -> dict[str, Any]:
    """`state`: pending | approved | returned | superseded | conflict (two
    final answers that disagree), with every outcome in order."""
    outcomes, problems = ex.outcomes(stored.id, member=stored.member, proposal_raw=stored.raw or None)
    finals = {o.act for o in outcomes if o.act in FINAL_ACTS}
    superseded_by = [s.id for s in siblings if s.member == stored.member and s.data.get("supersedes") == stored.id]
    if len(finals) > 1:
        state = "conflict"
    elif finals:
        state = next(iter(finals))
    elif superseded_by:
        state = "superseded"
    else:
        state = "pending"
    return {
        "state": state,
        "superseded_by": superseded_by,
        "outcomes": [dict(o.data) for o in outcomes],
        "problems": [p.payload() for p in problems],
    }


def verdict_words(carried: Mapping[str, Any] | None, labels: Mapping[str, str] | None) -> dict[str, Any] | None:
    """A proposal's [attempt] or [verdict] with every verdict in it also as
    a word (12.11): `verdict_status` for a row's verdict, `old_status` /
    `new_status` for a verdict change."""
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    if not isinstance(carried, Mapping):
        return None
    out = dict(carried)
    for key, target in (("verdict", "verdict_status"), ("old", "old_status"), ("new", "new_status")):
        if isinstance(out.get(key), str):
            out[target] = attempts_ingest.verdict_status(out[key], dict(labels) if labels is not None else None)
    return out


def _summary(stored: StoredProposal, state: Mapping[str, Any], labels: Mapping[str, str] | None = None) -> dict[str, Any]:
    data = stored.data
    files = data.get("file") or []
    return {
        "id": stored.id, "by": stored.member, "at": data.get("at"), "kind": data.get("kind"),
        "sentence": data.get("sentence"), "base": data.get("base"), "attempt": verdict_words(data.get("attempt"), labels),
        "verdict": verdict_words(data.get("verdict"), labels), "variable": data.get("variable"),
        "route": _route_summary(data.get("route")),
        "files": [{k: f.get(k) for k in ("path", "action", "size", "store")} for f in files],
        "not_carried": data.get("not_carried") or [], "supersedes": data.get("supersedes"),
        "base_seq": data.get("base_seq"),
        "verification": stored.check.payload() if getattr(stored, "check", None) is not None else None, **state,
    }


def listing(root: str | Path, *, exchange: LocalExchange | None = None) -> dict[str, Any]:
    """「我的提议」 for a member, the inbox for the PI, nothing for anyone
    else (12.4). Pending first."""
    root = Path(root)
    role, me, _team = role_of(root)
    out: dict[str, Any] = {"role": role, "me": me, "proposals": [], "problems": []}
    if role not in ("pi", "member"):
        return out
    pid = _project_id(root)
    ex = exchange or exchange_mod.for_project(pid, root)
    found = ex.list_proposals(None if role == "pi" else me)
    out["problems"] = [p.payload() for p in found.problems]
    labels = _labels(root)
    for stored in found.proposals:
        if stored.data.get("project") != pid:
            out["problems"].append({"kind": "wrong_project", "path": str(stored.directory),
                                    "detail": f"belongs to project {stored.data.get('project')}"})
            continue
        siblings = [s for s in found.proposals if s.member == stored.member]
        out["proposals"].append(_summary(stored, state_of(ex, stored, siblings), labels))
    owners: dict[str, set[str]] = {}
    for item in out["proposals"]:
        owners.setdefault(item["id"], set()).add(item["by"])
    for item in out["proposals"]:
        if len(owners[item["id"]]) > 1:
            item["duplicate_id"] = sorted(owners[item["id"]])
    for pid_, members in sorted(owners.items()):
        if len(members) > 1:
            out["problems"].append({"kind": "duplicate_id", "path": pid_,
                                    "detail": f"the same proposal id is in the areas of {', '.join(sorted(members))}: one "
                                              f"is a copy; name the member to open it (--from)"})
    order = {"pending": 0, "conflict": 1, "approved": 2, "returned": 3, "superseded": 4}
    out["proposals"].sort(key=lambda p: (order.get(p["state"], 9), str(p.get("at") or "")))
    return out


def open_for(root: str | Path, proposal_id: str, *, exchange: LocalExchange | None = None,
             member: str | None = None, lang: str = "zh") -> dict[str, Any]:
    """One proposal as its reader may see it: the PI gets `changes`
    (`what_changes`, its sentences in `lang`), its author the proposal and
    its outcomes; anyone else is refused. A member opens only their own
    area; the PI names `member` when the id is in more than one area."""
    root = Path(root)
    role, me, _team = role_of(root)
    if role not in ("pi", "member"):
        raise ProposalRefused("not_in_team" if role == "none" else ("single_person" if role == "single" else "team_unreadable"),
                              "only the PI and the proposal's author see a proposal")
    pid = _project_id(root)
    ex = exchange or exchange_mod.for_project(pid, root)
    if role == "member":
        try:
            stored = ex.open_proposal(proposal_id, member=me)
        except ExchangeError as exc:
            if exc.code != "no_such_proposal":
                raise
            ex.open_proposal(proposal_id)  # raises when it is nobody's
            raise ProposalRefused("not_yours", f"proposal {proposal_id} is not yours") from exc
    else:
        stored = ex.open_proposal(proposal_id, member=member)
    siblings = ex.list_proposals(stored.member).proposals
    out = _summary(stored, state_of(ex, stored, siblings), _labels(root))
    if role == "pi":
        out["changes"] = what_changes(stored, root, lang)
    return out


def _labels(root: Path) -> dict[str, str]:
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    return attempts_ingest.verdict_labels(root)


# -- what approving would do (the PI) ---------------------------------------------------------


def _official_fp(root: Path, rel: str) -> dict[str, Any] | None:
    return fingerprint(root / rel)


def _confined(root: Path, rel: str) -> Path:
    target = root / rel
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ProposalRefused("invalid", f"{rel} resolves outside the project") from exc
    return target


#: Why a part of a proposal cannot simply be applied, by a code that never changes (the page
#: compares codes, never words: 13.6), in Chinese and English.
REASONS = {
    "placed": ("这次批准已经放入了这个文件", "This approval already placed this file"),
    "dataless": ("正式版本里的这个文件还在云端，无法比较", "The official copy of this file is still in the cloud, so it cannot be compared"),
    "both_changed": ("这个文件在提议之后，正式版本里也改了", "The official version also changed this file after the proposal"),
    "gone": ("这个文件在正式版本里已经不在了", "The official version no longer has this file"),
    "content_missing": ("提议里缺少这个文件的内容", "The proposal is missing this file's content"),
    "no_table": ("正式版本没有配置尝试表", "The official version has no attempt table configured"),
    "table_unreadable": ("尝试表无法读取：{error}", "The attempt table cannot be read: {error}"),
    "row_taken": ("正式版本的尝试表里已经有第 {n} 行", "The official attempt table already has row {n}"),
    "row_missing": ("正式版本的尝试表里没有第 {n} 行", "The official attempt table has no row {n}"),
    "verdict_moved": ("第 {n} 行的结论或结果在正式版本里也改了", "Row {n}'s verdict or result also changed in the official version"),
    "row_moved": ("第 {n} 行在提议之后，正式版本里也改了", "Row {n} also changed in the official version after the proposal"),
    "reason_required": (MESSAGES["reason_required"], "Abandoning a route needs both the result and the reason"),
    "version_missing": ("提议里缺少这个版本文件", "The proposal is missing this version file"),
    "version_mismatch": ("版本文件与提议记录的指纹不符", "The version file does not match the fingerprint the proposal recorded"),
    "version_unreadable": ("版本文件无法读取：{error}", "The version file cannot be read: {error}"),
    "version_incomplete": ("这一版还不完整，缺少：{missing}", "This version is not complete yet; it lacks: {missing}"),
    "card_gone": ("这个变量卡在正式版本里已经不在了", "The official version no longer has this variable card"),
    "card_draft": ("正式版本里这个变量卡有未确认的草稿 v{n}", "The official version has an unconfirmed draft v{n} of this variable card"),
    "card_moved": ("这个变量卡在正式版本里已经有 v{n}（提议基于 v{base}）",
                   "The official version already has v{n} of this variable card (the proposal is based on v{base})"),
    "wrong_project": (MESSAGES["wrong_project"], "This proposal belongs to another project"),
    "not_member": ("{who}: 不是这个项目的成员，提议不能批准", "{who}: not a member of this project, so the proposal cannot be approved"),
    "id_in_approval": ("这个编号的另一份提议（{who}）已经在批准中", "Another proposal with this id ({who}) is being approved"),
}


def _reason(code: str, lang: str = "zh", **values: Any) -> dict[str, str]:
    """`reason` (the sentence, in `lang`) and `reason_code` for a part."""
    from researchos import settings  # noqa: PLC0415

    return {"reason": settings.say(*REASONS[code], lang, **values), "reason_code": code}


def _file_change(root: Path, stored: StoredProposal, item: Mapping[str, Any],
                 done: Mapping[str, Any] | None = None, lang: str = "zh") -> dict[str, Any]:
    rel = item["path"]
    out: dict[str, Any] = {k: item.get(k) for k in ("path", "action", "size", "sha256", "base_sha256", "base_size", "store")}
    _confined(root, rel)
    if rel in (done or {}).get("placed", ()):
        # This approval placed it before it was interrupted; whatever the
        # file is now, it is not the proposal's to place again.
        out.update(state="done", **_reason("placed", lang))
        return out
    official = _official_fp(root, rel)
    out["official_sha256"] = official.get("sha256") if official else None
    out["official_size"] = official.get("size") if official else None
    if official is not None and official.get("dataless"):
        out.update(state="problem", **_reason("dataless", lang))
        return out
    proposed = item.get("sha256")
    current = official.get("sha256") if official else None
    if item["action"] == "added":
        state = "apply" if official is None else ("same" if current == proposed else "conflict")
    elif item["action"] == "changed":
        state = "apply" if current == item.get("base_sha256") else ("same" if current == proposed else "conflict")
    else:
        state = "apply" if current == item.get("base_sha256") else ("same" if official is None else "conflict")
    out["state"] = state
    if state == "conflict":
        out.update(_reason("both_changed" if official is not None else "gone", lang))
    if item["action"] != "removed":
        try:
            content = stored.content_path(item)
        except ExchangeError as exc:
            out.update(state="problem", reason=str(exc))
            return out
        try:
            size = content.stat().st_size
        except OSError:
            size = None
        if size != item.get("size"):
            out.update(state="problem", **_reason("content_missing", lang))
            return out
        out["content"] = str(content)
    if state in ("apply", "conflict"):
        _describe(root, rel, item, out, lang)
    return out


SMALL_TABLE_BYTES = 64 * 1024  # 12.13: a table this small also shows its line diff, below its summary


def _describe(root: Path, rel: str, item: Mapping[str, Any], out: dict[str, Any], lang: str = "zh") -> None:
    """A line diff for text; for data, size, fingerprint and -- for tables
    -- rows and columns (12.3). Every sentence also in the screen's words
    (`*_words`, 12.13: never an arrow, in `lang`, 13.6); the command line
    keeps its own. A table (CSV, TSV) leads with its rows and columns; its
    line diff is kept only when it is small (`table_small`)."""
    old_path = root / rel
    old_text = _text_of(old_path, out.get("official_size")) if out.get("official_sha256") else ""
    if item["action"] == "removed":
        new_text: str | None = ""
    elif item.get("store") == "text":
        new_text = _text_of(Path(out["content"]), item.get("size"))
    else:
        new_text = None
    if old_text is not None and new_text is not None:
        out["diff"], out["truncated"] = history.unified(old_text, new_text, f"a/{rel}", f"b/{rel}")
        # 12.3: a table is data too -- its rows and columns, beside the diff.
        try:
            before = history.data_summary(old_path, rel) if out.get("official_sha256") else None
            after = history.data_summary(Path(out["content"]), rel) if item["action"] != "removed" else None
        except OSError:
            before = after = None
        if before or after:
            out["table"] = (history.table_sentence(before, after) if before and after
                            else history.table_shape(after or before))
            out["table_words"] = (history.table_sentence(before, after, words=True, lang=lang) if before and after
                                  else history.table_shape(after or before, lang))
            out["table_small"] = max(int(out.get("official_size") or 0), int(item.get("size") or 0)) <= SMALL_TABLE_BYTES
        return
    try:
        a = history.data_summary(old_path, rel) if out.get("official_sha256") else None
        b = history.data_summary(Path(out["content"]), rel) if item["action"] != "removed" else None
    except OSError:
        a = b = None
    out["summary"] = history.table_sentence(a, b) if (a or b) else None
    out["summary_words"] = history.table_sentence(a, b, words=True, lang=lang) if (a or b) else None
    if b and not a:
        out["table"], out["table_words"] = history.table_shape(b), history.table_shape(b, lang)
    official_size = out.get("official_size") if out.get("official_size") is not None else "—"
    size = item.get("size") if item.get("size") is not None else "—"
    sizes = f"{official_size} → {size} 字节"
    out["sentence"] = (out["summary"] + "；" if out.get("summary") else "") + f"大小 {sizes}"
    from researchos import settings  # noqa: PLC0415

    said = {"a": history.number_text(official_size, lang), "b": history.number_text(size, lang)}
    moved = (settings.say("大小从 {a} 变为 {b} 字节", "Size changed from {a} to {b} bytes", lang, **said)
             if official_size != size else settings.say("大小不变（{b} 字节）", "Size unchanged ({b} bytes)", lang, **said))
    out["sentence_words"] = (out["summary_words"] + settings.say("；", "; ", lang) if out.get("summary_words") else "") + moved


def abandon_result(result: str, reason: str | None, verdict: str) -> str:
    """The result cell an approved abandonment writes (12.4: approving
    records the result and the reason in the attempt table): the result,
    and 「（放弃理由：…）」 unless the row already says the reason."""
    said = " ".join(str(reason or "").split())
    if not said or said in result or said in verdict:
        return result
    return f"{result}（放弃理由：{said}）" if result.strip() else f"放弃理由：{said}"


_REASON_SUFFIX = re.compile(r"^（放弃理由：.*）$", re.S)


def _as_cells(fields: Mapping[str, str]) -> dict[str, str]:
    """A row's cells as read (`_clean_cell`: a `<br>` the researcher wrote is
    a line feed) back into the table's own spelling, so an approval writes
    a cell over two lines as `<br>` -- a line feed would split the row."""
    return {k: v.replace("\n", "<br>") if isinstance(v, str) else v for k, v in fields.items()}


def row_as_approved(mine: Mapping[str, str], official: Mapping[str, str] | None) -> bool:
    """`official` is the member's row `mine` as an approval wrote it: the
    same cells, the result perhaps followed by the reason `abandon_result`
    adds."""
    if official is None or any(mine.get(k) != official.get(k) for k in ROW_FIELDS if k != "result"):
        return False
    a, b = mine.get("result") or "", official.get("result") or ""
    if a == b:
        return True
    if not a.strip():
        return b.startswith("放弃理由：")
    return b.startswith(a) and bool(_REASON_SUFFIX.match(b[len(a):]))


def _table_at(root: Path, config, sha: Any) -> str | None:
    """The attempt table's text as it was when its SHA-256 was `sha` -- from
    this machine's history store (12.6), else from a published snapshot
    that carried it (verified by that hash). None when neither has it."""
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        return None
    try:
        pid = _project_id(root)
    except ProposalRefused:
        return None
    data = None
    try:
        store = history.Store.existing(pid)
        if store is not None:
            try:
                data = store.read_blob(sha)
            finally:
                store.close()
    except Exception:  # noqa: BLE001 -- a store that cannot be read is no answer, not a failure
        data = None
    if data is None or hashlib.sha256(data).hexdigest() != sha:
        data = None
        from researchos import share  # noqa: PLC0415

        shared = exchange_mod.folders_for(pid)
        try:
            snaps, _problems = share.read_snapshots(shared) if shared is not None else ([], [])
            for snap in reversed(snaps):
                entry = snap.files.get(config.file)
                if entry is not None and entry.get("sha256") == sha:
                    data = share._read_content(shared, entry)
                    break
        except (share.ShareError, OSError):
            data = None
    try:
        return data.decode("utf-8") if data is not None else None
    except UnicodeDecodeError:
        return None


def _route_change(root: Path, data: Mapping[str, Any], done: Mapping[str, Any], lang: str) -> dict[str, Any] | None:
    """What approving a `route_link` would do to the official mappings.toml
    (14.2), in words: `apply`, `same` (the file already says so), `done`
    (this approval wrote it before it was interrupted), or `conflict` with
    a `code` -- an end no longer in the official table (`attempt_not_found`)
    or a line that would now close a circle (`circle`)."""
    from researchos import settings  # noqa: PLC0415
    from researchos.ingest import mappings as mappings_ingest  # noqa: PLC0415

    route = data.get("route")
    if data["kind"] != "route_link" or not isinstance(route, Mapping):
        return None
    out: dict[str, Any] = {**_route_summary(route), "file": mappings_ingest.MAPPINGS_RELATIVE_PATH}
    numbers = {"child": out["child_number"], "parent": out["parent_number"]}
    out["words"] = (settings.say("批准后加上连线：#{child} 源自 #{parent}", "Approving adds the line: #{child} grew out "
                                 "of #{parent}", lang, **numbers) if route["op"] == "add"
                    else settings.say("批准后去掉连线：#{child} 源自 #{parent}", "Approving removes the line: #{child} "
                                      "grew out of #{parent}", lang, **numbers))
    if done.get("route_applied"):
        out["state"] = "done"
        return out
    c, p = route["child"].removeprefix("attempt:"), route["parent"].removeprefix("attempt:")
    lines = _lines_in(root, _mappings_text(root))
    present = p in lines.get(c, ())
    if route["op"] == "remove" or present:
        out["state"] = "apply" if present == (route["op"] == "remove") else "same"
        return out
    config = _attempts_config(root)
    rows = _table_rows(root, config) if config is not None else {}
    if any(end.rpartition("#")[0] != getattr(config, "file", None) or end.rpartition("#")[2] not in rows
           for end in (c, p)):
        out.update(state="conflict", code="attempt_not_found", reason=settings.say(
            "连线的一端在正式版本的尝试表里找不到了", "One end of the line is no longer in the official attempt table", lang))
    elif mappings_ingest._reaches(lines, p, c):
        out.update(state="conflict", code="circle", reason=settings.say(
            "这条连线会绕成一个圈：它要连的来处本身就源自它",
            "This line would close a circle: the attempt it would grow out of already grows out of it", lang))
    else:
        out["state"] = "apply"
    return out


def _parents_conflict(root: Path, config, rows: Mapping[str, Any], number: str, parents: list[str],
                      lang: str) -> str | None:
    """Why the lines a new attempt names (14.2) cannot be added now, if they cannot."""
    from researchos import settings  # noqa: PLC0415
    from researchos.ingest import mappings as mappings_ingest  # noqa: PLC0415

    table = f"attempt:{config.file}#"
    gone = [p.rpartition("#")[2] for p in parents if not p.startswith(table) or p[len(table):] not in rows]
    if gone:
        return settings.say("它源自的第 {n} 行在正式版本的尝试表里不在了",
                            "Row {n}, which it grew out of, is no longer in the official attempt table",
                            lang, n="、".join(gone) if lang != "en" else ", ".join(gone))
    lines = _lines_in(root, _mappings_text(root))
    if any(mappings_ingest._reaches(lines, p.removeprefix("attempt:"), f"{config.file}#{number}") for p in parents):
        return settings.say("它源自的连线会绕成一个圈", "The lines it grew out of would close a circle", lang)
    return None


def _attempt_change(root: Path, data: Mapping[str, Any], done: Mapping[str, Any] | None = None,
                    lang: str = "zh") -> dict[str, Any] | None:
    from researchos.webapp import mapedit  # noqa: PLC0415

    config = _attempts_config(root)
    kind = data["kind"]
    if kind not in ("attempt_new", "verdict_change"):
        return None
    done = done or {}
    if config is None:
        return {"state": "problem", **_reason("no_table", lang)}
    try:
        raw = (root / config.file).read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"state": "problem", **_reason("table_unreadable", lang, error=exc)}
    rows = _rows(text, config) or {}
    node = f"attempt:{config.file}#"
    carried = data["attempt"] if kind == "attempt_new" else data["verdict"]
    table_base = carried.get("table_base_sha256")
    # 12.12: the attempt table conflicts by ROW, not as a file. Whether the
    # official table moved at all is said (`table_moved`), but only the
    # proposal's own row -- or the number it takes -- can block it.
    table_moved = isinstance(table_base, str) and hashlib.sha256(raw).hexdigest() != table_base
    if kind == "attempt_new":
        row = data["attempt"]
        number = row["number"]
        abandons = row.get("abandons") == "yes" or is_abandonment(row["verdict"], abandon_markers(root))
        fields = {k: row[k] for k in ROW_FIELDS}
        if abandons:
            fields["result"] = abandon_result(row["result"], row.get("reason"), row["verdict"])
        out: dict[str, Any] = {"op": "append", "number": number, "node": node + number, "file": config.file,
                               "fields": fields, "abandons": abandons, "result": row["result"],
                               "reason": row.get("reason")}
        if abandons and (not str(row.get("reason") or "").strip() or not row["result"].strip()):
            # Proposed before 14.3 asked for them: returned and proposed again, as an old verdict change is.
            out.update(state="problem", **_reason("reason_required", lang))
            return out
        if done.get("attempt_applied"):
            out["state"] = "done"
            return out
        out["table_moved"] = table_moved
        if row.get("parents"):
            out["parents"] = list(row["parents"])
            out["parent_words"] = parent_words(row, lang)  # 12.3: the lines approving adds, said, never left unseen
            stale = _parents_conflict(root, config, rows, number, out["parents"], lang)
            if stale:
                out.update(state="conflict", reason=stale)
                return out
        if number in rows:
            # The number it takes is taken: by the PI, or by another
            # member's proposal approved first.
            if rows[number] == out["fields"]:
                out["state"] = "same"
            else:
                out.update(state="conflict", **_reason("row_taken", lang, n=number))
            return out
    else:
        v = data["verdict"]
        number = v["number"]
        abandons = v.get("abandons") == "yes" or is_abandonment(v["new"], abandon_markers(root))
        written = abandon_result(v["result"], v.get("reason"), v["new"]) if abandons else v["result"]
        fields = {"verdict": v["new"]}
        if written != v["old_result"]:
            fields["result"] = written
        out = {"op": "update", "number": number, "node": node + number, "file": config.file, "fields": fields,
               "old": v["old"], "new": v["new"], "abandons": abandons,
               "result": v["result"], "reason": v.get("reason")}
        if abandons and (not str(v.get("reason") or "").strip() or not v["result"].strip()):
            out.update(state="problem", **_reason("reason_required", lang))
            return out
        if done.get("attempt_applied"):
            out["state"] = "done"
            return out
        out["table_moved"] = table_moved
        current = rows.get(number)
        if current is None:
            out.update(state="conflict", **_reason("row_missing", lang, n=number))
            return out
        if current["verdict"] == v["new"] and current["result"] == written:
            out["state"] = "same"
            return out
        if current["verdict"] != v["old"] or current["result"] != v["old_result"]:
            out.update(state="conflict", **_reason("verdict_moved", lang, n=number))
            return out
        # The row's other cells, against the row as the proposal's base had
        # it (the base table found by its fingerprint): a row the PI edited
        # since is a conflict; the PI editing other rows is not.
        base_row = (_rows(_table_at(root, config, table_base), config) or {}).get(number) if table_moved else None
        if base_row is not None:
            moved_cells = [k for k in ROW_FIELDS if k not in ("verdict", "result") and current[k] != base_row[k]]
            if moved_cells:
                out.update(state="conflict", **_reason("row_moved", lang, n=number))
                return out
        elif table_moved:
            out["base_row_unknown"] = True  # only the verdict and result could be compared
    try:
        shown = mapedit.preview_edit(root, out["op"], number, _as_cells(out["fields"]))
    except mapedit.MapEditError as exc:
        out.update(state="problem", reason=str(exc), code=exc.code)
        return out
    except Exception as exc:  # noqa: BLE001 -- AttemptsConfigError and friends: shown, never applied
        out.update(state="problem", reason=str(exc))
        return out
    out.update(state="apply", diff=shown["diff"], old_row=shown["old_row"], new_row=shown["new_row"])
    return out


def _variable_change(root: Path, stored: StoredProposal, data: Mapping[str, Any],
                     done: Mapping[str, Any] | None = None, lang: str = "zh") -> dict[str, Any] | None:
    from researchos.records import variables as V  # noqa: PLC0415

    variable = data.get("variable")
    if variable is None:
        return None
    card_id = variable["card"]
    out: dict[str, Any] = {"card": card_id, "attested": variable["attested"], "proposed_file": variable["file"]}
    try:
        raw = stored.content_path({"path": variable["file"], "store": "text"}).read_bytes()
    except (OSError, ExchangeError):
        return {**out, "state": "problem", **_reason("version_missing", lang)}
    if hashlib.sha256(raw).hexdigest() != variable["sha256"]:
        return {**out, "state": "problem", **_reason("version_mismatch", lang)}
    try:
        parsed = V.parse_version(raw.decode("utf-8"))
    except (V.VersionInvalid, UnicodeDecodeError) as exc:
        return {**out, "state": "problem", **_reason("version_unreadable", lang, error=exc)}
    missing = V.missing_for_confirm(parsed)
    out["content"] = V.content_hash(parsed)
    out["text"] = raw.decode("utf-8")
    if missing:
        return {**out, "state": "problem", **_reason("version_incomplete", lang, missing=("、" if lang != "en" else ", ").join(missing))}
    problem = V.id_problem(card_id)
    if problem:
        return {**out, "state": "problem", "reason": problem}
    found = V.find_card_dirs(root, card_id)
    if len(found) > 1:
        return {**out, "state": "problem", "reason": V.MESSAGES["case_duplicate"]}
    if not found:
        out.update(number=1, new_card=True)
        if variable["base_version"] != 0:
            out.update(state="conflict", **_reason("card_gone", lang))
        else:
            out["state"] = "apply"
        return out
    card = V.read_card(root, found[0])
    for e in card.entries:
        if e.get("act") == "confirmed" and e.get("proposal") == stored.id:
            out.update(state="same", number=e.get("version"))
            return out
    if not card.readable:
        return {**out, "state": "problem", "reason": card.message or "变量卡当前无法读取"}
    highest = max(card.versions, default=0)
    out["number"] = card.next_number
    ours = (done or {}).get("version")
    if (card.draft is not None and ours and ours.get("card") == card_id and ours.get("number") == card.draft
            and _draft_is(found[0], card.draft, variable["sha256"])):
        # This approval wrote that draft before it was interrupted: it is
        # confirmed now, not written again and not taken for a conflict.
        out.update(state="apply", number=card.draft, written=True)
    elif card.draft is not None:
        out.update(state="conflict", **_reason("card_draft", lang, n=card.draft))
    elif highest != variable["base_version"]:
        out.update(state="conflict", **_reason("card_moved", lang, n=highest, base=variable["base_version"]))
    else:
        out["state"] = "apply"
        if card.in_use is not None:
            view = card.versions.get(card.in_use)
            old = view.data.decode("utf-8", errors="replace") if view is not None and view.data is not None else ""
            out["diff"], out["truncated"] = history.unified(old, out["text"], f"v{card.in_use}", f"v{card.next_number}")
    return out


def _draft_is(directory: Path, number: int, sha: str) -> bool:
    try:
        return hashlib.sha256((directory / f"v{number}.toml").read_bytes()).hexdigest() == sha
    except OSError:
        return False


def what_changes(stored: StoredProposal, official_root: str | Path, lang: str = "zh") -> dict[str, Any]:
    """Exactly what approving `stored` would do to the official version
    (module docstring). Reads only. A line's words in `lang` (13.6)."""
    root = Path(official_root)
    data = _parsed(stored)
    problems: list[str] = []
    if data["project"] != _project_id(root):
        problems.append(_reason("wrong_project", lang)["reason"])
    team = people.read_team(root)
    author = next((m for m in team.members if m.id == stored.member), None)
    if author is None or author.role != "member":
        problems.append(_reason("not_member", lang, who=stored.member)["reason"])
    current = snapshot_id(manifest(root))
    out: dict[str, Any] = {
        "id": stored.id, "kind": data["kind"], "by": stored.member, "sentence": data["sentence"], "base": data["base"],
        "official_now": current, "moved": current != data["base"],
    }
    done = read_journal(root, stored.id)
    if done["member"] is not None and done["member"] != stored.member:
        problems.append(_reason("id_in_approval", lang, who=done["member"])["reason"])
    out["resuming"] = done["started"]
    out["attempt"] = _attempt_change(root, data, done, lang)
    out["route"] = _route_change(root, data, done, lang)
    if out["attempt"] is not None:
        labels = _labels(root)
        out["attempt"] = verdict_words(out["attempt"], labels)
        if isinstance((out["attempt"].get("fields") or {}).get("verdict"), str):
            # Beside `fields`, never in it: `fields` is what approving writes.
            out["attempt"]["verdict_status"] = verdict_words(out["attempt"]["fields"], labels)["verdict_status"]
    out["variable"] = _variable_change(root, stored, data, done, lang)
    out["files"] = [_file_change(root, stored, item, done, lang) for item in data.get("file") or []]
    out["not_carried"] = data.get("not_carried") or []
    conflicts = []
    parts = [p for p in (out["attempt"], out["variable"], out["route"]) if p] + out["files"]
    for part in parts:
        if part.get("state") == "conflict":
            conflicts.append({"path": part.get("path") or part.get("file") or part.get("card"), "reason": part.get("reason"),
                              **({"code": part["code"]} if part.get("code") else {})})
        elif part.get("state") == "problem":
            problems.append(f"{part.get('path') or part.get('file') or part.get('card')}: {part.get('reason')}")
    check = getattr(stored, "check", None)
    out["verification"] = check.payload() if check is not None else None
    if check is not None and not check.trusted:
        # 12.5: unsigned or wrongly signed by a member whose key is listed --
        # shown as unverifiable, never approved.
        problems.append(f"签名：{check.message_zh}")
    out["conflicts"] = conflicts
    out["problems"] = problems
    out["approvable"] = not conflicts and not problems
    return out


# -- applying (the PI) ------------------------------------------------------------------------------


def _backup_path(root: Path, proposal_id: str, rel: str) -> Path:
    return paths.project_dir(root) / record_files.BACKUPS_DIRNAME / APPROVED_BACKUPS / proposal_id / rel


def _keep_old(root: Path, target: Path, backup: Path, *, move: bool) -> None:
    record_files.ensure_dir_within(root, backup.parent)
    if backup.exists():
        return
    if move:
        os.rename(target, backup)
        return
    try:
        os.link(target, backup)
    except OSError:
        shutil.copy2(target, backup)


JOURNAL_SUFFIX = ".journal"


def journal_path(root: Path, proposal_id: str) -> Path:
    return paths.project_dir(root) / record_files.BACKUPS_DIRNAME / APPROVED_BACKUPS / f"{proposal_id}{JOURNAL_SUFFIX}"


def read_journal(root: Path, proposal_id: str) -> dict[str, Any]:
    """What an earlier, interrupted approval of `proposal_id` did (module
    docstring). A torn last line is ignored."""
    done: dict[str, Any] = {"started": False, "member": None, "placed": set(), "attempt_started": False,
                            "attempt_applied": False, "version": None, "deciding": False, "route_applied": False}
    try:
        lines = journal_path(root, proposal_id).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return done
    except (OSError, UnicodeDecodeError):
        lines = ['{"step": "unreadable"}']
    for line in lines:
        try:
            step = json.loads(line)
        except ValueError:
            continue
        if not isinstance(step, dict):
            continue
        done["started"] = True
        done["member"] = done["member"] or step.get("member")
        kind = step.get("step")
        if kind == "placed" and isinstance(step.get("path"), str):
            done["placed"].add(step["path"])
        elif kind == "attempt":
            done["attempt_started"] = True
        elif kind == "attempt_applied":
            done["attempt_started"] = done["attempt_applied"] = True
        elif kind == "version":
            done["version"] = {"card": step.get("card"), "number": step.get("number")}
        elif kind == "deciding":
            done["deciding"] = True
        elif kind == "route_applied":
            done["route_applied"] = True
    return done


def _mark(root: Path, stored: StoredProposal, step: str, **fields: Any) -> None:
    """One journal line, appended and fsynced before the step it names
    (and, for a file or the table, again after it)."""
    path = journal_path(root, stored.id)
    record_files.ensure_dir_within(root, path.parent)
    line = json.dumps({"step": step, "member": stored.member, "at": _now_iso(), **fields}, ensure_ascii=False) + "\n"
    created = not path.exists()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    if created:
        record_files._fsync_dir(path.parent)


def _stage(root: Path, changes: Mapping[str, Any]) -> list[tuple[dict[str, Any], Path | None]]:
    """Every file to place, copied beside its target and verified by hash
    -- before anything is applied (module docstring). Refused, with every
    copy removed, when one does not match or cannot be placed."""
    staged: list[tuple[dict[str, Any], Path | None]] = []
    try:
        for change in changes["files"]:
            if change["state"] != "apply":
                continue
            rel = change["path"]
            target = _confined(root, rel)
            if change["action"] == "removed":
                staged.append((change, None))
                continue
            try:
                record_files.ensure_dir_within(root, target.parent)
                if target.is_dir() or target.is_symlink():
                    raise record_files.RecordFileError(f"{target} is a folder or a link")
            except (OSError, record_files.RecordFileError) as exc:
                raise ProposalRefused("not_approvable", f"{rel} cannot be placed in the official version ({exc}); "
                                      f"nothing applied", message_zh="提议里有文件无法放进正式版本，没有应用任何改动") from exc
            tmp = record_files.temp_path_for(target)
            staged.append((change, tmp))
            got = exchange_mod.copy_file(Path(change["content"]), tmp)
            if got != change["sha256"]:
                raise ProposalRefused("not_approvable", f"{rel}: the proposal's content does not match its fingerprint; "
                                      f"nothing applied", message_zh="提议里的文件内容与记录的指纹不符，没有应用任何改动")
    except BaseException:
        _unstage(staged)
        raise
    return staged


def _unstage(staged: Iterable[tuple[Mapping[str, Any], Path | None]]) -> None:
    for _change, tmp in staged:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def _place_file(root: Path, stored: StoredProposal, change: Mapping[str, Any], tmp: Path | None) -> None:
    """One staged file into place (journalled before and after)."""
    rel = change["path"]
    target = _confined(root, rel)
    backup = _backup_path(root, stored.id, rel)
    now = fingerprint(target)
    if change["action"] == "removed":
        if now is not None:
            if now.get("sha256") != change["base_sha256"]:
                raise ProposalRefused("not_approvable", f"{rel} changed in the official version while approving; approve "
                                      f"again once it is settled")
            _mark(root, stored, "placing", path=rel)
            _keep_old(root, target, backup, move=True)
        _mark(root, stored, "placed", path=rel)
        return
    assert tmp is not None
    if now is not None:
        if now.get("sha256") == change["sha256"]:
            tmp.unlink()
            _mark(root, stored, "placed", path=rel)
            return
        expected = change.get("base_sha256") if change["action"] == "changed" else None
        if now.get("sha256") != expected:
            raise ProposalRefused("not_approvable", f"{rel} changed in the official version while approving; approve again "
                                  f"once it is settled")
    _mark(root, stored, "placing", path=rel, sha256=change["sha256"])
    if now is not None:
        _keep_old(root, target, backup, move=False)
    os.replace(tmp, target)
    record_files._fsync_dir(target.parent)
    _mark(root, stored, "placed", path=rel)


def _attribute_file(store: history.Store, root: Path, rel: str, before: int, *, who: str, approved_by: str,
                    note: str | None) -> None:
    """The version of `rel` an approval wrote through another write path
    (the attempt table, through the map editor and its re-scan) carries the
    member and the PI like every other approved file (12.3)."""
    from researchos.records import notes as notes_mod  # noqa: PLC0415

    node = notes_mod.normalize_node(rel)
    latest = store.latest(node)
    try:
        sha = exchange_mod.sha256_of(root / rel)[0]
    except OSError:
        return
    if latest is not None and latest.seq > before and latest.sha256 == sha and latest.source == "scan":
        store.attribute(latest.seq, source="approval", who=who, note=note, approved_by=approved_by)
    else:
        history.record_file(store, root, rel, source="approval", who=who, note=note, approved_by=approved_by)


def _attribute_attempt(store: history.Store, conn, node: str, before: int, *, who: str, approved_by: str,
                       note: str | None) -> None:
    attempt = db.get_node(conn, node)
    if attempt is None:
        return
    hf = attempt.get("human_fields") or {}
    fields = {"verdict": str(hf.get("verdict") or ""), "result": str(hf.get("result") or "")}
    digest = history._attempt_digest(fields)
    latest = store.latest(node)
    if latest is not None and latest.seq > before and latest.sha256 == digest and latest.source == "scan":
        store.attribute(latest.seq, source="approval", who=who, note=note, approved_by=approved_by)
    else:
        store.add(node, sha256=digest, size=None, kind="attempt", source="approval", who=who, note=note,
                  summary=fields, approved_by=approved_by)


def _max_seq(store: history.Store) -> int:
    row = store.conn.execute("SELECT MAX(seq) FROM versions").fetchone()
    return int(row[0] or 0)


def _write_version_file(root: Path, change: Mapping[str, Any], stored: StoredProposal) -> Path:
    from researchos.records import variables as V  # noqa: PLC0415

    card_id = change["card"]
    if change.get("new_card"):
        record_files.ensure_dir_within(root, V.variables_dir(root))
        directory = V.variables_dir(root) / card_id
        directory.mkdir()
    else:
        directory = V.find_card_dirs(root, card_id)[0]
    target = directory / f"v{change['number']}.toml"
    raw = stored.content_path({"path": stored.data["variable"]["file"], "store": "text"}).read_bytes()
    if hashlib.sha256(raw).hexdigest() != stored.data["variable"]["sha256"]:
        raise ProposalRefused("not_approvable", f"{target.name}: the proposal's content does not match its fingerprint")
    _mark(root, stored, "version", card=card_id, number=change["number"])
    record_files.write_new_file(target, raw)  # exclusive: never over an existing file
    if target.read_bytes() != raw:  # pragma: no cover -- disk guard
        raise ProposalRefused("not_approvable", f"{target} does not read back as written")
    return target


def _evidence(conn, root: Path, attempt_node: str, store: history.Store, *, who: str, approved_by: str,
              note: str | None) -> list[dict[str, Any]]:
    """The attempt's evidence at this moment (12.4): its step scripts and
    the files tied to it by `belongs_to`, and the outputs those scripts
    write -- each by fingerprint, text also by content in the history store."""
    from researchos.records import notes as notes_mod  # noqa: PLC0415

    attempt = db.get_node(conn, attempt_node)
    config = _attempts_config(root)
    members: list[tuple[str, str]] = []
    if attempt is not None:
        steps_dir = (config.steps_dir if config is not None else None) or ""
        for name in attempt["attrs"].get("step_files") or []:
            members.append((f"{steps_dir.rstrip('/')}/{name}" if steps_dir else name, "script"))
        for edge in db.query_edges(conn, dst=attempt_node, type="belongs_to"):
            if edge["status"] == "rejected":
                continue
            rel = notes_mod.node_path(edge["src"])
            if rel:
                members.append((rel, "script" if edge["src"].startswith("script:") else "file"))
    outputs: list[tuple[str, str]] = []
    for rel, role in members:
        if role != "script":
            continue
        for edge_type in ("writes", "generates"):
            for edge in db.query_edges(conn, src=notes_mod.normalize_node(rel), type=edge_type):
                out_rel = notes_mod.node_path(edge["dst"]) if edge["status"] != "rejected" else None
                if out_rel:
                    outputs.append((out_rel, "output"))
    seen: set[str] = set()
    evidence = []
    for rel, role in members + outputs:
        if rel in seen:
            continue
        seen.add(rel)
        item: dict[str, Any] = {"path": rel, "role": role}
        full = root / rel
        if not full.exists():
            item["state"] = "missing"
        elif paths.is_dataless(full):
            item["state"] = "dataless"
        else:
            try:
                sha, size = exchange_mod.sha256_of(full)
            except OSError:
                item["state"] = "unreadable"
            else:
                item.update(sha256=sha, size=size)
                # Frozen as it is: a version the store has not seen yet is
                # recorded as seen here (本机), never credited to the member
                # who proposed the abandonment -- they did not write it.
                history.record_file(store, root, rel)
                latest = store.latest(notes_mod.normalize_node(rel))
                kept = latest is not None and latest.sha256 == sha and latest.stored
                item["state"] = "kept" if kept else "fingerprint"
                if latest is not None and latest.sha256 == sha:
                    item["version"] = str(latest.seq)
        evidence.append(item)
    return evidence


# -- a route abandoned by a direct write (14.3) ------------------------------------------

#: 8.8: a direct write that abandons a route without saying why, refused.
DIRECT_REASON_REQUIRED = "放弃一条路线时必须写明理由"
#: 8.8 (ruled 2026-10-13): with the team file unreadable nobody can tell the PI's direct write from
#: a member's own row, so a write that would abandon a route is refused.
DIRECT_ROLE_UNKNOWN = "成员文件（team.toml）当前无法读取，无法判断你能否直接放弃这条路线"


def direct_abandonment(root: str | Path, op: str, number: str, fields: Mapping[str, Any]) -> dict[str, Any] | None:
    """14.3: whether a direct write of the attempt table TURNS a route
    abandoned -- a new row whose verdict abandons it, or a row whose verdict
    did not -- as the map says it (`route_status`: ☠, `[abandoned_verdicts]`,
    the researcher's own `[verdict_labels]`). Only for the PI and a single
    researcher: a member's row is their own change, and the proposal it
    becomes asks for the reason (12.4). Rewording a verdict that already
    abandons asks nothing again. None, or {node, number, cells: the row's
    cells as written now, None for a new row}; refused (`role_unknown`)
    while the team file cannot be read, as whose write it is cannot be told."""
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415
    from researchos.webapp import mapedit  # noqa: PLC0415

    root = Path(root)
    verdict = fields.get("verdict")
    config = _attempts_config(root)
    role = role_of(root)[0]
    if op not in ("append", "update") or not isinstance(verdict, str) or config is None or role in ("member", "none"):
        return None
    labels, markers = attempts_ingest.verdict_labels(root), abandon_markers(root)

    def abandons(cell: str) -> bool:
        status = attempts_ingest.route_status(attempts_ingest._clean_cell(cell), labels, markers)
        return status["status"] == "abandoned"

    if not abandons(verdict):
        return None
    cells = mapedit.row_cells(root, number) if op == "update" else None
    if op == "update" and (cells is None or abandons(cells["verdict"])):
        return None  # no such row (the write refuses it), or abandoned already
    if role == "unknown":
        raise ProposalRefused("role_unknown", f"{root / '.researchos' / 'team.toml'} cannot be read, so abandoning "
                              f"attempt {number.strip()} directly is refused; nothing written",
                              message_zh=DIRECT_ROLE_UNKNOWN)
    return {"node": f"attempt:{config.file}#{number.strip()}", "number": number.strip(), "cells": cells}


def with_reason(fields: Mapping[str, str], turn: Mapping[str, Any], reason: str) -> dict[str, str]:
    """The cells a direct abandonment writes: `fields`, with the reason in
    the result as an approval writes it (`abandon_result`) -- after the
    result sent, else after the row's own cell as written, its decoration
    kept (`mapedit.row_cells`)."""
    out = dict(fields)
    result = out["result"] if "result" in out else (turn["cells"] or {}).get("result", "")
    written = abandon_result(result, reason, out["verdict"])
    if "result" in out or written != result:
        out["result"] = written
    return out


def write_directly(root: str | Path, op: str, number: str, fields: Mapping[str, Any], reason: str | None = None, *,
                   ingest_lock: Any = None, via: str = "app",
                   now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """The attempt table written directly (`mapedit.apply_edit`) under
    14.3's rule: a write that turns a route abandoned (`direct_abandonment`)
    carries its reason, else it is refused (`reason_required`, nothing
    written); the reason goes into the result as an approval writes it
    (`with_reason`), and the basis is frozen as an approved abandonment
    freezes it -- `_evidence` at this moment, in a `decisions.toml` entry
    with act `abandoned`, by whoever wrote it, with no proposal and nobody
    approving. `apply_edit`'s result plus `decision` (the entry, or None)."""
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415
    from researchos.webapp import mapedit  # noqa: PLC0415

    root = Path(root)
    with write_guard(root, human=True) as held:
        turn = direct_abandonment(root, op, number, fields) if isinstance(fields, Mapping) else None
        if turn is None:
            return {**mapedit.apply_edit(root, op, number, fields, ingest_lock), "decision": None}
        mapedit.preview_edit(root, op, number, dict(fields))  # refused as the write would be, before the reason
        said = " ".join(str(reason or "").split())
        if not said:
            raise ProposalRefused("reason_required", f"abandoning attempt {turn['number']} needs a reason; "
                                  "nothing written", message_zh=DIRECT_REASON_REQUIRED)
        result = fields["result"] if "result" in fields else (turn["cells"] or {}).get("result", "")
        entry = {"act": decisions_mod.ABANDONED_ACT, "kind": "attempt_new" if op == "append" else "verdict_change",
                 "attempt": turn["node"], "number": turn["number"],
                 "verdict": attempts_ingest._clean_cell(fields["verdict"]),
                 "result": attempts_ingest._clean_cell(result) or None, "reason": said, "via": via}
        try:
            me = people.current_member_id()  # asked first: the entry names who, or the write would stop halfway
            decisions_mod.require_writable(root)
            # The entry as it will be recorded, checked before the row is written: a decision that could
            # never be recorded must not leave the route abandoned with no basis.
            decisions_mod.check({**entry, "by": me})
        except people.PeopleError as exc:
            raise ProposalRefused("no_me", str(exc), message_zh=exc.message_zh) from exc
        except decisions_mod.DecisionRefused as exc:
            raise ProposalRefused("decisions_unwritable", str(exc), message_zh=exc.message_zh) from exc
        written = mapedit.apply_edit(root, op, number, with_reason(fields, turn, said), ingest_lock)
        identity_id = _project_id(root)
        with history.Store.open(identity_id) as store:
            conn = db.connect(index_db_path(identity_id))
            try:
                entry["evidence"] = _evidence(conn, root, turn["node"], store, who=me or "", approved_by="", note=None)
            finally:
                conn.close()
        try:
            decision = decisions_mod.append_decision(root, entry, lock=held, now=now)
        except decisions_mod.DecisionRefused as exc:
            raise ProposalRefused("decisions_unwritable", f"the row was written, but its basis could not be "
                                  f"recorded ({exc})", message_zh="尝试表已经写入，但放弃时的依据没能记进 "
                                  "decisions.toml：" + exc.message_zh) from exc
    return {**written, "decision": _jsonable(dict(decision.data))}


def _outcome(ex: LocalExchange, stored: StoredProposal, act: str, by: str, text: str | None,
             extra: Mapping[str, Any] | None, now: Callable[[], datetime] | None, *,
             root: Path | None = None) -> dict[str, Any]:
    outcome_id = "o-" + secrets.token_hex(16)
    data: dict[str, Any] = {"format": FORMAT, "id": outcome_id, "proposal": stored.id, "act": act, "by": by,
                            "at": _now_iso(now), "member": stored.member}
    if stored.raw:
        data["proposal_sha256"] = hashlib.sha256(stored.raw).hexdigest()
    if text:
        data["text"] = text
    data.update(extra or {})
    raw = emit(data, _OUTCOME_HEADER)
    signature = _signature(root, by, raw) if root is not None else None
    written = ex.write_outcome(stored.id, outcome_id, raw, signature=signature)
    return dict(written.data)


def _pending(ex: LocalExchange, stored: StoredProposal) -> dict[str, Any]:
    state = state_of(ex, stored, ex.list_proposals(stored.member).proposals)
    if state["state"] != "pending":
        raise ProposalRefused("decided", f"proposal {stored.id} is {state['state']}; it takes no further answer",
                              details={"proposal_state": state["state"], "outcomes": state["outcomes"]})
    return state


def _exchange_for(root: Path, exchange: LocalExchange | None) -> LocalExchange:
    return exchange or exchange_mod.for_project(_project_id(root), root)


def approve(
    official_root: str | Path,
    proposal_id: str,
    *,
    note: str | None = None,
    exchange: LocalExchange | None = None,
    via: str = "cli",
    expected_id: Any = READ_NOW,
    timeout: float | None = None,
    now: Callable[[], datetime] | None = None,
    ingest_lock: Any = None,
    rescan: bool = True,
    member: str | None = None,
) -> dict[str, Any]:
    """Approve a proposal (module docstring, steps 1-6). Returns what was
    applied, the decision entry and the outcome. Raises `ProposalRefused`
    (nothing applied -- or, once the journal has begun, what was applied is
    finished by approving again) or the write guard's errors. `member`
    names whose proposal it is when the id is in more than one area."""
    root = Path(official_root)
    note = note.strip() if isinstance(note, str) and note.strip() else None
    _require_pi(root)  # asked first: a member's own copy is refused as such, whatever its index says
    with write_guard(root, expected_id, human=True, timeout=timeout) as held:
        pi = _require_pi(root)
        _preflight(root, pi)  # the outcome will be signed: refused now, before anything is applied
        ex = _exchange_for(root, exchange)
        stored = ex.open_proposal(proposal_id, member=member)
        _pending(ex, stored)
        member_id, data = stored.member, stored.data
        identity_id = _project_id(root)
        applied: dict[str, Any] = {"files": [], "attempt": None, "variable": None, "evidence": None, "lines": []}
        decision = decisions_mod.for_proposal(root, stored.id)
        if decision is not None and decision.get("by") != member_id:
            raise ProposalRefused("not_approvable", f"a proposal {stored.id} of {decision.get('by')} was already approved; "
                                  f"this one ({member_id}) is a copy of its id")
        attempt = variable = None
        if decision is not None:
            # Interrupted after the decision was recorded (the outcome could
            # not be written): everything was applied; only the outcome is left.
            _parsed(stored)
            applied["files"] = [{"path": f, "action": "applied"} for f in decision.get("files") or []]
            resumed = True
        else:
            resumed = False
            changes = what_changes(stored, root)
            if not changes["approvable"]:
                raise ProposalRefused("not_approvable", f"proposal {proposal_id} cannot be approved: "
                                      + "; ".join([f"{c['path']}: both sides changed" for c in changes["conflicts"]] + changes["problems"]),
                                      details={"conflicts": changes["conflicts"], "problems": changes["problems"],
                                               "resuming": changes["resuming"]})
            try:
                decisions_mod.require_writable(root)
            except decisions_mod.DecisionRefused as exc:
                raise ProposalRefused("not_approvable", str(exc), message_zh=exc.message_zh) from exc
            staged = _stage(root, changes)  # nothing applied yet: refused whole, or every file ready beside its target
            try:
                with history.Store.open(identity_id) as store:
                    for change, tmp in staged:
                        if change["action"] != "added":
                            history.record_file(store, root, change["path"])  # the version being replaced, kept
                        _place_file(root, stored, change, tmp)
                        if change["action"] != "removed":
                            history.record_file(store, root, change["path"], source="approval", who=member_id, note=note,
                                                approved_by=pi)
                    applied["files"] = [{"path": f["path"], "action": f["action"]} for f in changes["files"]
                                        if f["state"] in ("apply", "done")]

                    attempt = changes["attempt"]
                    if attempt is not None:
                        before = _max_seq(store)
                        if attempt["state"] == "apply":
                            from researchos.webapp import mapedit  # noqa: PLC0415

                            _mark(root, stored, "attempt", node=attempt["node"])
                            result = mapedit.apply_edit(root, attempt["op"], attempt["number"],
                                                          _as_cells(attempt["fields"]), ingest_lock)
                            _mark(root, stored, "attempt_applied", node=attempt["node"])
                            applied["attempt"] = {"node": attempt["node"], "op": attempt["op"], "backup": result["backup"],
                                                  "ingest_error": result["ingest_error"]}
                            _attribute_file(store, root, attempt["file"], before, who=member_id, approved_by=pi, note=note)
                        conn = db.connect(index_db_path(identity_id))
                        try:
                            if attempt["state"] == "apply":
                                _attribute_attempt(store, conn, attempt["node"], before, who=member_id, approved_by=pi,
                                                   note=note)
                            if attempt.get("abandons"):
                                if data["kind"] == "attempt_new":
                                    # The scripts this approval placed, read before the basis is frozen:
                                    # otherwise it records them but not the outputs they write.
                                    _scan_placed(conn, root, applied["files"], ingest_lock)
                                applied["evidence"] = _evidence(conn, root, attempt["node"], store, who=member_id,
                                                                approved_by=pi, note=note)
                        finally:
                            conn.close()

                    variable = changes["variable"]
                    if variable is not None and variable["state"] == "apply":
                        from researchos.records import cards  # noqa: PLC0415

                        if not variable.get("written"):
                            _write_version_file(root, variable, stored)
                        confirmed = cards.confirm(
                            root, variable["card"], attested=variable["attested"], expected_content=variable["content"],
                            via=via, approval={"by": member_id, "approved_by": pi, "proposal": stored.id, "note": note},
                        )
                        applied["variable"] = {"card": variable["card"], "version": confirmed.entry.get("version"),
                                               "entry": confirmed.entry.id, "reference": confirmed.reference.label}

                    # 14.2: a line, or the lines a new attempt grew out of (after its row exists).
                    lines = [(r["op"], r["child"], r["parent"], r.get("note"))
                             for r in [changes["route"]] if r is not None and r["state"] == "apply"]
                    if data["kind"] == "attempt_new" and attempt is not None:
                        lines += [("add", attempt["node"], p, None) for p in data["attempt"].get("parents") or []]
                    if lines:
                        _mark(root, stored, "route", lines=len(lines))
                        for op, child, parent, line_note in lines:
                            if _apply_line(root, op, child, parent, line_note, by=member_id):
                                applied["lines"].append({"op": op, "child": child, "parent": parent})
                        if changes["route"] is not None:
                            _mark(root, stored, "route_applied")
            finally:
                _unstage(staged)

            fields: dict[str, Any] = {
                "by": member_id, "act": "approved", "approved_by": pi, "proposal": stored.id, "kind": data["kind"],
                "note": note, "via": via, "files": [f["path"] for f in applied["files"]] or None,
            }
            if attempt is not None:
                fields.update(attempt=attempt["node"], number=attempt["number"])
            if data["kind"] == "verdict_change":
                fields.update(verdict=attempt["new"], result=attempt["result"], reason=attempt.get("reason"))
            elif data["kind"] == "attempt_new" and attempt.get("abandons"):
                fields.update(verdict=data["attempt"]["verdict"], result=attempt["result"], reason=attempt.get("reason"))
            if attempt is not None and attempt.get("abandons"):
                fields["act"] = "abandoned"
                fields["evidence"] = applied["evidence"] or []
            if variable is not None:
                fields.update(card=variable["card"], version=variable.get("number"))
            if data["kind"] == "route_link":
                fields.update({k: data["route"][k] for k in ("op", "child", "parent")})
            elif data["kind"] == "attempt_new" and data["attempt"].get("parents"):
                fields["parents"] = list(data["attempt"]["parents"])
            _mark(root, stored, "deciding")
            try:
                entry = decisions_mod.append_decision(root, fields, lock=held, now=now)
            except decisions_mod.DecisionRefused as exc:
                raise ProposalRefused("not_approvable", f"the changes were applied but the decision could not be "
                                      f"recorded ({exc}); approve again once decisions.toml is readable",
                                      message_zh=exc.message_zh) from exc
            decision = dict(entry.data)
        outcome = _outcome(ex, stored, "approved", pi, note, {
            "decision": decision["id"], "files": [f["path"] for f in applied["files"]] or None,
            "attempt": decision.get("attempt"),
            "variable": (applied["variable"] or {}).get("reference")
            or (f"{decision['card']} v{decision['version']}" if decision.get("card") and decision.get("version") else None),
        }, now, root=root)
        published = _publish_after(root, ex, expected_id, timeout, APPROVAL_NOTE.format(id=stored.id))
        if rescan:
            _rescan(root)
    return {"proposal": stored.id, "applied": applied, "decision": _jsonable(decision), "outcome": outcome,
            "published": published, "resumed": resumed}


#: The note a snapshot an approval published carries (written as it always was: the manifest, its
#: signature and old snapshots stay as they are). Read back by its shape (`approval_note`), so a
#: screen says it in its own words rather than showing Research OS's Chinese (13.6).
APPROVAL_NOTE = "批准提议 {id}"
_APPROVAL_NOTE_RE = re.compile(r"^批准提议 (pr-[0-9a-f]+)$")


def approval_note(note: Any) -> str | None:
    """The proposal a snapshot's note says it was published on approving, if it says so."""
    found = _APPROVAL_NOTE_RE.match(note) if isinstance(note, str) else None
    return found.group(1) if found else None


def _publish_after(root: Path, ex: LocalExchange, expected_id: Any, timeout: float | None, note: str) -> dict[str, Any]:
    """12.3: approving publishes a new snapshot. Contained: the approval is
    done and recorded whatever publishing says; a failure is returned (and
    「发布」 can be pressed again)."""
    from researchos import share  # noqa: PLC0415

    try:
        return share.publish(root, shared=ex.folders, note=note, expected_id=expected_id, timeout=timeout)
    except share.ShareError as exc:
        return {"published": False, "error": exc.code, "message": exc.message_zh, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.exception("publishing after approving into %s failed", root)
        return {"published": False, "error": "failed", "message": "发布没有完成", "detail": str(exc)}


def _scan_placed(conn, root: Path, placed: Iterable[Mapping[str, Any]], ingest_lock: Any = None) -> None:
    """The scripts an approval just placed, read by the dataflow extractor
    (one partial scan: those files, nothing else, 9.6) -- under the
    watcher's ingest lock when the app passes it -- so the outputs they
    write are in the index before an abandoned route's basis is frozen.
    Contained, as the scan after an approval is: a failure leaves the basis
    frozen with what the index holds, never the approval half done."""
    from researchos.ingest import dataflow as dataflow_ingest  # noqa: PLC0415

    kinds: dict[str, list[str]] = {".py": [], ".r": [], ".rmd": [], ".do": [], ".ado": []}
    for item in placed:
        rel = str(item.get("path") or "")
        suffix = Path(rel).suffix.lower()
        if item.get("action") != "removed" and suffix in kinds and (root / rel).is_file():
            kinds[suffix].append(rel)
    if not any(kinds.values()):
        return
    try:
        with ingest_lock if ingest_lock is not None else contextlib.nullcontext():
            dataflow_ingest.ingest_dataflow_repo(
                conn, root, kinds[".py"], kinds[".r"], kinds[".rmd"], do_paths=kinds[".do"], ado_paths=kinds[".ado"],
            )
    except Exception:  # noqa: BLE001 -- contained: the basis is frozen with what the index holds
        logger.exception("scanning the scripts an approval placed in %s failed", root)


def _rescan(root: Path) -> None:
    """The scan the CLI runs after an approval, so the new files are nodes
    at once (the app's watcher picks them up by itself). Contained."""
    from researchos.ingest import pipeline  # noqa: PLC0415

    try:
        conn = db.connect(index_db_path(_project_id(root)))
        try:
            pipeline.ingest_sources(conn, root)
            pipeline.ingest_records(conn, root)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 -- the approval is done; a failed scan is the watcher's next try
        logger.exception("the scan after approving into %s failed", root)


def _jsonable(value: Any) -> Any:
    from researchos.records.judgements import _jsonable as jsonable  # noqa: PLC0415

    return jsonable(value)


def return_(
    official_root: str | Path,
    proposal_id: str,
    reason: str,
    *,
    exchange: LocalExchange | None = None,
    expected_id: Any = READ_NOW,
    timeout: float | None = None,
    now: Callable[[], datetime] | None = None,
    member: str | None = None,
) -> dict[str, Any]:
    """「退回并说明」: an outcome with the reason; nothing is applied, ever.
    Refused once an approval of it has begun applying (module docstring):
    a returned proposal never leaves anything in the official version."""
    if not isinstance(reason, str) or not reason.strip():
        raise ProposalRefused("empty_reason", "returning a proposal says why (--reason)")
    root = Path(official_root)
    _require_pi(root)
    with write_guard(root, expected_id, human=True, timeout=timeout):
        pi = _require_pi(root)
        _preflight(root, pi)
        ex = _exchange_for(root, exchange)
        stored = ex.open_proposal(proposal_id, member=member)
        _parsed(stored)
        _pending(ex, stored)
        started = read_journal(root, stored.id)
        decision = decisions_mod.for_proposal(root, stored.id)
        if (started["started"] and started["member"] in (None, stored.member)) or (
                decision is not None and decision.get("by") == stored.member):
            raise ProposalRefused("approval_started", f"an approval of {stored.id} has begun applying it "
                                  f"({journal_path(root, stored.id)}); approve it again to finish -- it is not returned")
        return _outcome(ex, stored, "returned", pi, reason.strip(), None, now, root=root)


def discuss(
    official_root: str | Path,
    proposal_id: str,
    text: str,
    *,
    exchange: LocalExchange | None = None,
    expected_id: Any = READ_NOW,
    timeout: float | None = None,
    now: Callable[[], datetime] | None = None,
    member: str | None = None,
) -> dict[str, Any]:
    """「讨论」: a note to the member; the proposal stays open."""
    if not isinstance(text, str) or not text.strip():
        raise ProposalRefused("empty_text", "a discussion says something (--text)")
    root = Path(official_root)
    _require_pi(root)
    with write_guard(root, expected_id, human=True, timeout=timeout):
        pi = _require_pi(root)
        _preflight(root, pi)
        ex = _exchange_for(root, exchange)
        stored = ex.open_proposal(proposal_id, member=member)
        _parsed(stored)
        _pending(ex, stored)
        return _outcome(ex, stored, "discussed", pi, text.strip(), None, now, root=root)


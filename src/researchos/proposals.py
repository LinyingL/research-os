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
attempt table itself excepted: the table travels as rows). What it does
not carry -- another new row, a change outside the table's rows -- is
listed in `not_carried`, so nothing is dropped silently. The member sees
it (the CLI's dry run) before `write_proposal` writes it.

The kinds, and what each carries besides its files:

- `attempt_new` -- the new attempt-table row's fields;
- `verdict_change` -- the attempt number, the old and new verdict and
  result; when the new verdict abandons the route (a marker listed in
  `attempts.toml`'s `abandoned_verdicts`, or ☠ always) the result and a
  reason are REQUIRED (12.4);
- `variable_version` -- the card id and the version file's bytes, and the
  member's own answer to "was the output built with this definition"
  (`attested`, 9.11), which the PI's confirmation records as theirs;
- `files` -- only files.

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

An approval interrupted between steps leaves the proposal pending; the
next approval finds what was already applied ("same") and does not apply
it twice. `return_` (with a reason) and `discuss` (a note to the member;
the proposal stays open) write an outcome and nothing else: a returned
proposal never applies anything.

Not a node until approved
-------------------------

A pending proposal lives only in the exchange, which may not lie inside
the project (`researchos.exchange.check_location`), so no scan and no
reader of the official index -- tree, lineage, canvas, trace, citations,
variables -- ever sees it. The one reader that does is `listing`: the
proposer's 「我的提议」 and the PI's inbox; anyone else sees none.
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

from researchos import db, history, paths, people
from researchos import exchange as exchange_mod
from researchos.exchange import ExchangeError, LocalExchange, StoredProposal
from researchos.records import decisions as decisions_mod
from researchos.records import files as record_files
from researchos.records import ledger as ledger_mod
from researchos.records.identity import IdentityState, read_identity
from researchos.records.situation import READ_NOW, index_db_path, write_guard

logger = logging.getLogger(__name__)

KINDS = ("attempt_new", "verdict_change", "variable_version", "files")
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
                 "decisions.toml", "canvas.json")

_PROPOSAL_HEADER = "# 一份提议：成员工作副本里的改动，PI 批准后才进入正式版本。由 Research OS 写入，写成后不再改动。\n"
_OUTCOME_HEADER = "# PI 对一份提议的答复。由 Research OS 写入，写成后不再改动。\n"
_VERSION_FILE_RE = re.compile(r"^\.researchos/variables/([^/]+)/v([1-9][0-9]*)\.toml$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

#: Product language (8.8) for every refusal.
MESSAGES = {
    "single_person": "这是单人项目：改动直接生效，不需要提议和批准",
    "team_unreadable": "成员文件（team.toml）当前无法读取，无法判断谁是 PI",
    "no_me": "请先设置“我是谁”（researchos me set）",
    "not_pi": "只有 PI 可以批准或退回提议",
    "pi_changes_directly": "PI 直接修改正式版本，不需要提议",
    "not_member": "你不在这个项目的成员名单里",
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


def team_of(root: str | Path) -> people.TeamRead:
    """The team, refused unless it is a team project that can be read."""
    team = people.read_team(root)
    mode = people.team_mode(team)
    if mode == "single":
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
        raise ProposalRefused("not_member", f"{me} is not in {team.path}")
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
    base = {
        "format": FORMAT, "project": project_id, "snapshot": snapshot_id(files), "working_copy": str(working_copy),
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
) -> Draft:
    """What a proposal of `kind` from `working_copy` would carry (module
    docstring). Reads only."""
    root = Path(working_copy)
    if kind not in KINDS:
        raise ProposalRefused("bad_kind", f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
    if not isinstance(sentence, str) or not sentence.strip() or len(sentence) > MAX_SENTENCE:
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
            if kind == "attempt_new":
                chosen = _pick(new_rows, number, "new attempt row")
                data["attempt"] = {"number": chosen, **work_rows[chosen]}
                carried.add(chosen)
            elif kind == "verdict_change":
                verdicts = [n for n in changed if work_rows[n]["verdict"] != base_rows[n]["verdict"]]
                chosen = _pick(verdicts, number, "attempt verdict")
                old, new = base_rows[chosen], work_rows[chosen]
                verdict: dict[str, Any] = {"number": chosen, "old": old["verdict"], "new": new["verdict"],
                                           "old_result": old["result"], "result": new["result"] if result is None else result}
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
    files = _file_changes(root, base, work, skip=skip, only=set(only) if only is not None else None)
    if kind == "files" and not files:
        raise ProposalRefused("nothing", "no file differs from the official version")
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
    texts = {rel: source for rel, source in draft.texts.items()}
    try:
        return ex.write_proposal(draft.data["by"], proposal_id, raw, texts, draft.blobs)
    except ExchangeError:
        raise
    except OSError as exc:
        raise ExchangeError("unreachable", f"could not write the proposal: {exc}") from exc


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
    outcomes, problems = ex.outcomes(stored.id)
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


def _summary(stored: StoredProposal, state: Mapping[str, Any]) -> dict[str, Any]:
    data = stored.data
    files = data.get("file") or []
    return {
        "id": stored.id, "by": stored.member, "at": data.get("at"), "kind": data.get("kind"),
        "sentence": data.get("sentence"), "base": data.get("base"), "attempt": data.get("attempt"),
        "verdict": data.get("verdict"), "variable": data.get("variable"),
        "files": [{k: f.get(k) for k in ("path", "action", "size", "store")} for f in files],
        "not_carried": data.get("not_carried") or [], "supersedes": data.get("supersedes"), **state,
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
    for stored in found.proposals:
        if stored.data.get("project") != pid:
            out["problems"].append({"kind": "wrong_project", "path": str(stored.directory),
                                    "detail": f"belongs to project {stored.data.get('project')}"})
            continue
        siblings = [s for s in found.proposals if s.member == stored.member]
        out["proposals"].append(_summary(stored, state_of(ex, stored, siblings)))
    order = {"pending": 0, "conflict": 1, "approved": 2, "returned": 3, "superseded": 4}
    out["proposals"].sort(key=lambda p: (order.get(p["state"], 9), str(p.get("at") or "")))
    return out


def open_for(root: str | Path, proposal_id: str, *, exchange: LocalExchange | None = None) -> dict[str, Any]:
    """One proposal as its reader may see it: the PI gets `changes`
    (`what_changes`), its author the proposal and its outcomes; anyone else
    is refused."""
    root = Path(root)
    role, me, _team = role_of(root)
    if role not in ("pi", "member"):
        raise ProposalRefused("not_member" if role == "none" else ("single_person" if role == "single" else "team_unreadable"),
                              "only the PI and the proposal's author see a proposal")
    pid = _project_id(root)
    ex = exchange or exchange_mod.for_project(pid, root)
    stored = ex.open_proposal(proposal_id)
    if role == "member" and stored.member != me:
        raise ProposalRefused("not_yours", f"proposal {proposal_id} is not yours")
    siblings = ex.list_proposals(stored.member).proposals
    out = _summary(stored, state_of(ex, stored, siblings))
    if role == "pi":
        out["changes"] = what_changes(stored, root)
    return out


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


def _file_change(root: Path, stored: StoredProposal, item: Mapping[str, Any]) -> dict[str, Any]:
    rel = item["path"]
    out: dict[str, Any] = {k: item.get(k) for k in ("path", "action", "size", "sha256", "base_sha256", "base_size", "store")}
    _confined(root, rel)
    official = _official_fp(root, rel)
    out["official_sha256"] = official.get("sha256") if official else None
    out["official_size"] = official.get("size") if official else None
    if official is not None and official.get("dataless"):
        out.update(state="problem", reason="正式版本里的这个文件还在云端，无法比较")
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
        out["reason"] = "这个文件在你提议之后，正式版本里也改了" if official is not None else "这个文件在正式版本里已经不在了"
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
            out.update(state="problem", reason="提议里缺少这个文件的内容")
            return out
        out["content"] = str(content)
    if state in ("apply", "conflict"):
        _describe(root, rel, item, out)
    return out


def _describe(root: Path, rel: str, item: Mapping[str, Any], out: dict[str, Any]) -> None:
    """A line diff for text; for data, size, fingerprint and -- for tables
    -- rows and columns (12.3)."""
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
        return
    try:
        a = history.data_summary(old_path, rel) if out.get("official_sha256") else None
        b = history.data_summary(Path(out["content"]), rel) if item["action"] != "removed" else None
    except OSError:
        a = b = None
    out["summary"] = history.table_sentence(a, b) if (a or b) else None
    sizes = f"{out.get('official_size') if out.get('official_size') is not None else '—'} → {item.get('size') if item.get('size') is not None else '—'} 字节"
    out["sentence"] = (out["summary"] + "；" if out.get("summary") else "") + f"大小 {sizes}"


def _attempt_change(root: Path, data: Mapping[str, Any]) -> dict[str, Any] | None:
    from researchos.webapp import mapedit  # noqa: PLC0415

    config = _attempts_config(root)
    kind = data["kind"]
    if kind not in ("attempt_new", "verdict_change"):
        return None
    if config is None:
        return {"state": "problem", "reason": "正式版本没有配置尝试表"}
    try:
        text = (root / config.file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"state": "problem", "reason": f"尝试表无法读取：{exc}"}
    rows = _rows(text, config) or {}
    node = f"attempt:{config.file}#"
    if kind == "attempt_new":
        row = data["attempt"]
        number = row["number"]
        out: dict[str, Any] = {"op": "append", "number": number, "node": node + number, "file": config.file,
                               "fields": {k: row[k] for k in ROW_FIELDS}}
        if number in rows:
            if rows[number] == out["fields"]:
                out["state"] = "same"
            else:
                out.update(state="conflict", reason=f"正式版本的尝试表里已经有第 {number} 行")
            return out
    else:
        v = data["verdict"]
        number = v["number"]
        fields = {"verdict": v["new"]}
        if v["result"] != v["old_result"]:
            fields["result"] = v["result"]
        abandons = v.get("abandons") == "yes" or is_abandonment(v["new"], abandon_markers(root))
        out = {"op": "update", "number": number, "node": node + number, "file": config.file, "fields": fields,
               "old": v["old"], "new": v["new"], "abandons": abandons,
               "result": v["result"], "reason": v.get("reason")}
        if abandons and (not str(v.get("reason") or "").strip() or not v["result"].strip()):
            out.update(state="problem", reason=MESSAGES["reason_required"])
            return out
        current = rows.get(number)
        if current is None:
            out.update(state="conflict", reason=f"正式版本的尝试表里没有第 {number} 行")
            return out
        if current["verdict"] == v["new"] and current["result"] == v["result"]:
            out["state"] = "same"
            return out
        if current["verdict"] != v["old"] or current["result"] != v["old_result"]:
            out.update(state="conflict", reason=f"第 {number} 行的结论或结果在正式版本里也改了")
            return out
    try:
        shown = mapedit.preview_edit(root, out["op"], number, out["fields"])
    except mapedit.MapEditError as exc:
        out.update(state="problem", reason=str(exc), code=exc.code)
        return out
    except Exception as exc:  # noqa: BLE001 -- AttemptsConfigError and friends: shown, never applied
        out.update(state="problem", reason=str(exc))
        return out
    out.update(state="apply", diff=shown["diff"], old_row=shown["old_row"], new_row=shown["new_row"])
    return out


def _variable_change(root: Path, stored: StoredProposal, data: Mapping[str, Any]) -> dict[str, Any] | None:
    from researchos.records import variables as V  # noqa: PLC0415

    variable = data.get("variable")
    if variable is None:
        return None
    card_id = variable["card"]
    out: dict[str, Any] = {"card": card_id, "attested": variable["attested"], "proposed_file": variable["file"]}
    try:
        raw = stored.content_path({"path": variable["file"], "store": "text"}).read_bytes()
    except (OSError, ExchangeError):
        return {**out, "state": "problem", "reason": "提议里缺少这个版本文件"}
    if hashlib.sha256(raw).hexdigest() != variable["sha256"]:
        return {**out, "state": "problem", "reason": "版本文件与提议记录的指纹不符"}
    try:
        parsed = V.parse_version(raw.decode("utf-8"))
    except (V.VersionInvalid, UnicodeDecodeError) as exc:
        return {**out, "state": "problem", "reason": f"版本文件无法读取：{exc}"}
    missing = V.missing_for_confirm(parsed)
    out["content"] = V.content_hash(parsed)
    out["text"] = raw.decode("utf-8")
    if missing:
        return {**out, "state": "problem", "reason": "这一版还不完整，缺少：" + "、".join(missing)}
    problem = V.id_problem(card_id)
    if problem:
        return {**out, "state": "problem", "reason": problem}
    found = V.find_card_dirs(root, card_id)
    if len(found) > 1:
        return {**out, "state": "problem", "reason": V.MESSAGES["case_duplicate"]}
    if not found:
        out.update(number=1, new_card=True)
        if variable["base_version"] != 0:
            out.update(state="conflict", reason="这个变量卡在正式版本里已经不在了")
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
    if card.draft is not None:
        out.update(state="conflict", reason=f"正式版本里这个变量卡有未确认的草稿 v{card.draft}")
    elif highest != variable["base_version"]:
        out.update(state="conflict", reason=f"这个变量卡在正式版本里已经有 v{highest}（提议基于 v{variable['base_version']}）")
    else:
        out["state"] = "apply"
        if card.in_use is not None:
            view = card.versions.get(card.in_use)
            old = view.data.decode("utf-8", errors="replace") if view is not None and view.data is not None else ""
            out["diff"], out["truncated"] = history.unified(old, out["text"], f"v{card.in_use}", f"v{card.next_number}")
    return out


def what_changes(stored: StoredProposal, official_root: str | Path) -> dict[str, Any]:
    """Exactly what approving `stored` would do to the official version
    (module docstring). Reads only."""
    root = Path(official_root)
    data = _parsed(stored)
    problems: list[str] = []
    if data["project"] != _project_id(root):
        problems.append(MESSAGES["wrong_project"])
    team = people.read_team(root)
    author = next((m for m in team.members if m.id == stored.member), None)
    if author is None or author.role != "member":
        problems.append(f"{stored.member}: 不是这个项目的成员，提议不能批准")
    current = snapshot_id(manifest(root))
    out: dict[str, Any] = {
        "id": stored.id, "kind": data["kind"], "by": stored.member, "sentence": data["sentence"], "base": data["base"],
        "official_now": current, "moved": current != data["base"],
    }
    out["attempt"] = _attempt_change(root, data)
    out["variable"] = _variable_change(root, stored, data)
    out["files"] = [_file_change(root, stored, item) for item in data.get("file") or []]
    out["not_carried"] = data.get("not_carried") or []
    conflicts = []
    for part in ([out["attempt"]] if out["attempt"] else []) + ([out["variable"]] if out["variable"] else []) + out["files"]:
        if part.get("state") == "conflict":
            conflicts.append({"path": part.get("path") or part.get("file") or part.get("card"), "reason": part.get("reason")})
        elif part.get("state") == "problem":
            problems.append(f"{part.get('path') or part.get('file') or part.get('card')}: {part.get('reason')}")
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


def _place_file(root: Path, stored: StoredProposal, change: Mapping[str, Any]) -> None:
    rel = change["path"]
    target = _confined(root, rel)
    backup = _backup_path(root, stored.id, rel)
    now = fingerprint(target)
    if change["action"] == "removed":
        if now is None:
            return
        if now.get("sha256") != change["base_sha256"]:
            raise ProposalRefused("not_approvable", f"{rel} changed in the official version while approving; nothing more applied")
        _keep_old(root, target, backup, move=True)
        return
    record_files.ensure_dir_within(root, target.parent)
    tmp = record_files.temp_path_for(target)
    try:
        got = exchange_mod.copy_file(Path(change["content"]), tmp)
        if got != change["sha256"]:
            raise ProposalRefused("not_approvable", f"{rel}: the proposal's content does not match its fingerprint",
                                  message_zh="提议里的文件内容与记录的指纹不符")
        if now is not None:
            if now.get("sha256") == change["sha256"]:
                return
            expected = change.get("base_sha256") if change["action"] == "changed" else None
            if now.get("sha256") != expected:
                raise ProposalRefused("not_approvable", f"{rel} changed in the official version while approving")
            _keep_old(root, target, backup, move=False)
        os.replace(tmp, target)
        record_files._fsync_dir(target.parent)
    finally:
        if tmp.exists():
            tmp.unlink()


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
                history.record_file(store, root, rel, source="approval", who=who, note=note, approved_by=approved_by)
                latest = store.latest(notes_mod.normalize_node(rel))
                kept = latest is not None and latest.sha256 == sha and latest.stored
                item["state"] = "kept" if kept else "fingerprint"
                if latest is not None and latest.sha256 == sha:
                    item["version"] = str(latest.seq)
        evidence.append(item)
    return evidence


def _outcome(ex: LocalExchange, stored: StoredProposal, act: str, by: str, text: str | None,
             extra: Mapping[str, Any] | None, now: Callable[[], datetime] | None) -> dict[str, Any]:
    outcome_id = "o-" + secrets.token_hex(16)
    data: dict[str, Any] = {"format": FORMAT, "id": outcome_id, "proposal": stored.id, "act": act, "by": by,
                            "at": _now_iso(now)}
    if text:
        data["text"] = text
    data.update(extra or {})
    written = ex.write_outcome(stored.id, outcome_id, emit(data, _OUTCOME_HEADER))
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
) -> dict[str, Any]:
    """Approve a proposal (module docstring, steps 1-6). Returns what was
    applied, the decision entry and the outcome. Raises `ProposalRefused`
    (nothing applied) or the write guard's errors."""
    root = Path(official_root)
    note = note.strip() if isinstance(note, str) and note.strip() else None
    _require_pi(root)  # asked first: a member's own copy is refused as such, whatever its index says
    with write_guard(root, expected_id, human=True, timeout=timeout) as held:
        pi = _require_pi(root)
        ex = _exchange_for(root, exchange)
        stored = ex.open_proposal(proposal_id)
        _pending(ex, stored)
        changes = what_changes(stored, root)
        if not changes["approvable"]:
            raise ProposalRefused("not_approvable", f"proposal {proposal_id} cannot be approved: "
                                  + "; ".join([f"{c['path']}: both sides changed" for c in changes["conflicts"]] + changes["problems"]),
                                  details={"conflicts": changes["conflicts"], "problems": changes["problems"]})
        try:
            decisions_mod.require_writable(root)
        except decisions_mod.DecisionRefused as exc:
            raise ProposalRefused("not_approvable", str(exc), message_zh=exc.message_zh) from exc
        member, data = stored.member, stored.data
        identity_id = _project_id(root)
        applied: dict[str, Any] = {"files": [], "attempt": None, "variable": None, "evidence": None}
        with history.Store.open(identity_id) as store:
            for change in changes["files"]:
                if change["state"] != "apply":
                    continue
                if change["action"] != "added":
                    history.record_file(store, root, change["path"])  # the version being replaced, kept
                _place_file(root, stored, change)
                if change["action"] != "removed":
                    history.record_file(store, root, change["path"], source="approval", who=member, note=note,
                                        approved_by=pi)
                applied["files"].append({"path": change["path"], "action": change["action"]})

            attempt = changes["attempt"]
            if attempt is not None:
                before = _max_seq(store)
                if attempt["state"] == "apply":
                    from researchos.webapp import mapedit  # noqa: PLC0415

                    result = mapedit.apply_edit(root, attempt["op"], attempt["number"], attempt["fields"], ingest_lock)
                    applied["attempt"] = {"node": attempt["node"], "op": attempt["op"], "backup": result["backup"],
                                          "ingest_error": result["ingest_error"]}
                conn = db.connect(index_db_path(identity_id))
                try:
                    if attempt["state"] == "apply":
                        _attribute_attempt(store, conn, attempt["node"], before, who=member, approved_by=pi, note=note)
                    if data["kind"] == "verdict_change" and attempt.get("abandons"):
                        applied["evidence"] = _evidence(conn, root, attempt["node"], store, who=member,
                                                        approved_by=pi, note=note)
                finally:
                    conn.close()

            variable = changes["variable"]
            if variable is not None and variable["state"] == "apply":
                from researchos.records import cards  # noqa: PLC0415

                _write_version_file(root, variable, stored)
                confirmed = cards.confirm(
                    root, variable["card"], attested=variable["attested"], expected_content=variable["content"], via=via,
                    approval={"by": member, "approved_by": pi, "proposal": stored.id, "note": note},
                )
                applied["variable"] = {"card": variable["card"], "version": confirmed.entry.get("version"),
                                       "entry": confirmed.entry.id, "reference": confirmed.reference.label}

        decision = decisions_mod.for_proposal(root, stored.id)
        if decision is None:
            fields: dict[str, Any] = {
                "by": member, "act": "approved", "approved_by": pi, "proposal": stored.id, "kind": data["kind"],
                "note": note, "via": via, "files": [f["path"] for f in applied["files"]] or None,
            }
            if attempt is not None:
                fields.update(attempt=attempt["node"], number=attempt["number"])
            if data["kind"] == "verdict_change":
                fields.update(verdict=attempt["new"], result=attempt["result"], reason=attempt.get("reason"))
                if attempt.get("abandons"):
                    fields["act"] = "abandoned"
                    fields["evidence"] = applied["evidence"] or []
            if variable is not None:
                fields.update(card=variable["card"], version=variable.get("number"))
            try:
                entry = decisions_mod.append_decision(root, fields, lock=held, now=now)
            except decisions_mod.DecisionRefused as exc:
                raise ProposalRefused("not_approvable", f"the changes were applied but the decision could not be "
                                      f"recorded ({exc}); approve again once decisions.toml is readable",
                                      message_zh=exc.message_zh) from exc
            decision = dict(entry.data)
        outcome = _outcome(ex, stored, "approved", pi, note, {
            "decision": decision["id"], "files": [f["path"] for f in applied["files"]] or None,
            "attempt": attempt["node"] if attempt is not None else None,
            "variable": (applied["variable"] or {}).get("reference"),
        }, now)
        if rescan:
            _rescan(root)
    return {"proposal": stored.id, "applied": applied, "decision": _jsonable(decision), "outcome": outcome}


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
) -> dict[str, Any]:
    """「退回并说明」: an outcome with the reason; nothing is applied, ever."""
    if not isinstance(reason, str) or not reason.strip():
        raise ProposalRefused("empty_reason", "returning a proposal says why (--reason)")
    root = Path(official_root)
    _require_pi(root)
    with write_guard(root, expected_id, human=True, timeout=timeout):
        pi = _require_pi(root)
        ex = _exchange_for(root, exchange)
        stored = ex.open_proposal(proposal_id)
        _parsed(stored)
        _pending(ex, stored)
        return _outcome(ex, stored, "returned", pi, reason.strip(), None, now)


def discuss(
    official_root: str | Path,
    proposal_id: str,
    text: str,
    *,
    exchange: LocalExchange | None = None,
    expected_id: Any = READ_NOW,
    timeout: float | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """「讨论」: a note to the member; the proposal stays open."""
    if not isinstance(text, str) or not text.strip():
        raise ProposalRefused("empty_text", "a discussion says something (--text)")
    root = Path(official_root)
    _require_pi(root)
    with write_guard(root, expected_id, human=True, timeout=timeout):
        pi = _require_pi(root)
        ex = _exchange_for(root, exchange)
        stored = ex.open_proposal(proposal_id)
        _parsed(stored)
        _pending(ex, stored)
        return _outcome(ex, stored, "discussed", pi, text.strip(), None, now)


"""Notes on any node: `.researchos/notes.toml` (DESIGN.md 12.6, task V8
phase 2).

An append-only ledger (9.3, the engine of `researchos.records.ledger`):

::

    # 节点上的笔记。Research OS 只追加、不改写；撤回一条笔记也是追加一条记录。
    [[note]]
    id   = "n-…"
    seq  = 3
    at   = "2026-10-11T15:02:11+02:00"
    by   = "li@example.com"
    act  = "note"
    node = "figure:figures/fig1.png"
    text = "这张图的纵轴单位要换成百分点"
    via  = "cli"

    [[note]]
    id   = "n-…"
    seq  = 4
    at   = "…"
    act  = "withdrawn"
    node = "figure:figures/fig1.png"
    withdraws = "n-…"            # the note it takes back
    via  = "app"

A note is never edited, only withdrawn -- and withdrawing is an entry too,
so the file stays its own history. `text` may hold line breaks; the
emitter writes them as the TOML escape, so the file's own lines are never
split.

The trust rules are the judgment ledger's (`researchos.records.trust`),
the same functions:

- **missing though expected** -- a snapshot of the file exists under
  `.researchos/backups/` (one is taken right after the file is founded, and
  daily after that), yet the file is not there: refused, never re-created;
- **in the cloud**, **unreadable**, **invalid** (the line is named; a
  withdrawal that names no earlier note of its node is invalid too), **a
  sync conflict copy beside it**: every write refused;
- **shrunk** -- readable, but lacking entries the index applied (the
  index keeps a copy, `applied_notes`, migration 0007): nothing changes,
  writes are refused, and the question 「笔记记录文件比图谱少了 N 条」 has
  the ledger's two answers (`answer_shrunk`).

While the file cannot be trusted, readers show the notes the index last
applied, marked as such -- never nothing, and never a guess.

Nodes are named as the index names them (`script:…`, `figure:…`,
`attempt:…`). A bare project-relative path is accepted wherever a node is
asked for and becomes the node id its extension gives (`normalize_node`);
a file no node type covers (a draft, a PDF) is `file:<path>`.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection
from typing import Any, Callable, Iterable, Mapping

from researchos import db, people
from researchos.records import files as record_files
from researchos.records import ledger as ledger_mod
from researchos.records.files import RecordState
from researchos.records.identity import IdentityState, ProjectIdentity, read_identity
from researchos.records.ledger import LedgerEntry, LedgerLoad, LedgerSchema, LedgerWriteRefused
from researchos.records.situation import READ_NOW, index_db_path, write_guard
from researchos.records.trust import Trust, TrustDecision, assess_ledger

NOTES_FILENAME = "notes.toml"
RECORD_STATUS_NAME = "notes"
NOTE_ACT = "note"
WITHDRAWN_ACT = "withdrawn"
ACTS = frozenset({NOTE_ACT, WITHDRAWN_ACT})
VIAS = frozenset({"cli", "app", "mcp", "recovered"})
MAX_TEXT = 20_000

_HEADER = "# 节点上的笔记。Research OS 只追加、不改写；撤回一条笔记也是追加一条记录。\n"

#: Product language (8.8) for each trust reason, for notes.
MESSAGES = {
    "no_identity": "项目身份文件无法读取",
    "migrating": "项目正在迁移，迁移完成前不能写入人工记录",
    "missing": "笔记记录文件当前无法读取，请先恢复它",
    "dataless": "记录文件正在从云端下载…",
    "unreadable": "笔记记录文件当前无法读取，请先恢复它",
    "invalid": "笔记记录文件当前无法读取，请先恢复它",
    "empty_but_expected": "笔记记录文件当前无法读取，请先恢复它",
    "conflict_copy": "笔记记录文件旁有同步冲突副本，请先处理",
    "shrunk": "笔记记录文件比图谱少了 {n} 条",
}

#: Node-id prefixes Research OS knows (Section 4's types, the citation
#: references of 11.4, a variable card, and `file:` for any other file).
KNOWN_PREFIXES = frozenset({
    "project", "experiment", "commit", "figure", "section", "claim", "reference", "contributor",
    "attempt", "script", "dataset", "ref", "variable", "file",
})
FILE_PREFIXES = frozenset({"script", "dataset", "figure", "file"})
_PREFIX_RE = re.compile(r"^([a-z_]+):(.+)$", re.DOTALL)
_MAX_NODE = 2000


class NoteRefused(Exception):
    """Nothing was written. `code`: `untrusted` (`decision` says why),
    `invalid`, `no_index`, `no_such_note`, `already_withdrawn`,
    `no_question`, `question_changed`, `would_lose`."""

    def __init__(self, code: str, message: str, *, decision: TrustDecision | None = None, message_zh: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.decision = decision
        self._zh = message_zh

    @property
    def message_zh(self) -> str | None:
        if self._zh is not None:
            return self._zh
        if self.decision is not None and self.decision.reason is not None:
            return message_of(self.decision)
        return None


def message_of(decision: TrustDecision) -> str | None:
    if decision.reason is None:
        return None
    return MESSAGES.get(decision.reason, "笔记记录文件当前无法读取，请先恢复它").format(n=len(decision.missing))


# -- node names ---------------------------------------------------------------------


def normalize_node(given: str) -> str:
    """The node id `given` names (module docstring). Raises `ValueError`
    for something that cannot name a node: empty, a line break, an absolute
    path or one climbing above the project."""
    if not isinstance(given, str) or not given.strip():
        raise ValueError("a node is named by its id (e.g. script:analysis/fit.py) or a project-relative path")
    given = given.strip()
    if len(given) > _MAX_NODE or any(ch in given for ch in "\r\n\x00\x85  "):
        raise ValueError(f"not a node name: {given!r}")
    match = _PREFIX_RE.match(given)
    if match and match.group(1) in KNOWN_PREFIXES:
        prefix, rest = match.group(1), match.group(2)
        if prefix not in FILE_PREFIXES:
            return given
        return f"{prefix}:{_relative(rest)}"
    path = _relative(given)
    from researchos.ingest import dataflow  # noqa: PLC0415 -- the canvas's own classification (8.1)

    return f"{dataflow.node_type_for_path(path) or 'file'}:{path}"


def _relative(path: str) -> str:
    if posixpath.isabs(path) or path.startswith("\\") or re.match(r"^[A-Za-z]:[\\/]", path):
        raise ValueError(f"{path!r} is absolute; nodes are named by project-relative paths")
    norm = posixpath.normpath(path.replace("\\", "/"))
    if norm in (".", "..") or norm.startswith("../"):
        raise ValueError(f"{path!r} points outside the project")
    return norm


def node_path(node: str) -> str | None:
    """The project-relative path a file node names, else None."""
    match = _PREFIX_RE.match(node)
    if match and match.group(1) in FILE_PREFIXES:
        return match.group(2)
    return None


# -- schema -------------------------------------------------------------------------


def _validate_note(entry: Mapping[str, Any]) -> None:
    act = entry.get("act")
    if act == NOTE_ACT:
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("a note needs its 'text'")
        if len(text) > MAX_TEXT:
            raise ValueError(f"a note's text is at most {MAX_TEXT} characters")
        if "withdraws" in entry:
            raise ValueError("'withdraws' belongs only on a 'withdrawn' entry")
    else:
        target = entry.get("withdraws")
        if not isinstance(target, str) or not target:
            raise ValueError("a 'withdrawn' entry names the note it takes back in 'withdraws'")
    via = entry.get("via")
    if via is not None and via not in VIAS:
        raise ValueError(f"'via' must be one of {', '.join(sorted(VIAS))}, got {via!r}")


def _validate_new_note(entry: Mapping[str, Any]) -> None:
    if entry.get("via") not in VIAS:
        raise ValueError(f"'via' must be one of {', '.join(sorted(VIAS))}, got {entry.get('via')!r}")


NOTES_SCHEMA = LedgerSchema(
    table="note",
    id_prefix="n-",
    act_field="act",
    acts=ACTS,
    undo_act=None,
    key_fields=("node",),
    field_order=("id", "seq", "at", "by", "act", "node", "text", "withdraws", "via", "recovered_from", "recovered_at"),
    header=_HEADER,
    validate=_validate_note,
    validate_new=_validate_new_note,
    multiline=frozenset({"note.text"}),
)


def notes_path(project_root: str | Path) -> Path:
    from researchos import paths  # noqa: PLC0415

    return paths.project_dir(project_root) / NOTES_FILENAME


def _target_of(ledger: ledger_mod.Ledger, withdrawal: LedgerEntry) -> LedgerEntry | None:
    """The note a withdrawal takes back: by id, or -- after 「把缺少的补回
    文件」 restored both -- the restored copy of that id."""
    target = withdrawal.get("withdraws")
    for e in ledger.entries:
        if e.index >= withdrawal.index:
            break
        if e.get("act") == NOTE_ACT and (e.id == target or e.get("recovered_from") == target):
            return e
    return None


def _withdrawal_problem(ledger: ledger_mod.Ledger) -> tuple[str, int | None] | None:
    for e in ledger.entries:
        if e.get("act") != WITHDRAWN_ACT:
            continue
        target = _target_of(ledger, e)
        if target is None:
            return f"'withdraws' names {e.get('withdraws')!r}, which is not an earlier note", e.line
        if target.key != e.key:
            return f"'withdraws' names a note on a different node ({target.get('node')!r})", e.line
    return None


def load_notes(project_root: str | Path) -> LedgerLoad:
    """`ledger.load_ledger` plus the one rule across entries: a withdrawal
    names an earlier note of its own node."""
    loaded = ledger_mod.load_ledger(notes_path(project_root), NOTES_SCHEMA)
    if loaded.ledger is not None:
        problem = _withdrawal_problem(loaded.ledger)
        if problem is not None:
            message, line = problem
            where = f"line {line}: " if line else ""
            return LedgerLoad(RecordState.INVALID, loaded.path, data=loaded.data, error=where + message, line=line,
                              conflict_copies=loaded.conflict_copies)
    return loaded


def notes_expected(project_root: str | Path) -> bool:
    """Whether the project is known to have had a notes file: a snapshot of
    it exists (module docstring)."""
    return bool(record_files.snapshots(project_root, notes_path(project_root)))


# -- trust ----------------------------------------------------------------------------


def applied_copy(conn: Connection | None, loaded: LedgerLoad | None = None) -> dict[str, Mapping[str, Any]]:
    """The index's copy of the notes entries it applied (`judgements.
    applied_copy`'s rule: an entry still in the file unchanged is the file's
    own object)."""
    from researchos.records.judgements import entry_json  # noqa: PLC0415 -- judgements imports this module lazily

    if conn is None:
        return {}
    rows = db.applied_note_rows(conn)
    in_file: dict[str, Mapping[str, Any]] = {}
    if loaded is not None and loaded.ledger is not None:
        in_file = {e.id: e.data for e in loaded.ledger.entries}
    copy: dict[str, Mapping[str, Any]] = {}
    for entry_id, stored in rows.items():
        mine = in_file.get(entry_id)
        copy[entry_id] = mine if mine is not None and entry_json(mine) == stored else json.loads(stored)
    return copy


def _identity_now(project_root: Path) -> ProjectIdentity | None:
    got = read_identity(project_root)
    return got.identity if got.state is IdentityState.PRESENT else None


def assess(
    conn: Connection | None, project_root: str | Path, identity: ProjectIdentity | None, *, for_migration: bool = False,
) -> tuple[LedgerLoad, TrustDecision]:
    loaded = load_notes(project_root)
    decision = assess_ledger(
        identity, loaded, applied_copy(conn, loaded), for_migration=for_migration, expected=notes_expected(project_root),
    )
    return loaded, decision


def status_payload(decision: TrustDecision, loaded: LedgerLoad) -> dict[str, Any]:
    from researchos.records.judgements import _jsonable  # noqa: PLC0415

    return {
        "state": decision.verdict.value,
        "reason": decision.reason,
        "message": message_of(decision),
        "detail": decision.detail,
        "line": decision.line,
        "file_state": loaded.state.value,
        "missing": [_public(_jsonable(m)) for m in decision.missing],
        "conflict_copies": [p.name for p in loaded.conflict_copies],
    }


def apply_notes(
    conn: Connection, project_root: str | Path, *, identity: ProjectIdentity | None = None, for_migration: bool = False,
) -> TrustDecision | None:
    """Refresh the index's copy of the notes from the file, when it can be
    trusted; store the trust decision either way. The caller holds the
    project lock (a scan's end, a write, the watcher). None without an
    identity."""
    from researchos.records.judgements import entry_json  # noqa: PLC0415

    root = Path(project_root)
    if identity is None:
        identity = _identity_now(root)
        if identity is None:
            return None
    db.ensure_v8_tables(conn)
    loaded, decision = assess(conn, root, identity, for_migration=for_migration)
    db.set_record_status(conn, RECORD_STATUS_NAME, status_payload(decision, loaded))
    if decision.may_apply:
        entries = loaded.ledger.entries if loaded.ledger is not None else ()
        db.write_applied_notes(conn, {e.id: (e.seq, entry_json(e.data)) for e in entries})
    return decision


# -- writes ---------------------------------------------------------------------------


def _held_identity(project_root: Path) -> ProjectIdentity:
    identity = _identity_now(project_root)
    if identity is None:
        raise NoteRefused(
            "untrusted", f"{project_root} has no readable project identity -- nothing written",
            decision=assess_ledger(None, LedgerLoad(RecordState.ABSENT, project_root), {}),
        )
    return identity


def _open_index(identity: ProjectIdentity) -> Connection:
    path = index_db_path(identity.id)
    if not path.exists():
        raise NoteRefused("no_index", f"the index of project {identity.id} is missing ({path}); reopen the project")
    conn = db.connect(path)
    db.ensure_v8_tables(conn)
    return conn


def _refuse_untrusted(conn: Connection, loaded: LedgerLoad, decision: TrustDecision) -> None:
    db.set_record_status(conn, RECORD_STATUS_NAME, status_payload(decision, loaded))
    raise NoteRefused(
        "untrusted",
        f"the notes file may not be written now ({decision.reason}: {decision.detail or message_of(decision)}) -- nothing written",
        decision=decision,
    )


def _write(
    project_root: str | Path,
    fields: Mapping[str, Any] | Callable[[ledger_mod.Ledger], Mapping[str, Any]],
    *,
    expected_id: Any,
    timeout: float | None,
    now: Callable[[], datetime] | None,
) -> LedgerEntry:
    root = Path(project_root)
    with write_guard(root, expected_id, human=True, timeout=timeout) as held:
        identity = _held_identity(root)
        conn = _open_index(identity)
        try:
            loaded, decision = assess(conn, root, identity)
            if not decision.may_write:
                _refuse_untrusted(conn, loaded, decision)
            ledger = loaded.ledger if loaded.ledger is not None else ledger_mod.parse_ledger("", NOTES_SCHEMA)
            planned = fields(ledger) if callable(fields) else fields
            founding = loaded.state is RecordState.ABSENT
            try:
                entry = ledger_mod.append(
                    notes_path(root), NOTES_SCHEMA, planned, lock=held, project_root=root,
                    create=decision.may_create, now=now,
                )
            except LedgerWriteRefused as exc:
                raise NoteRefused("invalid", str(exc)) from exc
            if founding:
                # From now on the file is expected: a later read that finds
                # it gone refuses rather than founds a new one (9.3).
                record_files.snapshot_if_first_change_today(root, notes_path(root), now=now)
            apply_notes(conn, root, identity=identity)
            return entry
        finally:
            conn.close()


def add_note(
    project_root: str | Path,
    node: str,
    text: str,
    *,
    via: str,
    expected_id: Any = READ_NOW,
    timeout: float | None = None,
    now: Callable[[], datetime] | None = None,
) -> LedgerEntry:
    """Append a note on `node` (a node id or a project-relative path). No
    approval in any project (12.4: a note travels with `by`). Raises
    `NoteRefused`, or the write guard's errors; nothing is written then."""
    try:
        node_id = normalize_node(node)
    except ValueError as exc:
        raise NoteRefused("invalid", str(exc), message_zh="无法识别这个节点") from exc
    if not isinstance(text, str) or not text.strip():
        raise NoteRefused("invalid", "a note needs text", message_zh="笔记不能为空")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return _write(
        project_root, {"act": NOTE_ACT, "node": node_id, "text": text, "via": via},
        expected_id=expected_id, timeout=timeout, now=now,
    )


def withdraw_note(
    project_root: str | Path,
    note_id: str,
    *,
    via: str,
    expected_id: Any = READ_NOW,
    timeout: float | None = None,
    now: Callable[[], datetime] | None = None,
) -> LedgerEntry:
    """Withdraw a note (a new entry; the note itself is never touched)."""

    def plan(ledger: ledger_mod.Ledger) -> Mapping[str, Any]:
        target = ledger.by_id(note_id) if isinstance(note_id, str) else None
        if target is None or target.get("act") != NOTE_ACT:
            raise NoteRefused("no_such_note", f"no note {note_id!r} in {NOTES_FILENAME}", message_zh="找不到这条笔记")
        if any(e.get("act") == WITHDRAWN_ACT and _target_of(ledger, e) is target for e in ledger.entries):
            raise NoteRefused("already_withdrawn", f"note {note_id} is already withdrawn", message_zh="这条笔记已经撤回")
        return {"act": WITHDRAWN_ACT, "node": target.get("node"), "withdraws": target.id, "via": via}

    return _write(project_root, plan, expected_id=expected_id, timeout=timeout, now=now)


# -- the SHRUNK question --------------------------------------------------------------

ANSWER_FILE = "file"
ANSWER_RESTORE = "restore"
ANSWERS = (ANSWER_FILE, ANSWER_RESTORE)


def answer_shrunk(
    project_root: str | Path,
    answer: str,
    *,
    expected_missing: Iterable[str] | None = None,
    expected_id: Any = READ_NOW,
    timeout: float | None = None,
    now: Callable[[], datetime] | None = None,
) -> tuple[int, int]:
    """Answer 「笔记记录文件比图谱少了 N 条」: `file` forgets the missing
    entries from the index's copy (refused when the file as it is could not
    be applied afterwards); `restore` appends them again in one write, `via
    = "recovered"`, their content and attribution kept. `expected_missing`
    ties the answer to the question shown. Returns (missing, appended)."""
    if answer not in ANSWERS:
        raise NoteRefused("invalid", f"answer must be one of {', '.join(ANSWERS)}, got {answer!r}")
    root = Path(project_root)
    with write_guard(root, expected_id, human=True, timeout=timeout) as held:
        identity = _held_identity(root)
        conn = _open_index(identity)
        try:
            loaded, decision = assess(conn, root, identity)
            if decision.verdict is not Trust.SHRUNK:
                raise NoteRefused("no_question", "the notes file has not shrunk; there is nothing to answer")
            missing = decision.missing
            ids = [str(m["id"]) for m in missing]
            if expected_missing is not None and sorted(set(map(str, expected_missing))) != sorted(ids):
                raise NoteRefused("question_changed", "the notes file changed since the question was shown -- nothing written",
                                  decision=decision)
            appended: list[LedgerEntry] = []
            if answer == ANSWER_FILE:
                remaining = {k: v for k, v in applied_copy(conn, loaded).items() if k not in set(ids)}
                after = assess_ledger(identity, loaded, remaining, expected=notes_expected(root))
                if not after.may_apply:
                    raise NoteRefused(
                        "would_lose",
                        f"taking the file as it is would leave a notes file Research OS cannot apply ({after.reason}); "
                        f"the {len(ids)} missing entr(y/ies) exist only in the index's copy -- nothing changed",
                        decision=after,
                    )
                db.forget_applied_notes(conn, ids)
            else:
                batch = []
                for old in missing:
                    fields = {k: v for k, v in old.items() if k not in ("id", "seq", "at", "settles", "via")}
                    fields["via"] = "recovered"
                    fields["recovered_from"] = str(old["id"])
                    if old.get("at") is not None:
                        fields["recovered_at"] = str(old["at"])
                    batch.append(fields)
                try:
                    appended = ledger_mod.append_many(
                        notes_path(root), NOTES_SCHEMA, batch, lock=held, project_root=root,
                        create=loaded.state is RecordState.ABSENT, now=now,
                    )
                except LedgerWriteRefused as exc:
                    raise NoteRefused("invalid", f"could not restore the missing entries (nothing written): {exc}") from exc
                db.forget_applied_notes(conn, ids)
            apply_notes(conn, root, identity=identity)
            return len(ids), len(appended)
        finally:
            conn.close()


# -- readers --------------------------------------------------------------------------


def _public(data: Mapping[str, Any]) -> dict[str, Any]:
    from researchos.records.judgements import _jsonable  # noqa: PLC0415

    keep = ("id", "seq", "at", "by", "act", "node", "text", "withdraws", "via", "recovered_from")
    return {k: _jsonable(data[k]) for k in keep if k in data}


def _timeline(entries: list[Mapping[str, Any]], node: str | None, team: people.TeamRead | None) -> list[dict[str, Any]]:
    """Every note (of `node`, or all), oldest first, each with who wrote it
    and whether -- by whom, when -- it was withdrawn."""
    ledger = ledger_mod.Ledger(
        schema=NOTES_SCHEMA,
        entries=tuple(ledger_mod.LedgerEntry(data=d, index=i, line=None, key=(str(d.get("node")),)) for i, d in enumerate(entries)),
        anomalies=(),
    )
    withdrawn: dict[str, LedgerEntry] = {}
    for e in ledger.entries:
        if e.get("act") == WITHDRAWN_ACT:
            target = _target_of(ledger, e)
            if target is not None:
                withdrawn.setdefault(target.id, e)
    out = []
    for e in ledger.entries:
        if e.get("act") != NOTE_ACT or (node is not None and e.get("node") != node):
            continue
        item = _public(e.data)
        item.update(people.attribution(e.data, team))
        w = withdrawn.get(e.id)
        item["withdrawn"] = w is not None
        if w is not None:
            item["withdrawn_at"] = _public(w.data).get("at")
            item["withdrawn_by"] = people.attribution(w.data, team)
            item["withdrawal_id"] = w.id
        out.append(item)
    return out


def notes_for(project_root: str | Path, node: str | None = None, conn: Connection | None = None) -> dict[str, Any]:
    """What a reader shows: the notes on `node` (all when None) as the file
    holds them when it can be trusted; otherwise as the index last applied
    them, with `stale` set and the reason. Reads only."""
    root = Path(project_root)
    node_id = normalize_node(node) if node is not None else None
    identity = _identity_now(root)
    loaded, decision = assess(conn, root, identity) if identity is not None else (load_notes(root), None)
    team = people.read_team(root)
    if decision is None:
        trusted = loaded.state in (RecordState.PRESENT, RecordState.ABSENT) and not loaded.conflict_copies
    else:
        trusted = decision.may_apply
    stale = False
    if trusted:
        entries = [dict(e.data) for e in loaded.ledger.entries] if loaded.ledger is not None else []
    else:
        stored = applied_copy(conn, None)
        entries = sorted((dict(v) for v in stored.values()), key=lambda d: (d.get("seq") is None, d.get("seq") or 0))
        stale = True
    return {
        "node": node_id,
        "notes": _timeline(entries, node_id, team if team.state == "present" else None),
        "trusted": trusted,
        "stale": stale,
        "file_state": loaded.state.value,
        "state": decision.verdict.value if decision is not None else ("ok" if trusted else "refuse_writes"),
        "reason": decision.reason if decision is not None else None,
        "message": message_of(decision) if decision is not None else None,
        "detail": (decision.detail if decision is not None else loaded.error),
        "line": loaded.line,
        "missing": [_public(m) for m in decision.missing] if decision is not None else [],
        "conflict_copies": [p.name for p in loaded.conflict_copies],
    }


def summary(project_root: str | Path) -> dict[str, Any]:
    """For `researchos records`: counts from the file as it is."""
    loaded = load_notes(project_root)
    if loaded.ledger is None:
        return {"state": loaded.state.value, "error": loaded.error, "line": loaded.line}
    timeline = _timeline([dict(e.data) for e in loaded.ledger.entries], None, None)
    return {
        "state": "present",
        "notes": len(timeline),
        "withdrawn": sum(1 for n in timeline if n["withdrawn"]),
        "nodes": len({n["node"] for n in timeline}),
        "entries": len(loaded.ledger.entries),
    }

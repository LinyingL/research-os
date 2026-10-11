"""Approved decisions: `.researchos/decisions.toml` (DESIGN.md 12.3, 12.4;
task V8 phase 3).

An append-only ledger (9.3, the engine of `researchos.records.ledger`),
written only by the PI's Research OS when it approves a proposal:

::

    # 经 PI 批准的决定。Research OS 只追加、不改写。
    [[decision]]
    id          = "d-…"
    seq         = 4
    at          = "2026-10-11T18:02:11+02:00"
    by          = "wang@example.com"     # the member who proposed it
    act         = "abandoned"            # approved | abandoned
    approved_by = "li@example.com"       # the PI
    proposal    = "pr-…"
    kind        = "verdict_change"
    attempt     = "attempt:map.md#16"
    number      = "16"
    verdict     = "☠️ 伪协整"
    result      = "t=0.41"
    reason      = "残差不平稳，协整关系不成立"
    note        = "同意"                  # the PI's note, when given
    via         = "cli"
    evidence    = [{ path = "steps/16-build.py", role = "script", sha256 = "…", size = 812, state = "kept" }, …]

`abandoned` is 12.4's "an abandoned route keeps its basis": the result,
the reason, and the attempt's evidence frozen at the moment of approval --
the step scripts and the outputs they wrote, by fingerprint, and (text up
to 5 MB) by content in the history store. Every other approval is one
`approved` entry naming the proposal, its kind and what it changed, so the
official version keeps its own trace of who proposed what and who approved
it (12.3: every entry recorded with `by` = the member and `approved_by` =
the PI) -- also for an approval whose substance lands in a file no ledger
covers, such as a row of the researcher's attempt table.

The trust rules are the notes ledger's, minus the index's copy: a file
missing though a snapshot of it exists, in the cloud, unreadable, invalid,
or with a sync conflict copy beside it refuses every write -- and so every
approval, before anything is applied. The index keeps no copy of these
entries (nothing derived reads them back yet), so a file that lost entries
at its end is not detected here; said in the phase report.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from researchos import paths, people
from researchos.records import files as record_files
from researchos.records import ledger as ledger_mod
from researchos.records.files import RecordState
from researchos.records.identity import IdentityState, read_identity
from researchos.records.ledger import LedgerEntry, LedgerLoad, LedgerSchema, LedgerWriteRefused
from researchos.records.lock import HeldLock
from researchos.records.trust import TrustDecision, assess_ledger

DECISIONS_FILENAME = "decisions.toml"
APPROVED_ACT = "approved"
ABANDONED_ACT = "abandoned"
ACTS = frozenset({APPROVED_ACT, ABANDONED_ACT})
VIAS = frozenset({"cli", "app", "mcp"})
EVIDENCE_ROLES = frozenset({"script", "output", "file"})
EVIDENCE_STATES = frozenset({"kept", "fingerprint", "missing", "dataless", "unreadable"})
MAX_TEXT = 20_000

_HEADER = "# 经 PI 批准的决定。Research OS 只追加、不改写；放弃一条路线时，它当时的依据也记在这里。\n"

MESSAGES = {
    "no_identity": "项目身份文件无法读取",
    "migrating": "项目正在迁移，迁移完成前不能写入人工记录",
    "missing": "决定记录文件（decisions.toml）当前无法读取，请先恢复它",
    "dataless": "记录文件正在从云端下载…",
    "unreadable": "决定记录文件（decisions.toml）当前无法读取，请先恢复它",
    "invalid": "决定记录文件（decisions.toml）当前无法读取，请先恢复它",
    "empty_but_expected": "决定记录文件（decisions.toml）当前无法读取，请先恢复它",
    "conflict_copy": "决定记录文件旁有同步冲突副本，请先处理",
    "shrunk": "决定记录文件比图谱少了 {n} 条",
}


class DecisionRefused(Exception):
    """Nothing was written. `code`: `untrusted` (`decision` says why) or
    `invalid`."""

    def __init__(self, code: str, message: str, *, decision: TrustDecision | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.decision = decision

    @property
    def message_zh(self) -> str:
        if self.decision is not None and self.decision.reason is not None:
            return MESSAGES.get(self.decision.reason, MESSAGES["unreadable"]).format(n=len(self.decision.missing))
        return "决定没有记录"


def _text(entry: Mapping[str, Any], name: str, *, required: bool) -> None:
    value = entry.get(name)
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name!r} must be non-empty text, got {value!r}")
    if len(value) > MAX_TEXT:
        raise ValueError(f"{name!r} is at most {MAX_TEXT} characters")


def _validate(entry: Mapping[str, Any]) -> None:
    approved_by = entry.get("approved_by")
    if not people.is_member_id(approved_by):
        raise ValueError(f"'approved_by' must name the PI (a member id), got {approved_by!r}")
    _text(entry, "proposal", required=True)
    _text(entry, "kind", required=True)
    _text(entry, "note", required=False)
    via = entry.get("via")
    if via is not None and via not in VIAS:
        raise ValueError(f"'via' must be one of {', '.join(sorted(VIAS))}, got {via!r}")
    if entry.get("act") == ABANDONED_ACT:
        for name in ("attempt", "number", "verdict", "result", "reason"):
            _text(entry, name, required=True)
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or not all(isinstance(e, dict) for e in evidence):
            raise ValueError("an 'abandoned' decision lists its evidence as tables in 'evidence'")
        for item in evidence:
            if not isinstance(item.get("path"), str) or not item["path"]:
                raise ValueError("each piece of evidence names its 'path'")
            if item.get("role") not in EVIDENCE_ROLES:
                raise ValueError(f"evidence 'role' must be one of {', '.join(sorted(EVIDENCE_ROLES))}")
            if item.get("state") not in EVIDENCE_STATES:
                raise ValueError(f"evidence 'state' must be one of {', '.join(sorted(EVIDENCE_STATES))}")
    elif "evidence" in entry:
        raise ValueError("only an 'abandoned' decision carries 'evidence'")


def _validate_new(entry: Mapping[str, Any]) -> None:
    if entry.get("via") not in VIAS:
        raise ValueError(f"'via' must be one of {', '.join(sorted(VIAS))}, got {entry.get('via')!r}")
    if not people.is_member_id(entry.get(ledger_mod.BY_FIELD)):
        raise ValueError("a decision names the member who proposed it in 'by'")


DECISIONS_SCHEMA = LedgerSchema(
    table="decision",
    id_prefix="d-",
    act_field="act",
    acts=ACTS,
    undo_act=None,
    key_fields=(),
    field_order=(
        "id", "seq", "at", "by", "act", "approved_by", "proposal", "kind", "attempt", "number", "verdict", "result",
        "reason", "card", "version", "files", "note", "via", "evidence",
    ),
    header=_HEADER,
    validate=_validate,
    validate_new=_validate_new,
    multiline=frozenset({"decision.reason", "decision.note", "decision.result"}),
)


def decisions_path(project_root: str | Path) -> Path:
    return paths.project_dir(project_root) / DECISIONS_FILENAME


def load_decisions(project_root: str | Path) -> LedgerLoad:
    return ledger_mod.load_ledger(decisions_path(project_root), DECISIONS_SCHEMA)


def decisions_expected(project_root: str | Path) -> bool:
    """A snapshot of the file exists (one is taken when it is founded): a
    later read that finds it gone refuses rather than founds a new one."""
    return bool(record_files.snapshots(project_root, decisions_path(project_root)))


def assess(project_root: str | Path) -> tuple[LedgerLoad, TrustDecision]:
    root = Path(project_root)
    got = read_identity(root)
    identity = got.identity if got.state is IdentityState.PRESENT else None
    loaded = load_decisions(root)
    return loaded, assess_ledger(identity, loaded, {}, expected=decisions_expected(root))


def require_writable(project_root: str | Path) -> TrustDecision:
    """Raise `DecisionRefused` unless the ledger may be appended to now --
    asked by an approval before it applies anything."""
    loaded, decision = assess(project_root)
    if not decision.may_write:
        raise DecisionRefused(
            "untrusted",
            f"{DECISIONS_FILENAME} may not be written now ({decision.reason}: {decision.detail or loaded.error or ''}) "
            f"-- nothing applied",
            decision=decision,
        )
    return decision


def append_decision(
    project_root: str | Path,
    fields: Mapping[str, Any],
    *,
    lock: HeldLock,
    now: Callable[[], datetime] | None = None,
) -> LedgerEntry:
    """Append one decision (the caller holds the project lock and has
    re-checked identity). `fields` names `by` (the member) explicitly."""
    root = Path(project_root)
    loaded, decision = assess(root)
    if not decision.may_write:
        raise DecisionRefused("untrusted", f"{DECISIONS_FILENAME} may not be written now ({decision.reason})",
                              decision=decision)
    founding = loaded.state is RecordState.ABSENT
    try:
        entry = ledger_mod.append(
            decisions_path(root), DECISIONS_SCHEMA, fields, lock=lock, project_root=root,
            create=decision.may_create, now=now,
        )
    except LedgerWriteRefused as exc:
        raise DecisionRefused("invalid", str(exc)) from exc
    if founding:
        record_files.snapshot_if_first_change_today(root, decisions_path(root), now=now)
    return entry


def entries(project_root: str | Path) -> list[dict[str, Any]]:
    """Every decision as the file holds it (empty when there is none or it
    cannot be read -- `assess` says which)."""
    loaded = load_decisions(project_root)
    if loaded.ledger is None:
        return []
    return [dict(e.data) for e in loaded.ledger.entries]


def for_proposal(project_root: str | Path, proposal_id: str) -> dict[str, Any] | None:
    """The decision an approval of `proposal_id` wrote, if any (an approval
    interrupted after it is resumed without writing a second one)."""
    return next((e for e in entries(project_root) if e.get("proposal") == proposal_id), None)


def for_attempt(project_root: str | Path, attempt_node: str) -> list[dict[str, Any]]:
    """The decisions about one attempt, oldest first -- what its page shows
    as the basis an abandoned route was dropped on."""
    return [e for e in entries(project_root) if e.get("attempt") == attempt_node]


def summary(project_root: str | Path) -> dict[str, Any]:
    loaded = load_decisions(project_root)
    if loaded.ledger is None:
        return {"state": loaded.state.value, "error": loaded.error, "line": loaded.line}
    acts = [e.get("act") for e in loaded.ledger.entries]
    return {"state": "present", "entries": len(acts), "approved": acts.count(APPROVED_ACT),
            "abandoned": acts.count(ABANDONED_ACT)}

"""Drafts marked 「设为论文正文」: `.researchos/papers.toml` (DESIGN.md
12.13, task V8).

A claim is any number in a sentence, and a researcher's drafts hold
thousands -- mostly in revision logs and review notes. 图表与论断 lists claims
only from the drafts the researcher marks as the paper itself. The marks
are a record, written through the record-file discipline of 9.3 as an
append-only ledger (the engine of `researchos.records.ledger`):

::

    # 设为论文正文的文稿：成果只从这些文稿列出论断。Research OS 只追加、不改写；取消也是追加一条记录。
    [[paper]]
    id    = "pp-…"
    seq   = 1
    at    = "2026-10-12T10:02:11+02:00"
    by    = "li@example.com"
    act   = "mark"                  # or "unmark"
    draft = "paper/main.md"
    via   = "app"

A draft is marked when its newest entry is `mark`. Unmarking is an entry
too, so the file stays its own history.

**Trust** (9.3's rules, `researchos.records.trust.assess_ledger`, without
an index copy): a file missing though a snapshot of it exists, in the
cloud, unreadable, invalid, or with a sync conflict copy beside it refuses
every write; readers then list no marked draft and say why. Unlike the
notes, the index keeps no copy of these entries, so a file that shrank by
hand is not detected -- it is read as it is.

**It travels like the notes** (12.12): it is one of the record files a
snapshot carries; in a member's working copy, the member's own marks go
to their area with the next sync, as packet entries, and the PI's next
publish appends them to the official file with the member's `by`.
"""

from __future__ import annotations

import posixpath
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from researchos import paths
from researchos.records import files as record_files
from researchos.records import ledger as ledger_mod
from researchos.records.files import RecordState
from researchos.records.identity import IdentityState, ProjectIdentity, read_identity
from researchos.records.ledger import LedgerLoad, LedgerSchema, LedgerWriteRefused
from researchos.records.situation import READ_NOW, write_guard
from researchos.records.trust import TrustDecision, assess_ledger

PAPERS_FILENAME = "papers.toml"
MARK, UNMARK = "mark", "unmark"
ACTS = frozenset({MARK, UNMARK})
VIAS = frozenset({"cli", "app", "recovered"})
DRAFT_SUFFIXES = (".md", ".markdown", ".tex", ".qmd", ".rmd", ".txt", ".docx")

# Written to the file as it always was: the header keeps the old name of the page, 成果 (DESIGN.md 18.2 renames
# the page 图表与论断, not what is on disk).
_HEADER = "# 设为论文正文的文稿：成果只从这些文稿列出论断。Research OS 只追加、不改写；取消也是追加一条记录。\n"

MESSAGES = {
    "no_identity": "项目身份文件无法读取",
    "migrating": "项目正在迁移，迁移完成前不能写入人工记录",
    "missing": "论文正文记录文件（papers.toml）当前无法读取，请先恢复它",
    "dataless": "记录文件正在从云端下载…",
    "unreadable": "论文正文记录文件（papers.toml）当前无法读取，请先恢复它",
    "invalid": "论文正文记录文件（papers.toml）格式不对，请先修复它",
    "empty_but_expected": "论文正文记录文件（papers.toml）当前无法读取，请先恢复它",
    "conflict_copy": "论文正文记录文件旁有同步冲突副本，请先处理",
}


class PaperRefused(Exception):
    """Nothing was written. `code`: `untrusted`, `invalid`, `no_such_draft`."""

    def __init__(self, code: str, message: str, message_zh: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message_zh = message_zh


def clean_draft(value: Any) -> str:
    """A draft named as a project-relative POSIX path (normalised; never
    absolute, never above the project, never inside `.researchos/`)."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value or "\n" in value:
        raise ValueError("a draft is named by its project-relative path")
    rel = posixpath.normpath(value.strip().replace("\\", "/"))
    if rel.startswith("/") or rel == "." or rel == ".." or rel.startswith("../"):
        raise ValueError(f"{value!r} is not a path inside the project")
    if rel.split("/", 1)[0] in (paths.PROJECT_DIRNAME, paths.LEGACY_PROJECT_DIRNAME):
        raise ValueError(f"{value!r} is a record file, not a draft")
    return rel


def _validate(entry: Mapping[str, Any]) -> None:
    clean_draft(entry.get("draft"))
    via = entry.get("via")
    if via is not None and via not in VIAS:
        raise ValueError(f"'via' must be one of {', '.join(sorted(VIAS))}, got {via!r}")


def _validate_new(entry: Mapping[str, Any]) -> None:
    if entry.get("via") not in VIAS:
        raise ValueError(f"'via' must be one of {', '.join(sorted(VIAS))}, got {entry.get('via')!r}")


PAPERS_SCHEMA = LedgerSchema(
    table="paper",
    id_prefix="pp-",
    act_field="act",
    acts=ACTS,
    undo_act=None,
    key_fields=("draft",),
    field_order=("id", "seq", "at", "by", "act", "draft", "via", "recovered_from", "recovered_at"),
    header=_HEADER,
    validate=_validate,
    validate_new=_validate_new,
)


def papers_path(project_root: str | Path) -> Path:
    return paths.project_dir(project_root) / PAPERS_FILENAME


def load(project_root: str | Path) -> LedgerLoad:
    return ledger_mod.load_ledger(papers_path(project_root), PAPERS_SCHEMA)


def expected(project_root: str | Path) -> bool:
    """Whether the project is known to have had the file: a snapshot of it
    exists under `.researchos/backups/`."""
    return bool(record_files.snapshots(project_root, papers_path(project_root)))


def _identity_now(project_root: Path) -> ProjectIdentity | None:
    got = read_identity(project_root)
    return got.identity if got.state is IdentityState.PRESENT else None


def assess(project_root: str | Path, identity: ProjectIdentity | None = None) -> tuple[LedgerLoad, TrustDecision]:
    root = Path(project_root)
    loaded = load(root)
    decision = assess_ledger(identity if identity is not None else _identity_now(root), loaded, {},
                             expected=expected(root))
    return loaded, decision


def message_of(decision: TrustDecision) -> str | None:
    if decision.reason is None:
        return None
    return MESSAGES.get(decision.reason, MESSAGES["unreadable"])


def marked_from(loaded: LedgerLoad) -> list[str]:
    """The drafts whose newest entry is `mark`, in the order first marked."""
    if loaded.ledger is None:
        return []
    state: dict[str, str] = {}
    for e in loaded.ledger.entries:
        draft = str(e.get("draft"))
        state.pop(draft, None)
        state[draft] = str(e.get("act"))
    return [d for d, act in state.items() if act == MARK]


def marked(project_root: str | Path) -> dict[str, Any]:
    """What 图表与论断 reads: the marked drafts, and -- when the file cannot be
    trusted now -- why none is listed (`message`)."""
    loaded, decision = assess(project_root)
    out: dict[str, Any] = {"drafts": [], "state": loaded.state.value, "trusted": decision.may_apply,
                           "message": None, "path": f"{paths.PROJECT_DIRNAME}/{PAPERS_FILENAME}"}
    if not decision.may_apply:
        out["message"] = message_of(decision)
        return out
    out["drafts"] = marked_from(loaded)
    return out


def set_marked(project_root: str | Path, draft: str, mark: bool, *, via: str, expected_id: Any = READ_NOW,
               timeout: float | None = None, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """「设为论文正文」 (`mark`) or 「不再作为论文正文」: one entry appended,
    under the project lock, with the write-time identity re-check (9.4) --
    nothing when the draft already stands so. The draft must be a file of
    the project."""
    root = Path(project_root)
    try:
        rel = clean_draft(draft)
    except ValueError as exc:
        raise PaperRefused("invalid", str(exc), "无法识别这份文稿") from exc
    with write_guard(root, expected_id, human=True, timeout=timeout) as held:
        target = root / rel
        try:
            inside = target.resolve().is_relative_to(root.resolve())
        except OSError:
            inside = False
        if mark and (not inside or not target.is_file()):
            raise PaperRefused("no_such_draft", f"{rel} is not a file of the project", "找不到这份文稿")
        loaded, decision = assess(root)
        if not decision.may_write:
            raise PaperRefused("untrusted", f"{PAPERS_FILENAME} may not be written now ({decision.reason}); nothing "
                                            f"written", message_of(decision))
        now_marked = rel in marked_from(loaded)
        if now_marked == mark:
            return {"changed": False, "draft": rel, "marked": mark, "drafts": marked_from(loaded)}
        founding = loaded.state is RecordState.ABSENT
        try:
            entry = ledger_mod.append(papers_path(root), PAPERS_SCHEMA, {"act": MARK if mark else UNMARK,
                                                                          "draft": rel, "via": via},
                                      lock=held, project_root=root, create=decision.may_create, now=now)
        except LedgerWriteRefused as exc:
            raise PaperRefused("invalid", str(exc), "论文正文记录没有写入") from exc
        if founding:
            # From now on the file is expected: a later read that finds it
            # gone refuses rather than founds a new one (9.3).
            record_files.snapshot_if_first_change_today(root, papers_path(root), now=now)
        return {"changed": True, "draft": rel, "marked": mark, "entry": entry.id,
                "drafts": marked_from(load(root))}

"""Drafts marked 「设为论文正文」: `.researchos/papers.toml` (DESIGN.md
12.13, task V8).

A claim is any number in a sentence, and a researcher's drafts hold
thousands -- mostly in revision logs and review notes. 图表与论断 lists claims
only from the drafts the researcher marks as the paper itself. The marks
are a record, written through the record-file discipline of 9.3 as an
append-only ledger (the engine of `researchos.records.ledger`):

::

    # 设为论文正文的文稿，和不属于论文的图表与表：成果只从设为论文正文的文稿列出论断。Research OS 只追加、不改写；取消、放回也是追加一条记录。
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

**「不属于论文」** (DESIGN.md 21.5, task V16 B3-2) is kept in the same file, in a table of
its own. A figure or table that is not part of the paper -- a by-product, a
.svg copy of a plot -- is put out with an entry of its own and put back with
another, both naming the file in `file`:

::

    [[paper_file]]
    id    = "pf-…"
    seq   = 1
    at    = "2026-10-30T10:02:11+02:00"
    by    = "li@example.com"
    act   = "not_in_paper"          # or "back_in_paper" (「放回论文」)
    file  = "00_Current/figs/hand.svg"
    via   = "app"

A file is out of the paper when its newest such entry is `not_in_paper`. The
two tables do not touch each other: a draft is never put out, a figure is never
marked. Nothing on disk is moved.

Why a table of its own: a Research OS from before the acts read `[[paper]]` and
refuse the whole file at an act they do not know (the ledger's strictness, 9.3),
which would have emptied the marked drafts on a member's older install, or on a
second machine not yet updated. `parse_ledger` reads only its own table, so a
reader that knows one passes over the other, as `mappings.toml` carries
`[[removal]]` and `[[received]]` beside `[[mapping]]`. Each table is its own
ledger -- ids (`pp-`, `pf-`) and numbers (`seq`) are counted in it -- and the
file is one for trust: either table unreadable or invalid refuses every write.

**Trust** (9.3's rules, `researchos.records.trust.assess_ledger`, without
an index copy): a file missing though a snapshot of it exists, in the
cloud, unreadable, invalid, or with a sync conflict copy beside it refuses
every write; readers then list no marked draft and say why. Unlike the
notes, the index keeps no copy of these entries, so a file that shrank by
hand is not detected -- it is read as it is.

**It travels like the notes** (12.12): it is one of the record files a
snapshot carries; in a member's working copy, the member's own marks --
「不属于论文」 among them -- go to their area with the next sync, as packet
entries, and the PI's next publish appends them to the official file with the
member's `by`. Neither waits for approval (12.4: a note, a judgment and these
marks are not proposals).
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
#: 「不属于论文」 and 「放回论文」 (21.5): the acts about a figure or table, named by `file`, in the table `paper_file`.
NOT_IN_PAPER, BACK_IN_PAPER = "not_in_paper", "back_in_paper"
ACTS = frozenset({MARK, UNMARK})
FILE_ACTS = frozenset({NOT_IN_PAPER, BACK_IN_PAPER})
VIAS = frozenset({"cli", "app", "recovered"})
DRAFT_SUFFIXES = (".md", ".markdown", ".tex", ".qmd", ".rmd", ".txt", ".docx")

# Written to the file as it always was: the header keeps the old name of the page, 成果 (DESIGN.md 18.2 renames
# the page 图表与论断, not what is on disk).
_HEADER = "# 设为论文正文的文稿，和不属于论文的图表与表：成果只从设为论文正文的文稿列出论断。Research OS 只追加、不改写；取消、放回也是追加一条记录。\n"

MESSAGES = {
    "no_identity": "项目身份文件无法读取",
    "migrating": "项目正在迁移，迁移完成前不能写入你的记录",
    "missing": "论文正文记录文件（papers.toml）当前无法读取，请先恢复它",
    "dataless": "记录文件正在从云端下载…",
    "unreadable": "论文正文记录文件（papers.toml）当前无法读取，请先恢复它",
    "invalid": "论文正文记录文件（papers.toml）格式不对，请先修复它",
    "empty_but_expected": "论文正文记录文件（papers.toml）当前无法读取，请先恢复它",
    "conflict_copy": "论文正文记录文件旁有同步冲突副本，请先处理",
}


class PaperRefused(Exception):
    """Nothing was written. `code`: `untrusted`, `invalid`, `no_such_draft`, `no_such_file`."""

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


def _validate_file(entry: Mapping[str, Any]) -> None:
    clean_draft(entry.get("file"))
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
    key_fields=(),  # the entries name a draft; the state of each is replayed in file order (`marked_from`)
    field_order=("id", "seq", "at", "by", "act", "draft", "via", "recovered_from", "recovered_at"),
    header=_HEADER,
    validate=_validate,
    validate_new=_validate_new,
)

#: The files put out of the paper (21.5): the second table of the same file (module docstring).
FILES_SCHEMA = LedgerSchema(
    table="paper_file",
    id_prefix="pf-",
    act_field="act",
    acts=FILE_ACTS,
    undo_act=None,
    key_fields=(),  # the entries name a file; the state of each is replayed in file order (`excluded_from`)
    field_order=("id", "seq", "at", "by", "act", "file", "via", "recovered_from", "recovered_at"),
    header=_HEADER,
    validate=_validate_file,
    validate_new=_validate_new,
)


def papers_path(project_root: str | Path) -> Path:
    return paths.project_dir(project_root) / PAPERS_FILENAME


def load(project_root: str | Path) -> LedgerLoad:
    """The drafts' table of the file (`[[paper]]`)."""
    return ledger_mod.load_ledger(papers_path(project_root), PAPERS_SCHEMA)


def load_files(project_root: str | Path) -> LedgerLoad:
    """The files' table of the same file (`[[paper_file]]`)."""
    return ledger_mod.load_ledger(papers_path(project_root), FILES_SCHEMA)


def expected(project_root: str | Path) -> bool:
    """Whether the project is known to have had the file: a snapshot of it
    exists under `.researchos/backups/`."""
    return bool(record_files.snapshots(project_root, papers_path(project_root)))


def _identity_now(project_root: Path) -> ProjectIdentity | None:
    got = read_identity(project_root)
    return got.identity if got.state is IdentityState.PRESENT else None


def _judged(drafts: LedgerLoad, files: LedgerLoad) -> LedgerLoad:
    """The load trust judges: the file is one, so the first table that does not read is the one that refuses, and the
    file holds no entries only when neither table does."""
    if drafts.state is not RecordState.PRESENT:
        return drafts
    if files.state is not RecordState.PRESENT:
        return files
    return drafts if drafts.ledger.entries else files


def _assess(project_root: str | Path, identity: ProjectIdentity | None) -> tuple[LedgerLoad, LedgerLoad, TrustDecision]:
    root = Path(project_root)
    drafts, files = load(root), load_files(root)
    decision = assess_ledger(identity if identity is not None else _identity_now(root), _judged(drafts, files), {},
                             expected=expected(root))
    return drafts, files, decision


def assess(project_root: str | Path, identity: ProjectIdentity | None = None) -> tuple[LedgerLoad, TrustDecision]:
    """The drafts' table as loaded, and whether the file may be written and obeyed (both tables: module docstring)."""
    drafts, _files, decision = _assess(project_root, identity)
    return drafts, decision


def assess_files(project_root: str | Path, identity: ProjectIdentity | None = None) -> tuple[LedgerLoad, TrustDecision]:
    """`assess`, with the files' table as loaded."""
    _drafts, files, decision = _assess(project_root, identity)
    return files, decision


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


def excluded_from(loaded: LedgerLoad) -> list[dict[str, Any]]:
    """The files whose newest entry (the files' table, `load_files`) is `not_in_paper` (21.5), in the order first put
    out, each as `{file, by, at}`: who put it out and when -- the entry's own `by` (none for the project's first
    researcher) and `at`."""
    if loaded.ledger is None:
        return []
    state: dict[str, Any] = {}
    for e in loaded.ledger.entries:
        file = str(e.get("file"))
        state.pop(file, None)
        state[file] = e
    return [{"file": file, "by": e.get("by"), "at": e.at} for file, e in state.items() if e.get("act") == NOT_IN_PAPER]


def marked(project_root: str | Path) -> dict[str, Any]:
    """What 图表与论断 reads: the marked drafts, the figures and tables put out of the paper (`not_in_paper`, see
    `excluded_from`), and -- when the file cannot be trusted now -- why none is listed (`message`)."""
    drafts, files, decision = _assess(project_root, None)
    out: dict[str, Any] = {"drafts": [], "not_in_paper": [], "state": drafts.state.value, "trusted": decision.may_apply,
                           "message": None, "path": f"{paths.PROJECT_DIRNAME}/{PAPERS_FILENAME}"}
    if not decision.may_apply:
        out["message"] = message_of(decision)
        return out
    out["drafts"] = marked_from(drafts)
    out["not_in_paper"] = excluded_from(files)
    return out


def _writable(root: Path) -> tuple[LedgerLoad, LedgerLoad, TrustDecision]:
    """The file as it stands (both tables), or `PaperRefused` (`untrusted`) when it may not be written now."""
    drafts, files, decision = _assess(root, None)
    if not decision.may_write:
        raise PaperRefused("untrusted", f"{PAPERS_FILENAME} may not be written now ({decision.reason}); nothing "
                                        f"written", message_of(decision))
    return drafts, files, decision


def _appended(root: Path, held: Any, loaded: LedgerLoad, decision: TrustDecision, schema: LedgerSchema,
              fields: dict[str, Any], now: Callable[[], datetime] | None) -> Any:
    """One entry appended to the table `schema` of the file under the lock `held`; a file this founds is snapshotted,
    so that from now on it is expected: a later read that finds it gone refuses rather than founds a new one (9.3)."""
    founding = loaded.state is RecordState.ABSENT
    try:
        entry = ledger_mod.append(papers_path(root), schema, fields, lock=held, project_root=root,
                                  create=decision.may_create, now=now)
    except LedgerWriteRefused as exc:
        raise PaperRefused("invalid", str(exc), "论文正文记录没有写入") from exc
    if founding:
        record_files.snapshot_if_first_change_today(root, papers_path(root), now=now)
    return entry


def _in_project(root: Path, rel: str) -> bool:
    """Whether `rel` is a file of the project: there, and not a link out of it."""
    target = root / rel
    try:
        return target.resolve().is_relative_to(root.resolve()) and target.is_file()
    except OSError:
        return False


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
        if mark and not _in_project(root, rel):
            raise PaperRefused("no_such_draft", f"{rel} is not a file of the project", "找不到这份文稿")
        loaded, _files, decision = _writable(root)
        now_marked = rel in marked_from(loaded)
        if now_marked == mark:
            return {"changed": False, "draft": rel, "marked": mark, "drafts": marked_from(loaded)}
        entry = _appended(root, held, loaded, decision, PAPERS_SCHEMA,
                          {"act": MARK if mark else UNMARK, "draft": rel, "via": via}, now)
        return {"changed": True, "draft": rel, "marked": mark, "entry": entry.id, "drafts": marked_from(load(root))}


def set_not_in_paper(project_root: str | Path, file: str, out: bool, *, via: str, expected_id: Any = READ_NOW,
                     timeout: float | None = None, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """「不属于论文」 (`out`) or 「放回论文」 for a figure or table (21.5): one entry appended, as `set_marked` appends
    one -- under the project lock, with the identity re-check, nothing when the file already stands so. The file must
    be a file of the project when it is put out; it may be gone when it is put back. Nothing is moved."""
    root = Path(project_root)
    try:
        rel = clean_draft(file)
    except ValueError as exc:
        raise PaperRefused("invalid", str(exc), "无法识别这个文件") from exc
    with write_guard(root, expected_id, human=True, timeout=timeout) as held:
        if out and not _in_project(root, rel):
            raise PaperRefused("no_such_file", f"{rel} is not a file of the project", "找不到这个文件")
        drafts, loaded, decision = _writable(root)
        files = [e["file"] for e in excluded_from(loaded)]
        if (rel in files) == out:
            return {"changed": False, "file": rel, "not_in_paper": out, "files": files}
        entry = _appended(root, held, drafts, decision, FILES_SCHEMA,
                          {"act": NOT_IN_PAPER if out else BACK_IN_PAPER, "file": rel, "via": via}, now)
        return {"changed": True, "file": rel, "not_in_paper": out, "entry": entry.id,
                "files": [e["file"] for e in excluded_from(load_files(root))]}

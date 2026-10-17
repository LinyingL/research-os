"""Which project folder a Stata global stands for: `.researchos/stata.toml`
(DESIGN.md 15.3, task V11).

Paths in a replication package are built on a global the package leaves
for each user to set (`$MyProject`, undefined) or hard-codes as one
author's root (`global github "C:\\Users\\..."`). Research OS proposes the
project folder such a global stands for (`researchos.ingest.stata_folders`)
and a person answers once, 「是」 or 「不是」. The answers are a record,
written through the record-file discipline of 9.3 as an append-only ledger
(`researchos.records.ledger`):

::

    # Stata 全局变量代表项目里的哪个文件夹：Research OS 提议，你回答。Research OS 只追加、不改写；同一个全局变量以最新的回答为准。
    [[global]]
    id     = "sg-…"
    seq    = 1
    at     = "2026-10-17T10:02:11+02:00"
    by     = "li@example.com"
    global = "MyProject"
    answer = "yes"             # or "no", with folder = "none"
    folder = "analysis"        # "." is the project folder itself
    via    = "app"

The newest answer for a global holds (a key whose history two machines
forked holds none until a new answer settles it). A `yes` makes every path
built on the global resolve against that folder, relative to the project
folder (`researchos.ingest.stata`'s `anchors`); a `no` leaves its paths
unresolved, listed with their reason.

**Who answers** (12.1, 15.3): in a project with members only the PI; a
single researcher is their own PI. A member sees the proposal and cannot
answer (`StataRefused("not_pi")`).

**Trust** (9.3, `researchos.records.trust.assess_ledger`, without an index
copy, as for `papers.toml`): a file missing though a snapshot of it exists,
in the cloud, unreadable, invalid, or with a sync conflict copy beside it
refuses every write -- and is never built upon: the scan then reports the
project's do-files unreadable, so their links keep the state they had
(`anchors`).

**It travels in snapshots** (12.2): it is one of the record files a
snapshot carries; a member's copy receives the PI's answers with the next
sync and never writes the file itself.
"""

from __future__ import annotations

import posixpath
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from researchos import paths, settings
from researchos.records import files as record_files
from researchos.records import ledger as ledger_mod
from researchos.records.files import RecordState
from researchos.records.identity import IdentityState, ProjectIdentity, read_identity
from researchos.records.ledger import LedgerLoad, LedgerSchema, LedgerWriteRefused
from researchos.records.situation import READ_NOW, write_guard
from researchos.records.trust import TrustDecision, assess_ledger

STATA_FILENAME = "stata.toml"
YES, NO = "yes", "no"
NONE_FOLDER = "none"  # the folder of a `no`
ROOT_FOLDER = "."  # the project folder itself
VIAS = frozenset({"app", "cli"})
#: Who may answer (15.3): the PI, or a single researcher.
ANSWERING_ROLES = frozenset({"pi", "single"})

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")  # a Stata name: 32 characters at most
_HEADER = ("# Stata 全局变量代表项目里的哪个文件夹：Research OS 提议，你回答。Research OS 只追加、不改写；"
           "同一个全局变量以最新的回答为准。\n")

MESSAGES = {
    "no_identity": ("项目身份文件无法读取", "The project's identity file cannot be read"),
    "migrating": ("项目正在迁移，迁移完成前不能写入人工记录", "The project is being migrated; no record is written until it ends"),
    "missing": ("全局变量记录文件（stata.toml）当前无法读取，请先恢复它",
                "The record of globals (stata.toml) cannot be read now; restore it first"),
    "dataless": ("记录文件正在从云端下载…", "The record file is downloading from the cloud…"),
    "invalid": ("全局变量记录文件（stata.toml）格式不对，请先修复它",
                "The record of globals (stata.toml) is not in the right form; fix it first"),
    "conflict_copy": ("全局变量记录文件旁有同步冲突副本，请先处理",
                      "A sync conflict copy sits beside the record of globals; deal with it first"),
    "not_pi": ("只有 PI 可以回答全局变量指向哪个项目里的文件夹；成员只能查看",
               "Only the PI answers which project folder a global stands for; members can only look"),
    "not_in_team": ("你还不是这个项目的成员，不能回答", "You are not in this project's team yet, so you cannot answer"),
    "team_unreadable": ("成员名单（team.toml）当前无法读取，无法判断谁是 PI",
                        "The member list (team.toml) cannot be read now, so who the PI is cannot be told"),
    "no_such_folder": ("项目里没有这个文件夹", "There is no such folder in the project"),
    "invalid_answer": ("无法识别这个回答", "This answer cannot be read"),
    "not_written": ("这个回答没有写入", "The answer was not written"),
}
_ROLE_REFUSAL = {"member": "not_pi", "none": "not_in_team", "unknown": "team_unreadable"}


class StataRefused(Exception):
    """Nothing was written. `code`: a key of `MESSAGES` (`untrusted` uses
    the trust reason's sentence); `message_zh` / `message_en` the page's."""

    def __init__(self, code: str, message: str, words: tuple[str, str]) -> None:
        super().__init__(message)
        self.code = code
        self.message_zh, self.message_en = words

    def say(self, lang: str) -> str:
        return settings.say(self.message_zh, self.message_en, lang)


def clean_global(value: Any) -> str:
    """A global's name, without `$` or braces: a Stata name."""
    if not isinstance(value, str) or not _NAME_RE.match(value):
        raise ValueError(f"{value!r} is not the name of a Stata global")
    return value


def clean_folder(value: Any) -> str:
    """A project folder as the record names it: "." or a normalised
    project-relative POSIX path (never absolute, never above the project,
    never inside `.researchos/`)."""
    if not isinstance(value, str) or not value or "\\" in value or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{value!r} is not a project folder")
    rel = posixpath.normpath(value)
    if rel != value.rstrip("/") or rel.startswith("/") or rel == ".." or rel.startswith("../") or re.match(r"^[A-Za-z]:", rel):
        raise ValueError(f"{value!r} is not a folder inside the project")
    if rel.split("/", 1)[0] in (paths.PROJECT_DIRNAME, paths.LEGACY_PROJECT_DIRNAME):
        raise ValueError(f"{value!r} is the record folder, not a project folder")
    return rel


def _validate(entry: Mapping[str, Any]) -> None:
    clean_global(entry.get("global"))
    folder = entry.get("folder")
    if entry.get("answer") == NO:
        if folder != NONE_FOLDER:
            raise ValueError(f"a 'no' answer has folder = {NONE_FOLDER!r}, got {folder!r}")
    else:
        clean_folder(folder)
    via = entry.get("via")
    if via is not None and via not in VIAS:
        raise ValueError(f"'via' must be one of {', '.join(sorted(VIAS))}, got {via!r}")


def _validate_new(entry: Mapping[str, Any]) -> None:
    if entry.get("via") not in VIAS:
        raise ValueError(f"'via' must be one of {', '.join(sorted(VIAS))}, got {entry.get('via')!r}")


STATA_SCHEMA = LedgerSchema(
    table="global",
    id_prefix="sg-",
    act_field="answer",
    acts=frozenset({YES, NO}),
    undo_act=None,
    key_fields=("global",),
    field_order=("id", "seq", "at", "by", "global", "answer", "folder", "via"),
    header=_HEADER,
    validate=_validate,
    validate_new=_validate_new,
)


def stata_path(project_root: str | Path) -> Path:
    return paths.project_dir(project_root) / STATA_FILENAME


def load(project_root: str | Path) -> LedgerLoad:
    return ledger_mod.load_ledger(stata_path(project_root), STATA_SCHEMA)


def expected(project_root: str | Path) -> bool:
    """Whether the project is known to have had the file: a snapshot of it
    exists under `.researchos/backups/`."""
    return bool(record_files.snapshots(project_root, stata_path(project_root)))


def assess(project_root: str | Path, identity: ProjectIdentity | None = None) -> tuple[LedgerLoad, TrustDecision]:
    root = Path(project_root)
    loaded = load(root)
    if identity is None:
        got = read_identity(root)
        identity = got.identity if got.state is IdentityState.PRESENT else None
    return loaded, assess_ledger(identity, loaded, {}, expected=expected(root))


def message_of(decision: TrustDecision) -> tuple[str, str] | None:
    if decision.reason is None:
        return None
    return MESSAGES.get(decision.reason, MESSAGES["missing"])


def answers_from(loaded: LedgerLoad) -> dict[str, dict[str, Any]]:
    """global -> its newest answer (the entry's fields), for every global
    whose history is not in conflict."""
    if loaded.ledger is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key in loaded.ledger.keys():
        state = loaded.ledger.state(key)
        if state.entry is not None:
            out[key[0]] = dict(state.entry.data)
    return out


def anchors(project_root: str | Path) -> tuple[dict[str, str] | None, str | None]:
    """What a scan expands globals with: ({global: folder} for every `yes`,
    None) -- or (None, the trust reason) when the file cannot be trusted
    now, and then nothing may be built on it (module docstring). A file
    that does not exist (and is not expected) holds no answer."""
    root = Path(project_root)
    if not stata_path(root).exists() and not stata_path(root).is_symlink() and not expected(root):
        return {}, None
    loaded, decision = assess(root)
    if not decision.may_apply:
        return None, decision.reason or "unreadable"
    return {name: e["folder"] for name, e in answers_from(loaded).items() if e.get("answer") == YES}, None


def role(project_root: str | Path) -> str:
    """"pi" | "single" | "member" | "none" | "unknown" (`proposals.role_of`)."""
    from researchos import proposals  # noqa: PLC0415 -- proposals imports the records

    return proposals.role_of(project_root)[0]


def refusal_for(role_name: str) -> str | None:
    """The refusal code for a person of this role, None when they answer."""
    return None if role_name in ANSWERING_ROLES else _ROLE_REFUSAL.get(role_name, "team_unreadable")


def answer(project_root: str | Path, name: str, folder: str | None, *, via: str, expected_id: Any = READ_NOW,
           timeout: float | None = None, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """「是」 (`folder` names it) or 「不是」 (`folder` None): one entry appended,
    under the project lock with the write-time identity re-check (9.4) --
    nothing when the global's newest answer already says so. Refused for a
    member (module docstring), for a folder that is not a folder of the
    project, and while the file cannot be trusted."""
    root = Path(project_root)
    try:
        name = clean_global(name)
        rel = None if folder is None else clean_folder(folder)
    except ValueError as exc:
        raise StataRefused("invalid_answer", str(exc), MESSAGES["invalid_answer"]) from exc
    refused = refusal_for(role(root))
    if refused is not None:
        raise StataRefused(refused, f"{refused}: nothing written", MESSAGES[refused])
    with write_guard(root, expected_id, human=True, timeout=timeout) as held:
        if rel is not None:
            target = root / rel
            try:
                inside = target.resolve().is_relative_to(root.resolve())
            except OSError:
                inside = False
            if not inside or not target.is_dir():
                raise StataRefused("no_such_folder", f"{rel} is not a folder of the project", MESSAGES["no_such_folder"])
        loaded, decision = assess(root)
        if not decision.may_write:
            raise StataRefused("untrusted", f"{STATA_FILENAME} may not be written now ({decision.reason}); nothing "
                                            f"written", message_of(decision) or MESSAGES["missing"])
        fields = {"global": name, "answer": NO if rel is None else YES, "folder": NONE_FOLDER if rel is None else rel,
                  "via": via}
        current = answers_from(loaded).get(name)
        if current is not None and (current.get("answer"), current.get("folder")) == (fields["answer"], fields["folder"]):
            return {"changed": False, **fields}
        founding = loaded.state is RecordState.ABSENT
        try:
            entry = ledger_mod.append(stata_path(root), STATA_SCHEMA, fields, lock=held, project_root=root,
                                      create=decision.may_create, now=now)
        except LedgerWriteRefused as exc:
            raise StataRefused("not_written", str(exc), MESSAGES["not_written"]) from exc
        if founding:
            # From now on the file is expected: a later read that finds it
            # gone refuses rather than founds a new one (9.3).
            record_files.snapshot_if_first_change_today(root, stata_path(root), now=now)
        return {"changed": True, "entry": entry.id, **fields}

"""Project output locations and the files reported by agent tasks.

Both records are ordinary project files. Registration checks paths and formats;
it never moves files, executes analysis, or confirms a research judgment.

The eighth location, `work` (DESIGN.md 19.2), is where an agent puts everything
it writes only to carry out a task. It is never declared: `validate` refuses it,
and `filing` (19.4) is how the app tells, after a run, which of the files the run
created are records, outputs, process files, caches or none of these. It stands
apart from the other seven (`_apart`); when nothing usable is left to name it,
the settings still read and `choose` says so (`folders`).

A person registers a file for a task's result record, and takes a registration away, in the same catalog an agent's
declaration goes to (DESIGN.md 19.5: `register_files`, `unregister_file`); the task rows and the views read the catalogs
back with the task each entry belongs to (19.6: `task_outputs`, `catalog`). The files that runs before this one created
and nobody registered are listed for the person to review, each with a suggested kind (19.5: `backfill`).
"""
from __future__ import annotations

import fnmatch
import hashlib
import csv
import io
import itertools
import json
import os
import posixpath
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from researchos import people, workspace
from researchos.agents import guard, runs, snapshot
from researchos.ingest import files as ingest_files
from researchos.records import situation

FOLDERS_PATH = "research/folders.json"
CATALOG_PATH = "research/results/*.outputs.json"
LIMIT = 1000
ROLES = {
    "code": ("code", "scripts", "src"),
    "data": ("data/derived", "data/processed", "data"),
    "figure": ("figures", "outputs/figures", "plots"),
    "table": ("tables", "outputs/tables"),
    "literature": ("literature", "references", "bibliography"),
    "document": ("reports", "manuscript", "paper"),
    "variable": ("research/variables", "variables"),
    "work": (ingest_files.WORK_FOLDER, "agent-work"),
}
SUFFIXES = {
    "code": {".py", ".r", ".rmd", ".do", ".ado", ".ipynb", ".jl", ".m", ".sh", ".sql"},
    "data": set(snapshot.DATA_SUFFIXES),
    "figure": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf"},
    "table": {".csv", ".tsv", ".xlsx", ".xls", ".tex", ".md", ".html"},
    "literature": {".bib", ".csv", ".json", ".pdf"},
    "document": {".md", ".txt", ".tex", ".docx", ".pdf", ".html"},
    "variable": {".toml"},
}
#: What an agent's declaration can never name, whatever a location says (DESIGN.md 19.2): the notes, the result
#: records and the app's own record files.
RECORD_FOLDERS = ("research/notes", "research/results")
RECORD_FILES = (FOLDERS_PATH, workspace.TASKS_PATH, workspace.OVERVIEW_PATH)
#: What a run, and the page, say while the work location has no usable name (DESIGN.md 19.2): English, Chinese.
CHOOSE_WORK = ("Choose a folder for process files.", "请为过程文件选一个目录。")
#: The kinds as the page says them (workspace.js `outputKinds`), for the refusals that name one (DESIGN.md 19.5).
KIND_ZH = {"code": "代码", "data": "数据", "figure": "图", "table": "表", "literature": "文献", "document": "文稿与报告",
           "variable": "变量草稿", "work": "过程文件"}
#: The longest first line of a task a slug is made from (19.2).
SLUG_LIMIT = 24
#: A task's first line as a heading or a label is cut here, at a word boundary where there is one (19.6).
TITLE_CUT = 80
#: How many files of a work folder are counted for the task row (19.6); past it the row says "more than".
WORK_COUNT_LIMIT = 5000
#: And how many entries, folders included, the count looks at, as a multiple of that limit.
WORK_VISIT_FACTOR = 4
#: Filing (19.4): the classes a created file is sorted into, and how many unregistered paths a run keeps.
FILING_CLASSES = ("record", "output", "work", "cache", "unregistered")
FILING_KEPT = 500
#: Folders whose files are caches (19.4): the ones 17.5 already does not walk (git's own state aside), and what Python,
#: pytest and ruff leave behind.
CACHE_FOLDERS = frozenset(snapshot.SKIP_DIRS - {snapshot.GIT_DIR}) | {"__pycache__", ".pytest_cache", ".ruff_cache"}


def _bad(message: str, zh: str = "输出目录或文件不符合设置，请检查后重试") -> workspace.WorkspaceError:
    return workspace.WorkspaceError("output", message, 409, zh)


def _path(root: Path, rel: Any) -> Path:
    if (not isinstance(rel, str) or not rel or len(rel) > 1024 or "\\" in rel
            or posixpath.isabs(rel) or posixpath.normpath(rel) != rel
            or any(ord(c) < 32 or ord(c) == 127 for c in rel)
            or any(p.startswith(".") for p in rel.split("/"))
            or any(p in {"node_modules", "__pycache__", "venv", "env"} for p in rel.split("/"))):
        raise _bad("Use a visible project-relative path, without '..', hidden folders or dependency folders",
                   "请填写项目内的相对路径，例如 figures；不要使用 ..、隐藏目录或依赖目录。")
    try:
        return workspace._confined(root, rel)
    except workspace.WorkspaceError as exc:
        raise _bad(str(exc), f"“{rel}”不能用于输出，请检查路径、符号链接和本机下载状态。") from exc


def _read(root: Path, rel: str) -> tuple[Any, bytes | None]:
    raw = workspace._read(root, rel)
    if raw is None:
        return None, None
    try:
        return json.loads(raw), raw
    except ValueError as exc:
        raise _bad(f"Cannot read {rel}: invalid JSON") from exc


def _bytes(value: Any) -> bytes:
    return workspace._content(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _apart(work: str, others: dict[str, str]) -> workspace.WorkspaceError | None:
    """Why `work` cannot be the work location (DESIGN.md 19.2), or None: it stands apart from the other roles'
    locations (`others`) -- it is not one of them, does not contain one and does not lie inside one -- and is not under
    `research/`, where only Research OS's tools write. Compared as the scan compares paths (`files.fold`)."""
    key = ingest_files.fold(work)
    if key == "research" or key.startswith("research/"):
        return _bad("The work location cannot be under research/, which only Research OS's tools write in",
                    "过程文件的目录不能放在 research/ 里，那里只由 Research OS 的工具写入。")
    for role, rel in others.items():
        other = ingest_files.fold(rel)
        if key == other or key.startswith(other + "/") or other.startswith(key + "/"):
            return _bad(f"The work location must stand apart from the {role} location: it cannot be, contain or lie "
                        f"inside {rel}", f"过程文件的目录“{work}”不能与“{rel}”相同，也不能包含它或位于它里面。")
    return None


def _work_default(root: Path, others: dict[str, Any]) -> str:
    """The work location nobody has chosen yet (DESIGN.md 19.2): the first of its candidates that is a folder or can
    become one (not a file, not a link) and stands apart from the other roles' locations (`others`); one that already
    exists as a folder wins over one that does not, as for the other roles. An empty name when none is usable."""
    apart = {role: rel for role, rel in others.items() if isinstance(rel, str)}
    found = ""
    for rel in ROLES["work"]:
        try:
            target = _path(root, rel)
        except workspace.WorkspaceError:
            continue
        if (target.exists() and not target.is_dir()) or _apart(rel, apart) is not None:
            continue
        if target.is_dir():
            return rel
        found = found or rel
    return found


def _roles(root: Path, value: Any) -> Any:
    """Locations as DESIGN.md 19.2 reads them: a set without `work` (a version-1 file saved before it existed, a run
    that froze its locations before) names the default no one has chosen (`_work_default`)."""
    if isinstance(value, dict) and "work" not in value:
        return {**value, "work": _work_default(root, value)}
    return value


def _folders(root: Path, value: Any, *, unset: bool = False) -> dict[str, str]:
    """Check a full set of locations. `unset` lets `work` be empty: no candidate is usable and the person has yet to
    choose (`folders` reads such settings; saving them is refused with `CHOOSE_WORK`)."""
    if not isinstance(value, dict) or set(value) != set(ROLES):
        raise _bad("Choose a folder for each output type: " + ", ".join(ROLES))
    for role, rel in value.items():
        if role == "work" and rel == "":
            if unset:
                continue
            raise _bad(*CHOOSE_WORK)
        target = _path(root, rel)
        if any(rel == p or rel.startswith(p + "/") for p in (FOLDERS_PATH, workspace.TASKS_PATH, workspace.OVERVIEW_PATH)):
            raise _bad("An output folder cannot replace a project record file")
        if target.exists() and not target.is_dir():
            raise _bad(f"The {role} location is a file, not a folder: {rel}", f"“{rel}”是文件，请填写目录路径。")
        if rel in {"research", "research/notes", "research/results"} or rel.startswith(("research/notes/", "research/results/")):
            raise _bad("Notes and result records have their own save tools; choose another output folder")
    if value["work"]:
        problem = _apart(value["work"], {role: rel for role, rel in value.items() if role != "work"})
        if problem is not None:
            raise problem
    return {role: value[role] for role in ROLES}


def folders(root: Path) -> dict[str, Any]:
    """The eight output locations, saved or defaulted. When no candidate for the work location is usable the settings
    still read, with `work` empty and `"choose": ["work"]` (DESIGN.md 19.2): the page says the location needs choosing,
    and a run is refused until it is."""
    value, raw = _read(root, FOLDERS_PATH)
    if raw is not None:
        if not isinstance(value, dict) or value.get("version") != 1:
            raise _bad("Unknown output-folder settings version")
        chosen = _folders(root, _roles(root, value.get("folders")), unset=True)
    else:
        chosen = {}
        for role, candidates in ROLES.items():
            if role == "work":  # last: it must stand apart from the seven that are chosen by now
                continue
            for rel in candidates:
                try:
                    target = _path(root, rel)
                    if target.exists() and not target.is_dir():
                        continue
                    chosen.setdefault(role, rel)
                    if target.is_dir():
                        chosen[role] = rel
                        break
                except workspace.WorkspaceError:
                    continue
            if role not in chosen:
                raise _bad(f"Choose a safe output folder for {role}")
        try:
            if chosen["data"] == "data" and _path(root, "data/raw").is_dir():
                chosen["data"] = "data/derived"
        except workspace.WorkspaceError:
            pass
        chosen["work"] = _work_default(root, chosen)
        chosen = _folders(root, chosen, unset=True)
    found = {"folders": chosen, "revision": workspace._revision(raw), "saved": raw is not None}
    if not chosen["work"]:
        found["choose"] = ["work"]
    return found


def save_folders(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    with situation.write_guard(root, human=True):
        pid = workspace._require_write(root)
        before = workspace._read(root, FOLDERS_PATH)
        workspace._check_revision(body, before)
        chosen = _folders(root, body.get("folders"))
        after = _bytes({"version": 1, "folders": chosen})
        workspace._save(root, FOLDERS_PATH, before, after, pid)
    return {"folders": chosen, "revision": workspace._revision(after), "saved": True}


def catalog_path(report: str) -> str:
    if not isinstance(report, str) or not workspace._REPORT_RE.fullmatch(report):
        raise _bad("An output record must belong to a task result")
    return report.removesuffix(".md") + ".outputs.json"


def _entries(root: Path, rel: str) -> tuple[list[dict[str, Any]], bytes | None]:
    value, raw = _read(root, rel)
    if raw is None:
        return [], None
    if (not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("items"), list)
            or len(value["items"]) > LIMIT or not all(isinstance(i, dict) for i in value["items"])):
        raise _bad("Cannot read the output catalog")
    for item in value["items"]:
        if (not isinstance(item.get("kind"), str) or item["kind"] not in ROLES
                or not isinstance(item.get("path"), str) or not isinstance(item.get("title"), str)
                or not isinstance(item.get("fingerprint"), dict)):
            raise _bad("Cannot read an entry in the output catalog")
        _path(root, item["path"])
    return value["items"], raw


def _fingerprint(root: Path, rel: str, known: dict[str, Any] | None = None) -> dict[str, Any]:
    full = _path(root, rel)
    if not full.is_file():
        raise _bad(f"The output file does not exist: {rel}", f"“{rel}”已经不在项目里了，不能登记。")
    stamp = full.stat()
    if known and known.get("size") == stamp.st_size and known.get("mtime_ns") == stamp.st_mtime_ns:
        return known
    mark = snapshot.fingerprint(root, rel)
    if not mark or mark.get("dataless") or "link" in mark or mark.get("error") or (stamp.st_size <= snapshot.HASH_LIMIT and not mark.get("sha256")):
        raise _bad(f"The output file cannot be read locally: {rel}",
                   f"“{rel}”现在读不出来（可能还在云端，没有下载到本机），不能登记。")
    return {k: mark[k] for k in ("sha256", "size", "mtime_ns") if k in mark}


def _current(item: dict[str, Any], mark: dict[str, Any]) -> bool:
    old = item.get("fingerprint") or {}
    if old.get("sha256") and mark.get("sha256"):
        return old["sha256"] == mark["sha256"]
    return old == mark


def variable_content(root: Path, rel: str) -> tuple[dict[str, Any], bytes]:
    from researchos.records import variables

    raw = workspace._read(root, rel)
    if raw is None:
        raise _bad(f"The variable definition is missing: {rel}", f"“{rel}”不在了，读不到变量定义。")
    try:
        content = variables.parse_version(raw.decode("utf-8"))
    except (variables.VariableError, variables.VersionInvalid, ValueError) as exc:
        raise _bad(f"Invalid variable definition {rel}: {exc}", f"“{rel}”不是有效的变量定义，不能登记为变量草稿。") from exc
    if not content.get("name"):
        raise _bad(f"A variable draft needs a name: {rel}", f"“{rel}”里的变量定义没有名称，不能登记为变量草稿。")
    decision = content.get("decision") or {}
    if decision.get("decided_by") or decision.get("adopted_on"):
        raise _bad(f"A variable draft cannot claim a human decision or adoption date: {rel}",
                   f"“{rel}”里的变量定义写了人工决定或采用日期，变量草稿不能带这些。")
    return content, raw


def _within(root: Path, rel: str, folder: str) -> bool:
    """Whether `rel` lies inside `folder` by the file system's own case rule (DESIGN.md 19.2): the resolved real
    paths are compared, not the strings. On a case-insensitive volume a declared `Data/x.csv` is inside the location
    `data` -- some folder on the way up is the very folder `data` -- and on no volume is a path outside it."""
    top = os.path.realpath(root)
    place, here = os.path.realpath(root / folder), os.path.realpath(root / rel)
    if here.startswith(place + os.sep):
        return True
    parent = os.path.dirname(here)
    while len(parent) > len(top) and parent.startswith(top):
        try:
            if os.path.samefile(parent, place):
                return True
        except OSError:  # the location is not there: it holds nothing
            return False
        parent = os.path.dirname(parent)
    return False


def _is_record(rel: str) -> bool:
    """Whether a declared path is a note, a result record or one of the app's record files (DESIGN.md 19.2), in
    whatever case or normalisation it is spelled (`files.fold`: on a case-sensitive volume this also refuses a
    namesake, which nothing but Research OS's tools should write beside the records either)."""
    key = ingest_files.fold(rel)
    return (key in {ingest_files.fold(record) for record in RECORD_FILES}
            or any(key.startswith(ingest_files.fold(folder) + "/") for folder in RECORD_FOLDERS))


def _changed_zh(rel: str) -> str:
    """The sentence of a file that changed while it was being registered (DESIGN.md 19.5), naming it."""
    return f"“{rel}”在登记的过程中被改动了，请重新打开清单再试。"


def _catalog_full() -> workspace.WorkspaceError:
    """The refusal of a person's registration that a task's catalog cannot hold (DESIGN.md 19.5): more than `LIMIT`
    entries, or a catalog past the 200 KB a record may be (`workspace.TEXT_LIMIT`), which comes first."""
    return _bad("This task's list of registered files cannot hold that many files: register fewer, or remove "
                "registrations first",
                "这项任务的登记清单装不下这么多文件。请少勾选一些，或先移除不需要的登记。")


def validate(root: Path, declared: Any, locations: dict[str, str] | None, *, anywhere: bool = False) -> list[dict[str, Any]]:
    """Check every declaration before any result/task/catalog file is written. A declaration is an output
    (DESIGN.md 19.2): kind `work` is refused, since process files stay in the run's work folder and are not reported.
    With `anywhere` (a person registering, 19.5; `locations` is then not looked at) a file may be wherever it is, kind
    `work` is accepted with any suffix, and the call may name as many files as a catalog holds (`LIMIT`, where an
    agent's declaration is at most 200); the rest of the checks stand."""
    if anywhere and isinstance(declared, list) and len(declared) > LIMIT:
        raise _catalog_full()
    if not isinstance(declared, list) or len(declared) > (LIMIT if anywhere else 200):
        raise _bad("outputs must be a list of at most 200 {kind, path, title?} objects; use [] for no files")
    locations = None if anywhere else _folders(root, _roles(root, locations), unset=True)
    checked = []
    seen = set()
    for raw in declared:
        if not isinstance(raw, dict) or set(raw) - {"kind", "path", "title"}:
            raise _bad("Each output accepts only kind, path and an optional title")
        kind, rel = raw.get("kind"), raw.get("path")
        if not isinstance(kind, str) or kind not in ROLES or not isinstance(rel, str):
            raise _bad("Each output needs a supported kind and a project-relative path")
        if kind == "work" and not anywhere:
            raise _bad("Process files are not reported as outputs: leave them in your work folder and declare only "
                       f"the outputs ({rel})", "过程文件留在这次任务的工作文件夹里，不要登记；只列出输出文件。")
        _path(root, rel)
        if _is_record(rel):
            raise _bad(f"Notes, result records and the app's record files are written by Research OS only: {rel}",
                       f"“{rel}”是笔记、结果记录或应用自己的记录文件，只能由 Research OS 写入，不能登记。")
        if not anywhere and not _within(root, rel, locations[kind]):
            raise _bad(f"Put the {kind} output under {locations[kind]}/, then report its new path: {rel}",
                       f"“{rel}”不在“{locations[kind]}”里：请把{KIND_ZH[kind]}放进这个目录，再报告它的新路径。")
        if kind != "work" and Path(rel).suffix.lower() not in SUFFIXES[kind]:
            raise _bad(f"Unsupported {kind} format: {rel}", f"“{rel}”的文件格式不能登记为{KIND_ZH[kind]}。")
        if rel in seen:
            raise _bad(f"Report each output file once: {rel}", f"“{rel}”在同一次登记里出现了不止一次。")
        seen.add(rel)
        title = raw.get("title", Path(rel).name)
        if not isinstance(title, str) or not title.strip() or len(title) > 200 or any(ord(c) < 32 for c in title):
            raise _bad(f"An output title must be one line of at most 200 characters: {rel}",
                       f"“{rel}”的标题要写在一行里，不超过 200 个字。")
        mark = _fingerprint(root, rel)
        if kind == "literature" and Path(rel).suffix.lower() != ".pdf":
            from researchos import bibliography

            try:
                records = bibliography._read_source(root, rel)
            except ValueError as exc:
                raise _bad(f"Invalid bibliography {rel}: {exc}", f"“{rel}”不是能读的文献导出文件，不能登记为文献。") from exc
            if not records or not any(bibliography._record(r) for r in records):
                raise _bad(f"The bibliography contains no readable references: {rel}",
                           f"“{rel}”里没有读得出的文献条目，不能登记为文献。")
        elif kind == "variable":
            variable_content(root, rel)
        checked.append({"kind": kind, "path": rel, "title": title.strip(), "fingerprint": mark})
    return checked


def register(root: Path, checked: list[dict[str, Any]], run: dict[str, Any], report: str,
             author: dict[str, Any]) -> tuple[str, str] | None:
    """One record per task report, so independent tasks can also be undone independently. An item the caller says the
    run created (`created: true`, `made_by_run`; DESIGN.md 19.7) keeps that mark when a later run of the task declares
    the file again."""
    if not checked:
        return None
    rel = catalog_path(report)
    existing, before = _entries(root, rel)
    items = {item.get("path"): item for item in existing}
    for item in checked:
        if not _current(item, _fingerprint(root, item["path"])):
            raise _bad(f"The output changed during registration: {item['path']}", _changed_zh(item["path"]))
        entry = {**item, "run": run["id"], "task": run["task_id"], "agent": run.get("label") or run.get("owner"),
                 "report": report, "evidence": "agent_declaration"}
        if (items.get(item["path"]) or {}).get("created") is True:  # 19.7: an earlier run of the task made the file
            entry["created"] = True
        items[item["path"]] = entry
    if len(items) > LIMIT:
        raise _bad("The output catalog is full")
    after = _bytes({"version": 1, "items": list(items.values())})
    workspace._save(root, rel, before, after, run["project_id"], author)
    return rel, hashlib.sha256(after).hexdigest()


def made_by_run(project_id: str, run_id: str, rels: Iterable[str]) -> set[str]:
    """Which of `rels` the run created (DESIGN.md 19.7), for the `created: true` of a catalog entry: the files its
    before-picture (`before.json` in the run's folder) does not hold, compared as the scan compares paths
    (`files.fold`). Nothing is known of a file in a folder the picture does not walk (`snapshot.SKIP_DIRS`), nor of any
    file when the picture is missing, unreadable or cut short (`partial`): those stay as they are, not created."""
    try:
        before = json.loads((runs.run_dir(project_id, run_id) / "before.json").read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError):
        return set()
    held = before.get("files") if isinstance(before, dict) and not before.get("partial") else None
    if not isinstance(held, dict):
        return set()
    known = {ingest_files.fold(rel) for rel in held}
    return {rel for rel in rels if ingest_files.fold(rel) not in known and not snapshot.SKIP_DIRS & set(rel.split("/"))}


# -- a person registers and unregisters (DESIGN.md 19.5) ------------------------------------------------------------


def _owner(root: Path, report: Any) -> dict[str, Any]:
    """The task whose result record `report` is: a person registers files for a task, so the task must still exist (and
    the record be there to be read)."""
    catalog_path(report)  # a research/results/<32 hex>.md path, or refused
    tasks, _revision = workspace.read_tasks(root)
    task = next((t for t in tasks if t.get("report") == report), None)
    if task is None or workspace._read(root, report) is None:  # noqa: SLF001
        raise _bad(f"No task owns the result record {report}", "没有哪项任务的结果记录是这一份，不能给它登记文件。")
    return task


def _created_by(project_id: str, task_id: str, rels: Iterable[str]) -> set[str]:
    """Which of `rels` a run of the task on this Mac created (DESIGN.md 19.5): the paths in the `changes.created` of
    its runs, an undone run's left out (what it created was moved away, so a file at that path now is not its own),
    compared as the scan compares paths (`files.fold`)."""
    known: set[str] = set()
    try:
        for run in runs.iter_runs(project_id):
            created = (run.get("changes") or {}).get("created")
            if run.get("task_id") == task_id and not run.get("reverted") and isinstance(created, list):
                known.update(ingest_files.fold(rel) for rel in created if isinstance(rel, str))
    except (OSError, ValueError):
        pass
    return {rel for rel in rels if ingest_files.fold(rel) in known}


def register_files(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    """A person registers files for a task's result record (DESIGN.md 19.5, `POST /api/outputs/register`): the checks
    of `validate` with the folder rule dropped (`anywhere`), the entries joining the record's catalog beside an
    agent's. An entry says `evidence: "person"`, `who` (the id `me.toml` names, as the ledgers record `by`; none when
    the Mac names nobody), `at` (UTC) and `created`: whether a run of the task on this Mac created the file (a file
    already registered as created stays so). The write is the single person's, the PI's in the official project, a
    member's in their working copy, and goes through the history and backup of every other save."""
    with situation.write_guard(root, human=True):
        pid = workspace._require_write(root)  # noqa: SLF001
        report = body.get("report")
        task = _owner(root, report)
        checked = validate(root, body.get("items"), None, anywhere=True)
        if not checked:
            raise _bad("Choose at least one file to register", "请选择要登记的文件。")
        try:
            who = people.current_member_id()
        except people.PeopleError as exc:  # no entry without saying who made it
            raise _bad(str(exc), "这台电脑上的个人信息文件（me.toml）读不出来，请先修好它。") from exc
        made = _created_by(pid, task["id"], [item["path"] for item in checked])
        rel = catalog_path(report)
        existing, before = _entries(root, rel)
        items = {item.get("path"): item for item in existing}
        at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        for item in checked:
            if not _current(item, _fingerprint(root, item["path"])):
                raise _bad(f"The file changed during registration: {item['path']}", _changed_zh(item["path"]))
            created = item["path"] in made or (items.get(item["path"]) or {}).get("created") is True
            items[item["path"]] = {**item, "report": report, "task": task["id"], "evidence": "person", "who": who,
                                   "at": at, "created": created}
        if len(items) > LIMIT:
            raise _catalog_full()
        try:
            after = _bytes({"version": 1, "items": list(items.values())})
        except workspace.WorkspaceError as exc:  # past 200 KB a record cannot be read back: say what a person can do
            if exc.code != "too_large":
                raise
            raise _catalog_full() from exc
        workspace._save(root, rel, before, after, pid)  # noqa: SLF001
    return {"catalog": rel, "registered": [item["path"] for item in checked]}


def unregister_file(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    """A person takes one file's registration away (DESIGN.md 19.5, `POST /api/outputs/unregister`): the entry leaves
    the record's catalog, which stays a valid file when it is the last. The file itself is never touched. Whoever made
    the entry, agent or person; the task need not be there any more."""
    with situation.write_guard(root, human=True):
        pid = workspace._require_write(root)  # noqa: SLF001
        rel = catalog_path(body.get("report"))
        existing, before = _entries(root, rel)
        kept = [item for item in existing if item.get("path") != body.get("path")]
        if before is None or len(kept) == len(existing):
            raise _bad(f"That file is not registered for this task: {body.get('path')}", "这个文件没有登记在这项任务下。")
        workspace._save(root, rel, before, _bytes({"version": 1, "items": kept}), pid)  # noqa: SLF001
    return {"catalog": rel, "removed": body.get("path")}


# -- the catalogs read back ------------------------------------------------------------------------------------------


def task_title(text: Any) -> str:
    """A task's first line as a heading or a label (DESIGN.md 19.6): the result record's default heading (the agent
    gave none) and the task named on the views' rows. The first line that is not blank as one line, a leading `#`
    dropped, cut to `TITLE_CUT` characters at a space where there is one past half of them, with `…`."""
    lines = (text if isinstance(text, str) else "").splitlines()
    first = re.sub(r"^#+\s*", "", re.sub(r"[\s\0]+", " ", next((line for line in lines if line.strip()), "")).strip())
    if len(first) > TITLE_CUT:
        cut = first[:TITLE_CUT]
        space = cut.rfind(" ")
        first = (cut[:space] if space > TITLE_CUT // 2 else cut).rstrip(" ,;:，；：") + "…"
    return first


def _showable(moment: datetime) -> datetime | None:
    """`moment` when it can be shown in this Mac's local time, else None: a time at the edge of the calendar
    (`0001-01-01T00:00:00Z`, `9999-12-31T23:59:59Z`) overflows in the conversion, in the zones on one side of UTC."""
    try:
        moment.astimezone()
    except (OverflowError, OSError, ValueError):
        return None
    return moment


def _moment(item: dict[str, Any]) -> datetime | None:
    """When an entry was made (DESIGN.md 19.6): its `at`, else the UTC time its run id carries; None for neither. An
    `at` that cannot be read as a date, or cannot be shown in local time, is none: the entry stands on its run id
    (DESIGN.md 19.6 -- one bad entry never empties a view)."""
    if isinstance(item.get("at"), str):
        try:
            moment = datetime.fromisoformat(item["at"].replace("Z", "+00:00"))
            moment = _showable(moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc))
        except ValueError:
            moment = None
        if moment is not None:
            return moment
    found = re.match(r"r-(\d{8}T\d{6}Z)-", str(item.get("run") or ""))
    try:
        return _showable(datetime.strptime(found.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)) if found else None
    except ValueError:
        return None


def _state(root: Path, item: dict[str, Any]) -> str | None:
    """`current` when the registered file is what was registered, `changed` when it is not, None when it is gone."""
    try:
        return "current" if _current(item, _fingerprint(root, item.get("path"), item.get("fingerprint"))) else "changed"
    except (workspace.WorkspaceError, OSError):
        return None


def _view_row(root: Path, item: dict[str, Any], titles: dict[str, str] | None) -> dict[str, Any] | None:
    """One registered file as a view row (DESIGN.md 19.6), or None when its file is not there to be shown."""
    try:
        mark = _fingerprint(root, item.get("path"), item.get("fingerprint"))
    except (workspace.WorkspaceError, OSError):
        return None
    row = {**item, "state": "current" if _current(item, mark) else "changed"}
    task = item.get("task")
    if titles is not None and isinstance(task, str) and task:
        if task in titles:
            row["task_title"] = titles[task]
        else:
            row["task_removed"] = True
    moment = _moment(item)
    if moment is not None:
        row["at"] = moment.astimezone().isoformat(timespec="seconds")
    else:  # an `at` that is no time is no date (19.6)
        row.pop("at", None)
    if item["kind"] == "variable" and row["state"] == "current":
        row["definition"] = variable_content(root, item["path"])[0]
    if item["kind"] == "table" and Path(item["path"]).suffix.lower() in {".csv", ".tsv"}:
        # A bounded sample only; the original table remains unchanged.
        try:
            full = _path(root, item["path"])
            with full.open("rb") as stream:
                body = stream.read(32 * 1024)
            text = body.decode("utf-8-sig")
            if mark["size"] > len(body):
                text = text[:text.rfind("\n")] if "\n" in text else ""
            rows = list(itertools.islice(csv.reader(io.StringIO(text), delimiter="\t" if full.suffix.lower() == ".tsv" else ",", strict=True), 11))
            row["table_preview"] = [r[:20] for r in rows]
        except (OSError, ValueError, csv.Error):
            pass
    return row


def catalog(root: Path, kinds: set[str] | None = None) -> dict[str, Any]:
    """Read registered files even before the index catches up; never follow missing files. Each row says which task
    produced it (`task_title`, or `task_removed` when the task is gone from the task list) and when (`at`, local ISO
    time; DESIGN.md 19.6)."""
    items = []
    try:
        try:
            titles = {t["id"]: task_title(t["text"]) for t in workspace.read_tasks(root)[0]}
        except workspace.WorkspaceError:  # a damaged task list names no task, and removes none
            titles = None
        directory = _path(root, "research/results")
        sources = []
        if directory.is_dir():
            sources = sorted((p for p in directory.iterdir() if guard.is_output_record(p.relative_to(root).as_posix())),
                             key=lambda p: p.stat().st_mtime_ns, reverse=True)[:500]
        entries = []
        for source in sources:
            rel = source.relative_to(root).as_posix()
            if not _path(root, rel.removesuffix(".outputs.json") + ".md").is_file():
                continue
            found, _raw = _entries(root, rel)
            entries.extend({**i, "catalog": rel} for i in found)
            if len(entries) >= LIMIT:
                break
        floor = datetime.min.replace(tzinfo=timezone.utc)
        entries.sort(key=lambda i: (_moment(i) or floor, str(i.get("run") or "")), reverse=True)
        seen = set()
        for item in entries:
            if item.get("kind") not in ROLES or (kinds is not None and item["kind"] not in kinds):
                continue
            if item["path"] in seen:
                continue
            seen.add(item["path"])
            try:  # a bad entry costs its own row and nothing else (19.6)
                row = _view_row(root, item, titles)
            except (workspace.WorkspaceError, OSError, ValueError, OverflowError):
                continue
            if row is not None:
                items.append(row)
        return {"items": items, "error": None}
    except (workspace.WorkspaceError, OSError) as exc:
        return {"items": [], "error": str(exc)}


def import_variable(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    """A human copies a reported definition to a new card/version draft; no confirmation."""
    from researchos.records import cards, variables

    with situation.write_guard(root, human=True):
        pid = workspace._require_write(root)
        registered = next((i for i in catalog(root, {"variable"})["items"] if i["path"] == body.get("path")), None)
        if registered is None:
            raise _bad("The registered variable draft is missing")
        rel_catalog = registered["catalog"]
        entries, before = _entries(root, rel_catalog)
        item = next((i for i in entries if i.get("kind") == "variable" and i.get("path") == body.get("path")), None)
        if item is None or not _current(item, _fingerprint(root, item["path"])):
            raise _bad("The registered variable draft has changed or is missing; register it again")
        card_id = body.get("id")
        if not isinstance(card_id, str) or variables.id_problem(card_id):
            raise _bad("Choose a valid variable card ID")
        _content, raw = variable_content(root, item["path"])
        if hashlib.sha256(raw).hexdigest() != item["fingerprint"].get("sha256"):
            raise _bad("The variable draft changed during import; register it again")
        try:
            path = (cards.revise(root, card_id, expected_id=pid, timeout=10.0) if variables.find_card_dirs(root, card_id)
                    else cards.new_card(root, card_id, expected_id=pid, timeout=10.0))
        except variables.VariableError as exc:
            raise _bad(str(exc), getattr(exc, "message_zh", None) or "无法建立变量草稿，请检查变量 ID 和现有草稿。") from exc
        rel = path.relative_to(root).as_posix()
        workspace._save(root, rel, workspace._read(root, rel), raw, pid)
        item["variable_card"] = card_id
        workspace._save(root, rel_catalog, before, _bytes({"version": 1, "items": entries}), pid)
    return {"id": card_id, "file": rel}


def _official_named(base: Any, rel: str) -> set[str]:
    """The paths the official version's copy of the catalog `rel` names (`base`: the version a member's working copy was
    last synced to, `proposals.Base`): none when it had no such catalog or it cannot be read."""
    text = base.text(rel)
    try:
        return {i["path"] for i in json.loads(text)["items"] if isinstance(i, dict) and isinstance(i.get("path"), str)} if text else set()
    except (ValueError, KeyError, TypeError):
        return set()


def related_files(root: Path, reports: list[str], *, unchanged: Iterable[str] = (), base: Any = None) -> list[str]:
    """Files to offer alongside changed task reports in the existing team review (DESIGN.md 18.3, 19.5): the catalog
    of each of `reports` and every file it names. The catalog of a task that did not change (`unchanged`: the reports
    of such tasks) is offered too when it differs from the official version's (`base`), with the files it names that
    the official one did not -- a file a member registered for a task whose own record stays as it is. Nothing of the
    kind without a `base`."""
    related = []
    for report in reports:
        rel = catalog_path(report)
        try:
            if not _path(root, rel).is_file():
                continue
            related.append(rel)
            entries, _raw = _entries(root, rel)
            related.extend(i["path"] for i in entries)
        except (workspace.WorkspaceError, OSError):
            continue
    for report in unchanged if base is not None else ():
        try:
            rel = catalog_path(report)
            entries, raw = _entries(root, rel)
            if raw is None or hashlib.sha256(raw).hexdigest() == (base.files.get(rel) or {}).get("sha256"):
                continue
            related.append(rel)
            official = _official_named(base, rel)
            related.extend(i["path"] for i in entries if i["path"] not in official)
        except (workspace.WorkspaceError, OSError):
            continue
    if related and _path(root, FOLDERS_PATH).is_file():
        related.append(FOLDERS_PATH)
    related = list(dict.fromkeys(related))
    found = []
    for start in range(0, len(related), workspace.EXISTS_LIMIT):
        found.extend(workspace.existing_files(root, related[start:start + workspace.EXISTS_LIMIT]))
    return found


# -- a task's name and the filing of a run (DESIGN.md 19.2, 19.4) --------------------------------------------------


def slug(text: Any) -> str:
    """The name a task goes by in its folders (DESIGN.md 19.2): its first line, cut to `SLUG_LIMIT` characters, every
    run of characters other than letters, digits, CJK, `-` and `_` turned into one `-` (a `-` among them is part of
    the run, so `a - b` is `a-b`), trimmed of `-`. A task with nothing left of its first line is `task`."""
    lines = unicodedata.normalize("NFC", text if isinstance(text, str) else "").splitlines()
    first = next((line.strip() for line in lines if line.strip()), "")
    return re.sub(r"\W+", "-", first[:SLUG_LIMIT]).strip("-") or "task"


def _is_cache(rel: str) -> bool:
    return any(part in CACHE_FOLDERS for part in rel.split("/")) or rel.lower().endswith((".pyc", ".pyo"))


def _there(root: Path, rel: Any) -> bool:
    """Whether the file a registration names is there (DESIGN.md 19.6): a regular file of the project that can be read
    on this Mac. It is looked for and never read."""
    try:
        return _path(root, rel).is_file()
    except (workspace.WorkspaceError, OSError):
        return False


#: One path a catalog names: the spelling it is registered under, the run that made the entry (an agent's declaration
#: has one, a person's registration does not) and when (`_moment`; None when the entry carries no time).
Named = tuple[str, Any, datetime | None]


def _named(root: Path, catalogs: Iterable[str]) -> dict[str, list[Named]]:
    """What the catalogs name: each path (`files.fold`) with the spellings it is registered under, and by whom and
    when each entry was made (`Named`). A catalog that cannot be read registers nothing, and an entry whose time
    cannot be worked out is named without one."""
    found: dict[str, list[Named]] = {}
    for rel in catalogs:
        try:
            for item in _entries(root, rel)[0]:
                try:
                    moment = _moment(item)
                except (ValueError, OverflowError, OSError, TypeError):  # one bad time costs its entry's date (19.6)
                    moment = None
                found.setdefault(ingest_files.fold(item["path"]), []).append((item["path"], item.get("run"), moment))
        except (workspace.WorkspaceError, OSError, TypeError):
            continue
    return found


def _registered(root: Path, named: dict[str, list[Named]], rel: str, run: dict[str, Any] | None = None) -> bool:
    """Whether `rel` is registered (DESIGN.md 19.6): a catalog of `named` names it, and the file it registered is
    there. While that file is gone the registration stays in its task's row, to be removed, and counts for nothing:
    not in the filing, not in the row's count, not as "already registered" in the listing of earlier runs' files.
    Nor does it count for a file that later appears at that path: asked about a file `run` created, an entry made
    before the run began (not the run's own declaration) registered an earlier file, which was gone by then -- a file
    a run creates was not there when it began -- so the new one is not registered. An entry with no time, or a run
    with none, is judged by the file alone."""
    began = _began(run) if run else None
    floor = began.replace(microsecond=0) if began is not None else None   # entries are stamped to the second
    for path, by, moment in named.get(ingest_files.fold(rel), ()):
        if not _there(root, path):
            continue
        if floor is not None and moment is not None and by != run.get("id") and moment < floor:
            continue
        return True
    return False


def filing(root: Path, created: Iterable[str], *, records: Iterable[str], reports: Iterable[Any],
           work_folder: Any, registered: dict[str, list[Named]] | None = None,
           run: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sort the files a run created into one class each (DESIGN.md 19.4), first match: `record` (`records`: written by
    Research OS's own tools), `output` (registered, and its file is there: named by the catalog of one of the task's
    result records, `reports`, or, given `registered` (`_named`), by any catalog it was made from; given the `run`
    that created the files, not by an entry made before it began, `_registered`), `work` (inside the run's
    `work_folder`), `cache` (`CACHE_FOLDERS`, `.pyc`) and `unregistered` (the rest). Returns `{"counts":
    {class: n}, "unregistered": [at most FILING_KEPT paths], "unregistered_more": n}`. It reads the catalogs and
    nothing else, changes no state, and a catalog it cannot read registers nothing; no class is a violation."""
    known = {ingest_files.fold(rel) for rel in records}
    if registered is None:
        owned = []
        for report in reports:
            try:
                owned.append(catalog_path(report))
            except workspace.WorkspaceError:
                continue
        registered = _named(root, owned)
    folder = ingest_files.fold(work_folder).rstrip("/") + "/" if isinstance(work_folder, str) and work_folder else None
    counts = dict.fromkeys(FILING_CLASSES, 0)
    unregistered: list[str] = []
    for rel in created:
        key = ingest_files.fold(rel)
        if key in known:
            kind = "record"
        elif _registered(root, registered, rel, run):
            kind = "output"
        elif folder is not None and key.startswith(folder):
            kind = "work"
        elif _is_cache(rel):
            kind = "cache"
        else:
            kind = "unregistered"
            unregistered.append(rel)
        counts[kind] += 1
    return {"counts": counts, "unregistered": unregistered[:FILING_KEPT],
            "unregistered_more": max(0, len(unregistered) - FILING_KEPT)}


# -- what a task's row shows (DESIGN.md 19.6) ----------------------------------------------------------------------


def _count_files(root: Path, rel: str) -> tuple[int, bool]:
    """How many visible files the folder `rel` holds now, links not followed, and whether it holds more than
    `WORK_COUNT_LIMIT` (the count is then the limit). A folder that is not there holds none. The walk is bounded in
    what it looks at, not only in what it counts: past `WORK_VISIT_FACTOR` times the limit in entries, files and
    folders alike, it stops and says "more". It asks the directory listing what each entry is, and stats nothing
    (DESIGN.md 19.6: the row's data is read often)."""
    try:
        stack = [_path(root, rel)]
    except workspace.WorkspaceError:
        return 0, False
    count = visited = 0
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    visited += 1
                    if visited > WORK_VISIT_FACTOR * WORK_COUNT_LIMIT:
                        return count, True
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        count += 1
                        if count > WORK_COUNT_LIMIT:
                            return WORK_COUNT_LIMIT, True
        except OSError:
            continue
    return count, False


def _latest_filing(root: Path, run: dict[str, Any], registered: dict[str, list[Named]]) -> dict[str, Any] | None:
    """The filing of the task's latest run as it stands now (DESIGN.md 19.4, 19.6): the files the run created sorted
    again against the catalogs, so that a file registered or unregistered since shows; a run from before V15, which
    has no `filing`, files all it created as unregistered. A file counts as registered when any catalog of the
    project names it (`registered`, `_registered_paths`), the file is there and the entry was not made before the
    run began (`_registered`: a file that appears again at the path of a registration whose file was gone is a new
    one), as the listing of earlier runs' files has it (`backfill`), so that the row's count and that list agree; a
    record is also what the run's tools wrote
    (`tool_paths`). Only the unregistered files that are still there and that a person could register count
    (`workspace.existing_files`: visible, outside the dependency folders). None for a run that was undone: what it
    created was moved away."""
    if run.get("reverted"):
        return None
    changes = run.get("changes")
    created = [rel for rel in (changes.get("created") if isinstance(changes, dict) else None) or () if isinstance(rel, str)]
    held = [rel for rel in run.get("tool_paths") or () if isinstance(rel, str)]
    sorted_ = filing(root, created, records=[*held, *(rel for rel in created if _is_record(rel))], reports=(),
                     work_folder=run.get("work_folder"), registered=registered, run=run)
    there: list[str] = []
    for start in range(0, len(sorted_["unregistered"]), workspace.EXISTS_LIMIT):
        there.extend(workspace.existing_files(root, sorted_["unregistered"][start:start + workspace.EXISTS_LIMIT]))
    return {"counts": {**sorted_["counts"], "unregistered": len(there) + sorted_["unregistered_more"]},
            "unregistered_more": sorted_["unregistered_more"]}


def _task_outputs(root: Path, task: dict[str, Any], run: dict[str, Any] | None,
                  registered: dict[str, list[Named]]) -> dict[str, Any]:
    found: dict[str, Any] = {"entries": [], "error": False, "filing": None, "work_folder": None,
                             "work_files": None, "work_files_more": False}
    try:
        entries, _raw = _entries(root, catalog_path(task["report"]))
    except (workspace.WorkspaceError, OSError):  # a catalog that cannot be read registers nothing, and is said so
        entries, found["error"] = [], True
    for item in entries:
        # a file that is gone stays listed, to be removed (19.6); the views leave it out
        found["entries"].append({"kind": item["kind"], "path": item["path"], "title": item["title"],
                                 "state": _state(root, item) or "gone", "evidence": item.get("evidence")})
    if run is not None:
        if isinstance(run.get("work_folder"), str) and run["work_folder"]:
            found["work_folder"] = run["work_folder"]
            found["work_files"], found["work_files_more"] = _count_files(root, run["work_folder"])
        found["filing"] = _latest_filing(root, run, registered)
    return found


def _stamp(path: Path) -> tuple[int, int] | None:
    """A file's or folder's modification time and size, None when it is not there: a cheap mark of a change."""
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size


def _rows_key(root: Path, project_id: str, token: Any, wanted: list[dict[str, Any]]) -> tuple[Any, ...]:
    """What the answer of `task_outputs` is the answer of, short of reading it (DESIGN.md 19.6): the caller's `token`
    (the generation of the file watcher, which moves with every write the app makes and every run that ends), the
    tasks asked about, a stamp of each catalog (a catalog changed by something else, `git pull` say) and of the
    project's folder of runs (a run started since). A few stats, and nothing is read."""
    catalogs = []
    for task in wanted:
        try:
            catalogs.append(_stamp(root / catalog_path(task["report"])))
        except workspace.WorkspaceError:
            catalogs.append(None)
    try:
        started = _stamp(runs.project_runs_dir(project_id))
    except ValueError:
        started = None
    return str(root), project_id, token, tuple((task["id"], task["report"]) for task in wanted), tuple(catalogs), started


def task_outputs(root: Path, project_id: str, tasks: Iterable[dict[str, Any]], *, memo: dict[str, Any] | None = None,
                 token: Any = None) -> dict[str, dict[str, Any]]:
    """What the task rows show under the run controls (DESIGN.md 19.6), by task id, for each task with a result
    record: `entries` (its catalog's files: kind, path, title, evidence, and `state`: `current`, `changed`, or `gone`
    for a file that is no longer there, which stays listed so that its registration can be removed; at most `LIMIT`),
    `error` (the catalog cannot be read), and, from the task's latest run on this Mac, `filing` (the counts of 19.4
    as they stand now, `_latest_filing`) and the `work_folder` with `work_files`, the files in it now
    (`_count_files`; `work_files_more` when there are more than are counted). Reads only.

    The answer is worked out once per change of the project's files, not once per poll of the page: given a `memo`
    (a dict the caller keeps for the project it serves) and a `token`, a call whose `_rows_key` is that of the last
    answer returns that answer, and the next call after anything moved works it out again. The caller reads the token
    before the call, so a change that lands while the answer is worked out leaves it keyed to the older token."""
    wanted = [task for task in tasks if isinstance(task.get("report"), str) and task["report"]]
    key = None
    if memo is not None and token is not None:
        key = _rows_key(root, project_id, token, wanted)
        last = memo.get("last")   # one read: another request may replace it meanwhile
        if last is not None and last[0] == key:
            return last[1]
    pending = {task["id"] for task in wanted}
    latest: dict[str, dict[str, Any]] = {}
    try:
        for run in runs.iter_runs(project_id) if pending else ():
            if run.get("task_id") in pending:
                latest[run["task_id"]] = run
                pending.discard(run["task_id"])
            if not pending:
                break
    except (OSError, ValueError):  # no runs folder to read, or no valid project id: no runs
        pass
    registered = _registered_paths(root) if latest else {}   # any catalog of the project, read once for every row
    found = {task["id"]: _task_outputs(root, task, latest.get(task["id"]), registered) for task in wanted}
    if key is not None and memo is not None:
        memo["last"] = (key, found)
    return found


# -- the files of earlier runs, for a person to review (DESIGN.md 19.5, 19.10 item 4) ---------------------------------

#: How many files the listing of earlier runs' unregistered files names (19.5); past it, a count.
BACKFILL_LIMIT = 5000
#: 19.5's rule 1, the first to match: a path segment of a process file, or a file name of one.
WORK_SEGMENTS = frozenset({"checks", "check", "logs", "log", "tmp", "scratch", "cache", "sources", "vendor"})
WORK_NAMES = ("*_stdout.txt", "*_stderr.txt", "*.log", "report_check.*", "summary_check.*", "pytest*.xml", "*sha256*.json")
#: Rule 2: an image, or a pdf under one of these folders, is a figure.
FIGURE_SEGMENTS = frozenset({"figures", "figure", "plots", "fig"})
IMAGE_SUFFIXES = SUFFIXES["figure"] - {".pdf"}


def suggest_kind(rel: str) -> str | None:
    """The kind DESIGN.md 19.5 suggests for a file an earlier run created, by the eight numbered rules, first match
    wins; None is rule 8, not registered. The path is compared as the scan compares paths (`files.fold`), so `Checks/`
    is `checks/`. Every kind returned is one the file's suffix lets a person register it as (`SUFFIXES`)."""
    parts = ingest_files.fold(rel).split("/")
    name, folders = parts[-1], set(parts[:-1])
    suffix = posixpath.splitext(name)[1]
    if WORK_SEGMENTS & set(parts) or any(fnmatch.fnmatchcase(name, pattern) for pattern in WORK_NAMES):
        return "work"                                                               # 1
    if suffix in IMAGE_SUFFIXES or (suffix == ".pdf" and FIGURE_SEGMENTS & folders):
        return "figure"                                                             # 2
    if suffix == ".bib":
        return "literature"                                                         # 3
    if suffix == ".toml" and "variables" in folders:
        return "variable"
    if suffix in SUFFIXES["code"]:
        return "code"                                                               # 4
    if suffix in {".csv", ".tsv"} and "tables" in folders:
        return "table"                                                              # 5
    if suffix in SUFFIXES["data"]:
        return "data"                                                               # 6
    if suffix in SUFFIXES["document"]:
        return "document"                                                           # 7
    return None                                                                     # 8


def _has_record(root: Path, report: str) -> bool:
    """Whether the result record `report` names is there to be read, as the views read the catalogs beside it."""
    try:
        catalog_path(report)
        return _path(root, report).is_file()
    except workspace.WorkspaceError:
        return False


def _registered_paths(root: Path) -> dict[str, list[Named]]:
    """Every path any catalog of the project names (`_named`), a catalog being what the views read: the one beside a
    result record that is there. A catalog that cannot be read registers nothing."""
    catalogs = []
    try:
        directory = _path(root, "research/results")
        sources = sorted(directory.iterdir()) if directory.is_dir() else []
    except (workspace.WorkspaceError, OSError):
        return {}
    for source in sources:
        rel = source.relative_to(root).as_posix()
        try:
            if guard.is_output_record(rel) and _path(root, rel.removesuffix(".outputs.json") + ".md").is_file():
                catalogs.append(rel)
        except (workspace.WorkspaceError, OSError):
            continue
    return _named(root, catalogs)


def _began(run: dict[str, Any]) -> datetime | None:
    """When a run began: its `started`, else its `created`, else the UTC time its id carries; None for none of them."""
    for value in (run.get("started"), run.get("created")):
        moment = _moment({"at": value}) if isinstance(value, str) else None
        if moment is not None:
            return moment
    return _moment({"run": run.get("id")})


def _run_moment(run: dict[str, Any]) -> str | None:
    """When a run began, as local ISO time (`_began`)."""
    moment = _began(run)
    return moment.astimezone().isoformat(timespec="seconds") if moment is not None else None


def backfill(root: Path, project_id: Any, run_id: Any = None) -> dict[str, Any]:
    """What a person may register from earlier runs (DESIGN.md 19.5, `GET /api/outputs/backfill`): for every run of the
    project on this Mac that is not undone and whose task still exists with a result record -- `run_id` limits it to
    that one run -- the files it created (`changes.created`) that are still regular files in the project (visible, so
    `.DS_Store` is none; no link on the way: `workspace.existing_files`), and are not records (the run's `tool_paths`,
    the app's record files, notes and results), not caches, not inside the run's own work folder (19.4 files those as
    `work`, not as new) and not yet registered (in any catalog, its file there and the entry not made before the run
    began: `_registered`); each with the kind `suggest_kind` proposes. A file a newer run
    created again is listed once, under the newest run. Grouped by task, then by run, newest first:
    `{"groups": [{"task", "text", "report", "runs": [{"id", "date", "agent", "files": [{"path", "kind"}]}]}],
    "listed": n, "more": n}`, at most `BACKFILL_LIMIT` files named and the rest counted in `more`. It reads and writes
    nothing of the project and nothing at all of the runs."""
    tasks, _revision = workspace.read_tasks(root)
    owners = {t["id"]: t for t in tasks if isinstance(t.get("report"), str) and t["report"] and _has_record(root, t["report"])}
    registered = _registered_paths(root)
    seen: set[str] = set()
    groups: dict[str, dict[str, Any]] = {}
    listed = more = 0
    try:
        found = [runs.load_run(project_id, run_id)] if isinstance(run_id, str) and run_id else runs.iter_runs(project_id)
        for run in found:
            changes = run.get("changes") if run else None
            created = changes.get("created") if isinstance(changes, dict) else None
            if not run or run.get("reverted") or run.get("task_id") not in owners or not isinstance(created, list):
                continue
            task = owners[run["task_id"]]
            held = {ingest_files.fold(rel) for rel in run.get("tool_paths") or () if isinstance(rel, str)}
            work = run.get("work_folder")
            folder = ingest_files.fold(work).rstrip("/") + "/" if isinstance(work, str) and work else None
            wanted = []
            for rel in created:
                key = ingest_files.fold(rel) if isinstance(rel, str) else None
                if (key is None or key in seen or key in held or _is_record(rel) or _is_cache(rel)
                        or (folder is not None and key.startswith(folder)) or _registered(root, registered, rel, run)):
                    continue
                seen.add(key)
                wanted.append(rel)
            there: set[str] = set()
            for start in range(0, len(wanted), workspace.EXISTS_LIMIT):
                there.update(workspace.existing_files(root, wanted[start:start + workspace.EXISTS_LIMIT]))
            files = []
            for rel in wanted:
                if rel not in there:
                    continue
                if listed >= BACKFILL_LIMIT:
                    more += 1
                    continue
                files.append({"path": rel, "kind": suggest_kind(rel)})
                listed += 1
            if files:
                group = groups.setdefault(task["id"], {"task": task["id"], "text": task_title(task["text"]),
                                                       "report": task["report"], "runs": []})
                group["runs"].append({"id": run.get("id"), "date": _run_moment(run),
                                      "agent": run.get("label") or run.get("owner"), "files": files})
    except (OSError, ValueError):  # no runs folder to read, or no valid project id or run id: no runs
        pass
    return {"groups": list(groups.values()), "listed": listed, "more": more}

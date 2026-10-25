"""Project output locations and the files reported by agent tasks.

Both records are ordinary project files. Registration checks paths and formats;
it never moves files, executes analysis, or confirms a research judgment.

The eighth location, `work` (DESIGN.md 19.2), is where an agent puts everything
it writes only to carry out a task. It is never declared: `validate` refuses it,
and `filing` (19.4) is how the app tells, after a run, which of the files the run
created are records, outputs, process files, caches or none of these. It stands
apart from the other seven (`_apart`); when nothing usable is left to name it,
the settings still read and `choose` says so (`folders`).
"""
from __future__ import annotations

import hashlib
import csv
import io
import itertools
import json
import os
import posixpath
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from researchos import workspace
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
#: The longest first line of a task a slug is made from (19.2).
SLUG_LIMIT = 24
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
    if not workspace._REPORT_RE.fullmatch(report):
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
        raise _bad(f"The output file does not exist: {rel}")
    stamp = full.stat()
    if known and known.get("size") == stamp.st_size and known.get("mtime_ns") == stamp.st_mtime_ns:
        return known
    mark = snapshot.fingerprint(root, rel)
    if not mark or mark.get("dataless") or "link" in mark or mark.get("error") or (stamp.st_size <= snapshot.HASH_LIMIT and not mark.get("sha256")):
        raise _bad(f"The output file cannot be read locally: {rel}")
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
        raise _bad(f"The variable definition is missing: {rel}")
    try:
        content = variables.parse_version(raw.decode("utf-8"))
    except (variables.VariableError, ValueError) as exc:
        raise _bad(f"Invalid variable definition {rel}: {exc}") from exc
    if not content.get("name"):
        raise _bad("A variable draft needs a name")
    decision = content.get("decision") or {}
    if decision.get("decided_by") or decision.get("adopted_on"):
        raise _bad("A variable draft cannot claim a human decision or adoption date")
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


def validate(root: Path, declared: Any, locations: dict[str, str]) -> list[dict[str, Any]]:
    """Check every declaration before any result/task/catalog file is written. A declaration is an output
    (DESIGN.md 19.2): kind `work` is refused, since process files stay in the run's work folder and are not reported."""
    if not isinstance(declared, list) or len(declared) > 200:
        raise _bad("outputs must be a list of at most 200 {kind, path, title?} objects; use [] for no files")
    locations = _folders(root, _roles(root, locations), unset=True)
    checked = []
    seen = set()
    for raw in declared:
        if not isinstance(raw, dict) or set(raw) - {"kind", "path", "title"}:
            raise _bad("Each output accepts only kind, path and an optional title")
        kind, rel = raw.get("kind"), raw.get("path")
        if not isinstance(kind, str) or kind not in ROLES or not isinstance(rel, str):
            raise _bad("Each output needs a supported kind and a project-relative path")
        if kind == "work":
            raise _bad("Process files are not reported as outputs: leave them in your work folder and declare only "
                       f"the outputs ({rel})", "过程文件留在这次任务的工作文件夹里，不要登记；只列出输出文件。")
        _path(root, rel)
        if _is_record(rel):
            raise _bad(f"Notes, result records and the app's record files are written by Research OS only: {rel}")
        if not _within(root, rel, locations[kind]):
            raise _bad(f"Put the {kind} output under {locations[kind]}/, then report its new path: {rel}")
        if Path(rel).suffix.lower() not in SUFFIXES[kind]:
            raise _bad(f"Unsupported {kind} format: {rel}")
        if rel in seen:
            raise _bad(f"Report each output file once: {rel}")
        seen.add(rel)
        title = raw.get("title", Path(rel).name)
        if not isinstance(title, str) or not title.strip() or len(title) > 200 or any(ord(c) < 32 for c in title):
            raise _bad("An output title must be one line of at most 200 characters")
        mark = _fingerprint(root, rel)
        if kind == "literature" and Path(rel).suffix.lower() != ".pdf":
            from researchos import bibliography

            try:
                records = bibliography._read_source(root, rel)
            except ValueError as exc:
                raise _bad(f"Invalid bibliography {rel}: {exc}") from exc
            if not records or not any(bibliography._record(r) for r in records):
                raise _bad(f"The bibliography contains no readable references: {rel}")
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
            raise _bad(f"The output changed during registration: {item['path']}")
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


def catalog(root: Path, kinds: set[str] | None = None) -> dict[str, Any]:
    """Read registered files even before the index catches up; never follow missing files."""
    items = []
    try:
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
        entries.sort(key=lambda i: str(i.get("run") or ""), reverse=True)
        seen = set()
        for item in entries:
            if item.get("kind") not in ROLES or (kinds is not None and item["kind"] not in kinds):
                continue
            if item["path"] in seen:
                continue
            seen.add(item["path"])
            try:
                mark = _fingerprint(root, item.get("path"), item.get("fingerprint"))
            except (workspace.WorkspaceError, OSError):
                continue
            row = {**item, "state": "current" if _current(item, mark) else "changed"}
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


def related_files(root: Path, reports: list[str]) -> list[str]:
    """Files to offer alongside changed task reports in the existing team review."""
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


def filing(root: Path, created: Iterable[str], *, records: Iterable[str], reports: Iterable[Any],
           work_folder: Any) -> dict[str, Any]:
    """Sort the files a run created into one class each (DESIGN.md 19.4), first match: `record` (`records`: written by
    Research OS's own tools), `output` (in the catalog of one of the task's result records, `reports`), `work` (inside
    the run's `work_folder`), `cache` (`CACHE_FOLDERS`, `.pyc`) and `unregistered` (the rest). Returns `{"counts":
    {class: n}, "unregistered": [at most FILING_KEPT paths], "unregistered_more": n}`. It reads the catalogs and
    nothing else, changes no state, and a catalog it cannot read registers nothing; no class is a violation."""
    known = {ingest_files.fold(rel) for rel in records}
    registered: set[str] = set()
    for report in reports:
        try:
            registered.update(ingest_files.fold(item["path"]) for item in _entries(root, catalog_path(report))[0])
        except (workspace.WorkspaceError, OSError, TypeError):
            continue
    folder = ingest_files.fold(work_folder).rstrip("/") + "/" if isinstance(work_folder, str) and work_folder else None
    counts = dict.fromkeys(FILING_CLASSES, 0)
    unregistered: list[str] = []
    for rel in created:
        key = ingest_files.fold(rel)
        if key in known:
            kind = "record"
        elif key in registered:
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

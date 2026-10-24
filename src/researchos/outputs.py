"""Project output locations and the files reported by agent tasks.

Both records are ordinary project files. Registration checks paths and formats;
it never moves files, executes analysis, or confirms a research judgment.
"""
from __future__ import annotations

import hashlib
import csv
import io
import itertools
import json
import posixpath
from pathlib import Path
from typing import Any

from researchos import workspace
from researchos.agents import guard, snapshot
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


def _folders(root: Path, value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(ROLES):
        raise _bad("Choose a folder for each output type: " + ", ".join(ROLES))
    for role, rel in value.items():
        target = _path(root, rel)
        if any(rel == p or rel.startswith(p + "/") for p in (FOLDERS_PATH, workspace.TASKS_PATH, workspace.OVERVIEW_PATH)):
            raise _bad("An output folder cannot replace a project record file")
        if target.exists() and not target.is_dir():
            raise _bad(f"The {role} location is a file, not a folder: {rel}", f"“{rel}”是文件，请填写目录路径。")
        if rel in {"research", "research/notes", "research/results"} or rel.startswith(("research/notes/", "research/results/")):
            raise _bad("Notes and result records have their own save tools; choose another output folder")
    return dict(value)


def folders(root: Path) -> dict[str, Any]:
    value, raw = _read(root, FOLDERS_PATH)
    if raw is not None:
        if not isinstance(value, dict) or value.get("version") != 1:
            raise _bad("Unknown output-folder settings version")
        chosen = _folders(root, value.get("folders"))
    else:
        chosen = {}
        for role, candidates in ROLES.items():
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
        chosen = _folders(root, chosen)
    return {"folders": chosen, "revision": workspace._revision(raw), "saved": raw is not None}


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


def validate(root: Path, declared: Any, locations: dict[str, str]) -> list[dict[str, Any]]:
    """Check every declaration before any result/task/catalog file is written."""
    if not isinstance(declared, list) or len(declared) > 200:
        raise _bad("outputs must be a list of at most 200 {kind, path, title?} objects; use [] for no files")
    _folders(root, locations)
    checked = []
    seen = set()
    for raw in declared:
        if not isinstance(raw, dict) or set(raw) - {"kind", "path", "title"}:
            raise _bad("Each output accepts only kind, path and an optional title")
        kind, rel = raw.get("kind"), raw.get("path")
        if not isinstance(kind, str) or kind not in ROLES or not isinstance(rel, str):
            raise _bad("Each output needs a supported kind and a project-relative path")
        _path(root, rel)
        if not rel.startswith(locations[kind] + "/"):
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
    """One record per task report, so independent tasks can also be undone independently."""
    if not checked:
        return None
    rel = catalog_path(report)
    existing, before = _entries(root, rel)
    items = {item.get("path"): item for item in existing}
    for item in checked:
        if not _current(item, _fingerprint(root, item["path"])):
            raise _bad(f"The output changed during registration: {item['path']}")
        items[item["path"]] = {**item, "run": run["id"], "task": run["task_id"],
                               "agent": run.get("label") or run.get("owner"), "report": report,
                               "evidence": "agent_declaration"}
    if len(items) > LIMIT:
        raise _bad("The output catalog is full")
    after = _bytes({"version": 1, "items": list(items.values())})
    workspace._save(root, rel, before, after, run["project_id"], author)
    return rel, hashlib.sha256(after).hexdigest()


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

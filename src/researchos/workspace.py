"""A project's writing desk, stored as ordinary Markdown and JSON files.

Reads never initialize a desk. Saves share the project lock, backups and
version history used by the other human writers. In a team, a member may
save only in their recognized working copy: these files then travel through
the existing file proposal and publication paths.

The documents are `research/overview.md` and the Markdown files directly in
`research/notes/` and `research/results/` (DESIGN.md 18.3): the ones Research
OS writes are named by 32 hex digits, but any other `.md` name there is shown
and can be opened and saved as well (a hand-made file, a sync service's
conflict copy), and the engine only ever creates the first kind.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import posixpath
import re
import secrets
import sqlite3
import stat
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from researchos import desk, history, officialness, paths, proposals, settings
from researchos.agents import labels as agent_labels
from researchos.ingest import attempts as attempts_ingest
from researchos.ingest import files as ingest_files
from researchos.records import files as record_files
from researchos.records import situation
from researchos.records.identity import IdentityState, read_identity
from researchos.webapp import mapedit

TEXT_LIMIT = 200 * 1024
FILE_LIMIT = 500
DOCUMENT_LIMIT = 500
TASK_LIMIT = 500
EXISTS_LIMIT = 200
OVERVIEW_PATH = "research/overview.md"
TASKS_PATH = "research/tasks.json"
# A document directly in research/notes/ or research/results/: a plain, visible `.md` name (no slash, no control
# character, not hidden). Which of them the engine may create is `_GENERATED_RE`.
_DOCUMENT_RE = re.compile(r"research/(notes|results)/(?!\.)[^/\\\x00-\x1f]+\.md\Z")
_GENERATED_RE = re.compile(r"research/(notes|results)/[0-9a-f]{32}\.md\Z")
_REVISION_RE = re.compile(r"[0-9a-f]{64}\Z")
_TASK_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
# The result record an agent task points to (DESIGN.md 17.2): a result document, never a note or a free path.
_REPORT_RE = re.compile(r"research/results/[0-9a-f]{32}\.md\Z")
logger = logging.getLogger(__name__)


class WorkspaceError(Exception):
    """A refusal suitable for an HTTP response and a localized message."""

    def __init__(self, code: str, message: str, status: int = 400, message_zh: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message_zh = message_zh or {
            "invalid": "内容格式不对，请检查后重试",
            "path": "只能编辑项目中的研究方案、笔记和结果记录",
            "not_found": "这份记录已不存在，请刷新后重试",
            "conflict": "文件已在别处修改，请重新打开后合并内容",
            "cloud": "文件还在云端，请下载到本机后再打开",
            "unreadable": "文件无法读取，请先检查项目文件",
            "too_large": "这份记录超过 200 KB，请拆分后保存",
            "binary": "这里只能编辑 UTF-8 文本文件",
            "read_only": "当前项目只读，团队成员请在自己的工作副本中编辑",
            "history": "无法保留修改历史，本次内容尚未保存",
            "write_failed": "无法写入文件，当前内容尚未保存，请检查磁盘空间和文件权限",
            "identity": "项目的身份文件暂时读不出来（可能还在云端），所以研究尝试的列表先空着；其余内容可以阅读",
        }.get(code, "暂时无法保存，请重试")


def _root(value: str | Path) -> Path:
    root = Path(value).resolve()
    if not root.is_dir():
        raise WorkspaceError("not_found", "The project folder is missing", 404)
    return root


def _title(kind: str, lang: str = "zh") -> str:
    return ({"overview": "Research question and plan", "note": "Note", "result": "Result record"}
            if lang == "en" else {"overview": "研究问题与计划", "note": "笔记", "result": "结果记录"})[kind]


def _default_titles(kind: str) -> frozenset[str]:
    """The title a document without a heading shows, in either language: what the page holds in its title field
    whichever language the screen was in when it read the file (13.1: the file never depends on the screen)."""
    return frozenset({_title(kind, "zh"), _title(kind, "en")})


_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _heading(content: str) -> str | None:
    """The text of a document's first Markdown heading, which is its title; None when it has none."""
    found = _HEADING_RE.search(content)
    return (found.group(1).strip()[:200] or None) if found else None


def _kind(rel: Any) -> str:
    if rel == OVERVIEW_PATH:
        return "overview"
    if not isinstance(rel, str) or not _DOCUMENT_RE.fullmatch(rel):
        raise WorkspaceError("path", "This path is not a workspace document")
    return "note" if rel.startswith("research/notes/") else "result"


def _confined(root: Path, rel: str) -> Path:
    """Reject symlinks, including ones still inside the project."""
    current = root
    for component in Path(rel).parts:
        current = current / component
        if current.is_symlink():
            raise WorkspaceError("path", f"A workspace path must not be a symbolic link: {rel}")
        if paths.is_dataless(current) or current.with_name(f".{current.name}.icloud").exists():
            raise WorkspaceError("cloud", f"{rel} is only in the cloud", 409)
    try:
        current.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise WorkspaceError("path", "The workspace path leads outside the project") from exc
    return current


def _read(root: Path, rel: str) -> bytes | None:
    full = _confined(root, rel)
    try:
        fd = os.open(full, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise WorkspaceError("unreadable", f"Cannot read {rel}: {exc}", 409) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise WorkspaceError("binary", f"{rel} is not a regular text file", 409)
        if info.st_size > TEXT_LIMIT:
            raise WorkspaceError("too_large", f"{rel} is larger than {TEXT_LIMIT} bytes", 413)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(TEXT_LIMIT + 1)
        if len(raw) > TEXT_LIMIT:
            raise WorkspaceError("too_large", f"{rel} is larger than {TEXT_LIMIT} bytes", 413)
        _decode(raw)
        return raw
    finally:
        os.close(fd)


def _decode(raw: bytes) -> str:
    if b"\0" in raw:
        raise WorkspaceError("binary", "A workspace document must not contain NUL bytes", 409)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceError("binary", "A workspace document must be UTF-8 text", 409) from exc


def _revision(raw: bytes | None) -> str | None:
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def _check_revision(body: dict[str, Any], raw: bytes | None) -> None:
    if "revision" not in body:
        raise WorkspaceError("invalid", "An explicit revision is required (null creates a new file)")
    revision = body["revision"]
    if revision is not None and (not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision)):
        raise WorkspaceError("invalid", "The file revision must be a SHA-256 hash or null")
    if revision != _revision(raw):
        raise WorkspaceError("conflict", "The file has changed since it was opened", 409)


def _content(raw: Any) -> bytes:
    if not isinstance(raw, str):
        raise WorkspaceError("invalid", "Content must be text")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkspaceError("invalid", "Content contains an invalid Unicode character") from exc
    if len(encoded) > TEXT_LIMIT:
        raise WorkspaceError("too_large", f"Content is larger than {TEXT_LIMIT} bytes", 413)
    _decode(encoded)
    return encoded


def _document(root: Path, rel: str, raw: bytes | None, *, lang: str = "zh") -> dict[str, Any]:
    kind = _kind(rel)
    if raw is None and kind != "overview":
        raise WorkspaceError("not_found", f"{rel} does not exist", 404)
    content = _decode(raw) if raw is not None else ""
    title = _heading(content) or _title(kind, lang)
    return {"path": rel, "kind": kind, "title": title, "content": content, "revision": _revision(raw)}


def _modified(root: Path, rel: str) -> str | None:
    """When the file last changed (UTC, ISO 8601); None when it does not exist."""
    try:
        return datetime.fromtimestamp((root / rel).stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def document(root: str | Path, path: str, lang: str = "zh") -> dict[str, Any]:
    """Read a managed document, or the virtual empty overview, without writing."""
    root = _root(root)
    _kind(path)
    return _document(root, path, _read(root, path), lang=lang)


def _require_write(root: Path) -> str:
    mode, _me, _team = proposals.role_of(root)
    got = read_identity(root)
    if got.state is not IdentityState.PRESENT or got.identity is None:
        raise WorkspaceError("read_only", "The project has no readable identity", 403)
    pid = got.identity.id
    if mode == "member":
        try:
            base = proposals.read_base(pid)
        except (proposals.ProposalRefused, KeyError, TypeError, ValueError) as exc:
            raise WorkspaceError("read_only", "The working copy's base cannot be read", 403) from exc
        if (base is None or base.project != pid or not isinstance(base.working_copy, str)
                or not Path(base.working_copy).is_absolute() or Path(base.working_copy).resolve() != root):
            raise WorkspaceError("read_only", "A team member may only save in their recognized working copy", 403)
    elif mode not in {"pi", "single"}:
        raise WorkspaceError("read_only", f"The project is read-only for this user ({mode})", 403)
    return pid


def _author(author: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The attribution of a save made by an AI agent's tool (DESIGN.md 17.5):
    `{"source": "agent", "who": "agent:<runner>:<model>[#<person>]", "note":
    <run id> | None}`. Anything else is refused: a save is a person's unless
    it says exactly whose agent it is."""
    if author is None:
        return None
    if (not isinstance(author, Mapping) or author.get("source") != "agent"
            or agent_labels.parse_who(author.get("who")) is None
            or not (author.get("note") is None or isinstance(author.get("note"), str))):
        raise WorkspaceError("invalid", "A save by an agent must name its source, the agent and the run")
    return {"source": "agent", "who": author["who"], "note": author.get("note")}


def _history(store: history.Store, root: Path, rel: str, author: dict[str, Any] | None = None,
             pre_image: bool = False) -> None:
    """Record `rel`'s current content. The pre-image of a save is always the
    scan's (whoever last wrote it outside Research OS): while an agent's run
    is going in the project that includes the run, which may have changed the
    file since the last scan (18.1 item 3), so the pre-image is read as a
    scan during the run reads it. Only the post-image carries `author`."""
    got = (history.record_file(store, root, rel, who=history.scan_who(root),
                               seen=history.run_seen(root) if pre_image else None) if author is None
           else history.record_file(store, root, rel, source=author["source"], who=author["who"],
                                    note=author["note"]))
    if got not in {"recorded", "unchanged"}:
        raise WorkspaceError("history", f"Cannot preserve the history of {rel}", 409)


def _write(root: Path, rel: str, before: bytes | None, after: bytes, pid: str,
           author: dict[str, Any] | None = None) -> tuple[str | None, str | None]:
    """The caller holds write_guard and has checked the current revision."""
    if before == after:
        return None, None
    target = _confined(root, rel)
    record_files.ensure_dir_within(root, target.parent)
    # The backup path is checked too: backups are never redirected by a link.
    _confined(root, f"{paths.project_dir(root).relative_to(root).as_posix()}/backups")
    with history.Store.open(pid) as store:
        backup = None
        if before is not None:
            _history(store, root, rel, None, True)  # the pre-image
            record_files.snapshot_if_first_change_today(root, target)
            backup = mapedit.write_backup_bytes(root, target, before, target.suffix)
        # Catch a file edited outside Research OS while its backup was made.
        if _read(root, rel) != before:
            raise WorkspaceError("conflict", "The file changed while its history was being preserved", 409)
        if before is None:
            tmp = record_files.temp_path_for(target)
            try:
                record_files.write_new_file(tmp, after)
                try:
                    os.link(tmp, target)  # exclusive creation, even beside an external editor
                except FileExistsError as exc:
                    raise WorkspaceError("conflict", "The file was created elsewhere", 409) from exc
                record_files._fsync_dir(target.parent)
            finally:
                tmp.unlink(missing_ok=True)
            record_files.notify_write(target)  # linked into place, not written under its own name
        else:
            record_files.durable_write(target, after)
        # Record explicitly, including in git projects, so uncommitted saves
        # have their own history and do not depend on a future scan/commit.
        try:
            _history(store, root, rel, author)
        except (WorkspaceError, OSError, sqlite3.Error) as exc:
            # The source save already happened. A retry with the old revision
            # would now conflict, so report success plus a history warning.
            logger.exception("saved %s but could not record its new history", target)
            return backup, str(exc)
    return backup, None


def _save(root: Path, rel: str, before: bytes | None, after: bytes, pid: str,
          author: dict[str, Any] | None = None) -> tuple[str | None, str | None]:
    try:
        return _write(root, rel, before, after, pid, author)
    except (OSError, sqlite3.Error, record_files.RecordFileError, mapedit.MapEditError) as exc:
        raise WorkspaceError("write_failed", f"Could not save {rel}: {exc}", 409) from exc


def save_document(root: str | Path, body: dict[str, Any], *,
                  author: Mapping[str, Any] | None = None, lang: str = "zh") -> dict[str, Any]:
    """Save a document. `author` (the agent tools only, DESIGN.md 17.4) is
    `{"source": "agent", "who": ..., "note": ...}`: it attributes the new
    version in the change history; without it nothing differs. `lang` is the
    language the title of a document without a heading is given in (13.1: the
    file is the same whichever it is)."""
    root = _root(root)
    author = _author(author)
    if not isinstance(body, dict):
        raise WorkspaceError("invalid", "A document save must be an object")
    rel = body.get("path")
    if rel is None:
        kind = body.get("kind", "note")
        if kind not in {"overview", "note", "result"}:
            raise WorkspaceError("invalid", "Document kind must be overview, note or result")
        rel = OVERVIEW_PATH if kind == "overview" else f"research/{'notes' if kind == 'note' else 'results'}/{secrets.token_hex(16)}.md"
    kind = _kind(rel)
    if "kind" in body and body["kind"] != kind:
        raise WorkspaceError("invalid", "The document kind does not match its path")
    text = body.get("content")
    title = None
    if "title" in body:
        title = body["title"]
        if not isinstance(title, str) or not title.strip() or len(title) > 200 or any(c in title for c in "\r\n\0"):
            raise WorkspaceError("invalid", "A title must be 1–200 characters on one line")
        title = title.strip()
    _content(text)
    with situation.write_guard(root, human=True):
        pid = _require_write(root)
        before = _read(root, rel)
        _check_revision(body, before)
        if before is None and kind != "overview" and not _GENERATED_RE.fullmatch(rel):
            # A file with any other name is shown and saved once it exists; the engine only creates its own names.
            raise WorkspaceError("path", "A new note or result record is named by Research OS")
        # A title is the first Markdown heading, not a second source of
        # document data. An unchanged title field must not undo a heading
        # the researcher changed directly in the writing area.
        if title is not None and before is None:
            # A new overview is shown with its default title in the title field; saving it as it is shown is no
            # title typed (a new note or result record is shown with none, and the person must give one).
            if kind == "overview" and title in _default_titles(kind):
                pass
            elif text.splitlines()[:1] != [f"# {title}"]:
                text = f"# {title}\n\n{text}"
        elif title is not None:
            # Only a title the person typed is written (DESIGN.md 18.3): one different from the title the file shows.
            # A file without a heading shows its kind's default title, which the page holds in its title field and
            # sends back as it was; that default is in the language of the screen that read the file, so both
            # languages' words stand for "no title typed" (13.1: what is on disk never depends on the screen).
            shown = _heading(_decode(before))
            if (title != shown) if shown is not None else (title not in _default_titles(kind)):
                heading = re.search(r"^#{1,6}[ \t]+.+$", text, re.MULTILINE)
                if heading:
                    text = text[:heading.start()] + "# " + title + text[heading.end():]
                else:
                    text = f"# {title}\n\n{text}"
        after = _content(text)
        backup, history_error = _save(root, rel, before, after, pid, author)
        return {**_document(root, rel, after, lang=lang), "created": before is None, "backup": backup,
                "history_error": history_error, "modified": _modified(root, rel)}


def _tasks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > TASK_LIMIT:
        raise WorkspaceError("invalid", f"Tasks must be a list with at most {TASK_LIMIT} entries")
    result = []
    seen = set()
    for task in value:
        if not isinstance(task, dict) or set(task) - {"id", "text", "done", "owner", "due", "report"}:
            raise WorkspaceError("invalid", "Each task must contain only id, text, done, owner, due and report")
        tid, text = task.get("id"), task.get("text")
        if not isinstance(tid, str) or not _TASK_ID_RE.fullmatch(tid) or tid in seen:
            raise WorkspaceError("invalid", "Each task needs a unique plain id")
        seen.add(tid)
        if not isinstance(text, str) or not text.strip() or len(text) > 2000 or "\0" in text:
            raise WorkspaceError("invalid", "Each task needs text of at most 2,000 characters")
        done, owner, due = task.get("done", False), task.get("owner", ""), task.get("due", "")
        if not isinstance(done, bool) or not isinstance(owner, str) or len(owner) > 200 or any(c in owner for c in "\r\n\0"):
            raise WorkspaceError("invalid", "Task completion must be true/false and owner a single line")
        try:
            agent_labels.parse_owner(owner)
        except ValueError as exc:  # starts with "agent:" but names no runner or no valid model (17.2)
            raise WorkspaceError("invalid", str(exc)) from exc
        report = task.get("report", "")
        if not isinstance(report, str) or (report and not _REPORT_RE.fullmatch(report)):
            raise WorkspaceError("invalid", "A task report must be empty or a research/results/ document path")
        if not isinstance(due, str) or (due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due)):
            raise WorkspaceError("invalid", "A due date must be empty or YYYY-MM-DD")
        if due:
            try:
                date.fromisoformat(due)
            except ValueError as exc:
                raise WorkspaceError("invalid", "The due date is not a calendar date") from exc
        entry = {"id": tid, "text": text, "done": done, "owner": owner, "due": due}
        if report:  # written only when set: a task without one stays as it was on disk
            entry["report"] = report
        result.append(entry)
    return result


def _read_tasks(root: Path) -> tuple[list[dict[str, Any]], bytes | None]:
    raw = _read(root, TASKS_PATH)
    if raw is None:
        return [], None
    try:
        return _tasks(json.loads(raw)), raw
    except (ValueError, WorkspaceError) as exc:
        raise WorkspaceError("unreadable", "research/tasks.json has invalid task data", 409) from exc


def read_tasks(root: str | Path) -> tuple[list[dict[str, Any]], str | None]:
    """The project's tasks and the revision of `research/tasks.json` (its
    SHA-256, None when the file does not exist); `WorkspaceError` like the
    overview's read. Writes nothing."""
    root = _root(root)
    tasks, raw = _read_tasks(root)
    return tasks, _revision(raw)


def save_tasks(root: str | Path, body: dict[str, Any], *,
               author: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Save the task list; `author` as in `save_document`."""
    root = _root(root)
    author = _author(author)
    if not isinstance(body, dict) or set(body) - {"tasks", "revision"}:
        raise WorkspaceError("invalid", "A task save contains tasks and revision only")
    tasks = _tasks(body.get("tasks"))
    after = _content(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n")
    with situation.write_guard(root, human=True):
        pid = _require_write(root)
        _old_tasks, before = _read_tasks(root)
        _check_revision(body, before)
        backup, history_error = _save(root, TASKS_PATH, before, after, pid, author)
        return {"tasks": tasks, "tasks_revision": _revision(after), "backup": backup, "history_error": history_error,
                "changed_task_reports": _changed_reports(root, tasks, proposals.role_of(root)[0])}


def _changed_reports(root: Path, tasks: list[dict[str, Any]], mode: str) -> list[str]:
    """A member's submission of tasks takes the result records along (DESIGN.md 18.3): the `report` paths of the
    tasks that are new or differ from the official version this working copy was last synced to, so that the PI's
    approval of the task does not leave it pointing at a record that is not in the official project. Empty for
    anyone who changes the official version directly. A proposal carries only files that differ from the base, so a
    path that is no change (a record already official) is harmless in the list; when the base cannot be read every
    task counts as changed."""
    if mode != "member" or not any(task.get("report") for task in tasks):
        return []
    official: dict[str, dict[str, Any]] = {}
    try:
        got = read_identity(root)
        base = proposals.read_base(got.identity.id) if got.identity is not None else None
        raw = base.text(TASKS_PATH) if base is not None else None
        if raw is not None:
            official = {task["id"]: task for task in _tasks(json.loads(raw))}
    except (proposals.ProposalRefused, OSError, KeyError, TypeError, ValueError, WorkspaceError):
        official = {}
    return list(dict.fromkeys(task["report"] for task in tasks if task.get("report") and official.get(task["id"]) != task))


def _attempt_order(row: sqlite3.Row) -> tuple[Any, ...]:
    """Attempts of one table by their number as a number, so #2 comes before #10 and 14a right after 14 (the order
    the desk and the command line use); tables by their file."""
    table, _hash, number = str(row["id"]).removeprefix("attempt:").rpartition("#")
    return table, attempts_ingest.attempt_sort_key(number), row["id"]


def _attempts(root: Path) -> list[dict[str, str]]:
    """The attempts a document can refer to, by number. An unreadable project identity is not an empty list: it
    propagates as `paths.IdentityUnavailableError` for `overview` to say."""
    try:
        path = paths.graph_db_path(root)
        if not path.is_file() or paths.is_dataless(path):
            return []
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = sorted(conn.execute("SELECT id, title FROM nodes WHERE type = 'attempt'"), key=_attempt_order)
            return [{"id": row["id"], "label": row["title"] or row["id"]} for row in rows[:FILE_LIMIT]]
    except (OSError, sqlite3.Error, ValueError):
        return []


VERSION_UNKNOWN = ("暂时无法核对版本", "Version unavailable")


def _working_base(root: Path) -> proposals.Base | None:
    """The official version this working copy was last synced to, when it is a readable one made for this folder."""
    got = read_identity(root)
    try:
        base = proposals.read_base(got.identity.id) if got.identity is not None else None
        if (base is None or got.identity is None or base.project != got.identity.id
                or not isinstance(base.working_copy, str) or not Path(base.working_copy).is_absolute()
                or Path(base.working_copy).resolve() != root or not isinstance(base.files, Mapping)):
            return None
    except (proposals.ProposalRefused, OSError, KeyError, TypeError, ValueError):
        return None
    return base


def local_annotations(root: str | Path, revisions: Mapping[str, str | None], lang: str = "zh") -> dict[str, Any]:
    """What 项目首页 says of each workspace file's version, in the words of `researchos.officialness` (DESIGN.md
    18.3): the official version itself, 我的改动，尚未提交, 提议中 or 已批准，待同步 -- the module, states and
    words every other page uses. `revisions` maps a file to its hash (None for a file that does not exist).

    Only these files' hashes are compared with the working copy's base: the rest of the project (datasets above
    all) is neither inventoried nor hashed. A member's own pending proposals are read from their own area of the
    shared folder, as every page does -- for no longer than the desk waits for it (`desk.SHARED_TIMEOUT`, 14.4), so
    a shared folder that hangs never holds the page or a save back; when that cannot be read in time, or at all,
    `mode.pending_known` says so and the words are those of the local comparison. Call it outside the project
    lock: it is only the answer's decoration."""
    root = _root(root)
    mode, _me, _team = proposals.role_of(root)
    base = None if mode in {"single", "pi"} else _working_base(root)
    if mode in {"single", "pi"}:
        seen = officialness.View(root=root)
    elif base is None:
        seen = officialness.View(mode="working_copy", root=root, problems=["这个工作副本所依据的正式版本无法读取"])
    else:
        seen = officialness.View(mode="working_copy", root=root, base=dict(base.files), current=dict(revisions))
        state, got = desk.read_within(f"workspace|{root}|{mode}", lambda: officialness.pending_of(seen))
        officialness.with_pending(seen, got if state == "ok" else None)
    marks = {rel: ({"state": "unknown", "label": settings.say(*VERSION_UNKNOWN, lang)} if seen.problems
                   else officialness.mark(seen.file_state(rel), lang)) for rel in revisions}
    return {"files": marks, "mode": seen.mode_payload(lang)}


def overview(root: str | Path, lang: str = "zh") -> dict[str, Any]:
    """The 项目首页 payload. The documents are the overview first, then the notes and result records by last change,
    newest first (DESIGN.md 18.3), so that when there are more than `DOCUMENT_LIMIT` it is the oldest that are left
    out; the attempts are by number."""
    root = _root(root)
    documents = []
    candidates = []
    revisions = {}
    errors = []
    for directory in ("research/notes", "research/results"):
        try:
            full = _confined(root, directory)
            entries = list(full.iterdir()) if full.is_dir() else []
        except (WorkspaceError, OSError) as exc:
            error = exc if isinstance(exc, WorkspaceError) else WorkspaceError("unreadable", str(exc), 409)
            errors.append({"path": directory, **_error(error, lang)})
            continue
        for entry in entries:
            rel = entry.relative_to(root).as_posix()
            if not _DOCUMENT_RE.fullmatch(rel):
                continue
            try:
                info = entry.stat()
            except OSError:
                candidates.append((0, rel))  # read below, which says why it cannot be
                continue
            if not stat.S_ISDIR(info.st_mode):  # a folder that happens to end in .md is no document
                candidates.append((info.st_mtime_ns, rel))
    candidates.sort(key=lambda c: (-c[0], c[1]))
    documents_truncated = len(candidates) > DOCUMENT_LIMIT - 1
    found = [OVERVIEW_PATH] + [rel for _changed, rel in candidates[:DOCUMENT_LIMIT - 1]]
    for rel in found:
        try:
            raw = _read(root, rel)
            item = _document(root, rel, raw, lang=lang)
            revisions[rel] = _revision(raw)
            modified = _modified(root, rel) if raw is not None else None
            documents.append({k: item[k] for k in ("path", "kind", "title")}
                             | {"excerpt": " ".join(item["content"].split())[:200], "modified": modified})
        except (WorkspaceError, OSError) as exc:
            error = exc if isinstance(exc, WorkspaceError) else WorkspaceError("unreadable", str(exc), 409)
            documents.append({"path": rel, "kind": _kind(rel), "title": Path(rel).stem,
                              "excerpt": "", "modified": None, "error": _error(error, lang)})
    tasks_error = None
    try:
        tasks, raw_tasks = _read_tasks(root)
        revisions[TASKS_PATH] = _revision(raw_tasks)
    except WorkspaceError as exc:
        tasks, raw_tasks, tasks_error = [], None, _error(exc, lang)
    files = []
    truncated = False
    for rel, category in ingest_files.iter_files(root):
        if len(files) >= FILE_LIMIT:
            truncated = True
            break
        try:
            full = _confined(root, rel)
        except WorkspaceError as exc:
            if exc.code in {"cloud", "path"}:
                continue
            raise
        # iter_files is metadata-only and omits hidden files and symlinks.
        # Recheck all parents and regular-file status before exposing links.
        if full.is_file():
            files.append({"path": rel, "type": category or full.suffix.lower().lstrip(".") or "file"})
    mode, _me, _team = proposals.role_of(root)
    try:
        _require_write(root)
        can_write = True
    except WorkspaceError:
        can_write = False
    annotations = local_annotations(root, revisions, lang)
    for item in documents:
        item["officialness"] = annotations["files"].get(item["path"], {
            "state": "unknown", "label": settings.say(*VERSION_UNKNOWN, lang),
        })
    # A project identity that cannot be read right now (a project.toml evicted to the cloud, say) has no index to
    # list attempts from; the page is still shown, with that said (DESIGN.md 18.3), not refused.
    identity_error = None
    try:
        attempts = _attempts(root)
    except paths.IdentityUnavailableError:
        attempts, identity_error = [], _error(WorkspaceError("identity", "The project's identity file cannot be read "
                                              "right now (it may be only in the cloud), so the list of attempts is "
                                              "left out; the rest can be read"), lang)
    return {"documents": documents, "tasks": tasks, "tasks_revision": _revision(raw_tasks),
            "tasks_error": tasks_error, "files": files, "attempts": attempts, "mode": mode,
            "can_write": can_write, "errors": errors, "identity_error": identity_error,
            "tasks_officialness": annotations["files"].get(TASKS_PATH), "officialness_mode": annotations["mode"],
            "changed_task_reports": _changed_reports(root, tasks, mode),
            "files_truncated": truncated, "files_limit": FILE_LIMIT, "documents_truncated": documents_truncated}


def existing_files(root: str | Path, rels: Any) -> list[str]:
    """Which of the project paths in `rels` are files a document may link to (DESIGN.md 18.3): the ones the file
    list of `overview` would offer if it were not cut at `FILE_LIMIT` -- a regular file inside the project, not
    hidden or in a folder the scan leaves out, no link on the way, not only in the cloud. The page asks for the
    paths a document names that its listed files do not include, so that a link to an existing file beyond the
    first 500 is still a link. At most `EXISTS_LIMIT` paths are looked at; reads nothing of the files."""
    root = _root(root)
    found = []
    for rel in list(rels)[:EXISTS_LIMIT] if isinstance(rels, (list, tuple)) else []:
        if (not isinstance(rel, str) or not rel or len(rel) > 1024 or "\\" in rel
                or any(ord(char) < 32 or ord(char) == 127 for char in rel) or posixpath.isabs(rel)
                or posixpath.normpath(rel) != rel):
            continue
        parts = rel.split("/")
        if any(part.startswith(".") for part in parts) or any(part in ingest_files._NOISE_DIRS for part in parts[:-1]):  # noqa: SLF001
            continue
        try:
            if _confined(root, rel).is_file():
                found.append(rel)
        except (WorkspaceError, OSError):
            continue
    return found


def _error(error: WorkspaceError, lang: str) -> dict[str, str]:
    return {"code": error.code, "message": error.message_zh if lang == "zh" else str(error)}


def initialize(root: str | Path, language: str = "zh") -> dict[str, Any]:
    """Create only an empty writing page; an existing page is untouched."""
    root = _root(root)
    with situation.write_guard(root, human=True):
        pid = _require_write(root)
        before = _read(root, OVERVIEW_PATH)
        if before is not None:
            return {**_document(root, OVERVIEW_PATH, before, lang=language), "created": False, "backup": None}
        after = _content(f"# {_title('overview', language)}\n\n")
        _backup, history_error = _save(root, OVERVIEW_PATH, None, after, pid)
        return {**_document(root, OVERVIEW_PATH, after, lang=language), "created": True, "backup": None,
                "history_error": history_error}

"""The node page's endpoints (DESIGN.md 12.1, 12.6, 12.7; task V8 phase 2):
who I am, notes, what changed, and the trace -- the payloads only.
`researchos.webapp.server` routes to them after `_check_local_origin`
(every endpoint, as every other) and maps `NodeApiRefused` to its error.

- `GET /api/me`, `POST /api/me {name, email}` -- `~/.researchos/me.toml`.
- `GET /api/notes?node=…` -- the notes on a node (all, without `node`),
  from the file when it can be trusted, otherwise what the index last
  applied, with the reason; `POST /api/notes {action: add, node, text}` /
  `{action: withdraw, id}` -- the one write path, `researchos.records.notes`.
- `GET /api/history?node=…` -- every version, newest first;
  `GET /api/history/diff?node=…&a=…&b=…` -- what changed (both versions,
  or neither: the newest and the one before it).
- `GET /api/trace?node=…` -- the whole chain of 12.7 with its breaks.

Copy for the page is Chinese (8.8); `error` stays the engine's English for
「详情」.
"""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection
from typing import Any

from researchos import history, people
from researchos import trace as trace_mod
from researchos.records import notes as notes_mod


class NodeApiRefused(Exception):
    """`status` (HTTP), `code` (machine-readable), the English message, and
    `message_zh`; `extra` is merged into the error body."""

    def __init__(self, status: int, code: str, message: str, message_zh: str | None = None,
                 extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message_zh = message_zh
        self.extra = extra or {}


def _arg(query: dict[str, list[str]], name: str, *, required: bool = False) -> str | None:
    value = (query.get(name) or [None])[0]
    if required and not value:
        raise NodeApiRefused(400, "missing", f"missing required query parameter {name!r}", "缺少参数")
    return value or None


# -- me -------------------------------------------------------------------------------


def me_payload() -> dict[str, Any]:
    got = people.read_me()
    return {"state": got.state, "path": str(got.path), "me": got.me.payload() if got.me else None, "error": got.error}


def me_set_payload(body: dict[str, Any]) -> dict[str, Any]:
    name, email = body.get("name"), body.get("email")
    if not isinstance(name, str) or not isinstance(email, str):
        raise NodeApiRefused(400, "missing", "request body must carry string 'name' and 'email'", "请填写名字和邮箱")
    try:
        me = people.write_me(name, email)
    except people.PeopleError as exc:
        raise NodeApiRefused(400, exc.code, str(exc), exc.message_zh) from exc
    return {"ok": True, **me_payload(), "me": me.payload()}


# -- notes ----------------------------------------------------------------------------


def notes_payload(conn: Connection | None, root: Path, query: dict[str, list[str]]) -> dict[str, Any]:
    try:
        return notes_mod.notes_for(root, _arg(query, "node"), conn)
    except ValueError as exc:
        raise NodeApiRefused(400, "not_a_node", str(exc), "无法识别这个节点") from exc


def _note_refused(exc: notes_mod.NoteRefused) -> NodeApiRefused:
    status = {"untrusted": 409, "no_such_note": 404, "already_withdrawn": 409, "no_index": 400,
              "no_question": 409, "question_changed": 409, "would_lose": 409}.get(exc.code, 400)
    extra: dict[str, Any] = {}
    if exc.decision is not None:
        extra["records"] = {
            "state": exc.decision.verdict.value, "reason": exc.decision.reason,
            "message": notes_mod.message_of(exc.decision), "detail": exc.decision.detail,
            "missing": [notes_mod._public(m) for m in exc.decision.missing],
        }
    code = "record_shrunk" if exc.decision is not None and exc.decision.verdict.value == "shrunk" else exc.code
    return NodeApiRefused(status, code, str(exc), exc.message_zh or "笔记没有保存", extra)


def notes_post_payload(root: Path, project_id: str | None, body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    action = body.get("action", "add")
    try:
        if action == "add":
            node, text = body.get("node"), body.get("text")
            if not isinstance(node, str) or not isinstance(text, str):
                raise NodeApiRefused(400, "missing", "request body must carry string 'node' and 'text'", "请填写笔记")
            entry = notes_mod.add_note(root, node, text, via="app", expected_id=project_id, timeout=timeout)
        elif action == "withdraw":
            note_id = body.get("id")
            if not isinstance(note_id, str) or not note_id:
                raise NodeApiRefused(400, "missing", "request body must carry the note's 'id'", "缺少笔记编号")
            entry = notes_mod.withdraw_note(root, note_id, via="app", expected_id=project_id, timeout=timeout)
        else:
            raise NodeApiRefused(400, "bad_action", f"'action' must be add or withdraw, got {action!r}", "不支持的操作")
    except notes_mod.NoteRefused as exc:
        raise _note_refused(exc) from exc
    return {"ok": True, "entry": notes_mod._public(entry.data), "node": entry.get("node")}


def notes_answer_payload(root: Path, project_id: str | None, answer: str, shown: list[str], *, timeout: float) -> dict[str, Any]:
    try:
        missing, appended = notes_mod.answer_shrunk(root, answer, expected_missing=shown, expected_id=project_id,
                                                    timeout=timeout)
    except notes_mod.NoteRefused as exc:
        raise _note_refused(exc) from exc
    return {"ok": True, "file": "notes", "answer": answer, "missing": missing, "appended": appended}


# -- history --------------------------------------------------------------------------


def _history_refused(exc: history.HistoryError) -> NodeApiRefused:
    status = {"no_identity": 409, "no_such_version": 404, "not_a_node": 400, "git": 500}.get(exc.code, 400)
    zh = {"no_identity": "项目身份文件无法读取", "no_such_version": "找不到这个版本",
          "not_a_node": "无法识别这个节点", "git": "git 无法读取这个文件的历史"}.get(exc.code)
    return NodeApiRefused(status, exc.code, str(exc), zh)


def history_payload(root: Path, query: dict[str, list[str]]) -> dict[str, Any]:
    try:
        return history.versions(root, _arg(query, "node", required=True) or "")
    except history.HistoryError as exc:
        raise _history_refused(exc) from exc


def diff_payload(root: Path, query: dict[str, list[str]]) -> dict[str, Any]:
    try:
        return history.diff(root, _arg(query, "node", required=True) or "", _arg(query, "a"), _arg(query, "b"))
    except history.HistoryError as exc:
        raise _history_refused(exc) from exc


# -- trace ----------------------------------------------------------------------------


def trace_payload(conn: Connection, root: Path, query: dict[str, list[str]]) -> dict[str, Any]:
    try:
        result = trace_mod.chain(conn, root, _arg(query, "node", required=True) or "")
    except ValueError as exc:
        raise NodeApiRefused(400, "not_a_node", str(exc), "无法识别这个节点") from exc
    if not result["found"]:
        raise NodeApiRefused(404, "not_found", f"no such node or file: {result['node']['id']}", "找不到这个节点")
    return result

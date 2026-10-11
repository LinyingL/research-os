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
- `GET /api/proposals` -- the PI's inbox or a member's own proposals;
  `GET /api/proposals/<id>`; `POST /api/proposals` (from the working copy:
  a dry run, or written with `write`); `POST /api/proposals/<id>/approve |
  return | discuss` -- the PI only (12.3; `researchos.proposals`).
- `GET /api/share` -- the shared folder's state for this project (role,
  folder, last snapshot, verification, what is pending); `POST
  /api/share/publish {note?}` (the PI), `POST /api/share/sync` (a joined
  working copy), `POST /api/share/join {shared, folder}` (12.2, 12.3, 12.5;
  `researchos.share`).

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


# -- proposals (12.3, 12.4; V8 phase 3) -----------------------------------------------


_PROPOSAL_STATUS = {
    "single_person": 409, "team_unreadable": 409, "no_me": 409, "not_pi": 403, "pi_changes_directly": 409,
    "not_member": 403, "not_yours": 403, "no_base": 409, "bad_kind": 400, "nothing": 409, "ambiguous": 409,
    "reason_required": 400, "no_attempt_table": 409, "attested_required": 400, "changed_since_shown": 409,
    "decided": 409, "not_approvable": 409, "invalid": 400, "wrong_project": 409, "empty_reason": 400,
    "empty_text": 400, "cannot_sign": 409,
}
_EXCHANGE_STATUS = {"not_configured": 409, "inside_project": 409, "unreachable": 503, "exists": 409,
                    "no_such_proposal": 404, "invalid": 400, "half_written": 409, "content": 409,
                    "bad_location": 409}


def _proposal_refused(exc: Exception) -> NodeApiRefused:
    from researchos import exchange as exchange_mod  # noqa: PLC0415
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    if isinstance(exc, proposals_mod.ProposalRefused):
        return NodeApiRefused(_PROPOSAL_STATUS.get(exc.code, 400), exc.code, str(exc), exc.message_zh, dict(exc.details))
    if isinstance(exc, exchange_mod.ExchangeError):
        return NodeApiRefused(_EXCHANGE_STATUS.get(exc.code, 409), f"exchange_{exc.code}", str(exc), exc.message_zh)
    raise exc


def _proposal_call(fn):  # noqa: ANN001, ANN202
    from researchos import exchange as exchange_mod  # noqa: PLC0415
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    try:
        return fn()
    except (proposals_mod.ProposalRefused, exchange_mod.ExchangeError) as exc:
        raise _proposal_refused(exc) from exc


def proposals_payload(root: Path) -> dict[str, Any]:
    """GET /api/proposals: the PI's inbox (收件箱) or a member's 我的提议."""
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    return _proposal_call(lambda: proposals_mod.listing(root))


def proposal_payload(root: Path, proposal_id: str) -> dict[str, Any]:
    """GET /api/proposals/<id>: for the PI with exactly what approving would do."""
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    return _proposal_call(lambda: proposals_mod.open_for(root, proposal_id))


def proposal_create_payload(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    """POST /api/proposals {kind, sentence, number?, result?, reason?, card?,
    attested?, only?, write?, expected?}: without `write`, the dry run (with
    the `fingerprint` to send back); with it, the proposal is written --
    refused if it is no longer the one shown."""
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    kind, sentence = body.get("kind"), body.get("sentence")
    if not isinstance(kind, str) or not isinstance(sentence, str):
        raise NodeApiRefused(400, "missing", "request body must carry string 'kind' and 'sentence'", "请选择提议类型并写一句说明")
    options = {}
    for name in ("number", "result", "reason", "card", "attested", "supersedes"):
        value = body.get(name)
        if value is not None and not isinstance(value, str):
            raise NodeApiRefused(400, "missing", f"{name!r} must be text", "提议的内容不对")
        options[name] = value
    only = body.get("only")
    if only is not None and (not isinstance(only, list) or not all(isinstance(x, str) for x in only)):
        raise NodeApiRefused(400, "missing", "'only' must be a list of paths", "提议的内容不对")

    def run() -> dict[str, Any]:
        draft = proposals_mod.make_proposal(root, kind, sentence, only=only, **options)
        if not body.get("write"):
            return {"ok": True, "written": False, "proposal": draft.data, "preview": draft.preview,
                    "fingerprint": draft.fingerprint}
        expected = body.get("expected")
        stored = proposals_mod.write_proposal(root, draft, expected=expected if isinstance(expected, str) else None)
        return {"ok": True, "written": True, "id": stored.id}

    return _proposal_call(run)


def proposal_act_payload(root: Path, project_id: str | None, proposal_id: str, action: str, body: dict[str, Any], *,
                         timeout: float, ingest_lock: Any = None) -> dict[str, Any]:
    """POST /api/proposals/<id>/approve {note?} | return {reason} | discuss
    {text}: the PI only (refused otherwise, nothing written)."""
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    def text(name: str) -> str | None:
        value = body.get(name)
        if value is not None and not isinstance(value, str):
            raise NodeApiRefused(400, "missing", f"{name!r} must be text", "内容不对")
        return value

    def run() -> dict[str, Any]:
        if action == "approve":
            done = proposals_mod.approve(root, proposal_id, note=text("note"), via="app", expected_id=project_id,
                                         timeout=timeout, ingest_lock=ingest_lock, rescan=False)
            return {"ok": True, **done}
        if action == "return":
            return {"ok": True, "outcome": proposals_mod.return_(root, proposal_id, text("reason") or "",
                                                                 expected_id=project_id, timeout=timeout)}
        if action == "discuss":
            return {"ok": True, "outcome": proposals_mod.discuss(root, proposal_id, text("text") or "",
                                                                 expected_id=project_id, timeout=timeout)}
        raise NodeApiRefused(404, "bad_action", f"no such action on a proposal: {action!r}", "不支持的操作")

    return _proposal_call(run)


# -- the shared drive (12.2, 12.3, 12.5; V8 phase 4) ---------------------------------------


_SHARE_STATUS = {
    "not_configured": 409, "unreachable": 503, "no_team": 409, "team_unreadable": 409, "not_pi": 403, "no_me": 409,
    "team_exists": 409, "member_exists": 409, "invalid": 400, "invalid_key": 400, "dataless": 409, "waiting": 409,
    "nothing_published": 404, "untrusted": 409, "not_empty": 409, "not_joined": 409, "cannot_sign": 409, "content": 409,
}


def _share_call(fn):  # noqa: ANN001, ANN202
    from researchos import exchange as exchange_mod  # noqa: PLC0415
    from researchos import proposals as proposals_mod  # noqa: PLC0415
    from researchos import share  # noqa: PLC0415

    try:
        return fn()
    except share.ShareError as exc:
        raise NodeApiRefused(_SHARE_STATUS.get(exc.code, 400), exc.code, str(exc), exc.message_zh, dict(exc.details)) from exc
    except (proposals_mod.ProposalRefused, exchange_mod.ExchangeError) as exc:
        raise _proposal_refused(exc) from exc


def share_status_payload(root: Path) -> dict[str, Any]:
    """GET /api/share."""
    from researchos import share  # noqa: PLC0415

    return _share_call(lambda: share.status(root))


def share_publish_payload(root: Path, project_id: str | None, body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    """POST /api/share/publish {note?}: 「发布」 -- the PI only."""
    from researchos import share  # noqa: PLC0415

    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise NodeApiRefused(400, "missing", "'note' must be text", "内容不对")
    return {"ok": True, **_share_call(lambda: share.publish(root, note=note, expected_id=project_id, timeout=timeout))}


def share_sync_payload(root: Path, project_id: str | None, *, timeout: float) -> dict[str, Any]:
    """POST /api/share/sync: 「同步」 -- this working copy to the latest
    verified snapshot; the member's own changes are never overwritten."""
    from researchos import share  # noqa: PLC0415

    return {"ok": True, **_share_call(lambda: share.sync(root, expected_id=project_id, timeout=timeout))}


def share_join_payload(body: dict[str, Any]) -> dict[str, Any]:
    """POST /api/share/join {shared, folder}: a working copy in a new (or
    empty) folder; refused at a top-level folder, in Research OS's home,
    inside a project, or anywhere not empty."""
    from researchos import share  # noqa: PLC0415

    shared, folder = body.get("shared"), body.get("folder")
    if not isinstance(shared, str) or not shared.strip() or not isinstance(folder, str) or not folder.strip():
        raise NodeApiRefused(400, "missing", "request body must carry string 'shared' and 'folder'", "请选择公盘文件夹和工作副本的位置")
    shared_path, folder_path = Path(shared).expanduser(), Path(folder).expanduser()
    if not shared_path.is_absolute() or not folder_path.is_absolute():
        # Never resolved against the engine's own working directory.
        raise NodeApiRefused(400, "not_absolute", "'shared' and 'folder' must be absolute paths", "请给出完整的文件夹路径")
    return {"ok": True, **_share_call(lambda: share.join(shared_path, folder_path))}

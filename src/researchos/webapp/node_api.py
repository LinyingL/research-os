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
- `GET /api/node?node=…` -- one object's page (U3): its trace as columns,
  its breaks with their repairs, the links touching it;
  `GET /api/results[?draft=…]` -- 成果; `GET /api/data-code` -- 数据与代码
  (`researchos.nodepage`).
- `POST /api/papers {draft, marked}` -- 「设为论文正文」 (12.13,
  `researchos.records.papers`).
- `GET /api/routes` -- 研究路线; `GET /api/evidence?node=attempt:…` -- an
  abandoned route's frozen basis; `GET /api/history/version?node=&version=`
  -- one kept version's content (U4, `researchos.nodepage`).
- `GET /api/proposals` -- the PI's inbox or a member's own proposals;
  `GET /api/proposals/<id>`; `POST /api/proposals` (from the working copy:
  a dry run, or written with `write`); `POST /api/proposals/<id>/approve |
  return | discuss` -- the PI only (12.3; `researchos.proposals`).
- `GET /api/share` -- the shared folder's state for this project (role,
  folder, last snapshot, verification, what is pending); `POST
  /api/share/publish {note?}` (the PI), `POST /api/share/sync` (a joined
  working copy), `POST /api/share/join {shared, folder}` (12.2, 12.3, 12.5;
  `researchos.share`); `POST /api/team/add {id, name, key}` (the PI, 12.12:
  the member's area is made once and the PI told to share it).
- U4: `GET /api/me/key` (this machine's public key, never made by a GET),
  `POST /api/me/key` (made on first need); `POST /api/team/init` (the PI's
  team file); `POST /api/share/set {shared}` (the shared folder, on this
  machine); `POST /api/share/join-preview {shared, folder}` and `GET
  /api/share/sync-preview` -- what joining and syncing would write, before
  they write it.

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


def notes_payload(conn: Connection | None, root: Path, query: dict[str, list[str]], lang: str = "zh") -> dict[str, Any]:
    try:
        return notes_mod.notes_for(root, _arg(query, "node"), conn, lang=lang)
    except ValueError as exc:
        raise NodeApiRefused(400, "not_a_node", str(exc), "无法识别要打开的内容") from exc


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
          "not_a_node": "无法识别要打开的内容", "git": "git 无法读取这个文件的历史"}.get(exc.code)
    return NodeApiRefused(status, exc.code, str(exc), zh)


def history_payload(root: Path, query: dict[str, list[str]], lang: str = "zh") -> dict[str, Any]:
    try:
        return history.versions(root, _arg(query, "node", required=True) or "", lang)
    except history.HistoryError as exc:
        raise _history_refused(exc) from exc


SMALL_TABLE_BYTES = 64 * 1024  # `researchos.proposals.SMALL_TABLE_BYTES`


def diff_payload(root: Path, query: dict[str, list[str]]) -> dict[str, Any]:
    """What changed, in the screen's words (12.13: 「行数从 96 增至 97」,
    「判决从「待定」改为「现行」」 -- the engine's word form, never an
    arrow; the command line keeps its own sentences). `summary_words` is
    the sentence the page shows. A table (CSV, TSV) leads with its rows
    and columns; its line diff is kept only when both versions are small
    (`table_small`), otherwise it is left out (`diff_left_out`)."""
    try:
        out = history.diff(root, _arg(query, "node", required=True) or "", _arg(query, "a"), _arg(query, "b"),
                           words=True)
    except history.HistoryError as exc:
        raise _history_refused(exc) from exc
    out["summary_words"] = out.get("summary")
    if out.get("table"):
        sizes = [int(v.get("size") or 0) for v in (out.get("a") or {}, out.get("b") or {})]
        out["table_small"] = max(sizes) <= SMALL_TABLE_BYTES
        if not out["table_small"] and out.get("diff"):
            out["diff"], out["diff_left_out"] = None, True
    return out


# -- 设为论文正文 (12.13) ------------------------------------------------------------------


def papers_post_payload(root: Path, project_id: str | None, body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    """POST /api/papers {draft, marked}: a draft marked 「设为论文正文」, or
    no longer -- `researchos.records.papers`, the one write path. In a
    member's working copy the mark travels with the next sync, like a note."""
    from researchos.records import papers  # noqa: PLC0415

    draft, marked = body.get("draft"), body.get("marked")
    if not isinstance(draft, str) or not isinstance(marked, bool):
        raise NodeApiRefused(400, "missing", "request body must carry string 'draft' and boolean 'marked'", "缺少文稿")
    try:
        done = papers.set_marked(root, draft, marked, via="app", expected_id=project_id, timeout=timeout)
    except papers.PaperRefused as exc:
        status = {"untrusted": 409, "no_such_draft": 404}.get(exc.code, 400)
        raise NodeApiRefused(status, exc.code, str(exc), exc.message_zh) from exc
    return {"ok": True, **done}


# -- trace ----------------------------------------------------------------------------


def trace_payload(conn: Connection, root: Path, query: dict[str, list[str]]) -> dict[str, Any]:
    try:
        result = trace_mod.chain(conn, root, _arg(query, "node", required=True) or "")
    except ValueError as exc:
        raise NodeApiRefused(400, "not_a_node", str(exc), "无法识别要打开的内容") from exc
    if not result["found"]:
        raise NodeApiRefused(404, "not_found", f"no such node or file: {result['node']['id']}", "找不到要打开的内容")
    return result


# -- the node page, 成果, 数据与代码 (12.6, 12.7, 12.9; V8 U3) ----------------------------


def node_payload(conn: Connection, root: Path, query: dict[str, list[str]], lang: str = "zh") -> dict[str, Any]:
    """GET /api/node?node=…: one object's page (`researchos.nodepage.node`),
    an attempt's 来龙去脉 in the request's language."""
    from researchos import nodepage  # noqa: PLC0415

    given = _arg(query, "node", required=True) or ""
    try:
        return nodepage.node(conn, root, given, lang)
    except ValueError as exc:
        raise NodeApiRefused(400, "not_a_node", str(exc), "无法识别要打开的内容") from exc
    except nodepage.NotFound as exc:
        raise NodeApiRefused(404, "not_found", str(exc), "找不到要打开的内容") from exc


def results_payload(conn: Connection, root: Path, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/results[?draft=…]: 成果, or one draft's claims."""
    from researchos import nodepage  # noqa: PLC0415

    try:
        return nodepage.results(conn, root, _arg(query, "draft"))
    except ValueError as exc:
        raise NodeApiRefused(400, "not_a_node", str(exc), "无法识别这份文稿") from exc


def routes_payload(conn: Connection, root: Path, lang: str = "zh") -> dict[str, Any]:
    """GET /api/routes: 研究路线 (`researchos.nodepage.routes`)."""
    from researchos import nodepage  # noqa: PLC0415

    return nodepage.routes(conn, root, lang)


def evidence_payload(conn: Connection, root: Path, query: dict[str, list[str]], lang: str = "zh") -> dict[str, Any]:
    """GET /api/evidence?node=attempt:…: 「当时的依据」."""
    from researchos import nodepage  # noqa: PLC0415

    given = _arg(query, "node", required=True) or ""
    try:
        return nodepage.evidence(conn, root, given, lang)
    except ValueError as exc:
        raise NodeApiRefused(400, "not_a_node", str(exc), "这不是一条尝试") from exc
    except nodepage.NotFound as exc:
        raise NodeApiRefused(404, "not_found", str(exc), "找不到这条尝试") from exc


def version_payload(root: Path, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/history/version?node=&version=: one kept version as it was."""
    from researchos import nodepage  # noqa: PLC0415

    given = _arg(query, "node", required=True) or ""
    sha = _arg(query, "sha256") or None
    version = _arg(query, "version", required=sha is None) or None
    try:
        return nodepage.version_content(root, given, version, sha)
    except ValueError as exc:
        raise NodeApiRefused(400, "not_a_node", str(exc), "无法识别这个版本") from exc
    except nodepage.NotFound as exc:
        raise NodeApiRefused(404, "no_such_version", str(exc), "找不到这个版本") from exc


def data_code_payload(conn: Connection, root: Path) -> dict[str, Any]:
    """GET /api/data-code: 数据与代码."""
    from researchos import nodepage  # noqa: PLC0415

    return nodepage.data_code(conn, root)


# -- proposals (12.3, 12.4; V8 phase 3) -----------------------------------------------


_PROPOSAL_STATUS = {
    "single_person": 409, "team_unreadable": 409, "no_me": 409, "not_pi": 403, "pi_changes_directly": 409,
    "not_member": 403, "not_in_team": 403, "not_yours": 403, "no_base": 409, "bad_kind": 400, "nothing": 409, "ambiguous": 409,
    "reason_required": 400, "no_attempt_table": 409, "attested_required": 400, "changed_since_shown": 409,
    "decided": 409, "not_approvable": 409, "invalid": 400, "wrong_project": 409, "empty_reason": 400,
    "empty_text": 400, "cannot_sign": 409,
    # a 「源自」 line (14.2)
    "bad_route": 400, "self_link": 400, "circle": 409, "attempt_not_found": 409, "line_exists": 409,
    "no_such_line": 409,
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


_NOT_CARRIED = (
    (r"^row (\S+) \((new|removed|changed)\) is not part of this proposal$",
     lambda m: f"尝试表第 {m.group(1)} 行（{ {'new': '新增', 'removed': '删除', 'changed': '改动'}[m.group(2)] }）不在这份提议里"),
    (r"^row (\S+): changes to (.+) are not part of a verdict change$",
     lambda m: f"第 {m.group(1)} 行其它格子的改动不属于改判，不会发送"),
    (r"^changes outside the table's rows are not proposed$", lambda m: "尝试表的行以外的改动不会提议"),
    (r"^variable versions travel in a variable_version proposal$", lambda m: "变量定义的新版本要用「变量定义」提议发送"),
    (r"^the official version's copy that sync put beside yours$", lambda m: "同步时放在旁边的官方版本，不会发送"),
    (r"^notes, link judgments and hand-drawn links travel", lambda m: "笔记、关联判断和手工连线随同步发送，不需要批准"),
    (r"^canvas arrangements do not travel", lambda m: "关系图上的摆放不随提议发送：每个人保留自己的"),
    (r"^Research OS's own record files are not part of a proposal$", lambda m: "Research OS 自己的记录文件不属于提议"),
    (r"^not chosen for this proposal$", lambda m: "没有选进这份提议"),
)


def not_carried_words(items: Any) -> list[dict[str, Any]]:
    """A proposal's `not_carried` with each reason also in words (8.8):
    `reason_zh`; the engine's English stays as `reason` for 「详情」."""
    import re  # noqa: PLC0415

    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "")
        words = next((fn(m) for pattern, fn in _NOT_CARRIED for m in [re.match(pattern, reason)] if m), None)
        out.append({**item, "reason_zh": words or "这一处改动没有包含在提议里"})
    return out


def _worded(out: dict[str, Any], root: Path) -> dict[str, Any]:
    """A proposal payload as the page shows it (12.11, 8.8): verdicts as
    words, data summaries in the engine's word form (12.13), `not_carried`
    in words."""
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    labels = proposals_mod._labels(root)
    for key in ("attempt", "verdict"):
        if isinstance(out.get(key), dict) and not any(k.endswith("_status") for k in out[key]):
            out[key] = proposals_mod.verdict_words(out[key], labels)
    out["not_carried"] = not_carried_words(out.get("not_carried"))
    changes = out.get("changes")
    if isinstance(changes, dict):
        changes["not_carried"] = not_carried_words(changes.get("not_carried"))
    return out


def proposals_payload(root: Path) -> dict[str, Any]:
    """GET /api/proposals: the PI's inbox (收件箱) or a member's 我的提议."""
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    return _proposal_call(lambda: proposals_mod.listing(root))


def proposal_payload(root: Path, proposal_id: str, lang: str = "zh", member: str | None = None) -> dict[str, Any]:
    """GET /api/proposals/<id>[?from=<member>]: for the PI with exactly what
    approving would do (a line's words in `lang`); with `uses`, the variable
    versions its files use (12.3, U4). `member` names whose proposal it is
    when the id is in more than one member's area, as the acts' `from` does."""
    from researchos import nodepage  # noqa: PLC0415
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    out = _worded(_proposal_call(lambda: proposals_mod.open_for(root, proposal_id, member=member or None, lang=lang)),
                  root)
    files = (out.get("changes") or {}).get("files") or out.get("files") or []
    try:
        out["uses"] = nodepage.variables_used(root, files)
    except Exception:  # noqa: BLE001 -- nothing said rather than something wrong
        out["uses"] = []
    return out


def proposal_create_payload(root: Path, body: dict[str, Any], lang: str = "zh") -> dict[str, Any]:
    """POST /api/proposals {kind, sentence, number?, result?, reason?, card?,
    attested?, only?, parents?, write?, expected?}: without `write`, the dry
    run (with the `fingerprint` to send back); with it, the proposal is
    written -- refused if it is no longer the one shown. A member's 「源自」
    line (14.2) is `{kind: "route_link", op, child, parent, note?}`, its
    sentence optional; a new attempt may name its `parents`."""
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    kind = body.get("kind")
    sentence = body.get("sentence", "" if kind == "route_link" else None)
    if not isinstance(kind, str) or not isinstance(sentence, str):
        raise NodeApiRefused(400, "missing", "request body must carry string 'kind' and 'sentence'", "请选择提议类型并写一句说明")
    options: dict[str, Any] = {}
    for name in ("number", "result", "reason", "card", "attested", "supersedes"):
        value = body.get(name)
        if value is not None and not isinstance(value, str):
            raise NodeApiRefused(400, "missing", f"{name!r} must be text", "提议的内容不对")
        options[name] = value
    if kind == "route_link":
        options["route"] = {k: body.get(k) for k in ("op", "child", "parent", "note")}
    for name in ("only", "parents"):
        value = body.get(name)
        if value is not None and (not isinstance(value, list) or not all(isinstance(x, str) for x in value)):
            raise NodeApiRefused(400, "missing", f"{name!r} must be a list of text", "提议的内容不对")
    only, options["parents"] = body.get("only"), body.get("parents")

    def run() -> dict[str, Any]:
        draft = proposals_mod.make_proposal(root, kind, sentence, only=only, **options)
        if not body.get("write"):
            # 12.13: each file offered says where it stands -- one an earlier
            # proposal already carries is 「提议中」 or 「已批准，待同步」.
            from researchos import officialness  # noqa: PLC0415

            try:
                officialness.annotate(officialness.view(root), draft.preview.get("candidates") or [], lang)
            except Exception:  # noqa: BLE001 -- a mark is a nicety; the files are still offered
                pass
            return {"ok": True, "written": False, "proposal": _worded(dict(draft.data), root), "preview": draft.preview,
                    "fingerprint": draft.fingerprint}
        expected = body.get("expected")
        stored = proposals_mod.write_proposal(root, draft, expected=expected if isinstance(expected, str) else None)
        return {"ok": True, "written": True, "id": stored.id}

    return _proposal_call(run)


def proposal_act_payload(root: Path, project_id: str | None, proposal_id: str, action: str, body: dict[str, Any], *,
                         timeout: float, ingest_lock: Any = None) -> dict[str, Any]:
    """POST /api/proposals/<id>/approve {note?} | return {reason} | discuss
    {text}: the PI only (refused otherwise, nothing written). `from` names
    whose proposal it is when the id is in more than one member's area."""
    from researchos import proposals as proposals_mod  # noqa: PLC0415

    def text(name: str) -> str | None:
        value = body.get(name)
        if value is not None and not isinstance(value, str):
            raise NodeApiRefused(400, "missing", f"{name!r} must be text", "内容不对")
        return value

    def run() -> dict[str, Any]:
        member = text("from")
        if action == "approve":
            done = proposals_mod.approve(root, proposal_id, note=text("note"), via="app", expected_id=project_id,
                                         timeout=timeout, ingest_lock=ingest_lock, rescan=False, member=member)
            return {"ok": True, **done}
        if action == "return":
            return {"ok": True, "outcome": proposals_mod.return_(root, proposal_id, text("reason") or "",
                                                                 expected_id=project_id, timeout=timeout, member=member)}
        if action == "discuss":
            return {"ok": True, "outcome": proposals_mod.discuss(root, proposal_id, text("text") or "",
                                                                 expected_id=project_id, timeout=timeout, member=member)}
        raise NodeApiRefused(404, "bad_action", f"no such action on a proposal: {action!r}", "不支持的操作")

    return _proposal_call(run)


# -- the shared drive (12.2, 12.3, 12.5; V8 phase 4) ---------------------------------------


_SHARE_STATUS = {
    "not_configured": 409, "unreachable": 503, "no_team": 409, "team_unreadable": 409, "not_pi": 403, "no_me": 409,
    "team_exists": 409, "member_exists": 409, "invalid": 400, "invalid_key": 400, "dataless": 409, "waiting": 409,
    "nothing_published": 404, "untrusted": 409, "not_empty": 409, "not_joined": 409, "cannot_sign": 409, "content": 409,
    "damaged": 409, "not_in_team": 403, "other_project": 409, "ambiguous_project": 409, "changed_since_shown": 409,
    "not_writable": 409,
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


def share_sync_payload(root: Path, project_id: str | None, *, timeout: float, lang: str = "zh") -> dict[str, Any]:
    """POST /api/share/sync: 「同步」 -- this working copy to the latest
    verified snapshot; the member's own changes are never overwritten. Its
    reasons and table sentences in the request's language (13.2)."""
    from researchos import share  # noqa: PLC0415

    return {"ok": True, **_share_call(lambda: share.sync(root, expected_id=project_id, timeout=timeout, lang=lang))}


def team_add_payload(root: Path, project_id: str | None, body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    """POST /api/team/add {id, name, key}: `researchos team add` -- the PI
    appends a member with the public key they sent (its text, never a
    path), and the member's empty area `提议/<id>/` is made once on the
    shared folder; `sentence` tells the PI to give that member edit rights
    to it in OneDrive (12.12)."""
    from researchos import share  # noqa: PLC0415

    member, name, key = body.get("id"), body.get("name"), body.get("key")
    key_file = body.get("key_file")
    if (key is None or key == "") and isinstance(key_file, str) and key_file.strip():
        key = _read_key_file(key_file)
    if not all(isinstance(v, str) and v.strip() for v in (member, name, key)):
        raise NodeApiRefused(400, "missing", "request body must carry string 'id', 'name' and 'key'",
                             "请填写成员的邮箱、名字和公钥")
    return {"ok": True, **_share_call(lambda: share.team_add(root, member, name, key, expected_id=project_id,
                                                             timeout=timeout))}


KEY_FILE_LIMIT = 16 * 1024


def _read_key_file(given: str) -> str:
    """The text of the public key file the PI chose (the shell's file
    panel, or a path field where there is none): an absolute path to a
    small file, read once; its text is then parsed as a key like a pasted
    one. Nothing else is done with the path."""
    path = Path(given.strip()).expanduser()
    if not path.is_absolute():
        raise NodeApiRefused(400, "not_absolute", "'key_file' must be an absolute path", "请给出公钥文件的完整路径")
    try:
        if not path.is_file():
            raise NodeApiRefused(400, "no_key_file", f"{path} is not a file", "找不到这个公钥文件")
        if path.stat().st_size > KEY_FILE_LIMIT:
            raise NodeApiRefused(400, "invalid_key", f"{path} is too large for a public key", "这个文件不是公钥（太大了）")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise NodeApiRefused(400, "no_key_file", f"{path} cannot be read: {exc}", "公钥文件无法读取") from exc


def _mine(body: dict[str, Any]) -> Path | None:
    """12.13: the member's own proposals folder, when it is a separate
    folder (then `shared` is the published folder alone)."""
    mine = body.get("mine")
    if mine is None or (isinstance(mine, str) and not mine.strip()):
        return None
    if not isinstance(mine, str):
        raise NodeApiRefused(400, "missing", "'mine' must be text", "内容不对")
    path = Path(mine.strip()).expanduser()
    if not path.is_absolute():
        raise NodeApiRefused(400, "not_absolute", "'mine' must be an absolute path", "请给出完整的文件夹路径")
    return path


def _paths(body: dict[str, Any]) -> tuple[Path, Path, str | None]:
    shared, folder, project = body.get("shared"), body.get("folder"), body.get("project")
    if project is not None and not isinstance(project, str):
        raise NodeApiRefused(400, "missing", "'project' must be text", "项目编号不对")
    if not isinstance(shared, str) or not shared.strip() or not isinstance(folder, str) or not folder.strip():
        raise NodeApiRefused(400, "missing", "request body must carry string 'shared' and 'folder'", "请选择公盘文件夹和工作副本的位置")
    shared_path, folder_path = Path(shared.strip()).expanduser(), Path(folder.strip()).expanduser()
    if not shared_path.is_absolute() or not folder_path.is_absolute():
        # Never resolved against the engine's own working directory.
        raise NodeApiRefused(400, "not_absolute", "'shared' and 'folder' must be absolute paths", "请给出完整的文件夹路径")
    return shared_path, folder_path, project


def join_preview_payload(body: dict[str, Any]) -> dict[str, Any]:
    """POST /api/share/join-preview {shared, folder, project?}: what 「加入」
    would write and whose key it would trust -- nothing is written."""
    from researchos import share  # noqa: PLC0415

    shared_path, folder_path, project = _paths(body)
    mine = _mine(body)
    return {"ok": True, **_share_call(lambda: share.join_preview(shared_path, folder_path, project=project, mine=mine))}


def sync_preview_payload(root: Path, lang: str = "zh") -> dict[str, Any]:
    """GET /api/share/sync-preview: what 「同步」 would write -- read only;
    its reasons in the request's language (13.2)."""
    from researchos import share  # noqa: PLC0415

    return _share_call(lambda: share.sync_preview(root, lang=lang))


def me_key_payload() -> dict[str, Any]:
    """GET /api/me/key: this machine's public key when it has one -- a GET
    never makes one (`POST /api/me/key` does)."""
    from researchos import signing  # noqa: PLC0415

    tool = signing.tool() is not None
    key = signing.local_public_key() if tool else None
    return {"ssh_keygen": tool, "key": key, "fingerprint": signing.fingerprint(key) if key else None,
            "path": str(signing.key_path()) + ".pub"}


def me_key_create_payload() -> dict[str, Any]:
    """POST /api/me/key: `researchos me key` -- made on first need."""
    from researchos import share  # noqa: PLC0415

    return {"ok": True, "ssh_keygen": True, **_share_call(share.my_key)}


def team_init_payload(root: Path, project_id: str | None, *, timeout: float) -> dict[str, Any]:
    """POST /api/team/init: `researchos team init` -- the team file with me
    as the PI and my public key; never over an existing one."""
    from researchos import share  # noqa: PLC0415

    return {"ok": True, **_share_call(lambda: share.team_init(root, expected_id=project_id, timeout=timeout))}


def share_set_payload(root: Path, project_id: str | None, body: dict[str, Any]) -> dict[str, Any]:
    """POST /api/share/set {shared}: `researchos share set` -- this project's
    shared folder, remembered on this machine (`projects.json`); nothing is
    written in the project or the folder. The PI's (or one person's) act:
    a member's copy has its folder from joining.

    12.13: `{published, mine}` -- a member's two folders (the 已发布 folder
    and their own proposals folder, two shortcuts on a personal OneDrive),
    each checked before use (`researchos.share.set_member_folders`); and
    `{member, area}` -- the PI records where a member's area is when it is
    not under 提议/ (`researchos.share.set_member_area`)."""
    from researchos import exchange as exchange_mod  # noqa: PLC0415
    from researchos import proposals as proposals_mod  # noqa: PLC0415
    from researchos import share  # noqa: PLC0415

    if "published" in body or "mine" in body:
        published, mine = body.get("published"), body.get("mine")
        if not all(isinstance(v, str) and v.strip() for v in (published, mine)):
            raise NodeApiRefused(400, "missing", "request body must carry string 'published' and 'mine'",
                                 "请选择公盘的已发布文件夹和你自己的提议文件夹")
        if not all(Path(v.strip()).expanduser().is_absolute() for v in (published, mine)):
            raise NodeApiRefused(400, "not_absolute", "'published' and 'mine' must be absolute paths", "请给出完整的文件夹路径")
        return {"ok": True, **_share_call(lambda: share.set_member_folders(
            root, Path(published.strip()).expanduser(), Path(mine.strip()).expanduser()))}
    if "member" in body or "area" in body:
        member, area = body.get("member"), body.get("area")
        if not all(isinstance(v, str) and v.strip() for v in (member, area)):
            raise NodeApiRefused(400, "missing", "request body must carry string 'member' and 'area'", "请选择成员的提议区")
        if not Path(area.strip()).expanduser().is_absolute():
            raise NodeApiRefused(400, "not_absolute", "'area' must be an absolute path", "请给出完整的文件夹路径")
        return {"ok": True, **_share_call(lambda: share.set_member_area(root, member.strip(),
                                                                        Path(area.strip()).expanduser()))}
    shared = body.get("shared")
    if not isinstance(shared, str) or not shared.strip():
        raise NodeApiRefused(400, "missing", "request body must carry a string 'shared'", "请选择公盘文件夹")
    path = Path(shared.strip()).expanduser()
    if not path.is_absolute():
        raise NodeApiRefused(400, "not_absolute", "'shared' must be an absolute path", "请给出完整的文件夹路径")
    if not path.is_dir():
        raise NodeApiRefused(400, "no_folder", f"{path} is not a folder", "找不到这个文件夹")
    role, _me, _team = _proposal_call(lambda: proposals_mod.role_of(root))
    if role in ("member", "none"):
        raise NodeApiRefused(403, "not_pi", "a member's copy has its shared folder from joining", "只有 PI 设置公盘文件夹")
    if not project_id:
        raise NodeApiRefused(409, "no_identity", "the project has no identity", "项目身份文件无法读取")
    done = _proposal_call(lambda: exchange_mod.configure(project_id, path, root))
    return {"ok": True, "shared": str(done)}


def share_join_payload(body: dict[str, Any]) -> dict[str, Any]:
    """POST /api/share/join {shared, folder}: a working copy in a new (or
    empty) folder; refused at a top-level folder, in Research OS's home,
    inside a project, or anywhere not empty."""
    from researchos import share  # noqa: PLC0415

    shared_path, folder_path, project = _paths(body)
    expect = body.get("expect_pi")
    if expect is not None and not isinstance(expect, str):
        raise NodeApiRefused(400, "missing", "'expect_pi' must be text", "内容不对")
    mine = _mine(body)
    return {"ok": True, **_share_call(lambda: share.join(shared_path, folder_path, project=project, expect_pi=expect,
                                                         mine=mine))}

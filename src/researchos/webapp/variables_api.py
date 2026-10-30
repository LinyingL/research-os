"""The 「变量」 view's endpoints (DESIGN.md 9.11 "In the app" and stage (b);
task V5 phase 9). Routed by `researchos.webapp.server`, whose handler runs
`_check_local_origin` first on every request and wraps every write in its
write guard (project lock, the 9.4 re-check, read-only and pre-V5 refusals).

Reads (GET, files first, the index for the trust decision and stage (b)):

    /api/variables                 the cards, one row each
    /api/variables/card?id=        one card: its versions, the log as history,
                                   each check with its result and reason, the
                                   observations, the attestation, problems,
                                   references, the stage-(b) comparison
    /api/variables/code?id=&entry= 「查看当时的代码」: the code copy the entry
                                   names, as text
    /api/variables/frozen?id=&entry=  the frozen copy the entry names, as text

Writes (POST) -- ONLY what Research OS authors, each through `researchos.records.cards`
(`via="app"`, `expected_id` = the served id). Never the researcher's text:
these actions do not write definitions. A separate human action,
`POST /api/outputs/variable`, copies a registered agent definition into a
new card draft; confirmation still uses the existing human action below.

    /api/variables/confirm      {id, attested: yes|no|unknown, content_hash}
    /api/variables/new          {id}                       「新建变量卡」: the same code as `researchos variable new`
                                (`cards.new_card`), the draft v1.toml from the template; the 21.7 refusals worded
    /api/variables/revise       {id}                       (also 「口径已变」)
    /api/variables/reaffirm     {items: [{id, version, signature, data_version?}], note?}
                                「口径未变」 / 「全部口径未变」: one entry per card
    /api/variables/abandon      {id, note}
    /api/variables/revive       {id, note}
    /api/variables/answer       {id, question: "edited", answer: new|correct, version, content_hash}
                                {id, question: "shrunk", answer: file|restore, missing: [ids shown]}
    /api/variables/full-compare {id}                       「完整比对」 (the index only)
    /api/variables/settle       {id, keeps: [entry ids], shown: [entry ids the page showed]}
                                「以这一条为准」 (9.12): two merged histories settled
                                by naming, per version in dispute, what stands

`content_hash` is the hash the page showed for the version file (the
draft's, or the edited file's `content_hash`; null for a file that could
not be read): 9.12, "an answer belongs to the question that was shown" --
if the file differs now, the act is refused (`card_question_changed`) and
the page shows the file again. The attestation is recorded against the
text the researcher saw, never against what reached the disk after.

Paths: a card id is only ever compared with the names `.researchos/variables/`
lists (never joined into a path); a copy named by a log entry is resolved
and must stay inside `.researchos/variables/_code/` (code) or the card's
`frozen/` (frozen) -- resolve, then `relative_to` -- before it is read.
"""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection
from typing import Any

from researchos import settings
from researchos.records import cards
from researchos.records import implementation
from researchos.records import variables as V
from researchos.records.identity import IdentityState, read_identity

#: Above this a kept copy is cut (and the page says so); scripts are small.
TEXT_LIMIT = 1024 * 1024

# 21.7 「新建变量卡」: the reasons `researchos variable new` refuses with (`cards.new_card`'s `reason`), each worded in
# the request's language (13.6) as (zh, en); the engine's own sentence stays behind 「详情」.
NEW_REFUSED = {
    "id_length": ("编号要有 1 到 64 个字符。", "An id is 1 to 64 characters long."),
    "id_start": ("编号不能以下划线、点或短横线开头。", "An id may not start with an underscore, a dot or a hyphen."),
    "id_char": ("编号只能用字母、数字、下划线、短横线和点，不能含「{char}」。",
                "An id may hold only letters, digits, underscores, hyphens and dots, not “{char}”."),
    "id_space": ("编号里不能有空格。", "An id may not hold a space."),
    "id_dots": ("编号里不能有连续的两个点。", "An id may not hold two dots in a row."),
    "taken": ("已经有一张叫「{id}」的变量卡了（编号不分大小写）。", "A variable card called “{id}” already exists (ids are not case-sensitive)."),
    "index_holds": ("索引里还留着变量卡「{id}」，请先回答它的问题，再用这个编号。",
                    "The index still holds a variable card called “{id}”. Answer its question first, then use the id."),
    "snapshots": ("「{id}」这个编号以前用过，留有备份，不能再用。", "The id “{id}” was used before and its backups remain, so it cannot be used again."),
    "no_identity": ("项目的身份文件读不出来，什么都没有写。请先恢复它。",
                    "The identity file of the project cannot be read, so nothing was written. Restore it first."),
    "unlistable": ("变量卡所在的文件夹读不了，什么都没有写。", "The folder of the variable cards cannot be listed, so nothing was written."),
    "unsafe": ("没能在项目里建变量卡的文件夹（项目文件夹不在了，或者那个位置不是文件夹、指向了项目之外），什么都没有写。",
               "The folder for variable cards could not be made in the project (the project folder is gone, or that place is not a folder or leads outside the project), so nothing was written."),
}

# 8.8: the sentence for each refusal code that carries no sentence of its own.
REFUSED_ZH = {
    "invalid": "这个请求无法完成，卡片的内容或请求本身有问题",
    "exists": "已经有同名的变量卡",
    "no_such_card": "找不到这张变量卡，可能刚被移走或改名",
    "untrusted": "变量卡的记录文件当前无法读取，请先恢复它",
    "no_index": "项目的索引不在，请重新打开项目",
    "no_draft": "这张卡没有可以确认的草稿",
    "incomplete": "草稿还不完整：确认需要名称、含义、单位、粒度、至少一个输入、构建公式和决策理由",
    "unresolvable": "引用暂不可解析",
    "draft_open": "已有草稿，请先确认它",
    "no_question": "现在已经没有需要回答的问题了",
    "question_open": "这一版在确认后被改动了，请先回答那个问题",
    "copy_missing": "确认记录引用的副本缺失",
    "nothing_confirmed": "这张卡还没有确认过的版本，请直接编辑草稿",
    "already_abandoned": "这张卡已经弃用了",
    "not_abandoned": "这张卡没有被弃用",
    "would_lose": "记录文件当前无法使用，以文件为准会丢掉仅存的记录副本；请先恢复文件，或选择「把缺少的补回文件」",
    "question_changed": "文件在提问之后又变了，请重新查看再回答",
    "changed": "文件在写入时被改动了，没有记录任何东西；请重试",
    "region_missing": "找不到这一版指定的代码块或函数",
    "not_confirmed": "只有确认过的版本才能被引用",
    "no_conflict": "这张卡的记录里没有需要处理的两份历史",
}

STATUS = {"invalid": 400, "incomplete": 400, "no_such_card": 404}


class CardActionRefused(Exception):
    """Nothing was written. `state` is `card_<code>`; `message_zh` the
    product sentence the page shows (the engine's English stays behind
    「详情」)."""

    def __init__(self, message: str, code: str, message_zh: str | None, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = STATUS.get(code, 409)
        self.state = "card_" + code
        self.message_zh = message_zh or REFUSED_ZH.get(code)
        self.extra = {"message_zh": self.message_zh, **(extra or {})}


def _refused(exc: V.VariableError) -> CardActionRefused:
    extra = None
    decision = getattr(exc, "decision", None)
    if decision is not None:
        extra = {"records": {"state": decision.verdict.value, "reason": decision.reason, "detail": decision.detail,
                             "missing": [cards._summary(m) for m in decision.missing]}}
    return CardActionRefused(str(exc), exc.code, exc.message_zh, extra)


def _bad(message: str) -> CardActionRefused:
    return CardActionRefused(message, "invalid", None)


def _card_id(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise _bad("'id' must name a variable card")
    return value


# -- reads -------------------------------------------------------------------------------------


def _name(card_payload: dict[str, Any]) -> str:
    versions = {v["version"]: v for v in card_payload.get("versions") or []}
    for number in (card_payload.get("in_use"), card_payload.get("draft"), *sorted(versions, reverse=True)):
        content = (versions.get(number) or {}).get("content") if number is not None else None
        if isinstance(content, dict) and isinstance(content.get("name"), str) and content["name"].strip():
            return content["name"]
    return card_payload["id"]


def _identity(root: Path):
    got = read_identity(root)
    return got.identity if got.state is IdentityState.PRESENT else None


def _full(conn: Connection | None, root: Path, card: V.Card, dead: list[dict[str, Any]],
          lang: str = "zh") -> dict[str, Any]:
    decision = cards.assess_card(conn, root, card, _identity(root))
    out = cards.card_payload(root, card, decision, dead)
    out["name"] = _name(out)
    for version in out["versions"]:
        ref = version.get("reference")
        entry = card.versions[version["version"]].entry
        if ref:
            resolved = V.resolve(root, V.Reference(ref["variable"], ref["version"], ref["entry"], ref["content"]))
            ref["resolves"] = resolved is not None and not resolved.copy_missing
        version["upstream"] = []
        for item in (entry.get("upstream") or []) if entry is not None else []:
            if not isinstance(item, dict):
                continue
            pinned = V.Reference(str(item.get("variable")), int(item.get("version") or 0), str(item.get("entry")),
                                 str(item.get("content")))
            resolves = V.resolve(root, pinned) is not None
            version["upstream"].append({"ref": pinned.label, "resolves": resolves,
                                        "unresolvable": None if resolves else V.UNRESOLVABLE})
    out["implementation"] = implementation.stored(conn).get(card.id) if card.readable else None
    _name_authors(root, card, out, lang)
    return out


def _name_authors(root: Path, card: V.Card, out: dict[str, Any], lang: str = "zh") -> None:
    """12.13: every entry of the card's log names its author as the team
    (or me.toml) names them -- `by_label`, never the raw id; an entry
    without `by` is the first researcher's, in `lang` (13.7) -- and who
    approved it."""
    from researchos import people  # noqa: PLC0415

    team = people.read_team(root)
    known = team if team.state == "present" else None
    for item, entry in zip(out.get("history") or [], card.entries):
        item["by"] = entry.data.get("by")
        item["by_label"] = people.attribution(entry.data, known, lang)["label"]
        approved = entry.data.get("approved_by")
        if isinstance(approved, str) and approved:
            item["approved_by_label"] = people.attribution({"by": approved}, known, lang)["label"]


def list_payload(conn: Connection | None, root: Path) -> dict[str, Any]:
    """`GET /api/variables`: one row per card (and per card the index knows
    whose directory is gone)."""
    reviews = implementation.stored(conn)
    rows = []
    for card in cards.overview(conn, root):
        review = reviews.get(card["id"]) if card["state"] == "ok" else None
        trust = card.get("trust") or {}
        rows.append({
            "id": card["id"],
            "name": _name(card),
            "state": card["state"],
            "reason": card.get("reason"),
            "message": card.get("message") or trust.get("message"),
            "trust_state": trust.get("state"),
            "in_use": card["in_use"],
            "draft": card["draft"],
            "abandoned": card["abandoned"] is not None,
            "questions": len(card["questions"]),
            "problems": len(card["problems"]),
            "dead_flags": card["dead_flags"],
            "under_review": bool(review and review.get("under_review")),
            "review_labels": [r["label"] for r in (review or {}).get("reasons") or []],
        })
    from researchos import outputs

    pending = outputs.catalog(root, {"variable"})
    known = {r["id"].casefold() for r in rows}
    pending["items"] = [i for i in pending["items"] if str(i.get("variable_card") or "").casefold() not in known]
    return {"cards": rows, "outputs": pending}


def _open(root: Path, card_id: str) -> V.Card:
    found = V.find_card_dirs(root, card_id)
    if not found:
        raise CardActionRefused(f"there is no variable card {card_id!r}", "no_such_card", None)
    return V.read_card(root, found[0])


def card_payload(conn: Connection | None, root: Path, card_id: str, lang: str = "zh") -> dict[str, Any]:
    """`GET /api/variables/card?id=`: the whole card (module docstring);
    people named in `lang` (13.7)."""
    card = _open(root, _card_id(card_id))
    return _full(conn, root, card, cards.dead_variable_flags(root, [card]), lang)


def _entry(card: V.Card, entry_id: Any):
    if not isinstance(entry_id, str) or not entry_id:
        raise _bad("'entry' must name a log entry")
    entry = card.log.ledger.by_id(entry_id) if card.log is not None and card.log.ledger is not None else None
    if entry is None:
        raise CardActionRefused(f"{card.id} has no log entry {entry_id!r}", "no_such_card", "找不到这条记录")
    return entry


def _confined_text(target: Path, allowed: Path) -> tuple[str, bool]:
    try:
        resolved = target.resolve()
        resolved.relative_to(allowed.resolve())
    except (ValueError, OSError) as exc:
        raise CardActionRefused(f"{target} is outside {allowed}; refusing", "invalid", "这个副本不在它应在的位置，不予读取") from exc
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise CardActionRefused(f"{target}: {exc}", "copy_missing", None) from exc
    return data[:TEXT_LIMIT].decode("utf-8", "replace"), len(data) > TEXT_LIMIT


def code_payload(root: Path, card_id: str, entry_id: str) -> dict[str, Any]:
    """「查看当时的代码」: the code copy entry `entry_id` names."""
    card = _open(root, _card_id(card_id))
    entry = _entry(card, entry_id)
    script = (entry.get("checked") or {}).get("script") or {}
    copy = script.get("copy") if isinstance(script, dict) else None
    if not isinstance(copy, str) or not copy:
        raise CardActionRefused(f"entry {entry_id} kept no code copy", "copy_missing", "这条记录没有保存代码副本")
    text, truncated = _confined_text(V.variables_dir(root) / copy, V.code_dir(root))
    return {"id": card.id, "entry": entry.id, "act": entry.get("act"), "version": entry.get("version"),
            "at": entry.at, "copy": copy, "sha256": script.get("sha256"), "text": text, "truncated": truncated}


def frozen_payload(root: Path, card_id: str, entry_id: str) -> dict[str, Any]:
    """The frozen copy entry `entry_id` names (a version as confirmed or
    corrected)."""
    card = _open(root, _card_id(card_id))
    entry = _entry(card, entry_id)
    frozen = entry.get("frozen")
    if not isinstance(frozen, str) or not frozen:
        raise CardActionRefused(f"entry {entry_id} names no frozen copy", "copy_missing", None)
    text, truncated = _confined_text(card.directory / frozen, card.directory / V.FROZEN_DIRNAME)
    return {"id": card.id, "entry": entry.id, "version": entry.get("version"), "frozen": frozen, "text": text,
            "truncated": truncated}


# -- writes ------------------------------------------------------------------------------------

ACTIONS = ("new", "confirm", "revise", "reaffirm", "abandon", "revive", "answer", "full-compare", "settle")


def _note(body: dict[str, Any], *, required: bool) -> str | None:
    note = body.get("note")
    if note is None and not required:
        return None
    if not isinstance(note, str) or (required and not note.strip()):
        raise CardActionRefused("a reason is required in 'note'", "invalid", "请写下理由")
    return note


def _shown(body: dict[str, Any]) -> str | None:
    """The version file's content hash as the page showed it (required)."""
    if "content_hash" not in body:
        raise _bad("'content_hash' must carry the content hash of the version file the page showed")
    shown = body["content_hash"]
    if shown is not None and (not isinstance(shown, str) or not shown.startswith(V.CONTENT_PREFIX)):
        raise _bad("'content_hash' must be sha256:<hex> or null")
    return shown


def act(root: Path, project_id: str | None, action: str, body: dict[str, Any], lang: str = "zh") -> dict[str, Any]:
    """One POST action (module docstring), its refusals worded in `lang` where the engine words them (`new`). The
    caller holds the server's write guard; every write takes the project lock itself as well."""
    if action not in ACTIONS:
        raise CardActionRefused(f"no such action: {action}", "invalid", None)
    common = {"expected_id": project_id, "timeout": 10.0}
    try:
        if action == "new":
            return _new(root, body, common, lang)
        if action == "reaffirm":
            return _reaffirm(root, body, common)
        card_id = _card_id(body.get("id"))
        if action == "confirm":
            attested = body.get("attested")
            if attested not in V.ATTESTED:
                raise _bad("'attested' must be yes, no or unknown")
            done = cards.confirm(root, card_id, attested=attested, expected_content=_shown(body), via="app", **common)
            return {"ok": True, "id": done.reference.variable, "version": done.entry.get("version"),
                    "entry": done.entry.id, "reference": done.reference.label}
        if action == "revise":
            path = cards.revise(root, card_id, **common)
            return {"ok": True, "id": card_id, "file": path.relative_to(root).as_posix(),
                    "version": int(path.stem[1:])}
        if action in ("abandon", "revive"):
            fn = cards.abandon if action == "abandon" else cards.revive
            entry = fn(root, card_id, note=_note(body, required=True), via="app", **common)
            return {"ok": True, "id": card_id, "entry": entry.id}
        if action == "full-compare":
            review = cards.full_compare(root, card_id, **common)
            return {"ok": True, "id": card_id, "implementation": review}
        if action == "settle":
            keeps, shown = body.get("keeps"), body.get("shown")
            for name, value in (("keeps", keeps), ("shown", shown)):
                if not isinstance(value, list) or not value or len(value) > 500 or not all(isinstance(i, str) and i for i in value):
                    raise _bad(f"'{name}' must list entry ids")
            done = cards.settle(root, card_id, keeps, expected_shown=shown, note=_note(body, required=False),
                                via="app", **common)
            return {"ok": True, "id": card_id, "entry": done.entry.id, "keeps": list(done.keeps),
                    "not_in_force": list(done.not_in_force), "questions": list(done.card.questions)}
        return _answer(root, card_id, body, common)
    except V.VariableError as exc:
        raise _refused(exc) from exc


def _new(root: Path, body: dict[str, Any], common: dict[str, Any], lang: str) -> dict[str, Any]:
    """「新建变量卡」 (21.7): `cards.new_card`, the one function `researchos variable new` calls, with the same id
    rules and the same exclusive creation; a refusal is worded in `lang`. The card's log gets no entry: it begins at
    the draft's confirmation, as it does for the command."""
    card_id = body.get("id")
    if not isinstance(card_id, str):
        raise _bad("'id' must be the new card's id")
    try:
        path = cards.new_card(root, card_id, **common)
    except cards.CardRefused as exc:
        reason = "id_space" if exc.reason == "id_char" and exc.values.get("char", "").isspace() else exc.reason or ""
        words = NEW_REFUSED.get(reason)
        if words is None:
            raise
        raise CardActionRefused(str(exc), exc.code, settings.say(*words, lang, **exc.values)) from exc
    return {"ok": True, "id": card_id, "file": path.relative_to(root).as_posix(), "version": 1}


def _reaffirm(root: Path, body: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    items = body.get("items")
    if not isinstance(items, list) or not items or len(items) > 200:
        raise _bad("'items' must list the cards answered")
    note = _note(body, required=False)
    done: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("version"), int) or not isinstance(item.get("signature"), str):
            raise _bad("each item needs id, version and signature")
        data_version = item.get("data_version")
        if data_version is not None and not isinstance(data_version, str):
            raise _bad("'data_version' must be text")
        card_id = _card_id(item.get("id"))
        try:
            entry = cards.reaffirm(root, card_id, version=item["version"], signature=item["signature"],
                                   data_version=data_version, note=note, via="app", **common)
        except V.VariableError as exc:
            # Cards answered before this one stay answered (each is its own
            # record); the page is told which.
            refused = _refused(exc)
            refused.extra["done"] = done
            refused.extra["card"] = card_id
            raise refused from exc
        done.append({"id": card_id, "entry": entry.id})
    return {"ok": True, "done": done}


def _answer(root: Path, card_id: str, body: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    question, answer = body.get("question"), body.get("answer")
    if question == "edited":
        version = body.get("version")
        if not isinstance(version, int):
            raise _bad("'version' must name the version asked about")
        done = cards.answer_edited(root, card_id, answer, version=version, expected_content=_shown(body),
                                   via="app", **common)
        return {"ok": True, "id": card_id, "answer": done.answer, "version": done.version,
                "new_version": done.new_version, "entry": None if done.entry is None else done.entry.id}
    if question == "shrunk":
        shown = body.get("missing")
        if not isinstance(shown, list) or not all(isinstance(i, str) and i for i in shown):
            raise _bad("'missing' must carry the ids of the missing entries the question showed")
        done = cards.answer_shrunk(root, card_id, answer, expected_missing=shown, via="app", **common)
        return {"ok": True, "id": card_id, "answer": done.answer, "missing": len(done.missing),
                "appended": len(done.appended)}
    raise _bad("'question' must be 'edited' or 'shrunk'")

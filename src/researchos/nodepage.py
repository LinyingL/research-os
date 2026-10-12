"""The node page, 成果 and 数据与代码 (DESIGN.md 12.6, 12.7, 12.9; task V8 U3).

Three read-only payloads for the page, built from the index, the record and
the files -- nothing here writes:

- `node(conn, root, node)` -- one object's page: what it is (type in words,
  name, path, whether it is on disk), its trace drawn as columns (12.7: 数据
  | 脚本 | 中间数据 | 脚本 | 本页 | 被引用) with the lines that join them,
  every break as a gap with its repair, the attempts it belongs to and the
  variable versions it uses, and the links touching it (for 判断). The
  same shape for every kind of node -- a script, a data file, a figure, a
  draft section or claim, an attempt, a variable card, a reference -- so
  the page has one renderer. Choices for the two repairs come with it:
  the project's scripts for 「补上来源」 (a `generates` / `writes` mapping)
  and its attempts for 「关联文件」 (a `belongs_to` mapping), or, on an
  attempt's page, its files.
- `results(conn, root, draft=None)` -- 成果: the figures and tables the
  drafts include, grouped by draft section, figures no draft includes, and
  the claims of the drafts marked 「设为论文正文」 only (12.13,
  `researchos.records.papers`: a draft's revision log or review notes hold
  numbers that are not the paper's claims) -- per draft, on request: a
  draft can hold hundreds -- each with its trace state 完整 / 缺来源 /
  缺尝试. Every draft is listed with whether it is marked, so the page
  can offer the mark on its line.
- `data_code(conn, root)` -- 数据与代码: every script and data file with what
  it reads and writes, and which of the lineage report's blocks it is in
  (无来源的输入 / 断链 / 同名拷贝), so the page can filter.

The page's copy is Chinese (8.8) and says relations in words (12.11): 读取,
写出, 生成, 引用, 属于 -- never an arrow.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from researchos import db, lineage
from researchos import trace as trace_mod
from researchos.records import judgements
from researchos.records import notes as notes_mod

TYPE_WORDS = {
    "script": "脚本", "dataset": "数据", "figure": "图表", "file": "文件", "attempt": "尝试",
    "variable": "变量", "section": "文稿章节", "claim": "论断", "reference": "文献", "ref": "文献",
    "experiment": "实验记录", "commit": "提交",
}
RELATION_WORDS = {
    "reads": "读取", "writes": "写出", "generates": "生成", "includes": "引用", "cites": "引用",
    "belongs_to": "属于", "backed_by": "依据", "produces": "产出", "implements": "实现", "uses": "使用",
    "supports": "支持", "authored_by": "作者是", "derives_from": "源自",
}
#: A relation no word above names (a newer engine's) -- never its English type on screen (8.8).
RELATION_FALLBACK = "关联"
TRACE_STATES = {"complete": "完整", "no_source": "缺来源", "no_attempt": "缺尝试"}
SOURCE_BREAKS = ("no_generating_script", "script_missing", "data_missing", "script_unreadable")
MAX_COLUMNS = 6           # upstream levels drawn; deeper ones are said, not drawn
LINK_LIMIT = 300          # links listed under 判断
CLAIM_LIMIT = 2000        # claims of one draft
FILE_NODE_TYPES = ("script", "dataset", "figure", "file")


class NotFound(LookupError):
    """No such node, file, card or reference."""


# -- small helpers ----------------------------------------------------------------------------


def _type_of(node_id: str) -> str:
    return node_id.partition(":")[0]


def _exists(root: Path, rel: str | None) -> bool:
    return rel is not None and (root / rel).exists()


def _file_of(node_id: str) -> str | None:
    """The project-relative file a node lives in: its own path, or the
    draft of a section or claim."""
    path = notes_mod.node_path(node_id)
    if path is not None:
        return path
    prefix, _, rest = node_id.partition(":")
    if prefix in ("section", "claim") and "#" in rest:
        return rest.rsplit("#", 1)[0]
    return None


def _label(conn: Connection, node_id: str, record: dict[str, Any] | None = None) -> str:
    """What a reader calls a node: a file's name, an attempt's number and
    title, a section's heading, a claim's sentence, a reference's title."""
    path = notes_mod.node_path(node_id)
    if path is not None:
        return posixpath.basename(path) or path
    record = record if record is not None else db.get_node(conn, node_id)
    prefix = _type_of(node_id)
    if prefix == "attempt" and record is not None:
        number = str(record["attrs"].get("number") or "").lstrip("#")
        title = record.get("title") or record["attrs"].get("description") or ""
        return " ".join(x for x in (f"#{number}" if number else "", title) if x)
    if prefix == "claim" and record is not None:
        return str(record["attrs"].get("sentence") or record.get("title") or node_id)
    if record is not None and record.get("title"):
        return str(record["title"])
    if prefix == "variable":
        return node_id.partition(":")[2]
    return node_id.partition(":")[2] or node_id


def _item(conn: Connection, root: Path, node_id: str, **extra: Any) -> dict[str, Any]:
    record = db.get_node(conn, node_id)
    path = notes_mod.node_path(node_id)
    out = {
        "id": node_id, "type": _type_of(node_id), "type_label": TYPE_WORDS.get(_type_of(node_id), "条目"),
        "label": _label(conn, node_id, record), "path": path,
        "exists": _exists(root, path) if path is not None else record is not None,
    }
    file = _file_of(node_id)
    if path is None and file is not None:
        out["file"] = file
    out.update(extra)
    return out


def _line(edge: dict[str, Any], flags: judgements.LinkFlags, *, frm: str, to: str) -> dict[str, Any]:
    marks = flags.for_key(judgements.key_of(edge))
    return {
        "from": frm, "to": to, "type": edge["type"], "relation": RELATION_WORDS.get(edge["type"], RELATION_FALLBACK),
        "human": edge["extractor"] == "mapping", "status": edge["status"],
        "review": bool(marks.get("review") or marks.get("conflict")),
    }


def _live(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in edges if e["status"] != "rejected"]


def _verdict(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    from researchos import proposals  # noqa: PLC0415

    verdict = (record.get("human_fields") or {}).get("verdict", "")
    return attempts_ingest.route_status(verdict, attempts_ingest.verdict_labels(root), proposals.abandon_markers(root))


def _attempt_choices(conn: Connection, root: Path) -> list[dict[str, Any]]:
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    labels = attempts_ingest.verdict_labels(root)
    out = []
    for a in db.get_nodes_by_type(conn, "attempt"):
        verdict = (a.get("human_fields") or {}).get("verdict", "")
        out.append({"id": a["id"], "number": a["attrs"].get("number"), "title": a.get("title") or a["attrs"].get("description"),
                    "verdict_status": attempts_ingest.verdict_status(verdict, labels),
                    "_key": attempts_ingest.attempt_sort_key(str(a["attrs"].get("number") or ""))})
    out.sort(key=lambda a: a.pop("_key"))
    return out


def _file_choices(conn: Connection, root: Path, types: tuple[str, ...]) -> list[dict[str, Any]]:
    out = []
    for t in types:
        for n in db.get_nodes_by_type(conn, t):
            path = notes_mod.node_path(n["id"])
            if path is not None:
                out.append({"id": n["id"], "type": t, "type_label": TYPE_WORDS[t], "path": path})
    return sorted(out, key=lambda f: f["path"])


# -- the links touching a node (判断) ---------------------------------------------------------


def _team(root: Path) -> Any:
    from researchos import people  # noqa: PLC0415

    try:
        team = people.read_team(root)
    except Exception:  # noqa: BLE001 -- a name is a nicety; the id stays
        return None
    return team if team.state == "present" else None


def _person(team: Any, member: str) -> str:
    """A person as every view names them (12.13): by their name."""
    from researchos import people  # noqa: PLC0415

    return people.attribution({"by": member}, team)["label"]


def _links(conn: Connection, node_id: str, flags: judgements.LinkFlags,
           root_people: Any = None) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for edge in db.query_edges(conn, src=node_id) + db.query_edges(conn, dst=node_id):
        key = judgements.key_of(edge)
        if key in seen:
            continue
        other = edge["dst"] if edge["src"] == node_id else edge["src"]
        item = {k: edge[k] for k in ("src", "dst", "type", "extractor", "status")}
        item.update(flags.for_key(key))
        item["human"] = edge["extractor"] == "mapping"
        item["relation"] = RELATION_WORDS.get(edge["type"], RELATION_FALLBACK)
        item["direction"] = "out" if edge["src"] == node_id else "in"
        item["src_label"] = _label(conn, edge["src"])
        item["dst_label"] = _label(conn, edge["dst"])
        item["other"] = {"id": other, "type": _type_of(other), "path": notes_mod.node_path(other)}
        if item["human"]:
            # 12.13: the note written with 「关联文件」 / 「补上来源」 goes into the
            # node's change timeline with the link -- its note, its date, who drew it.
            drawn = (edge["evidence"] or {}).get("mapping") if isinstance(edge["evidence"], dict) else None
            if isinstance(drawn, dict):
                item["mapping"] = {k: drawn.get(k) for k in ("note", "date", "by") if drawn.get(k)}
                if drawn.get("by"):
                    item["mapping"]["by_label"] = _person(root_people, drawn["by"])
        occurrences = trace_mod._occurrences(edge["evidence"])
        item["evidence"] = [{"file": o.get("file"), "line": o.get("line"), "callee": o.get("callee")}
                            for o in occurrences[:5] if o.get("line") is not None]
        seen[key] = item
        if len(seen) >= LINK_LIMIT:
            break
    order = {"pending": 0, "auto": 1, "confirmed": 2, "rejected": 3}
    return sorted(seen.values(), key=lambda i: (not (i.get("review") or i.get("conflict")), i["human"],
                                                order.get(i["status"], 1), i["type"], i["src"], i["dst"]))


# -- the trace as columns (12.7) ---------------------------------------------------------------


def _columns_from_chain(conn: Connection, root: Path, node_id: str, chain: dict[str, Any],
                        flags: judgements.LinkFlags) -> dict[str, Any]:
    """A file node's chain laid out by depth: the node at depth 0, the
    scripts that made it at 1, what they read at 2, and so on upstream;
    downstream, the sections that include it (被引用), a data file's
    readers (被读取) and a script's outputs (写出)."""
    start = _type_of(node_id)
    by_depth: dict[int, list[dict[str, Any]]] = {}
    lines: list[dict[str, Any]] = []
    for script in chain["scripts"]:
        if script["id"] == node_id:
            continue
        extra = {}
        if script["scan_status"] in ("unreadable", "unparseable"):
            extra["unreadable"] = True
        by_depth.setdefault(script["depth"], []).append(_item(conn, root, script["id"], **extra))
    for data in chain["data"]:
        if data["id"] == node_id:
            continue
        by_depth.setdefault(data["depth"], []).append(_item(conn, root, data["id"], raw=data["raw"]))
    deepest = max(by_depth, default=0)
    shown = sorted(d for d in by_depth if d <= MAX_COLUMNS)
    hidden = sum(len(by_depth[d]) for d in by_depth if d > MAX_COLUMNS)
    on_screen = {i["id"] for d in shown for i in by_depth[d]} | {node_id}

    breaks = list(chain["breaks"])
    if start == "file":
        # A draft or a document is written by hand; the mapping grammar has
        # no source for it -- the break is said, without a repair it cannot take.
        breaks = [{k: v for k, v in b.items() if k != "repair"} for b in breaks]
    gap = next((b for b in breaks if b["kind"] == "no_generating_script"), None)
    if gap is not None and 1 not in shown:
        shown = [1, *shown]
        by_depth.setdefault(1, [])

    columns: list[dict[str, Any]] = []
    data_depths = [d for d in shown if by_depth.get(d) and all(i["type"] != "script" for i in by_depth[d])]
    raw_depth = max(data_depths, default=None)
    for depth in sorted(shown, reverse=True):
        items = sorted(by_depth.get(depth, []), key=lambda i: i["path"] or i["id"])
        is_script = (depth % 2 == 1) if start != "script" else (depth % 2 == 0)
        if items:
            is_script = items[0]["type"] == "script"
        title = "脚本" if is_script else ("数据" if depth == raw_depth else "中间数据")
        column: dict[str, Any] = {"key": f"d{depth}", "title": title, "items": items}
        if depth == 1 and gap is not None:
            column["gap"] = gap
        columns.append(column)

    self_item = _item(conn, root, node_id, self=True)
    columns.append({"key": "self", "title": "本页", "items": [self_item]})

    for link in chain["links"]:
        if link["type"] == "reads" and link["src"] in on_screen and link["dst"] in on_screen:
            lines.append(_line(link, flags, frm=link["dst"], to=link["src"]))
        elif link["type"] in trace_mod.PRODUCER_TYPES:
            script = link["src"] if link["src"].startswith("script:") else None
            if script is None:  # a commit's `generates`: the script its evidence names
                for s in chain["scripts"]:
                    if link["dst"] in s["writes"]:
                        script = s["id"]
                        break
            if script in on_screen and link["dst"] in on_screen:
                lines.append(_line(link, flags, frm=script, to=link["dst"]))

    downstream: list[dict[str, Any]] = []
    if start == "script":
        outs = []
        for edge in _live(db.query_edges(conn, src=node_id)):
            if edge["type"] in trace_mod.PRODUCER_TYPES:
                outs.append(_item(conn, root, edge["dst"]))
                lines.append(_line(edge, flags, frm=node_id, to=edge["dst"]))
        if outs:
            downstream.append({"key": "out", "title": "写出", "items": _dedupe(outs)})
    if start == "dataset":
        readers = []
        for edge in _live(db.query_edges(conn, dst=node_id, type="reads")):
            readers.append(_item(conn, root, edge["src"]))
            lines.append(_line(edge, flags, frm=node_id, to=edge["src"]))
        if readers:
            downstream.append({"key": "readers", "title": "被读取", "items": _dedupe(readers)})
    cited = []
    for draft in chain["drafts"]:
        cited.append(_item(conn, root, draft["section"], file=draft["file"]))
        lines.append({"from": node_id, "to": draft["section"], "type": "includes", "relation": "引用",
                      "human": draft["extractor"] == "mapping", "status": "auto", "review": False})
    if cited:
        downstream.append({"key": "cited", "title": "被引用", "items": _dedupe(cited)})
    columns.extend(downstream)
    return {
        "columns": columns, "lines": _dedupe_lines(lines), "deeper": hidden, "deepest": deepest,
        "breaks": breaks, "attempts": chain["attempts"], "variables": chain["variables"],
    }


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for item in items:
        if item["id"] not in seen:
            seen.add(item["id"])
            out.append(item)
    return sorted(out, key=lambda i: i.get("path") or i.get("file") or i["id"])


def _dedupe_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for line in lines:
        key = (line["from"], line["to"], line["type"])
        if key not in seen:
            seen.add(key)
            out.append(line)
    return out


def _attempt_trace(conn: Connection, root: Path, record: dict[str, Any], flags: judgements.LinkFlags) -> dict[str, Any]:
    """An attempt's page: its files -- the step scripts its description
    names and the files attached to it (「关联文件」) -- as 读取的数据 | 脚本
    | 产出, with the scripts' reads and writes. Its files are 14.3's one
    membership rule (`trace.attempt_members`), as on the desk and in 关系图."""
    node_id = record["id"]
    members = trace_mod.attempt_members(conn, root, [record])[node_id]
    inputs, scripts, outputs, lines = [], [], [], []
    for member, how in members.items():
        item = _item(conn, root, member, member=how["via"], attached="link" in how)
        if "link" in how:
            item["link"] = how["link"]
        kind = item["type"]
        if kind == "script":
            scripts.append(item)
            for edge in _live(db.query_edges(conn, src=member)):
                if edge["type"] == "reads":
                    inputs.append(_item(conn, root, edge["dst"]))
                    lines.append(_line(edge, flags, frm=edge["dst"], to=member))
                elif edge["type"] in trace_mod.PRODUCER_TYPES:
                    outputs.append(_item(conn, root, edge["dst"]))
                    lines.append(_line(edge, flags, frm=member, to=edge["dst"]))
        elif kind == "figure":
            outputs.append(item)
        else:
            inputs.append(item)
    for column in (inputs, outputs):  # a member keeps its member marks over the plain copy
        marked = {i["id"]: i for i in column if i.get("member")}
        column[:] = [marked.get(i["id"], i) for i in column]
    columns = [
        {"key": "in", "title": "读取的数据", "items": _dedupe(inputs)},
        {"key": "scripts", "title": "脚本", "items": _dedupe(scripts)},
        {"key": "out", "title": "产出", "items": _dedupe(outputs)},
    ]
    breaks = []
    if not members:
        breaks.append({"kind": "no_files", "node": node_id, "path": None, "label": "这条尝试还没有关联任何文件",
                       "repair": {"type": "belongs_to", "label": "关联文件", "file": None}})
        columns[1]["gap"] = breaks[0]
    return {"columns": columns, "lines": _dedupe_lines(lines), "deeper": 0, "breaks": breaks, "attempts": [],
            "variables": []}


def _variable_trace(conn: Connection, root: Path, card: Any, node_id: str) -> dict[str, Any]:
    """A variable's page: the inputs its definition names, the script that
    builds it and the file it is written to (9.11's [implementation]), as
    数据 | 脚本 | 输出 | 本页 -- from the version in use, else the newest."""
    number = card.in_use if card.in_use is not None else (max(card.versions) if card.versions else None)
    content = (card.versions[number].content or {}) if number is not None else {}
    impl = content.get("implementation") or {}
    inputs, lines = [], []

    def file_item(rel: Any) -> dict[str, Any] | None:
        if not isinstance(rel, str) or not rel.strip():
            return None
        try:
            return _item(conn, root, notes_mod.normalize_node(rel))
        except ValueError:
            return None

    for entry in content.get("input") or []:
        if not isinstance(entry, dict):
            continue
        item = file_item(entry.get("dataset"))
        if item is None and isinstance(entry.get("variable"), str):
            item = {"id": f"variable:{entry['variable']}", "type": "variable", "type_label": "变量",
                    "label": str(entry["variable"]) + (f" 第 {entry.get('version')} 版" if entry.get("version") else ""),
                    "path": None, "exists": True}
        if item is not None:
            inputs.append(item)
    script, output = file_item(impl.get("script")), file_item(impl.get("output"))
    columns = [{"key": "in", "title": "输入", "items": _dedupe(inputs)}]
    if script is not None:
        columns.append({"key": "script", "title": "脚本", "items": [script]})
        for item in inputs:
            lines.append({"from": item["id"], "to": script["id"], "type": "reads", "relation": "读取", "human": False,
                          "status": "card", "review": False})
    if output is not None:
        if impl.get("field"):
            output["note"] = "列 " + str(impl["field"])
        columns.append({"key": "output", "title": "输出", "items": [output]})
        if script is not None:
            lines.append({"from": script["id"], "to": output["id"], "type": "writes", "relation": "写出", "human": False,
                          "status": "card", "review": False})
    self_item = {"id": node_id, "type": "variable", "type_label": "变量", "label": _card_name(card), "path": None,
                 "exists": True, "self": True}
    columns.append({"key": "self", "title": "本页", "items": [self_item]})
    if output is not None:
        lines.append({"from": output["id"], "to": node_id, "type": "defines", "relation": "定义", "human": False,
                      "status": "card", "review": False})
    breaks = []
    if script is None and output is None:
        breaks.append({"kind": "no_implementation", "node": node_id, "path": None,
                       "label": "定义卡还没有写明实现它的脚本和输出文件"})
    return {"columns": columns, "lines": lines, "deeper": 0, "breaks": breaks, "attempts": [], "variables": [],
            "version": number}


def _card_name(card: Any) -> str:
    for number in (card.in_use, *sorted(card.versions, reverse=True)):
        if number is None or number not in card.versions:
            continue
        content = card.versions[number].content
        if isinstance(content, dict) and isinstance(content.get("name"), str) and content["name"].strip():
            return content["name"]
    return card.id


def _text_trace(conn: Connection, root: Path, node_id: str, record: dict[str, Any] | None,
                flags: judgements.LinkFlags) -> dict[str, Any]:
    """A section, a claim or a reference: what it rests on and what cites it."""
    prefix = _type_of(node_id)
    before, after, lines = [], [], []
    for edge in _live(db.query_edges(conn, src=node_id)):
        if edge["type"] in ("includes", "backed_by", "cites"):
            before.append(_item(conn, root, edge["dst"]))
            lines.append(_line(edge, flags, frm=edge["dst"], to=node_id))
    for edge in _live(db.query_edges(conn, dst=node_id)):
        if edge["type"] in ("cites", "includes", "backed_by"):
            after.append(_item(conn, root, edge["src"]))
            lines.append(_line(edge, flags, frm=node_id, to=edge["src"]))
    titles = {"section": ("引用的图表与文献", "被引用"), "claim": ("依据", "被引用"), "reference": ("依据", "被这些地方引用")}
    first, last = titles.get(prefix, titles["reference"] if prefix == "ref" else ("依据", "被引用"))
    columns = []
    if before or prefix == "claim":
        columns.append({"key": "before", "title": first, "items": _dedupe(before)})
    self_item = _item(conn, root, node_id, self=True)
    columns.append({"key": "self", "title": "本页", "items": [self_item]})
    if prefix == "claim" and record is not None and record["attrs"].get("section"):
        section = record["attrs"]["section"]
        after.append(_item(conn, root, section))
        lines.append({"from": node_id, "to": section, "type": "in", "relation": "属于", "human": False,
                      "status": "auto", "review": False})
    if after:
        columns.append({"key": "after", "title": "所在章节" if prefix == "claim" and not any(
            i["type"] != "section" for i in after) else last, "items": _dedupe(after)})
    breaks = []
    if prefix == "claim" and not before:
        breaks.append({"kind": "no_backing", "node": node_id, "path": None, "label": "没有找到支撑这个数字的结果"})
        columns[0]["gap"] = breaks[0]
    return {"columns": columns, "lines": _dedupe_lines(lines), "deeper": 0, "breaks": breaks, "attempts": [],
            "variables": []}


# -- the node page -----------------------------------------------------------------------------


def node(conn: Connection, project_root: str | Path, given: str) -> dict[str, Any]:
    """One node's page (module docstring). `ValueError` for something that
    cannot name a node; `NotFound` for a node nothing knows."""
    root = Path(project_root)
    node_id = notes_mod.normalize_node(given)
    prefix = _type_of(node_id)
    record = db.get_node(conn, node_id)
    path = notes_mod.node_path(node_id)
    flags = judgements.link_flags(conn)
    out: dict[str, Any] = {
        "id": node_id, "type": prefix, "type_label": TYPE_WORDS.get(prefix, "条目"),
        "name": _label(conn, node_id, record), "path": path, "file": _file_of(node_id),
        "exists": _exists(root, path) if path is not None else record is not None,
        "in_index": record is not None, "details": {},
    }
    if prefix == "variable":
        from researchos.records import variables as variables_mod  # noqa: PLC0415

        try:
            card = variables_mod.open_card(root, node_id.partition(":")[2])
        except variables_mod.VariableError as exc:
            raise NotFound(f"no variable card {node_id!r}") from exc
        out.update(name=_card_name(card), exists=True, card=card.id)
        out["details"] = {"card_state": card.state, "in_use": card.in_use,
                          "versions": sorted(card.versions, reverse=True)}
        out["trace"] = _variable_trace(conn, root, card, node_id)
    elif record is None and not (path is not None and out["exists"]):
        raise NotFound(f"no such node or file: {node_id}")
    elif prefix == "attempt":
        attrs = record["attrs"]
        human = record.get("human_fields") or {}
        out["details"] = {
            "number": attrs.get("number"), "date": attrs.get("date"), "description": attrs.get("description"),
            "variables": attrs.get("variables"), "verdict": human.get("verdict", ""),
            "verdict_status": _verdict(root, record), "result": human.get("result", ""),
            "source_file": attrs.get("source_file"), "source_line": attrs.get("source_line"),
            "step_files_broken": attrs.get("step_files_broken") or [],
        }
        # U4: an abandoned route whose approval froze its basis (12.4) offers 「当时的依据」.
        decided, _problem = _decisions(root)
        out["details"]["basis"] = sum(1 for e in decided if e.get("attempt") == node_id and e.get("evidence"))
        out["trace"] = _attempt_trace(conn, root, record, flags)
        out["choices"] = {"files": _file_choices(conn, root, ("script", "dataset", "figure"))}
    elif prefix in FILE_NODE_TYPES:
        if prefix == "file" and path is not None and path in _draft_files(conn):
            # 12.13: a draft's page offers 「设为论文正文」 too.
            out["paper"] = {"draft": path, "marked": path in _marked(root)["drafts"]}
        chain = trace_mod.chain(conn, root, node_id)
        out["trace"] = _columns_from_chain(conn, root, node_id, chain, flags)
        out["choices"] = {"attempts": _attempt_choices(conn, root)}
        if prefix in ("figure", "dataset", "file"):
            out["choices"]["scripts"] = _file_choices(conn, root, ("script",))
        out["details"] = {"made_by": chain["node"].get("made_by", [])}
        out["belongs_to"] = [
            {"attempt": e["dst"], "label": _label(conn, e["dst"]), "human": e["extractor"] == "mapping",
             "link": {k: e[k] for k in ("src", "dst", "type", "extractor")}}
            for e in _live(db.query_edges(conn, src=node_id, type="belongs_to"))
        ]
    else:
        if prefix == "claim":
            out["details"] = {"sentence": record["attrs"].get("sentence"), "line": record["attrs"].get("line")}
        elif prefix in ("reference", "ref"):
            attrs = record["attrs"]
            out["details"] = {k: attrs.get(k) for k in ("doi", "year", "authors", "venue", "zotero_key", "citekey")
                              if attrs.get(k)}
        elif prefix == "section":
            out["details"] = {"level": record["attrs"].get("level")}
        out["trace"] = _text_trace(conn, root, node_id, record, flags)
    out["links"] = _links(conn, node_id, flags, _team(root))
    out["review"] = sum(1 for link in out["links"] if link.get("review") or link.get("conflict"))
    return out


# -- 成果 (12.9) ----------------------------------------------------------------------------------


def _trace_state(conn: Connection, root: Path, node_id: str) -> dict[str, Any]:
    chain = trace_mod.chain(conn, root, node_id)
    kinds = [b["kind"] for b in chain["breaks"]]
    if any(k in SOURCE_BREAKS for k in kinds):
        state = "no_source"
    elif "no_attempt" in kinds:
        state = "no_attempt"
    else:
        state = "complete"
    return {"state": state, "label": TRACE_STATES[state], "breaks": [b["label"] for b in chain["breaks"]]}


def _claim_state(conn: Connection, root: Path, claim_id: str, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    backing = [e["dst"] for e in _live(db.query_edges(conn, src=claim_id, type="backed_by"))]
    if not backing:
        return {"state": "no_source", "label": TRACE_STATES["no_source"], "breaks": ["没有找到支撑这个数字的结果"]}
    attempted = False
    for target in backing:
        if target not in cache:
            chain = trace_mod.chain(conn, root, target) if (notes_mod.node_path(target) or db.get_node(conn, target)) else None
            cache[target] = {"attempts": bool(chain and chain.get("attempts"))}
        attempted = attempted or cache[target]["attempts"]
    state = "complete" if attempted else "no_attempt"
    return {"state": state, "label": TRACE_STATES[state], "breaks": [] if attempted else ["还没有归入任何尝试"]}


def results(conn: Connection, project_root: str | Path, draft: str | None = None) -> dict[str, Any]:
    """成果 (module docstring). Without `draft`: the drafts' figures and
    tables by section, figures no draft includes, and per draft how many
    claims it holds. With `draft` (a project-relative file): that draft's
    claims by section."""
    root = Path(project_root)
    if draft is not None:
        return _draft_claims(conn, root, draft)
    groups: dict[str, dict[str, Any]] = {}
    included: set[str] = set()
    for edge in _live(db.query_edges(conn, type="includes")):
        target = edge["dst"]
        if _type_of(target) not in FILE_NODE_TYPES:
            continue
        section = edge["src"]
        group = groups.get(section)
        if group is None:
            record = db.get_node(conn, section)
            group = groups[section] = {"section": section, "title": _label(conn, section, record),
                                       "file": _file_of(section), "items": []}
        if any(i["id"] == target for i in group["items"]):
            continue
        included.add(target)
        item = _item(conn, root, target)
        item["kind"] = "图" if item["type"] == "figure" else "表"
        item["trace"] = _trace_state(conn, root, target)
        group["items"].append(item)
    loose = []
    for figure in db.get_nodes_by_type(conn, "figure"):
        if figure["id"] in included:
            continue
        item = _item(conn, root, figure["id"])
        item["kind"] = "图"
        item["trace"] = _trace_state(conn, root, figure["id"])
        loose.append(item)
    ordered = sorted(groups.values(), key=lambda g: (g["file"] or "", g["section"]))
    for g in ordered:
        g["items"].sort(key=lambda i: i["path"] or i["id"])
    counts: dict[str, int] = {}
    for claim in db.get_nodes_by_type(conn, "claim"):
        file = _file_of(claim["id"])
        if file:
            counts[file] = counts.get(file, 0) + 1
    papers = _marked(root)
    marked = set(papers["drafts"])
    drafts = sorted(_draft_files(conn) | set(counts))
    return {
        "sections": ordered,
        "loose": sorted(loose, key=lambda i: i["path"] or i["id"]),
        # 12.13: claims only from the drafts marked 「设为论文正文」.
        "claims": [{"file": f, "count": counts[f]} for f in drafts if f in marked and f in counts],
        "drafts": [{"file": f, "marked": f in marked, "claims": counts.get(f, 0), "node": notes_mod.normalize_node(f)} for f in drafts],
        "papers": {"marked": sorted(marked), "trusted": papers["trusted"], "message": papers["message"],
                   "path": papers["path"]},
        "states": TRACE_STATES,
    }


def _marked(root: Path) -> dict[str, Any]:
    from researchos.records import papers  # noqa: PLC0415

    try:
        return papers.marked(root)
    except Exception as exc:  # noqa: BLE001 -- 成果 still lists figures and tables
        return {"drafts": [], "trusted": False, "message": f"论文正文记录文件无法读取：{exc}",
                "path": f".researchos/{papers.PAPERS_FILENAME}"}


def _draft_files(conn: Connection) -> set[str]:
    """Every draft the index read: the files its sections and claims are in."""
    found: set[str] = set()
    for kind in ("section", "claim"):
        for row in conn.execute("SELECT id FROM nodes WHERE type = ?", (kind,)).fetchall():
            file = _file_of(row[0])
            if file:
                found.add(file)
    return found


def _draft_claims(conn: Connection, root: Path, draft: str) -> dict[str, Any]:
    rel = notes_mod.node_path(notes_mod.normalize_node(f"file:{draft}")) or draft
    prefix = f"claim:{rel}#"
    rows = conn.execute("SELECT id FROM nodes WHERE type = 'claim' AND substr(id, 1, ?) = ?", (len(prefix), prefix)).fetchall()
    cache: dict[str, dict[str, Any]] = {}
    sections: dict[str, dict[str, Any]] = {}
    claims = [db.get_node(conn, r[0]) for r in rows[:CLAIM_LIMIT]]
    claims = [c for c in claims if c is not None]
    claims.sort(key=lambda c: (c["attrs"].get("line") or 0, c["id"]))
    for claim in claims:
        section = claim["attrs"].get("section") or ""
        group = sections.get(section)
        if group is None:
            group = sections[section] = {"section": section or None,
                                         "title": _label(conn, section) if section else "（不在任何章节下）",
                                         "claims": []}
        group["claims"].append({
            "id": claim["id"], "sentence": claim["attrs"].get("sentence") or claim.get("title") or "",
            "number": claim["attrs"].get("printed_number"), "line": claim["attrs"].get("line"),
            "trace": _claim_state(conn, root, claim["id"], cache),
        })
    return {"file": rel, "sections": list(sections.values()), "total": len(rows), "shown": len(claims),
            "states": TRACE_STATES}


# -- 数据与代码 (12.9) -----------------------------------------------------------------------------


def data_code(conn: Connection, project_root: str | Path) -> dict[str, Any]:
    """Every script and data file, with what it reads and writes and which
    of the lineage report's blocks it belongs to (module docstring)."""
    root = Path(project_root)
    report = lineage.build_lineage_report(conn, root)
    orphan = {o["path"] for o in report["orphans"]}
    broken: dict[str, list[dict[str, Any]]] = {}
    for b in report["broken_links"]:
        for who in (b.get("script"), b.get("target")):
            if who:
                broken.setdefault(who, []).append({"script": b.get("script"), "target": b.get("target"),
                                                   "relation": RELATION_WORDS.get(b.get("kind") or "", "读取"),
                                                   "line": b.get("line")})
    copies: dict[str, list[str]] = {}
    for d in report["duplicates"]:
        copies[d["path"]] = list(d["other_copies"])
    flags = judgements.link_flags(conn)

    def waiting(edges: list[dict[str, Any]]) -> int:
        return sum(1 for e in edges if (lambda f: f.get("review") or f.get("conflict"))(flags.for_key(judgements.key_of(e))))

    files = []
    for kind in ("script", "dataset"):
        for n in db.get_nodes_by_type(conn, kind):
            path = notes_mod.node_path(n["id"])
            if path is None:
                continue
            item: dict[str, Any] = {"id": n["id"], "type": kind, "type_label": TYPE_WORDS[kind], "path": path,
                                    "exists": _exists(root, path)}
            if kind == "script":
                edges = _live(db.query_edges(conn, src=n["id"]))
                item["reads"] = sorted({notes_mod.node_path(e["dst"]) or e["dst"] for e in edges if e["type"] == "reads"})
                item["writes"] = sorted({notes_mod.node_path(e["dst"]) or e["dst"] for e in edges
                                         if e["type"] in trace_mod.PRODUCER_TYPES})
                status = (db.scan_source_row(conn, "dataflow", path) or {}).get("status")
                if status in ("unreadable", "unparseable"):
                    item["unreadable"] = True
            else:
                edges = _live(db.query_edges(conn, dst=n["id"]))
                item["read_by"] = sorted({notes_mod.node_path(e["src"]) or e["src"] for e in edges if e["type"] == "reads"})
                item["written_by"] = sorted({notes_mod.node_path(e["src"]) or e["src"] for e in edges
                                             if e["type"] in trace_mod.PRODUCER_TYPES})
            item["orphan_input"] = path in orphan
            item["review"] = waiting(db.query_edges(conn, src=n["id"]) + db.query_edges(conn, dst=n["id"]))
            if path in broken:
                item["broken"] = broken[path]
            if path in copies:
                item["other_copies"] = copies[path]
            files.append(item)
    files.sort(key=lambda f: (f["path"]))
    return {
        "files": files,
        "counts": {
            "all": len(files),
            "orphans": sum(1 for f in files if f["orphan_input"]),
            "broken": sum(1 for f in files if f.get("broken") or not f["exists"]),
            "duplicates": sum(1 for f in files if f.get("other_copies")),
        },
        "scanned": report["scanned"],
    }


# -- 研究路线 and an abandoned route's basis (12.4, 12.9; V8 U4) -------------------------------------

#: The order of 研究路线's groups (U4): the live routes first -- 现行 and
#: 待定 -- then 不利证据, 已放弃, 被否, 归档; a word of the researcher's own
#: after those, and 其它 last.
ROUTE_ORDER = ("current", "pending", "adverse", "abandoned", "rejected", "archived", "labelled", "other")
EVIDENCE_ROLE_WORDS = {"script": "步骤脚本", "output": "脚本写出的文件", "file": "关联的文件"}
EVIDENCE_STATE_WORDS = {
    "kept": "内容已保存", "fingerprint": "只记下了指纹", "missing": "当时文件已不存在",
    "dataless": "当时文件只在云端", "unreadable": "当时文件无法读取",
}
#: The reason an approval wrote into the table's result cell (12.12:
#: 「（放弃理由：…）」), or one the researcher wrote the same way by hand.
_REASON_RE = re.compile(r"[（(]\s*放弃理由\s*[:：]\s*(.+?)\s*[）)]\s*$|^\s*放弃理由\s*[:：]\s*(.+?)\s*$", re.S)


def _reason_in(result: str) -> tuple[str, str | None]:
    """(the result without its 「放弃理由」 part, that reason) -- or the
    result as it is and None when it names no reason."""
    text = result or ""
    m = _REASON_RE.search(text)
    if not m:
        return text, None
    reason = (m.group(1) or m.group(2) or "").strip()
    return text[:m.start()].rstrip(), reason or None


def _decisions(root: Path) -> tuple[list[dict[str, Any]], str | None]:
    from researchos.records import decisions as decisions_mod  # noqa: PLC0415

    try:
        return decisions_mod.entries(root), None
    except Exception as exc:  # noqa: BLE001 -- the routes are still shown; the page says the basis cannot be read
        return [], str(exc)


def _who(entry: dict[str, Any], key: str, team: Any, lang: str = "zh") -> dict[str, Any] | None:
    from researchos import people  # noqa: PLC0415

    value = entry.get(key)
    if key != "by" and not value:
        return None
    got = people.attribution({"by": value}, team, lang)
    return {"id": got["by"], "label": got["label"]}


def _decision_payload(entry: dict[str, Any], team: Any, labels: dict[str, str], lang: str = "zh") -> dict[str, Any]:
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    out = {k: entry.get(k) for k in ("id", "at", "act", "kind", "proposal", "note", "result", "reason", "via")}
    out["by"] = _who(entry, "by", team, lang)
    out["approved_by"] = _who(entry, "approved_by", team, lang)
    if isinstance(entry.get("verdict"), str):
        out["verdict_status"] = attempts_ingest.verdict_status(entry["verdict"], labels)
    out["evidence_count"] = len(entry.get("evidence") or [])
    if entry.get("missing_from_file"):
        out["missing_from_file"] = True
    return out


def routes(conn: Connection, project_root: str | Path, lang: str = "zh") -> dict[str, Any]:
    """研究路线 (12.9, U4): every attempt of the table, grouped by its
    verdict's status in `ROUTE_ORDER`, each in the table's own order: its
    number, date, the researcher's title, the status and the researcher's
    words; for an abandoned route its result and its reason -- from
    `decisions.toml` when an approval recorded it (with who proposed and
    who approved, and how many files of its basis were frozen), else from
    the table's own 「放弃理由：…」. People are named in `lang` (13.7).
    Reads only."""
    from researchos import people  # noqa: PLC0415
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    from researchos import proposals  # noqa: PLC0415

    root = Path(project_root)
    labels = attempts_ingest.verdict_labels(root)
    markers = proposals.abandon_markers(root)
    decided, problem = _decisions(root)
    team = people.read_team(root)
    team = team if team.state == "present" else None
    last: dict[str, dict[str, Any]] = {}
    for entry in decided:
        node = entry.get("attempt")
        if isinstance(node, str) and node:
            if entry.get("act") == "abandoned" or node not in last or last[node].get("act") != "abandoned":
                last[node] = entry
    nodes = sorted(db.get_nodes_by_type(conn, "attempt"),
                   key=lambda n: (n["attrs"].get("source_file", ""),
                                  attempts_ingest.attempt_sort_key(str(n["attrs"].get("number") or ""))))
    groups: dict[str, dict[str, Any]] = {}
    for n in nodes:
        attrs, human = n["attrs"], n.get("human_fields") or {}
        status = attempts_ingest.route_status(human.get("verdict", ""), labels, markers)
        result, reason = _reason_in(str(human.get("result") or ""))
        item: dict[str, Any] = {
            "id": n["id"], "number": attrs.get("number"), "date": attrs.get("date"),
            "title": attrs.get("description") or n.get("title") or "", "verdict_status": status,
            "result": result, "reason": reason, "reason_from": "table" if reason else None,
            "parent": attrs.get("parent"),
        }
        entry = last.get(n["id"])
        if entry is not None:
            item["decision"] = _decision_payload(entry, team, labels, lang)
            if entry.get("act") == "abandoned" and str(entry.get("reason") or "").strip():
                item["reason"], item["reason_from"] = str(entry["reason"]).strip(), "decision"
        key = status["status"] if status["status"] != "labelled" else "labelled:" + status["label"]
        group = groups.setdefault(key, {"status": status["status"], "label": status["label"], "items": []})
        group["items"].append(item)

    def order(key: str) -> tuple[int, str]:
        base = key.split(":", 1)[0]
        return (ROUTE_ORDER.index(base) if base in ROUTE_ORDER else len(ROUTE_ORDER), key)

    out = {"groups": [groups[k] for k in sorted(groups, key=order)], "count": len(nodes)}
    if problem:
        out["decisions_problem"] = problem
    return out


def evidence(conn: Connection, project_root: str | Path, given: str, lang: str = "zh") -> dict[str, Any]:
    """「当时的依据」 (12.4): the decisions about one attempt, newest first,
    each with the files its approval froze -- by fingerprint, and whether
    the file is still as it was then (the version store's latest look at
    it), and whether its content can be shown (`version`). Reads only.
    `ValueError` for something that is not an attempt; `NotFound` for an
    attempt nothing knows."""
    from researchos import history, people  # noqa: PLC0415
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415
    from researchos.records.identity import IdentityState, read_identity  # noqa: PLC0415

    root = Path(project_root)
    node_id = notes_mod.normalize_node(given)
    if _type_of(node_id) != "attempt":
        raise ValueError(f"not an attempt: {given!r}")
    record = db.get_node(conn, node_id)
    decided, problem = _decisions(root)
    mine = [e for e in decided if e.get("attempt") == node_id]
    if record is None and not mine:
        raise NotFound(f"no such attempt: {node_id}")
    labels = attempts_ingest.verdict_labels(root)
    team = people.read_team(root)
    team = team if team.state == "present" else None
    got = read_identity(root)
    pid = got.identity.id if got.state is IdentityState.PRESENT and got.identity is not None else None
    store = history.Store.existing(pid) if pid else None
    try:
        decisions_out = []
        for entry in reversed(mine):
            item = _decision_payload(entry, team, labels, lang)
            files = []
            for ev in entry.get("evidence") or []:
                rel = ev.get("path")
                if not isinstance(rel, str) or not rel:
                    continue
                try:
                    file_node = notes_mod.normalize_node(rel)
                except ValueError:
                    continue
                f = {k: ev.get(k) for k in ("path", "role", "state", "sha256", "size", "version")}
                f["node"] = file_node
                f["role_label"] = EVIDENCE_ROLE_WORDS.get(ev.get("role"), "文件")
                f["state_label"] = EVIDENCE_STATE_WORDS.get(ev.get("state"), "")
                exists = _exists(root, rel)
                latest = store.latest(file_node) if store is not None else None
                if not exists:
                    f["now"], f["now_label"] = "missing", "现在已不在项目里"
                elif not ev.get("sha256"):
                    f["now"], f["now_label"] = "unknown", ""
                elif latest is not None and latest.sha256 == ev.get("sha256"):
                    f["now"], f["now_label"] = "same", "现在仍是这一版"
                elif latest is not None:
                    f["now"], f["now_label"] = "changed", "之后改过"
                else:
                    f["now"], f["now_label"] = "unknown", ""
                f["viewable"] = bool(ev.get("state") == "kept" and (ev.get("sha256") or ev.get("version")))
                files.append(f)
            item["files"] = files
            decisions_out.append(item)
    finally:
        if store is not None:
            store.close()
    title = _label(conn, node_id, record) if record is not None else node_id
    out: dict[str, Any] = {"id": node_id, "name": title, "decisions": decisions_out}
    if problem:
        out["decisions_problem"] = problem
    return out


VERSION_TEXT_LIMIT = 2 * 1024 * 1024  # what the page shows of one kept version


def _published_content(project_id: str, rel: str, sha256: str) -> bytes | None:
    """`rel`'s content with fingerprint `sha256` from a published snapshot
    that carried it (verified by that hash), when this machine knows the
    shared folder; None otherwise."""
    import hashlib  # noqa: PLC0415

    from researchos import exchange as exchange_mod  # noqa: PLC0415
    from researchos import share  # noqa: PLC0415

    try:
        shared = exchange_mod.folders_for(project_id)
        if shared is None:
            return None
        snaps, _problems = share.read_snapshots(shared)
        for snap in reversed(snaps):
            entry = snap.files.get(rel)
            if entry is not None and entry.get("sha256") == sha256 and int(entry.get("size") or 0) <= VERSION_TEXT_LIMIT * 4:
                data = share._read_content(shared, entry)
                return data if hashlib.sha256(data).hexdigest() == sha256 else None
    except Exception:  # noqa: BLE001 -- no shared folder now: nothing more is known
        return None
    return None


def version_content(project_root: str | Path, given: str, version: str | None, sha256: str | None = None) -> dict[str, Any]:
    """One kept version of a file as it was (「当时的依据」 opens the frozen
    evidence): its text when the version store holds its content and it
    is text, else what is known of it -- size and fingerprint. Reads only.

    A decision freezes a file by its fingerprint; the version number beside
    it is the approving machine's own (each machine numbers its store), so
    with `sha256` the content is found by fingerprint -- in this machine's
    store, else in a published snapshot that carried it -- and the number
    is never trusted alone. `ValueError` for a bad node, version or
    fingerprint; `NotFound` for content nothing here holds."""
    import hashlib  # noqa: PLC0415

    from researchos import history  # noqa: PLC0415
    from researchos.records.identity import IdentityState, read_identity  # noqa: PLC0415

    root = Path(project_root)
    node_id = notes_mod.normalize_node(given)
    rel = notes_mod.node_path(node_id)
    if rel is None:
        raise ValueError(f"not a file: {given!r}")
    if sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
        raise ValueError(f"not a fingerprint: {sha256!r}")
    if sha256 is None and (not isinstance(version, str) or not version.isdigit()):
        raise ValueError(f"not a version: {version!r}")
    got = read_identity(root)
    if got.state is not IdentityState.PRESENT or got.identity is None:
        raise NotFound("the project's identity cannot be read")
    store = history.Store.existing(got.identity.id)
    data = None
    found = None
    try:
        if store is not None:
            kept = store.versions(node_id)
            if sha256 is not None:
                same = [v for v in kept if v.sha256 == sha256]
                found = next((v for v in same if v.stored), same[0] if same else None)
                data = store.read_blob(sha256)
            else:
                found = next((v for v in kept if v.seq == int(version)), None)
                data = store.read_blob(found.sha256) if found is not None and found.stored else None
    finally:
        if store is not None:
            store.close()
    if sha256 is not None:
        if data is not None and hashlib.sha256(data).hexdigest() != sha256:
            data = None
        if data is None:
            data = _published_content(got.identity.id, rel, sha256)
        if data is None and found is None:
            raise NotFound(f"no content {sha256[:12]} of {rel}")
    elif found is None:
        raise NotFound(f"no version {version} of {rel}")
    out: dict[str, Any] = {"node": node_id, "path": rel, "version": str(found.seq) if found is not None else None,
                           "at": found.at if found is not None else None,
                           "size": found.size if found is not None else (len(data) if data is not None else None),
                           "sha256": sha256 or found.sha256, "who": found.who if found is not None else None,
                           "text": None, "truncated": False}
    if data is not None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None:
            out["truncated"] = len(text) > VERSION_TEXT_LIMIT
            out["text"] = text[:VERSION_TEXT_LIMIT]
    if out["text"] is None:
        out["reason"] = "这一版只记下了指纹和大小，没有保存内容" if data is None else "这一版不是文本，不在这里显示"
    return out


# -- what a proposal's files use (12.3: the variable versions it uses; V8 U4) -------------------------

_ROLE_WORDS_CARD = {"script": "实现脚本", "output": "输出", "input": "输入数据"}


def _card_paths(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Every project path a card's version in use names -- its input data,
    its implementation script and output -- with the card and version."""
    from researchos.records import variables as V  # noqa: PLC0415

    out: dict[str, list[dict[str, Any]]] = {}
    try:
        cards = V.read_cards(root)
    except Exception:  # noqa: BLE001 -- nothing is said rather than something wrong
        return out
    for card in cards:
        if card.in_use is None or card.in_use not in card.versions:
            continue
        content = card.versions[card.in_use].content
        if not isinstance(content, dict):
            continue
        base = {"card": card.id, "name": _card_name(card), "version": card.in_use}
        impl = content.get("implementation") if isinstance(content.get("implementation"), dict) else {}
        for role, value in (("script", impl.get("script")), ("output", impl.get("output"))):
            if isinstance(value, str) and value.strip():
                out.setdefault(posixpath.normpath(value.strip()), []).append({**base, "role": role})
        for item in content.get("input") or []:
            if isinstance(item, dict) and isinstance(item.get("dataset"), str) and item["dataset"].strip():
                out.setdefault(posixpath.normpath(item["dataset"].strip()), []).append({**base, "role": "input"})
    return out


def _script_reads(rel: str, content: str | None) -> list[str]:
    """The paths a proposed script reads, from its own text (the scanner's
    reading, on the proposal's copy of the file) -- empty when it is not a
    script or its text is not at hand."""
    from researchos.ingest import dataflow  # noqa: PLC0415

    if not content:
        return []
    path = Path(content)
    suffix = path.suffix.lower()
    try:
        if suffix == ".py":
            outcome = dataflow.scan_py_file(path.parent, path.name)
        elif suffix == ".r":
            outcome = dataflow.scan_r_file(path.parent, path.name)
        elif suffix == ".rmd":
            outcome = dataflow.scan_rmd_file(path.parent, path.name)
        else:
            return []
    except Exception:  # noqa: BLE001
        return []
    found = []
    folder = posixpath.dirname(rel)
    for call in outcome.calls:
        if call.kind != "read" or not call.literal:
            continue
        literal = call.literal.strip()
        if literal.startswith("/"):
            continue
        found.append(posixpath.normpath(literal))
        if folder:
            found.append(posixpath.normpath(posixpath.join(folder, literal)))
    return found


def variables_used(project_root: str | Path, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The variable versions a proposal's files use (12.3): a file that is
    a card's input, implementation script or output in the version in use,
    and a proposed script that reads such a file -- each once, with how."""
    root = Path(project_root)
    named = _card_paths(root)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for f in files or []:
        rel = f.get("path")
        if not isinstance(rel, str) or f.get("action") == "removed":
            continue
        for use in named.get(posixpath.normpath(rel), []):
            key = (use["card"], rel, use["role"])
            if key not in seen:
                seen.add(key)
                out.append({**use, "path": rel, "how": f"{rel} 是这个变量的{_ROLE_WORDS_CARD[use['role']]}"})
        for read in _script_reads(rel, f.get("content")):
            for use in named.get(read, []):
                if use["role"] == "script":
                    continue
                key = (use["card"], rel, "reads:" + read)
                if key not in seen:
                    seen.add(key)
                    out.append({**use, "path": rel, "reads": read,
                                "how": f"{rel} 读取 {read}，这是这个变量的{_ROLE_WORDS_CARD[use['role']]}"})
    return out

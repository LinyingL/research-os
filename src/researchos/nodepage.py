"""The node page, 成果 and 数据与代码 (DESIGN.md 12.6, 12.7, 12.9; task V8 U3).

Three read-only payloads for the page, built from the index, the record and
the files -- nothing here writes:

- `node(conn, root, node)` -- one object's page: what it is (type in words,
  name, path, whether it is on disk), its trace drawn as columns (12.7: 数据
  | 脚本 | 中间数据 | 脚本 | 本节点 | 被引用) with the lines that join them,
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
  the drafts' claims (per draft, on request: a draft can hold hundreds),
  each with its trace state 完整 / 缺来源 / 缺尝试.
- `data_code(conn, root)` -- 数据与代码: every script and data file with what
  it reads and writes, and which of the lineage report's blocks it is in
  (无来源的输入 / 断链 / 同名拷贝), so the page can filter.

The page's copy is Chinese (8.8) and says relations in words (12.11): 读取,
写出, 生成, 引用, 属于 -- never an arrow.
"""

from __future__ import annotations

import posixpath
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
    "belongs_to": "属于", "backed_by": "依据",
}
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
        "id": node_id, "type": _type_of(node_id), "type_label": TYPE_WORDS.get(_type_of(node_id), "节点"),
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
        "from": frm, "to": to, "type": edge["type"], "relation": RELATION_WORDS.get(edge["type"], edge["type"]),
        "human": edge["extractor"] == "mapping", "status": edge["status"],
        "review": bool(marks.get("review") or marks.get("conflict")),
    }


def _live(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in edges if e["status"] != "rejected"]


def _verdict(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    verdict = (record.get("human_fields") or {}).get("verdict", "")
    return attempts_ingest.verdict_status(verdict, attempts_ingest.verdict_labels(root))


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


def _links(conn: Connection, node_id: str, flags: judgements.LinkFlags) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for edge in db.query_edges(conn, src=node_id) + db.query_edges(conn, dst=node_id):
        key = judgements.key_of(edge)
        if key in seen:
            continue
        other = edge["dst"] if edge["src"] == node_id else edge["src"]
        item = {k: edge[k] for k in ("src", "dst", "type", "extractor", "status")}
        item.update(flags.for_key(key))
        item["human"] = edge["extractor"] == "mapping"
        item["relation"] = RELATION_WORDS.get(edge["type"], edge["type"])
        item["direction"] = "out" if edge["src"] == node_id else "in"
        item["src_label"] = _label(conn, edge["src"])
        item["dst_label"] = _label(conn, edge["dst"])
        item["other"] = {"id": other, "type": _type_of(other), "path": notes_mod.node_path(other)}
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
    columns.append({"key": "self", "title": "本节点", "items": [self_item]})

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
    | 产出, with the scripts' reads and writes."""
    node_id = record["id"]
    members: dict[str, dict[str, Any]] = {}
    config = trace_mod._attempts_config(root)
    steps_dir = config.steps_dir if config is not None else None
    for name in record["attrs"].get("step_files") or []:
        if steps_dir and isinstance(name, str) and name:
            rel = posixpath.normpath(f"{steps_dir.rstrip('/')}/{name}")
            try:
                member = notes_mod.normalize_node(rel)
            except ValueError:
                continue
            members.setdefault(member, {"via": "step_files"})
    attached = []
    for edge in _live(db.query_edges(conn, dst=node_id, type="belongs_to")):
        members.setdefault(edge["src"], {"via": "belongs_to"})["link"] = {
            k: edge[k] for k in ("src", "dst", "type", "extractor")}
        attached.append(edge["src"])
    inputs, scripts, outputs, lines = [], [], [], []
    for member, how in members.items():
        item = _item(conn, root, member, member=how["via"], attached=member in attached)
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
    数据 | 脚本 | 输出 | 本节点 -- from the version in use, else the newest."""
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
    columns.append({"key": "self", "title": "本节点", "items": [self_item]})
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
    columns.append({"key": "self", "title": "本节点", "items": [self_item]})
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
        "id": node_id, "type": prefix, "type_label": TYPE_WORDS.get(prefix, "节点"),
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
        out["trace"] = _attempt_trace(conn, root, record, flags)
        out["choices"] = {"files": _file_choices(conn, root, ("script", "dataset", "figure"))}
    elif prefix in FILE_NODE_TYPES:
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
    out["links"] = _links(conn, node_id, flags)
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
    return {
        "sections": ordered,
        "loose": sorted(loose, key=lambda i: i["path"] or i["id"]),
        "claims": [{"file": f, "count": n} for f, n in sorted(counts.items())],
        "states": TRACE_STATES,
    }


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

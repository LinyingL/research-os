"""Tracing a result (DESIGN.md 12.7, task V8 phase 2).

"Where does this figure come from" answered as one chain:

    the data it was built from -> the scripts that read and wrote it ->
    the figure -> the draft sections that include it,

with the attempt(s) it belongs to and the variable versions it used.
`chain(conn, root, node)` builds it from the index and the record, reading
nothing it does not have to:

- **Upstream.** A figure's or a data file's producers are the scripts with
  a `generates` or `writes` link to it -- from any extractor, hand-drawn
  links (`mapping`) included; a `generates` link that starts at a commit
  (`pyfig`, Section 4) names its script in its evidence. Each script's
  `reads` give the data it read; each of those data files' producers come
  next, and so on (bounded). A rejected link is wrong everywhere (8.12)
  and is not followed; a link under review is followed and marked.
- **Downstream.** The sections that `include` the node.
- **Attempts.** An attempt claims a file through its step numbers
  (`attrs.step_files`, resolved under `steps_dir`) or through a hand-drawn
  `belongs_to` mapping from the file to the attempt (「关联文件」). Every
  file of the chain is asked.
- **Variables.** A variable card's version whose `[implementation]` names a
  file of the chain as its `output` -- or whose `field` is a column of a
  table in the chain (said as such: a column name is weaker evidence than
  a path). What a version is used for is the researcher's statement in the
  card; the trace only reports that the card points here.

Every break is explicit, never an empty list that looks like "nothing":

- `no_generating_script` -- the node itself has no producer; repair:
  「补上来源」, a `generates` (figure) or `writes` (data) mapping;
- `no_attempt` -- no attempt claims any file of the chain; repair:
  「关联文件」, a `belongs_to` mapping;
- `script_unreadable` -- the latest scan could not read or parse a script
  of the chain (its reads may be missing);
- `script_missing` / `data_missing` -- a file of the chain is not on disk.
"""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection
from typing import Any

from researchos import db
from researchos.records import judgements
from researchos.records import notes as notes_mod

MAX_DEPTH = 12
PRODUCER_TYPES = ("generates", "writes")
TABLE_SUFFIXES = (".csv", ".tsv", ".tab")

BREAK_LABELS = {
    "no_generating_script": "没有找到生成它的脚本",
    "no_attempt": "还没有归入任何尝试",
    "script_unreadable": "脚本暂时无法读取或解析",
    "script_missing": "脚本文件不在磁盘上",
    "data_missing": "数据文件不在磁盘上",
}
REPAIR_LABELS = {"generates": "补上来源", "writes": "补上来源", "belongs_to": "关联文件"}


def _live(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in edges if e["status"] != "rejected"]


def _occurrences(evidence: Any) -> list[dict[str, Any]]:
    if isinstance(evidence, dict):
        occ = evidence.get("occurrences")
        if isinstance(occ, list):
            return [o for o in occ if isinstance(o, dict)]
        return [evidence]
    return []


def _script_of(edge: dict[str, Any]) -> str | None:
    """The script a producing link starts at: the link's own source when
    that is a script, else (a commit's `generates`) the file its evidence
    names."""
    src = edge["src"]
    if src.startswith("script:"):
        return src
    if src.startswith("commit:"):
        for occ in _occurrences(edge["evidence"]):
            file = occ.get("file")
            if isinstance(file, str) and file:
                return f"script:{file}"
    return None


def _exists(root: Path, rel: str | None) -> bool:
    return rel is not None and (root / rel).exists()


def _link(edge: dict[str, Any], flags: judgements.LinkFlags) -> dict[str, Any]:
    out = {k: edge[k] for k in ("src", "dst", "type", "extractor", "status")}
    out["human"] = edge["extractor"] == "mapping"
    out.update(flags.for_key(judgements.key_of(edge)))
    return out


def _attempts_config(root: Path):
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    try:
        return attempts_ingest.load_config(root)
    except attempts_ingest.AttemptsConfigError:
        return None


def chain(conn: Connection, project_root: str | Path, node: str) -> dict[str, Any]:
    """The whole chain for `node` (module docstring). Reads only."""
    root = Path(project_root)
    node_id = notes_mod.normalize_node(node)
    rel = notes_mod.node_path(node_id)
    record = db.get_node(conn, node_id)
    flags = judgements.link_flags(conn)
    out: dict[str, Any] = {
        "node": {"id": node_id, "type": node_id.partition(":")[0], "path": rel, "exists": _exists(root, rel),
                 "in_index": record is not None},
        "found": record is not None or _exists(root, rel),
        "scripts": [], "data": [], "drafts": [], "attempts": [], "variables": [], "links": [], "breaks": [],
    }
    if not out["found"]:
        return out

    scripts: dict[str, dict[str, Any]] = {}
    data: dict[str, dict[str, Any]] = {}
    links: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    def producers(target: str) -> list[str]:
        found = []
        for edge_type in PRODUCER_TYPES:
            for edge in _live(db.query_edges(conn, dst=target, type=edge_type)):
                script = _script_of(edge)
                if script is None:
                    continue
                links[judgements.key_of(edge)] = _link(edge, flags)
                found.append(script)
        return list(dict.fromkeys(found))

    def visit_script(script: str, depth: int, frontier: list[tuple[str, int]]) -> None:
        if script in scripts:
            return
        path = notes_mod.node_path(script)
        status = (db.scan_source_row(conn, "dataflow", path) or {}).get("status") if path else None
        entry = {"id": script, "path": path, "exists": _exists(root, path), "depth": depth,
                 "scan_status": status, "reads": [], "writes": []}
        scripts[script] = entry
        for edge in _live(db.query_edges(conn, src=script)):
            if edge["type"] == "reads":
                links[judgements.key_of(edge)] = _link(edge, flags)
                entry["reads"].append(edge["dst"])
                frontier.append((edge["dst"], depth + 1))
            elif edge["type"] in PRODUCER_TYPES:
                entry["writes"].append(edge["dst"])

    start_type = out["node"]["type"]
    frontier: list[tuple[str, int]] = []
    if start_type == "script":
        visit_script(node_id, 0, frontier)
    else:
        made_by = producers(node_id)
        out["node"]["made_by"] = made_by
        for script in made_by:
            visit_script(script, 1, frontier)
        if not made_by and start_type in ("figure", "dataset", "file"):
            repair = "generates" if start_type == "figure" else "writes"
            out["breaks"].append(_break("no_generating_script", node_id, rel, repair=repair))
    while frontier:
        dataset, depth = frontier.pop(0)
        if dataset in data or depth > MAX_DEPTH:
            continue
        path = notes_mod.node_path(dataset)
        made_by = producers(dataset)
        data[dataset] = {"id": dataset, "path": path, "exists": _exists(root, path), "depth": depth,
                         "written_by": made_by, "raw": not made_by}
        for script in made_by:
            visit_script(script, depth + 1, frontier)

    for script in scripts.values():
        if not script["exists"]:
            out["breaks"].append(_break("script_missing", script["id"], script["path"]))
        elif script["scan_status"] in ("unreadable", "unparseable"):
            out["breaks"].append(_break("script_unreadable", script["id"], script["path"], detail=script["scan_status"]))
    for item in data.values():
        if not item["exists"]:
            out["breaks"].append(_break("data_missing", item["id"], item["path"]))

    for edge in _live(db.query_edges(conn, dst=node_id, type="includes")):
        section = edge["src"]
        links[judgements.key_of(edge)] = _link(edge, flags)
        section_node = db.get_node(conn, section)
        file = section.partition(":")[2].rsplit("#", 1)[0] if section.startswith("section:") else None
        out["drafts"].append({"section": section, "file": file,
                              "title": section_node.get("title") if section_node else None,
                              "extractor": edge["extractor"]})

    chain_nodes = list(dict.fromkeys([node_id, *scripts, *data]))  # a script's own page has it in both
    out["attempts"] = _attempts(conn, root, chain_nodes, flags, links)
    if not out["attempts"]:
        out["breaks"].append(_break("no_attempt", node_id, rel, repair="belongs_to"))
    out["variables"] = _variables(root, [notes_mod.node_path(n) for n in chain_nodes])
    out["scripts"] = sorted(scripts.values(), key=lambda s: (s["depth"], s["id"]))
    out["data"] = sorted(data.values(), key=lambda d: (d["depth"], d["id"]))
    out["links"] = list(links.values())
    return out


def _break(kind: str, node: str, path: str | None, *, repair: str | None = None, detail: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"kind": kind, "node": node, "path": path, "label": BREAK_LABELS[kind]}
    if detail is not None:
        item["detail"] = detail
    if repair is not None:
        item["repair"] = {"type": repair, "label": REPAIR_LABELS[repair], "file": path}
    return item


def _attempts(conn: Connection, root: Path, chain_nodes: list[str], flags: judgements.LinkFlags,
              links: dict[tuple[str, str, str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    config = _attempts_config(root)
    steps_dir = config.steps_dir if config is not None else None
    labels = attempts_ingest.verdict_labels(root)
    paths_in_chain = {notes_mod.node_path(n): n for n in chain_nodes if notes_mod.node_path(n)}
    found: dict[str, dict[str, Any]] = {}

    def claim(attempt: dict[str, Any], via: str, through: str) -> None:
        item = found.setdefault(attempt["id"], {
            "id": attempt["id"], "number": attempt["attrs"].get("number"), "title": attempt.get("title"),
            "verdict": (attempt.get("human_fields") or {}).get("verdict", ""),
            "verdict_status": attempts_ingest.verdict_status((attempt.get("human_fields") or {}).get("verdict", ""),
                                                             labels),
            "result": (attempt.get("human_fields") or {}).get("result", ""), "via": [],
        })
        item["via"].append({"via": via, "through": through})

    attempts = db.get_nodes_by_type(conn, "attempt")
    if steps_dir:
        for attempt in attempts:
            for name in attempt["attrs"].get("step_files") or []:
                path = f"{steps_dir.rstrip('/')}/{name}"
                if path in paths_in_chain:
                    claim(attempt, "step_files", paths_in_chain[path])
    by_id = {a["id"]: a for a in attempts}
    for node in chain_nodes:
        for edge in _live(db.query_edges(conn, src=node, type="belongs_to")):
            attempt = by_id.get(edge["dst"])
            if attempt is None:
                continue
            links[judgements.key_of(edge)] = _link(edge, flags)
            claim(attempt, "belongs_to", node)
    return sorted(found.values(), key=lambda a: a["id"])


def _columns_of(root: Path, rel: str) -> list[str] | None:
    from researchos import history  # noqa: PLC0415

    full = root / rel
    if not rel.lower().endswith(TABLE_SUFFIXES) or not full.is_file() or history._is_dataless(full):
        return None
    try:
        with open(full, "rb") as stream:
            import csv  # noqa: PLC0415
            import io  # noqa: PLC0415

            text = io.TextIOWrapper(stream, encoding="utf-8", errors="replace", newline="")
            delimiter = "," if rel.lower().endswith(".csv") else "\t"
            header = next(csv.reader(text, delimiter=delimiter), None)
            text.detach()
    except (OSError, ValueError):
        return None
    if header and header[0].startswith("﻿"):
        header[0] = header[0][1:]
    return header


def _variables(root: Path, chain_paths: list[str | None]) -> list[dict[str, Any]]:
    from researchos.records import variables as variables_mod  # noqa: PLC0415

    paths_in_chain = [p for p in chain_paths if p]
    found = []
    columns: dict[str, list[str] | None] = {}
    for card in variables_mod.read_cards(root):
        for number, view in sorted(card.versions.items()):
            impl = (view.content or {}).get("implementation") or {}
            output, field = impl.get("output"), impl.get("field")
            matched_by, dataset = None, None
            if isinstance(output, str) and output in paths_in_chain:
                matched_by, dataset = "output", output
            elif isinstance(field, str) and field:
                for path in paths_in_chain:
                    if path not in columns:
                        columns[path] = _columns_of(root, path)
                    if field in (columns[path] or []):
                        matched_by, dataset = "field", path
                        break
            if matched_by is None:
                continue
            reference = None
            if view.entry is not None and view.entry.get("content"):
                reference = variables_mod.Reference(card.id, number, view.entry.id, str(view.entry.get("content"))).payload()
            found.append({
                "variable": card.id, "version": number, "status": view.status, "matched_by": matched_by,
                "dataset": dataset, "field": field, "reference": reference, "card_state": card.state,
            })
    return found


def summary_lines(result: dict[str, Any]) -> list[str]:
    """`researchos trace --chain`'s text (English, the CLI's language)."""
    node = result["node"]
    if not result["found"]:
        return [f"No such node or file: {node['id']}"]
    lines = [f"Trace of {node['id']}"]
    for item in result["data"]:
        mark = "" if item["exists"] else "  [missing on disk]"
        origin = "raw input" if item["raw"] else "written by " + ", ".join(item["written_by"])
        lines.append(f"  data    {item['path'] or item['id']}  ({origin}){mark}")
    for item in result["scripts"]:
        status = f"  [{item['scan_status']}]" if item["scan_status"] in ("unreadable", "unparseable") else ""
        lines.append(f"  script  {item['path'] or item['id']}{status}")
    lines.append(f"  node    {node['path'] or node['id']}")
    for item in result["drafts"]:
        lines.append(f"  draft   {item['section']}")
    for item in result["attempts"]:
        via = ", ".join(f"{v['via']} via {v['through']}" for v in item["via"])
        lines.append(f"  attempt {item['id']}  {item['verdict'] or '(no verdict)'}  ({via})")
    for item in result["variables"]:
        ref = item["reference"]["label"] if item["reference"] else f"v{item['version']} ({item['status']})"
        lines.append(f"  variable {ref}  matched by {item['matched_by']} in {item['dataset']}")
    for link in result["links"]:
        marker = judgements.review_marker(link)
        if marker:
            lines.append(f"  link    {link['src']} --{link['type']}--> {link['dst']}{marker}")
    for item in result["breaks"]:
        repair = f" -- repair: {item['repair']['type']} mapping" if item.get("repair") else ""
        lines.append(f"  BREAK   {item['kind']}: {item['path'] or item['node']}{repair}")
    return lines

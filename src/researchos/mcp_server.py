"""Research OS's MCP stdio server (T5) -- the product's primary interface
(DESIGN.md section 1/7: the user's existing AI assistant is the front
end; this is what it talks to).

Built on the official `mcp` SDK's FastMCP wrapper rather than hand-rolling
JSON-RPC framing (constitution section 0, Occam rule 1). Runtime dependency
"mcp" approved by Owner 2026-07-22.

Each tool is a thin wrapper: open a connection, call one of the plain
functions below (which take `conn` directly -- that's what the test suite
calls, no stdio/client involved), format as text, close the connection.
Every tool states explicitly when its result is empty/unknown, per the
constitution's "无边如实返回空结构，禁止编造" -- never invented. researchos_confirm_edge
is the sole write tool; since V5 (DESIGN.md 9.1, 9.8) it records the act in
the project's `.researchos/judgements.toml` through `researchos.records.judgements.judge`
-- the one human write path every surface uses -- and the index follows.
Its verdicts are the ledger's (confirmed / rejected / withdrawn / undone),
and like the canvas it refuses a hand-drawn link. Its description tells
the calling assistant to invoke it only on an explicit human ask. A link
whose judgment is under review or in conflict is marked in every tool's
output (9.6 "Where it shows").

**Agent mode** (DESIGN.md 17.4, `researchos mcp --agent-run <run id>`): the
same server, started by an AI agent's program for one run, offers exactly
the agent's tools and not `researchos_confirm_edge` -- a judgment is never
the agent's. `researchos_complete_task`, `researchos_ask` and
`researchos_save_note` are the only writes an agent run may make through
Research OS: the process carries `RESEARCHOS_AGENT_RUN`, so every write
under the project lock refuses (`records.situation.write_guard`) unless a
tool below opened it with `agent_writes_allowed()`. They write through the
page's own paths (`workspace.save_document` / `save_tasks`: lock, backup,
history) as the agent's, and tell the engine what they did in the run's
`signals.jsonl`, which is how it later tells the agent's `research/tasks.json`
from a tampered one. The tools never raise for an expected refusal: the
answer says "Not written: <why>" and the model reads it.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import secrets
import re
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from mcp.server.fastmcp import FastMCP

from researchos import db, names, outputs as output_files, paths, query, rename, research_records, workspace
from researchos.agents import research_context as agent_research_context
from researchos import project as project_identity
from researchos.agents import labels as agent_labels
from researchos.agents import runs as agent_runs
from researchos.records import judgements
from researchos.records import lock as records_lock
from researchos.records import situation as records_situation

# Kept as module attributes for callers quoting them; the definitions live
# in researchos.paths (DESIGN.md section 8.10 rule 1).
PROJECT_DIRNAME = paths.PROJECT_DIRNAME
DB_FILENAME = paths.DB_FILENAME


class McpServerError(Exception):
    """User-facing error; caught once in main() -> "Error: <msg>" on stderr, exit 1."""


def _require_db(project_root: Path) -> Path:
    """This subsystem's copy of the gate (each raises its own error type),
    resolving through `researchos.paths.resolve_graph_db` like every other copy:
    the graph is at `~/.researchos/graphs/<id>/graph.db`, and a legacy in-project
    one is migrated here on first touch -- an MCP client may well be the
    first thing to open a project after the upgrade."""
    try:
        path = paths.resolve_graph_db(project_root)
    except paths.GraphMigrationError as exc:
        raise McpServerError(str(exc)) from exc
    if not path.exists():
        raise McpServerError(
            f"no Research OS project at {project_root} (missing its graph at {path}); "
            f"run 'researchos init {project_root}' first"
        )
    return path


class NoIndexError(McpServerError):
    """Agent mode only: the project has no graph yet (the read-only tools
    answer `NO_INDEX`; the agent's run does not need one)."""


NO_INDEX = "No index for this project yet."


@contextmanager
def _connect(project_root: Path, *, agent: bool = False):
    """Short-lived per-call connection shared by the read tools below --
    nothing is held open across calls. In agent mode the graph is only
    looked for where it would be (`paths.graph_db_path`): the one-time legacy
    migration `_require_db` may start is a write, and an agent run makes
    none through here."""
    if agent:
        path = paths.graph_db_path(project_root)
        if not path.exists():
            raise NoIndexError(NO_INDEX)
    else:
        path = _require_db(project_root)
    conn = db.connect(path)
    try:
        yield conn
    finally:
        conn.close()


# -- plain, directly-testable implementations (no FastMCP/stdio involved) ---


def trace_result(conn: Connection, node_id: str, max_hops: int = 4) -> dict[str, Any]:
    return query.trace(conn, node_id, max_hops=max_hops)


def format_trace_text(node_id: str, result: dict[str, Any]) -> str:
    """Human-readable trace text. Each hop's `source_location` (query-time-
    resolved claim line -- see researchos.query.claim_source_location/trace) is
    surfaced as `file:line` right in the summary line when present, not
    just left buried in the raw `evidence` JSON dump -- the full structured
    `result` (source_location included) is also appended separately by the
    `researchos_trace` tool below, for a scripted consumer."""
    if not result["found"]:
        return f"No such node: {node_id}"
    if not result["hops"]:
        return f"Node {node_id} exists but has no provenance edges to trace."
    lines = [f"Provenance trace for {node_id}:"]
    for hop in result["hops"]:
        evidence = json.dumps(hop["evidence"], sort_keys=True)
        location = hop.get("source_location")
        location_note = f", source_location={location['file']}:{location['line']}" if location else ""
        lines.append(
            f"  [{hop['depth']}] {hop['src']} --{hop['type']}--> {hop['dst']} "
            f"(extractor={hop['extractor']}, confidence={hop['confidence']:.2f}, "
            f"status={hop['status']}{location_note}){judgements.review_marker(hop)} evidence={evidence}"
        )
    return "\n".join(lines)


def find_nodes(conn: Connection, text: str, node_type: str | None = None) -> list[dict[str, Any]]:
    """Case-insensitive substring match against node id/title, optionally
    restricted to one node type. ValueError on an unknown node_type."""
    if node_type is not None and node_type not in db.NODE_TYPES:
        raise ValueError(f"unknown node type: {node_type!r}")
    needle = text.lower()
    types = [node_type] if node_type else sorted(db.NODE_TYPES)
    return [
        node
        for t in types
        for node in db.get_nodes_by_type(conn, t)
        if needle in node["id"].lower() or (node["title"] and needle in node["title"].lower())
    ]


def format_find_text(text: str, node_type: str | None, matches: list[dict[str, Any]]) -> str:
    if not matches:
        filt = f" (type={node_type})" if node_type else ""
        return f"No nodes matching {text!r}{filt}."
    lines = [f"Found {len(matches)} node(s) matching {text!r}:"]
    for node in matches:
        title = f' "{node["title"]}"' if node["title"] else ""
        lines.append(f"  {node['id']} ({node['type']}){title}")
    return "\n".join(lines)


def status_summary(conn: Connection) -> dict[str, Any]:
    node_counts = {t: len(db.get_nodes_by_type(conn, t)) for t in sorted(db.NODE_TYPES)}
    edge_counts = {t: 0 for t in sorted(db.EDGE_TYPES)}
    for edge in db.query_edges(conn):
        edge_counts[edge["type"]] += 1
    return {
        "nodes": node_counts, "edges": edge_counts, "pending": len(db.pending_edges(conn)),
        "review": judgements.review_count(conn),
    }


def format_status_text(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Research OS graph status:",
            "  Nodes: " + " ".join(f"{k}={v}" for k, v in summary["nodes"].items()),
            "  Edges: " + " ".join(f"{k}={v}" for k, v in summary["edges"].items()),
            f"  Pending confirmation queue: {summary['pending']}",
            f"  Judgments under review (see `researchos review`): {summary.get('review', 0)}",
        ]
    )


CONFIRM_VERDICTS = ("confirmed", "rejected", "withdrawn", "undone")


def confirm_edge(
    root: str | Path,
    project_id: str | None,
    src: str,
    dst: str,
    type: str,  # noqa: A002 -- the tool's own parameter name
    extractor: str,
    new_status: str,
    note: str | None = None,
) -> str:
    """The human act on one machine link, through the one write path
    (`researchos.records.judgements.judge`, `via = "mcp"`): written to the ledger
    first, then applied. ValueError for an unknown edge type or verdict; a
    refusal (a hand-drawn link, no such edge, a ledger that cannot be
    trusted, a moved or pre-V5 project) is the tool's answer, not an
    exception -- nothing was written."""
    if type not in db.EDGE_TYPES:
        raise ValueError(f"unknown edge type: {type!r}")
    if new_status not in CONFIRM_VERDICTS:
        raise ValueError(f"unknown verdict: {new_status!r} (one of {', '.join(CONFIRM_VERDICTS)})")
    label = f"{src} --{type}--> {dst} (extractor={extractor})"
    try:
        judged = judgements.judge(
            Path(root), (src, dst, type, extractor), new_status, via="mcp", note=note, expected_id=project_id,
        )
    except judgements.JudgementRefused as exc:
        if exc.code == "no_such_link":
            return f"No such edge: {label}; nothing changed."
        why = f" ({exc.message_zh})" if exc.message_zh else ""
        return f"Not written: {exc}{why}"
    except records_situation.WriteRefused as exc:
        return f"Not written: {exc}"
    except records_lock.ProjectLockError as exc:
        return f"Not written: could not take the project lock ({exc})"
    state = judged.state
    tail = ""
    if state is not None and state["outcome"] != "applied":
        reason = state.get("reason") or state["outcome"]
        tail = f" Not applied: {reason} ({judgements.REASON_LABELS.get(reason, '')}) -- see `researchos review`."
    return (
        f"Edge {label}: recorded {new_status!r} in .researchos/judgements.toml ({judged.entry.id}); "
        f"status set to {judged.status!r}.{tail}"
    )


# -- agent mode: the tools of one AI agent run (DESIGN.md 17.4) -------------------


NOT_WRITTEN = "Not written: "
ASK_LIMIT = 4000
TITLE_LIMIT = 200
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class _Refused(Exception):
    """An expected refusal of an agent tool: its text is the tool's answer."""


def _one_line(text: object, limit: int | None = None) -> str:
    """`text` as one line (any run of whitespace, NUL included, becomes a space)."""
    line = re.sub(r"[\s\0]+", " ", text if isinstance(text, str) else "").strip()
    return line[:limit].rstrip() if limit is not None else line


def _agent_run(project_id: str, run_id: str) -> tuple[dict[str, Any], agent_labels.Agent]:
    """The run the tools belong to, and its agent. Only a run that is still
    going may write: after it was stopped, timed out or finished, a program
    it left behind cannot mark the task done or add notes. `_Refused` otherwise."""
    try:
        run = agent_runs.load_run(project_id, run_id)
    except ValueError as exc:
        raise _Refused(f"{exc}") from exc
    if run is None:
        raise _Refused("this run is not known to Research OS")
    if run.get("state") not in agent_runs.ACTIVE_STATES:
        raise _Refused(f"this run is no longer going (it is {run.get('state')})")
    try:
        agent = agent_labels.parse_owner(run.get("owner"))
    except ValueError as exc:
        raise _Refused(str(exc)) from exc
    if agent is None:
        raise _Refused("this run has no agent")
    return run, agent


def _author(run: dict[str, Any], agent: agent_labels.Agent) -> dict[str, Any]:
    """The change history's attribution of what this run's tools write (17.5)."""
    by = run.get("by")
    return {"source": "agent", "who": agent_labels.who_value(agent, by if isinstance(by, str) and by else None),
            "note": run["id"]}


def _answering(work) -> str:
    """Run `work()` (returns the tool's answer) turning every expected
    refusal into the answer `Not written: <why>`; nothing raises."""
    try:
        return work()
    except _Refused as exc:
        return f"{NOT_WRITTEN}{exc}"
    except workspace.WorkspaceError as exc:
        if exc.code == "too_large":
            return f"{NOT_WRITTEN}the text is larger than {workspace.TEXT_LIMIT // 1024} KB; shorten it and call the tool again."
        if exc.code == "conflict":
            return f"{NOT_WRITTEN}{exc}. Call the tool again."
        return f"{NOT_WRITTEN}{exc}"
    except records_situation.WriteRefused as exc:
        return f"{NOT_WRITTEN}{exc}"
    except records_lock.ProjectLockError as exc:
        return f"{NOT_WRITTEN}could not take the project lock ({exc})"
    except OSError as exc:  # an unwritable run folder or project folder
        return f"{NOT_WRITTEN}{exc}"


def _stamp(lang: str) -> str:
    now = datetime.now().astimezone()
    if lang == "en":
        return f"{now.day} {_MONTHS[now.month - 1]} {now.year} {now:%H:%M}"
    return f"{now:%Y-%m-%d %H:%M}"


def _attribution_line(run: dict[str, Any], agent: agent_labels.Agent) -> str:
    """The first line under the result's heading: which agent wrote it,
    when, for whom (17.5 "Attribution")."""
    by_name = _one_line(run.get("by_name"), 100)
    if run.get("lang") == "en":
        parts = [f"Done by {agent.label}", _stamp("en")] + ([f"assigned by {by_name}"] if by_name else [])
        parts.append(f"run {run['id']}")
    else:
        parts = [f"由 {agent.label} 完成", _stamp("zh")] + ([f"{by_name} 指派"] if by_name else [])
        parts.append(f"运行 {run['id']}")
    return "> " + " · ".join(parts)


#: A heading made from the task's own words is cut here, at a word boundary where there is one. The rule is
#: `outputs.task_title`'s: the views name the task that produced a file with the same words (DESIGN.md 19.6).
DEFAULT_TITLE_LIMIT = output_files.TITLE_CUT


def _result_title(title: str | None, task_text: str) -> str:
    """The report's heading: the agent's title, else the task's
    first line cut to `DEFAULT_TITLE_LIMIT` characters (a task can be a
    paragraph; a heading is a few words)."""
    given = re.sub(r"^#+\s*", "", _one_line(title, TITLE_LIMIT))
    if given:
        return given
    return output_files.task_title(task_text) or "Task result"


def _complete(root: Path, project_id: str, run_id: str, report: str, title: str | None,
              outputs: list[dict[str, str]] | None = None,
              research_context: dict[str, Any] | None = None) -> str:
    if not isinstance(report, str) or not report.strip():
        raise _Refused("the report is empty; write what was done, the commands run, the files produced and the "
                       "key numbers exactly as the runs gave them, then call the tool again")
    report = report.strip()
    if len(report.encode("utf-8", "replace")) > workspace.TEXT_LIMIT - 4096:
        raise _Refused(f"the report is larger than {workspace.TEXT_LIMIT // 1024} KB; shorten it and call the tool again")
    run, agent = _agent_run(project_id, run_id)
    author = _author(run, agent)
    with records_situation.agent_writes_allowed(), records_situation.write_guard(
        root, project_id, human=True, timeout=10.0,
    ):
        tasks, _revision = workspace.read_tasks(root)
        task = next((t for t in tasks if t["id"] == run.get("task_id")), None)
        if task is None:
            raise _Refused(f"task {run.get('task_id')!r} no longer exists in research/tasks.json, so there is "
                           "nothing to complete; tell the person in your final message")
        if "output_folders" in run and outputs is None:
            raise _Refused("Include outputs=[{kind, path, title}] for the files you produced; use outputs=[] if there are none")
        checked = output_files.validate(root, outputs, run.get("output_folders") or output_files.folders(root)["folders"]) if outputs is not None else []
        # 19.7: a file the run created says so (`created: true`), as the run's before-picture shows; one that was there
        # before -- the researcher's own draft an agent edited and declared -- does not, and stays what it was
        if checked:  # the before-picture is read only when there are outputs to flag
            made = output_files.made_by_run(project_id, run_id, [item["path"] for item in checked])
            checked = [{**item, "created": True} if item["path"] in made else item for item in checked]
        if checked and task.get("report") and (root / task["report"]).is_file():
            output_files._entries(root, output_files.catalog_path(task["report"]))
        context = agent_research_context.prepare(root, run, research_context, checked)
        heading = _result_title(title, task["text"])
        # The record's one top heading is ours: a report that opens with its own
        # `# ...` keeps it one level down, so the document has a single title.
        body_text = re.sub(r"\A#(?=[ \t])", "##", report)
        content = f"# {heading}\n\n{_attribution_line(run, agent)}\n\n{body_text}\n"
        if checked:
            from urllib.parse import quote

            # 19.12 item 6: the record's one list of outputs is this section; the brief tells the agent to add none
            label = "Registered output files" if run.get("lang") == "en" else "已登记的输出文件"
            content += f"\n## {label}\n\n"
            for item in checked:
                caption = item["title"].replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
                target = quote(posixpath.relpath(item["path"], "research/results"), safe="/")
                content += f"- [{caption}]({target})\n"
            if (root / output_files.FOLDERS_PATH).is_file():
                content += "- [" + ("Output folders" if run.get("lang") == "en" else "输出目录") + "](../folders.json)\n"
        body: dict[str, Any] = {"content": content}
        existing = task.get("report")
        if existing:  # a run of this task wrote it before (a task sent back): the same record, rewritten
            try:
                body.update(path=existing, revision=workspace.document(root, existing)["revision"])
            except workspace.WorkspaceError as exc:
                if exc.code != "not_found":
                    raise
                existing = None
        if not existing:
            body.update(kind="result", revision=None, path="research/results/" + secrets.token_hex(16) + ".md")
        if checked:
            body["content"] += f"- [{label}]({Path(output_files.catalog_path(body['path'])).name})\n"
        context_revision = None
        if context is not None:
            context_rel = research_records.path_for(body['path'])
            raw_context = workspace._read(root, context_rel)
            research_records.parse(raw_context)
            if (root / body['path']).is_file():
                output_files._entries(root, output_files.catalog_path(body['path']))
            frozen = run.get('research_handoff')
            if isinstance(frozen, dict):
                if frozen.get('target_report') != task.get('report'):
                    raise _Refused('The task result association changed since this run began; start a new run')
                context_revision = frozen.get('target_revision')
            else:
                context_revision = workspace._revision(raw_context)
            workspace._check_revision({'revision': context_revision}, raw_context)
        saved = workspace.save_document(root, body, author=author)
        path = saved["path"]
        registration = output_files.register(root, checked, run, path, author)
        saved_context = (research_records.save_agent(root, path, context, author, context_revision)
                         if context is not None else None)
        notes = [saved["history_error"]] if saved.get("history_error") else []
        if saved_context is not None and saved_context.get("history_warning"):
            notes.append(saved_context["history_warning"])
        saved_tasks = None
        for attempt in (1, 2):  # a second go only when the file moved under an external editor
            tasks, revision = workspace.read_tasks(root)
            if not any(t["id"] == run["task_id"] for t in tasks):
                raise _Refused(f"the report {path} was saved, but task {run['task_id']!r} is gone from "
                               "research/tasks.json, so it was not marked done")
            marked = [{**t, "done": True, "report": path} if t["id"] == run["task_id"] else t for t in tasks]
            try:
                saved_tasks = workspace.save_tasks(root, {"tasks": marked, "revision": revision}, author=author)
                break
            except workspace.WorkspaceError as exc:
                if exc.code != "conflict" or attempt == 2:
                    raise _Refused(f"the report {path} was saved, but the task could not be marked done "
                                   f"({exc}); call the tool again") from exc
        if saved_tasks is None:  # not reachable: the loop either breaks or raises
            raise _Refused(f"the report {path} was saved, but the task could not be marked done")
        if saved_tasks.get("history_error"):
            notes.append(saved_tasks["history_error"])
        extra = {"outputs_sha": registration[1], "outputs_catalog": registration[0]} if registration else {}
        if saved_context is not None:
            extra.update(context_path=saved_context['path'], context_sha=saved_context['written_revision'])
        agent_runs.append_signal(
            project_id, run_id, "complete", path=path, tasks_sha=saved_tasks["tasks_revision"],
            paths=[path, workspace.TASKS_PATH] + ([registration[0]] if registration else [])
                  + ([saved_context['path']] if saved_context is not None else []), **extra,
        )
    tail = " (The change history could not be updated; the person is told.)" if notes else ""
    return (f"Saved the report {path} and marked the task done. "
            f"Your work on this task is finished; stop now.{tail}")


def agent_complete_task(root: str | Path, project_id: str, run_id: str, report: str, title: str | None = None,
                        outputs: list[dict[str, str]] | None = None,
                        research_context: dict[str, Any] | None = None) -> str:
    """`researchos_complete_task`: write the task's report (a new
    `research/results/` document, or the one an earlier run of this task
    wrote), mark the task done and link the record as its `report`, as the
    agent (DESIGN.md 17.4). Returns the answer text; an expected refusal
    (empty report, over 200 KB, task gone, project locked or read-only, a run
    that is over) is `Not written: <why>`, with nothing written. The
    `complete` signal tells the engine which `research/tasks.json` is the
    tool's."""
    return _answering(lambda: _complete(Path(root).resolve(), project_id, run_id, report, title, outputs, research_context))


def _ask(project_id: str, run_id: str, question: str) -> str:
    text = question.strip() if isinstance(question, str) else ""
    if not text:
        raise _Refused("the question is empty; ask one clear question")
    if len(text) > ASK_LIMIT:
        raise _Refused(f"the question is longer than {ASK_LIMIT} characters; shorten it")
    _agent_run(project_id, run_id)
    agent_runs.append_signal(project_id, run_id, "ask", question=text)
    return ("Your question is recorded and will be shown to the person. Stop working now; their answer will "
            "continue this conversation.")


def agent_ask(project_id: str, run_id: str, question: str) -> str:
    """`researchos_ask`: record one question for the person (a signal; no
    project file is written). The engine shows the task as waiting for an
    answer once the agent's program ends."""
    return _answering(lambda: _ask(project_id, run_id, question))


def _save_note(root: Path, project_id: str, run_id: str, title: str, content: str) -> str:
    if not isinstance(title, str) or not title.strip():
        raise _Refused("a note needs a title")
    if len(title.strip()) > TITLE_LIMIT or any(c in title for c in "\r\n\0"):
        raise _Refused(f"a note's title is one line of at most {TITLE_LIMIT} characters")
    if not isinstance(content, str):
        raise _Refused("a note needs text")
    if len(content.encode("utf-8", "replace")) > workspace.TEXT_LIMIT - 4096:
        raise _Refused(f"the note is larger than {workspace.TEXT_LIMIT // 1024} KB; split it")
    run, agent = _agent_run(project_id, run_id)
    with records_situation.agent_writes_allowed(), records_situation.write_guard(
        root, project_id, human=True, timeout=10.0,
    ):
        saved = workspace.save_document(
            root, {"kind": "note", "title": title.strip(), "content": content, "revision": None},
            author=_author(run, agent),
        )
        path = saved["path"]
        agent_runs.append_signal(project_id, run_id, "note", path=path, paths=[path])
    return f"Saved the note {path}."


def agent_save_note(root: str | Path, project_id: str, run_id: str, title: str, content: str) -> str:
    """`researchos_save_note`: a new `research/notes/` document, as the agent."""
    return _answering(lambda: _save_note(Path(root).resolve(), project_id, run_id, title, content))


def build_agent_server(project_root: str | Path, project_id: str, run_id: str) -> FastMCP:
    """The server of one agent run (`researchos mcp --agent-run`): the
    agent's three tools plus the read-only graph tools, and no
    `researchos_confirm_edge` (DESIGN.md 17.4). The tools are bound to this
    run, project and folder; the model cannot name another."""
    root = Path(project_root).resolve()
    mcp = FastMCP(names.MCP_SERVER_NAME)

    @mcp.tool()
    def researchos_complete_task(report: str, title: str | None = None,
                                 outputs: list[dict[str, str]] | None = None,
                                 research_context: dict[str, Any] | None = None) -> str:
        """Call this ONCE, when the task you were given is finished. `report`
        is the report, in Markdown: what you did, the commands you
        ran, and the key numbers exactly as your actual runs gave them --
        never invented or rounded from memory. Name a file where it matters,
        but add no list of your outputs: Research OS appends the section
        "Registered output files" to the record from `outputs`.
        `title` is a short heading naming the result, a few words (default:
        the start of the task). This saves
        the report in the project, marks the task done
        and links the record to it. After it answers, stop: do nothing
        more. `outputs` lists each final file as {kind, path, title}; kinds:
        code, data, figure, table, literature, document, variable. Paths must
        be inside the assigned folder for that kind. Use [] if no files were
        produced. Bibliographies accept BibTeX or CSV/JSON reference exports;
        variable drafts use the native variable-definition TOML schema and
        remain unconfirmed. For research tasks, include `research_context` with
        question, method, finding, limits and next_step; attempts (existing node IDs),
        variables ([{id, version}] for explicit existing versions), references/materials
        (project paths), optional purposes (path to short note), and optional
        attempt_draft {description, result, parents}. A suggested attempt is never
        automatically registered or adopted. Earlier findings are background only.
        A research task also hands back, in the same `research_context`: `headline`
        (the answer to the task's question in one plain sentence, at most 200
        characters); `key_numbers` (at most five {what, value, file}: `value` is text
        written exactly as the cited project file has it, which Research OS checks);
        `undone` (what you did not finish, and why); `decide` (at most three questions
        only the researcher can answer); `suggested_assessment` {status, reason}
        (`status` one of the project's assessment statuses, `reason` at most 1,000
        characters). The suggested assessment is only a suggestion: the researcher
        confirms or changes it, you never write the attempt table, and
        `assessment_confirmed` is refused. Write no note besides this report unless the
        task asks for one.
        If you cannot finish or a decision belongs to the person, call
        researchos_ask instead."""
        return agent_complete_task(root, project_id, run_id, report, title, outputs, research_context)

    @mcp.tool()
    def researchos_ask(question: str) -> str:
        """Call this when you are blocked or a decision belongs to the person
        (which of two data sources, a judgment about a result, a missing
        file or credential). `question` is one clear question with what you
        already tried and the options you see. After it answers, STOP
        working: the person's answer will continue this conversation. Do
        not call it for what you can find out yourself."""
        return agent_ask(project_id, run_id, question)

    @mcp.tool()
    def researchos_save_note(title: str, content: str) -> str:
        """Save a note in the project's notes (Markdown): something worth
        keeping that is not the task's final report (a finding along the
        way, a data caveat, how a dataset was obtained). `title` is one
        line. Does not finish the task; researchos_complete_task does."""
        return agent_save_note(root, project_id, run_id, title, content)

    _register_graph_tools(mcp, root, agent=True)
    return mcp


# -- FastMCP server assembly --------------------------------------------------


def _register_graph_tools(mcp: FastMCP, root: Path, *, agent: bool = False) -> None:
    """The three read-only tools, identical in both modes. In agent mode a
    project without a graph answers `NO_INDEX` instead of failing."""

    def answer(work) -> str:
        try:
            with _connect(root, agent=agent) as conn:
                return work(conn)
        except NoIndexError:
            return NO_INDEX

    @mcp.tool()
    def researchos_trace(node_id: str) -> str:
        """Return the full provenance/evidence chain for a node (e.g. a
        figure, claim, or experiment id): how it traces back to the
        commit(s)/experiment(s) that produced it, and forward to the paper
        section(s)/reference(s) that cite or rely on it. Each hop lists its
        edge type, extractor, confidence, status, and evidence. Ends with a
        structured JSON block. States explicitly when the node is unknown or
        has no edges -- never invents a chain."""
        def work(conn: Connection) -> str:
            result = trace_result(conn, node_id)
            return format_trace_text(node_id, result) + "\n\nJSON:\n" + json.dumps(result, sort_keys=True)
        return answer(work)

    @mcp.tool()
    def researchos_find(text: str, node_type: str | None = None) -> str:
        """Search graph nodes by case-insensitive substring match against id
        or title. Use this BEFORE researchos_trace whenever the user gives a vague
        description instead of an exact node id (e.g. "Figure 4",
        "result.png") -- researchos_find locates the candidate id(s), then
        researchos_trace explains their provenance. Optional node_type filters to
        one of: project/experiment/commit/figure/section/claim/reference/
        contributor. States explicitly when nothing matches."""
        return answer(lambda conn: format_find_text(text, node_type, find_nodes(conn, text, node_type)))

    @mcp.tool()
    def researchos_status() -> str:
        """Report whole-graph node and edge counts by type, plus the size of
        the pending human-confirmation queue."""
        return answer(lambda conn: format_status_text(status_summary(conn)))


def build_server(project_root: str | Path, project_id: str | None = None) -> FastMCP:
    """Register the four tools against project_root's graph (resolved by
    researchos.paths -- outside the project since DESIGN.md section 8.10 rule 1).
    `project_id` is the id `main` opened the project with (None: read it
    from the folder now)."""
    root = Path(project_root).resolve()
    if project_id is None:
        got = records_situation.read_identity(root)
        project_id = got.identity.id if got.identity is not None else None
    mcp = FastMCP(names.MCP_SERVER_NAME)
    _register_graph_tools(mcp, root)

    @mcp.tool()
    def researchos_confirm_edge(
        src: str, dst: str, type: str, extractor: str, new_status: str, note: str | None = None,
    ) -> str:
        """Human judgment channel for exactly one machine-extracted edge,
        recorded in the project's .researchos/judgements.toml. Call this ONLY when
        the user has explicitly asked to confirm, reject, withdraw or undo
        a judgment on a specific edge (e.g. "confirm that figure 4 is
        backed by run xyz") -- never speculatively or during routine
        tracing. new_status must be one of: confirmed / rejected /
        withdrawn (back to the machine's status) / undone (takes back the
        last act). `note` is optional. Hand-drawn links (extractor
        "mapping") are refused: they live in .researchos/mappings.toml. States
        explicitly when no matching edge exists or nothing was written."""
        return confirm_edge(root, project_id, src, dst, type, extractor, new_status, note)

    return mcp


def _agent_mode(flag: str | None) -> str | None:
    """The run id this server serves, or None for the plain four-tool server.
    `--agent-run` and `RESEARCHOS_AGENT_RUN` must agree (DESIGN.md 17.4/17.5):
    a process an agent started carries the variable and is only ever the
    agent's server; the flag without the variable would serve the agent's
    write tools outside the run's guard. McpServerError otherwise."""
    env = os.environ.get(records_situation.AGENT_RUN_ENV, "")
    if flag is None and not env:
        return None
    if flag is None:
        raise McpServerError(
            f"{records_situation.AGENT_RUN_ENV} is set (inside an AI agent run): the server runs only as "
            "'researchos mcp --agent-run <run id>'"
        )
    if not agent_runs.RUN_ID_RE.match(flag):
        raise McpServerError(f"--agent-run: not a run id: {flag!r}")
    if env != flag:
        raise McpServerError(
            f"--agent-run {flag} does not match {records_situation.AGENT_RUN_ENV}="
            f"{env or '(not set)'}; the agent tools are served only to their own run"
        )
    return flag


def main(argv: list[str] | None = None) -> int:
    # The one path convention of every subcommand (DESIGN.md 9.12): the
    # project as a positional path or as --path.
    from researchos.cli import CliError, add_project_path, settle_project_path  # noqa: PLC0415 -- the CLI imports this module lazily

    parser = argparse.ArgumentParser(prog="researchos mcp", description="Run the Research OS MCP stdio server.")
    add_project_path(parser, help=(
        "project root of an initialized Research OS project (default: '.'), or give it as --path; the "
        "graph itself lives at ~/.researchos/graphs/<id>/graph.db, see researchos.paths"
    ))
    parser.add_argument("--agent-run", dest="agent_run", default=None, metavar="RUN_ID", help=(
        "serve the tools of one AI agent run (DESIGN.md 17.4): complete_task, ask, save_note and the read-only "
        f"tools; needs {records_situation.AGENT_RUN_ENV} set to the same id. Started by the app, not by hand"
    ))
    args = parser.parse_args(argv)
    try:
        run_id = _agent_mode(args.agent_run)
    except McpServerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    try:
        # DESIGN.md 12.8: the home's new name first (the project's follows in
        # the identity check below).
        rename.ensure_home_renamed()
    except rename.RenameError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    try:
        chosen = settle_project_path(args.path, args.path_flag, args.path_default)
    except CliError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    project_root = Path(chosen).resolve()
    try:
        # The identity check first (DESIGN.md 9.4): a copy, a home that
        # cannot be checked, a lost or unreadable identity stops here and
        # nothing is written; a moved project is adopted.
        if not project_root.is_dir():
            raise McpServerError(f"{project_root} is not a directory")
        try:
            # Adopting a moved project takes the write guard, which refuses
            # inside an agent run unless a run's tool opened it: this is
            # the one write the server itself makes (the person's own check).
            with records_situation.agent_writes_allowed():
                opened = project_identity.open_project(project_root)
        except (project_identity.ProjectBlocked, project_identity.AnswerRefused, records_situation.WriteRefused) as exc:
            raise McpServerError(str(exc)) from exc
        if run_id is None:
            _require_db(project_root)
            server = build_server(project_root, opened.project_id)
        else:
            # An agent's run needs no graph; it needs its own run.
            if opened.project_id is None:
                raise McpServerError(f"{project_root} is not an initialized Research OS project (no identity)")
            run = agent_runs.load_run(opened.project_id, run_id)
            if run is None:
                raise McpServerError(f"run {run_id} does not exist for project {opened.project_id}")
            recorded = run.get("root")
            if not isinstance(recorded, str) or Path(recorded).resolve() != project_root:
                raise McpServerError(f"run {run_id} belongs to another folder ({recorded}), not {project_root}")
            server = build_agent_server(project_root, opened.project_id, run_id)
    except McpServerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())

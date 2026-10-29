"""An attempt holds its tasks (DESIGN.md 21.1): the reading side.

`workspace.py` writes and checks the link -- a task's `attempt` (an attempt's node id) and `after` (the task it
continues). This module reads it for the route map: the tasks of an attempt in `after` order, each one's state, the
figures its card shows (how many tasks, how many unfinished, how many handbacks wait to be opened) and the first line
of its latest handback. Reads only; a task list or a report that cannot be read leaves that part out, never the map.

States are codes -- `not_started`, `running`, `needs_answer`, `to_read`, `done`, `failed`, `out_of_bounds` -- and the
page words them (未开始, 运行中, 等你回答, 等你看, 已完成, 失败, 越过了限制): the engine says no sentence here (13.1).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from researchos import handbacks, workspace
from researchos.agents import labels as agent_labels

HEADLINE_LIMIT = 200


def ordered(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The tasks of one attempt in `after` order: each task stands right after the one it continues, a task that
    continues none (or one that is not in `tasks`) by the order of the list. A circle written by hand (`after`
    pointing round) is not followed: its tasks come last, in the order of the list."""
    ids = {task["id"] for task in tasks}
    followers: dict[str, list[dict[str, Any]]] = {}
    roots = []
    for task in tasks:
        after = task.get("after")
        if after and after in ids and after != task["id"]:
            followers.setdefault(after, []).append(task)
        else:
            roots.append(task)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(first: dict[str, Any]) -> None:
        pending = [first]
        while pending:
            task = pending.pop()
            if task["id"] in seen:
                continue
            seen.add(task["id"])
            out.append(task)
            pending.extend(reversed(followers.get(task["id"], [])))

    for task in roots:
        walk(task)
    for task in tasks:
        walk(task)
    return out


def state_of(task: dict[str, Any], run: dict[str, Any] | None, waiting: bool) -> str:
    """A task's state in one code, from its done mark, its latest run (`run`, None when it never ran) and whether
    its handback waits to be opened (`waiting`): a run going on says 运行中 and one that asked a question 等你回答,
    whatever else is true; an unopened handback says 等你看; a task not marked done reads from how its last run
    ended (越过了限制, 失败 -- a run that failed, was interrupted or ended without a report); a task the person
    marked done is 已完成; anything else, including a run the person stopped, has not started."""
    state = (run or {}).get("state")
    if state in ("preparing", "running"):
        return "running"
    if state == "needs_input":
        return "needs_answer"
    if not task.get("done"):
        if state == "out_of_bounds":
            return "out_of_bounds"
        if state in ("failed", "interrupted", "ended"):
            return "failed"
    if waiting:
        return "to_read"
    return "done" if task.get("done") else "not_started"


def owner_label(owner: str) -> str:
    """The owner as people read it: an agent by its label (`Claude Code · sonnet`), a person as typed."""
    try:
        agent = agent_labels.parse_owner(owner)
    except ValueError:
        agent = None
    return agent.label if agent is not None else owner


def _first_line(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    for line in value.splitlines():
        line = line.strip()
        if line:
            return line[:HEADLINE_LIMIT]
    return None


def _handback(root: Path, report: str, waiting: bool) -> dict[str, Any] | None:
    """What a card says of the handback whose report is `report`: its title (the report's first heading, else its
    file name), the context's `headline` when it has one (21.2) and `line`, the one of them the card shows. The
    context is read as the JSON it is, so a context from before `headline` and one from after read alike."""
    try:
        raw = workspace._read(root, report)  # noqa: SLF001
        if raw is None:
            return None
        title = workspace._heading(workspace._decode(raw)[:4096]) or Path(report).stem  # noqa: SLF001
    except (workspace.WorkspaceError, OSError):
        return None
    headline = None
    try:
        sidecar = workspace._read(root, report.removesuffix(".md") + ".context.json")  # noqa: SLF001
        context = json.loads(sidecar)["context"] if sidecar else {}
        headline = _first_line(context.get("headline"))
    except (workspace.WorkspaceError, OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return {"report": report, "title": title, "headline": headline, "line": headline or title, "unopened": waiting}


def _latest_runs(project_id: str | None, wanted: set[str]) -> dict[str, dict[str, Any]]:
    """The newest run of each task in `wanted`, found by reading the project's runs newest first and stopping at the
    last one wanted (18.4 item 2)."""
    found: dict[str, dict[str, Any]] = {}
    if not project_id or not wanted:
        return found
    from researchos.agents import runs as run_store  # noqa: PLC0415

    try:
        for run in run_store.iter_runs(project_id):
            task = run.get("task_id")
            if task in wanted and task not in found:
                found[task] = run
                if len(found) == len(wanted):
                    break
    except OSError:
        pass
    return found


def by_attempt(root: str | Path, project_id: str | None, person: str | None,
               attempts: Iterable[str]) -> dict[str, dict[str, Any]]:
    """For each of `attempts` (node ids) that has tasks: `tasks` (how many), `unfinished` (not marked done),
    `unopened` (its handbacks `person` has not opened: `handbacks.unopened`), `latest` (the handback whose report was
    written last, `_handback`; None when no task has one) and `items`, the tasks in `after` order with their `owner`
    and its `owner_label`, `due`, `done`, `after`, `report`, `state` (`state_of`) and `unopened`. An attempt with no
    tasks has no entry, and a task naming an attempt that is not in `attempts` is left out."""
    root = Path(root)
    wanted = set(attempts)
    try:
        tasks, _revision = workspace.read_tasks(root)
    except (workspace.WorkspaceError, OSError):
        return {}
    mine = [task for task in tasks if task.get("attempt") in wanted]
    if not mine:
        return {}
    waiting = set(handbacks.unopened(project_id, person))
    runs = _latest_runs(project_id, {task["id"] for task in mine if not task.get("done") or task.get("report") in waiting})
    out: dict[str, dict[str, Any]] = {}
    for attempt in wanted:
        own = ordered([task for task in mine if task["attempt"] == attempt])
        if not own:
            continue
        items = []
        for task in own:
            unopened = bool(task.get("report")) and task["report"] in waiting
            items.append({"id": task["id"], "text": task["text"], "owner": task.get("owner", ""),
                          "owner_label": owner_label(task.get("owner", "")), "due": task.get("due", ""),
                          "done": bool(task.get("done")), "after": task.get("after"), "report": task.get("report"),
                          "state": state_of(task, runs.get(task["id"]), unopened), "unopened": unopened})
        newest, newest_at = None, -1.0
        for item in items:
            if not item["report"]:
                continue
            try:
                at = (root / item["report"]).stat().st_mtime
            except OSError:
                continue
            if at > newest_at:
                newest, newest_at = item, at
        out[attempt] = {"tasks": len(items), "unfinished": sum(1 for item in items if not item["done"]),
                        "unopened": sum(1 for item in items if item["unopened"]),
                        "latest": _handback(root, newest["report"], newest["unopened"]) if newest else None,
                        "items": items}
    return out

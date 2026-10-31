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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from researchos import handbacks, workspace
from researchos.agents import labels as agent_labels

HEADLINE_LIMIT = 200
#: The states that ask the person who started the run for something (21.4, 等你处理): the code of `state_of`, and
#: the kind of row each makes.
ATTENTION = {"needs_answer": "answer", "failed": "failed", "out_of_bounds": "out_of_bounds"}


def parse_time(value: Any) -> datetime | None:
    """A time as the run store and the engine write it (an ISO string, `Z` or an offset or neither, the last read as
    local time); None for anything else."""
    if not isinstance(value, str) or not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.astimezone()


def _ran_at(run: dict[str, Any] | None) -> str | None:
    """When the run last did something of its own, as the run store wrote it: the later of its start and its end, else
    (a run not started yet) its creation."""
    found = [(when, value) for value in ((run or {}).get("started"), (run or {}).get("ended")) if (when := parse_time(value))]
    if not found:
        value = (run or {}).get("created")
        return value if parse_time(value) else None
    return max(found, key=lambda pair: pair[0])[1]


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
    whatever else is true; a run that went out of bounds says 越过了限制 even when the agent marked the task done
    (18.1 item 4: the done mark stays the agent's and the run is what warns -- 等你看 and 已完成 would hide it); an
    unopened handback says 等你看; a task not marked done reads from how its last run ended (失败 -- a run that
    failed, was interrupted or ended without a report); a task the person marked done is 已完成, and so is one
    that is not but whose `report` is in and whose latest run is the one that handed it back (state done, not
    undone) or does not exist -- older versions did not set the done mark, and a copy of a project or another Mac
    has no run records; anything else, including a run the person stopped, has not started. A run the person undid
    (`reverted`, 撤回本次运行) has been dealt with: it no longer asks, fails or warns, so the task reads as if its
    latest run were not there (`attention` leaves the same run out of 等你处理)."""
    state = None if (run or {}).get("reverted") else (run or {}).get("state")
    if state in ("preparing", "running"):
        return "running"
    if state == "needs_input":
        return "needs_answer"
    if state == "out_of_bounds":
        return "out_of_bounds"
    if not task.get("done") and state in ("failed", "interrupted", "ended"):
        return "failed"
    if waiting:
        return "to_read"
    if task.get("done"):
        return "done"
    if task.get("report") and (run is None or state == "done"):
        return "done"
    return "not_started"


def owner_label(owner: str) -> str:
    """The owner as people read it: an agent by its label (`Claude Code · sonnet`), a person as typed."""
    try:
        agent = agent_labels.parse_owner(owner)
    except ValueError:
        agent = None
    return agent.label if agent is not None else owner


def _agent_owned(task: dict[str, Any]) -> bool:
    try:
        return agent_labels.parse_owner(task.get("owner")) is not None
    except ValueError:
        return False


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


def _read_runs(project_id: str | None) -> list[dict[str, Any]]:
    """The project's runs, newest first: the one reading of the run store an answer makes (21.4). Nothing when the project
    has no identity or the store cannot be read."""
    if not project_id:
        return []
    from researchos.agents import runs as run_store  # noqa: PLC0415

    try:
        return list(run_store.iter_runs(project_id))
    except (OSError, ValueError):
        return []


def _latest_runs(runs: list[dict[str, Any]], wanted: set[str]) -> dict[str, dict[str, Any]]:
    """The newest run of each task in `wanted`, found in `runs` (newest first) and stopping at the last one wanted
    (18.4 item 2)."""
    found: dict[str, dict[str, Any]] = {}
    if not wanted:
        return found
    for run in runs:
        task = run.get("task_id")
        if task in wanted and task not in found:
            found[task] = run
            if len(found) == len(wanted):
                break
    return found


def _look(root: Path, project_id: str | None, person: str | None, tasks: list[dict[str, Any]],
          shared: dict[str, Any] | None) -> tuple[set[str], dict[str, dict[str, Any]], tuple[list[str], list[dict[str, Any]]]]:
    """What the states of `tasks` are read from: the handbacks `person` has not opened and the latest run of each task
    that may need one -- a task not marked done, one whose handback waits, and a task an agent finished, for its run may
    have gone out of bounds, which 已完成 must not hide (18.1). The third part is the reading itself: the unopened
    handbacks in order and the runs they were worked out from, which `reading` hands to the list of 交回 rows. `shared`,
    a dict the caller hands to every function of one answer, keeps it, so that the cards and 等你处理 of one answer read
    the run store once (21.4)."""
    if shared is not None and "look" in shared:
        return shared["look"]
    runs = _read_runs(project_id)
    ordered = handbacks.unopened(project_id, person, root, runs)
    waiting = set(ordered)
    latest = _latest_runs(runs, {task["id"] for task in tasks
                                 if not task.get("done") or task.get("report") in waiting or _agent_owned(task)})
    if shared is not None:
        shared["look"] = (waiting, latest, (ordered, runs))
    return waiting, latest, (ordered, runs)


def reading(root: str | Path, project_id: str | None, person: str | None, tasks: list[dict[str, Any]],
            shared: dict[str, Any] | None = None) -> tuple[list[str], list[dict[str, Any]]]:
    """The handbacks `person` has not opened (`handbacks.unopened`, oldest first) and the runs they were worked out from,
    as the states of this answer read them (`_look`'s keep): 等你处理 lists its 交回 rows from the very reading the cards
    and the task rows say 等你看 from, so that they cannot disagree and the run store is read once."""
    return _look(Path(root), project_id, person, tasks, shared)[2]


def attention(root: str | Path, project_id: str | None, person: str | None,
              shared: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The tasks whose latest run `person` started and that read 等你回答, 失败 or 越过了限制 in `state_of` -- the
    one reading of a task's state, so 等你处理 and the cards never disagree --, each `{task, run, kind}` with the kind
    `ATTENTION` names (`answer`, `failed`, `out_of_bounds`), in the order of the task list. A run that was undone
    (`reverted`) has been dealt with and is left out, and so is a task the list no longer has."""
    root = Path(root)
    try:
        tasks, _revision = workspace.read_tasks(root)
    except (workspace.WorkspaceError, OSError):
        return []
    waiting, runs, _read = _look(root, project_id, person, tasks, shared)
    out = []
    for task in tasks:
        run = runs.get(task["id"])
        if run is None or run.get("reverted") or not handbacks._mine(run, person):  # noqa: SLF001
            continue
        kind = ATTENTION.get(state_of(task, run, bool(task.get("report")) and task["report"] in waiting))
        if kind:
            out.append({"task": task, "run": run, "kind": kind})
    return out


def task_states(root: str | Path, project_id: str | None, person: str | None,
                shared: dict[str, Any] | None = None) -> dict[str, str]:
    """The state (`state_of`) of every task, by task id -- also of the tasks that belong to no attempt (or to one the
    table no longer has), which `by_attempt` leaves out: 项目首页's 全部任务 says each row's state without opening it
    (21.4). The same reading as the cards', over the same `shared` keep of the run store; {} when the task list cannot be
    read."""
    root = Path(root)
    try:
        tasks, _revision = workspace.read_tasks(root)
    except (workspace.WorkspaceError, OSError):
        return {}
    if not tasks:
        return {}
    waiting, runs, _read = _look(root, project_id, person, tasks, shared)
    return {task["id"]: state_of(task, runs.get(task["id"]), bool(task.get("report")) and task["report"] in waiting) for task in tasks}


def by_attempt(root: str | Path, project_id: str | None, person: str | None,
               attempts: Iterable[str], shared: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """For each of `attempts` (node ids) that has tasks: `tasks` (how many), `unfinished` (not marked done and not
    reading 已完成: a task whose report is in counts as finished, `state_of`), `unopened` (its handbacks `person` has not opened, that can be read and carry the handback's fields:
    `handbacks.unopened`), `latest` (the handback whose report was
    written last, `_handback`; None when no task has one) and `items`, the tasks in `after` order with their `owner`
    and its `owner_label`, `due`, `done`, `after`, `report`, `state` (`state_of`) and `unopened`, and the two times
    21.4 orders 进行中的尝试 by: `ran_at` (its latest run's start or end, `_ran_at`) and `report_at` (when its report
    was written, the file's time; None without one). An attempt with no tasks has no entry, and a task naming an
    attempt that is not in `attempts` is left out. `shared` is `_look`'s."""
    root = Path(root)
    wanted = set(attempts)
    try:
        tasks, _revision = workspace.read_tasks(root)
    except (workspace.WorkspaceError, OSError):
        return {}
    mine = [task for task in tasks if task.get("attempt") in wanted]
    if not mine:
        return {}
    waiting, runs, _read = _look(root, project_id, person, tasks, shared)
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
                          "state": state_of(task, runs.get(task["id"]), unopened), "unopened": unopened,
                          "ran_at": _ran_at(runs.get(task["id"])), "report_at": None})
        newest, newest_at = None, -1.0
        for item in items:
            if not item["report"]:
                continue
            try:
                at = (root / item["report"]).stat().st_mtime
            except OSError:
                continue
            item["report_at"] = datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds")
            if at > newest_at:
                newest, newest_at = item, at
        out[attempt] = {"tasks": len(items), "unfinished": sum(1 for item in items if not item["done"] and item["state"] != "done"),
                        "unopened": sum(1 for item in items if item["unopened"]),
                        "latest": _handback(root, newest["report"], newest["unopened"]) if newest else None,
                        "items": items}
    return out

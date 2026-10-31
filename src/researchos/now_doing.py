"""现在在做什么 (DESIGN.md 21.4): what is going on in the project, for the person asking.

Two parts, worked out together because both read the same things (the attempt table, the task list, the run store and
the shared folder):

- **进行中的尝试** (`in_progress`): the attempts whose research assessment is 现行 or 待定 and that hold at least one
  task, newest activity first, each with the figures of 21.1's card -- taken from `attempt_tasks.by_attempt`, the
  reading the route map's cards use, never counted again here.
- **等你处理** (`needs_you`): ONE list for the person -- the handbacks they have not opened, the runs they started that
  wait for an answer, failed or went out of bounds, and everything 研究路线's 待办 column used to hold (the PI's
  proposals to decide; a member's own proposals, returned ones and a newer official version; 待复核 judgments; lines
  with an end missing). 研究路线's column and 项目首页 both draw this list, and the sidebar counts its rows: there is no
  second copy of what waits.

A third part is the arrangement of what 项目首页 shows below (`home`): the attempts that hold a task or a report in the
same newest-activity order, every task's state and the attempt each report belongs to -- the page groups 报告 and 全部任务
by it and never works any of it out again.

`payload` answers `GET /api/workspace/now`; `desk.payload` carries `needs` for the column. The engine says no sentence
of its own here but the notices `desk` already composed (13.1, 13.6): a row is a code and the data it points at, and the
page words it.

Reads only, except `set_aside`, which keeps 「知道了」 on a failed run in the Research OS home, beside the store of
opened handbacks (`handbacks`): per person on this Mac, never in the project."""

from __future__ import annotations

import itertools
import json
import logging
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from researchos import attempt_tasks, handbacks, workspace
from researchos.agents import runs as run_store
from researchos.records import files as record_files
from researchos.records.identity import IdentityState, read_identity

logger = logging.getLogger(__name__)

ASIDE_SUFFIX = ".aside.json"
#: The statuses 进行中的尝试 lists (`ingest.attempts.verdict_status`'s codes for 现行 and 待定).
IN_PROGRESS = ("current", "pending")
#: The order of the rows, by kind -- what an agent waits on first, what the person decides last; inside a kind newest
#: first (`at`), and rows with no time (a judgment, a line) in the order they were found.
KIND_ORDER = ("answer", "handback", "out_of_bounds", "failed", "proposal", "mine", "official", "review", "line_missing")
TEXT_LINE = 90
#: How many research-context sidecars `placed_reports` reads, and how many entries of the results folder it looks at.
CONTEXT_READS = 500
FOLDER_ENTRIES = 2000
_READS = (OSError, ValueError, workspace.WorkspaceError)


# -- 「知道了」 on a failed run, kept per person on this Mac ---------------------------------------------------


def _aside_file(project_id: str, person: str | None) -> Path:
    """Beside the store of opened handbacks (`handbacks._store_file`), under the same key: `<key>.aside.json`."""
    return handbacks._store_file(project_id, person).with_suffix(ASIDE_SUFFIX)  # noqa: SLF001


def _read_aside(file: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(file.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError):
        return {}
    aside = data.get("aside") if isinstance(data, dict) else None
    if not isinstance(aside, dict):
        return {}
    return {run: entry for run, entry in aside.items() if isinstance(run, str) and isinstance(entry, dict)}


def asides(project_id: str | None, person: str | None) -> set[str]:
    """The runs `person` has set aside with 「知道了」; none when nothing was kept or the store cannot be read."""
    if not project_id:
        return set()
    try:
        return set(_read_aside(_aside_file(project_id, person)))
    except ValueError:
        return set()


def set_aside(project_id: str, person: str | None, run_id: str) -> dict[str, Any]:
    """`person` says 「知道了」 to the run `run_id`: its row leaves 等你处理. Only a run they started can be set aside, and
    only the run is kept -- a task run again has a new run, which asks again. The store keeps the runs the run store
    still has, so it does not grow with the runs it has forgotten. Nothing of the project is written."""
    run = run_store.load_run(project_id, run_id)
    if run is None or not handbacks._mine(run, person):  # noqa: SLF001
        return {"run": run_id, "aside": False}
    file = _aside_file(project_id, person)
    with handbacks._LOCK:  # noqa: SLF001
        alive = set(run_store._run_ids(project_id))  # noqa: SLF001
        kept = {key: entry for key, entry in _read_aside(file).items() if key in alive}
        kept[run_id] = {"at": run_store.now_iso()}
        file.parent.mkdir(parents=True, exist_ok=True)
        record_files.durable_write(file, (json.dumps({"version": 1, "aside": kept}, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {"run": run_id, "aside": True}


# -- the rows ---------------------------------------------------------------------------------------------


def _first_line(text: Any) -> str:
    """A task's words as a row says them: its first line, cut at `TEXT_LINE` characters."""
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= TEXT_LINE else line[:TEXT_LINE] + "…"
    return ""


def _attempt_ref(by_id: dict[str, dict[str, Any]], attempt_id: Any) -> dict[str, Any] | None:
    found = by_id.get(attempt_id) if isinstance(attempt_id, str) else None
    return {"id": found["id"], "number": found["number"], "title": found["title"]} if found else None


def _task_ref(task: dict[str, Any]) -> dict[str, Any]:
    return {"id": task["id"], "text": _first_line(task.get("text"))}


def _row(kind: str, key: str, at: Any = None, **fields: Any) -> dict[str, Any]:
    """One row: its `kind`, a `key` that names it for as long as it waits (the page marks and finds it by it), `at`
    (when it began to wait, an ISO time, or None), `attempt` (the one attempt it names, `{id, number, title}`, or
    None) with `attempts`, the ids it touches, and what the kind points at."""
    fields.setdefault("attempt", None)
    fields.setdefault("attempts", [fields["attempt"]["id"]] if fields["attempt"] else [])
    return {"kind": kind, "key": key, "at": at if attempt_tasks.parse_time(at) else None, **fields}


def _agent_rows(root: Path, project_id: str | None, person: str | None, by_id: dict[str, dict[str, Any]],
                shared: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Unopened handbacks and the runs `person` started that wait for an answer, failed or went out of bounds. A failed
    or out-of-bounds run they said 「知道了」 to is left out; a run that is not its task's latest, and a task the list no
    longer has, never get here (`attempt_tasks.attention`)."""
    rows: list[dict[str, Any]] = []
    tasks: dict[str, dict[str, Any]] = {}
    try:
        listed = workspace.read_tasks(root)[0]
        tasks = {task["id"]: task for task in listed}
        # the 交回 rows come from the reading the cards and the task rows say 等你看 from (one pass over the run store)
        waiting, runs = attempt_tasks.reading(root, project_id, person, listed, shared)
        for item in handbacks.list_unopened(root, project_id, person, waiting, runs):
            task = tasks.get(item["task"])
            if task is None:
                continue
            rows.append(_row("handback", "h:" + item["report"], item.get("ended"), report=item["report"], title=item["title"],
                             headline=item["headline"], run=item["run"], task=_task_ref(task),
                             attempt=_attempt_ref(by_id, item.get("attempt"))))
    except _READS:
        logger.info("the handbacks 等你处理 lists could not be read", exc_info=True)
    set_aside_runs = asides(project_id, person)
    try:
        for entry in attempt_tasks.attention(root, project_id, person, shared):
            run, task, kind = entry["run"], entry["task"], entry["kind"]
            if not isinstance(run.get("id"), str) or (kind != "answer" and run["id"] in set_aside_runs):
                continue
            at = run.get("ended") or run.get("started") or run.get("created")
            question = str(run.get("question") or "").strip() if kind == "answer" else ""
            rows.append(_row(kind, "run:" + run["id"], at, run=run["id"], question=question[:200], task=_task_ref(task),
                             attempt=_attempt_ref(by_id, task.get("attempt"))))
    except _READS:
        logger.info("the runs 等你处理 lists could not be read", exc_info=True)
    return rows


def _desk_rows(desk_out: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """What 研究路线's 待办 column held (14.4, 18.3), as rows: the PI's proposals to decide, a member's own proposals
    that wait (提议中, returned until they say 「知道了」 -- which the page keeps, in the browser --, approved and not yet
    synced), a newer official version, the judgments to review one row per attempt (and one for those that belong to
    none), the lines with a missing end. A role that cannot decide a proposal has none."""
    column = desk_out.get("column") or {}
    role = column.get("role")
    rows: list[dict[str, Any]] = []
    own = [p for p in column.get("mine") or [] if p.get("state") in ("pending", "conflict", "returned") or p.get("awaiting_sync")]
    for kind, proposals in (("proposal", column.get("waiting") or [] if role == "pi" else []),
                            ("mine", own if role == "member" else [])):
        for p in proposals:
            touched = [a for a in p.get("touches") or [] if isinstance(a, str)]
            rows.append(_row(kind, "p:" + str(p["id"]), p.get("at"), proposal=p, attempts=touched,
                             attempt=_attempt_ref(by_id, touched[0]) if touched else None))
    sync = column.get("sync") or {}
    if role == "member" and sync.get("shared") and sync.get("reachable") and sync.get("behind") and sync.get("last_snapshot"):
        rows.append(_row("official", "official", (sync["last_snapshot"] or {}).get("at"), snapshot=sync["last_snapshot"]))
    review = column.get("review") or {}
    for group in review.get("groups") or []:
        rows.append(_row("review", "r:" + group["id"], attempt=_attempt_ref(by_id, group["id"]), items=group["items"],
                         cards=[], n=len(group["items"]), officialness=group.get("officialness")))
    none = review.get("none") or {}
    left = none.get("items") or []
    cards = none.get("cards") or []
    n = len(left) + sum(1 for card in cards if card.get("counted"))
    if n:
        rows.append(_row("review", "r:none", items=left, cards=cards, n=n))
    for line in desk_out.get("lines_missing") or []:
        ends = [line[end] for end in ("child", "parent") if end not in (line.get("missing") or []) and isinstance(line.get(end), str)]
        rows.append(_row("line_missing", f"l:{line.get('child')}|{line.get('parent')}", line=line, attempts=ends,
                         attempt=_attempt_ref(by_id, ends[0]) if ends else None))
    return rows


def _newest_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rank = {kind: i for i, kind in enumerate(KIND_ORDER)}

    def key(row: dict[str, Any]) -> tuple[int, int, float]:
        when = attempt_tasks.parse_time(row["at"])
        return rank[row["kind"]], 0 if when else 1, -when.timestamp() if when else 0.0

    return sorted(rows, key=key)  # stable: rows with no time keep the order they were found in


def assemble(desk_out: dict[str, Any], agent_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """`{rows, notices, role}`: the rows `desk_out` (a desk payload, annotated) holds -- `_desk_rows` -- and `agent_rows`,
    in one order (`KIND_ORDER`), and the notices the desk composed in its language for a role that cannot act (not in the
    team yet, a team file that cannot be read) and a shared folder that cannot be read; the column says them above the
    rows. The list of a desk payload alone, whoever's runs there are."""
    column = desk_out.get("column") or {}
    by_id = {a["id"]: a for a in desk_out.get("attempts") or []}
    notices = [n for n in (column.get("notice"), (column.get("shared") or {}).get("message")) if n]
    return {"rows": _newest_first(list(agent_rows or []) + _desk_rows(desk_out, by_id)), "notices": notices, "role": column.get("role")}


def needs_you(project_root: str | Path, desk_out: dict[str, Any], shared: dict[str, Any] | None = None) -> dict[str, Any]:
    """等你处理 (21.4): `{rows, notices, role}`. `desk_out` is `desk.payload`'s answer (annotated, before this is put
    in it): the PI's and a member's proposals, the review groups, the lines with a missing end and the sync state are
    read from it (`assemble`), so the column and the home draw one reading of the shared folder. The rest -- unopened
    handbacks and runs -- are this person's (`column.me`): what they started, what they have not opened, what they set
    aside. `shared` is `attempt_tasks`' keep of one reading of the run store."""
    root = Path(project_root)
    got = read_identity(root)
    project_id = got.identity.id if got.state is IdentityState.PRESENT and got.identity else None
    by_id = {a["id"]: a for a in desk_out.get("attempts") or []}
    return assemble(desk_out, _agent_rows(root, project_id, (desk_out.get("column") or {}).get("me"), by_id, shared))


# -- 进行中的尝试 ------------------------------------------------------------------------------------------


def _activity(attempt: dict[str, Any], work: dict[str, Any]) -> str | None:
    """When an attempt last moved, the latest of: its tasks' latest runs' start or end (`ran_at`), the times its
    handbacks' reports were written (`report_at`) and the date of its row (the table's day, where `date_year` makes it
    readable: `date_key`). None when it has none of them."""
    found = [when for item in work.get("items") or [] for text in (item.get("ran_at"), item.get("report_at"))
             if (when := attempt_tasks.parse_time(text))]
    if (day := attempt_tasks.parse_time(attempt.get("date_key"))):
        found.append(day)
    return max(found).isoformat(timespec="seconds") if found else None


def _newest_activity_first(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`items` (each with `activity`, an ISO time or None, and the table's `order`) newest activity first; one with no
    time after the others, in the table's order. The one order 进行中的尝试, 报告 and 全部任务 stand in (21.4)."""
    def key(item: dict[str, Any]) -> tuple[int, float, int]:
        when = attempt_tasks.parse_time(item["activity"])
        return (0, -when.timestamp(), item["order"]) if when else (1, 0.0, item["order"])

    return sorted(items, key=key)


def in_progress(root: str | Path, project_id: str | None, person: str | None,
                attempts: list[dict[str, Any]], shared: dict[str, Any] | None = None,
                work: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """进行中的尝试: `{attempts, holding}`. `attempts` are those of `attempts` (the desk's cards) whose assessment is
    现行 or 待定 (`IN_PROGRESS`) and that hold at least one task -- an unopened handback implies a task --, newest
    `activity` first (`_activity`; an attempt with no time at all after the others, in the table's order), each with its
    `work`: `attempt_tasks.by_attempt`'s figures for the card (the progress line is `tasks` and `unfinished`, 等你看 is
    `unopened`, the first line of the latest handback is `latest.line`). An attempt assessed 不利证据, 被否, 已放弃 or
    归档, one the person's own words mark with a status of their own, and one with no task are not listed -- a 待定
    attempt whose tasks are all finished is, because it waits for the person's assessment. `holding` counts the
    attempts of any status that hold a task: 0 means the project has none, which the page says with the way in. `work`
    is `attempt_tasks.by_attempt`'s reading of all `attempts` when the caller has it already (`payload` reads it once
    for this and for `home`)."""
    if work is None:
        work = attempt_tasks.by_attempt(root, project_id, person, [a["id"] for a in attempts], shared)
    listed = []
    for attempt in attempts:
        figures = work.get(attempt["id"])
        if figures and (attempt.get("route_status") or {}).get("status") in IN_PROGRESS:
            listed.append({"id": attempt["id"], "number": attempt["number"], "title": attempt["title"], "date": attempt["date"],
                           "route_status": attempt["route_status"], "tone": attempt.get("tone"), "work": figures,
                           "activity": _activity(attempt, figures), "order": attempt.get("order", 0)})
    return {"attempts": _newest_activity_first(listed), "holding": len(work)}


# -- below it: 报告 and 全部任务 by attempt ---------------------------------------------------------------------------


def placed_reports(root: Path, attempt_ids: set[str], tasks: list[dict[str, Any]]) -> dict[str, str]:
    """The attempt each report belongs to (21.4), `{report path: attempt id}`: the attempt of the task whose `report` it
    is (the first such task in the list, when the attempt is in `attempt_ids`), else the first attempt its research
    context links (20: the sidecar's `attempts`) that the table has. A report that neither a task nor a link places is
    not in the answer. The sidecars are read as the JSON they are, as a handback's headline is, so one with a field this
    version does not know still places its report; at most `CONTEXT_READS` of them, and none for a report a task
    placed already."""
    placed: dict[str, str] = {}
    for task in tasks:
        report, attempt = task.get("report"), task.get("attempt")
        if isinstance(report, str) and report and attempt in attempt_ids and report not in placed:
            placed[report] = attempt
    try:
        base = workspace._confined(root, "research/results")  # noqa: SLF001
        names = sorted(entry.name for entry in itertools.islice(base.iterdir(), FOLDER_ENTRIES)
                       if entry.name.endswith(".context.json") and not entry.name.startswith("."))
    except (OSError, workspace.WorkspaceError):
        return placed
    reads = 0
    for name in names:
        report = "research/results/" + name.removesuffix(".context.json") + ".md"
        if report in placed:
            continue
        if reads >= CONTEXT_READS:
            break
        reads += 1
        try:
            raw = workspace._read(root, "research/results/" + name)  # noqa: SLF001
            linked = json.loads(raw)["context"]["attempts"] if raw else []
        except (*_READS, KeyError, TypeError):
            continue
        found = next((ref for ref in linked if isinstance(ref, str) and ref in attempt_ids), None) if isinstance(linked, list) else None
        if found:
            placed[report] = found
    return placed


def home(root: str | Path, project_id: str | None, person: str | None, attempts: list[dict[str, Any]],
         work: dict[str, dict[str, Any]], shared: dict[str, Any] | None = None) -> dict[str, Any]:
    """What 项目首页 groups below 现在在做什么 by (21.4): `{attempts, tasks, reports}`.

    - `attempts`: the ids of the attempts (any status) that hold at least one task or one report, newest activity first
      -- `_activity` and the order of 进行中的尝试, so the three lists never disagree. An attempt with neither is left
      out; the page puts what belongs to none under 「未归入尝试」, last.
    - `tasks`: every task's state code (`attempt_tasks.task_states`, the cards' reading), by task id.
    - `reports`: `placed_reports`.

    `work` is `attempt_tasks.by_attempt`'s reading of all `attempts` (the same `shared` keep of the run store)."""
    root = Path(root)
    ids = {a["id"] for a in attempts}
    try:
        tasks = workspace.read_tasks(root)[0]
    except _READS:
        tasks = []
    reports = placed_reports(root, ids, tasks)
    holding = set(work) | set(reports.values())
    listed = [{"id": a["id"], "activity": _activity(a, work.get(a["id"]) or {}), "order": a.get("order", 0)}
              for a in attempts if a["id"] in holding]
    return {"attempts": [item["id"] for item in _newest_activity_first(listed)],
            "tasks": attempt_tasks.task_states(root, project_id, person, shared), "reports": reports}


# -- the endpoint -----------------------------------------------------------------------------------------


def payload(conn: Connection, project_root: str | Path, *, lang: str = "zh", timeout: float | None = None,
            only: str | None = None) -> dict[str, Any]:
    """`GET /api/workspace/now` (DESIGN.md 21.4): `{role, needs, shared, progress, home}` for the open project and the person this
    Mac belongs to -- `needs` is `needs_you`'s answer, `progress` is `in_progress`'s, `home` is `home`'s, and `shared` is
    `{state}` of the shared folder as the desk gives the column: a notice alone does not tell the page whether a later read
    would help (`slow` and `stale` do, `unreadable` does not), so the home asks again after a slow first read. `only=needs`
    leaves `progress` and `home` out (the sidebar's count reads the list alone). The shared folder is read for at most
    `desk.SHARED_TIMEOUT` seconds, as the desk's is (the same job, so the two never read it twice at once); the map's
    cards are not worked out (`light`)."""
    from researchos import desk  # noqa: PLC0415

    root = Path(project_root)
    shared: dict[str, Any] = {}
    out = desk.payload(conn, root, lang=lang, timeout=timeout, light=True, shared=shared)
    column = out.get("column") or {}
    result: dict[str, Any] = {"role": column.get("role"), "needs": out["needs"],
                             "shared": {"state": (column.get("shared") or {}).get("state")}}
    if only != "needs":
        got = read_identity(root)
        project_id = got.identity.id if got.state is IdentityState.PRESENT and got.identity else None
        cards = out.get("attempts") or []
        work = attempt_tasks.by_attempt(root, project_id, column.get("me"), [a["id"] for a in cards], shared)
        result["progress"] = in_progress(root, project_id, column.get("me"), cards, shared, work)
        result["home"] = home(root, project_id, column.get("me"), cards, work, shared)
    return result

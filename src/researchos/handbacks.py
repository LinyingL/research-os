"""Handbacks (交回, DESIGN.md 21.2): what an agent hands back, whether this person has opened it, and what confirming
its suggested assessment writes.

A handback is a run's report, the report's context and its registered outputs, from the moment the run ends with
`researchos_complete_task`. It is listed in 等你处理 and on its attempt's card until the person who started the run
opens it. That an agent run handed something back is in the project (the task's `report`); that *this person* has
looked at it is not: "opened" is kept per person on this Mac under the Research OS home, never in the project, in
`<home>/handbacks/<project id>/<one file per person>.json`, by report path. A report is opened for the run that
handed it back: a task sent back and handed in again is unopened again.

- **Reading.** `unopened` (the interface the route map's cards and 项目首页's 等你处理 use), `mark_opened`, `view`
  (everything the handback page shows, in this order: headline, key numbers with the number check of 16.1 against
  their files, what is undone, what the researcher must decide, the suggestion, the outputs by kind, the report, the
  run) -- reads, except for `mark_opened`, which writes the home's store and nothing of the project. Given the
  project, `unopened` leaves out a report that is gone and one whose context holds none of the handback's fields: a
  report written before they existed is no handback of this version, however many a project has, and a person's
  confirming it (`assessment_confirmed`, theirs and no field an agent hands back) does not make it one.
- **Confirming.** `confirm` writes the attempt's research assessment through the write the panel uses
  (`proposals.write_directly`: only the changed cell, backup, history, conflict and abandonment rules -- 14.3 and
  18.3), puts 「由 张三 确认（采纳 Claude Code · sonnet 的建议）」 on that version in the history (a plain 「由 张三 确认」
  when the person changed the suggestion), and records `assessment_confirmed` in the report's context through the
  human context write (`research_records.confirm_assessment`) -- in that order, and only after the record could be
  written has been checked, so the table is never left saying something the record does not. The page sends the
  cell it showed (`was`), and a row that has moved since is refused, as the panel's own save refuses it (18.3). A
  member's write lands in their working copy and the page proposes it (12.4). Nothing of this is an agent's: the
  guard that keeps an agent from writing the attempt table is unchanged.
- **Taking a confirmation back.** `unconfirm`: 撤销 on the written line puts the cell back through the panel's write;
  this then removes `assessment_confirmed` from the report's context and puts 「撤销了确认」 on the version 撤销 wrote,
  so that the handback offers its three buttons again and says the assessment is back as it was.

The engine says no sentence of its own here but its refusals (13.1)."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from researchos import db, desk, history, officialness, paths, people, proposals, research_records, workspace
from researchos.agents import labels as agent_labels
from researchos.agents import runs as run_store
from researchos.ingest import attempts as attempts_ingest
from researchos.records import files as record_files
from researchos.records.identity import read_identity
from researchos.semantic import draft
from researchos.webapp import mapedit

OPENED_DIRNAME = "handbacks"
#: The outputs the page lists, by kind, in this order (21.2: figures, tables, data, code, then the rest).
OUTPUT_KINDS = ("figure", "table", "data", "code", "document", "literature", "variable")
OUTPUTS_SHOWN = 200
#: The context fields an agent hands back (21.2). `assessment_confirmed` is the person's own record, written when they
#: confirm: that they confirmed an older report does not make it a handback of this version.
AGENT_FIELDS = tuple(key for key in research_records.HANDBACK_FIELDS if key != "assessment_confirmed")
_LOCK = threading.Lock()


# -- which handbacks a person has opened ---------------------------------------------------------------------------


def _store_file(project_id: str, person: str | None) -> Path:
    """The file one person's opened handbacks of one project are kept in: named by a hash of the member id, which can
    hold any character of an email address."""
    key = hashlib.sha256(person.encode("utf-8")).hexdigest()[:32] if person else "nobody"
    return paths.researchos_home() / OPENED_DIRNAME / run_store._check_project_id(project_id) / f"{key}.json"  # noqa: SLF001


def _read_opened(file: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(file.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError):
        return {}
    opened = data.get("opened") if isinstance(data, dict) else None
    if not isinstance(opened, dict):
        return {}
    return {report: entry for report, entry in opened.items() if isinstance(report, str) and isinstance(entry, dict)}


def _handed_back(project_id: str) -> dict[str, dict[str, Any]]:
    """Each report an agent run handed back that is still handed back -- the run was not undone -- with the newest run
    that did, by report path. At most the runs the run store keeps (17.6)."""
    found: dict[str, dict[str, Any]] = {}
    for run in run_store.iter_runs(project_id):  # newest first
        report = run.get("report")
        if isinstance(report, str) and report and not run.get("reverted") and report not in found:
            found[report] = run
    return found


def person_of() -> str | None:
    """The person this Mac belongs to (`me.toml`'s id); None when none is set or it cannot be read."""
    try:
        return people.current_member_id()
    except people.PeopleError:
        return None


def _mine(run: dict[str, Any], person: str | None) -> bool:
    """Whether `person` is the one who started `run`; a run that names nobody (no `me.toml` then) is whoever's Mac it
    is."""
    return run.get("by") is None or run.get("by") == person


def readable(root: Path, report: str) -> bool:
    """Whether the report file is there to be read (a plain file inside the project)."""
    try:
        return workspace._read(root, report) is not None  # noqa: SLF001
    except (workspace.WorkspaceError, OSError):
        return False


def _predates_fields(root: Path, report: str) -> bool:
    """Whether the report's context holds none of the fields an agent hands back (21.2) -- a report from before they
    existed, with a context from before them or none, confirmed by the person or not. A context that cannot be read is
    not that: the page says so itself, and the handback waits."""
    try:
        raw = workspace._read(root, research_records.path_for(report))  # noqa: SLF001
        return raw is None or not any(key in research_records.parse(raw) for key in AGENT_FIELDS)
    except workspace.WorkspaceError:
        return False


def unopened(project_id: str | None, person: str | None, root: str | Path | None = None) -> list[str]:
    """The handbacks `person` has not opened in the project `project_id`: the project paths of their result records
    (`research/results/<name>.md`), oldest first. `person` is the person this Mac belongs to (`proposals.role_of`'s
    `me`; None when none is set), `project_id` the project's identity (None when it has none): neither is a path
    or a name typed on a page. A handback counts for the person who started the run that handed it back, and until
    they open it; one opened before a later run handed the report back again is unopened again. Given the project's
    `root`, a report that is gone and one written before the handback's fields existed (`_predates_fields`: its
    context holds none of the fields an agent hands back, a confirmation by the person aside) are left out, so that
    a project's older reports do not all wait on the first day and the card and the list agree. Reads only."""
    if not project_id:
        return []
    try:
        opened = _read_opened(_store_file(project_id, person))
        found = _handed_back(project_id)
    except (OSError, ValueError):
        return []
    waiting = [(run["id"], report) for report, run in found.items()
               if _mine(run, person) and str((opened.get(report) or {}).get("run") or "") < run["id"]]
    if root is not None:
        where = Path(root)
        waiting = [(run, report) for run, report in waiting if readable(where, report) and not _predates_fields(where, report)]
    return [report for _run, report in sorted(waiting)]


def mark_opened(project_id: str, person: str | None, report: str) -> dict[str, Any]:
    """`person` has opened the handback `report`: kept in the home's store for the run that handed it back. A report no
    run handed back (one written by hand) has nothing to open and is not recorded. The store keeps only reports
    that are still handed back, so it does not grow with the runs it has forgotten."""
    file = _store_file(project_id, person)
    found = _handed_back(project_id)
    run = found.get(report)
    if run is None:
        return {"report": report, "opened": False}
    with _LOCK:
        opened = {key: entry for key, entry in _read_opened(file).items() if key in found}
        opened[report] = {"run": run["id"], "at": run_store.now_iso()}
        file.parent.mkdir(parents=True, exist_ok=True)
        record_files.durable_write(file, (json.dumps({"version": 1, "opened": opened}, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {"report": report, "opened": True, "run": run["id"]}


def list_unopened(root: str | Path, project_id: str | None, person: str | None) -> list[dict[str, Any]]:
    """`unopened`, each with what a list of them says: the report, its title (the first heading, else the file name),
    the context's headline, the task it belongs to and that task's attempt, and the run that handed it back (`run`,
    `ended`). A report that is gone, or whose task has been removed, is left out."""
    root = workspace._root(root)  # noqa: SLF001
    waiting = unopened(project_id, person, root)
    if not waiting or not project_id:
        return []
    try:
        tasks, _revision = workspace.read_tasks(root)
    except (workspace.WorkspaceError, OSError):
        return []
    by_report = {task["report"]: task for task in tasks if task.get("report")}
    found = _handed_back(project_id)
    out = []
    for report in waiting:
        task = by_report.get(report)
        if task is None:
            continue
        try:
            raw = workspace._read(root, report)  # noqa: SLF001
            if raw is None:
                continue
            title = workspace._heading(workspace._decode(raw)[:4096]) or Path(report).stem  # noqa: SLF001
        except workspace.WorkspaceError:
            continue
        out.append({"report": report, "title": title, "headline": _context(root, report).get("headline") or "",
                    "task": task["id"], "attempt": task.get("attempt"),
                    "run": found[report]["id"], "ended": found[report].get("ended")})
    return out


def _context(root: Path, report: str) -> dict[str, Any]:
    """The report's context as stored; empty when there is none or it cannot be read (the page says so itself)."""
    try:
        return research_records.parse(workspace._read(root, research_records.path_for(report)))  # noqa: SLF001
    except workspace.WorkspaceError:
        return {}


# -- the number check ----------------------------------------------------------------------------------------------


def _squash(text: str) -> str:
    return " ".join(text.split()).casefold()


def check_numbers(root: str | Path, numbers: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Each key number with `check`: `found` when its value occurs in the text of the file it cites, by the number
    check of 16.1 (`semantic.draft._unsupported_numbers`: every number in the value occurs in the text, as written or
    as a value -- 0.10 is 0.1 -- a comma read both as a thousands and as a field separator); a value that holds no
    number must occur as written, case and spacing aside. `missing` when it does not, `unreadable` when the file
    cannot be read as text here (it is not there, only in the cloud, over 200 KB, not UTF-8). The check says what the
    text holds; it does not say the number is right."""
    root = workspace._root(root)  # noqa: SLF001
    texts: dict[str, str | None] = {}
    out = []
    for item in numbers:
        if item["file"] not in texts:
            try:
                raw = workspace._read(root, item["file"])  # noqa: SLF001
                texts[item["file"]] = workspace._decode(raw) if raw is not None else None  # noqa: SLF001
            except (workspace.WorkspaceError, OSError):
                texts[item["file"]] = None
        text = texts[item["file"]]
        if text is None:
            state = "unreadable"
        elif draft._numbers(item["value"]):  # noqa: SLF001
            state = "missing" if draft._unsupported_numbers(item["value"], text) else "found"  # noqa: SLF001
        else:
            state = "found" if _squash(item["value"]) in _squash(text) else "missing"
        out.append({**item, "check": state})
    return out


# -- the page -----------------------------------------------------------------------------------------------------


def _task_of(root: Path, report: str) -> dict[str, Any] | None:
    try:
        tasks, _revision = workspace.read_tasks(root)
    except (workspace.WorkspaceError, OSError):
        return None
    return next((task for task in tasks if task.get("report") == report), None)


def _own_row(root: Path, node: str, number: str, lang: str) -> dict[str, Any] | None:
    """A member's view of the attempt row (the officialness the panel uses, 12.12): `new_row` when the official version
    they synced to does not have it -- changing it is proposing it whole, as a new attempt -- and `state` and the
    pending `proposal` they sent for it. The shared folder is read only for such a row, and for no longer than the desk
    waits (14.4). None when it cannot be told."""
    try:
        seen = officialness.view(root, pending=False)
        item: dict[str, Any] = {"id": node, "number": number}
        officialness.annotate_desk(seen, {"attempts": [item]}, lang)
        if (item.get(officialness.FIELD) or {}).get("new_row"):
            state, got = desk.read_within(f"handback|{root}", lambda: officialness.pending_of(seen))
            officialness.with_pending(seen, got if state == "ok" else None)
            item = {"id": node, "number": number}
            officialness.annotate_desk(seen, {"attempts": [item]}, lang)
        mark = item.get(officialness.FIELD) or {}
    except Exception:  # noqa: BLE001 -- a mark is a nicety; the handback is still read
        return None
    return {"state": mark.get("state"), "proposal": mark.get("proposal"), "new_row": bool(mark.get("new_row"))}


def _attempt_view(root: Path, attempt_id: str | None, mode: str = "single", lang: str = "zh") -> dict[str, Any] | None:
    """The attempt a handback belongs to: `id`, `number`, `label`, and its research assessment now (`verdict`, the cell as
    the table's reader sees it, `status`: `{status, label, words}`, and `result`, the result cell); for a member also
    `officialness` (`_own_row`). None when the task has none or the table no longer has it."""
    if not attempt_id:
        return None
    row = next((item for item in workspace._attempts(root, None) if item["id"] == attempt_id), None)  # noqa: SLF001
    if row is None:
        return None
    number = attempt_id.rpartition("#")[2]
    out: dict[str, Any] = {"id": attempt_id, "number": number, "label": row["label"], "verdict": None, "status": None, "result": None,
                           "officialness": _own_row(root, attempt_id, number, lang) if mode == "member" else None}
    try:
        cells = mapedit.row_cells(root, number)
    except (mapedit.MapEditError, attempts_ingest.AttemptsConfigError):
        cells = None
    if cells is not None:
        verdict = attempts_ingest._clean_cell(cells["verdict"])  # noqa: SLF001
        out.update(verdict=verdict, status=attempts_ingest.verdict_status(verdict, attempts_ingest.verdict_labels(root)),
                   result=attempts_ingest._clean_cell(cells["result"]))  # noqa: SLF001
    return out


def _project_id(root: Path) -> str | None:
    got = read_identity(root)
    return got.identity.id if got.identity is not None else None


#: The states of a run that will still write its own report (`manager`: preparing and running; a question waits on the person).
_GOING = ("preparing", "running", "needs_input")


def _run_going(project_id: str | None, task_id: str) -> bool:
    """Whether the newest run of the task is still going, or waits for an answer: it will hand its own report back, and
    what is confirmed from the earlier handback in the meantime would make that run's completion fail for good
    (it checks the context against the revision it was started with, 20)."""
    if not project_id:
        return False
    try:
        latest = next((run for run in run_store.iter_runs(project_id) if run.get("task_id") == task_id), None)
    except OSError:
        return False
    return latest is not None and run_store.outcome_of(latest) in _GOING


def view(root: str | Path, project_id: str | None, person: str | None, report: str, lang: str = "zh") -> dict[str, Any]:
    """`GET /api/workspace/handback?report=…`: the handback of the result record `report`, in the order the page shows
    it. `report` and `title`, `text` (the report in full); `task` (`id`, `text`, `attempt`, `after`, `owner`, `done`;
    None when no task points to it); `attempt` (`_attempt_view`); `run` (the newest run that handed it back: `id`,
    `label`, `runner`, `model`, `by`, `by_name`, `ended`, and how it ended -- `state`, `outcome`, `violations`, the
    count of out-of-bounds changes, which the page warns of; None for a report written by hand); `run_active` (a
    later run of the task is still going, or waits for an answer: confirming waits for it); the context's
    `headline`, `key_numbers` (with `check`, `check_numbers`), `undone`, `decide`, and Section 20's `finding`,
    `limits` and `next_step`, which a report written without the 21.2 fields still has and the page shows in the
    headline's place (it says the report has no key points only when it holds none of the three); `suggested` (`status`, `reason`,
    `known`, `marker`, `abandons`) and `confirmed` (`assessment_confirmed` with `by_label`); `statuses`
    (`research_records.assessment_statuses`); `outputs` by kind order; `attempt_draft`; `context_error` when the
    context cannot be read; `unopened` for `person`; `mode` (who writes: single, pi, member, none, unknown). Reads
    only: opening is `mark_opened`, which the page asks for."""
    root = workspace._root(root)  # noqa: SLF001
    research_records.path_for(report)  # a result record, or the refusal
    raw = workspace._read(root, report)  # noqa: SLF001
    if raw is None:
        raise workspace.WorkspaceError("not_found", "This report does not exist in the project", 404,
                                       "这份报告已经不在项目里了。")
    text = workspace._decode(raw)  # noqa: SLF001
    task = _task_of(root, report)
    run = (_handed_back(project_id).get(report) if project_id else None)
    mode, _me, team = proposals.role_of(root)
    out: dict[str, Any] = {
        "report": report, "title": workspace._heading(text[:4096]) or Path(report).stem, "text": text,  # noqa: SLF001
        "revision": workspace._revision(raw),  # noqa: SLF001
        "task": ({key: task.get(key) for key in ("id", "text", "attempt", "after", "owner", "done")} if task else None),
        "attempt": _attempt_view(root, task.get("attempt") if task else None, mode, lang),
        "run": ({"id": run["id"], "label": run.get("label"), "runner": run.get("runner"), "model": run.get("model"),
                 "by": run.get("by"), "by_name": run.get("by_name"), "ended": run.get("ended"),
                 "state": run.get("state"), "outcome": run_store.outcome_of(run),
                 "violations": len(run.get("violations") or ())} if run else None),
        "run_active": bool(task) and _run_going(project_id, task["id"]),
        "mode": mode, "statuses": research_records.assessment_statuses(root),
        "unopened": report in unopened(project_id, person, root), "context_error": None,
        "headline": "", "key_numbers": [], "undone": "", "decide": [], "suggested": None, "confirmed": None,
        "finding": "", "limits": "", "next_step": "", "attempt_draft": None, "outputs": [],
    }
    try:
        context = research_records.parse(workspace._read(root, research_records.path_for(report)))  # noqa: SLF001
    except workspace.WorkspaceError as exc:
        out["context_error"] = str(exc)
        context = research_records.normalize({})
    out.update(headline=context.get("headline", ""), undone=context.get("undone", ""), decide=list(context.get("decide", [])),
               finding=context.get("finding", ""), limits=context.get("limits", ""), next_step=context.get("next_step", ""),
               attempt_draft=context.get("attempt_draft"), key_numbers=check_numbers(root, context.get("key_numbers", [])))
    suggestion = context.get("suggested_assessment")
    if suggestion is not None:
        known = next((item for item in out["statuses"] if item["status"] == suggestion["status"]), None)
        out["suggested"] = {**suggestion, "known": known is not None, "marker": known["marker"] if known else "",
                            "abandons": bool(known and known["abandons"])}
    confirmed = context.get("assessment_confirmed")
    if confirmed is not None:
        out["confirmed"] = {**confirmed, "by_label": people.attribution({"by": confirmed.get("by")}, team if team.state == "present" else None, lang)["label"]}
    try:
        entries = research_records._entries(root, report)  # noqa: SLF001
    except (workspace.WorkspaceError, OSError):
        entries = []
    rank = {kind: index for index, kind in enumerate(OUTPUT_KINDS)}
    out["outputs"] = [{key: item.get(key) for key in ("kind", "path", "title", "state")}
                      for item in sorted((e for e in entries if e.get("kind") in rank), key=lambda e: rank[e["kind"]])][:OUTPUTS_SHOWN]
    return out


# -- confirming ----------------------------------------------------------------------------------------------------


def _refused(code: str, message: str, message_zh: str, status: int = 409):
    return workspace.WorkspaceError(code, message, status, message_zh)


def _table_refusal(exc: Exception, root: Path):
    """The attempt table's refusal as the page's: the cell was not written."""
    if isinstance(exc, proposals.ProposalRefused):
        return workspace.WorkspaceError(exc.code, str(exc), 409, exc.message_zh)
    code = mapedit.error_code(exc, root) or "refused"
    return workspace.WorkspaceError("handback_" + code, f"The attempt table refused the assessment, so nothing was changed: {exc}", 409,
                                    "尝试表没有接受这条研究判断，什么都没有改。")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _record_history(root: Path, node: str, before: int, who: str | None, note: str) -> str | None:
    """The version of the attempt the write made carries `note` and `who`: the re-ingest that follows a write saw the
    row first, as a scan on this Mac; this names the person on that same version (as an approval does) instead of
    adding a second. None, or why the history could not be updated (the assessment is written all the same)."""
    try:
        identity = read_identity(root).identity
        if identity is None:
            return "the project has no readable identity"
        conn = db.connect(paths.graph_db_path(root))
        try:
            attempt = db.get_node(conn, node)
        finally:
            conn.close()
        if attempt is None:
            return f"{node} is not in the index"
        human = attempt.get("human_fields") or {}
        fields = {"verdict": str(human.get("verdict") or ""), "result": str(human.get("result") or "")}
        digest = history._attempt_digest(fields)  # noqa: SLF001
        with history.Store.open(identity.id) as store:
            latest = store.latest(node)
            if latest is not None and latest.seq > before and latest.sha256 == digest and latest.source == "scan":
                store.attribute(latest.seq, source="scan", who=who, note=note, approved_by=None)
            else:
                store.add(node, sha256=digest, size=None, kind="attempt", source="scan", who=who, note=note, summary=fields)
    except Exception as exc:  # noqa: BLE001 -- the history is the record of the write, never a reason to undo it
        return str(exc)
    return None


def _max_seq(root: Path) -> int:

    try:
        identity = read_identity(root).identity
        if identity is None:
            return 0
        with history.Store.open(identity.id) as store:
            row = store.conn.execute("SELECT MAX(seq) FROM versions").fetchone()
            return int(row[0] or 0)
    except Exception:  # noqa: BLE001 -- see `_record_history`
        return 0


def _squashed(text: str) -> str:
    return "".join(str(text).split())


def confirm(root: str | Path, body: Any, *, ingest_lock: Any = None, lang: str = "zh") -> dict[str, Any]:
    """`POST /api/workspace/handback/confirm` {report, status, reason?, abandon_reason?, was?, result?}: write the research
    assessment of the attempt the report's task belongs to, as the person chose it -- the suggestion as it stands, or
    another status or reason (`adopted` is whether it is the suggestion unchanged: the same status and reason). The cell
    is the status's marker, a space and the reason (21.3); 其它 writes the reason alone; when the status is the one the
    cell already has, the cell's own marker is written again, not the status's first (and a cell that already has that
    status and reason is not written at all, whatever its marker or line breaks). `abandon_reason` is what 14.3 asks when
    the status turns the route abandoned (the PI and a single researcher; a member's proposal needs it as well as the
    attempt's `result`, which is asked here when the row has none, since the proposal is refused without it). `was` is
    the cell the page showed: a row that no longer reads that way is refused (`handback_moved`), as the panel's own save
    refuses it (18.3). A task whose latest run is still going is refused (`handback_run_active`). Everything is checked
    before anything is written: who may write, the task and its attempt, the status, the row, the context the record will
    go into. Then the cell (a cell the table already holds that way is not written again), the history note, and
    `assessment_confirmed` in the context; the last two cannot undo the first, so they are answered as `history_error`
    and `context_error`. Answers `written` (whether the table changed), `adopted`, `note` (the history note) and
    `sentence` (the same, in both languages, for a member's proposal), `confirmed`, `number`, `node`, `cell`, `before`
    (the cell as the table held it: what 撤销 writes back), `file`, `backup`, `ingest_error`, `decision`, `mode`,
    `context_path` (the record, which a member's proposal carries) and `by_label` (who confirmed, as people are named in
    `lang`)."""
    if not isinstance(body, dict):
        raise _refused("invalid", "A confirmation must be an object", "内容格式不对，请检查后重试", 400)
    root = workspace._root(root)  # noqa: SLF001
    report, status = body.get("report"), body.get("status")
    research_records.path_for(report)
    mode, me, team = proposals.role_of(root)
    if mode not in ("single", "pi", "member"):
        raise _refused("read_only", f"The project is read-only for this user ({mode})", "当前项目只读，不能写研究判断。", 403)
    workspace._require_write(root)  # noqa: SLF001 -- a member writes only in their recognized working copy: asked before the table is
    reason = " ".join(str(body.get("reason") or "").split())
    was, given = body.get("was"), body.get("result")
    if (not isinstance(status, str) or len(reason) > research_records.REASON_LIMIT
            or any(value is not None and not isinstance(value, str) for value in (was, given))):
        raise _refused("invalid", "A confirmation names a status and a reason of at most "
                       f"{research_records.REASON_LIMIT} characters", "内容格式不对，请检查后重试", 400)
    given = " ".join((given or "").split())
    chosen = next((item for item in research_records.assessment_statuses(root) if item["status"] == status), None)
    if chosen is None:
        raise _refused("handback_status_unknown", f"{status!r} is not one of this project's statuses",
                       "这个研究判断的状态不在这个项目的选项里。", 400)
    if not chosen["marker"] and not reason:
        raise _refused("handback_reason_needed", "A status of Other needs the researcher's words", "选「其它」时，请写下你自己的话。", 400)
    task = _task_of(root, report)
    if task is None or not task.get("attempt"):
        raise _refused("handback_no_attempt", "This handback's task does not belong to an attempt, so there is no assessment to "
                       "write; put the task under an attempt first", "这份交回的任务还没有归入尝试，没有可写的研究判断。请先把任务归入一条尝试。")
    node = task["attempt"]
    if not any(item["id"] == node for item in workspace._attempts(root, None)):  # noqa: SLF001
        raise _refused("handback_attempt_unknown", "The attempt this task belongs to is no longer in the attempt table; put the "
                       "task under another attempt first", "这个任务所属的尝试已经不在尝试表里了。请先把任务重新归入一条尝试。")
    number = node.rpartition("#")[2]
    if _run_going(_project_id(root), task["id"]):
        raise _refused("handback_run_active", "A run of this task is still going and will hand its own report back; confirm "
                       "after it has", "这个任务还有一次运行没有结束，它会交回自己的报告。等它交回以后再确认。")
    stored = research_records.parse(workspace._read(root, research_records.path_for(report)))  # noqa: SLF001
    suggestion = stored.get("suggested_assessment")
    adopted = bool(suggestion) and suggestion["status"] == status and " ".join(suggestion["reason"].split()) == reason
    run = _handed_back_run(root, report)
    agent = (run or {}).get("label") or (agent_labels.parse_owner(task.get("owner")).label if _is_agent(task.get("owner")) else None)
    who = people.attribution({"by": me}, team if team.state == "present" else None)["label"]
    note = history.confirmed_note(who, agent if adopted else None)
    confirmed = {**({"by": me} if me else {}), "at": _now_iso(), "status": status, "adopted": adopted}
    research_records.confirmed_context(root, report, confirmed)  # refused now, not after the cell is written
    try:
        cells = mapedit.row_cells(root, number)
    except (mapedit.MapEditError, attempts_ingest.AttemptsConfigError) as exc:
        raise _table_refusal(exc, root) from exc
    if cells is None:
        raise _refused("handback_attempt_unknown", "The attempt table has no single row for this attempt", "尝试表里找不到这条尝试的那一行。")
    clean = attempts_ingest._clean_cell  # noqa: SLF001
    labels = attempts_ingest.verdict_labels(root)
    holds, result_now = clean(cells["verdict"]), clean(cells["result"])
    read = attempts_ingest.verdict_status(holds, labels)
    # 21.3: the status the cell has keeps the marker it was written with
    own = attempts_ingest.verdict_marker(holds, labels) if read["label"] == status else ""
    cell = attempts_ingest.compose_verdict(own if own in chosen["markers"] else chosen["marker"], reason)
    abandon_reason = " ".join(str(body.get("abandon_reason") or "").split())
    if mode == "member" and chosen["abandons"] and not (abandon_reason and (given or result_now.strip())):
        # the proposal a member's abandonment becomes is refused without both (12.4); the working copy is not written first
        raise _refused("handback_result_needed", "Abandoning a route needs its result and a reason, and nothing was changed",
                       "放弃一条路线时，结果和理由都必须填写。什么都没有改。")
    fields: dict[str, str] = {}
    if not ((read["label"] == status and " ".join(read["words"].split()) == reason) or _squashed(holds) == _squashed(clean(cell))):
        fields["verdict"] = cell
    if given and _squashed(given) != _squashed(result_now):
        fields["result"] = given
    if fields.get("verdict") is not None and was is not None and _squashed(was) != _squashed(holds):
        raise _refused("handback_moved", "The assessment of this attempt changed after the handback was opened, so nothing was changed",
                       "这条尝试的研究判断在你打开这份交回以后被改过，什么都没有改。页面里现在是它最新的样子，请看一眼再确认。")
    out: dict[str, Any] = {"written": False, "adopted": adopted, "note": note, "confirmed": confirmed, "number": number, "node": node,
                           "cell": cell, "file": None, "backup": None, "ingest_error": None, "decision": None, "mode": mode,
                           "history_error": None, "context_error": None,
                           "before": {"was": cells["verdict"], "was_text": holds, "now_text": clean(cell)},
                           "context_path": research_records.path_for(report),
                           "by_label": people.attribution({"by": me}, team if team.state == "present" else None, lang)["label"]}
    out["sentence"] = {"zh": note, "en": history_note_words(note, "en")}
    if fields:
        before = _max_seq(root)
        try:
            written = proposals.write_directly(root, "update", number, fields, abandon_reason or None, ingest_lock=ingest_lock)
        except (mapedit.MapEditError, attempts_ingest.AttemptsConfigError, proposals.ProposalRefused) as exc:
            raise _table_refusal(exc, root) from exc
        out.update(written=True, file=written["file"], backup=written["backup"], ingest_error=written["ingest_error"],
                   decision=(written.get("decision") or {}).get("id"))
        out["history_error"] = _record_history(root, node, before, me, note)
    try:
        research_records.confirm_assessment(root, report, confirmed)
    except workspace.WorkspaceError as exc:
        out["context_error"] = str(exc)
    return out


def _note_undo(root: Path, node: str, who: str | None, note: str) -> str | None:
    """The version of the attempt that 撤销 wrote when it put the cell back carries `note` and `who`: it is the newest
    version of the attempt, a scan that saw the row as the index holds it now and has no note of its own, and the note
    goes on it instead of a second version being added (as `_record_history` does for the confirmation). None, or why
    the history could not be updated (the confirmation is taken back all the same)."""
    try:
        identity = read_identity(root).identity
        if identity is None:
            return "the project has no readable identity"
        conn = db.connect(paths.graph_db_path(root))
        try:
            attempt = db.get_node(conn, node)
        finally:
            conn.close()
        if attempt is None:
            return f"{node} is not in the index"
        human = attempt.get("human_fields") or {}
        digest = history._attempt_digest({"verdict": str(human.get("verdict") or ""), "result": str(human.get("result") or "")})  # noqa: SLF001
        with history.Store.open(identity.id) as store:
            latest = store.latest(node)
            if latest is None or latest.source != "scan" or latest.note or latest.sha256 != digest:
                return "the version 撤销 wrote is not the newest version of the attempt"
            store.attribute(latest.seq, source="scan", who=who, note=note, approved_by=None)
    except Exception as exc:  # noqa: BLE001 -- see `_record_history`
        return str(exc)
    return None


def unconfirm(root: str | Path, body: Any, *, lang: str = "zh") -> dict[str, Any]:
    """`POST /api/workspace/handback/unconfirm` {report}: 撤销 on the line of a written confirmation has put the attempt's cell
    back as it was (the page's own write, `/api/attempts/write`); this takes the confirmation back too, so that the
    report's context no longer says the person confirmed what the table no longer says. `assessment_confirmed` leaves
    the context (`research_records.unconfirm_assessment`, the one other writer of the field) and the version 撤销
    wrote carries the note 「撤销了确认」 (`history.UNCONFIRMED_NOTE`, stored in Chinese and said in `lang` when it is read).
    Only for the PI and a single researcher, the cases where 撤销 is offered, and only for the confirmation of the person
    asking; refused while a later run of the task is going (its completion is checked against the context it started
    with, as for `confirm`) and when there is no confirmation. Answers `removed`, `note` (stored), `said` (in `lang`),
    `report`, `number`, `node` and `history_error` (the confirmation is taken back whether or not the note could be put on
    the history)."""
    if not isinstance(body, dict):
        raise _refused("invalid", "A request must be an object", "内容格式不对，请检查后重试", 400)
    root = workspace._root(root)  # noqa: SLF001
    report = body.get("report")
    research_records.path_for(report)
    mode, me, _team = proposals.role_of(root)
    if mode not in ("single", "pi"):
        raise _refused("handback_unconfirm_not_offered", "Only the PI or a single researcher takes a confirmation back; a member's "
                       "confirmation is a proposal", "只有 PI 和单人项目的研究者能撤销确认；成员的确认是一份提议。", 403)
    workspace._require_write(root)  # noqa: SLF001
    stored = research_records.parse(workspace._read(root, research_records.path_for(report)))  # noqa: SLF001
    confirmed = stored.get("assessment_confirmed")
    if confirmed is None:
        raise _refused("handback_not_confirmed", "This handback holds no confirmation to take back", "这份交回里没有可以撤销的确认。")
    if confirmed.get("by") != me:
        raise _refused("handback_not_yours", "Another person confirmed this assessment; only they take the confirmation back",
                       "这个判断是别人确认的，只有他们能撤销这次确认。", 403)
    task = _task_of(root, report)
    if task is not None and _run_going(_project_id(root), task["id"]):
        raise _refused("handback_run_active", "A run of this task is still going and will hand its own report back; take the "
                       "confirmation back after it has", "这个任务还有一次运行没有结束，它会交回自己的报告。等它交回以后再撤销确认。")
    research_records.unconfirm_assessment(root, report)
    node = (task or {}).get("attempt")
    note = history.UNCONFIRMED_NOTE
    return {"report": report, "removed": True, "note": note, "said": history_note_words(note, lang), "node": node,
            "number": node.rpartition("#")[2] if node else None,
            "history_error": _note_undo(root, node, me, note) if node else "the task has no attempt"}


def _is_agent(owner: Any) -> bool:

    try:
        return agent_labels.parse_owner(owner) is not None
    except ValueError:
        return False


def _handed_back_run(root: Path, report: str) -> dict[str, Any] | None:

    got = read_identity(root)
    return _handed_back(got.identity.id).get(report) if got.identity is not None else None


def history_note_words(note: str, lang: str) -> str:
    """A confirmation's note in `lang` (the note itself is stored in Chinese)."""
    return history.confirmed_note_words(note, lang) or note

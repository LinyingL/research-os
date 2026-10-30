"""Grouping the tasks a project already has (DESIGN.md 21.1, "How groups are proposed").

A project that kept its work as tasks before 21 has tasks with no `attempt`. `propose` reads them and *proposes*
groups -- each a new attempt row, prefilled, with the tasks that would go under it. `confirm` takes one group the
researcher kept: it writes the attempt row through the write the new-attempt form uses (`proposals.write_directly`:
the dry run, the conflict and abandonment rules, backup, re-ingest -- a member's row lands in the working copy, from
where it is proposed as 12.4 says) and only then sets `attempt` on the group's tasks, so a refused row leaves
`research/tasks.json` as it was. Nothing is moved, renamed or deleted on disk. `propose` reads only.

How groups are proposed -- a small rule, so that it can be said in a sentence and checked by eye. Only tasks with no
`attempt` are looked at, and every one lands in at most one group.

1. A task is read by its text (whitespace collapsed) and by the title of the report it points to (the report's first
   heading). Nothing else of the task is read, and nothing is sent anywhere.
2. Version groups. The first version token -- `v` and digits with optional `.digits` parts, in either case (v0, v0.2,
   V1.2) -- found in the task's text, and only when the text has none in its report's title, together with the Latin
   word right before it (`Sword v0.2`, not `a v0.2`), is the task's version. Tasks with the same version, subject
   compared without regard to case, form a group, even a group of one: a version is one attempt. Its name is the
   version as first written, followed by the words the group's tasks have in common, or by the first clause of the
   task when there is one.
3. Shared-term groups. Of the tasks left, a term is a Latin word of three letters or more that is not a common English
   word, or any run of three to eight Chinese characters that does not begin with a function character (对, 请, 一,
   上, ...) or end with one (的, 了, 等, ...), taken from the first line of the text (120 characters) and from the
   report's title. A term counts once for each distinct text (the same words twice are one text, not two). The term in the most texts, at
   least two, goes first, the longer one when equal, the one met first when still equal; the tasks that have it become
   a group and leave the pool; then again, until no term is shared. A term shared by more than half of eight texts or
   more is the project's own name and tells nothing, and is skipped. The group is named by the longest words its
   tasks' first lines have in common (the term when that is shorter).
4. Each group carries a new attempt: the next free number (the greatest whole number in the table plus one, then one
   more for each further group, groups in the order of their dates), the date, the name, 待定 as its research
   assessment and an empty result. The date is the earliest of the group's tasks' dates (`due`) and the days its
   reports were written; today when it has none. It is written the way the table writes dates (`MM-DD` when any row
   of the table does, else `YYYY-MM-DD`).
5. Duplicates: a task whose text, whitespace collapsed, is word for word that of an earlier task of the list is
   reported with that task (`same_as`) and the place of that task in the list (`number`). A duplicate stays in its
   group: the person decides to remove it.

The page proposes and the person decides: the name, the number and the date can be changed, tasks moved, groups merged
or dropped before one is confirmed. The engine says no sentence of its own here but its refusals (13.1)."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from researchos import attempt_tasks, paths, proposals, workspace
from researchos.ingest import attempts as attempts_ingest
from researchos.records import situation
from researchos.webapp import mapedit

TEXT_READ = 300      # characters of a task's text that are searched for a version
HEAD_READ = 120      # characters of its first line (and of a report's title) that terms are taken from
TERM_SIZES = (3, 8)  # a Chinese term: from this many characters to that many
GROUP_LIMIT = 30
PENDING = "待定"

_VERSION = re.compile(r"(?<![A-Za-z0-9_])[vV](\d+(?:\.\d+)*)(?![A-Za-z0-9_])")
_SUBJECT = re.compile(r"([A-Za-z][A-Za-z0-9]*(?:[_\-][A-Za-z0-9]+)*)[ \t_\-]*\Z")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[._&\-][A-Za-z0-9]+)*")
_CJK_RUN = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_PUNCTUATION = " \t\u3000.,;:!?()[]{}<>\"'`-_/\\|*#~\uff0c\u3002\u3001\uff1b\uff1a\uff01\uff1f\uff08\uff09\u3010\u3011\u300a\u300b\u300c\u300d\u201c\u201d\u2018\u2019\u2026\u00b7\u2014"
# Characters a Chinese term does not begin with (it begins a phrase: 对上一份, 请你, 把, 在) or end with (的, 了, 等).
_NOT_A_START = set("的了和与及或对给把请在是并将从向以个份这那其之也都就而被让上下一二三四五六七八九十第本该")
_NOT_AN_END = set("的了和与及或之也都就而被让等个份")
# Words before a version that name nothing ("run v0.2", "the v0.2 report") and common English words that are no term.
_NOT_A_SUBJECT = frozenset("a an the of in on at to for with and or vs from by as is this that new old final version ver "
                           "release tag rev revision draft run rerun using use all".split())
_COMMON = frozenset((
    "the and for with from that this into over under using use run runs rerun data task tasks report reports result "
    "results file files code script scripts analysis analyze check test tests review reply respond response write read "
    "make build update new old all any not are was were has have can will you your our their its via per vs etc "
    "please based about each both only also than then when what which who how why").split())
_VERSION_ONLY = re.compile(r"v\d+(?:\.\d+)*\Z")


def _squash(text: str) -> str:
    return " ".join(text.split())


# -- reading the tasks -----------------------------------------------------------------------------------------


def _report_facts(root: Path, rel: str) -> tuple[str | None, str | None]:
    """The first heading of the report a task points to and the day (local, `YYYY-MM-DD`) it was written; None for
    what cannot be read -- a task whose report is gone is still a task."""
    try:
        raw = workspace._read(root, rel)  # noqa: SLF001
        if raw is None:
            return None, None
        title = workspace._heading(workspace._decode(raw)[:4096])  # noqa: SLF001
        return title, date.fromtimestamp((root / rel).stat().st_mtime).isoformat()
    except (workspace.WorkspaceError, OSError, ValueError, OverflowError):
        return None, None


def _version_of(text: str, title: str | None) -> tuple[str, str, str] | None:
    """`(key, shown, subject)` of the version a task is about: the first token of its text, else of its report's title,
    with the Latin word before it (rule 2). `key` is for comparing (lower case), `shown` as first written."""
    for source in (text[:TEXT_READ], (title or "")[:TEXT_READ]):
        found = _VERSION.search(source)
        if found is None:
            continue
        before = _SUBJECT.search(source[:found.start()])
        subject = before.group(1) if before and before.group(1).lower() not in _NOT_A_SUBJECT else ""
        number = "v" + found.group(1)
        return (f"{subject.lower()} {number}".strip(), f"{subject} {number}".strip(), subject)
    return None


def _terms(head: str) -> list[str]:
    """The terms of one first line or title (rule 3), each once, in the order they are met; a Latin one lower case."""
    out: dict[str, None] = {}
    for found in _LATIN.finditer(head):
        word = found.group(0).lower()
        if len(word) >= 3 and word not in _COMMON and not _VERSION_ONLY.fullmatch(word):
            out[word] = None
    low, high = TERM_SIZES
    for run in _CJK_RUN.findall(head):
        for size in range(low, min(high, len(run)) + 1):
            for at in range(len(run) - size + 1):
                gram = run[at:at + size]
                if gram[0] not in _NOT_A_START and gram[-1] not in _NOT_AN_END:
                    out[gram] = None
    return list(out)


def _common(strings: list[str]) -> str:
    """The longest stretch every one of `strings` holds, cut of punctuation and function characters at its ends;
    empty when what is left is no term (shorter than three characters)."""
    strings = [s for s in strings if s]
    if not strings:
        return ""
    if len(strings) == 1:
        return strings[0]
    first = min(strings, key=len)
    for size in range(len(first), TERM_SIZES[0] - 1, -1):
        for at in range(len(first) - size + 1):
            piece = first[at:at + size]
            if all(piece in s for s in strings):
                cut = piece.strip(_PUNCTUATION)
                while cut and _CJK_RUN.fullmatch(cut[0]) and cut[0] in _NOT_A_START:
                    cut = cut[1:].lstrip(_PUNCTUATION)
                while cut and _CJK_RUN.fullmatch(cut[-1]) and cut[-1] in _NOT_AN_END:
                    cut = cut[:-1].rstrip(_PUNCTUATION)
                words = cut.split(" ")   # and, in Latin, a dangling "of" or "the" at either end
                while len(words) > 1 and words[-1].lower() in _NOT_A_SUBJECT:
                    words.pop()
                while len(words) > 1 and words[0].lower() in _NOT_A_SUBJECT:
                    words.pop(0)
                cut = " ".join(words)
                if len(cut) >= TERM_SIZES[0] or (cut and _LATIN.fullmatch(cut)):
                    return cut
    return ""


def _clause(text: str) -> str:
    """The first clause of a first line, at most 40 characters."""
    first = re.split(r"[。！？!?；;\n]", text, maxsplit=1)[0].strip(_PUNCTUATION)
    return first if len(first) <= 40 else first[:40].rstrip(_PUNCTUATION) + "…"


def _without_version(head: str, subject: str, number: str) -> tuple[str, bool]:
    """`head` without the version it names (and the subject right before it), and whether it named it."""
    gone = (re.escape(subject) + r"[ \t_\-]*" if subject else "") + "[vV]" + re.escape(number[1:]) + r"(?![A-Za-z0-9_])"
    cut, found = re.subn(gone, " ", head, flags=re.IGNORECASE)
    return _squash(cut).strip(_PUNCTUATION), bool(found)


# -- the attempt table's facts -----------------------------------------------------------------------------------


def _table(root: Path) -> dict[str, Any]:
    """What the new rows are numbered and dated by: whether the project has an attempt table at all (`config`), the
    greatest whole number in it (the same `defaultNewNumber` the form uses: "14a" counts as 14) and whether it dates
    its rows `MM-DD` (as `defaultDateValue`)."""
    try:
        attempts_ingest.load_config(root)
        config = True
    except (attempts_ingest.AttemptsConfigError, OSError, ValueError, TypeError):
        config = False
    biggest, short = 0, False
    try:
        path = paths.graph_db_path(root)
        if path.is_file() and not paths.is_dataless(path):
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
                for (raw,) in conn.execute("SELECT attrs FROM nodes WHERE type = 'attempt'"):
                    attrs = json.loads(raw)
                    found = re.match(r"\s*(\d+)", str(attrs.get("number") or ""))
                    biggest = max(biggest, int(found.group(1))) if found else biggest
                    short = short or bool(re.match(r"\d{2}-\d{2}", str(attrs.get("date") or "")))
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError, paths.IdentityUnavailableError):
        pass
    return {"config": config, "biggest": biggest, "short": short}


def pending_marker(root: Path) -> str:
    """The marker the table writes for 待定: the project's own (`[verdict_labels]` over the defaults), else 🕒."""
    for marker, label in attempts_ingest.verdict_labels(root).items():
        if label == PENDING:
            return marker
    return "🕒"


# -- proposing ---------------------------------------------------------------------------------------------------


def propose(root: str | Path, *, today: date | None = None) -> dict[str, Any]:
    """`GET /api/workspace/task-groups`: `tasks_revision`; `table` (whether the project has an attempt table to write
    to); `tasks`, the tasks with no attempt -- or one the table no longer has -- in the order of the list, each with `number` (its place in the whole list,
    from 1), `text`, `owner`, `owner_label`, `due`, `report` and `report_title`; `groups`, each `id`, `basis`
    (`version` | `term`), `term` (what the tasks share), `tasks` (their ids, in list order) and `attempt` -- the row
    to write: `number`, `date`, `description`, `variables`, `result` (empty) and `verdict` (the marker of 待定) --
    with `verdict_status` as the page words it; `ungrouped` (ids in no group); and `duplicates`, each `task`,
    `same_as` and `number` (the place of that task in the list). Reads only."""
    root = workspace._root(root)  # noqa: SLF001
    tasks, raw = workspace._read_tasks(root)  # noqa: SLF001
    facts = _table(root)
    known = {row["id"] for row in workspace._attempts(root, None)}  # noqa: SLF001
    # a task whose attempt row was removed or renumbered by hand is as loose as one with none: the page lists it so too
    loose = [(at + 1, task) for at, task in enumerate(tasks) if not task.get("attempt") or task["attempt"] not in known]
    reports: dict[str, tuple[str | None, str | None]] = {}
    for _n, task in loose:
        rel = task.get("report")
        if rel and rel not in reports:
            reports[rel] = _report_facts(root, rel)
    info = {task["id"]: {"id": task["id"], "number": n, "text": task["text"], "owner": task.get("owner", ""),
                         "owner_label": attempt_tasks.owner_label(task.get("owner", "")), "due": task.get("due", ""),
                         "report": task.get("report") or None,
                         "report_title": reports.get(task.get("report") or "", (None, None))[0]} for n, task in loose}
    squashed = {task["id"]: _squash(task["text"]) for _n, task in loose}
    # Rule 2, then rule 3, over what is left.
    found: list[dict[str, Any]] = []
    left = []
    by_version: dict[str, dict[str, Any]] = {}
    for _n, task in loose:
        version = _version_of(squashed[task["id"]], info[task["id"]]["report_title"])
        if version is None:
            left.append(task["id"])
            continue
        key, shown, subject = version
        group = by_version.setdefault(key, {"id": "v:" + key, "basis": "version", "term": shown, "subject": subject, "tasks": []})
        group["tasks"].append(task["id"])
    found.extend(by_version.values())
    found.extend(_term_groups(left, squashed, info))
    for group in found:
        group["tasks"].sort(key=lambda tid: info[tid]["number"])
        group["name"] = _name(group, squashed, info)
    when = {group["id"]: _earliest([info[tid] for tid in group["tasks"]], reports) for group in found}
    found.sort(key=lambda group: (when[group["id"]] or "9999-99-99", info[group["tasks"][0]]["number"]))
    marker = pending_marker(root)
    labels = attempts_ingest.verdict_labels(root)
    out = []
    for at, group in enumerate(found[:GROUP_LIMIT]):
        iso = when[group["id"]] or (today or date.today()).isoformat()
        out.append({"id": group["id"], "basis": group["basis"], "term": group["term"], "tasks": group["tasks"],
                    "attempt": {"number": str(facts["biggest"] + 1 + at), "date": iso[5:] if facts["short"] else iso,
                                "description": group["name"], "variables": "", "result": "", "verdict": marker},
                    "verdict_status": attempts_ingest.verdict_status(marker, labels)})
    grouped = {tid for group in out for tid in group["tasks"]}
    return {"tasks_revision": workspace._revision(raw), "table": facts["config"],  # noqa: SLF001
            "tasks": list(info.values()), "groups": out,
            "ungrouped": [tid for tid in info if tid not in grouped], "duplicates": _duplicates(tasks, info, squashed)}


def _term_groups(left: list[str], squashed: dict[str, str], info: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Rule 3 over the tasks in `left` (ids, in list order)."""
    heads: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    terms: dict[str, dict[str, None]] = {}   # in the order they are met, which breaks a tie (a set would not keep it)
    for tid in left:
        text = squashed[tid]
        members.setdefault(text, []).append(tid)
        first = (info[tid]["text"].strip().splitlines() or [""])[0]
        heads.setdefault(text, _squash(first)[:HEAD_READ])
        terms.setdefault(text, {}).update(dict.fromkeys(_terms(heads[text]) + _terms(_squash(info[tid]["report_title"] or "")[:HEAD_READ])))
    order: dict[str, int] = {}
    for text in members:
        for term in terms[text]:
            order.setdefault(term, len(order))
    pool = list(members)
    cap = len(pool) / 2 if len(pool) >= 8 else len(pool)   # of all the texts looked at, not of those still left
    groups: list[dict[str, Any]] = []
    while len(groups) < GROUP_LIMIT:
        counts = Counter(term for text in pool for term in terms[text])
        shared = [term for term, n in counts.items() if 2 <= n <= cap]
        if not shared:
            break
        best = max(shared, key=lambda term: (counts[term], len(term), -order[term]))
        chosen = [text for text in pool if best in terms[text]]
        pool = [text for text in pool if best not in terms[text]]
        together = _common([heads[text] for text in chosen])
        written = re.search(re.escape(best), heads[chosen[0]], re.IGNORECASE)
        shown = together if len(together) >= len(best) else written.group(0) if written else best
        groups.append({"id": "t:" + best, "basis": "term", "term": shown, "subject": "",
                       "tasks": [tid for text in chosen for tid in members[text]]})
    return groups


def _name(group: dict[str, Any], squashed: dict[str, str], info: dict[str, dict[str, Any]]) -> str:
    """What the group's attempt is called (rules 2 and 3)."""
    if group["basis"] == "term":
        return group["term"]
    shown = group["term"]
    number = shown.rsplit(" ", 1)[-1]
    named, others = [], []   # the first lines that say the version, and those whose version is in a report's title
    for tid in group["tasks"]:
        first = (info[tid]["text"].strip().splitlines() or [""])[0]
        left, said = _without_version(_squash(first)[:HEAD_READ], group["subject"], number)
        if left and left not in (named if said else others):
            (named if said else others).append(left)
    rest = named or others
    words = _common(rest) if len(rest) > 1 else _clause(rest[0]) if rest else ""
    return f"{shown} {words}".strip()


def _earliest(group: list[dict[str, Any]], reports: dict[str, tuple[str | None, str | None]]) -> str | None:
    """The earliest day among a group's tasks' dates and its reports' (rule 4)."""
    days = [task["due"] for task in group if task["due"]]
    days += [reports[task["report"]][1] for task in group if task["report"] and reports.get(task["report"], (None, None))[1]]
    return min(days) if days else None


def _duplicates(tasks: list[dict[str, Any]], info: dict[str, dict[str, Any]], squashed: dict[str, str]) -> list[dict[str, Any]]:
    """Rule 5: each task with no attempt whose text is that of an earlier task of the list."""
    first: dict[str, tuple[int, str]] = {}
    out = []
    for at, task in enumerate(tasks):
        text = squashed.get(task["id"], _squash(task["text"]))
        if text in first and task["id"] in info:
            out.append({"task": task["id"], "same_as": first[text][1], "number": first[text][0]})
        first.setdefault(text, (at + 1, task["id"]))
    return out


# -- confirming ----------------------------------------------------------------------------------------------------

_ATTEMPT_WORDS = {
    "duplicate": "这个编号已经在尝试表里了，请换一个编号。",
    "table_missing": "找不到尝试表了：尝试表文件、它的标题或 .researchos/attempts.toml 已经不在。",
}


def _invalid(message: str, message_zh: str) -> workspace.WorkspaceError:
    return workspace.WorkspaceError("invalid", message, 400, message_zh)


def _body(body: Any) -> tuple[dict[str, str], list[str], str | None]:
    """The attempt row's `number`, `date` and `description`, the ids of the tasks and the revision the page saw (None
    when it sends none: the engine then reads the list under the lock, so a run that saved a report meanwhile is no
    conflict)."""
    if not isinstance(body, dict) or set(body) - {"attempt", "tasks", "tasks_revision"}:
        raise _invalid("A group is an attempt, its tasks and optionally the tasks revision", "请求不完整，什么都没有改。")
    row, ids = body.get("attempt"), body.get("tasks")
    if (not isinstance(row, dict) or set(row) - {"number", "date", "description"}
            or not all(isinstance(row.get(key, ""), str) for key in ("number", "date", "description"))):
        raise _invalid("The attempt must carry a number, a date and a description", "尝试要有编号、日期和描述。")
    if not isinstance(ids, list) or not ids or len(ids) > workspace.TASK_LIMIT or not all(isinstance(x, str) for x in ids):
        raise _invalid("A group holds a list of task ids", "这一组要有至少一个任务。")
    revision = body.get("tasks_revision")
    if revision is not None and not isinstance(revision, str):
        raise _invalid("The tasks revision is text", "请求不完整，什么都没有改。")
    cells = {key: " ".join(str(row.get(key, "")).split()) for key in ("number", "date", "description")}
    if not cells["number"] or not cells["description"]:
        raise _invalid("The attempt needs a number and a description", "尝试要有编号和描述。")
    if len(cells["description"]) > 500 or len(cells["date"]) > 40 or len(cells["number"]) > 40:
        raise _invalid("The attempt's cells are too long", "尝试的描述、编号或日期太长了。")
    return cells, list(dict.fromkeys(ids)), revision


def _refused(exc: Exception, root: Path) -> workspace.WorkspaceError:
    """The attempt table's refusal as the page's: nothing was changed, in the request's language."""
    if isinstance(exc, proposals.ProposalRefused):
        return workspace.WorkspaceError("group_refused", str(exc), 400, f"{exc.message_zh}。什么都没有改。")
    code = mapedit.error_code(exc, root)
    said = _ATTEMPT_WORDS.get(code or "", "尝试表没有接受这一行。")
    return workspace.WorkspaceError("group_" + (code or "refused"), f"The attempt table refused the row, so nothing was changed: {exc}",
                                    400, f"{said}什么都没有改。")


def confirm(root: str | Path, body: Any, *, ingest_lock: Any = None) -> dict[str, Any]:
    """`POST /api/workspace/task-groups`: write the attempt row (`body["attempt"]`: `number`, `date`, `description`;
    the assessment is 待定 and the result empty), then set `attempt` on the tasks `body["tasks"]` names. Everything
    is checked before the row is written -- who may write, that the tasks still exist and still have no attempt,
    the revision when one is sent, the row as a dry run -- and a refused row leaves the task file untouched. Answers
    the attempt (`id`, `number`, `label`, `file`, `date`), `moved` (the task ids), `tasks` and `tasks_revision` as
    the task save does, and `ingest_error` when the row was written but the index could not be read again: the
    tasks are then not moved (`linked` false), since an attempt the index lacks cannot be named."""
    root = workspace._root(root)  # noqa: SLF001
    cells, ids, revision = _body(body)
    with situation.write_guard(root, human=True):
        workspace._require_write(root)  # noqa: SLF001
        tasks, raw = workspace._read_tasks(root)  # noqa: SLF001
        if revision is not None and revision != workspace._revision(raw):  # noqa: SLF001
            raise workspace.WorkspaceError("conflict", "The tasks changed after the groups were proposed", 409,
                                           "任务在提议分组以后又改过。请重新打开这个面板。")
        by_id = {task["id"]: task for task in tasks}
        gone = [tid for tid in ids if tid not in by_id]
        if gone:
            raise workspace.WorkspaceError("group_task_gone", f"Task {gone[0]} is no longer in the list, so nothing was changed", 409,
                                           "有一个任务已经不在任务列表里了，什么都没有改。请重新打开这个面板。")
        known = {row["id"] for row in workspace._attempts(root, None)}  # noqa: SLF001 -- before the row below is written
        taken = [tid for tid in ids if by_id[tid].get("attempt") in known]
        if taken:
            raise workspace.WorkspaceError("group_task_taken", f"Task {taken[0]} already belongs to an attempt, so nothing was changed", 409,
                                           "有一个任务已经属于某条尝试了，什么都没有改。请重新打开这个面板。")
        fields = {"description": cells["description"], "date": cells["date"], "verdict": pending_marker(root)}
        try:
            mapedit.preview_edit(root, "append", cells["number"], fields)
            written = proposals.write_directly(root, "append", cells["number"], fields, None, ingest_lock=ingest_lock)
        except (mapedit.MapEditError, attempts_ingest.AttemptsConfigError, proposals.ProposalRefused) as exc:
            raise _refused(exc, root) from exc
        node = f"attempt:{written['file']}#{cells['number']}"
        out: dict[str, Any] = {
            "attempt": {"id": node, "number": cells["number"], "label": cells["description"], "file": written["file"],
                        "date": cells["date"]},
            "moved": [], "linked": False, "tasks": tasks, "tasks_revision": workspace._revision(raw),  # noqa: SLF001
            "backup": written["backup"], "ingest_error": written["ingest_error"], "history_error": None,
            "changed_task_reports": [], "mode": proposals.role_of(root)[0]}
        if written["ingest_error"]:
            return out
        wanted = set(ids)
        try:
            saved = workspace.save_tasks(root, {"tasks": [{**task, "attempt": node} if task["id"] in wanted else task for task in tasks],
                                                "revision": workspace._revision(raw)})  # noqa: SLF001
        except workspace.WorkspaceError as exc:
            raise workspace.WorkspaceError(
                "group_not_linked", f"The attempt row {cells['number']} was written, but its tasks could not be moved into it: {exc}",
                409, f"尝试 #{cells['number']} 已经写进尝试表，但任务没能归入它：{exc.message_zh}。请在每个任务的「属于哪条尝试」里选这一条。") from exc
        out.update(moved=ids, linked=True, tasks=saved["tasks"], tasks_revision=saved["tasks_revision"],
                   history_error=saved["history_error"], changed_task_reports=saved["changed_task_reports"])
        return out

"""Research OS V16, task B2-1 (DESIGN.md 21.4 'At the top, 现在在做什么'): the engine.

Two parts for the person asking: 进行中的尝试 (the attempts assessed 现行 or 待定 that hold at least one task, newest
activity first, with the figures `attempt_tasks.by_attempt` gives the route map's cards) and 等你处理 (one list: unopened
handbacks, runs that wait for an answer, failed or out-of-bounds runs, and what 研究路线's 待办 column held). The
acceptance case is Quant after the grouping of 21.1 -- Sword v0, v0.2 and v0.3, every task handed back -- and the thesis
project with attempts kept by hand and no task. Every test names its scenario."""

from __future__ import annotations

import dataclasses
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from researchos import attempt_tasks, desk, handbacks, now_doing, people, proposals, share, workspace
from researchos.agents import runs
from researchos.records.identity import read_identity

from test_agents_mcp import _run
from test_v10_desk_payload import _single, _two_people
from test_v10_route_link import drive  # noqa: F401 -- the two-person fixture
from test_v16_handback import AGENT, OTHER, PERSON, hand_back
from test_v16_task_groups import TODAY, _confirm, _group, quant  # noqa: F401 -- the researcher's Quant, and the grouping write
from test_v8_phase2 import ATTEMPTS_TOML, _conn, _project, _scan

A = {n: f"attempt:map.md#{n}" for n in range(1, 10)}
A16, A17 = "attempt:map.md#16", "attempt:map.md#17"
#: Quant after the grouping: three Sword versions assessed 待定, a survey with adverse evidence, an idea with no task, a
#: settled baseline kept by hand.
QUANT = ("# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n"
         "| 4 | 10-20 | Sword v0 | V | r4 | 🕒 |\n| 5 | 10-21 | Sword v0.2 | V | r5 | 🕒 |\n| 6 | 10-22 | Sword v0.3 | V | r6 | 🕒 |\n"
         "| 7 | 10-25 | Data source survey | V | r7 | ⚠️ 没有 alpha |\n| 8 | 10-26 | Idle idea | V | r8 | 🕒 |\n"
         "| 9 | 10-18 | Settled baseline | V | r9 | ✅ |\n")


@pytest.fixture(autouse=True)
def me() -> people.Me:
    """This Mac belongs to 张三, who starts the runs of these tests."""
    return people.write_me("张三", PERSON)


def _with_table(root: Path, table: str, year: bool = True) -> Path:
    (root / "map.md").write_text(table)
    (root / ".researchos" / "attempts.toml").write_text(
        ATTEMPTS_TOML.replace('steps_dir = "steps"\n', 'steps_dir = "steps"\ndate_year = 2026\n') if year else ATTEMPTS_TOML)
    _scan(root)
    return root


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "results").mkdir()
    (tmp_path / "proj" / "results" / "sharpe.csv").write_text("member,sharpe\nall,1.82\nn,12\n")
    return _with_table(_project(tmp_path / "proj"), QUANT)


@pytest.fixture
def pid(root: Path) -> str:
    return read_identity(root).identity.id


def _at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=timezone.utc)


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds").replace("+00:00", "Z")


def _same(text: str, when: datetime) -> bool:
    return attempt_tasks.parse_time(text) == when


def handed(root: Path, pid: str, task: str, attempt: str, when: datetime, *, opened: bool = True, by: str | None = PERSON) -> str:
    """A run of `task` that handed its report back at `when` (the run's start and end, the report's time); opened by
    the person who started it unless it is meant to wait."""
    report = hand_back(root, pid, task_id=task, attempt=attempt, by=by)
    run = next(r for r in runs.iter_runs(pid) if r.get("report") == report)
    runs.update_run(pid, run["id"], started=_iso(when - timedelta(minutes=5)), ended=_iso(when))
    os.utime(root / report, (when.timestamp(), when.timestamp()))
    if opened:
        handbacks.mark_opened(pid, PERSON, report)
    return report


def asking(root: Path, pid: str, task: str, attempt: str, state: str, *, by: str | None = PERSON, when: datetime | None = None, **more) -> str:
    """An agent task under `attempt` whose latest run (started by `by`) is in `state`."""
    tasks = workspace.read_tasks(root)[0]
    if not any(t["id"] == task for t in tasks):
        workspace.save_tasks(root, {"tasks": tasks + [{"id": task, "text": f"Question {task}\nsecond line", "done": False,
                                                         "owner": AGENT.owner, "due": "", "attempt": attempt}],
                                    "revision": workspace.read_tasks(root)[1]})
    run_id = _run(root, pid, state=state, by=by, task_id=task)
    runs.update_run(pid, run_id, ended=_iso(when or _at(29)), **more)
    return run_id


def _now(root: Path, **options) -> dict:
    conn = _conn(root)
    try:
        return now_doing.payload(conn, root, **options)
    finally:
        conn.close()


def _of(got: dict, kind: str) -> list[dict]:
    return [row for row in got["needs"]["rows"] if row["kind"] == kind]


def _numbers(got: dict) -> list[str]:
    return [a["number"] for a in got["progress"]["attempts"]]


# -- 进行中的尝试 --------------------------------------------------------------------------------------------


def test_quant_after_grouping_lists_the_three_sword_attempts_and_the_settled_one_newest_activity_first(root, pid):
    """Sword v0 (#4, tasks 7 and 8), v0.2 (#5, 12 and 13, 13 not opened yet) and v0.3 (#6, 14) all assessed 待定 with every
    task handed back, and a human task under the settled baseline #9: all four are listed, newest activity first --
    the run and report times of #5 (24th), #6 (23rd), #4 (20th), then #9 by its row's date (18th). #7 (⚠️ with a task),
    #8 (🕒 with none) are not."""
    handed(root, pid, "t7", A[4], _at(20, 9))
    handed(root, pid, "t8", A[4], _at(20, 12))
    handed(root, pid, "t12", A[5], _at(24, 9))
    handed(root, pid, "t13", A[5], _at(24, 11), opened=False)
    handed(root, pid, "t14", A[6], _at(23, 9))
    handed(root, pid, "t20", A[7], _at(29, 9))
    workspace.save_tasks(root, {"tasks": workspace.read_tasks(root)[0] + [{"id": "t30", "text": "Read the baseline again", "done": False, "owner": "", "due": "", "attempt": A[9]}],
                                "revision": workspace.read_tasks(root)[1]})
    got = _now(root)
    assert _numbers(got) == ["5", "6", "4", "9"]
    assert got["progress"]["holding"] == 5   # #7 holds a task too, though it is not in progress
    listed = {a["number"]: a for a in got["progress"]["attempts"]}
    assert [listed[n]["route_status"]["status"] for n in ("5", "6", "4", "9")] == ["pending", "pending", "pending", "current"]
    assert _same(listed["5"]["activity"], _at(24, 11)) and _same(listed["4"]["activity"], _at(20, 12))
    assert listed["9"]["activity"].startswith("2026-10-18")   # no run and no report: the date of its row
    # the figures are the card's: the engine's by_attempt reading, not counted again
    cards = attempt_tasks.by_attempt(root, pid, PERSON, [A[4], A[5], A[6], A[9]])
    assert {n: listed[n]["work"] for n in listed} == {"4": cards[A[4]], "5": cards[A[5]], "6": cards[A[6]], "9": cards[A[9]]}
    assert (listed["4"]["work"]["tasks"], listed["4"]["work"]["unfinished"], listed["4"]["work"]["unopened"]) == (2, 0, 0)
    assert (listed["5"]["work"]["tasks"], listed["5"]["work"]["unfinished"], listed["5"]["work"]["unopened"]) == (2, 0, 1)
    assert listed["4"]["work"]["latest"]["line"].startswith("Sword v0.2 keeps a Sharpe ratio")   # the headline of the latest handback
    assert (listed["9"]["work"]["tasks"], listed["9"]["work"]["unfinished"]) == (1, 1)


def test_a_pending_attempt_whose_tasks_are_all_finished_is_listed_because_it_waits_for_the_assessment(root, pid):
    """Batch 1 made a task that handed back read 已完成: 21.4 no longer asks for an unfinished task or an unopened handback,
    because the person's assessment is what a 待定 attempt with every task handed back waits for."""
    handed(root, pid, "t14", A[6], _at(22))
    got = _now(root)
    (card,) = got["progress"]["attempts"]
    assert card["number"] == "6" and card["work"]["unfinished"] == 0 and card["work"]["unopened"] == 0
    assert [i["state"] for i in card["work"]["items"]] == ["done"]


@pytest.mark.parametrize("marker, listed", [("🕒", True), ("✅", True), ("⚠️", False), ("☠️ 方向反了", False), ("⛔", False), ("📋", False), ("🧪 自己的", False)])
def test_only_an_attempt_assessed_current_or_pending_is_in_progress(root, pid, marker, listed):
    _with_table(root, QUANT.replace("| 9 | 10-18 | Settled baseline | V | r9 | ✅ |", f"| 9 | 10-18 | Settled baseline | V | r9 | {marker} |"))
    handed(root, pid, "t30", A[9], _at(22))
    assert ("9" in _numbers(_now(root))) is listed


def test_the_activity_is_the_latest_of_the_runs_the_reports_and_the_date_of_the_row(root, pid):
    """The rule 21.4 gives: a run's start or end, a handback's report time, the row's date -- whichever is latest. #6's row is
    dated the 27th, after its run and report of the 22nd, so its date decides; #5 has a report written after its run ended; #4
    has a run that started after it."""
    _with_table(root, QUANT.replace("| 6 | 10-22 |", "| 6 | 10-27 |"))
    handed(root, pid, "t14", A[6], _at(22))
    report = handed(root, pid, "t12", A[5], _at(24, 9))
    os.utime(root / report, (_at(25, 9).timestamp(), _at(25, 9).timestamp()))
    handed(root, pid, "t7", A[4], _at(20, 9))
    run = next(r for r in runs.iter_runs(pid) if r["task_id"] == "t7")
    runs.update_run(pid, run["id"], started=_iso(_at(26, 9)))   # a rerun that is going: started later than it ended
    got = {a["number"]: a["activity"] for a in _now(root)["progress"]["attempts"]}
    assert got["6"].startswith("2026-10-27") and _same(got["5"], _at(25, 9)) and _same(got["4"], _at(26, 9))
    assert _numbers(_now(root)) == ["6", "4", "5"]


def test_an_attempt_with_no_time_at_all_comes_after_the_others_in_the_tables_order(root, pid):
    """No `date_year`: the table's days cannot be read, so a human task with no run has no activity; it is listed after
    the attempts that have some, and two of them in the table's order."""
    _with_table(root, QUANT, year=False)
    workspace.save_tasks(root, {"tasks": [{"id": "h1", "text": "Think", "done": False, "owner": "", "due": "", "attempt": A[9]},
                                          {"id": "h2", "text": "Think more", "done": False, "owner": "", "due": "", "attempt": A[4]}],
                                "revision": workspace.read_tasks(root)[1]})
    handed(root, pid, "t14", A[6], _at(22))
    got = _now(root)
    assert _numbers(got) == ["6", "4", "9"]    # with no day to read the table orders its rows by their number: #4 before #9
    assert [a["activity"] is None for a in got["progress"]["attempts"]] == [False, True, True]


def test_a_thesis_with_only_hand_kept_attempts_and_no_task_has_nothing_in_progress_and_nothing_waits(tmp_path):
    """24 attempts kept by hand and no task: none is in progress, none holds a task (the page says so in one quiet
    sentence), and the list is empty."""
    root = _project(tmp_path / "thesis")
    rows = "".join(f"| {n} | 10-{n:02d} | Idea {n} | V | r{n} | {'✅' if n % 3 else '🕒'} |\n" for n in range(1, 25))
    _with_table(root, "# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n" + rows)
    got = _now(root)
    assert got["progress"] == {"attempts": [], "holding": 0} and got["needs"]["rows"] == []
    assert got["role"] == "single" and got["needs"]["notices"] == []


def test_tasks_that_belong_to_no_attempt_do_not_put_an_attempt_in_progress(root):
    # a task list as an older version left it: no `attempt` on its task
    (root / workspace.TASKS_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / workspace.TASKS_PATH).write_text(json.dumps([{"id": "old", "text": "Before attempts", "done": False, "owner": "", "due": ""}]))
    got = _now(root)
    assert got["progress"] == {"attempts": [], "holding": 0}


def test_the_light_payload_does_not_work_out_the_map(root, pid):
    """The home reads the desk's `light` payload: the cards carry their number, title, status and marks, and no `work`,
    `changed`, result, reason or decision; the full desk payload is unchanged and carries the same list."""
    handed(root, pid, "t14", A[6], _at(22), opened=False)
    conn = _conn(root)
    try:
        light = desk.payload(conn, root, light=True)
        full = desk.payload(conn, root)
    finally:
        conn.close()
    assert {"work", "changed", "changed_at", "result", "reason", "decision"} & set(light["attempts"][0]) == set()
    assert {"id", "number", "title", "date", "date_key", "route_status", "tone", "marks", "order"} <= set(light["attempts"][0])
    assert {"work", "changed", "changed_at", "result", "reason", "decision"} <= set(full["attempts"][0])
    assert [r["key"] for r in light["needs"]["rows"]] == [r["key"] for r in full["needs"]["rows"]] != []


# -- 等你处理: handbacks and runs ------------------------------------------------------------------------------


def test_an_unopened_handback_is_a_row_for_the_person_who_started_the_run_and_opening_it_removes_it_for_that_person_only(root, pid):
    report = handed(root, pid, "t12", A[5], _at(24), opened=False)
    (row,) = _of(_now(root), "handback")
    assert (row["report"], row["title"], row["task"], row["attempt"], row["run"]) == (
        report, "Recheck", {"id": "t12", "text": "Recheck Sword v0.2"}, {"id": A[5], "number": "5", "title": "Sword v0.2"},
        next(r["id"] for r in runs.iter_runs(pid) if r.get("report") == report))
    assert row["headline"].startswith("Sword v0.2 keeps a Sharpe") and row["attempts"] == [A[5]] and row["key"] == "h:" + report
    assert row["at"] == _iso(_at(24))
    people.write_me("李", OTHER)   # another person on this Mac: the run was not theirs
    assert _of(_now(root), "handback") == []
    people.write_me("张三", PERSON)
    assert len(_of(_now(root), "handback")) == 1
    handbacks.mark_opened(pid, OTHER, report)   # opened by someone else: not by the person it waits for
    assert len(_of(_now(root), "handback")) == 1
    handbacks.mark_opened(pid, PERSON, report)
    assert _of(_now(root), "handback") == []


def test_a_handback_opened_by_one_person_still_waits_for_the_other_whose_run_it_was(root, pid):
    """A run that names nobody (no `me.toml` when it started) is whoever's Mac it is: each of two people on one Mac has the row,
    and opening it clears it for that person only."""
    report = handed(root, pid, "t12", A[5], _at(24), opened=False, by=None)
    handbacks.mark_opened(pid, PERSON, report)
    assert _of(_now(root), "handback") == []
    people.write_me("李", OTHER)
    assert [r["report"] for r in _of(_now(root), "handback")] == [report]


def test_a_run_waiting_for_an_answer_is_a_row_for_the_person_who_started_it(root, pid):
    run = asking(root, pid, "q1", A[5], "needs_input", question="Which sample should I use?", when=_at(28))
    asking(root, pid, "q2", A[6], "needs_input", by=OTHER, when=_at(28))   # someone else's question
    (row,) = _of(_now(root), "answer")
    assert (row["run"], row["question"], row["task"], row["attempt"]["number"], row["key"]) == (
        run, "Which sample should I use?", {"id": "q1", "text": "Question q1"}, "5", "run:" + run)
    assert row["at"] == _iso(_at(28))
    people.write_me("李", OTHER)
    assert [r["task"]["id"] for r in _of(_now(root), "answer")] == ["q2"]


@pytest.mark.parametrize("state, kind", [("failed", "failed"), ("interrupted", "failed"), ("ended", "failed"), ("out_of_bounds", "out_of_bounds")])
def test_a_failed_or_out_of_bounds_run_is_a_row_while_it_is_its_tasks_latest_run(root, pid, state, kind):
    run = asking(root, pid, "q1", A[5], state, outcome="done")
    (row,) = _of(_now(root), kind)
    assert row["run"] == run and row["task"]["id"] == "q1"
    asking(root, pid, "q1", A[5], "running")    # the task is run again: the new run is the latest
    assert _of(_now(root), kind) == []


def test_a_failed_run_set_aside_with_got_it_leaves_the_list_for_that_person_and_comes_back_for_a_new_failure(root, pid):
    """「知道了」 is kept per person on this Mac under the Research OS home, beside the opened store, never in the project."""
    run = asking(root, pid, "q1", A[5], "failed")
    assert now_doing.set_aside(pid, PERSON, run) == {"run": run, "aside": True}
    assert _of(_now(root), "failed") == [] and now_doing.asides(pid, PERSON) == {run}
    kept = now_doing._aside_file(pid, PERSON)
    assert kept.parent == handbacks._store_file(pid, PERSON).parent and kept != handbacks._store_file(pid, PERSON)
    assert not [p for p in root.rglob("*") if p.is_file() and "aside" in p.name]   # nothing in the project
    newer = asking(root, pid, "q1", A[5], "failed")    # the task failed again: a new run asks again
    assert [r["run"] for r in _of(_now(root), "failed")] == [newer]


def test_got_it_is_the_persons_own_and_only_for_a_run_they_started(root, pid):
    mine = asking(root, pid, "q1", A[5], "failed", by=None)     # a run that names nobody: whoever's Mac it is
    theirs = asking(root, pid, "q2", A[6], "failed", by=OTHER)
    assert now_doing.set_aside(pid, PERSON, theirs) == {"run": theirs, "aside": False}     # not a run they started
    assert now_doing.set_aside(pid, PERSON, "r-20260101T000000Z-aaaaaa") == {"run": "r-20260101T000000Z-aaaaaa", "aside": False}
    assert now_doing.set_aside(pid, PERSON, mine)["aside"] is True
    assert _of(_now(root), "failed") == []
    people.write_me("李", OTHER)
    assert sorted(r["run"] for r in _of(_now(root), "failed")) == sorted([mine, theirs])    # their own store: nothing set aside
    with pytest.raises(ValueError):
        now_doing.set_aside(pid, PERSON, "../escape")


def test_a_row_leaves_with_its_task_and_an_undone_run_and_a_finished_task_has_no_failed_row(root, pid):
    run = asking(root, pid, "q1", A[5], "failed")
    asking(root, pid, "q2", A[6], "out_of_bounds", outcome="done", reverted=True)    # undone: dealt with
    assert [r["run"] for r in _of(_now(root), "failed")] == [run] and _of(_now(root), "out_of_bounds") == []
    tasks = workspace.read_tasks(root)[0]
    workspace.save_tasks(root, {"tasks": [t for t in tasks if t["id"] != "q1"], "revision": workspace.read_tasks(root)[1]})
    assert _of(_now(root), "failed") == []   # the task was removed
    again = asking(root, pid, "q3", A[4], "failed")
    tasks = workspace.read_tasks(root)[0]
    workspace.save_tasks(root, {"tasks": [{**t, "done": True} if t["id"] == "q3" else t for t in tasks], "revision": workspace.read_tasks(root)[1]})
    assert again and _of(_now(root), "failed") == []   # a task the person marked done reads 已完成, not 失败


def test_the_rows_take_the_states_the_cards_do(root, pid):
    """One notion of a task's state: a task reads 等你回答, 失败 or 越过了限制 on its card exactly when it has a row."""
    asking(root, pid, "a1", A[5], "needs_input")
    asking(root, pid, "f1", A[5], "failed")
    asking(root, pid, "o1", A[5], "out_of_bounds", outcome="done")
    asking(root, pid, "r1", A[5], "running")
    got = _now(root)
    states = {i["id"]: i["state"] for i in attempt_tasks.by_attempt(root, pid, PERSON, [A[5]])[A[5]]["items"]}
    rows = {r["task"]["id"]: r["kind"] for r in got["needs"]["rows"]}
    assert states == {"a1": "needs_answer", "f1": "failed", "o1": "out_of_bounds", "r1": "running"}
    assert rows == {"a1": "answer", "f1": "failed", "o1": "out_of_bounds"}


def test_a_run_that_was_undone_leaves_the_list_and_its_task_alike_on_the_cards_and_in_all_tasks(root, pid):
    """The rows and the states are one reading (21.1, 21.4): an undone run that asked, failed or went out of bounds has no row,
    and its task no longer reads 等你回答, 失败 or 越过了限制 -- not on the card, not in 全部任务, not in the progress line."""
    asking(root, pid, "a1", A[5], "needs_input", reverted=True)
    asking(root, pid, "f1", A[5], "failed", reverted=True)
    asking(root, pid, "o1", A[5], "out_of_bounds", outcome="done", reverted=True)
    asking(root, pid, "l1", A[5], "failed")     # one that was not undone still reads and lists as before
    got = _now(root)
    items = {i["id"]: i["state"] for i in attempt_tasks.by_attempt(root, pid, PERSON, [A[5]])[A[5]]["items"]}
    assert items == {"a1": "not_started", "f1": "not_started", "o1": "not_started", "l1": "failed"}
    assert {k: v for k, v in got["home"]["tasks"].items() if k in items} == items
    assert {r["task"]["id"]: r["kind"] for r in got["needs"]["rows"]} == {"l1": "failed"}


def test_the_run_store_is_read_once_for_the_cards_and_the_list_of_one_answer(root, pid, monkeypatch):
    """21.4 (fix batch 3): the handbacks that wait, the latest run of each task and the 交回 rows are one reading of the run
    store, for the home's answer, the sidebar's `only=needs` and the desk's -- not four passes over every run.json -- and the
    rows and the states of one answer cannot disagree for a handback opened between two readings."""
    handed(root, pid, "t12", A[5], _at(24), opened=False)
    handed(root, pid, "t13", A[5], _at(23), opened=False)
    asking(root, pid, "q1", A[6], "failed")
    asking(root, pid, "q2", A[6], "needs_input")
    passes: list[int] = []
    real = runs.iter_runs
    monkeypatch.setattr(runs, "iter_runs", lambda *a, **k: (passes.append(1), real(*a, **k))[1])

    def count(read) -> int:
        passes.clear()
        read()
        return len(passes)

    conn = _conn(root)
    try:
        assert count(lambda: _now(root)) == 1
        assert count(lambda: _now(root, only="needs")) == 1
        assert count(lambda: desk.payload(conn, root)) == 1
        assert count(lambda: desk.payload(conn, root, light=True)) == 1
    finally:
        conn.close()
    got = _now(root)
    assert [r["report"][-8:] for r in _of(got, "handback")] and {r["task"]["id"] for r in _of(got, "handback")} == {"t12", "t13"}
    assert [r["kind"] for r in got["needs"]["rows"] if r["kind"] != "handback"] == ["answer", "failed"]
    assert {t: got["home"]["tasks"][t] for t in ("t12", "t13")} == {"t12": "to_read", "t13": "to_read"}


def test_the_rows_are_in_one_order_by_kind_and_newest_first_inside_a_kind(root, pid):
    asking(root, pid, "f1", A[5], "failed", when=_at(26))
    asking(root, pid, "f2", A[6], "failed", when=_at(28))
    asking(root, pid, "a1", A[5], "needs_input", when=_at(20))
    handed(root, pid, "t12", A[5], _at(21), opened=False)
    handed(root, pid, "t14", A[6], _at(22), opened=False)
    asking(root, pid, "o1", A[5], "out_of_bounds", outcome="done", when=_at(10))
    rows = _now(root)["needs"]["rows"]
    assert [(r["kind"], r["task"]["id"]) for r in rows] == [("answer", "a1"), ("handback", "t14"), ("handback", "t12"),
                                                          ("out_of_bounds", "o1"), ("failed", "f2"), ("failed", "f1")]


# -- 等你处理: what 待办 held ---------------------------------------------------------------------------------


def test_what_the_todo_column_held_is_rows_for_one_person_review_judgments_and_a_line_with_a_missing_end(tmp_path):
    """The single researcher's 待办 (the desk fixture of 14.1): one row for #16's judgment to review, one for the judgment
    no attempt holds, one for the line whose end the table lost -- no proposal, no sync, whatever the role."""
    root, (key16, key_other) = _single(tmp_path)
    got = _now(root)
    review = _of(got, "review")
    assert [(r["key"], r["attempt"] and r["attempt"]["number"], r["n"], len(r["items"])) for r in review] == \
        [("r:" + A16, "16", 1, 1), ("r:none", None, 1, 1)]
    assert review[0]["items"][0]["label"] == "依据已变化" and review[0]["attempts"] == ["attempt:map.md#16"]
    assert review[1]["attempts"] == [] and review[1]["items"][0]["src"] == key_other[0]
    (line,) = _of(got, "line_missing")
    assert (line["line"]["child_number"], line["line"]["parent_number"], line["attempts"]) == ("20", "16", ["attempt:map.md#16"])
    assert line["attempt"]["number"] == "16" and line["line"]["officialness"]["state"] == "official"
    assert [r["kind"] for r in got["needs"]["rows"]] == ["review", "review", "line_missing"]
    assert got["needs"]["role"] == "single" and not [r for r in got["needs"]["rows"] if r["kind"] in ("proposal", "mine", "official")]
    english = _now(root, lang="en")
    assert _of(english, "review")[0]["items"][0]["label"] == "The basis changed"


def test_the_pi_has_the_members_proposals_to_decide_and_a_member_has_their_own_and_a_newer_official_version(drive):
    """12.4 / 18.3: the PI's rows are the proposals awaiting approval (newest first, the attempts each touches); the member's
    are their own that wait, a returned one with its reason, and, once the PI has published beyond what they synced, the newer
    official version."""
    verdict, add, remove = _two_people(drive)
    with drive.member():
        mine = _now(drive.work)
        rows = _of(mine, "mine")
        assert {r["proposal"]["id"] for r in rows} == {verdict.id, add.id, remove.id} and not _of(mine, "proposal")
        assert [r["at"] for r in rows] == sorted((r["at"] for r in rows), reverse=True)   # newest first
        row = next(r for r in rows if r["proposal"]["id"] == verdict.id)
        assert (row["attempts"], row["attempt"]["number"], row["key"]) == ([A16], "16", "p:" + verdict.id)
        assert not _of(mine, "official")
    with drive.pi():
        pi = _now(drive.official, lang="en")
        waiting = _of(pi, "proposal")
        assert {r["proposal"]["id"] for r in waiting} == {verdict.id, add.id, remove.id} and not _of(pi, "mine")
        assert pi["role"] == "pi" and pi["needs"]["role"] == "pi"
        assert next(r for r in waiting if r["proposal"]["id"] == remove.id)["attempts"] == [A17, A16]
        proposals.return_(drive.official, add.id, "先别连")
        proposals.approve(drive.official, verdict.id)
        share.publish(drive.official)
    with drive.member():
        later = _now(drive.work)
        by_id = {r["proposal"]["id"]: r for r in _of(later, "mine")}
        assert (by_id[add.id]["proposal"]["state"], by_id[add.id]["proposal"]["reason"]) == ("returned", "先别连")
        (official,) = _of(later, "official")
        assert official["snapshot"]["seq"] >= 1 and official["key"] == "official"


def test_a_shared_folder_that_cannot_be_read_is_a_notice_and_the_rest_of_the_list_is_still_there(drive):
    """The notices are the sentences the desk composed; the list does not wait for the shared folder longer than the desk does."""
    _two_people(drive)
    with drive.member():
        drive.shared.rename(drive.shared.with_name("away"))
        got = _now(drive.work, lang="en")
    assert got["needs"]["notices"] == [desk.SHARED_WORDS["unreadable"][1]] and _of(got, "mine") == []
    assert got["shared"] == {"state": "unreadable"}      # the page tells a read worth repeating (slow, stale) from one that is not


def test_a_shared_folder_too_slow_for_the_wait_is_said_slow_so_that_the_home_can_ask_again(drive, monkeypatch):
    """21.4: the notice alone does not tell the home whether to read again; the answer says the state the desk gives the column,
    with the rows the slow read left out missing, and the next answer (the read has finished) is whole."""
    import time

    verdict, add, _remove = _two_people(drive)
    with drive.pi():
        read = desk._read_shared
        monkeypatch.setattr(desk, "_read_shared", lambda *a: time.sleep(1) or read(*a))
        slow = _now(drive.official, timeout=0.1)
        assert slow["shared"] == {"state": "slow"} and slow["needs"]["notices"] == [desk.SHARED_WORDS["slow"][0]] and _of(slow, "proposal") == []
        monkeypatch.setattr(desk, "_read_shared", read)  # (not undo(): the homes are monkeypatched too)
        while desk._JOBS:
            time.sleep(0.05)
        whole = _now(drive.official)
        assert whole["shared"] == {"state": "ok"} and whole["needs"]["notices"] == [] and len(_of(whole, "proposal")) == 3
    alone = None
    with drive.member():
        alone = _now(drive.work, only="needs")
    assert alone["shared"] == {"state": "ok"} and "progress" not in alone


# -- the endpoint -----------------------------------------------------------------------------------------


@pytest.fixture
def served(root: Path):
    from researchos.webapp import server

    httpd = server.build_server(root, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_the_endpoint_answers_both_parts_or_the_list_alone_in_the_pages_language(root, pid, served):
    from test_webapp_server import _get

    base, _httpd = served
    handed(root, pid, "t12", A[5], _at(24), opened=False)
    asking(root, pid, "q1", A[6], "failed")
    status, got = _get(base, "/api/workspace/now")
    assert status == 200 and got["id"] == pid and got["project"] == str(root) and got["role"] == "single"
    assert _numbers(got) == ["6", "5"] and got["progress"]["holding"] == 2   # #6's failed run (29th) is later than #5's report (24th)
    assert [r["kind"] for r in got["needs"]["rows"]] == ["handback", "failed"]
    status, alone = _get(base, "/api/workspace/now?only=needs")
    assert status == 200 and "progress" not in alone and alone["needs"] == got["needs"]


def test_setting_a_run_aside_is_a_post_that_refuses_a_missing_run_a_wrong_project_and_a_foreign_origin(root, pid, served):
    from test_webapp_server import _get, _post, _request_with_headers

    base, _httpd = served
    run = asking(root, pid, "q1", A[5], "failed")
    assert _post(base, "/api/workspace/run/aside", {"project": str(root), "run": run})[1] == {"run": run, "aside": True}
    assert _get(base, "/api/workspace/now?only=needs")[1]["needs"]["rows"] == []
    assert _post(base, "/api/workspace/run/aside", {"project": str(root)})[0] == 400
    assert _post(base, "/api/workspace/run/aside", {"project": str(root), "run": "../x"})[0] == 400
    assert _post(base, "/api/workspace/run/aside", {"project": "/other", "run": run})[0] == 409
    assert _request_with_headers(base, "POST", "/api/workspace/run/aside", {"Host": base.removeprefix("http://"), "Origin": "https://example.com",
                                                                            "Content-Length": "2"}, b"{}")[0] == 403
    assert dataclasses.is_dataclass(_httpd.get_served())


# -- the researcher's real Quant, after the grouping write (21.9 item 1) -----------------------------------------


def test_quants_eighteen_tasks_after_the_grouping_write_list_sword_v0_2_v0_3_and_v0_newest_activity_first(quant):
    """The acceptance case of 21.9 item 1 with the real grouping (21.1): 18 agent tasks, each with the report it handed back and none
    marked done, are grouped as Sword v0 (tasks 7-11), v0.2 (12, 13, 16, 17, 18) and v0.3 (14), each written as an attempt assessed
    待定. Every task reads 已完成, so no attempt has an unfinished task or an unopened handback -- and all three are listed, because
    they wait for the assessment: the newest report of each says the order (v0.2's 18th task, v0.3's 14th, v0's 11th). The other
    tasks stay in no attempt and put no attempt in progress; nothing waits."""
    from researchos import task_groups

    plan = task_groups.propose(quant, today=TODAY)
    for group_id in ("v:sword v0", "v:sword v0.2", "v:sword v0.3"):
        _confirm(quant, _group(plan, group_id), after=_group(plan, group_id)["after"])
    got = _now(quant)
    assert [(a["title"], a["route_status"]["label"], a["work"]["tasks"], a["work"]["unfinished"], a["work"]["unopened"])
            for a in got["progress"]["attempts"]] == [("Sword v0.2", "待定", 5, 0, 0), ("Sword v0.3", "待定", 1, 0, 0), ("Sword v0", "待定", 5, 0, 0)]
    assert got["progress"]["holding"] == 3
    assert all(item["state"] == "done" for a in got["progress"]["attempts"] for item in a["work"]["items"])
    assert got["progress"]["attempts"][0]["work"]["latest"]["line"].startswith("全成员 Sword v0.2 复算")   # the title of the latest report: the headline is not there
    assert got["needs"]["rows"] == []

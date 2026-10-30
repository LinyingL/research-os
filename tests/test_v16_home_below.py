"""Research OS V16, task B2-2 (DESIGN.md 21.4, 'Below'): the engine's arrangement of what 项目首页 shows under 现在在做什么.

Reports and tasks are grouped by attempt, the attempts newest activity first (the rule 进行中的尝试 is ordered by), 「未归入尝试」
last: `now_doing.home` gives the order, every task's state (the cards' reading, also for the tasks that belong to no attempt)
and the attempt each report belongs to -- the attempt of the task whose report it is, else the first attempt its research
context links. The acceptance case is Quant after the grouping (18 agent tasks, every one handed back a report, Sword v0 /
v0.2 / v0.3) and the thesis project with attempts kept by hand and no task. Every test names its scenario."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from researchos import attempt_tasks, now_doing, workspace
from researchos.agents import runs

from test_v16_now_doing import (A, _at, _now, _of, _with_table, asking, handed, me, pid, root)  # noqa: F401 -- fixtures
from test_v8_phase2 import _project

#: Quant after the grouping (21.9 item 1): Sword v0 holds tasks 7-11, v0.2 12, 13, 16, 17 and 18, v0.3 task 14; the other
#: seven (1-6 and the data-source survey, 15) are not grouped yet.
GROUPED = {4: (7, 8, 9, 10, 11), 5: (12, 13, 16, 17, 18), 6: (14,)}
LOOSE = (1, 2, 3, 4, 5, 6, 15)
#: The titles the researcher wrote, a date and all.
LONG = "Sword v0 审查与回测：本样本内未见 Alpha，收益≈大盘 beta + 动量风格（2026-10-24）"


def retitle(root: Path, report: str, title: str) -> None:
    """The report keeps its time and says `title` in its first heading."""
    stat = (root / report).stat()
    (root / report).write_text(f"# {title}\n\nBody of the report.\n", encoding="utf-8")
    os.utime(root / report, (stat.st_atime, stat.st_mtime))


def quant_eighteen(root: Path, pid: str) -> dict[int, str]:
    """Quant's 18 agent tasks, each handed back (opened, but the last of v0.2): the report of each by task number."""
    reports: dict[int, str] = {}
    for n in LOOSE:
        reports[n] = handed(root, pid, f"t{n}", None, _at(5 + n, 9))
    for number, tasks in GROUPED.items():
        for n in tasks:
            reports[n] = handed(root, pid, f"t{n}", A[number], _at(10 + n, 9), opened=n != 18)
    return reports


def _ids(items: list[dict]) -> list[str]:
    return [item["number"] for item in items]


@pytest.fixture
def quant(root, pid):
    return quant_eighteen(root, pid)


# -- the order ---------------------------------------------------------------------------------------------------------


def test_quant_groups_by_the_three_sword_attempts_newest_activity_first_and_the_ungrouped_tasks_belong_to_none(root, pid, quant):
    """v0.2 (its last report on the 28th), v0.3 (the 24th) and v0 (the 21st): the engine lists the attempts that hold a task
    or a report in the order 进行中的尝试 stands in; the seven tasks no attempt holds are in none of them."""
    got = _now(root)["home"]
    cards = {a["id"]: a for a in _now(root)["progress"]["attempts"]}
    assert got["attempts"] == [A[5], A[6], A[4]]
    assert [a for a in got["attempts"] if a in cards] == [a["id"] for a in _now(root)["progress"]["attempts"]]   # one order
    assert all(report in quant.values() for report in got["reports"])
    assert {report: attempt for report, attempt in got["reports"].items()} == {
        quant[n]: A[number] for number, tasks in GROUPED.items() for n in tasks}
    assert not any(quant[n] in got["reports"] for n in LOOSE)


def test_every_task_has_a_state_also_the_ones_that_belong_to_no_attempt(root, pid, quant):
    """The cards' states are `state_of`'s; the home asks for all 18, not only for the tasks an attempt holds: every one handed
    back and was opened reads done, except task 18, whose handback waits."""
    got = _now(root)["home"]["tasks"]
    assert set(got) == {f"t{n}" for n in range(1, 19)}
    assert {task: state for task, state in got.items() if state != "done"} == {"t18": "to_read"}
    cards = attempt_tasks.by_attempt(root, pid, "zhang@example.com", [A[4], A[5], A[6]])
    assert {item["id"]: item["state"] for card in cards.values() for item in card["items"]}.items() <= got.items()


def test_a_run_that_waits_for_an_answer_failed_or_went_out_of_bounds_is_read_as_the_cards_read_it(root, pid, quant):
    asking(root, pid, "q1", A[5], "needs_input", question="Which sample?", when=_at(30))
    asking(root, pid, "q2", A[6], "failed", when=_at(30))
    asking(root, pid, "q3", A[4], "out_of_bounds", when=_at(30))
    asking(root, pid, "q4", A[4], "running", when=_at(30))
    got = _now(root)["home"]["tasks"]
    assert (got["q1"], got["q2"], got["q3"], got["q4"]) == ("needs_answer", "failed", "out_of_bounds", "running")
    card = {item["id"]: item["state"] for item in attempt_tasks.by_attempt(root, pid, "zhang@example.com", [A[5]])[A[5]]["items"]}
    assert card["q1"] == got["q1"]


def test_an_attempt_of_any_status_that_holds_only_a_report_is_listed_by_the_date_of_its_row(root, pid, quant):
    """#7 is assessed ⚠️ and holds no task, but a report links it in its research context: it stands among the attempts by the
    rule 进行中的尝试 uses (here the date of its row, the 25th: after v0.2's reports of the 28th, before v0.3's of the 24th and
    v0's of the 21st)."""
    report = "research/results/" + "e" * 32 + ".md"
    (root / report).write_text("# Linked by hand\n", encoding="utf-8")
    (root / report.replace(".md", ".context.json")).write_text(json.dumps({"version": 1, "context": {"attempts": [A[7]]}}))
    got = _now(root)["home"]
    assert got["attempts"] == [A[5], A[7], A[6], A[4]] and got["reports"][report] == A[7]


# -- which attempt a report belongs to ---------------------------------------------------------------------------------


def test_a_report_belongs_to_the_attempt_of_the_task_whose_report_it_is_even_when_its_context_links_another(root, pid, quant):
    report = quant[14]
    side = root / report.replace(".md", ".context.json")
    context = json.loads(side.read_text())
    context["context"]["attempts"] = [A[9]]
    side.write_text(json.dumps(context))
    assert _now(root)["home"]["reports"][report] == A[6]


def test_a_report_of_a_task_that_belongs_to_no_attempt_goes_to_the_first_attempt_its_context_links_that_the_table_has(root, pid, quant):
    side = root / quant[2].replace(".md", ".context.json")
    context = json.loads(side.read_text())
    context["context"]["attempts"] = ["attempt:map.md#99", A[8], A[9]]    # #99 is not in the table
    side.write_text(json.dumps(context))
    got = _now(root)["home"]
    assert got["reports"][quant[2]] == A[8] and A[8] in got["attempts"]


def test_a_context_this_version_does_not_know_still_places_its_report_and_a_damaged_one_places_nothing_and_breaks_nothing(root, pid):
    (root / "research" / "results").mkdir(parents=True, exist_ok=True)
    report, damaged = "research/results/" + "a" * 32 + ".md", "research/results/" + "b" * 32 + ".md"
    for name in (report, damaged):
        (root / name).write_text("# By hand\n", encoding="utf-8")
    (root / report.replace(".md", ".context.json")).write_text(json.dumps({"version": 7, "context": {"attempts": [A[9]], "tomorrow": 1}}))
    (root / damaged.replace(".md", ".context.json")).write_text("{ not json")
    got = _now(root)["home"]
    assert got["reports"] == {report: A[9]} and got["attempts"] == [A[9]]


def test_a_report_nobody_links_belongs_to_none(root, pid):
    (root / "research" / "results").mkdir(parents=True, exist_ok=True)
    (root / "research" / "results" / "free.md").write_text("# Free\n", encoding="utf-8")
    assert _now(root)["home"] == {"attempts": [], "tasks": {}, "reports": {}}


# -- the thesis, and the cut-down answer -------------------------------------------------------------------------------


def test_a_thesis_with_attempts_kept_by_hand_and_no_task_has_nothing_to_group(tmp_path):
    root = _project(tmp_path / "thesis")
    rows = "".join(f"| {n} | 10-{n:02d} | Idea {n} | V | r{n} | {'✅' if n % 3 else '🕒'} |\n" for n in range(1, 25))
    _with_table(root, "# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n" + rows)
    assert _now(root)["home"] == {"attempts": [], "tasks": {}, "reports": {}}


def test_the_sidebar_s_count_asks_for_the_list_alone_and_gets_neither_progress_nor_the_arrangement(root, pid, quant):
    got = _now(root, only="needs")
    assert "home" not in got and "progress" not in got and "needs" in got


def test_the_arrangement_reads_the_run_store_once_with_the_cards(root, pid, quant, monkeypatch):
    """`home`, `progress` and `needs` of one answer share one reading of the run store (`shared`)."""
    reads = []
    real = attempt_tasks._latest_runs

    def spy(*args, **kwargs):
        reads.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(attempt_tasks, "_latest_runs", spy)
    _now(root)
    assert len(reads) == 1

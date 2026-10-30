"""Research OS V16, task B1-2 (DESIGN.md 21.1 'Grouping the tasks a project already has'): the engine.

A project whose tasks were made before attempts held them gets groups proposed -- each a new attempt row, prefilled,
with the tasks that would go under it -- and the duplicates among them named. Confirming one group writes the attempt
row through the write the new-attempt form uses and then sets `attempt` on the group's tasks; a refusal leaves the
task file as it was. The fixture is shaped like Quant's tasks: versions of one strategy in the text and in the titles
of the reports, a response task made three times, and a few data-source questions. Every test names its scenario."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path

import pytest

from researchos import addproject, proposals, signing, task_groups, workspace
from researchos import project as project_mod

from test_v8_phase2 import _project, _scan

TODAY = date(2026, 10, 29)
RESPONSE = "对上一份审查报告给出回应，逐条说明改动"
QUANT_TASKS = [
    {"id": "t1", "text": "Sword v0 基线：用全部成员跑一遍回测，输出收益表", "report": "R1"},
    {"id": "t2", "text": "Sword v0.2 全成员复算，核对收益表", "due": "2026-09-01"},
    {"id": "t3", "text": "Sword v0.2 全成员复算，核对夏普比率"},
    {"id": "t4", "text": RESPONSE, "report": "R4"},
    {"id": "t5", "text": RESPONSE, "report": "R5"},
    {"id": "t6", "text": RESPONSE},
    {"id": "t7", "text": "Sword v0.3 新增行业中性约束"},
    {"id": "t8", "text": "调查美股历史数据源：Yahoo Finance 的复权口径"},
    {"id": "t9", "text": "对比美股历史数据源覆盖范围：Stooq 与 Tiingo"},
    {"id": "t10", "text": "查一下 CRSP 的申请流程"},
    {"id": "t11", "text": "整理下一次组会要讨论的问题"},
]


def _report(root: Path, name: str, title: str, day: str) -> str:
    rel = f"research/results/{(name * 32)[:32]}.md"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(f"# {title}\n\nBody\n")
    stamp = time.mktime(date.fromisoformat(day).timetuple()) + 3600  # one o'clock: the same local day anywhere
    os.utime(root / rel, (stamp, stamp))
    return rel


def _legacy(root: Path, tasks: list[dict]) -> None:
    """Tasks as they were written before attempts: the task file as a hand edit or an older Research OS left it."""
    cells = [{"done": False, "owner": "", "due": "", **task} for task in tasks]
    (root / workspace.TASKS_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / workspace.TASKS_PATH).write_text(json.dumps(cells, ensure_ascii=False))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = _project(tmp_path / "proj")
    reports = {"R1": _report(root, "a", "Sword v0 基线回测", "2026-08-02"), "R4": _report(root, "b", "Sword v0.2 审查回应", "2026-09-10"),
               "R5": _report(root, "c", "Sword v0.3 审查回应", "2026-10-05")}
    _legacy(root, [{**task, **({"report": reports[task["report"]]} if "report" in task else {})} for task in QUANT_TASKS])
    return root


def _state(root: Path) -> dict[str, str]:
    """Every file of the project folder by its content, to show that nothing moved."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def _group(plan: dict, group_id: str) -> dict:
    return next(g for g in plan["groups"] if g["id"] == group_id)


def _confirm(root: Path, group: dict, **changes) -> dict:
    body = {"attempt": {k: group["attempt"][k] for k in ("number", "date", "description")}, "tasks": group["tasks"]}
    body.update(changes)
    return task_groups.confirm(root, body)


# -- the proposal ---------------------------------------------------------------------------------------------------


def test_tasks_shaped_like_quants_are_proposed_as_three_versions_and_a_data_source_group_with_the_duplicates_named(root):
    plan = task_groups.propose(root, today=TODAY)
    assert [(g["id"], g["basis"], g["tasks"]) for g in plan["groups"]] == [
        ("v:sword v0", "version", ["t1"]), ("v:sword v0.2", "version", ["t2", "t3", "t4", "t6"]),   # t6 continues the step before: t3's group
        ("v:sword v0.3", "version", ["t5", "t7"]), ("t:美股历史数据源", "term", ["t8", "t9"])]
    # the table is the project's own: its row 16 is dated MM-DD, so the next rows are numbered 17 on and dated the same way
    assert [(g["term"], g["attempt"]) for g in plan["groups"]] == [
        ("Sword v0", {"number": "17", "date": "08-02", "description": "Sword v0", "variables": "", "result": "", "verdict": "🕒"}),
        ("Sword v0.2", {"number": "18", "date": "09-01", "description": "Sword v0.2", "variables": "", "result": "", "verdict": "🕒"}),
        ("Sword v0.3", {"number": "19", "date": "10-05", "description": "Sword v0.3", "variables": "", "result": "", "verdict": "🕒"}),
        # a shared-term group with no report title is named by its earliest task's first line
        ("美股历史数据源", {"number": "20", "date": "10-29", "description": "调查美股历史数据源：Yahoo Finance 的复权口径",
                            "variables": "", "result": "", "verdict": "🕒"})]
    assert all(g["verdict_status"]["status"] == "pending" and g["verdict_status"]["label"] == "待定" for g in plan["groups"])
    assert plan["ungrouped"] == ["t10", "t11"]
    assert plan["duplicates"] == [{"task": "t5", "same_as": "t4", "number": 4}, {"task": "t6", "same_as": "t4", "number": 4}]
    assert plan["table"] is True and plan["tasks_revision"] == workspace.read_tasks(root)[1]
    shown = {t["id"]: t for t in plan["tasks"]}
    assert [t["id"] for t in plan["tasks"]] == [f"t{n}" for n in range(1, 12)] and shown["t9"]["number"] == 9
    assert shown["t4"]["report_title"] == "Sword v0.2 审查回应" and shown["t2"]["report_title"] is None and shown["t1"]["owner_label"] == ""


def test_proposing_reads_only_and_gives_the_same_groups_every_time(root):
    before = _state(root)
    first = task_groups.propose(root, today=TODAY)
    assert _state(root) == before  # no file written, none moved
    assert task_groups.propose(root, today=TODAY) == first


def test_only_tasks_with_no_attempt_are_looked_at_and_a_twin_that_is_in_an_attempt_makes_the_other_a_duplicate(root):
    tasks = workspace.read_tasks(root)[0]
    tasks[0]["attempt"] = "attempt:map.md#16"        # t1 already belongs to an attempt
    tasks[2]["text"] = tasks[0]["text"]               # and t3 says what t1 says
    (root / workspace.TASKS_PATH).write_text(json.dumps(tasks, ensure_ascii=False))
    plan = task_groups.propose(root, today=TODAY)
    assert "t1" not in {t["id"] for t in plan["tasks"]} and all("t1" not in g["tasks"] for g in plan["groups"])
    assert {"task": "t3", "same_as": "t1", "number": 1} in plan["duplicates"]
    assert _group(plan, "v:sword v0")["tasks"] == ["t3", "t6"]  # t3 now reads as Sword v0, wherever t1 is; t6 continues the step before it


@pytest.mark.parametrize("text, title, expected", [
    ("Sword v0.2 全成员复算", None, ("sword v0.2", "Sword v0.2", "Sword")),
    ("sword V0.2 recheck", None, ("sword v0.2", "sword v0.2", "sword")),
    ("Rerun Sword-v1.2.3 now", None, ("sword v1.2.3", "Sword v1.2.3", "Sword")),
    ("Run v0.2 once more", None, None),      # a bare version names no subject, so it makes no version group
    ("the v3 report", None, None),
    ("策略 v2 复算", None, None),
    ("四模块量化系统 v0 框架与开发指引", None, None),
    ("v0 first, then Sword v0.2", None, ("sword v0.2", "Sword v0.2", "Sword")),        # a bare token is passed over for a later one
    ("没有版本号", "Sword 构建指引：已有研究梳理与 v0 路线", None),                       # Sword is in the title, but not right before v0
    ("没有版本号", "四模块量化系统 v0 框架 / Sword v1 复算", ("sword v1", "Sword v1", "Sword")),
    ("对上一份审查报告给出回应", "Sword v0.3 审查回应", ("sword v0.3", "Sword v0.3", "Sword")),
    ("Sword v0.2 in the text", "Sword v0.3 in the title", ("sword v0.2", "Sword v0.2", "Sword")),  # the text wins
    ("rev2 and dev3 and v", None, None),
    ("version 2.0 of v0x", None, None),
    ("没有版本号的任务", "也没有", None),
])
def test_a_tasks_version_is_the_first_token_of_its_text_else_of_its_report_with_the_latin_word_before_it(text, title, expected):
    assert task_groups._version_of(text, title) == expected


def test_a_version_is_one_attempt_even_with_one_task_and_the_subject_is_compared_without_regard_to_case(root):
    _legacy(root, [{"id": "a", "text": "Sword v0.2 first"}, {"id": "b", "text": "SWORD V0.2 second"}, {"id": "c", "text": "Other v0.2 third"},
                   {"id": "d", "text": "v0.2 with no subject"}, {"id": "e", "text": "Sword v9 alone"}])
    plan = task_groups.propose(root, today=TODAY)
    assert sorted((g["term"], g["tasks"]) for g in plan["groups"]) == [("Other v0.2", ["c"]), ("Sword v0.2", ["a", "b"]), ("Sword v9", ["e"])]
    assert plan["ungrouped"] == ["d"]     # a version with no word before it makes no group


def test_a_version_only_a_report_title_names_still_groups_its_task_and_the_name_is_the_version_alone(root):
    plan = task_groups.propose(root, today=TODAY)
    v02 = _group(plan, "v:sword v0.2")
    assert "t4" in v02["tasks"]  # its text names no version; its report is titled Sword v0.2
    assert v02["attempt"]["description"] == "Sword v0.2"  # the version alone, no words of any task glued on
    assert _group(plan, "v:sword v0.3")["attempt"]["description"] == "Sword v0.3"


def test_the_same_words_twice_are_one_text_for_a_shared_term_and_a_term_in_most_texts_goes_first(root):
    _legacy(root, [{"id": "a", "text": "回测框架重构"}, {"id": "b", "text": "回测框架重构"}, {"id": "c", "text": "回测框架重构"},
                   {"id": "d", "text": "估计因子暴露矩阵"}, {"id": "e", "text": "检查因子暴露矩阵的符号"},
                   {"id": "f", "text": "因子暴露矩阵与回测框架的接口"}])
    plan = task_groups.propose(root, today=TODAY)
    # three identical tasks are one text: they share nothing with themselves. 因子暴露矩阵 is in three texts and 回测框架 in two (a,
    # and f, which has both): the term in more texts goes first, and f has left the pool when 回测框架 would have taken it
    assert [(g["term"], g["tasks"]) for g in plan["groups"]] == [("因子暴露矩阵", ["d", "e", "f"])] and plan["ungrouped"] == ["a", "b", "c"]
    assert [d["task"] for d in plan["duplicates"]] == ["b", "c"] and all(d["same_as"] == "a" and d["number"] == 1 for d in plan["duplicates"])


def test_three_identical_tasks_alone_are_no_group_but_are_still_duplicates(root):
    _legacy(root, [{"id": "a", "text": RESPONSE}, {"id": "b", "text": RESPONSE}, {"id": "c", "text": RESPONSE}])
    plan = task_groups.propose(root, today=TODAY)
    assert plan["groups"] == [] and plan["ungrouped"] == ["a", "b", "c"]
    assert [(d["task"], d["same_as"], d["number"]) for d in plan["duplicates"]] == [("b", "a", 1), ("c", "a", 1)]


def test_a_term_that_most_of_the_tasks_share_is_the_projects_own_name_and_groups_nothing(root):
    own = [{"id": f"o{n}", "text": f"Quantlab 第 {n} 项：{w}"} for n, w in enumerate(["清洗行情", "估计波动", "绘制净值", "导出明细", "核对口径"], 1)]
    pair = [{"id": "p1", "text": "Quantlab 复盘周报整理"}, {"id": "p2", "text": "写出复盘周报初稿"}, {"id": "p3", "text": "Quantlab 收尾"}]
    _legacy(root, own + pair)
    plan = task_groups.propose(root, today=TODAY)
    assert all("quantlab" not in g["id"] for g in plan["groups"])  # in 7 of 8 texts: it tells nothing
    assert [g["tasks"] for g in plan["groups"]] == [["p1", "p2"]] and plan["groups"][0]["term"] == "复盘周报"


def test_a_shared_term_comes_from_a_report_title_too_and_function_characters_do_not_start_or_end_one(root):
    first, second = _report(root, "d", "供应商调研记录", "2026-07-01"), _report(root, "e", "供应商调研结论", "2026-07-02")
    _legacy(root, [{"id": "a", "text": "请你整理", "report": first}, {"id": "b", "text": "写出建议", "report": second},
                   {"id": "c", "text": "对上一份报告回应"}, {"id": "d", "text": "对上一份材料回应"}])
    plan = task_groups.propose(root, today=TODAY)
    assert [g["tasks"] for g in plan["groups"]] == [["a", "b"]] and plan["groups"][0]["attempt"]["description"].startswith("供应商调研")
    for term in task_groups._terms("对上一份报告的回应了"):
        assert term[0] not in task_groups._NOT_A_START and term[-1] not in task_groups._NOT_AN_END


# -- the attempt each group carries ---------------------------------------------------------------------------------


def test_the_date_is_the_earliest_of_the_tasks_dates_and_the_days_their_reports_were_written_else_today(root):
    plan = task_groups.propose(root, today=TODAY)
    # v0.2: t2 is due 2026-09-01, t4's report was written 09-10; v0.3: only a report (10-05); the data sources: none, today
    assert {g["id"]: g["attempt"]["date"] for g in plan["groups"]} == {
        "v:sword v0": "08-02", "v:sword v0.2": "09-01", "v:sword v0.3": "10-05", "t:美股历史数据源": "10-29"}
    tasks = workspace.read_tasks(root)[0]
    tasks[1]["due"] = "2026-09-20"  # the report is earlier than the only date a task has
    (root / workspace.TASKS_PATH).write_text(json.dumps(tasks, ensure_ascii=False))
    assert _group(task_groups.propose(root, today=TODAY), "v:sword v0.2")["attempt"]["date"] == "09-10"


def test_groups_are_numbered_after_the_greatest_number_in_the_table_in_the_order_of_their_dates_and_dated_as_its_rows_are(root):
    (root / "map.md").write_text(
        "# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n"
        "| 3 | 2026-07-26 | First | V | r | 🕒 |\n| 14a | 2026-07-27 | Second | V | r | ✅ |\n")
    _scan(root)
    plan = task_groups.propose(root, today=TODAY)
    assert [(g["id"], g["attempt"]["number"], g["attempt"]["date"]) for g in plan["groups"]] == [
        ("v:sword v0", "15", "2026-08-02"), ("v:sword v0.2", "16", "2026-09-01"), ("v:sword v0.3", "17", "2026-10-05"),
        ("t:美股历史数据源", "18", "2026-10-29")]  # "14a" counts as 14, and full dates stay full
    assert plan["numbers"] == ["3", "14a"]    # the rows as written: the page keeps a group's number while the table does not hold it
    (root / "map.md").write_text("# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n")
    _scan(root)
    empty = task_groups.propose(root, today=TODAY)
    assert [g["attempt"]["number"] for g in empty["groups"]] == ["1", "2", "3", "4"] and empty["numbers"] == []


def test_the_assessment_is_the_projects_own_marker_for_pending(root):
    assert task_groups.pending_marker(root) == "🕒"
    config = root / ".researchos" / "attempts.toml"
    config.write_text(config.read_text() + '[verdict_labels]\n"🕒" = "进行中"\n"🔜" = "待定"\n')
    assert task_groups.pending_marker(root) == "🔜"
    assert {g["attempt"]["verdict"] for g in task_groups.propose(root, today=TODAY)["groups"]} == {"🔜"}
    config.write_text(config.read_text().replace('"🔜" = "待定"\n', ""))
    assert task_groups.pending_marker(root) == "🕒"  # no marker named 待定: the default one is written


def test_a_project_with_no_attempt_table_still_gets_groups_numbered_from_one_and_says_it_has_no_table(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    project_mod.init_project(root)
    _legacy(root, [{"id": "a", "text": "Sword v0.2 one"}, {"id": "b", "text": "Sword v0.3 two"}])
    plan = task_groups.propose(root, today=TODAY)
    assert plan["table"] is False
    assert [(g["attempt"]["number"], g["attempt"]["date"]) for g in plan["groups"]] == [("1", "2026-10-29"), ("2", "2026-10-29")]
    assert plan["numbers"] == []


def test_no_tasks_and_no_task_file_propose_nothing_and_an_unreadable_task_file_is_refused(root):
    (root / workspace.TASKS_PATH).unlink()
    assert task_groups.propose(root, today=TODAY) == {"tasks_revision": None, "table": True, "tasks": [], "groups": [], "ungrouped": [],
                                                      "duplicates": [], "numbers": ["16"]}   # the table's own rows are listed even when there is nothing to propose
    (root / workspace.TASKS_PATH).write_text("not json")
    with pytest.raises(workspace.WorkspaceError) as refused:
        task_groups.propose(root, today=TODAY)
    assert refused.value.code == "unreadable"


# -- confirming one group -------------------------------------------------------------------------------------------


def test_confirming_a_group_writes_its_row_through_the_attempt_write_then_sets_attempt_on_its_tasks_and_moves_nothing(root):
    plan = task_groups.propose(root, today=TODAY)
    group = _group(plan, "v:sword v0.2")
    group["attempt"]["number"], group["attempt"]["description"] = "18", "Sword v0.2 全成员复算"  # renamed by the person
    before = _state(root)
    done = _confirm(root, group)
    node = "attempt:map.md#18"
    assert done["attempt"] == {"id": node, "number": "18", "label": "Sword v0.2 全成员复算", "file": "map.md", "date": "09-01"}
    assert done["moved"] == ["t2", "t3", "t4", "t6"] and done["linked"] is True and done["ingest_error"] is None and done["mode"] == "single"
    assert "| 18 | 09-01 | Sword v0.2 全成员复算 |  |  | 🕒 |" in (root / "map.md").read_text()
    tasks = workspace.read_tasks(root)[0]
    assert {t["id"]: t.get("attempt") for t in tasks} == {"t2": node, "t3": node, "t4": node, "t6": node} | {
        f"t{n}": None for n in (1, 5, 7, 8, 9, 10, 11)}
    assert done["tasks"] == tasks and done["tasks_revision"] == workspace.read_tasks(root)[1]
    assert node in {row["id"] for row in workspace._attempts(root)}  # the index has it: the row was read back
    assert done["backup"] and (root / done["backup"]).is_file()
    after = _state(root)
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    assert {"map.md", workspace.TASKS_PATH} <= changed
    assert not [p for p in changed if p.startswith(("research/results/", "steps/", "data/", "figs/"))]  # no file moved or was touched
    assert set(before) - set(after) == set()  # and none was removed
    # asked again, the plan no longer offers those tasks, and what is left keeps its number
    again = task_groups.propose(root, today=TODAY)
    assert [(g["id"], g["attempt"]["number"]) for g in again["groups"]] == [("v:sword v0", "19"), ("v:sword v0.3", "20"), ("t:美股历史数据源", "21")]
    assert all(t not in {x["id"] for x in again["tasks"]} for t in ("t2", "t3", "t4", "t6"))


def test_a_task_save_that_follows_keeps_the_other_fields_of_every_task_as_they_were(root):
    tasks = workspace.read_tasks(root)[0]
    tasks[1].update(owner="Zhang San", done=True)
    (root / workspace.TASKS_PATH).write_text(json.dumps(tasks, ensure_ascii=False))
    group = _group(task_groups.propose(root, today=TODAY), "v:sword v0.2")
    _confirm(root, group)
    after = {t["id"]: t for t in workspace.read_tasks(root)[0]}
    assert after["t2"] == {**tasks[1], "attempt": "attempt:map.md#18"} and after["t5"] == tasks[4]


@pytest.mark.parametrize("changes, code, said", [
    ({"attempt": {"number": "16", "date": "09-01", "description": "Clash"}}, "group_duplicate", "编号"),
    ({"attempt": {"number": "18", "date": "09-01", "description": "  "}}, "invalid", "描述"),
    ({"attempt": {"number": "", "date": "09-01", "description": "No number"}}, "invalid", "编号"),
    ({"tasks": []}, "invalid", "至少一个任务"),
    ({"tasks": ["t2", "nope"]}, "group_task_gone", "不在任务列表"),
    ({"tasks": ["t2", "t3"], "tasks_revision": "0" * 64}, "conflict", "又改过"),
    ({"extra": 1}, "invalid", "不完整"),
])
def test_a_refused_group_changes_neither_the_attempt_table_nor_the_task_file(root, changes, code, said):
    group = _group(task_groups.propose(root, today=TODAY), "v:sword v0.2")
    before = _state(root)
    with pytest.raises(workspace.WorkspaceError) as refused:
        _confirm(root, group, **changes)
    assert refused.value.code == code and said in refused.value.message_zh and refused.value.message_zh.endswith(("。", "改。"))
    assert str(refused.value) and not any("一" <= ch <= "鿿" for ch in str(refused.value).replace("“", ""))  # the English is English
    assert _state(root) == before


def test_a_task_that_already_belongs_to_an_attempt_refuses_the_group_before_the_row_is_written(root):
    group = _group(task_groups.propose(root, today=TODAY), "v:sword v0.2")
    tasks = workspace.read_tasks(root)[0]
    tasks[2]["attempt"] = "attempt:map.md#16"
    (root / workspace.TASKS_PATH).write_text(json.dumps(tasks, ensure_ascii=False))
    before = _state(root)
    with pytest.raises(workspace.WorkspaceError) as refused:
        _confirm(root, group)
    assert refused.value.code == "group_task_taken" and refused.value.status == 409 and "已经属于" in refused.value.message_zh
    assert _state(root) == before


def test_a_group_confirmed_with_the_revision_it_was_proposed_on_goes_through_and_without_one_a_save_in_between_is_no_conflict(root):
    plan = task_groups.propose(root, today=TODAY)
    group = _group(plan, "v:sword v0")
    tasks = workspace.read_tasks(root)[0]
    tasks[9]["done"] = True  # a run finished and marked another task meanwhile
    (root / workspace.TASKS_PATH).write_text(json.dumps(tasks, ensure_ascii=False))
    done = _confirm(root, group)  # no revision sent: the engine applies the group to the list as it is now
    assert done["linked"] and {t["id"]: t["done"] for t in done["tasks"]}["t10"] is True
    other = _group(task_groups.propose(root, today=TODAY), "v:sword v0.3")
    assert _confirm(root, other, tasks_revision=workspace.read_tasks(root)[1])["linked"] is True


def test_a_project_with_no_attempt_table_refuses_the_group_and_nothing_is_written(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    project_mod.init_project(root)
    _legacy(root, [{"id": "a", "text": "Sword v0.2 one"}])
    group = task_groups.propose(root, today=TODAY)["groups"][0]
    before = _state(root)
    with pytest.raises(workspace.WorkspaceError) as refused:
        _confirm(root, group)
    assert refused.value.code == "group_table_missing" and "找不到尝试表" in refused.value.message_zh
    assert _state(root) == before


def test_someone_who_may_not_write_in_the_project_is_refused_before_anything_is_checked(root, monkeypatch):
    group = _group(task_groups.propose(root, today=TODAY), "v:sword v0")
    before = _state(root)
    monkeypatch.setattr(proposals, "role_of", lambda _root: ("none", None, None))
    with pytest.raises(workspace.WorkspaceError) as refused:
        _confirm(root, group)
    assert refused.value.code == "read_only" and refused.value.status == 403
    monkeypatch.undo()
    assert _state(root) == before


def test_an_attempt_row_written_but_not_read_back_leaves_the_tasks_where_they_are_and_says_so(root, monkeypatch):
    group = _group(task_groups.propose(root, today=TODAY), "v:sword v0")
    real = proposals.write_directly
    monkeypatch.setattr(proposals, "write_directly", lambda *a, **k: {**real(*a, **k), "ingest_error": "disk is full"})
    before_tasks = (root / workspace.TASKS_PATH).read_bytes()
    done = _confirm(root, group)
    assert done["linked"] is False and done["moved"] == [] and done["ingest_error"] == "disk is full"
    assert "| 17 |" in (root / "map.md").read_text() and (root / workspace.TASKS_PATH).read_bytes() == before_tasks


def test_tasks_that_cannot_be_moved_after_the_row_was_written_say_which_row_exists_and_how_to_finish(root, monkeypatch):
    group = _group(task_groups.propose(root, today=TODAY), "v:sword v0")
    boom = workspace.WorkspaceError("write_failed", "disk is full", 409, "无法写入文件")
    monkeypatch.setattr(workspace, "save_tasks", lambda *a, **k: (_ for _ in ()).throw(boom))
    before_tasks = (root / workspace.TASKS_PATH).read_bytes()
    with pytest.raises(workspace.WorkspaceError) as refused:
        _confirm(root, group)
    assert refused.value.code == "group_not_linked" and refused.value.status == 409
    assert "#17" in refused.value.message_zh and "属于哪条尝试" in refused.value.message_zh and "attempt row 17" in str(refused.value)
    assert "写进尝试表" in refused.value.message_zh and "年表" not in refused.value.message_zh      # 21.6: the table is 尝试表
    assert "| 17 |" in (root / "map.md").read_text() and (root / workspace.TASKS_PATH).read_bytes() == before_tasks


def test_the_row_a_group_writes_reads_back_as_a_pending_attempt_with_no_result(root):
    from researchos.ingest import attempts as attempts_ingest

    group = _group(task_groups.propose(root, today=TODAY), "t:美股历史数据源")
    _confirm(root, group)
    rows = attempts_ingest.parse_attempts_table((root / "map.md").read_text(), "Attempt timeline", attempts_ingest.load_config(root).columns)
    row = next(r for r in rows if r.number == "20")
    assert (row.description, row.date, row.result) == ("调查美股历史数据源：Yahoo Finance 的复权口径", "10-29", "") and "🕒" in row.verdict


def test_a_name_with_a_pipe_or_a_line_break_is_written_as_one_cell_and_reads_back_the_same(root):
    from researchos.ingest import attempts as attempts_ingest

    group = _group(task_groups.propose(root, today=TODAY), "v:sword v0")
    _confirm(root, group, attempt={"number": "17", "date": "08-02", "description": "A | B\nC  D"})
    rows = attempts_ingest.parse_attempts_table((root / "map.md").read_text(), "Attempt timeline", attempts_ingest.load_config(root).columns)
    assert next(r for r in rows if r.number == "17").description == "A | B C D"
    assert "| A \\| B C D |" in (root / "map.md").read_text()


# -- a member's group ----------------------------------------------------------------------------------------------

needs_ssh = pytest.mark.skipif(signing.tool() is None, reason="ssh-keygen is not installed here")


@needs_ssh
def test_a_members_group_is_written_into_the_working_copy_and_proposing_the_attempt_carries_its_tasks(tmp_path, monkeypatch):
    from test_v8_phase4 import Drive

    monkeypatch.setattr(addproject, "_top_level_folders", lambda: set())
    drive = Drive(tmp_path, monkeypatch)
    with drive.member():
        project_mod.open_project(drive.work)
        _scan(drive.work)
        _legacy(drive.work, [{"id": "a", "text": "Sword v0.2 first check"}, {"id": "b", "text": "Sword v0.2 second check"}])
        group = task_groups.propose(drive.work, today=TODAY)["groups"][0]
        done = _confirm(drive.work, group)
        assert done["mode"] == "member" and done["linked"] and done["attempt"]["number"] == "17"
        assert "| 17 |" in (drive.work / "map.md").read_text()
        assert {t["id"]: t["attempt"] for t in workspace.read_tasks(drive.work)[0]} == {"a": done["attempt"]["id"], "b": done["attempt"]["id"]}
        draft = proposals.make_proposal(drive.work, "attempt_new", "Put the first tasks under Sword v0.2", number="17")
        assert workspace.TASKS_PATH in {item["path"] for item in draft.data["file"]}  # the tasks travel with the attempt
    with drive.pi():
        assert not (drive.official / workspace.TASKS_PATH).exists()  # nothing reached the official version


# -- over HTTP --------------------------------------------------------------------------------------------------------


@pytest.fixture
def live(root):
    import threading

    from researchos.webapp import server

    httpd = server.build_server(root, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", root, httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _asked(base: str, method: str, path: str, body: dict | None = None, lang: str = "zh"):
    from test_webapp_server import _request_with_headers

    raw = None if body is None else json.dumps(body).encode()
    headers = {"Host": base.removeprefix("http://"), "X-ResearchOS-Lang": lang}
    if raw is not None:
        headers.update({"Content-Type": "application/json", "Content-Length": str(len(raw))})
    return _request_with_headers(base, method, path, headers, raw)


def test_the_page_reads_the_proposal_and_confirms_a_group_over_http_and_the_other_pages_see_the_attempt(live):
    base, root, _httpd = live
    status, plan = _asked(base, "GET", "/api/workspace/task-groups")
    assert status == 200 and plan["project"] == str(root) and plan["id"]
    assert [g["id"] for g in plan["groups"]] == ["v:sword v0", "v:sword v0.2", "v:sword v0.3", "t:美股历史数据源"]
    group = plan["groups"][1]
    before = _asked(base, "GET", "/api/generation")[1]["generation"]
    status, done = _asked(base, "POST", "/api/workspace/task-groups", {
        "project": str(root), "tasks": group["tasks"], "after": group["after"],
        "attempt": {k: group["attempt"][k] for k in ("number", "date", "description")}})
    assert status == 200, done
    assert group["after"] == {"t6": "t3"}
    assert done["attempt"]["id"] == "attempt:map.md#18" and done["moved"] == ["t2", "t3", "t4", "t6"] and done["linked"] is True
    assert done["tasks_officialness"] and done["generation"] > before
    home = _asked(base, "GET", "/api/workspace")[1]
    assert {t["id"] for t in home["tasks"] if t.get("attempt") == "attempt:map.md#18"} == {"t2", "t3", "t4", "t6"}
    assert {t["id"]: t["after"] for t in home["tasks"] if t.get("after")} == {"t6": "t3"}
    assert "attempt:map.md#18" in {a["id"] for a in home["attempts"]}
    rows = _asked(base, "GET", "/api/attempts")[1]["attempts"]
    assert "18" in {a["attrs"]["number"] for a in rows}


def test_a_group_refused_over_http_says_why_in_the_pages_language_and_changes_nothing(live):
    base, root, httpd = live
    group = _asked(base, "GET", "/api/workspace/task-groups")[1]["groups"][0]
    before = _state(root)
    body = {"project": str(root), "tasks": group["tasks"], "attempt": {"number": "16", "date": "08-02", "description": "Clash"}}
    said = {}
    for lang in ("zh", "en"):
        status, error = _asked(base, "POST", "/api/workspace/task-groups", body, lang)
        assert status == 400 and error["state"] == "workspace_group_duplicate", error
        said[lang] = error["message"]
    assert "编号" in said["zh"] and "什么都没有改" in said["zh"] and "number" in said["en"] and "nothing was changed" in said["en"]
    assert _state(root) == before
    # another project's page, another origin, a read-only open: nothing is written either
    status, error = _asked(base, "POST", "/api/workspace/task-groups", {**body, "project": str(root.parent / "elsewhere")})
    assert status == 409 and error["state"] == "workspace_project_changed"
    import dataclasses

    from test_webapp_server import _request_with_headers

    assert _request_with_headers(base, "POST", "/api/workspace/task-groups", {
        "Host": base.removeprefix("http://"), "Origin": "https://example.com", "Content-Length": "2"}, b"{}")[0] == 403
    httpd.set_served(dataclasses.replace(httpd.get_served(), read_only=True))
    status, error = _asked(base, "POST", "/api/workspace/task-groups", {**body, "attempt": {**body["attempt"], "number": "30"}})
    assert status == 409 and error["state"] == "read_only"
    assert _state(root) == before


def test_a_project_with_no_attempt_table_is_told_so_by_the_proposal_and_the_first_confirmation_after_setup_works(tmp_path):
    import threading

    from researchos.webapp import server
    from test_webapp_server import _get, _post

    root = tmp_path / "plain"
    root.mkdir()
    project_mod.init_project(root)
    _legacy(root, [{"id": "a", "text": "Sword v0.2 one"}, {"id": "b", "text": "Sword v0.2 two"}])
    httpd = server.build_server(root, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        plan = _get(base, "/api/workspace/task-groups")[1]
        assert plan["table"] is False and plan["groups"][0]["attempt"]["number"] == "1"
        body = {"project": str(root), "tasks": ["a", "b"], "attempt": {k: plan["groups"][0]["attempt"][k] for k in ("number", "date", "description")}}
        status, error = _post(base, "/api/workspace/task-groups", body)
        assert status == 400 and error["state"] == "workspace_group_table_missing"
        assert _post(base, "/api/attempts/initialize", {"project": str(root)})[0] == 200
        status, done = _post(base, "/api/workspace/task-groups", body)
        assert status == 200 and done["attempt"]["id"] == "attempt:research/attempts.md#1" and done["linked"] is True
        assert {t["attempt"] for t in workspace.read_tasks(root)[0]} == {"attempt:research/attempts.md#1"}
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_a_task_whose_report_is_gone_or_cannot_be_read_is_still_grouped_by_its_text(root):
    tasks = workspace.read_tasks(root)[0]
    tasks[3]["report"] = "research/results/" + "f" * 32 + ".md"  # t4: a report that is not there
    (root / tasks[4]["report"]).write_bytes(b"\xff\xfe not text \x00")  # t5: one that is no text
    (root / workspace.TASKS_PATH).write_text(json.dumps(tasks, ensure_ascii=False))
    plan = task_groups.propose(root, today=TODAY)
    shown = {t["id"]: t for t in plan["tasks"]}
    assert shown["t4"]["report_title"] is None and shown["t5"]["report_title"] is None
    # their versions were only in the titles of those reports: with no title they read as tasks of no version -- but they say
    # they answer the step before, so they (and t6, who says the same) go with the nearest group before them, Sword v0.2's
    assert _group(plan, "v:sword v0.2")["tasks"] == ["t2", "t3", "t4", "t5", "t6"] and _group(plan, "v:sword v0.3")["tasks"] == ["t7"]
    assert {"t4", "t5"}.isdisjoint(plan["ungrouped"])


def test_a_task_whose_attempt_row_has_gone_is_proposed_for_grouping_like_one_with_none_and_one_in_the_table_is_not(root):
    tasks = workspace.read_tasks(root)[0]
    tasks[0]["attempt"] = "attempt:map.md#99"      # a row renumbered or removed by hand: the task names an attempt the table does not have
    tasks[1]["attempt"] = "attempt:map.md#16"      # and one that it has
    (root / workspace.TASKS_PATH).write_text(json.dumps(tasks, ensure_ascii=False))
    plan = task_groups.propose(root, today=TODAY)
    assert "t1" in {t["id"] for t in plan["tasks"]} and "t2" not in {t["id"] for t in plan["tasks"]}
    group = _group(plan, "v:sword v0")
    assert group["tasks"] == ["t1"]
    done = _confirm(root, group)                     # and it is put under the new attempt, not refused as one that already has an attempt
    assert done["linked"] is True and {t["id"]: t.get("attempt") for t in done["tasks"]}["t1"] == done["attempt"]["id"]
    assert {t["id"]: t.get("attempt") for t in done["tasks"]}["t2"] == "attempt:map.md#16"    # the other is where it was


def test_a_taken_number_and_a_missing_table_are_said_with_the_attempt_table_not_the_retired_word(root, tmp_path):
    group = _group(task_groups.propose(root, today=TODAY), "v:sword v0")
    with pytest.raises(workspace.WorkspaceError) as taken:
        _confirm(root, group, attempt={"number": "16", "date": group["attempt"]["date"], "description": "Again"})
    assert taken.value.code == "group_duplicate" and "尝试表" in taken.value.message_zh and "年表" not in taken.value.message_zh
    assert all("年表" not in sentence for sentence in task_groups._ATTEMPT_WORDS.values())    # 21.6 renames it; no screen uses the old word


# -- the tasks of the researcher's real Quant project (acceptance of V16, round AF-2) ---------------------------------

SONNET, DEEPSEEK, OPUS, CODEX = ("agent:claude-code:sonnet", "agent:deepseek-harness:deepseek-official/deepseek-v4-pro",
                                 "agent:claude-code:opus", "agent:codex:gpt-5")
ASKED = "对上一份审查报告给出回应，以基金经理的角度，因子选择是否正确？"
# (text, title of the report, owner): Quant's 18 tasks in list order, each with its own report and not marked done
QUANT_18 = [
    ("根据目前的思路给出初步的方向和框架，为下一步Coding Agent提供指导", "四模块量化系统 v0 框架与开发指引", SONNET),
    ("根据初步的框架设计量化算法，哪些因子应当被考虑？", "四模块候选因子设计", SONNET),
    ("研究Openstock（https://github.com/example/openstock），哪些因子可以使用？可以进行回测吗？", "OpenStock 因子与回测能力核查", SONNET),
    ("目前缺少历史因子数据，寻找可用美股的数据来源用于回测，主要目标标普500，需要覆盖历史成分股", "标普500历史因子数据源评估", SONNET),
    ("量化算法主要目标标普500，搜索文献，梳理已有的因子与框架", "标普500四模块文献综述：9 类框架与 48 个因子族", SONNET),
    ("梳理总结目前已有的研究结果，为下一步构建最强的Sword提供指引", "Sword 构建指引：已有研究梳理与 v0 路线", SONNET),
    ("根据之前的指引，构建Sword模块的算法", "Sword v0 算法实现与测试", SONNET),
    ("审查代码是否符合目的意图，并做回测", "Sword v0 审查与回测：本样本未见 Alpha", SONNET),
    (ASKED, "基金经理对 Sword 审查的回应", DEEPSEEK),
    (ASKED, "基金经理第二意见：Sword 缺的是风险中性化", OPUS),
    (ASKED, "基金经理审查回应与核查", CODEX),
    ("执行r-20261024T113851Z-e4ca6d的改进", "Sword v0.2 改进实现与回测", SONNET),
    ("审查代码改进是否符合意见，并回测", "Sword v0.2 审查：意见部分落实，仍未见 Alpha", SONNET),
    ("根据最新的Sword v0.3 实施方向提供方案", "Sword v0.3 实施方案与跑输大盘诊断", SONNET),
    ("根据目前的情况，我判断需要更好的数据用于回测，请调研免费与低价的数据源", "免费与 10 欧以内回测数据源实测评估", SONNET),
    ("先走 A，在含退市股的数据上复现 Sword v0.2，看 +1.25%/年 的股票池偏差还剩多少；然后补 Tiingo 数据", "含退市股后池偏差 +1.25 → +0.71，Tiingo 下载进行中", SONNET),
    ("检查上一步是否完成，以及Quant Connect连接，作为研究助理汇报", "上一步核对与 QuantConnect 连接状态", SONNET),
    ("完成未完成的任务", "全成员 Sword v0.2 复算与数据层刷新", SONNET),
]


@pytest.fixture
def quant(tmp_path: Path) -> Path:
    root = _project(tmp_path / "quant")
    rows = []
    for n, (text, title, owner) in enumerate(QUANT_18, 1):
        rel = _report(root, f"{n:02x}", title, f"2026-10-{n:02d}")
        rows.append({"id": f"q{n}", "text": text, "owner": owner, "report": rel})
    _legacy(root, rows)
    return root


def test_quants_eighteen_tasks_make_sword_v0_v0_2_and_v0_3_with_the_nine_to_eleven_kept_as_one_question_and_no_broken_names(quant):
    plan = task_groups.propose(quant, today=TODAY)
    numbered = {t["id"]: t["number"] for t in plan["tasks"]}
    groups = {g["id"]: g for g in plan["groups"]}

    def nums(group):
        return [numbered[tid] for tid in group["tasks"]]
    assert nums(groups["v:sword v0"]) == [7, 8, 9, 10, 11] and nums(groups["v:sword v0.2"]) == [12, 13, 16, 17, 18]
    assert nums(groups["v:sword v0.3"]) == [14]
    # a version group is named by the version alone, never with a piece of a task's text
    assert [(g["term"], g["attempt"]["description"]) for g in plan["groups"] if g["basis"] == "version"] == [
        ("Sword v0", "Sword v0"), ("Sword v0.2", "Sword v0.2"), ("Sword v0.3", "Sword v0.3")]
    # 9-11 and 17 continue the step before: confirming the group sets their `after`
    assert groups["v:sword v0"]["after"] == {"q9": "q8", "q10": "q8", "q11": "q8"} and groups["v:sword v0.2"]["after"] == {"q17": "q16"}
    assert all(not g["after"] for g in plan["groups"] if g["basis"] == "term")
    # the same question given to three agents, each with its own report: not duplicates, shown with the agents' names
    assert plan["duplicates"] == []
    asked = ["Claude Code · opus", "Codex · gpt-5", "DeepSeek Harness · deepseek-official/deepseek-v4-pro"]
    shown = {t["id"]: t for t in plan["tasks"]}
    assert all(sorted(shown[f"q{n}"]["asked_of"]) == sorted(asked) for n in (9, 10, 11))
    assert all(shown[f"q{n}"]["asked_of"] == [] for n in range(1, 9)) and shown["q17"]["asked_of"] == []
    # the rest: in shared-term groups named by a report title, or in none; none is named by a fragment of a sentence
    rest = [g for g in plan["groups"] if g["basis"] == "term"]
    titles = {title for _text, title, _owner in QUANT_18}
    firsts = {text for text, _title, _owner in QUANT_18}
    assert rest and all(g["attempt"]["description"] in titles for g in rest), [g["attempt"]["description"] for g in rest]
    assert not {g["attempt"]["description"] for g in rest} & firsts
    inside = {tid for g in plan["groups"] for tid in g["tasks"]}
    assert inside >= {f"q{n}" for n in (7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18)} and "q15" not in groups["v:sword v0.3"]["tasks"]
    assert plan["ungrouped"] == [tid for tid in numbered if tid not in inside]
    assert [(g["id"], g["attempt"]["description"], nums(g)) for g in rest] == [
        ("t:为下一步", "四模块量化系统 v0 框架与开发指引", [1, 6]), ("t:哪些因子", "四模块候选因子设计", [2, 3]),
        ("t:主要目标标普", "标普500历史因子数据源评估", [4, 5])]      # each named by the report title of its earliest task
    # 根据目前 opens task 1 and task 15 alike and is no subject: the data-source survey (15) is not taken into the framework brief's group
    assert plan["ungrouped"] == ["q15"] and not any(15 in nums(g) for g in rest)


@pytest.mark.parametrize("opener", ["根据目前", "根据之前", "根据现有"])
def test_an_instruction_that_opens_two_tasks_alike_is_no_shared_subject_and_groups_nothing(tmp_path, opener):
    root = _project(tmp_path / "proj")
    _legacy(root, [{"id": "a", "text": f"{opener}的思路写出初步框架"}, {"id": "b", "text": f"{opener}的情况调研免费数据源"}])
    plan = task_groups.propose(root, today=TODAY)
    assert plan["groups"] == [] and plan["ungrouped"] == ["a", "b"]
    assert not [term for term in task_groups._terms(f"{opener}的思路给出初步的方向") if term[0] in "根据"]      # neither 根 nor 据 begins a term


# -- the same text, given to one owner or to several --------------------------------------------------------------------


def _twins(root: Path, *owners_and_reports: tuple[str, bool], text: str = "核对夏普比率的口径") -> dict:
    """Tasks of one text, each with an owner and, when asked, a report of its own."""
    rows = []
    for n, (owner, has_report) in enumerate(owners_and_reports, 1):
        row = {"id": f"s{n}", "text": text, "owner": owner}
        if has_report:
            row["report"] = _report(root, f"{n:x}", f"Report {n}", f"2026-10-0{n}")
        rows.append(row)
    _legacy(root, rows)
    return task_groups.propose(root, today=TODAY)


@pytest.mark.parametrize("tasks, duplicates", [
    # the same owner: made twice, whatever the reports
    ((("agent:codex:gpt-5", True), ("agent:codex:gpt-5", True)), [("s2", "s1")]),
    ((("", True), ("", True)), [("s2", "s1")]),
    # different owners, at most one with a report: the one without is the copy
    ((("agent:codex:gpt-5", True), ("agent:claude-code:opus", False)), [("s2", "s1")]),
    ((("agent:codex:gpt-5", False), ("agent:claude-code:opus", True)), [("s2", "s1")]),
    ((("agent:codex:gpt-5", False), ("agent:claude-code:opus", False)), [("s2", "s1")]),
    # different owners who each have a report: one question given to two agents
    ((("agent:codex:gpt-5", True), ("agent:claude-code:opus", True)), []),
    # a third task of the first owner is a copy of the first; the second stays
    ((("agent:codex:gpt-5", True), ("agent:claude-code:opus", True), ("agent:codex:gpt-5", True)), [("s3", "s1")]),
    # a third with another owner and no report of its own is a copy of the first, whose report is the one that counts
    ((("agent:codex:gpt-5", True), ("agent:claude-code:opus", True), ("agent:deepseek-harness:deepseek-official/deepseek-v4-pro", False)), [("s3", "s1")]),
])
def test_the_same_text_is_a_duplicate_only_for_one_owner_or_when_at_most_one_of_them_has_a_report(root, tasks, duplicates):
    plan = _twins(root, *tasks)
    assert [(d["task"], d["same_as"]) for d in plan["duplicates"]] == duplicates


def test_the_same_question_given_to_different_agents_is_shown_with_their_names_on_each_task_and_kept_together_in_one_group(root):
    one, two, three = "agent:codex:gpt-5", "agent:claude-code:opus", "agent:deepseek-harness:deepseek-official/deepseek-v4-pro"
    plan = _twins(root, (one, True), (two, True), (three, True))
    assert plan["duplicates"] == []    # nothing to remove: three distinct pieces of work
    names = ["Codex · gpt-5", "Claude Code · opus", "DeepSeek Harness · deepseek-official/deepseek-v4-pro"]
    assert [t["asked_of"] for t in plan["tasks"]] == [names] * 3
    # a copy by one of the owners is a duplicate and is not counted among the agents
    plan = _twins(root, (one, True), (two, True), (one, True))
    assert [t["asked_of"] for t in plan["tasks"]] == [names[:2]] * 2 + [[]] and [d["task"] for d in plan["duplicates"]] == ["s3"]
    # a task asked of only one agent says nothing of the kind
    assert [t["asked_of"] for t in _twins(root, (one, True), (two, False))["tasks"]] == [[], []]


def test_tasks_of_one_question_asked_of_several_agents_land_in_one_group_even_when_only_one_report_title_names_the_version(root):
    one, two = "agent:codex:gpt-5", "agent:claude-code:opus"
    rows = [{"id": "a", "text": "对上一份报告回应", "owner": one, "report": _report(root, "a", "Sword v0.2 回应", "2026-10-01")},
            {"id": "b", "text": "对上一份报告回应", "owner": two, "report": _report(root, "b", "第二意见", "2026-10-02")},
            {"id": "c", "text": "对上一份报告回应", "owner": two, "report": _report(root, "c", "Sword v0.3 第二意见的复核", "2026-10-03")}]
    _legacy(root, rows)
    plan = task_groups.propose(root, today=TODAY)
    # a and b were asked, each by another agent: b is read with a's version. c is b's copy and keeps the version its own report names
    assert [(g["id"], g["tasks"]) for g in plan["groups"]] == [("v:sword v0.2", ["a", "b"]), ("v:sword v0.3", ["c"])]
    assert [(d["task"], d["same_as"]) for d in plan["duplicates"]] == [("c", "b")]


# -- names -----------------------------------------------------------------------------------------------------------


def test_a_shared_term_group_is_named_by_the_report_title_of_its_earliest_task_else_its_first_line_cut_at_forty_characters(root):
    long_first = "整理供应商调研记录，" + "并且逐条核对每一项的来源和口径" * 4
    title = _report(root, "a", "供应商调研记录：第一轮", "2026-09-01")
    _legacy(root, [{"id": "a", "text": "整理供应商调研记录", "report": title}, {"id": "b", "text": "补充供应商调研结论"}])
    group = task_groups.propose(root, today=TODAY)["groups"][0]
    assert group["term"] == "供应商调研" and group["attempt"]["description"] == "供应商调研记录：第一轮"   # the shared term stays 共同点
    # the earliest task has no report: its first line, though a later task's report has a title
    _legacy(root, [{"id": "a", "text": long_first + "\n第二行不用"}, {"id": "b", "text": "补充供应商调研结论", "report": title}])
    named = task_groups.propose(root, today=TODAY)["groups"][0]["attempt"]["description"]
    assert named == long_first[:40].rstrip("，") + "…" and len(named) <= 41 and "第二行" not in named
    # a task short enough is the name whole
    _legacy(root, [{"id": "a", "text": "整理供应商调研记录"}, {"id": "b", "text": "补充供应商调研结论"}])
    assert task_groups.propose(root, today=TODAY)["groups"][0]["attempt"]["description"] == "整理供应商调研记录"


# -- a task that goes on from the step before ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, joins", [
    ("对上一份审查报告给出回应", True), ("检查上一步是否完成", True), ("按上一轮的意见改", True), ("重做上一次的回测", True),
    ("看看上次的结果", True), ("接着前一步继续", True),
    ("Review the previous report", True), ("Redo the last step", True), ("Fix the above", True), ("FOLLOW THE ABOVE notes", True),
    ("根据目前所有的指引和之前的全部结论，再构建上一份报告提到的模块", False),         # the reference is past the first 20 characters
    ("Please respond to the previous report", False),         # so is this one
    ("为下一步构建算法", False), ("下一轮再说", False),            # the step after is no step before
])
def test_a_task_whose_first_twenty_characters_refer_to_the_step_before_joins_the_group_of_the_nearest_earlier_task(root, text, joins):
    _legacy(root, [{"id": "a", "text": "Sword v0.2 recheck"}, {"id": "b", "text": text}])
    plan = task_groups.propose(root, today=TODAY)
    assert [(g["id"], g["tasks"]) for g in plan["groups"]] == [("v:sword v0.2", ["a", "b"] if joins else ["a"])]
    assert plan["groups"][0]["after"] == ({"b": "a"} if joins else {})


def test_a_continuing_task_goes_after_the_nearest_earlier_task_that_is_in_a_group_and_has_other_words(root):
    _legacy(root, [{"id": "a", "text": "Sword v0.2 first"}, {"id": "b", "text": "Other v1 first"}, {"id": "c", "text": "unrelated task one"},
                   {"id": "d", "text": "对上一份报告回应"}, {"id": "e", "text": "对上一份报告回应"},          # e: the same words as d, so not d
                   {"id": "f", "text": "检查上一步是否完成"}, {"id": "g", "text": "Sword v0.2 once more"}, {"id": "h", "text": "上次的结果再看一遍"}])
    plan = task_groups.propose(root, today=TODAY)
    groups = {g["id"]: g for g in plan["groups"]}
    # d and e go after b: c is in no group, and e does not follow d, whose words are its own. f goes after e, which joined by this
    # rule and is in a group for the ones after it; h after g
    assert groups["v:other v1"]["tasks"] == ["b", "d", "e", "f"] and groups["v:other v1"]["after"] == {"d": "b", "e": "b", "f": "e"}
    assert groups["v:sword v0.2"]["tasks"] == ["a", "g", "h"] and groups["v:sword v0.2"]["after"] == {"h": "g"}
    assert plan["ungrouped"] == ["c"]


def test_a_continuing_task_with_nothing_before_it_in_a_group_is_left_to_the_shared_terms_and_a_task_that_has_its_own_after_keeps_it(root):
    _legacy(root, [{"id": "a", "text": "对上一份报告回应", "report": _report(root, "a", "供应商调研记录", "2026-10-01")},
                   {"id": "b", "text": "写出供应商调研结论"}, {"id": "c", "text": "Sword v0.2 x"}, {"id": "d", "text": "检查上一步是否完成", "after": "c"}])
    plan = task_groups.propose(root, today=TODAY)
    groups = {g["id"]: g for g in plan["groups"]}
    assert groups["t:供应商调研"]["tasks"] == ["a", "b"] and groups["t:供应商调研"]["after"] == {}    # nothing before a was in a group
    assert groups["v:sword v0.2"]["tasks"] == ["c", "d"] and groups["v:sword v0.2"]["after"] == {}     # d already has an after: left as it is


# -- confirming a group with its `after` -------------------------------------------------------------------------------


def test_confirming_a_group_sets_after_on_the_tasks_that_continue_another_and_leaves_the_others_as_they_were(quant):
    plan = task_groups.propose(quant, today=TODAY)
    group = _group(plan, "v:sword v0")
    done = _confirm(quant, group, after=group["after"])
    assert done["moved"] == [f"q{n}" for n in (7, 8, 9, 10, 11)] and done["linked"] is True
    saved = {t["id"]: t for t in workspace.read_tasks(quant)[0]}
    assert {tid: t.get("after") for tid, t in saved.items() if t.get("after")} == {"q9": "q8", "q10": "q8", "q11": "q8"}
    assert saved["q9"]["attempt"] == saved["q8"]["attempt"] == done["attempt"]["id"] and "after" not in saved["q12"]
    # the attempt's tasks are read in the order of `after`: 8, then the three that answer it
    from researchos import attempt_tasks

    assert [t["id"] for t in attempt_tasks.ordered([saved[f"q{n}"] for n in (7, 8, 9, 10, 11)])] == ["q7", "q8", "q9", "q10", "q11"]


@pytest.mark.parametrize("after", [
    {"q9": "q9"},                          # a task cannot follow itself
    {"q9": "q10", "q10": "q9"},            # nor can two follow each other
    {"q9": "q10", "q10": "q11", "q11": "q9"},
    {"q9": "q12"},                         # a task of another group
    {"q12": "q9"},                         # or a task that is not in this one at all
    {"q9": "nope"},
])
def test_an_after_that_is_not_inside_the_group_or_goes_round_refuses_the_group_before_anything_is_written(quant, after):
    group = _group(task_groups.propose(quant, today=TODAY), "v:sword v0")
    before = _state(quant)
    with pytest.raises(workspace.WorkspaceError) as refused:
        _confirm(quant, group, after=after)
    assert refused.value.code in ("invalid", "group_after_cycle") and refused.value.message_zh.endswith("。") and str(refused.value)
    assert _state(quant) == before


@pytest.mark.parametrize("after", ["q8", ["q8"], {"q9": 8}, {8: "q8"}])
def test_an_after_that_is_not_a_map_of_task_ids_is_refused(quant, after):
    group = _group(task_groups.propose(quant, today=TODAY), "v:sword v0")
    before = _state(quant)
    with pytest.raises(workspace.WorkspaceError) as refused:
        _confirm(quant, group, after=after)
    assert refused.value.code == "invalid" and _state(quant) == before


def test_a_cycle_through_an_after_a_task_already_has_is_refused_too(quant):
    tasks = workspace.read_tasks(quant)[0]
    tasks[7]["after"] = "q9"       # q8 already continues q9, which is not in the attempt table yet either
    (quant / workspace.TASKS_PATH).write_text(json.dumps(tasks, ensure_ascii=False))
    group = _group(task_groups.propose(quant, today=TODAY), "v:sword v0")
    before = _state(quant)
    with pytest.raises(workspace.WorkspaceError) as refused:
        _confirm(quant, group, after={"q9": "q8"})
    assert refused.value.code == "group_after_cycle" and _state(quant) == before

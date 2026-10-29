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
    rel = f"research/results/{name * 32}.md"
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
        ("v:sword v0", "version", ["t1"]), ("v:sword v0.2", "version", ["t2", "t3", "t4"]),
        ("v:sword v0.3", "version", ["t5", "t7"]), ("t:美股历史数据源", "term", ["t8", "t9"])]
    # the table is the project's own: its row 16 is dated MM-DD, so the next rows are numbered 17 on and dated the same way
    assert [(g["term"], g["attempt"]) for g in plan["groups"]] == [
        ("Sword v0", {"number": "17", "date": "08-02", "description": "Sword v0 基线：用全部成员跑一遍回测，输出收益表",
                      "variables": "", "result": "", "verdict": "🕒"}),
        ("Sword v0.2", {"number": "18", "date": "09-01", "description": "Sword v0.2 全成员复算，核对", "variables": "", "result": "", "verdict": "🕒"}),
        ("Sword v0.3", {"number": "19", "date": "10-05", "description": "Sword v0.3 新增行业中性约束", "variables": "", "result": "", "verdict": "🕒"}),
        ("美股历史数据源", {"number": "20", "date": "10-29", "description": "美股历史数据源", "variables": "", "result": "", "verdict": "🕒"})]
    assert all(g["verdict_status"]["status"] == "pending" and g["verdict_status"]["label"] == "待定" for g in plan["groups"])
    assert plan["ungrouped"] == ["t6", "t10", "t11"]
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
    assert _group(plan, "v:sword v0")["tasks"] == ["t3"]  # t3 now reads as Sword v0, wherever t1 is


@pytest.mark.parametrize("text, title, expected", [
    ("Sword v0.2 全成员复算", None, ("sword v0.2", "Sword v0.2", "Sword")),
    ("sword V0.2 recheck", None, ("sword v0.2", "sword v0.2", "sword")),
    ("Rerun Sword-v1.2.3 now", None, ("sword v1.2.3", "Sword v1.2.3", "Sword")),
    ("Run v0.2 once more", None, ("v0.2", "v0.2", "")),
    ("the v3 report", None, ("v3", "v3", "")),
    ("策略 v2 复算", None, ("v2", "v2", "")),
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
    assert sorted((g["term"], g["tasks"]) for g in plan["groups"]) == [
        ("Other v0.2", ["c"]), ("Sword v0.2", ["a", "b"]), ("Sword v9", ["e"]), ("v0.2", ["d"])]
    assert plan["ungrouped"] == []


def test_a_version_only_a_report_title_names_still_groups_its_task_and_the_name_uses_the_tasks_that_say_it_themselves(root):
    plan = task_groups.propose(root, today=TODAY)
    v02 = _group(plan, "v:sword v0.2")
    assert "t4" in v02["tasks"]  # its text names no version; its report is titled Sword v0.2
    assert v02["attempt"]["description"] == "Sword v0.2 全成员复算，核对"  # the words t2 and t3 share, not t4's
    alone = _group(plan, "v:sword v0.3")["attempt"]["description"]
    assert alone == "Sword v0.3 新增行业中性约束"  # one task that says it: its first clause


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
    (root / "map.md").write_text("# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n")
    _scan(root)
    assert [g["attempt"]["number"] for g in task_groups.propose(root, today=TODAY)["groups"]] == ["1", "2", "3", "4"]


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


def test_no_tasks_and_no_task_file_propose_nothing_and_an_unreadable_task_file_is_refused(root):
    (root / workspace.TASKS_PATH).unlink()
    assert task_groups.propose(root, today=TODAY) == {"tasks_revision": None, "table": True, "tasks": [], "groups": [], "ungrouped": [],
                                                      "duplicates": []}
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
    assert done["moved"] == ["t2", "t3", "t4"] and done["linked"] is True and done["ingest_error"] is None and done["mode"] == "single"
    assert "| 18 | 09-01 | Sword v0.2 全成员复算 |  |  | 🕒 |" in (root / "map.md").read_text()
    tasks = workspace.read_tasks(root)[0]
    assert {t["id"]: t.get("attempt") for t in tasks} == {"t2": node, "t3": node, "t4": node} | {
        f"t{n}": None for n in (1, 5, 6, 7, 8, 9, 10, 11)}
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
    assert all(t not in {x["id"] for x in again["tasks"]} for t in ("t2", "t3", "t4"))


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
    assert refused.value.code == "group_table_missing" and "找不到尝试年表" in refused.value.message_zh
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
    assert "| 17 |" in (root / "map.md").read_text() and (root / workspace.TASKS_PATH).read_bytes() == before_tasks


def test_the_row_a_group_writes_reads_back_as_a_pending_attempt_with_no_result(root):
    from researchos.ingest import attempts as attempts_ingest

    group = _group(task_groups.propose(root, today=TODAY), "t:美股历史数据源")
    _confirm(root, group)
    rows = attempts_ingest.parse_attempts_table((root / "map.md").read_text(), "Attempt timeline", attempts_ingest.load_config(root).columns)
    row = next(r for r in rows if r.number == "20")
    assert (row.description, row.date, row.result) == ("美股历史数据源", "10-29", "") and "🕒" in row.verdict


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
        "project": str(root), "tasks": group["tasks"], "attempt": {k: group["attempt"][k] for k in ("number", "date", "description")}})
    assert status == 200, done
    assert done["attempt"]["id"] == "attempt:map.md#18" and done["moved"] == ["t2", "t3", "t4"] and done["linked"] is True
    assert done["tasks_officialness"] and done["generation"] > before
    home = _asked(base, "GET", "/api/workspace")[1]
    assert {t["id"] for t in home["tasks"] if t.get("attempt") == "attempt:map.md#18"} == {"t2", "t3", "t4"}
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
    # their versions were only in the titles of those reports: with no title they read as tasks of no version
    assert _group(plan, "v:sword v0.2")["tasks"] == ["t2", "t3"] and _group(plan, "v:sword v0.3")["tasks"] == ["t7"]
    assert {"t4", "t5"} <= set(plan["ungrouped"])

"""Research OS V16, task B1-3 (DESIGN.md 21.2 'The handback'): the engine.

What an agent hands back -- a headline, key numbers each with its file, what is undone, questions, a suggested
assessment -- is context fields the agent's tool path may write and the confirmation of an assessment is not one of
them. The brief asks for them; "opened" is kept per person on this Mac under the Research OS home and never in the
project; the number check of 16.1 marks a value its file does not hold; confirming writes the attempt's assessment
through the panel's write, with the history note 21.2 gives, and records `assessment_confirmed` in the context.
Every test names its scenario."""

from __future__ import annotations

import dataclasses
import json
import re
import threading
from pathlib import Path

import pytest

from researchos import handbacks, history, mcp_server, paths, people, proposals, research_records, workspace
from researchos.agents import brief, labels, runs
from researchos.records.identity import read_identity

from test_agents_manager import COMPLETE, scene  # noqa: F401 -- `scene` is a fixture
from test_agents_mcp import _run
from test_v8_phase2 import _project, _scan

A16 = "attempt:map.md#16"
A17 = "attempt:map.md#17"
TWO_ROWS = ("# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n"
            "| 16 | 07-26 | TopicShift volatility (16-17) | V | t=2.91 | 🕒 |\n"
            "| 17 | 07-27 | Sword v0.2 recheck | V | r17 | 🕒 |\n")
AGENT = labels.Agent("claude-code", "sonnet")
PERSON, OTHER = "zhang@example.com", "li@example.com"
FULL = {
    "question": "Does it hold?", "finding": "Sharpe 1.82 for Sword v0.2",
    "headline": "Sword v0.2 keeps a Sharpe ratio of 1.82 over the full sample.",
    "key_numbers": [{"what": "Sharpe ratio", "value": "1.82", "file": "results/sharpe.csv"},
                    {"what": "Members", "value": "12", "file": "results/sharpe.csv"}],
    "undone": "The robustness table for 2008 is missing: the data stops in 2009.",
    "decide": ["Should 2008 be dropped from the sample?"],
    "suggested_assessment": {"status": "现行", "reason": "Holds on every member"},
}


@pytest.fixture(autouse=True)
def me() -> people.Me:
    """This Mac belongs to 张三 (the person who starts the runs of these tests)."""
    return people.write_me("张三", PERSON)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = _project(tmp_path / "proj")
    (root / "map.md").write_text(TWO_ROWS)
    (root / "results").mkdir()
    (root / "results" / "sharpe.csv").write_text("member,sharpe\nall,1.82\nn,12\n")
    _scan(root)
    return root


@pytest.fixture
def pid(root: Path) -> str:
    return read_identity(root).identity.id


def _save_tasks(root: Path, tasks: list[dict]) -> None:
    workspace.save_tasks(root, {"tasks": tasks, "revision": workspace.read_tasks(root)[1]})


def hand_back(root: Path, pid: str, *, context=FULL, task_id: str = "t1", by: str | None = PERSON, attempt: str | None = A16,
              report_text: str = "Ran the recheck.\n\nSharpe: 1.82", outputs=None, owner: str = AGENT.owner) -> str:
    """An agent's run that ended with `researchos_complete_task` on a task under an attempt: the report path."""
    tasks = workspace.read_tasks(root)[0]
    if not any(t["id"] == task_id for t in tasks):
        fresh = {"id": task_id, "text": "Recheck Sword v0.2", "done": False, "owner": owner, "due": ""}
        if attempt:
            fresh["attempt"] = attempt
            _save_tasks(root, tasks + [fresh])
        else:  # a task from before 21: no attempt, which a save would refuse for a new task
            (root / workspace.TASKS_PATH).parent.mkdir(parents=True, exist_ok=True)
            (root / workspace.TASKS_PATH).write_text(json.dumps(tasks + [fresh], ensure_ascii=False))
    run_id = _run(root, pid, by=by, by_name="张三" if by else None, task_id=task_id)
    answer = mcp_server.agent_complete_task(root, pid, run_id, report_text, title="Recheck",
                                            outputs=outputs if outputs is not None else [], research_context=context)
    assert answer.startswith("Saved"), answer
    report = next(t for t in workspace.read_tasks(root)[0] if t["id"] == task_id)["report"]
    runs.update_run(pid, run_id, report=report, state="done", ended=runs.now_iso())
    return report


def _context(root: Path, report: str) -> dict:
    return research_records.parse(workspace._read(root, research_records.path_for(report)))


# -- the context fields ---------------------------------------------------------------------------------------------


def test_the_handback_fields_are_kept_as_written_and_a_context_without_them_reads_and_saves_byte_for_byte_as_it_was(root, pid):
    report = hand_back(root, pid)
    found = _context(root, report)
    for key in ("headline", "key_numbers", "undone", "decide", "suggested_assessment"):
        assert found[key] == FULL[key]
    assert "assessment_confirmed" not in found
    old = research_records.normalize({"question": "Q", "finding": "F"})
    assert not set(old) & set(research_records.HANDBACK_FIELDS)  # written only when they hold something
    bare = "research/results/" + "d" * 32 + ".md"
    (root / bare).write_text("# Manual\n")
    saved = research_records.save(root, {"report": bare, "revision": None, "context": {"question": "Q"}})
    raw = (root / research_records.path_for(bare)).read_text()
    assert not any(key in json.loads(raw)["context"] for key in research_records.HANDBACK_FIELDS)
    assert saved["context"]["question"] == "Q"


@pytest.mark.parametrize("field, value", [
    ("headline", "x" * 201), ("headline", 7), ("key_numbers", [{"what": "n", "value": "1", "file": "a.csv"}] * 6),
    ("key_numbers", [{"what": "n", "value": 1.82, "file": "results/sharpe.csv"}]),     # a number, not the text as written
    ("key_numbers", [{"what": "n", "value": "1", "file": "results/sharpe.csv", "x": 1}]),
    ("key_numbers", [{"what": "n", "value": "1"}]), ("key_numbers", "1.82"),
    ("undone", 3), ("decide", ["a", "b", "c", "d"]), ("decide", [""]), ("decide", "ask"),
    ("suggested_assessment", {"status": "现行"}), ("suggested_assessment", {"status": "现行", "reason": "r" * 1001}),
    ("suggested_assessment", {"status": "", "reason": "r"}), ("suggested_assessment", "现行"),
    ("assessment_confirmed", {"by": "m", "at": "now", "status": "现行"}),
    ("assessment_confirmed", {"by": "m", "at": "now", "status": "现行", "adopted": "yes"}),
])
def test_a_handback_field_of_the_wrong_shape_or_over_its_limit_is_refused(field, value):
    with pytest.raises(workspace.WorkspaceError) as exc:
        research_records.normalize({field: value})
    assert exc.value.code == "research_record"


def test_the_limits_of_the_handback_are_the_design_s_and_they_are_inclusive():
    ok = research_records.normalize({
        "headline": "h" * 200, "key_numbers": [{"what": "n", "value": "1", "file": "a.csv"}] * 5, "decide": ["q"] * 3,
        "suggested_assessment": {"status": "现行", "reason": "r" * 1000}})
    assert len(ok["headline"]) == 200 and len(ok["key_numbers"]) == 5 and len(ok["decide"]) == 3
    assert len(ok["suggested_assessment"]["reason"]) == 1000


def test_a_suggested_status_must_be_one_of_the_projects_statuses_default_words_and_its_own(root):
    default = [item["status"] for item in research_records.assessment_statuses(root)]
    assert default == ["现行", "待定", "不利证据", "已放弃", "被否", "归档", "其它"]   # 21.3 names the order the chooser offers them in
    for word in default:
        research_records.validate_context(root, {"suggested_assessment": {"status": word, "reason": ""}})
    with pytest.raises(workspace.WorkspaceError) as exc:
        research_records.validate_context(root, {"suggested_assessment": {"status": "done", "reason": ""}})
    assert "Current" in str(exc.value) and "Pending" in str(exc.value)   # it says what is allowed
    assert research_records.validate_context(root, {"suggested_assessment": {"status": "pending", "reason": "r"}})[
        "suggested_assessment"]["status"] == "待定"   # an agent writing English may name a default status by its English name
    assert research_records.validate_context(root, {"suggested_assessment": {"status": " Adverse Evidence ", "reason": ""}})[
        "suggested_assessment"]["status"] == "不利证据"   # and the file keeps the word (13.1)
    (root / ".researchos" / "attempts.toml").write_text(
        (root / ".researchos" / "attempts.toml").read_text() + '[verdict_labels]\n"🧪" = "待重复"\n"✅" = "成立"\n')
    named = research_records.assessment_statuses(root)
    assert [item["status"] for item in named] == ["成立", "待定", "不利证据", "已放弃", "被否", "归档", "待重复", "其它"]
    assert {item["status"]: item["marker"] for item in named}["待重复"] == "🧪"
    research_records.validate_context(root, {"suggested_assessment": {"status": "待重复", "reason": ""}})
    with pytest.raises(workspace.WorkspaceError):
        research_records.validate_context(root, {"suggested_assessment": {"status": "现行", "reason": ""}})  # the word it replaced


def test_a_status_the_researcher_took_out_later_does_not_stop_them_saving_the_rest_of_the_record(root, pid):
    report = hand_back(root, pid, context={**FULL, "suggested_assessment": {"status": "现行", "reason": "r"}})
    (root / ".researchos" / "attempts.toml").write_text(
        (root / ".researchos" / "attempts.toml").read_text() + '[verdict_labels]\n"✅" = "成立"\n')
    read = research_records.read(root, report)
    sent = {**read["context"], "limits": "A human edit"}
    saved = research_records.save(root, {"report": report, "revision": read["revision"], "context": sent})
    assert saved["context"]["limits"] == "A human edit" and saved["context"]["suggested_assessment"]["status"] == "现行"
    with pytest.raises(workspace.WorkspaceError):  # but a new suggestion has to be a status the project has
        research_records.save(root, {"report": report, "revision": saved["revision"],
                                     "context": {**sent, "suggested_assessment": {"status": "现行", "reason": "other"}}})


def test_a_key_number_cites_a_project_path_and_a_path_outside_the_project_is_refused(root):
    for bad in ("../outside.csv", "/etc/passwd", ".researchos/x.csv", "a/../b.csv"):
        with pytest.raises(workspace.WorkspaceError):
            research_records.validate_context(root, {"key_numbers": [{"what": "n", "value": "1", "file": bad}]})
    # a file that is not there is shown as such by the number check, not refused when the agent hands in
    research_records.validate_context(root, {"key_numbers": [{"what": "n", "value": "1", "file": "results/gone.csv"}]})


# -- the agent cannot confirm ----------------------------------------------------------------------------------------


CONFIRMED = {"by": PERSON, "at": "2026-10-30T09:00:00+01:00", "status": "现行", "adopted": True}


def test_an_agent_that_sends_assessment_confirmed_is_refused_and_nothing_is_written(root, pid):
    _save_tasks(root, [{"id": "t1", "text": "Recheck", "done": False, "owner": AGENT.owner, "due": "", "attempt": A16}])
    run_id = _run(root, pid)
    before = (root / workspace.TASKS_PATH).read_bytes()
    answer = mcp_server.agent_complete_task(root, pid, run_id, "Done.", outputs=[],
                                            research_context={**FULL, "assessment_confirmed": CONFIRMED})
    assert answer.startswith("Not written:") and "assessment_confirmed" in answer
    assert (root / workspace.TASKS_PATH).read_bytes() == before
    assert not list((root / "research").glob("results/*.md")) and runs.read_signals(pid, run_id) == []
    for value in (None, {}):  # even an empty one: the key itself is not an agent's
        again = mcp_server.agent_complete_task(root, pid, run_id, "Done.", outputs=[],
                                               research_context={**FULL, "assessment_confirmed": value})
        assert again.startswith("Not written:")


def test_the_agent_save_path_itself_refuses_a_confirmation_as_a_second_defence(root, pid):
    report = hand_back(root, pid)
    author = {"source": "agent", "who": AGENT.owner, "note": "run"}
    with pytest.raises(workspace.WorkspaceError) as exc:
        research_records.save_agent(root, report, {**FULL, "assessment_confirmed": CONFIRMED}, author,
                                    research_records.read(root, report)["revision"])
    assert "assessment_confirmed" in str(exc.value)
    assert "assessment_confirmed" not in _context(root, report)


def test_the_editors_save_keeps_a_stored_confirmation_refuses_a_changed_one_and_cannot_make_one(root, pid):
    report = hand_back(root, pid)
    plain = research_records.read(root, report)
    with pytest.raises(workspace.WorkspaceError) as exc:  # no confirmation yet: none can be made by a save
        research_records.save(root, {"report": report, "revision": plain["revision"],
                                     "context": {**plain["context"], "assessment_confirmed": CONFIRMED}})
    assert "confirm" in str(exc.value)
    research_records.confirm_assessment(root, report, CONFIRMED)
    now = research_records.read(root, report)
    assert now["context"]["assessment_confirmed"] == CONFIRMED
    kept = {k: v for k, v in now["context"].items() if k != "assessment_confirmed"}  # a client that does not know the field
    saved = research_records.save(root, {"report": report, "revision": now["revision"], "context": {**kept, "limits": "More"}})
    assert saved["context"]["assessment_confirmed"] == CONFIRMED and saved["context"]["limits"] == "More"
    with pytest.raises(workspace.WorkspaceError):
        research_records.save(root, {"report": report, "revision": saved["revision"],
                                     "context": {**saved["context"], "assessment_confirmed": {**CONFIRMED, "adopted": False}}})
    assert _context(root, report)["assessment_confirmed"] == CONFIRMED


def test_a_rerun_of_the_task_hands_back_afresh_and_the_old_confirmation_does_not_carry_over(root, pid):
    report = hand_back(root, pid)
    research_records.confirm_assessment(root, report, CONFIRMED)
    run_id = _run(root, pid, task_id="t1")
    assert mcp_server.agent_complete_task(root, pid, run_id, "Second go.", outputs=[],
                                          research_context={"headline": "Second answer."}).startswith("Saved")
    again = _context(root, report)
    assert again["headline"] == "Second answer." and "assessment_confirmed" not in again and "suggested_assessment" not in again


# -- what the agent is told -------------------------------------------------------------------------------------------


def test_the_brief_requires_the_handback_for_a_research_task_in_both_languages_and_lists_the_statuses(root):
    words = ["现行", "待重复", "其它"]
    en = brief.rules(lang="en", runner="claude-code", prot={"data": [], "records": []}, statuses=words)
    zh = brief.rules(lang="zh", runner="claude-code", prot={"data": [], "records": []}, statuses=words)
    for needle in ("headline", "key_numbers", "undone", "decide", "suggested_assessment", "200 characters", "five",
                   "three questions", "1,000", "Current, 待重复, Other", "suggestion only", "never send assessment_confirmed",
                   "Write no note besides your report unless the task asks"):
        assert needle in en, needle
    for needle in ("headline", "key_numbers", "undone", "decide", "suggested_assessment", "200", "五个", "三个", "1000",
                   "现行, 待重复, 其它", "代理建议只是建议", "绝不提交 assessment_confirmed", "除了报告，不要另写笔记，除非任务要求"):
        assert needle in zh, needle
    default = brief.rules(lang="en", runner="claude-code", prot={"data": [], "records": []})
    assert "Current, Abandoned, Pending, Adverse evidence, Rejected, Archived, Other" in default
    assert "现行, 已放弃, 待定, 不利证据, 被否, 归档, 其它" in brief.rules(lang="zh", runner="claude-code", prot={"data": [], "records": []})
    assert not re.search(r"[\u4e00-\u9fff]", default)   # an English brief holds no Chinese character (the statuses are named in English)
    # the guard's first rule is unchanged: the agent still cannot write the attempt's assessment
    assert "Make a decision that belongs to the person: an attempt's research assessment" in en


def test_the_completion_tool_documents_the_handback_and_the_refusal_of_a_confirmation():
    server = mcp_server.build_agent_server(".", "p-1", "r-20261030T000000Z-aaaaaa")
    tool = next(t for t in server._tool_manager.list_tools() if t.name == "researchos_complete_task")
    for needle in ("headline", "key_numbers", "undone", "decide", "suggested_assessment", "only a suggestion",
                   "assessment_confirmed", "Write no note besides this report"):
        assert needle in tool.description, needle


def test_a_real_run_is_given_the_handback_brief_with_the_projects_status_words_and_its_report_waits_for_the_person_who_started_it(scene):
    from researchos.agents import runs as run_store

    (scene.root / ".researchos").mkdir(exist_ok=True)
    (scene.root / ".researchos" / "attempts.toml").write_text(
        'file = "map.md"\nheading = "Attempt timeline"\n[columns]\nid = "#"\ndate = "Date"\ndescription = "Path"\n'
        'variables = "Variables"\nresult = "Result"\nverdict = "Verdict"\n[verdict_labels]\n"🧪" = "待重复"\n')
    scene.scenario(COMPLETE)
    run = scene.wait(scene.start()["id"])
    assert run["state"] == "done"
    sent = (run_store.run_dir(scene.pid, run["id"]) / "instruction.txt").read_text()
    for needle in ("headline", "key_numbers", "suggested_assessment", "待重复", "Write no note besides your report"):
        assert needle in sent, needle
    assert handbacks.unopened(scene.pid, "m-1") == [run["report"]]     # the manager records the report on the run (`report`)
    assert handbacks.unopened(scene.pid, "m-2") == []                   # and it waits for the person who pressed 运行 only


# -- who has opened what ----------------------------------------------------------------------------------------------


def test_a_handback_waits_for_the_person_who_started_the_run_until_they_open_it_and_for_nobody_else(root, pid):
    mine = hand_back(root, pid, task_id="t1", by=PERSON)
    theirs = hand_back(root, pid, task_id="t2", by=OTHER)
    assert handbacks.unopened(pid, PERSON) == [mine] and handbacks.unopened(pid, OTHER) == [theirs]
    assert handbacks.unopened(pid, "m-3@example.com") == [] and handbacks.unopened(None, PERSON) == []
    assert handbacks.mark_opened(pid, PERSON, mine) == {"report": mine, "opened": True, "run": handbacks._handed_back(pid)[mine]["id"]}
    assert handbacks.unopened(pid, PERSON) == [] and handbacks.unopened(pid, OTHER) == [theirs]  # for that person only
    handbacks.mark_opened(pid, OTHER, mine)  # opening someone else's handback is kept for the one who opened it
    assert handbacks.unopened(pid, OTHER) == [theirs]


def test_the_opened_store_is_under_the_research_os_home_one_file_per_person_and_never_in_the_project(root, pid):
    report = hand_back(root, pid, by=PERSON)
    before = {p.relative_to(root) for p in root.rglob("*")}
    handbacks.mark_opened(pid, PERSON, report)
    handbacks.mark_opened(pid, "Zhang@Example.com", report)
    assert {p.relative_to(root) for p in root.rglob("*")} == before  # nothing in the project
    folder = paths.researchos_home() / "handbacks" / pid
    files = sorted(p.name for p in folder.iterdir())
    assert len(files) == 2 and all(name.endswith(".json") and "@" not in name for name in files)  # a hash, not the address
    stored = json.loads(next(folder.glob("*.json")).read_text())
    assert stored["version"] == 1 and list(stored["opened"]) == [report] and stored["opened"][report]["run"].startswith("r-")


def test_a_handback_is_unopened_again_when_a_later_run_hands_the_same_report_back(root, pid):
    report = hand_back(root, pid)
    handbacks.mark_opened(pid, PERSON, report)
    assert handbacks.unopened(pid, PERSON) == []
    run_id = _run(root, pid, task_id="t1", by=PERSON)
    mcp_server.agent_complete_task(root, pid, run_id, "Sent back, done again.", outputs=[], research_context={"headline": "Again."})
    runs.update_run(pid, run_id, report=report, state="done", ended=runs.now_iso())
    assert handbacks.unopened(pid, PERSON) == [report]
    handbacks.mark_opened(pid, PERSON, report)
    assert handbacks.unopened(pid, PERSON) == []


def test_an_undone_run_hands_nothing_back_and_a_report_no_run_wrote_has_nothing_to_open(root, pid):
    report = hand_back(root, pid)
    run_id = handbacks._handed_back(pid)[report]["id"]
    runs.update_run(pid, run_id, reverted=True)
    assert handbacks.unopened(pid, PERSON) == []
    other = "research/results/" + "e" * 32 + ".md"
    assert handbacks.mark_opened(pid, PERSON, other) == {"report": other, "opened": False}
    assert not (paths.researchos_home() / "handbacks" / pid).exists()


def test_a_run_that_names_nobody_is_whoever_s_mac_it_is_and_the_store_survives_a_damaged_file(root, pid):
    report = hand_back(root, pid, by=None)
    assert handbacks.unopened(pid, None) == [report] and handbacks.unopened(pid, PERSON) == [report]
    handbacks.mark_opened(pid, None, report)
    assert handbacks.unopened(pid, None) == []
    folder = paths.researchos_home() / "handbacks" / pid
    for file in folder.glob("*.json"):
        file.write_text("{not json")
    assert handbacks.unopened(pid, None) == [report]  # a store that cannot be read says everything is unopened, not an error


def test_the_unopened_list_names_each_with_its_title_headline_task_and_attempt_and_drops_a_report_whose_task_is_gone(root, pid):
    first = hand_back(root, pid, task_id="t1")
    second = hand_back(root, pid, task_id="t2", context={"headline": "Other answer."}, attempt=A17)
    listed = handbacks.list_unopened(root, pid, PERSON)
    assert [(i["report"], i["headline"], i["task"], i["attempt"]) for i in listed] == [
        (first, FULL["headline"], "t1", A16), (second, "Other answer.", "t2", A17)]
    assert listed[0]["title"] == "Recheck" and listed[0]["run"].startswith("r-")
    _save_tasks(root, [t for t in workspace.read_tasks(root)[0] if t["id"] != "t1"])
    assert [i["report"] for i in handbacks.list_unopened(root, pid, PERSON)] == [second]


def test_the_route_map_cards_count_what_the_store_says_is_unopened(root, pid):
    from researchos import attempt_tasks

    first = hand_back(root, pid, task_id="t1")
    hand_back(root, pid, task_id="t2")
    work = attempt_tasks.by_attempt(root, pid, PERSON, [A16, A17])
    assert work[A16]["unopened"] == 2 and work[A16]["latest"]["unopened"] is True
    assert {i["id"]: i["state"] for i in work[A16]["items"]} == {"t1": "to_read", "t2": "to_read"}
    handbacks.mark_opened(pid, PERSON, first)
    assert attempt_tasks.by_attempt(root, pid, PERSON, [A16])[A16]["unopened"] == 1
    assert attempt_tasks.by_attempt(root, pid, OTHER, [A16])[A16]["unopened"] == 0  # nothing waits for someone who started no run


# -- the number check -------------------------------------------------------------------------------------------------


def _check(root: Path, value: str, file: str = "results/sharpe.csv") -> str:
    return handbacks.check_numbers(root, [{"what": "n", "value": value, "file": file}])[0]["check"]


def test_a_key_number_is_found_when_every_number_of_its_value_is_in_its_file_as_the_draft_check_reads_numbers(root):
    (root / "results" / "t.txt").write_text("N = 1,204 firms; share 12.5%; beta -0.0031; p 1.2e-05\n")
    assert _check(root, "1.82") == "found" and _check(root, "12") == "found"
    assert _check(root, "1.820") == "found"                      # 1.820 is the value 1.82
    assert _check(root, "1,204", "results/t.txt") == "found" and _check(root, "1204", "results/t.txt") == "found"
    assert _check(root, "12.5%", "results/t.txt") == "found" and _check(root, "-0.0031", "results/t.txt") == "found"
    assert _check(root, "1.2e-05", "results/t.txt") == "found"
    assert _check(root, "N = 1,204", "results/t.txt") == "found"   # the words around a number do not matter, the number does


def test_a_value_the_file_does_not_hold_is_marked_missing_not_found(root):
    assert _check(root, "1.83") == "missing" and _check(root, "2.91") == "missing"
    assert _check(root, "1.82 and 9.9") == "missing"                # one number of the value is not in the file
    (root / "results" / "t.txt").write_text("beta 0.0031\n")
    assert _check(root, "-0.0031", "results/t.txt") == "missing"    # the sign is part of the number


def test_a_value_without_a_number_must_occur_as_written_case_and_spacing_aside(root):
    (root / "results" / "t.txt").write_text("Result:  Significant   at the 5% level\n")
    assert _check(root, "significant at the 5% level", "results/t.txt") == "found"
    assert _check(root, "not significant", "results/t.txt") == "missing"


def test_a_file_that_cannot_be_read_as_text_is_unreadable_and_says_nothing_of_the_value(root):
    (root / "results" / "pic.png").write_bytes(b"\x89PNG\x00\x01")
    (root / "results" / "big.csv").write_text("x," * 200_000)
    (root / "results" / "latin.txt").write_bytes("café 1.82".encode("latin-1"))
    for file in ("results/gone.csv", "results/pic.png", "results/big.csv", "results/latin.txt"):
        assert _check(root, "1.82", file) == "unreadable", file
    (root / "results" / "real.csv").write_text("1.82\n")
    (root / "results" / "link.csv").symlink_to(root / "results" / "real.csv")
    assert _check(root, "1.82", "results/link.csv") == "unreadable"   # a link is not followed (workspace._read)


def test_the_check_reads_each_cited_file_once_and_keeps_the_numbers_in_order(root):
    numbers = [{"what": "a", "value": "1.82", "file": "results/sharpe.csv"}, {"what": "b", "value": "7.77", "file": "results/sharpe.csv"},
               {"what": "c", "value": "12", "file": "results/gone.csv"}]
    got = handbacks.check_numbers(root, numbers)
    assert [(i["what"], i["check"]) for i in got] == [("a", "found"), ("b", "missing"), ("c", "unreadable")]
    assert got[0]["file"] == "results/sharpe.csv" and got[0]["value"] == "1.82"


# -- the handback as the page reads it ---------------------------------------------------------------------------------


def test_the_view_has_everything_the_page_shows_in_its_order_and_marks_the_numbers_it_cannot_find(root, pid):
    for folder in ("figures", "code", "tables"):
        (root / folder).mkdir(exist_ok=True)
    (root / "figures" / "sharpe.png").write_bytes(b"\x89PNG fig")
    (root / "code" / "recheck.py").write_text("print(1.82)\n")
    (root / "tables" / "t.csv").write_text("a,b\n1,2\n")
    ctx = {**FULL, "key_numbers": [*FULL["key_numbers"], {"what": "Alpha", "value": "9.99", "file": "results/sharpe.csv"}]}
    report = hand_back(root, pid, context=ctx, outputs=[
        {"kind": "code", "path": "code/recheck.py", "title": "Recheck script"},
        {"kind": "figure", "path": "figures/sharpe.png", "title": "Sharpe by member"},
        {"kind": "table", "path": "tables/t.csv", "title": "Table 1"}])
    got = handbacks.view(root, pid, PERSON, report, "zh")
    assert got["headline"] == FULL["headline"] and got["undone"] == FULL["undone"] and got["decide"] == FULL["decide"]
    assert [(n["what"], n["check"]) for n in got["key_numbers"]] == [("Sharpe ratio", "found"), ("Members", "found"), ("Alpha", "missing")]
    assert got["suggested"] == {"status": "现行", "reason": "Holds on every member", "known": True, "marker": "✅", "abandons": False}
    assert [o["kind"] for o in got["outputs"]] == ["figure", "table", "code"]   # figures first, then tables, data, code
    assert got["outputs"][0] == {"kind": "figure", "path": "figures/sharpe.png", "title": "Sharpe by member", "state": "current"}
    assert got["title"] == "Recheck" and "Sharpe: 1.82" in got["text"] and got["text"].startswith("# Recheck")
    assert got["task"] == {"id": "t1", "text": "Recheck Sword v0.2", "attempt": A16, "after": None, "owner": AGENT.owner, "done": True}
    assert got["attempt"]["number"] == "16" and got["attempt"]["status"]["status"] == "pending" and got["attempt"]["verdict"] == "🕒"
    assert (got["run"]["label"], got["run"]["by"], got["run"]["by_name"]) == ("Claude Code · sonnet", PERSON, "张三")
    assert got["unopened"] is True and got["mode"] == "single" and got["confirmed"] is None and got["context_error"] is None
    assert [s["status"] for s in got["statuses"]][-1] == "其它" and got["statuses"][0]["abandons"] is False
    assert next(s for s in got["statuses"] if s["status"] == "已放弃")["abandons"] is True
    handbacks.mark_opened(pid, PERSON, report)
    assert handbacks.view(root, pid, PERSON, report)["unopened"] is False
    assert handbacks.view(root, pid, OTHER, report)["unopened"] is False


def test_the_view_of_a_report_written_by_hand_and_of_a_damaged_context_still_opens(root, pid):
    by_hand = "research/results/" + "c" * 32 + ".md"
    (root / by_hand).parent.mkdir(parents=True, exist_ok=True)
    (root / by_hand).write_text("# By hand\n\nMy own result.\n")
    got = handbacks.view(root, pid, PERSON, by_hand)
    assert got["run"] is None and got["task"] is None and got["attempt"] is None and got["suggested"] is None
    assert got["title"] == "By hand" and got["key_numbers"] == [] and got["unopened"] is False
    (root / research_records.path_for(by_hand)).write_text("{not json")
    assert handbacks.view(root, pid, PERSON, by_hand)["context_error"]
    with pytest.raises(workspace.WorkspaceError) as exc:
        handbacks.view(root, pid, PERSON, "research/results/" + "9" * 32 + ".md")
    assert exc.value.code == "not_found"
    with pytest.raises(workspace.WorkspaceError):
        handbacks.view(root, pid, PERSON, "research/notes/" + "9" * 32 + ".md")


def test_a_suggestion_with_a_status_the_project_no_longer_has_is_shown_as_unknown_not_hidden(root, pid):
    report = hand_back(root, pid, context={**FULL, "suggested_assessment": {"status": "现行", "reason": "r"}})
    (root / ".researchos" / "attempts.toml").write_text(
        (root / ".researchos" / "attempts.toml").read_text() + '[verdict_labels]\n"✅" = "成立"\n')
    got = handbacks.view(root, pid, PERSON, report)
    assert got["suggested"]["status"] == "现行" and got["suggested"]["known"] is False and got["suggested"]["marker"] == ""


# -- confirming ---------------------------------------------------------------------------------------------------------


def _cell(root: Path, number: str = "16") -> str:
    return next(line for line in (root / "map.md").read_text().splitlines() if line.startswith(f"| {number} |"))


def _versions(pid: str, node: str):
    with history.Store.open(pid) as store:
        return store.versions(node)


def test_confirming_the_suggestion_writes_only_the_verdict_cell_in_the_tables_form_and_says_it_was_adopted(root, pid):
    report = hand_back(root, pid)
    before = (root / "map.md").read_text()
    done = handbacks.confirm(root, {"report": report, "status": "现行", "reason": "Holds on every member"})
    assert done["written"] is True and done["adopted"] is True and done["cell"] == "✅ Holds on every member"
    after = (root / "map.md").read_text()
    assert _cell(root) == "| 16 | 07-26 | TopicShift volatility (16-17) | V | t=2.91 | ✅ Holds on every member |"
    assert [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b] == [(
        "| 16 | 07-26 | TopicShift volatility (16-17) | V | t=2.91 | 🕒 |", _cell(root))]   # only that cell of that row
    assert _cell(root, "17") == "| 17 | 07-27 | Sword v0.2 recheck | V | r17 | 🕒 |"   # another row is as it was
    assert done["backup"] and done["ingest_error"] is None and done["decision"] is None and done["history_error"] is None
    assert done["note"] == "由 张三 确认（采纳 Claude Code · sonnet 的建议）"
    stored = _context(root, report)["assessment_confirmed"]
    assert stored["status"] == "现行" and stored["adopted"] is True and stored["by"] == PERSON and stored["at"]
    assert done["confirmed"] == stored and done["context_error"] is None
    assert stored["status"] == _context(root, report)["suggested_assessment"]["status"]   # the agent's own words are untouched
    [newest, *_older] = _versions(pid, A16)
    assert newest.note == done["note"] and newest.who == PERSON and newest.summary["verdict"] == "✅ Holds on every member"
    assert _older and _older[0].summary["verdict"] == "🕒"   # the version before it is the scan that saw the row


def test_the_history_entry_reads_as_21_2_gives_it_in_both_languages_and_a_changed_one_has_the_plain_sentence(root, pid):
    report = hand_back(root, pid)
    done = handbacks.confirm(root, {"report": report, "status": "现行", "reason": "Holds on every member"})
    assert done["note"] == "由 张三 确认（采纳 Claude Code · sonnet 的建议）"
    assert done["sentence"] == {"zh": "由 张三 确认（采纳 Claude Code · sonnet 的建议）",
                                "en": "Confirmed by 张三 (adopted the suggestion of Claude Code · sonnet)"}
    shown = history.versions(root, A16, "en")["versions"][0]
    assert shown["note"] == "Confirmed by 张三 (adopted the suggestion of Claude Code · sonnet)"
    assert history.versions(root, A16, "zh")["versions"][0]["note"] == "由 张三 确认（采纳 Claude Code · sonnet 的建议）"
    other = hand_back(root, pid, task_id="t2", attempt=A17)
    changed = handbacks.confirm(root, {"report": other, "status": "待定", "reason": "Needs 2008"})
    assert changed["adopted"] is False and changed["note"] == "由 张三 确认"
    assert history.versions(root, A17, "en")["versions"][0]["note"] == "Confirmed by 张三"
    assert _context(root, other)["assessment_confirmed"]["adopted"] is False


def test_confirming_with_another_status_or_another_reason_is_a_change_and_records_adopted_false(root, pid):
    report = hand_back(root, pid)
    done = handbacks.confirm(root, {"report": report, "status": "不利证据", "reason": "Fails in 2008"})
    assert done["adopted"] is False and _cell(root).endswith("| ⚠️ Fails in 2008 |")
    assert _context(root, report)["assessment_confirmed"] == {**done["confirmed"], "status": "不利证据", "adopted": False}
    same_status = hand_back(root, pid, task_id="t2", attempt=A17)
    done = handbacks.confirm(root, {"report": same_status, "status": "现行", "reason": "Not what the agent said"})
    assert done["adopted"] is False   # the same status with other words is a change too
    spaced = hand_back(root, pid, task_id="t3", attempt=A17, context={**FULL, "suggested_assessment": {"status": "现行", "reason": "a  b"}})
    assert handbacks.confirm(root, {"report": spaced, "status": "现行", "reason": " a b "})["adopted"] is True   # spaces do not count


def test_confirming_a_handback_with_no_suggestion_is_a_plain_confirmation_and_a_status_other_writes_the_words_alone(root, pid):
    report = hand_back(root, pid, context={"headline": "No view."})
    done = handbacks.confirm(root, {"report": report, "status": "待定", "reason": "Wait for the data"})
    assert done["adopted"] is False and done["note"].endswith("确认") and _cell(root).endswith("| 🕒 Wait for the data |")
    other = hand_back(root, pid, task_id="t2", attempt=A17, context={"headline": "x"})
    done = handbacks.confirm(root, {"report": other, "status": "其它", "reason": "see the paper draft"})
    assert _cell(root, "17").endswith("| see the paper draft |") and done["cell"] == "see the paper draft"
    with pytest.raises(workspace.WorkspaceError) as exc:
        handbacks.confirm(root, {"report": other, "status": "其它", "reason": ""})
    assert exc.value.code == "handback_reason_needed"


def test_a_cell_the_table_already_holds_that_way_is_not_written_again_but_the_confirmation_is_recorded(root, pid):
    report = hand_back(root, pid, context={**FULL, "suggested_assessment": {"status": "待定", "reason": ""}})
    before = (root / "map.md").read_bytes()
    done = handbacks.confirm(root, {"report": report, "status": "待定", "reason": ""})
    assert done["written"] is False and done["backup"] is None and (root / "map.md").read_bytes() == before
    assert _context(root, report)["assessment_confirmed"]["status"] == "待定" and done["adopted"] is True


def test_choosing_abandoned_asks_for_the_reason_freezes_the_basis_and_writes_nothing_without_it(root, pid):
    report = hand_back(root, pid, context={**FULL, "suggested_assessment": {"status": "已放弃", "reason": "Dead end"}})
    before = (root / "map.md").read_bytes()
    with pytest.raises(workspace.WorkspaceError) as exc:
        handbacks.confirm(root, {"report": report, "status": "已放弃", "reason": "Dead end"})
    assert exc.value.code == "reason_required" and "理由" in exc.value.message_zh
    assert (root / "map.md").read_bytes() == before and "assessment_confirmed" not in _context(root, report)
    done = handbacks.confirm(root, {"report": report, "status": "已放弃", "reason": "Dead end", "abandon_reason": "The data stops in 2009"})
    assert done["decision"] and done["adopted"] is True and "☠️ Dead end" in _cell(root)
    assert "The data stops in 2009" in _cell(root)   # the reason goes into the result as 14.3 has it
    decisions = (root / ".researchos" / "decisions.toml").read_text()
    assert 'act = "abandoned"' in decisions and "The data stops in 2009" in decisions   # and the basis is frozen


def test_a_confirmation_that_cannot_be_recorded_is_refused_before_the_table_is_written(root, pid):
    report = hand_back(root, pid)
    (root / research_records.path_for(report)).write_text("{damaged")
    before = (root / "map.md").read_bytes()
    with pytest.raises(workspace.WorkspaceError):
        handbacks.confirm(root, {"report": report, "status": "现行", "reason": "r"})
    assert (root / "map.md").read_bytes() == before


@pytest.mark.parametrize("body, code", [
    ({"status": "不存在的状态", "reason": ""}, "handback_status_unknown"),
    ({"status": "现行", "reason": "r" * 1001}, "invalid"), ({"status": 3, "reason": ""}, "invalid"),
])
def test_a_confirmation_of_an_unknown_status_or_a_reason_over_the_limit_is_refused_and_writes_nothing(root, pid, body, code):
    report = hand_back(root, pid)
    before = (root / "map.md").read_bytes()
    with pytest.raises(workspace.WorkspaceError) as exc:
        handbacks.confirm(root, {"report": report, **body})
    assert exc.value.code == code and (root / "map.md").read_bytes() == before


def test_a_handback_whose_task_has_no_attempt_or_whose_attempt_is_gone_cannot_be_confirmed(root, pid):
    loose = hand_back(root, pid, task_id="t1", attempt=None)  # a task from before 21: no attempt
    with pytest.raises(workspace.WorkspaceError) as exc:
        handbacks.confirm(root, {"report": loose, "status": "现行", "reason": ""})
    assert exc.value.code == "handback_no_attempt" and "归入" in exc.value.message_zh
    gone = hand_back(root, pid, task_id="t2", attempt=A17)
    (root / "map.md").write_text(TWO_ROWS.replace("| 17 | 07-27 | Sword v0.2 recheck | V | r17 | 🕒 |\n", ""))
    _scan(root)
    with pytest.raises(workspace.WorkspaceError) as exc:
        handbacks.confirm(root, {"report": gone, "status": "现行", "reason": ""})
    assert exc.value.code == "handback_attempt_unknown"
    with pytest.raises(workspace.WorkspaceError):
        handbacks.confirm(root, {"report": "research/notes/x.md", "status": "现行", "reason": ""})
    with pytest.raises(workspace.WorkspaceError):
        handbacks.confirm(root, ["not", "an", "object"])


def test_a_project_nobody_may_write_refuses_to_confirm(root, pid, monkeypatch):
    report = hand_back(root, pid)
    monkeypatch.setattr(proposals, "role_of", lambda r: ("none", None, None))
    with pytest.raises(workspace.WorkspaceError) as exc:
        handbacks.confirm(root, {"report": report, "status": "现行", "reason": ""})
    assert exc.value.code == "read_only" and exc.value.status == 403


def test_the_agent_guard_is_unchanged_an_agent_run_still_cannot_write_the_attempt_table_or_the_context_file(root, pid, tmp_path):
    # 21.2: an agent may propose an assessment in its own field; the table is written by a person's confirmation only
    from researchos.agents import guard, snapshot

    prot = snapshot.protected(root, snapshot.take(root))
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    guard.write_guard_file(run_dir, root=root, prot=prot, engine_port=None)
    g = guard.load_guard(run_dir)
    report = hand_back(root, pid)
    assert guard.check_tool(g, "Edit", {"file_path": str(root / "map.md")}, cwd=str(root))
    assert guard.check_tool(g, "Bash", {"command": "echo x >> map.md"}, cwd=str(root))
    assert guard.check_tool(g, "Write", {"file_path": str(root / research_records.path_for(report))}, cwd=str(root))
    assert guard.check_tool(g, "Write", {"file_path": str(root / "results" / "notes.txt")}, cwd=str(root)) is None
    rules = brief.rules(lang="en", runner="claude-code", prot=prot)
    assert "Make a decision that belongs to the person: an attempt's research assessment" in rules
    assert "never send assessment_confirmed" in rules


def test_a_members_confirmation_lands_in_their_working_copy_with_the_sentence_for_the_proposal_and_the_official_version_is_untouched(tmp_path, monkeypatch):
    from researchos import addproject
    from researchos import project as project_mod
    from test_v8_phase4 import Drive

    monkeypatch.setattr(addproject, "_top_level_folders", lambda: set())
    drive = Drive(tmp_path, monkeypatch)
    with drive.member():
        project_mod.open_project(drive.work)
        _scan(drive.work)
        work_id = read_identity(drive.work).identity.id
        report = hand_back(drive.work, work_id, by=None, attempt=A16)
        done = handbacks.confirm(drive.work, {"report": report, "status": "现行", "reason": "Holds on every member"})
        assert done["mode"] == "member" and done["written"] is True and done["adopted"] is True
        assert done["sentence"] == {"zh": "由 王 确认（采纳 Claude Code · sonnet 的建议）",
                                    "en": "Confirmed by 王 (adopted the suggestion of Claude Code · sonnet)"}
        assert "| ✅ Holds on every member |" in (drive.work / "map.md").read_text()           # their own copy
        assert _context(drive.work, report)["assessment_confirmed"]["by"] == "wang@example.com"
        assert handbacks.view(drive.work, work_id, "wang@example.com", report)["mode"] == "member"
        draft = proposals.make_proposal(drive.work, "verdict_change", done["sentence"]["zh"], number="16")   # what the page composes
        assert draft.data["kind"] == "verdict_change" and draft.data["sentence"] == done["sentence"]["zh"]
    with drive.pi():
        assert "Holds on every member" not in (drive.official / "map.md").read_text()                   # nothing reached the official version


# -- the HTTP routes ----------------------------------------------------------------------------------------------------


@pytest.fixture
def served(root, pid):
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


def test_the_four_routes_read_the_handback_list_the_unopened_mark_one_opened_and_confirm(root, pid, served):
    from test_webapp_server import _get, _post

    base, _httpd = served
    report = hand_back(root, pid, by=None)
    listed = _get(base, "/api/workspace/handbacks")[1]
    assert [i["report"] for i in listed["unopened"]] == [report] and listed["id"] == pid
    status, shown = _get(base, "/api/workspace/handback?report=" + report)
    assert status == 200 and shown["headline"] == FULL["headline"] and shown["unopened"] is True and shown["key_numbers"][0]["check"] == "found"
    assert _get(base, "/api/workspace/handback?report=" + report)[1]["unopened"] is True   # reading is not opening
    assert _post(base, "/api/workspace/handback/opened", {"project": str(root), "report": report})[1] == {
        "report": report, "opened": True, "run": handbacks._handed_back(pid)[report]["id"]}
    assert _get(base, "/api/workspace/handbacks")[1]["unopened"] == []
    status, done = _post(base, "/api/workspace/handback/confirm", {"project": str(root), "report": report, "status": "现行",
                                                                    "reason": "Holds on every member"})
    assert status == 200 and done["written"] is True and done["adopted"] is True and done["generation"] is not None
    assert _cell(root).endswith("| ✅ Holds on every member |")
    assert _get(base, "/api/workspace/handback?report=" + report)[1]["confirmed"]["status"] == "现行"


def test_the_routes_refuse_a_missing_report_a_wrong_project_a_foreign_origin_and_a_read_only_project(root, pid, served):
    from test_webapp_server import _get, _post, _request_with_headers

    base, httpd = served
    report = hand_back(root, pid)
    body = {"project": str(root), "report": report, "status": "现行", "reason": ""}
    assert _get(base, "/api/workspace/handback?report=research/results/" + "7" * 32 + ".md")[0] == 404
    assert _post(base, "/api/workspace/handback/confirm", {**body, "project": "/other"})[0] == 409
    assert _post(base, "/api/workspace/handback/opened", {"project": "/other", "report": report})[0] == 409
    assert _post(base, "/api/workspace/handback/opened", {"project": str(root)})[0] == 400
    status, error = _post(base, "/api/workspace/handback/confirm", {**body, "status": "不存在"})
    assert status == 400 and error["state"] == "workspace_handback_status_unknown"
    for path in ("/api/workspace/handback/confirm", "/api/workspace/handback/opened"):
        assert _request_with_headers(base, "POST", path, {"Host": base.removeprefix("http://"), "Origin": "https://example.com",
                                                          "Content-Length": "2"}, b"{}")[0] == 403
    before = (root / "map.md").read_bytes()
    httpd.set_served(dataclasses.replace(httpd.get_served(), read_only=True))
    assert _post(base, "/api/workspace/handback/confirm", body)[0] == 409
    assert (root / "map.md").read_bytes() == before

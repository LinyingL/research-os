"""Research OS V16, task B1-1 (DESIGN.md 21.1, 21.4 'The task form'): an attempt holds its tasks.

The engine: `research/tasks.json` tasks gain `attempt` and `after`; a new task without an attempt is refused with a
sentence that offers both ways in, an unknown attempt or an unknown, self or circular `after` is refused, old tasks
without them stay valid and readable, a member's task may name the member's own unproposed attempt row and proposing
that attempt carries its tasks. The desk payload: each attempt's task counts, its tasks in `after` order with their
states, and the first line of its latest handback. Every test names its scenario."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos import addproject, attempt_tasks, desk, handbacks, proposals, signing, workspace
from researchos import project as project_mod

from test_v8_phase2 import _conn, _project, _scan

A16 = "attempt:map.md#16"
A17 = "attempt:map.md#17"
TWO_ROWS = ("# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n"
            "| 16 | 07-26 | TopicShift volatility (16-17) | V | t=2.91 | 🕒 |\n"
            "| 17 | 07-27 | Sword v0.2 recheck | V | r17 | 🕒 |\n")
REPORT = "research/results/" + "a" * 32 + ".md"
OTHER_REPORT = "research/results/" + "b" * 32 + ".md"


def _task(tid: str, **fields) -> dict:
    return {"id": tid, "text": f"Task {tid}", **fields}


def _save(root: Path, tasks: list[dict], revision=...) -> dict:
    if revision is ...:
        revision = workspace.read_tasks(root)[1]
    return workspace.save_tasks(root, {"tasks": tasks, "revision": revision})


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = _project(tmp_path / "proj")
    (root / "map.md").write_text(TWO_ROWS)
    _scan(root)
    return root


def _write_raw(root: Path, text: str) -> None:
    """The task file as a hand edit or an older Research OS left it."""
    (root / workspace.TASKS_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / workspace.TASKS_PATH).write_text(text)


def _refusal(root: Path, tasks: list[dict]) -> workspace.WorkspaceError:
    before = workspace.read_tasks(root)
    with pytest.raises(workspace.WorkspaceError) as exc:
        _save(root, tasks)
    assert workspace.read_tasks(root) == before  # nothing is written by a refusal
    return exc.value


# -- the engine: what a save accepts and refuses --------------------------------------------------------------


def test_a_new_task_without_an_attempt_is_refused_with_both_ways_in_said_in_either_language(root):
    refused = _refusal(root, [_task("t1")])
    assert refused.code == "task_needs_attempt" and refused.status == 400
    for word in ("在这条尝试下加任务", "属于哪条尝试", "新开一条尝试…"):
        assert word in refused.message_zh
    for word in ("Add a task under this attempt", "Which attempt it belongs to", "Start a new attempt…"):
        assert word in str(refused)
    assert not (root / workspace.TASKS_PATH).exists()


def test_a_task_that_names_an_attempt_of_the_table_is_saved_read_and_listed_with_it(root):
    saved = _save(root, [_task("t1", attempt=A16), _task("t2", attempt=A17, after=None, owner="Zhang San")])
    assert [(t["id"], t.get("attempt"), t.get("after")) for t in saved["tasks"]] == [("t1", A16, None), ("t2", A17, None)]
    on_disk = json.loads((root / workspace.TASKS_PATH).read_text())
    assert on_disk[0]["attempt"] == A16 and "after" not in on_disk[1]  # written only when set
    assert workspace.overview(root)["tasks"] == saved["tasks"]


def test_an_attempt_that_is_not_in_the_table_is_refused_and_so_is_a_malformed_one(root):
    refused = _refusal(root, [_task("t1", attempt="attempt:map.md#99")])
    assert refused.code == "task_attempt_unknown" and "属于哪条尝试" in refused.message_zh
    for bad in (7, "x" * 2000, "line\nbreak"):
        with pytest.raises(workspace.WorkspaceError) as exc:
            _save(root, [_task("t1", attempt=bad)])
        assert exc.value.code == "invalid"


def test_after_names_another_task_of_the_list_and_never_itself_or_a_circle(root):
    first = _save(root, [_task("a", attempt=A16), _task("b", attempt=A16, after="a")])
    assert first["tasks"][1]["after"] == "a"
    for tasks, code, said in (
            ([_task("a", attempt=A16, after="a")], "task_after_self", "一个任务不能接着它自己"),
            ([_task("a", attempt=A16), _task("b", attempt=A16, after="gone")], "task_after_unknown", "接着哪个任务"),
            ([_task("a", attempt=A16, after="b"), _task("b", attempt=A16, after="a")], "task_after_cycle", "接着哪个任务"),
            ([_task("a", attempt=A16, after="c"), _task("b", attempt=A16, after="a"),
              _task("c", attempt=A16, after="b")], "task_after_cycle", "接着哪个任务")):
        refused = _refusal(root, tasks)
        assert refused.code == code and said in refused.message_zh and str(refused).endswith((".", "again."))
    with pytest.raises(workspace.WorkspaceError) as exc:
        _save(root, [_task("a", attempt=A16, after=" no spaces ")])
    assert exc.value.code == "invalid"


def test_tasks_from_before_21_stay_valid_and_a_save_that_leaves_them_as_they_are_is_not_refused(root):
    old = [{"id": "old", "text": "Written before attempts", "done": False, "owner": "", "due": ""}]
    _write_raw(root, json.dumps(old))
    assert workspace.read_tasks(root)[0] == old
    assert workspace.overview(root)["tasks"] == old and workspace.overview(root)["tasks_error"] is None
    done = _save(root, [{**old[0], "done": True}])  # a done mark on an old task needs no attempt
    assert done["tasks"][0]["done"] is True and "attempt" not in done["tasks"][0]
    _refusal(root, [{**old[0]}, _task("fresh")])  # but a new task beside it does
    both = _save(root, [{**old[0]}, _task("fresh", attempt=A17)])
    assert [t.get("attempt") for t in both["tasks"]] == [None, A17]


def test_a_task_whose_attempt_row_was_removed_still_saves_as_it_is_and_a_removed_task_leaves_no_refusal_behind(root):
    saved = _save(root, [_task("a", attempt=A17), _task("b", attempt=A17, after="a")])
    (root / "map.md").write_text(TWO_ROWS.replace("| 17 | 07-27 | Sword v0.2 recheck | V | r17 | 🕒 |\n", ""))
    _scan(root)
    assert A17 not in {row["id"] for row in workspace._attempts(root)}
    marked = _save(root, [{**saved["tasks"][0], "done": True}, saved["tasks"][1]])  # unchanged links: not looked at again
    assert marked["tasks"][0]["done"] is True
    # `a` is removed and `b` still follows it: the list reads and saves as it is; changing `after` is checked again
    left = _save(root, [marked["tasks"][1]])
    assert left["tasks"][0]["after"] == "a"
    assert _refusal(root, [{**left["tasks"][0], "after": "other"}]).code == "task_after_unknown"
    assert _refusal(root, [_task("c", attempt=A17)]).code == "task_attempt_unknown"  # a new link to it is not


def test_the_revision_check_the_write_guard_and_the_history_of_a_task_save_are_unchanged(root):
    first = _save(root, [_task("a", attempt=A16)])
    with pytest.raises(workspace.WorkspaceError) as exc:
        _save(root, [_task("a", attempt=A16), _task("b", attempt=A17)], revision=None)
    assert exc.value.code == "conflict"
    second = _save(root, [_task("a", attempt=A16), _task("b", attempt=A17)], revision=first["tasks_revision"])
    assert second["backup"] and (root / second["backup"]).read_text().count('"attempt"') == 1
    # the attempt link survives the done mark an agent's completion writes (mcp_server: {**t, done, report})
    marked = [{**t, "done": True, "report": REPORT} if t["id"] == "a" else t for t in second["tasks"]]
    (root / REPORT).parent.mkdir(parents=True, exist_ok=True)
    (root / REPORT).write_text("# Done\n")
    third = _save(root, marked, revision=second["tasks_revision"])
    assert third["tasks"][0]["attempt"] == A16 and third["tasks"][0]["report"] == REPORT


# -- order, states --------------------------------------------------------------------------------------------


def test_tasks_of_an_attempt_are_listed_in_after_order_and_a_circle_written_by_hand_is_not_followed():
    tasks = [_task("a"), _task("x"), _task("b", after="a"), _task("c", after="b"), _task("d", after="x"), _task("e", after="a")]
    assert [t["id"] for t in attempt_tasks.ordered(tasks)] == ["a", "b", "c", "e", "x", "d"]
    assert [t["id"] for t in attempt_tasks.ordered([_task("b", after="a"), _task("a")])] == ["a", "b"]  # a follower written first
    assert [t["id"] for t in attempt_tasks.ordered([_task("a", after="zzz"), _task("b")])] == ["a", "b"]  # one that follows no task here
    circle = [_task("p", after="r"), _task("q", after="p"), _task("r", after="q"), _task("s")]
    assert sorted(t["id"] for t in attempt_tasks.ordered(circle)) == ["p", "q", "r", "s"]
    assert attempt_tasks.ordered(circle)[0]["id"] == "s"


@pytest.mark.parametrize("task, run, waiting, state", [
    ({}, None, False, "not_started"),
    ({"done": True}, None, False, "done"),
    ({"done": True}, {"state": "done"}, True, "to_read"),
    ({}, {"state": "running"}, True, "running"),
    ({}, {"state": "preparing"}, False, "running"),
    ({}, {"state": "needs_input"}, False, "needs_answer"),
    ({}, {"state": "out_of_bounds", "outcome": "done"}, False, "out_of_bounds"),
    ({}, {"state": "failed"}, False, "failed"),
    ({}, {"state": "interrupted"}, False, "failed"),
    ({}, {"state": "ended"}, False, "failed"),
    ({}, {"state": "stopped"}, False, "not_started"),
    ({"done": True}, {"state": "failed"}, False, "done"),
    ({"done": True}, {"state": "out_of_bounds", "outcome": "done"}, False, "out_of_bounds"),   # the done mark stays the agent's; the run is what warns (18.1 item 4)
    ({"done": True}, {"state": "out_of_bounds", "outcome": "done"}, True, "out_of_bounds"),    # and an unopened handback does not hide it
    # a task whose report is in but that was never marked done (older versions did not set the mark): it has handed back
    ({"report": REPORT}, None, False, "done"),                                                  # a copy or another Mac has no run records
    ({"report": REPORT}, {"state": "done"}, False, "done"),                                     # the latest run is the one that handed it back
    ({"report": REPORT}, {"state": "done"}, True, "to_read"),                                   # until its handback has been opened
    ({"report": REPORT}, {"state": "done", "reverted": True}, False, "not_started"),            # a run that was undone handed nothing back
    ({"report": REPORT}, {"state": "stopped"}, False, "not_started"),                           # the person stopped a later run: it is not the one that handed back
    ({"report": REPORT}, {"state": "failed"}, False, "failed"),                                 # a later run that failed still reads 失败 beside an older report
    ({"report": REPORT}, {"state": "interrupted"}, False, "failed"),
    ({"report": REPORT}, {"state": "ended"}, False, "failed"),
    ({"report": REPORT}, {"state": "running"}, False, "running"),
    ({"report": REPORT}, {"state": "needs_input"}, False, "needs_answer"),
    ({"report": REPORT}, {"state": "out_of_bounds", "outcome": "done"}, False, "out_of_bounds"),
    ({"report": ""}, {"state": "done"}, False, "not_started"),                                   # no report in: nothing was handed back
    ({"report": None}, None, False, "not_started"),
])
def test_a_tasks_state_comes_from_its_done_mark_its_latest_run_and_whether_its_handback_waits(task, run, waiting, state):
    assert attempt_tasks.state_of({"id": "t", **task}, run, waiting) == state


# -- the desk payload -----------------------------------------------------------------------------------------


def _payload(root: Path, lang: str = "zh") -> dict:
    conn = _conn(root)
    try:
        return desk.payload(conn, root, lang=lang)
    finally:
        conn.close()


def _work(payload: dict, attempt: str) -> dict:
    return next(a for a in payload["attempts"] if a["id"] == attempt)["work"]


def _report(root: Path, name: str, title: str, headline=None, mtime=None) -> str:
    rel = f"research/results/{name * 32}.md"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(f"# {title}\n\nBody\n")
    if headline is not None:
        (root / rel.removesuffix(".md")).with_suffix(".context.json").write_text(
            json.dumps({"version": 1, "context": {"headline": headline}}))
    if mtime is not None:
        import os
        os.utime(root / rel, (mtime, mtime))
    return rel


def test_the_desk_says_how_many_tasks_an_attempt_holds_how_many_are_unfinished_and_lists_them_in_after_order(root):
    _save(root, [_task("a", attempt=A16, owner="agent:claude-code:sonnet"), _task("c", attempt=A16),
                 _task("b", attempt=A16, after="a", owner="Zhang San", due="2026-11-02"), _task("z", attempt=A17)])
    _save(root, [t | ({"done": True} if t["id"] == "z" else {}) for t in workspace.read_tasks(root)[0]])
    payload = _payload(root)
    work = _work(payload, A16)
    assert (work["tasks"], work["unfinished"], work["unopened"], work["latest"]) == (3, 3, 0, None)
    assert [(i["id"], i["state"], i["after"]) for i in work["items"]] == [("a", "not_started", None), ("b", "not_started", "a"),
                                                                          ("c", "not_started", None)]
    assert [i["owner_label"] for i in work["items"]] == ["Claude Code · sonnet", "Zhang San", ""]
    assert work["items"][1]["due"] == "2026-11-02" and work["items"][0]["text"] == "Task a"
    other = _work(payload, A17)
    assert (other["tasks"], other["unfinished"]) == (1, 0) and other["items"][0]["state"] == "done"


def test_an_attempt_without_tasks_and_a_project_without_a_task_file_have_an_empty_work_and_the_map_still_draws(root):
    payload = _payload(root)
    assert {a["id"]: a["work"] for a in payload["attempts"]} == {A16: desk.NO_WORK, A17: desk.NO_WORK}
    _write_raw(root, "not json")  # an unreadable task file leaves the work out, not the map
    assert all(a["work"]["tasks"] == 0 for a in _payload(root)["attempts"])


def test_the_latest_handback_is_the_report_written_last_and_its_first_line_is_the_headline_else_the_title(root):
    old = _report(root, "a", "Older report", mtime=1_700_000_000)
    new = _report(root, "b", "Newer report", headline="Sword v0.2 beats v0.1 on every month.\nSecond line.", mtime=1_800_000_000)
    _save(root, [_task("a", attempt=A16, done=True, report=old), _task("b", attempt=A16, done=True, report=new, after="a")])
    latest = _work(_payload(root), A16)["latest"]
    assert latest == {"report": new, "title": "Newer report", "headline": "Sword v0.2 beats v0.1 on every month.",
                      "line": "Sword v0.2 beats v0.1 on every month.", "unopened": False}
    (root / new.removesuffix(".md")).with_suffix(".context.json").unlink()
    latest = _work(_payload(root), A16)["latest"]
    assert latest["headline"] is None and latest["line"] == "Newer report"  # no headline: the report's title
    (root / new.removesuffix(".md")).with_suffix(".context.json").write_text('{"version": 1, "context": {"headline": 7}}')
    assert _work(_payload(root), A16)["latest"]["line"] == "Newer report"  # a headline that is no text is no headline
    (root / new).unlink()
    assert _work(_payload(root), A16)["latest"]["report"] == old  # a report that is gone is not the latest


def test_an_unopened_handback_is_counted_on_its_attempt_and_waits_as_to_read(root, monkeypatch):
    report = _report(root, "a", "Sword v0.2 recheck")
    _save(root, [_task("a", attempt=A16, done=True, report=report), _task("b", attempt=A16)])
    seen = []
    with monkeypatch.context() as local:
        local.setattr(handbacks, "unopened", lambda project_id, person, root=None: seen.append((project_id, person)) or [report])
        work = _work(_payload(root), A16)
    assert (work["tasks"], work["unfinished"], work["unopened"]) == (2, 1, 1)
    assert [(i["id"], i["state"], i["unopened"]) for i in work["items"]] == [("a", "to_read", True), ("b", "not_started", False)]
    assert work["latest"]["unopened"] is True
    assert seen and seen[0][0] and seen[0][1] is None  # this project's id; no person is set on this machine
    assert handbacks.unopened("p", "me") == []  # a project id that is no project's has no run folder, so nothing waits (the store itself: B1-3)
    assert _work(_payload(root), A16)["unopened"] == 0


def test_a_tasks_latest_run_decides_how_it_reads_and_only_unfinished_waiting_or_agents_tasks_are_looked_up(root, monkeypatch):
    agent = "agent:claude-code:sonnet"
    _save(root, [_task(n, attempt=A16) for n in "abcde"] + [_task("f", attempt=A16, done=True), _task("g", attempt=A16, done=True, owner=agent),
                                                         _task("h", attempt=A16, done=True, owner="Zhang San")])
    wanted = []
    runs = {"a": {"state": "running"}, "b": {"state": "needs_input"}, "c": {"state": "out_of_bounds", "outcome": "done"},
            "d": {"state": "failed"}, "g": {"state": "out_of_bounds", "outcome": "done"}}
    monkeypatch.setattr(attempt_tasks, "_latest_runs", lambda project_id, ids: wanted.append(ids) or {k: v for k, v in runs.items() if k in ids})
    states = {i["id"]: i["state"] for i in _work(_payload(root), A16)["items"]}
    assert states == {"a": "running", "b": "needs_answer", "c": "out_of_bounds", "d": "failed", "e": "not_started", "f": "done",
                      "g": "out_of_bounds", "h": "done"}   # a finished task of an agent is looked at, for the run may have gone out of bounds
    assert wanted == [{"a", "b", "c", "d", "e", "g"}]


def test_the_latest_run_of_each_task_is_read_from_the_run_store_newest_first(root):
    from researchos.agents import runs as run_store
    from researchos.records.identity import read_identity

    pid = read_identity(root).identity.id
    _save(root, [_task("a", attempt=A16), _task("b", attempt=A16), _task("c", attempt=A16)])
    for n, (task, state) in enumerate([("a", "failed"), ("a", "needs_input"), ("b", "running"), ("c", "stopped")]):
        run_store.create_run(pid, {"id": f"r-20261029T10000{n}Z-aaaaa{n}", "task_id": task, "state": state})
    found = attempt_tasks._latest_runs(pid, {"a", "b"})
    assert {task: run["state"] for task, run in found.items()} == {"a": "needs_input", "b": "running"}
    assert attempt_tasks._latest_runs(None, {"a"}) == {} and attempt_tasks._latest_runs(pid, set()) == {}
    assert {i["id"]: i["state"] for i in _work(_payload(root), A16)["items"]} == {
        "a": "needs_answer", "b": "running", "c": "not_started"}


def _quant_like(root: Path) -> list[str]:
    """Quant as the researcher's copy has it: five tasks under one attempt, each with its report in and none marked done
    (older versions did not set the mark); the reports predate the handback's fields, one has a Section 20 context
    with a finding and no 21.2 field; one more task under another attempt that was never run."""
    reports = [_report(root, name, f"Report {name}") for name in "abcde"]
    (root / reports[4].removesuffix(".md")).with_suffix(".context.json").write_text(
        json.dumps({"version": 1, "context": {"finding": "Sword v0.2 on every member", "limits": "One data vendor"}}))
    _save(root, [_task(n, attempt=A16, report=report, after=None if n == "a" else chr(ord(n) - 1)) for n, report in zip("abcde", reports)]
          + [_task("z", attempt=A17)])
    return reports


def test_a_task_that_has_handed_back_reads_finished_and_the_card_counts_only_what_is_not(root):
    # a copy or another Mac has no run records at all
    _quant_like(root)
    work = _work(_payload(root), A16)
    assert (work["tasks"], work["unfinished"], work["unopened"]) == (5, 0, 0)
    assert [i["state"] for i in work["items"]] == ["done"] * 5 and [i["done"] for i in work["items"]] == [False] * 5
    other = _work(_payload(root), A17)
    assert (other["tasks"], other["unfinished"]) == (1, 1) and other["items"][0]["state"] == "not_started"


def test_with_run_records_the_latest_run_decides_whether_a_report_still_counts_as_the_tasks_answer(root):
    from researchos.agents import runs as run_store
    from researchos.records.identity import read_identity

    pid = read_identity(root).identity.id
    reports = _quant_like(root)
    for n, (task, report) in enumerate(zip("abcde", reports)):   # every run in the store ended in state done
        run_store.create_run(pid, {"id": f"r-20261029T10000{n}Z-aaaaa{n}", "task_id": task, "state": "done", "report": report})
    work = _work(_payload(root), A16)
    assert (work["unfinished"], [i["state"] for i in work["items"]]) == (0, ["done"] * 5)
    # later runs: one failed, one the person stopped, one still going, one that was undone
    run_store.create_run(pid, {"id": "r-20261029T110001Z-bbbbb1", "task_id": "b", "state": "failed"})
    run_store.create_run(pid, {"id": "r-20261029T110002Z-bbbbb2", "task_id": "c", "state": "stopped"})
    run_store.create_run(pid, {"id": "r-20261029T110003Z-bbbbb3", "task_id": "d", "state": "running"})
    run_store.update_run(pid, "r-20261029T100004Z-aaaaa4", reverted=True)
    work = _work(_payload(root), A16)
    assert {i["id"]: i["state"] for i in work["items"]} == {"a": "done", "b": "failed", "c": "not_started", "d": "running", "e": "not_started"}
    assert work["unfinished"] == 4                    # only what reads 已完成 is left out of 未完成
    assert work["tasks"] == 5 and work["latest"]["report"] == reports[4]


# -- the team: a member's task may name the member's own unproposed attempt, and proposing it carries its tasks --


needs_ssh = pytest.mark.skipif(signing.tool() is None, reason="ssh-keygen is not installed here")


@needs_ssh
def test_a_members_task_names_the_members_own_unproposed_attempt_and_proposing_the_attempt_carries_its_tasks(tmp_path, monkeypatch):
    from test_v8_phase4 import Drive, _member_new_attempt

    monkeypatch.setattr(addproject, "_top_level_folders", lambda: set())
    drive = Drive(tmp_path, monkeypatch)
    new = "attempt:map.md#18"
    with drive.member():
        project_mod.open_project(drive.work)
        _member_new_attempt(drive)
        assert _refusal(drive.work, [_task("t1", attempt=new)]).code == "task_attempt_unknown"  # not indexed yet
        _scan(drive.work)
        saved = _save(drive.work, [_task("t1", attempt=new), _task("t2", attempt=new, after="t1")])
        assert [t["attempt"] for t in saved["tasks"]] == [new, new]
        plain = proposals.make_proposal(drive.work, "attempt_new", "Add attempt 18", number="18")
        carried = {item["path"] for item in plain.data["file"]}
        assert workspace.TASKS_PATH in carried and "steps/18-big.py" in carried
        assert next(c for c in plain.preview["candidates"] if c["path"] == workspace.TASKS_PATH)["linked"] is True
        left = proposals.make_proposal(drive.work, "attempt_new", "Add attempt 18", number="18", leave=[workspace.TASKS_PATH])
        assert workspace.TASKS_PATH not in {item["path"] for item in left.data["file"]}  # the member may leave it out
        stored = proposals.write_proposal(drive.work, plain)
    with drive.pi():
        assert not (drive.official / workspace.TASKS_PATH).exists()
        proposals.approve(drive.official, stored.id)
        official = workspace.read_tasks(drive.official)[0]
        assert [(t["id"], t["attempt"], t.get("after")) for t in official] == [("t1", new, None), ("t2", new, "t1")]
        assert new in {row["id"] for row in workspace._attempts(drive.official)}
    with drive.member():
        # an attempt no task names carries no task file
        (drive.work / "map.md").write_text((drive.work / "map.md").read_text()
                                           + "| 19 | 08-03 | Another idea (19) | V | r19 | 🕒 |\n")
        _scan(drive.work)
        quiet = proposals.make_proposal(drive.work, "attempt_new", "Add attempt 19", number="19")
        assert workspace.TASKS_PATH not in {item["path"] for item in quiet.data["file"]}


@needs_ssh
def test_a_members_task_with_a_result_record_takes_the_record_along_when_its_attempt_is_proposed(tmp_path, monkeypatch):
    from test_v8_phase4 import Drive, _member_new_attempt

    monkeypatch.setattr(addproject, "_top_level_folders", lambda: set())
    drive = Drive(tmp_path, monkeypatch)
    new = "attempt:map.md#18"
    with drive.member():
        project_mod.open_project(drive.work)
        _member_new_attempt(drive)
        _scan(drive.work)
        record = workspace.save_document(drive.work, {"kind": "result", "title": "Run 18", "content": "Found it.", "revision": None})
        saved = _save(drive.work, [_task("t1", attempt=new, done=True, report=record["path"])])
        assert saved["changed_task_reports"] == [record["path"]]
        draft = proposals.make_proposal(drive.work, "attempt_new", "Add attempt 18 with its result", number="18")
        assert {workspace.TASKS_PATH, record["path"]} <= {item["path"] for item in draft.data["file"]}

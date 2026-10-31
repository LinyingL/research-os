"""Package D of DESIGN.md Section 17: the MCP server's agent mode -- the three
tools an AI agent reports through (`researchos_complete_task`, `researchos_ask`,
`researchos_save_note`), the read-only graph tools beside them, the argument
checks of `researchos mcp --agent-run`, and one end-to-end run of the exact
command the runners hand to the agent's program (`runners.mcp_command`) over
a real stdio session."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
from pathlib import Path

import pytest

from researchos import history, mcp_server, paths, project, workspace
from researchos.agents import labels, runners, runs
from researchos.records import lock as records_lock
from researchos.records import notes as notes_mod
from researchos.records import situation
from researchos.records.identity import read_identity

from attempt_seed import seed_attempt

AGENT = labels.Agent("claude-code", "sonnet")
TASK_ID = "t1"
TASK_TEXT = "Clean the survey data\nand tabulate it by region"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    return root


@pytest.fixture
def pid(root: Path) -> str:
    return read_identity(root).identity.id


def _tasks(root: Path, *, done: bool = False, report: str = "", owner: str = AGENT.owner) -> None:
    attempt = seed_attempt(root)  # 21.1: a task belongs to an attempt
    task = {"id": TASK_ID, "text": TASK_TEXT, "done": done, "owner": owner, "due": "2026-11-01", "attempt": attempt}
    if report:
        task["report"] = report
    other = {"id": "t2", "text": "Another task", "done": False, "owner": "", "due": "", "attempt": attempt}
    got = workspace.read_tasks(root)
    workspace.save_tasks(root, {"tasks": [task, other], "revision": got[1]})


def _run(root: Path, pid: str, *, state: str = "running", lang: str = "zh", by: str | None = "m-1",
         by_name: str | None = "Dr. Li", task_id: str = TASK_ID) -> str:
    run_id = runs.new_run_id()
    runs.create_run(pid, {
        "id": run_id, "project_id": pid, "root": str(root), "task_id": task_id, "task_text": TASK_TEXT,
        "runner": AGENT.runner, "model": AGENT.model, "owner": AGENT.owner, "label": AGENT.label,
        "by": by, "by_name": by_name, "lang": lang, "state": state,
    })
    return run_id


@pytest.fixture
def setup(root, pid):
    _tasks(root)
    return root, pid, _run(root, pid)


def _read_tasks(root: Path) -> list[dict]:
    return json.loads((root / "research" / "tasks.json").read_text("utf-8"))


def _task(root: Path, tid: str = TASK_ID) -> dict:
    return next(t for t in _read_tasks(root) if t["id"] == tid)


def _versions(pid: str, root: Path, rel: str):
    with history.Store.open(pid) as store:
        return store.versions(notes_mod.normalize_node(rel))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


REPORT = "## What was done\n\nCleaned `survey.csv` into `survey_clean.csv`.\n\nMean income: 41,203.5"


# -- researchos_complete_task -----------------------------------------------------------------------


def test_complete_task_writes_result_marks_done_and_signals(setup):
    root, pid, run_id = setup
    answer = mcp_server.agent_complete_task(root, pid, run_id, REPORT)
    task = _task(root)
    assert task["done"] is True
    assert re.fullmatch(r"research/results/[0-9a-f]{32}\.md", task["report"])
    assert task["report"] in answer and "marked the task done" in answer and "stop now" in answer
    assert _task(root, "t2") == {"id": "t2", "text": "Another task", "done": False, "owner": "", "due": "",
                                    "attempt": seed_attempt(root)}  # the other task is as it was

    text = (root / task["report"]).read_text("utf-8")
    lines = text.split("\n")
    assert lines[0] == "# Clean the survey data"  # the first line of the task text
    assert lines[1] == ""
    assert re.fullmatch(
        rf"> 由 Claude Code · sonnet 完成 · \d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}} · Dr\. Li 指派 · 运行 {run_id}",
        lines[2],
    )
    assert lines[3] == "" and text.endswith(REPORT + "\n")

    [signal] = runs.read_signals(pid, run_id)
    assert signal["kind"] == "complete" and signal["path"] == task["report"]
    assert signal["tasks_sha"] == _sha(root / "research" / "tasks.json")
    assert signal["paths"] == [task["report"], "research/tasks.json"]


def test_complete_task_english_line_title_and_no_assigner(root, pid):
    _tasks(root)
    run_id = _run(root, pid, lang="en", by_name=None)
    mcp_server.agent_complete_task(root, pid, run_id, REPORT, title="  Survey\n cleaning # done ")
    text = (root / _task(root)["report"]).read_text("utf-8")
    lines = text.split("\n")
    assert lines[0] == "# Survey cleaning # done"
    assert re.fullmatch(
        rf"> Done by Claude Code · sonnet · \d{{1,2}} [A-Z][a-z]{{2}} \d{{4}} \d{{2}}:\d{{2}} · run {run_id}", lines[2],
    )


def test_complete_task_english_line_with_assigner(root, pid):
    _tasks(root)
    run_id = _run(root, pid, lang="en", by_name="Ana\nPérez")
    mcp_server.agent_complete_task(root, pid, run_id, REPORT)
    line = (root / _task(root)["report"]).read_text("utf-8").split("\n")[2]
    assert line.endswith(f" · assigned by Ana Pérez · run {run_id}")


def test_complete_task_is_attributed_to_the_agent_in_the_history(setup):
    root, pid, run_id = setup
    mcp_server.agent_complete_task(root, pid, run_id, REPORT)
    report = _task(root)["report"]
    who = labels.who_value(AGENT, "m-1")
    for rel in (report, "research/tasks.json"):
        agent_versions = [v for v in _versions(pid, root, rel) if v.source == "agent"]
        assert agent_versions, rel
        assert agent_versions[-1].who == who and agent_versions[-1].note == run_id


def test_complete_task_without_a_person_omits_the_hash(root, pid):
    _tasks(root)
    run_id = _run(root, pid, by=None, by_name=None)
    mcp_server.agent_complete_task(root, pid, run_id, REPORT)
    versions = [v for v in _versions(pid, root, _task(root)["report"]) if v.source == "agent"]
    assert versions[-1].who == AGENT.owner


def test_complete_task_twice_rewrites_the_same_record(setup):
    root, pid, run_id = setup
    mcp_server.agent_complete_task(root, pid, run_id, REPORT)
    first = _task(root)["report"]
    answer = mcp_server.agent_complete_task(root, pid, run_id, REPORT + "\n\nA second paragraph.")
    assert _task(root)["report"] == first and first in answer
    assert len(list((root / "research" / "results").glob("*.md"))) == 1
    assert "A second paragraph." in (root / first).read_text("utf-8")
    assert [s["kind"] for s in runs.read_signals(pid, run_id)] == ["complete", "complete"]
    # the last signal names the tasks.json that is on disk now
    assert runs.read_signals(pid, run_id)[-1]["tasks_sha"] == _sha(root / "research" / "tasks.json")


def test_complete_task_after_send_back_reuses_the_report_a_new_run_wrote(setup):
    root, pid, run_id = setup
    mcp_server.agent_complete_task(root, pid, run_id, REPORT)
    report = _task(root)["report"]
    # 打回: the engine clears the done mark and keeps the report
    _tasks(root, done=False, report=report)
    second = _run(root, pid)
    mcp_server.agent_complete_task(root, pid, second, "Revised report")
    assert _task(root) == {**_task(root), "done": True, "report": report}
    assert "Revised report" in (root / report).read_text("utf-8")
    assert len(list((root / "research" / "results").glob("*.md"))) == 1


def test_complete_task_with_a_missing_report_file_writes_a_new_one(root, pid):
    ghost = "research/results/" + "cd" * 16 + ".md"
    _tasks(root, report=ghost)
    run_id = _run(root, pid)
    mcp_server.agent_complete_task(root, pid, run_id, REPORT)
    new = _task(root)["report"]
    assert new != ghost and (root / new).is_file() and not (root / ghost).exists()


def test_complete_task_title_falls_back_when_the_task_text_is_blank_headed(root, pid):
    _tasks(root)
    run_id = _run(root, pid)
    mcp_server.agent_complete_task(root, pid, run_id, REPORT, title="x" * 500)
    first = (root / _task(root)["report"]).read_text("utf-8").split("\n")[0]
    assert first == "# " + "x" * 200


@pytest.mark.parametrize("report", ["", "   \n\t ", None])
def test_complete_task_refuses_an_empty_report(setup, report):
    root, pid, run_id = setup
    before = (root / "research" / "tasks.json").read_bytes()
    answer = mcp_server.agent_complete_task(root, pid, run_id, report)
    assert answer.startswith("Not written: ") and "empty" in answer
    assert (root / "research" / "tasks.json").read_bytes() == before
    assert runs.read_signals(pid, run_id) == []


def test_complete_task_refuses_an_oversized_report_and_writes_nothing(setup):
    root, pid, run_id = setup
    before = (root / "research" / "tasks.json").read_bytes()
    answer = mcp_server.agent_complete_task(root, pid, run_id, "x" * (workspace.TEXT_LIMIT + 1))
    assert answer.startswith("Not written: ") and "shorten" in answer
    assert (root / "research" / "tasks.json").read_bytes() == before
    assert not (root / "research" / "results").exists() or not list((root / "research" / "results").glob("*.md"))
    assert runs.read_signals(pid, run_id) == []


def test_complete_task_report_just_under_the_limit_is_saved_or_refused_cleanly(setup):
    root, pid, run_id = setup
    answer = mcp_server.agent_complete_task(root, pid, run_id, "x" * (workspace.TEXT_LIMIT - 5000))
    assert answer.startswith("Saved the report")


def test_complete_task_report_with_nul_is_refused(setup):
    root, pid, run_id = setup
    answer = mcp_server.agent_complete_task(root, pid, run_id, "abc\0def")
    assert answer.startswith("Not written: ")
    assert runs.read_signals(pid, run_id) == []


def test_complete_task_when_the_task_is_gone(root, pid):
    _tasks(root)
    run_id = _run(root, pid, task_id="gone")
    answer = mcp_server.agent_complete_task(root, pid, run_id, REPORT)
    assert answer.startswith("Not written: ") and "'gone'" in answer
    assert runs.read_signals(pid, run_id) == []
    assert not _task(root).get("report")


@pytest.mark.parametrize("state", ["done", "stopped", "failed", "interrupted", "needs_input", "ended"])
def test_tools_refuse_once_the_run_is_over(root, pid, state):
    _tasks(root)
    run_id = _run(root, pid, state=state)
    for answer in (
        mcp_server.agent_complete_task(root, pid, run_id, REPORT),
        mcp_server.agent_save_note(root, pid, run_id, "T", "c"),
        mcp_server.agent_ask(pid, run_id, "Q?"),
    ):
        assert answer.startswith("Not written: ") and "no longer going" in answer
    assert not _task(root)["done"] and runs.read_signals(pid, run_id) == []


@pytest.mark.parametrize("state", ["preparing", "running"])
def test_tools_work_while_the_run_is_active(root, pid, state):
    _tasks(root)
    run_id = _run(root, pid, state=state)
    assert mcp_server.agent_ask(pid, run_id, "Q?").startswith("Your question is recorded")


def test_tools_refuse_an_unknown_run(setup):
    root, pid, _ = setup
    ghost = runs.new_run_id()
    for answer in (
        mcp_server.agent_complete_task(root, pid, ghost, REPORT),
        mcp_server.agent_save_note(root, pid, ghost, "T", "c"),
        mcp_server.agent_ask(pid, ghost, "Q?"),
    ):
        assert answer.startswith("Not written: ") and "not known" in answer
    assert mcp_server.agent_ask(pid, "not-a-run-id", "Q?").startswith("Not written: ")


def test_complete_task_refuses_a_run_whose_owner_is_not_an_agent(root, pid):
    _tasks(root)
    run_id = _run(root, pid)
    runs.update_run(pid, run_id, owner="somebody")
    assert mcp_server.agent_complete_task(root, pid, run_id, REPORT).startswith("Not written: ")


def test_complete_task_in_a_read_only_project_writes_nothing(setup, monkeypatch):
    root, pid, run_id = setup
    from researchos import proposals

    monkeypatch.setattr(proposals, "role_of", lambda _root: ("viewer", None, None))
    answer = mcp_server.agent_complete_task(root, pid, run_id, REPORT)
    assert answer.startswith("Not written: ")
    assert not _task(root)["done"] and runs.read_signals(pid, run_id) == []


def test_complete_task_reports_a_lock_that_cannot_be_taken(setup, monkeypatch):
    root, pid, run_id = setup

    def busy(*_a, **_k):
        raise records_lock.ProjectLockError("held by another process")

    monkeypatch.setattr(situation, "write_guard", busy)
    answer = mcp_server.agent_complete_task(root, pid, run_id, REPORT)
    assert answer.startswith("Not written: could not take the project lock")


def test_complete_task_names_the_record_when_the_task_could_not_be_marked(setup, monkeypatch):
    root, pid, run_id = setup

    def broken(*_a, **_k):
        raise workspace.WorkspaceError("conflict", "The file has changed since it was opened", 409)

    monkeypatch.setattr(workspace, "save_tasks", broken)
    answer = mcp_server.agent_complete_task(root, pid, run_id, REPORT)
    assert answer.startswith("Not written: the report research/results/")
    assert "call the tool again" in answer
    assert runs.read_signals(pid, run_id) == []


def test_complete_task_retries_once_when_tasks_json_moved_underneath(setup, monkeypatch):
    root, pid, run_id = setup
    real = workspace.save_tasks
    calls = []

    def flaky(root_, body, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise workspace.WorkspaceError("conflict", "moved", 409)
        return real(root_, body, **kw)

    monkeypatch.setattr(workspace, "save_tasks", flaky)
    assert mcp_server.agent_complete_task(root, pid, run_id, REPORT).startswith("Saved the report")
    assert len(calls) == 2 and _task(root)["done"] is True


# -- the guard interplay ----------------------------------------------------------------------


def test_in_an_agent_run_only_the_tools_can_write(setup, monkeypatch):
    root, pid, run_id = setup
    monkeypatch.setenv(situation.AGENT_RUN_ENV, run_id)
    # a script the agent wrote: every write through the page's own paths is refused ...
    with pytest.raises(situation.AgentRunRefused):
        workspace.save_tasks(root, {"tasks": [], "revision": workspace.read_tasks(root)[1]})
    with pytest.raises(situation.AgentRunRefused):
        workspace.save_document(root, {"kind": "note", "title": "x", "content": "y", "revision": None})
    before = (root / "research" / "tasks.json").read_bytes()
    assert not (root / "research" / "notes").exists()
    # ... the tools open it for themselves ...
    assert mcp_server.agent_complete_task(root, pid, run_id, REPORT).startswith("Saved the report")
    assert mcp_server.agent_save_note(root, pid, run_id, "A note", "text").startswith("Saved the note research/notes/")
    assert (root / "research" / "tasks.json").read_bytes() != before
    # ... and only for the length of the call
    with pytest.raises(situation.AgentRunRefused):
        workspace.save_document(root, {"kind": "note", "title": "x", "content": "y", "revision": None})


def test_the_tool_refusals_hold_in_an_agent_run_too(setup, monkeypatch):
    root, pid, run_id = setup
    monkeypatch.setenv(situation.AGENT_RUN_ENV, run_id)
    assert mcp_server.agent_complete_task(root, pid, run_id, "").startswith("Not written: ")
    assert mcp_server.agent_ask(pid, run_id, "").startswith("Not written: ")


# -- researchos_ask ---------------------------------------------------------------------------------


def test_ask_records_a_signal_and_writes_no_project_file(setup):
    root, pid, run_id = setup
    before = (root / "research" / "tasks.json").read_bytes()
    answer = mcp_server.agent_ask(pid, run_id, "  Which file is the 2024 wave?\nA or B?  ")
    assert answer == ("Your question is recorded and will be shown to the person. Stop working now; their "
                      "answer will continue this conversation.")
    [signal] = runs.read_signals(pid, run_id)
    assert signal["kind"] == "ask" and signal["question"] == "Which file is the 2024 wave?\nA or B?"
    assert (root / "research" / "tasks.json").read_bytes() == before


def test_ask_validates_the_question(setup):
    root, pid, run_id = setup
    for bad in ("", "   ", None, "q" * 4001):
        assert mcp_server.agent_ask(pid, run_id, bad).startswith("Not written: ")
    assert runs.read_signals(pid, run_id) == []
    assert mcp_server.agent_ask(pid, run_id, "q" * 4000).startswith("Your question is recorded")


def test_ask_when_the_run_folder_is_gone(setup):
    import shutil

    root, pid, run_id = setup
    shutil.rmtree(runs.run_dir(pid, run_id))
    assert mcp_server.agent_ask(pid, run_id, "Q?").startswith("Not written: ")


# -- researchos_save_note ----------------------------------------------------------------------------


def test_save_note_creates_a_note_attributed_to_the_agent(setup):
    root, pid, run_id = setup
    answer = mcp_server.agent_save_note(root, pid, run_id, "  Data caveat ", "Two respondents are duplicated.")
    [signal] = runs.read_signals(pid, run_id)
    path = signal["path"]
    assert signal["kind"] == "note" and signal["paths"] == [path]
    assert re.fullmatch(r"research/notes/[0-9a-f]{32}\.md", path) and path in answer
    assert (root / path).read_text("utf-8") == "# Data caveat\n\nTwo respondents are duplicated."
    agent_versions = [v for v in _versions(pid, root, path) if v.source == "agent"]
    assert agent_versions[-1].who == labels.who_value(AGENT, "m-1") and agent_versions[-1].note == run_id
    assert not _task(root)["done"]  # a note does not finish the task


def test_save_note_validates_title_and_size(setup):
    root, pid, run_id = setup
    for title, content in (("", "c"), ("   ", "c"), ("a\nb", "c"), ("t" * 201, "c"), (None, "c"), ("T", None),
                           ("T", "x" * (workspace.TEXT_LIMIT + 1))):
        assert mcp_server.agent_save_note(root, pid, run_id, title, content).startswith("Not written: "), (title,)
    assert runs.read_signals(pid, run_id) == []
    assert mcp_server.agent_save_note(root, pid, run_id, "t" * 200, "").startswith("Saved the note")


def test_concurrent_tool_calls_all_land(setup):
    root, pid, run_id = setup
    answers: list[str] = []

    def note(i: int) -> None:
        answers.append(mcp_server.agent_save_note(root, pid, run_id, f"Note {i}", f"body {i}"))

    threads = [threading.Thread(target=note, args=(i,)) for i in range(6)]
    threads.append(threading.Thread(target=lambda: answers.append(mcp_server.agent_complete_task(root, pid, run_id, REPORT))))
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(answers) == 7 and all(a.startswith("Saved") for a in answers), answers
    assert len(list((root / "research" / "notes").glob("*.md"))) == 6
    assert _task(root)["done"] is True
    kinds = sorted(s["kind"] for s in runs.read_signals(pid, run_id))
    assert kinds == ["complete"] + ["note"] * 6


# -- the server -------------------------------------------------------------------------------------


def _call(server, name: str, args: dict | None = None) -> str:
    content = asyncio.run(server.call_tool(name, args or {}))
    items = content[0] if isinstance(content, tuple) else content
    return items[0].text


def test_agent_server_offers_exactly_the_six_tools(setup):
    root, pid, run_id = setup
    tools = asyncio.run(mcp_server.build_agent_server(root, pid, run_id).list_tools())
    assert {t.name for t in tools} == {
        "researchos_complete_task", "researchos_ask", "researchos_save_note",
        "researchos_find", "researchos_trace", "researchos_status",
    }
    by_name = {t.name: t for t in tools}
    for tool in tools:
        assert tool.description and tool.inputSchema["type"] == "object"
    assert by_name["researchos_complete_task"].inputSchema["required"] == ["report"]
    assert set(by_name["researchos_complete_task"].inputSchema["properties"]) == {"report", "title", "outputs", "research_context"}
    assert by_name["researchos_ask"].inputSchema["required"] == ["question"]
    assert set(by_name["researchos_save_note"].inputSchema["required"]) == {"title", "content"}
    assert "researchos_confirm_edge" not in by_name
    # the plain server is unchanged
    plain = asyncio.run(mcp_server.build_server(root, pid).list_tools())
    assert {t.name for t in plain} == {"researchos_trace", "researchos_find", "researchos_status", "researchos_confirm_edge"}


def test_agent_server_tools_run_through_the_protocol_layer(setup):
    root, pid, run_id = setup
    server = mcp_server.build_agent_server(root, pid, run_id)
    assert _call(server, "researchos_ask", {"question": "Which?"}).startswith("Your question is recorded")
    assert _call(server, "researchos_save_note", {"title": "N", "content": "c"}).startswith("Saved the note")
    assert _call(server, "researchos_complete_task", {"report": REPORT, "title": "T"}).startswith("Saved the report")
    assert [s["kind"] for s in runs.read_signals(pid, run_id)] == ["ask", "note", "complete"]


def test_agent_server_read_tools_answer_without_a_graph(setup):
    root, pid, run_id = setup
    paths.graph_db_path(root).unlink()
    server = mcp_server.build_agent_server(root, pid, run_id)
    assert _call(server, "researchos_status") == "No index for this project yet."
    assert _call(server, "researchos_find", {"text": "x"}) == "No index for this project yet."
    assert _call(server, "researchos_trace", {"node_id": "figure:a.png"}) == "No index for this project yet."


def test_agent_server_read_tools_read_the_graph(setup):
    root, pid, run_id = setup
    assert paths.graph_db_path(root).exists()
    server = mcp_server.build_agent_server(root, pid, run_id)
    assert _call(server, "researchos_status").startswith("Research OS graph status:")
    assert "No nodes matching" in _call(server, "researchos_find", {"text": "zzz-not-there"})
    assert "No such node" in _call(server, "researchos_trace", {"node_id": "figure:none.png"})


def test_agent_read_tools_never_start_the_legacy_migration(setup, monkeypatch):
    root, pid, run_id = setup
    paths.graph_db_path(root).unlink()

    def boom(*_a, **_k):
        raise AssertionError("an agent run must not migrate anything")

    monkeypatch.setattr(paths, "resolve_graph_db", boom)
    server = mcp_server.build_agent_server(root, pid, run_id)
    assert _call(server, "researchos_status") == "No index for this project yet."


# -- main() --------------------------------------------------------------------------------------


def _main(root: Path, *extra: str) -> int:
    return mcp_server.main(["--path", str(root), *extra])


def test_main_agent_run_flag_needs_the_matching_environment(setup, monkeypatch, capsys):
    root, pid, run_id = setup
    monkeypatch.delenv(situation.AGENT_RUN_ENV, raising=False)
    assert _main(root, "--agent-run", run_id) == 1
    assert "does not match RESEARCHOS_AGENT_RUN=(not set)" in capsys.readouterr().err

    monkeypatch.setenv(situation.AGENT_RUN_ENV, runs.new_run_id())
    assert _main(root, "--agent-run", run_id) == 1
    assert "does not match" in capsys.readouterr().err


def test_main_in_an_agent_run_refuses_the_plain_server(setup, monkeypatch, capsys):
    root, pid, run_id = setup
    monkeypatch.setenv(situation.AGENT_RUN_ENV, run_id)
    assert _main(root) == 1
    err = capsys.readouterr().err
    assert "Error" in err and "--agent-run" in err


def test_main_rejects_a_malformed_run_id(setup, monkeypatch, capsys):
    root, _pid, _run_id = setup
    monkeypatch.setenv(situation.AGENT_RUN_ENV, "../../etc")
    assert _main(root, "--agent-run", "../../etc") == 1
    assert "not a run id" in capsys.readouterr().err


def test_main_rejects_an_unknown_run_and_a_run_of_another_folder(setup, monkeypatch, capsys, tmp_path):
    root, pid, run_id = setup
    ghost = runs.new_run_id()
    monkeypatch.setenv(situation.AGENT_RUN_ENV, ghost)
    assert _main(root, "--agent-run", ghost) == 1
    assert f"run {ghost} does not exist" in capsys.readouterr().err

    (tmp_path / "other").mkdir()
    elsewhere = _run(tmp_path / "other", pid)
    monkeypatch.setenv(situation.AGENT_RUN_ENV, elsewhere)
    assert _main(root, "--agent-run", elsewhere) == 1
    assert "belongs to another folder" in capsys.readouterr().err


def test_main_rejects_a_folder_that_is_not_a_project(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "plain"
    folder.mkdir()
    run_id = runs.new_run_id()
    monkeypatch.setenv(situation.AGENT_RUN_ENV, run_id)
    assert _main(folder, "--agent-run", run_id) == 1
    assert "Error" in capsys.readouterr().err


class _FakeServer:
    def __init__(self):
        self.ran = None

    def run(self, transport):
        self.ran = transport


def test_main_serves_the_agent_tools_without_a_graph(setup, monkeypatch):
    root, pid, run_id = setup
    paths.graph_db_path(root).unlink()  # an agent run does not need one
    monkeypatch.setenv(situation.AGENT_RUN_ENV, run_id)
    fake = _FakeServer()
    seen = {}

    def build(project_root, project_id, rid):
        seen.update(root=Path(project_root), pid=project_id, run=rid)
        return fake

    monkeypatch.setattr(mcp_server, "build_agent_server", build)
    assert _main(root, "--agent-run", run_id) == 0
    assert fake.ran == "stdio" and seen == {"root": root.resolve(), "pid": pid, "run": run_id}


def test_main_accepts_a_run_root_spelled_through_a_symlink(setup, monkeypatch, tmp_path):
    root, pid, run_id = setup
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    monkeypatch.setenv(situation.AGENT_RUN_ENV, run_id)
    fake = _FakeServer()
    monkeypatch.setattr(mcp_server, "build_agent_server", lambda *_a: fake)
    assert mcp_server.main(["--path", str(link), "--agent-run", run_id]) == 0 and fake.ran == "stdio"


def test_main_identity_check_may_write_inside_an_agent_run(setup, monkeypatch):
    """Adopting a moved project takes write_guard, which refuses in an agent run
    unless opened: main opens it for that one check."""
    root, pid, run_id = setup
    monkeypatch.setenv(situation.AGENT_RUN_ENV, run_id)
    seen = []
    real = situation.write_guard

    def spying(*a, **k):
        seen.append(situation._agent_writes.get())
        return real(*a, **k)

    def opening(path, **_k):
        with situation.write_guard(path, human=True):  # what adoption does
            pass
        return type("O", (), {"project_id": pid})()

    monkeypatch.setattr(situation, "write_guard", spying)
    monkeypatch.setattr(mcp_server.project_identity, "open_project", opening)
    monkeypatch.setattr(mcp_server, "build_agent_server", lambda *_a: _FakeServer())
    assert _main(root, "--agent-run", run_id) == 0
    assert seen == [True]


# -- end to end: the exact command the runners give the agent's program -----------------------------


@pytest.mark.slow
def test_stdio_session_with_the_runners_command(setup):
    pytest.importorskip("mcp.client.stdio")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root, pid, run_id = setup
    argv = runners.mcp_command(root, run_id)
    env = {**os.environ, "RESEARCHOS_HOME": os.environ["RESEARCHOS_HOME"], situation.AGENT_RUN_ENV: run_id}
    params = StdioServerParameters(command=argv[0], args=argv[1:], env=env)

    async def session():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                names = sorted(t.name for t in (await client.list_tools()).tools)
                ask = await client.call_tool("researchos_ask", {"question": "Which wave?"})
                note = await client.call_tool("researchos_save_note", {"title": "Caveat", "content": "Duplicates."})
                done = await client.call_tool(
                    "researchos_complete_task", {"report": REPORT, "title": "Survey cleaned"})
                status = await client.call_tool("researchos_status", {})
                return names, ask, note, done, status

    names, ask, note, done, status = asyncio.run(asyncio.wait_for(session(), 60))
    assert names == sorted([
        "researchos_complete_task", "researchos_ask", "researchos_save_note",
        "researchos_find", "researchos_trace", "researchos_status",
    ])
    assert not any(r.isError for r in (ask, note, done, status))
    assert done.content[0].text.startswith("Saved the report research/results/")
    assert status.content[0].text.startswith("Research OS graph status:")

    task = _task(root)
    assert task["done"] is True and (root / task["report"]).read_text("utf-8").startswith("# Survey cleaned\n")
    signals = runs.read_signals(pid, run_id)
    assert [s["kind"] for s in signals] == ["ask", "note", "complete"]
    assert signals[-1]["tasks_sha"] == _sha(root / "research" / "tasks.json")
    assert signals[-1]["path"] == task["report"]
    assert (root / signals[1]["path"]).is_file()
    assert [v for v in _versions(pid, root, task["report"]) if v.source == "agent"]


@pytest.mark.slow
def test_command_refuses_a_mismatched_environment(setup):
    import subprocess

    root, pid, run_id = setup
    argv = runners.mcp_command(root, run_id)
    env = {**os.environ, situation.AGENT_RUN_ENV: runs.new_run_id()}
    got = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert got.returncode == 1 and "does not match" in got.stderr
    assert got.stdout == ""  # stdout is the protocol's: nothing else is ever printed there


def test_a_result_heading_from_a_long_task_is_cut_at_a_word():
    """A task can be a paragraph; the heading made from it is a few words (17.4)."""
    long_task = "Write a Python script analysis/mean.py that reads data.csv and prints the mean of column x; run it"
    heading = mcp_server._result_title(None, long_task)
    assert len(heading) <= mcp_server.DEFAULT_TITLE_LIMIT + 1 and heading.endswith("…")
    assert long_task.startswith(heading[:-1])
    assert mcp_server._result_title("  ## Mean of x ", long_task) == "Mean of x"
    assert mcp_server._result_title(None, "Short task") == "Short task"


def test_a_report_opening_with_its_own_title_keeps_it_one_level_down(setup):
    """The result record has one top heading, the record's own (17.4)."""
    root, pid, run_id = setup
    mcp_server.agent_complete_task(root, pid, run_id, "# Mean of x\n\nThe mean is 2.5.\n#hashtag stays", title="Mean")
    text = (root / _task(root)["report"]).read_text("utf-8")
    assert [line for line in text.split("\n") if line.startswith("#")] == ["# Mean", "## Mean of x", "#hashtag stays"]

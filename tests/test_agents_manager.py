"""Package E of DESIGN.md Section 17: the run manager. The agents' programs
are fakes -- Python scripts (a `#!<python>` shebang, made executable and
registered with `profiles.set_commands`) that answer `--version`, read the
prompt on standard input, print the stream the real program prints
(stream-json for Claude Code, JSON events for OpenCode: the formats recorded
in the experiments behind `runners.py`), change files in the working folder
and report through `runs.append_signal` / the workspace the way the MCP agent
tools do (inside `situation.agent_writes_allowed()`, with the agent as the
author). What each fake does is a JSON scenario next to it."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from researchos import db, history, names, paths, project, workspace
from researchos.agents import labels, manager, profiles, snapshot
from researchos.agents import runs as run_store
from researchos.records import files as record_files
from researchos.records import notes as notes_mod
from researchos.records.identity import read_identity
from researchos.webapp import watcher as project_watcher

FAKE = r'''#!@PYTHON@
import json, os, signal, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
args = sys.argv[1:]
if "--version" in args:
    print("fake 1.0")
    sys.exit(0)
scenario = json.loads((HERE / "scenario.json").read_text())
flavor = scenario.get("flavor", "claude")
stdin_text = "" if scenario.get("no_stdin") else sys.stdin.read()
if scenario.get("ignore_sigterm"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def arg_after(*names):
    for name in names:
        if name in args:
            i = args.index(name)
            if i + 1 < len(args):
                return args[i + 1]
    return None


session = (arg_after("--session-id") or "session-" + str(os.getpid())) if flavor == "deepseek-harness" else arg_after("--session-id", "--resume") if flavor == "claude" else (arg_after("resume") or "codex_fake_" + str(os.getpid())) if flavor == "codex" else (arg_after("--session") or "ses_fake_" + str(os.getpid()))
with open(HERE / "calls.jsonl", "a") as handle:
    handle.write(json.dumps({"argv": sys.argv, "stdin": stdin_text, "cwd": os.getcwd(),
                             "env": {k: v for k, v in os.environ.items()
                                     if k.startswith(("RESEARCHOS", "RCE_", "CLAUDE", "OPENCODE", "CCR_", "ANTHROPIC_"))
                                     or k.endswith(("_TOKEN", "_SECRET", "_API_KEY"))},
                             "pid": os.getpid()}) + "\n")
if scenario.get("home"):
    # The fake plays the MCP server's tools in its own process; the real server is given the home by its entry in
    # the MCP configuration, the program itself is not (DESIGN 18.1 item 2). The call log above shows what it was given.
    os.environ["RESEARCHOS_HOME"] = scenario["home"]


def emit(obj):
    print(json.dumps(obj), flush=True)


def tool_input_key(name):
    return "filePath" if flavor == "opencode" else "file_path"


cost = 0.0
for step in scenario.get("steps", []):
    op = step["op"]
    if op == "init":
        if flavor == "deepseek-harness":
            emit({"type": "session", "sessionId": session, "cwd": os.getcwd()})
        elif flavor == "codex":
            emit({"type": "thread.started", "thread_id": session})
            emit({"type": "turn.started"})
        elif flavor == "claude":
            emit({"type": "system", "subtype": "init", "session_id": session, "cwd": os.getcwd(),
                  "model": scenario.get("model", "claude-fake-1"), "mcp_servers": []})
        else:
            emit({"type": "step_start", "timestamp": 1, "sessionID": session, "part": {"type": "step-start"}})
    elif op == "say":
        if flavor == "deepseek-harness":
            emit({"type": "text", "text": step["text"]})
        elif flavor == "codex":
            emit({"type": "item.completed", "item": {"id": "say", "type": "agent_message", "text": step["text"]}})
        elif flavor == "claude":
            emit({"type": "assistant", "session_id": session, "message": {"content": [{"type": "text", "text": step["text"]}]}})
        else:
            emit({"type": "text", "sessionID": session, "part": {"type": "text", "text": step["text"]}})
    elif op == "tool":
        if flavor == "deepseek-harness":
            emit({"type": "tool_call", "callId": "1", "tool": step["name"], "input": step["input"]})
        elif flavor == "codex":
            emit({"type": "item.completed", "item": {"id": "tool", "type": "mcp_tool_call", "server": "researchos", "tool": step["name"], "arguments": step["input"]}})
        elif flavor == "claude":
            emit({"type": "assistant", "session_id": session, "message": {"content": [
                {"type": "tool_use", "id": "toolu_1", "name": step["name"], "input": step["input"]}]}})
        else:
            emit({"type": "tool_use", "sessionID": session, "part": {"type": "tool", "tool": step["name"].lower(),
                  "state": {"status": "completed", "input": step["input"], "output": "ok"}}})
    elif op == "noise":
        print(step.get("text", "not json"), flush=True)
        print("x" * step.get("size", 0), flush=True)
    elif op == "stderr":
        sys.stderr.write(step["text"] + "\n")
        sys.stderr.flush()
    elif op == "write":
        path = Path(step["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, step.get("mode", "w")) as out:
            out.write(step["text"])
    elif op == "delete":
        os.unlink(step["path"])
    elif op == "compile":
        import py_compile
        py_compile.compile(step["path"], doraise=True)
    elif op == "mkdir":
        Path(step["path"]).mkdir(parents=True, exist_ok=True)
    elif op == "venv":
        import venv
        venv.EnvBuilder(with_pip=False, symlinks=True).create(step["path"])
    elif op == "rename":
        Path(step["to"]).parent.mkdir(parents=True, exist_ok=True)
        os.rename(step["from"], step["to"])
    elif op == "wait_file":
        end = time.time() + step.get("timeout", 30)
        while not os.path.exists(step["path"]) and time.time() < end:
            time.sleep(0.05)
    elif op == "sleep":
        time.sleep(step["s"])
    elif op == "spawn":
        code = ("import sys, time\nwhile True:\n    print('chatter', flush=True)\n    time.sleep(0.03)"
                if step.get("chatty") else "import time; time.sleep(600)")
        child = subprocess.Popen([sys.executable, "-c", code],
                                 start_new_session=step.get("new_session", True),
                                 stdout=None if step.get("inherit_stdout") else subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL)
        Path(step["pidfile"]).write_text(str(child.pid))
    elif op == "pidfile":
        Path(step["pidfile"]).write_text(str(os.getpid()))
    elif op == "ask":
        from researchos.agents import runs
        runs.append_signal(scenario["project_id"], os.environ["RESEARCHOS_AGENT_RUN"], "ask", question=step["question"])
    elif op == "refusal":
        from researchos.agents import guard, runs
        reason = guard.Refusal(step["reason"], step["code"]) if step.get("code") else step["reason"]
        guard.log_refusal(runs.run_dir(scenario["project_id"], os.environ["RESEARCHOS_AGENT_RUN"]), "Bash", step["target"], reason)
    elif op == "complete":
        # what researchos_complete_task does: a result record, the task done, attributed to the agent
        from researchos import workspace
        from researchos.agents import labels, runs
        from researchos.records import situation
        run_id = os.environ["RESEARCHOS_AGENT_RUN"]
        root = Path(os.getcwd())
        agent = labels.Agent(*scenario["agent"])
        author = {"source": "agent", "who": labels.who_value(agent, scenario.get("by")), "note": run_id}
        with situation.agent_writes_allowed():
            tasks, revision = workspace.read_tasks(root)
            task = next(t for t in tasks if t["id"] == scenario["task_id"])
            content = "# Result\n\n" + step.get("report", "Done.") + "\n"
            if task.get("report"):
                current = workspace.document(root, task["report"])
                doc = workspace.save_document(root, {"path": task["report"], "content": content,
                                                     "revision": current["revision"]}, author=author)
            else:
                doc = workspace.save_document(root, {"kind": "result", "content": content, "revision": None}, author=author)
            task = dict(task, done=True, report=doc["path"])
            saved = workspace.save_tasks(root, {"tasks": [task if t["id"] == task["id"] else t for t in tasks],
                                                "revision": revision}, author=author)
        runs.append_signal(scenario["project_id"], run_id, "complete", path=doc["path"],
                           tasks_sha=saved["tasks_revision"], paths=[doc["path"], "research/tasks.json"])
    elif op == "result":
        cost = step.get("cost", 0.0123)
        if flavor == "deepseek-harness":
            reason = {"kind": "error", "message": step.get("text", "boom")} if step.get("is_error") else {"kind": "completed"}
            emit({"type": "final", "sessionId": session, "reason": reason, "text": step.get("text", "All done.")})
        elif flavor == "codex":
            if step.get("is_error"):
                emit({"type": "turn.failed", "error": {"message": step.get("text", "boom")}})
            else:
                emit({"type": "item.completed", "item": {"id": "final", "type": "agent_message", "text": step.get("text", "All done.")}})
                emit({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}})
        elif flavor == "claude":
            emit({"type": "result", "subtype": step.get("subtype", "success"), "is_error": step.get("is_error", False),
                  "result": step.get("text", "All done."), "session_id": session, "total_cost_usd": cost,
                  "errors": step.get("errors")} if step.get("errors") else
                 {"type": "result", "subtype": step.get("subtype", "success"), "is_error": step.get("is_error", False),
                  "result": step.get("text", "All done."), "session_id": session, "total_cost_usd": cost})
        else:
            if step.get("is_error"):
                emit({"type": "error", "sessionID": session, "error": {"name": "APIError", "data": {"message": step.get("text", "boom")}}})
            else:
                emit({"type": "text", "sessionID": session, "part": {"type": "text", "text": step.get("text", "All done.")}})
                emit({"type": "step_finish", "sessionID": session, "part": {"type": "step-finish", "reason": "stop", "cost": cost}})
    elif op == "exit":
        sys.stdout.flush()
        os._exit(step.get("code", 0))
'''


# -- the scene ---------------------------------------------------------------------------------------


class Scene:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "project"
        self.root.mkdir()
        project.init_project(self.root)
        self.pid = read_identity(self.root).identity.id
        self.finished: list[tuple[Path, dict]] = []
        self.manager = manager.AgentManager(on_finish=lambda r, run: self.finished.append((r, run)), engine_port=4321)
        self.bins: dict[str, Path] = {}
        self.pidfiles: list[Path] = []
        monkeypatch.setattr(profiles, "mcp_available", lambda: True)
        for runner, program in profiles.PROGRAMS.items():
            folder = tmp_path / f"bin-{program}"
            folder.mkdir()
            script = folder / program
            script.write_text(FAKE.replace("@PYTHON@", sys.executable))
            script.chmod(0o755)
            self.bins[runner] = script
        profiles.set_commands({r: str(p) for r, p in self.bins.items()})
        self.write_tasks([{"id": "t1", "text": "Analyse the sample", "owner": "agent:claude-code:sonnet"}])

    # files and tasks
    def write_tasks(self, tasks: list[dict]) -> None:
        try:
            _old, revision = workspace.read_tasks(self.root)
        except workspace.WorkspaceError:
            revision = None
        workspace.save_tasks(self.root, {"tasks": tasks, "revision": revision})

    def tasks(self) -> dict[str, dict]:
        return {t["id"]: t for t in workspace.read_tasks(self.root)[0]}

    def put(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def pidfile(self, name: str) -> Path:
        path = self.tmp / name
        self.pidfiles.append(path)
        return path

    # scenarios
    def scenario(self, steps: list[dict], runner: str = "claude-code", task_id: str = "t1", **extra) -> None:
        owner = next(t["owner"] for t in workspace.read_tasks(self.root)[0] if t["id"] == task_id)
        agent = labels.parse_owner(owner)
        body = {"flavor": "claude" if runner == "claude-code" else runner, "steps": steps,
                "project_id": self.pid, "task_id": task_id, "agent": [agent.runner, agent.model],
                "by": "m-1", "home": str(paths.researchos_home()), **extra}
        (self.bins[runner].parent / "scenario.json").write_text(json.dumps(body))

    def calls(self, runner: str = "claude-code") -> list[dict]:
        path = self.bins[runner].parent / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    # running
    def start(self, task_id: str = "t1", **kw) -> dict:
        args = {"by": "m-1", "by_name": "Lin", "lang": "en"} | kw
        return self.manager.start(self.root, self.pid, task_id, **args)

    def run(self, run_id: str) -> dict:
        return run_store.load_run(self.pid, run_id)

    def wait(self, run_id: str, timeout: float = 40.0) -> dict:
        """Until the run is terminal and the manager has let go of the project."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            run = self.run(run_id)
            if run and run["state"] not in run_store.ACTIVE_STATES and self.manager.active_for(self.pid) is None:
                return run
            time.sleep(0.05)
        raise AssertionError(f"run {run_id} did not finish: {self.run(run_id)}")

    def wait_state(self, run_id: str, state: str, timeout: float = 30.0) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            run = self.run(run_id)
            if run and run["state"] == state:
                return run
            time.sleep(0.03)
        raise AssertionError(f"run {run_id} never reached {state}: {self.run(run_id)}")

    def wait_file(self, path: Path, timeout: float = 30.0) -> str:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if path.exists() and path.read_text().strip():
                return path.read_text().strip()
            time.sleep(0.03)
        raise AssertionError(f"{path} never appeared")

    def go(self, steps: list[dict], **kw) -> dict:
        """Start a run of `task_id` (default t1) on `runner` (default claude-code) with `steps`, and wait for it."""
        runner = kw.pop("runner", "claude-code")
        self.scenario(steps, runner=runner, task_id=kw.get("task_id", "t1"))
        return self.wait(self.start(**kw)["id"])

    def versions(self, rel: str):
        with history.Store.open(self.pid) as store:
            return store.versions(notes_mod.normalize_node(rel))

    def cleanup(self) -> None:
        self.manager.shutdown()
        for path in self.pidfiles:
            try:
                os.kill(int(path.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass


@pytest.fixture
def scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    s = Scene(tmp_path, monkeypatch)
    try:
        yield s
    finally:
        s.cleanup()


NOTE = "research/notes/" + "ab" * 16 + ".md"


def init_step():
    return {"op": "init"}


def finish_step(text: str = "All done."):
    return {"op": "result", "text": text}


def alive(pid: int) -> bool:
    return manager._pid_alive(pid)


def wait_gone(pid: int, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


COMPLETE = [init_step(), {"op": "tool", "name": "Bash", "input": {"command": "python analysis.py"}},
            {"op": "write", "path": "analysis.py", "text": "print(1)\n"},
            {"op": "say", "text": "Writing the report"}, {"op": "complete", "report": "Mean is 3."}, finish_step()]


# -- the happy path ----------------------------------------------------------------------------------------


def test_done_run_end_to_end(scene: Scene, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_REMOTE_SESSION_ID", "cse_parent")
    scene.put(NOTE, "# old\n")
    scene.scenario(COMPLETE)
    started = scene.start()
    assert started["state"] == "preparing" and started["can_stop"] is True and started["can_reply"] is False
    assert started["owner"] == "agent:claude-code:sonnet" and started["label"] == "Claude Code · sonnet"
    assert started["by"] == "m-1" and started["by_name"] == "Lin" and started["kind"] == "start"
    assert scene.manager.active_for(scene.pid) == started["id"]
    run = scene.wait(started["id"])

    assert run["state"] == "done", (run["error"], run["exit_code"])
    assert run["error"] is None and run["exit_code"] == 0 and run["pid"] > 0
    assert run["started"] and run["ended"] and run["created"] <= run["started"] <= run["ended"]
    assert run["engine_pid"] == os.getpid() and run["lang"] == "en"
    assert run["cost_usd"] == pytest.approx(0.0123) and run["model_answered"] == "claude-fake-1"
    assert run["summary"] == "All done." and run["revertible"] is True
    assert run["session_id"] and run["session_id"] == run["session_id"].lower()
    result = run["report"]
    assert result.startswith("research/results/") and (scene.root / result).is_file()
    tasks = scene.tasks()
    assert tasks["t1"]["done"] is True and tasks["t1"]["report"] == result
    # the change list: the script and the result record are the run's; tasks.json is the tools' own
    assert "analysis.py" in run["changes"]["created"] and result in run["changes"]["created"]
    assert "research/tasks.json" in run["changes"]["modified"]
    assert run["violations"] == [], run["violations"]
    assert run["tool_paths"] == sorted([result, "research/tasks.json"])
    assert run["task_done_before"] is False and run["task_report_before"] is None
    assert run["snapshot"]["partial"] is False and run["snapshot"]["files"] >= 1

    # the program was started the way runners.py says
    (call,) = scene.calls()
    argv = call["argv"]
    assert argv[1:4] == ["-p", "--output-format", "stream-json"] and "--session-id" in argv and "--resume" not in argv
    assert argv[argv.index("--session-id") + 1] == run["session_id"]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv.index("--allowedTools") > argv.index("--append-system-prompt")
    assert argv[argv.index("--setting-sources") + 1] == "user"  # the project's own .claude/settings*.json is not loaded
    # ... but its CLAUDE.md is: the project folder is added, and Claude Code reads an added folder's CLAUDE.md with this
    assert argv[argv.index("--add-dir") + 1] == str(scene.root.resolve()) == call["cwd"]
    assert call["env"]["CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"] == "1"
    assert call["cwd"] == str(scene.root.resolve())
    assert call["env"]["RESEARCHOS_AGENT_RUN"] == run["id"]
    assert "CLAUDECODE" not in call["env"] and "CLAUDE_CODE_REMOTE_SESSION_ID" not in call["env"]
    assert "Analyse the sample" in call["stdin"] and "Analyse the sample" not in " ".join(argv)

    # the run folder
    rd = run_store.run_dir(scene.pid, run["id"])
    for name in ("run.json", "instruction.txt", "events.jsonl", "stderr.log", "signals.jsonl", "guard.json",
                 "before.json", "after.json", "claude-settings.json", "mcp.json"):
        assert (rd / name).exists(), name
    assert (rd / "before" / NOTE).read_text() == "# old\n"
    assert json.loads((rd / "guard.json").read_text())["engine_port"] == 4321
    instruction = (rd / "instruction.txt").read_text()
    assert instruction.endswith(call["stdin"]) and len(instruction) > len(call["stdin"])  # rules + instruction
    assert NOTE in call["stdin"]  # the existing notes are listed

    # attribution: the agent's script is in the change history as the agent's, with the person and the run
    (version,) = scene.versions("analysis.py")
    assert version.source == "agent" and version.who == "agent:claude-code:sonnet#m-1" and version.note == run["id"]
    assert [v.source for v in scene.versions(result)] == ["agent"]  # written by the tool, not re-recorded

    # listing and on_finish
    listing = scene.manager.runs(scene.pid)
    assert listing["active"] is None and [r["id"] for r in listing["runs"]] == [run["id"]]
    only = listing["runs"][0]
    assert only["state"] == "done" and only["can_stop"] is False and only["can_reply"] is True
    assert only["can_revert"] is True and only["changes"] >= 3 and only["violations"] == 0
    assert scene.finished and scene.finished[-1][1]["id"] == run["id"] and scene.finished[-1][1]["state"] == "done"


def test_detail(scene: Scene):
    scene.put(NOTE, "# old\n")
    steps = [init_step(), {"op": "say", "text": "Looking around"},
             {"op": "tool", "name": "Bash", "input": {"command": "ls -la"}},
             {"op": "noise", "text": "this is not json", "size": 10},
             {"op": "stderr", "text": "warning: something on stderr"},
             {"op": "refusal", "target": "data.csv", "reason": "protected"}, finish_step("Final words")]
    run = scene.go(steps)
    detail = scene.manager.detail(scene.pid, run["id"])
    kinds = [s["kind"] for s in detail["steps"]]
    assert kinds[0] == "start" and "say" in kinds and "tool" in kinds and kinds[-1] == "end"
    assert any(s["text"] == "Bash: ls -la" for s in detail["steps"])
    assert detail["steps_total"] == len(detail["steps"])
    assert "Analyse the sample" in detail["instruction"] and NOTE in detail["instruction"]
    assert detail["stderr_tail"].strip() == "warning: something on stderr"
    assert detail["refusals"] and detail["refusals"][0]["target"] == "data.csv"
    assert detail["state"] == "ended" and detail["changes"] == run["changes"] and detail["error"] is None
    assert detail["summary"] == "Final words" and detail["exit_code"] == 0
    assert detail["can_reply"] is True and detail["id"] == run["id"]
    with pytest.raises(manager.AgentError) as caught:
        scene.manager.detail(scene.pid, "r-20200101T000000Z-aaaaaa")
    assert caught.value.code == "run_missing" and caught.value.status == 404
    with pytest.raises(manager.AgentError) as caught:
        scene.manager.detail(scene.pid, "../etc")
    assert caught.value.code == "invalid"


def test_stderr_and_stdout_noise_do_not_break_the_run(scene: Scene):
    run = scene.go([init_step(), {"op": "noise", "text": "{broken json", "size": 100000}, finish_step()])
    assert run["state"] == "ended"


def test_a_program_that_never_reads_its_input_is_fine(scene: Scene):
    scene.scenario([init_step(), finish_step()], no_stdin=True)
    run = scene.wait(scene.start()["id"])
    assert run["state"] == "ended" and run["exit_code"] == 0


# -- the other states --------------------------------------------------------------------------------------


def test_needs_input_then_answer_then_send_back(scene: Scene):
    scene.scenario([init_step(), {"op": "ask", "question": "Which sample?"}, finish_step("Asked")])
    first = scene.wait(scene.start()["id"])
    assert first["state"] == "needs_input" and first["question"] == "Which sample?" and first["report"] is None
    assert first["error"] is None
    listing = scene.manager.runs(scene.pid)["runs"][0]
    assert listing["can_reply"] is True and listing["question"] == "Which sample?"

    # 回答: resumes the same conversation
    scene.scenario(COMPLETE)
    second_summary = scene.manager.reply(scene.root, scene.pid, first["id"], "Use sample B", by="m-1", by_name="Lin",
                                         lang="en")
    assert second_summary["kind"] == "answer" and second_summary["parent"] == first["id"]
    second = scene.wait(second_summary["id"])
    assert second["state"] == "done" and second["message"] == "Use sample B" and second["parent"] == first["id"]
    first_call, second_call = scene.calls()
    assert "--resume" in second_call["argv"] and "--session-id" not in second_call["argv"]
    assert second_call["argv"][second_call["argv"].index("--resume") + 1] == first["session_id"]
    assert "Use sample B" in second_call["stdin"] and "Analyse the sample" not in second_call["stdin"]
    assert second["session_id"] == first["session_id"]
    assert scene.tasks()["t1"]["done"] is True
    assert scene.manager.runs(scene.pid)["runs"][-1]["can_reply"] is False  # the first is no longer the latest

    with pytest.raises(manager.AgentError) as caught:  # only the latest run can be continued
        scene.manager.reply(scene.root, scene.pid, first["id"], "again", by=None, by_name=None, lang="en")
    assert caught.value.code == "not_latest"

    # 打回 on a done task: its done mark is cleared first, the report stays
    result = scene.tasks()["t1"]["report"]
    scene.scenario([init_step(), {"op": "write", "path": "extra.txt", "text": "more\n"}, finish_step("Sent back")])
    third_summary = scene.manager.reply(scene.root, scene.pid, second["id"], "Please also add extra.txt", by="m-1",
                                        by_name="Lin", lang="zh")
    assert third_summary["kind"] == "send_back"
    third = scene.wait(third_summary["id"])
    assert third["state"] == "ended" and third["lang"] == "zh"
    task = scene.tasks()["t1"]
    assert task["done"] is False and task["report"] == result
    third_call = scene.calls()[-1]
    assert "Please also add extra.txt" in third_call["stdin"]
    assert third["task_done_before"] is False  # the engine cleared the mark before the run's before-picture


def test_ended_without_report_and_failed_variants(scene: Scene):
    ended = scene.go([init_step(), finish_step()])
    assert ended["state"] == "ended" and ended["error"] is None and ended["report"] is None
    assert scene.tasks()["t1"]["done"] is False

    # a non-zero exit with no result at all
    failed = scene.go([init_step(), {"op": "say", "text": "hmm"}, {"op": "exit", "code": 3}])
    assert failed["state"] == "failed" and failed["exit_code"] == 3
    assert failed["error"]["code"] == "aborted" and "3" in failed["error"]["detail"]

    # a result with is_error (a bad model is subtype success with is_error true)
    errored = scene.go([init_step(), {"op": "result", "is_error": True, "text": "model not found"},
                        {"op": "exit", "code": 1}])
    assert errored["state"] == "failed" and errored["error"] == {"code": "error", "detail": "model not found"}

    budget = scene.go([init_step(), {"op": "result", "is_error": True, "subtype": "error_max_budget_usd",
                                     "text": "", "errors": ["Reached maximum budget"]}, {"op": "exit", "code": 1}])
    assert budget["error"]["code"] == "error_max_budget_usd" and "budget" in budget["error"]["detail"]
    # failed runs can be continued when they have a conversation
    assert scene.manager.runs(scene.pid)["runs"][0]["can_reply"] is True


def test_a_program_that_cannot_start_fails_the_run(scene: Scene, monkeypatch):
    scene.scenario([init_step(), finish_step()])
    broken = scene.tmp / "broken"
    broken.write_text("#!/nonexistent/interpreter\n")
    broken.chmod(0o755)
    monkeypatch.setattr(profiles, "find", lambda runner, **kw: profiles.Found(runner, str(broken), "1", None))
    run = scene.wait(scene.start()["id"])
    assert run["state"] == "failed" and run["error"]["code"] == "start_failed" and run["pid"] is None
    assert run["revertible"] is True and run["changes"]["created"] == []


def test_opencode_run_and_continuation(scene: Scene):
    scene.write_tasks([{"id": "t1", "text": "Fit the model", "owner": "agent:opencode:lmstudio/qwen3-coder"}])
    steps = [init_step(), {"op": "tool", "name": "Write", "input": {"filePath": "x"}},
             {"op": "write", "path": "model.py", "text": "x = 1\n"}, {"op": "ask", "question": "Which model?"},
             finish_step("Waiting")]
    scene.scenario(steps, runner="opencode")
    first = scene.wait(scene.start()["id"])
    assert first["state"] == "needs_input" and first["runner"] == "opencode"
    assert first["session_id"].startswith("ses_fake_") and first["cost_usd"] == pytest.approx(0.0123)
    assert first["summary"] == "Waiting"
    (call,) = scene.calls("opencode")
    argv = call["argv"]
    assert argv[1:4] == ["run", "--format", "json"] and argv[argv.index("--model") + 1] == "lmstudio/qwen3-coder"
    assert argv[argv.index("--dir") + 1] == str(scene.root.resolve()) and "--session" not in argv
    rd = run_store.run_dir(scene.pid, first["id"])
    assert call["stdin"] == (rd / "instruction.txt").read_text()  # the rules and the instruction, on stdin
    assert call["env"]["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    config = json.loads(call["env"]["OPENCODE_CONFIG_CONTENT"])
    assert config["mcp"]["researchos"]["environment"]["RESEARCHOS_AGENT_RUN"] == first["id"]
    steps_seen = [s["kind"] for s in scene.manager.detail(scene.pid, first["id"])["steps"]]
    assert steps_seen[0] == "start" and "tool" in steps_seen and steps_seen[-1] == "end"  # the end is added

    scene.scenario([init_step(), finish_step("Resumed")], runner="opencode")
    second = scene.wait(scene.manager.reply(scene.root, scene.pid, first["id"], "The big one", by=None,
                                            by_name=None, lang="en")["id"])
    assert second["state"] == "ended"
    second_argv = scene.calls("opencode")[-1]["argv"]
    assert second_argv[second_argv.index("--session") + 1] == first["session_id"]
    assert "The big one" in scene.calls("opencode")[-1]["stdin"]


def test_opencode_error_event_fails(scene: Scene):
    scene.write_tasks([{"id": "t1", "text": "x", "owner": "agent:opencode:p/m"}])
    run = scene.go([init_step(), {"op": "result", "is_error": True, "text": "provider said no"},
                    {"op": "exit", "code": 1}], runner="opencode")
    assert run["state"] == "failed" and run["error"] == {"code": "model_error", "detail": "provider said no"}


@pytest.mark.parametrize("runner,model,sid_prefix", [("codex", "gpt-6.1-sol", "codex_fake_"),
                                                    ("deepseek-harness", "deepseek-account/deepseek-flash", "session-")])
def test_agent_question_resume_report_and_undo(scene: Scene, runner, model, sid_prefix):
    scene.write_tasks([{"id": "t1", "text": "Compare paper planes", "owner": f"agent:{runner}:{model}"}])
    first = scene.go([init_step(), {"op": "ask", "question": "Which design?"}, finish_step("Waiting")], runner=runner)
    assert first["state"] == "needs_input" and first["session_id"].startswith(sid_prefix)
    assert first["model"] == model and first["cost_usd"] is None
    scene.scenario([init_step(), {"op": "write", "path": "comparison.py", "text": "design = 'A'\n"},
                    {"op": "complete", "report": "Compared design A."}, finish_step("Done")], runner=runner)
    second = scene.wait(scene.manager.reply(scene.root, scene.pid, first["id"], "Design A", by=None,
                                            by_name=None, lang="en")["id"])
    assert second["state"] == "done" and scene.tasks()["t1"]["done"]
    call = scene.calls(runner)[-1]
    assert call["argv"][-3:] == ["resume" if runner == "codex" else "--session-id", first["session_id"], "-"]
    assert "Design A" in call["stdin"]
    assert (scene.root / scene.tasks()["t1"]["report"]).exists()
    scene.manager.revert(scene.root, scene.pid, second["id"], by=None)
    assert not scene.tasks()["t1"]["done"] and not (scene.root / "comparison.py").exists()


def test_codex_project_settings_change_is_detected_and_reversible(scene: Scene):
    scene.write_tasks([{"id": "t1", "text": "Compare", "owner": "agent:codex:gpt-6.1-sol"}])
    run = scene.go([init_step(), {"op": "write", "path": ".codex/config.toml", "text": "model = 'other'\n"},
                    finish_step()], runner="codex")
    assert run["state"] == "out_of_bounds"
    detail = scene.manager.detail(scene.pid, run["id"])
    assert any(v["reason"] == "agent_settings_changed" for v in detail["violations"])
    scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert not (scene.root / ".codex/config.toml").exists()


# -- start() refusals ----------------------------------------------------------------------------------


def codes(callable_, *args, **kw) -> str:
    with pytest.raises(manager.AgentError) as caught:
        callable_(*args, **kw)
    err = caught.value
    assert err.message and err.message_zh and err.status in (400, 403, 404, 409, 500)
    return err.code


def test_start_refusals(scene: Scene, monkeypatch):
    start = scene.manager.start
    base = {"by": None, "by_name": None, "lang": "en"}
    assert codes(start, scene.root, "not-a-project-id!!", "t1", **base) == "invalid"
    assert codes(start, scene.root, scene.pid, "nope", **base) == "task_missing"
    scene.write_tasks([{"id": "t1", "text": "Analyse", "owner": "agent:claude-code:sonnet"},
                       {"id": "t2", "text": "Human", "owner": "Zhang San"},
                       {"id": "t3", "text": "Done", "owner": "agent:claude-code:sonnet", "done": True},
                       {"id": "t4", "text": "None"}])
    assert codes(start, scene.root, scene.pid, "t2", **base) == "not_agent_task"
    assert codes(start, scene.root, scene.pid, "t4", **base) == "not_agent_task"
    assert codes(start, scene.root, scene.pid, "t3", **base) == "task_done"
    monkeypatch.setattr(profiles, "mcp_available", lambda: False)
    assert codes(start, scene.root, scene.pid, "t1", **base) == "mcp_missing"
    monkeypatch.setattr(profiles, "mcp_available", lambda: True)
    profiles.set_commands({"claude-code": str(scene.tmp / "does-not-exist")})
    assert codes(start, scene.root, scene.pid, "t1", **base) == "runner_missing"
    profiles.set_commands({"claude-code": str(scene.bins["claude-code"])})
    # a folder that is not this project's
    other = scene.tmp / "other"
    other.mkdir()
    project.init_project(other)
    assert codes(start, other, scene.pid, "t1", **base) == "invalid"
    # the desk's own write rule
    def refuse(root):
        raise workspace.WorkspaceError("read_only", "read only", 403)
    monkeypatch.setattr(workspace, "_require_write", refuse)
    assert codes(start, scene.root, scene.pid, "t1", **base) == "read_only"
    assert not (run_store.project_runs_dir(scene.pid)).exists() or not list(run_store.project_runs_dir(scene.pid).iterdir())


def test_busy_and_concurrent_starts(scene: Scene):
    scene.write_tasks([{"id": "t1", "text": "A", "owner": "agent:claude-code:sonnet"},
                       {"id": "t2", "text": "B", "owner": "agent:claude-code:sonnet"}])
    gate = scene.tmp / "gate"
    scene.scenario([init_step(), {"op": "wait_file", "path": str(gate)}, finish_step()])
    first = scene.start()
    assert codes(scene.manager.start, scene.root, scene.pid, "t2", by=None, by_name=None, lang="en") == "busy"
    assert codes(scene.manager.reply, scene.root, scene.pid, first["id"], "x", by=None, by_name=None,
                 lang="en") == "run_active"
    assert codes(scene.manager.revert, scene.root, scene.pid, first["id"], by=None) == "run_active"
    gate.write_text("go")
    scene.wait(first["id"])

    # eight requests at once: exactly one wins
    gate2 = scene.tmp / "gate2"
    scene.scenario([init_step(), {"op": "wait_file", "path": str(gate2)}, finish_step()])
    results: list = []

    def attempt() -> None:
        try:
            results.append(scene.start("t2"))
        except manager.AgentError as exc:
            results.append(exc)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in results if isinstance(r, dict)]
    assert len(winners) == 1 and all(isinstance(r, manager.AgentError) and r.code == "busy"
                                     for r in results if not isinstance(r, dict))
    gate2.write_text("go")
    scene.wait(winners[0]["id"])
    assert len(run_store.list_runs(scene.pid)) == 2


# -- stopping --------------------------------------------------------------------------------------------------


def test_stop_kills_the_program_and_a_grandchild_in_its_own_session(scene: Scene):
    child, agent = scene.pidfile("child.pid"), scene.pidfile("agent.pid")
    scene.scenario([init_step(), {"op": "pidfile", "pidfile": str(agent)},
                    {"op": "spawn", "pidfile": str(child), "new_session": True}, {"op": "sleep", "s": 120}])
    started = scene.start()
    child_pid, agent_pid = int(scene.wait_file(child)), int(scene.wait_file(agent))
    scene.wait_state(started["id"], "running")
    assert alive(child_pid) and alive(agent_pid)
    assert os.getsid(child_pid) != os.getsid(agent_pid)  # killpg could not reach it

    began = time.monotonic()
    stopped = scene.manager.stop(scene.pid, started["id"])
    assert time.monotonic() - began < 8
    assert stopped["state"] == "stopped" and stopped["can_stop"] is False and stopped["can_reply"] is True
    assert wait_gone(agent_pid) and wait_gone(child_pid)
    run = scene.run(started["id"])
    assert run["state"] == "stopped" and run["error"] is None and run["exit_code"] in (-15, -9, 143)
    assert scene.manager.active_for(scene.pid) is None
    # idempotent, and a run that is not going is returned unchanged
    again = scene.manager.stop(scene.pid, started["id"])
    assert again["state"] == "stopped"
    assert codes(scene.manager.stop, scene.pid, "r-20200101T000000Z-aaaaaa") == "run_missing"
    assert [s["kind"] for s in scene.manager.detail(scene.pid, started["id"])["steps"]][-1] == "end"
    # the project is free again
    scene.scenario([init_step(), finish_step()])
    assert scene.wait(scene.start()["id"])["state"] == "ended"


def test_stop_escalates_to_sigkill_when_sigterm_is_ignored(scene: Scene, monkeypatch):
    monkeypatch.setattr(manager, "KILL_GRACE_S", 0.6)
    agent = scene.pidfile("agent.pid")
    scene.scenario([init_step(), {"op": "pidfile", "pidfile": str(agent)}, {"op": "sleep", "s": 120}],
                   ignore_sigterm=True)
    started = scene.start()
    pid = int(scene.wait_file(agent))
    scene.wait_state(started["id"], "running")
    time.sleep(0.3)  # the handler is installed before the pidfile is written
    scene.manager.stop(scene.pid, started["id"])
    assert wait_gone(pid)
    run = scene.wait(started["id"])
    assert run["state"] == "stopped" and run["exit_code"] == -9


def test_stop_during_preparing_never_launches(scene: Scene, monkeypatch):
    release, entered = threading.Event(), threading.Event()
    real_take = snapshot.take

    def slow_take(root, copy_to=None):
        entered.set()
        release.wait(10)
        return real_take(root, copy_to)

    monkeypatch.setattr(snapshot, "take", slow_take)
    scene.scenario([init_step(), finish_step()])
    started = scene.start()
    assert entered.wait(10)  # the run is preparing: the before-picture is being taken
    stopper = threading.Thread(target=scene.manager.stop, args=(scene.pid, started["id"]))
    stopper.start()
    time.sleep(0.3)
    assert scene.run(started["id"])["state"] == "preparing"
    release.set()
    stopper.join(20)
    run = scene.wait(started["id"])
    assert run["state"] == "stopped" and run["pid"] is None and scene.calls() == []


def test_timeout(scene: Scene, monkeypatch):
    monkeypatch.setattr(profiles, "timeout_minutes", lambda: 0.04)  # 2.4 s
    agent = scene.pidfile("agent.pid")
    scene.scenario([init_step(), {"op": "pidfile", "pidfile": str(agent)}, {"op": "sleep", "s": 120}])
    started = scene.start()
    run = scene.wait(started["id"])
    assert run["state"] == "failed" and run["error"]["code"] == "timeout" and "time limit" in run["error"]["detail"]
    assert wait_gone(int(agent.read_text()))
    assert scene.manager.runs(scene.pid)["runs"][0]["can_reply"] is True


def test_shutdown_interrupts_every_run_and_refuses_new_ones(scene: Scene):
    agent = scene.pidfile("agent.pid")
    scene.scenario([init_step(), {"op": "pidfile", "pidfile": str(agent)},
                    {"op": "write", "path": "partial.txt", "text": "half"}, {"op": "sleep", "s": 120}])
    started = scene.start()
    scene.wait_state(started["id"], "running")
    pid = int(scene.wait_file(agent))
    scene.manager.shutdown()
    scene.manager.shutdown()  # idempotent
    run = scene.run(started["id"])
    assert run["state"] == "interrupted" and run["error"]["code"] == "shutdown"
    assert scene.manager.detail(scene.pid, started["id"])["steps"][-1]["code"] == "shutdown"  # the log ends with the code
    assert wait_gone(pid)
    assert "partial.txt" in run["changes"]["created"] and run["revertible"] is True  # it can still be undone
    assert codes(scene.manager.start, scene.root, scene.pid, "t1", by=None, by_name=None, lang="en") == "start_failed"
    assert scene.finished and scene.finished[-1][1]["state"] == "interrupted"


def test_the_time_limit_and_the_person_do_not_both_win(scene: Scene):
    """stop() after the program already ended on its own changes nothing."""
    run = scene.go([init_step(), finish_step()])
    assert run["state"] == "ended"
    assert scene.manager.stop(scene.pid, run["id"])["state"] == "ended"
    assert scene.run(run["id"])["state"] == "ended"


def test_background_process_holding_stdout_does_not_hang_the_run(scene: Scene, monkeypatch):
    monkeypatch.setattr(manager, "EXIT_DRAIN_S", 0.5)
    holder = scene.pidfile("holder.pid")
    run = scene.go([init_step(), {"op": "spawn", "pidfile": str(holder), "new_session": False, "inherit_stdout": True},
                    finish_step()])
    assert run["state"] == "ended"


# -- bounds and attribution -------------------------------------------------------------------------------------


def test_overwriting_a_data_file_is_an_out_of_bounds_change(scene: Scene):
    scene.put("data.csv", "a,b\n1,2\n")
    scene.put("clean.txt", "keep\n")
    run = scene.go([init_step(), {"op": "write", "path": "data.csv", "text": "a,b\n9,9\n"},
                    {"op": "write", "path": "newdata.csv", "text": "x\n1\n"}, {"op": "complete"}, finish_step()])
    # the agent reported the task done, and the run reads out of bounds all the same (18.1 item 4)
    assert run["state"] == "out_of_bounds" and run["outcome"] == "done"
    assert run["violations"] == [{"path": "data.csv", "reason": "data_changed"}]
    assert scene.tasks()["t1"]["done"] is True  # the task's done mark stays as the agent set it
    assert "data.csv" in run["changes"]["modified"] and "newdata.csv" in run["changes"]["created"]
    listed = scene.manager.runs(scene.pid)["runs"][0]
    assert listed["violations"] == 1 and listed["can_revert"] is True
    # protected files were passed to the guard
    guard_file = json.loads((run_store.run_dir(scene.pid, run["id"]) / "guard.json").read_text())
    assert "data.csv" in guard_file["files"] and "research/tasks.json" in guard_file["files"]
    assert ".researchos" in guard_file["dirs"]
    # undoing restores the data file
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by="m-1")
    assert "data.csv" in result["restored"] and "newdata.csv" in result["removed"]
    assert (scene.root / "data.csv").read_text() == "a,b\n1,2\n" and not (scene.root / "newdata.csv").exists()


def test_a_tasks_json_edit_the_tools_did_not_make_is_a_violation(scene: Scene):
    tasks_path = scene.root / "research" / "tasks.json"
    sneaky = json.dumps([{"id": "t1", "text": "Analyse the sample", "done": True,
                          "owner": "agent:claude-code:sonnet", "due": ""}])
    run = scene.go([init_step(), {"op": "write", "path": str(tasks_path), "text": sneaky}, finish_step()])
    assert {"path": "research/tasks.json", "reason": "record_changed"} in run["violations"]
    assert run["state"] == "out_of_bounds" and run["outcome"] == "ended"
    # the history keeps the file's attribution out of the agent's hands
    assert all(v.source != "agent" for v in scene.versions("research/tasks.json"))


def test_researchos_folder_and_git_changes_are_violations(scene: Scene):
    subprocess.run(["git", "init", "-q"], cwd=scene.root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"],
                   cwd=scene.root, check=True)
    team = scene.root / ".researchos" / "team.toml"
    run = scene.go([init_step(), {"op": "write", "path": str(team), "text": "x = 1\n"}, finish_step()])
    reasons = {(v["path"], v["reason"]) for v in run["violations"]}
    assert (".researchos/team.toml", "researchos_changed") in reasons
    assert run["state"] == "out_of_bounds" and run["outcome"] == "ended"
    assert (".researchos/team.toml" not in [v.who for v in scene.versions(".researchos/team.toml")])  # no attribution there


def test_the_persons_own_saves_during_the_run_are_not_the_agents(scene: Scene):
    scene.put("notes/mine.md", "# mine\n")
    gate = scene.tmp / "gate"
    scene.scenario([init_step(), {"op": "write", "path": "agent.txt", "text": "agent\n"},
                    {"op": "wait_file", "path": str(gate)}, finish_step()])
    started = scene.start()
    scene.wait_state(started["id"], "running")
    # the person saves two files through Research OS while the run goes on
    record_files.durable_write(scene.root / "notes" / "mine.md", b"# mine, edited\n")
    record_files.write_new_file(scene.root / "person.txt", b"by hand\n")
    # an unrelated write elsewhere on this Mac is not recorded as a project path
    record_files.durable_write(scene.tmp / "elsewhere.txt", b"x")
    time.sleep(0.2)
    gate.write_text("go")
    run = scene.wait(started["id"])
    assert run["state"] == "ended"
    assert "agent.txt" in run["changes"]["created"]
    assert "person.txt" not in run["changes"]["created"] and "notes/mine.md" not in run["changes"]["modified"]
    assert run["changes"]["by_person"] == ["notes/mine.md", "person.txt"]
    assert run["human_paths"] == ["notes/mine.md", "person.txt"]
    assert [v.source for v in scene.versions("person.txt")] == []
    assert [v.who for v in scene.versions("agent.txt")] == ["agent:claude-code:sonnet#m-1"]
    # the listener is gone once the run ended
    assert not record_files._listeners


def git_repo(scene: Scene) -> None:
    subprocess.run(["git", "init", "-q"], cwd=scene.root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"],
                   cwd=scene.root, check=True)


def test_a_planted_git_hook_makes_the_run_read_out_of_bounds_and_undo_takes_it_away(scene: Scene):
    git_repo(scene)
    hook = scene.root / ".git" / "hooks" / "pre-commit"
    run = scene.go([init_step(), {"op": "write", "path": str(hook), "text": "#!/bin/sh\ntouch /tmp/nope\n"},
                    {"op": "complete"}, finish_step()])
    assert run["state"] == "out_of_bounds" and run["outcome"] == "done"
    assert {"path": ".git/hooks/pre-commit", "reason": "git_hooks_changed"} in run["violations"]
    assert ".git/hooks/pre-commit" in run["changes"]["created"]
    assert scene.versions(".git/hooks/pre-commit") == []  # git's own files are not the history's business
    listed = scene.manager.runs(scene.pid)["runs"][0]
    assert listed["state"] == "out_of_bounds" and listed["outcome"] == "done" and listed["violations"] >= 1
    assert listed["can_revert"] is True and listed["can_reply"] is True
    # never "done" for the run, and the task keeps the mark the agent gave it
    assert scene.tasks()["t1"]["done"] is True
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by="m-1")
    assert ".git/hooks/pre-commit" in result["removed"] and not hook.exists()
    assert (scene.root / ".git" / "hooks").is_dir()
    assert scene.tasks()["t1"]["done"] is False  # undo takes the agent's own done mark back (17.6)
    assert scene.manager.runs(scene.pid)["runs"][0]["reverted"] is True


def test_a_changed_git_config_is_out_of_bounds_and_undo_puts_it_back(scene: Scene):
    git_repo(scene)
    config = scene.root / ".git" / "config"
    original = config.read_text()
    run = scene.go([init_step(), {"op": "write", "path": str(config), "text": original + "[core]\n\thooksPath = /tmp/h\n"},
                    finish_step()])
    assert run["state"] == "out_of_bounds"
    assert {"path": ".git/config", "reason": "git_config_changed"} in run["violations"]
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert ".git/config" in result["restored"] and config.read_text() == original


def test_a_repository_the_run_started_is_not_taken_apart_by_undo(scene: Scene):
    run = scene.go([init_step(), {"op": "write", "path": str(scene.root / ".git" / "config"), "text": "[core]\n"},
                    {"op": "write", "path": str(scene.root / ".git" / "HEAD"), "text": "ref: refs/heads/main\n"},
                    finish_step()])
    assert run["state"] == "out_of_bounds"
    assert {"path": ".git/config", "reason": "git_config_changed"} in run["violations"]
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert {"path": ".git/config", "reason": "protected"} in result["skipped"]
    assert (scene.root / ".git" / "config").read_text() == "[core]\n"


def backup_name(day: int, kind: str = "toml") -> str:
    return f"x.toml.202601{day:02d}T000000000000Z.{kind}"


def test_a_deleted_backup_is_out_of_bounds(scene: Scene):
    backup = scene.put(f".researchos/backups/{backup_name(1)}", "old copy\n")
    scene.put(f".researchos/backups/{backup_name(2)}", "newer copy\n")
    run = scene.go([init_step(), {"op": "delete", "path": str(backup)}, finish_step()])
    assert run["state"] == "out_of_bounds" and run["outcome"] == "ended"
    path = f".researchos/backups/{backup_name(1)}"
    assert run["violations"] == [{"path": path, "reason": "backup_deleted"}]
    assert path in run["changes"]["deleted"]
    # the picture holds no copy of a backup: undo says so instead of pretending
    assert scene.manager.runs(scene.pid)["runs"][0]["can_revert"] is True
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["skipped"] == [{"path": path, "reason": "no_copy"}] and result["restored"] == []
    before = json.loads((run_store.run_dir(scene.pid, run["id"]) / "before.json").read_text())
    assert before["backups"][path] == {"size": len("old copy\n")}  # names and sizes, from the start
    assert f".researchos/backups/{backup_name(2)}" in before["backups"]


def test_new_backups_are_nothing_and_a_run_saving_through_its_tools_may_rotate_old_ones(scene: Scene):
    """Research OS keeps the newest 20 copies of what it writes before a write; a run's own `complete` tool
    saves tasks.json and so adds one and prunes the oldest. That is not the agent deleting a backup."""
    series = [f".researchos/backups/tasks.json.202601{d:02d}T000000000000Z.write.json" for d in range(1, 21)]
    for rel in series:
        scene.put(rel, "{}")
    run = scene.go(COMPLETE)
    assert run["state"] == "done" and run["violations"] == [], run["violations"]
    left = sorted(p.name for p in (scene.root / ".researchos" / "backups").glob("tasks.json.*.write.json"))
    assert len(left) == 20 and left[0] == Path(series[1]).name  # the oldest went, a new one came
    assert not (scene.root / series[0]).exists()
    assert run["changes"]["deleted"] == []


def test_an_out_of_bounds_run_that_asked_a_question_can_still_be_answered(scene: Scene):
    scene.put("data.csv", "a,b\n1,2\n")
    scene.scenario([init_step(), {"op": "write", "path": "data.csv", "text": "a,b\n9,9\n"},
                    {"op": "ask", "question": "Which sample?"}, finish_step("Asked")])
    first = scene.wait(scene.start()["id"])
    assert first["state"] == "out_of_bounds" and first["outcome"] == "needs_input"
    assert first["question"] == "Which sample?"
    item = scene.manager.runs(scene.pid)["runs"][0]
    assert item["state"] == "out_of_bounds" and item["outcome"] == "needs_input" and item["can_reply"] is True
    scene.scenario([init_step(), finish_step("Thanks")])
    answered = scene.manager.reply(scene.root, scene.pid, first["id"], "Sample B", by="m-1", by_name="Lin", lang="en")
    assert answered["kind"] == "answer"  # it was a question, however the run reads
    assert scene.wait(answered["id"])["state"] == "ended"


@pytest.mark.parametrize("how", ["stop", "failed"])
def test_a_stopped_or_failed_run_that_went_out_of_bounds_still_says_so_first(scene: Scene, how):
    scene.put("data.csv", "a,b\n1,2\n")
    gate = scene.tmp / "gate"
    steps = [init_step(), {"op": "write", "path": "data.csv", "text": "a,b\n9,9\n"}, {"op": "wait_file", "path": str(gate)},
             finish_step()]
    if how == "failed":
        steps = [init_step(), {"op": "write", "path": "data.csv", "text": "a,b\n9,9\n"},
                 {"op": "result", "is_error": True, "subtype": "error_during_execution", "text": "boom"}]
    scene.scenario(steps)
    started = scene.start()
    if how == "stop":
        scene.wait_state(started["id"], "running")
        time.sleep(0.3)
        scene.manager.stop(scene.pid, started["id"])
    run = scene.wait(started["id"])
    assert run["state"] == "out_of_bounds" and run["outcome"] == ("stopped" if how == "stop" else "failed")
    detail = scene.manager.detail(scene.pid, run["id"])
    assert detail["state"] == "out_of_bounds" and detail["outcome"] == run["outcome"]
    if how == "stop":
        assert detail["steps"][-1] == {"kind": "end", "text": "", "tool": None, "code": "stopped"}  # the log still says how it ended
    else:
        assert detail["error"]


def test_a_recovered_run_that_went_out_of_bounds_reads_so(scene: Scene):
    scene.put("data.csv", "a,b\n1,2\n")
    run = stale_run(scene)
    (scene.root / "data.csv").write_text("a,b\n9,9\n")
    item = scene.manager.runs(scene.pid)["runs"][0]
    assert item["state"] == "out_of_bounds" and item["outcome"] == "interrupted" and item["can_stop"] is False
    stored = scene.run(run["id"])
    assert stored["error"]["code"] == "engine_gone" and stored["violations"] == [
        {"path": "data.csv", "reason": "data_changed"}]


def attempts_project(scene: Scene) -> project_watcher.ProjectWatcher:
    """The smallest project the watcher has something to watch in (an attempt table, a steps folder)."""
    config = ('file = "map.md"\nheading = "H"\nsteps_dir = "steps"\n\n[columns]\nid = "#"\ndate = "date"\n'
              'description = "desc"\nvariables = "vars"\nresult = "result"\nverdict = "verdict"\n')
    scene.put(".researchos/attempts.toml", config)
    scene.put("map.md", "## H\n\n| # | date | desc | vars | result | verdict |\n|---|---|---|---|---|---|\n"
                        "| 1 | 2026-01-01 | d | v | r | ✅ |\n")
    (scene.root / "steps").mkdir()
    watcher = project_watcher.ProjectWatcher(lambda: scene.root, interval=0.01)
    watcher.poll_once()  # the baseline
    return watcher


def test_a_watcher_scan_during_the_run_records_the_agents_files_and_the_run_adds_nothing_twice(scene: Scene):
    watcher = attempts_project(scene)
    scene.put("existing.py", "print('there before the run')\n")  # never scanned, and not the run's
    gate, mine = scene.tmp / "gate", scene.root / "mine.py"
    step = scene.root / "steps" / "s1.py"
    scene.scenario([init_step(), {"op": "write", "path": str(step), "text": "x = 1\n"},
                    {"op": "write", "path": "analysis.py", "text": "print(1)\n"},
                    {"op": "wait_file", "path": str(gate)},
                    {"op": "write", "path": "analysis.py", "text": "print(2)\n"}, finish_step()])
    started = scene.start()
    scene.wait_state(started["id"], "running")
    scene.wait_file(step)
    scene.wait_file(scene.root / "analysis.py")
    assert history.run_seen(scene.root) is not None  # the history knows a run is going on
    record_files.durable_write(mine, b"print('the person saved this through Research OS')\n")
    assert watcher.poll_once() is True  # the scan the agent's new step script caused
    note = f"seen during run {started['id']}"
    who = "agent:claude-code:sonnet#m-1"
    assert [(v.source, v.who, v.note) for v in scene.versions("steps/s1.py")] == [("agent", who, note)]
    assert [(v.source, v.who, v.note) for v in scene.versions("analysis.py")] == [("agent", who, note)]
    # the person's save keeps their name, and a file older than the run is not the run's
    assert [v.source for v in scene.versions("mine.py")] == ["scan"]
    assert [v.source for v in scene.versions("existing.py")] == ["scan"]
    assert [v.who for v in scene.versions("mine.py")] == [history.scan_who(scene.root)]
    gate.write_text("go")
    run = scene.wait(started["id"])
    assert run["state"] == "ended"
    assert history.run_seen(scene.root) is None  # the window closes with the run
    # the script the scan saw is not recorded again; the one the agent changed afterwards is
    assert [(v.source, v.note) for v in scene.versions("steps/s1.py")] == [("agent", note)]
    assert [(v.source, v.note) for v in reversed(scene.versions("analysis.py"))] == [("agent", note),
                                                                                     ("agent", run["id"])]
    assert [v.source for v in scene.versions("mine.py")] == ["scan"]
    assert run["changes"]["by_person"] == ["mine.py"]


def test_the_history_is_told_for_exactly_as_long_as_the_run_is_going(scene: Scene, monkeypatch):
    marks: list[tuple[str, object]] = []
    original = manager.AgentManager._update

    def spy(self, act, **changes):
        if changes.get("state") in ("running", "ended"):
            marks.append((changes["state"], history.run_seen(scene.root)))
        return original(self, act, **changes)

    monkeypatch.setattr(manager.AgentManager, "_update", spy)
    assert history.run_seen(scene.root) is None
    scene.go([init_step(), finish_step()])
    (running, seen), (ended, after) = marks
    assert running == "running" and seen is not None and seen.who == "agent:claude-code:sonnet#m-1"
    assert ended == "ended" and after is None  # released when the attribution was written, before the final state
    assert history.run_seen(scene.root) is None


def test_a_run_that_could_not_be_checked_still_lets_the_history_go(scene: Scene, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("cannot compare")

    monkeypatch.setattr(manager.AgentManager, "_assess", boom)
    run = scene.go([init_step(), finish_step()])
    assert run["error"]["code"] == "check_failed" and history.run_seen(scene.root) is None


def test_the_attempt_table_the_agent_changed_gives_the_agents_verdict(scene: Scene):
    watcher = attempts_project(scene)
    scene.scenario([init_step(), {"op": "write", "path": "map.md", "text": "## H\n\n| # | date | desc | vars | result | verdict |\n"
                                    "|---|---|---|---|---|---|\n| 1 | 2026-01-01 | d | v | r | ❌ |\n"},
                    {"op": "write", "path": str(scene.root / "steps" / "s1.py"), "text": "x = 1\n"},
                    {"op": "wait_file", "path": str(scene.tmp / "gate")}, finish_step()])
    started = scene.start()
    scene.wait_state(started["id"], "running")
    scene.wait_file(scene.root / "steps" / "s1.py")
    watcher.poll_once()
    conn = db.connect(paths.graph_db_path(scene.root))
    try:
        node = db.get_nodes_by_type(conn, "attempt")[0]["id"]
    finally:
        conn.close()
    with history.Store.open(scene.pid) as store:
        [version] = store.versions(node)
    assert version.source == "agent" and version.note == f"seen during run {started['id']}"
    assert version.who == "agent:claude-code:sonnet#m-1" and version.summary["verdict"]
    (scene.tmp / "gate").write_text("go")
    run = scene.wait(started["id"])
    assert run["state"] == "out_of_bounds"  # the table is not the agent's to change
    assert {"path": "map.md", "reason": "record_changed"} in run["violations"]


def test_history_attribution_without_a_person(scene: Scene):
    run = scene.go([init_step(), {"op": "write", "path": "a.txt", "text": "a\n"}, finish_step()], by=None,
                   by_name=None)
    (version,) = scene.versions("a.txt")
    assert version.who == "agent:claude-code:sonnet" and version.source == "agent" and version.note == run["id"]
    assert run["by"] is None and run["by_name"] is None


def test_a_cut_picture_does_not_report_unseen_files_as_deleted(scene: Scene, monkeypatch):
    for i in range(5):
        scene.put(f"f{i}.txt", str(i))
    monkeypatch.setattr(snapshot, "FILE_LIMIT", 4)
    run = scene.go([init_step(), finish_step()])
    assert run["snapshot"]["partial"] is True
    assert run["changes"]["deleted"] == []


def test_the_person_saving_a_document_during_the_run_is_not_the_agents(scene: Scene):
    """Through the real desk: the save, its backup and its history are the engine's own writes."""
    doc = workspace.save_document(scene.root, {"kind": "note", "content": "# Mine\n\nfirst\n", "revision": None})
    gate = scene.tmp / "gate"
    scene.scenario([init_step(), {"op": "wait_file", "path": str(gate)}, finish_step()])
    started = scene.start()
    scene.wait_state(started["id"], "running")
    workspace.save_document(scene.root, {"path": doc["path"], "content": "# Mine\n\nsecond\n",
                                         "revision": doc["revision"]})
    gate.write_text("go")
    run = scene.wait(started["id"])
    assert run["state"] == "ended" and run["violations"] == []
    assert doc["path"] in run["changes"]["by_person"] and doc["path"] not in run["changes"]["modified"]
    assert run["changes"]["created"] == [] and run["changes"]["modified"] == []
    assert all(v.source != "agent" for v in scene.versions(doc["path"]))


def test_two_projects_run_side_by_side(scene: Scene):
    other = scene.tmp / "second"
    other.mkdir()
    project.init_project(other)
    other_id = read_identity(other).identity.id
    workspace.save_tasks(other, {"tasks": [{"id": "t1", "text": "Other", "owner": "agent:claude-code:sonnet"}],
                                 "revision": None})
    gate = scene.tmp / "gate"
    scene.scenario([init_step(), {"op": "wait_file", "path": str(gate)}, finish_step()])
    first = scene.start()
    second = scene.manager.start(other, other_id, "t1", by=None, by_name=None, lang="en")
    assert scene.manager.active_for(scene.pid) == first["id"] and scene.manager.active_for(other_id) == second["id"]
    gate.write_text("go")
    scene.wait(first["id"])
    end = time.monotonic() + 30
    while scene.manager.active_for(other_id) and time.monotonic() < end:
        time.sleep(0.05)
    assert run_store.load_run(other_id, second["id"])["state"] == "ended"
    assert [r["id"] for r in scene.manager.runs(other_id)["runs"]] == [second["id"]]


def test_a_failure_while_reading_the_output_kills_the_program_and_ends_the_run(scene: Scene, monkeypatch):
    agent = scene.pidfile("agent.pid")
    scene.scenario([init_step(), {"op": "pidfile", "pidfile": str(agent)}, {"op": "write", "path": "x.txt", "text": "x"},
                    {"op": "sleep", "s": 120}])

    def broken(self, act, proc, stream, events_path):
        scene.wait_file(agent)
        raise RuntimeError("pump bug")

    monkeypatch.setattr(manager.AgentManager, "_pump", broken)
    run = scene.wait(scene.start()["id"])
    assert run["state"] == "failed" and run["error"]["code"] == "internal" and "pump bug" in run["error"]["detail"]
    assert wait_gone(int(agent.read_text()))
    assert "x.txt" in run["changes"]["created"]  # the after-run check still ran


def test_an_oversized_output_line_is_dropped_not_fatal(scene: Scene, monkeypatch):
    monkeypatch.setattr(manager, "MAX_LINE_BYTES", 1000)
    run = scene.go([init_step(), {"op": "noise", "text": "short", "size": 5000}, finish_step("After the long line")])
    assert run["state"] == "ended" and run["summary"] == "After the long line"
    events = (run_store.run_dir(scene.pid, run["id"]) / "events.jsonl").read_text().splitlines()
    assert events and all(len(line) <= 1000 for line in events)


# -- robustness ------------------------------------------------------------------------------------------------------


def test_an_exception_in_the_thread_ends_in_failed_with_the_error(scene: Scene, monkeypatch):
    def boom(root, copy_to=None):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(snapshot, "take", boom)
    scene.scenario([init_step(), finish_step()])
    started = scene.start()
    run = scene.wait(started["id"])
    assert run["state"] == "failed" and run["error"]["code"] == "internal" and "disk on fire" in run["error"]["detail"]
    assert run["ended"] and scene.manager.active_for(scene.pid) is None and scene.calls() == []
    assert scene.finished and scene.finished[-1][1]["state"] == "failed"
    assert not record_files._listeners


def test_a_failing_after_run_check_still_ends_the_run(scene: Scene, monkeypatch):
    real_take = snapshot.take
    calls = {"n": 0}

    def flaky(root, copy_to=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("cannot read the folder")
        return real_take(root, copy_to, **kwargs)

    monkeypatch.setattr(snapshot, "take", flaky)
    run = scene.go([init_step(), finish_step()])
    assert run["state"] == "ended" and run["error"]["code"] == "check_failed"


def test_history_failure_never_fails_a_run(scene: Scene, monkeypatch):
    monkeypatch.setattr(history.Store, "open", classmethod(lambda cls, pid: (_ for _ in ()).throw(OSError("no"))))
    run = scene.go([init_step(), {"op": "write", "path": "a.txt", "text": "a"}, finish_step()])
    assert run["state"] == "ended" and run["error"] is None


def test_on_finish_exception_is_logged_not_raised(scene: Scene):
    scene.manager.on_finish = lambda root, run: (_ for _ in ()).throw(RuntimeError("x"))
    run = scene.go([init_step(), finish_step()])
    assert run["state"] == "ended"
    scene.scenario([init_step(), finish_step()])
    assert scene.wait(scene.start()["id"])["state"] == "ended"  # the project was freed before on_finish


def test_events_are_capped(scene: Scene, monkeypatch):
    monkeypatch.setattr(run_store, "EVENTS_LIMIT", 600)
    steps = [init_step()] + [{"op": "say", "text": "word " * 20} for _ in range(20)] + [finish_step()]
    run = scene.go(steps)
    rd = run_store.run_dir(scene.pid, run["id"])
    assert (rd / "events.jsonl").stat().st_size <= 600 and run["events_truncated"] is True
    assert run["state"] == "ended" and run["summary"] == "All done."  # the parser still saw the end


def test_run_json_updates_are_serialised_while_the_run_goes_on(scene: Scene):
    gate = scene.tmp / "gate"
    scene.scenario([init_step(), {"op": "wait_file", "path": str(gate)}, finish_step()])
    started = scene.start()
    scene.wait_state(started["id"], "running")
    run = scene.wait_state(started["id"], "running")
    end = time.monotonic() + 1.0
    while time.monotonic() < end:  # hammer the readers while the thread persists
        assert scene.manager.runs(scene.pid)["active"] == started["id"]
        assert run_store.load_run(scene.pid, started["id"]) is not None
    assert scene.run(started["id"])["session_id"]  # learned from the init event while running
    gate.write_text("go")
    scene.wait(started["id"])


# -- undo ----------------------------------------------------------------------------------------------------------


def revert_scene(scene: Scene) -> dict:
    scene.put("old.txt", "v0\n")
    scene.put("gone.txt", "bye\n")
    scene.put("edited.txt", "e0\n")
    scene.put("kept/inner.txt", "inner\n")
    return scene.go([init_step(), {"op": "write", "path": "new.txt", "text": "new\n"},
                     {"op": "write", "path": "deep/er/new2.txt", "text": "new2\n"},
                     {"op": "write", "path": "old.txt", "text": "v1\n"},
                     {"op": "write", "path": "edited.txt", "text": "e1\n"},
                     {"op": "delete", "path": "gone.txt"}, {"op": "complete"}, finish_step()])


def test_revert_matrix(scene: Scene):
    run = revert_scene(scene)
    assert run["state"] == "done"
    created, modified, deleted = (set(run["changes"][k]) for k in ("created", "modified", "deleted"))
    assert {"new.txt", "deep/er/new2.txt"} <= created and {"old.txt", "edited.txt"} <= modified
    assert deleted == {"gone.txt"}
    result_path = scene.tasks()["t1"]["report"]
    assert result_path in created
    scene.put("edited.txt", "e2 by the person\n")  # changed after the run
    time.sleep(0.01)

    result = scene.manager.revert(scene.root, scene.pid, run["id"], by="m-1")
    assert sorted(result["restored"]) == sorted(["gone.txt", "old.txt", "research/tasks.json"])
    assert sorted(result["removed"]) == sorted(["deep/er/new2.txt", "new.txt", result_path])
    assert result["skipped"] == [{"path": "edited.txt", "reason": "changed_since"}]
    assert (scene.root / "old.txt").read_text() == "v0\n" and (scene.root / "gone.txt").read_text() == "bye\n"
    assert (scene.root / "edited.txt").read_text() == "e2 by the person\n"
    assert not (scene.root / "new.txt").exists() and not (scene.root / result_path).exists()
    rd = run_store.run_dir(scene.pid, run["id"])
    assert (rd / "reverted" / "new.txt").read_text() == "new\n"
    assert (rd / "reverted" / "deep" / "er" / "new2.txt").read_text() == "new2\n"
    assert (rd / "reverted" / "old.txt").read_text() == "v1\n"  # what was there is kept too
    assert (rd / "reverted" / result_path).is_file()
    # the task: done mark and report taken back logically
    task = scene.tasks()["t1"]
    assert task["done"] is False and "report" not in task
    stored = scene.run(run["id"])
    assert stored["reverted"] is True and stored["reverted_by"] == "m-1" and stored["reverted_at"]
    summary = result["run"]
    assert summary["reverted"] is True and summary["can_revert"] is False and summary["can_reply"] is False
    assert summary["id"] == run["id"]
    # the restore is in the history, under the person's name (the scan's), as an undo
    versions = scene.versions("old.txt")
    assert versions[0].note == f"undo {run['id']}" and versions[0].source == "scan"  # newest first
    assert [v.source for v in versions].count("agent") == 1
    # undone once
    assert codes(scene.manager.revert, scene.root, scene.pid, run["id"], by=None) == "reverted"
    assert codes(scene.manager.reply, scene.root, scene.pid, run["id"], "x", by=None, by_name=None,
                 lang="en") == "reverted"


def test_revert_keeps_a_task_edit_the_person_made_since(scene: Scene):
    run = revert_scene(scene)
    tasks, revision = workspace.read_tasks(scene.root)
    edited = [dict(t, text="Analyse the sample, renamed", owner="Lin") for t in tasks]
    workspace.save_tasks(scene.root, {"tasks": edited, "revision": revision})
    scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    task = scene.tasks()["t1"]
    assert task["text"] == "Analyse the sample, renamed" and task["owner"] == "Lin"
    assert task["done"] is False and "report" not in task


def test_revert_all_skipped_leaves_the_run_not_undone(scene: Scene):
    run = scene.go([init_step(), {"op": "write", "path": "mine.txt", "text": "agent\n"}, finish_step()])
    scene.put("mine.txt", "the person rewrote it\n")
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["skipped"] == [{"path": "mine.txt", "reason": "changed_since"}]
    assert result["restored"] == [] and result["removed"] == []
    assert result["run"]["reverted"] is False and result["run"]["can_revert"] is True
    assert (scene.root / "mine.txt").read_text() == "the person rewrote it\n"
    (scene.root / "mine.txt").write_text("agent\n")  # put it back, retry
    again = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert again["removed"] == ["mine.txt"] and again["run"]["reverted"] is True


def test_revert_a_file_without_a_copy_is_skipped(scene: Scene, monkeypatch):
    scene.put("big.txt", "v0 is longer than the limit\n")
    scene.put("small.txt", "s")
    monkeypatch.setattr(snapshot, "COPY_LIMIT", 3)
    run = scene.go([init_step(), {"op": "write", "path": "big.txt", "text": "v1\n"},
                    {"op": "write", "path": "n.txt", "text": "n"}, finish_step()])
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert {"path": "big.txt", "reason": "no_copy"} in result["skipped"]
    assert (scene.root / "big.txt").read_text() == "v1\n"
    assert result["removed"] == ["n.txt"] and result["run"]["reverted"] is True


def test_revert_a_corrupt_copy_is_never_written_back(scene: Scene):
    scene.put("old.txt", "v0\n")
    run = scene.go([init_step(), {"op": "write", "path": "old.txt", "text": "v1\n"}, finish_step()])
    (run_store.run_dir(scene.pid, run["id"]) / "before" / "old.txt").write_text("tampered\n")
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["skipped"] == [{"path": "old.txt", "reason": "no_copy"}]
    assert (scene.root / "old.txt").read_text() == "v1\n"


def test_later_run_blocks_an_undo(scene: Scene):
    scene.put("shared.txt", "v0\n")
    scene.write_tasks([{"id": "t1", "text": "A", "owner": "agent:claude-code:sonnet"},
                       {"id": "t2", "text": "B", "owner": "agent:claude-code:sonnet"}])
    first = scene.go([init_step(), {"op": "write", "path": "shared.txt", "text": "v1\n"},
                      {"op": "complete"}, finish_step()], task_id="t1")
    second = scene.go([init_step(), {"op": "write", "path": "shared.txt", "text": "v2\n"},
                       {"op": "complete"}, finish_step()], task_id="t2")
    assert first["state"] == second["state"] == "done"
    # the two runs both changed tasks.json, which is handled logically and is no obstacle by itself
    assert codes(scene.manager.revert, scene.root, scene.pid, first["id"], by=None) == "later_run"
    scene.manager.revert(scene.root, scene.pid, second["id"], by=None)
    assert (scene.root / "shared.txt").read_text() == "v1\n"
    scene.manager.revert(scene.root, scene.pid, first["id"], by=None)
    assert (scene.root / "shared.txt").read_text() == "v0\n"
    tasks = scene.tasks()
    assert tasks["t1"]["done"] is False and tasks["t2"]["done"] is False


def test_unrelated_later_run_does_not_block_an_undo(scene: Scene):
    first = scene.go([init_step(), {"op": "write", "path": "one.txt", "text": "1"}, finish_step()])
    scene.go([init_step(), {"op": "write", "path": "two.txt", "text": "2"}, finish_step()])
    assert scene.manager.revert(scene.root, scene.pid, first["id"], by=None)["removed"] == ["one.txt"]


def test_revert_refusals(scene: Scene):
    empty = scene.go([init_step(), finish_step()])
    assert codes(scene.manager.revert, scene.root, scene.pid, empty["id"], by=None) == "not_revertible"
    assert codes(scene.manager.revert, scene.root, scene.pid, "r-20200101T000000Z-aaaaaa", by=None) == "run_missing"
    run = scene.go([init_step(), {"op": "write", "path": "a.txt", "text": "a"}, finish_step()])
    run_store.update_run(scene.pid, run["id"], revertible=False)  # what prune does to an old run
    assert codes(scene.manager.revert, scene.root, scene.pid, run["id"], by=None) == "not_revertible"
    assert scene.manager.runs(scene.pid)["runs"][0]["can_revert"] is False


def test_reply_refusals(scene: Scene):
    run = scene.go([init_step(), finish_step()])
    reply = scene.manager.reply
    assert codes(reply, scene.root, scene.pid, run["id"], "   ", by=None, by_name=None, lang="en") == "invalid"
    assert codes(reply, scene.root, scene.pid, run["id"], "x" * 8001, by=None, by_name=None, lang="en") == "invalid"
    assert codes(reply, scene.root, scene.pid, "r-20200101T000000Z-aaaaaa", "x", by=None, by_name=None,
                 lang="en") == "run_missing"
    # a run without a conversation (the program never said its session id)
    rd = run_store.run_dir(scene.pid, run["id"])
    run_store.update_run(scene.pid, run["id"], session_id=None)
    assert codes(reply, scene.root, scene.pid, run["id"], "x", by=None, by_name=None, lang="en") == "no_session"
    run_store.update_run(scene.pid, run["id"], session_id="abc")
    # the task moved to another agent
    scene.write_tasks([{"id": "t1", "text": "Analyse", "owner": "agent:claude-code:opus"}])
    assert codes(reply, scene.root, scene.pid, run["id"], "x", by=None, by_name=None, lang="en") == "not_agent_task"
    scene.write_tasks([{"id": "t1", "text": "Analyse", "owner": "Zhang San"}])
    assert codes(reply, scene.root, scene.pid, run["id"], "x", by=None, by_name=None, lang="en") == "not_agent_task"
    scene.write_tasks([])
    assert codes(reply, scene.root, scene.pid, run["id"], "x", by=None, by_name=None, lang="en") == "task_missing"
    assert rd.is_dir()


def test_reply_that_cannot_start_gives_the_done_mark_back(scene: Scene, monkeypatch):
    done = scene.go(COMPLETE)
    assert scene.tasks()["t1"]["done"] is True
    monkeypatch.setattr(profiles, "mcp_available", lambda: False)
    assert codes(scene.manager.reply, scene.root, scene.pid, done["id"], "x", by=None, by_name=None,
                 lang="en") == "mcp_missing"
    assert scene.tasks()["t1"]["done"] is True  # checked before the mark is cleared
    monkeypatch.setattr(profiles, "mcp_available", lambda: True)
    real = scene.manager._launch

    def refuse(*a, **kw):
        raise manager.AgentError("busy")

    monkeypatch.setattr(scene.manager, "_launch", refuse)
    assert codes(scene.manager.reply, scene.root, scene.pid, done["id"], "x", by=None, by_name=None,
                 lang="en") == "busy"
    assert scene.tasks()["t1"]["done"] is True and scene.tasks()["t1"]["report"] == done["report"]
    monkeypatch.setattr(scene.manager, "_launch", real)



def test_send_back_maps_a_busy_or_refused_task_save(scene: Scene, monkeypatch):
    """Clearing the done mark is a write under the project lock: a lock that
    cannot be had is `busy`, a refused write `read_only` -- never a raw error."""
    from researchos.records import lock as records_lock
    from researchos.records import situation

    done = scene.go(COMPLETE)
    for error, code in ((records_lock.ProjectLockTimeout("held"), "busy"),
                        (situation.ProjectMovedError("moved -- nothing written"), "read_only")):
        def boom(*_a, _error=error, **_k):
            raise _error

        monkeypatch.setattr(manager.workspace, "save_tasks", boom)
        assert codes(scene.manager.reply, scene.root, scene.pid, done["id"], "x", by=None, by_name=None,
                     lang="en") == code
    assert scene.tasks()["t1"]["done"] is True


# -- recovery -------------------------------------------------------------------------------------------------------


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def stale_run(scene: Scene, *, state: str = "running", pid: int | None = None, engine_pid: int | None = None,
              before: bool = True, program_started: str | None = None, engine_started: str | None = None,
              where: tuple[str, Path] | None = None,
              **more) -> dict:
    run_id = run_store.new_run_id()
    project_id, root = where or (scene.pid, scene.root)  # another project of this Mac, when `where` names it
    run = {"id": run_id, "project_id": project_id, "root": str(root), "task_id": "t1", "task_text": "x",
           "task_due": None, "runner": "claude-code", "model": "sonnet", "owner": "agent:claude-code:sonnet",
           "label": "Claude Code · sonnet", "by": "m-1", "by_name": None, "lang": "en", "kind": "start",
           "parent": None, "message": None, "state": state, "reverted": False, "created": run_store.now_iso(),
           "started": run_store.now_iso(), "ended": None, "session_id": "s1", "pid": pid,
           "engine_pid": engine_pid if engine_pid is not None else dead_pid(), "changes": {}, "violations": [],
           "revertible": False, "program_started": program_started, "engine_started": engine_started, **more}
    run_store.create_run(project_id, run)
    if before:
        rd = run_store.run_dir(project_id, run_id)
        picture = snapshot.take(root, copy_to=rd / "before")
        picture["protected"] = snapshot.protected(root, picture)
        (rd / "before.json").write_text(json.dumps(picture))
    return run


def test_stale_running_run_is_marked_interrupted_with_its_changes(scene: Scene):
    scene.put("keep.txt", "k")
    run = stale_run(scene)
    (scene.root / "made_by_agent.txt").write_text("m")
    listing = scene.manager.runs(scene.pid)
    assert listing["active"] is None
    (item,) = listing["runs"]
    assert item["state"] == "interrupted" and item["error"]["code"] == "engine_gone" and item["can_stop"] is False
    assert scene.manager.detail(scene.pid, run["id"])["steps"][-1]["code"] == "engine_gone"
    stored = scene.run(run["id"])
    assert stored["ended"] and stored["changes"]["created"] == ["made_by_agent.txt"] and stored["revertible"] is True
    assert item["can_revert"] is True
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["removed"] == ["made_by_agent.txt"]
    # a new run is possible now
    scene.scenario([init_step(), finish_step()])
    assert scene.wait(scene.start()["id"])["state"] == "ended"


def test_stale_run_in_preparing_of_this_very_process_is_recovered(scene: Scene):
    run = stale_run(scene, state="preparing", engine_pid=os.getpid(), before=False)
    assert scene.manager.runs(scene.pid)["runs"][0]["state"] == "interrupted"
    stored = scene.run(run["id"])
    assert stored["revertible"] is False and stored["error"]["code"] == "engine_gone"


def test_stale_run_ends_its_program_only_if_it_is_still_that_program(scene: Scene):
    scene.scenario([{"op": "sleep", "s": 120}])
    ours = subprocess.Popen([str(scene.bins["claude-code"]), "sleep"])  # `ps` shows the fake `claude`
    other = subprocess.Popen(["sleep", "120"])
    try:
        stale_run(scene, pid=other.pid, program_started="Mon Jan  1 00:00:00 1990")  # another process's start time
        scene.manager.runs(scene.pid)
        assert other.poll() is None  # a pid that is not the agent's program is left alone
        stale_run(scene, pid=ours.pid, program_started=manager._start_of(ours.pid))
        scene.manager.runs(scene.pid)
        assert ours.wait(timeout=15) in (-15, -9)
    finally:
        for proc in (ours, other):
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def test_a_recycled_pid_whose_command_line_names_the_program_is_not_killed(scene: Scene):
    """Recovery signals a process only when pid AND start time are the ones recorded at launch (a substring
    of the command line is not an identity), and never its process group otherwise."""
    bystander = subprocess.Popen(["bash", "-c", "exec -a my-claude-notes sleep 120"], start_new_session=True)
    try:
        time.sleep(0.3)
        assert "claude" in manager._command_of(bystander.pid)
        stale_run(scene, pid=bystander.pid, program_started="Mon Jan  1 00:00:00 1990")  # another start time
        stale_run(scene, pid=bystander.pid)  # no start time recorded at all
        listing = scene.manager.runs(scene.pid)
        assert [r["state"] for r in listing["runs"]] == ["interrupted", "interrupted"]
        time.sleep(0.5)
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()


def test_a_run_of_another_live_engine_is_left_alone(scene: Scene):
    engine = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        run = stale_run(scene, engine_pid=engine.pid)
        listing = scene.manager.runs(scene.pid)
        assert listing["active"] == run["id"] and listing["runs"][0]["state"] == "running"
        assert listing["runs"][0]["can_stop"] is False and listing["runs"][0]["can_revert"] is False
        assert codes(scene.manager.start, scene.root, scene.pid, "t1", by=None, by_name=None, lang="en") == "busy"
        assert codes(scene.manager.revert, scene.root, scene.pid, run["id"], by=None) == "run_active"
        assert scene.manager.stop(scene.pid, run["id"])["state"] == "running"
    finally:
        engine.kill()
        engine.wait()
    assert scene.manager.runs(scene.pid)["runs"][0]["state"] == "interrupted"


# -- retention ---------------------------------------------------------------------------------------------------------


def test_prune_after_a_run(scene: Scene, monkeypatch):
    monkeypatch.setattr(run_store, "KEEP_RUNS", 3)
    monkeypatch.setattr(run_store, "KEEP_BACKUPS", 2)
    ids = []
    for i in range(5):
        ids.append(scene.go([init_step(), {"op": "write", "path": f"f{i}.txt", "text": str(i)}, finish_step()])["id"])
    kept = [r["id"] for r in run_store.list_runs(scene.pid)]
    assert kept == ids[::-1][:3]
    newest, middle, oldest = (run_store.run_dir(scene.pid, i) for i in kept)
    assert (newest / "before").is_dir() and (middle / "before").is_dir() and not (oldest / "before").exists()
    assert scene.run(kept[2])["revertible"] is False and scene.run(kept[0])["revertible"] is True
    assert scene.manager.runs(scene.pid)["runs"][2]["can_revert"] is False
    assert codes(scene.manager.revert, scene.root, scene.pid, kept[2], by=None) == "not_revertible"


# -- the summary function ----------------------------------------------------------------------------------------------


def test_summary_flags():
    base = {"id": "r-1", "task_id": "t", "state": "done", "session_id": "s", "revertible": True,
            "changes": {"created": ["a"], "modified": [], "deleted": [], "by_person": ["z"]}, "violations": [1, 2]}
    s = manager.summary(base, latest_of_task=True, active=False)
    assert s["can_reply"] and s["can_revert"] and not s["can_stop"] and s["changes"] == 1 and s["violations"] == 2
    assert not manager.summary(base, latest_of_task=False, active=False)["can_reply"]
    assert not manager.summary({**base, "reverted": True}, latest_of_task=True, active=False)["can_reply"]
    assert not manager.summary({**base, "reverted": True}, latest_of_task=True, active=False)["can_revert"]
    assert not manager.summary({**base, "session_id": None}, latest_of_task=True, active=False)["can_reply"]
    assert not manager.summary({**base, "revertible": False}, latest_of_task=True, active=False)["can_revert"]
    running = manager.summary({**base, "state": "running"}, latest_of_task=True, active=True)
    assert running["can_stop"] and not running["can_reply"] and not running["can_revert"]
    assert not manager.summary({**base, "state": "needs_input", "changes": {}}, latest_of_task=True,
                               active=False)["can_revert"]
    assert manager.summary({**base, "state": "needs_input"}, latest_of_task=True, active=False)["can_reply"]
    assert manager.summary({**base, "state": "preparing"}, latest_of_task=True, active=False)["can_reply"] is False
    assert manager.summary({}, latest_of_task=True, active=False)["can_stop"] is False
    # an out-of-bounds run reads so; what it would have read decides what can be done with it (18.1 item 4)
    assert s["state"] == s["outcome"] == "done"
    oob = manager.summary({**base, "state": "out_of_bounds", "outcome": "done"}, latest_of_task=True, active=False)
    assert oob["state"] == "out_of_bounds" and oob["outcome"] == "done" and oob["can_reply"] and oob["can_revert"]
    assert not manager.summary({**base, "state": "out_of_bounds", "outcome": "failed", "session_id": None},
                               latest_of_task=True, active=False)["can_reply"]
    assert manager.summary({**base, "state": "out_of_bounds", "outcome": "stopped"}, latest_of_task=True,
                           active=False)["can_reply"]
    assert manager.summary({**base, "state": "out_of_bounds"}, latest_of_task=True, active=False)["outcome"] == (
        "out_of_bounds")  # no outcome kept: nothing to continue from
    assert run_store.outcome_of({"state": "out_of_bounds", "outcome": "ended"}) == "ended"
    assert run_store.outcome_of({"state": "done", "outcome": "ended"}) == "done"
    assert "out_of_bounds" in run_store.STATES and "out_of_bounds" not in run_store.ACTIVE_STATES
    assert manager._warn_if_out_of_bounds("done", [{"path": "a", "reason": "x"}]) == ("out_of_bounds", "done")
    assert manager._warn_if_out_of_bounds("done", []) == ("done", None)
    assert manager._warn_if_out_of_bounds("failed", None) == ("failed", None)


def test_agent_error_table():
    for code in ("mcp_missing", "runner_missing", "task_missing", "not_agent_task", "task_done", "busy", "read_only",
                 "run_missing", "run_active", "not_latest", "no_session", "reverted", "later_run", "not_finished",
                 "invalid", "start_failed"):
        err = manager.AgentError(code)
        assert err.code == code and err.message and err.message_zh and err.status in (400, 403, 404, 409, 500)
        assert "→" not in err.message_zh and "->" not in err.message
    assert manager.AgentError("busy").status == 409 and manager.AgentError("task_missing").status == 404
    assert manager.AgentError("invalid", "custom").message == "custom"


def test_a_reused_pid_is_not_mistaken_for_the_noted_process():
    table = {10: (1, "S", "Wed Oct 21 10:00:00 2026"), 11: (1, "Z", "Wed Oct 21 10:00:00 2026")}
    assert manager._same_alive(table, 10, "Wed Oct 21 10:00:00 2026")
    assert not manager._same_alive(table, 10, "Wed Oct 21 10:00:05 2026")  # same pid, started later: another process
    assert not manager._same_alive(table, 11, "Wed Oct 21 10:00:00 2026")  # a zombie is not alive
    assert not manager._same_alive(table, 12, "x")


def test_kill_tree_on_a_gone_process_is_harmless():
    assert manager.kill_tree(dead_pid(), grace=0.1) == []


def test_ps_table_and_descendants():
    table = manager._ps_table()
    assert os.getpid() in table
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.2)
        assert child.pid in manager._descendants(manager._ps_table(), os.getpid())
    finally:
        child.kill()
        child.wait()


def test_steps_name_project_files_by_their_path_in_the_project(tmp_path):
    """The run log reads `Write: analysis/vol.py`, not the whole path from `/` (17.4)."""
    from researchos.agents import manager as manager_mod

    root = tmp_path / "Quant"
    root.mkdir()
    events = tmp_path / "events.jsonl"
    write = {"type": "assistant", "session_id": "s", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": f"{root}/analysis/vol.py"}}]}}
    events.write_text(json.dumps(write) + "\n", encoding="utf-8")
    steps, total = manager_mod.AgentManager._steps(events, {"runner": "claude-code", "root": str(root),
                                                            "state": "done", "lang": "en"})
    assert steps[0]["text"] == "Write: analysis/vol.py" and total == 2


# -- review fixes: the hook canary, leftovers, the person's saves, recovery, undo, retention ---------------------------


def after_take_hook(monkeypatch, action) -> None:
    """Run `action()` at the start of every after-picture (`snapshot.take` without a copy folder)."""
    real_take = snapshot.take

    def take(root, copy_to=None, **kwargs):
        if copy_to is None:
            action()
        return real_take(root, copy_to, **kwargs)

    monkeypatch.setattr(snapshot, "take", take)


def test_a_hook_that_does_not_refuse_ends_the_run_before_the_program_starts(scene: Scene, monkeypatch):
    seen = []
    monkeypatch.setattr(manager.runners, "hook_canary",
                        lambda run_dir: seen.append(run_dir) or "the hook probe was not refused (exit code 1, expected 2)")
    scene.scenario(COMPLETE)
    run = scene.wait(scene.start()["id"])
    assert run["state"] == "failed" and run["error"]["code"] == "guard_failed"
    assert "not refused" in run["error"]["detail"]
    assert scene.calls() == [] and run["pid"] is None  # the program was never started
    assert seen == [run_store.run_dir(scene.pid, run["id"])]
    detail = scene.manager.detail(scene.pid, run["id"])
    assert detail["error"]["code"] == "guard_failed" and detail["snapshot"]["partial"] is False


def test_the_hook_is_really_tried_before_claude_code_starts(scene: Scene, monkeypatch):
    """No stub: the real hook command runs against the run's guard file and refuses (OpenCode has no hook)."""
    calls = []
    real = manager.runners.hook_canary
    monkeypatch.setattr(manager.runners, "hook_canary", lambda rd: calls.append(rd) or real(rd))
    assert scene.go([init_step(), finish_step()])["state"] == "ended"
    assert len(calls) == 1
    scene.write_tasks([{"id": "t1", "text": "x", "owner": "agent:opencode:anthropic/claude-sonnet-4"}])
    assert scene.go([init_step(), finish_step()], runner="opencode")["state"] == "ended"
    assert len(calls) == 1


def test_what_the_program_left_running_is_ended_before_the_after_picture(scene: Scene, monkeypatch):
    child = scene.pidfile("child.pid")
    seen = {}
    after_take_hook(monkeypatch, lambda: seen.setdefault("alive", alive(int(child.read_text()))))
    run = scene.go([init_step(), {"op": "spawn", "pidfile": str(child), "new_session": True},
                    {"op": "sleep", "s": 1.2}, {"op": "write", "path": "out.txt", "text": "x"}, finish_step()])
    assert run["state"] == "ended"
    assert seen == {"alive": False}  # gone by the time the after-picture is taken
    assert wait_gone(int(child.read_text()))
    assert run["leftovers"] == 1
    assert scene.manager.detail(scene.pid, run["id"])["leftovers"] == 1
    assert scene.manager.runs(scene.pid)["runs"][0]["state"] == "ended"


def test_a_run_without_leftovers_says_zero(scene: Scene):
    assert scene.go([init_step(), finish_step()])["leftovers"] == 0


def test_a_chatty_descendant_does_not_keep_a_finished_run_going(scene: Scene, monkeypatch):
    monkeypatch.setattr(manager, "EXIT_DRAIN_S", 0.5)
    child = scene.pidfile("child.pid")
    scene.scenario([init_step(), {"op": "spawn", "pidfile": str(child), "new_session": True, "chatty": True,
                                  "inherit_stdout": True}, {"op": "sleep", "s": 1.0}, finish_step()])
    started = time.monotonic()
    run = scene.wait(scene.start()["id"], timeout=20)
    assert time.monotonic() - started < 15
    assert run["state"] == "ended" and run["leftovers"] == 1
    assert wait_gone(int(child.read_text()))


def test_stop_ends_a_run_whose_program_exited_but_whose_output_is_still_held(scene: Scene, monkeypatch):
    monkeypatch.setattr(manager, "EXIT_DRAIN_S", 60.0)  # the drain alone would take a minute
    child, agent = scene.pidfile("child.pid"), scene.pidfile("agent.pid")
    scene.scenario([init_step(), {"op": "pidfile", "pidfile": str(agent)},
                    {"op": "spawn", "pidfile": str(child), "new_session": True, "chatty": True,
                     "inherit_stdout": True}, {"op": "sleep", "s": 1.0}, finish_step()])
    started = scene.start()
    agent_pid = int(scene.wait_file(agent))
    assert wait_gone(agent_pid)
    time.sleep(0.3)
    began = time.monotonic()
    stopped = scene.manager.stop(scene.pid, started["id"])
    assert time.monotonic() - began < 15
    assert stopped["state"] == "stopped" and stopped["can_stop"] is False
    assert wait_gone(int(child.read_text()))


def test_the_pump_notes_the_programs_descendants_while_it_runs(scene: Scene):
    child = scene.pidfile("child.pid")
    scene.scenario([init_step(), {"op": "spawn", "pidfile": str(child), "new_session": True},
                    {"op": "wait_file", "path": str(scene.tmp / "never"), "timeout": 2.5}, finish_step()])
    started = scene.start()
    child_pid = int(scene.wait_file(child))
    end = time.monotonic() + 10
    act = None
    while time.monotonic() < end:
        with scene.manager._lock:
            act = scene.manager._active.get(scene.pid)
        if act is not None and child_pid in act.tree:
            break
        time.sleep(0.05)
    assert act is not None and child_pid in act.tree and act.tree[child_pid]
    scene.wait(started["id"])


def test_the_run_records_when_the_engine_and_the_program_started(scene: Scene):
    run = scene.go([init_step(), finish_step()])
    assert run["engine_started"] and run["engine_started"] == manager._start_of(os.getpid())
    assert run["program_started"]  # ps lstart of the program's pid right after launch


def test_the_person_saving_during_the_after_picture_is_not_the_agent(scene: Scene, monkeypatch):
    scene.put("mine.txt", "v0\n")
    after_take_hook(monkeypatch, lambda: (record_files.durable_write(scene.root / "mine.txt", b"v1 by the person\n"),
                                          record_files.write_new_file(scene.root / "person-note.md", b"# new\n")))
    run = scene.go([init_step(), {"op": "write", "path": "agent.txt", "text": "a\n"}, finish_step()])
    assert run["changes"]["created"] == ["agent.txt"] and run["changes"]["modified"] == []
    assert run["changes"]["by_person"] == ["mine.txt", "person-note.md"]
    assert run["human_paths"] == ["mine.txt", "person-note.md"]
    assert scene.versions("person-note.md") == [] and all(v.source != "agent" for v in scene.versions("mine.txt"))
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["removed"] == ["agent.txt"] and result["restored"] == []
    assert (scene.root / "mine.txt").read_text() == "v1 by the person\n" and (scene.root / "person-note.md").exists()


def test_undo_leaves_a_path_the_person_saved_during_the_run_alone(scene: Scene):
    scene.put("old.txt", "v0\n")
    run = scene.go([init_step(), {"op": "write", "path": "old.txt", "text": "v1\n"},
                    {"op": "write", "path": "new.txt", "text": "n\n"}, finish_step()])
    assert run["changes"]["modified"] == ["old.txt"] and run["changes"]["created"] == ["new.txt"]
    # a record that lists a person's path among the changes (an older record, a race): undo does not touch it
    run_store.update_run(scene.pid, run["id"], human_paths=["new.txt", "old.txt"])
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert sorted(s["path"] for s in result["skipped"]) == ["new.txt", "old.txt"]
    assert {s["reason"] for s in result["skipped"]} == {"changed_since"}
    assert result["removed"] == [] and result["restored"] == []
    assert (scene.root / "old.txt").read_text() == "v1\n" and (scene.root / "new.txt").exists()


def test_a_protected_path_the_person_saved_is_still_checked_against_the_agent(scene: Scene):
    scene.put("data.csv", "a,b\n1,2\n")
    scene.put("data2.csv", "a\n1\n")
    gate = scene.tmp / "gate"
    both = scene.root / ".researchos" / "both.toml"
    only = scene.root / ".researchos" / "only-me.toml"
    scene.scenario([init_step(), {"op": "wait_file", "path": str(gate)},
                    {"op": "write", "path": str(both), "text": "agent = 1\n"},
                    {"op": "write", "path": "data.csv", "text": "a,b\n9,9\n"}, finish_step()])
    started = scene.start()
    scene.wait_state(started["id"], "running")
    for path, data in ((both, b"person = 1\n"), (only, b"person = 2\n"), (scene.root / "data.csv", b"a,b\n5,5\n"),
                       (scene.root / "data2.csv", b"a\n2\n")):
        record_files.durable_write(path, data)
    gate.write_text("go")
    run = scene.wait(started["id"])
    reasons = {(v["path"], v["reason"]) for v in run["violations"]}
    # the person saved these and the agent wrote them after: out of bounds although the person touched them
    assert (".researchos/both.toml", "researchos_changed") in reasons and ("data.csv", "data_changed") in reasons
    # what only the person wrote is allowed to end with exactly what the person wrote
    assert not any(v["path"] in (".researchos/only-me.toml", "data2.csv") for v in run["violations"])
    # and none of it is the agent's change list
    assert set(run["changes"]["by_person"]) >= {".researchos/both.toml", ".researchos/only-me.toml", "data.csv", "data2.csv"}
    assert not {".researchos/both.toml", "data.csv", "data2.csv"} & set(run["changes"]["modified"] + run["changes"]["created"])


def test_a_stale_listing_does_not_turn_a_finished_run_into_interrupted(scene: Scene, monkeypatch):
    run = scene.go([init_step(), {"op": "write", "path": "a.txt", "text": "a"}, {"op": "complete"}, finish_step()])
    assert run["state"] == "done"
    stale = dict(run, state="running", ended=None)
    # (a) the listing is older than the run's end: _recover reads the run again
    monkeypatch.setattr(run_store, "list_runs", lambda project_id: [stale])
    assert scene.manager._recover(scene.pid) == []
    assert scene.run(run["id"])["state"] == "done"
    # (b) and the write itself only happens while the run is still active
    scene.manager._interrupt_stale(scene.pid, stale)
    after = scene.run(run["id"])
    assert after["state"] == "done" and after["error"] is None and after["ended"] == run["ended"]


def test_update_run_if_active_is_a_compare_and_set(scene: Scene):
    run = stale_run(scene, before=False)
    assert run_store.update_run_if_active(scene.pid, run["id"], state="failed")["state"] == "failed"
    assert run_store.update_run_if_active(scene.pid, run["id"], state="interrupted") is None
    assert scene.run(run["id"])["state"] == "failed"
    with pytest.raises(KeyError):
        run_store.update_run_if_active(scene.pid, "r-20200101T000000Z-aaaaaa", state="failed")


def test_a_recycled_engine_pid_does_not_make_a_dead_engines_run_look_live(scene: Scene):
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        run = stale_run(scene, engine_pid=bystander.pid, engine_started="Mon Jan  1 00:00:00 1990")
        listing = scene.manager.runs(scene.pid)
        assert listing["active"] is None and listing["runs"][0]["state"] == "interrupted"
        assert scene.run(run["id"])["error"]["code"] == "engine_gone"
        assert bystander.poll() is None
        # the project is free again
        scene.scenario([init_step(), finish_step()])
        assert scene.wait(scene.start()["id"])["state"] == "ended"
        # the same process with the start time that was recorded is the engine
        live = stale_run(scene, engine_pid=bystander.pid, engine_started=manager._start_of(bystander.pid))
        listing = scene.manager.runs(scene.pid)
        assert listing["active"] == live["id"] and listing["runs"][0]["state"] == "running"
    finally:
        bystander.kill()
        bystander.wait()


def test_rename_of_a_file_without_a_copy_is_undone_by_moving_it_back(scene: Scene, monkeypatch):
    scene.put("raw.txt", "the only copy of the raw data\n")
    monkeypatch.setattr(snapshot, "COPY_LIMIT", 5)  # nothing is copied
    run = scene.go([init_step(), {"op": "rename", "from": "raw.txt", "to": "sub/raw_old.txt"}, finish_step()])
    assert run["changes"]["created"] == ["sub/raw_old.txt"] and run["changes"]["deleted"] == ["raw.txt"]
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert (scene.root / "raw.txt").read_text() == "the only copy of the raw data\n"
    assert not (scene.root / "sub").exists()  # the folder the move emptied is gone too
    assert result["restored"] == ["raw.txt"] and result["removed"] == [] and result["skipped"] == []
    rd = run_store.run_dir(scene.pid, run["id"])
    assert not (rd / "reverted").exists()  # nothing was parked in the run folder
    assert result["run"]["reverted"] is True


def test_a_cut_picture_does_not_call_the_persons_old_files_created(scene: Scene, monkeypatch):
    for i in range(6):
        scene.put(f"f{i}.txt", str(i))
    total = len(snapshot.take(scene.root)["files"])
    monkeypatch.setattr(snapshot, "FILE_LIMIT", total - 2)  # the last two files of the walk are never seen
    run = scene.go([init_step(), {"op": "delete", "path": "f0.txt"}, {"op": "delete", "path": "f1.txt"},
                    {"op": "write", "path": "new.txt", "text": "n"}, finish_step()])
    assert run["snapshot"]["partial"] is True
    # two files fewer and one more: the after-picture's cut moves, and a file of the tail (.researchos/...) comes
    # into view -- it is the person's old file, not a creation
    assert run["changes"]["created"] == ["new.txt"] and run["changes"]["deleted"] == ["f0.txt", "f1.txt"]
    assert not any(c.startswith(".researchos") for c in run["changes"]["created"])
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["removed"] == ["new.txt"] and sorted(result["restored"]) == ["f0.txt", "f1.txt"]
    assert (scene.root / ".researchos" / "project.toml").is_file()


def test_undo_never_moves_the_projects_own_records_away(scene: Scene):
    run = scene.go([init_step(), {"op": "write", "path": "new.txt", "text": "n"}, finish_step()])
    rd = run_store.run_dir(scene.pid, run["id"])
    after = json.loads((rd / "after.json").read_text())
    after["files"][".researchos/project.toml"] = snapshot.fingerprint(scene.root, ".researchos/project.toml")
    (rd / "after.json").write_text(json.dumps(after))
    changes = dict(run["changes"], created=[".researchos/project.toml", "new.txt"])
    run_store.update_run(scene.pid, run["id"], changes=changes)
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert {"path": ".researchos/project.toml", "reason": "protected"} in result["skipped"]
    assert result["removed"] == ["new.txt"] and (scene.root / ".researchos" / "project.toml").is_file()


def test_a_file_replaced_by_a_folder_is_restored_by_undo(scene: Scene):
    scene.put("results", "this was a file\n")
    run = scene.go([init_step(), {"op": "delete", "path": "results"},
                    {"op": "write", "path": "results/a.txt", "text": "a\n"}, {"op": "write", "path": "deep/er/x.txt",
                                                                                   "text": "x\n"}, finish_step()])
    assert "results" in run["changes"]["deleted"] and "results/a.txt" in run["changes"]["created"]
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["skipped"] == [] and "results" in result["restored"]
    assert (scene.root / "results").read_text() == "this was a file\n"
    assert not (scene.root / "deep").exists()  # folders only the run's files lived in are gone
    assert result["run"]["reverted"] is True


def test_an_empty_folder_where_a_deleted_file_was_does_not_block_its_restore(scene: Scene):
    scene.put("gone.txt", "g\n")
    run = scene.go([init_step(), {"op": "delete", "path": "gone.txt"}, finish_step()])
    (scene.root / "gone.txt").mkdir()
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["restored"] == ["gone.txt"] and (scene.root / "gone.txt").read_text() == "g\n"


def test_a_folder_with_something_in_it_where_a_deleted_file_was_is_left_alone(scene: Scene):
    scene.put("gone.txt", "g\n")
    run = scene.go([init_step(), {"op": "delete", "path": "gone.txt"}, finish_step()])
    (scene.root / "gone.txt").mkdir()
    (scene.root / "gone.txt" / "mine.txt").write_text("the person's\n")
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["skipped"] == [{"path": "gone.txt", "reason": "changed_since"}] and result["restored"] == []
    assert (scene.root / "gone.txt" / "mine.txt").read_text() == "the person's\n"


def test_a_partial_undo_is_counted_in_the_summary(scene: Scene):
    run = revert_scene(scene)
    scene.put("edited.txt", "e2 by the person\n")
    time.sleep(0.01)
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["run"]["reverted"] is True and result["run"]["revert_skipped"] == 1
    listed = scene.manager.runs(scene.pid)["runs"][0]
    assert listed["revert_skipped"] == 1
    detail = scene.manager.detail(scene.pid, run["id"])
    assert detail["revert"]["skipped"] == [{"path": "edited.txt", "reason": "changed_since"}]
    assert detail["revert_skipped"] == 1
    # a run never undone has no such field
    assert "revert_skipped" not in manager.summary({"id": "r", "state": "done"}, latest_of_task=True, active=False)
    assert manager.summary({"id": "r", "state": "done", "revert": {"skipped": [1, 2]}},
                           latest_of_task=True, active=False)["revert_skipped"] == 2


def test_git_tracked_files_are_not_attributed_to_the_agent(scene: Scene):
    scene.put("tracked.py", "v0\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q"], cwd=scene.root, check=True)
    subprocess.run([*git, "add", "tracked.py"], cwd=scene.root, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "i"], cwd=scene.root, check=True)
    run = scene.go([init_step(), {"op": "write", "path": "tracked.py", "text": "v1\n"},
                    {"op": "write", "path": "fresh.py", "text": "f\n"}, finish_step()])
    assert "tracked.py" in run["changes"]["modified"] and "fresh.py" in run["changes"]["created"]
    assert scene.versions("tracked.py") == []  # git is that file's history
    assert [v.source for v in scene.versions("fresh.py")] == ["agent"]


def test_undo_is_refused_while_another_engine_runs_the_project(scene: Scene):
    run = scene.go([init_step(), {"op": "write", "path": "a.txt", "text": "a"}, finish_step()])
    engine = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        stale_run(scene, engine_pid=engine.pid, engine_started=manager._start_of(engine.pid), before=False)
        assert codes(scene.manager.revert, scene.root, scene.pid, run["id"], by=None) == "busy"
        assert (scene.root / "a.txt").exists()
    finally:
        engine.kill()
        engine.wait()
    assert scene.manager.revert(scene.root, scene.pid, run["id"], by=None)["removed"] == ["a.txt"]


def test_undo_does_not_open_files_that_live_in_the_cloud(scene: Scene, monkeypatch):
    scene.put("mod.txt", "v0\n")
    run = scene.go([init_step(), {"op": "write", "path": "mod.txt", "text": "v1\n"},
                    {"op": "write", "path": "new.txt", "text": "n\n"}, finish_step()])
    real = snapshot.fingerprint
    monkeypatch.setattr(snapshot, "fingerprint", lambda root, rel: dict(real(root, rel) or {}, dataless=True)
                        if rel in ("mod.txt", "new.txt") else real(root, rel))
    opened = []
    monkeypatch.setattr(manager.shutil, "copy2", lambda *a, **k: opened.append(a))
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert sorted((s["path"], s["reason"]) for s in result["skipped"]) == [("mod.txt", "in_cloud"),
                                                                           ("new.txt", "in_cloud")]
    assert opened == [] and (scene.root / "mod.txt").read_text() == "v1\n" and (scene.root / "new.txt").exists()


def test_a_team_member_works_in_their_working_copy_and_nothing_is_proposed(scene: Scene, monkeypatch):
    """17.7: on a member's Mac the agent works in the working copy; what it wrote is the member's local
    change, attributed in the history; no proposal is made for them."""
    from researchos import proposals

    base = proposals.Base(project=scene.pid, snapshot="s", working_copy=str(scene.root.resolve()), official=None,
                          made="2026-10-21T00:00:00Z", files={})
    monkeypatch.setattr(proposals, "role_of", lambda root: ("member", "m-1", None))
    monkeypatch.setattr(proposals, "read_base", lambda pid: base if pid == scene.pid else None)
    made = []
    for name in ("make_proposal", "write_proposal"):
        monkeypatch.setattr(proposals, name, lambda *a, **k: made.append(a) or pytest.fail("a proposal was made"))
    run = scene.go(COMPLETE)
    assert run["state"] == "done", run["error"]
    assert "analysis.py" in run["changes"]["created"] and run["report"]
    assert (scene.root / "analysis.py").read_text() == "print(1)\n"
    (version,) = scene.versions("analysis.py")
    assert version.source == "agent" and version.who == "agent:claude-code:sonnet#m-1" and version.note == run["id"]
    assert made == []
    home = Path(os.environ["RESEARCHOS_HOME"])
    assert not (home / proposals.WORKING_DIRNAME).exists()  # no base, blob or journal was written for a proposal
    # the same member may undo it: still their own working copy
    assert scene.manager.revert(scene.root, scene.pid, run["id"], by="m-1")["run"]["reverted"] is True


def test_retention_keeps_the_log_of_many_runs_and_the_copies_of_a_few(scene: Scene, monkeypatch):
    assert run_store.KEEP_RUNS == 200 and run_store.KEEP_BACKUPS == 20
    monkeypatch.setattr(run_store, "KEEP_RUNS", 4)
    monkeypatch.setattr(run_store, "KEEP_BACKUPS", 2)
    ids = [scene.go([init_step(), {"op": "write", "path": f"f{i}.txt", "text": str(i)}, finish_step()])["id"]
           for i in range(6)]
    kept = [r["id"] for r in run_store.list_runs(scene.pid)]
    assert kept == ids[::-1][:4]
    dirs = [run_store.run_dir(scene.pid, i) for i in kept]
    for newest in dirs[:2]:
        assert (newest / "before").is_dir() and (newest / "before.json").exists() and (newest / "after.json").exists()
    for older in dirs[2:]:  # the log stays; the copies and the pictures are gone and the run cannot be undone
        for name in ("run.json", "events.jsonl", "instruction.txt"):
            assert (older / name).is_file(), name
        assert not (older / "before").exists() and not (older / "before.json").exists()
        assert not (older / "after.json").exists()
    assert not run_store.run_dir(scene.pid, ids[0]).exists() and not run_store.run_dir(scene.pid, ids[1]).exists()
    assert scene.run(kept[3])["revertible"] is False
    assert codes(scene.manager.revert, scene.root, scene.pid, kept[3], by=None) == "not_revertible"
    detail = scene.manager.detail(scene.pid, kept[3])  # the log of an older run still reads
    assert detail["instruction"] and detail["steps"]


# -- undo says what it will do, and asks (DESIGN 18.1 item 5) -----------------------------------------------


def tree_state(root: Path) -> dict[str, tuple[str, str]]:
    """Every file and folder under `root`, with the SHA-256 of each file: what a read must leave as it was."""
    import hashlib

    state: dict[str, tuple[str, str]] = {}
    for here, dirs, files in os.walk(root):
        for name in dirs:
            state[os.path.relpath(os.path.join(here, name), root)] = ("dir", "")
        for name in files:
            full = Path(here) / name
            state[os.path.relpath(full, root)] = ("file", hashlib.sha256(full.read_bytes()).hexdigest())
    return state


def undo_run(scene: Scene) -> dict:
    """A finished run that changed one file, deleted one, made two (a script and the result record), changed a
    fourth that the person then edited, and marked the task done."""
    scene.put("old.txt", "v0\n")
    scene.put("gone.txt", "g\n")
    scene.put("later.txt", "l0\n")
    run = scene.go(COMPLETE + [{"op": "write", "path": "old.txt", "text": "v1\n"},
                               {"op": "delete", "path": "gone.txt"},
                               {"op": "write", "path": "later.txt", "text": "l1\n"}])
    assert run["state"] == "done", run["error"]
    (scene.root / "later.txt").write_text("l2\n")  # after the run
    return run


def plan_paths(items: list[dict]) -> list[str]:
    return [item["path"] for item in items]


def test_the_undo_plan_lists_what_it_would_do_with_times_and_writes_nothing(scene: Scene):
    run = undo_run(scene)
    report = run["report"]
    before_tree, before_run = tree_state(scene.root), scene.run(run["id"])
    before_versions = {rel: len(scene.versions(rel)) for rel in ("old.txt", "gone.txt", "analysis.py")}
    plan = scene.manager.revert_plan(scene.root, scene.pid, run["id"])
    assert plan_paths(plan["restore"]) == ["gone.txt", "old.txt", "research/tasks.json"]
    assert plan_paths(plan["remove"]) == sorted(["analysis.py", report])
    assert plan["cannot"] == [{"path": "later.txt", "reason": "changed_since"}]
    assert plan["run"]["id"] == run["id"] and plan["run"]["can_revert"] is True
    # a file that is there carries its own modification time, local, to the second; one that is gone, the run's end
    by_path = {item["path"]: item for item in plan["restore"] + plan["remove"]}
    mtime = os.lstat(scene.root / "old.txt").st_mtime
    assert by_path["old.txt"]["time"] == manager._local_time(mtime)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d", by_path["old.txt"]["time"])
    assert by_path["analysis.py"]["time"] == manager._local_time(os.lstat(scene.root / "analysis.py").st_mtime)
    assert by_path["gone.txt"]["time"] == manager._local_time(run["ended"])
    # the done mark is listed as the task file, with the task it belongs to
    assert by_path["research/tasks.json"] == {"path": "research/tasks.json", "task": "Analyse the sample",
                                              "time": manager._local_time(run["ended"])}
    assert all("from" not in item for item in plan["restore"] + plan["remove"])
    # nothing was written: not the project, not the run's folder, not the run, not the history, not the task
    assert tree_state(scene.root) == before_tree and scene.run(run["id"]) == before_run
    assert not (run_store.run_dir(scene.pid, run["id"]) / "reverted").exists()
    assert {rel: len(scene.versions(rel)) for rel in before_versions} == before_versions
    assert scene.tasks()["t1"]["done"] is True
    # asked again, the same; and it is what the undo then does
    assert scene.manager.revert_plan(scene.root, scene.pid, run["id"]) == plan
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by="m-1")
    assert result["restored"] == plan_paths(plan["restore"]) and result["removed"] == plan_paths(plan["remove"])
    assert result["skipped"] == plan["cannot"]


def test_undo_with_a_choice_leaves_the_unchecked_files_alone_and_lists_them_as_kept(scene: Scene):
    run = undo_run(scene)
    report = run["report"]
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by="m-1",
                                  files=["gone.txt", report, "research/tasks.json"])
    assert result["restored"] == ["gone.txt", "research/tasks.json"] and result["removed"] == [report]
    assert sorted((s["path"], s["reason"]) for s in result["skipped"]) == [
        ("analysis.py", "kept"), ("later.txt", "changed_since"), ("old.txt", "kept")]
    assert (scene.root / "old.txt").read_text() == "v1\n" and (scene.root / "analysis.py").is_file()
    assert (scene.root / "gone.txt").read_text() == "g\n" and not (scene.root / report).exists()
    assert scene.tasks()["t1"]["done"] is False
    # something was undone, so the run reads undone, partly: the kept files are counted with the others
    assert result["run"]["reverted"] is True and result["run"]["revert_skipped"] == 3
    stored = scene.run(run["id"])
    assert stored["revert"]["skipped"] == result["skipped"]
    assert scene.manager.detail(scene.pid, run["id"])["revert"]["skipped"] == result["skipped"]
    assert f"undo {run['id']}" in [v.note for v in scene.versions("gone.txt")]
    assert f"undo {run['id']}" not in [v.note for v in scene.versions("old.txt")]  # it was not touched


def test_the_done_mark_is_one_of_the_choices(scene: Scene):
    run = undo_run(scene)
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None,
                                  files=["gone.txt", "old.txt", "analysis.py", run["report"]])
    assert ("research/tasks.json", "kept") in [(s["path"], s["reason"]) for s in result["skipped"]]
    assert "research/tasks.json" not in result["restored"]
    task = scene.tasks()["t1"]
    assert task["done"] is True and task["report"] == run["report"]  # the task still points at the result record


def test_the_choice_is_checked_and_a_path_the_plan_does_not_offer_is_never_acted_on(scene: Scene):
    run = undo_run(scene)
    before_tree = tree_state(scene.root)
    for bad in ([], "old.txt", [1], [""], ["old.txt", None], {"old.txt": True}):
        assert codes(scene.manager.revert, scene.root, scene.pid, run["id"], by=None, files=bad) == "invalid"
    assert codes(scene.manager.revert, scene.root, scene.pid, run["id"], by=None,
                 files=["a"] * (manager.UNDO_FILES_LIMIT + 1)) == "invalid"
    assert tree_state(scene.root) == before_tree and not scene.run(run["id"])["reverted"]
    # naming only files the plan does not offer undoes nothing: what could have been is kept, the rest keeps its reason
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None, files=["later.txt", "nothing.txt"])
    assert result["restored"] == [] and result["removed"] == []
    reasons = {s["path"]: s["reason"] for s in result["skipped"]}
    assert reasons["later.txt"] == "changed_since" and reasons["old.txt"] == "kept" and reasons["gone.txt"] == "kept"
    assert "nothing.txt" not in reasons and tree_state(scene.root) == before_tree
    stored = scene.run(run["id"])
    assert stored["reverted"] is False and stored["revert"]["skipped"] == result["skipped"]
    # nothing was undone, so the run can still be: all of it, now
    assert scene.manager.detail(scene.pid, run["id"])["revert"]["skipped"] == result["skipped"]
    full = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert full["restored"] == ["gone.txt", "old.txt", "research/tasks.json"]
    assert [s for s in full["skipped"] if s["reason"] == "kept"] == []
    assert full["run"]["reverted"] is True


def test_a_renamed_file_is_one_choice_under_its_old_name(scene: Scene, monkeypatch):
    scene.put("raw.txt", "the only copy of the raw data\n")
    monkeypatch.setattr(snapshot, "COPY_LIMIT", 5)  # nothing is copied
    run = scene.go([init_step(), {"op": "rename", "from": "raw.txt", "to": "sub/raw_old.txt"}, finish_step()])
    plan = scene.manager.revert_plan(scene.root, scene.pid, run["id"])
    assert [(i["path"], i["from"]) for i in plan["restore"]] == [("raw.txt", "sub/raw_old.txt")]
    assert plan["remove"] == [] and plan["cannot"] == []
    assert plan["restore"][0]["time"] == manager._local_time(os.lstat(scene.root / "sub" / "raw_old.txt").st_mtime)
    # left unchecked: the file keeps its new name, and nothing is made or removed
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None, files=["elsewhere.txt"])
    assert result["skipped"] == [{"path": "raw.txt", "reason": "kept"}] and result["restored"] == []
    assert (scene.root / "sub" / "raw_old.txt").is_file() and not (scene.root / "raw.txt").exists()
    assert scene.run(run["id"])["reverted"] is False
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None, files=["raw.txt"])
    assert result["restored"] == ["raw.txt"] and result["removed"] == [] and result["skipped"] == []
    assert (scene.root / "raw.txt").read_text() == "the only copy of the raw data\n"
    assert not (scene.root / "sub").exists()


def test_undo_keeps_the_folders_that_existed_before_the_run(scene: Scene):
    (scene.root / "figs").mkdir()  # empty
    (scene.root / "quiet").mkdir()  # holds only what the picture never records
    (scene.root / "quiet" / ".DS_Store").write_text("noise")
    scene.put("busy/a.txt", "a")
    run = scene.go([init_step(), {"op": "write", "path": "figs/plot.png", "text": "p"},
                    {"op": "write", "path": "quiet/new.txt", "text": "n"},
                    {"op": "write", "path": "busy/b.txt", "text": "b"},
                    {"op": "write", "path": "made/by/run.txt", "text": "r"}, finish_step()])
    before = json.loads((run_store.run_dir(scene.pid, run["id"]) / "before.json").read_text())
    assert {"figs", "quiet", "busy"} <= set(before["dirs"]) and "made" not in before["dirs"]
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["removed"] == ["busy/b.txt", "figs/plot.png", "made/by/run.txt", "quiet/new.txt"]
    for folder in ("figs", "quiet", "busy"):
        assert (scene.root / folder).is_dir(), folder  # the researcher's own folders, empty or not
    assert (scene.root / "quiet" / ".DS_Store").is_file() and (scene.root / "busy" / "a.txt").is_file()
    assert not (scene.root / "made").exists()  # made by the run, emptied by the undo


def test_undo_removes_python_and_pytest_caches_and_new_empty_folders(scene: Scene):
    run = scene.go([init_step(), {"op": "write", "path": "src/model.py", "text": "x = 1\n"},
                    {"op": "compile", "path": "src/model.py"},
                    {"op": "write", "path": ".pytest_cache/v/cache/nodeids", "text": '["test_model"]'},
                    {"op": "mkdir", "path": "outputs/unused/empty"}, finish_step()])
    cache = next((scene.root / "src/__pycache__").glob("model.*.pyc")).relative_to(scene.root).as_posix()
    assert cache in run["changes"]["created"]
    plan = scene.manager.revert_plan(scene.root, scene.pid, run["id"])
    assert cache in plan_paths(plan["remove"]) and ".pytest_cache/v/cache/nodeids" in plan_paths(plan["remove"])
    # A folder made after the run is not part of its cleanup.
    (scene.root / "later-empty").mkdir()
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["skipped"] == []
    assert set(result["removed"]) == {"src/model.py", cache, ".pytest_cache/v/cache/nodeids"}
    assert not (scene.root / "src").exists() and not (scene.root / ".pytest_cache").exists()
    assert not (scene.root / "outputs").exists() and (scene.root / "later-empty").is_dir()
    assert (run_store.run_dir(scene.pid, run["id"]) / "reverted" / cache).is_file()


def test_undo_restores_old_caches_and_keeps_cache_files_changed_after_the_run(scene: Scene):
    scene.put(".pytest_cache/v/cache/nodeids", '["old_test"]')
    scene.put("src/__pycache__/existing.pyc", "old bytecode")
    run = scene.go([init_step(), {"op": "write", "path": ".pytest_cache/v/cache/nodeids", "text": '["new_test"]'},
                    {"op": "write", "path": "src/__pycache__/existing.pyc", "text": "new bytecode"},
                    {"op": "write", "path": "src/__pycache__/keep.pyc", "text": "run bytecode"}, finish_step()])
    scene.put("src/__pycache__/keep.pyc", "later bytecode")
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert (scene.root / ".pytest_cache/v/cache/nodeids").read_text() == '["old_test"]'
    assert (scene.root / "src/__pycache__/existing.pyc").read_text() == "old bytecode"
    assert (scene.root / "src/__pycache__/keep.pyc").read_text() == "later bytecode"
    assert result["skipped"] == [{"path": "src/__pycache__/keep.pyc", "reason": "changed_since"}]


def test_undo_archives_a_new_virtual_environment(scene: Scene):
    run = scene.go([init_step(), {"op": "venv", "path": ".venv"}, finish_step()])
    assert run["changes"]["created"] == [".venv"]
    plan = scene.manager.revert_plan(scene.root, scene.pid, run["id"])
    assert plan["remove"][0]["path"] == ".venv" and plan["remove"][0]["directory"]
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["removed"] == [".venv"] and result["skipped"] == []
    assert not (scene.root / ".venv").exists()
    assert (run_store.run_dir(scene.pid, run["id"]) / "reverted/.venv/pyvenv.cfg").is_file()


def test_undo_does_not_archive_an_environment_with_an_incomplete_snapshot(scene: Scene, monkeypatch):
    monkeypatch.setattr(snapshot, "FILE_LIMIT", 5)
    run = scene.go([init_step(), {"op": "venv", "path": ".venv"}, finish_step()])
    after = json.loads((run_store.run_dir(scene.pid, run["id"]) / "after.json").read_text())
    assert after["files"][".venv"]["tree_partial"]
    plan = scene.manager.revert_plan(scene.root, scene.pid, run["id"])
    assert plan["remove"] == [] and plan["cannot"] == [{"path": ".venv", "reason": "not_recorded"}]
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["skipped"] == plan["cannot"] and not result["run"]["reverted"]
    assert (scene.root / ".venv/pyvenv.cfg").is_file()


def test_undo_keeps_a_new_environment_changed_after_the_run(scene: Scene):
    run = scene.go([init_step(), {"op": "venv", "path": ".venv"},
                    {"op": "write", "path": "generated.txt", "text": "run"}, finish_step()])
    scene.put(".venv/later-package.txt", "installed later")
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["removed"] == ["generated.txt"]
    assert result["skipped"] == [{"path": ".venv", "reason": "changed_since"}]
    assert (scene.root / ".venv/later-package.txt").read_text() == "installed later"


def test_undo_keeps_an_environment_that_existed_before_the_run(scene: Scene):
    scene.put(".venv/pyvenv.cfg", "existing environment")
    run = scene.go([init_step(), {"op": "write", "path": ".venv/installed.txt", "text": "package"},
                    {"op": "write", "path": "generated.txt", "text": "run"}, finish_step()])
    assert run["changes"]["created"] == ["generated.txt"]
    scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert (scene.root / ".venv/pyvenv.cfg").read_text() == "existing environment"
    assert (scene.root / ".venv/installed.txt").is_file()


def test_undo_folder_cleanup_does_not_follow_a_replaced_parent(scene: Scene, tmp_path):
    run = scene.go([init_step(), {"op": "mkdir", "path": "outputs/empty"},
                    {"op": "write", "path": "generated.txt", "text": "run"}, finish_step()])
    outside = tmp_path / "outside"
    (outside / "empty").mkdir(parents=True)
    (scene.root / "outputs/empty").rmdir()
    (scene.root / "outputs").rmdir()
    (scene.root / "outputs").symlink_to(outside, target_is_directory=True)
    scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert (outside / "empty").is_dir() and (scene.root / "outputs").is_symlink()


def test_a_picture_without_folders_still_keeps_the_folders_its_files_stand_in(scene: Scene):
    """A run whose picture was taken before `dirs` existed: the folders of its files are the ones it knows."""
    scene.put("busy/a.txt", "a")
    run = scene.go([init_step(), {"op": "write", "path": "busy/b.txt", "text": "b"},
                    {"op": "write", "path": "made/run.txt", "text": "r"}, finish_step()])
    path = run_store.run_dir(scene.pid, run["id"]) / "before.json"
    picture = json.loads(path.read_text())
    picture.pop("dirs")
    picture.pop("dirs_partial")
    path.write_text(json.dumps(picture))
    scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert (scene.root / "busy").is_dir() and not (scene.root / "made").exists()


def test_a_cut_picture_cannot_say_which_folders_existed_so_undo_removes_none(scene: Scene, monkeypatch):
    for i in range(6):
        scene.put(f"f{i}.txt", str(i))
    total = len(snapshot.take(scene.root)["files"])
    monkeypatch.setattr(snapshot, "FILE_LIMIT", total - 1)  # the walk meets `.aaa` before `.researchos`
    run = scene.go([init_step(), {"op": "write", "path": ".aaa/x.txt", "text": "x"}, finish_step()])
    assert run["snapshot"]["partial"] is True and run["changes"]["created"] == [".aaa/x.txt"]
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["removed"] == [".aaa/x.txt"]
    assert (scene.root / ".aaa").is_dir() and not (scene.root / ".aaa" / "x.txt").exists()
    assert manager._folders_before({"partial": True, "files": {}, "dirs": ["a"]}) is None
    assert manager._folders_before({"files": {}, "dirs": ["a"], "dirs_partial": True}) is None
    assert manager._folders_before({"files": {"a/b/c.txt": {}}, "dirs": ["x"]}) == {"a", "a/b", "x"}


def test_the_plan_refuses_as_the_undo_does(scene: Scene):
    first = scene.go([init_step(), {"op": "write", "path": "shared.txt", "text": "1"}, finish_step()])
    second = scene.go([init_step(), {"op": "write", "path": "shared.txt", "text": "2"}, finish_step()])
    assert codes(scene.manager.revert_plan, scene.root, scene.pid, first["id"]) == "later_run"
    assert codes(scene.manager.revert_plan, scene.root, scene.pid, "r-20200101T000000Z-aaaaaa") == "run_missing"
    assert codes(scene.manager.revert_plan, scene.root, "not-a-project-id!!", second["id"]) == "invalid"
    assert scene.manager.revert_plan(scene.root, scene.pid, second["id"])["remove"] == []  # 'shared.txt' is modified
    scene.manager.revert(scene.root, scene.pid, second["id"], by=None)
    assert codes(scene.manager.revert_plan, scene.root, scene.pid, second["id"]) == "reverted"
    assert scene.manager.revert_plan(scene.root, scene.pid, first["id"])["remove"] == [
        {"path": "shared.txt", "time": manager._local_time(os.lstat(scene.root / "shared.txt").st_mtime)}]
    # nothing to undo: a run that changed nothing and reported nothing
    empty = scene.go([init_step(), finish_step()])
    assert codes(scene.manager.revert_plan, scene.root, scene.pid, empty["id"]) == "not_revertible"
    # a run going elsewhere, and a run that is still going
    engine = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        stale_run(scene, engine_pid=engine.pid, engine_started=manager._start_of(engine.pid), before=False)
        assert codes(scene.manager.revert_plan, scene.root, scene.pid, first["id"]) == "busy"
    finally:
        engine.kill()
        engine.wait()


def test_the_plan_does_not_recover_a_run_a_gone_engine_left_going(scene: Scene):
    """Listing the runs recovers them (a write); asking for a plan only reads."""
    done = scene.go([init_step(), {"op": "write", "path": "a.txt", "text": "a"}, finish_step()])
    stale = stale_run(scene, before=False)
    assert codes(scene.manager.revert_plan, scene.root, scene.pid, stale["id"]) == "not_finished"
    assert scene.run(stale["id"])["state"] == "running"  # still as it was stored
    assert scene.manager.revert_plan(scene.root, scene.pid, done["id"])["remove"][0]["path"] == "a.txt"
    assert scene.run(stale["id"])["state"] == "running"


# -- at engine start (DESIGN 18.1 item 6) -------------------------------------------------------------------


def test_recover_all_recovers_the_runs_of_every_project_at_once(scene: Scene, tmp_path: Path):
    other_root = tmp_path / "other"
    other_root.mkdir()
    project.init_project(other_root)
    other_pid = read_identity(other_root).identity.id
    scene.put("keep.txt", "k")
    (other_root / "keep.txt").write_text("k")
    finished = scene.go([init_step(), finish_step()])  # a run that is over is not touched (starting one recovers)
    here = stale_run(scene)
    there = stale_run(scene, where=(other_pid, other_root))
    (scene.root / "made_here.txt").write_text("m")
    (other_root / "made_there.txt").write_text("m")
    assert scene.run(here["id"])["state"] == "running" and run_store.load_run(other_pid, there["id"])["state"] == "running"
    recovered = scene.manager.recover_all()  # nobody listed anything
    assert sorted(recovered) == sorted([here["id"], there["id"]])
    for pid, run in ((scene.pid, here), (other_pid, there)):
        stored = run_store.load_run(pid, run["id"])
        assert stored["state"] == "interrupted" and stored["error"]["code"] == "engine_gone" and stored["ended"]
    assert run_store.load_run(other_pid, there["id"])["changes"]["created"] == ["made_there.txt"]
    assert scene.run(here["id"])["changes"]["created"] == ["made_here.txt"]
    assert scene.run(finished["id"])["state"] == "ended"
    assert scene.manager.recover_all() == []  # nothing is left going


def test_recover_all_ends_a_program_only_when_it_is_verifiably_the_runs_and_leaves_other_engines_alone(
        scene: Scene, tmp_path: Path):
    other_root = tmp_path / "other"
    other_root.mkdir()
    project.init_project(other_root)
    other_pid = read_identity(other_root).identity.id
    scene.scenario([{"op": "sleep", "s": 120}])
    ours = subprocess.Popen([str(scene.bins["claude-code"]), "sleep"])  # `ps` shows the fake `claude`
    bystander = subprocess.Popen(["sleep", "120"])
    engine = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        stale_run(scene, pid=ours.pid, program_started=manager._start_of(ours.pid))
        stale_run(scene, where=(other_pid, other_root), pid=bystander.pid, program_started="Mon Jan  1 00:00:00 1990")
        live = stale_run(scene, engine_pid=engine.pid, engine_started=manager._start_of(engine.pid), before=False)
        recovered = scene.manager.recover_all()
        assert len(recovered) == 2 and live["id"] not in recovered
        assert ours.wait(timeout=15) in (-15, -9)
        time.sleep(0.3)
        assert bystander.poll() is None  # a pid that is not that program's start time is nobody's to end
        assert scene.run(live["id"])["state"] == "running"  # another engine carries it out
    finally:
        for proc in (ours, bystander, engine):
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def test_recover_all_survives_one_projects_trouble_and_stops_when_closing(scene: Scene, monkeypatch):
    first = stale_run(scene)
    other = "p-" + "ab" * 16
    stale = {"id": run_store.new_run_id(), "project_id": other, "root": str(scene.root), "state": "running",
             "created": run_store.now_iso(), "engine_pid": dead_pid()}
    run_store.create_run(other, stale)
    real = scene.manager._recover

    def flaky(project_id):
        if project_id == other:
            raise RuntimeError("boom")
        return real(project_id)

    monkeypatch.setattr(scene.manager, "_recover", flaky)
    assert scene.manager.recover_all() == [first["id"]]
    assert run_store.load_run(other, stale["id"])["state"] == "running"
    monkeypatch.setattr(scene.manager, "_recover", real)
    scene.manager._closing = True
    assert scene.manager.recover_all() == []
    assert run_store.load_run(other, stale["id"])["state"] == "running"


def test_the_plan_sees_an_empty_folder_where_a_deleted_file_was_as_nothing_and_does_not_remove_it(scene: Scene):
    scene.put("gone.txt", "g\n")
    run = scene.go([init_step(), {"op": "delete", "path": "gone.txt"}, finish_step()])
    (scene.root / "gone.txt").mkdir()  # what a replaced file leaves behind
    before = tree_state(scene.root)
    plan = scene.manager.revert_plan(scene.root, scene.pid, run["id"])
    assert plan_paths(plan["restore"]) == ["gone.txt"] and plan["cannot"] == []
    assert tree_state(scene.root) == before and (scene.root / "gone.txt").is_dir()  # the plan removed nothing
    # with something in it the folder is not nothing: the plan says so, as the undo would
    (scene.root / "gone.txt" / "mine.txt").write_text("the person's\n")
    plan = scene.manager.revert_plan(scene.root, scene.pid, run["id"])
    assert plan["restore"] == [] and plan["cannot"] == [{"path": "gone.txt", "reason": "changed_since"}]
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["skipped"] == plan["cannot"] and result["restored"] == []


def test_the_plan_and_the_undo_agree_when_a_parent_of_a_deleted_file_is_now_a_file(scene: Scene):
    scene.put("sub/inner.txt", "inner\n")
    run = scene.go([init_step(), {"op": "delete", "path": "sub/inner.txt"}, finish_step()])
    (scene.root / "sub").rmdir()
    (scene.root / "sub").write_text("now a file\n")  # a folder cannot be made there
    plan = scene.manager.revert_plan(scene.root, scene.pid, run["id"])
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert plan["restore"] == [] and plan["cannot"] == [{"path": "sub/inner.txt", "reason": "changed_since"}]
    assert result["skipped"] == plan["cannot"] and result["restored"] == []
    assert (scene.root / "sub").read_text() == "now a file\n"


# -- review findings of group 1 (V14) -----------------------------------------------------------------------


def test_a_deleted_file_with_no_copy_is_listed_as_having_none_not_as_one_the_person_kept(scene: Scene, monkeypatch):
    scene.put("big.txt", "far more than the copy limit\n")
    scene.put("small.txt", "s\n")
    monkeypatch.setattr(snapshot, "COPY_LIMIT", 5)
    run = scene.go([init_step(), {"op": "delete", "path": "big.txt"},
                    {"op": "write", "path": "small.txt", "text": "now longer than before\n"},
                    {"op": "write", "path": "n.txt", "text": "new\n"}, finish_step()])
    plan = scene.manager.revert_plan(scene.root, scene.pid, run["id"])
    assert plan_paths(plan["restore"]) == ["small.txt"] and plan_paths(plan["remove"]) == ["n.txt"]
    assert plan["cannot"] == [{"path": "big.txt", "reason": "no_copy"}]
    # the page posts only what is ticked, and a file it could not offer is not among them: it still has no copy
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None, files=["small.txt", "n.txt"])
    assert result["skipped"] == [{"path": "big.txt", "reason": "no_copy"}]
    assert result["restored"] == ["small.txt"] and result["removed"] == ["n.txt"]
    assert scene.manager.detail(scene.pid, run["id"])["revert"]["skipped"] == result["skipped"]


def test_a_modified_file_whose_copy_is_no_longer_what_was_recorded_is_no_copy_even_when_not_ticked(scene: Scene):
    scene.put("old.txt", "v1\n")
    scene.put("other.txt", "o1\n")
    run = scene.go([init_step(), {"op": "write", "path": "old.txt", "text": "v2\n"},
                    {"op": "write", "path": "other.txt", "text": "o2\n"}, finish_step()])
    (run_store.run_dir(scene.pid, run["id"]) / "before" / "old.txt").write_text("tampered\n")  # not the recorded bytes
    plan = scene.manager.revert_plan(scene.root, scene.pid, run["id"])
    assert plan["cannot"] == [{"path": "old.txt", "reason": "no_copy"}] and plan_paths(plan["restore"]) == ["other.txt"]
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None, files=["other.txt"])
    assert result["skipped"] == plan["cannot"] and result["restored"] == ["other.txt"]
    assert (scene.root / "old.txt").read_text() == "v2\n"
    assert not (run_store.run_dir(scene.pid, run["id"]) / "reverted" / "old.txt").exists()  # nothing was set aside


def test_undo_does_not_put_git_files_into_the_change_history(scene: Scene):
    git_repo(scene)
    hook = scene.root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    config = scene.root / ".git" / "config"
    original = config.read_text()
    scene.put("ordinary.txt", "before\n")
    run = scene.go([init_step(), {"op": "delete", "path": str(hook)},
                    {"op": "write", "path": str(config), "text": original + "[remote \"o\"]\n\turl = https://u:tok@h/x\n"},
                    {"op": "write", "path": "ordinary.txt", "text": "after\n"}, finish_step()])
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by="m-1")
    assert ".git/config" in result["restored"] and ".git/hooks/pre-commit" in result["restored"]
    assert config.read_text() == original and hook.is_file() and (scene.root / "ordinary.txt").read_text() == "before\n"
    assert scene.versions(".git/config") == [] and scene.versions(".git/hooks/pre-commit") == []
    assert f"undo {run['id']}" in [v.note for v in scene.versions("ordinary.txt")]  # an ordinary file still is recorded


def test_a_run_that_cannot_be_compared_because_its_folder_is_away_is_compared_when_it_is_back(scene: Scene):
    scene.put("keep.txt", "k")
    run = stale_run(scene)
    (scene.root / "made_by_agent.txt").write_text("m")
    away = scene.tmp / "project.away"
    scene.root.rename(away)  # the drive is not attached when the engine starts
    assert scene.manager.recover_all() == [run["id"]]
    stored = scene.run(run["id"])
    assert stored["state"] == "interrupted" and stored["check_pending"] is True and stored["revertible"] is False
    assert codes(scene.manager.revert, away, scene.pid, run["id"], by=None) in ("not_revertible", "read_only", "folder_gone")
    away.rename(scene.root)  # back
    assert scene.manager.runs(scene.pid)["runs"][0]["state"] == "interrupted"  # the listing compares it
    stored = scene.run(run["id"])
    assert stored["check_pending"] is False and stored["revertible"] is True
    assert stored["changes"]["created"] == ["made_by_agent.txt"]
    assert scene.manager.recover_all() == []
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["removed"] == ["made_by_agent.txt"] and not (scene.root / "made_by_agent.txt").exists()


def test_a_pending_comparison_survives_a_folder_that_stays_away_and_is_made_when_a_run_starts_after_its_return(
        scene: Scene):
    run = stale_run(scene)
    away = scene.tmp / "project.away"
    scene.root.rename(away)
    scene.manager.recover_all()
    scene.manager.recover_all()  # the folder is still away: still pending, nothing breaks
    assert scene.run(run["id"])["check_pending"] is True
    away.rename(scene.root)
    (scene.root / "made.txt").write_text("m")
    scene.scenario([init_step(), finish_step()])
    scene.wait(scene.start()["id"])  # starting recovers first, before the new run takes its own picture
    stored = scene.run(run["id"])
    assert stored["check_pending"] is False and stored["changes"]["created"] == ["made.txt"]


def test_a_pending_comparison_waits_while_a_run_of_the_project_is_going(scene: Scene):
    scene.scenario([init_step(), {"op": "sleep", "s": 2}, finish_step()])
    going = scene.start()
    scene.wait_state(going["id"], "running")
    owing = stale_run(scene, state="interrupted", check_pending=True)
    (scene.root / "made_by_the_going_run.txt").write_text("m")
    scene.manager._recover(scene.pid)  # what the running one changes is not the old run's doing
    assert scene.run(owing["id"])["check_pending"] is True
    scene.wait(going["id"])
    scene.manager._recover(scene.pid)
    assert scene.run(owing["id"])["check_pending"] is False


def test_a_run_that_changes_the_agent_programs_settings_reads_out_of_bounds_and_undo_puts_them_back(scene: Scene):
    scene.put("opencode.json", '{"model": "x"}\n')
    run = scene.go([init_step(),
                    {"op": "write", "path": ".claude/settings.json", "text": '{"hooks": {"SessionStart": []}}\n'},
                    {"op": "write", "path": "opencode.json", "text": '{"permission": {"bash": "allow"}}\n'},
                    {"op": "complete"}, finish_step()])
    assert run["state"] == "out_of_bounds" and run["outcome"] == "done"
    assert sorted((v["path"], v["reason"]) for v in run["violations"]) == [
        (".claude/settings.json", "agent_settings_changed"), ("opencode.json", "agent_settings_changed")]
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by="m-1")
    assert ".claude/settings.json" in result["removed"] and not (scene.root / ".claude" / "settings.json").exists()
    assert "opencode.json" in result["restored"] and (scene.root / "opencode.json").read_text() == '{"model": "x"}\n'


def test_the_program_is_not_given_the_home_or_credentials_and_the_run_names_what_was_left_out(scene: Scene, monkeypatch):
    monkeypatch.setenv("FOO_TOKEN", "value-that-must-not-travel")
    monkeypatch.setenv("GITHUB_PAT", "another-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "kept-for-the-model")
    run = scene.go([init_step(), finish_step()])
    (call,) = scene.calls()
    assert "RESEARCHOS_HOME" not in call["env"] and names.LEGACY_HOME_ENV_VAR not in call["env"]
    assert "FOO_TOKEN" not in call["env"] and call["env"]["ANTHROPIC_API_KEY"] == "kept-for-the-model"
    stored = scene.run(run["id"])
    assert "FOO_TOKEN" in stored["env_removed"] and "GITHUB_PAT" in stored["env_removed"]
    assert "ANTHROPIC_API_KEY" not in stored["env_removed"]
    assert "value-that-must-not-travel" not in json.dumps(stored)  # names only
    detail = scene.manager.detail(scene.pid, run["id"])
    assert detail["env_removed"] == stored["env_removed"]
    # the MCP server alone is told where the home is
    mcp = json.loads((run_store.run_dir(scene.pid, run["id"]) / "mcp.json").read_text())["mcpServers"]["researchos"]
    assert mcp["env"]["RESEARCHOS_HOME"] == str(paths.researchos_home())
    guard_file = json.loads((run_store.run_dir(scene.pid, run["id"]) / "guard.json").read_text())
    assert str(paths.researchos_home()) in guard_file["off_limits"] or os.path.realpath(paths.researchos_home()) in guard_file["off_limits"]


# -- 18.1 item 6: refusals and errors with a code, and the cap on stderr.log ------------------------------------


def test_the_refusals_in_a_runs_detail_carry_their_code(scene: Scene):
    run = scene.go([init_step(), {"op": "refusal", "target": "git push", "reason": "No git.", "code": "git_state"},
                    {"op": "refusal", "target": "data.csv", "reason": "an old-style reason, no code"}, finish_step()])
    refusals = scene.manager.detail(scene.pid, run["id"])["refusals"]
    assert [r["reason"] for r in refusals] == ["No git.", "an old-style reason, no code"]
    assert refusals[0]["code"] == "git_state" and "code" not in refusals[1]


def test_a_failed_runs_log_ends_with_the_code_of_its_error(scene: Scene):
    # the program's own last event is the end of the log: it gets the code of the run's error
    budget = scene.go([init_step(), {"op": "result", "is_error": True, "subtype": "error_max_budget_usd",
                                     "text": "", "errors": ["Reached maximum budget"]}, {"op": "exit", "code": 1}])
    steps = scene.manager.detail(scene.pid, budget["id"])["steps"]
    assert steps[-1]["kind"] == "end" and steps[-1]["code"] == "error_max_budget_usd"
    assert "Reached maximum budget" in steps[-1]["text"] and all("code" not in s for s in steps[:-1])
    # a run that ended well keeps the program's own last words, without a code
    ended = scene.go([init_step(), finish_step("All done.")])
    assert "code" not in scene.manager.detail(scene.pid, ended["id"])["steps"][-1]


def test_an_end_the_engine_adds_says_the_programs_words_and_the_code_never_stands_for_them(scene: Scene):
    # a program that dies with no result: the engine writes the end, from the error's detail, with its code
    died = scene.go([init_step(), {"op": "say", "text": "hmm"}, {"op": "exit", "code": 3}])
    end = scene.manager.detail(scene.pid, died["id"])["steps"][-1]
    assert end["kind"] == "end" and end["code"] == "aborted" and end["text"] == died["error"]["detail"]
    # no detail: the text is empty, never the code
    run_store.update_run(scene.pid, died["id"], error={"code": "error_something_new"})
    end = scene.manager.detail(scene.pid, died["id"])["steps"][-1]
    assert end == {"kind": "end", "text": "", "tool": None, "code": "error_something_new"}
    # a stopped run's end has the code `stopped` and no sentence of ours: the page words it in the person's language,
    # not in the language the run began in
    run_store.update_run(scene.pid, died["id"], state="stopped", error=None)
    assert scene.manager.detail(scene.pid, died["id"])["steps"][-1] == {"kind": "end", "text": "", "tool": None, "code": "stopped"}


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize("code", ["shutdown", "engine_gone"])
def test_the_end_of_an_interrupted_run_has_the_code_of_its_error_in_either_language(scene: Scene, code, lang):
    """18.1 item 6: a run that failed or was interrupted ends its log with the code of its error. An interrupted run has no
    result event, so the engine writes the end: with the code, and the error's detail (the engine's own words) as the
    text, whatever language the run began in."""
    died = scene.go([init_step(), {"op": "say", "text": "hmm"}, {"op": "exit", "code": 3}])  # no result event
    detail_text = "Research OS was closed while the run was going"
    run_store.update_run(scene.pid, died["id"], state="interrupted", lang=lang, error={"code": code, "detail": detail_text})
    end = scene.manager.detail(scene.pid, died["id"])["steps"][-1]
    assert end == {"kind": "end", "text": detail_text, "tool": None, "code": code}
    # an old record that has no error keeps the sentence in the run's language, with no code
    run_store.update_run(scene.pid, died["id"], error=None)
    end = scene.manager.detail(scene.pid, died["id"])["steps"][-1]
    assert end["kind"] == "end" and "code" not in end and end["text"].startswith("Interrupted:" if lang == "en" else "已中断")


def test_stderr_log_is_capped_as_the_event_log_is(scene: Scene, monkeypatch):
    monkeypatch.setattr(run_store, "STDERR_LIMIT", 600)
    steps = [init_step()] + [{"op": "stderr", "text": "warning " * 20} for _ in range(20)] + [finish_step()]
    run = scene.go(steps)
    rd = run_store.run_dir(scene.pid, run["id"])
    assert (rd / "stderr.log").stat().st_size == 600 and run["stderr_truncated"] is True
    assert (rd / "stderr.log").read_text().startswith("warning warning")  # the beginning is what stays
    assert run["state"] == "ended" and run["summary"] == "All done."  # the run itself is untouched
    detail = scene.manager.detail(scene.pid, run["id"])
    assert detail["stderr_truncated"] is True and detail["events_truncated"] is False
    assert len(detail["stderr_tail"].encode()) <= manager.STDERR_TAIL_BYTES


def test_a_capped_stderr_shows_how_the_program_ended_not_where_the_cap_fell(scene: Scene, monkeypatch):
    """18.1 item 6: the log keeps the beginning, but the page's `stderr_tail` is the program's last words: a program that
    prints a great deal of warnings and then dies with `fatal: out of memory` shows that, not the cap's 4 KB."""
    monkeypatch.setattr(run_store, "STDERR_LIMIT", 600)
    steps = ([init_step()] + [{"op": "stderr", "text": "warning " * 20} for _ in range(40)]  # 6.6 KB, over the 600 bytes
             + [{"op": "stderr", "text": "fatal: out of memory"}, finish_step()])
    run = scene.go(steps)
    rd = run_store.run_dir(scene.pid, run["id"])
    assert (rd / "stderr.log").stat().st_size == 600 and run["stderr_truncated"] is True  # the cap itself is as before
    assert (rd / "stderr.log").read_text().startswith("warning warning")
    tail = scene.manager.detail(scene.pid, run["id"])["stderr_tail"]
    assert tail.endswith("fatal: out of memory\n") and len(tail.encode()) == manager.STDERR_TAIL_BYTES
    assert tail.encode() == (rd / "stderr.tail").read_bytes()  # the last 4096 bytes the program wrote, not a byte more
    # if the sidecar is gone (an engine that died before it was written), the log's own end is the best there is
    (rd / "stderr.tail").unlink()
    assert scene.manager.detail(scene.pid, run["id"])["stderr_tail"] == (rd / "stderr.log").read_text()[-manager.STDERR_TAIL_BYTES:]


def test_stderr_under_the_cap_is_whole_and_unmarked(scene: Scene):
    run = scene.go([init_step(), {"op": "stderr", "text": "one"}, {"op": "stderr", "text": "two"}, finish_step()])
    rd = run_store.run_dir(scene.pid, run["id"])
    assert (rd / "stderr.log").read_text() == "one\ntwo\n" and not run.get("stderr_truncated")
    assert not (rd / "stderr.tail").exists()  # only a log that was cut has a second file
    assert scene.manager.detail(scene.pid, run["id"])["stderr_tail"] == "one\ntwo\n"
    assert scene.manager.detail(scene.pid, run["id"])["stderr_truncated"] is False
    assert run_store.STDERR_LIMIT == run_store.EVENTS_LIMIT  # the same cap


def test_a_program_that_writes_a_great_deal_to_stderr_is_not_held_up_by_it(scene: Scene, monkeypatch):
    """Standard error is a pipe now: it is read to its end whatever the cap, so a program that writes far more than
    a pipe holds (64 KB) neither blocks nor loses what fits."""
    monkeypatch.setattr(run_store, "STDERR_LIMIT", 100_000)
    steps = [init_step()] + [{"op": "stderr", "text": "e" * 2000} for _ in range(400)] + [finish_step()]  # 800 KB
    run = scene.go(steps)
    rd = run_store.run_dir(scene.pid, run["id"])
    assert run["state"] == "ended" and run["exit_code"] == 0
    assert (rd / "stderr.log").stat().st_size == 100_000 and run["stderr_truncated"] is True


def test_stderr_kept_up_to_a_stop_and_a_program_that_never_starts_leaves_an_empty_log(scene: Scene, monkeypatch):
    scene.scenario([init_step(), {"op": "stderr", "text": "before the stop"}, {"op": "sleep", "s": 60}])
    started = scene.start()
    rd = run_store.run_dir(scene.pid, started["id"])
    end = time.monotonic() + 20
    while time.monotonic() < end and "before the stop" not in ((rd / "stderr.log").read_text() if (rd / "stderr.log").exists() else ""):
        time.sleep(0.05)
    scene.manager.stop(scene.pid, started["id"])
    run = scene.wait(started["id"])
    assert run["state"] == "stopped" and "before the stop" in (rd / "stderr.log").read_text()
    # a program that cannot start: the run fails as before and no handle is left open
    broken = scene.tmp / "broken"
    broken.write_text("#!/nonexistent/interpreter\n")
    broken.chmod(0o755)
    monkeypatch.setattr(profiles, "find", lambda runner, **kw: profiles.Found(runner, str(broken), "1", None))
    failed = scene.wait(scene.start()["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "start_failed"
    assert (run_store.run_dir(scene.pid, failed["id"]) / "stderr.log").read_text() == ""  # made, as before, and empty

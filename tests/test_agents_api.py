"""Package F of DESIGN.md Section 17: the HTTP API of the agent runs, through
the real local server. The agents' programs are the fakes of
`test_agents_manager.py` (Python scripts answering `--version` that print the
streams the real programs print and change files per a scenario file), so
nothing here reaches the network or a real `claude` / `opencode`."""

from __future__ import annotations

import dataclasses
import json
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from researchos import people, project, workspace
from researchos.agents import labels, profiles
from researchos.agents import runs as run_store
from researchos.records.identity import read_identity
from researchos.webapp import server
from test_agents_manager import COMPLETE, FAKE, NOTE, finish_step, init_step
from test_webapp_server import _request_with_headers

CJK = re.compile(r"[一-鿿]")
FOREIGN = {"Origin": "https://example.com"}


class Api:
    """A running server (on its own thread) over one project, fake runners registered."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_project: bool = True) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "project"
        self.root.mkdir()
        project.init_project(self.root)
        self.pid = read_identity(self.root).identity.id
        self.bins: dict[str, Path] = {}
        monkeypatch.setattr(profiles, "mcp_available", lambda: True)
        for runner, program in (("claude-code", "claude"), ("opencode", "opencode")):
            folder = tmp_path / f"bin-{program}"
            folder.mkdir()
            script = folder / program
            script.write_text(FAKE.replace("@PYTHON@", sys.executable))
            script.chmod(0o755)
            self.bins[runner] = script
        profiles.set_commands({r: str(p) for r, p in self.bins.items()})
        self.httpd = server.build_server(self.root if with_project else None, 0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        if with_project:
            self.write_tasks([{"id": "t1", "text": "Analyse the sample", "owner": "agent:claude-code:sonnet"}])

    # -- requests
    def call(self, method: str, path: str, body: dict | None = None, *, lang: str | None = None) -> tuple[int, Any]:
        headers = {"Content-Type": "application/json"}
        if lang:
            headers["X-ResearchOS-Lang"] = lang
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(self, path: str, *, lang: str | None = None) -> tuple[int, Any]:
        return self.call("GET", path, lang=lang)

    def post(self, path: str, body: dict | None = None, *, lang: str | None = None, project: bool = True):
        payload = dict(body or {})
        if project:
            payload.setdefault("project", str(self.root))
        return self.call("POST", path, payload, lang=lang)

    # -- the project
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

    def scenario(self, steps: list[dict], runner: str = "claude-code", task_id: str = "t1") -> None:
        owner = next(t["owner"] for t in workspace.read_tasks(self.root)[0] if t["id"] == task_id)
        agent = labels.parse_owner(owner)
        body = {"flavor": "claude" if runner == "claude-code" else "opencode", "steps": steps,
                "project_id": self.pid, "task_id": task_id, "agent": [agent.runner, agent.model],
                "by": people.current_member_id()}
        (self.bins[runner].parent / "scenario.json").write_text(json.dumps(body))

    def calls(self, runner: str = "claude-code") -> list[dict]:
        path = self.bins[runner].parent / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    # -- runs
    def run_state(self, run_id: str) -> dict:
        run = run_store.load_run(self.pid, run_id)
        assert run is not None
        return run

    def poll(self, run_id: str, timeout: float = 40.0) -> dict:
        """GET /api/agents/runs until the run is terminal and nothing is active (what the page does)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            status, listing = self.get("/api/agents/runs")
            assert status == 200, listing
            mine = next((r for r in listing["runs"] if r["id"] == run_id), None)
            if mine and mine["state"] not in run_store.ACTIVE_STATES and listing["active"] is None:
                return mine
            time.sleep(0.05)
        raise AssertionError(f"run {run_id} did not finish: {self.get('/api/agents/runs')}")

    def wait_state(self, run_id: str, state: str, timeout: float = 30.0) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            run = run_store.load_run(self.pid, run_id)
            if run and run["state"] == state:
                return run
            time.sleep(0.03)
        raise AssertionError(f"run {run_id} never reached {state}: {run_store.load_run(self.pid, run_id)}")

    def wait_running(self, run_id: str, timeout: float = 30.0) -> dict:
        """Running, and the program has said its session id (a stop before that leaves nothing to continue)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            run = run_store.load_run(self.pid, run_id)
            if run and run["state"] == "running" and run.get("session_id"):
                return run
            time.sleep(0.03)
        raise AssertionError(f"run {run_id} never got going: {run_store.load_run(self.pid, run_id)}")

    def start(self, task: str = "t1", **kw) -> dict:
        status, summary = self.post("/api/agents/runs", {"task": task}, **kw)
        assert status == 200, summary
        return summary

    def generation(self) -> int:
        return self.get("/api/generation")[1]["generation"]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()  # stops any run still going
        self.thread.join(timeout=10)


@pytest.fixture
def api(tmp_path, monkeypatch):
    a = Api(tmp_path, monkeypatch)
    try:
        yield a
    finally:
        a.close()


@pytest.fixture
def api_me(api):
    """The same, with a person on this Mac (`by` and `by_name` of the runs)."""
    status, _ = api.post("/api/me", {"name": "Lin", "email": "lin@example.com"}, project=False)
    assert status == 200
    return api


# -- no project: this Mac's agents ---------------------------------------------------------------


def test_agents_work_without_a_project_served(tmp_path, monkeypatch):
    a = Api(tmp_path, monkeypatch, with_project=False)
    try:
        status, data = a.get("/api/agents")
        assert status == 200, data
        assert [r["runner"] for r in data["runners"]] == ["claude-code", "opencode"]
        assert all(r["found"] and r["version"] == "fake 1.0" and r["problem"] is None for r in data["runners"])
        assert data["runners"][0]["path"] == str(a.bins["claude-code"])
        assert data["runners"][0]["label"] == "Claude Code"
        assert data["agents"] == [] and data["mcp"] is True
        assert data["claude_models"] == ["sonnet", "opus", "haiku", "fable"]
        assert data["timeout_minutes"] == 120 and data["max_budget_usd"] is None
        # the models of Claude Code need no program and no project
        assert a.get("/api/agents/models?runner=claude-code") == (
            200, {"runner": "claude-code", "models": ["sonnet", "opus", "haiku", "fable"]})
        # settings are this Mac's, not a project's
        status, saved = a.post("/api/settings/agents", {"agents": [{"runner": "opencode", "model": "lm/qwen"}]},
                               project=False)
        assert status == 200 and saved["agents"][0]["owner"] == "agent:opencode:lm/qwen"
        # the runs are a project's
        for method, path in (("GET", "/api/agents/runs"), ("GET", "/api/agents/runs/r-20260101T000000Z-abcdef"),
                             ("POST", "/api/agents/runs")):
            status, error = a.call(method, path, {"project": "x", "task": "t1"} if method == "POST" else None)
            assert status == 409 and error["state"] == "no_project", (method, path)
    finally:
        a.close()


def test_agents_payload_shape_with_agents_and_a_missing_runner(api, tmp_path):
    api.post("/api/settings/agents", {"agents": [{"runner": "claude-code", "model": "sonnet"},
                                                  {"runner": "opencode", "model": "lm/qwen"}],
                                       "commands": {"opencode": str(tmp_path / "nowhere" / "opencode")}},
             project=False)
    status, data = api.get("/api/agents")
    assert status == 200
    by_runner = {r["runner"]: r for r in data["runners"]}
    assert by_runner["opencode"]["found"] is False and by_runner["opencode"]["problem"] == "missing"
    assert by_runner["opencode"]["path"] is None
    assert [(a["owner"], a["label"], a["available"], a["mcp"]) for a in data["agents"]] == [
        ("agent:claude-code:sonnet", "Claude Code · sonnet", True, True),
        ("agent:opencode:lm/qwen", "OpenCode · lm/qwen", False, True)]


def test_mcp_flag_follows_the_installation(api, monkeypatch):
    monkeypatch.setattr(profiles, "mcp_available", lambda: False)
    status, data = api.get("/api/agents")
    assert status == 200 and data["mcp"] is False


# -- settings ------------------------------------------------------------------------------------


def test_settings_round_trip(api, tmp_path):
    status, data = api.post("/api/settings/agents", {
        "agents": [{"runner": "claude-code", "model": "opus"}, {"runner": "opencode", "model": "ollama/llama3"}],
        "timeout_minutes": 30, "max_budget_usd": 2.5}, project=False)
    assert status == 200, data
    assert data["timeout_minutes"] == 30 and data["max_budget_usd"] == 2.5
    assert [a["owner"] for a in data["agents"]] == ["agent:claude-code:opus", "agent:opencode:ollama/llama3"]
    assert api.get("/api/agents")[1] == data  # what was answered is what is stored
    assert [a.owner for a in profiles.agents()] == [a["owner"] for a in data["agents"]]

    # keys not sent are kept; commands merge per runner
    other = tmp_path / "bin-x" / "claude"
    other.parent.mkdir()
    other.write_text("#!/bin/sh\necho x\n")
    other.chmod(0o755)
    status, data = api.post("/api/settings/agents", {"commands": {"claude-code": str(other)}}, project=False)
    assert status == 200 and data["timeout_minutes"] == 30 and data["max_budget_usd"] == 2.5
    assert len(data["agents"]) == 2
    by_runner = {r["runner"]: r for r in data["runners"]}
    assert by_runner["claude-code"]["path"] == str(other) and by_runner["opencode"]["path"] == str(api.bins["opencode"])

    # null / "" put a limit back to its default; [] empties the list; "" removes a program location
    status, data = api.post("/api/settings/agents", {"timeout_minutes": None, "max_budget_usd": "", "agents": [],
                                                      "commands": {"claude-code": ""}}, project=False)
    assert status == 200
    assert data["timeout_minutes"] == 120 and data["max_budget_usd"] is None and data["agents"] == []
    assert profiles.commands() == {"opencode": str(api.bins["opencode"])}
    assert api.post("/api/settings/agents", {}, project=False)[1] == data  # nothing sent, nothing changed


def test_settings_refusals_save_nothing_and_speak_the_requests_language(api):
    good = [{"runner": "claude-code", "model": "sonnet"}]
    assert api.post("/api/settings/agents", {"agents": good}, project=False)[0] == 200
    for bad in ({"agents": [{"runner": "claude-code", "model": "-rf"}]},
                {"agents": "sonnet"},
                {"commands": {"claude-code": "relative/claude"}},
                {"commands": {"vim": "/usr/bin/vim"}},
                {"timeout_minutes": 1},
                {"timeout_minutes": "soon"},
                {"max_budget_usd": -3}):
        status, error = api.post("/api/settings/agents", dict(bad, project="ignored"), project=False)
        assert status == 400 and error["state"] == "agent_invalid", bad
        assert error["message"] and CJK.search(error["message"]) and error["detail"] == error["error"]
        assert not CJK.search(error["detail"])
        status, english = api.post("/api/settings/agents", dict(bad), project=False, lang="en")
        assert status == 400 and english["message"] == english["detail"] and not CJK.search(english["message"])
    # a refusal after a good key saves neither: the earlier key is put back
    status, error = api.post("/api/settings/agents", {"agents": [], "timeout_minutes": 1}, project=False)
    assert status == 400 and error["state"] == "agent_invalid"
    status, data = api.get("/api/agents")
    assert [a["owner"] for a in data["agents"]] == ["agent:claude-code:sonnet"] and data["timeout_minutes"] == 120
    assert api.call("POST", "/api/settings/agents", None)[0] == 200  # an empty body is {}: nothing to change


def test_models(api, monkeypatch, tmp_path):
    seen: list[str] = []

    def fake_models(path: str) -> list[str]:
        seen.append(path)
        return ["lmstudio/qwen3-coder", "ollama/llama3"]

    monkeypatch.setattr(profiles, "opencode_models", fake_models)
    status, data = api.get("/api/agents/models?runner=opencode")
    assert status == 200 and data == {"runner": "opencode", "models": ["lmstudio/qwen3-coder", "ollama/llama3"]}
    assert seen == [str(api.bins["opencode"])]

    def failing(path: str) -> list[str]:
        raise RuntimeError("opencode models failed (exit 1)")

    monkeypatch.setattr(profiles, "opencode_models", failing)
    status, error = api.get("/api/agents/models?runner=opencode", lang="en")
    assert status == 502 and error["state"] == "agent_models_failed"
    assert error["message"] == "opencode models failed (exit 1)"

    for query in ("", "?runner=vim", "?runner="):
        status, error = api.get("/api/agents/models" + query)
        assert status == 400 and error["state"] == "agent_invalid"

    api.post("/api/settings/agents", {"commands": {"opencode": str(tmp_path / "nowhere" / "opencode")}}, project=False)
    status, error = api.get("/api/agents/models?runner=opencode")
    assert status == 409 and error["state"] == "agent_runner_missing" and CJK.search(error["message"])
    status, error = api.get("/api/agents/models?runner=opencode", lang="en")
    assert not CJK.search(error["message"]) and error["message"] == error["detail"]


# -- the full flow -------------------------------------------------------------------------------


def test_start_poll_detail_revert(api_me, monkeypatch):
    api = api_me
    api.put(NOTE, "# old\n")
    api.put("data.csv", "a,b\n1,2\n")
    api.put("old.txt", "v0\n")
    scans: list[Path] = []
    monkeypatch.setattr(api.httpd, "start_scan", lambda served: scans.append(served.root))
    before_generation = api.generation()
    api.scenario(COMPLETE + [{"op": "write", "path": "old.txt", "text": "v1\n"}])

    started = api.start(lang="en")
    assert started["state"] == "preparing" and started["can_stop"] is True and started["kind"] == "start"
    assert started["owner"] == "agent:claude-code:sonnet" and started["label"] == "Claude Code · sonnet"
    me = people.read_me().me
    assert started["by"] == me.id and started["by_name"] == "Lin"
    assert api.get("/api/agents/runs")[1]["active"] in (started["id"], None)

    done = api.poll(started["id"])
    assert done["state"] == "done" and done["can_reply"] is True and done["can_revert"] is True
    assert done["report"].startswith("research/results/") and done["changes"] >= 3 and done["violations"] == 0
    assert api.tasks()["t1"]["done"] is True and api.tasks()["t1"]["report"] == done["report"]
    # the run ended in the served project: pages are told and the project is rescanned (once)
    assert api.generation() > before_generation
    assert scans == [api.root]
    listing = api.get("/api/agents/runs")[1]
    assert listing["active"] is None and [r["id"] for r in listing["runs"]] == [started["id"]]

    # the detail
    status, detail = api.get(f"/api/agents/runs/{started['id']}")
    assert status == 200 and detail["id"] == started["id"] and detail["state"] == "done"
    assert "analysis.py" in detail["changes"]["created"] and detail["violations"] == []
    assert "Analyse the sample" in detail["instruction"] and detail["lang"] == "en"
    assert any(step["kind"] == "tool" and "python analysis.py" in step["text"] for step in detail["steps"])
    assert detail["error"] is None and detail["exit_code"] == 0 and isinstance(detail["stderr_tail"], str)
    assert api.get("/api/agents/runs/r-20260101T000000Z-abcdef")[0] == 404

    # 撤回本次运行
    before_generation = api.generation()
    status, reverted = api.post(f"/api/agents/runs/{started['id']}/revert", lang="en")
    assert status == 200, reverted
    assert "analysis.py" in reverted["removed"] and "old.txt" in reverted["restored"]
    assert reverted["skipped"] == [] and reverted["run"]["reverted"] is True
    assert reverted["run"]["can_revert"] is False and reverted["generation"] > before_generation
    assert not (api.root / "analysis.py").exists() and (api.root / "old.txt").read_text() == "v0\n"
    assert api.tasks()["t1"]["done"] is False and "report" not in api.tasks()["t1"]
    assert (api.root / "data.csv").read_text() == "a,b\n1,2\n"
    assert scans == [api.root, api.root]
    # once
    status, error = api.post(f"/api/agents/runs/{started['id']}/revert", lang="en")
    assert status == 409 and error["state"] == "agent_reverted" and not CJK.search(error["message"])
    status, error = api.post(f"/api/agents/runs/{started['id']}/reply", {"message": "again"})
    assert status == 409 and error["state"] == "agent_reverted" and CJK.search(error["message"])


def test_a_run_of_another_project_does_not_bump_the_served_one(api, monkeypatch):
    scans: list[Path] = []
    monkeypatch.setattr(api.httpd, "start_scan", lambda served: scans.append(served.root))
    bumped: list[Any] = []
    monkeypatch.setattr(api.httpd.watcher, "record_external_change", lambda *a, **k: bumped.append(a) or 99)
    other = api.tmp / "elsewhere"
    other.mkdir()
    api.httpd._agent_finished(other, {"id": "r-x"})
    assert scans == [] and bumped == []
    api.httpd._agent_finished(api.root, {"id": "r-y"})
    assert scans == [api.root] and len(bumped) == 1
    # a symlinked spelling of the served folder is the same project
    link = api.tmp / "link"
    link.symlink_to(api.root, target_is_directory=True)
    api.httpd._agent_finished(link, {"id": "r-z"})
    assert len(scans) == 2 and len(bumped) == 2
    # nothing for a blocked / no-project state, and nothing once the server is closing
    api.httpd.set_served(dataclasses.replace(api.httpd.get_served(), blocked={"situation": "copy"}))
    api.httpd._agent_finished(api.root, {"id": "r-b"})
    assert len(scans) == 2
    api.httpd.set_served(dataclasses.replace(api.httpd.get_served(), blocked=None))
    api.httpd.set_served(server.no_project_served())
    api.httpd._agent_finished(api.root, {"id": "r-n"})
    assert len(scans) == 2
    api.httpd.set_served(server.served_for(api.root))
    api.httpd._closing = True
    api.httpd._agent_finished(api.root, {"id": "r-c"})
    assert len(scans) == 2 and len(bumped) == 2
    api.httpd._closing = False


def test_a_scan_that_cannot_start_does_not_fail_the_finish(api, monkeypatch):
    def refuse(served):
        raise server.AddProjectError("scan_running", "scanning", "a scan is running")

    monkeypatch.setattr(api.httpd, "start_scan", refuse)
    api.scenario([init_step(), finish_step()])
    run = api.start()
    assert api.poll(run["id"])["state"] == "ended"


# -- reply, stop ----------------------------------------------------------------------------------


def test_answer_then_send_back(api_me):
    api = api_me
    api.scenario([init_step(), {"op": "ask", "question": "Which sample?"}, finish_step("Asked")])
    first = api.start(lang="en")
    asked = api.poll(first["id"])
    assert asked["state"] == "needs_input" and asked["question"] == "Which sample?" and asked["can_reply"] is True

    api.scenario(COMPLETE)
    status, second = api.post(f"/api/agents/runs/{first['id']}/reply", {"message": "Use sample B"}, lang="en")
    assert status == 200, second
    assert second["kind"] == "answer" and second["parent"] == first["id"] and second["state"] == "preparing"
    assert second["by_name"] == "Lin"
    assert api.poll(second["id"])["state"] == "done"
    first_call, second_call = api.calls()
    assert "--resume" in second_call["argv"] and "Use sample B" in second_call["stdin"]

    # only the latest run continues; an empty message is refused; the language follows the request
    status, error = api.post(f"/api/agents/runs/{first['id']}/reply", {"message": "x"}, lang="en")
    assert status == 409 and error["state"] == "agent_not_latest" and not CJK.search(error["message"])
    status, error = api.post(f"/api/agents/runs/{second['id']}/reply", {"message": "   "})
    assert status == 400 and error["state"] == "agent_invalid" and CJK.search(error["message"])
    status, error = api.post(f"/api/agents/runs/{second['id']}/reply", {})
    assert status == 400 and error["state"] == "agent_invalid"

    # 打回 on a done task: the done mark is cleared, the report stays, pages are told
    report = api.tasks()["t1"]["report"]
    api.scenario([init_step(), finish_step("Sent back")])
    before_generation = api.generation()
    status, third = api.post(f"/api/agents/runs/{second['id']}/reply", {"message": "Add extra.txt"})
    assert status == 200 and third["kind"] == "send_back" and third["generation"] > before_generation
    assert api.poll(third["id"])["state"] == "ended"
    assert api.tasks()["t1"]["done"] is False and api.tasks()["t1"]["report"] == report


def test_stop(api):
    waiting = api.tmp / "never-appears"
    api.scenario([init_step(), {"op": "wait_file", "path": str(waiting), "timeout": 60}, finish_step()])
    started = api.start()
    api.wait_running(started["id"])
    assert api.get("/api/agents/runs")[1]["active"] == started["id"]
    status, stopped = api.post(f"/api/agents/runs/{started['id']}/stop")
    assert status == 200, stopped
    assert stopped["state"] == "stopped" and stopped["can_stop"] is False and stopped["can_reply"] is True
    assert api.run_state(started["id"])["state"] == "stopped"
    assert api.get("/api/agents/runs")[1]["active"] is None
    # idempotent; an unknown run is a refusal
    assert api.post(f"/api/agents/runs/{started['id']}/stop")[1]["state"] == "stopped"
    status, error = api.post("/api/agents/runs/r-20260101T000000Z-abcdef/stop", lang="en")
    assert status == 404 and error["state"] == "agent_run_missing" and not CJK.search(error["message"])
    # a stopped run can be sent back
    api.scenario([init_step(), finish_step()])
    status, again = api.post(f"/api/agents/runs/{started['id']}/reply", {"message": "continue"})
    assert status == 200 and again["kind"] == "send_back"
    api.poll(again["id"])


def test_stop_is_allowed_on_a_read_only_project_start_is_not(api):
    waiting = api.tmp / "never-appears"
    api.scenario([init_step(), {"op": "wait_file", "path": str(waiting), "timeout": 60}, finish_step()])
    started = api.start()
    api.wait_state(started["id"], "running")
    api.httpd.set_served(dataclasses.replace(api.httpd.get_served(), read_only=True))
    assert api.get("/api/agents/runs")[0] == 200
    status, error = api.post("/api/agents/runs", {"task": "t1"})
    assert status == 409 and error["state"] == "read_only"
    status, error = api.post(f"/api/agents/runs/{started['id']}/revert")
    assert status == 409 and error["state"] == "read_only"
    status, error = api.post(f"/api/agents/runs/{started['id']}/reply", {"message": "x"})
    assert status == 409 and error["state"] == "read_only"
    status, stopped = api.post(f"/api/agents/runs/{started['id']}/stop")
    assert status == 200 and stopped["state"] == "stopped"


def test_a_blocked_project_refuses_every_run_endpoint(api):
    api.httpd.set_served(dataclasses.replace(api.httpd.get_served(), blocked={"situation": "copy", "answers": []}))
    for method, path in (("GET", "/api/agents/runs"), ("GET", "/api/agents/runs/r-20260101T000000Z-abcdef"),
                         ("POST", "/api/agents/runs"), ("POST", "/api/agents/runs/r-20260101T000000Z-abcdef/stop")):
        status, error = api.call(method, path, {"project": str(api.root), "task": "t1"} if method == "POST" else None)
        assert status == 409 and error["state"] == "project_blocked", (method, path)
    assert api.get("/api/agents")[0] == 200  # this Mac's agents do not depend on the project


# -- errors ----------------------------------------------------------------------------------------


def test_error_mapping_and_language(api, monkeypatch, tmp_path):
    api.write_tasks([
        {"id": "t1", "text": "Agent task", "owner": "agent:claude-code:sonnet"},
        {"id": "t2", "text": "Another agent task", "owner": "agent:claude-code:sonnet"},
        {"id": "t3", "text": "Person task", "owner": "Li"},
        {"id": "t4", "text": "OpenCode task", "owner": "agent:opencode:lm/qwen"},
        {"id": "t5", "text": "Done already", "owner": "agent:claude-code:sonnet", "done": True},
    ])

    def refused(task: str, lang: str | None, status: int, state: str) -> dict:
        got, error = api.post("/api/agents/runs", {"task": task}, lang=lang)
        assert (got, error["state"]) == (status, state), error
        assert error["detail"] == error["error"] and not CJK.search(error["detail"])
        return error

    for task, status, state in (("t3", 400, "agent_not_agent_task"), ("nope", 404, "agent_task_missing"),
                                ("t5", 409, "agent_task_done")):
        zh = refused(task, None, status, state)
        en = refused(task, "en", status, state)
        assert CJK.search(zh["message"]), state
        assert en["message"] == en["detail"] and not CJK.search(en["message"]), state
        assert zh["message"] == refused(task, "zh", status, state)["message"]
    assert refused("t3", "EN", 400, "agent_not_agent_task")["message"] == refused("t3", "en", 400, "agent_not_agent_task")["message"]

    # a request that names no task
    for body in ({}, {"task": ""}, {"task": 7}):
        got, error = api.post("/api/agents/runs", body, lang="en")
        assert got == 400 and error["state"] == "agent_invalid"

    # the MCP extra is missing
    monkeypatch.setattr(profiles, "mcp_available", lambda: False)
    zh = refused("t1", None, 409, "agent_mcp_missing")
    en = refused("t1", "en", 409, "agent_mcp_missing")
    assert CJK.search(zh["message"]) and not CJK.search(en["message"])
    monkeypatch.setattr(profiles, "mcp_available", lambda: True)

    # the program is not found
    api.post("/api/settings/agents", {"commands": {"opencode": str(tmp_path / "nowhere" / "opencode")}}, project=False)
    zh = refused("t4", None, 409, "agent_runner_missing")
    en = refused("t4", "en", 409, "agent_runner_missing")
    assert CJK.search(zh["message"]) and not CJK.search(en["message"])

    # run ids: unknown and malformed
    assert api.get("/api/agents/runs/r-20260101T000000Z-abcdef")[1]["state"] == "agent_run_missing"
    status, error = api.get("/api/agents/runs/..%2Fescape", lang="en")
    assert status == 400 and error["state"] == "agent_invalid"
    status, error = api.get("/api/agents/runs/not-a-run", lang="en")
    assert status == 400 and error["state"] == "agent_invalid"
    assert api.get("/api/agents/runs/r-20260101T000000Z-abcdef/extra")[0] == 404
    # unknown actions and a POST without an action are not endpoints
    for path in ("/api/agents/runs/r-20260101T000000Z-abcdef", "/api/agents/runs/r-20260101T000000Z-abcdef/frob",
                 "/api/agents/runs/a/b/c"):
        assert api.post(path)[0] == 404, path
    # nothing was started by any of this
    assert api.get("/api/agents/runs")[1] == {"runs": [], "active": None}


def test_busy_while_a_run_is_going(api):
    api.write_tasks([{"id": "t1", "text": "One", "owner": "agent:claude-code:sonnet"},
                     {"id": "t2", "text": "Two", "owner": "agent:claude-code:sonnet"}])
    waiting = api.tmp / "release"
    api.scenario([init_step(), {"op": "wait_file", "path": str(waiting), "timeout": 60}, finish_step()])
    first = api.start("t1")
    api.wait_state(first["id"], "running")
    status, error = api.post("/api/agents/runs", {"task": "t2"}, lang="en")
    assert status == 409 and error["state"] == "agent_busy" and not CJK.search(error["message"])
    status, error = api.post("/api/agents/runs", {"task": "t2"})
    assert status == 409 and error["state"] == "agent_busy" and CJK.search(error["message"])
    status, error = api.post(f"/api/agents/runs/{first['id']}/reply", {"message": "x"})
    assert status == 409 and error["state"] == "agent_run_active"
    status, error = api.post(f"/api/agents/runs/{first['id']}/revert")
    assert status == 409 and error["state"] == "agent_run_active"
    waiting.write_text("go")
    api.poll(first["id"])


# -- who may call ---------------------------------------------------------------------------------


def test_every_agent_post_is_refused_across_origins(api):
    api.scenario([init_step(), finish_step()])
    done = api.start()
    api.poll(done["id"])
    run_id = done["id"]
    before_settings = api.get("/api/agents")[1]
    posts = ["/api/settings/agents", "/api/agents/runs", f"/api/agents/runs/{run_id}/reply",
             f"/api/agents/runs/{run_id}/stop", f"/api/agents/runs/{run_id}/revert"]
    body = json.dumps({"project": str(api.root), "task": "t1", "message": "x", "agents": [], "timeout_minutes": 7}).encode()
    host = api.base.removeprefix("http://")
    for path in posts:
        status, error = _request_with_headers(
            api.base, "POST", path, {"Host": host, "Content-Length": str(len(body)), **FOREIGN}, body)
        assert status == 403, path
        status, _ = _request_with_headers(  # a rebinding Host
            api.base, "POST", path, {"Host": "evil.example:80", "Content-Length": str(len(body))}, body)
        assert status == 403, path
    for path in ("/api/agents", "/api/agents/models?runner=claude-code", "/api/agents/runs",
                 f"/api/agents/runs/{run_id}"):
        status, _ = _request_with_headers(api.base, "GET", path, {"Host": host, **FOREIGN})
        assert status == 403, path
    # nothing happened: no new run, no revert, no settings change
    listing = api.get("/api/agents/runs")[1]
    assert [r["id"] for r in listing["runs"]] == [run_id] and listing["runs"][0]["reverted"] is False
    assert api.get("/api/agents")[1] == before_settings


def test_a_post_for_another_project_is_refused(api):
    api.scenario([init_step(), finish_step()])
    done = api.start()
    api.poll(done["id"])
    run_id = done["id"]
    stale = str(api.tmp / "previous-project")
    for path, extra in (("/api/agents/runs", {"task": "t1"}), (f"/api/agents/runs/{run_id}/reply", {"message": "x"}),
                        (f"/api/agents/runs/{run_id}/stop", {}), (f"/api/agents/runs/{run_id}/revert", {})):
        for lang in (None, "en"):
            status, error = api.call("POST", path, dict(extra, project=stale), lang=lang)
            assert status == 409 and error["state"] == "agent_project_changed", path
            assert error["message"] == (error["detail"] if lang else error["message"])
            assert bool(CJK.search(error["message"])) is (lang is None)
        status, error = api.call("POST", path, dict(extra))  # no project at all
        assert status == 409 and error["state"] == "agent_project_changed", path
    listing = api.get("/api/agents/runs")[1]
    assert [r["id"] for r in listing["runs"]] == [run_id] and listing["runs"][0]["reverted"] is False
    assert api.tasks()["t1"]["done"] is False  # ended without a report: untouched


def test_person_is_optional(api):
    """No me.toml on this Mac: the run names nobody."""
    api.scenario([init_step(), finish_step()])
    run = api.start()
    assert run["by"] is None and run["by_name"] is None
    api.poll(run["id"])


# -- the server's lifecycle ------------------------------------------------------------------------


def test_server_close_stops_an_active_run(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    try:
        waiting = tmp_path / "never-appears"
        api.scenario([init_step(), {"op": "wait_file", "path": str(waiting), "timeout": 120}, finish_step()])
        scans: list[Path] = []
        monkeypatch.setattr(api.httpd, "start_scan", lambda served: scans.append(served.root))
        started = api.start()
        run = api.wait_state(started["id"], "running")
        pid = run["pid"]
        api.httpd.shutdown()
        api.httpd.server_close()
        api.thread.join(timeout=10)
        final = run_store.load_run(api.pid, started["id"])
        assert final["state"] == "interrupted" and final["ended"]
        assert api.httpd.agents.active_for(api.pid) is None
        from researchos.agents import manager

        end = time.monotonic() + 10
        while manager._pid_alive(pid) and time.monotonic() < end:
            time.sleep(0.05)
        assert not manager._pid_alive(pid)
        assert scans == []  # a closing server does not rescan
        api.httpd.server_close()  # idempotent
    finally:
        api.close()


def test_serve_installs_a_sigterm_handler_that_stops_the_agents(tmp_path, monkeypatch):
    """From the main thread a SIGTERM ends `serve` like Ctrl+C, so the `finally` closes the server (and its agents);
    the previous handler is back afterwards."""
    project.init_project(tmp_path)
    previous = signal.getsignal(signal.SIGTERM)
    closed: list[bool] = []
    stopped: list[bool] = []
    seen: dict[str, Any] = {}
    monkeypatch.setattr(server.agent_manager.AgentManager, "shutdown", lambda self: stopped.append(True))
    real_close = server.RceHTTPServer.server_close

    def close(self):
        closed.append(True)
        real_close(self)

    def serve_forever(self):
        seen["handler"] = signal.getsignal(signal.SIGTERM)
        import os

        os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGTERM)  # a second one while shutting down must not cut it short
        time.sleep(10)
        seen["not_interrupted"] = True

    monkeypatch.setattr(server.RceHTTPServer, "server_close", close)
    monkeypatch.setattr(server.RceHTTPServer, "serve_forever", serve_forever)
    server.serve(tmp_path, port=0, open_browser=False)
    assert "not_interrupted" not in seen
    assert seen["handler"] not in (previous, signal.SIG_DFL, signal.SIG_IGN)
    assert closed == [True] and stopped == [True]
    assert signal.getsignal(signal.SIGTERM) == previous


def test_serve_from_a_thread_installs_no_signal_handler(tmp_path, monkeypatch):
    project.init_project(tmp_path)
    previous = signal.getsignal(signal.SIGTERM)
    seen: dict[str, Any] = {}

    def serve_forever(self):
        seen["handler"] = signal.getsignal(signal.SIGTERM)

    monkeypatch.setattr(server.RceHTTPServer, "serve_forever", serve_forever)
    errors: list[BaseException] = []

    def target():
        try:
            server.serve(tmp_path, port=0, open_browser=False)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout=30)
    assert not errors and seen["handler"] == previous
    assert signal.getsignal(signal.SIGTERM) == previous


def test_build_server_alone_starts_no_threads_and_names_its_port(tmp_path):
    before = {t.name for t in threading.enumerate()}
    httpd = server.build_server(None, 0)
    try:
        assert httpd.agents.engine_port == httpd.server_address[1]
        assert {t.name for t in threading.enumerate()} == before
    finally:
        httpd.server_close()
        httpd.server_close()

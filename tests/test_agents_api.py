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
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from researchos import paths, people, project, workspace
from researchos.agents import labels, profiles
from researchos.agents import runs as run_store
from researchos.records.identity import read_identity
from researchos.webapp import server
from test_agents_manager import COMPLETE, FAKE, NOTE, finish_step, init_step, tree_state
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
        for runner, program in profiles.PROGRAMS.items():
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
        body = {"flavor": "claude" if runner == "claude-code" else runner, "steps": steps,
                "project_id": self.pid, "task_id": task_id, "agent": [agent.runner, agent.model],
                "by": people.current_member_id(), "home": str(paths.researchos_home())}  # the fake plays the MCP server
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
        assert [r["runner"] for r in data["runners"]] == ["claude-code", "opencode", "codex", "deepseek-harness"]
        assert all(r["found"] and r["version"] == "fake 1.0" and r["problem"] is None for r in data["runners"])
        assert data["runners"][0]["path"] == str(a.bins["claude-code"])
        assert data["runners"][0]["label"] == "Claude Code"
        assert data["agents"] == [] and data["mcp"] is True
        assert data["claude_models"] == list(profiles.CLAUDE_MODEL_SUGGESTIONS)
        assert "claude-sonnet-5-5" in data["claude_models"] and "claude-opus-4-6" in data["claude_models"]
        assert data["timeout_minutes"] == 120 and data["max_budget_usd"] is None
        # the models of Claude Code need no program and no project
        assert a.get("/api/agents/models?runner=claude-code") == (
            200, {"runner": "claude-code", "models": list(profiles.CLAUDE_MODEL_SUGGESTIONS)})
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


def test_claude_model_versions_are_saved_without_aliasing(api):
    versions = ["claude-sonnet-5-5", "claude-sonnet-4-5-20250929"]
    chosen = [{"runner": "claude-code", "model": model} for model in versions]
    assert api.post("/api/settings/agents", {"agents": chosen}, project=False)[0] == 200
    status, data = api.get("/api/agents")
    assert status == 200 and [a["model"] for a in data["agents"]] == versions


def test_harness_models_keep_account_and_api_routes_distinct(api):
    status, data = api.get("/api/agents/models?runner=deepseek-harness")
    assert status == 200 and data["models"] == list(profiles.HARNESS_MODEL_SUGGESTIONS)
    chosen = [{"runner": "deepseek-harness", "model": m} for m in data["models"]]
    status, saved = api.post("/api/settings/agents", {"agents": chosen}, project=False)
    assert status == 200 and saved["harness_models"] == data["models"]
    assert [a["model"] for a in saved["agents"]] == data["models"]
    assert api.post("/api/settings/agents", {"agents": [{"runner": "deepseek-harness", "model": "deepseek-flash"}]},
                    project=False)[0] == 400


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
    assert profiles.commands() == {r: str(api.bins[r]) for r in ("opencode", "codex", "deepseek-harness")}
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


def test_codex_models_and_exact_version_settings(api, monkeypatch):
    catalog = {"gpt-6.1-sol": ["low", "high", "max"], "gpt-6-luna": ["medium", "high"]}
    monkeypatch.setattr(profiles, "codex_model_catalog", lambda path: catalog)
    status, data = api.get("/api/agents/models?runner=codex")
    assert status == 200 and data == {"runner": "codex", "models": list(catalog), "reasoning_efforts": catalog}
    chosen = [{"runner": "codex", "model": "gpt-6.1-sol"}, {"runner": "codex", "model": "gpt-5.5"}]
    assert api.post("/api/settings/agents", {"agents": chosen}, project=False)[0] == 200
    _, data = api.get("/api/agents")
    assert [a["owner"] for a in data["agents"]] == ["agent:codex:gpt-6.1-sol", "agent:codex:gpt-5.5"]


def test_reasoning_effort_round_trip_and_invalid_settings_are_atomic(api, monkeypatch):
    monkeypatch.setattr(profiles, "codex_model_catalog", lambda path: {"gpt-6.1-sol": ["low", "max"]})
    entries = [{"runner": "codex", "model": "gpt-6.1-sol", "reasoning_effort": "max"},
               {"runner": "claude-code", "model": "claude-sonnet-5-5", "reasoning_effort": "xhigh"}]
    status, data = api.post("/api/settings/agents", {"agents": entries}, project=False)
    assert status == 200
    assert [a["reasoning_effort"] for a in data["agents"]] == ["max", "xhigh"]
    assert data["reasoning_efforts"]["claude-code"]["claude-opus-4-6"] == ["low", "medium", "high", "max"]
    bad = [dict(entries[0], reasoning_effort="high")]
    assert api.post("/api/settings/agents", {"agents": bad}, project=False)[0] == 400
    assert [a["reasoning_effort"] for a in api.get("/api/agents")[1]["agents"]] == ["max", "xhigh"]


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


def test_a_run_that_went_out_of_bounds_is_listed_and_detailed_as_such(api_me, monkeypatch):
    """18.1 item 4 through the real server: the state the page maps to 越过了限制, what it would have read, and the
    reasons, in the list and in the detail."""
    api = api_me
    api.put("data.csv", "a,b\n1,2\n")
    backup = api.put(".researchos/backups/x.toml.20260101T000000000000Z.toml", "old\n")
    api.put(".researchos/backups/x.toml.20260102T000000000000Z.toml", "newer\n")
    monkeypatch.setattr(api.httpd, "start_scan", lambda served: None)
    api.scenario(COMPLETE + [{"op": "write", "path": "data.csv", "text": "a,b\n9,9\n"},
                             {"op": "delete", "path": str(backup)}])
    started = api.start(lang="en")
    done = api.poll(started["id"])
    assert done["state"] == "out_of_bounds" and done["outcome"] == "done" and done["violations"] == 2
    assert done["can_revert"] is True and done["can_reply"] is True and done["can_stop"] is False
    assert api.tasks()["t1"]["done"] is True  # the agent's mark is left as it is
    status, detail = api.get(f"/api/agents/runs/{started['id']}")
    assert status == 200 and detail["state"] == "out_of_bounds" and detail["outcome"] == "done"
    assert sorted((v["path"], v["reason"]) for v in detail["violations"]) == [
        (".researchos/backups/x.toml.20260101T000000000000Z.toml", "backup_deleted"), ("data.csv", "data_changed")]
    assert ".researchos/backups/x.toml.20260101T000000000000Z.toml" in detail["changes"]["deleted"]
    status, reverted = api.post(f"/api/agents/runs/{started['id']}/revert", lang="en")
    assert status == 200 and "data.csv" in reverted["restored"]
    assert reverted["skipped"] == [{"path": ".researchos/backups/x.toml.20260101T000000000000Z.toml",
                                    "reason": "no_copy"}]
    assert (api.root / "data.csv").read_text() == "a,b\n1,2\n"


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
    for path, body in (("/api/agents/runs", {"task": "t1"}), (f"/api/agents/runs/{started['id']}/revert", {}),
                       (f"/api/agents/runs/{started['id']}/reply", {"message": "x"})):
        status, error = api.post(path, body, lang="en")  # the contract: agent_<code>, in the request's language
        assert status == 409 and error["state"] == "agent_read_only", (path, error)
        assert not CJK.search(error["message"]) and error["detail"] == error["error"]
        status, error = api.post(path, body, lang="zh")
        assert status == 409 and error["state"] == "agent_read_only" and CJK.search(error["message"]), (path, error)
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


def test_a_busy_port_raises_the_bind_error_not_an_attribute_error(tmp_path):
    """`TCPServer.__init__` closes the server when the bind fails; that close must not need what is created
    after the bind (a busy port stays an OSError for whoever reports 'port in use')."""
    import socket

    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    try:
        with pytest.raises(OSError) as raised:
            server.build_server(None, holder.getsockname()[1])
        assert not isinstance(raised.value, AttributeError)
    finally:
        holder.close()


def test_server_close_before_the_agents_exist_is_harmless(tmp_path):
    httpd = server.build_server(None, 0)
    httpd.agents = None
    httpd.server_close()
    assert httpd._closing is True


def test_a_sigterm_during_the_shutdown_does_not_cut_it_short(tmp_path, monkeypatch):
    """The loop ended by itself (POST /api/shutdown) and the wrapper's SIGTERM arrives while `server_close` is stopping
    the agents: it must be ignored, so the stopping completes (no KeyboardInterrupt out of `serve`)."""
    import os

    project.init_project(tmp_path)
    previous = signal.getsignal(signal.SIGTERM)
    seen: dict[str, Any] = {}

    def shutdown(self):
        seen["begun"] = True
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.3)  # the kill threads' work: a KeyboardInterrupt here would end it
        seen["finished"] = True

    monkeypatch.setattr(server.agent_manager.AgentManager, "shutdown", shutdown)
    monkeypatch.setattr(server.RceHTTPServer, "serve_forever", lambda self: None)
    server.serve(tmp_path, port=0, open_browser=False)  # raises KeyboardInterrupt before the fix
    assert seen == {"begun": True, "finished": True}
    assert signal.getsignal(signal.SIGTERM) == previous


# -- no project lock around the run manager; the write guard's refusals up front ------------------


def _lock_is_free(api: Api) -> bool:
    """Can another thread take the project's write lock right now?"""
    from researchos.records import situation

    got: list[bool] = []

    def attempt() -> None:
        try:
            with situation.write_guard(api.root, api.pid, human=True, timeout=0.5):
                got.append(True)
        except Exception:  # noqa: BLE001
            got.append(False)

    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join(timeout=10)
    return got == [True]


def test_start_reply_revert_do_not_hold_the_project_lock(api, monkeypatch):
    seen: dict[str, bool] = {}

    def stub(name, result):
        def call(*args, **kwargs):
            seen[name] = _lock_is_free(api)
            return result

        return call

    manager = api.httpd.agents
    monkeypatch.setattr(manager, "start", stub("start", {"id": "r-x", "state": "preparing"}))
    monkeypatch.setattr(manager, "reply", stub("reply", {"id": "r-y", "kind": "answer"}))
    monkeypatch.setattr(manager, "revert", stub("revert", {"run": {}, "restored": [], "removed": [], "skipped": []}))
    assert api.post("/api/agents/runs", {"task": "t1"})[0] == 200
    assert api.post("/api/agents/runs/r-20260101T000000Z-abcdef/reply", {"message": "x"})[0] == 200
    assert api.post("/api/agents/runs/r-20260101T000000Z-abcdef/revert")[0] == 200
    assert seen == {"start": True, "reply": True, "revert": True}


def test_a_slow_start_does_not_stall_other_saves(api, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def slow(*args, **kwargs):  # what `<program> --version` does on a cold start
        entered.set()
        release.wait(30)
        return {"id": "r-x", "state": "preparing"}

    monkeypatch.setattr(api.httpd.agents, "start", slow)
    result: list[Any] = []
    thread = threading.Thread(target=lambda: result.append(api.post("/api/agents/runs", {"task": "t1"})))
    thread.start()
    try:
        assert entered.wait(10)
        status, saved = api.post("/api/workspace/tasks", {"tasks": [
            {"id": "t1", "text": "Analyse the sample", "owner": "agent:claude-code:sonnet"}],
            "revision": api.get("/api/workspace")[1]["tasks_revision"]})
        assert status == 200, saved
    finally:
        release.set()
        thread.join(timeout=30)
    assert result and result[0][0] == 200


def test_the_managers_lock_refusals_keep_the_agent_error_contract(api, monkeypatch):
    from researchos.records import lock, situation

    manager = api.httpd.agents
    for raised, state, status in (
        (lock.ProjectLockTimeout("held by another process"), "agent_busy", 409),
        (situation.WriteRefused("not writable"), "agent_read_only", 403),
        (situation.NeedsMigrationError("not migrated"), "agent_needs_migration", 409),
    ):
        def refuse(*args, _raised=raised, **kwargs):
            raise _raised

        monkeypatch.setattr(manager, "start", refuse)
        monkeypatch.setattr(manager, "reply", refuse)
        monkeypatch.setattr(manager, "revert", refuse)
        for path, body in (("/api/agents/runs", {"task": "t1"}),
                           ("/api/agents/runs/r-20260101T000000Z-abcdef/reply", {"message": "x"}),
                           ("/api/agents/runs/r-20260101T000000Z-abcdef/revert", {})):
            got, error = api.post(path, body, lang="zh")
            assert (got, error["state"]) == (status, state), (path, error)
            assert CJK.search(error["message"]) and error["detail"]


def test_a_project_frozen_until_migrated_refuses_start_reply_revert_but_not_stop(api):
    api.httpd.set_served(dataclasses.replace(api.httpd.get_served(), needs_migration=True))
    for path, body in (("/api/agents/runs", {"task": "t1"}),
                       ("/api/agents/runs/r-20260101T000000Z-abcdef/reply", {"message": "x"}),
                       ("/api/agents/runs/r-20260101T000000Z-abcdef/revert", {})):
        status, error = api.post(path, body, lang="en")
        assert status == 409 and error["state"] == "agent_needs_migration", (path, error)
        assert not CJK.search(error["message"])
        status, error = api.post(path, body, lang="zh")
        assert error["state"] == "agent_needs_migration" and CJK.search(error["message"])
    status, error = api.post("/api/agents/runs/r-20260101T000000Z-abcdef/stop")
    assert status == 404 and error["state"] == "agent_run_missing"  # not refused for the migration


# -- a run that ends while another project is served ------------------------------------------------


def test_a_run_that_ended_in_another_project_is_scanned_when_the_page_switches_back(api, monkeypatch):
    from researchos.webapp import registry

    other = api.tmp / "other-project"
    other.mkdir()
    project.init_project(other)
    registry.register(api.root)
    registry.register(other)
    paths = {Path(e["path"]).resolve(): e["path"] for e in registry.load()}
    mine, theirs = paths[api.root.resolve()], paths[other.resolve()]
    scans: list[Path] = []
    monkeypatch.setattr(api.httpd, "start_scan", lambda served: scans.append(served.root))
    finished = threading.Event()
    manager = api.httpd.agents
    real = manager.on_finish

    def on_finish(root, run):
        real(root, run)
        finished.set()

    monkeypatch.setattr(manager, "on_finish", on_finish)
    gate = api.tmp / "gate"
    api.scenario([init_step(), {"op": "wait_file", "path": str(gate), "timeout": 60}, finish_step()])
    started = api.start()
    api.wait_state(started["id"], "running")
    status, _ = api.call("POST", "/api/projects/switch", {"path": theirs})
    assert status == 200 and api.httpd.get_served().root.resolve() == other.resolve()
    gate.write_text("go")
    assert finished.wait(30)
    api.wait_state(started["id"], "ended")
    assert scans == []  # not the served project: nothing now
    generation = api.generation()
    status, _ = api.call("POST", "/api/projects/switch", {"path": mine})
    assert status == 200
    assert scans == [api.httpd.get_served().root] and scans[0].resolve() == api.root.resolve()
    assert api.generation() > generation
    # once: the next switch away and back scans nothing more
    api.call("POST", "/api/projects/switch", {"path": theirs})
    api.call("POST", "/api/projects/switch", {"path": mine})
    assert len(scans) == 1


# -- a served project without identity ------------------------------------------------------------


def test_a_project_without_identity_lists_no_runs(api):
    api.httpd.set_served(dataclasses.replace(api.httpd.get_served(), project_id=None, needs_migration=True))
    assert api.get("/api/agents/runs", lang="zh") == (200, {"runs": [], "active": None})
    status, error = api.get("/api/agents/runs/r-20260101T000000Z-abcdef", lang="zh")
    assert status == 404 and error["state"] == "agent_run_missing" and CJK.search(error["message"])
    for path, body in (("/api/agents/runs", {"task": "t1"}),
                       ("/api/agents/runs/r-20260101T000000Z-abcdef/reply", {"message": "x"}),
                       ("/api/agents/runs/r-20260101T000000Z-abcdef/stop", {}),
                       ("/api/agents/runs/r-20260101T000000Z-abcdef/revert", {})):
        status, error = api.post(path, body, lang="en")
        assert status == 409 and error["state"] == "agent_needs_migration", (path, error)
        assert not CJK.search(error["message"])
        assert api.post(path, body, lang="zh")[1]["message"] != error["message"]


# -- an unreadable me.toml ---------------------------------------------------------------------------


def test_an_unreadable_me_toml_refuses_start_and_reply_only(api_me):
    api = api_me
    api.scenario(COMPLETE)
    done = api.poll(api.start()["id"])
    assert done["state"] == "done"
    people.me_path().write_text("this is [not valid toml\n")
    for path, body in (("/api/agents/runs", {"task": "t1"}),
                       (f"/api/agents/runs/{done['id']}/reply", {"message": "again"})):
        status, error = api.post(path, body, lang="en")
        assert status == 409 and error["state"] == "agent_person_unreadable", (path, error)
        assert "me.toml" in error["detail"] and not CJK.search(error["message"])
        status, error = api.post(path, body, lang="zh")
        assert status == 409 and error["state"] == "agent_person_unreadable" and CJK.search(error["message"])
    assert len(api.get("/api/agents/runs")[1]["runs"]) == 1  # nothing started
    status, stopped = api.post(f"/api/agents/runs/{done['id']}/stop")
    assert status == 200
    status, reverted = api.post(f"/api/agents/runs/{done['id']}/revert")
    assert status == 200 and reverted["run"]["reverted"] is True
    assert run_store.load_run(api.pid, done["id"])["reverted_by"] is None
    people.me_path().unlink()  # absent (not broken): nobody, as before
    api.scenario(COMPLETE)
    run = api.start()
    assert run["by"] is None
    api.poll(run["id"])


# -- undo says what it will do, and asks (DESIGN 18.1 item 5) -----------------------------------------------


def _finished_run(api: Api, monkeypatch) -> dict:
    """A run that changed old.txt, deleted gone.txt, made analysis.py and the result record, and is done."""
    monkeypatch.setattr(api.httpd, "start_scan", lambda served: None)
    api.put("old.txt", "v0\n")
    api.put("gone.txt", "g\n")
    api.scenario(COMPLETE + [{"op": "write", "path": "old.txt", "text": "v1\n"},
                             {"op": "delete", "path": "gone.txt"}])
    done = api.poll(api.start()["id"])
    assert done["state"] == "done" and done["can_revert"] is True
    return done


def test_revert_plan_lists_what_undo_would_do_and_changes_nothing(api_me, monkeypatch):
    api = api_me
    done = _finished_run(api, monkeypatch)
    (api.root / "stray.txt").write_text("a file nobody asked about\n")
    before = tree_state(api.root)
    status, plan = api.get(f"/api/agents/runs/{done['id']}/revert-plan", lang="en")
    assert status == 200, plan
    assert [i["path"] for i in plan["restore"]] == ["gone.txt", "old.txt", "research/tasks.json"]
    assert [i["path"] for i in plan["remove"]] == sorted(["analysis.py", done["report"]])
    assert plan["cannot"] == [] and plan["run"]["id"] == done["id"] and plan["run"]["can_revert"] is True
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d", i["time"])
               for i in plan["restore"] + plan["remove"])
    assert next(i for i in plan["restore"] if i["path"] == "research/tasks.json")["task"] == "Analyse the sample"
    assert "stray.txt" not in [i["path"] for i in plan["restore"] + plan["remove"]]
    assert tree_state(api.root) == before
    assert api.get(f"/api/agents/runs/{done['id']}")[1]["revert"] is None
    assert api.tasks()["t1"]["done"] is True
    # the same words in either language: the plan carries codes and paths, the page makes the sentences
    assert api.get(f"/api/agents/runs/{done['id']}/revert-plan", lang="zh")[1] == plan
    # a file changed since is listed, with why
    (api.root / "old.txt").write_text("v2, by the person\n")
    status, plan = api.get(f"/api/agents/runs/{done['id']}/revert-plan")
    assert plan["cannot"] == [{"path": "old.txt", "reason": "changed_since"}]
    assert "old.txt" not in [i["path"] for i in plan["restore"]]


def test_revert_plan_refuses_as_undo_does_in_the_requests_language(api_me, monkeypatch):
    api = api_me
    done = _finished_run(api, monkeypatch)
    status, error = api.get("/api/agents/runs/r-20260101T000000Z-abcdef/revert-plan", lang="en")
    assert status == 404 and error["state"] == "agent_run_missing" and not CJK.search(error["message"])
    assert api.get("/api/agents/runs/not-a-run/revert-plan")[0] == 400
    assert api.post(f"/api/agents/runs/{done['id']}/revert")[0] == 200
    status, error = api.get(f"/api/agents/runs/{done['id']}/revert-plan", lang="en")
    assert status == 409 and error["state"] == "agent_reverted" and not CJK.search(error["message"])
    status, error = api.get(f"/api/agents/runs/{done['id']}/revert-plan", lang="zh")
    assert status == 409 and error["state"] == "agent_reverted" and CJK.search(error["message"])
    # a nested path is not the plan, and the plan is only read
    assert api.get(f"/api/agents/runs/{done['id']}/revert-plan/extra")[0] == 404
    assert api.post(f"/api/agents/runs/{done['id']}/revert-plan")[0] == 404


def test_revert_plan_needs_a_project_with_an_identity(api):
    api.httpd.set_served(dataclasses.replace(api.httpd.get_served(), project_id=None, needs_migration=True))
    status, error = api.get("/api/agents/runs/r-20260101T000000Z-abcdef/revert-plan", lang="zh")
    assert status == 404 and error["state"] == "agent_run_missing" and CJK.search(error["message"])


def test_revert_with_files_undoes_only_those_and_says_what_was_kept(api_me, monkeypatch):
    api = api_me
    done = _finished_run(api, monkeypatch)
    status, plan = api.get(f"/api/agents/runs/{done['id']}/revert-plan")
    paths = [i["path"] for i in plan["restore"] + plan["remove"]]
    chosen = [p for p in paths if p not in ("old.txt", "analysis.py")]
    status, reverted = api.post(f"/api/agents/runs/{done['id']}/revert", {"files": chosen}, lang="en")
    assert status == 200, reverted
    assert reverted["restored"] == ["gone.txt", "research/tasks.json"] and reverted["removed"] == [done["report"]]
    assert sorted((s["path"], s["reason"]) for s in reverted["skipped"]) == [("analysis.py", "kept"),
                                                                            ("old.txt", "kept")]
    assert reverted["run"]["reverted"] is True and reverted["run"]["revert_skipped"] == 2
    assert (api.root / "old.txt").read_text() == "v1\n" and (api.root / "analysis.py").is_file()
    assert (api.root / "gone.txt").read_text() == "g\n" and api.tasks()["t1"]["done"] is False
    # the run's log lists them
    status, detail = api.get(f"/api/agents/runs/{done['id']}")
    assert detail["revert"]["skipped"] == reverted["skipped"]
    # absent = everything, as before (nothing is left to do here, so it is refused: the run is undone)
    status, error = api.post(f"/api/agents/runs/{done['id']}/revert", {}, lang="en")
    assert status == 409 and error["state"] == "agent_reverted"


def test_revert_with_a_bad_files_value_is_refused_and_changes_nothing(api_me, monkeypatch):
    api = api_me
    done = _finished_run(api, monkeypatch)
    before = tree_state(api.root)
    for bad in ([], "old.txt", [1], {"old.txt": True}):
        status, error = api.post(f"/api/agents/runs/{done['id']}/revert", {"files": bad}, lang="en")
        assert status == 400 and error["state"] == "agent_invalid" and not CJK.search(error["message"]), bad
    status, error = api.post(f"/api/agents/runs/{done['id']}/revert", {"files": []}, lang="zh")
    assert status == 400 and CJK.search(error["message"])
    assert tree_state(api.root) == before and api.run_state(done["id"])["reverted"] is False
    # null is "absent": everything
    status, reverted = api.post(f"/api/agents/runs/{done['id']}/revert", {"files": None})
    assert status == 200 and reverted["skipped"] == [] and reverted["run"]["reverted"] is True


def test_the_plan_reads_without_the_session_and_the_choice_does_not_write_without_it(api_me, monkeypatch):
    api = api_me
    done = _finished_run(api, monkeypatch)
    api.httpd.session = "s" * 64  # from here on a write must carry it (18.1)
    before = tree_state(api.root)
    assert api.get(f"/api/agents/runs/{done['id']}/revert-plan")[0] == 200
    assert api.get("/api/agents/large-files")[0] == 200
    status, error = api.post(f"/api/agents/runs/{done['id']}/revert", {"files": ["gone.txt"]})
    assert status == 403 and error["state"] == "session_required"
    assert tree_state(api.root) == before and api.run_state(done["id"])["reverted"] is False


def test_large_files_names_the_ten_biggest_and_counts_the_rest(api, monkeypatch):
    from researchos.agents import snapshot

    monkeypatch.setattr(snapshot, "COPY_LIMIT", 5000)  # the project's own files are smaller
    for i in range(12):
        api.put(f"data/big{i:02d}.bin", "x" * (5001 + i))
    api.put("small.txt", "x" * 5000)
    status, found = api.get("/api/agents/large-files")
    assert status == 200 and found["count"] == 12 and found["partial"] is False
    assert [f["path"] for f in found["largest"]] == [f"data/big{i:02d}.bin" for i in range(11, 1, -1)]
    assert found["largest"][0] == {"path": "data/big11.bin", "size": 5012}
    monkeypatch.setattr(snapshot, "COPY_LIMIT", 16 * 1024 * 1024)
    assert api.get("/api/agents/large-files") == (200, {"count": 0, "largest": [], "partial": False})


def test_large_files_needs_a_project(tmp_path, monkeypatch):
    a = Api(tmp_path, monkeypatch, with_project=False)
    try:
        status, error = a.get("/api/agents/large-files")
        assert status == 409 and error["state"] == "no_project"
    finally:
        a.close()


# -- at engine start (DESIGN 18.1 item 6) -----------------------------------------------------------------------


def test_serve_recovers_the_runs_of_every_project_before_anyone_lists_them(tmp_path, tmp_path_factory, monkeypatch):
    project.init_project(tmp_path)
    other = tmp_path_factory.mktemp("other-project")
    project.init_project(other)
    other_pid = read_identity(other).identity.id
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    run_id = run_store.new_run_id()
    run_store.create_run(other_pid, {"id": run_id, "project_id": other_pid, "root": str(other), "state": "running",
                                     "created": run_store.now_iso(), "engine_pid": dead.pid})
    seen: dict[str, Any] = {}

    def serve_forever(self):
        end = time.monotonic() + 30
        while time.monotonic() < end and run_store.load_run(other_pid, run_id)["state"] == "running":
            time.sleep(0.05)
        seen["run"] = run_store.load_run(other_pid, run_id)

    monkeypatch.setattr(server.RceHTTPServer, "serve_forever", serve_forever)
    server.serve(tmp_path, port=0, open_browser=False)
    assert seen["run"]["state"] == "interrupted" and seen["run"]["error"]["code"] == "engine_gone"


def test_a_failing_recovery_never_ends_the_engine(tmp_path, monkeypatch):
    project.init_project(tmp_path)

    def boom(self):
        raise RuntimeError("the store is unreadable")

    seen: dict[str, Any] = {}

    def serve_forever(self):
        for thread in threading.enumerate():
            if thread.name == "researchos-agent-recover":
                thread.join(timeout=10)
        seen["served"] = True

    monkeypatch.setattr(server.agent_manager.AgentManager, "recover_all", boom)
    monkeypatch.setattr(server.RceHTTPServer, "serve_forever", serve_forever)
    server.serve(tmp_path, port=0, open_browser=False)
    assert seen == {"served": True}
    # and building a server alone recovers nothing and starts no thread (the test above of this)
    before = {t.name for t in threading.enumerate()}
    httpd = server.build_server(None, 0)
    try:
        assert {t.name for t in threading.enumerate()} == before
    finally:
        httpd.server_close()


def test_a_run_is_refused_over_http_while_the_work_location_has_no_usable_name(api):
    """DESIGN 19.2 (amended): the refusal is the work location's own sentence in the request's language, as an
    `agent_work_location` state; nothing is recorded, and the run starts once a folder is chosen."""
    api.put("work", "a file, not a folder")
    api.put("agent-work", "another file")
    api.scenario([init_step(), finish_step()])
    status, error = api.post("/api/agents/runs", {"task": "t1"}, lang="en")
    assert status == 409 and error["state"] == "agent_work_location"
    assert error["message"] == error["detail"] == "Choose a folder for process files."
    status, error = api.post("/api/agents/runs", {"task": "t1"}, lang="zh")
    assert status == 409 and error["message"] == "请为过程文件选一个目录。"
    assert run_store.list_runs(api.pid) == [] and api.get("/api/agents/runs")[1]["runs"] == []
    status, info = api.get("/api/outputs/folders")                  # the panel still opens, and says what is missing
    assert status == 200 and info["choose"] == ["work"] and info["folders"]["work"] == "" and info["folders"]["code"] == "code"
    status, error = api.post("/api/outputs/folders", {"revision": None, "folders": info["folders"]}, lang="en")
    assert status == 409 and error["state"] == "workspace_output" and error["message"] == "Choose a folder for process files."
    status, saved = api.post("/api/outputs/folders", {"revision": None, "folders": {**info["folders"], "work": "process"}})
    assert status == 200 and saved["folders"]["work"] == "process" and "choose" not in saved
    assert api.poll(api.start()["id"])["state"] == "ended"


def test_a_run_is_refused_over_http_while_the_output_settings_cannot_be_read(api):
    """DESIGN 19.2 (amended): settings that cannot be read -- a file that is no JSON, a saved work location that
    crowds another -- refuse the run as `agent_output_folders`, a 409 with the settings' own sentence in the request's
    language, not a server error; nothing is recorded, and the run starts once they read again."""
    api.put("research/folders.json", "not json")
    api.scenario([init_step(), finish_step()])
    status, error = api.post("/api/agents/runs", {"task": "t1"}, lang="en")
    assert status == 409 and error["state"] == "agent_output_folders" and "invalid JSON" in error["message"]
    status, error = api.post("/api/agents/runs", {"task": "t1"}, lang="zh")
    assert status == 409 and error["message"].startswith("读不出输出目录的设置：")
    seven = {"code": "code", "data": "data", "figure": "figures", "table": "tables", "literature": "literature",
             "document": "reports", "variable": "research/variables"}
    api.put("research/folders.json", json.dumps({"version": 1, "folders": {**seven, "work": "reports/work"}}))
    status, error = api.post("/api/agents/runs", {"task": "t1"}, lang="en")
    assert status == 409 and error["state"] == "agent_output_folders" and "work location" in error["message"]
    assert run_store.list_runs(api.pid) == [] and api.get("/api/agents/runs")[1]["runs"] == []
    api.put("research/folders.json", json.dumps({"version": 1, "folders": {**seven, "work": "work"}}))
    assert api.poll(api.start()["id"])["state"] == "ended"


def test_the_chinese_refusal_for_unreadable_output_settings_names_the_file_and_the_reason(api):
    """DESIGN 19.2 (amended): the 409 says in Chinese what the English says -- which file (research/folders.json) and,
    where it is known, why: invalid JSON, a version nobody knows, a location that is a file, a work location that
    crowds another."""
    seven = {"code": "code", "data": "data", "figure": "figures", "table": "tables", "literature": "literature",
             "document": "reports", "variable": "research/variables"}
    api.put("src", "a file, not a folder")
    api.scenario([init_step(), finish_step()])
    cases = [
        ("not json", "invalid JSON", "不是有效的 JSON"),
        (json.dumps({"version": 2, "folders": {**seven, "work": "work"}}), "Unknown output-folder settings version", "版本号不认识"),
        (json.dumps({"version": 1, "folders": {**seven, "code": "src", "work": "work"}}), "The code location is a file", "“src”是文件"),
        (json.dumps({"version": 1, "folders": {**seven, "work": "reports/work"}}), "work location must stand apart",
         "过程文件的目录“reports/work”不能与“reports”相同"),
    ]
    for text, english, chinese in cases:
        api.put("research/folders.json", text)
        status, error = api.post("/api/agents/runs", {"task": "t1"}, lang="en")
        assert status == 409 and error["state"] == "agent_output_folders" and english in error["message"]
        status, error = api.post("/api/agents/runs", {"task": "t1"}, lang="zh")
        assert status == 409 and error["message"].startswith("读不出输出目录的设置：")
        assert "research/folders.json" in error["message"] and chinese in error["message"]
        assert "输出目录或文件不符合设置" not in error["message"]
    assert run_store.list_runs(api.pid) == []

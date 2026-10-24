"""Tests for researchos.agents core (DESIGN.md Section 17): this Mac's agents
and programs (profiles), the run store (runs), the before-picture and the
after-run check (snapshot), and Claude Code's PreToolUse guard (guard, hook).

No test starts a real `claude` or `opencode`: programs are tiny scripts with a
`#!<sys.executable>` shebang."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from researchos import names
from researchos import paths, settings
from researchos.agents import guard, hook, labels, profiles, runs, snapshot

PID = "p-" + "0123456789abcdef" * 2


# -- helpers ----------------------------------------------------------------------------------


def _script(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return path


def _version_script(path: Path, version: str = "1.2.3 (Test)") -> Path:
    return _script(path, f"import sys\nprint({version!r})\n")


def _settings() -> dict:
    return settings.read_settings()


# -- profiles: agents ---------------------------------------------------------------------------


def test_agents_default_empty_and_round_trip():
    assert profiles.agents() == []
    saved = profiles.set_agents([{"runner": "claude-code", "model": "sonnet"},
                                 {"runner": "opencode", "model": "lmstudio/qwen3-coder"}])
    assert saved == profiles.agents()
    assert [a.owner for a in saved] == ["agent:claude-code:sonnet", "agent:opencode:lmstudio/qwen3-coder"]
    assert _settings()["agents"] == [{"runner": "claude-code", "model": "sonnet"},
                                     {"runner": "opencode", "model": "lmstudio/qwen3-coder"}]


def test_codex_versions_round_trip_without_aliasing():
    saved = profiles.set_agents([{"runner": "codex", "model": m} for m in ("gpt-6.1-sol", "gpt-5.5")])
    assert [a.model for a in profiles.agents()] == ["gpt-6.1-sol", "gpt-5.5"]
    assert [labels.parse_owner(a.owner) for a in saved] == saved


def test_claude_version_suggestions_and_legacy_aliases_round_trip():
    assert "claude-sonnet-5-5" in profiles.CLAUDE_MODEL_SUGGESTIONS
    assert "claude-opus-4-6" in profiles.CLAUDE_MODEL_SUGGESTIONS
    assert all(m.startswith("claude-") for m in profiles.CLAUDE_MODEL_SUGGESTIONS)
    models = ["claude-sonnet-5-5", "claude-sonnet-4-5-20250929", "sonnet", "opus"]
    profiles.set_agents([{"runner": "claude-code", "model": model} for model in models])
    assert [a.model for a in profiles.agents()] == models


def test_codex_catalog_filters_hidden_models_and_preserves_full_ids(tmp_path):
    catalog = {"models": [{"slug": m, "visibility": v} for m, v in (
        ("gpt-6.1-sol", "list"), ("gpt-5.5", "list"), ("codex-auto-review", "hide"),
        ("gpt-6.1-sol", "list"), ("--invalid", "list"))]}
    binary = _script(tmp_path / "codex", "import json, sys\nassert sys.argv[1:] == ['debug', 'models']\n"
                     + f"print({json.dumps(catalog)!r})\n")
    assert profiles.codex_models(str(binary)) == ["gpt-6.1-sol", "gpt-5.5"]


def test_reasoning_effort_is_saved_separately_from_agent_identity(monkeypatch):
    from researchos.agents import reasoning

    monkeypatch.setattr(profiles, "find", lambda *a, **kw: profiles.Found("codex", "/test/codex", None, None))
    monkeypatch.setattr(profiles, "codex_model_catalog", lambda path: {"gpt-6.1-sol": ["low", "max"]})
    entries = [
        {"runner": "claude-code", "model": "claude-opus-4-6", "reasoning_effort": "max"},
        {"runner": "codex", "model": "gpt-6.1-sol", "reasoning_effort": "max"},
        {"runner": "deepseek-harness", "model": "deepseek-official/deepseek-flash", "reasoning_effort": "off"},
    ]
    profiles.set_agents(entries)
    assert _settings()["agents"] == entries
    assert [profiles.reasoning_effort(a) for a in profiles.agents()] == ["max", "max", "off"]
    assert [labels.parse_owner(a.owner) for a in profiles.agents()] == profiles.agents()
    before = _settings()
    for runner, model, level in (("claude-code", "claude-opus-4-6", "xhigh"),
                                 ("claude-code", "claude-haiku-4-5-20251001", "high"),
                                 ("codex", "gpt-6.1-sol", "high"),
                                 ("deepseek-harness", "deepseek-official/deepseek-flash", "medium"),
                                 ("opencode", "local/model", "high"),
                                 ("codex", "gpt-6.1-sol", "--help")):
        with pytest.raises(ValueError):
            profiles.set_agents([{"runner": runner, "model": model, "reasoning_effort": level}])
        assert _settings() == before
    assert reasoning.known_levels("claude-code", "claude-sonnet-5-5") == ("low", "medium", "high", "xhigh", "max")
    profiles.set_agents([dict(item, reasoning_effort=None) for item in entries])
    assert all(profiles.reasoning_effort(a) is None for a in profiles.agents())
    assert all("reasoning_effort" not in item for item in _settings()["agents"])


def test_codex_catalog_retains_only_advertised_valid_efforts(tmp_path):
    data = {"models": [{"slug": "test-model", "supported_reasoning_levels": [
        {"effort": "low"}, {"effort": "ultra"}, {"effort": "low"}, {"effort": "--help"}, None,
    ]}, {"slug": "hidden", "visibility": "hide", "supported_reasoning_levels": [{"effort": "high"}]}]}
    binary = _script(tmp_path / "codex", f"print({json.dumps(data)!r})\n")
    assert profiles.codex_model_catalog(str(binary)) == {"test-model": ["low", "ultra"]}


@pytest.mark.parametrize("output", ["not json", "[]", '{"models": {}}'])
def test_codex_catalog_does_not_invent_models_on_invalid_output(tmp_path, output):
    binary = _script(tmp_path / "codex", f"print({output!r})\n")
    with pytest.raises(RuntimeError):
        profiles.codex_models(str(binary))


def test_codex_prefers_working_app_cli_and_keeps_an_explicit_path(tmp_path, monkeypatch):
    bundled = _version_script(tmp_path / "bundled-codex", "codex-cli 0.162.0")
    monkeypatch.setattr(profiles, "CODEX_APP_COMMANDS", (str(bundled),))
    monkeypatch.setattr(shutil, "which", lambda *a, **kw: pytest.fail("broken PATH binary should not run"))
    assert profiles.find("codex").path == str(bundled)
    explicit = str(tmp_path / "missing-codex")
    profiles.set_commands({"codex": explicit})
    found = profiles.find("codex")
    assert found.path is None and found.problem == "missing"


def test_harness_bundled_cli_and_exact_authentication_route(tmp_path, monkeypatch):
    bundled = _version_script(tmp_path / "dsh", "0.2.1-rc.2")
    monkeypatch.setattr(profiles, "HARNESS_APP_COMMANDS", (str(bundled),))
    monkeypatch.setattr(shutil, "which", lambda *a, **kw: pytest.fail("bundled runtime should be used"))
    assert profiles.find("deepseek-harness").path == str(bundled)
    models = [*profiles.HARNESS_MODEL_SUGGESTIONS, "deepseek-account/deepseek-future-1"]
    profiles.set_agents([{"runner": "deepseek-harness", "model": m} for m in models])
    assert [a.model for a in profiles.agents()] == models
    assert labels.parse_owner("agent:deepseek-harness:" + models[0]).model == models[0]
    for model in ("deepseek-flash", "other/deepseek-flash", "deepseek-account/"):
        with pytest.raises(ValueError):
            profiles.set_agents([{"runner": "deepseek-harness", "model": model}])
        with pytest.raises(ValueError):
            labels.parse_owner("agent:deepseek-harness:" + model)
    profiles.set_commands({"deepseek-harness": str(tmp_path / "missing")})
    assert profiles.find("deepseek-harness").problem == "missing"


def test_set_agents_keeps_other_settings_and_empty_removes_the_key():
    settings.update_setting("interface_language", "en")
    profiles.set_agents([{"runner": "claude-code", "model": "opus"}])
    assert _settings()["interface_language"] == "en"
    profiles.set_agents([])
    assert "agents" not in _settings()
    assert _settings()["interface_language"] == "en"


def test_set_agents_dedupes_and_keeps_order():
    saved = profiles.set_agents([{"runner": "claude-code", "model": "b"}, {"runner": "claude-code", "model": "a"},
                                 {"runner": "claude-code", "model": "b"}])
    assert [a.model for a in saved] == ["b", "a"]


@pytest.mark.parametrize("items", [
    "sonnet", {"runner": "claude-code", "model": "sonnet"}, [None], ["claude-code"],
    [{"runner": "gemini", "model": "x"}], [{"runner": "claude-code"}], [{"runner": "claude-code", "model": ""}],
    [{"runner": "claude-code", "model": "-p"}], [{"runner": "claude-code", "model": "a b"}],
    [{"runner": "claude-code", "model": "x" * 121}], [{"runner": "claude-code", "model": 5}],
    [{"runner": None, "model": "x"}],
])
def test_set_agents_refuses_invalid_and_writes_nothing(items):
    profiles.set_agents([{"runner": "claude-code", "model": "keep"}])
    with pytest.raises(ValueError):
        profiles.set_agents(items)
    assert [a.model for a in profiles.agents()] == ["keep"]


def test_set_agents_limit():
    ok = [{"runner": "opencode", "model": f"p/m{i}"} for i in range(profiles.MAX_AGENTS)]
    assert len(profiles.set_agents(ok)) == profiles.MAX_AGENTS
    with pytest.raises(ValueError):
        profiles.set_agents(ok + [{"runner": "opencode", "model": "p/extra"}])
    assert len(profiles.agents()) == profiles.MAX_AGENTS


def test_agents_read_is_tolerant():
    settings.update_setting("agents", [{"runner": "claude-code", "model": "sonnet"}, "junk", {"runner": "x", "model": "y"},
                                       {"runner": "claude-code", "model": "-bad"},
                                       {"runner": "claude-code", "model": "sonnet"}, 7])
    assert [a.owner for a in profiles.agents()] == ["agent:claude-code:sonnet"]
    settings.update_setting("agents", "not a list")
    assert profiles.agents() == []
    settings.settings_path().write_text("{broken", encoding="utf-8")
    assert profiles.agents() == []


# -- profiles: commands and limits --------------------------------------------------------------


def test_commands_round_trip_merge_and_remove():
    assert profiles.commands() == {}
    assert profiles.set_commands({"claude-code": "/opt/x/claude"}) == {"claude-code": "/opt/x/claude"}
    assert profiles.set_commands({"opencode": " /opt/x/opencode "}) == {"claude-code": "/opt/x/claude",
                                                                         "opencode": "/opt/x/opencode"}
    assert profiles.set_commands({"claude-code": ""}) == {"opencode": "/opt/x/opencode"}
    assert profiles.set_commands({"opencode": None}) == {}
    assert "agent_commands" not in _settings()


@pytest.mark.parametrize("mapping", [
    "x", {"gemini": "/a/b"}, {"claude-code": "claude"}, {"claude-code": "~/bin/claude"}, {"claude-code": "rel/claude"},
    {"claude-code": "/a/b\nc"}, {"claude-code": "/a/b\0c"}, {"claude-code": 5}, {5: "/a"},
])
def test_set_commands_refuses_invalid(mapping):
    profiles.set_commands({"opencode": "/keep/opencode"})
    with pytest.raises(ValueError):
        profiles.set_commands(mapping)
    assert profiles.commands() == {"opencode": "/keep/opencode"}


def test_commands_read_ignores_invalid_entries():
    settings.update_setting("agent_commands", {"claude-code": "relative", "opencode": "/ok/opencode", "gemini": "/x"})
    assert profiles.commands() == {"opencode": "/ok/opencode"}
    settings.update_setting("agent_commands", ["x"])
    assert profiles.commands() == {}


def test_timeout_minutes():
    assert profiles.timeout_minutes() == profiles.DEFAULT_TIMEOUT_MINUTES == 120
    assert profiles.set_timeout_minutes(30) == 30
    assert profiles.set_timeout_minutes("45") == 45
    assert profiles.set_timeout_minutes(60.0) == 60
    assert profiles.set_timeout_minutes(5) == 5 and profiles.set_timeout_minutes(1440) == 1440
    for bad in (4, 1441, 0, -3, 12.5, "abc", True, [], float("nan"), float("inf"), "1e999"):
        with pytest.raises(ValueError):
            profiles.set_timeout_minutes(bad)
    assert profiles.timeout_minutes() == 1440
    assert profiles.set_timeout_minutes(None) == 120
    assert "agent_timeout_minutes" not in _settings()
    profiles.set_timeout_minutes(30)
    assert profiles.set_timeout_minutes("") == 120
    settings.update_setting("agent_timeout_minutes", 99999)  # out of range in the file: reads as default
    assert profiles.timeout_minutes() == 120
    settings.update_setting("agent_timeout_minutes", True)
    assert profiles.timeout_minutes() == 120


def test_max_budget_usd():
    assert profiles.max_budget_usd() is None
    assert profiles.set_max_budget_usd(2.5) == 2.5
    assert profiles.set_max_budget_usd("10") == 10.0
    assert profiles.set_max_budget_usd(1000) == 1000.0
    for bad in (0, -1, 1000.01, "x", True, float("nan"), float("inf"), [], {}):
        with pytest.raises(ValueError):
            profiles.set_max_budget_usd(bad)
    assert profiles.max_budget_usd() == 1000.0
    assert profiles.set_max_budget_usd(None) is None
    assert "agent_max_budget_usd" not in _settings()
    profiles.set_max_budget_usd(3)
    assert profiles.set_max_budget_usd("") is None
    settings.update_setting("agent_max_budget_usd", -4)
    assert profiles.max_budget_usd() is None


# -- profiles: finding programs -----------------------------------------------------------------


def test_search_path_widens_dedupes_and_expands(monkeypatch):
    monkeypatch.setattr(profiles, "EXTRA_PATH_DIRS", ("/opt/homebrew/bin", "~/.local/bin", "/usr/bin"))
    monkeypatch.setenv("HOME", "/home/someone")
    result = profiles.search_path("/usr/bin::/custom/bin")
    assert result.split(os.pathsep) == ["/usr/bin", "/custom/bin", "/opt/homebrew/bin", "/home/someone/.local/bin"]
    monkeypatch.setenv("PATH", "/x/bin")
    assert profiles.search_path().split(os.pathsep)[:3] == ["/x/bin", "/opt/homebrew/bin", "/home/someone/.local/bin"]
    assert "" not in profiles.search_path(":/a:").split(os.pathsep)


def test_find_with_saved_path_and_version(tmp_path):
    program = _version_script(tmp_path / "claude-fake", "2.0.1 (Claude Code)")
    profiles.set_commands({"claude-code": str(program)})
    found = profiles.find("claude-code")
    assert (found.path, found.version, found.problem, found.found) == (str(program), "2.0.1 (Claude Code)", None, True)
    assert found.payload() == {"runner": "claude-code", "label": "Claude Code", "path": str(program),
                               "version": "2.0.1 (Claude Code)", "problem": None, "found": True}
    skipped = profiles.find("claude-code", check_version=False)
    assert skipped.path == str(program) and skipped.version is None and skipped.problem is None


def test_find_caps_version_and_takes_first_nonempty_line(tmp_path):
    program = _script(tmp_path / "oc", "print()\nprint('v' * 500)\nprint('second')\n")
    profiles.set_commands({"opencode": str(program)})
    assert profiles.find("opencode").version == "v" * 200


def test_find_missing_saved_path(tmp_path):
    profiles.set_commands({"opencode": str(tmp_path / "nowhere")})
    found = profiles.find("opencode")
    assert (found.path, found.problem, found.found) == (None, "missing", False)
    assert found.payload()["found"] is False


def test_find_not_executable_and_directory(tmp_path):
    plain = tmp_path / "claude"
    plain.write_text("not a program", encoding="utf-8")
    plain.chmod(0o644)
    profiles.set_commands({"claude-code": str(plain)})
    found = profiles.find("claude-code")
    assert (found.path, found.problem, found.found) == (str(plain), "not_executable", False)
    folder = tmp_path / "adir"
    folder.mkdir()
    profiles.set_commands({"claude-code": str(folder)})
    assert profiles.find("claude-code").problem == "not_executable"


def test_find_no_version_when_program_fails_or_is_silent(tmp_path):
    failing = _script(tmp_path / "fail", "import sys\nprint('boom', file=sys.stderr)\nsys.exit(3)\n")
    profiles.set_commands({"claude-code": str(failing)})
    found = profiles.find("claude-code")
    assert (found.path, found.version, found.problem, found.found) == (str(failing), None, "no_version", True)
    silent = _script(tmp_path / "silent", "pass\n")
    profiles.set_commands({"claude-code": str(silent)})
    assert profiles.find("claude-code").problem == "no_version"
    broken = tmp_path / "broken"
    broken.write_text("no shebang, not a binary\x00", encoding="utf-8")
    broken.chmod(0o755)
    profiles.set_commands({"claude-code": str(broken)})
    assert profiles.find("claude-code").problem == "no_version"


def test_find_version_timeout_kills_the_program(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "VERSION_TIMEOUT_S", 0.5)
    slow = _script(tmp_path / "slow", "import time\ntime.sleep(60)\n")
    profiles.set_commands({"claude-code": str(slow)})
    started = datetime.now()
    assert profiles.find("claude-code").problem == "no_version"
    assert (datetime.now() - started).total_seconds() < 10


def test_find_looks_on_the_widened_path(tmp_path, monkeypatch):
    extra = tmp_path / "extra"
    extra.mkdir()
    _version_script(extra / "opencode", "0.9.0")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(profiles, "EXTRA_PATH_DIRS", (str(extra),))
    found = profiles.find("opencode")
    assert found.path == str(extra / "opencode") and found.version == "0.9.0"
    monkeypatch.setattr(profiles, "EXTRA_PATH_DIRS", ())
    assert profiles.find("opencode").problem == "missing"
    assert profiles.find("claude-code").problem == "missing"


def test_saved_path_beats_path_lookup(tmp_path, monkeypatch):
    on_path = tmp_path / "bin"
    on_path.mkdir()
    _version_script(on_path / "claude", "from-path")
    mine = _version_script(tmp_path / "mine", "from-setting")
    monkeypatch.setenv("PATH", str(on_path))
    assert profiles.find("claude-code").version == "from-path"
    profiles.set_commands({"claude-code": str(mine)})
    assert profiles.find("claude-code").version == "from-setting"


def test_find_unknown_runner():
    with pytest.raises(ValueError):
        profiles.find("gemini")


def test_mcp_available(monkeypatch, tmp_path):
    import importlib.util

    package = tmp_path / "mcp"
    (package / "server" / "fastmcp").mkdir(parents=True)
    (package / "server" / "fastmcp" / "__init__.py").write_text("", encoding="utf-8")
    spec = SimpleNamespace(submodule_search_locations=[str(package)])
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: spec)
    assert profiles.mcp_available() is True
    (package / "server" / "fastmcp" / "__init__.py").unlink()
    (package / "server" / "fastmcp").rmdir()
    assert profiles.mcp_available() is False
    (package / "server" / "fastmcp.py").write_text("", encoding="utf-8")
    assert profiles.mcp_available() is True
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert profiles.mcp_available() is False

    def boom(name):
        raise ValueError("bad spec")

    monkeypatch.setattr(importlib.util, "find_spec", boom)
    assert profiles.mcp_available() is False


def test_mcp_available_does_not_import_mcp(tmp_path):
    code = ("import sys\nfrom researchos.agents import profiles\nprofiles.mcp_available()\n"
            "sys.exit(1 if any(m == 'mcp' or m.startswith('mcp.') for m in sys.modules) else 0)\n")
    assert subprocess.run([sys.executable, "-c", code], cwd=tmp_path).returncode == 0


def test_opencode_models(tmp_path):
    program = _script(tmp_path / "opencode", (
        "import sys\nassert sys.argv[1:] == ['models']\nimport os\nassert os.environ['OPENCODE_DISABLE_AUTOUPDATE'] == '1'\n"
        "print('anthropic/claude-sonnet-4')\nprint('INFO  starting')\nprint('lmstudio/qwen3-coder')\n"
        "print('anthropic/claude-sonnet-4')\nprint('/leading')\nprint('noslash')\nprint('a b/c')\n"))
    assert profiles.opencode_models(str(program)) == ["anthropic/claude-sonnet-4", "lmstudio/qwen3-coder"]


def test_opencode_models_failures(tmp_path, monkeypatch):
    failing = _script(tmp_path / "bad", "import sys\nprint('no providers configured', file=sys.stderr)\nsys.exit(1)\n")
    with pytest.raises(RuntimeError, match="no providers configured"):
        profiles.opencode_models(str(failing))
    with pytest.raises(RuntimeError):
        profiles.opencode_models(str(tmp_path / "missing"))
    monkeypatch.setattr(profiles, "MODELS_TIMEOUT_S", 0.5)
    slow = _script(tmp_path / "slow", "import time\ntime.sleep(60)\n")
    with pytest.raises(RuntimeError, match="did not answer"):
        profiles.opencode_models(str(slow))


# -- runs ------------------------------------------------------------------------------------------


def _run(run_id: str, **extra) -> dict:
    return {"id": run_id, "project_id": PID, "state": "running", **extra}


def test_run_ids_have_the_documented_shape_and_increase():
    ids = [runs.new_run_id() for _ in range(300)]
    assert all(runs.RUN_ID_RE.match(i) for i in ids)
    assert ids == sorted(ids) and len(set(ids)) == 300
    fixed = runs.new_run_id(datetime(2026, 10, 21, 8, 30, 5, tzinfo=timezone.utc))
    assert fixed.startswith("r-20261021T083005Z-")
    for bad in ("", "r-1", "r-20261021T083005Z-ABCDEF", "x" + fixed, fixed + "\n", None, 5):
        assert not (isinstance(bad, str) and runs.RUN_ID_RE.match(bad))


def test_paths_validate_ids(tmp_path):
    rid = runs.new_run_id()
    assert runs.run_dir(PID, rid) == paths.researchos_home() / "agent-runs" / PID / rid
    assert runs.runs_root() == paths.researchos_home() / "agent-runs"
    for bad_pid in ("../x", "p-123", "", None, PID.upper()):
        with pytest.raises(ValueError):
            runs.project_runs_dir(bad_pid)
        with pytest.raises(ValueError):
            runs.run_dir(bad_pid, rid)
    for bad_rid in ("../../etc", "r-x", "", None):
        with pytest.raises(ValueError):
            runs.run_dir(PID, bad_rid)


def test_create_load_update_save():
    rid = runs.new_run_id()
    created = runs.create_run(PID, {"id": rid, "state": "preparing", "task_text": "中文 task"})
    assert created["project_id"] == PID
    assert (runs.run_dir(PID, rid) / "run.json").is_file()
    assert oct(runs.run_dir(PID, rid).stat().st_mode & 0o777) == "0o700"
    loaded = runs.load_run(PID, rid)
    assert loaded == created and loaded["task_text"] == "中文 task"
    updated = runs.update_run(PID, rid, state="running", pid=42)
    assert updated["state"] == "running" and runs.load_run(PID, rid)["pid"] == 42
    runs.save_run(PID, {**updated, "state": "done"})
    assert runs.load_run(PID, rid)["state"] == "done"
    with pytest.raises(FileExistsError):
        runs.create_run(PID, {"id": rid})
    with pytest.raises(ValueError):
        runs.create_run(PID, {"state": "x"})
    with pytest.raises(ValueError):
        runs.update_run(PID, rid, id="r-20260101T000000Z-aaaaaa")


def test_update_missing_run_and_tolerant_load():
    rid = runs.new_run_id()
    assert runs.load_run(PID, rid) is None
    with pytest.raises(KeyError):
        runs.update_run(PID, rid, state="x")
    runs.create_run(PID, {"id": rid})
    (runs.run_dir(PID, rid) / "run.json").write_text("{not json", encoding="utf-8")
    assert runs.load_run(PID, rid) is None
    (runs.run_dir(PID, rid) / "run.json").write_text("[1]", encoding="utf-8")
    assert runs.load_run(PID, rid) is None
    (runs.run_dir(PID, rid) / "run.json").write_text(json.dumps({"id": "r-20200101T000000Z-aaaaaa"}), encoding="utf-8")
    assert runs.load_run(PID, rid) is None
    (runs.run_dir(PID, rid) / "run.json").write_bytes(b"\xff\xfe\x00")
    assert runs.load_run(PID, rid) is None
    with pytest.raises(KeyError):
        runs.update_run(PID, rid, state="x")


def test_update_run_is_atomic_under_threads():
    rid = runs.new_run_id()
    runs.create_run(PID, {"id": rid, "n": 0})

    def increment(key):
        for i in range(25):
            runs.update_run(PID, rid, **{key: i})

    threads = [threading.Thread(target=increment, args=(f"k{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = runs.load_run(PID, rid)
    assert [final[f"k{n}"] for n in range(4)] == [24] * 4  # no thread's update was lost to another's


def test_list_runs_newest_first_and_skips_bad():
    ids = [runs.new_run_id() for _ in range(5)]
    for rid in ids:
        runs.create_run(PID, _run(rid))
    broken = runs.new_run_id()
    runs.create_run(PID, _run(broken))
    (runs.run_dir(PID, broken) / "run.json").write_text("garbage", encoding="utf-8")
    (runs.project_runs_dir(PID) / "not-a-run").mkdir()
    (runs.project_runs_dir(PID) / "stray.txt").write_text("x", encoding="utf-8")
    assert [r["id"] for r in runs.list_runs(PID)] == list(reversed(ids))
    assert runs.list_runs("p-" + "f" * 32) == []


def test_append_line_and_read_lines_tolerance(tmp_path):
    target = tmp_path / "x.jsonl"
    runs.append_line(target, {"a": 1})
    runs.append_line(target, {"b": "中文"})
    with open(target, "ab") as handle:
        handle.write(b"not json\n\n[1, 2]\n{\"half\": ")
    assert runs.read_lines(target) == [{"a": 1}, {"b": "中文"}]
    assert runs.read_lines(tmp_path / "missing.jsonl") == []
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_read_lines_tail_drops_a_cut_line(tmp_path):
    target = tmp_path / "t.jsonl"
    for i in range(10):
        runs.append_line(target, {"i": i, "pad": "x" * 20})
    lines = target.read_bytes().split(b"\n")
    one = len(lines[0]) + 1
    assert [r["i"] for r in runs.read_lines(target, tail_bytes=one * 3)] == [7, 8, 9]  # exactly at a line start
    assert [r["i"] for r in runs.read_lines(target, tail_bytes=one * 3 + 5)] == [7, 8, 9]  # cut inside line 6
    assert [r["i"] for r in runs.read_lines(target, tail_bytes=one * 3 - 5)] == [8, 9]
    assert len(runs.read_lines(target, tail_bytes=10_000)) == 10
    assert runs.read_lines(target, tail_bytes=3) == []


def test_append_line_from_many_threads_never_interleaves(tmp_path):
    target = tmp_path / "many.jsonl"

    def writer(n):
        for i in range(40):
            runs.append_line(target, {"n": n, "i": i, "pad": "y" * 3000})

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    records = runs.read_lines(target)
    assert len(records) == 240
    assert sorted((r["n"], r["i"]) for r in records) == sorted((n, i) for n in range(6) for i in range(40))


def test_signals():
    rid = runs.new_run_id()
    runs.create_run(PID, _run(rid))
    assert runs.read_signals(PID, rid) == []
    runs.append_signal(PID, rid, "ask", question="Which year?")
    runs.append_signal(PID, rid, "note", path="research/notes/a.md", paths=["research/notes/a.md"])
    runs.append_signal(PID, rid, "complete", path="research/results/x.md", tasks_sha="ab" * 32)
    got = runs.read_signals(PID, rid)
    assert [s["kind"] for s in got] == ["ask", "note", "complete"]
    assert got[0]["question"] == "Which year?" and got[0]["at"].endswith("Z")
    with pytest.raises(ValueError):
        runs.append_signal(PID, rid, "approve")
    with pytest.raises(ValueError):
        runs.append_signal(PID, rid, "note", at="yesterday")
    with pytest.raises(OSError):
        runs.append_signal(PID, runs.new_run_id(), "note", path="x")  # the run's folder does not exist


def test_prune_keeps_newest_runs_and_never_an_active_one(monkeypatch):
    monkeypatch.setattr(runs, "KEEP_RUNS", 5)
    monkeypatch.setattr(runs, "KEEP_BACKUPS", 2)
    ids = [runs.new_run_id() for _ in range(8)]
    for index, rid in enumerate(ids):
        state = "running" if index == 0 else "done"  # the oldest is still active
        runs.create_run(PID, _run(rid, state=state, revertible=True))
        (runs.run_dir(PID, rid) / "before").mkdir()
        (runs.run_dir(PID, rid) / "before" / "f.txt").write_text("x", encoding="utf-8")
    runs.prune(PID)
    kept = [r["id"] for r in runs.list_runs(PID)]
    assert kept == list(reversed(ids[3:])) + [ids[0]]
    by_id = {r["id"]: r for r in runs.list_runs(PID)}
    for rid in ids[6:]:  # the newest two keep their copies
        assert by_id[rid]["revertible"] is True and (runs.run_dir(PID, rid) / "before").is_dir()
    for rid in ids[3:6]:
        assert by_id[rid]["revertible"] is False and not (runs.run_dir(PID, rid) / "before").exists()
    assert by_id[ids[0]]["state"] == "running" and (runs.run_dir(PID, ids[0]) / "before").is_dir()
    for rid in ids[1:3]:
        assert not runs.run_dir(PID, rid).exists()
    runs.prune(PID)  # idempotent
    assert {r["id"] for r in runs.list_runs(PID)} == set(kept)
    runs.prune("p-" + "e" * 32)  # no folder: nothing to do


# -- snapshot ----------------------------------------------------------------------------------------


def _project(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return root


def test_take_records_hashes_copies_and_skips(tmp_path):
    root = _project(tmp_path / "proj", {
        "data.csv": "a,b\n1,2\n", "code/run.py": "print(1)\n", "中文/说明.md": "你好",
        ".researchos/attempts.toml": "x = 1\n", ".researchos/backups/old.bak": "old",
        f"{names.LEGACY_PROJECT_DIRNAME}/backups/old2.bak": "old", ".git/config": "[core]", "node_modules/m/x.js": "x",
        "sub/__pycache__/a.pyc": "x", ".pytest_cache/v/cache/nodeids": "[]", "sub/.venv/pyvenv.cfg": "x", ".hidden": "h"})
    copies = tmp_path / "copies"
    picture = snapshot.take(root, copy_to=copies)
    # `.git/config` is the one thing of `.git/` the picture holds (18.1 item 4); the backups are listed apart
    assert set(picture["files"]) == {"data.csv", "code/run.py", "中文/说明.md", ".researchos/attempts.toml", ".hidden",
                                     ".git/config", "sub/__pycache__/a.pyc", ".pytest_cache/v/cache/nodeids",
                                     "node_modules", "sub/.venv"}
    assert picture["backups"] == {".researchos/backups/old.bak": {"size": 3},
                                  f"{names.LEGACY_PROJECT_DIRNAME}/backups/old2.bak": {"size": 3}}
    assert picture["backups_partial"] is False
    entry = picture["files"]["data.csv"]
    import hashlib

    assert entry["sha256"] == hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    assert entry["size"] == 8 and entry["copied"] is True and isinstance(entry["mtime_ns"], int)
    assert (copies / "data.csv").read_bytes() == b"a,b\n1,2\n"
    assert (copies / "中文" / "说明.md").read_text(encoding="utf-8") == "你好"
    assert (copies / ".researchos" / "attempts.toml").is_file()
    assert (copies / ".git" / "config").read_text() == "[core]"
    assert (copies / "sub/__pycache__/a.pyc").read_text() == "x"
    assert (copies / ".pytest_cache/v/cache/nodeids").read_text() == "[]"
    assert not (copies / ".researchos" / "backups").exists() and not (copies / "node_modules").exists()
    assert picture["partial"] is False and picture["not_copied"] == 0
    assert picture["git"]["head"] is None and picture["git"]["branch"] is None and len(picture["git"]["refs"]) == 64
    assert picture["copied_bytes"] == sum(e["size"] for e in picture["files"].values())
    json.dumps(picture)  # it is stored as before.json


def test_new_dependency_tree_is_fingerprinted_without_following_links(tmp_path):
    root = _project(tmp_path / "proj", {"note.md": "existing"})
    before = snapshot.take(root)
    _project(root, {".venv/pyvenv.cfg": "home = /python", ".venv/lib/module.py": "x = 1"})
    (root / ".venv/python").symlink_to(tmp_path / "absent-outside")
    after = snapshot.take(root, reuse=before)
    tree = after["files"][".venv"]
    assert tree["directory"] and tree["tree_sha256"] and not tree["tree_partial"]
    assert snapshot.same(tree, snapshot.fingerprint(root, ".venv", directory=True))
    unchanged_root = (root / ".venv").stat().st_mtime_ns
    (root / ".venv/lib/module.py").write_text("x = 2")
    assert (root / ".venv").stat().st_mtime_ns == unchanged_root
    assert not snapshot.same(tree, snapshot.fingerprint(root, ".venv", directory=True))


def test_existing_dependency_trees_are_not_scanned_or_treated_as_run_outputs(tmp_path, monkeypatch):
    root = _project(tmp_path / "proj", {".venv/lib/module.py": "old", "node_modules/pkg/index.js": "old"})
    monkeypatch.setattr(snapshot, "_tree_entry", lambda _: pytest.fail("existing dependencies must not be walked"))
    before = snapshot.take(root)
    assert before["files"][".venv"]["excluded"]
    (root / ".venv/lib/module.py").write_text("changed dependency")
    after = snapshot.take(root, reuse=before)
    assert snapshot.compare(before, after) == {"created": [], "modified": [], "deleted": []}
    # An older snapshot did not record directory existence: never infer that an environment is new.
    before.pop("excluded_dirs_recorded")
    before["files"].pop(".venv")
    before["files"].pop("node_modules")
    assert ".venv" not in snapshot.take(root, reuse=before)["files"]


def test_an_incomplete_dependency_tree_cannot_match_for_undo(tmp_path, monkeypatch):
    root = _project(tmp_path / "proj", {".venv/a": "a", ".venv/b": "b"})
    monkeypatch.setattr(snapshot, "FILE_LIMIT", 1)
    tree = snapshot.fingerprint(root, ".venv", directory=True)
    assert tree["tree_partial"] and tree["tree_sha256"] is None
    assert not snapshot.same(tree, tree)


def test_take_without_copies(tmp_path):
    root = _project(tmp_path / "proj", {"a.txt": "a"})
    picture = snapshot.take(root)
    assert "copied" not in picture["files"]["a.txt"] and picture["files"]["a.txt"]["sha256"]
    assert picture["not_copied"] == 1 and picture["copied_bytes"] == 0


def test_take_records_the_folders_empty_ones_and_ones_holding_only_noise_included(tmp_path, monkeypatch):
    """18.1 item 5: undo keeps a folder that existed before the run, so the picture says which did: every folder
    the main walk went into, not only the ones that hold a recorded file."""
    root = _project(tmp_path / "proj", {
        "code/run.py": "1", "figs/.DS_Store": "noise", "deep/er/x.txt": "x", "__pycache__/a.pyc": "x",
        "node_modules/m/x.js": "x", ".researchos/backups/old.bak": "o", ".git/config": "[core]"})
    (root / "empty").mkdir()
    (root / "empty" / "nested").mkdir()
    (root / "link-to-folder").symlink_to(root / "code")
    picture = snapshot.take(root)
    assert picture["dirs"] == sorted([".researchos", "__pycache__", "code", "deep", "deep/er", "empty", "empty/nested", "figs"])
    assert picture["dirs_partial"] is False
    assert "figs/.DS_Store" not in picture["files"]  # the noise is not a file of the picture; its folder is
    json.dumps(picture)
    monkeypatch.setattr(snapshot, "DIR_LIMIT", 3)
    cut = snapshot.take(root)
    assert cut["dirs_partial"] is True and len(cut["dirs"]) == 3 and cut["partial"] is False


def test_large_files_names_the_ten_biggest_over_the_copy_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "COPY_LIMIT", 10)
    files = {f"data/big{i:02d}.bin": b"x" * (11 + i) for i in range(12)}
    files.update({"small.txt": b"x" * 10, "node_modules/m/huge.bin": b"x" * 99, ".git/objects/pack": b"x" * 99,
                  "sub/.DS_Store": b"x" * 99, ".researchos/backups/old.bak": b"x" * 99, "tied/a.bin": b"x" * 50,
                  "tied/b.bin": b"x" * 50})
    root = _project(tmp_path / "proj", files)
    (root / "link.bin").symlink_to(root / "tied" / "a.bin")
    found = snapshot.large_files(root)
    assert found["count"] == 14 and found["partial"] is False
    assert [f["path"] for f in found["largest"]] == [
        "tied/a.bin", "tied/b.bin", "data/big11.bin", "data/big10.bin", "data/big09.bin", "data/big08.bin",
        "data/big07.bin", "data/big06.bin", "data/big05.bin", "data/big04.bin"]
    assert found["largest"][0]["size"] == 50 and found["largest"][2]["size"] == 22
    assert snapshot.large_files(root, over=40)["count"] == 2
    cut = snapshot.large_files(root, limit=3)
    assert cut["partial"] is True and cut["count"] <= 3
    assert snapshot.large_files(tmp_path / "missing") == {"count": 0, "largest": [], "partial": False}
    monkeypatch.setattr(snapshot, "COPY_LIMIT", 16 * 1024 * 1024)  # the default is the copy limit, read at call time
    assert snapshot.large_files(root)["count"] == 0


def test_take_copy_limit_and_budget(tmp_path, monkeypatch):
    root = _project(tmp_path / "proj", {"small.txt": "1234", "big.bin": b"x" * 100, "also.txt": "12345678"})
    monkeypatch.setattr(snapshot, "COPY_LIMIT", 10)
    picture = snapshot.take(root, copy_to=tmp_path / "c")
    files = picture["files"]
    assert files["small.txt"]["copied"] and files["also.txt"]["copied"] and "copied" not in files["big.bin"]
    assert "sha256" in files["big.bin"]  # hashed up to HASH_LIMIT, copied up to COPY_LIMIT
    assert not (tmp_path / "c" / "big.bin").exists()
    assert picture["not_copied"] == 1 and picture["copied_bytes"] == 12
    monkeypatch.setattr(snapshot, "COPY_LIMIT", 1000)
    monkeypatch.setattr(snapshot, "COPY_BUDGET", 110)
    picture = snapshot.take(root, copy_to=tmp_path / "c2")
    copied = sorted(r for r, e in picture["files"].items() if e.get("copied"))
    assert picture["copied_bytes"] <= 110 and picture["not_copied"] == 3 - len(copied) and copied
    for rel in copied:
        assert (tmp_path / "c2" / rel).read_bytes() == (root / rel).read_bytes()


def test_take_hash_limit(tmp_path, monkeypatch):
    root = _project(tmp_path / "proj", {"huge.bin": b"x" * 50})
    monkeypatch.setattr(snapshot, "HASH_LIMIT", 10)
    entry = snapshot.take(root, copy_to=tmp_path / "c")["files"]["huge.bin"]
    assert "sha256" not in entry and "copied" not in entry and entry["size"] == 50


def test_take_file_limit_marks_partial(tmp_path, monkeypatch):
    root = _project(tmp_path / "proj", {f"f{i}.txt": "x" for i in range(5)} | {"sub/g.txt": "y"})
    monkeypatch.setattr(snapshot, "FILE_LIMIT", 3)
    picture = snapshot.take(root)
    assert picture["partial"] is True and len(picture["files"]) == 3
    monkeypatch.setattr(snapshot, "FILE_LIMIT", 6)
    picture = snapshot.take(root)
    assert picture["partial"] is False and len(picture["files"]) == 6


def test_take_does_not_follow_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    root = _project(tmp_path / "proj", {"real.txt": "r"})
    os.symlink(outside, root / "linkdir")
    os.symlink(outside / "secret.txt", root / "linkfile")
    os.symlink(root / "nothing", root / "dangling")
    copies = tmp_path / "c"
    files = snapshot.take(root, copy_to=copies)["files"]
    assert set(files) == {"real.txt", "linkdir", "linkfile", "dangling"}
    assert files["linkdir"]["link"] == str(outside) and "sha256" not in files["linkdir"]
    assert files["linkfile"]["link"] == str(outside / "secret.txt") and "copied" not in files["linkfile"]
    assert files["dangling"]["link"] == str(root / "nothing")
    assert not (copies / "linkdir").exists() and not (copies / "linkfile").exists()


def test_take_never_opens_dataless_or_fifo(tmp_path, monkeypatch):
    root = _project(tmp_path / "proj", {"cloud.csv": "cloud", "local.csv": "local"})
    os.mkfifo(root / "pipe")
    real = paths.is_dataless
    monkeypatch.setattr(paths, "is_dataless", lambda p: Path(p).name == "cloud.csv" or real(p))
    opened = []
    builtin_open = open

    def spy(file, *args, **kwargs):
        opened.append(os.fspath(file))
        return builtin_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", spy)
    files = snapshot.take(root, copy_to=tmp_path / "c")["files"]
    monkeypatch.undo()
    assert files["cloud.csv"]["dataless"] is True and "sha256" not in files["cloud.csv"]
    assert not any(p.endswith("cloud.csv") or p.endswith("pipe") for p in opened)
    assert "sha256" not in files["pipe"] and files["local.csv"]["sha256"]


def test_take_survives_unreadable_file(tmp_path, monkeypatch):
    root = _project(tmp_path / "proj", {"a.txt": "a", "b.txt": "b"})
    builtin_open = open

    def refusing(file, *args, **kwargs):
        if os.fspath(file).endswith("a.txt"):
            raise PermissionError("denied")
        return builtin_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", refusing)
    files = snapshot.take(root, copy_to=tmp_path / "c")["files"]
    monkeypatch.undo()
    assert "sha256" not in files["a.txt"] and files["a.txt"]["size"] == 1 and not (tmp_path / "c" / "a.txt").exists()
    assert files["b.txt"]["copied"]


def test_fingerprint(tmp_path):
    root = _project(tmp_path / "proj", {"d/x.csv": "1,2"})
    assert snapshot.fingerprint(root, "d/x.csv")["sha256"]
    assert snapshot.fingerprint(root, "d/missing.csv") is None
    assert snapshot.fingerprint(root, "d") is None
    assert snapshot.fingerprint(root, "../x") is None and snapshot.fingerprint(root, "/etc/passwd") is None
    assert snapshot.fingerprint(root, "") is None


def test_same_rules():
    base = {"size": 3, "mtime_ns": 10, "sha256": "aa"}
    assert snapshot.same(None, None)
    assert not snapshot.same(None, base) and not snapshot.same(base, None)
    assert snapshot.same(base, {"size": 3, "mtime_ns": 99, "sha256": "aa"})  # touched, same content
    assert not snapshot.same(base, {"size": 3, "mtime_ns": 10, "sha256": "bb"})
    assert snapshot.same({"size": 3, "mtime_ns": 10}, {"size": 3, "mtime_ns": 10})
    assert not snapshot.same({"size": 3, "mtime_ns": 10}, {"size": 3, "mtime_ns": 11})
    assert not snapshot.same({"size": 3, "mtime_ns": 10}, {"size": 4, "mtime_ns": 10})
    assert snapshot.same({"size": 1, "mtime_ns": 1, "link": "t"}, {"size": 9, "mtime_ns": 2, "link": "t"})
    assert not snapshot.same({"size": 1, "mtime_ns": 1, "link": "t"}, {"size": 1, "mtime_ns": 1, "link": "u"})
    assert not snapshot.same({"size": 1, "mtime_ns": 1, "link": "t"}, {"size": 1, "mtime_ns": 1, "sha256": "aa"})


def test_compare_and_exclude(tmp_path):
    root = _project(tmp_path / "proj", {"keep.txt": "k", "edit.txt": "old", "gone.txt": "g", "touch.txt": "same"})
    before = snapshot.take(root)
    (root / "edit.txt").write_text("new content", encoding="utf-8")
    (root / "gone.txt").unlink()
    (root / "new.txt").write_text("n", encoding="utf-8")
    (root / "people.txt").write_text("p", encoding="utf-8")
    os.utime(root / "touch.txt", ns=(1, 1))
    after = snapshot.take(root)
    assert snapshot.compare(before, after) == {"created": ["new.txt", "people.txt"], "modified": ["edit.txt"],
                                               "deleted": ["gone.txt"]}
    assert snapshot.compare(before, after, exclude={"people.txt", "edit.txt"}) == {
        "created": ["new.txt"], "modified": [], "deleted": ["gone.txt"]}
    assert snapshot.compare(before["files"], after["files"])["created"] == ["new.txt", "people.txt"]  # bare maps too
    assert snapshot.compare(before, before) == {"created": [], "modified": [], "deleted": []}
    # a JSON round trip (before.json) changes nothing
    assert snapshot.compare(json.loads(json.dumps(before)), after)["modified"] == ["edit.txt"]


ATTEMPTS_TOML = """\
file = "map.md"
heading = "Attempts"

[columns]
id = "#"
date = "Date"
description = "Path"
variables = "Variables"
result = "Result"
verdict = "Verdict"
"""


def _attempts_project(tmp_path: Path, file_value: str = "map.md") -> Path:
    root = _project(tmp_path / "proj", {
        ".researchos/attempts.toml": ATTEMPTS_TOML.replace('"map.md"', f'"{file_value}"'),
        "map.md": "# map\n", "data/raw.csv": "a\n1\n", "data/clean.DTA": b"\x00\x01", "data/notes.txt": "n",
        "research/tasks.json": "[]", "config.json": "{}", "figs/plot.png": b"png"})
    return root


def test_attempts_table(tmp_path):
    root = _attempts_project(tmp_path)
    assert snapshot.attempts_table(root) == "map.md"
    nested = _attempts_project(tmp_path / "n", "./docs//map.md")
    assert snapshot.attempts_table(nested) == "docs/map.md"
    assert snapshot.attempts_table(_attempts_project(tmp_path / "abs", str(tmp_path / "abs" / "proj" / "map.md"))) == "map.md"
    assert snapshot.attempts_table(_attempts_project(tmp_path / "up", "../elsewhere.md")) is None
    assert snapshot.attempts_table(_attempts_project(tmp_path / "ab2", "/etc/passwd")) is None
    assert snapshot.attempts_table(tmp_path / "no-such-project") is None
    bare = _project(tmp_path / "bare", {"a.txt": "a"})
    assert snapshot.attempts_table(bare) is None
    (bare / ".researchos").mkdir()
    (bare / ".researchos" / "attempts.toml").write_text("this is [not valid", encoding="utf-8")
    assert snapshot.attempts_table(bare) is None


def test_protected_lists_data_records_and_dirs(tmp_path):
    root = _attempts_project(tmp_path)
    before = snapshot.take(root)
    prot = snapshot.protected(root, before)
    assert prot["dirs"] == [".researchos", names.LEGACY_PROJECT_DIRNAME, ".git", ".claude", ".codex", ".opencode"]
    assert prot["records"] == ["map.md", "research/tasks.json", "research/folders.json"]
    assert prot["data"] == ["config.json", "data/clean.DTA", "data/raw.csv"]
    assert prot["files"] == sorted(prot["data"] + prot["records"])
    assert "figs/plot.png" not in prot["files"] and "data/notes.txt" not in prot["files"]
    json.dumps(prot)


def test_protected_without_attempts_config_or_tasks_file(tmp_path):
    root = _project(tmp_path / "proj", {"x.csv": "1", "script.py": "1"})
    prot = snapshot.protected(root, snapshot.take(root))
    assert prot["records"] == ["research/tasks.json", "research/folders.json"]  # protected even before creation
    assert prot["data"] == ["x.csv"] and "research/tasks.json" in prot["files"]


def test_protected_attempt_table_that_is_a_data_file_is_a_record(tmp_path):
    root = _project(tmp_path / "proj", {".researchos/attempts.toml": ATTEMPTS_TOML.replace("map.md", "attempts.csv"),
                                        "attempts.csv": "a", "other.csv": "b"})
    prot = snapshot.protected(root, snapshot.take(root))
    assert prot["records"] == ["attempts.csv", "research/tasks.json", "research/folders.json"] and prot["data"] == ["other.csv"]


def _violations(root, mutate, *, allowed=None, after_sha_from_disk=True):
    before = snapshot.take(root)
    prot = snapshot.protected(root, before)
    mutate(root)
    after = snapshot.take(root)
    changes = snapshot.compare(before, after)
    return snapshot.violations(changes, prot, git_before=before["git"], git_after=after["git"],
                               allowed=allowed or {}), changes


def test_violations_matrix(tmp_path):
    root = _attempts_project(tmp_path)

    def mutate(r):
        (r / "data/raw.csv").write_text("a\n9\n", encoding="utf-8")        # data changed
        (r / "data/clean.DTA").unlink()                                      # data deleted
        (r / "map.md").write_text("# verdict changed\n", encoding="utf-8")   # record changed
        (r / "research/tasks.json").unlink()                                 # record deleted
        (r / ".researchos/attempts.toml").write_text("x", encoding="utf-8")  # researchos changed
        (r / ".researchos/new.toml").write_text("x", encoding="utf-8")       # researchos created
        (r / names.LEGACY_PROJECT_DIRNAME).mkdir()
        (r / names.LEGACY_PROJECT_DIRNAME / "old.toml").write_text("x", encoding="utf-8")
        (r / "new.csv").write_text("fine", encoding="utf-8")                 # new data: allowed
        (r / "script.py").write_text("fine", encoding="utf-8")
        (r / "data/notes.txt").write_text("changed but not protected", encoding="utf-8")
        (r / "figs/plot.png").unlink()                                       # not protected

    found, _ = _violations(root, mutate)
    assert sorted((v["path"], v["reason"]) for v in found) == sorted([
        ("data/raw.csv", "data_changed"), ("data/clean.DTA", "data_deleted"), ("map.md", "record_changed"),
        ("research/tasks.json", "record_deleted"), (".researchos/attempts.toml", "researchos_changed"),
        (".researchos/new.toml", "researchos_changed"), (f"{names.LEGACY_PROJECT_DIRNAME}/old.toml", "researchos_changed")])
    assert all(set(v) == {"path", "reason"} for v in found)


def test_violations_untouched_and_touched_only(tmp_path):
    root = _attempts_project(tmp_path)
    found, changes = _violations(root, lambda r: os.utime(r / "data/raw.csv", ns=(5, 5)))
    assert found == [] and changes["modified"] == []


def test_violations_allowed_sha_for_tasks_json(tmp_path):
    import hashlib

    root = _attempts_project(tmp_path)
    new_tasks = b'[{"id": "t1", "done": true}]\n'
    sha = hashlib.sha256(new_tasks).hexdigest()

    def write_tasks(r):
        (r / "research/tasks.json").write_bytes(new_tasks)

    found, changes = _violations(root, write_tasks)
    assert changes["modified"] == ["research/tasks.json"]
    assert found == [{"path": "research/tasks.json", "reason": "record_changed"}]
    found, _ = _violations(root, lambda r: None, allowed={"research/tasks.json": {sha}})
    assert found == []
    root2 = _attempts_project(tmp_path / "two")
    found, _ = _violations(root2, write_tasks, allowed={"research/tasks.json": {sha}})
    assert found == []
    root3 = _attempts_project(tmp_path / "three")
    found, _ = _violations(root3, lambda r: (r / "research/tasks.json").write_bytes(new_tasks + b" "),
                           allowed={"research/tasks.json": {sha}})
    assert found == [{"path": "research/tasks.json", "reason": "record_changed"}]  # not what the tools wrote
    root4 = _attempts_project(tmp_path / "four")
    found, _ = _violations(root4, write_tasks, allowed={"map.md": {sha}})  # allowed for another path only
    assert found == [{"path": "research/tasks.json", "reason": "record_changed"}]


def test_violations_created_record_and_explicit_after(tmp_path):
    import hashlib

    root = _project(tmp_path / "proj", {"a.csv": "1"})
    before = snapshot.take(root)
    prot = snapshot.protected(root, before)
    (root / "research").mkdir()
    (root / "research/tasks.json").write_text("[]", encoding="utf-8")
    after = snapshot.take(root)
    changes = snapshot.compare(before, after)
    assert changes["created"] == ["research/tasks.json"]
    assert snapshot.violations(changes, prot, git_before=None, git_after=None, allowed={}) == [
        {"path": "research/tasks.json", "reason": "record_changed"}]
    sha = hashlib.sha256(b"[]").hexdigest()
    assert snapshot.violations(changes, prot, git_before=None, git_after=None,
                               allowed={"research/tasks.json": {sha}}, after=after) == []
    # a record whose hash cannot be determined is a violation
    (root / "research/tasks.json").unlink()
    assert snapshot.violations(changes, prot, git_before=None, git_after=None,
                               allowed={"research/tasks.json": {sha}}) == [
        {"path": "research/tasks.json", "reason": "record_changed"}]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                    "-C", str(root), *args], check=True, capture_output=True)


def _git_project(tmp_path: Path) -> Path:
    root = _project(tmp_path / "proj", {"a.csv": "1", "b.py": "x"})
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "first")
    return root


def test_git_state(tmp_path):
    assert snapshot.git_state(_project(tmp_path / "plain", {"a": "a"})) is None
    root = _git_project(tmp_path)
    state = snapshot.git_state(root)
    assert state["branch"] == "main" and len(state["head"]) == 40
    assert snapshot.take(root)["git"] == state
    _git(root, "checkout", "-q", "--detach")
    detached = snapshot.git_state(root)
    assert detached["branch"] is None and detached["head"] == state["head"]
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _git(fresh, "init", "-q", "-b", "trunk")
    fresh_state = snapshot.git_state(fresh)
    assert fresh_state["head"] is None and fresh_state["branch"] == "trunk" and len(fresh_state["refs"]) == 64


def test_git_state_ignores_repository_hooks(tmp_path):
    root = _git_project(tmp_path)
    marker = tmp_path / "hook-ran"
    hook_file = root / ".git" / "hooks" / "reference-transaction"
    hook_file.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook_file.chmod(0o755)
    snapshot.git_state(root)
    assert not marker.exists()


def test_violations_git_changed(tmp_path):
    root = _git_project(tmp_path)

    def commit(r):
        (r / "new.py").write_text("y", encoding="utf-8")
        _git(r, "add", "-A")
        _git(r, "commit", "-q", "-m", "agent commit")

    found, _ = _violations(root, commit)
    assert found == [{"path": ".git", "reason": "git_changed"}]
    root2 = _git_project(tmp_path / "b")
    found, _ = _violations(root2, lambda r: _git(r, "checkout", "-q", "-b", "other"))
    assert found == [{"path": ".git", "reason": "git_changed"}]
    root3 = _git_project(tmp_path / "c")
    found, _ = _violations(root3, lambda r: (r / "b.py").write_text("edited, not committed", encoding="utf-8"))
    assert found == []
    root4 = _git_project(tmp_path / "d")
    import shutil

    found, _ = _violations(root4, lambda r: shutil.rmtree(r / ".git"))
    # the repository is gone, and its configuration with it
    assert {"path": ".git", "reason": "git_changed"} in found
    assert {v["reason"] for v in found} == {"git_changed", "git_config_changed"}
    # no repository before and none after: nothing to report
    found, _ = _violations(_project(tmp_path / "plain", {"a": "a"}), lambda r: None)
    assert found == []


# -- git's hooks and config, folders at any depth, backups (18.1 item 4) -----------------------------------------


def _plant(root: Path, name: str, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path = root / ".git" / "hooks" / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_take_records_git_config_and_hooks_with_copies_but_not_the_samples(tmp_path):
    root = _git_project(tmp_path)
    _plant(root, "pre-commit")
    (root / ".git" / "hooks" / "sub").mkdir()
    (root / ".git" / "hooks" / "sub" / "helper.sh").write_text("x", encoding="utf-8")
    picture = snapshot.take(root, copy_to=tmp_path / "copies")
    git_files = {rel for rel in picture["files"] if rel.startswith(".git/")}
    assert git_files == {".git/config", ".git/hooks/pre-commit", ".git/hooks/sub/helper.sh"}
    assert (root / ".git" / "hooks" / "applypatch-msg.sample").exists()  # the templates are there, and not recorded
    assert picture["files"][".git/hooks/pre-commit"]["copied"] is True
    assert (tmp_path / "copies" / ".git" / "hooks" / "pre-commit").read_text() == "#!/bin/sh\nexit 0\n"
    assert (tmp_path / "copies" / ".git" / "config").read_text() == (root / ".git" / "config").read_text()
    assert ".git/HEAD" not in picture["files"] and not any(r.startswith(".git/objects") for r in picture["files"])
    # not a repository, or a `.git` that is only a pointer file (a worktree): nothing to record
    assert not any(r.startswith(".git") for r in snapshot.take(_project(tmp_path / "plain", {"a": "a"}))["files"])
    pointer = _project(tmp_path / "pointer", {".git": "gitdir: /elsewhere/.git\n"})
    assert set(snapshot.take(pointer)["files"]) == {".git"}  # the pointer is a file like any other


def test_a_planted_hook_or_a_changed_config_is_out_of_bounds(tmp_path):
    root = _git_project(tmp_path)
    _plant(root, "post-merge", "#!/bin/sh\necho old\n")
    found, changes = _violations(root, lambda r: (_plant(r, "pre-commit"), _plant(r, "post-merge", "#!/bin/sh\necho new\n")))
    assert changes["created"] == [".git/hooks/pre-commit"] and changes["modified"] == [".git/hooks/post-merge"]
    assert found == [{"path": ".git/hooks/pre-commit", "reason": "git_hooks_changed"},
                     {"path": ".git/hooks/post-merge", "reason": "git_hooks_changed"}]
    found, _ = _violations(_git_project(tmp_path / "b"), lambda r: _git(r, "config", "core.hooksPath", "/tmp/evil"))
    assert found == [{"path": ".git/config", "reason": "git_config_changed"}]
    root3 = _git_project(tmp_path / "c")
    _plant(root3, "pre-push")
    found, _ = _violations(root3, lambda r: (r / ".git" / "hooks" / "pre-push").unlink())
    assert found == [{"path": ".git/hooks/pre-push", "reason": "git_hooks_changed"}]
    # a sample the template put there, or a commit, is not a hook
    found, _ = _violations(_git_project(tmp_path / "d"), lambda r: (r / ".git" / "hooks" / "x.sample").write_text("y"))
    assert found == []
    # the same project, nothing done: nothing found
    found, _ = _violations(_git_project(tmp_path / "e"), lambda r: None)
    assert found == []


def test_the_researchos_folders_are_protected_at_any_depth(tmp_path):
    root = _project(tmp_path / "proj", {"sub/.researchos/keep.toml": "a", "deep/er/.rce/x.toml": "b",
                                        ".researchos/a/b/c/d.toml": "c", "plain.txt": "p"})

    def mutate(r):
        (r / "sub/.researchos/new.toml").write_text("n", encoding="utf-8")
        (r / "deep/er/.rce/x.toml").write_text("changed", encoding="utf-8")
        (r / ".researchos/a/b/c/d.toml").unlink()
        (r / "plain.txt").write_text("ok", encoding="utf-8")

    found, _ = _violations(root, mutate)
    assert sorted(found, key=lambda v: v["path"]) == [
        {"path": ".researchos/a/b/c/d.toml", "reason": "researchos_changed"},
        {"path": "deep/er/.rce/x.toml", "reason": "researchos_changed"},
        {"path": "sub/.researchos/new.toml", "reason": "researchos_changed"}]
    # the engine's own save of such a file, with the bytes it wrote, is allowed as before
    sha = hashlib.sha256(b"mine").hexdigest()
    found, _ = _violations(_project(tmp_path / "two", {"sub/.researchos/x.toml": "a"}),
                           lambda r: (r / "sub/.researchos/x.toml").write_bytes(b"mine"),
                           allowed={"sub/.researchos/x.toml": {sha}})
    assert found == []


def _backups_project(tmp_path: Path, names_: list[str]) -> Path:
    return _project(tmp_path, {f".researchos/backups/{n}": f"backup {n}" for n in names_} | {"a.txt": "a"})


def _backup_violations(root, mutate, **kw):
    before = snapshot.take(root)
    prot = snapshot.protected(root, before)
    mutate(root)
    after = snapshot.take(root)
    return snapshot.violations(snapshot.compare(before, after), prot, git_before=None, git_after=None, allowed={},
                               after=after, backups_before=before["backups"], backups_after=after["backups"], **kw)


def _stamp(n: int) -> str:
    return f"2026010{n}T000000000000Z"


def test_a_deleted_backup_is_out_of_bounds_and_a_new_one_is_not(tmp_path):
    names_ = [f"tasks.json.{_stamp(n)}.write.json" for n in (1, 2, 3)]
    root = _backups_project(tmp_path / "a", names_)
    picture = snapshot.take(root)
    assert picture["backups"] == {f".researchos/backups/{n}": {"size": len(f"backup {n}")} for n in names_}
    assert "map.md" not in picture["files"] and not any("/backups/" in r for r in picture["files"])
    new = ".researchos/backups/tasks.json.20260105T000000000000Z.write.json"
    # a backup added (Research OS saves) is nothing
    assert _backup_violations(root, lambda r: (r / new).write_text("n")) == []
    # one removed, with nothing newer in its series, is an out-of-bounds change, wherever it lies
    root2 = _backups_project(tmp_path / "b", names_)
    gone = ".researchos/backups/" + names_[0]
    assert _backup_violations(root2, lambda r: (r / gone).unlink()) == [{"path": gone, "reason": "backup_deleted"}]
    root3 = _backups_project(tmp_path / "c", names_)
    assert _backup_violations(root3, lambda r: shutil.rmtree(r / ".researchos/backups")) == [
        {"path": f".researchos/backups/{n}", "reason": "backup_deleted"} for n in names_]
    # at any depth, and in the legacy folder
    legacy = f"{names.LEGACY_PROJECT_DIRNAME}/backups/y.toml"
    root4 = _project(tmp_path / "d", {".researchos/backups/approved/p1/x.toml": "x", legacy: "y"})
    found = _backup_violations(root4, lambda r: [(r / ".researchos/backups/approved/p1/x.toml").unlink(),
                                                 (r / legacy).unlink()])
    assert [v["path"] for v in found] == [legacy, ".researchos/backups/approved/p1/x.toml"]
    # a file that was not there at the start, or an unchanged project, is nothing
    assert _backup_violations(_backups_project(tmp_path / "e", names_), lambda r: None) == []


def _day(n: int) -> str:
    return f"202601{n:02d}T000000000000Z"


def test_the_backup_series_length_the_check_assumes_is_what_research_os_keeps():
    from researchos.records import files as records_files
    from researchos.webapp import mapedit

    assert snapshot.BACKUP_KEEP == records_files.SNAPSHOT_KEEP == mapedit.BACKUP_KEEP == 20


def test_research_oss_own_rotation_of_a_backup_series_is_not_a_violation(tmp_path):
    # Research OS prunes a series to its newest 20 and removes nothing from a shorter one, so a rotation can only
    # have happened in a series of 20: a save adds the newest and drops the oldest
    series = [f"tasks.json.{_day(n)}.write.json" for n in range(1, 21)]
    other = [f"attempts.toml.{_stamp(n)}.toml" for n in (1, 2)]
    root = _backups_project(tmp_path / "a", series + other)
    folder = ".researchos/backups/"
    oldest, second, newest = folder + series[0], folder + series[1], folder + f"tasks.json.{_day(25)}.write.json"

    def rotate(r):
        (r / newest).write_text("n")
        (r / oldest).unlink()

    assert _backup_violations(root, rotate) == []
    # two saves, two pruned, is the same
    root2 = _backups_project(tmp_path / "b", series)

    def rotate_two(r):
        (r / newest).write_text("n")
        (r / (folder + f"tasks.json.{_day(26)}.write.json")).write_text("n")
        (r / oldest).unlink()
        (r / second).unlink()

    assert _backup_violations(root2, rotate_two) == []
    # the oldest removed with nothing added, a middle one removed beside an addition, another series, or a
    # name Research OS never gave a backup: all out of bounds
    root3 = _backups_project(tmp_path / "c", series)
    assert _backup_violations(root3, lambda r: (r / oldest).unlink()) == [{"path": oldest, "reason": "backup_deleted"}]
    root4 = _backups_project(tmp_path / "d", series)
    middle = folder + series[2]
    found = _backup_violations(root4, lambda r: ((r / newest).write_text("n"), (r / middle).unlink()))
    assert found == [{"path": middle, "reason": "backup_deleted"}]
    root5 = _backups_project(tmp_path / "e", series + other)
    gone_other = folder + other[0]
    found = _backup_violations(root5, lambda r: ((r / newest).write_text("n"), (r / gone_other).unlink()))
    assert found == [{"path": gone_other, "reason": "backup_deleted"}]
    root6 = _project(tmp_path / "f", {".researchos/backups/notes.txt": "n", "a.txt": "a"})
    found = _backup_violations(root6, lambda r: ((r / (".researchos/backups/z.20260109T000000000000Z.txt")).write_text("x"),
                                                 (r / ".researchos/backups/notes.txt").unlink()))
    assert found == [{"path": ".researchos/backups/notes.txt", "reason": "backup_deleted"}]


def test_a_planted_newer_backup_name_does_not_excuse_deleting_the_older_ones(tmp_path):
    # Research OS never prunes a series of 20 or fewer: with the series short, any deletion is the agent's,
    # however many newer names were added beside it (DESIGN 18.1 item 4)
    folder = ".researchos/backups/"
    series = [f"map.md.{_stamp(n)}.md" for n in (1, 2, 3)]
    root = _backups_project(tmp_path / "a", series)
    planted = folder + "map.md.99991231T000000000000Z.md"

    def plant_and_delete(r):
        (r / planted).write_text("x")
        for name in series:
            (r / (folder + name)).unlink()

    found = _backup_violations(root, plant_and_delete)
    assert [v["path"] for v in found] == [folder + n for n in series]
    assert {v["reason"] for v in found} == {"backup_deleted"}
    # a save of the person's or the run's own tool into a cleared folder is no different
    root2 = _backups_project(tmp_path / "b", series)
    found = _backup_violations(root2, lambda r: [(r / (folder + n)).unlink() for n in series]
                               + [(r / planted).write_text("x")])
    assert len(found) == 3
    # a series of 20 that gained one can have lost one, not three: the oldest is rotation, the next two are not
    long_series = [f"map.md.{_day(n)}.md" for n in range(1, 21)]
    root3 = _backups_project(tmp_path / "c", long_series)

    def one_save_three_deleted(r):
        (r / (folder + f"map.md.{_day(25)}.md")).write_text("n")
        for name in long_series[:3]:
            (r / (folder + name)).unlink()

    found = _backup_violations(root3, one_save_three_deleted)
    assert found == [{"path": folder + long_series[1], "reason": "backup_deleted"},
                     {"path": folder + long_series[2], "reason": "backup_deleted"}]
    # a series that never reached 20 stays out of reach however many are planted but one
    root4 = _backups_project(tmp_path / "d", long_series[:10])
    found = _backup_violations(root4, lambda r: ((r / (folder + f"map.md.{_day(25)}.md")).write_text("n"),
                                                 (r / (folder + long_series[0])).unlink()))
    assert found == [{"path": folder + long_series[0], "reason": "backup_deleted"}]


def test_a_backup_a_cut_after_walk_did_not_see_is_not_called_deleted(tmp_path, monkeypatch):
    names_ = [f"tasks.json.{_stamp(n)}.write.json" for n in (1, 2, 3)]
    root = _backups_project(tmp_path, names_)
    before = snapshot.take(root)
    monkeypatch.setattr(snapshot, "BACKUP_FILE_LIMIT", 1)
    after = snapshot.take(root)
    assert after["backups_partial"] is True and len(after["backups"]) == 1
    prot = snapshot.protected(root, before)
    found = snapshot.violations(snapshot.compare(before, after), prot, git_before=None, git_after=None, allowed={},
                                after=after, backups_before=before["backups"], backups_after=after["backups"])
    assert found == []  # everything is still on disk
    # without a root to look in, a cut walk proves nothing either
    found = snapshot.violations(snapshot.compare(before, after), {"dirs": prot["dirs"], "files": [], "data": [],
                                                                  "records": []}, git_before=None, git_after=None,
                                allowed={}, after=after, backups_before=before["backups"],
                                backups_after=after["backups"])
    assert found == []
    # a picture taken before backups were recorded says nothing about them
    old_picture = {k: v for k, v in before.items() if k not in ("backups", "backups_partial")}
    assert snapshot.violations(snapshot.compare(old_picture, before), prot, git_before=None, git_after=None,
                               allowed={}, after=before, backups_before=None) == []


# -- guard: check_tool --------------------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    root = _attempts_project(tmp_path)
    (root / "link-out").symlink_to(tmp_path)
    (root / "link-data.txt").symlink_to(root / "data" / "raw.csv")
    before = snapshot.take(root)
    prot = snapshot.protected(root, before)
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    guard.write_guard_file(run_dir, root=root, prot=prot, engine_port=8765)
    return SimpleNamespace(root=root, run_dir=run_dir, guard=guard.load_guard(run_dir), tmp=tmp_path)


def test_guard_file_round_trip(project):
    g = project.guard
    assert g["root"] == os.path.realpath(project.root) and g["engine_port"] == 8765
    assert g["dirs"] == [".researchos", names.LEGACY_PROJECT_DIRNAME, ".git", ".claude", ".codex", ".opencode"] \
        and "data/raw.csv" in g["files"]
    guard.write_guard_file(project.run_dir, root=project.root, prot={"dirs": [], "files": []}, engine_port=None)
    assert guard.load_guard(project.run_dir)["engine_port"] is None
    assert not [p for p in project.run_dir.iterdir() if p.name != "guard.json"]  # no temp file left behind


@pytest.mark.parametrize("content", ["", "{", "[]", '{"root": 5, "dirs": [], "files": []}',
                                      '{"root": "/x", "dirs": "no", "files": []}',
                                      '{"root": "/x", "dirs": [], "files": [1]}',
                                      '{"root": "/x", "dirs": [], "files": [], "engine_port": "80"}',
                                      '{"root": "", "dirs": [], "files": []}'])
def test_load_guard_rejects_bad_files(tmp_path, content):
    (tmp_path / "guard.json").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        guard.load_guard(tmp_path)


def test_load_guard_missing(tmp_path):
    with pytest.raises(OSError):
        guard.load_guard(tmp_path)


def _refused(project, tool, tool_input, cwd=None):
    return guard.check_tool(project.guard, tool, tool_input, cwd)


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_file_tools_allow_ordinary_project_files(project, tool):
    root = project.root
    for target in (root / "analysis.py", root / "data" / "new.csv", root / "figs" / "plot.png",
                   root / "research" / "notes" / "n.md", root / "deep" / "er" / "f.txt", root / "data" / "notes.txt",
                   root / "中文" / "说明.md", root / ".gitignore", root / ".github" / "ci.yml"):
        assert _refused(project, tool, {"file_path": str(target)}) is None, target
    assert _refused(project, "NotebookEdit", {"notebook_path": str(root / "nb.ipynb")}) is None
    assert _refused(project, "Write", {"file_path": "relative/new.py"}, cwd=str(root)) is None


def test_file_tools_refuse_protected_places(project):
    root = project.root
    cases = {
        "outside": str(project.tmp / "elsewhere.txt"),
        "outside parent": str(root / ".." / "x.txt"),
        "etc": "/etc/hosts",
        "root itself": str(root),
        "researchos dir": str(root / ".researchos" / "team.toml"),
        "researchos attempts": str(root / ".researchos" / "attempts.toml"),
        "rce dir": str(root / names.LEGACY_PROJECT_DIRNAME / "x"),
        "git dir": str(root / ".git" / "config"),
        "git hook": str(root / ".git" / "hooks" / "pre-commit"),
        "nested git": str(root / "vendor" / "lib" / ".git" / "hooks" / "post-commit"),
        "nested researchos": str(root / "sub" / ".researchos" / "x"),
        "case": str(root / ".RESEARCHOS" / "team.toml"),
        "data file": str(root / "data" / "raw.csv"),
        "data upper suffix": str(root / "data" / "clean.DTA"),
        "data case": str(root / "DATA" / "RAW.CSV"),
        "json data": str(root / "config.json"),
        "attempt table": str(root / "map.md"),
        "tasks": str(root / "research" / "tasks.json"),
        "dotdot into protected": str(root / "code" / ".." / "data" / "raw.csv"),
        "symlink out": str(root / "link-out" / "x.txt"),
        "symlink to data": str(root / "link-data.txt"),
    }
    for name, target in cases.items():
        reason = _refused(project, "Edit", {"file_path": target})
        assert isinstance(reason, str) and reason.endswith("."), name
    # the same targets through the other file tools
    assert _refused(project, "MultiEdit", {"file_path": cases["tasks"]})
    assert _refused(project, "Write", {"file_path": cases["data file"]})
    assert _refused(project, "NotebookEdit", {"notebook_path": str(root / ".researchos" / "x.ipynb")})


def test_file_tools_unicode_normalisation_and_new_data_files(project):
    import unicodedata

    root = project.root
    (root / "数据").mkdir()
    # protected names compare in NFC and case-folded: a decomposed spelling of the same name is the same file
    guard.write_guard_file(project.run_dir, root=root, prot={"dirs": [".researchos"], "files": ["Café.csv", "数据/表.xlsx"]},
                           engine_port=None)
    g = guard.load_guard(project.run_dir)
    nfd = unicodedata.normalize("NFD", "Café.csv")
    assert guard.check_tool(g, "Write", {"file_path": str(root / nfd)})
    assert guard.check_tool(g, "Write", {"file_path": str(root / "CAFÉ.CSV")})
    assert guard.check_tool(g, "Write", {"file_path": str(root / "数据" / "表.xlsx")})
    assert guard.check_tool(g, "Write", {"file_path": str(root / "数据" / "新.xlsx")}) is None


def test_file_tools_relative_paths_resolve_against_cwd(project):
    root = project.root
    assert _refused(project, "Write", {"file_path": "raw.csv"}, cwd=str(root / "data"))
    assert _refused(project, "Write", {"file_path": "new.csv"}, cwd=str(root / "data")) is None
    assert _refused(project, "Write", {"file_path": "../x"}, cwd=str(root))
    assert _refused(project, "Write", {"file_path": "data/raw.csv"})  # no cwd: relative to the project
    assert _refused(project, "Write", {"file_path": "raw.csv"}, cwd=str(project.tmp))  # cwd outside


def test_file_tools_without_a_usable_path_are_refused(project):
    for tool_input in ({}, {"file_path": ""}, {"file_path": "   "}, {"file_path": None}, {"file_path": 5},
                       {"file_path": ["a"]}, {"path": "x.py"}, {"file_path": "a\0b"}):
        assert _refused(project, "Edit", tool_input), tool_input
    assert _refused(project, "Write", None)
    assert _refused(project, "NotebookEdit", {})
    assert _refused(project, "NotebookEdit", {"file_path": str(project.root / "x.ipynb")}) is None  # file_path is accepted


def test_home_expansion_cannot_smuggle_a_path(project, monkeypatch):
    monkeypatch.setenv("HOME", str(project.tmp / "elsewhere-home"))
    assert _refused(project, "Write", {"file_path": "~/x.py"}, cwd=str(project.root))
    monkeypatch.setenv("HOME", str(project.root / "myhome"))
    assert _refused(project, "Write", {"file_path": "~/x.py"}, cwd=str(project.root)) is None


# -- what the agent can read, and the agent program's own settings (18.1 item 2) ---------------------------------


@pytest.fixture
def home_files(project):
    """Research OS's home with a session file and a settings file in it, as the engine leaves them."""
    home = paths.researchos_home()
    (home / "engine").mkdir(parents=True, exist_ok=True)
    (home / "engine" / "7357.session").write_text("ab" * 32, encoding="utf-8")
    (home / "settings.json").write_text("{}", encoding="utf-8")
    (project.root / "link-home").symlink_to(home)
    return home


def test_the_guard_file_lists_the_folders_an_agent_may_not_read(project, home_files, monkeypatch):
    off = project.guard["off_limits"]
    assert str(home_files) in off or os.path.realpath(home_files) in off
    assert os.path.expanduser("~/.ssh") in off and os.path.expanduser("~/.researchos") in off
    # also when the environment names another home: the default one is still the person's
    monkeypatch.setenv("HOME", str(project.tmp / "someone"))
    guard.write_guard_file(project.run_dir, root=project.root, prot={"dirs": [], "files": []}, engine_port=None)
    assert os.path.join(str(project.tmp / "someone"), ".ssh") in guard.load_guard(project.run_dir)["off_limits"]


@pytest.mark.parametrize("tool, key", [("Read", "file_path"), ("Glob", "path"), ("Grep", "path"), ("LS", "path")])
def test_reading_tools_refuse_research_os_home(project, home_files, tool, key):
    for target in (home_files / "engine" / "7357.session", home_files / "settings.json", home_files / "engine",
                   home_files, project.root / "link-home" / "engine" / "7357.session",
                   str(home_files).upper() + "/ENGINE/7357.SESSION", "~/.researchos/settings.json", "~/.ssh/id_ed25519",
                   "~/.ssh", f"~/{names.LEGACY_HOME_DIRNAME}/settings.json"):
        reason = _refused(project, tool, {key: str(target), "pattern": "*"}, cwd=str(project.root))
        assert reason and reason.endswith("."), (tool, target)
        assert "home" in reason or ".ssh" in reason
    # a relative path resolves against the call's folder
    assert _refused(project, tool, {key: "engine/7357.session"}, cwd=str(home_files))


def test_reading_tools_refuse_the_secret_however_it_is_named(project, home_files):
    session = str(home_files / "engine" / "7357.session")
    assert _refused(project, "Glob", {"pattern": session})
    assert _refused(project, "Glob", {"pattern": str(home_files) + "/**/*.session"})
    assert _refused(project, "Glob", {"pattern": "~/.ssh/*"})
    assert _refused(project, "Glob", {"pattern": "*.session", "path": str(home_files / "engine")})
    assert _refused(project, "Read", {"file_path": session + "\0.txt"})  # an embedded NUL is not a path to trust
    # a Grep that starts above the home would read into it; a Glob or LS above it only lists names
    above = str(home_files.parent)
    assert _refused(project, "Grep", {"pattern": "a", "path": above})
    assert _refused(project, "Grep", {"pattern": "a", "path": "/"})
    assert _refused(project, "Grep", {"pattern": "a"}, cwd=above)  # no folder given: where the call runs
    assert _refused(project, "Grep", {"pattern": "a", "path": "~"})
    assert _refused(project, "Glob", {"pattern": "*", "path": above}) is None
    assert _refused(project, "LS", {"path": above}) is None


def test_reading_tools_leave_the_project_and_everything_else_alone(project, home_files):
    root = project.root
    for tool, tool_input in (("Read", {"file_path": str(root / "analysis.py")}), ("Read", {"file_path": "/etc/hosts"}),
                             ("Read", {"file_path": "sub/x.py"}), ("Read", {"file_path": "~/notes.txt"}),
                             ("Read", {"file_path": str(home_files) + "-other/x"}),  # another folder with a longer name
                             ("Glob", {"pattern": "**/*.py"}), ("Glob", {"pattern": str(root) + "/**/*.csv"}),
                             ("Glob", {"pattern": "*.py", "path": str(root / "code")}),
                             ("Grep", {"pattern": "needle"}), ("Grep", {"pattern": "x", "path": str(root)}),
                             ("Grep", {"pattern": "x", "path": str(root / "data"), "glob": "*.csv"}),
                             ("LS", {"path": str(root)}), ("Read", {})):
        assert _refused(project, tool, tool_input, cwd=str(root)) is None, (tool, tool_input)
    assert _refused(project, "Read", ["x"]) and _refused(project, "Grep", None)  # not arguments: refused as the others
    # a guard written without the list (an older file) judges nothing here
    bare = {k: v for k, v in project.guard.items() if k != "off_limits"}
    assert guard.check_tool(bare, "Read", {"file_path": str(home_files / "settings.json")}) is None


def test_the_hook_runs_on_the_reading_tools(project, home_files):
    done = _hook(project.run_dir, {"tool_name": "Read", "tool_input": {"file_path": str(home_files / "engine" / "7357.session")},
                                   "cwd": str(project.root)})
    assert done.returncode == 2 and done.stderr.startswith(b"Research OS: ") and b"ab" * 32 not in done.stderr
    logged = runs.read_lines(project.run_dir / "refusals.jsonl")
    assert logged and logged[-1]["tool"] == "Read" and logged[-1]["target"].endswith("7357.session")
    done = _hook(project.run_dir, {"tool_name": "Read", "tool_input": {"file_path": str(project.root / "analysis.py")},
                                   "cwd": str(project.root)})
    assert done.returncode == 0


@pytest.mark.parametrize("command", [
    "cat $RESEARCHOS_HOME/engine/7357.session", "cat ${RESEARCHOS_HOME}/engine/*.session", "ls $RESEARCHOS_HOME",
    "echo $RCE_HOME", "env | grep RESEARCHOS_HOME", "cd \"$RESEARCHOS_HOME\" && ls",
    "cat ~/.ssh/id_ed25519", "ls ~/.ssh", "cp ~/.ssh/id_rsa /tmp/k", "cat $HOME/.ssh/config; echo", "ls -a .ssh",
    "tar c ~/.ssh|gzip", "cat '/Users/me/.ssh/id_rsa'", "ls (.ssh)",
])
def test_bash_refuses_the_home_variable_and_ssh(project, command):
    reason = _refused(project, "Bash", {"command": command}, cwd=str(project.root))
    assert reason and reason.endswith("."), command


def test_bash_refuses_the_absolute_path_of_the_home(project, home_files):
    for text in (str(home_files), os.path.realpath(home_files)):
        assert _refused(project, "Bash", {"command": f"cat {text}/engine/7357.session"}), text
        assert _refused(project, "Bash", {"command": f"ls '{text}'"}), text
        assert _refused(project, "Bash", {"command": f"cd {text.upper()};ls"}), text  # case-insensitive volumes
        assert _refused(project, "Bash", {"command": f"python3 -c \"open('{text}/settings.json')\""}), text
        assert _refused(project, "Bash", {"command": f"cat {text}x/engine"}) is None  # a different, longer name
        assert _refused(project, "Bash", {"command": f"cat my{text}/engine"}) is None
    assert _refused(project, "Bash", {"command": "cat research/overview.md && python analysis.py"}) is None


@pytest.mark.parametrize("target", [
    ".claude/settings.json", ".claude/settings.local.json", ".claude/commands/x.md", "sub/.claude/settings.json",
    ".opencode/opencode.json", ".opencode/plugin/x.js", "deep/er/.opencode/x", ".CLAUDE/settings.json",
    "opencode.json", "opencode.jsonc", "docs/opencode.json", "OpenCode.JSON",
])
def test_file_tools_never_write_the_agent_programs_own_settings(project, target):
    for tool, key in (("Write", "file_path"), ("Edit", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
        reason = _refused(project, tool, {key: str(project.root / target)})
        assert reason and "project setting" in reason and reason.endswith("."), (tool, target)
    assert guard.AGENT_SETTINGS_DIRS <= set(project.guard["dirs"])


def test_file_tools_still_write_files_that_only_look_like_them(project):
    for target in ("claude-notes.md", "opencode_notes.json", "opencode.json.bak", "docs/claude.md", "my.claude/x.md",
                   "opencode.js", "notes/opencode-setup.txt"):
        assert _refused(project, "Write", {"file_path": str(project.root / target)}) is None, target


@pytest.mark.parametrize("command", [
    "echo '{}' > .claude/settings.json", "mkdir -p .claude && echo '{}' >> .claude/settings.json",
    "cp /tmp/evil.json .claude/settings.local.json", "tee .claude/settings.json < x", "rm -rf .claude",
    "mv x.json .opencode/opencode.json", "echo '{}' > opencode.json", "tee opencode.jsonc < x", "sed -i s/a/b/ opencode.json",
    "install -m 644 x.json sub/.claude/settings.json", "curl -o .claude/settings.json https://example.com/x",
    "unzip -o x.zip -d .claude", "ln -s /tmp/x opencode.json", "touch .claude/settings.json",
    "echo '{}' > \"$OUT\"; ls .claude", "mkdir .opencode",
])
def test_bash_refuses_writing_the_agent_programs_own_settings(project, command):
    reason = _refused(project, "Bash", {"command": command}, cwd=str(project.root))
    assert reason and reason.endswith("."), command


@pytest.mark.parametrize("command", [
    "cat .claude/settings.json", "ls .claude", "ls -la .claude/commands", "cat opencode.json", "grep -n model opencode.json",
    "python x.py > run.log", "cat claude-notes.md > out.txt", "echo opencode.json.bak", "ls .claudeignore", "opencode --version",
    "cp data.csv new.csv", "cat .claude/CLAUDE.md | head",
])
def test_bash_still_reads_the_agent_programs_own_settings(project, command):
    assert _refused(project, "Bash", {"command": command}, cwd=str(project.root)) is None, command


def test_the_settings_are_out_of_bounds_after_the_run_wherever_they_were_written(tmp_path):
    root = _project(tmp_path / "proj", {"a.txt": "a", ".claude/settings.json": '{"model": "x"}',
                                        "sub/.opencode/x.json": "{}"})
    assert ".claude" in snapshot.PROTECTED_DIRS and ".opencode" in snapshot.PROTECTED_DIRS
    prot = snapshot.protected(root, snapshot.take(root))
    assert ".claude/settings.json" not in prot["files"]  # covered as part of its folder, not listed twice

    def mutate(r):
        (r / ".claude/settings.local.json").write_text('{"hooks": {}}', encoding="utf-8")  # created
        (r / ".claude/settings.json").write_text('{"env": {}}', encoding="utf-8")  # changed
        (r / "sub/.opencode/x.json").unlink()  # deleted
        (r / "opencode.json").write_text("{}", encoding="utf-8")
        (r / "deep").mkdir()
        (r / "deep/opencode.jsonc").write_text("{}", encoding="utf-8")
        (r / "notes.txt").write_text("fine", encoding="utf-8")

    found, _ = _violations(root, mutate)
    assert sorted(found, key=lambda v: v["path"]) == [
        {"path": ".claude/settings.json", "reason": "agent_settings_changed"},
        {"path": ".claude/settings.local.json", "reason": "agent_settings_changed"},
        {"path": "deep/opencode.jsonc", "reason": "agent_settings_changed"},
        {"path": "opencode.json", "reason": "agent_settings_changed"},
        {"path": "sub/.opencode/x.json", "reason": "agent_settings_changed"}]
    # the same without a protected list in `prot` (a run recorded before this existed) is judged the same
    found, _ = _violations(_project(tmp_path / "again", {"a.txt": "a"}), lambda r: (r / "opencode.json").write_text("{}"))
    assert found == [{"path": "opencode.json", "reason": "agent_settings_changed"}]
    # and an untouched project is clean
    assert _violations(_project(tmp_path / "clean", {".claude/settings.json": "{}"}), lambda r: None)[0] == []


def test_a_setting_that_is_also_a_data_file_is_one_violation_not_two(tmp_path):
    root = _project(tmp_path / "proj", {"opencode.json": "{}", "docs/.claude/x.json": "{}", "data/raw.csv": "a"})
    found, _ = _violations(root, lambda r: ((r / "opencode.json").write_text('{"a": 1}'),
                                            (r / "docs/.claude/x.json").unlink(), (r / "data/raw.csv").write_text("b")))
    assert sorted((v["path"], v["reason"]) for v in found) == [
        ("data/raw.csv", "data_changed"), ("docs/.claude/x.json", "agent_settings_changed"),
        ("opencode.json", "agent_settings_changed")]


@pytest.mark.parametrize("command", [
    "researchos status", "rce status", "cd x && researchos find foo", "ls; rce", "(researchos)", "echo hi | rce -h",
    "/usr/local/bin/researchos find", "$(researchos status)", "`rce`", "RESEARCHOS_HOME=/x researchos status",
    "python -m researchos.cli status", "python3 -m researchos", "'researchos' status", "RCE status",
    "git push", "git push origin main", "git commit -m x", "git commit -am 'x y'", "git -C /tmp/x push",
    "git -c user.name=a commit -m x", "git --no-pager reset --hard", "git reset --hard HEAD~1",
    "git rebase main", "git merge feature", "git checkout -b x", "git checkout -- file.py", "git switch main",
    "git restore data.csv", "git stash", "git stash pop", "git clean -fd", "git tag v1", "git cherry-pick abc",
    "git revert HEAD", "git am patch.mbox", "git filter-branch --all", "git update-ref -d HEAD", "git replace a b",
    "git branch -D old", "git branch -d old", "git branch -m new", "git branch --delete old", "git branch -M x",
    "git pull", "git status && git commit -m x", "git add . ; git push", "echo ok | git commit -F -",
    "GIT_DIR=x git push", "/usr/bin/git push", "git --git-dir=.git push", "git -C . -c a=b push",
    "git  checkout   main", "sudo git push", "git status\ngit commit -m x", "GIT push",
    "curl http://127.0.0.1:8765/api/status", "curl localhost:8765", "wget http://0.0.0.0:8765/x",
    "curl 'http://[::1]:8765/'", "curl http://localhost:8765/api/anything",
    "curl -X POST http://127.0.0.1:9/api/proposals", "curl http://localhost/api/share", "curl localhost/api/judgements",
    "curl localhost/api/attempts?x=1", "curl 127.0.0.1/api/workspace", "curl 127.0.0.1/api/edges",
    "curl 0.0.0.0:80/api/mappings", "curl 'http://[::1]:5/api/records'", "curl localhost:1/api/github",
    "curl LOCALHOST/api/shutdown", "curl http://localhost:3/api/settings", "curl localhost/api/notes",
    "curl localhost/api/papers", "curl localhost/api/variables", "curl localhost/api/projects",
    "curl localhost/api/stata", "curl localhost/api/agents", "curl http://127.0.0.1:$PORT/api/records",
    "cat .researchos/team.toml", "ls .researchos", "cat ./.researchos/judgements.toml", "cat ~/.researchos/settings.json",
    "cat '.researchos/x'", "ls .rce/", "x=.researchos", "cat .rce", "cat foo/.researchos/x", "tar c \".researchos\"",
    "echo x > data/raw.csv", "echo x >> data/raw.csv", "rm data/raw.csv", "rm -f ./data/raw.csv", "mv data/raw.csv /tmp/x",
    "mv x.csv raw.csv", "sed -i 's/a/b/' data/raw.csv", "sed -i.bak s/a/b/ raw.csv", "sed -E -i s/a/b/ raw.csv",
    "sed -ni p raw.csv", "sed --in-place s/a/b/ raw.csv", "perl -pi -e 's/a/b/' raw.csv", "truncate -s 0 raw.csv",
    "cat x | tee data/raw.csv", "dd if=/dev/zero of=raw.csv", "echo '' > tasks.json", "echo > research/tasks.json",
    "echo x > map.md", "rm map.md", "cp new.csv raw.csv", "cp -f new.csv data/raw.csv", "install x.csv raw.csv",
    "rsync -a new/ raw.csv", "ln -sf other.csv raw.csv", "sudo cp a.csv raw.csv", "cat a > 'clean.DTA'",
    "OUT=raw.csv; echo x > $OUT", "python x.py raw.csv > \"$(echo y)\"", "echo x >(tee raw.csv)",
    "echo x > .git/HEAD", "rm -rf .git", "cat x | tee .git/hooks/pre-commit",
])
def test_bash_refusals(project, command):
    reason = _refused(project, "Bash", {"command": command})
    assert reason and reason.endswith("."), command


@pytest.mark.parametrize("command", [
    "python analysis.py", "python3 -c 'print(1)'", "Rscript fit.R", "git status", "git diff", "git diff --stat HEAD",
    "git log --oneline -5", "git show HEAD:file", "git branch", "git branch -a", "git branch --list", "git remote -v",
    "git blame x.py", "git ls-files", "git rev-parse HEAD", "git add -A", "git config user.name",
    "cat data.csv | head", "head -5 data/raw.csv", "wc -l raw.csv", "cp data.csv new.csv", "cp raw.csv /tmp/copy.csv",
    "cp -r data backup", "cp raw.csv raw-copy.csv", "rsync -a data/ backup/", "ln -s raw.csv alias.csv",
    "grep -c x raw.csv 2>/dev/null", "python x.py raw.csv 2>&1", "python x.py raw.csv > run.log", "ls -la data",
    "cat raw.csv > /dev/null", "python x.py >/dev/null 2>&1", "echo done", "pip install pandas statsmodels",
    "uv pip install scikit-learn", "curl -O https://example.com/data/file.csv", "curl https://api.example.org/v1/papers",
    "curl http://localhost:3000/", "curl http://127.0.0.1:9999/x", "wget https://example.com/api.json",
    "mkdir -p out && cd out && python ../x.py", "sed -n '1,5p' raw.csv", "sed s/a/b/ raw.csv", "awk '{print $1}' raw.csv",
    "rm tmp.txt", "mv a.txt b.txt", "echo x > out.txt", "tee out.txt < in.txt", "echo $(date) >> log.txt",
    "stata -b do clean.do", "python -m pip install -U pip", "python -m pytest -q", "python -m venv .venv",
    "echo research-os", "ls research", "cat research/overview.md", "cd /home/user/research-os && ls",
    "echo researchos_complete_task", "grep -rn digit .", "cat .gitignore", "cat .github/workflows/ci.yml",
    "git clone https://example.com/x.git", "echo 'github.com/x/y.git'", "ls .git", "cat .git/HEAD", "git-lfs version",
    "awk '$1>5' raw.csv", "python x.py raw.csv > \"out file.txt\"", "python x.py raw.csv 2> err.log >> run.log",
    "mv notes.md notes-old.md", "dd if=in.bin of=out.bin", "perl -e 'print 1'", "sed -e p raw.csv",
])
def test_bash_allowed_commands(project, command):
    assert _refused(project, "Bash", {"command": command}) is None, command


def test_bash_engine_port_depends_on_the_guard(project):
    assert _refused(project, "Bash", {"command": "curl 127.0.0.1:8765"})
    assert _refused(project, "Bash", {"command": "curl 127.0.0.1:87650"}) is None  # \b: a different port
    guard.write_guard_file(project.run_dir, root=project.root, prot={"dirs": [], "files": []}, engine_port=None)
    g = guard.load_guard(project.run_dir)
    assert guard.check_tool(g, "Bash", {"command": "curl 127.0.0.1:8765"}) is None
    assert guard.check_tool(g, "Bash", {"command": "curl 127.0.0.1:1/api/proposals"})  # the API path always counts


def test_bash_protected_name_matching_is_whole_word(project):
    assert _refused(project, "Bash", {"command": "echo x > subraw.csv"}) is None
    assert _refused(project, "Bash", {"command": "echo x > raw.csv.bak"}) is None
    assert _refused(project, "Bash", {"command": "echo x > my-raw.csv"}) is None
    assert _refused(project, "Bash", {"command": "echo x > raw.csv"})
    assert _refused(project, "Bash", {"command": "echo x > ./data/raw.csv"})
    assert _refused(project, "Bash", {"command": "echo x>raw.csv"})
    assert _refused(project, "Bash", {"command": "echo x > 'RAW.CSV'"})
    assert _refused(project, "Bash", {"command": "echo x > a/b/tasks.json"})


def test_bash_protected_names_with_spaces_and_unicode(project):
    guard.write_guard_file(project.run_dir, root=project.root, prot={
        "dirs": [], "files": ["my data (1).csv", "结果.xlsx", "x/Plain.csv"]}, engine_port=None)
    g = guard.load_guard(project.run_dir)
    assert guard.check_tool(g, "Bash", {"command": "rm 'my data (1).csv'"})
    assert guard.check_tool(g, "Bash", {"command": "echo 1 > 结果.xlsx"})
    assert guard.check_tool(g, "Bash", {"command": "echo 1 > \"my data (1).csv\""})
    assert guard.check_tool(g, "Bash", {"command": "echo 1 > 'x/Plain.csv'"})
    assert guard.check_tool(g, "Bash", {"command": "cat 'my data (1).csv' > \"copy of it.csv\""}) is None
    assert guard.check_tool(g, "Bash", {"command": "echo 1 > ./plain.CSV"})
    assert guard.check_tool(g, "Bash", {"command": "cat 结果.xlsx"}) is None
    assert guard.check_tool(g, "Bash", {"command": "cp 结果.xlsx copy.xlsx"}) is None
    assert guard.check_tool(g, "Bash", {"command": "cp copy.xlsx 结果.xlsx"})


def test_bash_with_missing_or_odd_command(project):
    assert _refused(project, "Bash", {}) is None  # no command: nothing to check (contract)
    assert _refused(project, "Bash", {"command": ""}) is None
    assert _refused(project, "Bash", {"command": ["git", "push"]})
    assert _refused(project, "Bash", {"command": 5})
    assert _refused(project, "Bash", ["git push"])


def test_other_tools_are_not_judged(project):
    for tool in ("Read", "Grep", "Glob", "WebFetch", "mcp__researchos__researchos_ask", "Task", "Unknown"):
        assert _refused(project, tool, {"file_path": str(project.root / ".researchos" / "x")}) is None


def test_pathological_commands_are_fast(project):
    import time

    started = time.monotonic()
    for command in ("git " + "-a " * 3000 + "status", "sed " + "-x " * 3000 + "file", "x" * 200_000,
                    "echo " + "git " * 5000, "perl " + "-q " * 3000, "cp " + "a " * 5000):
        _refused(project, "Bash", {"command": command})
    assert time.monotonic() - started < 5


def test_log_refusal(tmp_path):
    guard.log_refusal(tmp_path, "Bash", "git push " + "x" * 2000, "no pushing.")
    guard.log_refusal(tmp_path, "Edit", "/a/b", "outside.")
    records = runs.read_lines(tmp_path / "refusals.jsonl")
    assert [r["tool"] for r in records] == ["Bash", "Edit"]
    assert len(records[0]["target"]) == 500 and records[1]["reason"] == "outside."
    assert records[0]["at"].endswith("Z")


# -- the hook, as Claude Code runs it ------------------------------------------------------------------------


def _hook(run_dir, payload, *, raw: bytes | None = None, args=None):
    stdin = raw if raw is not None else json.dumps(payload).encode("utf-8")
    return subprocess.run([sys.executable, "-m", "researchos.agents.hook", *(args if args is not None else [str(run_dir)])],
                          input=stdin, capture_output=True, timeout=60)


def test_hook_allows_silently(project):
    done = _hook(project.run_dir, {"tool_name": "Bash", "tool_input": {"command": "python analysis.py"},
                                   "cwd": str(project.root), "hook_event_name": "PreToolUse"})
    assert (done.returncode, done.stdout, done.stderr) == (0, b"", b"")
    done = _hook(project.run_dir, {"tool_name": "Write", "tool_input": {"file_path": str(project.root / "new.py")}})
    assert done.returncode == 0 and done.stderr == b""
    done = _hook(project.run_dir, {"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}})
    assert done.returncode == 0
    assert not (project.run_dir / "refusals.jsonl").exists()


def test_hook_refuses_with_exit_2_message_and_log(project):
    done = _hook(project.run_dir, {"tool_name": "Bash", "tool_input": {"command": "git push origin main"},
                                   "cwd": str(project.root)})
    assert done.returncode == 2 and done.stdout == b""
    message = done.stderr.decode("utf-8")
    assert message.startswith("Research OS: ") and "git" in message and message.endswith("\n")
    done = _hook(project.run_dir, {"tool_name": "Edit", "tool_input": {"file_path": str(project.root / "data/raw.csv"),
                                                                        "old_string": "a", "new_string": "b"}})
    assert done.returncode == 2 and b"protected" in done.stderr
    done = _hook(project.run_dir, {"tool_name": "Write", "tool_input": {"file_path": "../escape.txt"},
                                   "cwd": str(project.root)})
    assert done.returncode == 2
    logged = runs.read_lines(project.run_dir / "refusals.jsonl")
    assert [r["tool"] for r in logged] == ["Bash", "Edit", "Write"]
    assert logged[0]["target"] == "git push origin main" and logged[1]["target"].endswith("data/raw.csv")
    assert all(r["reason"] and r["at"] for r in logged)


def test_hook_refuses_with_non_ascii_message_under_the_c_locale(project):
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONIOENCODING": "ascii"}
    target = str(project.root / "数据" / ".." / ".." / "出去.txt")
    done = subprocess.run([sys.executable, "-m", "researchos.agents.hook", str(project.run_dir)], env=env,
                          input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": target}}).encode("utf-8"),
                          capture_output=True, timeout=60)
    assert done.returncode == 2 and "出去.txt".encode("utf-8") in done.stderr


@pytest.mark.parametrize("raw", [b"", b"not json", b"\xff\xfe\x00", b"[]", b"null", b"5", b'{"tool_input": {}}',
                                 b'{"tool_name": 5}', b'{"tool_name": "Bash", "tool_input": "git push"}',
                                 b'{"tool_name": "Bash", "tool_input": ["x"]}', b'{"tool_name": "Edit"}'])
def test_hook_fails_closed_on_garbage(project, raw):
    done = _hook(project.run_dir, None, raw=raw)
    assert done.returncode == 2, raw
    assert done.stderr.startswith(b"Research OS: ") and done.stdout == b""


def test_hook_fails_closed_without_guard_or_run_dir(tmp_path, project):
    empty = tmp_path / "empty-run"
    empty.mkdir()
    ok_call = {"tool_name": "Bash", "tool_input": {"command": "python x.py"}}
    done = _hook(empty, ok_call)
    assert done.returncode == 2 and b"could not check" in done.stderr
    assert runs.read_lines(empty / "refusals.jsonl")  # the failure is logged too
    assert _hook(tmp_path / "does-not-exist", ok_call).returncode == 2
    assert _hook(None, ok_call, args=[]).returncode == 2
    (project.run_dir / "guard.json").write_text("{broken", encoding="utf-8")
    assert _hook(project.run_dir, ok_call).returncode == 2
    (project.run_dir / "guard.json").unlink()
    assert _hook(project.run_dir, ok_call).returncode == 2


def test_hook_refuses_oversized_input(project):
    big = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(project.root / "x.txt"),
                                                            "content": "a" * (hook.INPUT_LIMIT + 10)}}).encode("utf-8")
    done = _hook(project.run_dir, None, raw=big)
    assert done.returncode == 2 and b"10 MB" in done.stderr


def test_hook_main_in_process(project, monkeypatch, capsysbinary):
    import io

    def run(payload):
        monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode("utf-8"))))
        return hook.main([str(project.run_dir)])

    assert run({"tool_name": "Bash", "tool_input": {"command": "ls"}}) == 0
    assert run({"tool_name": "Bash", "tool_input": {"command": "researchos status"}}) == 2
    assert capsysbinary.readouterr().err.startswith(b"Research OS: ")


def test_hook_imports_stay_light():
    code = ("import sys\nimport researchos.agents.hook\n"
            "bad = [m for m in ('sqlite3', 'researchos.db', 'researchos.records', 'researchos.paths', 'researchos.settings',"
            " 'researchos.workspace', 'mcp') if m in sys.modules]\nsys.exit(1 if bad else 0)\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_hook_command_string_round_trips_through_shlex(project):
    command = shlex.join([sys.executable, "-m", "researchos.agents.hook", str(project.run_dir)])
    done = subprocess.run(shlex.split(command), input=b'{"tool_name": "Bash", "tool_input": {"command": "ls"}}',
                          capture_output=True, timeout=60)
    assert done.returncode == 0


def test_labels_contract_used_by_this_package():
    agent = labels.Agent("claude-code", "sonnet")
    assert agent.owner == "agent:claude-code:sonnet" and agent.label == "Claude Code · sonnet"
    assert labels.parse_owner(agent.owner) == agent


# -- review fixes: snapshot ------------------------------------------------------------------------------------


def test_skip_files_are_never_recorded_or_protected(tmp_path):
    root = _project(tmp_path / "proj", {
        "a.csv": "1", ".DS_Store": "x", "sub/.DS_Store": "x", "Thumbs.db": "x", "desktop.ini": "x", "Icon\r": "x",
        ".Rhistory": "x", "~$report.xlsx": "x", "data/~$raw.csv": "x", ".~lock.table.ods#": "x",
        "~WRL0001.tmp": "x", "keep.txt": "k", "~tilde.txt": "t"})
    picture = snapshot.take(root)
    assert set(picture["files"]) == {"a.csv", "keep.txt", "~tilde.txt"}
    assert set(picture["data"]) == {"a.csv"}  # the data walk skips them too: `~$raw.csv` is not data
    assert snapshot.protected(root, picture)["data"] == ["a.csv"]
    before = snapshot.take(root)
    (root / ".DS_Store").unlink()
    (root / "new" / ".DS_Store").parent.mkdir()
    (root / "new" / ".DS_Store").write_text("y", encoding="utf-8")
    (root / "~$other.docx").write_text("y", encoding="utf-8")
    changes = snapshot.compare(before, snapshot.take(root))
    assert changes == {"created": [], "modified": [], "deleted": []}
    assert snapshot.skip_file("THUMBS.DB") and snapshot.skip_file(".~lock.x.docx#") and not snapshot.skip_file("lock.csv")


def test_data_walk_protects_files_beyond_the_file_limit_and_in_skipped_folders(tmp_path, monkeypatch):
    files = {f"a{i}.txt": "x" for i in range(4)} | {"z/late.csv": "1", "_cache/panel.parquet": "p",
                                                    ".cache/x.csv": "c", "data/raw.csv": "r", "venv/lib/v.csv": "v",
                                                    "node_modules/m/n.json": "n", ".venv/c.csv": "c", ".git/g.json": "g",
                                                    "sub/__pycache__/p.csv": "p", "deep/er/est/d.TSV": "t"}
    root = _project(tmp_path / "proj", files)
    monkeypatch.setattr(snapshot, "FILE_LIMIT", 3)
    picture = snapshot.take(root)
    assert picture["partial"] is True and len(picture["files"]) == 3
    assert set(picture["data"]) == {"z/late.csv", "_cache/panel.parquet", ".cache/x.csv", "data/raw.csv", "deep/er/est/d.TSV"}
    assert picture["data_partial"] is False
    entry = picture["data"]["z/late.csv"]
    assert entry["size"] == 1 and isinstance(entry["mtime_ns"], int) and "sha256" not in entry
    prot = snapshot.protected(root, picture)
    assert prot["data"] == sorted(picture["data"]) and set(prot["files"]) >= set(prot["data"])
    # the hook now refuses what the cut picture alone would have left unprotected
    guard_file = tmp_path / "rd"
    guard_file.mkdir()
    guard.write_guard_file(guard_file, root=root, prot=prot, engine_port=None)
    g = guard.load_guard(guard_file)
    for rel in ("z/late.csv", "_cache/panel.parquet", ".cache/x.csv"):
        assert guard.check_tool(g, "Write", {"file_path": str(root / rel)}), rel
        assert guard.check_tool(g, "Bash", {"command": f"echo x > {rel}"}), rel
    assert guard.check_tool(g, "Write", {"file_path": str(root / "z" / "new.csv")}) is None


def test_data_walk_never_opens_files_and_is_json(tmp_path, monkeypatch):
    root = _project(tmp_path / "proj", {"a.csv": "1", "gone.csv": "2"})
    os.symlink(root / "a.csv", root / "link.csv")
    real_open = open
    opened = []

    def spy(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    monkeypatch.setattr(snapshot, "_hash_and_copy", lambda *a, **k: (None, False, 0))  # the main walk reads nothing either
    monkeypatch.setattr("builtins.open", spy)
    monkeypatch.setattr(paths, "is_dataless", lambda p: str(p).endswith("gone.csv"))
    picture = snapshot.take(root)
    monkeypatch.undo()
    assert not [o for o in opened if o.endswith(".csv")]
    assert picture["data"]["gone.csv"]["dataless"] is True and "dataless" not in picture["data"]["a.csv"]
    assert picture["data"]["link.csv"]["link"] == str(root / "a.csv")
    json.dumps(picture)


def test_data_walk_cap_marks_partial(tmp_path, monkeypatch):
    root = _project(tmp_path / "proj", {f"d{i}.csv": "x" for i in range(5)})
    monkeypatch.setattr(snapshot, "DATA_FILE_LIMIT", 3)
    picture = snapshot.take(root)
    assert picture["data_partial"] is True and len(picture["data"]) == 3


def test_data_entries_take_the_hash_of_the_main_walk(tmp_path):
    import hashlib

    root = _project(tmp_path / "proj", {"a.csv": "abc"})
    entry = snapshot.take(root)["data"]["a.csv"]
    assert entry["sha256"] == hashlib.sha256(b"abc").hexdigest()


def test_protected_without_a_data_walk_falls_back_to_files_and_leaves_out_researchos_json(tmp_path):
    root = _project(tmp_path / "proj", {"a.csv": "1", ".researchos/s.json": "{}", "b.json": "{}"})
    picture = snapshot.take(root)
    assert snapshot.protected(root, picture)["data"] == ["a.csv", "b.json"]
    old_shape = {k: v for k, v in picture.items() if k not in ("data", "data_partial")}
    assert snapshot.protected(root, old_shape)["data"] == ["a.csv", "b.json"]


def _data_violations(root, mutate, *, allowed=None, limit=None, monkeypatch=None):
    if limit is not None:
        monkeypatch.setattr(snapshot, "FILE_LIMIT", limit)
    before = snapshot.take(root)
    prot = snapshot.protected(root, before)
    mutate(root)
    after = snapshot.take(root)
    changes = snapshot.compare(before, after)
    return snapshot.violations(changes, prot, git_before=None, git_after=None, allowed=allowed or {}, after=after,
                               data_before=before["data"], data_after=after["data"]), changes


def test_violations_see_data_files_beyond_the_file_limit(tmp_path, monkeypatch):
    files = {f"a{i}.txt": "x" for i in range(4)} | {"z/late.csv": "1", "z/gone.csv": "2", "z/touched.csv": "3",
                                                    "_cache/p.parquet": "p"}
    root = _project(tmp_path / "proj", files)

    def mutate(r):
        (r / "z/late.csv").write_text("changed!", encoding="utf-8")
        (r / "z/gone.csv").unlink()
        os.utime(r / "z/touched.csv", ns=(5, 5))                       # touched, content is the same: sha unknown, mtime differs
        (r / "_cache/p.parquet").write_bytes(b"longer than before")
        (r / "z/new.csv").write_text("new", encoding="utf-8")           # new data file: fine

    found, changes = _data_violations(root, mutate, limit=3, monkeypatch=monkeypatch)
    assert not {"z/late.csv", "z/gone.csv"} & set(changes["modified"] + changes["deleted"])  # the cut picture never saw them
    reasons = {(v["path"], v["reason"]) for v in found}
    assert ("z/late.csv", "data_changed") in reasons and ("z/gone.csv", "data_deleted") in reasons
    assert ("_cache/p.parquet", "data_changed") in reasons
    assert ("z/new.csv", "data_changed") not in reasons and len(found) == len({(v["path"], v["reason"]) for v in found})


def test_violations_data_walk_untouched_and_main_hash_wins(tmp_path):
    root = _project(tmp_path / "proj", {"a.csv": "same", "b.csv": "1"})
    found, _ = _data_violations(root, lambda r: os.utime(r / "a.csv", ns=(5, 5)))
    assert found == []  # both walks saw it and the hash is the same: touching is not changing
    found, _ = _data_violations(root, lambda r: (r / "b.csv").write_text("2", encoding="utf-8"))
    assert found == [{"path": "b.csv", "reason": "data_changed"}]  # once, though both comparisons see it


def test_violations_deleted_data_file_with_a_cut_after_picture_needs_it_really_gone(tmp_path, monkeypatch):
    root = _project(tmp_path / "proj", {"a.csv": "1", "b.csv": "2"})
    before = snapshot.take(root)
    prot = snapshot.protected(root, before)
    monkeypatch.setattr(snapshot, "DATA_FILE_LIMIT", 1)
    after = snapshot.take(root)
    assert after["data_partial"] is True and len(after["data"]) == 1
    missing = next(r for r in ("a.csv", "b.csv") if r not in after["data"])
    found = snapshot.violations(snapshot.compare(before, after), prot, git_before=None, git_after=None, allowed={},
                                after=after, data_before=before["data"], data_after=after["data"])
    assert all(v["path"] != missing for v in found)  # it is still there, only the walk stopped short
    (root / missing).unlink()
    found = snapshot.violations({"created": [], "modified": [], "deleted": []}, prot, git_before=None, git_after=None,
                                allowed={}, after=after, data_before=before["data"], data_after=after["data"])
    assert {"path": missing, "reason": "data_deleted"} in found


def test_violations_allowed_applies_to_every_protected_kind(tmp_path):
    import hashlib

    root = _attempts_project(tmp_path)
    mine = b"a\n42\n"
    mine_sha = hashlib.sha256(mine).hexdigest()
    other = hashlib.sha256(b"something else").hexdigest()

    def mutate(r):
        (r / "data/raw.csv").write_bytes(mine)                            # data: the person saved this
        (r / "map.md").write_bytes(mine)                                  # a record
        (r / ".researchos/attempts.toml").write_bytes(mine)               # a .researchos path, modified
        (r / ".researchos/new.toml").write_bytes(mine)                    # ... and created
        (r / "data/clean.DTA").write_bytes(mine)

    allowed = {"data/raw.csv": {mine_sha}, "map.md": {mine_sha}, ".researchos/attempts.toml": {mine_sha},
               ".researchos/new.toml": {mine_sha}, "data/clean.DTA": {other}}
    found, _ = _violations(root, mutate, allowed=allowed)
    assert found == [{"path": "data/clean.DTA", "reason": "data_changed"}]  # its final content is not the allowed one
    # an allowed hash never excuses a deletion
    root2 = _attempts_project(tmp_path / "two")
    found, _ = _violations(root2, lambda r: (r / "data/raw.csv").unlink(), allowed={"data/raw.csv": {mine_sha}})
    assert found == [{"path": "data/raw.csv", "reason": "data_deleted"}]
    root3 = _attempts_project(tmp_path / "three")
    found, _ = _violations(root3, lambda r: (r / ".researchos/attempts.toml").unlink(),
                           allowed={".researchos/attempts.toml": {mine_sha}})
    assert found == [{"path": ".researchos/attempts.toml", "reason": "researchos_changed"}]


def test_created_since(tmp_path):
    root = _project(tmp_path / "proj", {"old.txt": "o", "new.txt": "n", "sub/new2.txt": "n"})
    os.utime(root / "old.txt", ns=(1_000, 1_000))
    started = 2_000_000_000_000_000_000  # far in the future for mtime; ctime of a fresh file is "now", also earlier
    assert snapshot.created_since(root, ["old.txt", "new.txt", "sub/new2.txt", "missing.txt", "../x", "/etc/hosts", ""], 0) \
        == ["old.txt", "new.txt", "sub/new2.txt"]
    os.utime(root / "new.txt", ns=(started, started))
    assert snapshot.created_since(root, ["old.txt", "new.txt", "sub/new2.txt"], started) == ["new.txt"]
    # a file whose mtime is old but which was changed (ctime) after the start counts: touch -d / cp -p keep old mtimes
    now = os.lstat(root / "sub/new2.txt").st_ctime_ns
    os.utime(root / "sub/new2.txt", ns=(1_000, 1_000))
    assert snapshot.created_since(root, ["sub/new2.txt"], now - 1_000_000_000) == ["sub/new2.txt"]
    os.symlink(root / "nothing", root / "dangling")
    assert snapshot.created_since(root, ["dangling"], 0) == ["dangling"]  # a link is judged by its own times


# -- review fixes: git refs --------------------------------------------------------------------------------------


def _dirty_then_stash(root):
    (root / "b.py").write_text("dirty", encoding="utf-8")
    _git(root, "stash")


def _git_refs_project(tmp_path):
    root = _git_project(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-q", "-u", "origin", "main")
    return root


@pytest.mark.parametrize("name,mutate", [
    ("push to a new remote branch", lambda r: _git(r, "push", "-q", "origin", "main:side")),
    ("tag", lambda r: _git(r, "tag", "v1")),
    ("annotated tag", lambda r: _git(r, "tag", "-a", "v2", "-m", "x")),
    ("branch -f", lambda r: _git(r, "branch", "-f", "other", "HEAD")),
    ("stash", lambda r: _dirty_then_stash(r)),
    ("update-ref", lambda r: _git(r, "update-ref", "refs/heads/zzz", "HEAD")),
])
def test_git_refs_fingerprint_catches_what_head_and_branch_do_not(tmp_path, name, mutate):
    root = _git_refs_project(tmp_path)
    before = snapshot.git_state(root)
    mutate(root)
    after = snapshot.git_state(root)
    assert (after["head"], after["branch"]) == (before["head"], before["branch"]), name
    assert after["refs"] != before["refs"], name
    found, _ = _violations(_git_refs_project(tmp_path / "again"), mutate)
    assert found == [{"path": ".git", "reason": "git_changed"}], name


def test_git_refs_fingerprint_is_stable_and_old_states_compare_without_it(tmp_path):
    root = _git_refs_project(tmp_path)
    assert snapshot.git_state(root) == snapshot.git_state(root)
    (root / "b.py").write_text("edited but not committed", encoding="utf-8")
    _git(root, "add", "-A")  # the index is not a ref
    assert snapshot.git_state(root)["refs"] == snapshot.git_state(root)["refs"]
    state = snapshot.git_state(root)
    assert snapshot.git_equal(state, dict(state))
    assert snapshot.git_equal({"head": state["head"], "branch": state["branch"]}, state)  # recorded before `refs` existed
    assert not snapshot.git_equal(state, {**state, "branch": "x"}) and not snapshot.git_equal(state, None)
    assert snapshot.git_equal(None, None) and not snapshot.git_equal(None, state)


# -- review fixes: the guard, Bash read path-aware -------------------------------------------------------------------


@pytest.fixture
def pa(tmp_path):
    """A project for the path-aware Bash check: protected data/raw.csv, data/clean.DTA, config.json, map.md and
    research/tasks.json; an unprotected data/notes.txt, new/raw.csv and archives."""
    import tarfile
    import zipfile

    root = _attempts_project(tmp_path)
    prot = snapshot.protected(root, snapshot.take(root))  # taken before the files below exist: they are not protected
    _project(root, {"new/raw.csv": "n", "new/other.csv": "o", "code/run.py": "1", "research/overview.md": "o",
                    "research/notes/a.md": "a", "fresh/keep.txt": "k"})
    (root / "link-data.txt").symlink_to(root / "data" / "raw.csv")
    with zipfile.ZipFile(root / "clash.zip", "w") as archive:
        archive.writestr("raw.csv", "1")
    with zipfile.ZipFile(root / "nested.zip", "w") as archive:
        archive.writestr("data/clean.DTA", "1")
    with zipfile.ZipFile(root / "fine.zip", "w") as archive:
        archive.writestr("fresh.csv", "1")
    with tarfile.open(root / "clash.tar", "w") as archive:
        archive.add(root / "new/raw.csv", arcname="raw.csv")
    with tarfile.open(root / "fine.tar.gz", "w:gz") as archive:
        archive.add(root / "new/other.csv", arcname="other.csv")
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    guard.write_guard_file(run_dir, root=root, prot=prot, engine_port=8765)
    return SimpleNamespace(root=root, guard=guard.load_guard(run_dir), tmp=tmp_path)


#: Commands that remove, move or overwrite a protected file or a folder holding one, or put a same-named file
#: into such a folder (review finding 1). The working folder is the project root unless the command moves it.
PATH_REFUSALS = [
    # remove
    "rm -rf data", "rm -r data/", "rm -rf ./data", "rm data/*.csv", "rm -rf data/*", "rm -rf d*", "rm -rf ./*",
    "rm -rf .", "rm -rf ..", "rm -rf research", "rm -rf -- data", "sudo rm -rf data", "sudo -u root rm -rf data",
    "env FOO=1 rm -rf data", "nohup rm -rf data", "time -p rm -rf data", "(cd data && rm -rf .)", "cd data && rm -rf *",
    "cd data; rm -rf ../data", "bash -c 'rm -rf data'", "sh -c \"cd data && rm -f ./*\"", "eval 'rm -rf data'",
    "rm -rf link-out/../data", "shred data/raw.csv", "unlink config.json", "$(rm -rf data)",
    "find data -name '*.csv' -delete", "find . -name '*.json' -exec rm {} \\;", "find data -delete",
    "find . -iname 'RAW.CSV' -delete", "find data ! -name '*.txt' -delete", "find . -path './data/*' -delete",
    "find data -type f -exec rm -f {} +", "find data -name 'raw*' -execdir rm {} +",
    "git rm -r data", "git rm data/raw.csv", "git -C data rm raw.csv", "git --no-pager rm -rf research",
    # move
    "mv data data_old", "mv research research_old", "mv research/tasks.json /tmp/", "mv config.json /tmp/x",
    "mv data/ /tmp/", "mv -t /tmp data", "mv a.txt data/raw.csv", "git mv research x", "git mv data x",
    # copy / move / download into a folder that holds a protected file of the same name
    "mv new/raw.csv data/", "cp ~/Downloads/raw.csv data/", "cp /tmp/new/raw.csv ./data/", "cp new/raw.csv data",
    "cd data && cp ../new/raw.csv .", "cd data; mv ../new/raw.csv .", "cp /tmp/tasks.json research/",
    "cp -t data new/raw.csv", "cp --target-directory=data new/raw.csv", "cp -r new/. data", "cp new/*.csv data/",
    "cp -r new data/ && cp /tmp/clean.DTA data/", "install new/raw.csv data/", "install -m 644 new/raw.csv data/",
    "rsync -a new/ data/", "rsync -a --delete fresh/ data/", "rsync -av new/raw.csv data/", "ditto new/raw.csv data/",
    "ln -sf /tmp/x data/raw.csv", "ln -s /tmp/raw.csv data/", "cd data && ln -s /tmp/raw.csv",
    "cp x.csv link-data.txt", "git -C data status && rm -rf data",
    "curl -o data/raw.csv http://x", "curl -sLo data/raw.csv http://x", "curl http://x --output data/raw.csv",
    "curl --output=data/raw.csv http://x", "curl -O --output-dir data https://x.org/files/raw.csv",
    "cd data && curl -sLO https://x.org/files/raw.csv", "curl -o config.json http://x",
    "wget -O data/raw.csv http://x", "wget -qO data/raw.csv http://x", "wget --output-document=config.json http://x",
    "wget -P data http://x/raw.csv", "wget --directory-prefix=data http://x/raw.csv?x=1",
    # extract
    "unzip -o clash.zip -d data", "unzip clash.zip -d data", "unzip -o nested.zip", "unzip -o notthere.zip -d data",
    "unzip -oj nested.zip -d data", "cd data && unzip -o ../clash.zip", "tar xf clash.tar -C data",
    "tar -xvf clash.tar --directory=data", "tar xf missing.tar -C data", "tar -x -f clash.tar -C data",
]

PATH_ALLOWED = [
    # reading, and copies that leave the protected files alone
    "cat data/raw.csv", "head -5 data/raw.csv", "python x.py data/raw.csv", "python x.py data/raw.csv > out.log",
    "cp data/raw.csv new.csv", "cp data/raw.csv data/raw2.csv", "cp data/raw.csv /tmp/", "cp -r data backup",
    "cp -r data/. backup", "rsync -a data/ backup/", "cp new/other.csv data/", "cp new/*.txt data/",
    "install -d out", "ln -s data/raw.csv alias.csv", "ln -s ../data/raw.csv fresh/alias.csv", "mv new/other.csv data/",
    "cd new && cp raw.csv /tmp/x", "cd code && rm run.py", "cd data && cp raw.csv new.csv", "cp -t fresh new/raw.csv",
    # removing or moving other things
    "rm -rf build dist", "rm code/run.py", "rm data/notes.txt", "rm data/*.txt", "rm *.pyc", "rm -rf research/notes",
    "rm -rf fresh", "rm -rf new", "rm -rf code/*", "mv research/overview.md research/o2.md", "mv a b", "mv new fresh",
    "rm link-data.txt", "mv link-data.txt other.csv", "rm -rf $BUILD", "rm -rf \"$(pwd)/build\"",
    "find . -name '*.pyc' -delete", "find data -name '*.txt' -delete", "find data -type d -delete",
    "find data -name '*.csv'", "find data -name '*.csv' -exec wc -l {} \\;", "find . -name '*.pyc' -exec rm {} +",
    "find code -delete", "find nowhere -delete", "git rm --cached code/run.py", "git rm code/run.py",
    "git mv code/run.py code/run2.py", "git status", "git -C data status", "git log -1",
    # downloads and extractions that land elsewhere or under other names
    "curl -o out.csv http://x", "curl -O https://x.org/files/fresh.csv", "curl -sL https://x.org/a -o fresh/a.csv",
    "wget http://x/raw.csv", "wget -P /tmp http://x/raw.csv", "wget -P fresh http://x/raw.csv", "wget -O out.csv http://x",
    "wget -O - http://x | head", "curl https://zenodo.org/api/records/12/files/data.zip -o data.zip",
    "unzip fine.zip -d data", "unzip fine.zip", "unzip later.zip", "tar xzf later.tgz", "unzip -l clash.zip", "unzip -n clash.zip -d data", "unzip -t clash.zip",
    "unzip clash.zip -d fresh", "unzip -p clash.zip", "tar tf clash.tar", "tar xf clash.tar -C fresh",
    "tar xf fine.tar.gz -C data", "tar xzf fine.tar.gz", "tar czf out.tgz data", "tar -cf out.tar data/raw.csv",
    "tar xf clash.tar -O",
]


@pytest.mark.parametrize("command", PATH_REFUSALS)
def test_bash_path_aware_refusals(pa, command):
    reason = guard.check_tool(pa.guard, "Bash", {"command": command}, str(pa.root))
    assert reason and reason.endswith("."), command


@pytest.mark.parametrize("command", PATH_ALLOWED)
def test_bash_path_aware_allowed(pa, command):
    assert guard.check_tool(pa.guard, "Bash", {"command": command}, str(pa.root)) is None, command


def test_bash_path_aware_uses_the_calls_working_folder(pa):
    here = {"command": "rm -rf ."}
    assert guard.check_tool(pa.guard, "Bash", here, str(pa.root / "data"))                 # data holds protected files
    assert guard.check_tool(pa.guard, "Bash", here, str(pa.root / "code")) is None          # code does not
    assert guard.check_tool(pa.guard, "Bash", here, None)                                   # no cwd: the project folder
    assert guard.check_tool(pa.guard, "Bash", {"command": "cp ../new/raw.csv ."}, str(pa.root / "data"))
    assert guard.check_tool(pa.guard, "Bash", {"command": "cp ../new/raw.csv ."}, str(pa.root / "fresh")) is None
    assert guard.check_tool(pa.guard, "Bash", {"command": "rm -rf data"}, str(pa.tmp)) is None  # outside the project: another data


def test_bash_path_aware_without_protected_files_judges_nothing(project):
    guard.write_guard_file(project.run_dir, root=project.root, prot={"dirs": [], "files": []}, engine_port=None)
    g = guard.load_guard(project.run_dir)
    assert guard.check_tool(g, "Bash", {"command": "rm -rf data"}, str(project.root)) is None


def test_bash_path_aware_home_and_symlinks(pa, monkeypatch):
    monkeypatch.setenv("HOME", str(pa.root))
    assert guard.check_tool(pa.guard, "Bash", {"command": "rm -rf ~/data"}, str(pa.root / "code"))
    assert guard.check_tool(pa.guard, "Bash", {"command": "cp ~/new/raw.csv ~/data/"}, str(pa.root / "code"))
    monkeypatch.setenv("HOME", str(pa.tmp / "elsewhere"))
    assert guard.check_tool(pa.guard, "Bash", {"command": "rm -rf ~/data"}, str(pa.root / "code")) is None
    (pa.root / "datalink").symlink_to(pa.root / "data")
    assert guard.check_tool(pa.guard, "Bash", {"command": "cp new/raw.csv datalink/"}, str(pa.root))   # through the link
    assert guard.check_tool(pa.guard, "Bash", {"command": "rm -rf datalink"}, str(pa.root)) is None    # removes the link only


def test_bash_path_aware_never_raises_on_odd_text(pa):
    import random

    words = ("rm mv cp -r -rf -t -o -O -P -C -d -x -- find -delete -name -exec unzip tar curl wget git rsync ln install "
             "data data/ data/* raw.csv ~ . .. / ' \" ; && | ( ) $( ` $X * ? [ { } xf d.zip bash sh -c eval sudo env FOO=1 "
             "-u > 2>&1 \\ \0").split()
    rnd = random.Random(7)
    for _ in range(2000):
        command = " ".join(rnd.choice(words) for _ in range(rnd.randint(1, 12)))
        guard.check_tool(pa.guard, "Bash", {"command": command}, rnd.choice([None, str(pa.root), str(pa.root / "data"), "rel"]))


def test_git_mutation_reads_quoted_option_values(project):
    for command in ('git -C "/tmp/My Project" commit -m x', "git -C '/tmp/My Project' push",
                    'git -c "user.name=A B" -c user.email=a@b commit -m "x y"', 'git --git-dir="/a b/.git" tag v1',
                    'git -C "/tmp/a;b" push'):
        assert _refused(project, "Bash", {"command": command}), command
    for command in ('git -C "/tmp/My Project" status', "git -c 'a b=c' log -1", 'git -C "/x y" diff --stat'):
        assert _refused(project, "Bash", {"command": command}) is None, command


@pytest.mark.parametrize("command", [
    "curl -sL https://zenodo.org/api/records/1234567/files/data.zip -o data.zip", "curl https://zenodo.org/api/records/123",
    "wget https://example.org/api/papers?q=x", "curl http://example.com/api/share", "curl https://x.org/api/notes/1",
    "curl https://notlocalhost.example.com/api/records", "curl https://localhost.example.com/api/records",
    "curl https://127.0.0.1.example.com/api/records", "curl h/api/projects", "curl -X POST http://x/api/proposals",
])
def test_bash_public_apis_with_research_os_looking_paths_are_allowed(project, command):
    assert _refused(project, "Bash", {"command": command}) is None, command


@pytest.mark.parametrize("command", [
    "curl http://127.0.0.1/api/records", "curl http://127.0.0.1:8123/api/records/1", "curl localhost/api/papers",
    "curl http://localhost:3000/api/notes", "curl 0.0.0.0/api/projects", "curl 'http://[::1]/api/agents'",
    "curl http://127.1.2.3:99/api/settings", "wget http://LOCALHOST:5/api/Records",
    "curl localhost:8765", "curl http://127.0.0.1:8765/anything",
])
def test_bash_research_os_api_on_a_loopback_host_or_the_engine_port_is_refused(project, command):
    assert _refused(project, "Bash", {"command": command}), command


# -- 18.1 item 6: rule 3's suffixes ---------------------------------------------------------------------------

NEW_DATA_SUFFIXES = (".sqlite", ".db", ".npy", ".npz", ".mat", ".sas7bdat", ".jsonl", ".pickle")


def test_the_added_suffixes_are_data_for_the_walk_the_hook_and_the_after_run_check(tmp_path):
    """One list: `snapshot.DATA_SUFFIXES` is what the data walk finds, what `protected` hands the hook through
    `guard.json`, and what the after-run check reports."""
    assert set(NEW_DATA_SUFFIXES) <= snapshot.DATA_SUFFIXES
    wanted = [f"data/x{s}" for s in NEW_DATA_SUFFIXES] + ["data/Upper.SQLITE", "deep/er/panel.NPZ"]
    root = _project(tmp_path / "proj", {rel: "1" for rel in wanted} | {"data/notes.txt": "n", "data/run.sqlite3": "3"})
    picture = snapshot.take(root)
    assert set(picture["data"]) == set(wanted)  # `.sqlite3` is no listed suffix
    prot = snapshot.protected(root, picture)
    assert set(prot["data"]) == set(wanted) and set(prot["files"]) >= set(wanted)
    old_shape = {k: v for k, v in picture.items() if k not in ("data", "data_partial")}
    assert set(snapshot.protected(root, old_shape)["data"]) == set(wanted)  # the fallback reads the same list

    rd = tmp_path / "rd"
    rd.mkdir()
    guard.write_guard_file(rd, root=root, prot=prot, engine_port=None)
    g = guard.load_guard(rd)
    for rel in wanted:
        assert guard.check_tool(g, "Write", {"file_path": str(root / rel)}), rel
        assert guard.check_tool(g, "Edit", {"file_path": str(root / rel)}), rel
        assert guard.check_tool(g, "Bash", {"command": f"rm {rel}"}), rel
        assert guard.check_tool(g, "Bash", {"command": f"echo x > {rel}"}), rel
    for rel in ("data/new.jsonl", "data/run.sqlite3", "data/notes.txt"):
        assert guard.check_tool(g, "Write", {"file_path": str(root / rel)}) is None, rel

    def mutate(r):
        for rel in wanted[:4]:
            (r / rel).write_text("changed", encoding="utf-8")
        for rel in wanted[4:]:
            (r / rel).unlink()
        (r / "data/new.pickle").write_text("fresh data is allowed", encoding="utf-8")
        (r / "data/run.sqlite3").write_text("not listed", encoding="utf-8")

    found, _ = _violations(root, mutate)
    assert sorted((v["path"], v["reason"]) for v in found) == sorted(
        [(rel, "data_changed") for rel in wanted[:4]] + [(rel, "data_deleted") for rel in wanted[4:]])


def test_package_json_and_package_lock_are_not_data_in_any_folder(tmp_path):
    exempt = ["package.json", "package-lock.json", "web/package.json", "web/sub/package-lock.json",
              "up/Package.JSON", "up/PACKAGE-LOCK.json"]
    data = ["packages.json", "my-package.json", "web/package.jsonl", "web/package-lock.json.csv", "settings.json",
            "npm-shrinkwrap.json"]  # exactly the two names are exempt (18.1 item 6): a dataset of that name is not
    root = _project(tmp_path / "proj", {rel: "{}" for rel in exempt + data})
    picture = snapshot.take(root)
    assert set(picture["data"]) == set(data)
    assert set(snapshot.protected(root, picture)["data"]) == set(data)
    assert not any(snapshot._is_data(rel) for rel in exempt)  # by file name, whatever the folder
    assert all(snapshot._is_data(rel) for rel in data)

    rd = tmp_path / "rd"
    rd.mkdir()
    guard.write_guard_file(rd, root=root, prot=snapshot.protected(root, picture), engine_port=None)
    g = guard.load_guard(rd)
    for rel in exempt:  # `npm install` writes these: the hook lets it
        assert guard.check_tool(g, "Write", {"file_path": str(root / rel)}) is None, rel
        assert guard.check_tool(g, "Bash", {"command": f"rm {rel}"}) is None, rel
    for rel in data:
        assert guard.check_tool(g, "Write", {"file_path": str(root / rel)}), rel

    def mutate(r):
        for rel in exempt:
            (r / rel).write_text('{"changed": true}', encoding="utf-8")
        (r / "web/package.json").unlink()
        (r / "packages.json").write_text("changed", encoding="utf-8")

    found, changes = _violations(root, mutate)
    assert [(v["path"], v["reason"]) for v in found] == [("packages.json", "data_changed")]
    assert "web/package.json" in changes["deleted"] and "package.json" in changes["modified"]  # still in the change list


# -- 18.1 item 6: every refusal has a code ----------------------------------------------------------------------

REFUSAL_CASES = [  # (tool, input, expected code)
    ("Write", {"file_path": "/etc/hosts"}, "outside_project"),
    ("Edit", {"file_path": "../x.txt"}, "outside_project"),
    ("Write", {}, "no_file"),
    ("Write", {"file_path": "."}, "no_file"),
    ("Write", {"file_path": ".researchos/team.toml"}, "records_folder"),
    ("Write", {"file_path": "sub/.RCE/x"}, "records_folder"),
    ("Write", {"file_path": ".git/config"}, "git_files"),
    ("Write", {"file_path": "vendor/.git/hooks/pre-commit"}, "git_files"),
    ("Write", {"file_path": ".claude/settings.json"}, "agent_settings"),
    ("Write", {"file_path": "opencode.json"}, "agent_settings"),
    ("Write", {"file_path": "data/raw.csv"}, "protected_file"),
    ("Edit", {"file_path": "map.md"}, "protected_file"),
    ("Write", {"file_path": "data/clean.DTA"}, "protected_file"),
    ("Bash", {"command": "researchos status"}, "research_os_command"),
    ("Bash", {"command": "git push origin main"}, "git_state"),
    ("Bash", {"command": "cd x && git -C . commit -m y"}, "git_state"),
    ("Bash", {"command": "curl http://127.0.0.1:8765/api/agents/runs"}, "engine_api"),
    ("Bash", {"command": "cat .researchos/team.toml"}, "records_folder"),
    ("Bash", {"command": "echo x > .git/config"}, "git_files"),
    ("Bash", {"command": "echo x > .claude/settings.json"}, "agent_settings"),
    ("Bash", {"command": "rm data/raw.csv"}, "protected_file"),
    ("Bash", {"command": "echo x > data/raw.csv"}, "protected_file"),
    ("Bash", {"command": "rm -rf data"}, "protected_file"),
    ("Bash", {"command": 5}, "unchecked"),
    ("Bash", ["git push"], "unchecked"),
    ("Edit", "file", "unchecked"),
    ("Read", {"file_path": "x\0y"}, "unchecked"),
]


@pytest.mark.parametrize("tool,tool_input,code", REFUSAL_CASES)
def test_a_refusal_is_a_sentence_for_the_agent_that_carries_its_code(project, tool, tool_input, code):
    reason = guard.check_tool(project.guard, tool, tool_input, str(project.root))
    assert isinstance(reason, str) and reason and isinstance(reason, guard.Refusal)
    assert reason.code == code and guard.refusal_code(reason) == code and code in guard.REFUSAL_CODES


def test_the_reading_tools_refuse_with_the_off_limits_code(project, tmp_path):
    for tool, tool_input in (("Read", {"file_path": os.path.expanduser("~/.ssh/id_ed25519")}),
                             ("Grep", {"pattern": "x", "path": os.path.expanduser("~")}),
                             ("Bash", {"command": "ls ~/.ssh"}), ("Bash", {"command": "echo $RESEARCHOS_HOME"})):
        reason = guard.check_tool(project.guard, tool, tool_input, str(project.root))
        assert reason and reason.code == "off_limits", (tool, tool_input)


def test_every_code_is_reached_and_a_code_nobody_declared_is_refused():
    reached = {code for _t, _i, code in REFUSAL_CASES} | {"off_limits"}
    assert reached == set(guard.REFUSAL_CODES)  # a code in the list that no case reaches would be dead
    with pytest.raises(ValueError):
        guard.Refusal("x", "made_up")
    assert guard.refusal_code("a plain sentence") is None and guard.refusal_code(None) is None
    assert guard.refusal_code(guard.Refusal("x", "git_state")) == "git_state"


def test_the_marker_a_code_travels_in_round_trips_and_is_taken_off_only_at_the_end():
    sentence = "Writing inside .git is not allowed in this run; leave git's files alone."
    said = guard.with_marker(sentence, "git_files")
    assert said == sentence + " [refusal: git_files]"
    assert guard.split_refusal(said) == (sentence, "git_files")
    assert guard.split_refusal("Research OS: " + said + "\n") == ("Research OS: " + sentence, "git_files")
    assert guard.with_marker(sentence, None) == sentence and guard.with_marker(sentence, "made_up") == sentence
    for text in (sentence, "a [refusal: git_files] in the middle of it", "[refusal: made_up]", "ends with [refusal: ]", ""):
        assert guard.split_refusal(text) == (text, None), text


def test_log_refusal_records_the_code_of_a_refusal_and_none_for_a_plain_reason(tmp_path):
    guard.log_refusal(tmp_path, "Bash", "git push", guard.Refusal("no git.", "git_state"))
    guard.log_refusal(tmp_path, "Bash", "x", "a plain reason")
    first, second = runs.read_lines(tmp_path / "refusals.jsonl")
    assert first["code"] == "git_state" and first["reason"] == "no git." and "code" not in second
    assert second["reason"] == "a plain reason"


def test_the_hook_says_the_code_to_the_agent_and_logs_it(project):
    done = _hook(project.run_dir, {"tool_name": "Edit", "tool_input": {"file_path": str(project.root / "data/raw.csv")}})
    message = done.stderr.decode("utf-8")
    assert done.returncode == 2 and message.startswith("Research OS: ") and message.endswith(" [refusal: protected_file]\n")
    reason, code = guard.split_refusal(message)
    assert code == "protected_file" and "protected file" in reason
    (record,) = runs.read_lines(project.run_dir / "refusals.jsonl")
    assert record["code"] == "protected_file" and record["reason"] == reason[len("Research OS: "):]  # the log has no marker
    done = _hook(project.run_dir, {"tool_name": "Bash", "tool_input": {"command": "researchos status"}, "cwd": str(project.root)})
    assert done.stderr.decode("utf-8").endswith(" [refusal: research_os_command]\n")


def test_a_hook_that_could_not_check_refuses_with_the_unchecked_code(project, tmp_path):
    for raw in (b"not json", b'{"tool_name": 5}'):
        done = _hook(project.run_dir, None, raw=raw)
        assert done.returncode == 2 and done.stderr.decode("utf-8").endswith(" [refusal: unchecked]\n")
    assert [r["code"] for r in runs.read_lines(project.run_dir / "refusals.jsonl")] == ["unchecked", "unchecked"]
    empty = tmp_path / "empty-run"
    empty.mkdir()
    done = _hook(empty, {"tool_name": "Bash", "tool_input": {"command": "python x.py"}})
    assert done.returncode == 2 and b"could not check" in done.stderr and done.stderr.endswith(b" [refusal: unchecked]\n")
    assert runs.read_lines(empty / "refusals.jsonl")[0]["code"] == "unchecked"

"""Tests for researchos.agents core (DESIGN.md Section 17): this Mac's agents
and programs (profiles), the run store (runs), the before-picture and the
after-run check (snapshot), and Claude Code's PreToolUse guard (guard, hook).

No test starts a real `claude` or `opencode`: programs are tiny scripts with a
`#!<sys.executable>` shebang."""

from __future__ import annotations

import json
import os
import shlex
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
        "sub/__pycache__/a.pyc": "x", "sub/.venv/pyvenv.cfg": "x", ".hidden": "h"})
    copies = tmp_path / "copies"
    picture = snapshot.take(root, copy_to=copies)
    assert set(picture["files"]) == {"data.csv", "code/run.py", "中文/说明.md", ".researchos/attempts.toml", ".hidden"}
    entry = picture["files"]["data.csv"]
    import hashlib

    assert entry["sha256"] == hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    assert entry["size"] == 8 and entry["copied"] is True and isinstance(entry["mtime_ns"], int)
    assert (copies / "data.csv").read_bytes() == b"a,b\n1,2\n"
    assert (copies / "中文" / "说明.md").read_text(encoding="utf-8") == "你好"
    assert (copies / ".researchos" / "attempts.toml").is_file()
    assert not (copies / ".git").exists() and not (copies / "node_modules").exists()
    assert picture["partial"] is False and picture["not_copied"] == 0 and picture["git"] == {"head": None, "branch": None}
    assert picture["copied_bytes"] == sum(e["size"] for e in picture["files"].values())
    json.dumps(picture)  # it is stored as before.json


def test_take_without_copies(tmp_path):
    root = _project(tmp_path / "proj", {"a.txt": "a"})
    picture = snapshot.take(root)
    assert "copied" not in picture["files"]["a.txt"] and picture["files"]["a.txt"]["sha256"]
    assert picture["not_copied"] == 1 and picture["copied_bytes"] == 0


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
    assert prot["dirs"] == [".researchos", names.LEGACY_PROJECT_DIRNAME, ".git"]
    assert prot["records"] == ["map.md", "research/tasks.json"]
    assert prot["data"] == ["config.json", "data/clean.DTA", "data/raw.csv"]
    assert prot["files"] == sorted(prot["data"] + prot["records"])
    assert "figs/plot.png" not in prot["files"] and "data/notes.txt" not in prot["files"]
    json.dumps(prot)


def test_protected_without_attempts_config_or_tasks_file(tmp_path):
    root = _project(tmp_path / "proj", {"x.csv": "1", "script.py": "1"})
    prot = snapshot.protected(root, snapshot.take(root))
    assert prot["records"] == ["research/tasks.json"]  # listed even though it does not exist yet
    assert prot["data"] == ["x.csv"] and "research/tasks.json" in prot["files"]


def test_protected_attempt_table_that_is_a_data_file_is_a_record(tmp_path):
    root = _project(tmp_path / "proj", {".researchos/attempts.toml": ATTEMPTS_TOML.replace("map.md", "attempts.csv"),
                                        "attempts.csv": "a", "other.csv": "b"})
    prot = snapshot.protected(root, snapshot.take(root))
    assert prot["records"] == ["attempts.csv", "research/tasks.json"] and prot["data"] == ["other.csv"]


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
    assert snapshot.git_state(fresh) == {"head": None, "branch": "trunk"}


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
    assert found == [{"path": ".git", "reason": "git_changed"}]
    # no repository before and none after: nothing to report
    found, _ = _violations(_project(tmp_path / "plain", {"a": "a"}), lambda r: None)
    assert found == []


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
    assert g["dirs"] == [".researchos", names.LEGACY_PROJECT_DIRNAME, ".git"] and "data/raw.csv" in g["files"]
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
    "curl -X POST http://x/api/proposals", "curl http://example.com/api/share", "curl h/api/judgements",
    "curl h/api/attempts?x=1", "curl h/api/workspace", "curl h/api/edges", "curl h/api/mappings",
    "curl h/api/records", "curl h/api/github", "curl h/api/shutdown", "curl h/api/settings", "curl h/api/notes",
    "curl h/api/papers", "curl h/api/variables", "curl h/api/projects", "curl h/api/stata", "curl h/api/agents",
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

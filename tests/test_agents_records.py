"""Package C of DESIGN.md Section 17: what the records layer, the workspace,
the history and the command line do for AI agents -- the agent labels, the
`RESEARCHOS_AGENT_RUN` refusal of every human write path, write listeners,
the task `report`, the agent as the author of a save, and how the history
names an agent."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from researchos import cli, history, project, workspace
from researchos.agents import labels
from researchos.records import files as record_files
from researchos.records import notes as notes_mod
from researchos.records import situation
from researchos.records.identity import read_identity

from attempt_seed import seed_attempt

RESULT = "research/results/" + "ab" * 16 + ".md"
AGENT = labels.Agent("claude-code", "sonnet")
AUTHOR = {"source": "agent", "who": labels.who_value(AGENT, "m-1"), "note": "r-20261021T101500Z-abc123"}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    return root


@pytest.fixture
def in_run(monkeypatch):
    monkeypatch.setenv(situation.AGENT_RUN_ENV, "r-20261021T101500Z-abc123")


def _pid(root: Path) -> str:
    return read_identity(root).identity.id


def _versions(root: Path, rel: str):
    with history.Store.open(_pid(root)) as store:
        return store.versions(notes_mod.normalize_node(rel))


# -- labels ---------------------------------------------------------------------------------------


def test_agent_spellings():
    assert AGENT.owner == "agent:claude-code:sonnet"
    assert AGENT.label == "Claude Code · sonnet"
    assert AGENT.payload() == {"runner": "claude-code", "model": "sonnet", "owner": "agent:claude-code:sonnet",
                               "label": "Claude Code · sonnet"}
    assert labels.Agent("opencode", "lmstudio/qwen3-coder").label == "OpenCode · lmstudio/qwen3-coder"


@pytest.mark.parametrize("model", ["sonnet", "claude-opus-4", "a" * 120, "lmstudio/qwen3-coder:30b@q4", "m[1m]+x"])
def test_check_model_accepts(model):
    assert labels.check_model(f"  {model} ") == model


@pytest.mark.parametrize("model", ["", "   ", "-x", "a" * 121, "a b", "a;b", "a\nb", ".x", None, 3, "é"])
def test_check_model_refuses(model):
    with pytest.raises(ValueError):
        labels.check_model(model)


def test_parse_owner():
    assert labels.parse_owner("agent:claude-code:sonnet") == AGENT
    assert labels.parse_owner("agent:codex:gpt-6.1-sol") == labels.Agent("codex", "gpt-6.1-sol")
    assert labels.parse_owner("agent:opencode:a/b:c") == labels.Agent("opencode", "a/b:c")  # split once
    for plain in ("", "Zhang San", "me", None, 5, "Agent:claude-code:x"):
        assert labels.parse_owner(plain) is None
    for bad in ("agent:", "agent:claude-code", "agent:claude-code:", "agent:unknown:x", "agent::x",
                "agent:claude-code:-x", "agent:claude-code: sonnet", "agent:claude-code:sonnet\n"):
        with pytest.raises(ValueError):
            labels.parse_owner(bad)


def test_who_round_trip():
    assert labels.who_value(AGENT, None) == "agent:claude-code:sonnet"
    assert labels.who_value(AGENT, "m-1") == "agent:claude-code:sonnet#m-1"
    assert labels.parse_who("agent:claude-code:sonnet#m-1") == (AGENT, "m-1")
    assert labels.parse_who("agent:claude-code:sonnet") == (AGENT, None)
    assert labels.parse_who("agent:claude-code:sonnet#") == (AGENT, None)
    for other in (None, "", "zhang@example.com", "agent:nope:x#m", "agent:claude-code", 7):
        assert labels.parse_who(other) is None


# -- the refusal inside an agent's run -------------------------------------------------------------


def test_human_write_refused_inside_an_agent_run(root, in_run):
    with pytest.raises(situation.AgentRunRefused) as caught:
        with situation.write_guard(root, human=True):
            pytest.fail("the body must not run")
    assert isinstance(caught.value, situation.WriteRefused)
    assert caught.value.state == "agent_refused" and "nothing written" in str(caught.value)
    # the page's own save goes through the same guard
    with pytest.raises(situation.AgentRunRefused):
        workspace.save_document(root, {"kind": "note", "title": "T", "content": "x", "revision": None})
    assert not (root / "research").exists()


def test_refusal_comes_before_the_lock(root, in_run, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("the lock must not be taken")

    monkeypatch.setattr(situation, "project_lock", boom)
    with pytest.raises(situation.AgentRunRefused):
        with situation.write_guard(root, "no-such-id", human=True):
            pass


def test_unset_env_is_not_refused_and_any_guard_is_refused_in_a_run(root, monkeypatch):
    with situation.write_guard(root):  # no env
        pass
    with situation.write_guard(root, human=True):
        pass
    monkeypatch.setenv(situation.AGENT_RUN_ENV, "")  # empty means unset
    with situation.write_guard(root, human=True):
        pass
    monkeypatch.setenv(situation.AGENT_RUN_ENV, "r-x")
    with pytest.raises(situation.AgentRunRefused):  # a publication or index write is refused too
        with situation.write_guard(root):
            pass


def test_agent_writes_allowed_is_scoped(root, in_run):
    with situation.agent_writes_allowed():
        with situation.write_guard(root, human=True):
            pass
        seen = []
        thread = threading.Thread(target=lambda: seen.append(_refused(root)))
        thread.start()
        thread.join()
        assert seen == [True]  # another thread's context is not opened
    assert _refused(root)
    with pytest.raises(RuntimeError):
        with situation.agent_writes_allowed():
            raise RuntimeError("x")
    assert _refused(root)  # reset even when the body raised


def _refused(root: Path) -> bool:
    try:
        with situation.write_guard(root, human=True):
            return False
    except situation.AgentRunRefused:
        return True


# -- write listeners -------------------------------------------------------------------------------


@pytest.fixture
def heard():
    paths: list[Path] = []
    record_files.add_write_listener(paths.append)
    yield paths
    record_files.remove_write_listener(paths.append)


def test_listeners_hear_durable_write_append_and_new_files(tmp_path, heard):
    target = tmp_path / "a.txt"
    record_files.durable_write(target, b"one\n")
    assert heard == [target]  # the final path, never the temp name
    record_files.append_bytes(target, b"two\n")
    assert heard == [target, target]
    fresh = tmp_path / "b.bin"
    record_files.write_new_file(fresh, b"x")
    assert heard[-1] == fresh and len(heard) == 3
    record_files.notify_write(str(tmp_path / "c"))
    assert heard[-1] == tmp_path / "c"


def test_failed_write_is_not_announced(tmp_path, heard):
    with pytest.raises(record_files.RecordFileError):
        record_files.durable_write(tmp_path / "gone" / "a.txt", b"x")
    assert heard == []


def test_listener_errors_are_swallowed_and_removal_works(tmp_path, heard, caplog):
    def bad(_path):
        raise RuntimeError("listener bug")

    record_files.add_write_listener(bad)
    try:
        record_files.durable_write(tmp_path / "a.txt", b"x")
    finally:
        record_files.remove_write_listener(bad)
    assert (tmp_path / "a.txt").read_bytes() == b"x" and heard == [tmp_path / "a.txt"]
    record_files.remove_write_listener(bad)  # not registered: ignored
    record_files.durable_write(tmp_path / "a.txt", b"y")
    assert len(heard) == 2


def test_workspace_creation_is_announced_at_its_final_path(root, heard):
    saved = workspace.save_document(root, {"kind": "note", "title": "T", "content": "x", "revision": None})
    assert (root / saved["path"]) in heard



# -- tasks -----------------------------------------------------------------------------------------


def _task(**extra) -> dict:
    return {"id": "t1", "text": "Run it", "done": False, "owner": "", "due": "", **extra}


def test_task_without_report_is_byte_identical_on_disk(root):
    attempt = seed_attempt(root)  # 21.1: a new task names its attempt
    first = workspace.save_tasks(root, {"tasks": [_task(owner="Zhang San", due="2026-11-01", attempt=attempt)], "revision": None})
    expected = json.dumps([_task(owner="Zhang San", due="2026-11-01", attempt=attempt)], ensure_ascii=False, indent=2) + "\n"
    assert (root / "research/tasks.json").read_text() == expected
    assert "report" not in first["tasks"][0]
    again = workspace.save_tasks(root, {"tasks": [_task(owner="Zhang San", due="2026-11-01", report="", attempt=attempt)],
                                        "revision": first["tasks_revision"]})
    assert again["backup"] is None  # an empty report is the same file
    assert (root / "research/tasks.json").read_text() == expected


def test_task_report_and_agent_owner(root):
    saved = workspace.save_tasks(root, {"tasks": [_task(owner="agent:claude-code:sonnet", done=True, report=RESULT,
                                                        attempt=seed_attempt(root))], "revision": None})
    assert saved["tasks"][0]["report"] == RESULT
    on_disk = json.loads((root / "research/tasks.json").read_text())
    assert on_disk[0]["report"] == RESULT and on_disk[0]["owner"] == "agent:claude-code:sonnet"
    tasks, revision = workspace.read_tasks(root)
    assert tasks == saved["tasks"] and revision == saved["tasks_revision"]
    assert revision == hashlib.sha256((root / "research/tasks.json").read_bytes()).hexdigest()
    assert workspace.overview(root)["tasks"] == tasks


@pytest.mark.parametrize("report", [
    "research/notes/" + "ab" * 16 + ".md", "research/results/x.md", "research/results/" + "AB" * 16 + ".md",
    "../research/results/" + "ab" * 16 + ".md", RESULT + "\n", "/abs/" + RESULT, 5, None, ["x"],
])
def test_bad_report_is_refused(root, report):
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.save_tasks(root, {"tasks": [_task(report=report)], "revision": None})
    assert caught.value.code == "invalid"
    assert not (root / "research/tasks.json").exists()


@pytest.mark.parametrize("owner", ["agent:", "agent:claude-code", "agent:unknown:x", "agent:claude-code:-rf",
                                   "agent:opencode:a b", "agent:claude-code:" + "m" * 121])
def test_bad_agent_owner_is_refused(root, owner):
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.save_tasks(root, {"tasks": [_task(owner=owner)], "revision": None})
    assert caught.value.code == "invalid" and "runner and a model" in str(caught.value)


def test_other_keys_still_refused_and_message_lists_report(root):
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.save_tasks(root, {"tasks": [_task(session="x")], "revision": None})
    assert "report" in str(caught.value)


def test_read_tasks_without_file_and_with_garbage(root):
    assert workspace.read_tasks(root) == ([], None)
    (root / "research").mkdir()
    (root / "research/tasks.json").write_text("not json")
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.read_tasks(root)
    assert caught.value.code == "unreadable"


# -- the agent as author -----------------------------------------------------------------------------


def test_document_author_reaches_only_the_new_version(root):
    created = workspace.save_document(root, {"kind": "result", "title": "Done", "content": "numbers", "revision": None},
                                      author=AUTHOR)
    rel = created["path"]
    assert rel.startswith("research/results/")
    [only] = _versions(root, rel)
    assert (only.source, only.who, only.note) == ("agent", "agent:claude-code:sonnet#m-1", AUTHOR["note"])
    # a person edits it afterwards: the pre-image is the scan's, the post-image the person's
    workspace.save_document(root, {"path": rel, "content": "# Done\n\nedited", "revision": created["revision"]})
    # and the agent overwrites it
    now = workspace.document(root, rel)
    workspace.save_document(root, {"path": rel, "content": "# Done\n\nagent again", "revision": now["revision"]},
                            author=AUTHOR)
    sources = [v.source for v in _versions(root, rel)]
    assert sources[0] == "agent" and sources[1] != "agent" and "agent" in sources[2:]


def test_overwrite_by_agent_keeps_pre_image_as_scan(root):
    first = workspace.save_document(root, {"kind": "note", "title": "N", "content": "a", "revision": None})
    workspace.save_document(root, {"path": first["path"], "content": "# N\n\nb", "revision": first["revision"]},
                            author=AUTHOR)
    newest, oldest = _versions(root, first["path"])[:2]
    assert newest.source == "agent" and newest.who.startswith("agent:claude-code:sonnet")
    assert oldest.source != "agent"


def test_tasks_author_and_no_author_unchanged(root):
    attempt = seed_attempt(root)
    workspace.save_tasks(root, {"tasks": [_task(attempt=attempt)], "revision": None}, author=AUTHOR)
    [v] = _versions(root, "research/tasks.json")
    assert v.source == "agent" and v.note == AUTHOR["note"]
    # without an author the version is the scan's, as before
    _tasks, revision = workspace.read_tasks(root)
    workspace.save_tasks(root, {"tasks": [_task(text="Edited", attempt=attempt)], "revision": revision})
    assert _versions(root, "research/tasks.json")[0].source == "scan"


@pytest.mark.parametrize("author", [
    {"source": "approval", "who": "agent:claude-code:sonnet", "note": None},
    {"source": "agent", "who": "someone", "note": None},
    {"source": "agent", "who": "agent:claude-code:sonnet", "note": 5},
    {"who": "agent:claude-code:sonnet"},
    "agent",
])
def test_bad_author_writes_nothing(root, author):
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.save_document(root, {"kind": "note", "title": "T", "content": "x", "revision": None}, author=author)
    assert caught.value.code == "invalid"
    assert not (root / "research").exists()


def test_agent_tools_write_inside_a_run(root, in_run):
    """The shape package D uses: the env var is set and only the tools open the guard."""
    with pytest.raises(situation.AgentRunRefused):
        workspace.save_document(root, {"kind": "note", "title": "T", "content": "x", "revision": None})
    with situation.agent_writes_allowed(), situation.write_guard(root, _pid(root), human=True, timeout=10.0):
        saved = workspace.save_document(root, {"kind": "note", "title": "T", "content": "x", "revision": None},
                                        author=AUTHOR)
    assert (root / saved["path"]).is_file()


# -- naming an agent in the history ----------------------------------------------------------------------


def _version(who: str | None, **extra) -> dict:
    return {"who": who, "source": "agent", **extra}


def test_name_people_labels_agents(root, monkeypatch):
    from researchos import people

    team = SimpleNamespace(state="present", members=[SimpleNamespace(id="m-1", name="张三")])
    monkeypatch.setattr(people, "read_team", lambda _root: team)
    versions = [_version("agent:claude-code:sonnet#m-1"), _version("agent:opencode:lmstudio/qwen3-coder"),
                _version("agent:claude-code:sonnet#nobody@example.com"), _version("agent:bogus:x#m-1")]
    zh = history.name_people(root, [dict(v) for v in versions], "zh")
    assert [v["who_label"] for v in zh] == [
        "Claude Code · sonnet（张三 指派）", "OpenCode · lmstudio/qwen3-coder",
        "Claude Code · sonnet（nobody@example.com 指派）", "agent:bogus:x#m-1"]
    en = history.name_people(root, [dict(v) for v in versions], "en")
    assert en[0]["who_label"] == "Claude Code · sonnet (assigned by 张三)"
    assert en[1]["who_label"] == "OpenCode · lmstudio/qwen3-coder"


def test_name_people_does_not_confuse_braces_in_names(root, monkeypatch):
    from researchos import people

    team = SimpleNamespace(state="present", members=[SimpleNamespace(id="m-1", name="{x}")])
    monkeypatch.setattr(people, "read_team", lambda _root: team)
    [v] = history.name_people(root, [_version("agent:claude-code:sonnet#m-1")], "en")
    assert v["who_label"] == "Claude Code · sonnet (assigned by {x})"


def test_agent_is_a_history_source_and_shows_in_versions(root):
    assert "agent" in history.SOURCES
    created = workspace.save_document(root, {"kind": "result", "title": "R", "content": "x", "revision": None},
                                      author=AUTHOR)
    out = history.versions(root, created["path"], "en")
    [v] = out["versions"]
    assert v["source"] == "agent" and v["note"] == AUTHOR["note"]
    assert v["who_label"].startswith("Claude Code · sonnet")


# -- what a scan sees while a run goes on (DESIGN.md 18.1 item 3) ------------------------------------------

RUN = "r-20261021T101500Z-abc123"
WHO = labels.who_value(AGENT, "m-1")


@pytest.fixture
def watched(root):
    """A run going on in `root` whose `owns` is whatever the test puts in `owned`; released afterwards."""
    owned: set[str] = set()
    seen = history.RunSeen(who=WHO, run=RUN, owns=lambda rel, entry: rel in owned)
    history.watch_run(root, seen)
    try:
        yield owned
    finally:
        history.release_run(root, seen)


def _rows(root: Path, rel: str) -> list[tuple]:
    return [(v.source, v.who, v.note) for v in reversed(_versions(root, rel))]


def test_the_registry_knows_one_run_per_project_and_lets_go(root, tmp_path):
    assert history.run_seen(root) is None
    first = history.RunSeen(who=WHO, run=RUN, owns=lambda rel, entry: True)
    second = history.RunSeen(who=WHO, run="r-20261021T111500Z-def456", owns=lambda rel, entry: True)
    history.watch_run(root, first)
    assert history.run_seen(root) is first and history.run_seen(tmp_path / "elsewhere") is None
    assert first.note == f"seen during run {RUN}" and history.seen_run_id(first.note) == RUN
    history.watch_run(root, second)
    history.release_run(root, first)  # the first one's release does not undo the second
    assert history.run_seen(root) is second
    history.release_run(root, second)
    assert history.run_seen(root) is None
    history.release_run(root, second)  # twice is harmless
    assert history.seen_run_id("a note") is None and history.seen_run_id(None) is None
    assert history.seen_run_id("seen during run ") is None


def test_a_scan_during_a_run_credits_what_the_run_owns_and_nothing_else(root, watched):
    (root / "agent.py").write_text("print(1)\n", encoding="utf-8")
    (root / "person.py").write_text("print(2)\n", encoding="utf-8")
    watched.add("agent.py")
    history.record_scan(None, root)
    assert _rows(root, "agent.py") == [("agent", WHO, f"seen during run {RUN}")]
    assert [r[0] for r in _rows(root, "person.py")] == ["scan"]  # not the run's: the person's, or older than it
    # the same scan again, and a touch without a change, add nothing
    history.record_scan(None, root)
    (root / "agent.py").write_text("print(1)\n", encoding="utf-8")
    history.record_scan(None, root)
    assert len(_rows(root, "agent.py")) == 1
    # a change adds a version, again the run's
    (root / "agent.py").write_text("print(3)\n", encoding="utf-8")
    history.record_scan(None, root)
    assert [r[0] for r in _rows(root, "agent.py")] == ["agent", "agent"]


def test_without_a_run_a_scan_is_the_scans_as_before(root):
    (root / "x.py").write_text("print(1)\n", encoding="utf-8")
    history.record_scan(None, root)
    assert [r[0] for r in _rows(root, "x.py")] == ["scan"]


def test_a_scan_that_is_told_the_run_does_not_need_the_registry(root):
    (root / "x.py").write_text("print(1)\n", encoding="utf-8")
    seen = history.RunSeen(who=WHO, run=RUN, owns=lambda rel, entry: entry["sha256"] == hashlib.sha256(b"print(1)\n").hexdigest())
    history.record_scan(None, root, seen=seen)
    assert _rows(root, "x.py") == [("agent", WHO, f"seen during run {RUN}")]


def test_owns_is_asked_about_the_content_it_is_given(root):
    asked = []

    def owns(rel, entry):
        asked.append((rel, entry["size"], entry["sha256"], isinstance(entry["mtime_ns"], int)))
        return False

    (root / "x.py").write_text("print(1)\n", encoding="utf-8")
    history.record_scan(None, root, seen=history.RunSeen(who=WHO, run=RUN, owns=owns))
    assert asked == [("x.py", 9, hashlib.sha256(b"print(1)\n").hexdigest(), True)]


def test_the_runs_attribution_after_it_adds_nothing_for_a_content_a_scan_holds(root, watched):
    path = root / "made.py"
    path.write_text("print(1)\n", encoding="utf-8")
    watched.add("made.py")
    history.record_scan(None, root)
    note = f"seen during run {RUN}"
    with history.Store.open(_pid(root)) as store:
        assert history.record_file(store, root, "made.py", source="agent", who=WHO, note=RUN, unless_noted=note) == "unchanged"
        # other bytes, another agent, or a version of another kind are not that very record
        path.write_text("print(2)\n", encoding="utf-8")
        assert history.record_file(store, root, "made.py", source="agent", who=WHO, note=RUN, unless_noted=note) == "recorded"
        assert history.record_file(store, root, "made.py", source="agent", who=WHO, note=RUN, unless_noted=note) == "unchanged"
        assert history.record_file(store, root, "made.py", source="agent", who="agent:opencode:x", note=RUN,
                                   unless_noted=note) == "recorded"
        # without `unless_noted` an agent's record is always added, as before
        assert history.record_file(store, root, "made.py", source="agent", who=WHO, note=RUN) == "recorded"
    assert [r[2] for r in _rows(root, "made.py")] == [note, RUN, RUN, RUN]


def test_the_stored_note_reads_in_the_request_language(root):
    note = f"seen during run {RUN}"
    versions = [_version(WHO, note=note), _version(WHO, note=RUN), {"who": WHO, "source": "scan", "note": note}]
    zh = history.name_people(root, [dict(v) for v in versions], "zh")
    en = history.name_people(root, [dict(v) for v in versions], "en")
    assert zh[0]["note"] == f"在运行 {RUN} 期间看到" and en[0]["note"] == f"Seen during run {RUN}"
    assert zh[1]["note"] == RUN == en[1]["note"]  # an ordinary run id is left as it is
    assert zh[2]["note"] == note  # only an agent's version is read that way


def test_the_save_of_a_person_keeps_their_name_and_the_pre_image_is_the_runs(root, watched):
    created = workspace.save_document(root, {"kind": "note", "title": "N", "content": "first", "revision": None})
    rel = created["path"]
    (root / rel).write_text("# N\n\nthe agent wrote this\n", encoding="utf-8")  # behind Research OS's back
    watched.add(rel)
    current = workspace.document(root, rel)
    workspace.save_document(root, {"path": rel, "content": "# N\n\nthe person's words", "revision": current["revision"]})
    rows = _rows(root, rel)
    # oldest first: the person's first save, what the run left there, the person's second save
    assert [r[0] for r in rows] == ["scan", "agent", "scan"]
    assert rows[1][1:] == (WHO, f"seen during run {RUN}")
    assert rows[2][1] == history.scan_who(root)


# -- the command line ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [["status", "."], ["serve"], ["mcp"], ["mcp", "--path", "."], [], ["--help"],
                                  ["mcp-agent-run"], ["status", "--agent-run", "x"]])
def test_cli_refuses_everything_in_a_run(argv, in_run, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(cli.rename, "ensure_home_renamed", lambda: called.append("rename"))
    assert cli.main(argv) == 1
    err = capsys.readouterr().err
    assert "not available inside an AI agent run" in err and "RESEARCHOS_AGENT_RUN" in err
    assert called == []  # before anything that may write


def test_legacy_command_is_refused_too(in_run, capsys):
    assert cli.legacy_main(["status", "."]) == 1
    assert "not available inside an AI agent run" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["mcp", "--agent-run", "r-1"], ["mcp", "--agent-run=r-1", "--path", "."]])
def test_cli_lets_the_agent_mcp_mode_through(argv, in_run, capsys, monkeypatch):
    received = []
    fake = SimpleNamespace(main=lambda rest: received.append(rest) or 0)
    monkeypatch.setattr(cli, "_import_mcp_server", lambda: fake)
    assert cli.main(argv) == 0
    assert received == [argv[1:]]
    assert "not available inside an AI agent run" not in capsys.readouterr().err


def test_cli_unaffected_without_the_variable(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(situation.AGENT_RUN_ENV, raising=False)
    received = []
    monkeypatch.setattr(cli, "_import_mcp_server", lambda: SimpleNamespace(main=lambda rest: received.append(rest) or 0))
    assert cli.main(["mcp", "--path", str(tmp_path)]) == 0 and received == [["--path", str(tmp_path)]]
    assert "not available inside" not in capsys.readouterr().err

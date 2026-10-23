"""DESIGN.md 18.4 items 2 and 3 (task V14, G4-2): agent runs and polling.

Nothing here changes what a payload says. Each test sets the faster path beside the way it read before and
asks for the same answer: the after-picture against a full read of the folder, the run's detail read from a
cursor against the whole log parsed again (a verbatim copy of the old `_steps` stands for "before"), the runs
listing against the listing it made with three reads of every `run.json`. The page's timers run under node.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from researchos.agents import manager, runners, snapshot
from researchos.agents import runs as run_store

PID = "p-" + "0123456789abcdef" * 2
HOUR_NS = 3600 * 1_000_000_000


# -- the after-picture reuses a hash whose stamp is unchanged (18.4 item 2) -----------------------------------------


def _project(root: Path, files: dict[str, str | bytes], *, age_ns: int = HOUR_NS) -> Path:
    """A project folder whose files were last changed `age_ns` ago (an hour: well clear of `snapshot.RACY_NS`)."""
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        then = time.time_ns() - age_ns
        os.utime(target, ns=(then, then))
    return root


@pytest.fixture
def reads(monkeypatch) -> list[str]:
    """The paths `snapshot` opens to hash, in order."""
    seen: list[str] = []
    real = snapshot._hash_and_copy

    def counting(full: str, size: int, copy_dest: str | None):
        seen.append(full)
        return real(full, size, copy_dest)

    monkeypatch.setattr(snapshot, "_hash_and_copy", counting)
    return seen


def _rels(root: Path, seen: list[str]) -> list[str]:
    return sorted(os.path.relpath(p, root).replace(os.sep, "/") for p in seen)


FILES = {"data/a.csv": "1,2\n3,4\n", "code/run.py": "print(1)\n", "notes.md": "hello", "中文/说明.md": "你好"}


def test_an_unchanged_file_is_not_read_again_and_the_picture_is_the_full_read(tmp_path, reads):
    root = _project(tmp_path / "proj", FILES)
    before = snapshot.take(root, copy_to=tmp_path / "copies")
    assert len(reads) == len(FILES) and all("ino" in e for e in before["files"].values())
    reads.clear()
    after = snapshot.take(root, reuse=before)
    assert reads == []  # not one file was opened
    assert after == snapshot.take(root)  # and the picture is what reading every file gives, `ino` included
    assert len(reads) == len(FILES)
    assert snapshot.compare(before, after) == {"created": [], "modified": [], "deleted": []}


def test_a_picture_that_went_through_json_still_lets_the_after_picture_reuse(tmp_path, reads):
    """A recovered run reads its before-picture back from `before.json`."""
    root = _project(tmp_path / "proj", FILES)
    stored = json.loads(json.dumps(snapshot.take(root, copy_to=tmp_path / "copies")))
    reads.clear()
    assert snapshot.take(root, reuse=stored) == snapshot.take(root)
    assert len(reads) == len(FILES)  # only the second take, the comparison, read


def test_a_touched_file_with_the_same_content_is_read_again_and_stays_unchanged(tmp_path, reads):
    root = _project(tmp_path / "proj", FILES)
    before = snapshot.take(root, copy_to=tmp_path / "copies")
    reads.clear()
    later = time.time_ns() - HOUR_NS // 2
    os.utime(root / "notes.md", ns=(later, later))
    after = snapshot.take(root, reuse=before)
    assert _rels(root, reads) == ["notes.md"]
    assert after["files"]["notes.md"]["mtime_ns"] == later
    assert after["files"]["notes.md"]["sha256"] == before["files"]["notes.md"]["sha256"]
    assert snapshot.compare(before, after) == {"created": [], "modified": [], "deleted": []}
    reads.clear()
    assert after == snapshot.take(root)


def test_a_changed_file_of_the_same_size_is_read_again_and_found(tmp_path, reads):
    root = _project(tmp_path / "proj", FILES)
    before = snapshot.take(root, copy_to=tmp_path / "copies")
    reads.clear()
    (root / "data/a.csv").write_bytes(b"9,9\n3,4\n")  # the same 8 bytes, the time is now
    after = snapshot.take(root, reuse=before)
    assert _rels(root, reads) == ["data/a.csv"]
    assert after["files"]["data/a.csv"]["sha256"] != before["files"]["data/a.csv"]["sha256"]
    assert snapshot.compare(before, after)["modified"] == ["data/a.csv"]


def test_a_file_replaced_by_another_inode_with_the_same_size_and_time_is_read_again_and_found(tmp_path, reads):
    root = _project(tmp_path / "proj", FILES)
    before = snapshot.take(root, copy_to=tmp_path / "copies")
    old = os.lstat(root / "notes.md")
    reads.clear()
    other = root / "notes.md.new"  # an editor's atomic save: a new file of the same size, renamed over the old
    other.write_bytes(b"HELLO")
    os.utime(other, ns=(old.st_atime_ns, old.st_mtime_ns))
    os.replace(other, root / "notes.md")
    now = os.lstat(root / "notes.md")
    assert (now.st_size, now.st_mtime_ns) == (old.st_size, old.st_mtime_ns) and now.st_ino != old.st_ino
    after = snapshot.take(root, reuse=before)
    assert _rels(root, reads) == ["notes.md"]
    assert snapshot.compare(before, after)["modified"] == ["notes.md"]


def test_a_file_new_to_the_folder_is_read_and_a_deleted_one_is_gone(tmp_path, reads):
    root = _project(tmp_path / "proj", FILES)
    before = snapshot.take(root, copy_to=tmp_path / "copies")
    reads.clear()
    (root / "new.txt").write_text("new", encoding="utf-8")
    (root / "notes.md").unlink()
    after = snapshot.take(root, reuse=before)
    assert _rels(root, reads) == ["new.txt"]
    assert snapshot.compare(before, after) == {"created": ["new.txt"], "modified": [], "deleted": ["notes.md"]}


def test_a_file_changed_just_before_the_picture_is_never_trusted(tmp_path, reads):
    """A stamp is as fine as the file system keeps it: a file written within `RACY_NS` of being read could be written
    again with the very same time. Its entry carries no `ino`, so the after-picture reads it, and finds the change."""
    root = _project(tmp_path / "proj", {"fresh.csv": "1,2\n", "old.csv": "5,6\n"})
    fresh = root / "fresh.csv"
    now = time.time_ns()
    os.utime(fresh, ns=(now, now))
    before = snapshot.take(root, copy_to=tmp_path / "copies")
    assert "ino" not in before["files"]["fresh.csv"] and "ino" in before["files"]["old.csv"]
    stamp = os.lstat(fresh)
    with open(fresh, "r+b") as handle:  # in place, the same size, and the time put back
        handle.write(b"9,9\n")
    os.utime(fresh, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert os.lstat(fresh).st_ino == stamp.st_ino and os.lstat(fresh).st_mtime_ns == stamp.st_mtime_ns
    reads.clear()
    after = snapshot.take(root, reuse=before)
    assert _rels(root, reads) == ["fresh.csv"]
    assert snapshot.compare(before, after)["modified"] == ["fresh.csv"]


PROTECTED_FILES = {"data/survey.csv": "id,val\n1,10\n2,20\n", ".researchos/attempts.toml": "file = 'attempts.csv'\n",
                   ".claude/settings.json": '{"hooks": {"a": 1}}\n'}


def _violations(before: dict, after: dict) -> list[dict]:
    return snapshot.violations(snapshot.compare(before, after), before["protected"], git_before=before.get("git"),
                               git_after=after.get("git"), allowed={}, after=after, data_before=before["data"],
                               data_after=after["data"], backups_before=before["backups"],
                               backups_after=after["backups"])


def test_a_protected_file_rewritten_in_place_with_its_time_put_back_is_read_again_and_out_of_bounds(tmp_path, reads):
    """`touch -r`, `cp -p`, `rsync -t` or a program's own `os.utime` after an in-place write of the same size leave the
    size, the modification time and the inode as they were -- the status-change time (`ctime`) cannot be put back, so
    the stamp differs, the file is hashed, and the check that 18.1 relies on for an agent that bypassed the hook
    (OpenCode has none) sees the change."""
    root = _project(tmp_path / "proj", {**PROTECTED_FILES, "notes.md": "hello"})
    before = snapshot.take(root, copy_to=tmp_path / "copies")
    before["protected"] = snapshot.protected(root, before)
    assert all("ctime_ns" in e for e in before["files"].values()) and "data/survey.csv" in before["protected"]["data"]
    for rel in PROTECTED_FILES:
        path = root / rel
        was = os.lstat(path)
        with open(path, "r+b") as handle:  # in place, the same size
            handle.write(b"X")
        os.utime(path, ns=(was.st_atime_ns, was.st_mtime_ns))
        now = os.lstat(path)
        assert (now.st_ino, now.st_size, now.st_mtime_ns) == (was.st_ino, was.st_size, was.st_mtime_ns)  # the old stamp
        assert now.st_ctime_ns != was.st_ctime_ns
    reads.clear()
    after = snapshot.take(root, reuse=before)
    assert _rels(root, reads) == sorted(PROTECTED_FILES)  # the three are read; notes.md is not
    assert after == snapshot.take(root)
    assert snapshot.compare(before, after)["modified"] == sorted(PROTECTED_FILES)
    assert {(v["path"], v["reason"]) for v in _violations(before, after)} == {
        ("data/survey.csv", "data_changed"), (".researchos/attempts.toml", "researchos_changed"),
        (".claude/settings.json", "agent_settings_changed")}


def test_a_picture_without_status_change_times_reuses_nothing(tmp_path, reads):
    """A before-picture stored by the version before this one has `ino` but no `ctime_ns`: read in full."""
    root = _project(tmp_path / "proj", FILES)
    before = snapshot.take(root, copy_to=tmp_path / "copies")
    for entry in before["files"].values():
        del entry["ctime_ns"]
    reads.clear()
    assert snapshot.take(root, reuse=before)["files"] == snapshot.take(root)["files"]
    assert len(reads) == 2 * len(FILES)


def test_a_file_whose_status_changed_without_its_content_is_read_again_and_stays_unchanged(tmp_path, reads):
    root = _project(tmp_path / "proj", FILES)
    before = snapshot.take(root, copy_to=tmp_path / "copies")
    os.chmod(root / "notes.md", 0o600)  # the mode moves the status-change time, nothing else
    reads.clear()
    after = snapshot.take(root, reuse=before)
    assert _rels(root, reads) == ["notes.md"]
    assert snapshot.compare(before, after) == {"created": [], "modified": [], "deleted": []}


def test_a_time_in_the_future_is_racy_too(tmp_path):
    root = _project(tmp_path / "proj", {"ahead.csv": "1\n"}, age_ns=-HOUR_NS)
    assert "ino" not in snapshot.take(root)["files"]["ahead.csv"]


def test_a_picture_without_inodes_reuses_nothing(tmp_path, reads):
    """A before-picture stored before this existed, or by anything else, is read in full as it always was."""
    root = _project(tmp_path / "proj", FILES)
    before = snapshot.take(root, copy_to=tmp_path / "copies")
    for entry in before["files"].values():
        del entry["ino"]
    reads.clear()
    assert snapshot.take(root, reuse=before)["files"] == snapshot.take(root)["files"]
    assert len(reads) == 2 * len(FILES)


def test_a_file_that_is_copied_is_read_even_when_the_stamp_is_the_same(tmp_path, reads):
    root = _project(tmp_path / "proj", FILES)
    before = snapshot.take(root)
    reads.clear()
    picture = snapshot.take(root, copy_to=tmp_path / "copies", reuse=before)
    assert len(reads) == len(FILES)
    assert all(e.get("copied") for e in picture["files"].values())
    assert (tmp_path / "copies" / "notes.md").read_text(encoding="utf-8") == "hello"


def test_links_clouds_and_big_files_are_recorded_as_before(tmp_path, reads, monkeypatch):
    monkeypatch.setattr(snapshot, "HASH_LIMIT", 6)  # "notes.md" (5 bytes) is hashed, "data/a.csv" (8) is too big
    root = _project(tmp_path / "proj", FILES)
    os.symlink("notes.md", root / "link.md")
    before = snapshot.take(root, copy_to=tmp_path / "copies")
    assert "sha256" not in before["files"]["data/a.csv"] and "ino" not in before["files"]["data/a.csv"]
    assert before["files"]["link.md"]["link"] == "notes.md" and "ino" not in before["files"]["link.md"]
    reads.clear()
    after = snapshot.take(root, reuse=before)
    assert reads == []  # the two files over the limit never had a hash to reuse and are not read; the others are not read again
    assert after == snapshot.take(root)
    # a placeholder in the cloud is never opened, with or without a picture to reuse
    monkeypatch.setattr(snapshot.paths, "is_dataless", lambda full: str(full).endswith("notes.md"))
    reads.clear()
    cloud = snapshot.take(root, reuse=before)
    assert cloud["files"]["notes.md"].get("dataless") is True and "sha256" not in cloud["files"]["notes.md"]
    assert not any(p.endswith("notes.md") for p in reads)


def test_the_run_manager_takes_its_after_picture_from_the_before_picture(tmp_path, reads):
    """`_assess` on a run whose folder did not change reads no file, and says what it said when it read them all."""
    root = _project(tmp_path / "proj", FILES)
    run = run_store.create_run(PID, {"id": run_store.new_run_id(), "root": str(root), "state": "running",
                                     "task_id": "t1", "runner": "claude-code"})
    rd = run_store.run_dir(PID, run["id"])
    before = snapshot.take(root, copy_to=rd / "before")
    before["protected"] = snapshot.protected(root, before)
    (root / "code/run.py").write_text("print(2)\n", encoding="utf-8")  # one change, to have something to report
    mgr = manager.AgentManager()
    reads.clear()
    with_reuse = mgr._assess(root, PID, run["id"], run, before, lambda: [])
    assert _rels(root, reads) == ["code/run.py"]

    real_take = snapshot.take
    seen_kwargs: list[dict] = []

    def full_read(root_, copy_to=None, *, reuse=None):
        seen_kwargs.append({"reuse": reuse})
        return real_take(root_, copy_to)

    try:
        snapshot.take = full_read  # type: ignore[assignment]
        reads.clear()
        without = mgr._assess(root, PID, run["id"], run, before, lambda: [])
    finally:
        snapshot.take = real_take  # type: ignore[assignment]
    assert len(reads) == len(FILES) and seen_kwargs and seen_kwargs[0]["reuse"] is before
    assert with_reuse == without
    assert with_reuse["changes"]["modified"] == ["code/run.py"]


# -- the run's detail is read from a cursor in its log, with the same answer (18.4 item 2) -----------------------------


def _old_steps(events_path: Path, run: dict) -> tuple[list[dict], int]:
    """`AgentManager._steps` as it was before the cursor (HEAD of task G4-1): the whole log read and parsed again."""
    stream: runners.ClaudeStream | runners.OpenCodeStream = (
        runners.ClaudeStream() if run.get("runner") == "claude-code" else runners.OpenCodeStream())
    raw = manager._read_text(events_path, run_store.EVENTS_LIMIT)
    for line in raw.splitlines():
        stream.feed(line)
    steps = list(stream.steps)
    prefixes = sorted({os.path.join(str(p), "") for p in (run.get("root"), os.path.realpath(str(run.get("root"))))
                       if p and p != "None"}, key=len, reverse=True)
    if prefixes:
        for step in steps:
            for prefix in prefixes:
                step["text"] = step["text"].replace(prefix, "")
    state = run.get("state")
    outcome = run_store.outcome_of(run)
    error = run.get("error") if isinstance(run.get("error"), dict) else {}
    code = error.get("code") if isinstance(error.get("code"), str) and error.get("code") else None
    if code and outcome in ("failed", "interrupted") and steps and steps[-1]["kind"] == "end":
        steps[-1]["code"] = code
    if state not in run_store.ACTIVE_STATES and not (steps and steps[-1]["kind"] == "end"):
        en = manager._lang(run.get("lang")) == "en"
        end_code: str | None = None
        if outcome == "stopped":
            text, end_code = "", "stopped"
        elif error and (code or outcome != "interrupted"):
            text, end_code = str(error.get("detail") or ""), code
        elif outcome == "interrupted":
            text = ("Interrupted: Research OS was closed while the run was going." if en
                    else "已中断：运行期间 Research OS 被关闭了。")
        else:
            text = (run.get("summary") or "").strip() or ("Ended." if en else "已结束。")
        end = {"kind": "end", "text": " ".join(text.split())[:runners.STEP_TEXT_LIMIT], "tool": None}
        if end_code:
            end["code"] = end_code
        steps.append(end)
    total = len(steps)
    return steps[-manager.STEPS_LIMIT:], total


class _WholeLogManager(manager.AgentManager):
    """The manager as it read a run's log before: all of it, every time."""

    @staticmethod
    def _steps(events_path, run, cursor=None):  # type: ignore[override]
        return _old_steps(events_path, run)


def _claude(kind: str, i: int) -> dict:
    if kind == "init":
        return {"type": "system", "subtype": "init", "session_id": "s-1", "model": "claude-fake", "mcp_servers": []}
    if kind == "say":
        return {"type": "assistant", "session_id": "s-1",
                "message": {"content": [{"type": "text", "text": f"Thought {i}: look at /work/proj/data/a.csv"}]}}
    if kind == "tool":
        return {"type": "assistant", "session_id": "s-1", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": f"ls /work/proj/{i}"}}]}}
    if kind == "refused":
        return {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True, "content":
                "PreToolUse:Write [hook] Research OS: data/a.csv is protected (protected_path)"}]}}
    if kind == "result":
        return {"type": "result", "subtype": "success", "is_error": False, "session_id": "s-1",
                "result": "The last thing said", "total_cost_usd": 0.02}
    raise AssertionError(kind)


def _opencode(kind: str, i: int) -> dict:
    if kind == "init":
        return {"type": "step_start", "sessionID": "s-1", "part": {"type": "step-start"}}
    if kind == "say":
        return {"type": "text", "sessionID": "s-1", "part": {"type": "text", "text": f"Thought {i}: /work/proj/x"}}
    if kind == "tool":
        return {"type": "tool_use", "sessionID": "s-1", "part": {"type": "tool", "tool": "bash", "state": {
            "status": "completed", "input": {"command": f"ls /work/proj/{i}"}, "output": "ok"}}}
    if kind == "refused":
        return {"type": "tool_use", "sessionID": "s-1", "part": {"type": "tool", "tool": "edit", "state": {
            "status": "error", "input": {"filePath": "/work/proj/a.csv"},
            "error": "The user has specified a rule which prevents you from using this tool. Here are the rules"}}}
    if kind == "result":
        return {"type": "step_finish", "sessionID": "s-1", "part": {"cost": 0.01}}
    raise AssertionError(kind)


def _log(runner: str, count: int, *, ending: bool = True) -> list[str]:
    """`count` events of a program's stream as lines of text: a start, then thoughts, tool calls and the odd refusal."""
    make = _claude if runner == "claude-code" else _opencode
    kinds = ["init"] + [("say" if i % 5 == 0 else "refused" if i % 13 == 0 else "tool") for i in range(1, count)]
    if ending:
        kinds.append("result")
    return [json.dumps(make(kind, i), ensure_ascii=False) for i, kind in enumerate(kinds)]


RUNNERS = ["claude-code", "opencode"]


class Scene:
    """A run in the run store with a log of its own, and a manager reading it by cursor and one that reads it whole."""

    def __init__(self, tmp_path: Path, runner: str, **fields) -> None:
        self.root = tmp_path / "proj"
        self.root.mkdir(exist_ok=True)
        self.runner = runner
        run = run_store.create_run(PID, {
            "id": run_store.new_run_id(), "root": str(self.root), "task_id": "t1", "runner": runner,
            "state": "running", "lang": "en", "owner": "agent:" + runner + ":m", **fields})
        self.id = run["id"]
        self.log = run_store.run_dir(PID, self.id) / "events.jsonl"
        self.new = manager.AgentManager()
        self.old = _WholeLogManager()

    def append(self, lines: list[str], *, newline: bool = True) -> None:
        with open(self.log, "ab") as handle:
            handle.write(("\n".join(lines) + ("\n" if newline else "")).encode("utf-8"))

    def same(self) -> dict:
        """The detail both managers give, which must be equal; returns it."""
        new, old = self.new.detail(PID, self.id), self.old.detail(PID, self.id)
        assert new == old
        assert json.dumps(new, sort_keys=True, ensure_ascii=False) == json.dumps(old, sort_keys=True, ensure_ascii=False)
        return new


@pytest.fixture
def lines_fed(monkeypatch) -> list[str]:
    """Every line the stream parsers are given, in order."""
    fed: list[str] = []
    for cls in (runners.ClaudeStream, runners.OpenCodeStream):
        real = cls.feed

        def counting(self, line, _real=real):
            fed.append(line)
            return _real(self, line)

        monkeypatch.setattr(cls, "feed", counting)
    return fed


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_log_that_grows_between_polls_reads_as_a_whole_read_does(tmp_path, runner):
    scene = Scene(tmp_path, runner)
    assert scene.same()["steps"] == [] and scene.same()["steps_total"] == 0  # no log yet
    lines = _log(runner, 60, ending=False)
    for start in range(0, len(lines), 7):  # a few lines at a time, the way a program writes
        scene.append(lines[start:start + 7])
        detail = scene.same()
    assert detail["steps_total"] > 40 and detail["steps"][0]["kind"] == "start"
    scene.append(_log(runner, 2, ending=True)[-1:])
    ended = scene.same()
    if runner == "claude-code":  # OpenCode prints no closing event: the manager adds the end once the run is over
        assert ended["steps"][-1]["kind"] == "end"
    run_store.update_run(PID, scene.id, state="done")  # finished: the same log, a different state
    scene.same()


@pytest.mark.parametrize("runner", RUNNERS)
def test_each_line_is_parsed_once_however_often_the_run_is_polled(tmp_path, runner, lines_fed):
    scene = Scene(tmp_path, runner)
    lines = _log(runner, 40, ending=False)
    scene.append(lines[:25])
    for _ in range(5):
        scene.new.detail(PID, scene.id)
    scene.append(lines[25:])
    for _ in range(5):
        scene.new.detail(PID, scene.id)
    assert lines_fed == lines


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_line_still_being_written_is_read_as_it_stands_and_again_when_it_is_whole(tmp_path, runner):
    scene = Scene(tmp_path, runner)
    lines = _log(runner, 12, ending=False)
    scene.append(lines[:8])
    scene.same()
    half = lines[8][:len(lines[8]) // 2]
    scene.append([half], newline=False)  # cut mid-line: not an event yet
    cut = scene.same()
    scene.append([lines[8][len(half):]], newline=False)  # now the whole event, but the newline has not come
    whole = scene.same()
    assert whole["steps_total"] == cut["steps_total"] + 1
    scene.append(lines[9:11], newline=False)  # the line after it is glued on: one line that is no event
    scene.same()
    scene.append([""], newline=True)
    scene.same()
    scene.append(lines[11:])
    scene.same()


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_log_that_was_rotated_truncated_or_rewritten_is_read_afresh(tmp_path, runner):
    scene = Scene(tmp_path, runner)
    first = _log(runner, 30, ending=False)
    scene.append(first)
    before = scene.same()
    # rotated: the file is a new one (another inode) and is as long as the old
    other = scene.log.with_name("events.new")
    other.write_bytes(("\n".join(_log(runner, 30, ending=False)[::-1]) + "\n").encode("utf-8"))
    os.replace(other, scene.log)
    rotated = scene.same()
    assert rotated["steps"] != before["steps"]
    # rotated to a file that ends with the same bytes the cursor remembers, but differs earlier: only the inode says so
    now = scene.log.read_bytes().splitlines()
    blank = b"{" + b" " * (len(now[1]) - 2) + b"}"  # an event of no interest, as long as the one it replaces
    other.write_bytes(b"\n".join([now[0], blank, *now[2:]]) + b"\n")
    os.replace(other, scene.log)
    scene.same()
    # truncated, in place: shorter than where the cursor stood
    with open(scene.log, "r+b") as handle:
        handle.truncate(300)
    scene.same()
    scene.append(_log(runner, 6, ending=False))
    scene.same()
    # rewritten in place to something longer (the same inode, not shorter): the bytes before the cursor differ
    size = scene.log.stat().st_size
    ino = scene.log.stat().st_ino
    scene.log.write_bytes(("\n".join(_log(runner, 50, ending=False)[::-1]) + "\n").encode("utf-8"))
    assert scene.log.stat().st_ino == ino and scene.log.stat().st_size > size
    scene.same()
    # removed, then made again
    scene.log.unlink()
    assert scene.same()["steps"] == []
    scene.append(_log(runner, 5, ending=False))
    assert scene.same()["steps_total"] == 5


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_long_log_reads_as_a_whole_read_does_and_the_cursor_stays_small(tmp_path, runner):
    scene = Scene(tmp_path, runner)
    lines = _log(runner, 1700, ending=False)
    # the last thing said is far behind the end: the end of the stream is compared with it
    if runner == "claude-code":
        lines = lines[:300] + [json.dumps(_claude("say", 1), ensure_ascii=False)] + [
            json.dumps(_claude("tool", i)) for i in range(2000)]
        lines.append(json.dumps({**_claude("result", 0), "result": "Thought 1: look at /work/proj/data/a.csv"}))
    scene.append(lines[:900])
    scene.same()
    scene.append(lines[900:1400])
    scene.same()
    scene.append(lines[1400:])
    detail = scene.same()
    assert detail["steps_total"] > manager.STEPS_LIMIT * 2 and len(detail["steps"]) == manager.STEPS_LIMIT
    cursor = scene.new._cursors[(PID, scene.id)]
    assert len(cursor.stream.steps) <= manager.STEPS_LIMIT + manager.CURSOR_SLACK + 1
    assert cursor.dropped + len(cursor.stream.steps) == detail["steps_total"]
    if runner == "claude-code":  # the final answer repeats the newest `say`, so the end shows none of it
        assert detail["steps"][-1] == {"kind": "end", "text": "", "tool": None}


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_run_that_ended_without_a_closing_line_reads_as_it_did(tmp_path, runner):
    root = tmp_path / "proj"
    for state, extra in (("done", {"summary": "All done"}), ("failed", {"error": {"code": "start_failed", "detail": "no program"}}),
                         ("stopped", {}), ("interrupted", {"error": {"code": "engine_gone", "detail": "gone"}}),
                         ("interrupted", {}), ("running", {})):
        scene = Scene(tmp_path, runner, state=state, root=str(root), **extra)
        scene.append(_log(runner, 6, ending=False))
        scene.same()
        scene.same()
    for lang in ("zh", "en"):
        scene = Scene(tmp_path, runner, state="interrupted", lang=lang)
        scene.append(_log(runner, 3, ending=False))
        scene.same()


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_failed_run_whose_log_ends_with_a_closing_line_gets_its_code(tmp_path, runner):
    scene = Scene(tmp_path, runner, state="failed", error={"code": "internal", "detail": "x"})
    scene.append(_log(runner, 8, ending=True) if runner == "claude-code" else _log(runner, 8, ending=False)
                 + [json.dumps({"type": "error", "sessionID": "s-1", "error": {"name": "X", "data": {"message": "bad"}}})])
    scene.same()


@pytest.mark.parametrize("runner", RUNNERS)
def test_only_the_first_events_limit_bytes_of_a_log_count_as_before(tmp_path, runner, monkeypatch):
    monkeypatch.setattr(run_store, "EVENTS_LIMIT", 2500)
    scene = Scene(tmp_path, runner)
    lines = _log(runner, 60, ending=False)
    scene.append(lines[:10])
    scene.same()
    scene.append(lines[10:])  # far over the limit; the limit falls inside a line
    assert scene.log.stat().st_size > 2500
    scene.same()
    scene.append(lines[:5])
    scene.same()


@pytest.mark.parametrize("runner", RUNNERS)
def test_lines_are_split_as_a_whole_read_split_them(tmp_path, runner):
    """`str.splitlines` also cuts at U+2028, a lone CR and the like: an event holding one is lost, now as before; and
    bytes that are not UTF-8 read as replacement characters."""
    scene = Scene(tmp_path, runner)
    make = _claude if runner == "claude-code" else _opencode
    odd = json.dumps(make("say", 1), ensure_ascii=False).replace("Thought 1", "Thought 1")
    scene.append(_log(runner, 4, ending=False))
    scene.append([odd, json.dumps(make("say", 2)).replace("Thought", "Thought\r"), "\r"])
    with open(scene.log, "ab") as handle:
        handle.write(json.dumps(make("say", 3)).encode("utf-8")[:-4] + b"\xff\xfe" + b"\n")
        handle.write(json.dumps(make("say", 4), ensure_ascii=False).replace("Thought", "café").encode("utf-8") + b"\n")
    scene.same()
    scene.append(_log(runner, 3, ending=False)[1:])
    scene.same()


def test_a_log_in_the_cloud_has_no_steps(tmp_path, monkeypatch):
    scene = Scene(tmp_path, "claude-code")
    scene.append(_log("claude-code", 5, ending=False))
    assert scene.new.detail(PID, scene.id)["steps_total"] == 5
    with monkeypatch.context() as patched:
        patched.setattr(manager.paths, "is_dataless", lambda path: str(path).endswith("events.jsonl"))
        assert scene.new.detail(PID, scene.id)["steps_total"] == 0
    assert scene.new.detail(PID, scene.id)["steps_total"] == 5


def test_a_cursor_that_fails_midway_starts_over_instead_of_going_on_from_half_a_batch(tmp_path, monkeypatch):
    scene = Scene(tmp_path, "claude-code")
    lines = _log("claude-code", 10, ending=False)
    scene.append(lines[:4])
    scene.same()
    real = runners.ClaudeStream.feed
    calls = {"n": 0}

    def breaks(self, line):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("a parser bug")
        return real(self, line)

    monkeypatch.setattr(runners.ClaudeStream, "feed", breaks)
    scene.append(lines[4:])
    with pytest.raises(RuntimeError):
        scene.new.detail(PID, scene.id)
    monkeypatch.setattr(runners.ClaudeStream, "feed", real)
    scene.same()  # the next poll reads the log from its start and agrees with a whole read


def test_the_cursors_kept_are_bounded_and_a_run_of_another_program_gets_its_own(tmp_path):
    mgr = manager.AgentManager()
    scenes = [Scene(tmp_path, "claude-code") for _ in range(manager.CURSORS_KEPT + 4)]
    for scene in scenes:
        scene.append(_log("claude-code", 4, ending=False))
        mgr.detail(PID, scene.id)
    assert len(mgr._cursors) == manager.CURSORS_KEPT
    assert [key[1] for key in mgr._cursors] == [s.id for s in scenes[-manager.CURSORS_KEPT:]]
    # asking for the oldest one again works (a new cursor) and makes it the newest
    assert mgr.detail(PID, scenes[0].id)["steps_total"] == 4 and list(mgr._cursors)[-1][1] == scenes[0].id
    # the same run read as another program's (the stored runner changed) is not read with the first one's parser
    run_store.update_run(PID, scenes[0].id, runner="opencode")
    assert mgr._cursor(PID, scenes[0].id, "opencode").runner == "opencode"


def test_polls_from_several_threads_while_the_log_grows_all_agree_with_a_whole_read(tmp_path):
    scene = Scene(tmp_path, "claude-code")
    lines = _log("claude-code", 400, ending=False)
    stop = threading.Event()
    problems: list[BaseException] = []
    seen: dict[int, list[int]] = {n: [] for n in range(6)}

    def poll(n: int) -> None:
        try:
            while not stop.is_set():
                seen[n].append(scene.new.detail(PID, scene.id)["steps_total"])
        except BaseException as exc:  # noqa: BLE001
            problems.append(exc)

    threads = [threading.Thread(target=poll, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    try:
        for start in range(0, len(lines), 20):
            scene.append(lines[start:start + 20])
            time.sleep(0.003)
        deadline = time.monotonic() + 10  # every thread has asked a few times, whatever the machine is doing
        while time.monotonic() < deadline and min(len(counts) for counts in seen.values()) < 5:
            time.sleep(0.01)
    finally:
        stop.set()
        for t in threads:
            t.join(20)
    assert not problems
    assert all(counts == sorted(counts) for counts in seen.values())  # the log only grows: so does what is read
    assert all(len(counts) >= 5 for counts in seen.values())
    assert scene.same()["steps_total"] == len(lines)  # every event of this stream is one step


# -- the runs of a project are listed once for a request (18.4 item 2) -------------------------------------------------


def _old_runs(mgr: manager.AgentManager, project_id: str, *, recover: bool = True) -> dict:
    """`AgentManager.runs` as it was: recovery lists the project, and the listing lists it again."""
    foreign = mgr._recover(project_id) if recover else mgr._foreign_active(project_id)
    with mgr._lock:
        act = mgr._active.get(project_id)
        active_id = act.run_id if act is not None else None
    out = []
    seen: set = set()
    for run in run_store.list_runs(project_id):
        latest = run.get("task_id") not in seen
        seen.add(run.get("task_id"))
        out.append(manager.summary(run, latest_of_task=latest, active=run["id"] == active_id))
    return {"runs": out, "active": active_id or (foreign[0] if foreign else None)}


def _old_summary_of(mgr: manager.AgentManager, project_id: str, run: dict) -> dict:
    latest = next((r for r in run_store.list_runs(project_id) if r.get("task_id") == run.get("task_id")), None)
    return manager.summary(run, latest_of_task=latest is not None and latest.get("id") == run.get("id"),
                           active=mgr._is_active(project_id, run["id"]))


def _make_runs(tmp_path: Path, tasks: int = 3, per_task: int = 4, **fields) -> list[dict]:
    """Finished runs of a few tasks, interleaved, with ids in creation order."""
    made = []
    for n in range(tasks * per_task):
        made.append(run_store.create_run(PID, {
            "id": f"r-20261001T0900{n:02d}Z-{n:06x}", "root": str(tmp_path / "proj"), "task_id": f"t{n % tasks}",
            "runner": "claude-code", "state": "done", "owner": "agent:claude-code:m", "session_id": "s-1",
            "report": "r", "revertible": True, "changes": {"created": ["a"], "modified": [], "deleted": []},
            "created": f"2026-10-01T09:00:{n:02d}Z", **fields}))
    return made


@pytest.fixture
def run_reads(monkeypatch) -> dict[str, int]:
    """How often the run store lists the project and how many `run.json` files it reads."""
    counts = {"list": 0, "iter": 0, "read": 0}
    real_list, real_iter, real_read = run_store.list_runs, run_store.iter_runs, run_store._read_run

    def listing(project_id):
        counts["list"] += 1
        return real_list(project_id)

    def iterating(project_id):
        counts["iter"] += 1
        return real_iter(project_id)

    def reading(directory, run_id):
        counts["read"] += 1
        return real_read(directory, run_id)

    monkeypatch.setattr(run_store, "list_runs", listing)
    monkeypatch.setattr(run_store, "iter_runs", iterating)
    monkeypatch.setattr(run_store, "_read_run", reading)
    return counts


@pytest.mark.parametrize("recover", [True, False])
def test_the_runs_listing_reads_each_run_once_and_says_what_it_said(tmp_path, run_reads, recover):
    made = _make_runs(tmp_path)
    mgr = manager.AgentManager()
    listed = mgr.runs(PID, recover=recover)
    assert run_reads["list"] == 1 and run_reads["read"] == len(made)  # not twice over
    assert [r["id"] for r in listed["runs"]] == [r["id"] for r in reversed(made)]
    assert listed == _old_runs(mgr, PID, recover=recover)
    assert [r["can_reply"] for r in listed["runs"]].count(True) == 3  # the newest of each task


def test_the_listing_shows_a_run_recovery_ended_and_a_run_of_another_engine_as_the_old_listing_did(tmp_path, run_reads):
    made = _make_runs(tmp_path, tasks=2, per_task=2)
    gone = subprocess.Popen(["true"])
    gone.wait()
    stale = run_store.create_run(PID, {"id": "r-20261001T091000Z-aaaaaa", "root": str(tmp_path / "proj"), "task_id": "t0",
                                       "runner": "claude-code", "state": "running", "owner": "agent:claude-code:m",
                                       "engine_pid": gone.pid, "created": "2026-10-01T09:10:00Z"})
    mgr = manager.AgentManager()
    run_reads.update(list=0, read=0)
    listed = mgr.runs(PID)
    assert run_reads["list"] == 1  # the runs it looked at are read again one by one, never listed again
    first = listed["runs"][0]
    assert first["id"] == stale["id"] and first["state"] == "interrupted" and listed["active"] is None
    assert run_store.load_run(PID, stale["id"])["error"]["code"] == "engine_gone"
    assert listed == _old_runs(mgr, PID)
    assert len(listed["runs"]) == len(made) + 1
    # a run another engine carries out is listed as it is, and is the active one
    other = subprocess.Popen(["sleep", "60"])
    try:
        alive = run_store.create_run(PID, {"id": "r-20261001T091100Z-bbbbbb", "root": str(tmp_path / "proj"),
                                           "task_id": "t1", "runner": "claude-code", "state": "running",
                                           "owner": "agent:claude-code:m", "engine_pid": other.pid,
                                           "engine_started": manager._start_of(other.pid),
                                           "created": manager._now()})
        run_reads.update(list=0, read=0)
        listed = mgr.runs(PID)
        assert run_reads["list"] == 1
        assert listed["active"] == alive["id"] and listed["runs"][0]["state"] == "running"
        assert listed == _old_runs(mgr, PID)
        assert mgr.runs(PID, recover=False) == _old_runs(mgr, PID, recover=False)
    finally:
        other.kill()
        other.wait()


def test_recovery_hands_back_the_runs_it_looked_at_as_they_are_now(tmp_path, monkeypatch):
    """The listing recovery works from may be older than a run's end: the run is read again, and one that cannot be
    read any more is left out, as a fresh listing would."""
    made = _make_runs(tmp_path, tasks=1, per_task=3)
    newest = made[-1]
    stale = dict(newest, state="running", ended=None)
    vanished = dict(made[0], state="preparing")
    run_store.save_run(PID, dict(made[0], state="done"))
    (run_store.run_dir(PID, made[0]["id"]) / "run.json").write_text("{not json", encoding="utf-8")  # unreadable now
    monkeypatch.setattr(run_store, "list_runs", lambda project_id: [stale, made[1], vanished])
    foreign, listing = manager.AgentManager()._recovered(PID)
    assert foreign == []
    assert [r["id"] for r in listing] == [newest["id"], made[1]["id"]]
    assert listing[0]["state"] == "done"  # what is on disk, not what the older listing said


def test_a_run_started_since_the_listing_is_in_the_runs(tmp_path):
    from types import SimpleNamespace

    made = _make_runs(tmp_path, tasks=1, per_task=2)
    fresh = run_store.create_run(PID, {"id": "r-20261001T092000Z-cccccc", "root": str(tmp_path / "proj"), "task_id": "t0",
                                       "runner": "claude-code", "state": "preparing", "owner": "agent:claude-code:m"})
    mgr = manager.AgentManager()
    mgr._recovered = lambda project_id: ([], [dict(r) for r in made])  # type: ignore[method-assign] -- listed before it began
    mgr._active[PID] = SimpleNamespace(run_id=fresh["id"])  # type: ignore[assignment]
    listed = mgr.runs(PID)
    assert listed["active"] == fresh["id"]
    assert [r["id"] for r in listed["runs"]][0] == fresh["id"] and listed["runs"][0]["can_stop"] is True


def test_a_runs_detail_reads_the_listing_no_further_than_the_newest_run_of_its_task(tmp_path, run_reads):
    made = _make_runs(tmp_path, tasks=3, per_task=10)  # 30 runs
    mgr = manager.AgentManager()
    newest = made[-1]
    run_reads.update(list=0, iter=0, read=0)
    detail = mgr.detail(PID, newest["id"])
    assert run_reads["list"] == 0 and run_reads["read"] == 2  # the run itself, and the first run of the listing
    assert detail["can_reply"] is True
    oldest = made[0]  # its task has later runs: the listing is read down to the newest of the task, which is not it
    run_reads.update(list=0, iter=0, read=0)
    assert mgr.detail(PID, oldest["id"])["can_reply"] is False
    assert run_reads["read"] <= 1 + 3  # the run and the newest runs down to the one of its task
    for run in made:  # and every summary is what the whole listing gave
        assert mgr._summary_of(PID, run) == _old_summary_of(mgr, PID, run)
        assert mgr.detail(PID, run["id"])["can_reply"] == _old_summary_of(mgr, PID, run)["can_reply"]


def test_iter_runs_is_the_listing_one_run_at_a_time(tmp_path):
    made = _make_runs(tmp_path, tasks=1, per_task=4)
    (run_store.run_dir(PID, made[1]["id"]) / "run.json").write_text("[]", encoding="utf-8")  # not a run
    assert [r["id"] for r in run_store.iter_runs(PID)] == [r["id"] for r in run_store.list_runs(PID)] \
        == [made[3]["id"], made[2]["id"], made[0]["id"]]
    walker = run_store.iter_runs(PID)
    assert next(walker)["id"] == made[3]["id"]
    (run_store.run_dir(PID, made[2]["id"]) / "run.json").unlink()  # read when asked for, not before
    assert next(walker)["id"] == made[0]["id"]
    with pytest.raises(ValueError):
        run_store.list_runs("not a project")
    assert run_store.list_runs("p-" + "f" * 32) == []


@pytest.mark.parametrize("runner", RUNNERS)
def test_steps_without_a_cursor_read_the_whole_log_and_keep_nothing(tmp_path, runner):
    scene = Scene(tmp_path, runner, state="done", summary="Fine")
    scene.append(_log(runner, 50, ending=False))
    run = run_store.load_run(PID, scene.id)
    assert manager.AgentManager._steps(scene.log, run) == _old_steps(scene.log, run)
    assert manager.AgentManager._steps(scene.log, run, manager._EventsCursor(runner)) == _old_steps(scene.log, run)
    assert scene.new._cursors == {}


# -- the run poll and the generation poll rest while the window is hidden (18.4 items 2 and 3) -------------------------

WEBAPP = Path(__file__).parents[1] / "src" / "researchos" / "webapp"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

#: A window that can be hidden: `document.hidden`, and the listeners of `visibilitychange` that the page and the
#: workspace module register; `intervals` are the page's `setInterval`s, taken over so that a test fires them by hand.
WINDOW = r"""
const docEvents = {};
document.hidden = false;
document.addEventListener = (k, f) => { (docEvents[k] ||= []).push(f); };
const setHidden = async (value) => {
  document.hidden = value;
  for (const f of docEvents.visibilitychange || []) await f();
  await sleepTick(); await sleepTick();
};
const intervals = [];
global.setInterval = (f, ms) => { const t = { f, ms, cleared: false }; intervals.push(t); return t; };
global.clearInterval = (t) => { if (t) t.cleared = true; };
const liveIntervals = () => intervals.filter((t) => !t.cleared);
"""


def _workspace_harness() -> str:
    """The `DOM` of tests/test_workspace_js.py: the real writing UI's small DOM and in-memory API."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_workspace_js_harness", Path(__file__).with_name("test_workspace_js.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DOM


def _app_slice(start: str, end: str) -> str:
    text = (WEBAPP / "app.html").read_text(encoding="utf-8")
    a = text.index(start)
    return text[a:text.index(end, a)]


#: What the page declares around the pieces of app.html a test takes: the generation poll's globals, the words and
#: the header the stopped page reads, and `ROWorkspace` as a bare name. `pollGeneration` is the harness's (it counts
#: in `generations`).
APP_GLOBALS = r"""
const REFRESH_POLL_MS = 2000;
let pollTimer = null;
global.t = (s) => s;
Object.defineProperty(global, 'ROWorkspace', { get: () => window.ROWorkspace });
customPost = (path) => path === '/api/shutdown' ? {} : undefined;
function showHeaderError() {}
const appEl = { innerHTML: '' }, shutdownHandlers = {};
document.getElementById = (id) => id === 'app' ? appEl
  : id === 'shutdown-btn' ? { disabled: false, addEventListener: (e, fn) => { shutdownHandlers[e] = fn; } } : null;
"""


def _app_pieces() -> str:
    return (_app_slice("// -- The poll rests while the window is hidden (DESIGN.md 18.4 item 3)", "function showRefreshingChip() {")
            + _app_slice("function escapeHtml(", "\n}\n") + "\n}\n"
            + _app_slice("function renderStoppedState() {", "// -- The language switch (DESIGN.md 13.2, 13.3)"))


def _page(scenario: str, *, with_workspace: bool = True, with_app: bool = False) -> dict:
    """`scenario` under node with the workspace's DOM, a hideable window and, as asked, the real workspace.js and the
    generation poll and 停止服务 of the real app.html."""
    source = _workspace_harness() + WINDOW + "\nwindow.ROI18N_SAVED=\"zh\";\n" + (WEBAPP / "i18n.js").read_text(encoding="utf-8")
    if with_app:
        source += "\n" + APP_GLOBALS + _app_pieces()
    if with_workspace:
        source += "\n" + (WEBAPP / "workspace.js").read_text(encoding="utf-8")
    source += "\n(async()=>{\n" + scenario + "\n})().catch(e=>{console.error(e.stack);process.exitCode=1});"
    result = subprocess.run([NODE, "-"], input=source, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


RUNNING = r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];setRuns([mkRun({state:'running',can_stop:true})]);
await window.ROWorkspace.load(root);await sleepTick();
const count=()=>gets.filter(g=>g==='/api/agents/runs').length;
"""


@needs_node
def test_the_run_poll_rests_while_the_window_is_hidden_and_polls_once_when_it_is_shown():
    out = _page(RUNNING + r"""
const start={waiting:waiting(),runs:count()};
await setHidden(true);
const hidden={waiting:waiting(),runs:count()};
await tick();
const stillHidden={waiting:waiting(),runs:count()};
await setHidden(false);
const shown={waiting:waiting(),runs:count()};
await setHidden(false);
const again={waiting:waiting(),runs:count()};
await tick();
console.log(JSON.stringify({start,hidden,stillHidden,shown,again,after:{waiting:waiting(),runs:count()}}));
""")
    assert out["start"] == {"waiting": 1, "runs": 1}
    assert out["hidden"] == {"waiting": 0, "runs": 1}  # the timer is taken away
    assert out["stillHidden"] == {"waiting": 0, "runs": 1}
    assert out["shown"] == {"waiting": 1, "runs": 2}  # one poll at once, and the chain goes on while the run does
    assert out["again"] == out["shown"]  # a second "shown" with nothing held makes no poll
    assert out["after"] == {"waiting": 1, "runs": 3}


@needs_node
def test_a_poll_in_flight_when_the_window_is_hidden_does_not_start_the_next_one():
    out = _page(RUNNING + r"""
const slow=deferred();let hold=false;
customGet=path=>{ if(path==='/api/agents/runs'&&hold) return slow.promise; };
hold=true;
const pending=tick();await sleepTick();
const inFlight={waiting:waiting(),runs:count()};
await setHidden(true);
slow.resolve(clone(runsList));await pending;await sleepTick();
const landed={waiting:waiting(),runs:count()};
hold=false;await setHidden(false);
console.log(JSON.stringify({inFlight,landed,shown:{waiting:waiting(),runs:count()}}));
""")
    assert out["inFlight"]["runs"] == 2
    assert out["landed"] == {"waiting": 0, "runs": 2}  # the answer is taken in; no timer is set while hidden
    assert out["shown"] == {"waiting": 1, "runs": 3}


@needs_node
def test_a_timer_that_fires_after_the_window_was_hidden_without_an_event_asks_nothing():
    out = _page(RUNNING + r"""
document.hidden=true;
await tick();
const fired={waiting:waiting(),runs:count()};
await setHidden(false);
console.log(JSON.stringify({fired,shown:{waiting:waiting(),runs:count()}}));
""")
    assert out["fired"] == {"waiting": 0, "runs": 1}
    assert out["shown"] == {"waiting": 1, "runs": 2}


@needs_node
def test_a_run_that_ended_while_the_window_was_hidden_is_found_when_it_is_shown():
    out = _page(RUNNING + r"""
const workspaceReads=()=>gets.filter(g=>g==='/api/workspace').length;
const reads=workspaceReads(),pulled=generations;
await setHidden(true);
Object.assign(runsList.runs[0],{state:'done',can_stop:false,can_reply:true});runsList.active=null;
await tick();
const hidden={waiting:waiting(),runs:count(),reloads:workspaceReads()-reads};
await setHidden(false);
console.log(JSON.stringify({hidden,shown:{waiting:waiting(),runs:count(),reloads:workspaceReads()-reads,pulled:generations-pulled,
  label:byClass('status-label').textContent}}));
""")
    assert out["hidden"] == {"waiting": 0, "runs": 1, "reloads": 0}
    assert out["shown"] == {"waiting": 0, "runs": 3, "reloads": 1, "pulled": 1, "label": "已完成"}  # the poll, and the reload's own read


@needs_node
def test_hiding_the_window_with_no_run_going_leaves_nothing_to_resume():
    out = _page(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];setRuns([mkRun({state:'done',can_reply:true})]);
await window.ROWorkspace.load(root);await sleepTick();
const n=gets.length;
await setHidden(true);await setHidden(false);
console.log(JSON.stringify({requests:gets.length-n,waiting:waiting()}));
""")
    assert out == {"requests": 0, "waiting": 0}


@needs_node
def test_showing_the_window_off_the_project_home_polls_nothing_and_coming_back_does():
    out = _page(RUNNING + r"""
await setHidden(true);
global.currentView='desk';
const n=gets.length;
await setHidden(false);
const away={requests:gets.length-n,waiting:waiting()};
global.currentView='workspace';window.ROWorkspace.resume();await sleepTick();
console.log(JSON.stringify({away,back:{requests:gets.length-n,waiting:waiting()}}));
""")
    assert out["away"] == {"requests": 0, "waiting": 0}
    assert out["back"] == {"requests": 1, "waiting": 1}


@needs_node
def test_stopping_the_service_ends_the_run_poll_for_good():
    out = _page(RUNNING + r"""
const slow=deferred();let hold=false;
customGet=path=>{ if(path==='/api/agents/runs'&&hold) return slow.promise; };
hold=true;
const pending=tick();await sleepTick();           // a poll is in flight
const inFlight=count();
window.ROWorkspace.stop();
slow.resolve(clone(runsList));await pending;await sleepTick();
const landed={waiting:waiting(),runs:count()};     // its answer does not set the next timer
hold=false;
window.ROWorkspace.resume();await sleepTick();     // nor does coming back to the home, nor showing the window
await setHidden(true);await setHidden(false);
const quiet={waiting:waiting(),runs:count()};
await window.ROWorkspace.load(root);await sleepTick();
console.log(JSON.stringify({inFlight,landed,quiet,final:{waiting:waiting(),runs:count()}}));
""")
    assert out["inFlight"] == 2
    assert out["landed"] == {"waiting": 0, "runs": 2}
    assert out["quiet"] == {"waiting": 0, "runs": 2}
    assert out["final"] == {"waiting": 0, "runs": 3}  # a reload of the home reads the runs once; no timer follows it


@needs_node
def test_the_generation_poll_pauses_while_the_window_is_hidden_and_polls_at_once_when_it_is_shown():
    out = _page(r"""
generationLive=true;startGenerationPoll();
const start={live:liveIntervals().length,polls:generations};
await setHidden(true);
const hidden={live:liveIntervals().length,polls:generations};
await setHidden(false);
const shown={live:liveIntervals().length,polls:generations};
await setHidden(false);   // shown again with nothing paused: no second poll, no second interval
const twice={live:liveIntervals().length,polls:generations};
console.log(JSON.stringify({start,hidden,shown,twice,every:liveIntervals().map(t=>t.ms)}));
""", with_workspace=False, with_app=True)
    assert out["start"] == {"live": 1, "polls": 0}
    assert out["hidden"] == {"live": 0, "polls": 0}
    assert out["shown"] == {"live": 1, "polls": 1}
    assert out["twice"] == out["shown"] and out["every"] == [2000]


@needs_node
def test_a_page_opened_in_a_hidden_window_starts_its_generation_poll_when_it_is_shown():
    out = _page(r"""
document.hidden=true;
generationLive=true;startGenerationPoll();
const opened={live:liveIntervals().length,polls:generations};
await setHidden(false);
console.log(JSON.stringify({opened,shown:{live:liveIntervals().length,polls:generations}}));
""", with_workspace=False, with_app=True)
    assert out["opened"] == {"live": 0, "polls": 0}
    assert out["shown"] == {"live": 1, "polls": 1}


@needs_node
def test_stopping_the_service_clears_the_generation_poll_and_the_run_poll_and_nothing_starts_them_again():
    out = _page(RUNNING + r"""
generationLive=true;startGenerationPoll();
const before={waiting:waiting(),live:liveIntervals().length};
await shutdownHandlers.click();
const stopped={waiting:waiting(),live:liveIntervals().length,page:appEl.innerHTML.includes('stopped-note')};
const n=gets.length,polls=generations;
await setHidden(true);await setHidden(false);
await tick();
console.log(JSON.stringify({before,stopped,after:{waiting:waiting(),live:liveIntervals().length,requests:gets.length-n,
  polls:generations-polls},asked:confirmQuestions.length}));
""", with_app=True)
    assert out["before"] == {"waiting": 1, "live": 1}
    assert out["stopped"] == {"waiting": 0, "live": 0, "page": True}
    assert out["after"] == {"waiting": 0, "live": 0, "requests": 0, "polls": 0}
    assert out["asked"] == 1


@needs_node
def test_stopping_the_service_works_on_a_page_where_the_workspace_never_loaded():
    out = _page(r"""
generationLive=true;startGenerationPoll();
await shutdownHandlers.click();
console.log(JSON.stringify({live:liveIntervals().length,page:appEl.innerHTML.includes('stopped-note')}));
""", with_workspace=False, with_app=True)
    assert out == {"live": 0, "page": True}


def test_the_page_starts_and_stops_its_generation_poll_in_one_place():
    html = (WEBAPP / "app.html").read_text(encoding="utf-8")
    assert html.count("setInterval(pollGeneration") == 1  # startGenerationPoll's
    assert html.count("clearInterval(pollTimer)") == 1  # stopGenerationPoll's
    assert "generationLive = true;\n  startGenerationPoll();\n})();" in html

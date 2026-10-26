"""DESIGN.md 19.8 and 19.10 item 5: the run store. A run's before-picture copies are hard links to one read-only blob per
content; undo still writes bytes back (with the recorded mode and modification time); an undone run releases its backup;
the project's store is kept within `agent_store_limit_gb`, oldest run first, never an active run, never `reverted/`; an
old store is converted once, in the background, and a half-converted store still undoes. The agents' programs are the
fakes of `test_agents_manager.py`."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from researchos import settings
from researchos.agents import profiles, snapshot, store
from researchos.agents import runs as run_store
from test_agents_api import FOREIGN, Api, api, api_me  # noqa: F401 -- `api` and `api_me` are fixtures
from test_agents_manager import COMPLETE, Scene, codes, finish_step, init_step, scene, stale_run  # noqa: F401 -- `scene` is a fixture
from test_webapp_server import _request_with_headers

OLD = time.time() - 3600  # an hour ago: a stamp the picture trusts (`snapshot.RACY_NS`)
SECOND_TASK = {"id": "t2", "text": "Second task", "owner": "agent:claude-code:sonnet"}


def run_dir(scene: Scene, run: dict) -> Path:
    return run_store.run_dir(scene.pid, run["id"])


def picture(scene: Scene, run: dict, name: str = "before.json") -> dict:
    return json.loads((run_dir(scene, run) / name).read_text())


def blobs(scene: Scene) -> list[Path]:
    return sorted(p for p in store.blobs_dir(scene.pid).glob("*/*") if p.is_file())


def sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


def old_file(scene: Scene, rel: str, text: str, mode: int = 0o644) -> Path:
    path = scene.put(rel, text)
    path.chmod(mode)
    os.utime(path, (OLD, OLD))
    return path


def write(rel: str, text: str) -> dict:
    return {"op": "write", "path": rel, "text": text}


def settle_store(scene: Scene) -> None:
    """The first clean-up started the conversion of the (empty) old store in the background: let it finish, so that a
    test of the limit or of the files sees the store at rest."""
    end = time.monotonic() + 30
    while time.monotonic() < end and scene.manager.converting(scene.pid):
        time.sleep(0.02)
    assert not scene.manager.converting(scene.pid)
    assert store.convert(scene.pid)


def tree(scene: Scene) -> dict[str, tuple[int, int]]:
    """Every file of the project's run folders with its inode and link count."""
    base = run_store.project_runs_dir(scene.pid)
    found = {}
    for here, _dirs, files in os.walk(base):
        for name in files:
            st = os.lstat(os.path.join(here, name))
            found[os.path.relpath(os.path.join(here, name), base)] = (st.st_ino, st.st_nlink)
    return found


# -- one copy of each content ---------------------------------------------------------------------------------------------


def test_two_runs_over_the_same_project_store_one_copy_and_the_second_reads_nothing_it_knows(scene: Scene, monkeypatch):
    path = old_file(scene, "data.txt", "the same bytes\n")
    first = scene.go([init_step(), finish_step()])
    read: list[str] = []
    real = snapshot._hash_and_copy

    def spy(full, size, dest):
        read.append(os.path.relpath(full, scene.root))
        return real(full, size, dest)

    monkeypatch.setattr(snapshot, "_hash_and_copy", spy)
    second = scene.go([init_step(), finish_step()])
    one, two = run_dir(scene, first) / "before" / "data.txt", run_dir(scene, second) / "before" / "data.txt"
    blob = store.blob_path(store.blobs_dir(scene.pid), sha("the same bytes\n"))
    assert one.read_text() == two.read_text() == "the same bytes\n"
    assert os.path.samefile(one, two) and os.path.samefile(one, blob) and blob.stat().st_nlink == 3
    assert [b.name for b in blobs(scene)].count(blob.name) == 1
    assert stat.S_IMODE(blob.stat().st_mode) == 0o444 and stat.S_IMODE(blob.parent.stat().st_mode) == 0o700
    assert not os.path.samefile(path, blob)  # the project's file is its own
    entry = picture(scene, second)["files"]["data.txt"]
    assert entry["copied"] is True and entry["sha256"] == sha("the same bytes\n") and entry["mode"] == 0o644
    assert "data.txt" not in read  # the stamp was the first run's: linked, not read
    assert read, "the files whose stamp is new are read"
    # only the bytes that were new count: what the first picture did not hold
    held = {e["sha256"] for e in picture(scene, first)["files"].values() if e.get("sha256")}
    new = {e["sha256"]: e["size"] for e in picture(scene, second)["files"].values()
           if e.get("copied") and e["sha256"] not in held}
    assert picture(scene, second)["copied_bytes"] == sum(new.values())
    assert picture(scene, second)["copied_bytes"] < picture(scene, first)["copied_bytes"]


def test_a_changed_file_gets_a_new_blob_and_the_old_one_stays_with_its_run(scene: Scene):
    old_file(scene, "data.txt", "version one\n")
    first = scene.go([init_step(), write("data.txt", "version two\n"), finish_step()])
    second = scene.go([init_step(), finish_step()])
    one, two = run_dir(scene, first) / "before" / "data.txt", run_dir(scene, second) / "before" / "data.txt"
    assert one.read_text() == "version one\n" and two.read_text() == "version two\n"
    assert not os.path.samefile(one, two)
    assert {sha("version one\n"), sha("version two\n")} <= {b.name for b in blobs(scene)}
    assert picture(scene, second)["files"]["data.txt"]["sha256"] == sha("version two\n")


def test_undo_after_deduplication_restores_the_bytes_the_mode_and_the_modification_time(scene: Scene):
    script = old_file(scene, "tool.sh", "#!/bin/sh\necho original\n", 0o755)
    mtime = os.stat(script).st_mtime_ns
    first = scene.go([init_step(), write("other.txt", "x\n"), finish_step()])
    second = scene.go([init_step(), write("tool.sh", "#!/bin/sh\necho changed\n"), finish_step()])
    one, two = run_dir(scene, first) / "before" / "tool.sh", run_dir(scene, second) / "before" / "tool.sh"
    assert os.path.samefile(one, two)  # one shared blob, which the undo of the second run must not touch
    blob = store.blob_path(store.blobs_dir(scene.pid), sha("#!/bin/sh\necho original\n"))
    ino = blob.stat().st_ino
    result = scene.manager.revert(scene.root, scene.pid, second["id"], by=None)
    assert result["restored"] == ["tool.sh"] and result["skipped"] == []
    st = os.stat(script)
    assert script.read_bytes() == b"#!/bin/sh\necho original\n"
    assert stat.S_IMODE(st.st_mode) == 0o755 and st.st_mtime_ns == mtime  # from the entry, not from the shared blob
    assert st.st_ino != ino and st.st_nlink == 1  # bytes were written back: the blob was never linked into the project
    assert blob.read_bytes() == b"#!/bin/sh\necho original\n" and stat.S_IMODE(blob.stat().st_mode) == 0o444
    assert one.read_bytes() == b"#!/bin/sh\necho original\n" and blob.stat().st_nlink == 2  # the first run still holds it


def test_an_entry_without_a_mode_keeps_the_mode_of_its_own_copy(scene: Scene):
    """A picture from before the entries held a mode has plain copies: undo takes the mode from the copy, as it did."""
    script = old_file(scene, "tool.sh", "echo a\n", 0o750)
    run = scene.go([init_step(), write("tool.sh", "echo b\n"), finish_step()])
    rd = run_dir(scene, run)
    saved = picture(scene, run)
    del saved["files"]["tool.sh"]["mode"]
    (rd / "before.json").write_text(json.dumps(saved))
    copy = rd / "before" / "tool.sh"
    copy.unlink()  # a plain copy of its own, as the old store made
    copy.write_text("echo a\n")
    copy.chmod(0o750)
    scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert script.read_text() == "echo a\n" and stat.S_IMODE(script.stat().st_mode) == 0o750


# -- an undone run releases its backup --------------------------------------------------------------------------------------


def test_an_undone_run_releases_its_backup_and_keeps_the_record_of_what_was_undone(scene: Scene):
    old_file(scene, "data.txt", "before\n")
    run = scene.go([init_step(), write("data.txt", "after\n"), write("new.txt", "n\n"), {"op": "complete"}, finish_step()])
    rd = run_dir(scene, run)
    assert (rd / "before").is_dir() and blobs(scene)
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by="m-1")
    assert result["restored"] == ["data.txt", "research/tasks.json"] and result["removed"][0] == "new.txt"
    for name in ("before", "before.json", "after.json"):
        assert not (rd / name).exists(), name
    for name in ("run.json", "events.jsonl", "instruction.txt"):
        assert (rd / name).is_file(), name
    stored = scene.run(run["id"])
    assert stored["reverted"] is True and stored["revert"]["restored"] == result["restored"] and stored["revertible"] is False
    assert blobs(scene) == []  # nothing links them any more
    assert (rd / "reverted" / "new.txt").read_text() == "n\n"
    assert codes(scene.manager.revert, scene.root, scene.pid, run["id"], by=None) == "reverted"
    assert scene.manager.detail(scene.pid, run["id"])["revert"] == stored["revert"]  # the run log still reads


def test_a_partly_undone_run_releases_its_backup_too_and_an_undo_that_did_nothing_keeps_it(scene: Scene):
    scene.put("a.txt", "a0\n")
    scene.put("b.txt", "b0\n")
    partly = scene.go([init_step(), write("a.txt", "a1\n"), write("b.txt", "b1\n"), finish_step()])
    (scene.root / "b.txt").write_text("b2 (the person)\n")  # changed after the run: left as it is
    result = scene.manager.revert(scene.root, scene.pid, partly["id"], by=None)
    assert result["restored"] == ["a.txt"] and result["skipped"] == [{"path": "b.txt", "reason": "changed_since"}]
    assert scene.run(partly["id"])["reverted"] is True
    assert not (run_dir(scene, partly) / "before").exists() and not (run_dir(scene, partly) / "before.json").exists()

    scene.put("c.txt", "c0\n")
    nothing = scene.go([init_step(), write("c.txt", "c1\n"), finish_step()])
    (scene.root / "c.txt").write_text("c2 (the person)\n")
    result = scene.manager.revert(scene.root, scene.pid, nothing["id"], by=None)
    assert result["restored"] == [] and result["skipped"] == [{"path": "c.txt", "reason": "changed_since"}]
    assert scene.run(nothing["id"])["reverted"] is False  # nothing was done: the person may try again
    assert (run_dir(scene, nothing) / "before").is_dir() and (run_dir(scene, nothing) / "before.json").is_file()


# -- the size the limit is measured against ----------------------------------------------------------------------------------
# DESIGN.md 19.8: a project's store is its blobs (each once) + every run's `reverted/` + everything else in its run folders.
# These tests make files of known sizes and compare `store.scan` and the limit with byte counts written out here, so that a
# count that went wrong (a blob once per run or per link, a plain copy dropped, `before/` or `reverted/` taken for a record)
# cannot move the limit's tests along with it, as it would if they took their thresholds from `scan` itself.

A_ONE, A_TWO, B_ONE = "a" * 1000, "c" * 1000, "b" * 500  # three contents of 1000, 1000 and 500 bytes
LOG, INSTRUCTION, BEFORE_JSON, AFTER_JSON, MARKER_BYTES = 200, 50, 70, 30, 20  # the other files of a run folder, in bytes


def sized(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"." * size)
    return path


def tree_of_two_files(scene: Scene) -> Path:
    """A folder of its own (not the project) holding a.txt of 1000 bytes and b.txt of 500, old enough for the picture to trust."""
    folder = scene.tmp / "pictured"
    folder.mkdir()
    for name, text in (("a.txt", A_ONE), ("b.txt", B_ONE)):
        (folder / name).write_text(text)
        os.utime(folder / name, (OLD, OLD))
    return folder


def run_of_known_files(scene: Scene, *, pictures: bool, **more) -> dict:
    """A finished run whose folder holds `events.jsonl` (200 bytes), `instruction.txt` (50) and, with `pictures`, `before.json`
    (70) and `after.json` (30), beside the `run.json` that `stale_run` writes. The copies are the test's to add."""
    run = stale_run(scene, state="done", before=False, **more)
    rd = run_dir(scene, run)
    sized(rd / "events.jsonl", LOG)
    sized(rd / "instruction.txt", INSTRUCTION)
    if pictures:
        sized(rd / "before.json", BEFORE_JSON)
        sized(rd / "after.json", AFTER_JSON)
    return run


def shared_store(scene: Scene) -> list[dict]:
    """Four runs, oldest first. Runs 1 to 3 took the picture of a.txt (1000 bytes) and b.txt (500) through the blobs, a.txt
    having changed (to another 1000 bytes) before the third: three blobs, A1 linked by runs 1 and 2, A2 by run 3, B by all
    three. Run 4 was undone: no backup, two moved-away files of 40 and 25 bytes. The project's `store.json` is 20 bytes."""
    folder = tree_of_two_files(scene)
    made, prior = [], None
    for n in range(3):
        if n == 2:
            (folder / "a.txt").write_text(A_TWO)
            os.utime(folder / "a.txt", (OLD + 10, OLD + 10))
        run = run_of_known_files(scene, pictures=True, revertible=True)
        prior = snapshot.take(folder, copy_to=run_dir(scene, run) / "before", blobs=store.blobs_dir(scene.pid), prior=prior)
        made.append(run)
    undone = run_of_known_files(scene, pictures=False, reverted=True)
    sized(run_dir(scene, undone) / "reverted" / "made.txt", 40)
    sized(run_dir(scene, undone) / "reverted" / "sub" / "deeper.txt", 25)
    sized(run_store.project_runs_dir(scene.pid) / store.MARKER, MARKER_BYTES)
    return [*made, undone]


def records_bytes(scene: Scene, runs: list[dict], holding: int) -> int:
    """What `shared_store` should have as records: four logs and instructions, the marker, the two pictures of each of the
    `holding` runs that still hold them -- all written out above -- and every `run.json` (not fixed: it carries paths and dates,
    and a release rewrites it, so its size is read from the file)."""
    fixed = len(runs) * (LOG + INSTRUCTION) + holding * (BEFORE_JSON + AFTER_JSON) + MARKER_BYTES
    return fixed + sum((run_dir(scene, r) / "run.json").stat().st_size for r in runs)


def test_a_blob_is_counted_once_however_many_runs_link_it_and_before_and_reverted_are_not_records(scene: Scene):
    runs = shared_store(scene)
    undone = runs[3]
    a_one, a_two, b_one = (store.blob_path(store.blobs_dir(scene.pid), sha(t)) for t in (A_ONE, A_TWO, B_ONE))
    assert [a_one.stat().st_nlink, a_two.stat().st_nlink, b_one.stat().st_nlink] == [3, 2, 4]  # the blob and each run's link
    assert sorted(b.stat().st_size for b in blobs(scene)) == [500, 1000, 1000] and len(blobs(scene)) == 3
    usage = store.scan(scene.pid)
    assert usage.backups == 1000 + 1000 + 500  # not 3 * 1500 (once per run) and not 4500 + 1500 (once per link)
    assert usage.moved == 40 + 25
    assert usage.records == records_bytes(scene, runs, holding=3)  # `before/` and `reverted/` are not in it
    assert usage.total == usage.backups + usage.moved + usage.records
    assert usage.moved_runs == [{"run": undone["id"], "size": 65, "files": 2}]
    space = scene.manager.space(scene.root, scene.pid)  # the panel reads the same numbers
    assert (space["backups"], space["moved"], space["records"]) == (2500, 65, records_bytes(scene, runs, holding=3))
    assert space["total"] == usage.total and space["releasable"] == 3 and space["over"] is False
    assert [(row["run"], row["size"], row["files"]) for row in space["moved_runs"]] == [(undone["id"], 65, 2)]


def test_releasing_a_run_lowers_the_size_only_by_what_no_other_run_links(scene: Scene):
    runs = shared_store(scene)
    one, two, three, _undone = runs
    store.release(scene.pid, one["id"])
    usage = store.scan(scene.pid)
    assert usage.backups == 2500, "runs 2 and 3 still link every blob: only the pictures of run 1 went"
    assert usage.records == records_bytes(scene, runs, holding=2) and usage.moved == 65
    assert not (run_dir(scene, one) / "before").exists() and len(blobs(scene)) == 3
    store.release(scene.pid, two["id"], sweep=False)
    assert store.scan(scene.pid).backups == 2500, "A1 is nobody's copy now, but its blob is on disk and counts until it is deleted"
    assert store.collect(scene.pid) == 1000
    usage = store.scan(scene.pid)
    assert usage.backups == 1000 + 500 and sorted(b.stat().st_size for b in blobs(scene)) == [500, 1000]  # A2 and B, for run 3
    assert usage.records == records_bytes(scene, runs, holding=1)
    store.release(scene.pid, three["id"])
    usage = store.scan(scene.pid)
    assert usage.backups == 0 and blobs(scene) == []
    assert usage.moved == 65 and usage.records == records_bytes(scene, runs, holding=0)  # the moved-away files stay


def test_a_copy_that_is_no_blob_link_counts_for_itself_and_the_conversion_leaves_one_copy_of_each(scene: Scene, monkeypatch):
    folder = tree_of_two_files(scene)

    def run_with_copies(**take_args) -> dict:
        run = run_of_known_files(scene, pictures=False, revertible=True)
        rd = run_dir(scene, run)
        (rd / "before.json").write_text(json.dumps(snapshot.take(folder, copy_to=rd / "before", **take_args)))
        return run

    def refuse(*args, **kwargs):
        raise OSError(18, "Invalid cross-device link")

    plain = run_with_copies()  # a store from before the blobs: a copy of its own for each file of each run
    with monkeypatch.context() as patch:
        patch.setattr(os, "link", refuse)  # and a file system that will not link: the same plain copies
        refused = run_with_copies(blobs=store.blobs_dir(scene.pid))
    copies = [run_dir(scene, r) / "before" / name for r in (plain, refused) for name in ("a.txt", "b.txt")]
    assert [c.stat().st_nlink for c in copies] == [1, 1, 1, 1] and blobs(scene) == []
    assert store.scan(scene.pid).backups == 2 * (1000 + 500)  # every copy counts, none is dropped for being no blob link
    today = run_with_copies(blobs=store.blobs_dir(scene.pid))  # a run of today: the copies are links to two new blobs
    assert sorted(b.stat().st_size for b in blobs(scene)) == [500, 1000]
    assert store.scan(scene.pid).backups == 2 * 1500 + 1500  # the blobs once, not once more for the links in `before/`
    assert store.convert(scene.pid) is True
    assert store.scan(scene.pid).backups == 1500  # one copy of each content: the plain copies are now links to the blobs
    assert [b.stat().st_nlink for b in blobs(scene)] == [4, 4]
    assert all(c.read_text() in (A_ONE, B_ONE) for c in copies) and (run_dir(scene, today) / "before" / "a.txt").read_text() == A_ONE


def test_the_limit_is_kept_against_written_out_sizes_oldest_run_first_and_never_touches_reverted(scene: Scene):
    runs = shared_store(scene)
    one, two, three, _undone = runs
    total = 2500 + 65 + records_bytes(scene, runs, holding=3)
    released, usage = store.enforce(scene.pid, total)
    assert released == [] and usage.total == total  # exactly at the limit is within it
    # 600 bytes under: run 1's release frees its two pictures only (the blobs stay linked), so run 2's must follow; run 3 stays
    released, usage = store.enforce(scene.pid, total - 600)
    assert released == [one["id"], two["id"]]
    assert usage.backups == 1500 and usage.total == 1500 + 65 + records_bytes(scene, runs, holding=1)
    assert [scene.run(r["id"])["revertible"] for r in (one, two, three)] == [False, False, True]
    assert store.holds_backup(run_dir(scene, three)) and not store.holds_backup(run_dir(scene, two))
    # 1500 bytes under the first total: the store is still over it, so the last backup goes -- A2 and B with it
    released, usage = store.enforce(scene.pid, total - 1500)
    assert released == [three["id"]] and usage.backups == 0 and blobs(scene) == []
    assert usage.total == 65 + records_bytes(scene, runs, holding=0)
    # a limit nothing can meet: no run holds a backup any more, and the moved-away files are the person's to delete
    released, usage = store.enforce(scene.pid, 1)
    assert released == [] and usage.moved == 65 and usage.total > 1
    assert (run_dir(scene, runs[3]) / "reverted" / "made.txt").stat().st_size == 40
    assert (run_dir(scene, runs[3]) / "reverted" / "sub" / "deeper.txt").stat().st_size == 25


# -- the limit --------------------------------------------------------------------------------------------------------------


def fill(scene: Scene, runs: int = 4) -> list[dict]:
    """`runs` runs, each changing `big.txt` to a text of its own, so that every run's backup holds a blob of its own."""
    scene.put("big.txt", "0" * 600)
    return [scene.go([init_step(), write("big.txt", str(i + 1) * 600), finish_step()]) for i in range(runs)]


def test_the_limit_releases_the_oldest_backup_first_and_stops_when_the_store_is_within_it(scene: Scene, monkeypatch):
    made = fill(scene)
    settle_store(scene)
    total = store.scan(scene.pid).total
    assert profiles.store_limit_gb() == 5 and scene.manager.enforce(scene.pid).total == total  # within 5 GB: nothing goes
    monkeypatch.setattr(store, "GB", total - 1)  # one "gigabyte" of this size: the store is 1 byte over
    profiles.set_store_limit_gb(1)
    usage = scene.manager.enforce(scene.pid)
    assert usage.total <= total - 1
    assert not store.holds_backup(run_dir(scene, made[0])) and scene.run(made[0]["id"])["revertible"] is False
    for later in made[1:]:
        assert store.holds_backup(run_dir(scene, later)) and scene.run(later["id"])["revertible"] is True
    assert codes(scene.manager.revert, scene.root, scene.pid, made[0]["id"], by=None) == "not_revertible"
    assert scene.manager.runs(scene.pid)["runs"][-1]["can_revert"] is False  # the oldest run: listed, not undoable
    # still over by one byte: the next oldest goes, newest last
    monkeypatch.setattr(store, "GB", usage.total - 1)
    assert scene.manager.enforce(scene.pid).total <= usage.total - 1
    assert [store.holds_backup(run_dir(scene, r)) for r in made] == [False, False, True, True]


def test_the_limit_after_a_run_s_clean_up_is_kept_without_anyone_asking(scene: Scene, monkeypatch):
    made = fill(scene, 2)
    settle_store(scene)
    total = store.scan(scene.pid).total
    monkeypatch.setattr(store, "GB", total)
    profiles.set_store_limit_gb(1)  # the store is exactly at the limit; the next run puts it over
    scene.go([init_step(), write("big.txt", "9" * 600), finish_step()])
    assert not store.holds_backup(run_dir(scene, made[0])) and scene.run(made[0]["id"])["revertible"] is False
    assert store.scan(scene.pid).total <= total or not store.holders(scene.pid)  # kept, or nothing is left to release


def test_the_limit_never_releases_an_active_run_and_may_stay_over(scene: Scene, monkeypatch):
    made = fill(scene, 2)
    settle_store(scene)
    older_id = run_store.new_run_id(datetime(2020, 1, 1, tzinfo=timezone.utc))  # older than every run: the first to go
    run_store.create_run(scene.pid, {**scene.run(made[0]["id"]), "id": older_id, "state": "running", "revertible": False})
    older = run_store.run_dir(scene.pid, older_id)
    (older / "before").mkdir()
    (older / "before" / "f.txt").write_text("x" * 300)
    (older / "before.json").write_text("{}")
    newest = stale_run(scene, state="running")
    monkeypatch.setattr(store, "GB", 1)
    profiles.set_store_limit_gb(1)  # one byte: nothing fits
    scene.manager.enforce(scene.pid)
    assert store.holds_backup(older) and store.holds_backup(run_dir(scene, newest)), "an active run keeps its backup"
    assert not any(store.holds_backup(run_dir(scene, r)) for r in made)
    space = scene.manager.space(scene.root, scene.pid)
    assert space["over"] is True and space["releasable"] == 0


def test_release_all_keeps_the_backups_of_runs_that_are_going_or_wait_for_their_check(scene: Scene):
    made = fill(scene, 2)
    running = stale_run(scene, state="running")
    waiting = stale_run(scene, state="interrupted", check_pending=True)
    result = scene.manager.release_backups(scene.pid)
    assert sorted(result["released"]) == sorted(r["id"] for r in made)
    assert store.holds_backup(run_dir(scene, running)) and store.holds_backup(run_dir(scene, waiting))
    assert [r for r in made if scene.run(r["id"])["revertible"]] == []
    assert scene.manager.release_backups(scene.pid) == {"released": []}  # idempotent


def test_reverted_is_never_removed_by_the_limit(scene: Scene, monkeypatch):
    run = scene.go([init_step(), write("made.txt", "m" * 500), finish_step()])
    scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    moved = run_dir(scene, run) / "reverted" / "made.txt"
    assert moved.read_text() == "m" * 500
    monkeypatch.setattr(store, "GB", 1)
    profiles.set_store_limit_gb(1)
    scene.manager.enforce(scene.pid)
    assert moved.read_text() == "m" * 500
    space = scene.manager.space(scene.root, scene.pid)
    assert space["moved"] >= 500 and space["moved_over"] is True and space["over"] is True


def test_the_200_run_rule_keeps_a_run_that_holds_moved_away_files_and_nothing_else_of_it(scene: Scene, monkeypatch):
    monkeypatch.setattr(run_store, "KEEP_RUNS", 3)
    undone = scene.go([init_step(), write("made.txt", "m\n"), finish_step()])
    scene.manager.revert(scene.root, scene.pid, undone["id"], by=None)
    plain = scene.go([init_step(), write("plain.txt", "p\n"), finish_step()])
    later = [scene.go([init_step(), write(f"x{i}.txt", "x\n"), finish_step()]) for i in range(3)]
    kept = [r["id"] for r in run_store.list_runs(scene.pid)]
    assert plain["id"] not in kept and not run_dir(scene, plain).exists()  # nothing moved away: removed whole
    assert undone["id"] in kept and [r["id"] for r in later] == sorted(kept)[1:]
    rd = run_dir(scene, undone)
    assert sorted(p.name for p in rd.iterdir()) == ["reverted", "run.json"]
    assert (rd / "reverted" / "made.txt").read_text() == "m\n"
    stored = scene.run(undone["id"])
    assert stored["reverted"] is True and stored["revertible"] is False and stored["revert"]["removed"] == ["made.txt"]
    run_store.prune(scene.pid)  # idempotent
    assert sorted(p.name for p in rd.iterdir()) == ["reverted", "run.json"]
    # the person deletes them: the run goes with them (here, at once)
    assert scene.manager.delete_moved(scene.pid, undone["id"]) == {"freed": 2} and not rd.exists()


def test_unused_blobs_are_deleted_and_a_dead_runs_temporary_names_go_when_they_are_old(scene: Scene):
    made = fill(scene, 2)
    keep = blobs(scene)
    assert keep and all(b.stat().st_nlink >= 2 for b in keep)
    stray = store.blobs_dir(scene.pid) / "00" / ("0" * 64)
    stray.parent.mkdir(exist_ok=True)
    stray.write_text("nobody links me")  # one link: its own name
    stale, fresh = store.blobs_dir(scene.pid) / ".tmp-1-dead", store.blobs_dir(scene.pid) / ".tmp-2-alive"
    stale.write_text("x")
    fresh.write_text("y")
    os.utime(stale, (time.time() - 3 * 24 * 3600,) * 2)
    freed = store.collect(scene.pid)
    assert freed == len("nobody links me") + 1 and not stray.exists() and not stale.exists() and fresh.exists()
    assert blobs(scene) == keep  # what a run links stays
    store.release(scene.pid, made[0]["id"])
    assert all(b.stat().st_nlink >= 2 for b in blobs(scene)) and len(blobs(scene)) < len(keep)


# -- an old store is converted ----------------------------------------------------------------------------------------------


def legacy_runs(scene: Scene, monkeypatch) -> list[dict]:
    """Three runs as the store made them before the blobs: plain copies, entries without a mode, nothing converted."""
    real = snapshot.take

    def take(root, copy_to=None, **kwargs):
        kwargs.pop("blobs", None)
        kwargs.pop("prior", None)
        return real(root, copy_to, **kwargs)

    old_file(scene, "tool.sh", "#!/bin/sh\necho 0\n", 0o755)
    old_file(scene, "same.txt", "the same in every run\n")
    with monkeypatch.context() as patch:
        patch.setattr(snapshot, "take", take)
        patch.setattr(scene.manager, "tidy", lambda project_id: None)
        made = [scene.go([init_step(), write("tool.sh", f"#!/bin/sh\necho {i + 1}\n"), write(f"new{i}.txt", "n\n"),
                          finish_step()]) for i in range(3)]
    for run in made:
        rd = run_dir(scene, run)
        saved = picture(scene, run)
        for entry in saved["files"].values():
            entry.pop("mode", None)
        (rd / "before.json").write_text(json.dumps(saved))
        assert (rd / "before" / "same.txt").stat().st_nlink == 1
    assert not store.converted(scene.pid) and store.needs_conversion(scene.pid)
    return made


def test_a_legacy_store_converts_into_blobs_once_and_idempotently(scene: Scene, monkeypatch):
    made = legacy_runs(scene, monkeypatch)
    run_store.update_run(scene.pid, made[0]["id"], reverted=True)  # undone, in the old store: its backup is still there
    before_bytes = {r["id"]: (run_dir(scene, r) / "before" / "tool.sh").read_bytes() for r in made}
    assert store.convert(scene.pid) is True
    marker = run_store.project_runs_dir(scene.pid) / store.MARKER
    assert not store.needs_conversion(scene.pid) and marker.is_file()
    assert not store.holds_backup(run_dir(scene, made[0]))  # undone runs release their backups
    for run in made[1:]:
        copy = run_dir(scene, run) / "before" / "same.txt"
        assert copy.stat().st_nlink >= 3 and copy.read_text() == "the same in every run\n"
        assert (run_dir(scene, run) / "before" / "tool.sh").read_bytes() == before_bytes[run["id"]]
        assert picture(scene, run)["files"]["tool.sh"]["mode"] == 0o755  # recorded before the inode went read only
        assert stat.S_IMODE(copy.stat().st_mode) == 0o444
    assert os.path.samefile(run_dir(scene, made[1]) / "before" / "same.txt", run_dir(scene, made[2]) / "before" / "same.txt")
    assert len([b for b in blobs(scene) if b.name == sha("the same in every run\n")]) == 1
    settled = tree(scene)
    assert store.convert(scene.pid) is True and tree(scene) == settled  # idempotent: nothing moved, nothing new
    # the converted store undoes: bytes, mode and time from the entries
    result = scene.manager.revert(scene.root, scene.pid, made[2]["id"], by=None)
    assert result["skipped"] == [] and "tool.sh" in result["restored"]
    assert (scene.root / "tool.sh").read_bytes() == before_bytes[made[2]["id"]]
    assert stat.S_IMODE((scene.root / "tool.sh").stat().st_mode) == 0o755


def test_an_interrupted_conversion_leaves_a_store_that_still_undoes(scene: Scene, monkeypatch):
    made = legacy_runs(scene, monkeypatch)
    asked = {"n": 0}

    def stop() -> bool:
        asked["n"] += 1
        return asked["n"] > 12

    assert store.convert(scene.pid, stop) is False
    assert not store.converted(scene.pid)
    links = [n for _ino, n in tree(scene).values()]
    assert any(n > 1 for n in links), "some files were converted before it stopped"
    assert any(n == 1 for n in links), "and some were not"
    result = scene.manager.revert(scene.root, scene.pid, made[2]["id"], by=None)  # half converted, it undoes
    assert result["skipped"] == [] and (scene.root / "tool.sh").read_text() == "#!/bin/sh\necho 2\n"
    assert stat.S_IMODE((scene.root / "tool.sh").stat().st_mode) == 0o755
    assert store.convert(scene.pid) is True and store.converted(scene.pid)  # and it can be finished


def test_a_run_that_is_going_is_left_alone_by_the_conversion(scene: Scene, monkeypatch):
    legacy_runs(scene, monkeypatch)
    going = stale_run(scene, state="running")
    copy = run_dir(scene, going) / "before" / "same.txt"
    assert copy.stat().st_nlink == 1
    assert store.convert(scene.pid) is True
    assert copy.stat().st_nlink == 1 and not store.converted(scene.pid)  # looked at again at the next clean-up


def test_the_manager_converts_in_the_background_and_says_so(scene: Scene, monkeypatch):
    legacy_runs(scene, monkeypatch)
    gate, entered = threading.Event(), threading.Event()
    real_file, real_convert = store._convert_file, store.convert
    threads: list[int] = []

    def slow(project_id, blobs_dir, path):
        entered.set()
        gate.wait(10)
        return real_file(project_id, blobs_dir, path)

    def spy(project_id, stop=lambda: False):
        threads.append(threading.get_ident())
        return real_convert(project_id, stop)

    monkeypatch.setattr(store, "_convert_file", slow)
    monkeypatch.setattr(store, "convert", spy)
    assert scene.manager.tidy(scene.pid) is None  # returns at once
    assert entered.wait(10) and scene.manager.converting(scene.pid)
    waiting = scene.manager.space(scene.root, scene.pid, tidy=True)  # a second ask does not start a second thread
    assert waiting["converting"] is True and waiting["total"] is None and waiting["backups"] is None
    assert waiting["moved_runs"] == [] and waiting["limit_gb"] == 5
    gate.set()
    end = time.monotonic() + 30
    while scene.manager.converting(scene.pid) and time.monotonic() < end:
        time.sleep(0.02)
    assert not scene.manager.converting(scene.pid) and store.converted(scene.pid)
    assert len(threads) == 1 and threads[0] != threading.get_ident()  # never on the caller's thread
    done = scene.manager.space(scene.root, scene.pid)
    assert done["converting"] is False and done["total"] > 0 and done["backups"] > 0


def test_a_run_s_clean_up_starts_the_conversion_of_an_old_store_and_the_limit_waits_for_it(scene: Scene, monkeypatch):
    """19.8: the first clean-up after the upgrade converts; until it has, the limit must not be kept against a store
    whose copies are not yet shared (it would release backups that the conversion is about to make small)."""
    legacy_runs(scene, monkeypatch)
    monkeypatch.setattr(store, "GB", 1)
    profiles.set_store_limit_gb(1)
    released: list[str] = []
    real = store.enforce
    monkeypatch.setattr(store, "enforce", lambda *a, **k: released.append("asked") or real(*a, **k))
    assert scene.manager.tidy(scene.pid) is None
    end = time.monotonic() + 30
    while scene.manager.converting(scene.pid) and time.monotonic() < end:
        time.sleep(0.02)
    assert store.converted(scene.pid) and released == ["asked"]  # asked once, after the conversion
    assert scene.manager.tidy(scene.pid) is not None and released == ["asked", "asked"]  # now a clean-up keeps the limit


# -- trouble ----------------------------------------------------------------------------------------------------------------


def test_a_file_system_that_will_not_link_gets_the_plain_copy_and_still_undoes(scene: Scene, monkeypatch):
    script = old_file(scene, "tool.sh", "echo a\n", 0o755)
    mtime = os.stat(script).st_mtime_ns

    def refuse(*args, **kwargs):
        raise OSError(18, "Invalid cross-device link")

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", refuse)
        patch.setattr(scene.manager, "tidy", lambda project_id: None)
        run = scene.go([init_step(), write("tool.sh", "echo b\n"), finish_step()])
    copy = run_dir(scene, run) / "before" / "tool.sh"
    assert copy.read_text() == "echo a\n" and copy.stat().st_nlink == 1  # its own file
    entry = picture(scene, run)["files"]["tool.sh"]
    assert entry["copied"] is True and entry["sha256"] == sha("echo a\n") and entry["mode"] == 0o755
    assert all(b.stat().st_nlink == 1 for b in blobs(scene))  # whatever was staged is unused
    result = scene.manager.revert(scene.root, scene.pid, run["id"], by=None)
    assert result["skipped"] == [] and script.read_text() == "echo a\n" and os.stat(script).st_mtime_ns == mtime


def test_take_copies_through_a_blob_deleted_under_it(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    for name in ("a.txt", "b.txt"):
        (root / name).write_text(f"content of {name}\n")
        os.utime(root / name, (OLD, OLD))
    store_dir = tmp_path / "blobs"
    first = snapshot.take(root, copy_to=tmp_path / "c1", blobs=store_dir)
    shutil.rmtree(store_dir)  # `collect` removed every blob after the stamps were read
    second = snapshot.take(root, copy_to=tmp_path / "c2", blobs=store_dir, prior=first)
    for name in ("a.txt", "b.txt"):
        assert (tmp_path / "c2" / name).read_text() == f"content of {name}\n"
        assert second["files"][name]["copied"] is True and second["files"][name]["sha256"] == first["files"][name]["sha256"]
    # a blob deleted between the two links of a new copy: the temporary file becomes the blob
    real_link = os.link
    deleted: list[str] = []

    def link(src, dst, **kw):
        if os.path.basename(str(src)) == first["files"]["a.txt"]["sha256"] and not deleted:
            deleted.append(str(src))
            os.unlink(src)  # the other copy's blob vanishes just as we are about to link it
        return real_link(src, dst, **kw)

    monkeypatch.setattr(os, "link", link)
    third = snapshot.take(root, copy_to=tmp_path / "c3", blobs=store_dir)
    assert deleted and (tmp_path / "c3" / "a.txt").read_text() == "content of a.txt\n"
    assert third["files"]["a.txt"]["copied"] is True
    assert store.blob_path(store_dir, first["files"]["a.txt"]["sha256"]).is_file()


def test_only_new_bytes_count_toward_the_budget_and_a_known_content_is_linked_whatever_the_budget(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    for name, text in (("a.txt", "aaaaaa"), ("b.txt", "bbbbbb")):
        (root / name).write_text(text)
        os.utime(root / name, (OLD, OLD))
    store_dir = tmp_path / "blobs"
    monkeypatch.setattr(snapshot, "COPY_BUDGET", 10)
    first = snapshot.take(root, copy_to=tmp_path / "c1", blobs=store_dir)
    assert first["copied_bytes"] == 6 and first["not_copied"] == 1  # the second file did not fit
    assert [r for r, e in first["files"].items() if e.get("copied")] == ["a.txt"]
    second = snapshot.take(root, copy_to=tmp_path / "c2", blobs=store_dir, prior=first)
    assert second["copied_bytes"] == 6 and second["not_copied"] == 0  # a.txt cost nothing, b.txt now fits
    monkeypatch.setattr(snapshot, "COPY_BUDGET", 0)
    third = snapshot.take(root, copy_to=tmp_path / "c3", blobs=store_dir)
    assert third["copied_bytes"] == 0 and third["not_copied"] == 0  # nothing is new: all linked, over a budget of nothing
    assert all(e["copied"] for e in third["files"].values())
    monkeypatch.setattr(snapshot, "COPY_LIMIT", 3)  # a file over the copy limit is never linked either
    assert "copied" not in snapshot.take(root, copy_to=tmp_path / "c4", blobs=store_dir)["files"]["a.txt"]


def test_a_plain_copy_is_never_written_through_an_existing_link(tmp_path, monkeypatch):
    """The fallback removes the destination first: a copy that is a link to a blob must not rewrite the blob."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("alpha\n")
    (root / "b.txt").write_text("bravo\n")
    store_dir = tmp_path / "blobs"
    snapshot.take(root, copy_to=tmp_path / "c1", blobs=store_dir)
    bravo = store.blob_path(store_dir, sha("bravo\n"))
    (tmp_path / "c2").mkdir()
    os.link(bravo, tmp_path / "c2" / "a.txt")  # the destination of a.txt already is a link to another content's blob
    monkeypatch.setattr(store, "settle", lambda *a, **k: (False, False))  # the link of a new copy fails
    snapshot.take(root, copy_to=tmp_path / "c2", blobs=store_dir)
    assert bravo.read_text() == "bravo\n" and (tmp_path / "c2" / "a.txt").read_text() == "alpha\n"
    assert not os.path.samefile(bravo, tmp_path / "c2" / "a.txt")


# -- the setting ------------------------------------------------------------------------------------------------------------


def test_the_store_limit_is_a_whole_number_of_gigabytes_from_1_to_100_and_defaults_to_5():
    assert profiles.store_limit_gb() == 5 and profiles.STORE_LIMIT_RANGE == (1, 100)
    for good, expected in ((1, 1), (100, 100), (7, 7), ("12", 12), (3.0, 3)):
        assert profiles.set_store_limit_gb(good) == expected == profiles.store_limit_gb()
    for bad in (0, 101, -1, 2.5, "x", True, [], {}, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            profiles.set_store_limit_gb(bad)
        assert profiles.store_limit_gb() == 3, bad
    assert profiles.set_store_limit_gb(None) == 5 == profiles.store_limit_gb()
    assert profiles.set_store_limit_gb("") == 5
    for stored in (0, 101, 2.5, "9", True, None, [3]):  # a bad value in the file reads as not set, never as an error
        settings.write_json(settings.settings_path(), {**settings.read_settings(), profiles.STORE_LIMIT_SETTING: stored})
        assert profiles.store_limit_gb() == 5, stored


# -- the space panel's answer -----------------------------------------------------------------------------------------------


def test_the_space_answer_names_sizes_against_the_limit_and_the_runs_holding_moved_away_files(scene: Scene):
    scene.write_tasks([{"id": "t1", "text": "Analyse the sample", "owner": "agent:claude-code:sonnet"}, SECOND_TASK])
    old_file(scene, "data.txt", "d" * 1000)
    kept = scene.go([init_step(), write("data.txt", "e" * 1000), write("made1.txt", "1" * 100), finish_step()])
    other = scene.go([init_step(), write("made2.txt", "2" * 300), write("made3.txt", "3" * 40), finish_step()],
                     task_id="t2")
    scene.manager.revert(scene.root, scene.pid, kept["id"], by=None)
    scene.manager.revert(scene.root, scene.pid, other["id"], by=None)
    scene.write_tasks([{"id": "t1", "text": "Analyse the sample", "owner": "agent:claude-code:sonnet"}])  # t2 is removed
    settle_store(scene)
    space = scene.manager.space(scene.root, scene.pid)
    assert space["limit"] == 5 * 1024 ** 3 and space["limit_gb"] == 5 and space["converting"] is False
    assert space["moved"] == 100 + 1000 + 300 + 40  # reverted/ holds the modified file copied away, and the created ones
    assert space["backups"] == 0  # both runs are undone: their backups are released
    assert space["records"] > 0 and space["total"] == space["backups"] + space["moved"] + space["records"]
    assert space["over"] is False and space["moved_over"] is False and space["releasable"] == 0
    rows = {row["run"]: row for row in space["moved_runs"]}
    assert rows[kept["id"]]["files"] == 2 and rows[kept["id"]]["size"] == 1100
    assert rows[kept["id"]]["task"] == "Analyse the sample" and rows[kept["id"]]["task_removed"] is False
    assert isinstance(rows[kept["id"]]["date"], str) and len(rows[kept["id"]]["date"]) >= 19
    assert rows[other["id"]]["files"] == 2 and rows[other["id"]]["size"] == 340 and rows[other["id"]]["task_removed"] is True
    assert rows[other["id"]]["task"] == "Second task"
    assert scene.manager.delete_moved(scene.pid, other["id"]) == {"freed": 340}
    assert not (run_dir(scene, other) / "reverted").exists()
    assert (run_dir(scene, other) / "run.json").is_file() and (run_dir(scene, kept) / "reverted" / "made1.txt").is_file()
    assert [row["run"] for row in scene.manager.space(scene.root, scene.pid)["moved_runs"]] == [kept["id"]]
    assert codes(scene.manager.delete_moved, scene.pid, "r-20200101T000000Z-aaaaaa") == "run_missing"
    assert codes(scene.manager.delete_moved, scene.pid, "../x") == "invalid"
    running = stale_run(scene, state="running")
    assert codes(scene.manager.delete_moved, scene.pid, running["id"]) == "run_active"


def test_a_project_without_runs_has_nothing_in_its_store(scene: Scene):
    space = scene.manager.space(scene.root, scene.pid, tidy=True)
    assert space["total"] == 0 and space["moved_runs"] == [] and space["converting"] is False
    assert not run_store.project_runs_dir(scene.pid).exists()  # reading it wrote nothing


def test_the_run_log_names_the_session_of_every_runner(scene: Scene):
    scene.write_tasks([
        {"id": "t1", "text": "A", "owner": "agent:claude-code:sonnet"},
        {"id": "t2", "text": "B", "owner": "agent:codex:gpt-6.1-sol"},
        {"id": "t3", "text": "C", "owner": "agent:deepseek-harness:deepseek-account/deepseek-flash"},
        {"id": "t4", "text": "D", "owner": "agent:opencode:lm/qwen"}])
    for task, runner in (("t1", "claude-code"), ("t2", "codex"), ("t3", "deepseek-harness"), ("t4", "opencode")):
        run = scene.go([init_step(), finish_step()], runner=runner, task_id=task)
        detail = scene.manager.detail(scene.pid, run["id"])
        assert detail["session_id"] and detail["session_id"] == scene.run(run["id"])["session_id"], runner
    assert scene.manager.detail(scene.pid, stale_run(scene, session_id=None)["id"])["session_id"] is None


# -- the endpoints (DESIGN.md 19.8) -----------------------------------------------------------------------------------------


def _api_run(api, monkeypatch, *, undo: bool = False) -> dict:
    """A finished run of the fake agent that changed old.txt and made analysis.py; `undo` also undoes it."""
    monkeypatch.setattr(api.httpd, "start_scan", lambda served: None)
    api.put("old.txt", "v0\n")
    api.scenario(COMPLETE + [{"op": "write", "path": "old.txt", "text": "v1\n"}])
    done = api.poll(api.start()["id"])
    assert done["state"] == "done" and done["can_revert"] is True
    if undo:
        assert api.post(f"/api/agents/runs/{done['id']}/revert")[0] == 200
    return done


def test_the_settings_endpoint_validates_the_store_limit_with_the_other_limits(api):
    status, info = api.get("/api/agents")
    assert status == 200 and info["store_limit_gb"] == 5 and info["store_limit_range"] == [1, 100]
    status, saved = api.post("/api/settings/agents", {"store_limit_gb": 12}, project=False)
    assert status == 200 and saved["store_limit_gb"] == 12 and profiles.store_limit_gb() == 12
    status, saved = api.post("/api/settings/agents", {"timeout_minutes": 40}, project=False)
    assert saved["store_limit_gb"] == 12 and saved["timeout_minutes"] == 40  # keys not sent are kept
    for bad in (0, 101, 2.5, "x", True, [1]):
        status, error = api.post("/api/settings/agents", {"store_limit_gb": bad}, project=False)
        assert status == 400 and error["state"] == "agent_invalid", bad
        assert profiles.store_limit_gb() == 12, bad
    # a refusal saves none of the keys sent with it, the spending cap included (it comes just before the limit)
    status, error = api.post("/api/settings/agents",
                             {"timeout_minutes": 99, "max_budget_usd": 7, "store_limit_gb": 0}, project=False)
    assert status == 400 and profiles.timeout_minutes() == 40 and profiles.max_budget_usd() is None
    assert api.post("/api/settings/agents", {"store_limit_gb": None}, project=False)[1]["store_limit_gb"] == 5


def test_the_store_endpoints_read_the_panel_release_backups_and_delete_moved_away_files(api_me, monkeypatch):
    api = api_me
    kept = _api_run(api, monkeypatch, undo=True)  # undone: its moved-away files are in reverted/
    api.scenario(COMPLETE + [{"op": "write", "path": "analysis2.py", "text": "print(2)\n"}])
    waiting = api.poll(api.start()["id"])  # not undone: it holds a backup
    assert waiting["state"] == "done"
    status, panel = api.get("/api/agents/store", lang="en")
    assert status == 200 and panel["converting"] in (True, False)
    deadline = time.monotonic() + 20
    while panel["converting"] and time.monotonic() < deadline:  # the engine without a session asks nothing of it
        time.sleep(0.05)
        panel = api.get("/api/agents/store")[1]
    assert panel["limit_gb"] == 5 and panel["total"] > 0 and panel["backups"] > 0 and panel["moved"] > 0
    assert panel["releasable"] == 1 and [row["run"] for row in panel["moved_runs"]] == [kept["id"]]
    assert panel["moved_runs"][0]["task"] == "Analyse the sample" and panel["moved_runs"][0]["task_removed"] is False
    assert api.get(f"/api/agents/store?project={api.pid}")[0] == 200 and api.get(f"/api/agents/store?project={api.root}")[0] == 200

    # a person's act, for the served project: not another project, not a made-up run, not without the session
    stale = str(api.tmp / "previous-project")
    for path in ("/api/agents/store/release", "/api/agents/store/delete-moved"):
        status, error = api.post(path, {"project": stale, "run": kept["id"]}, project=False)
        assert status == 409 and error["state"] == "agent_project_changed", path
    status, error = api.post("/api/agents/store/delete-moved", {})
    assert status == 400 and error["state"] == "agent_invalid"
    status, error = api.post("/api/agents/store/delete-moved", {"run": "r-20200101T000000Z-aaaaaa"})
    assert status == 404 and error["state"] == "agent_run_missing"
    status, error = api.post("/api/agents/store/delete-moved", {"run": "../etc"})
    assert status == 400 and error["state"] == "agent_invalid"
    api.httpd.session = "s" * 64  # from here on a write must carry it (18.1)
    before = tree_of(api)
    for path, body in (("/api/agents/store/release", {}), ("/api/agents/store/delete-moved", {"run": kept["id"]})):
        status, error = api.post(path, body)
        assert status == 403 and error["state"] == "session_required", path
        assert _request_with_headers(
            api.base, "POST", path, {"Host": api.base.removeprefix("http://"), **FOREIGN, "Content-Length": "2"}, b"{}")[0] == 403
    assert tree_of(api) == before
    api.httpd.session = None

    status, answer = api.post("/api/agents/store/delete-moved", {"run": kept["id"]})
    assert status == 200 and answer["freed"] > 0 and answer["moved_runs"] == [] and answer["moved"] == 0
    assert not (run_store.run_dir(api.pid, kept["id"]) / "reverted").exists()
    status, answer = api.post("/api/agents/store/release")
    assert status == 200 and answer["released"] == [waiting["id"]] and answer["releasable"] == 0
    assert answer["backups"] == 0 and api.run_state(waiting["id"])["revertible"] is False
    listing = api.get("/api/agents/runs")[1]["runs"]
    assert [r["can_revert"] for r in listing] == [False, False]


def test_release_over_http_leaves_the_backup_of_a_run_that_is_going(api_me, monkeypatch):
    api = api_me
    api.put("old.txt", "v0\n")
    api.scenario([init_step(), {"op": "write", "path": "old.txt", "text": "v1\n"}, {"op": "sleep", "s": 120}])
    started = api.start()
    api.wait_running(started["id"])
    status, answer = api.post("/api/agents/store/release")
    assert status == 200 and answer["released"] == []
    assert store.holds_backup(run_store.run_dir(api.pid, started["id"]))
    assert api.post(f"/api/agents/runs/{started['id']}/stop")[0] == 200
    api.poll(started["id"])
    assert api.post(f"/api/agents/runs/{started['id']}/revert")[0] == 200  # and it could still be undone


def test_the_store_panel_needs_a_project(tmp_path, monkeypatch):
    a = Api(tmp_path, monkeypatch, with_project=False)
    try:
        status, error = a.get("/api/agents/store")
        assert status == 409 and error["state"] == "no_project"
        for path in ("/api/agents/store/release", "/api/agents/store/delete-moved"):
            status, error = a.post(path, {"project": "x", "run": "r-20260101T000000Z-abcdef"}, project=False)
            assert status == 409 and error["state"] == "no_project", path
    finally:
        a.close()


def tree_of(api) -> dict:
    out = {}
    base = run_store.project_runs_dir(api.pid)
    for here, _dirs, files in os.walk(base):
        for name in files:
            st = os.lstat(os.path.join(here, name))
            out[os.path.join(here, name)] = (st.st_ino, st.st_nlink, st.st_size)
    return out


def test_releases_and_the_deletion_of_unused_blobs_beside_pictures_being_taken_lose_no_backup(tmp_path):
    """The lock of 19.8 and the tolerance of `take`: one thread takes picture after picture (as run after run does,
    each with the one before as `prior`) while another releases the pictures two behind and deletes the blobs nothing links.
    Every copy a picture says it holds is there, with the bytes it recorded."""
    pid = "p-" + "ab" * 16
    run_store.project_runs_dir(pid).mkdir(parents=True)
    blobs_path = store.blobs_dir(pid)
    root = tmp_path / "proj"
    root.mkdir()
    for n in range(30):
        (root / f"f{n:02d}.txt").write_text(f"file {n} version 0\n" * 20)
        os.utime(root / f"f{n:02d}.txt", (OLD, OLD))
    taken: list[Path] = []
    stop, failures = threading.Event(), []

    def sweeper():
        while not stop.is_set():
            with store.lock(pid):
                for old in taken[:-2]:
                    shutil.rmtree(old, ignore_errors=True)
                del taken[:-2]
            store.collect(pid)

    thread = threading.Thread(target=sweeper, daemon=True)
    thread.start()
    prior = None
    try:
        for i in range(40):
            for n in range(i % 5, 30, 7):  # some files change every round, the rest keep their stamp
                path = root / f"f{n:02d}.txt"
                path.write_text(f"file {n} version {i}\n" * 20)
                os.utime(path, (OLD + i, OLD + i))
            copies = tmp_path / f"c{i}"
            picture = snapshot.take(root, copy_to=copies, blobs=blobs_path, prior=prior)
            for rel, entry in picture["files"].items():
                if not entry.get("copied"):
                    failures.append((i, rel, "not copied"))
                elif sha((copies / rel).read_bytes()) != entry["sha256"]:
                    failures.append((i, rel, "wrong bytes"))
            taken.append(copies)
            prior = picture
    finally:
        stop.set()
        thread.join(10)
    assert failures == []

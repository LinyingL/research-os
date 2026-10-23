"""Research OS V14, task G4-4 (DESIGN.md 18.4 items 5 and 6): the file panel
and the drafts read at most 200 KB and one byte of a file, and take its size
from the open file; the change history behind 研究路线 is read by a time index,
only as far as it decides. Every test names its scenario, and each new way
is set beside a copy of the old one."""

from __future__ import annotations

import builtins
import tracemalloc
from pathlib import Path

import pytest

from researchos import cloud
from researchos.webapp import server

from test_v11_dta import _dta

LIMIT = server._FILE_SIZE_LIMIT


# -- the old file panel, kept as it was ----------------------------------------------------------------


def _old_file_payload(project_root: Path, rel_path: str, lang: str = "zh") -> dict:
    """`server.file_payload` before 18.4 item 5: the whole file is read, and
    its length is the size."""
    target = server._resolve_within_root(project_root, rel_path)
    if not target.is_file():
        if not target.exists():
            raise server.NotFoundError(f"no such file: {rel_path}")
        raise server.NotAFileError(f"not a regular file: {rel_path}")
    link = server.github_link_payload(project_root, target)
    if target.suffix.lower() == ".dta":
        return server._dta_payload(target, rel_path, link, lang)
    try:
        raw = cloud.read_bytes(target)
    except cloud.CloudOnlyError as exc:
        raise server.CloudOnlyApiError(exc, {"github": link}) from exc
    if b"\x00" in raw[:8192]:
        error = server.BinaryFileError(f"{rel_path} looks like a binary file; refusing to return its content")
        error.extra = {"github": link}
        raise error
    truncated = len(raw) > server._FILE_SIZE_LIMIT
    content_bytes = raw[:server._FILE_SIZE_LIMIT] if truncated else raw
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        if not truncated:
            error = server.BinaryFileError(f"{rel_path} is not valid UTF-8 text; refusing to return its content")
            error.extra = {"github": link}
            raise error from None
        content = content_bytes.decode("utf-8", errors="ignore")
    return {"path": rel_path, "content": content, "truncated": truncated, "size": len(raw), "github": link}


def _answer(fn, root: Path, rel: str):
    """What a panel call came to: its payload, or the refusal's class, text and body."""
    try:
        return ("ok", fn(root, rel))
    except server.ApiError as exc:
        return (type(exc).__name__, str(exc), getattr(exc, "extra", None), exc.status)


EDGE_FILES = {
    "empty.txt": b"",
    "one.txt": b"x",
    "short.txt": "héllo wörld\n".encode(),
    "exact.txt": b"a" * LIMIT,
    "over_by_one.txt": b"a" * (LIMIT + 1),
    "over_by_a_lot.txt": b"line of text\n" * 40_000,
    # a two-byte character whose first byte is the last byte kept, and whose second is the byte read past it
    "cut_two_byte.txt": b"a" * (LIMIT - 1) + "é".encode() + b"tail",
    "cut_two_byte_exact.txt": b"a" * (LIMIT - 1) + "é".encode(),
    # a three-byte character cut after one byte and after two
    "cut_three_a.txt": b"a" * (LIMIT - 1) + "字".encode() + b"tail",
    "cut_three_b.txt": b"a" * (LIMIT - 2) + "字".encode() + b"tail",
    # a four-byte character that ends exactly at the limit, and one that starts at it
    "whole_four_byte.txt": b"a" * (LIMIT - 4) + "\U0001F600".encode() + b"tail",
    "starts_at_limit.txt": b"a" * LIMIT + "\U0001F600".encode(),
    "bom.txt": b"\xef\xbb\xbfhello",
    "nul_first.bin": b"\x00" + b"abc" * 100,
    "nul_last_in_window.bin": b"a" * 8191 + b"\x00" + b"b" * 100,
    "nul_just_outside_window.txt": b"a" * 8192 + b"\x00" + b"b" * 100,
    "nul_past_the_limit.txt": b"a" * (LIMIT + 5) + b"\x00",
    "latin1_short.txt": "café au lait\n".encode("latin-1"),
    "latin1_long.txt": ("café au lait\n" * 30_000).encode("latin-1"),
    "latin1_after_the_limit.txt": b"a" * (LIMIT + 10) + "café".encode("latin-1"),
    "invalid_at_end.txt": b"hello \xff",
    "crlf.txt": b"one\r\ntwo\r\n",
}


@pytest.fixture()
def edge_project(tmp_path):
    for name, data in EDGE_FILES.items():
        (tmp_path / name).write_bytes(data)
    (tmp_path / "auto.dta").write_bytes(_dta(118, "LSF", [("make", "str12", "%12s", "Make", ""),
                                                           ("price", "int", "%8.0g", "Price", "")], rows=7, label="cars"))
    (tmp_path / "broken.dta").write_bytes(b"not a dta file " * 20)
    (tmp_path / "adir").mkdir()
    return tmp_path


def test_the_panel_answers_as_before_on_the_edge_files(edge_project):
    """18.4 item 5: for every edge file (empty, exactly 200 KB, 200 KB and one byte, a multi-byte
    character cut at the boundary, a NUL inside and outside the 8 KB window, text that is not
    UTF-8 before and after the limit, a BOM) the payload, or the refusal with its body and status,
    is what the whole-file read gave; a .dta returns early; a missing file and a folder are refused."""
    names = [*EDGE_FILES, "auto.dta", "broken.dta", "adir", "missing.txt", "../outside.txt"]
    for name in names:
        new, old = _answer(server.file_payload, edge_project, name), _answer(_old_file_payload, edge_project, name)
        assert new == old, name
    got = server.file_payload(edge_project, "exact.txt")
    assert got["truncated"] is False and got["size"] == LIMIT and len(got["content"]) == LIMIT
    got = server.file_payload(edge_project, "over_by_one.txt")
    assert got["truncated"] is True and got["size"] == LIMIT + 1 and len(got["content"]) == LIMIT
    got = server.file_payload(edge_project, "latin1_after_the_limit.txt")  # today's handling, unchanged
    assert got["truncated"] is True and got["content"] == "a" * LIMIT
    assert _answer(server.file_payload, edge_project, "latin1_long.txt")[0] == "ok"
    assert _answer(server.file_payload, edge_project, "latin1_short.txt")[0] == "BinaryFileError"
    assert server.file_payload(edge_project, "auto.dta")["kind"] == "dta"


def test_the_panel_over_http_answers_as_before(tmp_path):
    """18.4 item 5: GET /api/file through the handler's own payload function gives the same
    bytes of JSON as the old read for a truncated, a whole and a refused file."""
    import json

    for name in ("over_by_a_lot.txt", "short.txt", "nul_first.bin", "cut_two_byte.txt"):
        (tmp_path / name).write_bytes(EDGE_FILES[name])
        new, old = _answer(server.file_payload, tmp_path, name), _answer(_old_file_payload, tmp_path, name)
        assert json.dumps(new, sort_keys=True, default=str) == json.dumps(old, sort_keys=True, default=str)


# -- how much is read ---------------------------------------------------------------------------------


def _sparse(path: Path, head: bytes, size: int) -> None:
    with open(path, "wb") as handle:
        handle.write(head)
        handle.truncate(size)


def _peak(fn) -> tuple[object, int]:
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        result = fn()
        return result, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_previewing_a_64_mb_file_holds_a_few_times_the_limit_at_most(tmp_path):
    """18.4 item 5 and 18.5: a 64 MB sparse file whose first 300 KB is text previews with the
    same payload as the whole read, and Python's memory peaks under 4 times the limit; the
    old way held the file."""
    _sparse(tmp_path / "huge.txt", b"0123456789\n" * 30_000, 64 * 1024 * 1024)
    new, new_peak = _peak(lambda: server.file_payload(tmp_path, "huge.txt"))
    assert new["truncated"] is True and new["size"] == 64 * 1024 * 1024 and len(new["content"]) == LIMIT
    assert new_peak < 4 * LIMIT, new_peak
    old, old_peak = _peak(lambda: _old_file_payload(tmp_path, "huge.txt"))
    assert old_peak > 32 * 1024 * 1024  # the check measures what it claims to
    assert new == old


def test_previewing_a_one_gigabyte_file_raises_memory_by_under_five_megabytes(tmp_path):
    """18.5: a 1 GB sparse file (1 GB of nothing but a head of text) previews in under 5 MB."""
    _sparse(tmp_path / "gig.txt", b"abc\n" * 100_000, 1024 * 1024 * 1024)
    got, peak = _peak(lambda: server.file_payload(tmp_path, "gig.txt"))
    assert got["size"] == 1024 ** 3 and got["truncated"] is True
    assert peak < 5 * 1024 * 1024, peak


def test_the_read_never_asks_for_more_than_the_limit_and_one_byte(tmp_path, monkeypatch):
    """18.4 item 5: the file is opened once, unbuffered, and read in requests that add up to the
    limit and one byte -- never the whole file, never `read()` without a size."""
    (tmp_path / "big.txt").write_bytes(b"a" * (3 * LIMIT))
    asked: list[int] = []
    real_open = builtins.open

    class Spy:
        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *exc):
            return self.handle.__exit__(*exc)

        def read(self, n=-1):
            asked.append(n)
            return self.handle.read(n)

    def spy_open(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        return Spy(handle) if str(file).endswith("big.txt") else handle

    monkeypatch.setattr(builtins, "open", spy_open)
    got = server.file_payload(tmp_path, "big.txt")
    monkeypatch.setattr(builtins, "open", real_open)
    assert got["truncated"] is True and got["size"] == 3 * LIMIT
    assert asked and all(n > 0 for n in asked) and sum(asked) == LIMIT + 1


# -- read_head ---------------------------------------------------------------------------------------


def test_read_head_gives_the_head_and_the_size_of_the_open_file(tmp_path):
    """18.4 item 5: `cloud.read_head(path, limit)` is (the first `limit` bytes, the file's size);
    a shorter file comes back whole, a limit of 0 gives nothing, an empty file gives nothing."""
    (tmp_path / "f").write_bytes(b"0123456789")
    assert cloud.read_head(tmp_path / "f", 4) == (b"0123", 10)
    assert cloud.read_head(str(tmp_path / "f"), 10) == (b"0123456789", 10)
    assert cloud.read_head(tmp_path / "f", 11) == (b"0123456789", 10)
    assert cloud.read_head(tmp_path / "f", 1000) == (b"0123456789", 10)
    assert cloud.read_head(tmp_path / "f", 0) == (b"", 10)
    (tmp_path / "e").write_bytes(b"")
    assert cloud.read_head(tmp_path / "e", 5) == (b"", 0)
    with pytest.raises(FileNotFoundError):
        cloud.read_head(tmp_path / "nope", 5)


def test_read_head_asks_the_same_cloud_question_before_it_opens_anything(tmp_path, monkeypatch):
    """11.1: a file only in a cloud whose client is not running is refused before it is opened, as
    `read_bytes` refuses it; the panel and a draft answer with the sentence."""
    (tmp_path / "paper.md").write_text("# Results\n")
    prov = cloud.Provider("OneDrive", "OneDrive", "OneDrive-x", str(tmp_path))
    monkeypatch.setattr(cloud, "blocked_provider", lambda path: prov)
    real_open = builtins.open

    def no_open(file, *args, **kwargs):
        raise AssertionError(f"{file} was opened")

    monkeypatch.setattr(builtins, "open", no_open)
    with pytest.raises(cloud.CloudOnlyError):
        cloud.read_head(tmp_path / "paper.md", 10)
    with pytest.raises(server.CloudOnlyApiError) as refused:
        server.file_payload(tmp_path, "paper.md")
    assert refused.value.extra["message_zh"].startswith("这些文件只在 OneDrive 云端")
    with pytest.raises(server.DraftError) as draft:
        server._draft_file(tmp_path, "paper.md", "paper.md")
    assert draft.value.state == "draft_file_cloud_only"
    monkeypatch.setattr(builtins, "open", real_open)


# -- drafting -----------------------------------------------------------------------------------------


def test_a_draft_reads_the_chosen_files_through_the_same_bounded_read(tmp_path, monkeypatch):
    """18.4 item 5: `_draft_file` (hence the draft from files) reads a big chosen file as the panel
    does -- its head and its size, nothing more -- and gives the panel's payload."""
    _sparse(tmp_path / "huge.R", b"x <- 1\n" * 60_000, 64 * 1024 * 1024)
    (tmp_path / "small.R").write_text("y <- 2\n")
    monkeypatch.setattr(cloud, "read_bytes", lambda path: (_ for _ in ()).throw(AssertionError("whole read")))
    got, peak = _peak(lambda: server._draft_file(tmp_path, "huge.R", "huge.R"))
    assert got == server.file_payload(tmp_path, "huge.R") and got["truncated"] is True
    assert peak < 4 * LIMIT, peak
    assert server._draft_file(tmp_path, "small.R", "small.R")["content"] == "y <- 2\n"
    (tmp_path / "blob.bin").write_bytes(b"\x00" * 100)
    with pytest.raises(server.DraftError) as refused:
        server._draft_file(tmp_path, "blob.bin", "blob.bin")
    assert refused.value.state == "draft_file_not_text"


# -- the change history behind 研究路线 (18.4 item 6) -----------------------------------------------------


import json  # noqa: E402
import os  # noqa: E402
import random  # noqa: E402
import sqlite3  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from researchos import desk, history  # noqa: E402
from researchos.records.identity import read_identity  # noqa: E402

from test_v8_phase2 import _conn, _csv, _project, _scan  # noqa: E402
from test_v8_u2 import _backdate  # noqa: E402

A = "attempt:map.md#16"
B = "attempt:map.md#17"
S1, S2, S3 = "script:steps/16-build.py", "script:steps/17-plot.py", "dataset:data/panel.csv"


def _put(store: history.Store, rows: list[tuple]) -> None:
    """(node, at, source) rows, in order, straight into the store (the kind follows the node's name)."""
    store.conn.executemany(
        "INSERT INTO versions (node, at, sha256, size, who, source, note, kind, stored, summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(node, at, f"{i:064x}", None, None, source, None, ("attempt" if node.startswith("attempt:") else "text"), 0, "{}")
         for i, (node, at, source) in enumerate(rows)])
    store.conn.commit()


def _fresh(tmp_path: Path, rows: list[tuple], name: str = "h") -> history.Store:
    store = history.Store(tmp_path / name)
    _put(store, rows)
    return store


def _agree(store: history.Store, nodes=None) -> dict:
    """`_window_times` (what the desk reads now) against `_scan_times` (every version, as before)."""
    scan, window = desk._scan_times(store), desk._window_times(store, nodes)
    assert window is not None, "the indexes could not decide"
    expected = scan if nodes is None else {n: scan[n] for n in set(nodes) if n in scan}
    assert window == expected, (window, expected)
    return window


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)


def test_a_store_gets_its_time_indexes_and_goes_without_them_when_it_cannot_be_written(tmp_path):
    """18.4 item 6: a new store has idx_versions_at (on julianday(at)) and the one by node; a store
    made before them gets them when it is opened; a store that cannot be written is opened as it is
    and answers by a scan, with the same times."""
    def names(directory: Path) -> set[str]:
        with sqlite3.connect(directory / history.VERSIONS_FILENAME) as conn:
            return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}

    rows = [(A, "2026-10-01T09:00:00+02:00", "scan"), (A, "2026-10-03T09:00:00+02:00", "scan"),
            (S1, "2026-10-01T09:00:01+02:00", "scan"), (S1, "2026-10-05T10:00:00+02:00", "scan")]
    store = _fresh(tmp_path, rows)
    assert {"idx_versions_node", "idx_versions_at", "idx_versions_node_at"} <= names(tmp_path / "h")
    with store.conn:
        store.conn.execute("DROP INDEX idx_versions_at")
        store.conn.execute("DROP INDEX idx_versions_node_at")
    store.close()
    assert names(tmp_path / "h") == {"idx_versions_node"}
    reopened = history.Store(tmp_path / "h")
    assert {"idx_versions_at", "idx_versions_node_at"} <= names(tmp_path / "h") and reopened.time_readable()
    expected = _agree(reopened)
    assert expected == {A: datetime.fromisoformat("2026-10-03T09:00:00+02:00"), S1: datetime.fromisoformat("2026-10-05T10:00:00+02:00")}
    with reopened.conn:
        reopened.conn.execute("DROP INDEX idx_versions_at")
    reopened.close()
    path = tmp_path / "h" / history.VERSIONS_FILENAME
    path.chmod(0o444)
    try:
        if os.access(path, os.W_OK):
            pytest.skip("files are writable whatever their mode (running as root)")
        locked = history.Store(tmp_path / "h")
        assert not locked.time_readable() and "idx_versions_at" not in names(tmp_path / "h")
        assert desk._window_times(locked, None) is None and desk._scan_times(locked) == expected
        locked.close()
    finally:
        path.chmod(0o644)


def test_times_are_ordered_by_the_instant_not_by_the_text(tmp_path):
    """The clocks go back on 2026-10-25 at 03:00 (+02:00 to +01:00): 02:45+02:00 is 00:45 UTC and
    02:15+01:00 is 01:15 UTC, later although its text sorts first. The spring forward the same way.
    The newest of each node is the later instant, as the scan says."""
    rows = [
        # the first scan of the files was on 2026-03-29 (S2's first version is its baseline), of the attempts on 10-01
        (S2, "2026-03-29T01:59:00+01:00", "scan"), (A, "2026-10-01T09:00:00+02:00", "scan"),
        (S1, "2026-10-01T09:00:00+02:00", "scan"), (S3, "2026-10-01T09:00:00+02:00", "scan"),
        (A, "2026-10-25T02:45:00+02:00", "scan"), (A, "2026-10-25T02:15:00+01:00", "approval"),
        (S1, "2026-10-25T02:15:00+01:00", "scan"), (S1, "2026-10-25T02:45:00+02:00", "scan"),
        (S2, "2026-03-29T03:00:00+02:00", "scan"),
        (S3, "2026-10-25T01:30:00+00:00", "scan"), (S3, "2026-10-25T02:10:00+01:00", "agent"),
    ]
    got = _agree(_fresh(tmp_path, rows))
    assert got[A] == _utc(2026, 10, 25, 1, 15) and got[S1] == _utc(2026, 10, 25, 1, 15)
    assert got[S2] == datetime.fromisoformat("2026-03-29T03:00:00+02:00") and got[S3] == _utc(2026, 10, 25, 1, 30)


@pytest.mark.parametrize("zone", ["UTC", "Europe/Berlin", "America/New_York", "Asia/Kolkata", "Pacific/Kiritimati",
                                  "Pacific/Pago_Pago"])
def test_readings_that_differ_between_sqlite_and_python_still_agree(tmp_path, monkeypatch, zone):
    """A time with no offset is UTC to SQLite and local time to Python (up to 14 hours apart); 'Z',
    fractions and offsets of every kind are read alike. In every zone the window gives the scan's times."""
    monkeypatch.setenv("TZ", zone)
    time.tzset()
    try:
        rows = [(A, "2026-10-01T09:00:00", "scan"), (S1, "2026-10-01T09:00:00Z", "scan"), (S2, "2026-10-01T09:00:00.250+05:30", "scan"),
                (A, "2026-10-04T23:30:00", "scan"), (A, "2026-10-04T23:30:00Z", "scan"), (A, "2026-10-04T08:00:00-05:00", "agent"),
                (S1, "2026-10-04T23:59:59.999999+00:00", "scan"), (S1, "2026-10-05T00:00:00", "scan"),
                (S2, "2026-10-02 12:00:00", "scan"), (S2, "2026-10-03T12:00:00+14:00", "scan"), (S2, "2026-10-03T12:00:00-11:00", "scan"),
                (S3, "2026-10-01T09:00:00+01:00", "publish"), (S3, "2026-10-04T10:00:00", "scan")]
        _agree(_fresh(tmp_path, rows))
    finally:
        monkeypatch.undo()
        time.tzset()


def test_the_baseline_rule_is_the_scans(tmp_path):
    """The first version of a node is no change when a scan or a publish recorded it within 10
    minutes of the earliest of its kind (attempts apart from files); 9:59 is within, 10:00 is not;
    approvals and agents are changes; the kind is its newest version's."""
    t0 = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    def at(**kw) -> str:
        return (t0 + timedelta(**kw)).isoformat(timespec="seconds")

    rows = [
        (A, at(), "scan"), (B, at(minutes=9, seconds=59), "scan"), ("attempt:map.md#18", at(minutes=10), "scan"),
        ("attempt:map.md#19", at(minutes=5), "approval"),
        (S1, at(days=3), "scan"), (S2, at(days=3, minutes=9, seconds=59), "publish"), (S3, at(days=3, minutes=10), "scan"),
        ("script:late.py", at(days=3, minutes=1), "agent"), ("script:fresh.py", at(days=3, minutes=1), "scan"),
        (A, at(days=5), "scan"), (S1, at(days=6), "scan"), (S2, at(days=7), "scan"), ("script:late.py", at(days=8), "scan"),
        (B, at(days=9), "approval"), ("attempt:map.md#19", at(days=2), "scan"),
    ]
    store = _fresh(tmp_path, rows)
    got = _agree(store)
    assert "script:fresh.py" not in got and "attempt:map.md#18" in got and S3 in got and "script:late.py" in got
    assert got[B] == t0 + timedelta(days=9) and got["attempt:map.md#19"] == t0 + timedelta(days=2)
    assert _agree(store, [A, S3, "script:fresh.py", "no:such"]) == {A: got[A], S3: got[S3]}


def test_a_version_outside_the_window_can_still_be_the_answer(tmp_path):
    """The window is two days wide round a node's newest; where the node's versions are not in time
    order, or its first version is the newest and the baseline, the older ones are read: the answers
    are the scan's. A hot node with thousands of versions in one day is read in full."""
    t0 = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)

    def at(**kw) -> str:
        return (t0 + timedelta(**kw)).isoformat(timespec="seconds")

    rows = [
        (A, at(), "scan"), (S1, at(), "scan"), (S2, at(minutes=9), "scan"), (S3, at(days=300), "scan"),
        # the newest version was recorded first of the later ones, then older times follow (a clock set back)
        (S1, at(days=100), "scan"), (S1, at(days=1), "scan"), (S1, at(days=2), "scan"),
        # a baseline first version that is the newest of its node: the version before it in time is the answer
        (S2, at(), "scan"),
        # a hot node
        *[("script:hot.py", at(days=30, seconds=i), "scan") for i in range(2500)],
        (A, at(days=40), "scan"), (A, at(days=2), "scan"),
    ]
    store = _fresh(tmp_path, rows)
    got = _agree(store)
    assert got[S1] == t0 + timedelta(days=100) and got["script:hot.py"] == t0 + timedelta(days=30, seconds=2499)
    assert got[S2] == t0 and got[S3] == t0 + timedelta(days=300) and got[A] == t0 + timedelta(days=40)
    assert max(len(w.rows) for w in store.node_windows().values()) == 2500


def test_a_reading_that_puts_the_newest_version_far_back_reads_the_node_in_full(tmp_path, monkeypatch):
    """Where Python reads a version much earlier than SQLite (so the window cannot hold the answer),
    the node's versions are all read: the answer is the scan's, and the window alone would have been
    wrong. (No text reads that far apart; the reading is made up here to hold the check.)"""
    t0 = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    odd = "2026-12-01T00:00:00+00:00"
    real = desk._parse
    monkeypatch.setattr(desk, "_parse", lambda at: real("2026-09-21T00:00:00+00:00") if at == odd else real(at))
    rows = [(A, t0.isoformat(timespec="seconds"), "scan"), (S1, t0.isoformat(timespec="seconds"), "scan"),
            (S1, "2026-10-01T00:00:00+00:00", "scan"), (S1, odd, "scan"), (S2, odd, "scan")]
    store = _fresh(tmp_path, rows)
    narrow = store.node_windows([S1])[S1]
    assert narrow.count == 3 and [at for _seq, at in narrow.rows] == [odd]  # the window holds the odd version only
    assert desk._scan_times(store)[S1] == real("2026-10-01T00:00:00+00:00")
    assert desk._window_times(store, [S1]) == {S1: real("2026-10-01T00:00:00+00:00")}


def test_nothing_in_the_store_and_nothing_asked(tmp_path):
    """An empty store, a node list with nothing in it and a node the store has not heard of give
    nothing, as the scan does."""
    empty = _fresh(tmp_path, [], "empty")
    assert _agree(empty) == {} and _agree(empty, [A]) == {}
    store = _fresh(tmp_path, [(A, "2026-10-01T09:00:00+02:00", "scan"), (A, "2026-10-02T09:00:00+02:00", "scan")], "one")
    assert _agree(store, []) == {} and _agree(store, ["script:nope.py"]) == {}
    assert list(_agree(store, [A])) == [A] and list(_agree(store)) == [A]


@pytest.mark.parametrize("bad", [
    "", "not a time", "now", "20261022", "2026", "10:00:00", "2026-13-45T00:00:00Z", "0001-01-01T00:00:00Z", "9999-12-31T00:00:00Z",
    "2026-10-22T10:00:00+02:00 ", "2026-10-22T10:00:00+02:00\n", "2026-10-22T10:00:00z",  # SQLite reads, Python does not
    "2026-10-22T10:00:00+0200", "2026-10-22T10", "2026-10-22T10:00:00,5Z", "2026-W43-4T10:00:00Z", "20261022T100000Z"])  # the other way
def test_a_time_the_indexes_cannot_vouch_for_sends_the_desk_to_the_scan(tmp_path, bad):
    """A version whose `at` SQLite cannot read as a time (or reads as a number or a word), or reads
    far from Python, or that Python cannot read: the window says so (None) and the desk's answer is the
    scan's, whatever the value is. ('now' cannot even be indexed; the store simply goes without.)"""
    rows = [(A, "2026-10-01T09:00:00+02:00", "scan"), (S1, "2026-10-01T09:00:00+02:00", "scan"),
            (A, "2026-10-04T09:00:00+02:00", "scan"), (S1, "2026-10-04T09:00:00+02:00", "scan"), (S1, bad, "scan"),
            (S2, bad, "scan"), (S2, "2026-10-05T09:00:00+02:00", "agent")]
    store = history.Store(tmp_path / "h")
    with store.conn:  # a store from before the indexes, holding such a text
        store.conn.execute("DROP INDEX idx_versions_at")
        store.conn.execute("DROP INDEX idx_versions_node_at")
    _put(store, rows)
    store.close()
    store = history.Store(tmp_path / "h")
    scan = desk._scan_times(store)
    trusted = desk._window_times(store, None)
    julian = lambda text: sqlite3.connect(":memory:").execute("SELECT julianday(?)", (text,)).fetchone()[0]  # noqa: E731
    sqlite_reads = julian(bad) is not None and history._JULIAN_1970 <= julian(bad) <= history._JULIAN_2200
    if not sqlite_reads or desk._parse(bad) is None:
        assert trusted is None  # not a time SQLite reads, or one in a window that Python does not ("10:00:00" is year 2000)
    else:
        assert trusted == scan
    assert desk._scan_times(store) == scan and scan[A] == datetime.fromisoformat("2026-10-04T09:00:00+02:00")


def test_a_baseline_window_python_cannot_read_is_left_to_the_scan(tmp_path):
    """The earliest versions of the files are written in a way SQLite reads and Python does not (a
    trailing space); the earliest the scan reads is a later one, so the baseline is not the window's:
    the window gives way to the scan, whose answer is kept."""
    rows = [(S1, "2026-09-01T08:00:00+02:00 ", "scan"), (S2, "2026-09-01T08:00:30+02:00 ", "scan"),
            (S1, "2026-09-10T08:00:00+02:00", "scan"), (S2, "2026-09-10T08:05:00+02:00", "scan"),
            (S3, "2026-09-10T08:00:00+02:00", "scan"), (S3, "2026-09-12T08:00:00+02:00", "scan")]
    store = _fresh(tmp_path, rows)
    scan = desk._scan_times(store)
    assert desk._window_times(store, None) is None
    # the scan reads the baseline from the readable versions: the files' first scan was on 09-10
    assert scan[S1] == datetime.fromisoformat("2026-09-10T08:00:00+02:00") and S3 in scan
    assert scan[S2] == datetime.fromisoformat("2026-09-10T08:05:00+02:00") and scan[S3] == datetime.fromisoformat("2026-09-12T08:00:00+02:00")


def test_the_desk_falls_back_to_the_scan_for_an_unreadable_time(tmp_path, monkeypatch):
    """`_change_times` with a store holding a time the index cannot vouch for answers what the scan
    answers, and reads the whole store to do it."""
    from researchos import paths

    monkeypatch.setattr(paths, "index_dir", lambda project_id: tmp_path / "graphs" / project_id)
    store = history.Store.open("proj")
    _put(store, [(A, "2026-10-01T09:00:00+02:00", "scan"), (A, "2026-10-04T09:00:00+02:00", "scan"), (S1, "garbage", "scan"),
                 (S1, "2026-10-02T09:00:00+02:00", "scan")])
    store.close()
    scans = []
    real = desk._scan_times
    monkeypatch.setattr(desk, "_scan_times", lambda s: scans.append(1) or real(s))
    got = desk._change_times(tmp_path / "no-git", "proj", [A, S1])
    assert scans == [1] and got[A] == datetime.fromisoformat("2026-10-04T09:00:00+02:00")
    clean = history.Store.open("proj")
    clean.conn.execute("DELETE FROM versions WHERE at = 'garbage'")
    clean.conn.commit()
    clean.close()
    assert desk._change_times(tmp_path / "no-git", "proj", [A, S1])[A] == got[A] and scans == [1]  # the window this time


def _random_store(tmp_path: Path, seed: int, name: str) -> tuple[history.Store, bool]:
    """A store of random versions: a few nodes, times in every reading (offsets, 'Z', none, fractions),
    sometimes out of order, sometimes in bursts at the start; a seed with `dirty` has a reading SQLite or
    Python cannot take. Returns the store and whether it is clean."""
    rnd = random.Random(seed)
    nodes = [A, B, S1, S2, S3, "script:x.py", "dataset:y.csv"][:rnd.randint(1, 7)]
    zones = ["+02:00", "+01:00", "+00:00", "Z", "", "-05:00", "+05:30", "+14:00", "-11:00"]
    t0 = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
    rows = []
    for _ in range(rnd.randint(1, 45)):
        node = rnd.choice(nodes)
        shape = rnd.random()
        seconds = rnd.randint(0, 600) if shape < 0.35 else rnd.randint(0, 400 * 86400) if shape < 0.8 else rnd.randint(0, 3 * 86400)
        when = t0 + timedelta(seconds=seconds, microseconds=rnd.choice([0, 0, 250_000, 999_999]))
        zone = rnd.choice(zones)
        local = when + (timedelta(hours=int(zone[:3]), minutes=int(zone[0] + zone[4:6])) if zone[1:2].isdigit() else timedelta())
        text = local.replace(tzinfo=None).isoformat(timespec="seconds" if when.microsecond == 0 else "microseconds") + zone
        rows.append((node, text, rnd.choice(["scan", "scan", "scan", "approval", "publish", "agent"])))
    if rnd.random() < 0.5:
        rnd.shuffle(rows)
    dirty = rnd.random() < 0.12
    if dirty:
        rows.insert(rnd.randint(0, len(rows)), (rnd.choice(nodes), rnd.choice(["bad", "", "2026-10-22 10:00:00 +02:00", "20261022"]), "scan"))
    return _fresh(tmp_path, rows, name), not dirty


@pytest.mark.parametrize("zone", ["Europe/Berlin", "Asia/Kolkata", "Pacific/Kiritimati", "America/Los_Angeles"])
def test_random_stores_give_the_scans_times_whatever_they_hold(tmp_path, monkeypatch, zone):
    """300 random stores per zone, in every reading of a time, in and out of time order, with the odd
    version SQLite or Python cannot read: the window's times are the scan's times for every node and for
    random subsets of them, and a store with nothing odd is always answered from the window."""
    monkeypatch.setenv("TZ", zone)
    time.tzset()
    try:
        rnd = random.Random(zone)
        windowed = 0
        for seed in range(300):
            store, clean = _random_store(tmp_path, seed, f"s{seed}")
            scan = desk._scan_times(store)
            for nodes in (None, rnd.sample([A, B, S1, S2, S3, "script:x.py", "dataset:y.csv", "no:such"], rnd.randint(0, 5))):
                got = desk._window_times(store, nodes)
                if clean:
                    assert got is not None, seed
                if got is not None:
                    windowed += 1
                    expected = scan if nodes is None else {n: scan[n] for n in set(nodes) if n in scan}
                    assert got == expected, (seed, nodes, got, expected)
            store.close()
        assert windowed > 500
    finally:
        monkeypatch.undo()
        time.tzset()


def _big_store(directory: Path, versions: int = 100_000, nodes: int = 3_000, seed: int = 7) -> history.Store:
    """`versions` versions of `nodes` nodes (24 attempts among them) over a year, the first of each recorded by
    the first scan, with summaries as a real store has them."""
    rnd = random.Random(seed)
    store = history.Store(directory)
    t0 = datetime(2025, 10, 22, 8, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    names = [f"attempt:map.md#{i}" for i in range(24)] + [f"script:code/f{i}.py" for i in range(nodes - 24)]
    per = dict.fromkeys(names, 1)
    for _ in range(versions - len(names)):
        per[rnd.choice(names)] += 1
    rows = []
    for node in names:
        stamps = sorted([t0 + timedelta(seconds=rnd.randint(0, 120))]
                        + [t0 + timedelta(seconds=rnd.randint(300, 365 * 86400)) for _ in range(per[node] - 1)])
        rows += [(s, node, "attempt" if node.startswith("attempt:") else rnd.choice(["text", "data"]),
                  "scan" if i == 0 else rnd.choice(["scan", "approval", "agent"])) for i, s in enumerate(stamps)]
    rows.sort(key=lambda r: r[0])
    summary = json.dumps({"rows": 1200, "cols": ["a", "b", "c", "d"], "note": "x" * 120})
    store.conn.executemany(
        "INSERT INTO versions (node, at, sha256, size, who, source, note, kind, stored, summary) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(node, s.astimezone(timezone(timedelta(hours=1 if s.month in (11, 12, 1, 2, 3) else 2))).isoformat(timespec="seconds"),
          f"{i:064x}", 100, None, source, None, kind, 1, summary) for i, (s, node, kind, source) in enumerate(rows)])
    store.conn.commit()
    return store


def _best_ms(fn, rounds: int = 5) -> tuple[object, float]:
    best, result = 1e9, None
    for _ in range(rounds):
        start = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - start)
    return result, best * 1000


def test_the_history_of_100000_versions_is_read_in_a_window(tmp_path):
    """18.4 item 6 and 18.5: 100,000 versions of 3,000 nodes. The window gives the scan's times for all
    3,000 nodes, and for the 300 the desk shows, in a few tens of milliseconds where the scan took 300 and
    more. The target is under 60 ms on the desk's nodes; the assertion is 300 ms, to keep a slow machine
    from failing it (the measured numbers are in the commit message)."""
    store = _big_store(tmp_path / "big")
    assert store.counts() == (100_000, 3_000)
    scan, scan_ms = _best_ms(lambda: desk._scan_times(store), 2)
    shown = sorted(scan)[:300]
    everything, all_ms = _best_ms(lambda: desk._window_times(store, None))
    wanted, shown_ms = _best_ms(lambda: desk._window_times(store, shown))
    assert everything == scan and wanted == {n: scan[n] for n in shown}
    assert shown_ms < 300 and all_ms < 300
    assert shown_ms < scan_ms and all_ms < scan_ms
    print(f"history window at 100,000 versions: scan {scan_ms:.0f} ms, window for 3,000 nodes {all_ms:.0f} ms, for 300 {shown_ms:.0f} ms")


def test_the_desk_payload_is_the_same_with_and_without_the_indexes(tmp_path, monkeypatch):
    """18.4 item 6: a real project with changes (a script edited, data replaced, a verdict moved) and
    versions recorded across a change of clocks, a clock set back and a time with no offset: the whole
    /api/desk payload (items, order, `changed`, `changed_more`, `changed_at`, the column) is byte for
    byte what the full read gives, and the window is what answers."""
    root = _project(tmp_path / "proj")
    _backdate(root, 10)
    (root / "steps" / "16-build.py").write_text((root / "steps" / "16-build.py").read_text() + "df = df.dropna()\n")
    (root / "data" / "panel.csv").write_text(_csv(97, ("id", "x", "log_ret")))
    (root / "map.md").write_text((root / "map.md").read_text().replace("🕒", "☠️ 伪协整", 1))
    _scan(root)
    pid = read_identity(root).identity.id
    store = history.Store.existing(pid)
    nodes = [row[0] for row in store.conn.execute("SELECT DISTINCT node FROM versions ORDER BY node")]
    extra = []
    for i, node in enumerate(nodes):
        extra += [(node, "2026-10-25T02:45:00+02:00", "scan"), (node, "2026-10-25T02:15:00+01:00", "scan"),
                  (node, f"2026-10-0{1 + i % 9}T03:00:00", "scan"), (node, "2026-10-20T09:00:00.5Z", "scan")]
    _put(store, extra)
    store.close()
    seen = []
    real_scan, real_window = desk._scan_times, desk._window_times
    monkeypatch.setattr(desk, "_scan_times", lambda s: seen.append("scan") or real_scan(s))
    monkeypatch.setattr(desk, "_window_times", lambda s, n: seen.append("window") or real_window(s, n))
    conn = _conn(root)
    try:
        new = desk.payload(conn, root)
        assert seen == ["window"], seen
        monkeypatch.setattr(history.Store, "time_readable", lambda self: False)
        old = desk.payload(conn, root)
    finally:
        conn.close()
    assert seen == ["window", "window", "scan"], seen
    assert json.dumps(new, sort_keys=True, default=str) == json.dumps(old, sort_keys=True, default=str)
    assert any(a["changed"] for a in new["attempts"]) and any(a["changed_at"] for a in new["attempts"])
    assert [a["changed"] for a in new["attempts"]] == [a["changed"] for a in old["attempts"]]

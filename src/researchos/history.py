"""Every node: what changed (DESIGN.md 12.6, task V8 phase 2).

A node that is a file -- a script, a data file, a draft, a figure, a record
file -- has versions: every content Research OS has seen, with when, who
(when known; "本机" for what a scan saw on this machine) and the note that
came with it.

Where the versions come from
----------------------------

- **A file git tracks**: its commits (`git log --follow`), with author, date
  and subject -- git is the history, nothing is copied. A tracked file whose
  working copy differs from HEAD gets one more, unsaved version first
  (`worktree`). Every git command here is hardened as 10.9 and 11.6 ask:
  the repository's fsmonitor and hooks off, no signature program, no
  pager, no optional locks; contents come from `git cat-file blob`, which
  runs no filter and no textconv, and the diff is computed here.
- **Any other file** (a project that is not a git repository, or a file git
  does not track -- the record files, an attempt table kept outside git):
  the version store beside the index, `<home>/graphs/<id>/history/`, filled
  by every scan (`record_scan`). Text files up to 5 MB are kept by content,
  content-addressed (`blobs/<sha256>`); larger and binary files by
  fingerprint and size, and tables also by row count and column names
  (CSV/TSV read with the csv module, bounded; an .xlsx's sheet dimension
  read from the zip without parsing its XML; a Stata .dta's rows and
  variable names from its header alone, `researchos.dta` -- never its
  labels, never its data, DESIGN.md 15.4). A file whose content is only
  in the cloud is never read. Nothing is pruned automatically. The store is
  not the index: deleting or rebuilding `graph.db` leaves it alone,
  because what it holds -- content a file no longer has -- cannot be
  derived again. Two time indexes (on `julianday(at)`, 18.4 item 6) let the
  desk ask when a node last changed without reading every version
  (`Store.node_windows`).
- **An attempt** (`attempt:<table>#<n>`): its verdict and result as they
  moved, recorded by every scan that read the attempt table (later phases
  add approvals, `source = "approval"`).
- **While an AI agent's run is going** (17.5, 18.1 item 3) the engine that
  started it tells the history (`watch_run`), and a scan in that engine
  credits to the run what the run changed: a version of a file that was not
  in the before-picture or ended with other bytes, and a person's save
  through Research OS is left to the person. Such a version is
  `source = "agent"`, `who` the run's, `note` "seen during run <id>"; the
  attempts the scan reads count when the attempt table is the run's. The
  run's own attribution after it ends (`unless_noted`) adds nothing for a
  content this already holds.
- **A variable** (`variable:<id>`): its card's versions and log (9.11).

`versions(root, node)` lists them newest first; `diff(root, node, a, b)`
compares two (default: the newest with the one before it): a unified diff
for text (capped, `truncated` says so), a sentence for tables (「行数 96 →
97，列不变」 / 「新增列 log_ret」), the verdict as it moved for an attempt
(「🕒 → ✅」).
"""

from __future__ import annotations

import csv
import difflib
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from sqlite3 import Connection
from typing import IO, Any, Callable, Iterable, Mapping, NamedTuple

from researchos import db, dta, paths, settings
from researchos.agents import labels as agent_labels
from researchos.records import notes as notes_mod
from researchos.records.identity import IdentityState, read_identity

logger = logging.getLogger(__name__)

HISTORY_DIRNAME = "history"
BLOBS_DIRNAME = "blobs"
VERSIONS_FILENAME = "versions.sqlite"
TEXT_LIMIT = 5 * 1024 * 1024
DIFF_LINE_LIMIT = 2000
DIFF_CHAR_LIMIT = 200_000
CSV_ROW_LIMIT = 20_000_000
XLSX_HEAD_BYTES = 64 * 1024
GIT_TIMEOUT_S = 60
SOURCES = ("scan", "approval", "publish", "git", "agent")
LOCAL_LABEL = "本机"
WORKTREE = "worktree"

_TABLE_DELIMITERS = {".csv": ",", ".tsv": "\t", ".tab": "\t"}
_SECTION_LIKE = ("section", "claim")

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS versions (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        node TEXT NOT NULL,
        at TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        size INTEGER,
        who TEXT,
        source TEXT NOT NULL,
        note TEXT,
        kind TEXT NOT NULL,
        stored INTEGER NOT NULL DEFAULT 0,
        summary TEXT NOT NULL DEFAULT '{}',
        stat TEXT,
        approved_by TEXT,
        snapshot INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_versions_node ON versions(node, seq)",
)

#: The time indexes (DESIGN.md 18.4 item 6), on the time as SQLite reads an `at` (UTC, so a changed UTC offset
#: does not reorder them): all versions by time, and one node's by time. A store that cannot be written (read
#: only, locked) goes without them and is read by a scan.
_TIME_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_versions_at ON versions(julianday(at))",
    "CREATE INDEX IF NOT EXISTS idx_versions_node_at ON versions(node, julianday(at))",
)
#: How far from a bound a window reaches, in days. SQLite reads a time with no offset as UTC and Python as local
#: time (up to 14 hours apart): the window is wide enough for what Python would pick from it to be what it would
#: pick from everything.
TIME_MARGIN_DAYS = 2.0
_JULIAN_1970 = 2440587.5  # julianday('1970-01-01')
_JULIAN_2200 = _JULIAN_1970 + (date(2200, 1, 1) - date(1970, 1, 1)).days
_NODE_CHUNK = 400  # names in one IN (...)


class NodeWindow(NamedTuple):
    """What decides when one node last changed (`Store.node_windows`)."""

    count: int  # versions the node has
    first_seq: int
    first_at: str
    first_source: str
    last_kind: str  # the kind of its newest version by seq
    newest: float  # the latest `julianday(at)` among them
    rows: list[tuple[int, str]]  # (seq, at) of those within TIME_MARGIN_DAYS of `newest`


class HistoryError(Exception):
    """A history could not be read as asked. `code`: `no_identity`,
    `not_a_node`, `no_such_version`, `git`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# -- an agent's run going on (18.1 item 3) -----------------------------------------------

SEEN_NOTE = "seen during run "


@dataclass(frozen=True)
class RunSeen:
    """A run of an AI agent going on in a project: the history's `who` for it (`labels.who_value`), its id,
    and `owns(rel, entry)`, whether the content now at `rel` (`entry` as `agents.snapshot` records a file: `size`,
    `mtime_ns`, `sha256`) is the run's and not the person's -- the run's manager answers from its before-picture
    and from what the engine wrote for the person since."""

    who: str
    run: str
    owns: Callable[[str, Mapping[str, Any]], bool]

    @property
    def note(self) -> str:
        return f"{SEEN_NOTE}{self.run}"


_WATCHED: dict[str, RunSeen] = {}
_WATCHED_LOCK = threading.Lock()


def watch_run(root: str | Path, seen: RunSeen) -> None:
    """From now until `release_run`, the scans in this process credit what a run changed in `root` to it. One run
    per project (the manager allows no more); a second replaces the first."""
    with _WATCHED_LOCK:
        _WATCHED[os.path.realpath(root)] = seen


def release_run(root: str | Path, seen: RunSeen) -> None:
    """The run is over for the history. A different run registered since is left alone."""
    key = os.path.realpath(root)
    with _WATCHED_LOCK:
        if _WATCHED.get(key) is seen:
            del _WATCHED[key]


def run_seen(root: str | Path) -> RunSeen | None:
    """The run going on in `root`, if this process started one."""
    with _WATCHED_LOCK:
        return _WATCHED.get(os.path.realpath(root))


def seen_run_id(note: object) -> str | None:
    """The run id in a note `watch_run`'s attribution wrote ("seen during run <id>"), else None."""
    if isinstance(note, str) and note.startswith(SEEN_NOTE):
        return note[len(SEEN_NOTE):] or None
    return None


# -- a confirmed assessment (DESIGN.md 21.2) ---------------------------------------------

_CONFIRMED_NOTE = re.compile(r"由 (?P<who>.+?) 确认(?:（采纳 (?P<agent>.+) 的建议）)?\Z", re.S)


def confirmed_note(who: str, agent: str | None = None) -> str:
    """The note of the version a person's confirmation of an attempt's assessment writes from a handback: 「由 张三
    确认（采纳 Claude Code · sonnet 的建议）」 when they took the agent's suggestion unchanged, 「由 张三 确认」 when they put
    another status or reason in its place. Stored in Chinese, as the file's own words are (13.1); `name_people` says it
    in the language of the request."""
    return f"由 {who} 确认（采纳 {agent} 的建议）" if agent else f"由 {who} 确认"


#: The note of the version 撤销 writes when it takes a confirmation back (21.2): stored in Chinese like the confirmation's own.
UNCONFIRMED_NOTE = "撤销了确认"


def confirmed_note_words(note: object, lang: str) -> str | None:
    """`note` said in `lang` when it is a confirmation's (`confirmed_note`) or the note of taking one back
    (`UNCONFIRMED_NOTE`), else None."""
    if note == UNCONFIRMED_NOTE:
        return settings.say(UNCONFIRMED_NOTE, "Withdrew the confirmation", lang)
    got = _CONFIRMED_NOTE.fullmatch(note) if isinstance(note, str) else None
    if got is None:
        return None
    from researchos import people  # noqa: PLC0415

    # a researcher with no `me.toml` is the stored 「最初的研究者」: said in the request's language, as every page names them (12.12, 13.4)
    who = first_researcher_label(lang) if got["who"] == people.FIRST_RESEARCHER_LABEL else got["who"]
    if got["agent"]:
        return settings.say("由 {who} 确认（采纳 {agent} 的建议）", "Confirmed by {who} (adopted the suggestion of {agent})",
                            lang, who=who, agent=got["agent"])
    return settings.say("由 {who} 确认", "Confirmed by {who}", lang, who=who)


# -- the version store ----------------------------------------------------------------


def history_dir(project_id: str) -> Path:
    return paths.index_dir(project_id) / HISTORY_DIRNAME


@dataclass(frozen=True)
class Version:
    seq: int
    node: str
    at: str
    sha256: str
    size: int | None
    who: str | None
    source: str
    note: str | None
    kind: str
    stored: bool
    summary: dict[str, Any]
    stat: str | None
    approved_by: str | None = None
    snapshot: int | None = None


class Store:
    """The version store of one project (module docstring). Writers hold
    the project lock; SQLite keeps a reader safe beside them."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        (directory / BLOBS_DIRNAME).mkdir(exist_ok=True)
        self.conn = sqlite3.connect(directory / VERSIONS_FILENAME)
        self.conn.row_factory = sqlite3.Row
        for statement in _SCHEMA:
            self.conn.execute(statement)
        # V8 phase 3 (12.3): who approved a version that came through a
        # proposal. A store written before that column existed gains it.
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(versions)")}
        if "approved_by" not in columns:
            self.conn.execute("ALTER TABLE versions ADD COLUMN approved_by TEXT")
        # 12.13: the published snapshot a version installed by 同步 came from.
        if "snapshot" not in columns:
            self.conn.execute("ALTER TABLE versions ADD COLUMN snapshot INTEGER")
        self.conn.commit()
        self._index_times()

    def _index_times(self) -> None:
        """18.4 item 6: the time indexes, built once for a store that lacks them. A store that cannot be written
        now (read only, a writer holding it) is left as it is and read by a scan (`time_readable`)."""
        self.conn.execute("PRAGMA busy_timeout = 250")  # a request does not wait for a writer to build an index
        try:
            for statement in _TIME_INDEXES:
                self.conn.execute(statement)
            self.conn.commit()
        except sqlite3.OperationalError:
            logger.info("the time indexes of %s were not built", self.directory, exc_info=True)
            self.conn.rollback()
        finally:
            self.conn.execute("PRAGMA busy_timeout = 5000")

    @classmethod
    def open(cls, project_id: str) -> "Store":
        return cls(history_dir(project_id))

    @classmethod
    def existing(cls, project_id: str) -> "Store | None":
        directory = history_dir(project_id)
        return cls(directory) if (directory / VERSIONS_FILENAME).exists() else None

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> Version:
        return Version(
            seq=row["seq"], node=row["node"], at=row["at"], sha256=row["sha256"], size=row["size"], who=row["who"],
            source=row["source"], note=row["note"], kind=row["kind"], stored=bool(row["stored"]),
            summary=json.loads(row["summary"] or "{}"), stat=row["stat"], approved_by=row["approved_by"],
            snapshot=row["snapshot"],
        )

    def latest(self, node: str) -> Version | None:
        row = self.conn.execute("SELECT * FROM versions WHERE node = ? ORDER BY seq DESC LIMIT 1", (node,)).fetchone()
        return None if row is None else self._row(row)

    def counts(self) -> tuple[int, int]:
        """(versions kept, nodes they belong to) -- what `researchos status`
        says of the store."""
        row = self.conn.execute("SELECT COUNT(*), COUNT(DISTINCT node) FROM versions").fetchone()
        return int(row[0]), int(row[1])

    def versions(self, node: str) -> list[Version]:
        rows = self.conn.execute("SELECT * FROM versions WHERE node = ? ORDER BY seq DESC", (node,)).fetchall()
        return [self._row(r) for r in rows]

    # -- by time (DESIGN.md 18.4 item 6) -----------------------------------------------------------------
    # The desk asks when each node last changed. These read the indexes of `_TIME_INDEXES` and hand back only
    # the versions near what is asked; the judging stays with the caller, who reads each `at` as Python does.

    def time_readable(self) -> bool:
        """Whether the time indexes can answer: both exist, SQLite reads every `at` as a time, and none is
        before 1970 or after 2199 (so none is a number or a word it took for a time). If not, read every version."""
        names = {row[0] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name IN ('idx_versions_at', 'idx_versions_node_at')")}
        if len(names) < 2:
            return False
        if self.conn.execute("SELECT 1 FROM versions INDEXED BY idx_versions_at WHERE julianday(at) IS NULL LIMIT 1").fetchone():
            return False
        lowest = self.conn.execute("SELECT MIN(julianday(at)) FROM versions").fetchone()[0]
        highest = self.conn.execute("SELECT MAX(julianday(at)) FROM versions").fetchone()[0]
        return lowest is None or _JULIAN_1970 <= lowest and highest <= _JULIAN_2200

    def node_windows(self, nodes: Iterable[str] | None = None, margin: float | None = None) -> dict[str, NodeWindow]:
        """For each of `nodes` (every node when None) that has a version: its count, its first version's time and
        source, its newest version's kind, and the versions within `margin` days of its newest. Needs
        `time_readable`."""
        margin = TIME_MARGIN_DAYS if margin is None else margin
        wanted = None if nodes is None else sorted(set(nodes))
        found: dict[str, list[Any]] = {}
        for start in range(0, 1 if wanted is None else len(wanted), _NODE_CHUNK):
            chunk = () if wanted is None else wanted[start:start + _NODE_CHUNK]
            where = f"WHERE node IN ({', '.join('?' * len(chunk))})" if chunk else ""
            for node, count, first, last, newest, seq, at in self.conn.execute(
                    "SELECT t.node, t.n, t.a, t.b, t.m, v.seq, v.at FROM (SELECT node, COUNT(*) AS n, MIN(seq) AS a, "
                    "MAX(seq) AS b, MAX(julianday(at)) AS m FROM versions INDEXED BY idx_versions_node_at "
                    f"{where} GROUP BY node) AS t CROSS JOIN versions AS v INDEXED BY idx_versions_node_at "
                    "ON v.node = t.node AND julianday(v.at) >= t.m - ?", (*chunk, margin)):
                found.setdefault(node, [count, first, last, newest, []])[4].append((seq, at))
        ends: dict[int, tuple[str, str, str]] = {}
        seqs = sorted({seq for got in found.values() for seq in got[1:3]})
        for start in range(0, len(seqs), _NODE_CHUNK):
            chunk = seqs[start:start + _NODE_CHUNK]
            for seq, at, source, kind in self.conn.execute(
                    f"SELECT seq, at, source, kind FROM versions WHERE seq IN ({', '.join('?' * len(chunk))})", chunk):
                ends[seq] = (at, source, kind)
        return {node: NodeWindow(count, first, ends[first][0], ends[first][1], ends[last][2], newest, rows)
                for node, (count, first, last, newest, rows) in found.items()}

    def earliest_times(self, margin: float | None = None) -> dict[str, list[str]]:
        """The `at` of the versions within `margin` days of the earliest one, for the attempts' rows
        (`"attempt"`) and for everything else (`"file"`); a kind with no version is left out. Needs `time_readable`."""
        margin = TIME_MARGIN_DAYS if margin is None else margin
        out: dict[str, list[str]] = {}
        for group, same in (("attempt", "="), ("file", "<>")):
            earliest = self.conn.execute(
                f"SELECT MIN(julianday(at)) FROM versions INDEXED BY idx_versions_at WHERE kind {same} 'attempt'").fetchone()[0]
            if earliest is not None:
                out[group] = [row[0] for row in self.conn.execute(
                    f"SELECT at FROM versions INDEXED BY idx_versions_at WHERE kind {same} 'attempt' AND julianday(at) <= ?",
                    (earliest + margin,))]
        return out

    def stamps(self, node: str) -> list[tuple[int, str]]:
        """(seq, at) of every version of `node`, oldest first."""
        rows = self.conn.execute("SELECT seq, at FROM versions WHERE node = ? ORDER BY seq", (node,))
        return [(row[0], row[1]) for row in rows]

    def add(self, node: str, *, sha256: str, size: int | None, kind: str, source: str = "scan", who: str | None = None,
            note: str | None = None, stored: bool = False, summary: dict[str, Any] | None = None,
            stat: str | None = None, at: str | None = None, approved_by: str | None = None,
            snapshot: int | None = None) -> Version:
        if source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}, got {source!r}")
        cur = self.conn.execute(
            "INSERT INTO versions (node, at, sha256, size, who, source, note, kind, stored, summary, stat, approved_by, "
            "snapshot) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (node, at or _now(), sha256, size, who, source, note, kind, int(stored),
             json.dumps(summary or {}, ensure_ascii=False, sort_keys=True), stat, approved_by, snapshot),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM versions WHERE seq = ?", (cur.lastrowid,)).fetchone()
        return self._row(row)

    def attribute(self, seq: int, *, source: str, who: str | None, note: str | None,
                  approved_by: str | None) -> None:
        """Say who a version came from, after the fact: the attempt row an
        approval wrote is first seen by the re-ingest that write runs (as a
        scan, on this machine); the approval then names the member and the
        PI on that same version instead of adding a second one."""
        if source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}, got {source!r}")
        self.conn.execute("UPDATE versions SET source = ?, who = ?, note = ?, approved_by = ? WHERE seq = ?",
                          (source, who, note, approved_by, seq))
        self.conn.commit()

    def note_stat(self, seq: int, stat: str) -> None:
        """The same content seen under a new stat (touched, copied back):
        remember the stat so the next scan does not read it again."""
        self.conn.execute("UPDATE versions SET stat = ? WHERE seq = ?", (stat, seq))
        self.conn.commit()

    def blob_path(self, sha: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
            raise HistoryError("no_such_version", f"not a content hash: {sha!r}")
        return self.directory / BLOBS_DIRNAME / sha

    def put_blob(self, data: bytes) -> str:
        """Keep `data` by content: written once (temp, fsync, rename), read
        back and checked; a blob already there is never rewritten."""
        sha = hashlib.sha256(data).hexdigest()
        target = self.blob_path(sha)
        if target.exists():
            return sha
        from researchos.records import files as record_files  # noqa: PLC0415

        tmp = record_files.temp_path_for(target)
        try:
            record_files.write_new_file(tmp, data)
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink()
        if hashlib.sha256(target.read_bytes()).hexdigest() != sha:  # pragma: no cover -- disk guard
            target.unlink()
            raise HistoryError("no_such_version", f"blob {sha} does not read back as written")
        return sha

    def read_blob(self, sha: str) -> bytes | None:
        try:
            return self.blob_path(sha).read_bytes()
        except FileNotFoundError:
            return None


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_dataless(path: Path) -> bool:
    """Seam for tests: a file whose content is only in the cloud is never
    read (8.10, 11.1)."""
    return paths.is_dataless(path)


def _is_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


# -- table summaries ----------------------------------------------------------------


def table_summary_stream(stream: IO[bytes], name: str) -> dict[str, Any] | None:
    """Row count and column names of a CSV/TSV read from `stream` (bounded:
    at most `CSV_ROW_LIMIT` rows are counted, `rows_complete` says whether
    all were). None for a file that is not a table by its extension."""
    suffix = Path(name).suffix.lower()
    delimiter = _TABLE_DELIMITERS.get(suffix)
    if delimiter is None:
        return None
    text = io.TextIOWrapper(stream, encoding="utf-8", errors="replace", newline="")
    reader = csv.reader(text, delimiter=delimiter)
    try:
        header = next(reader, None)
        if header is None:
            return {"format": suffix[1:], "columns": [], "rows": 0, "rows_complete": True}
        if header and header[0].startswith("﻿"):
            header[0] = header[0][1:]
        rows = 0
        complete = True
        for _row in reader:
            rows += 1
            if rows >= CSV_ROW_LIMIT:
                complete = next(reader, None) is None
                break
        return {"format": suffix[1:], "columns": header, "rows": rows, "rows_complete": complete}
    except (csv.Error, ValueError) as exc:
        return {"format": suffix[1:], "columns": None, "rows": None, "rows_complete": False, "error": str(exc)}
    finally:
        text.detach()


_DIMENSION_RE = re.compile(rb'<(?:\w+:)?dimension\s+ref="([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?"')


def _column_number(letters: bytes) -> int:
    n = 0
    for ch in letters.decode("ascii"):
        n = n * 26 + (ord(ch) - 64)
    return n


def xlsx_summary(path: Path) -> dict[str, Any] | None:
    """The first sheet's row and column count from its `<dimension>`
    element, read from the first 64 KB of the sheet inside the zip -- no
    XML parser, no whole-member decompression. None when that cannot be
    told safely (then size and fingerprint are all the history keeps)."""
    try:
        with zipfile.ZipFile(path) as archive:
            sheets = sorted(
                (n for n in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)),
            )
            if not sheets:
                return None
            with archive.open(sheets[0]) as member:
                head = member.read(XLSX_HEAD_BYTES)
    except (zipfile.BadZipFile, OSError, KeyError, ValueError, RuntimeError, NotImplementedError):
        return None
    match = _DIMENSION_RE.search(head)
    if match is None:
        return None
    first_col, first_row, last_col, last_row = match.groups()
    last_col, last_row = last_col or first_col, last_row or first_row
    out: dict[str, Any] = {
        "format": "xlsx",
        "sheet": sheets[0].rsplit("/", 1)[1],
        "sheet_rows": int(last_row) - int(first_row) + 1,
        "sheet_columns": _column_number(last_col) - _column_number(first_col) + 1,
    }
    columns = _xlsx_header(path, head)
    if columns is not None:
        # 12.6: like a CSV -- the first row names the columns, the rows
        # under it are counted.
        out.update(columns=columns, rows=max(out["sheet_rows"] - 1, 0), rows_complete=True)
    return out


_ROW_RE = re.compile(rb"<(?:\w+:)?row\b[^>]*?(?:/>|>(.*?)</(?:\w+:)?row>)", re.S)
_CELL_RE = re.compile(rb"<(?:\w+:)?c\b([^>]*?)(?:/>|>(.*?)</(?:\w+:)?c>)", re.S)
_ATTR_RE = re.compile(rb'\b(r|t)="([^"]*)"')
_VALUE_RE = re.compile(rb"<(?:\w+:)?v>(.*?)</(?:\w+:)?v>", re.S)
_TEXT_RE = re.compile(rb"<(?:\w+:)?t\b[^>]*>(.*?)</(?:\w+:)?t>", re.S)
XLSX_MAX_SHARED = 100_000


def _xml_text(raw: bytes) -> str:
    import html  # noqa: PLC0415

    return html.unescape(raw.decode("utf-8", errors="replace"))


def _xlsx_header(path: Path, head: bytes) -> list[str] | None:
    """The first sheet's first row as column names (shared strings and
    inline strings resolved), or None when it cannot be told from the
    sheet's first 64 KB -- then rows and columns are compared by count only."""
    m = _ROW_RE.search(head)
    if m is None or m.group(1) is None:
        return None
    cells: list[tuple[int, str, bytes]] = []
    for cell in _CELL_RE.finditer(m.group(1)):
        attrs = dict(_ATTR_RE.findall(cell.group(1)))
        ref = re.match(rb"([A-Z]+)", attrs.get(b"r", b""))
        column = _column_number(ref.group(1)) if ref else len(cells) + 1
        cells.append((column, attrs.get(b"t", b"").decode("ascii", "replace"), cell.group(2) or b""))
    shared_needed = set()
    for _col, kind, body in cells:
        if kind == "s":
            v = _VALUE_RE.search(body)
            if v is None or not v.group(1).strip().isdigit():
                return None
            shared_needed.add(int(v.group(1)))
    shared: dict[int, str] = {}
    if shared_needed:
        if max(shared_needed) >= XLSX_MAX_SHARED:
            return None
        try:
            from xml.etree import ElementTree  # noqa: PLC0415

            with zipfile.ZipFile(path) as archive, archive.open("xl/sharedStrings.xml") as member:
                index = 0
                for _event, element in ElementTree.iterparse(member, events=("end",)):
                    if element.tag.rsplit("}", 1)[-1] != "si":
                        continue
                    if index in shared_needed:
                        shared[index] = "".join(t.text or "" for t in element.iter() if t.tag.rsplit("}", 1)[-1] == "t")
                    element.clear()
                    index += 1
                    if index > max(shared_needed):
                        break
        except (KeyError, zipfile.BadZipFile, OSError, ElementTree.ParseError, RuntimeError, NotImplementedError):
            return None
    width = max((c for c, _k, _b in cells), default=0)
    names = [""] * width
    for column, kind, body in cells:
        if kind == "s":
            text = shared.get(int(_VALUE_RE.search(body).group(1)))
            if text is None:
                return None
        elif kind == "inlineStr":
            text = "".join(_xml_text(t) for t in _TEXT_RE.findall(body))
        else:
            v = _VALUE_RE.search(body)
            text = _xml_text(v.group(1)) if v else ""
        names[column - 1] = text
    return names


def data_summary(path: Path, rel: str) -> dict[str, Any] | None:
    suffix = Path(rel).suffix.lower()
    if suffix in _TABLE_DELIMITERS:
        with open(path, "rb") as stream:
            return table_summary_stream(stream, rel)
    if suffix == ".xlsx":
        return xlsx_summary(path)
    if suffix == ".dta":
        try:
            return dta.read_header(path).summary()
        except dta.DtaError as exc:
            return _dta_refused(exc)
    return None


def _dta_refused(exc: dta.DtaError) -> dict[str, Any]:
    """A .dta file whose header the reader refused (15.4): its rows and
    variables not compared, the refusal's code kept -- a corrupt file never
    stops a scan, a page or a proposal, which catch only OSError."""
    return {"format": "dta", "columns": None, "rows": None, "rows_complete": False, "error": exc.code}


def _hash_file(path: Path) -> tuple[str, bytes | None, int]:
    """(sha256, the bytes when the file is at most `TEXT_LIMIT`, size)."""
    digest = hashlib.sha256()
    kept = bytearray()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            if size <= TEXT_LIMIT:
                kept.extend(chunk)
    return digest.hexdigest(), (bytes(kept) if size <= TEXT_LIMIT else None), size


# -- recording ---------------------------------------------------------------------------


def record_file(store: Store, root: Path, rel: str, *, source: str = "scan", who: str | None = None,
                note: str | None = None, approved_by: str | None = None, snapshot: int | None = None,
                seen: RunSeen | None = None, unless_noted: str | None = None) -> str:
    """Record `rel`'s current content as a version if it differs from the
    newest one. Returns "recorded", "unchanged", "missing", "dataless" or
    "unreadable". A file whose size, modification time and inode are what
    the newest version saw is not read again. A version that came through
    an approved proposal (12.3) carries the member as `who` and the PI as
    `approved_by`; it is recorded even when the content is unchanged, so
    the approval itself is part of the file's history. A version 同步
    installed (`source = "publish"`) carries the PI as `who` and the
    snapshot it came from (12.13), and is recorded the same way; so is a
    version an AI agent's run made (`source = "agent"`, `who =
    agent:<runner>:<model>#<person>`, `note` the run id, 17.5).

    `seen` is the run going on in the project (`run_seen`): a scan's version
    (`source = "scan"`) of a content the run owns is recorded as the run's
    ("seen during run <id>"), and is skipped, as a scan's is, when the content
    is the newest version's. `unless_noted` is the note of a version that
    already stands for this very record: when the newest version has this
    content, this source and `who` and that note (or this one), nothing is
    added (the run's attribution after it ended, for what a scan saw while it
    went on)."""
    full = root / rel
    try:
        st = full.lstat()
    except OSError:
        return "missing"
    if not full.is_file() or full.is_symlink():
        return "missing"
    if _is_dataless(full):
        return "dataless"
    node = notes_mod.normalize_node(rel)
    latest = store.latest(node)
    stat = f"{st.st_size}:{st.st_mtime_ns}:{st.st_ino}"
    if latest is not None and latest.stat == stat and source == "scan":
        return "unchanged"
    try:
        sha, data, size = _hash_file(full)
    except OSError:
        return "unreadable"
    if latest is not None and latest.sha256 == sha and source == "scan":
        store.note_stat(latest.seq, stat)
        return "unchanged"
    if (unless_noted is not None and latest is not None and latest.sha256 == sha and latest.source == source
            and latest.who == who and latest.note in (unless_noted, note)):
        store.note_stat(latest.seq, stat)
        return "unchanged"
    if seen is not None and source == "scan":
        entry = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha}
        if seen.owns(rel, entry):
            source, who, note = "agent", seen.who, seen.note
    text = data is not None and _is_text(data)
    try:
        summary = data_summary(full, rel)
    except OSError:
        summary = None
    kind = "data" if summary is not None else ("text" if text else "binary")
    if text:
        store.put_blob(data)  # type: ignore[arg-type]
    store.add(node, sha256=sha, size=size, kind=kind, source=source, who=who, note=note, stored=text,
              summary=summary or {}, stat=stat, approved_by=approved_by, snapshot=snapshot)
    return "recorded"


def _attempt_digest(fields: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def record_attempt_rows(store: Store, rows: Iterable[tuple[str, Any, Any]], *, source: str = "scan",
                        who: str | None = None, note: str | None = None, approved_by: str | None = None,
                        snapshot: int | None = None) -> int:
    """Each (attempt node id, verdict, result), recorded when it differs
    from the newest version of that attempt."""
    recorded = 0
    for node_id, verdict, result in rows:
        fields = {"verdict": str(verdict or ""), "result": str(result or "")}
        digest = _attempt_digest(fields)
        latest = store.latest(node_id)
        if latest is not None and latest.sha256 == digest:
            continue
        store.add(node_id, sha256=digest, size=None, kind="attempt", source=source, who=who, note=note,
                  summary=fields, approved_by=approved_by, snapshot=snapshot)
        recorded += 1
    return recorded


def record_attempts(store: Store, conn: Connection, *, source: str = "scan", who: str | None = None,
                    note: str | None = None, approved_by: str | None = None,
                    only: Iterable[str] | None = None) -> int:
    """Each attempt's verdict and result, recorded when it differs from the
    newest version (an attempt no longer in the table keeps its history).
    `only` limits it to those attempt node ids (an approval records the
    one attempt it changed, with who proposed and who approved)."""
    wanted = set(only) if only is not None else None
    rows = []
    for node in db.get_nodes_by_type(conn, "attempt"):
        if wanted is not None and node["id"] not in wanted:
            continue
        hf = node.get("human_fields") or {}
        rows.append((node["id"], hf.get("verdict"), hf.get("result")))
    return record_attempt_rows(store, rows, source=source, who=who, note=note, approved_by=approved_by)


def scan_who(root: Path) -> str | None:
    """Who a version this machine's scan sees is credited to (12.13): the
    researcher named in me.toml -- except in a member's working copy, where
    a version with no other attribution is 「本机」, and on a machine
    without me.toml (nobody to name; read as 「最初的研究者」)."""
    from researchos import people, proposals  # noqa: PLC0415

    try:
        me = people.current_member_id()
    except people.PeopleError:
        return None
    if me is None:
        return None
    try:
        member_copy = proposals.joined_not_pi(root, me)
    except Exception:  # noqa: BLE001 -- not a working copy as far as anything here knows
        member_copy = False
    return None if member_copy else me


def _is_git(root: Path) -> bool:
    return (root / ".git").exists()


def _tracked(root: Path) -> set[str] | None:
    """Every path git tracks in `root` (None when it is not a repository)."""
    from researchos.ingest import git as git_ingest  # noqa: PLC0415

    try:
        return {rel for rel, _category in git_ingest.iter_tracked(root)}
    except git_ingest.GitIngestError:
        return None


def _record_paths(root: Path) -> list[str]:
    """The record files whose versions are kept (12.6: the attempt table
    and the record files), project-relative."""
    from researchos import inventory  # noqa: PLC0415 -- inventory imports the records

    found = []
    for path in [*inventory.record_file_paths(root), notes_mod.notes_path(root), paths.project_dir(root) / "team.toml"]:
        try:
            found.append(Path(path).relative_to(root).as_posix())
        except ValueError:
            continue
    return list(dict.fromkeys(found))


def record_scan(conn: Connection | None, project_root: str | Path, *, files: Iterable[str] | None = None,
                seen: RunSeen | None = None) -> dict[str, int]:
    """What a scan adds to the history (module docstring): every file of the
    inventory git does not track (all of them, in a project that is not a
    git repository) and the record files, then the attempts. `files`
    limits the file half to those paths (the watcher's changed files).
    While an agent's run is going in the project (`seen`, by default the one
    `run_seen` knows) what the run changed is recorded as the run's (18.1
    item 3): the files it owns, and the attempts when the attempt table is
    one of them. Only for a project with an identity whose migration has
    finished. The caller holds the project lock; a failure is the caller's to
    contain."""
    root = Path(project_root)
    got = read_identity(root)
    if got.state is not IdentityState.PRESENT or got.identity is None or got.identity.migrating_from is not None:
        return {}
    counts = {"recorded": 0, "unchanged": 0, "dataless": 0, "missing": 0, "unreadable": 0, "attempts": 0}
    tracked = _tracked(root) if _is_git(root) else None
    if files is None:
        if tracked is None:
            from researchos.ingest import files as files_ingest  # noqa: PLC0415

            candidates = [rel for rel, category in files_ingest.iter_files(root) if category is not None]
        else:
            candidates = []
        candidates += _record_paths(root)
    else:
        candidates = list(files)
    who = scan_who(root)
    seen = seen if seen is not None else run_seen(root)
    with Store.open(got.identity.id) as store:
        for rel in dict.fromkeys(candidates):
            if tracked is not None and rel in tracked:
                continue
            outcome = record_file(store, root, rel, who=who, seen=seen)
            counts[outcome] = counts.get(outcome, 0) + 1
        if conn is not None:
            author = {"source": "agent", "who": seen.who, "note": seen.note} if _owns_table(root, seen) else {}
            counts["attempts"] = record_attempts(store, conn, **({"who": who} | author))
    return counts


def _owns_table(root: Path, seen: RunSeen | None) -> bool:
    """Whether the attempt table is a file the run owns (`RunSeen.owns`): the verdicts a scan reads from it
    during the run are then the run's."""
    if seen is None:
        return False
    from researchos.agents import snapshot  # noqa: PLC0415 -- the picture's own reading of attempts.toml

    rel = snapshot.attempts_table(root)
    entry = snapshot.fingerprint(root, rel) if rel else None
    return entry is not None and seen.owns(rel, entry)


def record_after_scan(conn: Connection | None, project_root: str | Path, *, files: Iterable[str] | None = None,
                      seen: RunSeen | None = None) -> dict[str, int]:
    """`record_scan`, contained: a history that could not be written never
    fails the scan it follows."""
    try:
        return record_scan(conn, project_root, files=files, seen=seen)
    except Exception:  # noqa: BLE001 -- see docstring
        logger.exception("recording the history of %s failed", project_root)
        return {}


# -- git -----------------------------------------------------------------------------

_GIT_HARDENING = (
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "log.showSignature=false",
    "-c", "diff.external=",
    "-c", "core.pager=cat",
    "-c", "gc.auto=0",
    "-c", "maintenance.auto=false",
)


def _git_env() -> dict[str, str]:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"}
    for name in ("GIT_EXTERNAL_DIFF", "GIT_ASKPASS", "SSH_ASKPASS"):
        env.pop(name, None)
    return env


def _git(root: Path, args: list[str], *, binary: bool = False, limit: int | None = None) -> bytes | str | None:
    """Run hardened git; stdout, or None when git refuses. `limit` caps the
    bytes read (a larger output comes back cut at `limit + 1` bytes, so the
    caller can tell)."""
    argv = ["git", *_GIT_HARDENING, "--no-pager", "-C", str(root), *args]
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                env=_git_env())
    except FileNotFoundError:
        return None
    try:
        assert proc.stdout is not None
        out = proc.stdout.read(limit + 1) if limit is not None else proc.stdout.read()
        if limit is not None and len(out) > limit:
            proc.kill()
            proc.wait(timeout=GIT_TIMEOUT_S)
            return out if binary else out.decode("utf-8", errors="surrogateescape")
        code = proc.wait(timeout=GIT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        return None
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
    if code != 0:
        return None
    return out if binary else out.decode("utf-8", errors="surrogateescape")


def _git_stream(root: Path, args: list[str]) -> subprocess.Popen | None:
    argv = ["git", *_GIT_HARDENING, "--no-pager", "-C", str(root), *args]
    try:
        return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                env=_git_env())
    except FileNotFoundError:
        return None


def is_tracked(root: Path, rel: str) -> bool:
    if not _is_git(root):
        return False
    out = _git(root, ["ls-files", "-z", "--", rel])
    return bool(out) and rel in str(out).split("\x00")


_LOG_FORMAT = "%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f"


def git_versions(root: Path, rel: str) -> list[dict[str, Any]]:
    """The commits of a tracked file, newest first, following renames;
    each with the path the file had in that commit."""
    out = _git(root, ["log", "--follow", "--no-show-signature", "--no-textconv", "--no-ext-diff", "-z",
                      "--name-only", f"--format={_LOG_FORMAT}", "--", rel])
    if out is None:
        raise HistoryError("git", f"git log could not read the history of {rel}")
    found = []
    for record in str(out).split("\x1e"):
        if not record.strip("\x00\n"):
            continue
        parts = record.split("\x1f", 5)
        if len(parts) != 6:
            continue
        sha, name, email, at, subject, rest = parts
        names = [p for p in rest.removeprefix("\x00").removeprefix("\n").split("\x00") if p.strip("\n")]
        path = names[0].strip("\n") if names else rel
        found.append({
            "version": sha, "commit": sha, "at": at, "who": email.lower() or None, "who_name": name or None,
            "source": "git", "note": subject, "path": path, "sha256": None, "size": None, "kind": "git",
            "summary": {},
        })
    _fill_sizes(root, found)
    return found


def _fill_sizes(root: Path, versions: list[dict[str, Any]]) -> None:
    """Each version's size and blob id, in one `cat-file --batch-check`."""
    if not versions:
        return
    argv = ["git", *_GIT_HARDENING, "--no-pager", "-C", str(root), "cat-file", "--batch-check"]
    request = "".join(f"{v['commit']}:{v['path']}\n" for v in versions)
    try:
        proc = subprocess.run(argv, input=request.encode("utf-8"), capture_output=True, env=_git_env(),
                              timeout=GIT_TIMEOUT_S)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    lines = proc.stdout.decode("utf-8", errors="replace").splitlines()
    for version, line in zip(versions, lines):
        fields = line.split()
        if len(fields) == 3 and fields[1] == "blob":
            version["blob"] = fields[0]
            version["size"] = int(fields[2])


def _worktree_version(root: Path, rel: str, head: dict[str, Any] | None) -> dict[str, Any] | None:
    """The working copy as one more version, when it differs from the newest
    commit's (hashed by git without filters; a cloud-only file is not read)."""
    full = root / rel
    if not full.is_file() or _is_dataless(full):
        return None
    out = _git(root, ["hash-object", "--no-filters", "--", rel])
    if out is None:
        return None
    if head is not None and head.get("blob") == str(out).strip():
        return None
    st = full.stat()
    return {
        "version": WORKTREE, "commit": None, "at": datetime.fromtimestamp(st.st_mtime, timezone.utc).astimezone().isoformat(timespec="seconds"),
        "who": None, "who_name": None, "source": "git", "note": "工作区里还没有提交的改动", "path": rel,
        "sha256": None, "size": st.st_size, "kind": "git", "summary": {}, "blob": str(out).strip(),
    }


def _git_blob(root: Path, version: dict[str, Any], limit: int = TEXT_LIMIT) -> bytes | None:
    """At most `limit + 1` bytes of a version's content (more means "large")."""
    if version["version"] == WORKTREE:
        full = root / version["path"]
        if _is_dataless(full):
            return None
        with open(full, "rb") as handle:
            return handle.read(limit + 1)
    out = _git(root, ["cat-file", "blob", f"{version['commit']}:{version['path']}"], binary=True, limit=limit)
    return out if isinstance(out, bytes) else None


def _git_table_summary(root: Path, version: dict[str, Any]) -> dict[str, Any] | None:
    rel = version["path"]
    suffix = Path(rel).suffix.lower()
    if version["version"] == WORKTREE:
        full = root / rel
        return None if _is_dataless(full) else data_summary(full, rel)
    if suffix in _TABLE_DELIMITERS:
        proc = _git_stream(root, ["cat-file", "blob", f"{version['commit']}:{rel}"])
        if proc is None or proc.stdout is None:
            return None
        try:
            return table_summary_stream(proc.stdout, rel)
        finally:
            proc.stdout.close()
            proc.wait(timeout=GIT_TIMEOUT_S)
    if suffix == ".dta":  # the header only, streamed: the data is never read (15.4)
        size = version.get("size")
        if size is None:
            out = _git(root, ["cat-file", "-s", f"{version['commit']}:{rel}"])
            size = int(str(out).strip()) if out is not None and str(out).strip().isdigit() else None
        if size is None:
            return None
        proc = _git_stream(root, ["cat-file", "blob", f"{version['commit']}:{rel}"])
        if proc is None or proc.stdout is None:
            return None
        try:
            return dta.read_header_stream(proc.stdout, int(size)).summary()
        except dta.DtaError as exc:
            return _dta_refused(exc)
        finally:
            proc.stdout.close()  # the rest is not wanted: git stops on the closed pipe
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=GIT_TIMEOUT_S)
    if suffix == ".xlsx":
        data = _git_blob(root, version, limit=200 * 1024 * 1024)
        if data is None:
            return None
        import tempfile  # noqa: PLC0415

        with tempfile.NamedTemporaryFile(suffix=".xlsx") as handle:
            handle.write(data)
            handle.flush()
            return xlsx_summary(Path(handle.name))
    return None


# -- reading -------------------------------------------------------------------------


def _identity_id(root: Path) -> str:
    got = read_identity(root)
    if got.state is not IdentityState.PRESENT or got.identity is None:
        raise HistoryError("no_identity", f"{root} has no readable project identity")
    return got.identity.id


def _file_of(node: str) -> str | None:
    """The project-relative file a node lives in: its path for a file node,
    the draft for a section or a claim."""
    path = notes_mod.node_path(node)
    if path is not None:
        return path
    prefix, _, rest = node.partition(":")
    if prefix in _SECTION_LIKE and "#" in rest:
        return rest.rsplit("#", 1)[0]
    return None


def _who_label(who: str | None, name: str | None = None) -> str:
    return name or who or LOCAL_LABEL


def local_label(lang: str = "zh") -> str:
    """「本机」 in the request's language (13.4: "This Mac")."""
    return settings.say(LOCAL_LABEL, "This Mac", lang)


def first_researcher_label(lang: str = "zh") -> str:
    """「最初的研究者」 (12.12) in the request's language (13.4: "Original
    researcher")."""
    from researchos import people  # noqa: PLC0415

    return settings.say(people.FIRST_RESEARCHER_LABEL, people.FIRST_RESEARCHER_LABEL_EN, lang)


def number_text(value: Any, lang: str = "zh") -> Any:
    """A number as the screen writes it (13.5): 1,497 in English; anything
    else, and every number in Chinese, as it is."""
    return f"{value:,}" if lang == "en" and isinstance(value, int) and not isinstance(value, bool) else value


def unknown_who_label(root: Path, lang: str = "zh") -> str:
    """What a version without `who` is said to be (12.12: a missing `by` is
    「最初的研究者」) -- except in a member's working copy, where a version
    this machine's scan saw is the member's own or the snapshot's, and
    「本机」 is the only thing known. In the request's language (13.6)."""
    from researchos import people, proposals  # noqa: PLC0415

    try:
        me = people.current_member_id()
    except people.PeopleError:
        me = None
    try:
        member_copy = proposals.joined_not_pi(root, me)
    except Exception:  # noqa: BLE001 -- not a working copy as far as anything here knows
        member_copy = False
    return local_label(lang) if member_copy else first_researcher_label(lang)


def _agent_who_label(agent: agent_labels.Agent, person: str | None, team: Any, lang: str) -> str:
    """The agent's label, with the person who assigned the run (17.5)."""
    if not person:
        return agent.label
    from researchos import people  # noqa: PLC0415

    name = people.attribution({"by": person}, team, lang)["label"]
    return settings.say("{label}（{name} 指派）", "{label} (assigned by {name})", lang, label=agent.label, name=name)


def name_people(root: Path, versions: list[dict[str, Any]], lang: str = "zh") -> list[dict[str, Any]]:
    """Every version's `who_label` and `approved_by_label` as the rest of
    the page says people (8.8, one term per concept): the member's name
    from the team file, not their id; a version without `who` as
    `unknown_who_label`, in `lang` (13.7: people words on every page). A
    version an AI agent made (17.5: `who` is `agent:<runner>:<model>#<person>`)
    reads 「Claude Code · sonnet（张三 指派）」 / "Claude Code · sonnet
    (assigned by Zhang San)"; without a person, just the agent."""
    from researchos import people  # noqa: PLC0415

    team = people.read_team(root)
    team = team if team.state == "present" else None
    unknown = None
    for v in versions:
        who, name = v.get("who"), v.get("who_name")
        agent = agent_labels.parse_who(who)
        if agent is not None:
            v["who_label"] = _agent_who_label(agent[0], agent[1], team, lang)
            run = seen_run_id(v.get("note")) if v.get("source") == "agent" else None
            if run is not None:  # 18.1 item 3: said in the request's language, never as the stored English
                v["note"] = settings.say("在运行 {run} 期间看到", "Seen during run {run}", lang, run=run)
        elif name:
            v["who_label"] = name
        elif isinstance(who, str) and who and "@" in who:
            v["who_label"] = people.attribution({"by": who}, team, lang)["label"]
        elif isinstance(who, str) and who:
            v["who_label"] = who
        else:
            unknown = unknown or unknown_who_label(root, lang)
            v["who_label"] = unknown
        said = confirmed_note_words(v.get("note"), lang)  # 21.2: a confirmed assessment's note, in the request's language
        if said is not None:
            v["note"] = said
        approved = v.get("approved_by")
        if isinstance(approved, str) and approved:
            v["approved_by_label"] = people.attribution({"by": approved}, team, lang)["label"]
    return versions


def _store_payload(v: Version) -> dict[str, Any]:
    return {
        "version": str(v.seq), "at": v.at, "sha256": v.sha256, "size": v.size, "who": v.who,
        "who_label": _who_label(v.who), "source": v.source, "note": v.note, "kind": v.kind,
        "summary": v.summary, "stored": v.stored, "approved_by": v.approved_by, "snapshot": v.snapshot,
    }


def versions(project_root: str | Path, node: str, lang: str = "zh") -> dict[str, Any]:
    """Every version of `node`, newest first (module docstring); people
    named in `lang` (13.7)."""
    root = Path(project_root)
    try:
        node_id = notes_mod.normalize_node(node)
    except ValueError as exc:
        raise HistoryError("not_a_node", str(exc)) from exc
    pid = _identity_id(root)
    prefix = node_id.partition(":")[0]
    if prefix == "variable":
        return _variable_versions(root, node_id, lang)
    if prefix == "attempt":
        store = Store.existing(pid)
        from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

        labels = attempts_ingest.verdict_labels(root)
        found = [] if store is None else [_attempt_payload(v, labels) for v in store.versions(node_id)]
        if store is not None:
            store.close()
        return {"node": node_id, "kind": "attempt", "origin": "store", "path": None,
                "versions": name_people(root, found, lang)}
    rel = _file_of(node_id)
    if rel is None:
        return {"node": node_id, "kind": "other", "origin": None, "path": None, "versions": [],
                "reason": "它不是文件，没有版本"}
    file_node = notes_mod.normalize_node(rel)
    if is_tracked(root, rel):
        found = git_versions(root, rel)
        worktree = _worktree_version(root, rel, found[0] if found else None)
        out = ([worktree] if worktree else []) + found
        _attribute_git(root, pid, file_node, out)
        return {"node": node_id, "file_node": file_node, "kind": "file", "origin": "git", "path": rel,
                "versions": name_people(root, out, lang)}
    store = Store.existing(pid)
    found = [] if store is None else [_store_payload(v) for v in store.versions(file_node)]
    if store is not None:
        store.close()
    return {"node": node_id, "file_node": file_node, "kind": "file", "origin": "store", "path": rel,
            "versions": name_people(root, found, lang)}


def _git_sha256(root: Path, version: dict[str, Any]) -> str | None:
    """The SHA-256 of one git version's content, streamed."""
    digest = hashlib.sha256()
    if version["version"] == WORKTREE:
        full = root / version["path"]
        if _is_dataless(full):
            return None
        try:
            with open(full, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None
        return digest.hexdigest()
    proc = _git_stream(root, ["cat-file", "blob", f"{version['commit']}:{version['path']}"])
    if proc is None or proc.stdout is None:
        return None
    try:
        for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
            digest.update(chunk)
    finally:
        proc.stdout.close()
        proc.wait(timeout=GIT_TIMEOUT_S)
    return digest.hexdigest() if proc.returncode == 0 else None


def _attribute_git(root: Path, project_id: str, file_node: str, found: list[dict[str, Any]]) -> None:
    """12.6 in a git project: a version that came through an approved
    proposal carries the member, the PI who approved it and the PI's note
    -- the history store's record of the approval, matched by content
    (size first, then SHA-256, so only a candidate is read). The commit's
    own author and subject stay as `committed_by` / `commit_note`."""
    store = Store.existing(project_id)
    if store is None:
        return
    try:
        approvals = [v for v in store.versions(file_node) if v.source == "approval" and v.sha256]
    finally:
        store.close()
    if not approvals:
        return
    sizes = {v.size for v in approvals}
    for version in found:
        if version.get("size") not in sizes:
            continue
        sha = _git_sha256(root, version)
        match = next((v for v in approvals if v.sha256 == sha), None)  # newest approval of that content
        if match is None:
            continue
        version.update(committed_by=version.get("who"), commit_note=version.get("note"), who=match.who, who_name=None,
                       approved_by=match.approved_by, note=match.note or version.get("note"), sha256=sha,
                       approval_at=match.at)


def _attempt_payload(v: Version, labels: dict[str, str] | None = None) -> dict[str, Any]:
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    out = _store_payload(v)
    out["verdict"] = v.summary.get("verdict", "")
    out["verdict_status"] = attempts_ingest.verdict_status(out["verdict"], labels)
    out["result"] = v.summary.get("result", "")
    return out


def _variable_versions(root: Path, node_id: str, lang: str = "zh") -> dict[str, Any]:
    from researchos.records import variables as variables_mod  # noqa: PLC0415

    card_id = node_id.partition(":")[2]
    try:
        card = variables_mod.open_card(root, card_id)
    except variables_mod.VariableError as exc:
        raise HistoryError("not_a_node", str(exc)) from exc
    entries = []
    for e in reversed(card.entries):
        data = variables_mod.jsonable(dict(e.data))
        entries.append({
            "version": e.id, "at": data.get("at"), "who": data.get("by"), "who_label": _who_label(data.get("by")),
            "source": "card", "note": data.get("note"), "act": data.get("act"), "number": data.get("version"),
            "kind": "variable", "summary": {"act": data.get("act"), "version": data.get("version")},
        })
    numbers = [
        {"number": n, "status": view.status, "file": view.path.name} for n, view in sorted(card.versions.items(), reverse=True)
    ]
    name_people(root, [e for e in entries if e.get("who")], lang)
    for item in entries:  # a card entry without `by` is the first researcher's (12.1), wherever it is read
        if not item.get("who"):
            item["who_label"] = first_researcher_label(lang)
    return {"node": node_id, "kind": "variable", "origin": "card", "path": None, "versions": entries,
            "card_versions": numbers, "in_use": card.in_use, "state": card.state}


# -- diff ----------------------------------------------------------------------------


def _pick(found: list[dict[str, Any]], a: str | None, b: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    by_id = {str(v["version"]): v for v in found}
    if a is None and b is None:
        if len(found) < 2:
            raise HistoryError("no_such_version", "only one version so far -- nothing to compare")
        return found[1], found[0]
    if a is None or b is None:
        raise HistoryError("no_such_version", "name both versions, or neither")
    va, vb = by_id.get(str(a)), by_id.get(str(b))
    if va is None or vb is None:
        missing = a if va is None else b
        raise HistoryError("no_such_version", f"no version {missing!r} of this node")
    return va, vb


def unified(old: str, new: str, label_a: str, label_b: str) -> tuple[str, bool]:
    """A unified diff, cut at `DIFF_LINE_LIMIT` lines / `DIFF_CHAR_LIMIT`
    characters; the flag says whether it was cut."""
    lines = []
    size = 0
    truncated = False
    for line in difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), label_a, label_b):
        if not line.endswith("\n"):
            line += "\n\\ No newline at end of file\n"
        if len(lines) >= DIFF_LINE_LIMIT or size + len(line) > DIFF_CHAR_LIMIT:
            truncated = True
            break
        lines.append(line)
        size += len(line)
    return "".join(lines), truncated


#: The quantities `_moved` names, in English (13.4; lower case: a part of a sentence).
_QUANTITY_EN = {"行数": "rows", "列数": "columns"}


def _moved(name: str, a: Any, b: Any, words: bool, unit: str = "", more: str = "", lang: str = "zh") -> str:
    """One quantity as it moved: 「行数 96 → 97」 for the command line, and
    -- `words`, for the page (12.13: words, not arrows) -- 「行数从 96 增至
    97」 / 「行数从 97 减至 96」, or 「大小从 a 变为 b 字节」 when the two do
    not compare as numbers. In English (the page only), "rows from 96 to
    97" (13.5's numbers)."""
    if not words and lang != "en":
        return f"{name} {a} → {b}{more}{(' ' + unit) if unit else ''}"
    verb = "变为"
    if not more and isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool):
        verb = "增至" if b > a else "减至" if b < a else "变为"
    return settings.say("{name}从 {a} {verb} {b}{more}{unit}", "{name} from {a} to {b}{more}{unit}", lang,
                        name=_QUANTITY_EN.get(name, name) if lang == "en" else name, verb=verb,
                        a=number_text(a, lang), b=number_text(b, lang),
                        more=settings.say(more, " or more", lang) if more else "", unit=(" " + unit) if unit else "")


def _sentence(parts: list[str], lang: str) -> str:
    """A table sentence's parts as one sentence: 「，」 in Chinese; in English
    "; " (a part may hold a list of columns) and a capital first letter."""
    if lang != "en":
        return "，".join(parts)
    text = "; ".join(parts)
    return text[:1].upper() + text[1:]


def table_sentence(a: dict[str, Any] | None, b: dict[str, Any] | None, *, words: bool = False,
                   lang: str = "zh") -> str | None:
    """「行数 96 → 97，列不变」, 「新增列 log_ret」 -- what changed in a table,
    in the app's words; None when neither side is a table. `words`: the
    screen's form, 「行数从 96 增至 97」 (12.13); the command line keeps its
    arrows. `lang`: the page's language (13.6), "Rows from 96 to 97;
    column added: log_ret" in English."""
    if not a and not b:
        return None
    a, b = a or {}, b or {}
    sep = settings.say("、", ", ", lang)
    # A Stata .dta's columns are its variables (15.4): said so.
    var = a.get("format") == "dta" or b.get("format") == "dta"
    if (a.get("format") == "xlsx" or b.get("format") == "xlsx") and not (a.get("columns") is not None
                                                                         and b.get("columns") is not None):
        ra, rb = a.get("sheet_rows"), b.get("sheet_rows")
        ca, cb = a.get("sheet_columns"), b.get("sheet_columns")
        if ra is None or rb is None:
            return settings.say("工作表的行列数未比对", "The sheet’s rows and columns were not compared", lang)
        parts = [_moved("行数", ra, rb, words, lang=lang) if ra != rb
                 else settings.say("行数不变（{n}）", "rows unchanged ({n})", lang, n=number_text(rb, lang))]
        parts.append(_moved("列数", ca, cb, words, lang=lang) if ca != cb
                     else settings.say("列数不变", "columns unchanged", lang))
        return _sentence(parts, lang)
    parts = []
    ra, rb = a.get("rows"), b.get("rows")
    if ra is None or rb is None:
        parts.append(settings.say("行数未比对", "rows not compared", lang))
    elif ra != rb or not (a.get("rows_complete", True) and b.get("rows_complete", True)):
        more = "" if b.get("rows_complete", True) else " 以上"
        parts.append(_moved("行数", ra, rb, words, more=more, lang=lang))
    rows_said = bool(parts)
    cols_a, cols_b = a.get("columns"), b.get("columns")
    if cols_a is None or cols_b is None:
        parts.append(settings.say("变量未比对", "variables not compared", lang) if var
                     else settings.say("列未比对", "columns not compared", lang))
    else:
        added = [c for c in cols_b if c not in cols_a]
        removed = [c for c in cols_a if c not in cols_b]
        if added and var:
            parts.append(settings.say("新增变量 {cols}", "variable added: {cols}" if len(added) == 1
                                      else "variables added: {cols}", lang, cols=sep.join(added)))
        elif added:
            parts.append(settings.say("新增列 {cols}", "column added: {cols}" if len(added) == 1
                                      else "columns added: {cols}", lang, cols=sep.join(added)))
        if removed and var:
            parts.append(settings.say("删除变量 {cols}", "variable removed: {cols}" if len(removed) == 1
                                      else "variables removed: {cols}", lang, cols=sep.join(removed)))
        elif removed:
            parts.append(settings.say("删除列 {cols}", "column removed: {cols}" if len(removed) == 1
                                      else "columns removed: {cols}", lang, cols=sep.join(removed)))
        if not added and not removed and var:
            parts.append(settings.say("变量不变", "variables unchanged", lang) if cols_a == cols_b
                         else settings.say("变量的顺序变了", "variable order changed", lang))
        elif not added and not removed:
            parts.append(settings.say("列不变", "columns unchanged", lang) if cols_a == cols_b
                         else settings.say("列的顺序变了", "column order changed", lang))
    if not rows_said and cols_a is not None and cols_a == cols_b:  # nothing but 「列不变」
        if var:
            return settings.say("行数与变量都没变（内容有改动）", "Rows and variables unchanged (content changed)", lang)
        return settings.say("行数与列都没变（内容有改动）", "Rows and columns unchanged (content changed)", lang)
    return _sentence(parts, lang)


def _counted(n: Any, one: str, other: str) -> str:
    """「1 row」, 「2 rows」, 「1,497 rows」 (13.5: a singular and a plural)."""
    return f"{number_text(n, 'en')} {one if n == 1 else other}"


def table_shape(summary: dict[str, Any] | None, lang: str = "zh") -> str | None:
    """「2 行，列 a、b」 -- one table's rows and columns, in the app's
    words; None when it is not a table that was read. `lang`: the page's
    language (13.6), "2 rows; columns a, b" in English."""
    if not summary:
        return None
    if summary.get("columns") is None:
        rows, cols = summary.get("sheet_rows"), summary.get("sheet_columns")
        if rows is None:
            return None
        if lang == "en":
            return f"{_counted(rows, 'row', 'rows')}, {_counted(cols, 'column', 'columns')}"
        return f"{rows} 行，{cols} 列"
    rows = summary.get("rows")
    complete = summary.get("rows_complete", True)
    cols = [str(c) for c in summary.get("columns") or []]
    var = summary.get("format") == "dta"  # a .dta's columns are its variables (15.4)
    if lang == "en":
        one, other = ("variable", "variables") if var else ("column", "columns")
        parts = []
        if rows is not None:
            parts.append(_counted(rows, "row", "rows") if complete else f"{number_text(rows, 'en')} or more rows")
        if cols:
            listed = ", ".join(cols[:12]) + (f" and {number_text(len(cols) - 12, 'en')} more" if len(cols) > 12 else "")
            parts.append((f"{one} " if len(cols) == 1 else f"{other} ") + listed)
        else:
            parts.append(f"no {other}")
        return "; ".join(parts)
    word = "变量" if var else "列"
    more = "" if complete else " 以上"
    shown = "、".join(cols[:12]) + ("等 " + str(len(cols)) + " " + ("个变量" if var else "列") if len(cols) > 12 else "")
    return (f"{rows}{more} 行，" if rows is not None else "") + (f"{word} {shown}" if cols else f"没有{word}")


def _decode(data: bytes | None) -> str | None:
    if data is None or len(data) > TEXT_LIMIT or not _is_text(data):
        return None
    return data.decode("utf-8")


def diff(project_root: str | Path, node: str, a: str | None = None, b: str | None = None, *,
         words: bool = False, lang: str = "zh") -> dict[str, Any]:
    """Compare two versions of `node` (module docstring). `words`: every
    sentence in the screen's form, a relation said in words and never an
    arrow (12.13) -- the page's; the command line keeps its arrows. `lang`:
    the page's language for a file's sentences (13.6); Chinese by default,
    as the command line has it."""
    root = Path(project_root)
    listed = versions(root, node)
    found = listed["versions"]
    va, vb = _pick(found, a, b)
    out: dict[str, Any] = {"node": listed["node"], "kind": listed["kind"], "a": va, "b": vb, "diff": None,
                           "truncated": False, "summary": None, "same": False}
    if listed["kind"] == "attempt":
        return {**out, **_attempt_change(va, vb, words=words, lang=lang)}
    if listed["kind"] == "variable":
        return {**out, "summary": _variable_change(va, vb, words=words)}
    if listed["kind"] != "file":
        return out
    path = listed["path"]
    if listed["origin"] == "git":
        old_bytes, new_bytes = _git_blob(root, va), _git_blob(root, vb)
        summarised = (*_TABLE_DELIMITERS, ".xlsx", ".dta")
        sum_a = _git_table_summary(root, va) if Path(va["path"]).suffix.lower() in summarised else None
        sum_b = _git_table_summary(root, vb) if Path(vb["path"]).suffix.lower() in summarised else None
        out["same"] = va.get("blob") is not None and va.get("blob") == vb.get("blob")
    else:
        pid = _identity_id(root)
        with Store.open(pid) as store:
            old_bytes = store.read_blob(va["sha256"]) if va.get("stored") else None
            new_bytes = store.read_blob(vb["sha256"]) if vb.get("stored") else None
        sum_a, sum_b = va.get("summary") or None, vb.get("summary") or None
        out["same"] = va["sha256"] == vb["sha256"]
    old_text, new_text = _decode(old_bytes), _decode(new_bytes)
    if old_text is not None and new_text is not None:
        out["diff"], out["truncated"] = unified(
            old_text, new_text, f"{path} ({_label(va)})", f"{path} ({_label(vb)})",
        )
    out["summary"] = table_sentence(sum_a, sum_b, words=words, lang=lang)
    out["table"] = bool(sum_a or sum_b) and Path(path or "").suffix.lower() in _TABLE_DELIMITERS
    if out["summary"] is None and out["diff"] is None:
        out["summary"] = (settings.say("内容相同", "Identical content", lang) if out["same"]
                          else _size_sentence(va, vb, words=words, lang=lang))
    return out


def _label(v: dict[str, Any]) -> str:
    if v.get("commit"):
        return str(v["commit"])[:12]
    if v.get("version") == WORKTREE:
        return "工作区"
    return f"版本 {v.get('version')} · {v.get('at')}"


def _size_sentence(va: dict[str, Any], vb: dict[str, Any], *, words: bool = False, lang: str = "zh") -> str:
    sa, sb = va.get("size"), vb.get("size")
    if sa is None or sb is None:
        return settings.say("内容不同（未比对内容）", "Content differs (not compared)", lang)
    if sa == sb:
        return settings.say("内容不同（大小不变，指纹不同）", "Content differs (same size, different fingerprint)", lang)
    if words or lang == "en":
        return settings.say("内容不同（大小从 {a} 变为 {b} 字节）", "Content differs (size from {a} to {b} bytes)", lang,
                            a=number_text(sa, lang), b=number_text(sb, lang))
    return f"内容不同（大小 {sa} → {sb} 字节）"


def _verdict_word(v: dict[str, Any], lang: str = "zh") -> str:
    """What an attempt version's status reads as (13.4): a default word by its code, in `lang` (the stored 「待定」 is
    Pending in English); the researcher's own word, and a version with no reading, as written."""
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    status = v.get("verdict_status") or {}
    word = str(status.get("label") or v.get("verdict") or "其它")
    if lang == "en" and status.get("status") != "labelled":
        return attempts_ingest.VERDICT_ENGLISH.get(word, word)
    return word


def _verdict_reason(v: dict[str, Any]) -> str:
    """The reason of an attempt version's cell: the words the engine split off it (`verdict_status.words`), else the
    cell as written (21.3)."""
    words = (v.get("verdict_status") or {}).get("words")
    return str(words if isinstance(words, str) else v.get("verdict") or "").strip()


def _attempt_change(va: dict[str, Any], vb: dict[str, Any], *, words: bool = False, lang: str = "zh") -> dict[str, Any]:
    """How an attempt's row moved between two versions. `words`: the page's form (12.13) in `lang`, told by what the
    status reads as (21.3) -- another word is 「从…改为…」, the same word with another reason is 「仍是…，理由改了」, a cell
    that reads the same says nothing moved; the command line keeps its markers."""
    old_v, new_v = va.get("verdict") or "（空）", vb.get("verdict") or "（空）"
    changed = (va.get("result") or "") != (vb.get("result") or "")
    if words:
        # 12.13: 「研究判断从「待定」改为「现行」」 -- the status words, never the markers.
        la, lb = _verdict_word(va, lang), _verdict_word(vb, lang)
        if la != lb:
            parts = [settings.say("研究判断从「{was}」改为「{became}」", "Research assessment changed from “{was}” to “{became}”",
                                  lang, was=la, became=lb)]
        elif _verdict_reason(va) != _verdict_reason(vb):
            parts = [settings.say("研究判断仍是「{verdict}」，理由改了", "Research assessment still “{verdict}”; the reason changed",
                                  lang, verdict=lb)]
        else:
            parts = [settings.say("研究判断不变（「{verdict}」）", "Research assessment unchanged (“{verdict}”)", lang, verdict=lb)]
        if changed:
            parts.append(settings.say("结果改为：{result}", "new result: {result}", lang,
                                      result=vb.get("result") or settings.say("（空）", "(empty)", lang)))
        return {"summary": _sentence(parts, lang), "same": va.get("sha256") == vb.get("sha256")}
    parts = [f"{old_v} → {new_v}" if old_v != new_v else f"研究判断不变（{new_v}）"]
    if changed:
        parts.append(f"结果改为：{vb.get('result') or '（空）'}")
    return {"summary": "，".join(parts), "same": va.get("sha256") == vb.get("sha256")}


#: A variable card's acts in words (the page's ACT_WORDS).
CARD_ACT_WORDS = {"confirmed": "确认", "corrected": "更正", "reaffirmed": "确认口径未变", "abandoned": "弃用",
                  "revived": "恢复", "removed": "以文件为准移除", "settled": "指明以哪几条为准"}


def _variable_change(va: dict[str, Any], vb: dict[str, Any], *, words: bool = False) -> str:
    if words:
        def one(v: dict[str, Any]) -> str:
            act = CARD_ACT_WORDS.get(str(v.get("act")), str(v.get("act") or "记录"))
            return f"{act}第 {v.get('number')} 版" if v.get("number") else act
        return f"从「{one(va)}」改为「{one(vb)}」"
    return f"{va.get('act')} v{va.get('number') or ''} → {vb.get('act')} v{vb.get('number') or ''}".replace(" v ", " ")

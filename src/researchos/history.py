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
  read from the zip without parsing its XML). A file whose content is only
  in the cloud is never read. Nothing is pruned automatically. The store is
  not the index: deleting or rebuilding `graph.db` leaves it alone,
  because what it holds -- content a file no longer has -- cannot be
  derived again.
- **An attempt** (`attempt:<table>#<n>`): its verdict and result as they
  moved, recorded by every scan that read the attempt table (later phases
  add approvals, `source = "approval"`).
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
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection
from typing import IO, Any, Iterable

from researchos import db, paths
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
SOURCES = ("scan", "approval", "publish", "git")
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
        approved_by TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_versions_node ON versions(node, seq)",
)


class HistoryError(Exception):
    """A history could not be read as asked. `code`: `no_identity`,
    `not_a_node`, `no_such_version`, `git`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
        self.conn.commit()

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

    def add(self, node: str, *, sha256: str, size: int | None, kind: str, source: str = "scan", who: str | None = None,
            note: str | None = None, stored: bool = False, summary: dict[str, Any] | None = None,
            stat: str | None = None, at: str | None = None, approved_by: str | None = None) -> Version:
        if source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}, got {source!r}")
        cur = self.conn.execute(
            "INSERT INTO versions (node, at, sha256, size, who, source, note, kind, stored, summary, stat, approved_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (node, at or _now(), sha256, size, who, source, note, kind, int(stored),
             json.dumps(summary or {}, ensure_ascii=False, sort_keys=True), stat, approved_by),
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
    return None


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
                note: str | None = None, approved_by: str | None = None) -> str:
    """Record `rel`'s current content as a version if it differs from the
    newest one. Returns "recorded", "unchanged", "missing", "dataless" or
    "unreadable". A file whose size, modification time and inode are what
    the newest version saw is not read again. A version that came through
    an approved proposal (12.3) carries the member as `who` and the PI as
    `approved_by`; it is recorded even when the content is unchanged, so
    the approval itself is part of the file's history."""
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
    text = data is not None and _is_text(data)
    try:
        summary = data_summary(full, rel)
    except OSError:
        summary = None
    kind = "data" if summary is not None else ("text" if text else "binary")
    if text:
        store.put_blob(data)  # type: ignore[arg-type]
    store.add(node, sha256=sha, size=size, kind=kind, source=source, who=who, note=note, stored=text,
              summary=summary or {}, stat=stat, approved_by=approved_by)
    return "recorded"


def _attempt_digest(fields: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def record_attempts(store: Store, conn: Connection, *, source: str = "scan", who: str | None = None,
                    note: str | None = None, approved_by: str | None = None,
                    only: Iterable[str] | None = None) -> int:
    """Each attempt's verdict and result, recorded when it differs from the
    newest version (an attempt no longer in the table keeps its history).
    `only` limits it to those attempt node ids (an approval records the
    one attempt it changed, with who proposed and who approved)."""
    recorded = 0
    wanted = set(only) if only is not None else None
    for node in db.get_nodes_by_type(conn, "attempt"):
        if wanted is not None and node["id"] not in wanted:
            continue
        hf = node.get("human_fields") or {}
        fields = {"verdict": str(hf.get("verdict") or ""), "result": str(hf.get("result") or "")}
        digest = _attempt_digest(fields)
        latest = store.latest(node["id"])
        if latest is not None and latest.sha256 == digest:
            continue
        store.add(node["id"], sha256=digest, size=None, kind="attempt", source=source, who=who, note=note,
                  summary=fields, approved_by=approved_by)
        recorded += 1
    return recorded


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


def record_scan(conn: Connection | None, project_root: str | Path, *, files: Iterable[str] | None = None) -> dict[str, int]:
    """What a scan adds to the history (module docstring): every file of the
    inventory git does not track (all of them, in a project that is not a
    git repository) and the record files, then the attempts. `files`
    limits the file half to those paths (the watcher's changed files).
    Only for a project with an identity whose migration has finished. The
    caller holds the project lock; a failure is the caller's to contain."""
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
    with Store.open(got.identity.id) as store:
        for rel in dict.fromkeys(candidates):
            if tracked is not None and rel in tracked:
                continue
            outcome = record_file(store, root, rel)
            counts[outcome] = counts.get(outcome, 0) + 1
        if conn is not None:
            counts["attempts"] = record_attempts(store, conn)
    return counts


def record_after_scan(conn: Connection | None, project_root: str | Path, *, files: Iterable[str] | None = None) -> dict[str, int]:
    """`record_scan`, contained: a history that could not be written never
    fails the scan it follows."""
    try:
        return record_scan(conn, project_root, files=files)
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


def _store_payload(v: Version) -> dict[str, Any]:
    return {
        "version": str(v.seq), "at": v.at, "sha256": v.sha256, "size": v.size, "who": v.who,
        "who_label": _who_label(v.who), "source": v.source, "note": v.note, "kind": v.kind,
        "summary": v.summary, "stored": v.stored, "approved_by": v.approved_by,
    }


def versions(project_root: str | Path, node: str) -> dict[str, Any]:
    """Every version of `node`, newest first (module docstring)."""
    root = Path(project_root)
    try:
        node_id = notes_mod.normalize_node(node)
    except ValueError as exc:
        raise HistoryError("not_a_node", str(exc)) from exc
    pid = _identity_id(root)
    prefix = node_id.partition(":")[0]
    if prefix == "variable":
        return _variable_versions(root, node_id)
    if prefix == "attempt":
        store = Store.existing(pid)
        found = [] if store is None else [_attempt_payload(v) for v in store.versions(node_id)]
        if store is not None:
            store.close()
        return {"node": node_id, "kind": "attempt", "origin": "store", "path": None, "versions": found}
    rel = _file_of(node_id)
    if rel is None:
        return {"node": node_id, "kind": "other", "origin": None, "path": None, "versions": [],
                "reason": "这个节点不是文件，没有版本"}
    file_node = notes_mod.normalize_node(rel)
    if is_tracked(root, rel):
        found = git_versions(root, rel)
        worktree = _worktree_version(root, rel, found[0] if found else None)
        out = ([worktree] if worktree else []) + found
        _attribute_git(root, pid, file_node, out)
        for v in out:
            v["who_label"] = _who_label(v.get("who"), v.get("who_name"))
        return {"node": node_id, "file_node": file_node, "kind": "file", "origin": "git", "path": rel, "versions": out}
    store = Store.existing(pid)
    found = [] if store is None else [_store_payload(v) for v in store.versions(file_node)]
    if store is not None:
        store.close()
    return {"node": node_id, "file_node": file_node, "kind": "file", "origin": "store", "path": rel, "versions": found}


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


def _attempt_payload(v: Version) -> dict[str, Any]:
    out = _store_payload(v)
    out["verdict"] = v.summary.get("verdict", "")
    out["result"] = v.summary.get("result", "")
    return out


def _variable_versions(root: Path, node_id: str) -> dict[str, Any]:
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


def table_sentence(a: dict[str, Any] | None, b: dict[str, Any] | None) -> str | None:
    """「行数 96 → 97，列不变」, 「新增列 log_ret」 -- what changed in a table,
    in the app's words; None when neither side is a table."""
    if not a and not b:
        return None
    a, b = a or {}, b or {}
    if (a.get("format") == "xlsx" or b.get("format") == "xlsx") and not (a.get("columns") is not None
                                                                         and b.get("columns") is not None):
        ra, rb = a.get("sheet_rows"), b.get("sheet_rows")
        ca, cb = a.get("sheet_columns"), b.get("sheet_columns")
        if ra is None or rb is None:
            return "工作表的行列数未比对"
        parts = [f"行数 {ra} → {rb}" if ra != rb else f"行数不变（{rb}）"]
        parts.append(f"列数 {ca} → {cb}" if ca != cb else "列数不变")
        return "，".join(parts)
    parts = []
    ra, rb = a.get("rows"), b.get("rows")
    if ra is None or rb is None:
        parts.append("行数未比对")
    elif ra != rb or not (a.get("rows_complete", True) and b.get("rows_complete", True)):
        more = "" if b.get("rows_complete", True) else " 以上"
        parts.append(f"行数 {ra} → {rb}{more}")
    cols_a, cols_b = a.get("columns"), b.get("columns")
    if cols_a is None or cols_b is None:
        parts.append("列未比对")
    else:
        added = [c for c in cols_b if c not in cols_a]
        removed = [c for c in cols_a if c not in cols_b]
        if added:
            parts.append(f"新增列 {'、'.join(added)}")
        if removed:
            parts.append(f"删除列 {'、'.join(removed)}")
        if not added and not removed:
            parts.append("列不变" if cols_a == cols_b else "列的顺序变了")
    if parts == ["列不变"]:
        return "行数与列都没变（内容有改动）"
    return "，".join(parts)


def _decode(data: bytes | None) -> str | None:
    if data is None or len(data) > TEXT_LIMIT or not _is_text(data):
        return None
    return data.decode("utf-8")


def diff(project_root: str | Path, node: str, a: str | None = None, b: str | None = None) -> dict[str, Any]:
    """Compare two versions of `node` (module docstring)."""
    root = Path(project_root)
    listed = versions(root, node)
    found = listed["versions"]
    va, vb = _pick(found, a, b)
    out: dict[str, Any] = {"node": listed["node"], "kind": listed["kind"], "a": va, "b": vb, "diff": None,
                           "truncated": False, "summary": None, "same": False}
    if listed["kind"] == "attempt":
        return {**out, **_attempt_change(va, vb)}
    if listed["kind"] == "variable":
        return {**out, "summary": _variable_change(va, vb)}
    if listed["kind"] != "file":
        return out
    path = listed["path"]
    if listed["origin"] == "git":
        old_bytes, new_bytes = _git_blob(root, va), _git_blob(root, vb)
        sum_a = _git_table_summary(root, va) if Path(va["path"]).suffix.lower() in (*_TABLE_DELIMITERS, ".xlsx") else None
        sum_b = _git_table_summary(root, vb) if Path(vb["path"]).suffix.lower() in (*_TABLE_DELIMITERS, ".xlsx") else None
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
    out["summary"] = table_sentence(sum_a, sum_b)
    if out["summary"] is None and out["diff"] is None:
        out["summary"] = "内容相同" if out["same"] else _size_sentence(va, vb)
    return out


def _label(v: dict[str, Any]) -> str:
    if v.get("commit"):
        return str(v["commit"])[:12]
    if v.get("version") == WORKTREE:
        return "工作区"
    return f"版本 {v.get('version')} · {v.get('at')}"


def _size_sentence(va: dict[str, Any], vb: dict[str, Any]) -> str:
    sa, sb = va.get("size"), vb.get("size")
    if sa is None or sb is None:
        return "内容不同（未比对内容）"
    return f"内容不同（大小 {sa} → {sb} 字节）" if sa != sb else "内容不同（大小不变，指纹不同）"


def _attempt_change(va: dict[str, Any], vb: dict[str, Any]) -> dict[str, Any]:
    old_v, new_v = va.get("verdict") or "（空）", vb.get("verdict") or "（空）"
    parts = [f"{old_v} → {new_v}" if old_v != new_v else f"结论不变（{new_v}）"]
    if (va.get("result") or "") != (vb.get("result") or ""):
        parts.append(f"结果改为：{vb.get('result') or '（空）'}")
    return {"summary": "，".join(parts), "same": va.get("sha256") == vb.get("sha256")}


def _variable_change(va: dict[str, Any], vb: dict[str, Any]) -> str:
    return f"{va.get('act')} v{va.get('number') or ''} → {vb.get('act')} v{vb.get('number') or ''}".replace(" v ", " ")

"""The run store: one folder per agent run, on this Mac only (DESIGN.md 17.3).

`<researchos_home>/agent-runs/<project id>/<run id>/` holds everything a
run leaves behind -- `run.json` (the state the page shows), `instruction.txt`
(what was sent), `events.jsonl` (the program's own stream), `stderr.log`,
`signals.jsonl` (what the agent tools reported), `guard.json` and
`refusals.jsonl` (the hook's rules and what it refused), `before.json` /
`after.json` and the `before/` copies (17.5, 17.6), and the generated
`claude-settings.json` / `mcp.json`. It lives in the Research OS home, never
in the project: the run log "stays on the Mac that ran it" (17.2).

**Who writes what.** Only the engine writes `run.json`, atomically
(`records.files.durable_write`); every read tolerates a missing or damaged
file. The MCP agent-tool process, which runs in another process, only
appends to `signals.jsonl` (`append_signal`), and the hook only appends to
`refusals.jsonl`; both go through `append_line`, one `O_APPEND` write per
record, so concurrent appenders never interleave a line. The hook imports
this module, so it imports nothing heavy at the top: `researchos.paths` and
the records package are loaded inside the functions that need them.

**Run ids** `r-YYYYMMDDTHHMMSSZ-<6 hex>` sort in creation order (within one
process they are strictly increasing, even inside one second), so "newest
first" is a sort on the id.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RUN_ID_RE = re.compile(r"r-\d{8}T\d{6}Z-[0-9a-f]{6}\Z")
#: `out_of_bounds` (18.1 item 4) is how a run reads that ended with an out-of-bounds change, whatever else it
#: would have read; `outcome` then keeps the state it would have had (`outcome_of`).
STATES = ("preparing", "running", "needs_input", "done", "ended", "failed", "stopped", "interrupted",
          "out_of_bounds")
ACTIVE_STATES = frozenset({"preparing", "running"})
SIGNAL_KINDS = frozenset({"complete", "ask", "note"})
#: Run folders kept per project: `run.json`, `events.jsonl` and `instruction.txt` (the run's log) stay for the
#: newest this many runs; older folders are removed whole (17.6).
KEEP_RUNS = 200
#: The before-picture of the newest this many runs is kept (the `before/` copies, `before.json`, `after.json`):
#: older runs keep their log but can no longer be undone (17.6).
KEEP_BACKUPS = 20
EVENTS_LIMIT = 20 * 1024 * 1024
#: `stderr.log` is capped as `events.jsonl` is (18.1 item 6); a run that hit either cap is marked `events_truncated` /
#: `stderr_truncated`.
STDERR_LIMIT = EVENTS_LIMIT

RUNS_DIRNAME = "agent-runs"
RUN_FILENAME = "run.json"
SIGNALS_FILENAME = "signals.jsonl"

_LOCK = threading.Lock()
_ID_LOCK = threading.Lock()
_last_id: tuple[datetime, int] | None = None


def now_iso() -> str:
    """The present as a UTC ISO string with a `Z`, e.g. `2026-10-21T08:30:00.123456Z`."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _project_id_re() -> re.Pattern[str]:
    from researchos.records.lock import PROJECT_ID_RE  # noqa: PLC0415 -- keeps the hook's imports light

    return PROJECT_ID_RE


def _check_project_id(project_id: object) -> str:
    if not isinstance(project_id, str) or not _project_id_re().match(project_id):
        raise ValueError(f"not a project id: {project_id!r}")
    return project_id


def _check_run_id(run_id: object) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise ValueError(f"not a run id: {run_id!r}")
    return run_id


def runs_root() -> Path:
    from researchos import paths  # noqa: PLC0415 -- keeps the hook's imports light

    return paths.researchos_home() / RUNS_DIRNAME


def project_runs_dir(project_id: str) -> Path:
    return runs_root() / _check_project_id(project_id)


def project_ids() -> list[str]:
    """The ids of the projects that have a folder of runs on this Mac, sorted; a folder whose name is not a
    project id is not one."""
    try:
        found = sorted(e.name for e in os.scandir(runs_root()) if e.is_dir(follow_symlinks=False))
    except OSError:
        return []
    out = []
    for name in found:
        try:
            out.append(_check_project_id(name))
        except ValueError:
            continue
    return out


def new_run_id(now: datetime | None = None) -> str:
    """A fresh run id. Without `now` the id is also strictly greater than
    every id this process minted before, so ids sort in creation order even
    for runs started within one second (a reply right after its parent)."""
    global _last_id
    if now is not None:
        stamp = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
        return f"r-{stamp:%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    with _ID_LOCK:
        stamp = datetime.now(timezone.utc).replace(microsecond=0)
        low = 0
        if _last_id is not None:
            last_stamp, last_hex = _last_id
            if stamp < last_stamp:
                stamp = last_stamp
            if stamp == last_stamp:
                low = last_hex + 1
                if low > 0xFFFFFF:  # 16 million runs in one second: borrow the next one
                    stamp += timedelta(seconds=1)
                    low = 0
        value = low + secrets.randbelow(0xFFFFFF - low + 1)
        _last_id = (stamp, value)
        return f"r-{stamp:%Y%m%dT%H%M%SZ}-{value:06x}"


def run_dir(project_id: str, run_id: str) -> Path:
    return project_runs_dir(project_id) / _check_run_id(run_id)


def outcome_of(run: dict[str, Any]) -> Any:
    """How the run ended apart from the warning: its state, or, for a run that went out of bounds, the state
    it would otherwise have read (`outcome`: `done`, `ended`, `needs_input`, `failed`, `stopped` or
    `interrupted`) -- what decides whether it can be answered or sent back."""
    if run.get("state") == "out_of_bounds" and run.get("outcome"):
        return run["outcome"]
    return run.get("state")


# -- run.json -------------------------------------------------------------------------------


def _dump(run: dict[str, Any]) -> bytes:
    return (json.dumps(run, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write_run(directory: Path, run: dict[str, Any]) -> None:
    from researchos.records import files  # noqa: PLC0415 -- keeps the hook's imports light

    files.durable_write(directory / RUN_FILENAME, _dump(run))


def create_run(project_id: str, run: dict[str, Any]) -> dict[str, Any]:
    """Make the run's folder (private to the user) and write its `run.json`.
    `run` must carry a valid `"id"`; FileExistsError when that run exists.
    Returns the stored dict."""
    run = dict(run)
    directory = run_dir(project_id, run.get("id"))  # type: ignore[arg-type]
    run.setdefault("project_id", project_id)
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir(exist_ok=False)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    _write_run(directory, run)
    return run


def _read_run(directory: Path, run_id: str) -> dict[str, Any] | None:
    try:
        data = json.loads((directory / RUN_FILENAME).read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError):  # UnicodeError and JSONDecodeError are ValueErrors
        return None
    return data if isinstance(data, dict) and data.get("id") == run_id else None


def load_run(project_id: str, run_id: str) -> dict[str, Any] | None:
    """The run's `run.json`, or None when it is missing, unreadable, not a
    JSON object or names another run."""
    return _read_run(run_dir(project_id, run_id), run_id)


def save_run(project_id: str, run: dict[str, Any]) -> None:
    """Replace the run's `run.json`. The run's folder must exist."""
    _write_run(run_dir(project_id, run.get("id")), run)  # type: ignore[arg-type]


def update_run(project_id: str, run_id: str, **changes: Any) -> dict[str, Any]:
    """Read-modify-write `run.json` under one lock (the manager's thread and
    request threads both update runs). KeyError when the run is missing;
    ValueError when `changes` would rename it. Returns the stored dict."""
    if "id" in changes and changes["id"] != run_id:
        raise ValueError("a run's id cannot change")
    directory = run_dir(project_id, run_id)
    with _LOCK:
        run = _read_run(directory, run_id)
        if run is None:
            raise KeyError(run_id)
        run.update(changes)
        _write_run(directory, run)
        return run


def update_run_if_active(project_id: str, run_id: str, **changes: Any) -> dict[str, Any] | None:
    """`update_run`, but only while the run is still in an active state, checked and written under the
    same lock (a compare-and-set): a run that reached a terminal state meanwhile is left alone and None is
    returned. KeyError when the run is missing; ValueError when `changes` would rename it."""
    if "id" in changes and changes["id"] != run_id:
        raise ValueError("a run's id cannot change")
    directory = run_dir(project_id, run_id)
    with _LOCK:
        run = _read_run(directory, run_id)
        if run is None:
            raise KeyError(run_id)
        if run.get("state") not in ACTIVE_STATES:
            return None
        run.update(changes)
        _write_run(directory, run)
        return run


def list_runs(project_id: str) -> list[dict[str, Any]]:
    """The project's runs, newest first; folders without a readable
    `run.json` are skipped."""
    out = []
    for run_id in _run_ids(project_id):
        run = _read_run(project_runs_dir(project_id) / run_id, run_id)
        if run is not None:
            out.append(run)
    return out


def _run_ids(project_id: str) -> list[str]:
    """The run folder names of a project, newest first."""
    base = project_runs_dir(project_id)
    try:
        names = [e.name for e in os.scandir(base) if RUN_ID_RE.match(e.name) and e.is_dir(follow_symlinks=False)]
    except OSError:
        return []
    return sorted(names, reverse=True)


# -- appended records --------------------------------------------------------------------------


def append_line(path: Path, record: dict[str, Any]) -> None:
    """Append `record` as one JSON line: a single `write` on an `O_APPEND`
    descriptor (so two processes never interleave inside a line), then
    fsync. The folder must exist; the file is created (mode 0600)."""
    data = (json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        view = memoryview(data)
        while view:  # a short write is not expected on a regular file; finish it if it happens
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)


def append_signal(project_id: str, run_id: str, kind: str, **fields: Any) -> None:
    """Record what an agent tool did, in `signals.jsonl`: `kind` is
    `"complete"`, `"ask"` or `"note"`; `at` and `kind` are added."""
    if kind not in SIGNAL_KINDS:
        raise ValueError(f"signal kind must be one of {', '.join(sorted(SIGNAL_KINDS))}, not {kind!r}")
    if "at" in fields:
        raise ValueError("'at' is set by append_signal")
    append_line(run_dir(project_id, run_id) / SIGNALS_FILENAME, {"at": now_iso(), "kind": kind, **fields})


def read_signals(project_id: str, run_id: str) -> list[dict[str, Any]]:
    return read_lines(run_dir(project_id, run_id) / SIGNALS_FILENAME)


def read_lines(path: Path, *, tail_bytes: int | None = None) -> list[dict[str, Any]]:
    """A JSON-lines file as dicts, tolerantly: a missing file is `[]`, and a
    line that is not a JSON object (a half-written last line included) is
    skipped. With `tail_bytes`, only the last that many bytes are read and a
    line cut by the start of that window is dropped."""
    try:
        with open(path, "rb") as handle:
            if tail_bytes is not None and tail_bytes >= 0:
                size = os.fstat(handle.fileno()).st_size
                if size > tail_bytes:
                    handle.seek(size - tail_bytes - 1)
                    blob = handle.read()
                    # the byte before the window: if it is not a newline the first line is cut
                    cut = blob[:1] != b"\n"
                    blob = blob[1:]
                    if cut:
                        _, _, blob = blob.partition(b"\n")
                else:
                    blob = handle.read()
            else:
                blob = handle.read()
    except OSError:
        return []
    out = []
    for line in blob.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


# -- retention ----------------------------------------------------------------------------------


def prune(project_id: str) -> None:
    """Retention (17.6): the newest `KEEP_RUNS` run folders of the project are kept (never one that is
    active), older ones are removed whole; runs beyond the newest `KEEP_BACKUPS` lose their `before/` copies
    and the two pictures (`before.json`, `after.json`) and become not revertible, but keep `run.json`,
    `events.jsonl` and `instruction.txt` -- the log. Errors are logged, never raised: a failed clean-up
    must not fail a finished run."""
    for index, run_id in enumerate(_run_ids(project_id)):
        directory = project_runs_dir(project_id) / run_id
        try:
            run = _read_run(directory, run_id)
            if run is not None and run.get("state") in ACTIVE_STATES:
                continue
            if index >= KEEP_RUNS:
                shutil.rmtree(directory, ignore_errors=True)
            elif index >= KEEP_BACKUPS:
                before = directory / "before"
                pictures = [directory / "before.json", directory / "after.json"]
                if (before.exists() or any(p.exists() for p in pictures)
                        or (run is not None and run.get("revertible") is not False)):
                    shutil.rmtree(before, ignore_errors=True)
                    for picture in pictures:
                        try:
                            picture.unlink()
                        except FileNotFoundError:
                            pass
                    if run is not None:
                        update_run(project_id, run_id, revertible=False)
        except (OSError, KeyError, ValueError) as exc:
            logger.warning("could not prune agent run %s: %s", run_id, exc)

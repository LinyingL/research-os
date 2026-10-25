"""The run store's blobs, its size, and what keeps it within its limit (DESIGN.md 19.8, 19.1 item 3).

**One copy of each content.** `<home>/agent-runs/<project id>/blobs/<aa>/<sha256>` holds one read-only (0o444) file for each
content a before-picture copied (`snapshot.take(blobs=)`), and a run's `before/<path>` is a hard link to it: two runs over
the same project store one copy, a changed file gets a new blob. Undo (`manager`) only ever reads a blob and writes
the bytes into the project; it never moves or links one there, so a shared blob is never changed, and the modification
time and mode it restores come from the picture's entry (`mtime_ns`, `mode`), not from the shared file.

**Who mutates the blobs.** Conversion of an old store (`convert`), the release of a run's backup (`release`) and the deletion
of the blobs no run links any more (`collect`: `st_nlink == 1`) are done under one lock per project (`lock`). A run taking
its before-picture (`snapshot.take`) does not wait for it, because it may read for minutes: it makes a blob under a temporary
name inside `blobs/` and links it (`settle`), and a blob that is deleted under it only makes it copy the file once, as a
store without blobs does. A blob a run has linked is safe from `collect` whatever the order, since a file with two links is
never collected, and a run that links a blob `collect` is just deleting keeps the file it linked.

**What a project's store is.** Its blobs (each once), every run's `reverted/` (the files an undo moved away: never removed
automatically), and everything else in its run folders (logs, pictures), each file counted once whatever its links
(`scan`). `enforce` keeps it within the limit by releasing the backup of the oldest run that still holds one and is not
active, until it is.

Stdlib only at the top: `runs` is imported by the guard's hook, and this module imports `runs`, not the other way round
(`runs.prune` imports this module when it needs it).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import stat
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from researchos.agents import runs

logger = logging.getLogger(__name__)

BLOBS_DIRNAME = "blobs"
BEFORE_DIRNAME = "before"
MOVED_DIRNAME = "reverted"
#: The picture files of a run (17.5) that go with its `before/` copies when a backup is released.
PICTURES = ("before.json", "after.json")
#: Written into the project's run folder when its store has been looked through once (`convert`).
MARKER = "store.json"
BLOB_MODE = 0o444
#: `settings.json` `agent_store_limit_gb` is whole gigabytes, as many bytes as the 1 GiB one run may copy (`snapshot.COPY_BUDGET`).
GB = 1024 ** 3
_TMP_PREFIX = ".tmp-"
#: A temporary name in `blobs/` older than this is the leftover of a run that died; `collect` removes it.
STALE_TMP_S = 24 * 3600
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
_CHUNK = 1024 * 1024

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def lock(project_id: str) -> threading.RLock:
    """The one lock of the project's blob-store mutations: conversion, release and deletion (module docstring)."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(project_id, threading.RLock())


def blobs_dir(project_id: str) -> Path:
    return runs.project_runs_dir(project_id) / BLOBS_DIRNAME


def blob_path(blobs: Path | str, sha: str) -> Path:
    return Path(blobs) / sha[:2] / sha


def _unlink_quietly(path: Path | str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# -- making a blob (a run taking its before-picture) ----------------------------------------------------------------------


def _mkdir(path: Path | str) -> None:
    """A folder of the store, private to the user as the run folders are (a blob is a copy of the researcher's file)."""
    os.makedirs(path, mode=0o700, exist_ok=True)


def new_tmp(blobs: Path | str) -> str:
    """A fresh temporary name inside `blobs/` (made if need be): where a file is hashed and written before it is known
    whether its content already has a blob. Same folder, so the later link and rename stay on one volume."""
    _mkdir(blobs)
    return os.path.join(blobs, f"{_TMP_PREFIX}{os.getpid()}-{secrets.token_hex(6)}")


def link(blobs: Path | str, sha: str, dest: str) -> bool:
    """Make `dest` a hard link to the blob of `sha`; False when there is none, or the file system will not link (another
    volume, no hard links) -- the caller then copies, as a store without blobs does."""
    if not _SHA_RE.match(sha):
        return False
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.link(blob_path(blobs, sha), dest)
        return True
    except OSError:
        return False


def settle(blobs: Path | str, tmp: str, sha: str, dest: str) -> tuple[bool, bool]:
    """`tmp` is a finished temporary file in `blobs/` that holds the content whose SHA-256 is `sha`. If there is no blob for it
    the file becomes it (read only); either way `dest` becomes a hard link to the blob, and the temporary name is gone.
    Returns `(linked, new)`: `new` when the content was stored by this call. A blob that is deleted between the two links
    is tried again. False for `linked` when `dest` could not be linked (the caller copies the file instead)."""
    final = blob_path(blobs, sha)
    try:
        _mkdir(final.parent)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.chmod(tmp, BLOB_MODE)
        for _ in range(3):
            try:
                os.link(tmp, final)  # no blob yet: this inode becomes it
                source, new = tmp, True
            except FileExistsError:
                source, new = str(final), False  # there is one: ours is discarded
            try:
                os.link(source, dest)
                return True, new
            except FileNotFoundError:
                continue  # `collect` removed the blob between the two links: look again
        return False, False
    except OSError:
        return False, False
    finally:
        _unlink_quietly(tmp)


def file_sha256(path: Path | str) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


# -- what a run's folder holds ----------------------------------------------------------------------------------------


def _files(top: Path | str) -> Iterator[tuple[str, os.stat_result]]:
    """Every regular file below the folder `top` (links are not followed, a missing folder holds none) with its `lstat`."""
    stack = [str(top)]
    while stack:
        here = stack.pop()
        try:
            with os.scandir(here) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            yield entry.path, entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
        except OSError:
            continue


def holds_backup(rd: Path) -> bool:
    """Whether the run's folder still has its undo backup: the copies or one of the two pictures."""
    return any(os.path.lexists(rd / name) for name in (BEFORE_DIRNAME, *PICTURES))


def holds_moved(rd: Path) -> bool:
    """Whether the run's `reverted/` holds a file (an undo moved it away)."""
    return next(_files(rd / MOVED_DIRNAME), None) is not None


class Usage:
    """What a project's store takes (`scan`), in bytes, each file once: `backups` (blobs and any `before/` copy that is
    no link of one), `moved` (`reverted/` of every run), `records` (the rest: logs, pictures); `moved_runs` is
    `[{"run", "size", "files"}]` for the runs whose `reverted/` holds a file, oldest first."""

    def __init__(self) -> None:
        self.backups = self.moved = self.records = 0
        self.moved_runs: list[dict[str, Any]] = []

    @property
    def total(self) -> int:
        return self.backups + self.moved + self.records


def scan(project_id: str) -> Usage:
    """Walk the project's run folders (stat only) and add up their files, a file with more than one link counted once."""
    base = runs.project_runs_dir(project_id)
    usage = Usage()
    seen: set[tuple[int, int]] = set()

    def count(st: os.stat_result) -> int:
        if st.st_nlink > 1:
            key = (st.st_dev, st.st_ino)
            if key in seen:
                return 0
            seen.add(key)
        return st.st_size

    def kind(entry: os.DirEntry[str]) -> str:
        try:
            return "dir" if entry.is_dir(follow_symlinks=False) else "file" if entry.is_file(follow_symlinks=False) else ""
        except OSError:
            return ""

    for _path, st in _files(base / BLOBS_DIRNAME):  # first: a blob's links in `before/` then add nothing
        usage.backups += count(st)
    try:
        listed = sorted(os.scandir(base), key=lambda e: e.name)
    except OSError:
        return usage
    for entry in listed:
        if entry.name == BLOBS_DIRNAME:
            continue
        if kind(entry) == "file":
            with contextlib.suppress(OSError):
                usage.records += count(entry.stat(follow_symlinks=False))
            continue
        if kind(entry) != "dir" or not runs.RUN_ID_RE.match(entry.name):
            continue
        for _path, st in _files(entry.path + os.sep + BEFORE_DIRNAME):
            usage.backups += count(st)
        size = files = 0
        for _path, st in _files(entry.path + os.sep + MOVED_DIRNAME):
            size += count(st)
            files += 1
        usage.moved += size
        if files:
            usage.moved_runs.append({"run": entry.name, "size": size, "files": files})
        try:
            parts = list(os.scandir(entry.path))
        except OSError:
            continue
        for part in parts:
            if part.name in (BEFORE_DIRNAME, MOVED_DIRNAME):
                continue
            if kind(part) == "dir":
                usage.records += sum(count(st) for _p, st in _files(part.path))
            elif kind(part) == "file":
                with contextlib.suppress(OSError):
                    usage.records += count(part.stat(follow_symlinks=False))
    return usage


# -- releasing ------------------------------------------------------------------------------------------------------------


def release(project_id: str, run_id: str, *, sweep: bool = True) -> None:
    """Release the run's undo backup: `before/`, `before.json` and `after.json` go, the run can no longer be undone
    (`revertible` false) and keeps everything else, `run.json` and its record of what was undone included. With `sweep`
    the blobs nothing links any more are deleted too."""
    rd = runs.run_dir(project_id, run_id)
    with lock(project_id):
        try:  # first: from here the run is not offered for undo, whatever is left of its copies if this is cut short
            run = runs.load_run(project_id, run_id)
            if run is not None and run.get("revertible") is not False:
                runs.update_run(project_id, run_id, revertible=False)
        except (KeyError, OSError, ValueError):
            logger.warning("could not record the release of the backup of agent run %s", run_id, exc_info=True)
        for name in PICTURES:
            _unlink_quietly(rd / name)
        shutil.rmtree(rd / BEFORE_DIRNAME, ignore_errors=True)
        if sweep:
            collect(project_id)


def strip(project_id: str, run_id: str) -> None:
    """The 200-run rule (17.6) for a run that holds files an undo moved away: its `run.json` and `reverted/` stay, everything
    else of it goes (and it can no longer be undone). Idempotent."""
    rd = runs.run_dir(project_id, run_id)
    with lock(project_id):
        try:
            entries = list(os.scandir(rd))
        except OSError:
            return
        for entry in entries:
            if entry.name in (runs.RUN_FILENAME, MOVED_DIRNAME):
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path, ignore_errors=True)
            else:
                _unlink_quietly(entry.path)
        try:
            run = runs.load_run(project_id, run_id)
            if run is not None and run.get("revertible") is not False:
                runs.update_run(project_id, run_id, revertible=False)
        except (KeyError, OSError, ValueError):
            logger.warning("could not record that agent run %s can no longer be undone", run_id, exc_info=True)


def collect(project_id: str) -> int:
    """Delete the blobs no run links any more (a file with a single link: its own name), and the temporary names a dead run
    left. Returns the bytes freed. The folders `blobs/<aa>/` stay: a run taking its picture may be about to link into one."""
    blobs = blobs_dir(project_id)
    freed = 0
    with lock(project_id):
        try:
            tops = list(os.scandir(blobs))
        except OSError:
            return 0
        for top in tops:
            try:
                if top.is_dir(follow_symlinks=False):
                    for blob in list(os.scandir(top.path)):
                        st = blob.stat(follow_symlinks=False)
                        if stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                            os.unlink(blob.path)
                            freed += st.st_size
                elif top.name.startswith(_TMP_PREFIX):
                    st = top.stat(follow_symlinks=False)
                    if time.time() - st.st_mtime > STALE_TMP_S and st.st_nlink == 1:
                        os.unlink(top.path)
                        freed += st.st_size
            except OSError:
                continue
    return freed


def releasable(run: dict[str, Any]) -> bool:
    """Whether a run's backup may be released: it is not going, and not waiting for its changes to be compared (17.3)."""
    return run.get("state") not in runs.ACTIVE_STATES and not run.get("check_pending")


def holders(project_id: str, *, skip: Callable[[dict[str, Any]], bool] = lambda run: False) -> list[dict[str, Any]]:
    """The runs that hold a backup and may release it, oldest first."""
    found = []
    for run in sorted(runs.list_runs(project_id), key=lambda r: r["id"]):
        rd = runs.run_dir(project_id, run["id"])
        if releasable(run) and not skip(run) and holds_backup(rd):
            found.append(run)
    return found


def enforce(project_id: str, limit_bytes: int, *,
            skip: Callable[[dict[str, Any]], bool] = lambda run: False) -> tuple[list[str], Usage]:
    """While the project's store is over `limit_bytes`, release the backup of the oldest run that holds one and is not
    active (`skip` names more that stay). Returns the ids released, oldest first, and the usage as it ends. What cannot be
    released (active runs, `reverted/`) is left: the store may stay over the limit."""
    released: list[str] = []
    tried: set[str] = set()
    while True:
        usage = scan(project_id)
        if usage.total <= limit_bytes:
            break
        victim = next((r for r in holders(project_id, skip=skip) if r["id"] not in tried), None)
        if victim is None:
            break
        tried.add(victim["id"])
        release(project_id, victim["id"])
        released.append(victim["id"])
    return released, usage


def release_all(project_id: str, *, skip: Callable[[dict[str, Any]], bool] = lambda run: False) -> list[str]:
    """The person asked: release every backup but the active runs' (`skip` names more). Returns the ids released."""
    released = []
    for run in holders(project_id, skip=skip):
        release(project_id, run["id"], sweep=False)
        released.append(run["id"])
    collect(project_id)
    return released


def delete_moved(project_id: str, run_id: str) -> int:
    """The person asked: delete the files an undo moved away from this run (its `reverted/`, and nothing else). Returns the
    bytes freed."""
    rd = runs.run_dir(project_id, run_id)
    with lock(project_id):
        freed = sum(st.st_size for _path, st in _files(rd / MOVED_DIRNAME))
        shutil.rmtree(rd / MOVED_DIRNAME, ignore_errors=True)
    return freed


# -- converting an old store ----------------------------------------------------------------------------------------------


def converted(project_id: str) -> bool:
    return os.path.lexists(runs.project_runs_dir(project_id) / MARKER)


def needs_conversion(project_id: str) -> bool:
    """Whether the project has run folders that were never looked through (19.8: once per project)."""
    return not converted(project_id) and next(runs.iter_runs(project_id), None) is not None


def _read_picture(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("files"), dict) else None


def _record_modes(rd: Path) -> bool:
    """Write into `before.json` the mode of every copied file that lacks one, read from its copy in `before/`: a blob is
    read only and shared, so undo restores the mode from the entry. False when the picture cannot be read."""
    from researchos.records import files as record_files  # noqa: PLC0415 -- keeps the hook's imports light

    path = rd / "before.json"
    picture = _read_picture(path)
    if picture is None:
        return False
    changed = False
    for rel, entry in picture["files"].items():
        if not isinstance(entry, dict) or not entry.get("copied") or "mode" in entry:
            continue
        try:
            st = os.lstat(os.path.join(rd, BEFORE_DIRNAME, *rel.split("/")))
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            entry["mode"] = stat.S_IMODE(st.st_mode)
            changed = True
    if changed:
        record_files.durable_write(path, json.dumps(picture, ensure_ascii=False).encode("utf-8"))
    return True


def _convert_file(project_id: str, blobs: Path, path: str) -> None:
    """Make one `before/` file a link to the blob of its content: the file itself becomes the blob when there is none, else
    it is replaced (atomically) by a link to the one there is. A file that already has other links is one already."""
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
            return
    except OSError:
        return
    sha = file_sha256(path)  # outside the lock: reading may take a while
    if sha is None:
        return
    final = blob_path(blobs, sha)
    with lock(project_id):
        try:
            now = os.lstat(path)
            if (now.st_ino, now.st_size, now.st_mtime_ns) != (before.st_ino, before.st_size, before.st_mtime_ns) or now.st_nlink > 1:
                return  # released, replaced or linked since
            _mkdir(final.parent)
            try:
                os.link(path, final)
                os.chmod(final, BLOB_MODE)  # the one inode, now read only: the mode is in the entry
            except FileExistsError:
                tmp = new_tmp(blobs)
                try:
                    os.link(final, tmp)
                    os.replace(tmp, path)
                finally:
                    _unlink_quietly(tmp)
        except OSError:
            logger.debug("could not link %s to a blob", path, exc_info=True)


def _convert_run(project_id: str, rd: Path, stop: Callable[[], bool]) -> bool:
    """Convert the `before/` copies of one run (`convert`); False when stopped."""
    blobs = blobs_dir(project_id)
    with lock(project_id):  # a release must not find `before.json` written back after it removed it
        if not os.path.isdir(rd / BEFORE_DIRNAME) or not _record_modes(rd):
            return True
    for path in [p for p, _st in _files(rd / BEFORE_DIRNAME)]:
        if stop():
            return False
        _convert_file(project_id, blobs, path)
    return True


def convert(project_id: str, stop: Callable[[], bool] = lambda: False) -> bool:
    """Look through the project's store once (19.8): every regular file in a run's `before/` that is no blob link yet is
    hashed and moved into the blobs (or dropped when the content has a blob) and linked back; the runs that were undone
    release their backups. Idempotent and safe to interrupt at any point -- a file is replaced atomically, a half-converted
    store undoes as it did -- and it leaves a run that is going alone. Returns False when `stop()` ended it early; the
    marker (`MARKER`) is written only when every run was looked at."""
    from researchos.records import files as record_files  # noqa: PLC0415

    skipped_active = False
    for run in sorted(runs.list_runs(project_id), key=lambda r: r["id"]):
        if stop():
            return False
        rd = runs.run_dir(project_id, run["id"])
        if run.get("state") in runs.ACTIVE_STATES:
            skipped_active = True
            continue
        if run.get("reverted"):
            if holds_backup(rd):
                release(project_id, run["id"], sweep=False)
            continue
        if not _convert_run(project_id, rd, stop):
            return False
    collect(project_id)
    if skipped_active or converted(project_id):
        return True
    marker = runs.project_runs_dir(project_id) / MARKER
    record_files.durable_write(marker, (json.dumps({"version": 1, "converted": runs.now_iso()}) + "\n").encode("utf-8"))
    return True

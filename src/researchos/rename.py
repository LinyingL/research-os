"""The hidden folders take the new name (DESIGN.md 12.8): a project's
`.rce/` becomes `.researchos/`, the machine's `~/.rce` becomes
`~/.researchos`.

Automatic on first touch -- every CLI command, the server, the MCP server
(`ensure_renamed`, called by the identity check of `researchos.project`
and by the entry points before anything else) -- and safe in the 8.10
way:

1. **Refused before anything moves** (`RenameBlocked`, nothing touched):
   a folder that has both names (two candidate truths -- a person
   decides, never this module); an old folder that is not a plain folder
   (a symlink, a file); a file or folder inside it that macOS has evicted
   to iCloud (reading it to verify would block -- the 8.10 cloud sentence
   is given instead, and its download is asked for in the background); an
   engine of the old version still answering on the app's port (it would
   go on writing under the old name).
2. **Under the lock**: the home under the machine lock (an exclusive
   `flock` on the directory that holds the home, since the home itself is
   what moves), a project under its project lock (9.7) -- which lives in
   the home, so the home is always renamed first. The situation is looked
   at again once the lock is held: another process may have renamed it
   while this one waited.
3. **A manifest before**: every entry under the old folder -- directories,
   symlinks (their target) and files (size and sha256) -- and, read the
   way the rest of the code reads them, the project's identity, its
   judgment ledger and every variable card's log (the home: its project
   registry and every index's `home.json`).
4. **One `os.rename`** of the folder within its parent directory: the same
   volume, atomic. Nothing is copied.
5. **Read back and verified**: the same manifest taken again under the new
   name must be identical, entry for entry and byte for byte, and the
   records must read back with the same identity id and the same entries.
6. **Any failure from step 4 on renames the folder back** and raises
   `RenameFailed`: the old folder is exactly as it was. Should even the
   rename back fail, `RenameStuck` says where the folder now is.

The home holds no path that names the home itself (the registry lists
project folders, indexes are keyed by project id, migration sources under
`graphs/` are relative), so renaming it is the whole of its migration; the
verification proves the registry and the indexes' homes read back the same.

A home named by the environment (`RESEARCHOS_HOME`, or the old `RCE_HOME`)
is never renamed: it is where the person said it is.

The steps call `_checkpoint(step, folder)` -- a no-op -- at the points a
failure is injected in the tests ("before_rename", "after_rename").
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import stat as stat_module
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from researchos import names, paths

logger = logging.getLogger(__name__)

_HASH_CHUNK = 1 << 20


class RenameError(Exception):
    """The rename did not happen. `message_zh` is the app's sentence."""

    message_zh = "隐藏文件夹改名没有完成"

    def __init__(self, message: str, message_zh: str | None = None) -> None:
        super().__init__(message)
        if message_zh is not None:
            self.message_zh = message_zh


class RenameBlocked(RenameError):
    """Refused before anything moved; nothing was touched."""


class RenameFailed(RenameError):
    """A step after the rename failed; the folder was renamed back and is
    exactly as it was."""


class RenameStuck(RenameError):
    """A step failed AND renaming back failed: the message says where the
    folder is now. Nothing was deleted."""


@dataclass(frozen=True)
class Renamed:
    """One folder renamed and verified: `entries` entries read back with
    the same bytes."""

    old: Path
    new: Path
    entries: int


def _checkpoint(step: str, folder: Path) -> None:
    """A no-op; the tests replace it to inject a failure at `step` of the
    rename of `folder` (the old name)."""


# -- the dataless check ---------------------------------------------------------------


def _is_dataless(st: os.stat_result) -> bool:
    """`st`'s macOS dataless flag (8.10; `paths.is_dataless` for a path)."""
    if sys.platform != "darwin":
        return False
    return bool(getattr(st, "st_flags", 0) & paths.SF_DATALESS)


# -- the manifest ---------------------------------------------------------------------


def _walk(top: Path, *, skip_dataless: bool = False) -> list[tuple[str, Path, os.stat_result]]:
    """Every entry under `top` (relative POSIX path, path, lstat), sorted;
    directories are descended, symlinks never followed. `skip_dataless`:
    a directory macOS has evicted is listed but not descended (listing it
    would wait for the download)."""
    found: list[tuple[str, Path, os.stat_result]] = []
    pending = [top]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                st = os.lstat(path)
                found.append((path.relative_to(top).as_posix(), path, st))
                if stat_module.S_ISDIR(st.st_mode) and not (skip_dataless and _is_dataless(st)):
                    pending.append(path)
    found.sort(key=lambda item: item[0])
    return found


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def take_manifest(top: Path) -> dict[str, tuple[Any, ...]]:
    """`{relative path: entry}` for everything under `top`: `("dir",)`,
    `("link", target)`, `("file", size, sha256)`, or `("other", type)`."""
    manifest: dict[str, tuple[Any, ...]] = {}
    for rel, path, st in _walk(top):
        mode = st.st_mode
        if stat_module.S_ISDIR(mode):
            manifest[rel] = ("dir",)
        elif stat_module.S_ISLNK(mode):
            manifest[rel] = ("link", os.readlink(path))
        elif stat_module.S_ISREG(mode):
            manifest[rel] = ("file", st.st_size, _sha256(path))
        else:
            manifest[rel] = ("other", stat_module.S_IFMT(mode))
    return manifest


def _refuse_dataless(top: Path) -> None:
    """Before anything is read: a file or folder macOS has evicted to
    iCloud blocks the rename with the 8.10 sentence (its download asked
    for in the background), instead of blocking this thread on it."""
    if _is_dataless(os.lstat(top)):
        evicted = [top]
    else:
        evicted = [path for _rel, path, st in _walk(top, skip_dataless=True) if _is_dataless(st)]
    if not evicted:
        return
    for path in evicted[:20]:
        paths._request_download(path)
    shown = ", ".join(str(p.relative_to(top)) if p != top else p.name for p in evicted[:5]) + (" ..." if len(evicted) > 5 else "")
    raise RenameBlocked(
        f"{top} cannot be renamed to its new name yet: {len(evicted)} file(s) in it are not on this disk "
        f"right now (macOS has evicted them to iCloud: {shown}); their download has been requested. "
        f"Nothing was renamed; it happens on the next touch once they are back.",
        "记录文件正在从云端下载…下载完成后再打开，就会完成隐藏文件夹的改名；现在什么都没有改动。",
    )


# -- what the rest of the code reads ----------------------------------------------------


def _ledger_summary(load: Any) -> tuple[Any, ...]:
    if load.ledger is None:
        return (load.state.value,)
    return (load.state.value, tuple(repr(sorted(entry.data.items(), key=lambda kv: kv[0])) for entry in load.ledger.entries))


def project_records(project_root: Path) -> dict[str, Any]:
    """The project's records as the code reads them -- through
    `paths.project_dir`, so the same call reads the old folder before the
    rename and the new one after: the identity's state and id, the
    judgment ledger's entries, every variable card's log entries."""
    from researchos.records import identity as identity_mod  # noqa: PLC0415 -- records imports paths
    from researchos.records import ledger as ledger_mod  # noqa: PLC0415
    from researchos.records import variables as variables_mod  # noqa: PLC0415

    got = identity_mod.read_identity(project_root)
    read: dict[str, Any] = {
        "identity": (got.state.value, got.identity.id if got.identity is not None else None),
        "judgements": _ledger_summary(ledger_mod.load_judgements(project_root)),
    }
    for directory in variables_mod.card_dirs(project_root) or []:
        log = ledger_mod.load_ledger(directory / variables_mod.LOG_FILENAME, variables_mod.CARD_LOG_SCHEMA)
        read[f"card:{directory.name}"] = _ledger_summary(log)
    # V8 (12.1, 12.6): the notes ledger and the team file, when present.
    from researchos import people  # noqa: PLC0415
    from researchos.records import notes as notes_mod  # noqa: PLC0415

    if notes_mod.notes_path(project_root).exists():
        read["notes"] = _ledger_summary(notes_mod.load_notes(project_root))
    team = people.read_team(project_root)
    if team.state != "absent":
        read["team"] = (team.state, tuple(m.payload()["id"] for m in team.members))
    return read


def home_records(home: Path) -> dict[str, Any]:
    """The home as the code reads it: the registry's entries and each
    index's `home.json`."""
    import json  # noqa: PLC0415

    def _json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "absent"
        except (OSError, ValueError) as exc:
            return f"unreadable: {type(exc).__name__}"

    read: dict[str, Any] = {"registry": _json(home / "projects.json")}
    me = home / "me.toml"
    if me.exists():
        try:
            read["me"] = me.read_bytes()
        except OSError as exc:
            read["me"] = f"unreadable: {type(exc).__name__}"
    graphs = home / paths.GRAPHS_DIRNAME
    if graphs.is_dir():
        for index in sorted(graphs.iterdir()):
            if index.is_dir() and not index.is_symlink():
                read[f"home.json:{index.name}"] = _json(index / "home.json")
    return read


# -- the old engine ---------------------------------------------------------------------


def _refuse_old_engine(what: Path) -> None:
    """An engine of the old version answering on the app's port would go
    on writing under the old names (12.8): quit it first."""
    from researchos import migration  # noqa: PLC0415 -- migration imports project, which imports this

    port = migration.engine_port()
    if port is None or not migration.engine_running():
        return
    answer = migration.ask_engine(port)
    if answer is not None and answer.get("engine") == names.LEGACY_ENGINE_NAME:
        raise RenameBlocked(
            f"{what} was not renamed: an engine of the old version (rce, process {answer.get('pid')}) is "
            f"running on port {port} and would go on writing under the old name. Quit RCE.app or that "
            f"'rce serve' first. Nothing was renamed.",
            "旧版 RCE 的引擎还在运行，它会继续按旧名字写入。请先退出 RCE，再打开 Research OS；现在什么都没有改动。",
        )


# -- one rename -------------------------------------------------------------------------


def _both(old: Path, new: Path) -> RenameBlocked:
    return RenameBlocked(
        f"both {old} and {new} exist: Research OS does not choose between them and touched neither. "
        f"Compare the two, keep the one that is current under the name {new.name}, and move the other "
        f"out of {old.parent}.",
        f"{old.parent} 里同时有 {old.name} 和 {new.name} 两个文件夹。Research OS 不替你选择，两个都没有动；"
        f"请比较后把现在用的那个保留为 {new.name}，把另一个移出这个文件夹。",
    )


def _needs_rename(old: Path, new: Path) -> bool:
    """True when only the old name exists; `_both` when both do."""
    if not os.path.lexists(old):
        return False
    if os.path.lexists(new):
        raise _both(old, new)
    st = os.lstat(old)
    if not stat_module.S_ISDIR(st.st_mode):
        raise RenameBlocked(
            f"{old} is not a folder (a symlink or a file); Research OS neither reads nor renames through it. "
            f"Nothing was renamed.",
            f"{old} 不是普通文件夹（是链接或文件），Research OS 不会通过它读写，也不会改它的名字。",
        )
    return True


def _rename_verified(old: Path, new: Path, read_records) -> Renamed:  # noqa: ANN001
    """Steps 3-6 of the module docstring, the lock already held."""
    try:
        _refuse_dataless(old)
        before = take_manifest(old)
        records_before = read_records()
        _checkpoint("before_rename", old)
    except OSError as exc:
        raise RenameFailed(f"could not read {old} before renaming it ({exc}); nothing was renamed") from exc
    try:
        os.rename(old, new)
    except OSError as exc:
        raise RenameFailed(f"could not rename {old} to {new} ({exc}); nothing was renamed") from exc
    try:
        _checkpoint("after_rename", old)
        after = take_manifest(new)
        if after != before:
            missing = sorted(set(before) - set(after))
            changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
            added = sorted(set(after) - set(before))
            raise RenameFailed(
                f"{new} did not read back the same as {old}: "
                f"{len(missing)} missing, {len(changed)} changed, {len(added)} new "
                f"({', '.join((missing + changed + added)[:5])})"
            )
        records_after = read_records()
        if records_after != records_before:
            differ = sorted(k for k in set(records_before) | set(records_after) if records_before.get(k) != records_after.get(k))
            raise RenameFailed(f"the records under {new} did not read back the same as under {old}: {', '.join(differ)}")
    except BaseException as exc:
        try:
            os.rename(new, old)
        except OSError as back:
            raise RenameStuck(
                f"renaming {old} to {new} failed ({exc}) and renaming it back failed too ({back}): "
                f"the folder is now at {new}. Nothing was deleted; rename it back to {old.name} by hand.",
                f"改名失败，而且没能改回去：文件夹现在在 {new}。什么都没有删除；请手动把它改回 {old.name}。",
            ) from exc
        if isinstance(exc, RenameError):
            exc.args = (f"{exc.args[0]}. Renamed back: {old} is exactly as it was.",)
            raise
        if not isinstance(exc, Exception):
            raise
        raise RenameFailed(
            f"renaming {old} to {new} failed ({type(exc).__name__}: {exc}). Renamed back: {old} is exactly as it was."
        ) from exc
    return Renamed(old, new, len(before))


# -- the home ---------------------------------------------------------------------------

_HOME_THREAD_LOCK = threading.Lock()


@contextlib.contextmanager
def _machine_lock(parent: Path) -> Iterator[None]:
    """The machine lock: an exclusive `flock` on the directory that holds
    the home (the home itself is what moves, so its own lock directory
    cannot be the lock). Creates nothing."""
    import fcntl  # noqa: PLC0415 -- POSIX only, like the project lock

    with _HOME_THREAD_LOCK:
        fd = os.open(parent, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def ensure_home_renamed() -> Renamed | None:
    """`~/.rce` -> `~/.researchos`, once (module docstring). None when there
    is nothing to do: the environment names the home, or there is no old
    home. Raises `RenameError`."""
    if paths.home_override() is not None:
        return None
    old, new = paths.legacy_default_home(), paths.default_home()
    if not _needs_rename(old, new):
        return None
    _refuse_old_engine(old)
    with _machine_lock(old.parent):
        if not _needs_rename(old, new):
            return None  # another process renamed it while this one waited
        done = _rename_verified(old, new, lambda: home_records(new if new.exists() else old))
    logger.warning("Research OS renamed its home folder %s to %s (%d entries, verified)", old, new, done.entries)
    return done


# -- a project --------------------------------------------------------------------------


def project_folders(project_root: str | Path) -> tuple[Path, Path]:
    """(`<project>/.rce`, `<project>/.researchos`)."""
    root = Path(project_root)
    return root / names.LEGACY_PROJECT_DIRNAME, root / names.PROJECT_DIRNAME


def ensure_project_renamed(project_root: str | Path) -> Renamed | None:
    """`<project>/.rce` -> `<project>/.researchos`, once, under the project
    lock (module docstring). None when there is nothing to do. Renames the
    home first (the lock lives there). Raises `RenameError`."""
    from researchos.records import identity as identity_mod  # noqa: PLC0415 -- records imports paths
    from researchos.records.lock import ProjectLockError, project_lock  # noqa: PLC0415

    root = Path(project_root)
    old, new = project_folders(root)
    if not _needs_rename(old, new):
        return None
    ensure_home_renamed()
    _refuse_old_engine(old)
    got = identity_mod.read_identity(root)  # through the old name: the only one there
    project_id = got.identity.id if got.identity is not None else None
    try:
        with project_lock(root, None), (project_lock(root, project_id) if project_id else contextlib.nullcontext()):
            if not _needs_rename(old, new):
                return None
            had_readme = os.path.lexists(old / paths.README_FILENAME)
            done = _rename_verified(old, new, lambda: project_records(root))
    except ProjectLockError as exc:
        raise RenameBlocked(f"{old} was not renamed: the project lock could not be taken ({exc}). Nothing was renamed.") from exc
    logger.warning("Research OS renamed %s to %s (%d entries, verified)", old, new, done.entries)
    if had_readme:
        # Research OS's own signpost names the folder and the index's place;
        # rewritten only after the rename was verified, never part of it.
        try:
            paths.write_project_readme(root)
        except OSError as exc:
            logger.warning("could not rewrite %s (%s)", new / paths.README_FILENAME, exc)
    return done


def ensure_renamed(project_root: str | Path | None = None) -> list[Renamed]:
    """The home, then (when given) the project: what every entry point
    calls first. Returns what was renamed now."""
    done = [ensure_home_renamed()]
    if project_root is not None and Path(project_root).is_dir():
        done.append(ensure_project_renamed(project_root))
    return [d for d in done if d is not None]

"""The before-picture, the after-run comparison and what is protected
(DESIGN.md 17.5, 17.6).

**The before-picture.** Before an agent's program starts, `take` records
every file of the project folder -- size, modification time, a SHA-256 for
files up to `HASH_LIMIT` -- and copies every file up to `COPY_LIMIT` into
the run's folder (`COPY_BUDGET` per run at most). After the run the same
function takes the after-picture and `compare` names what was created,
modified and deleted ("a touched file with the same content is not
changed": two entries with the same hash are the same file whatever their
modification times). The copies are what 撤回本次运行 restores from (17.6).

What `take` never does: follow a symbolic link (a link is recorded by its
target and compared by it), open a file that is only in the cloud (marked
`dataless`; reading it would download it), open anything but a regular file
(a FIFO would block), or walk into `.git/` (but see below),
`.researchos/backups/` (but see below) and the usual caches. Hidden files and
folders are otherwise included on purpose: `.researchos/*.toml` is exactly
what an agent must not touch (17.5 rule 1).

**Two places the main walk skips are still recorded** (18.1 item 4). Git's
`config` and every hook in `.git/hooks/` (the `*.sample` templates, which git
never runs, aside) are entries of `files` like any other, copies included: a
hook planted or a `core.hooksPath` set is a change there, and undo can put it
back. `.researchos/backups/` (and `.rce/backups/`) is listed by name and size,
at any depth, in `picture["backups"]` -- not in `files`, because Research OS
itself adds backups with every save and so a new backup is never a change; a
backup that is gone is (`backup_deleted`), except as many of the oldest of a
series as Research OS's own pruning to the newest 20 can have removed since the
picture (`_Rotation`): none from a series that never held more than 20.
A project with more than `FILE_LIMIT` files is recorded up to that many and
marked `partial`: a partial picture cannot show every creation, and the
caller says so. Editor, Finder and sync-client noise (`SKIP_FILES`:
`.DS_Store`, `~$` and `.~lock.*#` lock files, `.Rhistory` and the like) is
never recorded: it is not research data and nobody did it on purpose.

**Folders are recorded too** (18.1 item 5): `picture["dirs"]` lists every folder the main walk went into, empty
ones included, so that undo can tell a folder that was there before the run from one the run made -- a folder
that only held files the picture does not record (editor noise, a cache) is as much the researcher's as any. A
picture cut at `FILE_LIMIT` files, or past `DIR_LIMIT` folders, marks the list incomplete (`dirs_partial`).

**Data files are walked separately** (`picture["data"]`, 17.5 rule 3): a
second, stat-only walk of the whole tree records every file whose suffix is
in `DATA_SUFFIXES` (less `NOT_DATA_NAMES`: `package.json` and
`package-lock.json`, in any folder, are not data) -- size, modification time,
the `dataless` flag, never opening a file -- not bounded by `FILE_LIMIT` (it stops at `DATA_FILE_LIMIT`,
marked `data_partial`) and descending into every folder except `.git`,
`node_modules`, `.venv`, `venv` and `__pycache__`. A data file in `_cache/`
or past the 50,000th file is therefore still protected. Where the main walk
also saw the file its hash is copied in, so a data file compares by hash
when both entries have one, else by size and modification time.

**The after-picture does not read what the before-picture already read** (18.4 item 2). A file that carries `ino`
in the before-picture, and whose size, modification time (ns) and inode are the same now, keeps the before-picture's
`sha256`; a file whose stamp differs (touched, replaced, grown) is read and hashed as ever. `take` writes `ino` only
for a file it hashed and whose modification time lay at least `RACY_NS` before the picture began: a file changed
within the timestamp's own resolution of being read (a 1 or 2 second stamp on an external drive, a time in the
future) could be written again with the same stamp, so it is never reused. A picture without `ino` (one taken before
this existed) reuses nothing. The result is the picture a full read gives, `ino` included.

**What is protected** (17.5 rules 1, 3, 5), decided from the before-picture:
the folders `.researchos`, `.rce` and `.git`; the agent program's own project
settings (`.claude/` and `.opencode/` at any depth, `opencode.json`: 18.1 item 2,
a later run must not load what an earlier one wrote); every data file that existed
before the run; the attempt table (its path read from `attempts.toml`) and
`research/tasks.json`. `protected` is also what the hook enforces during the
run (`guard`), and `violations` is the after-run check that catches what the
hook cannot see (OpenCode has no hook; a script can overwrite a file behind
Claude Code's back). The folders are protected at any depth, as the hook
reads them: `docs/.researchos/x` is as much out of bounds as `.researchos/x`.
Git's state is its `HEAD` and branch; `.git/` itself is not walked (but for the
two places above); its refs (branches, tags, remote-tracking refs, the stash)
are fingerprinted, so a push or a tag shows although `HEAD` did not move.
"""

from __future__ import annotations

import hashlib
import logging
import os
import fnmatch
import posixpath
import re
import shutil
import stat
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from researchos import names, paths
from researchos.agents import guard

logger = logging.getLogger(__name__)

SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv", ".ipynb_checkpoints",
                       ".Rproj.user", "_cache", ".mypy_cache", ".pytest_cache", ".tox", ".cache"})
SKIP_PATHS = frozenset({f"{names.PROJECT_DIRNAME}/backups", f"{names.LEGACY_PROJECT_DIRNAME}/backups"})
COPY_LIMIT = 16 * 1024 * 1024
COPY_BUDGET = 1024 * 1024 * 1024
#: DESIGN 17.5: a SHA-256 for files up to 64 MB; only the first 16 MB are also copied.
HASH_LIMIT = 64 * 1024 * 1024
FILE_LIMIT = 50_000
#: At most this many folders are recorded in a picture (`dirs`); more marks it `dirs_partial`.
DIR_LIMIT = FILE_LIMIT
#: Rule 3's data suffixes (DESIGN 17.5, 18.1 item 6). This is the one list: the after-run data walk and `protected`
#: read it through `_is_data`, and the hook gets its files from `protected` (via `guard.json`).
DATA_SUFFIXES = frozenset({".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".feather", ".rds", ".rdata", ".dta",
                           ".sav", ".json", ".h5", ".pkl", ".sqlite", ".db", ".npy", ".npz", ".mat", ".sas7bdat",
                           ".jsonl", ".pickle"})
#: Not data by file name, in any folder: package managers write them as a matter of course (`npm install`).
NOT_DATA_NAMES = frozenset({"package.json", "package-lock.json"})
TASKS_PATH = "research/tasks.json"
PROTECTED_DIRS = (names.PROJECT_DIRNAME, names.LEGACY_PROJECT_DIRNAME, ".git", *sorted(guard.AGENT_SETTINGS_DIRS))
#: The data walk skips only environments (not research data); every other folder, `_cache` and
#: `.cache` included, may hold a data file that is protected.
DATA_SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__"})
DATA_FILE_LIMIT = 500_000
#: Git's own files the picture records although `.git/` is not walked (module docstring): `config`, and the
#: files below `hooks/` (at most `GIT_FILES_LIMIT`).
GIT_DIR = ".git"
GIT_CONFIG = f"{GIT_DIR}/config"
GIT_HOOKS = f"{GIT_DIR}/hooks"
GIT_FILES_LIMIT = 1000
#: The backups are listed by name and size (module docstring), at most this many.
BACKUP_FILE_LIMIT = 200_000
#: How many backups of one series Research OS keeps (`records.files.SNAPSHOT_KEEP`, `webapp.mapedit.BACKUP_KEEP`; a test
#: holds the three equal): a save prunes the series to its newest this many, and never removes from a shorter one.
BACKUP_KEEP = 20
#: What Research OS names a backup: `<name>.<UTC stamp>Z[.<n>][.write]<suffix>` (`records.files`, 9.2); the series
#: of a backup is its folder, name, kind (daily or before-a-write) and suffix.
_BACKUP_NAME = re.compile(r"^(?P<name>.+)\.(?P<stamp>\d{8}T\d{12})Z(?:\.(?P<n>\d+))?(?P<write>\.write)?"
                          r"(?P<suffix>\.[^./]*)?$")
#: Names of files that editors, file managers and sync clients create and remove on their own
#: (case-insensitive): never recorded, never a change of the agent's.
SKIP_FILES = frozenset({".ds_store", "thumbs.db", "desktop.ini", "icon\r", ".rhistory"})
_SKIP_FILE_PATTERNS = ("~$*", ".~lock.*#", "~wr?*.tmp")
GIT_TIMEOUT_S = 10
_CHUNK = 1024 * 1024
#: A hash is reused (module docstring) only when the file's modification time is at least this much older than the
#: moment the picture that holds it began: the coarsest stamp a file system keeps is 2 s (FAT), plus a margin.
RACY_NS = 3_000_000_000


# -- git ------------------------------------------------------------------------------------------


def _git(root: Path, *args: str) -> str | None:
    """One git answer (stdout stripped), or None on any failure."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    argv = ["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args]
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=GIT_TIMEOUT_S,
                              env=env, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace").strip() or None


def git_state(root: Path) -> dict[str, str | None] | None:
    """`{"head", "branch", "refs"}` of the project's git repository (`head`
    and `branch` may be None: no commit yet, detached head, git
    unavailable); None when `root/.git` does not exist. `refs` is a SHA-256
    of every ref and the object it points to (`git for-each-ref`, plus the
    stash ref), so a push (the remote-tracking ref moves), a tag, a forced
    branch move or a stash changes it although `HEAD` and the branch name
    did not."""
    root = Path(root)
    if not os.path.lexists(root / ".git"):
        return None
    listing = _git(root, "for-each-ref", "--format=%(refname) %(objectname)")
    stash = _git(root, "rev-parse", "-q", "--verify", "refs/stash")
    refs = hashlib.sha256(f"{listing or ''}\n{stash or ''}\n".encode("utf-8", "replace")).hexdigest()
    return {"head": _git(root, "rev-parse", "HEAD"), "branch": _git(root, "symbolic-ref", "-q", "--short", "HEAD"),
            "refs": refs}


def git_equal(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> bool:
    """Whether two repository states are the same: both absent, or the same
    head, branch and refs fingerprint (`refs` only when both states have
    one, so a state recorded before it existed does not read as a change)."""
    if before is None or after is None:
        return before is None and after is None
    keys = ["head", "branch"] + (["refs"] if "refs" in before and "refs" in after else [])
    return all(before.get(k) == after.get(k) for k in keys)


def skip_file(name: str) -> bool:
    """Whether a file name is editor, Finder or sync-client noise (`SKIP_FILES`,
    `~$` Office and `.~lock.*#` LibreOffice lock files)."""
    low = name.casefold()
    return low in SKIP_FILES or any(fnmatch.fnmatchcase(low, pattern) for pattern in _SKIP_FILE_PATTERNS)


# -- entries ----------------------------------------------------------------------------------------


def _hash_and_copy(full: str, size: int, copy_dest: str | None) -> tuple[str | None, bool, int]:
    """Read `full` once: its SHA-256 (None when unreadable or not hashed),
    whether it was copied to `copy_dest`, and the bytes copied."""
    digest = hashlib.sha256()
    out = None
    copied = False
    try:
        with open(full, "rb") as src:
            if copy_dest is not None:
                os.makedirs(os.path.dirname(copy_dest), exist_ok=True)
                out = open(copy_dest, "wb")  # noqa: SIM115 -- closed in the finally below
            total = 0
            while True:
                chunk = src.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
                if out is not None:
                    if total > COPY_LIMIT:  # grew while being read: not a faithful copy
                        out.close()
                        out = None
                        _unlink_quietly(copy_dest)
                        copy_dest = None
                    else:
                        out.write(chunk)
            if out is not None:
                out.close()
                out = None
                shutil.copystat(full, copy_dest)  # type: ignore[arg-type]
                copied = True
        return digest.hexdigest(), copied, total if copied else 0
    except OSError:
        if out is not None:
            out.close()
        if copy_dest is not None:
            _unlink_quietly(copy_dest)
        return None, False, 0


def _unlink_quietly(path: str | None) -> None:
    if path is not None:
        try:
            os.unlink(path)
        except OSError:
            pass


def _same_stamp(known: Mapping[str, Any] | None, st: os.stat_result) -> bool:
    """Whether the before-picture's entry `known` holds a hash that still describes the file `st` (from `lstat`): it
    has a `sha256` and an `ino`, is no link and no cloud placeholder, and its size, modification time and inode are
    the file's now."""
    return (known is not None and bool(known.get("sha256")) and "link" not in known and "dataless" not in known
            and known.get("ino") == st.st_ino and known.get("size") == st.st_size
            and known.get("mtime_ns") == st.st_mtime_ns)


def _entry(full: str, st: os.stat_result, *, copy_dest: str | None = None,
           budget_left: int = 0, known: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], int]:
    """The recorded entry for one path (from `lstat`) and the bytes copied.
    Links are recorded by target and never followed; a regular file in the
    cloud is marked and never opened. A file is not read again (and no copy
    is made) when `known`, its entry in an earlier picture, has the same
    stamp (`_same_stamp`)."""
    entry: dict[str, Any] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    if stat.S_ISLNK(st.st_mode):
        try:
            entry["link"] = os.readlink(full)
        except OSError:
            entry["link"] = ""
        return entry, 0
    if not stat.S_ISREG(st.st_mode):
        return entry, 0
    if paths.is_dataless(full):
        entry["dataless"] = True
        return entry, 0
    if st.st_size > HASH_LIMIT:
        return entry, 0
    if copy_dest is None and _same_stamp(known, st):
        entry["sha256"] = known["sha256"]  # type: ignore[index]
        return entry, 0
    want_copy = copy_dest is not None and st.st_size <= COPY_LIMIT and st.st_size <= budget_left
    digest, copied, nbytes = _hash_and_copy(full, st.st_size, copy_dest if want_copy else None)
    if digest is not None:
        entry["sha256"] = digest
    if copied:
        entry["copied"] = True
    return entry, nbytes


def fingerprint(root: Path, rel: str) -> dict[str, Any] | None:
    """One file's entry as `take` records it (with a hash when small enough),
    or None when it is missing or `rel` is not a path inside `root`."""
    if not isinstance(rel, str) or not rel or posixpath.isabs(rel) or ".." in rel.split("/"):
        return None
    full = os.path.join(root, *rel.split("/"))
    try:
        st = os.lstat(full)
    except OSError:
        return None
    if stat.S_ISDIR(st.st_mode):
        return None
    return _entry(full, st)[0]


def _data_entry(full: str, st: os.stat_result) -> dict[str, Any]:
    """The stat-only entry of a data file: size, modification time, and
    `link` / `dataless` as they apply. The file is never opened."""
    entry: dict[str, Any] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    if stat.S_ISLNK(st.st_mode):
        try:
            entry["link"] = os.readlink(full)
        except OSError:
            entry["link"] = ""
    elif stat.S_ISREG(st.st_mode) and paths.is_dataless(full):
        entry["dataless"] = True
    return entry


def _is_data(name: str) -> bool:
    """Whether `name` (a bare name or a path) is a data file of rule 3: a suffix of `DATA_SUFFIXES`, unless the file
    name is one of `NOT_DATA_NAMES`."""
    base = posixpath.basename(name).lower()
    return base not in NOT_DATA_NAMES and posixpath.splitext(base)[1] in DATA_SUFFIXES


def _walk_data(base: str) -> tuple[dict[str, dict[str, Any]], bool]:
    """Every data file of the tree under `base` (see the module docstring):
    `({rel: entry}, partial)`. No file is opened; links are never followed."""
    data: dict[str, dict[str, Any]] = {}
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        reldir = os.path.relpath(dirpath, base).replace(os.sep, "/")
        reldir = "" if reldir == "." else reldir
        keep = []
        candidates = list(filenames)
        for name in dirnames:
            if name in DATA_SKIP_DIRS:
                continue
            if os.path.islink(os.path.join(dirpath, name)):
                candidates.append(name)  # a link to a folder is one entry, never walked
            else:
                keep.append(name)
        dirnames[:] = keep
        for name in candidates:
            if not _is_data(name) or skip_file(name):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue  # vanished while walking
            if stat.S_ISDIR(st.st_mode):
                continue
            if len(data) >= DATA_FILE_LIMIT:
                return data, True
            data[f"{reldir}/{name}" if reldir else name] = _data_entry(full, st)
    return data, False


def _git_files(base: str) -> list[tuple[str, str]]:
    """`(rel, full path)` of git's `config` and of every hook in `base/.git/hooks` (the `*.sample` templates
    git never runs left out), sorted, at most `GIT_FILES_LIMIT`; nothing when `.git` is not a real folder (a
    worktree's `.git` is a file that points elsewhere, outside the project)."""
    git = os.path.join(base, GIT_DIR)
    if os.path.islink(git) or not os.path.isdir(git):
        return []
    found: list[tuple[str, str]] = []
    config = os.path.join(git, "config")
    if os.path.lexists(config):
        found.append((GIT_CONFIG, config))
    for dirpath, dirnames, filenames in os.walk(os.path.join(git, "hooks"), followlinks=False):
        reldir = os.path.relpath(dirpath, base).replace(os.sep, "/")
        links = [n for n in dirnames if os.path.islink(os.path.join(dirpath, n))]
        dirnames[:] = sorted(n for n in dirnames if n not in links)
        for name in sorted(filenames + links):
            if name.endswith(".sample") or skip_file(name):
                continue
            if len(found) >= GIT_FILES_LIMIT:
                return found
            found.append((f"{reldir}/{name}", os.path.join(dirpath, name)))
    return found


def _walk_backups(base: str) -> tuple[dict[str, dict[str, int]], bool]:
    """Every file in `.researchos/backups` and `.rce/backups`, at any depth, as `({rel: {"size"}}, partial)`.
    Stat only; links are never followed (a backup folder that is a link is empty as far as the picture goes)."""
    found: dict[str, dict[str, int]] = {}
    for top in (names.PROJECT_DIRNAME, names.LEGACY_PROJECT_DIRNAME):
        folder = os.path.join(base, top, "backups")
        if os.path.islink(folder) or not os.path.isdir(folder):
            continue
        for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
            reldir = os.path.relpath(dirpath, base).replace(os.sep, "/")
            linked = [n for n in dirnames if os.path.islink(os.path.join(dirpath, n))]
            dirnames[:] = [n for n in dirnames if n not in linked]
            for name in filenames + linked:
                if skip_file(name):
                    continue
                try:
                    st = os.lstat(os.path.join(dirpath, name))
                except OSError:
                    continue  # vanished while walking
                if len(found) >= BACKUP_FILE_LIMIT:
                    return found, True
                found[f"{reldir}/{name}"] = {"size": st.st_size}
    return found, False


def take(root: Path, copy_to: Path | None = None, *, reuse: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The before- (or after-) picture of the project folder; see the module
    docstring. Returns `{"files": {rel: entry}, "partial", "copied_bytes",
    "not_copied", "git", "data", "data_partial", "backups", "backups_partial",
    "dirs", "dirs_partial"}` where `dirs` is the sorted list of the folders
    walked and an entry is `{"size", "mtime_ns"}` plus `sha256`, `ino`, `dataless`, `link`
    and `copied` as they apply, `data` is the `{rel: entry}` of the separate
    stat-only walk of data files (no `copied`; `sha256` only where `files` has
    it) and `backups` the `{rel: {"size"}}` of the backups. Copies go to
    `copy_to/<rel>`. `reuse` is an earlier picture of the same folder (the
    before-picture, when this is the after-picture): a file whose entry there
    has the same stamp keeps its hash instead of being read again; the picture
    is the one a full read gives. It is not consulted for a file that is copied."""
    began = time.time_ns()
    root = Path(root)
    base = str(root)
    copy_base = str(copy_to) if copy_to is not None else None
    earlier = _file_map(reuse) if reuse is not None else {}
    files: dict[str, dict[str, Any]] = {}
    dirs: list[str] = []
    dirs_partial = False
    copied_bytes = 0
    not_copied = 0
    partial = False

    def add(rel: str, full: str) -> bool:
        """Record one path; False when the file limit was reached."""
        nonlocal copied_bytes, not_copied
        if len(files) >= FILE_LIMIT:
            return False
        try:
            st = os.lstat(full)
        except OSError:
            return True  # vanished while walking
        dest = os.path.join(copy_base, *rel.split("/")) if copy_base is not None else None
        entry, nbytes = _entry(full, st, copy_dest=dest, budget_left=COPY_BUDGET - copied_bytes,
                               known=earlier.get(rel))
        if entry.get("sha256") and st.st_mtime_ns + RACY_NS <= began:
            entry["ino"] = st.st_ino  # what a later picture's `reuse` needs to trust this hash
        copied_bytes += nbytes
        if stat.S_ISREG(st.st_mode) and not entry.get("copied"):
            not_copied += 1
        files[rel] = entry
        return True

    stopped = False
    for rel, full in _git_files(base):  # first: the walk's limit must not be able to hide a planted hook
        if not add(rel, full):
            partial = stopped = True
            break
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        if stopped:
            break
        reldir = os.path.relpath(dirpath, base).replace(os.sep, "/")
        reldir = "" if reldir == "." else reldir
        keep = []
        links = []
        for name in sorted(dirnames):
            rel = f"{reldir}/{name}" if reldir else name
            if name in SKIP_DIRS or rel in SKIP_PATHS:
                continue
            if os.path.islink(os.path.join(dirpath, name)):
                links.append(name)  # a link to a folder is one entry, never walked
            else:
                keep.append(name)
                if len(dirs) < DIR_LIMIT:
                    dirs.append(rel)
                else:
                    dirs_partial = True
        dirnames[:] = keep
        for name in sorted(filenames + links):
            if skip_file(name):
                continue
            rel = f"{reldir}/{name}" if reldir else name
            if not add(rel, os.path.join(dirpath, name)):
                partial = stopped = True
                break
        if stopped:
            break
    data, data_partial = _walk_data(base)
    for rel, entry in data.items():  # where the main walk saw the file too, its hash and stat are the better record
        seen = files.get(rel)
        if seen is not None:
            data[rel] = {k: v for k, v in seen.items() if k in ("size", "mtime_ns", "sha256", "link", "dataless")}
    backups, backups_partial = _walk_backups(base)
    return {"files": files, "partial": partial, "copied_bytes": copied_bytes, "not_copied": not_copied,
            "git": git_state(root), "data": data, "data_partial": data_partial,
            "backups": backups, "backups_partial": backups_partial,
            "dirs": sorted(dirs), "dirs_partial": dirs_partial}


#: How many of the biggest files `large_files` names.
LARGE_NAMED = 10


def large_files(root: Path, *, over: int | None = None, limit: int | None = None) -> dict[str, Any]:
    """The files of the project bigger than `over` bytes (by default `COPY_LIMIT`, the size up to which
    the before-picture keeps a copy): a run that changes or deletes one of them cannot be undone (17.6), which
    the confirmation before a first run says (18.1 item 5). A stat-only walk by the main walk's rules --
    the same folders and noise left out, no link followed, nothing opened. Returns `{"count", "largest",
    "partial"}`: `largest` is `[{"path", "size"}]`, the biggest first (ties by path), at most `LARGE_NAMED`;
    `partial` is True when the walk stopped after `limit` files (default `FILE_LIMIT`), so `count` is of what it saw."""
    base = str(Path(root))
    over = COPY_LIMIT if over is None else over
    limit = FILE_LIMIT if limit is None else limit
    found: list[tuple[int, str]] = []
    seen = 0
    partial = False
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        reldir = os.path.relpath(dirpath, base).replace(os.sep, "/")
        reldir = "" if reldir == "." else reldir
        dirnames[:] = sorted(n for n in dirnames
                             if n not in SKIP_DIRS and (f"{reldir}/{n}" if reldir else n) not in SKIP_PATHS
                             and not os.path.islink(os.path.join(dirpath, n)))
        for name in sorted(filenames):
            if skip_file(name):
                continue
            if seen >= limit:
                partial = True
                break
            seen += 1
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue  # vanished while walking
            if stat.S_ISREG(st.st_mode) and st.st_size > over:
                found.append((st.st_size, f"{reldir}/{name}" if reldir else name))
        if partial:
            break
    found.sort(key=lambda item: (-item[0], item[1]))
    return {"count": len(found), "largest": [{"path": rel, "size": size} for size, rel in found[:LARGE_NAMED]],
            "partial": partial}


def created_since(root: Path, rels: Iterable[str], started_ns: int) -> list[str]:
    """The paths among `rels` (relative to `root`) that exist and were
    created or changed at or after `started_ns` (their `lstat`
    modification or status-change time): what a partial picture, which
    cannot list creations, may still call created. A path that is missing
    or cannot be statted is dropped."""
    kept = []
    for rel in rels:
        if not isinstance(rel, str) or not rel or posixpath.isabs(rel) or ".." in rel.split("/"):
            continue
        try:
            st = os.lstat(os.path.join(root, *rel.split("/")))
        except OSError:
            continue
        if st.st_mtime_ns >= started_ns or st.st_ctime_ns >= started_ns:
            kept.append(rel)
    return kept


# -- comparing -----------------------------------------------------------------------------------------


def same(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """Whether two entries are the same file: both absent; or the same link
    target; or equal hashes; else equal size and modification time."""
    if a is None or b is None:
        return a is None and b is None
    if "link" in a or "link" in b:
        return "link" in a and "link" in b and a["link"] == b["link"]
    if a.get("sha256") and b.get("sha256"):
        return a["sha256"] == b["sha256"]
    return a.get("size") == b.get("size") and a.get("mtime_ns") == b.get("mtime_ns")


def _file_map(picture: Mapping[str, Any]) -> Mapping[str, Any]:
    """The `{rel: entry}` of a `take` result (a bare map is accepted too)."""
    inner = picture.get("files")
    return inner if isinstance(inner, dict) and "partial" in picture else picture


def compare(before: Mapping[str, Any], after: Mapping[str, Any], *, exclude: Iterable[str] = ()) -> dict[str, list[str]]:
    """`{"created", "modified", "deleted"}` (sorted rel paths) between two
    pictures, ignoring the paths in `exclude`."""
    old, new = _file_map(before), _file_map(after)
    skip = set(exclude)
    created = sorted(r for r in new if r not in old and r not in skip)
    deleted = sorted(r for r in old if r not in new and r not in skip)
    modified = sorted(r for r in new if r in old and r not in skip and not same(old[r], new[r]))
    return {"created": created, "modified": modified, "deleted": deleted}


# -- protection ------------------------------------------------------------------------------------------


def attempts_table(root: Path) -> str | None:
    """The attempt table's path relative to the project, read from
    `attempts.toml`; None when there is no readable config or the file it
    names is outside the project. Never raises."""
    try:
        from researchos.ingest import attempts  # noqa: PLC0415 -- heavy; only the engine needs it

        cfg_path = attempts.config_path(root)
        if paths.is_dataless(cfg_path):
            return None
        name = attempts.load_config(Path(root)).file
        if not isinstance(name, str) or not name.strip() or "\0" in name:
            return None
        candidate = Path(name.strip())
        if candidate.is_absolute():
            try:
                candidate = candidate.resolve().relative_to(Path(root).resolve())
            except (OSError, ValueError):
                return None
        rel = posixpath.normpath(candidate.as_posix())
        if rel in (".", "") or rel == ".." or rel.startswith("../") or posixpath.isabs(rel):
            return None
        return rel
    except Exception:  # noqa: BLE001 -- documented: any trouble reading the config means "unknown"
        return None


def protected(root: Path, before: Mapping[str, Any]) -> dict[str, Any]:
    """What an agent must not change (17.5 rules 1, 3, 5), from the
    before-picture: `{"dirs", "files", "data", "records", "root"}`. `files`
    is `data` plus `records` (the attempt table and `research/tasks.json`,
    listed even if absent: creating them is a write too). `data` is read
    from the picture's separate data walk (`before["data"]`: no file limit,
    skipped folders included) when it has one, else from `files`; a data
    file inside `.researchos`, `.rce` or `.git` is covered as part of that
    folder and not listed again."""
    records = [r for r in (attempts_table(root), TASKS_PATH) if r]
    records = list(dict.fromkeys(records))
    walked = before.get("data") if isinstance(before, Mapping) else None
    source = walked if isinstance(walked, Mapping) else _file_map(before)
    folders = {d.casefold() for d in PROTECTED_DIRS}
    data = sorted(rel for rel in source
                  if _is_data(rel) and rel not in records and rel.split("/", 1)[0].casefold() not in folders)
    return {"dirs": list(PROTECTED_DIRS), "files": sorted({*data, *records}), "data": data,
            "records": records, "root": str(root)}


def is_agent_settings(rel: str) -> bool:
    """Whether `rel` is the agent program's own project setting (18.1 item 2): below a folder named `.claude` or
    `.opencode` at any depth, or an `opencode.json` / `opencode.jsonc` file anywhere."""
    parts = [part.casefold() for part in rel.split("/")]
    return any(part in guard.AGENT_SETTINGS_DIRS for part in parts) or parts[-1] in guard.AGENT_SETTINGS_FILES


def violations(changes: Mapping[str, Any], prot: Mapping[str, Any], *, git_before: Any, git_after: Any,
               allowed: Mapping[str, set[str]], after: Mapping[str, Any] | None = None,
               data_before: Mapping[str, Any] | None = None,
               data_after: Mapping[str, Any] | None = None,
               backups_before: Mapping[str, Any] | None = None,
               backups_after: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    """The out-of-bounds changes among `changes` (a `compare` result), each
    `{"path", "reason"}`: `data_changed` / `data_deleted` (a data file that
    existed before), `record_changed` / `record_deleted` (the attempt table,
    `research/tasks.json`), `researchos_changed` (anything under a folder
    named `.researchos` or `.rce`, at any depth), `agent_settings_changed` (a `.claude` or `.opencode` folder at any
    depth, or an `opencode.json` file: `is_agent_settings`), `git_hooks_changed` (a file
    in `.git/hooks`) and `git_config_changed` (`.git/config`), `git_changed`
    (path `.git`: HEAD, branch or refs differ, or the repository is gone) and
    `backup_deleted` (a file of `.researchos/backups` that is gone, unless it
    is among the oldest of a series that Research OS's own pruning to the
    newest 20 can have removed since the picture: `_Rotation`).

    `allowed` maps a path to the hashes it may legitimately end with, for
    every kind of protected path (records, data files, `.researchos` / `.rce`
    paths) and for created and modified entries: `research/tasks.json` as the
    agent tools wrote it, a file the person saved themselves during the run.
    The hash of a changed path is looked up in `after` (a `take` result)
    when given, else read from the project folder named in `prot["root"]`;
    a path whose hash cannot be determined is a violation.

    `data_before` / `data_after` are the `data` maps of the two pictures
    (`take(...)["data"]`; a bare picture is accepted and its `data` used).
    `changes` only knows what the main walk saw, so the data files beyond
    its file limit or in skipped folders are compared here, by hash when
    both entries have one, else by size and modification time; a deletion is
    reported when the file is really gone from the folder named in
    `prot["root"]` (else when the after-walk was complete, `after["data_partial"]`
    not set). `backups_before` / `backups_after` are the `backups` maps of the
    two pictures (a bare picture is accepted), compared the same way."""
    dirs = {d.casefold() for d in prot.get("dirs", PROTECTED_DIRS) if d != ".git"} - guard.AGENT_SETTINGS_DIRS
    data = set(prot.get("data", ()))
    records = set(prot.get("records", ()))
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(rel: str, reason: str) -> None:
        if (rel, reason) not in seen:
            seen.add((rel, reason))
            found.append({"path": rel, "reason": reason})

    def after_sha(rel: str) -> str | None:
        entry = _file_map(after).get(rel) if after is not None else None
        if (entry is None or not entry.get("sha256")) and prot.get("root"):
            entry = fingerprint(Path(prot["root"]), rel)
        return entry.get("sha256") if entry else None

    def permitted(rel: str) -> bool:
        ok = allowed.get(rel)
        if not ok:
            return False
        sha = after_sha(rel)
        return sha is not None and sha in ok

    for kind in ("created", "modified", "deleted"):
        for rel in changes.get(kind, ()):
            if rel == GIT_CONFIG:
                add(rel, "git_config_changed")
            elif rel.startswith(GIT_HOOKS + "/"):
                add(rel, "git_hooks_changed")
            elif any(part.casefold() in dirs for part in rel.split("/")):
                if kind == "deleted" or not permitted(rel):
                    add(rel, "researchos_changed")
            elif is_agent_settings(rel):
                if kind == "deleted" or not permitted(rel):
                    add(rel, "agent_settings_changed")
            elif rel in records:
                if kind == "deleted":
                    add(rel, "record_deleted")
                elif not permitted(rel):
                    add(rel, "record_changed")
            elif rel in data:
                if kind == "deleted":
                    add(rel, "data_deleted")
                elif kind == "modified" and not permitted(rel):
                    add(rel, "data_changed")

    old_data = _data_map(data_before)
    new_data = _data_map(data_after if data_after is not None else after)
    if old_data and new_data is not None:
        complete = not (isinstance(after, Mapping) and after.get("data_partial"))
        for rel in sorted(data):
            was = old_data.get(rel)
            if was is None:
                continue
            now = new_data.get(rel)
            if now is None:
                root = prot.get("root")
                if not _lexists(root, rel) if root else complete:
                    add(rel, "agent_settings_changed" if is_agent_settings(rel) else "data_deleted")
            elif not same(was, now) and not permitted(rel):  # one reason for a path: a setting is a setting first
                add(rel, "agent_settings_changed" if is_agent_settings(rel) else "data_changed")
    if not git_equal(git_before, git_after):
        add(".git", "git_changed")
    old_backups = _backup_map(backups_before)
    new_backups = _backup_map(backups_after if backups_after is not None else after)
    if old_backups and new_backups is not None:
        complete = not (isinstance(after, Mapping) and after.get("backups_partial"))
        root = prot.get("root")
        gone = [rel for rel in sorted(old_backups) if rel not in new_backups
                and (not _lexists(root, rel) if root else complete)]  # a cut after-walk that did not see it: still there
        rotated = _Rotation(old_backups, new_backups).excused(gone) if gone else set()
        for rel in gone:
            if rel not in rotated:
                add(rel, "backup_deleted")
    return found


def _series(rel: str) -> tuple[tuple[str, str, bool, str], tuple[str, str]] | None:
    """`(series, order)` of a backup path: the series is its folder, file name, kind (before a write or daily) and
    suffix; the order is its stamp and name, which sorts oldest first as `records.files` sorts. None when the name is
    not one Research OS gives a backup."""
    folder, _, name = rel.rpartition("/")
    match = _BACKUP_NAME.match(name)
    if match is None:
        return None
    return (folder, match["name"], bool(match["write"]), match["suffix"] or ""), (match["stamp"], name)


class _Rotation:
    """Research OS keeps the newest `BACKUP_KEEP` backups of each series (`records.files`, `webapp.mapedit`), so a save
    that adds one can remove the oldest -- and only when the series then holds more than that. That is not an agent's
    doing, and a run's own tools save too, in another process nobody here hears. What a rotation can have removed from
    a series is a stretch of its oldest backups no longer than `before + gained - BACKUP_KEEP`; a missing backup is
    excused when it lies in that stretch (older than every backup that is left), and the rest are the agent's. A series
    that never held more than `BACKUP_KEEP` is therefore never rotated: planting one newer name does not excuse the
    deletion of the older ones."""

    def __init__(self, before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
        self.oldest_left: dict[Any, tuple[str, str]] = {}
        self.before_count: Counter[Any] = Counter()
        self.gained: Counter[Any] = Counter()
        for rel in before:
            found = _series(rel)
            if found is not None:
                self.before_count[found[0]] += 1
        for rel in after:
            found = _series(rel)
            if found is None:
                continue
            series, order = found
            if series not in self.oldest_left or order < self.oldest_left[series]:
                self.oldest_left[series] = order
            if rel not in before:
                self.gained[series] += 1

    def excused(self, gone: Iterable[str]) -> set[str]:
        """The paths of `gone` (backups that are missing after the run) that Research OS's own rotation explains."""
        candidates: dict[Any, list[tuple[tuple[str, str], str]]] = {}
        for rel in gone:
            found = _series(rel)
            if found is None:
                continue
            series, order = found
            if series in self.oldest_left and order < self.oldest_left[series]:
                candidates.setdefault(series, []).append((order, rel))
        out: set[str] = set()
        for series, items in candidates.items():
            allowance = self.before_count[series] + self.gained[series] - BACKUP_KEEP
            out.update(rel for _order, rel in sorted(items)[:max(0, allowance)])
        return out


def _data_map(picture: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The `{rel: entry}` data map of a `take` result (or of a bare data map)."""
    if picture is None:
        return None
    inner = picture.get("data")
    return inner if isinstance(inner, Mapping) and "files" in picture else picture


def _backup_map(picture: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The `{rel: {"size"}}` backups map of a `take` result (or the bare map); None when a picture has none
    (one taken before backups were recorded)."""
    if picture is None:
        return None
    if "files" in picture:
        inner = picture.get("backups")
        return inner if isinstance(inner, Mapping) else None
    return picture


def _lexists(root: Any, rel: str) -> bool:
    """Whether `rel` still exists under `root`."""
    return os.path.lexists(os.path.join(root, *rel.split("/")))

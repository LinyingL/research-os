"""Filesystem-walk file inventory (W1) -- the non-git counterpart to
`researchos.ingest.git.list_source_files`, used when a project root is not (or not
yet) a git repository at all.

`list_source_files(root)` returns the same category shape as its git
sibling (tex/bib/image/py/md/r/rmd/do/ado/data), built by walking the filesystem
directly with stdlib `os.walk` (Occam rule 1: no third-party ignore-file
parser) instead of `git ls-files`. Creates no graph nodes, exactly like the
git version.

Because there is no `.gitignore` to consult, this walk skips a fixed set of
noise directories a research project routinely accumulates that a git-
tracked listing would never have surfaced anyway (a repo's own .gitignore
commonly excludes exactly these; see `_NOISE_DIRS` below) -- plus every
dot-prefixed directory outright (a project's own tool caches --
`.mypy_cache`, `.pytest_cache`, `.idea`, ... -- are not enumerable in a
fixed list, so "any directory whose name starts with '.'" is the
deterministic stand-in DESIGN.md section 0 asks for, not a guess). Hidden
files (dot-prefixed filenames) are skipped the same way. No symlink is ever
followed, to a directory or to a file -- `os.walk(..., followlinks=False)`
already keeps the walk from descending into a symlinked directory; a
symlinked *file* is still reported in `os.walk`'s own `filenames` list
regardless of `followlinks` (that flag only governs directory traversal),
so it is filtered out explicitly via `Path.is_symlink()`.

**What the project scan also leaves out** (DESIGN.md 19.7). `iter_files` is shared with the
add-project preview, which looks at a folder that is no project yet, so the skips that belong to a
Research OS project live in `scan_skips`, applied where the scan builds its inventory
(`list_source_files` here and its git sibling): the work folder (process files, `research/folders.json`'s
`work` location, else `WORK_FOLDER`) and the files registered with kind `work` in
`research/results/*.outputs.json`. Those settings are read fail-safe: a file that is missing, only in the
cloud or not JSON means the default work folder and no registered file, never a failed scan.
`registered` also gives the files registered as `document`, which are not paper drafts (mdpaper, citations).
"""

from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterator

from researchos import cloud, names
from researchos.ingest.git import DATA_EXTENSIONS, IMAGE_EXTENSIONS

#: The default of the `work` output location (`outputs.ROLES["work"]`, DESIGN.md 19.2): kept here so that the scan
#: needs not import `outputs`, which imports the workspace, which imports this module.
WORK_FOLDER = "work"
_FOLDERS_FILE = "research/folders.json"
_RESULTS_DIR = "research/results"
_SETTINGS_LIMIT = 4 * 1024 * 1024

# Directories a research project accumulates that are never source material
# in their own right -- version control internals, this tool's own state,
# Python/R tooling caches, and common dependency directories. Every dot-
# prefixed directory is *also* skipped (see list_source_files below), so
# ".git"/".researchos" (and its old spelling, until a folder is
# renamed)/".Rproj.user"/".ipynb_checkpoints" are listed here only for
# readability, not because the dot-prefix rule wouldn't already catch them.
_NOISE_DIRS = frozenset(
    {
        ".git", names.PROJECT_DIRNAME, names.LEGACY_PROJECT_DIRNAME, "__pycache__", ".venv", "node_modules",
        ".Rproj.user", "_cache", ".ipynb_checkpoints",
    }
)

_CATEGORY_BY_SUFFIX: dict[str, str] = {
    ".tex": "tex",
    ".bib": "bib",
    ".py": "py",
    ".md": "md",
    ".r": "r",
    ".rmd": "rmd",
    ".do": "do",
    ".ado": "ado",
}


def _category_for(suffix: str) -> str | None:
    """Which inventory bucket `suffix` (already lowercased) belongs to, or
    None if it matches none of the tracked categories."""
    if suffix in _CATEGORY_BY_SUFFIX:
        return _CATEGORY_BY_SUFFIX[suffix]
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in DATA_EXTENSIONS:
        return "data"
    return None


def iter_files(root: str | Path) -> Iterator[tuple[str, str | None]]:
    """Every file the walk lists under `root`, as `(root-relative posix
    path, category or None)`, in the walk's deterministic order -- noise
    and dot-prefixed directories pruned, hidden files and symlinks skipped
    (module docstring). `list_source_files` keeps the categorized ones;
    the add-project preview (DESIGN.md 10.2, `researchos.addproject`) counts the
    same stream, uncategorized files included, so the numbers it shows are
    the numbers this walk gives the scan. Reads directory listings and
    `lstat` only; never opens a file. A consumer may stop early (closing
    the generator stops the walk)."""
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Prune in place (os.walk respects in-place mutation of dirnames)
        # so a noise/hidden directory is never even descended into, not
        # merely filtered out of the results afterward.
        dirnames[:] = sorted(d for d in dirnames if d not in _NOISE_DIRS and not d.startswith("."))
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            full_path = Path(dirpath) / filename
            if full_path.is_symlink():
                continue
            yield full_path.relative_to(root).as_posix(), _category_for(full_path.suffix.lower())


def fold(rel: str) -> str:
    """The form paths are compared in: NFC, case-folded (a Mac volume is case-insensitive and may store names decomposed)."""
    return unicodedata.normalize("NFC", rel).casefold()


def _json(path: Path) -> Any:
    """A project file's JSON, or None when it is missing, only in the cloud, too large or not JSON."""
    try:
        raw, _size = cloud.read_head(path, _SETTINGS_LIMIT)
        return json.loads(raw)
    except (OSError, ValueError):
        return None


def work_folder(root: str | Path) -> str:
    """The project's work location (DESIGN.md 19.2): `research/folders.json`'s `work`, else `WORK_FOLDER` -- also when
    the file is unreadable or the name is not a plain project-relative folder."""
    value = _json(Path(root) / _FOLDERS_FILE)
    folders = value.get("folders") if isinstance(value, dict) and value.get("version") == 1 else None
    named = folders.get("work") if isinstance(folders, dict) else None
    if isinstance(named, str) and not {"", ".", ".."} & set(named.split("/")):
        return named
    return WORK_FOLDER


def registered(root: str | Path) -> dict[str, frozenset[str]]:
    """The files the tasks' output records name, by kind: `{kind: {project-relative path}}` from
    `research/results/*.outputs.json`. A record that cannot be read is left out (19.7)."""
    directory = Path(root) / _RESULTS_DIR
    try:
        records = sorted(e.name for e in os.scandir(directory) if e.name.endswith(".outputs.json") and e.is_file())
    except OSError:
        return {}
    found: dict[str, set[str]] = {}
    for name in records:
        value = _json(directory / name)
        for item in value.get("items") if isinstance(value, dict) and isinstance(value.get("items"), list) else ():
            if isinstance(item, dict) and isinstance(item.get("kind"), str) and isinstance(item.get("path"), str):
                found.setdefault(item["kind"], set()).add(item["path"])
    return {kind: frozenset(rels) for kind, rels in found.items()}


def scan_skips(root: str | Path) -> Callable[[str], bool]:
    """Whether the project scan leaves a file out (DESIGN.md 19.7; module docstring): it is inside the work folder, or
    registered as `work`. Read once for a whole inventory; `rel` is project-relative with forward slashes."""
    folder = fold(work_folder(root)) + "/"
    work = {fold(rel) for rel in registered(root).get("work", ())}

    def skipped(rel: str) -> bool:
        key = fold(rel)
        return key.startswith(folder) or key in work

    return skipped


def list_source_files(root: str | Path) -> dict[str, list[str]]:
    """Inventory .tex/.bib/image/.py/.md/.r/.rmd/.do/.ado/data files under
    `root` by walking the filesystem, grouped by category -- same shape as
    `researchos.ingest.git.list_source_files`, for a project root that has no git
    repository to ask instead. Creates no graph nodes. The work folder and
    the files registered as `work` are left out (`scan_skips`).

    Returned paths are root-relative and forward-slash-normalized
    (`Path.as_posix()`), matching git's own path convention so downstream
    extractors (latex/pyfig/claims) can treat either inventory identically.
    """
    inventory: dict[str, list[str]] = {
        "tex": [], "bib": [], "image": [], "py": [], "md": [], "r": [], "rmd": [],
        "do": [], "ado": [], "data": [],
    }
    skipped = scan_skips(root)
    for rel, category in iter_files(root):
        if category is not None and not skipped(rel):
            inventory[category].append(rel)
    return inventory

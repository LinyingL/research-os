"""The project folder behind a Stata global: Research OS's proposal (DESIGN.md
15.3, task V11), which a person then confirms or declines
(`researchos.records.stata`).

A global is a candidate when it heads paths -- `"$MyProject/data/auto.csv"`
-- and does not resolve: it is **undefined** (left for each user to set),
or it holds an **absolute path outside the project** (one author's root,
`C:\\Users\\...\\Reproducibility Package`). A global assigned different
values, or a value that is not a literal, is never proposed: the do-files
themselves disagree, and a folder would hide that.

The evidence, all of it deterministic and shown with the proposal:

- **The files.** For every folder D of the project (the project folder
  itself included, written "."), how many of the distinct paths built on
  the global are files under D (the paths after the global, `.dta` default
  applied, as `researchos.ingest.stata` reads them; files as the inventory
  walk lists them, `researchos.ingest.files.iter_files`).
- **The value** (an absolute path only): its last folders name a project
  folder when they equal that folder's path in the project -- or the
  project folder's own name followed by it (`.../rio-safe-space/
  Reproducibility Package` names `Reproducibility Package/` of a project
  folder named `rio-safe-space`).

**When a proposal is made -- the thresholds, and why:**

- an undefined global: the folder holding the most of its paths, when that
  folder holds **at least two** of them (one file proves little: a common
  name can sit anywhere) and **at least half** of them (a folder that holds
  only a few of the files the do-files name is not where they point; half
  rather than all, because a package often leaves out the files its
  do-files write), and **no other folder holds as many** (two folders tie:
  no proposal -- a copy of the project inside itself is the usual cause);
- an absolute path: the folder its last folders name, taking the longest
  such tail, when **no other folder is named by a tail as long** (a tie:
  no proposal) and **no other folder holds more of its paths** than that
  one (the files would say otherwise: no proposal). Its value is the
  evidence; the count of its paths found there is shown beside it.

Nothing is proposed for a global a person has answered with 「是」: its
paths resolve. One answered 「不是」 is proposed again, with its answer
shown, so the answer can be changed.

**This computer's project path** (`LOCAL_ROOT`, a project with members
only): a global whose own value is an absolute path inside this project
-- the PI's do-file hard-codes their own folder -- resolves here, so it is
never unresolved on this computer; but in a member's working copy, which
sits elsewhere, the same value lies outside the project. Its folder is
what a member's copy needs, and only the PI may answer, so the PI is
asked: the folder is the value relative to this project folder (a
deterministic fact of the file system, "." for the project folder
itself), proposed when no other folder holds more of its paths. A single
researcher is never asked (nothing would change for them).
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from researchos.ingest import files as files_ingest
from researchos.ingest import stata as stata_ingest

#: The reasons a path is listed as 「展开不了」 (15.3): a macro in it did
#: not resolve. Command-level skips (a tempfile, an inline if, a table for
#: the outputs layer) are not unresolved paths.
UNRESOLVED_REASONS = (stata_ingest.UNDEFINED, stata_ingest.OUTSIDE, stata_ingest.REASSIGNED, stata_ingest.MACRO,
                      stata_ingest.LOOP, stata_ingest.CD_NOT_FOLLOWED, stata_ingest.WORKING_FOLDER,
                      stata_ingest.TWO_FOLDERS, stata_ingest.CYCLE)
_HEAD_REASONS = frozenset({stata_ingest.UNDEFINED, stata_ingest.OUTSIDE})
#: A global holding this computer's project path (module docstring).
LOCAL_ROOT = "this computer's project path"
_DRIVE_RE = re.compile(r"^(?:[A-Za-z]:|//[^/]*)")


@dataclass(frozen=True)
class HeadGlobal:
    """A global heading paths that do not resolve, with what was read."""

    name: str  # without `$`
    reason: str  # UNDEFINED or OUTSIDE
    value: str  # its own value, for OUTSIDE; "" otherwise
    paths: tuple[str, ...]  # the distinct paths after it, sorted
    uses: int  # how many commands use it so


@dataclass(frozen=True)
class Proposal:
    name: str
    folder: str  # "." is the project folder itself
    reason: str
    value: str  # the absolute path, for OUTSIDE
    named: str  # the value's last folders that name the folder ("" for an undefined global)
    paths: int  # the distinct paths built on the global
    found: int  # how many of them are files under `folder`


def head_globals(scans: Mapping[str, stata_ingest.DoScan | None], *, team: bool = False) -> dict[str, HeadGlobal]:
    """Every global that heads a path a do-file could not resolve because
    the global itself is undefined or outside the project -- written out
    (`"$root/data/x"`) or through the working folder (`cd "$root"`, then
    `"data/x"`) -- and, in a project with members (`team`), every global
    holding this computer's project path (`LOCAL_ROOT`)."""
    found: dict[str, dict] = {}
    for scan in scans.values():
        for name, value, rest in (scan.local_roots if team and scan is not None else ()):
            g = found.setdefault(name, {"reason": LOCAL_ROOT, "value": value, "paths": set(), "uses": 0})
            if g["reason"] == LOCAL_ROOT and rest:
                g["paths"].add(rest)
                g["uses"] += 1
    for scan in scans.values():
        for s in (scan.skipped if scan is not None else ()):
            reason = s.macro_reason or s.reason
            if reason not in _HEAD_REASONS or not s.macro.startswith("$") or not s.rest:
                continue
            g = found.setdefault(s.macro[1:], {"reason": reason, "value": s.value, "paths": set(), "uses": 0})
            if g["reason"] == LOCAL_ROOT:
                continue
            g["paths"].add(s.rest)
            g["uses"] += 1
    return {name: HeadGlobal(name, g["reason"], g["value"], tuple(sorted(g["paths"])), g["uses"])
            for name, g in sorted(found.items())}


def project_files(root: str | Path) -> set[str]:
    """The project's files as the inventory walk lists them."""
    return {rel for rel, _category in files_ingest.iter_files(root)}


def _folders(files: Iterable[str]) -> set[str]:
    out = {"."}
    for rel in files:
        parts = rel.split("/")[:-1]
        out.update("/".join(parts[:k]) for k in range(1, len(parts) + 1))
    return out


def _counts(paths: Iterable[str], files: set[str]) -> dict[str, int]:
    """folder -> how many of `paths` are files under it."""
    counts: dict[str, int] = {}
    by_name: dict[str, list[str]] = {}
    for rel in files:
        by_name.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)
    for path in paths:
        tail = posixpath.normpath(path)
        if tail.startswith("../") or tail == ".." or tail.startswith("/"):
            continue
        for rel in by_name.get(tail.rsplit("/", 1)[-1], ()):
            if rel == tail:
                counts["."] = counts.get(".", 0) + 1
            elif rel.endswith("/" + tail):
                folder = rel[:-len(tail) - 1]
                counts[folder] = counts.get(folder, 0) + 1
    return counts


def _named(value: str, folders: set[str], root_name: str) -> list[tuple[int, str, str]]:
    """(how many of the value's last folders match, the folder, those
    folders as written) for every project folder an absolute value's tail
    names (module docstring)."""
    parts = [p for p in _DRIVE_RE.sub("", value).split("/") if p]
    out = []
    for m in range(1, len(parts) + 1):
        tail = parts[-m:]
        if "/".join(tail) in folders:
            out.append((m, "/".join(tail), "/".join(tail)))
        if tail[0] == root_name:
            inner = "/".join(tail[1:]) or "."
            if inner in folders:
                out.append((m, inner, "/".join(tail)))
    return out


def propose(root: str | Path, heads: Mapping[str, HeadGlobal], files: set[str] | None = None) -> dict[str, Proposal]:
    """The unambiguous proposals for `heads` (module docstring); a global
    with no proposal is left out."""
    root = Path(root)
    if not heads:
        return {}
    files = project_files(root) if files is None else files
    folders = _folders(files)
    out: dict[str, Proposal] = {}
    for name, g in heads.items():
        counts = _counts(g.paths, files)
        n = len(g.paths)
        if g.reason == LOCAL_ROOT:
            folder = stata_ingest._inside(root, g.value)
            here = counts.get(folder or "", 0)
            if folder is None or any(c > here for f, c in counts.items() if f != folder):
                continue
            out[name] = Proposal(name, folder or ".", g.reason, g.value, "", n, here)
            continue
        if g.reason == stata_ingest.UNDEFINED:
            if not counts:
                continue
            best = max(counts.values())
            leaders = [f for f, c in counts.items() if c == best]
            if len(leaders) != 1 or best < 2 or best * 2 < n:
                continue
            out[name] = Proposal(name, leaders[0], g.reason, "", "", n, best)
            continue
        named = _named(g.value, folders, root.name)
        if not named:
            continue
        longest = max(m for m, _f, _t in named)
        leaders = {(f, t) for m, f, t in named if m == longest}
        if len({f for f, _t in leaders}) != 1:
            continue
        folder, tail = sorted(leaders)[0]
        here = counts.get(folder, 0)
        if any(c > here for f, c in counts.items() if f != folder):
            continue
        out[name] = Proposal(name, folder, g.reason, g.value, tail, n, here)
    return out


def unresolved(scans: Mapping[str, stata_ingest.DoScan | None]) -> dict[str, list[dict]]:
    """The paths 「展开不了」, by reason (`UNRESOLVED_REASONS`, in that
    order; a reason with none is left out): each with its do-file, line,
    command, the path as written and the macro at fault."""
    out: dict[str, list[dict]] = {reason: [] for reason in UNRESOLVED_REASONS}
    for rel in sorted(scans):
        scan = scans[rel]
        for s in (scan.skipped if scan is not None else ()):
            if s.reason in out:
                out[s.reason].append({"file": rel, "line": s.line, "command": s.command, "text": s.text,
                                      "macro": s.macro})
    return {reason: items for reason, items in out.items() if items}

"""Claude Code's PreToolUse guard: what an agent may not do, decided before
the call (DESIGN.md 17.5).

The engine writes `guard.json` into the run's folder before the program
starts (`write_guard_file`): the project's real path, the protected folders
and files (`snapshot.protected`) and the engine's port. The hook
(`researchos.agents.hook`, one short process per tool call) loads it and
asks `check_tool`, which answers a reason (one English sentence addressed to
the agent, saying what to do instead) or None.

**File tools** (Edit, Write, MultiEdit, NotebookEdit) are checked on the
path as the filesystem will see it: relative paths resolve against the
call's working directory, `~` expands, symbolic links are resolved, so a
link inside the project cannot lead out of it or onto a protected file. A
path outside the project, inside `.researchos` / `.rce` / `.git` (at any
depth: a nested repository's hooks run code), or naming a protected file is
refused; a call without a path is refused. Comparison is by NFC-normalised,
case-folded names (macOS is case-insensitive and may store names decomposed).

**Bash** is checked as text: it refuses a command that runs `researchos` or
`rce`, changes git's state, names the engine's address or its API paths,
names `.researchos` / `.rce` (and `.git` together with a writing operation),
or names a protected file together with a writing operation, and a
`cp`/`install`/`rsync`/`ln` whose destination is a protected file. This is
a barrier against mistakes and the obvious, not a sandbox: a program the
agent runs can write anything its user can, and the after-run check
(`snapshot.violations`) is what shows that (17.5).

Everything here is stdlib only and fast: the hook runs before every call.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from researchos.agents import runs

FILE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
GUARD_FILENAME = "guard.json"
REFUSALS_FILENAME = "refusals.jsonl"
_TARGET_CAP = 500

#: Subcommands that change git's state (17.5 rule 4); `pull` is fetch plus merge.
GIT_MUTATING = frozenset({"push", "commit", "reset", "rebase", "merge", "checkout", "switch", "restore", "stash",
                          "clean", "tag", "cherry-pick", "revert", "am", "filter-branch", "update-ref", "replace",
                          "pull"})
#: git's own options that take their value as the next word.
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                                "--super-prefix", "--config-env"})
_GIT_WORD_RE = re.compile(r"\bgit\b", re.I)
_SEGMENT_END_RE = re.compile(r"[;&|\n`)]")
_BRANCH_EDIT_RE = re.compile(r"^-[A-Za-z]*[dDmM]|^--(delete|move)\b")

_PROGRAM_RE = re.compile(r"""(^|[\s;&|(`$/'"])(researchos|rce)(\s|$|;|&|\||'|"|\)|`)""", re.I)
_PROGRAM_MODULE_RE = re.compile(r"-m\s+researchos\b", re.I)
_API_RE = re.compile(r"/api/(proposals|share|judgements|attempts|workspace|edges|mappings|records|github|"
                     r"shutdown|settings|notes|papers|variables|projects|stata|agents)\b", re.I)
_RESEARCHOS_DIR_RE = re.compile(r"""(^|[\s'"/=])\.(researchos|rce)(/|\s|$|['"])""", re.I)
_GIT_DIR_RE = re.compile(r"""(^|[\s'"/=])\.git(/|\s|$|['"])""", re.I)
#: Writing operations other than a redirect: any of them anywhere in a command that names a protected file.
_WRITE_OP_RE = re.compile(r"(\brm\b|\bmv\b|\bsed\b[^;&|\n]*?\s-(?:[A-Za-z]*i|-in-place)|\btruncate\b|\btee\b|\bdd\b"
                          r"|\bperl\b[^;&|\n]*?\s-[A-Za-z]*i)", re.I)
#: A redirect (`>`, `>>`, `>|`, `&>`) and the word it writes to.
_REDIRECT_RE = re.compile(r""">[>|]?\s*("[^"]*"|'[^']*'|\S*)""")
#: Redirections that write nowhere (`2>&1`, `>/dev/null`) are not writing operations.
_HARMLESS_REDIRECT_RE = re.compile(r"\d*>&\d+|&?\d*>>?\s*/dev/(?:null|stderr|stdout)\b")
_WORD_RE = re.compile(r"[\w.\-]+")
_PLAIN_NAME_RE = re.compile(r"[\w.\-]+\Z")
_COPY_PROGRAMS = frozenset({"cp", "install", "rsync", "ln", "ditto"})


def _key(name: str) -> str:
    """The form names are compared in: NFC, case-folded."""
    return unicodedata.normalize("NFC", name).casefold()


# -- the guard file --------------------------------------------------------------------------------


def write_guard_file(run_dir: Path, *, root: Path, prot: dict[str, Any], engine_port: int | None) -> None:
    """Write `guard.json` (atomically: the hook may read it at any moment)."""
    guard = {"root": os.path.realpath(root), "dirs": list(prot.get("dirs", ())), "files": list(prot.get("files", ())),
             "engine_port": engine_port}
    data = (json.dumps(guard, ensure_ascii=False) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".guard.", suffix=".tmp", dir=run_dir)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, Path(run_dir) / GUARD_FILENAME)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_guard(run_dir: Path) -> dict[str, Any]:
    """The run's guard; ValueError (or OSError) when it is missing or not
    the shape `write_guard_file` writes -- the hook then refuses everything."""
    guard = json.loads((Path(run_dir) / GUARD_FILENAME).read_bytes().decode("utf-8"))
    if not isinstance(guard, dict) or not isinstance(guard.get("root"), str) or not guard["root"]:
        raise ValueError("guard.json has no project root")
    for key in ("dirs", "files"):
        if not isinstance(guard.get(key), list) or not all(isinstance(x, str) for x in guard[key]):
            raise ValueError(f"guard.json {key} is not a list of names")
    port = guard.get("engine_port")
    if port is not None and (isinstance(port, bool) or not isinstance(port, int)):
        raise ValueError("guard.json engine_port is not a number")
    return guard


# -- file tools ---------------------------------------------------------------------------------------


def _check_file_tool(guard: dict[str, Any], tool_name: str, tool_input: dict[str, Any], cwd: str | None) -> str | None:
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(target, str) or not target.strip() or "\0" in target:
        return f"{tool_name} was called without a usable file path, so Research OS cannot tell where it would write; give the full path of a file inside the project."
    root = os.path.realpath(guard["root"])
    path = os.path.expanduser(target)
    if not os.path.isabs(path):
        path = os.path.join(cwd if isinstance(cwd, str) and cwd else root, path)
    real = os.path.realpath(path)
    rel = os.path.relpath(real, root)
    if rel == ".":
        return (f"{target} is the project folder itself, not a file in it; give the path of a file inside "
                f"the project.")
    if rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel):
        return (f"{target} is outside the project folder, and an agent may only change files inside the project; "
                f"write to a new file inside {root} instead.")
    parts = rel.split(os.sep)
    protected_dirs = {_key(d) for d in guard["dirs"]}
    for part in parts:
        if _key(part) in protected_dirs:
            return (f"{rel} is inside {part}, which belongs to the person and to Research OS and is off limits to "
                    f"an agent; do not touch it, and put your work in ordinary project files instead.")
    posix = "/".join(parts)
    if _key(posix) in {_key(f) for f in guard["files"]}:
        return (f"{posix} is a protected file (a data file that existed before this run, the attempt table, or "
                f"research/tasks.json) and cannot be changed by an agent; write the result to a new file instead, "
                f"and use the researchos_* tools to report on the task.")
    return None


# -- Bash ---------------------------------------------------------------------------------------------------


def _git_mutation(command: str) -> bool:
    """Whether some `git` in `command` runs a subcommand that changes git's
    state. Each `git` word is followed to the end of its simple command,
    git's own options (and their values) skipped, and the subcommand read."""
    for match in _GIT_WORD_RE.finditer(command):
        rest = command[match.end():]
        end = _SEGMENT_END_RE.search(rest)
        words = [w.strip("'\"") for w in (rest[:end.start()] if end else rest).split()]
        index = 0
        while index < len(words) and words[index].startswith("-"):
            index += 2 if words[index] in _GIT_VALUE_OPTIONS else 1
        if index >= len(words):
            continue
        sub = words[index]
        if sub in GIT_MUTATING:
            return True
        if sub == "branch" and any(_BRANCH_EDIT_RE.match(w) for w in words[index + 1:]):
            return True
    return False


def _split_words(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _copy_into(command: str, names: set[str]) -> bool:
    """Whether a cp / install / rsync / ln / ditto in `command` has a protected
    file as its destination (its last non-option word)."""
    for segment in re.split(r"[;&|\n]+", command):
        words = _split_words(segment)
        while words and (re.match(r"[A-Za-z_]\w*=", words[0]) or words[0] in ("sudo", "command", "exec", "env")):
            words.pop(0)
        if not words or os.path.basename(words[0]) not in _COPY_PROGRAMS:
            continue
        operands = [w for w in words[1:] if not w.startswith("-")]
        if len(operands) >= 2 and _key(operands[-1].rstrip("/").rsplit("/", 1)[-1]) in names:
            return True
    return False


def _names_protected_file(command: str, basenames: set[str]) -> bool:
    """Whether `command` names a protected file by its base name (a whole
    word: not preceded or followed by a letter, digit, `_`, `.` or `-`)."""
    text = unicodedata.normalize("NFC", command).casefold()
    plain = {b for b in basenames if _PLAIN_NAME_RE.match(b)}
    if plain and any(token in plain for token in _WORD_RE.findall(text)):
        return True
    return any(re.search(r"(?<![\w.\-])" + re.escape(b) + r"(?![\w.\-])", text) for b in basenames - plain)


def _writes_protected(command: str, targets: list[str], basenames: set[str]) -> bool:
    """Whether `command` writes to a protected file: it names one together
    with a writing operation (rm, mv, sed -i, truncate, tee, dd, perl -i), or
    redirects output to one. A redirect writes only to its target, so a
    command that merely reads a protected file and redirects elsewhere
    (`python fit.py raw.csv > fit.log`) is not a write; a target that is not
    plain text (`> $OUT`, a command substitution) cannot be judged, so the
    command is then treated as writing whenever it names a protected file."""
    if not _names_protected_file(command, basenames):
        return False
    if _WRITE_OP_RE.search(command):
        return True
    for target in targets:
        if re.search(r"[$`(]", target) or not target:
            return True
        if _names_protected_file(target.strip("'\""), basenames):
            return True
    return False


def _check_bash(guard: dict[str, Any], command: str) -> str | None:
    if _PROGRAM_RE.search(command) or _PROGRAM_MODULE_RE.search(command):
        return ("Research OS's own command (researchos, rce) is not available to an agent; use the researchos_* "
                "tools you were given to report on the task.")
    if _git_mutation(command):
        return ("Changing git's state (push, commit, reset, checkout, rebase, merge, stash, clean, tag and the like) "
                "is not allowed in this run; leave git as it is, the person will commit what they want to keep.")
    port = guard.get("engine_port")
    if (port and re.search(rf"(127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\]):{int(port)}\b", command, re.I)) \
            or _API_RE.search(command):
        return ("Research OS's own programme interface is not available to an agent; use the researchos_* tools "
                "you were given instead.")
    if _RESEARCHOS_DIR_RE.search(command):
        return (".researchos (and .rce) belong to the person and to Research OS and are off limits to an agent; "
                "do not read or write there, and keep your work in ordinary project files.")
    command_nr = _HARMLESS_REDIRECT_RE.sub(" ", command)
    targets = _REDIRECT_RE.findall(command_nr)
    if _GIT_DIR_RE.search(command) and (targets or _WRITE_OP_RE.search(command_nr)):
        return "Writing inside .git is not allowed in this run; leave git's files alone."
    basenames = {_key(f.rsplit("/", 1)[-1]) for f in guard["files"]}
    if basenames and (_writes_protected(command_nr, targets, basenames) or _copy_into(command, basenames)):
        return ("This command would write to a protected file (a data file that existed before this run, the "
                "attempt table, or research/tasks.json); write the result to a new file instead and leave the "
                "original as it is.")
    return None


def check_tool(guard: dict[str, Any], tool_name: str, tool_input: dict[str, Any], cwd: str | None = None) -> str | None:
    """Why this tool call must be refused, or None when it may go ahead."""
    if tool_name in FILE_TOOLS or tool_name == "Bash":
        if not isinstance(tool_input, dict):
            return f"{tool_name} was called without arguments Research OS can read, so it is refused."
    if tool_name in FILE_TOOLS:
        return _check_file_tool(guard, tool_name, tool_input, cwd)
    if tool_name == "Bash":
        command = tool_input.get("command")
        if command is None:
            return None
        if not isinstance(command, str):
            return "The Bash command was not text, so Research OS cannot check it and refuses it."
        return _check_bash(guard, command)
    return None


def log_refusal(run_dir: Path, tool_name: str, target: str, reason: str) -> None:
    """Append one refusal to the run's `refusals.jsonl` (the run log shows it)."""
    runs.append_line(Path(run_dir) / REFUSALS_FILENAME, {
        "at": runs.now_iso(), "tool": str(tool_name)[:100], "target": str(target)[:_TARGET_CAP], "reason": reason})

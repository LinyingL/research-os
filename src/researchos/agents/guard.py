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
`cp`/`install`/`rsync`/`ln` whose destination is a protected file. On top of
that text check, `rm`, `mv`, `cp`, `install`, `rsync`, `ln`, `ditto`,
`find -delete` / `-exec rm`, `unzip`, `tar -x`, `curl -o` / `-O`, `wget -O` /
`-P`, `git rm` / `git mv` and `-t DIR` are read **path-aware**: their path
words are resolved (against the call's working directory, which `cd` in the
same command moves; `~` expanded; a glob by its literal folder prefix) and
the command is refused when it removes, moves or overwrites a protected file
or a folder that holds one (`rm -rf data`, `mv research x`), or copies,
moves, extracts or downloads into a folder that already holds a protected
file of the same name (`cp /tmp/raw.csv data/`, `unzip -o d.zip -d data`).
Reading commands (`cat`, `head`, `python x.py data.csv`, `cp data/raw.csv
new.csv`) stay allowed. This is a barrier against mistakes and the obvious,
not a sandbox: a program the agent runs can write anything its user can, and
the after-run check (`snapshot.violations`) is what shows that (17.5).

Everything here is stdlib only and fast: the hook runs before every call.
"""

from __future__ import annotations

import fnmatch
import glob as _glob
import json
import os
import posixpath
import re
import shlex
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable

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
#: Research OS's API paths, refused only on the engine's own address (a loopback host, with or
#: without a port, or the engine's port): `https://zenodo.org/api/records/...` is ordinary data.
_API_RE = re.compile(r"(?<![\w.\-])(?:127(?:\.\d{1,3}){3}|localhost|0\.0\.0\.0|\[::1\])(?::[^/\s]*)?"
                     r"/api/(proposals|share|judgements|attempts|workspace|edges|mappings|records|github|"
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
    state. Each simple command is split into words once (a quoted value
    with spaces stays one word); after every word that names `git`, git's
    own options (and their values) are skipped and the subcommand read."""
    for segment in _segments(command, ";&|\n`)"):
        if not _GIT_WORD_RE.search(segment):
            continue
        words = _split_words(segment)
        for position, word in enumerate(words):
            match = _GIT_WORD_RE.search(word)
            if match is None:
                continue
            rest = _split_words(word[match.end():]) + words[position + 1:]  # the word may be a whole quoted script
            index = 0
            while index < len(rest) and rest[index].startswith("-"):
                index += 2 if rest[index] in _GIT_VALUE_OPTIONS else 1
            if index >= len(rest):
                continue
            sub = rest[index]
            if sub in GIT_MUTATING:
                return True
            if sub == "branch" and any(_BRANCH_EDIT_RE.match(w) for w in rest[index + 1:]):
                return True
    return False


def _segments(command: str, separators: str) -> list[str]:
    """`command` cut at the characters of `separators` that are neither quoted
    nor escaped: the simple commands (and, with `)` and a backtick, the
    substitutions) it is made of."""
    out: list[str] = []
    start, quote, index, size = 0, "", 0, len(command)
    while index < size:
        char = command[index]
        if char == "\\" and quote != "'":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char in separators:
            out.append(command[start:index])
            start = index + 1
        index += 1
    out.append(command[start:])
    return out


def _split_words(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return [w.strip("'\"") for w in segment.split()]


def _copy_into(command: str, names: set[str]) -> bool:
    """Whether a cp / install / rsync / ln / ditto in `command` has a protected
    file as its destination (its last non-option word; with `-t DIR`, `DIR`)."""
    for segment in re.split(r"[;&|\n]+", command):
        words = _split_words(segment)
        while words and (re.match(r"[A-Za-z_]\w*=", words[0]) or words[0] in ("sudo", "command", "exec", "env")):
            words.pop(0)
        if not words or os.path.basename(words[0]) not in _COPY_PROGRAMS:
            continue
        _flags, values, operands = _split_args(words[1:], _COPY_VALUE_SHORT[os.path.basename(words[0])],
                                               _COPY_VALUE_LONG)
        target = values.get("-t") or values.get("--target-directory")
        if target is not None:
            if _key(target.rstrip("/").rsplit("/", 1)[-1]) in names:
                return True
        elif len(operands) >= 2 and _key(operands[-1].rstrip("/").rsplit("/", 1)[-1]) in names:
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


# -- Bash, path-aware ------------------------------------------------------------------------------------

_GLOB_RE = re.compile(r"[*?\[]")
#: Commands the path-aware check looks at; a command naming none of them is not examined further.
_PATH_PROGRAMS_RE = re.compile(
    r"\b(rm|unlink|shred|mv|cp|install|rsync|ln|ditto|find|unzip|tar|curl|wget|git|cd|pushd|sh|bash|zsh|dash|ksh|eval)\b")
_PREFIX_PROGRAMS = frozenset({"sudo", "doas", "command", "exec", "env", "nohup", "time", "nice", "builtin"})
#: Options of a prefix program that take a value (`sudo -u root rm x`).
_PREFIX_VALUE_OPTIONS = {"sudo": frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U"}),
                         "doas": frozenset({"-u", "-C"}), "env": frozenset({"-u", "-C"})}
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_REMOVERS = frozenset({"rm", "unlink", "shred"})
_COPIERS = frozenset({"cp", "install", "rsync", "ln", "ditto"})
#: Value-taking options of the copy/move commands: short letters, and long names that take a
#: separate value (`--name=value` always works).
_COPY_VALUE_SHORT = {"cp": "tS", "mv": "tS", "ln": "tS", "install": "tSmog", "rsync": "efB", "ditto": ""}
_COPY_VALUE_LONG = frozenset({"--target-directory", "--suffix", "--mode", "--owner", "--group", "--exclude", "--include",
                              "--filter", "--rsh", "--files-from", "--exclude-from", "--include-from", "--compare-dest",
                              "--copy-dest", "--link-dest", "--backup-dir", "--rsync-path", "--port", "--timeout",
                              "--bwlimit", "--chmod", "--chown"})
_ARCHIVE_SIZE_CAP = 64 * 1024 * 1024
_MEMBER_CAP = 100_000
_WALK_CAP = 20_000
_GLOB_CAP = 200

_PROTECTED_MSG = ("This command would write to, delete or move a protected file (a data file that existed before "
                  "this run, the attempt table, or research/tasks.json), or a folder that holds one; write the "
                  "result to a new file or a new folder instead and leave the original as it is.")


class _Refuse(Exception):
    """Raised inside the path-aware check as soon as one command is found refusable."""


def _split_args(words: list[str], value_short: str = "", value_long: frozenset[str] = frozenset()
                ) -> tuple[list[str], dict[str, str], list[str]]:
    """Split a command's words into `(flags, values, operands)`: a flag is an
    option word, `values` maps an option that takes a value (`value_short`
    letters, `value_long` names, anything given as `--name=value`) to it, and
    everything after `--` or not starting with `-` is an operand."""
    flags: list[str] = []
    values: dict[str, str] = {}
    operands: list[str] = []
    index, count = 0, len(words)
    while index < count:
        word = words[index]
        index += 1
        if word == "--":
            operands.extend(words[index:])
            break
        if word.startswith("--"):
            name, eq, value = word.partition("=")
            flags.append(name)
            if eq:
                values[name] = value
            elif name in value_long and index < count:
                values[name] = words[index]
                index += 1
        elif word.startswith("-") and len(word) > 1:
            for position, letter in enumerate(word[1:], 1):
                if letter in value_short:
                    rest = word[position + 1:]
                    if rest:
                        values["-" + letter] = rest
                    elif index < count:
                        values["-" + letter] = words[index]
                        index += 1
                    flags.append("-" + letter)
                    break
                flags.append("-" + letter)
        else:
            operands.append(word)
    return flags, values, operands


class _PathCheck:
    """One Bash command read path-aware (see the module docstring): `files`
    and `dirs` are the protected files and the folders above them as
    comparison keys relative to the project, `cwd` where relative words
    start (moved by `cd`)."""

    def __init__(self, guard: dict[str, Any], cwd: str | None) -> None:
        self.root = os.path.realpath(guard["root"])
        self.files = {_key(f) for f in guard["files"]}
        self.dirs = {""}
        for key in self.files:
            parent = posixpath.dirname(key)
            while parent and parent not in self.dirs:
                self.dirs.add(parent)
                parent = posixpath.dirname(parent)
        self.cwd = os.path.realpath(cwd) if isinstance(cwd, str) and cwd and os.path.isabs(cwd) else self.root
        self.budget = _WALK_CAP

    # -- locating words ------------------------------------------------------------------------

    def _abs(self, word: str) -> str:
        word = os.path.expanduser(word)
        return word if os.path.isabs(word) else os.path.join(self.cwd, word)

    def locate(self, word: str, *, follow_last: bool, above: bool = False) -> tuple[str, str] | None:
        """`(real path, key)` of a word that is inside the project (the key is
        its comparison key relative to the root, "" for the root itself),
        else None; with `above` a folder that holds the whole project
        answers "" too. With `follow_last` False a link in the last position
        is not followed (removing or moving a link leaves its target alone)."""
        path = self._abs(word)
        head, tail = os.path.split(path)
        real = os.path.realpath(path) if follow_last or tail in ("", ".", "..") \
            else os.path.join(os.path.realpath(head), tail)
        rel = os.path.relpath(real, self.root)
        if rel == ".":
            return real, ""
        if rel == ".." or rel.startswith(".." + os.sep):
            if above and self.root.startswith(real.rstrip(os.sep) + os.sep):
                return real, ""  # a folder above the project holds all of it
            return None
        return real, _key(rel.replace(os.sep, "/"))

    def _glob_hits(self, word: str, candidates: Iterable[str]) -> bool:
        """Whether a glob word can match any of `candidates` (keys relative
        to the project): compared below the glob's literal folder prefix, one
        path component at a time as a shell does."""
        parts = self._abs(word).split(os.sep)
        first = next((i for i, part in enumerate(parts) if _GLOB_RE.search(part)), len(parts))
        literal = os.path.realpath(os.sep.join(parts[:first]) or os.sep)
        pattern = [_key(part) for part in parts[first:] if part not in ("", ".")]
        base = literal.rstrip(os.sep) + os.sep
        for key in candidates:
            full = self.root if not key else os.path.join(self.root, *key.split("/"))
            if not (full + os.sep).startswith(base):
                continue
            tail = [_key(p) for p in full[len(base):].split(os.sep) if p]
            if "**" in pattern:
                if fnmatch.fnmatchcase("/".join(tail), "/".join(pattern).replace("**", "*")):
                    return True
            elif len(tail) == len(pattern) and all(fnmatch.fnmatchcase(t, p) for t, p in zip(tail, pattern)):
                return True
        return False

    # -- the questions -----------------------------------------------------------------------------

    def removal_hit(self, word: str) -> bool:
        """Whether removing or moving `word` takes a protected file with it:
        it is one, or a folder above one."""
        if "\0" in word or re.search(r"[$`]", word):
            return False  # not plain text: cannot be judged
        if _GLOB_RE.search(word):
            return self._glob_hits(word, self.files | self.dirs)
        found = self.locate(word, follow_last=False, above=True)
        return found is not None and (found[1] in self.files or found[1] in self.dirs)

    def _names_of(self, word: str, contents: bool) -> list[str]:
        """The names a copy of `word` puts into the destination folder."""
        names: list[str] = []
        if _GLOB_RE.search(word):
            matches = _glob.glob(self._abs(word))[:_GLOB_CAP]
            if not matches:
                return [os.path.basename(word)]  # a pattern: compared with the folder's protected names
            return [n for m in matches for n in self._names_of(m, False)]
        path = self._abs(word)
        if os.path.isdir(path):
            top = "" if contents else os.path.basename(path.rstrip(os.sep).rstrip("/.")) or ""
            if top:
                names.append(top)
            for dirpath, dirnames, filenames in os.walk(path):
                relative = os.path.relpath(dirpath, path)
                for name in (*dirnames, *filenames):
                    self.budget -= 1
                    if self.budget < 0:
                        return names
                    inner = name if relative == "." else posixpath.join(relative.replace(os.sep, "/"), name)
                    names.append(posixpath.join(top, inner) if top else inner)
            return names
        base = os.path.basename(path.rstrip(os.sep))
        return [base] if base not in ("", ".", "..") else []

    def dest_hit(self, dest: str, names: Callable[[], list[str]] | list[str] | None, *,
                 directory: bool = False) -> bool:
        """Whether writing `names` (None: unknown; a function: asked only if
        needed) to `dest` overwrites a protected file: `dest` is one, or a
        folder (an existing one, or `directory`) that already holds a
        protected file of the same name."""
        if "\0" in dest or re.search(r"[$`]", dest):
            return False
        if _GLOB_RE.search(dest):
            return False
        if re.match(r"[\w.\-@]+:(?!/)", dest) and not os.path.exists(self._abs(dest)):
            return False  # host:path, another machine
        found = self.locate(dest, follow_last=True)
        if found is None:
            return False
        real, key = found
        if key in self.files:
            return True
        if not (directory or os.path.isdir(real)):
            return False
        if names is None:
            return any(posixpath.dirname(f) == key for f in self.files)
        for name in names() if callable(names) else names:
            name = _key(name)
            full = posixpath.join(key, name) if key else name
            if _GLOB_RE.search(name):
                if any(fnmatch.fnmatchcase(f, full) for f in self.files):
                    return True
            elif full in self.files:
                return True
        return False

    # -- commands ---------------------------------------------------------------------------------------

    def run(self, command: str, depth: int = 0) -> None:
        """Raise `_Refuse` when some simple command in `command` is refusable."""
        if depth > 3 or not _PATH_PROGRAMS_RE.search(command):
            return
        for segment in _segments(command, ";&|\n"):
            words = _split_words(segment)
            if words:
                words[0] = words[0].lstrip("({$`")
            if words and words[-1].endswith(")") and words[-1].count(")") > words[-1].count("("):
                words[-1] = words[-1].rstrip(")")
            while words and not words[0]:
                words.pop(0)
            while words:
                head = words[0]
                if re.match(r"[A-Za-z_]\w*=", head) or head in ("{", "(", "!", "then", "do", "else", "if", "while"):
                    words.pop(0)
                elif os.path.basename(head) in _PREFIX_PROGRAMS:
                    valued = _PREFIX_VALUE_OPTIONS.get(os.path.basename(words.pop(0)), frozenset())
                    while words and words[0].startswith("-") and words[0] != "--":
                        option = words.pop(0)
                        if words and (option in valued or (option == "-n" and re.fullmatch(r"-?\d+", words[0]))):
                            words.pop(0)
                else:
                    break
            if words:
                self._simple(words, depth)

    def _simple(self, words: list[str], depth: int) -> None:
        program = os.path.basename(words[0])
        args = words[1:]
        if program in ("cd", "pushd"):
            self._cd(args)
        elif program in _SHELLS or program == "eval":
            script = None
            if program == "eval":
                script = " ".join(args)
            else:
                for i, word in enumerate(args):
                    if word.startswith("-") and not word.startswith("--") and "c" in word and i + 1 < len(args):
                        script = args[i + 1]
                        break
            if script:
                self.run(script, depth + 1)
        elif program in _REMOVERS:
            _flags, _values, operands = _split_args(args)
            if any(self.removal_hit(o) for o in operands):
                raise _Refuse
        elif program in _COPIERS or program == "mv":
            self._transfer(program, args)
        elif program == "find":
            self._find(args)
        elif program == "git":
            self._git(args)
        elif program == "unzip":
            self._unzip(args)
        elif program == "tar":
            self._tar(args)
        elif program in ("curl", "wget"):
            self._download(args, program=program)

    def _cd(self, args: list[str]) -> None:
        operands = [a for a in args if not a.startswith("-") or a == "-"]
        target = operands[0] if operands else "~"
        if target == "-" or re.search(r"[$`*?\[]", target):
            return
        path = self._abs(target)
        if os.path.isdir(path):  # a folder that does not exist yet cannot be cd'd into: the working folder stays
            self.cwd = os.path.realpath(path)

    def _transfer(self, program: str, args: list[str]) -> None:
        flags, values, operands = _split_args(args, _COPY_VALUE_SHORT[program], _COPY_VALUE_LONG)
        if program == "install" and "-d" in flags:
            return
        target = values.get("-t") or values.get("--target-directory")
        if program == "rsync" and "--remove-source-files" in flags and any(self.removal_hit(o) for o in operands[:-1]):
            raise _Refuse
        if program == "mv" and any(self.removal_hit(o) for o in (operands if target else operands[:-1])):
            raise _Refuse
        if target is not None:
            sources, dest, directory = operands, target, True
        elif program == "ln" and len(operands) == 1:
            sources, dest, directory = operands, ".", True
        elif len(operands) >= 2:
            sources, dest, directory = operands[:-1], operands[-1], False
        else:
            return
        if program == "rsync" and any(f.startswith("--delete") for f in flags) \
                and not re.search(r"[$`*?\[]", dest):
            found = self.locate(dest, follow_last=True)
            if found is not None and (found[1] in self.dirs or found[1] in self.files):
                raise _Refuse  # rsync --delete empties the destination of everything the source lacks

        def names() -> list[str]:
            found: list[str] = []
            for source in sources:
                if not re.search(r"[$`]", source):
                    contents = (program == "rsync" and source.endswith("/")) or source.endswith("/.") or source == "."
                    found.extend(self._names_of(source, contents))
            return found

        if self.dest_hit(dest, names, directory=directory):
            raise _Refuse

    def _find(self, args: list[str]) -> None:
        starts: list[str] = []
        for word in args:
            if word.startswith("-") or word in ("(", ")", "!", ","):
                break
            starts.append(word)
        expr = args[len(starts):]
        deleting = "-delete" in expr
        for i, word in enumerate(expr):
            if word in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(expr) \
                    and os.path.basename(expr[i + 1]) in _REMOVERS:
                deleting = True
        if not deleting:
            return
        kinds = {expr[i + 1] for i, w in enumerate(expr[:-1]) if w == "-type"}
        if kinds and not any("f" in k for k in kinds):
            return  # only folders (empty ones) or links: no protected file is taken
        names = [expr[i + 1] for i, w in enumerate(expr[:-1]) if w in ("-name", "-iname")]
        unsure = any(w in ("!", "-not", "-path", "-ipath", "-wholename", "-regex", "-iregex") for w in expr)
        for start in starts or ["."]:
            if re.search(r"[$`]", start):
                continue
            if _GLOB_RE.search(start):
                if self._glob_hits(start, self.files | self.dirs):
                    raise _Refuse
                continue
            found = self.locate(start, follow_last=False)
            if found is None:
                continue
            key = found[1]
            below = [f for f in self.files if f == key or not key or f.startswith(key + "/")]
            if not below:
                continue
            if unsure or not names:
                raise _Refuse
            if any(fnmatch.fnmatchcase(posixpath.basename(f), _key(n)) for f in below for n in names):
                raise _Refuse

    def _git(self, args: list[str]) -> None:
        cwd = self.cwd  # `git -C dir` is that git's alone
        try:
            self._git_command(args)
        finally:
            self.cwd = cwd

    def _git_command(self, args: list[str]) -> None:
        index = 0
        while index < len(args) and args[index].startswith("-"):
            if args[index] == "-C" and index + 1 < len(args) and os.path.isdir(self._abs(args[index + 1])):
                self.cwd = os.path.realpath(self._abs(args[index + 1]))
            index += 2 if args[index] in _GIT_VALUE_OPTIONS else 1
        if index >= len(args):
            return
        sub, rest = args[index], args[index + 1:]
        if sub == "rm":
            flags, _values, operands = _split_args(rest)
            if "--cached" not in flags and any(self.removal_hit(o) for o in operands):
                raise _Refuse
        elif sub == "mv":
            _flags, _values, operands = _split_args(rest)
            if len(operands) >= 2:
                if any(self.removal_hit(o) for o in operands[:-1]):
                    raise _Refuse
                if self.dest_hit(operands[-1], lambda: [n for o in operands[:-1] if not re.search(r"[$`]", o)
                                                        for n in self._names_of(o, False)]):
                    raise _Refuse

    def _names_from_urls(self, urls: list[str]) -> list[str]:
        names = []
        for url in urls:
            clean = url.split("#", 1)[0].split("?", 1)[0]
            if "://" in clean:
                clean = clean.split("://", 1)[1]
            if "/" not in clean:
                continue
            name = clean.rsplit("/", 1)[1]
            if name:
                names.append(name)
        return names

    def _download(self, args: list[str], *, program: str) -> None:
        """`curl -o FILE` / `-O` / `--output-dir DIR` and `wget -O FILE` / `-P DIR`. wget without
        `-O` keeps an existing file and numbers the new one, so only its `-P` folder is judged."""
        curl = program == "curl"
        long_values = frozenset({"--output", "--output-dir", "--header", "--data", "--request", "--user",
                                 "--user-agent", "--max-time", "--output-document", "--directory-prefix"})
        flags, values, operands = _split_args(args, "oHdXuAebcCDEFKmPQrTtwxyYzU" if curl else "OPoaeiHtTwQUBlIXARDYuC",
                                              long_values)
        out = values.get("-o" if curl else "-O") or values.get("--output" if curl else "--output-document")
        if out is not None and out != "-" and self.dest_hit(out, []):
            raise _Refuse
        if curl:
            folder = values.get("--output-dir")
            remote = "-O" in flags or "--remote-name" in flags
        else:
            folder = values.get("-P") or values.get("--directory-prefix")
            remote = False
        if folder is not None or remote:
            names = self._names_from_urls(operands)
            if names and self.dest_hit(folder if folder is not None else ".", names, directory=True):
                raise _Refuse

    def _zip_names(self, path: str) -> list[str] | None:
        try:
            if os.path.getsize(path) > _ARCHIVE_SIZE_CAP:
                return None
            import zipfile  # noqa: PLC0415 -- only when an unzip is seen

            with zipfile.ZipFile(path) as archive:
                return archive.namelist()[:_MEMBER_CAP]
        except Exception:  # noqa: BLE001 -- unreadable or not a zip: contents unknown
            return None

    def _tar_names(self, path: str) -> list[str] | None:
        try:
            if os.path.getsize(path) > _ARCHIVE_SIZE_CAP:
                return None
            import tarfile  # noqa: PLC0415 -- only when a tar -x is seen

            names = []
            with tarfile.open(path, "r:*") as archive:
                for member in archive:
                    names.append(member.name)
                    if len(names) >= _MEMBER_CAP:
                        break
            return names
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _member_names(names: list[str], junk: bool, strip: int) -> list[str]:
        out = []
        for name in names:
            parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
            if junk:
                parts = parts[-1:]
            parts = parts[strip:]
            if parts and ".." not in parts:
                out.append("/".join(parts))
        return out

    def _unzip(self, args: list[str]) -> None:
        flags, values, operands = _split_args(args, "dxP", frozenset({"--directory"}))
        if any(f in flags for f in ("-l", "-t", "-p", "-v", "-Z", "-n", "-z", "-c")):
            return  # lists, tests, prints, or never overwrites
        dest = values.get("-d")
        if not operands:
            return
        archive = self._abs(operands[0])
        listed = self._zip_names(archive) if os.path.isfile(archive) else None
        names = None if listed is None else self._member_names(listed, "-j" in flags, 0)
        self._extract(dest, names)

    def dir_holds_direct(self, directory: str) -> bool:
        """Whether the folder `directory` directly holds a protected file."""
        if re.search(r"[$`*?\[]", directory):
            return False
        found = self.locate(directory, follow_last=True)
        return found is not None and any(posixpath.dirname(f) == found[1] for f in self.files)

    def _tar(self, args: list[str]) -> None:
        if not args:
            return
        words = list(args)
        if not words[0].startswith("-") and re.fullmatch(r"[A-Za-z]+", words[0]):
            words[0] = "-" + words[0]  # the old style: `tar xzf a.tgz`
        flags, values, operands = _split_args(words, "CfT", frozenset({"--directory", "--file", "--strip-components"}))
        letters = {f[1:] for f in flags if len(f) == 2}
        extracting = "x" in letters or "--extract" in flags or "--get" in flags
        if not extracting or "-O" in flags or "--to-stdout" in flags:
            return
        dest = values.get("-C") or values.get("--directory")
        archive = values.get("-f") or values.get("--file")
        if archive is None and operands and "f" in letters:
            archive = operands[0]
        try:
            strip = int(values.get("--strip-components", "0"))
        except ValueError:
            strip = 0
        listed = None
        if archive and archive != "-":
            path = self._abs(archive)
            listed = self._tar_names(path) if os.path.isfile(path) else None
        names = None if listed is None else self._member_names(listed, False, strip)
        self._extract(dest, names)

    def _extract(self, dest: str | None, names: list[str] | None) -> None:
        """An archive unpacked into `dest` (None: the working folder). With the member names known they
        are compared with the protected files there; with an archive that cannot be read (not
        downloaded yet) only a folder named on purpose that already holds a protected file is
        refused, since the working folder is the project and almost always holds one."""
        if names is not None:
            if self.dest_hit(dest or ".", names, directory=True):
                raise _Refuse
        elif dest is not None and self.dir_holds_direct(dest):
            raise _Refuse


def _check_paths(guard: dict[str, Any], command: str, cwd: str | None) -> bool:
    """Whether the path-aware reading of `command` finds a protected file removed, moved or overwritten."""
    if not guard.get("files"):
        return False
    try:
        _PathCheck(guard, cwd).run(command)
    except _Refuse:
        return True
    return False


def _check_bash(guard: dict[str, Any], command: str, cwd: str | None = None) -> str | None:
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
    if _check_paths(guard, command, cwd):
        return _PROTECTED_MSG
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
        return _check_bash(guard, command, cwd)
    return None


def log_refusal(run_dir: Path, tool_name: str, target: str, reason: str) -> None:
    """Append one refusal to the run's `refusals.jsonl` (the run log shows it)."""
    runs.append_line(Path(run_dir) / REFUSALS_FILENAME, {
        "at": runs.now_iso(), "tool": str(tool_name)[:100], "target": str(target)[:_TARGET_CAP], "reason": reason})

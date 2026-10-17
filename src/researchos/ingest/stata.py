"""Reading a Stata do-file for data lineage (DESIGN.md 15.1-15.2, task V11).

Nothing in Stata's syntax is safe to match with a word-anywhere pattern
(`gen use = 1`, `local save ...`, `keepusing(...)`), so a do-file goes
through two passes, and `researchos.ingest.dataflow` turns what they find
into `reads`/`writes` links exactly as it does for Python and R.

**The lexer** (`commands`) cuts the text into logical commands, each with
the line it starts on in the file: `/* */` comments (nested, across lines),
`//` comments (at a line's start or after a blank), `///` continuation, a
`*` comment at a command's start (continued by `///`, ended by the end of
the command), `#delimit ;` / `#delimit cr` (from `#d`), double quotes and
compound quotes `` `"..."' `` (a comment marker inside a string is text).

**Commands** are recognized only at a command's start, after the prefixes
`capture`, `quietly`, `noisily`, `by ...:`, `bysort ...:`, `version #:` and
`else`, and on their own lines inside the `{ }` blocks of `if`, `else`,
`foreach`, `forvalues`, `while`, `capture` and `quietly`. Spellings are
normalised to one name (`sa`, `sav` and `save` are all `save`; `gr export`
is `graph export`) by the minimum abbreviations of Stata's manual, so a
re-spelling never changes a link's basis (9.6). Mata and Python blocks are
not Stata commands and are passed over. A name a project program defines
(`program define`, in a project .do or .ado) is never read as a Stata
command (15.1).

**Reads**: `use` (also `use varlist using`), `merge`, `joinby`, `cross`,
`append` (several files), `import delimited`, `import excel`, `insheet`,
`infile`, `infix`. **Writes**: `save`, `saveold`, `graph export`, `export
delimited`, `export excel`, `outsheet`. A file name without an extension
gets `.dta` for exactly `use`, `save`, `saveold`, `merge`, `append`,
`joinby` and `cross`, as the manual states; nowhere else.

**Paths.** A backslash is a path separator, except before `$` and `` ` ``
where Stata reads it as an escape (15.3).

**Macros** (15.3; `scan_project` reads the project's do-files and .ado
files together). `$name`, `${name}` and `` `name' `` are expanded, inner
references first. A **global** is used only when every assignment to it in
the project gives the same literal, after its own macros are expanded
(`global data "${root}/data"`); assignments are not ordered, since they may
sit in any file. A **local** belongs to its file (to its program, inside
`program ... end`) and is used only when assigned once, before the line
that uses it; it may fold from a global. A name a loop, `tempname`,
`args`, `gettoken`, a `local()` option, `c_local`, `st_local` or a built
name may also set never folds, nor does any local of a program with
`syntax` or of a file part with `include`. An undefined macro is
unresolved, never the empty string Stata would use. The assignments used
are recorded (`StataCall.folded_from`).

**The working folder** (15.3; `_Project.scan`). A relative path is
relative to Stata's working folder, which a run carries along:

- `do`, `run` (`ru`) and `include` -- at a command's start, `.do` assumed
  as the manual states -- run another project do-file in the same
  working folder. A target is used only when it is literal or folded (the
  macro rules above), and it resolves in the caller's working folder.
- A **root** is a do-file no resolved run reaches (every do-file read as
  if nobody ran it, and what its runs reach). A root keeps today's rule
  for its own relative paths (the project root, then its own folder:
  dataflow's `_resolve_target`); the do-files it runs start in its own
  folder. A do-file reached by runs reads its relative paths in that one
  working folder, made project-relative (`StataCall.working`), with no
  fallback. One reached with two different working folders, or by a run
  of its own runs (a cycle), has its relative paths skipped; one no run
  from a root reaches keeps today's rule. Unresolved targets are never
  matched by name.
- A literal `cd` (`chdir`) changes the working folder from that command
  on, and into the runs after it; a `cd` in a run holds in its caller
  afterwards (Stata's working folder is global). Its folder is a confirmed
  project folder, an absolute path inside the project, or a path relative
  to the working folder -- in a root before any cd, the project folder or
  its own, whichever exactly one existing folder answers. A bare `cd` (the
  home folder on a Mac), one under `capture` or `else` or after an inline
  `if`, one inside a block, one whose folder does not resolve or lies
  outside the project, and a call to a project program whose body has a
  `cd`, leave the working folder unknown: every relative path after it is
  skipped (`WORKING_FOLDER`), carrying the global that headed the cd, when
  one did, so it counts toward that global's proposal. A `cd` inside a
  program's body holds in the body only. Absolute paths and paths built on
  a confirmed folder are not affected.

**Skipped and logged, never guessed** (each with a reason, `Skip.reason`,
and the macro at fault, `Skip.macro`): tempfiles, names built in a loop,
macros that are undefined, assigned more than once or not a literal, a
path built on a global that lies outside the project (a root on someone's
computer), the relative paths whose working folder cannot be told (above),
`sysuse`/`webuse` (Stata's own example data), tables and logs
(`esttab`, `estout`, `outreg2`, `texsave`, `log using`: the outputs layer
has no node for them yet), `save` with no file name (it writes to
wherever the data last came from), a command whose words do not fit its
syntax, a command after an inline `if` condition (the condition's end
cannot be told), web addresses. Where the macro at fault heads the path,
`Skip.rest` holds the path after it, for a project-folder proposal (and,
for a global outside the project, `Skip.value` its own value).
Vendored libraries (`vendored`) are listed as skipped and never read.

**The project folder behind a global** (15.3): `anchors` maps a global to
the project folder a person confirmed it stands for
(`researchos.records.stata`; "." is the project folder itself). It is used
only where the global would not resolve -- undefined, or holding a path
outside the project -- and a path built on it carries it
(`StataCall.anchor`): that path is relative to the project folder, never to
the do-file's.

A file whose structure the lexer could not follow -- a `/*` never closed, a
string never closed, unbalanced `{ }`, a command left open under `#delimit
;` at the end, an unknown `#delimit` -- is reported not parsed
(`DoScan.parsed` False, `UNPARSEABLE` for the scan): its calls are still
returned, as for R.
"""

from __future__ import annotations

import fnmatch
import logging
import posixpath
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping

from researchos import names

logger = logging.getLogger(__name__)

#: Where a confirmed project folder comes from, as a fold chain names it.
ANCHOR_SOURCE = f"{names.PROJECT_DIRNAME}/stata.toml"


# ---------------------------------------------------------------------------
# Names and their abbreviations
# ---------------------------------------------------------------------------

# Minimum abbreviations: the underlined part of each name in the syntax
# diagrams of Stata's manual (read 2026-10-17 at stata.com/help.cgi?<cmd>
# for use, save, merge, append, joinby, cross, import delimited, import
# excel, insheet, outsheet, infile1, infile2, infix, graph export, capture,
# quietly, by, version, program, do, delimit; and cd, do and include on
# 2026-10-17 for the working folder: `ru` is run's minimum, cd, chdir (cd's
# synonym), do and include underline nothing). A name whose diagram
# underlines nothing has no abbreviation: it maps to itself only. User-
# written commands (esttab, estout, outreg2, texsave) are matched in full.
_MINIMUM = {
    # commands
    "use": "u", "save": "sa", "saveold": "saveold", "merge": "mer",
    "append": "ap", "joinby": "joinby", "cross": "cross",
    "insheet": "insheet", "outsheet": "outs", "infile": "inf",
    "infix": "infix", "import": "import", "export": "export", "graph": "gr",
    "sysuse": "sysuse", "webuse": "webuse", "tempfile": "tempfile",
    "program": "pr", "log": "log", "if": "if", "mata": "mata", "python": "python",
    # the working folder (help cd, help do, help include)
    "cd": "cd", "chdir": "chdir", "do": "do", "run": "ru",
    "esttab": "esttab", "estout": "estout", "outreg2": "outreg2", "texsave": "texsave",
    # macros and loops (help macro, foreach, forvalues, args, gettoken,
    # syntax, include; c_local has no help page and no abbreviation)
    "global": "gl", "local": "loc", "macro": "ma", "tempname": "tempname",
    "tempvar": "tempvar", "args": "args", "gettoken": "gettoken", "syntax": "syntax",
    "c_local": "c_local", "foreach": "foreach", "forvalues": "forv", "include": "include",
    # prefixes
    "capture": "cap", "quietly": "qui", "noisily": "n", "by": "by",
    "bysort": "bys", "version": "vers", "else": "else",
}
_SUBCOMMANDS = {
    "import": {"delimited": "delim", "excel": "exc"},
    "export": {"delimited": "delim", "excel": "exc"},
    "graph": {"export": "export"},
    "program": {"define": "de", "drop": "drop", "list": "l", "dir": "di"},
    "macro": {"drop": "drop"},
}


def _spellings(table: dict[str, str]) -> dict[str, str]:
    """Every accepted spelling -> its full name: each prefix of the full
    name at least as long as the minimum abbreviation (Stata's rule)."""
    out: dict[str, str] = {}
    for full, minimum in table.items():
        for end in range(len(minimum), len(full) + 1):
            out[full[:end]] = full
    return out


_NAMES = _spellings(_MINIMUM)
_SUBNAMES = {head: _spellings(table) for head, table in _SUBCOMMANDS.items()}

_PREFIXES = frozenset({"capture", "quietly", "noisily"})
#: `cd` and its synonym, which change the working folder (help cd).
_CD = frozenset({"cd", "chdir"})
#: The commands that run another do-file in the same working folder.
RUNS = frozenset({"do", "run", "include"})
_COLON_PREFIXES = frozenset({"by", "bysort", "version"})

# canonical command -> (kind, where its file names are)
#   "using":          the one word after `using` (required)
#   "using_many":     every word after `using` (append)
#   "optional_using": after `using` when there is one, else the first word
#   "first":          the first word
#   "save":           the first word; none means c(filename), skipped
_FORMS = {
    "use": ("read", "optional_using"),
    "merge": ("read", "using"),
    "joinby": ("read", "using"),
    "cross": ("read", "using"),
    "append": ("read", "using_many"),
    "import delimited": ("read", "optional_using"),
    "import excel": ("read", "optional_using"),
    "insheet": ("read", "using"),
    "infile": ("read", "using"),
    "infix": ("read", "using"),
    "save": ("write", "save"),
    "saveold": ("write", "first"),
    "graph export": ("write", "first"),
    "export delimited": ("write", "optional_using"),
    "export excel": ("write", "optional_using"),
    "outsheet": ("write", "using"),
}
#: The commands whose file name gets `.dta` when it has no extension.
DTA_DEFAULT = frozenset({"use", "save", "saveold", "merge", "append", "joinby", "cross"})
#: A do-file named without an extension is `.do` for do, run and include
#: ("If filename is specified without an extension, .do is assumed": help do,
#: help include).
_DO_DEFAULT = RUNS
# Commands whose syntax lets `if`/`in` follow the file name.
_QUALIFIED = frozenset({"export delimited", "export excel", "outsheet", "infile", "infix"})
_OUTPUTS = frozenset({"esttab", "estout", "outreg2", "texsave", "log"})

# Reasons a path or command is skipped (`Skip.reason`).
TEMPFILE = "tempfile"
LOOP = "built in a loop"
UNDEFINED = "macro not defined"
REASSIGNED = "macro assigned more than once"  # a global: with different values
MACRO = "macro not a literal"  # an expression, an extended function, a program argument
OUTSIDE = "outside the project"
BUILTIN_DATA = "sysuse/webuse"
OUTPUTS = "outputs layer"
NO_FILE = "no file name"
SHAPE = "does not fit the syntax"
INLINE_IF = "inline if"
WEB = "web address"
ENCODING = "encoding"
PROGRAM = "project program"
UNCLOSED = "string not closed"
# The working folder (15.3): a `cd` that cannot be followed -- bare (the home
# folder on a Mac), under `capture` or an inline `if`, inside a block or a
# program, or a call to a project program that changes it -- and what it
# leaves unresolved.
CD_NOT_FOLLOWED = "cd not followed"
WORKING_FOLDER = "working folder unknown"  # a relative path after such a cd
TWO_FOLDERS = "run from two working folders"  # a do-file reached from two
CYCLE = "do-files run each other"
NOT_RUN = "no such do-file in the project"  # a do/run/include target
TOO_DEEP = "runs nested too deeply"
#: How deep a chain of do-files running do-files is followed: far deeper than
#: any package nests (the three acceptance packages nest two deep at most),
#: and well inside Python's recursion limit; a deeper run is reported, not
#: followed.
RUN_DEPTH = 100

_VENDORED_FOLDERS = (("ado", "plus"), ("ado", "personal"), ("libraries", "stata"))


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    """One logical command, comments removed, continuations joined."""

    line: int
    text: str
    unclosed: bool = False  # a string was still open at its end


@dataclass(frozen=True)
class StataCall:
    line: int
    command: str  # the canonical name, e.g. "graph export"
    kind: str  # "read" | "write"
    literal: str  # the path, macros expanded, separators and default extension applied
    # The word as written and the assignments that expanded it; None for a
    # path written out in full.
    folded_from: str | None = None
    # (global, project folder) when the path is built on a confirmed answer
    # (15.3): it is relative to the project folder.
    anchor: tuple[str, str] | None = None
    # (working folder, the command that set it, with its file and line) when
    # a relative path was made project-relative against a working folder that
    # a `cd` or a `do`/`run`/`include` set (15.3): `literal` is then relative
    # to the project folder.
    working: tuple[str, str] | None = None


@dataclass(frozen=True)
class Skip:
    line: int
    command: str
    reason: str
    text: str = ""
    macro: str = ""  # the macro at fault, as `$name` or `` `name' ``
    rest: str = ""  # the path after that macro, when the macro heads it
    value: str = ""  # for OUTSIDE, that global's own value (the absolute path)
    # For WORKING_FOLDER: the reason of the global heading the `cd` that could
    # not be followed (UNDEFINED or OUTSIDE), `macro` naming it and `rest` the
    # path after it -- a path built on that global through the working folder.
    macro_reason: str = ""


@dataclass
class DoScan:
    parsed: bool = True
    calls: list[StataCall] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The lexer
# ---------------------------------------------------------------------------


def commands(text: str) -> tuple[list[Command], str | None]:
    """The logical commands of a do-file, and the problem that made the
    lexer lose its place (None when there was none). See module docstring."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    out: list[Command] = []
    buf: list[str] = []
    line, start = 1, 0
    semi = False
    depth = 0  # /* */ nesting
    quote: str | int | None = None  # '"', or the depth of compound quotes
    star = False  # inside a `*` comment
    unclosed = False
    last = ""  # the command's last visible character
    problem: str | None = None
    i, n = 0, len(text)

    def emit() -> None:
        nonlocal buf, start, star, unclosed, last
        body = "".join(buf).strip()
        if body and not star:
            out.append(Command(start, body, unclosed))
        buf, start, star, unclosed, last = [], 0, False, False, ""

    def put(chars: str) -> None:
        nonlocal start, last
        if star:
            return
        if chars.strip():
            start = start or line
            last = chars.strip()[-1]
        buf.append(chars)

    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if depth:
            if two == "/*":
                depth, i = depth + 1, i + 2
            elif two == "*/":
                depth, i = depth - 1, i + 2
                if not depth:
                    put(" ")
            else:
                line += c == "\n"
                i += 1
            continue
        if quote is not None and not star:
            if c == "\n" and not semi:
                unclosed, quote = True, None  # strings do not span lines
                continue
            if quote == '"':
                if c == '"':
                    quote = None
                put(c if c != "\n" else " ")
                line += c == "\n"
                i += 1
            elif two == '`"':
                quote, i = quote + 1, i + 2
                put(two)
            elif two == "\"'":
                quote, i = quote - 1, i + 2
                put(two)
                if not quote:
                    quote = None
            else:
                put(c if c != "\n" else " ")
                line += c == "\n"
                i += 1
            continue
        if two == "/*":
            depth, i = 1, i + 2
            continue
        if two == "//" and (i == 0 or text[i - 1] in " \t\n"):
            end = text.find("\n", i)
            end = n if end == -1 else end
            if text.startswith("///", i) and end < n:
                put(" ")
                i, line = end + 1, line + 1  # continuation: the newline is not an end
            else:
                i = end
            continue
        if c == "\n":
            if not semi:
                emit()
            elif not star and last == "{":
                emit()  # a block's open brace ends its line under #delimit ; too
            else:
                put(" ")
            line, i = line + 1, i + 1
            continue
        if c == ";" and semi:
            emit()
            i += 1
            continue
        if star:
            i += 1
            continue
        if not start and c in " \t":
            i += 1
            continue
        if not start and c == "*":
            star, i = True, i + 1
            continue
        if not start and two == "#d":
            end = text.find("\n", i)
            end = n if end == -1 else end
            mode = _delimit(text[i:end])
            if mode is not None:
                if mode == "?":
                    problem = problem or f"line {line}: #delimit without ; or cr"
                else:
                    semi = mode == ";"
                i = end
                continue
        if c == '"':
            quote = '"'
        elif two == '`"':
            quote = 1
            put(two)
            i += 2
            continue
        put(c)
        i += 1
    if depth:
        problem = problem or "a /* comment is not closed at the end of the file"
    if quote is not None:
        unclosed = True
    if semi and "".join(buf).strip() and not star:
        problem = problem or "the file ends inside a command under #delimit ;"
    emit()
    return out, problem


def _delimit(physical: str) -> str | None:
    """`;`, `cr` or `?` (no valid argument) for a `#delimit` line; None if
    the line is not one (`#d` is the minimum abbreviation)."""
    match = re.match(r"#([A-Za-z]+)", physical)
    if not match or not "#delimit".startswith(match.group(0)) or len(match.group(0)) < 2:
        return None
    rest = physical[match.end():].strip()
    if rest.startswith(";"):
        return ";"
    if re.match(r"cr(?![A-Za-z0-9_])", rest):
        return "cr"
    return "?"


# ---------------------------------------------------------------------------
# Words of a command
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class _Token:
    text: str  # inside its quotes, for a quoted word
    bare: bool


def _string_end(text: str, i: int) -> int | None:
    """Index just after the string starting at `i` (`"` or `` `" ``); a
    macro inside a double-quoted string may hold quotes of its own."""
    n = len(text)
    if text.startswith('`"', i):
        depth, k = 1, i + 2
        while k < n:
            if text.startswith('`"', k):
                depth, k = depth + 1, k + 2
            elif text.startswith("\"'", k):
                depth, k = depth - 1, k + 2
                if not depth:
                    return k
            else:
                k += 1
        return None
    macro, k = 0, i + 1
    while k < n:
        ch = text[k]
        if ch == "\\" and k + 1 < n and text[k + 1] in "$`":
            k += 2
            continue
        if ch == "`":
            macro += 1
        elif ch == "'" and macro:
            macro -= 1
        elif ch == '"' and not macro:
            return k + 1
        k += 1
    return None


def _bare_end(text: str, i: int, stops: str = " \t,") -> int:
    """Index just after the unquoted word at `i`: it ends at a blank or a
    comma outside parentheses and macros, or where a string starts."""
    paren = macro = 0
    k, n = i, len(text)
    while k < n:
        ch = text[k]
        if ch == "\\" and k + 1 < n and text[k + 1] in "$`":
            k += 2
            continue
        if not paren and not macro and (ch in stops or (k > i and (ch == '"' or text.startswith('`"', k)))):
            break
        if ch == "`" and not text.startswith('`"', k):
            macro += 1
        elif ch == "'" and macro:
            macro -= 1
        elif ch == "(" and not macro:
            paren += 1
        elif ch == ")" and paren:
            paren -= 1
        k += 1
    return max(k, i + 1)


def _tokens(text: str) -> list[_Token] | None:
    """The words before the first top-level comma (where options start);
    None when a string is not closed."""
    out: list[_Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t":
            i += 1
        elif ch == ",":
            break
        elif ch == '"' or text.startswith('`"', i):
            end = _string_end(text, i)
            if end is None:
                return None
            inner = text[i + 1:end - 1] if ch == '"' else text[i + 2:end - 2]
            out.append(_Token(inner, False))
            i = end
        else:
            end = _bare_end(text, i)
            out.append(_Token(text[i:end], True))
            i = end
    return out


def _top_level_colon(text: str) -> int | None:
    """Index of the first `:` outside strings, parentheses and macros."""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == ":":
            return i
        if ch == '"' or text.startswith('`"', i):
            end = _string_end(text, i)
            if end is None:
                return None
            i = end
        elif ch in " \t":
            i += 1
        else:
            i = _bare_end(text, i, stops=" \t:")
    return None


def _strip_prefixes(text: str) -> str:
    """The command after its prefixes (module docstring) and after a
    leading `}` (`} else {`)."""
    return _split_prefixes(text)[1]


def _split_prefixes(text: str) -> tuple[tuple[str, ...], str]:
    """(the prefixes, by their canonical names, the command after them)."""
    rest = text.strip()
    found: list[str] = []
    while True:
        rest = rest.lstrip("}").lstrip()
        match = _WORD_RE.match(rest)
        if not match:
            return tuple(found), rest
        name = _NAMES.get(match.group(0))
        after = rest[match.end():].lstrip()
        if name in _PREFIXES:
            rest = after[1:] if after.startswith(":") else after
        elif name == "else":
            rest = after
        elif name in _COLON_PREFIXES:
            colon = _top_level_colon(after)
            if colon is None:
                return tuple(found), rest
            rest = after[colon + 1:]
        else:
            return tuple(found), rest
        found.append(name)


def _head(body: str) -> tuple[str, str, str]:
    """(as written, canonical name or "", the rest) of a command's first
    word -- and its second, for `import`, `export`, `graph`, `program`."""
    match = _WORD_RE.match(body)
    if not match:
        return "", "", body
    written = match.group(0)
    name = _NAMES.get(written, "")
    rest = body[match.end():]
    if name in _SUBNAMES:
        sub = _WORD_RE.match(rest.lstrip())
        subname = _SUBNAMES[name].get(sub.group(0)) if sub else None
        if subname is None:
            return written, name, rest
        rest = rest.lstrip()[sub.end():]
        return f"{written} {sub.group(0)}", f"{name} {subname}", rest
    return written, name, rest


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_LOCAL_NAME_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)'")
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
# A drive-letter or network path once backslashes are slashes: the rule of
# `researchos.ingest.dataflow.is_windows_absolute` (15.3).
_WINDOWS_ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:|//)")
# Locals and globals a command or Mata sets by name, never folded.
_OPTION_LOCAL_RE = re.compile(r"\b(?:local|qed|match)\(\s*([A-Za-z_][A-Za-z0-9_]*)")
_OPTION_GLOBAL_RE = re.compile(r"\bglobal\(\s*([A-Za-z_][A-Za-z0-9_]*)")
_ST_LOCAL_RE = re.compile(r"\bst_local\(\s*\"([A-Za-z_][A-Za-z0-9_]*)\"")
_ST_GLOBAL_RE = re.compile(r"\bst_global\(\s*\"([A-Za-z_][A-Za-z0-9_]*)\"")
_GETTOKEN_RE = re.compile(r"(?:\(\s*(local|global)\s*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class _Ref:
    """A macro reference in a word: `$name`, `${...}` or `` `...' ``."""

    kind: str  # "$" or "`"
    inner: tuple  # the pieces of its name, which may hold macros itself
    raw: str  # its name as written
    source: str  # the whole reference as written
    closed: bool


def _pieces(raw: str, i: int = 0, close: str = "") -> tuple[list, int, bool]:
    """`raw` from `i` cut into literal text and `_Ref`s, up to `close` (the
    end of a reference's name): (pieces, the index after them, whether
    `close` was found). A backslash is a path separator, except before `$`
    and `` ` ``, where it makes the next character literal (15.3)."""
    out: list = []
    buf: list[str] = []
    n = len(raw)

    def flush() -> None:
        if buf:
            out.append("".join(buf))
            buf.clear()

    while i < n:
        ch = raw[i]
        if close and ch == close:
            flush()
            return out, i + 1, True
        name = _WORD_RE.match(raw, i + 1) if ch == "$" else None
        if ch == "\\":
            escaped = i + 1 < n and raw[i + 1] in "$`"
            buf.append(raw[i + 1] if escaped else "/")
            i += 2 if escaped else 1
        elif (ch == "$" and raw.startswith("${", i)) or (ch == "`" and not raw.startswith('`"', i)):
            start, skip = i, 2 if ch == "$" else 1
            inner, i, closed = _pieces(raw, i + skip, "}" if ch == "$" else "'")
            flush()
            out.append(_Ref(ch, tuple(inner), raw[start + skip:i - 1 if closed else i], raw[start:i], closed))
        elif name:
            flush()
            out.append(_Ref("$", (name.group(0),), name.group(0), raw[i:name.end()], True))
            i = name.end()
        else:
            buf.append(ch)
            i += 1
    flush()
    return out, i, not close


@dataclass(frozen=True)
class _Value:
    """A word or a macro after expansion. `text` is None when it does not
    resolve: `reason` says why and `macro` names the macro at fault. `head`
    is the global whose own value starts the text and `rest` what follows
    that value, which a project-folder proposal needs (15.3). `chain` holds
    the assignments used, in the order they were used; `anchor` the
    confirmed answer used, if one was."""

    text: str | None
    reason: str = ""
    macro: str = ""
    head: str = ""
    rest: str | None = None
    chain: tuple[str, ...] = ()
    anchor: tuple[str, str] | None = None


@dataclass(frozen=True)
class _Def:
    """One assignment to a macro; `value` as written, None when it is not a
    literal (`=`, `:`, a loop, `args` ...)."""

    rel: str
    line: int
    scope: str
    kind: str  # "global" | "local"
    name: str
    value: str | None

    def describe(self) -> str:
        return f'{self.kind} {self.name} "{self.value}" ({self.rel}:{self.line})'


def _assignment(rest: str) -> tuple[str, str | None, bool] | None:
    """(name, value as written or None, whether the name is built from a
    macro) for the arguments of `global`/`local`; None without a name."""
    rest = rest.lstrip()
    bump = rest[:2] in ("++", "--")
    rest = rest[2:] if bump else rest
    match = _WORD_RE.match(rest)
    if not match:
        return ("", None, True) if rest[:1] in ("`", "$") and rest else None
    name, after = match.group(0), rest[match.end():]
    if after[:1] in ("`", "$"):
        return name, None, True
    value = after.strip()
    if bump or value[:1] in ("=", ":") or after[:2] in ("++", "--"):
        return name, None, False
    if value[:1] == '"' or value.startswith('`"'):
        if _string_end(value, 0) == len(value):
            value = value[1:-1] if value[0] == '"' else value[2:-2]
    return name, value, False


def _default(command: str, path: str) -> str:
    """`path` with `.dta` when its name has no extension, for exactly the
    `DTA_DEFAULT` commands -- and `.do` for do, run and include."""
    base = path.rsplit("/", 1)[-1]
    if not base or "." in base:
        return path
    return path + ".dta" if command in DTA_DEFAULT else path + ".do" if command in _DO_DEFAULT else path


def _file_words(form: str, command: str, tokens: list[_Token]) -> tuple[list[_Token], str]:
    """The words naming files, by the command's form, or ([], reason)."""
    using = next((k for k, t in enumerate(tokens) if t.bare and t.text == "using"), None)
    if form in ("using", "using_many") and using is None:
        return [], NO_FILE
    if using is not None and form != "first" and form != "save":
        after = tokens[using + 1:]
    else:
        after = tokens
    if not after:
        return [], NO_FILE
    if form == "using_many":
        return after, ""
    extra = after[1:]
    if extra and not (command in _QUALIFIED and extra[0].bare and extra[0].text in ("if", "in")):
        return [], SHAPE
    return after[:1], ""


# ---------------------------------------------------------------------------
# A do-file
# ---------------------------------------------------------------------------


def _blocks(cmds: list[Command]):
    """The commands outside Mata and Python blocks, with their command
    text after prefixes."""
    block = False
    for cmd in cmds:
        if block:
            block = cmd.text.split()[0] != "end"
            continue
        body = _strip_prefixes(cmd.text)
        written, name, rest = _head(body)
        if name in ("mata", "python") and rest.strip() in ("", ":"):
            block = True
            continue
        yield cmd, body, written, name, rest


def program_names(text: str) -> set[str]:
    """The program names `program [define] name` defines in `text`."""
    names: set[str] = set()
    for _cmd, _body, _written, name, rest in _blocks(commands(text)[0]):
        if name == "program define" or name == "program":
            tokens = _tokens(rest) or []
            if tokens and tokens[0].bare and _WORD_RE.fullmatch(tokens[0].text):
                names.add(tokens[0].text)
    return names


@dataclass(frozen=True)
class _Line:
    cmd: Command
    body: str
    written: str
    name: str
    rest: str
    scope: str  # "" for the file itself, else the program it is in
    opens: bool  # it opens a { } block
    depth: int = 0  # how many { } blocks it sits in


class _File:
    """One do-file or .ado: its commands with their scope and blocks, and
    the macros it assigns."""

    def __init__(self, rel: str, text: str) -> None:
        self.rel, self.text = rel, text
        cmds, problem = commands(text)
        self.parsed = True
        if problem:
            self._lost(problem)
        self.lines = self._lines(cmds)
        self.tempfiles: set[str] = set()
        self.locals: dict[tuple[str, str], list[_Def]] = {}
        self.loops: set[tuple[str, str]] = set()
        self.frozen: set[str] = set()  # scopes whose locals never fold
        self.built: set[tuple[str, str]] = set()  # (scope, prefix) of names built from macros
        self.st_locals = set(_ST_LOCAL_RE.findall(text))

    def _lost(self, where: str) -> None:
        """The lexer lost its place; said only of a do-file (an .ado is read
        for its macros and program names, it is not a source)."""
        self.parsed = False
        if not self.rel.lower().endswith(".ado"):
            logger.warning("%s: %s; the scan does not speak for this file", self.rel, where)

    @property
    def programs(self) -> set[str]:
        return {ln.scope for ln in self.lines if ln.scope}

    def _lines(self, cmds: list[Command]) -> list[_Line]:
        out: list[_Line] = []
        braces, scope = 0, ""
        for cmd, body, written, name, rest in _blocks(cmds):
            text = cmd.text.rstrip()
            opens = text.endswith("{") and not text.endswith("${")
            closes = len(cmd.text) - len(cmd.text.lstrip("}"))
            out.append(_Line(cmd, body, written, name, rest, scope, opens, max(braces - closes, 0)))
            if cmd.unclosed:
                continue
            braces -= closes
            if braces < 0:
                self._lost(f"line {cmd.line}: a }} closes no block")
                braces = 0
            braces += opens
            if name in ("program define", "program") and not scope:
                tokens = _tokens(rest) or []
                if tokens and tokens[0].bare and _WORD_RE.fullmatch(tokens[0].text):
                    scope = tokens[0].text
            elif scope and body.split()[:1] == ["end"]:
                scope = ""
        if braces:
            self._lost("unbalanced { } blocks")
        return out

    def add_local(self, d: _Def) -> None:
        self.locals.setdefault((d.scope, d.name), []).append(d)


def _inside(root: Path, path: str) -> str | None:
    """An absolute POSIX `path` under `root`, project-relative (symlinks
    resolved, as dataflow's `_remap_absolute_path` does), else None."""
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


class _Project:
    """The project's do-files and .ado files read together (15.3): the
    macros they assign and the program names they define."""

    def __init__(
        self, root: str | Path | None, texts: Mapping[str, str], programs, anchors: Mapping[str, str] | None = None,
    ) -> None:
        self.root = Path(root) if root is not None else None
        self.anchors = dict(anchors or {}) if root is not None else {}
        self.files = {rel: _File(rel, text) for rel, text in texts.items()}
        self.programs = set(programs)
        self.globals: dict[str, list[_Def]] = {}
        self.built_globals: set[str] = set()  # prefixes of global names built from macros
        self.drops: list[tuple[str, int, str]] = []  # `macro drop` patterns
        self.c_locals: set[str] = set()
        self._cache: dict[tuple, _Value] = {}
        self._busy: set[tuple] = set()
        for f in self.files.values():
            self.programs |= f.programs
            self._collect(f)
        # Project programs whose body changes the working folder: a call to
        # one leaves it unknown (15.3).
        self.cd_programs = {ln.scope for f in self.files.values() for ln in f.lines if ln.scope and ln.name in _CD}
        self.dos = {rel for rel in self.files if not rel.lower().endswith(".ado")}

    # -- assignments -------------------------------------------------------------------

    def _global_def(self, d: _Def) -> None:
        self.globals.setdefault(d.name, []).append(d)

    def _assign(self, f: _File, ln: _Line, kind: str, rest: str, *, literal: bool = True) -> None:
        parsed = _assignment(rest)
        if parsed is None:
            return
        name, value, built = parsed
        if built:
            if kind == "global":
                self.built_globals.add(name)
            else:
                f.built.add((ln.scope, name))
            return
        d = _Def(f.rel, ln.cmd.line, ln.scope, kind, name, value if literal else None)
        self._global_def(d) if kind == "global" else f.add_local(d)

    def _collect(self, f: _File) -> None:
        for ln in f.lines:
            name, rest, line, scope = ln.name, ln.rest, ln.cmd.line, ln.scope
            for target in _OPTION_LOCAL_RE.findall(ln.cmd.text):
                f.add_local(_Def(f.rel, line, scope, "local", target, None))
            for target in _OPTION_GLOBAL_RE.findall(ln.cmd.text):
                self._global_def(_Def(f.rel, line, scope, "global", target, None))
            words = [t.text for t in _tokens(rest) or [] if t.bare]
            if name in ("global", "local"):
                self._assign(f, ln, name, rest)
            elif name == "if" and not ln.opens:
                # its condition's end cannot be told, so it never folds
                for k, word in enumerate(words[:-1]):
                    if _NAMES.get(word) in ("global", "local"):
                        self._assign(f, ln, _NAMES[word], words[k + 1], literal=False)
            elif name == "tempfile":
                f.tempfiles.update(words)
            elif name in ("tempname", "tempvar", "args"):
                for word in words:
                    f.add_local(_Def(f.rel, line, scope, "local", word, None))
            elif name in ("foreach", "forvalues"):
                match = _WORD_RE.match(rest.lstrip())
                if match:
                    f.loops.add((scope, match.group(0)))
            elif name == "gettoken":
                for kind, target in _GETTOKEN_RE.findall(rest[:_top_level_colon(rest) or 0]):
                    d = _Def(f.rel, line, scope, kind or "local", target, None)
                    self._global_def(d) if kind == "global" else f.add_local(d)
            elif name in ("syntax", "include"):
                f.frozen.add(scope)
            elif name == "macro drop":
                for word in words:
                    if word == "_all" or "*" in word or "?" in word:
                        self.drops.append((f.rel, line, "*" if word == "_all" else word))
                    elif word.startswith("_"):
                        f.add_local(_Def(f.rel, line, scope, "local", word[1:], None))
                    else:
                        self._global_def(_Def(f.rel, line, scope, "global", word, None))
            elif name == "c_local" and words:
                self.c_locals.add(words[0])
        for target in _ST_GLOBAL_RE.findall(f.text):
            self._global_def(_Def(f.rel, 0, "", "global", target, None))

    # -- expansion ---------------------------------------------------------------------

    def _expand_raw(self, raw: str, rel: str, scope: str, line: int) -> _Value:
        return self._expand(_pieces(raw)[0], (rel, scope, line))

    def _expand(self, pieces: list, ctx: tuple[str, str, int]) -> _Value:
        out: list[str] = []
        chain: list[str] = []
        head, rest = "", None
        anchor = None
        for k, piece in enumerate(pieces):
            if isinstance(piece, str):
                out.append(piece)
                rest = None if rest is None else rest + piece
                continue
            v = self._ref(piece, ctx)
            chain.extend(v.chain)
            if v.text is None:
                tail = self._expand(pieces[1:], ctx) if k == 0 and v.head else _Value(None)
                more = None if tail.text is None or v.rest is None else v.rest + tail.text
                return _Value(None, v.reason, v.macro, v.head if k == 0 else "", more, tuple(chain))
            if v.anchor and k:  # a project folder inside a path names nothing
                return _Value(None, MACRO, piece.source, chain=tuple(chain))
            if k and _WINDOWS_ABSOLUTE_RE.match(v.text):  # nor does a drive or network root (15.3)
                return _Value(None, OUTSIDE, piece.source, chain=tuple(chain))
            out.append(v.text)
            anchor = anchor or v.anchor
            if k == 0 and v.head:
                head, rest = v.head, v.rest or ""
            elif rest is not None:
                rest += v.text
        return _Value("".join(out), head=head, rest=rest, chain=tuple(chain), anchor=anchor)

    def _ref(self, piece: _Ref, ctx: tuple[str, str, int]) -> _Value:
        if not piece.closed or (piece.kind == "`" and piece.raw[:1] in ("=", ":", "+", "-")):
            return _Value(None, MACRO, piece.source)
        name = self._expand(list(piece.inner), ctx)
        if name.text is None:
            return _Value(None, name.reason, name.macro, chain=name.chain)
        if not _WORD_RE.fullmatch(name.text):  # `1', `c(pwd)', `x++'
            return _Value(None, MACRO, piece.source)
        rel, scope, line = ctx
        if piece.kind == "$":
            v = self._memo(("$", name.text), lambda: self._global(name.text))
        else:
            v = self._local(self.files[rel], scope, name.text, line)
        return replace(v, chain=name.chain + v.chain) if name.chain else v

    def _memo(self, key: tuple, compute) -> _Value:
        if key in self._cache:
            return self._cache[key]
        if key in self._busy:  # a macro defined through itself
            return _Value(None, MACRO, f"${key[1]}" if key[0] == "$" else f"`{key[-1]}'")
        self._busy.add(key)
        try:
            value = compute()
        finally:
            self._busy.discard(key)
        self._cache[key] = value
        return value

    def _global(self, name: str) -> _Value:
        """A global's value: its assignments' (`_assigned`), or the project
        folder a person confirmed it stands for where those leave it
        undefined or outside the project (15.3)."""
        own = self._assigned(name)
        folder = self.anchors.get(name)
        tag = f"${name}"
        if folder is None:
            return own
        undefined = own.text is None and own.reason == UNDEFINED and own.macro == tag
        if not undefined and (own.text is None or not self._away(own.text)):
            return own
        # The folder stands in for the value, keeping the separator the value
        # would have had: `${root}data/x` was proposed as data/x under the
        # folder (an undefined global has no value to go by), and a root
        # written with a trailing separator joins the same way; one without
        # keeps `${out}_v2` a sibling (results_v2).
        text = folder.rstrip("/") + "/" if undefined or (own.text or "").endswith("/") else folder
        return _Value(text, head=tag, rest="", chain=(f'global {name} "{folder}" ({ANCHOR_SOURCE})',),
                      anchor=(name, folder))

    def _assigned(self, name: str) -> _Value:
        tag = f"${name}"
        defs = self.globals.get(name, [])
        if any(name.startswith(prefix) for prefix in self.built_globals):
            return _Value(None, MACRO, tag, head=tag, rest="")
        if not defs:
            return _Value(None, UNDEFINED, tag, head=tag, rest="")
        dropped = any(  # a drop the same file does not follow with an assignment
            fnmatch.fnmatchcase(name, pattern) and not any(d.rel == rel and d.line > line for d in defs)
            for rel, line, pattern in self.drops)
        values = [None if d.value is None else self._expand_raw(d.value, d.rel, d.scope, d.line) for d in defs]
        texts = {v.text if v is not None else None for v in values}
        first = values[0]
        if len(defs) == 1 and not dropped and first is not None and first.text is None:
            return replace(first, chain=(defs[0].describe(),) + first.chain)
        if dropped or None in texts or len(texts) > 1:
            return _Value(None, REASSIGNED if len(defs) + dropped > 1 else MACRO, tag, head=tag, rest="")
        head, rest = (first.head, first.rest) if first.head else (tag, "")
        return _Value(first.text, head=head, rest=rest, chain=(defs[0].describe(),) + first.chain, anchor=first.anchor)

    def _local(self, f: _File, scope: str, name: str, line: int) -> _Value:
        tag = f"`{name}'"
        if name in f.tempfiles:
            return _Value(None, TEMPFILE, tag)
        if (scope, name) in f.loops:
            return _Value(None, LOOP, tag)
        if (name in self.c_locals or name in f.st_locals or scope in f.frozen
                or any(s == scope and name.startswith(p) for s, p in f.built)):
            return _Value(None, MACRO, tag)
        defs = f.locals.get((scope, name), [])
        if not defs:  # in a program, its arguments
            return _Value(None, MACRO if scope else UNDEFINED, tag)
        if len(defs) > 1:
            return _Value(None, REASSIGNED, tag)
        d = defs[0]
        if d.value is None:
            return _Value(None, MACRO, tag)
        if d.line >= line:  # used before it is assigned
            return _Value(None, UNDEFINED, tag)
        v = self._memo(("`", f.rel, scope, name), lambda: self._expand_raw(d.value, f.rel, scope, d.line))
        return replace(v, chain=(d.describe(),) + v.chain)

    def _word(self, f: _File, ln: _Line, raw: str, command: str) -> tuple[str | None, _Value, tuple[str, str, str]]:
        """(path, its expansion, ()) for a file name as written, or (None,
        _, (reason, macro, rest))."""
        tempfiles = sorted({m.group(1) for m in _LOCAL_NAME_RE.finditer(raw)} & f.tempfiles)
        if tempfiles:
            return None, _Value(None), (TEMPFILE, f"`{tempfiles[0]}'", "")
        v = self._expand_raw(raw, f.rel, ln.scope, ln.cmd.line)
        if v.text is None:
            heads = v.head and v.head == v.macro and v.rest is not None
            return None, v, (v.reason, v.macro, _default(command, v.rest.lstrip("/")) if heads else "")
        path = v.text
        reason = NO_FILE if not path.strip() else ENCODING if "\ufffd" in path else WEB if _URL_RE.match(path) else ""
        if reason:
            return None, v, (reason, "", "")
        return _default(command, path), v, ("", "", "")

    def _away(self, path: str) -> bool:
        """A path that is absolute and outside the project."""
        if _WINDOWS_ABSOLUTE_RE.match(path):
            return True
        return posixpath.isabs(path) and self.root is not None and _inside(self.root, path) is None

    def call(self, f: _File, ln: _Line, kind: str, raw: str) -> tuple[StataCall | None, tuple[str, ...]]:
        """A file name as written, expanded: (the call, ()) or (None,
        (reason, macro, rest[, the head global's own value]))."""
        path, v, why = self._word(f, ln, raw, ln.name)
        if path is None:
            return None, why
        if v.chain and self._away(path):  # a root on someone's computer (15.3)
            heads = v.head.startswith("$") and v.rest is not None
            return None, (OUTSIDE, v.head, _default(ln.name, v.rest.lstrip("/")) if heads else "", self._own(v) if heads else "")
        if v.anchor is not None and _sibling(v.anchor, path):
            return None, (OUTSIDE, v.head, "", "")
        folded = "; ".join([raw, *dict.fromkeys(v.chain)]) if v.chain else None
        return StataCall(ln.cmd.line, ln.name, kind, path, folded, v.anchor), ()

    def _own(self, v: _Value) -> str:
        """The own value of the global heading `v`."""
        return self._memo(("$", v.head[1:]), lambda: self._global(v.head[1:])).text or ""

    def _place(self, f: _File, ln: _Line, raw: str) -> tuple:
        """Where a cd's folder or a run's do-file as written lies:
        ("fixed", project-relative path, anchor) -- built on a confirmed
        folder, or absolute inside the project -- ("rel", path), relative to
        the working folder, or ("bad", reason, macro, rest, value)."""
        path, v, why = self._word(f, ln, raw, ln.name)
        if path is None:
            return ("bad", *why[:3], "")
        heads = v.head.startswith("$") and v.rest is not None
        if v.anchor is not None:
            rel = posixpath.normpath(path)
            if _sibling(v.anchor, path) or rel == ".." or rel.startswith("../"):
                return ("bad", OUTSIDE, v.head, "", "")
            return ("fixed", rel, v.anchor)
        windows = bool(_WINDOWS_ABSOLUTE_RE.match(path))
        if windows or posixpath.isabs(path):
            inside = None if windows or self.root is None else _inside(self.root, path)
            if inside is None:
                return ("bad", OUTSIDE, v.head if heads else "", v.rest.lstrip("/") if heads else "",
                        self._own(v) if heads else "")
            return ("fixed", inside, None)
        return ("rel", path)

    # -- one do-file's commands, in order (15.2) ---------------------------------------

    def _items(self, rel_path: str) -> tuple[list, bool]:
        """The do-file's commands that matter to lineage, in order, and
        whether it was parsed: a call or a skip that holds wherever the file
        runs (`_Final`), a relative path whose place depends on the working
        folder (`_Relative`), a `cd`, a run of another do-file (`_Run`), and
        a point after which the working folder is unknown (`_Lost`). Every
        skip found here is logged here, once."""
        f = self.files[rel_path]
        items: list = []
        parsed = f.parsed
        scope = ""

        def skip(line: int, command: str, reason: str, raw: str = "", macro: str = "", rest: str = "",
                 value: str = "") -> None:
            items.append(_Final(_skip(rel_path, Skip(line, command, reason, raw, macro, rest, value))))

        for ln in f.lines:
            cmd, written, name, rest = ln.cmd, ln.written, ln.name, ln.rest
            if ln.scope != scope:  # a program's body runs when it is called, not where it is written
                scope = ln.scope
                items.append(_Scope(scope))
            if cmd.unclosed:
                skip(cmd.line, written or "?", UNCLOSED, cmd.text)
                parsed = False
                continue
            if written and written.split()[0] in self.cd_programs and written.split()[0] in self.programs:
                skip(cmd.line, written.split()[0], CD_NOT_FOLLOWED, cmd.text)  # a project program that cds
                items.append(_Lost(cmd.line))
                continue
            if not name:
                continue
            if written.split()[0] in self.programs and (name in _FORMS or name in _OUTPUTS or name in _CD
                                                        or name in RUNS):
                skip(cmd.line, written, PROGRAM)
                continue
            if name == "if" and not cmd.text.rstrip().endswith("{"):
                hit = next((t.text for t in _tokens(rest) or [] if t.bare and (
                    _NAMES.get(t.text) in _FORMS or _NAMES.get(t.text) in ("import", "export", "graph")
                    or t.text in _OUTPUTS or _NAMES.get(t.text) in _CD or _NAMES.get(t.text) in RUNS)), None)
                if hit:
                    skip(cmd.line, hit, INLINE_IF, cmd.text)
                    if _NAMES.get(hit) in _CD:
                        items.append(_Lost(cmd.line))
                continue
            if name in _CD:
                items.extend(self._cd_items(f, ln, skip))
                continue
            if name in RUNS:
                items.extend(self._run_items(f, ln, skip))
                continue
            if name in ("sysuse", "webuse"):
                skip(cmd.line, name, BUILTIN_DATA, rest.strip())
                continue
            if name in _OUTPUTS:
                tokens = _tokens(rest) or []
                if any(t.bare and t.text == "using" for t in tokens):
                    skip(cmd.line, f"{name} using" if name == "log" else name, OUTPUTS, rest.strip())
                continue
            if name not in _FORMS:
                continue
            kind, form = _FORMS[name]
            tokens = _tokens(rest)
            if tokens is None:
                skip(cmd.line, name, UNCLOSED, rest.strip())
                parsed = False
                continue
            words, reason = _file_words(form, name, tokens)
            if reason:
                skip(cmd.line, name, reason, rest.strip())
                continue
            for word in words:
                call, why = self.call(f, ln, kind, word.text)
                if call is None:
                    skip(cmd.line, name, why[0], word.text, *why[1:])
                elif call.anchor is None and not posixpath.isabs(call.literal) \
                        and not _WINDOWS_ABSOLUTE_RE.match(call.literal):
                    items.append(_Relative(call, word.text))
                else:
                    items.append(_Final(call))
        return items, parsed

    def _cd_items(self, f: _File, ln: _Line, skip) -> list:
        """A `cd`: the folder it changes to, or the point after which the
        working folder is unknown (module docstring)."""
        prefixes, _body = _split_prefixes(ln.cmd.text)
        tokens = _tokens(ln.rest)
        line, text = ln.cmd.line, ln.cmd.text
        if tokens is None:
            skip(line, ln.name, UNCLOSED, ln.rest.strip())
            return [_Lost(line)]
        conditional = "capture" in prefixes or "else" in prefixes or any(p in _COLON_PREFIXES for p in prefixes)
        if conditional or ln.depth or ln.scope or len(tokens) != 1:
            skip(line, ln.name, CD_NOT_FOLLOWED, text)  # under capture, in a block or a program, bare (home)
            return [_Lost(line)]
        where = self._place(f, ln, tokens[0].text)
        if where[0] == "bad":
            _kind, reason, macro, rest, value = where
            skip(line, ln.name, reason, tokens[0].text, macro)
            if macro.startswith("$") and reason in (UNDEFINED, OUTSIDE):
                return [_Lost(line, macro, rest, value, reason)]
            return [_Lost(line)]
        return [_Cd(line, text, where)]

    def _run_items(self, f: _File, ln: _Line, skip) -> list:
        """A `do`, `run` or `include`: the do-file it runs, as written."""
        tokens = _tokens(ln.rest)
        line = ln.cmd.line
        if tokens is None:
            skip(line, ln.name, UNCLOSED, ln.rest.strip())
            return []
        if not tokens:
            skip(line, ln.name, NO_FILE, ln.rest.strip())
            return []
        if ln.name == "include" and len(tokens) > 1:  # do and run take arguments; include does not
            skip(line, ln.name, SHAPE, ln.rest.strip())
            return []
        raw = tokens[0].text
        where = self._place(f, ln, raw)
        if where[0] == "bad":
            skip(line, ln.name, where[1], raw, where[2])
            return []
        prefixes, _body = _split_prefixes(ln.cmd.text)
        nested = bool(ln.depth or ln.scope or "capture" in prefixes or "else" in prefixes)
        return [_Run(line, ln.name, ln.cmd.text, raw, where, nested)]

    # -- the working folder along a run (15.3) ------------------------------------------

    def _cd(self, item: _Cd, wf: _Wf, rel_path: str) -> tuple[_Wf, Skip | None]:
        """The working folder after `item` from `wf`, and the skip when the
        cd cannot be followed."""
        set_by = f"{item.text} ({rel_path}:{item.line})"
        kind = item.target[0]
        if kind == "fixed":
            return _Wf(KNOWN, item.target[1], set_by, item.target[2]), None
        path = item.target[1]
        if wf.kind == UNKNOWN:
            if wf.macro:  # still built on that global: follow its rest
                return replace(wf, rest=posixpath.normpath(posixpath.join(wf.rest, path))), None
            return wf, None
        if wf.kind == KNOWN:
            new = _join(wf.folder, path)
            if new is None:
                return _Wf(UNKNOWN, reason=WORKING_FOLDER), Skip(item.line, "cd", OUTSIDE, path)
            return _Wf(KNOWN, new, set_by, wf.anchor), None
        # A do-file nobody runs: the project folder or its own, whichever
        # exactly one existing folder answers.
        found = {c for c in (_join(".", path), _join(wf.folder, path)) if c is not None
                 and self.root is not None and (self.root / c).is_dir()}
        if len(found) == 1:
            return _Wf(KNOWN, found.pop(), set_by), None
        return _Wf(UNKNOWN, reason=WORKING_FOLDER), Skip(item.line, "cd", CD_NOT_FOLLOWED, path)

    def _target(self, item: _Run, wf: _Wf, rel_path: str) -> tuple[str | None, Skip | None]:
        """The project do-file `item` runs from `wf`, or (None, why)."""
        if item.target[0] == "fixed":
            path = item.target[1]
        elif wf.kind == UNKNOWN:
            return None, Skip(item.line, item.command, wf.reason, item.raw)
        else:
            path = _join(wf.folder, item.target[1])
            if path is None:
                return None, Skip(item.line, item.command, OUTSIDE, item.raw)
        if path in self.dos:
            return path, None
        if path in self.files:  # a project .ado: its programs are read already
            return None, None
        return None, Skip(item.line, item.command, NOT_RUN, item.raw)

    def _walk(self, rel_path: str, entry: _Wf, *, depth: int, stack: tuple[str, ...], memo: dict,
              record=None, cycles: set[str] | None = None, out: list | None = None) -> _Wf | None:
        """Read `rel_path` from the working folder `entry`: its working
        folder at the end, None when it never changes it. `record(caller,
        item, callee, context)` hears every do-file a run reaches; `out`
        collects the file's calls and skips, the ones depending on the
        working folder logged here."""
        wf, changed = entry, False
        outside = (wf, changed)
        for item in self.items[rel_path]:
            if isinstance(item, _Scope):  # a cd in a program's body holds in the body only
                if item.name:
                    outside = (wf, changed)
                else:
                    wf, changed = outside
            elif isinstance(item, _Final):
                if out is not None:
                    out.append(item.thing)
            elif isinstance(item, _Relative):
                if out is not None:
                    out.append(self._relative(item, wf, rel_path))
            elif isinstance(item, _Lost):
                wf = _Wf(UNKNOWN, reason=WORKING_FOLDER, macro=item.macro, rest=item.rest, value=item.value,
                         macro_reason=item.macro_reason)
                changed = True
            elif isinstance(item, _Cd):
                wf, why = self._cd(item, wf, rel_path)
                changed = True
                if why is not None and out is not None:
                    out.append(_skip(rel_path, why))
            elif isinstance(item, _Run):
                callee, why = self._target(item, wf, rel_path)
                if callee is None:
                    if why is not None and out is not None:
                        _skip(rel_path, why)  # logged; a run is not a link (15.2), no path of the file is lost
                    continue
                if wf.kind == UNKNOWN:
                    context = wf
                else:
                    carried = f"{wf.set_by}; " if wf.kind == KNOWN and wf.set_by else ""
                    context = _Wf(KNOWN, wf.folder, f"{carried}{item.text} ({rel_path}:{item.line})", wf.anchor)
                if record is not None:
                    record(rel_path, item, callee, context)
                if callee == rel_path or callee in stack:
                    if cycles is not None:
                        cycles.update((*stack[stack.index(callee):], rel_path) if callee in stack else (rel_path,))
                    final: _Wf | None = _Wf(UNKNOWN, reason=CYCLE)
                elif depth + 1 >= RUN_DEPTH:
                    if out is not None:
                        _skip(rel_path, Skip(item.line, item.command, TOO_DEEP, item.raw))  # logged, as above
                    final = _Wf(UNKNOWN, reason=WORKING_FOLDER)
                else:
                    key = (callee, context.key)
                    if key not in memo:
                        memo[key] = self._walk(callee, context, depth=depth + 1, stack=(*stack, rel_path), memo=memo,
                                               record=record, cycles=cycles)
                    final = memo[key]
                if final is not None:  # Stata's working folder is global: a callee's cd holds after it
                    wf = _Wf(UNKNOWN, reason=WORKING_FOLDER) if item.nested else final
                    changed = True
        return wf if changed else None

    def _relative(self, item: _Relative, wf: _Wf, rel_path: str) -> StataCall | Skip:
        """A relative path in the working folder `wf`."""
        call = item.call
        if wf.kind == RULE:
            return call  # today's rule: the project folder, then the do-file's own (dataflow)
        if wf.kind == KNOWN:
            joined = _join(wf.folder, call.literal)
            if joined is None:
                return _skip(rel_path, Skip(call.line, call.command, OUTSIDE, item.raw))
            return replace(call, literal=joined, working=(wf.folder, wf.set_by), anchor=wf.anchor)
        rest = posixpath.normpath(posixpath.join(wf.rest, call.literal)) if wf.macro else ""
        return _skip(rel_path, Skip(call.line, call.command, wf.reason, item.raw, wf.macro, rest, wf.value,
                                    wf.macro_reason))

    def scan(self) -> dict[str, DoScan]:
        """Every do-file's calls and skips, each read from the working folder
        its runs give it (module docstring)."""
        self.items, parsed = {}, {}
        for rel in sorted(self.dos):
            self.items[rel], parsed[rel] = self._items(rel)

        def rule(rel: str) -> _Wf:
            return _Wf(RULE, posixpath.dirname(rel) or ".")

        # Every do-file as if nobody ran it: what its runs reach. A root is a
        # do-file none of them reaches.
        targeted: set[str] = set()
        loops: set[str] = set()
        memo: dict = {}
        for rel in sorted(self.dos):
            self._walk(rel, rule(rel), depth=0, stack=(), memo=memo, cycles=loops,
                       record=lambda _caller, _item, callee, _ctx: targeted.add(callee))
        roots = sorted(self.dos - targeted)
        # The runs that start from the roots: the working folder each do-file
        # is reached with.
        reached: dict[str, list[tuple[str, int, _Wf]]] = {}
        cycles: set[str] = set()
        memo = {}
        for rel in roots:
            self._walk(rel, rule(rel), depth=0, stack=(), memo=memo, cycles=cycles,
                       record=lambda caller, item, callee, ctx: reached.setdefault(callee, []).append(
                           (caller, item.line, ctx)))
        entries: dict[str, _Wf] = {}
        for rel in sorted(self.dos):
            contexts = sorted(reached.get(rel, ()), key=lambda c: (c[0], c[1]))
            keys = list(dict.fromkeys(c[2].key for c in contexts))
            if rel in cycles or (not contexts and rel not in roots and rel in loops):
                entries[rel] = _Wf(UNKNOWN, reason=CYCLE)
                logger.warning("%s: run by a do-file it runs itself; its relative paths are skipped, not guessing", rel)
            elif len(keys) > 1:
                entries[rel] = _Wf(UNKNOWN, reason=TWO_FOLDERS)
                logger.warning("%s: run from %d working folders (%s); its relative paths are skipped, not guessing",
                               rel, len(keys), "; ".join(f"{c}:{line}" for c, line, _ctx in contexts))
            elif contexts:
                entries[rel] = contexts[0][2]
            else:  # a root, or reached by no run that starts anywhere: today's rule
                entries[rel] = rule(rel)
        out: dict[str, DoScan] = {}
        memo = {}
        for rel in self.files:
            if rel not in self.dos:
                continue
            things: list = []
            self._walk(rel, entries[rel], depth=0, stack=(), memo=memo, out=things)
            out[rel] = DoScan(parsed=parsed[rel], calls=[t for t in things if isinstance(t, StataCall)],
                              skipped=[t for t in things if isinstance(t, Skip)])
        return out


RULE, KNOWN, UNKNOWN = "rule", "known", "unknown"


@dataclass(frozen=True)
class _Wf:
    """A working folder (15.3). RULE: a do-file nobody runs, before any
    cd -- its relative paths keep today's rule, the do-files it runs start
    in `folder`, its own. KNOWN: `folder` (project-relative, "." the
    project folder), set by `set_by`, built on `anchor` when a confirmed
    answer gave it. UNKNOWN: `reason`; when a cd headed by a global left it
    unknown, that global (`macro`, with its own `macro_reason` and `value`)
    and the path after it (`rest`)."""

    kind: str
    folder: str = ""
    set_by: str = ""
    anchor: tuple[str, str] | None = None
    reason: str = ""
    macro: str = ""
    rest: str = ""
    value: str = ""
    macro_reason: str = ""

    @property
    def key(self) -> tuple[str, ...]:
        return (self.kind, self.folder) if self.kind == KNOWN else (self.kind,)


@dataclass(frozen=True)
class _Final:
    thing: StataCall | Skip


@dataclass(frozen=True)
class _Scope:
    name: str  # the program whose body starts here; "" where the file itself resumes


@dataclass(frozen=True)
class _Relative:
    call: StataCall
    raw: str


@dataclass(frozen=True)
class _Lost:
    line: int
    macro: str = ""
    rest: str = ""
    value: str = ""
    macro_reason: str = ""


@dataclass(frozen=True)
class _Cd:
    line: int
    text: str
    target: tuple


@dataclass(frozen=True)
class _Run:
    line: int
    command: str
    text: str
    raw: str
    target: tuple
    nested: bool  # in a block or a program, or under capture: it may not run to its end


def _join(folder: str, path: str) -> str | None:
    """`path` relative to the project folder from `folder`; None when it
    leaves the project."""
    joined = posixpath.normpath(posixpath.join(folder, path))
    return None if joined == ".." or joined.startswith("../") or joined.startswith("/") else joined


def _sibling(anchor: tuple[str, str], path: str) -> bool:
    """A path built on the project folder itself (".") that names a sibling
    of the project: the global's value had no trailing separator and the
    rest does not start with one (`${out}_v2`)."""
    return anchor[1] == "." and path != "." and not path.startswith("./")


def _skip(rel_path: str, s: Skip) -> Skip:
    why = f"{s.reason}: {s.macro}" if s.macro else s.reason
    logger.warning("%s:%d: %s %r: skipped (%s), not guessing", rel_path, s.line, s.command, s.text, why)
    return s


def scan_project(
    root: str | Path | None, texts: Mapping[str, str], *, programs: frozenset[str] | set[str] = frozenset(),
    anchors: Mapping[str, str] | None = None,
) -> dict[str, DoScan]:
    """The read/write commands of a project's do-files (the keys of `texts`
    not ending in .ado), read together with its .ado files for macros and
    program names (module docstring). `root` tells which absolute paths
    lie inside the project and which folders exist; without it (None) none
    is known to, and no anchor is used. `anchors`: global -> confirmed
    project folder."""
    return _Project(root, texts, programs, anchors).scan()


def scan_text(text: str, rel_path: str, *, programs: frozenset[str] | set[str] = frozenset()) -> DoScan:
    """One do-file read as a project of its own (`scan_project` without a
    file system): its own macros only."""
    return scan_project(None, {rel_path: text}, programs=programs)[rel_path]


def vendored(repo_root: str | Path, rel_path: str) -> str | None:
    """Why a .do/.ado file belongs to a vendored library (15.1) -- the
    folder pair it sits in (`ado/plus`, `ado/personal`, `libraries/stata`)
    or `stata.trk` (a folder holding Stata's package-tracking file, up to
    but not including the project folder itself, which is the project) --
    or None for a project file."""
    folders = rel_path.split("/")[:-1]
    lowered = [f.lower() for f in folders]
    for pair in _VENDORED_FOLDERS:
        if any(tuple(lowered[k:k + 2]) == pair for k in range(len(lowered) - 1)):
            return "/".join(pair)
    for k in range(len(folders), 0, -1):
        if (Path(repo_root).joinpath(*folders[:k]) / "stata.trk").is_file():
            return "stata.trk"
    return None

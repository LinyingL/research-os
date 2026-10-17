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
where Stata reads it as an escape (15.3). Only literal paths are read in
this increment: a path holding any macro (`$name`, `${name}`, `` `name' ``)
is skipped and logged, a tempfile's name with its own reason.

**Skipped and logged, never guessed** (each with a reason, `Skip.reason`):
tempfiles, paths with macros, `sysuse`/`webuse` (Stata's own example
data), tables and logs (`esttab`, `estout`, `outreg2`, `texsave`, `log
using`: the outputs layer has no node for them yet), `save` with no file
name (it writes to wherever the data last came from), a command whose
words do not fit its syntax, a command after an inline `if` condition (the
condition's end cannot be told), web addresses. Vendored libraries
(`vendored`) are listed as skipped and never read.

A file whose structure the lexer could not follow -- a `/*` never closed, a
string never closed, unbalanced `{ }`, a command left open under `#delimit
;` at the end, an unknown `#delimit` -- is reported not parsed
(`DoScan.parsed` False, `UNPARSEABLE` for the scan): its calls are still
returned, as for R.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Names and their abbreviations
# ---------------------------------------------------------------------------

# Minimum abbreviations: the underlined part of each name in the syntax
# diagrams of Stata's manual (read 2026-10-17 at stata.com/help.cgi?<cmd>
# for use, save, merge, append, joinby, cross, import delimited, import
# excel, insheet, outsheet, infile1, infile2, infix, graph export, capture,
# quietly, by, version, program, do, delimit). A name whose diagram
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
    "esttab": "esttab", "estout": "estout", "outreg2": "outreg2", "texsave": "texsave",
    # prefixes
    "capture": "cap", "quietly": "qui", "noisily": "n", "by": "by",
    "bysort": "bys", "version": "vers", "else": "else",
}
_SUBCOMMANDS = {
    "import": {"delimited": "delim", "excel": "exc"},
    "export": {"delimited": "delim", "excel": "exc"},
    "graph": {"export": "export"},
    "program": {"define": "de", "drop": "drop", "list": "l", "dir": "di"},
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
# Commands whose syntax lets `if`/`in` follow the file name.
_QUALIFIED = frozenset({"export delimited", "export excel", "outsheet", "infile", "infix"})
_OUTPUTS = frozenset({"esttab", "estout", "outreg2", "texsave", "log"})

# Reasons a path or command is skipped (`Skip.reason`).
TEMPFILE = "tempfile"
MACRO = "macro"
BUILTIN_DATA = "sysuse/webuse"
OUTPUTS = "outputs layer"
NO_FILE = "no file name"
SHAPE = "does not fit the syntax"
INLINE_IF = "inline if"
WEB = "web address"
ENCODING = "encoding"
PROGRAM = "project program"
UNCLOSED = "string not closed"

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
    literal: str  # the path, separators and default extension applied


@dataclass(frozen=True)
class Skip:
    line: int
    command: str
    reason: str
    text: str = ""


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
    rest = text.strip()
    while True:
        rest = rest.lstrip("}").lstrip()
        match = _WORD_RE.match(rest)
        if not match:
            return rest
        name = _NAMES.get(match.group(0))
        after = rest[match.end():].lstrip()
        if name in _PREFIXES:
            rest = after[1:] if after.startswith(":") else after
        elif name == "else":
            rest = after
        elif name in _COLON_PREFIXES:
            colon = _top_level_colon(after)
            if colon is None:
                return rest
            rest = after[colon + 1:]
        else:
            return rest


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

_GLOBAL_RE = re.compile(r"\$(?:\{[^}]*\}|[A-Za-z_][A-Za-z0-9_]*)")
_LOCAL_NAME_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)'")
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def _literal(raw: str, command: str, tempfiles: set[str]) -> tuple[str | None, str]:
    """(the path, "") for a literal file name, else (None, reason). A
    backslash is a separator except before `$` and `` ` `` (15.3)."""
    out: list[str] = []
    macros: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\" and i + 1 < n and raw[i + 1] in "$`":
            out.append(raw[i + 1])
            i += 2
        elif ch == "\\":
            out.append("/")
            i += 1
        elif ch == "$" and _GLOBAL_RE.match(raw, i):
            match = _GLOBAL_RE.match(raw, i)
            macros.append(match.group(0))
            i = match.end()
        elif ch == "`":
            end, depth = i, 0
            while end < n:
                depth += raw[end] == "`"
                depth -= raw[end] == "'"
                end += 1
                if depth <= 0:
                    break
            macros.append(raw[i:end])
            i = end
        else:
            out.append(ch)
            i += 1
    if macros:
        locals_ = {m.group(1) for macro in macros for m in _LOCAL_NAME_RE.finditer(macro)}
        return None, TEMPFILE if locals_ & tempfiles else MACRO
    literal = "".join(out)
    if not literal.strip():
        return None, NO_FILE
    if "\ufffd" in literal:
        return None, ENCODING
    if _URL_RE.match(literal):
        return None, WEB
    base = literal.rsplit("/", 1)[-1]
    if command in DTA_DEFAULT and base and "." not in base:
        literal += ".dta"
    return literal, ""


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


def scan_text(text: str, rel_path: str, *, programs: frozenset[str] | set[str] = frozenset()) -> DoScan:
    """The read/write commands of one do-file's text (module docstring);
    every skip is logged with its reason and listed in `DoScan.skipped`."""
    cmds, problem = commands(text)
    scan = DoScan()
    if problem:
        logger.warning("%s: %s; the scan does not speak for this file", rel_path, problem)
        scan.parsed = False

    def skip(line: int, command: str, reason: str, raw: str = "") -> None:
        scan.skipped.append(Skip(line, command, reason, raw))
        logger.warning("%s:%d: %s %r: skipped (%s), not guessing", rel_path, line, command, raw, reason)

    tempfiles: set[str] = set()
    for _cmd, _body, _written, name, rest in _blocks(cmds):
        if name == "tempfile":
            tempfiles.update(t.text for t in _tokens(rest) or [] if t.bare)
    braces = 0
    for cmd, body, written, name, rest in _blocks(cmds):
        if cmd.unclosed:
            skip(cmd.line, written or "?", UNCLOSED, cmd.text)
            scan.parsed = False
            continue
        braces -= len(cmd.text) - len(cmd.text.lstrip("}"))
        if braces < 0:
            logger.warning("%s:%d: a } closes no block; the scan does not speak for this file", rel_path, cmd.line)
            scan.parsed, braces = False, 0
        if cmd.text.rstrip().endswith("{") and not cmd.text.rstrip().endswith("${"):
            braces += 1
        if not name:
            continue
        if written.split()[0] in programs and (name in _FORMS or name in _OUTPUTS):
            skip(cmd.line, written, PROGRAM)
            continue
        if name == "if" and not cmd.text.rstrip().endswith("{"):
            hit = next((t.text for t in _tokens(rest) or [] if t.bare and (
                _NAMES.get(t.text) in _FORMS or _NAMES.get(t.text) in ("import", "export", "graph")
                or t.text in _OUTPUTS)), None)
            if hit:
                skip(cmd.line, hit, INLINE_IF, cmd.text)
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
            scan.parsed = False
            continue
        words, reason = _file_words(form, name, tokens)
        if reason:
            skip(cmd.line, name, reason, rest.strip())
            continue
        for word in words:
            literal, reason = _literal(word.text, name, tempfiles)
            if literal is None:
                skip(cmd.line, name, reason, word.text)
            else:
                scan.calls.append(StataCall(cmd.line, name, kind, literal))
    if braces:
        logger.warning("%s: unbalanced { } blocks; the scan does not speak for this file", rel_path)
        scan.parsed = False
    return scan


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

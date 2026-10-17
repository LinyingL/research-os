"""Stata .dta files: the header, never the data (DESIGN.md 15.4).

`read_header(path)` returns a `Header`: the release, the byte order, the
number of rows, the variables (name, storage type, display format, label,
value-label name), the dataset label and its timestamp. Releases 113-115
(the binary layout of Stata 8-12) and 117-121 (the tagged layout of Stata
13 and later, 120 and 121 holding Stata 18's alias variables), both byte
orders, standard library only, written from StataCorp's format pages
(`help dta`, dta_113 to dta_121).

The data section is never read, so the cost grows with the number of
variables, not of rows (small reads come from one cached window of at most
`WINDOW` bytes, which may reach into the data; those bytes are never used). In the tagged layout every section is found
through the file's map and its length checked against the number of
variables before it is read; the data section's length is checked by
arithmetic alone (rows times the row width). A truncated, corrupt or
unsupported file is refused with `DtaError`, whose `code` says which and
whose text says what was expected and what was found -- never a partial
guess, never a hang: a FIFO, a device or a folder is refused before any
read, every read is capped by the file's size, every loop by the number
of variables or the file's size, and a header claiming more variables
than `MAX_VARIABLES` is refused before any large read. The walks of an old
file follow the format pages exactly (an expansion field is type 0 with
length 0, or type 1 holding its two 33-byte names; a value-label table
holds its two counts), so each pass consumes at least 48 bytes.

Names and labels come from the file: untrusted text. Control characters
and every bidi control (the marks U+061C, U+200E, U+200F, the embeddings,
overrides and isolates) become U+FFFD; release 118 and later are UTF-8 (invalid bytes
replaced), 117 and older are read as Latin-1, which maps every byte.

`read_header_stream(stream, size)` reads the same header from a stream (a
`git cat-file blob`), up to the data section only: it buffers no more
than the header's own bytes and skips the checks that would read past
the data. `Header.summary()` is the history's table summary (12.6).
"""

from __future__ import annotations

import os
import stat
import struct
from dataclasses import dataclass
from typing import IO, Any, Callable

#: Stata/MP holds at most 120,000 variables; a header claiming more than this
#: is refused before its sections are read.
MAX_VARIABLES = 200_000
#: The most a stream is buffered to reach the end of its header.
STREAM_LIMIT = 256 * 1024 * 1024

NEW_RELEASES = (117, 118, 119, 120, 121)
OLD_RELEASES = (113, 114, 115)

#: `DtaError.code`: what kind of problem.
NOT_A_FILE, UNREADABLE, NOT_DTA = "not_a_file", "unreadable", "not_dta"
UNSUPPORTED, TRUNCATED, CORRUPT, TOO_WIDE = "unsupported", "truncated", "corrupt", "too_wide"

_NEW_NUMERIC = {65526: ("double", 8), 65527: ("float", 4), 65528: ("long", 4), 65529: ("int", 2), 65530: ("byte", 1)}
_OLD_NUMERIC = {255: ("double", 8), 254: ("float", 4), 253: ("long", 4), 252: ("int", 2), 251: ("byte", 1)}
_STRL, _ALIAS = 32768, 65525
_SECTIONS = ("variable_types", "varnames", "sortlist", "formats", "value_label_names", "variable_labels")
_PROBES = ((8, b"<characteristics>"), (9, b"<data>"), (10, b"<strls>"), (11, b"<value_labels>"), (12, b"</stata_dta>"))
# Control characters and the whole Bidi_Control set (the marks U+061C,
# U+200E, U+200F; the embeddings and overrides; the isolates). Joiners
# (U+200C, U+200D) stay: Persian and Indic spellings need them.
_UNSAFE = {c: "�" for c in (*range(0x20), *range(0x7F, 0xA0), 0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F),
                                 *range(0x2066, 0x206A))}
#: Reads of at most this many bytes are served from one cached window of the
#: file, so a walk over many small fields costs one system call per window,
#: not two per field.
WINDOW = 64 * 1024


class DtaError(ValueError):
    """The file is not a .dta file this reader can read; `code` is one of
    the module's codes, the text the engine's own account."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Variable:
    name: str
    type: str  # byte, int, long, float, double, str1..str2045, strL, alias
    format: str
    label: str
    value_label: str


@dataclass(frozen=True)
class Header:
    release: int
    byteorder: str  # "LSF" (little-endian) or "MSF"
    rows: int
    variables: tuple[Variable, ...]
    label: str
    timestamp: str
    bytes_read: int

    def summary(self) -> dict[str, Any]:
        """Like a CSV's (12.6): the variables' names as its columns, and its
        rows -- exact, from the header."""
        return {"format": "dta", "columns": [v.name for v in self.variables], "rows": self.rows,
                "rows_complete": True}


def _text(raw: bytes, encoding: str) -> str:
    return raw.split(b"\0", 1)[0].decode(encoding, errors="replace").translate(_UNSAFE)


class _Reader:
    """Exact reads at a position, never past `size`; `fetch(offset, n)`
    returns at most n bytes."""

    def __init__(self, fetch: Callable[[int, int], bytes], size: int) -> None:
        self.fetch, self.size, self.pos, self.bytes_read, self.e = fetch, size, 0, 0, "<"

    def seek(self, offset: int, what: str) -> None:
        if not 0 <= offset <= self.size:
            raise DtaError(CORRUPT, f"{what} is placed at byte {offset}, outside the file's {self.size} bytes")
        self.pos = offset

    def read(self, n: int, what: str) -> bytes:
        if self.pos + n > self.size:
            raise DtaError(TRUNCATED, f"the file ends before {what}: {n} bytes needed at byte {self.pos}, "
                                      f"{self.size - self.pos} left")
        data = self.fetch(self.pos, n)
        if len(data) != n:
            raise DtaError(TRUNCATED, f"the file ended while reading {what} at byte {self.pos}")
        self.pos += n
        self.bytes_read += n
        return data

    def expect(self, tag: bytes, what: str) -> None:
        at = self.pos
        if self.read(len(tag), what) != tag:
            raise DtaError(CORRUPT, f"{tag.decode('ascii')} expected at byte {at} ({what}), something else found")

    def number(self, code: str, what: str) -> int:
        return struct.unpack(self.e + code, self.read(struct.calcsize(code), what))[0]


def _new(r: _Reader, max_variables: int, tail: bool) -> Header:
    r.expect(b"<stata_dta><header><release>", "the file's signature")
    raw = r.read(3, "the release")
    release = int(raw) if raw.isdigit() else None
    if release not in NEW_RELEASES:
        raise DtaError(UNSUPPORTED, f"release {raw.decode('latin-1')!r} is not one this reader knows "
                                    "(113-115, 117-121)")
    r.expect(b"</release><byteorder>", "the byte order")
    order = r.read(3, "the byte order")
    if order not in (b"LSF", b"MSF"):
        raise DtaError(CORRUPT, f"the byte order is {order!r}, neither LSF nor MSF")
    r.e = "<" if order == b"LSF" else ">"
    wide = release in (119, 121)
    r.expect(b"</byteorder><K>", "the number of variables")
    k = r.number("I" if wide else "H", "the number of variables")
    r.expect(b"</K><N>", "the number of rows")
    n = r.number("I" if release == 117 else "Q", "the number of rows")
    if k > max_variables:
        raise DtaError(TOO_WIDE, f"the header claims {k} variables; more than {max_variables} are not read")
    encoding = "latin-1" if release == 117 else "utf-8"
    r.expect(b"</N><label>", "the dataset label")
    label = _text(r.read(r.number("B" if release == 117 else "H", "the label's length"), "the dataset label"), encoding)
    r.expect(b"</label><timestamp>", "the timestamp")
    timestamp = _text(r.read(r.number("B", "the timestamp's length"), "the timestamp"), encoding)
    r.expect(b"</timestamp></header>", "the end of the header")
    map_at = r.pos
    r.expect(b"<map>", "the map")
    off = struct.unpack(r.e + "14Q", r.read(112, "the map"))
    r.expect(b"</map>", "the end of the map")
    if off[0] != 0 or off[1] != map_at or off[2] != r.pos:
        raise DtaError(CORRUPT, f"the map's first offsets {list(off[:3])} do not match the header "
                                f"(expected [0, {map_at}, {r.pos}])")
    if any(later < earlier for earlier, later in zip(off, off[1:])):
        raise DtaError(CORRUPT, "the map's offsets are not in file order")
    if off[13] > r.size:
        raise DtaError(TRUNCATED, f"the map says the file ends at byte {off[13]}, but it has only {r.size} bytes")
    if off[12] + len(b"</stata_dta>") != off[13]:
        raise DtaError(CORRUPT, "the map's end of file and its closing tag disagree")
    name_w = 33 if release == 117 else 129
    widths = (2, name_w, 4 if wide else 2, 49 if release == 117 else 57, name_w, 81 if release == 117 else 321)
    counts = (k, k, k + 1, k, k, k)
    for i, (tag, width, count) in enumerate(zip(_SECTIONS, widths, counts)):
        want, have = 2 * len(tag) + 5 + width * count, off[i + 3] - off[i + 2]
        if have != want:
            raise DtaError(CORRUPT, f"<{tag}> takes {have} bytes in the map; {k} variables need {want}")
    blocks = []
    for i, (tag, width, count) in enumerate(zip(_SECTIONS, widths, counts)):
        r.seek(off[i + 2], f"<{tag}>")
        r.expect(f"<{tag}>".encode(), tag)
        blocks.append(r.read(width * count, tag))
        r.expect(f"</{tag}>".encode(), tag)
    types: list[str] = []
    row: list[int | None] = []
    for i, code in enumerate(struct.unpack(f"{r.e}{k}H", blocks[0])):
        if 1 <= code <= 2045:
            types.append(f"str{code}")
            row.append(code)
        elif code == _STRL:
            types.append("strL")
            row.append(8)
        elif code in _NEW_NUMERIC:
            types.append(_NEW_NUMERIC[code][0])
            row.append(_NEW_NUMERIC[code][1])
        elif code == _ALIAS and release in (120, 121):
            types.append("alias")
            row.append(None)  # its width in the data is not documented
        else:
            raise DtaError(CORRUPT, f"variable {i + 1} has the unknown type code {code}")
    _check_sortlist(struct.unpack(f"{r.e}{k + 1}{'I' if wide else 'H'}", blocks[2]), k)
    for idx, tag in _PROBES if tail else _PROBES[:2]:
        r.seek(off[idx], tag.decode("ascii"))
        r.expect(tag, "a section the map points to")
    have = off[10] - off[9] - len(b"<data></data>")
    known, aliases = sum(w for w in row if w is not None), row.count(None)
    if not any(n * (known + aliases * width) == have for width in (range(9) if aliases else (0,))):
        raise DtaError(CORRUPT, f"the data section holds {have} bytes, which {n} rows of {known} bytes"
                                + (f" and {aliases} alias variables" if aliases else "") + " do not fill")
    return _header(release, order.decode(), n, types, blocks[1], blocks[3], blocks[4], blocks[5],
                   (name_w, widths[3], name_w, widths[5]), encoding, label, timestamp, r)


def _check_sortlist(entries: tuple[int, ...], k: int) -> None:
    for entry in entries:
        if entry == 0:
            return
        if entry > k:
            raise DtaError(CORRUPT, f"the sort order names variable {entry}, but there are {k}")


def _header(release: int, order: str, n: int, types: list[str], names: bytes, formats: bytes, value_labels: bytes,
            labels: bytes, widths: tuple[int, int, int, int], encoding: str, label: str, timestamp: str,
            r: _Reader) -> Header:
    def cut(block: bytes, w: int, i: int) -> str:
        return _text(block[i * w:(i + 1) * w], encoding)

    variables = tuple(Variable(cut(names, widths[0], i), t, cut(formats, widths[1], i), cut(labels, widths[3], i),
                               cut(value_labels, widths[2], i)) for i, t in enumerate(types))
    return Header(release, order, n, variables, label, timestamp, r.bytes_read)


def _old(r: _Reader, max_variables: int, tail: bool) -> Header:
    head = r.read(10, "the header")
    release, order, filetype = head[0], head[1], head[2]
    if release not in OLD_RELEASES:
        raise DtaError(UNSUPPORTED, f"release {release} was written by Stata 7 or earlier; this reader knows "
                                    "113-115 and 117-121")
    if order not in (1, 2) or filetype != 1:
        raise DtaError(CORRUPT, f"the byte order ({order}) or the file type ({filetype}) is not a .dta file's")
    r.e = ">" if order == 1 else "<"
    k, n = struct.unpack(r.e + "HI", head[4:10])
    if k > max_variables:
        raise DtaError(TOO_WIDE, f"the header claims {k} variables; more than {max_variables} are not read")
    label = _text(r.read(81, "the dataset label"), "latin-1")
    timestamp = _text(r.read(18, "the timestamp"), "latin-1")
    fmt_w = 12 if release == 113 else 49
    need = k * (1 + 33 + 2 + fmt_w + 33 + 81) + 2
    if r.pos + need > r.size:
        raise DtaError(TRUNCATED, f"{k} variables need {need} bytes of descriptors; only {r.size - r.pos} are left")
    types: list[str] = []
    row = 0
    for i, code in enumerate(r.read(k, "the variable types")):
        if 1 <= code <= 244:
            types.append(f"str{code}")
            row += code
        elif code in _OLD_NUMERIC:
            types.append(_OLD_NUMERIC[code][0])
            row += _OLD_NUMERIC[code][1]
        else:
            raise DtaError(CORRUPT, f"variable {i + 1} has the unknown type code {code}")
    names = r.read(33 * k, "the variable names")
    _check_sortlist(struct.unpack(f"{r.e}{k + 1}H", r.read(2 * (k + 1), "the sort order")), k)
    formats = r.read(fmt_w * k, "the display formats")
    value_labels = r.read(33 * k, "the value-label names")
    labels = r.read(81 * k, "the variable labels")
    while True:  # the expansion fields: each pass reads at least 71 bytes or stops
        at = r.pos
        kind, length = r.number("B", "a characteristic's type"), r.number("I", "a characteristic's length")
        if kind == 0:  # the end, always written with length 0
            if length:
                raise DtaError(CORRUPT, f"the end of the characteristics at byte {at} has length {length}, not 0")
            break
        if kind != 1:  # dta_113 to dta_115 define types 0 and 1 only
            raise DtaError(CORRUPT, f"a characteristic at byte {at} has the unknown type {kind}")
        if length < 66:  # a variable's name and the characteristic's name, 33 bytes each
            raise DtaError(CORRUPT, f"a characteristic at byte {at} takes {length} bytes, fewer than its two "
                                    "names need (66)")
        r.seek(r.pos + length, "a characteristic's end")
    end = r.pos + n * row
    if end > r.size:
        raise DtaError(TRUNCATED, f"the data section needs {n * row} bytes ({n} rows of {row}); "
                                  f"only {r.size - r.pos} are left")
    if tail:  # the value labels run to the end of the file; a walk that misses it is a cut or a corruption
        r.seek(end, "the value labels")
        while r.pos < r.size:  # each pass reads at least 48 bytes or stops
            at = r.pos
            length = r.number("I", "a value label's length")
            if length < 8:  # a table always holds n and txtlen
                raise DtaError(CORRUPT, f"a value label at byte {at} takes {length} bytes, fewer than its two "
                                        "counts need (8)")
            r.read(36, "a value label's name")
            if r.pos + length > r.size:
                raise DtaError(TRUNCATED, f"a value label needs {length} bytes; only {r.size - r.pos} are left")
            r.seek(r.pos + length, "a value label's end")
    return _header(release, "MSF" if order == 1 else "LSF", n, types, names, formats, value_labels, labels,
                   (33, fmt_w, 33, 81), "latin-1", label, timestamp, r)


def _read(r: _Reader, max_variables: int, tail: bool) -> Header:
    if r.size < 11:
        raise DtaError(NOT_DTA, f"{r.size} bytes are too few for a .dta file")
    first = r.fetch(0, 11)
    if first == b"<stata_dta>":
        return _new(r, max_variables, tail)
    if len(first) == 11 and 102 <= first[0] <= 115 and first[1] in (1, 2):
        return _old(r, max_variables, tail)
    raise DtaError(NOT_DTA, "the file does not begin as a .dta file does")


def read_header(path: str | os.PathLike[str], *, max_variables: int = MAX_VARIABLES) -> Header:
    """The header of the .dta file at `path` (module docstring); `DtaError`
    when it cannot be read."""
    try:
        # Non-blocking, so opening a FIFO cannot wait; fstat then refuses it.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise DtaError(UNREADABLE, f"the file cannot be opened: {exc.strerror}") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise DtaError(NOT_A_FILE, "not a regular file")
        handle = os.fdopen(fd, "rb", buffering=0)
    except BaseException:
        os.close(fd)
        raise
    with handle:
        def direct(offset: int, n: int) -> bytes:
            handle.seek(offset)
            chunks, got = [], 0
            while got < n:  # each pass reads something or stops
                chunk = handle.read(n - got)
                if not chunk:
                    break
                chunks.append(chunk)
                got += len(chunk)
            return b"".join(chunks)

        window = [0, b""]  # where the cached bytes start, and the bytes

        def fetch(offset: int, n: int) -> bytes:
            if n > WINDOW:
                return direct(offset, n)
            start, data = window
            if not (start <= offset and offset + n <= start + len(data)):
                start, data = offset, direct(offset, WINDOW)
                window[:] = [start, data]
            return data[offset - start:offset - start + n]

        try:
            return _read(_Reader(fetch, st.st_size), max_variables, tail=True)
        except OSError as exc:
            raise DtaError(UNREADABLE, f"the file could not be read: {exc.strerror}") from None


def read_header_stream(stream: IO[bytes], size: int, *, max_variables: int = MAX_VARIABLES) -> Header:
    """The header of a .dta file of `size` bytes read from `stream`, from
    its start to its data section only (module docstring)."""
    buffer = bytearray()

    def fetch(offset: int, n: int) -> bytes:
        end = offset + n
        if end > STREAM_LIMIT:
            raise DtaError(TOO_WIDE, f"the header runs past {STREAM_LIMIT} bytes; not read")
        while len(buffer) < end:  # each pass reads something or stops
            chunk = stream.read(min(end - len(buffer), 1 << 20))
            if not chunk:
                break
            buffer.extend(chunk)
        return bytes(buffer[offset:end])

    try:
        return _read(_Reader(fetch, size), max_variables, tail=False)
    except OSError as exc:
        raise DtaError(UNREADABLE, f"the stream could not be read: {exc.strerror}") from None

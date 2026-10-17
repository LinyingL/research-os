"""The .dta header reader (DESIGN.md 15.4, task V11 S4): rows and variables
from every release 113-121 in both byte orders, the data never read, and
every truncated, corrupt or strange file refused with `DtaError` -- never
another exception, never a hang. Every .dta here is written by the small
writer below, from StataCorp's format pages; none comes from a replication
package. Every test names its scenario."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import random
import signal
import struct
import time
from pathlib import Path

import pytest

from researchos import dta

OLD = (113, 114, 115)
NEW_CODE = {"double": 65526, "float": 65527, "long": 65528, "int": 65529, "byte": 65530, "strL": 32768, "alias": 65525}
OLD_CODE = {"double": 255, "float": 254, "long": 253, "int": 252, "byte": 251}
WIDTH = {"double": 8, "float": 4, "long": 4, "int": 2, "byte": 1, "strL": 8, "alias": 0}
#: (name, type, format, label, value-label name) -- `dta.Variable`'s order.
VARS = [("id", "long", "%12.0g", "Row id", ""), ("x", "double", "%10.0g", "X value", ""),
        ("grp", "byte", "%8.0g", "Group", "grp"), ("name", "str12", "%12s", "Name", "")]
STRL = ("note", "strL", "%9s", "Free text", "")
ALIAS = ("ali", "alias", "%9.0g", "Alias of a frame's variable", "")
TABLE = b"\x00" * 24  # one value-label table; its content is never read
STAMP = "17 Oct 2026 09:30"


def _pad(text: str | bytes, width: int, encoding: str) -> bytes:
    raw = text if isinstance(text, bytes) else text.encode(encoding)
    assert len(raw) < width
    return raw + b"\0" * (width - len(raw))


def _tag(name: str, body: bytes) -> bytes:
    return f"<{name}>".encode() + body + f"</{name}>".encode()


def _code(t: str, table: dict[str, int]) -> int:
    return int(t[3:]) if t.startswith("str") and t != "strL" else table[t]


def _dta(release: int, order: str, variables=VARS, rows: int = 2, *, label: str = "Test data") -> bytes:
    """A .dta file's bytes, data rows all zeros."""
    e, k = ("<" if order == "LSF" else ">"), len(variables)
    row = sum(int(v[1][3:]) if v[1].startswith("str") and v[1] != "strL" else WIDTH[v[1]] for v in variables)
    if release in OLD:
        fmt_w = 12 if release == 113 else 49
        out = bytes([release, 1 if order == "MSF" else 2, 1, 0]) + struct.pack(e + "HI", k, rows)
        out += _pad(label, 81, "latin-1") + _pad(STAMP, 18, "latin-1") + bytes(_code(v[1], OLD_CODE) for v in variables)
        out += b"".join(_pad(v[0], 33, "latin-1") for v in variables) + b"\0" * (2 * (k + 1))
        out += b"".join(_pad(v[2], fmt_w, "latin-1") for v in variables)
        out += b"".join(_pad(v[4], 33, "latin-1") for v in variables)
        out += b"".join(_pad(v[3], 81, "latin-1") for v in variables)
        note = _pad("_dta", 33, "latin-1") + _pad("note0", 33, "latin-1") + b"1\0"
        out += b"\x01" + struct.pack(e + "I", len(note)) + note + b"\0" * 5 + b"\0" * (rows * row)
        return out + struct.pack(e + "I", len(TABLE)) + _pad("grp", 33, "latin-1") + b"\0\0\0" + TABLE
    enc, wide = ("latin-1" if release == 117 else "utf-8"), release in (119, 121)
    name_w, fmt_w, lab_w = (33, 49, 81) if release == 117 else (129, 57, 321)
    raw_label = label.encode(enc)
    head = (b"<stata_dta><header><release>%d</release><byteorder>%s</byteorder><K>" % (release, order.encode())
            + struct.pack(e + ("I" if wide else "H"), k) + b"</K><N>"
            + struct.pack(e + ("I" if release == 117 else "Q"), rows) + b"</N><label>"
            + struct.pack(e + ("B" if release == 117 else "H"), len(raw_label)) + raw_label
            + b"</label><timestamp>" + bytes([len(STAMP)]) + STAMP.encode() + b"</timestamp></header>")
    sections = [
        _tag("variable_types", struct.pack(f"{e}{k}H", *(_code(v[1], NEW_CODE) for v in variables))),
        _tag("varnames", b"".join(_pad(v[0], name_w, enc) for v in variables)),
        _tag("sortlist", b"\0" * ((4 if wide else 2) * (k + 1))),
        _tag("formats", b"".join(_pad(v[2], fmt_w, enc) for v in variables)),
        _tag("value_label_names", b"".join(_pad(v[4], name_w, enc) for v in variables)),
        _tag("variable_labels", b"".join(_pad(v[3], lab_w, enc) for v in variables)),
        b"<characteristics></characteristics>", _tag("data", b"\0" * (rows * row)), b"<strls></strls>",
        _tag("value_labels", b"<lbl>" + struct.pack(e + "I", len(TABLE)) + _pad("grp", name_w, enc) + b"\0\0\0"
             + TABLE + b"</lbl>"),
    ]
    offsets, at = [0, len(head)], len(head) + len(b"<map>") + 112 + len(b"</map>")
    for section in sections:
        offsets.append(at)
        at += len(section)
    offsets += [at, at + len(b"</stata_dta>")]
    return head + b"<map>" + struct.pack(e + "14Q", *offsets) + b"</map>" + b"".join(sections) + b"</stata_dta>"


def _variables(release: int) -> list[tuple[str, str, str, str, str]]:
    return VARS + ([STRL] if release >= 117 else []) + ([ALIAS] if release in (120, 121) else [])


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def _same(a: dta.Header, b: dta.Header) -> bool:
    return dataclasses.replace(a, bytes_read=0) == dataclasses.replace(b, bytes_read=0)


@contextlib.contextmanager
def _deadline(seconds: int):
    """A hang fails the test instead of stopping the suite (where SIGALRM exists)."""
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def fail(*_args):
        raise AssertionError(f"no answer within {seconds} s")

    previous = signal.signal(signal.SIGALRM, fail)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


RELEASES = [113, 114, 115, 117, 118, 119, 120, 121]


@pytest.mark.parametrize("order", ["LSF", "MSF"])
@pytest.mark.parametrize("release", RELEASES)
def test_every_release_in_both_byte_orders_gives_rows_variables_label_and_timestamp(tmp_path, release, order):
    """15.4: releases 113-121, little- and big-endian: rows, each
    variable's name, storage type, display format, label and value-label
    name, the dataset label and its timestamp; strL (117+) and alias
    variables (120, 121) included."""
    variables = _variables(release)
    header = dta.read_header(_write(tmp_path / "t.dta", _dta(release, order, variables, rows=3, label="café")))
    assert (header.release, header.byteorder, header.rows) == (release, order, 3)
    assert header.variables == tuple(dta.Variable(*v) for v in variables)
    assert (header.label, header.timestamp) == ("café", STAMP)
    assert header.summary() == {"format": "dta", "columns": [v[0] for v in variables], "rows": 3, "rows_complete": True}


def test_the_cost_does_not_grow_with_rows(tmp_path):
    """15.4: the data section is never read -- three rows and 200,000 rows
    cost the same bytes."""
    small = dta.read_header(_write(tmp_path / "small.dta", _dta(118, "LSF", rows=3)))
    large = dta.read_header(_write(tmp_path / "large.dta", _dta(118, "LSF", rows=200_000)))
    assert large.rows == 200_000 and large.bytes_read == small.bytes_read < 5_000


@pytest.mark.parametrize("release", [114, 117, 118, 121])
def test_a_stream_gives_the_same_header_and_stops_before_the_data(release):
    """A version in git is read from `git cat-file`'s stream: the same
    header, and nothing past the start of the data section."""
    data = _dta(release, "MSF", _variables(release), rows=50_000)
    stream = io.BytesIO(data)
    header = dta.read_header_stream(stream, len(data))
    assert header.rows == 50_000 and [v.name for v in header.variables] == [v[0] for v in _variables(release)]
    data_starts = data.index(b"<data>") + len(b"<data>") if release >= 117 else len(data) - 40 - len(TABLE) - 50_000 * 25
    assert stream.tell() <= data_starts


@pytest.mark.parametrize("release,order", [(113, "MSF"), (114, "LSF"), (115, "MSF"), (117, "MSF"), (118, "LSF"),
                                           (119, "MSF"), (121, "LSF")])
def test_every_truncation_is_refused(tmp_path, release, order):
    """15.4, 15.6 #5: a file cut at any length is refused. The one cut no
    reader can see is an old file (113-115, no map) cut exactly at the end
    of its data, losing only its value labels -- then rows and variables
    are still what the whole file says."""
    whole = _dta(release, order, _variables(release))
    good = dta.read_header(_write(tmp_path / "whole.dta", whole))
    invisible = {len(whole) - 40 - len(TABLE)} if release in OLD else set()
    cut = tmp_path / "cut.dta"
    accepted = set()
    with _deadline(60):
        for n in range(len(whole)):
            cut.write_bytes(whole[:n])
            try:
                got = dta.read_header(cut)
            except dta.DtaError as exc:
                assert exc.code in (dta.TRUNCATED, dta.NOT_DTA, dta.CORRUPT), (n, exc)
                continue
            assert _same(got, good), n
            accepted.add(n)
    assert accepted == invisible


def test_byte_mutations_are_refused_or_leave_rows_and_variables_alone(tmp_path):
    """15.4: random single-byte changes in the header and its sections
    (seeded): each read answers within the deadline with a header or a
    DtaError, nothing else; an accepted change never alters the rows or the
    number of variables."""
    rng = random.Random(20261017)
    slowest = 0.0
    target = tmp_path / "m.dta"
    with _deadline(120):
        for release, order in ((113, "MSF"), (115, "LSF"), (117, "LSF"), (118, "MSF"), (119, "LSF"), (120, "MSF")):
            whole = _dta(release, order, _variables(release))
            good = dta.read_header(_write(target, whole))
            region = whole.index(b"<data>") if release >= 117 else len(whole) - 40 - len(TABLE) - 2 * 25
            for _ in range(400):
                mutated = bytearray(whole)
                at = rng.randrange(region)
                mutated[at] = (mutated[at] + rng.randrange(1, 256)) % 256
                target.write_bytes(mutated)
                started = time.perf_counter()
                try:
                    got = dta.read_header(target)
                except dta.DtaError:
                    continue
                finally:
                    slowest = max(slowest, time.perf_counter() - started)
                assert (got.rows, len(got.variables)) == (good.rows, len(good.variables)), (release, at)
    assert slowest < 1.0


def test_folders_fifos_devices_and_junk_are_refused_before_any_read(tmp_path):
    """15.4: nothing that is not a regular .dta file is read, and a FIFO
    with no writer does not hang the reader."""
    cases = {tmp_path: dta.NOT_A_FILE, tmp_path / "missing.dta": dta.UNREADABLE,
             _write(tmp_path / "empty.dta", b""): dta.NOT_DTA, _write(tmp_path / "ten.dta", b"0123456789"): dta.NOT_DTA,
             _write(tmp_path / "png.dta", b"\x89PNG\r\n\x1a\n" + b"\0" * 64): dta.NOT_DTA,
             _write(tmp_path / "noise.dta", random.Random(7).randbytes(4096)): dta.NOT_DTA}
    if hasattr(os, "mkfifo"):
        os.mkfifo(tmp_path / "pipe.dta")
        cases[tmp_path / "pipe.dta"] = dta.NOT_A_FILE
    if os.path.exists("/dev/zero"):
        cases[Path("/dev/zero")] = dta.NOT_A_FILE
    with _deadline(10):
        for path, code in cases.items():
            with pytest.raises(dta.DtaError) as caught:
                dta.read_header(path)
            assert caught.value.code == code, path


def test_an_absurd_number_of_variables_is_refused_before_the_sections(tmp_path):
    """15.4: a header claiming 4,294,967,295 variables is refused at once,
    before any section is read; the limit can be set lower."""
    head = (b"<stata_dta><header><release>119</release><byteorder>LSF</byteorder><K>" + struct.pack("<I", 2**32 - 1)
            + b"</K><N>" + struct.pack("<Q", 1) + b"</N><label>" + b"\0" * 64)
    with pytest.raises(dta.DtaError) as caught:
        dta.read_header(_write(tmp_path / "wide.dta", head))
    assert caught.value.code == dta.TOO_WIDE and "4294967295 variables" in str(caught.value)
    with pytest.raises(dta.DtaError) as caught:
        dta.read_header(_write(tmp_path / "five.dta", _dta(118, "LSF", _variables(118))), max_variables=4)
    assert caught.value.code == dta.TOO_WIDE


@pytest.mark.parametrize("release,old,new,code,words", [
    (118, b"<release>118", b"<release>116", dta.UNSUPPORTED, "release '116'"),
    (114, b"\x72\x02\x01", b"\x6e\x02\x01", dta.UNSUPPORTED, "Stata 7 or earlier"),
    (118, b">LSF<", b">XYZ<", dta.CORRUPT, "neither LSF nor MSF"),
    (118, b"<variable_types>\xf8\xff", b"<variable_types>\x40\x9c", dta.CORRUPT, "unknown type code 40000"),
    (118, b"<sortlist>\x00\x00", b"<sortlist>\x09\x00", dta.CORRUPT, "names variable 9"),
    (118, b"<data>", b"<dat4>", dta.CORRUPT, "<data> expected"),
    (118, b"</stata_dta>", b"</stata_dtX>", dta.CORRUPT, "</stata_dta> expected"),
])
def test_corruptions_are_named(tmp_path, release, old, new, code, words):
    """15.4, 15.6 #5: a corrupt file is refused with a sentence saying what
    is wrong."""
    base = _dta(release, "LSF", VARS)
    assert base.count(old) >= 1
    with pytest.raises(dta.DtaError) as caught:
        dta.read_header(_write(tmp_path / "bad.dta", base.replace(old, new, 1)))
    assert caught.value.code == code and words in str(caught.value)


def test_names_and_labels_are_untrusted_text(tmp_path):
    """15.4: control and bidi characters never reach the screen as they
    are, and bytes that are not UTF-8 are replaced."""
    variables = [("v1", "byte", "%8.0g", "red\x1b[31mtext", ""), ("v2", "byte", "%8.0g", "a‮b", ""),
                 ("v3", "byte", "%8.0g", b"caf\xe9", "")]
    header = dta.read_header(_write(tmp_path / "t.dta", _dta(118, "LSF", variables)))
    assert [v.label for v in header.variables] == ["red�[31mtext", "a�b", "caf�"]

"""Research OS V16, task AF-1 item 6 (DESIGN.md 21.8 'Numbers in tables'): a long number is shown to four significant digits.

`RODesk.formatNumber` is the one function the app uses for it (the handback's key numbers now; the rest of the app in
batch 5): a plain decimal with more than six significant digits is shown to four, anything else -- an integer, six digits
or fewer, a value with a unit, a percent sign, a word or a separator -- as written. The value as written is the caller's
to keep (on hover and in the record). Every test names its scenario."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from test_v9_language import WEBAPP

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _node(expression: str) -> object:
    """`expression` evaluated next to the real desk.js (a stand-alone window, as tests/test_v8_u2.py loads it)."""
    script = ("global.window = {}; global.document = {getElementById(){return null}};\n"
              + (WEBAPP / "desk.js").read_text(encoding="utf-8")
              + f"\nprocess.stdout.write(JSON.stringify({expression}));\n")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(out.stdout)


def _formatted(values: list) -> list:
    return _node(f"{json.dumps(values, ensure_ascii=False)}.map((v) => window.RODesk.formatNumber(v))")


@needs_node
def test_a_value_with_more_than_six_significant_digits_is_shown_to_four_in_the_form_it_was_written():
    long = ["9.509441061106294e-05", "0.008063173780026744", "-0.008063173780026744", "1.234567", "0.1234567", "+1.2345678",
            "1.2345678e10", "1.2345678E-10", "12345678e-3", "1.500000", "100.0000", "0.00000123456789"]
    assert _formatted(long) == ["9.509e-05", "0.008063", "-0.008063", "1.235", "0.1235", "+1.235",
                                "1.235e+10", "1.235E-10", "1.235e+04", "1.500", "100.0", "0.000001235"]


@needs_node
def test_rounding_carries_into_the_next_digit_and_a_large_plain_number_is_not_padded_with_zeros():
    assert _formatted(["9.999961", "99999.99", "1234.5678", "123456.789", "0.0000099999961", "-99.999999"]) == [
        "10.00", "1.000e+05", "1235", "1.235e+05", "0.00001000", "-100.0"]
    # the fifth digit decides, half up: 1.2345 is not an even more careful 1.234
    assert _formatted(["1.2345000", "1.2344999"]) == ["1.235", "1.234"]


@needs_node
def test_six_digits_or_fewer_integers_and_anything_that_is_not_a_plain_decimal_are_shown_as_written():
    as_written = ["1.82", "0.5", "123456", "1234567", "12345678901234567890", "-42", "0", "0.000", "0.0000000", "1.23456", "0.123456",
                  "123456.", "1e5", "1.5E-7", "12.5%", "3.2 ms", "p<0.001", "1,234,567.891", "0.1234567 (p)", "−0.1234567",
                  " 0.1234567", "0.1234567 ", "abc", "", ".", "e5", "-", "NaN", "Infinity", "0x1234567"]
    assert _formatted(as_written) == as_written
    assert _formatted([None, 12, 0.5]) == ["", "12", "0.5"]


@needs_node
def test_a_number_that_is_not_text_is_read_by_the_way_the_page_prints_it():
    assert _formatted([0.008063173780026744, 1234567.891, 5]) == ["0.008063", "1.235e+06", "5"]


@needs_node
def test_whatever_it_shortens_stays_within_half_a_unit_of_the_fourth_digit_and_never_shows_more_than_four():
    failures = _node(r"""(() => {
      let seed = 20261030; const next = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
      const bad = [];
      for (let i = 0; i < 4000; i++) {
        const count = 7 + next(14), digits = String(1 + next(9)) + Array.from({ length: count - 1 }, () => next(10)).join("");
        const at = next(count + 1), text = (next(2) ? "-" : "") + digits.slice(0, at) + "." + digits.slice(at) + (next(3) ? "" : "e" + (next(2) ? "-" : "+") + String(next(30)).padStart(2, "0"));
        const shown = window.RODesk.formatNumber(text), plain = /^[+-]?(\d*)\.?(\d*)(?:[eE][+-]?\d+)?$/.exec(shown);
        const significant = plain ? (plain[1] + plain[2]).replace(/^0+/, "") : "";
        const was = Number(text), now = Number(shown);
        const parts = /^[+-]?(\d*)\.?(\d*)/.exec(text), written = (parts[1] + parts[2]).replace(/^0+/, "");
        if (written.length <= 6) { if (shown !== text) bad.push([text, shown, "shortened"]); continue; }
        if (!plain || significant.length !== 4) bad.push([text, shown, "digits"]);
        if (!(Math.abs(now - was) <= Math.abs(was) * 5.0001e-4)) bad.push([text, shown, "far"]);
      }
      return bad.slice(0, 5);
    })()""")
    assert failures == []


@needs_node
def test_the_formatter_is_on_rodesk_for_the_rest_of_the_app_and_does_not_touch_the_value_it_is_given():
    assert _node("typeof window.RODesk.formatNumber") == "function"
    # a pure function: the same text gives the same answer, and the caller's string is not changed
    assert _node(r"""(() => { const v = "9.509441061106294e-05"; const a = window.RODesk.formatNumber(v), b = window.RODesk.formatNumber(v); return [a, b, v]; })()""") == [
        "9.509e-05", "9.509e-05", "9.509441061106294e-05"]

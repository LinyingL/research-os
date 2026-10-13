"""V8 U5: the last sweep of every screen for 12.11 and 8.8 (the native
shell's menus are tested in test_webapp_macapp.py).

- No control is labelled with a glyph: the side panel, the notices and the
  scan chip close with the word 关闭, the canvas's menu is 更多.
- The native shell's own words carry no emoji and no arrow characters.
- At the window's minimum (900x600) nothing is cut off without the full
  text on hover: the project menu keeps to the sidebar's width, a trace
  card (this node's own included) and the canvas's scope carry their full
  name as a title.
"""

from __future__ import annotations

import re
from pathlib import Path

from test_v8_u2 import FORBIDDEN, html_screen_text, js_strings

WEBAPP = Path(__file__).parents[1] / "src" / "researchos" / "webapp"
APP_HTML = (WEBAPP / "app.html").read_text(encoding="utf-8")

# A control whose whole label is a glyph standing for a word (the middle
# dot and the ellipsis are punctuation in running text and joins, not this).
GLYPH_LABEL = re.compile(r"^\s*(?:[×✕✖✗⋯≡☰]|&times;)\s*$")


def _page_strings() -> list[tuple[str, str]]:
    found = [("app.html markup", t) for t in html_screen_text(APP_HTML)]
    for block in re.findall(r"<script>(.*?)</script>", APP_HTML, re.S):
        found += [("app.html script", s) for _, s in js_strings(block)]
    for name in re.findall(r'<script src="/([\w.-]+\.js)"></script>', APP_HTML):
        found += [(name, s) for _, s in js_strings((WEBAPP / name).read_text(encoding="utf-8"))]
    return found


def test_no_control_is_labelled_with_a_glyph():
    offenders = [f"{where}: {s!r}" for where, s in _page_strings() if GLYPH_LABEL.match(s)]
    assert offenders == []
    # Re-baselined for V10 (DESIGN.md 14.6): the side panel opens on the desk, a converted surface,
    # so its 关闭 is marked for the English table -- still the word, never a glyph.
    assert '<button class="panel-close" id="panel-close" type="button" data-i18n>关闭</button>' in APP_HTML
    assert APP_HTML.count('mkButton("关闭", "scan-chip-close", hideScanChip)') == 2
    assert 'mkButton("关闭", "notice-close", onClose)' in APP_HTML
    canvas = (WEBAPP / "canvas.js").read_text(encoding="utf-8")
    assert 'button("更多", "",' in canvas


def test_the_scanner_catches_a_glyph_label():
    assert GLYPH_LABEL.match("×") and GLYPH_LABEL.match(" ⋯ ") and GLYPH_LABEL.match("&times;")
    assert not GLYPH_LABEL.match("关闭") and not GLYPH_LABEL.match("载入中…") and not GLYPH_LABEL.match(" · ")


def _swift_strings(src: str) -> list[str]:
    src = re.sub(r"//[^\n]*", "", src)
    return re.findall(r'"((?:[^"\\\n]|\\.)*)"', src)


def test_the_native_shell_writes_no_emoji_and_no_arrow():
    for name in ("RCEShell.swift", "RCEIcon.swift"):
        strings = _swift_strings((WEBAPP / "shell" / name).read_text(encoding="utf-8"))
        assert strings, name
        assert [s for s in strings if FORBIDDEN.search(s)] == [], name
    shell = (WEBAPP / "shell" / "RCEShell.swift").read_text(encoding="utf-8")
    # every menu title the shell writes is Chinese (the brand apart)
    titles = re.findall(r'(?:item|cmd|submenu)\("([^"]+)"', shell)
    assert titles and [t for t in titles if re.search(r"[A-Za-z]", t) and "Research OS" not in t] == []


def test_the_project_menu_keeps_to_the_sidebar():
    assert ".side-project .project-menu { left: -0.65rem; right: -0.65rem; min-width: 0; max-width: none; }" in APP_HTML
    sidebar = APP_HTML[APP_HTML.index(".sidebar {"):]
    sidebar = sidebar[: sidebar.index("}")]
    assert "overflow-y: auto; overflow-x: hidden;" in sidebar
    # a long name is cut with an ellipsis and its full path is on hover
    assert ".pm-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }" in APP_HTML
    assert 'role: "menuitemradio", mark: current, title: p.path,' in APP_HTML


def test_a_cut_short_name_is_on_hover():
    node = (WEBAPP / "node.js").read_text(encoding="utf-8")
    item = node[node.index("function traceItem"):]
    item = item[: item.index("\n  }\n")]
    # outside the `if (!self)` block: this node's own card too
    assert item.index("el.title = item.path || item.file || item.label;") > item.index("if (!self) {")
    assert "\n    el.title = item.path || item.file || item.label;" in item
    canvas = (WEBAPP / "canvas.js").read_text(encoding="utf-8")
    scope = canvas[canvas.index("function renderScopeSelect"):]
    scope = scope[: scope.index("\n  }\n")]
    assert 'sel.title = chosen ? "范围：" + chosen.textContent : "范围";' in scope


def test_a_menu_command_lands_with_air_above_it():
    assert "[data-anchor] { scroll-margin-top: 1.2rem; }" in APP_HTML

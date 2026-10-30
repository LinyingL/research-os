"""Research OS V16, task B6, finding 2 (DESIGN.md 21.8): a panel that opens with a different subject starts at the top.

The researcher scrolled 你的记录与备份 to 600 px, pressed 关闭, opened 设置 from the sidebar, and the new panel opened in its middle:
`panel-body.scrollTop` was still 600. `openPanel` (app.html) never reset it, and a hidden panel (`display: none`) has no box to
scroll, so the reset has to come after the panel is shown. The page's own functions run under node on the stand-in DOM of the
panel tests, with a model of how a browser scrolls (an element with no box ignores `scrollTop = n`); the real browser run is a
separate check made in headless Chrome. Every test names its scenario."""

from __future__ import annotations

import json
import subprocess

import pytest

from test_v10_desk_panel import _PANEL_DOM, A16, A17, _case
from test_v16_b53_acceptance import _QUANT_ROWS
from test_v9_language import FILL_VARS_LINE, T_LINE, WEBAPP, _app_block, _i18n_source, _node, needs_node

#: The stand-in DOM of the panel tests without its stand-in for `openPanel`: this file runs the page's own.
_STUB = "global.openPanel = () => { parts.panel.classList.remove(\"hidden\"); parts[\"panel-backdrop\"].classList.remove(\"hidden\"); };\n"
assert _STUB in _PANEL_DOM
_DOM = _PANEL_DOM.replace(_STUB, "")

#: The page's own panel code: openPanel, closePanel, 返回 (21.8), and 你的记录与备份 (21.7), unchanged.
_PANEL_BLOCK = _app_block("// -- Panel and 返回 (DESIGN.md 21.8)", "// -- end of panel and 返回")
_RECORDS_BLOCK = _app_block("// -- 你的记录与备份 (9.2", 'document.getElementById("records-btn")')

_PRELUDE = r"""
global.window = {};
%(i18n)s
%(dom)s
%(fill)s
parts["panel-back"] = el("button", "panel-back hidden");
global.panel = parts.panel; global.panelBackdrop = parts["panel-backdrop"];
global.panelTitle = parts["panel-title"]; global.panelSubtitle = parts["panel-subtitle"]; global.panelBody = parts["panel-body"];
global.endPanelResize = () => {};
global.chevronIcon = () => el("svg", "res-chevron");
global.uiLang = () => (window.ROI18N ? window.ROI18N.lang : "zh");
global.renderBlockingError = (node, framing) => { node.textContent = framing; };
global.renderErrorBox = (node, err) => { node.textContent = "ERROR " + err.message; };
global.text = (e) => e.textContent;
global.byClass = (host, c) => host.all().filter((k) => k.cls.has(c));
window.scrollTo = () => {};
global.currentView = "desk";
global.activateView = (name) => { global.currentView = name; };
// How a browser scrolls: an element with no box (the panel is `display: none` while hidden) ignores `scrollTop = n` and keeps
// what it had; a shown one takes it, within what it can scroll (a long panel: 5000 px).
let raw = 0;
Object.defineProperty(panelBody, "scrollTop", {
  get() { return parts.panel.cls.has("hidden") ? raw : Math.min(raw, 5000); },
  set(v) { if (!parts.panel.cls.has("hidden")) raw = Math.max(0, Math.min(5000, Number(v) || 0)); },
});
global.at = () => panelBody.scrollTop;
global.scroll = (n) => { panelBody.scrollTop = n; return at(); };
%(back)s
%(desk)s
global.RODesk = window.RODesk;
%(node)s
global.RONode = window.RONode;
%(records)s
"""


def _run(body: str, lang: str | None = None) -> dict:
    """The panel code of the page and desk.js's panels on a desk with attempts #16 and #17, `body` run on it (async)."""
    case = _case("single", lang or "zh")
    page = case["get"]["/api/node?node=" + A16.replace(":", "%3A").replace("#", "%23")]
    a17 = A17.replace(":", "%3A").replace("#", "%23")
    case["get"][f"/api/node?node={a17}"] = {**page, "id": A17}
    case["get"][f"/api/notes?node={a17}"] = {"notes": []}
    i18n = "" if lang is None else f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    script = _PRELUDE % {"i18n": i18n, "dom": _DOM, "fill": FILL_VARS_LINE + "\n" + T_LINE, "back": _PANEL_BLOCK,
                         "desk": (WEBAPP / "desk.js").read_text(encoding="utf-8"), "node": (WEBAPP / "node.js").read_text(encoding="utf-8"),
                         "records": _RECORDS_BLOCK}
    script += r"""
(async () => {
  const c = %(case)s, A16 = %(a16)s, A17 = %(a17)s;
  await openDesk(c);
  const deskGet = global.apiGet;
  global.apiGet = async (path) => (path === "/api/records" ? { rows: %(rows)s } : deskGet(path));
  const out = {};
  global.tick = () => new Promise((r) => setTimeout(r, 5));
%(body)s
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
""" % {"case": json.dumps(case, ensure_ascii=False), "a16": json.dumps(A16), "a17": json.dumps(A17),
       "rows": json.dumps(_QUANT_ROWS), "body": body}
    try:
        return _node(script)
    except subprocess.CalledProcessError as exc:
        raise AssertionError(exc.stderr[-2500:]) from exc


@needs_node
def test_a_panel_opened_after_another_was_closed_starts_at_the_top():
    """The finding: 你的记录与备份 scrolled to 600, 关闭, then 设置 -- here an attempt's panel, a record panel again and a panel
    whose opener draws its content after an await -- each opens at the top, never in the middle of the one that was closed."""
    out = _run(r"""
  await openRecordsPanel(); await tick();
  out.recordsOpens = at(); out.recordsScrolled = scroll(600);
  closePanel();
  await window.RODesk.openAttempt(A16); await tick();
  out.attemptAfterClose = at();
  scroll(600); closePanel();
  await openRecordsPanel(); await tick();
  out.recordsAfterAttempt = at();
""")
    assert out["recordsOpens"] == 0 and out["recordsScrolled"] == 600
    assert (out["attemptAfterClose"], out["recordsAfterAttempt"]) == (0, 0)


@needs_node
def test_a_panel_opened_from_inside_another_starts_at_the_top_and_so_does_the_one_that_return_leads_back_to():
    """An attempt's panel at 600 opens another attempt's (from a row of its own), then 你的记录与备份 from the sidebar (the panel is on
    screen the whole time); 返回 in the records panel opens the attempt's panel fresh -- at the top, not where it was left."""
    out = _run(r"""
  await window.RODesk.openAttempt(A16); await tick();
  out.firstScrolled = scroll(600);
  await window.RODesk.openAttempt(A17); await tick();
  out.otherAttempt = at();
  out.scrolledAgain = scroll(700);
  await openRecordsPanel(); await tick();
  out.records = at(); out.backShown = !parts["panel-back"].cls.has("hidden");
  scroll(300);
  parts["panel-back"].click(); await tick(); await tick();
  out.afterReturn = at(); out.subject = state.panelSubject.id;
""")
    assert out["firstScrolled"] == 600 and out["scrolledAgain"] == 700
    assert (out["otherAttempt"], out["records"]) == (0, 0)
    assert out["backShown"] is True and out["subject"] == A17 and out["afterReturn"] == 0


@needs_node
def test_a_node_page_opened_from_a_panel_gives_the_panel_back_at_the_top():
    """打开详情页 from an attempt's panel scrolled to 600: the page covers the panel (it closes), and its 返回 opens the attempt's panel
    again -- fresh, at the top."""
    out = _run(r"""
  global.views.node = el("div", "node");
  await window.RODesk.openAttempt(A16); await tick();
  out.scrolled = scroll(600);
  window.RONode.openNode("script:steps/16-build.py");
  out.closedWhileOnPage = parts.panel.cls.has("hidden");
  window.RONode._back(); await tick(); await tick();
  out.reopened = state.panelSubject && state.panelSubject.id; out.afterBack = at();
""")
    assert out["scrolled"] == 600 and out["closedWhileOnPage"] is True
    assert out["reopened"] == A16 and out["afterBack"] == 0


@needs_node
def test_an_opener_that_draws_before_it_opens_and_one_that_draws_after_both_end_at_the_top():
    """Openers differ in order: some build the whole panel and then call `openPanel` (设置, a file), some call it first and fill the
    body after an await (the records, a handback). Whichever it is, once the content is in, the place is the top -- with the panel
    closed at 600 (a hidden panel cannot be scrolled), and with another panel on screen at 600."""
    out = _run(r"""
  const opener = async (kind, early) => {
    const subject = { kind: kind };
    state.panelSubject = subject;
    panelTitle.textContent = kind;
    if (early) { panelBody.replaceChildren(el("p", "empty-note", "loading")); openPanel(); await tick(); }
    else await tick();
    panelBody.replaceChildren(...Array.from({ length: 80 }, (_, i) => el("p", null, kind + i)));
    if (!early) openPanel();
    await tick();
    return at();
  };
  await openRecordsPanel(); await tick();
  scroll(600); closePanel();
  out.lateWhileClosed = await opener("settings", false);
  scroll(600); closePanel();
  out.earlyWhileClosed = await opener("handback", true);
  scroll(600);
  out.lateOverAnother = await opener("file", false);
  scroll(600);
  out.earlyOverAnother = await opener("backfill", true);
""")
    assert out == {"lateWhileClosed": 0, "earlyWhileClosed": 0, "lateOverAnother": 0, "earlyOverAnother": 0}


@needs_node
def test_a_panel_drawn_again_with_the_same_subject_keeps_its_place_and_a_refresh_does_not_jump_to_the_top():
    """The places that keep the place today keep it: 你的记录与备份 read again when the project moves (`renderRecordsPanel`), an
    attempt's panel drawn again after a write or a poll (`RODesk.revalidatePanel`), and the same subject passed to `openPanel` again by
    an opener that draws the form again (the attempt form's preview step) while it is on screen."""
    out = _run(r"""
  await openRecordsPanel(); await tick();
  out.records = scroll(600);
  await renderRecordsPanel(state.panelSubject); await tick();
  out.recordsRefreshed = at();
  openPanel();
  out.recordsSameSubject = at();
  await window.RODesk.openAttempt(A16); await tick();
  out.attempt = scroll(600);
  await window.RODesk.revalidatePanel(state.panelSubject); await tick();
  out.attemptRefreshed = at();
  await window.RODesk.openAttempt(A16); await tick();
  out.attemptClickedAgain = at();
""")
    assert out == {"records": 600, "recordsRefreshed": 600, "recordsSameSubject": 600, "attempt": 600, "attemptRefreshed": 600,
                   "attemptClickedAgain": 600}


def test_the_reset_is_made_after_the_panel_is_shown_because_a_panel_with_no_box_cannot_be_scrolled():
    """The order inside `openPanel` is the point of the fix: `hidden` is removed first, then the place is set."""
    html = (WEBAPP / "app.html").read_text(encoding="utf-8")
    start = html.index("function openPanel() {")
    body = html[start:html.index("\n}\n", start)]
    assert body.index('panel.classList.remove("hidden")') < body.index("panelBody.scrollTop = 0")

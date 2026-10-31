"""Research OS V16, task B6 review (DESIGN.md 21.5, 21.8): two low findings of the review of fix batch B6, each with its regression test.

 1. 图表与论断's 论断 block. A draft marked 「设为论文正文」 that holds no claims (the project's own 研究问题与计划, which the index
    does not read, is the Quant case) expanded to nothing, with no sentence saying so; and when every mark was of a file that has
    gone, the block read 「展开一份文稿才载入它的论断」 with no row under it -- an instruction to open a draft that is not there.
 2. 「在设置里修改」 left the section it names off screen when the AI 执行者 list drew after the panel was open (the list is read
    from the engine, then the run store, then the model lists): `scrollIntoView` had run against a page that was a loading line
    long, and nothing took the place again once the box filled in.

The pages run under node on the stand-in DOMs of the other tests, in both languages where they say anything. Every test names its scenario."""

from __future__ import annotations

import json

import pytest

from researchos.records import papers

from test_v16_b6_acceptance import FACTORS, FRAMEWORK, GONE, OVERVIEW, _quant
from test_v16_paper_rows_js import _page_run
from test_v16_provenance import _results
from test_v16_settings import SETTINGS_BLOCK, _page
from test_v9_language import needs_node


def _w(lang: str):
    return lambda zh, en: en if lang == "en" else zh


# -- 1. a marked draft with no claims says so; a block with nothing to open does not tell the researcher to open one ------------


_CLAIMS_BLOCK = r"""
(async () => {
  const claims = %(claims)s, asked = [];
  global.apiGet = async (path) => {
    calls.push("GET " + path);
    if (path === "/api/results") return stages[0];
    if (path.startsWith("/api/results?draft=")) { const file = decodeURIComponent(path.slice(path.indexOf("=") + 1)); asked.push(file); return claims[file]; }
    throw new Error("no fixture for " + path);
  };
  await window.RONode.loadResults(host, false);
  const block = host.all().filter((e) => e.cls.has("res-block")).pop();
  const out = { intro: byClass(block, "node-sub").map(text) };
  const rows = byClass(block, "res-draft");
  out.names = rows.map((d) => text(byClass(d, "mono")[0]));
  for (const d of rows) { toggle(d); await settle(); await settle(); }
  out.bodies = rows.map((d) => text(byClass(d, "res-draft-body")[0]));
  out.asked = asked;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""

_ONLY_GONE_BLOCK = r"""
(async () => {
  await window.RONode.loadResults(host, false);
  const block = host.all().filter((e) => e.cls.has("res-block")).pop();
  const paper = byClass(host, "res-paper-list")[0];
  process.stdout.write(JSON.stringify({ intro: byClass(block, "node-sub").map(text), rows: byClass(block, "res-draft").length,
    paperRows: paper.all().filter((e) => e.cls.has("res-row")).length, gone: byClass(paper, "res-gone").map(text) }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_marked_draft_with_no_claims_opens_to_a_sentence_saying_so_and_one_with_claims_still_lists_them(lang, tmp_path):
    """Quant: the project's 研究问题与计划 and a design note are marked and read as drafts of no claim (the index does not read the first, and
    the second states no number); the framework has one. Opening the two shows 「这份文稿里没有论断。」 and asks the engine for nothing --
    the row already says 0 --, the framework's row loads its claim as before, and the sentence above the rows still tells to expand a draft,
    since there are rows to expand."""
    w = _w(lang)
    root = _quant(tmp_path)
    data = _results(root)
    claims = {file: _results(root, file) for file in (FRAMEWORK, FACTORS, OVERVIEW)}
    out = _page_run([data], _CLAIMS_BLOCK % {"claims": json.dumps(claims, ensure_ascii=False)}, language=None if lang == "zh" else lang)
    none = w("这份文稿里没有论断。", "This draft has no claims.")
    body = dict(zip(out["names"], out["bodies"]))
    assert sorted(body) == sorted([OVERVIEW, FRAMEWORK, FACTORS])          # the file that has gone is not listed under 论断
    assert body[OVERVIEW] == none and body[FACTORS] == none
    assert "0.42" in body[FRAMEWORK] and none not in body[FRAMEWORK]          # its claim is listed, not the sentence
    assert out["asked"] == [FRAMEWORK]
    assert out["intro"] == [w("只列出设为论文正文的文稿里的论断；展开一份文稿才载入它的论断。",
                              "Only drafts marked as the paper contribute claims. Expand a draft to load them.")]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_draft_that_holds_claims_the_page_was_not_told_of_still_ends_in_the_sentence_not_in_an_empty_body(lang, tmp_path):
    """The list says 1 claim and the engine, asked for the draft, answers none (a file edited between the two reads): the body is the
    sentence, never an empty box."""
    w = _w(lang)
    root = _quant(tmp_path)
    data = _results(root)
    claims = {FRAMEWORK: {**_results(root, FRAMEWORK), "sections": [], "total": 0, "shown": 0}}
    out = _page_run([data], _CLAIMS_BLOCK % {"claims": json.dumps(claims, ensure_ascii=False)}, language=None if lang == "zh" else lang)
    body = dict(zip(out["names"], out["bodies"]))
    assert body[FRAMEWORK] == w("这份文稿里没有论断。", "This draft has no claims.")


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_when_every_mark_is_of_a_file_that_has_gone_the_block_says_so_and_does_not_tell_to_expand_a_draft(lang, tmp_path):
    """Only 「research/design/old_sketch.txt」 is marked and its file has gone: 论文正文 keeps its row (and the way to take the mark back),
    the 论断 block has no row and one short sentence in place of 「展开一份文稿才载入它的论断」."""
    w = _w(lang)
    root = _quant(tmp_path)
    for file in (OVERVIEW, FRAMEWORK, FACTORS):
        papers.set_marked(root, file, False, via="app")
    out = _page_run([_results(root)], _ONLY_GONE_BLOCK, language=None if lang == "zh" else lang)
    assert out["rows"] == 0 and out["paperRows"] == 1 and out["gone"] == [w("这个文件现在不在项目里", "This file is no longer in the project")]
    assert out["intro"] == [w("设为论文正文的文件现在不在项目里，所以这里没有论断。",
                              "No file marked as the paper is in the project now, so there are no claims here.")]
    assert GONE in str(papers.marked(root)["drafts"])


# -- 2. 「在设置里修改」 keeps the section it names at the top while the boxes above it fill in --------------------------------


#: A model of how a browser lays the panel out and scrolls it: an element is as tall as its parts (a part with nothing in it, 40 px; the
#: agents' box, what the test puts in it), the panel shows 500 px, and `scrollIntoView` puts a section's top at the top of the view as far
#: as the content allows -- a page too short to scroll that far stops where it can. `ResizeObserver` reports what changed size when the
#: test says the browser has laid out again.
_PLACE = r"""
const VIEW = 500;
const baseEl = el;
el = function (tag, cls, text) {
  const e = baseEl(tag, cls, text);
  if (e.tag === "SECTION") e.scrollIntoView = () => { panelBody.scrollTop = Math.min(offsetOf(e), Math.max(0, total() - VIEW)); };
  return e;
};
const heightOf = (e) => (e.h != null ? e.h : e.kids.some((k) => typeof k !== "string") ? e.kids.filter((k) => typeof k !== "string").reduce((n, k) => n + heightOf(k), 0) : 40);
const total = () => panelBody.kids.reduce((n, k) => n + heightOf(k), 0);
const offsetOf = (section) => panelBody.kids.slice(0, panelBody.kids.indexOf(section)).reduce((n, k) => n + heightOf(k), 0);
const sectionOf = (key) => panelBody.querySelectorAll("section").find((s) => s.dataset.section === key);
const atTop = (key) => { const s = sectionOf(key); return panelBody.scrollTop === Math.min(offsetOf(s), Math.max(0, total() - VIEW)); };
const ros = [];
global.ResizeObserver = class { constructor(cb) { this.cb = cb; this.seen = []; this.gone = false; ros.push(this); } observe(e) { this.seen.push(e); } disconnect() { this.gone = true; } };
const laidOut = () => ros.filter((r) => !r.gone).forEach((r) => r.cb(r.seen.map((target) => ({ target }))));
panelBody.removeEventListener = (type, fn) => { panelBody.on[type] = (panelBody.on[type] || []).filter((f) => f !== fn); };
const byHand = (type) => (panelBody.on[type] || []).slice().forEach((f) => f({}));
let fill = null, box = null;
const runners = () => Array.from({ length: 6 }, () => Object.assign(el("div", "ws-runner"), { h: 250 }));
// workspace.js draws the loading line into the agents' box, then the list once the engine has answered
window.ROWorkspace.mountSettings = (b) => { box = b; box.appendChild(Object.assign(el("p", "ws-status", "..."), { h: 40 })); fill = () => box.replaceChildren(...runners()); };
"""

_CASES = {
    "cold": r"""
openSettingsPanel("lookup");
out.openedAtTop = atTop("lookup");
out.reachedAtOpen = panelBody.scrollTop;
fill(); laidOut();
out.agentsFilledIn = heightOf(sectionOf("agents")) > 1000;
out.afterFill = atTop("lookup"); out.scrollAfterFill = panelBody.scrollTop;
out.targetOnScreen = offsetOf(sectionOf("lookup")) - panelBody.scrollTop;
byHand("keydown");
""",
    "steps": r"""
openSettingsPanel("language");
fill(); laidOut();
out.afterList = atTop("language");
box.append(...runners().slice(0, 2)); laidOut();                   // the model lists arrive and the list grows again
out.afterModels = atTop("language");
byHand("pointerdown");
""",
    "warm": r"""
window.ROWorkspace.mountSettings = (b) => { box = b; box.replaceChildren(...runners()); };     // the list is already known: drawn at once
openSettingsPanel("lookup");
out.atOpen = atTop("lookup");
laidOut();
out.afterReport = atTop("lookup");
byHand("wheel");
""",
    "hand": r"""
openSettingsPanel("lookup");
byHand("wheel");                                                     // the researcher scrolls before the list is in
panelBody.scrollTop = 7;
fill(); laidOut();
out.left = panelBody.scrollTop;
out.released = ros.every((r) => r.gone);
""",
    "leaves": r"""
openSettingsPanel("lookup");
state.panelSubject = { kind: "file" };                               // another panel took the place
panelBody.scrollTop = 0;
fill(); laidOut();
out.left = panelBody.scrollTop;
out.released = ros.every((r) => r.gone);
""",
}


def _place(case: str) -> dict:
    return _page("zh", SETTINGS_BLOCK, r"""
reply["/api/settings/llm"] = { base_url: "http://localhost:1234/v1", model: "qwen", saved: { base_url: null, model: null }, where: "this_mac", host: "localhost" };
reply["/api/settings/lookup"] = { lookup: { enabled: false } };
""" + _PLACE + _CASES[case] + """
out.observed = ros.map((r) => r.seen.map((s) => s.dataset.section));
""")


@needs_node
def test_the_section_a_link_names_is_at_the_top_after_the_agents_list_has_drawn_below_an_opener_that_drew_before_it(tmp_path):
    """The researcher presses 「在设置里修改」 beside 联网查询 before the AI 执行者 list has been read: at open the page is a loading line
    long and the section is placed as far as that allows; the list then fills in, the browser reports the new sizes, and the section is
    at the top again. (It stayed where it was put, and the section was 1,478 px down the panel, off the screen.)"""
    out = _place("cold")
    assert out["agentsFilledIn"] is True
    assert out["afterFill"] is True and 0 <= out["targetOnScreen"] < 500          # the section is in the view, at its top as far as the content allows
    assert out["observed"] == [["agents", "llm", "lookup", "language"]]      # every section above or below the target is watched


@needs_node
def test_the_place_is_kept_through_every_step_the_box_above_it_grows_in():
    """The list of programs, then the model lists: each growth moves 界面语言 down, and each time the section is put at the top again."""
    out = _place("steps")
    assert out["afterList"] is True and out["afterModels"] is True


@needs_node
def test_a_list_that_is_already_known_is_placed_at_once_and_stays():
    """The second opening in a page: the list is drawn with the panel; the section is at the top at once and the report that follows keeps it."""
    out = _place("warm")
    assert out["atOpen"] is True and out["afterReport"] is True


@needs_node
def test_the_place_is_not_taken_back_once_the_researcher_has_scrolled():
    """The wheel (a touch, a key, a press) ends the hold: a scroll to 7 px stays 7 px when the list arrives, and nothing is watched any more."""
    out = _place("hand")
    assert out["left"] == 7 and out["released"] is True


@needs_node
def test_the_hold_ends_when_another_panel_has_taken_the_place():
    """Another subject on screen: the report that follows moves nothing and the watch is dropped."""
    out = _place("leaves")
    assert out["left"] == 0 and out["released"] is True

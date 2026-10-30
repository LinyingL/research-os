"""Research OS V16, task B5-2 (DESIGN.md 21.8, the last two bullets, and the two carried items of 21.4), the pages under node, in
both languages: every disclosure has 12.13's chevron; every panel that covers the page has the one scrim and a panel opened from
another panel has 返回 (a node page opened from a panel gives the panel back); the route map fits all its cards the first time a
project's map is opened and says how many are out of the view; Chinese text is never set smaller than 12 px; the folded plan
shows its first paragraph whole; a task's opened process files fold after the first few like the other groups. Every test
names its scenario."""

from __future__ import annotations

import json
import re

import pytest

from test_v10_desk_panel import A16, _case
from test_v10_desk_panel import _run as run_desk
from test_v10_fix_map import _MAP_DOM, _desk
from test_v9_language import APP_HTML, WEBAPP, _app_block, _app_function, _i18n_source, _node, needs_node
from test_v16_provenance_js import _run as node_run
from test_workspace_js import DOM, _OUTPUTS, run

pytestmark = needs_node     # every scenario here runs the page scripts under node

LANGS = ["zh", "en"]
SCRIPTS = ("app.html", "node.js", "desk.js", "team.js", "workspace.js", "canvas.js", "routemap.js")


def _w(lang: str):
    return lambda zh, en: en if lang == "en" else zh


def _source(name: str) -> str:
    return (WEBAPP / name).read_text(encoding="utf-8")


# -- Chinese text is never smaller than 12 px -------------------------------------------------------------------------------

#: A size in `em` or per cent is a fraction of the text around it, so it is read only where it is listed: these two are inline code set
#: at 0.85 of the text it sits in (the markdown preview's, 0.9rem and more; the stopped note's, 1rem).
_RELATIVE_SIZES_OK = {".ws-preview code": "inline code in the preview, 0.85 of text of at least 0.9rem",
                      ".stopped-note code": "inline code in a note of 1rem text"}


def _styles() -> dict[str, str]:
    html = _source("app.html")
    return {"app.html": "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S)), "workspace.css": _source("workspace.css")}


def test_no_style_sets_text_smaller_than_twelve_pixels():
    """21.8: Chinese text in the interface is never smaller than 12 px, which is 0.75rem at the browser's 16 px root. Every
    `font-size` and `font` of the page's style block and of workspace.css is read (rem and px by value; em and per cent only on
    the two inline-code rules listed above), and the sizes the scripts set on the canvas for measuring text agree with them."""
    too_small = []
    for name, css in _styles().items():
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
            selector, body = rule.group(1).strip(), rule.group(2)
            for size in re.finditer(r"(?<![\w-])font-size\s*:\s*([\d.]+)(rem|px|em|pt|%)", body):
                value, unit = float(size.group(1)), size.group(2)
                if unit in ("em", "%"):
                    if selector not in _RELATIVE_SIZES_OK:
                        too_small.append((name, selector, size.group(0), "relative size not listed"))
                    continue
                px = value * (16 if unit == "rem" else 4 / 3 if unit == "pt" else 1)
                if px < 12:
                    too_small.append((name, selector, size.group(0)))
            for shorthand in re.finditer(r"(?<![\w-])font\s*:\s*(?:[a-z-]+\s+)*([\d.]+)(rem|px)", body):
                if float(shorthand.group(1)) * (16 if shorthand.group(2) == "rem" else 1) < 12:
                    too_small.append((name, selector, shorthand.group(0)))
    assert too_small == []
    # nothing sets a size from a script (a style attribute, style.fontSize, an SVG font-size)
    for name in SCRIPTS:
        text = _source(name)
        scripts = text if name != "app.html" else "\n".join(re.findall(r"<script>(.*?)</script>", text, re.S))
        assert not re.search(r"fontSize|font-size|style=\\?[\"'][^\"']*font", scripts), name
    markup = re.sub(r"<script>.*?</script>|<style[^>]*>.*?</style>", "", _source("app.html"), flags=re.S)
    assert not re.search(r'style\s*=\s*"[^"]*font', markup)             # nor an inline style in the markup
    # the canvas fits text into its cards by measuring it with the size the stylesheet gives it
    canvas = _source("canvas.js")
    assert re.findall(r'"(\d+)px " \+ fontStack\("--font-(?:sans|mono)"\)', canvas) == ["12", "13", "12"]


# -- 研究问题与计划: the first paragraph, whole -----------------------------------------------------------------------------

_PLAN = r"""
const PLAN = %(plan)s;
listed.documents = [{ path: 'research/overview.md', kind: 'overview', title: 'Plan', excerpt: '' }];
docs['research/overview.md'] = { path: 'research/overview.md', kind: 'overview', title: 'Plan', content: PLAN, revision: 'doc-1' };
await window.ROWorkspace.load(root); await sleepTick(); await sleepTick();
const lead = byClass('ws-plan-lead');
console.log(JSON.stringify({ words: lead.children.map((c) => c.tagName + ':' + c.textContent), title: byClass('ws-plan-title').textContent, text: lead.textContent }));
"""

LONG = ("第一条：Sword 因子在全样本里是否仍有显著的 Alpha，这一条写得很长，需要把样本、频率、成本假设和检验方式都交代清楚，"
        "否则读的人会以为只做了一个简单的回归；第二句补充了样本外的做法。")
CASES = {
    "bullets": ("# 研究问题与计划\n\n- " + LONG + "\n  接着写完这一条的最后一句。\n- 第二条不该出现。\n- 第三条也不该出现。\n\n正文第二段不该出现。",
                ["UL:" + LONG, "P:接着写完这一条的最后一句。"]),
    "lead_in": ("# 计划\n\n研究约束：\n- 约束一：只用公开数据，所有结果都要能复算。\n- 约束二不该出现。\n", ["P:研究约束：", "UL:约束一：只用公开数据，所有结果都要能复算。"]),
    "lead_in_apart": ("# 计划\n\n研究约束：\n\n- 约束一：只用公开数据。\n  第二行仍是约束一。\n- 约束二不该出现。\n", ["P:研究约束：", "UL:约束一：只用公开数据。", "P:第二行仍是约束一。"]),
    "paragraph": ("# 计划\n\n第一行写问题。\n第二行写方法。\n第三行写数据。\n\n第二段不该出现。\n", ["P:第一行写问题。", "P:第二行写方法。", "P:第三行写数据。"]),
    "text_then_list": ("# 计划\n\n先回答一个问题。\n- 列表项不该出现。\n", ["P:先回答一个问题。"]),
    "nested": ("# 计划\n\n- 第一条\n  - 它的子条目\n  - 另一个子条目\n- 第二条不该出现\n", ["UL:第一条它的子条目另一个子条目"]),
}


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("case", sorted(CASES))
def test_the_folded_plan_shows_its_first_paragraph_whole_and_never_cuts_a_sentence(case, lang):
    """21.4: the fold shows the first paragraph -- the first item of a list that opens the plan with the lines that go on with it
    (its deeper items too), a lead-in that ends with a colon with the first item under it (also when a blank line comes between),
    a paragraph with all its lines, text that leads into a list without a colon alone -- and not a word of the second; whatever it
    shows is whole."""
    plan, expected = CASES[case]
    out = run(_PLAN % {"plan": json.dumps(plan)}, lang=lang)
    assert [w.split(":", 1)[0] + ":" + w.split(":", 1)[1].strip() for w in out["words"]] == expected
    assert "不该出现" not in out["text"] and out["title"] == _w(lang)("研究问题与计划", "Research question and plan")


def test_the_plan_is_not_clipped_by_the_stylesheet_either():
    """The fold used to be a box of eleven rem with its overflow hidden, which cut a long paragraph in the middle of a sentence."""
    rule = re.search(r"\.ws-plan-lead \{([^}]*)\}", _source("workspace.css")).group(1)
    assert "max-height" not in rule and "overflow: hidden" not in rule and "-webkit-line-clamp" not in rule


# -- 任务输出: a task that registered hundreds of files ------------------------------------------------------------------------

_MANY_FILES = r"""
listed.task_outputs.t1.entries = [].concat(
  Array.from({ length: 200 }, (_, i) => entry('work', 'work/20261025-openstock/p' + String(i).padStart(3, '0') + '.log', 'p' + i + '.log')),
  Array.from({ length: 30 }, (_, i) => entry('code', 'src/m' + String(i).padStart(2, '0') + '.py', 'm' + i + '.py')));
const shownIn = () => blockOf().all().filter((e) => e.classList.contains('ws-output')).map((li) => li.children[0].textContent);
const processFold = () => blockOf().all().find((e) => e.tagName === 'DETAILS');
const moreButtons = () => blockOf().all().filter((e) => e.tagName === 'BUTTON' && e.dataset.key && e.dataset.key.startsWith('output-more:'))
  .map((b) => [b.textContent, b.dataset.key, b.getAttribute('aria-label'), b.parentNode === processFold() ? 'in the fold' : 'in the block']);
const removes = () => blockOf().all().filter((e) => e.tagName === 'BUTTON' && e.dataset.key && e.dataset.key.startsWith('output-remove:')).length;
const count = () => ({ work: shownIn().filter((t) => t.endsWith('.log')).length, code: shownIn().filter((t) => t.endsWith('.py')).length });
await window.ROWorkspace.load(root); await sleepTick();
const out = { closed: { shown: count(), more: moreButtons() } };
const fold = processFold(); fold.open = true; await fold.emit('toggle');
out.opened = { shown: count(), more: moreButtons(), removes: removes() };
await find(%(more)s, blockOf()).click(); await sleepTick();
out.all = { shown: count(), more: moreButtons(), open: processFold().open, focus: document.activeElement && document.activeElement.dataset.key };
console.log(JSON.stringify(out));
"""


@pytest.mark.parametrize("lang", LANGS)
def test_a_task_with_hundreds_of_registered_files_lists_the_first_few_of_a_group_and_the_rest_under_n_more(lang):
    """项目首页, a task row with 200 process files and 30 code files registered: the process group is its count until opened (as
    before) and opened lists eight files with 「另外 192 个」, like the code group's eight with 「另外 22 个」; pressing the button of
    the process group lists all of them, the group stays open and the focus goes to the first file that was not shown."""
    w = _w(lang)
    more, code_more = w("另外 192 个", "192 more"), [w("另外 22 个", "22 more"), "output-more:t1:code", w("另外 22 个：代码", "22 more: Code"), "in the block"]
    out = run(_OUTPUTS + _MANY_FILES % {"more": json.dumps(more)}, lang=lang)
    work_more = [more, "output-more:t1:work", w("另外 192 个：过程文件", "192 more: Process files"), "in the fold"]
    assert out["closed"] == {"shown": {"work": 8, "code": 8}, "more": [code_more, work_more]}      # the shut fold already holds its first eight and its button
    assert out["opened"] == {"shown": {"work": 8, "code": 8}, "removes": 16,
                             "more": [code_more, work_more]}
    assert out["all"]["shown"] == {"work": 200, "code": 8} and out["all"]["open"] is True
    assert [m[1] for m in out["all"]["more"]] == ["output-more:t1:code"]
    assert out["all"]["focus"] == "output-open:t1:work/20261025-openstock/p008.log"


# -- every disclosure has the chevron --------------------------------------------------------------------------------------


def test_every_summary_the_scripts_make_carries_the_chevron_and_every_toggle_that_shows_more_in_place_has_it_too():
    """12.13, 21.8: a <details> has a `res-draft-summary` (which hides the browser's triangle) with the chevron in it; 文献 and the
    team pages are no exception. A button that shows or hides more of the same thing in place -- the 「详情」 of a refusal, 查看当时的内容,
    运行日志, 打开关系图 -- wears `has-chevron`, which the stylesheet draws the same shape with and turns while aria-expanded is true."""
    plain = []
    for name in SCRIPTS:
        source = _source(name)
        for match in re.finditer(r'(?:mk|el)\("summary"\s*,\s*([^,)]*)', source):
            around = source[match.start():match.start() + 420]
            if "res-draft-summary" in match.group(1) and "chevron" in around.lower():
                continue
            if name == "team.js" and "typeof chevronSummary" in source[match.start() - 80:match.start()]:
                continue                                        # summaryOf: plain words only where the page has no chevronSummary
            if name == "app.html" and "chevronSummary" in source[match.start() - 700:match.start()] and 'chevronIcon(), mk("span"' in around:
                continue                                        # chevronSummary itself
            plain.append((name, source[match.start():match.start() + 90]))
        if name == "app.html":
            assert "<details" not in re.sub(r"<script>.*?</script>|<style[^>]*>.*?</style>|<!--.*?-->", "", source, flags=re.S)    # no <details> in the markup
    assert plain == []
    # the toggles
    toggles = {"app.html": 2, "routemap.js": 1}
    for name, count in toggles.items():
        found = re.findall(r'err-detail-toggle[^"\']*', re.sub(r"<style[^>]*>.*?</style>", "", _source(name), flags=re.S))
        assert [f for f in found if "has-chevron" in f] and len([f for f in found if "has-chevron" in f]) >= count, name
    assert 'btn.classList.add("has-chevron")' in _source("team.js") and "ws-text-link has-chevron" in _source("workspace.js")
    assert '"btn btn-small has-chevron"' in _source("node.js")
    css = _styles()["app.html"]
    assert re.search(r"\.has-chevron::before \{[^}]*content: \"\"[^}]*transform: rotate\(45deg\)", css)
    assert re.search(r"\.has-chevron\[aria-expanded=\"true\"\]::before \{ transform: rotate\(135deg\); \}", css)
    assert "details[open] > .res-draft-summary .res-chevron { transform: rotate(90deg); }" in css


@needs_node
def test_a_literature_draft_and_the_reader_sources_open_with_the_chevron_not_the_browsers_triangle():
    """文献: a draft's summary (its file, its counts) is a `res-draft-summary` whose first child is the chevron, and so is the
    summary of 项目中的文献文件."""
    out = _node("global.window = {};\n" + DOM + """
global.mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
global.litState = { open: new Set() }; global.litDraftCountText = () => "2 citations"; global.renderLitCitation = () => document.createElement("div");
global.renderBlockingError = () => {};
""" + _app_function("function chevronSummary(") + "\n" + _app_function("function renderLitDraft(") + """
const box = renderLitDraft({ file: "paper.tex", readable: true, counts: {}, citations: [] });
const head = box.children[0], sources = chevronSummary("项目中的文献文件");
const shape = (s) => ({ tag: s.tagName, cls: s.className, first: s.children[0].className, words: s.textContent });
process.stdout.write(JSON.stringify({ draft: shape(head), sources: shape(sources), details: box.tagName }));
""")
    assert out["draft"] == {"tag": "SUMMARY", "cls": "res-draft-summary lit-draft-head", "first": "res-chevron", "words": "paper.tex2 citations"}
    assert out["sources"] == {"tag": "SUMMARY", "cls": "res-draft-summary", "first": "res-chevron", "words": "项目中的文献文件"} and out["details"] == "DETAILS"


# -- the scrim, and 返回 --------------------------------------------------------------------------------------------------


@needs_node
def test_an_attempts_panel_opened_from_another_page_covers_the_page_with_the_scrim_and_on_the_desk_leaves_the_map_live():
    """14.3 keeps the desk's map live beside an attempt's panel (no scrim there); the same panel opened from 项目首页 covers the page
    like every other panel does, so it has the scrim."""
    desk = run_desk(_case("single"), "await window.RODesk.openAttempt(" + json.dumps(A16) + ");")
    assert desk["subject"] == A16 and desk["backdrop"] == "hidden"
    home = run_desk(_case("single"), "global.currentView = 'workspace'; await window.RODesk.openAttempt(" + json.dumps(A16) + ");")
    assert home["subject"] == A16 and "hidden" not in home["backdrop"].split()


_BACK = r"""
const classesOf = () => new Set();
const mkEl = (cls) => { const s = new Set(cls ? cls.split(" ") : []);
  return { classList: { add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c),
    toggle: (c, on) => { const want = on === undefined ? !s.has(c) : !!on; if (want) s.add(c); else s.delete(c); return want; } },
    setAttribute() {}, on: {}, addEventListener(type, fn) { this.on[type] = fn; }, click() { this.on.click(); } }; };
const parts = { "panel-back": mkEl("hidden") };
global.panel = mkEl("hidden"); global.panelBackdrop = mkEl("hidden"); global.panelTitle = mkEl();
global.document = { getElementById: (id) => parts[id] };
global.state = { panelSubject: null };
global.endPanelResize = () => {};
const reopened = [], asked = [];
const open = (subject) => { state.panelSubject = subject; openPanel(); return subject; };
global.window = {
  ROWorkspace: { mayLeavePanel: () => { asked.push(1); return true; }, openHandback: (report, o) => { reopened.push(["handback", report]); mayReplacePanel(); return open({ kind: "handback", report, back: o && o.back }); } },
  RODesk: { openAttemptAnywhere: (id) => { reopened.push(["attempt", id]); mayReplacePanel(); return open({ kind: "desk-attempt", id }); }, mayLeavePanel: () => true } };
global.ROWorkspace = window.ROWorkspace; global.RODesk = window.RODesk;
global.openFilePanel = (path) => { reopened.push(["file", path]); open({ kind: "file", path }); };
global.openSettingsPanel = () => { reopened.push(["settings"]); open({ kind: "settings" }); };
global.openRecordsPanel = () => { reopened.push(["records"]); open({ kind: "records" }); };
%(block)s
const shown = () => !parts["panel-back"].classList.contains("hidden");
const click = () => { parts["panel-back"].click(); return reopened.splice(0); };
const out = {};
// 1. a panel opened with none on screen has no 返回
open({ kind: "settings" }); out.alone = shown();
// 2. settings opened from an attempt's panel: 返回 opens the attempt's panel again, which has no 返回 of its own
closePanel(); open({ kind: "desk-attempt", id: "a:1" }); open({ kind: "settings" });
out.fromAttempt = { shown: shown(), back: click(), after: shown(), subject: state.panelSubject.kind };
// 3. settings opened from the new-attempt form: 返回 reopens the form (which says how), and the form is not "from" settings
closePanel(); const form = () => ({ kind: "attempt-form", reopen: () => { reopened.push(["form"]); open(form()); } });
open(form()); open({ kind: "settings" });
out.fromForm = { shown: shown(), back: click(), after: shown(), subject: state.panelSubject.kind };
open({ kind: "settings" }); out.fromFormAgain = shown();
// 4. a file opened from another file
closePanel(); open({ kind: "file", path: "a.csv" }); open({ kind: "file", path: "b.csv" });
out.fileFromFile = { shown: shown(), back: click(), after: shown(), subject: state.panelSubject.path };
// 5. a handback keeps its own 返回 in its body, so the head has none
closePanel(); open({ kind: "desk-attempt", id: "a:1" }); const handback = open({ kind: "handback", report: "r.md", back: () => {} });
out.handback = { shown: shown(), linked: typeof handback.backTo };
// 6. the same panel drawn again keeps its way back
closePanel(); open({ kind: "desk-attempt", id: "a:1" }); open({ kind: "settings" }); open({ kind: "settings" });
out.redrawn = shown();
// 7. closed, the way back is gone
closePanel(); out.closed = shown(); open({ kind: "settings" }); out.afterClose = shown();
// 8. a panel that cannot be opened again from what it holds offers no way back to it
closePanel(); open({ kind: "variable-copy" }); open({ kind: "settings" }); out.unknown = shown();
// 9. 返回 asks once about half-typed words, the opener it calls does not ask again
closePanel(); open({ kind: "desk-attempt", id: "a:1" }); open({ kind: "settings" }); asked.length = 0;
mayReplacePanel.answeredYesUntil = 0;   // the earlier steps pressed 返回 a moment ago
click(); out.asked = asked.length;
process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_a_panel_opened_from_another_panel_has_a_return_step_that_opens_that_panel_again():
    """21.8: settings from an attempt's panel or from the new-attempt form, a file from another file: 返回 at the head of the panel
    opens the one it came from; that panel is not itself offered a way back (no ping-pong); a handback keeps its own 返回; a panel
    drawn again keeps its way back, a closed panel has none; and 返回 asks about half-typed words once."""
    script = "global.window = {};\n" + _BACK % {"block": _app_block("// -- Panel and 返回 (DESIGN.md 21.8)", "// -- end of panel and 返回")}
    out = _node(script)
    assert out["alone"] is False
    assert out["fromAttempt"] == {"shown": True, "back": [["attempt", "a:1"]], "after": False, "subject": "desk-attempt"}
    assert out["fromForm"] == {"shown": True, "back": [["form"]], "after": False, "subject": "attempt-form"}
    assert out["fromFormAgain"] is True                          # opened from the form again, the way back is there again
    assert out["fileFromFile"] == {"shown": True, "back": [["file", "a.csv"]], "after": False, "subject": "a.csv"}
    assert out["handback"] == {"shown": False, "linked": "function"}
    assert out["redrawn"] is True and out["closed"] is False and out["afterClose"] is False and out["unknown"] is False
    assert out["asked"] == 1


def test_the_panel_has_a_return_link_in_its_head_in_both_languages():
    """The head of the panel holds a text link 「返回」 (hidden until a panel is opened from another), in the page's markup so that
    English mode translates it with the rest."""
    html = APP_HTML.read_text(encoding="utf-8")
    assert re.search(r'<button class="panel-back ws-text-link hidden" id="panel-back" type="button" data-i18n>返回</button>\s*<div class="panel-title"', html)
    assert '"返回": "Back"' in _source("i18n.js")


_NODE_BACK = r"""
global.currentView = "desk";
global.views.node = el("div", "node");
global.apiGet = async () => { throw new Error("offline"); };   // a page is not drawn here: only where 返回 leads
global.panel = { classList: { contains: () => false } };
const reopened = [], views_ = [];
global.state = Object.assign(global.state || {}, { panelSubject: { kind: "desk-attempt", id: "a:1" }, dataEpoch: 0 });
global.panelReopener = (s) => (s ? () => reopened.push(s.id) : null);
global.closePanel = () => { state.panelSubject = null; };
global.activateView = (name) => { views_.push(name); global.currentView = name; };
(async () => {
  const R = window.RONode, out = {};
  // from an attempt's panel on the desk: the page gives the panel back
  R.openNode("script:x.py"); out.opened = { fromView: R._nav.fromView, hasPanel: typeof R._nav.fromPanel };
  R._back(); out.afterBack = { views: views_.splice(0), reopened: reopened.splice(0), cleared: R._nav.fromPanel };
  // opened with no panel on screen: nothing to give back
  state.panelSubject = null; R.openNode("script:y.py"); R._back();
  out.noPanel = { views: views_.splice(0), reopened: reopened.splice(0) };
  // a page opened from another node page is stepped back through first; the panel comes back at the end
  state.panelSubject = { kind: "desk-attempt", id: "a:2" }; global.currentView = "desk"; views_.length = 0;
  R.openNode("script:z.py"); R.openNode("dataset:d.csv"); R._back();
  out.trail = { views: views_.splice(0), reopened: reopened.splice(0) };
  R._back(); out.end = { views: views_.splice(0), reopened: reopened.splice(0) };
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
def test_a_node_page_opened_from_a_panel_gives_the_panel_back_with_its_return_step():
    """21.8: 打开详情页 from an attempt's panel: the page's 返回 goes back to where it was opened from and opens the panel again;
    opened with no panel on screen, it opens none; a page reached from another node page steps back through them first."""
    out = node_run(_NODE_BACK, language=None, view="desk")
    assert out["opened"] == {"fromView": "desk", "hasPanel": "function"}
    assert out["afterBack"] == {"views": ["node", "desk"], "reopened": ["a:1"], "cleared": None}
    assert out["noPanel"] == {"views": ["node", "desk"], "reopened": []}
    assert out["trail"] == {"views": ["node"], "reopened": []}
    assert out["end"] == {"views": ["desk"], "reopened": ["a:2"]}


# -- the route map -------------------------------------------------------------------------------------------------------


def _attempts(n: int) -> dict:
    """A desk answer of `n` attempts and no line, for project p-1, with a saved camera when asked."""
    base = _desk("single")
    template = base["attempts"][0]
    base["attempts"] = [{**template, "id": f"attempt:map.md#{i}", "number": str(i), "title": f"Route {i}"} for i in range(1, n + 1)]
    base["lines"] = []
    return base


_MAP_RUN = r"""
const calls = [];
(async () => {
  const desk = %(desk)s;
  global.first = %(first)s;
  map.update(desk, { changed: () => false });
  const view = () => { const t = root.querySelector(".rm-world").style.transform.match(/translate\((.*)px, (.*)px\) scale\((.*)\)/);
    return { x: Number(t[1]), y: Number(t[2]), zoom: Number(t[3]) }; };
  // the cards that show: any part of a card between the edges of the 1200 x 800 box, below the 52 px toolbar
  const outside = () => { const v = view(); return root.querySelectorAll(".rm-card").filter((c) => {
    const x = parseFloat(c.style.left) * v.zoom + v.x, y = parseFloat(c.style.top) * v.zoom + v.y, w = 232 * v.zoom, h = c.offsetHeight ? 88 * v.zoom : 88 * v.zoom;
    return !(x + w > 0 && x < 1200 && y + h > 52 && y < 800); }).length; };
  const note = () => { const n = root.querySelector(".rm-outside"); return n.cls.has("hidden") ? null : n.textContent; };
  const out = { start: view(), note: note(), outside: outside(), zoomWord: root.querySelector(".cv-zoom").textContent };
  %(steps)s
  process.stdout.write(JSON.stringify(Object.assign(out, { posted, firstAsked: global.asked_ || null })));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _map(desk: dict, steps: str = "", *, first: str = "false", lang: str | None = None) -> dict:
    script_dom = _MAP_DOM.replace("global.map = window.RORouteMap.create({ onOpen: (id) => opened.push(id), onChanged: async () => {} });",
                                  "global.asked_ = []; global.map = window.RORouteMap.create({ onOpen: (id) => opened.push(id), onChanged: async () => {},"
                                  " firstOpen: (project) => { asked_.push(project); return global.first; } });")
    assert script_dom != _MAP_DOM
    i18n = "" if lang is None else f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    script = ("global.window = {};\n" + i18n + (WEBAPP / "routemap.js").read_text(encoding="utf-8") + "\n" + script_dom
              + _MAP_RUN % {"desk": json.dumps(desk, ensure_ascii=False), "first": first, "steps": steps})
    return _node(script)


@needs_node
@pytest.mark.parametrize("lang", LANGS)
def test_the_first_time_a_projects_map_is_opened_it_fits_all_its_cards_and_forgets_a_camera_saved_before(lang):
    """21.8: with a camera saved before (far from the cards), the first opening of the project's map fits all cards (the zoom is
    below 100%, no card is outside, no line is said) and the saved camera is forgotten as 适应全部 forgets it; the opening after
    uses the saved camera."""
    saved = _attempts(30)
    saved["layout"]["viewport"] = {"x": -5000, "y": -5000, "zoom": 1}
    first = _map(saved, "await new Promise((r) => setTimeout(r, 600));", first="true", lang=lang)
    assert first["firstAsked"] == ["p-1"] and first["start"]["zoom"] < 1 and first["start"] != {"x": -5000, "y": -5000, "zoom": 1}
    assert first["outside"] == 0 and first["note"] is None
    assert first["posted"] == [["/api/canvas/layout", {"project": "p-1", "scope": "routes", "viewport": None}]]
    again = _map(saved, "", first="false", lang=lang)
    assert again["start"] == {"x": -5000, "y": -5000, "zoom": 1} and again["posted"] == []
    # nothing saved: the same fit, and nothing to forget
    plain = _map(_attempts(30), "await new Promise((r) => setTimeout(r, 600));", first="true", lang=lang)
    assert plain["start"]["zoom"] < 1 and plain["posted"] == [] and plain["outside"] == 0


@needs_node
@pytest.mark.parametrize("lang", LANGS)
def test_the_map_says_how_many_cards_are_outside_the_view_in_one_quiet_line_and_nothing_when_all_show(lang):
    """21.8: 900 cards cannot all be held at the smallest zoom (25%): the fit leaves some out and the line says how many, in the
    counted words of the language; at 100% after 适应全部 of a small map the line is gone; zooming a fitted map to 100% leaves some
    out and says it, and 适应全部 takes the line away again. The number is the one counted from the cards' places on the screen."""
    w = _w(lang)
    many = _map(_attempts(900), first="true", lang=lang)
    assert many["outside"] > 0 and many["zoomWord"] == "25%"
    assert many["note"] == w(f"有 {many['outside']} 张卡片在视野之外", f"{many['outside']} cards are outside the view")
    few = _map(_attempts(80), r"""
const fitted = { zoom: view().zoom, outside: outside(), note: note() };
root.querySelectorAll(".cv-btn").find((b) => b.textContent === "100%").click();
out.at100 = { zoom: view().zoom, outside: outside(), note: note() };
root.querySelectorAll(".cv-btn").find((b) => b.textContent === window.ROI18N ? window.ROI18N.t("适应全部") : "适应全部").click();
out.refit = { zoom: view().zoom, outside: outside(), note: note() };
out.fitted = fitted;""", first="true", lang=lang)
    assert few["fitted"]["outside"] == 0 and few["fitted"]["note"] is None
    assert few["at100"]["zoom"] == 1 and few["at100"]["outside"] > 0
    assert few["at100"]["note"] == w(f"有 {few['at100']['outside']} 张卡片在视野之外", f"{few['at100']['outside']} cards are outside the view")
    assert few["refit"]["outside"] == 0 and few["refit"]["note"] is None
    one = _map(_attempts(3), "", first="true", lang=lang)
    assert one["note"] is None and one["outside"] == 0


def test_the_desk_remembers_per_project_that_a_map_was_opened_and_with_storage_off_every_opening_is_the_first():
    """`firstMapOpen` (desk.js): true the first time a project is asked about in this browser, false after, true for another
    project; an empty project key is never the first; with no storage every opening is the first."""
    script = "global.window = { localStorage: (() => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, v) }; })() };\nglobal.document = { getElementById() { return null; } };\n"
    script += (WEBAPP / "desk.js").read_text(encoding="utf-8") + """
const f = window.RODesk._firstMapOpen, out = {};
out.sequence = [f("p-1"), f("p-1"), f("p-2"), f("p-1"), f("")];
window.localStorage = { getItem() { throw new Error("off"); }, setItem() { throw new Error("off"); } };
out.off = [f("p-1"), f("p-1")];
process.stdout.write(JSON.stringify(out));
"""
    out = _node(script)
    assert out["sequence"] == [True, False, True, False, False] and out["off"] == [True, True]

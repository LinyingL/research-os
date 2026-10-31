"""Research OS V16, task B5-2 (DESIGN.md 21.8 'Numbers in tables' and 'Times'), the pages under node, in both languages.

Numbers: every table or list the app draws from a data file goes through `RODesk.formatNumber` (a value with more than six
significant digits to four, the value as written in the element's `title`), and the text of a note or a report -- markdown a
person or an agent wrote, its tables included -- is shown as written. Times: every time the app shows is the viewer's local
time, told by `RODesk.whenWords` (the one function that converts a stored time), here in Europe/Berlin where 2026-10-30 is
UTC+1 and 2026-10-22 is UTC+2, fed times written with Z and with +02:00. Every test names its scenario."""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from test_outputs_ui import _rows_run
from test_v16_handback import me, pid, root  # noqa: F401 -- fixtures of the engine tests (the handback's)
from test_v16_handback_js import _run as handback_run, report  # noqa: F401 -- fixture of the handback pages
from test_v16_provenance_js import _run as node_run
from test_v9_language import NODE, NODE_ENV, WEBAPP, _app_block, _app_function, _node, _times_prelude, needs_node
from test_workspace_js import STORE_SCENE, _BACKFILL, run

pytestmark = needs_node     # every scenario here runs the page scripts under node

LANGS = ["zh", "en"]
DESK_JS = (WEBAPP / "desk.js").read_text(encoding="utf-8")


def _w(lang: str):
    return lambda zh, en: en if lang == "en" else zh


def _source_of(name: str) -> str:
    return (WEBAPP / name).read_text(encoding="utf-8")


# -- numbers ------------------------------------------------------------------------------------------------------------------

_PREVIEW = r"""
const items = [{ path: 'tables/est.csv', title: 'Estimates', kind: 'table', task: 't1', report: 'research/results/a.md', task_title: 'Estimate',
  report_title: 'Estimate', at: '2026-10-22T09:41:12+02:00',
  table_preview: [['term', 'estimate', 'p', 'n', 'share', 'note'],
    ['alpha', '0.008063173780026744', '9.509441061106294e-05', '1234567', '12.5%', '0.1234567 (p)'],
    ['beta', '1.82', '0.5', '123456', '1,234,567.891', '-3.14159265358979']] }];
const host = new Element('div');
renderRegisteredOutputs(host, { items });
const cells = host.all().filter((e) => e.tagName === 'TD' || e.tagName === 'TH');
console.log(JSON.stringify({ cells: cells.map((e) => [e.tagName, e.textContent, e.title === undefined ? null : e.title]),
  hint: host.all().filter((e) => e.classList.contains('lit-hint')).map((e) => e.textContent) }));
"""


@needs_node
@pytest.mark.parametrize("lang", LANGS)
def test_the_table_preview_of_a_registered_data_file_shows_a_long_number_to_four_digits_with_the_value_as_written_on_hover(lang):
    """A CSV's preview on 文献 and 变量 (the registered outputs): the header row as the file wrote it, a value of more than six
    significant digits to four with the value as written in the cell's `title`, and an integer, a short value, a percent, a
    value with a separator and a word beside it as written, with no title."""
    out = _rows_run(_PREVIEW, lang=lang)
    assert out["cells"] == [
        ["TH", "term", None], ["TH", "estimate", None], ["TH", "p", None], ["TH", "n", None], ["TH", "share", None], ["TH", "note", None],
        ["TD", "alpha", None], ["TD", "0.008063", "0.008063173780026744"], ["TD", "9.509e-05", "9.509441061106294e-05"],
        ["TD", "1234567", None], ["TD", "12.5%", None], ["TD", "0.1234567 (p)", None],
        ["TD", "beta", None], ["TD", "1.82", None], ["TD", "0.5", None], ["TD", "123456", None], ["TD", "1,234,567.891", None],
        ["TD", "-3.142", "-3.14159265358979"]]
    assert out["hint"][-1] == _w(lang)("表格预览：最多显示 10 行、20 列。", "Table preview: up to 10 rows and 20 columns.")   # the line that says how much is shown stays


_CLAIMS = r"""
const deskSource = %(desk)s;
RODesk.formatNumber = new Function("window", "document", deskSource + "; return window.RODesk;")({}, { getElementById() { return null; } }).formatNumber;
global.apiGet = async (path) => {
  if (path === "/api/results") return { items: [], other: [], loose: [], excluded: [], counts: {}, choices: {}, papers: { recent: [] },
    drafts: [{ file: "paper.md", node: "file:paper.md", marked: true, claims: 3, count: 3 }] };
  if (path.startsWith("/api/results?draft=")) return { total: 3, shown: 3, sections: [{ section: "s1", title: "Results", claims: [
    { id: "c1", sentence: "The effect is 0.008063173780026744 per year (p = 9.509441061106294e-05).", number: "0.008063173780026744", line: 4, trace: null },
    { id: "c2", sentence: "There are 1234567 firms.", number: "1234567", line: 9, trace: null },
    { id: "c3", sentence: "No number was found in this one.", number: null, line: 12, trace: null }] }] };
  throw new Error("no fixture for " + path);
};
(async () => {
  const host = el("div", "host"); views.results = host;
  await window.RONode.loadResults(host, false);
  toggle(byClass(host, "res-draft")[0]);
  await settle(); await settle();
  const rows = byClass(byClass(host, "res-draft-body")[0], "res-row").map((r) => ({ sentence: text(byClass(r, "node-link")[0]),
    where: byClass(r, "node-sub-inline").map(text),
    numbers: byClass(r, "claim-number").map((n) => [text(n), n.title || null]) }));
  process.stdout.write(JSON.stringify(rows));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("lang", LANGS)
def test_a_claims_number_on_the_paper_is_drawn_by_the_number_function_and_its_sentence_stays_as_the_paper_wrote_it(lang):
    """图表与论断, a draft set as the paper opened: the number of a claim to four significant digits when longer than six, the value
    as written on hover; an integer as written; the sentence -- the paper's own text, the same digits in it -- never reformatted."""
    w = _w(lang)
    out = node_run(_CLAIMS % {"desk": json.dumps(DESK_JS)}, language=lang, view="results")
    assert out[0]["sentence"] == "The effect is 0.008063173780026744 per year (p = 9.509441061106294e-05)."
    assert out[0]["numbers"] == [[w("数字 0.008063", "Number: 0.008063"), "0.008063173780026744"]]
    assert out[0]["where"] == [w("数字 0.008063 · 第 4 行", "Number: 0.008063 · Line 4")]
    assert out[1]["numbers"] == [[w("数字 1234567", "Number: 1234567"), None]] and out[1]["where"] == [w("数字 1234567 · 第 9 行", "Number: 1234567 · Line 9")]
    assert out[2]["numbers"] == [] and out[2]["where"] == [w("第 12 行", "Line 12")]


_NOTE_BODY = ("The estimate is 0.008063173780026744 (p = 9.509441061106294e-05) on 1234567 firms.\\n\\n"
              "| term | estimate |\\n| --- | --- |\\n| alpha | 0.008063173780026744 |\\n| beta | 9.509441061106294e-05 |")


@pytest.mark.parametrize("lang", LANGS)
def test_the_text_of_a_note_or_a_report_is_shown_as_it_was_written_its_tables_included(lang):
    """Markdown a person or an agent wrote -- a sentence with long numbers, a table of them -- is drawn by the writing area's own
    preview: every digit as written, no `title` on a cell. The number function is for the data the app reads from a file, not for
    what someone wrote (the same preview draws a report's text and a handback's report)."""
    w = _w(lang)
    out = run(r"""
await window.ROWorkspace.load(root);
await writeDocument('NOTE'.replace(/\\n/g, '\n'));
await find(window.ROI18N.t('预览')).click();
const preview = byClass('ws-preview');
console.log(JSON.stringify({ words: preview.textContent, cells: preview.all().filter((e) => e.tagName === 'TD' || e.tagName === 'TH').map((e) => [e.textContent, e.title === undefined ? null : e.title]) }));
""".replace("NOTE", _NOTE_BODY), lang=lang)
    assert "0.008063173780026744 (p = 9.509441061106294e-05) on 1234567 firms." in out["words"]
    assert out["cells"] == [["term", None], ["estimate", None], ["alpha", None], ["0.008063173780026744", None], ["beta", None], ["9.509441061106294e-05", None]]
    assert "0.008063173780026744" in out["words"] and "0.008063 " not in out["words"].replace("0.008063173780026744", "")
    # and the code that draws it -- a note, a report, a handback's report -- never asks for the number function
    draws = _source_of("workspace.js")
    markdown = draws[draws.index("function renderMarkdown("):draws.index("function buildPage(")]
    assert "formatNumber" not in markdown and "showNumber" not in markdown


#: Every place a script builds a table cell, and why it is not a table of numbers from a data file: a cell is the number function's
#: (`showNumber`) or the table is text somebody wrote or names of things.
_CELLS_ARE_NOT_DATA = {
    ("app.html", "codePreview"): "the file view: the lines of the file as written, a line number beside each",
    ("app.html", "dtaPreview"): "a Stata file's variables: names, labels and types, no values",
    ("app.html", "docxPreview"): "a Word document's tables: the document's own text",
    ("team.js", "team members"): "the team's members: names, roles, e-mail addresses and key fingerprints",
}


def test_no_script_builds_a_table_cell_from_data_without_the_number_function():
    """21.8: the number function is for every table and list drawn from a data file. The scripts are scanned for the places that
    build a cell (`td`/`th` made by hand): the one table of data (the registered outputs' preview) goes through `showNumber`, the
    others are on the list above with their reason, and a new one fails here until it is routed or listed."""
    found = {}
    for name in ("app.html", "node.js", "desk.js", "team.js", "workspace.js", "canvas.js", "routemap.js"):
        source = (WEBAPP / name).read_text(encoding="utf-8")
        for match in re.finditer(r'(?:mk|el|createElement)\(\s*(?:\w+\s*\?\s*)?"t[dh]"|\\"(?:td|th)\\"|<td[ >]|\?\s*"td"\s*:\s*"th"', source):
            before = source[:match.start()]
            pattern = r"^(?:async )?function (\w+)\(" if name == "app.html" else r"^[ ]*(?:async )?function (\w+)\("      # app.html: the top-level function it is in
            names = re.findall(pattern, before, re.M)
            found.setdefault((name, names[-1] if names else "?"), []).append(source[match.start():match.start() + 140])
    flagged = {}
    for (name, function), uses in found.items():
        if name == "app.html" and function == "renderRegisteredOutputs":
            source = (WEBAPP / name).read_text(encoding="utf-8")
            body = source[source.index("function renderRegisteredOutputs("):]
            assert "D.showNumber(td, cell)" in body.split("function versionLabel(")[0]     # the preview's cells go through the one function
            continue
        if name == "workspace.js" and function == "renderMarkdown":
            continue                                                                     # a note's or a report's table: as written
        if name == "team.js":
            function = "team members"
        if (name, function) not in _CELLS_ARE_NOT_DATA:
            flagged[(name, function)] = uses
    assert flagged == {}, f"a table built by hand: route its numbers through RODesk.showNumber, or list it with its reason: {flagged}"


# -- times --------------------------------------------------------------------------------------------------------------------


def _desk(expression: str):
    script = "global.window = {}; global.document = {getElementById(){return null}};\n" + DESK_JS + f"\nprocess.stdout.write(JSON.stringify({expression}));\n"
    return _node(script)


def test_when_words_tells_a_stored_time_in_this_macs_time_in_the_form_it_is_asked_for():
    """`RODesk.whenWords` in Europe/Berlin: a time with a Z, a +02:00, a space for the T, microseconds and no zone at all, in the
    form of the page ("full", 2026-10-30 16:33, and "day"); the day moves with the clock (22:30 UTC is already tomorrow here); what
    is no time with hours and minutes -- a day, a label, nothing -- gives "" and the caller keeps what it was given."""
    full = _desk("""[ "2026-10-30T15:33:00Z", "2026-10-30T15:33:00+02:00", "2026-10-22T07:41:12Z", "2026-10-30 15:33:00Z", "2026-10-22T09:41:12.123456+02:00",
      "2026-10-22 09:41:12", "2026-10-22T09:41", "2026-10-23T22:30:00Z", "2026-03-29T00:30:00Z" ].map((at) => window.RODesk.whenWords(at, new Date(2026, 9, 30), "full"))""")
    assert full == ["2026-10-30 16:33", "2026-10-30 14:33", "2026-10-22 09:41", "2026-10-30 16:33", "2026-10-22 09:41",
                    "2026-10-22 09:41", "2026-10-22 09:41", "2026-10-24 00:30", "2026-03-29 01:30"]
    day = _desk("""["2026-10-23T22:30:00Z", "2026-10-30T15:33:00Z", "2026-10-04T21:15:03+02:00"].map((at) => window.RODesk.whenWords(at, new Date(2026, 9, 30), "day"))""")
    assert day == ["2026-10-24", "2026-10-30", "2026-10-04"]
    none = _desk("""["2026-10-04", "migrated", "", null, undefined, "2026-13-45T25:00"].map((at) => [window.RODesk.whenWords(at, new Date(), "full"), window.RODesk.whenWords(at, new Date(), "day")])""")
    assert none == [["", ""]] * 6
    words = _desk("""["2026-10-30T15:33:00Z", "2026-10-29T15:33:00Z", "2026-10-04T15:33:00Z"].map((at) => window.RODesk.whenWords(at, new Date(2026, 9, 30, 18, 0)))""")
    assert words == ["今天 16:33", "昨天 16:33", "10月4日"]       # the page's other form reads the same instant


_WORDING = r"""
const W = { whenText, reviewHoverText, atText };
const calls = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(calls.map(([fn, args]) => W[fn](...args))));
"""


def _page_wording(*calls):
    block = (_times_prelude() + _app_block("// -- Judgment wording (pure", "// -- end of judgment wording") + _app_function("function atText(")
             + "\n" + _WORDING)
    result = subprocess.run([NODE, "-", json.dumps(list(calls))], input=block, capture_output=True, text=True, check=True, timeout=30, env=NODE_ENV)
    return json.loads(result.stdout)


def test_a_history_line_a_hover_card_and_a_card_entry_tell_the_time_in_this_macs_zone():
    """app.html's three readers of a stored time (a link judgment's history, the hover card of a link whose judgment waits, a
    variable card's confirmation and the registered rows): 15:33 UTC on 30 October is 16:33 here, a writer's +02:00 is converted
    too, the hover card says the day it is here, and a migrated entry, a label and a missing time are said as they were."""
    got = _page_wording(
        ["whenText", [{"at": "2026-10-30T15:33:00Z"}]], ["whenText", [{"at": "2026-10-30T15:33:00+02:00"}]],
        ["whenText", [{"migrated": True, "at_label": "迁移自旧索引（原连线确认的时间未知）", "at": "2026-10-30T15:33:00Z"}]], ["whenText", [{"at": None}]],
        ["reviewHoverText", [{"outcome": "review", "verdict": "rejected", "at": "2026-10-23T22:30:00Z", "label": "依据已变化"}]],
        ["atText", ["2026-10-30T15:33:00Z"]], ["atText", ["2026-10-22T09:41:12.123456+02:00"]], ["atText", ["2026-10-04"]], ["atText", [None]], ["atText", ["a label"]],
    )
    assert got == ["2026-10-30 16:33", "2026-10-30 14:33", "迁移自旧索引（原连线确认的时间未知）", "",
                   "你曾于 2026-10-24 否决 · 依据已变化，待复核", "2026-10-30 16:33", "2026-10-22 09:41", "2026-10-04", "", "a label"]


@needs_node
@pytest.mark.parametrize("lang", LANGS)
def test_the_registered_rows_date_their_task_and_their_files_in_this_macs_time(lang):
    """文献's and 变量's rows (the registered outputs): the heading's time and a row made at another time are the stamp in local
    time, whatever zone the engine wrote them in."""
    out = _rows_run(r"""
const items = [
  { path: 'tables/a.csv', title: 'A', kind: 'table', task: 't1', report: 'research/results/a.md', task_title: 'T', report_title: 'T', at: '2026-10-30T15:33:00Z' },
  { path: 'tables/b.csv', title: 'B', kind: 'table', task: 't1', report: 'research/results/a.md', task_title: 'T', report_title: 'T', at: '2026-10-22T07:41:12Z' }];
const host = new Element('div');
renderRegisteredOutputs(host, { items });
console.log(JSON.stringify(host.all().filter((e) => e.classList.contains('registered-time')).map((e) => e.textContent)));
""", lang=lang)
    assert out == ["2026-10-30 16:33", "2026-10-22 09:41"]


@needs_node
@pytest.mark.parametrize("lang", LANGS)
def test_a_handbacks_head_names_the_end_of_the_run_in_this_macs_time(root, pid, report, lang):  # noqa: F811
    """项目首页's handback panel: the run ended at 15:33 UTC on 30 October; at 16:33 here the head reads 2026-10-30 16:33, not the
    UTC clock."""
    out = handback_run(root, pid, report, r"""
VIEW.run = Object.assign({}, VIEW.run, { ended: '2026-10-30T15:33:00Z' });
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({ subtitle: panelSubtitle.textContent }));
""", lang=lang)
    assert out["subtitle"].endswith(" · 2026-10-30 16:33") and "15:33" not in out["subtitle"]


@pytest.mark.parametrize("lang", LANGS)
def test_the_review_list_of_earlier_runs_dates_each_run_in_this_macs_time(lang):
    """补登记以往运行的文件: a run that started at 15:33 UTC is dated 16:33; one written with +01:00 on the day the clocks change is
    told by the instant, not by the writer's digits."""
    out = run(_BACKFILL + r"""
server.listing.groups[0].runs[0].date = '2026-10-30T15:33:00Z';
await openList();
console.log(JSON.stringify(panelBody.all().filter((e) => e.classList.contains('ws-backfill-run')).map((e) => e.children.map((c) => c.textContent))));
""", lang=lang)
    assert out == [["2026-10-30 16:33", "Claude Code · sonnet"], ["2026-10-25 09:00", "Codex · gpt"]]


@pytest.mark.parametrize("lang", LANGS)
def test_the_undo_list_dates_each_file_in_this_macs_time(lang):
    """撤回本次运行: the files an undo would restore or remove, each with its time -- one written with Z is converted, one with the
    offset of that day is the clock it names."""
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
setRuns([mkRun({state:'done',can_revert:true,changes:2,can_reply:true})]);
runPlans['r-20261021T090000Z-aaaaaa']={run:clone(runsList.runs[0]),restore:[{path:'analysis.py',time:'2026-10-30T15:33:00Z'}],
  remove:[{path:'out/new.csv',time:'2026-10-22T09:40:03+02:00'}],cannot:[]};
await window.ROWorkspace.load(root);await sleepTick();
await find(window.ROI18N.t('撤回本次运行'),byClass('ws-agent')).click();await sleepTick();
console.log(JSON.stringify({rows:byClass('ws-agent-undo').all().filter((e)=>e.classList.contains('ws-undo-row')).map((e)=>e.textContent)}));
""", lang=lang)
    assert len(out["rows"]) == 2
    assert "analysis.py" in out["rows"][0] and "2026-10-30 16:33" in out["rows"][0] and "15:33" not in out["rows"][0]
    assert "out/new.csv" in out["rows"][1] and "2026-10-22 09:40" in out["rows"][1]


@pytest.mark.parametrize("lang", LANGS)
def test_the_space_panel_dates_each_run_that_holds_moved_files_in_this_macs_time(lang):
    """设置, 运行占用的空间: a run that started at 15:33 UTC is dated 16:33."""
    out = run(STORE_SCENE.replace("2026-10-21T09:40:03+02:00", "2026-10-30T15:33:00Z") + r"""
console.log(JSON.stringify(runRows()));
""", lang=lang)
    assert [row[1] for row in out] == ["2026-10-19 18:02", "2026-10-30 16:33"]


def test_no_script_slices_or_reformats_an_iso_time_by_hand():
    """21.8: every time a script shows goes through `RODesk.whenWords`. The scripts are scanned for the hand-made forms -- a stamp
    cut out of an ISO string (`.slice(0, 10)`, `.slice(11, 16)`, `substr`), a `T` replaced by a space, `toISOString` shown, a
    `toLocale…String`, an `Intl.DateTimeFormat` -- and a use of one on anything that reads as a time fails."""
    forms = [
        r'\b(?:at|iso|stamp|time|when|date|modified|created|ended|started|moment)\b[\w.\[\]()"]{0,24}\.(?:slice|substr|substring)\(\s*(?:0|11)\s*,\s*1[06]\s*\)',
        r'\.replace\(\s*["\']T["\']\s*,\s*["\'] ["\']\s*\)', r'\.replace\(\s*/T/\s*,\s*["\'] ["\']\s*\)', r'toLocale\w*String', r'Intl\.DateTimeFormat',
        r'\.slice\(\s*11\s*,\s*16\s*\)', r'\.substr(?:ing)?\(\s*11\s*,\s*5\s*\)',
    ]
    found = []
    for name in ("app.html", "node.js", "desk.js", "team.js", "workspace.js", "canvas.js", "routemap.js"):
        source = (WEBAPP / name).read_text(encoding="utf-8")
        for pattern in forms:
            found += [(name, m.group(0)) for m in re.finditer(pattern, source)]
    assert found == []

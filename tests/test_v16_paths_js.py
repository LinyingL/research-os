"""Research OS V16, task B4-2 (DESIGN.md 21.7, the second and third bullets): a path is the quiet second line under a title,
never the line itself. 数据与代码's rows are titled by the registered title or the file name, 图表与论断's rows put the path
under the name, a handback's files are a title that opens as a text link with the path under it (the agent and model named
once, in the head), and so are the files of a task, of a report's 要点 and of the notes that mention a file; a 断链 line does
not say its row's path or its direction twice. The pages run under node on the engine's own payloads, in both languages.
Every test names its scenario."""

from __future__ import annotations

import json

import pytest

from test_v16_handback import me, pid, root  # noqa: F401 -- fixtures of the engine tests
from test_v16_handback_js import _run as handback_run, report  # noqa: F401 -- fixture of the handback pages
from test_v16_provenance import CHECK, FIG, RAW, _data_code, _results, thesis
from test_v16_provenance_js import _json, _run
from test_v9_language import needs_node
from test_workspace_js import _OUTPUTS, run as workspace_run


# -- 数据与代码 and 图表与论断 ----------------------------------------------------------------------------------------------

_ROWS_BODY = r"""
const data = %(data)s, results = %(results)s;
global.apiGet = async (path) => (path === "/api/stata" ? { has_do: false } : path === "/api/results" ? results : data);
global.apiPost = async () => ({ ok: true });
(async () => {
  const host = el("div", "host"), figures = el("div", "host"); views.lineage = host; views.results = figures;
  await window.RONode.loadDataCode(host, false);
  await window.RONode.loadResults(figures, false);
  const shape = (row, headClass) => ({
    title: text(row.all().find((k) => k.cls.has("node-link"))), titleIsLink: row.all().find((k) => k.cls.has("node-link")).tag === "BUTTON",
    titleMono: row.all().find((k) => k.cls.has("node-link")).cls.has("mono"),
    path: byClass(row, "res-path").map(text), pathTag: byClass(row, "res-path").map((k) => k.tag),
    // what stands before the path in the row's own line of children: the title and its marks, never the path
    first: (headClass ? byClass(row, headClass)[0] : row).kids.filter((k) => typeof k !== "string").map((k) => text(k)) });
  const dc = (path) => byClass(host, "dc-row").find((r) => byClass(r, "res-path").some((k) => text(k) === path));
  const res = (path) => byClass(figures, "res-row").find((r) => byClass(r, "res-path").some((k) => text(k) === path));
  const out = { script: shape(dc(%(check)s), "dc-head"), raw: shape(dc(%(raw)s), "dc-head"), figure: shape(res(%(fig)s)) };
  // the order inside a figure's row: the name, its marks, the path, then the causes (the path is a line of its own)
  const main = byClass(res(%(fig)s), "res-main")[0];
  out.figureOrder = main.kids.filter((k) => typeof k !== "string").map((k) => k.cls.has("node-link") ? "name" : k.cls.has("res-path") ? "path"
    : k.cls.has("res-cause") ? "cause" : k.cls.has("res-field") ? "field" : k.cls.has("status-label") ? "mark" : "other");
  // a data row: title and marks in the head, the path just after the head, before the relations
  const row = dc(%(check)s);
  out.dataOrder = row.all().filter((k) => k.parentNode && k.parentNode.cls.has("dc-main") && k.parentNode === byClass(row, "dc-main")[0])
    .map((k) => k.cls.has("dc-head") ? "head" : k.cls.has("dc-path") ? "path" : k.cls.has("dc-rel") ? "rel" : k.cls.has("dc-broken") ? "broken" : "other");
  // the filters read the engine's data: the counts do not move
  out.filters = byClass(host, "dc-filter").map(text);
  byClass(host, "dc-filter").find((b) => b.dataset.filter === "broken").click();
  out.broken = byClass(host, "dc-row").map((r) => text(byClass(r, "dc-path")[0]));
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_a_file_row_is_its_name_with_the_path_as_the_quiet_line_under_it_on_both_kind_pages(tmp_path, language):
    """A script on 数据与代码 is titled by its file name (a link, not mono), a download too, with the path under the head in the
    quiet small style; a figure on 图表与论断 is its name, its marks, then the path as a line of its own before the causes. The
    filter of broken links still lists the script by its path."""
    root = thesis(tmp_path / "thesis")
    data, results = _data_code(root), _results(root)
    out = _run(_ROWS_BODY % {"data": _json(data), "results": _json(results), "check": _json(CHECK), "raw": _json(RAW), "fig": _json(FIG["main"])},
                      language=language, view="lineage")
    script = out["script"]
    assert script["title"] == "check_R_environment.R" and script["titleIsLink"] and not script["titleMono"]
    assert script["path"] == [CHECK] and script["pathTag"] == ["DIV"]
    assert script["first"][0] == "check_R_environment.R" and CHECK not in script["first"]  # the head is the title and its marks
    assert out["raw"]["title"] == "bis_us_neer_monthly.csv" and out["raw"]["path"] == [RAW]
    assert out["figure"]["title"] == "fig_main.png" and out["figure"]["path"] == [FIG["main"]]
    assert out["figureOrder"][0] == "name" and out["figureOrder"].index("path") == min(i for i, k in enumerate(out["figureOrder"]) if k not in ("name", "mark"))
    assert "cause" not in out["figureOrder"][:out["figureOrder"].index("path")]
    assert out["dataOrder"][:2] == ["head", "path"]
    zh = language == "zh"
    assert out["filters"][4] == f"{'断链' if zh else 'Broken links'} {data['counts']['broken']}"
    assert CHECK in out["broken"]


@needs_node
def test_a_file_registered_with_a_title_is_titled_by_it_and_the_path_stays_under_the_title(tmp_path):
    """The title an agent or a person registered a file with is the row's title on both pages (the file name is not repeated
    beside it), and the path is the line under it."""
    root = thesis(tmp_path / "thesis")
    data = _data_code(root)
    at = next(i for i, f in enumerate(data["files"]) if not f.get("attempts"))
    data["files"][at] = {**data["files"][at], "title": "The registered title"}
    path = data["files"][at]["path"]
    out = _run(r"""
const data = %(data)s;
global.apiGet = async (p) => (p === "/api/stata" ? { has_do: false } : data);
(async () => {
  const host = el("div", "host"); views.lineage = host;
  await window.RONode.loadDataCode(host, false);
  const row = byClass(host, "dc-row").find((r) => byClass(r, "res-path").some((k) => text(k) === %(path)s));
  process.stdout.write(JSON.stringify({ titles: byClass(row, "node-link").map(text), path: byClass(row, "res-path").map(text) }));
})().catch((e) => { console.error(e); process.exit(1); });
""" % {"data": _json(data), "path": _json(path)}, language=None, view="lineage")
    assert out == {"titles": ["The registered title"], "path": [path]}


# -- 断链 ---------------------------------------------------------------------------------------------------------------


_BROKEN_BODY = r"""
const entry = (script, target, type) => ({ script: script, target: target, type: type, relation: type === "writes" ? "写出" : "读取", line: 3, candidates: [] });
const own = %(own)s, other = %(other)s;
global.apiGet = async (path) => (path === "/api/stata" ? { has_do: false } : { files: [{ id: "script:" + own, type: "script", type_label: "脚本", path: own, exists: true,
  reads: [], writes: [], orphan_input: false, review: 0,
  broken: [entry(own, "data/gone.csv", "reads"), entry(own, "out/never.png", "writes")] }], counts: { all: 1, broken: 1 } });
(async () => {
  const host = el("div", "host"); views.lineage = host;
  await window.RONode.loadDataCode(host, false);
  const lines = (node) => byClass(node, "dc-broken").map((b) => ({ said: byClass(b, "dc-rel").map(text), paths: byClass(b, "dc-broken-path").map(text) }));
  const out = { row: lines(host) };
  // the same line on the page of another file: it names the script by its file name, the path under it as quiet text
  const section = el("div", "sec");
  const page = { id: "figure:" + other, type: "figure", path: other, trace: { columns: [], lines: [], breaks: [],
    broken: [entry(own, "data/gone.csv", "reads"), entry(own, "out/never.png", "writes")] } };
  window.RONode.renderTrace(section, page, { panel: true });
  out.page = lines(section);
  // and on the script's own page it is its own again
  const mine = el("div", "sec");
  window.RONode.renderTrace(mine, { ...page, id: "script:" + own, type: "script", path: own }, { panel: true });
  out.mine = lines(mine);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_a_broken_link_on_the_scripts_own_row_says_what_it_reads_or_writes_once_and_elsewhere_names_the_script_by_its_file(language):
    """On the script's row the line neither repeats the row's path nor says 读取 twice: 「断链：读取 data/gone.csv，文件不存在」
    (写出 for a write). On the page of another file the script is named by its file name and its path stands under the line as
    quiet text. English likewise. Each case is a sentence of its own."""
    own, other = "analysis/17-fit.R", "figs/fit.png"
    out = _run(_BROKEN_BODY % {"own": _json(own), "other": _json(other)}, language=language, view="lineage")
    zh = language == "zh"
    mine = [{"said": ["断链：读取 data/gone.csv，文件不存在" if zh else "Broken link: reads data/gone.csv, file not found"], "paths": []},
            {"said": ["断链：写出 out/never.png，文件不存在" if zh else "Broken link: writes out/never.png, file not found"], "paths": []}]
    assert out["row"] == mine and out["mine"] == mine
    named = [{"said": ["断链：17-fit.R 读取 data/gone.csv，文件不存在" if zh else "Broken link: 17-fit.R reads data/gone.csv, file not found"],
              "paths": [own]},
             {"said": ["断链：17-fit.R 写出 out/never.png，文件不存在" if zh else "Broken link: 17-fit.R writes out/never.png, file not found"],
              "paths": [own]}]
    assert out["page"] == named
    for line in (line for part in (out["row"], out["page"]) for entry in part for line in entry["said"]):
        assert ("（" not in line and "(" not in line), line  # the old brackets are gone


# -- the handback ----------------------------------------------------------------------------------------------------------


_FILES_SCENARIO = r"""
await window.ROWorkspace.openHandback(REPORT);
const numbers = cls('ws-hb-number').map(r => r.children.map(c => [c.tagName, c.className, c.textContent]));
const outputs = cls('ws-hb-output').map(li => li.children.map(c => [c.tagName, c.className, c.textContent]));
const boxes = panelBody.all().filter(e => e.tagName === 'BUTTON' && e.className.includes('btn') && (e.classList.contains('ws-file-link')));
console.log(JSON.stringify({ numbers, outputs, boxes: boxes.length, subtitle: panelSubtitle.textContent,
  agentUnderFiles: cls('ws-hb-number').concat(cls('ws-hb-output')).filter(r => /Claude Code|sonnet/.test(r.textContent)).length,
  head: /Claude Code · sonnet/.test(panelSubtitle.textContent) }));
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_a_handbacks_files_are_a_title_that_opens_as_a_text_link_with_the_path_under_it_and_the_agent_is_named_once(
        root, pid, report, language):  # noqa: F811
    """Each key number's file and each output: the file name (an output without a registered title) or its title as a text link
    -- a button without the box --, the path in the quiet code line after it; a file at the project's root has no path line.
    The agent and model are in the panel's head and in no row."""
    views = [{"kind": "figure", "path": "figures/sharpe.png", "title": None, "state": "current"},
             {"kind": "code", "path": "code/recheck.py", "title": "Recheck script", "state": "current"},
             {"kind": "table", "path": "t.csv", "title": None, "state": "current"}]
    out = handback_run(root, pid, report, _FILES_SCENARIO, lang=language, outputs=views)
    link = "BUTTON", "ws-text-link ws-file-link"
    for row in out["numbers"]:
        assert [row[2][0], row[2][1]] == list(link) and row[3][:2] == ["CODE", "ws-output-path"], row
    assert [row[2][2] for row in out["numbers"]] == ["sharpe.csv", "sharpe.csv", "sharpe.csv", "gone.csv"]
    assert [row[3][2] for row in out["numbers"]] == ["results/sharpe.csv"] * 3 + ["results/gone.csv"]
    by_title = {row[0][2]: row for row in out["outputs"]}
    assert by_title["sharpe.png"][0][1] == "ws-text-link ws-file-link" and by_title["sharpe.png"][1] == ["CODE", "ws-output-path", "figures/sharpe.png"]
    assert by_title["Recheck script"][1] == ["CODE", "ws-output-path", "code/recheck.py"]
    assert [c for c in by_title["t.csv"] if c[0] == "CODE"] == []  # at the root: the name is the path
    assert out["boxes"] == 0
    assert out["head"] is True and out["agentUnderFiles"] == 0
    assert out["subtitle"].startswith("Claude Code · sonnet")


# -- 项目首页: a task's files, a report's key points, the notes that mention a file ----------------------------------------


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_a_tasks_registered_files_are_titled_by_title_or_file_name_with_the_path_under_and_open_as_text_links(language):
    out = workspace_run(_OUTPUTS + r"""
listed.task_outputs.t1.entries=[entry('figure','figures/fit.png','Fit'),entry('code','src/model.py',null),entry('table','top.csv',null)];
await window.ROWorkspace.load(root);await sleepTick();
const rows=blockOf().all().filter(e=>e.classList.contains('ws-output')).map(r=>r.children.map(c=>[c.tagName,c.className,c.textContent]));
console.log(JSON.stringify(rows));
""", lang=language)
    for row in out:
        assert row[0][:2] == ["BUTTON", "ws-text-link ws-file-link"]   # a text link, not a box
    assert [row[0][2] for row in out] == ["model.py", "Fit", "top.csv"]   # grouped by kind: code, figure, table
    # the path comes after the remove control, which stays a button on the title's own line; a file at the root has none
    assert [[c for c in row if c[0] == "CODE"] for row in out] == [[["CODE", "ws-output-path", "src/model.py"]],
                                                                  [["CODE", "ws-output-path", "figures/fit.png"]], []]
    assert all(row[1][0] == "BUTTON" and "ws-text-link" not in row[1][1] for row in out)


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_a_reports_key_points_name_their_files_by_file_name_with_the_path_under_and_open_them_as_text_links(language):
    out = workspace_run(r"""
const path='research/results/manual.md';
listed.documents.push({path,kind:'result',title:'Result'});docs[path]={path,kind:'result',title:'Result',content:'Body',revision:'d1'};
researchRecords[path]={report:path,revision:'rr1',context:{finding:'Initial',materials:['code/model.do'],references:['data/source.csv'],
  purposes:{'code/model.do':'The model'}}};
await window.ROWorkspace.load(root);await find('Result').click();await editDocument();
const groups=byClass('ws-research-links').children.map(g=>[g.children[0].textContent,g.children.slice(1).filter(r=>r.classList.contains('ws-research-link')).map(r=>r.children.map(c=>[c.tagName,c.className,c.textContent]))]);
console.log(JSON.stringify(groups));
""", lang=language)
    groups = dict(out)
    words = {"zh": ("研究材料", "文献文件"), "en": ("Research materials", "Reference files")}[language]
    materials, references = groups[words[0]][0], groups[words[1]][0]
    assert materials[0] == ["BUTTON", "ws-text-link ws-file-link", "model.do"] and materials[-1] == ["CODE", "ws-output-path", "code/model.do"]
    assert references[0] == ["BUTTON", "ws-text-link ws-file-link", "source.csv"] and references[-1] == ["CODE", "ws-output-path", "data/source.csv"]


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_the_notes_and_reports_that_mention_a_file_name_a_report_by_its_title_and_a_file_by_its_name(language):
    out = workspace_run(r"""
const target=new Element('div');
window.ROWorkspace.renderResearchLinks(target,[{report:'research/results/abc.md',title:'',question:'Why?',
  matches:[{path:'data/source.csv',purpose:'the raw input'},{path:'top.csv',purpose:''}],variables:[]},
  {report:'research/results/def.md',title:'Sharpe check',question:'',matches:[],variables:[]}]);
console.log(JSON.stringify(target.all().filter(e=>['BUTTON','P','CODE'].includes(e.tagName)).map(e=>[e.tagName,e.className,e.textContent])));
""", lang=language)
    report = "报告" if language == "zh" else "Report"
    assert out[0] == ["BUTTON", "ws-text-link ws-file-link", report]       # no title: the word, not the file's own name
    assert ["P", "ws-research-purpose", "source.csv — the raw input"] in out
    assert ["CODE", "ws-output-path", "data/source.csv"] in out and ["P", "ws-research-purpose", "top.csv"] in out
    assert [row for row in out if row[2] == "top.csv" and row[0] == "CODE"] == []
    assert ["BUTTON", "ws-text-link ws-file-link", "Sharpe check"] in out

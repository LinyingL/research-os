"""Output-folder editing preserves drafts and ignores late project responses; the registered rows open the record
on the project home."""
import json

import pytest

from test_v9_language import _app_block, _app_function, _node
from test_workspace_js import DOM, NODE, WEBAPP, run

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def test_output_folder_form_saves_the_bound_project_and_keeps_errors_editable():
    result = run(r'''
const folders={code:'scripts',data:'data',figure:'figures',table:'tables',literature:'references',document:'reports',variable:'research/variables',work:'work'};
customGet=path=>path==='/api/outputs/folders'?{folders,revision:'r0'}:undefined;
let calls=0;
customPost=(path,body)=>{if(path==='/api/outputs/folders'){calls++;if(calls===1)throw Error('file changed');return {revision:'r1'};}};
await window.ROWorkspace.load(root);
await find('Output folders').click();
const screen=panelTitle.textContent+panelBody.textContent;
typed(field('Figure',panelBody),'outputs/charts');
await find('Save',panelBody).click();
const dirtyAfterFailure=window.ROWorkspace.isDirty(), editableAfterFailure=!field('Figure',panelBody).disabled;
await find('Save',panelBody).click();
console.log(JSON.stringify({screen,dirtyAfterFailure,editableAfterFailure,dirtyAfterSave:window.ROWorkspace.isDirty(),posted}));
''',lang="en")
    assert not any("\u4e00" <= c <= "\u9fff" for c in result["screen"])
    assert result["dirtyAfterFailure"] and result["editableAfterFailure"]
    assert not result["dirtyAfterSave"]
    body = result["posted"][-1][1]
    assert body["project"] == "/projects/study" and body["revision"] == "r0"
    assert body["folders"]["figure"] == "outputs/charts"


def test_unsaved_folder_settings_are_guarded_and_save_disables_inputs():
    result = run(r'''
const folders={code:'code',data:'data',figure:'figures',table:'tables',literature:'literature',document:'reports',variable:'research/variables',work:'work'};
const pending=deferred();
customGet=path=>path==='/api/outputs/folders'?{folders,revision:null}:undefined;
customPost=path=>path==='/api/outputs/folders'?pending.promise:undefined;
await window.ROWorkspace.load(root);await find('输出目录').click();
typed(field('图',panelBody),'charts');
answers.push(false);const refused=!window.ROWorkspace.mayLeavePanel();
const saving=find('保存',panelBody).click();await sleepTick();
const disabled=field('图',panelBody).disabled;
pending.resolve({revision:'saved'});await saving;
console.log(JSON.stringify({refused,disabled,clean:!window.ROWorkspace.isDirty(),enabled:!field('图',panelBody).disabled}));
''')
    assert all(result.values())


_WORK_PANEL = r'''
const folders={code:'code',data:'data',figure:'figures',table:'tables',literature:'literature',document:'reports',variable:'research/variables',work:'work'};
customGet=path=>path==='/api/outputs/folders'?{folders,revision:'r0'}:undefined;
customPost=path=>path==='/api/outputs/folders'?{revision:'r1'}:undefined;
await window.ROWorkspace.load(root);
await find(%(open)s).click();
const labels=panelBody.all().filter(e=>e.tagName==='LABEL').map(e=>e.children[0].textContent);
const text=panelBody.textContent;
typed(field(%(work)s,panelBody),'process');
await find(%(save)s,panelBody).click();
console.log(JSON.stringify({labels,text,posted}));
'''


def test_output_folders_panel_shows_the_work_location_last_with_its_sentence():
    """DESIGN.md 19.2: the eighth location is 过程文件 / "Process files", after the seven outputs and before 保存, with
    the sentence that says what it holds; saving sends it with the others."""
    zh = run(_WORK_PANEL % {"open": "'输出目录'", "work": "'过程文件'", "save": "'保存'"})
    sentence = "过程文件：只为完成这次任务而写的脚本、自检输出、下载的源码和中间文件。不进索引，不算输出。"
    assert zh["labels"] == ["代码", "数据", "图", "表", "文献", "文稿与报告", "变量草稿", "过程文件"]
    assert zh["text"].index("变量草稿") < zh["text"].index(sentence) and zh["text"].endswith(sentence + "保存")
    assert zh["posted"][-1][1]["folders"]["work"] == "process"
    en = run(_WORK_PANEL % {"open": "'Output folders'", "work": "'Process files'", "save": "'Save'"}, lang="en")
    sentence = ("Process files: scripts written only to carry out this task, self-check output, downloaded sources "
                "and intermediate files. Not indexed, not outputs.")
    assert en["labels"][-1] == "Process files" and len(en["labels"]) == 8
    assert en["text"].index("Variable drafts") < en["text"].index(sentence) and en["text"].endswith(sentence + "Save")
    assert not any("\u3400" <= c <= "\u9fff" for c in en["text"])
    assert en["posted"][-1][1]["folders"]["work"] == "process"


_CHOOSE_PANEL = r'''
const folders={code:'code',data:'data',figure:'figures',table:'tables',literature:'literature',document:'reports',variable:'research/variables',work:%(work)s};
customGet=path=>path==='/api/outputs/folders'?Object.assign({folders,revision:null,saved:false},%(extra)s):undefined;
customPost=path=>path==='/api/outputs/folders'?{revision:'r1'}:undefined;
await window.ROWorkspace.load(root);
await find(%(open)s).click();
const text=panelBody.textContent;
const says=panelBody.all().filter(e=>e.className==='ws-status err').map(e=>e.textContent);
console.log(JSON.stringify({text,says,work:field(%(field)s,panelBody).value}));
'''


def test_output_folders_panel_says_the_work_location_needs_choosing_when_no_name_is_usable_for_it():
    """DESIGN.md 19.2: the settings still read, the work field is empty and says so, under the field and before the
    sentence about process files; with a usable name nothing of the kind is said."""
    zh = run(_CHOOSE_PANEL % {"open": "'输出目录'", "field": "'过程文件'", "work": "''", "extra": "{choose:['work']}"})
    sentence = "过程文件：只为完成这次任务而写的脚本、自检输出、下载的源码和中间文件。不进索引，不算输出。"
    assert zh["says"] == ["请为过程文件选一个目录。"] and zh["work"] == ""
    assert zh["text"].index("变量草稿") < zh["text"].index("请为过程文件选一个目录。") < zh["text"].index(sentence)
    en = run(_CHOOSE_PANEL % {"open": "'Output folders'", "field": "'Process files'", "work": "''", "extra": "{choose:['work']}"}, lang="en")
    assert en["says"] == ["Choose a folder for process files."] and not any("\u3400" <= c <= "\u9fff" for c in en["text"])
    for extra in ("{}", "{choose:[]}"):
        usable = run(_CHOOSE_PANEL % {"open": "'输出目录'", "field": "'过程文件'", "work": "'agent-work'", "extra": extra})
        assert usable["says"] == [] and usable["work"] == "agent-work" and "请为过程文件选一个目录。" not in usable["text"]


def test_late_folder_read_never_replaces_a_new_project_panel():
    result = run(r'''
const late=deferred();customGet=path=>path==='/api/outputs/folders'?late.promise:undefined;
await window.ROWorkspace.load(root);const old=find('Output folders').click();await sleepTick();
state.dataEpoch++;state.currentPath='/projects/new';window.ROWorkspace.reset();
panelBody.replaceChildren(new Element('p'));panelBody.children[0].textContent='New project panel';
late.resolve({folders:{code:'old'},revision:null});await old;
console.log(JSON.stringify({text:panelBody.textContent}));
''',lang="en")
    assert result["text"] == "New project panel"


# -- 「打开结果」 on a registered row opens the record on 项目首页 (DESIGN.md 19.6, 19.10 item 1) --------------------

#: What the frame around the page needs for app.html's own view switch to run next to the real workspace.js: the
#: other views as hidden elements, the page title and switches, the desk's leave question (`desk.leave`), and the
#: result record the rows point at.
_HOME_PRELUDE = r"""
window.localStorage = { setItem() {} };
global.t = (s, v) => window.ROI18N.t(s, v);
global.ROWorkspace = window.ROWorkspace;
const ids = {}, desk = { leave: true };
document.getElementById = (id) => (ids[id] ||= new Element('div'));
document.body = new Element('body');
['results', 'desk', 'lineage', 'canvas', 'variables', 'literature', 'proposals', 'members', 'node']
  .forEach((k) => { views[k] = new Element('div'); views[k].className = 'hidden'; });
root.className = 'hidden';
global.VIEW_STORAGE_KEY = 'view-key'; global.FULL_WINDOW_VIEWS = ['canvas', 'desk']; global.lastDataView = 'lineage';
global.RODesk = window.RODesk = { onePerson: () => false, leaveDesk: () => desk.leave, resume() {}, loadDesk() {} };
global.RONode = { section: () => 'routes' };
docs['research/results/a.md'] = { path: 'research/results/a.md', kind: 'result', title: 'Result A',
  content: '# Result A\nThe estimate is 0.42.', revision: 'doc-r1' };
listed.documents.push({ path: 'research/results/a.md', kind: 'result', title: 'Result A', excerpt: '' });
const shownView = () => Object.keys(views).filter((k) => !views[k].classList.contains('hidden'));
// A page of registered rows on `view`, drawn by app.html's own function; the button is the row's 打开结果.
const rowsOn = (view) => {
  global.currentView = view; views[view].className = ''; if (view !== 'workspace') root.className = 'hidden';
  const host = new Element('div');
  renderRegisteredOutputs(host, { items: [{ path: 'figures/fit.png', title: 'Fit', kind: 'figure', report: 'research/results/a.md', agent: 'Claude Code' }] });
  return host;
};
const documentReads = () => gets.filter((p) => p.startsWith('/api/workspace/document?'));
const homeReads = () => gets.filter((p) => p === '/api/workspace');
const preview = () => byClass('ws-preview');
"""


def _home_run(scenario: str, *, lang: str = "zh") -> dict:
    """The registered rows, the page's view switch and the real project home under node."""
    source = DOM + "\nwindow.ROI18N_SAVED=" + json.dumps(lang) + ";\n"
    source += (WEBAPP / "i18n.js").read_text() + "\n" + (WEBAPP / "workspace.js").read_text() + "\n" + _HOME_PRELUDE
    source += _app_block("const SECTION_OF_VIEW", "const SECTION_TITLES") + "\n"
    for head in ("function sectionTitle(", "function mk(", "function mkButton(", "function activateView(",
                 "async function openResultOnHome(", "function renderRegisteredOutputs("):
        source += _app_function(head) + "\n"
    source += "\n(async()=>{\n" + scenario + "\n})().catch(e=>{console.error(e.stack);process.exitCode=1});"
    return _node(source)


def test_open_result_from_another_view_reads_the_home_first_and_opens_the_record_in_preview():
    out = _home_run(r"""
const host = rowsOn('results');
const wasHidden = root.classList.contains('hidden');
await find('打开结果', host).click();
console.log(JSON.stringify({wasHidden, view: currentView, shown: shownView(), title: ids['page-title'].textContent,
  order: gets.filter((p) => p.startsWith('/api/workspace')).map((p) => decodeURIComponent(p.split('path=')[1] || p).split('&')[0]),
  reading: !preview().classList.contains('hidden'), words: preview().textContent,
  writing: byClass('ws-writing').classList.contains('hidden'), loaded: root.dataset.loaded}));
""")
    assert out["wasHidden"] is True
    assert out["view"] == "workspace" and out["shown"] == ["workspace"] and out["title"] == "项目首页"
    assert out["loaded"] == "1"
    # the home was read, its own first document opened, and then the record: the record is what is left on screen
    assert out["order"] == ["/api/workspace", "research/overview.md", "research/results/a.md", "/api/workspace/research-record?report=research/results/a.md"]
    assert out["reading"] is True and out["writing"] is True and "The estimate is 0.42." in out["words"]


def test_open_result_with_the_home_already_read_switches_to_it_and_opens_the_record_in_preview():
    out = _home_run(r"""
await window.ROWorkspace.load(root);await sleepTick();
const before = {home: homeReads().length, words: preview().textContent};
const host = rowsOn('lineage');
await find('打开结果', host).click();
console.log(JSON.stringify({before, view: currentView, shown: shownView(), homeReads: homeReads().length,
  reading: !preview().classList.contains('hidden'), words: preview().textContent,
  record: documentReads().pop()}));
""")
    assert out["before"]["home"] == 1 and "An open question." in out["before"]["words"]
    assert out["view"] == "workspace" and out["shown"] == ["workspace"]
    assert out["homeReads"] == 1  # a home that is already read is not read again
    assert out["reading"] is True and "The estimate is 0.42." in out["words"] and "An open question." not in out["words"]
    assert "research%2Fresults%2Fa.md" in out["record"]


def test_open_result_from_a_view_that_refuses_to_be_left_stays_put_and_opens_nothing():
    out = _home_run(r"""
const host = rowsOn('desk');
desk.leave = false;
await find('打开结果', host).click();await sleepTick();
const refused = {view: currentView, shown: shownView(), gets: gets.slice(), loaded: root.dataset.loaded || null};
desk.leave = true;
await find('打开结果', host).click();
console.log(JSON.stringify({refused, view: currentView, words: preview().textContent}));
""")
    assert out["refused"] == {"view": "desk", "shown": ["desk"], "gets": [], "loaded": None}
    # nothing is held against the next try: with the view free to be left it opens the record
    assert out["view"] == "workspace" and "The estimate is 0.42." in out["words"]


def test_open_result_opens_nothing_when_the_home_cannot_be_read_or_the_project_changed_meanwhile():
    out = _home_run(r"""
customGet = (path) => { if (path === '/api/workspace') throw Error('down'); };
await find('打开结果', rowsOn('results')).click();await sleepTick();
const unreadable = {view: currentView, loaded: root.dataset.loaded || null, documents: documentReads().length};
customGet = null; root.replaceChildren(); delete root.dataset.loaded; gets.length = 0; window.ROWorkspace.reset();
const late = deferred();
customGet = (path) => (path === '/api/workspace' ? late.promise : undefined);
const host = rowsOn('results');
const clicking = find('打开结果', host).click();await sleepTick();
state.dataEpoch++; root.dataset.loaded = '1';            // the other project's home was read in the meantime
late.resolve(clone(listed));await clicking;await sleepTick();
console.log(JSON.stringify({unreadable, documents: documentReads().length}));
""")
    assert out["unreadable"] == {"view": "workspace", "loaded": None, "documents": 0}
    assert out["documents"] == 0


def test_open_result_from_a_view_that_refuses_to_be_left_keeps_that_view_when_the_home_was_read_first():
    """The project home was read before the page moved on to a view that asks to be kept (the desk with half-typed words):
    the click leaves the page where it is, shows nothing new and reads no document; with the view free to be left, the
    next click opens the record without reading the home again."""
    out = _home_run(r"""
await window.ROWorkspace.load(root);await sleepTick();
const home = {reads: homeReads().length, documents: documentReads().length, words: preview().textContent, loaded: root.dataset.loaded || null};
const host = rowsOn('desk');
desk.leave = false;
const seen = gets.length;
await find('打开结果', host).click();await sleepTick();
const refused = {view: currentView, shown: shownView(), newGets: gets.slice(seen),
  documents: documentReads().length, words: preview().textContent, loaded: root.dataset.loaded || null,
  homeHidden: root.classList.contains('hidden')};
desk.leave = true;
await find('打开结果', host).click();await sleepTick();
console.log(JSON.stringify({home, refused, view: currentView, homeReads: homeReads().length, words: preview().textContent}));
""")
    assert out["home"]["reads"] == 1 and out["home"]["loaded"] == "1" and "An open question." in out["home"]["words"]
    refused = out["refused"]
    assert refused["view"] == "desk" and refused["shown"] == ["desk"] and refused["homeHidden"] is True
    assert refused["newGets"] == [] and refused["documents"] == out["home"]["documents"]   # no document is read
    assert refused["words"] == out["home"]["words"] and "The estimate is 0.42." not in refused["words"]
    assert refused["loaded"] == "1"
    # nothing is held against the next try
    assert out["view"] == "workspace" and out["homeReads"] == 1 and "The estimate is 0.42." in out["words"]


# -- every registered row names the task that produced it and when (DESIGN.md 19.6) ---------------------------------------

_ROWS_PRELUDE = r"""
global.t = (s, v) => window.ROI18N.t(s, v);
const opened = [];
global.openResultOnHome = async (path) => { opened.push(path); };
const rowsOf = (items) => {
  const host = new Element('div');
  renderRegisteredOutputs(host, { items });
  return host.all().filter((e) => e.classList.contains('registered-output'));
};
const words = (row) => row.children.filter((e) => e.tagName === 'SPAN').map((e) => e.textContent);
// 19.12 item 3: one heading per task. A group is its heading (the words of its title, date and button, in order) and its rows.
const groupsOn = (host) => host.children.filter((e) => e.tagName === 'SECTION').flatMap((box) => box.children.filter((e) => e.classList.contains('registered-task-group')));
const groupsOf = (items) => {
  const host = new Element('div');
  renderRegisteredOutputs(host, { items });
  return groupsOn(host).map((group) => ({
    head: group.children.filter((e) => e.classList.contains('registered-task-head')).flatMap((h) => h.children.map((c) => c.tagName.toLowerCase() + ':' + c.textContent)),
    rows: group.children.filter((e) => e.classList.contains('registered-output')).map(words)}));
};
"""


def _rows_run(scenario: str, *, lang: str = "zh") -> dict:
    """The registered rows as app.html draws them, on their own."""
    source = DOM + "\nwindow.ROI18N_SAVED=" + json.dumps(lang) + ";\n" + (WEBAPP / "i18n.js").read_text() + "\n" + _ROWS_PRELUDE
    for head in ("function mk(", "function mkButton(", "function atText(", "function renderRegisteredOutputs("):
        source += _app_function(head) + "\n"
    source += "\n(async()=>{\n" + scenario + "\n})().catch(e=>{console.error(e.stack);process.exitCode=1});"
    return _node(source)


#: One task whose record has a heading, one whose run is later than its first row, a removed task (its record still has a
#: heading), a task known only by its first line, and an old entry that names no task.
_ROW_ITEMS = r"""
const A = 'research/results/a.md', B = 'research/results/b.md', C = 'research/results/c.md';
const items = [
  { path: 'tables/mine.csv', title: 'Mine', kind: 'table', task: 't1', report: A, evidence: 'person', task_title: 'Clean the survey data', report_title: 'Survey cleaned', at: '2026-10-23T10:00:00+02:00' },
  { path: 'figures/fit.png', title: 'Fit', kind: 'figure', task: 't1', report: A, run: 'r-20261022T074112Z-aaaaaa', agent: 'Claude Code', task_title: 'Clean the survey data', report_title: 'Survey cleaned', at: '2026-10-22T09:41:12+02:00' },
  { path: 'figures/fit2.png', title: 'Fit 2', kind: 'figure', task: 't1', report: A, run: 'r-20261022T074112Z-aaaaaa', agent: 'Claude Code', task_title: 'Clean the survey data', report_title: 'Survey cleaned', at: '2026-10-22T09:41:12+02:00' },
  { path: 'tables/two.csv', title: 'Two', kind: 'table', task: 't2', report: B, task_removed: true, report_title: 'The old heading', at: '2026-10-22T18:05:00+02:00' },
  { path: 'tables/old.csv', title: 'Old', kind: 'table' },
  { path: 'tables/three.csv', title: 'Three', kind: 'table', task: 't3', report: C, task_title: 'A task without a record heading' },
  { path: 'figures/late.png', title: 'Late', kind: 'figure', task: 't1', report: A, run: 'r-20261020T060000Z-bbbbbb', agent: 'Codex', task_title: 'Clean the survey data', report_title: 'Survey cleaned', at: '2026-10-20T08:00:00+02:00' },
];
"""


def test_the_rows_stand_under_one_heading_per_task_with_its_record_heading_its_run_date_and_the_button_once():
    """19.12 item 3: a heading per task -- the first heading of its result record, else the task's first line, 「任务已移除」
    for a removed task even when its record has a heading -- with the date of the run that produced the files and
    「打开结果」 once; the rows under it no longer repeat the task, and a row made at another time says so."""
    out = _rows_run(_ROW_ITEMS + r"""
const host = new Element('div');
renderRegisteredOutputs(host, { items });
const groups = groupsOn(host);
await find('打开结果', groups[0]).click();
console.log(JSON.stringify({groups: groupsOf(items), buttons: host.all().filter((e) => e.tagName === 'BUTTON' && e.textContent === '打开结果').length,
  heads: host.all().filter((e) => e.tagName === 'H3').map((e) => e.textContent), opened,
  rowButtons: groups.map((g) => g.children.filter((e) => e.classList.contains('registered-output')).flatMap((r) => r.children.filter((c) => c.tagName === 'BUTTON').map((c) => c.textContent))) }));
""")
    assert out["groups"] == [
        {"head": ["h3:Survey cleaned", "span:2026-10-22 09:41", "button:打开结果"],      # the newest row with a run dates the heading
         "rows": [["tables/mine.csv", "2026-10-23 10:00"], ["figures/fit.png", "Claude Code"], ["figures/fit2.png", "Claude Code"],
                  ["figures/late.png", "Codex", "2026-10-20 08:00"]]},
        {"head": ["h3:任务已移除", "span:2026-10-22 18:05", "button:打开结果"], "rows": [["tables/two.csv"]]},
        {"head": [], "rows": [["tables/old.csv"]]},                                    # an entry that names no task and no record: no heading
        {"head": ["h3:A task without a record heading", "button:打开结果"], "rows": [["tables/three.csv"]]}]
    assert out["buttons"] == 3 and out["heads"] == ["Survey cleaned", "任务已移除", "A task without a record heading"]   # once per task, not per row
    assert out["opened"] == ["research/results/a.md"]
    assert all("打开结果" not in names for rows in out["rowButtons"] for names in rows)


def test_a_registered_heading_reads_in_english_without_chinese():
    out = _rows_run(_ROW_ITEMS + r"""
const host = new Element('div');
renderRegisteredOutputs(host, { items });
console.log(JSON.stringify({groups: groupsOf(items), all: host.textContent, buttons: host.all().filter((e) => e.tagName === 'BUTTON').map((e) => e.textContent)}));
""", lang="en")
    assert out["groups"][1]["head"] == ["h3:Task removed", "span:2026-10-22 18:05", "button:Open the result"]
    assert not any("㐀" <= c <= "鿿" for c in out["all"])
    assert out["buttons"].count("Open the result") == 3


def test_a_tasks_rows_on_later_pages_stay_under_its_heading_and_a_tasks_pages_make_one_heading():
    """The rows come fifty at a time (「显示更多」): the second page's rows of a task whose heading is on the first page
    go under that heading, and a task met first on the second page gets its heading there, once."""
    out = _rows_run(r"""
const mine = (task, n, at) => Array.from({ length: n }, (_, i) => ({ path: 'tables/' + task + '-' + i + '.csv', title: task + i, kind: 'table', task, report: 'research/results/' + task + '.md',
  task_title: 'Task ' + task, at }));
const items = [].concat(mine('a', 30, '2026-10-22T09:00:00+02:00'), mine('b', 30, '2026-10-21T09:00:00+02:00'), mine('a', 5, '2026-10-22T09:00:00+02:00').map((r, i) => Object.assign(r, { path: 'tables/a-late-' + i + '.csv' })), mine('c', 3, '2026-10-20T09:00:00+02:00'));
const host = new Element('div');
renderRegisteredOutputs(host, { items });
const count = () => groupsOn(host).map((g) => [g.all().filter((e) => e.tagName === 'H3').map((e) => e.textContent), g.children.filter((e) => e.classList.contains('registered-output')).length]);
const first = count();
await find('显示更多', host).click();
console.log(JSON.stringify({first, second: count(), more: !!find('显示更多', host)}));
""")
    assert out["first"] == [[["Task a"], 30], [["Task b"], 20]] and out["more"] is False
    assert out["second"] == [[["Task a"], 35], [["Task b"], 30], [["Task c"], 3]]


def test_the_rows_of_a_variable_draft_stand_under_a_task_heading_too():
    out = _rows_run(r"""
const items = [{ path: 'research/variables/gdp.toml', title: 'gdp', kind: 'variable', task: 't1', report: 'research/results/a.md', task_title: 'Draft the GDP variable',
  definition: { name: 'GDP', meaning: 'Output', unit: 'EUR', granularity: 'year' }, state: 'current', at: '2026-10-22T09:41:12+02:00' }];
const host = new Element('div');
global.humanRecordsBlocked = () => false;
renderRegisteredOutputs(host, { items }, true);
const group = groupsOn(host)[0];
console.log(JSON.stringify({title: host.all().filter((e) => e.tagName === 'H2').map((e) => e.textContent), head: group.children[0].children.map((c) => c.textContent),
  row: group.children.filter((e) => e.classList.contains('registered-output')).length,
  import: host.all().filter((e) => e.tagName === 'BUTTON').map((e) => e.textContent)}));
""")
    assert out["title"] == ["待确认的变量草稿"]
    assert out["head"] == ["Draft the GDP variable", "2026-10-22 09:41", "打开结果"] and out["row"] == 1
    assert out["import"].count("打开结果") == 1 and "导入为变量草稿" in out["import"]


def test_the_four_views_draw_their_registered_rows_with_the_one_function():
    """图表与论断 and 数据与代码 (node.js), 文献 and 变量 (app.html) all call renderRegisteredOutputs, so the task and
    the date are on every one of them."""
    app = (WEBAPP / "app.html").read_text(encoding="utf-8")
    node = (WEBAPP / "node.js").read_text(encoding="utf-8")
    assert app.count("function renderRegisteredOutputs(") == 1 and node.count("function renderRegisteredOutputs(") == 0
    assert app.count("renderRegisteredOutputs(host, data.outputs") == 2      # 变量 (the drafts) and 文献
    assert node.count("renderRegisteredOutputs(") == 2                        # 图表与论断 and 数据与代码


_CHOOSING_GOES = r'''
const folders={code:'code',data:'data',figure:'figures',table:'tables',literature:'literature',document:'reports',variable:'research/variables',work:''};
customGet=path=>path==='/api/outputs/folders'?{folders,revision:null,saved:false,choose:['work']}:undefined;
let calls=0;
customPost=(path,body)=>{if(path==='/api/outputs/folders'){calls++;if(calls===1)throw Error('conflict');return {revision:'r1',saved:true,folders:body.folders};}};
await window.ROWorkspace.load(root);
await find(%(open)s).click();
const line=%(line)s;
const says=()=>panelBody.all().filter(e=>e.textContent===line).length;
const seen={opened:says()};
typed(field(%(work)s,panelBody),'process');
await find(%(save)s,panelBody).click();                    // refused by the engine: the line is still true
seen.afterRefusal=says();
await find(%(save)s,panelBody).click();
seen.afterSave=says();
seen.posted=posted[posted.length-1][1].folders.work;
seen.sentence=panelBody.textContent.includes(%(sentence)s);
console.log(JSON.stringify(seen));
'''


def test_the_line_that_says_the_work_location_needs_choosing_goes_after_a_save_that_sets_it():
    """DESIGN.md 19.2 (amended): the red line under the work field stays while no choice is saved -- a refused save
    leaves it -- and disappears with the first successful save; the sentence about process files stays."""
    zh = run(_CHOOSING_GOES % {"open": "'输出目录'", "work": "'过程文件'", "save": "'保存'", "line": "'请为过程文件选一个目录。'",
                               "sentence": "'过程文件：只为完成这次任务而写的脚本'"})
    assert zh == {"opened": 1, "afterRefusal": 1, "afterSave": 0, "posted": "process", "sentence": True}
    en = run(_CHOOSING_GOES % {"open": "'Output folders'", "work": "'Process files'", "save": "'Save'",
                               "line": "'Choose a folder for process files.'", "sentence": "'Process files: scripts written only'"}, lang="en")
    assert en == {"opened": 1, "afterRefusal": 1, "afterSave": 0, "posted": "process", "sentence": True}


# -- the views' error line names the catalogs it is about (DESIGN.md 19.6) ----------------------------------------------

_ERROR_LINE = r"""
const lines = (host) => host.all().filter((e) => e.classList.contains('err')).map((e) => e.textContent);
const rows = (host) => host.all().filter((e) => e.classList.contains('registered-output')).length;
const named = new Element('div'), alone = new Element('div'), clean = new Element('div');
renderRegisteredOutputs(named, { items: [{ path: 'figures/fit.png', title: 'Fit', kind: 'figure' }], error: 'x',
  broken: ['research/results/a.outputs.json', 'research/results/b.outputs.json'] });
renderRegisteredOutputs(alone, { items: [], error: 'research/results/c.outputs.json: Cannot read it' });   // no list: the sentence only
renderRegisteredOutputs(clean, { items: [{ path: 'figures/fit.png', title: 'Fit', kind: 'figure' }], error: null });
console.log(JSON.stringify({ named: lines(named), namedRows: rows(named), alone: lines(alone), clean: lines(clean) }));
"""


def test_the_views_error_line_names_the_catalogs_it_is_about_and_the_other_rows_still_show():
    zh = _home_run(_ERROR_LINE)
    assert zh["named"] == ["输出清单暂时无法读取。 research/results/a.outputs.json research/results/b.outputs.json"]
    assert zh["namedRows"] == 1 and zh["alone"] == ["输出清单暂时无法读取。"] and zh["clean"] == []
    en = _home_run(_ERROR_LINE, lang="en")
    assert en["named"] == ["The output list could not be read. research/results/a.outputs.json research/results/b.outputs.json"]

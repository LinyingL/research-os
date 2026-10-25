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
    assert out["order"] == ["/api/workspace", "research/overview.md", "research/results/a.md"]
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

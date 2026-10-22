"""The real writing UI under Node, with a small DOM and an in-memory API.

These are user workflows: unsaved writing must survive asynchronous work,
concurrent edits must not be overwritten, references stay relative and safe,
and creating a project never asks for an external attempts/history table.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEBAPP = Path(__file__).parents[1] / "src" / "researchos" / "webapp"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

DOM = r"""
class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.kids = []; this.parentNode = null;
    this.dataset = {}; this.attrs = {}; this.on = {}; this.className = ''; this.value = ''; this.type = ''; this.disabled = false;
    this.placeholder = ''; this.checked = false; this.selectionStart = null; this.selectionEnd = null; }
  get textContent() { return this.kids.map(k => typeof k === 'string' ? k : k.textContent).join(''); }
  set textContent(s) { this.replaceChildren(s == null ? '' : String(s)); }
  get children() { return this.kids.filter(k => typeof k !== 'string'); }
  get childElementCount() { return this.children.length; }
  get lastChild() { return this.kids[this.kids.length-1]; }
  get classList() { const e = this; return {
    contains(c) { return e.className.split(/\s+/).includes(c); },
    add(c) { if (!this.contains(c)) e.className += ' ' + c; },
    remove(c) { e.className = e.className.split(/\s+/).filter(x => x !== c).join(' '); },
    toggle(c, yes) { const v = yes === undefined ? !this.contains(c) : yes; if(v) this.add(c); else this.remove(c); return v; }
  }; }
  append(...xs) { xs.forEach(x => { if (typeof x !== 'string') x.parentNode = this; this.kids.push(x); }); }
  appendChild(x) { this.append(x); return x; }
  prepend(...xs) { xs.forEach(x => { if (typeof x !== 'string') x.parentNode = this; }); this.kids.unshift(...xs); }
  replaceChildren(...xs) { this.kids.forEach(x => { if (typeof x !== 'string') x.parentNode = null; }); this.kids=[]; this.append(...xs); }
  remove() { if(this.parentNode) this.parentNode.kids=this.parentNode.kids.filter(x => x!==this); this.parentNode=null; }
  setAttribute(k,v) { this.attrs[k]=String(v); }
  getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; }
  addEventListener(k,f) { (this.on[k] ||= []).push(f); }
  async emit(k,e={}) { for(const f of this.on[k] || []) await f(Object.assign({preventDefault(){},target:this},e)); }
  async click() { if(!this.disabled) await this.emit('click'); }
  focus() { global.focused=this; }
  setSelectionRange(a,b) { this.selectionStart=a; this.selectionEnd=b; }
  all() { return this.children.flatMap(x => [x,...x.all()]); }
  querySelectorAll(selector) { return this.all().filter(e=>selector.startsWith('.') ? e.classList.contains(selector.slice(1)) : e.tagName.toLowerCase()===selector); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  replaceWith(other) { if(this.parentNode) { const parent=this.parentNode;parent.kids[parent.kids.indexOf(this)]=other;other.parentNode=parent;this.parentNode=null; } }
}
global.window = { addEventListener(k,f) { (events[k] ||= []).push(f); }, crypto: { randomUUID:()=> 'task-fixture-1' } };
const events = {}, confirmQuestions = [], answers = [];
window.confirm = q => { confirmQuestions.push(q); return answers.length ? answers.shift() : true; };
global.document = { createElement:tag=>new Element(tag), documentElement:new Element('html'), querySelectorAll:()=>[] };
global.state = { currentPath:'/projects/study', currentId:'p-study', projectLabel:'My study', dataEpoch:0, panelSubject:null };
global.currentView = 'workspace';
const root = new Element('div');
global.views = { workspace:root };
global.panel = new Element('div'); panel.className='hidden';
global.panelTitle = new Element('h2'); global.panelSubtitle = new Element('p'); global.panelBody = new Element('div');
global.openPanel = ()=>panel.classList.remove('hidden');
global.closePanel = ()=>{ panel.classList.add('hidden'); state.panelSubject=null; };
global.mayReplacePanel = ()=>true;
global.uiLang = ()=>window.ROI18N ? window.ROI18N.lang : 'zh';
global.pollGeneration = ()=>{};
global.chooseFolder = async purpose=>{ chosenPurpose=purpose; return '/work'; };
let chosenPurpose=null;
const openedFiles=[], openedAttempts=[], posted=[];
global.openFilePanel=path=>openedFiles.push(path);
window.RONode={openNode:id=>openedAttempts.push(id)};
let listed = { project:'/projects/study', id:'p-study', read_only:false, mode:'single',
  documents:[{path:'research/overview.md',kind:'overview',title:'Research question',excerpt:''}],
  files:[{path:'figures/图 (1).png',type:'figure'},{path:'code/model.do',type:'script'},{path:'data/source.csv',type:'dataset'}],
  attempts:[{id:'attempt:research/attempts.md#1',label:'#1 First check'}], tasks:[],tasks_revision:'tasks-0' };
let docs = { 'research/overview.md':{path:'research/overview.md',kind:'overview',title:'Research question',content:'# Research question\nAn open question.',revision:'doc-0'} };
let customGet=null,customPost=null;
const clone = value=>JSON.parse(JSON.stringify(value));
global.apiGet=async path=>{
  if(customGet) { const value=customGet(path); if(value!==undefined) return await value; }
  if(path==='/api/workspace') return clone(listed);
  if(path.startsWith('/api/workspace/document?')) return clone(docs[new URLSearchParams(path.split('?')[1]).get('path')]);
  throw Error('unexpected GET '+path);
};
global.apiPost=async(path,body)=>{
  posted.push([path,clone(body)]);
  if(customPost) { const value=customPost(path,body); if(value!==undefined) return await value; }
  if(path==='/api/workspace/document') {
    const file=body.path || 'research/'+(body.kind==='result'?'results':'notes')+'/abc.md';
    const doc={path:file,kind:body.kind,title:body.title,content:body.content,revision:'doc-'+posted.length};
    docs[file]=clone(doc); const ix=listed.documents.findIndex(d=>d.path===file);
    if(ix<0) listed.documents.push(clone(doc)); else listed.documents[ix]=clone(doc);
    return clone(doc);
  }
  if(path==='/api/workspace/tasks') { listed.tasks=clone(body.tasks);listed.tasks_revision='tasks-'+posted.length;return clone(listed); }
  throw Error('unexpected POST '+path);
};
const find=(label,where=root)=>[where,...where.all()].find(e=>e.tagName==='BUTTON'&&e.textContent===label);
const byClass=(cls,where=root)=>[where,...where.all()].find(e=>e.classList.contains(cls));
const typed=(node,value)=>{node.value=value;(node.on.input||[]).forEach(f=>f({target:node}));};
const field=(words,where=root)=>where.all().find(e=>e.tagName==='LABEL'&&e.children[0].textContent===words).children[1];
const sleepTick=()=>new Promise(r=>setTimeout(r,0));
function deferred() { let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject}; }
"""


def run(scenario: str, *, lang: str = "zh") -> dict:
    source = DOM + "\nwindow.ROI18N_SAVED=" + json.dumps(lang) + ";\n"
    source += (WEBAPP / "i18n.js").read_text()
    source += "\n" + (WEBAPP / "workspace.js").read_text()
    source += "\n(async()=>{\n" + scenario + "\n})().catch(e=>{console.error(e.stack);process.exitCode=1});"
    result = subprocess.run([NODE, "-"], input=source, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_create_project_requires_only_name_and_location_and_opens_the_new_home():
    out = run(r"""
let created=null;
window.ROWorkspace.onProjectCreated=async result=>{created=result;};
customPost=(path,body)=>path==='/api/projects/create'?{path:'/work/Study',id:'p-new',opened:true}:undefined;
window.ROWorkspace.openCreateProject();
typed(field('项目名称',panelBody),'Study');
await find('选择文件夹…',panelBody).click();
await find('创建项目',panelBody).click();
console.log(JSON.stringify({posted,created,chosenPurpose,words:panelBody.textContent,subtitle:panelSubtitle.textContent,location:field('保存位置',panelBody).value,dirty:window.ROWorkspace.isDirty()}));
""")
    assert out["posted"] == [["/api/projects/create", {"name": "Study", "parent": "/work", "language": "zh"}]]
    assert out["created"]["opened"]
    assert out["chosenPurpose"] == "create"
    assert out["location"] == "/work"
    assert out["subtitle"] == ""
    assert "历史表" not in out["words"]
    assert not out["dirty"]


def test_write_plan_note_and_result_as_project_files_without_importing_history():
    out = run(r"""
await window.ROWorkspace.load(root);
typed(byClass('ws-writing'),'# A flexible plan\nTry several explanations.');
await find('保存').click();
await find('新建笔记').click();
typed(byClass('ws-title-input'),'Discussion'); typed(byClass('ws-writing'),'An idea worth revisiting.');
await find('保存').click();
await find('记录结果').click();
typed(byClass('ws-title-input'),'Exploratory result'); typed(byClass('ws-writing'),'This is still uncertain.');
await find('保存').click();
console.log(JSON.stringify({posted,words:root.textContent,dirty:window.ROWorkspace.isDirty()}));
""")
    payloads = [body for path, body in out["posted"]]
    assert [p["kind"] for p in payloads] == ["overview", "note", "result"]
    assert payloads[0]["revision"] == "doc-0"
    assert payloads[1]["revision"] is None and "path" not in payloads[1]
    assert payloads[2]["content"] == "This is still uncertain."
    assert not out["dirty"]


def test_writing_surface_uses_document_actions_and_short_empty_states():
    out = run(r"""
await window.ROWorkspace.load(root);
const initial={words:root.textContent,plan:byClass('ws-writing').placeholder,intro:!!byClass('ws-intro'),path:!!byClass('ws-path')};
await find('新建笔记').click();
const note=byClass('ws-writing').placeholder;
await find('记录结果').click();
console.log(JSON.stringify({initial,note,result:byClass('ws-writing').placeholder,summary:byClass('ws-attachments').children[0].textContent,taskHeading:byClass('ws-task-title').textContent}));
""")
    assert out["initial"]["plan"] == "研究问题、思路与计划"
    assert "还没有笔记。" in out["initial"]["words"]
    assert "还没有结果记录。" in out["initial"]["words"]
    assert not out["initial"]["intro"] and not out["initial"]["path"]
    assert "查看文件" in out["initial"]["words"] and "修改历史" in out["initial"]["words"]
    assert out["note"] == "写笔记…"
    assert out["result"] == "结果、解释与待确认的问题"
    assert out["summary"] == "引用文件或尝试"
    assert out["taskHeading"] == "任务"


def test_preview_hides_editable_title_and_returns_to_unchanged_writing():
    out = run(r"""
await window.ROWorkspace.load(root);
typed(byClass('ws-title-input'),'Updated research question');
typed(byClass('ws-writing'),'# Updated research question\nAn unfinished plan.');
await find('预览').click();
const preview={titleHidden:byClass('ws-title-input').classList.contains('hidden'),writingHidden:byClass('ws-writing').classList.contains('hidden'),words:byClass('ws-preview').textContent};
await find('继续编辑').click();
console.log(JSON.stringify({preview,titleHidden:byClass('ws-title-input').classList.contains('hidden'),writingHidden:byClass('ws-writing').classList.contains('hidden'),title:byClass('ws-title-input').value,content:byClass('ws-writing').value,dirty:window.ROWorkspace.isDirty(),posted}));
""")
    assert out["preview"]["titleHidden"] and out["preview"]["writingHidden"]
    assert out["preview"]["words"].count("Updated research question") == 1
    assert not out["titleHidden"] and not out["writingHidden"]
    assert out["title"] == "Updated research question"
    assert out["content"] == "# Updated research question\nAn unfinished plan."
    assert out["dirty"] and not out["posted"]
    note = run(r"""
await window.ROWorkspace.load(root);await find('新建笔记').click();
typed(byClass('ws-title-input'),'A separate title');typed(byClass('ws-writing'),'## Finding\nAn observation.');
await find('预览').click();
console.log(JSON.stringify({words:byClass('ws-preview').textContent,titleHidden:byClass('ws-title-input').classList.contains('hidden')}));
""")
    assert note["titleHidden"]
    assert note["words"].startswith("A separate titleFinding")


@pytest.mark.parametrize("body, expected", [
    ("## Research question\nAn unfinished plan.", "Research questionAn unfinished plan."),
    ("# Revised in the body\nAn unfinished plan.", "Revised in the bodyAn unfinished plan."),
])
def test_preview_keeps_body_headings_without_inserting_the_old_title(body, expected):
    out = run(r"""
await window.ROWorkspace.load(root);
typed(byClass('ws-writing'),BODY);
await find('预览').click();
console.log(JSON.stringify({words:byClass('ws-preview').textContent,posted,content:byClass('ws-writing').value}));
""".replace("BODY", json.dumps(body)))
    assert out["words"] == expected
    assert out["content"] == body and not out["posted"]


def test_clear_task_form_leaves_the_saved_task_unchanged():
    out = run(r"""
listed.tasks=[{id:'one',text:'Existing task',owner:'Lin',due:'2026-11-02',done:false}];
await window.ROWorkspace.load(root);
await find('编辑').click();typed(field('任务'),'Unsaved task edit');
await find('清空').click();
console.log(JSON.stringify({text:field('任务').value,owner:field('负责人').value,due:field('日期').value,tasks:listed.tasks,posted,adding:!!find('添加任务'),dirty:window.ROWorkspace.isDirty()}));
""")
    assert out["text"] == out["owner"] == out["due"] == ""
    assert out["tasks"] == [{"id": "one", "text": "Existing task", "owner": "Lin", "due": "2026-11-02", "done": False}]
    assert out["adding"] and not out["dirty"] and not out["posted"]


def test_saved_document_keeps_file_actions_and_uses_short_success_message():
    out = run(r"""
await window.ROWorkspace.load(root);typed(byClass('ws-writing'),'Saved words');
customPost=(path,body)=>path==='/api/workspace/document'?{path:body.path,kind:body.kind,title:body.title,content:body.content,revision:'new',backup:'backup/previous.md'}:undefined;
await find('保存').click();
const saved=root.all().find(e=>e.getAttribute('role')==='status').textContent;
await find('查看文件').click();await find('修改历史').click();
console.log(JSON.stringify({saved,openedFiles,openedAttempts,dirty:window.ROWorkspace.isDirty()}));
""")
    assert out["saved"] == "已保存"
    assert out["openedFiles"] == out["openedAttempts"] == ["research/overview.md"]
    assert not out["dirty"]


def test_project_references_are_relative_images_are_local_and_html_is_text():
    out = run(r"""
await window.ROWorkspace.load(root); await find('记录结果').click();
typed(byClass('ws-title-input'),'A figure');
typed(byClass('ws-writing'),'<script>alert(1)</script>\n[bad](javascript:alert)\n[escape](../../../../outside.png)');
const files=root.all().find(e=>e.tagName==='SELECT'&&e.getAttribute('aria-label')==='选择项目文件');
files.value='figures/图 (1).png'; await find('插入引用').click();
files.value='code/model.do'; await find('插入引用').click();
const attempt=root.all().find(e=>e.tagName==='SELECT'&&e.getAttribute('aria-label')==='选择研究尝试');
attempt.value=listed.attempts[0].id; await find('引用尝试').click();
const content=byClass('ws-writing').value;
await find('预览').click();
const preview=byClass('ws-preview');
const image=preview.all().find(e=>e.tagName==='IMG');
const links=preview.all().filter(e=>e.tagName==='A');
await links.find(e=>e.textContent==='model.do').click();
await links.find(e=>e.textContent==='#1 First check').click();
console.log(JSON.stringify({content,src:image.src,htmlText:preview.textContent,tags:preview.all().map(e=>e.tagName),openedFiles,openedAttempts,links:links.map(e=>e.href)}));
""")
    assert "../../figures/%E5%9B%BE%20%281%29.png" in out["content"]
    assert "../../code/model.do" in out["content"]
    assert out["src"] == "/api/workspace/asset?path=figures%2F%E5%9B%BE%20(1).png"
    assert "<script>alert(1)</script>" in out["htmlText"]
    assert "SCRIPT" not in out["tags"]
    assert len(out["links"]) == 2
    assert out["openedFiles"] == ["code/model.do"]
    assert out["openedAttempts"] == ["attempt:research/attempts.md#1"]


def test_background_refresh_and_task_writes_keep_unsaved_document_and_task_form():
    out = run(r"""
await window.ROWorkspace.load(root);
typed(byClass('ws-writing'),'Unfinished paragraph');
typed(field('任务'),'Read papers');typed(field('负责人'),'Lin');typed(field('日期'),'2026-11-02');
docs['research/overview.md'].content='Changed outside';docs['research/overview.md'].revision='doc-external';
await window.ROWorkspace.load(root,true);
answers.push(false);const allowed=window.ROWorkspace.mayLeave();
await find('添加任务').click();
console.log(JSON.stringify({allowed,questions:confirmQuestions,content:byClass('ws-writing').value,posted,tasks:listed.tasks,dirty:window.ROWorkspace.isDirty()}));
""")
    assert not out["allowed"]
    assert len(out["questions"]) == 1
    assert out["content"] == "Unfinished paragraph"
    task = out["tasks"][0]
    assert task["text"] == "Read papers" and task["owner"] == "Lin" and task["due"] == "2026-11-02"
    assert out["posted"][0][1]["revision"] == "tasks-0"
    assert out["dirty"]


def test_newer_typing_survives_a_slow_save_response():
    out = run(r"""
await window.ROWorkspace.load(root);
const waiting=deferred(); customPost=(path)=>path==='/api/workspace/document'?waiting.promise:undefined;
typed(byClass('ws-writing'),'First paragraph');
const saving=find('保存').click();
typed(byClass('ws-writing'),'First paragraph\nMore writing while saving');
waiting.resolve({path:'research/overview.md',kind:'overview',title:'Research question',content:'First paragraph',revision:'doc-1'});
await saving;
const after=byClass('ws-writing').value;
customPost=null; await find('保存').click();
console.log(JSON.stringify({after,posted,dirty:window.ROWorkspace.isDirty()}));
""")
    assert "More writing while saving" in out["after"]
    assert out["posted"][1][1]["revision"] == "doc-1"
    assert "More writing while saving" in out["posted"][1][1]["content"]
    assert not out["dirty"]


def test_conflicting_file_is_never_overwritten_and_reload_is_explicit():
    out = run(r"""
await window.ROWorkspace.load(root);
typed(byClass('ws-writing'),'My unsaved paragraph');
customPost=()=>Promise.reject(Object.assign(Error('external change'),{state:'workspace_conflict'}));
await find('保存').click();
const kept=byClass('ws-writing').value;
await window.ROWorkspace.load(root,true);
const afterRefresh=byClass('ws-writing').value;
docs['research/overview.md'].content='Latest disk content';docs['research/overview.md'].revision='doc-latest';
answers.push(false);await find('重新载入文件').click();await sleepTick();
const afterCancel=byClass('ws-writing').value;
answers.push(true);await find('重新载入文件').click();await sleepTick();
console.log(JSON.stringify({kept,afterRefresh,afterCancel,afterReload:byClass('ws-writing').value,dirty:window.ROWorkspace.isDirty(),posted}));
""")
    assert out["kept"] == out["afterRefresh"] == out["afterCancel"] == "My unsaved paragraph"
    assert out["afterReload"] == "Latest disk content"
    assert not out["dirty"]
    assert len(out["posted"]) == 1


def test_task_conflict_keeps_form_and_reload_merges_it_with_latest_list():
    out = run(r"""
await window.ROWorkspace.load(root);
typed(field('任务'),'Read papers');typed(field('负责人'),'Lin');
customPost=()=>Promise.reject(Object.assign(Error('external change'),{state:'workspace_conflict'}));
await find('添加任务').click();
const afterFailure=field('任务').value;
listed.tasks=[{id:'external',text:'New task from outside',done:false,owner:'Other',due:''}];listed.tasks_revision='external-1';
await find('重新载入任务').click();
const afterReload=field('任务').value;
customPost=null;await find('添加任务').click();
console.log(JSON.stringify({afterFailure,afterReload,posted,tasks:listed.tasks}));
""")
    assert out["afterFailure"] == out["afterReload"] == "Read papers"
    assert [t["text"] for t in out["tasks"]] == ["New task from outside", "Read papers"]
    assert out["posted"][-1][1]["revision"] == "external-1"


def test_old_project_document_response_cannot_replace_new_project_writing():
    out = run(r"""
const waiting=deferred();customGet=path=>path.includes('/document?')?waiting.promise:undefined;
const pending=window.ROWorkspace.load(root);
await sleepTick();
window.ROWorkspace.reset();state.dataEpoch+=1;state.currentPath='/projects/second';
listed.project=state.currentPath;customGet=null;docs['research/overview.md'].content='Second project question';
await window.ROWorkspace.load(root);
waiting.resolve({path:'research/overview.md',kind:'overview',title:'Old project',content:'Stale answer',revision:'old'});
await pending;
console.log(JSON.stringify({content:byClass('ws-writing').value,words:root.textContent}));
""")
    assert out["content"] == "Second project question"
    assert "Old project" not in out["words"]


def test_readonly_project_can_preview_but_cannot_create_or_save():
    out = run(r"""
listed.read_only=true;await window.ROWorkspace.load(root);
await find('保存').click();await find('新建笔记').click();await find('预览').click();
console.log(JSON.stringify({posted,disabled:byClass('ws-writing').disabled,preview:byClass('ws-preview').textContent,words:root.textContent}));
""")
    assert out["posted"] == []
    assert out["disabled"]
    assert "An open question." in out["preview"]


def test_english_writing_ui_and_create_dialog_have_no_chinese_labels():
    out = run(r"""
listed.files=listed.files.filter(f=>!/[\u4e00-\u9fff]/.test(f.path));
await window.ROWorkspace.load(root);
await find('New note').click();
const screen=root.textContent+root.all().map(e=>e.placeholder||'').join('');
window.ROWorkspace.openCreateProject();
const dialog=panelTitle.textContent+panelSubtitle.textContent+panelBody.textContent+panelBody.all().map(e=>e.placeholder||'').join('');
console.log(JSON.stringify({screen,dialog}));
""", lang="en")
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in out["screen"] + out["dialog"])


def test_refresh_files_finds_external_stata_outputs_without_losing_writing():
    out = run(r"""
await window.ROWorkspace.load(root);await find('记录结果').click();
typed(byClass('ws-title-input'),'Current finding');typed(byClass('ws-writing'),'An unfinished interpretation.');
listed.files.push({path:'analysis/stata-new.csv',type:'dataset'});
await find('刷新文件').click();
const files=root.all().find(e=>e.tagName==='SELECT'&&e.getAttribute('aria-label')==='选择项目文件');
files.value='analysis/stata-new.csv';await find('插入引用').click();
console.log(JSON.stringify({title:byClass('ws-title-input').value,content:byClass('ws-writing').value,files:files.children.map(e=>e.value),dirty:window.ROWorkspace.isDirty()}));
""")
    assert out["title"] == "Current finding"
    assert out["content"].startswith("An unfinished interpretation.")
    assert "../../analysis/stata-new.csv" in out["content"]
    assert "analysis/stata-new.csv" in out["files"]
    assert out["dirty"]


def test_typing_during_document_navigation_never_disappears_with_slow_response():
    out = run(r"""
listed.documents.push({path:'research/notes/discussion.md',kind:'note',title:'Discussion'});
await window.ROWorkspace.load(root);
const waiting=deferred();customGet=path=>path.includes('discussion.md')?waiting.promise:undefined;
const opening=find('Discussion').click();
typed(byClass('ws-writing'),'Writing added while another document loads.');
waiting.resolve({path:'research/notes/discussion.md',kind:'note',title:'Discussion',content:'The other note',revision:'note-1'});
await opening;
console.log(JSON.stringify({content:byClass('ws-writing').value,status:byClass('ws-status').textContent,dirty:window.ROWorkspace.isDirty()}));
""")
    assert out["content"] == "Writing added while another document loads."
    assert out["dirty"]


def test_tasks_can_finish_edit_and_remove_independently_of_the_research_plan():
    out = run(r"""
listed.tasks=[{id:'one',text:'Old task',owner:'Lin',due:'',done:false}];
await window.ROWorkspace.load(root);
typed(byClass('ws-writing'),'An unfinished plan');
const check=byClass('ws-task').children[0];check.checked=true;await check.emit('change');
await find('编辑').click();typed(field('任务'),'Revised task');await find('保存任务').click();
const afterEdit=clone(listed.tasks);
await find('移除').click();
console.log(JSON.stringify({afterEdit,tasks:listed.tasks,content:byClass('ws-writing').value,posted,dirty:window.ROWorkspace.isDirty()}));
""")
    assert out["afterEdit"] == [{"id": "one", "text": "Revised task", "owner": "Lin", "due": "", "done": True}]
    assert out["tasks"] == []
    assert out["content"] == "An unfinished plan"
    assert out["dirty"]
    assert all(path == "/api/workspace/tasks" for path, _ in out["posted"])


def test_damaged_tasks_and_unreadable_document_do_not_disable_other_writing():
    out = run(r"""
listed.tasks_error={code:'workspace_invalid',message:'Invalid task JSON'};
listed.documents.push({path:'research/notes/bad.md',kind:'note',title:'Bad note',error:{code:'cloud',message:'Not downloaded'}});
await window.ROWorkspace.load(root);
const blocked=field('任务').disabled;
await find('新建笔记').click();typed(byClass('ws-title-input'),'Working note');typed(byClass('ws-writing'),'Still able to write');await find('保存').click();
console.log(JSON.stringify({blocked,posted,words:root.textContent}));
""")
    assert out["blocked"]
    assert len(out["posted"]) == 1
    assert out["posted"][0][1]["kind"] == "note"
    assert "暂时无法读取" in out["words"]


def test_saved_file_with_history_warning_does_not_offer_to_retry_the_write():
    out = run(r"""
await window.ROWorkspace.load(root);typed(byClass('ws-writing'),'Saved words');
customPost=(path,body)=>path==='/api/workspace/document'?{path:body.path,kind:body.kind,title:body.title,content:body.content,revision:'new',history_error:'temporarily busy'}:undefined;
await find('保存').click();
console.log(JSON.stringify({words:root.textContent,dirty:window.ROWorkspace.isDirty()}));
""")
    assert "文件已保存，但修改历史暂时未能记录。" in out["words"]
    assert not out["dirty"]


def test_member_submission_includes_only_saved_file_and_browser_close_is_guarded():
    out = run(r"""
listed.mode='member';await window.ROWorkspace.load(root);
const proposals=[];window.ROWorkspace.onPropose=files=>proposals.push(files);
typed(byClass('ws-writing'),'Draft content');await find('提交修改').click();
let prevented=false;const event={preventDefault(){prevented=true;}};events.beforeunload[0](event);
await find('保存').click();await find('提交修改').click();
console.log(JSON.stringify({proposals,prevented,returnValue:event.returnValue}));
""")
    assert out["proposals"] == [["research/overview.md"]]
    assert out["prevented"] and out["returnValue"] == ""


def test_foreign_window_project_switch_preserves_writing_and_task_drafts():
    out = run(r"""
await window.ROWorkspace.load(root);
typed(byClass('ws-writing'),'Keep this unfinished interpretation.');typed(field('任务'),'Keep this task too');
window.ROWorkspace.projectChangedElsewhere();
const blocked=find('保存').disabled, before=byClass('ws-writing').value;
customPost=(path,body)=>path==='/api/projects/switch'?{}:undefined;
await find('保存').click();
await find('重新打开这个项目').click();
console.log(JSON.stringify({blocked,before,after:byClass('ws-writing').value,task:field('任务').value,posted,dirty:window.ROWorkspace.isDirty(),enabled:!find('保存').disabled}));
""")
    assert out["blocked"] and out["enabled"]
    assert out["before"] == out["after"] == "Keep this unfinished interpretation."
    assert out["task"] == "Keep this task too" and out["dirty"]
    assert [path for path, _ in out["posted"]] == ["/api/projects/switch"]


def test_result_submission_includes_local_references_for_pi_review():
    out = run(r"""
listed.mode='member';await window.ROWorkspace.load(root);
const proposals=[];window.ROWorkspace.onPropose=files=>proposals.push(files);
typed(byClass('ws-writing'),'![Result](../figures/%E5%9B%BE%20%281%29.png)\n[Script](../code/model.do)\n[Remote](https://example.com/file.csv)\n[Outside](../../secret.csv)');
await find('保存').click();await find('提交修改').click();
console.log(JSON.stringify({proposals}));
""")
    assert out["proposals"] == [["research/overview.md", "figures/图 (1).png", "code/model.do"]]


def test_task_save_uses_task_annotation_from_the_server():
    out = run(r"""
listed.mode='member';await window.ROWorkspace.load(root);
customPost=(path,body)=>path==='/api/workspace/tasks'?{tasks:body.tasks,tasks_revision:'new',tasks_officialness:{state:'local',label:'本地草稿'}}:undefined;
typed(field('任务'),'Consider another explanation');await find('添加任务').click();
console.log(JSON.stringify({words:byClass('ws-tasks').textContent}));
""")
    assert "本地草稿" in out["words"]

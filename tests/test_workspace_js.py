"""The real writing UI under Node, with a small DOM and an in-memory API.

These are user workflows: unsaved writing must survive asynchronous work,
concurrent edits must not be overwritten, references stay relative and safe,
and creating a project never asks for an external attempts/history table.
"""
from __future__ import annotations

import json
import re
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
  focus() { global.focused=this; document.activeElement=this; }
  before(...xs) { const parent=this.parentNode; xs.forEach(x => { if(x.parentNode) x.parentNode.kids=x.parentNode.kids.filter(k=>k!==x); x.parentNode=parent; }); parent.kids.splice(parent.kids.indexOf(this),0,...xs); }
  setSelectionRange(a,b) { this.selectionStart=a; this.selectionEnd=b; }
  all() { return this.children.flatMap(x => [x,...x.all()]); }
  querySelectorAll(selector) { return this.all().filter(e=>selector.startsWith('.') ? e.classList.contains(selector.slice(1)) : e.tagName.toLowerCase()===selector); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  replaceWith(other) { if(this.parentNode) { const parent=this.parentNode;parent.kids[parent.kids.indexOf(this)]=other;other.parentNode=parent;this.parentNode=null; } }
}
global.window = { addEventListener(k,f) { (events[k] ||= []).push(f); }, crypto: { randomUUID:()=> 'task-fixture-1' } };
const events = {}, confirmQuestions = [], answers = [];
window.confirm = q => { confirmQuestions.push(q); return answers.length ? answers.shift() : true; };
global.document = { createElement:tag=>new Element(tag), documentElement:new Element('html'), querySelectorAll:()=>[], activeElement:null };
global.state = { currentPath:'/projects/study', currentId:'p-study', projectLabel:'My study', dataEpoch:0, panelSubject:null };
global.currentView = 'workspace';
const root = new Element('div');
global.views = { workspace:root };
global.panel = new Element('div'); panel.className='hidden';
global.panelTitle = new Element('h2'); global.panelSubtitle = new Element('p'); global.panelBody = new Element('div');
global.openPanel = ()=>panel.classList.remove('hidden');
global.closePanel = ()=>{ panel.classList.add('hidden'); state.panelSubject=null; };
global.mayReplacePanel = ()=>true;
global.chevronIcon = ()=>{ const e = new Element('svg'); e.className = 'res-chevron'; return e; };
global.uiLang = ()=>window.ROI18N ? window.ROI18N.lang : 'zh';
let generations = 0;
global.pollGeneration = ()=>{ generations += 1; };
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
// AI agents (DESIGN.md Section 17): what this Mac has, who I am, the project's runs and one run's log.
// Every one is served only when a scenario sets it, so a page without them still has to work.
const gets=[];
let agentsInfo=null, meInfo=null, shareInfo=null, modelsList=null, runsList={runs:[],active:null}, runLogs={};
// 18.1 item 5: what an undo would do (per run id; a default plan otherwise) and the project's files over 16 MB.
let runPlans={}, bigFiles={count:0,largest:[],partial:false};
const defaultPlan=run=>({run:clone(run),restore:[{path:'analysis.py',time:'2026-10-21T09:41:12+02:00'}],remove:[{path:'out/new.csv',time:'2026-10-21T09:40:03+02:00'}],cannot:[]});
const agentFixture=()=>({
  runners:[{runner:'claude-code',label:'Claude Code',path:'/usr/local/bin/claude',version:'2.1.0',problem:null,found:true},
           {runner:'opencode',label:'OpenCode',path:null,version:null,problem:'missing',found:false},
           {runner:'codex',label:'Codex',path:'/opt/codex',version:'0.162.0',problem:null,found:true},
           {runner:'deepseek-harness',label:'DeepSeek Harness',path:'/apps/dsh',version:'0.2.1-rc.2',problem:null,found:true}],
  agents:[{runner:'claude-code',model:'sonnet',owner:'agent:claude-code:sonnet',label:'Claude Code · sonnet',available:true}],
  harness_models:['deepseek-account/deepseek-flash','deepseek-official/deepseek-v4-pro'],
  reasoning_efforts:{'claude-code':{'claude-sonnet-5-5':['low','medium','high','xhigh','max'],'claude-opus-4-6':['low','medium','high','max']},'deepseek-harness':{'deepseek-account/deepseek-flash':['off','low','high','max'],'deepseek-official/deepseek-v4-pro':['off','low','high','max']}},
  mcp:true, claude_models:['claude-sonnet-5-5','claude-sonnet-4-6','claude-opus-5-5','claude-opus-4-6','claude-haiku-4-5-20251001'], timeout_minutes:120, max_budget_usd:null });
const mkRun=(o)=>Object.assign({id:'r-20261021T090000Z-aaaaaa',task_id:'t1',task_text:'Run the model',owner:'agent:claude-code:sonnet',
  label:'Claude Code · sonnet',runner:'claude-code',model:'sonnet',state:'running',reverted:false,kind:'start',parent:null,
  created:'2026-10-21T09:00:00Z',started:null,ended:null,question:null,report:null,error:null,cost_usd:null,by:null,by_name:null,
  violations:0,changes:0,can_stop:false,can_reply:false,can_revert:false},o);
const agentTask=(o)=>Object.assign({id:'t1',text:'Run the model',owner:'agent:claude-code:sonnet',due:'',done:false},o);
const setRuns=(runs,active)=>{ runsList={runs:runs,active:active===undefined?(runs.find(r=>['preparing','running'].includes(r.state))||{}).id||null:active}; };
global.apiGet=async path=>{
  gets.push(path);
  if(customGet) { const value=customGet(path); if(value!==undefined) return await value; }
  if(path==='/api/workspace') return clone(listed);
  if(path.startsWith('/api/workspace/document?')) return clone(docs[new URLSearchParams(path.split('?')[1]).get('path')]);
  if(path==='/api/agents'&&agentsInfo) return clone(agentsInfo);
  if(path==='/api/agents/large-files') return clone(bigFiles);
  let plan=/^\/api\/agents\/runs\/([^/]+)\/revert-plan$/.exec(path);
  if(plan) { const id=decodeURIComponent(plan[1]); return clone(runPlans[id]||defaultPlan(runById(id))); }
  if(path==='/api/agents/runs'&&runsList) return clone(runsList);
  if(path.startsWith('/api/agents/runs/')&&runLogs[decodeURIComponent(path.split('/').pop())]) return clone(runLogs[decodeURIComponent(path.split('/').pop())]);
  if(path.startsWith('/api/agents/models?runner=')&&modelsList) return {models:clone(modelsList)};
  if(path==='/api/me'&&meInfo) return clone(meInfo);
  if(path==='/api/share'&&shareInfo) return clone(shareInfo);
  throw Error('unexpected GET '+path);
};
const runById=id=>runsList.runs.find(r=>r.id===id);
global.apiPost=async(path,body)=>{
  posted.push([path,clone(body)]);
  if(customPost) { const value=customPost(path,body); if(value!==undefined) return await value; }
  if(path==='/api/agents/runs') {
    const run=mkRun({id:'r-20261021T09'+String(10+runsList.runs.length)+'00Z-bbbbbb',task_id:body.task,state:'preparing',can_stop:true});
    runsList.runs.unshift(run); runsList.active=run.id; return clone(run);
  }
  let m=/^\/api\/agents\/runs\/([^/]+)\/(stop|reply|revert)$/.exec(path);
  if(m) {
    const run=runById(decodeURIComponent(m[1]));
    if(m[2]==='stop') { Object.assign(run,{state:'stopped',can_stop:false,can_reply:true}); runsList.active=null; return clone(run); }
    if(m[2]==='revert') {
      // what the engine does with `files` (18.1 item 5): the checked ones are done, the other offered ones are kept
      const plan=runPlans[run.id]||defaultPlan(run), files=body.files;
      const offered=[...plan.restore,...plan.remove].map(i=>i.path), checked=files===undefined?offered:files;
      const kept=offered.filter(p=>!checked.includes(p)).map(p=>({path:p,reason:'kept'}));
      Object.assign(run,{reverted:true,can_revert:false,can_reply:false});
      if(kept.length) run.revert_skipped=kept.length+plan.cannot.length;
      return {run:clone(run),restored:plan.restore.map(i=>i.path).filter(p=>checked.includes(p)),removed:plan.remove.map(i=>i.path).filter(p=>checked.includes(p)),skipped:[...plan.cannot,...kept]};
    }
    const next=mkRun({id:'r-20261021T0959'+runsList.runs.length+'0Z-cccccc',task_id:run.task_id,state:'running',can_stop:true,parent:run.id,kind:run.state==='needs_input'?'answer':'send_back',message:body.message});
    Object.assign(run,{can_reply:false}); runsList.runs.unshift(next); runsList.active=next.id; return clone(next);
  }
  if(path==='/api/settings/agents') {
    if(body.agents) agentsInfo.agents=body.agents.map(a=>Object.assign({},a,{owner:'agent:'+a.runner+':'+a.model,label:(a.runner==='claude-code'?'Claude Code':'OpenCode')+' · '+a.model,available:true}));
    if(body.commands) for(const [runner,p] of Object.entries(body.commands)) Object.assign(agentsInfo.runners.find(r=>r.runner===runner),{path:p,version:'0.9.0',problem:null,found:true});
    if(body.timeout_minutes) agentsInfo.timeout_minutes=body.timeout_minutes;
    if('max_budget_usd' in body) agentsInfo.max_budget_usd=body.max_budget_usd;
    return clone(agentsInfo);
  }
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
// The page polls a running run every 2 s; the scenario takes those timers over and fires them by hand.
const timers=[], realSetTimeout=global.setTimeout, realClearTimeout=global.clearTimeout;
global.setTimeout=(f,ms,...a)=>{ if(ms>=2000){const t={f,ms,cancelled:false,fired:false};timers.push(t);return t;} return realSetTimeout(f,ms,...a); };
global.clearTimeout=t=>{ if(t&&typeof t==='object'&&'cancelled' in t) t.cancelled=true; else realClearTimeout(t); };
const waiting=()=>timers.filter(t=>!t.cancelled&&!t.fired).length;
const tick=async()=>{ for(const t of timers.filter(t=>!t.cancelled&&!t.fired)) { t.fired=true; await t.f(); } await sleepTick(); await sleepTick(); };
const find=(label,where=root)=>[where,...where.all()].find(e=>e.tagName==='BUTTON'&&e.textContent===label);
const byClass=(cls,where=root)=>[where,...where.all()].find(e=>e.classList.contains(cls));
const typed=(node,value)=>{if(node.classList.contains('hidden'))throw Error('Cannot type into a hidden field');node.value=value;(node.on.input||[]).forEach(f=>f({target:node}));};
const editDocument=async()=>{const edit=find(window.ROI18N.t('编辑'),byClass('ws-editor-host'));if(edit&&!edit.classList.contains('hidden'))await edit.click();};
const writeDocument=async value=>{await editDocument();typed(byClass('ws-writing'),value);};
const titleDocument=async value=>{await editDocument();typed(byClass('ws-title-input'),value);};
const field=(words,where=root)=>where.all().find(e=>e.tagName==='LABEL'&&e.children[0].textContent===words).children[1];
const sleepTick=()=>new Promise(r=>setTimeout(r,0));
const buttonsIn=node=>node.all().filter(e=>e.tagName==='BUTTON').map(e=>e.textContent);
const optionsOf=select=>select.children.map(o=>[o.textContent,o.value]);
// The owner control is a select: pick by what is shown, then (for 其他人…) type the name.
const pickOwner=async (label,words='负责人')=>{ const s=field(words); s.value=s.children.find(o=>o.textContent===label).value; await s.emit('change'); };
const noChinese=text=>!/[\u3400-\u9fff]/.test(text);
function deferred() { let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject}; }
"""


def run(scenario: str, *, lang: str = "zh") -> dict:
    source = DOM + "\nwindow.ROI18N_SAVED=" + json.dumps(lang) + ";\n"
    source += (WEBAPP / "i18n.js").read_text()
    source += "\n" + (WEBAPP / "workspace.js").read_text()
    source += "\n(async()=>{\n" + scenario + "\n})().catch(e=>{console.error(e.stack);process.exitCode=1});"
    # The script goes in on stdin, not as an argument: it carries whole page modules,
    # and Linux caps a single argument at 128 KB (E2BIG).
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
await writeDocument('# A flexible plan\nTry several explanations.');
await find('保存').click();
await find('新建笔记').click();
await titleDocument('Discussion'); await writeDocument('An idea worth revisiting.');
await find('保存').click();
await find('记录结果').click();
await titleDocument('Exploratory result'); await writeDocument('This is still uncertain.');
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


@pytest.mark.parametrize("kind, path", [
    ("overview", "research/overview.md"),
    ("note", "research/notes/discussion.md"),
    ("result", "research/results/finding.md"),
])
def test_existing_documents_open_in_preview_and_only_edit_after_a_click(kind, path):
    out = run(r"""
const path=DOC_PATH,kind=DOC_KIND;
listed.documents=[{path,kind,title:'Read this'}];
docs[path]={path,kind,title:'Read this',content:'# Read this\nAn observation.',revision:'existing'};
await window.ROWorkspace.load(root);
await find('Read this',byClass('ws-documents')).click();
const initial={preview:!byClass('ws-preview').classList.contains('hidden'),
  writing:!byClass('ws-writing').classList.contains('hidden'),title:!byClass('ws-title-input').classList.contains('hidden'),
  save:!find('保存').classList.contains('hidden'),attachments:!byClass('ws-attachments').classList.contains('hidden'),words:byClass('ws-preview').textContent};
await find('编辑',byClass('ws-editor-host')).click();
const editing={preview:!byClass('ws-preview').classList.contains('hidden'),
  writing:!byClass('ws-writing').classList.contains('hidden'),save:!find('保存').classList.contains('hidden'),
  attachments:!byClass('ws-attachments').classList.contains('hidden'),focused:focused===byClass('ws-writing')};
typed(byClass('ws-writing'),'# Read this\nA revised observation.');
await find('保存').click();
console.log(JSON.stringify({initial,editing,after:{preview:!byClass('ws-preview').classList.contains('hidden'),
  writing:!byClass('ws-writing').classList.contains('hidden'),words:byClass('ws-preview').textContent,dirty:window.ROWorkspace.isDirty()}}));
""".replace("DOC_PATH", json.dumps(path)).replace("DOC_KIND", json.dumps(kind)))
    assert out["initial"] == {"preview": True, "writing": False, "title": False,
                              "save": False, "attachments": False, "words": "Read thisAn observation."}
    assert out["editing"] == {"preview": False, "writing": True, "save": True, "attachments": True, "focused": True}
    assert out["after"] == {"preview": True, "writing": False, "words": "Read thisA revised observation.", "dirty": False}


def test_new_documents_start_in_editing_and_read_only_documents_stay_in_preview():
    out = run(r"""
await window.ROWorkspace.load(root);
const fresh=[];
for (const label of ['新建笔记','记录结果']) {
  await find(label).click();
  fresh.push({writing:!byClass('ws-writing').classList.contains('hidden'),
    preview:!byClass('ws-preview').classList.contains('hidden'),focused:focused===byClass('ws-title-input')});
}
window.ROWorkspace.reset();listed.read_only=true;listed.can_write=false;
await window.ROWorkspace.load(root);
console.log(JSON.stringify({fresh,readonly:{preview:!byClass('ws-preview').classList.contains('hidden'),
  editHidden:find('编辑',byClass('ws-editor-host')).classList.contains('hidden'),
  editDisabled:find('编辑',byClass('ws-editor-host')).disabled,saveHidden:find('保存').classList.contains('hidden')}}));
""")
    assert out["fresh"] == [{"writing": True, "preview": False, "focused": True}] * 2
    assert out["readonly"] == {"preview": True, "editHidden": True, "editDisabled": True, "saveHidden": True}


def test_background_refresh_preserves_preview_and_edit_modes_and_unsaved_text():
    out = run(r"""
await window.ROWorkspace.load(root);
docs['research/overview.md'].revision='external-1';
await window.ROWorkspace.load(root,true);
const readMode=!byClass('ws-preview').classList.contains('hidden');
await find('编辑',byClass('ws-editor-host')).click();
docs['research/overview.md'].revision='external-2';
await window.ROWorkspace.load(root,true);
const editMode=!byClass('ws-writing').classList.contains('hidden');
const area=byClass('ws-writing');typed(area,'An unfinished thought.');
docs['research/overview.md'].revision='external-3';
await window.ROWorkspace.load(root,true);
console.log(JSON.stringify({readMode,editMode,sameArea:area===byClass('ws-writing'),
  words:byClass('ws-writing').value,hidden:byClass('ws-writing').classList.contains('hidden'),dirty:window.ROWorkspace.isDirty()}));
""")
    assert out == {"readMode": True, "editMode": True, "sameArea": True,
                   "words": "An unfinished thought.", "hidden": False, "dirty": True}


def test_preview_hides_editable_title_and_returns_to_unchanged_writing():
    out = run(r"""
await window.ROWorkspace.load(root);
await titleDocument('Updated research question');
await writeDocument('# Updated research question\nAn unfinished plan.');
await find('预览').click();
const preview={titleHidden:byClass('ws-title-input').classList.contains('hidden'),writingHidden:byClass('ws-writing').classList.contains('hidden'),words:byClass('ws-preview').textContent};
await find('编辑',byClass('ws-editor-host')).click();
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
await titleDocument('A separate title');await writeDocument('## Finding\nAn observation.');
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
await writeDocument(BODY);
await find('预览').click();
console.log(JSON.stringify({words:byClass('ws-preview').textContent,posted,content:byClass('ws-writing').value}));
""".replace("BODY", json.dumps(body)))
    assert out["words"] == expected
    assert out["content"] == body and not out["posted"]


def test_clear_task_form_leaves_the_saved_task_unchanged():
    out = run(r"""
listed.tasks=[{id:'one',text:'Existing task',owner:'Lin',due:'2026-11-02',done:false}];
await window.ROWorkspace.load(root);
await find('编辑',byClass('ws-tasks')).click();typed(field('任务'),'Unsaved task edit');
await find('清空').click();
console.log(JSON.stringify({text:field('任务').value,owner:field('负责人').value,due:field('日期').value,tasks:listed.tasks,posted,adding:!!find('添加任务'),dirty:window.ROWorkspace.isDirty()}));
""")
    assert out["text"] == out["owner"] == out["due"] == ""
    assert out["tasks"] == [{"id": "one", "text": "Existing task", "owner": "Lin", "due": "2026-11-02", "done": False}]
    assert out["adding"] and not out["dirty"] and not out["posted"]


def test_saved_document_keeps_file_actions_and_uses_short_success_message():
    out = run(r"""
await window.ROWorkspace.load(root);await writeDocument('Saved words');
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
await titleDocument('A figure');
await writeDocument('<script>alert(1)</script>\n[bad](javascript:alert)\n[escape](../../../../outside.png)');
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
await writeDocument('Unfinished paragraph');
typed(field('任务'),'Read papers');await pickOwner('其他人…');typed(field('姓名'),'Lin');typed(field('日期'),'2026-11-02');
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
await writeDocument('First paragraph');
const saving=find('保存').click();
await writeDocument('First paragraph\nMore writing while saving');
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
await writeDocument('My unsaved paragraph');
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
typed(field('任务'),'Read papers');await pickOwner('其他人…');typed(field('姓名'),'Lin');
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
await find('保存').click();await find('新建笔记').click();
console.log(JSON.stringify({posted,disabled:byClass('ws-writing').disabled,preview:byClass('ws-preview').textContent,
  visible:!byClass('ws-preview').classList.contains('hidden'),words:root.textContent}));
""")
    assert out["posted"] == []
    assert out["disabled"]
    assert out["visible"]
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
await titleDocument('Current finding');await writeDocument('An unfinished interpretation.');
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
await writeDocument('Writing added while another document loads.');
waiting.resolve({path:'research/notes/discussion.md',kind:'note',title:'Discussion',content:'The other note',revision:'note-1'});
await opening;
console.log(JSON.stringify({content:byClass('ws-writing').value,status:byClass('ws-status').textContent,dirty:window.ROWorkspace.isDirty()}));
""")
    assert out["content"] == "Writing added while another document loads."
    assert out["dirty"]


@pytest.mark.parametrize("count", [0, 2, 6])
def test_only_the_three_newest_tasks_show_their_details_by_default(count):
    out = run(r"""
listed.tasks=Array.from({length:COUNT},(_,i)=>agentTask({id:'t'+i,text:'Task '+i}));
await window.ROWorkspace.load(root);await sleepTick();
console.log(JSON.stringify({rows:root.all().filter(e=>e.classList.contains('ws-task')).map(row=>({
  title:byClass('ws-task-summary',row).textContent,open:byClass('ws-task-details',row).open,
  check:row.children[0].type,actions:buttonsIn(byClass('ws-task-body',row))})),posted}));
""".replace("COUNT", str(count)))
    assert [row["title"] for row in out["rows"]] == [f"Task {i}" for i in range(count)]
    assert [row["open"] for row in out["rows"]] == [i >= count - 3 for i in range(count)]
    assert all(row["check"] == "checkbox" and "编辑" in row["actions"] for row in out["rows"])
    assert out["posted"] == []


def test_task_disclosures_keep_manual_choices_and_reply_drafts_through_refresh():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=Array.from({length:6},(_,i)=>agentTask({id:'t'+i,text:'Task '+i}));
setRuns([mkRun({task_id:'t0',state:'needs_input',question:'Which sample?',can_reply:true})]);
await window.ROWorkspace.load(root);await sleepTick();
const boxes=()=>root.all().filter(e=>e.classList.contains('ws-task-details'));
// A refresh can land before the native toggle event is delivered.
boxes()[0].open=true;boxes()[5].open=false;
await window.ROWorkspace.load(root,true);await sleepTick();
const afterRefresh=boxes().map(e=>e.open);
await find('回答',boxes()[0]).click();typed(byClass('ws-agent-reply').children[0],'Keep the balanced sample.');
boxes()[0].open=false;await boxes()[0].emit('toggle');
await window.ROWorkspace.load(root,true);await sleepTick();
const closed=boxes()[0].open;
boxes()[0].open=true;await boxes()[0].emit('toggle');
console.log(JSON.stringify({afterRefresh,closed,reply:byClass('ws-agent-reply').children[0].value,dirty:window.ROWorkspace.isDirty(),posted}));
""")
    assert out["afterRefresh"] == [True, False, False, True, True, False]
    assert not out["closed"]
    assert out["reply"] == "Keep the balanced sample."
    assert out["dirty"] and out["posted"] == []


def test_adding_a_task_updates_default_disclosures_without_resetting_manual_choices():
    out = run(r"""
listed.tasks=Array.from({length:6},(_,i)=>({id:'t'+i,text:'Task '+i,owner:'Demo'}));
await window.ROWorkspace.load(root);await sleepTick();
const boxes=()=>root.all().filter(e=>e.classList.contains('ws-task-details'));
boxes()[0].open=true;await boxes()[0].emit('toggle');
typed(field('任务'),'New task');await find('添加任务').click();await sleepTick();
const added=boxes().map(e=>e.open);
window.ROWorkspace.reset();await window.ROWorkspace.load(root);await sleepTick();
console.log(JSON.stringify({added,reopened:boxes().map(e=>e.open),tasks:listed.tasks.length}));
""")
    assert out["added"] == [True, False, False, False, True, True, True]
    assert out["reopened"] == [False, False, False, False, True, True, True]
    assert out["tasks"] == 7


def test_tasks_can_finish_edit_and_remove_independently_of_the_research_plan():
    out = run(r"""
listed.tasks=[{id:'one',text:'Old task',owner:'Lin',due:'',done:false}];
await window.ROWorkspace.load(root);
await writeDocument('An unfinished plan');
const check=byClass('ws-task').children[0];check.checked=true;await check.emit('change');
await find('编辑',byClass('ws-tasks')).click();typed(field('任务'),'Revised task');await find('保存任务').click();
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
await find('新建笔记').click();await titleDocument('Working note');await writeDocument('Still able to write');await find('保存').click();
console.log(JSON.stringify({blocked,posted,words:root.textContent}));
""")
    assert out["blocked"]
    assert len(out["posted"]) == 1
    assert out["posted"][0][1]["kind"] == "note"
    assert "暂时无法读取" in out["words"]


def test_saved_file_with_history_warning_does_not_offer_to_retry_the_write():
    out = run(r"""
await window.ROWorkspace.load(root);await writeDocument('Saved words');
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
await writeDocument('Draft content');await find('提交修改').click();
let prevented=false;const event={preventDefault(){prevented=true;}};events.beforeunload[0](event);
await find('保存').click();await find('提交修改').click();
console.log(JSON.stringify({proposals,prevented,returnValue:event.returnValue}));
""")
    assert out["proposals"] == [["research/overview.md"]]
    assert out["prevented"] and out["returnValue"] == ""


def test_foreign_window_project_switch_preserves_writing_and_task_drafts():
    out = run(r"""
await window.ROWorkspace.load(root);
await writeDocument('Keep this unfinished interpretation.');typed(field('任务'),'Keep this task too');
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
await writeDocument('![Result](../figures/%E5%9B%BE%20%281%29.png)\n[Script](../code/model.do)\n[Remote](https://example.com/file.csv)\n[Outside](../../secret.csv)');
await find('保存').click();await find('提交修改').click();
console.log(JSON.stringify({proposals}));
""")
    assert out["proposals"] == [["research/overview.md", "figures/图 (1).png", "code/model.do"]]


def test_task_save_uses_task_annotation_from_the_server():
    out = run(r"""
listed.mode='member';await window.ROWorkspace.load(root);
customPost=(path,body)=>path==='/api/workspace/tasks'?{tasks:body.tasks,tasks_revision:'new',tasks_officialness:{state:'mine',label:'我的改动，尚未提交'}}:undefined;
typed(field('任务'),'Consider another explanation');await find('添加任务').click();
console.log(JSON.stringify({words:byClass('ws-tasks').textContent}));
""")
    assert "我的改动，尚未提交" in out["words"]


def test_the_open_documents_officialness_word_follows_a_proposal_sent_approved_and_synced():
    """The same text can be read again under another word (DESIGN 18.3): 提议中 once 发送提议 is done, 已批准，待同步
    once the PI approved, nothing once the sync made it official. The refresh after 发送提议 (a quiet re-read of the
    open document, same revision) redraws the word beside the kind -- once, not added to -- and only that: the
    writing area is the one on the page, not a new one."""
    out = run(r"""
const MINE={state:'mine',label:'我的改动，尚未提交'}, PROP={state:'proposed',label:'提议中'},
      APPROVED={state:'approved',label:'已批准，待同步'}, OFFICIAL={state:'official'};
const set=m=>{ listed.documents[0].officialness=m; docs['research/overview.md'].officialness=m; listed.tasks_officialness=m; };
listed.mode='member'; set(MINE);
await window.ROWorkspace.load(root);
const area=byClass('ws-writing');
const words=()=>byClass('ws-kind').querySelectorAll('.ws-officialness').map(e=>e.textContent);
const seen=[words()];
for (const m of [PROP, APPROVED, OFFICIAL, MINE]) { set(m); await window.ROWorkspace.load(root,true); seen.push(words()); }
console.log(JSON.stringify({seen, sameArea: byClass('ws-writing')===area, kind: byClass('ws-kind').textContent}));
""")
    assert out["seen"] == [["我的改动，尚未提交"], ["提议中"], ["已批准，待同步"], [], ["我的改动，尚未提交"]]
    assert out["sameArea"] is True and out["kind"].endswith("我的改动，尚未提交")


# -- 项目首页 (DESIGN.md 18.3) ---------------------------------------------------------------------------------


def _note(path: str, title: str, modified: str | None) -> str:
    """A note of the fixture project: the listing entry and the document, as JavaScript statements."""
    return (f"listed.documents.push({{path:'{path}',kind:'note',title:'{title}',excerpt:'',modified:{json.dumps(modified)}}});"
            f"docs['{path}']={{path:'{path}',kind:'note',title:'{title}',content:'# {title}\\n\\nBody',revision:'{title}-0'}};")


def test_notes_and_results_are_listed_by_last_change_and_a_saved_one_goes_to_the_top():
    out = run(_note("research/notes/a.md", "Oldest", "2026-10-01T09:00:00+00:00")
              + _note("research/notes/b.md", "Newest", "2026-10-22T09:00:00+00:00")
              + _note("research/notes/c.md", "Middle", "2026-10-10T09:00:00+00:00")
              + _note("research/notes/d.md", "Unknown time", None)
              + r"""
await window.ROWorkspace.load(root);
const names=()=>root.all().filter(e=>e.classList.contains('ws-document')).map(e=>e.textContent);
const before=names();
await find('Oldest').click();
await writeDocument('# Oldest\n\nEdited');
await find('保存').click();
console.log(JSON.stringify({before,after:names()}));
""")
    assert out["before"] == ["Research question", "Newest", "Middle", "Oldest", "Unknown time"]
    assert out["after"] == ["Research question", "Oldest", "Newest", "Middle", "Unknown time"]


def test_typing_during_a_save_keeps_the_title_the_engine_wrote_into_a_new_document():
    out = run(r"""
await window.ROWorkspace.load(root);
await find('新建笔记').click();
await titleDocument('Discussion'); await writeDocument('First words');
const waiting=deferred(); customPost=(path)=>path==='/api/workspace/document'?waiting.promise:undefined;
const saving=find('保存').click();
const area=byClass('ws-writing'); typed(area,'First words and more'); area.selectionStart=area.selectionEnd=20;
waiting.resolve({path:'research/notes/n1.md',kind:'note',title:'Discussion',content:'# Discussion\n\nFirst words',revision:'r1'});
await saving;
const after=area.value, caret=[area.selectionStart,area.selectionEnd], dirtyAfter=window.ROWorkspace.isDirty();
customPost=(path,body)=>path==='/api/workspace/document'?{path:body.path,kind:body.kind,title:'Discussion',content:body.content,revision:'r2'}:undefined;
await find('保存').click();
console.log(JSON.stringify({after,caret,dirtyAfter,second:posted[posted.length-1][1],dirty:window.ROWorkspace.isDirty()}));
""")
    assert out["after"] == "# Discussion\n\nFirst words and more"
    assert out["caret"] == [34, 34]  # the caret stays after the words it was after
    assert out["dirtyAfter"] and not out["dirty"]
    assert out["second"]["content"] == "# Discussion\n\nFirst words and more"
    assert out["second"]["title"] == "Discussion" and out["second"]["revision"] == "r1"


def test_typing_during_a_save_keeps_a_changed_title_in_place_of_the_old_heading():
    out = run(_note("research/notes/n2.md", "Old", "2026-10-20T10:00:00+00:00") + r"""
await window.ROWorkspace.load(root);
await find('Old').click();
await titleDocument('New');
const waiting=deferred(); customPost=(path)=>path==='/api/workspace/document'?waiting.promise:undefined;
const saving=find('保存').click();
await writeDocument('# Old\n\nBody, then more');
waiting.resolve({path:'research/notes/n2.md',kind:'note',title:'New',content:'# New\n\nBody',revision:'n2-1'});
await saving;
const after=byClass('ws-writing').value;
customPost=(path,body)=>path==='/api/workspace/document'?{path:body.path,kind:body.kind,title:body.title,content:body.content,revision:'n2-2'}:undefined;
await find('保存').click();
console.log(JSON.stringify({after,second:posted[posted.length-1][1],title:byClass('ws-title-input').value}));
""")
    assert out["after"] == "# New\n\nBody, then more"
    assert out["second"]["content"] == "# New\n\nBody, then more" and out["second"]["title"] == "New"
    assert out["title"] == "New"


def test_typing_during_a_save_leaves_the_text_alone_when_no_heading_was_written_or_the_input_method_is_composing():
    plain = run(r"""
await window.ROWorkspace.load(root);
const waiting=deferred(); customPost=(path)=>path==='/api/workspace/document'?waiting.promise:undefined;
await writeDocument('First paragraph');
const saving=find('保存').click();
await writeDocument('First paragraph\nMore');
waiting.resolve({path:'research/overview.md',kind:'overview',title:'Research question',content:'First paragraph',revision:'doc-1'});
await saving;
console.log(JSON.stringify({after:byClass('ws-writing').value}));
""")
    assert plain["after"] == "First paragraph\nMore"
    composing = run(r"""
await window.ROWorkspace.load(root);
await find('新建笔记').click();
await titleDocument('Discussion'); await writeDocument('Words');
const waiting=deferred(); customPost=(path)=>path==='/api/workspace/document'?waiting.promise:undefined;
const saving=find('保存').click();
const area=byClass('ws-writing'); await area.emit('compositionstart'); typed(area,'Words ni');
waiting.resolve({path:'research/notes/n1.md',kind:'note',title:'Discussion',content:'# Discussion\n\nWords',revision:'r1'});
await saving;
const during=area.value;
typed(area,'Words 你'); await area.emit('compositionend'); await sleepTick(); await sleepTick();
console.log(JSON.stringify({during,after:area.value,draft:window.ROWorkspace.isDirty()}));
""")
    assert composing["during"] == "Words ni"  # the input method's text is not touched while it composes
    assert composing["after"] == "# Discussion\n\nWords 你"


def test_a_new_overview_saved_with_the_title_it_was_shown_with_gets_no_heading():
    out = run(r"""
listed.documents=[];
await window.ROWorkspace.load(root);
await writeDocument('Why does this happen?');
await find('预览').click();
const preview=byClass('ws-preview').textContent;
await find('编辑',byClass('ws-editor-host')).click();
await find('保存').click();
console.log(JSON.stringify({preview,sent:posted[0][1]}));
""")
    assert out["preview"] == "Why does this happen?"
    assert out["sent"]["title"] == "研究问题与计划" and out["sent"]["content"] == "Why does this happen?"


def test_more_than_the_listed_files_says_so_where_files_are_chosen_and_a_link_beyond_them_is_still_known():
    out = run(r"""
listed.files_truncated=true; listed.files_limit=500;
await window.ROWorkspace.load(root);
const sentence=byClass('ws-files-cut').textContent;
customGet=path=>path.startsWith('/api/workspace/exists?')?{exists:['data/beyond.csv']}:undefined;
await writeDocument('[Beyond](../data/beyond.csv)\n[Absent](../data/absent.csv)\n[Listed](../code/model.do)');
await find('预览').click(); await sleepTick(); await sleepTick();
const preview=byClass('ws-preview'), links=preview.all().filter(e=>e.tagName==='A').map(e=>e.textContent);
const asked=gets.filter(p=>p.startsWith('/api/workspace/exists?'));
await find('编辑',byClass('ws-editor-host')).click(); await find('预览').click(); await sleepTick();
console.log(JSON.stringify({sentence,links,words:preview.textContent,asked,askedAgain:gets.filter(p=>p.startsWith('/api/workspace/exists?')).length}));
""")
    assert out["sentence"] == "项目里的文件多于 500 个，这里只列出前 500 个；其余的可以在正文里手写链接。"
    assert out["links"] == ["Beyond", "Listed"]
    assert "[Absent](../data/absent.csv)" in out["words"]
    assert out["asked"] == ["/api/workspace/exists?project=%2Fprojects%2Fstudy&path=data%2Fbeyond.csv&path=data%2Fabsent.csv"]
    assert out["askedAgain"] == 1  # the answers are remembered, the engine is not asked again


def test_a_complete_file_list_says_nothing_and_asks_nothing():
    out = run(r"""
await window.ROWorkspace.load(root);
await writeDocument('[Beyond](../data/beyond.csv)');
await find('预览').click(); await sleepTick();
console.log(JSON.stringify({sentence:!!byClass('ws-files-cut'),gets:gets.filter(p=>p.includes('exists'))}));
""")
    assert out == {"sentence": False, "gets": []}


def test_a_members_submission_takes_the_files_beyond_the_listed_ones_that_exist():
    out = run(r"""
listed.mode='member'; listed.files_truncated=true; listed.files_limit=500;
await window.ROWorkspace.load(root);
const proposals=[];window.ROWorkspace.onPropose=files=>proposals.push(files);
customGet=path=>path.startsWith('/api/workspace/exists?')?{exists:['data/beyond.csv']}:undefined;
await writeDocument('[Beyond](../data/beyond.csv)\n[Absent](../data/absent.csv)\n[Listed](../code/model.do)');
await find('保存').click(); await find('提交修改').click();
console.log(JSON.stringify({proposals}));
""")
    assert out["proposals"] == [["research/overview.md", "data/beyond.csv", "code/model.do"]]  # in the order the document names them


def test_a_members_submission_of_tasks_takes_the_result_records_the_changed_tasks_link():
    out = run(r"""
listed.mode='member'; listed.changed_task_reports=['research/results/'+'b'.repeat(32)+'.md'];
await window.ROWorkspace.load(root);
const proposals=[];window.ROWorkspace.onPropose=files=>proposals.push(files);
await find('提交修改',byClass('ws-tasks')).click();
customPost=(path,body)=>path==='/api/workspace/tasks'?{tasks:body.tasks,tasks_revision:'new',changed_task_reports:[]}:undefined;
typed(field('任务'),'Another task'); await find('添加任务').click();
await find('提交修改',byClass('ws-tasks')).click();
console.log(JSON.stringify({proposals}));
""")
    assert out["proposals"] == [["research/tasks.json", "research/results/" + "b" * 32 + ".md"], ["research/tasks.json"]]


def test_a_mac_that_is_not_a_member_says_so_and_a_project_opened_read_only_keeps_its_own_sentence():
    out = run(r"""
listed.mode='none'; listed.read_only=true; listed.can_write=false;
await window.ROWorkspace.load(root);
const none=byClass('ws-notices').textContent;
listed.mode='single'; await window.ROWorkspace.load(root);
const readOnly=byClass('ws-notices').textContent;
listed.read_only=false; listed.can_write=true; await window.ROWorkspace.load(root);
console.log(JSON.stringify({none,readOnly,gone:byClass('ws-notices').textContent}));
""")
    assert out["none"] == "这台 Mac 不是这个项目的成员，只能查看"
    assert out["readOnly"] == "项目以只读方式打开。可以阅读，暂时不能保存。"
    assert out["gone"] == ""


def test_an_unreadable_project_identity_is_said_on_the_page_that_still_shows_and_the_line_goes_when_it_reads_again():
    out = run(r"""
listed.read_only=true; listed.can_write=false;
listed.identity_error={code:'identity',message:'项目的身份文件暂时读不出来'};
await window.ROWorkspace.load(root);
const said=byClass('ws-notices').textContent, shown=root.textContent.includes('Research question');
listed.identity_error=null; listed.read_only=false; listed.can_write=true;
await window.ROWorkspace.load(root);
console.log(JSON.stringify({said,shown,after:byClass('ws-notices').textContent}));
""")
    assert "项目的身份文件暂时读不出来" in out["said"] and out["shown"] and out["after"] == ""


def test_a_pis_notes_and_result_records_say_they_go_to_the_members_with_the_next_publish():
    out = run(_note("research/notes/a.md", "Idea", "2026-10-20T10:00:00+00:00") + r"""
const sentence='笔记和结果记录会随下一次发布交给成员。';
const said=()=>byClass('ws-document-footer').textContent.includes(sentence);
listed.mode='pi'; await window.ROWorkspace.load(root);
const onOverview=said();
await find('Idea').click(); const onNote=said();
await find('记录结果').click(); const onNewResult=said();
listed.mode='member'; window.ROWorkspace.reset(); await window.ROWorkspace.load(root); await find('Idea').click(); const member=said();
listed.mode='single'; window.ROWorkspace.reset(); await window.ROWorkspace.load(root); await find('Idea').click(); const single=said();
console.log(JSON.stringify({onOverview,onNote,onNewResult,member,single}));
""")
    assert out == {"onOverview": False, "onNote": True, "onNewResult": True, "member": False, "single": False}


def test_the_new_sentences_of_the_project_home_read_in_english_with_no_chinese():
    out = run(_note("research/notes/a.md", "Idea", "2026-10-20T10:00:00+00:00") + r"""
listed.mode='none'; listed.read_only=true; listed.can_write=false; listed.files_truncated=true; listed.files_limit=500;
await window.ROWorkspace.load(root);
const none=byClass('ws-notices').textContent, cut=byClass('ws-files-cut').textContent;
listed.mode='pi'; listed.read_only=false; listed.can_write=true; window.ROWorkspace.reset(); await window.ROWorkspace.load(root); await find('Idea').click();
console.log(JSON.stringify({none,cut,pi:byClass('ws-document-footer').textContent}));
""", lang="en")
    assert out["none"] == "This Mac is not a member of this project; you can only read it"
    assert out["cut"] == "The project has more than 500 files; only the first 500 are listed here. Others can be linked by hand in the text."
    assert "Notes and result records go to the members with the next publish." in out["pi"]
    assert not re.search(r"[㐀-鿿]", out["none"] + out["cut"] + out["pi"])


# -- AI agents carry out tasks (DESIGN.md Section 17) --------------------------------------------------------

REPORT = "research/results/" + "b" * 32 + ".md"


def test_owner_control_offers_me_the_team_this_macs_agents_and_a_typed_name():
    out = run(r"""
listed.mode='pi';meInfo={state:'present',me:{id:'m-lin',name:'Lin Ying',email:'l@x.org'}};
shareInfo={role:'pi',me:{id:'m-lin'},team:[{id:'m-lin',name:'Lin Ying',role:'pi'},{id:'m-wang',name:'Wang Wei',role:'member'}]};
agentsInfo=agentFixture();
await window.ROWorkspace.load(root);await sleepTick();
const options=optionsOf(field('负责人'));
await pickOwner('Claude Code · sonnet');
typed(field('任务'),'Fit the model');await find('添加任务').click();
const asAgent=clone(listed.tasks);
await pickOwner('其他人…');const hasName=!!field('姓名');
typed(field('任务'),'Read papers');typed(field('姓名'),'Chen');await find('添加任务').click();
console.log(JSON.stringify({options,asAgent,hasName,tasks:listed.tasks,gets,nameGoneAfterSave:!root.all().some(e=>e.tagName==='LABEL'&&e.children[0].textContent==='姓名')}));
""")
    assert [text for text, _ in out["options"]] == ["不指定", "Lin Ying（我）", "Wang Wei", "其他人…", "Claude Code · sonnet"]
    assert dict(out["options"])["Claude Code · sonnet"] == "agent:claude-code:sonnet"
    assert dict(out["options"])["Lin Ying（我）"] == "Lin Ying"
    assert out["asAgent"][0]["owner"] == "agent:claude-code:sonnet"
    assert out["hasName"] and out["nameGoneAfterSave"]
    assert [t["owner"] for t in out["tasks"]] == ["agent:claude-code:sonnet", "Chen"]
    assert "/api/share" in out["gets"]


def test_owner_control_works_without_a_team_or_any_agent_information():
    out = run(r"""
await window.ROWorkspace.load(root);await sleepTick();
const options=optionsOf(field('负责人'));
typed(field('任务'),'Read papers');await find('添加任务').click();
console.log(JSON.stringify({options,tasks:listed.tasks,gets,agentRow:!!byClass('ws-agent')}));
""")
    assert [text for text, _ in out["options"]] == ["不指定", "其他人…"]
    assert "/api/share" not in out["gets"]
    assert out["tasks"][0]["owner"] == "" and not out["agentRow"]


def test_without_a_configured_agent_the_owner_control_leads_to_the_settings():
    out = run(r"""
agentsInfo=agentFixture();agentsInfo.agents=[];
await window.ROWorkspace.load(root);await sleepTick();
const options=optionsOf(field('负责人'));
const closed=byClass('ws-agent-settings').classList.contains('hidden');
await pickOwner('设置 AI 执行者…');
console.log(JSON.stringify({options,closed,open:!byClass('ws-agent-settings').classList.contains('hidden'),owner:field('负责人').value,words:byClass('ws-agent-settings').textContent}));
""")
    assert [text for text, _ in out["options"]][-1] == "设置 AI 执行者…"
    assert out["closed"] and out["open"] and out["owner"] == ""
    assert "Claude Code" in out["words"]


def test_owner_control_keeps_a_typed_name_and_names_an_agent_this_mac_lacks():
    out = run(r"""
agentsInfo=agentFixture();
listed.tasks=[{id:'a',text:'By hand',owner:'Lin',due:'',done:false},agentTask({id:'b',owner:'agent:opencode:lmstudio/qwen3-coder'})];
await window.ROWorkspace.load(root);await sleepTick();
const rows=()=>root.all().filter(e=>e.classList.contains('ws-task'));
await find('编辑',rows()[0]).click();
const typedName={options:optionsOf(field('负责人')),value:field('负责人').value};
await find('编辑',rows()[1]).click();
const unknown={options:optionsOf(field('负责人')),value:field('负责人').value};
console.log(JSON.stringify({typedName,unknown,meta:rows().map(r=>byClass('ws-task-meta',r).textContent)}));
""")
    assert ["Lin", "Lin"] in out["typedName"]["options"] and out["typedName"]["value"] == "Lin"
    assert ["OpenCode · lmstudio/qwen3-coder", "agent:opencode:lmstudio/qwen3-coder"] in out["unknown"]["options"]
    assert out["unknown"]["value"] == "agent:opencode:lmstudio/qwen3-coder"
    assert out["meta"] == ["Lin", "OpenCode · lmstudio/qwen3-coder"]
    assert not any("agent:" in m for m in out["meta"])


STATES = [
    # (task fields, run fields or None, label, tone, buttons)
    ({}, None, None, None, ["运行"]),
    ({}, {"state": "preparing", "can_stop": True}, "准备中", "ochre", ["停止", "运行记录"]),
    ({}, {"state": "running", "can_stop": True}, "运行中", "ochre", ["停止", "运行记录"]),
    ({}, {"state": "needs_input", "can_reply": True, "question": "Which sample?"}, "等你回答", "clay", ["运行记录", "回答"]),
    ({"done": True, "report": REPORT}, {"state": "done", "can_reply": True, "can_revert": True, "changes": 3, "report": REPORT},
     "已完成", "olive", ["运行记录", "打回", "撤回本次运行", "打开结果"]),
    ({}, {"state": "ended", "can_reply": True}, "未报告", "gray", ["运行", "运行记录", "打回"]),
    ({}, {"state": "failed", "can_reply": True, "error": {"code": "timeout", "detail": "past the limit"}}, "失败", "clay",
     ["运行", "运行记录", "打回"]),
    ({}, {"state": "stopped", "can_reply": True}, "已停止", "gray", ["运行", "运行记录", "打回"]),
    ({}, {"state": "interrupted", "can_reply": True, "can_revert": True, "changes": 1}, "已中断", "gray",
     ["运行", "运行记录", "打回", "撤回本次运行"]),
    ({}, {"state": "done", "reverted": True}, "已撤回本次运行", "gray", ["运行", "运行记录"]),
    # 18.1 item 4: a run that went out of bounds never reads 已完成 -- the task keeps the mark the agent gave it
    ({"done": True, "report": REPORT},
     {"state": "out_of_bounds", "outcome": "done", "can_reply": True, "can_revert": True, "changes": 3, "violations": 1,
      "report": REPORT}, "越过了限制", "clay oob", ["运行记录", "打回", "撤回本次运行", "打开结果"]),
    ({}, {"state": "out_of_bounds", "outcome": "needs_input", "can_reply": True, "can_revert": True, "changes": 1,
          "violations": 1, "question": "Which sample?"}, "越过了限制", "clay oob", ["运行记录", "回答", "撤回本次运行"]),
    ({}, {"state": "out_of_bounds", "outcome": "failed", "can_reply": True, "can_revert": True, "changes": 1,
          "violations": 1, "error": {"code": "timeout", "detail": "past the limit"}}, "越过了限制", "clay oob",
     ["运行", "运行记录", "打回", "撤回本次运行"]),
    ({}, {"state": "out_of_bounds", "outcome": "stopped", "reverted": True, "violations": 1}, "已撤回本次运行", "gray",
     ["运行", "运行记录"]),
]


def test_agent_task_row_shows_each_state_in_words_with_the_buttons_it_allows():
    out = run(r"""
const results=[];
for(const c of CASES){
  window.ROWorkspace.reset();
  listed.tasks=[agentTask(c[0])];setRuns(c[1]?[mkRun(c[1])]:[]);agentsInfo=agentFixture();
  await window.ROWorkspace.load(root);await sleepTick();
  const box=byClass('ws-agent'),label=byClass('status-label',box);
  results.push({label:label?label.textContent:null,tone:label?label.className:null,buttons:buttonsIn(box),words:box.textContent});
}
console.log(JSON.stringify(results));
""".replace("CASES", json.dumps(STATES)))
    for (_, _, label, tone, buttons), got in zip(STATES, out):
        assert got["label"] == label, got
        assert got["tone"] == (None if tone is None else f"status-label tone-{tone}"), got
        assert got["buttons"] == buttons, got
    assert "Which sample?" in out[3]["words"]
    assert "改动了 3 个文件" in out[4]["words"]
    assert "运行超过了时间限制，已被停止。" in out[6]["words"] and "详情" in out[6]["words"]
    oob_done, oob_asked, oob_failed = out[10], out[11], out[12]
    assert "已完成" not in oob_done["words"] and "有 1 处越界改动，详见运行记录。" in oob_done["words"]
    assert "Which sample?" in oob_asked["words"] and "等你回答" not in oob_asked["words"]
    assert "运行超过了时间限制，已被停止。" in oob_failed["words"]


def test_a_started_run_posts_project_and_task_and_asks_once_per_agent_where_content_goes():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
await window.ROWorkspace.load(root);await sleepTick();
answers.push(false);await find('运行',byClass('ws-agent')).click();
const declined={posted:clone(posted),questions:[...confirmQuestions]};
await find('运行',byClass('ws-agent')).click();
const started={posted:clone(posted),buttons:buttonsIn(byClass('ws-agent')),label:byClass('status-label').textContent,waiting:waiting()};
await find('停止',byClass('ws-agent')).click();
await find('运行',byClass('ws-agent')).click();
console.log(JSON.stringify({declined,started,questions:confirmQuestions,posts:posted.map(p=>p[0])}));
""")
    assert out["declined"]["posted"] == []
    first = out["questions"][0]
    assert "Anthropic" in first and "直接修改项目里的文件" in first
    assert len(out["questions"]) == 2 and out["questions"][1] == first  # declined once, then accepted
    assert out["started"]["posted"] == [["/api/agents/runs", {"project": "/projects/study", "task": "t1"}]]
    assert out["started"]["buttons"] == ["停止", "运行记录"] and out["started"]["label"] == "准备中"
    assert out["started"]["waiting"] == 1
    assert out["posts"] == ["/api/agents/runs", "/api/agents/runs/r-20261021T090000Z-aaaaaa/stop".replace("090000Z-aaaaaa", "091000Z-bbbbbb"), "/api/agents/runs"]


def test_first_run_of_an_opencode_agent_names_the_configured_provider():
    out = run(r"""
agentsInfo=agentFixture();agentsInfo.agents.push({runner:'opencode',model:'lmstudio/qwen3-coder',owner:'agent:opencode:lmstudio/qwen3-coder',label:'OpenCode · lmstudio/qwen3-coder',available:true});
listed.tasks=[agentTask({owner:'agent:opencode:lmstudio/qwen3-coder'})];
await window.ROWorkspace.load(root);await sleepTick();
await find('运行',byClass('ws-agent')).click();
console.log(JSON.stringify({questions:confirmQuestions,posted}));
""")
    assert "OpenCode 里配置的模型服务" in out["questions"][0] and "Anthropic" not in out["questions"][0]
    assert out["posted"][0][1]["task"] == "t1"


def test_running_without_the_mcp_extra_says_so_and_names_the_install_command():
    out = run(r"""
agentsInfo=agentFixture();agentsInfo.mcp=false;listed.tasks=[agentTask()];
await window.ROWorkspace.load(root);await sleepTick();
await find('运行',byClass('ws-agent')).click();
console.log(JSON.stringify({posted,words:byClass('ws-agent').textContent,commands:root.all().filter(e=>e.classList.contains('ws-install-cmd')).map(e=>e.textContent)}));
""")
    assert out["posted"] == []
    assert "mcp 组件" in out["words"]
    assert out["commands"] == ['uv tool install --force --with "mcp<2" git+https://github.com/LinyingL/research-os',
                               'python -m pip install -e ".[mcp]"']
    assert not any("researchos[mcp]" in c for c in out["commands"])


def test_a_refused_start_shows_the_engines_sentence_and_leaves_the_task_runnable():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
await window.ROWorkspace.load(root);await sleepTick();
customPost=()=>Promise.reject(Object.assign(Error('busy'),{state:'agent_busy',engineMessage:'这个项目里还有一次 AI 执行没有结束'}));
await find('运行',byClass('ws-agent')).click();
console.log(JSON.stringify({words:byClass('ws-agent').textContent,buttons:buttonsIn(byClass('ws-agent')),label:!!byClass('status-label')}));
""")
    assert "没能开始这次运行。 这个项目里还有一次 AI 执行没有结束" in out["words"]
    assert out["buttons"] == ["运行"] and not out["label"]


def test_one_run_at_a_time_disables_the_other_tasks_run_buttons():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask(),agentTask({id:'t2',text:'Another'})];
setRuns([mkRun({task_id:'t2',state:'running',can_stop:true})]);
await window.ROWorkspace.load(root);await sleepTick();
const boxes=root.all().filter(e=>e.classList.contains('ws-agent'));
const go=boxes[0].all().find(e=>e.tagName==='BUTTON'&&e.textContent==='运行');
console.log(JSON.stringify({disabled:go.disabled,title:go.title}));
""")
    assert out["disabled"] and "还有一次运行没有结束" in out["title"]


def test_stop_and_revert_post_to_the_run_and_revert_lists_what_it_will_do_first():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
setRuns([mkRun({state:'running',can_stop:true})]);
await window.ROWorkspace.load(root);await sleepTick();
const gen=generations;
await find('停止',byClass('ws-agent')).click();await sleepTick();
const stopped={label:byClass('status-label').textContent,posted:clone(posted),reloaded:generations>gen};
runsList.runs[0].state='done';Object.assign(runsList.runs[0],{can_revert:true,changes:2,can_reply:true});
await window.ROWorkspace.load(root,true);await sleepTick();
await find('撤回本次运行',byClass('ws-agent')).click();await sleepTick();
const listed_={posted:posted.length,asked:confirmQuestions.length,words:byClass('ws-agent-undo').textContent,buttons:buttonsIn(byClass('ws-agent-undo')),expanded:find('撤回本次运行',byClass('ws-agent')).getAttribute('aria-expanded'),gets:gets.filter(p=>p.endsWith('revert-plan'))};
await find('取消',byClass('ws-agent-undo')).click();
const declined={posted:posted.length,panel:!!byClass('ws-agent-undo'),expanded:find('撤回本次运行',byClass('ws-agent')).getAttribute('aria-expanded')};
await find('撤回本次运行',byClass('ws-agent')).click();await sleepTick();
await find('确认撤回',byClass('ws-agent-undo')).click();await sleepTick();
console.log(JSON.stringify({stopped,listed_,declined,posted,asked:confirmQuestions.length,words:byClass('ws-agent').textContent,label:byClass('status-label').textContent,buttons:buttonsIn(byClass('ws-agent')),panel:!!byClass('ws-agent-undo')}));
""")
    assert out["stopped"]["label"] == "已停止" and out["stopped"]["reloaded"]
    assert out["stopped"]["posted"] == [["/api/agents/runs/r-20261021T090000Z-aaaaaa/stop", {"project": "/projects/study"}]]
    # the button only reads: it lists what the undo would do, asks nothing in a dialog and posts nothing
    listed = out["listed_"]
    assert listed["posted"] == 1 and listed["asked"] == 0 and listed["expanded"] == "true"
    assert listed["gets"] == ["/api/agents/runs/r-20261021T090000Z-aaaaaa/revert-plan"]
    assert "analysis.py" in listed["words"] and "out/new.csv" in listed["words"] and "2026-10-21 09:41" in listed["words"]
    assert listed["buttons"] == ["全部勾选", "全部取消勾选", "确认撤回", "取消"]
    # 取消 closes the list and nothing was posted
    assert out["declined"] == {"posted": 1, "panel": False, "expanded": "false"}
    # 确认撤回 posts the checked files, once
    assert out["posted"][-1] == ["/api/agents/runs/r-20261021T090000Z-aaaaaa/revert",
                                 {"project": "/projects/study", "files": ["analysis.py", "out/new.csv"]}]
    assert out["asked"] == 0 and out["panel"] is False
    assert "撤回了 2 个文件的改动。" in out["words"]
    assert out["label"] == "已撤回本次运行" and "撤回本次运行" not in out["buttons"]


def test_revert_lists_the_files_it_left_alone_with_the_reason():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
setRuns([mkRun({state:'done',can_revert:true,changes:2})]);
customPost=(path)=>path.endsWith('/revert')?{run:mkRun({state:'done',can_revert:true,changes:2}),restored:['a.py'],removed:[],skipped:[{path:'data/new.csv',reason:'changed_since'},{path:'big.bin',reason:'no_copy'}]}:undefined;
await window.ROWorkspace.load(root);await sleepTick();
await find('撤回本次运行',byClass('ws-agent')).click();await sleepTick();
await find('确认撤回',byClass('ws-agent')).click();await sleepTick();
console.log(JSON.stringify({words:byClass('ws-agent-note').textContent}));
""")
    assert "撤回了 1 个文件的改动。" in out["words"]
    assert "这些文件没有动：" in out["words"]
    assert "data/new.csv 运行之后又改动过" in out["words"] and "big.bin 没有运行前的副本" in out["words"]


def test_answer_and_send_back_post_the_persons_words_to_the_run():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
setRuns([mkRun({state:'needs_input',can_reply:true,question:'Which sample?'})]);
await window.ROWorkspace.load(root);await sleepTick();
await find('回答',byClass('ws-agent')).click();
const area=()=>byClass('form-textarea');
await find('发送',byClass('ws-agent')).click();
const empty={posted:posted.length,words:byClass('ws-agent').textContent};
typed(area(),'Use sample B');
await find('发送',byClass('ws-agent')).click();await sleepTick();
const answered={posted:clone(posted),label:byClass('status-label').textContent,form:!!area()};
// the new run finishes; the task is done, so the person can send it back
runsList.runs.forEach(r=>{ if(r.state==='running') Object.assign(r,{state:'done',can_stop:false,can_reply:true}); });
listed.tasks=[agentTask({done:true,report:REPORT_PATH})];runsList.active=null;
await window.ROWorkspace.load(root,true);await sleepTick();
await find('打回',byClass('ws-agent')).click();typed(area(),'Redo it with robust errors');
await find('发送',byClass('ws-agent')).click();await sleepTick();
console.log(JSON.stringify({empty,answered,posted}));
""".replace("REPORT_PATH", json.dumps(REPORT)))
    assert out["empty"]["posted"] == 0 and "请先写下内容。" in out["empty"]["words"]
    first = out["answered"]["posted"][0]
    assert first == ["/api/agents/runs/r-20261021T090000Z-aaaaaa/reply", {"project": "/projects/study", "message": "Use sample B"}]
    assert out["answered"]["label"] == "运行中" and not out["answered"]["form"]
    last = out["posted"][-1]
    assert last[0].endswith("/reply") and last[1] == {"project": "/projects/study", "message": "Redo it with robust errors"}
    assert last[0] != first[0]


def test_open_result_opens_the_report_the_way_the_rail_opens_a_document():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask({done:true,report:REPORT_PATH})];
docs[REPORT_PATH]={path:REPORT_PATH,kind:'result',title:'Fit results',content:'# Fit results\nIt worked.',revision:'r-1'};
listed.documents.push({path:REPORT_PATH,kind:'result',title:'Fit results',excerpt:''});
setRuns([mkRun({state:'done',can_reply:true,report:REPORT_PATH})]);
await window.ROWorkspace.load(root);await sleepTick();
await find('打开结果',byClass('ws-agent')).click();
console.log(JSON.stringify({title:byClass('ws-title-input').value,text:byClass('ws-writing').value,current:root.all().filter(e=>e.getAttribute('aria-current')==='page').map(e=>e.textContent)}));
""".replace("REPORT_PATH", json.dumps(REPORT)))
    assert out["title"] == "Fit results" and "It worked." in out["text"]
    assert out["current"] == ["Fit results"]


def test_task_edits_keep_the_report_the_agent_linked():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask({done:true,report:REPORT_PATH})];
await window.ROWorkspace.load(root);await sleepTick();
await find('编辑',byClass('ws-tasks')).click();typed(field('任务'),'Run the model again');await pickOwner('Claude Code · sonnet');
await find('保存任务').click();
console.log(JSON.stringify({tasks:listed.tasks}));
""".replace("REPORT_PATH", json.dumps(REPORT)))
    assert out["tasks"] == [{"id": "t1", "text": "Run the model again", "owner": "agent:claude-code:sonnet", "due": "", "done": True, "report": REPORT}]


def test_a_running_task_cannot_be_edited_removed_or_ticked_off():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];setRuns([mkRun({state:'running',can_stop:true})]);
await window.ROWorkspace.load(root);await sleepTick();
const row=byClass('ws-task');
console.log(JSON.stringify({edit:find('编辑',row).disabled,remove:find('移除',row).disabled,check:row.children[0].disabled}));
""")
    assert out == {"edit": True, "remove": True, "check": True}


def test_run_log_shows_out_of_bounds_changes_first_then_changes_steps_cost_and_instruction():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask({done:true,report:REPORT_PATH})];
const run=mkRun({state:'done',can_reply:true,can_revert:true,violations:1,changes:3,cost_usd:0.0421});
setRuns([run]);
runLogs[run.id]=Object.assign(clone(run),{steps:[{kind:'start',text:'model sonnet',tool:null},{kind:'say',text:'I will fit it.',tool:null},
  {kind:'tool',text:'Bash: python fit.py',tool:'Bash'},{kind:'refused',text:'git push is not allowed',tool:null},{kind:'end',text:'Done',tool:null}],
  steps_total:12,changes:{created:['out/fit.csv'],modified:['code/fit.py'],deleted:['old.txt'],by_person:[]},
  violations:[{path:'data/raw.csv',reason:'data_changed'},{path:'.git',reason:'git_changed'}],instruction:'Do the task now.',stderr_tail:'warning: slow',error:null,refusals:[]});
await window.ROWorkspace.load(root);await sleepTick();
const before={oob:byClass('ws-agent-oob').textContent,log:!!byClass('ws-run-details')};
await find('运行记录',byClass('ws-agent')).click();await sleepTick();
const d=byClass('ws-run-details'),words=d.textContent;
const kinds=d.all().filter(e=>e.classList.contains('ws-step-kind')).map(e=>e.textContent);
const mono=d.all().filter(e=>e.classList.contains('mono')).map(e=>e.textContent);
const more=d.all().filter(e=>e.tagName==='DETAILS').map(e=>[e.children[0].textContent,e.children[1].textContent]);
const headings=d.all().filter(e=>e.classList.contains('ws-run-heading')).map(e=>e.textContent);
const oobGroup=byClass('ws-oob',d).textContent;
await find('运行记录',byClass('ws-agent')).click();
console.log(JSON.stringify({before,words,kinds,mono,more,headings,oobGroup,closed:!byClass('ws-run-details'),gets:gets.filter(g=>g.startsWith('/api/agents/runs/'))}));
""".replace("REPORT_PATH", json.dumps(REPORT)))
    assert out["before"] == {"oob": "有 1 处越界改动，详见运行记录。", "log": False}
    assert out["headings"] == ["越界改动", "新建的文件", "修改的文件", "删除的文件", "经过"]
    assert "data/raw.csv" in out["oobGroup"] and "改动了运行前就有的数据文件" in out["oobGroup"]
    assert ".git" in out["oobGroup"] and "改变了 git 的状态" in out["oobGroup"]
    for text in ("out/fit.csv", "code/fit.py", "old.txt", "I will fit it."):
        assert text in out["words"]
    assert out["kinds"] == ["开始", "它说", "操作", "被拒绝", "结束"]
    # a refusal without a code reads as a generic sentence; the program's own words are its detail (18.1 item 6)
    assert out["mono"] == ["Bash: python fit.py"] and "这一步被拒绝了。" in out["words"]
    assert "只显示最近 5 步，共 12 步。" in out["words"] and "费用约 0.042 美元。" in out["words"]
    assert out["more"] == [["详情", "git push is not allowed"], ["程序的错误输出", "warning: slow"],
                           ["发给它的指示", "Do the task now."]]
    assert out["closed"] and len(out["gets"]) == 1


def test_a_failed_run_says_why_in_words_with_the_raw_detail_behind_details():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
setRuns([mkRun({state:'failed',can_reply:true,error:{code:'exit',detail:'exit code 2'}})]);
await window.ROWorkspace.load(root);await sleepTick();
const more=byClass('ws-agent-more');
console.log(JSON.stringify({words:byClass('ws-agent-error').textContent,summary:more.children[0].textContent,detail:more.children[1].textContent}));
""")
    assert out["words"].startswith("程序异常退出。") and out["summary"] == "详情" and out["detail"] == "exit code 2"


def test_the_two_disclosures_of_the_project_home_draw_the_quiet_chevron_not_the_browsers_triangle():
    """DESIGN 12.13 (18.2 text rules): 引用文件或尝试 and the 详情 behind a failed run are <details> whose summary
    draws the chevron the node page draws -- class res-draft-summary, a res-chevron before the words -- in both
    languages, and workspace.css turns it when the disclosure is open (the browser's triangle is hidden by the
    class)."""
    script = r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
setRuns([mkRun({state:'failed',can_reply:true,error:{code:'exit',detail:'exit code 2'}})]);
await window.ROWorkspace.load(root);await sleepTick();await editDocument();
const rows=root.all().filter(e=>e.tagName==='DETAILS'&&(e.classList.contains('ws-attachments')||e.classList.contains('ws-agent-more'))).map(d=>{
  const s=d.children[0];
  return {box:d.className,tag:s.tagName,cls:s.className,chevronFirst:s.children[0].classList.contains('res-chevron'),words:s.textContent};});
console.log(JSON.stringify({rows}));
"""
    for lang, words in (("zh", ["引用文件或尝试", "详情"]), ("en", ["Reference a file or attempt", "Details"])):
        rows = run(script, lang=lang)["rows"]
        assert [r["box"] for r in rows] == ["ws-attachments", "ws-agent-more"]
        assert all(r["tag"] == "SUMMARY" and "res-draft-summary" in r["cls"] and r["chevronFirst"] for r in rows)
        assert [r["words"] for r in rows] == words
    css = (WEBAPP / "workspace.css").read_text(encoding="utf-8")
    assert ".ws-attachments[open] > .res-draft-summary .res-chevron" in css
    assert ".ws-agent-more[open] > .res-draft-summary .res-chevron" in css
    assert "transform: rotate(90deg)" in css


def test_polling_follows_a_run_until_it_ends_then_reloads_the_page():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];setRuns([mkRun({state:'running',can_stop:true})]);
await window.ROWorkspace.load(root);await sleepTick();
const count=p=>gets.filter(g=>g===p).length;
const atStart={waiting:waiting(),runs:count('/api/agents/runs')};
await tick();
const afterTick={waiting:waiting(),runs:count('/api/agents/runs'),workspace:count('/api/workspace')};
const generation=generations;
Object.assign(runsList.runs[0],{state:'done',can_stop:false,can_reply:true});runsList.active=null;
await tick();
console.log(JSON.stringify({atStart,afterTick,after:{waiting:waiting(),runs:count('/api/agents/runs'),workspace:count('/api/workspace'),pulled:generations-generation,label:byClass('status-label').textContent}}));
""")
    assert out["atStart"] == {"waiting": 1, "runs": 1}
    assert out["afterTick"]["waiting"] == 1 and out["afterTick"]["runs"] == 2
    assert out["after"]["workspace"] == out["afterTick"]["workspace"] + 1
    assert out["after"]["pulled"] == 1 and out["after"]["waiting"] == 0 and out["after"]["label"] == "已完成"


def test_polling_stops_off_the_workspace_view_and_for_another_project_and_resumes_on_return():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];setRuns([mkRun({state:'running',can_stop:true})]);
await window.ROWorkspace.load(root);await sleepTick();
global.currentView='desk';const n=gets.length;await tick();
const away={requests:gets.length-n,waiting:waiting()};
global.currentView='workspace';window.ROWorkspace.resume();await sleepTick();
const back={requests:gets.length-n,waiting:waiting()};
state.currentPath='/projects/other';const m=gets.length;await tick();
const other={requests:gets.length-m,waiting:waiting()};
window.ROWorkspace.reset();
console.log(JSON.stringify({away,back,other,afterReset:waiting()}));
""")
    assert out["away"] == {"requests": 0, "waiting": 0}
    assert out["back"] == {"requests": 1, "waiting": 1}
    assert out["other"] == {"requests": 0, "waiting": 0}
    assert out["afterReset"] == 0


def test_reset_clears_the_polling_timer():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];setRuns([mkRun({state:'running',can_stop:true})]);
await window.ROWorkspace.load(root);await sleepTick();
const before=waiting();window.ROWorkspace.reset();
console.log(JSON.stringify({before,after:waiting()}));
""")
    assert out == {"before": 1, "after": 0}


def test_polling_keeps_the_open_reply_form_and_its_words():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask(),agentTask({id:'t2',text:'Other task'})];
setRuns([mkRun({task_id:'t2',id:'r-20261021T091000Z-dddddd',state:'running',can_stop:true}),
         mkRun({state:'needs_input',can_reply:true,question:'Which sample?'})]);
await window.ROWorkspace.load(root);await sleepTick();
await find('回答').click();
const area=byClass('form-textarea');typed(area,'Half-written answer');
await tick();
console.log(JSON.stringify({same:byClass('form-textarea')===area,value:byClass('form-textarea').value,waiting:waiting()}));
""")
    assert out["same"] and out["value"] == "Half-written answer" and out["waiting"] == 1


def test_settings_show_where_each_program_is_and_save_a_path_for_one_not_found():
    out = run(r"""
agentsInfo=agentFixture();
await window.ROWorkspace.load(root);await sleepTick();
await find('AI 执行者').click();
const box=byClass('ws-agent-settings'),runners=box.all().filter(e=>e.classList.contains('ws-runner'));
const found=runners[0].textContent,missing=runners[1].textContent;
const hasPathInput=r=>r.all().some(e=>e.tagName==='INPUT');
const inputs=[hasPathInput(runners[0]),hasPathInput(runners[1])];
const pathBox=runners[1].all().find(e=>e.tagName==='INPUT');
await find('保存',runners[1]).click();
const empty=posted.length;
typed(pathBox,'/opt/tools/opencode');await find('保存',runners[1]).click();await sleepTick();
const after=byClass('ws-agent-settings').all().filter(e=>e.classList.contains('ws-runner'))[1].textContent;
console.log(JSON.stringify({found,missing,inputs,empty,posted,after,pressed:find('AI 执行者').getAttribute('aria-pressed')}));
""")
    assert "/usr/local/bin/claude" in out["found"] and "版本 2.1.0" in out["found"]
    assert "Anthropic" in out["found"]
    assert "没有找到这个程序" in out["missing"] and "OpenCode 里配置的模型服务" in out["missing"]
    assert "没有逐步检查" in out["missing"]
    assert out["inputs"] == [False, True] and out["empty"] == 0
    assert out["posted"] == [["/api/settings/agents", {"commands": {"opencode": "/opt/tools/opencode"}}]]
    assert "/opt/tools/opencode" in out["after"] and "版本 0.9.0" in out["after"]
    assert out["pressed"] == "true"


def test_settings_add_and_remove_an_agent_and_set_the_time_limit():
    out = run(r"""
agentsInfo=agentFixture();modelsList=['lmstudio/qwen3-coder','ollama/llama3'];
Object.assign(agentsInfo.runners[1],{path:'/opt/opencode',version:'0.9.0',problem:null,found:true});
await window.ROWorkspace.load(root);await sleepTick();
await find('AI 执行者').click();
const box=()=>byClass('ws-agent-settings'),add=()=>byClass('ws-agent-add',box()).parentNode;
const suggestions=()=>field('模型版本',box()).children.map(o=>o.value).filter(v=>v&&v!=='__custom__');
const claude=suggestions();
await find('添加',box()).click();const empty=posted.length;
const choice=field('模型版本',box());choice.value='claude-opus-4-6';await choice.emit('change');await find('添加',box()).click();await sleepTick();
const afterAdd=optionsOf(field('负责人'));
const runner2=field('程序',box());runner2.value='opencode';await runner2.emit('change');await sleepTick();
const opencode=suggestions();
const choice2=field('模型版本',box());choice2.value='lmstudio/qwen3-coder';await choice2.emit('change');await find('添加',box()).click();await sleepTick();
const removeButtons=()=>box().all().filter(e=>e.tagName==='BUTTON'&&e.textContent==='移除');
await removeButtons()[0].click();await sleepTick();
const minutes=field('每次运行的时间限制（分钟）',box());
typed(minutes,'2');await find('保存',box()).click();const tooLow=posted.length;
typed(field('每次运行的时间限制（分钟）',box()),'90');await find('保存',box()).click();await sleepTick();
console.log(JSON.stringify({claude,empty,afterAdd,opencode,posted:posted.map(p=>p[1]),tooLow,gets:gets.filter(g=>g.includes('models')),list:box().all().filter(e=>e.classList.contains('ws-agent-label')).map(e=>e.textContent)}));
""")
    assert out["claude"] == ["claude-sonnet-5-5", "claude-sonnet-4-6", "claude-opus-5-5", "claude-opus-4-6", "claude-haiku-4-5-20251001"]
    assert out["empty"] == 0
    assert [t for t, _ in out["afterAdd"]][-2:] == ["Claude Code · sonnet", "Claude Code · claude-opus-4-6"]
    assert out["opencode"] == ["lmstudio/qwen3-coder", "ollama/llama3"] and out["gets"] == ["/api/agents/models?runner=opencode"]
    sonnet = {"runner": "claude-code", "model": "sonnet"}
    opus = {"runner": "claude-code", "model": "claude-opus-4-6"}
    qwen = {"runner": "opencode", "model": "lmstudio/qwen3-coder"}
    assert out["posted"] == [{"agents": [sonnet, opus]}, {"agents": [sonnet, opus, qwen]}, {"agents": [opus, qwen]}, {"timeout_minutes": 90}]
    assert out["tooLow"] == 3
    assert out["list"] == ["Claude Code · claude-opus-4-6", "OpenCode · lmstudio/qwen3-coder"]


def test_reasoning_picker_follows_model_and_preserves_saved_settings():
    out = run(r"""
agentsInfo=agentFixture();
customGet=path=>path==='/api/agents/models?runner=codex'?{models:['gpt-test'],reasoning_efforts:{'gpt-test':['medium','high']}}:undefined;
await window.ROWorkspace.load(root);await sleepTick();await find('AI 执行者').click();
const box=()=>byClass('ws-agent-settings'),form=()=>byClass('ws-agent-add',byClass('ws-agent-list'));
const model=()=>field('模型版本',form()),effort=()=>field('推理强度',form());
model().value='claude-sonnet-5-5';await model().emit('change');
const sonnet=optionsOf(effort());effort().value='xhigh';await effort().emit('change');
await find('添加',box()).click();await sleepTick();
let rows=()=>byClass('ws-agent-list').children.filter(e=>e.classList.contains('ws-agent-item'));
const saved=field('推理强度',rows()[1]).value;
field('推理强度',rows()[1]).value='';await field('推理强度',rows()[1]).emit('change');await sleepTick();
const cleared=agentsInfo.agents[1];
model().value='claude-opus-4-6';await model().emit('change');const opus=optionsOf(effort());
const program=()=>field('程序',form());program().value='deepseek-harness';await program().emit('change');
model().value='deepseek-official/deepseek-v4-pro';await model().emit('change');const harness=optionsOf(effort());
effort().value='off';await effort().emit('change');await find('添加',box()).click();await sleepTick();
program().value='codex';await program().emit('change');await sleepTick();
model().value='gpt-test';await model().emit('change');const codex=optionsOf(effort());
effort().value='high';await effort().emit('change');await find('添加',box()).click();await sleepTick();
console.log(JSON.stringify({sonnet,opus,harness,codex,saved,cleared,posted:posted.filter(p=>p[0]==='/api/settings/agents')}));
""")
    assert [v for _, v in out["sonnet"]] == ["", "low", "medium", "high", "xhigh", "max"]
    assert [v for _, v in out["opus"]] == ["", "low", "medium", "high", "max"]
    assert [v for _, v in out["harness"]] == ["", "off", "low", "high", "max"]
    assert [v for _, v in out["codex"]] == ["", "medium", "high"]
    assert out["saved"] == "xhigh" and "reasoning_effort" not in out["cleared"]
    posts = [body["agents"] for _, body in out["posted"]]
    assert posts[0][-1]["reasoning_effort"] == "xhigh"
    assert posts[2][-1]["reasoning_effort"] == "off"
    assert posts[3][-1]["reasoning_effort"] == "high"
    assert posts[3][-2]["reasoning_effort"] == "off"  # other saved profiles retain their choices


@pytest.mark.parametrize("lang,level,words", [("en", "max", "Reasoning effort: Max"),
                                              ("zh", None, "推理强度：默认"),
                                              ("en", "off", "Reasoning effort: Off")])
def test_run_log_shows_recorded_reasoning_setting(lang, level, words):
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
const recorded=mkRun({state:'ended',reasoning_effort:LEVEL});setRuns([recorded]);
runLogs[recorded.id]=Object.assign({},recorded,{steps:[],changes:{},violations:[]});
await window.ROWorkspace.load(root);await sleepTick();
await find(uiLang()==='en'?'Run log':'运行记录',byClass('ws-agent')).click();await sleepTick();
console.log(JSON.stringify({words:byClass('ws-run-details').textContent}));
""".replace("LEVEL", json.dumps(level)), lang=lang)
    assert words in out["words"]


def test_claude_model_versions_are_visible_and_custom_snapshots_are_preserved():
    out = run(r"""
agentsInfo=agentFixture();
await window.ROWorkspace.load(root);await sleepTick();await find('AI 执行者').click();
const box=()=>byClass('ws-agent-settings'),versions=()=>field('模型版本',box());
const offered=optionsOf(versions());
versions().value='claude-sonnet-5-5';await versions().emit('change');
await find('添加',box()).click();await sleepTick();
versions().value='__custom__';await versions().emit('change');
typed(field('完整模型 ID',box()),'claude-sonnet-4-5-20250929');
await find('添加',box()).click();await sleepTick();
console.log(JSON.stringify({offered,posted:posted.filter(p=>p[0]==='/api/settings/agents'),owners:optionsOf(field('负责人'))}));
""")
    assert ["Sonnet 5.5 · claude-sonnet-5-5", "claude-sonnet-5-5"] in out["offered"]
    assert ["Opus 4.6 · claude-opus-4-6", "claude-opus-4-6"] in out["offered"]
    assert out["posted"][0][1]["agents"][-1] == {"runner": "claude-code", "model": "claude-sonnet-5-5"}
    assert out["posted"][1][1]["agents"][-1] == {"runner": "claude-code", "model": "claude-sonnet-4-5-20250929"}
    assert "agent:claude-code:sonnet" in [value for _, value in out["owners"]]


def test_codex_version_selection_custom_id_and_separate_catalogs():
    out = run(r"""
agentsInfo=agentFixture();modelsList=['gpt-6.1-sol','gpt-6-luna'];
await window.ROWorkspace.load(root);await sleepTick();await find('AI 执行者').click();
const box=()=>byClass('ws-agent-settings');
const runner=field('程序',box());runner.value='codex';await runner.emit('change');await sleepTick();
const versions=()=>field('模型版本',box());
const offered=optionsOf(versions());versions().value='gpt-6.1-sol';await versions().emit('change');
await find('添加',box()).click();await sleepTick();
versions().value='__custom__';await versions().emit('change');
const custom=field('完整模型 ID',box());typed(custom,'gpt-5.5');await find('添加',box()).click();await sleepTick();
Object.assign(agentsInfo.runners[1],{found:true,path:'/opt/opencode',problem:null});
modelsList=['openai/gpt-6.1-sol'];
await find('AI 执行者').click();await find('AI 执行者').click();await sleepTick();
const provider=field('程序',box());provider.value='opencode';await provider.emit('change');await sleepTick();
const other=optionsOf(versions());provider.value='codex';await provider.emit('change');await sleepTick();
console.log(JSON.stringify({offered,other,back:optionsOf(versions()),posted:posted.filter(p=>p[0]==='/api/settings/agents'),gets:gets.filter(g=>g.includes('/models?'))}));
""")
    assert [v for _, v in out["offered"]] == ["", "gpt-6.1-sol", "gpt-6-luna", "__custom__"]
    assert out["posted"][0][1]["agents"][-1] == {"runner": "codex", "model": "gpt-6.1-sol"}
    assert out["posted"][1][1]["agents"][-1] == {"runner": "codex", "model": "gpt-5.5"}
    assert "openai/gpt-6.1-sol" in [v for _, v in out["other"]]
    assert out["back"] == out["offered"]
    assert out["gets"] == ["/api/agents/models?runner=codex", "/api/agents/models?runner=opencode"]


def test_harness_model_selection_distinguishes_authentication_and_preserves_full_id():
    out = run(r"""
agentsInfo=agentFixture();
await window.ROWorkspace.load(root);await sleepTick();await find('AI 执行者').click();
const box=()=>byClass('ws-agent-settings');
const runner=field('程序',box());runner.value='deepseek-harness';await runner.emit('change');await sleepTick();
const versions=field('模型版本',box()),offered=optionsOf(versions);
versions.value='deepseek-account/deepseek-flash';await versions.emit('change');
await find('添加',box()).click();await sleepTick();
console.log(JSON.stringify({offered,posted:posted.filter(p=>p[0]==='/api/settings/agents'),gets:gets.filter(g=>g.includes('/models?')),words:box().textContent}));
""")
    assert out["offered"][1] == ["账户 · deepseek-account/deepseek-flash", "deepseek-account/deepseek-flash"]
    assert out["offered"][2] == ["API Key · deepseek-official/deepseek-v4-pro", "deepseek-official/deepseek-v4-pro"]
    assert out["posted"][0][1]["agents"][-1] == {"runner": "deepseek-harness", "model": "deepseek-account/deepseek-flash"}
    assert out["gets"] == []
    assert "DeepSeek Harness" in out["words"]


def test_settings_warn_about_the_missing_mcp_extra_and_a_failed_save_keeps_the_form():
    out = run(r"""
agentsInfo=agentFixture();agentsInfo.mcp=false;
await window.ROWorkspace.load(root);await sleepTick();
await find('AI 执行者').click();
const box=byClass('ws-agent-settings');
const commands=box.all().filter(e=>e.classList.contains('ws-install-cmd')).map(e=>e.textContent);
customPost=()=>Promise.reject(Object.assign(Error('bad'),{state:'agent_invalid',engineMessage:'模型名不对'}));
typed(field('完整模型 ID',box),'bad name!');await find('添加',box).click();await sleepTick();
console.log(JSON.stringify({commands,words:byClass('ws-agent-settings').textContent,model:field('完整模型 ID',byClass('ws-agent-settings')).value}));
""")
    assert len(out["commands"]) == 2 and out["commands"][1] == 'python -m pip install -e ".[mcp]"'
    assert "没能保存设置。 模型名不对" in out["words"] and out["model"] == "bad name!"  # a refusal keeps what was typed


def test_a_failed_agent_listing_says_so_and_can_be_retried():
    out = run(r"""
let fail=true;customGet=path=>path==='/api/agents'&&fail?Promise.reject(Error('boom')):undefined;
agentsInfo=agentFixture();
await window.ROWorkspace.load(root);await sleepTick();
await find('AI 执行者').click();
const failed=byClass('ws-agent-settings').textContent;
fail=false;await find('重试').click();await sleepTick();
console.log(JSON.stringify({failed,words:byClass('ws-agent-settings').textContent}));
""")
    assert "没能读取 AI 执行者的设置。" in out["failed"]
    assert "Claude Code" in out["words"] and "没能读取" not in out["words"]


def test_agent_ui_in_english_has_no_chinese_and_uses_the_glossary_words():
    out = run(r"""
agentsInfo=agentFixture();agentsInfo.mcp=false;
listed.files=listed.files.filter(f=>!/[一-鿿]/.test(f.path));
listed.tasks=[agentTask(),agentTask({id:'t2',text:'Second',done:true,report:REPORT_PATH}),agentTask({id:'t3',text:'Third',owner:'agent:opencode:lmstudio/qwen3-coder'})];
const mk=(id,task,o)=>mkRun(Object.assign({id:id,task_id:task},o));
setRuns([mk('r-20261021T090000Z-aaaaaa','t1',{state:'needs_input',can_reply:true,question:'Which sample?'}),
         mk('r-20261021T090100Z-bbbbbb','t2',{state:'done',can_reply:true,can_revert:true,violations:1,changes:2,cost_usd:1.5,report:REPORT_PATH}),
         mk('r-20261021T090200Z-cccccc','t3',{state:'failed',can_reply:true,error:{code:'timeout',detail:'x'}})],null);
runLogs['r-20261021T090100Z-bbbbbb']=Object.assign(clone(runsList.runs[1]),{steps:[{kind:'start',text:'model sonnet'},{kind:'say',text:'hi'},{kind:'tool',text:'Bash: ls'},{kind:'refused',text:'no'},{kind:'error',text:'bad'},{kind:'end',text:'ok'}],steps_total:9,
  changes:{created:['a'],modified:['b'],deleted:['c']},violations:[{path:'d.csv',reason:'data_deleted'},{path:'x',reason:'weird'}],instruction:'Do it',stderr_tail:'err',refusals:[]});
await window.ROWorkspace.load(root);await sleepTick();
const boxes=()=>root.all().filter(e=>e.classList.contains('ws-agent'));
await find('Answer',boxes()[0]).click();
await find('Run log',boxes()[1]).click();await sleepTick();
const rows=boxes().map(b=>buttonsIn(b));
await find('AI agent').click();
await pickOwner('Someone else…','Owner');
const all=root.textContent+root.all().map(e=>(e.placeholder||'')+(e.getAttribute('aria-label')||'')+(e.title||'')).join(' ');
console.log(JSON.stringify({all,rows}));
""".replace("REPORT_PATH", json.dumps(REPORT)), lang="en")
    text = out["all"]
    assert re.search(r"[㐀-鿿]", text) is None, [c for c in text if "㐀" <= c <= "鿿"][:20]
    for words in ("Waiting for your answer", "Done", "Failed", "Answer", "Send back", "Undo this run", "Run log", "Open the result",
                  "Out-of-bounds change", "AI agent", "Someone else…", "Created files", "Changed files", "Deleted files",
                  "The instruction sent to it", "Steps", "Went outside what is allowed", "The run passed its time limit and was stopped.",
                  "Showing the last 6 of 9 steps.", "There is 1 out-of-bounds change. See the run log.", "Changed 2 files"):
        assert words in text, words
    assert "Claude Code · sonnet" in text and "OpenCode · lmstudio/qwen3-coder" in text
    assert out["rows"][0] == ["Run log", "Answer", "Send", "Cancel"] and out["rows"][1] == ["Run log", "Send back", "Undo this run", "Open the result"]
    assert out["rows"][2] == ["Run", "Run log", "Send back"]


def test_every_chinese_string_in_the_workspace_page_has_an_english_entry():
    from test_v9_language import CJK, _unescape, english_table, js_literals
    table = english_table()
    src = (WEBAPP / "workspace.js").read_text(encoding="utf-8")
    missing = {_unescape(raw) for _, _, raw in js_literals(src) if CJK.search(_unescape(raw)) and _unescape(raw) not in table}
    assert not missing, sorted(missing)


def test_an_opened_log_detail_stays_open_while_the_run_is_followed():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
const run=mkRun({state:'running',can_stop:true});setRuns([run]);
runLogs[run.id]=Object.assign(clone(run),{steps:[{kind:'start',text:'model sonnet'}],steps_total:1,changes:{},violations:[],instruction:'Do the task now.',stderr_tail:'',error:null,refusals:[]});
await window.ROWorkspace.load(root);await sleepTick();
await find('运行记录',byClass('ws-agent')).click();await sleepTick();
const instruction=()=>byClass('ws-run-details').all().find(e=>e.tagName==='DETAILS');
instruction().open=true;await instruction().emit('toggle');
runLogs[run.id].steps.push({kind:'say',text:'Working on it.'});runLogs[run.id].steps_total=2;
await tick();
console.log(JSON.stringify({open:instruction().open,steps:byClass('ws-run-details').all().filter(e=>e.classList.contains('ws-step')).length}));
""")
    assert out == {"open": True, "steps": 2}


def test_a_run_of_a_task_whose_owner_changed_can_still_be_undone():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask({owner:'Lin'})];
setRuns([mkRun({state:'done',can_reply:true,can_revert:true,changes:1})]);
await window.ROWorkspace.load(root);await sleepTick();
console.log(JSON.stringify({buttons:buttonsIn(byClass('ws-agent'))}));
""")
    assert out["buttons"] == ["运行记录", "打回", "撤回本次运行"]



# -- review fixes: the page keeps up with a run without losing what the person is writing ----------------------


def test_the_task_list_takes_the_agents_done_mark_while_the_task_form_holds_text():
    # #29: the run ended and the engine saved the done mark and the report; the page must not offer 运行 again,
    # tick the box or send the old revision just because a new task is being typed.
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];setRuns([mkRun({state:'running',can_stop:true})]);
await window.ROWorkspace.load(root);await sleepTick();
typed(field('任务'),'Next question for the group');
Object.assign(runsList.runs[0],{state:'done',can_stop:false,can_reply:true,changes:1});runsList.active=null;
listed.tasks=[agentTask({done:true,report:'research/results/run.md'})];listed.tasks_revision='tasks-agent';
await tick();
const row=root.all().find(e=>e.classList.contains('ws-task'));
const check=row.all().find(e=>e.tagName==='INPUT'&&e.type==='checkbox');
const after={buttons:buttonsIn(byClass('ws-agent')),checked:check.checked,text:field('任务').value};
await find('添加任务').click();await sleepTick();
const save=posted.filter(p=>p[0]==='/api/workspace/tasks').pop();
console.log(JSON.stringify({after,revision:save&&save[1].revision,tasks:save&&save[1].tasks.map(x=>[x.text,x.done])}));
""")
    assert "运行" not in out["after"]["buttons"] and "打开结果" in out["after"]["buttons"]
    assert out["after"]["checked"] is True and out["after"]["text"] == "Next question for the group"
    assert out["revision"] == "tasks-agent"
    assert out["tasks"] == [["Run the model", True], ["Next question for the group", False]]


def test_the_task_list_is_redrawn_around_a_task_form_that_has_focus():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];setRuns([mkRun({state:'running',can_stop:true})]);
await window.ROWorkspace.load(root);await sleepTick();
const input=field('任务');typed(input,'half a sentence');input.focus();
Object.assign(runsList.runs[0],{state:'done',can_stop:false,can_reply:true});runsList.active=null;
listed.tasks=[agentTask({done:true,report:'research/results/run.md'})];listed.tasks_revision='tasks-agent';
await tick();
console.log(JSON.stringify({same:field('任务')===input,focus:document.activeElement===input,value:input.value,buttons:buttonsIn(byClass('ws-agent'))}));
""")
    assert out["same"] and out["focus"] and out["value"] == "half a sentence"
    assert "打开结果" in out["buttons"] and "运行" not in out["buttons"]


def test_an_edit_in_progress_is_kept_but_not_saved_when_the_task_file_moved():
    out = run(r"""
listed.tasks=[{id:'t1',text:'Plan the survey',owner:'',due:'',done:false}];
await window.ROWorkspace.load(root);await sleepTick();
await find('编辑',byClass('ws-tasks')).click();typed(field('任务'),'Plan the survey, twice');
listed.tasks=[{id:'t1',text:'Plan the survey',owner:'',due:'',done:true}];listed.tasks_revision='tasks-moved';
await window.ROWorkspace.load(root,true);await sleepTick();
const held={text:field('任务').value,disabled:find('保存任务').disabled,status:byClass('ws-status',root.all().find(e=>e.classList.contains('ws-task-form')).parentNode.children.slice(-1)[0]||root)};
await find('保存任务').click();
const posts=posted.filter(p=>p[0]==='/api/workspace/tasks').length;
const words=root.textContent;
await find('重新载入任务').click();await sleepTick();
await find('保存任务').click();await sleepTick();
const last=posted.filter(p=>p[0]==='/api/workspace/tasks').pop();
console.log(JSON.stringify({held:{text:held.text,disabled:held.disabled},posts,words,revision:last[1].revision,saved:last[1].tasks[0]}));
""")
    assert out["held"] == {"text": "Plan the survey, twice", "disabled": True}
    assert out["posts"] == 0 and "任务已在其他地方修改，当前填写已保留。" in out["words"]
    assert out["revision"] == "tasks-moved"
    assert out["saved"]["text"] == "Plan the survey, twice" and out["saved"]["done"] is True


def test_a_reply_form_goes_with_the_button_that_opens_it():
    # #28: 打回 left open, then 运行 / 撤回本次运行 / a run waiting for an answer: no stale 发送 form.
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
setRuns([mkRun({state:'done',can_reply:true,can_revert:true,changes:1})]);
await window.ROWorkspace.load(root);await sleepTick();
const formButtons=()=>buttonsIn(byClass('ws-agent')).filter(b=>b==='发送'||b==='取消');
const textareas=()=>root.all().filter(e=>e.tagName==='TEXTAREA'&&e.classList.contains('form-textarea')).length;
await find('打回').click();typed(byClass('form-textarea'),'redo this');
const open={buttons:formButtons(),areas:textareas(),dirty:window.ROWorkspace.isDirty()};
await find('运行').click();await sleepTick();
const started={label:byClass('status-label').textContent,buttons:buttonsIn(byClass('ws-agent')),areas:textareas(),dirty:window.ROWorkspace.isDirty()};
// the run before comes back as a finished one that can be undone; the form must not return with the old words
window.ROWorkspace.reset();
setRuns([mkRun({state:'done',can_reply:true,can_revert:true,changes:1})]);listed.tasks=[agentTask()];
await window.ROWorkspace.load(root);await sleepTick();
await find('打回').click();typed(byClass('form-textarea'),'redo this');
await find('撤回本次运行').click();await sleepTick();
const listedWith={areas:textareas(),buttons:buttonsIn(byClass('ws-agent-undo'))};
await find('确认撤回').click();await sleepTick();
const undone={label:byClass('status-label').textContent,buttons:buttonsIn(byClass('ws-agent')),areas:textareas()};
// a form for 打回 must not turn into a 要它改什么 form under a run that is waiting for an answer
window.ROWorkspace.reset();
setRuns([mkRun({state:'done',can_reply:true})]);
await window.ROWorkspace.load(root);await sleepTick();
await find('打回').click();
Object.assign(runsList.runs[0],{state:'needs_input',question:'Which?'});
await window.ROWorkspace.load(root,true);await sleepTick();
const waiting_={buttons:buttonsIn(byClass('ws-agent')),areas:textareas(),expanded:find('回答').getAttribute('aria-expanded')};
console.log(JSON.stringify({open,started,listedWith,undone,waiting_}));
""")
    assert out["open"] == {"buttons": ["发送", "取消"], "areas": 1, "dirty": True}
    assert out["started"]["areas"] == 0 and "发送" not in out["started"]["buttons"] and not out["started"]["dirty"]
    assert out["started"]["label"] in ("准备中", "运行中")
    # the list is opened beside the form the person was typing in; the form goes when the undo is done
    assert out["listedWith"]["areas"] == 1 and "确认撤回" in out["listedWith"]["buttons"]
    assert out["undone"]["label"] == "已撤回本次运行" and out["undone"]["areas"] == 0 and "发送" not in out["undone"]["buttons"]
    assert out["waiting_"]["areas"] == 0 and out["waiting_"]["expanded"] == "false"


def test_a_reply_box_keeps_its_node_focus_and_words_while_the_page_refreshes():
    # #30: refreshes must not rebuild a textarea that has focus; unsent words also count as unsaved.
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
setRuns([mkRun({state:'needs_input',can_reply:true,question:'Which sample?'})]);
await window.ROWorkspace.load(root);await sleepTick();
const clean=window.ROWorkspace.isDirty();
await find('回答').click();
const area=byClass('form-textarea');
const focusedOnOpen=document.activeElement===area;
typed(area,'Sample B, because');area.selectionStart=area.selectionEnd=7;
const dirty=window.ROWorkspace.isDirty();
const leave=window.ROWorkspace.mayLeave(),asked=confirmQuestions.length;
runsList.runs[0].question='Which sample, A or B?';listed.tasks=[agentTask({text:'Run the model now'})];listed.tasks_revision='tasks-x';
await window.ROWorkspace.load(root,true);await sleepTick();
console.log(JSON.stringify({clean,focusedOnOpen,dirty,leave,asked,same:byClass('form-textarea')===area,focus:document.activeElement===area,
  value:area.value,caret:area.selectionStart,question:byClass('ws-agent-question').textContent,buttons:buttonsIn(byClass('ws-agent'))}));
""")
    assert out["clean"] is False and out["focusedOnOpen"] is True and out["dirty"] is True and out["asked"] == 1
    assert out["same"] and out["focus"] and out["value"] == "Sample B, because" and out["caret"] == 7
    assert out["question"] == "Which sample, A or B?"
    assert out["buttons"] == ["运行记录", "回答", "发送", "取消"]


def test_a_settings_input_is_not_rebuilt_under_the_typing_when_the_agent_list_arrives():
    out = run(r"""
agentsInfo=agentFixture();
await window.ROWorkspace.load(root);await sleepTick();
const slow=deferred();let slowed=false;
customGet=path=>{ if(path==='/api/agents'&&!slowed){slowed=true;return slow.promise;} };
await find('AI 执行者').click();
const box=()=>byClass('ws-agent-settings'),model=field('完整模型 ID',box());
typed(model,'son');model.focus();
agentsInfo.timeout_minutes=90;
slow.resolve(clone(agentsInfo));await sleepTick();await sleepTick();
const again=field('完整模型 ID',box());
console.log(JSON.stringify({same:again===model,focus:document.activeElement===model,value:model.value,tasksSame:true}));
""")
    assert out["same"] and out["focus"] and out["value"] == "son"


def test_polling_keeps_going_with_back_off_and_says_so_quietly_while_reads_fail():
    # #31
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];setRuns([mkRun({state:'running',can_stop:true})]);
await window.ROWorkspace.load(root);await sleepTick();
let down=true;customGet=path=>{ if(path==='/api/agents/runs'&&down) return Promise.reject(Object.assign(Error('offline'),{})); };
const delays=[],notice=()=>{ const n=root.all().find(e=>e.classList.contains('ws-connection')); return n?n.textContent:''; };
delays.push(timers.filter(t=>!t.cancelled&&!t.fired).map(t=>t.ms)[0]);
for(let i=0;i<7;i+=1){ await tick(); delays.push(timers.filter(t=>!t.cancelled&&!t.fired).map(t=>t.ms)[0]); }
const failing={notice:notice(),label:byClass('status-label').textContent,waiting:waiting()};
down=false;await tick();
const back={notice:notice(),waiting:waiting(),delay:timers.filter(t=>!t.cancelled&&!t.fired).map(t=>t.ms)[0]};
console.log(JSON.stringify({delays,failing,back}));
""")
    assert out["delays"] == [2000, 4000, 8000, 16000, 30000, 30000, 30000, 30000]
    assert out["failing"] == {"notice": "暂时连不上 Research OS，正在重试。", "label": "运行中", "waiting": 1}
    assert out["back"] == {"notice": "", "waiting": 1, "delay": 2000}


def test_a_failed_run_log_read_is_asked_for_again_on_reopen_and_by_the_retry_button():
    # #32
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
const run1=mkRun({state:'done',can_reply:true});setRuns([run1]);
let broken=true;const logPath='/api/agents/runs/'+encodeURIComponent(run1.id);
runLogs[run1.id]=Object.assign(clone(run1),{steps:[{kind:'say',text:'The fit converged.'}],steps_total:1,changes:{created:[],modified:[],deleted:[]},violations:[],refusals:[]});
customGet=path=>{ if(path===logPath&&broken) return Promise.reject(Object.assign(Error('timeout'),{})); };
const reads=()=>gets.filter(g=>g===logPath).length;
await window.ROWorkspace.load(root);await sleepTick();
await find('运行记录').click();await sleepTick();
const failed={reads:reads(),words:byClass('ws-run-details').textContent,retry:buttonsIn(byClass('ws-run-details'))};
broken=false;
await find('运行记录').click();await find('运行记录').click();await sleepTick();
const reopened={reads:reads(),words:byClass('ws-run-details').textContent};
// the button, with the read failing again once more
broken=true;window.ROWorkspace.reset();setRuns([run1]);listed.tasks=[agentTask()];
await window.ROWorkspace.load(root);await sleepTick();
await find('运行记录').click();await sleepTick();
broken=false;const before=reads();
await find('重试',byClass('ws-run-details')).click();await sleepTick();
console.log(JSON.stringify({failed,reopened,button:{reads:reads()-before,words:byClass('ws-run-details').textContent}}));
""")
    assert out["failed"]["reads"] == 1 and "没能读取运行记录。" in out["failed"]["words"] and out["failed"]["retry"] == ["重试"]
    assert out["reopened"]["reads"] == 2 and "The fit converged." in out["reopened"]["words"]
    assert out["button"]["reads"] == 1 and "The fit converged." in out["button"]["words"]


def test_keyboard_focus_comes_back_after_a_redraw_and_buttons_say_which_task():
    # #33
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask(),agentTask({id:'t2',text:'Other task',owner:''})];
setRuns([mkRun({state:'needs_input',can_reply:true,question:'Which sample?'})]);
await window.ROWorkspace.load(root);await sleepTick();
const box=()=>byClass('ws-agent');
const names={log:find('运行记录',box()).getAttribute('aria-label'),answer:find('回答',box()).getAttribute('aria-label'),
  edit:byClass('ws-tasks').all().filter(e=>e.tagName==='BUTTON'&&e.textContent==='编辑').map(e=>e.getAttribute('aria-label')),
  remove:root.all().filter(e=>e.tagName==='BUTTON'&&e.textContent==='移除').map(e=>e.getAttribute('aria-label'))};
const logButton=find('运行记录',box());logButton.focus();await logButton.click();
const afterLog={moved:document.activeElement!==logButton,key:document.activeElement.dataset.key,label:document.activeElement.textContent,expanded:document.activeElement.getAttribute('aria-expanded')};
await find('回答',box()).click();
const reply={tag:document.activeElement.tagName,label:document.activeElement.getAttribute('aria-label')};
await find('取消',box()).click();
const closed={label:document.activeElement.textContent,key:document.activeElement.dataset.key};
const live=byClass('ws-agent-status',box());
console.log(JSON.stringify({names,afterLog,reply,closed,live:{role:live.getAttribute('role'),words:live.textContent}}));
""")
    assert out["names"]["log"] == "运行记录：Run the model" and out["names"]["answer"] == "回答：Run the model"
    assert out["names"]["edit"] == ["编辑：Run the model", "编辑：Other task"]
    assert out["names"]["remove"] == ["移除：Run the model", "移除：Other task"]
    assert out["afterLog"] == {"moved": True, "key": "agent:t1:log", "label": "运行记录", "expanded": "true"}
    assert out["reply"] == {"tag": "TEXTAREA", "label": "你的回答"}
    assert out["closed"] == {"label": "回答", "key": "agent:t1:answer"}
    assert out["live"]["role"] == "status" and "等你回答" in out["live"]["words"] and "Which sample?" in out["live"]["words"]


def test_notes_and_the_program_path_input_have_accessible_names():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];setRuns([]);
customPost=path=>path==='/api/agents/runs'?Promise.reject(Object.assign(Error('no'),{state:'agent_busy',engineMessage:'Another run is going.'})):undefined;
await window.ROWorkspace.load(root);await sleepTick();
const go=find('运行',byClass('ws-agent'));go.focus();await go.click();await sleepTick();
const note=byClass('ws-agent-note');
await find('AI 执行者').click();
const path=root.all().find(e=>e.tagName==='INPUT'&&e.dataset.key==='set-path:opencode');
console.log(JSON.stringify({note:{role:note.getAttribute('role'),words:note.textContent},path:path.getAttribute('aria-label'),
  focusBack:document.activeElement&&document.activeElement.dataset.key}));
""")
    assert out["note"]["role"] == "status" and "Another run is going." in out["note"]["words"]
    assert out["path"] == "OpenCode 的程序路径"
    assert out["focusBack"] == "agent:t1:run"


def test_buttons_carry_the_task_in_their_english_accessible_names():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];setRuns([mkRun({state:'done',can_reply:true,can_revert:true})]);
await window.ROWorkspace.load(root);await sleepTick();
const names=root.all().filter(e=>e.tagName==='BUTTON'&&e.getAttribute('aria-label')).map(e=>e.getAttribute('aria-label'));
await find('AI agent').click();
const path=root.all().find(e=>e.dataset&&e.dataset.key==='set-path:opencode');
console.log(JSON.stringify({names,path:path.getAttribute('aria-label')}));
""", lang="en")
    for name in ("Run: Run the model", "Run log: Run the model", "Send back: Run the model", "Undo this run: Run the model",
                 "Edit: Run the model", "Remove: Run the model"):
        assert name in out["names"], name
    assert out["path"] == "Program path for OpenCode"


def test_a_partly_undone_run_says_so_and_the_log_lists_what_was_left_with_the_reason():
    # #17
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
const partly=mkRun({state:'done',reverted:true,revert_skipped:4,changes:4});
setRuns([partly]);
runLogs[partly.id]=Object.assign(clone(partly),{steps:[],steps_total:0,changes:{created:[],modified:[],deleted:[]},violations:[],refusals:[],
  revert:{restored:['a.py'],removed:[],skipped:[{path:'data/new.csv',reason:'changed_since'},{path:'big.bin',reason:'no_copy'},{path:'Drive/x.xlsx',reason:'in_cloud'},{path:'raw/old.csv',reason:'kept'}]}});
await window.ROWorkspace.load(root);await sleepTick();
const partlyLabel=byClass('status-label').textContent;
await find('运行记录').click();await sleepTick();
const log=byClass('ws-run-details').textContent;
window.ROWorkspace.reset();
setRuns([mkRun({state:'done',reverted:true,revert_skipped:0})]);listed.tasks=[agentTask()];
await window.ROWorkspace.load(root);await sleepTick();
console.log(JSON.stringify({partlyLabel,log,wholeLabel:byClass('status-label').textContent}));
""")
    assert out["partlyLabel"] == "部分撤回" and out["wholeLabel"] == "已撤回本次运行"
    assert "data/new.csv 运行之后又改动过" in out["log"] and "big.bin 没有运行前的副本" in out["log"]
    assert "Drive/x.xlsx 文件只在云端" in out["log"]
    assert "raw/old.csv 你选择保留" in out["log"]  # a file the person left unchecked (18.1 item 5)


def test_the_run_log_warns_about_a_partial_picture_and_leftover_processes_and_guard_failures():
    # #15 / #36
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
const mk=(o)=>{ const r=mkRun({state:'done',can_reply:true}); runLogs[r.id]=Object.assign(clone(r),{steps:[],steps_total:0,changes:{created:[],modified:[],deleted:[]},violations:[],refusals:[]},o); setRuns([r]); };
mk({snapshot:{files:50000,partial:true},leftovers:2});
await window.ROWorkspace.load(root);await sleepTick();
await find('运行记录').click();await sleepTick();
const log=byClass('ws-run-details'),warn=log.all().find(e=>e.classList.contains('err')&&/50,000/.test(e.textContent));
const first=log.all().filter(e=>e.tagName==='P').map(e=>e.textContent);
window.ROWorkspace.reset();
mk({snapshot:{files:10,partial:false},leftovers:1});listed.tasks=[agentTask()];
await window.ROWorkspace.load(root);await sleepTick();
await find('运行记录').click();await sleepTick();
const clean=byClass('ws-run-details').textContent;
window.ROWorkspace.reset();
setRuns([mkRun({state:'failed',can_reply:true,error:{code:'guard_failed',detail:'hook did not start'}})]);listed.tasks=[agentTask()];
await window.ROWorkspace.load(root);await sleepTick();
console.log(JSON.stringify({warn:!!warn,first,clean,guard:byClass('ws-agent-error').textContent}));
""")
    assert out["warn"] and out["first"][0].startswith("这个项目文件太多，运行前只记录了前 50,000 个文件")
    assert "无法核对，也无法撤回。" in out["first"][0]
    assert "程序结束后还有 2 个它启动的进程在运行，已一并结束。" in out["first"]
    assert "50,000" not in out["clean"] and "程序结束后还有 1 个它启动的进程在运行，已一并结束。" in out["clean"]
    assert out["guard"].startswith("Research OS 的检查程序没有正常启动，为了安全没有运行。")


def test_the_run_log_warnings_read_in_english_without_chinese():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];listed.files=listed.files.filter(f=>!/[一-鿿]/.test(f.path));
const r=mkRun({state:'done',reverted:true,revert_skipped:1,can_reply:true});
runLogs[r.id]=Object.assign(clone(r),{steps:[],steps_total:0,changes:{created:[],modified:[],deleted:[]},violations:[],refusals:[],snapshot:{partial:true},leftovers:1,
  revert:{skipped:[{path:'a.csv',reason:'in_cloud'}]}});
setRuns([r]);
await window.ROWorkspace.load(root);await sleepTick();
await find('Run log').click();await sleepTick();
const one=root.textContent;
runLogs[r.id].leftovers=3;window.ROWorkspace.reset();setRuns([r]);listed.tasks=[agentTask()];
await window.ROWorkspace.load(root);await sleepTick();await find('Run log').click();await sleepTick();
const many=byClass('ws-run-details').textContent;
console.log(JSON.stringify({one,many,label:byClass('status-label').textContent}));
""", lang="en")
    assert re.search(r"[㐀-鿿]", out["one"]) is None
    assert out["label"] == "Partly undone"
    assert "After the program ended, 1 process it had started was still running. It was ended too." in out["one"]
    assert "3 processes it had started were still running" in out["many"]
    assert "a.csv The file is only in the cloud" in out["one"] and "cannot be checked or undone" in out["one"]


def test_the_budget_setting_posts_max_budget_usd_and_empty_removes_it():
    # #43
    out = run(r"""
agentsInfo=agentFixture();agentsInfo.max_budget_usd=5;
await window.ROWorkspace.load(root);await sleepTick();
await find('AI 执行者').click();
const box=()=>byClass('ws-agent-settings'),usd=()=>field('每次运行的费用上限（美元，只对 Claude Code 有效，留空为不限）',box());
const shown=usd().value;
typed(usd(),'12.5');await find('保存',usd().parentNode.parentNode).click();await sleepTick();
const saved=posted.filter(p=>p[0]==='/api/settings/agents').pop();
typed(usd(),'0');await find('保存',usd().parentNode.parentNode).click();await sleepTick();
const refused={posts:posted.filter(p=>p[0]==='/api/settings/agents').length,words:box().textContent.includes('费用上限要大于 0、不超过 1000 美元。')};
typed(usd(),'');await find('保存',usd().parentNode.parentNode).click();await sleepTick();
const cleared=posted.filter(p=>p[0]==='/api/settings/agents').pop();
console.log(JSON.stringify({shown,saved:saved[1],refused,cleared:cleared[1],after:usd().value}));
""")
    assert out["shown"] == "5"
    assert out["saved"] == {"max_budget_usd": 12.5}
    assert out["refused"] == {"posts": 1, "words": True}
    assert out["cleared"] == {"max_budget_usd": None} and out["after"] == ""


def test_an_out_of_bounds_run_reads_so_in_english_with_the_new_reasons_and_no_chinese():
    out = run(r"""
agentsInfo=agentFixture();listed.files=listed.files.filter(f=>!/[一-鿿]/.test(f.path));
listed.tasks=[agentTask({done:true,report:REPORT_PATH})];
const run=mkRun({state:'out_of_bounds',outcome:'done',can_reply:true,can_revert:true,violations:3,changes:2,report:REPORT_PATH});
setRuns([run]);
runLogs[run.id]=Object.assign(clone(run),{steps:[],steps_total:0,changes:{created:['.git/hooks/pre-commit'],modified:['.git/config'],deleted:['.researchos/backups/x.toml.20260101T000000000000Z.toml']},
  violations:[{path:'.git/hooks/pre-commit',reason:'git_hooks_changed'},{path:'.git/config',reason:'git_config_changed'},
              {path:'.researchos/backups/x.toml.20260101T000000000000Z.toml',reason:'backup_deleted'}],instruction:'',stderr_tail:'',refusals:[]});
await window.ROWorkspace.load(root);await sleepTick();
await find('Run log',byClass('ws-agent')).click();await sleepTick();
const box=byClass('ws-agent'),label=byClass('status-label',box);
console.log(JSON.stringify({label:label.textContent,cls:label.className,buttons:buttonsIn(box),text:box.textContent,oob:byClass('ws-oob',box).textContent}));
""".replace("REPORT_PATH", json.dumps(REPORT)), lang="en")
    assert out["label"] == "Went out of bounds" and out["cls"] == "status-label tone-clay oob"
    assert out["buttons"] == ["Run log", "Send back", "Undo this run", "Open the result"]
    assert "Done" not in out["text"].replace("Deleted", "")
    assert "Changed a git hook (a program git runs by itself)" in out["oob"]
    assert "Changed git’s configuration" in out["oob"] and "Deleted a Research OS backup" in out["oob"]
    assert re.search(r"[㐀-鿿]", out["text"]) is None, out["text"]


LOG_SETUP = r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];listed.files=listed.files.filter(f=>!/[一-鿿]/.test(f.path));
const r=mkRun({state:'out_of_bounds',outcome:'done',can_reply:true,can_revert:true,violations:1,changes:2,reverted:true,revert_skipped:1});
setRuns([r]);
runLogs[r.id]=Object.assign(clone(r),{steps:[],steps_total:0,changes:{created:['.claude/settings.json'],modified:[],deleted:[]},
  violations:[{path:'.claude/settings.json',reason:'agent_settings_changed'}],refusals:[],instruction:'',stderr_tail:'',
  env_removed:['GITHUB_TOKEN','FOO_SECRET'],revert:{skipped:[{path:'.git/config',reason:'protected'}]}});
await window.ROWorkspace.load(root);await sleepTick();
await find(LOG).click();await sleepTick();
const box=byClass('ws-run-details');
const env=box.all().find(e=>e.tagName==='DETAILS'&&/GITHUB_TOKEN/.test(e.textContent));
console.log(JSON.stringify({text:box.textContent,oob:byClass('ws-oob',box).textContent,env:env?env.textContent:null}));
"""


def test_the_run_log_words_the_settings_reason_the_protected_reason_and_the_withheld_variables():
    out = run(LOG_SETUP.replace("LOG", "'运行记录'"))
    assert "改动了 AI 执行程序的项目设置（.claude、.opencode、.codex、opencode.json）" in out["oob"]
    assert ".git/config 属于 Research OS 的记录文件夹或 git，撤回不会动它" in out["text"]
    assert "没有改动这个文件" not in out["text"]  # that was no reason, only the outcome again
    assert out["env"] is not None and "没有交给它的环境变量（名字像密钥，只列名字）" in out["env"]
    assert "GITHUB_TOKEN" in out["env"] and "FOO_SECRET" in out["env"]
    english = run(LOG_SETUP.replace("LOG", "'Run log'"), lang="en")
    assert "Changed the agent program’s project settings (.claude, .opencode, .codex, opencode.json)" in english["oob"]
    assert ".git/config Belongs to Research OS’s records folder or to git; undo does not touch it" in english["text"]
    assert "Left unchanged" not in english["text"]
    assert "Environment variables not given to it (their names look like credentials; names only)" in english["env"]
    assert re.search(r"[㐀-鿿]", english["text"]) is None, english["text"]


def test_a_run_without_withheld_variables_shows_no_such_line():
    out = run(LOG_SETUP.replace("LOG", "'运行记录'").replace("env_removed:['GITHUB_TOKEN','FOO_SECRET'],", "env_removed:[],"))
    assert out["env"] is None and "没有交给它的环境变量" not in out["text"]


def test_the_undo_list_gives_the_reason_for_a_file_that_belongs_to_research_os_or_git():
    out = run(UNDO_SETUP.replace("{path:'Drive/y.xlsx',reason:'in_cloud'}]", "{path:'Drive/y.xlsx',reason:'in_cloud'},{path:'.git/config',reason:'protected'}]")
              + r"""
await find('撤回本次运行',byClass('ws-agent')).click();await sleepTick();
const groups=undoBox().all().filter(e=>e.tagName==='DETAILS');
console.log(JSON.stringify({title:groups[2].children[0].textContent,cannot:groups[2].textContent}));
""")
    assert out["title"] == "撤不回的文件（4）"
    assert ".git/config 属于 Research OS 的记录文件夹或 git，撤回不会动它" in out["cannot"]


# -- 撤回本次运行 lists what it will do, and asks (DESIGN 18.1 item 5) ---------------------------------------

UNDO_SETUP = r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
const finished=mkRun({state:'done',can_revert:true,can_reply:true,changes:6});
setRuns([finished]);
runPlans[finished.id]={run:clone(finished),
  restore:[{path:'analysis.py',time:'2026-10-21T09:41:12+02:00'},{path:'raw/old.txt',time:'2026-10-21T09:40:03+02:00'}],
  remove:[{path:'out/new.csv',time:'2026-10-21T09:42:50+02:00'}],
  cannot:[{path:'data/later.csv',reason:'changed_since'},{path:'big.bin',reason:'no_copy'},{path:'Drive/y.xlsx',reason:'in_cloud'}]};
await window.ROWorkspace.load(root);await sleepTick();
const undoBox=()=>byClass('ws-agent-undo');
const ticks=()=>{ const b=undoBox(); return b?b.all().filter(e=>e.tagName==='INPUT'&&e.type==='checkbox'):[]; };
const untick=async path=>{ const t=ticks().find(e=>e.dataset.key.endsWith(':undo:'+path)); t.checked=false; await t.emit('change'); };
"""


def test_undo_lists_the_files_with_checked_boxes_their_times_and_the_ones_it_cannot_undo():
    out = run(UNDO_SETUP + r"""
await find('撤回本次运行',byClass('ws-agent')).click();await sleepTick();
const box=undoBox(),groups=box.all().filter(e=>e.tagName==='DETAILS');
console.log(JSON.stringify({posted,asked:confirmQuestions.length,
  groups:groups.map(g=>({open:g.open,title:g.children[0].textContent,chevron:g.children[0].children.some(c=>c.classList.contains('res-chevron'))})),
  rows:box.all().filter(e=>e.classList.contains('ws-undo-row')).map(r=>r.textContent),
  checked:ticks().map(t=>[t.dataset.key,t.checked]),
  restore:groups[0].all().filter(e=>e.classList.contains('ws-undo-row')).length,
  cannot:groups[2].textContent,
  count:box.all().filter(e=>e.classList.contains('ws-agent-quiet')).map(e=>e.textContent),
  keptHidden:box.all().filter(e=>e.classList.contains('ws-agent-quiet')&&e.textContent.startsWith('没勾选')).map(e=>e.classList.contains('hidden')),
  focus:document.activeElement&&document.activeElement.dataset.key,
  buttons:buttonsIn(box),confirmOff:find('确认撤回',box).disabled,label:box.getAttribute('aria-label'),role:box.getAttribute('role')}));
""")
    assert out["posted"] == [] and out["asked"] == 0
    assert [g["title"] for g in out["groups"]] == ["放回运行前的样子（2）", "移走（1）", "撤不回的文件（3）"]
    assert all(g["open"] and g["chevron"] for g in out["groups"])  # 12.13's chevron, and open until closed
    assert out["rows"] == ["analysis.py2026-10-21 09:41", "raw/old.txt2026-10-21 09:40", "out/new.csv2026-10-21 09:42"]
    assert [c for _k, c in out["checked"]] == [True, True, True]
    assert [k for k, _c in out["checked"]] == ["agent:t1:undo:analysis.py", "agent:t1:undo:raw/old.txt",
                                               "agent:t1:undo:out/new.csv"]
    assert "data/later.csv 运行之后又改动过" in out["cannot"] and "big.bin 没有运行前的副本" in out["cannot"]
    assert "Drive/y.xlsx 文件只在云端" in out["cannot"]
    assert "已选 3 项，共 3 项" in out["count"] and out["keptHidden"] == [True]
    assert out["focus"] == "agent:t1:undo:analysis.py"  # the first box is ready; nothing writes until 确认撤回
    assert out["buttons"] == ["全部勾选", "全部取消勾选", "确认撤回", "取消"] and out["confirmOff"] is False
    assert out["label"] == "撤回本次运行：Run the model" and out["role"] == "group"


def test_an_unchecked_file_is_left_out_of_the_request_and_the_log_lists_it_as_kept():
    out = run(UNDO_SETUP + r"""
await find('撤回本次运行',byClass('ws-agent')).click();await sleepTick();
const node=undoBox();
await untick('analysis.py');
const quiet=()=>undoBox().all().filter(e=>e.classList.contains('ws-agent-quiet'));
const some={count:quiet().map(e=>e.textContent),keptHidden:quiet().filter(e=>e.textContent.startsWith('没勾选')).map(e=>e.classList.contains('hidden'))};
// nothing is redrawn by a box: the same node, still unchecked, with the focus where it was
const same=ticks()[0].checked===false&&undoBox()===node;
await find('确认撤回',undoBox()).click();await sleepTick();
const note=byClass('ws-agent-note');
console.log(JSON.stringify({some,same,posted,words:note.textContent,panel:!!undoBox(),label:byClass('status-label').textContent}));
""")
    assert "已选 2 项，共 3 项" in out["some"]["count"] and out["some"]["keptHidden"] == [False]
    assert out["same"] is True
    assert out["posted"][-1] == ["/api/agents/runs/r-20261021T090000Z-aaaaaa/revert",
                                 {"project": "/projects/study", "files": ["raw/old.txt", "out/new.csv"]}]
    assert "撤回了 2 个文件的改动。" in out["words"] and "这些文件没有动：" in out["words"]
    assert "analysis.py 你选择保留" in out["words"] and "data/later.csv 运行之后又改动过" in out["words"]
    assert out["panel"] is False


def test_nothing_checked_cannot_be_confirmed_and_all_or_none_are_one_click():
    out = run(UNDO_SETUP + r"""
await find('撤回本次运行',byClass('ws-agent')).click();await sleepTick();
await find('全部取消勾选',undoBox()).click();
const none={ticked:ticks().map(t=>t.checked),off:find('确认撤回',undoBox()).disabled,count:undoBox().textContent.includes('已选 0 项，共 3 项')};
await find('确认撤回',undoBox()).click();await sleepTick();
const refused=posted.length;
await find('全部勾选',undoBox()).click();
await untick('out/new.csv');
const one={ticked:ticks().map(t=>t.checked),off:find('确认撤回',undoBox()).disabled};
console.log(JSON.stringify({none,refused,one}));
""")
    assert out["none"] == {"ticked": [False, False, False], "off": True, "count": True}
    assert out["refused"] == 0
    assert out["one"] == {"ticked": [True, True, False], "off": False}


def test_the_boxes_survive_a_redraw_and_the_list_goes_when_the_run_cannot_be_undone_any_more():
    out = run(UNDO_SETUP + r"""
await find('撤回本次运行',byClass('ws-agent')).click();await sleepTick();
await untick('raw/old.txt');
// the page reloads the project and the runs (a poll, a save elsewhere): the list stays as the person left it
await window.ROWorkspace.load(root,true);await sleepTick();
const kept={panel:!!undoBox(),ticked:ticks().map(t=>t.checked),expanded:find('撤回本次运行',byClass('ws-agent')).getAttribute('aria-expanded')};
// closing a group is remembered through a redraw too
const group=undoBox().all().find(e=>e.tagName==='DETAILS');group.open=false;await group.emit('toggle');
await window.ROWorkspace.load(root,true);await sleepTick();
const closed=undoBox().all().filter(e=>e.tagName==='DETAILS').map(g=>g.open);
// another run takes the task's place, or this one was undone elsewhere: the list is not kept for a run it does not describe
runsList.runs[0].can_revert=false;
await window.ROWorkspace.load(root,true);await sleepTick();
console.log(JSON.stringify({kept,closed,after:!!undoBox(),buttons:buttonsIn(byClass('ws-agent'))}));
""")
    assert out["kept"] == {"panel": True, "ticked": [True, False, True], "expanded": "true"}
    assert out["closed"] == [False, True, True]
    assert out["after"] is False and "撤回本次运行" not in out["buttons"]


def test_a_list_that_cannot_be_read_says_so_and_offers_another_try():
    out = run(UNDO_SETUP + r"""
let fail=true;
customGet=path=>path.endsWith('/revert-plan')&&fail?Promise.reject(Object.assign(Error('later'),{state:'agent_later_run',engineMessage:'之后的运行改动了相同的文件，请先撤回那一次'})):undefined;
await find('撤回本次运行',byClass('ws-agent')).click();await sleepTick();
const refused={words:undoBox().textContent,buttons:buttonsIn(undoBox()),focus:document.activeElement&&document.activeElement.dataset.key};
fail=false;
await find('重试',undoBox()).click();await sleepTick();
console.log(JSON.stringify({refused,posted,after:buttonsIn(undoBox()),rows:ticks().length}));
""")
    assert "没能读出撤回的清单。 之后的运行改动了相同的文件，请先撤回那一次" in out["refused"]["words"]
    assert out["refused"]["buttons"] == ["重试", "取消"] and out["refused"]["focus"] == "agent:t1:undo-cancel"
    assert out["posted"] == [] and out["after"] == ["全部勾选", "全部取消勾选", "确认撤回", "取消"] and out["rows"] == 3


def test_a_list_with_nothing_to_undo_offers_no_confirmation():
    out = run(UNDO_SETUP + r"""
runPlans[finished.id]=Object.assign(runPlans[finished.id],{restore:[],remove:[]});
await find('撤回本次运行',byClass('ws-agent')).click();await sleepTick();
console.log(JSON.stringify({words:undoBox().textContent,buttons:buttonsIn(undoBox()),focus:document.activeElement&&document.activeElement.dataset.key}));
""")
    assert "没有可以撤回的文件。" in out["words"] and "撤不回的文件（3）" in out["words"]
    assert out["buttons"] == ["取消"] and out["focus"] == "agent:t1:undo-cancel"


def test_a_moved_back_file_and_the_done_mark_are_named_for_what_they_are():
    out = run(UNDO_SETUP + r"""
runPlans[finished.id]={run:clone(finished),
  restore:[{path:'raw.txt',from:'sub/raw_old.txt',time:'2026-10-21T09:41:12+02:00'},{path:'research/tasks.json',task:'Run the model',time:'2026-10-21T09:45:00+02:00'}],
  remove:[],cannot:[]};
await find('撤回本次运行',byClass('ws-agent')).click();await sleepTick();
const rows=undoBox().all().filter(e=>e.classList.contains('ws-undo-row')).map(r=>r.textContent);
await find('确认撤回',undoBox()).click();await sleepTick();
console.log(JSON.stringify({rows,posted:posted[posted.length-1][1]}));
""")
    assert out["rows"] == ["raw.txt2026-10-21 09:41从 sub/raw_old.txt 移回",
                           "任务的完成标记：Run the model2026-10-21 09:45"]
    assert out["posted"]["files"] == ["raw.txt", "research/tasks.json"]


def test_the_undo_list_and_its_confirmation_read_in_english_with_no_chinese():
    out = run(UNDO_SETUP + r"""
await find('Undo this run',byClass('ws-agent')).click();await sleepTick();
await untick('analysis.py');
const text=undoBox().textContent,buttons=buttonsIn(undoBox());
await find('Confirm undo',undoBox()).click();await sleepTick();
console.log(JSON.stringify({text,buttons,note:byClass('ws-agent-note').textContent}));
""", lang="en")
    for words in (out["text"], out["note"]):
        assert re.search(r"[㐀-鿿]", words) is None, words
    assert out["buttons"] == ["Check all", "Uncheck all", "Confirm undo", "Cancel"]
    for expected in ("Put back as before the run (2)", "Move away (1)", "Moved into the run’s folder, not deleted.",
                     "Cannot be undone (3)", "2 of 3 selected", "Changed again after the run", "No copy from before the run",
                     "The file is only in the cloud", "this run can no longer undo them afterwards"):
        assert expected in out["text"], expected
    assert "Undid the changes to 2 files." in out["note"] and "You kept this" in out["note"]


def test_the_first_run_says_what_an_undo_cannot_bring_back_and_names_the_large_files():
    big = ", ".join(f"{{path:'data/big{i:02d}.csv',size:{(300 - i * 10) * 1024 * 1024}}}" for i in range(10))
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
bigFiles={count:12,partial:false,largest:[BIG]};
await window.ROWorkspace.load(root);await sleepTick();
await find('运行',byClass('ws-agent')).click();
console.log(JSON.stringify({question:confirmQuestions[0],asked:confirmQuestions.length,gets:gets.filter(p=>p.includes('large-files')).length}));
""".replace("BIG", big))
    question = out["question"]
    assert "运行之后可以撤回它在项目里的改动，但这些撤不回：安装的软件、下载的内容、git 里的操作，以及项目文件夹以外的一切。" in question
    assert "运行之后可以撤回。" not in question and "Anthropic" in question and question.endswith("要继续吗？")
    assert "项目里有 12 个文件超过 16 MB。运行前不会备份它们，被改动或删除后就恢复不了：" in question
    assert "data/big00.csv（300 MB）、data/big01.csv（290 MB）" in question and "data/big09.csv（210 MB）" in question
    assert "data/big10" not in question and "另有 2 个没有列出。" in question  # ten are named, the rest are a count
    assert out["asked"] == 1 and out["gets"] == 1


FIRST_RUN = r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
SETUP
await window.ROWorkspace.load(root);await sleepTick();
await find('运行',byClass('ws-agent')).click();await sleepTick();
console.log(JSON.stringify({question:confirmQuestions[0],started:posted.filter(p=>p[0]==='/api/agents/runs').length}));
"""


def test_the_first_run_names_no_large_file_when_there_is_none_or_the_engine_cannot_say():
    none = run(FIRST_RUN.replace("SETUP", ""))  # nothing over 16 MB
    one = run(FIRST_RUN.replace("SETUP", "bigFiles={count:1,partial:true,largest:[{path:'one.bin',size:1610612736}]};"))
    unknown = run(FIRST_RUN.replace(
        "SETUP", "customGet=path=>path==='/api/agents/large-files'?Promise.reject(Error('engine busy')):undefined;"))
    assert "超过 16 MB" not in none["question"] and "这些撤不回：安装的软件" in none["question"]
    assert "项目里有 1 个文件超过 16 MB" in one["question"] and "one.bin（1.5 GB）" in one["question"]
    assert "另有" not in one["question"] and "项目文件太多，这份清单只查了前 50,000 个文件。" in one["question"]
    assert "超过 16 MB" not in unknown["question"] and "这些撤不回：安装的软件" in unknown["question"]
    assert unknown["started"] == 1  # the question is still asked, and the run starts when it is accepted


def test_the_first_run_confirmation_reads_in_english_and_is_asked_once_per_agent():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
bigFiles={count:11,partial:false,largest:[{path:'a.csv',size:52428800}]};
await window.ROWorkspace.load(root);await sleepTick();
await find('Run',byClass('ws-agent')).click();
const first=confirmQuestions[0];
runsList.runs[0].state='done';Object.assign(runsList.runs[0],{can_reply:true,can_stop:false});runsList.active=null;
await window.ROWorkspace.load(root,true);await sleepTick();
await find('Send back',byClass('ws-agent')).click();typed(byClass('form-textarea'),'again');
await find('Send',byClass('ws-agent')).click();await sleepTick();
console.log(JSON.stringify({first,asked:confirmQuestions.length,gets:gets.filter(p=>p.includes('large-files')).length,
  posts:posted.map(p=>p[0].split('/').pop())}));
""", lang="en")
    assert re.search(r"[㐀-鿿]", out["first"]) is None, out["first"]
    assert "but not these: software it installs, content it downloads, what it does in git, and anything outside the project folder." in out["first"]
    assert "11 files in the project are over 16 MB. They are not backed up before the run" in out["first"]
    assert "a.csv (50 MB)" in out["first"] and "10 more are not listed." in out["first"] and out["first"].endswith("Continue?")
    assert out["asked"] == 1 and out["gets"] == 1 and out["posts"] == ["runs", "reply"]  # a reply is not a first run


# -- 18.1 item 6: refusals and run errors in the person's language ---------------------------------------------

LOG_STEPS_SETUP = r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
const run=mkRun({state:'failed',can_reply:true,error:{code:'error_max_budget_usd',detail:'Reached maximum budget ($0.001)'}});
setRuns([run]);
runLogs[run.id]=Object.assign(clone(run),{steps:[
  {kind:'refused',text:'Research OS: data/raw.csv is a protected file and cannot be changed.',tool:'Write',code:'protected_file'},
  {kind:'refused',text:'Research OS: Changing git state is not allowed.',tool:'Bash',code:'git_state'},
  {kind:'refused',text:'Permission to use Bash with command git push has been denied.',tool:'Bash',code:'permission_rule'},
  {kind:'refused',text:'a refusal the page has never heard of',tool:'Bash',code:'brand_new_code'},
  {kind:'refused',text:'a refusal logged before codes existed',tool:null},
  {kind:'error',text:'The Research OS tools did not start (status: failed).',tool:null,code:'mcp_failed'},
  {kind:'error',text:'rate limit exceeded',tool:null},
  {kind:'tool',text:'Bash: python fit.py',tool:'Bash'},
  {kind:'end',text:'Reached maximum budget ($0.001)',tool:null,code:'error_max_budget_usd'}],
  steps_total:9,changes:{created:[],modified:[],deleted:[],by_person:[]},violations:[],instruction:'',stderr_tail:'',refusals:[]});
await window.ROWorkspace.load(root);await sleepTick();
await find(LOG,byClass('ws-agent')).click();await sleepTick();
const d=byClass('ws-run-details');
const rows=d.all().filter(e=>e.classList.contains('ws-step')).map(li=>({
  kind:li.children[0].textContent,
  text:li.all().filter(e=>e.classList.contains('ws-step-text')).map(e=>e.textContent)[0],
  detail:li.all().filter(e=>e.tagName==='PRE').map(e=>e.textContent)[0]||null,
  summary:li.all().filter(e=>e.tagName==='SUMMARY').map(e=>e.textContent)[0]||null,
  mono:li.all().some(e=>e.classList.contains('mono')),
  bad:li.classList.contains('bad')}));
console.log(JSON.stringify({rows,words:d.textContent,top:byClass('ws-agent-error').textContent}));
"""


def test_a_refusal_and_an_error_in_the_log_are_a_sentence_with_the_programs_words_behind_details():
    out = run(LOG_STEPS_SETUP.replace("LOG", "'运行记录'"))
    rows = out["rows"]
    assert [r["kind"] for r in rows] == ["被拒绝", "被拒绝", "被拒绝", "被拒绝", "被拒绝", "出错", "出错", "操作", "结束"]
    assert [r["text"] for r in rows] == [
        "它想改动或删除运行前就有的数据文件（或尝试表、任务文件），被拦下了。",
        "它想改变 git 的状态（提交、推送等），被拦下了。",
        "它想做的事被运行的权限规则拒绝了，比如 git 命令。",
        "这一步被拒绝了。", "这一步被拒绝了。",   # a code the page does not know, and no code: a generic sentence
        "Research OS 的工具没有启动，AI 执行者无法汇报结果。",
        "程序报告了一个错误。",
        "Bash: python fit.py",                    # a step that was never told by a code is as it was
        "费用达到了上限。"]
    assert [r["detail"] for r in rows] == [
        "Research OS: data/raw.csv is a protected file and cannot be changed.",
        "Research OS: Changing git state is not allowed.",
        "Permission to use Bash with command git push has been denied.",
        "a refusal the page has never heard of", "a refusal logged before codes existed",
        "The Research OS tools did not start (status: failed).", "rate limit exceeded", None,
        "Reached maximum budget ($0.001)"]
    assert [r["summary"] for r in rows if r["detail"]] == ["详情"] * 8
    assert [r["mono"] for r in rows] == [False] * 7 + [True, False]  # only the command the agent ran is monospaced
    assert [r["bad"] for r in rows] == [True] * 7 + [False, False]
    for code in ("protected_file", "git_state", "permission_rule", "brand_new_code", "mcp_failed", "error_max_budget_usd"):
        assert code not in out["words"]  # never a raw code
    assert out["top"].startswith("费用达到了上限。")


def test_a_refusal_and_an_error_in_the_log_read_in_english_with_no_chinese():
    out = run(LOG_STEPS_SETUP.replace("LOG", "'Run log'"), lang="en")
    assert [r["text"] for r in out["rows"]] == [
        "It tried to change or delete a data file that existed before the run (or the attempt table or the task file) and was stopped.",
        "It tried to change git’s state (commit, push and the like) and was stopped.",
        "A permission rule of this run refused what it tried to do, for example a git command.",
        "This step was refused.", "This step was refused.",
        "Research OS’s tools did not start, so the agent cannot report back.",
        "The program reported an error.", "Bash: python fit.py", "The cost reached its limit."]
    assert [r["summary"] for r in out["rows"] if r["detail"]] == ["Details"] * 8
    assert not re.search(r"[㐀-鿿]", out["words"] + out["top"])


END_STEPS_SETUP = r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
const run=mkRun({state:'failed',can_reply:true,error:{code:'internal',detail:'OSError: [Errno 28] No space left on device'}});
setRuns([run]);
runLogs[run.id]=Object.assign(clone(run),{steps:[
  {kind:'end',text:'OSError: [Errno 28] No space left on device',tool:null,code:'internal'},
  {kind:'end',text:'',tool:null,code:'stopped'},
  {kind:'end',text:'Research OS was closed while the run was going',tool:null,code:'shutdown'},
  {kind:'end',text:'Research OS stopped while the run was going',tool:null,code:'engine_gone'}],
  steps_total:4,changes:{created:[],modified:[],deleted:[],by_person:[]},violations:[],instruction:'',stderr_tail:'',refusals:[]});
await window.ROWorkspace.load(root);await sleepTick();
await find(LOG,byClass('ws-agent')).click();await sleepTick();
const d=byClass('ws-run-details');
const rows=d.all().filter(e=>e.classList.contains('ws-step')).map(li=>({
  text:li.all().filter(e=>e.classList.contains('ws-step-text')).map(e=>e.textContent)[0],
  detail:li.all().filter(e=>e.tagName==='PRE').map(e=>e.textContent)[0]||null}));
console.log(JSON.stringify({rows,words:d.textContent,top:byClass('ws-agent-error').textContent}));
"""


def test_an_internal_error_is_worded_generally_and_a_stopped_or_interrupted_end_is_told_by_its_code():
    """18.1 item 6: `internal` is also the code of a crash before the program started, so it does not say the output was
    being read. The engine-written end of a stopped or interrupted run carries a code and the page words it."""
    out = run(END_STEPS_SETUP.replace("LOG", "'运行记录'"))
    assert out["top"].startswith("Research OS 在运行中出了错。") and "读取程序的输出" not in out["words"] + out["top"]
    assert [r["text"] for r in out["rows"]] == [
        "Research OS 在运行中出了错。", "运行已被你停止。", "运行期间 Research OS 被关闭了。", "运行期间 Research OS 退出了。"]
    assert [r["detail"] for r in out["rows"]] == [
        "OSError: [Errno 28] No space left on device", None,  # a stopped run has no words of the engine's under it
        "Research OS was closed while the run was going", "Research OS stopped while the run was going"]
    english = run(END_STEPS_SETUP.replace("LOG", "'Run log'"), lang="en")
    assert english["top"].startswith("Research OS ran into an internal error during the run.")
    assert [r["text"] for r in english["rows"]] == [
        "Research OS ran into an internal error during the run.", "The run was stopped by you.",
        "Research OS was closed while the run was going.", "Research OS quit while the run was going."]
    assert "reading" not in english["words"] + english["top"]
    assert not re.search(r"[㐀-鿿]", english["words"] + english["top"])


def _table_words(name: str) -> dict[str, str]:
    script = (WEBAPP / "workspace.js").read_text(encoding="utf-8")
    table = re.search(r"const " + name + r" = \{(.*?)\n  \};", script, re.S).group(1)
    # Each entry is `code: () => t("sentence")` (13.6: looked up when shown); the sentence is the key.
    return dict(re.findall(r'(\w+): \(\) => t\("([^"]+)"\)', table))


def test_every_code_the_engine_can_attach_has_a_sentence_in_both_languages():
    """Each refusal code the guard knows, and the codes the streams add, are words on the page and English in
    i18n.js (the scanner of test_v9_language reads workspace.js whole, so each sentence has an entry; this holds the\n    codes to the table). A code nobody wrote words for reads as a
    generic sentence, which is tested above."""
    from researchos.agents import guard

    steps = _table_words("STEP_WORDS")
    assert set(steps) == set(guard.REFUSAL_CODES) | {"permission_rule", "tool_unavailable", "mcp_failed"}
    errors = _table_words("ERROR_WORDS")
    assert {"internal", "error_max_turns", "error_during_execution", "error_max_budget_usd", "guard_failed",
            "shutdown", "engine_gone", "stopped"} <= set(errors)  # the last three: the codes of an end the engine writes
    i18n = (WEBAPP / "i18n.js").read_text(encoding="utf-8")
    for sentence in [*steps.values(), *errors.values(), "这一步被拒绝了。", "程序报告了一个错误。", "这次运行没有成功。", "状态未知"]:
        match = re.search(r'"' + re.escape(sentence) + r'":\s*"([^"]+)"', i18n)
        assert match, sentence
        english = match.group(1)
        assert not re.search(r"[㐀-鿿]|->|→", english), english


def test_a_state_the_page_does_not_know_reads_as_a_generic_word_never_as_its_code():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
setRuns([mkRun({state:'frobnicated',error:{code:'error_something_new',detail:'The thing went wrong in a new way'}})]);
await window.ROWorkspace.load(root);await sleepTick();
const label=byClass('status-label');
console.log(JSON.stringify({label:label.textContent,error:byClass('ws-agent-error').textContent,agent:byClass('ws-agent').textContent}));
""")
    assert out["label"] == "状态未知"
    assert out["error"].startswith("这次运行没有成功。") and "error_something_new" not in out["error"]
    assert "frobnicated" not in out["agent"] and "error_something_new" not in out["agent"]
    english = run(r"""
agentsInfo=agentFixture();listed.tasks=[agentTask()];
setRuns([mkRun({state:'frobnicated'})]);
await window.ROWorkspace.load(root);await sleepTick();
console.log(JSON.stringify({label:byClass('status-label').textContent}));
""", lang="en")
    assert english["label"] == "Unknown state"

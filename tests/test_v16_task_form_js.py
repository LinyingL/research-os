"""Research OS V16, task B2-3 (DESIGN.md 21.4 'The task form', 21.8), the page: the task form on 项目首页.

The task is written in a field of several lines that grows; 负责人 stands in two labelled groups, people and agents; 日期 is
optional and empty by default, kept in the task's own `due`; 「保存并运行」 stands beside the save button when the owner is an
agent and runs the task by the row's own path once it is saved; the fields stand in the order attempt, task, the task it
follows, owner, date, research context, and the save button is the form's one primary button. The scripts run under node on
the stand-in DOM of the home's other tests, in both languages. Every test names its scenario."""

from __future__ import annotations

import json

import pytest

from test_v9_language import CJK, needs_node
from test_workspace import root  # noqa: F401 -- fixture
from test_workspace_js import run as run_workspace

ATT = "attempt:research/attempts.md#1"


def _w(lang: str):
    return lambda zh, en: en if lang == "en" else zh


def _js(script: str) -> str:
    return script.replace("ATT", json.dumps(ATT))


#: A PI's project with a team and the one agent of the fixture on this Mac; `T` is the page's own translator.
_SETUP = r"""
const T=window.ROI18N.t;
listed.mode='pi';
meInfo={state:'present',me:{id:'m-lin',name:'Lin Ying',email:'l@x.org'}};
shareInfo={role:'pi',me:{id:'m-lin'},team:[{id:'m-lin',name:'Lin Ying',role:'pi'},{id:'m-wang',name:'Wang Wei',role:'member'}]};
agentsInfo=agentFixture();
const formButtons=()=>byClass('ws-task-buttons').children.map(b=>b.textContent);
const primaries=()=>byClass('ws-task-form').all().filter(e=>e.tagName==='BUTTON'&&e.className.includes('btn-primary')).map(e=>e.textContent);
const pickAtt=async()=>{ const s=field(T('属于哪条尝试')); s.value=ATT; await s.emit('change'); };
const runsPosted=()=>posted.filter(p=>p[0]==='/api/agents/runs');
const tasksPosted=()=>posted.filter(p=>p[0]==='/api/workspace/tasks');
"""


# -- the fields: their order, and the task in several lines -------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_form_asks_attempt_then_task_then_the_task_it_follows_then_owner_date_and_research_context_in_either_language(lang):
    w = _w(lang)
    out = run_workspace(_js(_SETUP + r"""
listed.attempts=[{id:ATT,label:'First check'}];
listed.tasks=[{id:'a',text:'Collect the data',owner:'Lin',due:'',done:false,attempt:ATT}]; listed.tasks_revision='tasks-1';
await window.ROWorkspace.load(root); await sleepTick();
const form=()=>byClass('ws-task-form');
const labels=()=>form().children.filter(e=>e.tagName==='LABEL').map(e=>e.children[0].textContent);
const plain=labels(), controls=form().children.filter(e=>e.tagName==='LABEL').map(e=>e.children[1].tagName), buttons=formButtons(), primary=primaries();
await pickOwner(T('其他人…'), T('负责人'));   // a typed name has its field, right after the owner
const typedName=labels();
console.log(JSON.stringify({plain,typedName,buttons,primary,controls}));
"""), lang=lang)
    order = [w("属于哪条尝试", "Which attempt it belongs to"), w("任务", "Task"), w("接着哪个任务", "Which task it follows"), w("负责人", "Owner"),
             w("日期", "Date"), w("研究上下文（可选）", "Research context (optional)")]
    assert out["plain"] == order
    assert out["typedName"] == order[:4] + [w("姓名", "Name")] + order[4:]
    assert out["controls"][:6] == ["SELECT", "TEXTAREA", "SELECT", "SELECT", "INPUT", "SELECT"]
    # one primary button, the save button; 清空 is the quiet one; nothing is offered to run for a person
    assert out["buttons"] == [w("添加任务", "Add task"), w("清空", "Clear")] and out["primary"] == [w("添加任务", "Add task")]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_task_is_written_in_several_lines_that_enter_makes_and_the_field_grows_with_what_is_typed(lang):
    w = _w(lang)
    out = run_workspace(_js(_SETUP + r"""
listed.tasks=[]; listed.tasks_revision='tasks-0';
await window.ROWorkspace.load(root); await sleepTick();
await pickAtt(); const area=field(T('任务',{n:1}));
const kind={tag:area.tagName, rows:area.rows, placeholder:area.placeholder, keys:Object.keys(area.on)};
// the stand-in has no layout: the browser's measures are set by hand (the three rows are 73 high and the box adds 2 for its border;
// the content of five lines is 96 high)
area.offsetWidth=600; area.offsetHeight=75; area.clientHeight=73; area.scrollHeight=73;
const empty=area.style.height;
area.scrollHeight=96;
typed(area,'Line one\nLine two\nLine three\nLine four\nLine five');
const grown=area.style.height;
area.scrollHeight=73;
typed(area,'Line one');
const shrunk=area.style.height;
// Enter is a new line: nothing is sent, and the form does not submit
typed(area,'  Survey the vendors\n\nStart with the three largest.  \n');
const draft={value:area.value, posted:posted.length, dirty:window.ROWorkspace.isDirty()};
await find(T('添加任务')).click(); await sleepTick();
const row=byClass('ws-task-text');
console.log(JSON.stringify({kind,empty,grown,shrunk,draft,saved:listed.tasks.map(t=>t.text),shown:row.textContent,cleared:field(T('任务',{n:1})).value,
  heightAfter:field(T('任务',{n:1})).style.height}));
"""), lang=lang)
    assert out["kind"]["tag"] == "TEXTAREA" and out["kind"]["rows"] >= 3                     # several lines, not one
    assert out["kind"]["placeholder"] == w("例如：整理下一次组会要讨论的问题", "For example: gather questions for the next group meeting")
    assert "keydown" not in out["kind"]["keys"] and "keypress" not in out["kind"]["keys"]    # nothing takes Enter away
    assert out["empty"] == "" and out["grown"] == "98px" and out["shrunk"] == ""             # three rows to begin with; it follows the content, up and down
    assert out["draft"] == {"value": "  Survey the vendors\n\nStart with the three largest.  \n", "posted": 0, "dirty": True}
    # saved as written but for the blank edges; the row shows the lines as they were written
    assert out["saved"] == ["Survey the vendors\n\nStart with the three largest."]
    assert out["shown"] == "Survey the vendors\n\nStart with the three largest." and out["cleared"] == ""


@needs_node
def test_a_task_written_on_one_line_edits_and_shows_as_before_and_a_task_of_several_lines_comes_back_whole_into_the_field():
    out = run_workspace(_js(r"""
listed.attempts=[{id:ATT,label:'First check'}];
listed.tasks=[{id:'one',text:'Check the sample',owner:'Lin',due:'2026-11-02',done:false,attempt:ATT},
              {id:'two',text:'Rerun the model\nwith all members\n\nand report the gap',owner:'',due:'',done:false,attempt:ATT,after:'one'}];
listed.tasks_revision='tasks-1';
await window.ROWorkspace.load(root); await sleepTick();
const rows=root.all().filter(e=>e.classList.contains('ws-task'));
const shown=rows.map(r=>byClass('ws-task-text',r).textContent);
await find('编辑',rows[0]).click();
const one={text:field('任务').value,tag:field('任务').tagName,due:field('日期').value,owner:field('负责人').value};
await find('保存任务').click(); await sleepTick();
const unchanged=clone(listed.tasks[0]);
await find('编辑',root.all().filter(e=>e.classList.contains('ws-task'))[1]).click();
const two=field('任务').value;
typed(field('任务'),two+'\nPlus the outliers.'); await find('保存任务').click(); await sleepTick();
console.log(JSON.stringify({shown,one,unchanged,two,saved:listed.tasks[1].text,after:listed.tasks[1].after,posted:posted.length}));
"""))
    assert out["shown"] == ["Check the sample", "Rerun the model\nwith all members\n\nand report the gap"]
    assert out["one"] == {"text": "Check the sample", "tag": "TEXTAREA", "due": "2026-11-02", "owner": "Lin"}
    assert out["unchanged"] == {"id": "one", "text": "Check the sample", "owner": "Lin", "due": "2026-11-02", "done": False, "attempt": ATT}
    assert out["two"] == "Rerun the model\nwith all members\n\nand report the gap"
    assert out["saved"] == out["two"] + "\nPlus the outliers." and out["after"] == "one" and out["posted"] == 2


def test_the_engine_keeps_a_task_of_several_lines_as_written_and_a_task_without_a_date_has_the_empty_date_it_always_had(root):
    """No new field and no new validation: `due` is the task's date, empty unless one was given (21.4)."""
    import json as _json

    from attempt_seed import seed_attempt
    from researchos import workspace

    attempt = seed_attempt(root)
    tasks = [{"id": "plain", "text": "Survey the vendors\n\nStart with the three largest.", "owner": "", "due": "", "done": False, "attempt": attempt},
             {"id": "dated", "text": "Rerun", "owner": "Lin", "due": "2026-11-04", "done": False, "attempt": attempt}]
    workspace.save_tasks(root, {"tasks": tasks, "revision": None})
    assert _json.loads((root / workspace.TASKS_PATH).read_text()) == tasks
    with pytest.raises(workspace.WorkspaceError):   # the date, when given, is still a calendar date
        workspace.save_tasks(root, {"tasks": [dict(tasks[0], due="2026-02-30")], "revision": workspace.read_tasks(root)[1]})


# -- the date -----------------------------------------------------------------------------------------------------------


@needs_node
def test_the_date_is_empty_by_default_is_kept_in_the_tasks_own_due_only_when_given_and_adds_no_field():
    out = run_workspace(_js(r"""
let made=0; window.crypto.randomUUID=()=>'task-new-'+(++made);
listed.tasks=[]; listed.tasks_revision='tasks-0';
await window.ROWorkspace.load(root); await sleepTick();
const initial={value:field('日期').value, type:field('日期').type, dirty:window.ROWorkspace.isDirty()};
await pickAttempt(); typed(field('任务'),'No date'); await find('添加任务').click(); await sleepTick();
const sentFirst=clone(posted[0][1].tasks);
const blankAgain=field('日期').value;
typed(field('任务'),'With a date'); typed(field('日期'),'2026-11-02'); await find('添加任务').click(); await sleepTick();
const sentSecond=clone(posted[1][1].tasks);
await find('编辑',root.all().filter(e=>e.classList.contains('ws-task'))[1]).click();
const edit=field('日期').value;
typed(field('日期'),''); await find('保存任务').click(); await sleepTick();
console.log(JSON.stringify({initial,sentFirst,blankAgain,sentSecond,edit,cleared:listed.tasks.map(t=>t.due)}));
"""))
    assert out["initial"] == {"value": "", "type": "date", "dirty": False}               # nothing is filled in, not even today
    assert out["sentFirst"] == [{"id": "task-new-1", "text": "No date", "owner": "", "due": "", "done": False, "attempt": ATT}]
    assert out["blankAgain"] == ""
    assert [t["due"] for t in out["sentSecond"]] == ["", "2026-11-02"]
    assert set(out["sentSecond"][1]) == {"id", "text", "owner", "due", "done", "attempt"}   # `due` is the one date field there is
    assert out["edit"] == "2026-11-02" and out["cleared"] == ["", ""]                            # a date taken out is empty again, not a missing field


# -- the owner: people and agents in two groups --------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_owner_control_shows_people_and_agents_in_two_labelled_groups_and_an_agent_is_named_by_agent_and_model(lang):
    w = _w(lang)
    out = run_workspace(_js(_SETUP + r"""
const shape=select=>select.children.map(g=>g.tagName==='OPTGROUP'?{group:g.label,options:g.children.map(o=>[o.textContent,o.value])}:{option:[g.textContent,g.value]});
await window.ROWorkspace.load(root); await sleepTick();
const select=field(T('负责人'));
const full=shape(select);
await pickOwner('Claude Code · sonnet', T('负责人'));
const chosen={value:select.value, draft:window.ROWorkspace.isDirty()};
await pickAtt(); typed(field(T('任务',{n:1})),'Fit the model'); await find(T('添加任务')).click(); await sleepTick();
console.log(JSON.stringify({full,chosen,saved:listed.tasks.map(t=>t.owner)}));
"""), lang=lang)
    assert out["full"] == [
        {"option": [w("不指定", "Not assigned"), ""]},
        {"group": w("人员", "People"), "options": [[w("Lin Ying（我）", "Lin Ying (me)"), "Lin Ying"], ["Wang Wei", "Wang Wei"],
                                                  [w("其他人…", "Someone else…"), "\u0000other"]]},
        {"group": w("这台电脑上的 AI 执行者", "AI agents on this Mac"), "options": [["Claude Code · sonnet", "agent:claude-code:sonnet"]]}]
    assert out["chosen"]["value"] == "agent:claude-code:sonnet" and out["saved"] == ["agent:claude-code:sonnet"]


@needs_node
def test_the_owner_control_without_an_agent_on_this_mac_leads_to_the_settings_from_the_agents_group_and_without_agent_information_has_none():
    shape = r"""const shape=select=>select.children.map(g=>g.tagName==='OPTGROUP'?{group:g.label,options:g.children.map(o=>[o.textContent,o.value])}:{option:[g.textContent,g.value]});"""
    out = run_workspace(shape + r"""
agentsInfo=agentFixture(); agentsInfo.agents=[];
await window.ROWorkspace.load(root); await sleepTick();
const none=shape(field('负责人'));
window.ROWorkspace.reset(); agentsInfo=null;
await window.ROWorkspace.load(root); await sleepTick();
console.log(JSON.stringify({none,unknown:shape(field('负责人'))}));
""")
    # 21.7: with no name set the researcher is among the people as 我 (written to tasks.json as 我, whatever the screen says)
    assert out["none"] == [{"option": ["不指定", ""]}, {"group": "人员", "options": [["我", "我"], ["其他人…", "\u0000other"]]},
                           {"group": "这台电脑上的 AI 执行者", "options": [["设置 AI 执行者…", "\u0000settings"]]}]
    assert out["unknown"] == [{"option": ["不指定", ""]}, {"group": "人员", "options": [["我", "我"], ["其他人…", "\u0000other"]]}]


@needs_node
def test_a_name_typed_by_hand_stays_among_the_people_and_an_agent_this_mac_lacks_stays_among_the_agents_while_it_is_the_value():
    out = run_workspace(r"""
agentsInfo=agentFixture();
listed.tasks=[{id:'a',text:'By hand',owner:'Lin',due:'',done:false},agentTask({id:'b',owner:'agent:opencode:lmstudio/qwen3-coder'})];
await window.ROWorkspace.load(root); await sleepTick();
const rows=()=>root.all().filter(e=>e.classList.contains('ws-task'));
const groupsOf=select=>select.children.filter(g=>g.tagName==='OPTGROUP').map(g=>[g.label,g.children.map(o=>o.textContent)]);
await find('编辑',rows()[0]).click();
const person=groupsOf(field('负责人'));
await find('编辑',rows()[1]).click();
console.log(JSON.stringify({person,agent:groupsOf(field('负责人')),value:field('负责人').value}));
""")
    assert out["person"] == [["人员", ["我", "Lin", "其他人…"]], ["这台电脑上的 AI 执行者", ["Claude Code · sonnet"]]]
    assert out["agent"] == [["人员", ["我", "其他人…"]], ["这台电脑上的 AI 执行者", ["Claude Code · sonnet", "OpenCode · lmstudio/qwen3-coder"]]]
    assert out["value"] == "agent:opencode:lmstudio/qwen3-coder"


# -- 保存并运行 ---------------------------------------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_save_and_run_stands_beside_the_save_button_only_while_the_owner_is_an_agent_and_is_never_the_primary_button(lang):
    w = _w(lang)
    out = run_workspace(_js(_SETUP + r"""
await window.ROWorkspace.load(root); await sleepTick();
const states=[];
const note=async label=>{ if(label) await pickOwner(label, T('负责人')); states.push({buttons:formButtons(),primary:primaries(),
  disabled:byClass('ws-task-buttons').children.map(b=>b.disabled)}); };
await note();                                    // nobody
await note('Lin Ying'+(window.ROI18N.lang==='en'?' (me)':'（我）'));  // a person
await note('Claude Code · sonnet');              // an agent
await note(T('不指定'));                          // nobody again
await note('Claude Code · sonnet');
await pickOwner(T('其他人…'), T('负责人'));        // a name typed by hand is a person
states.push({buttons:formButtons(),primary:primaries()});
console.log(JSON.stringify({states,noChinese:window.ROI18N.lang==='en'?noChinese(byClass('ws-task-form').textContent):true}));
"""), lang=lang)
    save, clear, run = w("添加任务", "Add task"), w("清空", "Clear"), w("保存并运行", "Save and run")
    states = out["states"]
    assert [s["buttons"] for s in states] == [[save, clear], [save, clear], [save, run, clear], [save, clear], [save, run, clear], [save, clear]]
    assert all(s["primary"] == [save] for s in states)           # the save button is the one primary button of the form
    assert states[2]["disabled"] == [False, False, False]
    assert out["noChinese"]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_save_and_run_saves_the_task_then_starts_the_run_by_the_rows_own_path_with_its_confirmation_and_says_so(lang):
    w = _w(lang)
    out = run_workspace(_js(_SETUP + r"""
bigFiles={count:1,partial:false,largest:[{path:'data/big.bin',size:52428800}]};
listed.attempts=[{id:ATT,label:'First check'}]; listed.tasks=[]; listed.tasks_revision='tasks-0';
await window.ROWorkspace.load(root); await sleepTick();
await pickAtt(); await pickOwner('Claude Code · sonnet', T('负责人'));
typed(field(T('任务',{n:1})),'Fit the model\nwith the new sample'); typed(field(T('日期')),'2026-11-02');
await find(T('保存并运行')).click(); await sleepTick();
const row=byClass('ws-task');
console.log(JSON.stringify({order:posted.map(p=>p[0]),tasks:clone(listed.tasks),run:runsPosted().map(p=>p[1]),questions:confirmQuestions.slice(),
  status:byClass('ws-status',byClass('ws-tasks').children[byClass('ws-tasks').children.length-1]).textContent,
  label:byClass('status-label',row).textContent,form:{text:field(T('任务',{n:1})).value,attempt:field(T('属于哪条尝试')).value,owner:field(T('负责人')).value},
  buttons:formButtons(),busy:window.ROWorkspace.isDirty()}));
"""), lang=lang)
    assert out["order"] == ["/api/workspace/tasks", "/api/agents/runs"]                        # saved first, then run
    assert out["tasks"] == [{"id": "task-fixture-1", "text": "Fit the model\nwith the new sample", "owner": "agent:claude-code:sonnet",
                             "due": "2026-11-02", "done": False, "attempt": ATT}]
    assert out["run"] == [{"project": "/projects/study", "task": "task-fixture-1"}]
    assert len(out["questions"]) == 1 and "data/big.bin" in out["questions"][0]               # the row's confirmation, large files and all
    assert out["questions"][0].endswith(w("要继续吗？", "Continue?"))
    assert out["status"] == w("任务已保存，运行已开始。", "Task saved. The run has started.")
    assert out["label"] == w("准备中", "Preparing")
    assert out["form"] == {"text": "", "attempt": ATT, "owner": ""} and out["buttons"] == [w("添加任务", "Add task"), w("清空", "Clear")]


@needs_node
def test_a_declined_confirmation_leaves_the_task_saved_and_no_run_started_and_the_run_can_still_be_started_from_its_row():
    out = run_workspace(_js(_SETUP + r"""
listed.tasks=[]; listed.tasks_revision='tasks-0';
await window.ROWorkspace.load(root); await sleepTick();
await pickAtt(); await pickOwner('Claude Code · sonnet', '负责人'); typed(field('任务'),'Fit the model');
answers.push(false);
await find('保存并运行').click(); await sleepTick();
const last=byClass('ws-tasks').children[byClass('ws-tasks').children.length-1];
const declined={order:posted.map(p=>p[0]),tasks:listed.tasks.length,questions:confirmQuestions.length,status:byClass('ws-status',last).textContent,
  rowButtons:buttonsIn(byClass('ws-agent')),open:byClass('ws-task-details').open};
await find('运行',byClass('ws-agent')).click(); await sleepTick();
console.log(JSON.stringify({declined,order:posted.map(p=>p[0]),questions:confirmQuestions.length}));
"""))
    assert out["declined"]["order"] == ["/api/workspace/tasks"] and out["declined"]["tasks"] == 1 and out["declined"]["questions"] == 1
    assert out["declined"]["status"] == "任务已保存，没有开始运行。" and out["declined"]["rowButtons"] == ["运行"]
    assert out["declined"]["open"] is False                                                   # a person's own no needs no explanation
    assert out["order"] == ["/api/workspace/tasks", "/api/agents/runs"] and out["questions"] == 2   # the row's 运行 asks as it always did


@needs_node
def test_when_the_save_fails_or_is_refused_nothing_runs_and_nothing_is_asked_and_what_was_typed_stays():
    out = run_workspace(_js(_SETUP + r"""
listed.tasks=[]; listed.tasks_revision='tasks-0';
await window.ROWorkspace.load(root); await sleepTick();
await pickOwner('Claude Code · sonnet', '负责人'); typed(field('任务'),'Fit the model');
// no attempt chosen: refused before anything is sent
await find('保存并运行').click(); await sleepTick();
const refused={posted:posted.length,focus:focused===field('属于哪条尝试'),words:byClass('ws-tasks').textContent.includes('新任务要属于一条尝试')};
// an empty task: refused too
await pickAtt(); typed(field('任务'),'   ');
await find('保存并运行').click(); await sleepTick();
const empty={posted:posted.length,words:byClass('ws-tasks').textContent.includes('请写下要做的事。')};
// the engine refuses the save
typed(field('任务'),'Fit the model');
customPost=(path)=>{ if(path==='/api/workspace/tasks') { const e=new Error('disk full'); e.engineMessage='磁盘已满'; throw e; } };
await find('保存并运行').click(); await sleepTick();
console.log(JSON.stringify({refused,empty,order:posted.map(p=>p[0]),questions:confirmQuestions.length,runs:runsList.runs.length,
  words:byClass('ws-tasks').textContent.includes('没能保存任务，当前填写已保留。 磁盘已满'),text:field('任务').value,owner:field('负责人').value,
  buttons:formButtons(),tasks:listed.tasks.length}));
"""))
    assert out["refused"] == {"posted": 0, "focus": True, "words": True} and out["empty"] == {"posted": 0, "words": True}
    assert out["order"] == ["/api/workspace/tasks"] and out["questions"] == 0 and out["runs"] == 0 and out["tasks"] == 0
    assert out["words"] and out["text"] == "Fit the model" and out["owner"] == "agent:claude-code:sonnet"
    assert out["buttons"] == ["添加任务", "保存并运行", "清空"]                                  # the form is as it was, to try again


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_run_that_cannot_start_leaves_the_task_saved_opens_its_row_where_the_reason_is_and_says_where_to_look(lang):
    w = _w(lang)
    out = run_workspace(_js(_SETUP + r"""
agentsInfo.mcp=false;
listed.tasks=[]; listed.tasks_revision='tasks-0';
await window.ROWorkspace.load(root); await sleepTick();
await pickAtt(); await pickOwner('Claude Code · sonnet', T('负责人')); typed(field(T('任务',{n:1})),'Fit the model');
await find(T('保存并运行')).click(); await sleepTick();
const last=byClass('ws-tasks').children[byClass('ws-tasks').children.length-1];
console.log(JSON.stringify({order:posted.map(p=>p[0]),open:byClass('ws-task-details').open,
  note:byClass('ws-agent-note').textContent.slice(0,40),status:byClass('ws-status',last).textContent,statusClass:byClass('ws-status',last).className}));
"""), lang=lang)
    assert out["order"] == ["/api/workspace/tasks"]
    assert out["open"] is True                                       # the rows are shut by default (21.4); this one shows its reason
    assert out["note"].startswith(w("运行 AI 执行者需要 mcp 组件", "Running an AI agent needs the mcp"))
    assert out["status"] == w("任务已保存，但运行没有开始。原因写在这项任务那一行里。", "Task saved, but the run did not start. The reason is in that task’s row.")
    assert out["statusClass"].endswith("err")


@needs_node
def test_a_refused_start_by_the_engine_reads_the_same_and_the_button_waits_while_another_run_goes_as_the_rows_does():
    out = run_workspace(_js(_SETUP + r"""
listed.tasks=[agentTask({id:'other',text:'Another task'})]; listed.tasks_revision='tasks-0';
setRuns([mkRun({id:'r-going',task_id:'other',state:'running',can_stop:true})]);
await window.ROWorkspace.load(root); await sleepTick();
await pickAtt(); await pickOwner('Claude Code · sonnet', '负责人'); typed(field('任务'),'Fit the model');
const run=find('保存并运行'), rowRun=find('运行',byClass('ws-agent'));
const waiting={disabled:run.disabled,title:run.title,rowDisabled:rowRun?rowRun.disabled:'none',save:find('添加任务').disabled};
// the run ends: the page learns it by its poll, and the button follows
runsList.runs[0].state='done'; runsList.active=null; await tick();
const free={disabled:find('保存并运行').disabled,title:find('保存并运行').title};
// the engine still refuses (another run began elsewhere): the same note and the same words
customPost=(path)=>{ if(path==='/api/agents/runs') { const e=new Error('busy'); e.engineMessage='另一个运行还在进行。'; throw e; } };
await find('保存并运行').click(); await sleepTick();
const last=byClass('ws-tasks').children[byClass('ws-tasks').children.length-1];
console.log(JSON.stringify({waiting,free,order:posted.map(p=>p[0]),status:byClass('ws-status',last).textContent,
  opened:root.all().filter(e=>e.classList.contains('ws-task-details')).map(d=>d.open)}));
"""))
    assert out["waiting"] == {"disabled": True, "title": "这个项目里还有一次运行没有结束。", "rowDisabled": "none", "save": False}
    assert out["free"] == {"disabled": False, "title": ""}
    assert out["order"][-2:] == ["/api/workspace/tasks", "/api/agents/runs"]
    assert out["status"] == "任务已保存，但运行没有开始。原因写在这项任务那一行里。"
    assert out["opened"] == [True, False]                          # the task that did not start shows why (it stands under its attempt, first); the other row is left shut


@needs_node
def test_an_existing_task_is_offered_save_and_run_only_when_its_row_would_offer_run_and_runs_after_the_edit_is_saved():
    out = run_workspace(_js(_SETUP + r"""
listed.attempts=[{id:ATT,label:'First check'}];
listed.tasks=[agentTask({id:'fresh',text:'Fit the model',attempt:ATT}),agentTask({id:'done',text:'Old task',attempt:ATT,done:true}),
              agentTask({id:'asked',text:'Which sample?',attempt:ATT})];
listed.tasks_revision='tasks-0';
setRuns([mkRun({id:'r-asked',task_id:'asked',state:'needs_input',question:'Which?',can_reply:true})]);
await window.ROWorkspace.load(root); await sleepTick();
const rows=()=>root.all().filter(e=>e.classList.contains('ws-task'));
const offered=async index=>{ await find('编辑',rows()[index]).click(); const names=formButtons(); await find('清空').click(); return names; };
const fresh=await offered(0), done=await offered(1), asked=await offered(2);
await find('编辑',rows()[0]).click();
typed(field('任务'),'Fit the model, twice');
// the save fails: the task as it was saved must not be run in its place
customPost=(path)=>{ if(path==='/api/workspace/tasks') throw new Error('disk full'); };
await find('保存并运行').click(); await sleepTick();
const failed={runs:runsPosted().length,questions:confirmQuestions.length,saved:listed.tasks[0].text,typed:field('任务').value,buttons:formButtons()};
customPost=null;
await find('保存并运行').click(); await sleepTick();
console.log(JSON.stringify({fresh,done,asked,failed,order:posted.map(p=>p[0]),saved:listed.tasks[0].text,runFor:runsPosted().map(p=>p[1].task),
  rowRows:rows().length}));
"""))
    assert out["fresh"] == ["保存任务", "保存并运行", "清空"]
    assert out["done"] == ["保存任务", "清空"]         # a task marked done is not run again from the row, nor from the form
    assert out["asked"] == ["保存任务", "清空"]        # a run waiting for the person's answer is answered, not started again
    assert out["failed"] == {"runs": 0, "questions": 0, "saved": "Fit the model", "typed": "Fit the model, twice",
                             "buttons": ["保存任务", "保存并运行", "清空"]}
    assert out["order"] == ["/api/workspace/tasks", "/api/workspace/tasks", "/api/agents/runs"] and out["saved"] == "Fit the model, twice"
    assert out["runFor"] == ["fresh"] and out["rowRows"] == 3


@needs_node
def test_the_english_form_has_no_chinese_in_it_with_an_agent_chosen_and_after_a_run_was_started():
    out = run_workspace(_js(_SETUP + r"""
listed.attempts=[{id:ATT,label:'First check'}]; listed.tasks=[]; listed.tasks_revision='tasks-0';
await window.ROWorkspace.load(root); await sleepTick();
await pickAtt(); await pickOwner('Claude Code · sonnet', T('负责人')); typed(field(T('任务',{n:1})),'Fit the model');
const before=byClass('ws-task-form').textContent;
await find(T('保存并运行')).click(); await sleepTick();
console.log(JSON.stringify({before,after:byClass('ws-tasks').textContent,select:field(T('负责人')).children.map(g=>g.label||g.textContent)}));
"""), lang="en")
    for key in ("before", "after"):
        assert not CJK.search(out[key]), out[key]
    assert out["select"] == ["Not assigned", "People", "AI agents on this Mac"]


@needs_node
def test_the_page_has_one_primary_button_at_a_time_save_while_a_document_is_being_written_and_add_task_otherwise():
    """21.8 (fix batch 3): 保存 of the writing area and 添加任务 of the task form are both primary buttons, and both stand on the
    one page. While the writing area is on screen in edit mode 保存 is the page's primary and the task form's save button is the
    quiet one; in preview, with nothing open, or with the plan folded (its writing area is in the fold) the task form's is."""
    out = run_workspace(_js(_SETUP + r"""
listed.attempts=[{id:ATT,label:'First check'}]; listed.tasks=[]; listed.tasks_revision='tasks-0';
const shown=e=>{ for (let n=e; n; n=n.parentNode) { if (n.classList && n.classList.contains('hidden')) return false;
  if (n.parentNode && n.parentNode.tagName==='DETAILS' && n.parentNode.open===false && n.tagName!=='SUMMARY') return false; } return true; };
const page=()=>root.all().filter(e=>e.tagName==='BUTTON'&&e.className.includes('btn-primary')&&shown(e)).map(e=>e.textContent);
const seen={};
await window.ROWorkspace.load(root); await sleepTick();
seen.start=page();                                                     // the plan is folded: nothing is being written
await find('新建笔记').click();
seen.note=page();                                                      // a new note starts in edit mode
await find('预览').click(); seen.preview=page();
await find('编辑').click(); seen.again=page();
const more=byClass('ws-plan-more');
more.open=true; await more.emit('toggle'); await sleepTick();         // the plan unfolds into the writing area (a new note was open: it is empty)
seen.plan=page();
await find('编辑').click(); seen.planEdit=page();
more.open=false; await more.emit('toggle'); await sleepTick(); seen.folded=page();
console.log(JSON.stringify(seen));
"""))
    add, save = "添加任务", "保存"
    assert out["start"] == [add] and out["note"] == [save] and out["preview"] == [add] and out["again"] == [save]
    assert out["plan"] == [add] and out["planEdit"] == [save] and out["folded"] == [add]


# -- the owner when nobody is named: the researcher can give a task to themselves (21.7) ----------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_in_a_project_of_one_person_with_no_name_set_the_researcher_is_among_the_people_and_the_task_is_written_to_them(lang):
    """A project of one person had an empty 人员 group: no one to give a task to. With no name set (no me.toml) the researcher is
    listed as 「我」 (“Me”) and chosen like a named person: the owner written to tasks.json is the text 「我」, whatever the
    screen's language is, and the row reads it in the screen's language."""
    w = _w(lang)
    out = run_workspace(_js(r"""
const T=window.ROI18N.t;
listed.mode='single'; listed.attempts=[{id:ATT,label:'First check'}]; listed.tasks=[]; listed.tasks_revision='tasks-0';
agentsInfo=agentFixture();
await window.ROWorkspace.load(root); await sleepTick();
const groupsOf=select=>select.children.filter(g=>g.tagName==='OPTGROUP').map(g=>[g.label,g.children.map(o=>[o.textContent,o.value])]);
const groups=groupsOf(field(T('负责人')));
await pickOwner(T('我'), T('负责人'));
const chosen=field(T('负责人')).value;
const s=field(T('属于哪条尝试')); s.value=ATT; await s.emit('change');
typed(field(T('任务',{n:1})),'Read the three papers'); await find(T('添加任务')).click(); await sleepTick();
const row=byClass('ws-task');
console.log(JSON.stringify({groups,chosen,saved:listed.tasks.map(t=>t.owner),shown:byClass('ws-task-facts',row)?byClass('ws-task-facts',row).textContent:row.textContent,gets:gets.filter(g=>g==='/api/share')}));
"""), lang=lang)
    assert out["groups"][0] == [w("人员", "People"), [[w("我", "Me"), "我"], [w("其他人…", "Someone else…"), "\u0000other"]]]
    assert out["chosen"] == "我" and out["saved"] == ["我"] and out["gets"] == []   # on disk: the one word, in Chinese, as a named person is written
    assert w("我", "Me") in out["shown"]


@needs_node
def test_with_a_name_set_the_researcher_is_listed_by_it_and_there_is_no_second_entry_for_them():
    """me.toml names the researcher: 「Lin Ying（我）」 as before, written as the name; no separate 「我」 beside it."""
    out = run_workspace(_js(r"""
listed.mode='single'; meInfo={state:'present',me:{id:'m-lin',name:'Lin Ying',email:'l@x.org'}};
await window.ROWorkspace.load(root); await sleepTick();
const people=flatOptions(field('负责人')).map(o=>[o.textContent,o.value]);
console.log(JSON.stringify({people}));
"""))
    assert out["people"] == [["不指定", ""], ["Lin Ying（我）", "Lin Ying"], ["其他人…", "\u0000other"]]

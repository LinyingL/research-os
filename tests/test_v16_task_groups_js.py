"""Research OS V16, task B1-2 (DESIGN.md 21.1 'Grouping the tasks a project already has'), the page.

On 项目首页, 「把这些任务归入尝试…」 sits in the task header and under 「未归入尝试」 and opens a side panel of the groups the
engine proposes: each can be renamed (with its number and date), have tasks moved in or out, be merged with another or
dropped, or be confirmed -- and nothing is written until one is. A duplicate says 「和第 n 条一样」 and is removed by the
task list's own removal, with its question. The page runs under node on the stand-in DOM of the tests that already drive
it, fed what the real engine proposes for a project shaped like Quant's, in both languages. Every test names its scenario."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos import task_groups, workspace
from test_v16_task_groups import TODAY, _legacy, root  # noqa: F401 -- `root` is the fixture of the engine tests
from test_v8_phase2 import _project
from test_v9_language import CJK, needs_node
from test_workspace_js import run

NODE_ID = "attempt:map.md#18"


def _page(root: Path, extra: str = "", tasks: list[dict] | None = None) -> str:
    """The first lines of a scenario: the project as the engine reads it, and the answers the stand-in API gives to the
    two calls of this page -- the proposal, and one group confirmed (what `task_groups.confirm` answers)."""
    plan = task_groups.propose(root, today=TODAY)
    listed = tasks if tasks is not None else workspace.read_tasks(root)[0]
    return f"""
const PLAN={json.dumps(plan, ensure_ascii=False)};
listed.attempts={json.dumps(workspace._attempts(root), ensure_ascii=False)};
listed.tasks={json.dumps(listed, ensure_ascii=False)}; listed.tasks_revision='tasks-1';
let table=PLAN.table, failWith=null;
customGet=(path)=>path.startsWith('/api/workspace/task-groups')?clone(PLAN):undefined;
customPost=(path,body)=>{{
  if(path==='/api/attempts/initialize') {{ table=true; return {{created:true,file:'research/attempts.md'}}; }}
  if(path!=='/api/workspace/task-groups') return undefined;
  if(failWith) {{ const e=new Error(failWith.message); e.state=failWith.state; e.engineMessage=failWith.message; throw e; }}
  const id='attempt:map.md#'+body.attempt.number;
  listed.tasks=listed.tasks.map(t=>body.tasks.includes(t.id)?Object.assign({{}},t,{{attempt:id}}):t); listed.tasks_revision='tasks-'+posted.length;
  return {{attempt:{{id,number:body.attempt.number,label:body.attempt.description,file:'map.md',date:body.attempt.date}},moved:body.tasks,linked:true,
    tasks:clone(listed.tasks),tasks_revision:listed.tasks_revision,backup:null,ingest_error:null,history_error:null,changed_task_reports:[],mode:listed.mode}};
}};
{extra}
"""


_CARDS = """
const cards=()=>panelBody.all().filter(e=>e.classList.contains('ws-group'));
const cardOf=(n)=>cards()[n];
const text=(card,key)=>card.all().find(e=>e.dataset&&e.dataset.key===key);
const rowsOf=(card)=>card.all().filter(e=>e.classList.contains('ws-group-task')).map(r=>r.all().find(e=>e.classList.contains('ws-group-task-text')).textContent);
const heads=()=>byClass('ws-task-list').children.filter(e=>e.tagName==='H3').map(e=>e.textContent);
"""


def _run(root, scenario: str, **options) -> dict:
    return run(_page(root, options.pop("extra", ""), options.pop("tasks", None)) + _CARDS + scenario, **options)


# -- where the page offers it ---------------------------------------------------------------------------------------


@needs_node
def test_the_page_offers_grouping_in_the_task_header_and_under_the_tasks_in_no_attempt_and_nowhere_else(root):
    out = _run(root, r"""
await window.ROWorkspace.load(root);
const where=()=>byClass('ws-tasks').all().filter(e=>e.tagName==='BUTTON'&&e.textContent==='把这些任务归入尝试…');
const loose=[where().map(b=>b.dataset.key), byClass('ws-task-list').children.filter(e=>e.tagName==='BUTTON').length,
  byClass('ws-task-list').children.map(e=>e.tagName==='H3'?'#':e.tagName==='BUTTON'?'button':'row').join('')];
listed.tasks=listed.tasks.map(t=>Object.assign({},t,{attempt:'attempt:map.md#16'})); await window.ROWorkspace.load(root);
const none=where().length;
listed.tasks=[]; await window.ROWorkspace.load(root);
console.log(JSON.stringify({loose,none,empty:where().length,posted:posted.length}));
""")
    # in the header, and right under the heading of the group of tasks in no attempt, which is last
    assert out["loose"][0] == ["group-tasks", "group-tasks-loose"] and out["loose"][1] == 1 and out["loose"][2].endswith("#button" + "row" * 11)
    assert out["none"] == 0 and out["empty"] == 0 and out["posted"] == 0


@needs_node
def test_a_project_that_cannot_be_written_is_not_offered_a_working_grouping(root):
    out = _run(root, r"""
listed.read_only=true; await window.ROWorkspace.load(root);
const buttons=byClass('ws-tasks').all().filter(e=>e.tagName==='BUTTON'&&e.textContent==='把这些任务归入尝试…');
await buttons[0].click();
console.log(JSON.stringify({disabled:buttons.map(b=>b.disabled),panel:panelBody.textContent,reads:gets.filter(p=>p.startsWith('/api/workspace/task-groups')).length}));
""")
    assert out == {"disabled": [True, True], "panel": "", "reads": 0}


# -- the panel ------------------------------------------------------------------------------------------------------


@needs_node
def test_the_panel_lists_the_proposed_groups_with_their_prefilled_attempt_their_tasks_and_the_duplicates_marked(root):
    out = _run(root, r"""
await window.ROWorkspace.load(root);
await find('把这些任务归入尝试…').click();
const card=(n)=>({number:text(cardOf(n),'group-number:'+PLAN.groups[n].id).value, date:text(cardOf(n),'group-date:'+PLAN.groups[n].id).value,
  name:text(cardOf(n),'group-name:'+PLAN.groups[n].id).value, rows:rowsOf(cardOf(n)),
  notes:cardOf(n).all().filter(e=>e.classList.contains('ws-agent-quiet')).map(e=>e.textContent)});
const dup=panelBody.all().filter(e=>e.classList.contains('ws-group-task')&&e.textContent.includes('和第 4 条一样')).map(r=>r.all().filter(e=>e.tagName==='BUTTON').map(b=>b.textContent));
console.log(JSON.stringify({title:panelTitle.textContent, intro:panelBody.all().find(e=>e.classList.contains('ws-agent-quiet')).textContent,
  groups:PLAN.groups.map((g,n)=>card(n)), pool:rowsOf(cards()[cards().length-1]), pooled:cards()[cards().length-1].all().find(e=>e.tagName==='H3').textContent,
  dup, nums:panelBody.all().filter(e=>e.classList.contains('ws-group-task-n')).map(e=>e.textContent).join(' '),
  posted:posted.length, dirty:window.ROWorkspace.isDirty(), subject:state.panelSubject&&state.panelSubject.kind}));
""")
    assert out["title"] == "把这些任务归入尝试" and "文件不会移动" in out["intro"] and out["posted"] == 0 and out["dirty"] is False
    assert out["subject"] == "task-groups"
    assert [(g["number"], g["date"], g["name"]) for g in out["groups"]] == [
        ("17", "08-02", "Sword v0 基线：用全部成员跑一遍回测，输出收益表"), ("18", "09-01", "Sword v0.2 全成员复算，核对"),
        ("19", "10-05", "Sword v0.3 新增行业中性约束"), ("20", "10-29", "美股历史数据源")]
    assert [len(g["rows"]) for g in out["groups"]] == [1, 3, 2, 2]
    assert out["groups"][1]["rows"][2].startswith("对上一份审查报告给出回应")
    assert out["groups"][1]["notes"][:2] == ["共同点：Sword v0.2", "研究判断：待定，结果先留空"]
    assert out["pooled"] == "没有分到组的任务" and len(out["pool"]) == 3
    assert out["dup"] == [["移除"], ["移除"]]  # t5 and t6 are the same as task 4
    assert "第 1 条" in out["nums"] and "第 11 条" in out["nums"]


@needs_node
def test_the_panel_in_english_says_everything_in_english_and_keeps_the_researchers_own_words(tmp_path):
    root = _project(tmp_path / "proj")
    _legacy(root, [{"id": "a", "text": "Sword v0.2 recheck of returns"}, {"id": "b", "text": "Sword v0.2 recheck of Sharpe"},
                   {"id": "c", "text": "Respond to the review"}, {"id": "d", "text": "Respond to the review"},
                   {"id": "e", "text": "Compare the Tiingo vendor feed"}, {"id": "f", "text": "Ask about Tiingo vendor prices"},
                   {"id": "g", "text": "Plan the next meeting"}])
    out = _run(root, r"""
const T=window.ROI18N.t;
await window.ROWorkspace.load(root);
await find(T('把这些任务归入尝试…')).click();
console.log(JSON.stringify({title:panelTitle.textContent, all:panelBody.textContent,
  buttons:buttonsIn(panelBody), groups:PLAN.groups.map(g=>g.id), vendor:rowsOf(cardOf(1))}));
""", lang="en")
    assert out["title"] == "Put these tasks into attempts" and out["groups"] == ["v:sword v0.2", "t:tiingo"]
    assert out["vendor"] == ["Compare the Tiingo vendor feed", "Ask about Tiingo vendor prices"]
    text = out["all"]
    assert not CJK.search(text), text
    for words in ("What they share: Sword v0.2", "Research assessment: Pending; result left empty for now", "Task 1", "Same as task 3",
                  "Tasks in no group", "Merge into…", "Drop this group", "Confirm this group", "Move to…", "Attempt description", "Number"):
        assert words in text, words
    assert out["buttons"].count("Remove") == 1 and out["buttons"].count("Confirm this group") == len(out["groups"])


@needs_node
def test_renaming_moving_merging_and_dropping_write_nothing_and_leaving_the_panel_after_a_change_asks_first(root):
    out = _run(root, r"""
await window.ROWorkspace.load(root);
await find('把这些任务归入尝试…').click();
const ids=PLAN.groups.map(g=>g.id);
const calm={posted:posted.length, dirty:window.ROWorkspace.isDirty(), leave:window.ROWorkspace.mayLeavePanel()};
// rename, renumber, redate
typed(text(cardOf(1),'group-name:'+ids[1]),'Sword v0.2 全成员复算'); typed(text(cardOf(1),'group-number:'+ids[1]),'30'); typed(text(cardOf(1),'group-date:'+ids[1]),'09-02');
confirmQuestions.length=0; answers.push(false);
const asked={leave:window.ROWorkspace.mayLeavePanel(), questions:confirmQuestions.slice()};
// move t3 out of v0.2 into v0.3 (a select on the row), then t2 into no group
const move=(id)=>panelBody.all().find(e=>e.dataset&&e.dataset.key==='group-move:'+id);
move('t3').value=ids[2]; await move('t3').emit('change');
move('t2').value='\u0000none'; await move('t2').emit('change');
const moved={g1:rowsOf(cardOf(1)).length, g2:rowsOf(cardOf(2)).length, pool:rowsOf(cards()[cards().length-1]).length, names:cards().map(c=>c.all().find(e=>e.dataset&&String(e.dataset.key).startsWith('group-name:')))};
// the pool's task goes into a group from the pool's own select
move('t2').value=ids[3]; await move('t2').emit('change');
const last=rowsOf(cardOf(3)).length;
// merge v0.3 (two tasks now three) into v0 : the date of the earlier stays, the tasks are in the order of the list
const merge=panelBody.all().find(e=>e.dataset&&e.dataset.key==='group-merge:'+ids[2]);
merge.value=ids[0]; await merge.emit('change');
const merged={count:cards().length-(cards().some(c=>c.all().some(e=>e.tagName==='H3'))?1:0), date:text(cardOf(0),'group-date:'+ids[0]).value, rows:rowsOf(cardOf(0)).length,
  options:optionsOf(panelBody.all().find(e=>e.dataset&&e.dataset.key==='group-merge:'+ids[0]))};
// drop the data-source group: its tasks go to the pool
const drop=panelBody.all().find(e=>e.dataset&&e.dataset.key==='group-drop:'+ids[3]); await drop.click();
const dropped={groups:cards().filter(c=>c.all().every(e=>e.tagName!=='H3')).length, pool:rowsOf(cards()[cards().length-1]).length};
console.log(JSON.stringify({calm,asked,moved:{g1:moved.g1,g2:moved.g2,pool:moved.pool},last,merged,dropped,posted:posted.length,dirty:window.ROWorkspace.isDirty()}));
""")
    assert out["calm"] == {"posted": 0, "dirty": False, "leave": True}
    assert out["asked"]["leave"] is False and out["asked"]["questions"] == ["有未保存的内容，仍要离开吗？"]
    assert out["moved"] == {"g1": 1, "g2": 3, "pool": 4}  # t4 stays in v0.2; v0.3 has t3 as well; t2 and the three leftovers wait
    assert out["last"] == 3  # the data-source group took t2 from the pool
    assert out["merged"]["count"] == 3 and out["merged"]["date"] == "08-02" and out["merged"]["rows"] == 4
    assert [o[0] for o in out["merged"]["options"]] == ["合并到…", "Sword v0.2 全成员复算", "美股历史数据源"]
    assert out["dropped"] == {"groups": 2, "pool": 6} and out["posted"] == 0 and out["dirty"] is True  # nothing was written; the changes are unsaved


@needs_node
def test_a_group_with_no_task_left_cannot_be_confirmed_and_one_without_a_number_or_a_description_is_not_sent(root):
    out = _run(root, r"""
await window.ROWorkspace.load(root);
await find('把这些任务归入尝试…').click();
const ids=PLAN.groups.map(g=>g.id);
const confirmOf=(n)=>text(cardOf(n),'group-confirm:'+ids[n]);
typed(text(cardOf(0),'group-name:'+ids[0]),'   ');
await confirmOf(0).click();
const refused={posted:posted.length, says:cardOf(0).all().filter(e=>e.classList.contains('ws-status')).map(e=>e.textContent)};
const move=(id)=>panelBody.all().find(e=>e.dataset&&e.dataset.key==='group-move:'+id);
move('t1').value='\u0000none'; await move('t1').emit('change');
console.log(JSON.stringify({refused, emptyDisabled:confirmOf(0).disabled, empty:cardOf(0).all().filter(e=>e.classList.contains('ws-agent-quiet')).map(e=>e.textContent).includes('这一组还没有任务。')}));
""")
    assert out["refused"] == {"posted": 0, "says": ["请写下这条尝试的编号和描述。"]}
    assert out["emptyDisabled"] is True and out["empty"] is True


# -- confirming -----------------------------------------------------------------------------------------------------


@needs_node
def test_confirming_a_group_sends_its_row_and_tasks_once_and_the_home_shows_the_attempt_with_its_tasks_under_it(root):
    out = _run(root, r"""
await window.ROWorkspace.load(root);
await find('把这些任务归入尝试…').click();
const ids=PLAN.groups.map(g=>g.id);
typed(text(cardOf(1),'group-name:'+ids[1]),'Sword v0.2 全成员复算');
await text(cardOf(1),'group-confirm:'+ids[1]).click(); await sleepTick();
const sent=posted.filter(p=>p[0]==='/api/workspace/task-groups');
const line=panelBody.all().filter(e=>e.classList.contains('ws-group-done')).map(e=>e.textContent);
console.log(JSON.stringify({sent, line, others:posted.length, groups:cards().length,
  heads:heads(), homeRows:byClass('ws-task-list').children.filter(e=>e.tagName==='DIV').length, dirty:window.ROWorkspace.isDirty(),}));
""")
    assert out["sent"] == [["/api/workspace/task-groups", {"project": "/projects/study", "tasks": ["t2", "t3", "t4"],
                                                              "attempt": {"number": "18", "date": "09-01", "description": "Sword v0.2 全成员复算"}}]]
    assert out["line"] == ["已新增尝试 #18，3 个任务归入了它。"] and out["others"] == 1
    assert out["groups"] == 4  # the other three groups and the card of the tasks in no group stay on offer
    assert out["heads"] == ["#18 Sword v0.2 全成员复算", "未归入尝试"]  # the attempt stands first, with its tasks, the rest under 未归入尝试
    assert out["homeRows"] == 11 and out["dirty"] is False


@needs_node
def test_a_confirmed_group_in_english_says_so_in_english_and_the_task_list_names_the_new_attempt(tmp_path):
    root = _project(tmp_path / "proj")
    _legacy(root, [{"id": "a", "text": "Sword v0.2 recheck of returns"}, {"id": "b", "text": "Sword v0.2 recheck of Sharpe"}, {"id": "c", "text": "Plan the meeting"}])
    out = _run(root, r"""
const T=window.ROI18N.t;
await window.ROWorkspace.load(root);
await find(T('把这些任务归入尝试…')).click();
await text(cardOf(0),'group-confirm:'+PLAN.groups[0].id).click(); await sleepTick();
console.log(JSON.stringify({line:panelBody.all().filter(e=>e.classList.contains('ws-group-done')).map(e=>e.textContent), heads:heads(),
  rest:panelBody.all().filter(e=>e.tagName==='H3').map(e=>e.textContent), sent:posted.map(p=>p[0])}));
""", lang="en")
    assert out["line"] == ["Added attempt #17; 2 tasks are now in it."]
    assert out["heads"] == ["#17 Sword v0.2 recheck", "Not in an attempt"] and out["rest"] == ["Tasks in no group"] and out["sent"] == ["/api/workspace/task-groups"]
    assert not CJK.search(json.dumps(out["line"] + out["rest"]))


@needs_node
def test_a_project_with_no_attempt_table_has_it_made_before_the_first_group_is_sent_and_only_then(root):
    out = _run(root, r"""
PLAN.table=false;
await window.ROWorkspace.load(root);
await find('把这些任务归入尝试…').click();
await text(cardOf(0),'group-confirm:'+PLAN.groups[0].id).click(); await sleepTick();
await text(cardOf(0),'group-confirm:'+PLAN.groups[1].id).click(); await sleepTick();
console.log(JSON.stringify({sent:posted.map(p=>p[0])}));
""")
    assert out["sent"] == ["/api/attempts/initialize", "/api/workspace/task-groups", "/api/workspace/task-groups"]


@needs_node
def test_a_refused_group_stays_on_offer_with_the_engines_sentence_and_everything_the_person_typed(root):
    out = _run(root, r"""
await window.ROWorkspace.load(root);
await find('把这些任务归入尝试…').click();
const id=PLAN.groups[1].id;
typed(text(cardOf(1),'group-name:'+id),'Sword v0.2 全成员复算');
failWith={state:'workspace_group_duplicate', message:'这个编号已经在年表里了，请换一个编号。什么都没有改。'};
await text(cardOf(1),'group-confirm:'+id).click(); await sleepTick();
const refused={says:cardOf(1).all().filter(e=>e.classList.contains('ws-status')).map(e=>[e.className,e.textContent]), name:text(cardOf(1),'group-name:'+id).value,
  locked:text(cardOf(1),'group-confirm:'+id).disabled, tasks:listed.tasks.filter(t=>t.attempt).length, groups:PLAN.groups.length, shown:cards().length};
failWith=null; typed(text(cardOf(1),'group-number:'+id),'31');
await text(cardOf(1),'group-confirm:'+id).click(); await sleepTick();
console.log(JSON.stringify({refused, sent:posted.filter(p=>p[0]==='/api/workspace/task-groups').map(p=>p[1].attempt), done:listed.tasks.filter(t=>t.attempt).map(t=>t.id)}));
""")
    assert out["refused"]["says"] == [["ws-status err", "没能归入这一组。 这个编号已经在年表里了，请换一个编号。什么都没有改。"]]
    assert out["refused"]["name"] == "Sword v0.2 全成员复算" and out["refused"]["locked"] is False and out["refused"]["tasks"] == 0
    assert out["sent"][-1] == {"number": "31", "date": "09-01", "description": "Sword v0.2 全成员复算"} and out["done"] == ["t2", "t3", "t4"]


@needs_node
def test_a_group_written_but_not_read_back_says_the_tasks_are_not_in_it_yet_and_where_to_choose_it(root):
    out = _run(root, r"""
customPost=((was)=>(path,body)=>path==='/api/workspace/task-groups'?{attempt:{id:'attempt:map.md#17',number:'17',label:'x',file:'map.md',date:'08-02'},moved:[],linked:false,
  tasks:clone(listed.tasks),tasks_revision:listed.tasks_revision,ingest_error:'disk is full',mode:'single'}:was(path,body))(customPost);
await window.ROWorkspace.load(root);
await find('把这些任务归入尝试…').click();
await text(cardOf(0),'group-confirm:'+PLAN.groups[0].id).click(); await sleepTick();
console.log(JSON.stringify({line:panelBody.all().filter(e=>e.classList.contains('ws-group-done')).map(e=>[e.all()[0].className,e.textContent]), unmoved:listed.tasks.filter(t=>t.attempt).length}));
""")
    assert out["line"] == [["ws-status err", "尝试 #17 已经写进尝试表，但没能重新读取它，任务还没有归入。稍后在任务的「属于哪条尝试」里选这一条。"]]
    assert out["unmoved"] == 0


@needs_node
def test_a_members_group_is_written_into_the_working_copy_and_the_proposal_opens_with_the_members_words(root):
    out = _run(root, r"""
listed.mode='member';
const composed=[]; global.ROTeam={composeInto:async(host,body,status)=>{ composed.push(body); host.append('PROPOSAL'); return {}; }};
await window.ROWorkspace.load(root);
await find('把这些任务归入尝试…').click();
const id=PLAN.groups[1].id, sentence=text(cardOf(1),'group-sentence:'+id);
const before={sentence:sentence.value, member:panelBody.textContent.includes('你是这个项目的成员')};
typed(sentence,'Put the v0.2 tasks under one attempt');
await text(cardOf(1),'group-confirm:'+id).click(); await sleepTick();
console.log(JSON.stringify({before, composed, line:panelBody.all().filter(e=>e.classList.contains('ws-group-done')).map(e=>e.textContent), sent:posted.map(p=>p[0])}));
""")
    assert out["before"] == {"sentence": "把已有的任务归入新尝试 #18", "member": True}
    assert out["composed"] == [{"kind": "attempt_new", "sentence": "Put the v0.2 tasks under one attempt", "number": "18"}]
    assert out["line"][0].startswith("已写进你的工作副本：尝试 #18，3 个任务归入了它。提议还没有发送。") and out["line"][0].endswith("PROPOSAL")
    assert out["sent"] == ["/api/workspace/task-groups"]


# -- the duplicates -------------------------------------------------------------------------------------------------


@needs_node
def test_removing_a_duplicate_asks_the_task_lists_question_and_a_no_removes_nothing(root):
    out = _run(root, r"""
await window.ROWorkspace.load(root);
await find('把这些任务归入尝试…').click();
const remove=(id)=>panelBody.all().find(e=>e.dataset&&e.dataset.key==='group-remove:'+id);
const has=(id)=>!!remove(id);
const before={t5:has('t5'), t6:has('t6'), t4:has('t4'), t1:has('t1')};
confirmQuestions.length=0; answers.push(false);
await remove('t6').click(); await sleepTick();
const declined={questions:confirmQuestions.slice(), posted:posted.length, still:has('t6'), tasks:listed.tasks.length};
answers.push(true);
await remove('t6').click(); await sleepTick();
console.log(JSON.stringify({before, declined, after:{sent:posted.map(p=>[p[0],p[1].tasks.length]), gone:has('t6'), tasks:listed.tasks.map(t=>t.id),
  pool:rowsOf(cards()[cards().length-1]).length, home:byClass('ws-task-list').all().filter(e=>e.classList.contains('ws-task')).length,
  other:has('t5')}}));
""")
    assert out["before"] == {"t5": True, "t6": True, "t4": False, "t1": False}  # only the later copies carry 「和第 n 条一样」
    assert out["declined"] == {"questions": ["移除这项任务吗？"], "posted": 0, "still": True, "tasks": 11}
    assert out["after"]["sent"] == [["/api/workspace/tasks", 10]] and out["after"]["gone"] is False and "t6" not in out["after"]["tasks"]
    assert out["after"]["pool"] == 2 and out["after"]["home"] == 10 and out["after"]["other"] is True


@needs_node
def test_a_removal_that_fails_leaves_the_duplicate_in_the_panel(root):
    out = _run(root, r"""
await window.ROWorkspace.load(root);
await find('把这些任务归入尝试…').click();
const remove=(id)=>panelBody.all().find(e=>e.dataset&&e.dataset.key==='group-remove:'+id);
const was=customPost; customPost=(path,body)=>{ if(path==='/api/workspace/tasks') throw Object.assign(new Error('boom'),{state:'workspace_write_failed'}); return was(path,body); };
answers.push(true); await remove('t6').click(); await sleepTick();
console.log(JSON.stringify({still:!!remove('t6'), tasks:listed.tasks.length}));
""")
    assert out == {"still": True, "tasks": 11}


# -- the reads that fail --------------------------------------------------------------------------------------------


@needs_node
def test_a_proposal_that_cannot_be_read_says_so_in_the_panel_and_a_project_with_nothing_to_group_says_that(root):
    out = _run(root, r"""
customGet=(path)=>path.startsWith('/api/workspace/task-groups')?Promise.reject(Object.assign(new Error('x'),{engineMessage:'任务文件暂时无法读取'})):undefined;
await window.ROWorkspace.load(root);
await find('把这些任务归入尝试…').click(); await sleepTick();
const failed=panelBody.textContent;
customGet=(path)=>path.startsWith('/api/workspace/task-groups')?{tasks_revision:null,table:true,tasks:[],groups:[],ungrouped:[],duplicates:[]}:undefined;
await find('把这些任务归入尝试…').click(); await sleepTick();
console.log(JSON.stringify({failed, empty:panelBody.textContent}));
""")
    assert out["failed"] == "没能读取任务的分组。 任务文件暂时无法读取"
    assert "没有可以归入尝试的任务。" in out["empty"]

"""Research OS V16, task B1-3 (DESIGN.md 21.2 'The handback'), the pages.

An agent's handback opens in the side panel -- from 项目首页's task row, the attempt's panel and the route map's card --
in this order: the headline, the key numbers each with its file (a number its file does not hold is marked), what is
undone, what the researcher must decide, the agent's suggested assessment with 「确认这个判断」, 「改一下」 and
「先不判断」, the outputs by kind, the report folded, the run log. Opening marks it opened for this person; confirming
writes the assessment and says who, and what comes after. The scripts run under node on the stand-in DOMs of the
tests that already drive them, fed what the real engine answers for a real handback, in both languages. Every test
names its scenario."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos import handbacks
from test_v16_handback import A16, A17, FULL, PERSON, hand_back, me, pid, root  # noqa: F401 -- fixtures of the engine tests
from test_v9_language import CJK, needs_node
from test_workspace_js import run

@pytest.fixture
def report(root, pid):
    for folder in ("figures", "code", "tables"):
        (root / folder).mkdir(exist_ok=True)
    (root / "figures" / "sharpe.png").write_bytes(b"\x89PNG fig")
    (root / "code" / "recheck.py").write_text("print(1.82)\n")
    (root / "tables" / "t.csv").write_text("a,b\n1,2\n")
    ctx = {**FULL, "key_numbers": [*FULL["key_numbers"], {"what": "Alpha", "value": "9.99", "file": "results/sharpe.csv"},
                                   {"what": "Beta", "value": "0.5", "file": "results/gone.csv"}]}
    return hand_back(root, pid, context=ctx, report_text="Ran the recheck.\n\n- Sharpe: 1.82\n- Members: 12", outputs=[
        {"kind": "code", "path": "code/recheck.py", "title": "Recheck script"},
        {"kind": "figure", "path": "figures/sharpe.png", "title": "Sharpe by member"},
        {"kind": "table", "path": "tables/t.csv", "title": "Table 1"}])


def _page(root: Path, pid: str, report: str, extra: str = "", **view) -> str:
    """The first lines of a scenario: the handback as the real engine reads it, and the answers the stand-in API gives to
    the three calls of this page -- the handback, opened, and a confirmation."""
    got = handbacks.view(root, pid, PERSON, report, "zh")
    got.update(view)
    return f"""
const REPORT={json.dumps(report)}; let VIEW={json.dumps(got, ensure_ascii=False)};
global.apiGet=((real)=>async(path)=>{{ if(!path.startsWith('/api/workspace/handback?')) return real(path); gets.push(path); if(handbackDown) throw Object.assign(new Error('gone'),{{engineMessage:'no such report'}}); return clone(VIEW); }})(global.apiGet);
const opened=[], composed=[], closed=[], started=[], attempts=[], logs=[], editing=[], refreshed=[], files=[];
let refuse=null, refuseState=null, handbackDown=false;
global.closePanel=()=>{{ closed.push(1); panel.classList.add('hidden'); state.panelSubject=null; }};
global.startTaskOnHome=(c)=>started.push(c); global.openResultOnHome=(p)=>editing.push(p); global.openRunLogOnHome=(id)=>logs.push(id);
global.refreshCurrentView=()=>refreshed.push(1); global.openFilePanel=(p)=>files.push(p);
global.verdictTone=(s)=>({{current:'olive',abandoned:'gray',pending:'ochre',adverse:'clay'}})[s.status]||'plain';
global.ROTeam={{composeInto:async(host,draft,says)=>{{ composed.push(draft); host.append('proposal'); return true; }}}};
window.ROWorkspace.onCreateAttempt=(prefill,cb)=>attempts.push(prefill);
customPost=(path,body)=>{{
  if(path==='/api/workspace/handback/opened') {{ opened.push(body); return {{report:body.report,opened:true}}; }}
  if(path!=='/api/workspace/handback/confirm') return undefined;
  if(refuse) {{ const e=new Error(refuse); e.state=refuseState||'workspace_handback_refused'; e.engineMessage=refuse; throw e; }}
  const adopted=!!VIEW.suggested&&VIEW.suggested.status===body.status&&VIEW.suggested.reason===body.reason;
  const at='2026-10-30T09:00:00+01:00';
  return {{written:true,adopted,confirmed:{{by:'{PERSON}',at,status:body.status,adopted}},by_label:'张三',number:'16',node:{json.dumps(A16)},mode:VIEW.mode,
    sentence:{{zh:adopted?'由 张三 确认（采纳 Claude Code · sonnet 的建议）':'由 张三 确认',en:adopted?'Confirmed by 张三 (adopted the suggestion of Claude Code · sonnet)':'Confirmed by 张三'}},
    file:'map.md',backup:'b',ingest_error:null,history_error:null,context_error:null,decision:null,cell:'x',
    before:{{was:VIEW.attempt?VIEW.attempt.verdict:'',was_text:VIEW.attempt?VIEW.attempt.verdict:'',now_text:'x'}},context_path:REPORT.replace('.md','.context.json')}};
}};
{extra}
"""


_HELPERS = r"""
const panelText=()=>panelBody.textContent;
const hb=()=>panelBody.all().find(e=>e.classList.contains('ws-hb'));
const parts=()=>hb().children.map(e=>e.classList.contains('ws-hb-part')?'part:'+(e.children[0]&&e.children[0].classList.contains('ws-hb-title')?e.children[0].textContent:'?'):e.tagName==='DETAILS'?'details:report':e.classList.contains('ws-hb-headline')?'headline':e.tagName==='BUTTON'?'button:'+e.textContent:e.classList.contains('ws-hb-next')?'next':'other:'+e.className);
const pbuttons=()=>buttonsIn(panelBody);
const pbtn=(label)=>panelBody.all().find(e=>e.tagName==='BUTTON'&&e.textContent===label);
const key=(k)=>panelBody.all().find(e=>e.dataset&&e.dataset.key===k);
const cls=(c,where=panelBody)=>where.all().filter(e=>e.classList.contains(c));
const pick=(word)=>cls('status-pick').find(b=>b.textContent===word);
"""


def _run(root, pid, report, scenario: str, *, lang="zh", extra="", **view) -> dict:
    return run(_page(root, pid, report, extra, **view) + _HELPERS + scenario, lang=lang)


# -- the panel ------------------------------------------------------------------------------------------------------


@needs_node
def test_the_handback_opens_in_the_panel_in_the_order_of_21_2_with_each_part_as_the_engine_gave_it(root, pid, report):
    out = _run(root, pid, report, r"""
const T=window.ROI18N.t;
await window.ROWorkspace.openHandback(REPORT);
const num=cls('ws-hb-number').map(r=>({what:r.children[0].textContent,value:r.children[1].textContent,file:r.children[2].textContent,flag:r.children.slice(3).map(c=>c.textContent)}));
const outs=cls('ws-hb-outputs').map(g=>({kind:g.children[0].textContent,rows:cls('ws-hb-output',g).map(li=>({name:li.children[0].textContent,path:li.children.filter(c=>c.tagName==='CODE').map(c=>c.textContent),img:cls('ws-output-figure',li).map(i=>i.src)}))}));
console.log(JSON.stringify({title:panelTitle.textContent,subtitle:panelSubtitle.textContent,parts:parts(),headline:cls('ws-hb-headline')[0].textContent,num,
  undone:cls('ws-hb-text')[0].textContent,decide:cls('ws-hb-decide')[0].children.map(c=>c.textContent),
  chip:cls('status-label',cls('ws-hb-suggested')[0])[0].textContent+'|'+cls('status-label',cls('ws-hb-suggested')[0])[0].className, reason:cls('ws-hb-reason')[0].textContent, buttons:pbuttons(),
  primary:panelBody.all().filter(e=>e.tagName==='BUTTON'&&e.className.includes('btn-primary')).map(e=>e.textContent),
  outs, report:{open:!!cls('ws-hb-report')[0].open, text:cls('ws-preview',cls('ws-hb-report')[0])[0].textContent, folded:cls('ws-hb-report')[0].tagName},
  subject:state.panelSubject&&state.panelSubject.kind, dirty:window.ROWorkspace.isDirty(), got:gets.filter(p=>p.startsWith('/api/workspace/handback?'))}));
""")
    assert out["title"] == "Recheck" and out["subtitle"].startswith("Claude Code · sonnet · 张三 指派 · ")   # the agent and model, once, at the top
    assert out["parts"] == ["headline", "part:关键数字", "part:没做完的", "part:需要你决定的", "part:尝试 #16：TopicShift volatility (16-17)", "part:代理建议",
                            "part:输出", "details:report", "button:运行日志"]   # the attempt it belongs to, with its assessment now, stands above the suggestion
    assert out["headline"] == FULL["headline"]
    assert [(n["what"], n["value"], n["file"]) for n in out["num"]] == [
        ("Sharpe ratio", "1.82", "results/sharpe.csv"), ("Members", "12", "results/sharpe.csv"),
        ("Alpha", "9.99", "results/sharpe.csv"), ("Beta", "0.5", "results/gone.csv")]
    assert [n["flag"] for n in out["num"]] == [[], [], ["在所引文件里没找到这个数"], ["所引文件读不了，没法核对"]]   # found, found, missing, unreadable
    assert out["undone"] == FULL["undone"] and out["decide"] == FULL["decide"]
    assert out["chip"] == "现行|status-label tone-olive" and out["reason"] == "Holds on every member"
    assert out["buttons"].count("确认这个判断") == 1 and out["buttons"][out["buttons"].index("确认这个判断"):][:3] == ["确认这个判断", "改一下", "先不判断"]
    assert out["primary"] == ["确认这个判断"]                    # one primary action in the panel
    assert [(g["kind"], [r["name"] for r in g["rows"]]) for g in out["outs"]] == [("图", ["Sharpe by member"]), ("表", ["Table 1"]), ("代码", ["Recheck script"])]
    assert out["outs"][0]["rows"][0]["path"] == ["figures/sharpe.png"] and out["outs"][0]["rows"][0]["img"] == ["/api/workspace/asset?path=figures%2Fsharpe.png"]
    assert out["outs"][2]["rows"][0]["img"] == [] and out["outs"][2]["rows"][0]["path"] == ["code/recheck.py"]
    assert out["report"]["open"] is False and out["report"]["folded"] == "DETAILS" and "Sharpe: 1.82" in out["report"]["text"]
    assert out["subject"] == "handback" and out["dirty"] is False and len(out["got"]) == 1


@needs_node
def test_a_handback_in_english_says_every_word_in_english_and_keeps_the_agents_own_words(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({parts:parts(), buttons:pbuttons(), all:panelBody.textContent.replace(cls("ws-hb-report")[0].textContent, ""), subtitle:panelSubtitle.textContent,
  flags:cls('ws-hb-flag').map(e=>e.textContent), chip:cls('status-label',cls('ws-hb-suggested')[0])[0].textContent, kinds:cls('ws-outputs-kind').map(e=>e.textContent)}));
""", lang="en")
    assert out["parts"] == ["headline", "part:Key numbers", "part:Not finished", "part:For you to decide", "part:Attempt #16: TopicShift volatility (16-17)",
                            "part:Suggested by the agent", "part:Outputs", "details:report", "button:Run log"]
    assert ["Confirm this assessment", "Change it", "Decide later"] == out["buttons"][out["buttons"].index("Confirm this assessment"):][:3]
    assert out["flags"] == ["This number is not in the file it cites", "The cited file cannot be read, so the number was not checked"]
    assert out["chip"] == "Current" and out["kinds"] == ["Figure", "Table", "Code"] and "指派" not in out["subtitle"]
    assert out["subtitle"].startswith("Claude Code · sonnet · Assigned by 张三")
    rest = out["all"]
    for own in (FULL["headline"], FULL["undone"], FULL["decide"][0], "Holds on every member", "Sharpe by member", "Recheck script", "Table 1", "张三",
                "TopicShift volatility (16-17)"):
        rest = rest.replace(own, "")
    assert not CJK.search(rest), rest


@needs_node
def test_opening_an_unopened_handback_marks_it_opened_once_and_draws_the_cards_again_and_an_opened_one_asks_nothing(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT); await sleepTick();
const first={opened:opened.slice(), refreshed:refreshed.length, mode:state.panelSubject.view.unopened};
VIEW.unopened=false; opened.length=0; refreshed.length=0;
await window.ROWorkspace.openHandback(REPORT); await sleepTick();
console.log(JSON.stringify({first, again:{opened:opened.length, refreshed:refreshed.length}}));
""", unopened=True)
    assert out["first"] == {"opened": [{"project": "/projects/study", "report": out["first"]["opened"][0]["report"]}], "refreshed": 1, "mode": False}
    assert out["first"]["opened"][0]["report"].startswith("research/results/")
    assert out["again"] == {"opened": 0, "refreshed": 0}


@needs_node
def test_a_handback_that_cannot_be_read_says_so_and_a_report_without_key_points_says_that(root, pid, report):
    out = _run(root, pid, report, r"""
handbackDown=true;
const failed=await window.ROWorkspace.openHandback(REPORT);
const unreadable=panelBody.textContent;
handbackDown=false; VIEW=Object.assign(VIEW,{headline:'',key_numbers:[],undone:'',decide:[],suggested:null,confirmed:null,outputs:[],finding:'',limits:'',next_step:''});
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({failed, unreadable, bare:parts(), words:cls('ws-agent-quiet').map(e=>e.textContent), open:cls('ws-hb-report')[0].open}));
""")
    assert out["failed"] is False and "没能读取这份交回。" in out["unreadable"]
    assert out["bare"] == ["other:ws-agent-quiet", "part:尝试 #16：TopicShift volatility (16-17)", "part:代理建议", "details:report", "button:运行日志"]
    assert out["words"][0] == "这份交回没有附上要点。" and "代理没有给出建议。" in out["words"]
    assert out["open"] is True                       # nothing else to read: the report is unfolded, not folded away


# -- confirming ---------------------------------------------------------------------------------------------------------


@needs_node
def test_confirm_this_assessment_sends_the_suggestion_as_it_stands_says_who_confirmed_it_and_offers_what_comes_next(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn('确认这个判断').click(); await sleepTick();
const post=posted.filter(p=>p[0]==='/api/workspace/handback/confirm');
const next=cls('ws-hb-next')[0];
await pbtn('再派一个任务').click();
console.log(JSON.stringify({post, line:cls('ws-hb-confirmed')[0].textContent, done:cls('ws-hb-done')[0].textContent, buttons:pbuttons().slice(0,8),
  nextPrimary:next.children.map(b=>b.className.includes('btn-primary')), pollsBefore:0, started, closed:closed.length}));
""")
    assert out["post"] == [["/api/workspace/handback/confirm", {"project": "/projects/study", "report": out["post"][0][1]["report"], "status": "现行",
                                                                "reason": "Holds on every member", "abandon_reason": "", "was": "🕒"}]]   # with the cell the page showed
    assert out["line"] == "张三 已确认：现行采纳了代理的建议" and out["done"] == "已写入尝试 #16 的研究判断。 撤销"
    assert "确认这个判断" not in out["buttons"] and "改一下" not in out["buttons"]            # the suggestion is answered
    assert out["nextPrimary"] == [True, False, False]                                        # 再派一个任务, 从这条开新尝试, 这条先到这里
    assert out["started"] == [{"attempt": A16, "after": "t1", "context": report}] and out["closed"] == 1    # the new task: same attempt, after this one, from this record


@needs_node
def test_the_three_ways_on_are_a_new_task_in_this_attempt_a_new_attempt_from_the_agents_draft_and_leaving_it(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn('确认这个判断').click(); await sleepTick();
const labels=cls('ws-hb-next')[0].children.map(b=>b.textContent);
await pbtn('从这条开新尝试').click();
const form=attempts.slice();
await pbtn('这条先到这里').click();
console.log(JSON.stringify({labels, form, closed:closed.length, started}));
""", attempt_draft={"description": "Add 2008 to the sample", "result": "", "parents": [A17]})
    assert out["labels"] == ["再派一个任务", "从这条开新尝试", "这条先到这里"]
    assert out["form"] == [{"description": "Add 2008 to the sample", "result": "", "parents": [A16, A17]}]   # 源自 this attempt, then the draft's own
    assert out["closed"] == 1 and out["started"] == []


@needs_node
def test_change_it_opens_the_chooser_prefilled_and_saving_another_status_or_reason_is_sent_as_the_persons_own(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn('改一下').click();
const prefilled={status:key('hb-status').value, reason:key('hb-reason').value, picks:cls('status-pick').map(b=>[b.textContent,b.getAttribute('aria-pressed')]), abandonHidden:cls('ws-hb-abandon')[0].classList.contains('hidden'),
  buttons:pbuttons(), dirty:window.ROWorkspace.isDirty()};
await pick('待定').click(); typed(key('hb-reason'),'Needs 2008');
const edited={dirty:window.ROWorkspace.isDirty(), asked:confirmQuestions.length};
answers.push(false); const stays=window.ROWorkspace.mayLeavePanel(); const asked=confirmQuestions.length;
await key('hb-save').click(); await sleepTick();
console.log(JSON.stringify({prefilled, edited, stays, asked, post:posted.filter(p=>p[0]==='/api/workspace/handback/confirm').map(p=>p[1]),
  line:cls('ws-hb-confirmed')[0].textContent, dirty:window.ROWorkspace.isDirty()}));
""")
    assert out["prefilled"]["status"] == "现行" and out["prefilled"]["reason"] == "Holds on every member"
    # 21.3: a button per status of the project in the order 21.3 names them, the suggested one pressed
    assert out["prefilled"]["picks"] == [["现行", "true"], ["待定", "false"], ["不利证据", "false"], ["已放弃", "false"], ["被否", "false"],
                                         ["归档", "false"], ["其它", "false"]] and out["prefilled"]["abandonHidden"] is True
    assert "保存" in out["prefilled"]["buttons"] and "取消" in out["prefilled"]["buttons"] and out["prefilled"]["dirty"] is False
    assert not {"确认这个判断", "改一下", "先不判断"} & set(out["prefilled"]["buttons"])         # while the chooser is open, only it asks
    assert out["edited"]["dirty"] is True and out["stays"] is False and out["asked"] == 1       # leaving a half-made assessment asks first
    assert out["post"][0]["status"] == "待定" and out["post"][0]["reason"] == "Needs 2008" and out["post"][0]["abandon_reason"] == ""
    assert out["line"] == "张三 已确认：待定改动了代理的建议" and out["dirty"] is False


@needs_node
def test_a_suggestion_to_abandon_the_route_still_asks_for_the_reason_and_the_agents_words_are_offered_for_it(root, pid, report):
    out = _run(root, pid, report, r"""
VIEW.suggested={status:'已放弃',reason:'Dead end',known:true,marker:'☠️',abandons:true};
await window.ROWorkspace.openHandback(REPORT);
await pbtn('确认这个判断').click();
const asked={posts:posted.filter(p=>p[0]==='/api/workspace/handback/confirm').length, status:key('hb-status').value, visible:!cls('ws-hb-abandon')[0].classList.contains('hidden'), offered:key('hb-abandon').value};
typed(key('hb-abandon'),'');
await key('hb-save').click(); await sleepTick();
const blocked={posts:posted.filter(p=>p[0]==='/api/workspace/handback/confirm').length, words:cls('ws-status').map(e=>e.textContent).join('|')};
typed(key('hb-abandon'),'The data stops in 2009');
await key('hb-save').click(); await sleepTick();
console.log(JSON.stringify({asked, blocked, post:posted.filter(p=>p[0]==='/api/workspace/handback/confirm').map(p=>p[1])}));
""")
    assert out["asked"] == {"posts": 0, "status": "已放弃", "visible": True, "offered": "Dead end"}
    assert out["blocked"]["posts"] == 0 and "放弃一条路线时必须写明理由" in out["blocked"]["words"]
    assert out["post"][0]["status"] == "已放弃" and out["post"][0]["reason"] == "Dead end" and out["post"][0]["abandon_reason"] == "The data stops in 2009"


@needs_node
def test_decide_later_writes_nothing_and_a_refused_confirmation_says_so_and_leaves_the_buttons_to_try_again(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn('先不判断').click();
const later={posts:posted.length-0, buttons:pbuttons().filter(b=>['确认这个判断','改一下','先不判断'].includes(b)), words:cls('ws-agent-quiet').map(e=>e.textContent)};
await window.ROWorkspace.openHandback(REPORT);
refuse='The attempt table refused the assessment';
await pbtn('确认这个判断').click(); await sleepTick();
const refused={words:cls('ws-status').map(e=>e.textContent).join('|'), buttons:pbuttons().filter(b=>['确认这个判断','改一下','先不判断'].includes(b)), confirmed:cls('ws-hb-confirmed').length};
refuse=null; await pbtn('确认这个判断').click(); await sleepTick();
console.log(JSON.stringify({later, refused, retried:cls('ws-hb-confirmed').length}));
""")
    assert out["later"]["buttons"] == [] and "先不判断。之后可以在尝试的面板里写研究判断。" in out["later"]["words"]
    assert out["later"]["posts"] == 1                                        # only the engine's note that it was opened (none for the assessment)
    assert "没能确认这个判断。" in out["refused"]["words"] and "The attempt table refused the assessment" in out["refused"]["words"]
    assert out["refused"]["buttons"] == ["确认这个判断", "改一下", "先不判断"] and out["refused"]["confirmed"] == 0 and out["retried"] == 1


@needs_node
def test_a_member_confirms_into_the_working_copy_and_the_proposal_carries_the_stored_chinese_sentence_and_the_record_whatever_the_screen_says(root, pid, report):
    sentence = "由 张三 确认（采纳 Claude Code · sonnet 的建议）"   # 13.1: what goes into a proposal file on the shared drive does not follow the screen
    for lang in ("zh", "en"):
        out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
const note=cls('ws-agent-quiet').map(e=>e.textContent).join('|');
await pbtn(window.ROI18N.t('确认这个判断')).click(); await sleepTick();
console.log(JSON.stringify({note, composed, done:cls('ws-hb-done')[0].textContent, host:cls('compose-result').length}));
""", lang=lang, mode="member")
        assert out["composed"] == [{"kind": "verdict_change", "sentence": sentence, "number": "16",
                                "only": [report.replace(".md", ".context.json")]}]   # the record of who confirmed travels with the row
        assert out["host"] == 1 and out["done"].endswith("proposal")
        assert ("项目的成员：确认会先写进你的工作副本" in out["note"]) if lang == "zh" else ("member of this project" in out["note"])
        assert ("已写进你的工作副本" in out["done"]) if lang == "zh" else ("Written into your working copy" in out["done"])


@needs_node
def test_someone_who_cannot_write_reads_the_handback_but_is_not_offered_to_confirm_it(root, pid, report):
    for mode, said in (("none", "你还不是这个项目的成员：请 PI 把你加入团队，然后同步"), ("unknown", "现在说不清你在这个项目里的角色，研究判断先不能改。")):
        out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({buttons:pbuttons(), words:cls('ws-agent-quiet').map(e=>e.textContent), headline:cls('ws-hb-headline').length}));
""", mode=mode)
        assert said in out["words"] and out["headline"] == 1
        assert not set(out["buttons"]) & {"确认这个判断", "改一下", "先不判断", "写下研究判断"}


@needs_node
def test_a_handback_with_no_suggestion_offers_to_write_the_assessment_prefilled_with_the_one_the_attempt_has(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
const before={words:cls('ws-agent-quiet').map(e=>e.textContent), buttons:pbuttons()};
await pbtn('写下研究判断').click();
const chooser={status:key('hb-status').value, reason:key('hb-reason').value};
typed(key('hb-reason'),'Wait for the data'); await key('hb-save').click(); await sleepTick();
console.log(JSON.stringify({before, chooser, post:posted.filter(p=>p[0]==='/api/workspace/handback/confirm').map(p=>p[1]), line:cls('ws-hb-confirmed')[0].textContent}));
""", suggested=None)
    assert "代理没有给出建议。" in out["before"]["words"] and "写下研究判断" in out["before"]["buttons"] and "确认这个判断" not in out["before"]["buttons"]
    assert out["chooser"] == {"status": "待定", "reason": ""} or out["chooser"]["status"] == "待定"   # the attempt's own status (🕒) is where it starts
    assert out["post"][0]["status"] == "待定" and out["post"][0]["reason"] == "Wait for the data"
    assert out["line"] == "张三 已确认：待定"                                              # nothing was suggested: nothing to say it adopted


@needs_node
def test_a_suggestion_with_a_status_the_project_no_longer_has_cannot_be_confirmed_as_it_is(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({disabled:pbtn('确认这个判断').disabled, change:pbtn('改一下').disabled, words:cls('ws-agent-quiet').map(e=>e.textContent)}));
""", suggested={"status": "成立", "reason": "r", "known": False, "marker": "", "abandons": False})
    assert out["disabled"] is True and out["change"] is False
    assert "这个状态已经不在项目的选项里，请用「改一下」另选一个。" in out["words"]


@needs_node
def test_a_handback_already_confirmed_says_who_and_how_and_offers_no_second_confirmation_only_the_ways_on(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({line:cls('ws-hb-confirmed')[0].textContent, buttons:pbuttons()}));
""", confirmed={"by": PERSON, "at": "2026-10-30T09:00:00+01:00", "status": "现行", "adopted": False, "by_label": "张三"})
    assert out["line"] == "张三 已确认：现行改动了代理的建议"
    assert not set(out["buttons"]) & {"确认这个判断", "改一下", "先不判断"} and "再派一个任务" in out["buttons"]


@needs_node
def test_the_files_the_panel_names_open_in_the_file_panel_and_the_report_and_the_run_log_have_their_own_way_in(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await cls('ws-file-link')[0].click(); await pbtn('Sharpe by member').click();
await pbtn('运行日志').click();
const log={logs:logs.slice(), closed:closed.length};
await window.ROWorkspace.openHandback(REPORT);
await pbtn('编辑报告').click();
console.log(JSON.stringify({files, log, editing, closed:closed.length}));
""")
    assert out["files"] == ["results/sharpe.csv", "figures/sharpe.png"]
    assert out["log"] == {"logs": ["t1"], "closed": 1} and out["editing"] == [out["editing"][0]] and out["editing"][0].startswith("research/results/")


@needs_node
def test_a_handback_opened_from_the_attempts_panel_has_a_way_back_to_it(root, pid, report):
    out = _run(root, pid, report, r"""
let back=0;
await window.ROWorkspace.openHandback(REPORT, {back:()=>{ back+=1; }});
const had=pbtn('返回')!==undefined;
await pbtn('返回').click();
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({had, back, none:pbtn('返回')===undefined}));
""")
    assert out == {"had": True, "back": 1, "none": True}


@needs_node
def test_another_panel_that_holds_half_typed_words_is_not_replaced_without_asking(root, pid, report):
    out = _run(root, pid, report, r"""
global.mayReplacePanel=()=>false;
const refused=await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({refused, gets:gets.filter(p=>p.startsWith('/api/workspace/handback?')).length, subject:state.panelSubject}));
""")
    assert out == {"refused": False, "gets": 0, "subject": None}


# -- where it is opened from -----------------------------------------------------------------------------------------


@needs_node
def test_open_result_on_a_task_whose_latest_run_handed_its_report_back_opens_the_handback_and_any_other_report_opens_to_write_in(root, pid, report):
    out = _run(root, pid, report, r"""
agentsInfo=agentFixture();
const hand='research/results/'+'a'.repeat(32)+'.md', mine='research/results/by-hand.md';
docs[mine]={path:mine,kind:'result',title:'By hand',content:'# By hand\nMine.',revision:'r-1'};
listed.documents.push({path:mine,kind:'result',title:'By hand',excerpt:''});
listed.tasks=[agentTask({id:'t1',done:true,report:hand}),agentTask({id:'t2',text:'Second',done:true,report:mine,owner:''})];
setRuns([mkRun({task_id:'t1',state:'done',can_reply:true,report:hand})]);
VIEW.report=hand;
await window.ROWorkspace.load(root);await sleepTick();
const rows=root.all().filter(e=>e.classList&&e.classList.contains('ws-task'));
await find('打开结果',byClass('ws-agent')).click();await sleepTick();
const handback={subject:state.panelSubject&&state.panelSubject.kind, got:gets.filter(p=>p.startsWith('/api/workspace/handback?')).length, doc:gets.filter(p=>p.startsWith('/api/workspace/document?path=research%2Fresults')).length};
state.panelSubject=null;
const second=root.all().filter(e=>e.tagName==='BUTTON'&&e.textContent==='打开结果');
console.log(JSON.stringify({handback, buttons:second.length}));
""".replace("const rows=root.all().filter(e=>e.classList&&e.classList.contains('ws-task'));\n", ""))
    assert out["handback"] == {"subject": "handback", "got": 1, "doc": 0}      # the editor was not opened for an agent's handback
    assert out["buttons"] == 1                                                   # only the task with a run that handed its report back has the button here


# -- the desk ------------------------------------------------------------------------------------------------------------


@needs_node
def test_the_attempts_panel_opens_the_handback_with_a_way_back_and_asks_before_it_replaces_a_half_made_assessment():
    from test_v10_desk_panel import A16 as DESK_A16, _case
    from test_v10_desk_panel import _run as run_panel
    from test_v16_attempt_tasks_js import _items, _work

    case = _case("single")
    case["desk"]["attempts"][0]["work"] = _work(tasks=7, unfinished=6, items=_items())
    out = run_panel(case, """
      await window.RODesk.openAttempt(%s);
      window.ROWorkspace = { openHandback: (report, options) => { global.extra = { report, back: typeof options.back }; global.backTo = options.back; return null; } };
      const open = panelPart('panel-body').all().filter((k) => k.tag === 'BUTTON' && k.kids.join('') === '打开交回')[0];
      open.click();
      global.mayReplacePanel = () => false; backTo(); const refused = opened.length + posted.length;
      global.mayReplacePanel = () => true; await backTo();
      global.extra.after = state.panelSubject && state.panelSubject.id;
    """ % json.dumps(DESK_A16))
    assert out["extra"] == {"report": "research/results/" + "a" * 32 + ".md", "back": "function", "after": DESK_A16}


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_click_on_a_cards_handback_line_opens_that_handback_and_a_click_on_the_rest_of_the_card_opens_the_attempt(lang):
    from test_v10_fix_map import _MAP_DOM, _desk
    from test_v10_fix_map import A16 as MAP_A16
    from test_v16_attempt_tasks_js import _work
    from test_v9_language import WEBAPP, _i18n_source, _node

    data = _desk("pi")
    waiting_report = "research/results/" + "b" * 32 + ".md"
    items = [{"id": "a", "report": "research/results/" + "a" * 32 + ".md", "unopened": False},   # the latest, already opened
             {"id": "b", "report": waiting_report, "unopened": True}]                         # and an older one that waits
    data["attempts"][0]["work"] = _work(items=items)
    data["attempts"][1]["work"] = _work(tasks=3, unfinished=0, unopened=0, latest=None)
    data["attempts"][2]["work"] = _work(tasks=2, unfinished=1, unopened=0)
    dom = _MAP_DOM.replace("onOpen: (id) => opened.push(id),", "onOpen: (id) => opened.push(id), onOpenHandback: (...args) => handbacks.push(args),")
    steps = """
      global.handbacks = [];
      const press = (target, moved) => { fire('pointerdown', event(target, { x: 5, y: 5 })); if (moved) fire('pointermove', event(root, { x: 80, y: 60 }));
        fire('pointerup', event(root, moved ? { x: 80, y: 60 } : { x: 5, y: 5 })); };
      const mark = () => ({ handbacks: handbacks.slice(), opened: opened.slice() });
      const log = [];
      press(card(%(a)s).querySelector('.rm-headline'));                 log.push(mark());   // the latest handback's line
      press(card(%(a)s).querySelector('.rm-look'));                     log.push(mark());   // 等你看 n
      press(card(%(a)s).querySelector('.rm-title'));                    log.push(mark());   // the rest of the card
      press(card(%(a)s).querySelector('.rm-tasks'));                    log.push(mark());   // the task count is the card's too
      press(card(%(b)s).querySelector('.rm-tasks'));                    log.push(mark());   // a card with tasks but no handback
      press(card(%(c)s).querySelector('.rm-headline'));                 log.push(mark());   // a handback that was opened: still its line
      press(card(%(a)s).querySelector('.rm-headline'), true);           log.push(mark());   // a drag is a move, not a click
      global.extra = { log, cursor: !!card(%(a)s).querySelector('.rm-headline') };
    """ % {"a": json.dumps(MAP_A16), "b": json.dumps("attempt:map.md#17"), "c": json.dumps("attempt:map.md#18")}
    i18n = f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    script = ("global.window = {};\n" + i18n + (WEBAPP / "routemap.js").read_text(encoding="utf-8") + "\n" + dom
              + f"(async () => {{ map.update({json.dumps(data, ensure_ascii=False)}, {{ changed: () => false }});\n{steps}\n"
              " process.stdout.write(JSON.stringify({ extra: global.extra })); })();\n")
    log = _node(script)["extra"]["log"]
    report = "research/results/" + "a" * 32 + ".md"
    c = MAP_A16
    assert [step["handbacks"] for step in log[:2]] == [[[report]], [[report], [waiting_report]]]   # the latest's line opens the latest, 等你看 the one that waits; no attempt is named: no way back
    assert [step["opened"] for step in log[:2]] == [[], []]                                         # and not the panel
    assert log[2]["opened"] == [c] and log[3]["opened"] == [c, c] and log[2]["handbacks"] == log[1]["handbacks"]
    assert log[4]["opened"] == [c, c, "attempt:map.md#17"] and log[4]["handbacks"] == log[1]["handbacks"]
    assert log[5]["handbacks"][-1] == [report]
    assert log[6] == log[5]                                                                          # a drag moved the card: nothing opened


def test_a_registered_heading_opens_a_handback_when_an_agent_run_produced_its_files_and_the_writing_area_when_a_person_did():
    """21.2: 「打开结果」 above registered files is the handback of a record a run handed back (a row of the group has a `run`);
    a record nobody's run wrote still opens to write in, as it always did."""
    from test_outputs_ui import _ROW_ITEMS, _rows_run

    out = _rows_run(_ROW_ITEMS + r"""
const handed = [];
global.ROWorkspace = { openHandback: async (path) => { handed.push(path); } };
const host = new Element('div');
renderRegisteredOutputs(host, { items });
for (const group of groupsOn(host)) { const button = find('打开结果', group); if (button) await button.click(); }
console.log(JSON.stringify({ handed, opened }));
""")
    assert out == {"handed": ["research/results/a.md"], "opened": ["research/results/b.md", "research/results/c.md"]}


# -- 21.3: the chooser of 「改一下」 is the shared one -------------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_21_3_the_handbacks_chooser_is_a_button_per_status_with_the_suggested_one_pressed_in_both_languages(root, pid, report, lang):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn(window.ROI18N.t('改一下')).click();
const picks=()=>cls('status-pick').map(b=>[b.textContent,b.getAttribute('aria-pressed'),b.classList.contains('selected')]);
const first=picks();
await pick(window.ROI18N.t('已放弃')).click();
const abandon={picks:picks(), status:key('hb-status').value, visible:!cls('ws-hb-abandon')[0].classList.contains('hidden')};
await pick(window.ROI18N.t('归档')).click();
console.log(JSON.stringify({first, abandon, after:picks().filter(p=>p[1]==='true'), hidden:cls('ws-hb-abandon')[0].classList.contains('hidden'),
  caption:cls('status-caption').map(e=>e.textContent), primary:panelBody.all().filter(e=>e.tagName==='BUTTON'&&e.className.includes('btn-primary')).map(e=>e.textContent),
  raw:panelBody.textContent.includes('✅')}));
""", lang=lang)
    w = (lambda zh, en: en if lang == "en" else zh)
    names = [w("现行", "Current"), w("待定", "Pending"), w("不利证据", "Adverse evidence"), w("已放弃", "Abandoned"), w("被否", "Rejected"),
             w("归档", "Archived"), w("其它", "Other")]
    assert out["first"] == [[n, "true" if i == 0 else "false", i == 0] for i, n in enumerate(names)]   # 现行 was suggested
    assert out["abandon"] == {"picks": [[n, "true" if i == 3 else "false", i == 3] for i, n in enumerate(names)], "status": w("已放弃", "Abandoned"), "visible": True}
    assert out["after"] == [[w("归档", "Archived"), "true", True]] and out["hidden"] is True   # the hidden field says the status as the screen does; the post below carries the word on disk
    assert out["caption"] == [w("理由", "Reason")] and out["primary"] == [w("保存", "Save")]         # one primary action in the panel
    assert out["raw"] is False                                                                        # the marker is in no field (12.11)


@needs_node
def test_21_3_a_suggestion_with_a_status_the_project_no_longer_has_leaves_nothing_pressed_and_saving_waits_for_a_choice(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn('改一下').click();
const first={pressed:cls('status-pick').filter(b=>b.getAttribute('aria-pressed')==='true').length, status:key('hb-status').value, save:key('hb-save').disabled, reason:key('hb-reason').value};
await pick('待定').click();
console.log(JSON.stringify({first, then:{pressed:cls('status-pick').filter(b=>b.getAttribute('aria-pressed')==='true').map(b=>b.textContent), save:key('hb-save').disabled}}));
""", suggested={"status": "成立", "reason": "r", "known": False, "marker": "", "abandons": False})
    assert out["first"] == {"pressed": 0, "status": "", "save": True, "reason": "r"}   # the agent's reason is offered, a status is for the person to pick
    assert out["then"] == {"pressed": ["待定"], "save": False}


@needs_node
def test_21_3_without_a_suggestion_the_chooser_reads_the_attempts_own_cell_and_a_cell_with_no_known_marker_is_other_with_all_of_it_the_reason(root, pid, report):
    own = {"id": A16, "number": "16", "label": "#16", "verdict": "待补：等 2008 年的数据", "status": {"status": "other", "label": "其它", "words": "待补：等 2008 年的数据"}}
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn('写下研究判断').click();
const first={status:key('hb-status').value, reason:key('hb-reason').value};
await key('hb-save').click(); await sleepTick();
console.log(JSON.stringify({first, post:posted.filter(p=>p[0]==='/api/workspace/handback/confirm').map(p=>p[1])}));
""", suggested=None, attempt=own)
    assert out["first"] == {"status": "其它", "reason": "待补：等 2008 年的数据"}
    assert out["post"][0]["status"] == "其它" and out["post"][0]["reason"] == "待补：等 2008 年的数据"   # the engine writes the reason alone: the cell as it was


# -- the first review's findings: what the page shows and spares ------------------------------------------------------------


def _attempt(verdict: str, *, result: str = "t=2.91", officialness=None) -> dict:
    return {"id": A16, "number": "16", "label": "TopicShift volatility (16-17)", "verdict": verdict, "result": result,
            "status": {"status": "current", "label": "现行", "words": "主线，别动"}, "officialness": officialness}


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_page_names_the_attempt_and_shows_the_assessment_it_has_now_above_the_suggestion_that_would_replace_it(root, pid, report, lang):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
const names=parts(); const at=cls('ws-hb-attempt')[0];
console.log(JSON.stringify({names, title:at.children[0].textContent, now:cls('ws-hb-now',at)[0].textContent, chip:cls('status-label',at)[0].className,
  all:panelBody.textContent}));
""", lang=lang, attempt=_attempt("✅ 主线，别动"))
    zh = lang == "zh"
    here = out["names"].index(f"part:{'尝试 #16：' if zh else 'Attempt #16: '}TopicShift volatility (16-17)")
    assert out["names"][here + 1] == ("part:代理建议" if zh else "part:Suggested by the agent")     # the attempt, then what the agent suggests
    assert out["title"] == ("尝试 #16：" if zh else "Attempt #16: ") + "TopicShift volatility (16-17)"
    assert out["now"] == ("现在的研究判断：现行主线，别动" if zh else "Research assessment now:Current主线，别动")   # the researcher's own words stay as written
    assert "tone-olive" in out["chip"] and "✅" not in out["all"]


@needs_node
def test_a_confirmation_the_engine_refuses_because_the_row_moved_shows_the_row_as_it_is_now_and_the_next_one_is_made_against_that(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
refuse='moved'; refuseState='workspace_handback_moved';
VIEW.attempt=Object.assign({},VIEW.attempt,{verdict:'⚠️ sample too small'});      // what the file holds now: the page reads it again
await pbtn('确认这个判断').click(); await sleepTick();
const first={note:cls('ws-status').map(e=>e.textContent).join('|'), now:cls('ws-hb-now')[0].textContent, gets:gets.length, buttons:pbuttons().filter(b=>b==='确认这个判断'),
  confirmed:cls('ws-hb-confirmed').length};
refuse=null; refuseState=null;
await pbtn('确认这个判断').click(); await sleepTick();
console.log(JSON.stringify({first, posts:posted.filter(p=>p[0]==='/api/workspace/handback/confirm').map(p=>p[1].was), confirmed:cls('ws-hb-confirmed').length}));
""")
    assert "这一行在你打开这份交回以后被改过：上面是它现在的研究判断，请看一眼再确认。" in out["first"]["note"]
    assert out["first"]["now"] == "现在的研究判断：不利证据sample too small" and out["first"]["gets"] == 2
    assert out["first"]["buttons"] == ["确认这个判断"] and out["first"]["confirmed"] == 0       # nothing was written, and the person can look and confirm again
    assert out["posts"] == ["🕒", "⚠️ sample too small"] and out["confirmed"] == 1       # the first against what the page was drawn with, the next against the row now


@needs_node
def test_the_written_line_offers_撤销_that_writes_the_old_cell_back_and_a_row_that_moved_since_keeps_its_confirmation(root, pid, report):
    out = _run(root, pid, report, r"""
const base=customPost, previews=[], writes=[]; let fileCell='✅ x';
customPost=(path,body)=>{
  if(path==='/api/workspace/handback/confirm'){ const r=base(path,body); r.cell='✅ x'; r.before.now_text='✅ x'; return r; }
  if(path==='/api/attempts/preview'){ previews.push(body); return {cells:{verdict:{was:'🕒',was_text:fileCell,now_text:body.fields.verdict}},reason_required:false}; }
  if(path==='/api/attempts/write'){ writes.push(body); return {file:'map.md',backup:'b',ingest_error:null}; }
  return base(path,body);
};
await window.ROWorkspace.openHandback(REPORT);
await pbtn('确认这个判断').click(); await sleepTick();
const written={line:cls('ws-hb-done')[0].textContent, undo:!!key('hb-undo'), now:cls('ws-hb-now')[0].textContent};
fileCell='⚠️ edited meanwhile';
await key('hb-undo').click(); await sleepTick();
const moved={note:cls('ws-status').map(e=>e.textContent).join('|'), writes:writes.length, undo:!!key('hb-undo'), confirmed:cls('ws-hb-confirmed').length,
  unconfirms:posted.filter(p=>p[0]==='/api/workspace/handback/unconfirm').length, next:!!pbtn('再派一个任务'), again:!!pbtn('确认这个判断')};
console.log(JSON.stringify({written, moved}));
""")
    assert out["written"] == {"line": "已写入尝试 #16 的研究判断。 撤销", "undo": True, "now": "现在的研究判断：现行x"}     # the row now reads as it was written
    assert "这一行之后又改过，不能撤销。" in out["moved"]["note"] and out["moved"]["writes"] == 0 and out["moved"]["undo"] is False
    assert (out["moved"]["confirmed"], out["moved"]["unconfirms"], out["moved"]["next"], out["moved"]["again"]) == (1, 0, True, False)   # nothing was undone: the confirmation stands


_UNDO_PAGE = r"""
const base=customPost, writes=[]; let unconfirmRefused=null;
customPost=(path,body)=>{
  if(path==='/api/workspace/handback/confirm'){ const r=base(path,body); r.cell='✅ x'; r.before.now_text='✅ x'; return r; }
  if(path==='/api/attempts/preview') return {cells:{verdict:{was:'🕒',was_text:'✅ x',now_text:body.fields.verdict}},reason_required:false};
  if(path==='/api/attempts/write'){ writes.push(body); return {file:'map.md',backup:'b',ingest_error:null}; }
  if(path==='/api/workspace/handback/unconfirm'){
    if(unconfirmRefused) { const e=new Error(unconfirmRefused); e.state='workspace_handback_run_active'; e.engineMessage=unconfirmRefused; throw e; }
    VIEW.confirmed=null;       // the engine's view of this handback from now on
    return {report:body.report,removed:true,note:'撤销了确认',said:'撤销了确认',node:'attempt:map.md#16',number:'16',history_error:null,generation:3};
  }
  return base(path,body);
};
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_撤销_takes_the_confirmation_back_too_so_the_three_buttons_return_and_a_later_opening_offers_them_as_well(root, pid, report, lang):
    out = _run(root, pid, report, _UNDO_PAGE + r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn(T('确认这个判断')).click(); await sleepTick();
const confirmed={line:cls('ws-hb-confirmed')[0].textContent, next:!!pbtn(T('再派一个任务'))};
await key('hb-undo').click(); await sleepTick();
const back={posts:posted.filter(p=>p[0]==='/api/workspace/handback/unconfirm'), writes,
  line:cls('ws-hb-done')[0].textContent, now:cls('ws-hb-now')[0].textContent, confirmed:cls('ws-hb-confirmed').length,
  buttons:pbuttons().filter(b=>[T('确认这个判断'),T('改一下'),T('先不判断')].includes(b)), undo:!!key('hb-undo'), next:!!pbtn(T('再派一个任务')),
  primary:panelBody.all().filter(e=>e.tagName==='BUTTON'&&e.className.includes('btn-primary')).map(e=>e.textContent),
  errors:cls('ws-status').filter(e=>e.className.includes('err')&&!e.className.includes('ws-hb-flag')).map(e=>e.textContent)};
await window.ROWorkspace.openHandback(REPORT);                      // opened again later: the engine no longer holds the confirmation
const later={confirmed:cls('ws-hb-confirmed').length, buttons:pbuttons().filter(b=>[T('确认这个判断'),T('改一下'),T('先不判断')].includes(b)), line:cls('ws-hb-done').length};
await pbtn(T('确认这个判断')).click(); await sleepTick();           // and it can be confirmed afresh, and undone again
const again={confirmed:cls('ws-hb-confirmed').length, undo:!!key('hb-undo'), posts:posted.filter(p=>p[0]==='/api/workspace/handback/confirm').length};
console.log(JSON.stringify({confirmed, back, later, again}));
""".replace("T(", "window.ROI18N.t("), lang=lang)
    zh = lang == "zh"
    assert out["confirmed"]["next"] is True
    assert out["back"]["posts"] == [["/api/workspace/handback/unconfirm", {"project": "/projects/study", "report": report}]]
    assert out["back"]["writes"] == [{"op": "update", "number": "16", "fields": {"verdict": "🕒"}}]
    assert out["back"]["line"] == ("已撤销，研究判断回到了 待定" if zh else "Undone: the research assessment is back to Pending")
    assert out["back"]["now"] == ("现在的研究判断：待定" if zh else "Research assessment now:Pending")
    assert out["back"]["confirmed"] == 0 and out["back"]["undo"] is False and out["back"]["next"] is False   # no "confirmed by", no ways on, no second 撤销
    assert out["back"]["buttons"] == (["确认这个判断", "改一下", "先不判断"] if zh else ["Confirm this assessment", "Change it", "Decide later"])
    assert out["back"]["primary"] == ["确认这个判断" if zh else "Confirm this assessment"] and out["back"]["errors"] == []
    assert out["later"] == {"confirmed": 0, "buttons": out["back"]["buttons"], "line": 0}
    assert out["again"] == {"confirmed": 1, "undo": True, "posts": 2}


@needs_node
def test_a_confirmation_the_engine_cannot_take_back_is_said_so_with_the_row_put_back_and_the_record_left_standing(root, pid, report):
    out = _run(root, pid, report, _UNDO_PAGE + r"""
unconfirmRefused='A run of this task is still going.';
await window.ROWorkspace.openHandback(REPORT);
await pbtn('确认这个判断').click(); await sleepTick();
await key('hb-undo').click(); await sleepTick();
console.log(JSON.stringify({writes, line:cls('ws-hb-done')[0].textContent, now:cls('ws-hb-now')[0].textContent, confirmed:cls('ws-hb-confirmed').length,
  errors:cls('ws-status').filter(e=>e.className.includes('err')&&!e.className.includes('ws-hb-flag')).map(e=>e.textContent), again:!!pbtn('确认这个判断'), next:!!pbtn('再派一个任务'), busy:pbuttons().length}));
""")
    assert out["writes"] == [{"op": "update", "number": "16", "fields": {"verdict": "🕒"}}] and out["now"] == "现在的研究判断：待定"
    assert out["line"] == "已撤销：尝试 #16 的研究判断改回了确认之前的样子。报告里仍记着你确认过。"        # honest: the record is still there
    assert out["errors"] == ["研究判断已改回，但报告的要点里没能撤销这次确认。 A run of this task is still going."]
    assert out["confirmed"] == 1 and out["again"] is False and out["next"] is True


@needs_node
@pytest.mark.parametrize("leave", ["closePanel()", "state.panelSubject={kind:'attempt'}"])
def test_撤销_takes_the_confirmation_back_even_when_the_panel_was_closed_or_replaced_while_the_old_cell_was_being_written(root, pid, report, leave):
    out = _run(root, pid, report, _UNDO_PAGE + r"""
const wrapped=customPost; let hold=null, stale='✅ x';       // `hold`: a call of this path waits for its gate, as a slow disk would make it wait
const produce=(path,body)=>path==='/api/attempts/preview'&&stale!=='✅ x'?{cells:{verdict:{was:'🕒',was_text:stale,now_text:body.fields.verdict}},reason_required:false}:wrapped(path,body);
customPost=(path,body)=>hold&&hold.path===path?hold.gate.promise.then(()=>produce(path,body)):produce(path,body);
await window.ROWorkspace.openHandback(REPORT);
await pbtn('确认这个判断').click(); await sleepTick();
const unconfirms=()=>posted.filter(p=>p[0]==='/api/workspace/handback/unconfirm').length;
hold={path:'/api/attempts/write',gate:deferred()};
const pending=key('hb-undo').click(); await sleepTick();            // the old cell is on its way to the file ...
const during={writes:writes.length, unconfirms:unconfirms(), busy:state.panelSubject.busy};
""" + leave + r""";                                                   // ... and the person closes the panel, or opens another
hold.gate.resolve(); await pending; await sleepTick();
const after={writes:writes.length, unconfirms:unconfirms(), posts:posted.filter(p=>p[0]==='/api/workspace/handback/unconfirm').map(p=>p[1])};
await window.ROWorkspace.openHandback(REPORT);                      // opened again later: the record has been taken back with the cell
const later={confirmed:cls('ws-hb-confirmed').length, buttons:pbuttons().filter(b=>['确认这个判断','改一下','先不判断'].includes(b))};
// a row that moved meanwhile is not put back, so nothing is taken back either, wherever the person is
VIEW.confirmed=null; await pbtn('确认这个判断').click(); await sleepTick();
stale='⚠️ edited meanwhile'; hold={path:'/api/attempts/preview',gate:deferred()};
const moved=key('hb-undo').click(); await sleepTick(); closePanel(); hold.gate.resolve(); await moved; await sleepTick();
console.log(JSON.stringify({during, after, later, moved:{writes:writes.length, unconfirms:unconfirms()}}));
""")
    assert out["during"] == {"writes": 0, "unconfirms": 0, "busy": True}
    assert out["after"] == {"writes": 1, "unconfirms": 1, "posts": [{"project": "/projects/study", "report": report}]}     # the cell went back, so the confirmation goes with it
    assert out["later"] == {"confirmed": 0, "buttons": ["确认这个判断", "改一下", "先不判断"]}                                  # and the handback offers its buttons, not a stale 已确认
    assert out["moved"] == {"writes": 1, "unconfirms": 1}                                                                  # no second write and no second request


@needs_node
def test_撤销_is_not_offered_for_a_member_s_write_an_abandonment_or_a_cell_that_was_not_changed(root, pid, report):
    out = _run(root, pid, report, r"""
const base=customPost; let decision=null, written=true;
customPost=(path,body)=>{ const r=base(path,body); if(path==='/api/workspace/handback/confirm'){ r.decision=decision; r.written=written; } return r; };
await window.ROWorkspace.openHandback(REPORT);
decision='d-1'; await pbtn('确认这个判断').click(); await sleepTick();
const abandoned=!!key('hb-undo');
await window.ROWorkspace.openHandback(REPORT);
decision=null; written=false; await pbtn('确认这个判断').click(); await sleepTick();
console.log(JSON.stringify({abandoned, same:!!key('hb-undo'), line:cls('ws-hb-done')[0].textContent}));
""")
    assert out == {"abandoned": False, "same": False, "line": "尝试 #16 的研究判断本来就是这样，只记下了你的确认。"}
    member = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn('确认这个判断').click(); await sleepTick();
console.log(JSON.stringify({undo:!!key('hb-undo')}));
""", mode="member")
    assert member == {"undo": False}


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_run_that_went_out_of_bounds_after_it_handed_in_is_warned_of_before_anything_is_confirmed_on_its_word(root, pid, report, lang):
    run_ = {**handbacks.view(root, pid, PERSON, report)["run"], "state": "out_of_bounds", "outcome": "done", "violations": 2}
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
const warn=cls('ws-hb-warning')[0];
console.log(JSON.stringify({first:hb().children[0].className, words:warn.textContent, label:cls('status-label',warn)[0].className, role:warn.getAttribute('role')}));
""", lang=lang, run=run_)
    assert out["first"] == "ws-hb-warning" and "oob" in out["label"] and out["role"] == "status"      # the first thing the page says
    assert out["words"] == ("越过了限制有 2 处越界改动。确认这个判断之前，请先看一下运行日志。" if lang == "zh" else
                            "Went out of boundsThere are 2 out-of-bounds changes. Look at the run log before you confirm this assessment.")
    quiet_ = _run(root, pid, report, "await window.ROWorkspace.openHandback(REPORT); console.log(JSON.stringify({n:cls('ws-hb-warning').length}));")
    assert quiet_ == {"n": 0}


@needs_node
def test_while_a_later_run_of_the_task_is_going_the_old_handback_cannot_be_confirmed_and_says_why(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({buttons:pbuttons().filter(b=>['确认这个判断','改一下','先不判断'].includes(b)), words:cls('ws-agent-quiet').map(e=>e.textContent)}));
""", run_active=True)
    assert out["buttons"] == [] and "这个任务又有一次运行在进行，等它交回以后再确认这一份。" in out["words"]


@needs_node
def test_a_task_whose_attempt_is_no_longer_in_the_table_is_told_so_and_not_that_it_was_never_put_in_one(root, pid, report):
    task = {**handbacks.view(root, pid, PERSON, report)["task"], "attempt": "attempt:map.md#99"}
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({words:cls('ws-agent-quiet').map(e=>e.textContent), buttons:pbuttons().filter(b=>['确认这个判断','改一下'].includes(b))}));
""", task=task, attempt=None)
    assert "这份交回的任务所属的尝试已经不在尝试表里了，请先把任务归入一条尝试。" in out["words"]
    assert not any("还没有归入尝试" in w for w in out["words"]) and out["buttons"] == []


@needs_node
def test_a_member_abandoning_a_route_with_no_result_is_asked_for_it_and_it_goes_with_the_confirmation(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn('改一下').click();
const shown=()=>!cls('ws-hb-result')[0].classList.contains('hidden');
const before=shown();
await pick('已放弃').click(); typed(key('hb-abandon'),'The data stops in 2009');
const asked=shown();
await key('hb-save').click(); await sleepTick();
const blocked={posts:posted.filter(p=>p[0]==='/api/workspace/handback/confirm').length, words:cls('ws-status').map(e=>e.textContent).join('|')};
typed(key('hb-result'),'Stops at 2009'); await key('hb-save').click(); await sleepTick();
console.log(JSON.stringify({before, asked, blocked, post:posted.filter(p=>p[0]==='/api/workspace/handback/confirm').map(p=>p[1]), composed}));
""", mode="member", attempt=_attempt("🕒", result="", officialness={"state": "mine", "proposal": None, "new_row": False}))
    assert out["before"] is False and out["asked"] is True
    assert out["blocked"]["posts"] == 0 and "放弃一条路线时，结果和理由都必须填写" in out["blocked"]["words"]
    assert out["post"][0]["result"] == "Stops at 2009" and out["post"][0]["abandon_reason"] == "The data stops in 2009"
    assert out["composed"][0]["kind"] == "verdict_change" and out["composed"][0]["reason"] == "The data stops in 2009"
    pi = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn('改一下').click(); await pick('已放弃').click();
console.log(JSON.stringify({shown:!cls('ws-hb-result')[0].classList.contains('hidden')}));
""", attempt=_attempt("🕒", result=""))
    assert pi == {"shown": False}                         # the PI's and a single researcher's reason goes into the result itself (14.3)
    has = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn('改一下').click(); await pick('已放弃').click();
console.log(JSON.stringify({shown:!cls('ws-hb-result')[0].classList.contains('hidden')}));
""", mode="member", attempt=_attempt("🕒", result="t=2.91"))
    assert has == {"shown": False}                        # a row that has its result is not asked again


@needs_node
def test_a_members_own_unproposed_row_is_proposed_whole_as_a_new_attempt_replacing_the_pending_one_and_an_approved_one_waits_for_the_sync(root, pid, report):
    mine = {"state": "mine", "proposal": None, "new_row": True}
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn('确认这个判断').click(); await sleepTick();
console.log(JSON.stringify({composed}));
""", mode="member", attempt=_attempt("🕒", officialness=mine))
    assert out["composed"] == [{"kind": "attempt_new", "sentence": "由 张三 确认（采纳 Claude Code · sonnet 的建议）", "number": "16"}]   # no context file: the row carries its own
    waiting = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
await pbtn('确认这个判断').click(); await sleepTick();
console.log(JSON.stringify({composed}));
""", mode="member", attempt=_attempt("🕒", officialness={**mine, "state": "proposed", "proposal": "p-" + "1" * 32}))
    assert waiting["composed"][0]["kind"] == "attempt_new" and waiting["composed"][0]["supersedes"] == "p-" + "1" * 32
    approved = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({buttons:pbuttons().filter(b=>['确认这个判断','改一下','先不判断'].includes(b)), words:cls('ws-agent-quiet').map(e=>e.textContent)}));
""", mode="member", attempt=_attempt("🕒", officialness={**mine, "state": "approved", "proposal": "p-" + "1" * 32}))
    assert approved["buttons"] == [] and "这一行已经批准，正式版本里有了：先同步，再改它。" in approved["words"]


@needs_node
def test_the_run_log_link_opens_the_row_of_a_task_that_is_not_among_the_newest_three_so_the_log_is_there_to_read():
    out = run(r"""
agentsInfo=agentFixture();listed.tasks=Array.from({length:6},(_,i)=>agentTask({id:'t'+i,text:'Task '+i}));
const r0=mkRun({task_id:'t0',state:'done'});
setRuns([r0]);
runLogs[r0.id]=Object.assign(clone(r0),{steps:[{kind:'say',text:'LOGLINE',tool:null}],steps_total:1,changes:{created:[],modified:[],deleted:[],by_person:[]},violations:[],instruction:'x',stderr_tail:'',error:null,refusals:[]});
await window.ROWorkspace.load(root);await sleepTick();
const boxes=()=>root.all().filter(e=>e.classList.contains('ws-task-details'));
const before=boxes().map(e=>e.open);
window.ROWorkspace.showRunLog('t0');await sleepTick();
const after=boxes().map(e=>e.open), log=byClass('ws-run-details');
await window.ROWorkspace.load(root);await sleepTick();
console.log(JSON.stringify({before, after, logText:log?log.textContent.slice(0,60):null, kept:boxes().map(e=>e.open)}));
""")
    assert out["before"] == [False, False, False, True, True, True]
    assert out["after"][0] is True and out["after"][1:] == out["before"][1:] and "LOGLINE" in out["logText"]   # only that row opened, with the log in it
    assert out["kept"][0] is True                                                                              # and it stays open when the list is drawn again


# -- AF-1: a handback without the 21.2 fields, the way on to a new attempt, long numbers -----------------------------------


_NO_21_2 = dict(headline="", key_numbers=[], undone="", decide=[], suggested=None, confirmed=None, outputs=[])


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_handback_with_no_headline_shows_the_contexts_finding_limits_and_next_step_in_its_place_and_the_report_stays_folded(root, pid, report, lang):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
const shown=cls('ws-hb-part').filter(p=>p.children[1]&&p.children[1].classList.contains('ws-hb-text')).map(p=>[p.children[0].textContent,p.children[1].textContent]);
console.log(JSON.stringify({parts:parts(), shown, words:cls('ws-agent-quiet').map(e=>e.textContent), open:cls('ws-hb-report')[0].open, headline:cls('ws-hb-headline').length}));
""", lang=lang, finding="Sword v0.2 beats v0.1 on all 12 members.\nIn every year.", limits="Only one data vendor.", next_step="Repeat on delisted names.", **_NO_21_2)
    zh = lang == "zh"
    names = ["发现与解释", "局限与待确认", "下一步"] if zh else ["Findings and interpretation", "Limitations and open questions", "Next step"]
    none = "这份交回没有附上要点。" if zh else "This handback has no key points."
    assert out["parts"][:4] == [*[f"part:{n}" for n in names], "part:" + ("尝试 #16：" if zh else "Attempt #16: ") + "TopicShift volatility (16-17)"]
    assert out["shown"] == [[names[0], "Sword v0.2 beats v0.1 on all 12 members.\nIn every year."], [names[1], "Only one data vendor."], [names[2], "Repeat on delisted names."]]
    assert none not in out["words"]                                            # they are the key points: no line saying there are none above them
    assert out["open"] is False and out["headline"] == 0                       # something to read above: the report stays folded
    only = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({parts:parts().slice(0,3), words:cls('ws-agent-quiet').map(e=>e.textContent), open:cls('ws-hb-report')[0].open}));
""", lang=lang, finding="Only a finding.", **_NO_21_2)
    assert only["parts"][0] == f"part:{names[0]}" and only["parts"][1].startswith("part:") and names[1] not in only["parts"][1]
    assert none not in only["words"] and only["open"] is False
    # either of the other two alone is something to read as well: no line; with no finding the report is unfolded below them (21.2)
    for held, name in (({"limits": "One vendor."}, names[1]), ({"next_step": "Delisted names."}, names[2])):
        one = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({parts:parts().slice(0,2), words:cls('ws-agent-quiet').map(e=>e.textContent), open:cls('ws-hb-report')[0].open}));
""", lang=lang, **{**_NO_21_2, "finding": "", "limits": "", "next_step": "", **held})
        assert one["parts"][0] == f"part:{name}" and none not in one["words"] and one["open"] is True
    # none of the three held: the line says so, and the report is unfolded so that the person reads something
    nothing = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({parts:parts().slice(0,2), words:cls('ws-agent-quiet').map(e=>e.textContent), open:cls('ws-hb-report')[0].open}));
""", lang=lang, **{**_NO_21_2, "finding": "", "limits": "", "next_step": ""})
    assert nothing["parts"][0] == "other:ws-agent-quiet" and nothing["words"][0] == none and nothing["open"] is True
    if not zh:
        assert not CJK.search(none) and not CJK.search(" ".join(out["words"] + nothing["words"]))


@needs_node
def test_a_handback_with_a_headline_or_a_confirmation_and_nothing_else_does_not_say_it_has_no_key_points(root, pid, report):
    said = "这份交回没有附上要点。"
    headline = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({words:cls('ws-agent-quiet').map(e=>e.textContent)}));
""", **{**_NO_21_2, "headline": "Sharpe 1.82 on all 12 members.", "finding": "", "limits": "", "next_step": ""})
    assert said not in headline["words"]
    confirmed = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({words:cls('ws-agent-quiet').map(e=>e.textContent)}));
""", **{**_NO_21_2, "confirmed": {"by": PERSON, "at": "2026-10-30T09:00:00+01:00", "status": "现行", "adopted": True, "by_label": "张三"},
        "finding": "", "limits": "", "next_step": ""})
    assert said not in confirmed["words"]


@needs_node
def test_with_a_headline_the_handback_does_not_repeat_the_contexts_finding_and_a_report_with_nothing_to_read_but_itself_is_unfolded(root, pid, report):
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({parts:parts(), open:cls('ws-hb-report')[0].open}));
""", finding="Is in the report too.", limits="Not shown beside a headline.")
    assert "part:发现与解释" not in out["parts"] and "part:局限与待确认" not in out["parts"] and out["parts"][0] == "headline" and out["open"] is False
    nothing = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({parts:parts(), open:cls('ws-hb-report')[0].open, text:cls('ws-preview',cls('ws-hb-report')[0])[0].textContent}));
""", finding="", limits="", next_step="", **_NO_21_2)
    assert nothing["open"] is True and "Sharpe: 1.82" in nothing["text"] and nothing["parts"][0] == "other:ws-agent-quiet"
    # limits and next step alone are not a finding: the report is unfolded below them
    partial = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({open:cls('ws-hb-report')[0].open}));
""", finding="", limits="One vendor.", next_step="", **_NO_21_2)
    assert partial["open"] is True
    unreadable = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify({open:cls('ws-hb-report')[0].open, words:cls('ws-status').map(e=>e.textContent)}));
""", context_error="Cannot read research context JSON", finding="", **_NO_21_2)
    assert unreadable["open"] is True and "这份报告的要点读不出来。" in unreadable["words"]       # the key points cannot be read: the report is what there is to read


@needs_node
def test_the_new_attempt_from_a_handback_starts_from_the_drafts_description_else_the_next_step_cut_to_the_limit_with_the_source_chosen(root, pid, report):
    out = _run(root, pid, report, r"""
const forms=[];
for (const [draft, next] of [[{description:'Add 2008 to the sample',result:'r',parents:[]}, 'Repeat on delisted names.'],
                              [null, 'Repeat on delisted names.'],
                              [{description:'   ',result:'',parents:[]}, 'Repeat on delisted names.'],
                              [null, '不到五百字的下一步。'.repeat(80)],
                              [null, ''], [null, 'x'.repeat(500)], [null, 'y'.repeat(501)]]) {
  VIEW.attempt_draft=draft; VIEW.next_step=next; attempts.length=0;
  await window.ROWorkspace.openHandback(REPORT);
  await pbtn('确认这个判断').click(); await sleepTick();
  await pbtn('从这条开新尝试').click();
  forms.push(attempts[0]);
}
console.log(JSON.stringify(forms));
""")
    assert out[0] == {"description": "Add 2008 to the sample", "result": "r", "parents": [A16]}                  # the draft wins
    assert out[1] == {"description": "Repeat on delisted names.", "result": "", "parents": [A16]}                # no draft: the next step, 源自 this attempt
    assert out[2]["description"] == "Repeat on delisted names."                                                # a draft with a blank description is none
    assert len(out[3]["description"]) == 500 and out[3]["description"].endswith("…") and out[3]["description"].startswith("不到五百字的下一步。")
    assert out[4]["description"] == "" and out[4]["parents"] == [A16]                                          # nothing to start from: as before
    assert out[5]["description"] == "x" * 500 and out[6]["description"] == "y" * 499 + "…"                      # at the limit as it is, over it cut


@needs_node
def test_a_long_key_number_is_shown_to_four_significant_digits_with_the_value_as_written_on_hover_and_the_check_is_unchanged(root, pid, report):
    numbers = [{"what": "p-value", "value": "9.509441061106294e-05", "file": "results/sharpe.csv", "check": "found"},
               {"what": "Share", "value": "0.008063173780026744", "file": "results/sharpe.csv", "check": "missing"},
               {"what": "Sharpe ratio", "value": "1.82", "file": "results/sharpe.csv", "check": "found"},
               {"what": "Members", "value": "12", "file": "results/sharpe.csv", "check": "found"},
               {"what": "Return", "value": "12.3456789%", "file": "results/sharpe.csv", "check": "found"},
               {"what": "Latency", "value": "3.2345678 ms", "file": "results/sharpe.csv", "check": "unreadable"}]
    out = _run(root, pid, report, r"""
await window.ROWorkspace.openHandback(REPORT);
console.log(JSON.stringify(cls('ws-hb-number').map(r=>({what:r.children[0].textContent, shown:r.children[1].textContent, title:r.children[1].title===undefined?null:r.children[1].title,
  flags:r.children.slice(3).map(c=>c.textContent)}))));
""", key_numbers=numbers)
    assert [(n["what"], n["shown"], n["title"]) for n in out] == [
        ("p-value", "9.509e-05", "9.509441061106294e-05"), ("Share", "0.008063", "0.008063173780026744"),
        ("Sharpe ratio", "1.82", None), ("Members", "12", None), ("Return", "12.3456789%", None), ("Latency", "3.2345678 ms", None)]   # the rest as written
    assert [n["flags"] for n in out] == [[], ["在所引文件里没找到这个数"], [], [], [], ["所引文件读不了，没法核对"]]               # the number check is of the value as written

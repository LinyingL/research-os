"""Research OS V16, task B5-1 (DESIGN.md 21.8): the pages, under node, in both languages.

One primary button per page or panel, drawn as the primary button; every other action the quiet outline button; a control that
only navigates a text link; a title, a file name or a status never a button. The real scripts run on the stand-in DOMs of the
other page tests, fed the engine's own payloads where the page reads them, and every scenario counts the primary buttons of
what is on screen and looks at what is drawn as a box and what as a link. Every test names its scenario."""

from __future__ import annotations

import json

import pytest

from test_v10_desk_panel import _saving
from test_v10_desk_panel import _run as run_desk
from test_v16_now_doing_js import _now, quant
from test_v16_now_doing_js import _run as run_now
from test_v16_now_doing import me, pid, root  # noqa: F401 -- the fixtures of the two (`me` is the researcher the runs were started by)
from test_v10_desk_proposals import _run_team as run_team
from test_v10_desk_proposals import _team_case
from test_v10_proposal_language import _draft
from test_v16_fix4 import _VARIABLES_BLOCK, _VARIABLES_PRELUDE, _script
from test_v16_provenance import _choose, _data_code, _results, thesis
from test_v16_provenance_js import _run as run_provenance
from test_v9_language import _app_function, needs_node
from test_workspace_js import REPORT
from test_workspace_js import run as run_workspace

#: What the two stand-in DOMs call things differently (`tag` and `cls` of the panel tests', `tagName` and `className` of the home's),
#: and the questions every scenario asks of the elements on screen.
AUDIT = r"""
const tagOf = (e) => e.tag || e.tagName;
const clsOf = (e) => (e.cls ? Array.from(e.cls) : String(e.className).split(/\s+/)).filter(Boolean);
const bodyOf = (e) => e.textContent;
// what is on screen: no `hidden` on it or above it
const shown = (e) => { for (let n = e; n; n = n.parentNode) if (clsOf(n).includes("hidden")) return false; return true; };
const buttonsOf = (host) => host.all().filter((e) => tagOf(e) === "BUTTON" && shown(e));
const primaries = (host) => buttonsOf(host).filter((b) => clsOf(b).includes("btn-primary")).map(bodyOf);
// a status, a mark, a chip or a date drawn as a button
const chipButtons = (host) => buttonsOf(host).filter((b) => clsOf(b).some((c) => ["status-label", "own-mark", "var-chip", "ws-now-date", "desk-meta", "ws-task-facts"].includes(c))).map(bodyOf);
// words drawn as the quiet outline button, and as a text link
const boxes = (host, words) => buttonsOf(host).filter((b) => clsOf(b).includes("btn") && words.includes(bodyOf(b))).map(bodyOf);
const links = (host, words) => buttonsOf(host).filter((b) => clsOf(b).includes("ws-text-link") && !clsOf(b).includes("btn") && words.includes(bodyOf(b))).map(bodyOf);
"""


def _w(lang: str):
    return lambda zh, en: en if lang == "en" else zh


# -- 项目首页 ----------------------------------------------------------------------------------------------------------------

_HOME = AUDIT + r"""
const T = window.ROI18N.t;
agentsInfo=agentFixture();
listed.documents=[{path:'research/overview.md',kind:'overview',title:'Research question',excerpt:''},{path:REPORT,kind:'result',title:'Sword v0 baseline',excerpt:''}];
docs[REPORT]={path:REPORT,kind:'result',title:'Sword v0 baseline',content:'# Sword v0 baseline\nBody',revision:'doc-9'};
listed.tasks=[agentTask({id:'t1',text:'Run the backtest',done:true,report:REPORT,context:{report:REPORT}}),agentTask({id:'t2',text:'Which sample?'})];
setRuns([mkRun({id:'r1',task_id:'t1',state:'done',report:REPORT,can_reply:true,can_revert:true,changes:3}),
         mkRun({id:'r2',task_id:'t2',state:'needs_input',question:'Which sample?',can_reply:true})]);
await window.ROWorkspace.load(root);await sleepTick();
const out={};
const nav=[T('运行日志'),T('打开结果'),T('查看文件'),T('修改历史'),T('报告：{title}',{title:'Sword v0 baseline'})];
out.idle={primary:primaries(root),chips:chipButtons(root),boxed:boxes(root,nav),links:links(root,nav)};
// 回答 opens the reply form of a row: its 发送 is the primary, the task form's save button waits
await find(T('回答')).click();await sleepTick();
out.asking=primaries(root);
await find(T('取消'),byClass('ws-agent-reply')).click();await sleepTick();
out.cancelled=primaries(root);
// the undo of a run asks too
await find(T('撤回本次运行')).click();await sleepTick();
out.undoing=primaries(root);
await find(T('取消'),byClass('ws-agent-undo')).click();await sleepTick();
out.undoCancelled=primaries(root);
// a report opened: its footer's ways on are links
await find('Sword v0 baseline',byClass('ws-documents')).click();await sleepTick();
out.document={primary:primaries(root),boxed:boxes(root,nav),links:links(root,nav)};
// writing a note: 保存 is the primary, and a question open in a row does not make two
await find(T('回答')).click();await sleepTick();
out.askingAgain=primaries(root);
await find(T('新建笔记')).click();await sleepTick();
out.writing=primaries(root);
console.log(JSON.stringify(out));
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_home_page_has_one_primary_button_at_a_time_and_its_ways_on_are_links_not_boxes(lang):
    """项目首页 with a handed-back task and a task that waits for an answer: 添加任务 is the one primary button; a reply form or an
    undo question that opens takes the primary (发送, 确认撤回) and the task form's button goes quiet; while a note is being written
    保存 is it and the question's button goes quiet too. 运行日志, 打开结果, 报告：<title>, 查看文件 and 修改历史 only navigate, so they
    are text links; no state word is a button."""
    w = _w(lang)
    out = run_workspace(_HOME.replace("REPORT", json.dumps(REPORT)), lang=lang)
    add, send, confirm, save = w("添加任务", "Add task"), w("发送", "Send"), w("确认撤回", "Confirm undo"), w("保存", "Save")
    assert out["idle"]["primary"] == [add] and out["idle"]["chips"] == []
    assert out["idle"]["boxed"] == []
    assert {w("运行日志", "Run log"), w("打开结果", "Open the result"), w("报告：Sword v0 baseline", "Report: Sword v0 baseline"),
            w("查看文件", "View file"), w("修改历史", "Change history")} == set(out["idle"]["links"])   # the plan stands open: its footer too
    assert out["asking"] == [send] and out["cancelled"] == [add]
    assert out["undoing"] == [confirm] and out["undoCancelled"] == [add]
    assert out["document"]["primary"] == [add] and out["document"]["boxed"] == []
    assert w("查看文件", "View file") in out["document"]["links"] and w("修改历史", "Change history") in out["document"]["links"]
    assert out["askingAgain"] == [send]
    assert out["writing"] == [save]


# -- 研究路线: an attempt's panel ---------------------------------------------------------------------------------------------

_ATTEMPT_PANEL = AUDIT + r"""
const T = window.ROI18N ? window.ROI18N.t : (s) => s;
const body = () => panelPart("panel-body");
const nav = [T("打开详情页")];
const out = {};
out.open = { primary: primaries(body()), links: links(body(), nav), boxed: boxes(body(), nav), chips: chipButtons(body()) };
type(resultField(), "t=3.10");
find(%(save)s, "BUTTON").click(); await flush();
find(%(abandoned)s, "BUTTON").click();
find(%(save)s, "BUTTON").click(); await flush();
type(field(%(why)s), "residuals are not stationary");
find(%(save)s, "BUTTON").click(); await flush();
out.preview = primaries(body());
find(%(back)s, "BUTTON").click();
out.back = primaries(body());
global.extra = out;
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_an_attempts_panel_has_one_primary_button_through_its_preview_and_opens_the_details_page_with_a_link(lang):
    """#16's panel: 保存 (the research assessment) is its one primary button; 打开详情页 only navigates, so it is a text link. When
    the preview of an abandoning row stands, 保存修改 is the primary and 保存 is the quiet button; 返回修改 gives 保存 back."""
    w = _w(lang)
    case, head = _saving("single", lang)
    steps = head + _ATTEMPT_PANEL % {
        "save": json.dumps(w("保存", "Save")), "abandoned": json.dumps(w("已放弃", "Abandoned")),
        "why": json.dumps(w("为什么放弃这条路线", "Why this route is abandoned")), "back": json.dumps(w("返回修改", "Back to editing"))}
    out = run_desk(case, steps, lang=None if lang == "zh" else lang)["extra"]
    assert out["open"]["primary"] == [w("保存", "Save")] and out["open"]["chips"] == []
    assert out["open"]["links"] == [w("打开详情页", "Open the details page")] and out["open"]["boxed"] == []
    assert out["preview"] == [w("保存修改", "Save changes")]
    assert out["back"] == [w("保存", "Save")]


# -- 图表与论断 and 数据与代码 --------------------------------------------------------------------------------------------------

_PROVENANCE = AUDIT + r"""
const T = window.ROI18N ? window.ROI18N.t : (s) => s;
global.apiGet = async (path) => {
  if (path === "/api/stata") return { has_do: false };
  if (path === "/api/data-code") return %(data)s;
  if (path === "/api/results") return %(results)s;
  throw new Error("no fixture for " + path);
};
global.apiPost = async () => ({ ok: true, generation: 6 });
(async () => {
  const out = {};
  const results = el("div", "host"); views.results = results;
  await window.RONode.loadResults(results, false);
  out.results = { primary: primaries(results), chips: chipButtons(results), boxed: boxes(results, %(titles)s) };
  const data = el("div", "host"); views.lineage = data;
  await window.RONode.loadDataCode(data, false);
  out.data = { primary: primaries(data), chips: chipButtons(data), boxed: boxes(data, %(titles)s) };
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_choosing_the_paper_is_the_one_primary_button_of_figures_and_claims_until_it_is_chosen_and_data_and_code_has_none(tmp_path, lang):
    """The thesis in miniature: with no draft chosen as the paper 「设为论文正文」 is 图表与论断's one primary button (its picker's
    choices are quiet); once a draft is chosen the page has none -- its rows' 指定生成它的脚本 and 不属于论文 are quiet buttons --;
    数据与代码 has none. A file's name and a state word on either page are never drawn as a button."""
    root = thesis(tmp_path / "thesis")
    before_results, data = _results(root), _data_code(root)
    _choose(root)
    after_results = _results(root)
    names = {f["path"].rsplit("/", 1)[-1] for f in data["files"]}
    names |= {i["path"].rsplit("/", 1)[-1] for key in ("items", "other", "loose", "excluded") for i in after_results[key]}
    titles = sorted(names)

    def page(results):
        body = _PROVENANCE % {"data": json.dumps(data, ensure_ascii=False), "results": json.dumps(results, ensure_ascii=False),
                              "titles": json.dumps(titles)}
        return run_provenance(body, language=None if lang == "zh" else lang, view="results")

    chosen_words = _w(lang)("设为论文正文", "Set as the paper")
    first, second = page(before_results), page(after_results)
    assert first["results"]["primary"] == [chosen_words] and second["results"]["primary"] == []
    for out in (first, second):
        assert out["results"]["chips"] == [] and out["data"]["chips"] == []
        assert out["results"]["boxed"] == [] and out["data"]["boxed"] == []
        assert out["data"]["primary"] == []


# -- 等你处理: the rows of the one list ------------------------------------------------------------------------------------------

_NEEDS = r"""
const quiet = (row) => row.all().filter((e) => e.tagName === 'BUTTON' && e.classList.contains('btn')).map((b) => b.textContent);
const linked = (row) => row.all().filter((e) => e.tagName === 'BUTTON' && e.classList.contains('ws-text-link')).map((b) => b.textContent);
await window.ROWorkspace.load(root); await sleepTick();
const rows = cls('desk-row', cls('ws-now-needs')[0]).map((row) => ({ chip: text('status-label', row)[0], quiet: quiet(row), links: linked(row) }));
// the cards of 进行中的尝试: an attempt's name and a handback's headline open something, none of them is drawn as a button
const cardButtons = cls('ws-now-cards')[0].all().filter((e) => e.tagName === 'BUTTON');
console.log(JSON.stringify({ rows, cardClasses: cardButtons.map((b) => b.className) }));
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_row_of_needs_you_offers_what_handles_it_the_ways_on_as_links_and_the_acts_as_quiet_buttons(quant, pid, lang):
    """A question to answer, a handback to read and a run that failed: 打开交回 and 运行日志 only open the place that handles it, so
    they are text links; 回答 (which opens the reply to write) and 知道了 (which sets the row aside) are acts, so they are the quiet
    buttons; no row has a primary button -- the page's is the task form's. The cards above it open the attempt and the
    handback by names that are links, never boxes."""
    w = _w(lang)
    out = run_now(_now(quant, lang=lang), _NEEDS, lang=lang)
    assert out["rows"] == [
        {"chip": w("等你回答", "Waiting for your answer"), "quiet": [w("回答", "Answer")], "links": []},
        {"chip": w("交回", "Handback"), "quiet": [], "links": [w("打开交回", "Open handback")]},
        {"chip": w("失败", "Failed"), "quiet": [w("知道了", "Got it")], "links": [w("运行日志", "Run log")]}]
    assert out["cardClasses"] and all("btn" not in c.split() for c in out["cardClasses"])


# -- 变量: the list, its chips and the registered drafts ---------------------------------------------------------------------------

_CARDS_BLOCK = "\n".join([_VARIABLES_BLOCK, _app_function("function cardChips(")])

_CARD_LIST = AUDIT + r"""
global.answer = { cards: [
  { id: "draftvar", name: "draftvar", state: "ok", draft: 1, in_use: null, abandoned: false },
  { id: "topicshift", name: "TopicShift", state: "ok", draft: null, in_use: 1, abandoned: false },
  { id: "old", name: "Old", state: "ok", draft: null, in_use: null, abandoned: true },
  { id: "twice", name: "Twice", state: "bad", reason: "conflict", draft: null, in_use: null, abandoned: false }] };
await loadVariablesView();
const chips = byClass(views.variables, "var-chip");
out.chips = chips.map((c) => [c.tag, c.className, text(c)]);
out.chipButtons = chipButtons(views.variables);
out.primary = primaries(views.variables);
out.rows = byClass(views.variables, "var-row").map((r) => r.tag);
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_variable_cards_state_is_a_label_never_a_button_and_a_page_of_cards_has_no_primary_button(lang):
    """The 变量 page with a draft, a card in use, a card out of use and one whose log has two histories: each state is a `var-chip`
    span -- the 「草稿 v1」 chip carried the team's `draft` box (a border and padding that made it look like a button), and its tone
    is `is-draft` now --, the rows are not buttons, and with cards on the page 「新建变量卡」 is the quiet button, so there is no
    primary one (the empty page's is 21.7's)."""
    w = _w(lang)
    out = _script(lang, [_CARDS_BLOCK, _VARIABLES_PRELUDE], _CARD_LIST)
    assert out["chips"] == [
        ["SPAN", "var-chip is-draft", "草稿 v1"], ["SPAN", "var-chip in-use", "使用 v1"],   # the 变量 page is still Chinese in English (13.7)
        ["SPAN", "var-chip dead", "已弃用"], ["SPAN", "var-chip bad", "两份历史待处理"]]
    assert out["chipButtons"] == [] and out["primary"] == [] and set(out["rows"]) == {"DIV"}


_REGISTERED = AUDIT + r"""
const host = el("div", "host");
global.atText = (at) => String(at).slice(0, 10);
global.openFilePanel = () => {}; global.openVariableCard = () => {}; global.openResultOnHome = () => {};
global.ROWorkspace = { openHandback() {} };
const plainMk = global.mk;   // the stand-in element has no `children` and no `remove`, which this block asks for
global.mk = (...args) => { const e = plainMk(...args); Object.defineProperty(e, "children", { get() { return this.kids.filter((k) => typeof k !== "string"); } }); return e; };
const plainButton = global.mkButton; global.mkButton = (...args) => { const b = plainButton(...args); b.remove = () => {}; return b; };
renderRegisteredOutputs(host, { broken: [], items: [
  { path: "out/prices.toml", title: "Prices definition", kind: "table", task: "t1", task_title: "Fetch prices", report: "research/results/aa.md",
    run: "r1", state: "current", at: "2026-10-20T10:00:00", variable_card: "prices" },
  { path: "out/returns.toml", title: "", kind: "table", task: "t1", task_title: "Fetch prices", state: "current", at: "2026-10-20T10:00:00",
    definition: { name: "Returns", meaning: "Monthly return" } }] }, true);
const T = window.ROI18N.t;
const kinds = (b) => ({ cls: clsOf(b), words: bodyOf(b) });
out.buttons = buttonsOf(host).map(kinds);
out.primary = primaries(host);
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_registered_draft_opens_by_a_title_that_is_a_link_and_a_way_on_that_is_a_link_while_importing_is_the_quiet_button(lang):
    """待确认的变量草稿: the draft's title opens its file (a text link with the path under it, never a box), 打开结果 and 打开定义卡
    only navigate (text links), 导入为变量草稿 writes a card (the quiet button); the block has no primary button."""
    w = _w(lang)
    out = _script(lang, [_app_function("function renderRegisteredOutputs(")], r"""
const kinds = (b) => ({ cls: clsOf(b), words: bodyOf(b) });
""" + _REGISTERED.replace("const kinds = (b) => ({ cls: clsOf(b), words: bodyOf(b) });", ""))
    by = {b["words"]: b["cls"] for b in out["buttons"]}
    assert by["Prices definition"] == ["ws-text-link", "ws-file-link"]
    assert by[w("打开结果", "Open the result")] == ["ws-text-link"] and by[w("打开定义卡", "Open definition card")] == ["ws-text-link"]
    assert by[w("导入为变量草稿", "Import as variable draft")] == ["btn", "btn-small"]
    assert out["primary"] == []


# -- 成员 and 提议: a page of steps, a draft that is looked at and then sent ------------------------------------------------------------

_MEMBERS = AUDIT + r"""
global.clearProjectState = () => {}; window.scrollY = 0; window.scrollTo = () => {}; global.shellCan = () => false;
const stage = (me, key, share) => { c.get["/api/me"] = me; c.get["/api/me/key"] = key; c.get["/api/share"] = share; };
const members = async (host) => { await window.ROTeam.loadMembers(host, false); return { primary: primaries(host), buttons: buttonsOf(host).map((b) => [bodyOf(b), clsOf(b).includes("btn-primary")]) }; };
const out = {};
// 1. nobody yet: the name and e-mail form comes first, the key waits
stage({ me: null, state: "absent", path: "~/.researchos/me.toml" }, { key: null, ssh_keygen: true, path: "~/.researchos/keys/id" }, { team: [], shared: null });
views.members = el("div", "host"); out.noOne = await members(views.members);
// 2. who I am is known: starting the team is the step, the key waits
stage({ me: { id: "li@example.com", name: "Li", email: "li@example.com" } }, { key: null, ssh_keygen: true, path: "~/.researchos/keys/id" }, { team: [], shared: null });
views.members = el("div", "host"); out.named = await members(views.members);
// 3. the PI with a shared folder: publishing comes first, adding a member and the key wait
stage({ me: { id: "li@example.com", name: "Li", email: "li@example.com" } }, { key: null, ssh_keygen: true, path: "~/.researchos/keys/id" },
  { role: "pi", team: [{ id: "li@example.com", name: "Li", role: "pi", fingerprint: "SHA256:x" }], shared: "/shared", reachable: true, last_snapshot: null, pending_publish: { count: 0, files: [] } });
views.members = el("div", "host"); out.pi = await members(views.members);
global.extra = out;
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_members_page_has_one_primary_button_the_next_step_it_waits_for(lang):
    """成员 is a page of steps in the order they are taken. Whatever stage it is at, its one primary button is the first step not
    done -- 保存 of 「我是谁」, then 建立成员名单, then 发布 -- and the buttons of the later steps (生成我的公钥, 添加成员) are the quiet
    ones until the earlier are done; 「从公盘加入项目…」 is quiet throughout."""
    case = _team_case("pi", lang)
    out = run_team(case, "global.c = c;\n" + _MEMBERS, lang=None if lang == "zh" else lang)["extra"]
    assert out["noOne"]["primary"] == ["保存"]
    assert out["named"]["primary"] == ["建立成员名单"]
    assert out["pi"]["primary"] == ["发布"]
    quiet = {"保存": False, "生成我的公钥": False, "添加成员": False, "从公盘加入项目…": False}
    for stage in out.values():
        for label, primary in stage["buttons"]:
            if label in quiet and label not in stage["primary"]:
                assert primary is False, label


_COMPOSE = AUDIT + r"""
views.proposals = el("div", "host");
await window.ROTeam.loadProposals(views.proposals);
const pick = (kind) => { const r = views.proposals.all().find((k) => k.tag === "INPUT" && k.value === kind); r.checked = true; (r.on.change || []).forEach((f) => f()); };
pick("verdict_change");
const out = { before: primaries(views.proposals) };
views.proposals.all().find((k) => k.tag === "TEXTAREA").value = "abandon 16";
views.proposals.all().find((k) => k.tag === "BUTTON" && k.cls.has("btn-primary")).click();
await settle(); await settle();
out.shown = { primary: primaries(views.proposals), buttons: buttonsOf(views.proposals).filter((b) => clsOf(b).includes("btn")).map(bodyOf) };
global.extra = out;
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_members_draft_has_one_primary_button_looking_at_it_first_and_sending_it_once_it_is_shown(lang):
    """The composer of 提交提议: 「查看将要发送的内容」 is its primary button until the engine's dry run stands under it; then 「发送提议」
    is, and looking again is the quiet one."""
    w = _w(lang)
    case = _team_case("member", lang)
    case["post"] = {"/api/proposals": _draft(lang)}
    out = run_team(case, "global.c = c;\n" + _COMPOSE, lang=None if lang == "zh" else lang)["extra"]
    assert out["before"] == [w("查看将要发送的内容", "Review what will be sent")]
    assert out["shown"]["primary"] == [w("发送提议", "Send proposal")]
    assert w("查看将要发送的内容", "Review what will be sent") in out["shown"]["buttons"]

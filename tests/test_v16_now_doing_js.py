"""Research OS V16, task B2-1 (DESIGN.md 21.4), the pages.

The top of 项目首页 says 「现在在做什么」: the attempts in progress as cards (the progress line, 「等你看 n」, the first line
of the latest handback; a click opens the attempt's panel as the route map's cards do) beside 等你处理, the one list of
what waits for the person (a click on a row's action opens the place that handles it), stacked when the window is narrow.
The sidebar counts the rows of that list beside 项目首页, in a project of one person too, and follows an act without a
reload. 研究路线's column is the same list; with nothing waiting it folds to a strip and unfolds when a row arrives. The
pages run under node on the stand-in DOM of the home's other tests (workspace.js and desk.js together), fed what the
real engine answers, in both languages. Every test names its scenario."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from researchos import handbacks
from test_v10_desk_panel import _case, desk_with_needs
from test_v10_desk_proposals import _team_case
from test_v10_route_link import drive  # noqa: F401 -- the two-person fixture
from test_v14_routes_page import _run as run_desk
from test_v16_handback import PERSON
from test_v16_now_doing import (A, _at, _now, _of, _with_table, asking, handed, me, pid, root)  # noqa: F401 -- fixtures
from test_v10_desk_payload import _two_people
from test_v9_language import CJK, needs_node
from test_workspace_js import WEBAPP, run

#: What the stand-in DOM of the home lacks that desk.js draws with: app.html's `mk` and `mkButton`, a status line and the
#: sidebar's parts; and the engine's answer to /api/workspace/now.
_STUBS = r"""
global.mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
global.mkButton = (label, cls, onClick) => { const b = mk("button", cls || "btn btn-small", label); b.type = "button"; if (onClick) b.addEventListener("click", onClick); return b; };
global.setStatus = (node, kind, text) => { node.className = "notice-status" + (kind ? " " + kind : ""); node.textContent = text || ""; };
global.ownMark = () => null; global.openReviewPanel = () => {};
global.sidebar = { "side-count-home": { textContent: "", title: "" }, "side-count-proposals": { textContent: "", title: "" },
  "share-line": { textContent: "", title: "", classList: { toggle() {} } }, "me-line": { textContent: "", title: "", append() {} }, "new-attempt-btn": { dataset: {} } };
const panelParts = { panel: panel, "panel-title": panelTitle, "panel-subtitle": panelSubtitle, "panel-body": panelBody,
  "panel-backdrop": { classList: { add() {}, remove() {}, contains: () => true } } };
document.getElementById = (id) => panelParts[id] || sidebar[id] || null;
const refreshed = [];
global.refreshCurrentView = () => { refreshed.push(1); return window.ROWorkspace.load(root, true); };
"""


def _page(now: dict, extra: str = "") -> str:
    answer = {**now, "project": "/projects/study", "id": "p-study"}
    return _STUBS + f"""
const NOW = {json.dumps(answer, ensure_ascii=False)};
let nowAnswers = 0;
customGet = (path) => {{
  if (path === '/api/workspace/now') {{ nowAnswers += 1; return clone(NOW); }}
  if (path === '/api/me') return {{ me: {{ id: 'zhang@example.com', name: '张三' }} }};
  return undefined;
}};
{extra}
"""


_HELPERS = r"""
const cls = (c, where = root) => where.all().filter((e) => e.classList.contains(c));
const text = (c, where = root) => cls(c, where).map((e) => e.textContent);
const cards = () => cls('ws-now-card').map((card) => ({
  name: text('ws-now-open', card)[0], status: text('status-label', card)[0], tasks: text('ws-now-tasks', card)[0],
  look: text('ws-now-look', card)[0] || null, line: text('ws-now-line', card)[0] || null }));
const needsRows = () => cls('desk-row', cls('ws-now-needs')[0]).map((row) => ({
  chip: text('status-label', row)[0], what: text('desk-what', row)[0] || null, meta: text('desk-meta', row)[0] || null,
  attempt: text('desk-link', row)[0] || null,
  buttons: row.all().filter((e) => e.tagName === 'BUTTON' && (e.classList.contains('btn') || e.classList.contains('ws-text-link'))).map((b) => b.textContent) }));
const button = (label, where = root) => where.all().find((e) => e.tagName === 'BUTTON' && e.textContent === label);
"""


def _run(now: dict, scenario: str, *, lang: str = "zh", extra: str = "") -> dict:
    return run(_page(now, extra) + _HELPERS + scenario, lang=lang)


@pytest.fixture
def quant(root, pid):
    """Quant after the grouping (21.9 item 1): Sword v0 (#4, two tasks), v0.2 (#5, two, the second not opened yet) and v0.3 (#6),
    all handed back; a settled baseline (#9) with a human task; a question a run is waiting on and one that failed."""
    handed(root, pid, "t7", A[4], _at(20, 9))
    handed(root, pid, "t8", A[4], _at(20, 12))
    handed(root, pid, "t12", A[5], _at(24, 9))
    handed(root, pid, "t13", A[5], _at(24, 11), opened=False)
    handed(root, pid, "t14", A[6], _at(23, 9))
    from researchos import workspace

    workspace.save_tasks(root, {"tasks": workspace.read_tasks(root)[0] + [{"id": "t30", "text": "Read the baseline again", "done": False, "owner": "", "due": "", "attempt": A[9]}],
                                "revision": workspace.read_tasks(root)[1]})
    asking(root, pid, "a1", A[5], "needs_input", question="Which sample should I use?", when=_at(25))
    asking(root, pid, "f1", A[6], "failed", when=_at(26))
    return root


# -- the top of the page ----------------------------------------------------------------------------------------


@needs_node
def test_the_home_opens_with_what_is_going_on_the_cards_of_the_attempts_in_progress_beside_the_one_list_that_needs_you(quant, pid):
    out = _run(_now(quant), r"""
await window.ROWorkspace.load(root); await sleepTick();
const page = cls('ws-page')[0];
console.log(JSON.stringify({ top: page.children.map((c) => c.className), title: text('ws-now-title'), headings: text('ws-now-heading'),
  cards: cards(), rows: needsRows(), count: sidebar['side-count-home'].textContent, hidden: cls('ws-now')[0].classList.contains('hidden') }));
""")
    assert out["top"] == ["ws-notices", "ws-heading", "ws-now", "ws-plan", "ws-columns", "ws-tasks"] and out["hidden"] is False   # 21.4: the plan stands under 现在在做什么
    assert out["title"] == ["现在在做什么"] and out["headings"] == ["进行中的尝试", "等你处理"]
    # newest activity first: #5 (its report of the 24th), #6 (a failed run on the 26th), #4 (the 20th), #9 by the date of its row
    assert [c["name"] for c in out["cards"]] == ["#6Sword v0.3", "#5Sword v0.2", "#4Sword v0", "#9Settled baseline"]
    by = {c["name"]: c for c in out["cards"]}
    assert by["#4Sword v0"]["tasks"] == "任务 2，全部完成" and by["#4Sword v0"]["look"] is None
    assert by["#4Sword v0"]["status"] == "待定" and by["#9Settled baseline"]["status"] == "现行"
    assert by["#5Sword v0.2"]["look"] == "等你看 1" and by["#5Sword v0.2"]["tasks"] == "任务 3，未完成 1"   # t12 and t13 handed back; the question a1 is not done
    assert by["#6Sword v0.3"]["tasks"] == "任务 2，未完成 1" and by["#6Sword v0.3"]["look"] is None
    assert by["#5Sword v0.2"]["line"].startswith("Sword v0.2 keeps a Sharpe ratio")      # the headline of the latest handback
    assert by["#9Settled baseline"]["tasks"] == "任务 1，未完成 1"
    # one list: a question to answer, the handback to read, the run that failed -- each with the one action that handles it
    assert [(r["chip"], r["buttons"]) for r in out["rows"]] == [
        ("等你回答", ["回答"]), ("交回", ["打开交回"]), ("失败", ["运行日志", "知道了"])]
    assert out["rows"][0]["what"] == "Which sample should I use?" and out["rows"][1]["what"] == "Recheck"
    assert out["count"] == "3"      # the sidebar's, beside 项目首页


@needs_node
def test_the_same_page_in_english_says_every_word_in_english_and_keeps_the_researchers_and_the_agents_own_words(quant, pid):
    out = _run(_now(quant, lang="en"), r"""
await window.ROWorkspace.load(root); await sleepTick();
console.log(JSON.stringify({ title: text('ws-now-title'), headings: text('ws-now-heading'), cards: cards(), rows: needsRows(),
  count: [sidebar['side-count-home'].textContent, sidebar['side-count-home'].title], all: cls('ws-now')[0].textContent }));
""", lang="en")
    assert out["title"] == ["In progress"] and out["headings"] == ["Attempts in progress", "Needs you"]
    assert out["cards"][1]["look"] == "To read: 1" and out["cards"][0]["tasks"] == "2 tasks, 1 unfinished" and out["cards"][2]["tasks"] == "2 tasks, all done"
    assert out["cards"][3]["tasks"] == "1 task, 1 unfinished" and out["cards"][0]["status"] == "Pending" and out["cards"][3]["status"] == "Current"
    assert [(r["chip"], r["buttons"]) for r in out["rows"]] == [
        ("Waiting for your answer", ["Answer"]), ("Handback", ["Open handback"]), ("Failed", ["Run log", "Got it"])]
    assert out["count"] == ["3", "3 need you"]
    rest = out["all"]
    for own in ("Sword v0.2", "Sword v0.3", "Sword v0", "Settled baseline", "Sword v0.2 keeps a Sharpe ratio over the full sample.",
                "Which sample should I use?", "Recheck", "Recheck Sword v0.2", "Question f1", "Question a1", "Recheck Sword v0.3"):
        rest = rest.replace(own, "")
    assert not CJK.search(rest), rest


@needs_node
def test_a_card_opens_the_attempts_panel_and_its_look_and_headline_open_the_handback(quant, pid):
    out = _run(_now(quant), r"""
await window.ROWorkspace.load(root); await sleepTick();
const card = cls('ws-now-card').find((c) => text('ws-now-open', c)[0] === '#5Sword v0.2');
const before = gets.length;
await cls('ws-now-open', card)[0].click(); await sleepTick();
const afterAttempt = gets.slice(before);
const subject = [state.panelSubject && state.panelSubject.kind, state.panelSubject && state.panelSubject.id];
await cls('ws-now-look', card)[0].click(); await sleepTick();
const handback = gets.filter((p) => p.startsWith('/api/workspace/handback?'));
console.log(JSON.stringify({ afterAttempt, subject, handback, panel: state.panelSubject && state.panelSubject.kind }));
""", extra=r"""
customGet = ((older) => (path) => {
  if (path === '/api/desk') return { role: 'single', attempts: [{ id: 'attempt:map.md#5', number: '5', title: 'Sword v0.2', date: '10-21', route_status: { status: 'pending', label: '待定', words: '' }, work: { tasks: 0, items: [] }, marks: {} }],
    lines: [], lines_missing: [], column: { role: 'single', me: null, shared: { state: 'not_needed', message: null }, waiting: [], mine: [], review: { count: 0, groups: [], none: { items: [], cards: [] } }, sync: null },
    needs: { rows: [], notices: [], role: 'single' } };
  return older(path);
})(customGet);
""")
    assert "/api/desk" in out["afterAttempt"]     # the panel draws from the desk's reading, taken first when the desk is not the page
    assert out["subject"] == ["desk-attempt", A[5]]
    assert len(out["handback"]) == 1 and out["panel"] == "handback"      # 「等你看 1」 opens the one handback that waits


@needs_node
def test_a_project_with_no_attempt_holding_a_task_says_so_in_one_quiet_sentence_with_the_way_in(tmp_path):
    from researchos import workspace
    from test_v8_phase2 import _project

    root = _project(tmp_path / "thesis")
    rows = "".join(f"| {n} | 10-{n:02d} | Idea {n} | V | r{n} | {'✅' if n % 3 else '🕒'} |\n" for n in range(1, 25))
    _with_table(root, "# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n" + rows)
    zh = _run(_now(root), r"""
await window.ROWorkspace.load(root); await sleepTick();
console.log(JSON.stringify({ cards: cards(), quiet: text('ws-now-none'), list: text('desk-empty', cls('ws-now-needs')[0]), count: sidebar['side-count-home'].textContent }));
""")
    assert zh["cards"] == [] and zh["quiet"] == ["还没有挂着任务的尝试。在研究路线里打开一条尝试，选「在这条尝试下加任务」。"]
    assert zh["list"] == ["现在没有要处理的事。"] and zh["count"] == ""
    en = _run(_now(root, lang="en"), r"""
await window.ROWorkspace.load(root); await sleepTick();
console.log(JSON.stringify({ quiet: text('ws-now-none'), list: text('desk-empty', cls('ws-now-needs')[0]) }));
""", lang="en")
    assert en["quiet"] == ["No attempt holds a task yet. Open an attempt under Research routes and choose “Add a task under this attempt”."]
    assert en["list"] == ["Nothing to handle right now."]
    # attempts hold tasks but none is in progress (assessed 不利证据): the other sentence
    workspace.save_tasks(root, {"tasks": [{"id": "h1", "text": "Think", "done": False, "owner": "", "due": "", "attempt": A[1]}], "revision": workspace.read_tasks(root)[1]})
    _with_table(root, (root / "map.md").read_text().replace("| 1 | 10-01 | Idea 1 | V | r1 | ✅ |", "| 1 | 10-01 | Idea 1 | V | r1 | ⚠️ |"))
    other = _run(_now(root), r"""
await window.ROWorkspace.load(root); await sleepTick();
console.log(JSON.stringify({ quiet: text('ws-now-none') }));
""")
    assert other["quiet"] == ["现在没有进行中的尝试。"]


@needs_node
def test_a_project_with_loose_tasks_or_no_attempt_at_all_is_told_the_ways_in_that_exist(tmp_path):
    """21.4 (fix batch 3): Quant before the grouping has 16 tasks and an empty attempt table; 「在研究路线里打开一条尝试」 leads to a
    map with no attempt on it. The sentence names what can be done there: group the tasks, or start an attempt in the task
    form. A project with attempts and no loose task keeps the sentence that sends the person to an attempt's panel."""
    from test_v8_phase2 import _project

    root = _project(tmp_path / "quant")
    _with_table(root, "# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n")
    loose = "listed.attempts = []; listed.tasks = [{ id: 't1', text: 'Read the papers', owner: '', due: '', done: false }];"
    none = "listed.attempts = []; listed.tasks = [];"
    scenario = "%s\nawait window.ROWorkspace.load(root); await sleepTick();\nconsole.log(JSON.stringify({ quiet: text('ws-now-none') }));"
    zh_loose = _run(_now(root), scenario % loose)["quiet"]
    zh_none = _run(_now(root), scenario % none)["quiet"]
    assert zh_loose == ["还没有挂着任务的尝试。全部任务下的「把这些任务归入尝试…」可以把已有的任务归到尝试里，也可以在任务表单里选「新开一条尝试…」。"]
    assert zh_none == ["还没有挂着任务的尝试。在任务表单里选「新开一条尝试…」，写下第一个任务。"]
    en_loose = _run(_now(root, lang="en"), scenario % loose, lang="en")["quiet"]
    en_none = _run(_now(root, lang="en"), scenario % none, lang="en")["quiet"]
    assert en_loose == ["No attempt holds a task yet. “Put these tasks into attempts…” under All tasks files the tasks you have under attempts, "
                        "or choose “Start a new attempt…” in the task form."]
    assert en_none == ["No attempt holds a task yet. In the task form, choose “Start a new attempt…” and write the first task."]
    # attempts, but no task loose: the sentence that sends the person to an attempt stands (and a task of a listed attempt is not loose)
    kept = _run(_now(root), "listed.tasks = [{ id: 't1', text: 'Read', owner: '', due: '', done: false, attempt: listed.attempts[0].id }];\n" + scenario % "")["quiet"]
    assert kept == ["还没有挂着任务的尝试。在研究路线里打开一条尝试，选「在这条尝试下加任务」。"]


@needs_node
def test_a_section_the_engine_has_not_answered_is_not_drawn_and_a_read_that_fails_leaves_what_was_drawn(quant, pid):
    out = _run(_now(quant), r"""
customGet = (path) => { if (path === '/api/workspace/now') throw new Error('down'); };
await window.ROWorkspace.load(root); await sleepTick();
const first = cls('ws-now')[0].classList.contains('hidden');
customGet = (path) => (path === '/api/workspace/now' ? clone(NOW) : undefined);
await window.ROWorkspace.load(root, true); await sleepTick();
const drawn = cards().length;
customGet = (path) => { if (path === '/api/workspace/now') throw new Error('down'); };
await window.ROWorkspace.load(root, true); await sleepTick();
console.log(JSON.stringify({ first, drawn, kept: cards().length, hidden: cls('ws-now')[0].classList.contains('hidden') }));
""")
    assert out["first"] is True and out["drawn"] == 4 and out["kept"] == 4 and out["hidden"] is False


# -- the stylesheet ---------------------------------------------------------------------------------------------


def test_the_two_parts_stand_side_by_side_at_desktop_width_and_stacked_when_narrow():
    css = (WEBAPP / "workspace.css").read_text(encoding="utf-8")
    assert re.search(r"\.ws-now-parts \{ display: grid; grid-template-columns: minmax\(0, 1\.25fr\) minmax\(0, 1fr\);", css)
    narrow = css[css.index("@media (max-width: 1000px) {\n  .ws-now-parts"):]
    assert "grid-template-columns: minmax(0, 1fr)" in narrow.split("}")[0]
    assert not re.search(r"[→←➔▸]", css[css.index("现在在做什么"):])      # 12.11: no arrow glyphs in what is drawn


def test_the_route_pages_and_the_home_never_call_the_attempt_table_by_its_retired_name():
    """21.6, 21.9 item 6 (fix batch 3): 年表 is 尝试表 now. The row of 等你处理 for a line whose end is missing, which 项目首页 draws too,
    and the route map's own sentences say so, and the English table has no key of the old word."""
    for name in ("desk.js", "routemap.js", "workspace.js", "i18n.js"):
        assert "年表" not in (WEBAPP / name).read_text(encoding="utf-8"), name
    assert 'mapping_attempt_not_found: "连线的一端在尝试表里找不到了' in (WEBAPP / "app.html").read_text(encoding="utf-8")


def test_the_state_words_and_marks_that_carry_chinese_are_never_smaller_than_12_px():
    """21.8 (fix batch 3): the root is the browser's 16 px, so 0.74rem is 11.84 px. The state word of every task row, the chips of
    等你处理, the assessment label on each card, the marks that say whose a record is and the variable chips are 12 px at least."""
    html = (WEBAPP / "app.html").read_text(encoding="utf-8")
    assert re.search(r"html\s*,\s*body\s*\{[^}]*margin:\s*0", html) and not re.search(r"html\s*\{[^}]*font-size", html)      # rem is the 16 px default
    for selector in (r"\.status-label", r"\.own-mark", r"\.var-chip"):
        rule = re.search(selector + r" \{([^}]*)\}", html).group(1)
        size = float(re.search(r"font-size:\s*([\d.]+)rem", rule).group(1))
        assert size * 16 >= 12, selector


# -- the rows' actions ------------------------------------------------------------------------------------------


@needs_node
def test_answering_a_run_from_the_list_opens_the_tasks_reply_form_and_a_handback_opens_in_the_panel(quant, pid):
    out = _run(_now(quant), r"""
listed.tasks = [{ id: 'a1', text: 'Question a1', owner: 'agent:claude-code:sonnet', due: '', done: false, attempt: 'attempt:research/attempts.md#1' }];
setRuns([mkRun({ id: 'r-20261029T090000Z-aaaaaa', task_id: 'a1', state: 'needs_input', question: 'Which sample should I use?', can_reply: true })]);
await window.ROWorkspace.load(root); await sleepTick(); await sleepTick();
const answerRow = cls('desk-row', cls('ws-now-needs')[0])[0];
await button('回答', answerRow).click(); await sleepTick();
const reply = cls('ws-agent-reply');
const handbackRow = cls('desk-row', cls('ws-now-needs')[0])[1];
await button('打开交回', handbackRow).click(); await sleepTick();
console.log(JSON.stringify({ reply: reply.length, focusedReply: global.focused && global.focused.dataset && global.focused.dataset.key,
  panel: state.panelSubject && state.panelSubject.kind, got: gets.filter((p) => p.startsWith('/api/workspace/handback?')).length }));
""")
    assert out["reply"] == 1 and out["focusedReply"] == "agent:a1:reply"
    assert out["panel"] == "handback" and out["got"] == 1


@needs_node
def test_answering_a_run_that_asked_while_another_page_was_on_screen_reads_the_runs_and_opens_the_reply_form(quant, pid):
    """The run was still going when the home last read the runs and asked its question while the person was on 研究路线 (the
    home polls only while it is on screen); 回答 there brings the home back, whose runs are older than the list. The reply
    form opens all the same: the runs are read once more before the row is said to be one that cannot be answered. A run that
    really cannot be answered (answered elsewhere meanwhile) still only brings its row into view."""
    scenario = r"""
listed.tasks = [{ id: 'a1', text: 'Question a1', owner: 'agent:claude-code:sonnet', due: '', done: false, attempt: 'attempt:research/attempts.md#1' }];
const ask = (o) => mkRun({ id: 'r-20261029T090000Z-aaaaaa', task_id: 'a1', ...o });
setRuns([ask({ state: 'running', can_stop: true })]);
await window.ROWorkspace.load(root); await sleepTick(); await sleepTick();
setRuns([ask(%s)]);                                                         // what the engine says since
await button('回答', cls('desk-row', cls('ws-now-needs')[0])[0]).click();
for (let i = 0; i < 6; i++) await sleepTick();
console.log(JSON.stringify({ reply: cls('ws-agent-reply').length, focused: global.focused && global.focused.dataset && global.focused.dataset.key }));
"""
    asked = _run(_now(quant), scenario % "{ state: 'needs_input', question: 'Which sample should I use?', can_reply: true }")
    assert asked["reply"] == 1 and asked["focused"] == "agent:a1:reply"
    answered = _run(_now(quant), scenario % "{ state: 'ended', can_reply: false }")
    assert answered["reply"] == 0


@needs_node
def test_the_home_reads_again_a_few_times_when_the_shared_folder_was_too_slow_for_the_first_read(quant, pid):
    """21.4 (fix batch 3): the first answer of a slow shared folder holds a notice and none of the PI's proposals; the home asks
    again 5 s later, as 研究路线 does, at most three times, and stops once the folder answers (or says it cannot be read, which
    asking again would not change); a home that was left waits no more."""
    now = _now(quant)
    out = _run({**now, "shared": {"state": "slow"}}, r"""
const ok = { ...NOW, shared: { state: 'ok' } }, slow = clone(NOW);
const fire = async () => { await tick(); await sleepTick(); };
await window.ROWorkspace.load(root); await sleepTick();
const first = [nowAnswers, waiting()];
await fire();
const second = [nowAnswers, waiting()];
customGet = (path) => (path === '/api/workspace/now' ? (nowAnswers += 1, clone(ok)) : undefined);      // the folder has answered
await fire();
const healed = [nowAnswers, waiting()];
await fire();
console.log(JSON.stringify({ first, second, healed, later: [nowAnswers, waiting()] }));
""")
    assert out["first"] == [1, 1] and out["second"] == [2, 1] and out["healed"] == [3, 0] and out["later"] == [3, 0]
    capped = _run({**now, "shared": {"state": "stale"}}, r"""
await window.ROWorkspace.load(root); await sleepTick();
for (let i = 0; i < 6; i++) { await tick(); await sleepTick(); }
console.log(JSON.stringify([nowAnswers, waiting()]));
""")
    assert capped == [4, 0]      # the load and three more reads
    for state in ("unreadable", "ok", "not_needed", "not_configured"):
        assert _run({**now, "shared": {"state": state}}, "await window.ROWorkspace.load(root); await sleepTick(); console.log(JSON.stringify([nowAnswers, waiting()]));") == [1, 0]
    left = _run({**now, "shared": {"state": "slow"}}, r"""
await window.ROWorkspace.load(root); await sleepTick();
global.currentView = 'desk';           // the person went on to 研究路线: the desk reads the shared folder itself there
await tick(); await sleepTick();
console.log(JSON.stringify([nowAnswers, waiting()]));
""")
    assert left == [1, 0]


@needs_node
def test_a_sync_pressed_on_the_home_says_what_it_did_in_the_list_where_the_row_stood(quant):
    """21.4 (fix batch 3): the official version's row goes with the sync that brings it in, so what the sync did is told in its
    place in the list (the version the working copy is now at, and what was written), as 研究路线's column tells it; it stays
    until the desk is opened afresh and is not part of the count."""
    now = _now(quant)
    snapshot = {"seq": 7, "at": "2026-10-29T09:00:00Z", "note": "", "note_code": None}
    behind = {**now, "role": "member", "needs": {**now["needs"], "role": "member", "rows": [
        {"kind": "official", "key": "official", "at": snapshot["at"], "snapshot": snapshot}]}}
    synced = {**behind, "needs": {**behind["needs"], "rows": []}}
    out = _run(behind, r"""
global.ROTeam = { syncWrites: () => 'The sync writes two files.', syncFlash: (res) => [{ text: '已同步到第 7 版；更新了 2 个文件' }],
  differenceBlock: () => mk('div', 'diff') };
customGet = ((older) => (path) => {
  if (path === '/api/share/sync-preview') return { untrusted: false };
  if (path === '/api/desk') return { role: 'member', attempts: [], lines: [], lines_missing: [], needs: SYNCED.needs,
    column: { role: 'member', me: 'zhang@example.com', shared: { state: 'ok', message: null }, waiting: [], mine: [], review: { count: 0, groups: [], none: { items: [], cards: [] } },
      sync: { shared: true, reachable: true, behind: false, last_snapshot: SNAP } } };
  return older(path);
})(customGet);
customPost = (path) => (path === '/api/share/sync' ? (NOW.needs = SYNCED.needs, { updated: [] }) : undefined);
await window.ROWorkspace.load(root); await sleepTick(); await sleepTick();
const before = needsRows().map((r) => r.chip);
await button('同步').click();
for (let i = 0; i < 8; i++) await sleepTick();
const box = cls('ws-now-needs')[0];
console.log(JSON.stringify({ before, after: needsRows().map((r) => r.chip), done: text('team-done', box), shown: text('desk-sync-done', box).length,
  title: text('desk-what', box), count: sidebar['side-count-home'].textContent }));
""", extra=f"const SYNCED = {json.dumps({**synced, 'project': '/projects/study', 'id': 'p-study'}, ensure_ascii=False)};\nconst SNAP = {json.dumps(snapshot)};\n")
    assert out["before"] == ["正式版本有更新"] and out["done"] == ["已同步到第 7 版；更新了 2 个文件"] and out["shown"] == 1
    assert out["after"] == ["正式版本"] and out["count"] == ""      # the row that says what was done is not something that waits


@needs_node
def test_got_it_on_a_failed_run_is_kept_on_this_mac_and_the_list_and_the_count_follow_without_a_reload(quant, pid):
    """The click posts the run to the engine (kept per person in the Research OS home, never in the project); the home reads
    again, so the row and the sidebar's count go with it. The page is not reloaded."""
    now = _now(quant)
    run_id = next(r["run"] for r in _of(now, "failed"))
    aside = {**now, "needs": {**now["needs"], "rows": [r for r in now["needs"]["rows"] if r["kind"] != "failed"]}}
    out = _run(now, r"""
customPost = (path, body) => (path === '/api/workspace/run/aside' ? (NOW.needs = ASIDE.needs, { run: body.run, aside: true }) : undefined);
await window.ROWorkspace.load(root); await sleepTick();
const before = needsRows().length;
const gotIt = button('知道了', cls('desk-row', cls('ws-now-needs')[0])[2]);
await gotIt.click(); await sleepTick(); await sleepTick();
console.log(JSON.stringify({ before, after: needsRows().length, count: sidebar['side-count-home'].textContent, refreshed: refreshed.length,
  posted: posted.filter((p) => p[0] === '/api/workspace/run/aside') }));
""", extra=f"const ASIDE = {json.dumps({**aside, 'project': '/projects/study', 'id': 'p-study'}, ensure_ascii=False)};\n")
    assert out["before"] == 3 and out["after"] == 2 and out["count"] == "2" and out["refreshed"] >= 1
    assert out["posted"] == [["/api/workspace/run/aside", {"project": "/projects/study", "run": run_id}]]


@needs_node
def test_opening_a_handback_updates_the_count_without_a_reload(quant, pid):
    """Opening marks the handback opened for this person; the home reads the list again (the page's existing refresh), so the row
    and the count go."""
    now = _now(quant)
    report = _of(now, "handback")[0]["report"]
    view = handbacks.view(quant, pid, PERSON, report, "zh")      # the handback as the engine reads it
    opened = {**now, "needs": {**now["needs"], "rows": [r for r in now["needs"]["rows"] if r["kind"] != "handback"]}}
    out = _run(now, r"""
customPost = (path, body) => { if (path === '/api/workspace/handback/opened') { NOW.needs = OPENED.needs; return { report: body.report, opened: true }; } };
customGet = ((older) => (path) => (path.startsWith('/api/workspace/handback?') ? clone(VIEW) : older(path)))(customGet);
await window.ROWorkspace.load(root); await sleepTick();
const before = [needsRows().length, sidebar['side-count-home'].textContent];
await button('打开交回', cls('desk-row', cls('ws-now-needs')[0])[1]).click();
for (let i = 0; i < 6; i++) await sleepTick();
console.log(JSON.stringify({ before, after: [needsRows().length, sidebar['side-count-home'].textContent], refreshed: refreshed.length }));
""", extra=f"const OPENED = {json.dumps({**opened, 'project': '/projects/study', 'id': 'p-study'}, ensure_ascii=False)};\n"
                                     f"const VIEW = {json.dumps(view, ensure_ascii=False)};\n")
    assert out["before"] == [3, "3"] and out["after"] == [2, "2"] and out["refreshed"] >= 1


# -- a team project and a project of one ---------------------------------------------------------------------


@needs_node
def test_the_pi_sees_the_proposals_to_decide_in_the_same_list_and_the_count_holds_them_too(drive):
    """12.4 / 18.3: in a team project the list holds what the PI has to decide, drawn by the same code; the count is the list's."""
    verdict, add, remove = _two_people(drive)
    with drive.pi():
        now = _now(drive.official)
    out = _run(now, r"""
await window.ROWorkspace.load(root); await sleepTick();
console.log(JSON.stringify({ rows: needsRows(), count: sidebar['side-count-home'].textContent }));
""")
    assert len(out["rows"]) == 3
    assert all(r["buttons"] == ["查看"] for r in out["rows"]) and {r["chip"] for r in out["rows"]} == {"研究判断变更", "连线"}
    assert out["count"] == "3" and all("等你批准" in (r["meta"] or "") for r in out["rows"])
    assert all(r["what"] is not None for r in out["rows"])    # a title is words on this page: there is no map here to mark


@needs_node
def test_the_one_person_project_has_no_proposal_rows_and_still_has_its_count(tmp_path):
    from test_v10_desk_payload import _single

    root, _keys = _single(tmp_path)
    now = _now(root)
    out = _run(now, r"""
await window.ROWorkspace.load(root); await sleepTick();
console.log(JSON.stringify({ rows: needsRows(), count: sidebar['side-count-home'].textContent }));
""")
    assert [r["chip"] for r in out["rows"]] == ["待复核", "待复核", "连线的一端找不到了"] and out["count"] == "3"
    assert not [r for r in out["rows"] if "查看" in r["buttons"]]


# -- the column of 研究路线 ---------------------------------------------------------------------------------


@needs_node
def test_the_column_folds_when_nothing_waits_and_unfolds_when_a_row_arrives():
    """21.4: the desk's column is the same list, under 等你处理; empty it is a strip (the map or the list has its width), and the
    next reading that brings a row opens it again."""
    waiting = _team_case("pi")
    empty = _case("pi")
    case = {**waiting, "emptied": empty["desk"], "arrived": waiting["desk"]}
    out = run_desk(case, r"""
const column = () => layout().kids[layout().kids.length - 1];
const rowsIn = () => column().all().filter((k) => k.cls.has("desk-row")).length;
global.extra = { first: [column().className, rowsIn()] };
c.desk = c.emptied; await window.RODesk.loadDesk(views.desk, true); await settle();
extra.empty = [column().className, column().title, rowsIn(), column().all().filter((k) => k.cls.has("desk-column-title")).map((k) => k.textContent)];
c.desk = c.arrived; await window.RODesk.loadDesk(views.desk, true); await settle();
extra.again = [column().className, column().title, rowsIn()];
""")["extra"]
    assert out["first"] == ["desk-column", 3] and out["empty"] == ["desk-column folded", "现在没有要处理的事。", 0, ["等你处理"]]
    assert out["again"] == ["desk-column", "", 3]


def test_the_folded_strip_gives_its_width_to_the_map_and_is_a_bar_when_the_window_is_narrow():
    html = (WEBAPP / "app.html").read_text(encoding="utf-8")
    assert ".desk-column.folded { width: 2.4rem;" in html and "writing-mode: vertical-rl" in html
    narrow = html[html.index("@media (max-width: 960px) {\n  .desk-layout"):]
    assert ".desk-column.folded { width: auto; padding:" in narrow.split("</style>")[0] and "writing-mode: horizontal-tb" in narrow


@needs_node
def test_answering_a_run_from_the_list_sends_the_answer_and_the_list_and_the_count_follow_without_a_reload(quant, pid):
    """回答 opens the task's reply form; sent, the home reads again by the path a finished run takes (`reloadAfterRun`), so the
    row and the sidebar's count go with the question."""
    now = _now(quant)
    answered = {**now, "needs": {**now["needs"], "rows": [r for r in now["needs"]["rows"] if r["kind"] != "answer"]}}
    out = _run(now, r"""
listed.tasks = [{ id: 'a1', text: 'Question a1', owner: 'agent:claude-code:sonnet', due: '', done: false, attempt: 'attempt:research/attempts.md#1' }];
setRuns([mkRun({ id: 'r-20261029T090000Z-aaaaaa', task_id: 'a1', state: 'needs_input', question: 'Which sample should I use?', can_reply: true })]);
customPost = (path) => { if (path.endsWith('/reply')) NOW.needs = ANSWERED.needs; };
await window.ROWorkspace.load(root); await sleepTick(); await sleepTick();
const before = [needsRows().length, sidebar['side-count-home'].textContent];
await button('回答', cls('desk-row', cls('ws-now-needs')[0])[0]).click(); await sleepTick();
typed(cls('form-textarea', cls('ws-agent-reply')[0])[0], 'Use the full sample.');
await button('发送', cls('ws-agent-reply')[0]).click();
for (let i = 0; i < 8; i++) await sleepTick();
console.log(JSON.stringify({ before, after: [needsRows().length, sidebar['side-count-home'].textContent], nowReads: nowAnswers,
  replied: posted.filter((p) => p[0].endsWith('/reply')).map((p) => p[1].message) }));
""", extra=f"const ANSWERED = {json.dumps({**answered, 'project': '/projects/study', 'id': 'p-study'}, ensure_ascii=False)};\n")
    assert out["before"] == [3, "3"] and out["after"] == [2, "2"] and out["nowReads"] >= 2
    assert out["replied"] == ["Use the full sample."]


@needs_node
def test_approving_a_proposal_in_the_panel_from_the_home_reads_the_desk_and_the_page_again_and_the_count_follows():
    """A proposal opened from a row of the list on 项目首页 is answered in the panel (the desk's own renderer); the answer reads the
    desk first (the attempt's panel draws from it) and then the page on screen again, and the count is the new list's."""
    case = _team_case("pi")
    listed = case["desk"]["column"]["waiting"]
    case["after"] = {**case["desk"], "column": {**case["desk"]["column"], "waiting": listed[1:]}}
    case["after"] = desk_with_needs(case["after"])
    out = run_desk(case, r"""
function fake() { return { textContent: "", title: "", kids: [], dataset: {}, hidden: false, classList: { toggle() {} }, append(...xs) { this.kids.push(...xs); } }; }
global.shown = { "side-count-proposals": fake(), "side-count-home": fake(), "share-line": fake(), "me-line": fake(), "new-attempt-btn": fake() };
const parts0 = document.getElementById;
document.getElementById = (id) => shown[id] || parts0(id);
await window.RODesk.ensureContext();
global.currentView = "workspace"; global.refreshed = [];
global.refreshCurrentView = async () => { refreshed.push(1); };
window.RODesk.noteNeeds(c.desk.needs);
global.extra = { before: shown["side-count-home"].textContent };
const row = c.desk.needs.rows[0];
window.RODesk.openProposal(row.proposal); await settle(); await settle();
const desks = gets.filter((p) => p === "/api/desk").length;
answer("/api/proposals/p1/return", () => { c.desk = c.after; return { ok: true }; });
type(field("为什么退回：王 会看到这句话"), "先补数据");
button(panelPart("panel-body"), "退回并说明").click();
for (let i = 0; i < 8; i++) await settle();
extra.after = shown["side-count-home"].textContent;
extra.reads = [gets.filter((p) => p === "/api/desk").length - desks, refreshed.length];
""")["extra"]
    assert out["before"] == "3" and out["after"] == "2" and out["reads"] == [1, 1]

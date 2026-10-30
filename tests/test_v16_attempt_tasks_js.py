"""Research OS V16, task B1-1 (DESIGN.md 21.1, 21.4 'The task form'), the pages: an attempt holds its tasks.

The route map's card says, under its title and status, 「任务 n，未完成 m」, 「等你看 n」 and the first line of the
latest handback; the attempt's side panel lists the tasks in `after` order with their owner and state in words and
opens a handback; the task form on 项目首页 asks first which attempt a task belongs to and which task it follows, and
tasks stand under their attempt, those without one under 「未归入尝试」. The three scripts run under node on the
stand-in DOMs of the tests that already drive them, in both languages. Every test names its scenario."""

from __future__ import annotations

import json

import pytest

from test_v10_desk_panel import A16, _case, _st
from test_v10_desk_panel import _run as _run_panel
from test_v10_fix_map import _desk, _run_map
from test_v9_language import CJK, WEBAPP, _node, needs_node
from test_workspace_js import run as run_workspace

ATTEMPT = "attempt:research/attempts.md#1"


def _w(lang: str):
    return lambda zh, en: en if lang == "en" else zh


def _work(**o) -> dict:
    return {"tasks": 5, "unfinished": 2, "unopened": 1, "items": [],
            "latest": {"report": "research/results/" + "a" * 32 + ".md", "title": "Sword v0.2", "headline": "v0.2 beats v0.1.",
                       "line": "v0.2 beats v0.1.", "unopened": True}, **o}


# -- the route map's card ---------------------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_card_says_its_tasks_the_handbacks_waiting_and_the_first_line_of_the_latest_in_either_language(lang):
    w = _w(lang)
    data = _desk("pi")
    data["attempts"][0]["work"] = _work()
    data["attempts"][1]["work"] = _work(tasks=3, unfinished=0, unopened=0, latest=None)
    data["attempts"][2]["work"] = {"tasks": 0, "unfinished": 0, "unopened": 0, "latest": None, "items": []}
    out = _run_map(data, """
      const lines = (id) => ({ work: card(id).querySelector('.rm-work') && card(id).querySelector('.rm-work').all().map((k) => k.className + ': ' + k.textContent),
        headline: card(id).querySelector('.rm-headline') && card(id).querySelector('.rm-headline').textContent,
        hasWork: card(id).cls.has('has-work'), title: card(id).title });
      global.extra = { first: lines(%s), second: lines(%s), third: lines(%s) };
    """ % (json.dumps(A16), json.dumps("attempt:map.md#17"), json.dumps("attempt:map.md#18")), lang=lang)
    first, second, third = out["extra"]["first"], out["extra"]["second"], out["extra"]["third"]
    assert first["work"] == [w("rm-tasks: 任务 5，未完成 2", "rm-tasks: 5 tasks, 2 unfinished"), w("rm-look: 等你看 1", "rm-look: To read: 1")]
    assert first["headline"] == "v0.2 beats v0.1." and first["hasWork"]
    assert first["title"] == "#16 Route 16\n" + w("任务 5，未完成 2 / 等你看 1", "5 tasks, 2 unfinished / To read: 1") + "\nv0.2 beats v0.1."
    # nothing waits and everything is done: the count says so, with no 等你看 and no headline of a handback not there
    assert second["work"] == [w("rm-tasks: 任务 3，全部完成", "rm-tasks: 3 tasks, all done")] and second["headline"] is None
    # an attempt with no tasks has the card it always had
    assert third == {"work": None, "headline": None, "hasWork": False, "title": third["title"]}
    assert "任务" not in third["title"] and "task" not in third["title"].lower()


@needs_node
def test_a_card_with_one_task_reads_in_the_singular_in_english_and_the_unfinished_count_is_never_zero_in_the_chinese_line():
    data = _desk("single")
    data["attempts"][0]["work"] = _work(tasks=1, unfinished=1, unopened=0, latest=None)
    out = _run_map(data, "global.extra = card(%s).querySelector('.rm-tasks').textContent;" % json.dumps(A16), lang="en")
    assert out["extra"] == "1 task, 1 unfinished"
    out = _run_map(data, "global.extra = card(%s).querySelector('.rm-tasks').textContent;" % json.dumps(A16))
    assert out["extra"] == "任务 1，未完成 1"


@needs_node
def test_the_work_lines_do_not_draw_after_and_no_line_is_added_to_the_map():
    data = _desk("single", lines=[("attempt:map.md#17", "attempt:map.md#16")])
    data["attempts"][0]["work"] = _work(items=[{"id": "a", "after": None}, {"id": "b", "after": "a"}])
    out = _run_map(data, "global.extra = root.querySelectorAll('.rm-line').length;")
    assert out["extra"] == 1  # the one 源自 line; the order of tasks inside an attempt is not a line


def _layout(attempts: list[dict], lines=()) -> dict:
    drawn = json.dumps([{"child": c, "parent": p} for c, p in lines])
    script = "global.window = {};\n" + (WEBAPP / "routemap.js").read_text(encoding="utf-8")
    script += (f"\nconst R = window.RORouteMap; process.stdout.write(JSON.stringify([R._layoutRoutes({json.dumps(attempts)}, {drawn}, "
               "{ fixed: {} }), R._geometry]));\n")
    got = _node(script)
    return {**got[0], "geometry": got[1]}


@needs_node
def test_cards_that_say_their_tasks_are_laid_out_for_their_height_and_a_map_without_tasks_is_laid_out_as_before():
    ids = [f"attempt:map.md#{n}" for n in (16, 17, 18, 19)]
    plain = _layout([{"id": i} for i in ids], [(ids[1], ids[0]), (ids[2], ids[0]), (ids[3], ids[0])])
    rich = _layout([{"id": i, "work": {"tasks": 2}} for i in ids], [(ids[1], ids[0]), (ids[2], ids[0]), (ids[3], ids[0])])
    g = plain["geometry"]
    rows = lambda lay: sorted({p[1] for p in lay["positions"].values()})  # noqa: E731
    assert rows(plain) == [0, g["ROW_PITCH"], 2 * g["ROW_PITCH"]] and g["ROW_PITCH"] == g["CARD_H"] + 28
    assert rows(rich) == [0, g["WORK_PITCH"], 2 * g["WORK_PITCH"]] and g["WORK_PITCH"] == g["CARD_WORK_H"] + 28
    for lay in (plain, rich):
        height = g["CARD_WORK_H"] if lay is rich else g["CARD_H"]
        boxes = list(lay["positions"].values())
        assert not [1 for i, p in enumerate(boxes) for q in boxes[i + 1:]
                    if abs(p[0] - q[0]) < g["CARD_W"] and abs(p[1] - q[1]) < height]
    # the row of attempts with no line below the forest keeps clear of the taller cards too
    alone = _layout([{"id": ids[0], "work": {"tasks": 1}}, {"id": ids[1], "work": {"tasks": 1}}])
    assert alone["caption"][1] >= 0 and [p[1] for p in alone["positions"].values()] == [g["CAPTION_H"]] * 2


# -- the attempt's panel ------------------------------------------------------------------------------------------


def _items() -> list[dict]:
    report = "research/results/" + "a" * 32 + ".md"
    mk = lambda tid, text, state, **o: {"id": tid, "text": text, "owner": "", "owner_label": "", "due": "", "done": state == "done",  # noqa: E731
                                        "after": None, "report": None, "state": state, "unopened": False, **o}
    return [mk("a", "Collect the v0.2 data\nfrom the vendor", "done", owner="agent:claude-code:sonnet", owner_label="Claude Code · sonnet",
               report=report, due="2026-11-02"),
            mk("b", "Rerun with all members", "to_read", after="a", owner="Zhang San", owner_label="Zhang San", report=report, unopened=True),
            mk("c", "Write up", "not_started", after="b"), mk("d", "Check outliers", "running"),
            mk("e", "Which sample?", "needs_answer"), mk("f", "Fit the model", "failed"), mk("g", "Download prices", "out_of_bounds")]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_panel_lists_the_attempts_tasks_in_after_order_with_owner_state_in_words_and_a_way_to_open_the_handback(lang):
    w = _w(lang)
    case = _case("single", lang)
    case["desk"]["attempts"][0]["work"] = _work(tasks=7, unfinished=6, items=_items())
    out = _run_panel(case, """
      await window.RODesk.openAttempt(%s);
      global.opening = [];
      window.ROWorkspace = { openHandback: (path) => { opening.push(path); } };   // 21.2: the handback opens in its own panel
      global.startTaskOnHome = (choice) => { opening.push(choice); };
      const buttons = panelPart('panel-body').all().filter((k) => k.tag === 'BUTTON');
      buttons.filter((b) => b.kids.join('') === %s).forEach((b) => b.click());
      const add = buttons.find((b) => b.kids.join('') === %s); add.click();
      global.extra = opening;
    """ % (json.dumps(A16), json.dumps(w("打开交回", "Open handback")), json.dumps(w("在这条尝试下加任务", "Add a task under this attempt"))), lang=lang)
    lines = out["lines"]
    title = "node-sec-title: " + w("任务", "Tasks")
    assert title in lines
    section = lines[lines.index(title) + 1:]
    section = section[:next(i for i, line in enumerate(section) if line.startswith("node-sec-title: "))]
    states = [w("未开始", "Not started"), w("运行中", "Running"), w("等你回答", "Waiting for your answer"), w("等你看", "To read"),
              w("已完成", "Done"), w("失败", "Failed"), w("越过了限制", "Went out of bounds")]
    texts = [line for line in section if line.startswith("dp-task-text: ")]
    assert [t.split(" (title")[0] for t in texts] == ["dp-task-text: Collect the v0.2 data", "dp-task-text: Rerun with all members",
                                                     "dp-task-text: Write up", "dp-task-text: Check outliers", "dp-task-text: Which sample?",
                                                     "dp-task-text: Fit the model", "dp-task-text: Download prices"]  # `after` order, as given
    assert "(title: Collect the v0.2 data\nfrom the vendor)" in texts[0]  # the whole text is one hover away
    words = [line.split(": ", 1)[1] for line in section if line.startswith("status-label ")]
    assert words == [states[4], states[3], states[0], states[1], states[2], states[5], states[6]]
    assert [line for line in section if line.startswith("status-label tone-clay")] == [
        "status-label tone-clay: " + states[5], "status-label tone-clay: " + states[6]]  # what is wrong, and only that
    assert "desk-meta: Claude Code · sonnet · 2026-11-02" in section and "desk-meta: Zhang San" in section
    assert section.count("ws-text-link: " + w("打开交回", "Open handback")) == 2 and "btn btn-small: " + w("在这条尝试下加任务", "Add a task under this attempt") in lines
    report = "research/results/" + "a" * 32 + ".md"
    assert out["extra"] == [report, report, {"attempt": A16, "after": "g"}]   # the form defaults to the attempt's last task in `after` order
    if lang == "en":
        assert not [line for line in section if CJK.search(line) and "Collect" not in line]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_an_attempt_with_no_tasks_says_so_and_still_offers_the_way_to_add_one(lang):
    w = _w(lang)
    case = _case("single", lang)
    case["desk"]["attempts"][0]["work"] = {"tasks": 0, "unfinished": 0, "unopened": 0, "latest": None, "items": []}
    lines = _run_panel(case, "await window.RODesk.openAttempt(%s);" % json.dumps(A16), lang=lang)["lines"]
    assert "desk-empty: " + w("这条尝试下还没有任务。", "No tasks under this attempt yet.") in lines
    assert "btn btn-small: " + w("在这条尝试下加任务", "Add a task under this attempt") in lines
    # no task yet: nothing to continue; with one task, that task is the one the new task continues
    def asked() -> list:
        return _run_panel(case, """
          await window.RODesk.openAttempt(%s);
          global.opening = []; global.startTaskOnHome = (choice) => { opening.push(choice); };
          panelPart('panel-body').all().find((k) => k.tag === 'BUTTON' && k.kids.join('') === %s).click();
          global.extra = opening;
        """ % (json.dumps(A16), json.dumps(w("在这条尝试下加任务", "Add a task under this attempt"))), lang=lang)["extra"]

    assert asked() == [{"attempt": A16, "after": ""}]
    case["desk"]["attempts"][0]["work"] = _work(tasks=1, unfinished=0, items=_items()[:1])
    assert asked() == [{"attempt": A16, "after": "a"}]
    # a payload from an older engine (no `work` at all) draws the same
    del case["desk"]["attempts"][0]["work"]
    assert "desk-empty: " + w("这条尝试下还没有任务。", "No tasks under this attempt yet.") in _run_panel(
        case, "await window.RODesk.openAttempt(%s);" % json.dumps(A16), lang=lang)["lines"]


@needs_node
def test_someone_who_cannot_write_in_the_project_is_not_offered_the_way_to_add_a_task_and_the_handback_entry_opens_the_handback():
    case = _case("none")
    case["desk"]["attempts"][0]["work"] = _work(tasks=1, unfinished=0, items=_items()[:1])
    out = _run_panel(case, """
      await window.RODesk.openAttempt(%s);
      window.ROWorkspace = { openHandback: (path) => { global.extra = path; } };
      window.confirm = (q) => { asked.push(q); return false; };
      const open = panelPart('panel-body').all().find((k) => k.tag === 'BUTTON' && k.kids.join('') === '打开交回');
      open.click();
    """ % json.dumps(A16))
    assert "btn btn-small: 在这条尝试下加任务" not in out["lines"] and "ws-text-link: 打开交回" in out["lines"]
    assert out["extra"] == "research/results/" + "a" * 32 + ".md"  # nothing half typed: no question, the report opens


@needs_node
def test_back_from_a_handback_read_on_the_home_draws_the_attempts_panel_from_a_fresh_desk_reading():
    """21.4 (fix batch 3): on 项目首页 nothing reads the desk when a handback is read or confirmed (the home reloads itself),
    so 返回 reads it again before the attempt's panel is drawn; the task that was 等你看 reads 已完成 and the chip is the new
    assessment, as the engine now says. On 研究路线 the desk is read by the act itself and 返回 draws at once."""
    case = _case("single")
    report = "research/results/" + "a" * 32 + ".md"
    before = case["desk"]["attempts"][0]
    before["work"] = _work(tasks=1, unfinished=0, unopened=1, items=[
        {"id": "b", "text": "Rerun with all members", "owner": "", "owner_label": "", "due": "", "done": False, "after": None,
         "report": report, "state": "to_read", "unopened": True}])
    after = json.loads(json.dumps(case["desk"]))
    after["attempts"][0]["route_status"] = _st("current", "现行")
    after["attempts"][0]["tone"] = "olive"
    after["attempts"][0]["work"] = _work(tasks=1, unfinished=0, unopened=0, items=[{**before["work"]["items"][0], "state": "done", "unopened": True}])
    case["after"] = after
    steps = """
      global.currentView = %s;
      window.ROWorkspace = { openHandback: (path, options) => { c.desk = c.after; global.backStep = options.back; return null; } };
      await window.RODesk.openAttemptAnywhere(%s);
      const words = () => { const all = panelPart("panel-body").all(), mark = (list) => list.find((k) => k.cls.has("status-label")).textContent;
        return [mark(all), mark(all.find((k) => k.cls.has("dp-task")).all())]; };   // the head's chip, the task's state
      const seen = {first: words()};
      panelPart('panel-body').all().find((k) => k.tag === 'BUTTON' && k.kids.join('') === '打开交回').click();
      if (%s) await window.RODesk.loadDesk(views.desk, true);       // on the desk the act itself reads the desk again
      state.panelSubject = {kind: 'handback'};     // the handback stands in the panel
      global.deskReads = 0; const real = global.apiGet; global.apiGet = async (p) => { if (p === '/api/desk') deskReads += 1; return real(p); };
      backStep(); for (let i = 0; i < 6; i++) await settle();
      seen.after = words(); seen.reads = deskReads;
      global.extra = seen;
    """
    home = _run_panel(case, steps % ("'workspace'", json.dumps(A16), "false"))["extra"]
    assert home["first"] == ["待定", "等你看"]
    assert home["after"] == ["现行", "已完成"] and home["reads"] == 1
    desk = _run_panel(case, steps % ("'desk'", json.dumps(A16), "true"))["extra"]
    assert desk["after"] == home["after"] and desk["reads"] == 0       # the desk was read by the act: nothing is read twice


# -- the task form on 项目首页 ----------------------------------------------------------------------------------------


_TASKS = """[{id:'a',text:'Collect the data',owner:'Lin',due:'',done:false,attempt:'ATT'},
 {id:'b',text:'Rerun with all members',owner:'',due:'',done:false,attempt:'ATT',after:'a'},
 {id:'old',text:'Written before attempts',owner:'',due:'',done:false}]""".replace("ATT", ATTEMPT)


@needs_node
def test_the_form_asks_which_attempt_first_then_which_task_it_follows_and_the_task_stands_under_its_attempt():
    out = run_workspace(r"""
listed.attempts=[{id:ATT,label:'First check'},{id:'attempt:research/attempts.md#2',label:'Second idea'}];
listed.tasks=TASKS; listed.tasks_revision='tasks-1';
await window.ROWorkspace.load(root);
const labels=()=>byClass('ws-task-form').children.filter(e=>e.tagName==='LABEL').map(e=>e.children[0].textContent);
const form={labels:labels(), attempt:optionsOf(field('属于哪条尝试')), afterEnabled:!field('接着哪个任务').disabled, after:optionsOf(field('接着哪个任务'))};
const select=field('属于哪条尝试'); select.value=ATT; await select.emit('change');
const chosen={after:optionsOf(field('接着哪个任务')),afterEnabled:!field('接着哪个任务').disabled};
const second=field('属于哪条尝试'); second.value='attempt:research/attempts.md#2'; await second.emit('change');
const other={after:optionsOf(field('接着哪个任务')),afterEnabled:!field('接着哪个任务').disabled};
second.value=ATT; await second.emit('change');
const follows=field('接着哪个任务'); follows.value='b'; await follows.emit('change');
typed(field('任务'),'Write up'); await find('添加任务').click(); await sleepTick();
const saved=listed.tasks.find(t=>t.text==='Write up');
const groups=byClass('ws-task-list').children.filter(e=>e.tagName!=='BUTTON').map(e=>e.tagName==='H3'?'# '+e.textContent:e.all().find(x=>x.classList.contains('ws-task-text')).textContent);
console.log(JSON.stringify({form,chosen,other,saved,groups,
  keptAttempt:field('属于哪条尝试').value,after:field('接着哪个任务').value,text:field('任务').value,dirty:window.ROWorkspace.isDirty()}));
""".replace("ATT", json.dumps(ATTEMPT)).replace("TASKS", _TASKS))
    assert out["form"]["labels"][:3] == ["属于哪条尝试", "任务", "接着哪个任务"]  # the attempt is the first field, then the task (21.4)
    assert out["form"]["attempt"] == [["请选择一条尝试", ""], ["#1 First check", ATTEMPT], ["#2 Second idea", "attempt:research/attempts.md#2"],
                                      ["新开一条尝试…", "\u0000new"]]
    assert out["form"]["afterEnabled"] is False and out["form"]["after"] == [["不接着任何任务", ""]]
    assert out["chosen"] == {"after": [["不接着任何任务", ""], ["Collect the data", "a"], ["Rerun with all members", "b"]], "afterEnabled": True}
    assert out["other"] == {"after": [["不接着任何任务", ""]], "afterEnabled": True}  # no task there to follow yet
    assert out["saved"]["attempt"] == ATTEMPT and out["saved"]["after"] == "b" and out["saved"]["done"] is False
    # under its attempt in `after` order (a, b, the new one that follows b), the task without an attempt last
    assert out["groups"] == ["# #1 First check", "Collect the data", "Rerun with all members", "Write up", "# 未归入尝试", "Written before attempts"]
    assert out["keptAttempt"] == ATTEMPT and out["after"] == "" and out["text"] == "" and not out["dirty"]  # the next task starts under the same attempt


@needs_node
def test_a_new_task_without_an_attempt_is_not_sent_and_says_how_to_give_it_one_while_an_old_task_edits_without_one():
    out = run_workspace(r"""
listed.tasks=[{id:'old',text:'Written before attempts',owner:'',due:'',done:false}]; listed.tasks_revision='tasks-1';
await window.ROWorkspace.load(root);
typed(field('任务'),'A task with no attempt'); await find('添加任务').click(); await sleepTick();
const refused={posted:posted.length, words:byClass('ws-tasks').textContent, focus:focused===field('属于哪条尝试'), text:field('任务').value, dirty:window.ROWorkspace.isDirty()};
await find('清空').click();
await find('编辑',byClass('ws-task-actions')).click(); typed(field('任务'),'Written before attempts, revised'); await find('保存任务').click(); await sleepTick();
console.log(JSON.stringify({refused, edited:listed.tasks, editPosted:posted.length}));
""")
    assert out["refused"]["posted"] == 0 and out["refused"]["focus"] and out["refused"]["text"] == "A task with no attempt" and out["refused"]["dirty"]
    assert "新任务要属于一条尝试：请在「属于哪条尝试」里选一条，或选「新开一条尝试…」。" in out["refused"]["words"]
    assert out["edited"] == [{"id": "old", "text": "Written before attempts, revised", "owner": "", "due": "", "done": False}]
    assert out["editPosted"] == 1


@needs_node
def test_the_form_and_the_sentence_that_refuses_are_english_in_english_mode():
    out = run_workspace(r"""
const T=window.ROI18N.t;
listed.attempts=[{id:ATT,label:'First check'}]; listed.tasks=TASKS; listed.tasks_revision='tasks-1';
await window.ROWorkspace.load(root);
typed(field(T('任务',{n:1})),'No attempt chosen'); await find(T('添加任务')).click(); await sleepTick();
const refused=byClass('ws-tasks').textContent;
const labels=byClass('ws-task-form').children.filter(e=>e.tagName==='LABEL').map(e=>e.children[0].textContent);
console.log(JSON.stringify({refused, labels, attempt:optionsOf(field(T('属于哪条尝试'))), groups:byClass('ws-task-list').children.filter(e=>e.tagName==='H3').map(e=>e.textContent)}));
""".replace("ATT", json.dumps(ATTEMPT)).replace("TASKS", _TASKS), lang="en")
    assert out["labels"][:3] == ["Which attempt it belongs to", "Task", "Which task it follows"]
    assert out["attempt"] == [["Choose an attempt", ""], ["#1 First check", ATTEMPT], ["Start a new attempt…", "\u0000new"]]
    assert ("A new task belongs to an attempt: choose one in “Which attempt it belongs to”, or pick “Start a new attempt…”."
            in out["refused"])
    assert out["groups"] == ["#1 First check", "Not in an attempt"]
    assert not CJK.search(out["refused"].replace("Collect the data", "").replace("Rerun with all members", ""))


@needs_node
def test_start_a_new_attempt_opens_the_attempt_form_and_once_its_row_is_read_back_the_task_already_written_is_added_under_it():
    out = run_workspace(r"""
listed.tasks=[{id:'old',text:'Written before attempts',owner:'',due:'',done:false}]; listed.tasks_revision='tasks-1';
const opened=[]; let saved=null;
window.ROWorkspace.onCreateAttempt=(prefill,onSaved)=>{ opened.push(prefill); saved=onSaved; };
await window.ROWorkspace.load(root);
typed(field('任务'),'Survey the vendors');
const select=field('属于哪条尝试'); select.value='\u0000new'; await select.emit('change');
const before={opened:opened.length, value:field('属于哪条尝试').value, posted:posted.length, text:field('任务').value};
// the row is saved and read back: the page is given the attempt as the records hold it
await saved({id:'attempt:research/attempts.md#2',attrs:{number:'2',description:'Vendor survey'}},{workingCopy:false}); await sleepTick();
console.log(JSON.stringify({before, tasks:listed.tasks, value:field('属于哪条尝试').value, options:optionsOf(field('属于哪条尝试')),
  groups:byClass('ws-task-list').children.filter(e=>e.tagName==='H3').map(e=>e.textContent), posted:posted.map(p=>p[0])}));
""")
    assert out["before"] == {"opened": 1, "value": "", "posted": 0, "text": "Survey the vendors"}  # nothing is added before the row exists
    added = next(t for t in out["tasks"] if t["text"] == "Survey the vendors")
    assert added["attempt"] == "attempt:research/attempts.md#2" and "after" not in added
    assert out["value"] == "attempt:research/attempts.md#2"
    assert ["#2 Vendor survey", "attempt:research/attempts.md#2"] in out["options"]
    assert out["groups"] == ["#2 Vendor survey", "未归入尝试"] and out["posted"] == ["/api/workspace/tasks"]


@needs_node
def test_start_a_new_attempt_with_no_task_written_only_chooses_the_new_attempt_and_a_declined_form_changes_nothing():
    out = run_workspace(r"""
let saved=null;
window.ROWorkspace.onCreateAttempt=(prefill,onSaved)=>{ saved=onSaved; };
await window.ROWorkspace.load(root);
const select=field('属于哪条尝试'); select.value='\u0000new'; await select.emit('change');
const declined={value:field('属于哪条尝试').value, posted:posted.length, dirty:window.ROWorkspace.isDirty()};
await saved({id:'attempt:research/attempts.md#2',attrs:{number:'2',description:'Vendor survey'}},{workingCopy:true}); await sleepTick();
console.log(JSON.stringify({declined, value:field('属于哪条尝试').value, posted:posted.length, dirty:window.ROWorkspace.isDirty()}));
""")
    assert out["declined"] == {"value": "", "posted": 0, "dirty": False}
    assert out["value"] == "attempt:research/attempts.md#2" and out["posted"] == 0 and out["dirty"] is False


@needs_node
def test_the_attempts_panel_starts_a_task_with_its_attempt_chosen_and_asks_before_it_replaces_a_task_half_written():
    out = run_workspace(r"""
listed.tasks=TASKS; listed.tasks_revision='tasks-1';
await window.ROWorkspace.load(root);
const first=window.ROWorkspace.startTask({attempt:ATT});
const chosen={ok:first, value:field('属于哪条尝试').value, focus:focused===field('任务'), dirty:window.ROWorkspace.isDirty()};
typed(field('任务'),'Half a thought');
answers.push(false); const declined=window.ROWorkspace.startTask({attempt:ATT,after:'a'});
const kept={ok:declined, text:field('任务').value, questions:confirmQuestions.length};
answers.push(true); const taken=window.ROWorkspace.startTask({attempt:ATT,after:'a'});
console.log(JSON.stringify({chosen,kept,taken:{ok:taken, text:field('任务').value, attempt:field('属于哪条尝试').value, after:field('接着哪个任务').value}}));
""".replace("ATT", json.dumps(ATTEMPT)).replace("TASKS", _TASKS))
    assert out["chosen"] == {"ok": True, "value": ATTEMPT, "focus": True, "dirty": False}  # a chosen attempt is not unsaved writing
    assert out["kept"] == {"ok": False, "text": "Half a thought", "questions": 1}
    assert out["taken"] == {"ok": True, "text": "", "attempt": ATTEMPT, "after": "a"}


@needs_node
def test_a_task_sent_on_from_a_handback_has_that_handbacks_record_chosen_as_its_research_context_and_that_is_not_unsaved_writing():
    out = run_workspace(r"""
const REC='research/results/'+'a'.repeat(32)+'.md', GONE='research/results/'+'b'.repeat(32)+'.md';
listed.documents.push({path:REC,kind:'result',title:'Recheck',excerpt:''});
listed.tasks=TASKS; listed.tasks_revision='tasks-1';
await window.ROWorkspace.load(root);
const ctx=()=>field('研究上下文（可选）');
window.ROWorkspace.startTask({attempt:ATT,after:'a'});
const without={value:ctx().value, none:ctx().children[0].textContent};
const sent=window.ROWorkspace.startTask({attempt:ATT,after:'b',context:REC});
const chosen={ok:sent, value:ctx().value, shown:ctx().children.find(o=>o.value===REC).textContent, dirty:window.ROWorkspace.isDirty(), after:field('接着哪个任务').value};
window.ROWorkspace.startTask({attempt:ATT,after:'b',context:GONE});
const missing={value:ctx().value, option:ctx().children.some(o=>o.value===GONE)};
window.ROWorkspace.startTask({attempt:ATT,after:'b',context:REC});
ctx().value='research/results/'+'a'.repeat(32)+'.md'; await ctx().emit('change'); const same={dirty:window.ROWorkspace.isDirty()};
ctx().value=''; await ctx().emit('change'); const cleared={dirty:window.ROWorkspace.isDirty()};   // the person's own choice, none, is not writing either
window.ROWorkspace.startTask({attempt:ATT,after:'b',context:REC});
typed(field('任务'),'Repeat on delisted names'); await find('添加任务').click(); await sleepTick();
const added=listed.tasks.find(t=>t.text==='Repeat on delisted names');
console.log(JSON.stringify({without,chosen,missing,same,cleared,added:{attempt:added.attempt,after:added.after,context:added.context}, then:{value:ctx().value, dirty:window.ROWorkspace.isDirty()}}));
""".replace("ATT", json.dumps(ATTEMPT)).replace("TASKS", _TASKS))
    assert out["without"] == {"value": "", "none": "不关联报告"}                       # asked for without a record: as before
    assert out["chosen"] == {"ok": True, "value": "research/results/" + "a" * 32 + ".md", "shown": "Recheck", "dirty": False, "after": "b"}
    assert out["missing"] == {"value": "research/results/" + "b" * 32 + ".md", "option": True}   # a record the list does not have is still the one chosen
    assert out["same"] == {"dirty": False} and out["cleared"] == {"dirty": False}
    assert out["added"] == {"attempt": ATTEMPT, "after": "b", "context": {"report": "research/results/" + "a" * 32 + ".md"}}
    assert out["then"] == {"value": "", "dirty": False}                                   # the next blank form is blank again


@needs_node
def test_removing_a_task_hands_what_followed_it_to_what_it_followed_and_editing_a_task_never_offers_it_a_task_that_follows_it():
    out = run_workspace(r"""
listed.attempts=[{id:ATT,label:'First check'}];
listed.tasks=[{id:'a',text:'First',owner:'',due:'',done:false,attempt:ATT},{id:'b',text:'Second',owner:'',due:'',done:false,attempt:ATT,after:'a'},
              {id:'c',text:'Third',owner:'',due:'',done:false,attempt:ATT,after:'b'}]; listed.tasks_revision='tasks-1';
await window.ROWorkspace.load(root);
const rows=()=>byClass('ws-task-list').children.filter(e=>e.tagName==='DIV').length;
// editing `a`: `b` and `c` follow it (one through the other), so neither is offered
const key=(k)=>root.all().find(e=>e.dataset&&e.dataset.key===k);
await key('edit:a').click();
const offered=optionsOf(field('接着哪个任务')).map(o=>o[0]); await find('清空').click();
await key('remove:b').click(); await sleepTick();
console.log(JSON.stringify({offered, tasks:listed.tasks.map(t=>[t.id,t.after||null]), rows:rows()}));
""".replace("ATT", json.dumps(ATTEMPT)))
    assert out["offered"] == ["不接着任何任务"]
    assert out["tasks"] == [["a", None], ["c", "a"]] and out["rows"] == 2


# -- 「在这条尝试下加任务」 from the desk reaches the home's form ------------------------------------------------------


def _home_run(scenario: str) -> dict:
    """app.html's own view switch and `startTaskOnHome` next to the real workspace.js (as 「打开结果」 is driven)."""
    from test_outputs_ui import _HOME_PRELUDE
    from test_v9_language import _app_block, _app_function
    from test_workspace_js import DOM

    source = DOM + "\nwindow.ROI18N_SAVED=\"zh\";\n" + (WEBAPP / "i18n.js").read_text() + "\n" + (WEBAPP / "workspace.js").read_text()
    source += "\n" + _HOME_PRELUDE + _app_block("const SECTION_OF_VIEW", "const SECTION_TITLES") + "\n"
    for head in ("function sectionTitle(", "function mk(", "function mkButton(", "function activateView(", "async function startTaskOnHome("):
        source += _app_function(head) + "\n"
    source += "\n(async()=>{\n" + scenario + "\n})().catch(e=>{console.error(e.stack);process.exitCode=1});"
    return _node(source)


@needs_node
def test_adding_a_task_from_an_attempts_panel_shows_the_home_read_and_its_form_with_that_attempt_chosen():
    out = _home_run(r"""
global.currentView = 'desk'; views.desk.className = ''; root.className = 'hidden';
await startTaskOnHome({ attempt: 'attempt:research/attempts.md#1' });
console.log(JSON.stringify({view: currentView, loaded: root.dataset.loaded, attempt: field('属于哪条尝试').value,
  focus: focused === field('任务'), reads: gets.filter((p) => p === '/api/workspace').length}));
""")
    assert out == {"view": "workspace", "loaded": "1", "attempt": ATTEMPT, "focus": True, "reads": 1}


@needs_node
def test_adding_a_task_from_a_view_that_refuses_to_be_left_stays_put_and_opens_nothing():
    out = _home_run(r"""
global.currentView = 'desk'; views.desk.className = ''; root.className = 'hidden';
desk.leave = false;
await startTaskOnHome({ attempt: 'attempt:research/attempts.md#1' }); await sleepTick();
console.log(JSON.stringify({view: currentView, loaded: root.dataset.loaded || null, gets: gets.slice()}));
""")
    assert out == {"view": "desk", "loaded": None, "gets": []}

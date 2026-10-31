"""Research OS V16, fix batch 4 (DESIGN.md 21.6, 21.7; the review of task B4): one regression test per finding.

Each test names its scenario and fails on the batch as it was committed. The page's blocks run under node on the stand-in DOM
of the other panel tests, in both languages; the engine's two findings (the variable cards' last backup, the refusal that sends
the researcher to a page with no name field) run against the engine itself. The scan for retired words is `test_glossary.py`."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

import pytest

from researchos import inventory
from researchos.records import cards
from researchos.records import files as record_files
from researchos.records import variables as V

from test_agents_api import COMPLETE, api, api_me  # noqa: F401 -- the fake-agent server and its fixtures
from test_v10_desk_panel import _PANEL_DOM
from test_v16_attempt_tasks_js import _case, _run_panel
from test_v16_records_panel import _open_panel, used  # noqa: F401 -- the records panel's harness and its fixture
from test_v5_phase7 import live  # noqa: F401 -- the project the records panel's fixture reads
from test_v16_settings import LANGUAGE_BLOCK, SETTINGS_BLOCK, _SETTINGS_PRELUDE, _node, _page, _w
from test_v16_task_form_js import _SETUP, _js
from test_v9_language import APP_HTML, FILL_VARS_LINE, NODE, T_LINE, WEBAPP, _app_block, _app_function, _i18n_source, needs_node
from test_variable_cards import _new_confirmed, _project
from test_workspace_js import run as run_workspace


def _script(lang: str, parts: list[str], steps: str) -> dict:
    """`parts` (the page's blocks) on the stand-in DOM in `lang`, then `steps` (async; they fill `out`)."""
    i18n = f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source() + "\n" + FILL_VARS_LINE + "\n" + T_LINE
    script = ("global.window = {};\n" + i18n + "\n" + _PANEL_DOM + "\n" + "\n".join(parts)
              + "\n(async () => {\n const out = {};\n" + steps + "\n process.stdout.write(JSON.stringify(out));\n})();\n")
    done = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


_PAGE_GLOBALS = r"""
global.uiLang = () => (window.ROI18N ? window.ROI18N.lang : "zh");
global.panelTitle = panelPart("panel-title"); global.panelSubtitle = panelPart("panel-subtitle"); global.panelBody = panelPart("panel-body");
global.setFormStatus = (s, cls, text, err) => { s.className = "panel-status " + cls; s.textContent = text; s.err = err ? (err.messageZh || err.state || err.message) : null; };
global.tick = () => new Promise((r) => setTimeout(r, 0));
global.byClass = (host, c) => host.all().filter((k) => k.cls.has(c));
global.byTag = (host, tag) => host.all().filter((k) => k.tag === tag);
global.text = (e) => e.textContent;
global.calls = [];
"""

# -- 1. 联网查询 on 文献's 引用核对 tab -----------------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_turning_the_lookup_on_while_the_citation_check_tab_is_showing_draws_the_literature_page_again(lang):
    """文献 is on its 引用核对 tab, whose line says 「联网查询：已关闭」. The researcher turns the lookup on from the 设置 page: the
    page behind it is drawn again on that tab with the new answer (it was drawn again on the library tab only); with another
    view showing nothing is drawn."""
    out = _page(lang, SETTINGS_BLOCK, r"""
reply["/api/settings/llm"] = { base_url: "x", model: "y", saved: {}, where: "this_mac", host: "localhost" };
reply["/api/settings/lookup"] = { lookup: { enabled: false } };
litState.data = { lookup: { enabled: false } }; litState.tab = "citations"; currentView = "literature";
openSettingsPanel("lookup");
await tick();
const check = byTag(byClass(panelBody, "lit-setting")[0], "INPUT")[0];
reply["/api/citations/lookup-setting"] = { ok: true, lookup: { enabled: true } };
check.checked = true;
await check.on.change[0]({});
out.onCitations = [rendered.length, rendered[0], litState.tab];
currentView = "workspace";
check.checked = false; reply["/api/citations/lookup-setting"] = { ok: true, lookup: { enabled: false } };
await check.on.change[0]({});
out.elsewhere = rendered.length;
""")
    assert out["onCitations"] == [1, {"enabled": True}, "citations"] and out["elsewhere"] == 1


# -- 2. the open 新建变量卡 form outlives a redraw -----------------------------------------------------------------------

_VARIABLES_BLOCK = "\n".join([
    _app_block("const varState = {", "function varNote("), _app_function("async function loadVariablesView("),
    _app_function("function cardsBlocked("), _app_function("function newCardBlock("), _app_function("function storageDetails("),
    _app_function("function chevronSummary("), _app_function("function renderVariableList("),
    # the stand-in DOM stubs `humanRecordsBlocked` as a global; the page's own function takes its place
    _app_function("function humanRecordsBlocked(").replace("function humanRecordsBlocked(", "global.humanRecordsBlocked = function (", 1)])

_VARIABLES_PRELUDE = _PAGE_GLOBALS + r"""
views.variables = el("div", "host"); global.currentView = "variables"; global.clearProjectState = () => {};
window.scrollY = 0; window.scrollTo = () => {};
global.openedCards = []; global.openVariableCard = (id) => openedCards.push(id);
global.chevronIcon = () => el("svg", "res-chevron");
global.apiPost = async (path, body) => { calls.push(["POST", path, body]); return { ok: true, id: body.id, generation: 7 }; };
global.lastGeneration = 0;
global.answer = { cards: [] };
global.apiGet = async () => answer;
global.form = () => byClass(views.variables, "var-new-form")[0];
global.idField = () => byClass(form(), "form-input")[0];
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_refresh_of_the_variables_page_keeps_the_open_new_card_form_the_id_typed_and_the_caret(lang):
    """The generation moves (an agent run changes files every two seconds) while the researcher types an id into 新建变量卡: the
    page is drawn again, and the form is open with the same id and the id field has the focus again. 取消 puts it away for
    good, creating the card leaves nothing behind, and a project switch forgets what was typed."""
    out = _script(lang, [_VARIABLES_BLOCK, _VARIABLES_PRELUDE], r"""
const T = window.ROI18N.t;
await loadVariablesView();
out.first = [form().cls.has("hidden"), idField().value];
byClass(views.variables, "btn")[0].click();
idField().value = "half_typed"; (idField().on.input || []).forEach((f) => f({}));
document.activeElement = idField(); global.focused = null;
await loadVariablesView();   // pollGeneration -> refreshCurrentView -> loadVariablesView
out.refreshed = [form().cls.has("hidden"), idField().value, global.focused === idField()];
// a refresh while the id field is not the focus leaves the focus where it is
document.activeElement = null; global.focused = null;
await loadVariablesView();
out.unfocused = [form().cls.has("hidden"), idField().value, global.focused];
// 取消 closes it, and the next refresh does not open it again
const cancel = byClass(form(), "var-new-actions")[0].kids[1];
cancel.click();
await loadVariablesView();
out.cancelled = [form().cls.has("hidden")];
// creating the card: what was typed is not kept for the next time the list is drawn
byClass(views.variables, "btn")[0].click();
idField().value = "rv"; (idField().on.input || []).forEach((f) => f({}));
await form().on.submit[0]({ preventDefault() {} });
await tick();
await loadVariablesView();
out.created = [openedCards, form().cls.has("hidden"), idField().value];
""")
    assert out["first"] == [True, ""]
    assert out["refreshed"] == [False, "half_typed", True]
    assert out["unfocused"] == [False, "half_typed", None]
    assert out["cancelled"] == [True]
    assert out["created"] == [["rv"], True, ""]


def test_the_page_resets_the_new_card_form_with_the_rest_of_the_variables_state_when_the_project_changes():
    """The switch to another project (app.html's resetProject block) clears varState; the new-card form's state is part of it."""
    src = APP_HTML.read_text(encoding="utf-8")
    block = src[src.index("  varState.card = null;\n  varState.version = null;\n  varState.asking = null;"):]
    block = block[:block.index("RCECanvas.reset();")]
    assert "varState.newCard.open = false;" in block and 'varState.newCard.id = "";' in block


# -- 3. 备份 panel keeps what was opened and read across a redraw ----------------------------------------------------------


@needs_node
@pytest.mark.parametrize("language", [None, "en"])
def test_a_refresh_of_the_backups_panel_keeps_the_folds_the_researcher_opened_the_finder_answer_and_the_scroll(used, language):  # noqa: F811
    """Opened while an agent runs: the researcher unfolds 详情 of one record and reads where it is kept, presses 在 Finder 中显示
    and reads 「已在 Finder 中显示。」 on another, and scrolls. The generation moves and revalidatePanel draws the panel again
    from the same rows: the same folds are open, the answer stands on its line, the scroll is where it was; a fold nobody
    opened is closed and a line nobody pressed says nothing."""
    steps = r"""
    const desc =Object.getOwnPropertyDescriptor(panelBody, "innerHTML");   // the browser clamps the scroll when the content goes
    Object.defineProperty(panelBody, "innerHTML", { configurable: true, set(v) { desc.set.call(this, v); this.scrollTop = 0; } });
    const rowsNow = () => byClass(panelBody, "records-row");
    const withFold = rowsNow().filter((r) => byClass(r, "records-fold").length);
    const opened = withFold[0];
    const fold = byClass(opened, "records-fold")[0]; fold.open = true; (fold.on.toggle || []).forEach((f) => f({}));
    const pressed = withFold.find((r) => r !== opened && r.all().some((k) => k.tag === "BUTTON"));
    pressed.all().find((k) => k.tag === "BUTTON").click(); await settle();
    const untouched = withFold.find((r) => r !== opened && r !== pressed);
    panelBody.scrollTop = 240;
    const before = { opened: opened.dataset.code, pressed: pressed.dataset.code, untouched: untouched.dataset.code };
    await renderRecordsPanel(state.panelSubject);   // what revalidatePanel does when the generation moves
    const now = (code) => rowsNow().find((r) => r.dataset.code === code);
    global.extra = { before, openFold: byClass(now(before.opened), "records-fold")[0].open === true,
      untouchedFold: !!byClass(now(before.untouched), "records-fold")[0].open,
      said: text(byClass(now(before.pressed), "panel-status")[0]), quiet: text(byClass(now(before.untouched), "panel-status")[0]),
      scroll: panelBody.scrollTop, rows: rowsNow().length };
    """
    out = _open_panel(used, steps, language=language)
    extra = out["extra"]
    assert extra["openFold"] is True and extra["untouchedFold"] is False
    assert extra["said"] == ("Shown in Finder." if language == "en" else "已在 Finder 中显示。") and extra["quiet"] == ""
    assert extra["scroll"] == 240 and extra["rows"] == len(out["view"])


# -- 4, 5. the language choice on the 设置 page -----------------------------------------------------------------------------

_LANGUAGE_PRELUDE = r"""
window.location = { reload: () => calls.push(["reload"]) };
if (window.RODesk) global.RODesk = window.RODesk;
window.webkit = { messageHandlers: { rce: { postMessage: (m) => calls.push(["shell", m]) } } };
global.showHeaderError = (framing, err) => calls.push(["header", framing]);
reply["/api/settings/llm"] = { base_url: "x", model: "y", saved: {}, where: "this_mac", host: "localhost" };
reply["/api/settings/lookup"] = { lookup: { enabled: false } };
"""


def _language_page(lang: str, steps: str, *, desk: bool = False) -> dict:
    i18n = (f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()) + "\n" + FILL_VARS_LINE + "\n" + T_LINE
    dom = _PANEL_DOM + ("\n" + (WEBAPP / "desk.js").read_text(encoding="utf-8") if desk else "")
    script = (_SETTINGS_PRELUDE % {"i18n": i18n, "dom": dom, "block": SETTINGS_BLOCK + LANGUAGE_BLOCK}
              + "\n(async () => {\n const out = {};\n" + _LANGUAGE_PRELUDE + steps + "\n process.stdout.write(JSON.stringify(out));\n})();\n")
    return _node(script)


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_declining_the_leave_question_puts_the_language_choice_back_to_the_language_in_force(lang):
    """Unsaved work on 项目首页 (here the workspace's own question), 设置 opened beside it, the other language chosen, 取消 to
    the question: nothing is saved, nothing reloads, and the choice shows the language still in force (it showed the one
    refused, so choosing the other again asked once more and reloaded for no change)."""
    other = "en" if lang == "zh" else "zh"
    out = _language_page(lang, r"""
window.ROWorkspace.mayLeave = () => { calls.push(["asked"]); return false; };
openSettingsPanel("language");
await tick();
const select = byTag(byClass(panelBody, "settings-language")[0], "SELECT")[0];
select.value = OTHER;
await select.on.change[0]({});
await tick();
out.declined = [select.value, select.disabled, calls.map((c) => c[0] === "POST" ? c[1] : c[0])];
""".replace("OTHER", json.dumps(other)))
    chosen, disabled, log = out["declined"]
    assert (chosen, disabled) == (lang, False)
    assert log.count("asked") == 1 and "/api/settings/language" not in log and "reload" not in log


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_language_change_asks_before_it_drops_words_half_typed_in_the_settings_page_itself(lang):
    """Words typed into 本地模型 and not saved: choosing the other language reloaded the page with no question. It asks first
    (14.3, as opening a panel over it does); declining keeps the words and the language in force, agreeing saves and reloads;
    with nothing typed it does not ask; and when the workspace already asked, the page does not ask a second time."""
    other = "en" if lang == "zh" else "zh"
    out = _language_page(lang, r"""
openSettingsPanel("language");
await tick();
const form = byClass(panelBody, "settings-llm")[0], url = byTag(form, "INPUT")[0];
const select = byTag(byClass(panelBody, "settings-language")[0], "SELECT")[0];
const choose = async () => { select.value = OTHER; select.disabled = false; await select.on.change[0]({}); await tick(); };
const posted = () => calls.filter((c) => c[1] === "/api/settings/language").length;
await choose();                                           // nothing typed: no question
out.clean = [asked.length, posted()];
calls.length = 0; asked.length = 0;
url.value = "http://typed.example:1234/v1"; url.dataset.typed = "1";
answers.push(false);
await choose();                                           // typed words, declined
out.declined = [asked.length, posted(), select.value, url.value, calls.filter((c) => c[0] === "reload").length];
answers.push(true);
await choose();                                           // typed words, agreed
out.agreed = [asked.length, posted(), calls.filter((c) => c[0] === "reload").length];
// the workspace's own question is the one question when both have something unsaved
calls.length = 0; asked.length = 0;
window.ROWorkspace.isDirty = () => true; window.ROWorkspace.mayLeave = () => { asked.push("workspace"); return true; };
await choose();
out.once = asked.slice();
""".replace("OTHER", json.dumps(other)), desk=True)
    assert out["clean"] == [0, 1]
    assert out["declined"] == [1, 0, lang, "http://typed.example:1234/v1", 0]
    assert out["agreed"] == [2, 1, 1]
    assert out["once"] == ["workspace"]


# -- 6, 13. an attempt's panel reads the stored 「我」 as the screen's language does ---------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_an_attempts_panel_says_me_for_a_task_given_to_the_researcher_and_names_others_as_before(lang):
    """A task whose owner is the stored text 「我」 (no name set): 项目首页 reads it “Me” on an English screen and so does the
    attempt's panel (it showed the Chinese character); an agent and a named person read as they did."""
    w = _w(lang)
    case = _case("single", lang)
    base = {"due": "", "done": False, "after": None, "report": None, "state": "not_started", "unopened": False}
    items = [{**base, "id": "m", "text": "Read the three papers", "owner": "我", "owner_label": "我"},
             {**base, "id": "a", "text": "Collect the data", "owner": "agent:claude-code:sonnet", "owner_label": "Claude Code · sonnet"},
             {**base, "id": "z", "text": "Rerun", "owner": "Zhang San", "owner_label": "Zhang San", "due": "2026-11-02"}]
    case["desk"]["attempts"][0]["work"] = {"tasks": 3, "unfinished": 3, "unopened": 0, "latest": None, "items": items}
    lines = _run_panel(case, "await window.RODesk.openAttempt(%s);" % json.dumps(case["desk"]["attempts"][0]["id"]), lang=lang)["lines"]
    metas = [line for line in lines if line.startswith("desk-meta: ")]
    assert "desk-meta: " + w("我", "Me") in metas
    assert "desk-meta: Claude Code · sonnet" in metas and "desk-meta: Zhang San · 2026-11-02" in metas


# -- 7. the empty 变量 page when no card can be made ------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_empty_variables_page_points_at_the_button_only_when_it_is_drawn_and_words_the_blocked_case_for_cards(lang):
    """Opened read-only, or before the migration, with no card: the box above says why no card can be made (in the second case
    about variable cards, not link judgments), and the guide card does not tell the researcher to press a button that is not
    there. Where the button is drawn the guide card ends as it did."""
    w = _w(lang)
    out = _script(lang, [_VARIABLES_BLOCK, _VARIABLES_PRELUDE], r"""
const last = () => text(byClass(views.variables, "guide-card")[0].kids[byClass(views.variables, "guide-card")[0].kids.length - 1]);
const above = () => text(byClass(views.variables, "var-new")[0]);
const buttons = () => byClass(views.variables, "var-new")[0].kids.filter((k) => typeof k !== "string" && k.tag === "BUTTON").length;
state.summary = null;
await loadVariablesView(); out.open = [last(), buttons()];
state.summary = { read_only: true };
await loadVariablesView(); out.readOnly = [above(), last(), buttons()];
state.summary = { needs_migration: true };
await loadVariablesView(); out.migration = [above(), last(), buttons()];
""")
    ends = w("为真正进入模型、图表或论断的变量建卡。点上面的「新建变量卡」创建第一张。",
             "Make cards for the variables that really go into a model, a figure or a claim. Choose “New variable card” above to make the first.")
    blocked = w("为真正进入模型、图表或论断的变量建卡。这个项目现在不能新建变量卡，原因在上面。",
                "Make cards for the variables that really go into a model, a figure or a claim. This project cannot make a variable card right now; the reason is above.")
    assert out["open"] == [ends, 1]
    assert out["readOnly"] == [w("只读打开：不会写入任何东西", "Opened read-only: nothing is written"), blocked, 0]
    assert out["migration"] == [w("迁移后才能新建变量卡", "Variable cards can be made once the old records are migrated"), blocked, 0]


# -- 8. the refusal for an unreadable me.toml points at a page that has a name field ----------------------------------------


def test_an_unreadable_me_toml_refusal_sends_the_researcher_to_the_members_page_not_to_the_settings(api_me):  # noqa: F811
    """Pressing 运行 with a `me.toml` that cannot be read: the sentence (Chinese screen) says where a name can be written -- 成员,
    which has the 「我是谁」 form, and for a project of one person the 开始协作… that opens it -- not 设置, which holds no name."""
    from researchos import people

    api_me.scenario(COMPLETE)
    people.me_path().write_text("this is [not valid toml\n")
    status, error = api_me.post("/api/agents/runs", {"task": "t1"}, lang="zh")
    assert status == 409 and error["state"] == "agent_person_unreadable"
    assert "在设置里" not in error["message"] and "「成员」" in error["message"] and "开始协作" in error["message"]
    status, english = api_me.post("/api/agents/runs", {"task": "t1"}, lang="en")
    assert status == 409 and "me.toml" in english["detail"]


# -- 9. the 设置 page names where a project's own settings are -----------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_settings_page_says_where_each_project_setting_is_and_every_place_it_names_has_it(lang):
    """The closing sentence sent the researcher to the project menu for 输出目录, which is on 项目首页. It names both places, and
    the home's header does carry the 输出目录 button the sentence names."""
    w = _w(lang)
    out = _page(lang, SETTINGS_BLOCK, r"""
reply["/api/settings/llm"] = { base_url: "x", model: "y", saved: {}, where: "this_mac", host: "localhost" };
reply["/api/settings/lookup"] = { lookup: { enabled: false } };
openSettingsPanel();
await tick();
out.last = text(panelBody.kids[panelBody.kids.length - 1]);
""")
    assert out["last"] == w("每个项目自己的设置：输出目录在项目首页的「全部任务」里，其余在侧栏顶部的项目菜单里。",
                            "Each project’s own settings: output folders are under “All tasks” on Project home, the others in the project menu at the top of the sidebar.")
    workspace = (WEBAPP / "workspace.js").read_text(encoding="utf-8")
    assert 't("全部任务")' in workspace and 't("输出目录")' in workspace


# -- 10. the files chosen in the attempt form count as unsaved work -------------------------------------------------------------

_ATTEMPT_BLOCK = "\n".join([_app_function("function mayReplacePanel("), _app_function("function settingsPageLink("),
                            _app_function("function attemptDraftBlock(")])


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_changing_the_model_from_the_attempt_form_asks_first_when_files_are_chosen_for_the_draft(lang):
    """Up to five files are chosen for 生成草稿 and the model address is wrong: 在设置里修改 takes the side panel. The chosen
    files are chips, not typed words, so no question was asked and the selection was gone. The panel's leave guard (14.3,
    desk.js) counts a chosen file now: it asks, a no keeps the form, a yes opens the settings; with none chosen (or all taken
    off again) it does not ask."""
    out = _script(lang, [(WEBAPP / "desk.js").read_text(encoding="utf-8"), _PAGE_GLOBALS, _ATTEMPT_BLOCK, r"""
global.apiGet = async () => ({ base_url: "http://localhost:1234/v1", model: "qwen", saved: {}, where: "this_mac", host: "localhost" });
global.opened = []; global.openSettingsPanel = (section) => opened.push(section);
global.draftLanguage = () => null;
global.RODesk = window.RODesk;
const baseEl = el;   // the block reads an element's `children`, which the stand-in keeps as `kids`
el = function (tag, cls, text) {
  const e = baseEl(tag, cls, text);
  Object.defineProperty(e, "children", { get() { return this.kids.filter((k) => typeof k !== "string"); } });
  return e;
};
"""], r"""
const T = window.ROI18N.t;
const block = attemptDraftBlock({ description: el("textarea"), result: el("textarea") }, [], false, {});
panelBody.appendChild(block.el); openPanel(); await tick();
const buttons = () => block.el.all().filter((k) => k.tag === "BUTTON");
const path = block.el.all().find((k) => k.tag === "INPUT");
const link = buttons().find((b) => text(b) === T("在设置里修改")), add = buttons().find((b) => text(b) === T("加入"));
out.none = [window.RODesk.mayLeavePanel(), asked.length];
path.value = "code/run.py"; add.click();
path.value = "out/t.csv"; add.click();
answers.push(false); link.click();
out.declined = [asked.length, opened.slice()];
answers.push(true); link.click();
out.agreed = [asked.length, opened.slice()];
const chips = () => buttons().filter((b) => b.cls.has("chip"));
while (chips().length) chips()[0].click();
asked.length = 0;
out.takenOff = [window.RODesk.mayLeavePanel(), asked.length];
""")
    assert out["none"] == [True, 0]
    assert out["declined"] == [1, []] and out["agreed"] == [2, ["llm"]]
    assert out["takenOff"] == [True, 0]


# -- 12. a stored 「我」 in a team ------------------------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_in_a_team_a_task_left_to_me_in_the_solo_period_reads_as_the_first_researcher_and_me_is_not_offered(lang):
    """A task given to 「我」 while the project was one person's, then a team: a member's 项目首页 read it “Me” beside the member's
    own tasks, as if it were theirs. Where the project is a team's the stored text reads as the project's first researcher; a
    named person reads by name; and the owner control offers no 「我」 to anyone who works in a team."""
    w = _w(lang)
    out = run_workspace(_js(_SETUP + r"""
listed.mode='member'; meInfo={state:'present',me:{id:'m-wang',name:'Wang Wei',email:'w@x.org'}};
shareInfo={role:'member',me:{id:'m-wang'},team:[{id:'m-lin',name:'Lin Ying',role:'pi'},{id:'m-wang',name:'Wang Wei',role:'member'}]};
listed.attempts=[{id:ATT,label:'First check'}];
listed.tasks=[{id:'old',text:'Read the three papers',owner:'我',due:'',done:false,attempt:ATT},
              {id:'mine',text:'Check the sample',owner:'Wang Wei',due:'',done:false,attempt:ATT}]; listed.tasks_revision='tasks-3';
await window.ROWorkspace.load(root); await sleepTick();
const owners=()=>root.all().filter(e=>e.classList.contains('ws-task-owner')).map(e=>e.textContent);
const member=owners();
window.ROWorkspace.reset(); listed.mode='pi'; meInfo=null; shareInfo=null;
await window.ROWorkspace.load(root); await sleepTick();
const pi=owners(), offered=flatOptions(field(T('负责人'))).map(o=>o.value);
console.log(JSON.stringify({member,pi,offered}));
"""), lang=lang)
    assert out["member"] == [w("最初的研究者", "Original researcher"), "Wang Wei"]
    assert out["pi"][0] == w("最初的研究者", "Original researcher")
    assert "我" not in out["offered"]


# -- 16. one primary button on the empty 变量 page --------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_new_card_opener_goes_quiet_while_its_form_is_open_so_the_page_has_one_primary_button(lang):
    """With no cards the opener is the page's primary button; the form it opens holds 创建, also primary. While the form is
    open the opener is the quiet button, 取消 gives it back, and a redraw with the form open draws it quiet at once."""
    out = _script(lang, [_VARIABLES_BLOCK, _VARIABLES_PRELUDE], r"""
const primaries = () => byClass(views.variables, "btn-primary").map((b) => text(b));
const opener = () => byClass(views.variables, "var-new")[0].kids[0];
await loadVariablesView();
out.closed = opener().className;
opener().click();
out.open = [opener().className, primaries()];
await loadVariablesView();
out.redrawn = [opener().className, primaries()];
byClass(form(), "var-new-actions")[0].kids[1].click();
out.cancelled = opener().className;
""")
    create = _w(lang)("创建", "Create")
    assert out["closed"] == "btn btn-primary"
    assert out["open"] == ["btn btn-small", [create]]       # the opener is quiet; the form's 创建 is the one primary button
    assert out["redrawn"] == ["btn btn-small", [create]]
    assert out["cancelled"] == "btn btn-primary"


# -- 11. the variable cards' last backup looks at every file of every card --------------------------------------------------

BACKED_UP = datetime(2026, 10, 28, 9, 30, 0, 250000, tzinfo=timezone.utc)
OLDER = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _variables_row(root):
    return {row.code: row for row in inventory.inventory(None, root)}["variables"]


def test_a_card_that_is_only_a_draft_reads_as_backed_up_when_its_definition_was(tmp_path):
    """`researchos variable new` makes v1.toml and no log; the daily snapshot copies v1.toml. The variables row looked at the log's
    backups only, so the 你的记录与备份 line said 「还没有备份过」 for a card whose definition (the text the researcher edits) has a
    backup. It reads the time of that copy, taken from its name."""
    root = tmp_path / "project"
    _project(root)
    cards.new_card(root, "rv")
    assert _variables_row(root).payload()["backed_up_at"] is None   # nothing copied yet
    record_files.snapshot_now(root, V.variables_dir(root) / "rv" / "v1.toml", V.snapshot_subdir("rv"), now=lambda: BACKED_UP)
    row = _variables_row(root)
    assert row.snapshot.startswith("v1.toml.20261028T093000250000Z") and row.payload()["backed_up_at"] == "2026-10-28T09:30:00.250Z"


def test_the_newest_of_a_cards_log_version_files_and_pre_write_copies_is_the_row_s_last_backup(tmp_path):
    """A confirmed card whose log was backed up in January and whose v1.toml in October: the row reads October (it read the
    log's January). A copy taken before a write is a backup too, and a card's newest counts against the other cards'."""
    root = tmp_path / "project"
    _project(root)
    _new_confirmed(root)
    card = V.variables_dir(root) / "topicshift"
    sub = V.snapshot_subdir("topicshift")
    record_files.snapshot_now(root, card / "log.toml", sub, now=lambda: OLDER)
    assert _variables_row(root).snapshot.startswith("log.toml.20260102T030405")
    record_files.snapshot_now(root, card / "v1.toml", sub, now=lambda: BACKED_UP)
    assert _variables_row(root).payload()["backed_up_at"] == "2026-10-28T09:30:00.250Z"
    later = V.variables_dir(root).parent / "backups" / "variables" / "topicshift" / "log.toml.20261029T101112000000Z.write.toml"
    later.write_bytes((card / "log.toml").read_bytes())
    row = _variables_row(root)
    assert row.snapshot == later.name and row.payload()["backed_up_at"] == "2026-10-29T10:11:12.000Z"


# -- 14. the guides name the control the model server is set in -----------------------------------------------------------


@needs_node
def test_the_guides_send_the_reader_to_the_settings_page_for_the_model_server_not_to_a_control_new_attempt_no_longer_has():
    """New attempt lost its 「模型服务」 fold in B4-3: the address and the model are set on the 设置 page, under 本地模型（辅助填写）.
    README and the CLI reference said to open "Model server" in the form. They name the places the screen names, in the English the
    page's table gives them."""
    from test_v9_language import english_table

    table = english_table()
    settings, section, new_attempt, generate = (table[k] for k in ("设置", "本地模型（辅助填写）", "新增尝试", "生成草稿"))
    assert "模型服务" not in table and "Model server" not in table.values()   # the control is gone from the page
    root = APP_HTML.parents[3]
    for guide in ("README.md", "docs/cli-reference.md"):
        text = " ".join((root / guide).read_text(encoding="utf-8").replace("*", "").split())
        assert "Model server" not in text and "open Model" not in text, guide
        for name in (settings, section, new_attempt, generate):
            assert name in text, f"{guide} does not name {name!r}"

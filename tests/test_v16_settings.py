"""Research OS V16, task B4-3 (DESIGN.md 21.7): the 设置 page for this Mac, and 停止服务 naming the agents it ends.

The 设置 page holds the one form of each setting of this Mac -- AI 执行者, 本地模型（辅助填写）, 联网查询, 界面语言 -- with the
storage and the routes each always had; 项目首页, 文献 and the attempt form keep only a text link 「在设置里修改」 and, where a
setting is read, say where it stands. 停止服务's question names each agent run still going (task, agent and model) and says
what stopping does to it, as the engine does it. The engine's routes run over HTTP against fake agents; the page's blocks run
under node on the stand-in DOM of the other panel tests, in both languages. Every test names its scenario."""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

from researchos import literature, paths, settings
from researchos.agents import profiles
from researchos.webapp import server

from test_agents_api import Api, api  # noqa: F401 -- the fake-agent server and its fixture
from test_agents_manager import finish_step, init_step
from test_v10_desk_panel import _PANEL_DOM
from test_v9_language import CJK, FILL_VARS_LINE, NODE, T_LINE, _app_block, _app_function, _i18n_source, needs_node
from test_workspace_js import run as run_workspace


def _node(script: str):
    """The script under node, its JSON on stdout; a failure shows node's own message."""
    done = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


def _w(lang: str):
    return lambda zh, en: en if lang == "en" else zh


# -- the engine: the runs 停止服务 would end, and what ending them does -------------------------------------------------


def _going_run(api: Api, rel: str | None = None):
    """A run that writes `rel` (when given) and then waits; returns the file it waits for, and the run."""
    waiting = api.tmp / "never-appears"
    steps = [init_step()]
    if rel:
        steps.append({"op": "write", "path": rel, "text": "a,b\n1,2\n"})
    api.scenario(steps + [{"op": "wait_file", "path": str(waiting), "timeout": 60}, finish_step()])
    started = api.start()
    api.wait_running(started["id"])
    return waiting, started


def test_the_runs_the_engine_carries_out_are_listed_with_task_agent_and_model_and_nothing_when_none(api):
    """GET /api/agents/going: empty while nothing runs; with a run going, its task as the person wrote it, the agent and
    model as the rows name them, and that it is in the served project."""
    assert api.get("/api/agents/going") == (200, {"runs": []})
    _going_run(api)
    status, going = api.get("/api/agents/going")
    assert status == 200 and len(going["runs"]) == 1
    run = going["runs"][0]
    assert (run["task"], run["label"], run["runner"], run["model"], run["here"]) == \
        ("Analyse the sample", "Claude Code · sonnet", "claude-code", "sonnet", True)
    assert run["project"] == api.root.name and run["started"]
    # a run of a project that is not the one served says its folder, not "here"
    elsewhere = server.ServedProject(root=api.tmp / "other", project_id=None)
    assert server.going_payload(api.httpd.agents, elsewhere)["runs"][0]["here"] is False
    assert server.going_payload(api.httpd.agents, server.ServedProject(root=api.root, no_project=True))["runs"][0]["here"] is False


def test_the_list_of_running_agents_is_read_with_no_project_served(tmp_path, monkeypatch):
    """The question before 停止服务 is the engine's, not a project's: it answers with no project served."""
    a = Api(tmp_path, monkeypatch, with_project=False)
    try:
        assert a.get("/api/agents/going") == (200, {"runs": []})
    finally:
        a.close()


def test_stopping_the_service_ends_the_program_records_the_run_interrupted_and_leaves_what_it_changed(tmp_path, monkeypatch):
    """What the question says, as the engine does it: stopping ends the agent's program, the run is recorded as 已中断
    (`interrupted`, `shutdown`), and a file it had already written stays where it is -- nothing is undone for the person."""
    a = Api(tmp_path, monkeypatch)
    try:
        _waiting, started = _going_run(a, "out/partial.csv")
        run = a.run_state(started["id"])
        pid = run["pid"]
        assert pid and (a.root / "out" / "partial.csv").exists()
    finally:
        a.close()   # the service stops: server_close ends every run it carries out
    final = a.run_state(started["id"])
    assert final["state"] == "interrupted" and final["error"]["code"] == "shutdown" and final["ended"]
    with pytest.raises(ProcessLookupError):   # the program, and what it started, are gone
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            os.kill(pid, 0)
            time.sleep(0.05)
        os.kill(pid, 0)
    assert (a.root / "out" / "partial.csv").read_text() == "a,b\n1,2\n"   # not reverted
    assert "out/partial.csv" in final["changes"]["created"]


# -- 联网查询: this Mac's setting, read with no project and kept in the machine's settings -----------------------------


def test_the_online_lookup_is_read_and_written_as_a_setting_of_this_mac_with_no_project_served(tmp_path, monkeypatch):
    """GET /api/settings/lookup reads what POST /api/citations/lookup-setting writes: `settings.json` under the Research OS
    home, one key, shared by every project. Both answer with no project served; no project file is touched."""
    a = Api(tmp_path, monkeypatch, with_project=False)
    try:
        assert a.get("/api/settings/lookup") == (200, {"lookup": {"enabled": False}})
        assert a.call("POST", "/api/citations/lookup-setting", {"on": True}) == (200, {"ok": True, "lookup": {"enabled": True}})
        assert a.get("/api/settings/lookup") == (200, {"lookup": {"enabled": True}})
        stored = json.loads(settings.settings_path().read_text(encoding="utf-8"))
        assert stored[literature.DOI_LOOKUP_SETTING] is True and settings.settings_path().parent == paths.researchos_home()
        assert a.call("POST", "/api/citations/lookup-setting", {"on": False})[1]["lookup"] == {"enabled": False}
        assert a.get("/api/settings/lookup")[1] == {"lookup": {"enabled": False}}
        assert a.call("POST", "/api/citations/lookup-setting", {"on": "yes"})[0] == 400
    finally:
        a.close()


# -- the page's blocks under node ------------------------------------------------------------------------------------

_SETTINGS_PRELUDE = r"""
global.window = {};
%(i18n)s
%(dom)s
global.uiLang = () => (window.ROI18N ? window.ROI18N.lang : "zh");
global.panelTitle = panelPart("panel-title"); global.panelSubtitle = panelPart("panel-subtitle"); global.panelBody = panelPart("panel-body");
global.setFormStatus = (s, cls, text, err) => { s.className = "panel-status " + cls; s.textContent = text; s.err = err ? (err.messageZh || err.state || err.message) : null; };
global.mayReplacePanel = () => true;
global.currentView = "workspace";
global.litState = { data: null, tab: "library" };
global.views = { literature: el("div") };
global.rendered = [];
global.renderLiterature = (host, data) => { rendered.push(data.lookup); };
const extra = { "lang-btn": el("button", "shutdown-btn", "English") };
const baseGet = document.getElementById;
document.getElementById = (id) => extra[id] || baseGet(id);
global.calls = [];
global.refuse = {};
global.reply = {};
global.apiGet = async (path) => { calls.push(["GET", path]); if (refuse[path]) throw refuse[path]; return reply[path]; };
global.apiPost = async (path, body) => { calls.push(["POST", path, body]); if (refuse[path]) throw refuse[path]; return reply[path] || { ok: true }; };
global.mounted = [];
window.ROWorkspace = { mayLeave: () => true, mountSettings: (box, subject) => { mounted.push([box.className, subject.kind, state.panelSubject === subject]); box.appendChild(el("p", "mounted", "[workspace.js draws the AI agents here]")); } };
global.ROWorkspace = window.ROWorkspace;
global.tick = () => new Promise((r) => setTimeout(r, 0));
global.byClass = (host, c) => host.all().filter((k) => k.cls.has(c));
global.byTag = (host, tag) => host.all().filter((k) => k.tag === tag);
global.text = (e) => e.textContent;
%(block)s
"""


def _page(lang: str, block: str, steps: str) -> dict:
    """`block` (a stretch of app.html) on the stand-in DOM in `lang`, then `steps` (async, may set `out`)."""
    i18n = (f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()) + "\n" + FILL_VARS_LINE + "\n" + T_LINE
    script = (_SETTINGS_PRELUDE % {"i18n": i18n, "dom": _PANEL_DOM, "block": block}
              + "\n(async () => {\n const out = {};\n" + steps + "\n process.stdout.write(JSON.stringify(out));\n})();\n")
    return _node(script)


SETTINGS_BLOCK = _app_block("// -- 设置 (DESIGN.md 21.7)", 'document.getElementById("settings-btn")')
LANGUAGE_BLOCK = (_app_block("// -- The language switch (DESIGN.md 13.2, 13.3)", "// -- Native shell bridge")
                  + _app_function("function postShellLanguage("))


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_settings_page_has_one_section_for_each_setting_of_this_mac_and_needs_no_project(lang):
    """Opened from the sidebar's foot with no project at all: the title and its subtitle, the four sections in order -- AI
    执行者, 本地模型（辅助填写）, 联网查询, 界面语言 --, the box workspace.js draws the agents' form in handed over with the
    panel's own subject, a line that per-project settings are in the project menu; nothing is asked of any project."""
    w = _w(lang)
    out = _page(lang, SETTINGS_BLOCK, r"""
state.noProject = true; state.currentId = null;
reply["/api/settings/llm"] = { base_url: "http://localhost:1234/v1", model: "qwen", saved: { base_url: null, model: null }, where: "this_mac", host: "localhost" };
reply["/api/settings/lookup"] = { lookup: { enabled: false } };
openSettingsPanel();
await tick();
out.title = [text(panelTitle), text(panelSubtitle)];
out.sections = byTag(panelBody, "SECTION").map((s) => [s.dataset.section, text(s.kids[0])]);
out.mounted = mounted;
out.last = text(panelBody.kids[panelBody.kids.length - 1]);
out.calls = calls.map((c) => c.join(" ")).sort();
out.shown = panelPart("panel").cls.has("hidden") ? "hidden" : "shown";
""")
    assert out["title"] == [w("设置", "Settings"), w("这台电脑上的设置，所有项目共用", "Settings of this Mac, shared by every project")]
    assert out["sections"] == [["agents", w("AI 执行者", "AI agent")], ["llm", w("本地模型（辅助填写）", "Local model (draft with a model)")],
                               ["lookup", w("联网查询", "Online lookup")], ["language", w("界面语言", "Interface language")]]
    assert out["mounted"] == [["ws-agent-settings", "settings", True]]
    assert out["last"] == w("每个项目自己的设置：输出目录在项目首页的「全部任务」里，其余在侧栏顶部的项目菜单里。",
                            "Each project’s own settings: output folders are under “All tasks” on Project home, the others in the project menu at the top of the sidebar.")
    assert out["calls"] == ["GET /api/settings/llm", "GET /api/settings/lookup"]
    assert out["shown"] == "shown"


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_model_form_shows_the_saved_values_over_the_ones_in_effect_and_saves_both_through_the_old_route(lang):
    """本地模型（辅助填写）: each field shows what is saved here with the setting in effect as its placeholder; 保存 (or Enter)
    sends both to POST /api/settings/llm, an empty one meaning the default; the leave guard counts only words not saved yet;
    a refusal says so in words with the engine's reason kept for 「详情」."""
    w = _w(lang)
    out = _page(lang, SETTINGS_BLOCK, r"""
reply["/api/settings/llm"] = { base_url: "http://studio.local:1234/v1", model: "qwen", saved: { base_url: "http://studio.local:1234/v1", model: null },
  where: "elsewhere", host: "studio.local" };
reply["/api/settings/lookup"] = { lookup: { enabled: false } };
openSettingsPanel("llm");
await tick();
const form = byClass(panelBody, "settings-llm")[0];
const [url, model] = byTag(form, "INPUT");
const save = byTag(form, "BUTTON")[0];
out.fields = [url.value, url.placeholder, model.value, model.placeholder, url.dataset.initial, model.dataset.initial];
out.labels = byTag(form, "LABEL").map(text);
out.saveLabel = text(save);
model.value = "m2"; model.dataset.typed = "1";
reply["/api/settings/llm"] = { base_url: "http://localhost:1234/v1", model: "m2", saved: { base_url: "http://studio.local:1234/v1", model: "m2" },
  where: "elsewhere", host: "studio.local" };
let prevented = false;
await model.on.keydown[0]({ key: "Enter", isComposing: false, keyCode: 13, preventDefault() { prevented = true; } });
await tick();
out.saved = [prevented, calls.filter((c) => c[0] === "POST").map((c) => [c[1], c[2]]), model.dataset.typed || null, model.dataset.initial,
  text(byClass(form, "panel-status")[0])];
refuse["/api/settings/llm"] = Object.assign(new Error("HTTP 400"), { state: "draft_bad_setting" });
url.value = "ftp://nope";
await save.on.click[0]({});
await tick();
const status = byClass(form, "panel-status")[0];
out.refused = [status.className, text(status), status.err];
delete refuse["/api/settings/llm"];
// the Enter that ends an input method's composition saves nothing
const before = calls.length;
await model.on.keydown[0]({ key: "Enter", isComposing: true, keyCode: 229, preventDefault() {} });
out.composing = calls.length - before;
""")
    assert out["fields"] == ["http://studio.local:1234/v1", "http://studio.local:1234/v1", "", "qwen", "http://studio.local:1234/v1", ""]
    assert out["labels"] == [w("服务地址", "Server address"), w("模型", "Model")] and out["saveLabel"] == w("保存", "Save")
    assert out["saved"] == [True, [["/api/settings/llm", {"base_url": "http://studio.local:1234/v1", "model": "m2"}]], None, "m2", w("已保存", "Saved")]
    assert out["refused"] == ["panel-status err", w("服务地址要用 http 或 https，写明主机，不带用户名和密码",
                                                    "The server address must use http or https, name a host and carry no user name or password"),
                              "draft_bad_setting"]
    assert out["composing"] == 0


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_lookup_form_reads_the_setting_of_this_mac_and_writes_it_through_the_old_route_and_the_literature_page_follows(lang):
    """联网查询: the box shows what GET /api/settings/lookup says; ticking it posts {on} to
    /api/citations/lookup-setting and says what happens next; the 文献 page, if it holds the project's answer, takes the new
    value and is drawn again; a refusal puts the box back. The section says the setting is this Mac's, shared by every project."""
    w = _w(lang)
    out = _page(lang, SETTINGS_BLOCK, r"""
reply["/api/settings/llm"] = { base_url: "x", model: "y", saved: {}, where: "this_mac", host: "localhost" };
reply["/api/settings/lookup"] = { lookup: { enabled: false } };
litState.data = { lookup: { enabled: false } }; currentView = "literature";
openSettingsPanel("lookup");
await tick();
const section = byTag(panelBody, "SECTION")[2];
const box = byClass(section, "lit-setting")[0];
const check = byTag(box, "INPUT")[0];
out.note = text(byClass(section, "settings-note")[0]);
out.start = [check.type, check.checked, check.disabled, text(byTag(box, "LABEL")[0]), text(byClass(box, "lit-setting-note")[0])];
reply["/api/citations/lookup-setting"] = { ok: true, lookup: { enabled: true } };
check.checked = true; check.dataset.typed = "1";   // the panel marks any field typed in; a tick is saved at once
await check.on.change[0]({});
const status = byClass(box, "panel-status")[0];
out.on = [check.checked, check.disabled, text(status), calls.filter((c) => c[0] === "POST").map((c) => [c[1], c[2]]), litState.data.lookup, rendered.length, check.dataset.typed || null];
refuse["/api/citations/lookup-setting"] = Object.assign(new Error("HTTP 500"), { state: "setting_failed" });
check.checked = false;
await check.on.change[0]({});
out.refused = [check.checked, check.disabled, status.className, text(status)];
""")
    assert out["note"] == w("这是这台电脑的设置，所有项目共用。", "This is a setting of this Mac, shared by every project.")
    assert out["start"] == ["checkbox", False, False, w("用 DOI 联网查文献信息", "Look up references online by DOI"),
                            w("只发送 DOI，不发送文稿内容。按需从 Crossref 和 OpenAlex 查询文献信息与摘要，结果保存在本机。",
                              "Only DOIs are sent. Reference details and abstracts are retrieved from Crossref and OpenAlex as needed and saved on this Mac.")]
    assert out["on"] == [True, False, w("已打开：下次扫描时查询", "Enabled: lookup on the next scan"),
                         [["/api/citations/lookup-setting", {"on": True}]], {"enabled": True}, 1, None]
    assert out["refused"][:3] == [True, False, "panel-status err"]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_language_choice_saves_what_the_footer_switch_saves_and_a_refusal_puts_it_back(lang):
    """界面语言: a choice of 中文 / English, each named in itself, showing the current language; choosing the other saves it
    through POST /api/settings/language, tells the shell and reloads -- the footer switch's own code --; a refusal says so on
    the page and the choice goes back to the language in use."""
    other = "en" if lang == "zh" else "zh"
    out = _page(lang, SETTINGS_BLOCK + LANGUAGE_BLOCK, r"""
reply["/api/settings/llm"] = { base_url: "x", model: "y", saved: {}, where: "this_mac", host: "localhost" };
reply["/api/settings/lookup"] = { lookup: { enabled: false } };
window.location = { reload: () => calls.push(["reload"]) };
window.webkit = { messageHandlers: { rce: { postMessage: (m) => calls.push(["shell", m]) } } };
global.showHeaderError = (framing, err) => calls.push(["header", framing]);
openSettingsPanel("language");
await tick();
const form = byClass(panelBody, "settings-language")[0];
const select = byTag(form, "SELECT")[0];
out.options = byTag(select, "OPTION").map((o) => [o.value, text(o), o.attrs.lang]);
out.current = select.value;
select.value = OTHER;
await select.on.change[0]({});
await tick();
out.saved = [calls.filter((c) => c[1] === "/api/settings/language").map((c) => c[2]), calls.filter((c) => c[0] === "shell" || c[0] === "reload").map((c) => c[0] === "shell" ? c[1].type : "reload")];
out.disabled = [select.disabled, extra["lang-btn"].disabled];
// a refusal: the choice goes back, the page says so, nothing reloads
calls.length = 0; select.disabled = false; extra["lang-btn"].disabled = false;
refuse["/api/settings/language"] = new Error("disk full");
select.value = OTHER;
await select.on.change[0]({});
await tick();
const status = byClass(form, "panel-status")[0];
out.refused = [select.value, select.disabled, status.className, text(status), calls.filter((c) => c[0] === "reload" || c[0] === "header").length];
out.shortcut = text(byClass(form, "settings-note")[0]);
""".replace("OTHER", json.dumps(other)))
    w = _w(lang)
    assert out["options"] == [["zh", "中文", "zh-CN"], ["en", "English", "en"]] and out["current"] == lang
    assert out["saved"] == [[{"language": other}], ["language", "reload"]] and out["disabled"] == [True, True]
    assert out["refused"] == [lang, False, "panel-status err", w("没能切换界面语言", "Could not switch the interface language"), 0]
    assert out["shortcut"] == w("侧栏下方的「English」和「中文」是同一个设置的快捷方式。",
                                "The “English” and “Chinese” switch at the foot of the sidebar is a shortcut to the same setting.")


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_literature_page_says_where_the_lookup_stands_and_links_to_the_settings_instead_of_holding_a_form(lang):
    """文献 carried the form twice (a fold on the library, the top of the citation check). It now says 「联网查询：已打开 / 已
    关闭」 with 「在设置里修改」, which opens the 设置 page at 联网查询; there is no checkbox on the page."""
    w = _w(lang)
    block = _app_function("function lookupSettingLine(") + _app_function("function settingsPageLink(")
    out = _page(lang, block, r"""
global.openedAt = [];
global.openSettingsPanel = (section) => openedAt.push(section);
const on = lookupSettingLine({ enabled: true }), off = lookupSettingLine({});
out.words = [text(on), text(off)];
out.inputs = byTag(on, "INPUT").length + byTag(off, "INPUT").length;
const link = byTag(off, "BUTTON")[0];
out.link = [text(link), link.cls.has("ws-text-link")];
link.click();
out.opened = openedAt;
""")
    assert out["words"] == [w("联网查询：已打开 在设置里修改", "Online lookup: on Change in Settings"),
                            w("联网查询：已关闭 在设置里修改", "Online lookup: off Change in Settings")]
    assert out["inputs"] == 0 and out["link"] == [w("在设置里修改", "Change in Settings"), True] and out["opened"] == ["lookup"]


def test_the_sidebar_foot_opens_the_settings_beside_backups_and_stop_service():
    """The foot: 备份, 设置, 停止服务 and the language switch; 设置 is a quiet text button of the neighbours' class that opens
    the page (guarded against half-typed words like the other panels), and shows with no project too."""
    html = _app_block('<div class="side-tools">', "</aside>")
    assert [m for m in __import__("re").findall(r'<button id="([\w-]+)" class="shutdown-btn"', html)] == \
        ["records-btn", "settings-btn", "shutdown-btn", "lang-btn"]
    assert ('<button id="settings-btn" class="shutdown-btn" type="button" title="这台电脑上的设置：AI 执行者、本地模型、联网查询和界面语言" '
            'data-i18n-attr="title" data-i18n>设置</button>') in html
    src = _app_block("// -- 设置 (DESIGN.md 21.7)", "// -- 变量 (9.11")
    assert 'document.getElementById("settings-btn").addEventListener("click", () => { if (mayReplacePanel()) openSettingsPanel(); });' in src
    assert "#settings-btn" not in _app_block("body.no-project #records-btn", "{")   # only 备份 is hidden with no project


# -- 停止服务 names the agents still going -------------------------------------------------------------------------------

STOP_BLOCK = _app_block("function renderStoppedState() {", "// -- The language switch (DESIGN.md 13.2, 13.3)")

_STOP_RUNS = [
    {"task": "Analyse the sample", "label": "Claude Code · sonnet", "runner": "claude-code", "model": "sonnet", "project": "study", "here": True},
    {"task": "Collect the data\nand clean it, then write the long report about everything that was found in the last week of work",
     "label": "OpenCode · lmstudio/qwen3-coder", "runner": "opencode", "model": "lmstudio/qwen3-coder", "project": "thesis", "here": False},
]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_stop_question_names_each_running_agent_and_says_what_stopping_does_and_without_one_asks_as_before(lang):
    """With two agent runs going (one in another project) the question names each by task, agent and model -- the task's
    first line, cut when long, the other project's folder named -- and says that their programs and everything those started
    are ended, that the runs are recorded 已中断, and that what they changed is not undone. With none it is the old question,
    unchanged; with the list unreadable it says it could not check and does not claim there is none."""
    w = _w(lang)
    out = _page(lang, _app_function("function goingRunLine(") + "\n" + _app_function("function stopServiceQuestion("),
                "const RUNS = " + json.dumps(_STOP_RUNS) + r"""
out.plain = stopServiceQuestion([]);
out.some = stopServiceQuestion(RUNS);
out.unknown = stopServiceQuestion(null);
out.one = stopServiceQuestion([RUNS[0]]);
""")
    ask = w("确定要停止本地服务吗？停止后这个页面将无法继续使用。", "Stop the local service? Once it stops, this page can no longer be used.")
    assert out["plain"] == ask
    assert out["unknown"] == w("没能查看是否还有 AI 执行者在运行。", "Could not check whether an AI agent is still running.") + "\n\n" + ask
    long_task = "Collect the data"
    lines = [w("「Analyse the sample」（Claude Code · sonnet）", "“Analyse the sample” (Claude Code · sonnet)"),
             w("「Collect the data」（OpenCode · lmstudio/qwen3-coder，项目 thesis）", "“Collect the data” (OpenCode · lmstudio/qwen3-coder, project thesis)")]
    effect = w("停止服务会结束它们的程序，以及程序启动的所有进程。这些运行会记为「已中断」。它们已经改动的文件不会自动撤回；下次打开 Research OS 后，能撤回的运行在任务那一行里有「撤回本次运行」。",
               "Stopping the service ends their programs and everything those programs started. The runs are recorded as “Interrupted”. Files they have already changed are not reverted automatically; when you open Research OS again, a run that can be undone has “Undo this run” in its task row.")
    assert long_task in lines[1]
    assert out["some"] == "\n\n".join([w("还有 2 个 AI 执行者正在运行：", "2 AI agents are still running:"), "\n".join(lines), effect, ask])
    assert out["one"].split("\n\n")[0] == w("还有 1 个 AI 执行者正在运行：", "1 AI agent is still running:")
    if lang == "en":
        assert not CJK.search(out["some"] + out["unknown"] + out["plain"])


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_stop_button_asks_the_engine_first_and_stops_only_when_the_question_is_agreed(lang):
    """The click reads GET /api/agents/going, shows the question that names the runs, and POSTs /api/shutdown only on a yes;
    a no leaves the service running and the button usable; a list that cannot be read still asks (and says so)."""
    runs = _stop_run(lang, [_STOP_RUNS[0]], agree=False)
    assert runs["posted"] == [] and runs["disabledAfter"] is False and "Analyse the sample" in runs["asked"][0]
    agreed = _stop_run(lang, [_STOP_RUNS[0]], agree=True)
    assert agreed["posted"] == [["/api/shutdown", {}]] and agreed["stopped"] is True
    quiet = _stop_run(lang, [], agree=True)
    assert quiet["asked"] == [_w(lang)("确定要停止本地服务吗？停止后这个页面将无法继续使用。", "Stop the local service? Once it stops, this page can no longer be used.")]
    unread = _stop_run(lang, None, agree=False)
    assert unread["asked"][0].startswith(_w(lang)("没能查看是否还有 AI 执行者在运行。", "Could not check whether an AI agent is still running.")) and unread["posted"] == []


def _stop_run(lang: str, runs, *, agree: bool) -> dict:
    """The 停止服务 button's click on `runs` (what /api/agents/going answers; None: it cannot be read)."""
    i18n = (f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()) + "\n" + FILL_VARS_LINE + "\n" + T_LINE
    script = f"""
global.window = {{}};
{i18n}
const posted = [], asked = [];
const appEl = {{ innerHTML: '' }};
const stopButton = {{ disabled: false, addEventListener(e, fn) {{ this.handler = fn; }} }};
global.document = {{ getElementById: (id) => id === 'app' ? appEl : id === 'shutdown-btn' ? stopButton : null }};
window.confirm = (q) => {{ asked.push(q); return {json.dumps(agree)}; }};
global.apiGet = async (path) => {{ if (path !== '/api/agents/going') throw new Error(path);
  if ({json.dumps(runs)} === null) throw new Error('HTTP 500'); return {{ runs: {json.dumps(runs)} }}; }};
global.apiPost = async (path, body) => {{ posted.push([path, body]); return {{ ok: true }}; }};
global.showHeaderError = () => {{}};
global.escapeHtml = (s) => s; global.closePanel = () => {{}};
let pollTimer = null, generationLive = true; function stopGenerationPoll() {{}}
{FILL_VARS_LINE}
{T_LINE}
{STOP_BLOCK}
(async () => {{
  await stopButton.handler();
  process.stdout.write(JSON.stringify({{ asked, posted, disabledAfter: stopButton.disabled, stopped: appEl.innerHTML.includes('stopped-note') }}));
}})();
"""
    return _node(script)


# -- 项目首页 and the owner control: a link, not a form ---------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_project_home_holds_no_agent_form_only_a_text_link_to_the_settings_page(lang):
    """项目首页's 全部任务 header had an 「AI 执行者」 button that unfolded the form on the page. It now has the words 「AI 执行者」
    and the text link 「在设置里修改」 (never a box) that opens the 设置 page at that section; no form is drawn on the home."""
    w = _w(lang)
    out = run_workspace(r"""
agentsInfo=agentFixture();
await window.ROWorkspace.load(root); await sleepTick();
const header=byClass('ws-task-header');
const link=find(window.ROI18N.t('在设置里修改'));
out_={headerText:header.textContent,link:[link.className,link.dataset.key],form:byClass('ws-agent-settings')===undefined,
  oldButton:find(window.ROI18N.t('AI 执行者'))===undefined,inHeader:header.all().includes(link)};
await link.click();
out_.opened=settingsOpened; out_.nowDrawn=byClass('ws-agent-settings')!==undefined;
console.log(JSON.stringify(out_));
""".replace("out_", "o"), lang=lang)
    assert out["headerText"].startswith(w("全部任务", "All tasks")) and w("AI 执行者 在设置里修改", "AI agent Change in Settings") in out["headerText"]
    assert out["link"] == ["ws-text-link ws-file-link", "settings-link"] and out["inHeader"] is True
    assert out["form"] is True and out["oldButton"] is True
    assert out["opened"] == ["agents"] and out["nowDrawn"] is True


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_agents_form_is_drawn_into_the_box_the_page_gives_and_saves_through_the_old_route(lang):
    """The form workspace.js draws into the 设置 page's box keeps its behaviour and its route: a program's path, an agent
    added, the time limit and the spending cap each POST /api/settings/agents with the keys they always sent, and the owner
    control of the home follows (the new agent is among the choices)."""
    w = _w(lang)
    out = run_workspace(r"""
const T=window.ROI18N.t;
agentsInfo=agentFixture(); agentsInfo.agents=[];
await window.ROWorkspace.load(root); await sleepTick();
const before=optionsOf(field(T('负责人'))).map(o=>o[0]);
await find(T('在设置里修改')).click(); await sleepTick();
const box=()=>byClass('ws-agent-settings');
typed(field(T('完整模型 ID'),box()),'sonnet');
await find(T('添加'),box()).click(); await sleepTick();
const minutes=field(T('每次运行的时间限制（分钟）'),box()); typed(minutes,'60');
await find(T('保存'),minutes.parentNode.parentNode).click(); await sleepTick();
const usd=field(T('每次运行的费用上限（美元，只对 Claude Code 有效，留空为不限）'),box()); typed(usd,'2.5');
await find(T('保存'),usd.parentNode.parentNode).click(); await sleepTick();
console.log(JSON.stringify({before,posted:posted.filter(p=>p[0]==='/api/settings/agents').map(p=>p[1]),
  after:optionsOf(field(T('负责人'))).map(o=>o[0]),heading:box().all().some(e=>e.classList.contains('ws-agent-heading'))}));
""", lang=lang)
    agent = {"runner": "claude-code", "model": "sonnet"}
    assert out["posted"] == [{"agents": [agent]}, {"timeout_minutes": 60}, {"max_budget_usd": 2.5}]
    assert w("Claude Code · sonnet", "Claude Code · sonnet") in out["after"] and w("Claude Code · sonnet", "Claude Code · sonnet") not in out["before"]
    assert out["heading"] is False   # the page gives the heading; the box is the form


@needs_node
def test_the_agents_form_works_on_the_settings_page_with_no_project_open_and_shows_no_run_store():
    """The 设置 page needs no project: opened with none, the form lists the programs and saves, asks for no run store (that is
    a project's) and draws none; opened in a project it asks for it as before."""
    out = run_workspace(r"""
state.currentPath=''; state.currentId=null;
agentsInfo=agentFixture();
openSettingsPanel('agents'); await sleepTick();
const box=byClass('ws-agent-settings');
const none={runners:box.all().filter(e=>e.classList.contains('ws-runner')).length,store:box.textContent.includes('运行占用的空间'),
  asked:gets.filter(g=>g.includes('/api/agents/store')).length,got:gets.includes('/api/agents')};
const minutes=field('每次运行的时间限制（分钟）',box); typed(minutes,'30');
await find('保存',minutes.parentNode.parentNode).click(); await sleepTick();
none.saved=posted.map(p=>p[0]);
state.currentPath='/projects/study'; state.currentId='p-study';
openSettingsPanel('agents'); await sleepTick();
const inProject={store:byClass('ws-agent-settings').textContent.includes('运行占用的空间'),asked:gets.filter(g=>g.includes('/api/agents/store')).length};
console.log(JSON.stringify({none,inProject}));
""")
    assert out["none"] == {"runners": 4, "store": False, "asked": 0, "got": True, "saved": ["/api/settings/agents"]}
    assert out["inProject"] == {"store": True, "asked": 1}


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_page_closed_over_the_form_stops_it_asking_for_the_store(lang):
    """The form lives while the 设置 page is the panel on screen: once the panel is closed or taken by another, a late answer
    draws nothing and no timer asks again."""
    out = run_workspace(r"""
agentsInfo=agentFixture(); storeInfo=Object.assign(storeInfo,{converting:true});
await window.ROWorkspace.load(root); await sleepTick();
await find(window.ROI18N.t('在设置里修改')).click(); await sleepTick();
const asked=()=>gets.filter(g=>g.includes('/api/agents/store')).length;
const first=asked(), timersBefore=waiting();
state.panelSubject=null; panel.classList.add('hidden');       // the page was closed
await tick();
console.log(JSON.stringify({first,afterClose:asked(),timersBefore,timersAfter:waiting()}));
""", lang=lang)
    assert out["first"] == 1 and out["afterClose"] == 1 and out["timersBefore"] >= 1


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_question_built_from_the_engines_own_answer_for_a_fake_agent_that_is_going_and_for_none(api, lang):
    """End to end between the engine and the page: the list GET /api/agents/going gives for a fake agent that is going is what
    the question names (its task, agent and model); with nothing going the same route gives the old question."""
    w = _w(lang)
    block = _app_function("function goingRunLine(") + "\n" + _app_function("function stopServiceQuestion(")
    none = api.get("/api/agents/going")[1]["runs"]
    _going_run(api)
    some = api.get("/api/agents/going")[1]["runs"]
    out = _page(lang, block, "out.none = stopServiceQuestion(" + json.dumps(none) + "); out.some = stopServiceQuestion(" + json.dumps(some) + ");")
    assert out["none"] == w("确定要停止本地服务吗？停止后这个页面将无法继续使用。",
                            "Stop the local service? Once it stops, this page can no longer be used.")
    first = out["some"].split("\n\n")
    assert first[0] == w("还有 1 个 AI 执行者正在运行：", "1 AI agent is still running:")
    assert first[1] == w("「Analyse the sample」（Claude Code · sonnet）", "“Analyse the sample” (Claude Code · sonnet)")
    assert first[-1] == out["none"]

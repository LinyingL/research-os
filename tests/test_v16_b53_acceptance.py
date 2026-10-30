"""Research OS V16, task B5-3 (DESIGN.md 21.6, 21.7, 21.1): what the researcher's acceptance of batch 4 found on a Quant-shaped
project -- one person, nineteen tasks in `research/tasks.json` of which one is marked done and eighteen have a report, three
attempts, backups present.

 - 你的记录与备份 counts the task list as 项目首页 reads it (`attempt_tasks.task_states`), drops the subtitle that only repeated the
   title, and orders its lines as 21.7 lists them with the records the project holds before the ones that say 还没有.
 - 设置's programs are titled by their name and version, the path a quiet second line.
 - 变量 says where a card lives and what its record is in words, the file and folder names under 「详情」.
 - 设置's sentence about what an agent writes says 报告, not 结果.
 - 停止服务's question says "it" for one run and "they" for several, in both languages.

The engine's count runs against a real project; the pages run under node on the stand-in DOMs of the other tests, in both
languages. Every test names its scenario."""

from __future__ import annotations

import json
import re

import pytest

from researchos import attempt_tasks, handbacks, inventory, workspace

from test_v16_fix4 import _PAGE_GLOBALS, _VARIABLES_BLOCK, _VARIABLES_PRELUDE, _script
from test_v16_now_doing import A, _at, asking, handed, me, pid, root  # noqa: F401 -- fixtures of the engine tests
from test_v16_handback import PERSON
from test_v16_records_panel import _open_panel
from test_v16_settings import _page, _w
from test_v9_language import CJK, WEBAPP, _app_function, needs_node
from test_workspace_js import run as run_workspace

# -- the task list line: counted the way 项目首页 reads the tasks --------------------------------------------------------------


def _hand_written(count: int) -> list[dict]:
    """The Quant project's task list: `count` hand-written tasks, the first marked done and every other one with a report and
    no mark (older versions did not set the mark; a copy of a project has no run records)."""
    return [{"id": f"h{i}", "text": f"Hand-written task {i}", "done": i == 1, "owner": "", "due": "",
             **({"report": f"research/results/{i:032x}.md"} if i > 1 else {})} for i in range(1, count + 1)]


def _tasks_row(root) -> inventory.Row:
    return {row.code: row for row in inventory.inventory(None, root)}["tasks"]


def _write_tasks(root, tasks: list[dict]) -> None:
    (root / "research").mkdir(exist_ok=True)
    (root / "research" / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")


def test_the_task_list_line_counts_what_the_home_page_reads_as_done_not_the_marks_in_the_file(root, pid):
    """Quant: nineteen tasks, one marked `done: true` and eighteen with a report. 项目首页 reads every one 已完成 (21.1), and the
    line of 你的记录与备份 said one: it now says nineteen, from the very reading the home uses."""
    _write_tasks(root, _hand_written(19))
    states = attempt_tasks.task_states(root, pid, PERSON)
    assert len(states) == 19 and set(states.values()) == {"done"}
    row = _tasks_row(root)
    assert (row.facts["tasks"], row.facts["done"], row.count) == (19, 19, "19 task(s), 19 done")
    assert sum(1 for task in json.loads((root / "research" / "tasks.json").read_text()) if task["done"]) == 1   # the file is as written


def test_a_handback_still_to_open_a_failed_run_and_a_task_with_no_report_are_not_counted_done(root, pid):
    """The same project with three more tasks: an agent's handback not opened yet reads 等你看, a run that failed reads 失败 and
    a hand-written task with no report and no mark reads 未开始; none is 已完成, so the line counts nineteen of twenty-two. Opening
    the handback makes it 已完成 and the line follows."""
    _write_tasks(root, _hand_written(19))
    report = handed(root, pid, "t13", A[5], _at(24, 11), opened=False)
    asking(root, pid, "t40", A[5], "failed")
    tasks, revision = workspace.read_tasks(root)
    workspace.save_tasks(root, {"tasks": tasks + [{"id": "plain", "text": "Not started", "done": False, "owner": "", "due": "", "attempt": A[4]}],
                                "revision": revision})
    states = attempt_tasks.task_states(root, pid, PERSON)
    assert (states["t13"], states["t40"], states["plain"]) == ("to_read", "failed", "not_started")
    row = _tasks_row(root)
    assert (row.facts["tasks"], row.facts["done"]) == (22, 19)
    handbacks.mark_opened(pid, PERSON, report)
    assert _tasks_row(root).facts["done"] == 20 and attempt_tasks.task_states(root, pid, PERSON)["t13"] == "done"


def test_a_task_list_the_home_cannot_read_falls_back_to_the_marks_in_the_file(root):
    """A list that is JSON but not tasks as 项目首页 accepts them (a key it does not know): there is no reading of states, so the
    line counts the `done` marks as it did, and does not fail."""
    _write_tasks(root, [{"id": "a", "text": "x", "done": True, "extra": 1}, {"id": "b", "text": "y"}])
    row = _tasks_row(root)
    assert (row.facts["tasks"], row.facts["done"]) == (2, 1)


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_panel_says_nineteen_of_nineteen_for_the_quant_project(root, pid, lang):
    """The words the researcher reads on the 任务列表 line, from the engine's own row of the Quant-shaped project."""
    _write_tasks(root, _hand_written(19))
    payload = {"rows": [row.payload() for row in inventory.inventory(None, root)]}
    rows = {row["code"]: row for row in _open_panel(payload, language=None if lang == "zh" else "en")["view"]}
    assert rows["tasks"]["count"] == _w(lang)("19 项任务，其中 19 项已完成", "19 tasks, 19 done")


# -- the panel: no subtitle, the lines in 21.7's order -----------------------------------------------------------------------


def _row(code: str, **facts) -> dict:
    """A row of /api/records as the page reads it (only what the page reads)."""
    return {"kind": code, "path": f".researchos/{code}", "count": "", "snapshot": None, "backed_up_at": None, "problems": [], "code": code,
            "facts": {"issues": [], **facts}}


#: The engine's order (`inventory.inventory`) with the facts of a project that holds the attempt table, the task list, a layout and
#: the version store and nothing else.
_QUANT_ROWS = [
    _row("judgements", file_state="absent", exists=False, entries=None), _row("mappings", state="absent", exists=False),
    _row("attempts", state="present", exists=True, attempts=3, with_verdict=3), _row("attempts_config", exists=True),
    _row("canvas", state="ok", exists=True, views=2, arranged=1), _row("variables", exists=False, cards=0),
    _row("notes", file_state="absent", exists=False), _row("decisions", file_state="absent", exists=False),
    _row("papers", file_state="absent", exists=False), _row("tasks", exists=True, tasks=19, done=19),
    _row("history", exists=True, versions=4, nodes=2, bytes=2048)]


def _codes(payload: dict, language) -> list[str]:
    return [row["code"] for row in _open_panel(payload, language=language)["view"]]


@needs_node
@pytest.mark.parametrize("language", [None, "en"])
def test_the_panel_has_no_subtitle_and_lists_what_the_project_holds_before_what_says_not_yet(language):
    """The Quant-shaped project: the subtitle that repeated the title is gone; the records it holds (the attempts and their table,
    the route layout, the task list, the version store) come first in 21.7's order, and the ones that say 还没有 after them in the same
    order -- not the engine's order, which put the empty link judgments and hand-drawn links at the top."""
    out = _open_panel({"rows": _QUANT_ROWS}, language=language)
    assert out["subtitle"] == ""
    assert [row["code"] for row in out["view"]] == [
        "attempts", "attempts_config", "canvas", "tasks", "history",
        "judgements", "mappings", "variables", "notes", "decisions", "papers"]
    said = {row["code"]: row["count"] for row in out["view"]}
    none = "还没有" if language is None else "None yet"
    assert [code for code in ("judgements", "mappings", "variables", "notes", "decisions", "papers") if said[code] != none] == []


@needs_node
@pytest.mark.parametrize("language", [None, "en"])
def test_every_kind_in_the_order_of_21_7_when_the_project_holds_them_all_and_a_file_that_cannot_be_read_counts_as_held(language):
    """A project that holds every record: the order of 21.7 (研究判断, 尝试表, 连线确认, 手画的连线, 研究路线的布局, 变量卡, 备注, 经 PI
    批准的决定, 论文正文, 任务列表, Stata, 版本历史). The hand-drawn links file that cannot be read is held (it has a problem to
    show), and a kind the page has no place for goes last of its group, in the order the engine gave."""
    held = [_row("history", exists=True, versions=1, nodes=1, bytes=10), _row("tasks", exists=True, tasks=1, done=1),
            _row("stata", file_state="present", exists=True, globals=1, folders=1),
            _row("papers", file_state="present", exists=True, marked=1, out=0), _row("decisions", file_state="present", exists=True, entries=1, abandoned=0),
            _row("notes", file_state="present", exists=True, notes=1, nodes=1, withdrawn=0), _row("variables", exists=True, cards=1),
            _row("canvas", state="ok", exists=True, views=1, arranged=1), _row("mappings", state="unreadable", exists=True, issues=["unreadable"]),
            _row("judgements", file_state="present", exists=True, entries=1, standing=1, conflicts=0), _row("attempts_config", exists=True),
            _row("attempts", state="present", exists=True, attempts=1, with_verdict=1), _row("something_new", exists=True)]
    assert _codes({"rows": held}, language) == [
        "attempts", "attempts_config", "judgements", "mappings", "canvas", "variables", "notes", "decisions", "papers", "tasks", "stata",
        "history", "something_new"]
    # nothing held but a kind the page does not know (nothing says it is empty): that one first, then the rest in the same order
    empty = [_row("history", exists=False), _row("tasks", exists=False), _row("something_new", exists=False),
             _row("notes", file_state="absent", exists=False), _row("attempts", state="not_configured", exists=False),
             _row("attempts_config", exists=False), _row("canvas", state="absent", exists=False)]
    assert _codes({"rows": empty}, language) == ["something_new", "attempts", "attempts_config", "canvas", "notes", "tasks", "history"]
    # a pending migration is held (it asks something of the researcher) and stands among the held ones, after the rest of them
    pending = [_row("judgements", file_state="absent", exists=False), _row("legacy", exists=False, waiting=1, issues=["needs_migration"]),
               _row("tasks", exists=True, tasks=1, done=0)]
    assert _codes({"rows": pending}, language) == ["tasks", "legacy", "judgements"]


def test_the_orders_of_the_page_and_of_the_design_are_one_list():
    """DESIGN.md 21.7 names the lines in an order; the page's `RECORD_ORDER` is that list (the kinds as the engine names them)."""
    src = (WEBAPP / "app.html").read_text(encoding="utf-8")
    order = re.search(r"const RECORD_ORDER = \[(.*?)\];", src, re.S).group(1)
    assert re.findall(r'"(\w+)"', order) == [
        "attempts", "attempts_config", "judgements", "mappings", "canvas", "variables", "notes", "decisions", "papers", "tasks", "stata",
        "history", "legacy"]
    design = (WEBAPP.parents[2] / "DESIGN.md").read_text(encoding="utf-8")
    line = design[design.index("one line per record (*B4-2*):"):][:700]
    kinds = ["研究判断", "尝试表", "连线确认", "手画的连线", "研究路线的布局", "变量卡", "备注", "经 PI 批准的决定", "论文正文与不属于论文", "任务列表"]
    assert [line.index(kind) for kind in kinds] == sorted(line.index(kind) for kind in kinds)


# -- 设置: a program is titled by its name and version -----------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_program_in_settings_is_titled_by_its_name_and_version_with_its_path_as_a_quiet_second_line(lang):
    """On the AI 执行者 section of 设置: Claude Code's line is 「Claude Code」 with its version beside it -- no path in it --, and the
    path is a second line of the quiet path style under the title; a program that is not found has a title that says so and no
    second line, and its box for a path is unchanged."""
    out = run_workspace(r"""
const T=window.ROI18N.t;
agentsInfo=agentFixture();
await window.ROWorkspace.load(root);await sleepTick();
await find(T('在设置里修改')).click();await sleepTick();
const runners=byClass('ws-agent-settings').all().filter(e=>e.classList.contains('ws-runner'));
const shape=r=>{
  const head=r.children.find(e=>e.classList.contains('ws-runner-head')), path=r.children.filter(e=>e.classList.contains('ws-output-path'));
  return {title:head.children[0].tagName+':'+head.children[0].textContent, head:head.textContent, headHasPath:head.all().some(e=>e.tagName==='CODE'),
    paths:path.map(e=>[e.tagName,e.textContent]), second:r.children[1]===path[0], inputs:r.all().filter(e=>e.tagName==='INPUT').length};};
console.log(JSON.stringify({shapes:runners.map(shape),version:T('版本 {version}',{version:'2.1.0'}),missing:T('没有找到这个程序')}));
""", lang=lang)
    claude, opencode, codex, harness = out["shapes"]
    assert claude["title"] == "H4:Claude Code" and claude["head"] == "Claude Code" + out["version"]
    assert claude["head"] == "Claude Code" + _w(lang)("版本 2.1.0", "Version 2.1.0")
    assert claude["headHasPath"] is False and claude["paths"] == [["CODE", "/usr/local/bin/claude"]] and claude["second"] is True
    assert claude["inputs"] == 0
    assert opencode["head"] == "OpenCode" + out["missing"] and opencode["paths"] == [] and opencode["inputs"] == 1
    assert (codex["head"], codex["paths"]) == ("Codex" + _w(lang)("版本 0.162.0", "Version 0.162.0"), [["CODE", "/opt/codex"]])
    assert harness["head"].startswith("DeepSeek Harness") and harness["paths"] == [["CODE", "/apps/dsh"]]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_version_in_the_title_is_the_number_the_program_printed_and_the_line_as_printed_is_the_hover(lang):
    """What `claude --version` prints is 「2.1.309 (Claude Code)」 and `codex --version` 「codex-cli 0.164.0-alpha.11.1」: each says the
    program's name again beside a title that names it. The title shows the number (a pre-release tag with it); the line as the program
    printed it is the hover; a line with no number in it is shown as it is, and a number already alone has no hover."""
    out = run_workspace(r"""
const T=window.ROI18N.t;
agentsInfo=agentFixture();
agentsInfo.runners[0].version='2.1.309 (Claude Code)'; agentsInfo.runners[1]=Object.assign(agentsInfo.runners[1],{path:'/opt/opencode',version:'opencode v2.1.3',problem:null,found:true});
agentsInfo.runners[2].version='codex-cli 0.164.0-alpha.11.1'; agentsInfo.runners[3].version='build seven';
await window.ROWorkspace.load(root);await sleepTick();
await find(T('在设置里修改')).click();await sleepTick();
const runners=byClass('ws-agent-settings').all().filter(e=>e.classList.contains('ws-runner'));
console.log(JSON.stringify(runners.map(r=>{const head=r.children[0],meta=head.children[1];return {head:head.textContent,hover:meta.title||null};})));
""", lang=lang)
    shown = _w(lang)("版本 {}", "Version {}")
    assert out == [{"head": "Claude Code" + shown.format("2.1.309"), "hover": "2.1.309 (Claude Code)"},
                   {"head": "OpenCode" + shown.format("2.1.3"), "hover": "opencode v2.1.3"},
                   {"head": "Codex" + shown.format("0.164.0-alpha.11.1"), "hover": "codex-cli 0.164.0-alpha.11.1"},
                   {"head": "DeepSeek Harness" + shown.format("build seven"), "hover": None}]


def test_the_quiet_path_line_is_the_one_the_other_pages_use_and_the_program_row_has_no_line_of_its_own():
    """The second line wears `ws-output-path` (small, quiet, the mono) like the paths under a file's title on the other pages, and the
    old `ws-runner-where` row that held the path and the version is gone from the script and the style sheet."""
    js = (WEBAPP / "workspace.js").read_text(encoding="utf-8")
    css = (WEBAPP / "workspace.css").read_text(encoding="utf-8")
    assert "ws-runner-where" not in js and "ws-runner-where" not in css
    assert re.search(r"\.ws-output-path \{[^}]*font-family: var\(--font-mono\)[^}]*color: var\(--ink-soft\)", css)
    assert re.search(r"\.ws-runner-head \{[^}]*display: flex", css)


# -- 设置: the sentence about what an agent writes -----------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_settings_sentence_says_notes_and_reports_and_no_sentence_of_the_page_says_result_for_a_report(lang):
    """The first sentence of AI 执行者 said an agent writes 「笔记和结果」; what an agent writes is 报告 (21.6). The words of the page
    in the section, the task form and its helpers, are scanned for the slip: none calls a report a result."""
    out = run_workspace(r"""
const T=window.ROI18N.t;
agentsInfo=agentFixture();
await window.ROWorkspace.load(root);await sleepTick();
const home=root.textContent;
await find(T('在设置里修改')).click();await sleepTick();
const box=byClass('ws-agent-settings');
console.log(JSON.stringify({first:box.children[0].textContent,all:box.textContent,home}));
""", lang=lang)
    assert out["first"] == _w(lang)(
        "负责人可以选这台电脑上的 AI 执行者：运行时它会直接在项目文件夹里做这项任务，写代码、运行程序、写笔记和报告。",
        "An owner can be an AI agent installed on this Mac: when run, it does the task directly in the project folder, writing code, running programs and writing notes and reports.")
    for word in _w(lang)(("结果记录", "笔记和结果"), ("result record", "notes and results")):
        assert word not in out["all"] and word not in out["home"]
    src = (WEBAPP / "workspace.js").read_text(encoding="utf-8") + (WEBAPP / "i18n.js").read_text(encoding="utf-8")
    assert "写笔记和结果" not in src and "notes and results" not in src


# -- 变量: where a card lives and what its record is, in words ---------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_empty_variables_page_says_where_a_card_lives_in_words_and_keeps_the_folder_name_under_details(lang):
    """No card yet: the guide card says each card is kept in Research OS's own records folder inside the project, filled in with the
    researcher's editor; the folder's name is not in any sentence -- it is under a folded 「详情」 with the chevron, behind the label
    「存放位置」."""
    w = _w(lang)
    out = _script(lang, [_VARIABLES_BLOCK, _VARIABLES_PRELUDE], r"""
await loadVariablesView();
const guide = byClass(views.variables, "guide-card")[0];
out.paragraphs = guide.kids.filter((k) => typeof k !== "string" && k.tag === "P").map(text);
const fold = byClass(guide, "records-fold")[0];
out.fold = { tag: fold.tag, open: !!fold.open, summary: text(fold.kids[0]), chevron: byClass(fold, "res-chevron").length,
  lines: fold.kids.slice(1).map((k) => k.kids.map(text)), summaryClass: fold.kids[0].className };
""")
    sentence = w("每张卡存放在项目里 Research OS 自己的记录文件夹中，由你在编辑器中填写；页面只显示它，并记录确认、弃用等动作。",
                 "Each card is kept in Research OS’s own records folder inside the project and filled in with your editor. This page shows it and records confirming, retiring and the like.")
    assert sentence in out["paragraphs"]
    assert not [p for p in out["paragraphs"] if ".researchos" in p or "<id>" in p]
    assert out["fold"] == {"tag": "DETAILS", "open": False, "summary": w("详情", "Details"), "chevron": 1,
                           "lines": [[w("存放位置", "Kept in"), ".researchos/variables/<id>/"]], "summaryClass": "res-draft-summary"}
    if lang == "en":
        assert not CJK.search(" ".join(out["paragraphs"]))


_CARD_BLOCK = "\n".join([_app_function("function storageDetails("), _app_function("function chevronSummary("),
                         _app_function("function renderVariableCard(")])

_CARD_PRELUDE = _PAGE_GLOBALS + r"""
global.chevronIcon = () => el("svg", "res-chevron");
global.varState = { card: "rv", version: null }; global.loadVariablesView = () => {}; global.RONode = { openNode() {} };
global.apiGet = () => new Promise(() => {});
global.cardChips = () => []; global.cardTrustNotice = () => null; global.abandonedNotice = () => null; global.questionNotice = () => null;
global.defaultVersion = (card) => card.versions[0].version; global.versionLabel = (v) => "v" + v.version;
global.renderVersion = () => {}; global.implementationSection = () => {}; global.openVariableCopy = () => {};
global.cardActText = (e) => e.text; global.ATTESTED_WORDS = {};
global.draw = (card) => { const host = el("div", "host"); renderVariableCard(host, card); return host; };
global.historyOf = (host) => byClass(host, "var-section")[0];
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_cards_record_is_headed_in_words_and_its_file_is_under_details(lang):
    """A card with two entries in its record: the heading says whose record it is and in what order (「这张卡的记录（按时间先后）」), no
    sentence or heading names log.toml, and the file with its place stands under a folded 「详情」; a card with no entry says
    the draft has not been confirmed yet, and a card whose record cannot be read says that -- both without the file name."""
    w = _w(lang)
    out = _script(lang, [_CARD_BLOCK, _CARD_PRELUDE], r"""
const base = { id: "rv", name: "Realised volatility", state: "ok", versions: [{ version: 1 }], in_use: 1, history: [{ text: "Confirmed v1" }, { text: "Retired" }] };
const view = (card) => { const section = historyOf(draw(card)), fold = byClass(section, "records-fold")[0];
  return { title: text(byClass(section, "var-section-title")[0]), lines: byClass(section, "var-line").map(text),
    outside: section.kids.filter((k) => typeof k !== "string" && k !== fold).map(text).join("|"),
    fold: fold ? { summary: text(fold.kids[0]), line: fold.kids.slice(1).map((k) => k.kids.map(text)), chevron: byClass(fold, "res-chevron").length } : null }; };
out.entries = view(base);
out.empty = view({ ...base, history: [] });
out.unreadable = view({ ...base, state: "unreadable", history: [] });
""")
    assert out["entries"]["title"] == w("这张卡的记录（按时间先后）", "Record of this card (in time order)")
    assert out["entries"]["lines"] == ["Confirmed v1", "Retired"]
    assert out["entries"]["fold"] == {"summary": w("详情", "Details"), "chevron": 1,
                                      "line": [[w("存放位置", "Kept in"), ".researchos/variables/rv/log.toml"]]}
    assert out["empty"]["lines"] == [w("还没有记录：草稿确认之前，这里什么都没有。", "No record yet: until the draft is confirmed, there is nothing here.")]
    assert out["unreadable"]["lines"] == [w("这张卡的记录现在无法读取，暂时看不到（文件本身没有被改动）。",
                                            "The record of this card cannot be read right now, so it cannot be shown for the moment (the file itself has not been changed).")]
    for name in ("entries", "empty", "unreadable"):
        assert "log.toml" not in out[name]["title"] + "".join(out[name]["lines"]) + out[name]["outside"]
        if lang == "en":
            assert not CJK.search(out[name]["title"] + "".join(out[name]["lines"]))


# -- 停止服务: one run is "it", several are "they" ---------------------------------------------------------------------------------

_RUNS = [
    {"task": "Analyse the sample", "label": "Claude Code · sonnet", "runner": "claude-code", "model": "sonnet", "project": "study", "here": True},
    {"task": "Collect the data", "label": "OpenCode · lmstudio/qwen3-coder", "runner": "opencode", "model": "lmstudio/qwen3-coder",
     "project": "study", "here": True},
]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_stop_question_uses_singular_words_for_one_run_and_plural_words_for_several(lang):
    """One agent run going: the question says its program, the run and the files it changed -- 「它的程序」, 「这次运行」, 「它已经改动
    的文件」 (“its program”, “The run”, “Files it has already changed”) -- with nothing plural in it. Two runs: the plural sentence, as
    before, with nothing singular in it."""
    w = _w(lang)
    out = _page(lang, _app_function("function goingRunLine(") + "\n" + _app_function("function stopServiceQuestion("),
                "const RUNS = " + json.dumps(_RUNS) + "; out.one = stopServiceQuestion([RUNS[0]]); out.two = stopServiceQuestion(RUNS);")
    ask = w("确定要停止本地服务吗？停止后这个页面将无法继续使用。", "Stop the local service? Once it stops, this page can no longer be used.")
    one_effect = w("停止服务会结束它的程序，以及程序启动的所有进程。这次运行会记为「已中断」。它已经改动的文件不会自动撤回；下次打开 Research OS 后，如果这次运行能撤回，任务那一行里有「撤回本次运行」。",
                   "Stopping the service ends its program and everything that program started. The run is recorded as “Interrupted”. Files it has already changed are not reverted automatically; when you open Research OS again, if the run can be undone, “Undo this run” is in its task row.")
    two_effect = w("停止服务会结束它们的程序，以及程序启动的所有进程。这些运行会记为「已中断」。它们已经改动的文件不会自动撤回；下次打开 Research OS 后，能撤回的运行在任务那一行里有「撤回本次运行」。",
                   "Stopping the service ends their programs and everything those programs started. The runs are recorded as “Interrupted”. Files they have already changed are not reverted automatically; when you open Research OS again, a run that can be undone has “Undo this run” in its task row.")
    assert out["one"].split("\n\n") == [w("还有 1 个 AI 执行者正在运行：", "1 AI agent is still running:"),
                                        w("「Analyse the sample」（Claude Code · sonnet）", "“Analyse the sample” (Claude Code · sonnet)"), one_effect, ask]
    assert out["two"].split("\n\n")[0] == w("还有 2 个 AI 执行者正在运行：", "2 AI agents are still running:")
    assert out["two"].split("\n\n")[-2:] == [two_effect, ask]
    for plural in w(("它们", "这些运行"), ("their", "those", "The runs", "they")):
        assert plural not in one_effect
    for singular in w(("它的程序", "这次运行", "它已经"), ("ends its program", "The run ", "Files it ")):
        assert singular not in two_effect
    if lang == "en":
        assert not CJK.search(out["one"] + out["two"])

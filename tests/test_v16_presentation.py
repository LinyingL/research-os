"""Research OS V16, task B5-1 (DESIGN.md 21.8): the presentation of every page and panel -- the sources, the style sheets and the engine.

What the pages draw, under node and in both languages, is `test_v16_presentation_js.py`; the clay colour is `test_v16_clay.py`.
This file holds what is true of the code as written: the controls that only navigate are never created as the outline button,
a new primary button is a new surface or a second one and has to be named, a chip does not borrow the box of another class,
a panel's title is in the title font, the kind label of 图表与论断 has room, and 关系图 opens on 全部 when the current
attempt has nothing to draw. Every test names its scenario."""

from __future__ import annotations

import re

import pytest

from researchos import db, paths
from researchos.webapp import canvas

from test_v16_clay import _declarations, _script_lines, _scripts
from test_v9_language import APP_HTML, WEBAPP, _app_block, _app_function, _i18n_source, _node, needs_node
from test_webapp_canvas import A16, A17, A18, STEPS, _canvas, _ingest, _make_pipeline, project  # noqa: F401 -- the pipeline fixture
from test_webapp_canvas_js import PIPELINE_LINKS, PIPELINE_NODES, _scenario, _view

# -- navigation is a text link ------------------------------------------------------------------------------------------------

#: The controls that only navigate: they open something to look at, change nothing and start nothing. By the script that draws
#: them. (Not here: 打开 and 在 Finder 中显示 start another program; 查看 of the join dialog reads a folder and 查看将要发送的内容
#: asks the engine for a dry run -- both are acts; a disclosure's toggle is 12.13's chevron, not a button.)
NAVIGATION = {
    "workspace.js": ["打开定义卡", "查看变量草稿…", "查看文件", "修改历史", "运行日志", "打开结果"],
    "desk.js": ["打开交回", "打开详情页", "运行日志", "查看"],
    "node.js": ["查看内容", "当时的依据", "打开定义卡"],
    "team.js": ["当时的依据", "查看当时的内容", "去成员一栏"],
    "app.html": ["打开结果", "打开定义卡", "查看当时的代码", "查看这一条的文字", "查看这一版当时的文字"],
}
#: The class of a text link: ink, underlined, no box (`.ws-text-link` of workspace.css); a name that opens a file adds `ws-file-link`.
TEXT_LINK = "ws-text-link"


def _calls(label: str, line: str) -> bool:
    return bool(re.search(r'(?:mkButton|button)\(\s*(?:t\()?"' + re.escape(label) + r'"\)?\s*,', line))


def test_a_control_that_only_navigates_is_created_as_a_text_link_never_as_the_outline_button():
    """21.8: 打开交回, 打开详情页, 打开结果, 打开定义卡, 查看文件, 修改历史, 运行日志, 当时的依据 and the like open a place and change nothing:
    each is created with the text link's class, and none with `btn`."""
    wrong, held = [], set()
    for path in _scripts():
        lines = _script_lines(path)
        for at, line in enumerate(lines):
            for label in NAVIGATION.get(path.name, []):
                if _calls(label, line):
                    held.add((path.name, label))
                    # a call that goes over several lines names its class on the line that closes it: `}, "ws-hb-link")`
                    call = line if not line.endswith("{") else line + " " + next((l for l in lines[at + 1:at + 40] if re.match(r'\}, "[^"]+"\)', l)), "")
                    if not re.search(r'"(?:[a-z-]+ )*(?:ws-text-link|ws-hb-link)(?: [a-z-]+)*"', call) or re.search(r'"btn[ "]', call):
                        wrong.append((path.name, label, call[:120]))
    assert wrong == [], f"drawn as a button, not as a text link: {wrong}"
    listed = {(f, label) for f, labels in NAVIGATION.items() for label in labels}
    assert listed - held == set(), f"listed, but no script creates it any more: {sorted(listed - held)}"


def test_a_name_that_opens_a_file_is_a_text_link_and_a_title_or_a_chip_is_never_made_a_button():
    """21.8: a file's name, a report's title and a variable card's state are words; where one opens something it is a text link
    (`ws-text-link ws-file-link`), and no state word (`status-label`, `var-chip`, `own-mark`) is made with `button`."""
    for path in _scripts():
        for line in _script_lines(path):
            if re.search(r'(?:mkButton|button|el|made|mk)\(\s*"button"[^)]*"(?:[^"]* )?(?:status-label|var-chip|own-mark)\b', line):
                pytest.fail(f"a state word is made a button: {path.name}: {line[:120]}")
            if re.search(r'mkButton\([^,]*(?:item\.title \|\| item\.path|task\.context\.report)[^,]*, "btn', line):
                pytest.fail(f"a title that opens a file is made a button: {path.name}: {line[:120]}")
    workspace = (WEBAPP / "workspace.js").read_text(encoding="utf-8")
    assert '"ws-text-link ws-file-link"' in workspace      # the report of a task, an attempt's parents and a note's file
    assert "is-draft" in APP_HTML.read_text(encoding="utf-8")


# -- the primary button ---------------------------------------------------------------------------------------------------------

#: Where a primary button is created, by script: how many sites. Each is one surface's primary action (DESIGN.md 21.8 lists them);
#: a new site is a new surface, or a second primary button on one -- add it to 21.8 with its surface, or draw it quiet.
PRIMARY_SITES = {"app.html": 10, "canvas.js": 1, "desk.js": 2, "node.js": 2, "routemap.js": 1, "team.js": 13, "workspace.js": 10}


def _primary_sites() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in _scripts():
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".html":
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        count = 0
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("//", "/*", "*")) or "classList" in line or "querySelector" in line:
                continue
            count += len(re.findall(r'"(?!\.)[^"\n]*\bbtn-primary\b[^"\n]*"', line))
        if count:
            found[path.name] = count
    return found


def test_a_primary_button_is_created_only_where_21_8_names_a_surface_for_it():
    assert _primary_sites() == PRIMARY_SITES


def test_a_step_that_follows_a_primary_button_takes_it_over_and_the_one_before_goes_quiet():
    """21.8: the attempt form's 预览改动 gives its primary to 保存修改 while the preview stands and takes it back with 返回修改; the
    join dialog's 查看 gives it to 核对无误，加入; the composer's 查看将要发送的内容 to 发送提议 (the node-run tests draw them)."""
    app = APP_HTML.read_text(encoding="utf-8")
    preview = app[app.index("function renderPreview("):]
    preview = preview[:preview.index("\n  }\n")]
    assert 'submitBtn.classList.remove("btn-primary")' in preview and 'submitBtn.classList.add("btn-primary")' in preview
    team = (WEBAPP / "team.js").read_text(encoding="utf-8")
    assert team.count('look.classList.toggle("btn-primary", !result.querySelector(".btn-primary"))') == 2
    desk = (WEBAPP / "desk.js").read_text(encoding="utf-8")
    assert 'go.classList.remove("btn-primary")' in desk and 'go.classList.add("btn-primary")' in desk


# -- the style sheets -------------------------------------------------------------------------------------------------------------


def test_the_state_chips_of_a_variable_card_borrow_no_box_from_another_class():
    """The 「草稿 v1」 chip was drawn as a button because its tone class was `draft`, which is also the team's draft box (a border,
    a padding, a background). A tone class of a chip is a name no other rule gives a box."""
    tones = set(re.findall(r'chips\.push\(\[[^,\]]+, "([a-z-]*)"\]\)', _app_function("function cardChips(")))
    assert {"bad", "dead", "in-use", "is-draft"} <= tones
    boxes = {selector for tone in tones if tone for selector in [f".{tone}"] if "border" in _declarations(selector) or "padding" in _declarations(selector)}
    assert boxes == set(), f"a chip's tone is also a bare class that draws a box: {sorted(boxes)}"
    assert _declarations(".var-chip.is-draft")["background"] == "var(--ochre-soft)"


def test_the_kind_label_of_figures_and_claims_has_room_for_the_english_word():
    """In English the label is 「Figure」 or 「Table」: its column is wide enough that it does not run into the thumbnail and the
    name (it was 1.6rem, the width of one Chinese character)."""
    width = _declarations(".res-kind")["width"]
    assert width.endswith("rem") and float(width[:-3]) >= 3.0


def test_a_panels_title_is_in_the_title_font_and_only_a_files_path_is_in_the_mono():
    """12.11: the mono is for paths, code and identifiers. The 备份 panel's title is words: the title font (the sans, weight 600) in
    both languages; the file panel's title is a path and is the mono, through the class `path-title` the frame sets from the
    panel's subject."""
    title = _declarations(".panel-title")
    assert title["font-family"] == "var(--font-sans)" and title["font-weight"] == "600"
    assert _declarations(".panel-title.path-title")["font-family"] == "var(--font-mono)"


@needs_node
def test_the_frame_marks_a_panels_title_as_a_path_only_for_the_file_panel():
    """`openPanel` sets `path-title` from the subject the opener made: a file's path in the file panel, words everywhere else --
    also in the panel that replaces a file panel without the panel being closed first."""
    script = ("global.window = {};\n" + "const classes = new Set();\n"
              "global.panelTitle = { classList: { toggle: (c, on) => { if (on) classes.add(c); else classes.delete(c); }, remove: (c) => classes.delete(c) } };\n"
              "const gone = () => ({ classList: { remove() {}, add() {}, contains: () => false, toggle() {} }, setAttribute() {}, addEventListener() {} });\n"
              "global.panel = gone(); global.panelBackdrop = gone(); global.state = { panelSubject: null }; global.document = { getElementById: gone };\n"
              + _app_block("// -- Panel and 返回 (DESIGN.md 21.8)", "// -- end of panel and 返回") +
              "\nconst seen = [];\n"
              "for (const subject of [{ kind: 'file', path: 'a/b.csv' }, { kind: 'records' }, null, { kind: 'file', path: 'c.py' }, { kind: 'settings' }]) {\n"
              "  state.panelSubject = subject; openPanel(); seen.push(classes.has('path-title'));\n}\n"
              "process.stdout.write(JSON.stringify(seen));\n")
    assert _node(script) == [True, False, False, True, False]


# -- 关系图 -----------------------------------------------------------------------------------------------------------------------------


def _with_empty_current_attempt(root) -> None:
    """The map names a row 19 marked ✅ -- the current attempt -- that has no step file and no file linked to it."""
    (root / "map.md").write_text((root / "map.md").read_text(encoding="utf-8") + "| 19 | 2026-09-08 | 论文第二章 (19) | v | r | ✅ |\n", encoding="utf-8")
    _ingest(root)


def test_the_default_scope_is_all_when_the_current_attempt_has_nothing_to_draw_and_says_which_attempt_it_left(project):
    """The thesis: 研究路线 marks #19 current and it holds no file, so the canvas drew nothing. A request that names no scope is
    answered with 全部 -- every card of the project -- and `fell_back_from`, the current attempt's own summary, for the page's one
    quiet line; the selector still marks #19 「（当前）」."""
    _with_empty_current_attempt(project)
    payload = _canvas(project)
    assert payload["scope"] == {"id": "all"} and payload["nodes"]
    assert payload["fell_back_from"]["id"] == "attempt:map.md#19" and payload["fell_back_from"]["number"] == "19"
    assert payload["fell_back_from"]["title"] == "论文第二章 (19)"
    assert [s["id"] for s in payload["scopes"] if s["current"]] == ["attempt:map.md#19"]
    assert payload["frames"] == []     # 全部 draws no attempt's frame (8.7)


def test_a_request_that_names_the_empty_attempt_or_all_is_answered_as_asked_and_a_current_attempt_with_files_is_the_default_still(project):
    """Only the request that names no scope falls back: choosing the empty attempt shows its empty view (the page says so as it
    did), choosing 全部 says nothing of a fall-back; and where the current attempt has files nothing changes."""
    default = _canvas(project)
    assert default["scope"]["id"] == A17 and "fell_back_from" not in default
    _with_empty_current_attempt(project)
    named = _canvas(project, "attempt:map.md#19")
    assert named["scope"]["id"] == "attempt:map.md#19" and named["nodes"] == [] and "fell_back_from" not in named
    everything = _canvas(project, "all")
    assert everything["scope"] == {"id": "all"} and "fell_back_from" not in everything
    assert [n["id"] for n in everything["nodes"]] == [n["id"] for n in _canvas(project)["nodes"]]


def test_the_layout_of_the_view_the_page_fell_back_to_is_the_view_all(project):
    """The page writes the arrangement of what it shows under the scope it was answered with: `all`, whose cards are the same
    ones the fall-back drew -- `view_card_ids` for `all` answers them."""
    _with_empty_current_attempt(project)
    conn = db.connect(paths.graph_db_path(project))
    try:
        assert canvas.view_card_ids(conn, project, "all") >= {n["id"] for n in _canvas(project)["nodes"]}
    finally:
        conn.close()


def test_the_page_remembers_why_it_opened_on_all_only_while_that_stays_the_view():
    """The note stays while the researcher looks at 全部 -- through re-fetches that name the scope -- and goes when they choose an
    attempt, when the project is another, and with a reset."""
    note = {"id": "attempt:map.md#19", "number": "19", "title": "论文第二章"}
    run = _scenario("""
      C._applyPayload(Object.assign({}, input.all, { fell_back_from: input.note }));
      const first = S.fellBack && S.fellBack.attempt.number;
      C._applyPayload(input.all);                      // the page asks for scope=all from now on: no fell_back_from
      const kept = !!S.fellBack;
      C._applyPayload(input.attempt);                  // the researcher chose an attempt
      const afterChoice = S.fellBack;
      C._applyPayload(Object.assign({}, input.all, { fell_back_from: input.note }));
      C._applyPayload(Object.assign({}, input.all, { project: "/tmp/other-project" }));
      const afterSwitch = S.fellBack;
      C._applyPayload(Object.assign({}, input.all, { project: "/tmp/other-project", fell_back_from: input.note }));
      C.reset();
      return { first, kept, afterChoice, afterSwitch, afterReset: S.fellBack };
    """, all=_view("all", PIPELINE_NODES, PIPELINE_LINKS), attempt=_view("attempt:map.md#17", PIPELINE_NODES, PIPELINE_LINKS), note=note)
    assert run["out"] == {"first": "19", "kept": True, "afterChoice": None, "afterSwitch": None, "afterReset": None}


_NOTE = """
global.window = {};
window.ROI18N_SAVED = %(lang)s;
%(i18n)s
%(canvas)s
const C = window.RCECanvas, S = C._state;
const note = { hidden: null, textContent: "", classList: { toggle(c, on) { note.hidden = on; } } };
S.dom = { note }; S.scope = "all"; S.project = "p";
const out = [];
S.fellBack = { project: "p", attempt: { number: "#19", title: "Thesis chapter two" } };
C._renderNote(); out.push([note.hidden, note.textContent]);
S.scope = "attempt:map.md#17"; C._renderNote(); out.push([note.hidden, note.textContent]);
process.stdout.write(JSON.stringify(out));
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_note_is_one_quiet_line_in_the_screens_language_and_hidden_once_the_view_is_another(lang):
    import json

    script = _NOTE % {"lang": json.dumps(lang), "i18n": _i18n_source(), "canvas": (WEBAPP / "canvas.js").read_text(encoding="utf-8")}
    shown, gone = _node(script)
    assert shown == [False, {"zh": "「#19 Thesis chapter two」还没有可画的文件，所以先显示全部。",
                             "en": "“#19 Thesis chapter two” has no files to draw yet, so the map shows all."}[lang]]
    assert gone == [True, ""]
    assert _declarations(".cv-note")["color"] == "var(--ink-soft)"      # quiet: not the clay of a problem

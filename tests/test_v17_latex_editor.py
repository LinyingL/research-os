"""DESIGN.md 22.1 (task V17, B1c): the LaTeX editor's page. The conversion of CodeMirror's positions (a line break as
one, UTF-16) into the server's (code points, a CRLF as two) and the edits computed from the changes since the last save,
run under node on the vendored CodeMirror and then spliced by the server into the same bytes; every line break typed or
pasted becomes the file's; the include links and the main file the server finds; and the hooks that put the editor on
screen (the view, 编辑 on a draft's page and on 论文正文's rows, the dirty guard). Every test names its scenario."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from researchos import latex_edit, project

WEBAPP = Path(__file__).resolve().parents[1] / "src" / "researchos" / "webapp"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

_HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const ctx = vm.createContext({});
vm.runInContext(fs.readFileSync(%(cm)s, 'utf8'), ctx);
global.window = {ROCodeMirror: ctx.ROCodeMirror};
eval(fs.readFileSync(%(latex)s, 'utf8'));
const CM = ctx.ROCodeMirror, P = window.ROLatex._pure;
let seed = 7;
const rand = (n) => { seed = (seed * 1103515245 + 12345) %% 2147483648; return seed %% n; };
const PIECES = ['a', 'Z', '\\section{', '}', '中', '文', '😀', '$x^2$', ' ', '\n', '%%'];
const word = (n) => Array.from({length: n}, () => PIECES[rand(PIECES.length)]).join('');
const splice = (text, edits) => {           // the server's splice, in code points
  const cps = Array.from(text); let out = [], at = 0;
  for (const e of edits) {
    if (!(at <= e.from && e.from <= e.to && e.to <= cps.length)) throw new Error('bad edit ' + JSON.stringify(e));
    out.push(cps.slice(at, e.from).join(''), e.insert); at = e.to;
  }
  out.push(cps.slice(at).join('')); return out.join('');
};
const cases = [];
let failures = 0, rounds = 0;
for (const sep of ['\n', '\r\n']) {
  for (let round = 0; round < 150; round++) {
    const original = word(40).split('\n').join(sep);
    let st = CM.EditorState.create({doc: original, extensions: [CM.EditorState.lineSeparator.of(sep), P.breakFilter(CM, sep)]});
    const saved = st.doc;
    let pending = CM.ChangeSet.empty(saved.length);
    for (let step = 1 + rand(5); step > 0; step--) {      // several transactions, each with one to three changes
      const len = st.doc.length, specs = [];
      let at = 0;
      for (let k = 1 + rand(3); k > 0 && at <= len; k--) {
        const from = at + rand(Math.max(1, len - at + 1)), to = Math.min(len, from + rand(4));
        specs.push({from, to, insert: word(rand(4)).split('\n').join(sep)});
        at = to + 1;
      }
      // keep pairs whole: CodeMirror's own commands never split a surrogate pair
      const whole = specs.filter((s) => [s.from, s.to].every((p) => p === 0 || p === len ||
        !(st.doc.sliceString(p - 1, p).charCodeAt(0) >= 0xD800 && st.doc.sliceString(p - 1, p).charCodeAt(0) <= 0xDBFF)));
      const tr = st.update({changes: whole});
      pending = pending.compose(tr.changes); st = tr.state;
    }
    const edits = P.serverEdits(saved, pending, sep), expected = st.doc.sliceString(0, st.doc.length, sep);
    rounds += 1;
    if (splice(original, edits) !== expected) failures += 1;
    if (round < 4) cases.push({sep, original, edits, expected});
  }
}
// line breaks typed or pasted take the file's ending
const crlf = CM.EditorState.create({doc: 'ab', selection: {anchor: 1}, extensions: [CM.EditorState.lineSeparator.of('\r\n'), P.breakFilter(CM, '\r\n')]});
const pasted = crlf.update({changes: {from: 1, insert: 'x\ny\rz\r\nw'}}).state;   // as a paste at the cursor
const lf = CM.EditorState.create({doc: 'ab', extensions: [CM.EditorState.lineSeparator.of('\n'), P.breakFilter(CM, '\n')]});
const lfPasted = lf.update({changes: {from: 2, insert: 'x\r\ny\r'}}).state;
const enter = crlf.update(crlf.replaceSelection(crlf.lineBreak)).state;
console.log(JSON.stringify({
  rounds, failures, cases,
  cp: [P.cpLength('a😀b'), P.cpLength('中文'), P.cpLength('')],
  pasted: {lines: pasted.doc.lines, text: pasted.doc.sliceString(0, pasted.doc.length, '\r\n'), head: pasted.selection.main.head},
  lfPasted: {lines: lfPasted.doc.lines, text: lfPasted.doc.sliceString(0, lfPasted.doc.length, '\n')},
  enter: enter.doc.sliceString(0, enter.doc.length, '\r\n'),
  multi: P.serverEdits(CM.Text.of(['中😀', 'ab', 'c']), CM.ChangeSet.of([{from: 3, insert: 'X'}, {from: 5, to: 6, insert: 'Y\nZ'}, {from: 8, insert: '!'}], 8), '\r\n'),
  includes: [P.includeMatches('\\input{a} %% \\input{b}'), P.includeMatches('50\\%% \\include{ch/one}'), P.includeMatches('\\\\%%\\input{c}'),
             P.includeMatches('\\input chapters/two \\subimport*{d/}{e}'), P.includeMatches('\\includegraphics{x} \\inputencoding')],
}));
"""


@pytest.fixture(scope="module")
def run() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    script = _HARNESS % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")), "latex": json.dumps(str(WEBAPP / "latex.js"))}
    return json.loads(subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True).stdout)


# -- coordinates and edits (22.1) ----------------------------------------------------------------------------------


@needs_node
def test_code_points_count_an_emoji_as_one(run):
    assert run["cp"] == [3, 2, 0]


@needs_node
def test_edits_from_many_composed_transactions_rebuild_the_text_on_lf_and_crlf_files_with_chinese_and_emoji(run):
    """300 random rounds (150 LF, 150 CRLF) of one to five transactions of up to three changes each: the edits, spliced
    in code points into the text as opened, give exactly what the editor holds, joined with the file's ending."""
    assert run["rounds"] == 300 and run["failures"] == 0


@needs_node
def test_several_edits_in_one_save_count_a_crlf_as_two_and_an_emoji_as_one(run):
    """'中😀' + CRLF + 'ab' + CRLF + 'c': CodeMirror's 3 (after the emoji, UTF-16) is the server's 2; its 5 ('a|b') is 5;
    its 8 (the end, after 'c') is 9: two code points, two CRLFs of two, two, one. An inserted line break is the file's
    CRLF."""
    assert run["multi"] == [{"from": 2, "to": 2, "insert": "X"},
                            {"from": 5, "to": 6, "insert": "Y\r\nZ"},
                            {"from": 9, "to": 9, "insert": "!"}]


@needs_node
def test_the_server_splices_the_editors_edits_into_exactly_the_bytes_the_editor_holds(run, tmp_path):
    """The node rounds' first cases sent through save_tex: the file on disk is byte for byte what the editor held."""
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    for i, case in enumerate(run["cases"]):
        rel = f"paper/c{i}.tex"
        (root / "paper").mkdir(exist_ok=True)
        raw = case["original"].encode("utf-8")
        (root / rel).write_bytes(raw)
        latex_edit.save_tex(root, {"path": rel, "revision": hashlib.sha256(raw).hexdigest(), "edits": case["edits"]})
        assert (root / rel).read_bytes() == case["expected"].encode("utf-8"), case


@needs_node
def test_a_line_break_typed_or_pasted_becomes_the_files_own(run):
    """In a CRLF file a pasted LF, CR and CRLF are each one CRLF line break; in an LF file a pasted CRLF and CR are LF;
    Return writes the file's ending. The cursor ends after what was pasted."""
    assert run["pasted"]["lines"] == 4 and run["pasted"]["text"] == "ax\r\ny\r\nz\r\nwb"
    assert run["pasted"]["head"] == len("ax\ny\nz\nw")
    assert run["lfPasted"] == {"lines": 3, "text": "abx\ny\n"}
    assert run["enter"] == "a\r\nb"


@needs_node
def test_the_editor_finds_includes_outside_comments_as_the_server_does(run):
    """A commented \\input is not a link; an escaped \\% does not start a comment, an escaped backslash before % does."""
    keys = [[m["key"].split("\u0000") for m in found] for found in run["includes"]]
    assert keys == [[["input", "a"]], [["include", "ch/one"]], [], [["input", "chapters/two"], ["subimport", "d/", "e"]], []]
    assert latex_edit.includes("\\input{a} % \\input{b}\n50\\% \\include{ch/one}\n\\\\%\\input{c}\n"
                               "\\input chapters/two \\subimport*{d/}{e}\n\\includegraphics{x} \\inputencoding") == [
        ("input", ("a",)), ("include", ("ch/one",)), ("input", ("chapters/two",)), ("subimport", ("d/", "e"))]


# -- include links and the main file (22.1, 22.4) ------------------------------------------------------------------


@pytest.fixture
def paper(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    files = {
        "paper/main.tex": "\\documentclass{article}\n\\begin{document}\n\\input{chapters/intro}\n% \\input{chapters/old}\n"
                          "\\include{chapters/results}\n\\end{document}\n",
        "paper/chapters/intro.tex": "\\section{引言}\n\\input{chapters/detail}\n\\subfile{local}\n\\input{../../outside}\n",
        "paper/chapters/detail.tex": "细节\n",
        "paper/chapters/local.tex": "本地\n",
        "paper/chapters/results.tex": "结果\n",
        "paper/chapters/old.tex": "旧的\n",
        "notes/loose.tex": "\\input{x}\n",
        "notes/x.tex": "x\n",
        "notes/doc.tex": "% \\documentclass{article}\n\\begin{document}\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def test_the_main_file_is_the_draft_itself_or_the_files_that_reach_it_transitively(paper):
    assert latex_edit.main_files(paper, "paper/main.tex") == ["paper/main.tex"]
    assert latex_edit.main_files(paper, "paper/chapters/intro.tex") == ["paper/main.tex"]
    assert latex_edit.main_files(paper, "paper/chapters/detail.tex") == ["paper/main.tex"]   # through intro
    assert latex_edit.main_files(paper, "paper/chapters/local.tex") == ["paper/main.tex"]    # \subfile, intro's folder
    assert latex_edit.main_files(paper, "paper/chapters/old.tex") == []                      # only in a comment
    assert latex_edit.main_files(paper, "notes/doc.tex") == []                               # \documentclass commented


def test_includes_resolve_from_the_main_files_folder_and_a_path_that_fails_is_text(paper):
    opened = latex_edit.read_tex(paper, "paper/chapters/intro.tex")
    assert opened["mains"] == ["paper/main.tex"]
    assert opened["links"] == [
        {"command": "input", "args": ["chapters/detail"], "path": "paper/chapters/detail.tex", "own_folder": False},
        {"command": "subfile", "args": ["local"], "path": "paper/chapters/local.tex", "own_folder": False},
        {"command": "input", "args": ["../../outside"], "path": None, "own_folder": False},
    ]


def test_with_no_main_file_an_include_resolves_from_the_drafts_own_folder_and_says_so(paper):
    assert latex_edit.read_tex(paper, "notes/loose.tex")["links"] == [
        {"command": "input", "args": ["x"], "path": "notes/x.tex", "own_folder": True}]


def test_an_include_into_a_hidden_folder_or_through_a_symlink_is_never_a_link(paper):
    (paper / ".researchos" / "backups" / "b.tex").parent.mkdir(parents=True, exist_ok=True)
    (paper / ".researchos" / "backups" / "b.tex").write_text("x", encoding="utf-8")
    (paper / "paper" / "link.tex").symlink_to(paper / "notes" / "x.tex")
    (paper / "paper" / "chapters" / "results.tex").write_text(
        "\\input{../.researchos/backups/b}\n\\input{link}\n\\input{/etc/passwd}\n\\input{chapters\\\\x}\n", encoding="utf-8")
    links = latex_edit.read_tex(paper, "paper/chapters/results.tex")["links"]
    assert [link["path"] for link in links] == [None, None, None, None]


def test_resolving_follows_tex_rules_for_each_command():
    assert latex_edit.resolve_include("input", ("ch/a",), "paper/ch", "paper") == "paper/ch/a.tex"
    assert latex_edit.resolve_include("include", ("ch/a.tex",), "paper/ch", "paper") == "paper/ch/a.tex"
    assert latex_edit.resolve_include("subfile", ("a",), "paper/ch", "paper") == "paper/ch/a.tex"
    assert latex_edit.resolve_include("import", ("parts/", "a"), "paper/ch", "paper") == "paper/parts/a.tex"
    assert latex_edit.resolve_include("subimport", ("parts/", "a"), "paper/ch", "paper") == "paper/ch/parts/a.tex"
    assert latex_edit.resolve_include("input", ("../../x",), "paper", "paper") is None
    assert latex_edit.resolve_include("input", ("",), "paper", "paper") is None


def test_reading_the_links_writes_nothing(paper):
    before = {p: p.read_bytes() for p in paper.rglob("*") if p.is_file()}
    latex_edit.read_tex(paper, "paper/chapters/intro.tex")
    assert {p: p.read_bytes() for p in paper.rglob("*") if p.is_file()} == before


# -- the hooks that put the editor on screen ---------------------------------------------------------------------


def test_the_editor_is_a_view_of_its_own_and_its_unsaved_text_is_asked_about_wherever_the_workspaces_is():
    app = (WEBAPP / "app.html").read_text(encoding="utf-8")
    assert '<section id="view-latex" class="view hidden"></section>' in app
    assert 'const FULL_WINDOW_VIEWS = ["canvas", "desk", "latex"];' in app
    assert 'if (currentView === "latex" && window.ROLatex && !ROLatex.mayClose()) return null;' in app
    assert 'v !== "node" && v !== "latex"' in app           # never reopened from storage
    assert "if (window.ROLatex) ROLatex.reset();" in app    # a project switch closes it
    workspace = (WEBAPP / "workspace.js").read_text(encoding="utf-8")
    assert "|| latexDirty(); }" in workspace and "window.ROLatex.isDirty()" in workspace


def test_edit_is_offered_on_a_tex_drafts_page_and_on_each_row_of_the_paper():
    node = (WEBAPP / "node.js").read_text(encoding="utf-8")
    assert node.count('mkButton(t("编辑"), "ws-text-link", () => ROLatex.open(') == 2
    latex = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    assert latex.count("btn-primary") == 1 and 'mkButton(t("保存"), "btn btn-primary"' in latex
    assert 'mkButton(t("撤销这次保存"), "ws-text-link' in latex and 'mkButton(t("载入新版本"), "ws-text-link"' in latex

"""DESIGN.md 22.1 "Like Word" (task V17, B3b): the LaTeX range reader, the decorations it feeds (a state field, the markup
hidden only where the selection does not touch it), KaTeX formulas, cite and ref chips, the folded 导言区, 显示源码, and
Return by context -- run under node on the vendored CodeMirror and KaTeX. The Return results are typeset by pdfLaTeX to
show the file still exports. Every test names its scenario.

Typing time, measured on this Mac (2026-11-13, node 3,000-line benchmark below: 2,998 lines, 181 kB, 580 formulas, the
reader and the decorations rebuilt on every key): median 4.3 ms, p95 5.7 ms, max 9.8 ms a key; the reader alone 1.1 ms;
a selection move that shows and hides nothing new 0.1 ms. The whole text is read again on each change: at 1.1 ms, reading
only the changed region (22.1) was not needed to stay under 16 ms."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_v17_latex_editor import _DRIVER, NODE, WEBAPP

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
TEXBIN = Path("/Library/TeX/texbin")
PDFLATEX = shutil.which("pdflatex") or (str(TEXBIN / "pdflatex") if (TEXBIN / "pdflatex").exists() else None)

_PRELUDE = r"""
const vm = require('vm'), fs = require('fs');
const ctx = vm.createContext({});
vm.runInContext(fs.readFileSync(__CM__, 'utf8'), ctx);
const kctx = vm.createContext({});
vm.runInContext(fs.readFileSync(__KATEX__, 'utf8'), kctx);
global.window = {ROCodeMirror: ctx.ROCodeMirror};
global.mk = (tag, cls, text) => ({tag, className: cls || '', textContent: text == null ? '' : String(text), innerHTML: '', attrs: {},
  setAttribute(k, v) { this.attrs[k] = v; }, addEventListener() {}});
eval(fs.readFileSync(__LATEX__, 'utf8'));
const CM = ctx.ROCodeMirror, P = window.ROLatex._pure, F = P.wordField(CM);
const out = {};
"""

_HARNESS = r"""
// -- the reader --
const R = (s) => { const r = P.readLatex(s); return {
  cmds: r.cmds.map((c) => ({name: c.name, role: c.role, text: s.slice(c.from, c.to), args: c.args.map((a) => s.slice(a.from, a.to)), n: c.n || null, depth: c.depth || null})),
  maths: r.maths.map((m) => ({text: s.slice(m.from, m.to), display: m.display, src: m.src})),
  raws: r.raws.map((x) => ({kind: x.kind, text: s.slice(x.from, x.to)})),
  envs: r.envs.map((e) => ({name: e.name, role: e.role, text: s.slice(e.from, e.to)})),
  preamble: r.preamble && s.slice(0, r.preamble.to), end: r.end >= 0 ? s.slice(r.end) : null}; };
out.reader = {
  nested: R('\\textbf{a {b} \\emph{c}} d \\foo{x}[y]{z {w}} \\bar \\section {S \\{ x}'),
  escaped: R('5\\$ and \\% $x$ and $$y$$ \\(z\\) \\[w\\] \\\\[2pt] \\$ $a\\$b$'),
  comments: R('a % $x$ \\section{y}\n50\\% $y$ \\\\% $not$\n'),
  verbatim: R('\\begin{verbatim}\n$x$ \\section{a}\n\\end{verbatim}\n\\begin{lstlisting}\n\\textbf{b}\n\\end{lstlisting}\n\\verb|$x$| \\verb+\\emph{c}+ $ok$'),
  unclosed: R('$a\n\nb$ and \\section{x\n\ny} $z$ \\textbf{'),
  lists: R('\\begin{enumerate}\n\\item a\n\\begin{itemize}\n\\item b\n\\end{itemize}\n\\item c\n\\end{enumerate}'),
  doc: R('\\documentclass{article}\n\\begin{document}\nx\n\\begin{equation*}\na\n\\end{equation*}\n\\end{document}\ntail'),
};

// -- the decorations --
const DOC = '\\documentclass{article}\n\\usepackage{amsmath}\n\\begin{document}\n\\section{Intro}\\label{sec:i}\n' +
  'Text \\textbf{bold} and \\emph{it} \\citep[p.~3]{a, b} see \\ref{sec:i}.\nInline $x^2$ and \\[ \\frac{1}{2} \\]\n' +
  '\\begin{equation}\na = b \\label{eq:1}\n\\end{equation}\nBad $\\frac{a}{$ here. \\newcommand{\\R}{\\mathbb{R}} \\unknown{x}\n' +
  '\\begin{itemize}\n  \\item one\n\\end{itemize}\n\\begin{enumerate}\n  \\item first\n  \\item second\n\\end{enumerate}\n' +
  '\\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}\n% a comment $q$ \\textbf{c}\n\\end{document}\n';
const drawn = (st) => {
  const got = [];
  st.field(F.field).deco.between(0, st.doc.length, (from, to, v) => {
    const w = v.spec.widget, el = w && w.toDOM({dispatch() {}});
    got.push({text: st.doc.sliceString(from, to), from, to, cls: v.spec.class || (v.spec.attributes && v.spec.attributes.class) || null,
      line: v.point === false && from === to && !w ? true : undefined, block: !!v.spec.block,
      widget: el ? {cls: el.className, text: el.textContent, html: el.innerHTML, expanded: el.attrs['aria-expanded']} : null});
  });
  return got;
};
const make = (doc, anchor, more) => CM.EditorState.create({doc, selection: {anchor}, extensions: [P.wordExtensions(CM)].concat(more || [])});
window.katex = kctx.katex;
out.away = drawn(make(DOC, DOC.length));
out.inHeading = drawn(make(DOC, DOC.indexOf('Intro') + 2));
out.inMath = drawn(make(DOC, DOC.indexOf('x^2') + 1));
const folded = make(DOC, DOC.length);
out.opened = drawn(folded.update({effects: F.preamble.of(true)}).state);
out.cursorInPreamble = drawn(make(DOC, DOC.indexOf('amsmath')));
delete window.katex;
const noMath = make(DOC, DOC.length);
out.noMath = drawn(noMath);
window.katex = kctx.katex;
out.mathArrives = drawn(noMath.update({effects: F.mathOn.of(true)}).state);
// a selection move that shows and hides nothing new keeps the very same decorations
const st0 = make(DOC, DOC.length), st1 = st0.update({selection: {anchor: DOC.length - 2}}).state;
out.sameDeco = st0.field(F.field).deco === st1.field(F.field).deco;
out.newDeco = st1.field(F.field).deco !== st1.update({selection: {anchor: DOC.indexOf('bold')}}).state.field(F.field).deco;

// -- Return by context --
const ret = (marked, sep, opts) => {
  opts = opts || {};
  const pos = marked.indexOf('|'), text = marked.replace('|', ''), head = opts.head != null ? opts.head : pos;
  const st = CM.EditorState.create({doc: text.split('\n').join(sep), selection: {anchor: pos, head},
    extensions: [CM.EditorState.lineSeparator.of(sep), P.breakFilter(CM, sep)].concat(opts.source ? [] : [P.wordExtensions(CM)])});
  const value = st.field(F.field, false), tr = st.update(P.returnSpec(st, value ? value.read : null)), after = tr.state;
  const at = after.selection.main.head, plainText = after.doc.toString();
  return {text: plainText.slice(0, at) + '|' + plainText.slice(at), file: after.doc.sliceString(0, after.doc.length, sep),
          edits: P.serverEdits(st.doc, tr.changes, sep)};
};
const RET = {
  running: 'Some |text here.',
  runningEnd: 'Some text.|\nMore of it.',
  blankLine: 'a\n\n|',
  heading: '\\section{Int|ro}\\label{s}\nNext',
  headingLast: 'x\n\\subsection*{A|b}',
  textbf: 'A \\textbf{bo|ld} b',
  caption: '\\caption{a|b}',
  item: '\\begin{itemize}\n  \\item one|\n\\end{itemize}',
  itemMiddle: '\\begin{enumerate}\n\\item on|e\n\\end{enumerate}',
  emptyItem: '\\begin{itemize}\n  \\item one\n  \\item |\n\\end{itemize}\nAfter',
  emptyItemEnd: '\\begin{itemize}\n  \\item one\n  \\item |\n\\end{itemize}',
  beforeItem: '\\begin{itemize}|\n  \\item one\n\\end{itemize}',
  inlineMath: 'a $x|y$ b',
  displayEnv: '\\begin{align}\na &= b|\n\\end{align}',
  verbatim: '\\begin{verbatim}\nx|\n\\end{verbatim}',
  tabular: '\\begin{tabular}{l}\na|\n\\end{tabular}',
  preamble: '\\documentclass{x}\n\\usepackage{y}|\n\\begin{document}\nz\n\\end{document}',
  inDocument: '\\documentclass{x}\n\\begin{document}\nz|\n\\end{document}',
  comment: 'a % note|\nb',
};
out.ret = {};
for (const sep of ['\n', '\r\n']) for (const k of Object.keys(RET)) out.ret[k + (sep === '\n' ? '' : '@crlf')] = ret(RET[k], sep);
out.retSource = ret('Some |text here.', '\n', {source: true});
out.retSelection = ret('Some |text here.', '\n', {head: 'Some text'.length + 1});
// ⇧Return, an input method composing, a read-only file
const fake = (st, composing) => ({state: st, composing, sent: [], dispatch(spec) { this.sent.push(spec); this.state = this.state.update(spec).state; }});
const v1 = fake(make('\\section{Int|ro}'.replace('|', ''), 12), false);
out.shiftReturn = {handled: P.onReturn(CM, true)(v1), text: v1.state.doc.toString()};
const v2 = fake(make('Some text', 4), true);
out.composing = {handled: P.onReturn(CM, false)(v2), sent: v2.sent.length};
const v3 = fake(CM.EditorState.create({doc: 'Some text', extensions: [CM.EditorState.readOnly.of(true), P.wordExtensions(CM)]}), false);
out.readOnly = {handled: P.onReturn(CM, false)(v3), sent: v3.sent.length};

// -- a draft edited by Return, for pdfLaTeX --
let st = CM.EditorState.create({doc: '\\documentclass{article}\n\\usepackage{amsmath}\n\\begin{document}\n\\section{Intro}\n' +
  'Some text here. \\textbf{bold words} end.\n\\begin{itemize}\n  \\item one\n\\end{itemize}\n\\begin{equation}\na = b\n\\end{equation}\n\\end{document}\n',
  extensions: [P.wordExtensions(CM)]});
const press = (anchor, offset) => {
  const at = anchor == null ? st.selection.main.head : st.doc.toString().indexOf(anchor) + offset;
  st = st.update({selection: {anchor: at}}).state;
  st = st.update(P.returnSpec(st, st.field(F.field).read)).state;
};
const type = (s) => { const at = st.selection.main.head; st = st.update({changes: {from: at, insert: s}, selection: {anchor: at + s.length}}).state; };
press('Intro', 2); type('Body of the introduction.');
press('bold words', 4);
press('\\item one', 9); type('two'); press(null); press(null); type('After the list.');
press('a = b', 3);
out.exported = st.doc.toString();
console.log(JSON.stringify(out));
"""

_TIMING = r"""
const lines = ['\\documentclass{article}', '\\usepackage{amsmath}', '\\begin{document}'];
for (let i = 0; lines.length < 2997; i++) {
  if (i % 40 === 0) lines.push('\\section{Section ' + i + '}\\label{sec:' + i + '}');
  else if (i % 10 === 0) lines.push('\\begin{equation}', 'y_{' + i + '} = \\alpha + \\beta x_i + \\varepsilon_i \\label{eq:' + i + '}', '\\end{equation}');
  else if (i % 5 === 0) lines.push('The effect $\\beta_' + i + ' = 0.' + i + '$ is \\textbf{large} and \\emph{robust} \\citep{a' + i + ', b' + i + '} (see \\ref{sec:' + i + '}).');
  else if (i % 7 === 0) lines.push('\\begin{itemize}', '  \\item first point with $x$', '  \\item second \\textit{point}', '\\end{itemize}');
  else lines.push('Running text of the paper, 中文的正文，一段一段地写下去，with numbers 3.14 and 5\\% and a comment % here');
}
lines.push('\\end{document}');
const text = lines.join('\n');
window.katex = {};
let st = CM.EditorState.create({doc: text, extensions: [P.wordExtensions(CM)], selection: {anchor: text.indexOf('Running text', 60000)}});
const ms = (t0) => Number(process.hrtime.bigint() - t0) / 1e6, stats = (xs) => { xs.sort((a, b) => a - b); return {median: xs[xs.length >> 1], p95: xs[Math.floor(xs.length * 0.95)], max: xs[xs.length - 1]}; };
for (let k = 0; k < 20; k++) st = st.update({changes: {from: st.selection.main.head, insert: 'w'}, selection: {anchor: st.selection.main.head + 1}}).state;  // warm up
const keys = [];
for (let k = 0; k < 300; k++) {
  const at = st.selection.main.head, t0 = process.hrtime.bigint();
  st = st.update({changes: {from: at, insert: k % 13 === 0 ? '$' : 'x'}, selection: {anchor: at + 1}}).state;
  st.field(F.field);
  keys.push(ms(t0));
}
const reads = [];
for (let k = 0; k < 100; k++) { const t0 = process.hrtime.bigint(); P.readLatex(st.doc.toString()); reads.push(ms(t0)); }
const moves = [];
for (let k = 0; k < 100; k++) { const t0 = process.hrtime.bigint(); st = st.update({selection: {anchor: text.indexOf('Running text', 60000) + (k % 5)}}).state; st.field(F.field); moves.push(ms(t0)); }
console.log(JSON.stringify({lines: st.doc.lines, formulas: P.readLatex(text).maths.length, key: stats(keys), read: stats(reads), move: stats(moves)}));
"""


def _node(body: str) -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    script = (_PRELUDE.replace("__CM__", json.dumps(str(WEBAPP / "vendor" / "codemirror.js")))
              .replace("__KATEX__", json.dumps(str(WEBAPP / "vendor" / "katex.js")))
              .replace("__LATEX__", json.dumps(str(WEBAPP / "latex.js"))) + body)
    done = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def run() -> dict:
    return _node(_HARNESS)


def _find(drawn: list[dict], text: str, **kw) -> list[dict]:
    return [d for d in drawn if d["text"] == text and all(d.get(k) == v for k, v in kw.items())]


# -- the range reader ------------------------------------------------------------------------------------------------


@needs_node
def test_the_reader_follows_nested_braces_and_takes_an_unknown_commands_adjacent_groups_as_its_arguments(run):
    r = run["reader"]["nested"]
    cmds = {c["name"]: c for c in r["cmds"]}
    assert cmds["textbf"]["text"] == "\\textbf{a {b} \\emph{c}}" and cmds["textbf"]["role"] == "style"
    assert cmds["emph"]["text"] == "\\emph{c}"                       # read inside its parent's argument
    assert cmds["foo"]["args"] == ["{x}", "[y]", "{z {w}}"] and cmds["foo"]["role"] == "other"
    assert cmds["bar"]["args"] == [] and cmds["bar"]["text"] == "\\bar"
    assert cmds["section"]["text"] == "\\section {S \\{ x}"        # an escaped brace does not close it


@needs_node
def test_an_escaped_dollar_or_percent_is_text_and_each_math_delimiter_is_read(run):
    r = run["reader"]["escaped"]
    assert [(m["text"], m["display"]) for m in r["maths"]] == [
        ("$x$", False), ("$$y$$", True), ("\\(z\\)", False), ("\\[w\\]", True), ("$a\\$b$", False)]
    assert r["raws"] == []                                          # \% is not a comment
    assert [m["src"] for m in r["maths"]] == ["x", "y", "z", "w", "a\\$b"]


@needs_node
def test_a_comment_hides_what_follows_it_on_its_line_and_an_escaped_percent_does_not_start_one(run):
    r = run["reader"]["comments"]
    assert [x["text"] for x in r["raws"]] == ["% $x$ \\section{y}", "% $not$"]
    assert [m["text"] for m in r["maths"]] == ["$y$"]
    assert all(c["name"] != "section" for c in r["cmds"])


@needs_node
def test_verbatim_like_environments_and_verb_are_left_raw_with_nothing_read_inside(run):
    r = run["reader"]["verbatim"]
    assert [x["kind"] for x in r["raws"]] == ["verbatim", "verbatim", "verb", "verb"]
    assert r["raws"][2]["text"] == "\\verb|$x$|" and r["raws"][3]["text"] == "\\verb+\\emph{c}+"
    assert [m["text"] for m in r["maths"]] == ["$ok$"]
    assert [c["name"] for c in r["cmds"]] == []


@needs_node
def test_math_and_a_short_argument_end_at_a_blank_line_so_one_unclosed_dollar_never_swaps_the_rest(run):
    r = run["reader"]["unclosed"]
    assert [m["text"] for m in r["maths"]] == ["$z$"]
    assert {c["name"]: c["role"] for c in r["cmds"]} == {"section": "other", "textbf": "other"}


@needs_node
def test_list_items_belong_to_their_innermost_list_numbered_within_it(run):
    items = [c for c in run["reader"]["lists"]["cmds"] if c["name"] == "item"]
    assert [(c["n"], c["depth"]) for c in items] == [(1, 1), (1, 2), (2, 1)]
    assert [(e["name"], e["role"]) for e in run["reader"]["lists"]["envs"]] == [("enumerate", "list"), ("itemize", "list")]


@needs_node
def test_the_preamble_runs_to_begin_document_and_a_math_environment_is_one_formula(run):
    r = run["reader"]["doc"]
    assert r["preamble"] == "\\documentclass{article}\n" and r["end"] == "\ntail"
    assert [m["text"] for m in r["maths"]] == ["\\begin{equation*}\na\n\\end{equation*}"]
    assert [(e["name"], e["role"]) for e in r["envs"]] == [("document", "text")]


# -- what is shown -----------------------------------------------------------------------------------------------------


@needs_node
def test_away_from_the_selection_headings_and_emphasis_hide_their_markup_and_take_their_style(run):
    d = run["away"]
    assert _find(d, "\\section{") and _find(d, "Intro", cls="latex-h latex-h2")
    assert _find(d, "bold", cls="latex-b") and _find(d, "it", cls="latex-i") and _find(d, "\\textbf{") and _find(d, "\\emph{")


@needs_node
def test_a_construct_the_selection_touches_shows_its_source_with_only_the_command_in_mono(run):
    d = run["inHeading"]
    assert not _find(d, "\\section{") and not _find(d, "Intro", cls="latex-h latex-h2")
    assert _find(d, "\\section", cls="latex-cmd")
    assert _find(d, "bold", cls="latex-b")                         # the rest stays as it was
    m = run["inMath"]
    assert not _find(m, "$x^2$") and _find(m, "\\[ \\frac{1}{2} \\]")


@needs_node
def test_formulas_are_typeset_by_katex_and_one_it_cannot_typeset_is_shown_as_written_in_mono(run):
    d = run["away"]
    inline = _find(d, "$x^2$")[0]["widget"]
    assert inline["cls"] == "latex-math" and 'class="katex"' in inline["html"]
    display = _find(d, "\\[ \\frac{1}{2} \\]")[0]["widget"]
    assert display["cls"] == "latex-math latex-math-display" and "katex-display" in display["html"]
    eq = _find(d, "\\begin{equation}\na = b \\label{eq:1}\n\\end{equation}")[0]["widget"]   # crosses lines; \label dropped
    assert eq["cls"] == "latex-math latex-math-display" and 'class="katex"' in eq["html"]
    bad = _find(d, "$\\frac{a}{$")[0]["widget"]
    assert bad == {"cls": "latex-math-raw", "text": "$\\frac{a}{$", "html": ""}


@needs_node
def test_until_katex_is_in_formulas_stay_as_written_and_they_are_typeset_when_it_arrives(run):
    assert not [d for d in run["noMath"] if d["widget"] and "latex-math" in d["widget"]["cls"]]
    assert len([d for d in run["mathArrives"] if d["widget"] and "latex-math" in d["widget"]["cls"]]) == 4


@needs_node
def test_cite_and_ref_are_quiet_chips_with_their_keys_and_unknown_commands_show_as_written(run):
    d = run["away"]
    assert _find(d, "\\citep[p.~3]{a, b}")[0]["widget"]["text"] == "a, b"
    assert _find(d, "\\citep[p.~3]{a, b}")[0]["widget"]["cls"] == "latex-chip latex-chip-cite"
    assert [x["widget"]["text"] for x in _find(d, "\\ref{sec:i}")] == ["sec:i"]
    assert [x["widget"]["text"] for x in _find(d, "\\label{sec:i}")] == ["sec:i"]
    assert _find(d, "\\newcommand", cls="latex-cmd") and _find(d, "\\unknown", cls="latex-cmd")
    assert _find(d, "\\begin{tabular}", cls="latex-cmd") and _find(d, "\\end{tabular}", cls="latex-cmd")
    # nothing of the comment, nor the arguments of an unknown command, is hidden or typeset
    assert not [x for x in d if "$q$" in x["text"] or x["text"] in ("{\\R}", "\\mathbb{R}", "{x}")]


@needs_node
def test_list_items_are_indented_with_a_bullet_or_their_number_and_the_item_command_hidden(run):
    d = run["away"]
    items = [x["widget"] for x in d if x["text"].startswith("\\item")]
    assert [(w["cls"], w["text"]) for w in items] == [("latex-item latex-bullet", ""), ("latex-item", "1."), ("latex-item", "2.")]
    assert [x["text"] for x in d if x["text"].startswith("\\item")] == ["\\item ", "\\item ", "\\item "]
    assert len([x for x in d if x["cls"] == "latex-li latex-li-1"]) == 3


@needs_node
def test_the_preamble_is_folded_under_its_label_and_opens_as_source_by_its_chevron_or_a_cursor_inside(run):
    fold = [x for x in run["away"] if x["widget"] and "latex-preamble" in x["widget"]["cls"]]
    assert len(fold) == 1 and fold[0]["block"] and fold[0]["from"] == 0
    assert fold[0]["text"] == "\\documentclass{article}\n\\usepackage{amsmath}"
    assert fold[0]["widget"] == {"cls": "latex-preamble has-chevron", "text": "导言区", "html": "", "expanded": "false"}
    for opened in (run["opened"], run["cursorInPreamble"]):
        bar = [x for x in opened if x["widget"] and "latex-preamble" in x["widget"]["cls"]]
        assert len(bar) == 1 and bar[0]["from"] == bar[0]["to"] == 0 and bar[0]["widget"]["expanded"] == "true"
        assert not [x for x in opened if 0 < x["from"] < len("\\documentclass{article}\n\\usepackage{amsmath}")]


@needs_node
def test_a_selection_move_that_changes_nothing_shown_keeps_the_decorations(run):
    assert run["sameDeco"] is True and run["newDeco"] is True


# -- Return by context -------------------------------------------------------------------------------------------------

RETURNS = {
    "running": "Some \n\n|text here.",
    "runningEnd": "Some text.\n\n|\nMore of it.",
    "blankLine": "a\n\n\n|",
    "heading": "\\section{Intro}\\label{s}\n\n|\n\nNext",
    "headingLast": "x\n\\subsection*{Ab}\n\n|",
    "textbf": "A \\textbf{bo\n|ld} b",
    "caption": "\\caption{a\n|b}",
    "item": "\\begin{itemize}\n  \\item one\n  \\item |\n\\end{itemize}",
    "itemMiddle": "\\begin{enumerate}\n\\item on\n\\item |e\n\\end{enumerate}",
    "emptyItem": "\\begin{itemize}\n  \\item one\n\\end{itemize}\n\n|\n\nAfter",
    "emptyItemEnd": "\\begin{itemize}\n  \\item one\n\\end{itemize}\n\n|",
    "beforeItem": "\\begin{itemize}\n|\n  \\item one\n\\end{itemize}",
    "inlineMath": "a $x\n|y$ b",
    "displayEnv": "\\begin{align}\na &= b\n|\n\\end{align}",
    "verbatim": "\\begin{verbatim}\nx\n|\n\\end{verbatim}",
    "tabular": "\\begin{tabular}{l}\na\n|\n\\end{tabular}",
    "preamble": "\\documentclass{x}\n\\usepackage{y}\n|\n\\begin{document}\nz\n\\end{document}",
    "inDocument": "\\documentclass{x}\n\\begin{document}\nz\n\n|\n\\end{document}",
    "comment": "a % note\n|\nb",
}


@needs_node
@pytest.mark.parametrize("case", sorted(RETURNS))
def test_return_does_what_22_1_says_where_the_cursor_is_in_an_lf_and_a_crlf_file(run, case):
    lf, crlf = run["ret"][case], run["ret"][case + "@crlf"]
    assert lf["text"] == RETURNS[case]
    assert crlf["text"] == RETURNS[case]                            # the same place, the same result
    assert crlf["file"] == RETURNS[case].replace("|", "").replace("\n", "\r\n")
    assert all("\n" not in e["insert"].replace("\r\n", "") for e in crlf["edits"])   # every break sent is the file's own


@needs_node
def test_with_source_shown_or_shift_return_return_is_a_plain_newline(run):
    assert run["retSource"]["text"] == "Some \n|text here."
    assert run["shiftReturn"] == {"handled": True, "text": "\\section{Int\nro}"}
    assert run["retSelection"]["text"] == "Some \n\n|here."           # the selected "text " is replaced


@needs_node
def test_return_leaves_an_input_methods_composition_and_a_read_only_file_alone(run):
    assert run["composing"] == {"handled": False, "sent": 0}
    assert run["readOnly"] == {"handled": False, "sent": 0}


@needs_node
@pytest.mark.skipif(PDFLATEX is None, reason="pdfLaTeX is not installed")
def test_a_draft_edited_by_return_in_a_heading_a_textbf_a_list_and_a_formula_still_typesets(run, tmp_path):
    text = run["exported"]
    assert text == ("\\documentclass{article}\n\\usepackage{amsmath}\n\\begin{document}\n\\section{Intro}\n\n"
                    "Body of the introduction.\n\nSome text here. \\textbf{bold\n words} end.\n\\begin{itemize}\n"
                    "  \\item one\n  \\item two\n\\end{itemize}\n\nAfter the list.\n\n\\begin{equation}\na =\n b\n"
                    "\\end{equation}\n\\end{document}\n")
    (tmp_path / "home").mkdir()
    (tmp_path / "paper.tex").write_text(text, encoding="utf-8")
    env = dict(os.environ, HOME=str(tmp_path / "home"), PATH=str(TEXBIN) + os.pathsep + os.environ.get("PATH", ""))
    done = subprocess.run([PDFLATEX, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", "paper.tex"],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout[-2000:]
    assert (tmp_path / "paper.pdf").read_bytes().startswith(b"%PDF-")


# -- typing time -------------------------------------------------------------------------------------------------------


@needs_node
def test_typing_stays_under_16_ms_a_key_on_a_3000_line_file_with_hundreds_of_formulas():
    got = _node(_TIMING)
    print("like Word, per key:", json.dumps(got))
    assert got["lines"] >= 2998 and got["formulas"] >= 300
    assert got["key"]["median"] < 16 and got["key"]["p95"] < 16
    assert got["move"]["median"] < 16


# -- in the editor: 显示源码, passages, words ----------------------------------------------------------------------------

_EDITOR = r"""
FakeView.prototype.dispatch = function (spec) {
  const tr = this.state.update(spec); this.state = tr.state;
  for (const listen of tr.state.facet(real.EditorView.updateListener)) listen({docChanged: tr.docChanged, selectionSet: !!spec.selection, changes: tr.changes, view: this, state: tr.state});
};
const out = {};
const TEXT = '\\section{Intro}\nA 显著 b \\textbf{c}\n';
evidenceAnswer = () => ({revision: 'r1', links: [{link: 'pl-1', last: 'pe-1', quote: '显著', file: 'figure:f.png', note: null, label: 'Li',
  at: null, state: 'found', from: 18, to: 20, candidates: [], file_missing: false}]});
const shown = (V) => {
  const got = [];
  for (const d of V.state.facet(real.EditorView.decorations)) {
    if (typeof d === 'function') continue;
    d.between(0, V.state.doc.length, (from, to, v) => { got.push(V.state.doc.sliceString(from, to) + '=' + (v.spec.class || (v.spec.widget ? 'widget' : 'hidden'))); });
  }
  return got.sort();
};
const open = async (path) => { L.open(path); await tick(); gets.shift().res({path, text: TEXT, revision: 'r1', line_ending: 'lf', read_only: false, links: [], mains: []}); await tick(); await tick(); await tick(); };
await open('a.tex');
let V = viewsMade[viewsMade.length - 1];
V.dispatch({selection: {anchor: TEXT.length}});
out.word = shown(V);
const source = button('显示源码');
out.sourceClass = source.className;
source.fire('click');
out.source = shown(V);
out.docSame = V.state.doc.toString() === TEXT && !L.isDirty();
// another file opens like Word; this one, opened again, keeps its source shown
await open('b.tex');
V = viewsMade[viewsMade.length - 1]; V.dispatch({selection: {anchor: TEXT.length}});
out.otherFile = shown(V).includes('Intro=latex-h latex-h2');
await open('a.tex');
V = viewsMade[viewsMade.length - 1]; V.dispatch({selection: {anchor: TEXT.length}});
out.reopened = shown(V).includes('Intro=latex-h latex-h2');
button('显示源码').fire('click');
out.back = shown(V).includes('Intro=latex-h latex-h2') && shown(V).includes('显著=latex-passage');
console.log(JSON.stringify(out));
"""


@needs_node
def test_show_source_turns_like_word_off_for_the_file_and_passages_stay_underlined_either_way():
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    script = prelude + "(async () => {\n" + _EDITOR + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });"
    done = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    assert "Intro=latex-h latex-h2" in out["word"] and "c=latex-b" in out["word"] and "显著=latex-passage" in out["word"]
    assert "\\section{=hidden" in out["word"]
    assert out["sourceClass"] == "ws-text-link latex-source"       # a text link (22.7)
    assert out["source"] == ["=widget", "显著=latex-passage"]          # only the passage and its count remain
    assert out["docSame"] is True                                  # a view change, never an edit
    assert out["otherFile"] is True and out["reopened"] is False and out["back"] is True


def test_the_words_of_like_word_go_through_t_with_their_english_and_the_toolbar_has_no_glyph():
    js = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    i18n = (WEBAPP / "i18n.js").read_text(encoding="utf-8")
    assert 'mkButton(t("显示源码"), "ws-text-link latex-source"' in js
    assert 'mk("div", "latex-preamble has-chevron", t("导言区"))' in js     # the chevron is drawn in CSS, 12.13
    assert '"导言区": "Preamble",' in i18n and '"显示源码": "Show source",' in i18n
    css = (WEBAPP / "workspace.css").read_text(encoding="utf-8")
    block = css[css.index("/* 22.1 like Word (B3b)"):css.index(".latex-include {")]
    assert "#" not in block.replace("/*", "").split("*/", 1)[1]       # palette tokens only
    assert 'content: ""' in block and "content: \"\\" not in block

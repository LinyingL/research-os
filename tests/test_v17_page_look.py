"""DESIGN.md 22.12 B (task V17, step B; the researcher, 2026-11-14: "我觉得很简陋…还需要优化UI"): the LaTeX editor as a
clean writing page. Like Word, the text is a centred column of a page's measure; \\begin{document} and \\end{document}
are quiet label lines, \\maketitle the title block read from the preamble, an abstract's \\begin a small heading over
indented lines, a table a read-only grid with its caption, a figure its files and caption, {\\heiti …} and {\\large …}
drawn in their face and size -- wherever the selection is (B1 of 22.12, the same day: the edit view shows no code;
tests/test_v17_no_code_view.py), never in 显示代码, and anything the reader doubts a quiet chip, never its code. A status line under the text counts the body's words and says
whether it is saved. Run under node on the vendored CodeMirror and KaTeX (the decorations, the word count, the 16 ms a
key) and on the stand-in page of test_v17_latex_editor (the status line, the columns); the style sheet read as written.
Every test names its scenario."""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from researchos import latex_edit
from test_v16_clay import _declarations
from test_v17_latex_editor import _DRIVER, NODE, WEBAPP
from test_v17_latex_word import _node

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
PAPER = latex_edit.TEMPLATES["en_paper"].replace("<TITLE>", "My Paper")

_LOOK = r"""
window.katex = kctx.katex;
const textOf = (e) => typeof e === 'string' ? e : (e.textContent || '') + (e.children || []).map(textOf).join('');
const shape = (e) => typeof e === 'string' ? e : e.tag + (e.className ? '.' + e.className.split(' ').join('.') : '') +
  (e.textContent ? '"' + e.textContent + '"' : '') + ((e.children || []).length ? '[' + e.children.map(shape).join(',') + ']' : '');
const make = (doc, anchor, head) => CM.EditorState.create({doc, selection: {anchor, head: head == null ? anchor : head}, extensions: [P.wordExtensions(CM)]});
// what is drawn: [the text it covers, its class or the shape of its widget, whether it is a block]
const drawn = (st) => { const got = []; st.field(F.field).deco.between(0, st.doc.length, (a, b, v) => {
  const w = v.spec.widget, el = w && w.toDOM({dispatch() {}});
  got.push({from: a, text: st.doc.sliceString(a, b), cls: v.spec.class || (v.spec.attributes && v.spec.attributes.class) || null, block: !!v.spec.block,
    shape: el ? shape(el) : null, words: el ? textOf(el) : null}); }); return got; };
const at = (doc, word, k) => doc.indexOf(word) + (k || 0);

// -- the researcher's paper, as 新建文稿 makes it --
const PAPER = __PAPER__;
out.paper = {away: drawn(make(PAPER, at(PAPER, 'Start writing') + 2))};
for (const [name, word] of [['docBegin', '\\begin{document}'], ['title', '\\maketitle'], ['absBegin', '\\begin{abstract}'], ['absEnd', '\\end{abstract}'], ['docEnd', '\\end{document}']])
  out.paper[name] = drawn(make(PAPER, at(PAPER, word, 3)));
const open = make(PAPER, at(PAPER, 'Start writing')).update({effects: F.preamble.of(true)}).state;
out.paper.preambleOpen = drawn(open);

// -- the title block --
const titled = (pre, body) => '\\documentclass{article}\n' + pre + '\\begin{document}\n' + (body || '\\maketitle') + '\nText.\n\\end{document}\n';
const title = (pre, body) => { const d = titled(pre, body); return drawn(make(d, d.indexOf('Text.') + 1)).filter((x) => x.shape && x.shape.startsWith('div.latex-titleblock')).map((x) => x.shape); };
out.title = {
  styled: title('\\title{A \\textbf{Bold} and $x^2$ \\thanks{fund}}\n\\author{Ann \\and Bo}\n\\date{1 May 2026}\n'),
  noTitle: title('\\author{Ann}\n'),
  emptyDate: title('\\title{T}\n\\date{}\n'),
  sameLine: title('\\title{T}\n', 'x \\maketitle'),
  chapter: drawn(make('\\maketitle\nText.\n', 14)).filter((x) => x.shape && x.shape.startsWith('div.latex-titleblock')).map((x) => x.shape),
};

// -- tables --
const body = (s) => '\\documentclass{article}\n\\begin{document}\nBefore.\n' + s + '\nAfter.\n\\end{document}\n';
const tableOf = (s) => { const d = body(s); const got = drawn(make(d, d.indexOf('After.') + 1)); const b = got.find((x) => x.shape && x.shape.startsWith('div.latex-table-preview'));
  return b ? b.shape : got.filter((x) => x.shape && x.shape.includes('latex-chip')).map((x) => x.shape).join(' '); };
const T = (rows, spec) => '\\begin{tabular}{' + (spec || 'lcr') + '}\n' + rows + '\n\\end{tabular}';
out.tables = {
  booktabs: tableOf('\\begin{table}[htbp]\n\\centering\n\\caption{Main \\emph{results}}\n\\label{tab:main}\n' + T('\\toprule\nA & B & C \\\\\n\\midrule\n1 & \\textbf{2} & $3$ \\\\\n\\bottomrule') + '\n\\end{table}'),
  hline: tableOf(T('\\hline\na & b\\\\[2pt]\n\\hline\nc \\& d & {e & f} & \\multicolumn{1}{c}{g} \\\\\n\\hline')),
  noCaption: tableOf('\\begin{table}\n' + T('a & b \\\\') + '\n\\end{table}'),
  unknownInCell: tableOf(T('\\foo{x} & \\cite{k} & 50\\% \\\\')),
  unbalanced: tableOf(T('a & {b \\\\')),
  nested: tableOf(T('a & \\begin{tabular}{l}x\\end{tabular} \\\\')),
  tooManyCells: tableOf(T('a & b & c & d \\\\')),
  partialRule: tableOf(T('a & b & c \\\\\n\\cmidrule(lr){1-2}\nd & e & f \\\\')),
  oddSpec: tableOf(T('a \\\\', 'lQ')),
  tooManyColumns: tableOf(T('a \\\\', '*{30}{l}')),
  extraInFloat: tableOf('\\begin{table}\n\\resizebox{\\linewidth}{!}{' + T('a \\\\') + '}\n\\end{table}'),
  notAlone: tableOf('x ' + T('a & b \\\\')),
  twoCaptions: tableOf('\\begin{table}\n\\caption{a}\\caption{b}\n' + T('a \\\\') + '\n\\end{table}'),
};
// 22.12 T: a table the grid reader is sure of is its cells and caption, typed in place: what is drawn, in order (a cell
// its text and class, a row or the caption's line its class, a widget its shape; hidden markup left out)
const gridShape = (s) => { const d = body(s), a = d.indexOf('Before.') + 7, b = d.indexOf('After.');
  return drawn(make(d, b + 1)).filter((x) => x.from > a && x.from < b).map((x) => x.shape || (x.cls ? (x.cls.startsWith('latex-cell') ? x.text + '=' + x.cls : x.cls) : null)).filter(Boolean); };
out.grids = {
  booktabs: gridShape('\\begin{table}[htbp]\n\\centering\n\\caption{Main \\emph{results}}\n\\label{tab:main}\n' + T('\\toprule\nA & B & C \\\\\n\\midrule\n1 & \\textbf{2} & $3$ \\\\\n\\bottomrule') + '\n\\end{table}'),
  noCaption: gridShape('\\begin{table}\n' + T('a & b \\\\', 'll') + '\n\\end{table}'),
  fewerCells: tableOf('\\begin{table}\n' + T('a & b \\\\') + '\n\\end{table}'),
  unknownInCell: gridShape(T('\\foo{x} & \\cite{k} & 50\\% \\\\')),
};
const tdoc = body('\\begin{table}\n\\caption{C}\n' + T('a & b & c \\\\') + '\n\\end{table}');
out.tableSame = [tdoc.indexOf('a & b'), tdoc.indexOf('\\caption'), tdoc.indexOf('C}'), tdoc.indexOf('After.')].every((p) => JSON.stringify(drawn(make(tdoc, p))) === JSON.stringify(drawn(make(tdoc, 0))));
out.grid = P.tabularGrid('{l|c}\n\\toprule\na & b \\\\\n\\bottomrule\n', 0);

// -- figures and a bare \includegraphics --
// a figure with one caption (B1): its images and 「图 N：」, then the caption's own words, editable, then what follows hidden
const figureOf = (s) => { const d = body(s); return drawn(make(d, d.indexOf('After.') + 1)).filter((x) => x.from > d.indexOf('Before.') && x.from < d.indexOf('After.'))
  .map((x) => x.shape || (x.cls ? x.text + '=' + x.cls : x.text ? 'hidden' : null)).filter(Boolean); };
out.figures = {
  one: figureOf('\\begin{figure}[htbp]\n\\centering\n\\includegraphics[width=\\linewidth]{figs/plot.png}\n\\caption{A \\textit{plot}}\n\\label{fig:p}\n\\end{figure}'),
  two: figureOf('\\begin{figure}\n\\includegraphics{a.png}\n\\includegraphics{b.pdf}\n\\caption{Two}\n\\end{figure}'),
  sub: figureOf('\\begin{figure}\n\\begin{subfigure}{.5\\linewidth}\n\\includegraphics{a.png}\n\\end{subfigure}\n\\caption{Sub}\n\\end{figure}'),
  none: figureOf('\\begin{figure}\n\\includegraphics{a.png}\n\\end{figure}'),
};
const gdoc = body('See \\includegraphics[width=2cm]{figs/a.png} here.');
out.graphic = {away: drawn(make(gdoc, gdoc.indexOf('After.'))).filter((x) => x.text.startsWith('\\includegraphics')).map((x) => [x.shape, x.block]),
  touched: drawn(make(gdoc, gdoc.indexOf('figs'))).filter((x) => x.text.startsWith('\\includegraphics')).map((x) => [x.text, x.cls])};

// -- font and size groups --
const G = '\\documentclass{ctexart}\n\\usepackage{x}\n{\\heiti 不在正文}\n\\begin{document}\n甲 {\\songti 宋} {\\heiti 黑} {\\kaishu 楷} {\\fangsong 仿} {\\small s} {\\large l} {\\Large L} \\textsf{sf} \\texttt{tt} \\textrm{rm} \\textbf{\\small arg} {\\heiti} {\\large $a}$ b}\n\\end{document}\n';
const gst = drawn(make(G, G.indexOf('甲') + 1));
out.groups = {marked: gst.filter((x) => x.cls && /latex-(f|s|sf|tt|rm)\b|latex-(f|s)-/.test(x.cls)).map((x) => [x.text, x.cls]),
  hidden: gst.filter((x) => !x.cls && !x.shape && x.text).map((x) => x.text),
  written: gst.filter((x) => x.cls === 'latex-cmd').map((x) => x.text)};
out.groups.touched = drawn(make(G, G.indexOf('楷'))).filter((x) => ['楷', '{\\kaishu ', '\\kaishu'].includes(x.text)).map((x) => [x.text, x.cls]);
// the cursor in the preamble opens it: nothing there is drawn but the bar that folds it
out.groups.preamble = drawn(make(G, G.indexOf('不在') + 1)).filter((x) => x.from < G.indexOf('\\begin{document}')).map((x) => [x.from, x.shape]);
out.groups.read = P.readLatex(G).groups.map((g) => [g.name, G.slice(g.from, g.to)]);

// -- a click on the title block opens 文档信息 (B1: the editor's hook); on a table it selects the table whole --
const mk0 = global.mk;
global.mk = (tag, cls, text) => { const e = mk0(tag, cls, text); e.addEventListener = (ev, fn) => { (e.on = e.on || {})[ev] = fn; }; return e; };
let titleHits = 0; F.hooks.title = () => { titleHits += 1; };
let titleWidget = null;
make(PAPER, at(PAPER, 'Start writing')).field(F.field).deco.between(0, PAPER.length, (a, b, v) => { if (v.spec.widget && String(v.spec.widget.key).includes('My Paper')) titleWidget = {w: v.spec.widget, a}; });
const sent = [], fakeView = {posAtDOM: () => titleWidget.a, dispatch: (spec) => sent.push(spec), focus: () => sent.push('focus')};
const titleEl = titleWidget.w.toDOM(fakeView);
titleEl.on.mousedown({button: 0, preventDefault: () => sent.push('prevented')});
out.click = {sent, at: titleWidget.a, line: PAPER.slice(titleWidget.a).split('\n')[0], ignored: titleWidget.w.ignoreEvent(), titled: titleHits};
F.hooks.title = null;
// (a table with merged cells: one unit -- 22.12 T fills the others in like Word)
let tableW = null; const tdoc2 = body('\\begin{table}\n\\caption{C}\n' + T('\\multicolumn{3}{c}{abc} \\\\') + '\n\\end{table}');
const tst = make(tdoc2, 0); tst.field(F.field).deco.between(0, tdoc2.length, (a, b, v) => { if (v.spec.kind === 'table') tableW = {w: v.spec.widget, a, b}; });
const tsent = [], tview = {state: tst, posAtDOM: () => tableW.a, dispatch: (spec) => tsent.push(spec), focus() {}};
tableW.w.toDOM(tview).on.mousedown({button: 0, preventDefault() {}});
out.tableClick = {sent: tsent, from: tableW.a, to: tableW.b};
global.mk = mk0;

// -- the word count --
out.words = {
  body: P.bodyWords(...((s) => [s, P.readLatex(s)])('\\documentclass{article}\n\\usepackage{x}\n\\title{Not counted}\n\\begin{document}\n\\section{Two words}\n' +
    '中文三字 and three words here. % comment words\n$x + y$ \\cite{key} \\ref{sec:a} \\label{l} \\textbf{bold} \\includegraphics[width=1in]{a.png} 3.14\n' +
    '\\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}\n\\end{document}\nafter the end\n')),
  chapter: P.bodyWords(...((s) => [s, P.readLatex(s)])('Hello world 你好 \\emph{again}\n')),
  paper: P.bodyWords(PAPER, P.readLatex(PAPER)),
};
out.fold = [P.preambleWords({cls: 'article', packages: 3}), P.preambleWords({}), P.preambleWords(undefined)];

// -- drawn again only where needed: equal to drawing whole, edit after edit, among the page's blocks --
let seed = 11;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
const bits = ['x', ' ', '\n', '\n\n', '{', '}', '$', '\\', '&', '\\\\', '\\begin{table}', '\\end{table}', '\\begin{tabular}{ll}', '\\end{tabular}',
  '\\toprule', '\\midrule', '{\\heiti ', '{\\large ', '\\maketitle', '\\title{T}', '\\author{A}', '\\begin{abstract}', '\\end{abstract}', '\\caption{c}',
  '\\includegraphics{a.png}', '\\begin{figure}', '\\end{figure}', '\\begin{document}', '\\end{document}', '\\textsf{s}', '%', '中'];
const sig = (s) => { const got = []; s.field(F.field).deco.between(0, s.doc.length, (a, b, v) => { const w = v.spec.widget;
  got.push([a, b, v.spec.class || (v.spec.attributes && v.spec.attributes.class) || '', w ? w.constructor.name + JSON.stringify(w) : '', !!v.spec.block].join('|')); }); return got.sort(); };
const fresh = (s, op) => { const f = CM.EditorState.create({doc: s.doc, selection: s.selection, extensions: [P.wordExtensions(CM)]});
  return op ? f.update({effects: F.preamble.of(true)}).state : f; };
let cur = CM.EditorState.create({doc: PAPER.replace('Start writing here.', 'Text {\\heiti 黑体} and {\\large big}.\n\\begin{table}\n\\caption{C}\n\\begin{tabular}{ll}\n\\toprule\na & b \\\\\n\\bottomrule\n\\end{tabular}\n\\end{table}\n\\begin{figure}\n\\includegraphics{a.png}\n\\caption{F}\n\\end{figure}\nEnd.'),
  extensions: [P.wordExtensions(CM)]});
let opened = false, mismatch = null, steps = 0;
for (let k = 0; k < 3000 && !mismatch; k++) {
  const len = cur.doc.length, r = rnd(12), a = rnd(len + 1);
  let spec;
  if (r >= 10) spec = {changes: {from: a, insert: bits[rnd(bits.length)]}};
  else if (r < 5) { const b = bits[rnd(bits.length)]; spec = {changes: {from: a, insert: b}, selection: {anchor: a + b.length}}; }
  else if (r < 7) { const b = Math.min(len, a + 1 + rnd(12)); spec = {changes: {from: a, to: b}, selection: {anchor: a}}; }
  else if (r < 9) spec = {selection: {anchor: a, head: rnd(len + 1)}};
  else { opened = !opened; spec = {effects: F.preamble.of(opened)}; }
  cur = cur.update(spec).state;
  steps += 1;
  const x = sig(cur), y = sig(fresh(cur, opened));
  if (JSON.stringify(x) !== JSON.stringify(y)) mismatch = {step: k, doc: cur.doc.toString(), sel: [cur.selection.main.anchor, cur.selection.main.head], only: x.filter((z) => !y.includes(z)), missing: y.filter((z) => !x.includes(z))};
}
out.incremental = {steps, mismatch};
console.log(JSON.stringify(out));
"""

_TIMING = r"""
// 22.1's file, with the page's blocks among its lines: a title and an abstract, a table float every 50 lines, a figure
// every 70, font and size groups in the running text
const lines = ['\\documentclass{article}', '\\usepackage{amsmath}', '\\usepackage{booktabs}', '\\title{A \\textbf{long} paper}', '\\author{A}',
  '\\begin{document}', '\\maketitle', '\\begin{abstract}', 'We study $x$ and {\\large y}.', '\\end{abstract}'];
for (let i = 0; lines.length < 2997; i++) {
  if (i % 40 === 0) lines.push('\\section{Section ' + i + '}\\label{sec:' + i + '}');
  else if (i % 50 === 3) lines.push('\\begin{table}[htbp]', '\\centering', '\\caption{Results ' + i + '}', '\\label{tab:' + i + '}', '\\begin{tabular}{lcr}', '\\toprule',
    'A & B & C \\\\', '\\midrule', '1 & $' + i + '$ & \\textbf{3} \\\\', '\\bottomrule', '\\end{tabular}', '\\end{table}');
  else if (i % 70 === 5) lines.push('\\begin{figure}[htbp]', '\\centering', '\\includegraphics[width=\\linewidth]{figs/f' + i + '.png}', '\\caption{Figure ' + i + '}', '\\end{figure}');
  else if (i % 10 === 0) lines.push('\\begin{equation}', 'y_{' + i + '} = \\alpha + \\beta x_i + \\varepsilon_i \\label{eq:' + i + '}', '\\end{equation}');
  else if (i % 5 === 0) lines.push('The effect $\\beta_' + i + ' = 0.' + i + '$ is \\textbf{large} and {\\heiti 稳健} \\citep{a' + i + '} (see \\ref{sec:' + i + '}).');
  else if (i % 7 === 0) lines.push('\\begin{itemize}', '  \\item first point with $x$', '  \\item second {\\large point}', '\\end{itemize}');
  else lines.push('Running text of the paper, 中文的正文，{\\kaishu 一段一段}地写下去，with numbers 3.14 and 5\\% and a comment % here');
}
lines.push('\\end{document}');
const text = lines.join('\n');
window.katex = kctx.katex;
const ms = (t0) => Number(process.hrtime.bigint() - t0) / 1e6, stats = (xs) => { xs.sort((a, b) => a - b); return {median: xs[xs.length >> 1], p95: xs[Math.floor(xs.length * 0.95)], max: xs[xs.length - 1]}; };
// one key: the transaction and every widget within 60 lines of the cursor the view would draw anew, by its toDOM
const typing = (at, n, key) => {
  let st = CM.EditorState.create({doc: text, extensions: [P.wordExtensions(CM)], selection: {anchor: at}});
  const shown = (s) => { const l = s.doc.lineAt(s.selection.main.head), from = s.doc.line(Math.max(1, l.number - 60)).from, to = s.doc.line(Math.min(s.doc.lines, l.number + 60)).to, got = []; s.field(F.field).deco.between(from, to, (a, b, v) => { if (v.spec.widget) got.push({a, b, w: v.spec.widget}); }); return got; };
  // typed as the edit view types (B1: through its filter), so a key never lands in hidden markup
  for (let k = 0; k < 20; k++) st = st.update({changes: {from: st.selection.main.head, insert: 'w'}, selection: {anchor: st.selection.main.head + 1}, userEvent: 'input.type'}).state;
  const keys = [];
  let drawnAnew = 0;
  for (let k = 0; k < n; k++) {
    const head = st.selection.main.head, before = shown(st), t0 = process.hrtime.bigint();
    const tr = st.update({changes: {from: head, insert: key(k)}, selection: {anchor: head + 1}, userEvent: 'input.type'});
    st = tr.state;
    st.field(F.field);
    let spent = ms(t0);
    const had = new Map(before.map((x) => [tr.changes.mapPos(x.a, 1) + ':' + tr.changes.mapPos(x.b, -1), x.w]));
    for (const {a, b, w} of shown(st)) if (!(had.get(a + ':' + b) && had.get(a + ':' + b).eq(w))) { const t2 = process.hrtime.bigint(); w.toDOM({dispatch() {}, state: st, focus() {}}); spent += ms(t2); drawnAnew += 1; }
    keys.push(spent);
  }
  return {keys: stats(keys), drawnAnew};
};
const running = typing(text.indexOf('Running text', 60000), 300, (k) => (k % 13 === 0 ? '$' : 'x'));
const inCell = typing(text.indexOf('1 & $', 60000) + 1, 200, () => 'x');   // in a table the cursor shows as source
const t0 = process.hrtime.bigint();
for (let k = 0; k < 20; k++) P.bodyWords(text, P.readLatex(text));
const read = P.readLatex(text);
console.log(JSON.stringify({lines: text.split('\n').length, formulas: read.maths.length, tables: read.envs.filter((e) => e.name === 'table').length,
  groups: read.groups.length, running: running.keys, runningDrawn: running.drawnAnew, inCell: inCell.keys, wordsMs: ms(t0) / 20}));
"""


@pytest.fixture(scope="module")
def look() -> dict:
    return _node(_LOOK.replace("__PAPER__", json.dumps(PAPER)))


def _blocks(drawn: list[dict]) -> list[str]:
    return [d["shape"] for d in drawn if d["block"]]


# -- the scaffolding of the researcher's paper ---------------------------------------------------------------------------


@needs_node
def test_the_new_english_paper_reads_as_a_page_with_its_title_block_abstract_heading_and_body_labels(look):
    away = look["paper"]["away"]
    assert _blocks(away) == [
        # B1: the preamble and \\begin{document} are one quiet line (fontenc, amsmath, graphicx, booktabs), never opened here
        'div.latex-preamble-block[button.latex-preamble"导言区：文档类 article，4 个宏包",div.latex-doc-mark"正文开始"]',
        'div.latex-titleblock[div.latex-title[span"My Paper"],div.latex-title-date"日期：导出当天"]',   # \\author{} left out
        'div.latex-doc-mark"正文结束"',
    ]
    # 22.12 M (F4): the abstract's heading is the document's own word, as its PDF prints it
    assert [d["shape"] for d in away if d["shape"] and "latex-abstract-head" in d["shape"]] == ['span.latex-abstract-head"Abstract"']
    abstract = [d for d in away if d["cls"] == "latex-abstract-line"]
    assert len(abstract) == 1                                         # "Write the abstract here." indented
    assert [d["text"] for d in away if d["cls"] == "latex-hline"] == [""]   # space above \\section{Introduction}
    assert not [d for d in away if d["cls"] == "latex-cmd"]           # no raw grey mono line left in the body


@needs_node
@pytest.mark.parametrize("case", ["docBegin", "title", "absBegin", "absEnd", "docEnd", "preambleOpen"])
def test_the_page_is_drawn_the_same_wherever_the_selection_is(look, case):
    assert look["paper"][case] == look["paper"]["away"]               # B1: a block never shows its code


@needs_node
def test_a_click_on_the_title_block_opens_document_details_and_one_on_a_table_selects_it_whole(look):
    c = look["click"]
    assert c["sent"] == ["prevented"] and c["titled"] == 1            # 版式's 文档信息 (the editor's hook), no cursor moved
    assert c["line"] == "\\maketitle" and c["ignored"] is True
    t = look["tableClick"]
    assert t["sent"] == [{"selection": {"anchor": t["from"], "head": t["to"]}, "userEvent": "select.atom"}]


# -- the title block -------------------------------------------------------------------------------------------------------


@needs_node
def test_the_title_block_styles_what_the_editor_knows_and_shows_the_rest_as_quiet_words(look):
    t = look["title"]
    [styled] = t["styled"]
    assert styled.startswith('div.latex-titleblock[div.latex-title[span"A ",span.latex-b[span"Bold"],span" and ",span.latex-math')
    assert 'span.latex-chip.latex-chip-unit"fund"' in styled           # \\thanks{fund}: its words, never its code
    assert 'div.latex-title-author[span"Ann ",span.latex-title-and,span"Bo"]' in styled
    assert styled.endswith('div.latex-title-date[span"1 May 2026"]]')
    assert t["noTitle"] == ['div.latex-titleblock[div.latex-title-none"（没有设置标题）",div.latex-title-author[span"Ann"],div.latex-title-date"日期：导出当天"]']
    assert t["emptyDate"] == ['div.latex-titleblock[div.latex-title[span"T"]]']          # \\date{} prints no date
    assert t["sameLine"] == []                                          # not alone on its line: a chip
    assert t["chapter"] == ['div.latex-titleblock[div.latex-title-none"（没有设置标题）"]']  # a chapter has no preamble: no date guessed


# -- tables ----------------------------------------------------------------------------------------------------------------


@needs_node
def test_a_table_is_its_cells_typed_in_place_under_its_numbered_caption_or_a_read_only_grid_where_cells_are_merged(look):
    # 22.12 T (acceptance, 2026-11-14): the caption's words after 「表 1：」 on a centred line, then each row a line of cells,
    # the rules as the PDF draws them, a formula, a style or a chip in a cell drawn as in the text
    g = look["grids"]
    W = "width:min(100%,"
    assert g["booktabs"][:3] == ["latex-tcap-line", 'span.latex-caption-word"Table 1: "', "latex-i"]   # 22.12 M: as the PDF prints it
    rows = [x for x in g["booktabs"] if x.startswith("latex-trow")]
    assert rows == ["latex-trow latex-trule-heavy", "latex-trow latex-trule-light latex-trule-below-heavy"]
    cells = [x for x in g["booktabs"] if "=latex-cell" in x]
    assert cells == ["A =latex-cell", " B =latex-cell latex-col-c", " C =latex-cell latex-col-r", "1 =latex-cell", " \\textbf{2} =latex-cell latex-col-c", " $3$ =latex-cell latex-col-r"]
    assert "latex-b" in g["booktabs"] and any(x.startswith("span.latex-math") for x in g["booktabs"])
    assert [x for x in g["noCaption"] if "latex-cell" in x or x.startswith("latex-t")] == ["latex-trow", "a =latex-cell", " b =latex-cell"]
    assert 'span.latex-chip.latex-chip-unit"x"' in g["unknownInCell"] and 'span.latex-chip.latex-chip-cite"k"' in g["unknownInCell"]
    assert " 50\\% =latex-cell latex-col-r" in g["unknownInCell"]
    t = look["tables"]
    # merged cells: a read-only grid, one unit. \\[2pt] ends a row, \& and a brace group's & are a cell's text,
    # \multicolumn its last argument's words
    assert t["hline"] == ('div.latex-table-preview[table.latex-table[tbody[tr.latex-rule-light[td[span"a"],td.latex-col-c[span"b"],td.latex-col-r],'
                          'tr.latex-rule-light.latex-rule-below-light[td[span"c & d"],td.latex-col-c[span"e & f"],td.latex-col-r[span"g"]]]]]')
    assert t["notAlone"].startswith("div.latex-table-preview")         # drawn in its line (B1), never as written
    assert g["fewerCells"].startswith("div.latex-table-preview")       # a row with fewer cells than columns: one unit too


@needs_node
@pytest.mark.parametrize("case", ["unbalanced", "nested", "tooManyCells", "partialRule", "oddSpec", "tooManyColumns", "extraInFloat", "twoCaptions"])
def test_a_table_the_reader_doubts_is_a_quiet_chip_never_guessed_and_never_its_code(look, case):
    shown = look["tables"][case]
    # a bare tabular says 表格; a table float, in a document whose words are known, the PDF's own name (22.12 M, F4)
    assert re.match(r'div\.latex-chip\.latex-chip-unit\.latex-chip-block"(?:表格|Table(?:: |"))', shown), shown
    assert "\\" not in shown


@needs_node
def test_a_table_is_drawn_the_same_wherever_the_cursor_is_and_the_grid_reader_keeps_rules_apart(look):
    assert look["tableSame"] is True
    g = look["grid"]
    assert g["cols"] == ["l", "c"]
    assert g["rows"] == [{"cells": ["a", "b"], "above": "heavy", "below": "heavy"}]


# -- figures ---------------------------------------------------------------------------------------------------------------


@needs_node
def test_a_figure_is_its_files_and_its_editable_caption_and_a_bare_includegraphics_a_line_with_its_name(look):
    f = look["figures"]
    assert f["one"] == ["=latex-figure-line",
                        'span.latex-figure-part[span.latex-graphic[span.latex-graphic-name"figs/plot.png"],span.latex-caption-word"Figure 1: "]',   # 22.12 M
                        "hidden", "plot=latex-i", "hidden", "hidden"]       # the caption's words in between, editable
    assert f["two"][1] == ('span.latex-figure-part[span.latex-graphic[span.latex-graphic-name"a.png"],'
                           'span.latex-graphic[span.latex-graphic-name"b.pdf"],span.latex-caption-word"Figure 1: "]')
    assert f["sub"] == ['div.latex-chip.latex-chip-unit.latex-chip-block"Figure: Sub"']   # subfigures: a chip with the caption's words (22.12 M: the article's word, no number)
    assert f["none"] == ['div.latex-figure-preview[div.latex-graphic[span.latex-graphic-name"a.png"]]']   # no caption: one block
    g = look["graphic"]
    assert g["away"] == [['span.latex-graphic[span.latex-graphic-name"figs/a.png"]', False]]
    assert g["touched"] == [["\\includegraphics[width=2cm]{figs/a.png}", None]]        # touched, the same: never as written


# -- font and size groups --------------------------------------------------------------------------------------------------


@needs_node
def test_font_and_size_groups_are_drawn_in_their_face_and_size_with_the_switch_hidden(look):
    g = look["groups"]
    assert g["marked"] == [["宋", "latex-f-song"], ["黑", "latex-f-hei"], ["楷", "latex-f-kai"], ["仿", "latex-f-song"], ["s", "latex-s-small"],
                           ["l", "latex-s-large"], ["L", "latex-s-larger"], ["sf", "latex-sf"], ["tt", "latex-tt"], ["rm", "latex-rm"]]
    for opening in ("{\\songti ", "{\\heiti ", "{\\kaishu ", "{\\fangsong ", "{\\small ", "{\\large ", "{\\Large "):
        assert opening in g["hidden"], opening
    # an argument's switch is hidden as a switch; an empty group and one whose brace closes inside a formula: hidden too
    assert g["written"] == [] and "\\small " in g["hidden"] and "{\\heiti" in g["hidden"]
    assert [x[1] for x in g["read"]] == ["{\\heiti 不在正文}", "{\\songti 宋}", "{\\heiti 黑}", "{\\kaishu 楷}", "{\\fangsong 仿}", "{\\small s}", "{\\large l}",
                                      "{\\Large L}", "{\\heiti}", "{\\large $a}"]   # \\textbf{\\small arg} is the command's argument
    assert g["preamble"] == [[0, 'div.latex-preamble-block[button.latex-preamble"导言区：文档类 ctexart，1 个宏包",div.latex-doc-mark"正文开始"]']]


@needs_node
def test_a_group_the_selection_touches_is_drawn_the_same(look):
    assert look["groups"]["touched"] == [["{\\kaishu ", None], ["楷", "latex-f-kai"]]   # B1: hidden and in its face still


# -- the word count --------------------------------------------------------------------------------------------------------


@needs_node
def test_the_word_count_takes_cjk_characters_one_each_and_latin_words_and_leaves_markup_out(look):
    w = look["words"]
    # Two words (2) + 中文三字 (4) + and three words here (4) + bold (1) + 3.14 (1) + a, b (2); not the preamble, the
    # comment, the formula, keys, labels, the image path, the column spec, nor what follows \end{document}
    assert w["body"] == 14
    assert w["chapter"] == 5                                            # no preamble: the whole file is the body
    assert w["paper"] == len("Write the abstract here. Introduction Start writing here.".split())
    assert look["fold"] == ["导言区：文档类 article，3 个宏包", "导言区", "导言区"]   # B1: never opened here


# -- drawn again only where needed, and in time ----------------------------------------------------------------------------


@needs_node
def test_the_page_blocks_drawn_again_only_where_needed_equal_those_drawn_whole_after_every_edit(look):
    assert look["incremental"]["mismatch"] is None, look["incremental"]["mismatch"]
    assert look["incremental"]["steps"] == 3000


@needs_node
def test_typing_stays_under_16_ms_a_key_on_a_3000_line_file_with_formulas_tables_figures_and_groups():
    got = _node(_TIMING)
    print("the page's look, per key:", json.dumps(got))
    assert got["lines"] >= 2998 and got["formulas"] >= 300 and got["tables"] >= 30 and got["groups"] >= 300
    assert got["running"]["median"] < 16 and got["running"]["p95"] < 16
    assert got["runningDrawn"] > 0
    assert got["inCell"]["median"] < 16 and got["inCell"]["p95"] < 16
    assert got["wordsMs"] < 50                                          # counted a moment after typing, never on a key


# -- the page: the status line and the columns -----------------------------------------------------------------------------

_PAGE = r"""
FakeView.prototype.dispatch = function (spec) {
  const tr = this.state.update(spec); this.state = tr.state;
  for (const listen of tr.state.facet(real.EditorView.updateListener)) listen({docChanged: tr.docChanged, selectionSet: !!spec.selection, changes: tr.changes, view: this, state: tr.state});
};
const out = {};
evidenceAnswer = () => ({revision: 'r1', links: []});
const TEXT = '\\documentclass{article}\n\\begin{document}\n\\section{Intro}\n中文 and words.\n\\end{document}\n';
const openFile = async (path, extra) => { L.open(path); await tick(); gets.shift().res(Object.assign({path, text: TEXT, revision: 'r1', line_ending: 'lf', read_only: false, links: [], mains: []}, extra || {})); await tick(); await tick(); await tick(); };
await openFile('a.tex');
const V = viewsMade[viewsMade.length - 1];
const said = () => [byClass('latex-words').textContent, byClass('latex-save-state').textContent];
out.opened = said();
const main = byClass('latex-main'), body = byClass('latex-body'), side = byClass('latex-side');
out.columns = {body: body.children.map((c) => c.className), main: main.children.map((c) => c.className.split(' ')[0]),
  side: side.children.map((c) => c.className || c.textContent), hint: side.children[1].children.map((c) => c.textContent)};   // under the panel, in its own box (review)
V.dispatch({changes: {from: TEXT.indexOf('words'), insert: 'more '}});
out.typed = said();
timers[timers.length - 1]();                                            // a moment after typing stops
out.counted = said();
button('保存').fire('click'); await tick();
out.saving = said();
posts.shift().res({revision: 'r2', changed: true, previous: 'r1'}); await tick(); await tick();
out.saved = said();
button('显示代码').fire('click');
out.source = {page: byClass('latex-page').className, likeWord: V.state.field(L._pure.wordField(CM).field, false) !== undefined};
button('显示代码').fire('click');
out.word = byClass('latex-page').className;
await openFile('b.tex', {read_only: true, read_only_reason: 'generated', read_only_message: 'x'});
out.readOnly = said();
console.log(JSON.stringify(out));
"""


def _page() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    script = prelude + "(async () => {\n" + _PAGE + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });"
    done = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    return out


@needs_node
def test_the_status_line_counts_the_body_and_follows_the_save():
    p = _page()
    assert p["opened"] == ["5 字", "已保存"]                              # Intro, 中, 文, and, words
    assert p["typed"] == ["5 字", "有未保存的改动"]                        # the count waits until typing stops
    assert p["counted"] == ["6 字", "有未保存的改动"]
    assert p["saving"] == ["6 字", "正在保存"]
    assert p["saved"] == ["6 字", "已保存"]
    assert p["readOnly"] == ["5 字", ""]                                # a file that can only be viewed is never "saved"


@needs_node
def test_the_header_and_the_status_line_sit_in_the_texts_column_and_the_right_column_beside_it_from_the_top():
    p = _page()
    c = p["columns"]
    assert c["body"] == ["latex-main", "latex-side"]
    assert c["main"] == ["latex-head", "latex-export", "node-sub", "node-sub", "latex-notice", "latex-editor", "latex-statusbar"]
    assert c["side"][0] == "latex-format"                              # 「显示代码」 heads it (B1)
    assert c["hint"] == ["选中一段文字，可以关联依据文件。"]               # under the panel
    assert p["source"] == {"page": "latex-page is-source", "likeWord": False}   # 显示代码: the full width, nothing drawn
    assert p["word"] == "latex-page"


# -- words, glyphs and the style sheet -------------------------------------------------------------------------------------

NEW_WORDS = {
    "正文开始": "Start of the text", "正文结束": "End of the text", "（没有设置标题）": "(No title set)",
    "日期：导出当天": "Date: the day of the export", "表：": "Table: ", "图：": "Figure: ", "正在保存": "Saving",
    "有未保存的改动": "Unsaved changes",
}


def test_every_new_word_goes_through_t_with_its_english():
    js = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    i18n = (WEBAPP / "i18n.js").read_text(encoding="utf-8")
    for zh, en in NEW_WORDS.items():
        assert f't("{zh}"' in js, zh
        assert f'"{zh}": "{en}",' in i18n, zh
    assert '"{n} 字": {one: "{n} word", other: "{n} words"},' in i18n
    assert '"导言区：文档类 {cls}，{n} 个宏包": {one: "Preamble: class {cls}, {n} package", other: "Preamble: class {cls}, {n} packages"},' in i18n
    assert '"摘要": "Abstract",' in i18n


def _section(js: str) -> str:
    return js[js.index("// -- the page's look (22.12 B)"):js.index("// The preamble is folded from the file's start")] + js[js.index("// -- the edit view (22.1;"):js.index("// The innermost place `pos` is in:")]


def test_the_pages_blocks_use_words_never_a_glyph_or_an_arrow():
    js = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    code = _section(js) + js[js.index("function drawWords(me)"):js.index("// 409: the file changed elsewhere.")]
    for glyph in ("->", "→", "▸", "›", "‹", "✓", "»", "«", "·", "•", "▾", "◂"):
        assert glyph not in code.replace("=>", ""), glyph
    css = (WEBAPP / "workspace.css").read_text(encoding="utf-8")
    block = css[css.index("/* 22.12 B: like Word, a page"):]
    assert "#" not in re.sub(r"/\*.*?\*/", "", block, flags=re.S)    # palette tokens only
    assert 'content: "";' in block and 'content: "\\' not in block    # the label line's hairline is a border, not a character


def test_the_style_sheet_draws_a_centred_page_column_of_a_pages_measure_and_the_full_width_source():
    assert _declarations(".latex-page")["--latex-measure"] == "46rem"
    assert _declarations(".latex-page.is-source")["--latex-measure"] == "none"
    word = _declarations(".latex-editor .cm-content.latex-word")
    assert word["max-width"] == "var(--latex-measure)" and word["margin"] == "0 auto"
    assert word["background"] == "var(--paper)" and word["font-size"] == "1rem"
    assert word["line-height"] == "calc(1.8 * var(--latex-stretch, 1))"
    assert word["padding"].startswith("2.4rem")
    assert _declarations(".latex-page:not(.is-source) .latex-editor")["background"] == "var(--paper-alt)"
    assert _declarations(".latex-word .latex-h")["font-family"] == "var(--font-serif)"
    assert _declarations(".latex-editor .cm-content.latex-word .cm-line.latex-hline")["padding-top"] == "1.1rem"
    assert _declarations(".latex-main > :not(.latex-editor)")["max-width"] == "var(--latex-measure)"
    assert _declarations(".latex-f-kai")["font-family"] == '"Kaiti SC", STKaiti, KaiTi, var(--font-serif)'
    assert _declarations(".latex-f-song")["font-family"] == "var(--font-serif)" and _declarations(".latex-f-hei")["font-family"] == "var(--font-sans)"
    assert _declarations(".latex-doc-mark")["font-family"] == "var(--font-sans)" and _declarations(".latex-doc-mark")["color"] == "var(--gray)"
    assert _declarations(".latex-title")["font-family"] == "var(--font-serif)"
    assert _declarations(".latex-statusbar")["color"] == "var(--gray)"


def test_the_right_column_stays_at_the_top_and_the_page_fits_the_shells_900_px_without_scrolling_sideways():
    side = _declarations(".latex-side")
    assert side["position"] == "sticky" and side["top"] == "0" and side["align-self"] == "flex-start" and side["overflow"] == "auto"
    assert _declarations(".latex-format-group + .latex-format-group")["border-top"] == "1px solid var(--line)"
    rem = 16
    sidebar = 236                                                       # app.html's sidebar (196 only below 720 px)
    view = 2 * 0.025 * 900                                              # #view-latex's clamp(1rem, 2.5vw, 2rem) each side
    gap = float(_declarations(".latex-body")["gap"].removesuffix("rem")) * rem
    # B2: the column's width is set, not shrunk by the flex share -- 14rem until the text has 28rem beside it, 19rem from
    # there (before B2 it shrank in proportion with the text and kept 18rem at 900 px, the text 312 px)
    assert side["flex"] == "none" and side["width"] == "clamp(14rem, 100% - 28rem, 19rem)" and side["min-width"] == "0"
    assert _declarations(".latex-main")["min-width"] == "0" and _declarations(".latex-editor")["min-width"] == "0"
    body = 900 - sidebar - view
    side_w = min(max(14 * rem, body - 28 * rem), 19 * rem)
    assert side_w == 14 * rem
    left = body - side_w - gap
    assert left > 23 * rem          # the text's column at 900 px: a browser measured 376 px (DESIGN 22.12 B2)
    wide = 1400 - sidebar - 2 * 32                                     # a wide window: the column its full 19rem
    assert min(max(14 * rem, wide - 28 * rem), 19 * rem) == 19 * rem


# -- B2 (the researcher, 2026-11-14: "我觉得很简陋…还需要优化UI"): the page polished -----------------------------------------------
# A paragraph's first line follows 版式's 首行缩进 where TeX starts a paragraph; the caret holders beside hidden pieces
# never break a line or leave an empty strut; the right column narrows first at 900 px. DESIGN 22.12 B2.

_PARAS = r"""
const make = (doc) => CM.EditorState.create({doc, extensions: [P.wordExtensions(CM)]});
// [the source line where a paragraph mark sits, its class]
const paras = (doc) => { const st = make(doc), got = []; st.field(F.field).deco.between(0, doc.length, (a, b, v) => {
  const c = v.spec.class; if (c && /latex-para/.test(c)) got.push([doc.slice(a).split('\n')[0], c]); }); return got; };
const MAIN = ['\\documentclass{article}', '\\usepackage{amsmath}', 'Not the body.', '', '\\begin{document}', 'First words of the body.', 'Same paragraph goes on.', '',
  'A new paragraph after a blank line.', '', '% a comment', '\\label{x}', 'After quiet lines.', '\\section{Intro}\\label{sec:i}', '% note', 'Right after a heading.', '',
  '\\noindent Not indented.', '', '\\begin{itemize}', '\\item an item', '', '\\item another', '\\end{itemize}', 'Text after a list goes on.', '', '\\begin{quote}', 'Quoted words.', '',
  'More quoted.', '\\end{quote}', '', '\\maketitle', 'After the title.', '\\[ x \\]', 'After a formula.', '\\subsection{Two}', '', 'A blank after a heading.', '',
  '\\begin{table}', '\\caption{C}', '\\begin{tabular}{l}', 'a \\\\', '\\end{tabular}', '\\end{table}', '', '$x$ starts this one.', '\\end{document}', '', 'After the end.'].join('\n') + '\n';
out.main = paras(MAIN);
out.chapter = paras('Chapter text.\n\nSecond.\n\\subsection{S}\nThird.\n');
out.quiet = ['% c', '\\label{a}', '\\noindent', '\\vspace{1em}', '\\centering \\label{b} % c', '', 'Words.', '\\maketitle', '\\section{S}', '\\foo', '\\label{a} x', '{\\large a}']
  .map((s) => [s, P.quietLine(s)]);

// drawn again only where a change reaches, equal to drawing whole -- among blank lines, comments, labels, headings, \noindent
let seed = 7;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
const bits = ['x', ' ', '\n', '\n\n', '% c\n', '\\label{l}\n', '\\noindent ', '\\section{S}\n', '\\maketitle\n', '\\begin{itemize}\n\\item i\n\\end{itemize}\n',
  '\\begin{quote}\nq\n\\end{quote}\n', '\\vspace{2pt}\n', '中', '{', '}', '\\[ y \\]\n'];
const sig = (s) => { const got = []; s.field(F.field).deco.between(0, s.doc.length, (a, b, v) => { const w = v.spec.widget;
  got.push([a, b, v.spec.class || (v.spec.attributes && v.spec.attributes.class) || '', w ? w.constructor.name + JSON.stringify(w) : '', !!v.spec.block].join('|')); }); return got.sort(); };
let cur = make(MAIN), mismatch = null, steps = 0, marked = 0;
for (let k = 0; k < 3000 && !mismatch; k++) {
  const len = cur.doc.length, r = rnd(10), a = rnd(len + 1);
  const spec = r < 6 ? {changes: {from: a, insert: bits[rnd(bits.length)]}} : {changes: {from: a, to: Math.min(len, a + 1 + rnd(14))}};
  cur = cur.update(spec).state;
  steps += 1;
  const x = sig(cur), y = sig(make(cur.doc.toString()));
  marked += x.filter((z) => z.includes('latex-para')).length;
  if (JSON.stringify(x) !== JSON.stringify(y)) mismatch = {step: k, doc: cur.doc.toString(), only: x.filter((z) => !y.includes(z)), missing: y.filter((z) => !x.includes(z))};
}
out.incremental = {steps, mismatch, marked};

// the indent the page gives the paragraphs, from the main file's preamble as 版式 reads it
const ind = (pre) => P.pageIndent(P.layoutOf(pre, []), pre);
out.indent = {
  article: ind('\\documentclass[12pt]{article}\n'),
  ctex: ind('\\documentclass{ctexart}\n'),
  none: ind('\\documentclass{article}\n\\setlength{\\parindent}{0pt}\n'),
  two: ind('\\documentclass{article}\n\\setlength{\\parindent}{2em}\n'),
  ctexNone: ind('\\documentclass{ctexart}\n\\setlength{\\parindent}{0pt}\n'),
  indentfirst: ind('\\documentclass{article}\n\\usepackage{indentfirst}\n'),
  parskip: ind('\\documentclass{article}\n\\usepackage{parskip}\n'),
  parskipKeeps: ind('\\documentclass{article}\n\\usepackage[skip=6pt,indent]{parskip}\n'),
  own: ind('\\documentclass{article}\n\\setlength{\\parindent}{3em}\n'),
  ownPt: ind('\\documentclass{article}\n\\setlength{\\parindent}{18pt}\n'),
  commented: ind('\\documentclass{article}\n% \\usepackage{indentfirst}\n'),
  noMain: P.pageIndent(null, null),
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def paras() -> dict:
    return _node(_PARAS)


@needs_node
def test_a_paragraphs_first_line_is_marked_where_tex_starts_a_paragraph(paras):
    """After \\begin{document}, a blank line (comments and labels between passed over), \\maketitle: latex-para. Right
    after a heading, a blank line between or not: latex-para-first. A paragraph's second line, \\noindent, what follows a
    list or a formula without a blank line, a list's and a quote's lines, the preamble and after \\end{document}: none."""
    # 22.12 M (F5): a blank line takes no room -- it is joined to the line of words after it, so the page's line, and its
    # mark, starts at the blank line
    assert paras["main"] == [
        ["First words of the body.", "latex-para"],
        ["", "latex-para"],                         # 'A new paragraph after a blank line.', from the blank line before it
        ["", "latex-para"],                         # the blank line the hidden comment and label join to 'After quiet lines.'
        ["% note", "latex-para-first"],             # the visual line of 'Right after a heading.' starts at the hidden note
        ["After the title.", "latex-para"],
        ["", "latex-para-first"],                   # 'A blank after a heading.': first after \subsection, across a blank line
        ["", "latex-para"],                         # '$x$ starts this one.': after a table and a blank line
    ]
    assert paras["chapter"] == [["Chapter text.", "latex-para"], ["", "latex-para"], ["Third.", "latex-para-first"]]


@needs_node
def test_only_lines_the_edit_view_hides_whole_are_passed_over(paras):
    assert dict(paras["quiet"]) == {"% c": True, "\\label{a}": True, "\\noindent": True, "\\vspace{1em}": True,
                                    "\\centering \\label{b} % c": True, "": False, "Words.": False, "\\maketitle": False,
                                    "\\section{S}": False, "\\foo": False, "\\label{a} x": False, "{\\large a}": False}


@needs_node
def test_paragraph_marks_drawn_again_only_where_a_change_reaches_equal_those_drawn_whole(paras):
    inc = paras["incremental"]
    assert inc["mismatch"] is None, inc["mismatch"]
    assert inc["steps"] == 3000 and inc["marked"] > 3000      # the marks were there to be compared, edit after edit


@needs_node
def test_the_indent_follows_版式_and_the_class_as_the_pdf_has_it(paras):
    i = paras["indent"]
    assert i["article"] == {"all": "1.5em", "first": "0"}     # article: 1.5em, none after a heading
    assert i["ctex"] == {"all": "2em", "first": "2em"}        # ctex: two characters, after a heading too
    assert i["none"] == {"all": "0", "first": "0"}            # 版式 首行缩进 无
    assert i["two"] == {"all": "2em", "first": "0"}           # 版式 首行缩进 2 字
    assert i["ctexNone"] == {"all": "0", "first": "0"}
    assert i["indentfirst"] == {"all": "1.5em", "first": "1.5em"}
    assert i["parskip"] == {"all": "0", "first": "0"}         # the parskip package without its indent option
    assert i["parskipKeeps"] == {"all": "1.5em", "first": "0"}
    assert i["own"] == {"all": "3em", "first": "0"}           # the person's own length, in em, read as it is
    assert i["ownPt"] == {"all": "1.5em", "first": "0"}       # one not in em: the class's
    assert i["commented"] == {"all": "1.5em", "first": "0"}   # a commented line does not count
    assert i["noMain"] == {"all": "1.5em", "first": "0"}


from test_v17_layout_settings import _PANEL as _LAYOUT_PANEL  # noqa: E402  (its stand-in page's helpers, reused)

_INDENT_PAGE = _LAYOUT_PANEL[:_LAYOUT_PANEL.index("const MAIN_PRE")] + r"""
// the page's lines follow 版式: the box's custom properties as 首行缩进 and 行距 are chosen
const MAIN = '\\documentclass[12pt]{article}\n\\usepackage{amsmath}\n\\begin{document}\n\\section{A}\nFirst.\n\nSecond.\n\\end{document}\n';
await openFile('paper/main.tex', MAIN, {mains: ['paper/main.tex'], format: {main: 'paper/main.tex', engine: 'pdflatex', folder: 'paper', preamble: null, local: []}});
const box = byClass('latex-editor'); box.style = {props: {}, setProperty(k, v) { this.props[k] = v; }};
tab('版式').fire('click');
const props = () => Object.assign({}, box.style.props);
out.opened = props();
choose('首行缩进', '0pt'); out.none = props(); out.noneText = doc().includes('\\setlength{\\parindent}{0pt}');
choose('首行缩进', '2em'); out.two = props();
choose('行距', '2'); out.double = props();
out.primaries = primaries();
const ZH = '\\documentclass{ctexart}\n\\begin{document}\n\\section{引言}\n正文。\n\\end{document}\n';
await openFile('zh/main.tex', ZH, {mains: ['zh/main.tex'], format: {main: 'zh/main.tex', engine: 'xelatex', folder: 'zh', preamble: null, local: []}});
const box2 = byClass('latex-editor'); box2.style = {props: {}, setProperty(k, v) { this.props[k] = v; }};
tab('版式').fire('click');
out.ctex = Object.assign({}, box2.style.props);
console.log(JSON.stringify(out));
"""


@needs_node
def test_the_pages_indent_and_line_height_follow_版式_on_the_page():
    if NODE is None:
        pytest.skip("node is not installed")
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    script = prelude + "(async () => {\n" + _INDENT_PAGE + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });"
    done = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    assert out["opened"] == {"--latex-stretch": "1", "--latex-indent": "1.5em", "--latex-indent-first": "0", "--latex-parskip": "0"}   # 22.12 M: 段间距 too
    assert out["noneText"] is True and out["none"]["--latex-indent"] == "0"
    assert out["two"]["--latex-indent"] == "2em" and out["two"]["--latex-indent-first"] == "0"
    assert out["double"]["--latex-stretch"] == "2" and out["double"]["--latex-indent"] == "2em"
    assert out["primaries"] == []                                       # 版式 has no primary (22.7)
    assert out["ctex"] == {"--latex-stretch": "1", "--latex-indent": "2em", "--latex-indent-first": "2em", "--latex-parskip": "0"}


def test_the_style_sheet_indents_a_paragraphs_first_line_and_keeps_the_lines_whole():
    word = ".latex-editor .cm-content.latex-word"
    assert _declarations(f"{word} .cm-line.latex-para")["text-indent"] == "var(--latex-indent, 1.5em)"
    assert _declarations(f"{word} .cm-line.latex-para-first")["text-indent"] == "var(--latex-indent-first, 0)"
    assert _declarations(f"{word} .cm-line.latex-para *")["text-indent"] == "0"       # never inherited by a block inside
    # the caret holders out of the flow: no break at them (naïve one word), no empty strut over a heading or a formula
    assert _declarations(f"{word} img.cm-widgetBuffer")["position"] == "absolute"
    assert _declarations(f"{word} .cm-line")["min-height"] == "calc(1.8em * var(--latex-stretch, 1))"
    for sel in (f"{word} .latex-env-head", f"{word} span.latex-abstract-head", f"{word} span.latex-math-display"):
        d = _declarations(sel)
        assert d["display"] == "inline-block" and d["width"] == "100%", sel
    # chips and a figure's file name are quiet words, never code: the sans, never the mono
    assert _declarations(".latex-chip")["font-family"] == "var(--font-sans)"
    assert _declarations(".latex-graphic-name")["font-family"] == "var(--font-sans)"
    assert _declarations(f"{word} .latex-chip-ref")["white-space"] == "nowrap"
    css = (WEBAPP / "workspace.css").read_text(encoding="utf-8")
    block = css[css.index("/* 22.12 B2"):]
    assert "#" not in re.sub(r"/\*.*?\*/", "", block, flags=re.S)       # palette tokens only
    assert "font-mono" not in block


def test_b2_adds_no_word_no_glyph_and_no_primary_button():
    js = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    code = js[js.index("// 首行缩进 on the page (22.12 B2)"):js.index("const IMAGE_UNSAFE")]
    code += js[js.index("// where a paragraph of running text starts"):js.index("// a figure's caption sits under its images")]
    code += js[js.index("// The page's first-line indent (22.12 B2)"):js.index("// Where the settings come from")]
    for glyph in ("->", "→", "▸", "›", "‹", "✓", "»", "«", "·", "•", "▾", "◂"):
        assert glyph not in code.replace("=>", ""), glyph
    assert not re.search(r"\bt\(", code)                               # nothing said: only lines and lengths
    assert js.count("btn-primary") == 5                                 # 保存, 插入, 创建 … as pinned before (22.7)

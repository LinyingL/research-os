"""DESIGN.md 22.1 and 22.12 B1 (task V17; the researcher, 2026-11-14: "我的想法是，编辑的时候是看不到latex代码的，右侧的选项打开才能
看到代码"): the edit view shows no LaTeX code; 「显示代码」, a switch at the top of the right column, shows the whole file as
code. Formulas and tables are typeset and changed only in the code. Under node on the vendored CodeMirror and KaTeX:
what the edit view puts on the page for a corpus of papers (the 新建文稿 templates filled with every tool of steps A, A2
and A3, and a hand-written paper with comments, unknown macros, tikz, verbatim, align, citations, footnotes, lists,
tables and figures); the cursor never inside what is hidden; typing, pasting, Return, ⇧Return, Backspace and a
seeded fuzz of 2,000 deletions and replacements, each leaving the source balanced and never losing a word outside the
range (30 of them compiled by latexmk with pdfLaTeX and XeLaTeX); undo byte for byte; and on the stand-in page of
test_v17_latex_editor: the switch, files opened as code, the right column's hints, the footnote, 文档信息 and the
16 ms a key. Every test names its scenario."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from researchos import latex_edit
from test_v17_latex_editor import _DRIVER, NODE, WEBAPP
from test_v17_latex_word import _node

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
TEXBIN = Path("/Library/TeX/texbin")
LATEXMK = shutil.which("latexmk") or (str(TEXBIN / "latexmk") if (TEXBIN / "latexmk").exists() else None)

ZH = latex_edit.TEMPLATES["zh_paper"].replace("<TITLE>", "汇率与信息")
EN = latex_edit.TEMPLATES["en_paper"].replace("<TITLE>", "Exchange Rates and News")

# A hand-written paper: what a researcher's draft holds besides the tools' output.
HAND = r"""\documentclass[a4paper,12pt]{article}
\usepackage[T1]{fontenc}
\usepackage{amsmath,amssymb,graphicx,booktabs,tikz}
\usepackage{hyperref}
\newcommand{\R}{\mathbb{R}}
\newcommand{\foo}[1]{#1}
\newcommand{\mystery}{M}
\newtheorem{theorem}{Theorem}
\title{A Study of \textbf{Things}}
\author{Ann Lee}
\date{\today}
\begin{document}
\maketitle
\begin{abstract}
We study things with care. % not printed
\end{abstract}
\tableofcontents
\section{Introduction}\label{sec:intro}
% a whole comment line
Text with \textbf{bold}, \textit{italic}, \emph{emphasis} and \underline{lines}.
A second line of the same paragraph, see \cite{smith2020, lee2019} and Section~\ref{sec:method}.
Prices rose 50\% while the na\"ive model, the caf\'e and \LaTeX{} stayed. % trailing comment
We use \foo{plain words here} and \mystery{} and $\R$ as defined.\footnote{A plain footnote.}

% only comments
% in this paragraph

\section[Short]{Method}\label{sec:method}
\begin{itemize}
  \item first point with $x^2$
  \item second {\large point}
  \begin{enumerate}
    \item nested one
    \item nested two
  \end{enumerate}
\end{itemize}
\begin{description}
  \item[Term] its meaning
\end{description}
\begin{theorem}[Main]
Every thing holds.
\end{theorem}
\begin{quote}
A quoted passage.
\end{quote}
\begin{center}
Centred words\\
and a second line
\end{center}
\begin{align}
a &= b \label{eq:a}\\
c &= d
\end{align}
\begin{table}[htbp]
\centering
\caption{Main results}
\label{tab:main}
\begin{tabular}{lcr}
\toprule
A & B & C \\
\midrule
1 & 2 & 3 \\
\bottomrule
\end{tabular}
\end{table}
\begin{figure}[htbp]
\centering
\includegraphics[width=0.3\linewidth]{figs/plot.png}
\caption{A \textit{plot} of things}
\label{fig:plot}
\end{figure}
\begin{tikzpicture}
\draw (0,0) -- (1,1);
\end{tikzpicture}
\begin{verbatim}
raw code here
\end{verbatim}
Inline \verb|verbatim| and a \url{https://example.org} link, \href{https://x.org}{the site}.
See Table~\ref{tab:main} and Figure~\ref{fig:plot}.\\
After a break. \newpage
\noindent Last words.
\end{document}
"""

# The page the corpus is built in, shared by the tests below.
_CORPUS = r"""
window.katex = kctx.katex;
const said = [];
const make = (doc, anchor, more) => CM.EditorState.create({doc, selection: {anchor: anchor || 0}, extensions: [P.wordExtensions(CM, {say: (s) => said.push(s)})].concat(more || [])});
// the edit view's replaces in order (a range set yields overlapping ranges layer by layer)
const atomsIn = (st, from, to) => { const got = []; st.field(F.field).deco.between(from, to, (a, b, d) => { if (d.spec.atom) got.push({a, b, d}); }); return got.sort((x, y) => x.a - y.a || x.b - y.b); };
const textOf = (e) => typeof e === 'string' ? e : (e.innerHTML ? e.innerHTML.replace(/<[^>]*>/g, '').replace(/&[a-z#0-9]+;/g, ' ') : '') + (e.textContent || '') + (e.children || []).map(textOf).join('');
const hasClass = (e, c) => e && typeof e === 'object' && (String(e.className || '').split(' ').includes(c) || (e.children || []).some((k) => hasClass(k, c)));
// what the edit view puts on the page: the text no replace covers, and each widget's own text (verbatim kept apart)
const view = (st) => { const doc = st.doc.toString(); let text = '', verbatim = '', at = 0;
  atomsIn(st, 0, doc.length).forEach(({a, b, d}) => { if (b <= at) return; text += doc.slice(at, a); const w = d.spec.widget;
    if (w) { const el = w.toDOM({dispatch() {}, state: st}); if (hasClass(el, 'latex-verbatim')) verbatim += textOf(el); else text += textOf(el); } at = b; });
  return {text: text + doc.slice(at), verbatim}; };
// CodeMirror's own skipAtomicRanges (view/cursor.ts), over the edit view's atomic ranges: where an arrow key stops
const skip = (set, pos, bias) => { for (;;) { let moved = 0; set.between(pos - 1, pos + 1, (from, to) => { if (pos > from && pos < to) { const side = moved || bias || (pos - from < to - pos ? -1 : 1); pos = side < 0 ? from : to; moved = side; } }); if (!moved) return pos; } };
const stopsOf = (st) => { const v = st.field(F.field), out = [0]; for (let p = 0, n = 0; p < st.doc.length && n < 1e6; n++) { p = skip(v.atoms, p + 1, 1); out.push(p); } return out; };
const hidden = (st, pos) => { let inside = false; st.field(F.field).deco.between(pos, pos, (a, b, d) => { if (d.spec.atom && a < pos && pos < b) inside = true; }); return inside; };
const letters = (s) => s.replace(/[^A-Za-z0-9\u4e00-\u9fff]/g, '');
// the source's own letters the edit view shows in [from, to) (widgets' words left out: numbers move when a figure goes)
const shownLetters = (st, from, to) => { const d = st.doc.toString(); let out = '', at = from;
  atomsIn(st, from, to).forEach(({a, b}) => { if (b <= at) return; if (a > at) out += d.slice(at, Math.min(a, to)); at = Math.max(at, b); });
  if (at < to) out += d.slice(at, to); return letters(out); };
const sure = (text) => P.unsureWhy(text, P.readLatex(text));
const rawShown = (st) => { const doc = st.doc.toString(); let text = '', at = 0;
  atomsIn(st, 0, doc.length).forEach(({a, b}) => { if (b <= at) return; text += doc.slice(at, a); at = b; }); return text + doc.slice(at); };

// -- the corpus: the 新建文稿 templates filled with every tool of steps A, A2 and A3 --
const fill = (tpl, zh) => {
  let st = make(tpl);
  const fails = [];
  const doc = () => st.doc.toString();
  const sel = (a, b) => { st = st.update({selection: {anchor: a, head: b == null ? a : b}}).state; };
  const word = (w, k) => { const at = doc().lastIndexOf(w); sel(at + (k || 0), at + w.length); };
  const run = (name, f) => { const r = f(st); if (!r || r.refused) { fails.push(name + ': ' + (r ? r.refused : 'nothing')); return; } st = st.update({changes: r.changes, selection: r.selection, userEvent: 'input.format'}).state; };
  const para = (text) => { const end = doc().lastIndexOf('\\end{document}'); st = st.update({changes: {from: end, insert: text + '\n\n'}, selection: {anchor: end + text.length}, userEvent: 'input.format'}).state; };
  para(zh ? '甲乙 丙丁 戊己 庚辛 壬癸。' : 'Alpha beta gamma delta epsilon.');
  word(zh ? '丙丁' : 'beta'); run('bold', (s) => P.styleSpec(s, 'textbf'));
  word(zh ? '戊己' : 'gamma'); run('italic', (s) => P.styleSpec(s, 'textit'));
  word(zh ? '庚辛' : 'delta'); run('underline', (s) => P.styleSpec(s, 'underline', {engine: zh ? 'xelatex' : 'pdflatex', cjk: zh, isMain: true}));
  word(zh ? '壬癸' : 'epsilon'); run('font', (s) => P.fontSpec(s, zh ? 'heiti' : 'textsf'));
  word(zh ? '甲乙' : 'Alpha'); run('size', (s) => P.sizeSpec(s, 'large'));
  para(zh ? '方法与数据' : 'Methods and data'); word(zh ? '方法与数据' : 'Methods and data', 2); sel(st.selection.main.head); run('section', (s) => P.headingSpec(s, 'section', false));
  para(zh ? '样本说明' : 'The sample'); word(zh ? '样本说明' : 'The sample', 2); sel(st.selection.main.head); run('subsection', (s) => P.headingSpec(s, 'subsection', false));
  para(zh ? '居中的一段。' : 'A centred paragraph.'); word(zh ? '居中的一段' : 'centred', 1); sel(st.selection.main.head); run('center', (s) => P.alignSpec(s, 'center'));
  para(zh ? '靠右的一段。' : 'A right paragraph.'); word(zh ? '靠右的一段' : 'right', 1); sel(st.selection.main.head); run('right', (s) => P.alignSpec(s, 'flushright'));
  para(zh ? '引用的一段。' : 'A quoted paragraph.'); word(zh ? '引用的一段' : 'quoted', 1); sel(st.selection.main.head); run('quote', (s) => P.quoteSpec(s));
  para(zh ? '公式 x+y 在此。' : 'The formula x+y here.'); word('x+y'); run('inline math', (s) => P.mathSpec(s, false));
  para('a=b'); word('a=b'); run('display math', (s) => P.mathSpec(s, true));
  para(zh ? '第一点\n第二点' : 'First point\nSecond point'); { const a = doc().lastIndexOf(zh ? '第一点' : 'First point'), b = doc().lastIndexOf(zh ? '第二点' : 'Second point') + 4; sel(a, b); } run('itemize', (s) => P.listSpec(s, 'itemize'));
  para(zh ? '其一\n其二' : 'One item\nTwo items'); { const a = doc().lastIndexOf(zh ? '其一' : 'One item'), b = doc().lastIndexOf(zh ? '其二' : 'Two items') + 3; sel(a, b); } run('enumerate', (s) => P.listSpec(s, 'enumerate'));
  para(zh ? '有脚注的话。' : 'A sentence with a note.'); word(zh ? '有脚注的话' : 'note'); sel(st.selection.main.head); run('footnote', (s) => P.footnoteSpec(s));
  st = st.update({changes: {from: st.selection.main.head, insert: zh ? '脚注的文字' : 'The note text'}, userEvent: 'input.format'}).state;
  para(zh ? '引用文献在此' : 'As shown'); word(zh ? '引用文献在此' : 'As shown'); sel(st.selection.main.head); run('cite', (s) => P.citeSpec(s, ['smith2020', 'lee2019']));
  para(zh ? '图在下面。' : 'A figure follows.'); word(zh ? '图在下面' : 'follows'); sel(st.selection.main.head);
  run('figure', (s) => P.figureSpec(s, {path: 'figs/plot.png', file: 'figs/plot.png', caption: zh ? '汇率走势' : 'The rate', graphicx: true, isMain: true}));
  para(zh ? '表在下面。' : 'A table follows.'); word(zh ? '表在下面' : 'A table follows'); sel(st.selection.main.head);
  run('table', (s) => P.tableSpec(s, {rows: 3, cols: 3, header: true, caption: zh ? '主要结果' : 'Main results', booktabs: true, isMain: true}));
  { const at = doc().indexOf('\\\\', doc().indexOf('\\begin{tabular}')); st = st.update({changes: {from: at - 4, to: at, insert: 'A & B & C '}, userEvent: 'input.format'}).state; }
  para(zh ? '见图' : 'See figure'); word(zh ? '见图' : 'See figure'); sel(st.selection.main.head); run('ref', (s) => P.refSpec(s, 'fig:plot', false));
  para(zh ? '网址' : 'The site'); word(zh ? '网址' : 'The site'); sel(st.selection.main.head); run('url', (s) => P.urlSpec(s, 'https://example.org', {url: false, isMain: true}));
  para(zh ? '分页之前' : 'Before the break'); word(zh ? '分页之前' : 'Before the break'); sel(st.selection.main.head); run('page break', (s) => P.pageBreakSpec(s));
  sel(doc().indexOf('\\maketitle') + 10); run('toc', (s) => P.tocSpec(s));
  // 版式 (A2, A3)
  const layout = [['margin', {preset: '2'}], ['columns', 'twocolumn'], ['pagestyle', 'plain'], ['hyperref', 'on'], ['size', zh ? 'zihao=-4' : '11pt'],
    ['spacing', '1.5'], ['indent', '2em'], ['parskip', 'half'], ['secnum', zh ? 'zh' : ''], ['capname', zh ? 'en' : 'zh'], ['capnum', 'within']];
  for (const [k, v] of layout) { if (!zh && (k === 'capname' || k === 'secnum')) continue; run('layout ' + k, (s) => P.layoutSpec(s, k, v, {local: []})); }
  return {doc: doc(), fails};
};
const ZHF = fill(__ZH__, true), ENF = fill(__EN__, false);
const CORPUS = {zh: ZHF.doc, en: ENF.doc, hand: __HAND__};
out.fails = {zh: ZHF.fails, en: ENF.fails};
out.corpus = CORPUS;
"""

# -- what the edit view shows ------------------------------------------------------------------------------------------

_SHOWN = r"""
out.shown = {};
for (const [name, doc] of Object.entries(CORPUS)) {
  const v = view(make(doc));
  out.shown[name] = {text: v.text, verbatim: v.verbatim, raw: rawShown(make(doc)), unsure: sure(doc)};
}
// each kind of construct, by itself
const E = (body, pre) => '\\documentclass{article}\n' + (pre || '') + '\\begin{document}\n' + body + '\n\\end{document}\n';
const parts = (doc) => { const st = make(doc); return atomsIn(st, 0, doc.length).map(({a, b, d}) => { const w = d.spec.widget;
  return {text: doc.slice(a, b), kind: d.spec.kind || null, shown: w ? textOf(w.toDOM({dispatch() {}})) : null}; }); };
out.parts = {
  unknown: parts(E('A \\foo{plain words} and \\bar{\\textbf{x}} and \\baz here.')),
  tikz: parts(E('\\begin{tikzpicture}\n\\draw (0,0) -- (1,1);\n\\end{tikzpicture}')),
  defined: parts(E('\\newcommand{\\R}{\\mathbb{R}}\nText \\def\\x#1{#1} more.')),
  verb: parts(E('Code \\verb|a_b| and\n\\begin{verbatim}\nx_1 = {y}\n\\end{verbatim}')),
  comment: parts(E('Words % gone\n% whole line\nmore.')),
  chips: parts(E('\\section{S}\\label{sec:s}\nSee \\ref{sec:s}, \\eqref{eq:x}, \\pageref{sec:s} and \\cite{k1,k2}.\\footnote{note} \\url{https://a.b} \\href{https://a.b}{site}')),
  lines: parts(E('\\tableofcontents\nText.\n\\clearpage\nMore.\n\\appendix\n\\section{A}', '\\usepackage{hyperref}\n')),
  escapes: parts(E('50\\% \\& \\$ \\# \\_ \\{ \\} a~b \\textasciitilde{} na\\"ive caf\\\'e \\LaTeX{} x\\\\y')),
  formula: parts(E('Inline $a+b$ and\n\\begin{align}\na &= b\n\\end{align}\nand a bad $\\frac{a}{$ one.')),
};
// a figure's caption: its words visible and editable, the rest one unit each side; a table: one unit
const FIG = E('\\begin{figure}\n\\centering\n\\includegraphics{a.png}\n\\caption{A \\textit{plot}}\n\\label{fig:a}\n\\end{figure}\nSee \\ref{fig:a}.');
out.figure = parts(FIG);
const TAB = E('\\begin{table}\n\\caption{Results}\n\\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}\n\\end{table}\nAfter.');
out.table = parts(TAB);
// numbers and citations: what the PDF will print where it is known, else words
const st0 = make(CORPUS.hand);
F.hooks.cites = new Map([['smith2020', 'Smith 2020'], ['lee2019', 'Lee 等 2019']]);
const st1 = st0.update({effects: F.refresh.of(true)}).state;
F.hooks.cites = null;
const chipsOf = (st, kind) => { const got = []; st.field(F.field).deco.between(0, st.doc.length, (a, b, d) => { if (d.spec.kind === kind) got.push(textOf(d.spec.widget.toDOM({dispatch() {}}))); }); return got; };
out.cites = {before: chipsOf(st0, 'cite'), after: chipsOf(st1, 'cite')};
out.refs = chipsOf(st0, 'ref');
out.notes = chipsOf(st0, 'footnote');
out.chapterRefs = chipsOf(make('\\section{A}\\label{a}\nSee \\ref{a}.\\footnote{n}\n'), 'ref').concat(chipsOf(make('\\section{A}\\label{a}\nSee \\ref{a}.\\footnote{n}\n'), 'footnote'));
// nothing drawn depends on where the cursor is
out.cursorFree = CORPUS.hand.length > 0 && [0, 400, 900, 1500, 2000].every((p) => JSON.stringify(view(make(CORPUS.hand, p))) === JSON.stringify(view(make(CORPUS.hand, 0))));
console.log(JSON.stringify(out));
"""


def _corpus(body: str) -> dict:
    return _node(_CORPUS.replace("__ZH__", json.dumps(ZH)).replace("__EN__", json.dumps(EN)).replace("__HAND__", json.dumps(HAND)) + body)


@pytest.fixture(scope="module")
def shown() -> dict:
    return _corpus(_SHOWN)


MARKUP = set("\\{}$&#^_~")
NAMES = ("documentclass", "usepackage", "begin{", "end{", "textbf", "textit", "includegraphics", "maketitle", "tableofcontents",
         "newcommand", "tikzpicture", "footnote", "label", "caption", "toprule", "midrule", "clearpage", "\\item", "geometry", "setstretch")


@needs_node
def test_the_corpus_is_every_tool_of_steps_a_a2_and_a3_applied_to_both_templates(shown):
    assert shown["fails"] == {"zh": [], "en": []}
    zh, en = shown["corpus"]["zh"], shown["corpus"]["en"]
    for doc in (zh, en):
        for written in ("\\textbf{", "\\textit{", "\\CJKunderline{" if doc is zh else "\\uline{", "{\\large ", "\\section{", "\\subsection{", "\\begin{center}", "\\begin{flushright}",
                        "\\begin{quote}", "$x+y$", "\\begin{equation}", "\\begin{itemize}", "\\begin{enumerate}", "\\footnote{", "\\cite{smith2020,lee2019}",
                        "\\begin{figure}", "\\begin{table}", "\\ref{fig:plot}", "\\url{https://example.org}", "\\clearpage", "\\tableofcontents",
                        "geometry", "twocolumn", "\\pagestyle{plain}", "hyperref", "\\setlength{\\parindent}{2em}", "parskip", "\\counterwithin"):
            assert written in doc, written
    assert "{\\heiti " in zh and "zihao=-4" in zh and "\\chinese{section}" in zh and "\\linespread{" in zh
    assert "\\textsf{" in en and "11pt" in en and "\\setstretch{1.5}" in en and "\\renewcommand{\\thefigure}{\\arabic{section}-\\arabic{figure}}" in en


@needs_node
@pytest.mark.parametrize("paper", ["zh", "en", "hand"])
def test_the_edit_view_shows_no_latex_markup_in_any_paper_of_the_corpus(shown, paper):
    s = shown["shown"][paper]
    assert s["unsure"] is None
    assert sorted(set(s["raw"]) & (MARKUP | {"%"})) == []              # the source itself shows no markup character
    text = s["text"]
    assert sorted(set(text) & MARKUP) == []
    if paper != "hand":
        assert "%" not in text
    else:
        assert text.count("%") == 1 and "50%" in text                  # the researcher's own \% only
    for name in NAMES:
        assert name not in text, name
    assert "Things" in text or "汇率与信息" in text or "Exchange" in text   # the title block, read from the preamble


@needs_node
def test_verbatim_text_is_shown_as_it_is_in_its_own_quiet_block_never_as_code(shown):
    assert shown["shown"]["hand"]["verbatim"] == "raw code hereverbatim"
    assert "raw code here" not in shown["shown"]["hand"]["text"]


@needs_node
def test_the_researchers_words_are_all_shown_somewhere_in_the_edit_view(shown):
    text = shown["shown"]["hand"]["text"]
    for words in ("We study things with care.", "plain words here", "first point with", "nested two", "Term", "its meaning", "Main",
                  "Every thing holds.", "A quoted passage.", "Centred words", "Main results", "plot", "of things", "the site",
                  "https://example.org", "After a break.", "Last words.", "naïve", "café", "LaTeX", "Short", "Method"):
        assert words in text, words
    zh = shown["shown"]["zh"]["text"]
    for words in ("丙丁", "方法与数据", "居中的一段", "引用的一段", "第一点", "其二", "有脚注的话", "汇率走势", "主要结果", "分页之前"):
        assert words in zh, words


@needs_node
def test_each_construct_is_shown_as_words_never_its_command(shown):
    p = shown["parts"]
    by = lambda name, text: [x for x in p[name] if x["text"] == text]
    assert by("unknown", "\\foo{plain words}")[0]["shown"] == "plain words"          # its plain words, read-only
    assert by("unknown", "\\bar{\\textbf{x}}")[0]["shown"] == "LaTeX 内容"
    assert by("unknown", "\\baz")[0]["shown"] == "LaTeX 内容"
    assert [x["shown"] for x in p["tikz"] if x["kind"] == "unit"] == ["LaTeX 内容"]
    assert [x["shown"] for x in p["defined"] if x["kind"] == "unit"] == ["LaTeX 内容", "LaTeX 内容"]
    assert [x["shown"] for x in p["verb"] if x["kind"] in ("verb", "verbatim")] == ["a_b", "x_1 = {y}"]
    assert [(x["text"], x["shown"]) for x in p["comment"] if x["kind"] == "comment"] == [("% gone", None), ("% whole line\n", None)]   # its line break too
    chips = {x["text"]: x["shown"] for x in p["chips"] if x["shown"] is not None}
    assert chips["\\ref{sec:s}"] == "第 1 节" and chips["\\eqref{eq:x}"] == "引用：eq:x" and chips["\\pageref{sec:s}"] == "引用：sec:s"
    assert chips["\\cite{k1,k2}"] == "k1；k2" and chips["\\footnote{note}"] == "1"
    assert chips["\\url{https://a.b}"] == "https://a.b" and chips["\\href{https://a.b}{site}"] == "site"
    assert [x for x in p["chips"] if x["text"] == "\\label{sec:s}"][0]["shown"] is None   # hidden
    lines = [x["shown"] for x in p["lines"] if x["kind"] == "line"]
    assert lines == ["目录（导出时生成）", "分页", "附录"]
    esc = [(x["text"], x["shown"]) for x in p["escapes"] if x["kind"] in ("text", "break")]
    assert esc == [("\\%", "%"), ("\\&", "&"), ("\\$", "$"), ("\\#", "#"), ("\\_", "_"), ("\\{", "{"), ("\\}", "}"), ("~", "\u00a0"),
                   ("\\textasciitilde{}", "~"), ("\\\"i", "ï"), ("\\'e", "é"), ("\\LaTeX{}", "LaTeX"), ("\\\\", "")]   # \\ mid-line: a line break
    f = [x for x in p["formula"] if x["kind"] == "math"]
    assert len(f) == 3 and f[2]["shown"] == "公式"                       # one KaTeX cannot typeset: a quiet word
    assert all("\\" not in (x["shown"] or "") for x in f)


@needs_node
def test_a_figure_shows_its_images_and_numbered_caption_and_only_the_caption_is_editable(shown):
    f = shown["figure"]
    figure = [x for x in f if x["kind"] == "figure"]
    assert len(figure) == 2 and figure[0]["shown"].endswith("图 1：") and "a.png" in figure[0]["shown"]
    assert figure[0]["text"].startswith("\\begin{figure}") and figure[0]["text"].endswith("\\caption{")
    assert figure[1]["text"].startswith("}\n\\label{fig:a}") and figure[1]["text"].endswith("\\end{figure}")
    assert [x["shown"] for x in f if x["kind"] == "ref"] == ["图 1"]
    t = [x for x in shown["table"] if x["kind"] == "table"]
    assert len(t) == 1 and t[0]["text"].startswith("\\begin{table}") and t[0]["text"].endswith("\\end{table}")   # one unit
    assert t[0]["shown"].startswith("表 1：Results")


@needs_node
def test_citations_say_author_and_year_once_the_bib_is_read_and_refs_the_number_where_it_is_known(shown):
    assert shown["cites"] == {"before": ["smith2020；lee2019"], "after": ["Smith 2020；Lee 等 2019"]}
    assert shown["refs"] == ["第 2 节", "表 1", "图 1"]
    assert shown["notes"] == ["1"]
    assert shown["chapterRefs"] == ["引用：a", "注"]                    # a chapter file: its numbers are the main file's


@needs_node
def test_nothing_the_edit_view_draws_depends_on_where_the_cursor_is(shown):
    assert shown["cursorFree"] is True


# -- the cursor, typing, Return and Backspace ---------------------------------------------------------------------------

_EDIT = r"""
const T = (st, at, text, ev) => st.update({changes: {from: at, insert: text}, selection: {anchor: at + text.length}, userEvent: ev || 'input.type'}).state;
// every place an arrow key stops: never inside what is hidden; a letter typed there is shown and the source stays sure
out.walk = {};
for (const [name, doc] of Object.entries(CORPUS)) {
  const st = make(doc), stops = stopsOf(st);
  let inside = 0, unsure = [], unseen = [], refused = 0;
  for (const s of stops) {
    if (hidden(st, s)) inside += 1;
    said.length = 0;
    const after = T(st, s, 'Q'), d = after.doc.toString();
    if (d === doc) { refused += 1; continue; }
    if (sure(d)) unsure.push(s);
    else if (!view(after).text.includes('Q')) unseen.push(s);
  }
  out.walk[name] = {stops: stops.length, inside, unsure: unsure.length, unseen: unseen.length, refused};
}
// typing each special character writes its escape, and shows the character
const P0 = '\\documentclass{article}\n\\begin{document}\nSome text.\n\\end{document}\n', at0 = P0.indexOf('text');
out.typed = {};
for (const ch of ['%', '$', '&', '#', '_', '{', '}', '~', '^', '\\']) {
  const st = T(make(P0, at0), at0, ch), d = st.doc.toString();
  out.typed[ch] = {source: d.slice(at0, d.indexOf('text.')), shows: view(st).text.includes('Some ' + ch + 'text')};
}
// an input method's composition is left as typed (never moved or escaped in its middle)
out.compose = T(make(P0, at0), at0, '汉字', 'input.type.compose').doc.toString().includes('Some 汉字text');
// pasting: plain text, escaped; a line break a new paragraph, in a heading a space, in a list a new item
const PS = '\\documentclass{article}\n\\begin{document}\n\\section{Head}\nPara.\n\\begin{itemize}\n  \\item one\n\\end{itemize}\n\\end{document}\n';
const paste = (at, text) => { const d = T(make(PS, at), at, text, 'input.paste').doc.toString(); return d.slice(PS.indexOf('\\begin{document}') + 17, d.lastIndexOf('\\end{document}')); };
out.paste = {text: paste(PS.indexOf('Para.') + 4, ' 5% & more\nnext line'), heading: paste(PS.indexOf('Head') + 2, 'a\nb'), item: paste(PS.indexOf('one') + 3, 'two\nthree')};
// moving a line or commenting it out are the code's: nothing happens in the edit view
out.lineMoves = ['move.line', 'comment.line'].map((ev) => make(P0, at0).update({changes: {from: P0.indexOf('Some'), insert: '% '}, userEvent: ev}).state.doc.toString() === P0);
// a letter typed right after a command with no braces never runs into it
const PN = '\\documentclass{article}\n\\begin{document}\n\\noindent Text and \\LaTeX here.\n\\end{document}\n';
const stN = make(PN), noi = PN.indexOf('\\noindent');
out.joint = T(stN, noi + 10, 'X').doc.toString().split('\n')[2];
const lat = PN.indexOf('\\LaTeX') + 6;
out.joint2 = stN.update({changes: {from: lat, to: lat + 1}, userEvent: 'delete.backward'}).state.doc.toString().split('\n')[2];   // the space after \LaTeX deleted
// Backspace like Word
const BK = '\\documentclass{article}\n\\begin{document}\nThe first paragraph.\n\n\\section{Head}\\label{s}\nA \\textbf{bold} word and \\textit{} empty.\n\\begin{itemize}\n  \\item one\n  \\item two\n\\end{itemize}\nA formula $x$ here.\n\\end{document}\n';
const back = (at, dir) => { const st = make(BK, at), r = P.backSpec(CM, st, st.field(F.field), dir || -1); if (!r) { const st2 = st.update({changes: dir > 0 ? {from: at, to: at + 1} : {from: at - 1, to: at}, userEvent: dir > 0 ? 'delete.forward' : 'delete.backward'}).state; return st2.doc.toString() === BK ? 'unchanged' : st2.doc.toString().split('\\begin{document}\n')[1]; } return r.refused != null ? {refused: r.refused} : st.update(r).state.doc.toString().split('\\begin{document}\n')[1]; };
// CodeMirror's own Backspace takes an atom whole: modelled by deleting the atom that ends at the cursor
const backAtom = (at) => { const st = make(BK, at), v = st.field(F.field); let from = at - 1; v.atoms.between(at, at, (a, b, d) => { if (b === at && d.spec.atom) from = Math.min(from, a); }); const st2 = st.update({changes: {from, to: at}, userEvent: 'delete.backward'}).state; return st2.doc.toString().split('\\begin{document}\n')[1]; };
out.back = {
  headingStart: back(BK.indexOf('Head')),
  boldStart: back(BK.indexOf('bold')),
  boldEnd: back(BK.indexOf('bold') + 4, 1),
  emptyStyle: back(BK.indexOf('\\textit{}') + 8),
  secondItem: backAtom(BK.indexOf('two')),
  firstItem: backAtom(BK.indexOf('one')),
  formula: backAtom(BK.indexOf('$ here') + 1),
};
// undo restores the file byte for byte after typing, pasting, deleting across markup, Return and Backspace
const undoView = (st) => { const v = {state: st}; v.dispatch = (tr) => { v.state = tr.state; }; return v; };   // CodeMirror calls dispatch unbound
let us = make(CORPUS.hand, CORPUS.hand.indexOf('second line'), [CM.history()]);
const steps = [];
const typeAt = (pos, text, ev) => { us = us.update({changes: {from: pos, insert: text}, selection: {anchor: pos + text.length}, userEvent: ev || 'input.type'}).state; steps.push(ev || 'input.type'); };
typeAt(CORPUS.hand.indexOf('second line'), 'fresh ');
typeAt(us.doc.toString().indexOf('Prices'), '{a}$', 'input.paste');
{ const d = us.doc.toString(), a = d.indexOf('bold') + 2, b = d.indexOf('italic') + 3; us = us.update({changes: {from: a, to: b}, userEvent: 'delete.selection'}).state; steps.push('del'); }
{ us = us.update({selection: {anchor: us.doc.toString().indexOf('Every') + 5}}).state; us = us.update(P.enterSpec(CM, us, us.field(F.field), false)).state; steps.push('enter'); }
{ const p = us.doc.toString().indexOf('Method}'); us = us.update({selection: {anchor: p}}).state; const r = P.backSpec(CM, us, us.field(F.field), -1); if (r && !r.refused) { us = us.update(r).state; steps.push('back'); } }
out.undo = {changed: us.doc.toString() !== CORPUS.hand, steps: steps.length};
const uv = undoView(us);
for (let k = 0; k < 40 && CM.undo(uv); k++);
out.undo.restored = uv.state.doc.toString() === CORPUS.hand;
// copying in the edit view gives the words as shown
const cp = make(CORPUS.hand), cd = CORPUS.hand, cfrom = cd.indexOf('Text with'), cto = cd.indexOf('Prices');
out.copy = P.plainOf(cp.field(F.field), cfrom, cto);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def edited() -> dict:
    return _corpus(_EDIT)


@needs_node
@pytest.mark.parametrize("paper", ["zh", "en", "hand"])
def test_the_cursor_never_stops_inside_what_is_hidden_and_a_letter_typed_at_any_stop_is_shown_and_safe(edited, paper):
    w = edited["walk"][paper]
    assert w["stops"] > 150
    assert w["inside"] == 0 and w["unsure"] == 0 and w["unseen"] == 0 and w["refused"] == 0, w


@needs_node
def test_typing_a_special_character_writes_its_escape_and_shows_the_character(edited):
    t = edited["typed"]
    assert {k: v["source"] for k, v in t.items()} == {"%": "\\%", "$": "\\$", "&": "\\&", "#": "\\#", "_": "\\_", "{": "\\{", "}": "\\}",
                                                       "~": "\\textasciitilde{}", "^": "\\textasciicircum{}", "\\": "\\textbackslash{}"}
    assert all(v["shows"] for v in t.values())
    assert edited["compose"] is True


@needs_node
def test_pasting_is_plain_text_escaped_with_line_breaks_as_paragraphs_spaces_or_items_by_place(edited):
    p = edited["paste"]
    assert p["text"] == "\\section{Head}\nPara 5\\% \\& more\n\nnext line.\n\\begin{itemize}\n  \\item one\n\\end{itemize}\n"
    assert p["heading"] == "\\section{Hea bad}\nPara.\n\\begin{itemize}\n  \\item one\n\\end{itemize}\n"     # one line in a heading
    assert p["item"] == "\\section{Head}\nPara.\n\\begin{itemize}\n  \\item onetwo\n  \\item three\n\\end{itemize}\n"


@needs_node
def test_moving_or_commenting_out_lines_does_nothing_in_the_edit_view(edited):
    assert edited["lineMoves"] == [True, True]


@needs_node
def test_a_letter_after_a_command_with_no_braces_never_runs_into_its_name(edited):
    assert edited["joint"] == "\\noindent XText and \\LaTeX here."        # after the space \\noindent swallows
    assert edited["joint2"] == "\\noindent Text and \\LaTeX{}here."        # its space deleted: {} keeps them apart


@needs_node
def test_backspace_follows_word_at_a_heading_a_style_an_empty_style_an_item_and_a_formula(edited):
    b, rest = edited["back"], "\n\\end{document}\n"
    body = "A \\textbf{bold} word and \\textit{} empty.\n\\begin{itemize}\n  \\item one\n  \\item two\n\\end{itemize}\nA formula $x$ here."
    # at a heading's start: body text, joined with the paragraph before (its blank line one line break)
    assert b["headingStart"] == "The first paragraph.\nHead\\label{s}\n" + body + rest
    head = "The first paragraph.\n\n\\section{Head}\\label{s}\n"
    assert b["boldStart"] == head + body.replace("A \\textbf", "A\\textbf") + rest          # the space before the style
    assert b["boldEnd"] == head + body.replace("bold} word", "bold}word") + rest              # Delete: the space after it
    assert b["emptyStyle"] == head + body.replace("\\textit{}", "") + rest                  # an empty style goes whole
    assert b["secondItem"] == head + body.replace("\\item one\n  \\item two", "\\item one\ntwo") + rest   # merged with the item before
    assert b["firstItem"] == head + body + rest                                            # the list's first \\item stays
    assert b["formula"] == head + body.replace("$x$", "") + rest                            # a formula goes whole


@needs_node
def test_undo_restores_the_file_byte_for_byte(edited):
    assert edited["undo"]["changed"] is True and edited["undo"]["steps"] >= 4
    assert edited["undo"]["restored"] is True


@needs_node
def test_copying_in_the_edit_view_gives_the_words_as_shown(edited):
    c = edited["copy"]
    assert c.startswith("Text with bold, italic, emphasis and lines. A second line of the same paragraph, see smith2020；lee2019 and Section")
    assert "\\" not in c and "{" not in c


# -- deletion and replacement fuzz --------------------------------------------------------------------------------------

_FUZZ = r"""
let seed = 20261114;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
const texts = ['', '', '', 'x', 'new words', '50%', 'a{b}', 'c$d', 'para\none', '#_&~^\\', '中文'];
out.fuzz = {cases: 0, refused: 0, unsure: 0, lost: 0, markup: 0, examples: []};
out.samples = {zh: [], en: [], hand: []};
const keepEvery = {zh: 0, en: 0, hand: 0};
for (const [name, doc] of Object.entries(CORPUS)) {
  const st = make(doc), stops = stopsOf(st);
  for (let k = 0; k < 800; k++) {
    // a selection as the editor makes them (from caret stop to caret stop), or one from anywhere to anywhere
    const free = k % 4 === 3, a = free ? rnd(doc.length + 1) : stops[rnd(stops.length)], b = free ? rnd(doc.length + 1) : stops[rnd(stops.length)];
    const from = Math.min(a, b), to = Math.max(a, b);
    if (from === to) continue;
    let ins = texts[rnd(texts.length)];
    if (name !== 'zh' && ins === '中文') ins = 'words';   // pdfLaTeX cannot set Chinese: the English papers get English
    const r = st.update({changes: {from, to, insert: ins}, userEvent: ins ? (k % 2 ? 'input.paste' : 'input.type') : 'delete.selection'});
    const d = r.state.doc.toString();
    out.fuzz.cases += 1;
    if (d === doc) { out.fuzz.refused += 1; continue; }
    if (sure(d)) { out.fuzz.unsure += 1; if (out.fuzz.examples.length < 3) out.fuzz.examples.push(['unsure', name, from, to, ins]); continue; }
    // the words outside the range (widened to whole atoms) are all still there, in order
    let lo = from, hi = to;
    st.field(F.field).atoms.between(from, from, (x, y) => { if (x < from && from < y) lo = Math.min(lo, x); });
    st.field(F.field).atoms.between(to, to, (x, y) => { if (x < to && to < y) hi = Math.max(hi, y); });
    const before = shownLetters(st, 0, lo), after = shownLetters(st, hi, doc.length), now = shownLetters(r.state, 0, d.length);
    if (!now.startsWith(before) || !now.endsWith(after)) { out.fuzz.lost += 1; if (out.fuzz.examples.length < 3) out.fuzz.examples.push(['lost', name, from, to, ins]); }
    // what is shown of the source itself (outside every widget) holds no markup: a typed % is shown by its escape's widget
    const raw = rawShown(r.state);
    if ([...raw].some((c) => '\\{}$&#^_~%'.includes(c))) { out.fuzz.markup += 1; if (out.fuzz.examples.length < 3) out.fuzz.examples.push(['markup', name, from, to, ins, raw.slice(0, 80)]); }
    if (name === 'zh' ? true : true) { keepEvery[name] += 1; if (keepEvery[name] % 37 === 5 && out.samples[name].length < (name === 'zh' ? 15 : name === 'en' ? 8 : 7)) out.samples[name].push(d); }
  }
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def fuzz() -> dict:
    return _corpus(_FUZZ)


@needs_node
def test_two_thousand_random_deletions_and_replacements_keep_the_source_balanced_and_lose_no_word_outside(fuzz):
    f = fuzz["fuzz"]
    print("fuzz:", json.dumps({k: v for k, v in f.items() if k != "examples"}))
    assert f["cases"] >= 2000
    assert f["unsure"] == 0 and f["lost"] == 0 and f["markup"] == 0, f["examples"]
    assert f["refused"] < f["cases"] // 20                             # a refusal is the exception, said with a sentence


def _compile(tmp_path: Path, name: str, text: str, engine: str) -> subprocess.CompletedProcess:
    work = tmp_path / name
    (work / "figs").mkdir(parents=True)
    # a real png (2 by 2, grey), as the figures name it
    (work / "figs" / "plot.png").write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d4948445200000002000000020802000000fdd49a730000000e49444154789c63680003060805002a0e0601159e42d70000000049454e44ae426082"))
    (work / "main.tex").write_text(text, encoding="utf-8")
    env = dict(os.environ, HOME=str(tmp_path / "home"), PATH=str(TEXBIN) + os.pathsep + os.environ.get("PATH", ""))
    return subprocess.run([LATEXMK, "-" + engine, "-interaction=nonstopmode", "-halt-on-error", "-f-", "main.tex"],
                          cwd=work, env=env, capture_output=True, text=True, timeout=300)


@needs_node
@pytest.mark.skipif(LATEXMK is None, reason="latexmk is not installed")
def test_thirty_fuzzed_papers_and_the_corpus_compile_with_latexmk_on_both_engines(fuzz, tmp_path):
    (tmp_path / "home").mkdir()
    samples = [("zh", d) for d in fuzz["samples"]["zh"]] + [("en", d) for d in fuzz["samples"]["en"]] + [("hand", d) for d in fuzz["samples"]["hand"]]
    assert len(samples) == 30
    jobs = [(f"corpus-{k}", d, "xelatex" if k == "zh" else "pdf") for k, d in fuzz["corpus"].items()]
    jobs += [(f"fuzz-{n:02d}-{k}", d, "xelatex" if k == "zh" else "pdf") for n, (k, d) in enumerate(samples)]
    failed = []
    for name, text, engine in jobs:
        done = _compile(tmp_path, name, text, engine)
        pdf = tmp_path / name / "main.pdf"
        log = tmp_path / name / "main.log"
        said = log.read_text(errors="replace") if log.exists() else done.stdout
        # a TeX error is a line starting "!"; citations with no .bib (latexmk's 12) are only warnings
        errors = [ln for ln in said.splitlines() if ln.startswith("!")]
        if errors or not pdf.exists() or not pdf.read_bytes().startswith(b"%PDF-") or done.returncode not in (0, 12):
            failed.append((name, "\n".join(errors) or said[-1500:]))
    print("compiled:", len(jobs), "failed:", [f[0] for f in failed])
    assert failed == [], failed[0][1]


# -- on the page: 显示代码, files opened as code, the right column ---------------------------------------------------------

_PAGE = r"""
El.prototype.setAttribute = function (k, v) { (this.attrs || (this.attrs = {}))[k] = String(v); };
FakeView.prototype.dispatch = function (spec) {
  const tr = this.state.update(spec); this.state = tr.state;
  for (const listen of tr.state.facet(real.EditorView.updateListener)) listen({docChanged: tr.docChanged, selectionSet: !!spec.selection, changes: tr.changes, view: this, state: tr.state});
};
FakeView.prototype.focus = function () { this.focused = (this.focused || 0) + 1; };
global.requestAnimationFrame = (f) => f();
const out = {};
const view = () => viewsMade[viewsMade.length - 1];
const inside = (el, test, acc) => { acc = acc || []; if (el && typeof el === 'object') { if (test(el)) acc.push(el); (el.children || []).forEach((k) => inside(k, test, acc)); } return acc; };
const side = () => byClass('latex-side');
const hiddenEl = (el) => el.classList.contains('hidden');
const shownWords = (root) => { const acc = []; const walk = (el) => { if (!el || typeof el !== 'object' || hiddenEl(el)) return; if (el.textContent) acc.push(el.textContent); (el.children || []).forEach(walk); }; walk(root); return acc; };
const sw = () => inside(side(), (e) => e.classList.contains('latex-switch'))[0];
const field = () => view().state.field(L._pure.wordField(CM).field, false);
const doc = () => view().state.doc.toString();
const f = (path, text, extra) => Object.assign({path, text, revision: 'r1', line_ending: 'lf', read_only: false, read_only_reason: null, links: [], mains: []}, extra);
const openFile = async (path, text, extra) => { L.open(path); await tick(); gets.shift().res(f(path, text, extra)); await tick(); await tick(); };
const context = () => inside(side(), (e) => e.classList.contains('latex-context'))[0];
const TEXT = '\\documentclass{article}\n\\title{Old \\& title}\n\\author{Ann}\n\\begin{document}\n\\maketitle\nSome text.\\footnote{A plain note}\n\\begin{table}\n\\caption{T}\n\\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}\n\\end{table}\nA formula $x$ here.\n\\end{document}\n';
await openFile('a.tex', TEXT, {mains: ['a.tex'], format: {main: 'a.tex', engine: 'pdflatex', folder: '', preamble: null, local: []}});
const s0 = sw();
out.atTop = side().children[0].className === 'latex-format' && side().children[0].children[0].className === 'latex-switch-row';
out.switch0 = {pressed: s0.attrs['aria-pressed'], disabled: !!s0.disabled, page: byClass('latex-page').className, word: !!field()};
out.header = inside(byClass('latex-head'), (e) => e.tag === 'button').map((b) => b.textContent);
// a place in the text: 显示代码 on keeps it; off again keeps it; no step of ⌘Z
const at = TEXT.indexOf('text.') + 2;
view().dispatch({selection: {anchor: at}});
const undo0 = real.undoDepth(view().state);
s0.fire('click');
out.codeOn = {pressed: sw().attrs['aria-pressed'], page: byClass('latex-page').className, word: !!field(), sel: view().state.selection.main.head, undo: real.undoDepth(view().state) - undo0,
  formatOff: inside(side(), (e) => e.classList.contains('latex-format-reason'))[0].textContent, layoutOn: !inside(side(), (e) => e.tag === 'select' && e.attrs && e.attrs['aria-label'] === '日期')[0].disabled};
// in the code, a place inside what the edit view hides: back in the edit view the cursor is at its edge
view().dispatch({selection: {anchor: TEXT.indexOf('footnote') + 3}});
sw().fire('click');
const v = field(); let edge = true; v.deco.between(view().state.selection.main.head, view().state.selection.main.head, (a, b, d) => { if (d.spec.atom && a < view().state.selection.main.head && view().state.selection.main.head < b) edge = false; });
out.codeOff = {pressed: sw().attrs['aria-pressed'], word: !!field(), edge, undo: real.undoDepth(view().state) - undo0};
// the cursor beside the table: the column says where its cells are changed; the link turns the code on there
view().dispatch({selection: {anchor: TEXT.indexOf('\\end{table}') + 11}});
out.tableHint = shownWords(context());
inside(context(), (e) => e.tag === 'button')[0].fire('click');
out.tableLink = {code: sw().attrs['aria-pressed'], at: view().state.selection.main.head === TEXT.indexOf('\\begin{table}')};
sw().fire('click');
// a click on a formula selects it whole, and the column says so
view().dispatch({selection: {anchor: TEXT.indexOf('$x$'), head: TEXT.indexOf('$x$') + 3}});
out.formulaHint = shownWords(context());
// the footnote: its words in the column, changed there (escaped as typing is), one undoable run of typing
view().dispatch({selection: {anchor: TEXT.indexOf('\\footnote') + '\\footnote{A plain note}'.length}});
const note = inside(context(), (e) => e.tag === 'textarea')[0];
out.noteShown = note ? note.value : null;
const u1 = real.undoDepth(view().state);
note.value = 'A 50% note'; note.fire('input');
note.value = 'A 50% note!'; note.fire('input');
out.noteWritten = {source: doc().slice(doc().indexOf('\\footnote'), doc().indexOf('\\footnote') + 23), steps: real.undoDepth(view().state) - u1, still: inside(context(), (e) => e.tag === 'textarea')[0] === note};
// 文档信息 in 版式: read from the preamble, written there as the editor's own step
inside(side(), (e) => e.attrs && e.attrs.role === 'tab' && e.textContent === '版式')[0].fire('click');
const inputs = inside(side(), (e) => e.tag === 'input' && e.attrs && ['标题', '作者', '日期'].includes(e.attrs['aria-label']));
const title = inputs.find((i) => i.attrs['aria-label'] === '标题'), author = inputs.find((i) => i.attrs['aria-label'] === '作者');
const date = inside(side(), (e) => e.tag === 'select' && e.attrs && e.attrs['aria-label'] === '日期')[0];
out.info = {title: title.value, author: author.value, date: date.value};
title.value = 'New & title'; title.fire('change');
date.value = 'none'; date.fire('change');
out.infoWritten = doc().split('\\begin{document}')[0];
// the title block: a click opens 版式 and its 标题
inside(side(), (e) => e.attrs && e.attrs.role === 'tab' && e.textContent === '格式')[0].fire('click');
let tb = null; field().deco.between(0, doc().length, (a, b, d) => { if (d.spec.kind === 'title') tb = d.spec.widget; });
const tel = tb.toDOM({state: view().state, dispatch() {}, focus() {}});
title.focus = () => { out.titleFocused = true; };
tel.fire('mousedown', {button: 0});
out.titleClick = inside(side(), (e) => e.attrs && e.attrs.role === 'tab' && e.textContent === '版式')[0].attrs['aria-selected'];
// 公式 in the edit view: written in the code, the cursor in it
inside(side(), (e) => e.attrs && e.attrs.role === 'tab' && e.textContent === '格式')[0].fire('click');
view().dispatch({selection: {anchor: doc().indexOf('here.') + 5}});
inside(side(), (e) => e.tag === 'button' && e.textContent === '行内')[0].fire('click');
out.formulaTool = {code: sw().attrs['aria-pressed'], around: doc().slice(view().state.selection.main.head - 1, view().state.selection.main.head + 1)};
// the edit view will not take back a text it cannot show (a brace typed in the code and not closed): it says why, stays
// in the code (the tool's own empty formula, \\(\\), is one it can show)
view().dispatch({changes: {from: doc().indexOf('Some text'), insert: '{'}});
sw().fire('click');
out.unsureBack = {code: sw().attrs['aria-pressed'], why: shownWords(inside(side(), (e) => e.classList.contains('latex-code-why'))[0])};
// a file the edit view cannot show safely, or one only viewed: opened as code, the switch on and disabled, saying why
await openFile('b.tex', 'Text with {an unclosed brace.\n');
out.unsure = {pressed: sw().attrs['aria-pressed'], disabled: !!sw().disabled, why: shownWords(inside(side(), (e) => e.classList.contains('latex-code-why'))[0]), word: !!field(), page: byClass('latex-page').className};
sw().fire('click');
out.unsureClick = {word: !!field()};
await openFile('c.tex', 'Plain \\textbf{text}.\n', {read_only: true, read_only_reason: 'generated', read_only_message: 'x'});
out.readOnly = {pressed: sw().attrs['aria-pressed'], disabled: !!sw().disabled, why: shownWords(inside(side(), (e) => e.classList.contains('latex-code-why'))[0]), word: !!field()};
// the .bib's authors and years reach the chips a moment after a file with a citation opens
await openFile('d.tex', '\\documentclass{article}\n\\begin{document}\nSee \\cite{smith2020}.\n\\end{document}\n');
out.bibAsked = gets.length;
timers.splice(0).forEach((fn) => fn()); await tick();
const bibGet = gets.find((g) => g.url.startsWith('/api/latex/insert?what=bib'));
out.bibUrl = bibGet ? bibGet.url : null;
bibGet.res({files: ['refs.bib'], entries: [{key: 'smith2020', author: 'Smith, John and Lee, Ann and Wu, Bo', year: '2020', title: 'T'}]}); await tick(); await tick();
let chip = null; field().deco.between(0, doc().length, (a, b, d) => { if (d.spec.kind === 'cite') chip = d.spec.widget.text; });
out.bibChip = chip;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def page() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    script = prelude + "(async () => {\n" + _PAGE + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });"
    done = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=120)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    return out


@needs_node
def test_show_code_is_a_switch_at_the_top_of_the_right_column_off_by_default_and_gone_from_the_header(page):
    assert page["atTop"] is True
    assert page["switch0"] == {"pressed": "false", "disabled": False, "page": "latex-page", "word": True}
    assert "显示代码" not in page["header"] and "显示源码" not in page["header"]


@needs_node
def test_show_code_keeps_the_cursors_place_is_no_undo_step_and_leaves_layout_working(page):
    on = page["codeOn"]
    assert on["pressed"] == "true" and on["page"] == "latex-page is-source" and on["word"] is False
    assert on["undo"] == 0 and on["sel"] == page["codeOn"]["sel"]
    assert on["formatOff"] == "正在显示代码。关掉「显示代码」后可以用格式工具。" and on["layoutOn"] is True
    off = page["codeOff"]
    assert off == {"pressed": "false", "word": True, "edge": True, "undo": 0}   # back: the cursor at the edge of the footnote


@needs_node
def test_the_right_column_says_a_table_and_a_formula_are_changed_in_show_code_with_a_link_there(page):
    assert page["tableHint"] == ["表格内容要在「显示代码」里改。", "在「显示代码」里改"]
    assert page["tableLink"] == {"code": "true", "at": True}
    assert page["formulaHint"] == ["公式要在「显示代码」里改。", "在「显示代码」里改"]


@needs_node
def test_a_footnotes_words_are_read_and_changed_in_the_right_column_escaped_as_one_run_of_typing(page):
    assert page["noteShown"] == "A plain note"
    w = page["noteWritten"]
    assert w["source"] == "\\footnote{A 50\\% note!}" and w["steps"] == 1 and w["still"] is True


@needs_node
def test_document_details_read_and_write_the_title_author_and_date_in_the_preamble(page):
    assert page["info"] == {"title": "Old & title", "author": "Ann", "date": "today"}
    assert page["infoWritten"] == "\\documentclass{article}\n\\title{New \\& title}\n\\author{Ann}\n\\date{}\n"
    assert page["titleClick"] == "true" and page["titleFocused"] is True


@needs_node
def test_a_formula_from_the_tool_is_written_in_show_code_and_an_unsure_text_stays_there(page):
    assert page["formulaTool"] == {"code": "true", "around": "(\\"}                  # \\(|\\): review, "$$" opened a display
    assert page["unsureBack"] == {"code": "true", "why": ["这个文件里有没配对的括号，只能显示代码。"]}   # "{" typed in the code


@needs_node
def test_a_file_the_edit_view_cannot_show_safely_or_only_viewed_opens_as_code_with_the_switch_on_and_disabled(page):
    assert page["unsure"] == {"pressed": "true", "disabled": True, "why": ["这个文件里有没配对的括号，只能显示代码。"], "word": False,
                              "page": "latex-page is-source"}
    assert page["unsureClick"] == {"word": False}
    assert page["readOnly"] == {"pressed": "true", "disabled": True, "why": ["这个文件只能查看，只显示代码。"], "word": False}


@needs_node
def test_citations_take_author_and_year_from_the_bib_read_after_the_file_opens(page):
    assert page["bibUrl"] == "/api/latex/insert?what=bib&path=d.tex"
    assert page["bibChip"] == "Smith 等 2020"


# -- time -------------------------------------------------------------------------------------------------------------

_TIMING = r"""
window.katex = kctx.katex;
const lines = ['\\documentclass{article}', '\\usepackage{amsmath}', '\\usepackage{booktabs}', '\\title{A \\textbf{long} paper}', '\\author{A}',
  '\\begin{document}', '\\maketitle', '\\begin{abstract}', 'We study $x$ and {\\large y}.', '\\end{abstract}'];
for (let i = 0; lines.length < 2997; i++) {
  if (i % 40 === 0) lines.push('\\section{Section ' + i + '}\\label{sec:' + i + '}');
  else if (i % 50 === 3) lines.push('\\begin{table}[htbp]', '\\centering', '\\caption{Results ' + i + '}', '\\label{tab:' + i + '}', '\\begin{tabular}{lcr}', '\\toprule',
    'A & B & C \\\\', '\\midrule', '1 & $' + i + '$ & \\textbf{3} \\\\', '\\bottomrule', '\\end{tabular}', '\\end{table}');
  else if (i % 70 === 5) lines.push('\\begin{figure}[htbp]', '\\centering', '\\includegraphics[width=\\linewidth]{figs/f' + i + '.png}', '\\caption{Figure ' + i + '}', '\\end{figure}');
  else if (i % 10 === 0) lines.push('\\begin{equation}', 'y_{' + i + '} = \\alpha + \\beta x_i + \\varepsilon_i \\label{eq:' + i + '}', '\\end{equation}');
  else if (i % 9 === 0) lines.push('% a comment line ' + i);
  else if (i % 5 === 0) lines.push('The effect $\\beta_' + i + ' = 0.' + i + '$ is \\textbf{large} and {\\heiti 稳健} \\cite{a' + i + '} (see \\ref{sec:' + i + '}).\\footnote{Note ' + i + '.}');
  else if (i % 7 === 0) lines.push('\\begin{itemize}', '  \\item first point with $x$', '  \\item second {\\large point}', '\\end{itemize}');
  else lines.push('Running text of the paper, 中文的正文，{\\kaishu 一段一段}地写下去，with numbers 3.14 and 5\\% and a comment % here');
}
lines.push('\\end{document}');
const text = lines.join('\n');
const ms = (t0) => Number(process.hrtime.bigint() - t0) / 1e6, stats = (xs) => { xs.sort((a, b) => a - b); return {median: xs[xs.length >> 1], p95: xs[Math.floor(xs.length * 0.95)], max: xs[xs.length - 1]}; };
// one key, typed as the edit view types it (its filter), and every widget within 60 lines the view would draw anew
const measure = (at, n, key) => {
  let st = CM.EditorState.create({doc: text, extensions: [P.wordExtensions(CM)], selection: {anchor: at}});
  const shown = (s) => { const l = s.doc.lineAt(s.selection.main.head), from = s.doc.line(Math.max(1, l.number - 60)).from, to = s.doc.line(Math.min(s.doc.lines, l.number + 60)).to, got = []; s.field(F.field).deco.between(from, to, (a, b, v) => { if (v.spec.widget) got.push({a, b, w: v.spec.widget}); }); return got; };
  for (let k = 0; k < 20; k++) st = st.update(key(st, -1)).state;
  const keys = [];
  for (let k = 0; k < n; k++) {
    const before = shown(st), t0 = process.hrtime.bigint();
    const tr = st.update(key(st, k));
    st = tr.state;
    st.field(F.field);
    st.field(F.field).atoms.between(st.selection.main.head - 1, st.selection.main.head + 1, () => {});   // the cursor's next move
    let spent = ms(t0);
    const had = new Map(before.map((x) => [tr.changes.mapPos(x.a, 1) + ':' + tr.changes.mapPos(x.b, -1), x.w]));
    for (const {a, b, w} of shown(st)) if (!(had.get(a + ':' + b) && had.get(a + ':' + b).eq(w))) { const t2 = process.hrtime.bigint(); w.toDOM({dispatch() {}, state: st, focus() {}}); spent += ms(t2); }
    keys.push(spent);
  }
  return stats(keys);
};
const typeKey = (ch) => (st) => { const h = st.selection.main.head; return {changes: {from: h, insert: ch(st)}, selection: {anchor: h + 1}, userEvent: 'input.type'}; };
const run1 = text.indexOf('Running text', 60000), run2 = text.indexOf('The effect', 60000), item = text.indexOf('first point', 60000) + 5;
Object.assign(out, {
  lines: text.split('\n').length, formulas: P.readLatex(text).maths.length,
  running: measure(run1, 300, typeKey(() => 'x')),
  beside: measure(run2 + 4, 200, typeKey(() => 'y')),
  inItem: measure(item, 200, typeKey(() => 'z')),
  special: measure(run1, 100, typeKey(() => '%')),
  enter: measure(run1, 60, (st, k) => (k < 0 ? {selection: {anchor: st.selection.main.head}} : P.enterSpec(CM, st, st.field(F.field), false))),
  backspace: measure(run1 + 200, 100, (st) => { const h = st.selection.main.head; return {changes: {from: h - 1, to: h}, selection: {anchor: h - 1}, userEvent: 'delete.backward'}; }),
});
const t0 = process.hrtime.bigint(); CM.EditorState.create({doc: text, extensions: [P.wordExtensions(CM)]}).field(F.field); out.open = ms(t0);
console.log(JSON.stringify(out));
"""


@needs_node
def test_typing_return_and_backspace_stay_under_16_ms_a_key_on_the_3000_line_file_with_300_formulas():
    got = _node(_TIMING)
    print("the edit view, per key:", json.dumps(got))
    assert got["lines"] >= 2998 and got["formulas"] >= 300
    for kind in ("running", "beside", "inItem", "special", "enter", "backspace"):
        assert got[kind]["median"] < 16 and got[kind]["p95"] < 16, kind


# -- drawn again only where a change reaches ---------------------------------------------------------------------------

_INCREMENTAL = r"""
const bits = ['x', ' ', '\n', '\n\n', '{', '}', '$', '\\', '&', '\\\\', '%', '中', '\\begin{itemize}\n\\item a\n\\end{itemize}', '\\textbf{b}',
  '\\section{S}', '% c\n', '\\label{x}', '\\cite{k}', '\\ref{fig:plot}', '\\begin{quote}\nq\n\\end{quote}', '\\footnote{n}', '~', "\\'e",
  '\\begin{figure}\n\\includegraphics{a.png}\n\\caption{C}\n\\end{figure}'];
const sig = (s) => { const got = []; const v = s.field(F.field);
  v.deco.between(0, s.doc.length, (a, b, d) => { const w = d.spec.widget; got.push([a, b, d.spec.class || (d.spec.attributes && d.spec.attributes.class) || '',
    w ? w.constructor.name + JSON.stringify(Object.assign({}, w, {make: undefined, onClick: undefined})) : '', !!d.spec.block, d.spec.kind || '', d.spec.lead, d.spec.trail, d.spec.fwd, d.spec.slotFrom, d.spec.slotTo].join('|')); });
  v.atoms.between(0, s.doc.length, (a, b, d) => { got.push('A' + a + ':' + b + (d.spec.bridge ? 'b' : '')); });
  return got.sort(); };
out.incremental = [];
for (const seed0 of [11, 12]) {
  let seed = seed0; const rnd = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
  let cur = make(CORPUS.hand), mismatch = null, steps = 0;
  for (let k = 0; k < 1000 && !mismatch; k++) {
    const len = cur.doc.length, r = rnd(10), a = rnd(len + 1);
    // changes as 显示代码, undo or a tool would make them: unfiltered
    let spec;
    if (r < 5) spec = {changes: {from: a, insert: bits[rnd(bits.length)]}, userEvent: 'input.format'};
    else if (r < 8) spec = {changes: {from: a, to: Math.min(len, a + 1 + rnd(30))}, userEvent: 'input.format'};
    else spec = {changes: {from: a, to: Math.min(len, a + rnd(40)), insert: bits[rnd(bits.length)]}, userEvent: 'input.format'};
    cur = cur.update(spec).state; steps += 1;
    const x = sig(cur), y = sig(make(cur.doc.toString()));
    if (JSON.stringify(x) !== JSON.stringify(y)) mismatch = {step: k, only: x.filter((z) => !y.includes(z)).slice(0, 4), missing: y.filter((z) => !x.includes(z)).slice(0, 4)};
  }
  out.incremental.push({steps, mismatch});
}
console.log(JSON.stringify(out));
"""


@needs_node
def test_drawing_again_only_where_a_change_reaches_equals_drawing_the_whole_paper_after_every_edit():
    got = _corpus(_INCREMENTAL)["incremental"]
    assert got == [{"steps": 1000, "mismatch": None}, {"steps": 1000, "mismatch": None}]

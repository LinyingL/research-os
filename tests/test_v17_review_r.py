"""DESIGN.md 22.12 R (task V17): what three adversarial reviews of T and M found (2026-11-14/15) -- tables and the text's
integrity, the edit view against the PDF, behaviour and design -- each reproduced and fixed with a test that failed
before. Under node on the vendored CodeMirror and KaTeX, the engine's own functions, and real compiles (TeX Live 2023,
latexmk, read back by pdftotext) where what TeX prints is the question.

Tables: a first cell emptied keeps the cursor in it and the next Return, ⇧Return and Tab keep the grid; lines pasted
into a cell's words are its words; words typed between two drawn pieces stay there; ⇧Return at a table's start is
refused; "[" typed after a rule's name never becomes its option; a row deleted between two rules takes one of them; the
arrow keys pass the table's end; colours are hidden and kept; a longtable says its caption. The view against the PDF:
\\frontmatter and \\backmatter, subfigures, longtables, sideways and wrapped floats, \\captionof, \\setcounter, the
appendix package, \\iffalse before a chapter file, \\footnotemark and \\footnote[7], TeX's own spacing, words after a
heading or a figure on its line, \\noindent on a line of its own, the document's names, hand-written 段间距 and 行距. The
engine: a figure that cannot be drawn is remembered, the converters capped, the cache pruned; build folders' PDFs are no
images. The page: a disabled switch looks disabled, the code's marks of invisible characters stay in the code, a
figure's PDF page is drawn at the figure's size, wide tables never break a number, 第一行是表头 says why, a refusal goes
when the cursor moves on, a picture is unseen until it loads, the 「表格」 group is a named group."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from researchos import latex_edit, project
from test_v17_acceptance import LATEXMK, PDFTOTEXT, _compile, _page, _view
from test_v17_latex_editor import NODE, WEBAPP
from test_v17_latex_word import _node
from test_v17_layout_settings import _PANEL as _LAYOUT_PANEL
from test_v17_table_edit import _BASE, _PAGE as _TABLE_PAGE

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
needs_tex = pytest.mark.skipif(LATEXMK is None or PDFTOTEXT is None, reason="latexmk and pdftotext are needed")
CSS = (WEBAPP / "workspace.css").read_text(encoding="utf-8")
JS = (WEBAPP / "latex.js").read_text(encoding="utf-8")

# keys as the editor runs them: the edit view's own binding, else CodeMirror's step (an atom whole), through the filter
_KEYS = r"""
const fakeView = (st) => { const v = {state: st, composing: false}; v.dispatch = (spec) => { v.state = v.state.update(spec).state; }; return v; };
const runKey = (st, key) => { const v = fakeView(st); for (const b of st.facet(CM.keymap).flat().filter((x) => x.key === key)) if (b.run && b.run(v)) return {st: v.state, handled: true}; return {st: v.state, handled: false}; };
const back = (st) => { const r = runKey(st, 'Backspace'); if (r.handled) return r.st; const p = st.selection.main.head; let a = p - 1, b = p;
  st.field(F.field).atoms.between(a, b, (x, y, v) => { if (v.spec.atom && x < b && y > a) { a = Math.min(a, x); b = Math.max(b, y); } });
  return st.update({changes: {from: a, to: b}, userEvent: 'delete.backward'}).state; };
const typeAt = (st, s, ev) => { const sel = st.selection.main; return st.update({changes: {from: sel.from, to: sel.to, insert: s}, selection: {anchor: sel.from + s.length}, userEvent: ev || 'input.type'}).state; };
const Wd = (body, pre) => '\\documentclass{article}\n\\usepackage{booktabs,tabularx,longtable,amsmath,colortbl}\n' + (pre || '') + '\\begin{document}\nBefore the table.\n\n' + body + '\n\nAfter the table.\n\\end{document}\n';
const BOOK = Wd('\\begin{table}[htbp]\n  \\centering\n  \\caption{Main}\n  \\label{tab:m}\n  \\begin{tabular}{lcr}\n    \\toprule\n    Var & Mean & SD \\\\\n    \\midrule\n    x & 1 & 2 \\\\\n    y & 3 & 4 \\\\\n    \\bottomrule\n  \\end{tabular}\n\\end{table}');
const HLINE = Wd('\\begin{tabular}{|l|c|}\n\\hline\na & b \\\\ \\hline\nc & d \\\\ \\hline\n\\end{tabular}');
const ONE = Wd('\\begin{tabular}{l}\na \\\\\n\\end{tabular}');
const caretOf = (text, cell) => { const s = text.slice(cell[0], cell[1]), lead = s.length - s.trimStart().length; return cell[0] + (s.trim() ? lead : Math.min(1, s.length)); };
const state = (st) => { const d = st.doc.toString(), h = st.selection.main.head, g = gridsIn(d)[0]; const c = g ? P.cellAt(d, P.readLatex(d), h) : null;
  return {grids: gridsIn(d).length, hidden: hidden(st, h), cell: c ? [c.r, c.c] : null, sure: sure(d), body: d.slice(d.indexOf('\\begin{tabular}'), d.indexOf('\\end{tabular}'))}; };
"""

_TABLES = _BASE + _KEYS + r"""
// 1. a first cell emptied by Backspace: the cursor in it, Return to the cell below, ⇧Return refused, Tab to the next cell
{ const at = BOOK.indexOf('x & 1') + 1, s1 = back(make(BOOK, at, [P.historyOf(CM)]));
  out.emptied = state(s1);
  const ret = runKey(s1, 'Enter'), sret = runKey(s1, 'Shift-Enter'), tab = runKey(s1, 'Tab');
  out.ret = Object.assign(state(ret.st), {handled: ret.handled}); out.sret = Object.assign(state(sret.st), {said: said[said.length - 1]});
  out.tab = Object.assign(state(tab.st), {handled: tab.handled});
  out.typed = state(typeAt(s1, 'Z'));
  const h = HLINE.indexOf('a & b') + 1, s2 = back(make(HLINE, h)); out.hline = state(runKey(s2, 'Enter').st);
  const o = ONE.indexOf('a \\\\') + 1, s3 = back(make(ONE, o)); out.one = state(runKey(s3, 'Enter').st);
  // a deletion across cells that clears a first cell: from the caption's end to the end of "Var"
  const cut = make(BOOK).update({changes: {from: BOOK.indexOf('Main}') + 4, to: BOOK.indexOf('Var') + 3}, userEvent: 'delete.selection'}).state;
  out.cut = state(cut); out.cutRet = state(runKey(cut, 'Enter').st); }
// 2. lines pasted into a cell's words are its words; a spreadsheet's (a tab) fills the cells, its quoted value one cell
{ const p = (doc, at, text, to) => state(make(doc, at).update({changes: {from: at, to: to == null ? at : to, insert: text}, selection: {anchor: at + text.length}, userEvent: 'input.paste'}).state);
  out.pasteMid = p(BOOK, BOOK.indexOf('Mean') + 2, 'line one\nline two').body;
  out.pastePdf = p(BOOK, BOOK.indexOf('Mean') + 4, ' (see the\nappendix)').body;
  const E = Wd('\\begin{tabular}{ll}\n& \\\\\nc & d \\\\\n\\end{tabular}');
  out.pasteExcel = p(E, E.indexOf('& \\\\'), '"multi\nline"\tb').body;
  out.pasteColumn = p(E, E.indexOf('& \\\\'), 'one\ntwo').body;
  out.pasteWhole = p(BOOK, BOOK.indexOf('Mean'), 'M1\nM2', BOOK.indexOf('Mean') + 4).body; }
// 3. a special character typed before a formula, a style, \verb or a citation, then a letter: the letter after the character
{ const typed = (doc, at) => { let st = make(doc, at); st = typeAt(st, '%'); st = typeAt(st, 'Z'); return st.doc.toString(); };
  const F1 = Wd('\\begin{tabular}{ll}\n$x_1$ & \\textbf{Total} \\\\\n\\verb|&| & \\cite{k} \\\\\n\\end{tabular}');
  out.beforeMath = typed(F1, F1.indexOf('$x_1$')); out.beforeBold = typed(F1, F1.indexOf('\\textbf')); out.beforeVerb = typed(F1, F1.indexOf('\\verb'));
  out.beforeCite = typed(F1, F1.indexOf('\\cite'));
  const R = '\\documentclass{article}\n\\begin{document}\nText $x$ and \\textbf{bold}.\n\\end{document}\n';
  out.running = typed(R, R.indexOf('$x$')); out.runningBold = typed(R, R.indexOf('\\textbf')); }
// 4. ⇧Return at a table's first place, its first row after a rule line
{ const g = gridsIn(HLINE)[0], st = make(HLINE, g.from), r = runKey(st, 'Shift-Enter');
  out.sretStart = {same: r.st.doc.toString() === HLINE, said: said[said.length - 1]};
  const B2 = Wd('\\begin{table}\n\\centering\n\\begin{tabular}{ll}\n\\toprule\na & b \\\\\nc & d \\\\\n\\bottomrule\n\\end{tabular}\n\\caption{Below}\n\\end{table}');
  const g2 = gridsIn(B2)[0]; out.sretStart2 = gridsIn(B2).length && runKey(make(B2, g2.from), 'Shift-Enter').st.doc.toString() === B2; }
// 5. "[" typed into an empty first cell on a rule's line
{ const D = Wd('\\begin{tabular}{ll}\n\\toprule& b \\\\\n\\midrule & d \\\\\n\\bottomrule\n\\end{tabular}'), g = gridsIn(D)[0];
  out.bracket = [0, 1].map((r) => { const st = typeAt(make(D, g.rows[r].cells[0][0]), '[1]'); return st.doc.toString().split('\n').filter((l) => l.includes('[1]'))[0]; });
  out.bracketDoc = typeAt(make(D, g.rows[0].cells[0][0]), '[1]').doc.toString(); }
// 6. 删除行 between two rules
{ const del = (doc, r) => { const g = gridsIn(doc)[0], st = make(doc, caretOf(doc, g.rows[r].cells[0])); return st.update(P.gridSpec(st, 'rowDelete')).state.doc.toString(); };
  const H3 = Wd('\\begin{tabular}{|l|c|}\n\\hline\na & b \\\\ \\hline\nc & d \\\\ \\hline\n\\end{tabular}');
  out.delHeader = bodyOf(del(BOOK, 0)); out.delLast = bodyOf(del(H3, 1)); out.delMiddle = bodyOf(del(BOOK, 1)); }
// 7. the arrow keys: from the end of the last cell the next stop is the next line, not the table's end drawn at the same place
{ const g = gridsIn(BOOK)[0], stops = stopsOf(make(BOOK)); out.stopsAtEnd = stops.includes(g.to); out.stopAfter = stops.includes(g.to + 1); }
// 8. colours hidden and kept; Tab over a styled cell and typing keeps its style
{ const C = Wd('\\begin{tabular}{ll}\n\\rowcolor{gray!20}x & \\cellcolor{red}y \\\\\n\\textbf{Total} & z \\\\\n\\end{tabular}');
  out.colours = view(make(C)).text;
  const g = gridsIn(C)[0]; let st = make(C, caretOf(C, g.rows[0].cells[1]));
  st = runKey(st, 'Tab').st; out.tabSel = C.slice(st.selection.main.from, st.selection.main.to);
  const s2 = make(C, caretOf(C, g.rows[0].cells[1])); let s3 = runKey(s2, 'Tab').st; s3 = typeAt(s3, 'Sum');
  out.overBold = s3.doc.toString().includes('\\textbf{Sum} & z');
  const s4 = runKey(make(C, caretOf(C, g.rows[0].cells[0])), 'Tab').st; out.firstSel = C.slice(s4.selection.main.from, s4.selection.main.to);
  const s5 = runKey(make(C, caretOf(C, g.rows[1].cells[1])), 'Shift-Tab').st; out.prevSel = C.slice(s5.selection.main.from, s5.selection.main.to);
  out.rowTyped = typeAt(make(C, caretOf(C, g.rows[0].cells[0])), 'Q').doc.toString().includes('\\rowcolor{gray!20}Qx'); }
// 9. a longtable: its caption as the PDF prints it, else its cells' words
{ const L = Wd('\\begin{longtable}{ll}\n\\caption{Long one}\\label{lt}\\\\\na & b \\\\\n\\end{longtable}\n\n\\begin{longtable}{ll}\nAlpha & Beta \\\\\n\\end{longtable}');
  out.long = view(make(L)).text; }
// 10. a wrapper's arguments are counted: a group right after it is a group of its own, its words editable (the fuzz)
{ const U = '\\documentclass{ctexart}\n\\usepackage{xeCJKfntef}\n\\begin{document}\n\\CJKunderline{甲乙}{\\heiti 丙丁}。\n\\end{document}\n';
  out.wrapper = view(make(U)).raw; }
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def tables() -> dict:
    return _node(_TABLES)


@needs_node
def test_r1_a_first_cell_emptied_keeps_the_cursor_in_it_and_return_shift_return_and_tab_keep_the_grid(tables):
    """Found: Backspace after "x" in `    x & 1 & 2 \\\\` left the cursor in the hidden indent; Return then broke the row over
    two lines (the table one unit again), ⇧Return added an empty printed row, Tab left the editor."""
    t = tables
    assert t["emptied"]["grids"] == 1 and t["emptied"]["hidden"] is False and t["emptied"]["cell"] == [1, 0]   # was: hidden
    assert t["ret"]["grids"] == 1 and t["ret"]["cell"] == [2, 0] and t["ret"]["body"] == t["emptied"]["body"]  # was: 0 grids
    assert t["sret"]["grids"] == 1 and t["sret"]["body"] == t["emptied"]["body"] and t["sret"]["said"] == "表格的格子里不能换行。"
    assert t["tab"]["handled"] is True and t["tab"]["cell"] == [1, 1]                                          # was: not handled
    assert t["typed"]["grids"] == 1 and "     Z & 1 & 2 \\\\" in t["typed"]["body"]
    assert t["hline"]["grids"] == 1 and t["one"]["grids"] == 1                                                  # an hline table, a 1-by-1
    assert t["cut"]["grids"] == 1 and t["cut"]["hidden"] is False and t["cutRet"]["grids"] == 1                 # a deletion across cells


@needs_node
def test_r2_lines_pasted_into_a_cells_words_are_its_words_and_a_spreadsheets_quoted_value_is_one_cell(tables):
    """Found: "line one\\nline two" pasted inside "Mean" overwrote "Mean" and the "1" under it; a sentence copied from a PDF
    was split over two cells; Excel's quoted value was two rows, quotes and all."""
    t = tables
    assert "Var & Meline one line twoan & SD \\\\\n    \\midrule\n    x & 1 & 2" in t["pasteMid"]          # was: Var & line one & SD / x & line two
    assert "Var & Mean (see the appendix) & SD" in t["pastePdf"]
    assert t["pasteExcel"].startswith("\\begin{tabular}{ll}\nmulti line & b \\\\\nc & d")          # was: "multi / line" in two rows
    assert t["pasteColumn"].startswith("\\begin{tabular}{ll}\none & \\\\\ntwo & d")                 # into an empty cell: down the column
    assert "Var & M1 & SD \\\\\n    \\midrule\n    x & M2 & 2" in t["pasteWhole"]                  # over a cell's words whole: spread


@needs_node
def test_r3_words_typed_between_two_drawn_pieces_stay_there(tables):
    """Found: "%" typed before a formula, then "Z", gave `\\%$x_1$Z` (the page: %x1Z); before \\textbf{Total} the Z went into
    the bold. Between an escape's character and a formula, a chip, \\verb or a style's edge is a place where words go."""
    t = tables
    assert "\\%Z$x_1$ &" in t["beforeMath"]                       # was: \%$x_1$Z
    assert "& \\%Z\\textbf{Total}" in t["beforeBold"]             # was: \%\textbf{ZTotal}
    assert "\\%Z\\verb|&| &" in t["beforeVerb"] and "& \\%Z\\cite{k}" in t["beforeCite"]
    assert "Text \\%Z$x$ and" in t["running"] and "and \\%Z\\textbf{bold}" in t["runningBold"]


@needs_node
def test_r4_shift_return_at_a_tables_first_place_is_refused(tables):
    """Found: ⇧Return at the start of a table whose first row follows \\hline or \\toprule wrote `\\\\` on a line of its own
    (an empty printed row, the grid lost)."""
    assert tables["sretStart"] == {"same": True, "said": "表格的格子里不能换行。"}
    assert tables["sretStart2"] is True


@needs_node
def test_r5_a_bracket_typed_after_a_rules_name_never_becomes_its_option(tables):
    """Found: "[1]" typed into the empty first cell of `\\toprule& b` wrote `\\toprule [1]`, which stops pdfLaTeX ("Illegal unit
    of measure")."""
    assert tables["bracket"][0] == "\\toprule{}[1] & b \\\\"     # was: \toprule [1] & b
    assert tables["bracket"][1] == "\\midrule {}[1] & d \\\\"


@needs_node
def test_r6_a_row_deleted_between_two_rules_takes_one_so_no_double_line_prints(tables):
    """Found: 删除行 on the header left `\\toprule\\n\\midrule`; on a last row written `c & d \\\\ \\hline` it left two \\hline."""
    assert tables["delHeader"] == "\\begin{tabular}{lcr}\n    \\toprule\n    x & 1 & 2 \\\\\n    y & 3 & 4 \\\\\n    \\bottomrule\n  \\end{tabular}"
    assert tables["delLast"] == "\\begin{tabular}{|l|c|}\n\\hline\na & b \\\\\n \\hline\n\\end{tabular}"   # was: a & b \\ \hline / \hline
    assert "\\midrule\n    y & 3 & 4" in tables["delMiddle"]                                           # one rule only: kept


@needs_node
def test_r7_the_arrow_keys_pass_from_the_last_cell_to_the_next_line(tables):
    """Found: → from the end of the last cell stopped at the table's end, drawn at the same place (a key press that showed
    no move). Two tables with no blank line between: ↓ from the first lands in the second's first cell and Return there
    makes the paragraph between them, as Word does with a table after a table (kept, DESIGN 22.12 R)."""
    assert tables["stopsAtEnd"] is False and tables["stopAfter"] is True


@needs_node
def test_r8_colour_commands_are_hidden_and_a_cells_style_is_kept_when_its_words_are_typed_over(tables):
    """Found: `\\rowcolor{gray!20}x` and `\\cellcolor{red}y` showed "gray!20x" and "redy"; Tab over a bold cell and typing
    dropped its \\textbf."""
    assert "gray" not in tables["colours"] and "red" not in tables["colours"] and "x  y" in tables["colours"]
    assert tables["firstSel"] == "y" and tables["prevSel"] == "Total" and tables["overBold"] is True   # was: \textbf{Total} selected whole
    assert tables["rowTyped"] is True                               # words never typed before \rowcolor (it must start its row)


@needs_node
def test_r9_a_longtable_says_its_caption_as_printed_or_its_cells_words(tables):
    """Found: a longtable was 「LaTeX 内容」, none of its words shown."""
    assert "Table 1: Long one" in tables["long"] and "Table 2: Alpha Beta" in tables["long"]


@needs_tex
def test_r5_the_bracket_typed_after_a_rule_compiles(tables, tmp_path):
    """The table of R5 with "[1]" typed in its first cell compiles with pdfLaTeX (was: "Illegal unit of measure")."""
    text = _compile(tmp_path / "bracket", {"main.tex": tables["bracketDoc"]}, "main.tex", "pdflatex", tmp_path / "home")
    assert "[1] b" in text


# -- the edit view against the PDF -------------------------------------------------------------------------------------

_DOCS = {
    "book": "\\documentclass{book}\n\\usepackage{graphicx}\n\\begin{document}\n\\frontmatter\n\\chapter{Preface}\n\\mainmatter\n\\chapter{Intro}\\label{c1}\n"
            "\\section{Sone}\\label{s1}\n\\begin{figure}[h]\\centering\\rule{1cm}{1cm}\\caption{Fa}\\label{fa}\\end{figure}\n"
            "RefA \\ref{c1} RefB \\ref{s1} RefC \\ref{fa}\n\\backmatter\n\\chapter{Afterword}\n\\end{document}\n",
    "ctexbook": "\\documentclass{ctexbook}\n\\begin{document}\n\\frontmatter\n\\chapter{序}\n\\mainmatter\n\\chapter{引言}\\label{c1}\n\\section{一节}\n"
                "RefA \\ref{c1}\n\\backmatter\n\\chapter{后记}\n\\end{document}\n",
    "floats": "\\documentclass{article}\n\\usepackage{graphicx,subcaption,longtable,rotating,wrapfig,capt-of}\n\\begin{document}\n"
              "\\begin{figure}[h]\n\\begin{subfigure}{.4\\linewidth}\\rule{1cm}{1cm}\\caption{SubA}\\end{subfigure}\n"
              "\\begin{subfigure}{.4\\linewidth}\\rule{1cm}{1cm}\\caption{SubB}\\end{subfigure}\n\\caption{FigMain}\\label{fm}\n\\end{figure}\n"
              "\\begin{figure}[h]\n\\centering\n\\rule{1cm}{1cm}\n\\caption{FigTwo}\\label{ft}\n\\end{figure}\n"
              "\\begin{longtable}{ll}\n\\caption{LongCap}\\label{lt}\\\\\na & b \\\\\n\\end{longtable}\n"
              "\\begin{table}[h]\n\\centering\n\\caption{TabTwo}\\label{t2}\n\\begin{tabular}{l}\nx \\\\\n\\end{tabular}\n\\end{table}\n"
              "\\begin{sidewaystable}\n\\centering\n\\caption{Side}\\label{st}\n\\begin{tabular}{l}\nx \\\\\n\\end{tabular}\n\\end{sidewaystable}\n"
              "\\begin{table}[h]\n\\centering\n\\caption{TabFour}\\label{t4}\n\\begin{tabular}{l}\nx \\\\\n\\end{tabular}\n\\end{table}\n"
              "\\noindent\\begin{minipage}{3cm}\\rule{1cm}{1cm}\\captionof{figure}{Minip}\\label{mp}\\end{minipage}\n\n"
              "\\begin{wrapfigure}{r}{3cm}\\rule{1cm}{1cm}\\caption{Wrapped}\\label{wf}\\end{wrapfigure}\nWords beside.\n\n"
              "\\begin{figure}[h]\n\\centering\n\\rule{1cm}{1cm}\n\\caption{FigLast}\\label{fl}\n\\end{figure}\n"
              "RefA \\ref{fm} RefB \\ref{ft} RefC \\ref{lt} RefD \\ref{t2} RefE \\ref{st} RefF \\ref{t4} RefG \\ref{mp} RefH \\ref{wf} RefI \\ref{fl}\n\\end{document}\n",
    "counters": "\\documentclass{article}\n\\begin{document}\n\\section{Sa}\\setcounter{section}{4}\\section{Sb}\\label{sb}\\subsection{Sc}\\addtocounter{figure}{2}\n"
                "\\begin{figure}[h]\\centering\\rule{1cm}{1cm}\\caption{Fc}\\label{fc}\\end{figure}\nRefA \\ref{sb} RefB \\ref{fc}\n\\end{document}\n",
    "appendices": "\\documentclass{article}\n\\usepackage{appendix}\n\\begin{document}\n\\section{Sa}\n\\section{Sb}\n\\begin{appendices}\n\\section{ApA}\\label{aa}\n"
                  "\\subsection{ApAs}\\label{aas}\nRefA \\ref{aa} RefB \\ref{aas}\n\\end{appendices}\n\\end{document}\n",
    "notes": "\\documentclass{article}\n\\begin{document}\nA\\footnotemark{} B\\footnote{Nb} C\\footnote[7]{N7} D\\footnote{Nd}\n\n"
             "\\begin{minipage}{3cm}E\\footnote{Ne}\\end{minipage}\n\nF\\footnote{Nf}\n\\end{document}\n",
    "plain": "\\documentclass[scheme=plain]{ctexart}\n\\renewcommand*{\\tablename}{Tab.}\n\\begin{document}\n\\begin{abstract}x\\end{abstract}\n"
             "\\begin{figure}[h]\\centering x\\caption{Pf}\\end{figure}\n\\begin{table}[h]\\centering\\caption{Pt}\\begin{tabular}{l}\nx \\\\\n\\end{tabular}\\end{table}\n\\end{document}\n",
    "babel": "\\documentclass{article}\n\\usepackage[english]{babel}\n\\usepackage[labelsep=period]{caption}\n\\usepackage{hyperref}\n"
             "\\renewcommand{\\figurename}{Fig.}\n\\begin{document}\n\\section{S}\\label{s}\n\\subsection{SS}\\label{ss}\n"
             "\\begin{figure}[h]\\centering x\\caption{C}\\label{f}\\end{figure}\nRefA \\autoref{f} RefB \\autoref{s} RefC \\autoref{ss}\n\\end{document}\n",
    "capset": "\\documentclass{article}\n\\usepackage{caption}\n\\captionsetup[figure]{name=Fig.}\n\\begin{document}\n"
              "\\begin{figure}[h]\\centering x\\caption{Cs}\\end{figure}\n\\end{document}\n",
    "reportctex": "\\documentclass{report}\n\\usepackage[UTF8]{ctex}\n\\begin{document}\n\\chapter{One}\n\\section{Sec}\n"
                  "\\begin{figure}[h]\\centering x\\caption{Cap}\\end{figure}\n\\appendix\n\\chapter{App}\n\\end{document}\n",
    "autoctex": "\\documentclass{ctexart}\n\\usepackage{hyperref}\n\\begin{document}\n\\section{S}\\label{s}\n"
                "\\begin{figure}[h]\\centering x\\caption{C}\\label{f}\\end{figure}\nRefA \\autoref{f} RefB \\autoref{s}\n\\appendix\n\\section{Ap}\\label{a}\nRefC \\autoref{a}\n\\end{document}\n",
}
_GERMAN = ("\\documentclass{article}\n\\usepackage[ngerman]{babel}\n\\begin{document}\n\\begin{abstract}x\\end{abstract}\n"
           "\\begin{figure}[h]\\centering x\\caption{Gc}\\end{figure}\n\\end{document}\n")


@pytest.fixture(scope="module")
def numbered() -> dict:
    return _view([{"name": n, "text": t} for n, t in list(_DOCS.items()) + [("german", _GERMAN)]])


def _nums(shown: dict) -> list:
    return [h["number"] for h in shown["heads"]]


@needs_node
def test_r10_chapters_outside_the_main_matter_take_no_number(numbered):
    """Found: book with \\frontmatter, \\mainmatter and \\backmatter showed Chapter 1, 2, 3, 2.1, Figure 2.1 and \\ref 2 /
    2.1 / 2.1 where the PDF prints none, Chapter 1, none, 1.1, Figure 1.1, 1 / 1.1 / 1.1."""
    b = numbered["book"]
    assert _nums(b) == [None, "Chapter 1", "1.1", None] and b["caps"] == ["Figure 1.1: Fa"] and b["refs"] == ["1", "1.1", "1.1"]
    assert _nums(numbered["ctexbook"]) == [None, "第一章", "1.1", None] and numbered["ctexbook"]["refs"] == ["一"]


@needs_node
def test_r11_every_float_form_steps_its_counter_as_tex_does(numbered):
    """Found: subfigure captions counted as figures; a longtable, a sidewaystable, \\captionof and a wrapfigure not counted."""
    f = numbered["floats"]
    assert f["refs"] == ["1", "2", "1", "2", "3", "4", "3", "4", "5"]
    assert "Figure 2: FigTwo" in f["caps"] and "Figure 5: FigLast" in f["caps"] and "Table 4: TabFour" in f["caps"]


@needs_node
def test_r12_counters_set_by_hand_are_followed(numbered):
    """Found: after \\setcounter{section}{4} and \\addtocounter{figure}{2} the page showed 2, 2.1, Figure 1 (the PDF 5, 5.1,
    Figure 3)."""
    c = numbered["counters"]
    assert _nums(c) == ["1", "5", "5.1"] and c["caps"] == ["Figure 3: Fc"] and c["refs"] == ["5", "3"]


@needs_node
def test_r13_the_appendix_packages_appendices_number_as_appendix_does(numbered):
    """Found: \\begin{appendices} showed 3, 3.1 and \\ref 3 (the PDF: A, A.1, A)."""
    a = numbered["appendices"]
    assert _nums(a) == ["1", "2", "A", "A.1"] and a["refs"] == ["A", "A.1"]


@needs_node
def test_r14_footnote_marks_given_numbers_and_minipage_notes_as_printed(numbered):
    """Found: \\footnotemark did not step the counter; \\footnote[7] showed its count; a minipage's footnote was counted."""
    assert numbered["notes"]["notes"] == ["2", "7", "3", "注", "4"]   # 注: a minipage's own (a), not counted here


@needs_node
def test_r15_the_documents_names_are_read_where_they_are_set_and_unknown_ones_are_not_the_interfaces(numbered):
    """Found: scheme=plain showed 图/表/摘要; \\renewcommand* was not read; babel's reset of a preamble name was not; the
    caption package's labelsep and name= were not; a German paper showed 图/表 1/摘要; report with the ctex package
    第一章; \\autoref 图 1 / 第 1 节."""
    p = numbered["plain"]
    assert p["envHeads"] == ["Abstract"] and p["caps"][0].startswith("Figure 1: ") and p["caps"][1].startswith("Tab. 1: ")
    b = numbered["babel"]
    assert b["caps"] == ["Figure 1. C"] and b["refs"] == ["Figure 1", "section 1", "subsection 1.1"]
    assert numbered["capset"]["caps"] == ["Fig. 1: Cs"]
    g = numbered["german"]
    assert g["envHeads"] == [] and g["caps"] == ["Gc"]                                 # no word, never 摘要 or 图
    assert _nums(numbered["reportctex"]) == ["Chapter 1", "1.1", "附录 A"] and numbered["reportctex"]["caps"] == ["图 1.1: Cap"]
    assert numbered["autoctex"]["refs"] == ["Figure 1", "section 1", "Appendix A"]


@needs_node
@needs_tex
def test_r10_to_r15_what_the_page_shows_is_what_the_pdf_prints(numbered, tmp_path):
    """Each document above compiled (pdfLaTeX, ctex ones by XeLaTeX) and read back: the numbers, captions and references the
    page shows are on the PDF's lines."""
    for name, text in _DOCS.items():
        engine = "xelatex" if "ctex" in text else "pdflatex"
        lines = [ln.strip() for ln in _compile(tmp_path / name, {"main.tex": text}, "main.tex", engine, tmp_path / "home").splitlines() if ln.strip()]
        joined = " ".join(lines)
        s = numbered[name]
        for h in s["heads"]:
            # (pdftotext runs a page's number and its running head into the line after them)
            if h["number"] is None:                                       # its title with no number before it
                assert any(ln.endswith(h["title"]) for ln in lines), (name, h)
                assert not any(re.search(r"(?:\d|[一二三四五六七八九十]章?)\s*" + re.escape(h["title"]) + "$", ln) for ln in lines), (name, h)
            elif re.match(r"^(?:Chapter|Appendix|附录) |^第", h["number"]):
                k = next(i for i, ln in enumerate(lines) if ln.endswith(h["number"]) or ln.endswith(h["number"] + " " + h["title"]))
                assert lines[k].endswith(h["title"]) or lines[k + 1] == h["title"], (name, h)
            else:
                assert h["number"] + " " + h["title"] in lines, (name, h)
        for cap in s["caps"]:
            assert any(cap in ln for ln in lines), (name, cap)
        printed = re.findall(r"\bRef([A-I]) ((?:Figure|Table|section|subsection|Appendix) )?([^\sR]+)", joined)
        assert [((w or "") + v) for _k, w, v in printed] == s["refs"], (name, printed, s["refs"])


def test_r16_iffalse_before_a_chapter_files_input_leaves_its_numbers_unknown():
    """Found: \\iffalse … \\fi in the main file before \\input{chap} was counted (the page 3 and Figure 2, the PDF 2 and
    Figure 1). What the engine gives the page is none then: no numbers rather than wrong ones."""
    files = {"main.tex": "\\documentclass{article}\n\\begin{document}\n\\section{A}\\iffalse\\section{Old}\\begin{figure}\\caption{Old}\\end{figure}\\fi\n"
                         "\\input{chap}\n\\end{document}\n", "chap.tex": "\\section{ChapS}\n"}
    assert latex_edit.text_before("main.tex", "chap.tex", lambda p: files.get(p, "")) is None                    # was: counted
    files["main.tex"] = files["main.tex"].replace("\\iffalse\\section{Old}\\begin{figure}\\caption{Old}\\end{figure}\\fi", "")
    assert latex_edit.text_before("main.tex", "chap.tex", lambda p: files.get(p, "")) == "\n\\section{A}\n"


# -- paragraphs and TeX's own spacing ----------------------------------------------------------------------------------

_RFIG = "\\begin{figure}[h]\n\\centering\n\\rule{1cm}{2mm}\n\\caption{Cap}\n\\end{figure}"
_RCORPUS = [
    "Qa x.\n\\vskip 1em\nQb y.",
    "Qc x.\n\\vfill\nQd y.",
    "Qe x.\n\\hrule\nQf y.",
    "Qg x. \\vskip 2pt plus 1pt Qh y.",
    "Qi x.\n\n" + _RFIG + " Qj y.",
    "Qk x.\n" + _RFIG + "Ql y.",
    "Qm x.\n\\par\\noindent Qn y.",
    "Qo x.\n\n\\noindent\nQp y.",
    "Qq x.\n\n\\noindent\n\nQr y.",
    "\\subsection{SubH} Qs y.\n\nQt z.",
    "\\subsection{SubI}\\label{si} Qu y.\nQv z.",
    "Qw x. \\kern 3pt Qx y. \\penalty10000 Qy z.",   # (\\hskip's gap would split pdftotext's line: in R17 only)
]
_RPRE = ("\\documentclass{article}\n\\usepackage{indentfirst}\n\\setlength{\\parindent}{4em}\n\\setlength{\\parskip}{0pt}\n"
         "\\usepackage{graphicx}\n\\pagestyle{empty}\n")
_RPARAS = r"""
const DOC = __DOC__;
const st = CM.EditorState.create({doc: DOC, extensions: [P.wordExtensions(CM, {})]});
const v = st.field(F.field), cls = new Map(), atoms = [], marks = [];
v.deco.between(0, DOC.length, (a, b, d) => { if (a === b && d.spec.class) cls.set(a, (cls.get(a) || '') + ' ' + d.spec.class); else if (d.spec.class) marks.push([a, b, d.spec.class]); if (d.spec.atom) atoms.push([a, b, d.spec]); });
const at = (p) => atoms.find((x) => x[0] <= p && p < x[1]);
const res = {}, re = /Q[a-z]+/g, body = DOC.indexOf('\\begin{document}');
for (let m = re.exec(DOC); m; m = re.exec(DOC)) {
  if (m.index < body) continue;
  let L = st.doc.lineAt(m.index);
  while (L.number > 1 && at(L.from - 1)) L = st.doc.line(L.number - 1);
  let shown = false;
  for (let p = L.from; p < m.index && !shown; p++) { const x = at(p); shown = x ? ['space', 'text', 'parbreak'].includes(x[2].kind) || (x[2].kind === 'close' && x[2].widget && x[2].widget.plain === '\n') : /\S/.test(DOC[p]); }
  let start = /latex-para(?!-first)|latex-para-first/.test(cls.get(L.from) || '') && !shown;
  // a paragraph break drawn right before it on its line: \par (or \vskip …) between words, a heading's words after it
  const pb = atoms.find((x) => (x[2].kind === 'parbreak' || (x[2].kind === 'close' && x[2].widget && x[2].widget.plain === '\n')) && x[1] <= m.index && !DOC.slice(x[1], m.index).replace(/\\label\{[^}]*\}/g, '').trim());
  if (pb) start = true;
  // words after a figure on its line: a line of their own, indented only when they start a paragraph
  const mk = marks.find((x) => /latex-after-float/.test(x[2]) && x[0] <= m.index && m.index < x[1]);
  if (mk) start = mk[0] === m.index && /latex-after-float-(?:para|first)/.test(mk[2]);
  res[m[0]] = start;
}
out.res = res;
// what is left shown of TeX's spacing (outside every replace)
const raw = (doc) => { const s = CM.EditorState.create({doc, extensions: [P.wordExtensions(CM, {})]}), f = s.field(F.field); const end = doc.indexOf('\\end{document}'); let t = '', a = doc.indexOf('\\begin{document}') + 16; const xs = [];
  f.deco.between(0, doc.length, (x, y, d) => { if (d.spec.atom) xs.push([x, y]); }); xs.sort((p, q) => p[0] - q[0]);
  xs.forEach(([x, y]) => { if (y <= a || x >= end) return; t += doc.slice(a, Math.max(a, x)); a = y; }); return t + doc.slice(a, Math.max(a, end)); };
const W = (b) => '\\documentclass{article}\n\\begin{document}\n' + b + '\n\\end{document}\n';
out.raw = ['One.\n\\vskip 1em\nTwo.', 'a \\hskip 2em b', 'a\\kern 1pt b', 'c\n\\vskip 1em plus 2pt\nd', 'e \\penalty10000 f', '\\parindent=0pt\ng', 'h\\vskip\\baselineskip i'].map((b) => raw(W(b)));
console.log(JSON.stringify(out));
"""


@needs_node
def test_r17_teXs_own_spacing_shows_no_length(numbered):
    """Found: "\\vskip 1em" left "1em" on the page, "\\hskip 2em" "2em", "\\kern 1pt" "1pt", "\\penalty10000" "10000",
    "\\parindent=0pt" "=0pt" -- markup in the edit view."""
    doc = _RPRE + "\\begin{document}\nX\n\\end{document}\n"
    got = _node(_RPARAS.replace("__DOC__", json.dumps(doc)))["raw"]
    assert len(got) == 7
    for shown in got:
        assert re.search(r"[A-Za-z]", shown) and not re.search(r"\d|pt|em\b|plus|=|\\", shown), shown


@needs_node
@needs_tex
def test_r18_paragraphs_round_teXs_spacing_headings_figures_and_noindent_are_teXs(tmp_path):
    """Found: \\vskip, \\vfill and \\hrule did not end the paragraph; words after \\section{…} on its line were drawn in the
    heading's line and the paragraph after them as the heading's first; \\noindent on a line of its own or after \\par
    still indented; words after \\end{figure} on its line were drawn in the centred caption line. Compiled with every
    paragraph indented 4em (indentfirst) and read back with pdftotext's word positions: each line of words starts a
    paragraph on the page exactly where it starts one in the PDF."""
    doc = _RPRE + "\\begin{document}\n" + "\n\n".join(_RCORPUS) + "\n\\end{document}\n"
    html = _compile(tmp_path / "paras", {"p.tex": doc}, "p.tex", "pdflatex", tmp_path / "home", layout="-bbox-layout")
    starts, firsts = {}, []
    for line in re.findall(r"<line[^>]*>(.*?)</line>", html, re.S):
        words = re.findall(r'<word xMin="([\d.]+)"[^>]*>(.*?)</word>', line)
        if words:
            firsts.append(float(words[0][0]))
        for k, (x, w) in enumerate(words):
            w = w.rstrip(".")
            if re.fullmatch(r"Q[a-z]+", w):
                starts[w] = (k == 0, float(x))
    margin = min(firsts)
    tex = {t: f and x > margin + 20 for t, (f, x) in starts.items()}
    page = _node(_RPARAS.replace("__DOC__", json.dumps(doc)))["res"]
    assert set(tex) == set(page) and len(tex) == 25
    assert sorted((t, tex[t], page[t]) for t in tex if tex[t] != page[t]) == []
    # the corpus says what TeX does: \vskip, \vfill and \hrule end the paragraph; \noindent's paragraph is not indented
    assert tex["Qb"] and tex["Qd"] and tex["Qf"] and tex["Qh"] and not tex["Qn"] and not tex["Qp"] and tex["Qr"] and tex["Qj"] and not tex["Ql"]
    assert tex["Qs"] and tex["Qt"] and tex["Qu"] and not tex["Qv"]


# -- hand-written 段间距 and 行距 on the page --------------------------------------------------------------------------

@needs_node
def test_r19_hand_written_paragraph_and_line_spacing_are_drawn():
    """Found: \\setlength{\\parskip}{1em}, {6pt} and {0.5\\baselineskip plus 2pt} were drawn as no gap, and
    \\renewcommand{\\baselinestretch}{1.5} as single spacing."""
    got = _page(_LAYOUT_PANEL[:_LAYOUT_PANEL.index("const MAIN_PRE")] + r"""
const look = async (pre) => {
  const MAIN = '\\documentclass{article}\n' + pre + '\\begin{document}\nOne.\n\nTwo.\n\\end{document}\n';
  await openFile('paper/main.tex', MAIN, {mains: ['paper/main.tex'], format: {main: 'paper/main.tex', engine: 'pdflatex', folder: 'paper', preamble: null, local: []}});
  const box = byClass('latex-editor'); box.style = {props: {}, setProperty(k, v) { this.props[k] = v; }};
  tab('版式').fire('click');
  return [box.style.props['--latex-parskip'], box.style.props['--latex-stretch']];
};
out.em = await look('\\setlength{\\parskip}{1em}\n');
out.pt = await look('\\setlength{\\parskip}{6pt}\n');
out.half = await look('\\setlength{\\parskip}{0.5\\baselineskip plus 2pt}\n');
out.stretch = await look('\\renewcommand{\\baselinestretch}{1.5}\n');
out.none = await look('');
console.log(JSON.stringify(out));
""")
    assert got["em"][0] == "calc(1.5em * var(--latex-stretch, 1))"          # 1em of a 10pt body: 10/12 of a line (was: 0)
    assert got["pt"][0] == "calc(0.9em * var(--latex-stretch, 1))"          # 6pt: half a line
    assert got["half"][0] == "calc(0.9em * var(--latex-stretch, 1))"
    assert got["stretch"] == ["0", "1.5"] and got["none"] == ["0", "1"]       # was: 1


# -- the engine: thumbnails and the image picker -------------------------------------------------------------------------

def _thumb_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    (root / "figures").mkdir()
    (root / "figures" / "dense.pdf").write_bytes(b"%PDF-1.4\n% a figure no converter draws in time\n")
    return root


def test_r20_a_figure_that_cannot_be_drawn_is_remembered_and_the_converters_are_capped(tmp_path, monkeypatch):
    """Found: a dense plot took 20 s on every request (pdftoppm 10 s, then sips 10 s) and nothing was remembered. Now the
    converters together run at most THUMB_SECONDS, none after one ran out of time, and the failure is kept (`.fail`)."""
    from researchos import paths

    root, ran = _thumb_project(tmp_path), []
    marker = tmp_path / "second-ran"
    monkeypatch.setattr(latex_edit, "THUMB_SECONDS", 1)
    monkeypatch.setattr(latex_edit, "_thumb_commands", lambda source, out: (ran.append(1), [["/bin/sleep", "5"], ["/usr/bin/touch", str(marker)]])[1])
    start = time.monotonic()
    with pytest.raises(latex_edit.WorkspaceError) as first:
        latex_edit.figure_thumbnail(root, "figures/dense.pdf", "pid")
    assert first.value.code == "no_thumbnail" and time.monotonic() - start < 3 and not marker.exists()   # was: the next converter ran
    assert list((paths.researchos_home() / "latex-thumbs" / "pid").glob("*.fail"))
    start = time.monotonic()
    with pytest.raises(latex_edit.WorkspaceError):
        latex_edit.figure_thumbnail(root, "figures/dense.pdf", "pid")
    assert time.monotonic() - start < 0.5 and len(ran) == 1                                        # remembered: no converter again
    os.utime(root / "figures" / "dense.pdf", (1, 1))                                                # changed: tried again
    with pytest.raises(latex_edit.WorkspaceError):
        latex_edit.figure_thumbnail(root, "figures/dense.pdf", "pid")
    assert len(ran) == 2


def test_r20_the_thumbnail_cache_is_pruned_oldest_first(tmp_path, monkeypatch):
    from researchos import paths

    folder = paths.researchos_home() / "latex-thumbs" / "prune"
    folder.mkdir(parents=True)
    for k in range(6):
        (folder / f"old{k}.png").write_bytes(b"x" * 10)
        os.utime(folder / f"old{k}.png", (1000 + k, 1000 + k))
    monkeypatch.setattr(latex_edit, "THUMB_CACHE_FILES", 4)
    keep = folder / "new.png"
    keep.write_bytes(b"y")
    latex_edit._prune_thumbs(folder, keep)
    assert sorted(p.name for p in folder.iterdir()) == ["new.png", "old3.png", "old4.png", "old5.png"]


def test_r21_the_picker_leaves_out_a_documents_pdf_in_its_build_folder(tmp_path):
    """Found: paper/build/english.pdf and paper/out/main.pdf (latexmk's -outdir, VS Code) were listed as images."""
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    for rel, data in (("paper/english.tex", latex_edit.TEMPLATES["en_paper"].replace("<TITLE>", "E")), ("paper/main.tex", "\\documentclass{article}\n")):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(data, encoding="utf-8")
    for rel in ("paper/build/english.pdf", "paper/out/main.pdf", "paper/build/fig.pdf", "figures/a.pdf"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"%PDF-1.4\n")
    listed = [x["path"] for x in latex_edit.insertables(root, "paper/english.tex", "images")["images"]]
    assert listed == ["figures/a.pdf", "paper/build/fig.pdf"], listed                              # was: + both build PDFs


def test_r22_the_thumbnail_route_lets_the_page_keep_a_picture_a_minute():
    """Found: the route answered no-store, so every redraw and page open drew the figure again."""
    server = (WEBAPP / "server.py").read_text(encoding="utf-8")
    route = server[server.index('elif path == "/api/latex/thumb":'):server.index('elif path == "/api/workspace/exists":')]
    assert 'self.send_header("Cache-Control", "private, max-age=60")' in route and "no-store" not in route


# -- the page: looks and words -------------------------------------------------------------------------------------------

def test_r23_a_disabled_switch_looks_disabled_and_focus_is_a_palette_ring():
    """Found: 显示代码, disabled on a file the edit view cannot show, looked exactly as the enabled pressed switch."""
    rule = re.search(r"\.latex-switch:disabled, \.latex-switch:disabled\[aria-pressed=\"true\"\] \{([^}]*)\}", CSS)
    assert rule and "color: var(--gray)" in rule.group(1) and "background: transparent" in rule.group(1) and "border-color: var(--line)" in rule.group(1)
    assert re.search(r"\.latex-side \.btn:focus-visible, \.latex-switch:focus-visible \{ outline: 1px solid var\(--ink\);", CSS)
    assert re.search(r"\.latex-tools \.btn:disabled:not\(\.btn-primary\) \{ color: var\(--gray\); background: transparent; border-color: var\(--line\);", CSS)


@needs_node
def test_r24_codemirrors_marks_of_invisible_characters_are_the_codes_alone():
    """Found: a zero-width space, a soft hyphen and a BOM in the text showed as red bullets titled "Control character" in
    the edit view (CodeMirror's highlightSpecialChars was always on). It is now in the code's own compartment."""
    assert JS.count("highlightSpecialChars") == 1
    body = JS[JS.index("  function codeLook(CM) {"):JS.index("  function highlight(CM) {")]
    assert "CM.highlightSpecialChars()" in body


def test_r25_a_figures_pdf_page_is_drawn_at_the_figures_size():
    """Found: `span.latex-graphic .latex-graphic-thumb { max-height: 4rem }` (0,2,1) beat the figure's 14rem (0,2,0): a
    PDF page drawn 46 by 64 px. And while a picture loads it is unseen (it was a framed 2-pixel dot)."""
    assert re.search(r"\.latex-figure-part span\.latex-graphic \.latex-graphic-thumb \{ max-height: 14rem; \}", CSS)
    assert re.search(r"\.latex-graphic-thumb\.latex-thumb-loading \{ visibility: hidden; border-color: transparent; \}", CSS)
    body = JS[JS.index("  function graphicDOM(path, tag) {"):JS.index("  function captionDOM(word, src) {")]
    assert 'latex-thumb-loading' in body and 'addEventListener("load"' in body


@needs_node
def test_r26_a_wide_table_is_compact_and_never_breaks_a_number_letter_by_letter():
    """Found: a 7-column table at 900 px broke "0.007" over five lines and "R squared" one letter a line. Its longest words
    now set its columns' least share and it is drawn compact; a cell breaks a word only when it cannot hold it."""
    got = _node(_BASE + r"""
const D = W('', '\\begin{tabular}{lllllll}\n\\toprule\nCoefficient & R squared & 0.007 & 0.045678 & Estimate & Std. error & p-value \\\\\n\\midrule\na & b & c & d & e & f & g \\\\\n\\bottomrule\n\\end{tabular}');
const st = make(D), lines = [], marks = [];
st.field(F.field).deco.between(0, D.length, (a, b, d) => { if (a === b && d.spec.class) lines.push([d.spec.class, (d.spec.attributes || {}).style || '']); else if (d.spec.class && /latex-cell/.test(d.spec.class)) marks.push([D.slice(a, b).trim(), (d.spec.attributes || {}).style]); });
out.lines = lines.filter((x) => /latex-trow/.test(x[0])); out.marks = marks.slice(0, 7);
const S = W('', '\\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}'), s2 = make(S), l2 = [];
s2.field(F.field).deco.between(0, S.length, (a, b, d) => { if (a === b && d.spec.class && /latex-trow/.test(d.spec.class)) l2.push(d.spec.class); });
out.small = l2;
console.log(JSON.stringify(out));
""")
    assert all("latex-trow-compact" in c for c, _s in got["lines"]) and not any("compact" in c for c in got["small"])
    share = {w: float(re.search(r"width:([\d.]+)%", s).group(1)) for w, s in got["marks"]}
    assert share["0.045678"] >= 12 and share["Coefficient"] >= 14                      # each at least its longest word's share
    assert re.search(r"\.latex-editor \.cm-content\.latex-word \.latex-cell \{ overflow-wrap: break-word; word-break: normal; \}", CSS)


_GRID_PAGE = _TABLE_PAGE.split("const TEXT =")[0] + r"""
const TEXT = '\\documentclass{article}\n\\usepackage{booktabs}\n\\begin{document}\nSome text.\n\n\\begin{tabular}{l}\nonly \\\\\n\\end{tabular}\n\nAfter.\n\\end{document}\n';
await openFile('a.tex', TEXT, {mains: ['a.tex'], format: {main: 'a.tex', engine: 'pdflatex', folder: '', preamble: null, local: []}});
const box = () => inside(side(), (e) => e.classList.contains('latex-grid-tools'))[0];
const btn = (w) => inside(box(), (e) => e.tag === 'button' && e.textContent === w)[0];
const note = () => inside(side(), (e) => e.classList.contains('latex-format-note'))[0];
view().dispatch({selection: {anchor: TEXT.indexOf('only') + 2}});
out.group = {role: box().attrs.role, labelled: box().attrs['aria-labelledby'], title: inside(box(), (e) => e.attrs && e.attrs.id === box().attrs['aria-labelledby']).map((e) => e.textContent)};
out.header = {disabled: !!btn('第一行是表头').disabled};
btn('第一行是表头').fire('click');
out.said = {text: note().textContent, hidden: hiddenEl(note())};
btn('删除列').fire('click');
out.said2 = note().textContent;
view().dispatch({changes: {from: TEXT.indexOf('only') + 2, insert: 'Z'}, selection: {anchor: TEXT.indexOf('only') + 3}, userEvent: 'input.type'});
out.after = {text: note().textContent, hidden: hiddenEl(note())};
console.log(JSON.stringify(out));
"""


@needs_node
def test_r27_the_header_button_says_why_in_a_one_row_table_a_refusal_goes_when_the_text_moves_on_and_the_group_is_named():
    """Found: 第一行是表头 was a silent gray button in a one-row table (DESIGN says it is refused with a sentence); a refusal
    stayed after typing, beside the next one; the 「表格」 group had no group semantics."""
    if NODE is None:
        pytest.skip("node is not installed")
    from test_v17_latex_editor import _DRIVER

    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")), "latex": json.dumps(str(WEBAPP / "latex.js"))}
    script = prelude + "(async () => {\n" + _GRID_PAGE + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });"
    done = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=120)
    got = json.loads(done.stdout.strip().splitlines()[-1])
    assert "error" not in got, got.get("error")
    assert got["group"] == {"role": "group", "labelled": "latex-grid-title", "title": ["表格"]}
    assert got["header"]["disabled"] is False                                                  # was: True, no sentence
    assert got["said"] == {"text": "表格要有两行以上，才能把第一行设成表头。", "hidden": False}
    assert got["said2"] == "表格至少要有一列。"
    assert got["after"]["hidden"] is True and got["after"]["text"] == ""                       # was: still said after typing


@needs_node
def test_r28_the_image_pickers_cap_says_the_list_is_cut_when_the_engine_cut_it():
    """Found: with more images than the engine lists, the picker said to search for the rest, but its search only looks
    through what the engine sent."""
    body = JS[JS.index("      if (!shown.length) list.appendChild(mk(\"p\", \"node-sub\", t(\"没有找到这样的图片\")));"):]
    body = body[:body.index("};")]
    assert "else if (more)" in body and "项目里的图片太多，这里没有全部列出" in body
    i18n = (WEBAPP / "i18n.js").read_text(encoding="utf-8")
    assert "项目里的图片太多，这里没有全部列出；不在这里的图片可以在「显示代码」里写上路径插入。" in i18n
    assert "项目里的文献太多，这里没有全部列出；不在这里的文献可以在「显示代码」里用它的键引用。" in i18n


@needs_node
def test_r29_a_group_right_after_a_wrapper_is_its_own_and_its_words_stay_editable(tables):
    """Found by the no-code view's fuzz: a deletion that left `\\CJKunderline{a}{\\heiti b}` made the group a second
    argument, the words of both a read-only chip. A wrapper takes as many arguments as it has."""
    assert "甲乙" in tables["wrapper"] and "丙丁" in tables["wrapper"]                 # was: neither, a chip

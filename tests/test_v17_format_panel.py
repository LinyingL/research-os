"""DESIGN.md 22.12 「格式与插入」 (task V17, step A): the panel at the top of the LaTeX editor's right column and every tool in
it -- 加粗 / 斜体 / 下划线 toggled like Word, 字体 and 字号, 标题's levels, 图片 (the path as the export compiles it,
\\graphicspath, graphicx), 表格 (header on and off, booktabs or \\hline), 公式, 列表, 脚注 and 文献引用 -- each the same as
typing its command, run under node on the vendored CodeMirror; the panel's page behaviour (its tabs, pickers, one primary
at a time, ⌘B / ⌘I / ⌘U, the save path and ⌘Z, read-only and 显示源码); the engine's facts and its listing route; and one
real compile of a ctexrep (XeLaTeX) and an article (pdfLaTeX) document with every insertion applied. Every test names
its scenario."""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from researchos import latex_edit, project
from test_v17_latex_editor import _DRIVER, NODE, WEBAPP

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
TEXBIN = Path("/Library/TeX/texbin")
LATEXMK = shutil.which("latexmk") or (str(TEXBIN / "latexmk") if (TEXBIN / "latexmk").exists() else None)
LATEX_JS = WEBAPP / "latex.js"

_PURE = r"""
const vm = require('vm'), fs = require('fs');
const ctx = vm.createContext({});
vm.runInContext(fs.readFileSync(__CM__, 'utf8'), ctx);
global.window = {ROCodeMirror: ctx.ROCodeMirror};
eval(fs.readFileSync(__LATEX__, 'utf8'));
const CM = ctx.ROCodeMirror, P = window.ROLatex._pure;
// '«' and '»' mark a selection, '|' a cursor; the answer is the text with the new selection marked the same way
const state = (marked, sep) => {
  const a = marked.indexOf('«'), text = marked.replace('«', '').replace('»', '').replace('|', '');
  const anchor = a >= 0 ? a : marked.indexOf('|'), head = a >= 0 ? marked.indexOf('»') - 1 : anchor;
  return CM.EditorState.create({doc: sep ? text.split('\n').join(sep) : text, selection: {anchor, head},
    extensions: sep ? [CM.EditorState.lineSeparator.of(sep), P.breakFilter(CM, sep)] : []});
};
const result = (st, r) => {
  if (!r) return null;
  if (r.refused) return {refused: r.refused};
  const tr = st.update({changes: r.changes, selection: r.selection}), n = tr.state, sel = n.selection.main, x = n.doc.toString();
  const shown = sel.empty ? x.slice(0, sel.head) + '|' + x.slice(sel.head) : x.slice(0, sel.from) + '«' + x.slice(sel.from, sel.to) + '»' + x.slice(sel.to);
  return Object.assign({text: shown}, r.note ? {note: r.note} : {}, {file: n.doc.sliceString(0, n.doc.length, n.lineBreak), steps: tr.changes.empty ? 0 : 1});
};
const run = (marked, make, sep) => { const st = state(marked, sep); return result(st, make(st)); };
const out = {};
const S = (name) => (st) => P.styleSpec(st, name);
out.style = {
  wrap: run('a «bold» b', S('textbf')),
  unwrapText: run('a \\textbf{«bold»} b', S('textbf')),
  unwrapWhole: run('a «\\textbf{bold}» b', S('textbf')),
  unwrapAtCursor: run('a \\textit{bo|ld} b', S('textit')),
  emptyInserts: run('a | b', S('underline')),
  otherCommandWraps: run('a \\textbf{«bold»} b', S('textit')),
  nestedWrap: run('\\textbf{a «b» c}', S('textit')),
  acrossBraces: run('a «b} {c» d', S('textbf')),
  acrossArgument: run('a \\emph{«x} y» d', S('textbf')),
  acrossCommandName: run('a \\tex«tbf{x}» d', S('textit')),
  acrossEnvironment: run('«a\n\\begin{quote}\nb»\n\\end{quote}', S('textbf')),
  acrossFormula: run('a $x«$ y» d', S('textbf')),
  intoComment: run('«a % note»\nb', S('textbf')),
  escapedBrace: run('a \\«{ b» c', S('textbf')),
  acrossParagraphs: run('«a\n\nb»', S('textbf')),
  inFormula: run('a $x|$ d', S('textbf')),
  inPreamble: run('\\documentclass{article}\n\\usepackage{x}|\n\\begin{document}\nz\n\\end{document}', S('textbf')),
  afterDocument: run('\\documentclass{article}\n\\begin{document}\nz\n\\end{document}\n|', S('textbf')),
  inLabel: run('a \\label{se|c} b', S('textbf')),
  inCite: run('a \\cite{ke|y} b', S('textbf')),
  inCaption: run('\\caption{a «b» c}', S('textbf')),
  crlf: run('a «bold»\nb', S('textbf'), '\r\n'),
};
const F = (choice) => (st) => P.fontSpec(st, choice), Z = (choice) => (st) => P.sizeSpec(st, choice);
out.font = {
  songti: run('a «word» b', F('songti')),
  declChanged: run('a {\\songti wo|rd} b', F('heiti')),
  declToCommand: run('a {\\songti wo|rd} b', F('textsf')),
  commandToDecl: run('a \\texttt{«word»} b', F('kaishu')),
  defaultRemoves: run('a \\textsf{wo|rd} b', F('')),
  defaultNothing: run('a wo|rd b', F('')),
  same: run('a \\textsf{wo|rd} b', F('textsf')),
  emptyInsertsCommand: run('a | b', F('texttt')),
  emptyInsertsDecl: run('a | b', F('fangsong')),
  innermost: run('{\\heiti a \\textsf{b|c}}', F('')),
  groupOverLines: run('{\\songti a\nb|c\n}', F('')),
  argumentIsNotAGroup: run('\\textbf{\\songti a|b}', F('')),
  textrm: run('a «x» b', F('textrm')),
};
out.size = {
  small: run('a «word» b', Z('small')),
  normalRemoves: run('a {\\small wo|rd} b', Z('')),
  changed: run('a {\\small «word»} b', Z('Large')),
  large: run('a | b', Z('large')),
  acrossParagraphs: run('«a\n\nb»', Z('large')),
};
const H = (level, chapters) => (st) => P.headingSpec(st, level, chapters);
const level = (marked) => { const st = state(marked); return P.headingLevel(st, P.readLatex(st.doc.toString())); };
out.heading = {
  paragraph: run('Before\n\nIntro te|xt\nmore\n\nnext', H('section', false)),
  indentedWithComment: run('  Intro te|xt % why\n', H('subsection', false)),
  levelChanged: run('\\section{Int|ro}\\label{s}\ntext', H('subsubsection', false)),
  starredKept: run('\\section*[short]{Int|ro}', H('subsection', false)),
  backToBody: run('\\section{Int|ro}\\label{s}\ntext', H('', false)),
  bodyStays: run('Some |text', H('', false)),
  emptyLine: run('x\n\n|\n\ny', H('section', false)),
  chapterRefused: run('Intro|', H('chapter', false)),
  chapterMade: run('Intro|', H('chapter', true)),
  blankRefused: run('«a\n\nb»', H('section', true)),
  structuralRefused: run('\\begin{quote}|\nx\n\\end{quote}', H('section', false)),
  inListRefused: run('\\begin{itemize}\n\\item a|\n\\end{itemize}', H('section', false)),
  inArgumentRefused: run('\\textbf{a|b}', H('section', false)),
  stopsAtHeading: run('\\section{A}\nbody te|xt\n\\subsection{B}', H('subsection', false)),
  crlf: run('Intro te|xt\nmore', H('section', false), '\r\n'),
  levels: [level('\\section{A|}'), level('\\chapter{A}|'), level('text|'), level('\\begin{quote}|'), level('\\paragraph{x|}')],
};
const fig = (extra) => (st) => P.figureSpec(st, Object.assign({path: '../figures/a.png', file: 'figures/a.png', caption: 'A_b & c', graphicx: true}, extra));
const MAIN = '\\documentclass{article}\n\\usepackage{amsmath}\n\\begin{document}\nText|\n\\end{document}';
out.figure = {
  midLine: run('Text | more', fig({})),
  lineEnd: run('Text|\nnext', fig({half: true})),
  lineStart: run('|Text', fig({caption: ''})),
  graphicxAddedToMain: run(MAIN, fig({graphicx: false, isMain: true})),
  graphicxNamedForChapter: run('Text|', fig({graphicx: false, isMain: false, main: 'paper/main.tex'})),
  graphicxNoMain: run('Text|', fig({graphicx: false, isMain: false, main: null})),
  labelUnique: run('\\label{fig:a}\nText|', fig({})),
  crlf: run('Text|\nnext', fig({}), '\r\n'),
  atEnd: run('Text|', fig({})),
  inArgument: run('\\caption{a|}', fig({})),
  inTabular: run('\\begin{tabular}{l}\na|\n\\end{tabular}', fig({})),
};
out.paths = [P.figurePath('figures/a.png', 'paper', []), P.figurePath('figures/a.png', 'paper', ['../figures/']),
  P.figurePath('paper/img/x.pdf', 'paper', ['img/']), P.figurePath('a.png', '', []), P.figurePath('paper/f/a.png', 'paper', []),
  P.figurePath('figures/sub/a.png', '', ['figures/']), P.figurePath('other/a.png', 'paper', ['../figures/'])];
const tab = (extra) => (st) => P.tableSpec(st, Object.assign({rows: 3, cols: 2, header: true, caption: 'Main results', booktabs: true}, extra));
out.table = {
  headerBooktabs: run('Text|', tab({})),
  noHeaderBooktabs: run('Text|', tab({header: false})),
  headerHline: run('Text|', tab({booktabs: false})),
  oneColumn: run('Text|', tab({rows: 2, cols: 1, header: false, caption: '', booktabs: false})),
  booktabsAddedToMain: run(MAIN, tab({booktabs: false, isMain: true, rows: 1, cols: 3})),
  clamped: run('|', tab({rows: 99, cols: 0})),
  crlf: run('Text|', tab({rows: 2}), '\r\n'),
};
out.math = {
  inlineWrap: run('a «x+y» b', (st) => P.mathSpec(st, false)),
  inlineEmpty: run('a | b', (st) => P.mathSpec(st, false)),
  displayWrap: run('a «x+y» b', (st) => P.mathSpec(st, true)),
  displayEmpty: run('Text|', (st) => P.mathSpec(st, true)),
  displayAcross: run('«a\n\nb»', (st) => P.mathSpec(st, true)),
};
out.list = {
  wrapLines: run('«one\ntwo\n\nthree»\nafter', (st) => P.listSpec(st, 'itemize')),
  wrapPartOfLine: run('al«pha»\nbeta', (st) => P.listSpec(st, 'enumerate')),
  empty: run('x|', (st) => P.listSpec(st, 'enumerate')),
  structural: run('«a\n\\section{b}»', (st) => P.listSpec(st, 'itemize')),
  crlf: run('«one\ntwo»', (st) => P.listSpec(st, 'itemize'), '\r\n'),
};
out.footnote = [run('a «b» c', P.footnoteSpec), run('a| c', P.footnoteSpec), run('$x|$', P.footnoteSpec)];
out.cite = [run('a | c', (st) => P.citeSpec(st, ['k1', 'k2'])), run('a «b» c', (st) => P.citeSpec(st, ['k1'])), run('a | c', (st) => P.citeSpec(st, []))];
out.facts = [P.preambleFacts('\\documentclass[12pt]{ctexbook}\n%\\usepackage{graphicx}\n\\usepackage{amsmath, booktabs}\n\\graphicspath{{fig/}{../img/}}'),
  P.preambleFacts('\\documentclass{article}\n\\usepackage[demo]{graphicx}'), P.preambleFacts('\\documentclass{beamer}'),
  P.preambleFacts('\\documentclass{report}\n\\usepackage{ctex,tikz}'), P.preambleFacts('')];
out.escape = P.escapeTex('50% of $x & y_1 {a} ~^\\ #\n  two');
const fs2 = (marked) => { const st = state(marked); return P.formatState(st, P.readLatex(st.doc.toString())); };
out.state = [fs2('a \\textbf{\\textit{b|}} c'), fs2('{\\songti {\\small x|}}'), fs2('\\section{\\texttt{x|}}'), fs2('«\\underline{x}»')];
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def pure() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    script = (_PURE.replace("__CM__", json.dumps(str(WEBAPP / "vendor" / "codemirror.js")))
              .replace("__LATEX__", json.dumps(str(LATEX_JS))))
    done = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


CROSSING = {"refused": "选中的文字跨过了括号或环境的边界，不能整段加格式。请重新选择。"}


# -- 文字: 加粗, 斜体, 下划线 ------------------------------------------------------------------------------------------


@needs_node
def test_a_selection_is_wrapped_and_the_same_command_unwraps_it_like_word(pure):
    s = pure["style"]
    assert s["wrap"]["text"] == "a \\textbf{«bold»} b" and s["wrap"]["steps"] == 1
    assert s["unwrapText"]["text"] == "a «bold» b"
    assert s["unwrapWhole"]["text"] == "a «bold» b"
    assert s["unwrapAtCursor"]["text"] == "a bo|ld b"
    assert s["emptyInserts"]["text"] == "a \\underline{|} b"
    assert s["otherCommandWraps"]["text"] == "a \\textbf{\\textit{«bold»}} b"
    assert s["nestedWrap"]["text"] == "\\textbf{a \\textit{«b»} c}"
    assert s["inCaption"]["text"] == "\\caption{a \\textbf{«b»} c}"
    assert s["crlf"]["file"] == "a \\textbf{bold}\r\nb"


@needs_node
def test_a_selection_that_crosses_a_brace_a_name_an_environment_a_formula_or_a_comment_is_refused_never_broken(pure):
    s = pure["style"]
    for case in ("acrossBraces", "acrossArgument", "acrossCommandName", "acrossEnvironment", "acrossFormula", "intoComment",
                 "escapedBrace"):
        assert s[case] == CROSSING or "refused" in s[case], case
    for case in ("acrossBraces", "acrossArgument", "acrossEnvironment", "escapedBrace"):
        assert s[case] == CROSSING, case
    assert s["intoComment"] == {"refused": "光标在注释或原样照排的文字里，这里不能用格式与插入。"}
    assert s["acrossCommandName"] == {"refused": "光标在命令 \\textbf 的名字里。请把光标移开。"}
    assert s["acrossFormula"] == {"refused": "光标在公式里。请把光标移到公式外面。"}
    assert s["acrossParagraphs"] == {"refused": "选中的文字跨了段落，不能整段加这个格式。"}


@needs_node
def test_where_a_style_makes_no_sense_it_says_why_in_a_quiet_sentence(pure):
    s = pure["style"]
    assert s["inFormula"] == {"refused": "光标在公式里。请把光标移到公式外面。"}
    assert s["inPreamble"] == {"refused": "光标在导言区里。请把光标放到正文里。"}
    assert s["afterDocument"] == {"refused": "光标在文稿结尾（\\end{document}）之后。请把光标放到正文里。"}
    assert s["inLabel"] == {"refused": "光标在 \\label 的参数里，这里不能用这个工具。"}
    assert s["inCite"] == {"refused": "光标在 \\cite 的参数里，这里不能用这个工具。"}


# -- 字体 and 字号 -------------------------------------------------------------------------------------------------


@needs_node
def test_a_font_wraps_changes_or_removes_the_font_the_cursor_is_in(pure):
    f = pure["font"]
    assert f["songti"]["text"] == "a {\\songti «word»} b"
    assert f["declChanged"]["text"] == "a {\\heiti wo|rd} b"
    assert f["declToCommand"]["text"] == "a \\textsf{wo|rd} b"
    assert f["commandToDecl"]["text"] == "a {\\kaishu «word»} b"
    assert f["defaultRemoves"]["text"] == "a wo|rd b"
    assert f["defaultNothing"] is None and f["same"] is None
    assert f["emptyInsertsCommand"]["text"] == "a \\texttt{|} b"
    assert f["emptyInsertsDecl"]["text"] == "a {\\fangsong |} b"
    assert f["innermost"]["text"] == "{\\heiti a b|c}"
    assert f["groupOverLines"]["text"] == "a\nb|c\n"
    assert f["argumentIsNotAGroup"] is None            # \textbf{\songti …}: braces that are \textbf's, never removed
    assert f["textrm"]["text"] == "a \\textrm{«x»} b"


@needs_node
def test_a_size_is_a_group_and_normal_removes_the_one_the_cursor_is_in(pure):
    z = pure["size"]
    assert z["small"]["text"] == "a {\\small «word»} b"
    assert z["normalRemoves"]["text"] == "a wo|rd b"
    assert z["changed"]["text"] == "a {\\Large «word»} b"
    assert z["large"]["text"] == "a {\\large |} b"
    assert z["acrossParagraphs"] == {"refused": "选中的文字跨了段落，不能整段加这个格式。"}


@needs_node
def test_the_panel_reads_pressed_styles_the_font_the_size_and_the_level_where_the_cursor_is(pure):
    st = pure["state"]
    assert st[0]["pressed"] == {"textbf": True, "textit": True, "underline": False} and st[0]["heading"] == ""
    assert st[1]["font"] == "songti" and st[1]["size"] == "small"
    assert st[2]["font"] == "texttt" and st[2]["heading"] == "section"
    assert st[3]["pressed"]["underline"] is False       # the whole command selected is not inside it


# -- 标题 ------------------------------------------------------------------------------------------------------


@needs_node
def test_a_paragraph_becomes_a_heading_a_heading_changes_level_and_body_unwraps_it(pure):
    h = pure["heading"]
    assert h["paragraph"]["text"] == "Before\n\n\\section{Intro te|xt\nmore}\n\nnext"
    assert h["indentedWithComment"]["text"] == "  \\subsection{Intro te|xt} % why\n"
    assert h["levelChanged"]["text"] == "\\subsubsection{Int|ro}\\label{s}\ntext"
    assert h["starredKept"]["text"] == "\\subsection*[short]{Int|ro}"
    assert h["backToBody"]["text"] == "Int|ro\\label{s}\ntext"
    assert h["bodyStays"] is None
    assert h["emptyLine"]["text"] == "x\n\n\\section{|}\n\ny"
    assert h["stopsAtHeading"]["text"] == "\\section{A}\n\\subsection{body te|xt}\n\\subsection{B}"
    assert h["crlf"]["file"] == "\\section{Intro text\r\nmore}"
    assert h["levels"] == ["section", "chapter", "", None, "paragraph"]


@needs_node
def test_chapter_is_only_for_a_class_with_chapters_and_a_heading_with_a_blank_line_is_refused(pure):
    h = pure["heading"]
    assert h["chapterRefused"] == {"refused": "这份文稿的文档类没有「章」。"}
    assert h["chapterMade"]["text"] == "\\chapter{Intro|}"
    assert h["blankRefused"] == {"refused": "标题里不能有空行：选中的文字跨了段落。"}
    assert h["structuralRefused"] == {"refused": "这一行是命令或环境，不能变成标题。"}
    assert h["inListRefused"] == {"refused": "光标在列表里，不能把这一段变成标题。"}
    assert h["inArgumentRefused"] == {"refused": "光标在 \\textbf 的参数里，这里不能用这个工具。"}


@needs_node
def test_the_chapter_classes_and_the_packages_are_read_from_the_preamble_outside_comments(pure):
    ctexbook, demo, beamer, report, empty = pure["facts"]
    assert ctexbook == {"cls": "ctexbook", "chapters": True, "ctex": True, "graphicx": False, "booktabs": True,
                        "amsmath": True, "url": False, "graphicspath": ["fig/", "../img/"]}   # amsmath and url: A2
    assert demo["graphicx"] is True and demo["chapters"] is False and demo["ctex"] is False
    assert beamer["graphicx"] is True
    assert report["chapters"] is True and report["ctex"] is True and report["graphicx"] is True   # tikz loads graphicx
    assert empty["cls"] is None and empty["graphicx"] is False


# -- 插入: 图片 ---------------------------------------------------------------------------------------------------


FIGURE = ("\\begin{figure}[htbp]\n\\centering\n\\includegraphics[width=\\linewidth]{../figures/a.png}\n"
          "\\caption{A\\_b \\& c}\n\\label{fig:a}\n\\end{figure}")


@needs_node
def test_a_figure_goes_on_lines_of_its_own_and_the_cursor_lands_where_the_text_goes_on(pure):
    f = pure["figure"]
    assert f["midLine"]["text"] == "Text \n" + FIGURE + "\n| more"
    assert f["lineEnd"]["text"] == "Text\n" + FIGURE.replace("width=\\linewidth", "width=0.5\\linewidth") + "\n|next"   # no blank line added
    assert f["lineStart"]["text"] == FIGURE.replace("A\\_b \\& c", "") + "\n|Text"
    assert f["labelUnique"]["text"].count("\\label{fig:a-2}") == 1
    assert f["crlf"]["file"] == "Text\r\n" + FIGURE.replace("\n", "\r\n") + "\r\nnext"
    assert f["atEnd"]["text"] == "Text\n" + FIGURE + "\n|"
    assert f["inArgument"] == {"refused": "光标在 \\caption 的参数里，这里不能用这个工具。"}
    assert f["inTabular"] == {"refused": "光标在 tabular 环境里，这里不能用这个工具。"}


@needs_node
def test_graphicx_is_added_only_to_an_open_main_file_in_the_same_transaction_else_the_panel_names_the_line(pure):
    f = pure["figure"]
    main = f["graphicxAddedToMain"]
    assert main["text"].startswith("\\documentclass{article}\n\\usepackage{graphicx}\n\\usepackage{amsmath}\n\\begin{document}\nText\n")
    assert main["steps"] == 1 and "note" not in main
    chapter = f["graphicxNamedForChapter"]
    assert "\\usepackage{graphicx}" not in chapter["text"].split("\\begin{figure}")[0]
    assert chapter["note"] == "主文件 paper/main.tex 的导言区需要加一行：\\usepackage{graphicx}"
    assert f["graphicxNoMain"]["note"] == "主文件的导言区需要加一行：\\usepackage{graphicx}"


@needs_node
def test_an_image_path_is_relative_to_the_main_files_folder_or_to_a_graphicspath_folder(pure):
    assert pure["paths"] == ["../figures/a.png", "a.png", "x.pdf", "a.png", "f/a.png", "sub/a.png", "../other/a.png"]


# -- 插入: 表格, 公式, 列表, 脚注, 文献引用 ----------------------------------------------------------------------------------


TABLE_HEAD = "\\begin{table}[htbp]\n\\centering\n\\caption{Main results}\n\\label{tab:main-results}\n\\begin{tabular}{ll}\n"


@needs_node
def test_a_table_has_its_header_rule_only_with_a_header_and_booktabs_only_where_it_is_loaded(pure):
    t = pure["table"]
    assert t["headerBooktabs"]["text"] == "Text\n" + TABLE_HEAD + "\\toprule\n|& \\\\\n\\midrule\n& \\\\\n& \\\\\n\\bottomrule\n\\end{tabular}\n\\end{table}"
    assert t["noHeaderBooktabs"]["text"] == "Text\n" + TABLE_HEAD + "\\toprule\n|& \\\\\n& \\\\\n& \\\\\n\\bottomrule\n\\end{tabular}\n\\end{table}"
    assert t["headerHline"]["text"] == "Text\n" + TABLE_HEAD + "\\hline\n|& \\\\\n\\hline\n& \\\\\n& \\\\\n\\hline\n\\end{tabular}\n\\end{table}"
    assert t["oneColumn"]["text"] == ("Text\n\\begin{table}[htbp]\n\\centering\n\\caption{}\n\\label{tab:table}\n\\begin{tabular}{l}\n"
                                      "\\hline\n|\\\\\n\\\\\n\\hline\n\\end{tabular}\n\\end{table}")
    added = t["booktabsAddedToMain"]
    assert added["text"].startswith("\\documentclass{article}\n\\usepackage{booktabs}\n") and "\\toprule\n|& & \\\\\n\\bottomrule" in added["text"]
    assert added["steps"] == 1
    assert t["clamped"]["text"].count("\\\\\n") == 20 and "{tabular}{l}" in t["clamped"]["text"]
    assert t["crlf"]["file"].count("\r\n") == t["crlf"]["text"].count("\n") and "\n" not in t["crlf"]["file"].replace("\r\n", "")


@needs_node
def test_formulas_lists_footnotes_and_citations_are_typed_as_their_commands(pure):
    m, li = pure["math"], pure["list"]
    assert m["inlineWrap"]["text"] == "a $«x+y»$ b" and m["inlineEmpty"]["text"] == "a $|$ b"
    assert m["displayWrap"]["text"] == "a \n\\begin{equation}\nx+y|\n\\end{equation}\n b"
    assert m["displayEmpty"]["text"] == "Text\n\\begin{equation}\n|\n\\end{equation}"
    assert m["displayAcross"] == {"refused": "选中的文字跨了段落，不能整段加这个格式。"}
    assert li["wrapLines"]["text"] == "\\begin{itemize}\n  \\item one\n  \\item two\n  \\item three|\n\\end{itemize}\nafter"
    assert li["wrapPartOfLine"]["text"] == "\\begin{enumerate}\n  \\item alpha|\n\\end{enumerate}\nbeta"
    assert li["empty"]["text"] == "x\n\\begin{enumerate}\n  \\item |\n\\end{enumerate}"
    assert li["structural"] == {"refused": "选中的几行里有命令或环境，不能变成列表。"}
    assert li["crlf"]["file"] == "\\begin{itemize}\r\n  \\item one\r\n  \\item two\r\n\\end{itemize}"
    assert [x if x is None or "refused" in x else x["text"] for x in pure["footnote"]] == [
        "a b\\footnote{|} c", "a\\footnote{|} c", {"refused": "光标在公式里。请把光标移到公式外面。"}]
    assert pure["cite"][0]["text"] == "a \\cite{k1,k2}| c" and pure["cite"][1]["text"] == "a b\\cite{k1}| c" and pure["cite"][2] is None


@needs_node
def test_a_caption_is_escaped_so_it_cannot_break_the_file(pure):
    assert pure["escape"] == "50\\% of \\$x \\& y\\_1 \\{a\\} \\~{}\\^{}\\textbackslash{} \\# two"


# -- the panel on the page ---------------------------------------------------------------------------------------

_PANEL = r"""
El.prototype.setAttribute = function (k, v) { (this.attrs || (this.attrs = {}))[k] = String(v); };
FakeView.prototype.dispatch = function (spec) {
  const tr = this.state.update(spec); this.state = tr.state;
  for (const listen of tr.state.facet(real.EditorView.updateListener)) listen({docChanged: tr.docChanged, selectionSet: !!spec.selection, changes: tr.changes, view: this, state: tr.state});
};
FakeView.prototype.focus = function () { this.focused = (this.focused || 0) + 1; };
const out = {};
const view = () => viewsMade[viewsMade.length - 1];
const side = () => byClass('latex-side');
const inside = (el, test, acc) => { acc = acc || []; if (el && typeof el === 'object') { if (test(el)) acc.push(el); (el.children || []).forEach((k) => inside(k, test, acc)); } return acc; };
const hidden = (el) => el.classList.contains('hidden');
const shown = (root) => { const acc = []; const walk = (el) => { if (!el || typeof el !== 'object' || hidden(el)) return; acc.push(el); (el.children || []).forEach(walk); }; walk(root); return acc; };
const words = (root) => shown(root).map((e) => e.textContent).filter(Boolean);
const allWords = (root) => inside(root, () => true).map((e) => e.textContent).filter(Boolean);
const primaries = () => shown(side()).filter((e) => e.tag === 'button' && e.classList.contains('btn-primary')).map((e) => e.textContent);
const tool = (label) => inside(side(), (e) => e.tag === 'button' && e.textContent === label).pop();
const panes = () => inside(side(), (e) => e.classList.contains('latex-tab-pane'));
const selects = () => inside(panes()[0], (e) => e.tag === 'select');   // 格式's own (版式's are in the other pane, A2)
const picker = () => inside(side(), (e) => e.classList.contains('latex-picker'))[0];
const options = (s) => s.children.map((o) => [o.value, o.textContent].concat(o.disabled ? ['disabled'] : []));
const doc = () => view().state.doc.toString();
const pick = (word, n) => { const at = doc().indexOf(word); view().dispatch({selection: {anchor: at, head: at + (n == null ? word.length : n)}}); };
const f = (path, text, extra) => Object.assign({path, text, revision: 'r1', line_ending: 'lf', read_only: false, read_only_reason: null, links: [], mains: []}, extra);
const openFile = async (path, text, extra) => { L.open(path); await tick(); gets.shift().res(f(path, text, extra)); await tick(); await tick(); };
const CHAPTER = {main: 'paper/main.tex', engine: 'pdflatex', folder: 'paper', preamble: '\\documentclass{report}\n\\usepackage{booktabs}\n'};
await openFile('paper/ch.tex', 'Some text here.\nSecond line.\n', {mains: ['paper/main.tex'], format: CHAPTER});
const V = view();
out.firstIsPanel = side().children[0] === inside(side(), (e) => e.classList.contains('latex-format'))[0];
out.tabs = inside(side(), (e) => e.attrs && e.attrs.role === 'tab').map((b) => [b.textContent, b.attrs['aria-selected']]);
out.layout = allWords(inside(side(), (e) => e.classList.contains('latex-tab-pane'))[1]).slice(0, 2);
out.layoutHidden = hidden(inside(side(), (e) => e.classList.contains('latex-tab-pane'))[1]);
tool('版式').fire('click');
out.layoutShown = [hidden(inside(side(), (e) => e.classList.contains('latex-tab-pane'))[0]), words(inside(side(), (e) => e.classList.contains('latex-tab-pane'))[1])];
tool('格式').fire('click');
out.groups = inside(side(), (e) => e.tag === 'h4').map((h) => h.textContent);
out.fontOptions = options(selects()[0]); out.sizeOptions = options(selects()[1]); out.headingOptions = options(selects()[2]);
out.shortcut = ['加粗', '斜体', '下划线'].map((w) => [tool(w).textContent + tool(w).children.map((c) => typeof c === 'string' ? c : c.textContent).join(''), tool(w).attrs['aria-keyshortcuts']]);
// 加粗 on a selection: one transaction, pressed after; ⌘Z takes it back; the save sends it as typed
pick('text');
out.pressedBefore = tool('加粗').attrs['aria-pressed'];
tool('加粗').fire('mousedown'); tool('加粗').fire('click');
out.afterBold = {doc: doc(), pressed: tool('加粗').attrs['aria-pressed'], undoDepth: real.undoDepth(V.state), focused: V.focused, dirty: L.isDirty()};
const undoView = {get state() { return V.state; }, dispatch: (tr) => { V.state = tr.state; }};
real.undo(undoView);
out.afterUndo = doc();
real.redo(undoView);
// ⌘I comes before CodeMirror's own Mod-i
const maps = V.state.facet(real.keymap), flat = [].concat(...maps);
const bound = (key) => flat.filter((b) => b.key === key);
out.modI = bound('Mod-i').length; out.modU = bound('Mod-u').length;
pick('Second');
bound('Mod-i')[0].run(V); bound('Mod-u')[0].run(V); bound('Mod-b')[0].run(V);
out.afterKeys = doc();
// 字体 / 字号 / 标题 selects
pick('here');
selects()[0].value = 'texttt'; selects()[0].fire('change');
out.afterFont = doc();
out.fontShown = selects()[0].value;
selects()[1].value = 'large'; selects()[1].fire('change');
out.afterSize = doc();
view().dispatch({selection: {anchor: 2}});
selects()[2].value = 'chapter'; selects()[2].fire('change');
out.afterHeading = {doc: doc(), level: selects()[2].value};
// a refusal is said in the panel; nothing changes
view().dispatch({selection: {anchor: doc().indexOf('{So') + 1, head: doc().indexOf('here') + 2}});
tool('加粗').fire('click');
const note = inside(side(), (e) => e.classList.contains('latex-format-note'))[0];
out.refused = {text: note.textContent, hidden: hidden(note), doc: doc()};
// 图片: the picker lists the project's images; one primary; the path from the main file's folder; graphicx named
view().dispatch({selection: {anchor: doc().length}});
tool('图片').fire('click'); await tick();
out.imageUrl = gets[gets.length - 1].url;
out.expanded = tool('图片').attrs['aria-expanded'];
gets.pop().res({images: [{path: 'figures/plot.png', thumb: true}, {path: 'figures/bad%name.png', thumb: false}, {path: 'figures/old.eps', thumb: false}]});
await tick(); await tick();
const items = () => inside(side(), (e) => e.classList.contains('latex-image'));
out.items = items().map((b) => [b.children.map((c) => c.tag === 'img' ? 'img:' + c.src : c.textContent).join(' '), !!b.disabled]);
out.pickerPrimaries = primaries();
out.insertDisabled = tool('插入').disabled;
items()[0].fire('click');
const inputs = inside(picker(), (e) => e.tag === 'input' && e.type === 'text');
inputs[1].value = 'Effects & sizes';
inside(picker(), (e) => e.tag === 'select')[0].value = 'half';
tool('插入').fire('click');
out.afterFigure = {doc: doc(), note: note.textContent, warn: note.classList.contains('latex-export-warn'), pickerOpen: !hidden(inside(side(), (e) => e.classList.contains('latex-picker'))[0]), primaries: primaries()};
// 表格: 行 / 列 by the grid, header off; booktabs from the main file's preamble
tool('表格').fire('click');
const cells = inside(side(), (e) => e.classList.contains('latex-grid-cell'));
out.grid = {cells: cells.length, tabindex: cells[0].attrs.tabindex};
cells[8 + 2].fire('click');   // row 2, column 3
const numbers = inside(picker(), (e) => e.tag === 'input' && e.type === 'number');
out.numbers = numbers.map((i) => [i.value, i.min, i.max, i.attrs['aria-label']]);
inside(side(), (e) => e.tag === 'input' && e.type === 'checkbox')[0].checked = false;
out.tablePrimaries = primaries();
tool('插入').fire('click');
out.afterTable = doc();
// 文献引用: the .bib entries, two picked, one \cite
view().dispatch({selection: {anchor: doc().indexOf('Second') }});
tool('文献引用').fire('click'); await tick();
out.bibUrl = gets[gets.length - 1].url;
gets.pop().res({files: ['refs.bib'], entries: [{key: 'smith2020', author: 'Smith, J.', year: '2020', title: 'A study'}, {key: 'li2021', author: 'Li', year: '2021', title: ''}]});
await tick(); await tick();
const bibs = () => inside(side(), (e) => e.classList.contains('latex-bib'));
bibs()[0].fire('click'); bibs()[1].fire('click');
out.picked = words(side()).filter((w) => w.startsWith('已选'));
tool('插入').fire('click');
out.afterCite = doc();
// with no .bib, and no image: said in the picker
tool('文献引用').fire('click'); await tick(); gets.pop().res({files: [], entries: []}); await tick(); await tick();
out.noBib = words(inside(side(), (e) => e.classList.contains('latex-picker'))[0]);
tool('图片').fire('click'); await tick(); gets.pop().res({images: []}); await tick(); await tick();
out.noImage = words(inside(side(), (e) => e.classList.contains('latex-picker'))[0]);
out.noImagePrimaries = primaries();
// the link panel closes the picker: one primary at a time
tool('表格').fire('click');
pick('Second');
byClass('latex-link-button').fire('click'); await tick();
gets.shift().res({files: []}); await tick();
out.withLink = primaries();
// saved: the edits the tools made go through the editor's own save
button('保存').fire('click'); await tick();
out.saved = posts.length ? posts[0].body.edits.length > 0 : false;
posts.shift().res({revision: 'r2', changed: true, previous: 'r1'}); await tick(); await tick();
// 显示源码 turns the panel off with its reason, and on again
button('显示源码').fire('click');
const reason = inside(side(), (e) => e.classList.contains('latex-format-reason'))[0];
out.source = {reason: reason.textContent, hidden: hidden(reason), disabled: tool('加粗').disabled && selects()[0].disabled && tool('图片').disabled};
const before = doc(); bound('Mod-b').length; [].concat(...view().state.facet(real.keymap)).find((b) => b.key === 'Mod-b').run(view());
out.source.keyDid = doc() !== before;
button('显示源码').fire('click');
out.sourceOff = {hidden: hidden(reason), disabled: tool('加粗').disabled};
// a ctexart main file open: the Chinese fonts, no 衬线 (XeLaTeX), no 章; graphicx and booktabs added to it
await openFile('paper/main.tex', '\\documentclass{ctexart}\n\\begin{document}\n正文\n\\end{document}\n',
  {mains: ['paper/main.tex'], format: {main: 'paper/main.tex', engine: 'xelatex', folder: 'paper', preamble: null}});
out.ctexFonts = options(selects()[0]).map((o) => o[1]);
out.ctexHeadings = options(selects()[2]).map((o) => o[0]);
view().dispatch({selection: {anchor: doc().indexOf('正文') + 2}});
tool('表格').fire('click'); tool('插入').fire('click');
out.mainTable = doc().split('\n').slice(0, 3);
// a read-only file: the panel is there, disabled, with its reason
await openFile('gen.tex', 'x\n', {read_only: true, read_only_reason: 'generated', read_only_message: 'generated'});
const r2 = inside(side(), (e) => e.classList.contains('latex-format-reason'))[0];
out.readOnly = {reason: r2.textContent, hidden: hidden(r2), allOff: inside(side(), (e) => (e.tag === 'button' && e.classList.contains('latex-tool')) || e.tag === 'select').every((c) => c.disabled)};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def panel() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(LATEX_JS))}
    script = (prelude + "(async () => {\n" + _PANEL
              + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });")
    done = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    return out


@needs_node
def test_the_panel_is_at_the_top_of_the_right_column_with_two_word_tabs_and_layout_waiting_for_its_step(panel):
    assert panel["firstIsPanel"] is True
    assert panel["tabs"] == [["格式", "true"], ["版式", "false"]]
    # A2 fills 版式 (test_v17_layout_settings.py): in a chapter file it says where the layout is set
    assert panel["layoutHidden"] is True and panel["layout"] == ["版式在主文件 paper/main.tex 里设置。", "打开 paper/main.tex"]
    assert panel["layoutShown"][0] is True and panel["layoutShown"][1][:2] == panel["layout"]
    assert panel["groups"][:4] == ["文字", "章节标题", "段落", "插入"]


@needs_node
def test_the_choices_follow_the_document_fonts_by_engine_and_chapter_by_class(panel):
    assert panel["fontOptions"] == [["", "默认"], ["textsf", "无衬线"], ["texttt", "等宽"], ["textrm", "衬线"]]
    assert panel["sizeOptions"] == [["small", "小"], ["", "正常"], ["large", "大"], ["Large", "更大"]]
    assert panel["headingOptions"] == [["", "正文段落"], ["chapter", "章"], ["section", "节"], ["subsection", "小节"],
                                       ["subsubsection", "小小节"], ["other", "其他标题", "disabled"]]
    assert panel["ctexFonts"] == ["默认", "宋体", "黑体", "楷体", "仿宋", "无衬线", "等宽"]
    assert panel["ctexHeadings"] == ["", "section", "subsection", "subsubsection", "other"]


@needs_node
def test_a_tool_is_one_transaction_of_the_editor_undone_by_cmd_z_saved_as_typed_and_shown_pressed(panel):
    assert panel["shortcut"] == [["加粗 ⌘B", "Meta+B"], ["斜体 ⌘I", "Meta+I"], ["下划线 ⌘U", "Meta+U"]]
    assert panel["pressedBefore"] == "false"
    assert panel["afterBold"] == {"doc": "Some \\textbf{text} here.\nSecond line.\n", "pressed": "true", "undoDepth": 1,
                                  "focused": 1, "dirty": True}
    assert panel["afterUndo"] == "Some text here.\nSecond line.\n"
    assert panel["saved"] is True


@needs_node
def test_cmd_b_cmd_i_and_cmd_u_run_the_tools_before_codemirrors_own_bindings(panel):
    assert panel["modI"] >= 2 and panel["modU"] >= 2          # ours first, CodeMirror's after
    # each wraps the one before's text, which stays selected: Mod-i, then Mod-u, then Mod-b
    assert panel["afterKeys"] == "Some \\textbf{text} here.\n\\textit{\\underline{\\textbf{Second}}} line.\n"


@needs_node
def test_the_selects_apply_a_font_a_size_and_a_level_and_show_what_the_cursor_is_in(panel):
    assert panel["afterFont"].startswith("Some \\textbf{text} \\texttt{here}.") and panel["fontShown"] == "texttt"
    assert panel["afterSize"].startswith("Some \\textbf{text} \\texttt{{\\large here}}.")   # the font's text stayed selected
    assert panel["afterHeading"]["doc"].startswith(       # the paragraph's two lines are its text
        "\\chapter{Some \\textbf{text} \\texttt{{\\large here}}.\n\\textit{\\underline{\\textbf{Second}}} line.}\n")
    assert panel["afterHeading"]["level"] == "chapter"
    assert panel["refused"]["hidden"] is False and panel["refused"]["text"].startswith("选中的文字跨过了括号")
    assert panel["refused"]["doc"] == panel["afterHeading"]["doc"]


@needs_node
def test_the_image_picker_lists_the_projects_images_with_thumbnails_and_inserts_the_figure_from_the_main_folder(panel):
    assert panel["imageUrl"] == "/api/latex/insert?what=images&path=paper%2Fch.tex"
    assert panel["expanded"] == "true"
    assert panel["items"] == [["img:/api/workspace/asset?path=figures%2Fplot.png figures/plot.png", False],
                              ["figures/bad%name.png 文件名里有 LaTeX 不能用的字符", True], ["figures/old.eps", False]]
    assert panel["pickerPrimaries"] == ["插入"] and panel["insertDisabled"] is True
    fig = panel["afterFigure"]
    assert ("\\begin{figure}[htbp]\n\\centering\n\\includegraphics[width=0.5\\linewidth]{../figures/plot.png}\n"
            "\\caption{Effects \\& sizes}\n\\label{fig:plot}\n\\end{figure}\n") in fig["doc"]
    assert fig["note"] == "主文件 paper/main.tex 的导言区需要加一行：\\usepackage{graphicx}" and fig["warn"] is True
    assert fig["pickerOpen"] is False and fig["primaries"] == []


@needs_node
def test_the_table_picker_sets_rows_and_columns_by_keyboard_or_grid_and_uses_the_main_files_booktabs(panel):
    assert panel["grid"] == {"cells": 64, "tabindex": "-1"}
    assert panel["numbers"] == [["2", "1", "20", "行"], ["3", "1", "10", "列"]]
    assert panel["tablePrimaries"] == ["插入"]
    assert "\\begin{tabular}{lll}\n\\toprule\n& & \\\\\n& & \\\\\n\\bottomrule\n\\end{tabular}" in panel["afterTable"]


@needs_node
def test_the_citation_picker_joins_the_picked_keys_and_says_so_when_there_is_no_bib_or_no_image(panel):
    assert panel["bibUrl"] == "/api/latex/insert?what=bib&path=paper%2Fch.tex"
    assert panel["picked"] == ["已选：smith2020, li2021"]
    assert "\\cite{smith2020,li2021}Second" in panel["afterCite"]
    assert "项目里没有 .bib 文件，没有可以引用的文献。" in panel["noBib"]
    assert "项目里还没有图片。把图片放进项目文件夹后，这里就能选。" in panel["noImage"] and panel["noImagePrimaries"] == []


@needs_node
def test_one_primary_at_a_time_the_link_panel_closes_an_open_picker(panel):
    assert panel["withLink"] == ["确认关联"]


@needs_node
def test_show_source_and_a_read_only_file_disable_the_panel_with_a_reason(panel):
    assert panel["source"] == {"reason": "正在显示源码。关掉「显示源码」后可以用格式与插入。", "hidden": False, "disabled": True,
                               "keyDid": False}
    assert panel["sourceOff"] == {"hidden": True, "disabled": False}
    assert panel["readOnly"] == {"reason": "这个文件只能查看，不能用格式与插入。", "hidden": False, "allOff": True}


@needs_node
def test_booktabs_is_added_to_an_open_main_file_with_the_table(panel):
    assert panel["mainTable"] == ["\\documentclass{ctexart}", "\\usepackage{booktabs}", "\\begin{document}"]


# -- words, glyphs, primaries --------------------------------------------------------------------------------------


def _panel_source() -> str:
    js = LATEX_JS.read_text(encoding="utf-8")
    return js[js.index("// -- 格式与插入: the tools (22.12)"):js.index("  // -- the editor page ---")] + \
        js[js.index("// -- 格式与插入: the panel (22.12)"):js.index("  // -- export to PDF (22.4, B2b)")]


def test_every_new_word_has_its_english_and_none_is_a_glyph():
    from test_v9_language import english_table

    table = english_table()
    said = re.findall(r'\bt\("((?:[^"\\]|\\.)*)"', _panel_source())
    assert len(set(said)) > 80
    missing = [s for s in set(said) if s not in table]
    assert missing == []
    glyphs = ("›", "‹", "▸", "▾", "»", "→", "←", "->", ">", "✓", "✔", "•")
    assert [s for s in said if any(g in s for g in glyphs)] == []
    assert [s for s in said if any(g in str(table[s]) for g in glyphs)] == []
    for zh, en in (("加粗", "Bold"), ("斜体", "Italic"), ("下划线", "Underline"), ("插入", "Insert"), ("版式", "Layout"),
                   ("格式", "Format"), ("文献引用", "Citation"), ("正文段落", "Body text")):
        assert table[zh] == en


def test_the_pickers_share_one_primary_insert_and_cancel_is_a_text_link():
    src = _panel_source()
    assert src.count("btn-primary") == 1 and src.count("insertButton(") == 6      # the helper and its five pickers (A2: 交叉引用, 网址)
    assert 'mkButton(t("取消"), "ws-text-link"' in src
    design = (WEBAPP.parents[2] / "DESIGN.md").read_text(encoding="utf-8")
    present = design[design.index("### 22.7 Presentation"):design.index("### 22.8 Batches")]
    assert "插入" in present and "22.12" in present


def test_the_colours_of_the_panel_are_the_palettes_own_tokens():
    css = (WEBAPP / "workspace.css").read_text(encoding="utf-8")
    panel = css[css.index("/* 22.12: 格式与插入"):]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", panel)


# -- the engine: the facts of the main file and the pickers' lists --------------------------------------------------


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "paper" / "chapters").mkdir(parents=True)
    (root / "figures").mkdir()
    project.init_project(root)
    (root / "paper" / "main.tex").write_text("\\documentclass{ctexrep}\n\\usepackage{booktabs}\n\\begin{document}\n"
                                             "\\input{chapters/one}\n\\end{document}\n", encoding="utf-8")
    (root / "paper" / "chapters" / "one.tex").write_text("\\chapter{One}\nText.\n", encoding="utf-8")
    (root / "figures" / "plot.png").write_bytes(_PNG)
    (root / "figures" / "scan.pdf").write_bytes(b"%PDF-1.4\n")
    (root / "figures" / "notes.txt").write_text("x", encoding="utf-8")
    (root / "paper" / "refs.bib").write_text("@article{smith2020,\n  author = {Smith, John},\n  title = {A {Study}},\n"
                                             "  year = {2020}\n}\n@book{Li2021, author = {Li}, year = 2021}\n"
                                             "@article{SMITH2020, title = {dup}}\n", encoding="utf-8")
    return root


def test_the_file_read_carries_the_main_files_facts_for_a_writable_chapter_and_nothing_for_a_read_only_one(tmp_path):
    root = _project(tmp_path)
    got = latex_edit.read_tex(root, "paper/chapters/one.tex")
    assert got["format"] == {"main": "paper/main.tex", "engine": "xelatex", "folder": "paper",
                             "preamble": "\\documentclass{ctexrep}\n\\usepackage{booktabs}\n", "local": []}
    main = latex_edit.read_tex(root, "paper/main.tex")
    assert main["format"] == {"main": "paper/main.tex", "engine": "xelatex", "folder": "paper", "preamble": None, "local": []}
    assert "format" not in latex_edit.read_tex(root, "paper/main.tex", links=False)
    (root / "loose.tex").write_bytes(b"a\r\nb\nc")                       # mixed endings: read-only
    assert "format" not in latex_edit.read_tex(root, "loose.tex")
    (root / "alone.tex").write_text("Text.\n", encoding="utf-8")
    assert latex_edit.read_tex(root, "alone.tex")["format"] == {"main": None, "engine": None, "folder": "", "preamble": None, "local": []}


def test_the_pickers_list_the_projects_images_and_bib_entries_and_write_nothing(tmp_path):
    root = _project(tmp_path)
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if ".researchos" not in p.parts)
    images = latex_edit.insertables(root, "paper/chapters/one.tex", "images")
    assert images == {"images": [{"path": "figures/plot.png", "thumb": True}, {"path": "figures/scan.pdf", "thumb": False}], "more": False}
    bib = latex_edit.insertables(root, "paper/chapters/one.tex", "bib")
    assert bib["files"] == ["paper/refs.bib"]
    assert bib["entries"] == [{"key": "smith2020", "author": "Smith, John", "year": "2020", "title": "A Study", "file": "paper/refs.bib"},
                              {"key": "Li2021", "author": "Li", "year": "2021", "title": "", "file": "paper/refs.bib"}]
    with pytest.raises(latex_edit.WorkspaceError):
        latex_edit.insertables(root, "paper/chapters/one.tex", "everything")
    with pytest.raises(latex_edit.WorkspaceError):
        latex_edit.insertables(root, "../outside.tex", "images")
    after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if ".researchos" not in p.parts)
    assert after == before


# -- a real compile: every insertion, XeLaTeX (ctexrep) and pdfLaTeX (article) --------------------------------------

def _png_bytes(width: int = 8, height: int = 6) -> bytes:
    """A real PNG (grey, 8-bit) that pdfLaTeX and XeLaTeX both read."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes((x * 30) % 256 for x in range(width)) for _y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


_PNG = _png_bytes()

_BUILD = r"""
const vm = require('vm'), fs = require('fs');
const ctx = vm.createContext({});
vm.runInContext(fs.readFileSync(__CM__, 'utf8'), ctx);
global.window = {ROCodeMirror: ctx.ROCodeMirror};
eval(fs.readFileSync(__LATEX__, 'utf8'));
const CM = ctx.ROCodeMirror, P = window.ROLatex._pure;
const build = (start, chinese) => {
  let st = CM.EditorState.create({doc: start});
  const facts = () => P.preambleFacts(st.doc.toString().split('\\begin{document}')[0]);
  const apply = (r) => { if (!r || r.refused) throw new Error('refused: ' + JSON.stringify(r) + ' in ' + st.doc.toString()); st = st.update({changes: r.changes, selection: r.selection}).state; };
  const select = (word) => { const at = st.doc.toString().indexOf(word); if (at < 0) throw new Error('no ' + word); st = st.update({selection: {anchor: at, head: at + word.length}}).state; };
  const cursor = (word, end) => { const at = st.doc.toString().indexOf(word); if (at < 0) throw new Error('no ' + word); st = st.update({selection: {anchor: at + (end ? word.length : 0)}}).state; };
  select('BOLD'); apply(P.styleSpec(st, 'textbf'));
  select('ITALIC'); apply(P.styleSpec(st, 'textit'));
  select('UNDER'); apply(P.styleSpec(st, 'underline'));
  cursor('EMPTY'); apply(P.styleSpec(st, 'textbf'));
  st = st.update({changes: {from: st.selection.main.head, insert: 'typed'}}).state;
  select('SANS'); apply(P.fontSpec(st, 'textsf'));
  select('MONO'); apply(P.fontSpec(st, 'texttt'));
  select('SERIF'); apply(P.fontSpec(st, chinese ? 'songti' : 'textrm'));
  if (chinese) { select('HEI'); apply(P.fontSpec(st, 'heiti')); select('KAI'); apply(P.fontSpec(st, 'kaishu')); select('FANG'); apply(P.fontSpec(st, 'fangsong')); }
  select('SMALL'); apply(P.sizeSpec(st, 'small'));
  select('LARGE'); apply(P.sizeSpec(st, 'Large'));
  cursor('Chapter heading'); if (chinese) apply(P.headingSpec(st, 'chapter', facts().chapters)); else apply(P.headingSpec(st, 'section', facts().chapters));
  cursor('Section heading'); apply(P.headingSpec(st, 'section', facts().chapters));
  cursor('Sub heading'); apply(P.headingSpec(st, 'subsection', facts().chapters));
  cursor('Subsub heading'); apply(P.headingSpec(st, 'subsubsection', facts().chapters));
  cursor('FIGURE HERE', true);
  const f = facts();
  apply(P.figureSpec(st, {path: P.figurePath('figures/fig1.png', 'paper', f.graphicspath), file: 'figures/fig1.png', caption: 'A plot & 50% of it', half: false, graphicx: f.graphicx, isMain: true}));
  cursor('TABLE HERE', true);
  apply(P.tableSpec(st, {rows: 3, cols: 3, header: true, caption: 'Results', booktabs: facts().booktabs, isMain: true}));
  st = st.update({changes: {from: st.selection.main.head, insert: 'cell'}}).state;
  cursor('TABLE TWO', true);
  apply(P.tableSpec(st, {rows: 2, cols: 2, header: false, caption: '', booktabs: facts().booktabs, isMain: true}));
  select('x^2'); apply(P.mathSpec(st, false));
  select('E = mc^2'); apply(P.mathSpec(st, true));
  cursor('MATH HERE', true); apply(P.mathSpec(st, false)); st = st.update({changes: {from: st.selection.main.head, insert: 'y'}}).state;
  select('first item\nsecond item'); apply(P.listSpec(st, 'itemize'));
  cursor('NUMBERED HERE', true); apply(P.listSpec(st, 'enumerate')); st = st.update({changes: {from: st.selection.main.head, insert: 'one'}}).state;
  cursor('NOTE HERE', true); apply(P.footnoteSpec(st)); st = st.update({changes: {from: st.selection.main.head, insert: 'A note.'}}).state;
  cursor('CITE HERE', true); apply(P.citeSpec(st, ['knuth1984', 'lamport1994']));
  return st.doc.toString();
};
const BODY = 'Chapter heading\n\nSection heading\n\nThe BOLD and ITALIC and UNDER words, EMPTY here.\nSANS MONO SERIF HEI KAI FANG SMALL LARGE.\n\n' +
  'Sub heading\n\nSubsub heading\n\nText before. FIGURE HERE\nTABLE HERE\nTABLE TWO\nA formula x^2 and E = mc^2 inline. MATH HERE\n\n' +
  'first item\nsecond item\n\nNUMBERED HERE\nNOTE HERE and CITE HERE.\n\n\\bibliographystyle{plain}\n\\bibliography{refs}\n';
const xe = build('\\documentclass{ctexrep}\n\\begin{document}\n中文正文。\n\n' + BODY + '\\end{document}\n', true);
const pdf = build('\\documentclass{article}\n\\usepackage[T1]{fontenc}\n\\graphicspath{{../figures/}}\n\\begin{document}\n' +
  BODY.replace(' HEI KAI FANG', '') + '\\end{document}\n', false);
console.log(JSON.stringify({xe, pdf}));
"""

_BIB = """@book{knuth1984, author = {Donald E. Knuth}, title = {The {\\TeX}book}, publisher = {Addison-Wesley}, year = {1984}}
@book{lamport1994, author = {Leslie Lamport}, title = {{\\LaTeX}: A Document Preparation System}, publisher = {Addison-Wesley}, year = {1994}}
"""


@pytest.fixture(scope="module")
def built() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    script = (_BUILD.replace("__CM__", json.dumps(str(WEBAPP / "vendor" / "codemirror.js")))
              .replace("__LATEX__", json.dumps(str(LATEX_JS))))
    done = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_the_documents_built_by_the_tools_hold_every_insertion(built):
    xe, pdf = built["xe"], built["pdf"]
    for doc in (xe, pdf):
        for piece in ("\\textbf{BOLD}", "\\textit{ITALIC}", "\\underline{UNDER}", "\\textbf{typed}", "\\textsf{SANS}", "\\texttt{MONO}",
                      "{\\small SMALL}", "{\\Large LARGE}", "\\section{Section heading}", "\\subsection{Sub heading}",
                      "\\subsubsection{Subsub heading}", "\\usepackage{graphicx}", "\\usepackage{booktabs}", "\\toprule\ncell& & \\\\\n\\midrule",
                      "$x^2$", "\\begin{equation}\nE = mc^2\n\\end{equation}", "$y$", "\\begin{itemize}\n  \\item first item\n  \\item second item\n\\end{itemize}",
                      "\\begin{enumerate}\n  \\item one\n\\end{enumerate}", "\\footnote{A note.}", "\\cite{knuth1984,lamport1994}",
                      "\\caption{A plot \\& 50\\% of it}"):
            assert piece in doc, piece
    assert "\\chapter{Chapter heading}" in xe and "{\\songti SERIF}" in xe and "{\\heiti HEI}" in xe and "{\\kaishu KAI}" in xe
    assert "\\includegraphics[width=\\linewidth]{../figures/fig1.png}" in xe
    assert "\\includegraphics[width=\\linewidth]{fig1.png}" in pdf and "\\textrm{SERIF}" in pdf     # through \graphicspath


def _compile(tmp_path: Path, text: str, engine: str) -> tuple[subprocess.CompletedProcess, Path]:
    root = tmp_path / engine
    (root / "paper").mkdir(parents=True)
    (root / "figures").mkdir()
    (root / "figures" / "fig1.png").write_bytes(_PNG)
    (root / "paper" / "refs.bib").write_text(_BIB, encoding="utf-8")
    (root / "paper" / "main.tex").write_text(text, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"PATH": f"{TEXBIN}:/usr/bin:/bin", "HOME": str(home), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}
    done = subprocess.run([LATEXMK, "-norc", "-" + ("xelatex" if engine == "xelatex" else "pdf"), "-interaction=nonstopmode",
                           "-halt-on-error", "-file-line-error", "-no-shell-escape", "main.tex"],
                          cwd=root / "paper", env=env, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    return done, root / "paper"


@needs_node
@pytest.mark.skipif(LATEXMK is None, reason="latexmk is not installed")
@pytest.mark.parametrize("engine", ["xelatex", "pdflatex"])
def test_a_document_with_every_insertion_compiles_to_a_pdf_without_an_error(built, tmp_path, engine):
    """The tools' own output, as the person would save it, compiled by latexmk in the main file's folder (the export's
    working directory, 22.4) with bibtex: a PDF, no error, no undefined citation or reference."""
    done, folder = _compile(tmp_path, built["xe" if engine == "xelatex" else "pdf"], engine)
    log = (folder / "main.log").read_text(encoding="utf-8", errors="replace") if (folder / "main.log").exists() else ""
    assert done.returncode == 0, done.stdout[-3000:] + log[-3000:]
    assert (folder / "main.pdf").read_bytes().startswith(b"%PDF-")
    assert not re.search(r"^! |LaTeX Error|Undefined control sequence|Citation `[^']*' .*undefined|Reference `[^']*' .*undefined",
                         log, re.M), log[-3000:]
    assert os.path.getsize(folder / "main.pdf") > 1000

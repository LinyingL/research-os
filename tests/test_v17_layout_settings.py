"""DESIGN.md 22.12 「版式」 (task V17, steps A2 and A3): the document's layout settings -- 页边距, 分栏, 页码, 超链接, 正文字号,
行距, 首行缩进, 段间距, 章节编号, 图表名称, 图表编号 (纸张 taken out by the researcher, A3) -- read from the main file's preamble and written as the editor's own transaction; a setting written
in a form 版式 does not take shown as 「自定义」 with its line, never guessed; a chapter file's layout shown and not changed;
and the paragraph tools of 格式 (对齐, 引文块, 分页, 目录, 交叉引用, 网址). Run under node on the vendored CodeMirror, the
panel on the page as test_v17_format_panel drives it, the engine's facts, and real compiles: every setting at every value
on a ctexart (XeLaTeX) and an article (pdfLaTeX) document, probe compiles that measure the paper, the text width,
\\baselineskip, \\parindent and \\parskip, and the numbering styles compiled on ctexart, ctexrep, article and report and
read back from the PDF by pdftotext. Every test names its scenario."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from researchos import latex_edit, project
from test_v17_latex_editor import _DRIVER, NODE, WEBAPP

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
TEXBIN = Path("/Library/TeX/texbin")
LATEXMK = shutil.which("latexmk") or (str(TEXBIN / "latexmk") if (TEXBIN / "latexmk").exists() else None)
LATEX_JS = WEBAPP / "latex.js"

_HEAD = r"""
const vm = require('vm'), fs = require('fs');
const ctx = vm.createContext({});
vm.runInContext(fs.readFileSync(__CM__, 'utf8'), ctx);
global.window = {ROCodeMirror: ctx.ROCodeMirror};
eval(fs.readFileSync(__LATEX__, 'utf8'));
const CM = ctx.ROCodeMirror, P = window.ROLatex._pure;
const BODY = '\\begin{document}\nText.\n\\end{document}\n';
const make = (pre, sep, body) => {
  const text = pre + (body || BODY), at = text.indexOf('Text.');
  return CM.EditorState.create({doc: sep ? text.split('\n').join(sep) : text, selection: {anchor: at},
    extensions: [CM.history()].concat(sep ? [CM.EditorState.lineSeparator.of(sep), P.breakFilter(CM, sep)] : [])});
};
"""


def _node(script: str) -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    src = (_HEAD + script).replace("__CM__", json.dumps(str(WEBAPP / "vendor" / "codemirror.js"))) \
        .replace("__LATEX__", json.dumps(str(LATEX_JS)))
    done = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# -- reading: each recognised form, each form 版式 does not take -----------------------------------------------------

_READ = r"""
// each setting as the page shows it: its value, or MINE with the line (and the file) where it is the person's own
const read = (pre, local) => {
  const L = P.layoutOf(pre, local);
  if (L.why) return {why: L.why};
  const o = {};
  for (const k in L.settings) {
    const s = L.settings[k];
    o[k] = s.mine ? 'mine:' + (s.mine.file ? s.mine.file + ':' : '') + s.mine.line + (s.mine.why ? ':class' : '') : s.value;
    if (k === 'margin' && s.value && !s.mine) o.margins = [s.top, s.bottom, s.left, s.right];
    if (k === 'spacing' && s.n && !s.mine) o.n = s.n;
  }
  return o;
};
const out = {};
out.none = read('\\documentclass{article}\n\\usepackage{amsmath}\n');
out.recognised = read('\\documentclass[a4paper, 12pt,twocolumn]{article}\n\\usepackage[margin=2.5cm]{geometry}\n\\usepackage{setspace}\n' +
  '\\setstretch{1.5}\n\\setlength{\\parindent}{0pt}\n\\usepackage[skip=\\baselineskip,indent]{parskip}\n\\usepackage{nopageno}\n\\usepackage[hidelinks]{hyperref}\n');
out.otherForms = read('\\documentclass[b5paper,onecolumn,11pt]{article}\n\\usepackage{geometry}\n\\geometry{top=2cm,bottom=25mm,left=1in,right=3cm}\n' +
  '\\usepackage[doublespacing]{setspace}\n\\setlength\\parindent{2em}\n\\setlength{\\parskip}{0.5\\baselineskip}\n\\pagestyle{plain}\n\\usepackage{amsmath,hyperref}\n');
out.geometryPaper = read('\\documentclass{article}\n\\usepackage[paper=a4paper,margin=3cm]{geometry}\n');
out.paperUntouched = read('\\documentclass{article}\n\\setlength{\\paperwidth}{20cm}\n\\paperheight=25cm\n\\usepackage{a4wide}\n');
out.named = [10, 11, 12].map((pt) => read('\\documentclass[' + pt + 'pt]{article}\n\\usepackage{setspace}\n\\onehalfspacing\n').n);
out.ctex = read('\\documentclass[zihao=-4]{ctexart}\n\\linespread{1.95}\n');
out.ctexOwn = read('\\documentclass{ctexart}\n\\usepackage{setspace}\n\\setstretch{1.3}\n');
out.ctexOption = read('\\documentclass[zihao=5,linespread=2.6]{ctexart}\n');
out.ctexPackage = read('\\documentclass{article}\n\\usepackage[zihao=-4]{ctex}\n');
out.commented = read('\\documentclass{article}\n% \\usepackage[margin=1cm]{geometry}\n\\usepackage{amsmath} % \\onehalfspacing\n');
// forms 版式 does not take: shown as the person's own, with their line
out.inMacro = read('\\documentclass{article}\n\\newcommand{\\spacing}{\\onehalfspacing}\n');
out.atBegin = read('\\documentclass{article}\n\\usepackage{amsmath}\n\\AtBeginDocument{\\setlength{\\parindent}{0pt}}\n');
out.conditional = read('\\documentclass{article}\n\\ifdefined\\draft\n\\pagestyle{empty}\n\\fi\n\\usepackage{amsmath}\n');
out.newif = read('\\documentclass{article}\n\\newif\\ifdraft\n\\pagestyle{empty}\n');
out.ifthenelse = read('\\documentclass{article}\n\\usepackage{ifthen}\n\\ifthenelse{\\equal{a}{b}}{}{}\n\\pagestyle{empty}\n');
out.conflicting = read('\\documentclass{article}\n\\usepackage{setspace}\n\\setstretch{1.5}\n\\setstretch{2}\n');
out.agreeing = read('\\documentclass[a4paper]{article}\n\\usepackage[margin=2cm]{geometry}\n\\geometry{margin=2cm}\n');
out.disagreeing = read('\\documentclass[a4paper]{article}\n\\usepackage[margin=2cm]{geometry}\n\\geometry{margin=3cm}\n');
out.zihaoOnArticle = read('\\documentclass[zihao=-4]{article}\n');
out.zihaoFour = read('\\documentclass[zihao=4]{ctexart}\n');
out.bothSizes = read('\\documentclass[12pt,zihao=5]{ctexart}\n');
out.odd = read('\\documentclass[a5paper,9pt]{article}\n\\usepackage[margin=2cm,includehead]{geometry}\n\\linespread{1.3}\n' +
  '\\setlength{\\parindent}{1.5em}\n\\setlength{\\parskip}{6pt}\n\\pagestyle{headings}\n');
out.halfMargins = read('\\documentclass{article}\n\\usepackage[left=2cm,right=2cm]{geometry}\n');
out.marginPackage = read('\\documentclass{article}\n\\usepackage{fullpage}\n');
out.primitive = read('\\documentclass{article}\n\\parindent=0pt\n\\parskip 6pt\n');
out.usedNotSet = read('\\documentclass{article}\n\\newlength{\\x}\n\\setlength{\\x}{\\parindent}\n');
out.parskipNoIndent = read('\\documentclass{article}\n\\usepackage{parskip}\n');
out.sameLine = read('\\documentclass{article}\n\\usepackage{setspace}\\onehalfspacing\n');
out.hiddenPackage = read('\\documentclass{article}\n\\ifdefined\\x\\usepackage{hyperref}\\fi\n');
out.local = read('\\documentclass{article}\n\\usepackage{mystyle}\n', [{path: 'paper/mystyle.sty', text: '\\ProvidesPackage{mystyle}\n% \\geometry{margin=1cm}\n\\RequirePackage[colorlinks]{hyperref}\n\\setlength{\\parindent}{0pt}\n'}]);
out.unknownClass = read('\\documentclass[12pt,twocolumn]{elsarticle}\n\\usepackage{setspace}\n\\setstretch{2}\n');
out.beamer = read('\\documentclass{beamer}\n');
out.noClass = read('\\usepackage{amsmath}\n');
// 章节编号, 图表名称, 图表编号 (A3)
const ZH = '\\renewcommand{\\thesection}{\\chinese{section}}\n\\renewcommand{\\thesubsection}{（\\chinese{subsection}）}\n' +
  '\\renewcommand{\\thesubsubsection}{\\arabic{subsubsection}}\n\\ctexset{section/name={,、},section/aftername={},subsection/aftername={},subsubsection/name={,.}}\n';
const WITHIN = '\\counterwithin{figure}{section}\n\\counterwithin{table}{section}\n\\renewcommand{\\thefigure}{\\arabic{section}-\\arabic{figure}}\n' +
  '\\renewcommand{\\thetable}{\\arabic{section}-\\arabic{table}}\n';
const THEH = '\\providecommand{\\theHfigure}{\\theHsection.\\arabic{figure}}\n\\providecommand{\\theHtable}{\\theHsection.\\arabic{table}}\n';
out.numbers = {
  ctexDefault: read('\\documentclass{ctexart}\n'), articleDefault: read('\\documentclass{article}\n'),
  zh: read('\\documentclass{ctexart}\n' + ZH), zhSpaced: read('\\documentclass{ctexrep}\n' + ZH.replace('{\\chinese{section}}', '{ \\chinese {section} }').replace(',section/aftername', ', section/aftername')),
  zhShared: read('\\documentclass{ctexart}\n' + ZH.replace('\\ctexset{', '\\ctexset{section/format+=\\raggedright,')),
  zhPackage: read('\\documentclass{article}\n\\usepackage[heading=true]{ctex}\n' + ZH),
  zhPartial: read('\\documentclass{ctexart}\n' + ZH.split('\n').slice(0, 2).join('\n') + '\n'),
  zhNoKeys: read('\\documentclass{ctexart}\n' + ZH.split('\n').slice(0, 3).join('\n') + '\n'),
  zhOnArticle: read('\\documentclass{article}\n\\usepackage{xeCJK}\n' + ZH),
  none: read('\\documentclass{article}\n\\setcounter{secnumdepth}{0}\n'), noneLower: read('\\documentclass{ctexart}\n\\setcounter{secnumdepth}{-2}\n'),
  noneReport: read('\\documentclass{report}\n\\setcounter{secnumdepth}{-1}\n'), zeroReport: read('\\documentclass{ctexrep}\n\\setcounter{secnumdepth}{0}\n'),
  depth: read('\\documentclass{article}\n\\setcounter{secnumdepth}{2}\n'), depthMacro: read('\\documentclass{article}\n\\AtBeginDocument{\\setcounter{secnumdepth}{0}}\n'),
  roman: read('\\documentclass{article}\n\\renewcommand{\\thesection}{\\Roman{section}}\n'), seccnt: read('\\documentclass{article}\n\\makeatletter\n\\def\\@seccntformat#1{}\n\\makeatother\n'),
  titlesec: read('\\documentclass{article}\n\\usepackage{titlesec}\n'), nested: read('\\documentclass{ctexart}\n\\ctexset{section={name={第,节}}}\n'),
  ctexDepth: read('\\documentclass{ctexart}\n\\ctexset{secnumdepth=0}\n'), both: read('\\documentclass{ctexart}\n' + ZH + '\\setcounter{secnumdepth}{0}\n'),
  unknown: read('\\documentclass{elsarticle}\n\\setcounter{secnumdepth}{0}\n'),
};
out.names = {
  en: read('\\documentclass{ctexart}\n\\renewcommand{\\figurename}{Figure}\n\\renewcommand{\\tablename}{Table}\n'),
  ctexKeys: read('\\documentclass{ctexrep}\n\\ctexset{figurename = Figure, tablename={Table}}\n'),
  zh: read('\\documentclass{article}\n\\usepackage{xeCJK}\n\\renewcommand\\figurename{图}\n\\renewcommand\\tablename{表}\n'),
  zhNoCJK: read('\\documentclass{article}\n\\renewcommand{\\figurename}{图}\n\\renewcommand{\\tablename}{表}\n'),
  one: read('\\documentclass{article}\n\\renewcommand{\\figurename}{Fig.}\n'), def: read('\\documentclass{article}\n\\def\\figurename{Figure}\n'),
  babel: read('\\documentclass{article}\n\\usepackage[english]{babel}\n'), captionsetup: read('\\documentclass{article}\n\\usepackage{caption}\n\\captionsetup[figure]{name=Fig.}\n'),
  captionOption: read('\\documentclass{article}\n\\usepackage[figurename=Fig.]{caption}\n'), captionPlain: read('\\documentclass{article}\n\\usepackage[font=small]{caption}\n'),
};
out.capnum = {
  within: read('\\documentclass{article}\n' + WITHIN + THEH), withoutAnchors: read('\\documentclass{ctexart}\n' + WITHIN),
  chapter: read('\\documentclass{ctexrep}\n\\renewcommand{\\thefigure}{\\thechapter-\\arabic{figure}}\n\\renewcommand{\\thetable}{\\thechapter-\\arabic{table}}\n'),
  continuous: read('\\documentclass{report}\n\\counterwithout{figure}{chapter}\n\\counterwithout{table}{chapter}\n'),
  onlyFigure: read('\\documentclass{article}\n\\counterwithin{figure}{section}\n'), numberwithin: read('\\documentclass{article}\n\\usepackage{amsmath}\n\\numberwithin{figure}{section}\n'),
  starred: read('\\documentclass{article}\n\\counterwithin*{figure}{section}\n'), dotted: read('\\documentclass{article}\n\\counterwithin{figure}{section}\n\\counterwithin{table}{section}\n'),
  articleForm: read('\\documentclass{report}\n' + WITHIN), twice: read('\\documentclass{article}\n' + WITHIN + WITHIN),
  equation: read('\\documentclass{article}\n\\counterwithin{equation}{section}\n'),
};
out.localNumbers = read('\\documentclass{ctexart}\n\\usepackage{mystyle}\n', [{path: 'paper/mystyle.sty', text: '\\ProvidesPackage{mystyle}\n\\fancyhead[L]{\\thesection}\n\\renewcommand\\thefigure{\\thesection-\\arabic{figure}}\n\\ctexset{figurename=Fig.}\n\\setcounter{secnumdepth}{1}\n'}]);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def readings() -> dict:
    return _node(_READ)


@needs_node
def test_with_nothing_written_every_setting_reads_as_the_class_default(readings):
    assert readings["none"] == {"margin": "", "columns": "", "pagestyle": "", "hyperref": "off", "size": "",
                                "spacing": "", "indent": "", "parskip": "", "secnum": "", "capname": "en", "capnum": ""}


@needs_node
def test_each_recognised_form_is_read_as_its_value(readings):
    assert readings["recognised"] == {"margin": "2.5", "margins": [2.5, 2.5, 2.5, 2.5], "columns": "twocolumn",
                                      "pagestyle": "empty", "hyperref": "on", "size": "12pt", "spacing": "1.5", "n": 1.5,
                                      "indent": "0pt", "parskip": "one", "secnum": "", "capname": "en", "capnum": ""}
    other = readings["otherForms"]
    assert "paper" not in other and other["columns"] == "" and other["size"] == "11pt"
    assert other["margin"] == "custom" and other["margins"] == [2, 2.5, 2.54, 3]            # cm, mm and in, in \geometry{}
    assert other["spacing"] == "custom" and other["n"] == 1.62                               # setspace's own double at 11pt
    assert other["indent"] == "2em" and other["parskip"] == "half" and other["pagestyle"] == "plain"
    assert other["hyperref"] == "on"                                                         # in a shared \usepackage
    assert readings["geometryPaper"]["margin"] == "3"                                        # geometry's paper is no margin
    assert readings["commented"]["margin"] == "" and readings["commented"]["spacing"] == ""


@needs_node
def test_line_spacing_reads_as_lines_of_the_documents_own_single_line(readings):
    # setspace's \onehalfspacing is 1.25, 1.213 and 1.241 times the single line at 10, 11 and 12 pt (setspace.sty)
    assert readings["named"] == [1.25, 1.21, 1.24]
    # ctex's single line is its own \linespread{1.3}: 1.95 is 1.5 lines, a stretch of 1.3 single, linespread=2.6 double
    assert readings["ctex"]["spacing"] == "1.5" and readings["ctex"]["size"] == "zihao=-4"
    assert readings["ctexOwn"]["spacing"] == "1" and readings["ctexOption"]["spacing"] == "2"
    assert readings["ctexOption"]["size"] == "zihao=5" and readings["ctexPackage"]["size"] == "zihao=-4"


@needs_node
def test_a_setting_in_a_form_layout_does_not_take_is_the_persons_own_with_its_line(readings):
    assert readings["inMacro"]["spacing"] == "mine:2"
    assert readings["atBegin"]["indent"] == "mine:3"
    assert readings["conditional"]["pagestyle"] == "mine:3"
    assert readings["newif"]["pagestyle"] == "empty"                          # \newif\ifdraft opens no condition
    assert readings["ifthenelse"]["pagestyle"] == "empty"                     # \ifthenelse takes braces, no \fi
    assert readings["conflicting"]["spacing"] == "mine:4"
    assert readings["agreeing"]["margin"] == "2"                              # two places that agree are one setting
    assert readings["disagreeing"]["margin"] == "mine:3"
    odd = readings["odd"]
    assert odd["size"] == "mine:1" and odd["margin"] == "mine:2" and odd["spacing"] == "custom"
    assert odd["indent"] == "mine:4" and odd["parskip"] == "mine:5" and odd["pagestyle"] == "mine:6"
    assert readings["halfMargins"]["margin"] == "mine:2"                      # top and bottom unsaid
    assert readings["marginPackage"]["margin"] == "mine:2"
    assert readings["primitive"]["indent"] == "mine:2" and readings["primitive"]["parskip"] == "mine:3"
    assert readings["usedNotSet"]["indent"] == ""                             # \parindent only read
    assert readings["parskipNoIndent"]["parskip"] == "half" and readings["parskipNoIndent"]["indent"] == "mine:2"
    assert readings["sameLine"]["spacing"] == "mine:2"
    assert readings["hiddenPackage"]["hyperref"] == "mine:2"


@needs_node
def test_class_options_are_read_only_where_the_class_takes_them(readings):
    assert readings["zihaoOnArticle"]["size"] == "mine:1"                     # zihao is ctex's
    assert readings["zihaoFour"]["size"] == "mine:1"                          # ctex refuses zihao=4 (measured)
    assert readings["bothSizes"]["size"] == "mine:1"                          # never a pt and a zihao
    unknown = readings["unknownClass"]
    assert unknown["size"] == "mine:1:class" and unknown["columns"] == "mine:1:class"
    assert unknown["secnum"] == "mine:1:class" and unknown["capnum"] == "mine:1:class"      # chapters or not: unknown
    assert unknown["spacing"] == "2"                                          # what does not touch the class still works
    assert readings["beamer"] == {"why": "幻灯片（beamer）不用这些版式设置。"}
    assert readings["noClass"] == {"why": "主文件里没有找到 \\documentclass。"}


@needs_node
def test_a_project_sty_that_touches_a_setting_makes_it_its_own_with_file_and_line(readings):
    local = readings["local"]
    assert local["hyperref"] == "mine:paper/mystyle.sty:3" and local["indent"] == "mine:paper/mystyle.sty:4"
    assert local["margin"] == ""                                              # its comment is not a setting


@needs_node
def test_paper_is_no_setting_any_more_and_a_paper_written_by_hand_is_left_alone(readings):
    # the researcher, 2026-11-14: 纸张 "这个不需要！" -- no control, and nothing about the paper makes another setting its own
    for name in ("none", "recognised", "otherForms", "geometryPaper", "odd", "unknownClass"):
        assert "paper" not in readings[name]
    assert readings["paperUntouched"]["margin"] == "mine:4"                   # a4wide sets the margins; the paper lines are ignored
    assert set(readings["paperUntouched"]) == {"margin", "columns", "pagestyle", "hyperref", "size", "spacing", "indent", "parskip",
                                               "secnum", "capname", "capnum"}


@needs_node
def test_heading_numbers_read_as_the_tools_own_lines_or_the_persons_own_with_their_line(readings):
    n = readings["numbers"]
    assert n["ctexDefault"]["secnum"] == "" and n["articleDefault"]["secnum"] == ""
    # 一、（一）1.: the three \renewcommand lines and ctex's four keys, spaces aside, the keys among others of a \ctexset
    assert n["zh"]["secnum"] == "zh" and n["zhSpaced"]["secnum"] == "zh" and n["zhShared"]["secnum"] == "zh"
    assert n["zhPackage"]["secnum"] == "zh"                                   # the ctex package with heading=true
    assert n["zhPartial"]["secnum"] == "mine:2" and n["zhNoKeys"]["secnum"] == "mine:2"
    assert n["zhOnArticle"]["secnum"] == "mine:3"                             # \chinese and \ctexset are ctex's
    # 不编号: secnumdepth at or below a section's level (0), with chapters below a chapter's (-1)
    assert n["none"]["secnum"] == "none" and n["noneLower"]["secnum"] == "none" and n["noneReport"]["secnum"] == "none"
    assert n["zeroReport"]["secnum"] == "mine:2"                              # chapters still numbered: not offered
    assert n["depth"]["secnum"] == "mine:2" and n["depthMacro"]["secnum"] == "mine:2"
    assert n["roman"]["secnum"] == "mine:2" and n["seccnt"]["secnum"] == "mine:3" and n["titlesec"]["secnum"] == "mine:2"
    assert n["nested"]["secnum"] == "mine:2" and n["ctexDepth"]["secnum"] == "mine:2"
    assert n["both"]["secnum"] == "mine:6"                                    # 一、 and 不编号 at once disagree
    assert n["unknown"]["secnum"] == "mine:1:class"                           # with chapters or without: not known


@needs_node
def test_caption_names_and_numbers_read_as_the_tools_own_lines_or_the_persons_own(readings):
    nm = readings["names"]
    assert readings["numbers"]["ctexDefault"]["capname"] == "zh" and readings["numbers"]["articleDefault"]["capname"] == "en"
    assert nm["en"]["capname"] == "en" and nm["ctexKeys"]["capname"] == "en" and nm["zh"]["capname"] == "zh"
    assert nm["zhNoCJK"]["capname"] == "mine:2"                               # 图 cannot be set without a Chinese font
    assert nm["one"]["capname"] == "mine:2" and nm["def"]["capname"] == "mine:2"
    assert nm["babel"]["capname"] == "mine:2:class"                           # babel names them again at \begin{document}
    assert nm["captionsetup"]["capname"] == "mine:3" and nm["captionOption"]["capname"] == "mine:2"
    assert nm["captionPlain"]["capname"] == "en"
    c = readings["capnum"]
    assert c["within"]["capnum"] == "within" and c["withoutAnchors"]["capnum"] == "within"
    assert c["chapter"]["capnum"] == "within" and c["continuous"]["capnum"] == "continuous"
    assert c["onlyFigure"]["capnum"] == "mine:2" and c["numberwithin"]["capnum"] == "mine:3" and c["starred"]["capnum"] == "mine:2"
    assert c["dotted"]["capnum"] == "mine:2"                                  # 图 1.1 is no choice offered
    assert c["articleForm"]["capnum"] == "mine:2" and c["twice"]["capnum"] == "mine:2"
    assert c["equation"]["capnum"] == ""                                      # equations are not 图表编号's
    local = readings["localNumbers"]
    assert local["capnum"] == "mine:paper/mystyle.sty:3" and local["capname"] == "mine:paper/mystyle.sty:4"
    assert local["secnum"] == "mine:paper/mystyle.sty:5"                      # \thesection only read on line 2: not a setting


# -- writing: in place, new lines after the last \usepackage, removing only what the tool writes --------------------

_WRITE = r"""
// the preamble after writing `key` = `value`: {pre, steps}, {refused}, or null when nothing is to change
const write = (pre, key, value, opts, sep, body) => {
  const st = make(pre, sep, body), r = P.layoutSpec(st, key, value, opts || {});
  if (!r) return null;
  if (r.refused) return {refused: r.refused};
  const tr = st.update({changes: r.changes, selection: r.selection}), n = tr.state, text = n.doc.toString();
  return {pre: text.slice(0, text.indexOf('\\begin{document}')), file: n.doc.sliceString(0, n.doc.length, n.lineBreak),
          cursor: text.slice(n.selection.main.head, n.selection.main.head + 5)};
};
const A = '\\documentclass{article}\n\\usepackage{amsmath}\n';
const FULL = '\\documentclass[a4paper,12pt]{article}\n\\usepackage[margin=2cm]{geometry} % page\n\\usepackage{setspace}\n\\onehalfspacing\n' +
  '\\setlength{\\parindent}{0pt}\n\\usepackage[skip=0.5\\baselineskip,indent]{parskip}\n\\pagestyle{plain}\n\\usepackage[hidelinks]{hyperref}\n\\title{T}\n';
const out = {};
out.add = {
  margin: write(A, 'margin', {preset: '2.5'}),
  custom: write(A, 'margin', {preset: 'custom', top: 2, bottom: 2.5, left: 3, right: 1.75}),
  columns: write(A, 'columns', 'twocolumn'), plain: write(A, 'pagestyle', 'plain'), empty: write(A, 'pagestyle', 'empty'),
  hyperref: write(A, 'hyperref', 'on'), size: write(A, 'size', '11pt'), spacing: write(A, 'spacing', '1.5'),
  spacingCustom: write(A, 'spacing', 'custom', {n: 1.4}), indent: write(A, 'indent', '2em'), parskip: write(A, 'parskip', 'one'),
  ctexSpacing: write('\\documentclass{ctexart}\n', 'spacing', '2'), ctexSize: write('\\documentclass{ctexart}\n', 'size', 'zihao=-4'),
  ctexPackageSize: write('\\documentclass{article}\n\\usepackage{ctex}\n', 'size', 'zihao=5'),
  noPackages: write('\\documentclass{article}\n\\title{T}\n', 'indent', '0pt'),
  beforeHyperref: write('\\documentclass{article}\n\\usepackage{amsmath}\n\\usepackage[hidelinks]{hyperref}\n', 'margin', {preset: '3'}),
  hyperrefBeforeCleveref: write('\\documentclass{article}\n\\usepackage{amsmath}\n\\usepackage{cleveref}\n', 'hyperref', 'on'),
  setspaceLoaded: write('\\documentclass{article}\n\\usepackage{setspace}\n\\title{x}\n', 'spacing', '2'),
  geometryShared: write('\\documentclass{article}\n\\usepackage{amsmath,geometry}\n', 'margin', {preset: '2'}),
  geometryBare: write('\\documentclass{article}\n\\usepackage{geometry}\n', 'margin', {preset: '2'}),
};
out.change = {
  margin: write(FULL, 'margin', {preset: '3'}), size: write(FULL, 'size', '10pt'),
  spacing: write(FULL, 'spacing', '2'), indent: write(FULL, 'indent', '2em'), parskip: write(FULL, 'parskip', 'one'),
  pagestyle: write(FULL, 'pagestyle', 'empty'), columns: write(FULL, 'columns', 'twocolumn'),
  marginCustom: write(FULL, 'margin', {preset: 'custom', top: 1, bottom: 2, left: 3, right: 4}),
  zihao: write('\\documentclass[a4paper, 12pt]{ctexart}\n', 'size', 'zihao=-4'),
  toPt: write('\\documentclass[zihao=-4,a4paper]{ctexart}\n', 'size', '12pt'),
  linespread: write('\\documentclass{article}\n\\linespread{1.3}\n', 'spacing', '1.25'),
  setspaceOption: write('\\documentclass{article}\n\\usepackage[onehalfspacing]{setspace}\n', 'spacing', '2'),
  ctexOption: write('\\documentclass[linespread=1.3]{ctexart}\n', 'spacing', '1.5'),
  nopagenoToPlain: write('\\documentclass{article}\n\\usepackage{nopageno}\n', 'pagestyle', 'plain'),
  same: write(FULL, 'size', '12pt'),
};
out.remove = {
  margin: write(FULL, 'margin', null), size: write(FULL, 'size', ''), spacing: write(FULL, 'spacing', ''),
  indent: write(FULL, 'indent', ''), parskip: write(FULL, 'parskip', ''), pagestyle: write(FULL, 'pagestyle', ''),
  hyperref: write(FULL, 'hyperref', 'off'), columns: write('\\documentclass[twocolumn]{article}\n', 'columns', ''),
  marginKeepsPaper: write('\\documentclass{article}\n\\usepackage[a4paper,margin=2cm]{geometry}\n', 'margin', null),
  marginGeometryCommand: write('\\documentclass{article}\n\\usepackage{geometry}\n\\geometry{margin=2cm}\n', 'margin', null),
  marginUsedInBody: write('\\documentclass{article}\n\\usepackage[margin=2cm]{geometry}\n', 'margin', null, {}, null, '\\begin{document}\nText.\n\\newgeometry{margin=1cm}\n\\end{document}\n'),
  hyperrefShared: write('\\documentclass{article}\n\\usepackage{amsmath,hyperref}\n', 'hyperref', 'off'),
  hyperrefNeeded: write('\\documentclass{article}\n\\usepackage{hyperref}\n', 'hyperref', 'off', {}, null, '\\begin{document}\nText \\href{https://a.org}{a}.\n\\end{document}\n'),
  hyperrefForUrl: write('\\documentclass{article}\n\\usepackage{hyperref}\n', 'hyperref', 'off', {}, null, '\\begin{document}\nText \\url{https://a.org}.\n\\end{document}\n'),
  zihaoOnly: write('\\documentclass[zihao=-4]{ctexart}\n', 'size', ''),
  nothing: write(A, 'indent', ''),
};
out.refused = {
  mine: write('\\documentclass{article}\n\\newcommand{\\x}{\\onehalfspacing}\n', 'spacing', '2'),
  zihaoArticle: write(A, 'size', 'zihao=5'),
  margin: write(A, 'margin', {preset: 'custom', top: 2, bottom: 2, left: 12, right: 2}),
  spacing: write(A, 'spacing', 'custom', {n: 9}),
  noBegin: (() => { const st = CM.EditorState.create({doc: '\\documentclass{article}\nText.'}); return P.layoutSpec(st, 'columns', 'twocolumn', {}); })(),
  paper: write(A, 'paper', 'a4paper'),
  unknownClass: write('\\documentclass{elsarticle}\n', 'columns', 'twocolumn'),
};
// 章节编号, 图表名称, 图表编号 (A3): new lines after the last \usepackage and before hyperref, changed and removed whole
const C = '\\documentclass[a4paper]{ctexart}\n\\usepackage{amsmath}\n\\usepackage[hidelinks]{hyperref}\n';
const R = '\\documentclass{report}\n\\usepackage{amsmath}\n';
const step = (pre, key, value) => { const w = write(pre, key, value); return w && w.pre != null ? w.pre : w; };
const zh = step(C, 'secnum', 'zh'), none = step(zh, 'secnum', 'none'), within = step(C, 'capnum', 'within');
out.numbers = {
  zh: zh, zhToNone: none, noneToDefault: step(none, 'secnum', ''), zhToDefault: step(zh, 'secnum', ''), sameZh: write(zh, 'secnum', 'zh'),
  noneArticle: step(A, 'secnum', 'none'), noneReport: step(R, 'secnum', 'none'), zhPackage: step('\\documentclass{article}\n\\usepackage[heading]{ctex}\n', 'secnum', 'zh'),
  zhShared: step(zh.replace('\\ctexset{', '\\ctexset{section/format+=\\raggedright,'), 'secnum', ''),
  refusedArticle: write(A, 'secnum', 'zh'), refusedPackage: write('\\documentclass{article}\n\\usepackage{ctex}\n', 'secnum', 'zh'),
  refusedMine: write('\\documentclass{article}\n\\setcounter{secnumdepth}{2}\n', 'secnum', 'none'), unknownValue: write(A, 'secnum', 'roman'),
  noneWithin: write(within, 'secnum', 'none'), withinNone: write(none, 'capnum', 'within'), noneReportDefault: step('\\documentclass{report}\n', 'secnum', 'none'),
};
out.names = {
  en: step(C, 'capname', 'en'), back: step(step(C, 'capname', 'en'), 'capname', 'zh'), sameDefault: write(C, 'capname', 'zh'),
  zhXe: step('\\documentclass{article}\n\\usepackage{xeCJK}\n', 'capname', 'zh'),
  inPlace: step('\\documentclass{article}\n\\usepackage{xeCJK}\n\\renewcommand\\figurename{图} % 图名\n\\renewcommand\\tablename{表}\n', 'capname', 'en'),
  ctexKeys: step('\\documentclass{ctexart}\n\\ctexset{figurename=Figure, tablename=Table, section/format+=\\raggedright}\n', 'capname', 'zh'),
  ctexKeysAlone: step('\\documentclass{ctexart}\n\\ctexset{figurename=Figure, tablename=Table}\n\\title{T}\n', 'capname', 'zh'),
  refused: write(A, 'capname', 'zh'), babel: write('\\documentclass{article}\n\\usepackage[english]{babel}\n', 'capname', 'zh'),
};
out.capnum = {
  within: within, back: step(within, 'capnum', ''), chapterWithin: step(R, 'capnum', 'within'), chapterContinuous: step(R, 'capnum', 'continuous'),
  chapterSwitch: step(step(R, 'capnum', 'continuous'), 'capnum', 'within'), noContinuousWithoutChapters: write(A, 'capnum', 'continuous'),
  afterCleveref: step('\\documentclass{article}\n\\usepackage{hyperref}\n\\usepackage{cleveref}\n', 'capnum', 'within'),
};
// one transaction each, undone in one step; CRLF kept
(() => {
  const st = make(C), r = P.layoutSpec(st, 'secnum', 'zh', {});
  const v = {state: st.update({changes: r.changes, selection: r.selection, userEvent: 'input.format'}).state};
  v.dispatch = (tr) => { v.state = tr.state; };
  out.numbersUndo = {depth: CM.undoDepth(v.state)};
  CM.undo(v);
  out.numbersUndo.back = v.state.doc.toString() === st.doc.toString();
})();
out.numbersCrlf = write(C, 'capnum', 'within', {}, '\r\n');
// CRLF: every line written in the file's own ending
out.crlf = write(A, 'spacing', '1.5', {}, '\r\n');
out.crlfRemove = write(FULL, 'hyperref', 'off', {}, '\r\n');
// ⌘Z: one transaction, undone in one step
(() => {
  const st = make(FULL), r = P.layoutSpec(st, 'spacing', '2', {});
  const v = {state: st.update({changes: r.changes, selection: r.selection, userEvent: 'input.format'}).state};
  v.dispatch = (tr) => { v.state = tr.state; };
  out.undo = {depth: CM.undoDepth(v.state), after: v.state.doc.toString() !== st.doc.toString()};
  CM.undo(v);
  out.undo.back = v.state.doc.toString() === st.doc.toString();
})();
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def writes() -> dict:
    return _node(_WRITE)


A = "\\documentclass{article}\n\\usepackage{amsmath}\n"
A_A4 = A.replace("{article}", "[a4paper]{article}")   # review: geometry or hyperref added to a class naming no paper keeps it A4
FULL = ("\\documentclass[a4paper,12pt]{article}\n\\usepackage[margin=2cm]{geometry} % page\n\\usepackage{setspace}\n\\onehalfspacing\n"
        "\\setlength{\\parindent}{0pt}\n\\usepackage[skip=0.5\\baselineskip,indent]{parskip}\n\\pagestyle{plain}\n"
        "\\usepackage[hidelinks]{hyperref}\n\\title{T}\n")


@needs_node
def test_a_new_setting_goes_on_a_line_of_its_own_after_the_last_usepackage_or_into_the_class_options(writes):
    add = writes["add"]
    assert add["margin"]["pre"] == A_A4 + "\\usepackage[margin=2.5cm]{geometry}\n"
    assert add["custom"]["pre"] == A_A4 + "\\usepackage[top=2cm,bottom=2.5cm,left=3cm,right=1.75cm]{geometry}\n"
    assert add["columns"]["pre"] == "\\documentclass[twocolumn]{article}\n\\usepackage{amsmath}\n"
    assert add["plain"]["pre"] == A + "\\pagestyle{plain}\n"
    assert add["empty"]["pre"] == A + "\\usepackage{nopageno}\n"
    assert add["hyperref"]["pre"] == A_A4 + "\\usepackage[hidelinks]{hyperref}\n"
    assert add["size"]["pre"] == "\\documentclass[11pt]{article}\n\\usepackage{amsmath}\n"
    assert add["spacing"]["pre"] == A + "\\usepackage{setspace}\n\\setstretch{1.5}\n"
    assert add["spacingCustom"]["pre"] == A + "\\usepackage{setspace}\n\\setstretch{1.4}\n"
    assert add["indent"]["pre"] == A + "\\setlength{\\parindent}{2em}\n"
    assert add["parskip"]["pre"] == A + "\\usepackage[skip=\\baselineskip,indent]{parskip}\n"
    assert add["noPackages"]["pre"] == "\\documentclass{article}\n\\setlength{\\parindent}{0pt}\n\\title{T}\n"
    assert add["setspaceLoaded"]["pre"] == "\\documentclass{article}\n\\usepackage{setspace}\n\\setstretch{2}\n\\title{x}\n"
    assert add["geometryShared"]["pre"] == "\\documentclass{article}\n\\usepackage{amsmath,geometry}\n\\geometry{margin=2cm}\n"
    assert add["geometryBare"]["pre"] == "\\documentclass{article}\n\\usepackage[margin=2cm]{geometry}\n"
    assert add["columns"]["cursor"] == "Text."                                       # the cursor stays where it was


@needs_node
def test_ctex_takes_its_own_line_spread_and_chinese_sizes(writes):
    add = writes["add"]
    assert add["ctexSpacing"]["pre"] == "\\documentclass{ctexart}\n\\linespread{2.6}\n"      # 2 x ctex's 1.3, no setspace
    assert add["ctexSize"]["pre"] == "\\documentclass[zihao=-4]{ctexart}\n"
    assert add["ctexPackageSize"]["pre"] == "\\documentclass{article}\n\\usepackage[zihao=5]{ctex}\n"


@needs_node
def test_hyperref_is_loaded_last_but_before_a_package_that_must_follow_it(writes):
    add = writes["add"]
    assert add["beforeHyperref"]["pre"] == ("\\documentclass[a4paper]{article}\n\\usepackage{amsmath}\n\\usepackage[margin=3cm]{geometry}\n"
                                            "\\usepackage[hidelinks]{hyperref}\n")
    assert add["hyperrefBeforeCleveref"]["pre"] == ("\\documentclass[a4paper]{article}\n\\usepackage{amsmath}\n\\usepackage[hidelinks]{hyperref}\n"
                                                    "\\usepackage{cleveref}\n")


@needs_node
def test_a_setting_already_there_is_changed_in_place_and_the_other_options_are_kept(writes):
    ch = writes["change"]
    assert ch["margin"]["pre"] == FULL.replace("margin=2cm", "margin=3cm")                  # its comment kept
    assert ch["marginCustom"]["pre"] == FULL.replace("margin=2cm", "top=1cm,bottom=2cm,left=3cm,right=4cm")
    assert ch["size"]["pre"] == FULL.replace("12pt", "10pt")
    assert ch["spacing"]["pre"] == FULL.replace("\\onehalfspacing", "\\setstretch{2}")
    assert ch["indent"]["pre"] == FULL.replace("{\\parindent}{0pt}", "{\\parindent}{2em}")
    assert ch["parskip"]["pre"] == FULL.replace("skip=0.5\\baselineskip", "skip=\\baselineskip")
    assert ch["pagestyle"]["pre"] == FULL.replace("\\pagestyle{plain}\n", "").replace(
        "\\usepackage[hidelinks]{hyperref}\n", "\\usepackage{nopageno}\n\\usepackage[hidelinks]{hyperref}\n")
    assert ch["columns"]["pre"] == FULL.replace("[a4paper,12pt]", "[a4paper,12pt,twocolumn]")
    assert ch["zihao"]["pre"] == "\\documentclass[a4paper, zihao=-4]{ctexart}\n"           # never both: 12pt goes
    assert ch["toPt"]["pre"] == "\\documentclass[a4paper,12pt]{ctexart}\n"
    assert ch["linespread"]["pre"] == "\\documentclass{article}\n\\linespread{1.25}\n"
    assert ch["setspaceOption"]["pre"] == "\\documentclass{article}\n\\usepackage{setspace}\n\\setstretch{2}\n"
    assert ch["ctexOption"]["pre"] == "\\documentclass[linespread=1.95]{ctexart}\n"
    assert ch["nopagenoToPlain"]["pre"] == "\\documentclass{article}\n\\pagestyle{plain}\n"
    assert ch["same"] is None


@needs_node
def test_back_to_default_removes_only_what_the_tool_would_have_written(writes):
    rm = writes["remove"]
    assert rm["margin"]["pre"] == FULL.replace("\\usepackage[margin=2cm]{geometry} % page\n", "")
    assert rm["size"]["pre"] == FULL.replace(",12pt", "")
    assert rm["spacing"]["pre"] == FULL.replace("\\onehalfspacing\n", "")                   # \usepackage{setspace} stays
    assert rm["indent"]["pre"] == FULL.replace("\\setlength{\\parindent}{0pt}\n", "")
    assert rm["parskip"]["pre"] == FULL.replace("\\usepackage[skip=0.5\\baselineskip,indent]{parskip}\n", "")
    assert rm["pagestyle"]["pre"] == FULL.replace("\\pagestyle{plain}\n", "")
    assert rm["hyperref"]["pre"] == FULL.replace("\\usepackage[hidelinks]{hyperref}\n", "")
    assert rm["columns"]["pre"] == "\\documentclass{article}\n"
    # review: geometry with more than margins would keep its own margins, not the class's: 默认 is refused there
    assert rm["marginKeepsPaper"] == {"refused": "geometry 宏包还有别的设置，页边距回不到文档类的默认。请选一个页边距。"}
    assert rm["marginGeometryCommand"]["pre"] == "\\documentclass{article}\n"                # its bare \usepackage{geometry} goes too
    assert rm["marginUsedInBody"]["pre"] == "\\documentclass{article}\n\\usepackage{geometry}\n"   # \newgeometry still needs it
    assert rm["hyperrefShared"]["pre"] == "\\documentclass{article}\n\\usepackage{amsmath}\n"
    assert rm["hyperrefNeeded"] == {"refused": "文稿用到了 \\href，它需要 hyperref，不能关掉超链接。"}
    assert rm["hyperrefForUrl"] == {"refused": "文稿用到了 \\url，它需要 hyperref，不能关掉超链接。"}
    assert rm["zihaoOnly"]["pre"] == "\\documentclass{ctexart}\n"
    assert rm["nothing"] is None


@needs_node
def test_a_write_is_refused_with_a_reason_where_it_cannot_be_made_and_never_duplicates(writes):
    ref = writes["refused"]
    assert ref["mine"] == {"refused": "导言区第 2 行已有自己写的设置"}
    assert ref["zihaoArticle"] == {"refused": "五号、小四这些字号要用 ctex。"}
    assert ref["margin"] == {"refused": "页边距要在 0 到 10 厘米之间。"}
    assert ref["spacing"] == {"refused": "行距要在 0.5 到 4 倍之间。"}
    assert ref["noBegin"] == {"refused": "主文件里没有 \\begin{document}，版式还不能设置。"}
    assert ref["unknownClass"]["refused"].startswith("文档类 elsarticle 的选项这里不认识")
    assert ref["paper"] is None                                               # 纸张 is no setting: nothing is written


@needs_node
def test_every_line_written_keeps_the_files_line_ending_and_undo_takes_it_back_in_one_step(writes):
    assert writes["crlf"]["file"].startswith(A.replace("\n", "\r\n") + "\\usepackage{setspace}\r\n\\setstretch{1.5}\r\n\\begin{document}")
    assert "\n" not in writes["crlf"]["file"].replace("\r\n", "")
    assert "hyperref" not in writes["crlfRemove"]["file"] and "\n" not in writes["crlfRemove"]["file"].replace("\r\n", "")
    assert writes["undo"] == {"depth": 1, "after": True, "back": True}


ZH_LINES = ("\\renewcommand{\\thesection}{\\chinese{section}}\n\\renewcommand{\\thesubsection}{（\\chinese{subsection}）}\n"
            "\\renewcommand{\\thesubsubsection}{\\arabic{subsubsection}}\n"
            "\\ctexset{section/name={,、},section/aftername={},subsection/aftername={},subsubsection/name={,.}}\n")
C_PRE = "\\documentclass[a4paper]{ctexart}\n\\usepackage{amsmath}\n\\usepackage[hidelinks]{hyperref}\n"
HYPER = "\\usepackage[hidelinks]{hyperref}\n"


@needs_node
def test_heading_numbers_write_their_lines_before_hyperref_and_take_back_only_their_own(writes):
    n = writes["numbers"]
    assert n["zh"] == C_PRE.replace(HYPER, ZH_LINES + HYPER)
    assert n["zhToNone"] == C_PRE.replace(HYPER, "\\setcounter{secnumdepth}{0}\n" + HYPER)
    assert n["noneToDefault"] == C_PRE and n["zhToDefault"] == C_PRE and n["sameZh"] is None
    assert n["zhShared"] == C_PRE.replace(HYPER, "\\ctexset{section/format+=\\raggedright}\n" + HYPER)   # only its own keys go
    assert n["noneArticle"] == A + "\\setcounter{secnumdepth}{0}\n"
    assert n["noneReport"] == "\\documentclass{report}\n\\usepackage{amsmath}\n\\setcounter{secnumdepth}{-1}\n"   # a chapter's level is 0
    assert n["zhPackage"] == "\\documentclass{article}\n\\usepackage[heading]{ctex}\n" + ZH_LINES
    assert n["refusedArticle"] == {"refused": "中文编号需要中文文档类"}
    assert n["refusedPackage"] == {"refused": "中文编号需要 ctex 宏包的 heading=true 选项"}
    assert n["refusedMine"] == {"refused": "导言区第 2 行已有自己写的设置"}
    assert n["unknownValue"] is None
    # 不编号 and 按章或节编号 refuse each other: no heading number, so 图 0-1 (measured below); report's own 图 2.3 drops the 0
    assert n["noneWithin"] == {"refused": "图表按章或节编号时，章节不能不编号：没有章节号，图表会印成 0-1。"}
    assert n["withinNone"] == {"refused": "章节不编号时，图表不能按章或节编号：没有章节号，图表会印成 0-1。"}
    assert n["noneReportDefault"] == "\\documentclass{report}\n\\setcounter{secnumdepth}{-1}\n"


@needs_node
def test_caption_names_are_the_classs_own_unless_set_and_change_in_place(writes):
    nm = writes["names"]
    assert nm["en"] == C_PRE.replace(HYPER, "\\renewcommand{\\figurename}{Figure}\n\\renewcommand{\\tablename}{Table}\n" + HYPER)
    assert nm["back"] == C_PRE and nm["sameDefault"] is None                  # 图 / 表 is ctex's own: the lines go
    assert nm["zhXe"] == "\\documentclass{article}\n\\usepackage{xeCJK}\n\\renewcommand{\\figurename}{图}\n\\renewcommand{\\tablename}{表}\n"
    assert nm["inPlace"] == "\\documentclass{article}\n\\usepackage{xeCJK}\n"                 # Figure / Table is article's own
    assert nm["ctexKeys"] == "\\documentclass{ctexart}\n\\ctexset{section/format+=\\raggedright}\n"   # the other key kept
    assert nm["ctexKeysAlone"] == "\\documentclass{ctexart}\n\\title{T}\n"
    assert nm["refused"] == {"refused": "中文图表名需要中文文档类"}
    assert nm["babel"] == {"refused": "文稿用 babel 设置语言，图表名称由它决定。"}


@needs_node
def test_caption_numbers_write_by_section_or_chapter_and_continuous_only_where_chapters_reset_them(writes):
    c = writes["capnum"]
    within = ("\\counterwithin{figure}{section}\n\\counterwithin{table}{section}\n\\renewcommand{\\thefigure}{\\arabic{section}-\\arabic{figure}}\n"
              "\\renewcommand{\\thetable}{\\arabic{section}-\\arabic{table}}\n\\providecommand{\\theHfigure}{\\theHsection.\\arabic{figure}}\n"
              "\\providecommand{\\theHtable}{\\theHsection.\\arabic{table}}\n")
    assert c["within"] == C_PRE.replace(HYPER, within + HYPER) and c["back"] == C_PRE
    R = "\\documentclass{report}\n\\usepackage{amsmath}\n"
    assert c["chapterWithin"] == R + "\\renewcommand{\\thefigure}{\\thechapter-\\arabic{figure}}\n\\renewcommand{\\thetable}{\\thechapter-\\arabic{table}}\n"
    assert c["chapterContinuous"] == R + "\\counterwithout{figure}{chapter}\n\\counterwithout{table}{chapter}\n"
    assert c["chapterSwitch"] == c["chapterWithin"] and c["noContinuousWithoutChapters"] is None
    # no other package: after \\documentclass, hyperref and cleveref still after them
    assert c["afterCleveref"] == "\\documentclass{article}\n" + within + "\\usepackage{hyperref}\n\\usepackage{cleveref}\n"
    assert writes["numbersUndo"] == {"depth": 1, "back": True}
    crlf = writes["numbersCrlf"]["file"]
    assert "\\counterwithin{figure}{section}\r\n" in crlf and "\n" not in crlf.replace("\r\n", "")


# -- 段落 and the further insertions -------------------------------------------------------------------------------

_PARA = r"""
const state = (marked) => {
  const a = marked.indexOf('«'), text = marked.replace('«', '').replace('»', '').replace('|', '');
  const anchor = a >= 0 ? a : marked.indexOf('|'), head = a >= 0 ? marked.indexOf('»') - 1 : anchor;
  return CM.EditorState.create({doc: text, selection: {anchor, head}});
};
const run = (marked, make) => {
  const st = state(marked), r = make(st);
  if (!r) return null;
  if (r.refused) return {refused: r.refused};
  const n = st.update({changes: r.changes, selection: r.selection}).state, sel = n.selection.main, x = n.doc.toString();
  const shown = sel.empty ? x.slice(0, sel.head) + '|' + x.slice(sel.head) : x.slice(0, sel.from) + '«' + x.slice(sel.from, sel.to) + '»' + x.slice(sel.to);
  return r.note ? {text: shown, note: r.note} : shown;
};
const AL = (k) => (s) => P.alignSpec(s, k);
const out = {};
out.align = {
  center: run('a\n\nOne te|xt\nmore\n\nb', AL('center')), right: run('a\n\nOne te|xt\n\nb', AL('flushright')),
  left: run('a\n\nOne te|xt\n\nb', AL('flushleft')), renamed: run('\\begin{center}\nOne te|xt\n\\end{center}', AL('flushright')),
  justify: run('a\n\n\\begin{center}\nOne te|xt\n\\end{center}\n\nb', AL('')), already: run('a\n\nOne te|xt', AL('')),
  same: run('\\begin{center}\nOne te|xt\n\\end{center}', AL('center')), several: run('«one\n\ntwo»\n\nb', AL('center')),
  emptyLine: run('x\n\n|\n\ny', AL('center')), heading: run('\\section{A}|\n', AL('center')),
  structural: run('«a\n\\begin{itemize}»\n\\item b\n\\end{itemize}', AL('center')), unknown: run('a|', AL('chapter')),
  inline: run('a \\begin{center}x|\\end{center} b', AL('')),
  state: [P.paragraphState(state('\\begin{flushright}\na|\n\\end{flushright}'), P.readLatex('\\begin{flushright}\na\n\\end{flushright}')),
          P.paragraphState(state('\\begin{quote}\na|\n\\end{quote}'), P.readLatex('\\begin{quote}\na\n\\end{quote}'))],
};
out.quote = [run('a\n\nOne te|xt\n\nb', P.quoteSpec), run('\\begin{quote}\nOne te|xt\n\\end{quote}', P.quoteSpec), run('$x|$', P.quoteSpec)];
out.page = [run('Text|\nmore', P.pageBreakSpec), run('\\caption{a|}', P.pageBreakSpec)];
const DOC = (b) => '\\documentclass{article}\n\\begin{document}\n' + b + '\n\\end{document}';
out.toc = [run(DOC('\\maketitle\nText|'), P.tocSpec), run(DOC('Text|'), P.tocSpec), run(DOC('\\tableofcontents\nText|'), P.tocSpec),
  run(DOC('% \\tableofcontents\nText|'), P.tocSpec)];
out.ref = [run('See |', (s) => P.refSpec(s, 'fig:a', false)), run('See «x»', (s) => P.refSpec(s, 'eq:a', true)), run('\\cite{|}', (s) => P.refSpec(s, 'a', false))];
out.url = [run('See |', (s) => P.urlSpec(s, ' https://a.org/x?a=1#b ', {url: true})), run('See «https://a.org» now', (s) => P.urlSpec(s, 'https://a.org', {url: true})),
  run('\\footnote{See |}', (s) => P.urlSpec(s, 'https://a.org/#b', {url: true})), run('\\footnote{See |}', (s) => P.urlSpec(s, 'https://a.org/b', {url: true})),
  run('See |', (s) => P.urlSpec(s, 'a b', {url: true})), run('See |', (s) => P.urlSpec(s, 'https://a.org', {url: false, isMain: false, main: 'paper/main.tex'})),
  run(DOC('See |'), (s) => P.urlSpec(s, 'https://a.org', {url: false, isMain: true}))];
const T = '\\section{Intro}\\label{intro}\n% \\label{commented}\n\\begin{figure}\\caption{x}\\label{plot}\\end{figure}\n' +
  '\\begin{table}\\label{t1}\\end{table}\n\\begin{equation}a\\label{e1}\\end{equation}\n\\begin{align*}b\\label{e2}\\end{align*}\n' +
  '\\label{fig:a} \\label{eq:b} \\label{sec:c} \\label{tab:d}\n\\subsection{The $x^{2}$ case}\n\\label{xcase}\ntext \\label{loose}\n\\verb|\\label{verb}|';
out.labels = P.labelsOf(T, P.readLatex(T));
out.labelText = T;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def para() -> dict:
    return _node(_PARA)


@needs_node
def test_alignment_wraps_the_paragraph_renames_an_alignment_and_justified_takes_it_away(para):
    al = para["align"]
    assert al["center"] == "a\n\n\\begin{center}\nOne te|xt\nmore\n\\end{center}\n\nb"
    assert al["right"] == "a\n\n\\begin{flushright}\nOne te|xt\n\\end{flushright}\n\nb"
    assert al["left"] == "a\n\n\\begin{flushleft}\nOne te|xt\n\\end{flushleft}\n\nb"
    assert al["renamed"] == "\\begin{flushright}\nOne te|xt\n\\end{flushright}"
    assert al["justify"] == "a\n\nOne te|xt\n\nb"
    assert al["already"] is None and al["same"] is None and al["unknown"] is None
    assert al["several"] == "«\\begin{center}\none\n\ntwo\n\\end{center}»\n\nb"
    assert al["emptyLine"] == "x\n\n\\begin{center}\n|\n\\end{center}\n\ny"
    assert al["heading"] == {"refused": "这一段是标题，不能这样设置。"}
    assert al["structural"] == {"refused": "这一行是命令或环境，不能这样设置。"}
    assert al["inline"] == "a x| b"                                            # \begin and \end not alone: the commands go
    assert al["state"] == [{"align": "flushright", "quote": False}, {"align": "", "quote": True}]


@needs_node
def test_block_quote_page_break_and_contents_are_typed_as_their_commands(para):
    assert para["quote"][0] == "a\n\n\\begin{quote}\nOne te|xt\n\\end{quote}\n\nb"
    assert para["quote"][1] == "One te|xt\n"
    assert para["quote"][2] == {"refused": "光标在公式里。请把光标移到公式外面。"}
    assert para["page"][0] == "Text\n\\clearpage\n|more"
    assert para["page"][1]["refused"] == "图注里不能用这个工具。"                  # review: said by what is shown
    toc = para["toc"]
    assert toc[0] == "\\documentclass{article}\n\\begin{document}\n\\maketitle\n\\tableofcontents|\nText\n\\end{document}"
    assert toc[1] == "\\documentclass{article}\n\\begin{document}\nText\n\\tableofcontents\n|\\end{document}"
    assert toc[2] == {"refused": "文稿里已经有目录（第 3 行）。"}
    assert "\\tableofcontents\n|" in toc[3]                                      # a commented one does not count


@needs_node
def test_a_cross_reference_and_a_web_address_are_typed_as_their_commands(para):
    assert para["ref"][0] == "See \\ref{fig:a}|" and para["ref"][1] == "See x\\eqref{eq:a}|"
    assert para["ref"][2]["refused"] == "这里不能用这个工具。"
    url = para["url"]
    assert url[0] == "See \\url{https://a.org/x?a=1#b}|"
    assert url[1] == "See \\url{https://a.org}| now"                           # the selected address becomes the link
    assert url[2] == {"refused": "网址里有 % 或 #，放在命令的参数里（比如脚注）会出错。请把它放在正文里。"}
    assert url[3] == "\\footnote{See \\url{https://a.org/b}|}"
    assert url[4] == {"refused": "网址里不能有空格、花括号或反斜杠。"}
    assert url[5] == {"text": "See \\url{https://a.org}|", "note": "主文件 paper/main.tex 的导言区需要加一行：\\usepackage{url}"}
    assert url[6].startswith("\\documentclass{article}\n\\usepackage{url}\n\\begin{document}")


@needs_node
def test_labels_are_grouped_by_prefix_environment_or_heading_the_same_on_the_page_and_in_the_engine(para):
    kinds = [(x["key"], x["kind"]) for x in para["labels"]]
    assert kinds == [("intro", "section"), ("plot", "figure"), ("t1", "table"), ("e1", "equation"), ("e2", "equation"),
                     ("fig:a", "figure"), ("eq:b", "equation"), ("sec:c", "section"), ("tab:d", "table"),
                     ("xcase", "section"), ("loose", "other")]
    engine = [(x["key"], x["kind"]) for x in latex_edit.labels_in(para["labelText"].replace("\\verb|\\label{verb}|", ""))]
    assert engine == kinds


# -- the panel on the page -----------------------------------------------------------------------------------------

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
const words = (root) => shown(root).map((e) => typeof e === 'string' ? e : e.textContent).filter(Boolean);
const panes = () => inside(side(), (e) => e.classList && e.classList.contains('latex-tab-pane'));
const layoutPane = () => panes()[1];
const primaries = () => shown(side()).filter((e) => e.tag === 'button' && e.classList.contains('btn-primary')).map((e) => e.textContent);
const tool = (label) => inside(side(), (e) => e.tag === 'button' && e.textContent === label).pop();
const tab = (label) => inside(side(), (e) => e.attrs && e.attrs.role === 'tab' && e.textContent === label)[0];
const item = (label) => inside(layoutPane(), (e) => e.classList.contains('latex-layout-item') && e.children[0].children[0].textContent === label)[0];
const sel = (label) => item(label).children[0].children[1];
const choose = (label, value) => { const s = sel(label); s.value = value; s.fire('change'); };
const row = (label) => { const s = sel(label); return {value: s.value, disabled: !!s.disabled, options: s.children.map((o) => o.value), note: words(item(label).children[2]),
  labels: s.children.map((o) => o.textContent), off: s.children.filter((o) => o.disabled).map((o) => o.value)}; };
const doc = () => view().state.doc.toString();
const pre = () => doc().slice(0, doc().indexOf('\\begin{document}'));
const f = (path, text, extra) => Object.assign({path, text, revision: 'r1', line_ending: 'lf', read_only: false, read_only_reason: null, links: [], mains: []}, extra);
const openFile = async (path, text, extra) => { L.open(path); await tick(); gets.shift().res(f(path, text, extra)); await tick(); await tick(); };
const MAIN_PRE = '\\documentclass[a4paper,12pt]{article}\n\\usepackage[margin=2cm]{geometry}\n\\usepackage{setspace}\n\\setstretch{1.5}\n\\newcommand{\\nopages}{\\pagestyle{empty}}\n\\usepackage{mystyle}\n';
const LOCAL = [{name: 'mystyle.sty', path: 'paper/mystyle.sty', text: '\\ProvidesPackage{mystyle}\n\\RequirePackage{hyperref}\n'}];
// a chapter: the main file's settings shown, nothing can be changed, a link opens the main file
await openFile('paper/ch.tex', 'Chapter text.\n', {mains: ['paper/main.tex'], format: {main: 'paper/main.tex', engine: 'pdflatex', folder: 'paper', preamble: MAIN_PRE, local: LOCAL}});
tab('版式').fire('click');
out.chapter = {head: words(inside(layoutPane(), (e) => e.classList.contains('latex-layout-head'))[0]), groups: inside(layoutPane(), (e) => e.tag === 'h4').map((h) => h.textContent),
  labels: inside(layoutPane(), (e) => e.classList.contains('latex-layout-item')).map((i) => i.children[0].children[0].textContent),
  secnum: row('章节编号'), capname: row('图表名称'), capnum: row('图表编号'), margin: row('页边距'), spacing: row('行距'), size: row('正文字号'), pagestyle: row('页码'), hyperref: row('超链接'), indent: row('首行缩进'),
  allOff: inside(layoutPane(), (e) => e.tag === 'select' || e.tag === 'input').every((x) => x.disabled), primaries: primaries()};
// 看这一行 in a chapter opens the main file at that line
inside(item('页码'), (e) => e.tag === 'button' && e.textContent === '看这一行')[0].fire('click'); await tick();
out.lineUrl = gets[0] && gets[0].url;
const MAIN = MAIN_PRE + '\\title{T}\n\\begin{document}\n\\maketitle\nFirst paragraph.\n\nSecond paragraph.\n\\end{document}\n';
gets.shift().res(f('paper/main.tex', MAIN, {mains: ['paper/main.tex'], format: {main: 'paper/main.tex', engine: 'pdflatex', folder: 'paper', preamble: null, local: LOCAL}}));
await tick(); await tick();
out.cursorLine = view().state.doc.lineAt(view().state.selection.main.head).number;
// the main file: each setting can be changed, as the editor's own transaction
tab('版式').fire('click');
const V = view();
out.main = {head: hidden(inside(layoutPane(), (e) => e.classList.contains('latex-layout-head'))[0]), columns: row('分栏'), pagestyle: row('页码'), hyperref: row('超链接'),
  sizeOptions: row('正文字号').options};
V.dispatch({selection: {anchor: doc().indexOf('First')}});
choose('分栏', 'twocolumn');
out.afterPaper = {pre: pre(), undo: real.undoDepth(V.state), dirty: L.isDirty(), shown: row('分栏').value, cursor: doc().slice(V.state.selection.main.head, V.state.selection.main.head + 5)};
const undoView = {get state() { return V.state; }, dispatch: (tr) => { V.state = tr.state; }};
real.undo(undoView);
out.afterUndo = {same: doc() === MAIN};
real.redo(undoView);
// 自定义 行距: its field shows the number the file has; a change writes it
choose('行距', 'custom');
const stretch = inside(item('行距'), (e) => e.tag === 'input')[0];
out.customSpacing = {shown: !hidden(item('行距').children[1]), value: stretch.value, pre: pre().includes('\\setstretch{1.5}')};
stretch.value = '1.4'; stretch.fire('change');
out.afterStretch = {pre: pre(), row: row('行距').value};
// 自定义 页边距: four fields from the file's margins; one changed writes all four
choose('页边距', 'custom');
const margins = inside(item('页边距'), (e) => e.tag === 'input');
out.customMargins = margins.map((i) => [i.attrs['aria-label'], i.value]);
margins[0].value = '3'; margins[0].fire('change');
out.afterMargins = {pre: pre(), row: row('页边距').value};
choose('首行缩进', '0pt'); choose('段间距', 'half'); choose('分栏', 'twocolumn'); choose('正文字号', '11pt');
out.afterMore = pre();
choose('段间距', '');
out.afterParskipOff = pre();
// 章节编号, 图表名称, 图表编号 (A3) on an article: 一、 cannot be chosen (its reason in the option), 不编号 says what it costs
out.numbers = {before: row('章节编号'), names: row('图表名称'), capnum: row('图表编号')};
choose('章节编号', 'zh');
out.numbers.refused = {note: words(inside(layoutPane(), (e) => e.classList.contains('latex-format-note'))[0]), same: !pre().includes('chinese'), shown: row('章节编号').value};
const depth0 = real.undoDepth(V.state);
choose('图表编号', 'within');
out.numbers.within = {pre: pre(), secnum: row('章节编号')};
choose('图表编号', ''); choose('章节编号', 'none');
out.numbers.after = {pre: pre(), secnum: row('章节编号'), capnum: row('图表编号'), steps: real.undoDepth(V.state) - depth0};
// a setting of the person's own and one of the project's .sty: 自定义, not changeable, the line said with a link
out.mine = {pagestyle: row('页码'), hyperref: row('超链接')};
inside(item('页码'), (e) => e.tag === 'button' && e.textContent === '看这一行')[0].fire('click');
out.shownLine = V.state.doc.lineAt(V.state.selection.main.head).number;
out.shownInCode = byClass('latex-page').classList.contains('is-source');   // lines are the code's (B1)
button('显示代码').fire('click');
// the save sends what the selects wrote as typed edits
button('保存').fire('click'); await tick();
out.saved = posts.length ? posts[0].body.edits.length : 0;
posts.shift().res({revision: 'r2', changed: true, previous: 'r1'}); await tick(); await tick();
// 显示代码 (B1) leaves 版式 working: it edits the preamble, code or not
button('显示代码').fire('click');
out.source = {head: words(inside(layoutPane(), (e) => e.classList.contains('latex-layout-head'))[0]), off: sel('页边距').disabled && sel('章节编号').disabled};
button('显示代码').fire('click');
out.sourceOff = sel('页边距').disabled;
// 格式's 段落 and the further insertions
tab('格式').fire('click');
V.dispatch({selection: {anchor: doc().indexOf('Second') + 2}});
const align = inside(panes()[0], (e) => e.tag === 'select')[3];
out.alignOptions = align.children.map((o) => [o.value, o.textContent]);
align.value = 'center'; align.fire('change');
out.afterAlign = {text: doc().includes('\\begin{center}\nSecond paragraph.\n\\end{center}'), shown: align.value};
tool('引文块').fire('click');
out.afterQuote = {text: doc().includes('\\begin{center}\n\\begin{quote}\nSecond paragraph.\n\\end{quote}\n\\end{center}'), pressed: tool('引文块').attrs['aria-pressed']};
tool('目录').fire('click');
out.afterToc = doc().includes('\\maketitle\n\\tableofcontents\n');
V.dispatch({selection: {anchor: doc().indexOf('First paragraph.') + 16}});
tool('分页').fire('click');
out.afterPage = doc().includes('First paragraph.\n\\clearpage\n');
V.dispatch({selection: {anchor: doc().indexOf('First paragraph.') + 5}, changes: {from: doc().indexOf('First paragraph.') + 16, insert: '\\label{sec:first}'}});
tool('交叉引用').fire('click'); await tick();
out.refUrl = gets[gets.length - 1].url;
out.refPrimaries = primaries();
gets.pop().res({main: 'paper/main.tex', labels: [{key: 'fig:plot', kind: 'figure', file: 'paper/ch.tex'}, {key: 'eq:one', kind: 'equation', file: 'paper/ch.tex'}]});
await tick(); await tick();
const picker = () => inside(side(), (e) => e.classList.contains('latex-picker'))[0];
out.refList = words(picker());
inside(picker(), (e) => e.tag === 'button' && e.children.some((c) => c.textContent === 'fig:plot'))[0].fire('click');
tool('插入').fire('click');
out.afterRef = doc().includes('First\\ref{fig:plot}');
tool('网址').fire('click');
const address = inside(picker(), (e) => e.tag === 'input')[0];
address.value = 'https://example.org';
tool('插入').fire('click');
out.afterUrl = {text: doc().includes('\\url{https://example.org}'), pre: pre().includes('\\usepackage{url}'), primaries: primaries()};
// the editor's lines follow 行距 (like Word, roughly)
const box = byClass('latex-editor'); box.style = {props: {}, setProperty(k, v) { this.props[k] = v; }};
tab('版式').fire('click'); choose('行距', '2');
out.lineHeight = box.style.props['--latex-stretch'];
// a read-only main file: 版式 off with the reason
await openFile('gen.tex', '\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n', {read_only: true, read_only_reason: 'generated', read_only_message: 'generated'});
tab('版式').fire('click');
out.readOnly = {head: words(inside(layoutPane(), (e) => e.classList.contains('latex-layout-head'))[0]), off: inside(layoutPane(), (e) => e.tag === 'select').every((x) => x.disabled)};
// a file with no main file
await openFile('loose.tex', 'Loose text.\n', {format: {main: null, engine: null, folder: '', preamble: null, local: []}});
tab('版式').fire('click');
out.noMain = words(inside(layoutPane(), (e) => e.classList.contains('latex-layout-head'))[0]);
// a ctexrep main file: 一、（一）1. can be chosen, 连续编号 is offered with the class's 图 2.3 as the default
const ZMAIN = '\\documentclass[a4paper]{ctexrep}\n\\usepackage{amsmath}\n\\usepackage[hidelinks]{hyperref}\n\\begin{document}\n\\chapter{一}\n正文。\n\\end{document}\n';
await openFile('zh/main.tex', ZMAIN, {mains: ['zh/main.tex'], format: {main: 'zh/main.tex', engine: 'xelatex', folder: 'zh', preamble: null, local: []}});
tab('版式').fire('click');
out.ctex = {secnum: row('章节编号'), names: row('图表名称'), capnum: row('图表编号')};
choose('章节编号', 'zh'); choose('图表名称', 'en'); choose('图表编号', 'continuous');
out.ctex.after = {pre: pre(), secnum: row('章节编号').value, names: row('图表名称').value, capnum: row('图表编号').value, primaries: primaries()};
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
def test_a_chapter_shows_its_main_files_layout_read_only_with_a_link_to_the_main_file(panel):
    ch = panel["chapter"]
    assert ch["head"] == ["版式在主文件 paper/main.tex 里设置。", "打开 paper/main.tex"]
    assert ch["groups"] == ["文档信息", "页面", "文字与段落", "章节与图表"]   # 文档信息 first (B1)
    assert ch["labels"] == ["页边距", "分栏", "页码", "超链接", "正文字号", "行距", "首行缩进", "段间距", "章节编号", "图表名称", "图表编号"]
    assert ch["margin"]["value"] == "2" and ch["spacing"]["value"] == "1.5"
    assert ch["secnum"]["value"] == "" and ch["capname"]["value"] == "en" and ch["capnum"]["value"] == ""
    assert ch["size"]["value"] == "12pt" and ch["indent"]["value"] == ""
    assert ch["pagestyle"]["value"] == "mine" and ch["pagestyle"]["note"] == ["导言区第 5 行已有自己写的设置", "看这一行"]
    assert ch["hyperref"]["value"] == "mine" and ch["hyperref"]["note"] == ["paper/mystyle.sty 第 2 行已有自己写的设置"]
    assert ch["allOff"] is True and ch["primaries"] == []
    assert panel["lineUrl"] == "/api/latex/file?path=paper%2Fmain.tex" and panel["cursorLine"] == 5


@needs_node
def test_in_the_main_file_a_choice_edits_the_preamble_as_one_undoable_unsaved_step(panel):
    main = panel["main"]
    assert main["head"] is True and main["columns"]["disabled"] is False and main["columns"]["options"] == ["", "twocolumn"]
    assert main["pagestyle"]["disabled"] is True and main["hyperref"]["disabled"] is True       # the person's own
    assert main["sizeOptions"] == ["", "10pt", "11pt", "12pt"]                                     # no 五号 without ctex
    after = panel["afterPaper"]
    assert after["pre"].startswith("\\documentclass[a4paper,12pt,twocolumn]{article}\n") and after["undo"] == 1 and after["dirty"] is True
    assert after["shown"] == "twocolumn" and after["cursor"] == "First"
    assert panel["afterUndo"] == {"same": True}
    assert panel["customSpacing"] == {"shown": True, "value": "1.5", "pre": True}
    assert "\\setstretch{1.4}" in panel["afterStretch"]["pre"] and panel["afterStretch"]["row"] == "custom"
    assert panel["customMargins"] == [["上", "2"], ["下", "2"], ["左", "2"], ["右", "2"]]
    assert "\\usepackage[top=3cm,bottom=2cm,left=2cm,right=2cm]{geometry}" in panel["afterMargins"]["pre"]
    more = panel["afterMore"]
    assert more.startswith("\\documentclass[a4paper,11pt,twocolumn]{article}\n")                 # the paper left as written
    assert "\\setlength{\\parindent}{0pt}" in more and "\\usepackage[skip=0.5\\baselineskip,indent]{parskip}" in more
    assert "parskip" not in panel["afterParskipOff"]
    assert panel["shownLine"] == 5 and panel["saved"] > 0


@needs_node
def test_heading_numbers_and_captions_on_the_page_say_what_a_document_cannot_take(panel):
    n = panel["numbers"]
    before = n["before"]
    assert before["options"] == ["", "zh", "none"] and before["off"] == ["zh"] and before["disabled"] is False
    assert before["labels"] == ["1 / 1.1 / 1.1.1", "一、（一）1.：中文编号需要中文文档类", "不编号"] and before["note"] == []
    assert n["names"]["labels"] == ["图 / 表：中文图表名需要中文文档类", "Figure / Table"] and n["names"]["value"] == "en"
    assert n["capnum"]["labels"] == ["连续编号（图 1）", "按章或节编号（图 2-3）"] and n["capnum"]["options"] == ["", "within"]
    assert n["refused"] == {"note": ["中文编号需要中文文档类"], "same": True, "shown": ""}
    within = n["within"]
    assert "\\counterwithin{figure}{section}\n" in within["pre"] and within["secnum"]["off"] == ["zh", "none"]
    assert within["secnum"]["labels"][2] == "不编号：图表按章或节编号时不能选"
    after = n["after"]
    assert "\\setcounter{secnumdepth}{0}\n" in after["pre"] and "counterwithin" not in after["pre"]
    assert after["secnum"]["value"] == "none" and after["secnum"]["note"] == ["不编号时，交叉引用章节印不出编号。"]
    assert after["capnum"]["off"] == ["within"] and after["capnum"]["labels"][1] == "按章或节编号（图 2-3）：章节不编号时不能选"
    assert after["steps"] == 3                                                       # one undo step each
    c = panel["ctex"]
    assert c["secnum"]["off"] == [] and c["names"]["value"] == "zh" and c["names"]["off"] == []
    assert c["capnum"]["options"] == ["continuous", "", "within"]
    assert c["capnum"]["labels"] == ["连续编号（图 1）", "按章编号（图 2.3）", "按章或节编号（图 2-3）"] and c["capnum"]["value"] == ""
    a = c["after"]
    # each new line after the last \\usepackage (so the latest first), hyperref still last
    assert a["pre"] == ("\\documentclass[a4paper]{ctexrep}\n\\usepackage{amsmath}\n"
                        + "\\counterwithout{figure}{chapter}\n\\counterwithout{table}{chapter}\n"
                        + "\\renewcommand{\\figurename}{Figure}\n\\renewcommand{\\tablename}{Table}\n" + ZH_LINES + "\\usepackage[hidelinks]{hyperref}\n")
    assert (a["secnum"], a["names"], a["capnum"], a["primaries"]) == ("zh", "en", "continuous", [])


@needs_node
def test_show_code_leaves_the_layout_on_and_a_read_only_main_and_a_file_without_a_main_turn_it_off_with_a_reason(panel):
    assert panel["source"] == {"head": [], "off": False}         # 显示代码 (B1): 版式 edits the preamble, code or not
    assert panel["sourceOff"] is False
    assert panel["shownInCode"] is True                           # 看这一行 shows the line in 显示代码
    assert panel["readOnly"] == {"head": ["这个文件只能查看，不能改格式和版式。"], "off": True}
    assert panel["noMain"] == ["没有找到主文件。版式在主文件的导言区里设置。"]


@needs_node
def test_the_paragraph_tools_and_the_new_pickers_work_from_the_format_tab_with_one_primary(panel):
    assert panel["alignOptions"] == [["", "两端对齐"], ["flushleft", "左对齐"], ["center", "居中"], ["flushright", "右对齐"]]
    assert panel["afterAlign"] == {"text": True, "shown": "center"}
    assert panel["afterQuote"] == {"text": True, "pressed": "true"}
    assert panel["afterToc"] is True and panel["afterPage"] is True
    assert panel["refUrl"] == "/api/latex/insert?what=labels&path=paper%2Fmain.tex" and panel["refPrimaries"] == ["插入"]
    assert panel["refList"][:7] == ["插入交叉引用", "图", "fig:plot", "在 paper/ch.tex", "公式", "eq:one", "在 paper/ch.tex"]
    assert "节" in panel["refList"] and "sec:first" in panel["refList"]
    assert panel["afterRef"] is True
    assert panel["afterUrl"] == {"text": True, "pre": True, "primaries": []}
    assert panel["lineHeight"] == "2"


# -- words, glyphs, the primary ----------------------------------------------------------------------------------------


def _new_source() -> str:
    js = LATEX_JS.read_text(encoding="utf-8")
    return (js[js.index("// -- 版式: the document's layout settings (22.12, A2)"):js.index("  // -- the editor page ---")]
            + js[js.index("// 版式 (22.12, A2): the document's settings"):js.index("  // The right column: 格式与插入 at the top")])


def test_every_new_word_has_its_english_and_none_is_a_glyph():
    from test_v9_language import english_table

    table = english_table()
    said = {json.loads('"' + s + '"') for s in re.findall(r'\bt\("((?:[^"\\]|\\.)*)"', _new_source())}
    assert len(said) > 60
    assert sorted(s for s in said if s not in table) == []
    glyphs = ("›", "‹", "▸", "▾", "»", "«", "→", "←", "->", "✓", "✔", "•")
    assert [s for s in said if any(g in s for g in glyphs)] == []
    assert [s for s in said if any(g in str(table[s]) for g in glyphs)] == []
    for zh, en in (("行距", "Line spacing"), ("首行缩进", "First-line indent"), ("段间距", "Space between paragraphs"),
                   ("页边距", "Margins"), ("两端对齐", "Justified"), ("交叉引用", "Cross-reference"),
                   ("1.5 倍", "1.5 lines"), ("看这一行", "Show the line"), ("章节编号", "Heading numbers"),
                   ("图表名称", "Figure and table names"), ("图表编号", "Figure and table numbers"), ("不编号", "Unnumbered"),
                   ("连续编号（图 1）", "Continuous (Figure 1)"), ("按章或节编号（图 2-3）", "By chapter or section (Figure 2-3)")):
        assert table[zh] == en
    assert "（下一步加入）" not in table                                          # 版式 is filled
    assert "纸张" not in table and '"纸张"' not in LATEX_JS.read_text(encoding="utf-8")   # A3: taken out


def test_the_layout_tab_adds_no_primary_button_and_its_colours_are_the_palettes_tokens():
    src = _new_source()
    assert "btn-primary" not in src and src.count("insertButton(") == 2           # 交叉引用's and 网址's 插入
    css = (WEBAPP / "workspace.css").read_text(encoding="utf-8")
    added = css[css.index("/* 22.12 A2: 版式"):]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", added)


# -- the engine: the project's own .sty/.cls and the labels of the paper's other files -----------------------------


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "paper" / "chapters").mkdir(parents=True)
    project.init_project(root)
    (root / "paper" / "main.tex").write_text(
        "\\documentclass{mythesis}\n\\usepackage{mystyle, amsmath}\n% \\usepackage{commented}\n\\usepackage{evil}\n"
        "\\begin{document}\n\\section{Intro}\\label{sec:intro}\n\\input{chapters/one}\n\\input{chapters/two}\n\\end{document}\n", encoding="utf-8")
    (root / "paper" / "chapters" / "one.tex").write_text(
        "\\section{One}\\label{one}\n\\begin{figure}\\caption{x}\\label{plot}\\end{figure}\n", encoding="utf-8")
    (root / "paper" / "chapters" / "two.tex").write_text("\\begin{equation}a\\label{e}\\end{equation}\n", encoding="utf-8")
    (root / "paper" / "mythesis.cls").write_text("\\LoadClass[12pt]{report}\n", encoding="utf-8")
    (root / "paper" / "mystyle.sty").write_text("\\RequirePackage{setspace}\n\\onehalfspacing\n", encoding="utf-8")
    (root / "paper" / "commented.sty").write_text("x\n", encoding="utf-8")
    (tmp_path / "outside.sty").write_text("secret\n", encoding="utf-8")
    os.symlink(tmp_path / "outside.sty", root / "paper" / "evil.sty")
    return root


def test_the_file_read_carries_the_projects_own_class_and_packages_but_never_a_link_or_a_commented_one(tmp_path):
    root = _project(tmp_path)
    got = latex_edit.read_tex(root, "paper/chapters/one.tex")["format"]
    assert [(x["name"], x["path"], x["text"]) for x in got["local"]] == [
        ("mythesis.cls", "paper/mythesis.cls", "\\LoadClass[12pt]{report}\n"),
        ("mystyle.sty", "paper/mystyle.sty", "\\RequirePackage{setspace}\n\\onehalfspacing\n")]
    assert latex_edit.read_tex(root, "paper/main.tex")["format"]["local"] == got["local"]


def test_the_labels_of_the_papers_other_files_come_with_their_kinds_and_nothing_is_written(tmp_path):
    root = _project(tmp_path)
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if ".researchos" not in p.parts)
    got = latex_edit.insertables(root, "paper/chapters/one.tex", "labels")
    assert got["main"] == "paper/main.tex" and got["more"] is False
    assert got["labels"] == [{"key": "sec:intro", "kind": "section", "file": "paper/main.tex"},
                             {"key": "e", "kind": "equation", "file": "paper/chapters/two.tex"}]
    alone = tmp_path / "project" / "alone.tex"
    alone.write_text("Text.\n", encoding="utf-8")
    assert latex_edit.insertables(root, "alone.tex", "labels") == {"main": None, "labels": [], "more": False}
    after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if ".researchos" not in p.parts)
    assert after == sorted(before + ["alone.tex"])


# -- real compiles: every setting at every value, ctexart (XeLaTeX) and article (pdfLaTeX) ---------------------------

_PROBE = ("\\makeatletter\n\\typeout{PROBE size=\\f@size\\space bls=\\the\\baselineskip\\space stretch=\\baselinestretch\\space "
          "pi=\\the\\parindent\\space ps=\\the\\parskip\\space pw=\\the\\paperwidth\\space ph=\\the\\paperheight\\space "
          "tw=\\the\\textwidth\\space two=\\if@twocolumn 1\\else 0\\fi\\space hyper=\\ifdefined\\hypersetup 1\\else 0\\fi\\space "
          "noplain=\\ifx\\ps@plain\\ps@empty 1\\else 0\\fi}\n\\makeatother\n")

_BUILD = r"""
const SETTINGS = {
  margin: [null, {preset: '2'}, {preset: '2.5'}, {preset: '3'}, {preset: 'custom', top: 2, bottom: 2.5, left: 3, right: 2.5}],
  columns: ['', 'twocolumn'], pagestyle: ['', 'plain', 'empty'], hyperref: ['off', 'on'],
  size: null, spacing: ['', '1', '1.25', '1.5', '2', 'custom'], indent: ['', '0pt', '2em'], parskip: ['', 'half', 'one'],
};
const KEYS = ['margin', 'columns', 'pagestyle', 'hyperref', 'size', 'spacing', 'indent', 'parskip', 'secnum', 'capname', 'capnum'];
const apply = (st, r) => { if (!r) return st; if (r.refused) throw new Error('refused: ' + r.refused + '\n' + st.doc.toString()); return st.update({changes: r.changes, selection: r.selection}).state; };
const at = (st, word, end) => { const i = st.doc.toString().indexOf(word); if (i < 0) throw new Error('no ' + word); return st.update({selection: {anchor: i + (end ? word.length : 0)}}).state; };
const build = (start, combo) => {
  let st = CM.EditorState.create({doc: start});
  KEYS.forEach((k) => { st = apply(st, P.layoutSpec(st, k, combo[k], {n: 1.4})); });
  const facts = P.preambleFacts(st.doc.toString().split('\\begin{document}')[0]);
  st = at(st, 'CENTER'); st = apply(st, P.alignSpec(st, 'center'));
  st = at(st, 'RIGHT'); st = apply(st, P.alignSpec(st, 'flushright'));
  st = at(st, 'QUOTE'); st = apply(st, P.quoteSpec(st));
  st = apply(st, P.tocSpec(st));
  st = at(st, 'BREAK HERE', true); st = apply(st, P.pageBreakSpec(st));
  st = at(st, 'REF HERE', true); st = apply(st, P.refSpec(st, 'eq:one', facts.amsmath));
  st = at(st, 'SEC HERE', true); st = apply(st, P.refSpec(st, 'sec:intro', false));
  st = at(st, 'URL HERE', true); st = apply(st, P.urlSpec(st, 'https://example.org/a_b#c', {url: facts.url, isMain: true}));
  return st.doc.toString();
};
const out = {};
for (const [name, start, sizes, allOn, numbers] of TEMPLATES) {
  const lists = Object.assign({}, SETTINGS, {size: sizes}, numbers);
  const combos = [];
  for (let i = 0; i < 6; i++) { const c = {}; KEYS.forEach((k) => { c[k] = lists[k][i % lists[k].length]; }); combos.push(c); }
  combos.push(allOn);
  out[name] = combos.map((c) => ({combo: c, text: build(start, c)}));
}
// the probe matrix: 单倍, 1.5 倍 and 双倍 at each body size
out.probes = [];
for (const [name, start, sizes] of TEMPLATES) {
  for (const size of sizes.filter(Boolean)) for (const spacing of ['1', '1.5', '2']) {
    let st = CM.EditorState.create({doc: start});
    st = apply(st, P.layoutSpec(st, 'size', size, {})); st = apply(st, P.layoutSpec(st, 'spacing', spacing, {}));
    out.probes.push({engine: name, size: size, spacing: spacing, text: st.doc.toString()});
  }
}
console.log(JSON.stringify(out));
"""

_BODY = ("\\begin{document}\n" + _PROBE + "\\maketitle\n\\section{Intro}\\label{sec:intro}\n{TEXT}\n\nCENTER {TEXT}\n\nRIGHT {TEXT}\n\n"
         "QUOTE {TEXT}\n\n\\begin{itemize}\n\\item One.\n\\item Two.\n\\end{itemize}\nA note\\footnote{{TEXT}} BREAK HERE\n\n"
         "\\begin{equation}\\label{eq:one}\nE = mc^2\n\\end{equation}\nSee REF HERE and SEC HERE and URL HERE.\n\n"
         "\\section{Second}\n{TEXT}\n\\end{document}\n")
_ZH = "中文正文，用来看行距、缩进和段间距。" * 6
_EN = "English body text to see the spacing, the indent and the space between paragraphs. " * 3
_TEMPLATES = [
    # each starts as a new paper does (22.11): A4 in the class options, which no setting touches
    ["xelatex", "\\documentclass[a4paper]{ctexart}\n\\usepackage{amsmath}\n\\title{标题}\n\\author{作者}\n" + _BODY.replace("{TEXT}", _ZH),
     ["", "10pt", "11pt", "12pt", "zihao=5", "zihao=-4"],
     {"margin": {"preset": "custom", "top": 2, "bottom": 2.5, "left": 3, "right": 2.5}, "columns": "twocolumn",
      "pagestyle": "empty", "hyperref": "on", "size": "zihao=-4", "spacing": "1.5", "indent": "0pt", "parskip": "half",
      "secnum": "zh", "capname": "en", "capnum": "within"},
     {"secnum": ["", "zh", "none", "zh"], "capname": ["zh", "en"], "capnum": ["", "within", "", "", "within"]}],
    ["pdflatex", "\\documentclass[a4paper]{article}\n\\usepackage[T1]{fontenc}\n\\usepackage{amsmath}\n\\title{Title}\n\\author{Author}\n"
     + _BODY.replace("{TEXT}", _EN), ["", "10pt", "11pt", "12pt"],
     {"margin": {"preset": "custom", "top": 2, "bottom": 2.5, "left": 3, "right": 2.5}, "columns": "twocolumn",
      "pagestyle": "empty", "hyperref": "on", "size": "12pt", "spacing": "1.5", "indent": "0pt", "parskip": "half",
      "secnum": "", "capname": "en", "capnum": "within"},
     {"secnum": ["", "none", "", ""], "capname": ["en"], "capnum": ["", "", "within", "", "within"]}],
]


@pytest.fixture(scope="module")
def built() -> dict:
    return _node("const TEMPLATES = " + json.dumps(_TEMPLATES) + ";\n" + _BUILD)


PT_PER_CM = 72.27 / 2.54
A4 = (597.50787, 845.04684)   # every new paper's (22.11, A3), in TeX points
SIZES = {"10pt": 10.0, "11pt": 10.95, "12pt": 12.0, "zihao=5": 10.53937, "zihao=-4": 12.045}
#: LaTeX's own single line at each size (size10/11/12.clo); ctex's zihao sizes are 1.2 times the size
SINGLE = {10.0: 12.0, 10.95: 13.6, 12.0: 14.5, 10.53937: 1.2 * 10.53937, 12.045: 1.2 * 12.045}


def _env(home: Path) -> dict:
    home.mkdir(exist_ok=True)
    return {"PATH": f"{TEXBIN}:/usr/bin:/bin", "HOME": str(home), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "max_print_line": "4000"}


def _probe(log: str) -> dict:
    m = re.search(r"^PROBE (.*)$", log, re.M)
    assert m, log[-2000:]
    return dict(re.findall(r"(\w+)=(\S*(?: (?:plus|minus) \S+)*)", m.group(1)))


def _pt(value: str) -> float:
    return float(value.split("pt")[0])


def _latexmk(folder: Path, text: str, engine: str, home: Path) -> tuple[subprocess.CompletedProcess, str]:
    folder.mkdir(parents=True)
    (folder / "main.tex").write_text(text, encoding="utf-8")
    done = subprocess.run([LATEXMK, "-norc", "-xelatex" if engine == "xelatex" else "-pdf", "-interaction=nonstopmode",
                           "-halt-on-error", "-file-line-error", "-no-shell-escape", "main.tex"],
                          cwd=folder, env=_env(home), capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    log = (folder / "main.log").read_text(encoding="utf-8", errors="replace") if (folder / "main.log").exists() else ""
    return done, log


@needs_node
@pytest.mark.skipif(LATEXMK is None, reason="latexmk is not installed")
@pytest.mark.parametrize("engine", ["xelatex", "pdflatex"])
def test_every_setting_at_every_value_compiles_and_measures_as_set(built, tmp_path, engine):
    """Seven documents per class -- six combinations in which every value of every setting appears at least once, and
    every setting on at once -- each with every paragraph tool applied, compiled by latexmk as the export would (in the
    main file's folder): a PDF, no error, and the probe's paper (A4 as the class says, whatever else is set), text width,
    body size, \\baselineskip, \\parindent, \\parskip, columns, hyperref and page numbers as set. (What the numbering
    settings print is read back by the next test.)"""
    docs = built[engine]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda k: _latexmk(tmp_path / f"doc{k}", docs[k]["text"], engine, tmp_path / "home"), range(len(docs))))
    s0 = 1.3 if engine == "xelatex" else 1.0
    for doc, (done, log) in zip(docs, results):
        c = doc["combo"]
        assert done.returncode == 0, json.dumps(c) + done.stdout[-2000:] + log[-2000:]
        assert (tmp_path / f"doc{docs.index(doc)}" / "main.pdf").read_bytes().startswith(b"%PDF-")
        assert not re.search(r"^! |LaTeX Error|Undefined control sequence|Reference `[^']*' .*undefined", log, re.M), log[-2000:]
        p = _probe(log)
        pw, ph = A4                                                            # left as the class option says
        assert abs(_pt(p["pw"]) - pw) < 0.01 and abs(_pt(p["ph"]) - ph) < 0.01, (c, p)
        if c["margin"]:
            m = c["margin"]
            left, right = (m["left"], m["right"]) if m["preset"] == "custom" else (float(m["preset"]),) * 2
            assert abs(_pt(p["tw"]) - (pw - (left + right) * PT_PER_CM)) < 0.05, (c, p)
        size = SIZES.get(c["size"], 10.53937 if engine == "xelatex" else 10.0)
        assert abs(float(p["size"]) - size) < 0.001, (c, p)
        single = SINGLE[size] * s0
        lines = {"": 1, "1": 1, "1.25": 1.25, "1.5": 1.5, "2": 2, "custom": 1.4}[c["spacing"]]
        assert abs(_pt(p["bls"]) - single * lines) < 0.02, (c, p)
        if c["indent"]:
            # 2em is two of the body font's em (its quad, within 1% of the size: T1 Computer Modern's at 10.95 pt is 10.89 pt)
            assert abs(_pt(p["pi"]) - (0 if c["indent"] == "0pt" else 2 * size)) <= 0.02 * size, (c, p)
        parskip = {"": 0, "half": 0.5, "one": 1}[c["parskip"]]
        assert abs(_pt(p["ps"]) - parskip * single) < 0.02, (c, p)                    # half a single line, a whole one
        assert p["two"] == ("1" if c["columns"] == "twocolumn" else "0")
        assert p["hyper"] == ("1" if c["hyperref"] == "on" else "0")
        assert p["noplain"] == ("1" if c["pagestyle"] == "empty" else "0")


@needs_node
@pytest.mark.skipif(LATEXMK is None, reason="latexmk is not installed")
def test_single_one_and_a_half_and_double_are_one_one_and_a_half_and_two_single_lines_at_every_size(built, tmp_path):
    """The probe compiles of 22.12's 行距: \\baselineskip at 单倍, 1.5 倍 and 双倍 for article at 10, 11 and 12 pt and
    ctexart at 五号, 小四, 10, 11 and 12 pt, each the document's own single line (LaTeX's; ctex's 1.3 times it) times 1, 1.5, 2."""
    def run(k: int) -> tuple[dict, dict]:
        probe = built["probes"][k]
        folder = tmp_path / f"probe{k}"
        folder.mkdir()
        (folder / "main.tex").write_text(probe["text"], encoding="utf-8")
        subprocess.run([probe["engine"], "-interaction=nonstopmode", "-halt-on-error", "main.tex"], cwd=folder,
                       env=_env(tmp_path / "home"), capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
        return probe, _probe((folder / "main.log").read_text(encoding="utf-8", errors="replace"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        measured = list(pool.map(run, range(len(built["probes"]))))
    assert len(measured) == 3 * (5 + 3)
    for probe, p in measured:
        size, s0 = SIZES[probe["size"]], (1.3 if probe["engine"] == "xelatex" else 1.0)
        assert abs(float(p["size"]) - size) < 0.001, (probe["size"], p)
        assert abs(_pt(p["bls"]) - SINGLE[size] * s0 * float(probe["spacing"])) < 0.02, (probe["engine"], probe["size"], probe["spacing"], p)


# -- 章节编号, 图表名称, 图表编号 (A3) compiled and read back from the PDF -------------------------------------------------

PDFTOTEXT = shutil.which("pdftotext") or next((p for p in ("/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext") if os.access(p, os.X_OK)), None)

_NUMBERED_BODY = ("\\begin{document}\n\\tableofcontents\n{CH1}\\section{Intro}\\label{sec:intro}\n"
                  "SREF\\ref{sec:intro}END\n\nSUBREF\\ref{sub:back}END\n\nSSREF\\ref{ss:deep}END\n\nFIGREF\\ref{fig:one}END\n\n"
                  "ZREF\\ref{fig:two}END\n\nTABREF\\ref{tab:one}END\n\n\\subsection{Back}\\label{sub:back}\nText.\n"
                  "\\subsubsection{Deep}\\label{ss:deep}\nText.\n\\section*{Starred}\nText.\n"
                  "\\begin{figure}[h]\\centering\\rule{1cm}{1cm}\\caption{CapOne}\\label{fig:one}\\end{figure}\n"
                  "{CH2}\\section{Methods}\nText.\n\\begin{figure}[h]\\centering\\rule{1cm}{1cm}\\caption{CapTwo}\\label{fig:two}\\end{figure}\n"
                  "\\begin{table}[h]\\centering\\caption{TabOne}\\label{tab:one}\\begin{tabular}{c}a\\end{tabular}\\end{table}\n"
                  "\\end{document}\n")


def _numbered_start(cls: str) -> str:
    # each \\ref a paragraph of its own: pdftotext joins a line broken after a hyphen (2-1 would read 21)
    chapters = cls in ("report", "ctexrep")
    body = _NUMBERED_BODY.replace("{CH1}", "\\chapter{Alpha}\n" if chapters else "").replace("{CH2}", "\\chapter{Beta}\n" if chapters else "")
    return f"\\documentclass[a4paper]{{{cls}}}\n\\usepackage{{amsmath}}\n" + body


#: (class, engine, 章节编号, 图表名称, 图表编号, hyperref): every value of each, hyperref on and off, ctex and not, chapters
#: and not; hyperref written first, so the numbering lines must go before it.
_NUMBERED = [
    ("ctexart", "xelatex", "zh", "zh", "within", "on"), ("ctexart", "xelatex", "zh", "en", "", "off"),
    ("ctexart", "xelatex", "", "en", "within", "off"), ("ctexart", "xelatex", "none", "zh", "", "on"),
    ("ctexart", "xelatex", "", "zh", "", "on"), ("ctexart", "xelatex", "none", "en", "", "off"),
    ("ctexrep", "xelatex", "zh", "zh", "within", "on"), ("ctexrep", "xelatex", "none", "en", "continuous", "off"),
    ("ctexrep", "xelatex", "", "zh", "", "on"), ("ctexrep", "xelatex", "zh", "en", "continuous", "on"),
    ("ctexrep", "xelatex", "none", "zh", "", "off"), ("ctexrep", "xelatex", "", "en", "within", "off"),
    ("article", "pdflatex", "", "en", "within", "on"), ("article", "pdflatex", "none", "en", "", "off"),
    ("article", "pdflatex", "", "en", "", "off"), ("article", "pdflatex", "none", "en", "", "on"),
    ("report", "pdflatex", "", "en", "within", "on"), ("report", "pdflatex", "none", "en", "continuous", "off"),
    ("report", "pdflatex", "", "en", "", "on"), ("report", "pdflatex", "none", "en", "", "on"),
]

_NUMBERED_BUILD = r"""
const apply = (st, r) => { if (!r) return st; if (r.refused) throw new Error('refused: ' + r.refused); return st.update({changes: r.changes}).state; };
console.log(JSON.stringify(DOCS.map(([start, secnum, capname, capnum, hyperref]) => {
  let st = CM.EditorState.create({doc: start});
  for (const [k, v] of [['hyperref', hyperref], ['secnum', secnum], ['capname', capname], ['capnum', capnum]]) st = apply(st, P.layoutSpec(st, k, v, {}));
  return st.doc.toString();
})));
"""


def _expected_numbers(cls: str, secnum: str, capnum: str) -> dict:
    """What the PDF should print: the reference to each heading, the figure and table numbers."""
    chapters = cls in ("report", "ctexrep")
    if secnum == "none":
        refs = {"S": "", "SUB": ""}
    elif secnum == "zh":
        refs = {"S": "一", "SUB": "（一）"}
    else:
        refs = {"S": "1.1", "SUB": "1.1.1"} if chapters else {"S": "1", "SUB": "1.1"}
    if capnum == "within":
        figs = ("1-1", "2-1", "2-1")
    elif chapters and capnum == "" and secnum != "none":
        figs = ("1.1", "2.1", "2.1")                     # the class's own, by chapter
    else:
        figs = ("1", "2", "1")                           # continuous (and report's own while chapters are unnumbered)
    return {"refs": refs, "figs": figs}


@needs_node
@pytest.mark.skipif(LATEXMK is None or PDFTOTEXT is None, reason="latexmk and pdftotext are needed")
def test_heading_numbers_and_captions_print_as_chosen_in_the_pdf(tmp_path):
    """Twenty documents written by 版式 (章节编号 × 图表名称 × 图表编号 × hyperref, on ctexart and ctexrep by XeLaTeX and
    article and report by pdfLaTeX), compiled by latexmk and read back by pdftotext: each heading, its line in the table of
    contents and every \\\\ref to it print the chosen number (一、 （一） 1. and none at all included), a starred heading
    none, 第一章 stays in a ctex chapter class unless headings are unnumbered, captions read 图 / Figure / 表 / Table with
    1, 1-1 or 1.1 as chosen and every \\\\ref to a figure or table prints the caption's number; no error, no undefined
    reference, and with hyperref no two anchors of the same name."""
    docs = _node("const DOCS = " + json.dumps([[_numbered_start(c), sn, cn, cp, h] for c, _e, sn, cn, cp, h in _NUMBERED]) + ";\n"
                 + _NUMBERED_BUILD)

    def run(k: int) -> tuple[subprocess.CompletedProcess, str, str]:
        folder = tmp_path / f"n{k}"
        done, log = _latexmk(folder, docs[k], _NUMBERED[k][1], tmp_path / "home")
        # -raw: in the order the page was drawn (a centred heading in a big face is otherwise read out of place)
        text = subprocess.run([PDFTOTEXT, "-raw", "-enc", "UTF-8", str(folder / "main.pdf"), "-"], capture_output=True, text=True,
                              timeout=60).stdout if (folder / "main.pdf").exists() else ""
        return done, log, text
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(len(_NUMBERED))))
    for (cls, _engine, secnum, capname, capnum, hyperref), text_in, (done, log, pdf) in zip(_NUMBERED, docs, results):
        case = f"{cls} 章节编号={secnum or 'default'} 图表名称={capname} 图表编号={capnum or 'default'} hyperref={hyperref}"
        assert done.returncode == 0, case + log[-2000:]
        assert not re.search(r"^! |LaTeX Error|Undefined control sequence|Reference `[^']*' .*undefined|same identifier|Token not allowed", log, re.M), case + log[-3000:]
        assert ("\\usepackage[hidelinks]{hyperref}\n\\begin{document}" in text_in) == (hyperref == "on"), case   # hyperref still last
        flat = re.sub(r"\s+", "", pdf)
        want = _expected_numbers(cls, secnum, capnum)
        # every \ref to a heading prints the heading's number (nothing at all when headings are unnumbered)
        assert f"SREF{want['refs']['S']}END" in flat and f"SUBREF{want['refs']['SUB']}END" in flat, (case, pdf)
        # each line of the contents carries its heading's number, and the first heading (before the first \\ref) the same
        chapters = cls in ("report", "ctexrep")
        heads = {"zh": ("一、Intro", "（一）Back", "一、Methods" if chapters else "二、Methods"), "none": ("Intro", "Back", "Methods")}.get(
            secnum, ("1.1Intro", "1.1.1Back", "2.1Methods") if cls in ("report", "ctexrep") else ("1Intro", "1.1Back", "2Methods"))
        front = flat[:flat.index("SREF")]
        assert all(head in front for head in heads) and front.count(heads[0]) == 2, (case, heads, pdf)
        if secnum == "none":
            # no line of the contents or the text starts with a number before a heading (page numbers end lines)
            assert not re.search(r"^\s*(?:[\d.]+|[一二]、|（[一二]）)\s*(?:Intro|Back|Deep|Methods)\b", pdf, re.M), (case, pdf)
        if secnum == "zh" and cls == "ctexart":
            assert "1.Deep" in flat and "SSREF1END" in flat, (case, pdf)              # the third level: 1.
        assert "Text.Starred" in flat, (case, pdf)                                     # no number before it
        if cls == "ctexrep":
            assert ("第一章" in flat) == (secnum != "none"), (case, pdf)               # 第一章 stays with 一、（一）
        if cls == "report":
            assert ("Chapter1" in flat) == (secnum != "none"), (case, pdf)
        # captions: the chosen names, the chosen numbers, and \ref prints each caption's number
        fig, tab = ("图", "表") if capname == "zh" else ("Figure", "Table")
        one, two, table = want["figs"]
        assert f"{fig}{one}:CapOne" in flat and f"{fig}{two}:CapTwo" in flat and f"{tab}{table}:TabOne" in flat, (case, pdf)
        assert f"FIGREF{one}END" in flat and f"ZREF{two}END" in flat and f"TABREF{table}END" in flat, (case, pdf)

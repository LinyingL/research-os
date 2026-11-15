"""DESIGN.md 22.12 M (task V17): what browser acceptance found on 2026-11-14 -- a real Chromium against the engine's own
page, an English paper (article, pdfLaTeX) and a Chinese one (ctexart, XeLaTeX) from 新建文稿, the exported PDF compared
by eye and by pdftotext -- and what fixed it, each finding by the test that failed before:

F2 a disabled button of the right column looks disabled; F3 the code's colours never reach the edit view; F4 the words
the edit view shows for the document's parts are the ones the PDF prints (Figure / 图, Abstract / 摘要, Contents, a
\\ref's bare number) and 版式's 图表编号 reads in them; F5 blank lines take no room and paragraphs are apart as 段间距
says; F6 headings show the numbers the PDF prints, as 版式's 章节编号 has them, in a chapter file too; F7 the page's
paragraphs are TeX's (text after a float in a paragraph goes on with it, \\par starts one) and words typed under a new
float start a paragraph of their own; F8 the image picker never lists a paper's exported PDF; F9 a PDF or EPS figure
shows its first page.

F4, F6 and F7 are measured against real compiles (TeX Live 2023, latexmk) read back by pdftotext."""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from researchos import latex_edit, project
from test_v17_latex_editor import _DRIVER, NODE, WEBAPP
from test_v17_latex_word import _node
from test_v17_layout_settings import _PANEL as _LAYOUT_PANEL

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
TEXBIN = Path("/Library/TeX/texbin")
LATEXMK = shutil.which("latexmk") or (str(TEXBIN / "latexmk") if (TEXBIN / "latexmk").exists() else None)
PDFTOTEXT = shutil.which("pdftotext") or next((p for p in ("/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext") if os.access(p, os.X_OK)), None)
needs_tex = pytest.mark.skipif(LATEXMK is None or PDFTOTEXT is None, reason="latexmk and pdftotext are needed")
CSS = (WEBAPP / "workspace.css").read_text(encoding="utf-8")


def _env(home: Path) -> dict:
    home.mkdir(exist_ok=True)
    return {"PATH": f"{TEXBIN}:/usr/bin:/bin", "HOME": str(home), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "max_print_line": "4000"}


def _compile(folder: Path, files: dict[str, str], main: str, engine: str, home: Path, layout: str = "-raw") -> str:
    """`files` written into `folder`, `main` compiled by latexmk as the export would, its PDF read back by pdftotext."""
    folder.mkdir(parents=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    done = subprocess.run([LATEXMK, "-norc", "-xelatex" if engine == "xelatex" else "-pdf", "-interaction=nonstopmode", "-halt-on-error",
                           "-no-shell-escape", main], cwd=folder, env=_env(home), capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, (folder.name, done.stdout[-3000:])
    pdf = folder / (main[:-4] + ".pdf")
    out = folder / (main[:-4] + (".html" if layout == "-bbox-layout" else ".txt"))
    subprocess.run([PDFTOTEXT, layout, str(pdf), str(out)], check=True, timeout=60)
    return out.read_text(encoding="utf-8")


# -- F2: a disabled button looks disabled -------------------------------------------------------------------------------

def test_f2_a_disabled_button_of_the_right_column_looks_disabled_as_its_selects_do():
    """Found: with 显示代码 on, 加粗, 引文块 and every 插入 button were disabled but looked exactly like enabled ones (the
    selects beside them did look gray). Now gray words, no fill and a faint edge -- palette tokens only, no hover."""
    rule = re.search(r"\.latex-side \.btn:disabled, \.latex-side \.btn:disabled:hover \{([^}]*)\}", CSS)
    assert rule, "no rule for a disabled button of the right column"
    body = rule.group(1)
    assert "color: var(--gray)" in body and "background: transparent" in body and "border-color: var(--line)" in body
    assert "cursor: default" in body
    # an enabled .btn is ink on paper-alt with a strong edge (app.html): the disabled one differs in all three
    assert re.search(r"\.latex-side \.btn:disabled \.latex-key \{ color: inherit; \}", CSS)
    assert not re.findall(r"#[0-9A-Fa-f]{3,6}\b", body)


# -- F3: the code's colours only in 显示代码 ------------------------------------------------------------------------------

_SWITCH_PAGE = _LAYOUT_PANEL[:_LAYOUT_PANEL.index("const MAIN_PRE")] + r"""
const TEXT = '\\documentclass{article}\n\\begin{document}\nPrices rose 50 percent in 3 years.\n\\end{document}\n';
await openFile('paper/main.tex', TEXT, {mains: ['paper/main.tex'], format: {main: 'paper/main.tex', engine: 'pdflatex', folder: 'paper', preamble: null, local: []}});
const lang = P.codeLook(CM)[0];
out.editView = lang.isActiveAt(view().state, 60);
inside(side(), (e) => e.tag === 'button' && e.classList.contains('latex-switch'))[0].fire('click');
out.code = lang.isActiveAt(view().state, 60);
inside(side(), (e) => e.tag === 'button' && e.classList.contains('latex-switch'))[0].fire('click');
out.back = lang.isActiveAt(view().state, 60);
// a text the edit view cannot show opens as code, coloured
await openFile('paper/bad.tex', 'Text {unclosed\n', {mains: [], format: {main: null, engine: 'pdflatex', folder: 'paper', preamble: null, local: []}});
out.unsure = lang.isActiveAt(view().state, 2);
console.log(JSON.stringify(out));
"""


def _page(body: str) -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    prelude += "const P = window.ROLatex._pure;\n"
    script = prelude + "(async () => {\n" + body + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });"
    done = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=120)
    assert done.stdout.strip(), done.stderr[-3000:]
    got = json.loads(done.stdout.strip().splitlines()[-1])
    assert "error" not in got, got.get("error")
    return got


@needs_node
def test_f3_the_codes_syntax_colours_are_given_only_while_the_code_is_shown():
    """Found: numbers ("50", "3", "000") carried CodeMirror's highlight class (a gray-brown) in the edit view. The LaTeX
    language and its colours are now in a compartment given only while 显示代码 shows the code."""
    got = _page(_SWITCH_PAGE)
    assert got["editView"] is False                              # was: True -- the code's colours on the page's words
    assert got["code"] is True and got["back"] is False
    assert got["unsure"] is True                                 # a file opened as code is coloured as code


# -- F4 and F6: the document's words and numbers, as the PDF prints them ---------------------------------------------

_VIEW = r"""
const DOCS = __DOCS__;
const textOf = (e) => typeof e === 'string' ? e : (e.textContent || '') + (e.children || []).map(textOf).join('');
const drawn = (w, st) => (w ? textOf(w.toDOM({dispatch() {}, state: st})) : '');
out.docs = {};
for (const d of DOCS) {
  F.hooks.doc = d.doc || null;
  const st = CM.EditorState.create({doc: d.text, extensions: [P.wordExtensions(CM, {})]});
  const v = st.field(F.field), text = d.text, atoms = [];
  v.deco.between(0, text.length, (a, b, x) => { if (x.spec.atom) atoms.push({a, b, s: x.spec}); });
  atoms.sort((x, y) => x.a - y.a);
  const heads = [], caps = [], refs = [], lines = [], envHeads = [], notes = [];
  for (const c of v.read.cmds) {
    if (c.role !== 'heading') continue;
    const arg = c.args.filter((x) => !x.opt).pop(), open = atoms.find((x) => x.s.kind === 'open' && x.a <= c.from && arg.from < x.b);
    const w = open && open.s.widget;
    heads.push({title: P.plainWords(text.slice(arg.from + 1, arg.to - 1)), number: w ? drawn(w, st) : null, tight: !!(w && / latex-hnum-tight/.test(w.cls))});
  }
  for (const x of atoms) {
    const said = drawn(x.s.widget, st);
    if (x.s.kind === 'ref') refs.push(said);
    if (x.s.kind === 'line') lines.push(said);
    if (x.s.kind === 'begin' && said) envHeads.push(said);
    if (x.s.kind === 'footnote') notes.push(said);
    if ((x.s.kind === 'figure' || x.s.kind === 'table') && x.s.widget && x.s.widget.kind === 'unit') caps.push(said);   // a float drawn as one chip
    if (x.s.widget && x.s.widget.toDOM) {
      const el = x.s.widget.toDOM({dispatch() {}, state: st});
      const find = (e) => e && typeof e === 'object' ? ((e.className || '').split(' ').includes('latex-caption-word') ? e : (e.children || []).map(find).find(Boolean)) : null;
      const word = find(el);
      if (word) { const cap = v.read.cmds.find((c) => c.name === 'caption' && c.to >= x.b && c.from <= x.b + 400); caps.push(textOf(word) + (cap ? P.plainWords(text.slice(cap.args[0].from + 1, cap.args[0].to - 1)) : '')); }
    }
  }
  out.docs[d.name] = {heads, caps, refs, lines, envHeads, notes};
}
F.hooks.doc = null;
console.log(JSON.stringify(out));
"""


def _view(docs: list[dict]) -> dict:
    return _node(_VIEW.replace("__DOCS__", json.dumps(docs)))["docs"]


_ZH_SEC = ("\\renewcommand{\\thesection}{\\chinese{section}}\n\\renewcommand{\\thesubsection}{（\\chinese{subsection}）}\n"
           "\\renewcommand{\\thesubsubsection}{\\arabic{subsubsection}}\n"
           "\\ctexset{section/name={,、},section/aftername={},subsection/aftername={},subsubsection/name={,.}}\n")


def _probe(cls: str, secnum: str, capname: str, capnum: str) -> str:
    """One document of the matrix: every heading level, a starred one, \\paragraph, a figure, a table, \\ref to each,
    the abstract (no chapters) or chapters, the lists and the bibliography, and \\appendix."""
    chap = cls in ("report", "book", "ctexrep", "ctexbook")
    pre = [f"\\documentclass{{{cls}}}", "\\usepackage{amsmath,amsthm,graphicx}"]
    if secnum == "zh":
        pre.append(_ZH_SEC.rstrip("\n"))
    if secnum == "none":
        pre.append(f"\\setcounter{{secnumdepth}}{{{-1 if chap else 0}}}")
    if capname == "en":
        pre += ["\\renewcommand{\\figurename}{Figure}", "\\renewcommand{\\tablename}{Table}"]
    if capnum == "within":
        pre += (["\\renewcommand{\\thefigure}{\\thechapter-\\arabic{figure}}", "\\renewcommand{\\thetable}{\\thechapter-\\arabic{table}}"] if chap else
                ["\\counterwithin{figure}{section}", "\\counterwithin{table}{section}", "\\renewcommand{\\thefigure}{\\arabic{section}-\\arabic{figure}}",
                 "\\renewcommand{\\thetable}{\\arabic{section}-\\arabic{table}}"])
    body = ["\\begin{document}", "\\tableofcontents", "\\listoffigures", "\\listoftables"]
    if not chap:
        body += ["\\begin{abstract}", "AbsText.", "\\end{abstract}"]
    else:
        body += ["\\chapter{ChapOne}\\label{c1}"]
    body += ["\\section{SecOne}\\label{s1}", "\\subsection{SubOne}\\label{ss1}", "\\subsubsection{SubSubOne}\\label{sss1}", "\\paragraph{ParaOne} x",
             "\\section*{StarSec}", "\\section{SecTwo}",
             "\\begin{figure}[h]", "\\centering", "\\includegraphics[width=1cm]{example-image}", "\\caption{FigCap}\\label{f1}", "\\end{figure}",
             "\\begin{table}[h]", "\\centering", "\\caption{TabCap}\\label{t1}", "\\begin{tabular}{l}", "x \\\\", "\\end{tabular}", "\\end{table}",
             "RefA \\ref{s1} RefB \\ref{ss1} RefC \\ref{sss1} RefD \\ref{f1} RefE \\ref{t1}" + (" RefF \\ref{c1}" if chap else "") + " Note\\footnote{FootText}",
             "\\begin{proof}", "PrfText.", "\\end{proof}",
             "\\begin{thebibliography}{9}", "\\bibitem{a} BibA.", "\\end{thebibliography}", "\\appendix"]
    if chap:
        body += ["\\chapter{AppChap}\\label{ac}", "\\section{AppSecC}\\label{asc}"]
    body += ["\\section{AppSec}\\label{a1}", "\\subsection{AppSub}\\label{a2}", "RefG \\ref{a1} RefH \\ref{a2}" + (" RefI \\ref{ac}" if chap else ""), "\\end{document}"]
    return "\n".join(pre + body) + "\n"


#: the matrix compiled (class, 章节编号, 图表名称, 图表编号): every value of the three 版式 settings in an article-like and a
#: chapter class, by pdfLaTeX and by XeLaTeX with ctex
_MATRIX = [("article", "", "", ""), ("article", "", "", "within"), ("article", "none", "", ""), ("report", "", "", ""), ("report", "", "", "within"),
           ("report", "none", "", ""), ("book", "", "", ""), ("ctexart", "", "", ""), ("ctexart", "zh", "", ""), ("ctexart", "zh", "en", "within"),
           ("ctexart", "none", "", ""), ("ctexrep", "", "", ""), ("ctexrep", "zh", "", "within"), ("ctexrep", "none", "en", ""), ("ctexbook", "zh", "", "")]


def _check_against_pdf(name: str, shown: dict, pdf: str) -> dict:
    """What the edit view shows (`shown`) against what the PDF prints (`pdf`, pdftotext -raw): each heading's line, each
    caption, each \\ref, the document's own words. Answers what was compared."""
    lines = [ln.strip() for ln in pdf.splitlines() if ln.strip()]
    seen = {"numbered": 0, "unnumbered": 0, "refs": 0}
    for h in shown["heads"]:
        title, number = h["title"], h["number"]
        if number is None:
            # no number on the page: none in the PDF either (the heading's line is its title alone, or \paragraph's run-in)
            assert any(ln == title or ln.startswith(title + " x") for ln in lines), (name, title, "printed with a number")
            seen["unnumbered"] += 1
        elif re.match(r"^(?:Chapter|Appendix) ", number):
            k = lines.index(number)                                          # report and book: the number on a line of its own
            assert lines[k + 1] == title, (name, number, lines[k + 1])
            seen["numbered"] += 1
        else:
            want = number + ("" if h["tight"] else " ") + title
            assert want in lines, (name, want)
            seen["numbered"] += 1
    for cap in shown["caps"]:
        assert cap in lines, (name, cap)                                     # "Figure 1: FigCap", "图 1-1: FigCap"
    printed = re.findall(r"\bRef([A-I]) ?([^\sR]*)", " ".join(lines))
    assert len(printed) == len(shown["refs"]), (name, printed, shown["refs"])
    for (key, value), chip in zip(printed, shown["refs"]):
        if chip.startswith("引用："):
            continue                                                         # unknown here: said by its key, never a wrong number
        assert value == chip, (name, key, chip, value)
        seen["refs"] += 1
    for head in shown["envHeads"]:
        assert any(ln == head or ln.startswith(head + ".") for ln in lines), (name, head)   # Abstract / 摘要, Proof / 证明
    for line in shown["lines"]:
        m = re.match(r"^(.*)（导出时生成）$", line)
        if m:
            assert any(ln == m.group(1) or ln.endswith(m.group(1)) for ln in lines), (name, m.group(1))   # Contents, 插图, References …
    assert shown["notes"] == ["1"]
    return seen


@pytest.fixture(scope="module")
def matrix(tmp_path_factory) -> dict:
    if LATEXMK is None or PDFTOTEXT is None:
        pytest.skip("latexmk and pdftotext are needed")
    base = tmp_path_factory.mktemp("m-matrix")
    docs = {"_".join(x or "d" for x in combo): _probe(*combo) for combo in _MATRIX}

    def one(name):
        engine = "xelatex" if name.startswith("ctex") else "pdflatex"
        return name, _compile(base / name, {"main.tex": docs[name]}, "main.tex", engine, base / "home")

    with ThreadPoolExecutor(max_workers=6) as pool:
        pdfs = dict(pool.map(one, docs))
    return {"docs": docs, "pdfs": pdfs, "shown": _view([{"name": n, "text": t} for n, t in docs.items()])}


@needs_node
@needs_tex
def test_f4_f6_every_heading_caption_reference_and_name_the_page_shows_is_what_the_pdf_prints(matrix):
    """Found: in the English article the table's caption read 「表 1：」 and the abstract 「摘要」 (the PDF: "Table 1:",
    "Abstract"), and "Introduction" had no number (the PDF: "1 Introduction"). Fifteen documents -- article, report,
    book, ctexart, ctexrep, ctexbook; 章节编号 1 / 1.1 / 1.1.1, 一、（一）1. and 不编号; 图表名称 the class's and Figure in a
    ctex class; 图表编号 continuous, by chapter and by section -- each with a starred heading, \\paragraph and \\appendix,
    compiled and read back: every heading shows the number its PDF line has (or none where it has none), every caption
    the PDF's words and number, every \\ref the number the PDF prints, and Abstract, Proof, Contents, the lists and the
    bibliography the document's own words."""
    totals = {"numbered": 0, "unnumbered": 0, "refs": 0}
    for name in matrix["docs"]:
        for k, v in _check_against_pdf(name, matrix["shown"][name], matrix["pdfs"][name]).items():
            totals[k] += v
    assert totals["numbered"] >= 75 and totals["unnumbered"] >= 25 and totals["refs"] >= 80, totals
    s = matrix["shown"]
    # the findings' own cases, by name
    assert s["article_d_d_d"]["heads"][0] == {"title": "SecOne", "number": "1", "tight": False}
    assert s["article_d_d_d"]["caps"] == ["Figure 1: FigCap", "Table 1: TabCap"] and s["article_d_d_d"]["envHeads"][0] == "Abstract"
    assert s["ctexart_d_d_d"]["caps"] == ["图 1: FigCap", "表 1: TabCap"] and s["ctexart_d_d_d"]["envHeads"][0] == "摘要"
    assert [h["number"] for h in s["ctexart_zh_d_d"]["heads"]][:3] == ["一、", "（一）", "1."]
    assert [h["number"] for h in s["ctexrep_d_d_d"]["heads"]][:2] == ["第一章", "1.1"]
    assert [h["number"] for h in s["report_d_d_d"]["heads"]][:2] == ["Chapter 1", "1.1"]
    assert all(h["number"] is None for h in s["article_none_d_d"]["heads"])
    assert s["ctexrep_none_en_d"]["caps"][0] == "Figure 1: FigCap"
    assert [h["number"] for h in s["article_d_d_d"]["heads"] if h["title"].startswith("App")] == ["A", "A.1"]
    assert [h["number"] for h in s["ctexrep_d_d_d"]["heads"] if h["title"].startswith("App")] == ["附录 A", "A.1", "A.2", "A.2.1"]
    assert s["article_d_d_d"]["lines"][0] == "Contents（导出时生成）" and s["ctexart_d_d_d"]["lines"][0] == "目录（导出时生成）"


_CHAPTER_MAIN = r"""\documentclass{article}
\usepackage{graphicx}
\begin{document}
\section{One}
Text.\footnote{N1}
\begin{figure}[h]\centering\rule{1cm}{1cm}\caption{MainFig}\end{figure}
\input{early}
\input{chap}
\section{Four}
\end{document}
"""
_EARLY = "\\section{Two}\\label{two}\nWords.\n"
_CHAP = ("\\section{Three}\\label{three}\n\\subsection{ThreeA}\\label{threea}\n"
         "\\begin{figure}[h]\n\\centering\n\\includegraphics[width=1cm]{example-image}\n\\caption{ChapFig}\\label{cf}\n\\end{figure}\n"
         "RefA \\ref{three} RefB \\ref{threea} RefC \\ref{cf} Note\\footnote{N2}\n")
_REP_MAIN = r"""\documentclass{ctexrep}
\begin{document}
\include{c1}
\include{c2}
\end{document}
"""
_C1 = "\\chapter{OneC}\n\\section{OneS}\n"
_C2 = "\\chapter{TwoC}\\label{tc}\n\\section{TwoS}\\label{ts}\nRefA \\ref{tc} RefB \\ref{ts}\n"


@needs_node
@needs_tex
def test_f6_a_chapter_file_is_numbered_from_its_place_in_its_main_file(tmp_path):
    """A chapter file shows the numbers its main file prints for it: the engine gives the main file's preamble and what it
    reads before the file (`text_before`: its body to the \\input, an earlier \\input's file put in its place); the page
    counts on from there. Compiled: article with two \\input files, ctexrep with \\include."""
    reader = lambda files: (lambda p: files.get(p, ""))  # noqa: E731
    cases = []
    for name, files, main, chapter, engine in (("art", {"main.tex": _CHAPTER_MAIN, "early.tex": _EARLY, "chap.tex": _CHAP}, "main.tex", "chap.tex", "pdflatex"),
                                               ("rep", {"main.tex": _REP_MAIN, "c1.tex": _C1, "c2.tex": _C2}, "main.tex", "c2.tex", "xelatex")):
        before = latex_edit.text_before(main, chapter, reader(files))
        assert before is not None
        pre = files[main][:files[main].index("\\begin{document}")]
        pdf = _compile(tmp_path / name, files, main, engine, tmp_path / "home")
        cases.append((name, files[chapter], {"pre": pre, "local": None, "before": before}, pdf))
    shown = _view([{"name": n, "text": t, "doc": d} for n, t, d, _p in cases])
    art, rep = shown["art"], shown["rep"]
    assert [h["number"] for h in art["heads"]] == ["3", "3.1"] and art["caps"] == ["Figure 2: ChapFig"] and art["refs"] == ["3", "3.1", "2"]
    assert art["notes"] == ["2"]
    assert [h["number"] for h in rep["heads"]] == ["第二章", "2.1"] and rep["refs"] == ["二", "2.1"]
    for name, _t, _d, pdf in cases:
        lines = [ln.strip() for ln in pdf.splitlines() if ln.strip()]
        s = shown[name]
        for h in s["heads"]:
            assert h["number"] + " " + h["title"] in lines, (name, h)
        assert all(c in lines for c in s["caps"])
        printed = dict(re.findall(r"\bRef([A-C]) ?([^\sR]*)", " ".join(lines)))
        assert [printed[k] for k in "ABC"[:len(s["refs"])]] == s["refs"]
    # not sure, no number: a file the main file brings in twice, or through a file that brings in others
    assert latex_edit.text_before("main.tex", "chap.tex", reader({"main.tex": _CHAPTER_MAIN + "\\input{chap}\n"})) is None
    assert latex_edit.text_before("main.tex", "chap.tex", reader({"main.tex": _CHAPTER_MAIN, "early.tex": "\\input{x}\n", "chap.tex": _CHAP})) is None
    none = _view([{"name": "lost", "text": _CHAP, "doc": {"pre": _CHAPTER_MAIN[:30], "local": None, "before": None}}])["lost"]
    assert [h["number"] for h in none["heads"]] == [None, None] and none["caps"] == ["Figure: ChapFig"]


def test_f6_the_engine_gives_a_chapter_file_what_its_main_file_reads_before_it(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    (root / "paper").mkdir()
    for name, text in (("main.tex", _CHAPTER_MAIN), ("early.tex", _EARLY), ("chap.tex", _CHAP)):
        (root / "paper" / name).write_text(text, encoding="utf-8")
    got = latex_edit.read_tex(root, "paper/chap.tex")["format"]
    assert got["main"] == "paper/main.tex" and got["preamble"] == "\\documentclass{article}\n\\usepackage{graphicx}\n"
    assert got["before"].startswith("\n\\section{One}") and "\\section{Two}" in got["before"] and "chap" not in got["before"]
    assert "before" not in latex_edit.read_tex(root, "paper/main.tex")["format"]   # a main file counts its own text


@needs_node
def test_f4_the_layout_tabs_figure_numbering_reads_in_the_documents_words():
    """「图表编号」's options name the figure as the document does: 「连续编号（Figure 1）」 in an English paper, 「图 1」 in a
    Chinese one, and after 图表名称 changes, the new name."""
    got = _page(_LAYOUT_PANEL[:_LAYOUT_PANEL.index("const MAIN_PRE")] + r"""
const EN = '\\documentclass{article}\n\\begin{document}\nText.\n\\end{document}\n';
await openFile('paper/main.tex', EN, {mains: ['paper/main.tex'], format: {main: 'paper/main.tex', engine: 'pdflatex', folder: 'paper', preamble: null, local: []}});
tab('版式').fire('click');
out.en = row('图表编号').labels;
const ZH = '\\documentclass{ctexrep}\n\\begin{document}\n\\chapter{一}\n\\end{document}\n';
await openFile('zh/main.tex', ZH, {mains: ['zh/main.tex'], format: {main: 'zh/main.tex', engine: 'xelatex', folder: 'zh', preamble: null, local: []}});
tab('版式').fire('click');
out.zh = row('图表编号').labels;
choose('图表名称', 'en');
out.zhEn = row('图表编号').labels;
console.log(JSON.stringify(out));
""")
    assert got["en"] == ["连续编号（Figure 1）", "按章或节编号（Figure 2-3）"]           # was: 连续编号（图 1）
    assert got["zh"] == ["连续编号（图 1）", "按章编号（图 2.3）", "按章或节编号（图 2-3）"]
    assert got["zhEn"] == ["连续编号（Figure 1）", "按章编号（Figure 2.3）", "按章或节编号（Figure 2-3）"]


# -- F5: blank lines take no room; 段间距 on the page ------------------------------------------------------------------

_VISUAL = r"""
const make = (doc, anchor) => CM.EditorState.create({doc, selection: {anchor: anchor || 0}, extensions: [P.wordExtensions(CM, {})]});
// the page's lines: the text split where a line break is not hidden, each with its line classes and what it shows
const visual = (st) => { const doc = st.doc.toString(), v = st.field(F.field);
  const cov = (p) => { let c = false; v.deco.between(p, p + 1, (a, b, d) => { if (d.spec.atom && a <= p && p < b) c = true; }); return c; };
  const cls = new Map(); v.deco.between(0, doc.length, (a, b, d) => { if (a === b && d.spec.class) cls.set(a, ((cls.get(a) || '') + ' ' + d.spec.class).trim()); });
  const got = []; let from = 0;
  for (let p = 0; p <= doc.length; p++) if (p === doc.length || (doc[p] === '\n' && !cov(p))) { got.push([(cls.get(from) || '').replace(/latex-/g, ''), P.plainOf(v, from, p)]); from = p + 1; }
  return got.slice(1, -1); };   // the preamble's line and 正文结束 left out
const D = (body, pre) => '\\documentclass{article}\n' + (pre || '') + '\\begin{document}\n' + body + '\n\\end{document}\n';
const ret = (st, at) => { st = st.update({selection: {anchor: at}}).state; const r = P.enterSpec(CM, st, st.field(F.field), false); return st.update(r).state; };
const type = (st, s) => { const h = st.selection.main.head; return st.update({changes: {from: h, insert: s}, selection: {anchor: h + s.length}, userEvent: 'input.type'}).state; };
const R = {};
R.one = visual(make(D('One.\n\nTwo.')));
R.two = visual(make(D('One.\n\n\nTwo.')));
R.spaces = visual(make(D('One.\n  \n\t\nTwo.')));
R.comments = visual(make(D('One.\n\n% note\n\nTwo.\n% x\n\nThree.')));
R.par = visual(make(D('One.\n\\par\nTwo.')));
R.heading = visual(make(D('\\section{S}\n\nOne.\n\n\\section{T}\n\nTwo.')));
R.list = visual(make(D('One.\n\n\\begin{itemize}\n\\item a\n\n\\item b\n\\end{itemize}\n\nTwo.')));
R.figure = visual(make(D('One.\n\n\\begin{figure}\n\\centering\n\\includegraphics{a.png}\n\\caption{F}\n\\end{figure}\n\nTwo.')));
// Return makes an empty paragraph the page shows, where the cursor stands; typed into, it is a paragraph of its own
let st = make(D('One.\n\nTwo.'));
st = ret(st, st.doc.toString().indexOf('One.') + 4);
R.afterReturn = visual(st); R.caretLine = st.doc.lineAt(st.selection.main.head).text;
st = type(st, 'New');
R.typed = visual(st); R.typedSource = st.doc.toString().slice(st.doc.toString().indexOf('One.'), st.doc.toString().indexOf('\\end{document}'));
st = make(D('One.\n\nTwo.')); st = ret(st, st.doc.toString().indexOf('Two.'));
R.atStart = visual(st); R.atStartCaret = st.doc.sliceString(st.selection.main.head, st.selection.main.head + 4);
st = make(D('One.')); st = ret(st, st.doc.toString().indexOf('One.') + 4);
R.atEnd = visual(st); R.atEndCaret = st.doc.lineAt(st.selection.main.head).text === '';
st = make(D('One.\nTwo.')); st = ret(st, st.doc.toString().indexOf('One.') + 4);
R.split = visual(st);
out.R = R;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def visual() -> dict:
    return _node(_VISUAL)["R"]


@needs_node
def test_f5_blank_lines_between_paragraphs_take_no_room_whatever_their_number(visual):
    """Found: each blank source line between paragraphs was a full empty line (with 行距 双倍 a gap of about three lines),
    where the PDF has none. Now one or two blank lines -- spaces on them, a comment between -- are a new line and no
    more; \\par alone is one too; around headings, lists and figures no empty line is left."""
    para = [["para", "One."], ["para", "Two."]]
    assert visual["one"] == para                                       # was: [para One.], ['', ''], [para Two.]
    assert visual["two"] == para and visual["spaces"] == para and visual["par"] == para
    assert visual["comments"] == para + [["para", "Three."]]
    assert visual["heading"] == [["hline", "S"], ["para-first", "One."], ["hline", "T"], ["para-first", "Two."]]
    assert [x for x in visual["list"] if not x[1]] == []              # no empty line above, inside or after the list
    assert [x for x in visual["figure"] if not x[1]] == []


@needs_node
def test_f5_return_makes_an_empty_paragraph_the_page_shows_and_typing_there_makes_it_one(visual):
    """A blank line is still where words can be typed: Return at a paragraph's end shows one empty paragraph (a line, its
    first-line indent, the cursor on it) -- the source has a blank line on each side of it, so the words typed there
    are a paragraph of their own; at a paragraph's start the empty one is above; at the end of the text too; at a line's
    end inside a paragraph, Return splits it with no empty line."""
    assert visual["afterReturn"] == [["para", "One."], ["para", ""], ["para", "Two."]] and visual["caretLine"] == ""
    assert visual["typed"] == [["para", "One."], ["para", "New"], ["para", "Two."]]
    assert visual["typedSource"] == "One.\n\nNew\n\nTwo.\n"
    assert visual["atStart"] == [["para", "One."], ["para", ""], ["para", "Two."]] and visual["atStartCaret"] == "Two."
    assert visual["atEnd"] == [["para", "One."], ["para", ""]] and visual["atEndCaret"] is True
    assert visual["split"] == [["para", "One."], ["para", "Two."]]


@needs_node
def test_f5_paragraph_spacing_on_the_page_follows_段间距():
    """段间距 (A2): none, half a line or a line -- the page's --latex-parskip, above each paragraph's first line."""
    got = _page(_LAYOUT_PANEL[:_LAYOUT_PANEL.index("const MAIN_PRE")] + r"""
const MAIN = '\\documentclass{article}\n\\begin{document}\nOne.\n\nTwo.\n\\end{document}\n';
await openFile('paper/main.tex', MAIN, {mains: ['paper/main.tex'], format: {main: 'paper/main.tex', engine: 'pdflatex', folder: 'paper', preamble: null, local: []}});
const box = byClass('latex-editor'); box.style = {props: {}, setProperty(k, v) { this.props[k] = v; }};
tab('版式').fire('click');
out.none = box.style.props['--latex-parskip'];
choose('段间距', 'half'); out.half = box.style.props['--latex-parskip'];
choose('段间距', 'one'); out.one = box.style.props['--latex-parskip'];
console.log(JSON.stringify(out));
""")
    assert got == {"none": "0", "half": "calc(0.9em * var(--latex-stretch, 1))", "one": "calc(1.8em * var(--latex-stretch, 1))"}
    rule = re.search(r"\.cm-line\.latex-para, \.latex-editor \.cm-content\.latex-word \.cm-line\.latex-para-first \{ padding-top: var\(--latex-parskip, 0\); \}", CSS)
    assert rule


# -- F7: the page's paragraphs are TeX's ------------------------------------------------------------------------------

_FIG = "\\begin{figure}[h]\n\\centering\n\\rule{1cm}{2mm}\n\\caption{Cap}\n\\end{figure}"
_TAB = "\\begin{table}[h]\n\\centering\n\\caption{TCap}\n\\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}\n\\end{table}"
#: each case a few lines, each line of words starting with its own token (Qa, Qb …)
_CORPUS = [
    "Qa one.\nQb two.", "Qc three.\n\nQd four.", "Qe five.\n\n\nQf six.", "Qg x.\n% a comment\nQh y.", "Qi x.\n% c\n\nQj y.",
    "Qk x.\n\n% c\nQl y.", "Qm x.\n" + _FIG + "\nQn y.", "Qo x.\n\n" + _FIG + "\nQp y.",
    "Qq x.\n\\begin{itemize}\n\\item Qr in.\n\\end{itemize}\nQs y.", "Qt x.\n\\[ x = 1 \\]\nQu y.", "Qv x.\n\\par\nQw y.", "Qx x. \\par\nQy y.",
    "Qz x.\n\\par Qba y.", "\\section{Head}\nQbb y.", "\\section{Head two}\n\nQbc y.", "Qbd x.\n\\begin{quote}\nQbe in.\n\\end{quote}\nQbf y.",
    "Qbg x.\n\\noindent Qbh y.", "Qbi x.\n\n\\noindent Qbj y.", "Qbk x.\n" + _TAB + "\n\nQbl y.", "Qbm x.\n\\label{lab}\nQbn y.",
    "Qbo x.\n\n\\label{lab2}\nQbp y.", "\\begin{center}\nQbq in.\n\\end{center}\nQbr y.", "Qbs x.\n\\begin{equation}\nx = 2\n\\end{equation}\n\nQbt y.",
    "Qbu x.\n\\vspace{1em}\nQbv y.", "Qbw x.\n\n\\vspace{1em}\nQbx y.", "Qby x.\n\\newpage\nQbz y.", "Qca x.\n\n% c\n\nQcb y.", "Qcc x.\\\\\nQcd y.",
    "Qce x.\n\n" + _TAB + "\nQcf y.", "Qcg x.\n\\begin{equation}\nx = 3\n\\end{equation}\nQch y.", "Qci x.\n\n\n\nQcj y.", "Qck x.\n\n" + _FIG + "\n\nQcl y.",
    "Qcm x.\n" + _FIG + "\n" + _TAB + "\nQcn y.", "Qco x.\n\n\\begin{enumerate}\n\\item Qcp in.\n\\end{enumerate}\n\nQcq y.", "\\subsection*{Star}\n% c\nQcr y.",
    "Qcs x.\n\\par\n\nQct y.", "Qcu x. \\par Qcv y.", "\\section{Fl}\n" + _FIG + "\nQcw y.", "Qcx x.\n\n" + _FIG + "\n" + _TAB + "\nQcy y.",
]
#: lines inside a list, a quote or center: their own environments' lines, not the running text's paragraphs
_INNER = {"Qr", "Qbe", "Qbq", "Qcp"}
_PARA_PRE = ("\\documentclass{article}\n\\usepackage{indentfirst}\n\\setlength{\\parindent}{4em}\n\\setlength{\\parskip}{0pt}\n"
             "\\usepackage[a4paper,margin=2cm]{geometry}\n\\pagestyle{empty}\n")

_PARAS = r"""
const DOC = __DOC__;
const st = CM.EditorState.create({doc: DOC, extensions: [P.wordExtensions(CM, {})]});
const v = st.field(F.field), cls = new Map(), atoms = [];
v.deco.between(0, DOC.length, (a, b, d) => { if (a === b && d.spec.class) cls.set(a, (cls.get(a) || '') + ' ' + d.spec.class); if (d.spec.atom) atoms.push([a, b, d.spec]); });
const at = (p) => atoms.find((x) => x[0] <= p && p < x[1]);
const res = {}, re = /Q[a-z]+/g, body = DOC.indexOf('\\begin{document}');
for (let m = re.exec(DOC); m; m = re.exec(DOC)) {
  if (m.index < body) continue;
  // the page's line the token is on: back over line breaks that are hidden
  let L = st.doc.lineAt(m.index);
  while (L.number > 1 && at(L.from - 1)) L = st.doc.line(L.number - 1);
  // a paragraph starts there when the line's class says so and nothing is shown before the token on it
  let shown = false;
  for (let p = L.from; p < m.index && !shown; p++) { const x = at(p); shown = x ? ['space', 'text', 'parbreak'].includes(x[2].kind) : /\S/.test(DOC[p]); }
  let start = /latex-para/.test(cls.get(L.from) || '') && !shown;
  // or a paragraph break is drawn right before it on its line (\par between words)
  const pb = atoms.find((x) => x[2].kind === 'parbreak' && x[1] <= m.index && !DOC.slice(x[1], m.index).trim());
  if (pb) start = true;
  res[m[0]] = start;
}
out.res = res;
console.log(JSON.stringify(out));
"""


@needs_node
@needs_tex
def test_f7_the_pages_paragraphs_are_texs_on_a_corpus(tmp_path):
    """Found: "Second paragraph here." then a table then "Var" printed as one paragraph, "Second paragraph here. Var",
    while the page drew "Var" as a paragraph of its own. Measured on a corpus of 39 cases -- blank lines, comment lines,
    text after floats in a paragraph and between paragraphs, after lists, quotes and displayed formulas, \\par on its
    own line, at a line's end, at its start and between words, \\noindent, \\label, \\vspace, \\newpage, a heading --
    compiled with every paragraph indented (indentfirst, 4em) and read back with pdftotext's word positions: each line
    of words starts a paragraph on the page exactly where it starts one in the PDF."""
    doc = _PARA_PRE + "\\begin{document}\n" + "\n\n".join(_CORPUS) + "\n\\end{document}\n"
    html = _compile(tmp_path / "paras", {"p.tex": doc}, "p.tex", "pdflatex", tmp_path / "home", layout="-bbox-layout")
    starts, firsts = {}, []
    for line in re.findall(r"<line[^>]*>(.*?)</line>", html, re.S):
        words = re.findall(r'<word xMin="([\d.]+)"[^>]*>(.*?)</word>', line)
        if words:
            firsts.append(float(words[0][0]))
        for k, (x, w) in enumerate(words):
            if re.fullmatch(r"Q[a-z]+", w):
                starts[w] = (k == 0, float(x))
    margin = min(firsts)
    tex = {tok: first and x > margin + 20 for tok, (first, x) in starts.items()}
    page = _node(_PARAS.replace("__DOC__", json.dumps(doc)))["res"]
    assert len(tex) == 77 and set(tex) == set(page)
    differ = sorted((tok, tex[tok], page[tok]) for tok in tex if tok not in _INNER and tex[tok] != page[tok])
    assert differ == []          # was: 8 -- text after a float between paragraphs (Qp, Qcf, Qcy) and \par (Qw, Qy, Qba, Qcv …)
    assert tex["Qn"] is False and tex["Qp"] is True and tex["Qcw"] is True and tex["Qcy"] is True   # the corpus says what TeX does


_SLOT = r"""
const make = (doc) => CM.EditorState.create({doc, extensions: [P.wordExtensions(CM, {})]});
const D = '\\documentclass{article}\n\\begin{document}\nSecond paragraph here.\n\\begin{table}[htbp]\n\\centering\n\\caption{T}\n\\begin{tabular}{l}\nx \\\\\n\\end{tabular}\n\\end{table}\n\n\\end{document}\n';
let st = make(D);
const slot = D.indexOf('\\end{table}') + 12;
const v0 = st.field(F.field);
let hidden = false; v0.atoms.between(slot, slot, (a, b) => { if (a < slot && slot < b) hidden = true; });
out.slotOpen = !hidden;
st = st.update({changes: {from: slot, insert: 'Var'}, selection: {anchor: slot + 3}, userEvent: 'input.type'}).state;
out.typed = st.doc.toString();
// pasted, and typed by an input method, the same
let s2 = make(D); s2 = s2.update({changes: {from: slot, insert: 'Pasted words'}, userEvent: 'input.paste'}).state; out.pasted = s2.doc.toString();
// a figure's too; and after a float in a paragraph's middle, words typed on the line after it are not touched
const DF = D.replace(/\\begin\{table\}[\s\S]*\\end\{table\}/, '\\begin{figure}\n\\centering\n\\includegraphics{a.png}\n\\caption{F}\n\\end{figure}');
let s3 = make(DF); const at3 = DF.indexOf('\\end{figure}') + 13; s3 = s3.update({changes: {from: at3, insert: 'Var'}, userEvent: 'input.type'}).state; out.figure = s3.doc.toString();
console.log(JSON.stringify(out));
"""


@needs_node
def test_f7_words_typed_on_the_line_after_a_new_float_start_a_paragraph_of_their_own():
    """Found: 插入 > 表格 at the end of "Second paragraph here." and "Var" typed on the line under the table printed
    "Second paragraph here. Var". The line after a float (before \\end{document}, another float or the like) is a place
    the cursor can stand, and words typed, pasted or composed there are written after a blank line, so the PDF starts a
    paragraph with them, as the page shows."""
    got = _node(_SLOT)
    assert got["slotOpen"] is True
    assert "\\end{table}\n\nVar\n\\end{document}" in got["typed"]                 # was: \end{table}\nVar
    assert "\\end{table}\n\nPasted words\n\\end{document}" in got["pasted"]
    assert "\\end{figure}\n\nVar\n\\end{document}" in got["figure"]


# -- F8: the image picker never lists a paper's exported PDF ----------------------------------------------------------

def test_f8_the_image_picker_never_lists_any_papers_exported_pdf(tmp_path):
    """Found: 图片 listed paper/english.pdf and paper/main.pdf, the PDFs exported beside english.tex and main.tex (only
    the open draft's own paper's was left out). Now no `.pdf` beside a document `.tex` of the same name and no PDF an
    export recorded is offered; a figure made by a `standalone` .tex is."""
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    for rel, data in (("paper/english.tex", latex_edit.TEMPLATES["en_paper"].replace("<TITLE>", "E")),
                      ("paper/main.tex", latex_edit.TEMPLATES["zh_paper"].replace("<TITLE>", "Z")),
                      ("figures/plot.tex", "\\documentclass{standalone}\n\\begin{document}x\\end{document}\n")):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(data, encoding="utf-8")
    for rel in ("paper/english.pdf", "paper/main.pdf", "figures/fig1.pdf", "figures/plot.pdf", "figures/a.png"):
        (root / rel).write_bytes(b"%PDF-1.4\n" if rel.endswith(".pdf") else b"\x89PNG\r\n\x1a\n")
    for draft in ("paper/english.tex", "paper/main.tex"):
        listed = [x["path"] for x in latex_edit.insertables(root, draft, "images")["images"]]
        assert listed == ["figures/a.png", "figures/fig1.pdf", "figures/plot.pdf"], (draft, listed)   # was: + the other paper's PDF


# -- F9: a PDF or EPS figure shows its first page -----------------------------------------------------------------------

_EPS = "%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 100 50\nnewpath 10 10 moveto 90 40 lineto stroke\nshowpage\n"


@pytest.fixture
def figures(tmp_path) -> Path:
    if LATEXMK is None:
        pytest.skip("latexmk is needed to make a real PDF figure")
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    _compile(tmp_path / "fig", {"fig.tex": "\\documentclass{standalone}\n\\begin{document}\\rule{2cm}{1cm}\\end{document}\n"}, "fig.tex", "pdflatex", tmp_path / "home")
    (root / "figures").mkdir()
    shutil.copy(tmp_path / "fig" / "fig.pdf", root / "figures" / "fig1.pdf")
    (root / "figures" / "line.eps").write_text(_EPS, encoding="ascii")
    (root / "figures" / "broken.pdf").write_bytes(b"not a pdf")
    return root


def test_f9_the_engine_draws_a_pdf_figures_first_page_into_its_own_cache_never_the_project(figures):
    """Found: a PDF figure showed only its file name (../figures/fig1.pdf) while a png or jpg had its picture; most
    papers' figures are PDFs. The engine draws its first page as a PNG (pdftoppm, else macOS's sips; Ghostscript for an
    EPS) into `<home>/latex-thumbs/`, kept while the file is the same; nothing is written into the project."""
    from researchos import paths

    before = sorted(str(p.relative_to(figures)) for p in figures.rglob("*"))
    png = latex_edit.figure_thumbnail(figures, "figures/fig1.pdf", "pid")
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and 200 < len(png) < latex_edit.THUMB_LIMIT
    kept = list((paths.researchos_home() / "latex-thumbs" / "pid").glob("*.png"))
    assert len(kept) == 1 and kept[0].read_bytes() == png
    assert latex_edit.figure_thumbnail(figures, "figures/fig1.pdf", "pid") == png     # from the cache
    if latex_edit._tool("gs") or latex_edit._tool("sips"):
        try:
            assert latex_edit.figure_thumbnail(figures, "figures/line.eps", "pid").startswith(b"\x89PNG")
        except latex_edit.WorkspaceError as exc:                       # no converter for EPS here: the name alone
            assert exc.code == "no_thumbnail"
    assert sorted(str(p.relative_to(figures)) for p in figures.rglob("*")) == before
    with pytest.raises(latex_edit.WorkspaceError) as no:
        latex_edit.figure_thumbnail(figures, "figures/broken.pdf", "pid")
    assert no.value.code == "no_thumbnail" and no.value.status == 404


@pytest.mark.parametrize("rel", ["../outside.pdf", ".researchos/x.pdf", "figures/a.png", "/etc/x.pdf", "figures/none.pdf", "figures//fig1.pdf"])
def test_f9_the_thumbnail_is_guarded_as_the_editors_paths_are(figures, rel):
    with pytest.raises(latex_edit.WorkspaceError) as refused:
        latex_edit.figure_thumbnail(figures, rel, "pid")
    assert refused.value.status in (400, 404)


def test_f9_a_link_to_a_figure_outside_the_project_is_refused(figures, tmp_path):
    outside = tmp_path / "secret.pdf"
    shutil.copy(figures / "figures" / "fig1.pdf", outside)
    (figures / "figures" / "link.pdf").symlink_to(outside)
    with pytest.raises(latex_edit.WorkspaceError):
        latex_edit.figure_thumbnail(figures, "figures/link.pdf", "pid")


def test_f9_the_route_serves_the_png_and_the_picker_marks_pdfs_as_drawn(figures):
    from researchos.webapp import server

    httpd = server.build_server(figures, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=30)
        conn.request("GET", "/api/latex/thumb?path=figures/fig1.pdf")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200 and resp.getheader("Content-Type") == "image/png" and body.startswith(b"\x89PNG")
        assert resp.getheader("X-Content-Type-Options") == "nosniff"
        conn.request("GET", "/api/latex/thumb?path=figures/broken.pdf")
        resp = conn.getresponse()
        assert resp.status == 404 and json.loads(resp.read())["state"].endswith("no_thumbnail")
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    (figures / "paper").mkdir()
    (figures / "paper" / "main.tex").write_text(latex_edit.TEMPLATES["en_paper"].replace("<TITLE>", "T"), encoding="utf-8")
    images = {x["path"]: x["thumb"] for x in latex_edit.insertables(figures, "paper/main.tex", "images")["images"]}
    assert images == {"figures/fig1.pdf": True, "figures/line.eps": True, "figures/broken.pdf": True}   # was: False -- the name alone


@needs_node
def test_f9_the_page_asks_the_engine_for_a_pdf_or_eps_figures_picture():
    """In the edit view a figure's \\includegraphics of a .pdf or .eps gets a picture from /api/latex/thumb (a png or jpg
    from the asset route, as before); one that does not come is taken out and the file's name stays."""
    got = _page(_LAYOUT_PANEL[:_LAYOUT_PANEL.index("const MAIN_PRE")] + r"""
const TEXT = '\\documentclass{article}\n\\usepackage{graphicx}\n\\begin{document}\n\\begin{figure}\n\\centering\n\\includegraphics{../figures/fig1.pdf}\n\\includegraphics{../figures/a.png}\n\\includegraphics{../figures/b.eps}\n\\caption{F}\n\\end{figure}\n\\end{document}\n';
await openFile('paper/main.tex', TEXT, {mains: ['paper/main.tex'], format: {main: 'paper/main.tex', engine: 'pdflatex', folder: 'paper', preamble: null, local: []}});
const st = view().state, v = st.field(P.wordField(CM).field), srcs = [];
v.deco.between(0, st.doc.length, (a, b, d) => { if (d.spec.widget && d.spec.widget.toDOM) { const el = d.spec.widget.toDOM({dispatch() {}, state: st});
  inside(el, (e) => e.tag === 'img').forEach((i) => srcs.push(i.src)); } });
out.srcs = srcs;
console.log(JSON.stringify(out));
""")
    assert got["srcs"] == ["/api/latex/thumb?path=figures%2Ffig1.pdf", "/api/workspace/asset?path=figures%2Fa.png", "/api/latex/thumb?path=figures%2Fb.eps"]

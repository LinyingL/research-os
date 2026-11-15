"""DESIGN.md 22.1, 22.7 and 22.12 (task V17): what the adversarial reviews of 格式与插入, 版式 and the edit view (B1, B2)
found, each reproduced here as it was reported and kept from coming back. Under node on the vendored CodeMirror and
KaTeX (the edit view's own filter, the line-break filter and the history in the order the page puts them) and on the
stand-in page of test_v17_latex_editor; the LaTeX the tools now write is compiled by latexmk. Every test names its
scenario."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_v17_latex_editor import _DRIVER, NODE, WEBAPP
from test_v17_latex_word import _node

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
TEXBIN = Path("/Library/TeX/texbin")
LATEXMK = shutil.which("latexmk") or (str(TEXBIN / "latexmk") if (TEXBIN / "latexmk").exists() else None)
POPPLER = {k: shutil.which(k) or next((p for p in (f"/opt/homebrew/bin/{k}", f"/usr/local/bin/{k}") if os.access(p, os.X_OK)), None)
           for k in ("pdftotext", "pdfinfo")}

# The edit view as the page builds it: its own extensions first, the history, then (for a file of its own line ending)
# the line separator and the line-break filter last -- transaction filters run last to first.
_EDIT = r"""
window.katex = kctx.katex;
const said = [];
const make = (doc, anchor, sep) => { const ext = [P.wordExtensions(CM, {say: (s) => said.push(s)}), CM.history()];
  if (sep) ext.push(CM.EditorState.lineSeparator.of(sep), P.breakFilter(CM, sep));
  return CM.EditorState.create({doc, selection: {anchor: anchor || 0}, extensions: ext}); };
const T = (st, at, text, ev, to) => st.update({changes: {from: at, to: to == null ? at : to, insert: text}, selection: {anchor: at + text.length}, userEvent: ev || 'input.type'}).state;
const D = (st, from, to, ev) => st.update({changes: {from, to}, selection: {anchor: from}, userEvent: ev || 'delete.backward'}).state;
const E = (body, pre, cls) => '\\documentclass{' + (cls || 'article') + '}\n' + (pre || '') + '\\begin{document}\n' + body + '\n\\end{document}\n';
const body = (d) => d.slice(d.indexOf('\\begin{document}\n') + 17, d.lastIndexOf('\n\\end{document}'));
const at = (d, s, k) => d.indexOf(s) + (k || 0);
const sure = (text) => P.unsureWhy(text, P.readLatex(text));
const atomsIn = (st) => { const got = []; st.field(F.field).deco.between(0, st.doc.length, (a, b, d) => { if (d.spec.atom) got.push({a, b, d}); }); return got.sort((x, y) => x.a - y.a || x.b - y.b); };
const rawShown = (st) => { const doc = st.doc.toString(); let text = '', p = 0; atomsIn(st).forEach(({a, b}) => { if (b <= p) return; text += doc.slice(p, a); p = b; }); return text + doc.slice(p); };
const undoAll = (st) => { let x = st; while (CM.undo({state: x, dispatch: (tr) => { x = tr.state; }})); return x; };

"""


def _edit(body: str) -> dict:
    return _node(_EDIT + body + "\nconsole.log(JSON.stringify(out));")


# -- the edit view: nothing typed, pasted or deleted loses or hides the researcher's words -------------------------------

@needs_node
def test_deleting_the_line_break_after_a_hidden_comment_takes_the_comment_never_the_next_line():
    got = _edit(r"""
let d = E('\\section{Introduction} % TODO\nThis paragraph matters.', '', 'ctexart'), p = at(d, 'This');
out.r1 = body(D(make(d, p), p - 1, p).doc.toString());
d = E('甲乙。% 待补\n\\section{信息}\n正文。', '', 'ctexart'); p = at(d, '待补') + 2;
out.r2 = body(D(make(d, p), p, p + 1, 'delete.forward').doc.toString());
d = E('最后一段。% 待补', '', 'ctexart'); p = at(d, '待补') + 2;
const st4 = D(make(d, p), p, p + 1, 'delete.forward'); out.r4 = st4.doc.toString().split('\n').slice(-3); out.r4sure = sure(st4.doc.toString());
d = E('word% c\nnext'); p = at(d, 'next');
out.tight = body(D(make(d, p), p - 1, p).doc.toString());
""")
    assert got["r1"] == "\\section{Introduction} This paragraph matters."     # was: \section{Introduction} % TODOThis paragraph …
    assert got["r2"] == "甲乙。\\section{信息}\n正文。"                         # was: 甲乙。% 待补\section{信息}: the heading gone
    assert got["r4"] == ["\\documentclass{ctexart}", "\\begin{document}", "最后一段。\\end{document}", ""][1:] and got["r4sure"] is None
    assert got["tight"] == "wordnext"                                      # what TeX printed: the comment had taken the break


@needs_node
def test_typing_at_the_end_of_a_line_whose_comment_is_hidden_goes_before_the_comment():
    got = _edit(r"""
const d = E('最后一段。% 待补\n\n下一段。', '', 'ctexart'), p = at(d, '待补') + 2;
said.length = 0;
out.typed = body(T(make(d, p), p, '补充').doc.toString()); out.said = said.slice();
""")
    assert got["typed"] == "最后一段。补充% 待补\n\n下一段。" and got["said"] == []   # was: refused, 「这里不能输入文字。」


@needs_node
def test_a_pasted_line_break_of_the_other_ending_is_still_escaped_and_split_as_the_edit_view_pastes():
    got = _edit(r"""
let d = E('Para 5 here.'), p = at(d, 'Para');
out.lfWindows = body(T(make(d, p, '\n'), p, 'Costs rose 50%\r\nthen fell ', 'input.paste').doc.toString());
const dc = d.replace(/\n/g, '\r\n'), st = make(dc, 0, '\r\n'); p = st.doc.toString().indexOf('Para');
const pasted = T(st, p, '50% done\nnext {line} ', 'input.paste');
out.crlf = pasted.doc.sliceString(0, pasted.doc.length, '\r\n'); out.crlfBody = body(pasted.doc.toString());
d = E('\\section{Part 7}\nParagraph 7 here.'); const a = at(d, 'Part') + 3, b = at(d, 'graph 7');
out.overHeading = body(T(make(d, a, '\n'), a, 'X\r\nY', 'input.paste', b).doc.toString());
""")
    assert got["lfWindows"] == "Costs rose 50\\%\n\nthen fell Para 5 here."      # was: 50% left raw, the line break commented out
    assert got["crlfBody"] == "50\\% done\n\nnext \\{line\\} Para 5 here."        # was: raw % and braces; a new paragraph
    assert "\r\n\r\n" in got["crlf"] and "\n" not in got["crlf"].replace("\r\n", "")
    assert got["overHeading"].startswith("\\section{Par") and "}" in got["overHeading"].split("\n")[0]   # the heading kept closed


@needs_node
def test_an_input_methods_words_go_where_typing_puts_them_escaped_as_one_undo_step():
    got = _edit(r"""
const W = P.wordField(CM);
const comp = (d, p, text, noStart) => { let st = make(d, p); const s0 = noStart ? null : W.composeStart(st); if (s0) st = st.update(s0).state;
  const q = st.selection.main.head; st = T(st, q, text, 'input.type.compose'); const during = body(st.doc.toString());
  const r = W.composeFix(st); const st2 = r ? st.update(r).state : st;
  return {during, doc: body(st2.doc.toString()), refused: (r && r.refused) || null, undone: body(undoAll(st2).doc.toString()) === body(d), sure: sure(st2.doc.toString())}; };
let d = E('\\begin{itemize}\n  \\item first point\n\\end{itemize}');
out.item = comp(d, at(d, '  \\item'), '你');
out.itemLate = comp(d, at(d, '  \\item'), '你', true);           // the start not seen: put right when it ends
d = E('Intro.\n\\section{Introduction}'); out.heading = comp(d, at(d, '\\section'), '你');
d = E('Body text 50.'); out.percent = comp(d, at(d, '50') + 2, '%');
d = E('最后一段。% 待补\n\n下一段。', '', 'ctexart'); out.comment = comp(d, at(d, '待补') + 2, '补充', true);
d = E('今天是\\today', '', 'ctexart'); out.cjk = comp(d, at(d, '\\today') + 6, '你');
""")
    assert got["item"]["doc"] == "\\begin{itemize}\n  \\item 你first point\n\\end{itemize}"   # was: 你  \item first point
    assert got["itemLate"]["during"].startswith("\\begin{itemize}\n你") and got["itemLate"]["doc"] == got["item"]["doc"]
    assert got["heading"]["doc"] == "Intro.\n\\section{你Introduction}"                     # was: in the paragraph before
    assert got["percent"]["during"] == "Body text 50%." and got["percent"]["doc"] == "Body text 50\\%."
    assert got["comment"]["doc"] == "最后一段。补充% 待补\n\n下一段。"                        # was: % 待补补充, never printed
    assert got["cjk"]["doc"] == "今天是\\today{}你"
    for k in ("item", "itemLate", "heading", "percent", "comment", "cjk"):
        assert got[k]["undone"] is True and got[k]["sure"] is None and got[k]["refused"] is None, k   # one ⌘Z takes it all back


@needs_node
def test_typing_at_the_end_of_an_environments_last_line_stays_on_that_line():
    got = _edit(r"""
out.cases = [];
for (const [b, k] of [['\\begin{itemize}\n  \\item b \\cite{k}\n\\end{itemize}', 'Z'], ['\\begin{itemize}\n  \\item See \\ref{s}\n\\end{itemize}\n\\begin{itemize}\n  \\item Next\n\\end{itemize}', '.'],
    ['\\begin{quote}\nA $x$\n\\end{quote}', 'Z'], ['\\begin{center}\nThe \\LaTeX{}\n\\end{center}', 'Z'], ['\\begin{itemize}\n  \\item a\\footnote{n}\n\\end{itemize}', 'Z']]) {
  const d = E(b), p = d.indexOf('\n', d.indexOf(b.split('\n')[1]));
  out.cases.push(body(T(make(d, p), p, k).doc.toString()));
}
// one key at a time, as the researcher types "5% & 6"
let d = E('\\begin{itemize}\n  \\item one\n\\end{itemize}'), st = make(d, at(d, 'one') + 3);
for (const ch of '5% & 6') st = T(st, st.selection.main.head, ch);
out.keys = body(st.doc.toString());
""")
    assert got["cases"] == ["\\begin{itemize}\n  \\item b \\cite{k}Z\n\\end{itemize}",                   # was: \item b Z\cite{k}
                            "\\begin{itemize}\n  \\item See \\ref{s}.\n\\end{itemize}\n\\begin{itemize}\n  \\item Next\n\\end{itemize}",
                            "\\begin{quote}\nA $x$Z\n\\end{quote}", "\\begin{center}\nThe \\LaTeX{}Z\n\\end{center}",
                            "\\begin{itemize}\n  \\item a\\footnote{n}Z\n\\end{itemize}"]
    assert got["keys"] == "\\begin{itemize}\n  \\item one5\\% \\& 6\n\\end{itemize}"                   # was: \item one5  6\&\%


@needs_node
def test_a_letter_or_an_option_bracket_typed_after_a_command_never_joins_it():
    got = _edit(r"""
let d = E('今天是\\today', '', 'ctexart'), p = at(d, '\\today') + 6;
out.cjk = body(T(make(d, p), p, '你').doc.toString());
d = E('\\begin{itemize}\n  \\item one\n  \\item \n\\end{itemize}'); p = at(d, '\\item \n') + 6;
out.item = body(T(make(d, p), p, '[1] first').doc.toString());
d = E('Para Q.\\\\\nx'); p = at(d, '\nx') + 1;
out.lineBreak = body(T(make(d, p), p, '[x] after').doc.toString());
out.star = body(T(make(d, p), p, '*star').doc.toString());
d = E('\\begin{theorem}\nx\n\\end{theorem}', '\\newtheorem{theorem}{Theorem}\n'); p = at(d, '\nx\n') + 1;
out.theorem = body(T(make(d, p), p, '[1] ').doc.toString());
d = E('Plain [words].'); p = at(d, '[');
out.plain = body(T(make(d, p), p, '[').doc.toString());
""")
    assert got["cjk"] == "今天是\\today{}你"                                   # was: \today你, an undefined control sequence
    assert got["item"].endswith("\\item {}[1] first\n\\end{itemize}")         # was: \item [1] first, the bullet became "1"
    assert got["lineBreak"] == "Para Q.\\\\\n{}[x] afterx" and got["star"] == "Para Q.\\\\\n{}*starx"
    assert got["theorem"] == "\\begin{theorem}\n{}[1] x\n\\end{theorem}"
    assert got["plain"] == "Plain [[words]."                                  # in running text a bracket is a bracket


@needs_node
def test_codemirrors_other_editing_keys_change_nothing_in_the_edit_view():
    got = _edit(r"""
const d = E('Paragraph 9 with \\textbf{bold 9} and $a_9^2$.\n\n\\section{Intro}\n\\begin{itemize}\n  \\item one\n\\end{itemize}'), line = 'Paragraph 9 with \\textbf{bold 9} and $a_9^2$.';
const st = make(d, at(d, 'Paragraph')), p = at(d, '\\textbf');
out.same = [
  st.update({changes: {from: at(d, line) + line.length, insert: '\n' + line}, userEvent: 'input.copyline'}).state,
  st.update({changes: {from: p - 1, to: p + 1, insert: '\\ '}, userEvent: 'move.character'}).state,
  st.update({changes: {from: at(d, 'Intro'), insert: '  '}, userEvent: 'input.indent'}).state,
  st.update({changes: {from: at(d, '  \\item'), to: at(d, '  \\item') + 2}, userEvent: 'delete.dedent'}).state,
  st.update({changes: {from: at(d, 'Paragraph'), insert: '% '}, userEvent: 'comment.line'}).state,
  st.update({changes: {from: at(d, 'Paragraph'), insert: 'x'}, userEvent: 'move.line'}).state,
].map((s) => s.doc.toString() === d);
out.typed = T(st, at(d, 'with'), 'x').doc.toString() !== d;
""")
    assert got["same"] == [True] * 6                       # was: the line copied as escaped code; "with\ textbf{bold 9}"; \section{  Intro}
    assert got["typed"] is True


@needs_node
def test_listings_inline_code_and_addresses_with_percent_are_read_as_their_own_raw_text():
    got = _edit(r"""
const d = E('See \\url{https://example.org/a%20b} and \\href{https://x.org/a%20b#c}{the site} now. % real\nCode \\lstinline[language=C]{a_b}, \\lstinline|x{y| and \\path|a%b|.', '\\usepackage{hyperref,listings}\n');
out.sure = sure(d);
out.raws = P.readLatex(d).raws.map((r) => [r.kind, d.slice(r.from, r.to)]);
out.shown = rawShown(make(d)).trim();
const st = make(d), verbs = []; st.field(F.field).deco.between(0, d.length, (a, b, x) => { if (x.spec.kind === 'verb') verbs.push(x.spec.widget.text); });
out.verbs = verbs;
const p = at(d, 'a_b') + 1; out.enter = T(make(d, p), p, '\n').doc.toString() === d || !T(make(d, p), p, '\n').doc.toString().includes('a\n');
""")
    assert got["sure"] is None                                    # was: 「这个文件里有没结束的环境或公式」, a valid paper opened as code
    assert got["raws"] == [["comment", "% real"], ["verb", "\\lstinline[language=C]{a_b}"], ["verb", "\\lstinline|x{y|"], ["verb", "\\path|a%b|"]]
    assert got["shown"] == "See  and  now. Code ,  and ." and got["verbs"] == ["a_b", "x{y", "a%b"]   # was: |a_b| shown raw
    assert got["enter"] is True


@needs_node
def test_an_environments_options_are_hidden_with_its_begin():
    got = _edit(r"""
const d = E('\\begin{minipage}{0.5\\textwidth}\nText\n\\end{minipage}\n\\begin{enumerate}[label=(\\alph*)]\n\\item a\n\\end{enumerate}', '\\usepackage{enumitem}\n');
out.shown = rawShown(make(d));
""")
    assert got["shown"] == "\n\nText\na\n"                         # was: a raw "}" and ")]"


@needs_node
def test_return_on_a_lists_only_empty_item_leaves_no_empty_list():
    got = _edit(r"""
const d = E('Before.\n\\begin{itemize}\n  \\item \n\\end{itemize}\nAfter.'), p = at(d, '\\item ') + 6, st = make(d, p);
const r = P.enterSpec(CM, st, st.field(F.field), false), st2 = st.update(r).state;
out.doc = body(st2.doc.toString()); out.sure = sure(st2.doc.toString());
""")
    assert got["doc"] == "Before.\n\nAfter." and got["sure"] is None   # was: \begin{itemize}\n\end{itemize}, "missing \item"


# -- on the page: the switch follows what the text is -------------------------------------------------------------------

_PAGE = r"""
El.prototype.setAttribute = function (k, v) { (this.attrs || (this.attrs = {}))[k] = String(v); };
const specs = [];
FakeView.scrollIntoView = real.EditorView.scrollIntoView;
const apply = (view, tr) => { view.state = tr.state; for (const listen of tr.state.facet(real.EditorView.updateListener)) listen({docChanged: tr.docChanged, selectionSet: !!tr.selection, changes: tr.changes, view, state: tr.state}); };
FakeView.prototype.dispatch = function (spec) { specs.push(spec); apply(this, this.state.update(spec)); };
FakeView.prototype.focus = function () {};
global.requestAnimationFrame = (f) => f();
const out = {};
const view = () => viewsMade[viewsMade.length - 1];
const inside = (el, test, acc) => { acc = acc || []; if (el && typeof el === 'object') { if (test(el)) acc.push(el); (el.children || []).forEach((k) => inside(k, test, acc)); } return acc; };
const side = () => byClass('latex-side');
const sw = () => inside(side(), (e) => e.classList.contains('latex-switch'))[0];
const why = () => inside(side(), (e) => e.classList.contains('latex-code-why'))[0];
const field = () => view().state.field(L._pure.wordField(CM).field, false);
const doc = () => view().state.doc.toString();
const state = () => ({pressed: sw().attrs['aria-pressed'], disabled: !!sw().disabled, why: why().classList.contains('hidden') ? '' : why().textContent, word: !!field()});
const f = (path, text) => ({path, text, revision: 'r1', line_ending: 'lf', read_only: false, read_only_reason: null, links: [], mains: []});
const openFile = async (path, text) => { L.open(path); await tick(); gets.shift().res(f(path, text)); await tick(); await tick(); };
const later = () => timers.splice(0).forEach((fn) => fn());
const scrolled = (from) => specs.slice(from).some((s) => [].concat(s.effects || []).some((e) => e && e.is && e.is(real.EditorView.scrollIntoView(0).type)));
// ⌘Z in the edit view brings back a text it cannot show: the code turns on, saying why
await openFile('a.tex', '\\documentclass{article}\n\\begin{document}\nHello world.\n\\end{document}\n');
const H = doc().indexOf('Hello');
sw().fire('click');
view().dispatch({changes: {from: H, insert: '{'}, userEvent: 'input.type'}); later();
view().dispatch({changes: {from: H + 6, insert: '}'}, userEvent: 'input.type'}); later();
const n0 = specs.length;
sw().fire('click');
out.offAgain = Object.assign(state(), {scrolled: scrolled(n0)});
real.undo({state: view().state, dispatch: (tr) => apply(view(), tr)});
later();
out.afterUndo = Object.assign(state(), {doc: doc().split('\n')[2]});
// a file opened as code because it was unsure: once the brace is closed, the switch is free again
await openFile('b.tex', 'Hello {world.\n');
out.opened = state();
view().dispatch({changes: {from: doc().indexOf('.'), insert: '}'}, userEvent: 'input.type'});
later();
out.fixed = state();
sw().fire('click');
out.clicked = state();
console.log(JSON.stringify(out));
"""


def _page(script: str) -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    done = subprocess.run([NODE, "-"], input=prelude + "(async () => {\n" + script + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });",
                          capture_output=True, text=True, timeout=120)
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    return out


@pytest.fixture(scope="module")
def page() -> dict:
    return _page(_PAGE)


@needs_node
def test_undo_that_brings_back_a_text_the_edit_view_cannot_show_turns_the_code_on_saying_why(page):
    assert page["offAgain"] == {"pressed": "false", "disabled": False, "why": "", "word": True, "scrolled": True}   # the cursor kept in sight
    assert page["afterUndo"] == {"pressed": "true", "disabled": True, "why": "这个文件里有没配对的括号，只能显示代码。", "word": False,
                                 "doc": "{Hello world."}                    # was: drawn in the edit view, the raw brace visible


@needs_node
def test_a_file_opened_as_code_because_it_was_unsure_is_free_again_once_the_text_is_fixed(page):
    assert page["opened"] == {"pressed": "true", "disabled": True, "why": "这个文件里有没配对的括号，只能显示代码。", "word": False}
    assert page["fixed"] == {"pressed": "true", "disabled": False, "why": "", "word": False}   # was: disabled until reopened
    assert page["clicked"] == {"pressed": "false", "disabled": False, "why": "", "word": True}


# -- the tools of 格式 write only what compiles ---------------------------------------------------------------------------

_TOOLS = r"""
// the tools are pure: given a state (here without the edit view, whose filter would move a cursor out of markup), they
// answer the change
const R = (d, p, f, q) => { const s1 = CM.EditorState.create({doc: d, selection: {anchor: p, head: q == null ? p : q}}); const r = f(s1);
  if (!r) return null; if (r.refused) return {refused: r.refused};
  const s2 = s1.update({changes: r.changes, selection: r.selection, userEvent: 'input.format'}).state;
  return {text: body(s2.doc.toString()), pre: s2.doc.toString().split('\\begin{document}')[0], sel: s2.doc.sliceString(s2.selection.main.from, s2.selection.main.to), note: r.note || null}; };
const S = (name, o) => (s) => P.styleSpec(s, name, o);
let d;
out.note = {};
d = E('\\section{Intro words}'); out.note.heading = R(d, at(d, 'Intro') + 5, P.footnoteSpec);
d = E('\\begin{figure}\n\\caption{Cap words}\n\\end{figure}'); out.note.caption = R(d, at(d, 'Cap') + 3, P.footnoteSpec);
d = E('A \\underline{lined words} b'); out.note.underline = R(d, at(d, 'lined') + 5, P.footnoteSpec);
d = E('A \\textbf{bold words} b'); out.note.bold = R(d, at(d, 'bold') + 4, P.footnoteSpec);
out.url = {};
d = E('\\section{\\textbf{See} here}'); out.url.heading = R(d, at(d, 'here'), (s) => P.urlSpec(s, 'https://a.b/x', {url: true}));
d = E('\\begin{figure}\n\\caption{See here}\n\\end{figure}'); out.url.caption = R(d, at(d, 'here'), (s) => P.urlSpec(s, 'https://a.b/x', {url: true}));
out.heading = ['Intro\\footnote{x} words.', 'Intro \\verb|vv| text.', 'See \\url{https://a.b/c} page.', 'See \\href{http://x.org/a#b}{site}.', 'A \\par B.', 'A \\footnotemark{} b', 'Plain \\textbf{words}.']
  .map((b) => { const d2 = E(b); return R(d2, at(d2, b.slice(0, 2)) + 1, (s) => P.headingSpec(s, 'section', false)); });
out.wrap = {};
d = E('word \\verb|v| x'); out.wrap.verb = R(d, at(d, 'word'), S('textbf'), at(d, ' x') + 2);
d = E('Let $y=1$ ok'); out.wrap.display = R(d, at(d, '$y'), (s) => P.mathSpec(s, true), at(d, '1$') + 2);
d = E('Let a $y$ b ok'); out.wrap.inline = R(d, at(d, 'a $'), (s) => P.mathSpec(s, false), at(d, 'b ok') + 1);
d = E('this \\[x\\] word'); out.wrap.underlineDisplay = R(d, at(d, 'this'), S('underline', {}), at(d, 'word') + 4);
d = E('See\n\\begin{itemize}\n\\item a\n\\end{itemize}\nmore'); out.wrap.list = R(d, at(d, 'See'), S('textit'), at(d, 'more') + 4);
d = E('{\\heiti 第一段。\n\n第二段。}', '', 'ctexart'); out.wrap.groupToCommand = R(d, at(d, '第一') + 1, (s) => P.fontSpec(s, 'textsf'));
d = E('Alpha $a\nb$ and more'); out.wrap.listOverMath = R(d, at(d, 'Alpha'), (s) => P.listSpec(s, 'itemize'), at(d, 'and') + 3);
out.float = {};
d = E('\\begin{minipage}{0.5\\linewidth}\nText\n\\end{minipage}'); out.float.minipage = R(d, at(d, 'Text') + 4, (s) => P.tableSpec(s, {rows: 1, cols: 1}));
d = E('\\begin{multicols}{2}\nText\n\\end{multicols}', '\\usepackage{multicol}\n'); out.float.multicols = R(d, at(d, 'Text') + 4, (s) => P.figureSpec(s, {path: 'a.png', caption: 'c', graphicx: true}));
out.markup = [['\\begin{center}\nx\n\\end{center}', 'cen', 3], ['\\begin{minipage}{0.5\\linewidth}\nx\n\\end{minipage}', '0.5', 3], ['A \\textbf{word}', '\\textbf', 7],
  ['\\section{Intro}', '\\section', 8], ['a \\\\ b', '\\\\', 1], ['Ten \\% more', '\\%', 1]]
  .map(([b, k, n]) => { const d2 = E(b), p = at(d2, k) + n; return [R(d2, p, S('textbf')), R(d2, p, (s) => P.tableSpec(s, {rows: 1, cols: 1}))]; });
out.toggle = {};
d = E('A \\textbf{\\textit{bothword}} z'); out.toggle.nested = R(d, at(d, 'bothword'), S('textbf'), at(d, 'bothword') + 8);
d = E('A \\textbf{bothword} z'); out.toggle.start = R(d, at(d, 'bothword'), S('textbf'), at(d, 'bothword') + 4);
out.toggle.middle = R(d, at(d, 'bothword') + 2, S('textbf'), at(d, 'bothword') + 5);
d = E('A \\underline{word} z'); out.toggle.oldUnderline = R(d, at(d, 'word'), S('underline', {}), at(d, 'word') + 4);
d = E('A word z');
out.underline = {main: R(d, at(d, 'word'), S('underline', {engine: 'pdflatex', isMain: true}), at(d, 'word') + 4),
  chapter: R(d, at(d, 'word'), S('underline', {engine: 'pdflatex', isMain: false, main: 'main.tex'}), at(d, 'word') + 4),
  cjk: R(d, at(d, 'word'), S('underline', {engine: 'xelatex', cjk: true, isMain: true}), at(d, 'word') + 4),
  loaded: R(d, at(d, 'word'), S('underline', {engine: 'pdflatex', ulem: true, isMain: true}), at(d, 'word') + 4)};
d = E('this\\footnote{N} word'); out.underline.footnote = R(d, at(d, 'this'), S('underline', {}), at(d, 'word') + 4);
d = E('A first line\nof the paragraph.'); out.size = R(d, at(d, 'A first'), (s) => P.sizeSpec(s, 'Large'), at(d, 'paragraph.') + 10);
out.sizePart = R(d, at(d, 'first'), (s) => P.sizeSpec(s, 'Large'), at(d, 'first') + 5);
d = E('Hello wonderful world, more'); out.midWord = [R(d, at(d, 'wond') + 4, P.pageBreakSpec), R(d, at(d, 'world') + 5, (s) => P.tableSpec(s, {rows: 1, cols: 1}))];
d = E('Text here.'); out.formula = [R(d, at(d, 'here.') + 5, (s) => P.mathSpec(s, false)), R(d, at(d, 'here.') + 5, (s) => P.mathSpec(s, true))];
out.formula = out.formula.map((x) => Object.assign(x, {sure: sure(E(x.text))}));
d = E('\\label {tab:results-2} x'); out.label = [R(d, at(d, ' x') + 1, (s) => P.tableSpec(s, {rows: 1, cols: 1, caption: 'Results 2'})),
  R(E('x'), at(E('x'), 'x') + 1, (s) => P.figureSpec(s, {path: 'p.png', file: 'figs/p.png', caption: 'c', graphicx: true, taken: ['fig:p']}))];
out.label = out.label.map((x) => (x.text.match(/\\label\s*\{[^}]*\}/g) || []));
out.escape = P.escapeTex('a<b>c|d ~ ^');
out.typedEscape = body(T(make(E('Some text.'), at(E('Some text.'), 'text')), at(E('Some text.'), 'text'), '<a|b>').doc.toString());
out.figurePath = [P.figurePath('img/two.png', '', ['img/'], (p) => p === 'two.png'), P.figurePath('img/two.png', '', ['img/'], () => false)];
"""


@pytest.fixture(scope="module")
def tools() -> dict:
    return _edit(_TOOLS)


@needs_node
def test_a_footnote_or_a_web_address_never_goes_where_tex_moves_or_boxes_the_words(tools):
    assert tools["note"]["heading"] == {"refused": "标题里不能加脚注。"}        # was: \section{Intro\footnote{} words}, no PDF
    assert tools["note"]["caption"] == {"refused": "图注里不能加脚注。"}
    assert tools["note"]["underline"] == {"refused": "带下划线的文字里不能加脚注。"}   # was: its words never printed
    assert tools["note"]["bold"]["text"] == "A \\textbf{bold\\footnote{} words} b"   # in bold it compiles: still allowed
    assert tools["url"] == {"heading": {"refused": "标题里不能放网址。"}, "caption": {"refused": "图注里不能放网址。"}}


@needs_node
def test_a_heading_or_a_wrap_is_refused_over_what_a_commands_argument_cannot_hold(tools):
    h = tools["heading"]
    assert h[:6] == [{"refused": "这一段里有脚注或网址，不能整段变成标题。"}, {"refused": "这一段里有原样照排的文字、分段、列表或公式块，不能整段变成标题。"},
                     {"refused": "这一段里有脚注或网址，不能整段变成标题。"}, {"refused": "这一段里有脚注或网址，不能整段变成标题。"},
                     {"refused": "这一段里有原样照排的文字、分段、列表或公式块，不能整段变成标题。"}, {"refused": "这一段里有脚注或网址，不能整段变成标题。"}]
    assert h[6]["text"] == "\\section{Plain \\textbf{words}.}"
    w = tools["wrap"]
    assert w["verb"] == {"refused": "选中的文字里有原样照排的文字、分段、列表或公式块，不能整段加这个格式。"}   # was: \textbf{word \verb|v| x}
    assert w["display"] == w["inline"] == {"refused": "选中的文字里已经有公式或不能放进公式的内容，不能再变成公式。"}   # was: $a $y$ b$
    assert w["underlineDisplay"] == w["list"] == {"refused": "选中的文字里有原样照排的文字、分段、列表或公式块，不能整段加这个格式。"}
    assert w["groupToCommand"] == {"refused": "选中的文字跨了段落，不能整段加这个格式。"}           # was: \textsf{…\n\n…}, not long
    assert w["listOverMath"] == {"refused": "选中的几行有一部分在别的格式里，不能变成列表。"}      # was: \item Alpha $a / \item b$


@needs_node
def test_a_figure_or_table_is_refused_in_a_box_or_columns_and_no_tool_writes_inside_markup(tools):
    f = tools["float"]
    assert f["minipage"] == f["multicols"] == {"refused": "光标在并排的小块或分栏里，这里不能插入图片或表格。"}   # was: Float(s) lost
    assert tools["markup"] == [[{"refused": "光标在 LaTeX 命令里。请把光标移开。"}] * 2] * 6   # was: \begin{cen\textbf{}ter} …


@needs_node
def test_bold_on_nested_or_part_of_bold_text_takes_the_bold_away_like_word(tools):
    t = tools["toggle"]
    assert t["nested"]["text"] == "A \\textit{bothword} z" and t["nested"]["sel"] == "bothword"   # was: \textbf{\textit{\textbf{…}}}
    assert t["start"]["text"] == "A both\\textbf{word} z" and t["start"]["sel"] == "both"
    assert t["middle"]["text"] == "A \\textbf{bo}thw\\textbf{ord} z" and t["middle"]["sel"] == "thw"
    assert t["oldUnderline"]["text"] == "A word z"                            # a hand-written \underline is still taken off


@needs_node
def test_underline_is_uline_or_cjkunderline_whose_lines_break_with_its_package(tools):
    u = tools["underline"]
    assert u["main"]["pre"].endswith("\\usepackage[normalem]{ulem}\n") and u["main"]["text"] == "A \\uline{word} z"   # was: \underline
    assert u["chapter"]["note"] == "主文件 main.tex 的导言区需要加一行：\\usepackage[normalem]{ulem}"
    assert u["cjk"]["pre"].endswith("\\usepackage{xeCJKfntef}\n") and u["cjk"]["text"] == "A \\CJKunderline{word} z"
    assert "ulem" not in u["loaded"]["pre"] and u["loaded"]["sel"] == "word"
    assert u["footnote"] == {"refused": "选中的文字里有脚注、网址或换行，不能加下划线。"}


@needs_node
def test_a_size_over_a_whole_paragraph_ends_it_inside_the_group(tools):
    assert tools["size"]["text"] == "{\\Large A first line\nof the paragraph.\\par}"   # was: no \par, the lines touched
    assert tools["sizePart"]["text"] == "A {\\Large first} line\nof the paragraph."


@needs_node
def test_a_block_never_splits_a_word_and_keeps_the_spacing_as_typed(tools):
    pb, tab = tools["midWord"]
    assert pb["text"] == "Hello wonderful%\n\\clearpage\n world, more"            # was: wond / \clearpage / erful
    assert tab["text"].startswith("Hello wonderful world%\n\\begin{table}") and tab["text"].endswith("\\end{table}%\n, more")


@needs_node
def test_an_empty_formula_from_the_tool_is_one_tex_reads(tools):
    inline, display = tools["formula"]
    assert inline["text"] == "Text here.\\(\\)" and inline["sure"] is None          # was: $$, "Missing $ inserted"
    assert display["text"] == "Text here.\n\\begin{equation}\n\\end{equation}" and display["sure"] is None   # was: a blank line inside


@needs_node
def test_a_new_label_is_unique_in_the_file_however_written_and_in_the_papers_other_files(tools):
    tab, fig = tools["label"]
    assert tab == ["\\label {tab:results-2}", "\\label{tab:results-2-2}"]       # was: tab:results-2 twice
    assert fig == ["\\label{fig:p-2}"]                                           # fig:p is another file's


@needs_node
def test_typed_and_caption_characters_print_as_typed_and_paths_are_found(tools):
    assert tools["escape"] == "a\\textless{}b\\textgreater{}c\\textbar{}d \\textasciitilde{} \\textasciicircum{}"   # was: \~{} \^{} and raw < > |
    assert tools["typedEscape"] == "Some \\textless{}a\\textbar{}b\\textgreater{}text."
    assert tools["figurePath"] == ["img/two.png", "two.png"]                    # was: two.png, shadowed by ./two.png


# -- 版式 ------------------------------------------------------------------------------------------------------------

_LAYOUT = r"""
const L = (d, key, v, extra) => { const st = CM.EditorState.create({doc: d}); const r = P.layoutSpec(st, key, v, Object.assign({local: []}, extra || {}));
  if (!r) return null; if (r.refused) return {refused: r.refused}; return {pre: st.update({changes: r.changes}).state.doc.toString().split('\\begin{document}')[0], note: r.note || null}; };
const W = (d, steps) => { let st = CM.EditorState.create({doc: d}); for (const [k, v] of steps) { const r = P.layoutSpec(st, k, v, {local: []}); st = st.update({changes: r.changes}).state; } return st.doc.toString().split('\\begin{document}')[0]; };
out.off = ['\\urlstyle{same}', '\\nolinkurl{x}', '\\pdfstringdefDisableCommands{}'].map((c) => L(E('x ' + c, '\\usepackage{hyperref}\n'), 'hyperref', 'off'));
out.clash = {doi: L(E('x', '\\usepackage{doi}\n'), 'hyperref', 'on'), tufte: L(E('x', '', 'tufte-handout'), 'margin', {preset: '2'}),
  memoirSkip: L(E('x', '', 'memoir'), 'parskip', 'half'), memoirSpacing: L(E('x', '', 'memoir'), 'spacing', '1.5')};
out.back = {doi: W(E('x', '\\usepackage{doi}\n'), [['hyperref', 'on'], ['hyperref', 'off']]), tufte: W(E('x', '', 'tufte-handout'), [['margin', {preset: '2'}], ['margin', null]])};
out.paper = {margin: L(E('x'), 'margin', {preset: '2'}), hyperref: L(E('x'), 'hyperref', 'on'), named: L(E('x').replace('{article}', '[a4paper]{article}'), 'margin', {preset: '2'}),
  odd: L(E('x').replace('{article}', '[a2paper]{article}'), 'margin', {preset: '2'})};
out.marginStays = L(E('x', '\\usepackage[a4paper,margin=2cm]{geometry}\n'), 'margin', null);
out.marginChoices = P.layoutOf('\\documentclass{article}\n\\usepackage[a4paper,margin=2cm]{geometry}\n', []).settings.margin.stays;
out.parskipNone = L(E('x', '\\usepackage{parskip}\n'), 'parskip', '');
"""


@pytest.fixture(scope="module")
def layout() -> dict:
    return _edit(_LAYOUT)


@needs_node
def test_hyperref_stays_while_url_sty_or_hyperref_commands_are_used(layout):
    assert [x["refused"] for x in layout["off"]] == ["文稿用到了 \\urlstyle，它需要 hyperref，不能关掉超链接。",
                                                     "文稿用到了 \\nolinkurl，它需要 hyperref，不能关掉超链接。",
                                                     "文稿用到了 \\pdfstringdefDisableCommands，它需要 hyperref，不能关掉超链接。"]   # was: undefined control sequences


@needs_node
def test_layout_never_writes_an_option_a_class_or_package_has_loaded_already(layout):
    c = layout["clash"]
    assert c["doi"]["pre"].endswith("\\usepackage{doi}\n\\usepackage{hyperref}\n\\hypersetup{hidelinks}\n")   # was: [hidelinks], Option clash
    assert c["tufte"]["pre"] == "\\documentclass{tufte-handout}\n\\usepackage{geometry}\n\\geometry{margin=2cm}\n"
    why = {"refused": "文档类 memoir 自己设置行距和段间距，请用它自己的命令。"}
    assert c["memoirSkip"] == why and c["memoirSpacing"] == why                  # was: Option clash; \setstretch undefined
    assert layout["back"]["doi"] == "\\documentclass[a4paper]{article}\n\\usepackage{doi}\n"   # both lines go again
    assert layout["back"]["tufte"] == "\\documentclass{tufte-handout}\n"


@needs_node
def test_margins_or_links_keep_the_paper_a4_and_say_so(layout):
    p = layout["paper"]
    assert p["margin"]["pre"].startswith("\\documentclass[a4paper]{article}") and p["margin"]["note"] == "纸张保持 A4：文档类的选项里加上了 a4paper。"
    assert p["hyperref"]["pre"].startswith("\\documentclass[a4paper]{article}")   # was: the PDF became US Letter
    assert p["named"]["note"] is None and p["named"]["pre"].startswith("\\documentclass[a4paper]{article}\n\\usepackage[margin=2cm]")
    assert p["odd"]["note"] == "文档类不认识选项里的纸张 a2paper，加上这项设置后 PDF 会按 a2paper 排。"


@needs_node
def test_default_margins_are_refused_where_geometry_would_keep_its_own(layout):
    assert layout["marginStays"] == {"refused": "geometry 宏包还有别的设置，页边距回不到文档类的默认。请选一个页边距。"}
    assert layout["marginChoices"] is True                                       # 默认 shown disabled with why


@needs_node
def test_no_space_between_paragraphs_keeps_parskips_zero_indent(layout):
    assert layout["parskipNone"]["pre"] == "\\documentclass{article}\n\\setlength{\\parindent}{0pt}\n"   # was: the line gone, indent 15pt


# -- the page around the tools: undo steps, passages, alignment ----------------------------------------------------------

@needs_node
def test_a_tool_is_its_own_undo_step_even_with_typing_right_after_it():
    got = _edit(r"""
const ext = [P.historyOf(CM)];
let st = CM.EditorState.create({doc: E('text here.'), selection: {anchor: 0}, extensions: ext});
const p = at(st.doc.toString(), 'here');
st = st.update({selection: {anchor: p}}).state;
const r = P.styleSpec(st, 'textit');
st = st.update({changes: r.changes, selection: r.selection, userEvent: 'input.format'}).state;
st = st.update({changes: {from: st.selection.main.head, insert: 'new'}, selection: {anchor: st.selection.main.head + 3}, userEvent: 'input.type'}).state;
let x = st; CM.undo({state: x, dispatch: (tr) => { x = tr.state; }});
out.one = body(x.doc.toString());
""")
    assert got["one"] == "text \\textit{}here."                                  # was: one ⌘Z took the typing and the tool together


@needs_node
def test_a_block_written_at_a_passages_end_stays_out_of_the_passage():
    got = _edit(r"""
const PF = P.passageField(CM), d = E('The effect is large.\nMore.'), from = at(d, 'The'), to = at(d, 'large.') + 6;
let st = CM.EditorState.create({doc: d, selection: {anchor: to}, extensions: [PF.field]});
st = st.update({effects: PF.set.of([{key: 'k', links: [{}], from, to, candidates: [], state: 'found'}])}).state;
const r = P.tableSpec(st, {rows: 1, cols: 1});
const tool = st.update({changes: r.changes, selection: r.selection, userEvent: 'input.format'}).state;
const typed = st.update({changes: {from: to, insert: ' Indeed'}, userEvent: 'input.type'}).state;
const words = (s) => { const p = s.field(PF.field).list[0]; return s.doc.sliceString(p.from, p.to); };
out.tool = words(tool); out.typed = words(typed);
""")
    assert got["tool"] == "The effect is large."                                 # was: the table pulled in, 待复核 after the save
    assert got["typed"] == "The effect is large. Indeed"                         # typing at its edge still changes it (22.2)


@needs_node
def test_alignment_or_quote_at_a_paragraphs_end_keeps_the_cursor_in_the_paragraph():
    got = _edit(r"""
const d = E('a\n\nFirst para text.\n\nb'), p = at(d, 'text.') + 5;
out.align = ['center', null].map((k) => { const st = CM.EditorState.create({doc: d, selection: {anchor: p}}); const r = k ? P.alignSpec(st, k) : P.quoteSpec(st);
  const s2 = st.update({changes: r.changes, selection: r.selection}).state; return P.paragraphState(s2, P.readLatex(s2.doc.toString())); });
""")
    assert got["align"] == [{"align": "center", "quote": False}, {"align": "", "quote": True}]   # was: the cursor after \end, 两端对齐


# -- the engine: the pickers' lists say when they are cut and never offer a cloud-only thumbnail ------------------------

def test_a_cloud_only_image_has_no_thumbnail_and_a_cut_list_says_more(tmp_path, monkeypatch):
    from researchos import latex_edit, paths
    from test_v17_format_panel import _project

    root = _project(tmp_path)
    monkeypatch.setattr(paths, "is_dataless", lambda p: str(p).endswith("plot.png"))
    got = latex_edit.insertables(root, "paper/chapters/one.tex", "images")
    assert got["images"][0] == {"path": "figures/plot.png", "thumb": False}   # was: True, a broken picture in the picker
    monkeypatch.setattr(latex_edit, "_FILE_WALK_LIMIT", 2)
    assert latex_edit.insertables(root, "paper/chapters/one.tex", "images")["more"] is True   # was: False, "no images yet"
    assert latex_edit.insertables(root, "paper/chapters/one.tex", "bib")["more"] is True


# -- a real compile of what the fixed tools and the edit view now write --------------------------------------------------

_BUILD = r"""
const doTool = (st, f) => { const r = f(st); if (!r || r.refused) throw new Error('refused ' + JSON.stringify(r)); return st.update({changes: r.changes, selection: r.selection, userEvent: 'input.format'}).state; };
const sel = (st, w, n) => { const d = st.doc.toString(), a = d.indexOf(w); return st.update({selection: {anchor: a + (n == null ? 0 : n), head: n == null ? a + w.length : a + n}}).state; };
const LONG = Array.from({length: 40}, (_, i) => 'word' + i).join(' ');
// pdfLaTeX, no font encoding (OT1): the characters typed and in a caption, a long underline, a block mid-word, empty formulas
let st = make(E('Typed: here.\n\nUnder ' + LONG + ' end.\n\nHello wonderful world, more.\n\n\\begin{itemize}\n  \\item one\n  \\item \n\\end{itemize}\n\n\\section{Introduction} % TODO\nThis paragraph matters.\n\nFormula: x.'), 0);
st = sel(st, 'here', 0); st = T(st, st.selection.main.head, '<a|b> 50% ~^ ');
st = sel(st, 'word0', null); st = st.update({selection: {anchor: st.selection.main.from, head: st.doc.toString().indexOf('word39') + 6}}).state;
st = doTool(st, (s) => P.styleSpec(s, 'underline', {engine: 'pdflatex', isMain: true}));
st = sel(st, 'wonderful', 4); st = doTool(st, (s) => P.tableSpec(s, {rows: 1, cols: 1, caption: 'a<b>c|d ~ ^ cap'}));
{ const d = st.doc.toString(), p = d.indexOf('\\item \n') + 6; st = st.update({selection: {anchor: p}}).state; st = T(st, p, '[1] first'); }
{ const d = st.doc.toString(), p = d.indexOf('This para'); st = D(st.update({selection: {anchor: p}}).state, p - 1, p); }
st = sel(st, 'x.', 0); st = doTool(st, (s) => P.mathSpec(s, false));
st = sel(st, 'x.', 2); st = doTool(st, (s) => P.mathSpec(s, true));
st = doTool(st, (s) => P.layoutSpec(s, 'margin', {preset: '2'}, {local: []}));
out.pdf = st.doc.toString();
// XeLaTeX, ctexart: a CJK character after a command, a long Chinese underline
const ZH = '中文的句子很长很长' .repeat(12);
st = make(E('今天是\\today\n\n下划线：' + ZH + '。', '', 'ctexart'), 0);
{ const p = st.doc.toString().indexOf('\\today') + 6; st = T(st.update({selection: {anchor: p}}).state, p, '你好'); }
st = sel(st, ZH, null); st = doTool(st, (s) => P.styleSpec(s, 'underline', {engine: 'xelatex', cjk: true, isMain: true}));
out.xe = st.doc.toString();
"""


@pytest.fixture(scope="module")
def built() -> dict:
    return _edit(_BUILD)


def _latexmk(tmp_path: Path, name: str, text: str, engine: str) -> tuple[str, str, str]:
    work = tmp_path / name
    work.mkdir(parents=True)
    (work / "main.tex").write_text(text, encoding="utf-8")
    env = dict(os.environ, HOME=str(tmp_path), PATH=str(TEXBIN) + os.pathsep + os.environ.get("PATH", ""))
    subprocess.run([LATEXMK, "-" + engine, "-interaction=nonstopmode", "-halt-on-error", "main.tex"], cwd=work, env=env,
                   capture_output=True, text=True, timeout=300)
    log = (work / "main.log").read_text(errors="replace") if (work / "main.log").exists() else ""
    pdf = work / "main.pdf"
    words = subprocess.run([POPPLER["pdftotext"], "-raw", str(pdf), "-"], capture_output=True, text=True).stdout if pdf.exists() else ""
    info = subprocess.run([POPPLER["pdfinfo"], str(pdf)], capture_output=True, text=True).stdout if pdf.exists() else ""
    return log, words, info


@needs_node
@pytest.mark.skipif(LATEXMK is None or None in POPPLER.values(), reason="latexmk, pdftotext and pdfinfo are needed")
def test_what_the_fixed_tools_write_compiles_and_prints_as_typed(built, tmp_path):
    pdf, xe = built["pdf"], built["xe"]
    assert "\\uline{word0" in pdf and "wonderful%\n\\begin{table}" in pdf and "\\item {}[1] first" in pdf and "\\(\\)" in pdf
    assert "\\section{Introduction} This paragraph" in pdf and "\\documentclass[a4paper]{article}" in pdf
    log, words, info = _latexmk(tmp_path, "pdf", pdf, "pdf")
    assert [ln for ln in log.splitlines() if ln.startswith("!")] == [] and words, log[-2000:]
    assert "Overfull" not in log                                            # the underline breaks as words do
    flat = " ".join(words.split())
    assert "Typed: <a|b> 50%" in flat                                       # was: ¡a—b¿ without T1 (~ and ^ are OT1's own glyphs)
    assert "a<b>c|d" in flat and "wonderful world, more." in flat and "[1] first" in flat
    assert "This paragraph matters." in flat
    assert "595.276 x 841.89 pts (A4)" in info                              # the margins kept the paper A4
    log, words, _info = _latexmk(tmp_path, "xe", xe, "xelatex")
    assert [ln for ln in log.splitlines() if ln.startswith("!")] == [] and "Overfull" not in log, log[-2000:]
    assert "\\today{}你好" in xe and "你好" in words and "\\CJKunderline{中文" in xe


def test_the_main_files_preamble_ends_at_the_first_begin_document_outside_a_comment_and_says_when_cut(tmp_path):
    from researchos import latex_edit
    from test_v17_format_panel import _project

    root = _project(tmp_path)
    (root / "paper" / "main.tex").write_text("\\documentclass{article}\n% \\begin{document} in a comment\n\\usepackage{setspace}\n"
                                             "\\setstretch{2}\n\\begin{document}\n\\input{chapters/one}\n\\end{document}\n", encoding="utf-8")
    got = latex_edit.read_tex(root, "paper/chapters/one.tex")["format"]
    assert got["preamble"].endswith("\\setstretch{2}\n") and "cut" not in got        # was: cut at the comment, 行距 read as 默认
    (root / "paper" / "main.tex").write_text("\\documentclass{article}\n" + "%" * 70000 + "\n\\begin{document}\n\\input{chapters/one}\n\\end{document}\n",
                                             encoding="utf-8")
    assert latex_edit.read_tex(root, "paper/chapters/one.tex")["format"]["cut"] is True


# -- the right column: no code in the edit view, in sight, never taken from the researcher's field ------------------------

_COLUMN = r"""
El.prototype.setAttribute = function (k, v) { (this.attrs || (this.attrs = {}))[k] = String(v); };
const scrolled = [];
El.prototype.scrollIntoView = function () { scrolled.push(this.className); };
FakeView.prototype.dispatch = function (spec) { const tr = this.state.update(spec); this.state = tr.state;
  for (const listen of tr.state.facet(real.EditorView.updateListener)) listen({docChanged: tr.docChanged, selectionSet: !!spec.selection, changes: tr.changes, view: this, state: tr.state}); };
FakeView.prototype.focus = function () { this.focused = (this.focused || 0) + 1; };
global.requestAnimationFrame = (f) => f();
const out = {};
const view = () => viewsMade[viewsMade.length - 1];
const inside = (el, test, acc) => { acc = acc || []; if (el && typeof el === 'object') { if (test(el)) acc.push(el); (el.children || []).forEach((k) => inside(k, test, acc)); } return acc; };
const side = () => byClass('latex-side');
const doc = () => view().state.doc.toString();
const sw = () => inside(side(), (e) => e.classList.contains('latex-switch'))[0];
const tool = (w) => inside(side(), (e) => e.tag === 'button' && e.textContent === w)[0];
const f = (path, text, extra) => Object.assign({path, text, revision: 'r1', line_ending: 'lf', read_only: false, read_only_reason: null, links: [], mains: []}, extra);
const openFile = async (path, text, extra) => { L.open(path); await tick(); gets.shift().res(f(path, text, extra)); await tick(); await tick(); };
const TEXT = '\\documentclass{article}\n\\begin{document}\nText with \\textbf{bold} and $x$ here.\n\\end{document}\n';
await openFile('a.tex', TEXT, {mains: ['a.tex'], format: {main: 'a.tex', engine: 'pdflatex', folder: '', preamble: null, local: []}});
// the link panel quotes the words as the edit view shows them; in 显示代码 as the code
const from = TEXT.indexOf('Text'), to = TEXT.indexOf('here.') + 5;
view().dispatch({selection: {anchor: from, head: to}});
byClass('latex-link-button').fire('click'); await tick();
out.quote = byClass('latex-quote').textContent;
out.linkInSight = scrolled.includes('latex-side-title');
gets.splice(0);
// a redraw of the column (the passages read again) leaves the panel where it is: a field typed in keeps its focus
const panel = side().children[0];
let replaced = 0; const real0 = side().replaceChildren.bind(side()); side().replaceChildren = (...k) => { replaced += 1; real0(...k); };
L.refresh(); await tick();
out.panelKept = side().children[0] === panel && replaced === 0;
// a picker opens in sight; its list says when it is cut
inside(side(), (e) => e.tag === 'button' && e.textContent === '关闭')[0].fire('click');
view().dispatch({selection: {anchor: TEXT.indexOf('here')}});
tool('图片').fire('click'); await tick();
out.pickerInSight = scrolled.includes('latex-picker hidden') || scrolled.includes('latex-picker');
gets.pop().res({images: [{path: 'figs/a.png', thumb: false}], more: true}); await tick(); await tick();
out.cut = inside(byClass('latex-picker'), (e) => e.textContent === '项目里的图片太多，这里没有全部列出；不在这里的图片可以在「显示代码」里写上路径插入。').length;   // 22.12 R: the engine's cut
out.fileClass = inside(byClass('latex-picker'), (e) => e.textContent === 'figs/a.png')[0].className;
tool('图片').fire('click');
// a character pdfLaTeX cannot set: said in ochre
view().dispatch({changes: {from: TEXT.indexOf('here'), insert: 'α'}, userEvent: 'input.type'});
const note = inside(side(), (e) => e.classList.contains('latex-format-note'))[0];
out.unicode = {text: note.textContent, warn: note.classList.contains('latex-export-warn')};
// a tool while an input method composes says why
view().composing = true; tool('加粗').fire('click'); view().composing = false;
out.composing = note.textContent;
// 版式's fields keep the focus (Tab goes on to the next one)
inside(side(), (e) => e.attrs && e.attrs.role === 'tab' && e.textContent === '版式')[0].fire('click');
const f0 = view().focused || 0;
const margin = inside(side(), (e) => e.tag === 'select' && e.children.some((o) => o.value === 'custom' && o.textContent === '自定义') && e.children.some((o) => o.value === '2'))[0];
margin.value = 'custom'; margin.fire('change');
const top = inside(side(), (e) => e.tag === 'input' && e.attrs && e.attrs['aria-label'] === '上')[0];
top.value = '3'; top.fire('change');
out.layoutFocus = {focused: (view().focused || 0) - f0, written: doc().includes('top=3cm')};
inside(side(), (e) => e.attrs && e.attrs.role === 'tab' && e.textContent === '格式')[0].fire('click');
// in 显示代码, ⌘U and ⌘I are nothing, not CodeMirror's own
sw().fire('click');
view().dispatch({selection: {anchor: 30, head: 34}});
const keys = [].concat(...view().state.facet(real.keymap));
out.codeKeys = ['Mod-u', 'Mod-i'].map((k) => { const s0 = view().state; const done = keys.find((b) => b.key === k).run(view()); return done === true && view().state === s0; });
sw().fire('click');
// a chapter whose main file's preamble was cut: 版式 says so
await openFile('ch.tex', 'Text.\n', {mains: ['main.tex'], format: {main: 'main.tex', engine: 'pdflatex', folder: '', preamble: '\\documentclass{article}\n', local: [], cut: true}});
inside(side(), (e) => e.attrs && e.attrs.role === 'tab' && e.textContent === '版式')[0].fire('click');
out.cutHead = inside(byClass('latex-layout-head'), (e) => e.tag === 'p').map((p) => p.textContent);
// a setting of the person's own is 「自己写的设置」, not 版式's own 自定义
await openFile('m.tex', '\\documentclass{article}\n\\newcommand{\\x}{\\setstretch{2}}\n\\begin{document}\nx\n\\end{document}\n', {mains: ['m.tex'], format: {main: 'm.tex', engine: 'pdflatex', folder: '', preamble: null, local: []}});
inside(side(), (e) => e.attrs && e.attrs.role === 'tab' && e.textContent === '版式')[0].fire('click');
out.mine = inside(side(), (e) => e.tag === 'option' && e.value === 'mine').map((o) => o.textContent);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def column() -> dict:
    return _page(_COLUMN)


@needs_node
def test_the_columns_quote_of_a_passage_shows_no_latex_in_the_edit_view(column):
    assert column["quote"] == "Text with bold and 公式 here."                 # was: Text with \textbf{bold} and $x$ here.


@needs_node
def test_the_link_panel_and_a_picker_open_in_sight_and_a_redraw_never_takes_the_panel_out(column):
    assert column["linkInSight"] is True and column["pickerInSight"] is True   # was: below the column's fold at 900 px
    assert column["panelKept"] is True                                         # was: replaced, a picker's field lost its focus


@needs_node
def test_a_cut_list_says_so_and_file_names_are_quiet_words(column):
    assert column["cut"] == 1 and column["fileClass"] == "latex-file-name"


@needs_node
def test_a_character_pdflatex_cannot_set_is_said_in_ochre_and_a_tool_while_composing_says_why(column):
    assert column["unicode"] == {"text": "“α” 用 pdfLaTeX 导出时排不出来：中文请用中文文稿或 XeLaTeX 导出，数学符号请写成公式。", "warn": True}
    assert column["composing"] == "输入法还在输入。先确认文字，再用这个工具。"     # was: nothing


@needs_node
def test_layout_fields_keep_the_focus_and_the_code_view_keys_do_nothing(column):
    assert column["layoutFocus"] == {"focused": 0, "written": True}           # was: the editor took the focus, the next digit typed there
    assert column["codeKeys"] == [True, True]                                 # was: CodeMirror's undoSelection, selectParentSyntax


@needs_node
def test_a_cut_preamble_and_a_persons_own_setting_are_said_plainly(column):
    assert column["cutHead"] == ["主文件的导言区太长，版式读不全。"]
    assert column["mine"] == ["自己写的设置"]


def test_the_column_never_scrolls_sideways_and_a_choice_is_ink_not_ochre():
    css = (WEBAPP / "workspace.css").read_text(encoding="utf-8")
    assert ".latex-side .latex-format-select { width: auto; max-width: 100%; min-width: 0;" in css
    assert ".latex-format-label { font-size: .76rem; color: var(--ink-soft); white-space: nowrap; }" in css
    assert ".latex-grid-cell.is-on { background: var(--paper-alt); border-color: var(--ink); }" in css   # was: ochre (21.8)
    assert ".latex-file-name { min-width: 0; overflow-wrap: anywhere; font-family: var(--font-sans); }" in css


@needs_node
def test_cite_notes_brace_less_input_lengths_and_theorem_labels_are_read_as_the_pdf_prints_them():
    got = _edit(r"""
const d = E('\\section{S}\n\\begin{theorem}\\label{lem:one}\nT.\n\\end{theorem}\nSee \\cite[p.~3]{k}, Lemma~\\ref{lem:one}, \\nameref{lem:one}.\n\\parbox{3cm}{boxed words} \\raisebox{1ex}{up} \\hyperref[sec:x]{this section}\n\\input chap2\n', '\\newtheorem{theorem}{Theorem}\n');
const st = make(d), chips = [];
st.field(F.field).deco.between(0, d.length, (a, b, x) => { if (x.spec.widget && x.spec.widget.text) chips.push(x.spec.widget.text); });
out.chips = chips; out.shown = rawShown(st);
""")
    assert "k，p. 3" in got["chips"]                                           # was: the note dropped
    assert "引用：lem:one" in got["chips"] and "第 1 节" not in got["chips"]   # was: 第 1 节 (the PDF prints 1.1 or the title)
    assert "引入 chap2" in got["chips"]
    assert "boxed words" in got["shown"] and "3cm" not in got["shown"] and "1ex" not in got["shown"] and "sec:x" not in got["shown"]

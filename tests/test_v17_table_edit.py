"""DESIGN.md 22.1 and 22.12 T (task V17; browser acceptance, 2026-11-14, finding F1: "TABLES CANNOT BE FILLED IN THE EDIT
VIEW"; the researcher restricted only formulas to the code -- "公式要在「显示代码」里改" -- so a table is filled in like Word).
Under node on the vendored CodeMirror and KaTeX: a table the grid reader is sure of is drawn as a grid of the text itself,
no markup visible; the cursor never stops inside what is hidden and every stop in a table is in a cell or its caption;
Tab, ⇧Tab and the arrow keys walk the cells, Tab in the last cell adds a row; Return goes to the cell below; every action
of the right column's 「表格」 group writes its exact source as one step of ⌘Z; a deletion across cells clears them and
keeps the grid; typed characters are escaped; a paste from a spreadsheet fills the cells; a seeded fuzz of more than
1,000 edits inside tables keeps every row's & count equal to the columns less one and the source sure (20 of them compiled
by latexmk, pdfLaTeX and XeLaTeX); tables with merged cells and the like stay one unit with the hint; typing in a cell
of a long paper stays under 16 ms a key. Every test names its scenario."""

from __future__ import annotations

import json
import subprocess

import pytest

from researchos import latex_edit
from test_v17_latex_editor import _DRIVER, NODE, WEBAPP
from test_v17_latex_word import _node
from test_v17_no_code_view import LATEXMK, _PAGE as _NC_PAGE, _compile

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

EN = latex_edit.TEMPLATES["en_paper"].replace("<TITLE>", "Tables")
ZH = latex_edit.TEMPLATES["zh_paper"].replace("<TITLE>", "表格")

_BASE = r"""
window.katex = kctx.katex;
const said = [];
const make = (doc, anchor, more) => CM.EditorState.create({doc, selection: {anchor: anchor || 0}, extensions: [P.wordExtensions(CM, {say: (s) => said.push(s)})].concat(more || [])});
const atomsIn = (st, from, to) => { const got = []; st.field(F.field).deco.between(from, to, (a, b, d) => { if (d.spec.atom) got.push({a, b, d}); }); return got.sort((x, y) => x.a - y.a || x.b - y.b); };
const textOf = (e) => typeof e === 'string' ? e : (e.innerHTML ? e.innerHTML.replace(/<[^>]*>/g, '').replace(/&[a-z#0-9]+;/g, ' ') : '') + (e.textContent || '') + (e.children || []).map(textOf).join('');
// what the edit view puts on the page, and the source it shows outside every replace
const view = (st) => { const doc = st.doc.toString(); let text = '', raw = '', at = 0;
  atomsIn(st, 0, doc.length).forEach(({a, b, d}) => { if (b <= at) return; text += doc.slice(at, a); raw += doc.slice(at, a); const w = d.spec.widget; if (w) text += textOf(w.toDOM({dispatch() {}, state: st})); at = b; });
  return {text: text + doc.slice(at), raw: raw + doc.slice(at)}; };
const skip = (set, pos, bias) => { for (;;) { let moved = 0; set.between(pos - 1, pos + 1, (from, to) => { if (pos > from && pos < to) { const side = moved || bias || (pos - from < to - pos ? -1 : 1); pos = side < 0 ? from : to; moved = side; } }); if (!moved) return pos; } };
const stopsOf = (st) => { const v = st.field(F.field), out = [0]; for (let p = 0, n = 0; p < st.doc.length && n < 1e6; n++) { p = skip(v.atoms, p + 1, 1); out.push(p); } return out; };
const hidden = (st, pos) => { let inside = false; st.field(F.field).deco.between(pos, pos, (a, b, d) => { if (d.spec.atom && a < pos && pos < b) inside = true; }); return inside; };
const sure = (text) => P.unsureWhy(text, P.readLatex(text));
const gridsIn = (text) => { const read = P.readLatex(text), out = [];
  for (const e of read.envs) { if (!/^(?:table\*?|tabular\*?|tabularx)$/.test(e.name)) continue; const g = P.gridOf(text, read, e); if (g && !out.includes(g)) out.push(g); } return out; };
// a row's & counted on its own: its line's code, escaped \& and formulas and brace groups taken out
const ampsOf = (text, row) => { let s = text.slice(row.from, row.to); s = s.replace(/(?:^|[^\\])(?:\\\\)*%.*$/, '').replace(/\\[&%$#_{}]/g, '').replace(/\$[^$]*\$/g, '').replace(/\\\(.*?\\\)/g, '');
  for (let k = 0; k < 9; k++) s = s.replace(/\{[^{}]*\}/g, ''); return (s.match(/&/g) || []).length; };
const regions = (g) => (g.caption ? [[g.caption.open, g.caption.close]] : []).concat(...g.rows.map((r) => r.cells));
const bodyOf = (text) => text.slice(text.indexOf('\\begin{tabular}'), text.indexOf('\\end{tabular}') + 13);
const T = (st, at, text, ev) => st.update({changes: {from: at, insert: text}, selection: {anchor: at + text.length}, userEvent: ev || 'input.type'}).state;
const act = (st, name) => { const r = P.gridSpec(st, name); return !r ? {st, none: true} : r.refused != null ? {st, refused: r.refused} : {st: st.update(r).state}; };
const W = (pre, body) => '\\documentclass{article}\n\\usepackage{booktabs,tabularx,amsmath}\n' + (pre || '') + '\\begin{document}\nBefore the table.\n\n' + body + '\n\nAfter the table.\n\\end{document}\n';
// the tables of the corpus: as 表格 writes them, and as researchers write them by hand
const tool = (opts) => { const doc = W('', 'Here.'), st = make(doc, doc.indexOf('Here.') + 5), r = P.tableSpec(st, Object.assign({rows: 3, cols: 3, header: true, caption: 'Main results'}, opts));
  return st.update({changes: r.changes, selection: r.selection, userEvent: 'input.format'}).state.doc.toString(); };
const TABLES = {
  tool: tool({booktabs: true}),
  toolHline: tool({booktabs: false, header: false, caption: ''}),
  oneColumn: tool({booktabs: true, cols: 1, rows: 2}),
  hand: W('', '\\begin{table}[h]\n  \\centering\n  \\small\n  \\caption{Prices, \\textit{2020} and 50\\%}\\label{tab:p}\n  \\begin{tabular}{|l|c|r|}\n    \\hline\n' +
    '    Name & $x_1$ & \\textbf{Total} \\\\ \\hline\n    A~B & \\cite{k} & 3.5\\% \\\\[2pt]\n    % a comment between rows\n    C \\& D & {\\em e} & na\\"ive \\\\\n    \\hline\n  \\end{tabular}\n\\end{table}'),
  below: W('', '\\begin{table}\n\\centering\n\\begin{tabular}{lp{3cm}}\n\\toprule\nKey & A long description \\\\\n\\midrule\nx & y\n\\end{tabular}\n\\caption{Below}\n\\end{table}'),
  bare: W('', '\\begin{center}\n\\begin{tabular}{@{}lc@{}}\na & b \\\\\nc & d \\\\\n\\end{tabular}\n\\end{center}'),
  tabularx: W('', '\\begin{table}\n\\begin{tabularx}{\\linewidth}{lX}\nOne & Two \\\\\n\\end{tabularx}\n\\end{table}'),
};
"""

# -- what the edit view shows of a table, and where the cursor stops --------------------------------------------------

_SHOWN = r"""
out.shown = {};
for (const [name, doc] of Object.entries(TABLES)) {
  const st = make(doc), v = view(st), grids = gridsIn(doc), g = grids[0];
  const kinds = atomsIn(st, 0, doc.length).map((x) => x.d.spec.kind);
  // every caret stop inside the table: never in what is hidden; in a cell, the caption, or one of the table's two edges
  const stops = stopsOf(st).filter((p) => g && p >= g.from && p <= g.to), regs = g ? regions(g) : [];
  const where = stops.map((p) => p === g.from ? 'from' : p === g.to ? 'to' : regs.findIndex((r) => r[0] <= p && p <= r[1]));
  // a letter typed at each stop: in a cell or the caption, the grid as it was, the text sure and the letter shown
  let bad = [];
  for (const p of stops) {
    const after = T(st, p, 'Q'), d = after.doc.toString(), g2 = gridsIn(d)[0];
    const qAt = d.indexOf('Q'), ok = d !== doc && !sure(d) && g2 && g2.rows.length === g.rows.length && g2.rows.every((r) => r.cells.length === g.cols.length) &&
      regions(g2).some((r) => r[0] <= qAt && qAt < r[1]) && view(after).text.includes('Q');
    if (!ok) bad.push(p);
  }
  out.shown[name] = {grids: grids.length, rows: g ? g.rows.length : 0, cols: g ? g.cols.length : 0, text: v.text, raw: v.raw, kinds: [...new Set(kinds)],
    inside: stops.filter((p) => hidden(st, p)).length, where, bad, sure: sure(doc)};
}
// the stops, in order, through the 3-by-3 table 表格 writes: the caption's words, then each cell, row after row
{ const doc = TABLES.tool, st = make(doc), g = gridsIn(doc)[0], regs = regions(g);
  const seq = stopsOf(st).filter((p) => p > g.from && p < g.to).map((p) => regs.findIndex((r) => r[0] <= p && p <= r[1]));
  out.order = seq.filter((k, i) => i === 0 || seq[i - 1] !== k); }
// nothing drawn depends on where the cursor is
{ const doc = TABLES.hand, at = [0, doc.indexOf('Name'), doc.indexOf('3.5'), doc.indexOf('After')];
  out.cursorFree = at.every((p) => JSON.stringify(view(make(doc, p))) === JSON.stringify(view(make(doc, 0)))); }
// copying the table gives its words, a tab between cells
{ const doc = TABLES.hand, st = make(doc), g = gridsIn(doc)[0]; out.copy = P.plainOf(st.field(F.field), g.from, g.to); }
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def shown() -> dict:
    return _node(_BASE + _SHOWN)


MARKUP = set("\\{}$&#^_~%")
NAMES = ("begin", "end{", "tabular", "toprule", "midrule", "bottomrule", "hline", "caption", "label", "centering", "small", "textbf",
         "textit", "cite", "multicolumn", "linewidth")


@needs_node
@pytest.mark.parametrize("name", ["tool", "toolHline", "oneColumn", "hand", "below", "bare", "tabularx"])
def test_a_table_is_a_grid_of_its_own_words_with_no_markup_and_every_stop_in_a_cell_or_its_caption(shown, name):
    s = shown["shown"][name]
    assert s["sure"] is None and s["grids"] == 1 and "grid" in s["kinds"] and "table" not in s["kinds"]
    assert sorted(set(s["raw"]) & MARKUP) == []                       # the source shown holds no markup character
    for word in NAMES:
        assert word not in s["text"], word
    assert s["inside"] == 0                                           # the cursor never stops in what is hidden
    assert all(w == "from" or w == "to" or (isinstance(w, int) and w >= 0) for w in s["where"]), s["where"]
    assert s["bad"] == []                                             # a letter typed at any stop goes in a cell or the caption


@needs_node
def test_the_tables_words_are_shown_as_the_pdf_prints_them(shown):
    hand = shown["shown"]["hand"]
    assert (hand["rows"], hand["cols"]) == (3, 3)
    for words in ("Table 1: ", "Prices, ", "2020", "50%", "Name", "Total", "A\u00a0B", "k", "3.5%", "C & D", "e", "naïve"):
        assert words in hand["text"], words
    assert "a comment" not in hand["text"]                            # a comment between rows is hidden
    assert (shown["shown"]["tool"]["rows"], shown["shown"]["tool"]["cols"]) == (3, 3)
    assert "Main results" in shown["shown"]["tool"]["text"] and "Below" in shown["shown"]["below"]["text"]


@needs_node
def test_the_arrow_keys_walk_the_caption_then_each_cell_row_after_row_and_nothing_depends_on_the_cursor(shown):
    assert shown["order"] == list(range(10))                          # the caption, then the 9 cells in reading order
    assert shown["cursorFree"] is True


@needs_node
def test_copying_a_table_gives_its_words_with_a_tab_between_cells(shown):
    c = shown["copy"]
    assert "\\" not in c and "{" not in c
    assert "Name \t" in c and "\t \\" not in c and c.count("\t") == 6


# -- Tab, Return, the right column's actions, deleting, typing, pasting ------------------------------------------------

_EDIT = r"""
const SMALL = W('', '\\begin{table}[htbp]\n\\centering\n\\caption{T}\n\\label{tab:t}\n\\begin{tabular}{ll}\n\\toprule\nA & B \\\\\n\\midrule\n1 & 2 \\\\\n\\bottomrule\n\\end{tabular}\n\\end{table}');
const cellPos = (doc, r, c) => { const g = gridsIn(doc)[0]; return g.rows[r].cells[c]; };
const cellText = (st, r, c) => { const d = st.doc.toString(), x = cellPos(d, r, c); return d.slice(x[0], x[1]); };
const whereIs = (st) => { const d = st.doc.toString(), x = P.cellAt(d, P.readLatex(d), st.selection.main.head); return x ? [x.r, x.c, st.selection.main.from !== st.selection.main.to] : null; };
// Tab and ⇧Tab: the next and the previous cell (its words selected, as Word); Tab in the last cell adds a row
{ let st = make(SMALL, cellPos(SMALL, 0, 0)[0]); const walk = [whereIs(st)];
  for (let k = 0; k < 4; k++) { st = act(st, 'next').st; walk.push(whereIs(st)); }
  out.tab = {walk, body: bodyOf(st.doc.toString())};
  const back = [];
  for (let k = 0; k < 5; k++) { st = act(st, 'prev').st; back.push(whereIs(st)); }
  out.shiftTab = back;
  // the keys are the edit view's own
  out.keys = st.facet(CM.keymap).flat().map((b) => b.key).filter((k) => k === 'Tab' || k === 'Shift-Tab');
  out.noGrid = P.gridSpec(make(SMALL, SMALL.indexOf('Before') + 2), 'next'); }
// Return goes to the cell below (in the last row it stays); ⇧Return is refused in a cell; Return at the table's very
// start makes an empty paragraph above it
{ const st = make(SMALL, cellPos(SMALL, 0, 1)[0] + 1), r = P.enterSpec(CM, st, st.field(F.field), false);
  const s2 = st.update(r).state, r2 = P.enterSpec(CM, s2, s2.field(F.field), false), s3 = s2.update(r2).state;
  const sh = P.enterSpec(CM, st, st.field(F.field), true);
  const g = gridsIn(SMALL)[0], top = make(SMALL, g.from), rt = P.enterSpec(CM, top, top.field(F.field), false), s4 = top.update(rt).state;
  out.ret = {down: whereIs(s2), stays: whereIs(s3) && s3.doc.toString() === SMALL, shift: sh.refused, same: s2.doc.toString() === SMALL,
    above: s4.doc.toString().slice(s4.doc.toString().indexOf('Before'), s4.doc.toString().indexOf('\\begin{table}')), aboveAt: s4.selection.main.head - s4.doc.toString().indexOf('Before')}; }
// each action of 「表格」: its exact source, the cursor in its cell, one step of ⌘Z back to the file byte for byte
const undoView = (st) => { const v = {state: st}; v.dispatch = (tr) => { v.state = tr.state; }; return v; };
const acts = (doc, at, names) => { const res = {};
  for (const name of names) {
    const st = make(doc, at, [P.historyOf(CM)]), r = P.gridSpec(st, name);
    if (!r || r.refused != null) { res[name] = {refused: r ? r.refused : null}; continue; }
    const s2 = st.update(Object.assign({}, r, {userEvent: 'input.format.table'})).state, uv = undoView(s2);
    const once = CM.undo(uv), back = uv.state.doc.toString() === doc, more = CM.undo(uv);
    res[name] = {body: bodyOf(s2.doc.toString()), at: whereIs(s2), undone: once && back && !more, sure: sure(s2.doc.toString())};
  }
  return res; };
const ALL = ['rowAbove', 'rowBelow', 'colLeft', 'colRight', 'rowDelete', 'colDelete', 'header'];
out.acts = acts(SMALL, cellPos(SMALL, 1, 0)[0], ALL);
{ const off = make(SMALL, cellPos(SMALL, 0, 0)[0]), r = P.gridSpec(off, 'header'), s2 = off.update(r).state, r2 = P.gridSpec(s2, 'header');
  out.headerAgain = {off: bodyOf(s2.doc.toString()), on: bodyOf(s2.update(r2).state.doc.toString()), pressed: [P.gridHeader(SMALL, gridsIn(SMALL)[0]), P.gridHeader(s2.doc.toString(), gridsIn(s2.doc.toString())[0])]}; }
const RULED = W('', '\\begin{tabular}{|l|c|}\n\\hline\na & b \\\\ \\hline\nc & d \\\\\n\\hline\n\\end{tabular}');
out.ruled = acts(RULED, RULED.indexOf('d \\\\'), ['colRight', 'colLeft', 'colDelete', 'header']);
const STAR = W('', '\\begin{tabular}{*{2}{c}}\na & b \\\\\n\\end{tabular}');
out.star = acts(STAR, STAR.indexOf('a &'), ['colRight', 'colDelete', 'rowBelow']);
const ONE = W('', '\\begin{tabular}{l}\na \\\\\n\\end{tabular}');
out.one = acts(ONE, ONE.indexOf('a \\\\'), ['rowDelete', 'colDelete', 'header', 'colRight']);
const NOSLASH = W('', '\\begin{tabular}{ll}\na & b \\\\\nc & d % last row, no \\\\\n\\end{tabular}');
out.noslash = acts(NOSLASH, NOSLASH.indexOf('d %'), ['rowBelow', 'next']);
// a deletion across cells clears their words and keeps the grid; Backspace at a cell's start and Delete at its end do
// nothing; a deletion over the whole table takes it whole
{ const d = SMALL, a = d.indexOf('A &') + 1, b = d.indexOf('2 \\\\') + 1;
  const cleared = make(d).update({changes: {from: d.indexOf('A &'), to: b}, userEvent: 'delete.selection'}).state.doc.toString();
  const typed = make(d).update({changes: {from: a - 1, to: b, insert: 'Z'}, userEvent: 'input.type'}).state.doc.toString();
  const c1 = cellPos(d, 0, 1), st = make(d, c1[0]), bk = P.backSpec(CM, st, st.field(F.field), -1), st2 = make(d, c1[1]), dl = P.backSpec(CM, st2, st2.field(F.field), 1);
  const cmBack = make(d).update({changes: {from: c1[0] - 1, to: c1[0]}, userEvent: 'delete.backward'}).state.doc.toString() === d;
  const g = gridsIn(d)[0], whole = make(d).update({changes: {from: g.from, to: g.to}, userEvent: 'delete.selection'}).state.doc.toString();
  const top = make(d, g.from), bt = P.backSpec(CM, top, top.field(F.field), -1);
  const after = d.indexOf('After'), endJoin = make(d, after - 1), ej = P.backSpec(CM, endJoin, endJoin.field(F.field), -1);
  out.del = {cleared: bodyOf(cleared), typed: bodyOf(typed), back: bk && bk.refused, del: dl && dl.refused, cmBack, whole: whole.slice(whole.indexOf('Before'), whole.indexOf('\\end{document}')),
    tableStart: bt && bt.refused, sure: [cleared, typed].map(sure)}; }
// typing each special character into an empty cell writes its escape (the words set apart from each &) and shows it
{ const doc = TABLES.tool, g = gridsIn(doc)[0], c = g.rows[1].cells[1];
  out.typed = {};
  for (const ch of ['%', '$', '&', '#', '_', '{', '}', '~', '^', '\\', 'x']) {
    const st = T(make(doc), P.cellAt(doc, P.readLatex(doc), c[0]) ? c[0] + 1 : c[0], ch), d = st.doc.toString(), line = d.split('\n')[st.doc.lineAt(c[0]).number - 1];
    out.typed[ch] = {line, shows: view(st).text.includes(ch === '~' ? '~' : ch), sure: sure(d)};
  }
  // the first cell of a new table, typed in at once after 表格 (the cursor is there)
  const tableDoc = W('', 'Here.'), s0 = make(tableDoc, tableDoc.indexOf('Here.') + 5), r = P.tableSpec(s0, {rows: 2, cols: 3, header: true, caption: 'C', booktabs: true});
  let s1 = s0.update({changes: r.changes, selection: r.selection, userEvent: 'input.format'}).state;
  s1 = s1.update({selection: s1.selection}).state;   // as applyFormat does: a cursor in what is hidden moves to its edge
  const at0 = whereIs(s1); s1 = T(s1, s1.selection.main.head, 'Var'); s1 = act(s1, 'next').st; s1 = T(s1, s1.selection.main.head, '50% & more');
  out.newTable = {at: at0, body: bodyOf(s1.doc.toString())}; }
// a paste from a spreadsheet fills the cells from the cursor's, adding rows; plain text is pasted as typed
{ const d = SMALL, at = cellPos(d, 0, 1)[0] + 1;
  out.paste = {grid: bodyOf(make(d).update({changes: {from: at, insert: 'x\ty\tz\nu & v\nw\n'}, userEvent: 'input.paste'}).state.doc.toString()),
    plain: bodyOf(make(d).update({changes: {from: at, insert: '5% more'}, userEvent: 'input.paste'}).state.doc.toString())}; }
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def edited() -> dict:
    return _node(_BASE + _EDIT)


@needs_node
def test_tab_and_shift_tab_walk_the_cells_selecting_their_words_and_tab_in_the_last_cell_adds_a_row(edited):
    assert edited["tab"]["walk"] == [[0, 0, False], [0, 1, True], [1, 0, True], [1, 1, True], [2, 0, False]]
    assert edited["tab"]["body"] == "\\begin{tabular}{ll}\n\\toprule\nA & B \\\\\n\\midrule\n1 & 2 \\\\\n& \\\\\n\\bottomrule\n\\end{tabular}"
    assert edited["shiftTab"] == [[1, 1, True], [1, 0, True], [0, 1, True], [0, 0, True], [0, 0, True]]   # the first cell: stays
    assert edited["keys"] == ["Tab", "Shift-Tab"] and edited["noGrid"] is None               # elsewhere Tab is the page's


@needs_node
def test_return_in_a_cell_goes_to_the_cell_below_and_shift_return_is_refused_there(edited):
    r = edited["ret"]
    assert r["down"] == [1, 1, False] and r["same"] is True and r["stays"]   # nothing written
    assert r["shift"] == "表格的格子里不能换行。"
    assert r["above"] == "Before the table.\n\n\n" and r["aboveAt"] == len("Before the table.\n\n")   # Return before the table


@needs_node
def test_each_action_of_the_table_group_writes_its_exact_source_as_one_undo_step(edited):
    a = edited["acts"]
    head, tail = "\\begin{tabular}{ll}\n\\toprule\n", "\\bottomrule\n\\end{tabular}"
    assert a["rowAbove"]["body"] == head + "A & B \\\\\n\\midrule\n& \\\\\n1 & 2 \\\\\n" + tail and a["rowAbove"]["at"] == [1, 0, False]
    assert a["rowBelow"]["body"] == head + "A & B \\\\\n\\midrule\n1 & 2 \\\\\n& \\\\\n" + tail and a["rowBelow"]["at"] == [2, 0, False]
    assert a["colLeft"]["body"] == "\\begin{tabular}{lll}\n\\toprule\n& A & B \\\\\n\\midrule\n& 1 & 2 \\\\\n" + tail and a["colLeft"]["at"] == [1, 0, False]
    assert a["colRight"]["body"] == "\\begin{tabular}{lll}\n\\toprule\nA & & B \\\\\n\\midrule\n1 & & 2 \\\\\n" + tail and a["colRight"]["at"] == [1, 1, False]
    assert a["rowDelete"]["body"] == head + "A & B \\\\\n\\midrule\n" + tail and a["rowDelete"]["at"] == [0, 0, False]
    assert a["colDelete"]["body"] == "\\begin{tabular}{l}\n\\toprule\n B \\\\\n\\midrule\n 2 \\\\\n" + tail and a["colDelete"]["at"] == [1, 0, False]
    assert a["header"]["body"] == head + "A & B \\\\\n1 & 2 \\\\\n" + tail
    for name, x in a.items():
        assert x["undone"] is True and x["sure"] is None, name        # one step of ⌘Z, byte for byte
    h = edited["headerAgain"]
    assert h["pressed"] == [True, False] and h["on"] == "\\begin{tabular}{ll}\n\\toprule\nA & B \\\\\n\\midrule\n1 & 2 \\\\\n" + tail


@needs_node
def test_column_actions_keep_the_spec_letters_and_rules_and_say_why_where_they_cannot(edited):
    r = edited["ruled"]
    assert r["colRight"]["body"] == "\\begin{tabular}{|l|c|l|}\n\\hline\na & b & \\\\ \\hline\nc & d & \\\\\n\\hline\n\\end{tabular}"
    assert r["colLeft"]["body"] == "\\begin{tabular}{|l|l|c|}\n\\hline\na & & b \\\\ \\hline\nc & & d \\\\\n\\hline\n\\end{tabular}"
    assert r["colDelete"]["body"] == "\\begin{tabular}{|l|}\n\\hline\na \\\\ \\hline\nc \\\\\n\\hline\n\\end{tabular}"
    assert r["header"]["body"] == "\\begin{tabular}{|l|c|}\n\\hline\na & b \\\\\nc & d \\\\\n\\hline\n\\end{tabular}"   # the \\hline under the first row
    s = edited["star"]
    assert s["colRight"] == {"refused": "这个表格的列格式比较特别，加减列要在「显示代码」里改。"} and s["colDelete"] == s["colRight"]
    assert s["rowBelow"]["body"] == "\\begin{tabular}{*{2}{c}}\na & b \\\\\n& \\\\\n\\end{tabular}"
    o = edited["one"]
    assert o["rowDelete"] == {"refused": "表格至少要有一行。"} and o["colDelete"] == {"refused": "表格至少要有一列。"}
    assert o["header"] == {"refused": "表格要有两行以上，才能把第一行设成表头。"}
    assert o["colRight"]["body"] == "\\begin{tabular}{ll}\na & \\\\\n\\end{tabular}"
    n = edited["noslash"]                                              # a last row with no \\: it is written first
    assert n["rowBelow"]["body"] == "\\begin{tabular}{ll}\na & b \\\\\nc & d  \\\\\n& \\\\% last row, no \\\\\n\\end{tabular}"
    assert n["next"]["body"] == n["rowBelow"]["body"]


@needs_node
def test_deleting_across_cells_clears_them_and_keeps_the_grid_and_backspace_at_a_cells_edge_does_nothing(edited):
    d = edited["del"]
    assert d["cleared"] == "\\begin{tabular}{ll}\n\\toprule\n&\\\\\n\\midrule\n& \\\\\n\\bottomrule\n\\end{tabular}"   # words typed later are set apart again
    assert d["typed"] == "\\begin{tabular}{ll}\n\\toprule\nZ&\\\\\n\\midrule\n& \\\\\n\\bottomrule\n\\end{tabular}"
    assert d["sure"] == [None, None]
    assert d["back"] == "" and d["del"] == "" and d["cmBack"] is True and d["tableStart"] == ""
    assert d["whole"] == "Before the table.\n\n\n\nAfter the table.\n"     # the whole table selected: it goes whole


@needs_node
def test_characters_typed_in_a_cell_are_escaped_set_apart_from_each_and_and_shown(edited):
    t = edited["typed"]
    want = {"%": "\\%", "$": "\\$", "&": "\\&", "#": "\\#", "_": "\\_", "{": "\\{", "}": "\\}", "~": "\\textasciitilde{}",
            "^": "\\textasciicircum{}", "\\": "\\textbackslash{}", "x": "x"}
    for ch, src in want.items():
        assert t[ch]["line"] == "& " + src + " & \\\\", ch
        assert t[ch]["shows"] and t[ch]["sure"] is None, ch
    n = edited["newTable"]
    assert n["at"] == [0, 0, False]                                    # 表格 leaves the cursor in the first cell
    assert "\nVar & 50\\% \\& more & \\\\\n\\midrule\n& & \\\\\n" in n["body"]


@needs_node
def test_a_paste_from_a_spreadsheet_fills_the_cells_and_plain_text_is_pasted_as_typed(edited):
    p = edited["paste"]
    assert p["grid"] == "\\begin{tabular}{ll}\n\\toprule\nA & x y z \\\\\n\\midrule\n1 & u \\& v \\\\\n& w \\\\\n\\bottomrule\n\\end{tabular}"
    assert p["plain"] == "\\begin{tabular}{ll}\n\\toprule\nA & 5\\% moreB \\\\\n\\midrule\n1 & 2 \\\\\n\\bottomrule\n\\end{tabular}"


# -- tables that stay one unit ----------------------------------------------------------------------------------------

_UNITS = r"""
const cases = {
  multicolumn: '\\begin{tabular}{ll}\n\\multicolumn{2}{c}{ab} \\\\\na & b \\\\\n\\end{tabular}',
  multirow: '\\begin{tabular}{ll}\n\\multirow{2}{*}{a} & b \\\\\n & c \\\\\n\\end{tabular}',
  cmidrule: '\\begin{tabular}{ll}\na & b \\\\\n\\cmidrule(lr){1-2}\nc & d \\\\\n\\end{tabular}',
  nested: '\\begin{tabular}{ll}\na & \\begin{tabular}{l}x\\end{tabular} \\\\\n\\end{tabular}',
  fewerCells: '\\begin{tabular}{lll}\na & b \\\\\n\\end{tabular}',
  blankLine: '\\begin{tabular}{ll}\na & b \\\\\n\nc & d \\\\\n\\end{tabular}',
  twoRowsALine: '\\begin{tabular}{ll}\na & b \\\\ c & d \\\\\n\\end{tabular}',
  rowOverTwoLines: '\\begin{tabular}{ll}\na &\nb \\\\\n\\end{tabular}',
  sameLine: '\\begin{tabular}{ll}a & b\\end{tabular}',
  oddSpec: '\\begin{tabular}{lQ}\na & b \\\\\n\\end{tabular}',
  longtable: '\\begin{longtable}{ll}\na & b \\\\\n\\end{longtable}',
  notAlone: 'Text \\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}',
  resizebox: '\\begin{table}\n\\resizebox{\\linewidth}{!}{\\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}}\n\\end{table}',
};
out.units = {};
for (const [name, body] of Object.entries(cases)) {
  const doc = W('', body), st = make(doc), kinds = atomsIn(st, 0, doc.length).map((x) => x.d.spec.kind);
  // a letter typed where the cell's word is goes beside the table, never into it
  const typed = T(st, doc.indexOf('b'), 'Q').doc.toString();
  out.units[name] = {grid: kinds.includes('grid'), unit: kinds.includes('table') || kinds.includes('unit'), gridOf: gridsIn(doc).length, typedOutside: typed.includes(body)};
}
console.log(JSON.stringify(out));
"""


@needs_node
@pytest.mark.parametrize("case", ["multicolumn", "multirow", "cmidrule", "nested", "fewerCells", "blankLine", "twoRowsALine", "rowOverTwoLines",
                                  "sameLine", "oddSpec", "longtable", "notAlone", "resizebox"])
def test_a_table_the_grid_reader_is_unsure_of_stays_one_unit_changed_in_show_code(case):
    u = _units()[case]
    assert u == {"grid": False, "unit": True, "gridOf": 0, "typedOutside": True}, u


_UNITS_CACHE: dict = {}


def _units() -> dict:
    if not _UNITS_CACHE:
        _UNITS_CACHE.update(_node(_BASE + _UNITS)["units"])
    return _UNITS_CACHE


# -- a seeded fuzz of edits inside tables -----------------------------------------------------------------------------

_FUZZ = r"""
let seed = 20261114;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
const PAPERS = {
  en: __EN__.replace('Start writing here.', 'Start writing here.\n\n' + bodyOf(TABLES.tool).replace(/^/, '\\begin{table}[htbp]\n\\centering\n\\caption{Results}\n') + '\n\\end{table}\n\n' +
    '\\begin{table}[h]\n\\centering\n\\caption{Prices, 50\\%}\n\\begin{tabular}{|l|c|r|}\n\\hline\nName & $x_1$ & \\textbf{Total} \\\\ \\hline\nA~B & 2 & 3.5\\% \\\\\n\\hline\n\\end{tabular}\n\\end{table}\n'),
  zh: __ZH__.replace('在这里开始写正文。', '在这里开始写正文。\n\n\\begin{table}[htbp]\n\\centering\n\\caption{主要结果}\n\\begin{tabular}{lcr}\n\\hline\n变量 & 均值 & 标准差 \\\\\n\\hline\n汇率 & 6.5 & 0.2 \\\\\n\\hline\n\\end{tabular}\n\\end{table}\n'),
};
const TEXTS = ['x', 'Var', '50%', 'a & b', '$3_000', '{approx}', '~^\\', '#1', ' ', 'é', '中文', 'p\tq\nr\ts', '\n', 'line\nbreak'];
const ACTS = ['rowAbove', 'rowBelow', 'colLeft', 'colRight', 'rowDelete', 'colDelete', 'header', 'next', 'prev'];
out.fuzz = {edits: 0, changed: 0, refused: 0, unsure: 0, notGrid: 0, amps: 0, markup: 0, outside: 0, examples: []};
out.samples = {en: [], zh: []};
for (const [name, paper] of Object.entries(PAPERS)) {
  for (let chain = 0; chain < 6; chain++) {   // 90 edits a chain: ⌘Z keeps a hundred steps
    let st = make(paper, 0, [P.historyOf(CM)]);
    const n0 = gridsIn(paper).length;
    for (let k = 0; k < 90; k++) {
      const doc = st.doc.toString(), grids = gridsIn(doc), g = grids[rnd(grids.length)];
      if (!g) break;   // (counted below: a grid lost)
      const stops = stopsOf(st).filter((p) => p >= g.from && p <= g.to), p = stops[rnd(stops.length)], q = stops[rnd(stops.length)];
      const kind = rnd(10);
      let text = TEXTS[rnd(TEXTS.length)];
      if (name === 'en' && text === '中文') text = 'words';
      const lo = Math.min(p, q), hi = Math.max(p, q);
      let next = st;
      if (kind <= 2) next = st.update({changes: {from: p, insert: text}, selection: {anchor: p + text.length}, userEvent: 'input.type'}).state;
      else if (kind === 3) next = st.update({changes: {from: p, insert: text}, userEvent: 'input.paste'}).state;
      else if (kind === 4 && hi > lo) next = st.update({changes: {from: lo, to: hi}, userEvent: rnd(2) ? 'delete.selection' : 'delete.cut'}).state;
      else if (kind === 5 && hi > lo) next = st.update({changes: {from: lo, to: hi, insert: text}, userEvent: 'input.type'}).state;
      else if (kind === 6) {   // Backspace or Delete as the keys do: the edit view's own step, else CodeMirror's (an atom whole)
        const dir = rnd(2) ? -1 : 1, s0 = st.update({selection: {anchor: p}}).state, r = P.backSpec(CM, s0, s0.field(F.field), dir);
        if (r) next = r.changes ? s0.update(r).state : s0;
        else { let a = dir < 0 ? p - 1 : p, b = dir < 0 ? p : p + 1; s0.field(F.field).atoms.between(a, b, (x, y, v) => { if (v.spec.atom && x < b && y > a) { a = Math.min(a, x); b = Math.max(b, y); } });
          if (a >= 0 && b <= doc.length) next = s0.update({changes: {from: a, to: b}, userEvent: dir < 0 ? 'delete.backward' : 'delete.forward'}).state; }
      } else if (kind === 7) { const s0 = st.update({selection: {anchor: p}}).state, r = P.enterSpec(CM, s0, s0.field(F.field), !!rnd(4)); if (r && r.refused == null && p !== g.from) next = s0.update(r).state; }
      else { const s0 = st.update({selection: {anchor: p}}).state, r = P.gridSpec(s0, ACTS[rnd(ACTS.length)]); if (r && r.refused == null) next = s0.update(Object.assign({}, r, {userEvent: r.changes ? 'input.format.table' : 'select'})).state; }
      out.fuzz.edits += 1;
      const d = next.doc.toString();
      if (k % 45 === 44) out.samples[name].push(d);
      if (d === doc) { out.fuzz.refused += 1; st = next; continue; }
      out.fuzz.changed += 1;
      const bad = (why) => { if (out.fuzz.examples.length < 4) out.fuzz.examples.push([why, name, kind, lo, hi, text, d.slice(Math.max(0, g.from - 20), g.to + 60)]); };
      if (sure(d)) { out.fuzz.unsure += 1; bad('unsure'); st = next; continue; }
      const g2 = gridsIn(d);
      if (g2.length !== n0) { out.fuzz.notGrid += 1; bad('grid'); }
      for (const x of g2) for (const r of x.rows) if (r.seps.length !== x.cols.length - 1 || ampsOf(d, r) !== x.cols.length - 1) { out.fuzz.amps += 1; bad('amps'); }
      if ([...view(next).raw].some((c) => '\\{}$&#^_~%'.includes(c))) { out.fuzz.markup += 1; bad('markup'); }
      // the text outside the tables is as it was
      const outside = (t) => { const gs = gridsIn(t); let s = t.slice(0, gs[0].from), at = gs[0].to; for (const x of gs.slice(1)) { s += '|' + t.slice(at, x.from); at = x.to; } return s + '|' + t.slice(at); };
      if (g2.length === n0 && outside(d) !== outside(doc)) { out.fuzz.outside += 1; bad('outside'); }
      st = next;
    }
    // ⌘Z all the way: the paper as it was, byte for byte
    const uv = {state: st}; uv.dispatch = (tr) => { uv.state = tr.state; };
    for (let n = 0; n < 2000 && CM.undo(uv); n++);
    out.fuzz['undone_' + name + chain] = uv.state.doc.toString() === paper;
  }
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def fuzz() -> dict:
    return _node(_BASE + _FUZZ.replace("__EN__", json.dumps(EN)).replace("__ZH__", json.dumps(ZH)))


@needs_node
def test_a_thousand_seeded_edits_inside_tables_keep_every_rows_cells_and_the_source_sure(fuzz):
    f = fuzz["fuzz"]
    print("table fuzz:", json.dumps({k: v for k, v in f.items() if k != "examples"}))
    assert f["edits"] >= 1000 and f["changed"] >= 600
    assert f["unsure"] == 0 and f["notGrid"] == 0 and f["amps"] == 0 and f["markup"] == 0 and f["outside"] == 0, f["examples"]
    assert all(v for k, v in f.items() if k.startswith("undone_")), f


@needs_node
@pytest.mark.skipif(LATEXMK is None, reason="latexmk is not installed")
def test_twenty_fuzzed_papers_compile_with_latexmk_on_both_engines(fuzz, tmp_path):
    (tmp_path / "home").mkdir()
    jobs = [(f"en-{n:02d}", d, "pdf") for n, d in enumerate(fuzz["samples"]["en"][:10])]
    jobs += [(f"zh-{n:02d}", d, "xelatex") for n, d in enumerate(fuzz["samples"]["zh"][:10])]
    assert len(jobs) == 20
    failed = []
    for name, text, engine in jobs:
        done = _compile(tmp_path, name, text, engine)
        pdf, log = tmp_path / name / "main.pdf", tmp_path / name / "main.log"
        said = log.read_text(errors="replace") if log.exists() else done.stdout
        errors = [ln for ln in said.splitlines() if ln.startswith("!")]
        if errors or not pdf.exists() or done.returncode not in (0, 12):
            failed.append((name, "\n".join(errors) or said[-1500:]))
    print("compiled:", len(jobs), "failed:", [f[0] for f in failed])
    assert failed == [], failed[0][1]


# -- the right column's 「表格」 group, on the stand-in page ------------------------------------------------------------

_PAGE = _NC_PAGE.split("const TEXT =")[0] + r"""
const TEXT = '\\documentclass{article}\n\\usepackage{booktabs}\n\\begin{document}\nSome text.\n\\begin{table}\n\\centering\n\\caption{T}\n\\begin{tabular}{ll}\n\\toprule\nA & B \\\\\n\\midrule\n1 & 2 \\\\\n\\bottomrule\n\\end{tabular}\n\\end{table}\nAfter.\n\\end{document}\n';
await openFile('a.tex', TEXT, {mains: ['a.tex'], format: {main: 'a.tex', engine: 'pdflatex', folder: '', preamble: null, local: []}});
const box = () => inside(side(), (e) => e.classList.contains('latex-grid-tools'))[0];
const btn = (w) => inside(box(), (e) => e.tag === 'button' && e.textContent === w)[0];
view().dispatch({selection: {anchor: TEXT.indexOf('Some') + 2}});
out.outside = {hidden: hiddenEl(box())};
view().dispatch({selection: {anchor: TEXT.indexOf('1 &')}});
out.inCell = {hidden: hiddenEl(box()), words: shownWords(box()), header: btn('第一行是表头').attrs['aria-pressed'], hint: shownWords(context())};
const u0 = real.undoDepth(view().state);
btn('在下方插入行').fire('click');
out.below = {body: doc().slice(doc().indexOf('\\toprule'), doc().indexOf('\\bottomrule')), steps: real.undoDepth(view().state) - u0, focused: view().focused || 0};
btn('第一行是表头').fire('click');
out.header = {pressed: btn('第一行是表头').attrs['aria-pressed'], body: doc().slice(doc().indexOf('\\toprule'), doc().indexOf('\\bottomrule')), steps: real.undoDepth(view().state) - u0};
// 显示代码: the group goes with the edit view
sw().fire('click');
out.code = {hidden: hiddenEl(box())};
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
def test_the_table_group_shows_in_a_cell_with_its_actions_each_one_undo_step_and_goes_in_show_code(page):
    assert page["outside"]["hidden"] is True
    c = page["inCell"]
    assert c["hidden"] is False and c["header"] == "true" and c["hint"] == []   # no 「在显示代码里改」: it is filled in here
    assert c["words"] == ["表格", "在上方插入行", "在下方插入行", "在左侧插入列", "在右侧插入列", "删除行", "删除列", "第一行是表头"]
    assert page["below"]["body"] == "\\toprule\nA & B \\\\\n\\midrule\n1 & 2 \\\\\n& \\\\\n" and page["below"]["steps"] == 1
    assert page["below"]["focused"] >= 1                               # the editor takes the focus back
    assert page["header"]["pressed"] == "false" and page["header"]["steps"] == 2
    assert page["header"]["body"] == "\\toprule\nA & B \\\\\n1 & 2 \\\\\n& \\\\\n"
    assert page["code"]["hidden"] is True


# -- typing in a table's cell stays fast --------------------------------------------------------------------------------

_TIMING = r"""
// 22.1's 3,000-line paper (test_v17_latex_word's), with a table every 50 lines
const lines = ['\\documentclass{article}', '\\usepackage{amsmath}', '\\usepackage{booktabs}', '\\begin{document}'];
for (let i = 0; lines.length < 2996; i++) {
  if (i % 50 === 3) lines.push('\\begin{table}[htbp]', '\\centering', '\\caption{Results ' + i + '}', '\\label{tab:' + i + '}', '\\begin{tabular}{lcr}', '\\toprule',
    'A & B & C \\\\', '\\midrule', '1 & $' + i + '$ & \\textbf{3} \\\\', '2 & 4 & 6 \\\\', '\\bottomrule', '\\end{tabular}', '\\end{table}');
  else if (i % 40 === 0) lines.push('\\section{Section ' + i + '}\\label{sec:' + i + '}');
  else if (i % 10 === 0) lines.push('\\begin{equation}', 'y_{' + i + '} = \\alpha + \\beta x_i + \\varepsilon_i \\label{eq:' + i + '}', '\\end{equation}');
  else if (i % 5 === 0) lines.push('The effect $\\beta_' + i + ' = 0.' + i + '$ is \\textbf{large} and \\emph{robust} \\citep{a' + i + ', b' + i + '} (see \\ref{sec:' + i + '}).');
  else if (i % 7 === 0) lines.push('\\begin{itemize}', '  \\item first point with $x$', '  \\item second \\textit{point}', '\\end{itemize}');
  else lines.push('Running text of the paper, 中文的正文，一段一段地写下去，with numbers 3.14 and 5\\% and a comment % here');
}
lines.push('\\end{document}');
const text = lines.join('\n');
const ms = (t0) => Number(process.hrtime.bigint() - t0) / 1e6, stats = (xs) => { xs.sort((a, b) => a - b); return {median: xs[xs.length >> 1], p95: xs[Math.floor(xs.length * 0.95)], max: xs[xs.length - 1]}; };
const typing = (doc, at, n, key) => {
  let st = CM.EditorState.create({doc, extensions: [P.wordExtensions(CM)], selection: {anchor: at}});
  for (let k = 0; k < 10; k++) st = st.update({changes: {from: st.selection.main.head, insert: 'w'}, selection: {anchor: st.selection.main.head + 1}, userEvent: 'input.type'}).state;
  const keys = [];
  for (let k = 0; k < n; k++) {
    const t0 = process.hrtime.bigint(), at = st.selection.main.head;
    st = st.update({changes: {from: at, insert: key(k)}, selection: {anchor: at + 1}, userEvent: 'input.type'}).state;
    st.field(F.field);
    keys.push(ms(t0));
  }
  return {st, keys: stats(keys)};
};
const cell = text.indexOf('2 & 4', text.length >> 1);
const small = typing(text, cell + 1, 300, (k) => k % 11 === 0 ? '%' : 'x');
const g = P.cellAt(small.st.doc.toString(), P.readLatex(small.st.doc.toString()), small.st.selection.main.head);
const tabs = []; let st = small.st;
for (let k = 0; k < 60; k++) { const t0 = process.hrtime.bigint(), r = P.gridSpec(st, 'next'); st = st.update(r).state; st.field(F.field); tabs.push(ms(t0)); }
const body = lines.slice(4, -1), big = lines.slice(0, 4);
for (let i = 0, bytes = 0; bytes < 1.95e6; i++) { big.push(body[i % body.length]); bytes += Buffer.byteLength(body[i % body.length]) + 1; }
const bigText = big.concat(['\\end{document}']).join('\n');
const large = typing(bigText, bigText.indexOf('2 & 4', bigText.length >> 1) + 1, 100, () => 'x');
const text2 = typing(bigText, bigText.indexOf('Running text', bigText.length >> 1), 100, () => 'x');   // the same paper's running text
console.log(JSON.stringify({lines: text.split('\n').length, tables: P.readLatex(text).envs.filter((e) => e.name === 'table').length, inCell: !!g, key: small.keys, tab: stats(tabs),
  bigBytes: Buffer.byteLength(bigText), bigKey: large.keys, bigText: text2.keys}));
"""


@needs_node
def test_typing_in_a_tables_cell_stays_under_16_ms_a_key_in_a_long_paper():
    got = _node(_TIMING)
    print("in a cell, per key:", json.dumps(got))
    assert got["lines"] >= 2990 and got["tables"] >= 30 and got["inCell"] is True
    assert got["key"]["median"] < 16 and got["key"]["p95"] < 16
    assert got["tab"]["median"] < 32                                   # Tab reads the paper once: still within two frames
    assert got["bigBytes"] > 1.9e6 and got["bigKey"]["median"] < 32
    assert got["bigKey"]["median"] < got["bigText"]["median"] * 1.5    # a cell costs about what running text does

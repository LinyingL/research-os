"""What the lead saw in the real LaTeX editor after V17 B4 (DESIGN.md 22.1, 22.2, 22.6, 22.7), each reproduced first:
a draft's first heading shown as source on opening, the 导言区 header's chevron, a chosen file run into its 移除, a bare
「撤销」 beside 「撤销这次保存」, and the draft itself or its exported PDF offered as its own supporting file."""

from __future__ import annotations

import re

import pytest

from test_v17_latex_editor import NODE, WEBAPP
from test_v17_latex_word import _node

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

# paper/chapters/model.tex of the lead's scratch paper, byte for byte
MODEL = ("\\section{模型}\n设汇率为 $e_t$，则\n\\begin{equation}\n  \\Delta e_t = \\alpha + \\beta\\, s_t + \\varepsilon_t,"
         " \\qquad \\varepsilon_t \\sim N(0,\\sigma^2).\n  \\label{eq:main}\n\\end{equation}\n式 \\eqref{eq:main} 是基准。\n")

_OPENED = r"""
window.katex = kctx.katex;
const MODEL = __MODEL__;
const shown = (st) => { const g = []; st.field(F.field).deco.between(0, st.doc.length, (f, t, v) =>
  g.push(st.doc.sliceString(f, t) + '=' + (v.spec.class || (v.spec.widget ? v.spec.widget.constructor.name : 'hidden')))); return g; };
const fresh = () => CM.EditorState.create({doc: MODEL, extensions: [P.wordExtensions(CM)]});
// as the editor loads a file: the cursor CodeMirror puts at 0, nothing focused yet
let st = fresh().update({effects: F.live.of(false)}).state;
out.opened = shown(st);
out.cursor = st.selection.main.head;
// KaTeX arriving after the file is open draws again, still not as source
let late = CM.EditorState.create({doc: MODEL, extensions: [P.wordExtensions(CM)]});
out.lateMath = shown(late.update({effects: F.live.of(false)}).state.update({effects: F.mathOn.of(true)}).state);
// the first focus: what the focus facet answers turns the selection's rule on, and the cursor at 0 now shows the source
const focusing = st.facet(CM.EditorView.focusChangeEffect).map((f) => f(st, true)).filter(Boolean);
out.focusEffects = focusing.length;
st = st.update({effects: focusing}).state;
out.focused = shown(st);
out.again = st.facet(CM.EditorView.focusChangeEffect).map((f) => f(st, true)).filter(Boolean).length;
st = st.update({selection: {anchor: MODEL.length - 3}}).state;
out.lastLine = shown(st);
console.log(JSON.stringify(out));
"""


@needs_node
def test_model_tex_opens_with_its_section_as_a_heading_until_the_editor_is_focused():
    import json
    out = _node(_OPENED.replace("__MODEL__", json.dumps(MODEL)))
    assert out["cursor"] == 0
    for drawn in (out["opened"], out["lateMath"]):
        assert "模型=latex-h latex-h2" in drawn and "\\section{=hidden" in drawn and "}=hidden" in drawn
        assert "\\section=latex-cmd" not in drawn
    assert out["focusEffects"] == 1 and out["again"] == 0         # once a file, then the field stays live
    assert "\\section=latex-cmd" in out["focused"] and "模型=latex-h latex-h2" not in out["focused"]
    assert "模型=latex-h latex-h2" in out["lastLine"]
    assert any(d.startswith("\\begin{equation}") and d.endswith("=MathWidget") for d in out["lastLine"])


def test_the_editor_loads_a_file_with_the_selection_rule_off_unless_it_has_the_focus():
    js = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    load = js[js.index("  function load(me, file) {"):js.index("  // 显示源码 (22.1)")]
    assert "const focused = !!(me.view && me.view.hasFocus);" in load
    assert "state.update({ effects: wordField(CM).live.of(false) })" in load
    toggle = js[js.index("  function toggleSource(me) {"):js.index("  function mathWhenShown(me) {")]
    assert "if (!on && !me.view.hasFocus) me.view.dispatch({ effects: wordField(me.CM).live.of(false) });" in toggle


# -- words, not glyphs (22.6 "the toolbar is words, with no emoji, arrow or glyph") --------------------------------------

_GLYPHS = ("›", "‹", "▸", "▾", "»", "→", "←", "->", ">")


def _ui_strings(js: str) -> list[str]:
    """Every string latex.js shows: what goes through t(), and the literal text of mk() and mkButton()."""
    said = re.findall(r'\bt\("((?:[^"\\]|\\.)*)"', js)
    said += re.findall(r'\bmk\("[a-z0-9]+",\s*(?:"[^"]*"|null),\s*"((?:[^"\\]|\\.)*)"', js)
    said += re.findall(r'\bmkButton\("((?:[^"\\]|\\.)*)"', js)
    return said


def test_latex_js_shows_no_chevron_or_arrow_as_ui_text_and_draws_none_in_css():
    js = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    said = _ui_strings(js)
    assert len(said) > 100 and "导言区（已折叠，点开）" in said and "收起导言区" in said
    assert [s for s in said if any(g in s for g in _GLYPHS)] == []
    assert "has-chevron" not in js                                  # the CSS chevron of 12.13 is not used here either
    i18n = (WEBAPP / "i18n.js").read_text(encoding="utf-8")
    for zh in ("导言区（已折叠，点开）", "收起导言区"):
        english = re.search(r'"%s": "([^"]*)"' % re.escape(zh), i18n).group(1)
        assert english and not any(g in english for g in _GLYPHS)


def test_an_equations_number_follows_it_and_never_covers_its_last_characters():
    css = (WEBAPP / "workspace.css").read_text(encoding="utf-8")
    assert ".latex-math-display .katex-display > .katex > .katex-html > .katex-tag { position: static;" in css
    # a wide formula with its number in the flow scrolls in its own line, never past the column (review of 8439d3c)
    assert ".latex-math-display { display: block; text-align: center; margin: .2rem 0; overflow-x: auto; overflow-y: hidden; }" in css
    katex = (WEBAPP / "vendor" / "katex.css").read_text(encoding="utf-8")
    assert ".katex-display>.katex>.katex-html>.katex-tag{position:absolute;right:0}" in katex   # what it overrides


def test_a_chosen_file_and_its_remove_link_are_set_apart():
    js = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    assert 'picked = mk("ul", "latex-files latex-picked")' in js
    assert 'li.append(mk("span", "mono", file), mkButton(t("移除"), "ws-text-link"' in js
    css = (WEBAPP / "workspace.css").read_text(encoding="utf-8")
    assert ".latex-picked li, .latex-files li > div { display: flex; flex-wrap: wrap; align-items: baseline; gap: .3rem .9rem; }" in css


# -- a passage is never supported by its own draft or the PDF made from it (22.2) ---------------------------------------


def _paper(tmp_path):
    from researchos import project
    from researchos.records import papers

    root = tmp_path / "p"
    files = {
        "paper/main.tex": "\\documentclass{article}\n\\begin{document}\n\\input{chapters/intro}\n\\include{chapters/model}\n"
                          "\\input{../tables/t1.tex}\n\\end{document}\n",
        "paper/chapters/intro.tex": "\\section{引言}\nText.\n",
        "paper/chapters/model.tex": MODEL,
        "paper/main.pdf": "%PDF-1.4 built", "paper/refs.bib": "@book{a}", "figures/fig1.pdf": "%PDF-1.4 fig",
        "tables/t1.tex": "\\begin{tabular}{l}a\\end{tabular}\n", "other/notes.tex": "\\section{x}\n",
        "out/paper.pdf": "%PDF-1.4 exported",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    project.init_project(root)
    papers.record_export(root, {"main": "paper/main.tex", "pdf": "out/paper.pdf", "pdf_sha256": "0" * 64,
                                "tex_sha256": "1" * 64, "engine": "xelatex"}, via="app")
    return root


def test_the_link_panel_never_offers_the_draft_its_papers_other_drafts_or_its_exported_pdf(tmp_path):
    from researchos import latex_edit

    root = _paper(tmp_path)
    every = [f["path"] for f in latex_edit.supporting_files(root, "")["files"]]
    assert "paper/chapters/model.tex" in every and "out/paper.pdf" in every       # without a draft, as before
    for draft in ("paper/chapters/model.tex", "paper/main.tex"):
        offered = [f["path"] for f in latex_edit.supporting_files(root, "", draft=draft)["files"]]
        assert offered == ["figures/fig1.pdf", "tables/t1.tex", "other/notes.tex", "paper/refs.bib"], draft
    # a draft no main file includes leaves out only itself
    alone = [f["path"] for f in latex_edit.supporting_files(root, "", draft="other/notes.tex")["files"]]
    assert "other/notes.tex" not in alone and "paper/chapters/model.tex" in alone and "paper/main.pdf" in alone


def test_a_draft_gone_or_not_a_tex_leaves_out_only_its_own_path_and_the_list_never_fails(tmp_path):
    from researchos import latex_edit

    root = _paper(tmp_path)
    every = [f["path"] for f in latex_edit.supporting_files(root, "")["files"]]
    # deleted on disk while the editor is open, a path that is not a .tex, one outside the project, not a string
    (root / "paper/chapters/model.tex").unlink()
    gone = [f["path"] for f in latex_edit.supporting_files(root, "", draft="paper/chapters/model.tex")["files"]]
    assert gone == [p for p in every if p != "paper/chapters/model.tex"]
    for draft in ("paper/refs.bib", "nonexist.tex", "../x.tex", 7):
        offered = [f["path"] for f in latex_edit.supporting_files(root, "", draft=draft)["files"]]
        assert offered == [p for p in every if p != draft and p != "paper/chapters/model.tex"], draft


def test_the_files_search_takes_the_open_draft_from_the_editor_and_the_server():
    from pathlib import Path

    js = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    assert '"/api/latex/files?q=" + encodeURIComponent(search.value || "") + "&path=" + encodeURIComponent(me.path)' in js
    server = (Path(WEBAPP) / "server.py").read_text(encoding="utf-8")
    assert "latex_edit.supporting_files(served.root, q, draft=draft or None)" in server


def test_a_link_acts_undo_names_what_it_takes_back_and_stands_with_its_status_apart_from_undo_this_save():
    js = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    assert 'mkButton(t("撤销"),' not in js                               # no bare 撤销 beside 撤销这次保存
    words = js[js.index("  function undoActWords(action, done) {"):js.index("  // 撤销: the entries the last act wrote")]
    assert 'if (action === "link") return t("撤销这次关联");' in words
    assert 'if (action === "withdraw") return t("撤销这次撤回");' in words
    # a reanchor's undo is named after what its status said, never 「确认」, the link's word (review of 8439d3c)
    assert 'return done === t("已记录：仍然成立") ? t("撤销“仍然成立”") : t("撤销“是这段”");' in words
    assert "撤销这次确认" not in js
    assert "me.ui.undoLink.textContent = undoActWords(action, done);" in js
    assert "said.append(status, undoLink);" in js and "tools.append(save, undo, exported.button, source, said);" in js
    i18n = (WEBAPP / "i18n.js").read_text(encoding="utf-8")
    for zh, en in (("撤销这次关联", "Undo this link"), ("撤销这次撤回", "Undo this withdrawal"),
                   ("撤销“是这段”", "Undo “This is the passage”"), ("撤销“仍然成立”", "Undo “Still holds”")):
        assert f'"{zh}": "{en}",' in i18n


# -- the real path: L.open() and 显示源码 under the editor's driver (review of 8439d3c) --------------------------------

_OPEN_DRIVEN = r"""
const out = {}, MODEL = __MODEL__;
const F = L._pure.wordField(CM);
const shown = (st) => { const g = []; const w = st.field(F.field, false); if (!w) return null;
  w.deco.between(0, st.doc.length, (f, t, v) => g.push(st.doc.sliceString(f, t) + '=' + (v.spec.class || (v.spec.widget ? v.spec.widget.constructor.name : 'hidden'))));
  return g; };
evidenceAnswer = () => ({revision: 'r1', links: [{link: 'pl-1', last: 'pe-1', quote: '是基准', file: 'figure:figures/fig1.pdf', note: null,
  label: 'Li', at: null, state: 'changed', from: null, to: null, candidates: [{from: MODEL.indexOf('式'), to: MODEL.length - 1, text: '式 是基准。'}],
  file_missing: false}]});
L.open('paper/chapters/model.tex'); await tick();
gets.shift().res({path: 'paper/chapters/model.tex', text: MODEL, revision: 'r1', line_ending: 'lf', read_only: false, links: [], mains: []});
await tick(); await tick(); await tick();
const V = viewsMade[viewsMade.length - 1];
out.cursor = V.state.selection.main.head;
out.opened = shown(V.state);
const source = byClass('latex-source');
source.fire('click'); await tick();
out.sourceOn = shown(V.state);
source.fire('click'); await tick();
out.sourceOff = shown(V.state);
V.hasFocus = true;                                   // with the focus the cursor at 0 shows the heading's source
source.fire('click'); await tick(); source.fire('click'); await tick();
out.focusedOff = shown(V.state);
// 是这段 on the waiting passage: its undo is named after what the status said
all.filter((e) => e.classList.contains('latex-waiting')).pop().fire('click'); await tick();
button('是这段').fire('click'); await tick();
const re = posts.shift(); out.reanchor = re.url;
re.res({act: 'reanchor', link: 'pl-1', entry: 'pe-2'}); await tick(); await tick(); await tick();
const actUndo = all.filter((e) => e.classList.contains('latex-act-undo')).pop();
out.undoWords = actUndo.textContent; out.undoHidden = actUndo.classList.contains('hidden');
out.status = byClass('latex-act').children[0].textContent;
console.log(JSON.stringify(out));
"""


@needs_node
def test_l_open_shows_model_tex_section_as_a_heading_and_show_source_twice_keeps_it_until_focused():
    import json
    import subprocess

    from test_v17_latex_editor import _DRIVER

    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    script = (prelude + "(async () => {\n" + _OPEN_DRIVEN.replace("__MODEL__", json.dumps(MODEL))
              + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });")
    got = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True, timeout=60).stdout
    out = json.loads(got.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    assert out["cursor"] == 0
    for drawn in (out["opened"], out["sourceOff"]):
        assert "模型=latex-h latex-h2" in drawn and "\\section{=hidden" in drawn, drawn
        assert "\\section=latex-cmd" not in drawn
    assert out["sourceOn"] is None                                       # 显示源码: no word field at all
    assert "模型=latex-h latex-h2" not in out["focusedOff"]              # focused: the cursor's construct as source
    assert out["reanchor"] == "/api/latex/evidence/reanchor"
    assert out["status"] == "已记录：是这段"
    assert out["undoWords"] == "撤销“是这段”" and not out["undoHidden"]

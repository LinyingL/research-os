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


def test_the_files_search_takes_the_open_draft_from_the_editor_and_the_server():
    from pathlib import Path

    js = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    assert '"/api/latex/files?q=" + encodeURIComponent(search.value || "") + "&path=" + encodeURIComponent(me.path)' in js
    server = (Path(WEBAPP) / "server.py").read_text(encoding="utf-8")
    assert "latex_edit.supporting_files(served.root, q, draft=draft or None)" in server


def test_a_link_acts_undo_names_what_it_takes_back_and_stands_with_its_status_apart_from_undo_this_save():
    js = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    assert 'mkButton(t("撤销"),' not in js                               # no bare 撤销 beside 撤销这次保存
    assert ('return action === "link" ? t("撤销这次关联") : action === "withdraw" ? t("撤销这次撤回") : '
            't("撤销这次确认");') in js
    assert "me.ui.undoLink.textContent = undoActWords(action);" in js
    assert "said.append(status, undoLink);" in js and "tools.append(save, undo, exported.button, source, said);" in js
    i18n = (WEBAPP / "i18n.js").read_text(encoding="utf-8")
    for zh, en in (("撤销这次关联", "Undo this link"), ("撤销这次撤回", "Undo this withdrawal"), ("撤销这次确认", "Undo this confirmation")):
        assert f'"{zh}": "{en}",' in i18n

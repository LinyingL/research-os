"""DESIGN.md 22.4, 22.6, 22.7 (task V17, B2b): 「导出 PDF」 on the page. Each test names its scenario.

The editor runs under node on the vendored CodeMirror (the driver of test_v17_latex_editor.py); every request it makes
is answered by the scenario, so what is checked is what the person sees and what the page sends: the settings line
(main file, the engine as a quiet choice, 导出的 PDF), unsaved text saved first and a refused save stopping the export,
the running state with the engine named and 「停止」, 已导出 with 打开 PDF and 在 Finder 中显示 as text links, the question
of 替换 / 另存为 and of the main file, 导出没有成功 in clay with each file:line a link into the editor only where the server
passed the path, a missing character in ochre, the log under 详情, and the same words in English."""

from __future__ import annotations

import json
import subprocess

import pytest

from test_v17_latex_editor import _DRIVER, NODE, WEBAPP

_SCENARIO = r"""
const out = {};
const texts = (el) => el == null ? [] : typeof el === 'string' ? [el] : [el.textContent].concat(...el.children.map(texts));
const words = (el) => texts(el).filter(Boolean).join(' ');
const within = (root, test) => { const found = []; const walk = (e) => { if (!e || typeof e === 'string') return; if (test(e)) found.push(e); e.children.forEach(walk); }; walk(root); return found; };
let R = null;
const result = () => R || (R = byClass('latex-export-result'));   // the editor's, the first one asked for
const exportPosts = () => posts.filter((p) => p.url.startsWith('/api/latex/export'));
let lastPdf = 'reports/a.pdf';   // the last export of a.tex whose PDF is still there, as the server would answer
exportAnswer = () => ({main: 'a.tex', mains: ['a.tex'], engine: 'xelatex', engines: ['pdflatex', 'xelatex', 'lualatex'],
                       last: lastPdf && {pdf: lastPdf, at: '2026-11-13T10:00:00Z'}});
L.open('a.tex'); await tick();
gets.shift().res({path: 'a.tex', text: 'l1\nl2\nl3 \\foo\nl4\n', revision: 'r1', line_ending: 'lf', read_only: false, links: [], mains: ['a.tex']});
await tick(); await tick(); await tick();
const V = viewsMade[viewsMade.length - 1];
const exp = button('导出 PDF'), select = all.find((e) => e.tag === 'select');
out.button = {cls: exp.className, inTools: byClass('latex-tools').children.includes(exp)};
out.settings = {words: words(byClass('latex-export-line')), engine: select.value,
                options: select.children.map((o) => [o.value, o.textContent]),
                hidden: byClass('latex-export-line').classList.contains('hidden')};
out.settingsUrl = exportGets[0];
// unsaved text is saved first; then one export, the engine named while it runs, 停止 a text link
V.dispatch({changes: {from: 0, insert: 'X'}});
exp.fire('click'); await tick();
out.firstPost = posts[0].url;
posts.shift().res({revision: 'r2', changed: true, previous: 'r1'}); await tick(); await tick(); await tick();
const sent = exportPosts()[0];
out.exportBody = sent.body; out.running = words(result());
// the page drawn again during the run (a scan) shows the run with 停止; another draft's export waits for it
const during = L.exporter('a.tex'); await tick();
out.during = {words: words(during.block.children[1]), disabled: during.button.disabled};
const other = L.exporter('b.tex'); await tick();
other.button.fire('click'); await tick();
out.otherWhileRunning = {words: words(other.block.children[1]), posts: exportPosts().length};
const stop = button('停止');
out.stopClass = stop.className;
exp.fire('click'); await tick(); out.postsWhileRunning = exportPosts().length;
stop.fire('click'); await tick(); out.stopPost = exportPosts()[1].url; exportPosts()[1].res({stopped: true});
posts.splice(posts.indexOf(sent), 1);
sent.res({exported: false, stopped: null, main: 'a.tex', engine: 'xelatex',
  errors: [{file: './a.tex', line: 3, text: 'Undefined control sequence.', path: 'a.tex'},
           {file: '/usr/local/texlive/2023/texmf-dist/tex/latex/base/x.sty', line: 1, text: 'Emergency stop.', path: null}],
  notes: ['缺少宏包 foo'], missing_characters: [], missing_message: null, log: 'THE LOG', message: '导出没有成功'});
await tick(); await tick();
posts.length = 0;
out.duringAfter = {words: words(during.block.children[1].children[0]), disabled: during.button.disabled};
const failed = byClass('latex-export-failed');
out.failed = {words: words(failed), tag: failed.tag};
const errLinks = within(result(), (e) => e.tag === 'button');
out.errorLinks = errLinks.map((b) => [b.textContent, b.className]);
out.errorText = within(result(), (e) => e.tag === 'li').map(words);
out.notes = within(result(), (e) => e.tag === 'p' && e.className === 'node-sub').map(words);
const fold = within(result(), (e) => e.tag === 'details')[0];
out.fold = {summary: fold.children[0].textContent, log: fold.children[1].textContent};
// the error's file:line moves the open draft to that line
errLinks[0].fire('click'); await tick();
out.cursor = {head: V.state.selection.main.head, line3: V.state.doc.line(3).from, gets: gets.length};
// the engine changed in the quiet choice is sent once; the run answers that the PDF there was not exported
select.value = 'lualatex'; select.fire('change');
exp.fire('click'); await tick(); await tick();
out.engineBody = exportPosts()[0].body; out.runningLua = words(result());
exportPosts()[0].res({exported: false, ask: 'replace', file: 'reports/a.pdf', save_as: 'reports/a-导出.pdf', main: 'a.tex',
                      engine: 'lualatex', errors: [], notes: [], log: 'L', message: '...'});
await tick(); await tick(); posts.length = 0;
out.ask = words(result());
out.askButtons = within(result(), (e) => e.tag === 'button').map((b) => [b.textContent, b.className]);
button('另存为 reports/a-导出.pdf').fire('click'); await tick(); await tick();
out.saveAsBody = exportPosts()[0].body;
lastPdf = 'reports/a-导出.pdf';
exportPosts()[0].res({exported: true, pdf: 'reports/a-导出.pdf', main: 'a.tex', engine: 'lualatex', errors: [], notes: [],
  missing_characters: ['喆'], missing_message: '有 1 个字符字体里没有，PDF 里缺了：喆', log: 'L', message: '已导出 reports/a-导出.pdf'});
await tick(); await tick(); posts.length = 0;
out.done = words(result());
out.doneLinks = within(result(), (e) => e.tag === 'button').map((b) => [b.textContent, b.className]);
out.warn = within(result(), (e) => e.className === 'latex-export-warn').map(words);
out.doneFold = within(result(), (e) => e.tag === 'details').length;
within(result(), (e) => e.tag === 'button' && e.textContent === '在 Finder 中显示')[0].fire('click'); await tick();
out.openPost = posts[0] && {url: posts[0].url, body: posts[0].body}; posts.length = 0;
// the page drawn again (the scan after the export redraws it) shows the same answer; another draft's does not
out.redrawn = words(L.exporter('a.tex').block.children[1]) === out.done;
out.otherDraft = L.exporter('b.tex').block.children[1].children.length;
lastPdf = 'reports/a.pdf';
posts.length = 0;
// several main files: the person chooses one
exp.fire('click'); await tick(); await tick();
exportPosts()[0].res({exported: false, ask: 'main', mains: ['a.tex', 'b.tex'], message: null}); await tick(); await tick(); posts.length = 0;
out.askMain = words(result());
within(result(), (e) => e.tag === 'button' && e.textContent === 'b.tex')[0].fire('click'); await tick(); await tick();
out.mainBody = exportPosts()[0].body;
exportPosts()[0].rej({state: 'workspace_no_tex', engineMessage: '这台 Mac 上没有找到 TeX（TeX Live 或 MacTeX）。'}); await tick(); await tick(); posts.length = 0;
out.refused = {words: words(result()), cls: result().children[0].className};
out.refusedMore = [];
for (const err of [{state: 'workspace_cloud', engineMessage: '有文件还在 iCloud 里没下载：paper/a.tex'},
                   {state: 'workspace_busy', engineMessage: '正在导出另一份文稿，请等它结束。'}, {}]) {
  exp.fire('click'); await tick(); await tick();
  exportPosts()[0].rej(err); await tick(); await tick(); posts.length = 0;
  out.refusedMore.push(words(result()));
}
// a save refused stops the export: no export is sent
V.dispatch({changes: {from: 0, insert: 'Y'}});
exp.fire('click'); await tick();
posts.shift().rej({state: 'none'}); await tick(); await tick();
out.afterRefusedSave = {exports: exportPosts().length, words: words(result())};
// a save under way: the export waits for its answer, then runs
button('保存').fire('click'); await tick();
const saving = posts.shift();
exp.fire('click'); await tick(); await tick();
out.exportsWhileSaving = exportPosts().length;
saving.res({revision: 'r4', changed: true, previous: 'r3'}); await tick(); await tick(); await tick(); await tick();
out.exportsAfterSave = exportPosts().length;
const done = {exported: true, pdf: 'reports/a.pdf', main: 'a.tex', engine: 'xelatex', errors: [], notes: [], log: 'L', message: ''};
exportPosts()[0].res(done); await tick(); await tick(); posts.length = 0;
const redraw = async () => { const x = L.exporter('a.tex'); await tick(); await tick(); return words(x.block.children[1]); };
out.keptOnRedraw = await redraw();
// a later save of the draft: the answer was about the text before it
V.dispatch({changes: {from: 0, insert: 'Z'}});
button('保存').fire('click'); await tick();
posts.shift().res({revision: 'r5', changed: true, previous: 'r4'}); await tick(); await tick(); posts.length = 0;
out.afterSaveRedraw = await redraw();
// the PDF exported is gone: 已导出 and its 打开 PDF are not drawn again
exp.fire('click'); await tick(); await tick();
exportPosts()[0].res(done); await tick(); await tick(); posts.length = 0;
lastPdf = null;
out.goneRedraw = await redraw();
lastPdf = 'reports/a.pdf';
// the same, in English
window.ROI18N_SAVED = 'en';
eval(fs.readFileSync(%(i18n)s, 'utf8'));
out.enButton = window.ROI18N.t('导出 PDF');
const fresh = L.exporter('a.tex'); await tick();
out.enSettings = words(fresh.block.children[0]);
fresh.button.fire('click'); await tick();
out.enRunning = words(fresh.block.children[1]);
exportPosts()[0].res({exported: false, stopped: null, errors: [], notes: [], log: 'L'}); await tick(); await tick();
out.enFailed = words(fresh.block.children[1]);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def page() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    script = (prelude + "(async () => {\n" + _SCENARIO % {"i18n": json.dumps(str(WEBAPP / "i18n.js"))}
              + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });")
    got = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True, timeout=60).stdout
    out = json.loads(got.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    return out


def test_export_is_a_quiet_button_in_the_toolbar_and_the_settings_line_names_main_file_engine_and_last_pdf(page):
    assert page["button"] == {"cls": "btn btn-small", "inTools": True}
    assert page["settings"]["hidden"] is False and page["settings"]["engine"] == "xelatex"
    assert page["settings"]["words"] == "主文件 a.tex 排版引擎 pdfLaTeX XeLaTeX LuaLaTeX 导出的 PDF reports/a.pdf 打开 PDF"
    assert page["settings"]["options"] == [["pdflatex", "pdfLaTeX"], ["xelatex", "XeLaTeX"], ["lualatex", "LuaLaTeX"]]
    assert page["settingsUrl"] == "/api/latex/export?path=a.tex&project=%2Fp"   # tied to the project the page shows


def test_unsaved_text_is_saved_first_and_the_run_names_its_engine_with_a_way_to_stop_it(page):
    assert page["firstPost"] == "/api/latex/save"
    assert page["exportBody"] == {"project": "/p", "path": "a.tex"}
    assert page["running"] == "正在用 XeLaTeX 导出… 停止" and page["stopClass"] == "ws-text-link"
    assert page["postsWhileRunning"] == 1               # a second click while it runs sends nothing
    assert page["stopPost"] == "/api/latex/export/stop"


def test_a_failed_export_is_said_in_clay_with_its_errors_linked_only_where_the_server_passed_the_path(page):
    assert page["failed"] == {"words": "导出没有成功", "tag": "p"}
    assert page["errorLinks"] == [["./a.tex:3", "ws-text-link mono"]]       # TeX's own file stays text
    assert page["errorText"] == ["./a.tex:3   Undefined control sequence.",
                                 "/usr/local/texlive/2023/texmf-dist/tex/latex/base/x.sty:1   Emergency stop."]
    assert page["notes"] == ["缺少宏包 foo"]
    assert page["fold"] == {"summary": "详情", "log": "THE LOG"}
    assert page["cursor"]["head"] == page["cursor"]["line3"] and page["cursor"]["gets"] == 0   # the open draft, not reopened


def test_the_engine_chosen_is_sent_once_and_a_pdf_not_exported_asks_replace_or_save_as(page):
    assert page["engineBody"] == {"project": "/p", "path": "a.tex", "engine": "lualatex"}
    assert page["runningLua"] == "正在用 LuaLaTeX 导出… 停止"
    assert page["ask"] == "reports/a.pdf 不是上次导出的文件，要替换它吗？ 替换 另存为 reports/a-导出.pdf"
    assert page["askButtons"] == [["替换", "btn btn-small"], ["另存为 reports/a-导出.pdf", "btn btn-small"]]
    assert page["saveAsBody"] == {"project": "/p", "path": "a.tex", "main": "a.tex", "pdf": "save_as"}


def test_an_export_that_succeeded_offers_open_and_finder_as_text_links_and_a_missing_character_in_ochre_kept_on_a_redraw(page):
    assert page["done"].startswith("已导出 reports/a-导出.pdf 打开 PDF 在 Finder 中显示")
    assert page["doneLinks"] == [["打开 PDF", "ws-text-link"], ["在 Finder 中显示", "ws-text-link"]]
    assert page["warn"] == ["有 1 个字符字体里没有，PDF 里缺了：喆"] and page["doneFold"] == 0
    assert page["openPost"] == {"url": "/api/open", "body": {"path": "reports/a-导出.pdf", "reveal": True}}
    assert page["redrawn"] is True and page["otherDraft"] == 0


def test_several_main_files_are_a_choice_and_a_refusal_or_a_refused_save_says_the_export_did_not_run(page):
    assert page["askMain"] == "有几个主文件包含这个文件。导出哪一个？ a.tex b.tex"
    assert page["mainBody"] == {"project": "/p", "path": "a.tex", "main": "b.tex"}
    # a refusal before anything ran says the engine's own sentence (22.4); an error without one, the framing
    assert page["refused"] == {"words": "这台 Mac 上没有找到 TeX（TeX Live 或 MacTeX）。 [workspace_no_tex]",
                               "cls": "notice-status err"}
    assert page["refusedMore"] == ["有文件还在 iCloud 里没下载：paper/a.tex [workspace_cloud]",
                                   "正在导出另一份文稿，请等它结束。 [workspace_busy]", "导出没有成功 [none]"]
    assert page["afterRefusedSave"] == {"exports": 0, "words": "文稿没有保存，所以没有导出。"}


def test_the_export_speaks_english_on_an_english_screen(page):
    assert page["enButton"] == "Export PDF"
    assert page["enSettings"] == "Main file a.tex Typesetting engine pdfLaTeX XeLaTeX LuaLaTeX Exported PDF reports/a.pdf Open PDF"
    assert page["enRunning"] == "Exporting with XeLaTeX… Stop"
    assert page["enFailed"] == "The export did not succeed Details L"


def test_a_tex_drafts_own_page_offers_export_under_its_head_and_the_editor_keeps_one_primary():
    node = (WEBAPP / "node.js").read_text(encoding="utf-8")
    assert "exporter = ROLatex.exporter(page.path); actions.appendChild(exporter.button);" in node
    assert "if (exporter) host.appendChild(exporter.block);" in node
    latex = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    assert 'mkButton(t("导出 PDF"), "btn btn-small"' in latex and latex.count("btn-primary") == 2
    css = (WEBAPP / "workspace.css").read_text(encoding="utf-8")
    assert ".latex-export-failed { margin: 0; color: var(--clay);" in css
    assert ".latex-export-warn { margin: 0; border-left: 2px solid var(--ochre);" in css


def test_a_page_drawn_again_during_the_run_shows_it_and_gets_its_answer_and_another_draft_waits(page):
    assert page["during"] == {"words": "正在用 XeLaTeX 导出… 停止", "disabled": True}
    assert page["otherWhileRunning"] == {"words": "正在导出另一份文稿，请等它结束。", "posts": 1}
    assert page["duringAfter"] == {"words": "导出没有成功", "disabled": False}


def test_an_export_asked_while_a_save_runs_waits_for_that_save(page):
    assert page["exportsWhileSaving"] == 0 and page["exportsAfterSave"] == 1


def test_an_old_answer_is_not_drawn_again_after_a_save_or_once_its_pdf_is_gone(page):
    assert page["keptOnRedraw"].startswith("已导出 reports/a.pdf 打开 PDF")
    assert page["afterSaveRedraw"] == "" and page["goneRedraw"] == ""

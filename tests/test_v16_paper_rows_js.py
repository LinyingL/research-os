"""Research OS V16, task B3-2 (DESIGN.md 21.5, 21 intro decision 5): the pages. 图表与论断 and 数据与代码 under node, fed
the engine's own payloads for the thesis in miniature of test_v16_paper_rows, in both languages: the one control that
chooses the paper and its picker, the paper's figures first, 其他文稿 and 不属于论文 folded, the thumbnails, the attempt
each row names, 「不属于论文」 and 「放回论文」 with their 撤销, and the 任务输出 block that is gone. Every test names its scenario."""

from __future__ import annotations

import subprocess

import pytest

from test_v16_paper_rows import (  # noqa: F401 -- the scenario's fixtures
    A1, A2, EXTRA_FIG, LOOSE, REPORT, SVG, VECTOR, _agent_work, drafts, mappings_attach, root,
)
from test_v16_provenance import DRAFT, FIG, HAND, OTHER_DRAFT, PKG, RAW, WORD1, WORD3, _choose, _cite_table, _data_code, _page, _results
from test_v16_provenance_js import _json, _run
from test_v9_language import needs_node
from researchos import outputs
from researchos.records import papers

#: Today, for the page's "changed on" dates: the tests are run on any day.
_TODAY = r"""
const RealDate = Date;
global.Date = class extends RealDate { constructor(...a) { if (a.length) super(...a); else super(2026, 9, 30, 12, 0, 0); } static now() { return new RealDate(2026, 9, 30, 12).getTime(); } };
"""

_PAGE_BODY = r"""
const stages = %(stages)s;
let stage = 0;
global.apiGet = async (path) => { calls.push("GET " + path); if (path === "/api/results") return stages[stage]; throw new Error("no fixture for " + path); };
global.posted = [];
global.refreshCurrentView = async () => { await window.RONode.loadResults(views.results, true); };
global.apiPost = async (path, body) => {
  calls.push("POST " + path); posted.push([path, body]);
  stage = Math.min(stage + 1, stages.length - 1);
  return { ok: true, generation: 6 + stage };
};
const host = el("div", "host"); views.results = host;
const rowOf2 = (path) => host.all().find((e) => e.cls.has("res-row") && e.all().some((k) => k.cls.has("res-path") && k.textContent === path));
const pathsIn = (node) => node.all().filter((e) => e.cls.has("res-row")).flatMap((r) => r.all().filter((k) => k.cls.has("res-path")).map(text));
const section2 = (cls) => host.all().find((e) => e.cls.has(cls));
%(body)s
"""


def _node_run(script: str, *, language: str | None, view: str) -> object:
    """`_run`, a failing script reporting what node said."""
    try:
        return _run(script, language=language, view=view)
    except subprocess.CalledProcessError as exc:
        raise AssertionError(exc.stderr[-2000:]) from exc


def _page_run(stages: list[dict], body: str, *, language: str | None) -> object:
    return _node_run(_TODAY + _PAGE_BODY % {"stages": _json(stages), "body": body}, language=language, view="results")


# -- the paper is chosen once ------------------------------------------------------------------------------


_PICKER = r"""
(async () => {
  await window.RONode.loadResults(host, false);
  const out = {};
  const control = buttons(host, %(set)s);
  out.controls = control.length;                     // one control, not a button on every draft
  const picker = byClass(host, "res-picker-list")[0];
  out.hiddenAtFirst = picker.cls.has("hidden");
  control[0].click();
  out.shown = !picker.cls.has("hidden");
  const rows = (node) => node.kids.filter((k) => typeof k !== "string" && k.cls.has("res-picker-row"));
  // a file name is text, never the control (21.8); the choice is a button of its own
  out.first = rows(picker).map((r) => [text(r.kids[0]), text(r.kids[1] || "")]);
  out.names = rows(picker).map((r) => r.all().filter((k) => k.tag === "BUTTON").map(text));
  out.nameIsText = rows(picker).every((r) => r.kids[0].tag === "SPAN" && !r.kids[0].cls.has("node-link"));
  const more = byClass(picker, "res-picker-more")[0];
  out.more = [text(more.kids[0]), rows(more.kids[1]).map((r) => text(r.kids[0]))];
  out.claimDrafts = byClass(host, "res-draft").length;
  // choosing one writes the mark and the page reads again, now with a paper
  const choice = rows(picker)[1].all().find((k) => k.tag === "BUTTON");
  choice.click();
  await settle(); await settle();
  out.posted = posted.slice();
  out.reads = calls.filter((c) => c === "GET /api/results").length;
  out.lastGeneration = lastGeneration;
  const list = byClass(host, "res-paper-list")[0];
  out.paper = list.all().filter((e) => e.cls.has("res-row")).map((r) => [text(byClass(r, "mono")[0]), r.all().filter((k) => k.tag === "BUTTON" && k.cls.has("btn")).map(text)]);
  out.controlsAfter = buttons(host, %(set)s).length;
  out.claimsAfter = byClass(host, "res-draft").map((d) => text(d.kids[0]));
  // the picker no longer offers it
  const after = byClass(host, "res-picker-list")[0];
  out.offered = rows(after).length + rows(byClass(after, "res-picker-more")[0].kids[1]).length;
  // 不再作为论文正文 takes it back
  buttons(host, %(unset)s)[0].click();
  await settle(); await settle();
  out.posted2 = posted.slice(1);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_the_paper_is_chosen_with_one_control_that_opens_a_picker_of_the_drafts_changed_most_recently_first(root, drafts, language):  # noqa: F811
    zh = language == "zh"
    before = _results(root)
    _choose(root, DRAFT)
    after = _results(root)
    papers.set_marked(root, DRAFT, False, via="cli")
    out = _page_run([before, after, before], _PICKER % {"set": _json("设为论文正文" if zh else "Set as the paper"),
                                                       "unset": _json("不再作为论文正文" if zh else "Unset as the paper")}, language=language)
    assert out["controls"] == 1 and out["hiddenAtFirst"] is True and out["shown"] is True and out["claimDrafts"] == 0
    order = [LOOSE, DRAFT, OTHER_DRAFT, "00_Current/drafts/d6.md", "00_Current/drafts/d5.md"]
    assert [row[0] for row in out["first"]] == order  # five shown, the one changed most recently first
    days = ["10月10日", "10月8日", "10月7日", "10月6日", "10月5日"] if zh else ["10 Oct", "8 Oct", "7 Oct", "6 Oct", "5 Oct"]
    assert [row[1] for row in out["first"]] == [("改动于 " if zh else "Changed ") + day for day in days]
    assert out["nameIsText"] is True and out["names"] == [["选这份" if zh else "Choose this one"]] * 5  # one button a row
    assert out["more"][0] == ("更多" if zh else "More")  # the rest under 「更多」
    assert out["more"][1] == [f"00_Current/drafts/d{n}.md" for n in (4, 3, 2, 1)]
    # choosing the second writes the mark through the existing path, and the page reads again once
    assert out["posted"] == [["/api/papers", {"draft": DRAFT, "marked": True}]] and out["reads"] == 2 and out["lastGeneration"] == 7
    assert out["paper"] == [[DRAFT, ["不再作为论文正文" if zh else "Unset as the paper"]]]
    assert out["controlsAfter"] == 1  # still one control, to add the appendix
    assert out["claimsAfter"] and DRAFT in out["claimsAfter"][0]  # its claims are listed, the others' are not
    assert out["offered"] == 8  # the drafts the paper can still take
    assert out["posted2"] == [["/api/papers", {"draft": DRAFT, "marked": False}]]


# -- the paper first ----------------------------------------------------------------------------------------


_LISTS = r"""
(async () => {
  await window.RONode.loadResults(host, false);
  const out = {};
  const blocks = host.all().filter((e) => e.cls.has("res-block"));
  out.headings = blocks.map((b) => text(byClass(b, "desk-title")[0]));
  const figs = blocks[blocks.length - 2];
  out.figureLists = figs.kids.filter((k) => typeof k !== "string" && (k.cls.has("res-list") || k.cls.has("res-group") || k.cls.has("dc-fold"))).map((k) => [k.cls.has("res-list") ? "list" : k.cls.has("res-group") ? "group" : "fold", pathsIn(k)]);
  out.open = byClass(figs, "res-open").map((e) => [text(e), e.className]);
  out.foldTitles = byClass(figs, "dc-fold").map((d) => [text(d.kids[0]), d.open === true || !!d.cls.has("open")]);
  out.groupTitle = byClass(figs, "res-group-title").map(text);
  // one row for a figure two drafts include, naming both, the paper's first
  const word = rowOf2(%(word1)s);
  out.word = { drafts: byClass(word, "res-drafts-line").map(text), count: host.all().filter((e) => e.cls.has("res-row") && e.all().some((k) => k.cls.has("res-path") && k.textContent === %(word1)s)).length };
  // the attempt it belongs to, a quiet field; and a row with none says nothing
  out.main = { attempts: byClass(rowOf2(%(main)s), "res-attempts").map(text), links: byClass(rowOf2(%(main)s), "res-attempts")[0].all().filter((k) => k.tag === "BUTTON").length };
  out.noAttempt = byClass(rowOf2(%(word1)s), "res-attempts").length;
  // thumbnails
  const thumb = (path) => byClass(rowOf2(path), "res-thumb").map((i) => [i.src, i.loading, i.alt, (i.on.error || []).length]);
  out.thumbs = { main: thumb(%(main)s), vector: thumb(%(vector)s), svg: thumb(%(svg)s), extra: thumb(%(extra)s) };
  out.allThumbs = byClass(host, "res-thumb").length;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_once_the_paper_is_chosen_its_figures_come_first_the_other_drafts_are_folded_and_a_shared_figure_is_one_row(root, language):  # noqa: F811
    zh = language == "zh"
    _choose(root)
    data = _results(root)
    out = _page_run([data], _LISTS % {"word1": _json(WORD1), "main": _json(FIG["main"]), "vector": _json(VECTOR), "svg": _json(SVG),
                                     "extra": _json(EXTRA_FIG)}, language=language)
    assert out["headings"] == (["论文正文", "图与表", "论断"] if zh else ["Paper draft", "Figures and tables", "Claim"])
    kinds = [kind for kind, _paths in out["figureLists"]]
    assert kinds == ["list", "group", "fold"]  # the paper's, the loose ones, 其他文稿; 不属于论文 is there only when something is in it
    paper, loose, other = (paths for _kind, paths in out["figureLists"])
    assert paper == [i["path"] for i in data["items"]] and loose == [EXTRA_FIG] and other == [i["path"] for i in data["other"]]
    assert out["groupTitle"] == ["没有被文稿引用的图和表" if zh else "Figures and tables not cited in a draft"]
    assert out["foldTitles"] == [[("其他文稿（2）展开" if zh else "Other drafts (2)Show more"), False]]  # folded
    word_line = out["word"]["drafts"][0]
    assert out["word"]["count"] == 1  # a figure two drafts include is one row
    assert word_line == (("出现在" if zh else "Included in") + f"Beyond_Persistence_v9_reader.md{'、' if zh else ', '}Beyond_Persistence_notes.md")
    open_n = data["counts"]["open"]
    assert out["open"] == [[(f"论文用到的图和表里，{open_n} 项缺来源或缺尝试。" if zh else
                             f"{open_n} of the figures and tables the paper uses lack a source or an attempt."), "node-sub res-open warn"]]
    assert out["main"]["attempts"] == [("尝试" if zh else "Attempt") + "#1 Plots (02)"] and out["main"]["links"] == 1
    assert out["noAttempt"] == 0
    # a picture gets a thumbnail from the existing route, loaded when it scrolls into view; a pdf and an svg get none
    path = FIG["main"].replace("/", "%2F")
    assert out["thumbs"]["main"] == [[f"/api/workspace/asset?path={path}", "lazy", "", 1]]
    assert out["thumbs"]["vector"] == [] and out["thumbs"]["svg"] == [] and len(out["thumbs"]["extra"]) == 1
    assert out["allThumbs"] == sum(1 for key in ("items", "other", "loose") for i in data[key] if i["thumb"])  # the folded rows draw theirs too


@needs_node
def test_with_no_draft_chosen_the_page_says_so_lists_every_cited_figure_as_the_papers_and_offers_the_one_control(root):  # noqa: F811
    data = _results(root)
    out = _page_run([data], r"""
(async () => {
  await window.RONode.loadResults(host, false);
  const blocks = host.all().filter((e) => e.cls.has("res-block"));
  process.stdout.write(JSON.stringify({ paper: text(blocks[0]), controls: buttons(host, "设为论文正文").length, folds: byClass(host, "dc-fold").length,
    rows: pathsIn(blocks[1]).length, claims: text(blocks[blocks.length - 1]) }));
})().catch((e) => { console.error(e); process.exit(1); });
""", language=None)
    assert out["controls"] == 1 and out["folds"] == 0 and out["rows"] == len(data["items"]) + len(data["loose"])
    assert "还没有文稿设为论文正文" in out["paper"] and "所有文稿的图和表都当作论文用到的" in out["paper"]
    assert "还没有选定论文正文，这里暂时没有论断。" in out["claims"]


# -- 不属于论文 and 放回论文 ----------------------------------------------------------------------------------


_OUT = r"""
(async () => {
  await window.RONode.loadResults(host, false);
  const out = {};
  const row = rowOf2(%(needs)s);
  out.before = { buttons: buttons(row, %(put_out)s).length, back: buttons(row, %(put_back)s).length, tone: byClass(row, "status-label").pop().className };
  buttons(row, %(put_out)s)[0].click();
  await settle(); await settle();
  out.posted = posted.slice();
  out.reads = calls.filter((c) => c === "GET /api/results").length;
  out.lastGeneration = lastGeneration;
  // the row left the paper's list for the folded group, quiet; 撤销 stands at the top of the page
  const lists = host.all().filter((e) => e.cls.has("res-list"));
  out.inPaper = pathsIn(lists[lists.length - 2]).includes(%(needs)s);
  const fold = byClass(host, "dc-fold").find((d) => text(d.kids[0]).startsWith(%(fold_title)s));
  out.fold = text(fold.kids[0]);
  const moved = rowOf2(%(needs)s);
  out.moved = { buttons: buttons(moved, %(put_out)s).length, back: buttons(moved, %(put_back)s).length, tone: byClass(moved, "status-label").pop().className };
  out.open = byClass(host, "res-open").map(text);
  const notice = byClass(host, "dc-undo")[0];
  out.notice = text(notice);
  buttons(notice, %(undo)s)[0].click();
  await settle(); await settle();
  out.posted2 = posted.slice(1);
  out.noticeAfter = byClass(host, "dc-undo").length;
  out.backInPaper = pathsIn(host.all().filter((e) => e.cls.has("res-list"))[1]).includes(%(needs)s);
  // 放回论文 on a row of the folded group puts it back too (no 撤销 notice for it)
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_a_by_product_is_put_out_of_the_paper_with_one_click_kept_under_a_fold_and_taken_back_with_undo(root, language):  # noqa: F811
    zh = language == "zh"
    _choose(root)
    before = _results(root)
    papers.set_not_in_paper(root, FIG["needs"], True, via="app")
    out_of = _results(root)
    papers.set_not_in_paper(root, FIG["needs"], False, via="app")
    back = _results(root)
    words = {"put_out": "不属于论文" if zh else "Not part of the paper", "put_back": "放回论文" if zh else "Put back in the paper",
             "undo": "撤销" if zh else "Undo", "fold_title": "不属于论文（" if zh else "Not part of the paper ("}
    out = _page_run([before, out_of, back], _OUT % {"needs": _json(FIG["needs"]), **{k: _json(v) for k, v in words.items()}}, language=language)
    assert out["before"] == {"buttons": 1, "back": 0, "tone": "status-label tone-clay"}
    assert out["posted"] == [["/api/papers", {"file": FIG["needs"], "not_in_paper": True}]] and out["reads"] == 2 and out["lastGeneration"] == 7
    assert out["inPaper"] is False and out["fold"] == (f"不属于论文（1）展开" if zh else "Not part of the paper (1)Show more")
    assert out["moved"] == {"buttons": 0, "back": 1, "tone": "status-label tone-plain"}  # quiet, and offering 放回论文
    n = out_of["counts"]["open"]  # one fewer than before: what was put out is not the paper's open work
    assert n == before["counts"]["open"] - 1 and n > 1
    assert out["open"] == ([f"论文用到的图和表里，{n} 项缺来源或缺尝试。"] if zh else [f"{n} of the figures and tables the paper uses lack a source or an attempt."])
    assert out["notice"] == (f"已放到「不属于论文」里：fig_needs.png撤销" if zh else "Moved to “Not part of the paper”: fig_needs.pngUndo")
    # 撤销 writes 放回论文 and the notice goes
    assert out["posted2"] == [["/api/papers", {"file": FIG["needs"], "not_in_paper": False}]]
    assert out["noticeAfter"] == 0 and out["backInPaper"] is True


@needs_node
def test_a_member_s_control_says_it_travels_with_the_next_sync(root):  # noqa: F811
    _choose(root)
    data = _results(root)
    out = _node_run(_TODAY + r"""
global.RODesk = Object.assign(global.RODesk, { context: () => ({ mode: "member", me: { me: { id: "wang@example.com" } }, share: { mine: null } }) });
global.apiGet = async (path) => data;
const data = %(data)s;
(async () => {
  const host = el("div", "host"); views.results = host;
  await window.RONode.loadResults(host, false);
  const row = host.all().find((e) => e.cls.has("res-row") && e.all().some((k) => k.cls.has("res-path") && k.textContent === %(needs)s));
  process.stdout.write(JSON.stringify({ title: buttons(row, "不属于论文")[0].title, pick: buttons(host, "设为论文正文")[0].title }));
})().catch((e) => { console.error(e); process.exit(1); });
""" % {"data": _json(data), "needs": _json(FIG["needs"])}, language=None, view="results")
    assert "「不属于论文」的标记先记在你的工作副本里；下次同步时写入" in out["title"] and "正式的 papers.toml" in out["title"] and "不需要批准" in out["title"]
    assert "「设为论文正文」的标记先记在你的工作副本里" in out["pick"]


# -- 任务输出 is gone; each row says its attempt -----------------------------------------------------------------


_BLOCK = r"""
(async () => {
  const out = {};
  const figures = el("div", "host"), data = el("div", "host");
  views.results = figures; views.lineage = data;
  await window.RONode.loadResults(figures, false);
  await window.RONode.loadDataCode(data, false);
  const words = ["任务输出", "Task outputs"];
  out.block = [figures, data].map((h) => [byClass(h, "registered-outputs").length, words.some((w) => h.all().some((e) => text(e) === w))]);
  out.figureRows = Object.fromEntries(figures.all().filter((e) => e.cls.has("res-row")).map((r) => [text(byClass(r, "res-path")[0]),
    [text(r.all().find((k) => k.cls.has("node-link"))), byClass(r, "res-attempts").map(text)]]));
  // a row is named by its title -- the one it was registered with, else its file name -- and the path under it (21.7)
  out.dataRows = Object.fromEntries(data.all().filter((e) => e.cls.has("dc-row")).map((r) => [
    text(byClass(byClass(r, "dc-head")[0], "node-link")[0]) + " | " + text(byClass(r, "dc-path")[0]),
    byClass(r, "res-attempts").map(text)]));
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_the_task_output_block_is_gone_registered_files_are_rows_with_their_titles_and_each_row_names_the_attempt_of_its_task(root, language):  # noqa: F811
    zh = language == "zh"
    _agent_work(root)
    mappings_attach(root, f"{PKG}/code/03-stray.py", A1)
    results, data = _results(root), _data_code(root)
    out = _node_run(_TODAY + r"""
const results = %(results)s, data = %(data)s;
global.apiGet = async (path) => (path === "/api/results" ? results : path === "/api/data-code" ? data : { has_do: false });
global.apiPost = async () => ({ ok: true });
""" % {"results": _json(results), "data": _json(data)} + _BLOCK, language=language, view="results")
    assert out["block"] == [[0, False], [0, False]]  # neither page has the block
    attempt, a2, a1 = ("尝试" if zh else "Attempt"), "#2 Agent plots", "#1 Plots (02)"
    figures = out["figureRows"]
    assert figures[HAND] == ["Hand-drawn schematic", [attempt + a2]]  # the registered title, the attempt of the task
    assert figures["agent_out/fig_new.png"] == ["The new figure", [attempt + a2]]
    assert figures["agent_out/summary.csv"] == ["Summary of the run", [attempt + a2]]
    # the attempt its script is in, and the task's: the panel it reads is an output of the task, and a trace asks every
    # file of its chain which attempt holds it (21.5)
    assert figures[FIG["main"]] == ["fig_main.png", [attempt + a1 + ("、" if zh else ", ") + a2]]
    assert figures[WORD1] == ["image1.png", []]  # neither: nothing is said
    rows = out["dataRows"]
    assert rows["Cleaning script | agent_out/clean.py"] == [attempt + a2]
    assert rows["Cleaned data | agent_out/clean.csv"] == [attempt + a2]
    assert rows[f"The panel | {PKG}/Data/panel_clean.csv"] == [attempt + a2]
    assert rows[f"03-stray.py | {PKG}/code/03-stray.py"] == [attempt + a1]  # filed under an attempt by the researcher
    assert rows[f"02-plots.py | {PKG}/code/02-plots.py"] == [attempt + a1]  # the step number its attempt names
    assert rows[f"unused.csv | {PKG}/Data/unused.csv"] == []


@needs_node
def test_a_catalog_that_cannot_be_read_is_said_on_both_pages_and_the_rows_still_list(root):  # noqa: F811
    _agent_work(root)
    (root / outputs.catalog_path(REPORT)).write_text("not json", encoding="utf-8")
    results, data = _results(root), _data_code(root)
    out = _node_run(_TODAY + r"""
const results = %(results)s, data = %(data)s;
global.apiGet = async (path) => (path === "/api/results" ? results : path === "/api/data-code" ? data : { has_do: false });
(async () => {
  const figures = el("div", "host"), codes = el("div", "host");
  views.results = figures; views.lineage = codes;
  await window.RONode.loadResults(figures, false);
  await window.RONode.loadDataCode(codes, false);
  process.stdout.write(JSON.stringify([figures, codes].map((h) => [byClass(h, "err").map(text), byClass(h, "res-row").length + byClass(h, "dc-row").length])));
})().catch((e) => { console.error(e); process.exit(1); });
""" % {"results": _json(results), "data": _json(data)}, language=None, view="results")
    line = "输出列表暂时无法读取。 " + outputs.catalog_path(REPORT)  # the sentence, and the catalog it is about
    assert out[0][0] == [line] and out[1][0] == [line] and out[0][1] > 0 and out[1][1] > 0


# -- 数据与代码: a table of the paper can be put out of it ----------------------------------------------------


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_数据与代码_offers_不属于论文_on_a_table_of_the_paper_and_the_filter_count_follows_it(root, language):  # noqa: F811
    zh = language == "zh"
    _cite_table(root, RAW)
    _choose(root)
    before = _data_code(root)
    papers.set_not_in_paper(root, RAW, True, via="app")
    after = _data_code(root)
    out = _node_run(_TODAY + r"""
const stages = [%(before)s, %(after)s];
let stage = 0;
global.posted = [];
global.apiGet = async (path) => (path === "/api/stata" ? { has_do: false } : stages[stage]);
global.apiPost = async (path, body) => { posted.push([path, body]); stage = 1; return { ok: true, generation: 6 }; };
global.refreshCurrentView = async () => { await window.RONode.loadDataCode(views.lineage, true); };
(async () => {
  const host = el("div", "host"); views.lineage = host;
  await window.RONode.loadDataCode(host, false);
  const row = () => rowOf(host, "dc-row", %(raw)s);
  const out = { filters: byClass(host, "dc-filter").slice(1, 3).map(text), other: buttons(rowOf(host, "dc-row", %(fallback)s), %(put_out)s).length,
    tags: byClass(row(), "status-label").map(text), put: buttons(row(), %(put_out)s).length };
  buttons(row(), %(put_out)s)[0].click();
  await settle(); await settle();
  out.posted = posted;
  out.after = { filters: byClass(host, "dc-filter").slice(1, 3).map(text), tags: byClass(row(), "status-label").map(text), back: buttons(row(), %(put_back)s).length,
    put: buttons(row(), %(put_out)s).length, undo: byClass(host, "dc-undo").map(text) };
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
""" % {"before": _json(before), "after": _json(after), "raw": _json(RAW), "fallback": _json(f"{PKG}/raw/fallback/panel_pricing.csv"),
       "put_out": _json("不属于论文" if zh else "Not part of the paper"), "put_back": _json("放回论文" if zh else "Put back in the paper")},
               language=language, view="lineage")
    no, raw = ("缺生成脚本", "没有生成脚本") if zh else ("No generating script", "No script writes it")
    assert out["filters"] == [f"{no} 1", f"{raw} 2"] and out["other"] == 0  # a download of the paper's: no control
    assert out["tags"] == [no] and out["put"] == 1
    assert out["posted"] == [["/api/papers", {"file": RAW, "not_in_paper": True}]]
    assert out["after"]["filters"] == [f"{no} 0", f"{raw} 3"]  # no longer counted as open work
    assert out["after"]["tags"] == ["不属于论文" if zh else "Not part of the paper"]
    assert out["after"]["back"] == 1 and out["after"]["put"] == 0
    assert out["after"]["undo"] == [(f"已放到「不属于论文」里：{RAW}撤销" if zh else f"Moved to “Not part of the paper”: {RAW}Undo")]


# -- a figure's own page ----------------------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_a_figures_own_page_puts_it_out_of_the_paper_and_back(root, language):  # noqa: F811
    zh = language == "zh"
    _choose(root)
    pages = [_page(root, f"figure:{FIG['needs']}")]
    papers.set_not_in_paper(root, FIG["needs"], True, via="app")
    pages.append(_page(root, f"figure:{FIG['needs']}"))
    out = _node_run(_TODAY + r"""
const baseMk = global.mk;
global.mk = (tag, cls, text) => { const e = baseMk(tag, cls, text); Object.defineProperty(e, "childElementCount", { get: () => e.kids.filter((k) => typeof k !== "string").length }); return e; };
const pages = %(pages)s;
let stage = 0;
global.posted = [];
global.apiGet = async (path) => {
  if (path.startsWith("/api/node?")) return pages[stage];
  if (path.startsWith("/api/history?")) return { versions: [] };
  if (path.startsWith("/api/notes?")) return { notes: [] };
  if (path === "/api/me") return { me: { id: "li@example.com" } };
  throw new Error("no fixture for " + path);
};
global.apiPost = async (path, body) => { posted.push([path, body]); stage = 1; return { ok: true, generation: 6 }; };
global.refreshCurrentView = async () => { await window.RONode.loadNode(views.node, true); };
(async () => {
  views.node = el("div", "host");
  window.RONode.openNode(pages[0].id);
  await window.RONode.loadNode(views.node, false);
  const host = views.node;
  const out = { before: { put: buttons(host, %(put_out)s).length, tags: byClass(host, "node-marks").map((m) => m.all().map(text).filter(Boolean)) } };
  buttons(host, %(put_out)s)[0].click();
  await settle(); await settle();
  out.posted = posted;
  out.after = { back: buttons(host, %(put_back)s).length, put: buttons(host, %(put_out)s).length, tags: byClass(host, "node-marks").map((m) => m.all().map(text).filter(Boolean)) };
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
""" % {"pages": _json(pages), "put_out": _json("不属于论文" if zh else "Not part of the paper"),
       "put_back": _json("放回论文" if zh else "Put back in the paper")}, language=language, view="node")
    assert out["before"]["put"] == 1 and out["posted"] == [["/api/papers", {"file": FIG["needs"], "not_in_paper": True}]]
    assert out["after"] == {"back": 1, "put": 0, "tags": [[("不属于论文" if zh else "Not part of the paper")]]}

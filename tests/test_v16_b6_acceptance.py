"""Research OS V16, task B6 (DESIGN.md 21.3, 21.5, 21.8): what the researcher's acceptance of batch 5 found on a copy of a
real project. Four findings, each with its regression test:

 1. A reason-only change on an attempt's assessment read as a status change (「从「待定」改为「待定」」). It now says
    「研究判断仍是「待定」，理由改了」, on the page's timeline, in the panel's 「你上次查看以来的改动」, in a proposal's line and in the
    engine's word form (`history._attempt_change`), in both languages.
 2. A panel opened at the last panel's scroll position (`tests/test_v16_b6_panel_scroll.py`).
 3. 图表与论断 opened on a box 「提到它的笔记与报告」 whose 「它」 had nothing to refer to.
 4. A marked draft the index does not read had no row, so it could not be unmarked.

The engine runs against real projects; the pages run under node on the stand-in DOMs of the other tests, in both languages.
Every test names its scenario."""

from __future__ import annotations

import json

import pytest

from researchos import history
from researchos.webapp import node_api

from test_v10_desk_in_place import _TIMELINE, _changed
from test_v10_desk_in_place import _run as _panel_run
from test_v10_desk_panel import _PANEL_DOM, A16
from test_v8_phase2 import _map, _project, _scan
from test_v9_language import WEBAPP, _i18n_source, _node, needs_node


def _w(lang: str):
    return lambda zh, en: en if lang == "en" else zh


# -- 1. a change of the reason alone is not a change of the status ------------------------------------------------------------


def _version(cell: str, status: str, label: str, words: str, result: str = "") -> dict:
    """An attempt's version as /api/history serves it: the cell, what it reads as, the result."""
    return {"verdict": cell, "verdict_status": {"status": status, "label": label, "words": words}, "result": result,
            "at": "2026-10-20T10:00:00", "sha256": cell + result}


WAITING = _version("🕒 等复查", "pending", "待定", "等复查")
WAITING_AGAIN = _version("🕒 看了数据，样本太小", "pending", "待定", "看了数据，样本太小")
CURRENT = _version("✅ 稳健", "current", "现行", "稳健", "t=2.91")


def _page_run(lang: str | None, tail: str):
    """desk.js and node.js under node on the panel tests' stand-in DOM (the page's two files that word a verdict), `tail` run after."""
    i18n = "" if lang is None else f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    return _node("global.window = {};\n" + i18n + _PANEL_DOM + (WEBAPP / "desk.js").read_text(encoding="utf-8") + "\n"
                 + (WEBAPP / "node.js").read_text(encoding="utf-8") + "\n" + tail)


def _moves(lang: str | None, pairs: list[tuple[dict | None, dict]]) -> list[str]:
    return _page_run(lang, f"const pairs = {json.dumps(pairs, ensure_ascii=False)};\n"
                     "process.stdout.write(JSON.stringify(pairs.map(([a, b]) => window.RONode._verdictMove(a, b))));\n")


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_reason_only_change_reads_still_the_same_status_with_a_new_reason_not_from_pending_to_pending(lang):
    """The person confirmed an agent's suggestion with the same status and a new reason: the attempt's cell moved from
    「🕒 等复查」 to 「🕒 看了数据，样本太小」, both 待定. The timeline (and the panel that reuses it) says the reason changed;
    with the result changed too it joins 「结果改为」; a different status keeps today's sentence; a cell nobody touched says
    the assessment did not change."""
    w = _w(lang)
    again_result = {**WAITING_AGAIN, "result": "t=1.8"}
    moves = _moves(None if lang == "zh" else lang, [
        (WAITING, WAITING_AGAIN), (WAITING, again_result), (WAITING, CURRENT), (WAITING, dict(WAITING)),
        (WAITING, {**WAITING, "result": "t=3"})])
    assert moves == [
        w("研究判断仍是「待定」，理由改了", "Research assessment still “Pending”; the reason changed"),
        w("研究判断仍是「待定」，理由改了，结果改为：t=1.8", "Research assessment still “Pending”; the reason changed; new result: t=1.8"),
        w("研究判断从「待定」改为「现行」，结果改为：t=2.91", "Research assessment changed from “Pending” to “Current”; new result: t=2.91"),
        w("研究判断不变（待定）", "Research assessment unchanged (Pending)"),
        w("结果改为：t=3", "New result: t=3")]
    assert not any("待定」改为「待定" in m or "“Pending” to “Pending”" in m for m in moves)


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_two_markers_of_one_word_and_the_researchers_own_word_are_compared_as_they_read_not_as_they_are_written(lang):
    """A project that names two markers 现行 (✅ and ✔): changing the marker and the reason reads as the same status with a new
    reason; changing the marker alone reads as nothing moved. The researcher's own word (成立, a status of their own) is said as
    written in both languages, and a default word is told by its code (the stored 「待定」 in English is Pending)."""
    w = _w(lang)
    ticked = _version("✔ 稳健", "current", "现行", "稳健")
    ticked_again = _version("✔ 复核后仍稳健", "current", "现行", "复核后仍稳健")
    checked = _version("✅ 稳健", "current", "现行", "稳健")
    own = _version("成立 先看", "labelled", "成立", "先看")
    own_again = _version("成立 再看", "labelled", "成立", "再看")
    moves = _moves(None if lang == "zh" else lang, [(checked, ticked_again), (checked, ticked), (own, own_again), (own, WAITING)])
    assert moves == [
        w("研究判断仍是「现行」，理由改了", "Research assessment still “Current”; the reason changed"),
        w("研究判断不变（现行）", "Research assessment unchanged (Current)"),
        w("研究判断仍是「成立」，理由改了", "Research assessment still “成立”; the reason changed"),
        w("研究判断从「成立」改为「待定」", "Research assessment changed from “成立” to “Pending”")]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_panels_changes_since_the_last_visit_say_the_reason_changed(lang):
    """#16's panel on the desk, opened after a visit: its row was 「🕒」 and is now 「🕒 看了数据，样本太小」 -- the same
    status, a reason added. The part 「你上次查看以来的改动」 says so, in the panel, in both languages."""
    w = _w(lang)
    case = _changed(lang)
    key = "/api/history?node=" + A16.replace(":", "%3A").replace("#", "%23")
    now = case["get"][key]["versions"][0]
    older = case["get"][key]["versions"][1]
    now.update(verdict="🕒 看了数据，样本太小", verdict_status={"status": "pending", "label": "待定", "words": "看了数据，样本太小"})
    older.update(verdict="🕒", verdict_status={"status": "pending", "label": "待定", "words": ""})
    out = _panel_run(case, _TIMELINE, lang=None if lang == "zh" else lang)
    whats = [line for line in out["extra"] if line.startswith("tl-what: ")]
    assert whats == ["tl-what: " + w("研究判断仍是「待定」，理由改了，结果改为：t=3.10",
                                     "Research assessment still “Pending”; the reason changed; new result: t=3.10")]


def _proposal_line(lang: str | None, old: dict, new: dict, **extra) -> str:
    verdict = {"number": "16", "old": old["verdict"], "new": new["verdict"], "old_status": old["verdict_status"],
               "new_status": new["verdict_status"], **extra}
    return _page_run(lang, "process.stdout.write(JSON.stringify(window.RODesk._proposalWhat("
                     + json.dumps({"kind": "verdict_change", "files": [], "verdict": verdict}, ensure_ascii=False) + ")));\n")


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_a_proposal_that_changes_only_the_reason_says_so_instead_of_from_pending_to_pending(lang):
    """desk.js's line for a member's verdict_change (read at 14.5 and in 我的提议): a member who kept the status and wrote another
    reason proposes a cell that differs, and the line said the status moved from 「待定」 to 「待定」. It says the reason
    changes; a proposal that moves the status keeps its sentence (and 「放弃这条路线」); one whose cell differs only in its marker
    says the assessment does not change."""
    w = _w(lang)
    language = None if lang == "zh" else lang
    assert _proposal_line(language, WAITING, WAITING_AGAIN) == w(
        "#16 的研究判断仍是「待定」，理由改了", "#16: keep the research assessment “Pending” and change its reason")
    assert _proposal_line(language, WAITING, CURRENT) == w(
        "#16 的研究判断从「待定」改为「现行」", "#16: change the research assessment from “Pending” to “Current”")
    abandoned = _version("☠️ 伪协整", "abandoned", "已放弃", "伪协整")
    assert _proposal_line(language, WAITING, abandoned, abandons="yes") == w(
        "#16 的研究判断从「待定」改为「已放弃」，放弃这条路线",
        "#16: change the research assessment from “Pending” to “Abandoned”, abandoning the route")
    ticked = _version("✔ 稳健", "current", "现行", "稳健")
    assert _proposal_line(language, _version("✅ 稳健", "current", "现行", "稳健"), ticked) == w(
        "#16 的研究判断不变（现行）", "#16: the research assessment does not change (Current)")


def test_the_engines_word_form_says_the_reason_changed_in_both_languages_and_keeps_the_commands_marker_form():
    """`history._attempt_change` with `words=True` is the sentence the node page's comparison shows (the page asks for its own
    language); the command line's marker form (`words=False`) stays as it was."""
    result = {"result": "t=2.91"}
    cases = [(WAITING, WAITING_AGAIN, "研究判断仍是「待定」，理由改了", "Research assessment still “Pending”; the reason changed"),
             (WAITING, {**WAITING_AGAIN, **result}, "研究判断仍是「待定」，理由改了，结果改为：t=2.91",
              "Research assessment still “Pending”; the reason changed; new result: t=2.91"),
             (WAITING, {**CURRENT, **result}, "研究判断从「待定」改为「现行」，结果改为：t=2.91",
              "Research assessment changed from “Pending” to “Current”; new result: t=2.91"),
             (WAITING, dict(WAITING), "研究判断不变（「待定」）", "Research assessment unchanged (“Pending”)")]
    for old, new, zh, en in cases:
        assert history._attempt_change(old, new, words=True)["summary"] == zh
        assert history._attempt_change(old, new, words=True, lang="zh")["summary"] == zh
        assert history._attempt_change(old, new, words=True, lang="en")["summary"] == en
    own, own_again = _version("成立 先看", "labelled", "成立", "先看"), _version("成立 再看", "labelled", "成立", "再看")
    assert history._attempt_change(own, own_again, words=True)["summary"] == "研究判断仍是「成立」，理由改了"
    assert history._attempt_change(own, own_again, words=True, lang="en")["summary"] == "Research assessment still “成立”; the reason changed"
    ticked = _version("✔ 稳健", "current", "现行", "稳健")
    assert history._attempt_change(_version("✅ 稳健", "current", "现行", "稳健"), ticked, words=True)["summary"] == "研究判断不变（「现行」）"
    # the command line's form is the markers, as before
    assert history._attempt_change(WAITING, WAITING_AGAIN)["summary"] == "🕒 等复查 → 🕒 看了数据，样本太小"
    assert history._attempt_change(WAITING, dict(WAITING))["summary"] == "研究判断不变（🕒 等复查）"


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_comparison_the_page_asks_for_says_the_reason_changed_for_a_real_row_edited_twice(tmp_path, lang):
    """A real project: #16 is scanned with 「🕒 先看」, then the person keeps 待定 and writes 「🕒 样本太小」, and the new result.
    GET /api/history/diff (`node_api.diff_payload`) says it in the page's language."""
    root = _project(tmp_path / "proj")
    (root / "map.md").write_text(_map("🕒 先看"))
    _scan(root)
    text = (root / "map.md").read_text(encoding="utf-8").replace("🕒 先看", "🕒 样本太小").replace("t=2.91", "t=1.80")
    (root / "map.md").write_text(text, encoding="utf-8")
    _scan(root)
    out = node_api.diff_payload(root, {"node": ["attempt:map.md#16"]}, lang)
    assert out["summary_words"] == _w(lang)("研究判断仍是「待定」，理由改了，结果改为：t=1.80",
                                           "Research assessment still “Pending”; the reason changed; new result: t=1.80")


# -- 3. 图表与论断 does not open on a box whose 「它」 has nothing to refer to -------------------------------------------------

REPORT = "research/results/" + "d" * 32 + ".md"
TABLE = "00_Current/replication_v9/Data/panel_clean.csv"


def _report_naming(root, *materials: str) -> str:
    """A report an agent wrote for a task, whose Section 20 context names these files as its materials (and says why)."""
    (root / "research" / "results").mkdir(parents=True, exist_ok=True)
    (root / REPORT).write_text("# Pricing check\n\nThe sample is small.\n", encoding="utf-8")
    (root / (REPORT.removesuffix(".md") + ".context.json")).write_text(json.dumps({"version": 1, "context": {
        "question": "Is the effect stable?", "materials": list(materials),
        "purposes": {path: "what the check read" for path in materials}}}), encoding="utf-8")
    return REPORT


def _with_conn(root, fn):
    from test_v8_phase2 import _conn

    conn = _conn(root)
    try:
        return fn(conn)
    finally:
        conn.close()


def test_a_figure_or_table_a_report_names_still_shows_that_report_on_its_own_node_page(tmp_path):
    """Before the box is taken off 图表与论断: the figure, the table and the data file the thesis's report lists as materials each
    show the report on their own page (`/api/node` carries `research_records`), with the file it names and what it is for -- and a
    file no report names shows none. So no report is reachable only through the page that lost its box."""
    from test_v16_provenance import FIG, _cite_table, thesis

    root = thesis(tmp_path / "thesis")
    _cite_table(root, TABLE)
    report = _report_naming(root, FIG["main"], TABLE)
    for node in (f"figure:{FIG['main']}", f"dataset:{TABLE}"):
        page = _with_conn(root, lambda conn: node_api.node_payload(conn, root, {"node": [node]}))
        assert [r["report"] for r in page["research_records"]] == [report], node
        record = page["research_records"][0]
        assert record["title"] == "Pricing check" and record["question"] == "Is the effect stable?"
        assert [m["purpose"] for m in record["matches"]] == ["what the check read"]
    other = _with_conn(root, lambda conn: node_api.node_payload(conn, root, {"node": [f"figure:{FIG['stray']}"]}))
    assert other["research_records"] == []


def test_the_results_payload_carries_no_reports_and_does_not_read_them_for_the_page(tmp_path, monkeypatch):
    """GET /api/results answered a `research_records` list (the reports naming any figure or table of the page) for a box the page no
    longer draws; the key and the scan of up to 250 sidecars behind it are gone. A draft's claims never had one to use."""
    from researchos import research_records
    from test_v16_provenance import FIG, thesis

    root = thesis(tmp_path / "thesis")
    _report_naming(root, FIG["main"], TABLE)
    reads = []
    monkeypatch.setattr(research_records, "backlinks", lambda *a, **k: reads.append(1) or [])
    page = _with_conn(root, lambda conn: node_api.results_payload(conn, root, {}))
    draft = _with_conn(root, lambda conn: node_api.results_payload(conn, root, {"draft": ["00_Current/Beyond_Persistence_v9_reader.md"]}))
    assert "research_records" not in page and "research_records" not in draft
    assert reads == [] and page["items"]            # the page is still the figures and tables


_RESULTS_PAGE = r"""
window.ROWorkspace = { renderResearchLinks: (target, records, label) => { drawn.push([records && records.length, label || null]); } };
global.drawn = [];
global.apiGet = async (path) => { if (path === "/api/results") return %(data)s; throw new Error("no fixture for " + path); };
(async () => {
  const host = el("div", "host"); views.results = host;
  await window.RONode.loadResults(host, false);
  process.stdout.write(JSON.stringify({ drawn, intro: byClass(host, "node-sub").map(text).slice(0, 1), blocks: byClass(host, "res-block").length }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_results_page_opens_on_its_own_sentence_and_the_papers_draft_not_on_a_box_of_reports(lang, tmp_path):
    """The page of a project whose report names eleven files: the data the page is given may still carry `research_records` (a server
    of the earlier build), and the page draws no 「提到它的笔记与报告」 box at its top -- it opens on its own sentence."""
    from test_v16_provenance import FIG, _results, thesis
    from test_v16_provenance_js import _json, _run

    root = thesis(tmp_path / "thesis")
    data = _results(root)
    data["research_records"] = [{"report": REPORT, "title": "Pricing check", "question": "Is the effect stable?",
                                 "matches": [{"path": FIG["main"], "kind": "materials", "purpose": "x"}], "variables": []}]
    out = _run(_RESULTS_PAGE % {"data": _json(data)}, language=None if lang == "zh" else lang, view="results")
    assert out["drawn"] == []
    assert out["intro"] and out["blocks"] >= 2


def test_the_boxes_that_have_a_subject_stay_where_they_are():
    """「提到它的笔记与报告」 stays on a node page (node.js), on a variable card and 「提到这个文件的笔记与报告」 on 文献 (app.html): each has
    one thing it is about. node.js draws it once, for the node page."""
    node_js = (WEBAPP / "node.js").read_text(encoding="utf-8")
    app = (WEBAPP / "app.html").read_text(encoding="utf-8")
    assert node_js.count("renderResearchLinks(") == 1 and "renderResearchLinks(pageEl, page.research_records)" in node_js
    assert "renderResearchLinks(related, result.records)" in app
    assert 'renderResearchLinks(source, (data.research_records_by_file || {})[file], t("提到这个文件的笔记与报告"))' in app


# -- 4. every mark stands on the page ---------------------------------------------------------------------------------------

OVERVIEW, FRAMEWORK, FACTORS, GONE = ("research/overview.md", "research/framework.md", "research/design/factor_candidates.md",
                                      "research/design/old_sketch.txt")


def _quant(tmp_path):
    """Quant's papers.toml in miniature: three drafts marked in the app -- the project's 研究问题与计划 (which exists and has a
    heading), a framework and a design note in a folder of its own -- and a fourth mark, a plain-text sketch the index never read, whose file has since gone. The thesis fixture
    stands for the rest of the project (its drafts, figures and attempt table)."""
    from researchos.records import papers
    from test_v16_provenance import _write, thesis
    from test_v8_phase2 import _scan

    root = thesis(tmp_path / "thesis")
    _write(root, OVERVIEW, "# 研究问题与计划\n\n## 问题\n\nWhy do the estimates move with the window?\n")
    _write(root, FRAMEWORK, "# Framework\n\n## Idea\n\nThe effect is 0.42 in the panel.\n")
    _write(root, FACTORS, "# Factor candidates\n\n## One\n\nFirst candidate.\n")
    _write(root, GONE, "A sketch kept as plain text.\n")
    _scan(root)
    for file in (OVERVIEW, FRAMEWORK, FACTORS, GONE):
        papers.set_marked(root, file, True, via="app")
    (root / GONE).unlink()
    _scan(root)
    return root


def _drafts(root) -> dict[str, dict]:
    from test_v16_provenance import _results

    return {d["file"]: d for d in _results(root)["drafts"]}


def test_the_index_does_not_read_the_writing_desks_own_overview_but_does_read_a_framework_and_a_design_note(tmp_path):
    """Why 图表与论断 lists two of three marks: `research/overview.md` is a file Research OS itself writes (the project's 研究问题与计划,
    DESIGN 18.3), so the scan skips it as paper prose on purpose -- no section, no claim -- and the index holds nothing of it; the mark
    was accepted all the same (`set_marked` asks only that the file is the project's, and an index built before the rule offered it).
    The other two are the researcher's own Markdown in `research/`, which the scan reads."""
    from researchos import nodepage
    from researchos.ingest import mdpaper

    root = _quant(tmp_path)
    assert mdpaper._is_research_own(OVERVIEW) and not mdpaper._is_research_own(FRAMEWORK) and not mdpaper._is_research_own(FACTORS)
    read = _with_conn(root, nodepage._draft_files)
    assert OVERVIEW not in read and {FRAMEWORK, FACTORS} <= read and GONE not in read


def test_every_mark_stands_on_the_page_so_the_count_in_the_records_panel_and_the_papers_list_agree(tmp_path):
    """Quant: the panel 你的记录与备份 counts four marks (three that stand and one whose file went) and 图表与论断's `drafts` held two.
    A marked file the index does not read is a row of its own now: its name, no claim, and the mark -- so it can be taken back."""
    from researchos import inventory

    root = _quant(tmp_path)
    drafts = _drafts(root)
    marked = {file for file, d in drafts.items() if d["marked"]}
    assert marked == {OVERVIEW, FRAMEWORK, FACTORS, GONE}
    row = {r.code: r for r in inventory.inventory(None, root)}["papers"]
    assert row.facts["marked"] == len(marked) == 4
    assert drafts[OVERVIEW] == {"file": OVERVIEW, "marked": True, "claims": 0, "node": "file:" + OVERVIEW,
                                "modified": drafts[OVERVIEW]["modified"], "exists": True}
    assert drafts[OVERVIEW]["modified"]            # the day the file was changed, as for any draft
    assert drafts[FRAMEWORK]["claims"] == 1 and drafts[FRAMEWORK]["exists"] is True


def test_a_marked_file_that_is_gone_from_the_project_says_so_and_can_still_be_unmarked(tmp_path):
    """A mark whose file no longer exists: the row exists with no day and `exists` false (the page says it in one quiet sentence and
    does not link a name that opens nothing); it is not offered to choose; unmarking it needs no file, and the row is gone."""
    from researchos.records import papers
    from test_v16_provenance import _results

    root = _quant(tmp_path)
    gone = _drafts(root)[GONE]
    assert gone["exists"] is False and gone["modified"] is None and gone["claims"] == 0 and gone["marked"] is True
    page = _results(root)
    assert GONE not in page["papers"]["recent"] and GONE in page["papers"]["marked"]
    done = papers.set_marked(root, GONE, False, via="app")
    assert done["changed"] is True and GONE not in done["drafts"]
    assert GONE not in _drafts(root)
    # a mark can go without the file ever having been read, and the one that stands can be taken back too
    papers.set_marked(root, OVERVIEW, False, via="app")
    assert OVERVIEW not in _drafts(root) and {FRAMEWORK, FACTORS} <= set(_drafts(root))


def test_a_draft_marked_and_deleted_while_the_index_still_holds_it_is_listed_once_and_says_it_is_gone(tmp_path):
    """The index has not scanned since the file went: it still reads its sections, so the row is the indexed one (its claims kept) and
    `exists` is false, as `modified` already was for such a draft."""
    from researchos.records import papers

    root = _quant(tmp_path)
    (root / FRAMEWORK).unlink()
    drafts = _drafts(root)
    assert drafts[FRAMEWORK]["exists"] is False and drafts[FRAMEWORK]["modified"] is None and drafts[FRAMEWORK]["claims"] == 1
    assert [d["file"] for d in _drafts_list(root)].count(FRAMEWORK) == 1
    assert papers.marked(root)["drafts"].count(FRAMEWORK) == 1


def _drafts_list(root) -> list[dict]:
    from test_v16_provenance import _results

    return _results(root)["drafts"]


def test_the_other_lists_of_the_page_take_a_marked_unread_file_in_their_stride(tmp_path):
    """`_day`, `where`, `recent`, the nodes and the claims, with a marked file the index does not read: the day is the file's own (none
    for a gone one); a file of a name another draft has carries its folder (`where`); the picker still offers neither a marked file nor
    the attempt table; the node is the file's node id; the claims of the file are none, asked for or listed, also when it is gone."""
    from test_v16_provenance import _results, _write
    from test_v8_phase2 import _scan
    from researchos.records import papers

    root = _quant(tmp_path)
    _write(root, "00_Current/overview.md", "# Overview\n\n## Alone\n\nText.\n")
    _scan(root)
    page = _results(root)
    assert page["where"]["drafts"][OVERVIEW] == "research" and page["where"]["drafts"]["00_Current/overview.md"] == "00_Current"
    assert not {OVERVIEW, FRAMEWORK, FACTORS, GONE, "map.md"} & set(page["papers"]["recent"]) and "00_Current/overview.md" in page["papers"]["recent"]
    assert {f: d["node"] for f, d in _drafts(root).items() if f in (OVERVIEW, GONE)} == {OVERVIEW: "file:" + OVERVIEW, GONE: "file:" + GONE}
    assert [c["file"] for c in page["claims"]] == [FRAMEWORK]          # claims are listed for a marked draft that holds some
    for file in (OVERVIEW, GONE):
        asked = _results(root, file)
        assert asked["total"] == 0 and asked["sections"] == []
    # the picker of a project with nothing marked still leaves the attempt table out
    for file in (OVERVIEW, FRAMEWORK, FACTORS, GONE):
        papers.set_marked(root, file, False, via="app")
    assert "map.md" not in _results(root)["papers"]["recent"]


def test_the_node_page_of_a_marked_file_the_index_does_not_read_offers_to_unmark_it(tmp_path):
    """The file's own page: a draft the index reads offers 「设为论文正文」 and the marked file the index does not read offers to take the
    mark back; an unread file nobody marked offers nothing; a file that is gone has no page (the list does not link it)."""
    from researchos import nodepage
    from researchos.records import papers
    from test_v16_provenance import _write

    root = _quant(tmp_path)
    _write(root, "research/unmarked_note_for_nothing.txt", "x")
    page = lambda node: _with_conn(root, lambda conn: nodepage.node(conn, root, node))  # noqa: E731
    assert page("file:" + OVERVIEW)["paper"] == {"draft": OVERVIEW, "marked": True}
    assert page("file:" + FRAMEWORK)["paper"] == {"draft": FRAMEWORK, "marked": True}
    assert "paper" not in page("file:research/unmarked_note_for_nothing.txt")
    with pytest.raises(nodepage.NotFound):
        page("file:" + GONE)
    papers.set_marked(root, OVERVIEW, False, via="app")
    assert "paper" not in page("file:" + OVERVIEW)


_PAPER_ROWS = r"""
(async () => {
  await window.RONode.loadResults(host, false);
  const out = {};
  const list = byClass(host, "res-paper-list")[0];
  const rows = list.all().filter((e) => e.cls.has("res-row"));
  const view = (r) => ({ name: text(byClass(r, "mono")[0]), tag: byClass(r, "mono")[0].tag, link: byClass(r, "mono")[0].cls.has("node-link"),
    claims: text(byClass(r, "node-sub-inline")[0]), gone: byClass(r, "res-gone").map(text),
    buttons: r.all().filter((k) => k.tag === "BUTTON" && k.cls.has("btn")).map(text) });
  out.rows = rows.map(view);
  out.claimDrafts = byClass(host, "res-draft").map((d) => text(d.kids[0]));
  out.picker = byClass(host, "res-picker-row").map((r) => text(r.kids[0]));
  // opening the unread file's name opens its page; unmarking the gone one writes the same record as any other
  const opened = [];
  window.RONode._open = opened;
  const unread = rows.find((r) => text(byClass(r, "mono")[0]) === %(overview)s);
  const goneRow = rows.find((r) => text(byClass(r, "mono")[0]) === %(gone)s);
  out.goneHasClick = (byClass(goneRow, "mono")[0].on.click || []).length;
  buttons(goneRow, %(unset)s)[0].click();
  await settle(); await settle();
  out.posted = posted.slice();
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_paper_list_shows_a_row_for_every_mark_and_the_unread_and_the_gone_ones_are_told_apart(tmp_path, lang):
    """图表与论断 on Quant's project in both languages: four rows in 论文正文, each with its name and 「n 条论断」 and the way to unmark
    it; the file the index does not read has a name that opens its page and 「0 条论断」; the gone one has a name that is text, one quiet
    sentence, and the same button, which writes the same record. The claims block lists only the files that are there."""
    from test_v16_paper_rows_js import _page_run

    w = _w(lang)
    root = _quant(tmp_path)
    data = _with_conn(root, lambda conn: node_api.results_payload(conn, root, {}))
    # nothing needs the cause here: the payload is the engine's own
    out = _page_run([data], _PAPER_ROWS % {"overview": json.dumps(OVERVIEW), "gone": json.dumps(GONE),
                                           "unset": json.dumps(w("不再作为论文正文", "Unset as the paper"))}, language=None if lang == "zh" else lang)
    by_name = {row["name"]: row for row in out["rows"]}
    assert sorted(by_name) == sorted([OVERVIEW, FRAMEWORK, FACTORS, GONE])
    unset = [w("不再作为论文正文", "Unset as the paper")]
    assert by_name[OVERVIEW] == {"name": OVERVIEW, "tag": "BUTTON", "link": True, "claims": w("0 条论断", "0 claims"), "gone": [], "buttons": unset}
    assert by_name[FRAMEWORK]["claims"] == w("1 条论断", "1 claim") and by_name[FRAMEWORK]["link"] is True and by_name[FRAMEWORK]["gone"] == []
    assert by_name[GONE] == {"name": GONE, "tag": "SPAN", "link": False, "claims": w("0 条论断", "0 claims"),
                             "gone": [w("这个文件现在不在项目里", "This file is no longer in the project")], "buttons": unset}
    assert out["goneHasClick"] == 0                         # a name that opens nothing is not a link
    assert len(out["rows"]) == 4
    assert len(out["claimDrafts"]) == 3 and not any(GONE in name for name in out["claimDrafts"])   # 论断 expands only the files that are there
    assert any(name.startswith(OVERVIEW) for name in out["claimDrafts"])
    assert out["posted"] == [["/api/papers", {"draft": GONE, "marked": False}]]

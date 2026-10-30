"""Research OS V16, task B3-2 (DESIGN.md 21.5, 21 intro decision 5): the paper is chosen once, 图表与论断 puts it first,
「不属于论文」 takes a by-product out of the paper's open work, and the kind pages list by kind with each row's attempt.
The engine side, on the thesis in miniature of test_v16_provenance (one person, attempts kept by hand, no task) with a
few drafts more, and on a task that registered files under an attempt. Every test names its scenario."""

from __future__ import annotations

import json
import os
import tomllib
from datetime import datetime
from pathlib import Path

import pytest

from researchos import nodepage, now_doing, outputs, people, workspace
from researchos.records import papers
from researchos.records.identity import read_identity

from test_v16_provenance import (
    CHECK, DRAFT, FIG, HAND, MAP, OTHER_DRAFT, PKG, RAW, WORD1, WORD3, _answer, _choose, _cite_table, _data_code, _items,
    _page, _results, _with, _write, thesis,
)
from test_v8_1213 import drive  # noqa: F401 -- the two-person fixture
from test_v8_phase2 import _scan
from test_v8_phase4 import MEMBER, PI

LOOSE = "00_Current/loose_note.md"
A1, A2 = "attempt:map.md#1", "attempt:map.md#2"
MAP_TWO = MAP + "| 2 | 07-27 | Agent plots | x | r2 | 🕒 |\n"
REPORT = "research/results/" + "c" * 32 + ".md"
SVG, VECTOR = "00_Current/figs/hand_copy.svg", "00_Current/figs/vector.pdf"
EXTRA_SCRIPT, EXTRA_FIG = f"{PKG}/code/05-extra.py", f"{PKG}/current/Plots/fig_extra.png"


def _day(root: Path, rel: str, day: int, month: int = 10) -> None:
    """The file was last changed at noon on that day of 2026 (local time)."""
    when = datetime(2026, month, day, 12, 0).timestamp()
    os.utime(root / rel, (when, when))


@pytest.fixture
def root(tmp_path) -> Path:
    """The thesis in miniature, a vector and an svg copy among the paper's figures, a figure only a script made, and a
    second attempt in the table."""
    root = thesis(tmp_path / "thesis")
    _write(root, VECTOR, b"%PDF vector")
    _write(root, SVG, b"<svg/>")
    _write(root, DRAFT, (root / DRAFT).read_text(encoding="utf-8")
           + "\n![Vector](figs/vector.pdf)\n\n![Copy](figs/hand_copy.svg)\n")
    _write(root, EXTRA_SCRIPT, "import pandas as pd\nimport matplotlib.pyplot as plt\n" + 'df = pd.read_csv("../Data/panel_clean.csv")\n'
           + 'plt.savefig("../current/Plots/fig_extra.png")\n')
    _write(root, EXTRA_FIG, b"\x89PNG extra")
    _write(root, "map.md", MAP_TWO)
    _scan(root)
    return root


def _ids(page: dict) -> list[str]:
    return [i["id"] for key in ("items", "other", "loose", "excluded") for i in page[key]]


def _listed(page: dict, key: str) -> list[str]:
    return [i["path"] for i in page[key]]


# -- 设为论文正文 is one control: the picker (21.5) -----------------------------------------------------------


@pytest.fixture
def drafts(root) -> dict[str, str]:
    """Six more drafts and the three of the thesis, each last changed on a day of its own: the order is the one of the
    days, not of the names."""
    days = {LOOSE: 10, DRAFT: 8, OTHER_DRAFT: 7}  # and the attempt table's own file, which the index reads as a draft too
    for n in range(1, 7):
        rel = f"00_Current/drafts/d{n}.md"
        _write(root, rel, f"# Draft {n}\n\n## Section\n\nThe effect {n} is 0.{n}1 in the panel.\n")
        days[rel] = n
    _scan(root)
    for rel, day in days.items():
        _day(root, rel, day)
    _day(root, "map.md", 1, month=9)
    return {**{rel: f"2026-10-{day:02d}" for rel, day in days.items()}, "map.md": "2026-09-01"}


def test_the_picker_offers_the_drafts_changed_most_recently_first_with_five_shown_and_a_marked_one_leaves_it(root, drafts):
    page = _results(root)
    first, rest = page["papers"]["recent"][:5], page["papers"]["recent"][5:]
    assert page["papers"]["shown"] == 5
    assert first == [LOOSE, DRAFT, OTHER_DRAFT, "00_Current/drafts/d6.md", "00_Current/drafts/d5.md"]
    assert rest == [f"00_Current/drafts/d{n}.md" for n in (4, 3, 2, 1)]  # the rest, under 「更多」
    assert "map.md" not in page["papers"]["recent"] and "map.md" in [d["file"] for d in page["drafts"]]  # the attempt table is no draft to choose
    assert {d["file"]: d["modified"] for d in page["drafts"]} == drafts
    _choose(root, DRAFT)
    page = _results(root)
    assert DRAFT not in page["papers"]["recent"] and page["papers"]["recent"][0] == LOOSE and len(page["papers"]["recent"]) == 8
    assert page["papers"]["marked"] == [DRAFT]
    _day(root, "00_Current/drafts/d1.md", 20)  # changed again: now the newest
    assert _results(root)["papers"]["recent"][0] == "00_Current/drafts/d1.md"


def test_a_draft_the_disk_no_longer_has_comes_last_in_the_picker_with_no_day(root, drafts):
    (root / "00_Current/drafts/d3.md").unlink()
    page = _results(root)
    assert page["papers"]["recent"][-1] == "00_Current/drafts/d3.md"
    assert next(d for d in page["drafts"] if d["file"] == "00_Current/drafts/d3.md")["modified"] is None


# -- the paper first, the other drafts after, a figure of two drafts one row (21.5) -----------------------------


def test_with_no_draft_chosen_every_cited_figure_is_the_papers_and_a_figure_two_drafts_include_is_one_row_naming_both(root):
    page = _results(root)
    assert page["other"] == [] and page["papers"]["marked"] == []
    ids = _ids(page)
    assert len(ids) == len(set(ids))  # one row per figure, however many sections cite it
    word = next(i for i in page["items"] if i["path"] == WORD1)
    assert word["drafts"] == sorted([DRAFT, OTHER_DRAFT]) and word["in_paper"]
    stray = next(i for i in page["items"] if i["path"] == FIG["stray"])
    assert stray["drafts"] == sorted([LOOSE, OTHER_DRAFT])
    assert _listed(page, "loose") == [EXTRA_FIG]  # the figure no draft cites
    assert page["where"]["drafts"] == {}  # no two drafts of one name


def test_once_the_paper_is_chosen_its_figures_come_first_the_figures_of_the_other_drafts_stand_apart_and_the_loose_ones_last(root):
    _choose(root)
    page = _results(root)
    assert _listed(page, "items") == sorted([WORD1, "00_Current/main_assets/image2.png", HAND, SVG, VECTOR, FIG["main"], FIG["needs"]])
    assert _listed(page, "other") == sorted([WORD3, FIG["stray"]])  # only drafts the paper does not include cite them
    assert _listed(page, "loose") == [EXTRA_FIG] and page["excluded"] == []
    word = next(i for i in page["items"] if i["path"] == WORD1)  # cited by the paper's draft and by another: the paper's row, both named
    assert word["drafts"] == [DRAFT, OTHER_DRAFT]  # the paper's draft first
    assert [i["in_paper"] for i in page["other"]] == [False, False] and all(i["in_paper"] for i in page["items"])
    _choose(root, OTHER_DRAFT)  # the paper is main text and appendix: its figures are the paper's too
    page = _results(root)
    assert page["other"] == [] and WORD3 in _listed(page, "items") and FIG["stray"] in _listed(page, "items")
    assert len(_ids(page)) == len(set(_ids(page)))


def test_drafts_of_one_name_are_told_apart_by_their_folder(root):
    _write(root, "appendix/Beyond_Persistence_v9_reader.md", "# Results\n\n## Figures\n\n![Main](x.png)\n")
    _write(root, "appendix/x.png", b"\x89PNG x")
    _scan(root)
    where = _results(root)["where"]["drafts"]
    assert where == {DRAFT: "00_Current", "appendix/Beyond_Persistence_v9_reader.md": "appendix"}


def test_each_figure_row_says_whether_it_has_a_thumbnail_a_picture_only(root):
    page = _results(root)
    thumbs = {i["path"]: i["thumb"] for key in ("items", "loose") for i in page[key]}
    assert thumbs[FIG["main"]] is True and thumbs[WORD1] is True and thumbs[EXTRA_FIG] is True
    assert thumbs[VECTOR] is False and thumbs[SVG] is False  # the asset route serves png, jpeg, gif and webp
    big = root / "00_Current/figs/big.png"
    _write(root, "00_Current/figs/big.png", b"\x89PNG" + b"0" * (8 * 1024 * 1024))
    from researchos import kind_rows

    assert kind_rows.thumbnail(root, "00_Current/figs/big.png") is False and kind_rows.thumbnail(root, FIG["main"]) is True
    assert kind_rows.thumbnail(root, "00_Current/figs/gone.png") is False and kind_rows.thumbnail(root, None) is False  # not there: no broken image
    assert big.stat().st_size > kind_rows.THUMBNAIL_LIMIT


# -- 「不属于论文」 and 「放回论文」 (21.5) ------------------------------------------------------------------------------


def _entries(root: Path) -> list[dict]:
    return tomllib.loads(papers.papers_path(root).read_text(encoding="utf-8"))["paper"]


def test_putting_a_by_product_out_of_the_paper_and_back_is_an_entry_each_in_the_event_log_and_moves_its_row(root, monkeypatch):
    monkeypatch.setattr(people, "current_member_id", lambda: PI[1])  # the researcher's own name on what is written
    _choose(root)
    before = _results(root)
    assert next(i for i in before["items"] if i["path"] == FIG["needs"])["trace"]["tone"] == "warning"
    warned = [i["path"] for i in before["items"] if i["trace"]["tone"] == "warning"]
    assert FIG["needs"] in warned and FIG["main"] not in warned and before["counts"] == {"open": len(warned), "other": 2, "loose": 1, "excluded": 0}
    out = papers.set_not_in_paper(root, FIG["needs"], True, via="app")
    assert out["changed"] and out["not_in_paper"] and out["files"] == [FIG["needs"]]
    after = _results(root)
    assert FIG["needs"] not in _listed(after, "items") and _listed(after, "excluded") == [FIG["needs"]]
    row = after["excluded"][0]
    assert row["in_paper"] is False and row["trace"]["tone"] == "quiet" and row["trace"]["state"] == "no_source"
    assert row["paper"]["out"] is True and row["paper"]["at"] and row["paper"]["by"] is None  # one person: nobody to name
    assert after["counts"]["open"] == before["counts"]["open"] - 1 and after["counts"]["excluded"] == 1
    # the file is the history: an act per change, in the form of the marks (id, seq, at, by, via)
    marks, outs = _entries(root)[0], _entries(root)[1]
    assert (marks["act"], marks["draft"]) == ("mark", DRAFT) and "file" not in marks
    assert (outs["act"], outs["file"], outs["via"]) == ("not_in_paper", FIG["needs"], "app") and "draft" not in outs
    assert outs["id"].startswith("pp-") and outs["seq"] == 2 and outs["at"] and outs["by"] == PI[1] and marks["by"] == PI[1]
    # nothing on disk moved, and the draft is still the paper
    assert (root / FIG["needs"]).read_bytes() == b"\x89PNG needs" and papers.marked(root)["drafts"] == [DRAFT]
    assert papers.marked(root)["not_in_paper"][0]["file"] == FIG["needs"]
    # again: nothing appended
    assert papers.set_not_in_paper(root, FIG["needs"], True, via="app")["changed"] is False and len(_entries(root)) == 2
    # 放回论文: the next entry; the figure is the paper's again, as it was
    back = papers.set_not_in_paper(root, FIG["needs"], False, via="app")
    assert back["changed"] and back["files"] == []
    assert [(e["act"], e.get("file") or e.get("draft"), e["seq"]) for e in _entries(root)] == [
        ("mark", DRAFT, 1), ("not_in_paper", FIG["needs"], 2), ("back_in_paper", FIG["needs"], 3)]
    restored = _results(root)
    assert restored["excluded"] == [] and restored["counts"] == before["counts"]
    assert next(i for i in restored["items"] if i["path"] == FIG["needs"])["trace"]["tone"] == "warning"


def test_a_figure_put_out_of_the_paper_is_quiet_wherever_it_stood_and_stays_where_the_person_can_put_it_back(root):
    # it was a figure of a draft the paper does not include, one only a script made, and one the paper includes
    _choose(root)
    for path in (FIG["stray"], EXTRA_FIG, SVG):
        papers.set_not_in_paper(root, path, True, via="app")
    page = _results(root)
    assert sorted(_listed(page, "excluded")) == sorted([FIG["stray"], EXTRA_FIG, SVG])
    assert FIG["stray"] not in _listed(page, "other") and EXTRA_FIG not in _listed(page, "loose") and SVG not in _listed(page, "items")
    assert all(i["trace"]["tone"] in ("quiet", "ok") for i in page["excluded"])
    # every figure and table row carries the control, whichever list it is in
    assert all("out" in i["paper"] for key in ("items", "other", "loose", "excluded") for i in page[key])
    # the file went away: it is still listed, to be put back
    (root / SVG).unlink()
    gone = next(i for i in _results(root)["excluded"] if i["path"] == SVG)
    assert gone["exists"] is False and gone["paper"]["out"] is True and gone["thumb"] is False
    assert papers.set_not_in_paper(root, SVG, False, via="app")["changed"]
    with pytest.raises(papers.PaperRefused) as refused:
        papers.set_not_in_paper(root, "no/such.png", True, via="app")
    assert refused.value.code == "no_such_file"
    with pytest.raises(papers.PaperRefused):
        papers.set_not_in_paper(root, "../outside.png", True, via="app")
    with pytest.raises(papers.PaperRefused):
        papers.set_not_in_paper(root, ".researchos/papers.toml", True, via="app")


def test_the_open_work_of_the_papers_tables_follows_the_entry_on_数据与代码(root):
    """A data file the paper includes as a table and no script writes is 缺生成脚本, the warning, and counts in the
    filter; put out of the paper it is a download like the others (quiet 没有生成脚本), counted there, and put back it counts
    again."""
    _cite_table(root, RAW)
    _choose(root)
    files = {f["path"]: f for f in _data_code(root)["files"]}
    assert files[RAW]["no_generator"] == "warning" and files[RAW]["paper"] == {"out": False}
    assert "paper" not in files[f"{PKG}/raw/fallback/panel_pricing.csv"]  # not the paper's: no control on 数据与代码
    counts = _data_code(root)["counts"]
    assert (counts["no_generator"], counts["raw"]) == (1, 2)
    papers.set_not_in_paper(root, RAW, True, via="app")
    files = {f["path"]: f for f in _data_code(root)["files"]}
    assert files[RAW]["no_generator"] == "quiet" and files[RAW]["paper"] == {"out": True} and files[RAW]["in_paper"] is False
    counts = _data_code(root)["counts"]
    assert (counts["no_generator"], counts["raw"]) == (0, 3)
    page = _results(root)
    assert RAW in _listed(page, "excluded")  # a table of the paper's draft, put out
    papers.set_not_in_paper(root, RAW, False, via="app")
    assert _data_code(root)["counts"]["no_generator"] == 1


def test_a_figures_own_page_says_it_was_put_out_and_stops_calling_what_it_lacks_a_warning(root):
    _choose(root)
    papers.set_not_in_paper(root, FIG["needs"], True, via="app")
    page = _page(root, f"figure:{FIG['needs']}")
    assert page["in_paper"] is False and page["tone"] == "quiet" and page["paper_item"] == {"file": FIG["needs"], "out": True}
    other = _page(root, f"figure:{FIG['main']}")
    assert other["paper_item"] == {"file": FIG["main"], "out": False}
    assert "paper_item" not in _page(root, f"script:{CHECK}") and "paper_item" not in _page(root, f"file:{DRAFT}")


def test_a_damaged_papers_file_puts_nothing_out_and_says_why(root):
    papers.set_not_in_paper(root, FIG["needs"], True, via="app")
    papers.papers_path(root).write_text("[[paper]\nbroken", encoding="utf-8")
    page = _results(root)
    assert page["excluded"] == [] and page["papers"]["message"]
    with pytest.raises(papers.PaperRefused) as refused:
        papers.set_not_in_paper(root, FIG["needs"], False, via="app")
    assert refused.value.code == "untrusted"


def test_the_endpoint_writes_both_kinds_of_entry_and_refuses_a_body_that_is_neither(root):
    from test_v8_phase2 import _call
    from test_v8_phase3 import _serve

    with _serve(root) as base:
        status, got = _call(f"{base}/api/papers", "POST", {"file": FIG["needs"], "not_in_paper": True})
        assert status == 200 and got["changed"] and got["files"] == [FIG["needs"]] and isinstance(got["generation"], int)
        status, got = _call(f"{base}/api/papers", "POST", {"draft": DRAFT, "marked": True})
        assert status == 200 and got["drafts"] == [DRAFT]
        assert _call(f"{base}/api/papers", "POST", {"file": FIG["needs"]})[0] == 400
        assert _call(f"{base}/api/papers", "POST", {"file": FIG["needs"], "not_in_paper": True, "draft": DRAFT})[0] == 400
        assert _call(f"{base}/api/papers", "POST", {"file": "no/such.png", "not_in_paper": True})[0] == 404
        status, results = _call(f"{base}/api/results")
        assert status == 200 and _listed(results, "excluded") == [FIG["needs"]] and "outputs" not in results
        status, back = _call(f"{base}/api/papers", "POST", {"file": FIG["needs"], "not_in_paper": False})
        assert status == 200 and back["files"] == []


def test_a_members_not_in_paper_travels_with_the_sync_and_is_taken_at_the_publish_like_the_mark(drive):  # noqa: F811
    """12.4: not a proposal that waits for approval -- the same way 设为论文正文's mark goes: written in the working copy,
    carried by the next sync as a packet entry, appended to the official file at the PI's next publish with the member's
    own name on it; the file's next reader sees the figure out of the paper."""
    from researchos import cli, share

    with drive.member():
        assert cli.main(["ingest", str(drive.work)]) == 0
        papers.set_not_in_paper(drive.work, "figs/fig1.png", True, via="app")
        preview = share.sync_preview(drive.work)
        assert preview["sending"]["papers"] == 1
        share.sync(drive.work)
    with drive.pi():
        assert share.status(drive.official)["incoming"]["papers"] == 1
        share.publish(drive.official)
        entries = tomllib.loads(papers.papers_path(drive.official).read_text(encoding="utf-8"))["paper"]
        assert [(e["act"], e["file"], e["by"], e["via"]) for e in entries] == [("not_in_paper", "figs/fig1.png", MEMBER[1], "app")]
        assert entries[0]["recovered_from"].startswith("pp-")
        assert [e["file"] for e in papers.marked(drive.official)["not_in_paper"]] == ["figs/fig1.png"]
    with drive.member():
        papers.set_not_in_paper(drive.work, "figs/fig1.png", False, via="app")
        share.sync(drive.work)
    with drive.pi():
        share.publish(drive.official)
        assert papers.marked(drive.official)["not_in_paper"] == []
        assert [e["act"] for e in tomllib.loads(papers.papers_path(drive.official).read_text(encoding="utf-8"))["paper"]] == [
            "not_in_paper", "back_in_paper"]


def test_a_team_names_who_put_a_figure_out(drive):  # noqa: F811
    with drive.pi():
        from researchos import cli

        assert cli.main(["ingest", str(drive.official)]) == 0
        papers.set_not_in_paper(drive.official, "figs/fig1.png", True, via="app")
        row = next(i for i in _results_for(drive.official)["excluded"] if i["path"] == "figs/fig1.png")
        assert row["paper"]["by"] == PI[0]  # in a team, the name


def _results_for(root: Path) -> dict:
    return _with(root, lambda conn: nodepage.results(conn, root))


# -- the 任务输出 block goes; each row says its attempt (21.5, decision 5) -----------------------------------------


def _agent_work(root: Path, task_attempt: str | None = A2) -> str:
    """A task under `task_attempt` whose result record has these files registered: one the index knows as a figure the
    paper includes (and no attempt holds), one it knows as data, and four it holds no node for."""
    for rel, content in (("agent_out/fig_new.png", b"\x89PNG new"), ("agent_out/summary.csv", b"n\n1\n"),
                         ("agent_out/clean.py", b"print(1)\n"), ("agent_out/clean.csv", b"x\n1\n"), ("agent_out/report.md", b"# R\n")):
        _write(root, rel, content)
    (root / REPORT).parent.mkdir(parents=True, exist_ok=True)
    (root / REPORT).write_text("# Agent plots\n\nBody.\n", encoding="utf-8")
    tasks, revision = workspace.read_tasks(root)
    task = {"id": "t1", "text": "Draw the plots", "done": True, "owner": "", "due": "", "report": REPORT, "attempt": A2}
    workspace.save_tasks(root, {"tasks": tasks + [task], "revision": revision})
    if not task_attempt:  # a task of a project from before 21, which names no attempt: written as it was then
        saved = json.loads((root / workspace.TASKS_PATH).read_text(encoding="utf-8"))
        for each in saved:
            each.pop("attempt", None)
        (root / workspace.TASKS_PATH).write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    outputs.register_files(root, {"report": REPORT, "items": [
        {"kind": "figure", "path": HAND, "title": "Hand-drawn schematic"},
        {"kind": "figure", "path": "agent_out/fig_new.png", "title": "The new figure"},
        {"kind": "table", "path": "agent_out/summary.csv", "title": "Summary of the run"},
        {"kind": "code", "path": "agent_out/clean.py", "title": "Cleaning script"},
        {"kind": "data", "path": "agent_out/clean.csv", "title": "Cleaned data"},
        {"kind": "data", "path": f"{PKG}/Data/panel_clean.csv", "title": "The panel"},
        {"kind": "document", "path": "agent_out/report.md", "title": "The write-up"}]})
    return REPORT


def test_the_task_output_block_is_gone_from_both_payloads_and_a_registered_file_is_a_row_with_its_title(root):
    _agent_work(root)
    results, data = _results(root), _data_code(root)
    assert "outputs" not in results and "outputs" not in data
    rows = {i["path"]: i for key in ("items", "other", "loose", "excluded") for i in results[key]}
    assert rows[HAND]["title"] == "Hand-drawn schematic" and rows[HAND]["label"] == "hand.png"
    assert rows["agent_out/fig_new.png"]["title"] == "The new figure" and rows["agent_out/fig_new.png"]["type"] == "figure"
    table = rows["agent_out/summary.csv"]  # a registered table the index has no node for
    assert (table["kind"], table["type"], table["title"], table["exists"]) == ("表", "dataset", "Summary of the run", True)
    assert table["id"] == "dataset:agent_out/summary.csv" and table["trace"]["tone"] == "quiet"
    assert {"agent_out/fig_new.png", "agent_out/summary.csv"} <= set(_listed(results, "loose"))
    assert rows[FIG["main"]]["title"] is None  # not registered: no title of its own
    files = {f["path"]: f for f in data["files"]}
    assert files["agent_out/clean.py"]["title"] == "Cleaning script" and files["agent_out/clean.py"]["type"] == "script"
    assert files["agent_out/clean.csv"]["title"] == "Cleaned data" and files["agent_out/clean.csv"]["type"] == "dataset"
    assert files[f"{PKG}/Data/panel_clean.csv"]["title"] == "The panel" and files[f"{PKG}/code/01-build_data.py"]["title"] is None
    assert "agent_out/report.md" not in files and "agent_out/report.md" not in rows  # a document: seen in the handback, not by kind
    assert files["agent_out/clean.py"]["reads"] == [] and files["agent_out/clean.csv"]["read_by"] == []
    assert data["registered"] == {"error": None, "broken": []} and results["registered"] == {"error": None, "broken": []}


def test_a_row_names_the_attempt_of_the_task_whose_output_it_is_before_any_link_of_the_researchs(root):
    _agent_work(root)
    mappings_attach(root, HAND, A1)  # the researcher filed the same figure under #1 as well
    results, data = _results(root), _data_code(root)
    rows = {i["path"]: i for key in ("items", "other", "loose", "excluded") for i in results[key]}
    assert rows[HAND]["attempts"] == [A2]  # the task's, not the link
    assert rows["agent_out/fig_new.png"]["attempts"] == [A2] and rows["agent_out/summary.csv"]["attempts"] == [A2]
    assert results["attempt_labels"] == {A1: "#1 Plots (02)", A2: "#2 Agent plots"}
    files = {f["path"]: f for f in data["files"]}
    assert files["agent_out/clean.py"]["attempts"] == [A2] and files[f"{PKG}/Data/panel_clean.csv"]["attempts"] == [A2]
    assert data["attempt_labels"][A2] == "#2 Agent plots"


def mappings_attach(root: Path, path: str, attempt: str) -> None:
    from researchos.ingest import mappings as mappings_ingest

    mappings_ingest.add_mapping(root, path, attempt, "belongs_to")
    _scan(root)


def test_a_file_no_task_made_says_the_attempt_the_researcher_filed_it_under_and_one_with_neither_says_nothing(root):
    data = _data_code(root)
    files = {f["path"]: f for f in data["files"]}
    assert files[f"{PKG}/code/02-plots.py"]["attempts"] == [A1]  # the step number its attempt's description names
    assert files[f"{PKG}/code/00-notes.R"]["attempts"] == [A1]  # 归入尝试
    assert files[f"{PKG}/code/03-stray.py"]["attempts"] == [] and files[f"{PKG}/Data/unused.csv"]["attempts"] == []
    assert data["attempt_labels"] == {A1: "#1 Plots (02)"}
    rows = {i["path"]: i for key in ("items", "other", "loose", "excluded") for i in _results(root)[key]}
    assert rows[FIG["main"]]["attempts"] == [A1] and rows[FIG["main"]]["trace"]["state"] == "complete"  # its script's attempt: the one its own page says
    assert rows[FIG["stray"]]["attempts"] == [] and rows[WORD1]["attempts"] == []  # a row lacking one says nothing (a warning only for the paper's: provenance's)


def test_a_task_that_belongs_to_no_attempt_leaves_its_outputs_to_the_attempt_its_research_context_links_then_to_the_files_link(root):
    _agent_work(root, task_attempt=None)  # no attempt: a task of an older project
    tasks = json.loads((root / workspace.TASKS_PATH).read_text(encoding="utf-8"))
    assert "attempt" not in next(t for t in tasks if t["id"] == "t1")
    rows = {i["path"]: i for key in ("items", "other", "loose", "excluded") for i in _results(root)[key]}
    assert rows["agent_out/fig_new.png"]["attempts"] == [] and rows[HAND]["attempts"] == []
    (root / REPORT.replace(".md", ".context.json")).write_text(json.dumps({"version": 1, "context": {"attempts": [A2]}}))
    rows = {i["path"]: i for key in ("items", "other", "loose", "excluded") for i in _results(root)[key]}
    assert rows["agent_out/fig_new.png"]["attempts"] == [A2] and rows[HAND]["attempts"] == [A2]  # the home's rule for a report (now_doing.placed_reports)


def test_several_attempts_are_listed_in_the_order_of_their_numbers(root):
    mappings_attach(root, f"{PKG}/code/03-stray.py", A2)
    mappings_attach(root, f"{PKG}/code/03-stray.py", A1)
    files = {f["path"]: f for f in _data_code(root)["files"]}
    assert files[f"{PKG}/code/03-stray.py"]["attempts"] == [A1, A2]


def test_a_catalog_that_cannot_be_read_costs_the_titles_and_says_so_the_pages_still_list(root):
    _agent_work(root)
    catalog = root / outputs.catalog_path(REPORT)
    catalog.write_text("not json", encoding="utf-8")
    results, data = _results(root), _data_code(root)
    assert results["registered"]["error"] and data["registered"]["error"] and results["registered"]["broken"] == [outputs.catalog_path(REPORT)]
    assert "agent_out/fig_new.png" not in _listed(results, "loose") and results["items"]
    assert next(i for i in results["items"] if i["path"] == HAND)["title"] is None and len(data["files"]) > 10


def test_a_request_reads_the_catalogs_the_tasks_and_the_links_once_and_only_when_the_rows_need_them(root, monkeypatch):
    reads = {"catalog": 0, "placed": 0, "members": 0}
    original_catalog, original_placed = outputs.catalog, now_doing.placed_reports
    from researchos import trace as trace_mod

    original_members = trace_mod.attempt_members

    def catalog(*args, **kwargs):
        reads["catalog"] += 1
        return original_catalog(*args, **kwargs)

    def placed(*args, **kwargs):
        reads["placed"] += 1
        return original_placed(*args, **kwargs)

    def members(*args, **kwargs):
        reads["members"] += 1
        return original_members(*args, **kwargs)

    monkeypatch.setattr(outputs, "catalog", catalog)
    monkeypatch.setattr(now_doing, "placed_reports", placed)
    monkeypatch.setattr(trace_mod, "attempt_members", members)
    _data_code(root)
    assert reads == {"catalog": 1, "placed": 0, "members": 1}  # nothing registered: the tasks are not read for the rows
    reads.update(catalog=0, placed=0, members=0)
    _results(root)
    assert reads == {"catalog": 1, "placed": 0, "members": 0}  # the figures' attempts are their traces'
    _agent_work(root)
    reads.update(catalog=0, placed=0, members=0)
    _results(root)
    _data_code(root)
    assert reads["catalog"] == 2 and reads["placed"] == 2  # once for each answer

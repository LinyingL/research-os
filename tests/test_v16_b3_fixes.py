"""Research OS V16, the fixes to task B3 (DESIGN.md 21.5, 21.6, 21.9; the review of B3-1 and B3-2). The engine side, one
test per finding, each on the thesis in miniature of test_v16_paper_rows. Every test names its scenario."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import pytest

from researchos import lineage, nodepage, outputs, provenance, workspace
from researchos.ingest import mappings as mappings_ingest
from researchos.records import papers

from test_v16_paper_rows import (  # noqa: F401 -- the scenario's fixtures
    A1, A2, EXTRA_FIG, EXTRA_SCRIPT, MAP_TWO, REPORT, _entries, mappings_attach, root,
)
from test_v16_provenance import (
    CHECK, DRAFT, FIG, HAND, INSTALL, PKG, RAW, _answer, _choose, _data_code, _files, _items, _page, _results, _with, _write,
)
from test_v8_phase2 import _scan


# -- a task's attempt is the attempt that holds its output ----------------------------------------------------


def _extra_work(root: Path) -> None:
    """The paper's draft cites fig_extra.png, which only 05-extra.py writes, and no attempt holds that script. A task
    under #2 then registers the figure and the script as its outputs."""
    _write(root, DRAFT, (root / DRAFT).read_text(encoding="utf-8") + "\n![Extra](replication_v9/current/Plots/fig_extra.png)\n")
    _scan(root)


def _register_extra(root: Path) -> None:
    (root / REPORT).parent.mkdir(parents=True, exist_ok=True)
    (root / REPORT).write_text("# Agent plots\n\nBody.\n", encoding="utf-8")
    tasks, revision = workspace.read_tasks(root)
    task = {"id": "t1", "text": "Draw the plots", "done": True, "owner": "", "due": "", "report": REPORT, "attempt": A2}
    workspace.save_tasks(root, {"tasks": tasks + [task], "revision": revision})
    outputs.register_files(root, {"report": REPORT, "items": [
        {"kind": "figure", "path": EXTRA_FIG, "title": "The extra plot"}, {"kind": "code", "path": EXTRA_SCRIPT, "title": "Extra script"}]})
    _scan(root)


def test_a_figure_a_task_registered_under_an_attempt_is_held_by_that_attempt_on_its_row_its_count_and_its_pages(root):
    _extra_work(root)
    before = _results(root)
    row = next(i for i in before["items"] if i["path"] == EXTRA_FIG)
    assert row["trace"]["state"] == "no_attempt" and row["trace"]["tone"] == "warning" and row["attempts"] == []
    _register_extra(root)
    after = _results(root)
    row = next(i for i in after["items"] if i["path"] == EXTRA_FIG)
    # the row names the attempt and so does its state: it cannot be both in #2 and in no attempt
    assert row["attempts"] == [A2]
    assert row["trace"]["state"] == "complete" and row["trace"]["tone"] != "warning" and row["trace"]["repair"] is None
    assert row["trace"]["causes"] == [] and after["counts"]["open"] == before["counts"]["open"] - 1
    # its own page and its script's say the same
    for node in (f"figure:{EXTRA_FIG}", f"script:{EXTRA_SCRIPT}"):
        trace = _page(root, node)["trace"]
        assert [a["id"] for a in trace["attempts"]] == [A2], node
        assert not [b for b in trace["breaks"] if b["kind"] == "no_attempt"], node
        assert {v["via"] for v in trace["attempts"][0]["via"]} == {"task"}  # the figure and its script both


# -- 关系图 draws what 列表 draws (21.5, 21.8) -------------------------------------------------------------------


def _canvas(root: Path, scope: str = "all") -> dict:
    from researchos.webapp import canvas

    return _with(root, lambda conn: canvas.build_canvas(conn, root, scope))


def test_the_relation_graph_leaves_out_a_file_the_researcher_answered_and_draws_a_download_without_the_warning_socket(root):
    """The phantom `environment/R_packages.lock.csv` only the two environment scripts' text named, once both are answered
    with 「是」, is no card of 关系图 (it is no row of 列表); the downloads in raw/ are orphan inputs as the lineage report
    says, and their socket is the warning's only when the chosen paper includes the table."""
    phantom = "dataset:environment/R_packages.lock.csv"
    assert any(n["id"] == phantom and n["missing"] for n in _canvas(root)["nodes"])
    _answer(root, CHECK)
    _answer(root, INSTALL)
    assert "environment/R_packages.lock.csv" not in _files(root)  # 列表 has stopped listing it
    page = _canvas(root)
    assert phantom not in {n["id"] for n in page["nodes"]}
    assert not [link for link in page["links"] if phantom in (link["src"], link["dst"])]
    near = _canvas(root, f"near:script:{CHECK}")  # the script's own neighbourhood draws the real file, not the phantom
    assert phantom not in {n["id"] for n in near["nodes"]} and f"dataset:{PKG}/environment/R_packages.lock.csv" in {n["id"] for n in near["nodes"]}
    # the report and the canvas still name the same orphans, and tell the same tone as 列表
    report = _with(root, lambda conn: lineage.build_lineage_report(conn, root))
    cards = {n["path"]: n for n in page["nodes"] if n["orphan_input"]}
    assert set(cards) == {o["path"] for o in report["orphans"]}
    assert RAW in cards and {c["orphan_tone"] for c in cards.values()} == {"quiet"}
    _choose(root)
    from test_v16_provenance import _cite_table

    _cite_table(root, RAW)
    cards = {n["path"]: n for n in _canvas(root)["nodes"] if n["orphan_input"]}
    assert cards[RAW]["orphan_tone"] == "warning" and cards[f"{PKG}/raw/fallback/panel_pricing.csv"]["orphan_tone"] == "quiet"
    assert _files(root)[RAW]["no_generator"] == "warning"  # 列表 says the same


# -- one entry answers one missing path of its name (21.5) --------------------------------------------------------


def _two_missing(tmp_path: Path) -> Path:
    """A script that names `panel.csv` in two folders, neither of which is there, and one file of that name elsewhere."""
    from researchos import project as project_identity

    root = tmp_path / "two"
    _write(root, "code/a.py", 'import pandas as pd\ndf = pd.read_csv("../v8/Data/panel.csv")\ndf2 = pd.read_csv("../v9/Data/panel.csv")\n')
    _write(root, "elsewhere/panel.csv", "id,x\n1,2\n")
    _write(root, "other/panel.csv", "id,x\n1,2\n")
    project_identity.init_project(root)
    _scan(root)
    return root


def _broken(root: Path) -> list[str]:
    row = _files(root)["code/a.py"]
    return [b["target"] for b in row.get("broken", [])]


def test_one_entry_answers_one_of_two_missing_paths_of_the_same_name_and_the_other_stays_broken_until_it_has_its_own(tmp_path):
    root = _two_missing(tmp_path)
    assert sorted(_broken(root)) == ["v8/Data/panel.csv", "v9/Data/panel.csv"]
    mappings_ingest.add_mapping(root, "elsewhere/panel.csv", "code/a.py", "reads")
    _scan(root)
    left = _broken(root)
    assert left == ["v9/Data/panel.csv"]  # the other path is not accounted for: still said, with its own offer
    report = _with(root, lambda conn: lineage.build_lineage_report(conn, root))
    assert [b["target"] for b in report["broken_links"]] == left and _data_code(root)["counts"]["broken"] == 1
    page = _page(root, "script:code/a.py")
    assert [b["target"] for b in page["trace"]["broken"]] == left
    candidates = [c["path"] for c in next(b for b in _files(root)["code/a.py"]["broken"])["candidates"]]
    assert candidates == ["other/panel.csv"]  # the file the first entry took is not offered again
    # a second entry, for another file of the name, answers it
    mappings_ingest.add_mapping(root, "other/panel.csv", "code/a.py", "reads")
    _scan(root)
    assert _broken(root) == [] and _data_code(root)["counts"]["broken"] == 0
    assert "v9/Data/panel.csv" not in _files(root)


def test_a_single_missing_path_is_answered_by_one_entry_as_it_was(tmp_path):
    from researchos import project as project_identity

    root = tmp_path / "one"
    _write(root, "code/a.py", 'import pandas as pd\ndf = pd.read_csv("../v8/Data/panel.csv")\n')
    _write(root, "elsewhere/panel.csv", "id,x\n1,2\n")
    project_identity.init_project(root)
    _scan(root)
    assert _broken(root) == ["v8/Data/panel.csv"]
    mappings_ingest.add_mapping(root, "elsewhere/panel.csv", "code/a.py", "reads")
    _scan(root)
    assert _broken(root) == [] and "v8/Data/panel.csv" not in _files(root)
    # the primitive on a plain dict of entries, which knows no rivals, is the rule it always was
    assert lineage.superseded({("c.py", "writes"): {"x/out.png"}}, "c.py", "writes", "y/out.png") is True


# -- a candidate is a file of the project (21.5) ------------------------------------------------------------------


def test_a_candidate_is_never_a_link_that_leaves_the_project(tmp_path):
    from researchos import provenance

    outside = tmp_path / "outside"
    _write(outside, "secret.csv", "x\n1\n")
    root = tmp_path / "proj"
    _write(root, "pkg/run.R", 'd <- read.csv("secret.csv")\n')
    _write(root, "other/secret.csv", "x\n2\n")
    (root / "pkg/secret.csv").symlink_to(outside / "secret.csv")
    (root / "pkg/linkdir").symlink_to(outside, target_is_directory=True)
    names = lineage.Names(root)
    for target in ("secret.csv", "linkdir/secret.csv"):
        found = provenance.candidates(root, "pkg/run.R", target, "reads", names)
        assert [c["path"] for c in found] == ["other/secret.csv"], target
        assert found[0]["package"] is False


# -- 「不属于论文」 is offered where it can be written (21.5) --------------------------------------------------------


def test_a_cited_figure_the_disk_no_longer_has_is_not_offered_the_button_that_would_be_refused(root):
    _choose(root)
    before = _items(root)[HAND]
    assert before["exists"] and before["paper"] == {"out": False}
    (root / HAND).unlink()
    row = _items(root)[HAND]
    assert row["exists"] is False and row["trace"]["tone"] == "warning" and "paper" not in row  # a gone file cannot be put out
    assert "paper_item" not in _page(root, f"figure:{HAND}")
    # one put out while it was there stays listed and can be put back
    papers.set_not_in_paper(root, FIG["needs"], True, via="app")
    (root / FIG["needs"]).unlink()
    gone = _items(root)[FIG["needs"]]
    assert gone["exists"] is False and gone["paper"]["out"] is True
    assert _page(root, f"figure:{FIG['needs']}")["paper_item"] == {"file": FIG["needs"], "out": True}


# -- an older Research OS still reads papers.toml (21.5) ----------------------------------------------------------------


def _older_reader(text: str):
    """papers.toml as the Research OS from before 「不属于论文」 reads it: the table `paper`, the acts mark and unmark, and
    the strictness of the ledger at any other act."""
    from researchos.records import ledger as ledger_mod

    older = ledger_mod.LedgerSchema(
        table="paper", id_prefix="pp-", act_field="act", acts=frozenset({"mark", "unmark"}), undo_act=None, key_fields=(),
        field_order=("id", "seq", "at", "by", "act", "draft", "via", "recovered_from", "recovered_at"), header="",
        validate=lambda entry: papers.clean_draft(entry.get("draft")))
    return ledger_mod.parse_ledger(text, older)


def test_a_file_put_out_of_the_paper_is_in_a_table_an_older_engine_passes_over_and_the_marks_stay_readable(root):
    _choose(root)
    papers.set_not_in_paper(root, FIG["needs"], True, via="app")
    papers.set_marked(root, "00_Current/loose_note.md", True, via="app")  # a mark after the put-out, in file order
    text = papers.papers_path(root).read_text(encoding="utf-8")
    assert "[[paper_file]]" in text and "not_in_paper" not in text.split("[[paper_file]]")[0]
    ledger = _older_reader(text)  # raises at an act it does not know: the whole file would be refused
    assert [e.get("draft") for e in ledger.entries] == [DRAFT, "00_Current/loose_note.md"]
    # this engine reads both tables, each counted in itself
    assert papers.marked(root)["drafts"] == [DRAFT, "00_Current/loose_note.md"]
    assert [e["file"] for e in papers.marked(root)["not_in_paper"]] == [FIG["needs"]]
    assert [e["seq"] for e in _entries(root)] == [1, 2] and [e["seq"] for e in tomllib.loads(text)["paper_file"]] == [1]


def test_a_file_that_only_holds_put_out_entries_is_trusted_and_one_table_damaged_refuses_the_other_too(root):
    papers.set_not_in_paper(root, FIG["needs"], True, via="app")  # no draft marked: the file is founded by the put-out
    got = papers.marked(root)
    assert got["trusted"] and got["message"] is None and [e["file"] for e in got["not_in_paper"]] == [FIG["needs"]]
    path = papers.papers_path(root)
    path.write_text(path.read_text(encoding="utf-8") + '\n[[paper_file]]\nid = "pf-x"\nseq = 2\nact = "mark"\nfile = "a.png"\n',
                    encoding="utf-8")
    damaged = papers.marked(root)
    assert damaged["trusted"] is False and damaged["message"] and damaged["drafts"] == []
    with pytest.raises(papers.PaperRefused) as refused:
        papers.set_marked(root, DRAFT, True, via="app")
    assert refused.value.code == "untrusted"


# -- the kind pages and the node page do not read more than they need (the task's speed rule) --------------------------


def test_a_node_page_with_a_broken_link_walks_the_project_once_until_the_next_scan(root, monkeypatch):
    walks = []
    real = lineage._scan_basenames
    monkeypatch.setattr(lineage, "_scan_basenames", lambda r: (walks.append(r), real(r))[1])
    lineage._WALKS.clear()
    first = _page(root, f"script:{CHECK}")["trace"]["broken"]
    assert first and first[0]["candidates"] and len(walks) == 1
    assert _page(root, f"script:{CHECK}")["trace"]["broken"] == first and len(walks) == 1  # the same scan: the walk is kept
    _scan(root)
    _page(root, f"script:{CHECK}")
    assert len(walks) == 2  # a new scan reads the project again


def test_the_researchers_entries_are_read_once_for_data_code_and_no_link_is_asked_about_when_there_are_none(root, monkeypatch):
    reads, asked = [], []
    real_entries, real_target = lineage.person_entries, lineage._target_path_and_type
    monkeypatch.setattr(lineage, "person_entries", lambda conn: (reads.append(1), real_entries(conn))[1])
    monkeypatch.setattr(lineage, "_target_path_and_type", lambda conn, node: (asked.append(node), real_target(conn, node))[1])
    _data_code(root)
    assert len(reads) == 1  # `data_code` hands them to the report
    # with none, a link is not asked whether an entry answers it
    asked.clear()
    assert _with(root, lambda conn: lineage.replaced_targets(conn, lineage.person_entries(conn))) == set() and asked == []
    _answer(root, CHECK)
    _answer(root, INSTALL)  # both scripts that named the file
    asked.clear()
    assert _with(root, lambda conn: lineage.replaced_targets(conn)) and asked

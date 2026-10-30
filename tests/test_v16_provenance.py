"""Research OS V16, task B3-1 (DESIGN.md 21.5): a warning appears only where it can be acted on, with the action
beside it. The engine side, on a project shaped like the researcher's thesis in miniature: a current replication
package with downloads in raw/, R scripts that name a package-relative file, older copies of the package beside it, a
paper chosen among its drafts, figures a Word file was taken apart into. Every test names its scenario."""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos import db, lineage, nodepage, provenance
from researchos import project as project_identity
from researchos.ingest import mappings as mappings_ingest
from researchos.records import papers

from test_v8_phase2 import _conn, _scan

PKG = "00_Current/replication_v9"
DRAFT = "00_Current/Beyond_Persistence_v9_reader.md"
OTHER_DRAFT = "00_Current/Beyond_Persistence_notes.md"
LOCK = f"{PKG}/environment/R_packages.lock.csv"
ATTEMPTS = (
    'file = "map.md"\nheading = "Attempt timeline"\nsteps_dir = "00_Current/replication_v9/code"\n'
    '[columns]\nid = "#"\ndate = "Date"\ndescription = "Path"\nvariables = "Variables"\n'
    'result = "Result"\nverdict = "Verdict"\n'
)
MAP = (
    "# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n"
    "| 1 | 07-26 | Plots (02) | x | t=2.91 | 🕒 |\n"
)
READ = 'df = pd.read_csv("{}")\n'
REVIEW = 'd <- read.csv("{}")\n'


def _write(root: Path, rel: str, text: str | bytes = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))


def thesis(root: Path) -> Path:
    """The thesis in miniature (see the module docstring): one person, one attempt kept by hand, no tasks."""
    csv = "id,x\n1,2\n"
    # the current package: downloads in raw/ (read, written by no script), a build, a plot, the environment scripts
    for name in ("bis_us_neer_monthly.csv", "fallback/panel_pricing.csv"):
        _write(root, f"{PKG}/raw/{name}", csv)
    _write(root, f"{PKG}/code/01-build_data.py",
           "import pandas as pd\n" + READ.format("../raw/bis_us_neer_monthly.csv") + READ.format("../raw/fallback/panel_pricing.csv")
           + 'df.to_csv("../Data/panel_clean.csv")\ndf.to_csv("../Data/unused.csv")\n')
    _write(root, f"{PKG}/Data/unused.csv", "id,x\n")
    _write(root, f"{PKG}/code/00-notes.R", "# nothing here reads or writes a file\n")
    _write(root, f"{PKG}/Data/panel_clean.csv", csv)
    _write(root, f"{PKG}/Data/diagnostics.csv", csv)
    _write(root, f"{PKG}/code/02-plots.py",
           "import pandas as pd\nimport matplotlib.pyplot as plt\n" + READ.format("../Data/panel_clean.csv")
           + 'plt.savefig("../current/Plots/fig_main.png")\n')
    _write(root, f"{PKG}/current/Plots/fig_main.png", b"\x89PNG main")
    _write(root, f"{PKG}/code/03-stray.py", "import pandas as pd\nimport matplotlib.pyplot as plt\n"
           + READ.format("../Data/panel_clean.csv") + 'plt.savefig("../current/Plots/fig_stray.png")\n')
    _write(root, f"{PKG}/current/Plots/fig_stray.png", b"\x89PNG stray")
    for name in ("check_R_environment.R", "install_R_packages.R"):
        _write(root, f"{PKG}/environment/{name}", REVIEW.format("environment/R_packages.lock.csv"))
    _write(root, LOCK, "package,version\nggplot2,3.5\n")
    # older copies of the package: the lock file again, a nearer one, and review scripts of one name
    _write(root, "00_Current/replication_v8/environment/R_packages.lock.csv", "package,version\n")
    _write(root, "Beyond_Persistence_1012/environment/R_packages.lock.csv", "package,version\n")
    _write(root, "Data and Code_20261012/environment/R_packages.lock.csv", "package,version\n")
    _write(root, "Data and Code_20261012/inputs/reference/shock_series.csv", csv)
    _write(root, "Beyond_Persistence_1012/inputs/reference/shock_series.csv", csv)
    _write(root, "Data and Code_20261012/code/02-freeze.R", REVIEW.format("inputs/reference/shock_series.csv"))
    for folder in ("交付_20260927_审查修订", "交付_20261006", ""):
        _write(root, f"{folder + '/' if folder else ''}复核诊断.R", REVIEW.format(f"{PKG}/Data/diagnostics.csv"))
    # figures a Word file was taken apart into; the paper and another draft
    _write(root, "00_Current/main_assets/image1.png", b"\x89PNG word1")
    _write(root, "00_Current/main_assets/image2.png", b"\x89PNG word2")
    _write(root, "00_Current/main_assets/image3.png", b"\x89PNG word3")
    _write(root, "00_Current/figs/hand.png", b"\x89PNG hand")
    # a figure whose script reads a file that is not there, and has no same-named file anywhere
    _write(root, f"{PKG}/code/04-needs.py", "import pandas as pd\nimport matplotlib.pyplot as plt\n" + READ.format("../Data/missing.csv")
           + 'plt.savefig("../current/Plots/fig_needs.png")\n')
    _write(root, f"{PKG}/current/Plots/fig_needs.png", b"\x89PNG needs")
    _write(root, DRAFT, "# Results\n\n## Figures\n\n![Main](replication_v9/current/Plots/fig_main.png)\n\n"
           "![Word](main_assets/image1.png)\n\n![Word two](main_assets/image2.png)\n\n![Hand](figs/hand.png)\n\n"
           "![Needs](replication_v9/current/Plots/fig_needs.png)\n\n## Claims\n\nThe first effect is 0.42 in the panel.\n\n"
           "The second effect is 0.17 in the panel.\n\nThe third effect is 0.93 in the panel.\n\nThe fourth effect is 0.58 in the panel.\n")
    _write(root, OTHER_DRAFT, "# Notes\n\n## Old\n\n![Stray](replication_v9/current/Plots/fig_stray.png)\n\n"
           "![Word again](main_assets/image1.png)\n\n![Word three](main_assets/image3.png)\n")
    _write(root, "00_Current/loose_note.md", "# Loose\n\n## Alone\n\n![x](replication_v9/current/Plots/fig_stray.png)\n")
    _write(root, "map.md", MAP)
    project_identity.init_project(root)
    _write(root, ".researchos/attempts.toml", ATTEMPTS)
    _scan(root)
    # a script the index knows only through an attempt: nothing it reads or writes was found
    mappings_ingest.add_mapping(root, f"{PKG}/code/00-notes.R", "map.md#1", "belongs_to")
    _scan(root)
    return root


@pytest.fixture
def root(tmp_path):
    return thesis(tmp_path / "thesis")


CHECK = f"{PKG}/environment/check_R_environment.R"
INSTALL = f"{PKG}/environment/install_R_packages.R"
RAW = f"{PKG}/raw/bis_us_neer_monthly.csv"
FALLBACK = f"{PKG}/raw/fallback/panel_pricing.csv"
WORD1, WORD3 = "00_Current/main_assets/image1.png", "00_Current/main_assets/image3.png"
FIG = {name: f"{PKG}/current/Plots/fig_{name}.png" for name in ("main", "stray", "needs")}
HAND = "00_Current/figs/hand.png"


def _with(root: Path, fn):
    conn = _conn(root)
    try:
        return fn(conn)
    finally:
        conn.close()


def _data_code(root: Path) -> dict:
    return _with(root, lambda conn: nodepage.data_code(conn, root))


def _files(root: Path) -> dict[str, dict]:
    return {f["path"]: f for f in _data_code(root)["files"]}


def _results(root: Path, draft: str | None = None) -> dict:
    return _with(root, lambda conn: nodepage.results(conn, root, draft))


def _items(root: Path) -> dict[str, dict]:
    """The figures and tables 图表与论断 lists, by path (a figure two drafts include is one row each; they agree)."""
    page = _results(root)
    return {i["path"]: i for i in [*(i for g in page["sections"] for i in g["items"]), *page["loose"]]}


def _page(root: Path, node: str) -> dict:
    return _with(root, lambda conn: nodepage.node(conn, root, node))


def _choose(root: Path, draft: str = DRAFT) -> None:
    papers.set_marked(root, draft, True, via="cli")


def _cite_table(root: Path, path: str) -> None:
    """The paper's first section includes a data file (a table): the link a draft's table would make."""
    def write(conn):
        db.upsert_edge(conn, f"section:{DRAFT}#figures", f"dataset:{path}", "includes", "mdpaper", {"occurrences": [{"line": 1}]}, 1.0)
        conn.commit()
    _with(root, write)


def _answer(root: Path, script: str, candidate: int = 0) -> dict:
    """What the page's 「是」 writes for a broken link of `script`: the candidate's entry, through the mappings writer."""
    link = _files(root)[script]["broken"][0]["candidates"][candidate]["link"]
    mappings_ingest.add_mapping(root, link["from"], link["to"], link["type"])
    _scan(root)
    return link


# -- what is a warning (21.5) -----------------------------------------------------------------------


def test_a_download_only_a_script_reads_is_quiet_text_with_the_action_and_a_table_of_the_paper_is_the_warning(root):
    files = _files(root)
    for path in (RAW, FALLBACK):
        assert files[path]["orphan_input"] and files[path]["no_generator"] == "quiet" and not files[path]["in_paper"]
        assert files[path]["repair"] == {"type": "writes", "label": "指定生成它的脚本", "file": path}
    counts = _data_code(root)["counts"]
    assert counts["no_generator"] == 0 and counts["raw"] == 3  # the warning filter counts none; the neutral one the two downloads and Data/diagnostics.csv
    # The chosen paper includes one of them as a table: now that one lacks a generating script -- the warning.
    _cite_table(root, RAW)
    _choose(root)
    files = _files(root)
    assert files[RAW]["no_generator"] == "warning" and files[RAW]["in_paper"] and files[FALLBACK]["no_generator"] == "quiet"
    counts = _data_code(root)["counts"]
    assert counts["no_generator"] == 1 and counts["raw"] == 2
    item = _items(root)[RAW]
    assert item["kind"] == "表" and item["trace"]["tone"] == "warning" and item["trace"]["state"] == "no_source"
    assert item["trace"]["repair"] == {"type": "writes", "label": "指定生成它的脚本", "file": RAW}


def test_a_script_in_no_attempt_a_script_with_no_reads_and_a_file_nothing_reads_are_quiet_and_say_no_warning(root):
    files = _files(root)
    for path in (f"{PKG}/code/03-stray.py", f"{PKG}/code/00-notes.R", f"{PKG}/Data/unused.csv", f"{PKG}/code/01-build_data.py"):
        assert not {"no_generator", "broken", "repair"} & set(files[path]), path
    assert files[f"{PKG}/code/00-notes.R"]["reads"] == [] and files[f"{PKG}/code/00-notes.R"]["writes"] == []
    assert files[f"{PKG}/Data/unused.csv"]["read_by"] == [] and files[f"{PKG}/Data/unused.csv"]["written_by"] == [f"{PKG}/code/01-build_data.py"]
    page = _page(root, f"script:{PKG}/code/03-stray.py")
    attempt = next(b for b in page["trace"]["breaks"] if b["kind"] == "no_attempt")
    assert attempt["tone"] == "quiet" and attempt["repair"]["label"] == "归入尝试" and attempt["label"] == "还没有归入任何尝试"


def test_a_cited_figure_without_a_generating_script_is_the_warning_and_says_it_came_from_a_word_file(root):
    _choose(root)
    items = _items(root)
    word = items[WORD1]
    assert word["in_paper"] and word["trace"]["state"] == "no_source" and word["trace"]["tone"] == "warning"
    assert word["trace"]["causes"] == [{"code": "word", "label": "没有生成脚本：图是从 Word 文件里抽出来的"}]
    assert word["trace"]["repair"] == {"type": "generates", "label": "指定生成它的脚本", "file": WORD1}
    hand = items[HAND]  # a figure of the paper that is no Word picture: the plain cause
    assert hand["trace"]["tone"] == "warning" and [c["code"] for c in hand["trace"]["causes"]] == ["no_script"]
    assert hand["trace"]["causes"][0]["label"] == "没有生成脚本" and hand["trace"]["repair"]["type"] == "generates"
    # The same kind of figure in a draft the paper is not: the same cause and action, in no colour.
    other = items[WORD3]
    assert not other["in_paper"] and other["trace"]["tone"] == "quiet" and other["trace"]["label"] == "缺来源"
    assert [c["code"] for c in other["trace"]["causes"]] == ["word"] and other["trace"]["repair"]["type"] == "generates"
    # A figure whose script has an attempt is complete; the figure of the paper whose script reads a file that is not there says so.
    assert items[FIG["main"]]["trace"]["tone"] == "ok" and not items[FIG["main"]]["trace"]["causes"]
    needs = items[FIG["needs"]]["trace"]
    assert needs["state"] == "no_source" and needs["tone"] == "warning" and needs["repair"] is None
    assert needs["causes"] == [{"code": "reads_gone", "label": "生成它的脚本读的文件不在"}]


def test_a_figure_whose_script_is_in_no_attempt_says_so_and_is_a_warning_only_for_the_paper(root):
    _choose(root)
    stray = _items(root)[FIG["stray"]]  # only the other drafts include it
    assert not stray["in_paper"] and stray["trace"]["state"] == "no_attempt" and stray["trace"]["tone"] == "quiet"
    assert stray["trace"]["causes"] == [{"code": "script_no_attempt", "label": "生成脚本没有归入尝试"}]
    assert stray["trace"]["repair"] == {"type": "belongs_to", "label": "归入尝试", "file": FIG["stray"]}
    _choose(root, OTHER_DRAFT)  # the paper now includes it as well
    stray = _items(root)[FIG["stray"]]
    assert stray["in_paper"] and stray["trace"]["tone"] == "warning" and stray["trace"]["state"] == "no_attempt"
    page = _results(root)
    assert page["choices"]["attempts"][0]["id"] == "attempt:map.md#1" and page["choices"]["scripts"]  # once for the page


def test_with_no_draft_chosen_every_draft_stands_for_the_paper(root):
    items = _items(root)
    assert items[WORD3]["in_paper"] and items[WORD3]["trace"]["tone"] == "warning"
    assert items[FIG["stray"]]["trace"]["tone"] == "warning"
    _choose(root)
    assert _items(root)[WORD3]["trace"]["tone"] == "quiet"


def test_a_claim_of_the_paper_with_nothing_behind_it_or_a_broken_result_behind_it_is_the_warning(root):
    _choose(root)

    def back(conn):
        found = sorted(db.get_nodes_by_type(conn, "claim"), key=lambda c: c["attrs"].get("line") or 0)
        assert len(found) == 4
        for claim, target in zip(found, (None, f"figure:{FIG['needs']}", f"figure:{FIG['stray']}", f"figure:{FIG['main']}")):
            if target:
                db.upsert_edge(conn, claim["id"], target, "backed_by", "claims", {"occurrences": [{"line": 1}]}, 0.8)
        conn.commit()
    _with(root, back)
    claims = [c["trace"] for g in _results(root, DRAFT)["sections"] for c in g["claims"]]
    assert [(c["state"], c["tone"], [x["code"] for x in c["causes"]]) for c in claims] == [
        ("no_source", "warning", ["no_backing"]), ("no_source", "warning", ["reads_gone"]),
        ("no_attempt", "quiet", ["no_attempt"]), ("complete", "ok", [])]
    assert claims[0]["causes"][0]["label"] == "没有找到支撑这个数字的结果" and claims[1]["causes"][0]["label"] == "依据的结果读的文件不在"


def test_a_figure_can_be_missing_its_source_for_a_script_that_is_not_there(root):
    """The generating script is a file the index knows and the disk does not: the cause says the script is gone."""
    mappings_ingest.add_mapping(root, "gone/ghost.py", HAND, "generates")
    _scan(root)
    _choose(root)
    trace_ = _items(root)[HAND]["trace"]
    assert trace_["state"] == "no_source" and [c["code"] for c in trace_["causes"]] == ["script_gone"]
    assert trace_["causes"][0]["label"] == "生成它的脚本文件不在磁盘上" and trace_["repair"] is None and trace_["tone"] == "warning"


# -- a broken link, and the file it may mean (21.5) --------------------------------------------------------


def test_the_candidate_for_a_package_relative_path_is_the_files_of_the_package_then_the_same_names_nearest_first(root):
    broken = _files(root)[CHECK]["broken"]
    assert [(b["target"], b["type"], b["relation"]) for b in broken] == [("environment/R_packages.lock.csv", "reads", "读取")]
    candidates = broken[0]["candidates"]
    assert [(c["path"], c["package"]) for c in candidates] == [
        (LOCK, True),  # environment/R_packages.lock.csv taken from the script's package, 00_Current/replication_v9/
        ("00_Current/replication_v8/environment/R_packages.lock.csv", False),  # shares 00_Current with the script
        ("Beyond_Persistence_1012/environment/R_packages.lock.csv", False),
        ("Data and Code_20261012/environment/R_packages.lock.csv", False)]
    assert candidates[0]["link"] == {"from": LOCK, "to": CHECK, "type": "reads"}  # data first, as 补上来源 writes a read
    freeze = _files(root)["Data and Code_20261012/code/02-freeze.R"]["broken"][0]
    assert [c["path"] for c in freeze["candidates"]] == [
        "Data and Code_20261012/inputs/reference/shock_series.csv", "Beyond_Persistence_1012/inputs/reference/shock_series.csv"]
    # no file of that name anywhere: the warning is there, and nothing to offer
    needs = _files(root)[f"{PKG}/code/04-needs.py"]["broken"][0]
    assert needs["target"] == f"{PKG}/Data/missing.csv" and needs["candidates"] == []
    assert _data_code(root)["counts"]["broken"] == 4  # check, install, freeze and needs; the missing files' own rows are quiet


def test_the_missing_file_has_a_quiet_row_and_the_script_that_names_it_carries_the_warning(root):
    files = _files(root)
    phantom = files["environment/R_packages.lock.csv"]
    assert phantom["exists"] is False and "broken" not in phantom and "no_generator" not in phantom
    assert phantom["read_by"] == [CHECK, INSTALL] and len(phantom["other_copies"]) == 4
    assert files[CHECK]["broken"] and files[INSTALL]["broken"]


def test_writing_the_candidate_makes_the_row_stop_saying_broken_link_and_the_trace_follows_the_entry(root):
    before = _data_code(root)["counts"]["broken"]
    link = _answer(root, CHECK)
    assert link["from"] == LOCK
    files = _files(root)
    assert "broken" not in files[CHECK] and files[INSTALL]["broken"]  # only the script they answered
    assert files[CHECK]["reads"] == [LOCK]  # what it reads now; the file only its text named is not listed
    assert files["environment/R_packages.lock.csv"]["read_by"] == [INSTALL]  # install_R_packages.R still names it
    assert _data_code(root)["counts"]["broken"] == before - 1
    page = _page(root, f"script:{CHECK}")
    assert page["trace"]["broken"] == [] and not [b for b in page["trace"]["breaks"] if b["kind"] == "data_missing"]
    column = next(c for c in page["trace"]["columns"] if c["items"] and c["items"][0]["type"] == "dataset")
    assert [(i["path"], i["exists"]) for i in column["items"]] == [(LOCK, True)] and not column["items"][0].get("broken")
    # the other script names it too: while it does, the page says so
    page = _page(root, f"script:{INSTALL}")
    assert [b["target"] for b in page["trace"]["broken"]] == ["environment/R_packages.lock.csv"]
    assert page["trace"]["broken"][0]["candidates"][0]["path"] == LOCK
    assert next(c for c in page["trace"]["columns"] if c["items"] and c["items"][0]["type"] == "dataset")["items"][0]["broken"] is True
    _answer(root, INSTALL)
    files = _files(root)
    assert "environment/R_packages.lock.csv" not in files  # nothing refers to it any more
    assert not files[INSTALL].get("broken") and files[INSTALL]["reads"] == [LOCK]
    assert files[LOCK]["read_by"] == [CHECK, INSTALL]


def test_the_lineage_report_no_longer_lists_a_link_the_researchers_entry_answered(root):
    def report(conn):
        return lineage.build_lineage_report(conn, root)
    before = _with(root, report)
    assert {b["script"] for b in before["broken_links"]} >= {CHECK, INSTALL}
    assert "dataset:environment/R_packages.lock.csv" in {o["target"] for o in before["orphans"]}
    _answer(root, CHECK)
    mid = _with(root, report)
    assert {b["script"] for b in mid["broken_links"]} >= {INSTALL} and CHECK not in {b["script"] for b in mid["broken_links"]}
    _answer(root, INSTALL)
    after = _with(root, report)
    assert not {CHECK, INSTALL} & {b["script"] for b in after["broken_links"]}
    names = {b["target"] for block in ("orphans", "chains", "duplicates") for b in after[block]}
    assert "dataset:environment/R_packages.lock.csv" not in names
    assert "dataset:" + LOCK in {t["target"] for t in after["orphans"]}  # the file they named is what the scripts read now
    # an entry for the very path the script names answers nothing: that file is still not there
    assert lineage.superseded({(CHECK, "reads"): {"environment/R_packages.lock.csv"}}, CHECK, "reads", "environment/R_packages.lock.csv") is False
    assert lineage.superseded({(CHECK, "writes"): {"x/out.png"}}, CHECK, "writes", "y/out.png") is True


# -- lists of one name, and the node page (21.5) -----------------------------------------------------


def test_a_list_of_scripts_of_one_name_tells_them_apart_by_their_nearest_folder(root):
    panel = _files(root)[f"{PKG}/Data/diagnostics.csv"]
    assert panel["read_by"] == ["交付_20260927_审查修订/复核诊断.R", "交付_20261006/复核诊断.R", "复核诊断.R"]
    assert panel["where"] == {"read_by": {"交付_20260927_审查修订/复核诊断.R": "交付_20260927_审查修订", "交付_20261006/复核诊断.R": "交付_20261006"}}
    assert provenance.distinct_names(["a/x/f.R", "b/x/f.R", "c/y/f.R", "f.R", "g.R"]) == {
        "a/x/f.R": "a/x", "b/x/f.R": "b/x", "c/y/f.R": "y"}  # as few folders as tell it from the others; the root needs none
    assert provenance.distinct_names(["a/f.R", "b/g.R"]) == {}


def test_the_node_page_of_a_figure_says_what_its_row_says_in_the_tone_the_paper_gives_it(root):
    _choose(root)
    page = _page(root, f"figure:{WORD1}")
    assert page["in_paper"]
    gap = page["trace"]["columns"][0]["gap"]
    assert (gap["kind"], gap["tone"], gap["label"], gap["cause"]) == (
        "no_generating_script", "warning", "缺生成脚本", "没有生成脚本：图是从 Word 文件里抽出来的")
    assert gap["repair"]["label"] == "指定生成它的脚本"
    attempt = next(b for b in page["trace"]["breaks"] if b["kind"] == "no_attempt")
    assert attempt["tone"] == "quiet"  # the source is what is missing; one thing at a time
    quiet = _page(root, f"figure:{WORD3}")["trace"]["columns"][0]["gap"]
    assert not _page(root, f"figure:{WORD3}")["in_paper"] and quiet["tone"] == "quiet" and "cause" not in quiet
    assert quiet["label"] == "没有生成脚本：图是从 Word 文件里抽出来的" and quiet["repair"]["type"] == "generates"
    raw = _page(root, f"dataset:{RAW}")["trace"]["columns"][0]["gap"]
    assert raw["tone"] == "quiet" and raw["label"] == "没有生成脚本" and raw["repair"]["label"] == "指定生成它的脚本"
    needs = _page(root, f"figure:{FIG['needs']}")
    assert [b["target"] for b in needs["trace"]["broken"]] == [f"{PKG}/Data/missing.csv"] and needs["trace"]["broken"][0]["candidates"] == []
    assert not [b for b in needs["trace"]["breaks"] if b["kind"] == "data_missing"]  # the broken link says it
    assert any(i.get("broken") for c in needs["trace"]["columns"] for i in c["items"])


def test_a_figure_that_a_script_made_from_a_file_in_the_wrong_place_is_mended_with_one_entry_and_the_row_turns_complete(root):
    """The whole of it, on the figure the paper includes: its script reads a file that is not there and the same
    file sits one folder up -- 「是」 writes the entry, and the figure is complete."""
    _write(root, f"{PKG}/code/05-wrong.py", "import pandas as pd\nimport matplotlib.pyplot as plt\n" + READ.format("Data/panel_clean.csv")
           + 'plt.savefig("../current/Plots/fig_wrong.png")\n')
    _write(root, FIG["main"].replace("main", "wrong"), b"\x89PNG wrong")
    _write(root, DRAFT, (root / DRAFT).read_text(encoding="utf-8") + "\n![Wrong](replication_v9/current/Plots/fig_wrong.png)\n")
    _write(root, ".researchos/attempts.toml", ATTEMPTS)
    _write(root, "map.md", MAP.replace("Plots (02)", "Plots (02) and again (05)"))
    _scan(root)
    _choose(root)
    wrong = FIG["main"].replace("main", "wrong")
    assert _items(root)[wrong]["trace"]["causes"] == [{"code": "reads_gone", "label": "生成它的脚本读的文件不在"}]
    broken = _files(root)[f"{PKG}/code/05-wrong.py"]["broken"][0]
    assert broken["candidates"][0]["path"] == f"{PKG}/Data/panel_clean.csv" and broken["candidates"][0]["package"] is True
    _answer(root, f"{PKG}/code/05-wrong.py")
    done = _items(root)[wrong]["trace"]
    assert done["state"] == "complete" and done["tone"] == "ok" and not done["causes"]


def test_a_missing_file_is_plain_on_its_own_page_and_the_broken_links_file_is_the_warnings_in_the_script_s_trace(root):
    phantom = _page(root, "dataset:environment/R_packages.lock.csv")
    assert phantom["exists"] is False and phantom["tone"] == "quiet" and phantom["in_paper"] is False
    item = next(i for c in _page(root, f"script:{CHECK}")["trace"]["columns"] for i in c["items"] if i["id"] == "dataset:environment/R_packages.lock.csv")
    assert item["exists"] is False and item["broken"] is True and item["tone"] == "warning"
    _choose(root)
    assert _page(root, f"figure:{FIG['needs']}")["tone"] == "warning"  # a figure of the paper


def test_the_endpoints_answer_with_the_candidates_and_the_entry_they_wrote_clears_the_broken_link(root):
    """The page's whole way: GET /api/data-code gives the broken link and its candidates; POST /api/mappings/add with the
    first candidate's `link` writes the entry; GET /api/data-code then says no more of it, and /api/node follows."""
    import urllib.parse

    from test_v8_phase2 import _call
    from test_v8_phase3 import _serve

    with _serve(root) as base:
        status, got = _call(f"{base}/api/data-code")
        assert status == 200
        row = next(f for f in got["files"] if f["path"] == CHECK)
        candidate = row["broken"][0]["candidates"][0]
        assert candidate["path"] == LOCK and "outputs" in got and got["choices"]["scripts"]
        status, written = _call(f"{base}/api/mappings/add", "POST", candidate["link"])
        assert status == 200 and written["ok"] and written["entry"]["from"] == LOCK and written["entry"]["to"] == CHECK
        status, got = _call(f"{base}/api/data-code")
        row = next(f for f in got["files"] if f["path"] == CHECK)
        assert "broken" not in row and row["reads"] == [LOCK]
        status, page = _call(f"{base}/api/node?node={urllib.parse.quote(f'script:{CHECK}', safe='')}")
        assert status == 200 and page["trace"]["broken"] == []
        # 撤销: the same entry removed puts the warning back
        status, _gone = _call(f"{base}/api/mappings/delete", "POST", candidate["link"])
        assert status == 200
        status, got = _call(f"{base}/api/data-code")
        assert next(f for f in got["files"] if f["path"] == CHECK)["broken"][0]["candidates"][0]["path"] == LOCK
        status, results = _call(f"{base}/api/results")
        assert status == 200 and "choices" in results and results["sections"]


def test_what_the_page_can_offer_no_action_for_is_quiet_even_in_the_paper():
    """A warning is where it can be acted on: a table that is a file nothing generates (the grammar gives a draft or a
    document no generating script) or a file that cannot be filed under an attempt says its cause in no colour."""
    assert provenance.tone_of("no_source", True, ["no_script"], provenance.repair_of("no_source", "figure", ["no_script"], "f.png")) == "warning"
    assert provenance.repair_of("no_source", "file", ["no_script"], "t.md") is None
    assert provenance.tone_of("no_source", True, ["no_script"], None) == "quiet"
    assert provenance.tone_of("no_attempt", True, ["no_attempt"], provenance.repair_of("no_attempt", "file", ["no_attempt"], "t.md")) == "quiet"
    assert provenance.tone_of("no_source", True, ["reads_gone"], None) == "warning"  # a broken chain is the paper's whatever the file is
    assert provenance.tone_of("no_source", False, ["word"], None) == "quiet" and provenance.tone_of("complete", True) == "ok"


def test_the_kind_pages_answer_a_project_with_many_old_copies_without_a_scan_per_row(tmp_path):
    """Speed (21.5): eight copies of a package of 30 scripts each, every copy with five broken links and the same file
    names -- the project's files are walked once for every candidate, the person's entries read once, not per row."""
    root = tmp_path / "many"
    csv = "id,x\n1,2\n"
    for c in range(8):
        pkg = f"copy_{c}"
        for k in range(30):
            _write(root, f"{pkg}/raw/in_{k % 6}.csv", csv)
            _write(root, f"{pkg}/code/{k:02d}-step.py", "import pandas as pd\n" + READ.format(f"../raw/in_{k % 6}.csv")
                   + f'df.to_csv("../Data/out_{k}.csv")\n')
            _write(root, f"{pkg}/Data/out_{k}.csv", csv)
        for k in range(5):
            _write(root, f"{pkg}/env/check_{k}.R", REVIEW.format("env/R_packages.lock.csv"))
        _write(root, f"{pkg}/env/R_packages.lock.csv", csv)
    _write(root, "map.md", MAP)
    project_identity.init_project(root)
    _write(root, ".researchos/attempts.toml", ATTEMPTS)
    _scan(root)
    conn = _conn(root)
    try:
        best = {}
        for name, fn in (("data_code", lambda: nodepage.data_code(conn, root)), ("results", lambda: nodepage.results(conn, root))):
            best[name] = min(_timed(fn) for _ in range(3))
        out = nodepage.data_code(conn, root)
    finally:
        conn.close()
    assert len(out["files"]) >= 8 * (30 + 30 + 6 + 5) and out["counts"]["broken"] == 40
    assert all(len(f["broken"][0]["candidates"]) == 8 for f in out["files"] if f.get("broken"))  # its own copy first, the others after
    assert max(best.values()) < 2.0, best


def _timed(fn) -> float:
    import time

    began = time.perf_counter()
    fn()
    return time.perf_counter() - began

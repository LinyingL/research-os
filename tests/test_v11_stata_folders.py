"""The project folder behind a Stata global (DESIGN.md 15.3, task V11 S3):
Research OS's proposal and its thresholds (`researchos.ingest.stata_folders`),
the human record `.researchos/stata.toml` (`researchos.records.stata`), and
resolution through a confirmed answer (`researchos.ingest.dataflow`). Every
do-file here is written for the test; none is taken from a replication
package."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from researchos import db, inventory, paths, people, share
from researchos import project as project_identity
from researchos.ingest import dataflow, stata, stata_folders
from researchos.ingest import scan as scan_mod
from researchos.records import stata as stata_record

PI = ("李", "li@example.com")
MEMBER = ("王", "wang@example.com")
TEAM = (
    '[[member]]\nid = "li@example.com"\nname = "李"\nrole = "pi"\n\n'
    '[[member]]\nid = "wang@example.com"\nname = "王"\nrole = "member"\n'
)


def _files(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def _package(tmp_path: Path) -> Path:
    """A package whose root global each user sets: every path is
    `$Proj/<rel>`, and the files sit under analysis/."""
    return _files(tmp_path / "pkg", {
        "analysis/run.do": '* global Proj "C:/Users/someone/pkg/analysis"\ndo "$Proj/scripts/1_clean.do"\n',
        "analysis/scripts/1_clean.do": (
            'insheet using "$Proj/data/raw.csv", clear\n'  # 1
            'save "$Proj/processed/clean", replace\n'  # 2
            'use "$Proj/processed/clean", clear\n'  # 3
            'graph export "$Proj/results/fig.pdf", replace\n'  # 4
            'save "$Proj/results/new_table", replace\n'  # 5: not committed
            "tempfile t\nsave `t'\n"  # 6-7
        ),
        "analysis/data/raw.csv": "a\n1\n",
        "analysis/processed/clean.dta": "x",
        "analysis/results/fig.pdf": "%PDF",
        "analysis/scripts/data/raw.csv": "a\n1\n",  # beside the do-file: never used for an anchored path
    })


def _scans(root: Path):
    dos = sorted(str(p.relative_to(root)) for p in root.rglob("*.do"))
    return dataflow._stata_scans(root, dos, [])


def _ingest(conn, root: Path):
    dos = sorted(str(p.relative_to(root)) for p in root.rglob("*.do"))
    return dataflow.ingest_dataflow_repo(conn, root, [], [], [], do_paths=dos)


# -- the proposal (a) -------------------------------------------------------------------------------


def test_an_undefined_global_is_proposed_as_the_folder_holding_its_paths(tmp_path):
    root = _package(tmp_path)
    heads = stata_folders.head_globals(_scans(root))
    assert list(heads) == ["Proj"]
    assert heads["Proj"].reason == stata.UNDEFINED and heads["Proj"].uses == 5
    assert heads["Proj"].paths == ("data/raw.csv", "processed/clean.dta", "results/fig.pdf", "results/new_table.dta")
    assert stata_folders.propose(root, heads) == {"Proj": stata_folders.Proposal(
        "Proj", "analysis", stata.UNDEFINED, "", "", paths=4, found=3)}


@pytest.mark.parametrize("files, why", [
    ({"analysis/data/raw.csv": ""}, "one file proves little"),
    ({"analysis/data/raw.csv": "", "analysis/processed/clean.dta": "", "old/processed/clean.dta": "",
      "old/data/raw.csv": ""}, "two folders tie"),
    ({"analysis/data/raw.csv": "", "analysis/processed/clean.dta": ""}, "fewer than half of five"),
])
def test_no_proposal_unless_the_files_are_unambiguous(tmp_path, files, why):
    do = ('use "$Proj/data/raw.csv"\nuse "$Proj/processed/clean"\nuse "$Proj/a"\n'
          + ('use "$Proj/b"\nuse "$Proj/c"\n' if "half" in why else ""))
    root = _files(tmp_path / "p", {"code/x.do": do, **files})
    heads = stata_folders.head_globals(_scans(root))
    assert "Proj" in heads and stata_folders.propose(root, heads) == {}, why


def test_an_absolute_root_is_proposed_by_the_folders_its_value_ends_in(tmp_path):
    root = _files(tmp_path / "rio", {
        "Package/MASTER.do": (
            'global github "C:\\Users\\someone\\Documents\\GitHub\\rio\\Package"\n'
            'global data "${github}/data"\n'
            'global encrypt "C:\\secret"\nglobal encrypt "${onedrive}/raw"\n'  # assigned twice: never proposed
        ),
        "Package/dofiles/clean.do": (
            'use "${data}/raw/a.dta"\nsave "${data}/final/b.dta"\ngraph export "${github}/out/g.png"\n'
            'use "${encrypt}/c.dta"\nuse "${nowhere}/d.dta"\n'
        ),
        "Package/data/raw/a.dta": "x",
        "Package/out/g.png": "png",
    })
    heads = stata_folders.head_globals(_scans(root))
    assert sorted(heads) == ["github", "nowhere"]  # not encrypt: its assignments disagree
    assert heads["github"].value == "C:/Users/someone/Documents/GitHub/rio/Package"
    assert stata_folders.propose(root, heads) == {"github": stata_folders.Proposal(
        "github", "Package", stata.OUTSIDE, "C:/Users/someone/Documents/GitHub/rio/Package", "rio/Package",
        paths=3, found=2)}


def test_an_absolute_root_is_not_proposed_on_a_tie_or_against_the_files(tmp_path):
    tie = _files(tmp_path / "proj", {
        "a.do": 'global root "/Users/someone/proj"\nuse "$root/x/a.dta"\n',
        "proj/keep.txt": "",  # a folder named like the project: "proj" names both
    })
    heads = stata_folders.head_globals(_scans(tie))
    assert heads["root"].reason == stata.OUTSIDE and stata_folders.propose(tie, heads) == {}
    against = _files(tmp_path / "other", {
        "a.do": 'global root "/Users/someone/stuff/data"\nuse "$root/x/a.dta"\nuse "$root/x/b.dta"\n',
        "data/keep.txt": "",
        "copy/x/a.dta": "", "copy/x/b.dta": "",  # the files say copy/, the value says data/
    })
    heads = stata_folders.head_globals(_scans(against))
    assert stata_folders.propose(against, heads) == {}


def test_unresolved_paths_are_grouped_by_reason(tmp_path):
    root = _files(tmp_path / "p", {
        "a.do": ('global two "x"\nglobal two "y"\nuse "$two/a"\nuse "$none/b"\n'
                 'foreach v in 1 2 {\n  use "data/`v\'"\n}\ntempfile t\nsave `t\'\n'),
    })
    got = stata_folders.unresolved(_scans(root))
    assert list(got) == [stata.UNDEFINED, stata.REASSIGNED, stata.LOOP]  # the tempfile is not unresolved
    assert got[stata.UNDEFINED] == [{"file": "a.do", "line": 4, "command": "use", "text": "$none/b", "macro": "$none"}]


# -- the record (b) ---------------------------------------------------------------------------------


def test_answers_are_an_append_only_ledger_and_the_newest_holds(tmp_path):
    people.write_me(*PI)
    root = _package(tmp_path)
    project_identity.init_project(root)
    done = stata_record.answer(root, "Proj", "analysis/", via="app")
    assert done["changed"] and done["folder"] == "analysis"
    assert not stata_record.answer(root, "Proj", "analysis", via="app")["changed"]  # already so: nothing appended
    stata_record.answer(root, "Proj", None, via="app")
    text = stata_record.stata_path(root).read_text(encoding="utf-8")
    assert text.startswith("# Stata 全局变量代表项目里的哪个文件夹")
    entries = tomllib.loads(text)["global"]
    assert [(e["global"], e["answer"], e["folder"], e["by"], e["seq"]) for e in entries] == [
        ("Proj", "yes", "analysis", PI[1], 1), ("Proj", "no", "none", PI[1], 2)]
    assert stata_record.anchors(root) == ({}, None)
    stata_record.answer(root, "Proj", ".", via="app")
    assert stata_record.anchors(root) == ({"Proj": "."}, None)
    assert list((root / ".researchos" / "backups").rglob("stata.toml*"))  # founded: snapshotted
    for bad in ("../up", "/abs", "C:/x", "nope", ".researchos"):
        with pytest.raises(stata_record.StataRefused):
            stata_record.answer(root, "Proj", bad, via="app")
    with pytest.raises(stata_record.StataRefused):
        stata_record.answer(root, "not a name", "analysis", via="app")


def test_only_the_pi_answers_in_a_project_with_members(tmp_path):
    root = _package(tmp_path)
    project_identity.init_project(root)
    paths.project_dir(root).joinpath("team.toml").write_text(TEAM, encoding="utf-8")
    people.write_me(*MEMBER)
    with pytest.raises(stata_record.StataRefused) as refused:
        stata_record.answer(root, "Proj", "analysis", via="app")
    assert refused.value.code == "not_pi"
    assert refused.value.say("en") == "Only the PI answers which project folder a global stands for; members can only look"
    assert "只有 PI" in refused.value.say("zh")
    assert not stata_record.stata_path(root).exists()
    people.write_me(*PI)
    assert stata_record.answer(root, "Proj", "analysis", via="app")["changed"]


def test_a_record_that_cannot_be_trusted_is_never_built_upon(tmp_path, conn, caplog):
    people.write_me(*PI)
    root = _package(tmp_path)
    project_identity.init_project(root)
    stata_record.answer(root, "Proj", "analysis", via="app")
    assert _ingest(conn, root) == {"reads": 2, "writes": 3}
    stata_record.stata_path(root).write_text("[[global]\nbroken\n", encoding="utf-8")
    assert stata_record.anchors(root) == (None, "invalid")
    with pytest.raises(stata_record.StataRefused) as refused:
        stata_record.answer(root, "Proj", None, via="app")
    assert refused.value.code == "untrusted"
    with caplog.at_level("WARNING", logger="researchos.ingest"):
        _ingest(conn, root)
    assert "stata.toml cannot be trusted now (invalid)" in caplog.text
    status = db.scan_source_row(conn, "dataflow", "analysis/scripts/1_clean.do")["status"]
    assert status == scan_mod.UNREADABLE  # its links keep the state they had
    stata_record.stata_path(root).unlink()  # gone though a snapshot exists: still refused
    assert stata_record.anchors(root) == (None, "missing")


def test_the_record_is_snapshotted_carried_and_listed(tmp_path):
    people.write_me(*PI)
    root = _package(tmp_path)
    project_identity.init_project(root)
    assert not any(r.code == "stata" for r in inventory.inventory(None, root))  # no Stata answer, no row
    stata_record.answer(root, "Proj", "analysis", via="app")
    assert "stata.toml" in share.RECORD_FILES
    assert stata_record.stata_path(root) in inventory.record_file_paths(root)
    assert "stata.toml" in paths.project_readme_text(root)
    (row,) = [r for r in inventory.inventory(None, root) if r.code == "stata"]
    assert row.facts["globals"] == 1 and row.facts["folders"] == 1 and not row.problems


# -- resolution through a confirmed answer (c) ------------------------------------------------------


def test_a_confirmed_folder_resolves_its_paths_with_the_answer_in_the_evidence(tmp_path, conn):
    people.write_me(*PI)
    root = _package(tmp_path)
    project_identity.init_project(root)
    assert _ingest(conn, root) == {"reads": 0, "writes": 0}  # until answered: no link
    stata_record.answer(root, "Proj", "analysis", via="app")
    assert _ingest(conn, root) == {"reads": 2, "writes": 3}
    edges = {(e["type"], e["dst"]): e for e in db.query_edges(conn) if e["extractor"] == "dataflow"}
    assert sorted(edges) == [
        ("reads", "dataset:analysis/data/raw.csv"), ("reads", "dataset:analysis/processed/clean.dta"),
        ("writes", "dataset:analysis/processed/clean.dta"), ("writes", "dataset:analysis/results/new_table.dta"),
        ("writes", "figure:analysis/results/fig.pdf"),
    ]
    (occurrence,) = edges[("reads", "dataset:analysis/data/raw.csv")]["evidence"]["occurrences"]
    assert occurrence == {
        "file": "analysis/scripts/1_clean.do", "line": 1, "callee": "insheet",
        "anchor": {"global": "Proj", "folder": "analysis"},
        "folded_from": '$Proj/data/raw.csv; global Proj "analysis" (.researchos/stata.toml)',
    }  # relative to the project folder: not the copy beside the do-file
    assert edges[("writes", "dataset:analysis/results/new_table.dta")]["evidence"]["occurrences"][0]["missing"]
    assert scan_mod.current_basis(conn, {"src": "script:analysis/scripts/1_clean.do",
                                         "dst": "dataset:analysis/data/raw.csv", "type": "reads",
                                         "extractor": "dataflow"}) == {"calls": ["insheet"]}
    unresolved = stata_folders.unresolved(_scans(root))
    assert stata.UNDEFINED not in unresolved


def test_an_answer_replaces_an_absolute_root_and_a_no_leaves_it_unresolved(tmp_path, conn):
    people.write_me(*PI)
    root = _files(tmp_path / "rio", {
        "Package/MASTER.do": 'global github "C:\\Users\\someone\\rio\\Package"\nglobal data "${github}/data"\n',
        "Package/dofiles/clean.do": 'use "${data}/a.dta"\nuse "data/${github}"\n',
        "Package/data/a.dta": "x",
    })
    project_identity.init_project(root)
    stata_record.answer(root, "github", None, via="app")
    _ingest(conn, root)
    assert not db.query_edges(conn, type="reads")
    assert [s.reason for s in _scans(root)["Package/dofiles/clean.do"].skipped] == [stata.OUTSIDE, stata.OUTSIDE]
    stata_record.answer(root, "github", "Package", via="app")
    _ingest(conn, root)
    (edge,) = db.query_edges(conn, type="reads")
    assert edge["dst"] == "dataset:Package/data/a.dta"
    assert edge["evidence"]["occurrences"][0]["anchor"] == {"global": "github", "folder": "Package"}
    assert not conn.execute("SELECT 1 FROM nodes WHERE id LIKE '%C:%'").fetchall()
    # a project folder inside a path names nothing: never resolved
    assert [s.reason for s in _scans(root)["Package/dofiles/clean.do"].skipped] == [stata.MACRO]

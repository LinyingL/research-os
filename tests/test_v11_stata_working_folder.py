"""The working folder of a Stata run (DESIGN.md 15.3, task V11): a literal
`cd` is followed and one that cannot be followed leaves the paths after it
unresolved; a do-file run by `do`/`run`/`include` reads its relative paths
in the working folder of the run, never its own folder; a do-file nobody
runs keeps today's rule. Every do-file here is written for the test; none
is taken from a replication package."""

from __future__ import annotations

from pathlib import Path

from researchos import db, people
from researchos import project as project_identity
from researchos.ingest import stata, stata_folders
from researchos.records import stata as stata_record

from test_v11_stata_folders import PI, _files, _ingest, _scans


def _links(root: Path) -> tuple[list, dict, list]:
    """(sorted (type, dst, src) of the dataflow links, the evidence by (type,
    dst) -- of the first such link, by (type, dst, src) too -- every node id)."""
    conn = db.connect(":memory:")
    db.migrate(conn)
    try:
        _ingest(conn, root)
        edges = [e for e in db.query_edges(conn) if e["extractor"] == "dataflow"]
        nodes = [r[0] for r in conn.execute("SELECT id FROM nodes")]
    finally:
        conn.close()
    evidence = {(e["type"], e["dst"], e["src"]): e["evidence"]["occurrences"] for e in edges}
    for e in sorted(edges, key=lambda e: e["src"], reverse=True):
        evidence[(e["type"], e["dst"])] = e["evidence"]["occurrences"]
    return sorted((e["type"], e["dst"], e["src"]) for e in edges), evidence, nodes


def _skips(root: Path, rel: str) -> list[tuple]:
    return [(s.line, s.command, s.reason) for s in _scans(root)[rel].skipped]


# -- a literal cd (15.3) ----------------------------------------------------------------------------


def test_a_relative_cd_is_followed_from_the_one_folder_that_exists(tmp_path):
    """s1: code/master.do runs `cd "../data"`: from the project folder that
    leaves the project, from its own folder it is data/ -- exactly one
    existing folder, so the paths after it are read there."""
    root = _files(tmp_path / "s1", {
        "code/master.do": 'use "raw.dta"\ncd "../data"\nuse "raw.dta"\nsave "clean"\n',
        "data/raw.dta": "x", "code/raw.dta": "x",
    })
    edges, evidence, nodes = _links(root)
    assert edges == [("reads", "dataset:code/raw.dta", "script:code/master.do"),
                     ("reads", "dataset:data/raw.dta", "script:code/master.do"),
                     ("writes", "dataset:data/clean.dta", "script:code/master.do")]
    (read,) = evidence[("reads", "dataset:data/raw.dta")]
    assert read["working_folder"] == {"folder": "data", "set_by": 'cd "../data" (code/master.do:2)'}
    assert "dataset:raw.dta" not in nodes


def test_cd_to_a_windows_root_is_proposed_then_followed_once_answered(tmp_path):
    """s4, the AEA template: `global root "C:/..."`, `cd "$root"`, then
    relative paths. Before the answer nothing is linked -- not the file of
    the same name at the project folder (s6) -- and the paths count toward
    the proposal; after 「是」 they resolve under the folder, the evidence
    naming the cd and the answer."""
    people.write_me(*PI)
    root = _files(tmp_path / "s4", {
        "analysis/code/master.do": ('global root "C:/Users/me/s4/analysis"\ncd "$root"\n'
                                    'use "data/raw.dta"\nsave "output/clean"\n'),
        "analysis/data/raw.dta": "x",
        "data/raw.dta": "a wrong file at the project folder",
    })
    project_identity.init_project(root)
    edges, _evidence, _nodes = _links(root)
    assert edges == []
    assert _skips(root, "analysis/code/master.do") == [
        (2, "cd", stata.OUTSIDE), (3, "use", stata.WORKING_FOLDER), (4, "save", stata.WORKING_FOLDER)]
    heads = stata_folders.head_globals(_scans(root))
    assert heads["root"].reason == stata.OUTSIDE and heads["root"].paths == ("data/raw.dta", "output/clean.dta")
    assert stata_folders.propose(root, heads)["root"].folder == "analysis"
    assert stata.WORKING_FOLDER in stata_folders.unresolved(_scans(root))
    stata_record.answer(root, "root", "analysis", via="app")
    edges, evidence, _nodes = _links(root)
    assert edges == [("reads", "dataset:analysis/data/raw.dta", "script:analysis/code/master.do"),
                     ("writes", "dataset:analysis/output/clean.dta", "script:analysis/code/master.do")]
    (read,) = evidence[("reads", "dataset:analysis/data/raw.dta")]
    assert read["anchor"] == {"global": "root", "folder": "analysis"}
    assert read["working_folder"] == {"folder": "analysis", "set_by": 'cd "$root" (analysis/code/master.do:2)'}


def test_cd_to_an_undefined_root_gets_a_proposal_and_resolves(tmp_path):
    people.write_me(*PI)
    root = _files(tmp_path / "p", {
        "analysis/code/master.do": 'cd "$root"\nuse "data/raw.dta"\nuse "data/b"\nsave "output/clean"\n',
        "analysis/data/raw.dta": "x", "analysis/data/b.dta": "x",
    })
    project_identity.init_project(root)
    proposal = stata_folders.propose(root, stata_folders.head_globals(_scans(root)))["root"]
    assert (proposal.folder, proposal.paths, proposal.found) == ("analysis", 3, 2)
    stata_record.answer(root, "root", "analysis", via="app")
    edges, _evidence, _nodes = _links(root)
    assert [e[1] for e in edges] == ["dataset:analysis/data/b.dta", "dataset:analysis/data/raw.dta",
                                     "dataset:analysis/output/clean.dta"]


def test_a_cd_that_cannot_be_followed_leaves_the_paths_after_it_unresolved(tmp_path):
    """A bare `cd` (the home folder on a Mac), `capture cd`, a cd after an
    inline `if`, inside a block, or inside a program: the paths after it
    are skipped and logged, never resolved by today's rule."""
    root = _files(tmp_path / "p", {
        "bare.do": 'cd\nuse "data/a"\n',
        "capture.do": 'capture cd "data"\nuse "a"\n',
        "inline.do": 'if c(os) == "MacOSX" cd "data"\nuse "a"\n',
        "block.do": 'if 1 {\n  cd "data"\n}\nuse "a"\n',
        "program.do": 'program define go\n  cd "data"\nend\nuse "data/a"\ngo\nuse "data/a"\n',
        "data/a.dta": "x", "a.dta": "x",
    })
    edges, _evidence, _nodes = _links(root)
    assert edges == [("reads", "dataset:data/a.dta", "script:program.do")]  # line 4: before the program's cd runs
    assert _skips(root, "bare.do") == [(1, "cd", stata.CD_NOT_FOLLOWED), (2, "use", stata.WORKING_FOLDER)]
    assert _skips(root, "capture.do") == [(1, "cd", stata.CD_NOT_FOLLOWED), (2, "use", stata.WORKING_FOLDER)]
    assert _skips(root, "inline.do") == [(1, "cd", stata.INLINE_IF), (2, "use", stata.WORKING_FOLDER)]
    assert _skips(root, "block.do") == [(2, "cd", stata.CD_NOT_FOLLOWED), (4, "use", stata.WORKING_FOLDER)]
    assert _skips(root, "program.do") == [(2, "cd", stata.CD_NOT_FOLLOWED), (5, "go", stata.CD_NOT_FOLLOWED),
                                          (6, "use", stata.WORKING_FOLDER)]


def test_chdir_is_cd_and_a_cd_outside_the_project_is_not_followed(tmp_path):
    root = _files(tmp_path / "p", {
        "x.do": f'chdir "data"\nuse "a"\ncd "{tmp_path}/elsewhere"\nuse "a"\ncd "{tmp_path / "p" / "data"}"\nuse "b"\n',
        "data/a.dta": "x", "data/b.dta": "x",
    })
    edges, _evidence, _nodes = _links(root)
    assert [e[1] for e in edges] == ["dataset:data/a.dta", "dataset:data/b.dta"]
    assert _skips(root, "x.do") == [(3, "cd", stata.OUTSIDE), (4, "use", stata.WORKING_FOLDER)]


# -- do, run and include carry the working folder (15.2, 15.3) --------------------------------------


def test_a_do_file_run_by_another_reads_in_the_working_folder_of_the_run(tmp_path):
    """task/src/main.do runs ../input/raw/dict/load.do, whose
    `use "../input/raw/dict/rows.dta"` is relative to task/src -- the
    folder of the do-file that started the run -- never to its own folder
    (which invented task/input/raw/input/raw/dict/rows.dta)."""
    root = _files(tmp_path / "p", {
        "task/src/main.do": 'do "../input/raw/dict/load.do"\n',
        "task/input/raw/dict/load.do": 'use "../input/raw/dict/rows.dta"\n',
        "task/input/raw/dict/rows.dta": "x",
    })
    edges, evidence, nodes = _links(root)
    assert edges == [("reads", "dataset:task/input/raw/dict/rows.dta", "script:task/input/raw/dict/load.do")]
    assert not [n for n in nodes if "input/raw/input" in n]
    (read,) = evidence[("reads", "dataset:task/input/raw/dict/rows.dta")]
    assert read["working_folder"] == {"folder": "task/src",
                                      "set_by": 'do "../input/raw/dict/load.do" (task/src/main.do:1)'}


def test_nested_runs_stay_in_the_folder_of_the_run_and_run_is_ru(tmp_path):
    """src/main.do runs input/a.do (`ru`, run's minimum abbreviation, the .do
    extension assumed), which runs `do "../input/b"` -- relative to src/,
    not to input/; include carries the folder the same way."""
    root = _files(tmp_path / "p", {
        "src/main.do": 'ru "../input/a"\n',
        "input/a.do": 'do "../input/b"\ninclude "../input/c.do"\n',
        "input/b.do": 'use "../data/x"\n',
        "input/c.do": 'save "../out/y"\n',
        "data/x.dta": "x",
    })
    edges, _evidence, _nodes = _links(root)
    assert edges == [("reads", "dataset:data/x.dta", "script:input/b.do"),
                     ("writes", "dataset:out/y.dta", "script:input/c.do")]


def test_a_cd_carries_into_the_runs_after_it_and_back_from_them(tmp_path):
    """A callee run after a cd starts in that folder; a cd in a callee holds
    in its caller after the run (Stata's working folder is global)."""
    root = _files(tmp_path / "p", {
        "master.do": 'cd "data"\ndo "../code/clean.do"\ncd ".."\ndo "code/setup.do"\nuse "raw"\n',
        "code/clean.do": 'use "raw"\nsave "clean"\n',
        "code/setup.do": 'cd "data"\n',
        "data/raw.dta": "x", "raw.dta": "a file of the same name at the project folder",
    })
    edges, evidence, _nodes = _links(root)
    assert edges == [("reads", "dataset:data/raw.dta", "script:code/clean.do"),
                     ("reads", "dataset:data/raw.dta", "script:master.do"),
                     ("writes", "dataset:data/clean.dta", "script:code/clean.do")]
    (callee,) = evidence[("reads", "dataset:data/raw.dta", "script:code/clean.do")]
    assert callee["working_folder"] == {"folder": "data",
                                        "set_by": 'cd "data" (master.do:1); do "../code/clean.do" (master.do:2)'}
    (caller,) = evidence[("reads", "dataset:data/raw.dta", "script:master.do")]
    assert caller["working_folder"] == {"folder": "data", "set_by": 'cd "data" (code/setup.do:1)'}


def test_a_callee_run_after_a_cd_that_cannot_be_followed_is_skipped(tmp_path):
    people.write_me(*PI)
    root = _files(tmp_path / "p", {
        "master.do": 'cd "$elsewhere"\ndo "$root/code/clean.do"\n',
        "code/clean.do": 'use "data/raw"\n',
        "data/raw.dta": "x", "code/data/raw.dta": "x",
    })
    project_identity.init_project(root)
    stata_record.answer(root, "root", ".", via="app")
    edges, _evidence, _nodes = _links(root)
    assert edges == []
    (skip,) = _scans(root)["code/clean.do"].skipped
    assert (skip.reason, skip.macro, skip.rest, skip.macro_reason) == (
        stata.WORKING_FOLDER, "$elsewhere", "data/raw.dta", stata.UNDEFINED)


def test_a_do_file_run_from_two_working_folders_or_in_a_cycle_is_skipped(tmp_path):
    root = _files(tmp_path / "p", {
        "a/x.do": 'do "../common/c.do"\n',
        "b/y.do": 'do "../common/c.do"\n',
        "common/c.do": 'use "data.dta"\n',
        "loop/one.do": 'do "two.do"\nuse "d.dta"\n',
        "loop/two.do": 'do "one.do"\n',
        "a/data.dta": "x", "loop/d.dta": "x",
    })
    edges, _evidence, _nodes = _links(root)
    assert edges == []
    assert _skips(root, "common/c.do") == [(1, "use", stata.TWO_FOLDERS)]
    assert _skips(root, "loop/one.do") == [(2, "use", stata.CYCLE)]


def test_a_do_file_nobody_runs_keeps_todays_rule(tmp_path):
    """15.3: the project folder, then its own folder -- also when another
    do-file's run names a do-file that is not in the project."""
    root = _files(tmp_path / "p", {
        "code/x.do": 'use "data/a"\nuse "b"\n',
        "code/data/a.dta": "x", "b.dta": "x",
        "master.do": 'do "code/missing.do"\n',
    })
    edges, evidence, _nodes = _links(root)
    assert edges == [("reads", "dataset:b.dta", "script:code/x.do"), ("reads", "dataset:code/data/a.dta", "script:code/x.do")]
    assert all("working_folder" not in o for occurrences in evidence.values() for o in occurrences)

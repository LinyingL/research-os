"""Tests for researchos.ingest.files (W1): the filesystem-walk fallback inventory
used when a project root is not a git repository. No subprocess `git`
involved here -- plain directories/files built via tmp_path, mirroring the
category assertions tests/test_ingest_git.py already makes for its git
counterpart.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from researchos import db, paths
from researchos.ingest import files as files_ingest
from researchos.ingest import git as git_ingest


def test_categorizes_every_tracked_extension(tmp_path: Path):
    (tmp_path / "paper.tex").write_text(r"\section{Intro}")
    (tmp_path / "refs.bib").write_text("@article{a,}")
    (tmp_path / "fig1.png").write_bytes(b"\x89PNG")
    (tmp_path / "plot.py").write_text("print('hi')\n")
    (tmp_path / "notes.md").write_text("# Notes\n")
    (tmp_path / "analysis.R").write_text("x <- 1\n")
    (tmp_path / "report.Rmd").write_text("---\ntitle: x\n---\n")
    (tmp_path / "clean.do").write_text("use data5\n")
    (tmp_path / "tools.ado").write_text("program define tools\nend\n")
    (tmp_path / "data1.csv").write_text("a,b\n1,2\n")
    (tmp_path / "data2.xlsx").write_bytes(b"PK\x03\x04")
    (tmp_path / "data7.xls").write_bytes(b"\xd0\xcf\x11\xe0")
    (tmp_path / "data3.parquet").write_bytes(b"PAR1")
    (tmp_path / "data4.rds").write_bytes(b"\x1f\x8b")
    (tmp_path / "data5.dta").write_bytes(b"<stata_dta>")
    (tmp_path / "data6.json").write_text("{}")
    (tmp_path / "readme.txt").write_text("not a tracked category")

    inventory = files_ingest.list_source_files(tmp_path)

    assert inventory["tex"] == ["paper.tex"]
    assert inventory["bib"] == ["refs.bib"]
    assert inventory["image"] == ["fig1.png"]
    assert inventory["py"] == ["plot.py"]
    assert inventory["md"] == ["notes.md"]
    assert inventory["r"] == ["analysis.R"]
    assert inventory["rmd"] == ["report.Rmd"]
    assert inventory["do"] == ["clean.do"]
    assert inventory["ado"] == ["tools.ado"]
    assert sorted(inventory["data"]) == sorted(
        ["data1.csv", "data2.xlsx", "data3.parquet", "data4.rds", "data5.dta", "data6.json", "data7.xls"]
    )
    all_listed = [p for paths in inventory.values() for p in paths]
    assert "readme.txt" not in all_listed


def test_recurses_into_ordinary_subdirectories(tmp_path: Path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "gen.py").write_text("print('x')\n")
    (tmp_path / "figs" / "sub").mkdir(parents=True)
    (tmp_path / "figs" / "sub" / "deep.png").write_bytes(b"\x89PNG")

    inventory = files_ingest.list_source_files(tmp_path)
    assert inventory["py"] == ["scripts/gen.py"]
    assert inventory["image"] == ["figs/sub/deep.png"]


@pytest.mark.parametrize(
    "noise_dir",
    [".git", ".researchos", "__pycache__", ".venv", "node_modules", ".Rproj.user", "_cache", ".ipynb_checkpoints"],
)
def test_skips_known_noise_directories(tmp_path: Path, noise_dir: str):
    (tmp_path / noise_dir).mkdir()
    (tmp_path / noise_dir / "hidden.py").write_text("print('should not be seen')\n")
    (tmp_path / "visible.py").write_text("print('ok')\n")

    inventory = files_ingest.list_source_files(tmp_path)
    assert inventory["py"] == ["visible.py"]


def test_skips_arbitrary_dot_prefixed_directories(tmp_path: Path):
    """Not every tool-cache directory can be enumerated in a fixed list
    (.mypy_cache, .idea, .pytest_cache, ...) -- any dot-prefixed directory
    name is skipped outright, not just the ones in the known-noise list."""
    (tmp_path / ".mypy_cache").mkdir()
    (tmp_path / ".mypy_cache" / "hidden.py").write_text("print('should not be seen')\n")
    (tmp_path / "visible.py").write_text("print('ok')\n")

    inventory = files_ingest.list_source_files(tmp_path)
    assert inventory["py"] == ["visible.py"]


def test_skips_hidden_files(tmp_path: Path):
    (tmp_path / ".hidden.py").write_text("print('should not be seen')\n")
    (tmp_path / "visible.py").write_text("print('ok')\n")

    inventory = files_ingest.list_source_files(tmp_path)
    assert inventory["py"] == ["visible.py"]


def test_does_not_follow_symlinked_directory(tmp_path: Path):
    real_dir = tmp_path / "real_figs"
    real_dir.mkdir()
    (real_dir / "linked.png").write_bytes(b"\x89PNG")
    link_dir = tmp_path / "linked_dir"
    try:
        os.symlink(real_dir, link_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this filesystem")

    inventory = files_ingest.list_source_files(tmp_path)
    # The real directory's own file is found via its real path...
    assert inventory["image"] == ["real_figs/linked.png"]
    # ...but never a second time by walking through the symlink to it.
    assert "linked_dir/linked.png" not in inventory["image"]


def test_does_not_follow_symlinked_file(tmp_path: Path):
    real_file = tmp_path / "real.png"
    real_file.write_bytes(b"\x89PNG")
    link_file = tmp_path / "linked.png"
    try:
        os.symlink(real_file, link_file)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this filesystem")

    inventory = files_ingest.list_source_files(tmp_path)
    assert inventory["image"] == ["real.png"]


def test_empty_directory_returns_all_empty_categories(tmp_path: Path):
    inventory = files_ingest.list_source_files(tmp_path)
    assert inventory == {
        "tex": [], "bib": [], "image": [], "py": [], "md": [], "r": [], "rmd": [],
        "do": [], "ado": [], "data": [],
    }


# --- V15: the project scan leaves out the work folder and the files registered as work (DESIGN.md 19.7) ----------


def _put(root: Path, rel: str, text: str = "x = 1\n") -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


def _register(root: Path, name: str, *items: tuple[str, str]) -> None:
    """A task's output record, as `outputs.register` writes it (the parts the scan reads)."""
    body = {"version": 1, "items": [{"kind": kind, "path": rel, "title": rel, "fingerprint": {}} for kind, rel in items]}
    _put(root, f"research/results/{name}.outputs.json", json.dumps(body))


def _scripts(root: Path) -> list[str]:
    return files_ingest.list_source_files(root)["py"]


def test_the_default_work_folder_is_skipped_like_a_noise_folder_but_the_walk_itself_lists_it(tmp_path):
    for rel in ("src/a.py", "work/20261025-probe/probe.py", "work/20261025-probe/sub/more.py", "Work/20261024-x/loud.py",
                "worker/real.py", "src/work/real_too.py"):
        _put(tmp_path, rel)
    _put(tmp_path, "work/20261025-probe/out.csv", "a\n1\n")
    inventory = files_ingest.list_source_files(tmp_path)
    assert inventory["py"] == ["src/a.py", "src/work/real_too.py", "worker/real.py"] and inventory["data"] == []
    # iter_files is shared with the add-project preview, which counts every file it would not scan as a noise folder
    assert {rel for rel, _c in files_ingest.iter_files(tmp_path)} >= {"work/20261025-probe/probe.py", "work/20261025-probe/out.csv"}


def test_the_configured_work_folder_replaces_the_default(tmp_path):
    for rel in ("process/runs/a.py", "work/b.py", "src/c.py"):
        _put(tmp_path, rel)
    folders = {"code": "src", "data": "data", "figure": "figures", "table": "tables", "literature": "literature",
               "document": "reports", "variable": "research/variables", "work": "process/runs"}
    _put(tmp_path, "research/folders.json", json.dumps({"version": 1, "folders": folders}))
    assert files_ingest.work_folder(tmp_path) == "process/runs"
    assert _scripts(tmp_path) == ["src/c.py", "work/b.py"]
    old = {k: v for k, v in folders.items() if k != "work"}  # a file saved before the work location existed
    _put(tmp_path, "research/folders.json", json.dumps({"version": 1, "folders": old}))
    assert files_ingest.work_folder(tmp_path) == "work" and _scripts(tmp_path) == ["process/runs/a.py", "src/c.py"]


@pytest.mark.parametrize("settings", ["not json", "[]", "{}", '{"version": 2, "folders": {"work": "x"}}',
                                      '{"version": 1, "folders": []}', '{"version": 1, "folders": {"work": 3}}',
                                      '{"version": 1, "folders": {"work": ""}}', '{"version": 1, "folders": {"work": "../x"}}',
                                      '{"version": 1, "folders": {"work": "/etc"}}', '{"version": 1, "folders": {"work": "a/./b"}}'])
def test_unreadable_folder_settings_mean_the_default_work_folder_and_never_fail_the_scan(tmp_path, settings):
    _put(tmp_path, "work/a.py")
    _put(tmp_path, "src/b.py")
    _put(tmp_path, "research/folders.json", settings)
    assert files_ingest.work_folder(tmp_path) == "work" and _scripts(tmp_path) == ["src/b.py"]


def test_files_registered_as_work_are_skipped_wherever_they_are_and_other_kinds_are_not(tmp_path):
    for rel in ("scripts/probe.py", "scripts/real.py", "src/Check.py", "scripts/output_code.py"):
        _put(tmp_path, rel)
    _register(tmp_path, "a" * 32, ("work", "scripts/probe.py"), ("code", "scripts/real.py"))
    _register(tmp_path, "b" * 32, ("work", "src/check.py"), ("code", "scripts/output_code.py"))  # another case: same file here
    assert files_ingest.registered(tmp_path) == {
        "work": frozenset({"scripts/probe.py", "src/check.py"}), "code": frozenset({"scripts/real.py", "scripts/output_code.py"})}
    assert _scripts(tmp_path) == ["scripts/output_code.py", "scripts/real.py"]
    probe = files_ingest.scan_skips(tmp_path)
    assert probe("scripts/probe.py") and probe("SCRIPTS/Probe.py") and not probe("scripts/real.py") and not probe("work")


def test_a_record_that_cannot_be_read_registers_nothing_and_the_others_still_count(tmp_path):
    for rel in ("one.py", "two.py", "three.py"):
        _put(tmp_path, rel)
    _put(tmp_path, "research/results/" + "a" * 32 + ".outputs.json", "not json")
    _put(tmp_path, "research/results/" + "b" * 32 + ".outputs.json", '{"version": 1, "items": [3, {"kind": 4}, {"kind": "work"}, '
                                                                    '{"kind": "work", "path": 5}, {"kind": "work", "path": "two.py"}]}')
    _put(tmp_path, "research/results/" + "c" * 32 + ".outputs.json", '{"items": "x"}')
    _put(tmp_path, "research/results/notes.txt", "ignored")
    assert files_ingest.registered(tmp_path) == {"work": frozenset({"two.py"})}
    assert _scripts(tmp_path) == ["one.py", "three.py"]
    assert files_ingest.registered(tmp_path / "nowhere") == {}


def test_a_git_project_leaves_the_work_folder_and_registered_work_out_of_the_scan_but_not_out_of_the_preview(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel in ("src/a.py", "work/20261025-probe/probe.py", "tools/probe2.py", "tools/keep.py", "work/20261025-probe/out.csv"):
        _put(repo, rel, "x = 1\n" if rel.endswith(".py") else "a\n1\n")
    _register(repo, "a" * 32, ("work", "tools/probe2.py"))
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True, capture_output=True)
    subprocess.run([*git, "add", "-A"], check=True, capture_output=True)
    subprocess.run([*git, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i"], check=True, capture_output=True)
    inventory = git_ingest.list_source_files(repo)
    assert inventory["py"] == ["src/a.py", "tools/keep.py"]
    assert inventory["data"] == ["research/results/" + "a" * 32 + ".outputs.json"]  # the record itself is a tracked file
    assert {rel for rel, _c in git_ingest.iter_tracked(repo)} >= {"work/20261025-probe/probe.py", "tools/probe2.py"}


def test_researchos_ingest_scans_no_process_file_and_draws_no_claim_from_a_registered_document(tmp_path, capsys):
    """DESIGN 19.7 end to end: the scan of a project that had a task run. Its paper is read; the plan an agent was asked
    to write (registered as a document), the agent's probe script and a note in the work folder, and a script registered
    as work are not."""
    from researchos import cli

    project = tmp_path / "proj"
    project.mkdir()
    sentence = "# Results\n\nOur model achieves 87.3% accuracy.\n"
    _put(project, "paper.md", sentence)
    _put(project, "reports/plan.md", sentence.replace("Results", "Plan"))
    _put(project, "work/20261025-probe/notes.md", sentence.replace("Results", "Scratch"))
    _put(project, "work/20261025-probe/probe.py", "import pandas as pd\npd.read_csv('data/in.csv')\n")
    _put(project, "tools/odd.py", "import pandas as pd\npd.read_csv('data/odd.csv')\n")
    _put(project, "tools/real.py", "import pandas as pd\npd.read_csv('data/in.csv')\n")
    _register(project, "a" * 32, ("document", "reports/plan.md"), ("work", "tools/odd.py"))
    run_dir = project / "mlruns" / "0" / "run_a"
    (run_dir / "metrics").mkdir(parents=True)
    (run_dir / "meta.yaml").write_text("experiment_id: '0'\nrun_id: run_a\nstatus: FINISHED\n")
    (run_dir / "metrics" / "accuracy").write_text("0 0.873 0\n")
    cli.main(["init", str(project)])
    capsys.readouterr()
    assert cli.main(["ingest", str(project)]) == 0
    out = capsys.readouterr().out
    assert "mdpaper: 2 .md scanned (1 skipped as README/CHANGELOG/LICENSE or in research/)" in out  # plan.md; work/ never listed
    conn = db.connect(paths.graph_db_path(project))
    try:
        sections = [n["id"] for n in db.get_nodes_by_type(conn, "section")]
        assert sections == ["section:paper.md#results"]
        assert [c["attrs"]["tex_path"] for c in db.get_nodes_by_type(conn, "claim")] == ["paper.md"]
        every = [n["id"] for t in ("script", "dataset", "section", "claim") for n in db.get_nodes_by_type(conn, t)]
        assert not any("work/" in i or "probe" in i or "odd" in i or "plan.md" in i for i in every), every
        assert any("tools/real.py" in i for i in every), every
    finally:
        conn.close()

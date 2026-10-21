"""A new project starts with an empty writing desk, not imported history."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from researchos import db, newproject, paths, project, workspace
from researchos.ingest import attempts
from researchos.webapp import mapedit, registry


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def _body(directory: Path, **changes) -> dict:
    return {"name": "New research", "parent": str(directory), "language": "en", **changes}


@pytest.mark.parametrize("language,heading", [("en", "Research attempts"), ("zh", "研究尝试")])
def test_new_project_needs_no_history_and_first_attempt_can_be_saved(tmp_path, language, heading):
    result = newproject.create(_body(tmp_path, language=language, name="研究 topic"))
    root = Path(result["path"])
    assert result == {"path": str(tmp_path.resolve() / "研究 topic"), "id": result["id"], "label": "研究 topic"}
    assert (root / "research/overview.md").is_file()
    config = attempts.load_config(root)
    assert config.file == "research/attempts.md"
    assert config.heading == heading
    assert config.steps_dir is None
    table = (root / config.file).read_text(encoding="utf-8")
    assert attempts.parse_attempts_table(table, config.heading, config.columns) == []
    assert registry.load()[0]["id"] == result["id"]
    assert registry.load()[0]["path"] == str(root)

    with db.connect(paths.graph_db_path(root)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE type='attempt'").fetchone()[0] == 0
    saved = mapedit.apply_edit(root, "append", "1", {
        "date": "2026-10-21", "description": "Check the first idea", "variables": "",
        "result": "", "verdict": "",
    })
    assert saved["ingest_error"] is None
    rows = attempts.parse_attempts_table((root / config.file).read_text(), config.heading, config.columns)
    assert [row.number for row in rows] == ["1"]
    assert rows[0].description == "Check the first idea"


@pytest.mark.parametrize("existing", ["empty", "files", "symlink", "dangling"])
def test_creation_never_reuses_an_existing_name(tmp_path, existing):
    root = tmp_path / "New research"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    source = elsewhere / "analysis.do"
    source.write_text('display "keep me"\n')
    if existing in {"empty", "files"}:
        root.mkdir()
        if existing == "files":
            (root / "plan.md").write_text("My original plan")
    else:
        root.symlink_to(elsewhere if existing == "symlink" else tmp_path / "missing", target_is_directory=True)
    with pytest.raises(newproject.NewProjectError) as caught:
        newproject.create(_body(tmp_path))
    assert caught.value.code == "exists"
    assert caught.value.status == 409
    assert source.read_text() == 'display "keep me"\n'
    if existing == "files":
        assert (root / "plan.md").read_text() == "My original plan"
    assert not (elsewhere / ".researchos").exists()
    assert registry.load() == []


def test_concurrent_creation_has_one_winner(tmp_path):
    def create_one(_):
        try:
            return newproject.create(_body(tmp_path))
        except newproject.NewProjectError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create_one, range(4)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert [r.code for r in results if isinstance(r, newproject.NewProjectError)] == ["exists"] * 3
    assert len(registry.load()) == 1


@pytest.mark.parametrize("name", ["", " ", ".", "..", "../escape", "a/b", "a\\b", ".researchos", ".git",
                                 "bad\nname", "tab\t", "zero\0", "x\u2028y", "\ud800", "x" * 101, None])
def test_invalid_names_cannot_create_anything(tmp_path, name):
    with pytest.raises(newproject.NewProjectError) as caught:
        newproject.create(_body(tmp_path, name=name))
    assert caught.value.code == "invalid"
    assert [p.name for p in tmp_path.iterdir()] == ["user-home"]
    assert registry.load() == []


@pytest.mark.parametrize("language", [[], {}, None, "de"])
def test_invalid_language_is_a_clean_refusal(tmp_path, language):
    with pytest.raises(newproject.NewProjectError) as caught:
        newproject.create(_body(tmp_path, language=language))
    assert caught.value.code == "invalid"
    assert [p.name for p in tmp_path.iterdir()] == ["user-home"]


def test_save_location_must_be_an_existing_absolute_directory(tmp_path):
    file = tmp_path / "file"
    file.write_text("preserved")
    for parent in ("relative", str(tmp_path / "missing"), str(file), str(tmp_path / ".."), "/", "/usr"):
        with pytest.raises(newproject.NewProjectError):
            newproject.create(_body(tmp_path, parent=parent))
    assert {p.name for p in tmp_path.iterdir()} == {"file", "user-home"}
    assert file.read_text() == "preserved"


def test_cannot_nest_under_unregistered_project_or_global_engine_home(tmp_path):
    root = tmp_path / "Original"
    root.mkdir()
    project.init_project(root)
    inside = root / "data"
    inside.mkdir()
    assert registry.load() == []
    for parent in (root, inside, paths.researchos_home()):
        with pytest.raises(newproject.NewProjectError) as caught:
            newproject.create(_body(parent))
        assert caught.value.code in {"inside_project", "researchos_home"}
        assert not (parent / "New research").exists()


def test_registered_legacy_folder_is_also_protected(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    child = legacy / "analysis"
    child.mkdir()
    registry.register(legacy, label="Existing work")
    before = registry.registry_path().read_bytes()
    with pytest.raises(newproject.NewProjectError) as caught:
        newproject.create(_body(child))
    assert caught.value.code == "inside_project"
    assert registry.registry_path().read_bytes() == before
    assert list(child.iterdir()) == []


def test_parent_alias_is_resolved_but_alias_to_project_internals_is_refused(tmp_path):
    parent = tmp_path / "projects"
    parent.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)
    result = newproject.create(_body(alias))
    assert result["path"] == str(parent / "New research")
    internal = tmp_path / "internal-alias"
    internal.symlink_to(Path(result["path"]) / ".researchos", target_is_directory=True)
    with pytest.raises(newproject.NewProjectError):
        newproject.create(_body(internal, name="Nested"))
    assert not (Path(result["path"]) / ".researchos/Nested").exists()


def test_parent_swap_during_mkdir_cannot_write_into_link_target(tmp_path, monkeypatch):
    parent = tmp_path / "projects"
    parent.mkdir()
    moved = tmp_path / "moved"
    outside = tmp_path / "unrelated"
    outside.mkdir()
    real_mkdir = os.mkdir

    def swap(path, *args, **kwargs):
        if path == "New research" and kwargs.get("dir_fd") is not None:
            parent.rename(moved)
            parent.symlink_to(outside, target_is_directory=True)
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", swap)
    with pytest.raises(newproject.NewProjectError) as caught:
        newproject.create(_body(parent))
    assert caught.value.code == "incomplete"
    assert list(outside.iterdir()) == []
    assert (moved / "New research").is_dir()
    assert list((moved / "New research").iterdir()) == []
    assert registry.load() == []


def test_root_swap_before_identity_creation_is_refused(tmp_path, monkeypatch):
    outside = tmp_path / "unrelated"
    outside.mkdir()
    real_init = project.init_project

    def swap(root, **kwargs):
        root.rename(tmp_path / "moved")
        root.symlink_to(outside, target_is_directory=True)
        return real_init(root, **kwargs)

    monkeypatch.setattr(project, "init_project", swap)
    with pytest.raises(newproject.NewProjectError) as caught:
        newproject.create(_body(tmp_path))
    assert caught.value.code == "incomplete"
    assert list(outside.iterdir()) == []
    assert registry.load() == []


def test_setup_failure_keeps_new_files_and_reports_recoverable_location(tmp_path, monkeypatch):
    sibling = tmp_path / "keep.do"
    sibling.write_text("original analysis")

    def fail(root, **_):
        (root / "user-added.md").write_text("Research written while setup was running")
        raise OSError("disk full")

    monkeypatch.setattr(workspace, "initialize", fail)
    with pytest.raises(newproject.NewProjectError) as caught:
        newproject.create(_body(tmp_path))
    root = tmp_path / "New research"
    assert caught.value.code == "incomplete"
    assert str(root) in str(caught.value)
    assert (root / "user-added.md").read_text() == "Research written while setup was running"
    assert (root / ".researchos/project.toml").is_file()
    assert sibling.read_text() == "original analysis"
    assert registry.load() == []


def test_an_attempt_file_added_during_setup_is_preserved(tmp_path, monkeypatch):
    real_initialize = workspace.initialize

    def add_file(root, **kwargs):
        real_initialize(root, **kwargs)
        (root / "research/attempts.md").write_text("A hand-written research log")

    monkeypatch.setattr(workspace, "initialize", add_file)
    with pytest.raises(newproject.NewProjectError) as caught:
        newproject.create(_body(tmp_path))
    assert caught.value.code == "incomplete"
    assert (tmp_path / "New research/research/attempts.md").read_text() == "A hand-written research log"
    assert registry.load() == []


def test_failed_registration_cannot_be_reported_as_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "register", lambda *a, **kw: None)
    with pytest.raises(newproject.NewProjectError) as caught:
        newproject.create(_body(tmp_path))
    assert caught.value.code == "incomplete"
    assert (tmp_path / "New research/research/overview.md").is_file()
    assert registry.load() == []


def test_documents_is_a_save_location_and_unrelated_home_folders_are_not_opened(tmp_path, fake_home, monkeypatch):
    home = Path.home()
    documents = home / "Documents"
    documents.mkdir()
    (home / ".researchos").symlink_to(paths.researchos_home(), target_is_directory=True)
    real_open = os.open

    def selected_only(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and Path(path) in {home / "Desktop", home / "Downloads", home / "Library"}:
            raise AssertionError("New-project setup opened an unrelated user folder")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", selected_only)
    result = newproject.create(_body(documents))
    assert Path(result["path"]) == documents / "New research"
    assert (Path(result["path"]) / "research/overview.md").is_file()


def test_save_location_preserves_trailing_spaces_in_existing_folder_name(tmp_path):
    location = tmp_path / "Chosen folder "
    location.mkdir()
    result = newproject.create(_body(location))
    assert result["path"] == str(location / "New research")

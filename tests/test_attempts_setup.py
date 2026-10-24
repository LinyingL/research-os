"""First-attempt setup for a project opened from an existing folder."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from researchos import db, paths, project, workspace
from researchos.ingest import attempts
from researchos.webapp import mapedit
from test_webapp_server import _mk_attempt


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "Existing research"
    root.mkdir()
    project.init_project(root)
    return root


@pytest.mark.parametrize("language,heading", [("zh", "研究尝试"), ("en", "Research attempts")])
def test_first_attempt_in_existing_folder_needs_no_import(root, language, heading):
    assert not attempts.config_path(root).exists()
    result = workspace.initialize_attempts(root, language)
    assert result == {"created": True, "file": "research/attempts.md"}
    config = attempts.load_config(root)
    assert config.heading == heading
    assert attempts.parse_attempts_table((root / config.file).read_text(), heading, config.columns) == []
    assert not (root / workspace.OVERVIEW_PATH).exists()
    mapedit.apply_edit(root, "append", "1", {"description": "Compare paper plane designs", "date": "2026-10-24"})
    rows = attempts.parse_attempts_table((root / config.file).read_text(), heading, config.columns)
    assert [row.number for row in rows] == ["1"]
    assert rows[0].description == "Compare paper plane designs"
    before = (root / config.file).read_bytes(), attempts.config_path(root).read_bytes()
    assert workspace.initialize_attempts(root, "zh")["created"] is False
    assert ((root / config.file).read_bytes(), attempts.config_path(root).read_bytes()) == before


def test_existing_unconfigured_markdown_is_preserved(root):
    (root / "research").mkdir()
    original = root / "research/attempts.md"
    original.write_text("My existing research history\n")
    result = workspace.initialize_attempts(root, "en")
    assert result["file"] == "research/attempts-2.md"
    assert original.read_text() == "My existing research history\n"
    assert attempts.load_config(root).file == result["file"]


def test_invalid_config_is_never_replaced(root):
    config = attempts.config_path(root)
    config.write_text("file = [broken")
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.initialize_attempts(root)
    assert caught.value.code == "attempts_config"
    assert config.read_text() == "file = [broken"
    assert not (root / "research").exists()


def test_missing_config_with_indexed_history_does_not_create_a_new_empty_table(root):
    conn = db.connect(paths.graph_db_path(root))
    try:
        db.migrate(conn)
        _mk_attempt(conn, "old-history.md", "1")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.initialize_attempts(root)
    assert caught.value.code == "attempts_config"
    assert not attempts.config_path(root).exists()
    assert not (root / "research").exists()


def test_simultaneous_first_opens_create_only_one_table(root):
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: workspace.initialize_attempts(root, "en"), range(3)))
    assert sum(result["created"] for result in results) == 1
    assert {result["file"] for result in results} == {"research/attempts.md"}
    assert sorted(path.name for path in (root / "research").iterdir()) == ["attempts.md"]


def test_setup_refuses_linked_research_folder_and_team_members(root, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "research").symlink_to(outside, target_is_directory=True)
    with pytest.raises(workspace.WorkspaceError):
        workspace.initialize_attempts(root)
    assert list(outside.iterdir()) == []
    assert not attempts.config_path(root).exists()
    (root / "research").unlink()
    monkeypatch.setattr(workspace, "_require_write", lambda _: "test")
    monkeypatch.setattr(workspace.proposals, "role_of", lambda _: ("member", None, None))
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.initialize_attempts(root)
    assert caught.value.status == 403
    assert not attempts.config_path(root).exists()

"""The writing desk saves real project files and obeys existing team rules."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from researchos import addproject, db, history, paths, people, project, proposals, share, signing, workspace
from researchos.records.identity import read_identity


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    return root


def _new(root: Path, kind: str = "note", content: str = "An open question") -> dict:
    return workspace.save_document(root, {"kind": kind, "title": "Next steps", "content": content, "revision": None})


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_empty_workspace_reads_do_not_create_files_or_history(root):
    before = _files(root)
    pid = read_identity(root).identity.id
    out = workspace.overview(root, lang="en")
    page = workspace.document(root, workspace.OVERVIEW_PATH)
    assert page["revision"] is None and page["content"] == ""
    assert out["documents"] == [{"path": workspace.OVERVIEW_PATH, "kind": "overview",
                                  "title": "Research question and plan", "excerpt": "", "modified": None,
                                  "officialness": {"state": "official", "pending_known": False}}]
    assert out["tasks"] == out["files"] == out["attempts"] == []
    assert out["tasks_revision"] is None and out["mode"] == "single"
    assert _files(root) == before
    assert not history.history_dir(pid).exists()


def test_initialize_creates_only_overview_and_never_overwrites(root):
    before = set(_files(root))
    first = workspace.initialize(root, language="en")
    assert first["created"] and first["content"] == "# Research question and plan\n\n"
    assert set(_files(root)) - before == {workspace.OVERVIEW_PATH}
    (root / workspace.OVERVIEW_PATH).write_text("A hand-written research question\n")
    second = workspace.initialize(root)
    assert not second["created"] and second["content"] == "A hand-written research question\n"


def test_save_and_reopen_note_preserves_markdown_links_and_raw_text(root):
    content = "## Questions\n\n- [ ] Try another sample\n\n![Plot](figures/main.png)\n[Stata](code/model.do)\n"
    saved = _new(root, content=content)
    assert saved["created"] and saved["backup"] is None
    assert saved["path"].startswith("research/notes/")
    assert saved["content"] == "# Next steps\n\n" + content
    assert workspace.document(root, saved["path"]) == {k: saved[k] for k in ("path", "kind", "title", "content", "revision")}
    assert saved["revision"] == hashlib.sha256((root / saved["path"]).read_bytes()).hexdigest()
    edited = workspace.save_document(root, {"path": saved["path"], "content": "Free form\n", "revision": saved["revision"]})
    assert not edited["created"] and edited["content"] == "Free form\n"
    assert (root / edited["backup"]).read_text() == saved["content"]


def test_every_save_has_history_including_uncommitted_git_files(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    first = workspace.initialize(root)
    subprocess.run(["git", "-C", str(root), "add", workspace.OVERVIEW_PATH], check=True)
    second = workspace.save_document(root, {"path": first["path"], "content": "# Revised plan\n", "revision": first["revision"]})
    pid = read_identity(root).identity.id
    with history.Store.open(pid) as store:
        versions = store.versions("file:" + workspace.OVERVIEW_PATH)
        assert len(versions) == 2
        assert store.read_blob(versions[0].sha256).decode() == second["content"]
        assert store.read_blob(versions[1].sha256).decode() == first["content"]
    assert (root / second["backup"]).read_text() == first["content"]
    daily = [p for p in (root / ".researchos/backups").iterdir() if p.name.startswith("overview.") and ".write." not in p.name]
    assert len(daily) == 1


def test_conflict_and_null_revision_never_replace_existing_content(root):
    first = _new(root)
    target = root / first["path"]
    target.write_text("Edited in another application\n")
    before = _files(root)
    for revision in (first["revision"], None):
        with pytest.raises(workspace.WorkspaceError) as exc:
            workspace.save_document(root, {"path": first["path"], "content": "Old editor", "revision": revision})
        assert exc.value.code == "conflict" and exc.value.status == 409
    assert _files(root) == before


def test_deleted_document_cannot_be_resurrected_by_stale_save(root):
    first = _new(root)
    (root / first["path"]).unlink()
    with pytest.raises(workspace.WorkspaceError, match="changed"):
        workspace.save_document(root, {"path": first["path"], "content": "stale", "revision": first["revision"]})
    assert not (root / first["path"]).exists()


def test_existing_title_edit_and_heading_edited_directly_both_persist(root):
    first = _new(root)
    renamed = workspace.save_document(root, {"path": first["path"], "title": "A different direction",
                                             "content": first["content"], "revision": first["revision"]})
    assert renamed["title"] == "A different direction"
    assert renamed["content"] == "# A different direction\n\nAn open question"
    text_edited = workspace.save_document(root, {"path": first["path"], "title": renamed["title"],
                                                 "content": "# Heading edited directly\n\nMore thoughts",
                                                 "revision": renamed["revision"]})
    assert text_edited["title"] == "Heading edited directly"
    assert text_edited["content"] == "# Heading edited directly\n\nMore thoughts"


def test_one_unreadable_document_and_invalid_tasks_do_not_blank_workspace(root):
    first = _new(root)
    good = _new(root, "result", "A useful result")
    (root / first["path"]).write_bytes(b"binary\0content")
    (root / workspace.TASKS_PATH).write_text("not json")
    out = workspace.overview(root, lang="en")
    broken = next(doc for doc in out["documents"] if doc["path"] == first["path"])
    assert broken["error"]["code"] == "binary"
    assert any(doc["path"] == good["path"] and "error" not in doc for doc in out["documents"])
    assert out["tasks_error"]["code"] == "unreadable" and out["tasks"] == []
    assert out["can_write"]
    with pytest.raises(workspace.WorkspaceError):
        workspace.save_tasks(root, {"tasks": [], "revision": None})
    assert (root / workspace.TASKS_PATH).read_text() == "not json"


def test_history_failure_after_save_reports_saved_content_and_warning(root, monkeypatch):
    first = _new(root)
    original = workspace._history
    calls = []

    def fail_after_write(*args):
        calls.append(True)
        if len(calls) == 2:
            raise workspace.WorkspaceError("history", "Temporary history failure", 409)
        return original(*args)

    monkeypatch.setattr(workspace, "_history", fail_after_write)
    saved = workspace.save_document(root, {"path": first["path"], "content": "New idea\n", "revision": first["revision"]})
    assert saved["history_error"] == "Temporary history failure"
    assert saved["content"] == (root / saved["path"]).read_text() == "New idea\n"
    assert (root / saved["backup"]).read_text() == first["content"]


def test_backup_failure_preserves_source_and_is_a_localized_refusal(root, monkeypatch):
    first = _new(root)

    def no_space(*args):
        raise OSError("Disk full")

    monkeypatch.setattr(workspace.mapedit, "write_backup_bytes", no_space)
    with pytest.raises(workspace.WorkspaceError) as exc:
        workspace.save_document(root, {"path": first["path"], "content": "Changed", "revision": first["revision"]})
    assert exc.value.code == "write_failed" and exc.value.message_zh
    assert (root / first["path"]).read_text() == first["content"]


@pytest.mark.parametrize("rel", ["../outside.md", "/tmp/outside.md", "research/../plan.md", "plan.md",
                                "research/notes/handwritten.md", "research/attempts.md", "research/tasks.json",
                                "research/results/a" + "0" * 31 + ".md/../../oops"])
def test_unmanaged_paths_cannot_be_read_or_written(root, rel):
    before = _files(root)
    for action in (lambda: workspace.document(root, rel), lambda: workspace.save_document(
        root, {"path": rel, "content": "Overwrite", "revision": None},
    )):
        with pytest.raises(workspace.WorkspaceError) as exc:
            action()
        assert exc.value.code == "path"
    assert _files(root) == before


def test_links_and_cloud_placeholders_are_not_written_or_materialized(root, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "research").symlink_to(outside, target_is_directory=True)
    with pytest.raises(workspace.WorkspaceError) as exc:
        workspace.initialize(root)
    assert exc.value.code == "path" and not list(outside.iterdir())
    (root / "research").unlink()
    workspace.initialize(root)
    target = root / workspace.OVERVIEW_PATH
    monkeypatch.setattr(paths, "is_dataless", lambda p: Path(p) == target)
    with pytest.raises(workspace.WorkspaceError) as exc:
        workspace.document(root, workspace.OVERVIEW_PATH)
    assert exc.value.code == "cloud"
    monkeypatch.setattr(paths, "is_dataless", lambda p: False)
    target.unlink()
    target.with_name(f".{target.name}.icloud").touch()
    with pytest.raises(workspace.WorkspaceError) as exc:
        workspace.save_document(root, {"kind": "overview", "content": "New", "revision": None})
    assert exc.value.code == "cloud" and not target.exists()


def test_symlink_file_and_backup_directory_are_refused(root, tmp_path):
    first = workspace.initialize(root)
    target = root / first["path"]
    original = target.read_bytes()
    other = tmp_path / "other.md"
    other.write_bytes(original)
    target.unlink()
    target.symlink_to(other)
    with pytest.raises(workspace.WorkspaceError) as exc:
        workspace.document(root, first["path"])
    assert exc.value.code == "path"
    target.unlink()
    target.write_bytes(original)
    backups = root / ".researchos/backups"
    # Project identity initialization already has its own snapshots here.
    moved = root / ".researchos/old-backups"
    backups.rename(moved)
    backups.symlink_to(moved, target_is_directory=True)
    with pytest.raises(workspace.WorkspaceError) as exc:
        workspace.save_document(root, {"path": first["path"], "content": "changed", "revision": first["revision"]})
    assert exc.value.code == "path" and target.read_bytes() == original


@pytest.mark.parametrize("raw,code", [(b"\xff\xfe", "binary"), (b"text\0binary", "binary"),
                                     (b"x" * (workspace.TEXT_LIMIT + 1), "too_large")])
def test_binary_and_oversize_documents_are_rejected_without_overwrite(root, raw, code):
    (root / "research").mkdir()
    target = root / workspace.OVERVIEW_PATH
    target.write_bytes(raw)
    with pytest.raises(workspace.WorkspaceError) as exc:
        workspace.document(root, workspace.OVERVIEW_PATH)
    assert exc.value.code == code
    assert target.read_bytes() == raw


def test_tasks_are_optional_independent_and_revision_checked(root):
    tasks = [{"id": "sample", "text": "Check the sample definition", "owner": "Dr Li", "due": "2026-11-04", "done": False}]
    first = workspace.save_tasks(root, {"tasks": tasks, "revision": None})
    assert json.loads((root / workspace.TASKS_PATH).read_text()) == tasks
    assert not (root / workspace.OVERVIEW_PATH).exists()
    assert workspace.overview(root)["tasks"] == tasks
    updated = [dict(tasks[0], done=True)]
    second = workspace.save_tasks(root, {"tasks": updated, "revision": first["tasks_revision"]})
    assert second["tasks"] == updated
    assert json.loads((root / second["backup"]).read_text()) == tasks
    with pytest.raises(workspace.WorkspaceError) as exc:
        workspace.save_tasks(root, {"tasks": [], "revision": first["tasks_revision"]})
    assert exc.value.code == "conflict"
    assert json.loads((root / workspace.TASKS_PATH).read_text()) == updated


@pytest.mark.parametrize("tasks", [[{"id": "x", "text": "x", "due": "2026-02-30"}],
                                  [{"id": "x", "text": "x", "done": "false"}],
                                  [{"id": "x", "text": "x", "owner": []}],
                                  [{"id": "x", "text": "x"}] * 2])
def test_invalid_task_data_never_replaces_existing_file(root, tasks):
    first = workspace.save_tasks(root, {"tasks": [], "revision": None})
    with pytest.raises(workspace.WorkspaceError):
        workspace.save_tasks(root, {"tasks": tasks, "revision": first["tasks_revision"]})
    assert json.loads((root / workspace.TASKS_PATH).read_text()) == []


def test_inventory_links_visible_files_without_reading_their_content(root, tmp_path, monkeypatch):
    (root / "code").mkdir()
    (root / "code/model.do").write_text('regress y x\n')
    (root / "fig.png").write_bytes(b"\x89PNG")
    (root / ".secret").write_text("hidden")
    (root / "linked.py").symlink_to(tmp_path / "external.py")
    original_read = Path.read_bytes

    def forbidden(path):
        if path == root / "fig.png" or path == root / "code/model.do":
            pytest.fail("File inventory must not read file contents")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    out = workspace.overview(root)
    assert out["files"] == [{"path": "fig.png", "type": "image"}, {"path": "code/model.do", "type": "do"}]
    monkeypatch.setattr(workspace, "FILE_LIMIT", 1)
    assert workspace.overview(root)["files_truncated"]


def test_no_save_creates_or_approves_an_attempt_node(root):
    _new(root, "result", "A preliminary finding; reasons for doubt remain.")
    workspace.save_tasks(root, {"tasks": [{"id": "try", "text": "Try later"}], "revision": None})
    with db.connect(paths.graph_db_path(root)) as conn:
        assert db.get_nodes_by_type(conn, "attempt") == []


def test_unknown_team_and_nonmember_cannot_save(root):
    team_path = people.team_path(root)
    team_path.write_text("invalid toml[")
    for action in (lambda: _new(root), lambda: workspace.save_tasks(root, {"tasks": [], "revision": None})):
        with pytest.raises(workspace.WorkspaceError) as exc:
            action()
        assert exc.value.code == "read_only"
    team_path.write_text('[[member]]\nid="pi@example.com"\nname="PI"\nrole="pi"\n\n'
                         '[[member]]\nid="member@example.com"\nname="Member"\nrole="member"\n')
    with pytest.raises(workspace.WorkspaceError) as exc:
        _new(root)
    assert exc.value.code == "read_only"
    people.write_me("Member", "member@example.com")
    with pytest.raises(workspace.WorkspaceError) as exc:
        _new(root)
    assert exc.value.code == "read_only"
    assert not (root / "research").exists()


@pytest.mark.skipif(signing.tool() is None, reason="ssh-keygen is not available")
def test_member_writing_uses_existing_proposal_approval_and_snapshot_flow(tmp_path, monkeypatch):
    from test_v8_phase4 import Drive

    # This test's exchange is a fresh tmp_path child; do not inspect the
    # developer's real cloud roots merely to validate that location.
    monkeypatch.setattr(addproject, "_top_level_folders", lambda: set())
    drive = Drive(tmp_path, monkeypatch)
    with drive.member():
        project.open_project(drive.work)
        note = _new(drive.work, "result", "![Result](figs/fig2.png)\n[Analysis](steps/17-plot.py)")
        task = workspace.save_tasks(drive.work, {"tasks": [{"id": "check", "text": "Discuss this result"}], "revision": None})
        # Homepage badges compare these small files to the local base only;
        # touching the published area or hashing a dataset is unnecessary.
        def unexpected_inventory(*args, **kwargs):
            pytest.fail("Workspace annotations must not scan or fingerprint the whole project")
        with monkeypatch.context() as local:
            local.setattr(share, "official_files", unexpected_inventory)
            local.setattr(proposals, "manifest", unexpected_inventory)
            marked = workspace.overview(drive.work, lang="en")
        entry = next(doc for doc in marked["documents"] if doc["path"] == note["path"])
        assert entry["officialness"] == {"state": "local", "label": "Local draft", "pending_known": False}
        assert marked["tasks_officialness"]["state"] == "local"
        draft = proposals.make_proposal(drive.work, "files", "Add a result note and a follow-up task", only=[note["path"], workspace.TASKS_PATH])
        carried = {item["path"] for item in draft.data["file"]}
        assert carried == {note["path"], workspace.TASKS_PATH}
        stored = proposals.write_proposal(drive.work, draft)
    assert not (drive.official / note["path"]).exists()
    assert not (drive.official / workspace.TASKS_PATH).exists()
    with drive.pi():
        assert workspace.overview(drive.official)["mode"] == "pi"
        proposals.approve(drive.official, stored.id)
        assert (drive.official / note["path"]).read_text() == note["content"]
        assert workspace.overview(drive.official)["tasks"] == task["tasks"]
        # The next published snapshot includes both ordinary project files.
        published = share.publish(drive.official)
        assert published["seq"] >= 2
        assert workspace.TASKS_PATH in proposals.manifest(drive.official)
        assert note["path"] in proposals.manifest(drive.official)

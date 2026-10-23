"""The writing desk saves real project files and obeys existing team rules."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
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
                                  "officialness": {"state": "official"}}]
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
                                "research/notes/sub/handwritten.md", "research/notes/.hidden.md", "research/notes/note.txt", "research/attempts.md", "research/tasks.json",
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
        task = workspace.save_tasks(drive.work, {"tasks": [{"id": "check", "text": "Discuss this result", "report": note["path"]}],
                                                 "revision": None})
        # A member's 提交 of tasks takes the result records the changed tasks link along (18.3).
        assert task["changed_task_reports"] == [note["path"]]
        # Homepage badges compare these small files to the local base only;
        # touching the published area or hashing a dataset is unnecessary.
        def unexpected_inventory(*args, **kwargs):
            pytest.fail("Workspace annotations must not scan or fingerprint the whole project")
        with monkeypatch.context() as local:
            local.setattr(share, "official_files", unexpected_inventory)
            local.setattr(proposals, "manifest", unexpected_inventory)
            marked = workspace.overview(drive.work, lang="en")
        entry = next(doc for doc in marked["documents"] if doc["path"] == note["path"])
        assert entry["officialness"] == {"state": "mine", "label": "My change, not yet proposed"}
        assert marked["tasks_officialness"] == {"state": "mine", "label": "My change, not yet proposed"}
        assert marked["officialness_mode"]["mode"] == "working_copy" and marked["officialness_mode"]["pending_known"]
        assert workspace.overview(drive.work)["changed_task_reports"] == [note["path"]]
        draft = proposals.make_proposal(drive.work, "files", "Add a result note and a follow-up task",
                                        only=[workspace.TASKS_PATH, *task["changed_task_reports"]])
        carried = {item["path"] for item in draft.data["file"]}
        assert carried == {note["path"], workspace.TASKS_PATH}
        stored = proposals.write_proposal(drive.work, draft)
        # Once proposed, the same page says so, in the same words as every other page (18.3).
        proposed = workspace.overview(drive.work)
        entry = next(doc for doc in proposed["documents"] if doc["path"] == note["path"])
        assert entry["officialness"] == {"state": "proposed", "label": "提议中", "proposal": stored.id}
        assert proposed["tasks_officialness"]["state"] == "proposed"
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
    with drive.member():
        approved = workspace.overview(drive.work, lang="en")
        entry = next(doc for doc in approved["documents"] if doc["path"] == note["path"])
        assert entry["officialness"] == {"state": "approved", "label": "Approved, awaiting sync", "proposal": stored.id}
        share.sync(drive.work)
        synced = workspace.overview(drive.work, lang="en")
        assert synced["changed_task_reports"] == []  # the approved task is the official one now
        assert {doc["officialness"]["state"] for doc in synced["documents"] if doc["path"] == note["path"]} == {"official"}


class _StalledFolder:
    """The member's proposal area in the shared folder, read as a folder that hangs does (offline, or a file still to
    be downloaded): `LocalExchange.list_proposals` waits for `release`, and `entered` says a read began."""

    def __init__(self, monkeypatch) -> None:
        from researchos import desk, exchange

        self.entered, self.release = threading.Event(), threading.Event()
        real = exchange.LocalExchange.list_proposals

        def stalled(this, member=None):
            self.entered.set()
            self.release.wait(30)
            return real(this, member)

        monkeypatch.setattr(exchange.LocalExchange, "list_proposals", stalled)
        monkeypatch.setattr(desk, "SHARED_TIMEOUT", 0.4)  # the wait the page gives it, short for the test
        self.desk = desk

    def finish(self) -> None:
        """Let the stalled read end and its job leave the desk's table, as it does on its own."""
        self.release.set()
        deadline = time.monotonic() + 10
        while self.desk._JOBS and time.monotonic() < deadline:
            time.sleep(0.02)


def _member_drive(tmp_path, monkeypatch):
    from test_v8_phase4 import Drive

    monkeypatch.setattr(addproject, "_top_level_folders", lambda: set())
    return Drive(tmp_path, monkeypatch)


@pytest.mark.skipif(signing.tool() is None, reason="ssh-keygen is not available")
def test_a_member_s_marks_never_wait_longer_than_the_desk_for_a_shared_folder_that_hangs(tmp_path, monkeypatch):
    """A member's 项目首页 and document read the shared folder for their own proposals (DESIGN 18.3) -- for no longer
    than the desk waits (14.4). A folder that hangs leaves the words of the local comparison with `pending_known`
    false; the next read, once the folder answers, knows the proposals again."""
    drive = _member_drive(tmp_path, monkeypatch)
    with drive.member():
        project.open_project(drive.work)
        note = _new(drive.work, "result", "A result of mine")
        folder = _StalledFolder(monkeypatch)
        try:
            started = time.monotonic()
            hung = workspace.overview(drive.work, lang="en")
            assert time.monotonic() - started < 5  # (the stall is 30 s)
            assert folder.entered.is_set()
            entry = next(doc for doc in hung["documents"] if doc["path"] == note["path"])
            assert entry["officialness"] == {"state": "mine", "label": "My change, not yet proposed"}
            assert hung["officialness_mode"]["mode"] == "working_copy" and hung["officialness_mode"]["pending_known"] is False
            marks = workspace.local_annotations(drive.work, {note["path"]: note["revision"]}, "zh")
            assert marks["mode"]["pending_known"] is False and marks["files"][note["path"]]["label"] == "我的改动，尚未提交"
        finally:
            folder.finish()
        answered = workspace.overview(drive.work, lang="en")
        assert answered["officialness_mode"]["pending_known"] is True


@pytest.mark.skipif(signing.tool() is None, reason="ssh-keygen is not available")
def test_a_member_s_save_releases_the_project_lock_before_the_marks_read_the_shared_folder(tmp_path, monkeypatch):
    """POST /api/workspace/document writes the file under the project lock and answers with its officialness. The
    shared folder is read for that after the lock is released: while a hanging folder is waited for, another
    writer (a scan, an agent, a second window) is not held back, and the save still answers -- in the local words."""
    from researchos.records import lock as records_lock
    from researchos.records import situation
    from test_v8_phase2 import _call
    from test_v8_phase3 import _serve

    drive = _member_drive(tmp_path, monkeypatch)
    with drive.member():
        project.open_project(drive.work)
        pid = read_identity(drive.work).identity.id
        folder = _StalledFolder(monkeypatch)
        monkeypatch.setattr(folder.desk, "SHARED_TIMEOUT", 3.0)  # long enough to look inside the wait
        answer: dict = {}
        try:
            with _serve(drive.work) as base:
                body = {"project": str(drive.work), "kind": "note", "title": "Next steps", "content": "An open question", "revision": None}
                caller = threading.Thread(target=lambda: answer.update(zip(("status", "body"), _call(f"{base}/api/workspace/document", "POST", body))))
                caller.start()
                assert folder.entered.wait(10), "the save's marks did not read the shared folder for a member"
                try:
                    with situation.write_guard(drive.work, pid, human=True, timeout=1.0):
                        free = True
                except records_lock.ProjectLockTimeout:
                    free = False
                assert free, "the project lock was held while the shared folder was read"
                caller.join(15)
        finally:
            folder.finish()
    assert answer["status"] == 200 and answer["body"]["created"] is True
    assert answer["body"]["officialness"]["state"] == "mine"
    assert "An open question" in (drive.work / answer["body"]["path"]).read_text(encoding="utf-8")


def test_a_result_document_without_a_heading_is_titled_result_record_not_the_attempt_cells_word(root):
    """DESIGN.md 18.2: 结果记录 / "Result record" is a document in 项目首页, 结果 /
    "Result" the attempt's cell. A result document whose heading was deleted
    (in the writing area, or by an agent) is listed and opened under the
    document's word in both languages; a note keeps its own."""
    result = _new(root, "result", "# Fit results\n\nIt worked.\n")
    note = _new(root, "note", "# Reading list\n")
    (root / result["path"]).write_text("It worked, no heading.\n", encoding="utf-8")
    (root / note["path"]).write_text("Only a thought.\n", encoding="utf-8")
    assert workspace.document(root, result["path"], lang="en")["title"] == "Result record"
    assert workspace.document(root, result["path"])["title"] == "结果记录"
    assert workspace.document(root, note["path"], lang="en")["title"] == "Note"
    assert workspace.document(root, note["path"])["title"] == "笔记"
    listed = {doc["path"]: doc["title"] for doc in workspace.overview(root, lang="en")["documents"]}
    assert listed[result["path"]] == "Result record" and listed[note["path"]] == "Note"


# -- 项目首页 (DESIGN.md 18.3) --------------------------------------------------------------------------------


def _touch(root: Path, rel: str, text: str, when: int) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (when, when))


def test_notes_and_results_come_newest_change_first_and_the_oldest_are_the_ones_left_out(root, monkeypatch):
    """The page lists them by last change; with more than the limit it is the oldest that go, not the last names."""
    for name, when in (("a" * 32, 1_700_000_300), ("b" * 32, 1_700_000_100), ("c" * 32, 1_700_000_200)):
        _touch(root, f"research/notes/{name}.md", f"# Note {name[0]}\n", when)
    _touch(root, "research/results/" + "d" * 32 + ".md", "# Result d\n", 1_700_000_250)
    listed = [doc["path"] for doc in workspace.overview(root)["documents"]]
    assert listed == [workspace.OVERVIEW_PATH, "research/notes/" + "a" * 32 + ".md", "research/results/" + "d" * 32 + ".md",
                      "research/notes/" + "c" * 32 + ".md", "research/notes/" + "b" * 32 + ".md"]
    # An edit moves a document to the top, the same ordering a save makes.
    edited = workspace.save_document(root, {"path": "research/notes/" + "b" * 32 + ".md", "content": "# Note b\nmore\n",
                                            "revision": workspace.document(root, "research/notes/" + "b" * 32 + ".md")["revision"]})
    assert workspace.overview(root)["documents"][1]["path"] == edited["path"]
    assert edited["modified"] == workspace.overview(root)["documents"][1]["modified"]
    monkeypatch.setattr(workspace, "DOCUMENT_LIMIT", 3)
    out = workspace.overview(root)
    assert [d["path"] for d in out["documents"]] == [workspace.OVERVIEW_PATH, edited["path"], "research/notes/" + "a" * 32 + ".md"]
    assert out["documents_truncated"]


def test_attempts_are_listed_by_number_as_a_number(root):
    """#2 before #10, and 14a right after 14 (a text order would give 1, 10, 14, 14a, 15, 2)."""
    with db.connect(paths.graph_db_path(root)) as conn:
        for number in ("10", "2", "15", "14a", "1", "14"):
            db.upsert_node(conn, f"attempt:research/attempts.md#{number}", "attempt", title=f"Attempt {number}")
    assert [a["label"] for a in workspace.overview(root)["attempts"]] == [
        "Attempt 1", "Attempt 2", "Attempt 10", "Attempt 14", "Attempt 14a", "Attempt 15"]


def test_a_title_the_person_did_not_type_never_writes_a_heading_in_either_language(root):
    """A file with no heading shows its kind's default title, in the language of the screen that read it; posting
    that word back is no title typed, whichever screen it came from (13.1). A real title still writes one."""
    result = _new(root, "result", "# Fit results\n\nIt worked.\n")
    (root / result["path"]).write_text("It worked, no heading.\n", encoding="utf-8")
    for default in ("Result record", "结果记录"):
        shown = workspace.document(root, result["path"])
        saved = workspace.save_document(root, {"path": result["path"], "title": default,
                                               "content": shown["content"] + "x", "revision": shown["revision"]}, lang="en")
        assert saved["content"] == "It worked, no heading.\nx" and not saved["content"].startswith("#")
        assert saved["title"] == "Result record"  # the answer is in the screen's language
        (root / result["path"]).write_text("It worked, no heading.\n", encoding="utf-8")
    shown = workspace.document(root, result["path"])
    typed = workspace.save_document(root, {"path": result["path"], "title": "A real title", "content": shown["content"],
                                           "revision": shown["revision"]}, lang="en")
    assert typed["content"] == "# A real title\n\nIt worked, no heading.\n" and typed["title"] == "A real title"
    # A note keeps its own default, and a heading the file has is compared as before.
    note = _new(root, "note", "# Reading list\n")
    (root / note["path"]).write_text("Only a thought.\n", encoding="utf-8")
    shown = workspace.document(root, note["path"], lang="en")
    assert shown["title"] == "Note"
    same = workspace.save_document(root, {"path": note["path"], "title": shown["title"], "content": "Only a thought.\nAnd more.\n",
                                          "revision": shown["revision"]})
    assert same["content"] == "Only a thought.\nAnd more.\n"


def test_other_markdown_names_in_notes_and_results_are_shown_opened_and_saved_but_never_created(root, tmp_path):
    """DESIGN.md 18.3: a hand-made file or a sync service's conflict copy shows too, within the same confinement."""
    names = {"research/notes/Meeting 2.md": "note", "research/notes/" + "a" * 32 + ".official.md": "note",
             "research/results/结果 (conflicted copy 2026-10-23).md": "result"}
    for index, rel in enumerate(names):
        _touch(root, rel, f"# Kept {index}\nbody\n", 1_700_000_000 + index)
    _touch(root, "research/notes/.hidden.md", "# hidden\n", 1_700_000_000)
    _touch(root, "research/notes/readme.txt", "not markdown\n", 1_700_000_000)
    _touch(root, "research/notes/sub/deeper.md", "# deeper\n", 1_700_000_000)
    (root / "research/notes/folder.md").mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# elsewhere\n")
    (root / "research/notes/link.md").symlink_to(outside)
    out = workspace.overview(root, lang="en")
    by_path = {doc["path"]: doc for doc in out["documents"]}
    assert {rel: by_path[rel]["kind"] for rel in names} == names
    assert not any(rel in by_path for rel in ("research/notes/.hidden.md", "research/notes/readme.txt", "research/notes/sub/deeper.md", "research/notes/folder.md"))
    assert by_path["research/notes/link.md"]["error"]["code"] == "path"  # a link is listed as unreadable, never followed
    assert outside.read_text() == "# elsewhere\n"
    rel = "research/notes/Meeting 2.md"
    opened = workspace.document(root, rel)
    assert opened["title"] == "Kept 0" and opened["kind"] == "note"
    saved = workspace.save_document(root, {"path": rel, "content": "# Kept 0\nedited\n", "revision": opened["revision"]})
    assert (root / rel).read_text() == "# Kept 0\nedited\n" and saved["backup"]
    # Reading a name that is not there is an ordinary not-found; creating one is the engine's own business.
    with pytest.raises(workspace.WorkspaceError) as missing:
        workspace.document(root, "research/notes/absent.md")
    assert missing.value.code == "not_found"
    with pytest.raises(workspace.WorkspaceError) as refused:
        workspace.save_document(root, {"path": "research/notes/absent.md", "content": "x", "revision": None})
    assert refused.value.code == "path" and not (root / "research/notes/absent.md").exists()


def _hand_made(root: Path, rel: str, text: str = "# Draft\n") -> None:
    """A note or result record made by hand (a name of the researcher's own), as the folder holds it."""
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _resave(root: Path, rel: str, text: str) -> dict:
    return workspace.save_document(root, {"path": rel, "content": text, "revision": workspace.document(root, rel)["revision"]})


def test_hand_made_notes_and_results_of_one_name_keep_their_backups_apart(root):
    """research/notes/Summary.md and research/results/Summary.md used to share one series of backups (the newest 20
    copies of the bare name) and one snapshot a day: saved in turn, each pruned the other's. A hand-made name now has the
    backups of its own folder; the engine's own names, the overview and the task list stay where they were."""
    note, result = "research/notes/Summary.md", "research/results/Summary.md"
    _hand_made(root, note, "note 0\n")
    _hand_made(root, result, "result 0\n")
    for index in range(1, 14):
        _resave(root, note, f"note {index}\n")
        _resave(root, result, f"result {index}\n")
    backups = root / ".researchos" / "backups"
    for rel, word in ((note, "note"), (result, "result")):
        folder = backups / Path(rel).parent
        written = sorted(p for p in folder.iterdir() if ".write." in p.name)
        assert len(written) == 13 and all(p.read_text().startswith(word) for p in written)  # not 20 shared by two
        assert len([p for p in folder.iterdir() if ".write." not in p.name]) == 1  # a snapshot a day, each
    assert not [p for p in backups.iterdir() if p.name.startswith("Summary.md")]
    kept = _new(root, "note", "kept")
    again = workspace.save_document(root, {"path": kept["path"], "content": "kept again", "revision": kept["revision"]})
    assert (root / again["backup"]).parent == backups and Path(again["backup"]).name.startswith(Path(kept["path"]).name)
    workspace.initialize(root)
    assert (root / _resave(root, workspace.OVERVIEW_PATH, "# Plan 2\n")["backup"]).parent == backups


def test_a_hand_made_note_named_like_the_attempt_table_leaves_the_tables_backups_alone(root):
    from researchos.webapp import mapedit

    table = root / "research" / "attempts.md"
    table.parent.mkdir(parents=True, exist_ok=True)
    table.write_text("| table |\n", encoding="utf-8")
    kept = [mapedit.write_backup_bytes(root, table, f"| table {index} |\n".encode(), ".md") for index in range(5)]
    note = "research/notes/attempts.md"
    _hand_made(root, note, "note 0\n")
    for index in range(1, 24):
        _resave(root, note, f"note {index}\n")
    assert all((root / path).read_text().startswith("| table") for path in kept)  # all five still there, the table's own
    assert len(list((root / ".researchos" / "backups" / "research" / "notes").glob("attempts.md.*.write.md"))) == 20  # the note's, pruned among themselves


def test_an_unreadable_project_identity_shows_the_page_with_that_said(root):
    """A project.toml that cannot be read right now (a cloud-evicted one reads the same) is no 500."""
    with db.connect(paths.graph_db_path(root)) as conn:
        db.upsert_node(conn, "attempt:research/attempts.md#1", "attempt", title="Attempt 1")
    note = _new(root)
    (root / ".researchos" / "project.toml").write_text("not = [valid", encoding="utf-8")
    zh, en = workspace.overview(root), workspace.overview(root, lang="en")
    assert zh["attempts"] == en["attempts"] == [] and not zh["can_write"]
    assert any(doc["path"] == note["path"] for doc in en["documents"])
    assert "身份文件" in zh["identity_error"]["message"] and "identity file" in en["identity_error"]["message"]
    assert zh["identity_error"]["code"] == "identity"


def test_a_readable_identity_has_no_identity_error(root):
    assert workspace.overview(root)["identity_error"] is None


def test_existing_files_knows_a_listable_file_beyond_the_listed_ones(root, tmp_path, monkeypatch):
    """The page's file list is cut at FILE_LIMIT; a link to a file past the cut is still checked on disk."""
    for rel in ("data/a.csv", "data/b.csv", "deep/er/c.png", "node_modules/x/lib.js", ".secret", "data/.hidden.csv"):
        _touch(root, rel, "x", 1_700_000_000)
    (root / "outside.csv").symlink_to(tmp_path)
    (root / "data/linked.csv").symlink_to(root / "data/a.csv")
    monkeypatch.setattr(workspace, "FILE_LIMIT", 1)
    out = workspace.overview(root)
    assert out["files_truncated"] and len(out["files"]) == 1
    asked = ["data/b.csv", "deep/er/c.png", "data/a.csv", "data", "missing.csv", "node_modules/x/lib.js", ".secret",
             "data/.hidden.csv", "data/linked.csv", "outside.csv", "../outside.csv", "/etc/hosts", "data/../data/a.csv",
             "./data/a.csv", "data//a.csv", "a\\b", "data/a.csv\0", 7, ""]
    assert workspace.existing_files(root, asked) == ["data/b.csv", "deep/er/c.png", "data/a.csv"]
    assert workspace.existing_files(root, "data/a.csv") == [] and workspace.existing_files(root, None) == []
    monkeypatch.setattr(workspace, "EXISTS_LIMIT", 1)
    assert workspace.existing_files(root, ["data/b.csv", "data/a.csv"]) == ["data/b.csv"]


def test_a_one_person_project_has_no_report_paths_to_take_along(root):
    note = _new(root, "result", "# Fit\n")
    tasks = workspace.save_tasks(root, {"tasks": [{"id": "t", "text": "Check", "report": note["path"]}], "revision": None})
    assert tasks["changed_task_reports"] == [] and workspace.overview(root)["changed_task_reports"] == []

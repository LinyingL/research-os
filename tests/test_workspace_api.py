"""Authoring through the real local HTTP server, without imported history."""
from __future__ import annotations

import dataclasses
import json
import threading
from pathlib import Path

import pytest

from researchos import project, workspace
from researchos.webapp import server
from test_webapp_server import _get, _get_raw, _post, _request_with_headers


@pytest.fixture
def writing_server(tmp_path):
    root = tmp_path / "existing"
    root.mkdir()
    project.init_project(root)
    httpd = server.build_server(root, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", root, httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_existing_project_can_write_a_plan_without_an_attempt_table(writing_server):
    base, root, _httpd = writing_server
    status, data = _get(base, "/api/workspace")
    assert status == 200 and data["project"] == str(root)
    assert not (root / "research").exists()  # opening a page never initializes files
    status, doc = _get(base, "/api/workspace/document?path=research/overview.md")
    assert status == 200 and doc["revision"] is None
    status, saved = _post(base, "/api/workspace/document", {
        "project": str(root), "path": doc["path"], "revision": None,
        "content": "# 研究问题\n\n为什么会出现这一现象？\n\n## 暂定计划\n先读文献。\n",
    })
    assert status == 200, saved
    assert (root / doc["path"]).read_text().startswith("# 研究问题")
    assert not (root / ".researchos/attempts.toml").exists()


def test_first_attempt_setup_then_preview_and_save_over_http(writing_server):
    base, root, _httpd = writing_server
    assert _get(base, "/api/summary")[1]["attempts_config"] is None
    assert not (root / "research").exists()
    status, initialized = _post(base, "/api/attempts/initialize", {"project": str(root)})
    assert status == 200 and initialized["created"] is True
    assert _get(base, "/api/summary")[1]["attempts_config"]["file"] == "research/attempts.md"
    body = {"op": "append", "number": "1", "fields": {"description": "First research idea"}}
    assert _post(base, "/api/attempts/preview", body)[0] == 200
    status, saved = _post(base, "/api/attempts/write", body)
    assert status == 200, saved
    assert "First research idea" in (root / "research/attempts.md").read_text()
    assert _post(base, "/api/attempts/initialize", {"project": str(root)})[1]["created"] is False


def test_first_attempt_setup_respects_project_readonly_and_origin(writing_server):
    base, root, httpd = writing_server
    endpoint = "/api/attempts/initialize"
    assert _post(base, endpoint, {"project": "/other/project"})[0] == 409
    assert _request_with_headers(base, "POST", endpoint,
                                 {"Origin": "https://example.com", "Content-Type": "application/json"},
                                 json.dumps({"project": str(root)}).encode())[0] == 403
    httpd.set_served(dataclasses.replace(httpd.get_served(), read_only=True))
    assert _post(base, endpoint, {"project": str(root)})[0] == 409
    assert not (root / ".researchos/attempts.toml").exists()
    assert not (root / "research").exists()


def test_stale_save_keeps_external_edit_and_gives_a_readable_conflict(writing_server):
    base, root, _httpd = writing_server
    payload = {"project": str(root), "path": workspace.OVERVIEW_PATH, "revision": None, "content": "# Plan\noriginal"}
    status, first = _post(base, "/api/workspace/document", payload)
    assert status == 200
    (root / workspace.OVERVIEW_PATH).write_text("# Plan\nchanged in another editor")
    status, error = _post(base, "/api/workspace/document", dict(payload, revision=first["revision"], content="late browser edit"))
    assert status == 409 and error["state"] == "workspace_conflict"
    assert error["message"]
    assert (root / workspace.OVERVIEW_PATH).read_text().endswith("changed in another editor")


def test_note_result_and_optional_tasks_save_as_project_files(writing_server):
    base, root, _httpd = writing_server
    for kind, title in (("note", "文献讨论"), ("result", "初步结果")):
        status, doc = _post(base, "/api/workspace/document", {
            "project": str(root), "kind": kind, "title": title, "content": "自由记录，稍后补上证据。", "revision": None,
        })
        assert status == 200, doc
        assert doc["title"] == title
        assert (root / doc["path"]).is_file()
    task = {"id": "literature", "text": "查阅文献", "done": False, "owner": "Li", "due": "2026-12-01"}
    status, saved = _post(base, "/api/workspace/tasks", {"project": str(root), "tasks": [task], "revision": None})
    assert status == 200, saved
    assert json.loads((root / workspace.TASKS_PATH).read_text()) == [task]
    status, _ = _post(base, "/api/workspace/tasks", {"project": str(root), "tasks": [], "revision": None})
    assert status == 409
    status, seen = _get(base, "/api/workspace")
    assert status == 200 and seen["tasks"] == [task]
    assert {d["kind"] for d in seen["documents"]} == {"overview", "note", "result"}


def test_project_echo_prevents_saving_a_document_into_a_different_project(writing_server):
    base, root, _httpd = writing_server
    status, error = _post(base, "/api/workspace/document", {
        "project": str(root.parent / "previous-project"), "path": workspace.OVERVIEW_PATH,
        "revision": None, "content": "belongs to another project",
    })
    assert status == 409 and error["state"] == "workspace_project_changed"
    assert not (root / "research").exists()


def test_readonly_open_allows_reading_but_refuses_both_writers(writing_server):
    base, root, httpd = writing_server
    httpd.set_served(dataclasses.replace(httpd.get_served(), read_only=True))
    status, data = _get(base, "/api/workspace")
    assert status == 200 and data["read_only"]
    for endpoint, body in (("document", {"path": workspace.OVERVIEW_PATH, "content": "draft"}), ("tasks", {"tasks": []})):
        status, error = _post(base, "/api/workspace/" + endpoint, dict(body, project=str(root), revision=None))
        assert status == 409 and error["state"] == "read_only"
    assert not (root / "research").exists()


def test_result_preview_reads_local_image_and_confines_the_path(writing_server):
    base, root, _httpd = writing_server
    image = b"\x89PNG\r\n\x1a\nexample"
    (root / "figure.png").write_bytes(image)
    assert _get_raw(base, "/api/workspace/asset?path=figure.png") == (200, image)
    assert _get(base, "/api/workspace/asset?path=../outside.png")[0] == 403
    (root / "unsafe.svg").write_text('<svg onload="alert(1)"/>')
    assert _get(base, "/api/workspace/asset?path=unsafe.svg")[0] == 415
    assert _get(base, "/api/workspace/asset?path=absent.png")[0] == 404


def test_cross_origin_page_cannot_create_or_write_a_project(writing_server):
    base, root, _httpd = writing_server
    for endpoint in ("/api/projects/create", "/api/workspace/document", "/api/workspace/tasks"):
        status, _ = _request_with_headers(base, "POST", endpoint, {
            "Host": base.removeprefix("http://"), "Origin": "https://example.com", "Content-Length": "2",
        }, b"{}")
        assert status == 403
    assert not (root / "research").exists()


def test_new_project_can_start_an_attempt_without_importing_a_table(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "test-home"))
    httpd = server.build_server(None, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, created = _post(base, "/api/projects/create", {"name": "A new question", "parent": str(tmp_path), "language": "en"})
        assert status == 200 and created["opened"], created
        root = Path(created["path"])
        assert root == tmp_path / "A new question"
        assert _get(base, "/api/workspace")[1]["mode"] == "single"
        status, attempts = _get(base, "/api/attempts")
        assert status == 200
        assert not attempts["attempts"]
        status, saved = _post(base, "/api/attempts/write", {
            "op": "append", "number": "1", "fields": {"description": "First exploration", "date": "2026-10-21"},
        })
        assert status == 200, saved
        assert "First exploration" in (root / "research/attempts.md").read_text()
        status, _ = _post(base, "/api/projects/create", {"name": root.name, "parent": str(tmp_path)})
        assert status == 409
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _en_post(base: str, path: str, body: dict) -> tuple[int, dict]:
    return _request_with_headers(base, "POST", path, {
        "Host": base.removeprefix("http://"), "Content-Type": "application/json", "X-ResearchOS-Lang": "en",
    }, json.dumps(body).encode("utf-8"))


def test_an_english_screen_saving_a_document_without_a_heading_writes_no_heading(writing_server):
    """DESIGN.md 18.3: the heading is written only for a title the person typed, whatever the screen's language."""
    base, root, _httpd = writing_server
    status, first = _post(base, "/api/workspace/document", {
        "project": str(root), "kind": "result", "title": "Fit", "content": "Estimates.", "revision": None})
    assert status == 200
    (root / first["path"]).write_text("Estimates, no heading.\n", encoding="utf-8")
    status, shown = _request_with_headers(base, "GET", "/api/workspace/document?path=" + first["path"],
                                         {"Host": base.removeprefix("http://"), "X-ResearchOS-Lang": "en"})
    assert status == 200 and shown["title"] == "Result record"
    status, saved = _en_post(base, "/api/workspace/document", {
        "project": str(root), "path": first["path"], "title": shown["title"], "content": shown["content"] + "More.\n",
        "revision": shown["revision"]})
    assert status == 200, saved
    assert (root / first["path"]).read_text(encoding="utf-8") == "Estimates, no heading.\nMore.\n"
    assert saved["title"] == "Result record" and saved["modified"]
    status, again = _post(base, "/api/workspace/document", {
        "project": str(root), "path": first["path"], "title": "结果记录", "content": saved["content"] + "x",
        "revision": saved["revision"]})  # a Chinese screen reading the same file: no heading either
    assert status == 200 and not again["content"].startswith("#") and again["title"] == "结果记录"


def test_a_link_to_an_existing_file_beyond_the_listed_ones_is_still_known(writing_server, monkeypatch):
    base, root, _httpd = writing_server
    for name in ("a.csv", "b.csv", "c.csv"):
        (root / name).write_text("x\n")
    monkeypatch.setattr(workspace, "FILE_LIMIT", 2)
    status, data = _get(base, "/api/workspace")
    assert status == 200 and data["files_truncated"] and data["files_limit"] == 2 and len(data["files"]) == 2
    unlisted = ({"a.csv", "b.csv", "c.csv"} - {f["path"] for f in data["files"]}).pop()
    query = f"project={root}&path={unlisted}&path=missing.csv&path=../outside.csv&path=.secret"
    status, got = _get(base, "/api/workspace/exists?" + query.replace(" ", "%20"))
    assert status == 200 and got == {"exists": [unlisted]}
    status, error = _get(base, "/api/workspace/exists?project=" + str(root.parent / "other") + "&path=a.csv")
    assert status == 409 and error["state"] == "workspace_project_changed"


def test_an_unreadable_project_identity_is_a_page_not_a_server_error(writing_server):
    base, root, _httpd = writing_server
    (root / ".researchos" / "project.toml").write_text("not = [valid", encoding="utf-8")
    status, data = _get(base, "/api/workspace")
    assert status == 200, data
    assert data["attempts"] == [] and data["identity_error"]["code"] == "identity" and data["read_only"]

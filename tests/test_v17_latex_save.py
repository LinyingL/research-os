"""DESIGN.md 22.1 (task V17, B1b): the LaTeX editor reads and saves a `.tex` on the server. Which files it may open,
the coordinates (code points, a CRLF counted as two), the byte-exact splice, the read-only files and why, the refusals
of a save, backups per path, 撤销这次保存, and the endpoints (the session, the project echo, the 4 MB body, the scan
after the answer, a GET that writes nothing). Every test names its scenario."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import threading
from pathlib import Path

import pytest

from researchos import db, history, latex_edit, paths, people, project, workspace
from researchos.records.identity import read_identity
from researchos.webapp import mapedit, server

CHINESE = "\\section{引言}\n人民币国际化的进展 😀 很慢。\n\n第二段 $x^2$ 结束。\n"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    (root / "paper").mkdir()
    return root


def _put(root: Path, rel: str, data: bytes) -> str:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _save(root: Path, rel: str, revision: str, edits: list[dict]) -> dict:
    return latex_edit.save_tex(root, {"path": rel, "revision": revision, "edits": edits})


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def _refused(code: str, action) -> latex_edit.WorkspaceError:
    with pytest.raises(latex_edit.WorkspaceError) as caught:
        action()
    assert caught.value.code == code, (caught.value.code, str(caught.value))
    return caught.value


# -- coordinates and the byte-exact splice ------------------------------------------------------------------------


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_a_save_changes_exactly_the_edited_bytes_on_lf_and_crlf_files_with_chinese_and_an_emoji(root, ending):
    """Offsets are code points of the decoded text with a CRLF counted as two: an edit after the emoji and after a
    CRLF lands where Python's own string index says, and every other byte is the file's."""
    text = (CHINESE * 50).replace("\n", ending)
    raw = text.encode("utf-8")
    revision = _put(root, "paper/main.tex", raw)
    opened = latex_edit.read_tex(root, "paper/main.tex")
    assert opened["text"] == text and opened["revision"] == revision and not opened["read_only"]
    assert opened["line_ending"] == ("crlf" if ending == "\r\n" else "lf")
    emoji = text.index("😀")
    second = text.index("第二段", len(text) // 2)
    edits = [{"from": emoji, "to": emoji + 1, "insert": "🙂"},          # one code point, four UTF-8 bytes
             {"from": second, "to": second + 3, "insert": f"第三段{ending}新的一行"},
             {"from": len(text), "to": len(text), "insert": f"\\end{{document}}{ending}"}]
    saved = _save(root, "paper/main.tex", revision, edits)
    expected = (text[:emoji] + "🙂" + text[emoji + 1:second] + f"第三段{ending}新的一行" + text[second + 3:]
                + f"\\end{{document}}{ending}").encode("utf-8")
    on_disk = (root / "paper/main.tex").read_bytes()
    assert on_disk == expected
    head = len(text[:emoji].encode("utf-8"))
    assert on_disk[:head] == raw[:head]                                    # the bytes before the first edit
    tail = len(text[second + 3:].encode("utf-8"))
    assert on_disk[-tail - len(f"\\end{{document}}{ending}".encode()):].startswith(raw[-tail:])
    assert saved["revision"] == hashlib.sha256(expected).hexdigest() and saved["previous"] == revision
    assert saved["changed"] and saved["history_error"] is None
    assert (root / saved["backup"]).read_bytes() == raw


def test_typing_return_in_a_crlf_file_writes_crlf_and_a_bare_line_feed_is_refused(root):
    text = "a\r\nb\r\n"
    revision = _put(root, "paper/main.tex", text.encode())
    for insert in ("x\ny", "x\ry", "x\r\n\ny"):
        _refused("invalid", lambda: _save(root, "paper/main.tex", revision, [{"from": 1, "to": 1, "insert": insert}]))
    # An edit that splits a CRLF (text between its CR and LF, or deleting only one of them) is refused too.
    _refused("invalid", lambda: _save(root, "paper/main.tex", revision, [{"from": 2, "to": 2, "insert": "x"}]))
    _refused("invalid", lambda: _save(root, "paper/main.tex", revision, [{"from": 1, "to": 2, "insert": ""}]))
    assert (root / "paper/main.tex").read_bytes() == text.encode()
    _save(root, "paper/main.tex", revision, [{"from": 1, "to": 1, "insert": "\r\n"}])
    assert (root / "paper/main.tex").read_bytes() == b"a\r\n\r\nb\r\n"


def test_an_lf_file_refuses_a_carriage_return_and_a_crlf_file_may_lose_all_its_lines(root):
    revision = _put(root, "paper/lf.tex", b"a\nb\n")
    for insert in ("\r\n", "\r"):
        _refused("invalid", lambda: _save(root, "paper/lf.tex", revision, [{"from": 1, "to": 1, "insert": insert}]))
    revision = _put(root, "paper/crlf.tex", b"a\r\nb")
    _save(root, "paper/crlf.tex", revision, [{"from": 0, "to": 4, "insert": "c"}])
    assert (root / "paper/crlf.tex").read_bytes() == b"c"


def test_an_empty_edit_list_writes_nothing(root):
    revision = _put(root, "paper/main.tex", b"x\n")
    before = _files(root)
    saved = _save(root, "paper/main.tex", revision, [])
    assert saved == {"path": "paper/main.tex", "revision": revision, "previous": None, "changed": False,
                     "backup": None, "history_error": None}
    assert _files(root) == before


# -- read-only files ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("data", "reason"), [
    (b"a\r\nb\nc\r\n", "mixed"),
    (b"a\rb\n", "lone_cr"),
    (b"a\r", "lone_cr"),
    ("caf\u00e9\n".encode("latin-1"), "encoding"),
    (b"a\x00b\n", "nul"),
])
def test_a_file_that_is_not_clean_text_opens_read_only_with_one_sentence_and_cannot_be_saved(root, data, reason):
    revision = _put(root, "paper/main.tex", data)
    for lang, index in (("zh", 0), ("en", 1)):
        opened = latex_edit.read_tex(root, "paper/main.tex", lang)
        assert opened["read_only"] and opened["read_only_reason"] == reason
        assert opened["read_only_message"] == latex_edit.READ_ONLY[reason][index]
    error = _refused("read_only_file", lambda: _save(root, "paper/main.tex", revision, [{"from": 0, "to": 0, "insert": "x"}]))
    assert error.status == 409 and error.message_zh == latex_edit.READ_ONLY[reason][0]
    assert (root / "paper/main.tex").read_bytes() == data


def test_a_table_a_task_registered_opens_read_only_saying_a_script_writes_it(root):
    revision = _put(root, "tables/t1.tex", b"\\begin{tabular}{l}\\end{tabular}\n")
    (root / "research/results").mkdir(parents=True)
    (root / "research/results/abc.outputs.json").write_text(json.dumps(
        {"version": 1, "items": [{"kind": "table", "path": "tables/t1.tex"}]}))
    opened = latex_edit.read_tex(root, "tables/t1.tex")
    assert opened["read_only_reason"] == "generated"
    assert opened["read_only_message"] == "这个文件是脚本生成的，在这里改了下次运行会被覆盖。"
    _refused("read_only_file", lambda: _save(root, "tables/t1.tex", revision, []))


def test_a_tex_a_script_writes_in_the_index_opens_read_only_unless_the_link_was_rejected(root):
    _put(root, "tables/t2.tex", b"x\n")
    conn = db.connect(paths.graph_db_path(root))
    try:
        db.migrate(conn)
        db.upsert_node(conn, "script:make.py", "script")
        db.upsert_node(conn, "dataset:tables/t2.tex", "dataset")
        db.upsert_edge(conn, "script:make.py", "dataset:tables/t2.tex", "writes", "dataflow", {"line": 3}, 1.0)
        conn.commit()
        assert latex_edit.read_tex(root, "tables/t2.tex")["read_only_reason"] == "generated"
        conn.execute("UPDATE edges SET status = 'rejected'")
        conn.commit()
    finally:
        conn.close()
    assert not latex_edit.read_tex(root, "tables/t2.tex")["read_only"]


# -- which files ------------------------------------------------------------------------------------------------------


def test_the_validator_refuses_every_path_the_editor_may_not_open(root, tmp_path):
    _put(root, ".researchos/backups/main.tex.20261112T000000000000Z.tex", b"old\n")
    _put(root, "paper/.hidden/a.tex", b"x\n")
    _put(root, "paper/UPPER.TEX", b"x\n")
    _put(root, "paper/notes.md", b"x\n")
    (root / "paper/folder.tex").mkdir()
    _put(tmp_path / "outside", "real/main.tex", b"x\n")
    os.symlink(tmp_path / "outside/real", root / "linked")
    _put(root, "inside/main.tex", b"x\n")
    os.symlink(root / "inside", root / "alias")
    os.symlink(root / "inside/main.tex", root / "paper/link.tex")
    for rel in (".researchos/backups/main.tex.20261112T000000000000Z.tex", "paper/.hidden/a.tex", "paper/UPPER.TEX",
                "paper/notes.md", "paper/folder.tex", "linked/main.tex", "alias/main.tex", "paper/link.tex",
                "../outside/real/main.tex", "paper/../inside/main.tex", "./inside/main.tex", "/etc/main.tex",
                str(root / "inside/main.tex"), "inside\\main.tex", "inside//main.tex", " inside/main.tex", "",
                None, 3, "inside/main.tex\n"):
        _refused("path", lambda: latex_edit.read_tex(root, rel))
    _refused("not_found", lambda: latex_edit.read_tex(root, "paper/missing.tex"))
    assert latex_edit.read_tex(root, "inside/main.tex")["text"] == "x\n"


def test_a_tex_only_in_the_cloud_is_the_existing_409_cloud(root):
    (root / "paper/.main.tex.icloud").write_bytes(b"")
    error = _refused("cloud", lambda: latex_edit.read_tex(root, "paper/main.tex"))
    assert error.status == 409


def test_a_file_over_2_mb_does_not_open(root):
    _put(root, "paper/big.tex", b"x" * (latex_edit.SIZE_LIMIT + 1))
    assert _refused("too_large", lambda: latex_edit.read_tex(root, "paper/big.tex")).status == 413


# -- the refusals of a save -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("edits", [
    [{"from": 3, "to": 5, "insert": ""}, {"from": 4, "to": 6, "insert": ""}],     # overlapping
    [{"from": 5, "to": 6, "insert": ""}, {"from": 1, "to": 2, "insert": ""}],     # unsorted
    [{"from": 2, "to": 1, "insert": ""}],                                         # backwards
    [{"from": -1, "to": 0, "insert": ""}],                                        # before the text
    [{"from": 0, "to": 99, "insert": ""}],                                        # past the text
    [{"from": True, "to": 1, "insert": ""}],                                      # a boolean is no offset
    [{"from": 0.0, "to": 1, "insert": ""}],
    [{"from": 0, "to": 0, "insert": "\ud800"}],                                   # a lone surrogate
    [{"from": 0, "to": 0, "insert": "a\x00b"}],                                   # a NUL
    [{"from": 0, "to": 0}],                                                       # no text
    [{"from": 0, "to": 0, "insert": "", "extra": 1}],
    ["0:0:x"],
    {"from": 0, "to": 0, "insert": ""},
])
def test_edits_that_do_not_hold_are_refused_and_nothing_is_written(root, edits):
    revision = _put(root, "paper/main.tex", b"abcdefgh\n")
    before = _files(root)
    _refused("invalid", lambda: _save(root, "paper/main.tex", revision, edits))
    assert _files(root) == before


def test_more_than_2000_edits_and_a_result_over_2_mb_are_refused(root):
    revision = _put(root, "paper/main.tex", b"x" * 3000)
    many = [{"from": i, "to": i + 1, "insert": "y"} for i in range(latex_edit.EDIT_LIMIT + 1)]
    assert _refused("too_many_edits", lambda: _save(root, "paper/main.tex", revision, many)).status == 413
    _save(root, "paper/main.tex", revision, many[:latex_edit.EDIT_LIMIT])
    revision = hashlib.sha256((root / "paper/main.tex").read_bytes()).hexdigest()
    big = [{"from": 0, "to": 0, "insert": "z" * latex_edit.SIZE_LIMIT}]
    assert _refused("too_large", lambda: _save(root, "paper/main.tex", revision, big)).status == 413


def test_a_stale_revision_is_409_and_keeps_the_file_changed_elsewhere(root):
    revision = _put(root, "paper/main.tex", b"mine\n")
    (root / "paper/main.tex").write_bytes(b"changed in another editor\n")
    error = _refused("conflict", lambda: _save(root, "paper/main.tex", revision, [{"from": 0, "to": 0, "insert": "x"}]))
    assert error.status == 409 and error.message_zh == "这个文件在别处被改过。"
    assert (root / "paper/main.tex").read_bytes() == b"changed in another editor\n"
    _refused("invalid", lambda: _save(root, "paper/main.tex", "not-a-hash", []))


def test_a_member_outside_their_working_copy_cannot_save(root):
    revision = _put(root, "paper/main.tex", b"x\n")
    people.team_path(root).write_text('[[member]]\nid="pi@example.com"\nname="PI"\nrole="pi"\n\n'
                                      '[[member]]\nid="member@example.com"\nname="Member"\nrole="member"\n')
    people.write_me("Member", "member@example.com")
    assert _refused("read_only", lambda: _save(root, "paper/main.tex", revision, [])).status == 403
    assert (root / "paper/main.tex").read_bytes() == b"x\n"


# -- backups and history ----------------------------------------------------------------------------------------------


def test_backups_are_kept_per_path_so_two_main_tex_never_prune_each_other(root):
    paper = _put(root, "paper/main.tex", b"paper 0\n")
    slides = _put(root, "slides/main.tex", b"slides 0\n")
    first = _save(root, "slides/main.tex", slides, [{"from": 7, "to": 8, "insert": "1"}])
    assert first["backup"].startswith(".researchos/backups/" + latex_edit.backup_series("slides/main.tex") + "/")
    assert latex_edit.backup_series("paper/main.tex") != latex_edit.backup_series("slides/main.tex")
    for n in range(1, mapedit.BACKUP_KEEP + 3):
        paper = _save(root, "paper/main.tex", paper, [{"from": 6, "to": len(str(n - 1)) + 6, "insert": str(n)}])["revision"]
    assert (root / first["backup"]).read_bytes() == b"slides 0\n"
    series = root / ".researchos/backups" / latex_edit.backup_series("paper/main.tex")
    assert len([p for p in series.iterdir() if ".write." in p.name]) == mapedit.BACKUP_KEEP


def test_undoing_a_save_writes_the_old_bytes_back_as_a_second_history_entry(root):
    revision = _put(root, "paper/main.tex", CHINESE.encode())
    saved = _save(root, "paper/main.tex", revision, [{"from": 0, "to": 0, "insert": "% new\n"}])
    undone = latex_edit.undo_save(root, {"path": "paper/main.tex", "revision": saved["revision"],
                                         "previous": saved["previous"]})
    assert (root / "paper/main.tex").read_bytes() == CHINESE.encode()
    assert undone["revision"] == revision and undone["text"] == CHINESE and not undone["read_only"]
    with history.Store.open(read_identity(root).identity.id) as store:
        versions = store.versions("file:paper/main.tex")
    assert [v.sha256 for v in versions][:3] == [revision, saved["revision"], revision]


def test_an_undo_is_refused_once_the_file_changed_after_the_save(root):
    revision = _put(root, "paper/main.tex", b"one\n")
    saved = _save(root, "paper/main.tex", revision, [{"from": 0, "to": 3, "insert": "two"}])
    later = _save(root, "paper/main.tex", saved["revision"], [{"from": 0, "to": 3, "insert": "six"}])
    body = {"path": "paper/main.tex", "revision": saved["revision"], "previous": saved["previous"]}
    error = _refused("conflict", lambda: latex_edit.undo_save(root, body))
    assert error.status == 409
    assert (root / "paper/main.tex").read_bytes() == b"six\n"
    # The newest save can still be undone; a previous revision no backup holds is `gone`.
    latex_edit.undo_save(root, {"path": "paper/main.tex", "revision": later["revision"], "previous": later["previous"]})
    assert (root / "paper/main.tex").read_bytes() == b"two\n"
    error = _refused("gone", lambda: latex_edit.undo_save(
        root, {"path": "paper/main.tex", "revision": saved["revision"], "previous": "0" * 64}))
    assert error.status == 409


def test_reading_writes_nothing(root):
    _put(root, "paper/main.tex", CHINESE.encode())
    before = _files(root)
    pid = read_identity(root).identity.id
    latex_edit.read_tex(root, "paper/main.tex")
    assert _files(root) == before
    assert not history.history_dir(pid).exists()


# -- the endpoints ------------------------------------------------------------------------------------------------------


@pytest.fixture
def served(root):
    httpd = server.build_server(root, 0)
    scans: list[Path] = []
    httpd.start_scan = lambda target: scans.append(target.root)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], root, scans
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _call(port: int, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request(method, path, body=body, headers={"Content-Type": "application/json", **(headers or {})})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def test_the_endpoints_open_save_and_undo_and_the_scan_starts_after_the_answer(served):
    port, root, scans = served
    revision = _put(root, "paper/main.tex", b"a\r\nb\r\n")
    status, opened = _call(port, "GET", "/api/latex/file?path=paper/main.tex")
    assert status == 200 and opened["revision"] == revision and opened["line_ending"] == "crlf"
    assert opened["project"] == str(root) and scans == []
    body = {"project": str(root), "path": "paper/main.tex", "revision": revision,
            "edits": [{"from": 3, "to": 3, "insert": "x\r\n"}]}
    status, saved = _call(port, "POST", "/api/latex/save", json.dumps(body).encode())
    assert status == 200 and saved["changed"] and isinstance(saved["generation"], int)
    assert (root / "paper/main.tex").read_bytes() == b"a\r\nx\r\nb\r\n" and scans == [root]
    status, refused = _call(port, "POST", "/api/latex/save", json.dumps(body).encode(), {"X-ResearchOS-Lang": "en"})
    assert status == 409 and refused["state"] == "workspace_conflict"
    assert refused["message"] == "The file has changed since it was opened"
    status, undone = _call(port, "POST", "/api/latex/undo", json.dumps(
        {"project": str(root), "path": "paper/main.tex", "revision": saved["revision"], "previous": saved["previous"]}).encode())
    assert status == 200 and undone["revision"] == revision and len(scans) == 2


def test_another_project_echo_is_409_and_a_body_over_4_mb_is_refused_unread(served):
    port, root, scans = served
    revision = _put(root, "paper/main.tex", b"x\n")
    status, refused = _call(port, "POST", "/api/latex/save", json.dumps(
        {"project": "/elsewhere", "path": "paper/main.tex", "revision": revision, "edits": []}).encode())
    assert status == 409 and refused["state"] == "workspace_project_changed"
    status, refused = _call(port, "GET", "/api/latex/file?path=paper/main.tex&project=/elsewhere")
    assert status == 409 and refused["state"] == "workspace_project_changed"
    # The length alone refuses it: the few bytes sent are never read as a body.
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.putrequest("POST", "/api/latex/save")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(latex_edit.BODY_LIMIT + 1))
        conn.endheaders(b"{}")
        resp = conn.getresponse()
        status, refused = resp.status, json.loads(resp.read())
    finally:
        conn.close()
    assert status == 413 and refused["state"] == "workspace_too_large"
    assert (root / "paper/main.tex").read_bytes() == b"x\n" and scans == []


def test_the_get_refuses_a_path_outside_the_validator_with_its_code(served):
    port, root, _scans = served
    _put(root, ".researchos/backups/a.tex", b"x\n")
    status, refused = _call(port, "GET", "/api/latex/file?path=.researchos/backups/a.tex")
    assert status == 400 and refused["state"] == "workspace_path"
    status, refused = _call(port, "GET", "/api/latex/file?path=paper/none.tex")
    assert status == 404 and refused["state"] == "workspace_not_found"

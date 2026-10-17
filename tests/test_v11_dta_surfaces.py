"""Where a Stata .dta file is shown (DESIGN.md 15.4 "Shown", task V11): the
history keeps its rows and variable names -- never its labels, never its
data -- a version change reads like a table's in both languages, a corrupt
file never stops a scan, and the file panel answers rows and variables
instead of refusing a binary file. Every .dta here is written by the test
module's own writer (`test_v11_dta._dta`)."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from researchos import cloud, history
from researchos import project as project_identity
from researchos.records.identity import read_identity
from researchos.webapp import node_api, server

from test_v11_dta import VARS, _dta

CJK = __import__("re").compile("[㐀-鿿　-〿＀-￯]")
AUTO = [("make", "str12", "%12s", "Make and model", ""), ("price", "int", "%8.0g", "Price", ""),
        ("mpg", "int", "%8.0g", "Mileage (mpg)", "")]
WEIGHT = ("weight", "int", "%8.0g", "Weight (lbs.)", "")


def _auto(rows: int, *, weight: bool = False, label: str = "1978 automobile data") -> bytes:
    return _dta(118, "LSF", AUTO + ([WEIGHT] if weight else []), rows=rows, label=label)


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Li", "GIT_AUTHOR_EMAIL": "li@example.com",
           "GIT_COMMITTER_NAME": "Li", "GIT_COMMITTER_EMAIL": "li@example.com"}
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


# -- the history (12.6, 15.4) ------------------------------------------------------------------------


def test_a_dta_version_change_reads_rows_and_variables_in_both_languages(tmp_path):
    """15.4: auto.dta from 74 to 80 rows with `weight` added reads 「行数从
    74 增至 80，新增变量 weight」 -- and in English "Rows from 74 to 80;
    variable added: weight" -- not as a binary size change."""
    root = tmp_path / "p"
    root.mkdir()
    (root / "auto.dta").write_bytes(_auto(74))
    project_identity.init_project(root)
    history.record_scan(None, root)
    (root / "auto.dta").write_bytes(_auto(80, weight=True))
    history.record_scan(None, root)
    assert history.diff(root, "dataset:auto.dta", words=True)["summary"] == "行数从 74 增至 80，新增变量 weight"
    english = node_api.diff_payload(root, {"node": ["dataset:auto.dta"]}, "en")
    assert english["summary_words"] == "Rows from 74 to 80; variable added: weight"
    assert history.diff(root, "auto.dta")["summary"] == "行数 74 → 80，新增变量 weight"  # the command line's form
    first = history.versions(root, "auto.dta")["versions"][-1]
    assert history.table_shape(first["summary"]) == "74 行，变量 make、price、mpg"
    assert history.table_shape(first["summary"], "en") == "74 rows; variables make, price, mpg"


def test_a_dta_change_in_git_is_read_from_its_header_streamed(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "auto.dta").write_bytes(_auto(74))
    _git(root, "add", "auto.dta")
    _git(root, "commit", "-q", "-m", "auto")
    (root / "auto.dta").write_bytes(_auto(80, weight=True))
    _git(root, "commit", "-q", "-am", "more cars, their weight")
    project_identity.init_project(root)
    assert history.versions(root, "auto.dta")["origin"] == "git"
    assert history.diff(root, "auto.dta", words=True)["summary"] == "行数从 74 增至 80，新增变量 weight"
    assert history.diff(root, "auto.dta", words=True, lang="en")["summary"] == "Rows from 74 to 80; variable added: weight"
    (root / "auto.dta").write_bytes(b"<stata_dta><header><release>118</release>")  # the working copy, cut short
    worktree = history.diff(root, "auto.dta", words=True, lang="en")
    assert worktree["a"]["version"] != history.WORKTREE and worktree["b"]["version"] == history.WORKTREE
    assert worktree["summary"] == "Rows not compared; variables not compared"


def test_a_corrupt_dta_never_stops_the_scan_and_history_keeps_names_and_rows_only(tmp_path):
    """15.4: a corrupt .dta between two good ones -- all three are recorded,
    the corrupt one with its refusal's code; what the store keeps of a .dta
    is its variables' names and its rows: never a label, never a value."""
    root = tmp_path / "p"
    root.mkdir()
    secret = [("v1", "byte", "%8.0g", "SECRET-LABEL-TEXT", "SECRET-VALUE-LABEL")]
    (root / "a.dta").write_bytes(_dta(118, "LSF", secret, rows=3, label="SECRET-DATASET-LABEL"))
    (root / "b.dta").write_bytes(_auto(74)[:200])
    (root / "c.dta").write_bytes(_auto(5))
    project_identity.init_project(root)
    counts = history.record_after_scan(None, root)
    assert counts.get("recorded", 0) >= 3
    summaries = {rel: history.versions(root, rel)["versions"][0]["summary"] for rel in ("a.dta", "b.dta", "c.dta")}
    assert summaries["a.dta"] == {"format": "dta", "columns": ["v1"], "rows": 3, "rows_complete": True}
    assert summaries["b.dta"] == {"format": "dta", "columns": None, "rows": None, "rows_complete": False,
                                  "error": "truncated"}
    assert summaries["c.dta"]["rows"] == 5
    store_dir = history.history_dir(read_identity(root).identity.id)
    kept = b"".join(p.read_bytes() for p in store_dir.rglob("*") if p.is_file())
    assert b"SECRET" not in kept
    assert not any((store_dir / "blobs").rglob("*")) if (store_dir / "blobs").exists() else True


# -- the file panel (15.4) ---------------------------------------------------------------------------


def test_the_file_panel_answers_rows_and_variables(tmp_path):
    (tmp_path / "auto.dta").write_bytes(_auto(74))
    got = server.file_payload(tmp_path, "auto.dta")
    assert got["kind"] == "dta" and "content" not in got
    assert got["dta"]["rows"] == 74 and got["dta"]["label"] == "1978 automobile data"
    assert got["dta"]["variables"] == [{"name": n, "label": lab, "type": t, "format": f} for n, t, f, lab, _vl in AUTO]


@pytest.mark.parametrize("data, code", [
    (lambda: _auto(74)[:300], "truncated"),
    (lambda: _auto(74).replace(b"<variable_types>", b"<variable_typeX>", 1), "corrupt"),
    (lambda: b"\x89PNG\r\n\x1a\n" + b"\0" * 64, "not_dta"),
])
def test_a_corrupt_or_truncated_dta_is_refused_with_a_sentence_in_both_languages(tmp_path, data, code):
    (tmp_path / "bad.dta").write_bytes(data())
    for lang in ("zh", "en"):
        with pytest.raises(server.DtaApiError) as refused:
            server.file_payload(tmp_path, "bad.dta", lang)
        body = refused.value.extra
        assert refused.value.state == "dta_refused" and body["code"] == code and body["detail"]
        assert bool(CJK.search(body["message"])) == (lang == "zh")
        assert "github" in body


def test_a_dta_only_in_the_cloud_is_refused_as_cloud_only_and_other_binaries_as_binary(tmp_path, monkeypatch):
    (tmp_path / "auto.dta").write_bytes(_auto(74))
    (tmp_path / "pic.bin").write_bytes(b"\0\1\2")
    real = cloud.check_readable

    def only_in_the_cloud(path):
        if str(path).endswith(".dta"):
            raise cloud.CloudOnlyError(path, cloud.Provider("OneDrive", "OneDrive", "OneDrive", "/nowhere"))
        return real(path)

    monkeypatch.setattr(cloud, "check_readable", only_in_the_cloud)
    with pytest.raises(server.CloudOnlyApiError):
        server.file_payload(tmp_path, "auto.dta")
    with pytest.raises(server.BinaryFileError):
        server.file_payload(tmp_path, "pic.bin")


def test_names_and_labels_reach_the_page_as_text_and_the_panel_route_speaks_the_language(tmp_path):
    """Untrusted names and labels go out as JSON strings for the page to set
    with textContent; GET /api/file answers in the request's language."""
    root = tmp_path / "p"
    root.mkdir()
    hostile = [("v1", "byte", "%8.0g", "<img src=x onerror=alert(1)>", ""), *VARS[1:]]
    (root / "x.dta").write_bytes(_dta(118, "LSF", hostile, rows=2))
    (root / "bad.dta").write_bytes(b"<stata_dta>")
    project_identity.init_project(root)
    httpd = server.build_server(root, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path: str, lang: str) -> tuple[int, dict]:
        req = urllib.request.Request(base + path, headers={"X-ResearchOS-Lang": lang})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        status, body = get("/api/file?path=x.dta", "en")
        assert status == 200 and body["dta"]["variables"][0]["label"] == "<img src=x onerror=alert(1)>"
        status, body = get("/api/file?path=bad.dta", "en")
        assert status == 422 and body["state"] == "dta_refused" and not CJK.search(body["message"])
        status, body = get("/api/file?path=bad.dta", "zh")
        assert status == 422 and CJK.search(body["message"])
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# -- the panel's words (15.4, 13.6) ------------------------------------------------------------------

_DTA_CASE = r"""
function read(n) {
  if (typeof n === "string") return n;
  return [n.tag, n.className, ...n.kids.map(read)];
}
const info = { rows: 1497, label: "<b>Survey</b> 2026", variables: [
  { name: "in�come", label: "<img src=x onerror=alert(1)>", type: "double" },
  { name: "id", label: "", type: "long" } ] };
process.stdout.write(JSON.stringify(read(dtaPreview(info))));
"""


def _panel(saved: str | None) -> list:
    from test_v9_language import _MENU_DOM, _app_block, _app_function, _i18n_source, _node

    code = (_app_block("// -- The interface language (DESIGN.md 13.6)", "// -- Small shared state")
            + _app_function("function mk(") + _app_function("function groupedNumber(")
            + _app_block("// -- A Stata .dta file in the panel (DESIGN.md 15.4) --", "function isCurrentFilePanel(path) {"))
    i18n = "" if saved is None else f"window.ROI18N_SAVED = {json.dumps(saved)};\n" + _i18n_source()
    return _node("global.window = {};\n" + i18n + _MENU_DOM + code + _DTA_CASE)


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("saved", [None, "zh", "en"])
def test_the_panel_draws_rows_variables_and_the_table_as_text_in_the_screens_language(saved):
    """15.4 in the panel: rows by variables, the dataset label, the table of
    name, label and type -- each name and label set as text, never markup
    -- every word of Research OS's in the screen's language (13.6)."""
    shown = _panel(saved)
    table = shown[4]
    if saved == "en":
        assert shown[:4] == ["div", "dta-preview", ["p", "dta-shape", "1,497 rows × 2 variables"],
                             ["p", "dta-label", "Dataset label: <b>Survey</b> 2026"]]
        assert table[2] == ["tr", "", ["th", "", "Name"], ["th", "", "Label"], ["th", "", "Type"]]
        assert not CJK.search(json.dumps(shown, ensure_ascii=False))
    else:
        assert shown[:4] == ["div", "dta-preview", ["p", "dta-shape", "1497 行 × 2 个变量"],
                             ["p", "dta-label", "数据集标签：<b>Survey</b> 2026"]]
        assert table[2] == ["tr", "", ["th", "", "变量名"], ["th", "", "标签"], ["th", "", "类型"]]
    assert table[3] == ["tr", "", ["td", "dta-name", "in�come"],
                        ["td", "dta-label-cell", "<img src=x onerror=alert(1)>"], ["td", "dta-type", "double"]]

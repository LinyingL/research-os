"""DESIGN.md 22.0.1 (task V17, sub-batch B1a): CodeMirror 6 vendored, served by an allow-listed route, loaded on demand.

The bundle is built by tools/vendor/build-codemirror.mjs from the lock beside it; these tests check what was committed
(the bytes, their sha256 in the README, the licences, the names the bundle exposes) and how it reaches a page: only
through `GET /vendor/<name>`'s allow-list, and only when `ROLatex.loadEditorLibs()` is first called."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from researchos.webapp import server

from test_engine_session import Engine, _init_project, _running, _stop

REPO = Path(__file__).parents[1]
WEBAPP = Path(server.__file__).parent
VENDOR = WEBAPP / "vendor"
LOCK = json.loads((REPO / "tools" / "vendor" / "codemirror.lock.json").read_text(encoding="utf-8"))
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture(params=["project", "no project"])
def eng(request, tmp_path):
    root = None
    if request.param == "project":
        root = tmp_path / "proj"
        _init_project(root)
    httpd, thread = _running(root)
    try:
        yield Engine(httpd)
    finally:
        _stop(httpd, thread)


def test_the_route_serves_the_allow_listed_bundle_as_bytes_with_its_type(eng):
    status, headers, raw = eng.call("GET", "/vendor/codemirror.js")
    assert status == 200
    assert headers["content-type"] == "application/javascript"
    assert headers["x-content-type-options"] == "nosniff"
    assert raw == (VENDOR / "codemirror.js").read_bytes()
    assert server._VENDOR_FILES == {"codemirror.js": "application/javascript"}


@pytest.mark.parametrize("path", [
    "/vendor/README", "/vendor/LICENSE.codemirror-view", "/vendor/", "/vendor/codemirror.js/", "/vendor/./codemirror.js",
    "/vendor/../app.html", "/vendor/..%2fapp.html", "/vendor/%2e%2e/server.py", "/vendor/../../webapp/server.py",
    "/vendor/x/codemirror.js", "/vendor/codemirror.js%00", "/vendor/CODEMIRROR.JS", "/vendor//etc/passwd",
])
def test_the_route_refuses_every_name_not_on_the_list_and_every_path(eng, path):
    status, _headers, raw = eng.call("GET", path)
    assert status == 404, (path, status)
    assert b"function" not in raw and b"Copyright" not in raw


def test_another_origin_cannot_read_the_bundle(eng):
    status, _headers, _raw = eng.call("GET", "/vendor/codemirror.js", headers={"Origin": "http://evil.example"})
    assert status == 403


def test_no_page_references_the_vendored_files_but_the_editor_loader():
    """22.8 item 7: no page loads them until the editor opens. The markup never names /vendor/; of the served modules
    only latex.js does, inside loadEditorLibs."""
    assert "/vendor/" not in (WEBAPP / "app.html").read_text(encoding="utf-8")
    naming = sorted(p.name for p in WEBAPP.glob("*.js") if "/vendor/" in p.read_text(encoding="utf-8"))
    assert naming == ["latex.js"]
    assert '<script src="/latex.js"></script>' in (WEBAPP / "app.html").read_text(encoding="utf-8")


def test_the_package_ships_the_vendor_folder_and_the_loader():
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"webapp/vendor/*"' in pyproject and '"webapp/latex.js"' in pyproject
    assert re.search(r"^dependencies = \[\]", pyproject, re.M)   # 22.0.1: the Python side stays without dependencies


def test_the_readme_names_every_vendored_file_with_its_sha256_and_every_package_with_its_licence():
    readme = (VENDOR / "README").read_text(encoding="utf-8")
    listed = dict((name, digest) for digest, name in re.findall(r"^([0-9a-f]{64})  (\S+)$", readme, re.M))
    on_disk = {p.name for p in VENDOR.iterdir() if p.name != "README"}
    assert set(listed) == on_disk
    for name, digest in listed.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name
    for pkg in LOCK["packages"]:
        assert f"- {pkg['name']} {pkg['version']} ({pkg['license']})" in readme
        licence = VENDOR / ("LICENSE." + pkg["name"].lstrip("@").replace("/", "-"))
        assert pkg["license"] == "MIT" and "Permission is hereby granted, free of charge" in licence.read_text(encoding="utf-8"), pkg["name"]
    assert f"esbuild {LOCK['bundler']['version']}" in readme
    assert set(server._VENDOR_FILES) <= on_disk


def test_the_entry_exports_exactly_the_locked_names():
    entry = (REPO / "tools" / "vendor" / "codemirror-entry.js").read_text(encoding="utf-8")
    names = [n.strip() for block in re.findall(r"export \{([^}]*)\}", entry) for n in block.split(",") if n.strip()]
    assert sorted(names) == sorted(LOCK["exports"])


def test_the_bundle_is_ascii_and_fetches_nothing():
    raw = (VENDOR / "codemirror.js").read_bytes()
    assert max(raw) < 0x80   # charset=ascii: no character of its own reaches a scanner or the screen as written
    assert raw.startswith(b"/* CodeMirror 6 for Research OS")
    assert not re.search(rb"\bfetch\(|XMLHttpRequest|importScripts|sendBeacon", raw)


@needs_node
def test_the_bundle_exposes_the_locked_names_under_node():
    script = ("const vm = require('vm'); const fs = require('fs'); const ctx = vm.createContext({});\n"
              f"vm.runInContext(fs.readFileSync({json.dumps(str(VENDOR / 'codemirror.js'))}, 'utf8'), ctx);\n"
              "const cm = ctx.ROCodeMirror;\n"
              "const st = cm.EditorState.create({doc: 'a\\r\\nb', extensions: [cm.EditorState.lineSeparator.of('\\r\\n'),"
              " cm.StreamLanguage.define(cm.stex), cm.history()]});\n"
              "const ch = cm.ChangeSet.of([{from: 1, insert: 'x'}], st.doc.length);\n"
              "console.log(JSON.stringify({keys: Object.keys(cm).sort(), lines: st.doc.lines,"
              " mapped: ch.mapPos(2, 1, cm.MapMode.Simple), sep: st.lineBreak}));")
    out = json.loads(subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True).stdout)
    assert out["keys"] == sorted(LOCK["exports"])
    assert out["lines"] == 2 and out["sep"] == "\r\n" and out["mapped"] == 3


_LOADER = r"""
const tags = [];
global.window = {};
global.document = {head: {appendChild(tag) { tags.push(tag); }}, createElement(name) {
  const tag = {name, remove() { tag.removed = true; }}; return tag; }};
%(latex)s
(async () => {
  const L = window.ROLatex;
  const tick = () => new Promise(r => setTimeout(r, 0));
  const nothingYet = tags.length;          // loading the module adds nothing
  const first = L.loadEditorLibs(), again = L.loadEditorLibs();
  await tick();
  const before = tags.length;
  tags[0].onerror();                       // the first try fails: the promise rejects and is not kept
  let failed = null;
  try { await first; } catch (e) { failed = e.message; }
  const retry = L.loadEditorLibs();
  await tick();
  window.ROCodeMirror = {EditorView: 1};
  tags[1].onload();
  const libs = await retry;
  const third = await L.loadEditorLibs();
  console.log(JSON.stringify({nothingYet, same: first === again, before, failed, removed: !!tags[0].removed,
    srcs: tags.map(t => t.src), async: tags.map(t => t.async), cm: libs.CodeMirror, kept: third === libs}));
})();
"""


@needs_node
def test_the_loader_adds_the_script_tag_once_and_only_when_asked():
    latex = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    out = json.loads(subprocess.run([NODE, "-"], input=_LOADER % {"latex": latex}, capture_output=True, text=True,
                                    check=True).stdout)
    assert out["nothingYet"] == 0 and out["same"] is True and out["before"] == 1          # two calls, one tag
    assert out["failed"] == "/vendor/codemirror.js could not be loaded" and out["removed"] is True
    assert out["srcs"] == ["/vendor/codemirror.js", "/vendor/codemirror.js"] and out["async"] == [False, False]
    assert out["cm"] == {"EditorView": 1} and out["kept"] is True

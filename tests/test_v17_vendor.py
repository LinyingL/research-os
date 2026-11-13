"""DESIGN.md 22.0.1 (task V17, sub-batch B1a): CodeMirror 6 vendored, served by an allow-listed route, loaded on demand.

The bundle is built by tools/vendor/build-codemirror.mjs from the lock beside it; these tests check what was committed
(the bytes, their sha256 in the README, the licences, the names the bundle exposes) and how it reaches a page: only
through `GET /vendor/<name>`'s allow-list, and only when `ROLatex.loadEditorLibs()` is first called."""

from __future__ import annotations

import hashlib
import json
import os
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
    assert {k: v for k, v in server._VENDOR_FILES.items() if not k.startswith("katex") and "/" not in k} == {
        "codemirror.js": "application/javascript"}


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
    on_disk = {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*") if p.is_file() and p.name != "README"}
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


# -- KaTeX (sub-batch B3a): katex.js, katex.css and its woff2 fonts, by tools/vendor/build-katex.mjs ------------------

KATEX_LOCK = json.loads((REPO / "tools" / "vendor" / "katex.lock.json").read_text(encoding="utf-8"))
KATEX_TYPES = {"katex.js": "application/javascript", "katex.css": "text/css",
               **{"fonts/" + name: "font/woff2" for name in KATEX_LOCK["fonts"]}}


def test_the_allow_list_is_codemirror_katex_and_exactly_the_locked_fonts_each_by_name():
    assert len(KATEX_LOCK["fonts"]) == 20
    assert server._VENDOR_FILES == {"codemirror.js": "application/javascript", **KATEX_TYPES}
    assert sorted(p.name for p in (VENDOR / "fonts").iterdir()) == sorted(KATEX_LOCK["fonts"])


@pytest.mark.parametrize("name", sorted(KATEX_TYPES))
def test_the_route_serves_katex_and_each_font_as_bytes_with_its_type(eng, name):
    status, headers, raw = eng.call("GET", "/vendor/" + name)
    assert status == 200
    assert headers["content-type"] == KATEX_TYPES[name]
    assert headers["x-content-type-options"] == "nosniff"
    assert raw == (VENDOR / name).read_bytes()
    if name.endswith(".woff2"):
        assert raw[:4] == b"wOF2"


@pytest.mark.parametrize("path", [
    "/vendor/fonts/", "/vendor/fonts", "/vendor/fonts/../katex.js", "/vendor/fonts/..%2fkatex.js",
    "/vendor/fonts/%2e%2e/katex.css", "/vendor/fonts/./KaTeX_AMS-Regular.woff2", "/vendor/fonts//KaTeX_AMS-Regular.woff2",
    "/vendor/fonts/fonts/KaTeX_AMS-Regular.woff2", "/vendor/FONTS/KaTeX_AMS-Regular.woff2",
    "/vendor/fonts/KaTeX_AMS-Regular.woff", "/vendor/fonts/KaTeX_AMS-Regular.ttf", "/vendor/fonts/KaTeX_Nope.woff2",
    "/vendor/fonts/KaTeX_AMS-Regular.woff2/", "/vendor/KaTeX_AMS-Regular.woff2", "/vendor/LICENSE.katex",
    "/vendor/katex.min.js", "/vendor/katex.mjs", "/vendor/x/../katex.js", "/vendor/fonts/../../app.html",
])
def test_the_route_refuses_every_font_path_not_on_the_list(eng, path):
    status, _headers, raw = eng.call("GET", path)
    assert status == 404, (path, status)
    assert raw.startswith(b'{"error": "no such vendored file: ') or raw.startswith(b'{"error"'), raw[:80]
    assert b"wOF2" not in raw and b"@font-face" not in raw and b"renderToString" not in raw and b"Copyright" not in raw


def test_another_origin_cannot_read_katex_or_a_font(eng):
    for path in ("/vendor/katex.js", "/vendor/fonts/KaTeX_Main-Regular.woff2"):
        status, _headers, _raw = eng.call("GET", path, headers={"Origin": "http://evil.example"})
        assert status == 403, path


def test_the_package_ships_the_fonts_folder():
    assert '"webapp/vendor/fonts/*"' in (REPO / "pyproject.toml").read_text(encoding="utf-8")


def test_the_readme_pins_katex_and_its_licence_is_beside_it():
    readme = (VENDOR / "README").read_text(encoding="utf-8")
    assert f"KaTeX {KATEX_LOCK['package']['version']} (MIT" in readme
    assert KATEX_LOCK["tarball"]["sha256"] in readme and "tools/vendor/build-katex.mjs" in readme
    assert "is not vendored yet" not in readme
    listed = {name for _digest, name in re.findall(r"^([0-9a-f]{64})  (\S+)$", readme, re.M)}
    assert set(KATEX_TYPES) | {"LICENSE.katex"} <= listed
    licence = (VENDOR / "LICENSE.katex").read_text(encoding="utf-8")
    assert KATEX_LOCK["package"]["license"] == "MIT" and "Khan Academy" in licence
    assert "Permission is hereby granted, free of charge" in licence


def test_katex_css_names_only_its_woff2_fonts_by_the_served_path():
    css = (VENDOR / "katex.css").read_text(encoding="utf-8")
    urls = re.findall(r"url\(([^)]*)\)", css)
    assert sorted(set(urls)) == sorted("fonts/" + name for name in KATEX_LOCK["fonts"])
    assert all('url(%s) format("woff2")' % u in css for u in urls)
    assert ".woff)" not in css and ".ttf)" not in css and "truetype" not in css and "@import" not in css
    assert css.count("@font-face") == len(urls)


def test_katex_is_ascii_and_fetches_nothing():
    for name in ("katex.js", "katex.css"):
        raw = (VENDOR / name).read_bytes()
        assert max(raw) < 0x80, name
        assert not re.search(rb"XMLHttpRequest|importScripts|sendBeacon|WebSocket|EventSource|https?://(?!www\.w3\.org/)", raw), name
        # KaTeX's own lexer has a method called fetch(); none is the network's global fetch.
        assert not re.search(rb"(?<![.\w}])fetch\(", raw), name


@needs_node
def test_katex_typesets_a_formula_under_node():
    script = ("const vm = require('vm'); const fs = require('fs'); const ctx = vm.createContext({});\n"
              f"vm.runInContext(fs.readFileSync({json.dumps(str(VENDOR / 'katex.js'))}, 'utf8'), ctx);\n"
              "const k = ctx.katex; let bad = null;\n"
              "try { k.renderToString('\\\\frac{a}{', {throwOnError: true}); } catch (e) { bad = e.name; }\n"
              "console.log(JSON.stringify({version: k.version, html: k.renderToString('\\\\frac{a}{b}'), bad,"
              " soft: k.renderToString('\\\\frac{a}{', {throwOnError: false})}));")
    out = json.loads(subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True).stdout)
    assert out["version"] == KATEX_LOCK["package"]["version"]
    assert 'class="mfrac"' in out["html"] and "<annotation encoding=\"application/x-tex\">\\frac{a}{b}</annotation>" in out["html"]
    assert out["bad"] == "ParseError" and "katex-error" in out["soft"]


_MATH_LOADER = r"""
const tags = [];
global.window = {};
global.document = {head: {appendChild(tag) { tags.push(tag); }}, createElement(name) {
  const tag = {name, remove() { tag.removed = true; }}; return tag; }};
%(latex)s
(async () => {
  const L = window.ROLatex;
  const tick = () => new Promise(r => setTimeout(r, 0));
  const first = L.loadMath(), again = L.loadMath();
  await tick();
  const before = tags.map(t => [t.name, t.src || t.href, t.rel || null]);
  tags[1].onerror();                               // the script fails: both tags go, and the failure is not kept
  let failed = null;
  try { await first; } catch (e) { failed = e.message; }
  const removed = tags.map(t => !!t.removed);
  const retry = L.loadMath();
  await tick();
  window.katex = {version: "x"};
  tags[2].onload(); tags[3].onload();
  const katex = await retry;
  const kept = (await L.loadMath()) === katex;
  const editor = L._vendorScripts.map(s => s.src);
  console.log(JSON.stringify({same: first === again, before, failed, removed, tags: tags.length, katex, kept, editor}));
})();
"""


@needs_node
def test_load_math_adds_the_stylesheet_and_the_script_once_and_only_when_asked():
    latex = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    out = json.loads(subprocess.run([NODE, "-"], input=_MATH_LOADER % {"latex": latex}, capture_output=True, text=True,
                                    check=True).stdout)
    assert out["same"] is True
    assert out["before"] == [["link", "/vendor/katex.css", "stylesheet"], ["script", "/vendor/katex.js", None]]
    assert out["failed"] == "/vendor/katex.js could not be loaded" and out["removed"] == [True, True]
    assert out["tags"] == 4 and out["katex"] == {"version": "x"} and out["kept"] is True
    assert out["editor"] == ["/vendor/codemirror.js"]   # opening the editor never waits on KaTeX


_MATH_STYLE_FAILS = r"""
const tags = [];
global.window = {};
global.document = {head: {appendChild(tag) { tags.push(tag); }}, createElement(name) {
  const tag = {name, remove() { tag.removed = true; }}; return tag; }};
%(latex)s
(async () => {
  const L = window.ROLatex;
  const tick = () => new Promise(r => setTimeout(r, 0));
  const first = L.loadMath();
  await tick();
  tags[0].onerror();                               // the stylesheet fails while the script is still loading
  let failed = null;
  try { await first; } catch (e) { failed = e.message; }
  const removed = tags.map(t => !!t.removed);
  const retry = L.loadMath();
  await tick();
  const live = tags.filter(t => !t.removed).map(t => [t.name, t.src || t.href]);
  window.katex = {version: "y"};
  tags[2].onload(); tags[3].onload();
  const katex = await retry;
  console.log(JSON.stringify({failed, removed, live, tags: tags.length, katex}));
})();
"""


@needs_node
def test_load_math_takes_the_script_away_too_when_the_stylesheet_fails():
    latex = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    out = json.loads(subprocess.run([NODE, "-"], input=_MATH_STYLE_FAILS % {"latex": latex}, capture_output=True,
                                    text=True, check=True).stdout)
    assert out["failed"] == "/vendor/katex.css could not be loaded" and out["removed"] == [True, True]
    assert out["live"] == [["link", "/vendor/katex.css"], ["script", "/vendor/katex.js"]]   # one of each, never two scripts
    assert out["tags"] == 4 and out["katex"] == {"version": "y"}


def test_the_readme_says_to_run_the_rebuild_check_with_the_tarball():
    readme = (VENDOR / "README").read_text(encoding="utf-8")
    assert "RO_KATEX_TGZ=<katex-0.19.1.tgz> pytest tests/test_v17_vendor.py" in readme


@needs_node
def test_the_build_script_refuses_a_tarball_that_is_not_the_locked_one(tmp_path):
    tools = tmp_path / "tools" / "vendor"
    tools.mkdir(parents=True)
    for name in ("build-katex.mjs", "katex.lock.json"):
        shutil.copy(REPO / "tools" / "vendor" / name, tools / name)
    vendor = tmp_path / "src" / "researchos" / "webapp" / "vendor"
    vendor.mkdir(parents=True)
    (vendor / "README").write_text("x\n", encoding="utf-8")
    bad = tmp_path / "katex-0.19.1.tgz"
    bad.write_bytes(b"\x1f\x8b not the tarball")
    run = subprocess.run([NODE, str(tools / "build-katex.mjs"), str(bad)], capture_output=True, text=True)
    assert run.returncode == 1 and "sha256" in run.stderr and "nothing was written" in run.stderr
    assert sorted(p.name for p in vendor.iterdir()) == ["README"]


KATEX_TGZ = os.environ.get("RO_KATEX_TGZ")


@needs_node
@pytest.mark.skipif(not KATEX_TGZ, reason="set RO_KATEX_TGZ to the locked katex tarball to rebuild from it")
def test_a_rebuild_from_the_locked_tarball_gives_the_committed_bytes(tmp_path):
    tools = tmp_path / "tools" / "vendor"
    tools.mkdir(parents=True)
    for name in ("build-katex.mjs", "katex.lock.json"):
        shutil.copy(REPO / "tools" / "vendor" / name, tools / name)
    vendor = tmp_path / "src" / "researchos" / "webapp" / "vendor"
    shutil.copytree(VENDOR, vendor)
    for name in [*KATEX_TYPES, "LICENSE.katex"]:
        (vendor / name).unlink()
    subprocess.run([NODE, str(tools / "build-katex.mjs"), KATEX_TGZ], capture_output=True, text=True, check=True)
    for path in VENDOR.rglob("*"):
        if path.is_file():
            assert (vendor / path.relative_to(VENDOR)).read_bytes() == path.read_bytes(), path.name

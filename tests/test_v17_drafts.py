"""DESIGN.md 22.10 (task V17, after the researcher could not find the editor): 「正文」 in the sidebar. The engine's
GET /api/latex/drafts -- each main file with the files it brings in, the other `.tex` files, generated tables
counted and not listed, dot folders and symbolic links never read, a file only in the cloud flagged, passage counts,
nothing written, a blocked project refused -- the sidebar's new item and its place, the page drawn under node
(rows, the ochre count, 只在云端 as text, no primary button, the editor opened from it belonging to 正文), and its
English. Every test names its scenario."""

from __future__ import annotations

import dataclasses
import hashlib
import http.client
import json
import os
import re
import subprocess
import threading
from pathlib import Path

import pytest

from researchos import latex_edit, paths, project
from researchos.webapp import server
from test_v17_latex_editor import _DRIVER, NODE, WEBAPP

APP_HTML = WEBAPP / "app.html"
MAIN = "\\documentclass{article}\n\\begin{document}\n\\input{chapters/intro}\n\\include{chapters/results}\n" \
       "% \\input{chapters/commented}\n\\input{tables/t1}\n\\end{document}\n"


def _put(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}


@pytest.fixture
def root(tmp_path) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    project.init_project(root)
    _put(root, "paper/main.tex", MAIN)
    _put(root, "paper/chapters/intro.tex", "Intro 很慢。\n\\input{chapters/deep}\n")
    _put(root, "paper/chapters/deep.tex", "Deep.\n")
    _put(root, "paper/chapters/results.tex", "Results.\n")
    _put(root, "paper/chapters/commented.tex", "Only in a comment.\n")
    _put(root, "notes/old.tex", "An old draft.\n")
    _put(root, "paper/tables/t1.tex", "\\begin{tabular}{c}1\\end{tabular}\n")   # written by a script: registered below
    _put(root, ".hidden/secret.tex", "\\documentclass{article}\\begin{document}x\\end{document}\n")
    (root / "research/results").mkdir(parents=True, exist_ok=True)
    (root / "research/results/abc.outputs.json").write_text(json.dumps(
        {"version": 1, "items": [{"kind": "table", "path": "paper/tables/t1.tex"}]}))
    return root


def test_main_files_group_their_includes_in_document_order_and_the_rest_stand_apart(root):
    got = latex_edit.drafts(root)
    assert [g["main"]["path"] for g in got["groups"]] == ["paper/main.tex"]
    assert [f["path"] for f in got["groups"][0]["files"]] == [
        "paper/chapters/intro.tex", "paper/chapters/deep.tex", "paper/chapters/results.tex"]
    # an include only in a comment does not bring the file in; it stands with the rest
    assert [f["path"] for f in got["others"]] == ["notes/old.tex", "paper/chapters/commented.tex"]
    assert got["truncated"] is False and got["limit"] == latex_edit.TEX_FILE_LIMIT and got["message"] is None
    row = got["groups"][0]["main"]
    assert row["cloud"] is False and row["supported"] == 0 and row["waiting"] == 0
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", row["modified"]) and row["modified"].endswith("+00:00")


def test_a_generated_table_is_counted_never_listed_and_dot_folders_and_symlinks_are_never_read(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "elsewhere.tex").write_text("\\documentclass{article}\\begin{document}x\\end{document}\n")
    os.symlink(outside / "elsewhere.tex", root / "notes/linked.tex")
    os.symlink(outside, root / "linkdir")
    _put(root, ".researchos/backups/latex/x/main.tex", MAIN)
    got = latex_edit.drafts(root)
    listed = {r["path"] for g in got["groups"] for r in [g["main"], *g["files"]]} | {r["path"] for r in got["others"]}
    assert got["generated"] == 1 and "paper/tables/t1.tex" not in listed
    assert not any(p.startswith((".", "linkdir")) or p == "notes/linked.tex" for p in listed)


def test_a_file_only_in_the_cloud_is_flagged_and_not_read(root, monkeypatch):
    real = paths.is_dataless
    monkeypatch.setattr(paths, "is_dataless", lambda p: Path(p).name == "old.tex" or real(p))
    (old,) = [r for r in latex_edit.drafts(root)["others"] if r["path"] == "notes/old.tex"]
    assert old["cloud"] is True


def test_the_counts_follow_the_ledger_and_reading_writes_nothing(root):
    text = (root / "paper/chapters/intro.tex").read_text()
    revision = hashlib.sha256(text.encode()).hexdigest()
    _put(root, "figures/f1.png", "png")
    start = text.index("很慢")
    latex_edit.evidence_act(root, "link", {"path": "paper/chapters/intro.tex", "revision": revision,
                                           "from": start, "to": start + 3, "file": "figures/f1.png"})
    latex_edit.evidence_act(root, "link", {"path": "paper/chapters/intro.tex", "revision": revision,
                                           "from": 0, "to": 5, "file": "figures/f1.png"})
    _put(root, "paper/chapters/intro.tex", text.replace("Intro", "Outro"))   # the second passage's words changed
    before = _files(root)
    got = latex_edit.drafts(root)
    (intro,) = [f for f in got["groups"][0]["files"] if f["path"] == "paper/chapters/intro.tex"]
    assert (intro["supported"], intro["waiting"]) == (2, 1)
    assert _files(root) == before


def test_an_empty_project_lists_nothing(tmp_path):
    root = tmp_path / "e"
    root.mkdir()
    project.init_project(root)
    assert latex_edit.drafts(root) == {"groups": [], "others": [], "generated": 0, "truncated": False,
                                       "limit": latex_edit.TEX_FILE_LIMIT, "message": None}


def test_the_walk_stops_at_the_limit_and_says_so(root, monkeypatch):
    monkeypatch.setattr(latex_edit, "TEX_FILE_LIMIT", 3)
    got = latex_edit.drafts(root)
    assert got["truncated"] is True and got["limit"] == 3
    assert sum(1 + len(g["files"]) for g in got["groups"]) + len(got["others"]) <= 3


# -- the endpoint --------------------------------------------------------------------------------------------------------


def _get(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def test_the_endpoint_answers_with_the_project_writes_nothing_and_refuses_a_blocked_project(root):
    httpd = server.build_server(root, 0)
    httpd.start_scan = lambda target: None
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        before = _files(root)
        status, got = _get(port, "/api/latex/drafts")
        assert status == 200 and got["project"] == str(root) and got["groups"][0]["main"]["path"] == "paper/main.tex"
        assert _files(root) == before
        status, refused = _get(port, "/api/latex/drafts?project=/elsewhere")
        assert status == 409
        httpd.set_served(dataclasses.replace(httpd.get_served(), blocked={"situation": "copy"}))
        status, refused = _get(port, "/api/latex/drafts")
        assert (status, refused["state"]) == (409, "project_blocked")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# -- the sidebar and the page -------------------------------------------------------------------------------------------


def test_the_sidebar_has_manuscript_right_after_figures_and_claims_drawn_like_the_others():
    html = APP_HTML.read_text(encoding="utf-8")
    nav = html[html.index('<nav class="side-nav"'):html.index("</nav>")]
    items = re.findall(r'<button type="button" class="side-item" data-section="(\w+)" data-view="(\w+)">'
                       r'<svg class="side-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="[^"]+"/></svg>'
                       r'<span class="side-label" data-i18n>([^<]+)</span>', nav)
    assert [s for s, _v, _l in items].index("drafts") == [s for s, _v, _l in items].index("results") + 1
    assert ("drafts", "drafts", "正文") in items
    assert '<section id="view-drafts" class="view hidden"></section>' in html
    assert 'drafts: "drafts"' in html and 'drafts: t("正文")' in html
    assert 'if (name === "drafts" && !views.drafts.dataset.loaded) ROLatex.loadDrafts(views.drafts);' in html
    # the editor counts as the page it was opened from: its section is ROLatex.section()'s
    assert 'const page = name === "latex" ? ROLatex.section() : name;' in html


_PAGE = r"""
const texts = (el) => typeof el === 'string' ? [el] : [el.textContent].concat(...el.children.filter(Boolean).map(texts));
const host = mk('section'); host.dataset = {};
const row = (path, extra) => Object.assign({path, modified: null, cloud: false, supported: 0, waiting: 0}, extra);
let p = L.loadDrafts(host); await tick();
out.url = gets[0].url;
gets.shift().res({groups: [{main: row('paper/main.tex', {supported: 3, waiting: 1}),
  files: [row('paper/chapters/intro.tex'), row('paper/chapters/gone.tex', {cloud: true})]}],
  others: [row('notes/old.tex')], generated: 2, truncated: false, limit: 2000, message: null});
await p; await tick();
const inHost = (el) => { const seen = []; const walk = (e) => { if (typeof e === 'string' || !e) return; seen.push(e); e.children.forEach(walk); }; walk(el); return seen; };
const els = inHost(host);
out.loaded = host.dataset.loaded;
out.texts = texts(host).filter(Boolean);
out.primary = els.filter((e) => /btn-primary/.test(e.className)).length;
out.pathButtons = els.filter((e) => e.tag === 'button' && /mono/.test(e.className)).map((e) => e.textContent);
out.cloudIsText = els.some((e) => e.tag === 'span' && e.textContent === 'paper/chapters/gone.tex');
out.ochre = els.filter((e) => /tone-ochre/.test(e.className)).map((e) => e.textContent);
out.exportClass = els.filter((e) => e.textContent === '导出 PDF').map((e) => e.className);
// a path opens the editor; the editor then belongs to 正文
currentView = 'drafts';
els.find((e) => e.tag === 'button' && e.textContent === 'paper/chapters/intro.tex').fire('click'); await tick();
out.opened = gets[0].url; out.section = L.section();
gets.shift().rej({state: 'x'}); await tick();
// the empty state
currentView = 'drafts';
const empty = mk('section'); empty.dataset = {};
p = L.loadDrafts(empty); await tick();
gets.shift().res({groups: [], others: [], generated: 0, truncated: false, limit: 2000, message: null}); await p;
out.empty = texts(empty).filter(Boolean);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def page() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    script = (prelude + "const out = {};\n(async () => {\n" + _PAGE
              + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });")
    got = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True, timeout=60).stdout
    out = json.loads(got.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    return out


def test_the_page_lists_the_groups_with_counts_and_a_quiet_export_and_no_primary(page):
    assert page["url"] == "/api/latex/drafts" and page["loaded"] == "1"
    assert page["texts"][0] == "项目里的 LaTeX 文稿。点一份就在编辑器里打开；改动保存回原文件。"
    assert page["pathButtons"] == ["paper/main.tex", "paper/chapters/intro.tex", "notes/old.tex"]
    assert page["cloudIsText"] and "只在云端" in page["texts"]
    assert "3 段有依据文件" in page["texts"] and page["ochre"] == ["1 段待复核"]
    assert page["exportClass"] == ["ws-text-link"] and page["primary"] == 0
    assert "其他 .tex 文件" in page["texts"]
    assert page["texts"][-1] == "另有 2 个由脚本生成的 .tex 表格，只能查看，不在这里编辑。"


def test_a_path_opens_the_editor_which_belongs_to_the_manuscript_page(page):
    assert page["opened"] == "/api/latex/file?path=paper%2Fchapters%2Fintro.tex" and page["section"] == "drafts"


def test_an_empty_project_says_so_and_where_drafts_come_from(page):
    assert page["empty"][-2:] == ["这个项目里还没有 .tex 文稿。", "把 .tex 文件放进项目文件夹后，这里会列出来。"]


def test_every_new_word_has_its_english():
    i18n = (WEBAPP / "i18n.js").read_text(encoding="utf-8")
    for zh, en in [("正文", "Manuscript"), ("只在云端", "Only in the cloud"), ("其他 .tex 文件", "Other .tex files"),
                   ("项目里的 LaTeX 文稿。点一份就在编辑器里打开；改动保存回原文件。", "The project's LaTeX drafts."),
                   ("这个项目里还没有 .tex 文稿。", "This project has no .tex drafts yet."),
                   ("把 .tex 文件放进项目文件夹后，这里会列出来。", "Put .tex files in the project folder"),
                   ("没能读到项目里的文稿", "Could not read the project's drafts"),
                   ("项目里的 .tex 文件多于 {n} 个，这里只列出前 {n} 个。", "The project has more than {n} .tex files"),
                   ("另有 {n} 个由脚本生成的 .tex 表格，只能查看，不在这里编辑。", "There is also {n} .tex table")]:
        at = i18n.index(json.dumps(zh, ensure_ascii=False) + ":")
        assert en in i18n[at:at + 400], zh

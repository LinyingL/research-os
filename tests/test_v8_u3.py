"""Research OS V8 phase U3 (DESIGN.md 12.6, 12.7, 12.9, 12.11): the node
page -- one page for every object, its trace drawn as columns with every
break a gap and its repair, its changes and notes as one timeline, its
judgments, the canvas scoped to its neighbourhood -- and the sections built
on it: 图表与论断 (figures, tables and claims with their trace state) and 数据与代码
(scripts and data with the lineage report's blocks as filters). Every test
names its scenario."""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.parse
from pathlib import Path

import pytest

from researchos import nodepage
from researchos.records import cards
from researchos.records import notes as notes_mod
from researchos.webapp import server

from test_v8_phase2 import _call, _conn, _csv, _project, _scan
from test_v8_phase3 import _serve
from test_v8_u2 import FORBIDDEN, WEBAPP

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _page(root: Path, node: str) -> dict:
    conn = _conn(root)
    try:
        return nodepage.node(conn, root, node)
    finally:
        conn.close()


def _titles(page: dict) -> list[str]:
    return [c["title"] for c in page["trace"]["columns"]]


def _labels(page: dict) -> list[list[str]]:
    return [[i["label"] for i in c["items"]] for c in page["trace"]["columns"]]


def _card(root: Path) -> None:
    cards.new_card(root, "panelx")
    (notes_mod.notes_path(root).parent / "variables" / "panelx" / "v1.toml").write_text(
        'name = "Panel X"\nmeaning = \'\'\'x\'\'\'\nunit = "1"\ngranularity = "月"\n\n'
        '[[input]]\ndataset = "data/raw.csv"\n\n[construction]\nformula = \'\'\'x\'\'\'\n\n'
        '[implementation]\nscript = "steps/16-build.py"\noutput = "data/panel.csv"\nfield = "x"\n\n'
        "[decision]\nwhy = '''because'''\n",
        encoding="utf-8",
    )
    cards.confirm(root, "panelx", attested="yes")


# -- the node page: the trace as columns (12.7) ------------------------------------------------------


def test_12_7_a_figure_page_draws_data_scripts_intermediate_data_and_the_draft_as_columns(tmp_path):
    """The figure's page: 数据 | 脚本 | 中间数据 | 脚本 | 本节点 | 被引用, each
    item a node of its own, joined by the lines of the chain (a `reads` from
    the data to the script, a `writes`/`generates` from the script on), the
    attempt it belongs to and the variable version it uses below."""
    root = _project(tmp_path / "proj")
    _card(root)
    page = _page(root, "figs/fig2.png")
    assert page["id"] == "figure:figs/fig2.png" and page["type_label"] == "图表" and page["exists"]
    assert _titles(page) == ["数据", "脚本", "中间数据", "脚本", "本页", "被引用"]
    assert _labels(page) == [["raw.csv"], ["16-build.py"], ["panel.csv"], ["17-plot.py"], ["fig2.png"], ["Main figure"]]
    lines = {(l["from"], l["relation"], l["to"]) for l in page["trace"]["lines"]}
    assert ("dataset:data/raw.csv", "读取", "script:steps/16-build.py") in lines
    assert ("script:steps/16-build.py", "写出", "dataset:data/panel.csv") in lines
    assert ("dataset:data/panel.csv", "读取", "script:steps/17-plot.py") in lines
    assert ("script:steps/17-plot.py", "写出", "figure:figs/fig2.png") in lines
    assert ("figure:figs/fig2.png", "引用", "section:paper.md#main-figure") in lines
    assert [a["id"] for a in page["trace"]["attempts"]] == ["attempt:map.md#16"]
    assert page["trace"]["variables"][0]["variable"] == "panelx"
    assert page["trace"]["breaks"] == []
    assert page["trace"]["columns"][0]["items"][0]["raw"] is True
    assert {(l["src_label"], l["relation"], l["dst_label"]) for l in page["links"]} >= {("17-plot.py", "写出", "fig2.png")}


def test_12_7_a_figure_without_a_source_shows_the_gap_with_its_repair_and_the_choices(tmp_path):
    """A break is a gap where the missing piece would be, offering its
    repair: 「指定生成它的脚本」 (21.5; the write 补上来源 made) in the script column with the project's scripts to
    choose from, 「归入尝试」 for the attempt with the attempts (as status
    words, never markers)."""
    root = _project(tmp_path / "proj")
    page = _page(root, "figs/fig1.png")
    assert _titles(page) == ["脚本", "本页", "被引用"]
    gap = page["trace"]["columns"][0]["gap"]
    assert gap["kind"] == "no_generating_script" and gap["repair"] == {"type": "generates", "label": "指定生成它的脚本", "file": "figs/fig1.png"}
    assert {b["kind"] for b in page["trace"]["breaks"]} == {"no_generating_script", "no_attempt"}
    assert [s["path"] for s in page["choices"]["scripts"]] == ["steps/16-build.py", "steps/17-plot.py"]
    attempt = page["choices"]["attempts"][0]
    assert attempt["id"] == "attempt:map.md#16" and attempt["verdict_status"]["label"] == "待定"


def test_12_7_a_script_page_shows_what_it_read_upstream_and_what_it_writes(tmp_path):
    root = _project(tmp_path / "proj")
    page = _page(root, "steps/17-plot.py")
    assert _titles(page) == ["数据", "脚本", "中间数据", "本页", "写出"]
    assert _labels(page)[-1] == ["fig2.png"]
    data = _page(root, "data/panel.csv")
    assert _titles(data) == ["数据", "脚本", "本页", "被读取"]
    assert _labels(data)[-1] == ["17-plot.py"]
    via = data["trace"]["attempts"][0]["via"]
    assert len({json.dumps(v, sort_keys=True) for v in via}) == len(via)  # each claim once
    script = _page(root, "steps/16-build.py")["trace"]["attempts"][0]["via"]
    assert script == [{"via": "step_files", "through": "script:steps/16-build.py"}]  # not twice on its own page


def test_12_7_an_attempt_page_lists_its_files_and_a_variable_page_its_definition(tmp_path):
    """An attempt: 读取的数据 | 脚本 | 输出 from its step scripts, each marked
    步骤脚本; a variable: 输入 | 脚本 | 输出 | 本节点 from its card."""
    root = _project(tmp_path / "proj")
    _card(root)
    page = _page(root, "attempt:map.md#16")
    assert page["type_label"] == "尝试" and page["details"]["verdict_status"]["label"] == "待定"
    assert _titles(page) == ["读取的数据", "脚本", "输出"]
    assert _labels(page)[1] == ["16-build.py", "17-plot.py"]
    assert {i["member"] for i in page["trace"]["columns"][1]["items"]} == {"step_files"}
    assert "figs/fig2.png" in [f["path"] for f in page["choices"]["files"]]
    variable = _page(root, "variable:panelx")
    assert variable["name"] == "Panel X" and _titles(variable) == ["输入", "脚本", "输出", "本页"]
    assert variable["trace"]["columns"][2]["items"][0]["note"] == "列 x"
    with pytest.raises(nodepage.NotFound):
        _page(root, "variable:nope")
    with pytest.raises(nodepage.NotFound):
        _page(root, "figs/none.png")
    with pytest.raises(ValueError):
        _page(root, "/etc/passwd")


def test_12_7_a_claim_without_backing_and_a_section_with_its_figures(tmp_path):
    root = _project(tmp_path / "proj")
    (root / "paper.md").write_text((root / "paper.md").read_text() + "\nThe effect is 0.42 in the panel.\n")
    _scan(root)
    conn = _conn(root)
    try:
        claim = conn.execute("SELECT id FROM nodes WHERE type = 'claim'").fetchone()[0]
    finally:
        conn.close()
    page = _page(root, claim)
    assert page["type_label"] == "论断" and page["trace"]["columns"][0]["gap"]["kind"] == "no_backing"
    section = _page(root, "section:paper.md#main-figure")
    assert _titles(section)[:2] == ["引用的图表与文献", "本页"]
    assert set(_labels(section)[0]) == {"fig1.png", "fig2.png"}


# -- 图表与论断 and 数据与代码 (12.9) ------------------------------------------------------------------------


def test_12_9_results_lists_the_drafts_figures_by_section_with_their_trace_state(tmp_path):
    root = _project(tmp_path / "proj")
    (root / "figs" / "loose.png").write_bytes(b"\x89PNG")
    (root / "steps" / "18-loose.py").write_text('import matplotlib.pyplot as plt\nplt.savefig("figs/loose.png")\n')
    (root / "paper.md").write_text((root / "paper.md").read_text() + "\nThe effect is 0.42 in the panel.\n")
    _scan(root)
    conn = _conn(root)
    try:
        out = nodepage.results(conn, root)
        claims = nodepage.results(conn, root, "paper.md")
    finally:
        conn.close()
    # 21.5: one row per figure, naming the drafts that include it; with no draft marked every draft stands for the paper
    assert out["other"] == [] and out["excluded"] == []
    states = {i["path"]: i["trace"]["label"] for i in out["items"]}
    assert states == {"figs/fig1.png": "缺来源", "figs/fig2.png": "完整"} and all(i["drafts"] == ["paper.md"] for i in out["items"])
    assert [(i["path"], i["trace"]["label"]) for i in out["loose"]] == [("figs/loose.png", "缺尝试")]
    # 12.13: claims only from a draft marked 「设为论文正文」 -- none yet; every draft is offered.
    assert out["claims"] == [] and out["papers"]["marked"] == []
    assert {"file": "paper.md", "marked": False, "claims": 1, "node": "file:paper.md"}.items() <= next(d for d in out["drafts"] if d["file"] == "paper.md").items()  # 21.5 adds `modified` (the picker's day)
    from researchos.records import papers

    assert papers.set_marked(root, "paper.md", True, via="cli")["changed"]
    conn = _conn(root)
    try:
        out = nodepage.results(conn, root)
    finally:
        conn.close()
    assert out["claims"] == [{"file": "paper.md", "count": 1}]
    assert [d["file"] for d in out["drafts"] if d["marked"]] == ["paper.md"]
    assert claims["total"] == 1 and claims["sections"][0]["claims"][0]["trace"]["label"] == "缺来源"
    assert claims["sections"][0]["claims"][0]["number"] == "0.42"


def test_12_9_data_code_marks_orphans_chains_broken_links_and_copies(tmp_path):
    root = _project(tmp_path / "proj")
    (root / "old").mkdir()
    (root / "old" / "raw.csv").write_text(_csv(2))
    (root / "steps" / "18-gone.py").write_text('import pandas as pd\npd.read_csv("data/gone.csv")\n')
    _scan(root)
    conn = _conn(root)
    try:
        out = nodepage.data_code(conn, root)
    finally:
        conn.close()
    by = {f["path"]: f for f in out["files"]}
    assert by["data/raw.csv"]["orphan_input"] and by["data/raw.csv"]["other_copies"] == ["old/raw.csv"]
    assert by["data/panel.csv"]["written_by"] == ["steps/16-build.py"] and by["data/panel.csv"]["read_by"] == ["steps/17-plot.py"]
    assert by["steps/17-plot.py"]["reads"] == ["data/panel.csv"] and by["steps/17-plot.py"]["writes"] == ["figs/fig2.png"]
    assert by["steps/18-gone.py"]["broken"][0]["target"] == "data/gone.csv"
    assert (by["steps/18-gone.py"]["broken"][0]["type"], by["steps/18-gone.py"]["broken"][0]["relation"]) == ("reads", "读取")
    # 21.5: raw/csv is only read -- quiet, not a problem; the script that names data/gone.csv is the one broken link
    assert by["data/raw.csv"]["no_generator"] == "quiet" and out["counts"]["no_generator"] == 0 and out["counts"]["raw"] == 1
    assert out["counts"]["duplicates"] == 1 and out["counts"]["broken"] == 1


# -- through the app's endpoints ------------------------------------------------------------------------


@pytest.fixture
def live(tmp_path):
    root = _project(tmp_path / "proj")
    with _serve(root) as base:
        yield base, root


def test_12_7_repairs_go_through_the_mapping_write_path_and_the_page_follows(live):
    """「补上来源」 and 「关联文件」 from the page: POST /api/mappings/add with
    paths the page was given; the page then shows the human line and the
    attempt, and the links under 连线确认 offer 删除标注."""
    base, root = live
    status, body = _call(f"{base}/api/mappings/add", "POST",
                         {"from": "steps/16-build.py", "to": "figs/fig1.png", "type": "generates"})
    assert status == 200 and body["ok"]
    status, body = _call(f"{base}/api/mappings/add", "POST",
                         {"from": "figs/fig1.png", "to": "attempt:map.md#16", "type": "belongs_to"})
    assert status == 200
    status, page = _call(f"{base}/api/node?node={_q('figs/fig1.png')}")
    assert status == 200 and page["trace"]["breaks"] == []
    human = [l for l in page["trace"]["lines"] if l["human"]]
    assert [(l["from"], l["to"]) for l in human] == [("script:steps/16-build.py", "figure:figs/fig1.png")]
    assert {"via": "belongs_to", "through": "figure:figs/fig1.png"} in page["trace"]["attempts"][0]["via"]
    assert {l["type"] for l in page["links"] if l["human"]} == {"generates", "belongs_to"}
    assert page["belongs_to"][0]["attempt"] == "attempt:map.md#16"
    status, body = _call(f"{base}/api/mappings/add", "POST",
                         {"from": "steps/16-build.py", "to": "figs/fig1.png", "type": "generates"})
    assert status == 409 and body["state"] == "mapping_exists"
    status, attempt = _call(f"{base}/api/node?node={_q('attempt:map.md#16')}")
    assert "fig1.png" in [i["label"] for i in attempt["trace"]["columns"][2]["items"]]


def test_12_6_notes_judgments_and_the_word_only_diff_through_the_endpoints(live):
    base, root = live
    status, body = _call(f"{base}/api/notes", "POST", {"action": "add", "node": "data/panel.csv", "text": "单位是百分点"})
    assert status == 200
    status, page = _call(f"{base}/api/node?node={_q('data/panel.csv')}")
    link = next(l for l in page["links"] if l["type"] == "reads")
    assert link["evidence"][0]["line"] == 3 and link["relation"] == "读取"
    status, body = _call(f"{base}/api/judgements", "POST",
                         {k: link[k] for k in ("src", "dst", "type", "extractor")} | {"verdict": "confirmed"})
    assert status == 200
    status, page = _call(f"{base}/api/node?node={_q('data/panel.csv')}")
    link = next(l for l in page["links"] if l["type"] == "reads")
    assert link["status"] == "confirmed" and link["judgement"]["verdict"] == "confirmed"
    (root / "data" / "panel.csv").write_text(_csv(97, ("id", "x", "log_ret")))
    _scan(root)
    status, diff = _call(f"{base}/api/history/diff?node={_q('data/panel.csv')}")
    assert status == 200 and diff["summary_words"] == "行数从 96 增至 97，新增列 log_ret"
    assert not FORBIDDEN.search(diff["summary_words"])
    status, notes = _call(f"{base}/api/notes?node={_q('dataset:data/panel.csv')}")
    note = notes["notes"][0]
    status, body = _call(f"{base}/api/notes", "POST", {"action": "withdraw", "id": note["id"]})
    assert status == 200


def test_12_9_the_endpoints_are_origin_checked_and_node_js_is_served_and_packaged(live):
    base, root = live
    for path in (f"/api/node?node={_q('figs/fig2.png')}", "/api/results", "/api/data-code", "/node.js"):
        status, _ = _call(f"{base}{path}", headers={"Origin": "http://evil.example"})
        assert status == 403, path
    import urllib.request

    with urllib.request.urlopen(f"{base}/node.js") as resp:
        assert resp.headers["Content-Type"].startswith("text/javascript")
        assert resp.read().decode("utf-8") == (WEBAPP / "node.js").read_text(encoding="utf-8")
    assert "/node.js" in server._NO_PROJECT_GET
    pyproject = (Path(server.__file__).parents[3] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"webapp/node.js"' in pyproject
    html = (WEBAPP / "app.html").read_text(encoding="utf-8")
    assert '<script src="/node.js"></script>' in html and 'id="view-node"' in html
    status, body = _call(f"{base}/api/node?node={_q('figs/none.png')}")
    assert status == 404 and body["message"] == "找不到要打开的内容"
    status, body = _call(f"{base}/api/node")
    assert status == 400


def test_12_9_the_canvas_of_a_node_page_is_its_neighbourhood_and_keeps_no_arrangement(live):
    base, root = live
    status, canvas = _call(f"{base}/api/canvas?scope={_q('near:figure:figs/fig2.png')}")
    assert status == 200 and canvas["frozen"] is True and canvas["scope"]["near"] == "figure:figs/fig2.png"
    assert {n["id"] for n in canvas["nodes"]} == {"figure:figs/fig2.png", "script:steps/17-plot.py", "dataset:data/panel.csv"}
    assert canvas["frames"] == []
    status, body = _call(f"{base}/api/canvas/layout", "POST", {
        "project": canvas["project"], "scope": canvas["scope"]["id"], "positions": {"figure:figs/fig2.png": [0, 0]}})
    assert status == 404
    status, _ = _call(f"{base}/api/canvas?scope={_q('near:figure:figs/nope.png')}")
    assert status == 404


# -- a member's own work on the node page (12.12) ----------------------------------------------------------


def test_12_12_a_members_own_script_and_link_are_marked_on_the_node_page(tmp_path, monkeypatch):
    from researchos import signing

    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    from test_v8_phase4 import Drive

    drive = Drive(tmp_path, monkeypatch)
    with drive.member():
        (drive.work / "steps" / "19-new.py").write_text('import pandas as pd\ndf = pd.read_csv("data/raw.csv")\n')
        from researchos import cli

        assert cli.main(["ingest", str(drive.work)]) == 0
        with _serve(drive.work) as base:
            status, page = _call(f"{base}/api/node?node={_q('steps/19-new.py')}")
            assert status == 200 and page["officialness"]["state"] == "mine"
            assert page["officialness_mode"]["mode"] == "working_copy"
            status, body = _call(f"{base}/api/mappings/add", "POST",
                                 {"from": "steps/19-new.py", "to": "attempt:map.md#16", "type": "belongs_to"})
            assert status == 200
            status, page = _call(f"{base}/api/node?node={_q('steps/19-new.py')}")
            own = [l for l in page["links"] if l["type"] == "belongs_to"]
            assert own and own[0]["officialness"]["state"] == "mine"
            status, files = _call(f"{base}/api/data-code")
            assert {f["path"]: f["officialness"]["state"] for f in files["files"]}["steps/19-new.py"] == "mine"


# -- the page's own wording (node.js, pure) ----------------------------------------------------------------


def _node_call(name: str, *args) -> object:
    script = (
        "global.window = {addEventListener(){}}; global.document = {getElementById(){return null}};\n"
        + (WEBAPP / "node.js").read_text(encoding="utf-8")
        + f"\nprocess.stdout.write(JSON.stringify(window.RONode.{name}(...{json.dumps(list(args))})));\n"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@needs_node
def test_node_page_wording_diff_rows_judgments_and_the_timeline():
    rows = _node_call("_diffRows", "--- a\n+++ b\n@@ -1,2 +1,2 @@\n x\n-y\n+z\n\\ No newline at end of file\n")
    assert [r["cls"] for r in rows] == ["hunk", "ctx", "del", "add", "meta"]
    assert rows[2]["text"] == "-y"  # the diff's own prefix, nothing added
    machine = {"human": False, "status": "auto", "review": False, "conflict": False, "judgement": None}
    assert _node_call("_judgeActions", machine) == ["confirmed", "rejected"]
    assert _node_call("_judgeActions", {**machine, "status": "confirmed"}) == ["rejected", "withdrawn"]
    assert _node_call("_judgeActions", {**machine, "status": "rejected"}) == ["confirmed", "withdrawn"]
    assert _node_call("_judgeActions", {"human": True}) == ["delete"]
    assert _node_call("_mappingEnds", {"type": "reads", "src": "script:s.py", "dst": "dataset:d.csv"}) == \
        {"from": "d.csv", "to": "s.py", "type": "reads"}
    assert _node_call("_mappingEnds", {"type": "belongs_to", "src": "figure:f.png", "dst": "attempt:map.md#16"}) == \
        {"from": "f.png", "to": "attempt:map.md#16", "type": "belongs_to"}
    assert _node_call("_noteIsMine", {"by": "li@example.com"}, "li@example.com") is True
    assert _node_call("_noteIsMine", {"by": "wang@example.com"}, "li@example.com") is False
    assert _node_call("_noteIsMine", {}, None) is True
    old = {"verdict": "🕒 待拍板", "verdict_status": {"label": "待定"}, "result": ""}
    new = {"verdict": "✅ 现行", "verdict_status": {"label": "现行"}, "result": "t=2.91"}
    moved = _node_call("_verdictMove", old, new)
    assert moved == "研究判断从「待定」改为「现行」，结果改为：t=2.91" and not FORBIDDEN.search(moved)
    assert _node_call("_verdictMove", None, new) == "记下这条尝试，研究判断为「现行」"
    assert _node_call("_tableWords", {"rows": 3, "columns": ["a", "b"]}) == "3 行，列 a、b"
    history = {"kind": "file", "versions": [{"at": "2026-10-11T10:00:00", "version": "2"}, {"at": "2026-10-10T10:00:00", "version": "1"}]}
    notes = [{"at": "2026-10-10T12:00:00", "text": "x"}]
    links = [{"human": False, "judgement": {"verdict": "confirmed", "at": "2026-10-11T11:00:00"}}]
    kinds = [e["kind"] for e in _node_call("_timeline", history, notes, links)]
    assert kinds == ["judgment", "version", "note", "version"]
    assert _node_call("_inFilter", {"type": "dataset", "written_by": ["a"], "read_by": ["b"]}, "chains") is True
    # 21.5: a file that is not there is a plain fact; only a script that names a missing file is a broken link.
    assert _node_call("_inFilter", {"type": "script", "exists": False}, "broken") is False
    assert _node_call("_inFilter", {"type": "script", "broken": [{"target": "d.csv"}]}, "broken") is True
    assert _node_call("_inFilter", {"type": "dataset", "no_generator": "warning"}, "missing") is True
    assert _node_call("_inFilter", {"type": "dataset", "no_generator": "quiet"}, "missing") is False
    assert _node_call("_inFilter", {"type": "dataset", "no_generator": "quiet"}, "raw") is True
    assert _node_call("_fileRelations", {"type": "script", "reads": [], "writes": []}) == ["未识别到读写关联"]
    for text in _node_call("_fileRelations", {"type": "dataset", "written_by": ["s/a.py"], "read_by": ["s/b.py"]}):
        assert not FORBIDDEN.search(text)


def test_12_11_node_js_is_one_of_the_scripts_the_scanner_reads():
    from test_v8_u2 import _served_scripts

    assert WEBAPP / "node.js" in _served_scripts()

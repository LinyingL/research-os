"""DESIGN.md 12.13 -- rulings from building and accepting the V8 interface:
words not arrows on screen; who a version came from; 「已批准，待同步」;
what a proposal carries by kind; tables summary first; a link's note in the
timeline; 成果 lists claims only from drafts marked 「设为论文正文」; a
member's two folders. Every test names its ruling."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

from researchos import cli, history, nodepage, officialness, people, proposals, share, signing
from researchos import exchange as exchange_mod
from researchos.ingest import mappings as mappings_ingest
from researchos.records import papers
from researchos.records.identity import read_identity
from researchos.webapp import node_api, registry, variables_api
from researchos.records import cards

from test_v8_phase2 import _conn, _csv, _map, _project, _scan
from test_v8_phase4 import BIG, MEMBER, NEW_SCRIPT, PI, ROW_18, Drive, Witness, _as, _sparse
from test_v8_u2 import FORBIDDEN, WEBAPP

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
ARROWS = re.compile(r"→|←|↔|->|=>")


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Drive:
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    return Drive(tmp_path, monkeypatch)


def _node_call(name: str, *args) -> object:
    script = (
        "global.window = {addEventListener(){}}; global.document = {getElementById(){return null}};\n"
        + (WEBAPP / "node.js").read_text(encoding="utf-8")
        + f"\nprocess.stdout.write(JSON.stringify(window.RONode.{name}(...{json.dumps(list(args))})));\n"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


# -- words, not arrows (12.13) ------------------------------------------------------------------------


def test_12_13_change_sentences_have_a_word_form_and_the_command_line_keeps_its_arrows():
    a = {"format": "csv", "columns": ["id", "x"], "rows": 96, "rows_complete": True}
    b = {"format": "csv", "columns": ["id", "x", "log_ret"], "rows": 97, "rows_complete": True}
    assert history.table_sentence(a, b) == "行数 96 → 97，新增列 log_ret"
    assert history.table_sentence(a, b, words=True) == "行数从 96 增至 97，新增列 log_ret"
    assert history.table_sentence(b, a, words=True) == "行数从 97 减至 96，删除列 log_ret"
    xa = {"format": "xlsx", "sheet_rows": 5, "sheet_columns": 2}
    xb = {"format": "xlsx", "sheet_rows": 5, "sheet_columns": 3}
    assert history.table_sentence(xa, xb, words=True) == "行数不变（5），列数从 2 增至 3"
    assert history._size_sentence({"size": 10}, {"size": 12}, words=True) == "内容不同（大小从 10 变为 12 字节）"
    assert "→" in history._size_sentence({"size": 10}, {"size": 12})
    old = {"verdict": "🕒", "verdict_status": {"label": "待定"}, "result": ""}
    new = {"verdict": "✅", "verdict_status": {"label": "现行"}, "result": "t=2.91"}
    moved = history._attempt_change(old, new, words=True)["summary"]
    assert moved == "判决从「待定」改为「现行」，结果改为：t=2.91"
    assert history._attempt_change(old, new)["summary"].startswith("🕒 → ✅")
    card = history._variable_change({"act": "confirmed", "number": 1}, {"act": "confirmed", "number": 2}, words=True)
    assert card == "从「确认第 1 版」改为「确认第 2 版」" and not ARROWS.search(card)


def test_12_13_every_payload_the_page_reads_says_its_change_in_words(tmp_path):
    root = _project(tmp_path / "proj")
    (root / "data" / "panel.csv").write_text(_csv(97, ("id", "x", "log_ret")))
    (root / "map.md").write_text(_map("✅"))
    _scan(root)
    diff = node_api.diff_payload(root, {"node": ["data/panel.csv"]})
    assert diff["summary_words"] == "行数从 96 增至 97，新增列 log_ret" and not ARROWS.search(diff["summary"])
    # 12.13: a small table shows its line diff below its summary.
    assert diff["table"] and diff["table_small"] and diff["diff"]
    verdict = node_api.diff_payload(root, {"node": ["attempt:map.md#16"]})
    assert verdict["summary_words"] == "判决从「待定」改为「现行」" and not FORBIDDEN.search(verdict["summary_words"])
    from researchos import desk

    conn = _conn(root)
    try:
        out = desk.desk(conn, root, None)
    finally:
        conn.close()
    whats = [c.get("what") or "" for c in out["changes"]]
    assert not any(ARROWS.search(w) for w in whats)
    for name in ("node.js", "team.js", "desk.js"):
        source = (WEBAPP / name).read_text(encoding="utf-8")
        assert "→" not in source and "▸" not in source and "▾" not in source


def test_12_13_a_large_table_leads_with_rows_and_columns_and_leaves_its_line_diff_out(tmp_path):
    root = _project(tmp_path / "proj")
    (root / "data" / "panel.csv").write_text(_csv(9000, ("id", "x")))
    _scan(root)
    (root / "data" / "panel.csv").write_text(_csv(9001, ("id", "x")))
    _scan(root)
    diff = node_api.diff_payload(root, {"node": ["data/panel.csv"]})
    assert diff["summary_words"] == "行数从 9000 增至 9001，列不变" and diff["table_small"] is False
    assert diff["diff"] is None and diff["diff_left_out"]
    assert history.diff(root, "data/panel.csv")["diff"]  # the command line still has it


# -- who (12.13) -----------------------------------------------------------------------------------------


def test_12_13_a_version_sync_installed_is_the_publish_s_the_pi_and_the_snapshot(drive):
    with drive.pi():
        (drive.official / "steps" / "16-build.py").write_text("print('pi')\n")
        share.publish(drive.official, note="改了构建")
    with drive.member():
        done = share.sync(drive.work)
        assert "steps/16-build.py" in done["updated"]
        [newest, joined] = history.versions(drive.work, "steps/16-build.py")["versions"][:2]
        assert (newest["source"], newest["snapshot"], newest["who"]) == ("publish", 2, PI[1])
        assert newest["who_label"] == PI[0] and newest["note"] == "改了构建"
        assert joined["snapshot"] == 1 and joined["who_label"] == PI[0]
        (drive.work / "steps" / "16-build.py").write_text("print('mine')\n")
        assert cli.main(["ingest", str(drive.work)]) == 0
        assert history.versions(drive.work, "steps/16-build.py")["versions"][0]["who_label"] == "本机"
    with drive.pi():
        unknown = history.versions(drive.official, "steps/17-plot.py")["versions"][-1]
        assert unknown["who_label"] == "最初的研究者"


@needs_node
def test_12_13_the_node_page_names_the_publish_a_version_came_from():
    assert _node_call("_sourceWords", {"source": "publish", "snapshot": 3}) == "随第 3 版发布同步"
    assert _node_call("_sourceWords", {"source": "scan"}) == "扫描时记下"


def test_12_13_variable_card_authors_are_named_by_the_team(drive):
    with drive.pi():
        cards.new_card(drive.official, "panelx")
        (drive.official / ".researchos" / "variables" / "panelx" / "v1.toml").write_text(
            'name = "Panel X"\nmeaning = \'\'\'x\'\'\'\nunit = "1"\ngranularity = "月"\n\n'
            '[[input]]\ndataset = "data/raw.csv"\n\n[construction]\nformula = \'\'\'x\'\'\'\n\n'
            '[implementation]\nscript = "steps/16-build.py"\noutput = "data/panel.csv"\nfield = "x"\n\n'
            "[decision]\nwhy = '''because'''\n", encoding="utf-8")
        cards.confirm(drive.official, "panelx", attested="yes")
        conn = _conn(drive.official)
        try:
            card = variables_api.card_payload(conn, drive.official, "panelx")
        finally:
            conn.close()
    [entry] = card["history"]
    assert entry["by"] == PI[1] and entry["by_label"] == PI[0]
    app = (WEBAPP / "app.html").read_text(encoding="utf-8")
    assert "entry.by_label" in app


# -- 已批准，待同步 (12.13) ---------------------------------------------------------------------------


def test_12_13_an_approved_proposal_is_approved_awaiting_sync_until_the_member_syncs(drive):
    with drive.member():
        (drive.work / "steps" / "16-build.py").write_text("print('better')\n")
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(drive.work, "files", "改构建"))
        v = officialness.view(drive.work)
        assert v.file_state("steps/16-build.py") == ("proposed", stored.id)
    with drive.pi():
        proposals.approve(drive.official, stored.id, note="好")
    with drive.member():
        v = officialness.view(drive.work)
        state = v.file_state("steps/16-build.py")
        assert state == ("approved", stored.id)
        assert officialness.mark(state)["label"] == "已批准，待同步"
        share.sync(drive.work)
        assert officialness.view(drive.work).file_state("steps/16-build.py") == ("official", None)


# -- what a proposal carries (12.13) --------------------------------------------------------------------


def test_12_13_a_verdict_change_carries_its_row_only(drive):
    with drive.member():
        (drive.work / "steps" / "17-plot.py").write_text("print('changed')\n")
        (drive.work / "map.md").write_text(_map("✅"))
        draft = proposals.make_proposal(drive.work, "verdict_change", "定下来")
        assert draft.data["file"] == []
        offered = {c["path"]: c["chosen"] for c in draft.preview["candidates"]}
        assert offered == {"steps/17-plot.py": False}
        assert any(n["path"] == "steps/17-plot.py" and n["reason"] == "not chosen for this proposal"
                   for n in draft.data["not_carried"])
        added = proposals.make_proposal(drive.work, "verdict_change", "定下来", add=["steps/17-plot.py"])
        assert [f["path"] for f in added.data["file"]] == ["steps/17-plot.py"]


def test_12_13_a_new_attempt_carries_its_row_and_its_linked_files(drive):
    with drive.member():
        (drive.work / "steps" / "18-big.py").write_text(NEW_SCRIPT)
        (drive.work / "data" / "attached.csv").write_text("a\n1\n")
        (drive.work / "data" / "unrelated.csv").write_text("b\n2\n")
        (drive.work / "map.md").write_text(_map() + ROW_18)
        with open(drive.work / ".researchos" / "mappings.toml", "a", encoding="utf-8") as handle:
            handle.write('\n[[mapping]]\nfrom = "data/attached.csv"\nto = "attempt:map.md#18"\ntype = "belongs_to"\n')
        draft = proposals.make_proposal(drive.work, "attempt_new", "大面板")
        assert sorted(f["path"] for f in draft.data["file"]) == ["data/attached.csv", "steps/18-big.py"]
        linked = {c["path"]: (c["chosen"], c["linked"]) for c in draft.preview["candidates"]}
        assert linked["data/unrelated.csv"] == (False, False) and linked["steps/18-big.py"] == (True, True)
        unticked = proposals.make_proposal(drive.work, "attempt_new", "大面板", leave=["data/attached.csv"])
        assert [f["path"] for f in unticked.data["file"]] == ["steps/18-big.py"]


def test_12_13_a_file_from_a_returned_proposal_is_flagged(drive):
    with drive.member():
        (drive.work / "steps" / "17-plot.py").write_text("print('try')\n")
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(drive.work, "files", "试试"))
    with drive.pi():
        proposals.return_(drive.official, stored.id, "先别改")
    with drive.member():
        draft = proposals.make_proposal(drive.work, "files", "再试")
        [item] = draft.preview["candidates"]
        assert item["path"] == "steps/17-plot.py" and item["returned"] == [stored.id]
        out = node_api.proposal_create_payload(drive.work, {"kind": "files", "sentence": "再试"})
        assert out["preview"]["candidates"][0]["returned"] == [stored.id]


# -- a link's note in the timeline (12.13) ------------------------------------------------------------


def test_12_13_the_note_written_with_a_link_is_in_the_node_s_timeline(tmp_path, monkeypatch):
    home = tmp_path / "home"
    with _as(monkeypatch, home):
        people.write_me(*PI)
        root = _project(tmp_path / "proj")
        mappings_ingest.add_mapping(root, "figs/fig1.png", "attempt:map.md#16", "belongs_to",
                                    note="图一是这条尝试画的", date="2026-10-12")
        _scan(root)
        conn = _conn(root)
        try:
            page = nodepage.node(conn, root, "figs/fig1.png")
        finally:
            conn.close()
    [drawn] = [link for link in page["links"] if link["human"]]
    assert drawn["mapping"]["note"] == "图一是这条尝试画的" and drawn["mapping"]["date"] == "2026-10-12"
    assert drawn["mapping"]["by_label"] == PI[0]
    if NODE is not None:
        entries = _node_call("_timeline", {"kind": "file", "versions": []}, [], page["links"])
        assert [e["kind"] for e in entries] == ["link"]


# -- 成果 and 设为论文正文 (12.13) ------------------------------------------------------------------


def test_12_13_papers_toml_is_a_record_ledger_snapshotted_and_marked_drafts_feed_results(tmp_path, monkeypatch):
    home = tmp_path / "home"
    with _as(monkeypatch, home):
        people.write_me(*PI)
        root = _project(tmp_path / "proj")
        (root / "notes-log.md").write_text("# Log\n\n## v2\n\nWe changed 3 tables and 12 figures.\n")
        _scan(root)
        conn = _conn(root)
        try:
            before = nodepage.results(conn, root)
        finally:
            conn.close()
        assert before["claims"] == [] and before["papers"]["marked"] == []
        assert {d["file"] for d in before["drafts"]} >= {"paper.md", "notes-log.md"}
        done = papers.set_marked(root, "notes-log.md", True, via="cli")
        assert done["changed"] and done["drafts"] == ["notes-log.md"]
        assert not papers.set_marked(root, "notes-log.md", True, via="cli")["changed"]  # already so: nothing appended
        papers.set_marked(root, "notes-log.md", False, via="app")
        papers.set_marked(root, "paper.md", True, via="app")
        text = papers.papers_path(root).read_text(encoding="utf-8")
        entries = tomllib.loads(text)["paper"]
        assert [(e["act"], e["draft"], e["by"]) for e in entries] == [
            ("mark", "notes-log.md", PI[1]), ("unmark", "notes-log.md", PI[1]), ("mark", "paper.md", PI[1])]
        assert list((root / ".researchos" / "backups").rglob("papers.toml*"))  # founded: snapshotted
        with pytest.raises(papers.PaperRefused):
            papers.set_marked(root, "../outside.md", True, via="app")
        with pytest.raises(papers.PaperRefused):
            papers.set_marked(root, "nope.md", True, via="app")
        conn = _conn(root)
        try:
            after = nodepage.results(conn, root)
            page = nodepage.node(conn, root, "paper.md")
        finally:
            conn.close()
        assert [c["file"] for c in after["claims"]] == [d["file"] for d in after["drafts"] if d["marked"] and d["claims"]]
        assert page["paper"] == {"draft": "paper.md", "marked": True}
        assert "papers.toml" in share.RECORD_FILES and "papers.toml" in share.TRAVELLING


def test_12_13_a_damaged_papers_file_lists_no_draft_and_says_why(tmp_path, monkeypatch):
    with _as(monkeypatch, tmp_path / "home"):
        root = _project(tmp_path / "proj")
        papers.set_marked(root, "paper.md", True, via="cli")
        papers.papers_path(root).write_text("[[paper]\nbroken", encoding="utf-8")
        got = papers.marked(root)
        assert got["drafts"] == [] and got["message"]
        with pytest.raises(papers.PaperRefused) as refused:
            papers.set_marked(root, "paper.md", False, via="app")
        assert refused.value.code == "untrusted"


def test_12_13_a_member_s_mark_travels_with_sync_and_is_taken_at_publish(drive):
    with drive.member():
        assert cli.main(["ingest", str(drive.work)]) == 0  # the copy opened on this machine, as the app does
        papers.set_marked(drive.work, "paper.md", True, via="app")
        preview = share.sync_preview(drive.work)
        assert preview["sending"]["papers"] == 1
        share.sync(drive.work)
    with drive.pi():
        assert share.status(drive.official)["incoming"]["papers"] == 1
        share.publish(drive.official)
        entries = tomllib.loads(papers.papers_path(drive.official).read_text(encoding="utf-8"))["paper"]
        assert [(e["draft"], e["by"]) for e in entries] == [("paper.md", MEMBER[1])]
        assert papers.marked(drive.official)["drafts"] == ["paper.md"]


@needs_node
def test_12_13_results_disclosure_is_a_quiet_line_chevron_not_the_browser_triangle():
    node_js = (WEBAPP / "node.js").read_text(encoding="utf-8")
    app = (WEBAPP / "app.html").read_text(encoding="utf-8")
    assert 'setAttribute("class", "res-chevron")' in node_js and "设为论文正文" in node_js
    assert ".res-draft-summary::-webkit-details-marker { display: none; }" in app
    assert ".res-draft-summary::marker" in app and "list-style: none" in app


# -- two folders for a member (12.13) ------------------------------------------------------------------


def _probe_left(folder: Path) -> list[str]:
    return [p.name for p in folder.iterdir() if p.name.startswith(".researchos-probe")]


def test_12_13_two_folders_join_propose_approve_sync(tmp_path, monkeypatch):
    """The PI keeps one shared folder and records the member's area where
    it is; the member has the published folder and their own folder as two
    separate folders, joins from the published folder, proposes, syncs."""
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    drive = Drive(tmp_path, monkeypatch, join=False)
    elsewhere = tmp_path / "onedrive-wang" / "wang 的提议"
    elsewhere.mkdir(parents=True)
    with drive.pi():
        recorded = share.set_member_area(drive.official, MEMBER[1], elsewhere)
        assert recorded["area"] == str(elsewhere)
        assert exchange_mod.folders_for(drive.pid).area(MEMBER[1]) == elsewhere
    published = drive.shared / "已发布"
    with drive.member():
        preview = share.join_preview(published, drive.work, mine=elsewhere)
        assert preview["two_folders"] and preview["mine"] == str(elsewhere)
        assert _probe_left(elsewhere) == []
        joined = share.join(published, drive.work, mine=elsewhere)
        assert joined["mine"] == str(elsewhere) and _probe_left(elsewhere) == []
        state = share.read_state(drive.pid)
        assert state["published"] == str(published) and state["shared"] is None
        assert state["areas"] == {MEMBER[1]: str(elsewhere)}
        entry = registry.find(drive.pid)
        assert entry["published"] == str(published) and entry["areas"] == {MEMBER[1]: str(elsewhere)}
        status = share.status(drive.work)
        assert status["two_folders"] and status["mine"] == {"path": str(elsewhere), "reachable": True}
        # propose: written into the member's own folder, not under 提议/
        (drive.work / "steps" / "18-big.py").write_text(NEW_SCRIPT)
        _sparse(drive.work / "data" / "big.bin")
        (drive.work / "map.md").write_text(_map() + ROW_18)
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(
            drive.work, "attempt_new", "大面板", add=["data/big.bin"]))
        assert stored.directory.parent == elsewhere and (elsewhere / "blobs").is_dir()
        assert not (drive.shared / "提议" / MEMBER[1] / stored.id).exists()
        assert [p["id"] for p in proposals.listing(drive.work)["proposals"]] == [stored.id]
        assert cli.main(["ingest", str(drive.work)]) == 0
        papers.set_marked(drive.work, "paper.md", True, via="app")
    with drive.pi():
        inbox = proposals.listing(drive.official)
        assert [p["id"] for p in inbox["proposals"]] == [stored.id]
        assert share.inbox_marker(drive.official) == (f"{MEMBER[1]}/{stored.id}",)
        done = proposals.approve(drive.official, stored.id, note="同意")
        assert done["published"]["published"]
        assert (drive.official / "data" / "big.bin").stat().st_size == BIG
    with drive.member():
        assert officialness.view(drive.work).file_state("steps/18-big.py")[0] == "approved"
        synced = share.sync(drive.work)
        assert synced["seq"] == done["published"]["seq"] and synced["entries_sent"] == 1
        packets = [p for p in elsewhere.iterdir() if p.name.startswith("n-")]
        assert len(packets) == 1  # the mark went to the member's own folder
        mine = proposals.listing(drive.work)["proposals"]
        assert mine[0]["state"] == "approved"
    with drive.pi():
        share.publish(drive.official)
        assert papers.marked(drive.official)["drafts"] == ["paper.md"]


def test_12_13_two_folders_are_checked_before_use(drive, tmp_path):
    published = drive.shared / "已发布"
    mine = tmp_path / "wang-mine"
    mine.mkdir()
    empty = tmp_path / "not-published"
    empty.mkdir()
    with drive.member():
        with pytest.raises(share.ShareError) as refused:
            share.set_member_folders(drive.work, empty, mine)
        assert refused.value.code in ("nothing_published", "untrusted") and refused.value.message_zh
        with pytest.raises(share.ShareError) as refused:
            share.set_member_folders(drive.work, published, published / "snapshots")
        assert refused.value.code == "invalid"
        os.chmod(mine, stat.S_IRUSR | stat.S_IXUSR)
        try:
            with pytest.raises(share.ShareError) as refused:
                share.set_member_folders(drive.work, published, mine)
            assert refused.value.code == "not_writable"
        finally:
            os.chmod(mine, stat.S_IRWXU)
        assert _probe_left(mine) == []
        state_before = share.read_state(drive.pid)
        assert "published" not in state_before  # nothing changed by a refusal
        done = share.set_member_folders(drive.work, published, mine)
        assert done["published"] == str(published) and done["mine"] == str(mine) and _probe_left(mine) == []
        assert share.read_state(drive.pid)["areas"] == {MEMBER[1]: str(mine)}
        # the shared folder given whole is understood too: its 已发布 is used
        again = share.set_member_folders(drive.work, drive.shared, mine)
        assert again["published"] == str(published)


def test_12_13_two_folders_through_the_command_line_and_the_endpoint(drive, tmp_path, capsys):
    published = drive.shared / "已发布"
    mine = tmp_path / "wang-mine"
    mine.mkdir()
    with drive.member():
        assert cli.main(["share", "set", "--published", str(published), "--mine", str(mine), str(drive.work)]) == 0
        out = capsys.readouterr().out
        assert "verified" in out and "you can write into it" in out
        assert cli.main(["share", "show", str(drive.work)]) == 0
        assert str(mine) in capsys.readouterr().out
        body = node_api.share_set_payload(drive.work, drive.pid, {"published": str(published), "mine": str(mine)})
        assert body["ok"] and body["mine"] == str(mine)
        with pytest.raises(node_api.NodeApiRefused):
            node_api.share_set_payload(drive.work, drive.pid, {"published": "relative", "mine": str(mine)})
    with drive.pi():
        assert cli.main(["share", "set", "--area", f"{MEMBER[1]}={mine}", str(drive.official)]) == 0
        assert str(mine) in capsys.readouterr().out
        areas = share.status(drive.official)["areas"]
        assert areas == [{"member": MEMBER[1], "name": MEMBER[0], "area": str(mine), "set_apart": True,
                          "reachable": True}]


# -- installing for a collaborator (12.13) -----------------------------------------------------------


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "安装与加入团队.md"
#: Every label of Research OS's own screens the guide tells a reader to look
#: for or click; a renamed button must not leave the guide pointing at nothing.
GUIDE_APP_LABELS = (
    "添加项目…", "从公盘加入项目…", "我是谁", "保存", "我的公钥", "生成我的公钥", "复制", "请手动选中复制",
    "公盘和工作副本", "选择…", "公盘文件夹，或 PI 共享给你的「已发布」文件夹", "你自己的提议文件夹",
    "工作副本放在哪里", "查看", "PI 公钥指纹", "核对无误，加入", "公盘与同步", "同步", "已经是最新的官方版本",
    "开始协作", "建立成员名单", "设为公盘", "发布", "添加成员", "公钥文字", "公钥文件", "加入团队", "指纹：",
    "选择文件夹…", "加入并扫描", "提交提议", "新的尝试", "改判", "变量定义", "只有文件", "查看将要发送的内容",
    "按勾选的文件重新查看", "发送提议", "提议中", "已批准", "已退回", "已批准，待同步", "批准并加入", "退回并说明",
    "讨论", "两边都改过", "放在旁边的官方版本", "团队", "关闭",
)


def test_12_13_the_collaborator_guide_is_in_plain_words_and_names_only_what_the_app_shows():
    text = GUIDE.read_text(encoding="utf-8")
    assert not FORBIDDEN.search(text), FORBIDDEN.search(text)  # 12.11: no emoji, no arrows
    for command in ("xcode-select --install", "curl -LsSf https://astral.sh/uv/install.sh | sh",
                    "uv tool install git+https://github.com/LinyingL/research-os\n",
                    "uv tool install git+https://github.com/LinyingL/research-context-engine\n",
                    "researchos app\n", "uv tool upgrade researchos"):
        assert command in text, command
    screens = "".join((WEBAPP / name).read_text(encoding="utf-8") for name in ("app.html", "team.js", "node.js", "desk.js"))
    screens += "".join(officialness.LABELS.values())  # the engine names a member's own work
    missing = [label for label in GUIDE_APP_LABELS if label not in screens]
    assert missing == [] and all(label in text for label in GUIDE_APP_LABELS)
    # the command the guide installs, and the folder it says the app goes to
    assert "researchos" in tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert "~/Applications" in (cli.cmd_app.__doc__ or "")
    assert "docs/安装与加入团队.md" in (ROOT / "README.md").read_text(encoding="utf-8")

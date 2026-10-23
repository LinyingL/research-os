"""Research OS V8, the review round after all interface phases landed:
each test is the regression test of one confirmed finding (DESIGN.md 12.0,
12.3, 12.4, 12.6, 12.9, 12.11, 12.12, 8.8)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from researchos import cli, desk, nodepage, officialness, proposals, share, signing
from researchos.ingest import mappings as mi

from test_v8_phase2 import _call, _conn, _map, _project, _scan
from test_v8_phase3 import MEMBER, _serve
from test_v8_phase4 import Drive

WEBAPP = Path(__file__).parents[1] / "src" / "researchos" / "webapp"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Drive:
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    return Drive(tmp_path, monkeypatch)


def _links(root: Path) -> set[tuple[str, str, str]]:
    data = tomllib.loads((root / ".researchos" / "mappings.toml").read_text(encoding="utf-8"))
    return {(m["from"], m["to"], m["type"]) for m in data.get("mapping", [])}


def _removals(root: Path) -> list[dict]:
    path = root / ".researchos" / "mappings.toml"
    return tomllib.loads(path.read_text(encoding="utf-8")).get("removal", []) if path.exists() else []


PI_LINK = ("steps/17-plot.py", "figs/fig2.png", "generates")
MY_LINK = ("figs/fig1.png", "attempt:map.md#16", "belongs_to")


# -- a member's links travel (12.12): never a removal the member did not make --------------------------------


def test_12_12_links_waiting_for_the_pi_never_turn_the_pis_new_links_into_removals(drive):
    """The PI draws a link and publishes; the member, still on the older
    snapshot, draws one and syncs twice before the PI publishes again. The
    member's file waits for their link, and the second sync must not send
    the PI's link as the member's removal."""
    with drive.pi():
        mi.add_mapping(drive.official, *PI_LINK)
        share.publish(drive.official)
    with drive.member():
        assert cli.main(["ingest", str(drive.work)]) == 0
        mi.add_mapping(drive.work, *MY_LINK)
        first = share.sync(drive.work)
        assert first["entries_sent"] == 1 and first["notes_waiting"] == [".researchos/mappings.toml"]
        assert share.sync(drive.work)["entries_sent"] == 0
    with drive.pi():
        done = share.publish(drive.official)
        assert done["collected"]["links"] == {"added": 1, "removed": 0, "packets": 1}
        assert _links(drive.official) == {PI_LINK, MY_LINK} and _removals(drive.official) == []
    with drive.member():
        synced = share.sync(drive.work)
        assert ".researchos/mappings.toml" in synced["updated"] and not synced.get("notes_waiting")
        assert _links(drive.work) == {PI_LINK, MY_LINK}


def test_12_12_a_packet_delivered_after_a_publish_never_removes_the_pis_link(drive):
    """The same through OneDrive's delay: the member's packet reaches the PI
    only after the PI published a link of their own."""
    with drive.member():
        assert cli.main(["ingest", str(drive.work)]) == 0
        mi.add_mapping(drive.work, *MY_LINK)
        assert share.sync(drive.work)["entries_sent"] == 1
    area = drive.shared / "提议" / MEMBER[1]
    hold = drive.tmp / "hold"
    hold.mkdir()
    for packet in [p for p in area.iterdir() if p.name.startswith("n-")]:
        shutil.move(str(packet), str(hold / packet.name))
    with drive.pi():
        mi.add_mapping(drive.official, *PI_LINK)
        share.publish(drive.official)
    for packet in hold.iterdir():
        shutil.move(str(packet), str(area / packet.name))
    with drive.member():
        assert share.sync(drive.work)["notes_waiting"] == [".researchos/mappings.toml"]
        assert share.sync(drive.work)["entries_sent"] == 0
    with drive.pi():
        share.publish(drive.official)
        assert _links(drive.official) == {PI_LINK, MY_LINK} and _removals(drive.official) == []


def test_12_12_a_member_who_deletes_a_link_already_sent_sends_the_removal(drive):
    """Drawn, synced (sent), deleted before the PI published: the deletion
    travels too, the official file ends without the link, and the member's
    next sync does not put it back."""
    with drive.member():
        assert cli.main(["ingest", str(drive.work)]) == 0
        mi.add_mapping(drive.work, *MY_LINK)
        assert share.sync(drive.work)["entries_sent"] == 1
        mi.delete_mapping(drive.work, *MY_LINK)
        assert share.sync(drive.work)["entries_sent"] == 1
        # drawn again: travels again (the removal is what was last sent)
        mi.add_mapping(drive.work, *MY_LINK)
        assert share.sync(drive.work)["entries_sent"] == 1
        mi.delete_mapping(drive.work, *MY_LINK)
        assert share.sync(drive.work)["entries_sent"] == 1
    with drive.pi():
        done = share.publish(drive.official)
        assert done["collected"]["links"]["packets"] == 4
        assert MY_LINK not in _links(drive.official)
    with drive.member():
        share.sync(drive.work)
        assert MY_LINK not in _links(drive.work)


# -- an approved abandonment does not fork the member's table (12.12: by row) ---------------------------------


def test_12_12_the_row_an_approval_wrote_with_its_reason_is_the_members_own_row(drive):
    """The member abandons #16 and proposes it with a reason; the approval
    writes the row with 「（放弃理由：…）」 added. The member's next sync takes
    the official table: no .official copy, no "my change" left on #16."""
    with drive.member():
        (drive.work / "map.md").write_text(_map("☠️").replace("t=2.91", "t=0.20"))
        stored = proposals.write_proposal(
            drive.work, proposals.make_proposal(drive.work, "verdict_change", "放弃 16", reason="效应消失了"))
    with drive.pi():
        proposals.approve(drive.official, stored.id, note="同意")
    with drive.member():
        assert share.sync_preview(drive.work)["update"].count("map.md") == 1
        synced = share.sync(drive.work)
        assert "map.md" in synced["updated"] and synced["conflicts"] == []
        assert "放弃理由：效应消失了" in (drive.work / "map.md").read_text()
        assert not (drive.work / "map.official.md").exists()
        assert officialness.view(drive.work).node_state("attempt:map.md#16")[0] == "official"


def test_12_12_a_row_of_the_members_own_still_unsent_keeps_their_table(drive):
    """The same, but the member also changed a cell the approval did not
    carry: their table is theirs, the official one goes beside it."""
    with drive.member():
        (drive.work / "map.md").write_text(_map("☠️").replace("t=2.91", "t=0.20"))
        stored = proposals.write_proposal(
            drive.work, proposals.make_proposal(drive.work, "verdict_change", "放弃 16", reason="效应消失了"))
        (drive.work / "map.md").write_text(_map("☠️").replace("t=2.91", "t=0.20").replace("07-26", "07-27"))
    with drive.pi():
        proposals.approve(drive.official, stored.id, note="同意")
    with drive.member():
        synced = share.sync(drive.work)
        assert [c["path"] for c in synced["conflicts"]] == ["map.md"]


def test_12_12_row_as_approved_only_forgives_the_reason_suffix():
    row = {"date": "07-26", "description": "d", "variables": "v", "result": "t=0.2", "verdict": "☠️"}
    assert proposals.row_as_approved(row, dict(row))
    assert proposals.row_as_approved(row, {**row, "result": "t=0.2（放弃理由：无信号）"})
    assert not proposals.row_as_approved(row, {**row, "result": "t=0.3（放弃理由：无信号）"})
    assert not proposals.row_as_approved(row, {**row, "result": "t=0.2 改过"})
    assert not proposals.row_as_approved(row, {**row, "verdict": "✅"})
    assert proposals.row_as_approved({**row, "result": ""}, {**row, "result": "放弃理由：无信号"})
    assert not proposals.row_as_approved(row, None)


# -- every route the engine counts as abandoned is shown as abandoned (12.0, 12.9, 12.12) ---------------------

HEAD = 'file = "map.md"\nheading = "Attempt timeline"\nsteps_dir = "steps"\n'
COLS = ('[columns]\nid = "#"\ndate = "Date"\ndescription = "Path"\nvariables = "Variables"\n'
        'result = "Result"\nverdict = "Verdict"\n')


def _abandoned_and_groups(root: Path):
    """The desk's abandoned routes -- re-baselined for V10 (14.5): the
    abandoned cards of the route map -- and 研究路线's groups."""
    conn = _conn(root)
    try:
        got = desk.payload(conn, root)
        routes = nodepage.routes(conn, root)
    finally:
        conn.close()
    return ([a for a in got["attempts"] if a["abandoned"]],
            [(g["status"], g["label"], [i["number"] for i in g["items"]]) for g in routes["groups"]])


def test_12_12_a_declared_abandoned_marker_and_a_renamed_skull_are_abandoned_routes(tmp_path):
    root = _project(tmp_path / "p")
    (root / "map.md").write_text(_map("❌ 无效").replace("t=2.91", "t=0.1（放弃理由：无信号）"))
    (root / ".researchos" / "attempts.toml").write_text(HEAD + 'abandoned_verdicts = ["❌"]\n' + COLS)
    _scan(root)
    abandoned, groups = _abandoned_and_groups(root)
    assert [(a["number"], a["result"], a["reason"]) for a in abandoned] == [("16", "t=0.1", "无信号")]
    assert groups == [("abandoned", "已放弃", ["16"])]
    (root / "map.md").write_text(_map("☠️ 伪"))
    (root / ".researchos" / "attempts.toml").write_text(HEAD + COLS + '[verdict_labels]\n"☠️" = "放弃"\n')
    _scan(root)
    abandoned, groups = _abandoned_and_groups(root)
    assert [a["number"] for a in abandoned] == ["16"] and abandoned[0]["route_status"]["label"] == "放弃"
    assert groups == [("abandoned", "放弃", ["16"])]


# -- 提议中 only for exactly what a proposal carries (12.12) -----------------------------------------------------


def test_12_12_a_later_edit_of_a_proposed_rows_other_cell_is_mine_not_proposed(drive):
    with drive.member():
        (drive.work / "map.md").write_text(_map("✅"))
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(drive.work, "verdict_change", "16 成立"))
        assert officialness.view(drive.work).node_state("attempt:map.md#16") == ("proposed", stored.id)
        (drive.work / "map.md").write_text(_map("✅").replace("TopicShift volatility (16-17)", "TopicShift 波动 改过"))
        assert officialness.view(drive.work).node_state("attempt:map.md#16") == ("mine", None)


def test_8_8_a_conflict_reason_does_not_tell_the_pi_they_proposed():
    source = (Path(proposals.__file__)).read_text(encoding="utf-8")
    assert "你提议之后" not in source and source.count("在提议之后，正式版本里也改了") == 2


# -- 「当时的依据」 by fingerprint (12.4) ---------------------------------------------------------------------


def test_12_4_the_frozen_basis_opens_by_fingerprint_on_the_members_machine(drive):
    """The decision names the PI's store-local version number; the member's
    machine finds the content by its fingerprint (in a published snapshot
    before its own store has it, in its store after), and a version number
    that happens to exist there is never shown in its place."""
    with drive.member():
        (drive.work / "map.md").write_text(_map("☠️ 伪协整").replace("t=2.91", "t=0.41"))
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(
            drive.work, "verdict_change", "这条路线放弃", reason="残差不平稳"))
    with drive.pi():
        proposals.approve(drive.official, stored.id, note="同意")
        _scan(drive.official)
        conn = _conn(drive.official)
        try:
            [decision] = nodepage.evidence(conn, drive.official, "attempt:map.md#16")["decisions"]
        finally:
            conn.close()
    kept = {f["path"]: f for f in decision["files"]}["steps/16-build.py"]
    original = (drive.work / "steps" / "16-build.py").read_text()
    with drive.member():
        share.sync(drive.work)
        then = nodepage.version_content(drive.work, kept["node"], "999999", kept["sha256"])
        assert then["text"] == original and then["sha256"] == kept["sha256"]
        (drive.work / "steps" / "16-build.py").write_text("# the member changed it since\n")
        assert cli.main(["ingest", str(drive.work)]) == 0
        then = nodepage.version_content(drive.work, kept["node"], kept["version"], kept["sha256"])
        assert then["text"] == original
        with _serve(drive.work) as base:
            status, body = _call(f"{base}/api/history/version?node={kept['node']}&sha256={kept['sha256']}")
            assert status == 200 and body["text"] == original
            status, body = _call(f"{base}/api/history/version?node={kept['node']}&sha256={'0' * 64}")
            assert status == 404


# -- no English edge type reaches the screen (8.8) -------------------------------------------------------------


def test_8_8_every_edge_type_has_a_chinese_relation_word():
    import re

    from researchos import db

    assert set(db.EDGE_TYPES) <= set(nodepage.RELATION_WORDS)
    assert all(re.search(r"[一-鿿]", w) for w in nodepage.RELATION_WORDS.values())
    app = (WEBAPP / "app.html").read_text(encoding="utf-8")
    verbs = re.search(r"const LINK_VERBS = \{(.*?)\};", app, re.S).group(1)
    assert set(db.EDGE_TYPES) <= set(re.findall(r"(\w+):", verbs))
    node_js = (WEBAPP / "node.js").read_text(encoding="utf-8")
    assert "link.relation || link.type" not in node_js and 'LINK_VERBS[key.type] : key.type' not in app


def test_8_8_a_git_projects_judgments_read_in_chinese(tmp_path):
    import re

    from researchos import db

    from test_v8_phase2 import _git

    root = _project(tmp_path / "p")
    (root / ".researchos" / "attempts.toml").write_text(HEAD + COLS)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "first")
    _scan(root)
    conn = _conn(root)
    try:
        [commit] = db.get_nodes_by_type(conn, "commit")
        page = nodepage.node(conn, root, commit["id"])
    finally:
        conn.close()
    relations = {link["type"]: link["relation"] for link in page["links"]}
    assert relations.get("authored_by") == "作者是"
    assert all(re.search(r"[\u4e00-\u9fff]", link["relation"]) for link in page["links"])


# -- the scan chip names files only (8.8, 10.2) ------------------------------------------------------------------


def test_8_8_a_project_without_git_is_not_told_a_file_called_check_is_unreadable(tmp_path):
    from researchos import addproject, db
    from researchos.ingest import scan as scan_mod

    root = _project(tmp_path / "p")  # no git history, steps_dir configured
    report = addproject.rescan(root)
    conn = _conn(root)
    try:
        assert any(r["source"] == scan_mod.CHECK_SOURCE and r["status"] == scan_mod.UNREADABLE
                   for r in db.all_scan_sources(conn))
    finally:
        conn.close()
    assert report.ok and report.unreadable_sources == []


# -- people are named one way everywhere (8.8, 12.12) -----------------------------------------------------------


def test_8_8_a_timeline_names_the_member_and_the_pi_and_nobody_recorded_is_the_first_researcher(drive):
    from researchos import history

    from test_v8_phase3 import PI

    with drive.member():
        (drive.work / "map.md").write_text(_map("☠️").replace("t=2.91", "t=0.20"))
        stored = proposals.write_proposal(
            drive.work, proposals.make_proposal(drive.work, "verdict_change", "放弃 16", reason="残差不平稳"))
    with drive.pi():
        proposals.approve(drive.official, stored.id, note="同意")
        _scan(drive.official)
        newest = history.versions(drive.official, "attempt:map.md#16")["versions"][0]
        assert newest["who_label"] == MEMBER[0] and newest["approved_by_label"] == PI[0]
        table = history.versions(drive.official, "map.md")["versions"]
        assert table[0]["who_label"] == MEMBER[0] and table[0]["approved_by_label"] == PI[0]
        assert table[-1]["who_label"] == PI[0]  # 12.13: the PI's own scan (me.toml set) is named
    with drive.member():
        assert cli.main(["ingest", str(drive.work)]) == 0
        # 12.13: a version 加入/同步 installed is the publish's -- the PI and the snapshot.
        [joined] = history.versions(drive.work, "steps/16-build.py")["versions"]
        assert joined["who_label"] == PI[0] and joined["source"] == "publish" and joined["snapshot"] == 1
        (drive.work / "steps" / "16-build.py").write_text("print('mine')\n")
        assert cli.main(["ingest", str(drive.work)]) == 0
        assert history.versions(drive.work, "steps/16-build.py")["versions"][0]["who_label"] == "本机"
    node_js = (WEBAPP / "node.js").read_text(encoding="utf-8")
    assert "本机扫描时记下" not in node_js and node_js.count("approved_by_label || ") == 2


# -- a member's open page hears the PI's answers (12.3) -----------------------------------------------------------


def test_12_3_the_pis_return_and_publish_move_the_members_watch_marker(drive):
    with drive.member():
        (drive.work / "map.md").write_text(_map("☠️").replace("t=2.91", "t=0.20"))
        stored = proposals.write_proposal(
            drive.work, proposals.make_proposal(drive.work, "verdict_change", "放弃 16", reason="残差不平稳"))
        before = share.inbox_marker(drive.work)
        assert before is not None and any(m.startswith("s:") for m in before)
    with drive.pi():
        proposals.return_(drive.official, stored.id, "先补上 placebo")
    with drive.member():
        returned = share.inbox_marker(drive.work)
        assert returned != before and any(m.startswith("o:" + stored.id) for m in returned)
    with drive.pi():
        (drive.official / "paper.md").write_text((drive.official / "paper.md").read_text() + "\nMore.\n")
        assert share.publish(drive.official)["published"]
    with drive.member():
        assert share.inbox_marker(drive.work) != returned


# -- 发布 and 同步 say what they write (12.12, 8.8) -------------------------------------------------------------------


def test_12_12_the_publish_and_sync_previews_name_the_packets(drive):
    from researchos.records import notes as notes_mod

    with drive.pi():
        mi.add_mapping(drive.official, *PI_LINK)
        share.publish(drive.official)
    with drive.member():
        assert cli.main(["ingest", str(drive.work)]) == 0
        share.sync(drive.work)
        notes_mod.add_note(drive.work, "steps/16-build.py", "这里的残差要检验", via="cli")
        mi.add_mapping(drive.work, *MY_LINK)
        mi.delete_mapping(drive.work, *PI_LINK)
        preview = share.sync_preview(drive.work)
        assert preview["up_to_date"] and preview["area"] == f"提议/{MEMBER[1]}/"
        assert preview["sending"] == {"notes": 1, "judgements": 0, "papers": 0, "links_added": 1, "links_removed": 1}
        assert share.sync(drive.work)["entries_sent"] == 3
        assert share.sync_preview(drive.work)["sending"] == {"notes": 0, "judgements": 0, "papers": 0, "links_added": 0,
                                                             "links_removed": 0}
    with drive.pi():
        coming = share.status(drive.official)["incoming"]
        assert (coming["notes"], coming["links_added"], coming["links_removed"]) == (1, 1, 1)
        assert coming["members"] == [MEMBER[0]] and coming["removals"][0]["from"] == PI_LINK[0]
        assert coming["files"] == [".researchos/mappings.toml", ".researchos/notes.toml"]
        share.publish(drive.official)
        coming = share.status(drive.official)["incoming"]
        assert (coming["notes"], coming["links_added"], coming["links_removed"], coming["files"]) == (0, 0, 0, [])


@needs_node
def test_8_8_the_publish_and_sync_sentences_never_say_nothing_is_written_when_packets_wait():
    def team(name, *args):
        script = ("global.window = {}; global.document = {getElementById(){return null}};\n"
                  + (WEBAPP / "team.js").read_text(encoding="utf-8")
                  + f"\nprocess.stdout.write(JSON.stringify(window.ROTeam.{name}(...{json.dumps(list(args))})));\n")
        return json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout)

    last = {"seq": 4, "at": "2026-10-11T10:00:00"}
    quiet = team("_publishWrites", {"last_snapshot": last, "pending_publish": {"count": 0},
                                    "incoming": {"notes": 0, "judgements": 0, "links_added": 0, "links_removed": 0}})
    assert "不会写入新的版本" in quiet and "项目文件夹里不写入任何东西" in quiet and "只会再记一版" not in quiet
    busy = team("_publishWrites", {"last_snapshot": last, "pending_publish": {"count": 0},
                                   "incoming": {"notes": 1, "judgements": 0, "links_added": 1, "links_removed": 1,
                                                "members": ["王"], "files": [".researchos/notes.toml"]}})
    # 备注 is the word for notes everywhere (ruled 2026-10-13): re-baselined from 「1 条笔记」.
    assert "不写入任何东西" not in busy and "1 条备注" in busy and "删除 1 条" in busy and "王" in busy
    assert "第 5 版" in busy
    sync = team("_syncWrites", {"up_to_date": True, "area": "提议/wang@example.com/",
                                "sending": {"notes": 1, "judgements": 0, "links_added": 2, "links_removed": 1}})
    assert "1 条备注" in sync and "增加 2 条" in sync and "删除 1 条" in sync and "提议/wang@example.com/" in sync
    idle = team("_syncWrites", {"up_to_date": True,
                                "sending": {"notes": 0, "judgements": 0, "links_added": 0, "links_removed": 0}})
    assert "没有要发送" in idle


# -- the official copy put beside is shown with the difference (12.3) -------------------------------------------


def test_12_3_the_official_copy_beside_carries_the_difference(drive):
    with drive.pi():
        (drive.official / "steps" / "16-build.py").write_text("# PI tweak\n")
        share.publish(drive.official)
    with drive.member():
        (drive.work / "steps" / "16-build.py").write_text("# member tweak\n")
        [conflict] = share.sync(drive.work)["conflicts"]
        assert "-# member tweak" in conflict["diff"] and "+# PI tweak" in conflict["diff"]
        [beside] = share.status(drive.work)["joined"]["beside"]
        assert beside["copy"] == "steps/16-build.official.py" and beside["path"] == "steps/16-build.py"
        assert "-# member tweak" in beside["diff"] and "+# PI tweak" in beside["diff"]
    team_js = (WEBAPP / "team.js").read_text(encoding="utf-8")
    assert "diff: c.diff" in team_js and "differenceBlock(b, false)" in team_js


# -- a data file in the PI's review: fingerprint, rows and columns (12.3) -------------------------------------------


def test_12_3_the_review_shows_a_tables_rows_and_columns_and_its_fingerprint(drive):
    with drive.member():
        (drive.work / "data" / "out24.csv").write_text("a,b\n1,2\n3,4\n")
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(drive.work, "files", "新数据"))
    with drive.pi():
        with _serve(drive.official) as base:
            status, full = _call(f"{base}/api/proposals/{stored.id}")
        assert status == 200
        [f] = [x for x in full["changes"]["files"] if x["path"] == "data/out24.csv"]
        assert f["sha256"] and f["diff"] and f["table_words"] == "2 行，列 a、b"
    team_js = (WEBAPP / "team.js").read_text(encoding="utf-8")
    # V10 D3 (14.6): the table's words are one template through t().
    assert 't("表格：{words}", { words: f.table_words })' in team_js and 't("指纹 ")' in team_js


# -- the desk marks a member's own work, and says every abandoned route (12.0, 12.12) ------------------------------


def test_12_12_the_desk_marks_a_members_unsubmitted_and_proposed_changes(drive):
    from researchos.records import notes as notes_mod

    with drive.member():
        assert cli.main(["ingest", str(drive.work)]) == 0
        path = drive.work / "steps" / "17-plot.py"
        path.write_text(path.read_text() + "# mine\n")
        (drive.work / "map.md").write_text(_map("☠️ 方向测反").replace("t=2.91", "t=0.1"))
        proposals.write_proposal(drive.work, proposals.make_proposal(
            drive.work, "verdict_change", "放弃 16", reason="方向测反", only=[]))
        from researchos import addproject

        assert addproject.rescan(drive.work).ok
        with _serve(drive.work) as base:
            status, got = _call(f"{base}/api/desk")
            # Re-baselined for V10 (14.5): the desk's changes are its cards; a member's own
            # change to a file is marked where the file is shown -- the panel's 来龙去脉.
            status_node, page = _call(f"{base}/api/node?node=attempt%3Amap.md%2316")
        assert status == 200 and got["officialness_mode"]["mode"] == "working_copy"
        traced = {i["id"]: i for c in page["trace"]["columns"] for i in c["items"]}
        assert status_node == 200 and traced["script:steps/17-plot.py"]["officialness"]["label"] == "我的改动，尚未提交"
        # The route map's card (V10, 14.1): abandoned, with its result, marked as the member's proposal.
        card = next(a for a in got["attempts"] if a["number"] == "16")
        assert (card["abandoned"], card["result"], card["officialness"]["state"]) == (True, "t=0.1", "proposed")
        assert [a["number"] for a in got["attempts"] if a["abandoned"]] == ["16"]
    # Re-baselined for V10 (14.5): the desk is the route map. A member's own work is marked on its
    # card (routemap.js, from the payload's officialness) and in the column (ownMark); every
    # abandoned route is a quieter card, its reason kept for the panel.
    desk_js = (WEBAPP / "desk.js").read_text(encoding="utf-8")
    map_js = (WEBAPP / "routemap.js").read_text(encoding="utf-8")
    assert desk_js.count("ownMark(") >= 2 and 'if (own && own.state !== "official") add(own.label);' in map_js
    assert '(a.abandoned ? " abandoned" : "")' in map_js and "list.length + \" 条路线已经放弃" not in desk_js


# -- the page: wording run under node, and the served markup (12.3, 12.9, 12.11, 8.8) --------------------------------


def _js_call(module: str, glob: str, name: str, *args, prelude: str = "") -> object:
    script = ("global.window = {}; global.document = {getElementById(){return null}};\n" + prelude
              + (WEBAPP / module).read_text(encoding="utf-8")
              + f"\nprocess.stdout.write(JSON.stringify(window.{glob}.{name}(...{json.dumps(list(args))})));\n")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@needs_node
def test_12_12_a_members_note_link_and_judgment_say_they_travel():
    member = {"mode": "member", "me": {"me": {"id": "wang@example.com"}}}
    note = _js_call("node.js", "RONode", "_travelsWords", "note", member,
                    prelude="global.window.addEventListener = function(){};\n")
    assert "提议/wang@example.com/" in note and "不需要批准" in note and "下次同步" in note
    link = _js_call("node.js", "RONode", "_travelsWords", "link", member,
                    prelude="global.window.addEventListener = function(){};\n")
    assert "mappings.toml" in link
    pi = _js_call("node.js", "RONode", "_travelsWords", "note", {"mode": "pi"},
                  prelude="global.window.addEventListener = function(){};\n")
    assert pi == ""


@needs_node
def test_12_3_the_sync_sentence_counts_the_copies_put_beside_not_the_canvas():
    canvas_only = {"seq": 2, "updated": [], "conflicts": [], "entries_sent": 0,
                   "kept_yours": [{"path": ".researchos/canvas.json", "reason": "关系图上的摆放是每个人自己的，不随同步改变"}]}
    said = _js_call("team.js", "ROTeam", "_syncDone", canvas_only)
    assert "放在旁边" not in said and "1 个文件留着你的" in said
    conflict = {"seq": 2, "updated": ["a"], "kept_yours": [], "entries_sent": 1,
                "conflicts": [{"path": "steps/17-plot.py", "official_copy": "steps/17-plot.official.py"}]}
    said = _js_call("team.js", "ROTeam", "_syncDone", conflict)
    assert "1 个两边都改过的文件留着你的，正式的一份放在旁边" in said and "1 条" in said


def test_12_9_the_sidebar_and_views_follow_collaborative_acts():
    app = (WEBAPP / "app.html").read_text(encoding="utf-8")
    team = (WEBAPP / "team.js").read_text(encoding="utf-8")
    desk_js = (WEBAPP / "desk.js").read_text(encoding="utf-8")
    # 提议 in the sidebar always opens the list, read afresh
    assert 'if (target === "proposals") { ROTeam.showList(); return; }' in app
    assert "function showList()" in team and "view.proposal = null;" in team
    # the views showing the shared drive reload when gone to
    assert 'const SHARED_STATE_VIEWS = ["desk", "proposals", "members"];' in app
    # every act that changes what the views show marks them stale
    assert team.count("markViewsStale();") >= 4 and "markViewsStale()" in desk_js
    # the act's sentence is brought into view
    assert "revealFlash(host)" in team and "scrollIntoView" in team
    # the desk's 同步 looks first, then counts what the engine reports
    assert '"/api/share/sync-preview"' in desk_js and "ROTeam.syncFlash(res)" in desk_js
    assert "res.kept_yours.length : 0" not in desk_js


def test_12_11_typography_badges_and_icons():
    import re

    app = (WEBAPP / "app.html").read_text(encoding="utf-8")
    css = app[:app.index("</style>")]

    def rule(selector):
        return re.search(re.escape(selector) + r" \{([^}]*)\}", css).group(1)

    for selector in (".desk-title", ".node-sec-title", ".route-group-title.serif, .team-title", ".proposal-sentence",
                     ".panel-title.plain-title", ".var-block-title"):
        assert "--font-serif" not in rule(selector), selector
    for selector in (".page-title", ".brand-mark", ".node-name"):
        assert "--font-serif" in rule(selector), selector
    for selector in (".route-date", ".attempt-date", ".cv-type", ".cv-socket-label", ".cv-loose-title"):
        assert "--font-mono" not in rule(selector), selector
    assert "--clay" not in rule(".pm-mark")
    team = (WEBAPP / "team.js").read_text(encoding="utf-8")
    assert 'head.appendChild(label(g.label' not in team and 'mk("h2", "route-group-title", g.label)' in team
    assert '"已写入正式版本的改动"' in team and '"已写入正式版本的"' not in team


def test_8_8_no_node_or_canvas_on_screen():
    from test_v8_u2 import html_screen_text, js_strings

    app = (WEBAPP / "app.html").read_text(encoding="utf-8")
    found = list(html_screen_text(app))
    for block in re.findall(r"<script>(.*?)</script>", app, re.S):
        found += [s for _, s in js_strings(block)]
    for name in ("desk.js", "node.js", "team.js", "canvas.js"):
        found += [s for _, s in js_strings((WEBAPP / name).read_text(encoding="utf-8"))]
    assert [s for s in found if "节点" in s or "画布" in s] == []
    assert "本节点" not in Path(nodepage.__file__).read_text(encoding="utf-8")

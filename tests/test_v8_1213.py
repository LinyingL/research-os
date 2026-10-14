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
        out = desk.payload(conn, root)
    finally:
        conn.close()
    # Re-baselined for V10 (14.5): the desk words no change itself any more (a card's changed_at);
    # nothing it sends carries an arrow.
    assert not ARROWS.search(json.dumps(out, ensure_ascii=False))
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
        # The PI's own scan (me.toml present) is named; a pre-V8 entry without `by` is 「最初的研究者」.
        assert history.versions(drive.official, "steps/17-plot.py")["versions"][-1]["who_label"] == PI[0]
        with history.Store.open(drive.pid) as store:
            store.add("file:steps/old.py", sha256="0" * 64, size=1, kind="text")
            [old] = [history._store_payload(v) for v in store.versions("file:steps/old.py")]
        assert history.name_people(drive.official, [old])[0]["who_label"] == "最初的研究者"


def test_12_13_an_attempt_row_sync_installed_is_the_publish_s_not_this_machine_s(drive):
    """Verifier round 3: a row the PI changed and published reads as the
    PI and the snapshot in the member's copy, after the member's scan."""
    with drive.pi():
        (drive.official / "map.md").write_text(_map("✅").replace("t=2.91", "t=3.05"))
        published = share.publish(drive.official, note="16 的结果更新")
    with drive.member():
        share.sync(drive.work)
        assert cli.main(["ingest", str(drive.work)]) == 0
        [newest, *_older] = history.versions(drive.work, "attempt:map.md#16")["versions"]
        assert (newest["source"], newest["who"], newest["snapshot"]) == ("publish", PI[1], published["seq"])
        assert newest["who_label"] == PI[0] and newest["result"] == "t=3.05" and newest["note"] == "16 的结果更新"
        assert all(v["who_label"] != "本机" for v in history.versions(drive.work, "attempt:map.md#16")["versions"])


def test_12_13_the_pi_s_own_scan_is_named_not_the_first_researcher(drive):
    """Verifier round 3: on the PI's machine (me.toml set) a V8 scan names
    the PI, in the history; re-baselined for V10 (14.5), the desk marks the
    attempt holding the file changed by that very version, and the panel's
    timeline names the PI."""
    from researchos import desk

    with drive.pi():
        (drive.official / "data" / "panel.csv").write_text(_csv(97))
        (drive.official / "steps" / "16-build.py").write_text("print('pi')\n")
        _scan(drive.official)
        newest = history.versions(drive.official, "data/panel.csv")["versions"][0]
        assert (newest["who"], newest["who_label"]) == (PI[1], PI[0])
        script = history.versions(drive.official, "steps/16-build.py", "en")["versions"][0]
        assert (script["who"], script["who_label"]) == (PI[1], PI[0])
        conn = _conn(drive.official)
        try:
            card = next(a for a in desk.payload(conn, drive.official)["attempts"] if a["number"] == "16")
        finally:
            conn.close()
        assert desk._parse(card["changed_at"]) == desk._parse(script["at"])


def test_12_13_a_deleted_official_copy_leaves_the_list_without_a_new_snapshot(drive):
    """Verifier round 3 (the guide's conflict section): once the member
    deletes the `.official` copy, 「放在旁边的官方版本」 no longer lists it --
    at once in the status, and an up-to-date 同步 forgets it."""
    with drive.pi():
        (drive.official / "steps" / "16-build.py").write_text("# PI tweak\n")
        share.publish(drive.official)
    with drive.member():
        (drive.work / "steps" / "16-build.py").write_text("# member tweak\n")
        share.sync(drive.work)
        assert share.status(drive.work)["joined"]["official_copies"] == ["steps/16-build.official.py"]
        (drive.work / "steps" / "16-build.official.py").unlink()
        joined = share.status(drive.work)["joined"]
        assert joined["official_copies"] == [] and joined["beside"] == []
        assert share.sync(drive.work).get("up_to_date") is True
        assert share.official_copies(drive.pid) == set()


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


@needs_node
@pytest.mark.parametrize("tz", ["Europe/Berlin", "America/Los_Angeles", "Asia/Shanghai"])
def test_12_13_a_link_s_day_is_said_as_a_day_and_sorts_by_time_not_by_string(tz):
    """Verifier round 3: mappings.toml keeps a local day with no time; the
    timeline says 「今天」, not a made-up 02:00 (or yesterday west of UTC),
    and a link drawn today is not listed below this morning's versions."""
    import datetime as dt

    morning = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    history_body = {"kind": "file", "versions": [{"at": morning}, {"at": "2020-01-01T00:00:00+00:00"}]}
    script = (
        "global.window = {addEventListener(){}}; global.document = {getElementById(){return null}};\n"
        "window.RODesk = {_whenWords(){ return 'TIME'; }};\n"
        + (WEBAPP / "node.js").read_text(encoding="utf-8")
        + "\nconst day = (d) => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-'"
        + " + String(d.getDate()).padStart(2, '0');"
        + "\nconst today = day(new Date()), yesterday = day(new Date(Date.now() - 86400000));"
        + "\nconst links = [{human: true, mapping: {date: today, note: '补上来源'}}];"
        + f"\nconst t = window.RONode._timeline({json.dumps(history_body)}, [], links);"
        + "\nprocess.stdout.write(JSON.stringify([t.map((e) => e.kind), window.RONode._when(today),"
        + " window.RONode._when(yesterday)]));\n"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True, env={**os.environ, "TZ": tz})
    kinds, said_today, said_yesterday = json.loads(out.stdout)
    assert kinds == ["link", "version", "version"]
    assert (said_today, said_yesterday) == ("今天", "昨天")


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


def test_12_13_only_the_fingerprint_breaks_anywhere_in_the_team_table():
    """Verifier round 3: at 900 px the long fingerprint squeezed 「成员」 to
    one character a line; names and roles stay whole, e-mails break only
    when they must, and only the fingerprint breaks anywhere."""
    page = (WEBAPP / "app.html").read_text(encoding="utf-8")
    rules = {m.group(1).strip(): m.group(2) for m in re.finditer(r"^(\.team-table[^{]*)\{([^}]*)\}", page, re.M)}
    assert "break-all" not in rules[".team-table td"]
    assert "nowrap" in rules[".team-table td.team-name, .team-table td.team-role"]
    assert "break-all" in rules[".team-table td.team-fp"] and "break-all" not in rules[".team-table td.team-email"]
    team_js = (WEBAPP / "team.js").read_text(encoding="utf-8")
    assert all(f'"{cls}' in team_js or f' {cls}"' in team_js for cls in ("team-name", "team-role", "team-email", "team-fp"))


@needs_node
@pytest.mark.parametrize("features, sent", [
    (["choose-folder", "choose-folder-purpose", "choose-folder-share", "choose-folder-mine"], "mine"),
    (["choose-folder", "choose-folder-purpose", "choose-folder-share"], "shared"),  # a shell built before
])
def test_12_13_the_member_s_own_folder_is_chosen_with_its_own_panel_message(features, sent):
    """Verifier round 3: 「你自己的提议文件夹」 asks the shell for purpose
    "mine", whose panel says so -- not the shared-folder prompt; a shell
    that does not name the feature still gets a panel it knows."""
    team_js = (WEBAPP / "team.js").read_text(encoding="utf-8")
    assert team_js.count('"mine", "') == 2 and '提议文件夹（PI 共享给你，可以编辑）", "shared"' not in team_js
    shell = (WEBAPP / "shell" / "RCEShell.swift").read_text(encoding="utf-8")
    helper = shell[shell.index("func folderPanelMessage"):]
    helper = helper[: helper.index("\n}\n")]
    assert 'case "mine": return "选择你自己的提议文件夹（PI 共享给你、你可以编辑的那个）"' in helper
    page = (WEBAPP / "app.html").read_text(encoding="utf-8")
    code = page[page.index("function shellCan(feature)"):]
    code = code[: code.index("\n}\n", code.index("function chooseFolder(purpose)")) + 3]
    script = (
        "const sent = []; global.folderRequests = new Map();\n"
        f"global.window = {{RCEShellFeatures: {json.dumps(features)}, "
        "webkit: {messageHandlers: {rce: {postMessage(m) { sent.push(m); }}}}};\n"
        + code + "\nchooseFolder('mine'); process.stdout.write(JSON.stringify(sent[0].purpose));\n"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == sent


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
                    "researchos app\n", "uv tool upgrade researchos", "uv tool uninstall rce\n"):
        assert command in text, command
    # Verifier round 3: no fallback to the old address (it installs the V7 `rce` package, which has no
    # `researchos`); a failed install is told by the words uv really prints, and the reader stops.
    assert "research-context-engine" not in text
    for words in ("Installed 2 executables: rce, researchos", "Git operation failed", "could not read Username",
                  "Installed 1 executable: rce"):
        assert f"`{words}`" in text, words
    screens = "".join((WEBAPP / name).read_text(encoding="utf-8") for name in ("app.html", "team.js", "node.js", "desk.js"))
    screens += "".join(officialness.LABELS.values())  # the engine names a member's own work
    missing = [label for label in GUIDE_APP_LABELS if label not in screens]
    assert missing == [] and all(label in text for label in GUIDE_APP_LABELS)
    # the command the guide installs, and the folder it says the app goes to
    assert "researchos" in tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert "~/Applications" in (cli.cmd_app.__doc__ or "")
    assert "docs/安装与加入团队.md" in (ROOT / "README.md").read_text(encoding="utf-8")


# -- the English install guide (13.7) ----------------------------------------------------------------

GUIDE_EN = ROOT / "docs" / "install-and-join.en.md"
#: The app labels the Chinese guide names that the first English increment shows in English (the
#: welcome and the project menu; what a sync did, which 成员 shares with the desk); the English guide
#: names them in English.
GUIDE_LABELS_NOW_ENGLISH = ("添加项目…", "从公盘加入项目…", "已经是最新的官方版本", "已批准，待同步",
                            # V10 D3 (14.6): a proposal as the PI decides it and a member's draft.
                            "按勾选的文件重新查看", "发送提议", "提议中", "已批准", "已退回", "批准并加入", "退回并说明",
                            "讨论", "两边都改过", "关联文件", "补上来源")
#: Every Chinese label the English guide quotes from a screen still in Chinese, each written
#: **label** (meaning), so that a reader who does not read Chinese can match it on screen.
GUIDE_EN_CHINESE_LABELS = (
    "记录", "停止服务", "1. 我是谁", "我是谁", "名字", "邮箱", "保存", "2. 我的公钥", "我的公钥", "生成我的公钥",
    "复制", "请手动选中复制", "关闭", "3. 公盘和工作副本", "选择…", "公盘文件夹，或 PI 共享给你的「已发布」文件夹",
    "你自己的提议文件夹（PI 单独共享给你时才选；也可以以后在「成员」里选）", "工作副本放在哪里（一个新的或空的文件夹）", "查看", "PI 公钥指纹", "核对无误，加入", "公盘与同步", "同步",
    "团队", "选择文件夹…", "加入并扫描", "开始协作", "建立成员名单", "公盘", "设为公盘", "发布",
    "添加成员", "公钥文字", "公钥文件", "加入团队", "指纹：", "提交提议", "新的尝试", "改判", "变量定义", "只有文件",
    "查看将要发送的内容", "放在旁边的官方版本",
)
#: The only other Chinese the English guide may show: the line `researchos app` prints, the two
#: fixed folder names on the shared drive (13.1) and the switch's label on an English screen (13.3).
GUIDE_EN_OTHER_CHINESE = ("双击 Research OS.app 即可打开研究地图", "已发布", "提议", "中文")
CJK = re.compile(r"[⺀-鿿豈-﫿＀-￯]")
CODE_BLOCK = re.compile(r"^```(\w*)\n(.*?)^```$", re.M | re.S)


def test_13_7_the_english_guide_takes_the_same_steps_and_explains_every_chinese_label():
    text = GUIDE_EN.read_text(encoding="utf-8")
    assert not FORBIDDEN.search(text), FORBIDDEN.search(text)  # 12.11: no emoji, no arrows
    # The same commands as the Chinese guide, verbatim and in the same order, one per bash block.
    zh_commands = [line for _, body in CODE_BLOCK.findall(GUIDE.read_text(encoding="utf-8"))
                   for line in body.splitlines() if line.strip()]
    blocks = CODE_BLOCK.findall(text)
    assert all(lang == "bash" and body.count("\n") == 1 for lang, body in blocks), blocks
    assert [body.strip() for _, body in blocks] == zh_commands
    assert "research-context-engine" not in text
    for words in ("Installed 2 executables: rce, researchos", "Git operation failed", "could not read Username",
                  "Installed 1 executable: rce", "everything's installed!", "already installed", "is not on your PATH"):
        assert f"`{words}`" in text, words
    # How to switch the app to English comes before the first step.
    assert text.index("**English**") < text.index("## Part 1")
    # Every app label the Chinese guide names is named here too, in Chinese where the screen still is.
    assert [label for label in GUIDE_APP_LABELS if label not in GUIDE_LABELS_NOW_ENGLISH and label not in text] == []
    assert not any(label in text for label in GUIDE_LABELS_NOW_ENGLISH)
    # Each Chinese label is one the app shows, and comes with its meaning.
    screens = "".join((WEBAPP / name).read_text(encoding="utf-8") for name in ("app.html", "team.js", "node.js", "desk.js"))
    screens += "".join(officialness.LABELS.values())
    assert [label for label in GUIDE_EN_CHINESE_LABELS if label not in screens] == []
    assert [label for label in GUIDE_EN_CHINESE_LABELS if f"**{label}** (" not in text] == []
    assert GUIDE_EN_OTHER_CHINESE[0] in (ROOT / "src" / "researchos" / "cli.py").read_text(encoding="utf-8")
    rest = text
    for label in sorted(GUIDE_EN_CHINESE_LABELS, key=len, reverse=True):
        rest = rest.replace(f"**{label}**", "")
    for words in GUIDE_EN_OTHER_CHINESE:
        rest = rest.replace(words, "")
    assert CJK.findall(rest) == []
    # The README links it beside the Chinese guide, and DESIGN.md 12.13 names it.
    assert "docs/install-and-join.en.md" in (ROOT / "README.md").read_text(encoding="utf-8")
    design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    assert "docs/install-and-join.en.md" in design[design.index("### 12.13"):design.index("## Section 13")]

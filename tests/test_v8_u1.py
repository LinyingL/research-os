"""Research OS V8 phase U1 (DESIGN.md 12.11, 12.12): the engine follow-ups
of the rulings -- the attempt table conflicting by row, a member's area made
by `team add`, hand-drawn links travelling as packets, a damaged blob
repaired by the PI, the officialness of a member's own work in every view,
a member not yet in the team, the verdict as a word, and the history
store's size. Every test names its scenario."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from researchos import cli, db, history, inventory, names, officialness, people, proposals, share, signing
from researchos import exchange as exchange_mod
from researchos.ingest import attempts as attempts_ingest
from researchos.ingest import mappings as mappings_ingest
from researchos.records.identity import read_identity
from researchos.records.situation import index_db_path

from test_v8_phase2 import ATTEMPTS_TOML, _call, _map, _project, _scan
from test_v8_phase3 import MEMBER, PI, TEAM, ROW_18, Team, _as, _serve
from test_v8_phase4 import NEW_SCRIPT, Drive, Witness

ZHAO = ("赵", "zhao@example.com")
ROW_15 = "| 15 | 07-20 | PI's other row (15) | X->Y | t=0.5 | ☠️ 伪 |\n"
SENTENCE = "你还不是这个项目的成员：请 PI 把你加入团队，然后同步"

needs_ssh = pytest.mark.skipif(signing.tool() is None, reason="ssh-keygen is not installed here")


@pytest.fixture
def team(tmp_path, monkeypatch) -> Team:
    return Team(tmp_path, monkeypatch)


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Drive:
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    return Drive(tmp_path, monkeypatch)


def _row16(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("| 16 |"))


# -- 1. the attempt table conflicts by row (12.12 Approvals) ---------------------------------------------------


def test_12_12_the_pi_editing_other_rows_does_not_block_a_verdict_change(team):
    """The member changes row 16's verdict; meanwhile the PI added row 15
    and then edited it. Only row 16 can block the proposal: it is
    approvable, and approving keeps the PI's row 15 as the PI wrote it."""
    with team.member():
        (team.work / "map.md").write_text(_map("✅"))
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "verdict_change", "16 成立"))
    with team.pi():
        (team.official / "map.md").write_text(_map() + ROW_15)
        (team.official / "map.md").write_text(_map() + ROW_15.replace("t=0.5", "t=0.6"))
        changes = proposals.what_changes(stored, team.official)
        assert changes["approvable"] and changes["attempt"]["table_moved"] and not changes["conflicts"]
        proposals.approve(team.official, stored.id)
        text = (team.official / "map.md").read_text()
        assert "✅" in _row16(text) and "| 15 | 07-20 | PI's other row (15) | X->Y | t=0.6 |" in text


def test_12_12_the_pi_editing_the_same_row_blocks_it_and_nothing_is_applied(team):
    """Both sides changed row 16 -- the PI only its description -- so the
    member's verdict change conflicts on that row; nothing is applied."""
    with team.member():
        (team.work / "map.md").write_text(_map("✅"))
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "verdict_change", "16 成立"))
    with team.pi():
        _scan(team.official)  # the history store keeps the base table's text
        edited = _map().replace("TopicShift volatility (16-17)", "TopicShift 波动（16-17）")
        (team.official / "map.md").write_text(edited)
        changes = proposals.what_changes(stored, team.official)
        assert not changes["approvable"]
        assert changes["conflicts"] == [{"path": "map.md", "reason": "第 16 行在你提议之后，正式版本里也改了"}]
        with pytest.raises(proposals.ProposalRefused):
            proposals.approve(team.official, stored.id)
        assert (team.official / "map.md").read_text() == edited


def test_12_12_a_new_row_conflicts_only_when_its_number_is_taken(team):
    """A new row 18 is blocked by the PI's own row 18, not by the PI's row 15."""
    with team.member():
        (team.work / "map.md").write_text(_map() + ROW_18)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "attempt_new", "18"))
    with team.pi():
        (team.official / "map.md").write_text(_map() + ROW_15)
        assert proposals.what_changes(stored, team.official)["approvable"]
        (team.official / "map.md").write_text(_map() + ROW_15 + "| 18 | 08-03 | PI's 18 | A->B | — | 🕒 |\n")
        changes = proposals.what_changes(stored, team.official)
        assert changes["conflicts"] == [{"path": "map.md", "reason": "正式版本的尝试表里已经有第 18 行"}]


def test_12_12_two_members_proposing_the_same_new_number(tmp_path, monkeypatch):
    """Two members each propose a row 18. The first approved takes the
    number; the second then conflicts on it and cannot be approved -- the
    table holds one row 18, the first member's."""
    team = Team(tmp_path, monkeypatch)
    zhao_home, zhao_work = tmp_path / "home-zhao", tmp_path / "work-zhao"
    with team.pi():
        with open(team.official / ".researchos" / "team.toml", "a", encoding="utf-8") as handle:
            handle.write(f'\n[[member]]\nid = "{ZHAO[1]}"\nname = "{ZHAO[0]}"\nrole = "member"\n')
    with _as(monkeypatch, zhao_home):
        people.write_me(*ZHAO)
        proposals.start_working_copy(team.official, zhao_work, exchange=team.exchange)
        (zhao_work / "map.md").write_text(_map() + "| 18 | 08-04 | Zhao's 18 | C->D | t=3.0 | 🕒 |\n")
        second = proposals.write_proposal(zhao_work, proposals.make_proposal(zhao_work, "attempt_new", "赵的 18"))
    with team.member():
        (team.work / "map.md").write_text(_map() + ROW_18)
        first = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "attempt_new", "王的 18"))
    with team.pi():
        assert proposals.what_changes(second, team.official)["approvable"]  # before either is approved
        proposals.approve(team.official, first.id)
        changes = proposals.what_changes(second, team.official)
        assert not changes["approvable"] and changes["conflicts"][0]["reason"] == "正式版本的尝试表里已经有第 18 行"
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.approve(team.official, second.id)
        assert refused.value.code == "not_approvable"
        rows = [line for line in (team.official / "map.md").read_text().splitlines() if line.startswith("| 18 |")]
        assert rows == [ROW_18.rstrip("\n")]


@needs_ssh
def test_12_12_the_base_row_is_found_in_a_snapshot_when_the_history_store_is_gone(drive):
    """The base table is found by its fingerprint in this machine's history
    store, else in a published snapshot: with the store deleted, the PI's
    edit of row 16's description still conflicts with the member's change."""
    with drive.member():
        (drive.work / "map.md").write_text(_map("✅"))
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(drive.work, "verdict_change", "16 成立"))
    with drive.pi():
        shutil.rmtree(history.history_dir(drive.pid), ignore_errors=True)
        (drive.official / "map.md").write_text(_map().replace("TopicShift volatility", "TopicShift 波动"))
        changes = proposals.what_changes(stored, drive.official)
        assert not changes["approvable"] and changes["conflicts"][0]["reason"] == "第 16 行在你提议之后，正式版本里也改了"


# -- 2. team add makes the member's area (12.12 The shared drive) ---------------------------------------------


@needs_ssh
def test_12_12_team_add_makes_the_members_area_once_and_tells_the_pi(drive, tmp_path, capsys):
    """The member added before the shared folder was set got their area at
    the first publish, with the sentence for the PI. A member added now gets
    theirs at once (through the CLI, which prints the sentence); an area
    already there is never touched, whatever it holds."""
    created = drive.first["areas"]
    assert [(a["member"], a["state"]) for a in created] == [(MEMBER[1], "created")]
    assert "OneDrive" in created[0]["sentence"] and "编辑权限" in created[0]["sentence"]
    assert "edit rights" in created[0]["sentence_en"]
    area = drive.shared / "提议" / MEMBER[1]
    assert area.is_dir()
    with _as(drive.mp, tmp_path / "home-zhao"):
        key = share.my_key()["key"]
    key_file = tmp_path / "zhao.pub"
    key_file.write_text(key)
    with drive.pi():
        assert cli.main(["team", "add", ZHAO[1], "--name", ZHAO[0], "--key", str(key_file), str(drive.official)]) == 0
        out = capsys.readouterr().out
        assert "edit rights" in out and "OneDrive 里把这个文件夹的编辑权限给赵" in out
        assert (drive.shared / "提议" / ZHAO[1]).is_dir() and not any((drive.shared / "提议" / ZHAO[1]).iterdir())
        # An area already there, holding something: never replaced, never written into.
        held = drive.shared / "提议" / "qian@example.com"
        held.mkdir()
        (held / "keep.txt").write_text("mine")
        before = (held.stat().st_ino, (held / "keep.txt").stat().st_mtime_ns)
        done = share.team_add(drive.official, "qian@example.com", "钱", key)
        assert done["area"]["state"] == "exists" and "公盘上已经有" in done["sentence"]
        assert (held.stat().st_ino, (held / "keep.txt").stat().st_mtime_ns) == before
        assert sorted(p.name for p in (drive.shared / "提议").iterdir()) == sorted([MEMBER[1], ZHAO[1], "qian@example.com"])
        assert not [p for p in (drive.shared / "提议").iterdir() if ".tmp-" in p.name]
    drive.witness.look()


@needs_ssh
def test_12_12_team_add_over_http_is_the_pis_and_origin_checked(drive, tmp_path):
    """POST /api/team/add {id, name, key}: the key's text, never a path; a
    foreign origin is refused before anything is written."""
    with _as(drive.mp, tmp_path / "home-zhao"):
        key = share.my_key()["key"]
    with drive.pi(), _serve(drive.official) as base:
        team_before = people.team_path(drive.official).read_bytes()
        status, body = _call(f"{base}/api/team/add", "POST", {"id": ZHAO[1], "name": ZHAO[0], "key": key},
                             headers={"Origin": "http://evil.example"})
        assert status == 403 and people.team_path(drive.official).read_bytes() == team_before
        status, body = _call(f"{base}/api/team/add", "POST", {"id": ZHAO[1], "name": ZHAO[0], "key": key})
        assert status == 200 and body["area"]["state"] == "created" and "编辑权限给赵" in body["sentence"]
        assert (drive.shared / "提议" / ZHAO[1]).is_dir()
        status, body = _call(f"{base}/api/team/add", "POST", {"id": ZHAO[1], "name": ZHAO[0], "key": key})
        assert status == 409 and body["state"] == "member_exists"
    with drive.member(), _serve(drive.work) as base:
        status, body = _call(f"{base}/api/team/add", "POST", {"id": "sun@example.com", "name": "孙", "key": key})
        assert status in (403, 409) and not (drive.shared / "提议" / "sun@example.com").exists()


# -- 3. hand-drawn links travel as packets (12.12 The shared drive) --------------------------------------------


@needs_ssh
def test_12_12_a_members_links_travel_once_with_by_and_recovered_from(drive):
    """「关联文件」 and 「补上来源」 drawn by the member, and the removal of a
    link the PI drew, reach the official mappings.toml at the next publish:
    `by`, `recovered_from`, an explicit removal entry, and the packet marked
    received -- once: publishing again changes nothing, and a link the PI
    then deletes is not brought back. The member's file is then replaced by
    the official one (never forked beside it); canvas arrangements do not
    travel."""
    with drive.pi():
        mappings_ingest.add_mapping(drive.official, "steps/17-plot.py", "figs/fig2.png", "generates")
        (drive.official / ".researchos" / "canvas.json").write_text('{"format": 1, "pi": true}\n')
        assert share.publish(drive.official)["published"]
    with drive.member():
        assert share.sync(drive.work)["seq"] == 2
        assert cli.main(["ingest", str(drive.work)]) == 0
        mappings_ingest.delete_mapping(drive.work, "steps/17-plot.py", "figs/fig2.png", "generates")
        mappings_ingest.add_mapping(drive.work, "figs/fig1.png", "attempt:map.md#16", "belongs_to")
        mappings_ingest.add_mapping(drive.work, "steps/17-plot.py", "figs/fig1.png", "generates")
        (drive.work / ".researchos" / "canvas.json").write_text('{"format": 1, "member": true}\n')
        synced = share.sync(drive.work)
        assert synced["entries_sent"] == 3 and synced["up_to_date"]
        assert share.sync(drive.work)["entries_sent"] == 0  # sent once
    drive.witness.look()
    with drive.pi():
        done = share.publish(drive.official)
        assert done["collected"]["links"] == {"added": 2, "removed": 1, "packets": 1} and done["published"]
        text = (drive.official / ".researchos" / "mappings.toml").read_text()
        data = __import__("tomllib").loads(text)
        added = {(m["from"], m["to"], m["type"]): m for m in data["mapping"]}
        assert set(added) == {("figs/fig1.png", "attempt:map.md#16", "belongs_to"),
                              ("steps/17-plot.py", "figs/fig1.png", "generates")}
        assert all(m["by"] == MEMBER[1] and m["recovered_from"].startswith("n-") for m in added.values())
        assert [(r["from"], r["by"]) for r in data["removal"]] == [("steps/17-plot.py", MEMBER[1])]
        assert [r["by"] for r in data["received"]] == [MEMBER[1]]
        conn = db.connect(index_db_path(drive.pid))
        try:
            edges = {(e["src"], e["dst"], e["type"]) for e in db.query_edges(conn) if e["extractor"] == "mapping"}
        finally:
            conn.close()
        assert ("figure:figs/fig1.png", "attempt:map.md#16", "belongs_to") in edges
        assert ("script:steps/17-plot.py", "figure:figs/fig2.png", "generates") not in edges
        again = share.publish(drive.official)
        assert again["collected"]["links"]["packets"] == 0 and not again["published"]
        mappings_ingest.delete_mapping(drive.official, "steps/17-plot.py", "figs/fig1.png", "generates")
        (drive.official / ".researchos" / "canvas.json").write_text('{"format": 1, "pi": 2}\n')
        assert share.publish(drive.official)["collected"]["links"]["packets"] == 0
        assert "figs/fig1.png\"\ntype = \"generates\"" not in (drive.official / ".researchos" / "mappings.toml").read_text()
    with drive.member():
        synced = share.sync(drive.work)
        assert ".researchos/mappings.toml" in synced["updated"] and not synced["conflicts"]
        assert any(k["path"] == ".researchos/canvas.json" for k in synced["kept_yours"])
        assert json.loads((drive.work / ".researchos" / "canvas.json").read_text()) == {"format": 1, "member": True}
        assert not list((drive.work / ".researchos").glob("*.official*"))
        assert (drive.work / ".researchos" / "mappings.toml").read_bytes() == \
            (drive.official / ".researchos" / "mappings.toml").read_bytes()
        assert share.sync(drive.work)["entries_sent"] == 0
    drive.witness.look()


@needs_ssh
def test_12_12_a_links_packet_not_signed_by_its_member_is_not_taken(drive):
    """A packet in a member's area whose signature is not that member's is
    reported and its links are not applied."""
    with drive.member():
        assert cli.main(["ingest", str(drive.work)]) == 0
        mappings_ingest.add_mapping(drive.work, "figs/fig1.png", "attempt:map.md#16", "belongs_to")
        share.sync(drive.work)
    packet = next(p for p in (drive.shared / "提议" / MEMBER[1]).iterdir() if p.name.startswith("n-"))
    forged = packet.parent / ("n-" + "0" * 32)
    shutil.copytree(packet, forged)
    raw = (forged / "entries.toml").read_bytes().replace(packet.name.encode(), forged.name.encode())
    os.chmod(forged / "entries.toml", 0o644)
    (forged / "entries.toml").write_bytes(raw)
    (forged / "ready.toml").write_text(f'packet = "{forged.name}"\nsha256 = "{hashlib.sha256(raw).hexdigest()}"\n')
    with drive.pi():
        done = share.publish(drive.official)
        assert done["collected"]["links"]["packets"] == 1  # the real one
        assert any(p["kind"] == "unverified" and p["path"] == forged.name for p in done["collected"]["problems"])


# -- 4. a damaged blob is repaired by the PI (12.12 The shared drive) ------------------------------------------


@needs_ssh
def test_12_12_the_pi_removes_a_damaged_blob_and_the_next_publish_writes_it_again(drive, tmp_path, capsys):
    """`researchos share repair`: a dry run lists the damaged blob (which
    snapshots name it, whether it can be written again) and removes nothing;
    with --yes only that blob goes; the next publish writes it again and a
    new member can join."""
    with drive.pi():
        fig = drive.official / "figs" / "fig1.png"
        fig.write_bytes(b"\x89PNG a binary figure\x00\x01")
        share.publish(drive.official)
        sha = hashlib.sha256(fig.read_bytes()).hexdigest()
        blob = drive.shared / "已发布" / "blobs" / sha
        data = bytearray(blob.read_bytes())
        data[-1] ^= 0xFF
        os.chmod(blob, 0o644)
        blob.write_bytes(bytes(data))
        copy = drive.official / "figs" / "fig1-copy.png"
        copy.write_bytes(fig.read_bytes())  # the same content, published again: it meets the damaged blob
        with pytest.raises(share.ShareError) as refused:
            share.publish(drive.official)
        assert refused.value.code == "damaged" and "researchos share repair" in refused.value.message_zh
        copy.unlink()
        others = {p: p.stat().st_mtime_ns for p in drive.shared.rglob("*") if p.is_file() and p != blob}
        assert cli.main(["share", "repair", str(drive.official)]) == 0
        assert "Would remove 1 damaged blob(s)" in capsys.readouterr().out and blob.exists()
        dry = share.repair_blobs(drive.official)
        assert [(d["sha256"], d["restorable"]) for d in dry["damaged"]] == [(sha, True)] and dry["damaged"][0]["snapshots"]
        done = share.repair_blobs(drive.official, apply=True)
        assert done["removed"] == [sha] and not blob.exists()
        assert {p: p.stat().st_mtime_ns for p in drive.shared.rglob("*") if p.is_file()} == others
        again = share.publish(drive.official)
        assert again["restored"] == [sha] and hashlib.sha256(blob.read_bytes()).hexdigest() == sha
        assert share.repair_blobs(drive.official)["damaged"] == []
    with _as(drive.mp, tmp_path / "home-new"):
        people.write_me("新", "new@example.com")
        joined = share.join(drive.shared, tmp_path / "new-work")
        assert (tmp_path / "new-work" / "figs" / "fig1.png").read_bytes() == b"\x89PNG a binary figure\x00\x01"
        assert joined["files"] > 0


# -- 5. a member's own work, in every view (12.12) -----------------------------------------------------------


def _states(payload, key: str = "id") -> dict:
    found = {}

    def walk(value):
        if isinstance(value, dict):
            if key in value and officialness.FIELD in value:
                found.setdefault(value[key], value[officialness.FIELD]["state"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return found


@needs_ssh
def test_12_12_a_members_own_work_is_marked_in_every_view_never_dropped(drive):
    """In the member's working copy: an unchanged file is official; a script
    the member edited and one they added are 「我的改动，尚未提交」; a new
    attempt and its script in a pending proposal are 「提议中」 -- until the
    member changes the script again. Every view the interface uses carries
    the field; the PI's official version is official throughout."""
    with drive.member():
        (drive.work / "steps" / "17-plot.py").write_text((drive.work / "steps" / "17-plot.py").read_text() + "# mine\n")
        (drive.work / "steps" / "19-new.py").write_text('import pandas as pd\ndf = pd.read_csv("data/raw.csv")\n')
        (drive.work / "steps" / "18-big.py").write_text(NEW_SCRIPT)
        (drive.work / "map.md").write_text(_map() + ROW_18)
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(
            drive.work, "attempt_new", "18", only=["steps/18-big.py"]))
        assert cli.main(["ingest", str(drive.work)]) == 0
        seen = officialness.view(drive.work)
        assert seen.mode == "working_copy" and seen.seq == 1 and seen.pending_known
        assert seen.file_state("data/raw.csv") == ("official", None)
        assert seen.file_state("steps/17-plot.py") == ("mine", None)
        assert seen.file_state("steps/19-new.py") == ("mine", None)
        assert seen.file_state("steps/18-big.py") == ("proposed", stored.id)
        assert seen.node_state("attempt:map.md#18") == ("proposed", stored.id)
        assert seen.node_state("attempt:map.md#16") == ("official", None)
        assert officialness.mark(seen.node_state("script:steps/19-new.py")) == {"state": "mine", "label": "我的改动，尚未提交"}
        with _serve(drive.work) as base:
            status, tree = _call(f"{base}/api/tree")
            assert status == 200 and tree["officialness_mode"]["mode"] == "working_copy"
            states = _states(tree)
            assert states["attempt:map.md#18"] == "proposed" and states["attempt:map.md#16"] == "official"
            assert _states(tree, "path")["steps/18-big.py"] == "proposed"
            status, canvas = _call(f"{base}/api/canvas?scope=all")
            nodes = _states(canvas)
            assert nodes["script:steps/19-new.py"] == "mine" and nodes["script:steps/17-plot.py"] == "mine"
            assert nodes["dataset:data/raw.csv"] == "official"
            status, trace = _call(f"{base}/api/trace?node=figs%2Ffig2.png")
            assert status == 200 and trace["node"]["officialness"]["state"] == "official"
            assert _states(trace)["script:steps/17-plot.py"] == "mine"
            status, file = _call(f"{base}/api/file?path=steps%2F17-plot.py")
            assert file["officialness"] == {"state": "mine", "label": "我的改动，尚未提交"}
            status, lineage = _call(f"{base}/api/lineage")
            assert status == 200 and "officialness_mode" in lineage
            status, attempts = _call(f"{base}/api/attempts")
            assert _states(attempts)["attempt:map.md#18"] == "proposed"
            for path in ("/api/citations", "/api/variables", "/api/history?node=steps%2F17-plot.py"):
                status, body = _call(f"{base}{path}")
                assert status == 200 and body["officialness_mode"]["mode"] == "working_copy", path
        (drive.work / "steps" / "18-big.py").write_text(NEW_SCRIPT + "# changed again\n")
        assert officialness.view(drive.work).file_state("steps/18-big.py") == ("mine", None)
        (drive.work / ".researchos" / "variables" / "fx").mkdir(parents=True)
        (drive.work / ".researchos" / "variables" / "fx" / "v1.toml").write_text("draft = true\n")
        assert officialness.view(drive.work).card_state("fx") == ("mine", None)
    with drive.pi():
        _scan(drive.official)
        with _serve(drive.official) as base:
            status, canvas = _call(f"{base}/api/canvas?scope=all")
            assert canvas["officialness_mode"]["mode"] == "official"
            assert set(_states(canvas).values()) == {"official"}


def test_12_12_a_member_not_yet_in_the_team_is_told_so(tmp_path, monkeypatch):
    """A copy joined from a shared folder whose team does not list me yet
    (only the PI) is not a project of one: 'propose' and POST
    /api/proposals answer with the 12.12 sentence."""
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    pi_home, member_home = tmp_path / "home-pi", tmp_path / "home-member"
    shared, official, work = tmp_path / "shared", tmp_path / "official", tmp_path / "work"
    shared.mkdir()
    with _as(monkeypatch, pi_home):
        people.write_me(*PI)
        _project(official)
        share.team_init(official)
        pid = read_identity(official).identity.id
        exchange_mod.configure(pid, shared, official)
        share.publish(official)
    with _as(monkeypatch, member_home):
        people.write_me(*MEMBER)
        assert share.join(shared, work)["role"] == "none"
        (work / "steps" / "16-build.py").write_text("print('changed')\n")
        assert proposals.role_of(work)[0] == "none"
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.make_proposal(work, "files", "改脚本")
        assert refused.value.code == "not_in_team" and refused.value.message_zh == SENTENCE
        with _serve(work) as base:
            status, body = _call(f"{base}/api/proposals", "POST", {"kind": "files", "sentence": "改脚本"})
            assert status == 403 and body["message"] == SENTENCE
        assert proposals.listing(work)["role"] == "none"


# -- 6. the verdict as a word (12.11), and the history store's size (12.12) ----------------------------------


@pytest.mark.parametrize("cell, expected", [
    ("☠️ 审稿1轮 Major", ("abandoned", "已放弃", "审稿1轮 Major")),
    ("☠ 伪协整", ("abandoned", "已放弃", "伪协整")),
    ("✅ **现行论文**", ("current", "现行", "**现行论文**")),
    ("🕒 待拍板", ("pending", "待定", "待拍板")),
    ("⚠️ 不利证据，已写入现行稿", ("adverse", "不利证据", "不利证据，已写入现行稿")),
    ("⛔ 结果稳健但方向被否", ("rejected", "被否", "结果稳健但方向被否")),
    ("📋 归档，批评仍适用", ("archived", "归档", "归档，批评仍适用")),
    ("🚀 发射", ("other", "其它", "发射")),
    ("没有标记的结论", ("other", "其它", "没有标记的结论")),
    ("", ("other", "其它", "")),
])
def test_12_11_a_verdict_cell_is_a_status_a_word_and_the_researchers_words(cell, expected):
    got = attempts_ingest.verdict_status(cell)
    assert (got["status"], got["label"], got["words"]) == expected


def test_12_11_verdict_labels_come_from_attempts_toml(tmp_path, monkeypatch, team):
    """`[verdict_labels]` adds a marker or renames one; the tree, the
    attempts and a proposal's verdict change carry the word beside the
    researcher's own text (the raw cell is kept as it is)."""
    with team.pi():
        config = team.official / ".researchos" / "attempts.toml"
        config.write_text(ATTEMPTS_TOML + '\n[verdict_labels]\n"❌" = "被否"\n"✅" = "采用"\nbad = 3\n')
        labels = attempts_ingest.verdict_labels(team.official)
        assert labels["❌"] == "被否" and labels["✅"] == "采用" and labels["📋"] == "归档" and "bad" not in labels
        assert attempts_ingest.verdict_status("❌ 不成立", labels) == {"status": "rejected", "label": "被否", "words": "不成立"}
        assert attempts_ingest.verdict_status("✅ ok", labels)["status"] == "labelled"
        conn = db.connect(index_db_path(team.pid))
        try:
            from researchos.webapp import server  # noqa: PLC0415

            tree = server.tree_payload(conn, team.official)
            attempts = server.attempts_payload(conn, team.official)
        finally:
            conn.close()
        assert tree["attempts"][0]["verdict"] == "🕒"
        assert tree["attempts"][0]["verdict_status"] == {"status": "pending", "label": "待定", "words": ""}
        assert attempts["attempts"][0]["verdict_status"]["label"] == "待定"
    with team.member():
        (team.work / "map.md").write_text(_map("☠️ 效应消失"))
        stored = proposals.write_proposal(team.work, proposals.make_proposal(
            team.work, "verdict_change", "放弃 16", reason="残差不平稳"))
        mine = proposals.listing(team.work)["proposals"][0]
        assert mine["verdict"]["old_status"]["label"] == "待定"
        assert mine["verdict"]["new_status"] == {"status": "abandoned", "label": "已放弃", "words": "效应消失"}
    with team.pi():
        shown = proposals.open_for(team.official, stored.id)
        assert shown["changes"]["attempt"]["new_status"]["label"] == "已放弃"
        assert set(shown["changes"]["attempt"]["fields"]) <= {"verdict", "result"}


def test_12_12_records_lists_the_history_store_with_its_size(tmp_path, monkeypatch, capsys):
    """`researchos records` (and /api/records' rows) list the version store
    -- beside the index, not in the project -- with its size."""
    root = _project(tmp_path / "p")
    (root / "data" / "panel.csv").write_text("id,x\n1,2\n")
    _scan(root)
    pid = read_identity(root).identity.id
    size = inventory.history_size(pid)
    assert size["exists"] and size["versions"] >= 2 and size["bytes"] > 0
    row = next(r for r in inventory.inventory(None, root) if r.code == "history")
    assert row.facts["bytes"] == size["bytes"] and row.path == str(history.history_dir(pid))
    assert cli.main(["records", str(root)]) == 0
    out = capsys.readouterr().out
    assert "Version history (kept material, not a record)" in out and f"{size['bytes']} bytes" in out
    assert "deleting the index's folder deletes it" in out

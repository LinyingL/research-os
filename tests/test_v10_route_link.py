"""Research OS V10, increment A2 (DESIGN.md 14.2, 12.4): who draws a
「源自」 line. The PI draws directly; a member's line -- added or removed --
is a `route_link` proposal, marked 提议中 on the member's map until the PI
decides: approved, it is written to the official mappings.toml and recorded
in decisions.toml; returned, it disappears with its reason. A new attempt
may name what it grew out of. A member's line never travels in a packet.
12.10.3's two people on one machine, with real signatures. Every test
names its scenario."""

from __future__ import annotations

import tomllib

import pytest

from researchos import db, officialness, proposals, share, signing
from researchos.ingest import mappings as mappings_ingest
from researchos.records import decisions as decisions_mod

from test_v10_derives_from import _map
from test_v8_phase2 import _call, _conn, _scan
from test_v8_phase3 import _serve
from test_v8_phase4 import MEMBER, PI, Drive

A16, A17, A18, A19 = (f"attempt:map.md#{n}" for n in (16, 17, 18, 19))


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Drive:
    """12.10.3's PI and member; the official table holds #16, #17 and #18."""
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    d = Drive(tmp_path, monkeypatch, join=False)
    with d.pi():
        (d.official / "map.md").write_text(_map())
        _scan(d.official)
        share.publish(d.official, note="三行")
    with d.member():
        share.join(d.shared, d.work)
    return d


def _line(d: Drive, op: str, child: str, parent: str, note: str | None = None):
    with d.member():
        route = {"op": op, "child": child, "parent": parent, "note": note}
        return proposals.write_proposal(d.work, proposals.make_proposal(d.work, "route_link", "", route=route))


def _official(root) -> list[dict]:
    text = mappings_ingest.mappings_path(root).read_text() if mappings_ingest.mappings_path(root).exists() else ""
    return [e for e in tomllib.loads(text).get("mapping", []) if e["type"] == "derives_from"]


def _indexed(root) -> set[tuple[str, str]]:
    conn = _conn(root)
    try:
        return {(e["src"], e["dst"]) for e in db.query_edges(conn, type="derives_from") if e["status"] == "confirmed"}
    finally:
        conn.close()


def _marks(d: Drive, lang: str = "zh") -> list[tuple]:
    with d.member():
        lines = officialness.proposed_lines(officialness.view(d.work), lang)
    return [(x["src"], x["dst"], x["op"], x["officialness"]["state"], x["officialness"]["label"]) for x in lines]


def test_14_2_a_members_line_is_proposed_marked_then_approved_official_and_recorded(drive):
    stored = _line(drive, "add", A17, "16", note="换成 事件轴")
    with drive.member():
        (mine,) = proposals.listing(drive.work)["proposals"]
        assert (mine["kind"], mine["state"], mine["sentence"]) == ("route_link", "pending", "")
        assert mine["route"] == {"op": "add", "child": A17, "parent": A16, "note": "换成 事件轴",
                                 "child_number": "17", "parent_number": "16"}
        assert not _official(drive.work)  # never written into the member's copy
    assert _marks(drive, "en") == [(A17, A16, "add", "proposed", "Proposed")]
    with drive.pi():
        (inbox,) = proposals.listing(drive.official)["proposals"]
        assert inbox["route"]["child_number"] == "17"
        changes = proposals.open_for(drive.official, stored.id, lang="en")["changes"]
        assert changes["approvable"] and changes["route"]["state"] == "apply"
        assert changes["route"]["words"] == "Approving adds the line: #17 grew out of #16"
        assert proposals.open_for(drive.official, stored.id)["changes"]["route"]["words"] == "批准后加上连线：#17 源自 #16"
        done = proposals.approve(drive.official, stored.id, note="同意")
        assert done["applied"]["lines"] == [{"op": "add", "child": A17, "parent": A16}]
        decision = done["decision"]
        assert (decision["kind"], decision["act"], decision["by"], decision["approved_by"]) == \
            ("route_link", "approved", MEMBER[1], PI[1])
        assert (decision["op"], decision["child"], decision["parent"]) == ("add", A17, A16)
        assert decisions_mod.for_proposal(drive.official, stored.id)["id"] == decision["id"]
        (entry,) = _official(drive.official)
        assert (entry["from"], entry["to"], entry["by"], entry["note"]) == (A17, A16, MEMBER[1], "换成 事件轴")
        assert _indexed(drive.official) == {(A17, A16)}
        again = proposals.open_for(drive.official, stored.id)
        assert again["state"] == "approved"
    assert _marks(drive) == [(A17, A16, "add", "approved", "已批准，待同步")]
    with drive.member():
        assert ".researchos/mappings.toml" in share.sync(drive.work)["updated"]
    assert _marks(drive) == []  # official now
    drive.witness.look()


def test_14_2_a_returned_line_disappears_and_its_reason_stays_in_my_proposals(drive):
    with drive.pi():
        mappings_ingest.add_mapping(drive.official, A18, A17, "derives_from")  # the PI draws directly
        share.publish(drive.official)
    with drive.member():
        share.sync(drive.work)
    stored = _line(drive, "remove", A18, A17)
    assert _marks(drive) == [(A18, A17, "remove", "proposed", "提议中")]
    with drive.pi():
        assert proposals.open_for(drive.official, stored.id)["changes"]["route"]["words"] == "批准后去掉连线：#18 源自 #17"
        proposals.return_(drive.official, stored.id, "这条线是对的")
        assert [(e["from"], e["to"]) for e in _official(drive.official)] == [(A18, A17)]
    with drive.member():
        (mine,) = proposals.listing(drive.work)["proposals"]
        assert mine["state"] == "returned" and mine["outcomes"][-1]["text"] == "这条线是对的"
        view = officialness.view(drive.work)
        assert view.link_state("map.md#18", "map.md#17", "derives_from") == ("official", None)
    assert _marks(drive) == []


def test_14_2_each_rule_refuses_a_line_proposal_with_its_own_code_and_a_member_never_writes_one(drive):
    with drive.pi():
        mappings_ingest.add_mapping(drive.official, A17, A16, "derives_from")
        share.publish(drive.official)
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.make_proposal(drive.official, "route_link", "", route={"op": "add", "child": A18, "parent": A16})
        assert refused.value.code == "pi_changes_directly"
    with drive.member():
        share.sync(drive.work)
        for route, code in (
            ({"op": "add", "child": A17, "parent": "17"}, "self_link"),
            ({"op": "add", "child": A17, "parent": A16}, "line_exists"),
            ({"op": "add", "child": A16, "parent": A17}, "circle"),
            ({"op": "remove", "child": A18, "parent": A16}, "no_such_line"),
            ({"op": "add", "child": "attempt:map.md#99", "parent": A16}, "attempt_not_found"),
            ({"op": "add", "child": "attempt:other.md#18", "parent": A16}, "attempt_not_found"),
            ({"op": "move", "child": A18, "parent": A16}, "bad_route"),
        ):
            with pytest.raises(proposals.ProposalRefused) as refused:
                proposals.make_proposal(drive.work, "route_link", "", route=route)
            assert refused.value.code == code, route
            assert refused.value.message_zh == proposals.MESSAGES[code]
        before = mappings_ingest.mappings_path(drive.work).read_bytes()
        for write in (lambda: mappings_ingest.add_mapping(drive.work, A18, A16, "derives_from"),
                      lambda: mappings_ingest.delete_mapping(drive.work, A17, A16, "derives_from")):
            with pytest.raises(mappings_ingest.MappingsWriteError) as refused:
                write()
            assert refused.value.code == "member_proposes"
        assert mappings_ingest.mappings_path(drive.work).read_bytes() == before
    assert not any((drive.shared / "提议" / MEMBER[1]).glob("pr-*"))


def test_14_2_approving_a_line_whose_end_is_gone_or_that_closes_a_circle_now_is_refused(drive):
    circle = _line(drive, "add", A16, A18)
    gone = _line(drive, "add", A17, A18)
    with drive.pi():
        mappings_ingest.add_mapping(drive.official, A18, A16, "derives_from")  # the PI draws the other way
        (drive.official / "map.md").write_text(_map(("16", "17")).replace("| 17 |", "| 18 |"))  # #17 renumbered
        _scan(drive.official)
        before = mappings_ingest.mappings_path(drive.official).read_bytes()
        for stored, code in ((circle, "circle"), (gone, "attempt_not_found")):
            changes = proposals.open_for(drive.official, stored.id)["changes"]
            assert (changes["route"]["state"], changes["route"]["code"], changes["approvable"]) == ("conflict", code, False)
            with pytest.raises(proposals.ProposalRefused) as refused:
                proposals.approve(drive.official, stored.id)
            assert refused.value.code == "not_approvable"
            assert refused.value.details["conflicts"][0]["code"] == code
            assert decisions_mod.for_proposal(drive.official, stored.id) is None
        assert mappings_ingest.mappings_path(drive.official).read_bytes() == before


def test_14_2_a_new_attempt_names_what_it_grew_out_of_and_approving_adds_those_lines(drive):
    with drive.member():
        (drive.work / "map.md").write_text(_map(("16", "17", "18", "19")))
        for parents, code in ((["19"], "self_link"), (["20"], "attempt_not_found")):
            with pytest.raises(proposals.ProposalRefused) as refused:
                proposals.make_proposal(drive.work, "attempt_new", "第 19 条", parents=parents)
            assert refused.value.code == code
        stored = proposals.write_proposal(
            drive.work, proposals.make_proposal(drive.work, "attempt_new", "第 19 条", parents=["17", A16, "17"]))
        assert stored.data["attempt"]["parents"] == [A17, A16]
    assert _marks(drive) == [(A19, A16, "add", "proposed", "提议中"), (A19, A17, "add", "proposed", "提议中")]
    with drive.pi():
        assert proposals.open_for(drive.official, stored.id)["changes"]["attempt"]["parents"] == [A17, A16]
        done = proposals.approve(drive.official, stored.id)
        assert done["decision"]["parents"] == [A17, A16]
        assert {(e["from"], e["to"], e["by"]) for e in _official(drive.official)} == {(A19, A17, MEMBER[1]),
                                                                                     (A19, A16, MEMBER[1])}
        assert _indexed(drive.official) == {(A19, A17), (A19, A16)}


def test_14_2_a_members_hand_written_line_never_travels_in_a_packet(drive):
    """12.12's packets carry a member's other links without approval; a
    「源自」 line written into the copy by hand is left out of them."""
    with drive.member():
        path = mappings_ingest.mappings_path(drive.work)
        path.write_text((path.read_text() if path.exists() else "") + (
            '\n[[mapping]]\nfrom = "attempt:map.md#18"\nto = "attempt:map.md#16"\ntype = "derives_from"\n'
            f'by = "{MEMBER[1]}"\n\n[[mapping]]\nfrom = "figs/fig1.png"\nto = "attempt:map.md#16"\n'
            f'type = "belongs_to"\nby = "{MEMBER[1]}"\n'))
        assert share.sync(drive.work)["entries_sent"] == 1
    with drive.pi():
        assert share.publish(drive.official)["collected"]["links"]["added"] == 1
        kinds = [m.type for m in mappings_ingest.load_mappings(drive.official).mappings]
        assert "belongs_to" in kinds and "derives_from" not in kinds


def test_14_2_the_page_proposes_a_line_and_reads_what_approving_does(drive):
    with drive.member():
        with _serve(drive.work) as base:
            body = {"kind": "route_link", "op": "add", "child": A18, "parent": A17, "write": True}
            status, written = _call(f"{base}/api/proposals", "POST", body)
            assert status == 200 and written["written"] is True
            status, refused = _call(f"{base}/api/proposals", "POST", {**body, "parent": A18})
            assert (status, refused["state"], refused["message"]) == (400, "self_link", "一个尝试不能源自它自己")
            status, refused = _call(f"{base}/api/mappings/add", "POST", {"from": A18, "to": A16, "type": "derives_from"})
            assert (status, refused["state"]) == (400, "mapping_member_proposes")
            status, listed = _call(f"{base}/api/proposals")
            assert [p["kind"] for p in listed["proposals"]] == ["route_link"]
    with drive.pi():
        with _serve(drive.official) as base:
            status, shown = _call(f"{base}/api/proposals/{written['id']}", headers={"X-ResearchOS-Lang": "en"})
            assert status == 200 and shown["changes"]["route"]["words"] == "Approving adds the line: #18 grew out of #17"
            status, done = _call(f"{base}/api/proposals/{written['id']}/approve", "POST", {})
            assert status == 200 and done["applied"]["lines"] == [{"op": "add", "child": A18, "parent": A17}]

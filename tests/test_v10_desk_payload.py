"""Research OS V10, increment A3 (DESIGN.md 14.1, 14.3-14.5): one rule for
an attempt's files -- its step files and its 「关联文件」 -- shared by the
node page, 关系图's attempt view and the desk; and the desk's payload for
the route map: the cards with their marks, the 「源自」 lines and the column,
in a single-person, a PI's and a member's project, in either language, with
a shared folder that cannot be read. Every test names its scenario."""

from __future__ import annotations

import time

from researchos import cli, db, desk, nodepage, proposals, share, trace
from researchos.ingest import attempts as attempts_ingest
from researchos.ingest import mappings as mappings_ingest
from researchos.records import judgements
from researchos.webapp import canvas

from test_v10_derives_from import _map
from test_v10_route_link import _line, drive  # noqa: F401 -- the two-person fixture
from test_v8_phase2 import ATTEMPTS_TOML, _call, _conn, _project, _scan
from test_v8_phase3 import _serve
from test_v8_phase4 import MEMBER
from test_v8_u2 import _backdate
from test_v9_language import _THEIR_KEYS, CJK, _strings

A16, A17, A18, A20 = (f"attempt:map.md#{n}" for n in (16, 17, 18, 20))
FIG1 = "figure:figs/fig1.png"
OTHER = 'import pandas as pd\ndf = pd.read_csv("data/raw.csv")\n'
MAP = ("# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n"
       "| 16 | 07-26 | TopicShift volatility (16-17) | V | t=2.91 | 🕒 |\n"
       "| 17 | 07-08~09 | Event axis | V | t=0.1（放弃理由：无信号） | ☠ 方向反了 |\n"
       "| 18 | ≤07-07 | Plain panel | V | r18 | ✅ |\n| 19 | 秋天 | Later idea | V | r19 | 🕒 |\n")


def test_14_3_one_membership_rule_for_the_node_page_the_relation_map_and_the_desk(tmp_path):
    """#16 names steps 16-17 and 「关联文件」 attaches figs/fig1.png: the node
    page's trace and the attempt view of 关系图 show the same files."""
    root = _project(tmp_path / "proj")
    mappings_ingest.add_mapping(root, "figs/fig1.png", A16, "belongs_to")
    _scan(root)
    conn = _conn(root)
    try:
        members = trace.attempt_members(conn, root)[A16]
        page = nodepage.node(conn, root, A16)["trace"]
        drawn = canvas.build_canvas(conn, root, A16)
    finally:
        conn.close()
    assert members == {"script:steps/16-build.py": {"via": "step_files"}, "script:steps/17-plot.py": {"via": "step_files"},
                       FIG1: {"via": "belongs_to", "link": {"src": FIG1, "dst": A16, "type": "belongs_to",
                                                            "extractor": "mapping"}}}
    on_page = {i["id"] for c in page["columns"] for i in c["items"] if i.get("member")}
    assert on_page == set(members)
    assert next(i for c in page["columns"] for i in c["items"] if i["id"] == FIG1)["attached"] is True
    (frame,) = drawn["frames"]
    assert set(frame["node_ids"]) == set(members)  # 关系图 now holds the attached figure too
    assert FIG1 in {n["id"] for n in drawn["nodes"]}


def _payload(root, lang="zh", **options) -> dict:
    conn = _conn(root)
    try:
        return desk.payload(conn, root, None, lang=lang, **options)
    finally:
        conn.close()


def _under_review(root, script: str) -> tuple:
    """A rejected `reads` link of `script` whose basis then changed (9.6)."""
    conn = _conn(root)
    try:
        (edge,) = db.query_edges(conn, src=f"script:{script}", type="reads")
    finally:
        conn.close()
    key = (edge["src"], edge["dst"], edge["type"], edge["extractor"])
    judgements.judge(root, key, "rejected", via="cli")
    (root / script).write_text((root / script).read_text().replace("read_csv", "read_json"))
    return key


def _single(tmp_path):
    """One person: four attempts with dates as the table writes them, a line,
    a line whose child was renumbered away, a figure attached to #18, and two
    judgments under review -- one on #16's step script, one on a script no
    attempt holds; then #16's script and #19's row change."""
    root = _project(tmp_path / "proj")
    (root / ".researchos" / "attempts.toml").write_text(
        ATTEMPTS_TOML.replace('steps_dir = "steps"\n', 'steps_dir = "steps"\ndate_year = 2026\n'))
    (root / "other.py").write_text(OTHER)
    (root / "map.md").write_text(MAP)
    _scan(root)
    mappings_ingest.add_mapping(root, A17, A16, "derives_from", note="换轴")
    mappings_ingest.add_mapping(root, "figs/fig1.png", A18, "belongs_to")
    path = mappings_ingest.mappings_path(root)
    path.write_text(path.read_text() + f'\n[[mapping]]\nfrom = "{A20}"\nto = "{A16}"\ntype = "derives_from"\n')
    _scan(root)
    _backdate(root)
    keys = [_under_review(root, "steps/16-build.py"), _under_review(root, "other.py")]
    (root / "map.md").write_text(MAP.replace("r19", "r19 again"))
    _scan(root)
    return root, keys


def test_14_1_a_single_persons_map_cards_lines_marks_and_column(tmp_path):
    root, (key16, key_other) = _single(tmp_path)
    before = time.time()
    got = _payload(root)
    assert got["role"] == "single" and "changes" in got and "abandoned" in got  # the old blocks stay until C
    cards = [(a["number"], a["date"], a["date_key"], a["order"], a["route_status"]["status"], a["tone"], a["abandoned"])
             for a in got["attempts"]]
    assert cards == [("18", "≤07-07", "2026-07-07", 0, "current", "olive", False),
                     ("17", "07-08~09", "2026-07-09", 1, "abandoned", "gray", True),
                     ("16", "07-26", "2026-07-26", 2, "pending", "ochre", False),
                     ("19", "秋天", None, 3, "pending", "ochre", False)]
    by = {a["number"]: a for a in got["attempts"]}
    assert (by["17"]["result"], by["17"]["reason"], by["16"]["reason"]) == ("t=0.1", "无信号", None)
    assert all(a["officialness"] == {"state": "official"} for a in got["attempts"])
    # 「有改动」: #16's step script and #19's row changed after the baseline; #17 and #18 did not.
    assert by["16"]["changed_at"] and by["19"]["changed_at"] and not by["17"]["changed_at"] and not by["18"]["changed_at"]
    assert desk._parse(by["16"]["changed_at"]).timestamp() > before - 120
    assert by["16"]["marks"] == {"proposals": [], "review": {"count": 1, "label": "待复核 1"}}
    assert by["18"]["marks"]["review"] is None
    (line,) = got["lines"]
    assert (line["child"], line["parent"], line["note"], line["by_label"], line["pending"]) == \
        (A17, A16, "换轴", "最初的研究者", None)
    assert line["officialness"]["state"] == "official"
    (gone,) = got["lines_missing"]
    assert (gone["child"], gone["missing"], gone["child_number"], gone["parent_number"]) == (A20, ["child"], "20", "16")
    column = got["column"]
    assert (column["role"], column["notice"], column["shared"], column["sync"], column["empty"]) == \
        ("single", None, {"state": "not_needed", "message": None}, None, False)
    review = column["review"]
    assert review["count"] == 2 and [g["id"] for g in review["groups"]] == [A16]
    (item,) = review["groups"][0]["items"]
    assert (item["src"], item["dst"], item["reason"], item["label"], item["attempts"]) == \
        (key16[0], key16[1], "basis_changed", "依据已变化", [A16])
    assert [(i["src"], i["attempts"]) for i in review["none"]["items"]] == [(key_other[0], [])]


def test_14_6_the_maps_composed_words_follow_the_language(tmp_path):
    root, _keys = _single(tmp_path)
    zh, en = _payload(root), _payload(root, "en")
    words = [(p["attempts"][2]["marks"]["review"]["label"], p["column"]["review"]["groups"][0]["items"][0]["label"],
              p["lines"][0]["by_label"]) for p in (zh, en)]
    assert words == [("待复核 1", "依据已变化", "最初的研究者"), ("Needs review: 1", "The basis changed", "Original researcher")]
    assert [a["id"] for a in zh["attempts"]] == [a["id"] for a in en["attempts"]]
    vocabulary = set(attempts_ingest.DEFAULT_VERDICT_LABELS.values()) | {attempts_ingest.OTHER_VERDICT_LABEL}
    researchers = _THEIR_KEYS | {"date", "note", "src_label", "dst_label"}
    shown = {k: en[k] for k in ("attempts", "lines", "lines_missing", "column")}
    assert [(k, s) for k, s in _strings(shown) if CJK.search(s) and k not in researchers
            and not (k == "label" and s in vocabulary)] == []


def _two_people(d):
    """The PI draws #17 源自 #16; the member syncs, then proposes a verdict
    change on #16, a line #18 源自 #17 and the removal of #17 源自 #16."""
    with d.pi():
        mappings_ingest.add_mapping(d.official, A17, A16, "derives_from")
        share.publish(d.official)
    with d.member():
        share.sync(d.work)
        (d.work / "map.md").write_text(_map().replace("| r16 | 🕒 |", "| r16 | ✅ 有效 |"))
        assert cli.main(["ingest", str(d.work)]) == 0  # the copy opened on this machine, as the app does
        verdict = proposals.write_proposal(d.work, proposals.make_proposal(d.work, "verdict_change", "16 有效", only=[]))
    return verdict, _line(d, "add", A18, A17, note="接着做"), _line(d, "remove", A17, A16)


def test_14_4_a_members_map_and_column_and_the_pis(drive):
    verdict, add, remove = _two_people(drive)
    with drive.member():
        mine = _payload(drive.work)
    assert mine["role"] == "member" and mine["column"]["shared"] == {"state": "ok", "message": None}
    marks = {a["number"]: {m["id"] for m in a["marks"]["proposals"]} for a in mine["attempts"]}
    assert marks == {"16": {remove.id, verdict.id}, "17": {remove.id, add.id}, "18": {add.id}}
    assert {m["label"] for a in mine["attempts"] for m in a["marks"]["proposals"]} == {"提议中"}
    assert next(a for a in mine["attempts"] if a["number"] == "16")["officialness"]["state"] == "proposed"
    lines = {(x["child"], x["parent"]): x for x in mine["lines"]}
    assert (lines[(A17, A16)]["pending"]["op"], lines[(A17, A16)]["officialness"]["label"]) == ("remove", "提议中")
    assert (lines[(A18, A17)]["pending"]["proposal"], lines[(A18, A17)]["note"], lines[(A18, A17)]["by_label"]) == \
        (add.id, "接着做", MEMBER[0])
    assert {p["id"] for p in mine["column"]["mine"]} == {remove.id, add.id, verdict.id}
    at = [p["at"] for p in mine["column"]["mine"]]
    assert at == sorted(at, reverse=True)  # newest first
    assert mine["column"]["sync"]["behind"] is False and mine["column"]["waiting"] == []
    with drive.pi():
        pi = _payload(drive.official, "en")
        waiting = {p["id"]: p for p in pi["column"]["waiting"]}
        assert pi["role"] == "pi" and set(waiting) == {remove.id, add.id, verdict.id}
        assert (waiting[remove.id]["touches"], waiting[verdict.id]["touches"], waiting[remove.id]["by_label"]) == \
            ([A17, A16], [A16], MEMBER[0])
        assert {m["label"] for a in pi["attempts"] for m in a["marks"]["proposals"]} == {"Awaiting your approval"}
        (line,) = pi["lines"]  # a member's line is not the PI's until approved
        assert (line["child"], line["pending"]["op"], line["pending"]["proposal"]) == (A17, "remove", remove.id)
        proposals.return_(drive.official, add.id, "先别连")
    with drive.member():
        after = _payload(drive.work)
    assert (A18, A17) not in {(x["child"], x["parent"]) for x in after["lines"]}  # returned: it disappears
    returned = next(p for p in after["column"]["mine"] if p["id"] == add.id)
    assert (returned["state"], returned["reason"]) == ("returned", "先别连")


def test_14_4_a_shared_folder_that_cannot_be_read_never_holds_back_the_map(drive, monkeypatch):
    _two_people(drive)
    with drive.member():
        read, slow = desk._read_shared, (lambda *a: time.sleep(2) or {})
        monkeypatch.setattr(desk, "_read_shared", slow)
        started = time.monotonic()
        got = _payload(drive.work, "en", timeout=0.2)
        assert time.monotonic() - started < 1.5
        assert [a["number"] for a in got["attempts"]] == ["16", "17", "18"] and len(got["lines"]) == 1
        assert got["column"]["shared"] == {"state": "slow", "message": desk.SHARED_WORDS["slow"][1]}
        assert got["column"]["role"] == "member" and got["officialness_mode"]["pending_known"] is False
        monkeypatch.setattr(desk, "_read_shared", read)  # (not undo(): the homes are monkeypatched too)
        while desk._JOBS:  # the slow read ends on its own; the next visit starts a read of its own
            time.sleep(0.05)
        drive.shared.rename(drive.shared.with_name("away"))
        with _serve(drive.work) as base:
            status, body = _call(f"{base}/api/desk")
    assert status == 200 and body["role"] == "member" and [a["number"] for a in body["attempts"]] == ["16", "17", "18"]
    assert body["column"]["shared"]["state"] == "unreadable" and body["column"]["mine"] == []
    assert body["column"]["sync"]["reachable"] is False
    assert next(a for a in body["attempts"] if a["number"] == "16")["officialness"]["state"] == "mine"
    with drive.pi():  # the role comes from the team file: the PI's desk never falls back to single
        pi = _payload(drive.official)
    assert (pi["role"], pi["column"]["shared"]["state"], pi["column"]["waiting"]) == ("pi", "unreadable", [])


def test_14_4_an_approved_proposal_waits_in_my_proposals_until_the_sync_brings_it_in(drive):
    """The designer's ruling (2026-10-12): 已批准，待同步 stays in a member's
    column only while something the proposal carries is still marked
    approved here; the sync that brings it in takes it off the list."""
    verdict, add, _remove = _two_people(drive)
    with drive.pi():
        proposals.approve(drive.official, add.id)
    with drive.member():
        column = _payload(drive.work)["column"]
        mine = {p["id"]: p for p in column["mine"]}
        assert (mine[add.id]["state"], mine[add.id]["awaiting_sync"]) == ("approved", True)
        assert (mine[verdict.id]["state"], mine[verdict.id]["awaiting_sync"]) == ("pending", False)
        share.sync(drive.work)
        after = {p["id"]: p for p in _payload(drive.work)["column"]["mine"]}
    assert (after[add.id]["state"], after[add.id]["awaiting_sync"]) == ("approved", False)

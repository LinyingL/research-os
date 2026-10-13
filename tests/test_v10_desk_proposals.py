"""Research OS V10, increment D2 (DESIGN.md 14.3, 14.4): proposals decided
in place on the desk. team.js draws one proposal into any host
(ROTeam.renderProposalInto) -- the 提议 page is its back bar plus that --; the
desk's panel lists the proposals touching an attempt under 待办, each opened
in place; the column's 查看 opens a proposal in the panel as its own subject
and marks the cards it touches; deciding draws the map, the column and the
panel again in place. GET /api/proposals/<id> takes `from`, as the acts do,
for an id in two members' areas. Every test names its scenario."""

from __future__ import annotations

import json
import re

import pytest

from researchos import proposals

from test_v8_phase2 import _call
from test_v8_phase3 import MEMBER, TEAM, Team, _serve
from test_v8_review_round import OTHER, _copy_as
from test_v8_u2 import FORBIDDEN
from test_v9_language import CJK, WEBAPP, _i18n_source, _node, needs_node
from test_v10_desk_panel import _PANEL_DOM, A16, A18, _case, _st


@pytest.fixture
def team(tmp_path, monkeypatch) -> Team:
    return Team(tmp_path, monkeypatch)


def test_14_4_a_proposal_whose_id_is_in_two_areas_opens_by_whose_it_is(team):
    """12.5 / 14.4: a second member's area holds a copy of the first
    member's proposal id. Without `from` the PI's GET is refused as
    ambiguous (as the acts are); with it, each member's own proposal opens --
    the desk's 查看 names whose it is, so the PI never gets the wrong one."""
    (team.official / ".researchos" / "team.toml").write_text(
        TEAM + f'\n[[member]]\nid = "{OTHER[1]}"\nname = "{OTHER[0]}"\nrole = "member"\n')
    with team.member():
        (team.work / "steps" / "18-big.py").write_text("wang honest work\n")
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "files", "诚实的工作"))
    with team.pi():
        _copy_as(team, stored, OTHER[1], "print('a copy')\n")
        with _serve(team.official) as base:
            status, refused = _call(f"{base}/api/proposals/{stored.id}")
            assert (status, refused["state"]) == (409, "exchange_ambiguous")
            status, wang = _call(f"{base}/api/proposals/{stored.id}?from={MEMBER[1]}")
            assert status == 200 and wang["by"] == MEMBER[1] and wang["sentence"] == "诚实的工作"
            assert [f["state"] for f in wang["changes"]["files"]] == ["apply"]
            status, other = _call(f"{base}/api/proposals/{stored.id}?from={OTHER[1]}")
            assert status == 200 and other["by"] == OTHER[1]
    with team.member():
        with _serve(team.work) as base:  # a member opens their own, `from` or not
            status, mine = _call(f"{base}/api/proposals/{stored.id}?from={MEMBER[1]}")
            assert status == 200 and mine["by"] == MEMBER[1] and "changes" not in mine


# -- on the page: team.js's renderer in the desk's panel and on 提议, under node -----------------------

WANG = "wang@example.com"
FROM = "?from=wang%40example.com"
_RUN = r"""
const mk0 = global.mk; // and a row picked (14.4) found by its data-pick; 提议's flash put in place
global.mk = (tag, cls, text) => { const e = mk0(tag, cls, text); const q = e.querySelectorAll;
  e.querySelectorAll = (sel) => sel === "[data-pick]" ? e.all().filter((k) => k.dataset.pick) : q.call(e, sel);
  Object.defineProperty(e, "children", { get: () => e.kids.filter((k) => typeof k !== "string") });
  e.insertBefore = (n, before) => { n.parentNode = e; e.kids.splice(before ? e.kids.indexOf(before) : 0, 0, n); return n; };
  return e; };
global.highlighted = [];
global.mapUpdates = 0;
window.RORouteMap = { create: () => ({ mount() {}, update() { mapUpdates += 1; }, highlight(ids) { highlighted.push(ids); },
  visible: () => true }) };
"""
_AFTER = r"""
global.RONode = Object.assign({}, window.RONode, { openNode: (id) => opened.push(id) });
global.RODesk = window.RODesk;
"""
_TEAM = r"""
global.ROTeam = window.ROTeam;
global.markViewsStale = () => {};
global.closePanel = () => {};
global.activateView = () => {};
global.currentView = "desk";
global.renderBlockingError = (node, framing) => { node.textContent = framing; };
window.scrollTo = () => {};
window.scrollY = 0;
global.gets = [];
global.button = (root, text, n) => root.all().filter((k) => k.tag === "BUTTON" && k.kids.join("") === text)[n || 0];
global.row = (key) => views.desk.all().find((k) => k.dataset.pick === key);
global.answer = (path, fn) => { c.post = c.post || {}; c.post[path] = fn; };
"""


def _summary(pid: str, kind: str, sentence: str, touches: list[str], **extra) -> dict:
    """A proposal as the desk's column lists it (desk.route_map)."""
    return {"id": pid, "by": WANG, "by_label": "王", "at": "2024-03-01T10:00:00", "kind": kind, "sentence": sentence,
            "state": "pending", "outcomes": [], "files": [], "touches": touches, **extra}


def _proposals(role: str = "pi") -> tuple[list[dict], dict]:
    """p1 abandons #16 (a verdict change), p2 adds a data file (touches no
    attempt), p3 draws #18 源自 #16 (a line): the column's summaries and what
    GET /api/proposals/<id> answers the PI."""
    old, new = _st("pending", "待定", "先看"), _st("abandoned", "已放弃", "方向反了")
    verdict = {"number": "16", "old_status": old, "new_status": new, "result": "t=0", "reason": "残差不平稳", "abandons": True}
    route = {"op": "add", "child": A18, "parent": A16, "child_number": "18", "parent_number": "16", "note": "同一数据"}
    listed = [_summary("p1", "verdict_change", "放弃 16", [A16], verdict=verdict),
              _summary("p2", "files", "加上数据", [], files=[{"path": "data/new.csv", "action": "added", "size": 120, "store": "text"}]),
              _summary("p3", "route_link", "", [A18, A16], route=route)]
    none = {"moved": False, "resuming": False, "attempt": None, "variable": None, "route": None, "files": [], "not_carried": [],
            "conflicts": [], "problems": [], "approvable": True}
    changes = {
        "p1": {**none, "attempt": {"op": "update", "file": "map.md", "number": "16", "old_status": old, "new_status": new,
                                    "abandons": True, "state": "apply", "old_row": "| 16 | 待定 先看 |", "new_row": "| 16 | ☠ 方向反了 |"}},
        "p2": {**none, "files": [{"path": "data/new.csv", "action": "added", "size": 120, "store": "text", "state": "apply"}]},
        "p3": {**none, "route": {**route, "file": ".researchos/mappings.toml", "state": "apply",
                                 "words": "批准后加上连线：#18 源自 #16"}}}
    full = {p["id"]: {**p, "verification": {"state": "verified", "trusted": True}, "uses": [],
                      **({"changes": changes[p["id"]]} if role == "pi" else {})} for p in listed}
    return listed, full


def _team_case(role: str, lang: str = "zh") -> dict:
    case = _case(role, lang)
    listed, full = _proposals(role)
    column = case["desk"]["column"]
    if role == "pi":
        column["waiting"] = listed
    else:
        column["mine"] = [listed[0]]
    case["get"]["/api/share"] = {"team": [{"id": WANG, "name": "王"}]}
    case["get"]["/api/proposals"] = {"role": role, "proposals": listed}
    for pid, page in full.items():
        case["get"][f"/api/proposals/{pid}{FROM}"] = page
    return case


def _run_team(case: dict, steps: str, lang: str | None = None) -> dict:
    """The desk of `case` with the real desk.js, node.js and team.js (a
    stand-in route map recording what it marks), then `steps`."""
    i18n = "" if lang is None else f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    script = ("global.window = {};\n" + i18n + _PANEL_DOM + _RUN + (WEBAPP / "desk.js").read_text(encoding="utf-8")
              + "\n" + (WEBAPP / "node.js").read_text(encoding="utf-8") + _AFTER
              + (WEBAPP / "team.js").read_text(encoding="utf-8") + _TEAM
              + f"(async () => {{ global.c = {json.dumps(case, ensure_ascii=False)}; await openDesk(c);\n"
              " const read = global.apiGet; global.apiGet = (path) => { gets.push(path); return read(path); };\n"
              f"{steps}\n"
              " process.stdout.write(JSON.stringify({ lines: panelLines(), asked, posted, gets, highlighted, mapUpdates,"
              " extra: global.extra || null, subject: state.panelSubject && [state.panelSubject.kind, state.panelSubject.id] })); })();\n")
    return _node(script)


@needs_node
def test_14_3_the_pi_decides_a_proposal_in_the_attempts_panel():
    """The PI clicks #16: under 待办 its panel lists the proposals touching
    it (p1 changes its verdict, p3 draws a line to it; p2 touches no
    attempt), each with 查看. 查看 opens p1 in place, by whose it is: what
    approving does (the row before and after) and the three answers.
    Returned with a reason: the map and the column are read again, the
    panel says what the answer did and p1 is no longer listed."""
    case = _team_case("pi")
    case["after"] = {**case["desk"], "column": {**case["desk"]["column"], "waiting": case["desk"]["column"]["waiting"][1:]}}
    steps = """
      await window.RODesk.openAttempt(%(a16)s);
      global.extra = { before: panelLines() };
      button(panelPart("panel-body"), "查看").click(); await settle(); await settle();
      extra.opened = panelLines();
      answer("/api/proposals/p1/return", () => { c.desk = c.after; return { ok: true }; });
      type(field("为什么退回：王 会看到这句话"), "先补数据");
      button(panelPart("panel-body"), "退回并说明").click();
      for (let i = 0; i < 8; i++) await settle();
    """ % {"a16": json.dumps(A16)}
    out = _run_team(case, steps)
    before, opened, after = out["extra"]["before"], out["extra"]["opened"], out["lines"]
    urgent = before[before.index("node-sec-title: 待办"):]
    for line in ["status-label tone-plain: 判决变更", "desk-what: 放弃 16", "desk-note plain: #16 的判决从「待定」改为「已放弃」，放弃这条路线",
                 "desk-meta: 王 · 2024年3月1日", "status-label tone-plain: 连线", "desk-what: 加上连线：#18 源自 #16"]:
        assert line in urgent, line
    assert urgent.count("btn btn-small: 查看") == 2 and not any("加上数据" in line for line in before)
    assert f"/api/proposals/p1{FROM}" in out["gets"]
    for line in ["node-kind: 判决变更提议", "desk-meta: 王 · 2024年3月1日", "status-label tone-ochre: 提议中",
                 "node-sec-title: 批准以后会怎样", "diff-row old: | 16 | 待定 先看 |", "diff-row new: | 16 | ☠ 方向反了 |",
                 "desk-note: 放弃理由：残差不平稳", "btn btn-primary: 批准并加入", "btn: 退回并说明", "btn: 讨论"]:
        assert line in opened, line
    assert not any(line.startswith("node-name") for line in opened)  # the row names it; the panel's title is #16's
    assert out["posted"] == [["/api/proposals/p1/return", {"from": WANG, "reason": "先补数据"}]]
    assert out["gets"].count("/api/desk") == 1 and out["mapUpdates"] == 2  # the map and the column, read again in place
    assert after[after.index("panel-subtitle: 07-26") + 1] == "notice-status ok: 已退回：王 会看到你的理由。"
    assert "desk-what: 放弃 16" not in after and "desk-what: 加上连线：#18 源自 #16" in after
    assert out["subject"] == ["desk-attempt", A16] and out["asked"] == []


@needs_node
def test_14_4_the_columns_view_opens_a_proposal_in_the_panel_and_marks_its_cards():
    """The PI's 等你批准: 查看 on p2 (a data file, touching no attempt) opens
    it in the panel as its own subject, nothing marked on the map; 查看 on
    p3 (a line) replaces it -- nothing typed, nothing asked -- and marks #18
    and #16, its row picked. 连线提议 says what approving the line does in
    the engine's words. Approved: the map and the column are read again,
    p3 leaves the column and the map, and the panel says what approving
    did over the proposal as it now stands."""
    case = _team_case("pi")
    listed = case["desk"]["column"]["waiting"]
    case["after"] = {**case["desk"], "column": {**case["desk"]["column"], "waiting": listed[:2]}}
    approved = {**case["get"][f"/api/proposals/p3{FROM}"], "state": "approved",
                "outcomes": [{"act": "approved", "by": "li@example.com", "at": "2024-03-02T09:00:00"}]}
    approved.pop("changes")
    steps = """
      button(row("p:p2"), "查看").click(); await settle(); await settle();
      global.extra = { files: panelLines(), subject: [state.panelSubject.kind, state.panelSubject.id], marked: highlighted.slice() };
      button(row("p:p3"), "查看").click(); await settle(); await settle();
      extra.line = panelLines();
      extra.picked = row("p:p3").cls.has("picked");
      answer("/api/proposals/p3/approve", () => { c.desk = c.after; c.get[%(p3)s] = %(approved)s;
        return { ok: true, published: { published: true, seq: 5 } }; });
      button(panelPart("panel-body"), "批准并加入").click();
      for (let i = 0; i < 8; i++) await settle();
      extra.rows = views.desk.all().filter((k) => k.dataset.pick).map((k) => k.dataset.pick);
    """ % {"p3": json.dumps(f"/api/proposals/p3{FROM}"), "approved": json.dumps(approved, ensure_ascii=False)}
    out = _run_team(case, steps)
    extra = out["extra"]
    assert extra["subject"] == ["desk-proposal", "p2"] and extra["marked"] == [[]]
    for line in ["panel-title plain-title: 加上数据", "panel-subtitle: 文件 · 王", "node-kind: 文件提议", "status-label tone-ochre: 提议中",
                 "node-sec-title: 批准以后会怎样", "proposal-h4: 文件", "mono: data/new.csv", "btn btn-primary: 批准并加入"]:
        assert line in extra["files"], line
    for line in ["panel-title plain-title: 加上连线：#18 源自 #16", "panel-subtitle: 连线 · 王", "node-kind: 连线提议",
                 "desk-head: 批准后加上连线：#18 源自 #16", "desk-note: 说明：同一数据", "btn btn-primary: 批准并加入"]:
        assert line in extra["line"], line
    assert "team-line quiet: 这份提议没有带来任何改动。" not in extra["line"]
    assert out["highlighted"][:2] == [[], [A18, A16]] and extra["picked"] and out["asked"] == []
    assert out["posted"] == [["/api/proposals/p3/approve", {"from": WANG}]]
    assert out["gets"].count("/api/desk") == 1 and out["mapUpdates"] == 2
    assert out["highlighted"][-1] == [] and extra["rows"] == ["p:p1", "p:p2"]  # its row gone, nothing stays marked
    lines = out["lines"]
    assert lines[2] == "notice-status ok: 已批准，改动已写入正式版本，并发布为第 5 版：成员同步后就会看到。"
    assert "status-label tone-olive: 已批准" in lines and "node-sec-title: 已写入正式版本的改动" in lines
    assert "team-line: 加上连线：#18 源自 #16" in lines and "btn btn-primary: 批准并加入" not in lines
    assert out["subject"] == ["desk-proposal", "p3"]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_14_3_a_members_own_proposal_waits_under_its_attempt_and_opens_in_place(lang):
    """A member's panel for #16 lists their own waiting proposal (我的提议
    filtered as the column is, through what it touches) with how it stands;
    opened in place by its author it shows what it carries -- no answers to
    give. In English the desk's own words are English (13.4: 待办 "To
    handle", 提议中 "Proposed"); the proposal drawn by team.js is D3's."""
    case = _team_case("member", lang)
    steps = """
      await window.RODesk.openAttempt(%(a16)s);
      global.extra = { before: panelLines() };
      button(panelPart("panel-body"), %(view)s).click(); await settle(); await settle();
    """ % {"a16": json.dumps(A16), "view": json.dumps("View" if lang == "en" else "查看")}
    out = _run_team(case, steps, lang=None if lang == "zh" else lang)
    before = out["extra"]["before"]
    urgent = before[before.index("node-sec-title: " + ("To handle" if lang == "en" else "待办")):]
    w = (lambda zh, en: en if lang == "en" else zh)
    assert urgent == ["node-sec-title: " + w("待办", "To handle"), "status-label tone-plain: " + w("判决变更", "Verdict change"),
                      "desk-what: 放弃 16", "desk-note plain: " + w("#16 的判决从「待定」改为「已放弃」，放弃这条路线",
                                                                "#16: change the verdict from “Pending” to “Abandoned”, abandoning the route"),
                      "desk-meta: " + w("提议中 · 2024年3月1日", "Proposed · 1 Mar 2024"), "btn btn-small: " + w("查看", "View")]
    if lang == "en":  # the member's own sentence aside
        assert [line for line in urgent if CJK.search(re.sub("放弃 16", "", line))] == []
    assert not any(FORBIDDEN.search(line) for line in urgent)
    assert f"/api/proposals/p1{FROM}" in out["gets"] and out["posted"] == []
    lines = out["lines"]
    if lang == "zh":
        for line in ["node-kind: 判决变更提议", "status-label tone-ochre: 提议中", "node-sec-title: 提议带来的改动",
                     "desk-note: 结果：t=0", "desk-note: 理由：残差不平稳"]:
            assert line in lines, line
    assert not any("批准并加入" in line or "退回并说明" in line for line in lines)


@needs_node
def test_12_3_the_proposals_page_is_its_way_back_and_the_same_renderer():
    """提议 keeps working: the inbox names a line proposal 连线 (KIND_WORDS
    knows route_link); one proposal's page is 返回提议列表 and then what the
    panel draws, with its name, read by whose it is; 讨论 is written with
    `from` and the page is drawn again with what the answer did under the
    way back."""
    steps = """
      views.proposals = el("div", "host");
      const page = () => dump(views.proposals, []);
      await ROTeam.loadProposals(views.proposals);
      global.extra = { list: page() };
      ROTeam.openProposal("p1", %(wang)s);
      await ROTeam.loadProposals(views.proposals);
      extra.page = page();
      answer("/api/proposals/p1/discuss", () => ({ ok: true }));
      type(views.proposals.all().find((k) => k.placeholder === "想问或想说的话：提议保持打开"), "数据从哪来？");
      button(views.proposals, "讨论").click();
      for (let i = 0; i < 8; i++) await settle();
      extra.after = page();
    """ % {"wang": json.dumps(WANG)}
    out = _run_team(_team_case("pi"), steps)
    extra = out["extra"]
    for line in ["team-title: 收件箱", "status-label tone-plain: 连线", "desk-link strong: 加上连线：#18 源自 #16",
                 "status-label tone-plain: 判决变更", "desk-link strong: 放弃 16"]:
        assert line in extra["list"], line
    assert f"/api/proposals/p1{FROM}" in out["gets"]
    assert extra["page"][:3] == ["node-link: 返回提议列表", "node-kind: 判决变更提议", "node-name: 放弃 16"]
    for line in ["node-sec-title: 批准以后会怎样", "diff-row new: | 16 | ☠ 方向反了 |", "btn btn-primary: 批准并加入", "btn: 讨论"]:
        assert line in extra["page"], line
    assert out["posted"] == [["/api/proposals/p1/discuss", {"from": WANG, "text": "数据从哪来？"}]]
    assert extra["after"][:3] == ["node-link: 返回提议列表", "team-done: 已写下：提议保持打开。", "node-kind: 判决变更提议"]

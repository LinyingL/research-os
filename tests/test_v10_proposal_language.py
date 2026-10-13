"""Research OS V10, increment D3 (DESIGN.md 14.6, 14.3): a proposal decided
in the desk's panel and a member's draft speak the screen's language --
team.js renderProposalInto (its head and answers, 批准以后会怎样, the files,
the variable versions it uses, the answers to give and what each did) and
composeInto / renderDraft (what it carries, what is not sent, what sending
writes). On an English screen nothing there is Chinese but the
researcher's own text and the shared drive's fixed folder names (13.1);
the engine words what is not carried and how a file uses a variable in the
request's language. In Chinese everything reads as before. Every test
names its scenario."""

from __future__ import annotations

import json
import re
from unittest import mock

import pytest

from researchos import nodepage, signing
from researchos.webapp import node_api

from test_v8_phase2 import _call
from test_v8_phase3 import _serve
from test_v8_phase4 import Drive, _member_new_attempt
from test_v8_u2 import FORBIDDEN
from test_v9_language import CJK, WEBAPP, _i18n_source, _node, needs_node
from test_v10_desk_panel import _THEIRS as _PANEL_THEIRS
from test_v10_desk_panel import A16, _st
from test_v10_desk_proposals import FROM, _run_team, _team_case

#: The researcher's own text in these fixtures (13.1: a verdict cell as written, too), and the
#: shared drive's fixed folder names.
_THEIRS = [*_PANEL_THEIRS, "待定 先看", "☠ 方向反了", "放弃 16", "加上数据", "王", "残差不平稳", "同一数据", "数据从哪来？", "先补数据",
           "已发布/outcomes/", "提议/"]
#: A card whose version in use names a script and an output (nodepage._card_paths' shape).
_CARD = {"steps/16-build.py": [{"card": "topicshift", "name": "TopicShift", "version": 3, "role": "script"}],
         "data/panel.csv": [{"card": "topicshift", "name": "TopicShift", "version": 3, "role": "output"}]}


def _ours(lines: list[str]) -> list[str]:
    """Each line without the researcher's text: what Research OS writes."""
    theirs = "|".join(map(re.escape, sorted(_THEIRS, key=len, reverse=True)))
    return [re.sub(theirs, "", line) for line in lines]


def _chinese(lines: list[str]) -> list[str]:
    return [line for line in _ours(lines) if CJK.search(line)]


def _uses(root, files, lang):
    with mock.patch.object(nodepage, "_card_paths", return_value=_CARD):
        return nodepage.variables_used(root, files, lang)


def _w(lang: str):
    return lambda zh, en: en if lang == "en" else zh


# -- the engine's words --------------------------------------------------------------------------------


def test_14_6_how_a_proposals_file_uses_a_variable_is_worded_in_the_requests_language(tmp_path):
    """12.3's 用到的变量版本: a changed script that is a card's implementation,
    and a new script that reads its output -- said in the request's language,
    Chinese by default."""
    script = tmp_path / "plot.py"
    script.write_text('import pandas as pd\npd.read_csv("data/panel.csv")\n', encoding="utf-8")
    files = [{"path": "steps/16-build.py", "action": "changed"}, {"path": "steps/plot.py", "action": "added", "content": str(script)}]
    assert [u["how"] for u in _uses(tmp_path, files, "zh")] == [
        "steps/16-build.py 是这个变量的实现脚本", "steps/plot.py 读取 data/panel.csv，这是这个变量的输出"]
    assert [u["how"] for u in _uses(tmp_path, files, "en")] == [
        "steps/16-build.py is this variable's implementation script",
        "steps/plot.py reads data/panel.csv, which is this variable's output"]


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Drive:
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    return Drive(tmp_path, monkeypatch)


def test_14_6_what_a_proposal_does_not_carry_is_worded_in_the_requests_language(drive):
    """A member's new attempt #18 while #19 is also new and a large file is
    left unticked: the dry run's `not_carried` carries `words` in the
    request's language (reason_zh stays Chinese, the engine's English stays
    for 「详情」); the PI's GET of the written proposal words it likewise."""
    with drive.member():
        _member_new_attempt(drive)
        (drive.work / "map.md").write_text((drive.work / "map.md").read_text() + "| 19 | 08-03 | Another | a |  | 🕒 |\n")
        body = {"kind": "attempt_new", "sentence": "大面板", "number": "18", "only": ["steps/18-big.py"]}
        zh = node_api.proposal_create_payload(drive.work, body)
        en = node_api.proposal_create_payload(drive.work, body, "en")
        written = node_api.proposal_create_payload(drive.work, {**body, "write": True, "expected": en["fingerprint"]}, "en")

    def words(items: list[dict]) -> dict[str, str]:
        return {n["reason"].split(" ")[0] + " " + n["path"]: n["words"] for n in items}

    assert all(n["words"] == n["reason_zh"] for n in zh["proposal"]["not_carried"])
    assert [n["reason_zh"] for n in en["proposal"]["not_carried"]] == [n["reason_zh"] for n in zh["proposal"]["not_carried"]]
    said = words(en["proposal"]["not_carried"])
    assert said["row map.md"] == "Row 19 of the attempt table (added) is not part of this proposal"
    assert said["not data/big.bin"] == "Not chosen for this proposal"
    assert not any(CJK.search(n["words"]) for n in en["proposal"]["not_carried"])
    with drive.pi():
        with _serve(drive.official) as base:
            status, page = _call(f"{base}/api/proposals/{written['id']}", headers={"X-ResearchOS-Lang": "en"})
    assert status == 200 and words(page["changes"]["not_carried"])["row map.md"] == said["row map.md"]


# -- on the page: the PI's panel and a member's draft, under node ------------------------------------------


def _pi_case() -> dict:
    """The PI's English desk: p1 (abandon #16) as everything 批准以后会怎样
    can say -- an answer so far, the official version moved and an approval
    to resume, a variable version, a data file, a large file changed on both
    sides, a file removed, a change not carried, the variable version its
    script uses, a member's packets and a link removal to publish first --
    the engine's words in English; p2 with contradicting answers; p3 a line."""
    case = _team_case("pi", "en")
    case["get"]["/api/share"]["incoming"] = {"notes": 2, "links_removed": 1, "members": ["wang@example.com"],
                                             "removals": [{"from": "script:steps/a.py", "type": "reads", "to": "dataset:data/b.csv",
                                                           "by": "王"}]}
    p1 = case["get"][f"/api/proposals/p1{FROM}"]
    p1.update(base_seq=4, outcomes=[{"act": "discussed", "by": "wang@example.com", "at": "2024-03-01T11:00:00", "text": "数据从哪来？"}],
              verification={"state": "no_key", "trusted": True, "message": signing.CHECK_MESSAGES["no_key"]},
              uses=_uses("/nowhere", [{"path": "steps/16-build.py", "action": "changed"}], "en"))
    p1["changes"].update(
        moved=True, resuming=True, approvable=False, conflicts=[{"path": "data/big.bin"}],
        variable={"card": "topicshift", "number": "3", "new_card": True, "attested": "unknown",
                  "diff": "--- a/v2.toml\n+++ b/v3.toml\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"},
        files=[{"path": "data/new.csv", "action": "added", "size": 1, "store": "text", "state": "apply", "sha256": "ab" * 32,
                "summary_words": None, "diff": "--- /dev/null\n+++ b/data/new.csv\n@@ -0,0 +1 @@\n+a,b\n"},
               {"path": "data/big.bin", "action": "changed", "size": 2097152, "store": "blob", "content": "blobs/ab/cd",
                "state": "conflict", "sha256": "cd" * 32},
               {"path": "steps/old.py", "action": "removed", "store": "text", "state": "same"}],
        not_carried=node_api.not_carried_words([{"path": "map.md", "reason": "row 19 (new) is not part of this proposal"}], "en"))
    case["get"][f"/api/proposals/p2{FROM}"]["state"] = "conflict"
    case["get"][f"/api/proposals/p3{FROM}"]["changes"]["route"]["words"] = "Approving adds the line: #18 grew out of #16"
    return case


@needs_node
def test_14_6_an_english_pi_decides_a_proposal_in_the_panel_with_no_chinese_but_theirs():
    """English mode: the column's View opens p1 in the panel -- its head,
    answers, 批准以后会怎样 down to the removal list, what is not carried,
    why it cannot be approved yet, the variable versions it uses and the
    three answers -- all English (13.4: Approve, Return with reason). Then
    returned with a reason; p2 says its answers contradict; p3's approval
    refused by the engine with a sentence that has no English shows the
    framing (its own text behind “Details”), then approved: 1,497 (13.5)."""
    steps = """
      button(row("p:p1"), "View").click(); await settle(); await settle();
      global.extra = { p1: panelLines() };
      answer("/api/proposals/p1/return", () => ({ ok: true }));
      type(field("Why it is returned: 王 will see this"), "先补数据");
      button(panelPart("panel-body"), "Return with reason").click();
      for (let i = 0; i < 8; i++) await settle();
      extra.returned = panelLines();
      button(row("p:p2"), "View").click(); await settle(); await settle();
      extra.p2 = panelLines();
      button(row("p:p3"), "View").click(); await settle(); await settle();
      answer("/api/proposals/p3/approve", () => { throw Object.assign(new Error("the official version moved"),
        { state: "not_approvable", engineMessage: "正式版本又变了" }); });
      button(panelPart("panel-body"), "Approve").click();
      for (let i = 0; i < 4; i++) await settle();
      extra.refused = panelLines().filter((l) => l.startsWith("notice-status err"));
      answer("/api/proposals/p3/approve", () => ({ ok: true, published: { published: true, seq: 1497 } }));
      button(panelPart("panel-body"), "Approve").click();
      for (let i = 0; i < 8; i++) await settle();
    """
    out = _run_team(_pi_case(), steps, lang="en")
    extra = out["extra"]
    seen = extra["p1"] + extra["returned"] + extra["p2"] + extra["refused"] + out["lines"]
    assert _chinese(seen) == [] and not any(FORBIDDEN.search(line) or "节点" in line or "画布" in line for line in _ours(seen))
    for line in [
        "panel-subtitle: Verdict change · 王", "node-kind: Verdict change proposal", "desk-meta: 王 · 1 Mar 2024 · Based on official version 4",
        "status-label tone-ochre: Proposed", "status-label tone-ochre: Unverified",
        "node-sub-inline: The members list has no public key for this person: the signature cannot be verified, so only the shared "
        "folder's permissions vouch for it", "node-sec-title: Answers", "desk-head: 王 said", "desk-note: 数据从哪来？",
        "node-sec-title: What approving does",
        "team-line quiet: The official version changed after this proposal; approval is blocked only where both sides changed the same place.",
        "team-line warn: Approving this proposal did not finish last time: approving again completes the rest.",
        "desk-head: Row 16 of the attempt table: the verdict changes from  to ", "desk-note: Result: t=0",
        "desk-note: Reason for abandoning: 残差不平稳", "diff-label: Earlier row", "diff-label: New row",
        "desk-head: New variable card topicshift: definition version 3",
        "desk-note plain: Were the existing output files made by this definition? The member answers: Not sure",
        "proposal-h4: Files", "evidence-role: Added", "desk-meta: 1 byte", "status-label tone-olive: Written on approval",
        f"desk-meta: Fingerprint  (title: {'cd' * 32})", ": Show the change", "evidence-role: Changed", "desk-meta: 2.0 MB",
        "status-label tone-clay: Changed on both sides", "desk-meta: A large file, stored in the shared folder by its content:",
        "evidence-role: Removed", "status-label tone-plain: Already so in the official version",
        "team-line writes: Approving also records an entry in decisions.toml, proposed by 王 and approved by you, then publishes the "
        "official version to the shared folder. Before publishing, it first adds what members synced (2 notes and 1 link removed) to "
        "the official record, with no approval needed.",
        ": The link to be removed (1)", "team-help: Removed by 王", "node-sec-title: Changes not sent with the proposal",
        "team-help: Row 19 of the attempt table (added) is not part of this proposal",
        "node-sec-title: Cannot be approved yet", "desk-break: : Changed on both sides",
        "team-line: Research OS never merges one file for two people. Ask the member to sync to the latest official version and propose "
        "again on it; this one can be returned with a reason.",
        "node-sec-title: Variable versions it uses", "desk-link: TopicShift, version 3",
        "team-help: steps/16-build.py is this variable's implementation script",
        "node-sec-title: Your answer", ": Note with the approval", "btn btn-primary: Approve",
        "team-line warn: Something was changed on both sides or has a problem, so it cannot be approved now (see above).",
        ": Reason for returning (required)", "btn: Return with reason", ": Discuss", "btn: Discuss",
        "team-line quiet: The answer is signed and written into “已发布/outcomes/” (the published answers) in the shared folder; 王 sees "
        "it on their next sync. It is never changed once written."]:
        assert line in extra["p1"], line
    assert "form-textarea:  =  (A note with this approval (optional))" in extra["p1"]
    assert out["posted"][0] == ["/api/proposals/p1/return", {"from": "wang@example.com", "reason": "先补数据"}]
    assert "notice-status ok: Returned: 王 will see your reason." in extra["returned"]
    assert "status-label tone-clay: The approval and return records contradict each other" in extra["p2"]
    assert ("team-line warn: This proposal's answer records contradict each other (it is both approved and returned): check its "
            "answers in 已发布/outcomes/ (the published answers) in the shared folder.") in extra["p2"]
    assert extra["refused"] == ["notice-status err: Could not approve; the official version is unchanged"]
    assert ("notice-status ok: Approved: the change is written into the official version and published as version 1,497; members "
            "see it once they sync.") in out["lines"]


def _draft(lang: str) -> dict:
    """The engine's dry run of the member's verdict change on #16: its row,
    two files that travel (one large), one left out that was in a returned
    proposal, and two changes not carried, worded in `lang`."""
    old, new = _st("pending", "待定", "先看"), _st("abandoned", "已放弃", "方向反了")
    reasons = [{"path": "map.md", "reason": "row 16: changes to description are not part of a verdict change"},
               {"path": ".researchos/canvas.json", "reason": "canvas arrangements do not travel with a proposal"}]
    files = [{"path": "steps/16-build.py", "action": "changed", "size": 512, "store": "text",
              "diff": "--- a/steps/16-build.py\n+++ b/steps/16-build.py\n@@ -1 +1 @@\n-x\n+y\n"},
             {"path": "data/big.bin", "action": "added", "size": 3145728, "store": "blob"}]
    return {"ok": True, "written": False, "fingerprint": "f1",
            "proposal": {"kind": "verdict_change", "sentence": "放弃 16", "file": files,
                         "verdict": {"number": "16", "old_status": old, "new_status": new, "result": "t=2.91", "reason": "残差不平稳"},
                         "not_carried": node_api.not_carried_words(reasons, lang)},
            "preview": {"files": files, "candidates": [
                {**files[0], "chosen": True, "linked": True},
                {**files[1], "chosen": True, "officialness": {"state": "proposed", "label": _w(lang)("提议中", "Proposed")}},
                {"path": "notes/old.md", "action": "removed", "chosen": False, "returned": ["p0"]}]}}


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_14_6_a_members_draft_in_the_panel_reads_in_the_screens_language(lang):
    """A member opens #16: their own waiting p1 opened in place says what
    it carries; then they abandon #16 in the panel and see the engine's dry
    run (team.js renderDraft) -- its row, the files it carries, what is not
    sent, what sending writes -- and send it. In Chinese as before; in
    English nothing but their text and the 提议 folder's name is Chinese."""
    w = _w(lang)
    case = _team_case("member", lang)
    case["post"] = {"/api/attempts/preview": {"file": "map.md", "old_row": "a", "new_row": "b", "abandons": True,
                                              "reason_required": False},
                    "/api/attempts/write": {"ok": True, "file": "map.md"}}
    steps = """
      await window.RODesk.openAttempt(%(a16)s);
      button(panelPart("panel-body"), %(view)s).click(); await settle(); await settle();
      global.extra = { carried: panelLines() };
      answer("/api/proposals", (body) => body.write ? { ok: true, written: true, id: "p7" } : %(draft)s);
      find(%(chip)s).click();
      type(field(%(sentence)s), "放弃 16");
      type(field(%(why)s), "残差不平稳");
      find(%(look)s).click(); await settle(); await settle();
      find(%(write)s).click(); await settle(); await settle(); await settle();
      extra.draft = panelLines();
      find(%(send)s).click(); for (let i = 0; i < 6; i++) await settle();
    """ % {"a16": json.dumps(A16), "view": json.dumps(w("查看", "View")), "draft": json.dumps(_draft(lang), ensure_ascii=False),
           "chip": json.dumps(w("已放弃 方向反了", "Abandoned 方向反了")),
           "sentence": json.dumps(w("用你自己的话说明这份提议：PI 会先看到这句话",
                                    "Say what this proposal does in your own words: the PI reads this first")),
           "why": json.dumps(w("为什么放弃这条路线", "Why this route is abandoned")),
           "look": json.dumps(w("预览这一行", "Preview this row")),
           "write": json.dumps(w("写入工作副本，查看提议", "Write the working copy and see the proposal")),
           "send": json.dumps(w("发送提议", "Send proposal"))}
    out = _run_team(case, steps, lang=None if lang == "zh" else lang)
    carried, draft, after = out["extra"]["carried"], out["extra"]["draft"], out["lines"]
    for line in [w("node-sec-title: 提议带来的改动", "node-sec-title: Changes the proposal brings"),
                 w("desk-head: 第 16 行的判决  改为 ", "desk-head: Row 16: the verdict changes from  to "),
                 w("desk-note: 结果：t=0", "desk-note: Result: t=0"), w("desk-note: 理由：残差不平稳", "desk-note: Reason: 残差不平稳")]:
        assert line in carried, line
    for line in [w("node-sec-title: 将要发送的提议", "node-sec-title: The proposal to send"), "proposal-sentence: 放弃 16",
                 w("desk-head: 第 16 行的判决  改为 ", "desk-head: Row 16: the verdict changes from  to "),
                 w("desk-note: 结果：t=2.91", "desk-note: Result: t=2.91"),
                 w("proposal-h4: 带上的文件", "proposal-h4: Files it carries"),
                 w("team-line quiet: 改判只带尝试表里的这一行；要一起发送文件，在下面勾选。",
                   "team-line quiet: A verdict change carries only its row of the attempt table; to send files with it, tick them below."),
                 w("evidence-role: 改动", "evidence-role: Changed"), w("desk-meta: 512 字节", "desk-meta: 512 bytes"),
                 w("desk-meta: 与这条尝试关联", "desk-meta: Linked to this attempt"),
                 w("desk-meta: 3.0 MB，大文件", "desk-meta: 3.0 MB, a large file"), w("own-mark: 提议中", "own-mark: Proposed"),
                 w("evidence-role: 删除", "evidence-role: Removed"),
                 w("desk-break: 这个文件曾在被退回的提议里：先看看退回的理由，再决定是否带上。",
                   "desk-break: This file was in a returned proposal: read why it was returned before deciding whether to carry it."),
                 w(": 查看改动", ": Show the change"),
                 w("btn btn-small: 按勾选的文件重新查看", "btn btn-small: Review again with the ticked files"),
                 w("proposal-h4: 不会发送的改动", "proposal-h4: Changes that will not be sent"),
                 w("team-help: 第 16 行其它格子的改动不属于改判，不会发送",
                   "team-help: Changes to the other cells of row 16 are not part of a verdict change and are not sent"),
                 w("team-help: 关系图上的摆放不随提议发送：每个人保留自己的",
                   "team-help: Arrangements on the relation map do not travel with a proposal: everyone keeps their own"),
                 w("team-line writes: 发送会在公盘的「提议/li@example.com/」里写入一份新的提议，带上 2 个文件（其中 1 个大文件按内容只存一份）；"
                   "写成以后不再改动。你的工作副本不会改变。",
                   "team-line writes: Sending writes a new proposal into “提议/li@example.com/” (your proposals folder) in the shared "
                   "folder, carrying 2 files (1 of them a large file, stored once by its content). It is never changed once written, "
                   "and your working copy does not change."),
                 w("btn btn-primary: 发送提议", "btn btn-primary: Send proposal")]:
        assert line in draft, line
    sent = w("notice-status ok: 已发送：PI 会在收件箱里看到它。在 PI 批准之前，它不是正式记录。",
             "notice-status ok: Sent: the PI will see it in the inbox. Until the PI approves it, it is not part of the official record.")
    assert sent in after and w("btn btn-primary: 已发送", "btn btn-primary: Sent") in after
    assert out["posted"][-2][1] == {"kind": "verdict_change", "sentence": "放弃 16", "number": "16", "reason": "残差不平稳"}
    assert out["posted"][-1][1] == {**out["posted"][-2][1], "write": True, "expected": "f1"}
    if lang == "en":
        assert _chinese(carried + draft + after) == []
    assert not any(FORBIDDEN.search(line) for line in _ours(carried + draft + after))


@needs_node
def test_14_6_a_proposals_pure_words_in_english():
    """team.js's words for a proposal on an English screen: sizes with a
    singular (13.5), every state, each signature sentence the engine sends
    (signing.CHECK_MESSAGES) by its Chinese, and a publish that did not
    happen -- an engine reason with English shown in English."""
    checks = {state: {"state": state, "trusted": state != "bad", "message": message}
              for state, message in signing.CHECK_MESSAGES.items()}
    script = ("global.window = {};\nwindow.ROI18N_SAVED = \"en\";\n" + _i18n_source()
              + "\nglobal.document = {getElementById(){return null}};\n" + (WEBAPP / "team.js").read_text(encoding="utf-8")
              + "\nconst T = window.ROTeam;\nprocess.stdout.write(JSON.stringify({"
              + "size1: T._sizeWords(1), size: T._sizeWords(2048), unpublished: T._approveSentence("
              + json.dumps({"published": {"published": False, "message": "发布没有完成"}}, ensure_ascii=False) + "),"
              + "states: ['pending', 'conflict', 'approved', 'returned', 'superseded'].map((s) => T._stateLabel({ state: s }).label),"
              + f"checks: Object.values({json.dumps(checks, ensure_ascii=False)}).map((v) => T._signatureWords(v).reason),"
              + "unsigned: T._signatureWords(null) }));\n")
    out = _node(script)
    assert (out["size1"], out["size"]) == ("1 byte", "2.0 KB")
    assert out["states"] == ["Proposed", "The approval and return records contradict each other", "Approved", "Returned",
                             "Replaced by a later proposal"]
    assert not any(CJK.search(reason) for reason in out["checks"]) and len(out["checks"]) == len(signing.CHECK_MESSAGES)
    assert out["unsigned"] == {"label": "Unverified", "tone": "ochre", "reason": "This proposal carries no signature to check"}
    assert out["unpublished"] == ("Approved: the change is written into the official version, but it was not published to the "
                                  "shared folder this time: Publishing did not finish. You can publish again in “Members”.")

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
from test_v10_desk_proposals import FROM, WANG, _run_team, _team_case

#: The researcher's own text in these fixtures (13.1: a verdict cell as written, too -- 「☠ 待定 先看」 is
#: 「待定 先看」 with their ☠ put before it by the 已放弃 chip), and the shared drive's fixed folder names.
_THEIRS = [*_PANEL_THEIRS, "待定 先看", "☠ 方向反了", "☠ 待定 先看", "放弃 16", "加上数据", "王", "残差不平稳", "同一数据", "数据从哪来？", "先补数据",
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
    assert said["row map.md"] == "Attempt #19 (added) is not part of this proposal"
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
        "panel-subtitle: Research assessment change · 王", "node-kind: Research assessment change proposal", "desk-meta: 王 · 1 Mar 2024 · Based on official version 4",
        "status-label tone-ochre: Proposed", "status-label tone-ochre: Unverified",
        "node-sub-inline: The members list has no public key for this person: the signature cannot be verified, so only the shared "
        "folder's permissions vouch for it", "node-sec-title: Answers", "desk-head: 王 said", "desk-note: 数据从哪来？",
        "node-sec-title: What approving does",
        "team-line quiet: The official version changed after this proposal; approval is blocked only where both sides changed the same place.",
        "team-line warn: Approving this proposal did not finish last time: approving again completes the rest.",
        "desk-head: Attempt #16: research assessment changes from  to ", "desk-note: Result: t=0",
        "desk-note: Reason for abandoning: 残差不平稳", "diff-label: Earlier row", "diff-label: New row",
        "desk-head: New variable card topicshift: definition version 3",
        "desk-note plain: Were the existing output files made by this definition? The member answers: Not sure",
        "proposal-h4: Files", "evidence-role: Added", "desk-meta: 1 byte", "status-label tone-olive: Written on approval",
        f"desk-meta: Fingerprint  (title: {'cd' * 32})", ": Show the change", "evidence-role: Changed", "desk-meta: 2.0 MB",
        "status-label tone-clay: Changed on both sides", "desk-meta: A large file, stored in the shared folder by its content:",
        "evidence-role: Removed", "status-label tone-plain: Already so in the official version",
        "team-line writes: Approval publishes to the shared folder, including members’ synced changes (2 comments and 1 link removed).",
        ": The link to be removed (1)", "team-help: Removed by 王", "node-sec-title: Changes not sent with the proposal",
        "team-help: Attempt #19 (added) is not part of this proposal",
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
      find(%(chip)s, "BUTTON").click();
      type(field(%(sentence)s), "放弃 16");
      type(field(%(why)s), "残差不平稳");
      find(%(look)s).click(); await settle(); await settle();
      find(%(write)s).click(); await settle(); await settle(); await settle();
      extra.draft = panelLines();
      find(%(send)s).click(); for (let i = 0; i < 6; i++) await settle();
    """ % {"a16": json.dumps(A16), "view": json.dumps(w("查看", "View")), "draft": json.dumps(_draft(lang), ensure_ascii=False),
           "chip": json.dumps(w("已放弃", "Abandoned")),
           "sentence": json.dumps(w("用你自己的话说明这份提议：PI 会先看到这句话",
                                    "Say what this proposal does in your own words: the PI reads this first")),
           "why": json.dumps(w("为什么放弃这条路线", "Why this route is abandoned")),
           "look": json.dumps(w("预览这一行", "Preview this row")),
           "write": json.dumps(w("保存后查看提议", "Save and review proposal")),
           "send": json.dumps(w("发送提议", "Send proposal"))}
    out = _run_team(case, steps, lang=None if lang == "zh" else lang)
    carried, draft, after = out["extra"]["carried"], out["extra"]["draft"], out["lines"]
    for line in [w("node-sec-title: 提议带来的改动", "node-sec-title: Changes the proposal brings"),
                 w("desk-head: 尝试 #16 的研究判断  改为 ", "desk-head: Attempt #16: research assessment changes from  to "),
                 w("desk-note: 结果：t=0", "desk-note: Result: t=0"), w("desk-note: 理由：残差不平稳", "desk-note: Reason: 残差不平稳")]:
        assert line in carried, line
    for line in [w("node-sec-title: 将要发送的提议", "node-sec-title: The proposal to send"), "proposal-sentence: 放弃 16",
                 w("desk-head: 尝试 #16 的研究判断  改为 ", "desk-head: Attempt #16: research assessment changes from  to "),
                 w("desk-note: 结果：t=2.91", "desk-note: Result: t=2.91"),
                 w("proposal-h4: 带上的文件", "proposal-h4: Files it carries"),
                 w("team-line quiet: 修改研究判断只带尝试表里的这一行；要一起发送文件，在下面勾选。",
                   "team-line quiet: A research assessment change carries only its row of the attempt table; to send files with it, tick them below."),
                 w("evidence-role: 改动", "evidence-role: Changed"), w("desk-meta: 512 字节", "desk-meta: 512 bytes"),
                 w("desk-meta: 与这条尝试关联", "desk-meta: Linked to this attempt"),
                 w("desk-meta: 3.0 MB，大文件", "desk-meta: 3.0 MB, a large file"), w("own-mark: 提议中", "own-mark: Proposed"),
                 w("evidence-role: 删除", "evidence-role: Removed"),
                 w("desk-break: 这个文件曾在被退回的提议里：先看看退回的理由，再决定是否带上。",
                   "desk-break: This file was in a returned proposal: read why it was returned before deciding whether to carry it."),
                 w(": 查看改动", ": Show the change"),
                 w("btn btn-small: 按勾选的文件重新查看", "btn btn-small: Review again with the ticked files"),
                 w("proposal-h4: 不会发送的改动", "proposal-h4: Changes that will not be sent"),
                 w("team-help: 尝试 #16 的其他字段修改不属于修改研究判断，不会发送",
                   "team-help: Changes to the other fields of attempt #16 are not part of a research assessment change and are not sent"),
                 w("team-help: 关系图上的摆放不随提议发送：每个人保留自己的",
                   "team-help: Arrangements on the relationship map do not travel with a proposal: everyone keeps their own"),
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


# -- V11 (15.1, 15.3): a proposed Stata do-file is read for the variable versions it uses ----------------

_PANEL_CARD = {"data/panel.dta": [{"card": "panel", "name": "Panel", "version": 2, "role": "input"}]}


def _do_uses(root, rel: str, text: str, tmp_path, lang: str) -> list[str]:
    copy = tmp_path / "proposal-copy" / rel
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(text, encoding="utf-8")
    with mock.patch.object(nodepage, "_card_paths", return_value=_PANEL_CARD):
        return [u["how"] for u in nodepage.variables_used(root, [{"path": rel, "action": "added", "content": str(copy)}], lang)]


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_15_a_proposed_do_file_that_reads_a_variables_input_is_listed(tmp_path, lang):
    """12.3's 用到的变量版本 for a do-file (15.1: .do is a script in every
    classifier): `use "data/panel.dta"`, `use data/panel` (the default
    extension) and a path folded from a local are listed, as for Python."""
    root = tmp_path / "p"
    root.mkdir()
    said = {"zh": "{rel} 读取 data/panel.dta，这是这个变量的输入数据",
            "en": "{rel} reads data/panel.dta, which is this variable's input data"}[lang]
    for rel, text in (("clean.do", 'use "data/panel.dta", clear\n'), ("bare.do", "use data/panel\n"),
                      ("local.do", 'local d "data"\nuse "`d\'/panel"\n')):
        assert _do_uses(root, rel, text, tmp_path, lang) == [said.format(rel=rel)], rel
    assert not CJK.search(json.dumps(_do_uses(root, "clean.do", 'use "data/panel"\n', tmp_path, "en")))


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_15_a_proposed_do_file_on_a_confirmed_folder_is_listed_and_unconfirmed_it_is_not(tmp_path, lang):
    """15.3: `$Root/data/panel.dta` names the variable's input only once a
    person confirmed $Root stands for the project folder; until then its
    path does not resolve and nothing is listed."""
    from researchos import people
    from researchos import project as project_identity
    from researchos.records import stata as stata_record

    people.write_me("李", "li@example.com")
    root = tmp_path / "p"
    (root / "analysis").mkdir(parents=True)
    project_identity.init_project(root)
    text = 'use "$Root/data/panel.dta"\n'
    assert _do_uses(root, "analysis/clean.do", text, tmp_path, lang) == []
    stata_record.answer(root, "Root", ".", via="app")
    said = {"zh": "analysis/clean.do 读取 data/panel.dta，这是这个变量的输入数据",
            "en": "analysis/clean.do reads data/panel.dta, which is this variable's input data"}[lang]
    assert _do_uses(root, "analysis/clean.do", text, tmp_path, lang) == [said]
    stata_record.stata_path(root).write_text("[[global]\nbroken\n", encoding="utf-8")  # cannot be trusted now
    assert _do_uses(root, "analysis/clean.do", text, tmp_path, lang) == []


# -- 18.2 text rules: the sentences team.js joined from translated and bare fragments, and the composer -------


def _abandoned_routes() -> dict:
    """The routes page's abandoned group: three routes whose abandonment froze 4, 1 and 0 files."""
    from test_v10_desk_panel import _st

    def item(number: int, count: int) -> dict:
        return {"id": f"attempt:map.md#{number}", "number": str(number), "date": "07-08", "title": f"路线 {number}", "result": "t=0.1",
                "reason": "无信号", "verdict_status": _st("abandoned", "已放弃", "方向反了"),
                "decision": {"act": "abandoned", "at": "2024-03-01T10:00:00", "evidence_count": count}}

    return {"groups": [{"status": "abandoned", "label": "已放弃", "items": [item(17, 4), item(18, 1), item(19, 0)]}],
            "decisions_problem": None}


_ROUTES_AND_COMPOSER = """
  global.state.attemptsConfig = true; global.clearProjectState = () => {};
  views.list = el("div", "host");
  await window.ROTeam.loadRoutes(views.list);
  global.extra = { since: views.list.all().filter((k) => k.cls.has("route-src")).map((k) => k.textContent),
                   moves: views.list.all().filter((k) => k.cls.has("desk-what")).map((k) => k.textContent),
                   head: dump(views.list, []).slice(0, 2) };
  views.proposals = el("div", "host");
  await window.ROTeam.loadProposals(views.proposals);
  const composer = (host) => { const lines = dump(host, []); return lines.slice(lines.findIndex((l) => l.startsWith("team-title: ") && %(submit)s.includes(l.slice(12)))); };
  extra.composer = composer(views.proposals);
  const pick = (kind) => { const r = views.proposals.all().find((k) => k.tag === "INPUT" && k.value === kind); r.checked = true; (r.on.change || []).forEach((f) => f()); };
  const extras = () => dump(views.proposals.all().find((k) => k.cls.has("compose-extra")), []);
  pick("verdict_change"); extra.verdict = extras();
  pick("variable_version"); extra.variable = extras();
  extra.options = views.proposals.all().find((k) => k.tag === "SELECT").all().map((o) => o.textContent);
  const look = views.proposals.all().find((k) => k.tag === "BUTTON" && k.cls.has("btn-primary"));
  const notice = () => dump(views.proposals.all().find((k) => k.cls.has("notice-status")), []);
  look.click(); await settle(); extra.noSentence = notice();
  views.proposals.all().find((k) => k.tag === "TEXTAREA").value = "a sentence";
  look.click(); await settle(); extra.noAnswer = notice();
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_18_2_an_abandoned_route_and_a_proposed_change_are_one_sentence_each_and_the_composer_is_translated(lang):
    """18.2 text rules, run under node in both languages: 放弃于 {when}，… is one template for each ending (the
    date lower case inside an English line, the count a plural), 研究判断 {old} 改为 {new} is one with its two
    verdicts put in, and 提交提议 -- the kinds, the fields beside 源自（可选）, their help, their refusals --
    has no Chinese left on an English screen. In Chinese the page reads as it did."""
    w = _w(lang)
    case = _team_case("member", lang)
    case["get"]["/api/routes"] = _abandoned_routes()
    submit = json.dumps([w("提交提议", "Submit a proposal")], ensure_ascii=False)
    out = _run_team(case, _ROUTES_AND_COMPOSER % {"submit": submit}, lang=None if lang == "zh" else lang)["extra"]
    assert out["since"] == [
        w("放弃于 2024年3月1日，记录了 4 个文件的状态", "Abandoned 1 Mar 2024; recorded the state of 4 files"),
        w("放弃于 2024年3月1日，记录了 1 个文件的状态", "Abandoned 1 Mar 2024; recorded the state of 1 file"),
        w("放弃于 2024年3月1日，当时这条路线没有关联的文件", "Abandoned 1 Mar 2024; the route had no linked files then")]
    assert out["moves"] == [w("研究判断 待定先看 改为 已放弃方向反了", "Research assessment changes from Pending先看 to Abandoned方向反了")]
    assert out["head"][0] == "route-group-title serif: " + w("我的提议", "My proposals")
    assert out["head"][1] == "team-line quiet: " + w(
        "你提出的路线和修改研究判断：PI 批准以后才进入正式版本。",
        "The routes and research assessment changes you proposed: they enter the official version once the PI approves.")
    composer = out["composer"]
    for line in [w("team-title: 提交提议", "team-title: Submit a proposal"),
                 "compose-kind-name: " + w("新的尝试", "New attempt"), "compose-kind-name: " + w("修改研究判断", "Change a research assessment"),
                 "compose-kind-name: " + w("变量定义", "Variable definition"), "compose-kind-name: " + w("只有文件", "Files only"),
                 "team-help: " + w("只有尝试表里这一行的研究判断和结果", "Only this row of the attempt table: its research assessment and result"),
                 ": " + w("一句话说明", "One sentence"), ": " + w("尝试的编号", "Attempt number"),
                 ": " + w("源自（可选）", "Grew out of (optional)"),
                 "btn btn-primary: " + w("查看将要发送的内容", "Review what will be sent")]:
        assert line in composer, line
    assert out["verdict"] == [
        ": " + w("尝试的编号", "Attempt number"),
        "form-input form-mono:  =  (" + w("不填：只有一行时自动找出", "Leave empty: found automatically when only one row changed") + ")",
        "team-help: " + w("工作副本里改了不止一行时，填上要提议的那一行", "When more than one row changed in your working copy, enter the row to propose"),
        ": " + w("结果", "Result"),
        "form-textarea:  =  (" + w("不填：用尝试表里这一行现在的结果", "Leave empty: uses this row’s current result in the attempt table") + ")",
        ": " + w("理由", "Reason"),
        "form-textarea:  =  (" + w("放弃一条路线时必须写明", "Required when abandoning a route") + ")",
        "team-help: " + w("放弃（研究判断标为已放弃）时，结果和理由都必须填写",
                          "When abandoning (research assessment set to Abandoned), both the result and the reason are required")]
    assert out["variable"][0] == ": " + w("变量卡", "Variable card")
    assert out["options"] == w(["请选择", "是", "否", "不确定"], ["Choose", "Yes", "No", "Not sure"])
    assert out["noSentence"] == ["notice-status err: " + w("请用一句话说明这份提议", "Say in one sentence what this proposal does")]
    assert out["noAnswer"] == ["notice-status err: " + w("请回答：现有的输出文件是否按这个定义生成",
                                                          "Answer first: were the existing output files made with this definition?")]
    if lang == "en":
        everything = composer + out["verdict"] + out["variable"] + out["options"] + out["noSentence"] + out["noAnswer"] + out["since"]
        assert [line for line in everything if CJK.search(line)] == []
    assert not any(FORBIDDEN.search(line) for line in composer + out["verdict"] + out["since"] + out["moves"])


# -- 18.3: 研究路线's list half and 「当时的依据」 on an English screen ----------------------------------------------

#: The researcher's own text in the fixtures below (13.1): titles, a [verdict_labels] word, results, reasons, a PI's note.
_ROUTE_THEIRS = ["主线", "待看", "先看", "有反例", "事件轴", "方向反了", "换变量", "旧路线", "否决的", "封存的", "成立的", "成立", "无标记",
                 "随手写的", "无信号", "样本太小", "手写的理由", "先补稳健性", "死路", "放弃 16"]
_EVIDENCE_OF = "attempt:map.md#17"
_NO_BASIS = "attempt:map.md#20"


def _list_routes(abandoned: str = "已放弃") -> dict:
    """/api/routes: every group heading (the six default statuses, a word of the researcher's own, 其它); the abandoned
    group holds a route abandoned directly, one an approval abandoned (a PI answered), one the table alone records and
    one with no title and no result; and the decisions.toml warning. `abandoned` is the word its group goes by."""

    def item(number: int, title: str, status: str, label: str, words: str = "", **extra) -> dict:
        return {"id": f"attempt:map.md#{number}", "number": str(number), "date": "07-08", "title": title, "result": "t=0.1",
                "reason": None, "reason_from": None, "verdict_status": _st(status, label, words), **extra}

    def group(status: str, label: str, *items: dict) -> dict:
        return {"status": status, "label": label, "items": list(items)}

    li = {"id": "li@example.com", "label": "Li"}
    direct = {"id": "d1", "act": "abandoned", "at": "2024-03-01T10:00:00", "proposal": None, "by": li, "approved_by": None, "evidence_count": 2}
    approved = {"id": "d2", "act": "abandoned", "at": "2024-03-02T10:00:00", "proposal": "p-1", "by": {"id": WANG, "label": "Wang"},
                "approved_by": li, "evidence_count": 0}
    return {"groups": [
        group("current", "现行", item(14, "主线", "current", "现行")),
        group("pending", "待定", item(15, "待看", "pending", "待定", "先看")),
        group("adverse", "不利证据", item(16, "有反例", "adverse", "不利证据")),
        group("abandoned", abandoned,
              item(17, "事件轴", "abandoned", abandoned, "方向反了", reason="无信号", reason_from="decision", decision=direct),
              item(18, "换变量", "abandoned", abandoned, reason="样本太小", reason_from="decision", decision=approved),
              item(19, "旧路线", "abandoned", abandoned, reason="手写的理由", reason_from="table"),
              item(20, "", "abandoned", abandoned, result="")),
        group("rejected", "被否", item(21, "否决的", "rejected", "被否")),
        group("archived", "归档", item(22, "封存的", "archived", "归档")),
        group("labelled", "成立", item(23, "成立的", "labelled", "成立")),
        group("other", "其它", item(24, "无标记", "other", "其它", "随手写的"))], "decisions_problem": "decisions.toml: boom"}


def _kept_basis() -> dict:
    """/api/evidence for the route above: a direct abandonment whose files show every role, state and standing now (the
    engine's own Chinese labels beside the codes, as it sends them), and an approved one that has left decisions.toml."""

    def file(path: str, role: str, state: str, now: str, labels: tuple[str, str, str], **extra) -> dict:
        return {"path": path, "role": role, "state": state, "sha256": "ab" * 32, "size": 1536, "version": "3", "node": f"script:{path}",
                "now": now, "viewable": False, "role_label": labels[0], "state_label": labels[1], "now_label": labels[2], **extra}

    li, wang = {"id": "li@example.com", "label": "Li"}, {"id": WANG, "label": "Wang"}
    files = [file("steps/a.py", "script", "kept", "same", ("步骤脚本", "内容已保存", "现在仍是这一版"), viewable=True),
             file("steps/b.py", "script", "kept", "changed", ("步骤脚本", "内容已保存", "之后改过"), viewable=True),
             file("steps/c.py", "script", "kept", "changed", ("步骤脚本", "内容已保存", "之后改过"), viewable=True),
             file("out/d.csv", "output", "fingerprint", "missing", ("脚本写出的文件", "只记下了指纹", "现在已不在项目里"), sha256=None, size=20),
             file("notes/e.md", "file", "missing", "unknown", ("关联的文件", "当时文件已不存在", ""), size=None),
             file("data/f.csv", "file", "dataless", "unknown", ("关联的文件", "当时文件只在云端", "")),
             file("data/g.csv", "file", "unreadable", "unknown", ("关联的文件", "当时文件无法读取", ""))]
    return {"id": _EVIDENCE_OF, "name": "事件轴", "decisions": [
        {"id": "d1", "act": "abandoned", "at": "2024-03-01T10:00:00", "proposal": None, "by": li, "approved_by": None, "result": "t=0.1",
         "reason": "无信号", "note": None, "verdict_status": _st("abandoned", "已放弃", "方向反了"), "files": files},
        {"id": "d2", "act": "abandoned", "at": "2024-03-02T10:00:00", "proposal": "p-1", "by": wang, "approved_by": li, "result": "t=0.2",
         "reason": "样本太小", "note": "先补稳健性", "missing_from_file": True, "files": []},
        {"id": "d3", "act": "approved", "at": "2024-03-03T10:00:00", "proposal": "p-2", "by": wang, "approved_by": li, "files": files[:1]}]}


_ROUTES_LIST_STEPS = """
  Object.assign(global, { panelTitle: panelPart("panel-title"), panelSubtitle: panelPart("panel-subtitle"), panelBody: panelPart("panel-body") });
  global.state.attemptsConfig = true; global.clearProjectState = () => {};
  views.list = el("div", "host");
  await window.ROTeam.loadRoutes(views.list);
  global.extra = { list: dump(views.list, []) };
  if (%(more)s) {
    window.ROTeam.showAbandoned();
    await window.ROTeam.loadRoutes(views.list);
    extra.filtered = dump(views.list, []);
    await window.ROTeam.openEvidence(%(basis)s, { number: "17", title: "事件轴" });
    extra.evidence = panelLines();
    for (const b of panelPart("panel-body").all().filter((k) => k.tag === "BUTTON" && (k.cls.has("btn") || k.cls.has("ws-text-link")))) { b.click(); await settle(); await settle(); }
    extra.opened = panelLines();
    await window.ROTeam.openEvidence("attempt:map.md#99", null);
    extra.unread = panelLines();
    await window.ROTeam.openEvidence(%(none)s, null);
    extra.bare = panelLines();
  }
"""


def _routes_list(role: str, routes: dict, lang: str, more: bool = True) -> dict:
    """The list half of 研究路线 (and, with `more`, its abandoned filter and 「当时的依据」 with each file's content opened)
    drawn by the real team.js for `role` in `lang`, as lines of "class: words"."""
    from urllib.parse import quote

    case = _team_case(role, lang)
    case["get"]["/api/routes"] = routes
    case["get"]["/api/evidence?node=" + quote(_EVIDENCE_OF, safe="")] = _kept_basis()
    case["get"]["/api/evidence?node=" + quote(_NO_BASIS, safe="")] = {"id": _NO_BASIS, "name": "x", "decisions": []}
    node = quote("script:steps/", safe="")
    sha = "ab" * 32
    case["get"][f"/api/history/version?node={node}a.py&sha256={sha}"] = {"text": "alpha = 1\n", "at": "2024-03-01T09:00:00", "truncated": True}
    case["get"][f"/api/history/version?node={node}b.py&sha256={sha}"] = {"text": None, "reason_code": "not_kept", "reason": "这一版只记下了指纹和大小，没有保存内容"}
    case["get"][f"/api/history/version?node={node}c.py&sha256={sha}"] = {"text": None, "reason_code": "not_text", "reason": "这一版不是文本，不在这里显示"}
    steps = _ROUTES_LIST_STEPS % {"more": "true" if more else "false", "basis": json.dumps(_EVIDENCE_OF), "none": json.dumps(_NO_BASIS)}
    return _run_team(case, steps, lang=None if lang == "zh" else lang)["extra"]


def _headings(lines: list[str]) -> list[str]:
    return [line.split(": ", 1)[1] for line in lines if line.startswith("route-group-title: ")]


def _theirs_removed(lines: list[str]) -> list[str]:
    """What Research OS wrote on each line that still holds Chinese, the researcher's text taken out."""
    theirs = "|".join(map(re.escape, sorted(_ROUTE_THEIRS, key=len, reverse=True)))
    return [stripped for stripped in (re.sub(theirs, "", line) for line in lines) if CJK.search(stripped)]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_18_3_the_routes_list_and_the_basis_of_an_abandonment_read_in_the_screens_language(lang):
    """18.3 / 13.7: 研究路线's 列表 half -- the group headings (the desk's verdict word of each group's status code), each
    abandoned route's result and reason (with who abandoned it, or who proposed and who approved), the filter bar, the
    note for someone outside the team, and the decisions.toml warning -- and 「当时的依据」, whose files are worded from
    their role, state and standing-now codes (the engine's Chinese labels beside them are not shown), draw with no Chinese
    on an English screen but the researcher's own text and the word they named; in Chinese they read as they did."""
    w = _w(lang)
    out = _routes_list("none", _list_routes(), lang)
    lines = out["list"]
    assert _headings(lines) == w(["现行", "待定", "不利证据", "已放弃", "被否", "归档", "成立", "其它"],
                                 ["Current", "Pending", "Adverse evidence", "Abandoned", "Rejected", "Archived", "成立", "Other"])
    assert _headings(out["filtered"]) == w(["已放弃"], ["Abandoned"])
    for line in [
        "desk-alone: " + w("你还不是这个项目的成员：请 PI 把你加入团队，然后同步",
                           "You are not a member of this project yet: ask the PI to add you to the team, then sync"),
        "team-line warn: " + w("批准记录（decisions.toml）现在无法读取：放弃理由只显示尝试表里写着的",
                               "The approval records (decisions.toml) cannot be read right now: abandonment reasons are "
                               "shown only as the attempt table has them"),
        "status-label tone-gray: " + w("已放弃", "Abandoned"), "status-label tone-olive: " + w("现行", "Current"),
        "route-fact: " + w("结果：t=0.1", "Result: t=0.1"), "route-fact: " + w("理由：无信号", "Reason: 无信号"),
        "route-src: " + w("由 Li 放弃", "Abandoned by Li"),
        "route-src: " + w("放弃于 2024年3月1日，记录了 2 个文件的状态", "Abandoned 1 Mar 2024; recorded the state of 2 files"),
        "route-src: " + w("提议人 Wang，批准人 Li", "Proposed by Wang, approved by Li"),
        "route-src: " + w("放弃于 2024年3月2日，当时这条路线没有关联的文件", "Abandoned 2 Mar 2024; the route had no linked files then"),
        "route-src: " + w("写在尝试表里", "Written in the attempt table"),
        "route-title: " + w("（无描述）", "(no description)"), "route-fact: " + w("结果：（没有写）", "Result: (nothing written)"),
        "ws-text-link: " + w("当时的依据", "The basis then"),
    ]:
        assert line in lines, line
    for line in [": " + w("只显示已放弃的路线，每条附当时的结果和理由。",
                           "Only the abandoned routes are shown, each with its result and reason at the time."),
                 "btn btn-small: " + w("显示全部路线", "Show all routes")]:
        assert line in out["filtered"] and line not in lines, line
    evidence = out["evidence"]
    assert evidence[:2] == ["panel-title: " + w("当时的依据", "The basis then") + " (title: 事件轴)", "panel-subtitle: #17 事件轴"]
    for line in [
        "desk-what strong: " + w("放弃这条路线", "Route abandoned"), "desk-what strong: " + w("批准", "Approved"),
        "desk-meta: " + w("2024年3月1日 · 由 Li 放弃", "1 Mar 2024 · Abandoned by Li"),
        "desk-meta: " + w("2024年3月2日 · 提议人 Wang · 批准人 Li", "2 Mar 2024 · Proposed by Wang · Approved by Li"),
        "desk-note: " + w("结果：t=0.1", "Result: t=0.1"), "desk-note: " + w("理由：样本太小", "Reason: 样本太小"),
        "desk-note: " + w("PI 的附言：先补稳健性", "PI’s note: 先补稳健性"),
        "team-line warn: " + w("这条记录已经不在 decisions.toml 里了（这台电脑记得它）。",
                               "This record is no longer in decisions.toml (this Mac remembers it)."),
        "panel-h3: " + w("放弃时的文件状态", "File state when abandoned"), "panel-h3: " + w("批准时的文件状态", "File state at approval"),
        "node-empty: " + w("当时没有找到属于这条路线的文件。", "No files belonging to this route were found then."),
        "evidence-role: " + w("步骤脚本", "Step script"), "evidence-role: " + w("脚本写出的文件", "File written by the script"),
        "evidence-role: " + w("关联的文件", "Linked file"),
        "desk-meta: " + w("内容已保存 · 1.5 KB · 现在仍是这一版", "Content kept · 1.5 KB · Still this version now"),
        "desk-meta: " + w("内容已保存 · 1.5 KB · 之后改过", "Content kept · 1.5 KB · Changed since"),
        "desk-meta: " + w("只记下了指纹 · 20 字节 · 现在已不在项目里", "Only the fingerprint was recorded · 20 bytes · No longer in the project"),
        "desk-meta: " + w("当时文件已不存在", "The file did not exist then"),
        "desk-meta: " + w("当时文件只在云端 · 1.5 KB", "The file was only in the cloud then · 1.5 KB"),
        "desk-meta: " + w("当时文件无法读取 · 1.5 KB", "The file could not be read then · 1.5 KB"),
        "evidence-sha mono: " + w("指纹 ", "Fingerprint ") + "ab" * 8, "ws-text-link: " + w("查看当时的内容", "View the content then"),
    ]:
        assert line in evidence, line
    opened = out["opened"]
    for line in [
        "desk-meta: " + w("当时的内容，记下于 2024年3月1日", "The content then, recorded 1 Mar 2024"),
        "evidence-text: alpha = 1\n", "team-line quiet: " + w("内容太长，只显示了开头。", "The content is too long: only the beginning is shown."),
        "team-line quiet: " + w("这一版只记下了指纹和大小，没有保存内容",
                                "Only this version’s fingerprint and size were recorded; its content was not kept"),
        "team-line quiet: " + w("这一版不是文本，不在这里显示", "This version is not text, so it is not shown here"),
        "ws-text-link: " + w("收起", "Show less"),
    ]:
        assert line in opened, line
    assert out["unread"][-1] == "notice-status err: " + w("没能读到这条路线当时的依据", "Could not read the basis this route had then")
    assert out["bare"][-1] == "team-line quiet: " + w("这条路线没有经批准记下的依据：它是在尝试表里直接改的。",
                                                      "This route has no approved basis on record: it was changed directly in the attempt table.")
    if lang == "en":
        everything = lines + out["filtered"] + evidence + opened + out["unread"] + out["bare"]
        assert _theirs_removed(everything) == [] and not any(FORBIDDEN.search(line) for line in everything)


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_18_3_a_status_the_researcher_named_heads_its_group_in_their_word_and_a_members_returned_route_reads_in_english(lang):
    """A group whose status is 已放弃 but whose word the researcher named in [verdict_labels] is headed by their word, as
    written in either language (the heading is the desk's verdict word of the group's status code, like the chips on its
    rows). A member's list says 我的提议 and, for a returned route with no description, its title and 退回的理由."""
    w = _w(lang)
    out = _routes_list("none", _list_routes("死路"), lang, more=False)
    assert _headings(out["list"])[3] == "死路" and "status-label tone-gray: 死路" in out["list"]
    member = _team_case("member", lang)
    member["get"]["/api/routes"] = _list_routes()
    returned = {"id": "p4", "kind": "attempt_new", "state": "returned", "by": WANG, "at": "2024-03-01T10:00:00", "sentence": "",
                "attempt": {"number": "19", "date": "08-01", "description": "", "verdict_status": _st("pending", "待定")},
                "outcomes": [{"act": "returned", "text": "先补稳健性"}]}
    member["get"]["/api/proposals"]["proposals"].append(returned)
    steps = "global.state.attemptsConfig = true; global.clearProjectState = () => {};\n" \
            "views.list = el('div', 'host'); await window.ROTeam.loadRoutes(views.list); global.extra = dump(views.list, []);"
    lines = _run_team(member, steps, lang=None if lang == "zh" else lang)["extra"]
    for line in ["route-group-title serif: " + w("我的提议", "My proposals"), "route-title: " + w("（无描述）", "(no description)"),
                 "desk-note: " + w("退回的理由：先补稳健性", "Reason for return: 先补稳健性"),
                 "status-label tone-gray: " + w("已退回", "Returned")]:
        assert line in lines, line
    if lang == "en":
        assert _theirs_removed(lines) == []

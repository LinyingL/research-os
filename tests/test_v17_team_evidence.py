"""DESIGN.md 22.5 (task V17, B4): a member's passages tied to supporting files go through the PI. A member's 关联依据文件,
「是这段」 and 撤回关联 are written in their working copy only, shown there as 待批准 and backing nothing; 同步 sends them
as one proposal of the kind `passage_evidence` (「依据关系」) naming each draft's revision; approving appends them to the
official evidence.toml with the member's ids, `by` and `approved_by`; returning appends them with the PI's acts that take
them back; either way the member's copy is replaced at the next sync with no conflict copy. Links are never approved
before the file proposal holding their draft's revision; an engine that does not know the kind refuses it. 12.10.3's two
people on one machine, with real signatures. Every test names its scenario."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path

import pytest

from researchos import cli, latex_edit, proposals, share, signing
from researchos.records import decisions as decisions_mod
from researchos.records import evidence

from test_v8_phase2 import _scan
from test_v8_phase4 import MEMBER, PI, Drive

DRAFT = "\\section{引言}\n人民币国际化的进展很慢。\n\n第二段 $x^2$ 结束。\n\n\\section{数据}\n我们用 BIS 的数据。\n"
REL = "paper/main.tex"
EVIDENCE = ".researchos/evidence.toml"


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Drive:
    """The PI's official project holds a draft and a figure, published; the member has joined."""
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    d = Drive(tmp_path, monkeypatch, join=False)
    with d.pi():
        (d.official / "paper").mkdir()
        (d.official / REL).write_text(DRAFT, encoding="utf-8")
        (d.official / "figures").mkdir()
        (d.official / "figures/f1.png").write_bytes(b"png")
        (d.official / "figures/f2.png").write_bytes(b"png2")
        _scan(d.official)
        share.publish(d.official, note="正文")
    with d.member():
        share.join(d.shared, d.work)
        assert cli.main(["ingest", str(d.work)]) == 0   # the working copy opened on the member's Mac
    return d


def _act(root: Path, action: str, words: str | None = None, **body) -> dict:
    if words is not None:
        text = (root / REL).read_text(encoding="utf-8")
        start = text.index(words)
        body.update(path=REL, revision=hashlib.sha256((root / REL).read_bytes()).hexdigest(), **{"from": start},
                    to=start + len(words))
    return latex_edit.evidence_act(root, action, body)


def _links(root: Path) -> list[dict]:
    return latex_edit.evidence_of_draft(root, REL)["links"]


def _entries(root: Path) -> list[dict]:
    path = root / EVIDENCE
    return tomllib.loads(path.read_text(encoding="utf-8")).get("passage_evidence", []) if path.exists() else []


def _sync(d: Drive) -> dict:
    with d.member():
        return share.sync(d.work)


def _proposals(d: Drive, who: str = "pi") -> list[dict]:
    with (d.pi() if who == "pi" else d.member()):
        return [p for p in proposals.listing(d.official if who == "pi" else d.work)["proposals"]
                if p["kind"] == "passage_evidence"]


def _beside(root: Path) -> list[str]:
    return sorted(p.name for p in (root / ".researchos").iterdir() if p.name.startswith("evidence") and p.name != "evidence.toml")


def test_22_5_a_members_link_waits_as_pending_goes_at_sync_and_reaches_the_official_record_with_their_ids(drive):
    with drive.member():
        done = _act(drive.work, "link", "人民币国际化的进展很慢。", file="figures/f1.png", note="图 1")
        (mine,) = _links(drive.work)
        assert (mine["state"], mine["pending"]) == ("found", True)   # 待批准: shown, backing nothing
        assert latex_edit.declared_by_claim(drive.work, REL) == {}
        assert latex_edit.passages_supported(drive.work, "figures/f1.png")["passages"][0]["pending"] is True
    with drive.pi():
        assert not (drive.official / EVIDENCE).exists()   # nothing official yet
    first = _sync(drive)
    assert first["evidence_proposed"]
    assert _sync(drive)["evidence_proposed"] is None   # sent once
    (inbox,) = _proposals(drive)
    assert (inbox["state"], inbox["sentence"], inbox["files"]) == ("pending", "", [])
    assert inbox["evidence"] == [{"act": "link", "link": done["link"], "quote": "人民币国际化的进展很慢。", "draft": REL,
                                  "file": "figure:figures/f1.png", "note": "图 1"}]
    (sent,) = _proposals(drive, "member")
    assert sent["id"] == inbox["id"] == first["evidence_proposed"]
    with drive.pi():   # the official ledger moves on before the PI answers: the member's waits, nothing put beside it
        pis = _act(drive.official, "link", "我们用 BIS 的数据。", file="figures/f2.png")
        share.publish(drive.official)
    waiting = _sync(drive)
    assert EVIDENCE in waiting["notes_waiting"] and not waiting["conflicts"] and _beside(drive.work) == []
    with drive.member():
        assert [l["pending"] for l in _links(drive.work)] == [True]
    with drive.pi():
        changes = proposals.open_for(drive.official, inbox["id"], lang="en")["changes"]
        assert changes["approvable"] and changes["evidence"]["state"] == "apply"
        assert changes["evidence"]["items"][0]["quote"] == "人民币国际化的进展很慢。"
        result = proposals.approve(drive.official, inbox["id"], note="同意")
        assert result["applied"]["passages"] == [done["entry"]]
        (_pi_entry, entry) = _entries(drive.official)
        assert (entry["id"], entry["link"], entry["by"], entry["approved_by"], entry["act"]) == \
            (done["entry"], done["link"], MEMBER[1], PI[1], "link")
        decision = decisions_mod.for_proposal(drive.official, inbox["id"])
        assert (decision["kind"], decision["by"], decision["approved_by"]) == ("passage_evidence", MEMBER[1], PI[1])
        assert {(l["link"], l["state"], l["pending"]) for l in _links(drive.official)} == \
            {(done["link"], "found", False), (pis["link"], "found", False)}
    after = _sync(drive)
    assert EVIDENCE in after["updated"] and not after["conflicts"]
    assert _beside(drive.work) == []   # no conflict copy beside it
    with drive.member():
        assert {(l["link"], l["pending"]) for l in _links(drive.work)} == {(done["link"], False), (pis["link"], False)}
        assert (drive.work / EVIDENCE).read_bytes() == (drive.official / EVIDENCE).read_bytes()
    assert _sync(drive)["evidence_proposed"] is None
    drive.witness.look()


def test_22_5_a_returned_proposal_takes_back_a_new_link_and_puts_an_official_link_back_on_its_passage(drive):
    with drive.pi():
        official = _act(drive.official, "link", "我们用 BIS 的数据。", file="figures/f2.png")
        share.publish(drive.official)
    _sync(drive)
    with drive.member():
        made = _act(drive.work, "link", "第二段 $x^2$ 结束。", file="figures/f1.png")
        _act(drive.work, "reanchor", "我们用", link=official["link"])
        assert {l["link"]: l["pending"] for l in _links(drive.work)} == {official["link"]: True, made["link"]: True}
    _sync(drive)
    (inbox,) = _proposals(drive)
    assert [i["act"] for i in inbox["evidence"]] == ["link", "reanchor"]
    with drive.pi():
        proposals.return_(drive.official, inbox["id"], "这段不需要依据")
        acts = [(e["act"], e["by"], e.get("approved_by")) for e in _entries(drive.official)]
        assert acts == [("link", PI[1], None), ("link", MEMBER[1], None), ("reanchor", MEMBER[1], None),
                        ("withdraw", PI[1], None), ("reanchor", PI[1], None)]
        (left,) = _links(drive.official)   # the member's link is withdrawn, the PI's is on its own passage again
        assert (left["link"], left["quote"], left["state"]) == (official["link"], "我们用 BIS 的数据。", "found")
        with pytest.raises(proposals.ProposalRefused):
            proposals.approve(drive.official, inbox["id"])
    after = _sync(drive)
    assert EVIDENCE in after["updated"] and not after["conflicts"] and _beside(drive.work) == []
    with drive.member():
        (left,) = _links(drive.work)
        assert (left["link"], left["quote"], left["pending"]) == (official["link"], "我们用 BIS 的数据。", False)
        (mine,) = _proposals(drive, "member")
        assert mine["state"] == "returned"


def test_22_5_links_are_not_approved_before_the_file_proposal_holding_their_drafts_revision(drive):
    with drive.member():
        (drive.work / REL).write_text(DRAFT + "\n新加的一段，结论稳健。\n", encoding="utf-8")
        files = proposals.write_proposal(drive.work, proposals.make_proposal(drive.work, "files", "加一段"))
        _act(drive.work, "link", "新加的一段，结论稳健。", file="figures/f1.png")
    _sync(drive)
    (inbox,) = _proposals(drive)
    with drive.pi():
        changes = proposals.open_for(drive.official, inbox["id"])["changes"]
        part = changes["evidence"]
        assert (part["state"], part["code"], part["waiting_for"]) == ("conflict", "draft_waiting", [files.id])
        assert not changes["approvable"] and files.id in part["reason"] and REL in part["reason"]
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.approve(drive.official, inbox["id"])
        assert refused.value.message_zh.startswith(f"文稿 {REL} 的这一版还在提议")
        assert "approve it first" in str(refused.value)
        assert not (drive.official / EVIDENCE).exists()
        proposals.approve(drive.official, files.id)
        assert proposals.open_for(drive.official, inbox["id"])["changes"]["approvable"]
        proposals.approve(drive.official, inbox["id"])
        (link,) = _links(drive.official)
        assert (link["state"], link["quote"]) == ("found", "新加的一段，结论稳健。")


def test_22_5_a_link_the_pi_changed_since_the_member_saw_it_is_a_conflict_until_returned(drive):
    with drive.pi():
        official = _act(drive.official, "link", "我们用 BIS 的数据。", file="figures/f2.png")
        share.publish(drive.official)
    _sync(drive)
    with drive.member():
        _act(drive.work, "withdraw", link=official["link"])
        assert _links(drive.work) == []
    _sync(drive)
    (inbox,) = _proposals(drive)
    with drive.pi():
        _act(drive.official, "reanchor", "BIS 的数据", link=official["link"])
        part = proposals.open_for(drive.official, inbox["id"])["changes"]["evidence"]
        assert (part["state"], part["code"]) == ("conflict", "link_changed")
        before = (drive.official / EVIDENCE).read_bytes()
        with pytest.raises(proposals.ProposalRefused):
            proposals.approve(drive.official, inbox["id"])
        assert (drive.official / EVIDENCE).read_bytes() == before
        proposals.return_(drive.official, inbox["id"], "我已经改过这段")
        (left,) = _links(drive.official)   # the member's withdraw is taken back; the PI's passage stays
        assert (left["link"], left["quote"]) == (official["link"], "BIS 的数据")
    assert not _sync(drive)["conflicts"] and _beside(drive.work) == []


def test_22_5_an_engine_that_does_not_know_the_kind_refuses_it_and_writes_nothing(drive, monkeypatch):
    with drive.member():
        _act(drive.work, "link", "人民币国际化的进展很慢。", file="figures/f1.png")
    _sync(drive)
    (inbox,) = _proposals(drive)
    monkeypatch.setattr(proposals, "KINDS", ("attempt_new", "verdict_change", "variable_version", "files", "route_link"))
    with drive.pi():
        for action in (lambda: proposals.open_for(drive.official, inbox["id"]),
                       lambda: proposals.approve(drive.official, inbox["id"])):
            with pytest.raises(proposals.ProposalRefused) as refused:
                action()
            assert refused.value.code == "invalid"
        assert not (drive.official / EVIDENCE).exists()
        assert decisions_mod.for_proposal(drive.official, inbox["id"]) is None


def test_22_5_who_may_link_where_and_how_a_passage_proposal_is_made(drive):
    with drive.member():
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.make_proposal(drive.work, "passage_evidence", "")
        assert refused.value.code == "evidence_at_sync"
        assert proposals.evidence_draft(drive.work) is None   # nothing waiting
        with pytest.raises(latex_edit.WorkspaceError) as outside:   # the official folder is not the member's copy
            _act(drive.official, "link", "人民币国际化的进展很慢。", file="figures/f1.png")
        assert outside.value.code == "member" and not (drive.official / EVIDENCE).exists()
        _act(drive.work, "link", "人民币国际化的进展很慢。", file="figures/f1.png")
        draft = proposals.evidence_draft(drive.work)
        (revision,) = draft.data["revision"]
        assert revision == {"path": REL, "sha256": hashlib.sha256((drive.work / REL).read_bytes()).hexdigest()}
        assert draft.data["evidence"][0]["follows"] == ""
    with drive.pi():   # the PI's own links need no proposal
        _act(drive.official, "link", "我们用 BIS 的数据。", file="figures/f2.png")
        assert [l["pending"] for l in _links(drive.official)] == [False]
    assert evidence.SCHEMA.field_order.index("approved_by") == 4


# -- the PI's review list and panel, driven under node (22.5, 22.6) -----------------------------------------------------

ITEMS = [{"act": "link", "link": "pl-1", "quote": "人民币国际化的进展很慢。", "draft": REL, "file": "figure:figures/f1.png",
          "note": "图 1"},
         {"act": "reanchor", "link": "pl-0", "quote": "我们用", "draft": REL, "file": "figure:figures/f2.png"}]


def _evidence_case(lang: str) -> dict:
    from test_v10_desk_proposals import FROM, _team_case

    case = _team_case("pi", lang)
    for listed in (case["desk"]["column"]["waiting"], case["get"]["/api/proposals"]["proposals"]):
        for p in listed:
            if p["id"] == "p3":
                p.update(kind="passage_evidence", route=None, sentence="", evidence=ITEMS)
    said = proposals._reason("draft_waiting", lang, draft=REL, ids="pr-1")["reason"]  # noqa: SLF001
    page = case["get"][f"/api/proposals/p3{FROM}"]
    page.update(kind="passage_evidence", route=None, sentence="", evidence=ITEMS)
    page["changes"].update(route=None, approvable=False, conflicts=[{"path": EVIDENCE, "reason": said}],
                           evidence={"path": EVIDENCE, "state": "conflict", "code": "draft_waiting", "items": ITEMS,
                                     "waiting_for": ["pr-1"], "reason": said})
    return case


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_22_6_the_pi_sees_the_passage_its_draft_and_its_file_in_the_review_list_in_the_screens_language(lang):
    from test_v10_desk_proposals import _run_team
    from test_v9_language import NODE

    if NODE is None:
        pytest.skip("node is not installed")
    out = _run_team(_evidence_case(lang), """
      global.extra = { row: views.desk.all().map((k) => k.kids.filter((x) => typeof x === "string").join("")) };
      button(row("p:p3"), lang === "en" ? "View" : "查看").click(); await settle(); await settle();
      extra.panel = panelLines();
    """.replace("lang ===", f"{json.dumps(lang)} ==="), lang=None if lang == "zh" else lang)
    en = lang == "en"
    w = (lambda zh, english: english if en else zh)
    assert w("依据关系", "Supporting-file links") in out["extra"]["row"]
    assert w("2 条依据关系", "2 supporting-file links") in out["extra"]["row"]
    panel = out["extra"]["panel"]
    for line in [w("desk-head: 批准后写入正式版本的依据关系", "desk-head: Supporting-file links approving writes to the official version"),
                 w("evidence-role: 关联依据文件", "evidence-role: Link a supporting file"),
                 w("evidence-role: 指明段落现在的位置", "evidence-role: Said where the passage is now"),
                 "mono: " + REL, w("desk-note plain: 「人民币国际化的进展很慢。」", "desk-note plain: “人民币国际化的进展很慢。”"),
                 w("desk-meta: 依据文件：figures/f1.png", "desk-meta: Supporting file: figures/f1.png"), "desk-note: 图 1",
                 "desk-break: " + proposals._reason("draft_waiting", lang, draft=REL, ids="pr-1")["reason"]]:  # noqa: SLF001
        assert line in panel, (line, panel)
    if en:   # nothing of ours in Chinese: only the member's passage, note and file name
        assert not [l for l in panel if re.search(r"[一-鿿]", l) and "人民币" not in l and "我们用" not in l
                    and "图 1" not in l and "王" not in l and "数据从哪来" not in l and "残差" not in l]


# -- what the review of B4 found: answers out of order, a return taken up again, a hand-made proposal ----------------


def _readable(root: Path) -> bool:
    return evidence.assess(root)[1].may_apply


def _two_proposals(drive: Drive, second: str, link_words: str = "人民币国际化的进展很慢。") -> tuple[dict, dict, dict]:
    """The member links a passage (P1, synced), then withdraws or undoes that link (P2, synced)."""
    with drive.member():
        made = _act(drive.work, "link", link_words, file="figures/f1.png")
    _sync(drive)
    with drive.member():
        if second == "withdraw":
            _act(drive.work, "withdraw", link=made["link"])
        else:
            _act(drive.work, "undo", link=made["link"], undoes=made["entry"])
    _sync(drive)
    ps = _proposals(drive)
    first = next(p for p in ps if p["evidence"][0]["act"] == "link")
    later = next(p for p in ps if p["id"] != first["id"])
    return made, first, later


def test_22_5_a_later_proposal_on_a_link_still_waiting_in_an_earlier_one_waits_for_it_and_nothing_breaks(drive):
    made, first, later = _two_proposals(drive, "withdraw")
    with drive.pi():
        part = proposals.open_for(drive.official, later["id"])["changes"]["evidence"]
        assert (part["state"], part["code"], part["waiting_for"]) == ("conflict", "earlier_waiting", [first["id"]])
        assert first["id"] in part["reason"] and "请退回" not in part["reason"]
        for answer in (lambda: proposals.return_(drive.official, later["id"], "不要"),
                       lambda: proposals.approve(drive.official, later["id"])):
            with pytest.raises(proposals.ProposalRefused) as refused:
                answer()
            assert refused.value.code in ("not_returnable", "not_approvable") and first["id"] in str(refused.value)
        assert not (drive.official / EVIDENCE).exists()   # nothing written by either refusal
        proposals.approve(drive.official, first["id"])
        assert _readable(drive.official) and [l["link"] for l in _links(drive.official)] == [made["link"]]
        assert proposals.open_for(drive.official, later["id"])["changes"]["evidence"]["state"] == "apply"
        proposals.return_(drive.official, later["id"], "这段要有依据")
        assert _readable(drive.official) and [l["link"] for l in _links(drive.official)] == [made["link"]]
    after = _sync(drive)
    assert EVIDENCE in after["updated"] and not after["notes_waiting"] and _beside(drive.work) == []
    with drive.member():
        assert [(l["link"], l["pending"]) for l in _links(drive.work)] == [(made["link"], False)]


def test_22_5_an_undo_returned_after_the_link_it_undid_was_returned_is_kept_and_changes_nothing(drive):
    made, first, later = _two_proposals(drive, "undo")
    with drive.pi():
        proposals.return_(drive.official, first["id"], "不要")
        assert _readable(drive.official) and _links(drive.official) == []
        with pytest.raises(proposals.ProposalRefused):   # it no longer follows the official act
            proposals.approve(drive.official, later["id"])
        result = proposals.return_(drive.official, later["id"], "也不要")
        assert result["published"]["published"] is not False
        assert _readable(drive.official) and _links(drive.official) == []
        acts = [(e["act"], e["by"]) for e in _entries(drive.official)]
        assert acts == [("link", MEMBER[1]), ("withdraw", PI[1]), ("undone", MEMBER[1])]
    after = _sync(drive)   # every entry of the member's is held: their copy is replaced, nothing left waiting
    assert EVIDENCE in after["updated"] and not after["notes_waiting"] and _beside(drive.work) == []
    with drive.member():
        assert _links(drive.work) == []
        assert (drive.work / EVIDENCE).read_bytes() == (drive.official / EVIDENCE).read_bytes()


def test_22_5_a_returned_undo_of_the_pis_own_link_leaves_that_link_standing(drive):
    with drive.pi():
        pis = _act(drive.official, "link", "我们用 BIS 的数据。", file="figures/f2.png")
        share.publish(drive.official)
    _sync(drive)
    with drive.member():
        _act(drive.work, "undo", link=pis["link"], undoes=pis["entry"])
        assert _links(drive.work) == []
    _sync(drive)
    (inbox,) = _proposals(drive)
    with drive.pi():
        proposals.return_(drive.official, inbox["id"], "这条要留着")
        assert _readable(drive.official)
        assert [(l["link"], l["quote"], l["state"]) for l in _links(drive.official)] == \
            [(pis["link"], "我们用 BIS 的数据。", "found")]


@pytest.mark.parametrize("then", ["approve", "return"])
def test_22_5_a_return_refused_before_writing_binds_nothing_and_either_answer_works_after(drive, then):
    with drive.pi():
        _act(drive.official, "link", "我们用 BIS 的数据。", file="figures/f2.png")
        share.publish(drive.official)
    _sync(drive)
    with drive.member():
        made = _act(drive.work, "link", "人民币国际化的进展很慢。", file="figures/f1.png")
    _sync(drive)
    (inbox,) = _proposals(drive)
    copy = drive.official / ".researchos/evidence (conflicted copy).toml"
    with drive.pi():
        copy.write_bytes((drive.official / EVIDENCE).read_bytes())   # the official ledger is not to be trusted now
        before = (drive.official / EVIDENCE).read_bytes()
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.return_(drive.official, inbox["id"], "不要")
        assert refused.value.code == "evidence_not_written"
        assert (drive.official / EVIDENCE).read_bytes() == before
        assert proposals.read_journal(drive.official, inbox["id"])["answer"] == "return"   # the journal stays
        copy.unlink()
        if then == "approve":
            proposals.approve(drive.official, inbox["id"])
            assert made["link"] in [l["link"] for l in _links(drive.official)]
        else:
            proposals.return_(drive.official, inbox["id"], "不要")
            assert made["link"] not in [l["link"] for l in _links(drive.official)]
        assert _readable(drive.official)
    assert not _sync(drive)["notes_waiting"] and _beside(drive.work) == []


def test_22_5_a_return_taken_up_again_after_it_wrote_publishes_and_an_approval_stays_refused(drive, monkeypatch):
    with drive.member():
        made = _act(drive.work, "link", "人民币国际化的进展很慢。", file="figures/f1.png")
    _sync(drive)
    (inbox,) = _proposals(drive)
    real = proposals._outcome   # noqa: SLF001

    def cut(*args, **kwargs):
        raise OSError("the shared folder went away")

    with drive.pi():
        monkeypatch.setattr(proposals, "_outcome", cut)
        with pytest.raises(OSError):
            proposals.return_(drive.official, inbox["id"], "不要")
        monkeypatch.setattr(proposals, "_outcome", real)
        assert _readable(drive.official) and made["entry"] in {e["id"] for e in _entries(drive.official)}
        with pytest.raises(proposals.ProposalRefused) as refused:   # written as a return: never approved now
            proposals.approve(drive.official, inbox["id"])
        assert refused.value.code == "decided"
        result = proposals.return_(drive.official, inbox["id"], "不要")
        assert result["published"]["published"] is not False
        assert [(e["act"], e["by"]) for e in _entries(drive.official)] == [("link", MEMBER[1]), ("withdraw", PI[1])]
    after = _sync(drive)
    assert EVIDENCE in after["updated"] and not after["notes_waiting"]


@pytest.mark.parametrize("change", ["no_follows", "follows_twice", "follows_own", "bad_at", "bad_id", "id_newline"])
def test_22_5_a_hand_made_proposal_without_follows_or_with_a_bad_time_or_id_is_invalid(drive, change):
    with drive.member():
        made = _act(drive.work, "link", "人民币国际化的进展很慢。", file="figures/f1.png")
        _act(drive.work, "reanchor", "人民币国际化", link=made["link"])
        data = {**proposals.evidence_draft(drive.work).data, "id": "pr-" + "0" * 32, "at": "2026-11-13T10:00:00+01:00"}
    proposals.validate(data)
    first, second = data["evidence"]
    if change == "no_follows":
        del first["follows"]
    elif change == "follows_twice":
        second["follows"] = first["id"]
    elif change == "follows_own":
        first["follows"] = second["id"]
    elif change == "bad_at":
        first["at"] = "not a time"
    elif change == "bad_id":
        first["id"] = "pe-x"
    else:
        first["id"] = "pe-" + "0" * 31 + "\n"
    with pytest.raises(ValueError):
        proposals.validate(data)


def test_22_5_whether_a_drafts_revision_still_waits_unknown_refuses_the_approval(drive, monkeypatch):
    with drive.member():
        _act(drive.work, "link", "人民币国际化的进展很慢。", file="figures/f1.png")
    _sync(drive)
    (inbox,) = _proposals(drive)

    def unreachable(*_args):
        raise OSError("the shared folder cannot be listed")

    monkeypatch.setattr(proposals, "_pending_others", unreachable)
    with drive.pi():
        part = proposals.open_for(drive.official, inbox["id"], lang="en")["changes"]["evidence"]
        assert (part["state"], part["reason_code"]) == ("problem", "drafts_unknown")
        assert part["reason"].startswith("Whether this version of the draft still waits")
        with pytest.raises(proposals.ProposalRefused):
            proposals.approve(drive.official, inbox["id"])
        assert not (drive.official / EVIDENCE).exists()


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_22_6_links_not_proposed_at_sync_are_said_in_the_screens_language(drive, lang):
    with drive.member():
        _act(drive.work, "link", "人民币国际化的进展很慢。", file="figures/f1.png")
        (drive.work / EVIDENCE).write_text("not = [valid", encoding="utf-8")
        out = share.sync(drive.work, lang=lang)
    (problem,) = [p for p in out["problems"] if p["kind"] == "evidence_not_proposed"]
    if lang == "en":
        assert problem["detail"].startswith("The supporting-file links were not proposed: ")
        assert not re.search(r"[一-鿿]", problem["detail"])
    else:
        assert problem["detail"].startswith("依据关系没有提议：")

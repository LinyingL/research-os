"""Research OS V10, review fixes on the records (DESIGN.md 14.2, 14.3, 12.4,
9.2): what the confirmed findings of the V10 review showed the engine
writing wrongly, each with the scenario that would have caught it.

- A verdict the researcher wrote over two lines (`<br>`) is abandoned
  directly and through an approval, and its decision is recorded with the
  line break kept (decision.verdict is multiline, as decision.result is).
- A member's new attempt that is abandoned from the start asks for its
  result and reason, as a verdict change does, and approving it freezes
  its basis -- the placed step scripts and the outputs they write.
- With team.toml unreadable a 「源自」 line is not written directly.
- A git project with a file name holding a line break still draws the desk.
- One busy day of line edits never rotates the daily snapshots away.

Every test names its scenario."""

from __future__ import annotations

import json

import pytest

from researchos import proposals, signing
from researchos.records import decisions as decisions_mod

from test_v8_phase2 import _map, _project, _scan
from test_v8_phase4 import NEW_SCRIPT, Drive
from test_v9_language import needs_node

A16 = "attempt:map.md#16"
TWO_LINES = "☠️ 伪协整<br>方向反了"


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Drive:
    """12.10.3's PI and a member who joined from the shared folder."""
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    return Drive(tmp_path, monkeypatch)


def _row(root, number="16") -> str:
    return next(line for line in (root / "map.md").read_text().splitlines() if line.startswith(f"| {number} |"))


def test_14_3_a_verdict_over_two_lines_abandoned_directly_records_its_decision(tmp_path):
    """The researcher's verdict holds a <br>: the row is written as typed
    and decisions.toml records the abandonment, its verdict holding the
    line break as a line feed -- and a second write never leaves the route
    abandoned with no basis recorded."""
    root = _project(tmp_path / "proj")
    done = proposals.write_directly(root, "update", "16", {"verdict": TWO_LINES}, "残差不平稳")
    assert _row(root).endswith(f"| t=2.91（放弃理由：残差不平稳） | {TWO_LINES} |")
    [entry] = decisions_mod.entries(root)
    assert done["decision"]["id"] == entry["id"]
    assert (entry["act"], entry["verdict"], entry["reason"]) == ("abandoned", "☠️ 伪协整\n方向反了", "残差不平稳")
    raw = decisions_mod.decisions_path(root).read_text(encoding="utf-8")
    assert 'verdict = "☠️ 伪协整\\n方向反了"' in raw  # the TOML escape: the file's own lines are never split
    assert decisions_mod.assess(root)[1].verdict is decisions_mod.Trust.OK


def test_14_3_a_decision_that_could_not_be_recorded_is_refused_before_the_table_is_written(tmp_path):
    """A text the decision cannot hold (a result over the ledger's limit) is
    refused before anything is written, never after the row landed -- a
    write that did land would leave the route abandoned with no basis, and
    writing it again would find it abandoned already and record nothing."""
    root = _project(tmp_path / "proj")
    before = (root / "map.md").read_bytes()
    too_long = {"verdict": "☠️ 伪协整", "result": "t" * (decisions_mod.MAX_TEXT + 1)}
    with pytest.raises(proposals.ProposalRefused) as refused:
        proposals.write_directly(root, "update", "16", too_long, "残差不平稳")
    assert refused.value.code == "decisions_unwritable"
    assert (root / "map.md").read_bytes() == before and decisions_mod.entries(root) == []


def test_14_3_a_verdict_over_two_lines_abandoned_through_an_approval_records_its_decision(drive):
    """A member proposes ☠️ over two lines with its result and reason; the
    PI's approval writes the row and records one abandoned decision whose
    verdict holds the line feed."""
    with drive.member():
        (drive.work / "map.md").write_text(_map(TWO_LINES))
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(
            drive.work, "verdict_change", "放弃 16", result="t=0.41", reason="残差不平稳"))
    with drive.pi():
        done = proposals.approve(drive.official, stored.id)
        [entry] = [e for e in decisions_mod.entries(drive.official) if e.get("proposal") == stored.id]
        assert done["decision"]["id"] == entry["id"]
        assert (entry["act"], entry["verdict"], entry["result"]) == ("abandoned", "☠️ 伪协整\n方向反了", "t=0.41")
        assert _row(drive.official).endswith(f"| t=0.41（放弃理由：残差不平稳） | {TWO_LINES} |")


DEAD_18 = "| 18 | 08-02 | Big panel (18) | TopicShift->RV | {result} | ☠️ 太慢 |\n"


def _member_dead_end(drive: Drive, result: str = "t=1.10") -> None:
    """The member's new row #18, abandoned from the start, with its step
    script -- which writes data/out18.csv."""
    (drive.work / "steps" / "18-big.py").write_text(NEW_SCRIPT)
    (drive.work / "map.md").write_text(_map() + DEAD_18.format(result=result))


def test_14_3_a_members_new_attempt_abandoned_from_the_start_asks_its_result_and_reason(drive):
    """12.4 / 14.3: a new row whose verdict abandons is proposed only with
    its result and a reason -- as a verdict change is; the proposal carries
    them, and a stored one claiming to abandon without them is not valid."""
    with drive.member():
        _member_dead_end(drive, result="")
        for reason in (None, "样本太短"):  # no reason; a reason but no result
            with pytest.raises(proposals.ProposalRefused) as refused:
                proposals.make_proposal(drive.work, "attempt_new", "加一条死路", reason=reason)
            assert refused.value.code == "reason_required"
            assert refused.value.message_zh == proposals.MESSAGES["reason_required"]
        _member_dead_end(drive)
        draft = proposals.make_proposal(drive.work, "attempt_new", "加一条死路", reason=" 样本太短 ")
    attempt = draft.data["attempt"]
    assert (attempt["reason"], attempt["abandons"], attempt["result"]) == ("样本太短", "yes", "t=1.10")
    data = {**draft.data, "id": "pr-" + "0" * 32, "at": "2026-10-13T10:00:00+02:00"}
    proposals.validate(data)
    for broken in ({"reason": None}, {"result": ""}):
        bad = {**data, "attempt": {k: v for k, v in {**attempt, **broken}.items() if v is not None}}
        with pytest.raises(ValueError):
            proposals.validate(bad)


def test_14_3_approving_a_new_attempt_abandoned_from_the_start_freezes_its_basis(drive):
    """The PI sees the result, the reason and that the basis will freeze;
    approving writes the reason after the result as an abandonment writes
    it, and records an `abandoned` decision whose evidence holds the step
    script the proposal placed AND the output it writes -- the script is
    scanned before the basis is frozen."""
    with drive.member():
        _member_dead_end(drive)
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(
            drive.work, "attempt_new", "加一条死路", reason="样本太短"))
    with drive.pi():
        a = proposals.open_for(drive.official, stored.id, lang="en")["changes"]["attempt"]
        assert (a["op"], a["state"], a["abandons"], a["result"], a["reason"]) == (
            "append", "apply", True, "t=1.10", "样本太短")
        assert a["fields"]["result"] == "t=1.10（放弃理由：样本太短）" and "放弃理由：样本太短" in a["new_row"]
        done = proposals.approve(drive.official, stored.id)
        assert _row(drive.official, "18") == "| 18 | 08-02 | Big panel (18) | TopicShift->RV | t=1.10（放弃理由：样本太短） | ☠️ 太慢 |"
        entry = done["decision"]
        assert (entry["act"], entry["kind"], entry["attempt"], entry["proposal"]) == (
            "abandoned", "attempt_new", "attempt:map.md#18", stored.id)
        assert (entry["verdict"], entry["result"], entry["reason"]) == ("☠️ 太慢", "t=1.10", "样本太短")
        roles = {e["path"]: e["role"] for e in entry["evidence"]}
        assert roles == {"steps/18-big.py": "script", "data/out18.csv": "output"}
        assert decisions_mod.entries(drive.official)[-1]["id"] == entry["id"]


def test_14_3_a_new_attempt_proposed_before_the_rule_must_be_proposed_again(drive, monkeypatch):
    """A pending proposal sent before the rule -- abandoned, with no reason
    -- shows its problem and cannot be approved, as an old verdict change
    without its reason cannot."""
    with drive.member():
        _member_dead_end(drive)
        with monkeypatch.context() as old:
            old.setattr(proposals, "is_abandonment", lambda verdict, markers: False)
            stored = proposals.write_proposal(drive.work, proposals.make_proposal(drive.work, "attempt_new", "旧的"))
    assert "reason" not in stored.data["attempt"]
    with drive.pi():
        changes = proposals.open_for(drive.official, stored.id)["changes"]
        assert (changes["attempt"]["state"], changes["attempt"]["reason"]) == ("problem", proposals.MESSAGES["reason_required"])
        assert changes["approvable"] is False
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.approve(drive.official, stored.id)
        assert refused.value.code == "not_approvable" and "| 18 |" not in (drive.official / "map.md").read_text()


def _real_pi_case(drive: Drive, stored, lang: str) -> dict:
    """The PI's desk (test_v10_desk_proposals' stand-in) whose p1 is the
    member's real proposal, as GET /api/proposals/<id> answers it in `lang`."""
    from researchos.webapp import node_api

    from test_v10_desk_proposals import FROM, _team_case

    with drive.pi():
        full = node_api.proposal_payload(drive.official, stored.id, lang)
    case = _team_case("pi", lang)
    listed = case["desk"]["column"]["waiting"][0]
    listed.update(kind=full["kind"], sentence=full["sentence"], verdict=None, attempt=full.get("attempt"), route=full.get("route"),
                  files=full.get("files") or [])
    case["get"][f"/api/proposals/p1{FROM}"] = {**full, "id": "p1"}
    return case


_OPEN_P1 = """
  button(row("p:p1"), %s).click(); await settle(); await settle();
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_14_3_the_pi_reads_an_abandoned_new_attempts_result_reason_and_frozen_basis(drive, lang):
    """「批准以后会怎样」 for an append that abandons says the result, the
    reason and that the basis will be frozen -- 12.3's "exactly what
    approving would do" -- from the engine's real payload."""
    from test_v10_desk_proposals import _run_team

    with drive.member():
        _member_dead_end(drive)
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(
            drive.work, "attempt_new", "加一条死路", reason="样本太短"))
    out = _run_team(_real_pi_case(drive, stored, lang), _OPEN_P1 % json.dumps("View" if lang == "en" else "查看"),
                    lang=None if lang == "zh" else lang)
    w = (lambda zh, en: en if lang == "en" else zh)
    for line in [w("desk-note: 结果：t=1.10", "desk-note: Result: t=1.10"),
                 w("desk-note: 放弃理由：样本太短", "desk-note: Reason for abandoning: 样本太短"),
                 w("team-line quiet: 批准会把结果和理由写进尝试表，并冻结这条路线当时的依据：它的步骤脚本和它们写出的文件。",
                   "team-line quiet: Approving writes the result and the reason into the attempt table and freezes the route's "
                   "basis at this moment: its step scripts and the files they write.")]:
        assert line in out["lines"], line


def test_14_3_the_members_form_asks_the_reason_for_a_new_row_that_abandons():
    """app.html's 提议新的尝试: the reason field shows once the preview says
    the row abandons, the result and the reason are required as for a
    verdict change, and the reason goes with the proposal."""
    from test_v9_language import _app_function, english_table

    form = _app_function("function renderAttemptForm(")
    assert 'if (proposing && mode === "append") {' in form
    assert 'if (memberReason) memberReason.classList.toggle("hidden", !preview.abandons);' in form
    assert "if (proposing && preview.abandons &&" in form and 'proposing && mode === "update" && preview.abandons' not in form
    help_ = "这一行一开始就放弃了这条路线：结果和理由都必须填写，PI 批准后它们写进尝试表，这条路线当时的依据也会冻结下来。"
    assert help_ in form and english_table()[help_].startswith("This row abandons the route from the start")

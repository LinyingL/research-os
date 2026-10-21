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
from pathlib import Path

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


# -- 25: a file name no node can have, in the desk's git history ---------------------------------------------


def test_14_5_a_committed_file_name_with_a_line_break_never_breaks_the_desk(tmp_path):
    """A git project whose last commits hold a file named with a line feed
    and one with a NEL: the desk still draws -- the map and its column,
    over the engine and GET /api/desk -- those names on no card; a file
    that changed in the same commits still marks its attempt."""
    from researchos import desk

    from test_v8_phase2 import _call, _conn, _git
    from test_v8_phase3 import _serve

    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    _project(root)
    (root / "notes").mkdir()
    for name in ("a\nb.txt", "c\x85d.txt"):
        (root / "notes" / name).write_text("x\n")
    _git(root, "add", "steps", "data", "figs", "paper.md", "map.md", "notes")
    _git(root, "commit", "-q", "-m", "first", date="2026-01-01T10:00:00+00:00")
    assert "a\nb.txt" in _git(root, "ls-files", "-z", "notes")
    conn = _conn(root)
    try:
        out = desk.payload(conn, root, lang="en")
    finally:
        conn.close()
    assert [a["number"] for a in out["attempts"]] == ["16"] and out["attempts"][0]["changed_at"] is not None
    with _serve(root) as base:
        status, body = _call(f"{base}/api/desk")
    assert status == 200 and [a["number"] for a in body["attempts"]] == ["16"]


# -- 26: a shared folder always slower than the desk waits ----------------------------------------------------


def test_14_4_a_shared_folder_slower_than_the_wait_shows_what_it_last_read(drive, monkeypatch):
    """The PI's shared folder answers in 0.4 s, the desk waits 0.1 s: the
    first visit says it is slow; the read finishes anyway and is kept, so
    the next visit lists the waiting proposal and says it is the last read
    (stale, with its time) -- never 「稍后再看」 for ever. A read that fails is
    never kept."""
    import time

    from researchos import desk

    from test_v8_phase2 import _conn

    with drive.member():
        (drive.work / "steps" / "18-big.py").write_text(NEW_SCRIPT)
        (drive.work / "map.md").write_text(_map() + DEAD_18.format(result="t=1.10").replace("☠️ 太慢", "🕒"))
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(drive.work, "attempt_new", "大面板"))
    real = desk._read_shared
    monkeypatch.setattr(desk, "_read_shared", lambda *a: time.sleep(0.4) or real(*a))

    def visit(lang="en"):
        conn = _conn(drive.official)
        try:
            return desk.payload(conn, drive.official, lang=lang, timeout=0.1)["column"]
        finally:
            conn.close()

    with drive.pi():
        first = visit()
        assert first["shared"] == {"state": "slow", "message": desk.SHARED_WORDS["slow"][1]} and first["waiting"] == []
        time.sleep(0.8)  # the read finishes after the desk stopped waiting
        later = visit()
        assert later["shared"]["state"] == "stale" and later["shared"]["message"] == desk.SHARED_WORDS["stale"][1]
        assert later["shared"]["at"] and [p["id"] for p in later["waiting"]] == [stored.id]
        assert visit("zh")["shared"]["message"] == "公盘现在读得太慢：提议和同步显示的是上次读到的情况"
        time.sleep(0.8)
        key = next(k for k in desk._LAST if k.startswith(str(drive.official.resolve())))
        kept = desk._LAST[key]
        monkeypatch.setattr(desk, "_read_shared", lambda *a: {"errors": {"listing": OSError("gone")}})
        assert visit()["shared"]["state"] == "unreadable"
        time.sleep(0.1)
        assert desk._LAST[key] == kept  # a failed read is never kept


# -- 13: the PI's decision in English, from the engine's real payload -------------------------------------------


@needs_node
def test_14_6_a_files_proposal_and_a_conflict_read_in_english_in_the_panel(drive):
    """A member proposes a new CSV and a change to data/panel.csv; the PI
    then changes panel.csv too. 「批准以后会怎样」 in English, drawn by
    team.js from GET /api/proposals/<id>'s real payload: the table's rows
    and columns, the size, the both-sides conflict and why it cannot be
    approved -- no Chinese but the researcher's own words. Chinese stays
    the default (the command line, the old pins)."""
    import re

    from test_v8_phase2 import _csv
    from test_v9_language import CJK
    from test_v10_desk_proposals import _run_team

    with drive.member():
        (drive.work / "data" / "new.csv").write_text(_csv(1))
        (drive.work / "data" / "panel.csv").write_text(_csv(97))
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(drive.work, "files", "加数据"))
    with drive.pi():
        (drive.official / "data" / "panel.csv").write_text(_csv(98))
        zh = proposals.open_for(drive.official, stored.id)["changes"]
    by = {f["path"]: f for f in zh["files"]}
    assert by["data/new.csv"]["table_words"] == "1 行，列 id、x"
    assert by["data/panel.csv"]["reason"] == "这个文件在提议之后，正式版本里也改了"
    out = _run_team(_real_pi_case(drive, stored, "en"), _OPEN_P1 % json.dumps("View"), lang="en")
    lines = out["lines"]
    for line in ["desk-note plain: Table: 1 row; columns id, x", "status-label tone-clay: Changed on both sides",
                 "desk-break: The official version also changed this file after the proposal",
                 "node-sec-title: Cannot be approved yet"]:
        assert line in lines, line
    assert any(line.startswith("desk-break: ") and "The official version also changed this file" in line
               for line in lines[lines.index("node-sec-title: Cannot be approved yet"):])
    theirs = "加数据|王|data/new.csv|data/panel.csv|提议/|已发布/"  # their words, the shared folder's fixed names (13.1)
    assert [line for line in lines if CJK.search(re.sub(theirs, "", line))] == []


def test_13_6_a_tables_shape_and_a_files_size_in_english(tmp_path):
    """history.table_shape and the size sentence in English: singular and
    plural, an incomplete count, a sheet, more than twelve columns said as
    how many more -- Chinese unchanged."""
    from researchos import history

    many = [f"c{i}" for i in range(15)]
    assert history.table_shape({"rows": 1, "columns": ["id"]}, "en") == "1 row; column id"
    assert history.table_shape({"rows": 1497, "columns": ["a", "b"], "rows_complete": False}, "en") == \
        "1,497 or more rows; columns a, b"
    assert history.table_shape({"rows": 2, "columns": many}, "en") == "2 rows; columns " + ", ".join(many[:12]) + " and 3 more"
    assert history.table_shape({"rows": 0, "columns": []}, "en") == "0 rows; no columns"
    assert history.table_shape({"sheet_rows": 1, "sheet_columns": 3, "columns": None}, "en") == "1 row, 3 columns"
    assert history.table_shape({"rows": 2, "columns": many}) == "2 行，列 " + "、".join(many[:12]) + "等 15 列"
    assert history.table_shape({"rows": 9, "columns": ["a"], "rows_complete": False}) == "9 以上 行，列 a"


# -- 21: what an approval's publish says on a member's desk ----------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_14_6_a_publish_made_by_an_approval_is_said_in_the_screens_words(drive, lang):
    """The PI approves the member's new attempt, which publishes with
    Research OS's own note 「批准提议 pr-…」 (written as ever). The member's
    desk gets it by its code and the proposal, and 「正式版本有更新」 says the
    PI published on approving their proposal -- its sentence, never the
    Chinese note or the bare id -- in either language."""
    import re

    from researchos import cli, desk

    from test_v8_phase2 import _conn
    from test_v9_language import CJK, _DESK_DOM, _i18n_source, _node

    with drive.member():
        (drive.work / "steps" / "18-big.py").write_text(NEW_SCRIPT)
        (drive.work / "map.md").write_text(_map() + DEAD_18.format(result="t=1.10").replace("☠️ 太慢", "🕒"))
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(drive.work, "attempt_new", "大面板"))
    with drive.pi():
        done = proposals.approve(drive.official, stored.id)
    assert done["published"]["published"] and proposals.approval_note(f"批准提议 {stored.id}") == stored.id
    with drive.member():
        assert cli.main(["ingest", str(drive.work)]) == 0
        conn = _conn(drive.work)
        try:
            data = desk.payload(conn, drive.work, lang=lang)
        finally:
            conn.close()
    last = data["column"]["sync"]["last_snapshot"]
    assert (last["note"], last["note_code"], last["proposal"]) == (f"批准提议 {stored.id}", "approved", stored.id)
    i18n = f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    case = {"desk": data, "me": {"me": {"id": "wang@example.com"}}, "proposals": {"role": "member", "proposals": []},
            "share": {}, "preview": {}}
    lines = _node("global.window = {};\n" + i18n + _DESK_DOM + "global.ROTeam.syncWrites = () => 'W';\n"
                  + (Path(__file__).parents[1] / "src/researchos/webapp/desk.js").read_text(encoding="utf-8")
                  + f"\n(async () => {{ process.stdout.write(JSON.stringify(await renderDesk({json.dumps(case, ensure_ascii=False)}))); }})();\n")
    said = [line for line in lines if line.startswith("desk-sub: ")]
    if lang == "en":
        assert said == ["desk-sub: The PI published version 2 (today " + said[0].split("(today ")[1]]
        assert said[0].endswith("on approving the proposal “大面板”.") and not CJK.search(re.sub("大面板", "", said[0]))
    else:
        assert said[0].startswith("desk-sub: PI 于 今天 ") and said[0].endswith("批准提议「大面板」后发布了第 2 版。")
    assert stored.id not in said[0]


# -- 4: a busy day of writes never rotates the daily snapshots away -----------------------------------------------


def test_9_2_twenty_five_lines_in_one_day_keep_yesterdays_snapshot_and_todays_start(tmp_path):
    """mappings.toml has yesterday's snapshot, then a hand edit; 25 lines
    are drawn today. Yesterday's snapshot survives, today's first snapshot
    is the file as the day started (the hand edit in it), and the copies
    taken before each write -- a series of their own -- stay at 20. The map
    file's writes keep the same discipline."""
    from datetime import datetime, timedelta

    from researchos.ingest import mappings as mappings_ingest
    from researchos.records import files as record_files
    from researchos.webapp import mapedit

    root = _project(tmp_path / "proj")
    path = mappings_ingest.mappings_path(root)
    mappings_ingest.add_mapping(root, "steps/16-build.py", "figs/f0.png", "generates")
    yesterday = datetime.now().astimezone() - timedelta(days=1)
    [old] = [record_files.snapshot_if_first_change_today(root, path, now=lambda: yesterday)]
    path.write_text(path.read_text() + "# 手改的一行\n")
    started = path.read_bytes()
    for i in range(1, 26):
        mappings_ingest.add_mapping(root, "steps/16-build.py", f"figs/f{i}.png", "generates")
    daily = record_files.snapshots(root, path)
    assert daily[0] == old and old.exists() and len(daily) == 2
    assert daily[1].read_bytes() == started
    writes = record_files.snapshots(root, path, writes=True)
    assert len(writes) == mapedit.BACKUP_KEEP and all(w.name.endswith(".write.toml") for w in writes)
    assert record_files.newest_snapshot(root, path, writes=True) == writes[-1]  # the latest backup, either kind

    map_md = root / "map.md"
    before = map_md.read_bytes()
    for verdict in ("✅ 一", "✅ 二", "✅ 三"):
        mapedit.apply_edit(root, "update", "16", {"verdict": verdict})
    [today] = record_files.snapshots(root, map_md)
    assert today.read_bytes() == before and len(record_files.snapshots(root, map_md, writes=True)) == 3

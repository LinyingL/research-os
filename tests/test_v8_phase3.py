"""Research OS V8 phase 3 (DESIGN.md 12.3, 12.4): proposals and approval,
independent of transport -- 12.10 #3 ("two people, simulated: two
RESEARCHOS_HOMEs, a local folder as the shared drive") without the shared
drive's snapshots and sync, which are phase 4. Every test names its
scenario."""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
from pathlib import Path

import pytest

from researchos import cli, db, history, names, people, proposals, trace
from researchos import exchange as exchange_mod
from researchos.records import cards
from researchos.records import decisions as decisions_mod
from researchos.records import variables as V
from researchos.records.identity import read_identity
from researchos.webapp import literature_api, mapedit, server

from test_v8_phase2 import _call, _conn, _map, _project, _scan

PI = ("李", "li@example.com")
MEMBER = ("王", "wang@example.com")
TEAM = (
    '[[member]]\nid = "li@example.com"\nname = "李"\nrole = "pi"\n\n'
    '[[member]]\nid = "wang@example.com"\nname = "王"\nrole = "member"\n'
)
NEW_SCRIPT = 'import pandas as pd\ndf = pd.read_csv("data/panel.csv")\ndf.to_csv("data/out18.csv")\n'
BIG = 200 * 1024 * 1024
ROW_18 = "| 18 | 08-02 | Big panel (18) | TopicShift->RV | t=1.10 | 🕒 |\n"


@contextlib.contextmanager
def _as(monkeypatch, home: Path):
    """Be someone else on this machine: their RESEARCHOS_HOME."""
    old = os.environ.get(names.HOME_ENV_VAR)
    monkeypatch.setenv(names.HOME_ENV_VAR, str(home))
    try:
        yield
    finally:
        monkeypatch.setenv(names.HOME_ENV_VAR, old or "")


class Team:
    def __init__(self, tmp_path: Path, monkeypatch, *, team: bool = True) -> None:
        self.mp = monkeypatch
        self.pi_home = tmp_path / "home-pi"
        self.member_home = tmp_path / "home-member"
        self.exchange = tmp_path / "shared"
        self.exchange.mkdir()
        self.official = tmp_path / "official"
        self.work = tmp_path / "work"
        with self.pi():
            people.write_me(*PI)
            _project(self.official)
            if team:
                (self.official / ".researchos" / "team.toml").write_text(TEAM)
            self.pid = read_identity(self.official).identity.id
            exchange_mod.configure(self.pid, self.exchange, self.official)
        with self.member():
            people.write_me(*MEMBER)
            proposals.start_working_copy(self.official, self.work, exchange=self.exchange)

    def pi(self):
        return _as(self.mp, self.pi_home)

    def member(self):
        return _as(self.mp, self.member_home)


@pytest.fixture
def team(tmp_path, monkeypatch) -> Team:
    return Team(tmp_path, monkeypatch)


def _sparse(path: Path, size: int = BIG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"RAW\x00header")
        handle.truncate(size)


def _member_new_attempt(team: Team) -> None:
    (team.work / "steps" / "18-big.py").write_text(NEW_SCRIPT)
    _sparse(team.work / "data" / "big.bin")
    (team.work / "map.md").write_text(_map() + ROW_18)


def _readers(root: Path) -> dict:
    """Every reader of the official index the app has (12.4: a pending
    proposal is in none of them)."""
    conn = _conn(root)
    try:
        out = {
            "tree": server.tree_payload(conn, root),
            "lineage": server.lineage_payload(conn, root),
            "canvas": server.canvas_payload(conn, root, None),
            "attempts": server.attempts_payload(conn),
            "trace": trace.chain(conn, root, "figs/fig2.png"),
            "citations": literature_api.citations_payload(conn, root),
            "variables": cards.overview(conn, root),
            "nodes": sorted(n["id"] for t in db.NODE_TYPES for n in db.get_nodes_by_type(conn, t)),
            "edges": sorted((e["src"], e["dst"], e["type"]) for e in db.query_edges(conn)),
        }
    finally:
        conn.close()
    return json.loads(json.dumps(out, sort_keys=True, default=str))


def _tree_files(root: Path) -> dict[str, tuple[int, int, int]]:
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            st = path.stat()
            found[str(path.relative_to(root))] = (st.st_ino, st.st_mtime_ns, st.st_size)
    return found


# -- 12.10 #3: a new attempt, invisible until approved -------------------------------------


def test_12_10_3_a_new_attempt_with_a_200mb_file_is_no_node_until_approved(team):
    """12.10 #3: a member proposes a new attempt with a script and a 200 MB
    data file; it is not a node anywhere -- not after a scan, not in any
    reader -- until approved; approved, it is applied, verified by hash,
    and recorded with by = the member and approved_by = the PI."""
    with team.member():
        _member_new_attempt(team)
        draft = proposals.make_proposal(team.work, "attempt_new", "加一条大面板的尝试")
        assert draft.data["attempt"]["number"] == "18"
        assert {f["path"]: (f["action"], f["store"]) for f in draft.data["file"]} == {
            "data/big.bin": ("added", "blob"), "steps/18-big.py": ("added", "text"),
        }
        assert not list(team.exchange.rglob("proposal.toml"))  # a dry run writes nothing
        stored = proposals.write_proposal(team.work, draft)
        assert (team.exchange / "提议" / "wang@example.com" / "blobs").is_dir()

    with team.pi():
        before = _readers(team.official)
        _scan(team.official)
        assert _readers(team.official) == before
        conn = _conn(team.official)
        try:
            assert db.get_node(conn, "attempt:map.md#18") is None
            assert db.get_node(conn, "script:steps/18-big.py") is None
        finally:
            conn.close()
        assert not (team.official / "steps" / "18-big.py").exists()

        inbox = proposals.listing(team.official)
        assert inbox["role"] == "pi" and [p["id"] for p in inbox["proposals"]] == [stored.id]
        changes = proposals.what_changes(stored, team.official)
        assert changes["approvable"] and changes["attempt"]["op"] == "append"
        assert "+| 18 |" in changes["attempt"]["diff"]
        by_path = {f["path"]: f for f in changes["files"]}
        assert by_path["steps/18-big.py"]["state"] == "apply" and "+df.to_csv" in by_path["steps/18-big.py"]["diff"]
        assert "209715200" in by_path["data/big.bin"]["sentence"]

        done = proposals.approve(team.official, stored.id, note="同意，先跑一遍")
        assert done["decision"]["by"] == "wang@example.com" and done["decision"]["approved_by"] == "li@example.com"
        big = team.official / "data" / "big.bin"
        assert big.stat().st_size == BIG and exchange_mod.sha256_of(big)[0] == by_path["data/big.bin"]["sha256"]
        assert (team.official / "steps" / "18-big.py").read_text() == NEW_SCRIPT
        assert "| 18 |" in (team.official / "map.md").read_text()
        conn = _conn(team.official)
        try:
            assert db.get_node(conn, "attempt:map.md#18") is not None
            assert db.get_node(conn, "script:steps/18-big.py") is not None
        finally:
            conn.close()
        entry = decisions_mod.entries(team.official)[-1]
        assert entry["act"] == "approved" and entry["note"] == "同意，先跑一遍" and entry["proposal"] == stored.id
        versions = history.versions(team.official, "steps/18-big.py")["versions"]
        assert versions[0]["who"] == "wang@example.com" and versions[0]["approved_by"] == "li@example.com"
        attempt_versions = history.versions(team.official, "attempt:map.md#18")["versions"]
        assert attempt_versions[0]["source"] == "approval" and attempt_versions[0]["approved_by"] == "li@example.com"
        assert len(attempt_versions) == 1

    with team.member():
        mine = proposals.listing(team.work)
        assert mine["role"] == "member" and mine["proposals"][0]["state"] == "approved"
        assert mine["proposals"][0]["outcomes"][0]["text"] == "同意，先跑一遍"


def test_12_10_3_a_returned_proposal_never_applies_and_shows_its_reason(team):
    """12.10 #3: a returned proposal shows its reason to the member and
    never becomes a node; it takes no further answer."""
    with team.member():
        _member_new_attempt(team)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "attempt_new", "试试"))
    with team.pi():
        official_before = _tree_files(team.official)
        readers_before = _readers(team.official)
        outcome = proposals.return_(team.official, stored.id, "数据来源不清楚，请补上下载说明")
        assert outcome["act"] == "returned"
        assert _tree_files(team.official) == official_before
        assert _readers(team.official) == readers_before
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.approve(team.official, stored.id)
        assert refused.value.code == "decided"
        assert not decisions_mod.decisions_path(team.official).exists()
    with team.member():
        mine = proposals.open_for(team.work, stored.id)
        assert mine["state"] == "returned" and mine["outcomes"][-1]["text"] == "数据来源不清楚，请补上下载说明"
        assert "changes" not in mine


def test_12_10_3_discuss_keeps_the_proposal_open(team):
    """12.10 #3 / 12.3 「讨论」: a note to the member; the proposal stays open."""
    with team.member():
        _member_new_attempt(team)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "attempt_new", "试试"))
    with team.pi():
        proposals.discuss(team.official, stored.id, "能先说明一下样本区间吗？")
        listed = proposals.listing(team.official)["proposals"][0]
        assert listed["state"] == "pending" and listed["outcomes"][0]["act"] == "discussed"
        proposals.approve(team.official, stored.id)
    with team.member():
        assert proposals.listing(team.work)["proposals"][0]["state"] == "approved"


# -- 12.10 #3: abandonment ---------------------------------------------------------------


def test_12_10_3_abandoning_without_a_reason_is_refused(team):
    """12.10 #3: a verdict change to ☠️ without a reason is refused -- at the
    member's side, and at approval for a proposal that left it out."""
    with team.member():
        (team.work / "map.md").write_text(_map("☠️ 伪协整"))
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.make_proposal(team.work, "verdict_change", "放弃 16")
        assert refused.value.code == "reason_required"
        draft = proposals.make_proposal(team.work, "verdict_change", "放弃 16", reason="残差不平稳")
        assert draft.data["verdict"]["abandons"] == "yes"
        del draft.data["verdict"]["reason"], draft.data["verdict"]["abandons"]
        stored = proposals.write_proposal(team.work, draft)
    with team.pi():
        changes = proposals.what_changes(stored, team.official)
        assert not changes["approvable"] and any("理由" in p for p in changes["problems"])
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.approve(team.official, stored.id)
        assert refused.value.code == "not_approvable"
        assert "☠" not in (team.official / "map.md").read_text()


def test_12_10_3_abandoning_with_a_reason_freezes_the_evidence(team):
    """12.10 #3 / 12.4: approving an abandonment records the result and the
    reason and freezes the attempt's evidence -- its step scripts and the
    outputs they wrote, by fingerprint and (text) by content -- in
    decisions.toml; the evidence stays readable after the script changes."""
    with team.member():
        (team.work / "map.md").write_text(_map("☠️ 伪协整").replace("t=2.91", "t=0.41"))
        stored = proposals.write_proposal(
            team.work, proposals.make_proposal(team.work, "verdict_change", "放弃 16", reason="残差不平稳，协整不成立"),
        )
    with team.pi():
        done = proposals.approve(team.official, stored.id, note="同意放弃")
        assert "☠️ 伪协整" in (team.official / "map.md").read_text()
        entry = decisions_mod.entries(team.official)[-1]
        assert entry["act"] == "abandoned" and entry["attempt"] == "attempt:map.md#16"
        assert entry["reason"] == "残差不平稳，协整不成立" and entry["result"] == "t=0.41"
        assert entry["by"] == "wang@example.com" and entry["approved_by"] == "li@example.com"
        evidence = {e["path"]: e for e in entry["evidence"]}
        assert evidence["steps/16-build.py"]["role"] == "script" and evidence["steps/16-build.py"]["state"] == "kept"
        assert evidence["data/panel.csv"]["role"] == "output"
        assert done["applied"]["evidence"] == entry["evidence"]
        old_sha = evidence["steps/16-build.py"]["sha256"]
        (team.official / "steps" / "16-build.py").write_text("# rewritten\n")
        with history.Store.open(team.pid) as store:
            assert store.read_blob(old_sha).decode() == (team.work / "steps" / "16-build.py").read_text()


# -- 12.10 #3: both sides changed one file ----------------------------------------------------


def test_12_10_3_both_sides_changing_one_file_is_listed_and_not_approved(team):
    """12.10 #3: a file changed on both sides since the base -- the PI is
    told which, approving is refused, nothing of the proposal is applied
    (not even its other files), and no merge happens."""
    with team.member():
        (team.work / "steps" / "16-build.py").write_text("# member's edit\n" + (team.work / "steps" / "16-build.py").read_text())
        (team.work / "notes.md").write_text("member notes\n")
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "files", "改一下构造脚本"))
    with team.pi():
        pi_edit = "# PI's edit\n" + (team.official / "steps" / "16-build.py").read_text()
        (team.official / "steps" / "16-build.py").write_text(pi_edit)
        changes = proposals.what_changes(stored, team.official)
        assert [c["path"] for c in changes["conflicts"]] == ["steps/16-build.py"]
        assert changes["moved"] and not changes["approvable"]
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.approve(team.official, stored.id)
        assert refused.value.code == "not_approvable" and refused.value.details["conflicts"]
        assert (team.official / "steps" / "16-build.py").read_text() == pi_edit
        assert not (team.official / "notes.md").exists()
        assert proposals.listing(team.official)["proposals"][0]["state"] == "pending"


def test_12_10_3_a_changed_file_approved_keeps_the_replaced_version(team):
    """12.3: a changed file is copied in; the replaced version is kept
    (backups/approved, and the history store) and the diff was shown."""
    with team.member():
        original = (team.work / "steps" / "16-build.py").read_text()
        (team.work / "steps" / "16-build.py").write_text(original + "df.describe()\n")
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "files", "加一行"))
    with team.pi():
        changes = proposals.what_changes(stored, team.official)
        assert "+df.describe()" in changes["files"][0]["diff"]
        proposals.approve(team.official, stored.id)
        assert (team.official / "steps" / "16-build.py").read_text().endswith("df.describe()\n")
        kept = team.official / ".researchos" / "backups" / "approved" / stored.id / "steps" / "16-build.py"
        assert kept.read_text() == original
        whos = [v["who"] for v in history.versions(team.official, "steps/16-build.py")["versions"]]
        assert whos[0] == "wang@example.com" and None in whos[1:]


# -- variable versions ----------------------------------------------------------------------


CARD = (
    'name = "TopicShift（叙事更替）"\naliases = ["TopicShift"]\n\n'
    "meaning = '''相邻两月新闻主题分布的差异'''\nunit = \"无量纲\"\ngranularity = \"月\"\n\n"
    '[[input]]\ndataset = "data/raw.csv"\nfields = ["id"]\n\n'
    "[construction]\nformula = '''TS = 1 - cos'''\n\n"
    '[implementation]\nscript = "steps/16-build.py"\noutput = "data/panel.csv"\nfield = "x"\n\n'
    "[decision]\nwhy = '''余弦距离'''\n"
)


def test_12_10_3_a_variable_version_is_confirmed_when_approved(team):
    """12.4: a variable's new definition version is a proposal; approving
    writes it as the card's next version file and confirms it through the
    card's own path, attested from the proposal, with by and approved_by."""
    with team.member():
        card_dir = team.work / ".researchos" / "variables" / "topicshift"
        card_dir.mkdir(parents=True)
        (card_dir / "v1.toml").write_text(CARD)
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.make_proposal(team.work, "variable_version", "TopicShift 的定义")
        assert refused.value.code == "attested_required"
        stored = proposals.write_proposal(
            team.work, proposals.make_proposal(team.work, "variable_version", "TopicShift 的定义", attested="unknown"),
        )
    with team.pi():
        assert not V.find_card_dirs(team.official, "topicshift")
        changes = proposals.what_changes(stored, team.official)
        assert changes["approvable"] and changes["variable"]["number"] == 1
        done = proposals.approve(team.official, stored.id, note="可以")
        card = V.open_card(team.official, "topicshift")
        assert (card.directory / "v1.toml").read_text() == CARD and card.in_use == 1
        confirmed = card.entries[-1]
        assert confirmed.get("act") == "confirmed" and confirmed.get("attested") == "unknown"
        assert confirmed.get("by") == "wang@example.com" and confirmed.get("approved_by") == "li@example.com"
        assert confirmed.get("proposal") == stored.id and confirmed.get("note") == "可以"
        assert done["decision"]["card"] == "topicshift"


# -- single person ----------------------------------------------------------------------------


def test_12_10_3_a_single_person_project_needs_no_proposal(tmp_path, monkeypatch):
    """12.1 / 12.10 #3: a project without members has no approval step:
    proposing and approving are refused as unnecessary, and a change to the
    attempt table applies directly, as before."""
    root = _project(tmp_path / "solo")
    people.write_me(*PI)
    with pytest.raises(proposals.ProposalRefused) as refused:
        proposals.approve(root, "pr-" + "0" * 32)
    assert refused.value.code == "single_person"
    assert proposals.listing(root)["role"] == "single"
    mapedit.apply_edit(root, "update", "16", {"verdict": "✅"})
    conn = _conn(root)
    try:
        assert db.get_node(conn, "attempt:map.md#16")["human_fields"]["verdict"] == "✅"
    finally:
        conn.close()
    (root / ".researchos" / "team.toml").write_text('[[member]]\nid = "li@example.com"\nname = "李"\nrole = "pi"\n')
    assert proposals.listing(root)["role"] == "single"


def test_12_10_3_only_the_pi_approves_and_only_members_propose(team):
    """12.3: approving and returning are refused unless me is the team's
    pi; a PI does not propose; someone outside the team sees nothing."""
    with team.member():
        _member_new_attempt(team)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "attempt_new", "x"))
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.approve(team.work, stored.id)
        assert refused.value.code == "not_pi"
    with team.pi():
        (team.official / "README.md").write_text("pi edit\n")
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.make_proposal(team.official, "files", "x")
        assert refused.value.code == "pi_changes_directly"
        people.write_me("外人", "zhao@example.com")
        assert proposals.listing(team.official) == {"role": "none", "me": "zhao@example.com", "proposals": [], "problems": []}
        with pytest.raises(proposals.ProposalRefused):
            proposals.return_(team.official, stored.id, "no")


# -- the exchange: written once, half-written ignored, conflict copies not read ---------------


def test_12_10_3_the_exchange_is_written_once_and_never_edited(team):
    """12.10 #5 (for the proposals and outcomes this phase writes): after a
    proposal, a discussion and an approval, every file in the exchange was
    created once and never modified -- inode and mtime unchanged."""
    with team.member():
        _member_new_attempt(team)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "attempt_new", "x"))
    first = _tree_files(team.exchange)
    with team.pi():
        proposals.discuss(team.official, stored.id, "ok?")
        second = _tree_files(team.exchange)
        proposals.approve(team.official, stored.id)
    third = _tree_files(team.exchange)
    for earlier, later in ((first, second), (second, third)):
        for path, stat in earlier.items():
            assert later[path] == stat, path
    assert not any(".tmp-" in p for p in third)


def test_12_10_3_half_written_and_conflict_copies_are_reported_never_read(team):
    """12.10 #3: a half-written proposal (no ready.toml) is ignored; a sync
    conflict copy in the exchange is reported and never read."""
    with team.member():
        _member_new_attempt(team)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "attempt_new", "x"))
    area = team.exchange / "提议" / "wang@example.com"
    half = area / ("pr-" + "a" * 32)
    half.mkdir()
    (half / "proposal.toml").write_text('id = "x"\n')
    (area / f"{stored.id} 2").mkdir()
    (stored.directory / "proposal-MacBook.toml").write_text("garbage")
    with team.pi():
        listed = proposals.listing(team.official)
        assert [p["id"] for p in listed["proposals"]] == [stored.id]
        kinds = {Path(p["path"]).name: p["kind"] for p in listed["problems"]}
        assert kinds[half.name] == "half_written"
        assert kinds[f"{stored.id} 2"] == "unexpected" and kinds["proposal-MacBook.toml"] == "unexpected"
        with pytest.raises(exchange_mod.ExchangeError):
            proposals.approve(team.official, half.name)


def test_12_10_3_the_exchange_may_not_sit_inside_the_project(team):
    """12.4 (not a node until approved): an exchange inside the project
    would put a proposal's files in front of the scan -- refused."""
    with team.pi():
        with pytest.raises(exchange_mod.ExchangeError) as refused:
            exchange_mod.configure(team.pid, team.official / "shared", team.official)
        assert refused.value.code == "inside_project"


def test_12_10_3_an_interrupted_approval_is_resumed_without_applying_twice(team, monkeypatch):
    """12.3: an approval stopped after applying but before its outcome
    leaves the proposal pending; approving again finds what was applied
    and writes one decision, one row, one outcome."""
    with team.member():
        _member_new_attempt(team)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "attempt_new", "x"))
    with team.pi():
        real = proposals._outcome
        monkeypatch.setattr(proposals, "_outcome", lambda *a, **k: (_ for _ in ()).throw(OSError("drive gone")))
        with pytest.raises(OSError):
            proposals.approve(team.official, stored.id, rescan=False)
        monkeypatch.setattr(proposals, "_outcome", real)
        assert proposals.listing(team.official)["proposals"][0]["state"] == "pending"
        changes = proposals.what_changes(stored, team.official)
        assert changes["attempt"]["state"] == "same" and {f["state"] for f in changes["files"]} == {"same"}
        proposals.approve(team.official, stored.id)
        assert (team.official / "map.md").read_text().count("| 18 |") == 1
        assert len(decisions_mod.entries(team.official)) == 1
        assert proposals.listing(team.official)["proposals"][0]["state"] == "approved"


# -- CLI ------------------------------------------------------------------------------------


def test_12_10_3_cli_propose_dry_run_then_yes_and_the_pi_commands(team, capsys):
    """12.10 #3 through the command line: `researchos propose` is a dry run
    without --yes; `proposals`, `approve`, `return`, `discuss`."""
    with team.member():
        _member_new_attempt(team)
        assert cli.main(["propose", "attempt_new", "--sentence", "大面板", str(team.work)]) == 0
        out = capsys.readouterr().out
        assert "Dry run: nothing written" in out and "steps/18-big.py" in out
        assert not list(team.exchange.rglob("ready.toml"))
        assert cli.main(["propose", "attempt_new", "--sentence", "大面板", "--yes", str(team.work)]) == 0
        pid = re.search(r"Proposal (pr-[0-9a-f]{32}) written", capsys.readouterr().out).group(1)
        assert cli.main(["proposals", str(team.work)]) == 0
        assert pid in capsys.readouterr().out
        assert cli.main(["approve", pid, str(team.work)]) == 1
        assert "only the PI" in capsys.readouterr().err
    with team.pi():
        assert cli.main(["proposals", "--show", pid, str(team.official)]) == 0
        assert "approvable" in capsys.readouterr().out
        assert cli.main(["discuss", pid, "--text", "样本区间？", str(team.official)]) == 0
        assert cli.main(["approve", pid, "--note", "好", str(team.official)]) == 0
        out = capsys.readouterr().out
        assert "approved_by li@example.com" in out and "steps/18-big.py" in out
        assert cli.main(["return", pid, "--reason", "late", str(team.official)]) == 1
        assert "approved" in capsys.readouterr().err


# -- HTTP -----------------------------------------------------------------------------------


@contextlib.contextmanager
def _serve(root: Path):
    httpd = server.build_server(root, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_12_10_3_http_create_list_and_decide(team):
    """12.10 #3 through the app's endpoints: the member creates (dry run,
    then written against the shown fingerprint) and sees 我的提议; the
    member may not approve; the PI's inbox, the proposal with what
    approving would do, and approve."""
    with team.member():
        _member_new_attempt(team)
        with _serve(team.work) as base:
            status, body = _call(f"{base}/api/proposals", "POST", {"kind": "attempt_new", "sentence": "大面板"})
            assert status == 200 and body["written"] is False and body["proposal"]["attempt"]["number"] == "18"
            status, written = _call(f"{base}/api/proposals", "POST", {"kind": "attempt_new", "sentence": "大面板",
                                                                      "write": True, "expected": body["fingerprint"]})
            assert status == 200 and written["written"] is True
            pid = written["id"]
            status, body = _call(f"{base}/api/proposals")
            assert status == 200 and body["role"] == "member" and body["proposals"][0]["id"] == pid
            status, body = _call(f"{base}/api/proposals/{pid}/approve", "POST", {})
            assert status == 403 and body["state"] == "not_pi" and body["message"] == "只有 PI 可以批准或退回提议"
    with team.pi():
        with _serve(team.official) as base:
            status, body = _call(f"{base}/api/proposals/{pid}")
            assert status == 200 and body["changes"]["approvable"] is True
            status, body = _call(f"{base}/api/proposals/{pid}/approve", "POST", {"note": "好"})
            assert status == 200 and body["decision"]["approved_by"] == "li@example.com"
            status, body = _call(f"{base}/api/proposals/{pid}/return", "POST", {"reason": "x"})
            assert status == 409 and body["state"] == "decided"
            status, body = _call(f"{base}/api/proposals/pr-{'0' * 32}")
            assert status == 404 and body["state"] == "exchange_no_such_proposal"


@pytest.mark.parametrize("method, path, body", [
    ("GET", "/api/proposals", None),
    ("GET", "/api/proposals/pr-" + "0" * 32, None),
    ("POST", "/api/proposals", {"kind": "files", "sentence": "x", "write": True}),
    ("POST", "/api/proposals/pr-" + "0" * 32 + "/approve", {}),
    ("POST", "/api/proposals/pr-" + "0" * 32 + "/return", {"reason": "x"}),
    ("POST", "/api/proposals/pr-" + "0" * 32 + "/discuss", {"text": "x"}),
])
def test_12_10_3_every_proposal_endpoint_is_origin_checked(team, method, path, body):
    """12.10 #3 (every new endpoint origin-checked first): a cross-origin
    request is refused with 403 and nothing is written."""
    with team.pi():
        before = _tree_files(team.exchange)
        with _serve(team.official) as base:
            status, payload = _call(base + path, method, body, headers={"Origin": "http://evil.example"})
        assert status == 403 and "Origin" in payload["error"]
        assert _tree_files(team.exchange) == before


def test_12_10_3_an_unreadable_decisions_file_refuses_the_approval_before_anything(team):
    """9.3's trust rules on decisions.toml: a sync conflict copy beside it
    refuses the approval, and nothing of the proposal is applied."""
    with team.member():
        _member_new_attempt(team)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "attempt_new", "x"))
    with team.pi():
        path = decisions_mod.decisions_path(team.official)
        (path.parent / "decisions 2.toml").write_text("")
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.approve(team.official, stored.id)
        assert refused.value.code == "not_approvable" and "冲突副本" in refused.value.message_zh
        assert not (team.official / "steps" / "18-big.py").exists() and "| 18 |" not in (team.official / "map.md").read_text()


def test_12_10_3_a_link_planted_in_a_proposal_is_never_read(team, tmp_path):
    """12.2 (nothing unexpected is read): a proposal whose content is a link
    out of the exchange is a problem, never copied into the official version."""
    with team.member():
        (team.work / "steps" / "19-x.py").write_text("print(1)\n")
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "files", "x"))
    secret = tmp_path / "secret.py"
    secret.write_text("print(1)\n")
    content = stored.directory / "files" / "steps" / "19-x.py"
    content.unlink()
    content.symlink_to(secret)
    with team.pi():
        changes = proposals.what_changes(stored, team.official)
        assert not changes["approvable"] and "link" in changes["problems"][0]
        with pytest.raises(proposals.ProposalRefused):
            proposals.approve(team.official, stored.id)
        assert not (team.official / "steps" / "19-x.py").exists()

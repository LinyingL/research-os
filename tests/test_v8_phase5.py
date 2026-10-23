"""Research OS V8 phase 5: the engine usable end to end from the command
line for the two-person trial -- `researchos status` says who I am, my role,
the shared folder and how far my copy is behind, the proposals, notes and
history (12.1-12.6); `researchos collaborate` walks the PI or a member
through the steps in order, refuses one out of order and is a dry run
unless --yes; and the gaps the command-line path showed (approve says what
it published, history says who approved, `note list PATH`). Every test
names its 12.10 scenario."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos import cli, people, proposals, share, signing
from researchos import exchange as exchange_mod
from researchos.records.identity import read_identity

from test_v8_phase2 import _map, _project
from test_v8_phase4 import BIG, MEMBER, PI, Drive, _as, _forge_snapshot, _member_new_attempt, _other_key, _propose

needs_ssh = pytest.mark.skipif(signing.tool() is None, reason="ssh-keygen is not installed here")


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Drive:
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    return Drive(tmp_path, monkeypatch)


def _run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main([str(a) for a in argv])
    got = capsys.readouterr()
    return code, got.out, got.err


# -- researchos status (item 1) ----------------------------------------------------------------------


def test_12_10_2_status_of_one_person_says_no_approval_step_and_counts_notes_and_history(tmp_path, capsys):
    """12.10 #2 (one person): status says who I am (or that it is not set),
    that there is no approval step, and how many notes and versions are
    kept."""
    root = _project(tmp_path / "proj")
    code, out, _ = _run(capsys, "status", root)
    assert code == 0
    assert "Who I am: not set on this Mac" in out and "researchos me set --name NAME --email EMAIL" in out
    assert "My role: one person -- no approval step" in out
    assert "Notes: none yet" in out and "History: " in out and "kept by Research OS" in out
    assert "Shared folder" not in out  # one person, nothing shared: nothing to say
    people.write_me(*PI)
    assert _run(capsys, "note", "add", "steps/16-build.py", "先跑这个", root)[0] == 0
    code, out, _ = _run(capsys, "status", root)
    assert f"Who I am: {PI[0]} <{PI[1]}>" in out and "Notes: 1 note on 1 node" in out


def test_12_10_2_status_never_fails_on_an_unreadable_team_file(tmp_path, capsys):
    """12.10 #2: a team file that cannot be read is said ("unknown"), never
    taken for one person, and status still answers."""
    root = _project(tmp_path / "proj")
    people.team_path(root).write_text("[[member]]\nid = 3\n", encoding="utf-8")
    code, out, _ = _run(capsys, "status", root)
    assert code == 0 and "My role: unknown -- .researchos/team.toml cannot be read" in out


@needs_ssh
def test_12_10_3_status_says_role_shared_folder_behind_and_proposals(drive, capsys):
    """12.10 #3: the member's status says the snapshot their copy is at, how
    many verified snapshots it is behind and the command that syncs; the
    PI's says what waits for a decision; a returned proposal's reason reaches
    the member's status."""
    with drive.pi():
        code, out, _ = _run(capsys, "status", drive.official)
        assert code == 0
        assert "My role: the PI -- you approve proposals and publish the official version (team: you and 1 member)" in out
        assert f"Shared folder: {drive.shared}" in out
        assert "Last snapshot: 1, published" in out and "signature verified with the PI's key" in out
        assert "Proposals: none waiting for your decision" in out
        (drive.official / "paper.md").write_text("# Results\n\nchanged by the PI\n")
        code, out, _ = _run(capsys, "status", drive.official)
        assert "Not yet published: 1 file of the official version changed since the last snapshot" in out
        assert share.publish(drive.official)["seq"] == 2
        (drive.official / "paper.md").write_text("# Results\n\nchanged again\n")
        assert share.publish(drive.official)["seq"] == 3
    with drive.member():
        code, out, _ = _run(capsys, "status", drive.work)
        assert code == 0
        assert f"My role: a member -- your changes reach the official version as proposals the PI ({PI[1]}) approves" in out
        assert "Your copy: snapshot 1 -- 2 snapshots behind the latest (3): researchos share sync" in out
        assert "My proposals: none" in out
        assert share.sync(drive.work)["seq"] == 3
        _member_new_attempt(drive)
    stored = _propose(drive)
    with drive.pi():
        code, out, _ = _run(capsys, "status", drive.official)
        assert f"Proposals: 1 waiting for your decision -- researchos inbox {drive.official}" in out
        proposals.return_(drive.official, stored.id, "数据来源不清楚")
    with drive.member():
        code, out, _ = _run(capsys, "status", drive.work)
        assert "Your copy: snapshot 3 -- up to date" in out
        assert "My proposals: 1 returned" in out and f"returned {stored.id}: 数据来源不清楚" in out


@needs_ssh
def test_12_10_3_status_reports_a_forged_snapshot_in_plain_words(drive, tmp_path, monkeypatch, capsys):
    """12.10 #3: a snapshot written by someone other than the PI is refused,
    and status says so in plain words; the last good one is kept."""
    forger, _key = _other_key(tmp_path, monkeypatch)
    seq = _forge_snapshot(drive, sign_home=forger)
    with drive.member():
        code, out, _ = _run(capsys, "status", drive.work)
    assert code == 0
    assert (f"Refused snapshot {seq}: its signature is not the PI's key, or it was changed after it was signed -- "
            f"not used; the last good one is kept") in out
    assert "Your copy: snapshot 1 -- up to date" in out


@needs_ssh
def test_12_10_3_a_joined_copy_not_yet_in_the_team_is_not_called_one_person(tmp_path, monkeypatch, capsys):
    """12.10 #3 (join): before the PI adds the member, the joined copy's team
    lists only the PI; status says the member is not in the team yet and
    what to do -- never "one person, no approval step"."""
    shared, official, work = tmp_path / "shared", tmp_path / "official", tmp_path / "work"
    shared.mkdir()
    with _as(monkeypatch, tmp_path / "home-pi"):
        people.write_me(*PI)
        _project(official)
        share.team_init(official)
        exchange_mod.configure(read_identity(official).identity.id, shared, official)
        assert share.publish(official)["published"]
    with _as(monkeypatch, tmp_path / "home-member"):
        people.write_me(*MEMBER)
        share.join(shared, work)
        code, out, _ = _run(capsys, "status", work)
    assert code == 0 and "one person" not in out
    assert "My role: not in this project's team yet" in out and "researchos me key --out FILE" in out
    assert "Your copy: snapshot 1 -- up to date" in out


# -- researchos collaborate (item 2) -------------------------------------------------------------------


def test_12_10_3_collaborate_needs_a_role_and_is_a_dry_run_by_default(tmp_path, capsys):
    """12.10 #3 (guided): outside any project the role must be said; a step
    shown without --yes writes nothing."""
    folder = tmp_path / "empty"
    folder.mkdir()
    code, _, err = _run(capsys, "collaborate", folder)
    assert code == 1 and "--as pi" in err and "--as member" in err
    code, out, _ = _run(capsys, "collaborate", "--as", "pi", folder)
    assert code == 0 and "Next: researchos me set --name NAME --email EMAIL" in out
    code, out, _ = _run(capsys, "collaborate", "me", "--as", "pi", "--name", "李", "--email", "li@example.com", folder)
    assert code == 0 and "Would run: researchos me set --name" in out and "Dry run: nothing written" in out
    assert people.read_me().state == "absent"
    code, _, err = _run(capsys, "collaborate", "me", "--as", "pi", "--yes", folder)
    assert code == 1 and "needs --name, --email" in err and people.read_me().state == "absent"


def test_12_10_3_collaborate_refuses_a_step_out_of_order_with_the_reason(tmp_path, capsys):
    """12.10 #3 (guided): publishing before the shared folder is set is
    refused, naming the step that is missing and its command; nothing is
    written."""
    root = _project(tmp_path / "proj")
    people.write_me(*PI)
    code, _, err = _run(capsys, "collaborate", "publish", "--yes", root)
    assert code == 1
    assert "step 'publish' comes after step 3 'team', which is not done yet" in err
    assert f"first: researchos team init {root}" in err
    assert not people.team_path(root).exists()
    code, _, err = _run(capsys, "collaborate", "nonsense", root)
    assert code == 1 and "there is no step 'nonsense'" in err
    code, out, _ = _run(capsys, "collaborate", "--json", root)
    shown = json.loads(out)
    assert code == 0 and shown["role"] == "pi" and shown["next"] == "team"
    assert [s["name"] for s in shown["steps"]] == ["me", "init", "team", "share", "publish", "member"]
    assert shown["steps"][2]["command"] == f"researchos team init {root}"


@needs_ssh
def test_12_10_3_collaborate_walks_the_pi_and_a_member_through_every_step(tmp_path, monkeypatch, capsys):
    """12.10 #3 (guided, end to end in one process): the PI -- me, init,
    team, share, publish, member; a member -- me, key, join, added -- each
    run by `collaborate STEP --yes`, each step done afterwards; the role is
    read from the project once it says so; the daily loop is printed when
    everything is set up."""
    pi_home, member_home = tmp_path / "home-pi", tmp_path / "home-member"
    shared = tmp_path / "shared"
    shared.mkdir()
    official = tmp_path / "official"
    (official / "steps").mkdir(parents=True)
    (official / "steps" / "a.py").write_text("print(1)\n")
    work = tmp_path / "work"
    keyfile = tmp_path / "wang.pub"
    with _as(monkeypatch, pi_home):
        assert _run(capsys, "collaborate", "me", "--as", "pi", "--name", PI[0], "--email", PI[1], "--yes", official)[0] == 0
        assert _run(capsys, "collaborate", "init", "--as", "pi", "--yes", official)[0] == 0
        for step, extra in (("team", []), ("share", ["--shared", shared]), ("publish", [])):
            code, out, err = _run(capsys, "collaborate", step, *extra, "--yes", official)
            assert code == 0, err
        code, out, _ = _run(capsys, "collaborate", official)
        assert "Next: researchos team add EMAIL --name NAME --key KEYFILE" in out
    with _as(monkeypatch, member_home):
        assert _run(capsys, "collaborate", "me", "--as", "member", "--name", MEMBER[0], "--email", MEMBER[1], "--yes")[0] == 0
        assert _run(capsys, "collaborate", "key", "--as", "member", "--out", keyfile, "--yes")[0] == 0
        assert keyfile.is_file()
        code, out, err = _run(capsys, "collaborate", "join", "--as", "member", "--shared", shared, "--folder", work, "--yes")
        assert code == 0, err
        code, out, _ = _run(capsys, "collaborate", work)
        assert "[    ] 4 added" in out and f"Next: researchos share sync {work}" in out
    with _as(monkeypatch, pi_home):
        code, out, err = _run(capsys, "collaborate", "member", "--member", MEMBER[1], "--member-name", MEMBER[0],
                              "--key", keyfile, "--yes", official)
        assert code == 0, err
        assert "Then: then publish" in out
        code, out, _ = _run(capsys, "collaborate", official)
        assert "added but not published yet" in out
        assert _run(capsys, "collaborate", "member", "--yes", official)[0] == 0
        code, out, _ = _run(capsys, "collaborate", official)
        assert "Set up. The daily loop:" in out and f"researchos inbox {official}" in out
        assert out.count("[done]") == 6
        code, _, err = _run(capsys, "collaborate", "--as", "member", official)
        assert code == 1 and "makes you the PI here" in err
    with _as(monkeypatch, member_home):
        assert _run(capsys, "collaborate", "added", "--yes", work)[0] == 0
        code, out, _ = _run(capsys, "collaborate", work)
        assert out.count("[done]") == 4 and "Set up. The daily loop:" in out
        code, out, _ = _run(capsys, "status", work)
        assert "My role: a member" in out and "Your copy: snapshot 2 -- up to date" in out


# -- what the command-line path showed (item 4) ---------------------------------------------------------


@needs_ssh
def test_12_10_3_approve_says_which_snapshot_it_published_and_history_who_approved(drive, capsys):
    """12.10 #3 through the CLI: `researchos approve` prints the snapshot the
    approval published (the member sees nothing until one carries it), and
    `researchos history` names who approved a version."""
    with drive.member():
        _member_new_attempt(drive)
    stored = _propose(drive)
    with drive.pi():
        code, out, err = _run(capsys, "approve", stored.id, "--note", "同意", drive.official)
        assert code == 0, err
        assert "Published snapshot 2 for the members: signed" in out
        code, out, _ = _run(capsys, "history", "steps/18-big.py", drive.official)
        assert f"{MEMBER[1]} (approved by {PI[1]})" in out
    assert (drive.work / "data" / "big.bin").stat().st_size == BIG


@needs_ssh
def test_12_10_3_approve_says_when_publishing_after_it_failed(drive, capsys, monkeypatch):
    """12.10 #3: the approval stands when publishing fails afterwards; the
    CLI says it was NOT published and gives the command to publish."""
    with drive.member():
        _member_new_attempt(drive)
    stored = _propose(drive)

    def refuse(*_a, **_k):
        raise share.ShareError("unreachable", "the shared folder went away")

    monkeypatch.setattr(share, "publish", refuse)
    with drive.pi():
        code, out, _ = _run(capsys, "approve", stored.id, drive.official)
    assert code == 0
    assert "NOT published (unreachable: the shared folder went away)" in out
    assert f"researchos share publish {drive.official}" in out


def test_12_10_2_note_list_takes_the_project_as_its_only_argument(tmp_path, capsys, monkeypatch):
    """12.10 #2 through the CLI: `researchos note list PATH` lists the
    project's notes (a folder is never a node); `note list NODE PATH` and
    `note list NODE` from inside the project still work."""
    root = _project(tmp_path / "proj")
    assert _run(capsys, "note", "add", "data/panel.csv", "96 行", root)[0] == 0
    code, out, err = _run(capsys, "note", "list", root)
    assert code == 0, err
    assert "Notes: 1" in out and "96 行" in out
    code, out, _ = _run(capsys, "note", "list", "data/panel.csv", root)
    assert code == 0 and "Notes on dataset:data/panel.csv: 1" in out
    monkeypatch.chdir(root)
    code, out, _ = _run(capsys, "note", "list", "data/panel.csv")
    assert code == 0 and "Notes on dataset:data/panel.csv: 1" in out
    code, out, _ = _run(capsys, "note", "list")
    assert code == 0 and "Notes: 1" in out


def test_12_10_2_status_counts_versions_from_the_store(tmp_path, capsys):
    """12.10 #2: the history line counts what the version store keeps."""
    root = _project(tmp_path / "proj")
    (root / "map.md").write_text(_map("✅"))
    assert _run(capsys, "attempts", root)[0] == 0
    code, out, _ = _run(capsys, "status", root)
    line = next(l for l in out.splitlines() if l.startswith("History: "))
    versions = int(line.split()[1])
    assert versions >= 8 and "kept by Research OS" in line


@needs_ssh
def test_12_10_3_a_sync_from_the_cli_scans_the_working_copy(drive, capsys):
    """12.10 #3 through the CLI: after `researchos share sync` brings a new
    script, the member's own index knows it at once (no separate ingest)."""
    with drive.pi():
        (drive.official / "steps" / "20-new.py").write_text('import pandas as pd\npd.read_csv("data/raw.csv")\n')
        assert share.publish(drive.official)["seq"] == 2
    with drive.member():
        assert _run(capsys, "query", "script:steps/20-new.py", drive.work)[0] == 1
        code, out, err = _run(capsys, "share", "sync", drive.work)
        assert code == 0 and "Synced snapshot 1 -> 2" in out, err
        assert _run(capsys, "query", "script:steps/20-new.py", drive.work)[0] == 0

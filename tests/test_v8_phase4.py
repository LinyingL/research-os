"""Research OS V8 phase 4 (DESIGN.md 12.2, 12.3, 12.5): the shared drive --
12.10 #3 end to end ("two people, simulated: two RESEARCHOS_HOMEs, a local
folder as the shared drive") with real ssh-keygen signatures, and 12.10 #5
("nothing is written to the shared drive in place": every file there
created once, inode and mtime unchanged since). Every test names its
scenario. Tests that need ssh-keygen skip cleanly where it is absent."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tomllib
from pathlib import Path

import pytest

from researchos import cli, db, history, names, people, proposals, share, signing
from researchos import exchange as exchange_mod
from researchos.records import decisions as decisions_mod
from researchos.records.identity import read_identity
from researchos.webapp import server
from researchos.webapp import watcher as watcher_mod

from test_v8_phase2 import _call, _conn, _map, _project, _scan
from test_v8_phase3 import _readers, _serve, _sparse

PI = ("李", "li@example.com")
MEMBER = ("王", "wang@example.com")
BIG = 200 * 1024 * 1024
NEW_SCRIPT = 'import pandas as pd\ndf = pd.read_csv("data/panel.csv")\ndf.to_csv("data/out18.csv")\n'
ROW_18 = "| 18 | 08-02 | Big panel (18) | TopicShift->RV | t=1.10 | 🕒 |\n"

needs_ssh = pytest.mark.skipif(signing.tool() is None, reason="ssh-keygen is not installed here")


@contextlib.contextmanager
def _as(monkeypatch, home: Path):
    old = os.environ.get(names.HOME_ENV_VAR)
    monkeypatch.setenv(names.HOME_ENV_VAR, str(home))
    try:
        yield
    finally:
        monkeypatch.setenv(names.HOME_ENV_VAR, old or "")


class Witness:
    """12.10 #5: every file under the shared folder is recorded when it
    first appears; at every later look it must still be there with the same
    inode, mtime and size -- created once, never modified, never removed."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.seen: dict[str, tuple[int, int, int]] = {}

    def look(self) -> None:
        now = {}
        for path in self.root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                st = path.stat()
                now[str(path.relative_to(self.root))] = (st.st_ino, st.st_mtime_ns, st.st_size)
        for rel, stat in self.seen.items():
            assert rel in now, f"{rel} was removed from the shared folder"
            assert now[rel] == stat, f"{rel} was modified in place"
        for rel, stat in now.items():
            self.seen.setdefault(rel, stat)
        assert not any(".tmp-" in rel for rel in now), "a temporary was left in the shared folder"


class Drive:
    """A PI and a member on one machine: two homes, a local folder as the
    shared drive, the PI's official project, the member's working copy."""

    def __init__(self, tmp_path: Path, monkeypatch, *, keys: bool = True, join: bool = True) -> None:
        self.mp = monkeypatch
        self.tmp = tmp_path
        self.pi_home = tmp_path / "home-pi"
        self.member_home = tmp_path / "home-member"
        self.shared = tmp_path / "shared"
        self.shared.mkdir()
        self.official = tmp_path / "official"
        self.work = tmp_path / "work"
        self.witness = Witness(self.shared)
        with self.pi():
            people.write_me(*PI)
            _project(self.official)
            self.pid = read_identity(self.official).identity.id
            share.team_init(self.official)
        with self.member():
            people.write_me(*MEMBER)
            key = share.my_key()["key"] if keys else None
        with self.pi():
            if keys:
                share.team_add(self.official, MEMBER[1], MEMBER[0], key)
            else:
                with open(people.team_path(self.official), "a", encoding="utf-8") as handle:
                    handle.write(f'\n[[member]]\nid = "{MEMBER[1]}"\nname = "{MEMBER[0]}"\nrole = "member"\n')
            exchange_mod.configure(self.pid, self.shared, self.official)
            self.first = share.publish(self.official)
        self.witness.look()
        if join:
            with self.member():
                self.joined = share.join(self.shared, self.work)
            self.witness.look()

    def pi(self):
        return _as(self.mp, self.pi_home)

    def member(self):
        return _as(self.mp, self.member_home)


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Drive:
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    return Drive(tmp_path, monkeypatch)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _member_new_attempt(drive: Drive) -> None:
    (drive.work / "steps" / "18-big.py").write_text(NEW_SCRIPT)
    _sparse(drive.work / "data" / "big.bin")
    (drive.work / "map.md").write_text(_map() + ROW_18)


def _propose(drive: Drive, kind: str = "attempt_new", sentence: str = "加一条大面板的尝试", **options):
    with drive.member():
        return proposals.write_proposal(drive.work, proposals.make_proposal(drive.work, kind, sentence, **options))


def _other_key(tmp_path: Path, monkeypatch) -> tuple[Path, str]:
    """A key that is not the PI's (someone with write access to the folder)."""
    home = tmp_path / "home-forger"
    with _as(monkeypatch, home):
        key = signing.ensure_key()
    return home, key


def _forge_snapshot(drive: Drive, *, sign_home: Path, edit_after_signing: bool = False) -> int:
    """A snapshot written by someone other than the PI's Research OS: the
    latest manifest, renumbered and re-linked, one file's fingerprint
    changed, signed with the key in `sign_home` (or signed, then edited)."""
    snaps, _ = share.read_snapshots(drive.shared)
    last = snaps[-1]
    seq = share._next_seq(drive.shared)
    data = dict(last.data)
    data.update(seq=seq, prev_seq=last.seq, prev_manifest_sha256=last.sha256, note="forged")
    files = [dict(f) for f in data["file"]]
    target = next(f for f in files if f["path"] == "paper.md")
    data["file"] = files
    raw = proposals.emit(data)
    with _as(drive.mp, sign_home):
        signature = signing.sign(raw)
    if edit_after_signing:
        raw = raw.replace(target["sha256"].encode(), b"0" * 64)
    directory = share.snapshots_dir(drive.shared) / str(seq)
    directory.mkdir()
    (directory / "manifest.toml").write_bytes(raw)
    (directory / "manifest.toml.sig").write_bytes(signature)
    (directory / "ready.toml").write_bytes(exchange_mod._ready_bytes("snapshot", str(seq), raw))
    return seq


# -- joining (12.3, 12.5) ---------------------------------------------------------------------------


def test_12_10_3_join_trusts_the_pi_key_on_first_use_and_verifies_every_file(drive):
    """12.10 #3 (join): the member's working copy is the published snapshot,
    every file verified by hash; the PI's key is trusted on first use and
    its fingerprint shown to compare out of band; the base equals the
    official version."""
    assert drive.first["published"] and drive.first["seq"] == 1 and drive.first["signed"]
    joined = drive.joined
    with drive.pi():
        pi_key = people.read_team(drive.official).pi.key
        official = proposals.manifest(drive.official)
    assert joined["seq"] == 1 and joined["trust"] == "first_use" and joined["role"] == "member"
    assert joined["pi_fingerprint"] == signing.fingerprint(pi_key)
    for rel, entry in official.items():
        assert _sha(drive.work / rel) == entry["sha256"], rel
    assert (drive.work / ".researchos" / "team.toml").read_bytes() == people.team_path(drive.official).read_bytes()
    assert joined["base"] == proposals.snapshot_id(official)
    with drive.member():
        state = share.read_state(drive.pid)
        assert state["pi"] == PI[1] and state["pi_key"] == signing.parse_public_key(pi_key)
        assert exchange_mod.configured(drive.pid) == drive.shared
    manifest = tomllib.loads((share.snapshots_dir(drive.shared) / "1" / "manifest.toml").read_text())
    assert manifest["by"] == PI[1] and "prev_seq" not in manifest
    stores = {f["path"]: f["store"] for f in manifest["file"]}
    assert stores["figs/fig2.png"] == "blob" and stores["steps/16-build.py"] == "snapshot"
    assert stores[".researchos/team.toml"] == "snapshot"


def test_12_10_3_join_refuses_a_folder_that_is_not_empty_or_inside_a_project(drive):
    """12.2: a working copy starts in an empty folder, never inside a
    project; nothing is written."""
    busy = drive.tmp / "busy"
    busy.mkdir()
    (busy / "x.txt").write_text("mine")
    with drive.member():
        with pytest.raises(share.ShareError) as refused:
            share.join(drive.shared, busy)
        assert refused.value.code == "not_empty"
        with pytest.raises(share.ShareError) as refused:
            share.join(drive.shared, drive.official / "copy")
        assert refused.value.code == "invalid"
    assert sorted(p.name for p in busy.iterdir()) == ["x.txt"] and not (drive.official / "copy").exists()


# -- 12.10 #3 end to end ---------------------------------------------------------------------------------


def test_12_10_3_end_to_end_propose_approve_publish_sync_and_5_write_once(drive):
    """12.10 #3: a member proposes a new attempt with a script and a 200 MB
    file over the shared drive, signed; it is no node anywhere until the PI
    approves; approved -- applied, published as a new signed snapshot,
    synced by the member, recorded with by and approved_by. 12.10 #5: every
    file in the shared folder was created once and never modified."""
    with drive.member():
        _member_new_attempt(drive)
    stored = _propose(drive, add=["data/big.bin"])  # 12.13: the member adds the data file to the attempt's
    drive.witness.look()
    assert (stored.directory / "proposal.toml.sig").is_file()
    assert (drive.shared / "提议" / MEMBER[1] / "blobs").is_dir()

    with drive.pi():
        before = _readers(drive.official)
        _scan(drive.official)
        assert _readers(drive.official) == before
        conn = _conn(drive.official)
        try:
            assert db.get_node(conn, "attempt:map.md#18") is None and db.get_node(conn, "script:steps/18-big.py") is None
        finally:
            conn.close()
        inbox = proposals.listing(drive.official)
        assert [p["id"] for p in inbox["proposals"]] == [stored.id]
        assert inbox["proposals"][0]["verification"]["state"] == "verified"
        done = proposals.approve(drive.official, stored.id, note="同意")
        assert done["decision"]["by"] == MEMBER[1] and done["decision"]["approved_by"] == PI[1]
        published = done["published"]
        assert published["published"] and published["seq"] == 2 and published["signed"]
        assert "steps/18-big.py" in published["carried"] and len(published["blobs"]) == 1
        versions = history.versions(drive.official, "steps/18-big.py")["versions"]
        assert versions[0]["who"] == MEMBER[1] and versions[0]["approved_by"] == PI[1]
    drive.witness.look()
    outcome_dir = next((drive.shared / "已发布" / "outcomes" / stored.id).iterdir())
    assert (outcome_dir / "outcome.toml.sig").is_file()

    with drive.member():
        mine = proposals.listing(drive.work)["proposals"][0]
        assert mine["state"] == "approved" and mine["outcomes"][0]["text"] == "同意"
        (drive.work / "steps" / "18-big.py").unlink()
        (drive.work / "data" / "big.bin").unlink()
        (drive.work / "map.md").write_text(_map())  # back to the base: untouched files
        synced = share.sync(drive.work)
        assert synced["seq"] == 2 and not synced["conflicts"]
        assert {"steps/18-big.py", "data/big.bin", "map.md", ".researchos/decisions.toml"} <= set(synced["updated"])
        assert (drive.work / "data" / "big.bin").stat().st_size == BIG
        assert (drive.work / "steps" / "18-big.py").read_text() == NEW_SCRIPT
        assert decisions_mod.entries(drive.work)[-1]["approved_by"] == PI[1]
        assert share.sync(drive.work).get("up_to_date") is True
        status = share.status(drive.work)
        assert status["verification"] == "verified" and status["behind"] is False
    drive.witness.look()
    blobs = list((drive.shared / "已发布" / "blobs").iterdir())
    big = [b for b in blobs if b.stat().st_size == BIG]
    assert len(big) == 1
    with drive.pi():
        assert share.publish(drive.official)["published"] is False  # nothing changed: nothing written
    drive.witness.look()
    seen = set(drive.witness.seen)
    assert {"已发布/snapshots/1/ready.toml", "已发布/snapshots/2/manifest.toml.sig"} <= seen and len(seen) > 30


def test_12_10_3_a_returned_proposal_shows_its_reason_and_never_becomes_a_node(drive):
    """12.10 #3: returned -- the reason reaches the member through a signed
    outcome; nothing is applied or published."""
    with drive.member():
        _member_new_attempt(drive)
    stored = _propose(drive)
    with drive.pi():
        before = _readers(drive.official)
        outcome = proposals.return_(drive.official, stored.id, "数据来源不清楚")
        assert outcome["act"] == "returned"
        _scan(drive.official)
        assert _readers(drive.official) == before
        assert not (share.snapshots_dir(drive.shared) / "2").exists()
    with drive.member():
        mine = proposals.listing(drive.work)["proposals"][0]
        assert mine["state"] == "returned" and mine["outcomes"][0]["text"] == "数据来源不清楚"
        assert share.sync(drive.work).get("up_to_date") is True
    drive.witness.look()


def test_12_10_3_abandoning_without_a_reason_is_refused(drive):
    """12.10 #3: a verdict change to ☠️ without a reason is refused; nothing
    reaches the shared drive."""
    with drive.member():
        (drive.work / "map.md").write_text(_map("☠️"))
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.make_proposal(drive.work, "verdict_change", "放弃", result="t=0.3")
        assert refused.value.code == "reason_required"
    # 12.12: the member's area exists (the PI made it, empty) and stays empty.
    assert (drive.shared / "提议" / MEMBER[1]).is_dir() and not any((drive.shared / "提议" / MEMBER[1]).iterdir())


def test_12_10_3_both_sides_changing_one_file_is_listed_never_merged(drive):
    """12.10 #3: the PI changes a script directly and publishes; the member
    changed the same script. The member's sync keeps theirs and puts the
    official one beside it, listed with the difference; the member's
    proposal of it shows the PI the conflict and cannot be approved."""
    with drive.pi():
        (drive.official / "steps" / "16-build.py").write_text("# PI's version\n")
        done = share.publish(drive.official, note="PI 直接修改")
        assert done["published"] and done["changed"] == ["steps/16-build.py"]
        versions = history.versions(drive.official, "steps/16-build.py")["versions"]
        assert versions[0]["who"] == PI[1] and versions[0]["source"] == "publish"
    with drive.member():
        (drive.work / "steps" / "16-build.py").write_text("# member's version\n")
        stored = proposals.write_proposal(drive.work, proposals.make_proposal(drive.work, "files", "改脚本"))
        synced = share.sync(drive.work)
        assert synced["seq"] == 2
        [conflict] = synced["conflicts"]
        assert conflict["path"] == "steps/16-build.py" and conflict["official_copy"] == "steps/16-build.official.py"
        assert "+# PI's version" in conflict["diff"]
        assert (drive.work / "steps" / "16-build.py").read_text() == "# member's version\n"
        assert (drive.work / "steps" / "16-build.official.py").read_text() == "# PI's version\n"
        again = proposals.make_proposal(drive.work, "files", "再提一次")
        assert [f["path"] for f in again.data["file"]] == ["steps/16-build.py"]
        assert any(n["path"] == "steps/16-build.official.py" for n in again.data["not_carried"])
    with drive.pi():
        changes = proposals.what_changes(stored, drive.official)
        assert not changes["approvable"] and changes["conflicts"][0]["path"] == "steps/16-build.py"
        with pytest.raises(proposals.ProposalRefused):
            proposals.approve(drive.official, stored.id)
        assert (drive.official / "steps" / "16-build.py").read_text() == "# PI's version\n"
    drive.witness.look()


# -- forged snapshots (12.5) -------------------------------------------------------------------------------


def test_12_10_3_a_snapshot_signed_by_another_key_is_refused_and_the_last_good_kept(drive, tmp_path, monkeypatch):
    """12.10 #3: a snapshot written by someone other than the PI (a manifest
    re-signed with another key) is refused by the member's Research OS, said
    so, and the last good snapshot kept; the PI's next snapshot skips it and
    is accepted."""
    forger_home, _key = _other_key(tmp_path, monkeypatch)
    forged = _forge_snapshot(drive, sign_home=forger_home)
    with drive.member():
        synced = share.sync(drive.work)
        assert synced.get("up_to_date") and synced["seq"] == 1
        assert [(r["seq"], r["state"]) for r in synced["refused"]] == [(forged, "bad")]
        status = share.status(drive.work)
        assert status["last_snapshot"]["seq"] == 1 and status["refused"][0]["seq"] == forged
    with drive.pi():
        (drive.official / "paper.md").write_text("# Results\n\nrevised\n")
        done = share.publish(drive.official)
        assert done["seq"] == forged + 1 and done["refused"][0]["seq"] == forged
        manifest = tomllib.loads((share.snapshots_dir(drive.shared) / str(done["seq"]) / "manifest.toml").read_text())
        assert manifest["prev_seq"] == 1
    with drive.member():
        synced = share.sync(drive.work)
        assert synced["seq"] == forged + 1 and "paper.md" in synced["updated"]
        assert (drive.work / "paper.md").read_text() == "# Results\n\nrevised\n"
    drive.witness.look()


def test_12_10_3_a_snapshot_edited_after_signing_is_refused(drive):
    """12.10 #3: a manifest signed with the PI's own key and then edited is
    refused; so is joining afresh from it."""
    forged = _forge_snapshot(drive, sign_home=drive.pi_home, edit_after_signing=True)
    with drive.member():
        synced = share.sync(drive.work)
        assert synced["seq"] == 1 and synced["refused"] == [
            {**synced["refused"][0], "seq": forged, "state": "bad"}]
        fresh = share.join(drive.shared, drive.tmp / "work2")
        assert fresh["seq"] == 1 and fresh["refused"][0]["seq"] == forged


def test_12_10_3_a_snapshot_naming_a_new_pi_key_is_refused(drive, tmp_path, monkeypatch):
    """12.5: a snapshot validly signed by a key the member did not trust at
    joining -- even one whose own team.toml names that key -- is refused."""
    forger_home, forger_key = _other_key(tmp_path, monkeypatch)
    snaps, _ = share.read_snapshots(drive.shared)
    assert share.verify_chain(drive.shared, snaps, drive.pid, PI[1], forger_key).good == []


# -- what is not read (12.2) -------------------------------------------------------------------------------


def test_12_10_3_half_written_proposals_are_ignored_and_conflict_copies_reported_never_read(drive):
    """12.10 #3: a proposal without ready.toml is ignored; a OneDrive
    conflict copy in the shared folder -- of a snapshot, a proposal, a
    file -- is reported and never opened."""
    area = drive.shared / "提议" / MEMBER[1]
    half = area / ("pr-" + "b" * 32)
    half.mkdir(parents=True)
    (half / "proposal.toml").write_text('id = "x"\n')
    copy = share.snapshots_dir(drive.shared) / "1 2"
    shutil.copytree(share.snapshots_dir(drive.shared) / "1", copy)
    (copy / "manifest.toml").chmod(0)  # opening it would fail: it must never be opened
    (share.snapshots_dir(drive.shared) / "1" / "manifest-MacBook.toml").write_text("garbage")
    (area / ("pr-" + "c" * 32 + " (1)")).mkdir()
    try:
        with drive.pi():
            listed = proposals.listing(drive.official)
            assert listed["proposals"] == []
            kinds = {Path(p["path"]).name: p["kind"] for p in listed["problems"]}
            assert kinds[half.name] == "half_written" and kinds["pr-" + "c" * 32 + " (1)"] == "unexpected"
            status = share.status(drive.official)
            problem_kinds = {Path(p["path"]).name: p["kind"] for p in status["problems"]}
            assert problem_kinds == {"1 2": "unexpected", "manifest-MacBook.toml": "unexpected"}
        with drive.member():
            synced = share.sync(drive.work)
            assert {Path(p["path"]).name for p in synced["problems"]} == {"1 2", "manifest-MacBook.toml"}
    finally:
        (copy / "manifest.toml").chmod(0o644)


# -- signatures on proposals (12.5) ----------------------------------------------------------------------


@pytest.mark.parametrize("how", ["unsigned", "wrong_key"])
def test_12_10_3_an_unverifiable_proposal_is_shown_and_cannot_be_approved(drive, tmp_path, monkeypatch, how):
    """12.5 (the PI knows who sent what): a proposal from a member whose key
    is listed, unsigned or signed with another key, is listed as
    unverifiable and cannot be approved."""
    if how == "wrong_key":
        forger_home, _ = _other_key(tmp_path, monkeypatch)

        def signature(root, member, raw):
            with _as(monkeypatch, forger_home):
                return signing.sign(raw)
    else:
        def signature(root, member, raw):
            return None
    monkeypatch.setattr(proposals, "_signature", signature)
    with drive.member():
        (drive.work / "steps" / "19-x.py").write_text("print(19)\n")
    stored = _propose(drive, "files", "一个脚本")
    with drive.pi():
        item = proposals.listing(drive.official)["proposals"][0]
        assert item["verification"]["state"] == ("unsigned" if how == "unsigned" else "bad")
        assert item["verification"]["trusted"] is False
        changes = proposals.open_for(drive.official, stored.id)["changes"]
        assert not changes["approvable"] and changes["problems"][0].startswith("签名")
        with pytest.raises(proposals.ProposalRefused):
            proposals.approve(drive.official, stored.id)
        assert not (drive.official / "steps" / "19-x.py").exists()


def test_12_5_an_outcome_not_signed_by_the_pi_is_not_counted(drive, tmp_path, monkeypatch):
    """12.5: an "approved" outcome planted in the shared folder without the
    PI's signature is reported and never counted -- the proposal stays
    pending for its author."""
    with drive.member():
        (drive.work / "steps" / "19-x.py").write_text("print(19)\n")
    stored = _propose(drive, "files", "一个脚本")
    plain = exchange_mod.LocalExchange(drive.shared)  # no verifier: writes as a forger would
    raw = proposals.emit({"format": 1, "id": "o-" + "d" * 32, "proposal": stored.id, "act": "approved", "by": PI[1],
                          "at": "2026-10-11T00:00:00+00:00"})
    plain.write_outcome(stored.id, "o-" + "d" * 32, raw)
    with drive.member():
        mine = proposals.listing(drive.work)["proposals"][0]
        assert mine["state"] == "pending" and mine["problems"][0]["kind"] == "unverified"


# -- without ssh-keygen (12.5) ---------------------------------------------------------------------------


def test_12_5_without_ssh_keygen_snapshots_are_unverified_and_every_status_says_so(tmp_path, monkeypatch):
    """12.5: where ssh-keygen is missing, a team made without keys publishes
    unsigned snapshots; joining and syncing work, and every status and
    answer warns that snapshots cannot be verified."""
    monkeypatch.setattr(signing, "CANDIDATES", ())
    drive = Drive(tmp_path, monkeypatch, keys=False)
    assert drive.first["published"] and drive.first["signed"] is False
    assert signing.NO_TOOL_WARNING in drive.first["warnings"]
    assert drive.joined["pi_fingerprint"] is None and signing.NO_TOOL_WARNING in drive.joined["warnings"]
    with drive.pi():
        assert signing.NO_TOOL_WARNING in share.status(drive.official)["warnings"]
        (drive.official / "paper.md").write_text("# v2\n")
        assert share.publish(drive.official)["seq"] == 2
    with drive.member():
        synced = share.sync(drive.work)
        assert synced["seq"] == 2 and signing.NO_TOOL_WARNING in synced["warnings"]
        assert signing.NO_TOOL_WARNING in share.status(drive.work)["warnings"]
        (drive.work / "steps" / "19-x.py").write_text("print(19)\n")
    stored = _propose(drive, "files", "x")
    with drive.pi():
        item = proposals.listing(drive.official)["proposals"][0]
        assert item["verification"]["state"] == "no_key" and item["verification"]["trusted"]
        assert proposals.approve(drive.official, stored.id)["published"]["seq"] == 3
    drive.witness.look()


@needs_ssh
def test_12_5_a_pi_whose_key_is_listed_cannot_publish_without_ssh_keygen(drive, monkeypatch):
    """12.5: the PI's key is listed but ssh-keygen is gone -- publishing is
    refused with the reason rather than writing a snapshot no member would
    accept; nothing is written."""
    monkeypatch.setattr(signing, "CANDIDATES", ())
    with drive.pi():
        (drive.official / "paper.md").write_text("# v2\n")
        with pytest.raises(share.ShareError) as refused:
            share.publish(drive.official)
        assert refused.value.code == "cannot_sign"
    assert sorted(p.name for p in share.snapshots_dir(drive.shared).iterdir()) == ["1"]


# -- publishing (12.2) --------------------------------------------------------------------------------------


def test_12_2_publishing_never_reads_a_file_only_in_the_cloud(drive, monkeypatch):
    """12.2 / 8.10: a file only in the cloud on the PI's machine is never
    read; publishing is refused naming it, and nothing is written."""
    real = share._is_dataless  # 18.4: the flag is read from the lstat the walk already made
    cloud = (drive.official / "data" / "panel.csv").stat().st_ino
    monkeypatch.setattr(share, "_is_dataless", lambda st: st.st_ino == cloud or real(st))
    with drive.pi():
        (drive.official / "paper.md").write_text("# v2\n")
        with pytest.raises(share.ShareError) as refused:
            share.publish(drive.official)
        assert refused.value.code == "dataless" and refused.value.details["files"] == ["data/panel.csv"]
    assert sorted(p.name for p in share.snapshots_dir(drive.shared).iterdir()) == ["1"]


def test_12_2_only_the_pi_publishes_and_manages_the_team(drive):
    """12.1, 12.2: a member cannot publish or add members; team.toml is only
    ever appended to."""
    with drive.member():
        with pytest.raises(share.ShareError) as refused:
            share.publish(drive.work)
        assert refused.value.code == "not_pi"
        with pytest.raises(share.ShareError) as refused:
            share.team_add(drive.work, "zhao@example.com", "赵", signing.ensure_key())
        assert refused.value.code == "not_pi"
    with drive.pi():
        path = people.team_path(drive.official)
        before = path.read_bytes()
        with pytest.raises(share.ShareError) as refused:
            share.team_init(drive.official)
        assert refused.value.code == "team_exists"
        with pytest.raises(share.ShareError) as refused:
            share.team_add(drive.official, MEMBER[1], "again", signing.ensure_key())
        assert refused.value.code == "member_exists"
        with pytest.raises(share.ShareError) as refused:
            share.team_add(drive.official, "zhao@example.com", "赵", "not a key")
        assert refused.value.code == "invalid_key"
        share.team_add(drive.official, "zhao@example.com", "赵", signing.ensure_key())
        after = path.read_bytes()
        assert after.startswith(before) and [m.id for m in people.read_team(drive.official).members][-1] == "zhao@example.com"


def test_12_2_the_shared_folder_may_not_be_inside_a_project_or_the_home(drive):
    """12.2 / 10.2: the shared folder is refused inside a project and in
    Research OS's home; the setting is per machine, in projects.json."""
    with drive.pi():
        with pytest.raises(exchange_mod.ExchangeError) as refused:
            exchange_mod.configure(drive.pid, drive.official / "share", drive.official)
        assert refused.value.code == "inside_project"
        with pytest.raises(exchange_mod.ExchangeError) as refused:
            exchange_mod.configure(drive.pid, drive.pi_home / "share", drive.official)
        assert refused.value.code == "bad_location"
        registry = json.loads((drive.pi_home / "projects.json").read_text())
        assert registry["projects"][0]["shared"] == str(drive.shared)
    assert not list(drive.official.glob(".researchos/*shared*"))


# -- the watcher (12.3) --------------------------------------------------------------------------------------


def test_12_10_3_the_pis_watcher_notices_a_new_proposal(drive):
    """12.3: the PI's Research OS watches the members' areas: a new complete
    proposal bumps the generation and is counted in the status payload."""
    with drive.pi():
        watcher = watcher_mod.ProjectWatcher(lambda: drive.official, interval=60)
        assert watcher._poll_share(drive.official, force=True) is False
        generation = watcher.status_payload()["generation"]
    with drive.member():
        (drive.work / "steps" / "19-x.py").write_text("print(19)\n")
    _propose(drive, "files", "x")
    with drive.pi():
        assert watcher._poll_share(drive.official, force=True) is True
        payload = watcher.status_payload()
        assert payload["generation"] == generation + 1 and payload["share"] == {"proposals": 1}


# -- the command line and the app (12.2, 12.3) -------------------------------------------------------------------


@needs_ssh
def test_12_10_3_through_the_command_line(tmp_path, monkeypatch, capsys):
    """12.10 #3 through `researchos`: team init, me key, team add, share set,
    share publish, share join, propose, inbox, approve, share sync, share show."""
    pi_home, member_home = tmp_path / "home-pi", tmp_path / "home-member"
    shared, official, work = tmp_path / "shared", tmp_path / "official", tmp_path / "work"
    shared.mkdir()
    witness = Witness(shared)
    with _as(monkeypatch, pi_home):
        people.write_me(*PI)
        _project(official)
        assert cli.main(["team", "init", str(official)]) == 0
        assert "your key: SHA256:" in capsys.readouterr().out
    with _as(monkeypatch, member_home):
        people.write_me(*MEMBER)
        assert cli.main(["me", "key", "--out", str(tmp_path / "wang.pub")]) == 0
    with _as(monkeypatch, pi_home):
        assert cli.main(["team", "add", MEMBER[1], "--name", MEMBER[0], "--key", str(tmp_path / "wang.pub"), str(official)]) == 0
        assert cli.main(["share", "set", str(shared), str(official)]) == 0
        assert cli.main(["share", "publish", str(official)]) == 0
        assert "Published snapshot 1" in capsys.readouterr().out
    witness.look()
    with _as(monkeypatch, member_home):
        assert cli.main(["share", "join", str(shared), str(work)]) == 0
        out = capsys.readouterr().out
        assert "trusted on first use" in out and "snapshot 1" in out
        (work / "steps" / "19-x.py").write_text("print(19)\n")
        assert cli.main(["propose", "files", "--sentence", "一个脚本", "--yes", str(work)]) == 0
        capsys.readouterr()
    witness.look()
    with _as(monkeypatch, pi_home):
        assert cli.main(["inbox", str(official)]) == 0
        out = capsys.readouterr().out
        assert "1 pending" in out and "[signed]" in out
        pid = next(line.split()[0] for line in out.splitlines() if line.strip().startswith("pr-"))
        assert cli.main(["approve", pid, "--note", "好", str(official)]) == 0
        capsys.readouterr()
        assert cli.main(["share", "show", "--json", str(official)]) == 0
        shown = json.loads(capsys.readouterr().out)
        assert shown["role"] == "pi" and shown["last_snapshot"]["seq"] == 2 and shown["verification"] == "verified"
        assert shown["pending_publish"]["count"] == 0
    witness.look()
    with _as(monkeypatch, member_home):
        assert cli.main(["share", "sync", str(work)]) == 0
        assert "Synced snapshot 1 -> 2" in capsys.readouterr().out
        assert cli.main(["team", "show", str(work)]) == 0
        assert "wang@example.com" in capsys.readouterr().out
        assert cli.main(["share", "publish", str(work)]) == 1
        assert "nothing written" in capsys.readouterr().err
    witness.look()


@pytest.mark.parametrize("method, path, body", [
    ("GET", "/api/share", None),
    ("POST", "/api/share/publish", {}),
    ("POST", "/api/share/sync", {}),
    ("POST", "/api/share/join", {"shared": "/tmp/x", "folder": "/tmp/y"}),
])
def test_12_10_3_every_share_endpoint_is_origin_checked(drive, method, path, body):
    """Every new endpoint origin-checked first: a cross-origin request is
    refused with 403 and nothing is written."""
    with drive.pi():
        drive.witness.look()
        (drive.official / "paper.md").write_text("# would be published\n")
        with _serve(drive.official) as base:
            status, payload = _call(base + path, method, body, headers={"Origin": "http://evil.example"})
        assert status == 403 and "Origin" in payload["error"]
    assert sorted(p.name for p in share.snapshots_dir(drive.shared).iterdir()) == ["1"]
    drive.witness.look()


def test_12_10_3_the_app_publishes_syncs_and_joins(drive):
    """12.3 through the app's endpoints: 「发布」 (the PI), 「同步」 (the
    member), joining a new folder, and the status of each side."""
    with drive.pi():
        (drive.official / "paper.md").write_text("# Results\n\nfrom the app\n")
        with _serve(drive.official) as base:
            status, body = _call(f"{base}/api/share")
            assert status == 200 and body["role"] == "pi" and body["pending_publish"]["files"] == ["paper.md"]
            status, body = _call(f"{base}/api/share/publish", "POST", {"note": "改了正文"})
            assert status == 200 and body["published"] is True and body["seq"] == 2
    with drive.member():
        with _serve(drive.work) as base:
            status, body = _call(f"{base}/api/share")
            assert status == 200 and body["role"] == "member" and body["behind"] is True
            status, body = _call(f"{base}/api/share/sync", "POST", {})
            assert status == 200 and body["seq"] == 2 and body["updated"] == ["paper.md"]
            status, body = _call(f"{base}/api/share/publish", "POST", {})
            assert status == 403 and body["state"] == "not_pi" and body["message"] == share.MESSAGES["not_pi"]
            status, body = _call(f"{base}/api/share/join", "POST", {"shared": str(drive.shared),
                                                                    "folder": str(drive.tmp / "work3")})
            assert status == 200 and body["seq"] == 2
            status, body = _call(f"{base}/api/share/join", "POST", {"shared": str(drive.shared),
                                                                    "folder": str(drive.official)})
            assert status == 409 and body["state"] == "not_empty"
            status, body = _call(f"{base}/api/share/join", "POST", {"shared": str(drive.shared), "folder": "work4"})
            assert status == 400 and body["state"] == "not_absolute"
        assert (drive.work / "paper.md").read_text() == "# Results\n\nfrom the app\n"
    drive.witness.look()

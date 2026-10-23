"""Research OS V14, task G4-3 (DESIGN.md 18.4 item 4): the team paths cost less
and answer as they did. A published manifest or signature is parsed and
verified once per content, in memory (`share.Head`, `signing.verify`); the
working copy is walked once with one `lstat` per file; its fingerprint file
is written only when an entry changed; publishing stops rebuilding the last
snapshot's file map for each file; and a proposal's snapshot id is built on
the same fingerprints. Each new path is set beside the old one -- a copy of
the code as it was, kept here -- and the two must give the same payloads.
Every test names its scenario."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import tomllib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from researchos import paths, people, proposals, share, signing
from researchos import exchange as ex_mod
from researchos.exchange import Problem
from researchos.ingest import files as files_ingest
from researchos.memo import Memo
from researchos.records.identity import read_identity

from test_v8_phase2 import _project
from test_v8_phase4 import MEMBER, PI, Drive, _forge_snapshot, _other_key, _propose, _sha

needs_ssh = pytest.mark.skipif(signing.tool() is None, reason="ssh-keygen is not installed here")

FIXED_NOW = lambda: datetime.fromisoformat("2026-10-23T12:00:00+02:00")  # noqa: E731


def forget() -> None:
    """The engine as it is just after it starts: nothing remembered."""
    signing._VERIFIED.clear()
    for memo in (share._HEADS, share._PARSED, share._TEAMS, share._PRINTS):
        memo.clear()


@pytest.fixture(autouse=True)
def start_afresh() -> None:
    forget()


@pytest.fixture
def ssh_runs(monkeypatch) -> list[list[str]]:
    """The `ssh-keygen -Y verify` runs that really happened."""
    runs: list[list[str]] = []
    real = signing._run

    def spy(args, **kwargs):
        if args[:2] == ["-Y", "verify"]:
            runs.append(args)
        return real(args, **kwargs)

    monkeypatch.setattr(signing, "_run", spy)
    return runs


@pytest.fixture
def manifest_parses(monkeypatch) -> list[int]:
    """One entry for every manifest text parsed (a text with `[[file]]` tables)."""
    parsed: list[int] = []
    real = tomllib.loads

    def spy(text, *args, **kwargs):
        if isinstance(text, str) and "[[file]]" in text:
            parsed.append(len(text))
        return real(text, *args, **kwargs)

    monkeypatch.setattr(share.tomllib, "loads", spy)
    return parsed


@pytest.fixture
def reads(monkeypatch) -> list[Path]:
    """The files whose bytes were hashed."""
    seen: list[Path] = []
    real = ex_mod.sha256_of

    def spy(path):
        seen.append(Path(path))
        return real(path)

    monkeypatch.setattr(ex_mod, "sha256_of", spy)
    return seen


@pytest.fixture
def writes(monkeypatch) -> list[Path]:
    """The files `durable_write` wrote."""
    done: list[Path] = []
    real = share.record_files.durable_write

    def spy(path, data):
        done.append(Path(path))
        return real(path, data)

    monkeypatch.setattr(share.record_files, "durable_write", spy)
    return done


# -- the code as it was before item 4 --------------------------------------------------------------------


class OldSnapshot:
    """`share.Snapshot` as it was: all of the manifest parsed, and the file map rebuilt on every access."""

    def __init__(self, seq, directory, raw, data, signature):
        self.seq, self.directory, self.raw, self.data, self.signature = seq, directory, raw, data, signature

    @property
    def sha256(self):
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def files(self):
        return {f["path"]: dict(f) for f in self.data.get("file", [])}

    @property
    def head(self):  # glue for the code of today that reads the header (the same values)
        d = self.data
        return share.Head(seq=self.seq, project=d["project"], at=d["at"], by=d["by"], note=d.get("note"),
                          prev_seq=d.get("prev_seq"), prev_manifest_sha256=d.get("prev_manifest_sha256"),
                          files=len(d.get("file", [])), team=self.files.get(share._TEAM_REL))

    def payload(self):
        return {"seq": self.seq, "at": self.data.get("at"), "by": self.data.get("by"), "sha256": self.sha256,
                "files": len(self.data.get("file", [])), "signed": self.signature is not None,
                "note": self.data.get("note")}


def old_read_snapshots(shared):
    shared = ex_mod.as_folders(shared)
    if not shared.is_dir():
        raise share.ShareError("unreachable", f"the shared folder {shared} cannot be reached now")
    area = share.snapshots_dir(shared)
    found, problems = [], []
    if not area.is_dir():
        return found, problems
    for name in sorted(os.listdir(area)):
        path = area / name
        if name.startswith(".") and ".tmp-" in name:
            continue
        if not share._SEQ_RE.match(name) or not path.is_dir() or path.is_symlink():
            problems.append(Problem("unexpected", str(path), "not a snapshot Research OS wrote (a sync conflict copy?); not read"))
            continue
        problems += ex_mod._unexpected(path, {share.MANIFEST_FILE, share.MANIFEST_SIG, ex_mod.READY_FILE, ex_mod.FILES_DIRNAME})
        try:
            raw = (path / share.MANIFEST_FILE).read_bytes()
        except FileNotFoundError:
            problems.append(Problem("half_written", str(path), "no manifest.toml"))
            continue
        except OSError as exc:
            problems.append(Problem("invalid", str(path), f"manifest.toml cannot be read: {exc}"))
            continue
        problem = ex_mod._read_ready(path, "snapshot", name, raw)
        if problem is not None:
            problems.append(problem)
            continue
        try:
            data = tomllib.loads(raw.decode("utf-8"))
            share.validate_manifest(data, int(name))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as exc:
            problems.append(Problem("invalid", str(path / share.MANIFEST_FILE), str(exc)))
            continue
        found.append(OldSnapshot(int(name), path, raw, data, ex_mod._read_signature(path / share.MANIFEST_SIG)))
    found.sort(key=lambda s: s.seq)
    return found, problems


def old_verify(data, signature, public_key):
    key = signing.parse_public_key(public_key)
    with tempfile.TemporaryDirectory(prefix="researchos-verify-") as private:
        home = Path(private)
        allowed, sig = home / "allowed_signers", home / "data.sig"
        allowed.write_text(f'{signing._PRINCIPAL} namespaces="{signing.NAMESPACE}" {key}\n', encoding="utf-8")
        sig.write_bytes(signature)
        done = signing._run(["-Y", "verify", "-f", str(allowed), "-I", signing._PRINCIPAL, "-n", signing.NAMESPACE,
                             "-s", str(sig)], data=data, home=home)
    return done.returncode == 0


def old_check(data, signature, public_key, *, signer=None):
    if not public_key:
        return signing.Check("no_key", signer)
    try:
        print_ = signing.fingerprint(public_key)
    except signing.SigningError:
        return signing.Check("bad", signer)
    if signing.tool() is None:
        return signing.Check("no_tool", signer, print_)
    if signature is None:
        return signing.Check("unsigned", signer, print_)
    try:
        good = old_verify(data, signature, public_key)
    except signing.SigningError:
        good = False
    return signing.Check("verified" if good else "bad", signer, print_)


def old_snapshot_team(shared, snap):
    entry = snap.files.get(f"{paths.PROJECT_DIRNAME}/{people.TEAM_FILENAME}")
    if entry is None:
        return None
    try:
        return people.parse_team(share._read_content(shared, entry).decode("utf-8"))
    except (share.ShareError, UnicodeDecodeError, people.PeopleError):
        return None


def old_verify_chain(shared, snaps, project_id, pi, pi_key):
    chain = share.Chain()
    key = signing.parse_public_key(pi_key) if pi_key else None
    for snap in snaps:
        last = chain.last_good
        reason = None
        check = None
        if snap.data.get("project") != project_id:
            reason = ("wrong_project", "这个快照属于另一个项目")
        elif snap.data.get("by") != pi:
            reason = ("not_pi", f"这个快照不是 PI（{pi}）写的")
        else:
            check = old_check(snap.raw, snap.signature, key, signer=pi)
            if not check.trusted:
                reason = (check.state, check.message_zh)
            elif last is None and snap.data.get("prev_seq") is not None:
                reason = ("chain", "这个快照接在一个无法验证或不存在的快照后面")
            elif last is not None and (snap.data.get("prev_seq") != last.seq
                                       or snap.data.get("prev_manifest_sha256") != last.sha256):
                reason = ("chain", f"这个快照没有接在上一个可信的快照（第 {last.seq} 个）后面")
            else:
                team = old_snapshot_team(shared, snap)
                lead = next((m for m in team or () if m.role == "pi"), None)
                if lead is None or lead.id != pi:
                    reason = ("pi_changed", "这个快照里的成员名单换了 PI，或无法读取")
                elif (signing.parse_public_key(lead.key) if lead.key else None) != key:
                    reason = ("pi_key_changed", "这个快照里 PI 的公钥和加入时信任的不一致")
        if reason is not None:
            chain.refused.append({"seq": snap.seq, "state": reason[0], "reason": reason[1], "path": str(snap.directory)})
            continue
        chain.good.append(snap)
        if check is not None:
            chain.checks[snap.seq] = check
    return chain


def old_official_files(root):
    """`share.official_files` as it was, without its cache: each path of `snapshot_rels` looked at four ways, then read."""
    files, dataless = {}, []
    for rel in share.snapshot_rels(root):
        full = root / rel
        try:
            full.lstat()
        except OSError:
            continue
        if not full.is_file() or full.is_symlink():
            continue
        if paths.is_dataless(full):
            dataless.append(rel)
            continue
        sha, size = ex_mod.sha256_of(full)
        files[rel] = {"sha256": sha, "size": size}
    return files, dataless


def old_manifest(root):
    out = {}
    for rel in proposals.project_files(root):
        got = proposals.fingerprint(root / rel)
        if got is not None:
            out[rel] = got
    return out


def summary(chain):
    return ([s.seq for s in chain.good], chain.refused, {seq: c.payload() for seq, c in sorted(chain.checks.items())})


def listing(snaps, problems):
    return ([(s.seq, s.directory, s.raw, s.signature, s.sha256, dict(s.data), s.files, s.payload()) for s in snaps],
            [p.payload() for p in problems])


# -- the pieces -----------------------------------------------------------------------------------------


def test_a_memo_holds_the_most_recently_used_within_its_item_and_weight_budgets():
    memo = Memo(3)
    for i in range(4):
        memo.put(i, f"v{i}")
    assert memo.get(0) is None and [memo.get(i) for i in (1, 2, 3)] == ["v1", "v2", "v3"]
    memo.get(1)  # 1 is now the newest, so 2 goes first
    memo.put(4, "v4")
    assert memo.get(2) is None and memo.get(1) == "v1" and len(memo) == 3
    heavy = Memo(10, weight=100)
    heavy.put("a", 1, weight=60)
    heavy.put("b", 2, weight=30)
    heavy.put("c", 3, weight=30)  # 120 in all: the oldest goes
    assert heavy.get("a") is None and heavy.get("b") == 2 and heavy.get("c") == 3
    heavy.put("whole", 4, weight=101)  # heavier than the whole budget: not kept, and nothing else lost
    assert heavy.get("whole") is None and heavy.get("b") == 2
    heavy.put("b", 5, weight=10)  # replacing an entry takes its old weight back
    heavy.put("d", 6, weight=60)
    assert heavy.get("b") == 5 and heavy.get("c") == 3 and heavy.get("d") == 6
    heavy.clear()
    assert len(heavy) == 0


def test_a_memo_stays_whole_under_many_threads():
    memo = Memo(50, weight=500)
    errors = []

    def work(seed):
        try:
            for i in range(2000):
                key = (seed * 7 + i) % 120
                memo.put(key, key * 2, weight=1 + key % 5)
                got = memo.get((key * 3) % 120)
                assert got is None or got == ((key * 3) % 120) * 2
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(memo) <= 50


@needs_ssh
def test_ssh_keygen_runs_once_per_data_signature_and_key(tmp_path, monkeypatch, ssh_runs):
    home = tmp_path / "home-a"
    monkeypatch.setenv("RESEARCHOS_HOME", str(home))
    key = signing.ensure_key()
    data, other = b"a manifest", b"another manifest"
    signature = signing.sign(data)
    wrong = signing.parse_public_key(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UvlQQJdIlEz1ZYmXGPNcXbL8U1sW")
    assert [signing.verify(data, signature, key) for _ in range(5)] == [True] * 5
    assert len(ssh_runs) == 1
    assert signing.verify(other, signature, key) is False and signing.verify(other, signature, key) is False
    assert len(ssh_runs) == 2  # other data: asked once, and its "no" is remembered too
    assert signing.verify(data, signature, wrong) is False and signing.verify(data, signature, wrong) is False
    assert len(ssh_runs) == 3  # another key
    signing.verify(data, signature + b"\n", key)  # other bytes of a signature, whatever ssh-keygen makes of them
    assert len(ssh_runs) == 4  # another signature
    signing._VERIFIED.clear()
    assert signing.verify(data, signature, key) is True and len(ssh_runs) == 5  # a restart asks again


@needs_ssh
def test_a_run_that_did_not_end_is_not_an_answer_and_is_asked_again(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHOS_HOME", str(tmp_path / "home-a"))
    key = signing.ensure_key()
    signature = signing.sign(b"data")
    real = signing._run

    def dies(args, **kwargs):
        raise signing.SigningError("failed", "ssh-keygen did not finish within 30 seconds")

    monkeypatch.setattr(signing, "_run", dies)
    with pytest.raises(signing.SigningError):
        signing.verify(b"data", signature, key)
    assert signing.check(b"data", signature, key).state == "bad"  # as before: a verification that fails is "bad" now
    monkeypatch.setattr(signing, "_run", lambda *a, **k: SimpleNamespace(returncode=-9, stdout=b"", stderr=b""))
    assert signing.verify(b"data", signature, key) is False  # killed by a signal
    monkeypatch.setattr(signing, "_run", real)
    assert signing.verify(b"data", signature, key) is True  # neither was kept
    monkeypatch.setattr(signing, "tool", lambda: None)
    assert signing.check(b"data", signature, key).state == "no_tool"
    with pytest.raises(signing.SigningError):
        signing.verify(b"data", signature, key)


@needs_ssh
def test_verifying_from_several_threads_agrees_with_verifying_alone(tmp_path, monkeypatch, ssh_runs):
    monkeypatch.setenv("RESEARCHOS_HOME", str(tmp_path / "home-a"))
    key = signing.ensure_key()
    signatures = {n: (f"manifest {n}".encode(), signing.sign(f"manifest {n}".encode())) for n in range(4)}
    seen, errors = [], []

    def work():
        try:
            for _ in range(10):
                for n, (data, sig) in signatures.items():
                    seen.append((n, signing.verify(data, sig, key), signing.verify(data + b"x", sig, key)))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(seen) == 6 * 10 * 4 and all(good and not bad for _n, good, bad in seen)
    assert len(ssh_runs) <= 8 * 6  # two triples for each of 4 manifests, at worst once per thread; usually 8
    before = len(ssh_runs)
    assert signing.verify(*signatures[0], key) and len(ssh_runs) == before


# -- the snapshots ---------------------------------------------------------------------------------------


@pytest.fixture
def busy(tmp_path, monkeypatch) -> Drive:
    """A PI and a member with five published snapshots; then, in the folder: a manifest that is not valid (but
    has a ready.toml), a half-written snapshot, a sync service's conflict copy, and a snapshot the PI did not sign."""
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    drive = Drive(tmp_path, monkeypatch)
    with drive.pi():
        for n in range(4):
            (drive.official / "steps" / f"2{n}-extra.py").write_text(f"print({n})\n")
            assert share.publish(drive.official)["published"]
    area = share.snapshots_dir(drive.shared)
    junk = area / "40"
    junk.mkdir()
    (junk / "manifest.toml").write_bytes(b"this is = not [valid toml")
    (junk / "ready.toml").write_bytes(ex_mod._ready_bytes("snapshot", "40", b"this is = not [valid toml"))
    (area / "41").mkdir()
    (area / "41" / "manifest.toml").write_bytes(b"format = 1\n")
    shutil.copytree(area / "1", area / "1 2")
    forger_home, _key = _other_key(tmp_path, monkeypatch)
    drive.forged = _forge_snapshot(drive, sign_home=forger_home)
    return drive


@needs_ssh
def test_the_listing_and_the_chain_say_what_the_old_read_said(busy):
    drive = busy
    pi_key = people.read_team(drive.official).pi.key
    old_snaps, old_problems = old_read_snapshots(drive.shared)
    old_chain = old_verify_chain(drive.shared, old_snaps, drive.pid, PI[1], pi_key)
    assert [s.seq for s in old_chain.good] == [1, 2, 3, 4, 5] and old_chain.refused and old_problems
    one, _ = share.read_snapshots(drive.shared)
    forget()
    two, _ = share.read_snapshots(drive.shared)
    assert one == two and one[0] is not two[0]  # snapshots compare by value, as they did, whichever memory built them
    for round_ in ("cold", "warm", "warm again"):
        if round_ == "cold":
            forget()
        snaps, problems = share.read_snapshots(drive.shared)
        assert listing(snaps, problems) == listing(old_snaps, old_problems), round_
        chain = share.verify_chain(drive.shared, snaps, drive.pid, PI[1], pi_key)
        assert summary(chain) == summary(old_chain), round_
    # a copy of the chain on the other side: the member's pinned key is the same key, written the way the PI's was
    pinned = signing.parse_public_key(pi_key)
    assert summary(share.verify_chain(drive.shared, snaps, drive.pid, PI[1], pinned)) == summary(old_chain)
    other = _other_key_text(drive)
    assert summary(share.verify_chain(drive.shared, snaps, drive.pid, PI[1], other)) \
        == summary(old_verify_chain(drive.shared, old_snaps, drive.pid, PI[1], other))
    assert share.verify_chain(drive.shared, snaps, drive.pid, PI[1], other).good == []  # not the key: nothing is trusted
    assert summary(share.verify_chain(drive.shared, snaps, drive.pid, MEMBER[1], pi_key)) \
        == summary(old_verify_chain(drive.shared, old_snaps, drive.pid, MEMBER[1], pi_key))  # not the PI's snapshots
    assert summary(share.verify_chain(drive.shared, snaps, "p" + drive.pid[1:], PI[1], pi_key)) \
        == summary(old_verify_chain(drive.shared, old_snaps, "p" + drive.pid[1:], PI[1], pi_key))  # another project
    assert summary(share.verify_chain(drive.shared, snaps, drive.pid, PI[1], None)) \
        == summary(old_verify_chain(drive.shared, old_snaps, drive.pid, PI[1], None))  # no key listed for the PI


def _other_key_text(drive) -> str:
    with drive.member():
        return signing.ensure_key()


@needs_ssh
def test_status_and_the_pages_read_the_same_with_what_was_remembered_and_without(busy, monkeypatch):
    drive = busy

    def pages():
        out = {}
        with drive.pi():
            out["pi"] = share.status(drive.official)
        with drive.member():
            out["member"] = share.status(drive.work)
            out["sync"] = share.sync_preview(drive.work)
            out["sync_en"] = share.sync_preview(drive.work, lang="en")
            seen = share.available(drive.work)
            out["available"] = [seen["chain"].good and [s.seq for s in seen["chain"].good], seen["chain"].refused,
                                [p.payload() for p in seen["problems"]], [s.payload() for s in seen["snaps"]]]
            out["join"] = share.join_preview(drive.shared, drive.tmp / "second-copy")
        return json.loads(json.dumps(out, default=str))

    with monkeypatch.context() as old:
        old.setattr(share, "read_snapshots", old_read_snapshots)
        old.setattr(share, "verify_chain", old_verify_chain)
        old.setattr(share, "official_files", lambda root, pid: old_official_files(Path(root)))
        as_it_was = pages()
    forget()
    cold = pages()
    warm = pages()
    assert as_it_was["pi"]["last_snapshot"]["seq"] == 5 and as_it_was["pi"]["refused"]
    assert cold == as_it_was
    assert warm == as_it_was


@needs_ssh
def test_each_manifest_is_parsed_and_each_signature_verified_once_per_content(busy, ssh_runs, manifest_parses):
    drive = busy
    forget()
    ssh_runs.clear()
    manifest_parses.clear()
    with drive.member():
        share.status(drive.work)
    # six manifests are valid and complete: 1-5 and the forgery; 40 is not TOML and 41 has no ready.toml
    assert len(ssh_runs) == 6  # a verification for each of them, the forgery too (it is the PI's by its header)
    assert len(manifest_parses) == 6  # and each parsed once, whatever the page asks for
    with drive.member():
        share.sync_preview(drive.work)  # reads the file list of the snapshot it brings the copy to: once
    with drive.pi():
        share.status(drive.official)
    ssh_runs.clear()
    manifest_parses.clear()
    with drive.member():
        for _ in range(3):
            share.status(drive.work)
            share.sync_preview(drive.work)
    with drive.pi():
        for _ in range(3):
            share.status(drive.official)
    assert ssh_runs == [] and manifest_parses == []
    with drive.pi():
        (drive.official / "steps" / "30-more.py").write_text("print(30)\n")
        published = share.publish(drive.official)
    ssh_runs.clear()
    manifest_parses.clear()
    with drive.member():
        share.status(drive.work)
        share.status(drive.work)
    assert published["published"] and len(ssh_runs) == 1 and len(manifest_parses) == 1  # only the new snapshot


@needs_ssh
def test_the_whole_file_list_is_parsed_for_the_snapshot_that_is_used_not_for_all(busy, manifest_parses):
    drive = busy
    forget()
    share.read_snapshots(drive.shared)  # every header is known now, no file list is
    manifest_parses.clear()
    snaps, _problems = share.read_snapshots(drive.shared)
    pi_key = people.read_team(drive.official).pi.key
    share.verify_chain(drive.shared, snaps, drive.pid, PI[1], pi_key)
    assert manifest_parses == []  # listing and checking every snapshot parses none of them
    last = [s for s in snaps if s.seq == 5][0]
    assert last.payload()["files"] == last.head.files and manifest_parses == []
    assert last.head.files == len(last.files) and len(manifest_parses) == 1  # the one in use: its file list, once
    assert last.files is last.files and last.data["file"] and len(manifest_parses) == 1  # however often it is asked
    again, _problems = share.read_snapshots(drive.shared)
    assert [s for s in again if s.seq == 5][0].files == last.files and len(manifest_parses) == 1  # and kept for the next page
    assert all(s.head.files == len(tomllib.loads(s.raw.decode())["file"]) for s in snaps)


@needs_ssh
def test_what_is_remembered_is_bounded(busy):
    drive = busy
    forget()
    for _ in range(3):
        snaps, _problems = share.read_snapshots(drive.shared)
    assert len(share._HEADS) == 7  # the five, the forgery and the invalid one; the half-written never reaches the parse
    assert len(share._TEAMS) == 0 and len(share._PARSED) == 0  # nothing has asked for a team or a file list yet
    for snap in snaps:
        assert snap.files
    assert len(share._PARSED) == 4  # a few whole manifests, however many are used
    with drive.member():
        share.status(drive.work)
    assert len(share._TEAMS) == 1  # the same team.toml in every snapshot: understood once
    assert len(signing._VERIFIED) <= 1024 and len(share._HEADS) <= 1024


@needs_ssh
def test_a_snapshot_served_twice_hands_out_the_same_values_and_its_copies_do_not_touch_the_memory(busy):
    drive = busy
    snaps, _ = share.read_snapshots(drive.shared)
    last = [s for s in snaps if s.seq == 5][0]
    first_data, first_files = last.data, last.files
    first_data["file"][0]["size"] = -1
    first_files[next(iter(first_files))]["size"] = -1
    again, _ = share.read_snapshots(drive.shared)
    last_again = [s for s in again if s.seq == 5][0]
    assert last_again.data["file"][0]["size"] >= 0
    assert all(entry["size"] >= 0 for entry in last_again.files.values())
    share.validate_manifest(last_again.data, 5)


@needs_ssh
def test_a_forged_or_changed_manifest_is_still_refused_after_a_good_one_was_remembered(busy, tmp_path, monkeypatch):
    drive = busy
    pi_key = people.read_team(drive.official).pi.key
    area = share.snapshots_dir(drive.shared)
    snaps, _ = share.read_snapshots(drive.shared)
    assert [s.seq for s in share.verify_chain(drive.shared, snaps, drive.pid, PI[1], pi_key).good] == [1, 2, 3, 4, 5]
    # 1. the forged snapshot is refused, and the good ones are still good
    forged = drive.forged
    chain = share.verify_chain(drive.shared, snaps, drive.pid, PI[1], pi_key)
    assert [r["seq"] for r in chain.refused] == [forged] and chain.refused[0]["state"] == "bad"
    # 2. a snapshot signed, then edited (its ready.toml rewritten to match): the bytes are new, so asked again
    forger_home, _ = _other_key(tmp_path, monkeypatch)
    edited = _forge_snapshot(drive, sign_home=forger_home, edit_after_signing=True)
    # 3. the PI's own snapshot 3, its manifest edited and its ready.toml rewritten to match: not what was signed
    manifest = area / "3" / "manifest.toml"
    original = manifest.read_bytes()
    tampered = original + b"\n# edited on the drive\n"
    manifest.write_bytes(tampered)
    (area / "3" / "ready.toml").write_bytes(ex_mod._ready_bytes("snapshot", "3", tampered))
    snaps, problems = share.read_snapshots(drive.shared)
    chain = share.verify_chain(drive.shared, snaps, drive.pid, PI[1], pi_key)
    states = {r["seq"]: r["state"] for r in chain.refused}
    assert states[3] == "bad" and states[edited] == "bad" and states[forged] == "bad"
    assert [s.seq for s in chain.good] == [1, 2]  # 4 and 5 hang on 3, which is not trusted: "chain"
    assert states[4] == "chain" and states[5] == "chain"
    # 4. the signature file taken away, then swapped for a good signature over other bytes
    manifest.write_bytes(original)
    (area / "3" / "ready.toml").write_bytes(ex_mod._ready_bytes("snapshot", "3", original))
    snaps, _ = share.read_snapshots(drive.shared)
    assert [s.seq for s in share.verify_chain(drive.shared, snaps, drive.pid, PI[1], pi_key).good] == [1, 2, 3, 4, 5]
    sig = area / "3" / "manifest.toml.sig"
    kept = sig.read_bytes()
    sig.unlink()
    snaps, _ = share.read_snapshots(drive.shared)
    states = {r["seq"]: r["state"] for r in share.verify_chain(drive.shared, snaps, drive.pid, PI[1], pi_key).refused}
    assert states[3] == "unsigned"
    sig.write_bytes((area / "2" / "manifest.toml.sig").read_bytes())
    snaps, _ = share.read_snapshots(drive.shared)
    states = {r["seq"]: r["state"] for r in share.verify_chain(drive.shared, snaps, drive.pid, PI[1], pi_key).refused}
    assert states[3] == "bad"
    sig.write_bytes(kept)
    snaps, _ = share.read_snapshots(drive.shared)
    assert [s.seq for s in share.verify_chain(drive.shared, snaps, drive.pid, PI[1], pi_key).good] == [1, 2, 3, 4, 5]


@needs_ssh
def test_a_team_file_damaged_after_the_snapshot_was_trusted_is_seen(busy):
    drive = busy
    pi_key = people.read_team(drive.official).pi.key
    area = share.snapshots_dir(drive.shared)
    snaps, _ = share.read_snapshots(drive.shared)
    assert [s.seq for s in share.verify_chain(drive.shared, snaps, drive.pid, PI[1], pi_key).good] == [1, 2, 3, 4, 5]
    team = area / "2" / "files" / ".researchos" / "team.toml"
    assert team.is_file()
    team.write_bytes(team.read_bytes() + b"# damaged\n")
    chain = share.verify_chain(drive.shared, snaps, drive.pid, PI[1], pi_key)
    old = old_verify_chain(drive.shared, old_read_snapshots(drive.shared)[0], drive.pid, PI[1], pi_key)
    assert summary(chain) == summary(old)
    assert [r["seq"] for r in chain.refused if r["state"] == "pi_changed"] == [2]


# -- the working copy's files ----------------------------------------------------------------------------


def _messy(root: Path) -> None:
    """Everything the walk must treat as `os.walk` and `iter_files` treat it."""
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "sub" / "deep" / "x.py").write_text("print('x')\n")
    (root / "sub" / "plain.txt").write_text("plain\n")
    for noisy in ("node_modules", "__pycache__", "_cache", ".hidden", ".venv", ".git"):
        (root / noisy / "inner").mkdir(parents=True)
        (root / noisy / "inner" / "f.txt").write_text(noisy)
        (root / "sub" / noisy).mkdir()
        (root / "sub" / noisy / "f.txt").write_text(noisy)
    (root / ".dotfile").write_text("hidden\n")
    (root / "sub" / ".dotfile").write_text("hidden\n")
    os.symlink("paper.md", root / "link-to-file.md")
    os.symlink("sub", root / "link-to-folder")
    os.symlink("nowhere", root / "link-to-nothing")
    os.symlink("../sub/plain.txt", root / "sub" / "deep" / "link.txt")
    os.mkfifo(root / "a-pipe")
    (root / "back\\slash.txt").write_text("a name a snapshot does not carry\n")
    (root / "ünï").mkdir()
    (root / "ünï" / "çode 文件.txt").write_text("unicode\n")
    (root / "empty").mkdir()
    (root / "empty-file.txt").write_bytes(b"")
    (root / "big.bin").write_bytes(os.urandom(3_000_000))
    (root / ".researchos" / "variables" / "tfp").mkdir(parents=True)
    (root / ".researchos" / "variables" / "tfp" / "v1.toml").write_text('name = "tfp"\n')
    (root / ".researchos" / "variables" / "tfp" / "notes.txt").write_text("a card's other file\n")
    (root / ".researchos" / "notes.toml").write_text("# notes\n")
    (root / ".researchos" / "unlisted.toml").write_text("not a record file\n")


@pytest.fixture
def messy(tmp_path):
    root = _project(tmp_path / "proj")
    _messy(root)
    locked = root / "locked"
    locked.mkdir()
    (locked / "secret.txt").write_text("cannot be listed\n")
    locked.chmod(0)
    try:
        yield root, read_identity(root).identity.id
    finally:
        locked.chmod(0o755)


def test_the_walk_lists_the_files_iter_files_lists_in_the_same_order(messy):
    root, _pid = messy
    expected = [rel for rel, _category in files_ingest.iter_files(root) if (root / rel).is_file()]
    got = share._walk_project(root)
    assert [rel for rel, _seen in got] == expected
    assert len(expected) > 10 and "back\\slash.txt" in expected and "empty-file.txt" in expected
    assert not any(part in (".hidden", "node_modules", "locked") or part.startswith(".") for rel in expected
                   for part in rel.split("/")) and "a-pipe" not in expected and "link-to-file.md" not in expected
    for rel, seen in got:
        st = (root / rel).lstat()
        assert seen == share._Seen(st.st_size, st.st_mtime_ns, st.st_ino, False)
        assert seen.stamp == f"{st.st_size}:{st.st_mtime_ns}:{st.st_ino}"


def test_the_inventory_is_the_files_of_the_project_and_the_record_files(messy):
    root, _pid = messy
    listed, extra = share._inventory(root)
    assert [rel for rel, _ in listed] == [rel for rel in proposals.project_files(root) if (root / rel).is_file()]
    regular = [rel for rel in share.snapshot_rels(root) if (root / rel).is_file() and not (root / rel).is_symlink()]
    assert sorted(rel for rel, _ in listed + extra if rel in set(regular)) == regular
    assert "a-pipe" in share.snapshot_rels(root) and "a-pipe" not in [rel for rel, _ in listed]  # a pipe is no file
    assert ".researchos/variables/tfp/v1.toml" in [rel for rel, _ in listed]  # the cards' versions are listed files
    assert ".researchos/variables/tfp/notes.txt" in [rel for rel, _ in extra]
    assert ".researchos/unlisted.toml" not in [rel for rel, _ in listed + extra]


def test_official_files_gives_what_it_gave_through_every_change_to_the_folder(messy, monkeypatch):
    root, pid = messy

    def same():
        got = share.official_files(root, pid)
        was = old_official_files(root)
        assert (list(got[0].items()), got[1]) == (list(was[0].items()), was[1])
        assert list(got[0]) == sorted(got[0])
        return got

    first, _ = same()
    assert "back\\slash.txt" not in first and ".researchos/variables/tfp/notes.txt" in first
    assert ".researchos/unlisted.toml" not in first and "empty-file.txt" in first
    same()
    forget()
    same()
    (root / "paper.md").write_text("# a longer paper\n")
    (root / "steps" / "17-plot.py").unlink()
    (root / "new").mkdir()
    (root / "new" / "n.txt").write_text("n\n")
    (root / "figs" / "fig2.png").write_bytes(b"\x89PNG other")
    same()
    os.replace(root / "new" / "n.txt", root / "new" / "m.txt")
    (root / "sub" / "plain.txt").unlink()
    os.symlink("../paper.md", root / "sub" / "plain.txt")  # a file became a link
    same()
    # a file only in the cloud is never read, whichever way it is said
    cloud = root / "data" / "panel.csv"
    inode = cloud.stat().st_ino
    real = share._is_dataless
    monkeypatch.setattr(share, "_is_dataless", lambda st: st.st_ino == inode or real(st))
    monkeypatch.setattr(paths, "is_dataless", lambda p: Path(p) == cloud)
    files, dataless = same()
    assert dataless == ["data/panel.csv"] and "data/panel.csv" not in files


def test_the_manifest_and_the_snapshot_id_are_what_they_were(messy, monkeypatch):
    root, pid = messy
    for step in range(3):
        got, was = proposals.manifest(root), old_manifest(root)
        assert list(got.items()) == list(was.items()), step  # the same files, the same order, the same fingerprints
        assert proposals.snapshot_id(got) == proposals.snapshot_id(was)
        if step == 0:
            (root / "paper.md").write_text("# changed\n")
            (root / "extra.txt").write_text("e\n")
        elif step == 1:
            forget()
    cloud = root / "data" / "panel.csv"
    monkeypatch.setattr(share, "_is_dataless", lambda st: st.st_ino == cloud.stat().st_ino)
    monkeypatch.setattr(paths, "is_dataless", lambda p: Path(p) == cloud)
    got, was = proposals.manifest(root), old_manifest(root)
    assert list(got.items()) == list(was.items()) and got["data/panel.csv"]["dataless"] is True
    assert got["data/panel.csv"] == {"sha256": None, "size": cloud.stat().st_size, "dataless": True}
    assert proposals.snapshot_id(got) == proposals.snapshot_id(was)


def test_a_folder_without_a_project_identity_is_listed_as_before(tmp_path):
    root = tmp_path / "plain"
    (root / "a").mkdir(parents=True)
    (root / "a" / "x.txt").write_text("x\n")
    (root / "y.py").write_text("y = 1\n")
    assert list(proposals.manifest(root).items()) == list(old_manifest(root).items()) and len(old_manifest(root)) == 2


def test_an_unchanged_file_is_not_read_again_and_the_two_looks_share_the_fingerprints(messy, reads):
    root, pid = messy
    files, _ = share.official_files(root, pid)
    assert len(reads) == len(files) and len(set(reads)) == len(reads)  # the first look reads each file once
    reads.clear()
    got = proposals.manifest(root)  # the proposals' look is built on the same fingerprints
    assert [p.name for p in reads] == ["back\\slash.txt"]  # the one file a snapshot does not carry, which only it lists
    assert got["paper.md"]["sha256"] == files["paper.md"]["sha256"]
    reads.clear()
    share.official_files(root, pid)
    proposals.manifest(root)
    assert reads == []
    forget()
    share.official_files(root, pid)
    proposals.manifest(root)
    assert reads == []  # a restart reads the fingerprint file, not the files
    (root / "paper.md").write_text("# another paper, another size\n")
    (root / "new.txt").write_text("new\n")
    proposals.manifest(root)
    assert sorted(p.name for p in reads) == ["new.txt", "paper.md"]
    reads.clear()
    share.official_files(root, pid)
    assert reads == []  # the look that found the change left it in the file


def test_a_changed_file_is_read_whatever_the_other_look_did_before(messy, reads):
    root, pid = messy
    share.official_files(root, pid)
    proposals.manifest(root)
    reads.clear()
    target = root / "figs" / "fig1.png"
    before = target.stat()
    target.write_bytes(b"\x89PNG fig1")  # the same bytes again: new modification time, so read again (and found the same)
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 7))
    replaced = root / "figs" / "fig2.png"
    content = replaced.read_bytes()
    replaced.unlink()
    replaced.write_bytes(content)  # another inode, the same bytes
    share.official_files(root, pid)
    assert sorted(p.name for p in reads) in (["fig1.png", "fig2.png"], ["fig1.png"])  # a freed inode may come back
    assert "fig1.png" in [p.name for p in reads]


def test_the_fingerprint_file_is_written_only_when_an_entry_changed(messy, writes):
    root, pid = messy
    cache = paths.index_dir(pid) / share.CACHE_FILENAME

    def mine():
        return [p for p in writes if p == cache]

    def stamp():
        st = cache.stat()
        return st.st_ino, st.st_mtime_ns, st.st_size

    share.official_files(root, pid)
    proposals.manifest(root)  # lists one file more than a snapshot does, so it adds an entry
    written = len(mine())
    assert written == 2
    first = stamp()
    for _ in range(3):
        share.official_files(root, pid)
        proposals.manifest(root)
    forget()
    share.official_files(root, pid)  # a new engine: it reads the file, and finds nothing to change
    proposals.manifest(root)
    assert len(mine()) == written and stamp() == first
    (root / "paper.md").write_text("# changed\n")  # an entry changed
    share.official_files(root, pid)
    assert len(mine()) == written + 1 and json.loads(cache.read_text())["paper.md"][0].startswith(f"{len('# changed') + 1}:")
    share.official_files(root, pid)
    assert len(mine()) == written + 1
    (root / "empty-file.txt").unlink()  # an entry gone
    share.official_files(root, pid)
    assert len(mine()) == written + 2 and "empty-file.txt" not in json.loads(cache.read_text())
    (root / "later.txt").write_text("l\n")  # an entry new, found by the proposals' look
    proposals.manifest(root)
    assert len(mine()) == written + 3 and "later.txt" in json.loads(cache.read_text())
    share.official_files(root, pid)
    assert len(mine()) == written + 3
    cache.unlink()  # taken away from outside: written again, whatever the memory says
    share.official_files(root, pid)
    assert len(mine()) == written + 4 and cache.is_file()
    cache.write_text("not json at all")
    share.official_files(root, pid)
    assert len(mine()) == written + 5 and set(json.loads(cache.read_text())) >= set(share.official_files(root, pid)[0])
    assert len(mine()) == written + 5
    other = json.loads(cache.read_text())
    other["paper.md"][1] = "0" * 64  # another engine wrote a map under the stamp of the file: this one reads it
    cache.write_text(json.dumps(other))
    assert share.official_files(root, pid)[0]["paper.md"]["sha256"] == "0" * 64  # (a stamp is what a cache trusts)


def test_a_fingerprint_file_that_is_not_a_map_is_replaced_by_one(messy, writes):
    root, pid = messy
    cache = paths.index_dir(pid) / share.CACHE_FILENAME
    cache.parent.mkdir(parents=True, exist_ok=True)
    for text in ("[1, 2]", "null", '"x"', '{"paper.md": 5, "map.md": ["a"], "steps/16-build.py": ["x", "y", "z"]}'):
        cache.write_text(text)
        forget()
        files, _ = share.official_files(root, pid)
        assert files == old_official_files(root)[0], text
        saved = json.loads(cache.read_text())
        assert isinstance(saved, dict) and set(saved) == set(files)


def test_looks_from_several_threads_agree_with_a_look_alone(messy):
    root, pid = messy
    expected = old_official_files(root)
    expected_listed = old_manifest(root)
    results, errors = [], []

    def look():
        try:
            for _ in range(4):
                results.append(("o", share.official_files(root, pid)))
                results.append(("m", proposals.manifest(root)))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=look) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(results) == 6 * 8
    for kind, got in results:
        assert got == (expected if kind == "o" else expected_listed)
    cache = json.loads((paths.index_dir(pid) / share.CACHE_FILENAME).read_text())
    assert set(expected[0]) <= set(cache) and all(len(entry) == 2 for entry in cache.values())


def test_looks_while_files_change_never_fail_and_the_last_one_is_right(messy):
    root, pid = messy
    stop = threading.Event()
    errors = []

    def look():
        try:
            while not stop.is_set():
                share.official_files(root, pid)
                proposals.manifest(root)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=look) for _ in range(4)]
    for t in threads:
        t.start()
    try:
        for n in range(30):
            (root / "churn.txt").write_text("x" * n)
            if n % 3 == 0:
                (root / "gone.txt").write_text("g")
            elif (root / "gone.txt").exists():
                (root / "gone.txt").unlink()
    finally:
        stop.set()
        for t in threads:
            t.join()
    assert not errors
    forget()
    assert share.official_files(root, pid) == old_official_files(root)
    assert list(proposals.manifest(root).items()) == list(old_manifest(root).items())


# -- publishing and approving ----------------------------------------------------------------------------


@needs_ssh
@pytest.mark.parametrize("count", [20, 300])
def test_publishing_asks_for_the_last_snapshots_file_map_a_few_times_not_once_per_file(tmp_path, monkeypatch, count):
    drive = Drive(tmp_path, monkeypatch)
    asked = []
    original = share.Snapshot.files
    monkeypatch.setattr(share.Snapshot, "files", property(lambda self: (asked.append(self.seq), original.func(self))[1]))
    with drive.pi():
        for i in range(count):
            (drive.official / "data" / f"f{i}.txt").write_text(f"{i}\n")
        done = share.publish(drive.official)
        assert done["published"] and len(done["changed"]) >= count
        (drive.official / "data" / "f0.txt").write_text("changed\n")
        again = share.publish(drive.official)
        assert again["published"] and again["changed"] == ["data/f0.txt"]
    assert len(asked) <= 6  # the same however many files: it was once per file before


@needs_ssh
def test_a_publish_gives_the_same_with_and_without_what_was_remembered(tmp_path, monkeypatch):
    drive = Drive(tmp_path, monkeypatch)
    with drive.pi():
        for i in range(5):
            (drive.official / "steps" / f"3{i}-s.py").write_text(f"print({i})\n")
        (drive.official / "figs" / "big.bin").write_bytes(b"\x00" * 300 + b"\xff")
        share.publish(drive.official, note="first of the lot", now=FIXED_NOW)
        (drive.official / "paper.md").write_text("# a second paper\n")
        (drive.official / "steps" / "31-s.py").unlink()
        (drive.official / "figs" / "big.bin").write_bytes(b"\x00" * 300 + b"\xfe")
        before = tmp_path / "shared-before"
        shutil.copytree(drive.shared, before)
        warm = share.publish(drive.official, note="the second", now=FIXED_NOW)
        seq = warm["seq"]
        manifest_warm = (share.snapshots_dir(drive.shared) / str(seq) / "manifest.toml").read_bytes()
        signature_warm = (share.snapshots_dir(drive.shared) / str(seq) / "manifest.toml.sig").read_bytes()
        files_warm = {p.relative_to(drive.shared): p.read_bytes() for p in (drive.shared).rglob("*") if p.is_file()}
        shutil.rmtree(drive.shared)
        shutil.copytree(before, drive.shared)
        forget()
        cold = share.publish(drive.official, note="the second", now=FIXED_NOW)
        manifest_cold = (share.snapshots_dir(drive.shared) / str(seq) / "manifest.toml").read_bytes()
        files_cold = {p.relative_to(drive.shared): p.read_bytes() for p in (drive.shared).rglob("*") if p.is_file()}
        unchanged = share.publish(drive.official, now=FIXED_NOW)
        forget()
        unchanged_cold = share.publish(drive.official, now=FIXED_NOW)
    assert warm == cold and warm["published"] and warm["changed"]
    assert manifest_warm == manifest_cold and files_warm == files_cold
    assert signature_warm == (share.snapshots_dir(drive.shared) / str(seq) / "manifest.toml.sig").read_bytes()
    assert unchanged == unchanged_cold and unchanged["published"] is False and unchanged["reason"] == "unchanged"


@needs_ssh
def test_a_member_page_with_thirty_snapshots_costs_no_verification_once_asked(tmp_path, monkeypatch, ssh_runs, manifest_parses):
    drive = Drive(tmp_path, monkeypatch)
    with drive.pi():
        for n in range(29):
            (drive.official / "data" / f"extra{n}.txt").write_text(f"{n}\n")
            assert share.publish(drive.official)["published"]
    forget()
    ssh_runs.clear()
    manifest_parses.clear()
    with drive.member():
        began = time.perf_counter()
        first = share.status(drive.work)
        cold = time.perf_counter() - began
        ssh_runs_first, parses_first = len(ssh_runs), len(manifest_parses)
        best = min(_timed(lambda: share.status(drive.work)) for _ in range(3))
        again = share.status(drive.work)
        pages = (len(ssh_runs), len(manifest_parses))
        preview = share.sync_preview(drive.work)
    assert first["last_snapshot"]["seq"] == 30 and first["behind"] is True and first["verification"] == "verified"
    assert ssh_runs_first == 30 and parses_first == 30 and preview["to"] == 30
    assert pages == (30, 30)  # not one more, for four more pages
    assert len(ssh_runs) == 30 and len(manifest_parses) == 31  # the sync page reads the file list of the one it uses
    assert again == first and best < 1.0 and cold < 10


def _timed(call) -> float:
    began = time.perf_counter()
    call()
    return time.perf_counter() - began


# -- a proposal's snapshot id ----------------------------------------------------------------------------


@needs_ssh
def test_a_proposals_snapshot_id_is_built_on_the_fingerprints_for_the_pi_and_for_the_member(tmp_path, monkeypatch, reads):
    drive = Drive(tmp_path, monkeypatch)
    with drive.member():
        (drive.work / "steps" / "19-x.py").write_text("print(19)\n")
    stored = _propose(drive, "files", "一个脚本")
    with drive.pi():
        opened = proposals.open_for(drive.official, stored.id)
        assert opened["changes"]["official_now"] == proposals.snapshot_id(old_manifest(drive.official))
        assert opened["changes"]["moved"] is False and opened["changes"]["approvable"]
        reads.clear()
        again = proposals.open_for(drive.official, stored.id)
        assert not [p for p in reads if drive.official in p.parents and p.name != "19-x.py"], reads
        assert again["changes"]["official_now"] == opened["changes"]["official_now"]
        assert not [p for p in reads if p.parent == drive.official]  # nothing of the official folder was hashed again
    with drive.member():
        reads.clear()
        (drive.work / "steps" / "20-y.py").write_text("print(20)\n")
        draft = proposals.make_proposal(drive.work, "files", "another")
        assert draft.data["base"] == proposals.read_base(drive.pid).snapshot
        assert sorted(p.name for p in reads if drive.work in p.parents and "steps" in p.parts
                      and p.name.startswith("2")) in (["20-y.py"], ["19-x.py", "20-y.py"])
        assert not [p for p in reads if p.name in ("paper.md", "map.md", "panel.csv")]
        assert proposals.snapshot_id(proposals.manifest(drive.work)) == proposals.snapshot_id(old_manifest(drive.work))


@needs_ssh
def test_approving_leaves_the_same_snapshot_and_decision_with_the_fingerprints_as_without(tmp_path, monkeypatch, reads):
    drive = Drive(tmp_path, monkeypatch)
    with drive.member():
        (drive.work / "steps" / "19-x.py").write_text("print(19)\n")
    stored = _propose(drive, "files", "一个脚本")
    with drive.pi():
        done = proposals.approve(drive.official, stored.id, note="同意", now=FIXED_NOW)
        assert done["published"]["published"] and done["published"]["seq"] == 2
        assert "steps/19-x.py" in done["published"]["changed"]
        assert (drive.official / "steps" / "19-x.py").read_text() == "print(19)\n"
        official_now = proposals.snapshot_id(proposals.manifest(drive.official))
        assert official_now == proposals.snapshot_id(old_manifest(drive.official))
        snaps, _ = share.read_snapshots(drive.shared)
        last = snaps[-1]
        assert {rel: e["sha256"] for rel, e in last.files.items() if not rel.startswith(".researchos")} \
            == {rel: e["sha256"] for rel, e in old_manifest(drive.official).items()}
    with drive.member():
        synced = share.sync(drive.work)
        assert synced["seq"] == 2 and _sha(drive.work / "steps" / "19-x.py") == last.files["steps/19-x.py"]["sha256"]
        assert proposals.read_base(drive.pid).snapshot == official_now


@needs_ssh
def test_a_members_marks_are_the_same_with_what_was_remembered_and_the_page_writes_nothing_more(tmp_path, monkeypatch, writes):
    from researchos import officialness

    drive = Drive(tmp_path, monkeypatch)
    with drive.member():
        (drive.work / "steps" / "19-x.py").write_text("print(19)\n")
        (drive.work / "paper.md").write_text("# my own version of the paper\n")
        os.unlink(drive.work / "figs" / "fig1.png")
    stored = _propose(drive, "files", "一个脚本", only=["steps/19-x.py"])
    with drive.member():
        cache = paths.index_dir(drive.pid) / share.CACHE_FILENAME

        def marks():
            seen = officialness.view(drive.work)
            payload = {"files": [{"path": rel} for rel in ("steps/19-x.py", "paper.md", "figs/fig1.png", "map.md")]}
            officialness.annotate(seen, payload)
            return seen.current, seen.proposed_files, seen.proposal_states, payload

        forget()
        cold = marks()
        stamp = cache.stat().st_mtime_ns, cache.stat().st_ino
        written = len([p for p in writes if p == cache])
        warm = marks()
        again = marks()
        assert cold == warm == again
        assert [p for p in writes if p == cache] and len([p for p in writes if p == cache]) == written
        assert (cache.stat().st_mtime_ns, cache.stat().st_ino) == stamp  # the page did not touch the file again
    states = {item["path"]: item[officialness.FIELD]["state"] for item in cold[3]["files"]}
    assert states == {"steps/19-x.py": "proposed", "paper.md": "mine", "figs/fig1.png": "mine", "map.md": "official"}
    assert stored.id in cold[2]

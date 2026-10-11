#!/usr/bin/env python3
"""Research OS V8 phase 5, item 4: every scenario of DESIGN.md 12.10 #1-#3
and #5 again, through the command line only.

Each step here runs the installed `researchos` (or `rce`) command in a
subprocess, as the researcher and their collaborator would type it -- no
call into the package. What the test itself does is what a person or a
sync service would do: write the project's files, rename the old hidden
folders, copy a folder the way a sync conflict does, sign a forged
manifest with someone else's key using ssh-keygen directly, and read files
back to compare their bytes.

Every run has its own homes: `HOME` points into tmp_path and
`RESEARCHOS_HOME` names each person's home, so nothing here reads or
writes the real ~/.researchos, ~/.rce or ~/.ssh; `RCE_ENGINE_PORT=0` keeps
the retire guard off the network, and the Zotero library is an empty
folder. Marked slow (deselect with -m "not slow"); CI runs it.

Run it on its own: python tests/test_v8_phase5_cli.py
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

from researchos import names  # the folder, variable and command names only (12.8); nothing calls the package
from test_v8_phase4 import Witness

pytestmark = pytest.mark.slow

SSH_KEYGEN = next((p for p in ("/usr/bin/ssh-keygen", "/usr/local/bin/ssh-keygen", "/opt/homebrew/bin/ssh-keygen",
                               "/bin/ssh-keygen") if os.access(p, os.X_OK)), None)
NEW_DIR, OLD_DIR = names.PROJECT_DIRNAME, names.LEGACY_PROJECT_DIRNAME
NEW_HOME, OLD_HOME = names.HOME_DIRNAME, names.LEGACY_HOME_DIRNAME
NEW_COMMAND, OLD_COMMAND = names.COMMAND, names.LEGACY_COMMAND
needs_ssh = pytest.mark.skipif(SSH_KEYGEN is None, reason="ssh-keygen is not installed here")

PI = ("李", "li@example.com")
MEMBER = ("王", "wang@example.com")
BIG = 200 * 1024 * 1024

ATTEMPTS_TOML = (
    'file = "map.md"\nheading = "Attempt timeline"\nsteps_dir = "steps"\n'
    '[columns]\nid = "#"\ndate = "Date"\ndescription = "Path"\nvariables = "Variables"\n'
    'result = "Result"\nverdict = "Verdict"\n'
)
BUILD = 'import pandas as pd\ndf = pd.read_csv("data/raw.csv")\ndf.to_csv("data/panel.csv")\n'
PLOT = ('import pandas as pd\nimport matplotlib.pyplot as plt\n'
        'df = pd.read_csv("data/panel.csv")\nplt.savefig("figs/fig2.png")\n')
PAPER = "# Results\n\n## Main figure\n\n![Figure 2](figs/fig2.png)\n\n![Figure 1](figs/fig1.png)\n"
HEADER = "# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n"
ROW_16 = "| 16 | 07-26 | TopicShift volatility (16-17) | TopicShift->RV | t=2.91 | {verdict} |\n"
ROW_18 = "| 18 | 08-02 | Big panel (18) | TopicShift->RV | t=1.10 | 🕒 |\n"
ROW_19 = "| 19 | 08-03 | Short panel (19) | TopicShift->RV | t=0.40 | 🕒 |\n"
NEW_SCRIPT = 'import pandas as pd\ndf = pd.read_csv("data/panel.csv")\ndf.to_csv("data/out18.csv")\n'
CARD = (
    'name = "TopicShift（叙事更替）"\naliases = ["TopicShift"]\n\n# 含义\n'
    "meaning = '''相邻两月新闻主题分布的差异'''\nunit = \"无量纲，0–1\"\ngranularity = \"月\"\n\n"
    '[[input]]\ndataset = "data/raw.csv"\nfields = ["id"]\n\n'
    "[construction]\nformula = '''TS_t = 1 − cos(p_t, p_{t−1})'''\nmissing = '''缺月不插值'''\n\n"
    '[implementation]\nscript = "steps/16-build.py"\noutput = "data/panel.csv"\nfield = "x"\n\n'
    "[decision]\nwhy = '''余弦距离对主题总量不敏感'''\ndecided_by = \"LL\"\nadopted_on = \"2026-07-26\"\n"
)


def _program(name: str) -> list[str]:
    """The installed command beside this Python, or its entry point."""
    script = Path(sys.executable).with_name(name)
    if script.is_file():
        return [str(script)]
    entry = "legacy_main" if name == OLD_COMMAND else "main"
    return [sys.executable, "-c", f"import sys; from researchos.cli import {entry}; sys.exit({entry}())"]


@dataclass
class Ran:
    code: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return self.out + self.err


class Terminal:
    """Two people's terminals on one machine: each command runs with that
    person's home; HOME is a throwaway folder for everyone."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.user = tmp_path / "user"
        self.user.mkdir()
        zotero = tmp_path / "zotero-none"
        zotero.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if k not in (names.HOME_ENV_VAR, names.LEGACY_HOME_ENV_VAR, "RCE_ZOTERO_DATA_DIR", "RCE_ENGINE_PORT")}
        self.env.update(HOME=str(self.user), RCE_ENGINE_PORT="0", RCE_ZOTERO_DATA_DIR=str(zotero),
                        PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        self.homes = {"pi": tmp_path / "home-pi", "member": tmp_path / "home-member",
                      "forger": tmp_path / "home-forger", "one": tmp_path / "home-one"}
        self.after = None  # called after every command (12.10 #5's witness)

    def run(self, who: str | None, *argv: object, expect: int | None = 0, program: str = NEW_COMMAND,
            cwd: Path | None = None) -> Ran:
        env = dict(self.env)
        if who is not None:
            env[names.HOME_ENV_VAR] = str(self.homes[who])
        done = subprocess.run([*_program(program), *(str(a) for a in argv)], env=env, cwd=cwd or self.tmp,
                              capture_output=True, text=True, encoding="utf-8", timeout=600)
        ran = Ran(done.returncode, done.stdout, done.stderr)
        if expect is not None:
            assert ran.code == expect, f"researchos {' '.join(map(str, argv))} -> {ran.code}\n{ran.text}"
        if self.after is not None:
            self.after()
        return ran


def _write_project(root: Path) -> None:
    for sub in ("steps", "data", "figs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "data" / "raw.csv").write_text("id,x\n0,1\n1,2\n2,3\n")
    (root / "data" / "panel.csv").write_text("id,x\n" + "".join(f"{i * 10},{i * 10 + 1}\n" for i in range(96)))
    (root / "figs" / "fig2.png").write_bytes(b"\x89PNG fig2")
    (root / "figs" / "fig1.png").write_bytes(b"\x89PNG fig1")
    (root / "steps" / "16-build.py").write_text(BUILD)
    (root / "steps" / "17-plot.py").write_text(PLOT)
    (root / "paper.md").write_text(PAPER)
    (root / "map.md").write_text(HEADER + ROW_16.format(verdict="🕒"))


def _make_project(term: Terminal, who: str, root: Path) -> None:
    """A project as a researcher sets one up: files, `researchos init`, the
    attempt-table configuration they write, a scan of sources and table."""
    _write_project(root)
    term.run(who, "init", root)
    (root / NEW_DIR / "attempts.toml").write_text(ATTEMPTS_TOML)
    term.run(who, "ingest", root)
    term.run(who, "attempts", root)


def _tree(top: Path) -> dict[str, bytes]:
    return {p.relative_to(top).as_posix(): p.read_bytes() for p in sorted(top.rglob("*")) if p.is_file()}


def _sparse(path: Path, size: int = BIG) -> None:
    with open(path, "wb") as handle:
        handle.write(b"RAW\x00header")
        handle.truncate(size)


# -- 12.10 #1: rename and migration ------------------------------------------------------------------


def test_12_10_1_rename_and_migration_through_the_cli(tmp_path):
    """12.10 #1 through the CLI: a project and a home in the old layout
    (`.rce/`, `~/.rce`) are renamed on first touch; ledgers and cards are
    byte-identical; `researchos` and `rce` both work; a failure injected at
    each step the command line can reach (the home's rename, the project's
    rename, a folder that has both names) leaves the old folders exactly as
    they were. (A failure inside the read-back verification is injected in
    tests/test_v8_rename.py, which can reach it.)"""
    term = Terminal(tmp_path)
    root = tmp_path / "proj"
    _make_project(term, None, root)
    term.run(None, "me", "set", "--name", PI[0], "--email", PI[1])
    term.run(None, "note", "add", "steps/16-build.py", "先跑这个", root)
    term.run(None, "link", "steps/17-plot.py", "figs/fig1.png", "--type", "generates", root)
    term.run(None, "variable", "new", "topicshift", root)
    (root / NEW_DIR / "variables" / "topicshift" / "v1.toml").write_text(CARD, encoding="utf-8")
    term.run(None, "variable", "confirm", "topicshift", "--attest", "unknown", root)
    os.rename(root / NEW_DIR, root / OLD_DIR)
    os.rename(term.user / NEW_HOME, term.user / OLD_HOME)
    project_before, home_before = _tree(root / OLD_DIR), _tree(term.user / OLD_HOME)
    assert {"project.toml", "notes.toml", "mappings.toml", "variables/topicshift/log.toml"} <= set(project_before)

    can_deny = os.geteuid() != 0  # root ignores folder permissions
    if can_deny:
        os.chmod(term.user, 0o555)  # the home cannot be renamed
        try:
            failed = term.run(None, "status", root, expect=1)
        finally:
            os.chmod(term.user, 0o755)
        assert "nothing was renamed" in failed.err or "could not rename" in failed.err
        assert _tree(term.user / OLD_HOME) == home_before and not (term.user / NEW_HOME).exists()
        assert _tree(root / OLD_DIR) == project_before and not (root / NEW_DIR).exists()
        os.chmod(root, 0o555)  # the project's folder cannot be renamed
        try:
            failed = term.run(None, "status", root, expect=1, program=OLD_COMMAND)
        finally:
            os.chmod(root, 0o755)
        assert "rce is now researchos" in failed.err and "nothing was renamed" in failed.err
        assert _tree(root / OLD_DIR) == project_before and not (root / NEW_DIR).exists()

    (root / NEW_DIR).mkdir()  # both names: nothing is chosen, nothing touched
    both = term.run(None, "status", root, expect=1)
    assert "touched neither" in both.err
    assert _tree(root / OLD_DIR) == project_before
    (root / NEW_DIR).rmdir()

    renamed = term.run(None, "status", root, program=OLD_COMMAND)
    assert "rce is now researchos" in renamed.err and "verified" in renamed.text
    assert not (root / OLD_DIR).exists() and not (term.user / OLD_HOME).exists()
    after = _tree(root / NEW_DIR)
    for rel in ("project.toml", "notes.toml", "mappings.toml", "attempts.toml", "variables/topicshift/log.toml",
                "variables/topicshift/v1.toml"):
        assert after[rel] == project_before[rel], rel
    assert set(project_before) <= set(after)
    home_after = _tree(term.user / NEW_HOME)
    assert "me.toml" in home_before
    for rel in home_before:
        if not rel.startswith(("graphs/", "locks/")):  # the index is derived; it is rebuilt, not a record
            assert home_after[rel] == home_before[rel], rel
    out = term.run(None, "status", root).out
    assert "Who I am: 李" in out and "Notes: 1 note on 1 node" in out
    assert "先跑这个" in term.run(None, "note", "list", root).out
    assert "topicshift" in term.run(None, "variable", "list", root).out
    assert "steps/17-plot.py" in term.run(None, "trace", "--chain", "figs/fig1.png", root, program=OLD_COMMAND).out


# -- 12.10 #2: one person -----------------------------------------------------------------------------


def test_12_10_2_one_person_through_the_cli(tmp_path):
    """12.10 #2 through the CLI: no approval step anywhere; history and
    notes on a script, a data file and an attempt; a figure traced to data,
    scripts and attempt; 「补上来源」 (link --type generates) and 「关联文件」
    (link --type belongs_to) repair a broken trace."""
    term = Terminal(tmp_path)
    root = tmp_path / "proj"
    _make_project(term, "one", root)
    term.run("one", "me", "set", "--name", PI[0], "--email", PI[1])
    refused = term.run("one", "propose", "files", "--sentence", "x", root, expect=1)
    assert "single-person project has no proposals" in refused.err
    assert "single" in term.run("one", "proposals", root).out
    assert "My role: one person -- no approval step" in term.run("one", "status", root).out

    for node, text in (("steps/16-build.py", "先跑这个"), ("data/panel.csv", "96 行"), ("attempt:map.md#16", "看 t 值")):
        assert "(by li@example.com)" in term.run("one", "note", "add", node, text, root).out
    listed = term.run("one", "note", "list", root).out
    assert "Notes: 3" in listed and all(t in listed for t in ("先跑这个", "96 行", "看 t 值"))

    (root / "data" / "panel.csv").write_text("id,x\n" + "".join(f"{i * 10},{i * 10 + 1}\n" for i in range(97)))
    (root / "steps" / "16-build.py").write_text(BUILD + "# v2\n")
    (root / "map.md").write_text(HEADER + ROW_16.format(verdict="✅").replace("t=2.91", "t=2.95"))
    term.run("one", "ingest", root)
    term.run("one", "attempts", root)
    assert "2 version(s)" in term.run("one", "history", "data/panel.csv", root).out
    assert "行数 96 → 97，列不变" in term.run("one", "diff", "data/panel.csv", root).out
    assert "+# v2" in term.run("one", "diff", "steps/16-build.py", root).out
    attempt = term.run("one", "history", "attempt:map.md#16", root).out
    assert "verdict=✅ result=t=2.95" in attempt and "verdict=🕒 result=t=2.91" in attempt

    traced = term.run("one", "trace", "--chain", "figs/fig2.png", root).out
    assert all(s in traced for s in ("data/panel.csv", "data/raw.csv", "steps/17-plot.py", "steps/16-build.py",
                                     "attempt:map.md#16"))
    broken = term.run("one", "trace", "--chain", "figs/fig1.png", root).out
    assert "BREAK   no_generating_script" in broken and "BREAK   no_attempt" in broken
    term.run("one", "link", "steps/17-plot.py", "figs/fig1.png", "--type", "generates", root)
    term.run("one", "link", "figs/fig1.png", "attempt:map.md#16", "--type", "belongs_to", root)
    repaired = term.run("one", "trace", "--chain", "figs/fig1.png", root).out
    assert "BREAK" not in repaired and "steps/17-plot.py" in repaired and "attempt:map.md#16" in repaired
    status = term.run("one", "status", root).out
    assert "Notes: 3 notes on 3 nodes" in status and "History: " in status


# -- 12.10 #3 and #5: two people, simulated ----------------------------------------------------------


def _ids(term: Terminal, who: str, root: Path, state: str) -> list[str]:
    out = term.run(who, "proposals", root).out
    return [m.group(1) for m in re.finditer(rf"^\s+(pr-[0-9a-f]{{32}}) {state}\b", out, re.M)]


def _forge(term: Terminal, shared: Path) -> int:
    """Someone with write access to the shared folder, not the PI, writes a
    snapshot: the latest manifest renumbered and linked, one fingerprint
    changed, signed with their own key, ready.toml beside it."""
    term.run("forger", "me", "key")
    key = term.homes["forger"] / "keys" / "id_ed25519"
    area = shared / "已发布" / "snapshots"
    last = max(int(p.name) for p in area.iterdir() if p.name.isdigit())
    raw = (area / str(last) / "manifest.toml").read_text(encoding="utf-8")
    sha = hashlib.sha256((area / str(last) / "manifest.toml").read_bytes()).hexdigest()
    text = re.sub(r"^seq = \d+$", f"seq = {last + 1}", raw, count=1, flags=re.M)
    text = re.sub(r"^prev_seq = \d+$", f"prev_seq = {last}", text, count=1, flags=re.M)
    text = re.sub(r'^prev_manifest_sha256 = "[0-9a-f]+"$', f'prev_manifest_sha256 = "{sha}"', text, count=1, flags=re.M)
    work = term.tmp / "forging"
    work.mkdir()
    (work / "manifest.toml").write_text(text, encoding="utf-8")
    subprocess.run([SSH_KEYGEN, "-Y", "sign", "-f", str(key), "-n", "researchos", str(work / "manifest.toml")],
                   check=True, capture_output=True, env={"HOME": str(term.user), "PATH": "/usr/bin:/bin"})
    digest = hashlib.sha256((work / "manifest.toml").read_bytes()).hexdigest()
    (work / "ready.toml").write_text(f'snapshot = "{last + 1}"\nsha256 = "{digest}"\n')
    os.rename(work, area / str(last + 1))
    return last + 1


@needs_ssh
def test_12_10_3_and_5_two_people_through_the_cli(tmp_path):
    """12.10 #3 through the CLI, the setup walked with `researchos
    collaborate`: join; a member proposes a new attempt with a script and a
    200 MB data file; it is no node until approved; approved -- applied,
    published, synced, recorded with by and approved_by; a returned
    proposal shows its reason and never becomes a node; a verdict change to
    ☠️ without a reason is refused; both sides changing one file -- the PI
    is told, no merge; a forged snapshot is refused by the member; a
    half-written proposal is ignored; a conflict copy is reported and never
    read. 12.10 #5: after every command, every file in the shared folder was
    created once and never modified (inode, mtime and size)."""
    term = Terminal(tmp_path)
    shared, official, work = tmp_path / "shared", tmp_path / "official", tmp_path / "work"
    shared.mkdir()
    witness = Witness(shared)
    term.after = witness.look
    keyfile = tmp_path / "wang.pub"

    # -- setting up, guided (item 2) --
    _make_project(term, "pi", official)
    assert "--as pi" not in term.run("pi", "collaborate", official).err  # the project says who keeps it
    refused = term.run("pi", "collaborate", "publish", "--yes", official, expect=1)
    assert "comes after step 1 'me'" in refused.err
    term.run("pi", "collaborate", "me", "--name", PI[0], "--email", PI[1], "--yes", official)
    term.run("pi", "collaborate", "team", "--yes", official)
    term.run("pi", "collaborate", "share", "--shared", shared, "--yes", official)
    published = term.run("pi", "collaborate", "publish", "--yes", official)
    assert "Published snapshot 1" in published.out and "signed" in published.out
    term.run("member", "collaborate", "me", "--as", "member", "--name", MEMBER[0], "--email", MEMBER[1], "--yes")
    term.run("member", "collaborate", "key", "--as", "member", "--out", keyfile, "--yes")
    joined = term.run("member", "collaborate", "join", "--as", "member", "--shared", shared, "--folder", work, "--yes")
    assert "trusted on first use" in joined.out
    term.run("pi", "collaborate", "member", "--member", MEMBER[1], "--member-name", MEMBER[0], "--key", keyfile,
             "--yes", official)
    term.run("pi", "collaborate", "member", "--yes", official)  # publish the team that names the member
    term.run("member", "collaborate", "added", "--yes", work)
    assert "Set up." in term.run("pi", "collaborate", official).out
    assert "Set up." in term.run("member", "collaborate", work).out
    status = term.run("member", "status", work).out
    assert "My role: a member" in status and "Your copy: snapshot 2 -- up to date" in status
    assert "signature verified with the PI's key" in status

    # -- a new attempt with a script and a 200 MB file --
    (work / "steps" / "18-big.py").write_text(NEW_SCRIPT)
    _sparse(work / "data" / "big.bin")
    (work / "map.md").write_text(HEADER + ROW_16.format(verdict="🕒") + ROW_18)
    dry = term.run("member", "propose", "attempt_new", "--sentence", "加一条大面板的尝试", work)
    # 12.12: the PI made the member's area, empty; a dry run puts nothing in it.
    assert "Dry run: nothing written" in dry.out and not [p for p in (shared / "提议").rglob("*") if p.is_file()]
    sent = term.run("member", "propose", "attempt_new", "--sentence", "加一条大面板的尝试", "--yes", work)
    assert "data/big.bin 209715200B (blob)" in sent.out
    [first] = _ids(term, "member", work, "pending")
    assert "My proposals: 1 pending" in term.run("member", "status", work).out

    term.run("pi", "ingest", official)
    term.run("pi", "attempts", official)
    for node in ("attempt:map.md#18", "script:steps/18-big.py", "dataset:data/big.bin"):
        term.run("pi", "query", node, official, expect=1)  # no node anywhere until approved
    assert "#18" not in term.run("pi", "attempts", official).out
    traced = term.run("pi", "trace", "--chain", "figs/fig2.png", official).out
    assert "#18" not in traced and "18-big" not in traced
    assert f"{first} pending" in term.run("pi", "inbox", official).out and "[signed]" in term.run("pi", "inbox", official).out
    assert "Proposals: 1 waiting for your decision" in term.run("pi", "status", official).out
    shown = term.run("pi", "proposals", "--show", first, official).out
    assert "+| 18 | 08-02 | Big panel (18)" in shown and "approvable" in shown and "NOT approvable" not in shown

    approved = term.run("pi", "approve", first, "--note", "同意", official)
    assert "(by wang@example.com, approved_by li@example.com)" in approved.out
    assert "Published snapshot 3 for the members: signed" in approved.out
    term.run("pi", "query", "attempt:map.md#18", official)
    term.run("pi", "query", "script:steps/18-big.py", official)
    assert (official / "data" / "big.bin").stat().st_size == BIG
    assert "wang@example.com (approved by li@example.com)" in term.run("pi", "history", "steps/18-big.py", official).out
    decisions = tomllib.loads((official / NEW_DIR / "decisions.toml").read_text(encoding="utf-8"))
    [decision] = [d for d in decisions["decision"] if d.get("proposal") == first]
    assert decisions and decision["by"] == MEMBER[1] and decision["approved_by"] == PI[1]
    synced = term.run("member", "share", "sync", work).out
    assert "Synced snapshot 2 -> 3" in synced
    assert (work / NEW_DIR / "decisions.toml").read_bytes() == (official / NEW_DIR / "decisions.toml").read_bytes()
    mine = term.run("member", "proposals", work).out
    assert f"{first} approved" in mine and "approved: 同意" in mine

    # -- returned: the reason reaches the member; never a node --
    (work / "steps" / "19-short.py").write_text("print(19)\n")
    (work / "map.md").write_text((official / "map.md").read_text() + ROW_19)
    term.run("member", "propose", "attempt_new", "--sentence", "短面板", "--yes", work)
    [second] = _ids(term, "member", work, "pending")
    assert f"Returned {second}" in term.run("pi", "return", second, "--reason", "数据来源不清楚", official).out
    mine = term.run("member", "proposals", work).out
    assert f"{second} returned" in mine and "returned: 数据来源不清楚" in mine
    assert f"returned {second}: 数据来源不清楚" in term.run("member", "status", work).out
    term.run("pi", "attempts", official)
    term.run("pi", "query", "attempt:map.md#19", official, expect=1)
    assert "Up to date" in term.run("member", "share", "sync", work).out
    (work / "steps" / "19-short.py").unlink()
    (work / "map.md").write_text((official / "map.md").read_text())

    # -- abandoning without a reason is refused; nothing reaches the shared drive --
    before = sorted(p.name for p in (shared / "提议" / MEMBER[1]).iterdir())
    (work / "map.md").write_text((official / "map.md").read_text().replace("| t=2.91 | 🕒 |", "| t=0.20 | ☠️ |"))
    refused = term.run("member", "propose", "verdict_change", "--sentence", "放弃16", "--result", "t=0.20", "--yes",
                       work, expect=1)
    assert "needs its result and a reason" in refused.err
    assert sorted(p.name for p in (shared / "提议" / MEMBER[1]).iterdir()) == before
    (work / "map.md").write_text((official / "map.md").read_text())

    # -- both sides change one file: the PI is told; nothing is merged --
    (work / "paper.md").write_text(PAPER + "member edit\n")
    term.run("member", "propose", "files", "--sentence", "改论文", "--yes", work)
    [third] = _ids(term, "member", work, "pending")
    (official / "paper.md").write_text(PAPER + "pi edit\n")
    assert "Published snapshot 4" in term.run("pi", "share", "publish", official).out
    shown = term.run("pi", "proposals", "--show", third, official).out
    assert "CONFLICT paper.md" in shown and "NOT approvable" in shown
    refused = term.run("pi", "approve", third, official, expect=1)
    assert "both sides changed: paper.md" in refused.err
    assert (official / "paper.md").read_text() == PAPER + "pi edit\n"
    synced = term.run("member", "share", "sync", work).out
    assert "BOTH CHANGED paper.md: yours kept, the official one is paper.official.md" in synced
    assert (work / "paper.md").read_text() == PAPER + "member edit\n"
    assert (work / "paper.official.md").read_text() == PAPER + "pi edit\n"
    assert "Official versions beside yours" in term.run("member", "status", work).out

    # -- a forged snapshot is refused; the last good one is kept --
    forged = _forge(term, shared)
    refused = term.run("member", "share", "sync", work).out
    assert "Up to date at snapshot 4" in refused and f"REFUSED snapshot {forged} (bad)" in refused
    status = term.run("member", "status", work).out
    assert f"Refused snapshot {forged}: its signature is not the PI's key" in status
    assert "Your copy: snapshot 4 -- up to date" in status
    assert f"Refused snapshot {forged}" in term.run("pi", "status", official).out

    # -- a half-written proposal is ignored; a conflict copy reported, never read --
    area = shared / "提议" / MEMBER[1]
    half = area / ("pr-" + "0123456789abcdef" * 2)
    half.mkdir()
    shutil.copy2(area / first / "proposal.toml", half / "proposal.toml")
    shutil.copytree(area / third, area / f"{third} (1)")
    inbox = term.run("pi", "inbox", official).out
    assert f"{half.name} (half_written" in inbox and f"{third} (1) (unexpected" in inbox
    assert inbox.count("[signed]") == 3  # the three proposals Research OS wrote, nothing else
    term.run("pi", "approve", half.name, official, expect=1)
    witness.look()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-p", "no:cacheprovider"]))

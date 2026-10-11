"""DESIGN.md 12.8 and 12.10 scenario 1: the rename to Research OS.

Fixtures are built the old way: a project and a home made by the engine,
then given exactly the layout an RCE install left behind -- the project's
records under `.rce/`, the machine's home at `~/.rce` (a throwaway user
home: `HOME` points into tmp_path and neither home variable is set, which
is the only situation in which the home is renamed). The layouts of the
two versions differ in nothing but those two names, so the bytes under
them are what an old install wrote.

Every test here names its 12.10 scenario.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from researchos import cli, db, inventory, migration, names, paths, rename
from researchos import project as project_identity
from researchos.records import cards, judgements
from researchos.records import files as record_files
from researchos.records import ledger as ledger_mod
from researchos.records.identity import read_identity
from researchos.records.situation import Situation, index_db_path
from researchos.webapp import canvas, macapp, registry
from researchos.webapp import server as webapp_server

from test_variable_cards import SCRIPT, _text, _write

REPO = Path(__file__).resolve().parents[1]
READ = ("script:build.py", "dataset:Data/theme.csv", "reads", "dataflow")
OLD_DIR, NEW_DIR = names.LEGACY_PROJECT_DIRNAME, names.PROJECT_DIRNAME
MAPPINGS = '[[mapping]]\nfrom = "Data/ts.csv"\nto   = "build.py"\ntype = "reads"\nnote = "手画的"\n'


# -- the old layout -----------------------------------------------------------------------


@pytest.fixture
def user_home(tmp_path, monkeypatch) -> Path:
    """A throwaway user home with neither home variable set: the home is
    the default one, under it."""
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.delenv(names.HOME_ENV_VAR, raising=False)
    monkeypatch.delenv(names.LEGACY_HOME_ENV_VAR, raising=False)
    return user


def _build(root: Path) -> str:
    """A project with every kind of record: identity (and its snapshot in
    backups/), a judgment, a hand-drawn link, a canvas arrangement, a
    confirmed variable card with its frozen and _code copies; registered,
    indexed (graphs/), and locked once (locks/)."""
    (root / "Data").mkdir(parents=True)
    (root / "Data" / "theme.csv").write_text("month,theme,count\n2024-01,a,3\n")
    (root / "Data" / "ts.csv").write_text("month,topicshift\n2024-01,0.1\n")
    (root / "build.py").write_text(SCRIPT)
    assert cli.main(["init", str(root)]) == 0
    assert cli.main(["ingest", str(root)]) == 0
    pid = read_identity(root).identity.id
    judgements.judge(root, READ, "confirmed", via="cli", note="读的是主题表")
    (paths.project_dir(root) / "mappings.toml").write_text(MAPPINGS, encoding="utf-8")
    assert cli.main(["mappings", str(root)]) == 0
    conn = db.connect(index_db_path(pid))
    try:
        canvas.save_layout(conn, root, {"scope": "all", "positions": {"script:build.py": [10, 20]}})
    finally:
        conn.close()
    cards.new_card(root, "topicshift")
    _write(root, "topicshift", 1, _text())
    cards.confirm(root, "topicshift", attested="yes")
    registry.register(Path(paths._canonical_path(root)), pid)
    return pid


def _make_old(root: Path, user: Path) -> None:
    """Give the project and the home the names an RCE install used."""
    os.rename(root / NEW_DIR, root / OLD_DIR)
    os.rename(user / names.HOME_DIRNAME, user / names.LEGACY_HOME_DIRNAME)


@pytest.fixture
def old_layout(tmp_path, user_home):
    root = tmp_path / "proj"
    pid = _build(root)
    _make_old(root, user_home)
    return root, user_home, pid


def _tree(top: Path) -> dict[str, object]:
    """Everything under `top`: bytes for a file, the target for a link,
    "dir" for a directory -- independent of `researchos.rename`'s own."""
    found: dict[str, object] = {}
    for path in sorted(top.rglob("*")):
        rel = path.relative_to(top).as_posix()
        if path.is_symlink():
            found[rel] = ("link", os.readlink(path))
        elif path.is_dir():
            found[rel] = "dir"
        else:
            found[rel] = path.read_bytes()
    return found


def _snapshot(root: Path, user: Path) -> dict[str, dict[str, object]]:
    return {
        "project": _tree(root / OLD_DIR),
        "home": _tree(user / names.LEGACY_HOME_DIRNAME),
    }


def _ledgers(root: Path) -> dict[str, bytes]:
    record = paths.project_dir(root)
    return {
        rel: (record / rel).read_bytes()
        for rel in ("project.toml", "judgements.toml", "mappings.toml", "canvas.json", "variables/topicshift/log.toml")
    }


# -- 12.10 #1: rename and migration -------------------------------------------------------


def test_12_10_1_old_layout_is_renamed_byte_identical(old_layout):
    """12.10 #1: `.rce/` and `~/.rce` take their new names in one rename
    each; every file reads back with the same bytes; the identity, the
    ledger and the card log parse with the same id and entries."""
    root, user, pid = old_layout
    before = _snapshot(root, user)
    assert len(before["project"]) > 10 and "variables/_code" in before["project"]
    assert any(k.startswith("backups/") for k in before["project"])
    assert {"projects.json", "graphs", "locks"} <= set(before["home"])
    records_before = rename.project_records(root)  # read through the old name

    done = rename.ensure_renamed(root)

    assert [(d.old.name, d.new.name) for d in done] == [
        (names.LEGACY_HOME_DIRNAME, names.HOME_DIRNAME), (OLD_DIR, NEW_DIR)]
    assert not (root / OLD_DIR).exists() and not (user / names.LEGACY_HOME_DIRNAME).exists()
    after_project = _tree(root / NEW_DIR)
    readme = after_project.pop("README")
    assert {k: v for k, v in before["project"].items() if k != "README"} == after_project
    assert NEW_DIR.encode() in readme  # the signpost rewritten after the verified rename
    assert _tree(user / names.HOME_DIRNAME) == before["home"]
    assert rename.project_records(root) == records_before
    assert records_before["identity"] == ("present", pid)
    assert len(records_before["judgements"][1]) == 1 and len(records_before["card:topicshift"][1]) == 1


def test_12_10_1_both_commands_work_after_the_rename(old_layout, capsys):
    """12.10 #1: after the rename `researchos` and the `rce` alias both
    work; the alias says the new name in one stderr line first."""
    root, user, pid = old_layout
    assert cli.main(["status", str(root)]) == 0
    assert (root / NEW_DIR / "judgements.toml").is_file() and (user / names.HOME_DIRNAME).is_dir()
    capsys.readouterr()
    assert cli.legacy_main(["records", "--verify", str(root)]) == 0
    err = capsys.readouterr().err
    assert err.splitlines()[0] == "rce is now researchos"
    assert err.count("rce is now researchos") == 1
    opened = project_identity.open_project(root)
    assert opened.situation is Situation.NORMAL and opened.project_id == pid
    assert registry.load()[0]["id"] == pid  # the registry read back from the renamed home
    assert judgements_status(root) == "confirmed"


def judgements_status(root: Path) -> str | None:
    conn = db.connect(index_db_path(read_identity(root).identity.id))
    try:
        return db.edge_statuses(conn).get(READ, (None, None))[0]
    finally:
        conn.close()


def _console_script(name: str) -> Path:
    path = Path(sys.executable).parent / name
    if not path.is_file():
        pytest.skip(f"no '{name}' console script next to this interpreter")
    return path


def test_12_10_1_the_installed_commands_rename_on_first_touch(old_layout):
    """12.10 #1, end to end through the installed console scripts: the
    first command (`rce`, the alias) renames both folders; `researchos`
    then reads the same records."""
    root, user, pid = old_layout
    ledgers = None
    env = {k: v for k, v in os.environ.items() if k not in (names.HOME_ENV_VAR, names.LEGACY_HOME_ENV_VAR)}
    env.update(HOME=str(user), RCE_ENGINE_PORT="0")
    old = subprocess.run([str(_console_script("rce")), "status", str(root)], env=env, capture_output=True, text=True)
    assert old.returncode == 0, old.stderr
    assert old.stderr.splitlines()[0] == "rce is now researchos"
    assert "renamed its home folder" in old.stderr and f"{OLD_DIR} to " in old.stderr
    ledgers = _ledgers(root)
    new = subprocess.run([str(_console_script("researchos")), "records", "--verify", str(root)],
                         env=env, capture_output=True, text=True)
    assert new.returncode == 0, new.stderr + new.stdout
    assert "rce is now" not in new.stderr
    assert _ledgers(root) == ledgers
    assert not (root / OLD_DIR).exists() and not (user / names.LEGACY_HOME_DIRNAME).exists()


def test_12_10_1_the_server_renames_on_first_touch(old_layout):
    """12.10 #1: opening an old project in the app (the server's identity
    check) renames it like a command does."""
    root, user, pid = old_layout
    served = webapp_server.served_for(root)
    assert served.blocked is None and served.project_id == pid
    assert (root / NEW_DIR / "project.toml").is_file() and not (root / OLD_DIR).exists()


def test_12_10_1_adding_an_old_project_from_the_app_inspects_it_then_renames_it(old_layout):
    """12.10 #1 with 10.2: inspecting an old folder writes nothing and
    recognises its identity through the old name; adding it renames it
    (the identity check is the first touch) and registers it by its id."""
    from researchos import addproject

    root, user, pid = old_layout
    registry.remove(str(Path(paths._canonical_path(root))))
    before = _tree(root / OLD_DIR)
    insp = addproject.inspect(root)
    assert insp.kind == addproject.RESEARCHOS_PROJECT and insp.project_id == pid
    assert _tree(root / OLD_DIR) == before and not (root / NEW_DIR).exists()
    added = addproject.add(root, label="旧项目", inspected=insp.token)
    assert added.project_id == pid and not (root / OLD_DIR).exists()
    assert (root / NEW_DIR / "judgements.toml").read_bytes() == before["judgements.toml"]
    assert [e["label"] for e in registry.load() if e["id"] == pid] == ["旧项目"]


# -- 12.10 #1: a failure at each step leaves the old folders as they were -------------------


def _fail_at(monkeypatch, step: str, parent: str) -> None:
    """Inject a failure into the rename of the old folder inside the
    directory called `parent` ("user": the home, "proj": the project)."""
    if step == "verify_mismatch":
        real = rename.take_manifest

        def lying(top: Path):
            got = real(top)
            if top.name == names.PROJECT_DIRNAME and top.parent.name == parent:
                got = dict(got)
                got.pop(sorted(got)[-1])  # the copy "lost" an entry
            return got

        monkeypatch.setattr(rename, "take_manifest", lying)
        return

    def checkpoint(at: str, folder: Path) -> None:
        if at == step and folder.parent.name == parent:
            raise OSError(f"injected failure at {step}")

    monkeypatch.setattr(rename, "_checkpoint", checkpoint)


@pytest.mark.parametrize("step", ["before_rename", "after_rename", "verify_mismatch"])
@pytest.mark.parametrize("which", ["home", "project"])
def test_12_10_1_a_failure_at_each_step_leaves_the_old_folders_untouched(old_layout, monkeypatch, capsys, which, step):
    """12.10 #1: a failure injected before the rename, after the rename but
    before the verification, or as a verification mismatch: the command
    stops with the reason, and the old folder is exactly as it was --
    every entry and every byte -- with no new folder beside it. Once the
    cause is gone the next command renames it."""
    root, user, pid = old_layout
    _fail_at(monkeypatch, step, "user" if which == "home" else "proj")
    before = _snapshot(root, user)
    capsys.readouterr()

    assert cli.main(["status", str(root)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error:")
    if step == "before_rename":
        assert "injected failure" in err
    else:
        assert "exactly as it was" in err
    assert _tree(root / OLD_DIR) == before["project"] and not (root / NEW_DIR).exists()
    if which == "home":
        assert _tree(user / names.LEGACY_HOME_DIRNAME) == before["home"]
        assert not (user / names.HOME_DIRNAME).exists()
    else:
        # The home was renamed first, verified, and stays renamed.
        assert _tree(user / names.HOME_DIRNAME) == before["home"]

    monkeypatch.undo()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.delenv(names.HOME_ENV_VAR, raising=False)
    monkeypatch.setenv("RCE_ENGINE_PORT", "0")
    assert cli.main(["status", str(root)]) == 0
    assert not (root / OLD_DIR).exists() and (root / NEW_DIR / "judgements.toml").is_file()


def test_12_10_1_a_failure_in_the_server_is_a_blocked_project_not_a_crash(old_layout, monkeypatch):
    """12.10 #1 in the app: the project is served blocked, with the
    rename's own Chinese sentence and its English detail; nothing moved."""
    root, user, pid = old_layout
    rename.ensure_home_renamed()
    _fail_at(monkeypatch, "after_rename", "proj")
    before = _tree(root / OLD_DIR)
    served = webapp_server.served_for(root)
    assert served.blocked["situation"] == "rename_blocked" and served.blocked["answers"] == []
    assert served.blocked["message"] == rename.RenameError.message_zh
    assert "exactly as it was" in served.blocked["detail"]
    assert _tree(root / OLD_DIR) == before and not (root / NEW_DIR).exists()


# -- both names, a cloud file, an old engine ------------------------------------------------


def test_12_10_1_a_project_with_both_folders_is_left_untouched_and_reported(old_layout, capsys):
    """12.8: a folder that has both `.rce/` and `.researchos/` is not
    touched and is reported -- Research OS does not choose between them."""
    root, user, pid = old_layout
    (root / NEW_DIR).mkdir()
    (root / NEW_DIR / "notes.toml").write_text("# 另一份\n")
    old, new = _tree(root / OLD_DIR), _tree(root / NEW_DIR)
    capsys.readouterr()
    assert cli.main(["status", str(root)]) == 1
    err = capsys.readouterr().err
    assert f"both {root / OLD_DIR} and {root / NEW_DIR} exist" in err and "touched neither" in err
    assert _tree(root / OLD_DIR) == old and _tree(root / NEW_DIR) == new
    served = webapp_server.served_for(root)
    assert served.blocked["situation"] == "rename_blocked" and "两个都没有动" in served.blocked["message"]
    assert _tree(root / OLD_DIR) == old and _tree(root / NEW_DIR) == new


def test_12_10_1_a_home_with_both_folders_is_left_untouched_and_reported(old_layout, capsys):
    """12.8: the same for the machine's home: every command stops, saying
    so; neither folder is touched."""
    root, user, pid = old_layout
    (user / names.HOME_DIRNAME).mkdir()
    old = _tree(user / names.LEGACY_HOME_DIRNAME)
    capsys.readouterr()
    assert cli.main(["projects", "list"]) == 1
    assert "touched neither" in capsys.readouterr().err
    assert _tree(user / names.LEGACY_HOME_DIRNAME) == old and _tree(user / names.HOME_DIRNAME) == {}
    assert _tree(root / OLD_DIR)  # the project was never reached


def test_12_10_1_a_dataless_file_blocks_the_rename_with_the_cloud_sentence(old_layout, monkeypatch, capsys):
    """12.8 / 8.10: a file macOS has evicted to iCloud inside `.rce/` is
    never read (reading it would block): the rename is refused with the
    cloud sentence, its download is asked for in the background, and
    nothing moves."""
    root, user, pid = old_layout
    rename.ensure_home_renamed()
    evicted = root / OLD_DIR / "judgements.toml"
    inode = os.lstat(evicted).st_ino
    monkeypatch.setattr(rename, "_is_dataless", lambda st: st.st_ino == inode)
    asked: list[Path] = []
    monkeypatch.setattr(paths, "_request_download", asked.append)
    hashed: list[Path] = []
    real_sha = rename._sha256
    monkeypatch.setattr(rename, "_sha256", lambda p: hashed.append(p) or real_sha(p))
    before = _tree(root / OLD_DIR)
    capsys.readouterr()

    assert cli.main(["status", str(root)]) == 1
    err = capsys.readouterr().err
    assert "evicted them to iCloud" in err and "judgements.toml" in err and "Nothing was renamed" in err
    assert asked == [evicted] and hashed == []  # asked for, never opened
    served = webapp_server.served_for(root)
    assert served.blocked["situation"] == "rename_blocked"
    assert served.blocked["message"].startswith("记录文件正在从云端下载…")
    assert _tree(root / OLD_DIR) == before and not (root / NEW_DIR).exists()


def test_12_10_1_an_evicted_folder_is_not_even_listed(old_layout, monkeypatch):
    """12.8 / 8.10: a folder inside `.rce/` that macOS has evicted is not
    descended (listing it would wait for the download): the rename is
    refused naming it, and nothing under it is listed or read."""
    root, user, pid = old_layout
    rename.ensure_home_renamed()
    evicted = root / OLD_DIR / "variables"
    inode = os.lstat(evicted).st_ino
    monkeypatch.setattr(rename, "_is_dataless", lambda st: st.st_ino == inode)
    monkeypatch.setattr(paths, "_request_download", lambda _p: None)
    listed: list[str] = []
    real_scandir = os.scandir
    monkeypatch.setattr(rename.os, "scandir", lambda d: listed.append(str(d)) or real_scandir(d))
    with pytest.raises(rename.RenameBlocked, match="variables"):
        rename.ensure_project_renamed(root)
    assert not any(d.startswith(str(evicted)) for d in listed)
    assert (root / OLD_DIR).is_dir() and not (root / NEW_DIR).exists()


def test_12_10_1_an_old_engine_still_running_blocks_the_rename(old_layout, monkeypatch, capsys):
    """12.8: an engine of the old version answering on the app's port would
    go on writing under the old names; nothing is renamed until it quits."""
    root, user, pid = old_layout
    monkeypatch.setattr(migration, "engine_port", lambda: 7399)
    monkeypatch.setattr(migration, "engine_running", lambda: True)
    monkeypatch.setattr(migration, "ask_engine", lambda port, **_k: {"engine": "rce", "version": 5, "pid": 4242})
    before = _snapshot(root, user)
    capsys.readouterr()
    assert cli.main(["status", str(root)]) == 1
    assert "an engine of the old version (rce, process 4242)" in capsys.readouterr().err
    assert _snapshot(root, user) == before and not (user / names.HOME_DIRNAME).exists()


def test_12_10_1_a_named_home_is_never_renamed(tmp_path, monkeypatch):
    """12.8: a home named by `RESEARCHOS_HOME` or the old `RCE_HOME` is where
    the person said it is; an old `~/.rce` beside it is not touched."""
    user = tmp_path / "user"
    (user / names.LEGACY_HOME_DIRNAME).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv(names.LEGACY_HOME_ENV_VAR, str(tmp_path / "named"))
    monkeypatch.delenv(names.HOME_ENV_VAR, raising=False)
    assert rename.ensure_home_renamed() is None
    assert paths.researchos_home() == tmp_path / "named"
    assert (user / names.LEGACY_HOME_DIRNAME).is_dir() and not (user / names.HOME_DIRNAME).exists()


def test_12_10_1_an_old_project_reads_through_its_old_name_before_it_is_renamed(old_layout):
    """12.8: until it is renamed, an old folder is read where it is -- the
    registry can list it and adding it can inspect it -- and nothing that
    only reads renames it."""
    root, user, pid = old_layout
    assert paths.project_dir(root) == root / OLD_DIR
    assert paths.researchos_home() == user / names.LEGACY_HOME_DIRNAME
    assert read_identity(root).identity.id == pid
    assert registry.is_initialized(root)
    assert (root / OLD_DIR).is_dir() and not (root / NEW_DIR).exists()


# -- the names ------------------------------------------------------------------------------


_SOURCES = [p for p in (REPO / "src" / "researchos").rglob("*") if p.suffix in (".py", ".js", ".html")]
_TESTS = sorted((REPO / "tests").glob("*.py"))


def test_12_10_1_no_module_path_of_the_old_package_remains():
    """12.8: the package is `researchos`; nothing imports or names `rce.*`."""
    old_module = re.compile(r"\bfrom rce\b|\bimport rce\b|[\"']rce\.[a-z]")
    found = [f"{p.relative_to(REPO)}:{n}" for p in _SOURCES + _TESTS if p.suffix == ".py"
             for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if old_module.search(line)]
    assert found == []
    assert not (REPO / "src" / "rce").exists()


def test_12_10_1_the_old_names_are_spelled_only_in_the_constants_module():
    """12.8: every quoted `.rce`, `~/.rce` and `RCE_HOME` in the code and the
    tests goes through `researchos.names` -- the only file that spells them.
    (The Swift shell cannot import it and is checked by its own test.)"""
    dot, var = "." + "rce", "RCE" + "_HOME"
    literal = re.compile(r"[\"'](?:~/)?" + re.escape(dot) + r"(?![\w-])|[\"']" + re.escape(var) + r"[\"']")
    allowed = {REPO / "src" / "researchos" / "names.py"}
    found = [f"{p.relative_to(REPO)}:{n}: {line.strip()}" for p in _SOURCES + _TESTS if p not in allowed
             for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if literal.search(line)]
    assert found == []


def test_12_10_1_the_package_command_and_alias_are_declared():
    """12.8: the distribution is `researchos`; `researchos` and `rce` are
    both console scripts, the alias pointing at `legacy_main`."""
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "researchos"
    assert pyproject["project"]["scripts"] == {"researchos": "researchos.cli:main", "rce": "researchos.cli:legacy_main"}
    assert "webapp/shell/*.swift" in pyproject["tool"]["setuptools"]["package-data"]["researchos"]
    assert cli.build_parser().prog == "researchos"


def test_12_10_1_the_engine_answers_with_the_new_name_and_old_engines_still_count():
    """12.8: `GET /api/engine` says `researchos`; the retire guard still
    recognises an engine of the old version as one holding an index."""
    assert webapp_server.engine_payload(webapp_server.ServedProject(Path("/nowhere"), no_project=True))["engine"] == "researchos"
    assert migration.ENGINE_NAMES == {"researchos", "rce"}


def test_12_10_1_the_mcp_server_is_named_research_os(tmp_path):
    """12.8: the MCP server's name."""
    pytest.importorskip("mcp")
    from researchos import mcp_server

    assert cli.main(["init", str(tmp_path)]) == 0
    assert mcp_server.build_server(tmp_path).name == names.MCP_SERVER_NAME == "researchos"


def test_12_10_1_an_interrupted_write_of_the_old_version_is_still_recognised():
    """12.8: a durable write's temp file now ends `.researchos-tmp`; one an
    older version left (`.rce-tmp`) is still an interrupted write."""
    assert inventory._is_interrupted_write(".v1.toml.1.ab" + record_files._TMP_MARK)
    assert inventory._is_interrupted_write(".v1.toml.1.ab" + record_files.LEGACY_TMP_MARK)


def test_12_10_1_the_swift_shell_finds_the_home_the_engine_uses():
    """12.8: the shell writes serve.log where the engine's home is -- the
    new variable, then the old, then `~/.researchos`, or the old home while
    it is the only one -- so it never creates the new home beside the old."""
    shell = macapp.SHELL_SOURCE.read_text(encoding="utf-8")
    assert f'for name in ["{names.HOME_ENV_VAR}", "{names.LEGACY_HOME_ENV_VAR}"]' in shell
    for dirname in (names.HOME_DIRNAME, names.LEGACY_HOME_DIRNAME):
        assert f'appendingPathComponent("{dirname}", isDirectory: true)' in shell
    assert 'window.title = "Research OS"' in shell and 'submenu("Research OS"' in shell


# -- the app bundle -------------------------------------------------------------------------


def _old_bundle(target: Path, identifier: str) -> Path:
    import plistlib

    bundle = target / names.LEGACY_APP_BUNDLE_NAME
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    with (bundle / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleIdentifier": identifier, "CFBundleName": "RCE"}, fh)
    (bundle / "Contents" / "MacOS" / "RCE").write_text("#!/bin/sh\n")
    return bundle


def _fake_engine(tmp_path: Path) -> Path:
    path = tmp_path / "bin" / "researchos"
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\n")
    return path


def test_12_10_1_the_app_is_research_os_and_replaces_only_the_rce_app_it_built(tmp_path, monkeypatch, capsys):
    """12.8: `researchos app --dir` builds `Research OS.app` with the new
    bundle identifier, and removes an `RCE.app` beside it only when this
    tool built it (its identifier says so); macOS will ask for Documents
    access once more, and the command says so."""
    monkeypatch.setattr(macapp, "find_swiftc", lambda: None)
    monkeypatch.setattr(macapp, "resolve_executable", lambda: _fake_engine(tmp_path))
    apps = tmp_path / "apps"
    ours = _old_bundle(apps, names.LEGACY_BUNDLE_IDENTIFIER)
    assert cli.main(["app", "--dir", str(apps), "--port", "7398"]) == 0
    out = capsys.readouterr().out
    assert not ours.exists() and f"Removed the old {ours}" in out
    bundle = apps / "Research OS.app"
    assert macapp.bundle_identifier(bundle) == "dev.researchos.app"
    script = (bundle / "Contents" / "MacOS" / "ResearchOS").read_text()
    assert 'DATA="$RESEARCHOS_HOME"' in script and 'DATA="$HOME/.researchos"' in script and "serve.log" in script


def test_12_10_1_an_rce_app_this_tool_did_not_build_is_left_alone(tmp_path, monkeypatch):
    """12.8: an `RCE.app` with another identifier (or a symlink of that
    name) is someone else's: kept, and named in one notice."""
    monkeypatch.setattr(macapp, "find_swiftc", lambda: None)
    apps = tmp_path / "apps"
    theirs = _old_bundle(apps, "com.example.rce")
    result = macapp.build_app(apps, executable=_fake_engine(tmp_path), port=7398)
    assert theirs.is_dir() and result.removed_legacy is None
    assert result.notices[-1] == macapp.NOTICE_LEGACY_KEPT.format(path=theirs)
    assert (apps / "Research OS.app").is_dir()

"""Research OS V8 phase 2 (DESIGN.md 12.1, 12.6, 12.7): people, notes, node
history and the trace -- the single-person acceptance of 12.10 #2 ("one
person: no approval step anywhere; history and notes on a script, a data
file and an attempt; a figure traced to data, scripts and attempt;
「关联文件」 and 「补上来源」 repair a broken trace"), on fixtures with and
without git. Every test names its scenario."""

from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from researchos import cli, db, history, inventory, people, trace
from researchos import project as project_identity
from researchos.ingest import mappings as mappings_ingest
from researchos.ingest import pipeline
from researchos.records import files as record_files
from researchos.records import judgements
from researchos.records import ledger as ledger_mod
from researchos.records import notes
from researchos.records.identity import read_identity
from researchos.records.situation import index_db_path, write_guard
from researchos.webapp import server
from researchos.webapp import watcher as watcher_mod

ATTEMPTS_TOML = (
    'file = "map.md"\nheading = "Attempt timeline"\nsteps_dir = "steps"\n'
    '[columns]\nid = "#"\ndate = "Date"\ndescription = "Path"\nvariables = "Variables"\n'
    'result = "Result"\nverdict = "Verdict"\n'
)
BUILD = 'import pandas as pd\ndf = pd.read_csv("data/raw.csv")\ndf.to_csv("data/panel.csv")\n'
PLOT = (
    'import pandas as pd\nimport matplotlib.pyplot as plt\n'
    'df = pd.read_csv("data/panel.csv")\nplt.savefig("figs/fig2.png")\n'
)
PAPER = "# Results\n\n## Main figure\n\n![Figure 2](figs/fig2.png)\n\n![Figure 1](figs/fig1.png)\n"


def _map(verdict: str = "🕒", *, row: bool = True) -> str:
    rows = f"| 16 | 07-26 | TopicShift volatility (16-17) | TopicShift->RV | t=2.91 | {verdict} |\n" if row else ""
    return (
        "# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n"
        "|---|---|---|---|---|---|\n" + rows
    )


def _csv(rows: int, columns: tuple[str, ...] = ("id", "x")) -> str:
    lines = [",".join(columns)]
    lines += [",".join(str(i * 10 + j) for j in range(len(columns))) for i in range(rows)]
    return "\n".join(lines) + "\n"


def _project(root: Path) -> Path:
    for sub in ("steps", "data", "figs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "data" / "raw.csv").write_text(_csv(3))
    (root / "data" / "panel.csv").write_text(_csv(96))
    (root / "figs" / "fig2.png").write_bytes(b"\x89PNG fig2")
    (root / "figs" / "fig1.png").write_bytes(b"\x89PNG fig1")
    (root / "steps" / "16-build.py").write_text(BUILD)
    (root / "steps" / "17-plot.py").write_text(PLOT)
    (root / "paper.md").write_text(PAPER)
    (root / "map.md").write_text(_map())
    project_identity.init_project(root)
    (root / ".researchos" / "attempts.toml").write_text(ATTEMPTS_TOML)
    _scan(root)
    return root


def _conn(root: Path):
    return db.connect(index_db_path(read_identity(root).identity.id))


def _scan(root: Path) -> None:
    """A full scan, as `researchos ingest` runs it: sources, then the record."""
    with write_guard(root):
        conn = _conn(root)
        try:
            pipeline.ingest_sources(conn, root)
            pipeline.ingest_records(conn, root)
        finally:
            conn.close()


def _git(root: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Li", "GIT_AUTHOR_EMAIL": "Li@Example.com",
           "GIT_COMMITTER_NAME": "Li", "GIT_COMMITTER_EMAIL": "li@example.com"}
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    out = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env)
    return out.stdout.strip()


# -- people (12.1) ------------------------------------------------------------------------


def test_12_10_2_me_toml_is_written_and_its_id_is_the_email_lower_cased(capsys):
    """12.10 #2 (one person): `researchos me set` writes ~/.researchos/me.toml;
    the id is the e-mail lower-cased; a bad e-mail is refused, nothing written."""
    assert cli.main(["me", "set", "--name", "李", "--email", " Li@Example.COM "]) == 0
    got = people.read_me()
    assert got.state == "present" and got.me.id == "li@example.com" and got.me.name == "李"
    assert cli.main(["me", "show"]) == 0
    assert "li@example.com" in capsys.readouterr().out
    before = people.me_path().read_bytes()
    assert cli.main(["me", "set", "--name", "X", "--email", "not an email"]) == 1
    assert people.me_path().read_bytes() == before


def test_12_10_2_by_is_recorded_when_me_toml_exists_and_absent_otherwise(tmp_path):
    """12.10 #2: every entry Research OS appends carries `by` once me.toml
    exists -- judgments and notes alike -- and none before; a reader treats
    a missing `by` as the project's first researcher."""
    root = _project(tmp_path / "proj")
    key = ("script:steps/16-build.py", "dataset:data/raw.csv", "reads", "dataflow")
    first = judgements.judge(root, key, "confirmed", via="cli")
    note = notes.add_note(root, "steps/16-build.py", "before me.toml", via="cli")
    assert "by" not in first.entry.data and "by" not in note.data
    people.write_me("李", "li@example.com")
    second = judgements.judge(root, key, "rejected", via="cli")
    note2 = notes.add_note(root, "steps/16-build.py", "after me.toml", via="cli")
    assert second.entry.data["by"] == "li@example.com" and note2.data["by"] == "li@example.com"
    text = ledger_mod.judgements_path(root).read_text()
    assert text.count('by = "li@example.com"') == 1
    listed = notes.notes_for(root, "steps/16-build.py")["notes"]
    assert listed[0]["first_researcher"] is True and listed[0]["label"] == people.FIRST_RESEARCHER_LABEL
    assert listed[1]["by"] == "li@example.com" and listed[1]["name"] == "李"


def test_12_10_2_card_log_entries_carry_by(tmp_path):
    """12.10 #2: a variable card's log is a ledger too -- its entries carry
    `by` once me.toml exists."""
    from researchos.records import cards

    root = _project(tmp_path / "proj")
    people.write_me("李", "li@example.com")
    cards.new_card(root, "panelx")
    log = notes.notes_path(root).parent / "variables" / "panelx"
    assert log.is_dir()
    (log / "v1.toml").write_text(
        'name = "Panel X"\nmeaning = \'\'\'x\'\'\'\nunit = "1"\ngranularity = "月"\n\n'
        '[[input]]\ndataset = "data/raw.csv"\n\n[construction]\nformula = \'\'\'x\'\'\'\n\n'
        '[implementation]\nscript = "steps/16-build.py"\noutput = "data/panel.csv"\nfield = "x"\n\n'
        "[decision]\nwhy = '''because'''\n",
        encoding="utf-8",
    )
    confirmed = cards.confirm(root, "panelx", attested="unknown")
    assert confirmed.entry.data["by"] == "li@example.com"


def test_12_10_2_an_unreadable_me_toml_refuses_every_ledger_write(tmp_path):
    """12.10 #2: a me.toml that exists but cannot be read is not "nobody" --
    the write is refused and nothing is appended."""
    root = _project(tmp_path / "proj")
    people.me_path().write_text("id = \n")
    with pytest.raises(notes.NoteRefused):
        notes.add_note(root, "steps/16-build.py", "x", via="cli")
    assert not notes.notes_path(root).exists()


@pytest.mark.parametrize("text, mode", [
    (None, "single"),
    ('[[member]]\nid = "li@example.com"\nname = "李"\nrole = "pi"\n', "single"),
    ('[[member]]\nid = "li@example.com"\nname = "李"\nrole = "pi"\n\n'
     '[[member]]\nid = "wang@example.com"\nname = "王"\nrole = "member"\nkey = "ssh-ed25519 AAAA"\n', "team"),
    ('[[member]]\nid = "li@example.com"\nname = "李"\nrole = "pi"\n\n'
     '[[member]]\nid = "wang@example.com"\nname = "王"\nrole = "pi"\n', "unknown"),
    ('[[member]]\nid = "Li@Example.com"\nname = "李"\nrole = "pi"\n', "unknown"),
    ('[[member]]\nid = "li@example.com"\nname = "李"\nrole = "boss"\n', "unknown"),
    ('[[member]]\nid = "li@example.com"\nname = "李"\nrole = "pi"\ncolor = "red"\n', "unknown"),
])
def test_12_10_2_team_toml_and_the_single_person_case(tmp_path, text, mode):
    """12.10 #2 (one person, no approval step anywhere): without team.toml,
    or with only its PI, a project is single-person; an invalid team file
    is never assumed single."""
    root = tmp_path / "p"
    (root / ".researchos").mkdir(parents=True)
    if text is not None:
        (root / ".researchos" / "team.toml").write_text(text)
    assert people.team_mode(people.read_team(root)) == mode


# -- notes (12.6) -------------------------------------------------------------------------


def test_12_10_2_notes_add_list_withdraw_on_script_data_and_attempt(tmp_path, capsys):
    """12.10 #2: notes on a script, a data file and an attempt -- added,
    listed, withdrawn (an entry appended; the note itself is kept), the file
    growing only by appending."""
    root = _project(tmp_path / "proj")
    assert cli.main(["note", "add", "steps/16-build.py", "换成对数收益", str(root)]) == 0
    assert cli.main(["note", "add", "data/panel.csv", "第97行是新月份", str(root)]) == 0
    assert cli.main(["note", "add", "attempt:map.md#16", "等第二组样本", str(root)]) == 0
    capsys.readouterr()
    assert cli.main(["note", "list", "data/panel.csv", str(root)]) == 0
    assert "第97行是新月份" in capsys.readouterr().out
    before = notes.notes_path(root).read_bytes()
    listed = notes.notes_for(root, "script:steps/16-build.py")["notes"]
    assert [n["text"] for n in listed] == ["换成对数收益"]
    assert cli.main(["note", "withdraw", listed[0]["id"], str(root)]) == 0
    after = notes.notes_path(root).read_bytes()
    assert after.startswith(before) and b"withdrawn" in after[len(before):]
    listed = notes.notes_for(root, "steps/16-build.py")["notes"]
    assert listed[0]["withdrawn"] is True and listed[0]["text"] == "换成对数收益"
    with pytest.raises(notes.NoteRefused) as err:
        notes.withdraw_note(root, listed[0]["id"], via="cli")
    assert err.value.code == "already_withdrawn"
    assert notes.notes_path(root).read_bytes() == after
    every = notes.notes_for(root)["notes"]
    assert {n["node"] for n in every} == {"script:steps/16-build.py", "dataset:data/panel.csv", "attempt:map.md#16"}


def test_12_10_2_a_note_keeps_its_line_breaks_in_one_file_line(tmp_path):
    """12.10 #2: a note's text may hold line breaks; the file's own lines are
    never split (written as the TOML escape)."""
    root = _project(tmp_path / "proj")
    notes.add_note(root, "figs/fig2.png", "第一行\r\n第二行", via="app")
    text = notes.notes_path(root).read_text()
    assert 'text = "第一行\\n第二行"' in text
    assert notes.notes_for(root, "figs/fig2.png")["notes"][0]["text"] == "第一行\n第二行"
    with pytest.raises(notes.NoteRefused):
        notes.add_note(root, "figs/fig2.png", "bad separator", via="app")


def test_12_10_2_notes_missing_though_expected_is_never_recreated(tmp_path):
    """12.10 #2 (trust rules): once the notes file existed (a snapshot says
    so), a missing file refuses every write; Research OS never founds a new one."""
    root = _project(tmp_path / "proj")
    notes.add_note(root, "steps/16-build.py", "one", via="cli")
    assert notes.notes_expected(root)
    notes.notes_path(root).unlink()
    with pytest.raises(notes.NoteRefused) as err:
        notes.add_note(root, "steps/16-build.py", "two", via="cli")
    assert err.value.code == "untrusted" and err.value.message_zh.startswith("备注记录文件")
    assert not notes.notes_path(root).exists()


def test_12_10_2_notes_unreadable_refuses_and_readers_show_what_the_index_applied(tmp_path):
    """12.10 #2 (trust rules): an unparseable notes file refuses writes and
    the reader shows the notes the index last applied, marked stale."""
    root = _project(tmp_path / "proj")
    notes.add_note(root, "steps/16-build.py", "kept", via="cli")
    path = notes.notes_path(root)
    path.write_text(path.read_text() + "\n[[note\n")
    with pytest.raises(notes.NoteRefused):
        notes.add_note(root, "steps/16-build.py", "two", via="cli")
    conn = _conn(root)
    try:
        shown = notes.notes_for(root, "steps/16-build.py", conn)
    finally:
        conn.close()
    assert shown["stale"] is True and shown["reason"] == "invalid"
    assert [n["text"] for n in shown["notes"]] == ["kept"]


def test_12_10_2_notes_shrunk_asks_and_both_answers_work(tmp_path, capsys):
    """12.10 #2 (trust rules): a notes file with fewer entries than the index
    applied is asked about -- 「把缺少的补回文件」 appends them back (their
    content kept), 「以文件为准」 forgets them."""
    root = _project(tmp_path / "proj")
    notes.add_note(root, "steps/16-build.py", "one", via="cli")
    short = notes.notes_path(root).read_bytes()
    notes.add_note(root, "steps/16-build.py", "two", via="cli")
    notes.notes_path(root).write_bytes(short)
    with pytest.raises(notes.NoteRefused) as err:
        notes.add_note(root, "steps/16-build.py", "three", via="cli")
    assert err.value.decision.reason == "shrunk"
    capsys.readouterr()
    assert cli.main(["records", str(root)]) == 0
    out = capsys.readouterr().out
    assert "The notes file has 1 fewer" in out
    missing = [m["id"] for m in err.value.decision.missing]
    assert cli.main(["records", "--file", "notes", "--answer", "restore", "--missing", ",".join(missing), str(root)]) == 0
    texts = [n["text"] for n in notes.notes_for(root, "steps/16-build.py")["notes"]]
    assert texts == ["one", "two"]
    assert "recovered_from" in notes.notes_path(root).read_text()
    # And 「以文件为准」 on a second shrink.
    notes.notes_path(root).write_bytes(short)
    assert notes.answer_shrunk(root, "file")[0] == 1  # the restored copy of "two"
    assert [n["text"] for n in notes.notes_for(root, "steps/16-build.py")["notes"]] == ["one"]
    notes.add_note(root, "steps/16-build.py", "four", via="cli")


def test_12_10_2_a_sync_conflict_copy_beside_notes_refuses_writes(tmp_path):
    """12.10 #2 (trust rules): `notes 2.toml` beside the file -- reported,
    never read, every write refused."""
    root = _project(tmp_path / "proj")
    notes.add_note(root, "steps/16-build.py", "one", via="cli")
    (notes.notes_path(root).parent / "notes 2.toml").write_text("whatever")
    with pytest.raises(notes.NoteRefused) as err:
        notes.add_note(root, "steps/16-build.py", "two", via="cli")
    assert err.value.decision.reason == "conflict_copy"


def test_12_10_2_notes_join_the_watch_set_and_researchos_records(tmp_path, capsys):
    """12.10 #2: the notes file is watched (a hand edit re-applies the
    record) and listed by `researchos records`."""
    root = _project(tmp_path / "proj")
    notes.add_note(root, "steps/16-build.py", "one", via="cli")
    assert str(notes.notes_path(root)) in watcher_mod.take_snapshot(root).files
    assert notes.notes_path(root) in inventory.record_file_paths(root)
    row = next(r for r in inventory.inventory(None, root) if r.code == "notes")
    assert row.facts["notes"] == 1
    assert cli.main(["records", str(root)]) == 0
    assert "Notes on nodes" in capsys.readouterr().out


# -- history (12.6), without git ------------------------------------------------------------


def test_12_10_2_history_and_diff_of_a_script_without_git(tmp_path, capsys):
    """12.10 #2 (non-git): every scan keeps a changed script's content; the
    diff is a unified diff."""
    root = _project(tmp_path / "proj")
    (root / "steps" / "16-build.py").write_text(BUILD + 'df["log_ret"] = 1\n')
    _scan(root)
    _scan(root)  # unchanged: no new version
    listed = history.versions(root, "steps/16-build.py")
    assert listed["origin"] == "store" and len(listed["versions"]) == 2
    assert listed["versions"][0]["who_label"] == people.FIRST_RESEARCHER_LABEL  # 12.12: a missing by
    change = history.diff(root, "script:steps/16-build.py")
    assert '+df["log_ret"] = 1' in change["diff"] and change["truncated"] is False
    capsys.readouterr()
    assert cli.main(["diff", "steps/16-build.py", str(root)]) == 0
    assert '+df["log_ret"] = 1' in capsys.readouterr().out
    a, b = listed["versions"][1]["version"], listed["versions"][0]["version"]
    assert cli.main(["diff", "steps/16-build.py", a, b, "--path", str(root)]) == 0


def test_12_10_2_a_csv_whose_rows_then_columns_change(tmp_path):
    """12.10 #2 (non-git): a data file's versions say 「行数 96 → 97，列不变」
    and then 「新增列 log_ret」."""
    root = _project(tmp_path / "proj")
    (root / "data" / "panel.csv").write_text(_csv(97))
    _scan(root)
    assert history.diff(root, "data/panel.csv")["summary"] == "行数 96 → 97，列不变"
    (root / "data" / "panel.csv").write_text(_csv(97, ("id", "x", "log_ret")))
    _scan(root)
    assert history.diff(root, "dataset:data/panel.csv")["summary"] == "新增列 log_ret"
    first = history.versions(root, "data/panel.csv")["versions"][-1]
    assert first["summary"]["rows"] == 96 and first["summary"]["columns"] == ["id", "x"]


def test_12_10_2_an_attempt_whose_verdict_changes_across_scans(tmp_path, capsys):
    """12.10 #2: an attempt's history is its verdict and result as scanned:
    「🕒 → ✅」."""
    root = _project(tmp_path / "proj")
    (root / "map.md").write_text(_map("✅"))
    _scan(root)
    listed = history.versions(root, "attempt:map.md#16")
    assert [v["verdict"] for v in listed["versions"]] == ["✅", "🕒"]
    assert history.diff(root, "attempt:map.md#16")["summary"] == "🕒 → ✅"
    capsys.readouterr()
    assert cli.main(["history", "attempt:map.md#16", str(root)]) == 0
    assert "verdict=✅" in capsys.readouterr().out


def test_12_10_2_large_and_binary_files_are_kept_by_fingerprint(tmp_path, monkeypatch):
    """12.10 #2: a file above 5 MB, or a binary one, is kept by fingerprint
    and size (and for a table, rows and columns) -- never by content."""
    monkeypatch.setattr(history, "TEXT_LIMIT", 200)
    root = _project(tmp_path / "proj")
    v = history.versions(root, "data/panel.csv")["versions"][0]
    assert v["stored"] is False and v["kind"] == "data" and v["summary"]["rows"] == 96
    png = history.versions(root, "figs/fig2.png")["versions"][0]
    assert png["kind"] == "binary" and png["stored"] is False and len(png["sha256"]) == 64


def test_12_10_2_a_cloud_only_file_is_never_read(tmp_path, monkeypatch):
    """12.10 #2: a dataless (cloud-only) file gets no version and is never
    opened."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n")
    project_identity.init_project(root)
    monkeypatch.setattr(history, "_is_dataless", lambda path: path.name == "a.py")
    real_open = open

    def guarded(path, *args, **kwargs):
        assert Path(str(path)).name != "a.py", "a cloud-only file was opened"
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded)
    counts = history.record_scan(None, root)
    assert counts["dataless"] == 1
    assert history.versions(root, "a.py")["versions"] == []


def test_12_10_2_an_xlsx_row_and_column_count(tmp_path):
    """12.10 #2: an Excel file's rows and columns come from its sheet's
    dimension, read with zipfile -- no XML parser."""
    path = tmp_path / "t.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", '<worksheet><dimension ref="A1:F97"/></worksheet>')
    assert history.xlsx_summary(path) == {"format": "xlsx", "sheet": "sheet1.xml", "sheet_rows": 97, "sheet_columns": 6}
    (tmp_path / "bad.xlsx").write_bytes(b"not a zip")
    assert history.xlsx_summary(tmp_path / "bad.xlsx") is None
    assert history.table_sentence({"format": "xlsx", "sheet_rows": 97, "sheet_columns": 6},
                                  {"format": "xlsx", "sheet_rows": 98, "sheet_columns": 6}) == "行数 97 → 98，列数不变"


def test_12_10_2_the_history_survives_a_rebuild_of_the_index(tmp_path):
    """12.10 #2: the version store is not the index -- deleting graph.db
    leaves every version."""
    root = _project(tmp_path / "proj")
    (root / "steps" / "16-build.py").write_text(BUILD + "# v2\n")
    _scan(root)
    index_db_path(read_identity(root).identity.id).unlink()
    assert len(history.versions(root, "steps/16-build.py")["versions"]) == 2


# -- history (12.6), with git ---------------------------------------------------------------


def _git_project(root: Path) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    (root / "fit.py").write_text("x = 1\n")
    (root / "panel.csv").write_text(_csv(96))
    _git(root, "add", "fit.py", "panel.csv")
    _git(root, "commit", "-q", "-m", "first fit", date="2026-07-01T10:00:00+02:00")
    (root / "fit.py").write_text("x = 1\ny = 2\n")
    (root / "panel.csv").write_text(_csv(97))
    _git(root, "commit", "-q", "-am", "add y; one more month", date="2026-07-02T10:00:00+02:00")
    project_identity.init_project(root)
    return root


def test_12_10_2_git_history_is_the_commits(tmp_path):
    """12.10 #2 (git): a tracked file's versions are its commits, with
    author, date and subject; diffs come from git without filters."""
    root = _git_project(tmp_path / "repo")
    listed = history.versions(root, "fit.py")
    assert listed["origin"] == "git"
    assert [v["note"] for v in listed["versions"]] == ["add y; one more month", "first fit"]
    assert listed["versions"][0]["who"] == "li@example.com" and listed["versions"][0]["size"] == len("x = 1\ny = 2\n")
    change = history.diff(root, "fit.py")
    assert "+y = 2" in change["diff"]
    assert history.diff(root, "panel.csv")["summary"] == "行数 96 → 97，列不变"


def test_12_10_2_git_working_copy_and_rename(tmp_path):
    """12.10 #2 (git): an uncommitted change is one more version
    (`worktree`); a renamed file keeps its history (--follow)."""
    root = _git_project(tmp_path / "repo")
    (root / "fit.py").write_text("x = 1\ny = 3\n")
    listed = history.versions(root, "fit.py")["versions"]
    assert listed[0]["version"] == history.WORKTREE
    assert "+y = 3" in history.diff(root, "fit.py")["diff"]
    _git(root, "mv", "fit.py", "model.py")
    _git(root, "commit", "-q", "-am", "rename", date="2026-07-03T10:00:00+02:00")
    renamed = history.versions(root, "model.py")["versions"]
    assert [v["note"] for v in renamed if v["version"] != history.WORKTREE][-1] == "first fit"


def test_12_10_2_git_runs_no_program_the_repository_names(tmp_path):
    """12.10 #2 (git, 10.9/11.6): a repository whose own config names a
    textconv / filter program does not get it run by history or diff."""
    root = _git_project(tmp_path / "repo")
    marker = tmp_path / "ran"
    _git(root, "config", "diff.evil.textconv", f"touch {marker} #")
    _git(root, "config", "filter.evil.smudge", f"touch {marker} #")
    _git(root, "config", "core.fsmonitor", f"touch {marker} #")
    (root / ".gitattributes").write_text("*.py diff=evil filter=evil\n")
    history.versions(root, "fit.py")
    history.diff(root, "fit.py")
    assert not marker.exists()


# -- trace (12.7) ---------------------------------------------------------------------------


def test_12_10_2_a_figure_traced_to_data_scripts_attempt_and_variable(tmp_path):
    """12.10 #2: a figure traced to the data it was built from, the scripts
    that read and wrote it, the draft that includes it, the attempt it
    belongs to (step numbers) and the variable version whose output it
    uses."""
    from researchos.records import cards

    root = _project(tmp_path / "proj")
    cards.new_card(root, "panelx")
    (notes.notes_path(root).parent / "variables" / "panelx" / "v1.toml").write_text(
        'name = "Panel X"\nmeaning = \'\'\'x\'\'\'\nunit = "1"\ngranularity = "月"\n\n'
        '[[input]]\ndataset = "data/raw.csv"\n\n[construction]\nformula = \'\'\'x\'\'\'\n\n'
        '[implementation]\nscript = "steps/16-build.py"\noutput = "data/panel.csv"\nfield = "x"\n\n'
        "[decision]\nwhy = '''because'''\n",
        encoding="utf-8",
    )
    cards.confirm(root, "panelx", attested="yes")
    conn = _conn(root)
    try:
        result = trace.chain(conn, root, "figs/fig2.png")
    finally:
        conn.close()
    assert [d["path"] for d in result["data"]] == ["data/panel.csv", "data/raw.csv"]
    assert result["data"][1]["raw"] is True
    assert {s["path"] for s in result["scripts"]} == {"steps/16-build.py", "steps/17-plot.py"}
    assert result["drafts"][0]["section"] == "section:paper.md#main-figure"
    assert [a["id"] for a in result["attempts"]] == ["attempt:map.md#16"]
    assert result["variables"][0]["variable"] == "panelx" and result["variables"][0]["matched_by"] == "output"
    assert result["variables"][0]["reference"]["label"].startswith("panelx@v1·")
    assert result["breaks"] == []


def test_12_10_2_a_broken_trace_repaired_with_generates_and_belongs_to(tmp_path, capsys):
    """12.10 #2: a figure with no generating script and no attempt shows
    both breaks with their repairs; 「补上来源」 (a `generates` mapping) and
    「关联文件」 (a `belongs_to` mapping) repair them."""
    root = _project(tmp_path / "proj")
    (root / "scripts").mkdir()
    (root / "scripts" / "plot1.py").write_text('import pandas as pd\ndf = pd.read_csv("data/panel.csv")\n')
    _scan(root)
    conn = _conn(root)
    try:
        broken = trace.chain(conn, root, "figure:figs/fig1.png")
    finally:
        conn.close()
    kinds = {b["kind"]: b for b in broken["breaks"]}
    assert set(kinds) == {"no_generating_script", "no_attempt"}
    assert kinds["no_generating_script"]["repair"]["label"] == "指定生成它的脚本"
    assert kinds["no_attempt"]["repair"]["label"] == "归入尝试"
    assert cli.main(["link", "figs/fig1.png", "attempt:map.md#16", "--type", "belongs_to", str(root)]) == 0
    conn = _conn(root)
    try:
        half = trace.chain(conn, root, "figs/fig1.png")
    finally:
        conn.close()
    assert [b["kind"] for b in half["breaks"]] == ["no_generating_script"]  # 「关联文件」 repaired one
    assert cli.main(["link", "scripts/plot1.py", "figs/fig1.png", "--type", "generates", str(root)]) == 0
    conn = _conn(root)
    try:
        repaired = trace.chain(conn, root, "figs/fig1.png")
    finally:
        conn.close()
    assert repaired["breaks"] == []
    assert repaired["node"]["made_by"] == ["script:scripts/plot1.py"]
    assert {s["path"] for s in repaired["scripts"]} == {"scripts/plot1.py", "steps/16-build.py"}
    assert {"via": "belongs_to", "through": "figure:figs/fig1.png"} in repaired["attempts"][0]["via"]
    assert any(link["human"] for link in repaired["links"])
    capsys.readouterr()
    assert cli.main(["trace", "figs/fig1.png", "--chain", str(root)]) == 0
    assert "attempt:map.md#16" in capsys.readouterr().out


def test_12_10_2_trace_names_unreadable_scripts_and_missing_data(tmp_path):
    """12.10 #2: every break is explicit -- a script the scan could not
    parse, a data file not on disk."""
    root = _project(tmp_path / "proj")
    (root / "steps" / "16-build.py").write_text("def broken(:\n")
    (root / "data" / "raw.csv").unlink()
    _scan(root)
    conn = _conn(root)
    try:
        result = trace.chain(conn, root, "figs/fig2.png")
    finally:
        conn.close()
    kinds = {(b["kind"], b["path"]) for b in result["breaks"]}
    assert ("script_unreadable", "steps/16-build.py") in kinds
    # The read it made before it became unparseable is still the index's
    # (9.6: an unparseable source keeps its links), so the deleted input shows.
    assert ("data_missing", "data/raw.csv") in kinds
    assert all(b["kind"] != "no_attempt" for b in result["breaks"])


def test_12_10_2_belongs_to_grammar_and_a_row_that_leaves_and_returns(tmp_path):
    """12.10 #2: `belongs_to` runs from a script, data file or figure to an
    attempt; an attempt not in the index waits (the entry is kept); deleting
    the row never crashes the attempts ingest, and the link comes back with
    the row."""
    root = _project(tmp_path / "proj")
    with pytest.raises(mappings_ingest.MappingsWriteError) as err:
        mappings_ingest.add_mapping(root, "paper.md", "attempt:map.md#16", "belongs_to")
    assert err.value.code == "unknown_extension"
    with pytest.raises(mappings_ingest.MappingsWriteError) as err:
        mappings_ingest.add_mapping(root, "figs/fig1.png", "map.md", "belongs_to")
    assert err.value.code == "bad_attempt"
    mappings_ingest.add_mapping(root, "figs/fig1.png", "map.md#16", "belongs_to")
    assert 'to   = "attempt:map.md#16"' in mappings_ingest.mappings_path(root).read_text()
    _scan(root)

    def linked() -> bool:
        conn = _conn(root)
        try:
            return bool(db.query_edges(conn, src="figure:figs/fig1.png", type="belongs_to"))
        finally:
            conn.close()

    assert linked()
    (root / "map.md").write_text(_map(row=False))
    assert cli.main(["attempts", str(root)]) == 0
    assert not linked()
    assert "attempt:map.md#16" in mappings_ingest.mappings_path(root).read_text()
    (root / "map.md").write_text(_map("✅"))
    assert cli.main(["attempts", str(root)]) == 0
    assert linked()


# -- HTTP -------------------------------------------------------------------------------------


@pytest.fixture
def live(tmp_path):
    root = _project(tmp_path / "proj")
    httpd = server.build_server(root, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", root
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _call(url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_12_10_2_http_notes_history_trace_and_me(live):
    """12.10 #2 through the app's endpoints: GET/POST /api/notes, GET
    /api/history and /api/history/diff, GET /api/trace, GET/POST /api/me."""
    base, root = live
    status, body = _call(f"{base}/api/me")
    assert status == 200 and body["state"] == "absent"
    status, body = _call(f"{base}/api/me", "POST", {"name": "李", "email": "Li@example.com"})
    assert status == 200 and body["me"]["id"] == "li@example.com"
    status, body = _call(f"{base}/api/notes", "POST", {"action": "add", "node": "figs/fig2.png", "text": "单位"})
    assert status == 200 and body["entry"]["by"] == "li@example.com"
    status, body = _call(f"{base}/api/notes?node=figure%3Afigs%2Ffig2.png")
    assert status == 200 and body["notes"][0]["text"] == "单位" and body["notes"][0]["name"] == "李"
    status, body = _call(f"{base}/api/notes", "POST", {"action": "withdraw", "id": "n-nope"})
    assert status == 404 and body["state"] == "no_such_note" and body["message"] == "找不到这条备注"
    (root / "data" / "panel.csv").write_text(_csv(97))
    _scan(root)
    status, body = _call(f"{base}/api/history?node=data%2Fpanel.csv")
    assert status == 200 and len(body["versions"]) == 2
    status, body = _call(f"{base}/api/history/diff?node=data%2Fpanel.csv")
    assert status == 200 and body["summary"] == "行数从 96 增至 97，列不变"
    status, body = _call(f"{base}/api/trace?node=figs%2Ffig2.png")
    assert status == 200 and body["attempts"][0]["id"] == "attempt:map.md#16"
    status, body = _call(f"{base}/api/trace?node=figs%2Fnope.png")
    assert status == 404


@pytest.mark.parametrize("method, path, body", [
    ("POST", "/api/notes", {"action": "add", "node": "figs/fig2.png", "text": "x"}),
    ("POST", "/api/me", {"name": "X", "email": "x@example.com"}),
    ("GET", "/api/notes?node=figs%2Ffig2.png", None),
    ("GET", "/api/history?node=figs%2Ffig2.png", None),
    ("GET", "/api/history/diff?node=figs%2Ffig2.png", None),
    ("GET", "/api/trace?node=figs%2Ffig2.png", None),
])
def test_12_10_2_every_new_endpoint_is_origin_checked(live, method, path, body):
    """12.10 #2 (10.6/11.5's rule for every new endpoint): a cross-origin
    request is refused with 403 and nothing is written."""
    base, root = live
    status, payload = _call(base + path, method, body, headers={"Origin": "http://evil.example"})
    assert status == 403 and "Origin" in payload["error"]
    assert not notes.notes_path(root).exists()
    assert not people.me_path().exists()


def test_12_10_2_rename_verification_reads_notes_and_team(tmp_path):
    """12.8/12.10 #2: the rename's read-back covers the new record files."""
    from researchos import rename

    root = _project(tmp_path / "proj")
    notes.add_note(root, "steps/16-build.py", "x", via="cli")
    (root / ".researchos" / "team.toml").write_text('[[member]]\nid = "li@example.com"\nname = "李"\nrole = "pi"\n')
    read = rename.project_records(root)
    assert read["notes"][0] == "present" and read["team"] == ("present", ("li@example.com",))

"""Research OS V16, task B4-2 (DESIGN.md 21.7, first bullet): 「你的记录与备份」 says in words what Research OS keeps for the
project, one line each, with when it was last backed up -- read from the backup store, not from a file's modified time --
and 「在 Finder 中显示」; the file names and the backup names are folded under 「详情」. The engine's rows come over HTTP
from a project with real backups and from one with none; the panel is the page's own script under node, in both languages.
Every test names its scenario."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

import pytest

from researchos import inventory
from researchos.records import files as record_files
from researchos.records import judgements
from researchos.records import ledger as ledger_mod
from researchos.records import papers
from researchos.webapp import server

from test_records_judgements import READ
from test_v10_desk_panel import _PANEL_DOM
from test_v5_phase7 import _call, live  # noqa: F401 -- fixture of the engine tests
from test_v9_language import APP_HTML, FILL_VARS_LINE, T_LINE, WEBAPP, _app_block, _i18n_source, _node, needs_node

#: A backup made long ago, with the clock the engine's own writer takes (`now=`): its stamp is the time in its name.
LONG_AGO = datetime(2025, 3, 4, 11, 5, 30, 123456, tzinfo=timezone.utc)


def _with_backups(root) -> dict[str, str]:
    """A project that has been used: a judgment backed up long ago, the marks of the paper (the engine's own writer backs the
    file up as it founds it), a task list and a hand-drawn link backed up now. The names of the backups, by record."""
    judgements.judge(root, READ, "confirmed", via="cli")
    record_files.snapshot_now(root, ledger_mod.judgements_path(root), now=lambda: LONG_AGO)
    (root / "paper").mkdir()
    (root / "paper" / "main.md").write_text("# Paper\nSharpe 1.5\n", encoding="utf-8")
    papers.set_marked(root, "paper/main.md", True, via="cli")
    (root / "research").mkdir()
    (root / "research" / "tasks.json").write_text(json.dumps([{"id": "a", "text": "first", "done": True}, {"id": "b", "text": "second"}]))
    record_files.snapshot_now(root, root / "research" / "tasks.json")
    return {"judgements": "judgements.toml.20250304T110530123456Z.toml"}


def _rows(base) -> dict[str, dict]:
    status, payload = _call(base, "GET", "/api/records")
    assert status == 200
    return {row["code"]: row for row in payload["rows"]}


# -- the engine's rows ------------------------------------------------------------------------------------------------


def test_each_record_says_when_it_was_last_backed_up_from_the_name_of_its_newest_backup(live):
    """A project with real backups: the judgments were backed up on 4 March 2025 (the stamp in the backup's name, in UTC), the
    paper's marks and the task list now. The row carries the time of the newest backup of its own file, and the name, and a
    file's modified time is nowhere in it -- the ledger is touched now, its backup is long ago."""
    base, root, _ = live
    names = _with_backups(root)
    rows = _rows(base)
    assert rows["judgements"]["snapshot"] == names["judgements"]
    assert rows["judgements"]["backed_up_at"] == "2025-03-04T11:05:30.123Z"
    assert ledger_mod.judgements_path(root).stat().st_mtime > time.time() - 600  # the file is new; the backup is not
    for code in ("papers", "tasks"):
        stamp = datetime.strptime(rows[code]["backed_up_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        assert abs((datetime.now(timezone.utc) - stamp).total_seconds()) < 600, code
        assert record_files.snapshot_time(rows[code]["snapshot"]) is not None and rows[code]["snapshot"].startswith(
            {"papers": "papers.toml.", "tasks": "tasks.json."}[code])
    assert rows["mappings"]["backed_up_at"] is None and rows["mappings"]["snapshot"] is None  # nothing there, nothing copied


def test_a_project_with_no_backups_gives_no_time_and_the_marks_and_tasks_are_lines_of_their_own(live):
    """A project whose records were never backed up: no row has a time. The paper's marks (papers.toml) and the task list
    (research/tasks.json) are rows of the inventory with the files they live in, as 'none yet' while the files are not there."""
    base, root, _ = live
    rows = _rows(base)
    assert [code for code, row in rows.items() if row["backed_up_at"] is not None] == []
    assert rows["papers"]["path"] == ".researchos/papers.toml" and rows["papers"]["facts"]["exists"] is False
    assert rows["tasks"]["path"] == "research/tasks.json" and rows["tasks"]["facts"]["exists"] is False
    assert {rows["papers"]["count"], rows["tasks"]["count"]} == {"none yet"}
    assert set(rows) >= {"judgements", "mappings", "attempts", "attempts_config", "canvas", "variables", "notes", "decisions",
                         "papers", "tasks", "history"}


def test_the_marks_and_the_task_list_are_counted_and_a_task_file_that_is_not_tasks_says_so(live):
    """The rows count what the files hold: one draft set as the paper, none put out; two tasks, one done. A task file that
    is not a list of tasks is a problem on its line (`invalid`), not an error of the panel."""
    base, root, _ = live
    _with_backups(root)
    rows = _rows(base)
    assert rows["papers"]["facts"]["marked"] == 1 and rows["papers"]["facts"]["out"] == 0
    assert (rows["tasks"]["facts"]["tasks"], rows["tasks"]["facts"]["done"]) == (2, 1)
    (root / "research" / "tasks.json").write_text('{"not": "a list"}', encoding="utf-8")
    broken = _rows(base)["tasks"]
    assert "invalid" in broken["facts"]["issues"] and broken["problems"] and "tasks" not in broken["facts"]


def test_the_newest_backup_of_the_variable_cards_is_the_latest_by_time_not_by_name(tmp_path):
    """Cards are backed up under names that do not sort by time (a log and a version file of different cards): the row's
    newest is the one taken last."""
    older, newer = "v2.md.20250101T000000000000Z.md", "log.toml.20260101T000000000000Z.toml"
    assert max([older, newer]) == older  # by name, the older would win
    assert max([older, newer], key=inventory._taken) == newer
    assert inventory.backup_time(newer) == "2026-01-01T00:00:00.000Z" and inventory.backup_time(None) is None
    assert inventory.backup_time("notes.toml") is None  # a name with no stamp has no time
    for name in ("x.toml.20260102T030405123456Z.toml", "x.toml.20260102T030405123456Z.write.toml", "x.toml.20260102T030405123456Z.2.toml",
                 "x.md.20260102T030405123456Z.write.md", "LICENSE.20260102T030405123456Z"):
        assert record_files.snapshot_time(name) == datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc), name


# -- 在 Finder 中显示 --------------------------------------------------------------------------------------------------


def test_the_reveal_route_shows_a_record_and_its_backup_and_refuses_everything_outside_the_project(live, monkeypatch, tmp_path):
    """The panel reveals a record through the existing /api/open route (macOS's own call stubbed): the ledger, the task list
    and the backups folder open; a path outside the project -- `..`, an absolute path, a link that leads out, the version
    store the history row names -- is refused before anything is run."""
    base, root, _ = live
    _with_backups(root)
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    (root / ".researchos" / "link").symlink_to(outside)
    monkeypatch.setattr(server, "_is_macos", lambda: True)
    calls = []
    monkeypatch.setattr(server.subprocess, "run", lambda args, **kw: calls.append(args))
    rows = _rows(base)
    for path in (rows["judgements"]["path"], rows["tasks"]["path"], ".researchos/backups"):
        status, payload = _call(base, "POST", "/api/open", {"path": path, "reveal": True})
        assert status == 200 and payload["reveal"] is True, path
    assert [args[:2] for args in calls] == [["open", "-R"]] * 3
    calls.clear()
    for path in ("../outside.txt", str(outside), ".researchos/link", rows["history"]["path"], "/etc/passwd"):
        status, _payload = _call(base, "POST", "/api/open", {"path": path, "reveal": True})
        assert status == 403, path
    assert calls == []


# -- the panel ----------------------------------------------------------------------------------------------------------

PANEL = _app_block("// -- 你的记录与备份 (9.2", 'document.getElementById("records-btn")')

_PRELUDE = r"""
global.window = {};
%(i18n)s
%(dom)s
%(desk)s
global.chevronIcon = () => el("svg", "res-chevron");
global.uiLang = () => (window.ROI18N ? window.ROI18N.lang : "zh");
global.renderBlockingError = (node, framing, err) => { node.textContent = t(framing) + (err && err.detail ? " [" + err.detail + "]" : ""); };
global.renderErrorBox = (node, err) => { node.textContent = "ERROR " + err.message; };
global.panelTitle = panelPart("panel-title"); global.panelSubtitle = panelPart("panel-subtitle"); global.panelBody = panelPart("panel-body");
global.text = (e) => e.textContent;
global.byClass = (host, c) => host.all().filter((k) => k.cls.has(c));
global.calls = [];
%(panel)s
"""


def _page(language: str | None) -> str:
    i18n = (f"window.ROI18N_SAVED = {json.dumps(language)};\n" + _i18n_source() if language else "") + "\n" + FILL_VARS_LINE + "\n" + T_LINE
    return _PRELUDE % {"i18n": i18n, "dom": _PANEL_DOM, "desk": (WEBAPP / "desk.js").read_text(encoding="utf-8"), "panel": PANEL}


def _open_panel(payload: dict, steps: str = "", *, language: str | None) -> dict:
    """The panel opened on `payload`, the answer of /api/records, then `steps`; what it drew, line by line."""
    script = _page(language) + r"""
(async () => {
  const payload = %(payload)s;
  global.apiGet = async (path) => { calls.push("GET " + path); return payload; };
  global.posted = [];
  global.apiPost = async (path, body) => { posted.push([path, body]); if (global.refuse) throw Object.assign(new Error("refused"), { detail: global.refuse }); return { opened: "/x", reveal: true }; };
  global.state = { panelSubject: null };
  global.openPanel = () => {};
  await openRecordsPanel();
  const rows = byClass(panelBody, "records-row");
  const first = (row, c) => (byClass(row, c)[0] ? text(byClass(row, c)[0]) : null);
  global.view = rows.map((row) => ({
    code: row.dataset.code, kind: first(row, "records-kind"), count: first(row, "records-count"), backup: first(row, "records-backup"),
    problem: first(row, "records-problem"), reveal: row.all().filter((k) => k.tag === "BUTTON").map(text),
    fold: byClass(row, "records-fold").map((f) => ({ summary: text(f.kids[0]), lines: f.kids.slice(1).map(text), chevron: byClass(f, "res-chevron").length })),
    status: first(row, "panel-status") }));
  %(steps)s
  process.stdout.write(JSON.stringify({ title: text(panelTitle), subtitle: text(panelSubtitle), intro: first(panelBody, "records-intro"), view, calls, posted, extra: global.extra || null }));
})();
""" % {"payload": json.dumps(payload, ensure_ascii=False), "steps": steps}
    return _node(script)


@pytest.fixture
def used(live):
    """The panel's payload from a project with real backups: what /api/records answers."""
    base, root, _ = live
    _with_backups(root)
    status, payload = _call(base, "GET", "/api/records")
    assert status == 200
    return payload


@pytest.fixture
def unused(live):
    """And from one with none."""
    base, _root, _ = live
    return _call(base, "GET", "/api/records")[1]


def _by_code(out: dict) -> dict[str, dict]:
    return {row["code"]: row for row in out["view"]}


@needs_node
def test_the_panel_says_in_words_what_is_kept_with_the_time_of_its_last_backup_and_folds_the_file_names(used):
    """DESIGN 21.7: one line per kind of record, in words -- the title, what it holds, when it was last backed up (the long
    ago of the ledger as a date, the file backed up now as today), 「在 Finder 中显示」 for the ones the project holds -- and
    the file's name and the backup's name under a folded 「详情」 with the chevron, not on the line."""
    out = _open_panel(used, language=None)
    rows = _by_code(out)
    # B5-3: the subtitle only repeated the title and is gone; the records the project holds come first, in 21.7's order, and then the
    # ones that say 还没有, in the same order (the engine's order, which this fixture used to pin, was not the page's)
    assert (out["title"], out["subtitle"]) == ("你的记录与备份", "")
    assert [row["kind"] for row in out["view"]] == [
        "连线确认", "论文正文与不属于论文", "任务列表", "版本历史（保存的材料，不是记录）",
        "研究判断", "尝试表", "手画的连线", "研究路线的布局", "变量卡", "备注", "经 PI 批准的决定"]
    judgements_row = rows["judgements"]
    assert judgements_row["count"] == "1 条记录，其中 1 条正在生效"
    assert judgements_row["backup"] == "最近一次备份：2025年3月4日"
    assert judgements_row["reveal"] == ["在 Finder 中显示"]
    assert judgements_row["fold"] == [{"summary": "详情", "lines": ["存放位置.researchos/judgements.toml",
                                                                 "最新备份judgements.toml.20250304T110530123456Z.toml"], "chevron": 1}]
    assert rows["papers"]["count"] == "1 份文稿设为论文正文，不属于论文的图和表：0"
    assert rows["tasks"]["count"] == "2 项任务，其中 1 项已完成"
    for code in ("papers", "tasks"):
        assert rows[code]["backup"].startswith("最近一次备份：今天 ") and rows[code]["reveal"] == ["在 Finder 中显示"], code
    # nothing there yet: said so, and nothing to show in Finder; the file names are still under 详情
    assert (rows["mappings"]["count"], rows["mappings"]["backup"], rows["mappings"]["reveal"]) == ("还没有", "还没有备份过", [])
    assert rows["canvas"]["count"] == "还没有摆放过" and rows["variables"]["count"] == "还没有"
    # the version store is kept material, not a record: no backup line, and it lives outside the project, so nothing to reveal
    assert rows["history"]["backup"] is None and rows["history"]["reveal"] == [] and "不在项目里" in rows["history"]["count"]
    assert [fold["lines"][0].startswith("存放位置/") for fold in rows["history"]["fold"]] == [True]
    # no path and no snapshot name on any line: they are under 详情 only
    for row in out["view"]:
        for line in (row["kind"], row["count"], row["backup"], row["problem"]):
            assert not line or (".toml" not in line and "/" not in line), line
    assert out["intro"] == "Research OS 为这个项目保存了这些记录，并在它们变化时备份。"


@needs_node
def test_the_panel_in_english_says_the_same_in_english(used):
    out = _open_panel(used, language="en")
    rows = _by_code(out)
    assert (out["title"], out["subtitle"]) == ("Your records and backups", "")
    assert [row["kind"] for row in out["view"]] == [
        "Link judgments", "The paper, and what is not part of it", "Task list", "Version history (kept material, not a record)",
        "Research assessment", "Attempt table", "Hand-drawn links", "Layout of the research routes", "Variable card", "Comments",
        "Decisions approved by the PI"]
    assert rows["judgements"]["count"] == "1 entry, 1 in force"
    assert rows["judgements"]["backup"] == "Last backed up: 4 Mar 2025"
    assert rows["judgements"]["reveal"] == ["Show in Finder"]
    assert rows["judgements"]["fold"][0]["summary"] == "Details"
    assert rows["judgements"]["fold"][0]["lines"][0] == "Kept in.researchos/judgements.toml"
    assert rows["papers"]["count"] == "1 draft set as the paper; figures and tables not part of it: 0"
    assert rows["tasks"]["count"] == "2 tasks, 1 done" and rows["tasks"]["backup"].startswith("Last backed up: today ")
    assert (rows["mappings"]["count"], rows["mappings"]["backup"]) == ("None yet", "Not backed up yet")
    assert re.match(r"\d+ versions?, ", rows["history"]["count"]) and "Research OS’s own data folder" in rows["history"]["count"]
    assert out["intro"] == "Research OS keeps these records for this project and backs them up when they change."
    cjk = re.compile("[一-鿿　-〿＀-￯]")
    assert [line for row in out["view"] for line in (row["kind"], row["count"], row["backup"], row["problem"], row["status"])
            if line and cjk.search(line)] == []


@needs_node
def test_a_project_with_no_backups_says_so_on_every_line_that_could_have_one(unused):
    """The project that was never backed up: 「还没有备份过」 on each record the project holds a file of, no time anywhere, and
    the lines under 详情 name no backup."""
    for language, said, none in ((None, "还没有备份过", "还没有"), ("en", "Not backed up yet", "None yet")):
        out = _open_panel(unused, language=language)
        # the history is kept material, and a project with no attempt table has no file of it (path "-") to back up
        backed = [row for row in out["view"] if row["code"] not in ("history", "legacy", "attempts")]
        assert backed and {row["backup"] for row in backed} == {said}, language
        assert [row["backup"] for row in out["view"] if row["code"] in ("history", "attempts")] == [None, None]
        for row in backed:
            assert all(not line.startswith(("最新备份", "Newest backup")) for fold in row["fold"] for line in fold["lines"]), row["code"]
        assert _by_code(out)["papers"]["count"] == none and _by_code(out)["tasks"]["count"] == none


@needs_node
def test_a_record_with_a_problem_keeps_its_sentence_in_words_and_the_engines_text_goes_under_details(live):
    """A ledger that cannot be parsed: its line says in words which line (and in English, the same), the engine's English is
    under 详情 with the file's name, and the reveal still offers the file."""
    base, root, _ = live
    judgements.judge(root, READ, "confirmed", via="cli")
    path = ledger_mod.judgements_path(root)
    path.write_bytes(path.read_bytes() + b"\n[[judgement]\n")
    payload = _call(base, "GET", "/api/records")[1]
    line = next(r for r in payload["rows"] if r["code"] == "judgements")["facts"]["line"]
    for language, said in ((None, f"第 {line} 行无法解析"), ("en", f"Line {line} cannot be parsed")):
        row = _by_code(_open_panel(payload, language=language))["judgements"]
        assert row["problem"] == said and row["reveal"]
        engine = row["fold"][0]["lines"][-1]
        assert "invalid" in engine
    # a hand-edited, unreadable-in-part project: every issue the engine names has a sentence of its own
    for code, zh, en in (("dataless", "正在从云端下载", "Downloading from the cloud"), ("unreadable", "无法读取", "Cannot read"),
                         ("conflict_copy", "旁边有同步冲突副本", "A sync conflict copy sits beside it"),
                         ("refused_entries", "有几条连线没有被采用", "Some links were not used"),
                         ("needs_migration", "等待迁移", "Waiting to be migrated"), ("migration_unfinished", "上次迁移没有完成", "The last migration did not finish"),
                         ("shrunk", "文件里的记录比 Research OS 记得的少了几条，需要你先确认以哪一份为准",
                          "The file holds fewer records than Research OS remembers; you need to say which copy to go by"),
                         ("missing", "文件找不到了，但留有它的备份", "The file is missing, but a backup of it exists"),
                         ("copy_missing", "留存的副本不见了", "A kept copy is missing")):
        shaped = {"rows": [{"kind": "k", "path": ".researchos/mappings.toml", "count": "", "snapshot": None, "backed_up_at": None,
                            "problems": ["engine words"], "code": "mappings", "facts": {"exists": True, "state": "present", "links": 1,
                                                                                       "with_note": 0, "issues": [code]}}]}
        assert _open_panel(shaped, language=None)["view"][0]["problem"] == zh
        assert _open_panel(shaped, language="en")["view"][0]["problem"] == en
    # a reason the page has no sentence for says the engine's own Chinese sentence in Chinese, and never Chinese in English
    odd = {"rows": [{"kind": "k", "path": ".researchos/notes.toml", "count": "", "snapshot": None, "backed_up_at": None, "problems": ["engine words"],
                     "code": "notes", "facts": {"exists": True, "file_state": "present", "notes": 2, "nodes": 1, "withdrawn": 0,
                                                "issues": ["some_new_reason"], "trust_message": "引擎自己的一句话"}}]}
    assert _open_panel(odd, language=None)["view"][0]["problem"] == "引擎自己的一句话"
    assert _open_panel(odd, language="en")["view"][0]["problem"] == "Needs attention"


@needs_node
def test_showing_a_record_in_finder_posts_the_files_own_path_and_a_refusal_is_said_on_its_line(used):
    """「在 Finder 中显示」 asks the engine to reveal the project-relative path of the record, and says so on the line, shortly;
    a refusal (the engine's, for a path outside the project) is said on that line with its words under 详情."""
    steps = r"""
    const buttons = panelBody.all().filter((k) => k.tag === "BUTTON");
    buttons[0].click(); await settle();
    global.refuse = "outside the project";
    buttons[1].click(); await settle();
    global.extra = { statuses: byClass(panelBody, "panel-status").map(text).filter(Boolean), classes: byClass(panelBody, "panel-status").map((e) => e.className).filter((c) => c !== "panel-status") };
    """
    for language, shown, refused in ((None, "已在 Finder 中显示。", "无法在 Finder 中显示"), ("en", "Shown in Finder.", "Could not show in Finder")):
        out = _open_panel(used, steps, language=language)
        first, second = out["posted"][0][1]["path"], out["posted"][1][1]["path"]
        assert out["posted"][0][0] == "/api/open" and out["posted"][0][1]["reveal"] is True
        assert (first, second) == tuple(row["path"] for row in used["rows"] if row["facts"].get("exists") and row["code"] != "history")[:2]
        assert out["extra"]["statuses"] == [shown, f"{refused} [outside the project]"], language
        assert out["extra"]["classes"] == ["panel-status ok", "panel-status err"]


@needs_node
def test_the_panel_asks_the_engine_once_and_says_a_failure_in_the_error_box(used):
    """Opening the panel is one read of /api/records; a read that fails shows the page's error box and nothing else."""
    assert _open_panel(used, language=None)["calls"] == ["GET /api/records"]
    script = _page(None) + r"""
(async () => {
  global.apiGet = async () => { throw new Error("down"); };
  global.state = { panelSubject: null }; global.openPanel = () => {};
  await openRecordsPanel();
  process.stdout.write(JSON.stringify(text(panelBody)));
})();
"""
    assert _node(script) == "ERROR down"


def test_the_panels_script_is_in_the_converted_surfaces_and_has_no_path_on_a_line():
    """The block is held to 13.6 with the other converted surfaces (every Chinese literal is the whole argument of t()), and
    the sidebar's button and the panel keep the words of 21.6."""
    from test_v9_language import CONVERTED

    assert ("app.html", "// -- 你的记录与备份 (9.2", 'document.getElementById("records-btn")') in CONVERTED
    html = APP_HTML.read_text(encoding="utf-8")
    assert 'data-i18n>备份</button>' in html and 'title="查看你的记录有哪些，以及最近一次备份的时间"' in html
    assert "最新快照" not in html and "还没有快照" not in html

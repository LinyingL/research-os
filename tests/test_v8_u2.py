"""Research OS V8 phase U2 (DESIGN.md 12.0, 12.9, 12.11): the new page
structure -- a sidebar with the project switcher, the sections, the shared
drive's line and who I am -- and the desk (工作台): what waits for my
decision, what changed since I last looked and exactly where, which results
lost their evidence, which routes were abandoned. Plus the served page's
visual vocabulary: nothing that reaches the screen carries an emoji or an
arrow character. Every test names its scenario."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from researchos import desk, history, people, proposals
from researchos.records import notes as notes_mod
from researchos.records.identity import read_identity
from researchos.webapp import server

from test_v8_phase2 import _call, _conn, _csv, _map, _project, _scan
from test_v8_phase3 import MEMBER, PI, Team, _serve

WEBAPP = Path(server.__file__).parent
APP_HTML = WEBAPP / "app.html"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


# -- 12.11: nothing on screen looks generated -------------------------------------------------------------

#: 12.11's forbidden marks: emoji (the pictographic planes, the dingbats and
#: miscellaneous symbols the researcher's verdict markers come from, the
#: variation selector that makes a symbol an emoji) and arrow characters.
FORBIDDEN = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️←-⇿▲-◅＋]|->|=>"
)
_REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")
_KEYWORDS_BEFORE = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw", "yield", "await"}


def js_strings(src: str) -> list[tuple[int, str]]:
    """Every string and template literal of a script, with its line --
    comments and regular expressions skipped (an arrow function's `=>` is
    code, not text; a string is what can reach the screen)."""
    i, n, line, last, out = 0, len(src), 1, "", []
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += src.count("\n", i, j)
            i = j
        elif c in "'\"`":
            j, buf, start = i + 1, [], line
            while j < n and src[j] != c:
                if src[j] == "\\":
                    buf.append(src[j:j + 2])
                    j += 2
                    continue
                if c == "`" and src.startswith("${", j):
                    depth, j = 1, j + 2
                    while j < n and depth:
                        depth += {"{": 1, "}": -1}.get(src[j], 0)
                        j += 1
                    buf.append(" ")
                    continue
                if src[j] == "\n":
                    line += 1
                buf.append(src[j])
                j += 1
            out.append((start, "".join(buf)))
            i, last = j + 1, "str"
        elif c == "/" and (last == "" or last in _REGEX_BEFORE or last in _KEYWORDS_BEFORE):
            j, in_class = i + 1, False
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "/" and not in_class:
                    break
                j += 1
            j += 1
            while j < n and src[j].isalpha():
                j += 1
            i, last = j, "re"
        else:
            word = re.match(r"[A-Za-z_$][\w$]*|\d[\w.]*", src[i:i + 64])
            if word:
                last = word.group(0) if not word.group(0)[0].isdigit() else "num"
                i += len(word.group(0))
            else:
                last = c
                i += 1
    return out


def html_screen_text(html: str) -> list[str]:
    """What the page's markup itself puts on screen: text outside scripts,
    styles and comments, and the attributes a reader sees or hears."""
    body = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    body = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", body, flags=re.S)
    attrs = re.findall(r'\b(?:title|aria-label|placeholder|alt)="([^"]*)"', body)
    text = re.sub(r"<[^>]+>", "\n", body)
    return [t for t in text.split("\n") if t.strip()] + attrs


def _served_scripts() -> list[Path]:
    html = APP_HTML.read_text(encoding="utf-8")
    names = re.findall(r'<script src="/([\w.-]+\.js)"></script>', html)
    return [WEBAPP / name for name in names]


def _offenders() -> list[str]:
    html = APP_HTML.read_text(encoding="utf-8")
    found = [f"app.html markup: {t.strip()!r}" for t in html_screen_text(html) if FORBIDDEN.search(t)]
    for block in re.findall(r"<script>(.*?)</script>", html, re.S):
        found += [f"app.html script line {ln}: {s!r}" for ln, s in js_strings(block) if FORBIDDEN.search(s)]
    for path in _served_scripts():
        found += [f"{path.name} line {ln}: {s!r}" for ln, s in js_strings(path.read_text(encoding="utf-8"))
                  if FORBIDDEN.search(s)]
    return found


def test_12_11_no_emoji_or_arrow_reaches_the_screen_from_the_served_page():
    """12.11 binding for every surface: app.html's markup and every string
    in its scripts and in each served script (canvas.js, desk.js) is free
    of emoji and of → ← ↔ ⇒ ▸ ▾ ► ◄ ＋ '->' '=>'. The researcher's own text
    is exempt by construction: it comes from the server at run time."""
    assert {p.name for p in _served_scripts()} >= {"canvas.js", "desk.js"}
    assert _offenders() == []


def test_12_11_the_scanner_sees_strings_and_skips_code():
    """The check above is not vacuous: a string carrying an arrow or an
    emoji is caught; an arrow function, a comment and a regex are not."""
    src = (
        "const f = (a) => a + 1; // → in a comment\n"
        "const r = /[&<>\"']/g;\n"
        "const ok = \"读取\"; const bad = 'a → b'; const e = `✅ ${x} done`; const t = \"x->y\";\n"
        "/* ▸ */ const p = '工作台';\n"
    )
    strings = [s for _, s in js_strings(src)]
    assert "读取" in strings and "工作台" in strings
    flagged = [s for s in strings if FORBIDDEN.search(s)]
    assert flagged == ["a → b", "✅   done", "x->y"]
    assert [t for t in html_screen_text('<button title="打开 ▾">新增 ＋</button><!-- → -->') if FORBIDDEN.search(t)] == [
        "新增 ＋", "打开 ▾"]


def test_12_11_verdicts_are_shown_as_words_never_as_markers():
    """The tree, the attempt panel, the edit form's quick choices and the
    canvas's scope list read `verdict_status` (现行 / 已放弃 / …): the page
    no longer knows any marker, and no badge shows one."""
    html = APP_HTML.read_text(encoding="utf-8")
    canvas = (WEBAPP / "canvas.js").read_text(encoding="utf-8")
    assert "VERDICT_MARKERS" not in html and "verdictMarker" not in html and "verdictMarker" not in canvas
    assert "function verdictLabel(status)" in html and "status-label tone-" in html
    assert "s.verdict_status.label" in canvas and "frameTone(f.verdict_status)" in canvas


# -- 12.9: the sidebar ---------------------------------------------------------------------------------------


SECTIONS = [("desk", "工作台"), ("routes", "研究路线"), ("results", "成果"), ("variables", "变量"),
            ("literature", "文献"), ("data", "数据与代码"), ("proposals", "提议"), ("members", "成员")]


def test_12_9_the_sidebar_has_the_projects_and_the_sections_in_order():
    """The tabs are gone; the sidebar holds the project switcher (a real
    menu button naming the project, no glyph), the eight sections in 12.9's
    order with drawn line icons, and at the bottom the shared drive's line
    and who I am."""
    html = APP_HTML.read_text(encoding="utf-8")
    assert 'class="tab' not in html and 'role="tablist" aria-label="数据与代码"' in html
    found = re.findall(r'<button type="button" class="side-item" data-section="(\w+)" data-view="(\w+)">'
                       r'<svg class="side-icon"[^>]*><path d="[^"]+"/></svg><span class="side-label" data-i18n>([^<]+)</span>', html)
    assert [(s, label) for s, _v, label in found] == SECTIONS
    assert dict((s, v) for s, v, _l in found)["routes"] == "tree" and dict((s, v) for s, v, _l in found)["data"] == "lineage"
    nav = html[html.index('<aside class="sidebar"'):html.index("</aside>")]
    assert nav.index('id="project-menu-btn"') < nav.index('class="side-nav"') < nav.index('id="share-line"') < nav.index('id="me-line"')
    assert 'aria-haspopup="menu"' in nav and 'id="side-count-proposals"' in nav
    assert 'projectMenuBtn.textContent = label || NO_PROJECT_NAME;' in html
    # 数据与代码 holds the lineage list and 关系图 (the canvas, 12.9).
    assert 'data-subview="lineage"' in html and 'data-subview="canvas"' in html and ">关系图<" in html


def test_12_9_every_old_view_stays_reachable_and_the_shell_commands_still_work():
    """Until the later phases rebuild them, 研究路线 is the decision tree and
    数据与代码 the lineage report beside 关系图; the native menu's commands
    keep their names (12.12: identifiers the researcher never sees stay)."""
    html = APP_HTML.read_text(encoding="utf-8")
    for view in ("desk", "tree", "results", "lineage", "canvas", "variables", "literature", "proposals", "members"):
        assert f'id="view-{view}"' in html
    for command in ('"tree": () => activateView("tree")', '"lineage": () => activateView("lineage")',
                    '"canvas": () => activateView("canvas")', '"variables": () => activateView("variables")',
                    '"add-project": () => openAddDialog(),'):
        assert command in html
    assert 'activateView(storedView() || "desk")' in html


# -- the served desk script ----------------------------------------------------------------------------------


@pytest.fixture
def live(tmp_path):
    root = _project(tmp_path / "proj")
    with _serve(root) as base:
        yield base, root


def test_desk_js_is_served_verbatim_same_origin_and_packaged(live):
    base, _root = live
    with urllib.request.urlopen(base + "/desk.js") as resp:
        assert resp.headers.get("Content-Type", "").startswith("text/javascript")
        assert resp.read() == (WEBAPP / "desk.js").read_bytes()
    req = urllib.request.Request(base + "/desk.js", headers={"Origin": "http://evil.example"})
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(req)
    assert caught.value.code == 403
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"webapp/desk.js"' in pyproject
    js = (WEBAPP / "desk.js").read_text(encoding="utf-8")
    assert re.search(r"https?://", js) is None
    # The desk per 12.0 for each person, and 12.12's sentence for a member
    # not yet in the team (not a project of one).
    for block in ("等你批准", "你上次查看以来的改动", "依据变了的成果", "已放弃的路线", "我的提议", "官方版本有更新",
                  "你还不是这个项目的成员：请 PI 把你加入团队，然后同步"):
        assert block in js, block
    assert 'apiPost("/api/share/sync", {})' in js and js.count("apiPost(") == 1  # the desk's one write


# -- 12.0: the desk's data -----------------------------------------------------------------------------------


def _backdate(root: Path, days: int = 10) -> None:
    """Move everything the version store recorded `days` into the past: the
    project was added then, and what follows is a change."""
    pid = read_identity(root).identity.id
    with sqlite3.connect(history.history_dir(pid) / history.VERSIONS_FILENAME) as conn:
        for seq, at in conn.execute("SELECT seq, at FROM versions").fetchall():
            when = datetime.fromisoformat(at) - timedelta(days=days)
            conn.execute("UPDATE versions SET at = ? WHERE seq = ?", (when.isoformat(timespec="seconds"), seq))


def _desk(root: Path, since: str | None = None) -> dict:
    conn = _conn(root)
    try:
        return desk.desk(conn, root, since)
    finally:
        conn.close()


def test_12_0_a_project_just_added_reports_no_change_for_every_file(tmp_path):
    """The first scan's versions are the baseline: a project added today
    does not list each of its files as new."""
    root = _project(tmp_path / "proj")
    out = _desk(root)
    assert out["since_default"] and out["default_days"] == 7
    assert out["changes"] == []


def test_12_0_what_changed_since_i_last_looked_and_exactly_where(tmp_path):
    """After the project was added: a script gains lines, a table gains
    rows and a column, a figure appears, an attempt's verdict moves. Each
    is one item saying exactly what, with the verdict as words."""
    root = _project(tmp_path / "proj")
    _backdate(root)
    (root / "steps" / "16-build.py").write_text((root / "steps" / "16-build.py").read_text() + "df = df.dropna()\n")
    (root / "data" / "panel.csv").write_text(_csv(97, ("id", "x", "log_ret")))
    (root / "figs" / "fig3.png").write_bytes(b"\x89PNG fig3")
    (root / "map.md").write_text(_map("☠️ 伪协整"))
    _scan(root)
    out = _desk(root)
    by_node = {c["node"]: c for c in out["changes"]}
    assert by_node["script:steps/16-build.py"]["what"] == "增加 1 行"
    table = by_node["dataset:data/panel.csv"]["what"]
    assert "行数从 96 增至 97" in table and "新增列 log_ret" in table and "→" not in table
    assert by_node["figure:figs/fig3.png"]["what"] == "新文件"
    attempt = by_node["attempt:map.md#16"]
    assert attempt["what"] == "判决改了" and attempt["number"] == "16"
    assert attempt["old_status"]["label"] == "待定" and attempt["new_status"] == {
        "status": "abandoned", "label": "已放弃", "words": "伪协整"}
    assert all(c["who"] == ["最初的研究者"] for c in out["changes"] if c["kind"] in ("file", "attempt"))  # 12.12
    # Newest first; nothing from before `since`.
    later = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    assert _desk(root, later)["changes"] == []
    # The abandoned route is listed with its words.
    assert [(a["number"], a["verdict_status"]["label"]) for a in out["abandoned"]] == [("16", "已放弃")]


def test_12_0_results_that_lost_their_evidence(tmp_path):
    """figs/fig1.png is cited by the paper but nothing makes it: listed
    with 「补上来源」; figs/fig2.png, whose chain is whole, is not -- and
    once its data file is gone it is, with the break named."""
    root = _project(tmp_path / "proj")
    out = _desk(root)
    assert [r["path"] for r in out["results"]] == ["figs/fig1.png"]
    assert out["results"][0]["breaks"][0]["kind"] == "no_generating_script"
    assert out["results"][0]["breaks"][0]["repair"] == "补上来源" and out["results"][0]["drafts"] == ["paper.md"]
    (root / "data" / "raw.csv").unlink()
    paths = {r["path"]: r for r in _desk(root)["results"]}
    assert [b["kind"] for b in paths["figs/fig2.png"]["breaks"]] == ["data_missing"]


def test_12_0_notes_by_others_and_never_my_own(tmp_path, monkeypatch):
    """A note shows on the desk of everyone but its author."""
    monkeypatch.setenv("RESEARCHOS_HOME", str(tmp_path / "home"))
    root = _project(tmp_path / "proj")
    people.write_me(*PI)
    notes_mod.add_note(root, "steps/16-build.py", "这里的缺失值要先处理", via="cli")
    since = datetime.now(timezone.utc) - timedelta(days=1)
    conn = _conn(root)
    try:
        mine = desk._note_changes(root, conn, since, PI[1])
        theirs = desk._note_changes(root, conn, since, MEMBER[1])
    finally:
        conn.close()
    assert mine == []
    assert [(n["path"], n["text"], n["who"]) for n in theirs] == [("steps/16-build.py", "这里的缺失值要先处理", [PI[0]])]


def test_12_0_an_approval_is_on_the_pis_desk_with_who_proposed_and_who_approved(tmp_path, monkeypatch):
    """The member proposes a verdict change and the PI approves it: the
    official desk lists the decision -- the member as proposer, the PI as
    approver -- and the verdict the attempt now has, as words."""
    team = Team(tmp_path, monkeypatch)
    with team.member():
        (team.work / "map.md").write_text(_map("✅ 稳健"))
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, "verdict_change", "16 成立"))
    with team.pi():
        proposals.approve(team.official, stored.id, note="同意")
        out = _desk(team.official)
    decision = next(c for c in out["changes"] if c["kind"] == "decision")
    assert decision["who"] == [MEMBER[0]] and decision["approved_by"] == [PI[0]]
    assert decision["notes"] == ["同意"] and decision["number"] == "16"
    assert decision["verdict_status"]["label"] == "现行"


def test_12_0_http_desk_is_origin_checked_and_takes_since(live):
    base, _root = live
    status, body = _call(f"{base}/api/desk")
    assert status == 200 and body["since_default"] is True and "changes" in body and "abandoned" in body
    assert body["review"] == 0  # 9.6's count, as /api/summary has it
    status, body = _call(f"{base}/api/desk?since=2026-01-01T00:00:00%2B00:00")
    assert status == 200 and body["since_default"] is False and body["since"].startswith("2026-01-01")
    status, body = _call(f"{base}/api/desk", headers={"Origin": "http://evil.example"})
    assert status == 403


def test_12_0_a_git_project_reports_commits_and_uncommitted_edits(tmp_path):
    """In a git project the desk reads the files git tracks from git: each
    commit since then (author, subject) and edits not yet committed."""
    from test_v8_phase2 import _git

    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    _project(root)
    _git(root, "add", "steps", "data", "figs", "paper.md", "map.md")
    _git(root, "commit", "-q", "-m", "first", date="2026-01-01T10:00:00+00:00")
    (root / "steps" / "17-plot.py").write_text((root / "steps" / "17-plot.py").read_text() + "# v2\n")
    _git(root, "commit", "-q", "-am", "图 2 改用对数刻度")
    (root / "paper.md").write_text((root / "paper.md").read_text() + "\nMore.\n")
    out = _desk(root, "2026-06-01T00:00:00+00:00")
    by_path = {c.get("path"): c for c in out["changes"] if c["kind"] == "file"}
    assert by_path["steps/17-plot.py"]["notes"] == ["图 2 改用对数刻度"] and by_path["steps/17-plot.py"]["who"] == ["Li"]
    assert by_path["paper.md"].get("uncommitted") is True
    assert "data/raw.csv" not in by_path


# -- the desk's wording, run under node against the real desk.js ---------------------------------------------


def _desk_call(name: str, *args) -> object:
    script = (
        "global.window = {}; global.document = {getElementById(){return null}};\n"
        + (WEBAPP / "desk.js").read_text(encoding="utf-8")
        + f"\nprocess.stdout.write(JSON.stringify(window.RODesk.{name}(...{json.dumps(list(args))}.map((a) =>"
        " (a && a.__date) ? new Date(a.__date) : a))));\n"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@needs_node
def test_desk_wording_when_share_line_and_proposals():
    now = {"__date": "2026-10-11T15:00:00"}
    assert _desk_call("_whenWords", "2026-10-11T09:05:00", now) == "今天 09:05"
    assert _desk_call("_whenWords", "2026-10-10T23:30:00", now) == "昨天 23:30"
    assert _desk_call("_whenWords", "2026-10-04T12:00:00", now) == "10月4日"
    assert _desk_call("_whenWords", "2025-03-01T12:00:00", now) == "2025年3月1日"
    assert _desk_call("_shareLineText", {"shared": None}, now) == ""
    member = {"shared": "/x", "reachable": True, "role": "member", "joined": {"synced": "2026-10-11T08:00:00"},
              "verification": "verified", "refused": [], "behind": False}
    assert _desk_call("_shareLineText", member, now) == "已同步 今天 08:00"
    assert _desk_call("_shareLineText", {**member, "behind": True}, now) == "有新的官方版本"
    assert _desk_call("_shareLineText", {**member, "verification": "no_tool"}, now) == "未验证 · 已同步 今天 08:00"
    assert _desk_call("_shareLineText", {**member, "reachable": False}, now) == "公盘现在打不开"
    pi = {"shared": "/x", "reachable": True, "role": "pi", "last_snapshot": {"at": "2026-10-10T18:00:00"},
          "pending_publish": {"count": 2}}
    assert _desk_call("_shareLineText", pi, now) == "有改动尚未发布 · 已发布 昨天 18:00"
    change = {"kind": "verdict_change", "files": [], "verdict": {
        "number": "16", "old_status": {"label": "待定"}, "new_status": {"label": "已放弃"}, "abandons": "yes"}}
    assert _desk_call("_proposalWhat", change) == "#16 的判决从「待定」改为「已放弃」，放弃这条路线"
    new = {"kind": "attempt_new", "files": [{"path": "steps/18.py"}], "attempt": {"number": "18", "description": "新路线"}}
    assert _desk_call("_proposalWhat", new) == "新增尝试 #18：新路线，附带 1 个文件"
    returned = {"state": "returned", "outcomes": [{"act": "returned", "text": "先补稳健性"}]}
    assert _desk_call("_proposalStateText", returned) == "已退回：先补稳健性"
    assert _desk_call("_seenKey", "p-1", None) == "ro.desk.seen:p-1:-"
    for text in (_desk_call("_proposalWhat", change), _desk_call("_shareLineText", pi, now)):
        assert not FORBIDDEN.search(text)

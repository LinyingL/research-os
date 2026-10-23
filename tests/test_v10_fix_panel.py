"""Research OS V10, review fixes on the desk's side panel (DESIGN.md 14.3,
14.1, 13.5): the confirmed findings of the V10 review that a person meets
in the panel -- each with the scenario that would have caught it, the
panel driven under node with the real desk.js (tests/test_v10_desk_panel's
stand-in DOM), the engine through its own calls.

Every test names its scenario."""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from researchos import cli, desk, share, signing

from test_v8_phase2 import _conn, _scan
from test_v8_phase4 import Drive
from test_v9_language import APP_HTML, NODE, WEBAPP, _app_function, _i18n_source, _node, english_table, needs_node
from test_v10_derives_from import _map
from test_v10_desk_panel import A16, _case, _run

NOTE = "写一条备注：为什么这样做、要注意什么"
LEAVE = "这里有还没写入的内容，离开它就不保存了。继续吗？"


def _listener(pattern: str) -> str:
    """The arrow function app.html attaches, as written there."""
    found = re.search(pattern, APP_HTML.read_text(encoding="utf-8"))
    assert found, pattern
    return found.group(1)


# -- 7, 8: one panel at a time, also from the frame and when leaving the desk ---------------------------


@needs_node
def test_14_3_the_frames_openers_ask_before_they_replace_a_half_typed_panel():
    """A note half typed in #16's panel: the header's 「N 项待复核」 and
    the sidebar's 记录 ask first -- declined, #16 stays with its text;
    accepted, the review list (the records) opens. The same question as
    every opener inside the desk."""
    chip = _listener(r'btn\.addEventListener\("click", (\(e\) => \{ e\.stopPropagation\(\); if \(mayReplacePanel\(\)\) '
                     r'openReviewPanel\(null\); \})\);')
    records = _listener(r'getElementById\("records-btn"\)\.addEventListener\("click", (\(\) => \{ if \(mayReplacePanel\(\)\) '
                        r'openRecordsPanel\(\); \})\);')
    steps = """
      %(may)s
      global.mayReplacePanel = mayReplacePanel;
      global.RODesk = window.RODesk; // a page's window is its global object
      global.openRecordsPanel = () => { state.panelSubject = { kind: "records" }; };
      global.openReviewPanel = () => { state.panelSubject = { kind: "review" }; };
      await window.RODesk.openAttempt(%(a16)s);
      type(field(%(note)s), "还没写完");
      const chip = %(chip)s, records = %(records)s;
      answers.push(false); chip({ stopPropagation() {} });
      answers.push(false); records();
      global.extra = { kept: [state.panelSubject.id, field(%(note)s).value] };
      answers.push(true); records();
      extra.records = state.panelSubject.kind;
    """ % {"may": _app_function("function mayReplacePanel("), "a16": json.dumps(A16), "note": json.dumps(NOTE),
           "chip": chip, "records": records}
    out = _run(_case("pi"), steps)
    assert out["asked"] == [LEAVE, LEAVE, LEAVE]
    assert out["extra"]["kept"] == [A16, "还没写完"] and out["extra"]["records"] == "records"
    nothing = _run(_case("pi"), steps.replace('type(field(%s), "还没写完");' % json.dumps(NOTE), ""))
    assert nothing["asked"] == [] and nothing["extra"]["records"] == "records"  # nothing typed: nothing asked
    html = APP_HTML.read_text(encoding="utf-8")
    for opener in ('"new-attempt": () => { if (mayReplacePanel()) openAttemptForm("append"); },',
                   'addEventListener("click", () => { if (mayReplacePanel()) openAttemptForm("append"); })'):
        assert opener in html
    node_js = (WEBAPP / "node.js").read_text(encoding="utf-8")
    assert '() => { if (mayReplace()) openReviewPanel(null); }' in node_js
    assert "mayReplacePanel()) openReviewPanel(link); }" in (WEBAPP / "canvas.js").read_text(encoding="utf-8")


@needs_node
def test_14_3_leaving_the_desk_takes_its_panel_with_it_after_asking():
    """Another view asked for while #16's panel holds a half-typed note:
    declined, the desk stays and the panel with its text; accepted, the
    panel closes. Nothing typed: it closes unasked. A refresh elsewhere
    never redraws a desk panel from the desk's payload."""
    steps = """
      global.closePanel = () => { panelPart("panel").classList.add("hidden"); state.panelSubject = null; };
      await window.RODesk.openAttempt(%(a16)s);
      type(field(%(note)s), "还没写完");
      answers.push(false);
      global.extra = { declined: window.RODesk.leaveDesk(), kept: [state.panelSubject && state.panelSubject.id, field(%(note)s).value] };
      answers.push(true);
      extra.accepted = window.RODesk.leaveDesk();
      extra.after = [state.panelSubject, panelPart("panel").className];
      await window.RODesk.openAttempt(%(a16)s);
      extra.quiet = window.RODesk.leaveDesk();
      global.currentView = "results";
      await window.RODesk.openAttempt(%(a16)s);
      const before = posted.length;
      extra.redrawn = window.RODesk.revalidatePanel(state.panelSubject);
    """ % {"a16": json.dumps(A16), "note": json.dumps(NOTE)}
    out = _run(_case("pi"), steps)
    assert out["extra"]["declined"] is False and out["extra"]["kept"] == [A16, "还没写完"]
    assert out["extra"]["accepted"] is True and out["extra"]["after"] == [None, "hidden"]
    assert out["extra"]["quiet"] is True and out["asked"] == [LEAVE, LEAVE]
    assert out["extra"]["redrawn"] is None
    view = _app_function("function activateView(")
    gate = 'if (currentView === "desk" && window.RODesk && RODesk.leaveDesk && !RODesk.leaveDesk()) return null;'
    assert gate in view and view.index(gate) < view.index("currentView = name;")
    assert 'if (currentView === "desk") await RODesk.revalidatePanel(subject);' in _app_function("async function revalidatePanel(")


# -- 14: each mark once in the panel's head ----------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_14_1_the_panels_head_says_each_mark_once(lang):
    """#16 touched by two of the PI's proposals reads 「等你批准」 once; a
    member's own proposed row touched by two of their proposals reads
    「提议中」 once -- its own mark kept as the member's -- as the card says them."""
    w = (lambda zh, en: en if lang == "en" else zh)
    pi = _case("pi", lang)
    waiting = w("等你批准", "Awaiting your approval")
    pi["desk"]["attempts"][0]["marks"]["proposals"] = [{"id": "p1", "label": waiting}, {"id": "p3", "label": waiting}]
    member = _case("member", lang)
    proposed = w("提议中", "Proposed")
    member["desk"]["attempts"][0].update(officialness={"state": "proposed", "label": proposed, "proposal": "p1"},
                                         marks={"proposals": [{"id": "p1", "label": proposed}, {"id": "p2", "label": proposed}],
                                                "review": None})
    step = "await window.RODesk.openAttempt(%s);" % json.dumps(A16)
    i18n = None if lang == "zh" else lang
    def head(case):  # the panel's head: down to its 打开详情页
        lines = _run(case, step, lang=i18n)["lines"]
        lines = lines[:lines.index("btn btn-small: " + w("打开详情页", "Open the details page"))]
        return [line for line in lines if line.startswith(("dp-mark", "own-mark"))]

    heads = [head(pi), head(member)]
    assert heads[0] == ["dp-mark: " + waiting, "dp-mark: " + w("待复核 1", "Needs review: 1")]
    assert heads[1] == ["own-mark: " + proposed]


# -- 10: a member's own row not yet official, changed in the panel ------------------------------------------


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Drive:
    if signing.tool() is None:
        pytest.skip("ssh-keygen is not installed here")
    d = Drive(tmp_path, monkeypatch, join=False)
    with d.pi():
        (d.official / "map.md").write_text(_map())
        _scan(d.official)
        share.publish(d.official, note="三行")
    with d.member():
        share.join(d.shared, d.work)
    return d


def test_14_3_the_desk_says_which_of_a_members_rows_the_official_version_lacks(drive):
    """The member adds #19 and changes #16: #19 is `new_row` (proposing it
    is proposing a new attempt), #16 is not (a verdict change)."""
    with drive.member():
        (drive.work / "map.md").write_text(_map(("16", "17", "18", "19")).replace("| r16 | 🕒 |", "| r16 | ✅ |"))
        assert cli.main(["ingest", str(drive.work)]) == 0  # the copy opened on this machine, as the app does
        conn = _conn(drive.work)
        try:
            cards = {a["number"]: a["officialness"] for a in desk.payload(conn, drive.work)["attempts"]}
        finally:
            conn.close()
    assert cards["19"]["new_row"] is True and cards["19"]["state"] == "mine"
    assert cards["16"]["state"] == "mine" and "new_row" not in cards["16"]
    assert "new_row" not in cards["17"]
    with drive.pi():
        conn = _conn(drive.official)
        try:
            assert not any("new_row" in (a.get("officialness") or {}) for a in desk.payload(conn, drive.official)["attempts"])
        finally:
            conn.close()


_CHANGE = """
  await window.RODesk.openAttempt(%(a16)s);
  global.extra = { before: panelLines() };
  find("已放弃", "BUTTON").click();
  type(field("用你自己的话说明这份提议：PI 会先看到这句话"), "一条死路");
  type(field("为什么放弃这条路线"), "残差不平稳");
  find("预览这一行").click(); await settle(); await settle();
  extra.preview = panelLines().filter((l) => l.startsWith("confirm-note"));
  find("保存后查看提议").click(); await settle(); await settle(); await settle();
"""


def _member_row(state: str) -> dict:
    case = _case("member")
    labels = {"mine": "我的改动，尚未提交", "proposed": "提议中", "approved": "已批准，待同步"}
    case["desk"]["attempts"][0]["officialness"] = {"state": state, "label": labels[state], "new_row": True,
                                                   **({"proposal": "p7"} if state != "mine" else {})}
    case["post"] = {"/api/attempts/preview": {"file": "map.md", "old_row": "a", "new_row": "b", "abandons": True,
                                              "reason_required": False},
                    "/api/attempts/write": {"ok": True, "file": "map.md"}}
    return case


@needs_node
def test_14_3_a_members_own_new_row_changed_in_the_panel_is_proposed_whole():
    """#16 is the member's own row the official version lacks: its change
    is written to their copy, then proposed as a new attempt (with the
    reason, as it abandons) -- never a verdict change of a row the official
    version does not have; proposed already, the new one replaces it;
    approved and not synced, nothing is offered but the sync."""
    mine = _run(_member_row("mine"), _CHANGE % {"a16": json.dumps(A16)})
    assert mine["composed"] == [{"kind": "attempt_new", "sentence": "一条死路", "number": "16", "reason": "残差不平稳"}]
    assert "confirm-note: 这一行还不在正式版本里：它会整行作为一条新的尝试提议。" in mine["extra"]["preview"]
    again = _run(_member_row("proposed"), _CHANGE % {"a16": json.dumps(A16)})
    assert again["composed"] == [{"kind": "attempt_new", "sentence": "一条死路", "number": "16", "supersedes": "p7",
                                  "reason": "残差不平稳"}]
    assert "confirm-note: 这一行还不在正式版本里：它会整行作为新的尝试再提议一次，替换你还在等待的那份提议。" in again["extra"]["preview"]
    approved = _run(_member_row("approved"), "await window.RODesk.openAttempt(%s);" % json.dumps(A16))
    assert "desk-alone: 这一行已经批准，正式版本里有了：先同步，再改它。" in approved["lines"]
    assert not any("预览这一行" in line for line in approved["lines"]) and approved["posted"] == []
    official = _run(_case("member"), _CHANGE % {"a16": json.dumps(A16)})  # an official row: a verdict change, as before
    assert official["composed"][0]["kind"] == "verdict_change"
    table = english_table()
    for key in ("这一行已经批准，正式版本里有了：先同步，再改它。", "这一行还不在正式版本里：它会整行作为一条新的尝试提议。",
                "这一行还不在正式版本里：它会整行作为新的尝试再提议一次，替换你还在等待的那份提议。"):
        assert key in table


# -- 20: a line's date in the screen's form ------------------------------------------------------------------


def _day_words(cases: list, lang: str | None, tz: str | None = None) -> list:
    i18n = "" if lang is None else f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    script = ("global.window = {};\n" + i18n + "global.document = { getElementById: () => null };\n"
              + (WEBAPP / "desk.js").read_text(encoding="utf-8")
              + f"\nconst now = new Date(2026, 9, 13, 23, 30);\nprocess.stdout.write(JSON.stringify("
              + f"{json.dumps(cases)}.map((d) => window.RODesk.dayWords(d, now))));\n")
    env = {**os.environ, **({"TZ": tz} if tz else {})}
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True, env=env)
    return json.loads(out.stdout)


@needs_node
@pytest.mark.parametrize("tz", [None, "America/Los_Angeles", "Pacific/Kiritimati"])
def test_13_5_a_lines_date_is_said_as_the_screen_says_dates(tz):
    """A 「源自」 line's day (2026-10-12) reads 10月12日 / 12 Oct, another year
    with its year, today and yesterday as such -- the same day west and
    east of Greenwich (read as that day where the researcher is, never as
    UTC midnight); anything that is not a day comes back as it is."""
    cases = ["2026-10-13", "2026-10-12", "2026-03-04", "2025-10-04", "07-26", None]
    assert _day_words(cases, None, tz) == ["今天", "昨天", "3月4日", "2025年10月4日", "07-26", ""]
    assert _day_words(cases, "en", tz) == ["Today", "Yesterday", "4 Mar", "4 Oct 2025", "07-26", ""]
    rm = (WEBAPP / "routemap.js").read_text(encoding="utf-8")
    assert "window.RODesk.dayWords(l.date, new Date())" in rm  # the map's line card says it the same way


# -- 24: a shared folder that hangs never holds up the desk ---------------------------------------------------


@needs_node
def test_14_4_the_desk_is_drawn_while_the_shared_folder_hangs_and_its_context_is_read_once():
    """/api/proposals and /api/share hang (a stalled mount); /api/desk
    answers (it waits SHARED_TIMEOUT at most). The desk -- map and column --
    is drawn at once; two more loads, the sidebar and the panel's wait for
    the context share ONE pair of requests; once the folder answers, the
    sidebar is drawn and the context waiting for it is there."""
    from test_v9_language import _DESK_DOM, _desk_cases

    case = json.dumps(_desk_cases("zh")[0], ensure_ascii=False)
    script = (
        "global.window = {};\n" + _DESK_DOM + "const calls = [];\n"
        "window.RORouteMap = { create() { return { mount() {}, update(d) { calls.push('map'); }, highlight() {}, visible() { return true; } }; } };\n"
        + (WEBAPP / "desk.js").read_text(encoding="utf-8")
        + f"\n(async () => {{ const c = {case}; const gets = []; const hold = [];\n"
        "  global.apiGet = (path) => { gets.push(path);\n"
        "    if (path === '/api/proposals' || path === '/api/share') return new Promise((ok) => hold.push(() => ok(path === '/api/proposals' ? c.proposals : c.share)));\n"
        "    return Promise.resolve(path.startsWith('/api/desk') ? c.desk : path === '/api/me' ? c.me : null); };\n"
        "  const host = { kids: [], parentNode: null, dataset: {}, set innerHTML(v) { this.kids = []; }, set textContent(v) { this.kids = []; },"
        " appendChild(k) { k.parentNode = this; this.kids.push(k); return k; } };\n"
        "  views.desk = host;\n"
        "  await window.RODesk.loadDesk(host, false);\n"
        "  const drawn = calls.slice();\n"
        "  await window.RODesk.loadDesk(host, true); await window.RODesk.loadDesk(host, true);\n"
        "  const sidebar = window.RODesk.refreshSidebar(); const waited = window.RODesk.ensureContext();\n"
        "  await new Promise((r) => setTimeout(r, 0));\n"
        "  const before = { proposals: gets.filter((g) => g === '/api/proposals').length, mode: window.RODesk.context().mode };\n"
        "  hold.splice(0).forEach((f) => f()); await new Promise((r) => setTimeout(r, 0));\n"
        "  hold.splice(0).forEach((f) => f()); await sidebar; const ctx = await waited;\n"
        "  process.stdout.write(JSON.stringify({ drawn, column: host.kids[0].kids.map((k) => k.className), before,"
        " after: ctx.mode, share: gets.filter((g) => g === '/api/share').length })); })();\n")
    out = _node(script)
    assert out["drawn"] == ["map"] and out["column"] == ["desk-map", "desk-column"]
    assert out["before"] == {"proposals": 1, "mode": None}  # one request for every caller, nothing known yet
    assert out["after"] == "pi" and out["share"] == 1


@needs_node
def test_14_4_a_desk_drawn_while_the_folder_was_slow_reads_itself_again():
    """The column came back slow (or with the last read, stale): on the
    desk, it is read again a little later -- joining the read in flight --
    a bounded number of times; once the folder answers, nothing more."""
    from test_v9_language import _DESK_DOM, _desk_cases

    case = _desk_cases("zh")[0]
    case["desk"]["column"]["shared"] = {"state": "stale", "message": "M", "at": "2026-10-13T10:00:00+02:00"}
    script = (
        "global.window = {};\n" + _DESK_DOM
        + "window.RORouteMap = { create() { return { mount() {}, update() {}, highlight() {}, visible() { return true; } }; } };\n"
        + (WEBAPP / "desk.js").read_text(encoding="utf-8")
        + f"\n(async () => {{ const c = {json.dumps(case, ensure_ascii=False)}; let desks = 0; const later = [];\n"
        "  global.currentView = 'desk'; global.setTimeout = (fn, ms) => { later.push(ms); global.next = fn; return later.length; };\n"
        "  global.apiGet = async (path) => { if (path.startsWith('/api/desk')) { desks += 1; return c.desk; }"
        " return { '/api/me': c.me, '/api/proposals': c.proposals, '/api/share': c.share }[path]; };\n"
        "  const host = { kids: [], parentNode: null, dataset: {}, set innerHTML(v) { this.kids = []; }, set textContent(v) { this.kids = []; },"
        " appendChild(k) { k.parentNode = this; this.kids.push(k); return k; } };\n"
        "  views.desk = host;\n"
        "  await window.RODesk.loadDesk(host, false);\n"
        "  for (let i = 0; i < 5; i++) { const fn = global.next; global.next = null; if (fn) await fn(); await Promise.resolve(); }\n"
        "  const slow = desks;\n"
        "  c.desk.column.shared = { state: 'ok', message: null };\n"
        "  await window.RODesk.loadDesk(host, true);\n"
        "  process.stdout.write(JSON.stringify({ slow, later, after: desks, waiting: !!global.next })); })();\n")
    out = _node(script)
    assert out["later"] == [5000, 5000, 5000] and out["slow"] == 4  # the first read, then three more at most
    assert out["after"] == 5 and out["waiting"] is False

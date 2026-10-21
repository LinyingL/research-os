"""Research OS V10, increment D4 (DESIGN.md 14.3-14.5, 14.6): what waits on
an attempt is handled in its panel on the desk -- its 待复核 judgments
confirmed again, changed or withdrawn in place, with 「撤销」 offered there
and the note emptied once used (a panel that keeps what is half typed would
otherwise never be drawn again); the column's 复核 opens that panel. The
panel's timeline says what changed since the last visit -- the row as the
node page says it, the files -- so 「有改动」 is explained in place. A
member's returned proposal stays in 我的提议 until 「知道了」, remembered
per person in the browser (ruled 2026-10-13). desk.js, node.js, i18n.js
and app.html's own review code run under node with a stand-in DOM; the
engine's list of what changed comes from a real project. Every test names
its scenario."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from researchos import desk

from test_v10_desk_language import DATA, SCRIPT, _page_helpers, _review
from test_v10_desk_language import _THEIRS as _REVIEW_THEIRS
from test_v10_desk_panel import _PANEL_DOM, A16, A17, _case
from test_v8_phase2 import _csv, _map, _project, _scan
from test_v8_u2 import FORBIDDEN, _backdate, _card, _desk
from test_v9_language import CJK, WEBAPP, _i18n_source, _node, needs_node

#: The researcher's own text in the fixtures: a verdict cell, a note, a reason, proposals' sentences.
_THEIRS = _REVIEW_THEIRS + ["待定 先看", "伪协整", "复查过", "证据不够", "放弃 16", "16 改回"]


def _helpers() -> str:
    """app.html's review code (test_v10_desk_language), its item, key and
    undo put on `global` for desk.js, and its refresh doing what the app's
    does on the desk: the desk read again, then the panel."""
    src = _page_helpers()
    stub = "async function refreshCurrentView() {\n"
    shared = "refreshCurrentView });"
    assert src.count(stub) == 1 and src.count(shared) == 1
    return (src.replace(stub, stub + "  if (global.refreshDesk) return global.refreshDesk();\n")
            .replace(shared, "refreshCurrentView, renderReviewItem, keyId, undoFromReview });"))


def _run(case: dict, steps: str, lang: str | None = None, storage: dict | None = None) -> dict:
    """Opens the desk of `case` (with app.html's review code, and this
    browser's storage holding `storage`), runs `steps` (JS, awaited) and
    reports the panel's lines, the column's, what was posted and opened."""
    i18n = "" if lang is None else f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    store = json.dumps(storage or {}, ensure_ascii=False)
    script = (
        "global.window = {};\n" + i18n + _PANEL_DOM + _helpers()
        + f"global.stored = {store};\nwindow.localStorage = {{ getItem: (k) => (k in stored ? stored[k] : null),"
        " setItem: (k, v) => { stored[k] = String(v); } };\n"
        + "global.refreshDesk = async () => { await window.RODesk.loadDesk(views.desk, true);"
        " await window.RODesk.revalidatePanel(state.panelSubject); };\n"
        + "global.scrolled = [];\nconst made = global.mk;\n"
        "global.mk = (...a) => Object.assign(made(...a), { scrollIntoView() { scrolled.push(this.className); } });\n"
        + (WEBAPP / "desk.js").read_text(encoding="utf-8") + "\n" + (WEBAPP / "node.js").read_text(encoding="utf-8")
        + "\nglobal.RONode = Object.assign({}, window.RONode, { openNode: (id) => opened.push(id) });\n"
        + "function columnLines() { const out = []; const walk = (n) => n.kids.forEach((k) => { if (typeof k !== 'string') {"
        " const own = k.kids.filter((x) => typeof x === 'string').join(''); if (own) out.push(k.className + ': ' + own); walk(k); } });"
        " walk(views.desk); return out; }\n"
        + f"(async () => {{ const c = {json.dumps(case, ensure_ascii=False)}; await openDesk(c);\n{steps}\n"
        " process.stdout.write(JSON.stringify({ lines: panelLines(), column: columnLines(), posted, opened, stored, scrolled,"
        " extra: global.extra || null }), () => process.exit(0)); })();\n")
    return _node(script)


def _chinese(lines: list[str]) -> list[str]:
    left = []
    for line in lines:
        rest = line
        for theirs in sorted(_THEIRS, key=len, reverse=True):
            rest = rest.replace(theirs, "")
        if CJK.search(rest):
            left.append(line)
    return left


# -- 待复核 in the panel (14.3) -------------------------------------------------------------------------------


def _reviewing(lang: str) -> dict:
    """The PI's desk: #16 holds two judgments of _review -- one whose basis
    changed, one the records disagree on --, the claim's is under no attempt."""
    case = _case("pi", lang)
    review = _review(lang)
    mine = [review["review"][0], review["review"][2]]
    case["desk"]["column"]["review"] = {"count": 3, "groups": [{"id": A16, "items": mine}],
                                        "none": {"items": [review["review"][1]], "cards": []}}
    case["get"]["/api/review"] = review
    return case


_JUDGE = """
  const group = views.desk.all().find((k) => k.dataset.pick === "r:" + %(a16)s);
  group.all().find((k) => k.tag === "BUTTON" && k.kids.join("") === %(review)s).click();
  await settle(); await settle(); await settle();
  global.extra = { first: panelLines(), scrolled: scrolled.slice() };
  c.post = { "/api/judgements": (body) => {
    if (body.verdict !== "undone") { c.desk.column.review.groups[0].items.shift(); c.get["/api/review"].review.shift(); }
    return { ok: true, generation: 5 };
  } };
  type(field(%(note)s), "复查过");
  button(%(holds)s).click();
  for (let i = 0; i < 8; i++) await settle();
  global.extra.after = panelLines();
  global.extra.note = fields().filter((f) => f.dataset.noteFor).map((f) => [f.value, f.dataset.typed || null]);
  button(%(undo)s).click();
  for (let i = 0; i < 8; i++) await settle();
"""


@needs_node
def test_14_3_an_attempts_judgments_are_confirmed_again_in_its_panel_and_undone_there():
    """The column's 复核 under #16 opens #16's panel at its 待复核: its two
    judgments drawn as the review panel draws them (the claim's, under no
    attempt, is not). A note typed, 仍然成立: recorded with the note, the
    note's field emptied, the panel drawn again without the judgment and
    with 「撤销」 in it; 撤销 undoes it."""
    steps = _JUDGE % {k: json.dumps(v, ensure_ascii=False) for k, v in {
        "a16": A16, "review": "复核", "note": "备注（可选）", "holds": "仍然成立", "undo": "撤销"}.items()}
    out = _run(_reviewing("zh"), steps)
    first = out["extra"]["first"]
    assert "panel-title plain-title: #16 TopicShift 波动" in first and "node-sec-title: 待复核" in first
    links = [line.split(" (title")[0] for line in first if line.startswith("review-link: ")]
    assert links == ["review-link: 17-叙事.Rmd 读取 panel.csv", "review-link: 17-叙事.Rmd 读取 panel2.csv",
                     "review-link: 17-叙事.Rmd 读取 panel2.csv"]  # its candidate, then the conflict
    assert "btn btn-small: 定为确认" in first
    assert not any("依据" in line and "论断" in line for line in first) and not any("精度为 0.87" in line for line in first)
    assert out["extra"]["scrolled"] == ["node-sec dp-part dp-review"]
    key = {"src": SCRIPT, "dst": DATA, "type": "reads", "extractor": "python-ast"}
    assert out["posted"] == [["/api/judgements", {**key, "verdict": "confirmed", "note": "复查过"}],
                             ["/api/judgements", {**key, "verdict": "undone"}]]
    after = out["extra"]["after"]
    assert not any(line.startswith("review-link: 17-叙事.Rmd 读取 panel.csv ") for line in after)
    assert "btn btn-small: 定为确认" in after
    assert ": 已记录：仍然成立：17-叙事.Rmd 读取 panel.csv" in after and "err-detail-toggle: 撤销" in after
    assert out["extra"]["note"] == [["", None]]  # the conflict's note field, nothing half typed left


@needs_node
def test_14_6_judgments_handled_in_the_panel_read_in_english():
    """The same in English: the part, the judgments, the acts, what was
    recorded and “Undo” -- no Chinese but the researcher's own."""
    steps = _JUDGE % {k: json.dumps(v) for k, v in {
        "a16": A16, "review": "Review", "note": "Note (optional)", "holds": "Still holds", "undo": "Undo"}.items()}
    out = _run(_reviewing("en"), steps, lang="en")
    first, after = out["extra"]["first"], out["extra"]["after"]
    assert any(line.startswith("review-link: 17-叙事.Rmd reads panel.csv (title") for line in first)
    for line in ["node-sec-title: Needs review", "review-reason: The basis changed",
                 "btn btn-small: Still holds", "btn btn-small: Settle as confirmed", "form-input:  =  (Note (optional))"]:
        assert line in first, line
    assert ": Recorded: still holds: 17-叙事.Rmd reads panel.csv" in after and "err-detail-toggle: Undo" in after
    assert _chinese(first + after) == [] and not any(FORBIDDEN.search(line) for line in first + after)
    assert [p[1]["verdict"] for p in out["posted"]] == ["confirmed", "undone"]


# -- what changed since the last visit (14.5) ----------------------------------------------------------------


def test_14_5_a_cards_changes_name_its_row_and_its_files_newest_first(tmp_path, monkeypatch):
    """After the project was added, step 16's script gains a line and #16's
    verdict moves: the card lists both, newest first, each with when, the
    file with its path; at most CHANGED_LISTED, the rest counted."""
    root = _project(tmp_path / "proj")
    _backdate(root)
    (root / "steps" / "16-build.py").write_text((root / "steps" / "16-build.py").read_text() + "df = df.dropna()\n")
    (root / "data" / "panel.csv").write_text(_csv(97, ("id", "x", "log_ret")))
    (root / "map.md").write_text(_map("☠️ 伪协整"))
    _scan(root)
    card = _card(_desk(root), "16")
    assert sorted((c["node"], c["path"]) for c in card["changed"]) == [(A16, None), ("script:steps/16-build.py", "steps/16-build.py")]
    times = [desk._parse(c["at"]) for c in card["changed"]]
    assert times == sorted(times, reverse=True) and card["changed"][0]["at"] == card["changed_at"]
    assert card["changed_more"] == 0
    monkeypatch.setattr(desk, "CHANGED_LISTED", 1)
    card = _card(_desk(root), "16")
    assert len(card["changed"]) == 1 and card["changed_more"] == 1


def _changed(lang: str) -> dict:
    """#16 changed an hour ago (its row: 待定 to 已放弃, a new result) and a minute ago
    (its script); a data file a month ago, before the visit (none is
    remembered: the last seven days)."""
    case = _case("single", lang)
    now = datetime.now(timezone.utc)
    at = {k: (now - d).isoformat(timespec="seconds") for k, d in
          {"script": timedelta(minutes=1), "row": timedelta(hours=1), "old": timedelta(days=30)}.items()}
    a = case["desk"]["attempts"][0]
    a.update(changed_at=at["script"], changed_more=0, changed=[
        {"node": "script:steps/fit.py", "path": "steps/fit.py", "at": at["script"]},
        {"node": A16, "path": None, "at": at["row"]}, {"node": "dataset:data/old.csv", "path": "data/old.csv", "at": at["old"]}])
    who = "Original researcher" if lang == "en" else "最初的研究者"
    case["get"]["/api/history?node=" + A16.replace(":", "%3A").replace("#", "%23")] = {"kind": "attempt", "versions": [
        {"at": at["row"], "verdict": "☠️ 伪协整", "verdict_status": {"status": "abandoned", "label": "已放弃", "words": "伪协整"},
         "result": "t=3.10", "who_label": who, "source": "scan"},
        {"at": at["old"], "verdict": "🕒", "verdict_status": {"status": "pending", "label": "待定", "words": ""},
         "result": "t=2.91", "who_label": who, "source": "scan"}]}
    return case


_TIMELINE = """
  await window.RODesk.openAttempt(%(a16)s);
  global.extra = panelLines();
  find("steps/fit.py").click();
  answers.length = 0;
  await window.RODesk.openAttempt(%(a17)s);
""" % {"a16": json.dumps(A16), "a17": json.dumps(A17)}


@needs_node
def test_14_5_the_panel_says_what_changed_since_the_last_visit():
    """#16 is 「有改动」: its panel says what -- the script, which opens its
    page, then the row's verdict as it moved, who and how --, newest first;
    the data file changed before the visit is not listed. #17, unchanged,
    has no such part."""
    case = _changed("zh")
    a17 = A17.replace(":", "%3A").replace("#", "%23")
    case["get"][f"/api/node?node={a17}"] = {**case["get"]["/api/node?node=" + A16.replace(":", "%3A").replace("#", "%23")], "id": A17}
    case["get"][f"/api/notes?node={a17}"] = {"notes": []}
    out = _run(case, _TIMELINE)
    lines = out["extra"]
    at = lines.index("node-sec-title: 你上次查看以来的改动")
    shown = [line for line in lines[at + 1:at + 7] if not line.startswith("tl-when")]
    assert shown == ["desk-link mono: steps/fit.py (title: 打开它的详情页)", "tl-meta: 文件有改动",
                     "tl-what: 研究判断从「待定」改为「已放弃」，结果改为：t=3.10", "tl-meta: 最初的研究者 · 扫描时记下"]
    assert sum(line.startswith("tl-when: ") for line in lines) == 2 and not any("old.csv" in line for line in lines)
    assert lines.index("dp-mark: 有改动") < at < lines.index("node-sec-title: 写备注")
    assert out["opened"] == ["script:steps/fit.py"]
    assert "node-sec-title: 你上次查看以来的改动" not in out["lines"]  # #17's panel


@needs_node
def test_14_6_what_changed_reads_in_english_and_says_what_is_left_out():
    """English: the part's 13.4 title, the file and the row's move in words
    (no arrows); twenty listed and all new, the earlier ones counted."""
    case = _changed("en")
    case["desk"]["attempts"][0].update(changed=case["desk"]["attempts"][0]["changed"][:2], changed_more=3)
    out = _run(case, "await window.RODesk.openAttempt(%s);" % json.dumps(A16), lang="en")
    lines = out["lines"]
    for line in ["node-sec-title: Changes since you last looked", "desk-link mono: steps/fit.py (title: Open its details page)",
                 "tl-meta: File changed", "tl-what: Research assessment changed from “Pending” to “Abandoned”; new result: t=3.10",
                 "tl-meta: Original researcher · Recorded at a scan", "desk-empty: 3 files changed earlier are not listed here."]:
        assert line in lines, line
    assert _chinese(lines) == [] and not any(FORBIDDEN.search(line) for line in lines)


# -- 「知道了」 (ruled 2026-10-13) -----------------------------------------------------------------------------


def _returned(lang: str = "zh") -> dict:
    case = _case("member", lang)
    returned = {"id": "p3", "kind": "verdict_change", "state": "returned", "by": "li@example.com", "at": "2024-03-01T10:00:00",
                "sentence": "放弃 16", "reason": "证据不够", "touches": [A16], "outcomes": [{"act": "returned", "text": "证据不够"}],
                "verdict": {"number": "16", "old_status": {"status": "pending", "label": "待定"},
                            "new_status": {"status": "abandoned", "label": "已放弃"}, "abandons": True}}
    pending = {**returned, "id": "p4", "state": "pending", "sentence": "16 改回", "reason": None, "outcomes": []}
    case["desk"]["column"]["mine"] = [returned, pending]
    return case


@needs_node
def test_14_4_a_returned_proposal_stays_in_my_proposals_until_got_it():
    """A member's returned proposal stays in 我的提议 with its reason, and
    in #16's 待办, until 「知道了」: then it leaves both, remembered for
    this person and project in the browser; the pending one stays. Opened
    again (a new page), it is still put away."""
    steps = """
      await window.RODesk.openAttempt(%(a16)s);
      global.extra = { before: columnLines(), panel: panelLines() };
      views.desk.all().find((k) => k.tag === "BUTTON" && k.kids.join("") === "知道了").click();
      for (let i = 0; i < 6; i++) await settle();
      global.extra.after = columnLines();
    """ % {"a16": json.dumps(A16)}
    out = _run(_returned(), steps)
    before, after, panel = out["extra"]["before"], out["extra"]["after"], out["extra"]["panel"]
    assert "desk-meta: 已退回：证据不够 · 2024年3月1日" in before and before.count("btn btn-small: 知道了") == 1
    assert panel.count("desk-what: 放弃 16") == 1 and "desk-what: 16 改回" in panel
    assert not any("放弃 16" in line or "知道了" in line for line in after) and "desk-link strong: 16 改回" in after
    assert not any("放弃 16" in line for line in out["lines"]) and "desk-what: 16 改回" in out["lines"]
    key = "ro.desk.gotit:p-1:li@example.com"
    assert json.loads(out["stored"][key]) == ["p3"]
    again = _run(_returned("en"), "", lang="en", storage={key: out["stored"][key]})
    assert not any("Got it" in line or "放弃 16" in line for line in again["column"])
    shown = _run(_returned("en"), "", lang="en")["column"]
    assert "btn btn-small: Got it" in shown and "desk-meta: Returned: 证据不够 · 1 Mar 2024" in shown

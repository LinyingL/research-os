"""Research OS V16, task B2-2 (DESIGN.md 21.4, 'Below'): the pages.

项目首页 reads from the top: 现在在做什么, 研究问题与计划 (its first paragraph, 「展开」 for the rest), 笔记 and 报告 in a wide column beside
the open document (the reports under the attempt each belongs to, newest attempt activity first, 「未归入尝试」 last, each row a
title as written and apart from it a quiet date), 全部任务 (grouped the same way, each row saying its owner and its state in words
without being opened). The pages run under node on the stand-in DOM of the home's other tests, fed what the real engine answers
for Quant after the grouping (18 agent tasks, every one handed back a report) and for a one-person thesis project, in both
languages. Every test names its scenario."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import pytest

from researchos import workspace
from test_v16_home_below import LONG, quant_eighteen, retitle
from test_v16_now_doing import A, _now, _with_table, me, pid, root  # noqa: F401 -- fixtures
from test_v16_now_doing_js import _run
from test_v8_phase2 import _project
from test_v9_language import CJK, needs_node
from test_workspace_js import WEBAPP, run

PLAN = ("# 研究问题与计划\n\n第一段：Sword 因子在全样本里是否仍有 Alpha？\n这一段有两行。\n\n第二段：先复算，再做稳健性检验。\n\n## 路线\n\n- v0\n- v0.2\n")
MEETING = "组会纪要（2026-03-12）"


def local_day(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def at_noon(path: Path, day: str) -> None:
    """The file last changed at noon of `day` on this Mac's calendar."""
    stamp = datetime.strptime(day + " 12:00", "%Y-%m-%d %H:%M").timestamp()
    os.utime(path, (stamp, stamp))


def write(root: Path, rel: str, text: str, day: str | None = None) -> str:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")
    if day:
        at_noon(root / rel, day)
    return rel


@pytest.fixture
def quant(root, pid):
    """Quant: 18 handed-back tasks (the Sword attempts and the seven not grouped yet), the researcher's own plan, three notes (one
    named for its day) and two reports by hand (one linked to the settled baseline, one to none), reports titled as the researcher
    wrote them, a date and all."""
    reports = quant_eighteen(root, pid)
    retitle(root, reports[7], LONG)
    retitle(root, reports[12], "Sword v0.2 全成员复算：夏普 1.82，样本外 0.9（2026-10-26）")
    retitle(root, reports[14], "Sword v0.3 稳健性检验")
    retitle(root, reports[1], "已有研究梳理与 v0 路线")
    write(root, "research/overview.md", PLAN, "2026-10-01")
    write(root, "research/notes/2026-03-12-meeting.md", f"# {MEETING}\n\nNotes.\n", "2026-10-28")   # named for a day, changed later
    write(root, "research/notes/ideas.md", "# Ideas\n\nOne.\n", "2026-10-22")
    write(root, "research/notes/data-sources.md", "# 数据源（2026-10-26 更新）\n\nTwo.\n", "2026-10-26")
    linked = write(root, "research/results/" + "e" * 32 + ".md", "# Settled baseline recheck\n\nBody.\n", "2026-10-27")
    write(root, linked.replace(".md", ".context.json"), json.dumps({"version": 1, "context": {"attempts": [A[9]]}}))
    write(root, "research/results/" + "f" * 32 + ".md", "# Loose end\n\nBody.\n", "2026-10-29")
    return {"root": root, "reports": reports, "linked": linked}


def staged(root: Path, *, lang: str = "zh") -> tuple[dict, str]:
    """What the engine answers for the project (`/api/workspace/now`, `/api/workspace` and each document), as the page's prelude."""
    now = _now(root, lang=lang)
    listing = workspace.overview(root, lang)
    docs = {d["path"]: workspace.document(root, d["path"], lang) for d in listing["documents"]}
    listed = {**listing, "project": "/projects/study", "id": "p-study", "read_only": False, "mode": "single"}
    return now, f"listed = {json.dumps(listed, ensure_ascii=False)};\ndocs = {json.dumps(docs, ensure_ascii=False)};\n"


def page(root: Path, scenario: str, *, lang: str = "zh", before: str = "") -> dict:
    now, prelude = staged(root, lang=lang)
    return _run(now, scenario, lang=lang, extra=prelude + before)


_LOAD = r"""
const rows = () => cls('ws-document-row');
const rowOf = (title) => rows().find((r) => text('ws-document', r)[0] === title);
const groupOf = (title) => cls('ws-document-group').find((g) => text('ws-group-title', g)[0] === title);
await window.ROWorkspace.load(root); await sleepTick(); await sleepTick();
"""


# -- the page reads from the top --------------------------------------------------------------------------------------


@needs_node
def test_the_page_reads_from_the_top_now_then_the_plan_then_the_notes_and_reports_beside_the_open_document_then_all_tasks(quant):
    out = page(quant["root"], _LOAD + r"""
const pageNode = cls('ws-page')[0];
const columns = cls('ws-columns')[0];
console.log(JSON.stringify({ top: pageNode.children.map((c) => c.className), columns: columns.children.map((c) => c.className),
  plan: text('ws-plan-title'), now: text('ws-now-title'), tasks: text('ws-task-title'), groups: text('ws-group-title'),
  inColumns: columns.all().some((e) => e.classList.contains('ws-editor-host')) }));
""")
    assert out["top"] == ["ws-notices", "ws-heading", "ws-now", "ws-plan", "ws-columns", "ws-tasks"]
    assert out["columns"] == ["ws-documents", "ws-document-slot"]       # the lists beside the open document
    assert out["now"] == ["现在在做什么"] and out["plan"] == ["研究问题与计划"] and out["tasks"] == ["全部任务"]     # 21.4, 21.9 item 1: the section is 全部任务
    assert out["groups"] == ["笔记", "报告"]                             # the plan is no longer one of the lists


def test_at_1440_wide_the_lists_column_is_wide_enough_for_a_title_of_two_lines_and_nothing_stacks_the_page():
    """No browser here: the stylesheet is read for the rules that apply at 1440 (the base ones: every `max-width` query is for a
    narrower window), and a title of the acceptance case is set against the column that is left once its date and padding are
    taken, at the width the page's own font gives a Chinese character (.84rem) and a Latin one (about half of it)."""
    css = (WEBAPP / "workspace.css").read_text()
    applying = re.sub(r"@media \(max-width: (\d+)px\) \{.*?\n\}\n", lambda m: "" if int(m.group(1)) < 1440 else m.group(0), css, flags=re.S)
    grid = re.search(r"\.ws-columns \{[^}]*grid-template-columns: minmax\((\d+)rem, (\d+)rem\) minmax\((\d+)rem, 1fr\)", applying)
    assert grid, "the lists column is a range of rem wide beside the open document, which keeps a width of its own"
    least, most, editor = int(grid.group(1)), int(grid.group(2)), int(grid.group(3))
    assert most >= 22 and least >= 14 and editor >= 24 and "display: block" not in re.search(r"\.ws-columns \{[^}]*\}", applying).group(0)
    stacks = re.search(r"@media \(max-width: (\d+)px\) \{\s*/\*.*?\*/\s*\.ws-columns \{ display: block; \}", css, re.S)
    assert stacks and int(stacks.group(1)) == 960     # the width the desk stacks at: the lists go above the open document
    rem = 16
    column = most * rem - 1 - 1 * rem                 # the column, its border and the padding after it
    room = column - 2 - (0.3 + 0.55) * rem - (0.6 * rem + 11 * rem * 0.72 * 0.62 + 0.55 * rem)   # border, the title's padding, the date and its gaps
    width = sum(0.84 * rem if ord(c) > 0x2e80 else 0.84 * rem * 0.52 for c in LONG)
    assert width / room <= 3, (width, room)             # three lines at most; the title is never cut
    title_rule = re.search(r"\.ws-document \{([^}]*)\}", css).group(1)
    assert not re.search(r"text-overflow|-webkit-line-clamp|white-space: nowrap|overflow: hidden", title_rule)     # a title wraps, whole


# -- 研究问题与计划 -----------------------------------------------------------------------------------------------------------


@needs_node
def test_the_plan_is_folded_to_its_first_paragraph_and_unfolds_into_the_writing_area_where_it_is_edited_as_before():
    out = run(r"""
docs['research/overview.md']={path:'research/overview.md',kind:'overview',title:'研究问题与计划',content:PLAN,revision:'doc-0'};
await window.ROWorkspace.load(root);await sleepTick();
const more=byClass('ws-plan-more'),lead=()=>byClass('ws-plan-lead'),within=(n,top)=>{for(let x=n;x;x=x.parentNode)if(x===top)return true;return false;};
const state=()=>({open:!!more.open,lead:lead().textContent,leadHidden:lead().classList.contains('hidden'),word:byClass('ws-plan-summary').textContent.replace('尚未保存',''),
  chevron:byClass('ws-plan-summary').children.some(c=>c.classList.contains('res-chevron')),editorInPlan:within(byClass('ws-editor-host'),more),
  dirty:!byClass('ws-plan-dirty').classList.contains('hidden')});
const folded=state();
more.open=true;await more.emit('toggle');await sleepTick();
const unfolded=Object.assign(state(),{preview:byClass('ws-preview').textContent});
await find('编辑',byClass('ws-editor-host')).click();
typed(byClass('ws-writing'),'# 研究问题与计划\n\n改过的第一段。\n\n第二段。');
more.open=false;await more.emit('toggle');
const foldedDirty=state();
more.open=true;await more.emit('toggle');
await find('保存').click();await sleepTick();
more.open=false;await more.emit('toggle');
const saved=state();
console.log(JSON.stringify({folded,unfolded,foldedDirty,saved,posted:posted.filter(p=>p[0]==='/api/workspace/document')}));
""".replace("PLAN", json.dumps(PLAN, ensure_ascii=False)))
    assert out["folded"] == {"open": False, "lead": "第一段：Sword 因子在全样本里是否仍有 Alpha？这一段有两行。", "leadHidden": False, "word": "展开",
                             "chevron": True, "editorInPlan": True, "dirty": False}
    assert "第二段" not in out["folded"]["lead"]
    assert out["unfolded"]["open"] and out["unfolded"]["leadHidden"] and out["unfolded"]["word"] == "收起" and out["unfolded"]["editorInPlan"]
    assert "第二段：先复算，再做稳健性检验。" in out["unfolded"]["preview"] and "v0.2" in out["unfolded"]["preview"]   # all of it
    assert out["foldedDirty"]["open"] is False and out["foldedDirty"]["dirty"] is True and out["foldedDirty"]["lead"] == "改过的第一段。"
    assert out["saved"]["dirty"] is False and out["saved"]["lead"] == "改过的第一段。"
    (post,) = out["posted"]
    assert post[1]["path"] == "research/overview.md" and post[1]["kind"] == "overview" and post[1]["content"].startswith("# 研究问题与计划\n\n改过的第一段。")


@needs_node
def test_opening_a_note_folds_the_plan_and_the_writing_area_leaves_it_and_unfolding_asks_first_while_the_note_holds_unsaved_words():
    out = run(r"""
listed.documents.push({path:'research/notes/a.md',kind:'note',title:'A note',excerpt:'',modified:'2026-10-22T12:00:00+00:00'});
docs['research/notes/a.md']={path:'research/notes/a.md',kind:'note',title:'A note',content:'# A note\n\nWords.',revision:'n-0'};
docs['research/overview.md']={path:'research/overview.md',kind:'overview',title:'Plan',content:'# Plan\n\nThe first paragraph.\n\nThe rest.',revision:'doc-0'};
await window.ROWorkspace.load(root);await sleepTick();
const more=byClass('ws-plan-more'),within=(n,top)=>{for(let x=n;x;x=x.parentNode)if(x===top)return true;return false;};
more.open=true;await more.emit('toggle');await sleepTick();
const planOpen={open:!!more.open,editorInPlan:within(byClass('ws-editor-host'),more),hint:!byClass('ws-slot-hint').classList.contains('hidden')};
await find('A note').click();await sleepTick();
const note={open:!!more.open,editorInPlan:within(byClass('ws-editor-host'),more),inSlot:within(byClass('ws-editor-host'),byClass('ws-document-slot')),
  hint:!byClass('ws-slot-hint').classList.contains('hidden'),lead:byClass('ws-plan-lead').textContent,word:byClass('ws-plan-summary').textContent.replace('尚未保存',''),writing:byClass('ws-preview').textContent};
await editDocument();typed(byClass('ws-writing'),'# A note\n\nUnsaved words.');
answers.push(false);
more.open=true;await more.emit('toggle');await sleepTick();
const refused={open:!!more.open,writing:byClass('ws-writing').value,asked:confirmQuestions.length,editorInPlan:within(byClass('ws-editor-host'),more)};
answers.push(true);
more.open=true;await more.emit('toggle');await sleepTick();
const agreed={open:!!more.open,editorInPlan:within(byClass('ws-editor-host'),more),preview:byClass('ws-preview').textContent};
console.log(JSON.stringify({planOpen,note,refused,agreed}));
""")
    assert out["planOpen"] == {"open": True, "editorInPlan": True, "hint": True}
    assert out["note"] == {"open": False, "editorInPlan": False, "inSlot": True, "hint": False, "lead": "The first paragraph.", "word": "展开", "writing": "A noteWords."}
    assert out["refused"] == {"open": False, "writing": "# A note\n\nUnsaved words.", "asked": 1, "editorInPlan": False}   # stays folded, nothing lost
    assert out["agreed"]["open"] is True and out["agreed"]["editorInPlan"] is True and "The rest." in out["agreed"]["preview"]


@needs_node
def test_a_plan_with_no_words_yet_starts_unfolded_to_be_written_in_and_a_plan_changed_elsewhere_follows_its_file_under_an_open_note():
    out = run(r"""
listed.documents.push({path:'research/notes/a.md',kind:'note',title:'A note',excerpt:'',modified:'2026-10-22T12:00:00+00:00'});
docs['research/notes/a.md']={path:'research/notes/a.md',kind:'note',title:'A note',content:'# A note\n\nWords.',revision:'n-0'};
docs['research/overview.md']={path:'research/overview.md',kind:'overview',title:'研究问题与计划',content:'',revision:null};
listed.documents[0].modified=null;
await window.ROWorkspace.load(root);await sleepTick();
const more=byClass('ws-plan-more');
const empty={open:!!more.open,placeholder:byClass('ws-writing').placeholder};
await find('A note').click();await sleepTick();
const lead1=byClass('ws-plan-lead').textContent;
listed.documents[0].modified='2026-10-30T10:00:00+00:00';
docs['research/overview.md']={path:'research/overview.md',kind:'overview',title:'Plan',content:'# Plan\n\nWritten by someone else.',revision:'doc-1'};
await window.ROWorkspace.load(root,true);await sleepTick();
console.log(JSON.stringify({empty,lead1,lead2:byClass('ws-plan-lead').textContent,reads:gets.filter(g=>g.includes('overview.md')).length}));
""")
    assert out["empty"] == {"open": True, "placeholder": "研究问题、思路与计划"}
    assert out["lead1"] == "还没有研究方案。"
    assert out["lead2"] == "Written by someone else." and out["reads"] >= 2


# -- 笔记 and 报告 ------------------------------------------------------------------------------------------------------------


@needs_node
def test_notes_are_one_list_by_date_and_each_row_has_its_title_as_written_and_its_date_apart(quant):
    root = quant["root"]
    out = page(root, _LOAD + r"""
const notes = groupOf('笔记'), rowsIn = (g) => cls('ws-document-row', g);
console.log(JSON.stringify({ rows: rowsIn(notes).map((r) => ({ title: text('ws-document', r)[0], date: text('ws-document-date', r)[0] || null,
    kids: r.children.map((c) => c.className), button: r.children[0].tagName, whole: r.textContent })),
  headings: cls('ws-document-attempt', notes).length }));
""")
    # newest day first; the note named for a March day is dated by that day, not by the day its file changed (the 28th)
    assert [(r["title"], r["date"]) for r in out["rows"]] == [
        ("数据源（2026-10-26 更新）", "2026-10-26"), ("Ideas", "2026-10-22"), (MEETING, "2026-03-12")]
    by = {r["title"]: r for r in out["rows"]}
    # the title is a button that says only the title as written (a date in it stays in it); the date is another element beside it
    assert all(r["kids"] == ["ws-document", "ws-document-date"] and r["button"] == "BUTTON" for r in out["rows"])
    assert by["数据源（2026-10-26 更新）"]["whole"] == "数据源（2026-10-26 更新）2026-10-26"
    assert out["headings"] == 0                          # notes stay one list


@needs_node
def test_reports_stand_under_the_attempt_each_belongs_to_newest_attempt_activity_first_and_the_rest_under_not_in_an_attempt_last(quant):
    root, reports = quant["root"], quant["reports"]
    out = page(root, _LOAD + r"""
const group = groupOf('报告');
const parts = []; let current = null;
group.children.forEach((c) => {
  if (c.classList.contains('ws-document-attempt')) { current = { heading: c.textContent, rows: [] }; parts.push(current); }
  else if (c.classList.contains('ws-document-row')) { (current || (current = { heading: null, rows: [] }, parts.push(current), current)).rows.push([text('ws-document', c)[0], text('ws-document-date', c)[0]]); }
});
console.log(JSON.stringify({ parts, tags: cls('ws-document-attempt', group).map((h) => h.tagName) }));
""")
    heads = [p["heading"] for p in out["parts"]]
    assert heads == ["#5 Sword v0.2", "#6 Sword v0.3", "#4 Sword v0", "#9 Settled baseline", "未归入尝试"]    # activity order; the unplaced last
    by = {p["heading"]: p["rows"] for p in out["parts"]}
    titles = lambda head: [title for title, _day in by[head]]    # noqa: E731
    assert len(by["#5 Sword v0.2"]) == 5 and len(by["#6 Sword v0.3"]) == 1 and len(by["#4 Sword v0"]) == 5
    assert "Sword v0.2 全成员复算：夏普 1.82，样本外 0.9（2026-10-26）" in titles("#5 Sword v0.2")
    assert titles("#6 Sword v0.3") == ["Sword v0.3 稳健性检验"] and LONG in titles("#4 Sword v0")      # the title as written, a date inside it
    assert titles("#9 Settled baseline") == ["Settled baseline recheck"]                              # by its research context
    assert len(by["未归入尝试"]) == 8 and "Loose end" in titles("未归入尝试") and "已有研究梳理与 v0 路线" in titles("未归入尝试")   # 7 loose tasks' reports and one by hand
    assert set(out["tags"]) == {"H4"}
    # the date is the day its file last changed, a field of its own: a title that holds another date keeps it, and the field shows the file's own day
    for head, rows in by.items():
        assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) for title, day in rows), head
    assert dict(by["#4 Sword v0"])[LONG] == local_day(quant["root"] / reports[7])
    assert dict(by["未归入尝试"])["Loose end"] == "2026-10-29"
    # newest first inside an attempt
    days = [day for _title, day in by["#5 Sword v0.2"]]
    assert days == sorted(days, reverse=True)


@needs_node
def test_a_project_whose_reports_belong_to_no_attempt_lists_them_without_headings(tmp_path):
    root = _project(tmp_path / "thesis")
    rows = "".join(f"| {n} | 10-{n:02d} | Idea {n} | V | r{n} | {'✅' if n % 3 else '🕒'} |\n" for n in range(1, 25))
    _with_table(root, "# Map\n\n## Attempt timeline\n\n| # | Date | Path | Variables | Result | Verdict |\n|---|---|---|---|---|---|\n" + rows)
    write(root, "research/overview.md", "# 论文\n\n问题。\n", "2026-09-01")
    write(root, "research/results/" + "1" * 32 + ".md", "# 回归结果\n\nBody.\n", "2026-09-10")
    write(root, "research/results/" + "2" * 32 + ".md", "# 稳健性\n\nBody.\n", "2026-09-12")
    out = page(root, _LOAD + r"""
const group = groupOf('报告');
console.log(JSON.stringify({ rows: cls('ws-document-row', group).map((r) => text('ws-document', r)[0]), headings: cls('ws-document-attempt', group).length,
  taskHeadings: cls('ws-task-group').length, now: text('ws-now-none') }));
""")
    assert out["rows"] == ["稳健性", "回归结果"] and out["headings"] == 0 and out["taskHeadings"] == 0
    assert out["now"] == ["还没有挂着任务的尝试。在研究路线里打开一条尝试，选「在这条尝试下加任务」。"]


@needs_node
def test_opening_a_note_or_a_report_shows_it_as_before_and_the_deep_links_to_a_report_still_open_it(quant):
    root, reports = quant["root"], quant["reports"]
    out = page(root, _LOAD + r"""
const scrolled = []; Object.getPrototypeOf(root).scrollIntoView = function () { scrolled.push(this.className); };
const within = (n, top) => { for (let x = n; x; x = x.parentNode) if (x === top) return true; return false; };
await rowOf('Ideas').children[0].click(); await sleepTick();
const note = { preview: cls('ws-preview')[0].textContent, current: rowOf('Ideas').classList.contains('current'), aria: rowOf('Ideas').children[0].getAttribute('aria-current') };
await window.ROWorkspace.openReport(REPORT7); await sleepTick();
const report = { preview: cls('ws-preview')[0].textContent, inSlot: within(cls('ws-editor-host')[0], cls('ws-document-slot')[0]),
  current: rowOf(LONGTITLE).classList.contains('current'), scrolled, kind: text('ws-kind').join('|') };
console.log(JSON.stringify({ note, report }));
""".replace("REPORT7", json.dumps(reports[7])).replace("LONGTITLE", json.dumps(LONG, ensure_ascii=False)))
    assert out["note"] == {"preview": "IdeasOne.", "current": True, "aria": "page"}
    assert out["report"]["preview"] == f"{LONG}Body of the report." and out["report"]["inSlot"] and out["report"]["current"]
    assert out["report"]["scrolled"] == ["ws-editor-host"] and out["report"]["kind"] == "报告"


# -- 全部任务 ------------------------------------------------------------------------------------------------------------------


@needs_node
def test_all_tasks_stand_under_the_same_attempts_in_the_same_order_each_row_saying_its_owner_and_its_state_without_being_opened(quant):
    root = quant["root"]
    out = page(root, _LOAD + r"""
const list = cls('ws-task-list')[0];
const parts = []; let current = null;
list.children.forEach((c) => {
  if (c.classList.contains('ws-task-group')) { current = { heading: c.textContent, rows: [] }; parts.push(current); }
  else if (c.classList.contains('ws-task')) current.rows.push({ id: null, text: text('ws-task-text', c)[0], owner: text('ws-task-owner', c)[0] || null,
    state: text('status-label', cls('ws-task-facts', c)[0])[0] || null, tone: cls('status-label', cls('ws-task-facts', c)[0])[0].className,
    open: !!cls('ws-task-details', c)[0].open, body: buttonsIn(cls('ws-task-body', c)[0]) });
});
console.log(JSON.stringify({ parts, grouping: buttonsIn(cls('ws-tasks')[0]).filter((b) => b === '把这些任务归入尝试…').length }));
""")
    assert [p["heading"] for p in out["parts"]] == ["#5 Sword v0.2", "#6 Sword v0.3", "#4 Sword v0", "未归入尝试"]    # the reports' order
    sizes = {p["heading"]: len(p["rows"]) for p in out["parts"]}
    assert sizes == {"#5 Sword v0.2": 5, "#6 Sword v0.3": 1, "#4 Sword v0": 5, "未归入尝试": 7}
    rows = [row for p in out["parts"] for row in p["rows"]]
    assert all(r["owner"] == "Claude Code · sonnet" for r in rows)                  # the agent and its model
    assert all(r["open"] is False for r in rows)                                    # none is open: the row says it without being opened
    states = [r["state"] for r in rows]
    assert states.count("等你看") == 1 and states.count("已完成") == 17           # task 18's handback waits; every other task handed back and was opened
    assert all("打开结果" in r["body"] and "编辑" in r["body"] and "移除" in r["body"] for r in rows)    # the row's actions stay
    assert all("tone-olive" in r["tone"] or "tone-ochre" in r["tone"] for r in rows)       # 已完成 in olive, 等你看 in ochre: nothing here is wrong
    assert out["grouping"] == 2                                                     # in the header and under 「未归入尝试」, as before




@needs_node
def test_a_rows_outputs_are_drawn_in_the_row_which_is_shut_until_it_is_opened(quant):
    root = quant["root"]
    out = page(root, _LOAD + r"""
listed.task_outputs = { t12: { entries: [{ kind: 'figure', path: 'figures/fit.png', title: 'Fit', state: 'current', evidence: 'agent_declaration' }],
  error: false, filing: null, work_folder: null, work_files: null, work_files_more: false } };
await window.ROWorkspace.load(root, true); await sleepTick(); await sleepTick();
const row = () => cls('ws-task').find((r) => cls('ws-outputs', r).length);
const within = (n, top) => { for (let x = n; x; x = x.parentNode) if (x === top) return true; return false; };
const details = () => cls('ws-task-details', row())[0];
const before = { open: !!details().open, inRow: within(cls('ws-outputs', row())[0], details()), blocks: cls('ws-outputs').length, title: text('ws-outputs-title', row())[0] };
details().open = true; await details().emit('toggle'); await window.ROWorkspace.load(root, true); await sleepTick();
console.log(JSON.stringify({ before, after: { open: !!details().open, kept: cls('ws-outputs', row()).length } }));
""")
    assert out["before"] == {"open": False, "inRow": True, "blocks": 1, "title": "任务输出"}    # drawn in the row, which is shut: it shows when the row is opened
    assert out["after"] == {"open": True, "kept": 1}                                           # and stays open through the next draw


def _small(tasks: dict[str, str]) -> dict:
    """The engine's answer for a project of one attempt (#1) whose tasks read as `tasks` (id to state)."""
    attempt = "attempt:research/attempts.md#1"
    return {"role": "single", "needs": {"rows": [], "notices": [], "role": "single"}, "progress": {"attempts": [], "holding": 1},
            "home": {"attempts": [attempt], "tasks": tasks, "reports": {}}}


@needs_node
def test_a_run_that_starts_makes_the_engine_be_asked_again_so_the_row_says_running():
    out = _run(_small({"t1": "not_started"}), r"""
agentsInfo = agentFixture(); setRuns([]);
listed.tasks = [agentTask({ id: 't1', text: 'Run the model', attempt: 'attempt:research/attempts.md#1' })];
const chip = () => text('status-label', cls('ws-task-facts')[0])[0];
await window.ROWorkspace.load(root); await sleepTick(); await sleepTick();
const first = { chip: chip(), asked: nowAnswers };
NOW.home.tasks.t1 = 'running';                                // what the engine will read once the run is going
await find('运行').click(); await sleepTick(); await sleepTick(); await sleepTick();
console.log(JSON.stringify({ first, then: { chip: chip(), asked: nowAnswers } }));
""")
    assert out["first"]["chip"] == "未开始" and out["then"]["chip"] == "运行中"
    assert out["then"]["asked"] > out["first"]["asked"]


@needs_node
def test_a_runs_state_that_moves_while_the_page_polls_is_asked_for_and_a_poll_that_changes_nothing_asks_nothing():
    out = _run(_small({"t1": "running"}), r"""
agentsInfo = agentFixture();
listed.tasks = [agentTask({ id: 't1', text: 'Run the model', attempt: 'attempt:research/attempts.md#1' })];
setRuns([mkRun({ state: 'running', can_stop: true })]);
const chip = () => text('status-label', cls('ws-task-facts')[0])[0];
await window.ROWorkspace.load(root); await sleepTick(); await sleepTick();
const asked0 = nowAnswers, first = chip();
await tick(); await tick();                                    // two polls, the run unchanged
const still = nowAnswers;
NOW.home.tasks.t1 = 'needs_answer';
setRuns([mkRun({ state: 'needs_input', question: 'Which?', can_reply: true })]);
await tick();                                                  // the run now waits for an answer
await sleepTick();
console.log(JSON.stringify({ first, asked0, still, then: chip(), asked: nowAnswers }));
""")
    assert out["first"] == "运行中" and out["still"] == out["asked0"]
    assert out["then"] == "等你回答" and out["asked"] > out["still"]


@needs_node
def test_without_the_engines_arrangement_the_lists_still_draw_in_the_tables_order_with_the_owners_and_no_states(quant):
    root = quant["root"]
    out = page(root, _LOAD + r"""
console.log(JSON.stringify({ heads: text('ws-task-group'), reportHeads: cls('ws-document-attempt').length,
  reports: cls('ws-document-row', groupOf('报告')).length, chips: cls('status-label', cls('ws-task-list')[0]).length,
  owners: text('ws-task-owner').length }));
""", before=r"""
const earlier = customGet;
customGet = (path) => { if (path === '/api/workspace/now') throw new Error('engine busy'); return earlier(path); };
""")
    assert out["heads"] == ["#4 Sword v0", "#5 Sword v0.2", "#6 Sword v0.3", "未归入尝试"]      # the table's order, as before
    assert out["reportHeads"] == 0 and out["reports"] == 20 and out["chips"] == 0 and out["owners"] == 18


# -- English -------------------------------------------------------------------------------------------------------------------


@needs_node
def test_in_english_the_headings_and_the_states_are_english_and_what_the_researcher_wrote_stays_as_they_wrote_it(quant):
    root = quant["root"]
    out = page(root, _LOAD + r"""
const group = groupOf('Reports'), list = cls('ws-task-list')[0];
console.log(JSON.stringify({ groups: text('ws-group-title'), plan: text('ws-plan-title'), word: text('ws-plan-summary'),
  heads: cls('ws-document-attempt', group).map((h) => h.textContent), taskHeads: text('ws-task-group'), taskTitle: text('ws-task-title'),
  states: [...new Set(text('status-label', list))], owner: text('ws-task-owner')[0], titles: cls('ws-document', group).map((b) => b.textContent),
  notes: cls('ws-document', groupOf('Notes')).map((b) => b.textContent), hint: text('ws-slot-hint').join('') }));
""", lang="en")
    assert out["taskTitle"] == ["All tasks"]
    assert out["groups"] == ["Notes", "Reports"] and out["plan"] == ["Research question and plan"] and out["word"][0].startswith("Show more")
    assert out["heads"] == ["#5 Sword v0.2", "#6 Sword v0.3", "#4 Sword v0", "#9 Settled baseline", "Not in an attempt"]
    assert out["taskHeads"] == ["#5 Sword v0.2", "#6 Sword v0.3", "#4 Sword v0", "Not in an attempt"]
    assert sorted(out["states"]) == ["Done", "To read"] and out["owner"] == "Claude Code · sonnet"
    assert LONG in out["titles"] and "数据源（2026-10-26 更新）" in out["notes"]       # what the researcher wrote stays as they wrote it
    assert not CJK.search(out["hint"]) and out["hint"] == "Choose a note or a report to read, or start a new one."


@needs_node
def test_an_empty_list_of_reports_says_so_in_both_languages(tmp_path):
    root = _project(tmp_path / "bare")
    zh = page(root, _LOAD + r"""console.log(JSON.stringify({ empty: text('ws-empty', groupOf('报告')), plan: text('ws-plan-lead') }));""")
    en = page(root, _LOAD + r"""console.log(JSON.stringify({ empty: text('ws-empty', groupOf('Reports')) }));""", lang="en")
    assert zh["empty"] == ["还没有报告。"] and en["empty"] == ["No reports yet."]


STATES = {"not_started": ("未开始", "Not started", "gray"), "running": ("运行中", "Running", "ochre"), "needs_answer": ("等你回答", "Waiting for your answer", "ochre"),
          "to_read": ("等你看", "To read", "ochre"), "done": ("已完成", "Done", "olive"), "failed": ("失败", "Failed", "clay"),
          "out_of_bounds": ("越过了限制", "Went out of bounds", "clay")}


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_every_state_the_engine_names_is_said_in_words_and_in_its_tone_beside_the_owner_of_its_row(lang):
    """The seven codes of `attempt_tasks.state_of`, one row each: the words the attempt's panel uses (21.1), olive for what is
    done, ochre for what waits for the person or goes on, gray for what has not begun, clay for what went wrong (21.8)."""
    codes = list(STATES)
    out = _run(_small({f"t{i}": code for i, code in enumerate(codes)}), r"""
listed.tasks = CODES.map((code, i) => ({ id: 't' + i, text: 'Task ' + i, owner: i % 2 ? 'Lin' : 'agent:claude-code:sonnet', due: '', done: false, attempt: 'attempt:research/attempts.md#1' }));
await window.ROWorkspace.load(root); await sleepTick(); await sleepTick();
console.log(JSON.stringify(cls('ws-task').map((row) => ({ owner: text('ws-task-owner', row)[0], state: text('status-label', cls('ws-task-facts', row)[0])[0],
  tone: cls('status-label', cls('ws-task-facts', row)[0])[0].className.split(' ').pop() }))));
""".replace("CODES", json.dumps(codes)), lang=lang)
    column = 0 if lang == "zh" else 1
    assert [(row["state"], row["tone"]) for row in out] == [(STATES[code][column], "tone-" + STATES[code][2]) for code in codes]
    assert [row["owner"] for row in out] == [("Claude Code · sonnet" if i % 2 == 0 else "Lin") for i in range(len(codes))]

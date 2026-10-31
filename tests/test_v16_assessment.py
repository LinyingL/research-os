"""Research OS V16, task B1-4 (DESIGN.md 21.3 'A research assessment is a choice and a reason'): the engine's half, and the
page's reading and writing of a cell held to the engine's.

The assessment is a cell of the attempt table: the status's marker and the researcher's words. The statuses a project has
(the default words and any it named in [verdict_labels]) are listed in the order 21.3 gives, each with its marker or
markers, and ride with the records the form is drawn for; a cell is composed as the marker, a space and the reason, and the
page's chooser composes and reads it the same way. Every test names its scenario."""

from __future__ import annotations

import json

import pytest

from researchos import handbacks, research_records
from researchos.webapp import mapedit
from researchos.ingest import attempts as attempts_ingest

from test_v8_phase2 import _call
from test_v8_phase3 import _serve
from test_v9_language import WEBAPP, _node, needs_node
from test_v10_desk_panel import STATUSES
from test_v16_handback import A16, _context, hand_back, me, pid, root  # noqa: F401 -- fixtures of the handback engine tests

#: A project's own words: a new status (🧪), a second marker for 现行 (✔️), 待定 renamed 进行中 and a new 待定 (🔜).
LABELS = '\n[verdict_labels]\n"🧪" = "待重复"\n"✔️" = "现行"\n"🕒" = "进行中"\n"🔜" = "待定"\n'


def _with_labels(root) -> None:
    config = root / ".researchos" / "attempts.toml"
    config.write_text(config.read_text() + LABELS)


# -- the statuses ---------------------------------------------------------------------------------------------------------


def test_the_statuses_of_a_project_are_listed_in_the_order_of_21_3_with_their_markers_and_other_last(root):
    got = research_records.assessment_statuses(root)
    assert [item["status"] for item in got] == ["现行", "待定", "不利证据", "已放弃", "被否", "归档", "其它"]
    assert [item["marker"] for item in got] == ["✅", "🕒", "⚠️", "☠️", "⛔", "📋", ""]
    assert [item["abandons"] for item in got] == [False, False, False, True, False, False, False]
    assert got[-1] == {"status": "其它", "marker": "", "markers": [], "abandons": False, "other": True, "code": "other"}
    _with_labels(root)
    named = research_records.assessment_statuses(root)
    # 现行 and 进行中 (the renamed 🕒) keep their places; the researcher's own come after the defaults, in the order their file names them
    assert [item["status"] for item in named] == ["现行", "进行中", "不利证据", "已放弃", "被否", "归档", "待重复", "待定", "其它"]
    by_word = {item["status"]: item for item in named}
    assert by_word["现行"]["markers"] == ["✅", "✔️"] and by_word["现行"]["marker"] == "✅"     # every marker that names it, the first is written
    assert by_word["待重复"]["marker"] == "🧪" and by_word["待重复"]["code"] == "labelled" and by_word["待定"]["marker"] == "🔜"
    assert by_word["进行中"]["code"] == "labelled" and by_word["已放弃"]["code"] == "abandoned"


def test_the_panel_tests_stand_in_for_the_engines_own_list_of_a_default_project(root):
    """tests/test_v10_desk_panel.py draws the panel from `STATUSES`; it is what the engine answers for a project with no
    [verdict_labels]."""
    assert json.loads(json.dumps(STATUSES)) == research_records.assessment_statuses(root)


def test_the_records_the_form_is_drawn_for_carry_the_statuses_of_the_project(root):
    """GET /api/attempts answers the statuses with the attempts, in the project's own words once it named some, so that the
    chooser is drawn from the same read as the cell it shows (state.attemptStatuses)."""
    _with_labels(root)
    with _serve(root) as base:
        status, got = _call(f"{base}/api/attempts")
    assert status == 200 and got["statuses"] == research_records.assessment_statuses(root)
    assert [item["status"] for item in got["statuses"]][-1] == "其它" and "待重复" in [item["status"] for item in got["statuses"]]
    assert {a["id"] for a in got["attempts"]} == {A16, "attempt:map.md#17"}


# -- composing a cell -------------------------------------------------------------------------------------------------


def test_a_cell_is_the_marker_a_space_and_the_reason_and_a_status_with_no_marker_writes_the_reason_alone():
    assert attempts_ingest.compose_verdict("🕒", "活跃候选(全项目最新文件)") == "🕒 活跃候选(全项目最新文件)"
    assert attempts_ingest.compose_verdict("☠️", "") == "☠️"                       # no reason: the marker alone
    assert attempts_ingest.compose_verdict("☠️", "  伪协整 ") == "☠️ 伪协整"        # the reason as the researcher wrote it, trimmed
    assert attempts_ingest.compose_verdict("", "没有标记的话") == "没有标记的话"     # 其它: the words, as they are
    assert attempts_ingest.compose_verdict("", "") == ""
    assert attempts_ingest.compose_verdict("✅", "第一行\n第二行") == "✅ 第一行\n第二行"


@pytest.mark.parametrize("reason", ["", "Holds on every member", "第一行", "活跃候选(全项目最新文件)"])
def test_every_status_of_a_project_composes_to_a_cell_the_engine_reads_back_as_that_status_and_those_words(root, reason):
    """Each default marker and the researcher's own ([verdict_labels]): what the chooser writes is read by `verdict_status`
    (the table's reader, 12.11) as the status chosen with the reason typed; 其它 reads as 其它."""
    _with_labels(root)
    labels = attempts_ingest.verdict_labels(root)
    for item in research_records.assessment_statuses(root):
        cell = attempts_ingest.compose_verdict(item["marker"], reason)
        read = attempts_ingest.verdict_status(cell, labels)
        if item["other"]:
            assert cell == reason and read["status"] == "other"
        else:
            assert read["label"] == item["status"] and read["words"] == reason, (item["status"], cell)


def test_confirming_a_handback_with_each_status_writes_the_cell_the_chooser_composes(root, pid):
    """The handback's confirmation composes through the same function: for every status of the project the cell in the
    table is the marker and the reason (其它: the reason alone), the history and `assessment_confirmed` as before."""
    _with_labels(root)
    for number, item in enumerate(sorted(research_records.assessment_statuses(root), key=lambda s: s["abandons"])):   # the abandoning one last
        report = hand_back(root, pid, task_id=f"t{number}")
        reason = "没有标记的话" if item["other"] else "因为数据"
        body = {"report": report, "status": item["status"], "reason": reason}
        if item["abandons"]:
            body["abandon_reason"] = "数据到 2009 年为止"
        done = handbacks.confirm(root, body)
        assert done["cell"] == attempts_ingest.compose_verdict(item["marker"], reason), item["status"]
        written = attempts_ingest._clean_cell(mapedit.row_cells(root, "16")["verdict"])   # noqa: SLF001 -- the cell as the table now holds it
        assert written == done["cell"] and written.startswith(item["marker"]), item["status"]
        assert _context(root, report)["assessment_confirmed"]["status"] == item["status"]


# -- the page's reading and writing of a cell is the engine's -----------------------------------------------------------


def _desk_alone(steps: str):
    """desk.js evaluated alone under node (no /i18n.js: the Chinese), then `steps`."""
    return _node("global.window = {};\nglobal.document = { getElementById: () => null };\n"
                 + (WEBAPP / "desk.js").read_text(encoding="utf-8") + "\n" + steps)


#: Cells as a table holds them: every default marker, the researcher's own, a marker not leading, one with and without its
#: variation selector, two markers (the earliest counts), nothing known, a symbol nobody named, an empty cell, two lines.
CELLS = ["✅ 主线：TopicShift 显著，稳健性 3/4", "🕒", "🕒 **活跃候选**", "⚠️ 符号反了(β<0);需复核", "⚠ 少了选择器", "☠ 方向反了",
         "☠️ 伪协整;残差不平稳", "⛔ 审稿否决(R2)", "📋 归档：并入 #12", "先看 🕒", "**2** 轮以后 ✅", "  ✅  主线", "🧪 试跑", "🧪",
         "✔️ 第二个标记", "🔜 再看", "待补", "", "💀 方向反了", "🕒 第一行\n第二行", "✅ 和 ☠️ 都写了", "☠️ 先放弃 ✅ 后来又好了"]


@needs_node
def test_the_page_reads_a_cell_as_the_engine_does_and_a_cell_with_no_known_marker_is_other_with_all_of_it_as_the_reason(root):
    _with_labels(root)
    statuses, labels = research_records.assessment_statuses(root), attempts_ingest.verdict_labels(root)
    got = _desk_alone("process.stdout.write(JSON.stringify(%s.map((c) => window.RODesk.assessment.parse(c, %s))));\n"
                      % (json.dumps(CELLS, ensure_ascii=False), json.dumps(statuses, ensure_ascii=False)))
    for cell, read in zip(CELLS, got):
        engine = attempts_ingest.verdict_status(cell, labels)
        want = ("其它", cell) if engine["status"] == "other" else (engine["label"], engine["words"])
        assert (read["status"], read["reason"]) == want, repr(cell)
    assert {read["status"] for cell, read in zip(CELLS, got) if cell in ("待补", "", "💀 方向反了")} == {"其它"}
    assert got[CELLS.index("💀 方向反了")]["reason"] == "💀 方向反了"      # the symbol nobody named stays with the words
    assert got[CELLS.index("✔️ 第二个标记")]["marker"] == "✔️"             # the marker the cell wrote it with, kept to write it with again


@needs_node
@pytest.mark.parametrize("reason", ["", "Holds on every member", " 空白 ", "第一行\n第二行", "活跃候选(全项目最新文件)"])
def test_the_page_composes_a_cell_as_the_engine_does_for_every_status_of_the_project(root, reason):
    _with_labels(root)
    statuses = research_records.assessment_statuses(root)
    got = _desk_alone("process.stdout.write(JSON.stringify(%s.map((s) => window.RODesk.assessment.compose(s.status, %s, %s))));\n"
                      % (json.dumps(statuses, ensure_ascii=False), json.dumps(reason, ensure_ascii=False), json.dumps(statuses, ensure_ascii=False)))
    assert got == [attempts_ingest.compose_verdict(item["marker"], reason) for item in statuses]


@needs_node
def test_a_cell_the_person_changes_nothing_in_is_written_back_byte_for_byte_and_the_status_it_was_read_with_keeps_its_marker(root):
    """Reading a cell and writing it back with nothing changed is the cell itself, whatever its marker, spacing or lines;
    changing only the reason of a cell written with a second marker keeps that marker; another status takes its own."""
    _with_labels(root)
    statuses = research_records.assessment_statuses(root)
    out = _desk_alone("""
      const D = window.RODesk, statuses = %s, cells = %s;
      const doc = (tag) => ({ tag, className: "", dataset: {}, kids: [], style: {}, listeners: {}, value: "", disabled: false,
        classList: { add() {}, remove() {}, toggle() {}, contains: () => false }, attrs: {},
        setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; },
        addEventListener(type, f) { (this.listeners[type] = this.listeners[type] || []).push(f); },
        appendChild(c) { this.kids.push(c); return c; }, append(...cs) { cs.forEach((c) => this.kids.push(c)); }, focus() {}, set textContent(v) {} });
      global.document.createElement = doc;
      const same = cells.map((cell) => D.statusChooser({ statuses, cell }).cell() === cell);
      const second = D.statusChooser({ statuses, cell: "✔️ 第二个标记" });
      second.reasonEl.value = "改了理由"; const keptMarker = second.cell();
      const other = D.statusChooser({ statuses, cell: "✔️ 第二个标记" });
      other.picks.find((b) => b.dataset.status === "已放弃").listeners.click[0](); const changed = other.cell();
      process.stdout.write(JSON.stringify({ same, keptMarker, changed }));
    """ % (json.dumps(statuses, ensure_ascii=False), json.dumps(CELLS, ensure_ascii=False)))
    assert out["same"] == [True] * len(CELLS)
    assert out["keptMarker"] == "✔️ 改了理由" and out["changed"] == "☠️ 第二个标记"

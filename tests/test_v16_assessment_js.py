"""Research OS V16, task B1-4 (DESIGN.md 21.3 'A research assessment is a choice and a reason'): the pages.

The assessment is a status chosen from the project's -- the default words and any it named in [verdict_labels] -- and a
reason field holding the researcher's words; the chosen status is pressed, the cell's marker is in no field, and the cell
is written as the status's marker, a space and the reason. A cell the person changes nothing in is not written: it stays
byte for byte. The chooser is one component (RODesk.statusChooser) the side panel, the attempt form and the handback's
「改一下」 share. The new-attempt form numbers the row itself, calls the date 日期, folds the local model's draft below the
main fields and names the file it writes to only under 「详情」. desk.js and app.html's form run under node on the stand-in
DOM of the panel tests, in both languages. Every test names its scenario."""

from __future__ import annotations

import json

import pytest

from test_v9_language import WEBAPP, _i18n_source, _node, needs_node
from test_v10_desk_panel import _PANEL_DOM, A16, STATUSES, _case, _form_run, _run, _status


def _chooser_run(steps: str, lang: str | None = None, statuses=STATUSES):
    """desk.js on the panel tests' stand-in DOM, with `STATUSES` (a project's statuses) at hand; `steps` print what they found."""
    i18n = "" if lang is None else f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    script = ("global.window = {};\n" + i18n + _PANEL_DOM + (WEBAPP / "desk.js").read_text(encoding="utf-8")
              + f"\nconst STATUSES = {json.dumps(statuses, ensure_ascii=False)};\n"
              "const pressed = (c) => c.picks.filter((b) => b.getAttribute('aria-pressed') === 'true').map((b) => b.dataset.status);\n"
              "const names = (c) => c.picks.map((b) => b.textContent);\n"
              "(async () => {\n" + steps + "\n})().catch((e) => { console.error(e.stack); process.exit(1); });\n")
    return _node(script)


# -- the component ---------------------------------------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_21_3_the_chooser_offers_a_button_per_status_in_the_order_21_3_names_and_the_cells_own_status_is_pressed(lang):
    w = (lambda zh, en: en if lang == "en" else zh)
    out = _chooser_run("""
      const heard = [];
      const c = window.RODesk.statusChooser({ statuses: STATUSES, cell: "🕒 活跃候选(全项目最新文件)", label: "研究判断", onChange: (x) => heard.push([x.status(), x.reason()]) });
      const first = { names: names(c), pressed: pressed(c), aria: c.picks.map((b) => b.getAttribute("aria-pressed")), selected: c.picks.map((b) => b.cls.has("selected")),
                      status: c.status(), reason: c.reason(), hidden: c.statusEl.value, group: c.el.getAttribute("role") };
      c.picks.find((b) => b.dataset.status === "已放弃").click();
      const second = { pressed: pressed(c), status: c.statusEl.value, selected: c.picks.map((b) => b.cls.has("selected")), typed: c.statusEl.dataset.typed || null };
      c.reasonEl.value = "伪协整"; c.reasonEl.on.input.forEach((f) => f({}));
      process.stdout.write(JSON.stringify({ first, second, heard, reasonType: c.reasonEl.tag, caption: c.el.all().filter((k) => k.tag === "LABEL").map((k) => k.textContent) }));
    """, lang=None if lang == "zh" else lang)
    assert out["first"]["names"] == [w("现行", "Current"), w("待定", "Pending"), w("不利证据", "Adverse evidence"), w("已放弃", "Abandoned"),
                                     w("被否", "Rejected"), w("归档", "Archived"), w("其它", "Other")]
    assert out["first"]["pressed"] == ["待定"] and out["first"]["status"] == "待定"            # the word on disk, in either language (13.1)
    assert out["first"]["aria"] == ["false", "true", "false", "false", "false", "false", "false"]
    assert out["first"]["selected"] == [False, True, False, False, False, False, False]
    assert out["first"]["reason"] == "活跃候选(全项目最新文件)" and out["first"]["hidden"] == w("待定", "Pending") and out["first"]["group"] == "group"
    assert out["second"] == {"pressed": ["已放弃"], "status": w("已放弃", "Abandoned"), "typed": "1", "selected": [False, False, False, True, False, False, False]}
    assert out["heard"] == [["已放弃", "活跃候选(全项目最新文件)"], ["已放弃", "伪协整"]]   # told on each choice and each word typed
    assert out["reasonType"] == "TEXTAREA" and out["caption"] == [w("理由", "Reason")]


@needs_node
def test_21_3_the_cell_is_the_marker_a_space_and_the_reason_and_changing_nothing_writes_nothing():
    out = _chooser_run("""
      const c = window.RODesk.statusChooser({ statuses: STATUSES, cell: "🕒 活跃候选(全项目最新文件)" });
      const pick = (word) => c.picks.find((b) => b.dataset.status === word).click();
      const seen = { untouched: [c.cell(), c.changed(), c.shown()] };
      pick("已放弃"); seen.abandoned = [c.cell(), c.changed()];
      pick("现行"); c.reasonEl.value = "主线"; seen.reasoned = [c.cell(), c.changed()];
      c.reasonEl.value = ""; seen.noReason = [c.cell(), c.changed()];
      pick("待定"); c.reasonEl.value = "活跃候选(全项目最新文件)"; seen.backAgain = [c.cell(), c.changed()];
      c.reasonEl.value = "  活跃候选(全项目最新文件)  "; seen.spaced = [c.cell(), c.changed()];   // only spaces at the ends: the same cell
      c.reasonEl.value = ""; pick("现行"); c.markWritten(); seen.written =[c.cell(), c.changed(), c.shown(), pressed(c), c.reason(), c.statusEl.dataset.typed || null, c.statusEl.dataset.initial];
      c.set("☠ 方向反了"); seen.set = [pressed(c), c.reason(), c.cell(), c.shown()];
      process.stdout.write(JSON.stringify(seen));
    """)
    note = "🕒 活跃候选(全项目最新文件)"
    assert out["untouched"] == [note, False, note]
    assert out["abandoned"] == ["☠️ 活跃候选(全项目最新文件)", True]
    assert out["reasoned"] == ["✅ 主线", True] and out["noReason"] == ["✅", True]              # no reason: the marker alone
    assert out["backAgain"] == [note, False] and out["spaced"] == [note, False]                  # the cell it was read as, byte for byte
    assert out["written"] == ["✅", False, "✅", ["现行"], "", None, "现行"]                     # what is shown is now what the table holds
    assert out["set"] == [["已放弃"], "方向反了", "☠ 方向反了", "☠ 方向反了"]                  # read again from the cell (撤销), not rewritten


@needs_node
def test_21_3_a_cell_with_no_known_marker_is_other_with_all_of_it_as_the_reason_and_is_not_changed_unless_a_status_is_picked():
    out = _chooser_run("""
      const text = "待补：等 2008 年的数据（见 #12）";
      const c = window.RODesk.statusChooser({ statuses: STATUSES, cell: text });
      const first = { pressed: pressed(c), reason: c.reason(), cell: c.cell(), changed: c.changed() };
      c.reasonEl.value = "待补：等 2009 年的数据"; const reworded = [c.cell(), c.changed(), pressed(c)];
      c.picks.find((b) => b.dataset.status === "待定").click(); const pending = c.cell();
      c.picks.find((b) => b.dataset.status === "其它").click(); const other = c.cell();
      const blank = window.RODesk.statusChooser({ statuses: STATUSES, cell: "" });
      const symbol = window.RODesk.statusChooser({ statuses: STATUSES, cell: "💀 方向反了" });
      process.stdout.write(JSON.stringify({ first, reworded, pending, other, blank: [pressed(blank), blank.reason(), blank.cell(), blank.changed()],
                                            symbol: [pressed(symbol), symbol.reason(), symbol.cell()] }));
    """)
    text = "待补：等 2008 年的数据（见 #12）"
    assert out["first"] == {"pressed": ["其它"], "reason": text, "cell": text, "changed": False}
    assert out["reworded"] == ["待补：等 2009 年的数据", True, ["其它"]]       # words alone, no marker invented
    assert out["pending"] == "🕒 待补：等 2009 年的数据" and out["other"] == "待补：等 2009 年的数据"
    assert out["blank"] == [["其它"], "", "", False]
    assert out["symbol"] == [["其它"], "💀 方向反了", "💀 方向反了"]              # a symbol nobody named stays with the words


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_21_3_a_status_the_project_named_in_verdict_labels_is_a_button_as_written_and_writes_its_own_marker(lang):
    named = STATUSES[:-1] + [_status("待重复", "🧪", "labelled"), STATUSES[-1]]
    out = _chooser_run("""
      const c = window.RODesk.statusChooser({ statuses: STATUSES, cell: "🧪 换个样本再跑" });
      const first = { names: names(c), pressed: pressed(c), reason: c.reason() };
      c.picks.find((b) => b.dataset.status === "现行").click(); const current = c.cell();
      c.picks.find((b) => b.dataset.status === "待重复").click(); const back = [c.cell(), c.changed()];
      process.stdout.write(JSON.stringify({ first, current, back }));
    """, lang=None if lang == "zh" else lang, statuses=named)
    assert out["first"]["names"][-2:] == ["待重复", "Other" if lang == "en" else "其它"]   # the researcher's word as written, then 其它
    assert out["first"]["pressed"] == ["待重复"] and out["first"]["reason"] == "换个样本再跑"
    assert out["current"] == "✅ 换个样本再跑" and out["back"] == ["🧪 换个样本再跑", False]


@needs_node
def test_21_3_a_new_chooser_starts_as_given_nothing_pressed_for_an_unknown_status_and_other_when_none_is_named_and_a_locked_one_ignores_clicks():
    out = _chooser_run("""
      const made = (o) => window.RODesk.statusChooser(Object.assign({ statuses: STATUSES }, o));
      const given = made({ status: "待定", reason: "" }), unknown = made({ status: "成立", reason: "r" }), none = made({});
      given.reasonEl.value = "等数据";
      const c = made({ status: "待定", reason: "x" }); c.setDisabled(true);
      c.picks.find((b) => b.dataset.status === "现行").click(); const locked = [pressed(c), c.picks.every((b) => b.disabled), c.reasonEl.disabled];
      c.setDisabled(false); c.picks.find((b) => b.dataset.status === "现行").click();
      const bare = window.RODesk.statusChooser({});
      process.stdout.write(JSON.stringify({ given: [pressed(given), given.cell(), given.shown(), given.changed()], unknown: [pressed(unknown), unknown.status(), unknown.reason(), unknown.cell()],
                                            none: [pressed(none), none.cell()], locked, unlocked: pressed(c), bare: [names(bare), pressed(bare)] }));
    """)
    assert out["given"] == [["待定"], "🕒 等数据", None, True]       # a new row: always to be written
    assert out["unknown"] == [[], "", "r", "r"]                      # a status the project no longer has: none chosen, the words kept
    assert out["none"] == [["其它"], ""]
    assert out["locked"] == [["待定"], True, True] and out["unlocked"] == ["现行"]
    assert out["bare"] == [["其它"], ["其它"]]                        # a page given no statuses still reads and writes a cell as 其它


# -- the side panel ----------------------------------------------------------------------------------------------------------


@needs_node
def test_21_3_a_status_picked_in_the_panel_and_not_saved_is_guarded_like_typed_words_and_picking_the_old_one_again_is_not():
    steps = """
      await window.RODesk.openAttempt(%(id)s);
      const leave = () => { answers.push(false); return window.RODesk.mayLeavePanel(); };
      const pick = (word) => find(word, "BUTTON").click();
      const clean = window.RODesk.mayLeavePanel();
      pick("已放弃"); const picked = leave();
      pick("待定"); const back = window.RODesk.mayLeavePanel();
      const reason = panelPart("panel-body").all().find((k) => k.cls.has("status-reason"));
      type(reason, "先看一看"); const typed = leave();
      global.extra = { clean, picked, back, typed, asked: asked.slice() };
    """ % {"id": json.dumps(A16)}
    out = _run(_case("single"), steps)["extra"]
    question = "这里有还没写入的内容，离开它就不保存了。继续吗？"
    assert (out["clean"], out["picked"], out["back"], out["typed"]) == (True, False, True, False)
    assert out["asked"] == [question, question]


@needs_node
def test_21_3_a_cell_with_no_known_marker_in_the_panel_is_other_with_all_of_it_the_reason_and_changing_only_the_result_sends_no_verdict():
    case = _case("single")
    page = next(p for path, p in case["get"].items() if path.startswith("/api/node?"))
    page["details"].update(verdict="待补：等 2008 年的数据", verdict_status={"status": "other", "label": "其它", "words": "待补：等 2008 年的数据"})
    case["post"] = {"/api/attempts/preview": {"file": "map.md", "old_row": "a", "new_row": "b", "abandons": False, "reason_required": False,
                                              "cells": {"result": {"was": "t=2.91", "was_text": "t=2.91", "now_text": "t=3.10"}}}}
    steps = """
      await window.RODesk.openAttempt(%(id)s);
      const body = panelPart("panel-body");
      const shown = { chosen: body.all().filter((k) => k.cls.has("selected")).map((k) => k.dataset.status),
                      reason: body.all().find((k) => k.cls.has("status-reason")).value };
      const result = body.all().find((k) => k.tag === "TEXTAREA" && !k.placeholder && !k.cls.has("status-reason"));
      type(result, "t=3.10");
      find("保存", "BUTTON").click(); await settle(); await settle();
      global.extra = { shown };
    """ % {"id": json.dumps(A16)}
    out = _run(case, steps)
    assert out["extra"]["shown"] == {"chosen": ["其它"], "reason": "待补：等 2008 年的数据"}
    assert out["posted"][0] == ["/api/attempts/preview", {"op": "update", "number": "16", "fields": {"result": "t=3.10"}}]   # the verdict is not in it


# -- the attempt form: the node page's 编辑这一行 and 新增尝试 --------------------------------------------------------------------


_FORM_FACTS = """
      const body = panelPart("panel-body"), form = body.all().find((k) => k.tag === "FORM");
      const holds = (n, f) => n.all ? n.all().some(f) : false;
      const at = (f) => form.kids.findIndex((k) => typeof k !== "string" && (f(k) || holds(k, f)));
      const folds = body.all().filter((k) => k.tag === "DETAILS").map((d) => ({ summary: d.kids[0].textContent, open: !!d.open,
        at: form.kids.indexOf(d), text: d.textContent }));
      const chain = (n) => { const up = []; for (let p = n.parentNode; p; p = p.parentNode) up.push(p); return up; };
      const mentions = body.all().filter((k) => k.kids.some((c) => typeof c === "string" && c.includes("map.md")));
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_21_3_the_new_attempt_form_numbers_the_row_calls_the_date_日期_folds_the_draft_and_names_the_file_only_under_详情(lang):
    w = (lambda zh, en: en if lang == "en" else zh)
    steps = """
      global.attemptDraftBlock = () => ({ el: el("div", "attempt-draft", "draft body"), setFrozen() {}, markWritten() {} });
      renderAttemptForm("append");
    """ + _FORM_FACTS + """
      const fields = formFields();
      const first = (f) => form.kids.findIndex((k) => typeof k !== "string" && holds(k, (x) => x === f));
      global.extra = { labels: body.all().filter((k) => k.tag === "LABEL").map((k) => k.textContent), number: fields[0].value, numberDisabled: fields[0].disabled,
        subtitle: panelPart("panel-subtitle").textContent, folds,
        chooserAt: at((k) => k.cls && k.cls.has("status-chooser")), resultAt: first(fields[4]), draftAt: at((k) => k.cls && k.cls.has("attempt-draft")),
        files: mentions.map((k) => chain(k).some((p) => p.tag === "DETAILS" && p.kids[0].textContent === %(detail)s)) };
    """ % {"detail": json.dumps(w("详情", "Details"))}
    out = _form_run(steps, lang=None if lang == "zh" else lang)["extra"]
    assert out["number"] == "17" and out["numberDisabled"] is False        # the next free number, which the person may change
    assert w("日期", "Date") in out["labels"] and not any("（日期）" in l or "(日期)" in l or "Date (Date)" in l for l in out["labels"])
    assert out["subtitle"] == ""                                            # no "will write to" line at the top
    assert out["files"] == [True]                                           # the file is named once, under 详情
    names = [f["summary"] for f in out["folds"]]
    assert names == [w("用本地模型辅助填写", "Draft with a local model"), w("详情", "Details")]
    assert not any(f["open"] for f in out["folds"])                         # both folded
    assert out["resultAt"] < out["chooserAt"] < out["draftAt"] < out["folds"][1]["at"]   # the draft below the main fields, 详情 at the foot
    assert out["folds"][1]["text"] == w("详情将写入 map.md", "DetailsWill write to map.md")   # the summary, then the line


@needs_node
def test_21_3_the_date_is_labelled_with_the_tables_own_header_only_when_that_names_the_column_differently():
    steps = """
      state.attemptsConfig.columns.date = "时间";
      renderAttemptForm("append");
      global.extra = { labels: panelPart("panel-body").all().filter((k) => k.tag === "LABEL").map((k) => k.textContent) };
    """
    assert "日期（时间）" in _form_run(steps)["extra"]["labels"]
    assert "Date (时间)" in _form_run(steps, lang="en")["extra"]["labels"]
    steps = steps.replace('"时间"', '"Date"')
    assert "Date" in _form_run(steps, lang="en")["extra"]["labels"]


@needs_node
def test_21_3_a_members_new_attempt_says_the_working_copy_in_words_and_names_the_file_only_under_详情():
    steps = """
      renderAttemptForm("append");
    """ + _FORM_FACTS + """
      global.extra = { subtitle: panelPart("panel-subtitle").textContent, folds: folds.map((f) => [f.summary, f.text.slice(f.summary.length)]), mentioned: mentions.length };
    """
    out = _form_run(steps, role="member")["extra"]
    assert out["subtitle"] == "先写进你的工作副本，再生成提议；PI 批准以后才进入正式版本" and "map.md" not in out["subtitle"]
    assert out["folds"] == [["详情", "工作副本里的 map.md"]] and out["mentioned"] == 1
    out = _form_run(steps, role="member", lang="en")["extra"]
    assert out["subtitle"].startswith("Written into your working copy first") and out["folds"] == [["Details", "map.md in your working copy"]]


@needs_node
@pytest.mark.parametrize("role", ["single", "member"])
def test_21_3_a_new_row_starts_as_pending_and_is_written_with_the_status_pressed_and_the_reason_typed(role):
    steps = """
      state.panelSubject = { researchSource: null };
      renderAttemptForm("append");
      c.post["/api/attempts/preview"] = { file: "map.md", old_row: null, new_row: "New row" };
      const body = panelPart("panel-body");
      const pressed = () => body.all().filter((k) => k.cls.has("selected")).map((k) => k.dataset.status);
      const reason = () => body.all().find((k) => k.cls.has("status-reason"));
      const fields = formFields();
      const sentence = ROTeam.mode() === "member" ? fields[1] : null;
      const sent = () => posted.filter(([path]) => path === "/api/attempts/preview").map(([, b]) => b.fields.verdict);
      if (sentence) type(sentence, "新开一条");
      const start = pressed();
      await submit();                                              // as it opens: 待定, no reason
      find("返回修改", "BUTTON").click();
      type(reason(), "等 2008 年的数据");
      await submit();                                              // 待定 and the reason
      find("返回修改", "BUTTON").click();
      find("已放弃", "BUTTON").click();
      await submit();                                              // another status, the same reason
      global.extra = { start, sent: sent() };
    """
    out = _form_run(steps, role=role)["extra"]
    assert out["start"] == ["待定"]
    assert out["sent"] == ["🕒", "🕒 等 2008 年的数据", "☠️ 等 2008 年的数据"]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_21_3_the_node_pages_edit_form_shows_the_cells_status_pressed_and_its_words_apart_and_writes_only_what_changed(lang):
    w = (lambda zh, en: en if lang == "en" else zh)
    steps = """
      renderAttemptForm("update", %(id)s);
      const body = panelPart("panel-body");
      const pressed = () => body.all().filter((k) => k.cls.has("selected")).map((k) => k.dataset.status);
      const reason = body.all().find((k) => k.cls.has("status-reason"));
      const shown = { pressed: pressed(), reason: reason.value, values: formFields().map((f) => f.value), labels: body.all().filter((k) => k.tag === "LABEL").map((k) => k.textContent),
                      picks: body.all().filter((k) => k.cls.has("status-pick")).map((k) => k.textContent) };
      await submit();                                                  // nothing changed: nothing is sent
      const none = { posted: posted.length, said: panelLines().filter((l) => l.startsWith("panel-status")) };
      find(%(current)s, "BUTTON").click();
      await submit();                                                  // 现行 and the same words: saved at once
      global.extra = { shown, none, file: { ...file }, posted: posted.map(([path, b]) => [path, b.fields]) };
    """ % {"id": json.dumps(A16), "current": json.dumps(w("现行", "Current"))}
    out = _form_run(steps, lang=None if lang == "zh" else lang)["extra"]
    assert out["shown"]["pressed"] == ["待定"] and out["shown"]["reason"] == "先看"
    assert "🕒" not in "".join(out["shown"]["values"])                       # the marker is in no field (12.11)
    assert out["shown"]["picks"] == [w("现行", "Current"), w("待定", "Pending"), w("不利证据", "Adverse evidence"), w("已放弃", "Abandoned"),
                                    w("被否", "Rejected"), w("归档", "Archived"), w("其它", "Other")]
    assert out["none"] == {"posted": 0, "said": [f"panel-status: {w('没有需要写入的改动', 'Nothing has changed to write')}"]}
    assert out["posted"] == [["/api/attempts/preview", {"verdict": "✅ 先看"}], ["/api/attempts/write", {"verdict": "✅ 先看"}]]
    assert out["file"]["verdict"] == "✅ 先看"

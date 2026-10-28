"""Research OS V10, increment C2 (DESIGN.md 14.3, 14.6): a card on the desk
opens its attempt in the side panel, without leaving the desk -- its
verdict and result changed through the map file's preview-then-confirm
write (only the cells changed; turning a route abandoned asks its reason; a
member's change becomes a verdict-change proposal), 写备注, 来龙去脉 drawn
with the node page's renderer, 源自 and 由它而来 with remove (a member's as a
proposal), 打开详情页 -- one panel at a time, never replacing what is half
typed without asking; in Chinese as written and with no Chinese in English
but the researcher's own. desk.js, node.js and i18n.js run under node with a
stand-in DOM; the engine's words for the trace come over HTTP. Every test
names its scenario."""

from __future__ import annotations

import json
import re

import pytest

from test_v8_phase2 import _call, _project
from test_v8_phase3 import _serve
from test_v8_u2 import FORBIDDEN
from test_v9_language import CJK, WEBAPP, _app_function, _i18n_source, _node, needs_node

A16, A17, A18 = (f"attempt:map.md#{n}" for n in (16, 17, 18))

#: A stand-in DOM that keeps what the panel needs: classes, values, data-*,
#: click handlers, the panel's four parts; app.html's helpers stubbed as
#: they behave. A rendered panel is one line per element holding words.
_PANEL_DOM = r"""
function el(tag, cls, text) {
  const e = { tag: String(tag).toUpperCase(), kids: [], parentNode: null, dataset: {}, attrs: {}, style: {}, on: {},
    title: "", value: "", disabled: false, placeholder: "", rows: 0, type: "", scrollTop: 0, cls: new Set(),
    get className() { return Array.from(this.cls).join(" "); },
    set className(v) { this.cls = new Set(String(v || "").split(" ").filter(Boolean)); },
    get classList() { const s = this.cls; return { add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c),
      toggle: (c, on) => { const want = on === undefined ? !s.has(c) : !!on; if (want) s.add(c); else s.delete(c); return want; } }; },
    get textContent() { return this.kids.map((k) => (typeof k === "string" ? k : k.textContent)).join(""); },
    set textContent(v) { this.kids = v === "" || v == null ? [] : [String(v)]; },
    set innerHTML(v) { this.kids = v ? ["<html>" + v.replace(/<[^>]+>/g, "")] : []; },
    appendChild(c) { if (c && typeof c === "object") c.parentNode = this; this.kids.push(c); return c; },
    append(...cs) { cs.forEach((c) => this.appendChild(c)); },
    replaceChildren(...cs) { this.kids.forEach((k) => { if (k && typeof k === "object") k.parentNode = null; }); this.kids = []; this.append(...cs); },
    addEventListener(type, fn) { (this.on[type] = this.on[type] || []).push(fn); },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    focus() { global.focused = this; },
    all() { const out = []; const walk = (n) => n.kids.forEach((k) => { if (typeof k !== "string") { out.push(k); walk(k); } }); walk(this); return out; },
    querySelectorAll(sel) { const tags = sel.split(",").map((s) => s.trim().toUpperCase()); return this.all().filter((k) => tags.includes(k.tag)); },
    querySelector(sel) { return this.all().find((k) => k.cls.has(sel.slice(1))) || null; },
    click() { (this.on.click || []).forEach((f) => f({ preventDefault() {}, stopPropagation() {} })); },
  };
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
const parts = {};
["panel", "panel-title", "panel-subtitle", "panel-body", "panel-backdrop"].forEach((id) => { parts[id] = el("div", id === "panel" || id === "panel-backdrop" ? "hidden" : id); });
global.document = { getElementById: (id) => parts[id] || null, createElement: (tag) => el(tag), createElementNS: (ns, tag) => el(tag),
  querySelectorAll: () => [] };
window.addEventListener = () => {};
global.requestAnimationFrame = () => {}; // the trace's lines need a layout; none here
global.mk = (tag, cls, text) => el(tag, cls, text);
global.mkButton = (label, cls, onClick) => { const b = el("button", cls || "btn btn-small", label); b.type = "button"; if (onClick) b.addEventListener("click", onClick); return b; };
global.state = { noProject: false, dataEpoch: 0, currentId: "p-1", panelSubject: null, attemptsById: new Map() };
global.views = {};
global.ownMark = (item) => item && item.officialness && item.officialness.label && item.officialness.state !== "official"
  ? el("span", "own-mark", item.officialness.label) : null;
global.setStatus = (node, kind, text) => { node.className = "notice-status" + (kind ? " " + kind : ""); node.textContent = text || ""; };
global.renderViewFailure = () => {};
global.openReviewPanel = () => {};
global.openPanel = () => { parts.panel.classList.remove("hidden"); parts["panel-backdrop"].classList.remove("hidden"); };
global.formFieldEl = (label, input) => { const w = el("div", "form-field"); w.append(el("label", null, label), input); return w; };
global.toCellText = (v) => v.replace(/\r\n?/g, "\n").split("\n").map((s) => s.trim()).join("<br>").trim();
global.humanRecordsBlocked = () => null;
global.pollGeneration = () => {};
global.asked = [];
global.answers = [];
window.confirm = (q) => { asked.push(q); return answers.length ? answers.shift() : true; };
global.posted = [];
global.opened = [];
global.composed = [];
global.ROTeam = { openProposal() {}, composeInto: async (host, body) => { composed.push(body); host.appendChild(el("div", "draft", "[team.js]")); return {}; } };
function dump(node, out) {
  if (node.cls.has("hidden")) return out;
  const own = node.kids.filter((k) => typeof k === "string").join("");
  const value = node.tag === "INPUT" || node.tag === "TEXTAREA" ? " = " + node.value + (node.placeholder ? " (" + node.placeholder + ")" : "") : "";
  if (own || node.title || value) out.push(node.className + ": " + own + value + (node.title ? " (title: " + node.title + ")" : ""));
  node.kids.forEach((k) => { if (typeof k !== "string") dump(k, out); });
  return out;
}
global.panelLines = () => dump(parts["panel-title"], []).concat(dump(parts["panel-subtitle"], []), dump(parts["panel-body"], []));
global.panelPart = (id) => parts[id];
global.find = (text, tag) => parts["panel-body"].all().find((k) => (!tag || k.tag === tag) && k.kids.join("") === text);
global.field = (placeholder) => parts["panel-body"].all().find((k) => (k.tag === "TEXTAREA" || k.tag === "INPUT") && k.placeholder === placeholder);
global.fields = () => parts["panel-body"].all().filter((k) => k.tag === "TEXTAREA" || k.tag === "INPUT");
global.type = (f, text) => { f.value = text; f.dataset.typed = "1"; };
global.settle = () => new Promise((r) => setTimeout(r, 0));
async function openDesk(c) {
  global.apiGet = async (path) => {
    if (path === "/api/desk") return c.desk;
    const got = c.get[path];
    if (got === undefined) throw Object.assign(new Error("no fixture for " + path), { state: "missing" });
    return got;
  };
  global.apiPost = async (path, body) => {
    posted.push([path, body]);
    const answer = (c.post || {})[path];
    if (answer && answer.refuse) throw Object.assign(new Error("refused"), { state: answer.refuse });
    return typeof answer === "function" ? answer(body) : answer || { ok: true };
  };
  global.fetchAttemptRecords = async () => { state.attemptsById = new Map((c.records || []).map((r) => [r.id, r])); return true; };
  views.desk = el("div", "host");
  await window.RODesk.loadDesk(views.desk, false);
}
"""


def _st(status: str, label: str, words: str = "") -> dict:
    return {"status": status, "label": label, "words": words}


def _case(role: str, lang: str = "zh") -> dict:
    """The desk and the pages the panel reads for #16 TopicShift 波动, which
    #17 grew out of: one person, the PI, a member (whose own proposed line
    #18 源自 #16 is dashed 提议中), someone not in the team yet."""

    def w(zh: str, en: str) -> str:
        return en if lang == "en" else zh

    official = {"state": "official"}
    attempts = [
        {"id": A16, "number": "16", "title": "TopicShift 波动", "date": "07-26", "route_status": _st("pending", "待定", "先看"),
         "tone": "ochre", "abandoned": False, "result": "t=2.91", "changed_at": None, "officialness": official,
         "marks": {"proposals": [{"id": "p1", "kind": "verdict_change", "state": "pending", "label": w("等你批准", "Awaiting your approval")}] if role == "pi" else [],
                   "review": {"count": 1, "label": w("待复核 1", "Needs review: 1")}}},
        {"id": A17, "number": "17", "title": "事件轴", "date": "07-08~09", "route_status": _st("abandoned", "已放弃", "方向反了"),
         "tone": "gray", "abandoned": True, "result": "t=0.1", "reason": "无信号", "changed_at": None, "officialness": official, "marks": {}},
        {"id": A18, "number": "18", "title": "Plain panel", "date": "≤07-07", "route_status": _st("current", "现行"),
         "tone": "olive", "abandoned": False, "result": "r18", "changed_at": None, "officialness": official, "marks": {}},
    ]
    # A line's date is a day (mappings.toml); an earlier year keeps the pins below true on any day.
    lines = [{"child": A17, "parent": A16, "note": "换轴", "date": "2024-10-12", "by": None,
              "by_label": w("最初的研究者", "Original researcher"), "pending": None, "officialness": official}]
    if role == "member":
        lines.append({"child": A18, "parent": A16, "note": "", "date": "2026-10-13", "by": "wang@example.com", "by_label": "Wang",
                      "pending": {"proposal": "p9", "op": "add", "state": "pending"},
                      "officialness": {"state": "proposed", "label": w("提议中", "Proposed"), "proposal": "p9"}})
    desk = {"role": role, "attempts": attempts, "lines": lines, "lines_missing": [], "layout": {"positions": {}, "project": "p-1"},
            "column": {"role": role, "me": "li@example.com", "shared": {"state": "ok", "message": None},
                       "notice": None if role != "none" else w("你还不是这个项目的成员", "Not a member yet"),
                       "waiting": [], "mine": [], "review": {"count": 0, "groups": [], "none": {"items": [], "cards": []}}, "sync": None}}
    page = {"id": A16, "type": "attempt", "name": "#16 TopicShift 波动", "path": None, "file": "map.md",
            "details": {"number": "16", "date": "07-26", "description": "TopicShift 波动", "verdict": "待定 先看",
                        "verdict_status": _st("pending", "待定", "先看"), "result": "t=2.91"},
            "trace": {"columns": [
                {"key": "in", "title": w("读取的数据", "Data read"), "items": [
                    {"id": "dataset:data/raw.csv", "type": "dataset", "label": "raw.csv", "path": "data/raw.csv", "exists": True, "raw": True}]},
                {"key": "scripts", "title": w("脚本", "Scripts"), "items": [
                    {"id": "script:steps/16-build.py", "type": "script", "label": "16-build.py", "path": "steps/16-build.py",
                     "exists": True, "member": "step_files"}]},
                {"key": "out", "title": w("输出", "Outputs"), "items": [
                    {"id": "figure:figs/fig1.png", "type": "figure", "label": "fig1.png", "path": "figs/fig1.png", "exists": False,
                     "member": "belongs_to"}]}],
                "lines": [{"from": "dataset:data/raw.csv", "to": "script:steps/16-build.py", "type": "reads", "relation": "读取",
                           "human": False, "status": "auto", "review": False}], "breaks": [], "deeper": 0},
            "links": [], "review": 0}
    notes = {"notes": [{"id": "n1", "node": A16, "at": "2024-03-01T12:00:00", "by": "li@example.com", "label": "Li", "text": "这里的缺失值要先处理"},
                       {"id": "n2", "node": A16, "at": "2024-03-01T09:00:00", "by": "x", "label": "X", "text": "撤回了", "withdrawn": True}],
             "trusted": True}
    records = [{"id": A16, "verdict": "待定 先看", "verdict_status": _st("pending", "待定", "先看")},
               {"id": A17, "verdict": "☠ 方向反了", "verdict_status": _st("abandoned", "已放弃", "方向反了")}]
    return {"desk": desk, "records": records, "get": {
        "/api/me": {"me": {"id": "li@example.com", "name": "Li"}}, "/api/proposals": {"role": role, "proposals": []}, "/api/share": {},
        f"/api/node?node={A16.replace(':', '%3A').replace('#', '%23')}": page,
        f"/api/notes?node={A16.replace(':', '%3A').replace('#', '%23')}": notes}}


def _run(case: dict, steps: str, lang: str | None = None) -> dict:
    """Opens the desk of `case`, then runs `steps` (JS, awaited) and reports
    the panel's lines, what was asked, posted, composed and opened."""
    i18n = "" if lang is None else f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    script = ("global.window = {};\n" + i18n + _PANEL_DOM + (WEBAPP / "desk.js").read_text(encoding="utf-8")
              + "\n" + (WEBAPP / "node.js").read_text(encoding="utf-8")
              + "\nglobal.RONode = Object.assign({}, window.RONode, { openNode: (id) => opened.push(id) });\n"
              + f"(async () => {{ const c = {json.dumps(case, ensure_ascii=False)}; await openDesk(c);\n{steps}\n"
              " process.stdout.write(JSON.stringify({ lines: panelLines(), asked, posted, composed, opened, extra: global.extra || null,"
              " subject: state.panelSubject && state.panelSubject.id, backdrop: panelPart('panel-backdrop').className })); })();\n")
    return _node(script)


_THEIRS = ["TopicShift 波动", "07-26", "先看", "t=2.91", "这里的缺失值要先处理", "事件轴", "换轴", "方向反了"]


@needs_node
def test_14_3_a_card_opens_its_attempt_in_the_side_panel_in_chinese():
    """One person clicks #16: the panel on the desk (no backdrop: the map
    stays live) holds its verdict as a status label, its marks as words,
    打开详情页; the verdict and result to change; 写备注 with the notes (a
    withdrawn one left out); 来龙去脉 drawn by the node page's renderer
    without its repairs; 源自 (none yet) and 由它而来 (#17, removable)."""
    out = _run(_case("single"), "await window.RODesk.openAttempt(" + json.dumps(A16) + ");")
    lines = out["lines"]
    assert out["subject"] == A16 and out["backdrop"] == "hidden"
    for line in ["panel-title plain-title: #16 TopicShift 波动", "panel-subtitle: 07-26", "status-label tone-ochre: 待定",
                 "verdict-words: 先看", "dp-mark: 待复核 1", "btn btn-small: 打开详情页", "node-sec-title: 研究判断与结果",
                 "form-input:  = 待定 先看", "chip: 已放弃", "form-textarea:  = t=2.91",
                 "btn btn-primary: 保存", "node-sec-title: 写备注", "tl-note: 这里的缺失值要先处理", "desk-meta: Li · 2024年3月1日",
                 "form-textarea:  =  (写一条备注：为什么这样做、要注意什么)", "btn btn-small: 添加备注",
                 "node-sec-title: 来龙去脉", "trace-col-title: 读取的数据", "trace-item-name: raw.csv", "status-label tone-plain: 原始数据",
                 "status-label tone-plain: 步骤脚本", "status-label tone-clay: 文件不存在", "status-label tone-plain: 人工关联",
                 "node-sec-title: 源自", "desk-empty: 还没标来处", "node-sec-title: 由它而来", "desk-link: #17 事件轴",
                 "status-label tone-gray: 已放弃", "desk-note: 换轴", "desk-meta: 最初的研究者 · 2024年10月12日", "btn btn-small: 删除这条连线"]:
        assert line in lines, line
    # Re-baselined at acceptance (2026-10-13): one chip per status a cell leads with a marker --
    # 「☠ 方向反了」 gives 已放弃; 「待定 先看」 leads with none, so 待定 is not offered.
    assert [line for line in lines if line.startswith("chip: ")] == ["chip: 已放弃"]
    assert not any("撤回了" in line for line in lines)  # a withdrawn note is not shown
    assert not any("关联文件" in line or "还有文件属于" in line for line in lines)  # the node page's repairs stay there
    reason = [line for line in lines if "放弃的理由（必填）" in line]
    assert reason == []  # hidden until the preview says this write abandons the route


@needs_node
def test_14_6_the_panel_in_english_carries_no_chinese_but_the_researchers():
    """English mode: every word Research OS writes in the panel is English
    (13.4: Research assessment, Result, Provenance, Grew out of, What grew out of it),
    the researcher's text as written; no arrows or emoji, no 节点 or 画布."""
    out = _run(_case("pi", "en"), "await window.RODesk.openAttempt(" + json.dumps(A16) + ");", lang="en")
    lines = [line.split(" = ")[0] if " = " in line and "(" not in line.split(" = ")[1] else line for line in out["lines"]]
    left = [line for line in lines if CJK.search(re.sub("|".join(map(re.escape, _THEIRS)), "", line))]
    assert left == [] and not any(FORBIDDEN.search(line) for line in lines)
    for line in ["status-label tone-ochre: Pending", "dp-mark: Awaiting your approval", "dp-mark: Needs review: 1",
                 "btn btn-small: Open the details page", "node-sec-title: Research assessment and result", "chip: Abandoned",
                 "btn btn-primary: Save", "node-sec-title: Write a comment", "desk-meta: Li · 1 Mar 2024",
                 "btn btn-small: Add comment", "node-sec-title: Provenance", "trace-col-title: Data read",
                 "status-label tone-plain: Raw data", "status-label tone-plain: Step script", "status-label tone-clay: File not found",
                 "node-sec-title: Grew out of", "desk-empty: Origin not marked yet", "node-sec-title: What grew out of it",
                 # 13.5 (V10 review, finding 20): a line's date in the screen's form, re-baselined from its ISO day.
                 "desk-meta: Original researcher · 12 Oct 2024", "btn btn-small: Delete this line"]:
        assert line in lines, line
    assert [line for line in lines if line.startswith("chip: ")] == ["chip: Abandoned"]
    assert not any("节点" in line or "画布" in line for line in out["lines"])


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_14_3_an_abandoned_cards_panel_says_who_abandoned_it(lang):
    """Ruled 2026-10-13: a route abandoned by a direct write (a decision
    with no proposal) reads 「由 {who} 放弃」 ("Abandoned by {who}") under its
    reason, never 提议人; one abandoned through an approved proposal names
    who proposed and who approved it."""
    w = (lambda zh, en: en if lang == "en" else zh)
    seen = []
    for decision in ({"act": "abandoned", "proposal": None, "at": "2024-03-01T12:00:00",
                      "by": {"id": None, "label": w("最初的研究者", "Original researcher")}, "approved_by": None},
                     {"act": "abandoned", "proposal": "p-1", "at": "2024-03-01T12:00:00", "by": {"id": "w", "label": "Wang"},
                      "approved_by": {"id": "l", "label": "Li"}}):
        case = _case("single", lang)
        case["desk"]["attempts"][0].update(abandoned=True, reason="无信号", decision=decision)
        lines = _run(case, "await window.RODesk.openAttempt(" + json.dumps(A16) + ");", lang=lang)["lines"]
        at = next(i for i, line in enumerate(lines) if line.startswith("desk-note: "))
        seen.append(lines[at:at + 2])
    day = w("2024年3月1日", "1 Mar 2024")
    assert seen == [[w("desk-note: 放弃的理由：无信号", "desk-note: Reason for abandoning: 无信号"),
                     w(f"desk-meta: 由 最初的研究者 放弃 · {day}", f"desk-meta: Abandoned by Original researcher · {day}")],
                    [w("desk-note: 放弃的理由：无信号", "desk-note: Reason for abandoning: 无信号"),
                     w(f"desk-meta: 提议人 Wang · 批准人 Li · {day}", f"desk-meta: Proposed by Wang · Approved by Li · {day}")]]


#: A stand-in for the engine's attempt table, for the steps that save a row: the row's cells as the file holds
#: them (`file`, decoration kept), the preview's `cells` read from it as server.attempts_preview_payload does,
#: and the write changing it -- so that the panel, drawn again, shows what was written. The page it reads is
#: #16's (`c.get[...]`), `refuse` a state the write is refused with, `moved` a cell the file holds that the
#: panel did not show.
_ENGINE = r"""
global.file = { verdict: "待定 先看", result: "**t=2.91**" };
global.refuse = null;
const plain = (raw) => raw.replace(/\*\*/g, "").replace(/<br>/g, "\n");
const node = "/api/node?node=" + encodeURIComponent(%(id)s);
const sync = () => { c.get[node].details.verdict = plain(file.verdict); c.get[node].details.result = plain(file.result); };
c.post = {
  "/api/attempts/preview": (b) => {
    const cells = {};
    Object.keys(b.fields).forEach((k) => { const raw = k in file ? file[k] : "";
      cells[k] = { was: raw, was_text: plain(raw), now_text: plain(b.fields[k]) }; });
    const abandons = (b.fields.verdict || "").startsWith("☠");
    return { file: "map.md", old_row: "| 16 | old |", new_row: "| 16 | new |", abandons, cells,
             reason_required: abandons && !file.verdict.startsWith("☠") };
  },
  "/api/attempts/write": (b) => {
    if (refuse) throw Object.assign(new Error("refused"), { state: refuse });
    Object.keys(b.fields).forEach((k) => { file[k] = b.fields[k]; });
    sync();
    return { ok: true, file: "map.md", ingest_error: null, generation: 7 };
  },
};
global.flush = async () => { for (let i = 0; i < 8; i++) await settle(); };
global.resultField = () => fields().find((f) => f.tag === "TEXTAREA" && !f.placeholder);
global.verdictField = () => fields().find((f) => f.tag === "INPUT");
"""


def _saving(role: str = "single", lang: str = "zh") -> tuple[dict, str]:
    """#16 as `role` sees it, the engine stand-in installed once the panel is open; `steps` run after it."""
    case = _case(role, lang)
    page = case["get"]["/api/node?node=" + A16.replace(":", "%3A").replace("#", "%23")]
    page["details"]["result"] = "t=2.91"
    return case, "await window.RODesk.openAttempt(%s);\n" % json.dumps(A16) + _ENGINE.replace("%(id)s", json.dumps(A16))


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_18_3_a_change_of_the_result_saves_at_once_and_撤销_writes_the_old_cell_back(lang):
    """One person changes #16's result and presses 保存: the preview is asked (silently) and the write follows with
    the same body -- no row shown, no second click --, and the panel says 已保存 with 撤销 beside it. 撤销
    writes the cell back as the file held it (its Markdown decoration included, not the text the panel
    showed), the same way: a preview, then a write. The panel then says it undid the step and offers nothing."""
    w = (lambda zh, en: en if lang == "en" else zh)
    case, head = _saving("single", lang)
    steps = head + """
      type(resultField(), "t=3.10");
      find(%(save)s, "BUTTON").click(); await flush();
      const saved = panelLines();
      const savedFile = { ...file };
      global.extra = { saved, savedFile, shown: [find(%(confirm)s, "BUTTON"), find(%(back)s, "BUTTON")].map((x) => !!x),
                       field: resultField().value };
      find(%(undo)s, "BUTTON").click(); await flush();
      extra.undone = panelLines(); extra.file = { ...file }; extra.field = [extra.field, resultField().value];
      extra.offered = !!find(%(undo)s, "BUTTON");
    """ % {"save": json.dumps(w("保存", "Save")), "confirm": json.dumps(w("保存修改", "Save changes")),
           "back": json.dumps(w("返回修改", "Back to editing")), "undo": json.dumps(w("撤销", "Undo"))}
    out = _run(case, steps, lang=None if lang == "zh" else lang)
    body = {"op": "update", "number": "16", "fields": {"result": "t=3.10"}}
    undo = {"op": "update", "number": "16", "fields": {"result": "**t=2.91**"}}
    assert out["posted"] == [["/api/attempts/preview", body], ["/api/attempts/write", body],
                             ["/api/attempts/preview", undo], ["/api/attempts/write", undo]]
    extra = out["extra"]
    assert extra["shown"] == [False, False]  # no row, no confirmation, no way back to a preview
    assert not any(line.startswith("diff-row") or line.startswith("diff-label") for line in extra["saved"])
    assert extra["savedFile"]["result"] == "t=3.10" and extra["file"] == {"verdict": "待定 先看", "result": "**t=2.91**"}
    assert f": {w('已保存', 'Saved')}" in extra["saved"] and f"err-detail-toggle: {w('撤销', 'Undo')}" in extra["saved"]
    assert extra["field"] == ["t=3.10", "t=2.91"] and extra["offered"] is False
    assert f"notice-status ok: {w('撤销了上一步', 'Undid the last step')}" in extra["undone"]
    assert out["asked"] == []
    if lang == "en":  # the researcher's own cells (field values) apart, no Chinese
        words = [line for line in extra["saved"] + extra["undone"] if " = " not in line]
        assert [line for line in words if CJK.search(re.sub("|".join(map(re.escape, _THEIRS)), "", line))] == []


@needs_node
def test_18_3_a_second_edit_replaces_the_offer_and_a_row_that_changed_again_is_not_offered_back():
    """撤销 is offered until the panel closes or the row changes again. A second save offers back only its own
    step. A refresh that finds the row changed by someone else (the file edited meanwhile) draws the panel
    without 撤销; a refresh that finds it as the save left it keeps it; closing and opening the panel again
    offers nothing."""
    case, head = _saving()
    steps = head + """
      type(resultField(), "t=3.10");
      find("保存", "BUTTON").click(); await flush();
      type(verdictField(), "现行 主线");
      find("保存", "BUTTON").click(); await flush();
      const twice = posted.length;
      await window.RODesk.revalidatePanel(state.panelSubject); await flush();
      const kept = !!find("撤销", "BUTTON");
      find("撤销", "BUTTON").click(); await flush();
      const afterUndo = { ...file };
      type(resultField(), "t=3.50");
      find("保存", "BUTTON").click(); await flush();
      file.result = "somebody else";                 // the file changed elsewhere ...
      c.get[node].details.result = "somebody else";   // ... and the refresh read it
      await window.RODesk.revalidatePanel(state.panelSubject); await flush();
      const gone = !find("撤销", "BUTTON");
      await window.RODesk.openAttempt(%(a18)s);
      await window.RODesk.openAttempt(%(a16)s); await flush();
      global.extra = { twice, kept, afterUndo, gone, reopened: !find("撤销", "BUTTON") };
    """.replace("%(a18)s", json.dumps(A18)).replace("%(a16)s", json.dumps(A16))
    case["get"]["/api/node?node=" + A18.replace(":", "%3A").replace("#", "%23")] = {
        **case["get"]["/api/node?node=" + A16.replace(":", "%3A").replace("#", "%23")], "id": A18}
    case["get"]["/api/notes?node=" + A18.replace(":", "%3A").replace("#", "%23")] = {"notes": [], "trusted": True}
    out = _run(case, steps)
    posted = out["posted"]
    assert posted[3] == ["/api/attempts/write", {"op": "update", "number": "16", "fields": {"verdict": "现行 主线"}}]
    assert out["extra"]["twice"] == 4 and out["extra"]["kept"] is True
    # 撤销 after the second save undid that step only: the verdict is back, the first save's result stays.
    assert posted[4:6] == [["/api/attempts/preview", {"op": "update", "number": "16", "fields": {"verdict": "待定 先看"}}],
                           ["/api/attempts/write", {"op": "update", "number": "16", "fields": {"verdict": "待定 先看"}}]]
    assert out["extra"]["afterUndo"] == {"verdict": "待定 先看", "result": "t=3.10"}
    assert out["extra"]["gone"] is True and out["extra"]["reopened"] is True


@needs_node
def test_18_3_撤销_is_refused_in_place_when_the_row_moved_on_or_would_abandon_the_route_again():
    """The click on 撤销 asks the engine once more: a cell that no longer reads as the save left it (the file
    changed between the refresh and the click) is not written over, and an old cell that would abandon the
    route again needs its reason (14.3), so neither is written; each says so in a sentence of its own."""
    case, head = _saving()
    steps = head + """
      type(resultField(), "t=3.10");
      find("保存", "BUTTON").click(); await flush();
      file.result = "typed in the editor";             // the file moved on; the page has not read it yet
      find("撤销", "BUTTON").click(); await flush();
      const moved = panelLines().filter((l) => l.startsWith("notice-status"));
      const writes = posted.filter(([path]) => path === "/api/attempts/write").length;
      // The same for a route un-abandoned: the old verdict would abandon it again.
      file.verdict = "☠ 方向反了"; file.result = "t=2.91"; sync();
      await window.RODesk.revalidatePanel(state.panelSubject); await flush();
      type(verdictField(), "待定 再看");
      find("保存", "BUTTON").click(); await flush();
      find("撤销", "BUTTON").click(); await flush();
      global.extra = { moved, writes, abandons: panelLines().filter((l) => l.startsWith("notice-status")),
                       writesAfter: posted.filter(([path]) => path === "/api/attempts/write").length, verdict: file.verdict };
    """
    out = _run(case, steps)
    extra = out["extra"]
    assert extra["moved"] == ["notice-status err: 这一行之后又改过，不能撤销。"] and extra["writes"] == 1
    assert extra["abandons"] == ["notice-status err: 撤销会让这条路线重新放弃，需要写明理由：请把研究判断改回已放弃，再保存。"]
    assert extra["writesAfter"] == 2 and extra["verdict"] == "待定 再看"  # the second save only; both undos wrote nothing


@needs_node
def test_18_3_a_file_that_changed_meanwhile_and_a_refused_write_are_not_saved_over_unseen():
    """Conflict-checked. The cell the file holds is no longer the one the panel showed (edited elsewhere while
    the panel was open): nothing is written at once -- the row is previewed, with the file's row and a note
    saying it changed, and written only on 保存修改 (then 撤销 is offered as for any save). A write the engine
    refuses (the row is gone) says the existing sentence with the engine's text behind 「详情」, and the
    fields keep what was typed."""
    case, head = _saving()
    steps = head + """
      file.result = "**t=2.95**";                      // changed in the file; the panel still shows t=2.91
      type(resultField(), "t=3.10");
      find("保存", "BUTTON").click(); await flush();
      const held = { lines: panelLines(), writes: posted.filter(([path]) => path === "/api/attempts/write").length };
      find("保存修改", "BUTTON").click(); await flush();
      const written = { file: { ...file }, offered: !!find("撤销", "BUTTON") };
      refuse = "attempt_not_found";
      type(resultField(), "t=4.00");
      find("保存", "BUTTON").click(); await flush();
      global.extra = { held, written, refused: panelLines().filter((l) => l.startsWith("notice-status")), typed: resultField().value,
                       fields: fields().filter((f) => !f.disabled).length };
    """
    out = _run(case, steps)
    extra = out["extra"]
    assert extra["held"]["writes"] == 0
    for line in ["diff-row old: | 16 | old |", "diff-row new: | 16 | new |", "btn btn-primary: 保存修改", "btn: 返回修改",
                 "confirm-note: 这一行在你打开它以后被改过：上面的原行是文件里现在的样子。"]:
        assert line in extra["held"]["lines"], line
    assert extra["written"] == {"file": {"verdict": "待定 先看", "result": "t=3.10"}, "offered": True}
    assert extra["refused"] == ["notice-status err: 写入失败，文件没有被修改"] and extra["typed"] == "t=4.00"
    assert extra["fields"] >= 3  # the form is open again, not frozen


@needs_node
def test_14_3_abandoning_is_previewed_and_asks_why_before_anything_is_written():
    """An abandoning verdict is not saved at once: the engine's preview says the write turns the route
    abandoned, so the reason is asked before anything is shown; with it, the row is previewed and written only
    on 保存修改, the panel is drawn again with what was written -- and no 撤销, since the basis frozen with the
    abandonment (decisions.toml) is not written back by restoring the row."""
    case, head = _saving()
    steps = head + """
      type(resultField(), "t=3.10");
      find("保存", "BUTTON").click(); await flush();
      find("已放弃", "BUTTON").click();
      find("保存", "BUTTON").click(); await flush();
      global.extra = { asked: panelLines().filter((l) => l.startsWith("notice-status") || l.includes("放弃的理由")),
                       writes: posted.filter(([path]) => path === "/api/attempts/write").length };
      type(field("为什么放弃这条路线"), "残差不平稳");
      find("保存", "BUTTON").click(); await flush();
      extra.preview = panelLines().filter((l) => l.startsWith("diff-") || l.startsWith("confirm-note"));
      extra.writesBefore = posted.filter(([path]) => path === "/api/attempts/write").length;
      find("保存修改", "BUTTON").click(); await flush();
      extra.after = { lines: panelLines(), offered: !!find("撤销", "BUTTON") };
    """
    out = _run(case, steps)
    posted = out["posted"]
    # The result alone first (saved at once), then the abandoning verdict without its reason (previewed, refused
    # before anything is shown), then with it: previewed, written on the confirmation.
    assert posted[0] == ["/api/attempts/preview", {"op": "update", "number": "16", "fields": {"result": "t=3.10"}}]
    assert posted[1] == ["/api/attempts/write", posted[0][1]]
    assert posted[2] == ["/api/attempts/preview", {"op": "update", "number": "16", "fields": {"verdict": "☠ 待定 先看"}}]
    assert posted[3] == ["/api/attempts/preview", {"op": "update", "number": "16", "fields": {"verdict": "☠ 待定 先看"},
                                                   "reason": "残差不平稳"}]
    assert posted[4] == ["/api/attempts/write", posted[3][1]] and len(posted) == 5
    extra = out["extra"]
    assert extra["asked"] == [": 放弃的理由（必填）", "notice-status err: 放弃一条路线时必须写明理由"]
    assert extra["writes"] == 1 and extra["writesBefore"] == 1  # only the result was written before the confirmation
    assert "confirm-note: 这一行会放弃这条路线：理由写进结果一栏，它当时的依据冻结下来。" in extra["preview"]
    assert extra["after"]["offered"] is False and "notice-status ok: 已写入 map.md。" in extra["after"]["lines"]
    assert out["asked"] == []


@needs_node
def test_14_3_a_members_change_becomes_a_verdict_change_proposal():
    """A member: a sentence is asked first; the row goes into their own
    working copy through the same write, then the proposal's dry run is
    shown (team.js composeInto) with the sentence, the number and the
    reason. Their proposed line #18 源自 #16 is marked 提议中 and offers no
    removal; the official one offers its removal as a proposal."""
    case = _case("member")
    case["post"] = {"/api/attempts/preview": {"file": "map.md", "old_row": "a", "new_row": "b", "abandons": True, "reason_required": False},
                    "/api/attempts/write": {"ok": True, "file": "map.md"}}
    steps = """
      await window.RODesk.openAttempt(%(id)s);
      global.before = panelLines();
      find("已放弃", "BUTTON").click();
      find("预览这一行").click(); await settle();
      const noSentence = panelLines().filter((l) => l.startsWith("notice-status err"));
      type(field("用你自己的话说明这份提议：PI 会先看到这句话"), "放弃 16");
      type(field("为什么放弃这条路线"), "残差不平稳");
      find("预览这一行").click(); await settle(); await settle();
      find("保存后查看提议").click(); await settle(); await settle(); await settle();
      answers.push(true);
      find("提议删除这条连线").click(); await settle(); await settle(); await settle();
      global.extra = { noSentence, before };
    """ % {"id": json.dumps(A16)}
    out = _run(case, steps)
    assert out["posted"][0] == ["/api/attempts/preview", {"op": "update", "number": "16", "fields": {"verdict": "☠ 待定 先看"}}]
    assert out["posted"][1] == ["/api/attempts/write", out["posted"][0][1]]  # the reason goes with the proposal, not the row
    assert out["composed"] == [{"kind": "verdict_change", "sentence": "放弃 16", "number": "16", "reason": "残差不平稳"}]
    assert out["posted"][2] == ["/api/proposals", {"kind": "route_link", "op": "remove", "child": A17, "parent": A16, "write": True}]
    assert out["asked"] == ["提议删除这条连线？PI 批准后它才会从路线图上消失。"]
    assert out["extra"]["noSentence"] == ["notice-status err: 请用一句话说明这份提议"]
    before = out["extra"]["before"]
    assert "btn btn-primary: 预览这一行" in before and ": 一句话说明（必填）" in before
    assert "desk-link: #18 Plain panel" in before and "own-mark: 提议中" in before
    assert before.count("btn btn-small: 提议删除这条连线") == 1  # the official line only; the proposed one waits
    # The draft stays: the line's removal is said on its row, the panel not drawn over it.
    assert "draft: [team.js]" in out["lines"] and "notice-status ok: 已把删除这条连线作为提议交给 PI。" in out["lines"]


@needs_node
def test_14_3_one_panel_at_a_time_and_what_is_typed_is_never_replaced_unasked():
    """A note half typed in #16's panel: a click on the same card keeps it;
    a click on #17 asks first -- declined, #16 stays with what was typed;
    accepted, #17 opens. 打开详情页 asks too, then opens the node page. The
    PI removes a line directly (mappings.toml), asked first."""
    case = _case("pi")
    a17 = A17.replace(":", "%3A").replace("#", "%23")
    case["get"][f"/api/node?node={a17}"] = {**case["get"][f"/api/node?node={A16.replace(':', '%3A').replace('#', '%23')}"], "id": A17}
    case["get"][f"/api/notes?node={a17}"] = {"notes": [], "trusted": True}
    steps = """
      await window.RODesk.openAttempt(%(a16)s);
      answers.push(true);
      find("删除这条连线").click(); await settle(); await settle(); await settle();
      type(field("写一条备注：为什么这样做、要注意什么"), "还没写完");
      await window.RODesk.openAttempt(%(a16)s);
      answers.push(false);
      await window.RODesk.openAttempt(%(a17)s);
      global.extra = [state.panelSubject.id, field("写一条备注：为什么这样做、要注意什么").value];
      answers.push(false);
      find("打开详情页").click();
      answers.push(true);
      await window.RODesk.openAttempt(%(a17)s);
    """ % {"a16": json.dumps(A16), "a17": json.dumps(A17)}
    out = _run(case, steps)
    assert out["posted"] == [["/api/mappings/delete", {"from": A17, "to": A16, "type": "derives_from"}]]
    leave = "这里有还没写入的内容，离开它就不保存了。继续吗？"
    assert out["asked"] == ["删除这条连线？它会从项目的记录里删去。", leave, leave, leave]
    assert out["extra"] == [A16, "还没写完"] and out["opened"] == [] and out["subject"] == A17


def test_14_6_the_attempt_trace_speaks_the_requests_language(tmp_path):
    """GET /api/node for an attempt: its 来龙去脉's columns and its gap are
    worded in the request's language (13.6), Chinese by default."""
    root = _project(tmp_path / "proj")
    url = "/api/node?node=" + A16.replace(":", "%3A").replace("#", "%23")
    with _serve(root) as base:
        status, zh = _call(base + url)
        assert status == 200
        status, en = _call(base + url, headers={"X-ResearchOS-Lang": "en"})
        assert status == 200
    assert [c["title"] for c in zh["trace"]["columns"]] == ["读取的数据", "脚本", "输出"]
    assert [c["title"] for c in en["trace"]["columns"]] == ["Data read", "Scripts", "Outputs"]


# -- app.html's older attempt form (the node page's 编辑这一行): the same rule (18.3) ----------------------------


def _form_run(steps: str, lang: str | None = None, role: str = "single") -> dict:
    """The older form (app.html `renderAttemptForm`) built for #16 under node, with the engine stand-in of the panel
    tests and desk.js's helpers it shares (RODesk.atOnce); `steps` run after it is drawn. Reports the panel's lines,
    what was posted and what `steps` left in `extra`."""
    i18n = "" if lang is None else f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    helpers = "\n".join(_app_function(head) for head in (
        "function defaultNewNumber(", "function defaultDateValue(", "function distinctVerdicts(", "function setFormStatus(",
        "function renderAttemptForm("))
    record = {"id": A16, "attrs": {"number": "16", "date": "07-26", "description": "TopicShift 波动", "variables": "TopicShift->RV"},
              "verdict": "待定 先看", "result": "t=2.91"}
    script = ("global.window = {};\n" + i18n + _PANEL_DOM + (WEBAPP / "desk.js").read_text(encoding="utf-8") + "\n" + helpers + "\n"
              + "global.panelTitle = panelPart('panel-title'); global.panelSubtitle = panelPart('panel-subtitle');\n"
              "global.panelBody = panelPart('panel-body');\n"
              "global.t = (s, v) => (window.ROI18N ? window.ROI18N.t(s, v) : String(s).replace(/\\{(\\w+)\\}/g, (m, k) => (v && k in v ? String(v[k]) : m)));\n"
              "global.RODesk = window.RODesk;\n"
              "global.ROTeam.mode = () => " + json.dumps(role) + ";\n"
              "global.renderBlockingError = (node, text, err) => { node.textContent = text; node.title = err && err.state || ''; };\n"
              "global.attemptDraftBlock = () => null; global.panelErrorBoxCn = (m) => el('div', 'error-box', m);\n"
              "global.closePanel = () => { global.closed = (global.closed || 0) + 1; };\n"
              "global.EDITABLE_KEYS = ['date', 'description', 'variables', 'result', 'verdict'];\n"
              "global.mayReplacePanel = () => true;\n"
              f"state.attemptsById = new Map([[{json.dumps(A16)}, {json.dumps(record, ensure_ascii=False)}]]);\n"
              "state.attemptsConfig = { file: 'map.md', columns: { date: '日期', description: '描述', variables: '变量', result: '结果', verdict: '研究判断' } };\n"
              "const c = { post: {}, get: {} };\n"
              "global.apiPost = async (path, body) => { posted.push([path, body]); const a = c.post[path];\n"
              "  if (a && a.refuse) throw Object.assign(new Error('refused'), { state: a.refuse }); return typeof a === 'function' ? a(body) : a || { ok: true }; };\n"
              + _ENGINE.replace("%(id)s", json.dumps(A16)).replace("c.get[node].details", "(c.get[node] || { details: {} }).details")
              + "\nglobal.formFields = () => panelPart('panel-body').all().filter((k) => k.tag === 'INPUT' || k.tag === 'TEXTAREA');\n"
              "global.submit = async () => { const form = panelPart('panel-body').all().find((k) => k.tag === 'FORM'); (form.on.submit[0])({ preventDefault() {} }); await flush(); };\n"
              "(async () => {\n" + steps + "\n process.stdout.write(JSON.stringify({ lines: panelLines(), posted, extra: global.extra || null, closed: global.closed || 0 })); })();\n")
    return _node(script)


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_18_3_the_older_form_saves_a_changed_result_at_once_and_撤销_puts_it_back(lang):
    """The node page's 编辑这一行 (app.html's form): the PI or a single researcher changes the result and presses
    保存 -- no row shown, no confirmation --, the form stays open saying 已保存 with 撤销, which writes the old cell
    back (decoration kept) the same way and puts the old text in the field. Changing the date, description
    or variables as well is a preview, as before."""
    w = (lambda zh, en: en if lang == "en" else zh)
    steps = """
      renderAttemptForm("update", %(id)s);
      const [number, date, description, variables, result, verdict] = formFields();
      global.extra = { label: panelPart("panel-body").all().find((k) => k.tag === "BUTTON" && k.type === "submit").textContent };
      result.value = "t=3.10";
      await submit();
      extra.saved = panelLines().filter((l) => l.startsWith("panel-status") || l.startsWith("err-detail") || l.startsWith(": " + %(saved)s));
      extra.rowShown = panelPart("panel-body").all().filter((k) => k.cls.has("diff-row")).length;
      extra.file = { ...file };
      find(%(undo)s, "BUTTON").click(); await flush();
      extra.undone = panelLines().filter((l) => l.startsWith("panel-status") || l.startsWith("err-detail"));
      extra.field = result.value; extra.fileBack = { ...file };
      // description with the result: previewed, not written
      result.value = "t=4"; description.value = "TopicShift 波动 (重跑)";
      await submit();
      extra.mixed = { writes: posted.filter(([path]) => path === "/api/attempts/write").length,
                      confirm: !!find(%(confirm)s, "BUTTON"), rows: panelPart("panel-body").all().filter((k) => k.cls.has("diff-row")).length };
    """ % {"id": json.dumps(A16), "saved": json.dumps(w("已保存", "Saved")), "undo": json.dumps(w("撤销", "Undo")),
           "confirm": json.dumps(w("保存修改", "Save changes"))}
    out = _form_run(steps, lang=None if lang == "zh" else lang)
    extra = out["extra"]
    body = {"op": "update", "number": "16", "fields": {"result": "t=3.10"}}
    undo = {"op": "update", "number": "16", "fields": {"result": "**t=2.91**"}}
    assert out["posted"][:4] == [["/api/attempts/preview", body], ["/api/attempts/write", body],
                                 ["/api/attempts/preview", undo], ["/api/attempts/write", undo]]
    assert extra["label"] == w("保存", "Save") and extra["rowShown"] == 0
    assert [l for l in extra["saved"] if l.startswith("err-detail")] == [f"err-detail-toggle: {w('撤销', 'Undo')}"]
    assert f": {w('已保存', 'Saved')}" in extra["saved"] and extra["file"]["result"] == "t=3.10"
    assert extra["undone"] == [f"panel-status ok: {w('撤销了上一步', 'Undid the last step')}"]
    assert extra["field"] == "t=2.91" and extra["fileBack"] == {"verdict": "待定 先看", "result": "**t=2.91**"}
    assert out["closed"] == 0  # the form stays: 已保存 and 撤销 are in it
    # Other cells with it: the preview and its confirmation, nothing written yet.
    assert extra["mixed"] == {"writes": 2, "confirm": True, "rows": 2}
    assert out["posted"][4][0] == "/api/attempts/preview" and out["posted"][4][1]["fields"] == {"description": "TopicShift 波动 (重跑)", "result": "t=4"}


@needs_node
def test_18_3_the_older_form_holds_nothing_unwritten_after_a_save_at_once_or_撤销():
    """After 保存 (written at once, the form staying open) and after 撤销, the fields hold what the file holds:
    opening another panel (RODesk.mayLeavePanel, which every opener asks) does not ask 'content not yet written'.
    Text typed after that is still guarded."""
    steps = """
      renderAttemptForm("update", %(id)s);
      const [number, date, description, variables, result, verdict] = formFields();
      const leave = () => { answers.push(false); return RODesk.mayLeavePanel(); };
      type(result, "t=3.10");
      const typedFirst = leave();
      await submit();
      const saved = { may: leave(), typed: result.dataset.typed || null, initial: result.dataset.initial };
      find("撤销", "BUTTON").click(); await flush();
      const undone = { may: leave(), typed: result.dataset.typed || null, field: result.value, initial: result.dataset.initial };
      type(result, "t=4");
      const typedAgain = leave();
      global.extra = { typedFirst, saved, undone, typedAgain, asked: asked.slice() };
    """ % {"id": json.dumps(A16)}
    extra = _form_run(steps)["extra"]
    question = "这里有还没写入的内容，离开它就不保存了。继续吗？"
    assert extra["typedFirst"] is False and extra["typedAgain"] is False  # typed and unwritten: still asked
    assert extra["saved"] == {"may": True, "typed": None, "initial": "t=3.10"}
    assert extra["undone"] == {"may": True, "typed": None, "field": "t=2.91", "initial": "t=2.91"}
    assert extra["asked"] == [question, question]  # the two unwritten moments only


@needs_node
def test_18_3_撤销_in_the_older_form_puts_back_what_the_file_held_when_it_had_moved():
    """The file changed after the form opened (the cell reads 't=2.95', the form showed 't=2.91'): 保存 shows the
    row, 保存修改 writes it, and 撤销 writes the file's own cell back -- so the field says 't=2.95' too, not the
    text the form opened with, and the next change is judged against it (saved at once, not 'moved' again)."""
    steps = """
      renderAttemptForm("update", %(id)s);
      const [number, date, description, variables, result, verdict] = formFields();
      file.result = "**t=2.95**";
      result.value = "t=3.10";
      await submit();
      find("保存修改", "BUTTON").click(); await flush();
      const written = file.result;
      find("撤销", "BUTTON").click(); await flush();
      const undone = { file: file.result, field: result.value, status: panelLines().filter((l) => l.startsWith("panel-status")) };
      result.value = "t=4";
      const before = posted.length;
      await submit();
      global.extra = { written, undone, again: { rows: panelPart("panel-body").all().filter((k) => k.cls.has("diff-row")).length,
                       written: posted.slice(before).filter(([path]) => path === "/api/attempts/write").map(([, b]) => b.fields.result) } };
    """ % {"id": json.dumps(A16)}
    extra = _form_run(steps)["extra"]
    assert extra["written"] == "t=3.10"
    assert extra["undone"] == {"file": "**t=2.95**", "field": "t=2.95", "status": ["panel-status ok: 撤销了上一步"]}
    assert extra["again"] == {"rows": 0, "written": ["t=4"]}


@needs_node
def test_18_3_the_older_form_still_previews_abandoning_a_new_row_and_a_members_change():
    """What keeps its preview in the older form: a verdict that abandons the route (the reason is asked, then the
    row is shown and written on 保存修改, the form closing as before, no 撤销), a new row (其 button still reads
    预览改动), and a member's change, which is a proposal needing its sentence first."""
    steps = """
      renderAttemptForm("update", %(id)s);
      let [number, date, description, variables, result, verdict] = formFields();
      verdict.value = "☠ 方向反了";
      await submit();
      const asked = { writes: posted.filter(([path]) => path === "/api/attempts/write").length,
                      lines: panelLines().filter((l) => l.includes("放弃的理由") || l.startsWith("panel-status")) };
      formFields().find((f) => f.tag === "TEXTAREA" && f.placeholder === "为什么放弃这条路线").value = "残差不平稳";
      await submit();
      const rows = panelPart("panel-body").all().filter((k) => k.cls.has("diff-row")).length;
      find("保存修改", "BUTTON").click(); await flush();
      const after = { closed: global.closed || 0, writes: posted.filter(([path]) => path === "/api/attempts/write").length, offered: !!find("撤销", "BUTTON") };
      renderAttemptForm("append");
      const label = panelPart("panel-body").all().find((k) => k.tag === "BUTTON" && k.type === "submit").textContent;
      global.extra = { asked, rows, after, label };
    """ % {"id": json.dumps(A16)}
    out = _form_run(steps)
    extra = out["extra"]
    assert extra["asked"]["writes"] == 0
    assert extra["asked"]["lines"] == [": 放弃的理由（必填）", "panel-status err: 放弃一条路线时必须写明理由"]
    assert extra["rows"] == 2 and extra["after"] == {"closed": 1, "writes": 1, "offered": False}
    assert extra["label"] == "预览改动"
    steps = """
      renderAttemptForm("update", %(id)s);
      let [number, date, description, variables, result, verdict] = formFields();
      result.value = "t=3.10";
      await submit();
      global.extra = { posted: posted.length, lines: panelLines().filter((l) => l.startsWith("panel-status")),
                       label: panelPart("panel-body").all().find((k) => k.tag === "BUTTON" && k.type === "submit").textContent };
    """ % {"id": json.dumps(A16)}
    out = _form_run(steps, role="member")
    assert out["extra"] == {"posted": 0, "lines": ["panel-status err: 请用一句话说明这份提议"], "label": "预览这一行"}


@needs_node
def test_18_3_the_older_form_shows_the_row_when_the_file_moved_and_says_why_撤销_is_refused():
    """Conflict-checked in the older form too: a cell the file holds that the form did not show is previewed with
    its note and written only on 保存修改 (then 已保存 and 撤销, the form open); a refused write says the
    existing sentence and keeps the typed text; 撤销 after the file moved on writes nothing."""
    steps = """
      renderAttemptForm("update", %(id)s);
      const [number, date, description, variables, result, verdict] = formFields();
      file.result = "**t=2.95**";
      result.value = "t=3.10";
      await submit();
      const held = { writes: posted.filter(([path]) => path === "/api/attempts/write").length,
                     note: panelLines().includes("confirm-note: 这一行在你打开它以后被改过：上面的原行是文件里现在的样子。") };
      find("保存修改", "BUTTON").click(); await flush();
      const written = { file: { ...file }, offered: !!find("撤销", "BUTTON"), closed: global.closed || 0 };
      file.result = "typed in the editor";
      find("撤销", "BUTTON").click(); await flush();
      const refused = panelLines().filter((l) => l.startsWith("panel-status"));
      file.result = "t=3.10"; refuse = "attempt_not_found";
      result.value = "t=5";
      await submit();
      global.extra = { held, written, refused, failed: panelLines().filter((l) => l.startsWith("panel-status")), typed: result.value,
                       writes: posted.filter(([path]) => path === "/api/attempts/write").map(([, b]) => b.fields.result) };
    """ % {"id": json.dumps(A16)}
    out = _form_run(steps)
    extra = out["extra"]
    assert extra["held"] == {"writes": 0, "note": True}
    assert extra["written"] == {"file": {"verdict": "待定 先看", "result": "t=3.10"}, "offered": True, "closed": 0}
    assert extra["refused"] == ["panel-status err: 这一行之后又改过，不能撤销。"]
    assert extra["failed"] == ["panel-status err: 写入失败，文件没有被修改 (title: attempt_not_found)"] and extra["typed"] == "t=5"
    assert extra["writes"] == ["t=3.10", "t=5"]  # the first save, and the refused one -- not the 撤销


@needs_node
@pytest.mark.parametrize('role', ['single', 'pi', 'member'])
def test_research_prefill_uses_existing_attempt_form_without_a_verdict(role):
    shown = _form_run(r'''
state.panelSubject={researchSource:{prefill:{description:'Compare fictional samples',result:'A tentative difference',parents:['attempt:map.md#16']}}};
renderAttemptForm('append');
const fields=formFields();
const offset=ROTeam.mode()==='member'?1:0;
global.extra={description:fields[2+offset].value,result:fields[4+offset].value,verdict:fields[5+offset].value,posts:posted.length};
''', role=role, lang='en')
    assert shown['extra']['description'] == 'Compare fictional samples'
    assert shown['extra']['result'] == 'A tentative difference'
    assert shown['extra']['verdict'] == '' and shown['extra']['posts'] == 0


@needs_node
def test_research_attempt_callback_waits_for_successful_direct_write_and_actual_reloaded_id():
    shown = _form_run(r'''
let linked=null;
state.panelSubject={researchSource:{prefill:{description:'Compare fictional samples',result:'Still tentative'},onSaved:value=>linked=value.id}};
renderAttemptForm('append');
c.post['/api/attempts/preview']={file:'map.md',old_row:null,new_row:'Fictional new row'};
c.post['/api/attempts/write']={file:'map.md'};
global.ensureAttemptData=async()=>{state.attemptsById.set('attempt:map.md#17',{id:'attempt:map.md#17',attrs:{number:'17'}});return true;};
await submit();const before=linked;
find('保存修改','BUTTON').click();await flush();
global.extra={before,linked};
''')
    assert shown['extra'] == {'before': None, 'linked': 'attempt:map.md#17'}


@needs_node
@pytest.mark.parametrize('role', ['single', 'pi', 'member'])
def test_prefilled_attempt_sources_follow_existing_direct_or_member_proposal_flow(role):
    shown = _form_run(r'''
let linked=null;
state.panelSubject={researchSource:{prefill:{description:'Compare two samples',result:'Tentative',parents:['attempt:map.md#16']},onSaved:(value,details)=>linked={id:value.id,workingCopy:details.workingCopy}}};
renderAttemptForm('append');
if(ROTeam.mode()==='member') formFields()[1].value='Compare the pilot samples';
c.post['/api/attempts/preview']={file:'map.md',old_row:null,new_row:'Fictional new row'};
c.post['/api/attempts/write']={file:'map.md'};
global.ensureAttemptData=async()=>{state.attemptsById.set('attempt:map.md#17',{id:'attempt:map.md#17',attrs:{number:'17'}});return true;};
await submit();find(ROTeam.mode()==='member'?'保存后查看提议':'保存修改','BUTTON').click();await flush();
global.extra={linked,mappings:posted.filter(([path])=>path==='/api/mappings/add'),parents:composed[0]&&composed[0].parents};
''', role=role)
    assert shown['extra']['linked'] == {'id': A17, 'workingCopy': role == 'member'}
    if role == 'member':
        assert not shown['extra']['mappings'] and shown['extra']['parents'] == [A16]
    else:
        assert shown['extra']['mappings'] == [['/api/mappings/add', {'from': A17, 'to': A16, 'type': 'derives_from'}]]


@needs_node
def test_attempt_source_link_failure_does_not_offer_to_create_the_saved_attempt_again():
    shown = _form_run(r'''
state.panelSubject={researchSource:{prefill:{description:'Compare two samples',parents:['attempt:map.md#16']}}};
renderAttemptForm('append');c.post['/api/attempts/preview']={file:'map.md',old_row:null,new_row:'New row'};c.post['/api/attempts/write']={file:'map.md'};
c.post['/api/mappings/add']={refuse:'mapping_cycle'};
global.ensureAttemptData=async()=>{state.attemptsById.set('attempt:map.md#17',{id:'attempt:map.md#17',attrs:{number:'17'}});return true;};
await submit();find('保存修改','BUTTON').click();await flush();
global.extra={again:!!find('保存修改','BUTTON'),submitDisabled:panelBody.all().find(k=>k.tag==='BUTTON'&&k.type==='submit').disabled};
''')
    assert shown['extra'] == {'again': False, 'submitDisabled': True}
    assert any('尝试已保存，部分来源关联未成功' in line for line in shown['lines'])
    assert sum(path == '/api/attempts/write' for path, _ in shown['posted']) == 1

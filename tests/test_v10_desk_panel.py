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
from test_v9_language import CJK, WEBAPP, _i18n_source, _node, needs_node

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
global.distinctVerdicts = (records) => Array.from(new Set(records.map((r) => (r.verdict || "").trim()).filter(Boolean))).slice(0, 8);
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
    lines = [{"child": A17, "parent": A16, "note": "换轴", "date": "2026-10-12", "by": None,
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
                {"key": "out", "title": w("产出", "Outputs"), "items": [
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
                 "verdict-words: 先看", "dp-mark: 待复核 1", "btn btn-small: 打开详情页", "node-sec-title: 判决与结果",
                 "form-input:  = 待定 先看", "chip: 待定 先看", "chip: 已放弃 方向反了", "form-textarea:  = t=2.91",
                 "btn btn-primary: 预览改动", "node-sec-title: 写备注", "tl-note: 这里的缺失值要先处理", "desk-meta: Li · 2024年3月1日",
                 "form-textarea:  =  (写一条备注：为什么这样做、要注意什么)", "btn btn-small: 添加备注",
                 "node-sec-title: 来龙去脉", "trace-col-title: 读取的数据", "trace-item-name: raw.csv", "status-label tone-plain: 原始数据",
                 "status-label tone-plain: 步骤脚本", "status-label tone-clay: 文件不存在", "status-label tone-plain: 人工关联",
                 "node-sec-title: 源自", "desk-empty: 还没标来处", "node-sec-title: 由它而来", "desk-link: #17 事件轴",
                 "status-label tone-gray: 已放弃", "desk-note: 换轴", "desk-meta: 最初的研究者 · 2026-10-12", "btn btn-small: 删除这条连线"]:
        assert line in lines, line
    assert not any("撤回了" in line for line in lines)  # a withdrawn note is not shown
    assert not any("关联文件" in line or "还有文件属于" in line for line in lines)  # the node page's repairs stay there
    reason = [line for line in lines if "放弃的理由（必填）" in line]
    assert reason == []  # hidden until the preview says this write abandons the route


@needs_node
def test_14_6_the_panel_in_english_carries_no_chinese_but_the_researchers():
    """English mode: every word Research OS writes in the panel is English
    (13.4: Verdict, Result, Provenance, Grew out of, What grew out of it),
    the researcher's text as written; no arrows or emoji, no 节点 or 画布."""
    out = _run(_case("pi", "en"), "await window.RODesk.openAttempt(" + json.dumps(A16) + ");", lang="en")
    lines = [line.split(" = ")[0] if " = " in line and "(" not in line.split(" = ")[1] else line for line in out["lines"]]
    left = [line for line in lines if CJK.search(re.sub("|".join(map(re.escape, _THEIRS)), "", line))]
    assert left == [] and not any(FORBIDDEN.search(line) for line in lines)
    for line in ["status-label tone-ochre: Pending", "dp-mark: Awaiting your approval", "dp-mark: Needs review: 1",
                 "btn btn-small: Open the details page", "node-sec-title: Verdict and result", "chip: Abandoned 方向反了",
                 "btn btn-primary: Preview the change", "node-sec-title: Write a note", "desk-meta: Li · 1 Mar 2024",
                 "btn btn-small: Add note", "node-sec-title: Provenance", "trace-col-title: Data read",
                 "status-label tone-plain: Raw data", "status-label tone-plain: Step script", "status-label tone-clay: File not found",
                 "node-sec-title: Grew out of", "desk-empty: Origin not marked yet", "node-sec-title: What grew out of it",
                 "desk-meta: Original researcher · 2026-10-12", "btn btn-small: Delete this line"]:
        assert line in lines, line
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


@needs_node
def test_14_3_a_change_previews_then_writes_only_the_cells_changed_and_abandoning_asks_why():
    """The result changed alone: the preview and the write carry that cell
    only. Then an abandoning verdict: the engine's preview says the write
    turns the route abandoned, so the reason is asked before anything is
    shown; with it, the preview and the write carry it, and the panel is
    drawn again with what was written."""
    case = _case("single")
    row = {"file": "map.md", "old_row": "| 16 | 07-26 | … | t=2.91 | 待定 先看 |", "new_row": "| 16 | … |"}
    case["post"] = {"/api/attempts/write": {"ok": True, "file": "map.md", "ingest_error": None}}
    steps = """
      await window.RODesk.openAttempt(%(id)s);
      global.apiPost = ((post) => async (path, body) => path === "/api/attempts/preview"
        ? (posted.push([path, body]), Object.assign({ reason_required: (body.fields.verdict || "").startsWith("☠"), abandons: false }, %(row)s))
        : post(path, body))(global.apiPost);
      type(fields().find((f) => f.tag === "TEXTAREA" && f.value === "t=2.91"), "t=3.10");
      find("预览改动").click(); await settle(); await settle();
      find("确认写入").click(); await settle(); await settle(); await settle();
      find("已放弃 方向反了").click();
      find("预览改动").click(); await settle(); await settle();
      global.extra = panelLines().filter((l) => l.startsWith("notice-status") || l.includes("放弃的理由"));
      type(field("为什么放弃这条路线"), "残差不平稳");
      find("预览改动").click(); await settle(); await settle();
      find("确认写入").click(); await settle(); await settle(); await settle();
    """ % {"id": json.dumps(A16), "row": json.dumps(row, ensure_ascii=False)}
    out = _run(case, steps)
    posted = out["posted"]
    assert posted[0] == ["/api/attempts/preview", {"op": "update", "number": "16", "fields": {"result": "t=3.10"}}]
    assert posted[1] == ["/api/attempts/write", posted[0][1]]
    # The abandoning verdict, first without its reason: previewed, refused before anything is shown.
    assert posted[2] == ["/api/attempts/preview", {"op": "update", "number": "16", "fields": {"verdict": "☠ 方向反了"}}]
    assert posted[3] == ["/api/attempts/preview", {"op": "update", "number": "16", "fields": {"verdict": "☠ 方向反了"},
                                                   "reason": "残差不平稳"}]
    assert posted[4] == ["/api/attempts/write", posted[3][1]] and len(posted) == 5
    # Asked before the row was shown: the reason's field, said in its own sentence.
    assert out["extra"] == ["notice-status ok: 已写入 map.md。", ": 放弃的理由（必填）", "notice-status err: 放弃一条路线时必须写明理由"]
    assert "notice-status ok: 已写入 map.md。" in out["lines"]
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
      find("已放弃 方向反了").click();
      find("预览这一行").click(); await settle();
      const noSentence = panelLines().filter((l) => l.startsWith("notice-status err"));
      type(field("用你自己的话说明这份提议：PI 会先看到这句话"), "放弃 16");
      type(field("为什么放弃这条路线"), "残差不平稳");
      find("预览这一行").click(); await settle(); await settle();
      find("写入工作副本，查看提议").click(); await settle(); await settle(); await settle();
      answers.push(true);
      find("提议删除这条连线").click(); await settle(); await settle(); await settle();
      global.extra = { noSentence, before };
    """ % {"id": json.dumps(A16)}
    out = _run(case, steps)
    assert out["posted"][0] == ["/api/attempts/preview", {"op": "update", "number": "16", "fields": {"verdict": "☠ 方向反了"}}]
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
    assert [c["title"] for c in zh["trace"]["columns"]] == ["读取的数据", "脚本", "产出"]
    assert [c["title"] for c in en["trace"]["columns"]] == ["Data read", "Scripts", "Outputs"]

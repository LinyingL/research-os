"""Research OS V10, review fixes on the route map and around it (DESIGN.md
14.1, 14.2, 14.6, 8.8): the confirmed findings of the V10 review that a
person meets on the map -- each with the scenario that would have caught
it, the map driven under node through a stand-in DOM (pointer, keys and
the line's card), the engine through its own calls and HTTP.

Every test names its scenario."""

from __future__ import annotations

import json
import re

import pytest

from researchos import cli, proposals
from researchos.ingest import mappings as mappings_ingest

from test_v8_phase2 import _call, _project, _scan
from test_v8_phase3 import _serve
from test_v9_language import CJK, WEBAPP, _i18n_source, _node, english_table, needs_node
from test_v10_derives_from import A16, A17, A18, _map

#: A DOM just big enough for one route map: elements with classes, styles,
#: children and listeners; `closest` / `querySelector(All)` for the class
#: selectors routemap.js asks; a 1200 x 800 box; an SVG text measured at 7px
#: a character.
_MAP_DOM = r"""
function el(tag, cls, text) {
  const e = { tag: String(tag).toUpperCase(), kids: [], parentNode: null, dataset: {}, attrs: {}, style: {}, on: {},
    title: "", value: "", placeholder: "", type: "", maxLength: 0, cls: new Set(), offsetWidth: 200, offsetHeight: 100,
    get className() { return Array.from(this.cls).join(" "); },
    set className(v) { this.cls = new Set(String(v || "").split(" ").filter(Boolean)); },
    get classList() { const s = this.cls; return { add: (...cs) => cs.forEach((c) => s.add(c)), remove: (...cs) => cs.forEach((c) => s.delete(c)),
      contains: (c) => s.has(c), toggle: (c, on) => { const want = on === undefined ? !s.has(c) : !!on; if (want) s.add(c); else s.delete(c); return want; } }; },
    get textContent() { return this.kids.map((k) => (typeof k === "string" ? k : k.textContent)).join(""); },
    set textContent(v) { this.kids.forEach((k) => { if (typeof k !== "string") k.parentNode = null; }); this.kids = v === "" || v == null ? [] : [String(v)]; },
    appendChild(c) { if (c.parentNode) c.remove(); c.parentNode = this; this.kids.push(c); return c; },
    append(...cs) { cs.forEach((c) => (typeof c === "string" ? this.kids.push(c) : this.appendChild(c))); },
    remove() { if (this.parentNode) this.parentNode.kids = this.parentNode.kids.filter((k) => k !== this); this.parentNode = null; },
    contains(n) { while (n) { if (n === this) return true; n = n.parentNode; } return false; },
    get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === body; },
    addEventListener(type, fn) { (this.on[type] = this.on[type] || []).push(fn); },
    setAttribute(k, v) { if (k === "class") this.className = v; else this.attrs[k] = String(v); },
    getAttribute(k) { return k === "class" ? this.className : k in this.attrs ? this.attrs[k] : null; },
    getClientRects() { return [1]; }, getBoundingClientRect() { return { width: 1200, height: 800, left: 0, top: 0 }; },
    setPointerCapture() {}, releasePointerCapture() {}, focus() {},
    getComputedTextLength() { return this.textContent.length * 7; },
    all() { const out = []; const walk = (n) => n.kids.forEach((k) => { if (typeof k !== "string") { out.push(k); walk(k); } }); walk(this); return out; },
    matches(sel) { return sel.split(",").some((one) => { const m = one.trim().match(/^((?:\.[\w-]+)+)(?:\[data-id="(.*)"\])?$/);
      return !!m && m[1].split(".").filter(Boolean).every((c) => this.cls.has(c)) && (m[2] === undefined || this.dataset.id === m[2]); }); },
    closest(sel) { let n = this; while (n) { if (n.cls && n.matches(sel)) return n; n = n.parentNode; } return null; },
    querySelectorAll(sel) { return this.all().filter((k) => k.matches(sel)); },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
    click() { (this.on.click || []).forEach((f) => f({ preventDefault() {}, stopPropagation() {} })); },
  };
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
const body = el("body");
const panel = body.appendChild(el("div", "hidden"));
const keys = [];
global.document = { createElement: (tag) => el(tag), createElementNS: (ns, tag) => el(tag),
  addEventListener: (type, fn) => { if (type === "keydown") keys.push(fn); }, getElementById: (id) => (id === "panel" ? panel : null) };
global.CSS = { escape: (s) => s };
global.asked = [];
window.confirm = (q) => { asked.push(q); return true; };
global.posted = [];
global.refuse = null;
global.apiPost = async (path, b) => { posted.push([path, b]); if (refuse) throw Object.assign(new Error("refused"), { state: refuse }); return { ok: true }; };
global.settle = () => new Promise((r) => setTimeout(r, 0));
const host = body.appendChild(el("div", "desk-map"));
global.map = window.RORouteMap.create({ onOpen: (id) => opened.push(id), onChanged: async () => {} });
global.opened = [];
map.mount(host);
const root = host.kids[0];
global.root = root;
global.card = (id) => root.querySelector('.rm-card[data-id="' + id + '"]');
// Where a world point is on screen: the world's own transform.
global.screen = (x, y) => { const m = root.querySelector(".rm-world").style.transform.match(/translate\((.*)px, (.*)px\) scale\((.*)\)/);
  return { x: x * Number(m[3]) + Number(m[1]), y: y * Number(m[3]) + Number(m[2]) }; };
const at = (c) => ({ x: parseFloat(c.style.left), y: parseFloat(c.style.top) });
const event = (target, p, extra) => Object.assign({ button: 0, target, clientX: p.x, clientY: p.y, pointerId: 1, type: "pointermove",
  preventDefault() {}, stopPropagation() {} }, extra || {});
const fire = (type, e) => (root.on[type] || []).forEach((f) => f(Object.assign(e, { type })));
// A drag from `from`'s handle to the middle of `to` (or a point), released there -- or not.
global.dragLine = (from, to, o) => {
  o = o || {};
  const handle = card(from).querySelector(".rm-handle"), a = at(card(from));
  const start = screen(a.x + 232, a.y + 44);
  fire("pointerdown", event(handle, start));
  const end = typeof to === "string" ? (() => { const b = at(card(to)); return screen(b.x + 100, b.y + 44); })() : to;
  (o.via || []).forEach((p) => fire("pointermove", event(root, p)));
  fire("pointermove", event(root, end));
  if (o.key) keys.forEach((f) => f({ key: o.key, target: body, preventDefault() {} }));
  if (o.up !== false) fire("pointerup", event(root, end));
};
global.clickLine = (child, parent) => {
  const g = root.querySelectorAll(".rm-line").find((x) => x.getAttribute("data-key") === child + "\n" + parent);
  fire("pointerdown", event(g.kids[0], { x: 5, y: 5 }));
  fire("pointerup", event(g.kids[0], { x: 5, y: 5 }));
};
global.texts = (sel) => root.querySelectorAll(sel).map((n) => n.textContent);
global.status = () => { const s = root.querySelector(".cv-status"); return s.cls.has("hidden") ? null : s.querySelector(".err-text").textContent; };
"""

A19 = "attempt:map.md#19"


def _st(status: str, label: str) -> dict:
    return {"status": status, "label": label, "words": ""}


def _desk(role: str, *, lines=(), notice=None, mine=False) -> dict:
    """GET /api/desk for #16, #17 and #18 (official) and, when `mine`, the
    member's own new row #19 (我的改动，尚未提交)."""
    official = {"state": "official"}
    attempts = [{"id": a, "number": a.rpartition("#")[2], "title": "Route " + a.rpartition("#")[2], "date": "07-2" + str(i),
                 "route_status": _st("pending", "待定"), "tone": "ochre", "abandoned": False, "officialness": official,
                 "marks": {"proposals": [], "review": None}} for i, a in enumerate((A16, A17, A18))]
    if mine:
        attempts.append({**attempts[0], "id": A19, "number": "19", "title": "Mine",
                         "officialness": {"state": "mine", "label": "我的改动，尚未提交"}})
    return {"role": role, "attempts": attempts, "layout": {"positions": {}, "project": "p-1", "writable": True},
            "lines": [{"child": c, "parent": p, "note": "", "date": "2026-10-12", "by": None, "by_label": "李",
                       "pending": None, "officialness": official} for c, p in lines],
            "column": {"role": role, "notice": notice}}


def _run_map(desk: dict, steps: str, lang: str | None = None) -> dict:
    """routemap.js (after /i18n.js in `lang`) mounted on the stand-in DOM,
    fed `desk`, then `steps`; what it posted, asked, opened, and the
    status, the popover and the line card's words."""
    i18n = "" if lang is None else f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    script = ("global.window = {};\n" + i18n + (WEBAPP / "routemap.js").read_text(encoding="utf-8") + "\n" + _MAP_DOM
              + f"(async () => {{ map.update({json.dumps(desk, ensure_ascii=False)}, {{ changed: () => false }});\n{steps}\n"
              " process.stdout.write(JSON.stringify({ posted, asked, opened, status: status(), extra: global.extra || null,"
              " pop: texts('.cv-pop-line').concat(texts('.rm-pop-sub')), buttons: texts('.cv-linkcard button'),"
              " linking: root.cls.has('linking') })); })();\n")
    return _node(script)


# -- 6, 18, 23: a line while team.toml cannot be read ---------------------------------------------------


@pytest.fixture
def lines_root(tmp_path):
    root = _project(tmp_path / "proj")
    (root / "map.md").write_text(_map())
    _scan(root)
    mappings_ingest.add_mapping(root, A17, A16, "derives_from")
    return root


def _unreadable(root, how: str) -> None:
    team = root / ".researchos" / "team.toml"
    if how == "invalid":
        team.write_text("[[member]\nbroken\n")
    else:  # a sync conflict copy beside a good file
        team.write_text('[[member]]\nid = "li@example.com"\nname = "李"\nrole = "pi"\n')
        (root / ".researchos" / "team (1).toml").write_text(team.read_text())
    assert proposals.role_of(root)[0] == "unknown"


@pytest.mark.parametrize("how", ["invalid", "conflict_copy"])
def test_14_2_while_the_team_file_cannot_be_read_a_line_is_neither_drawn_nor_removed_directly(lines_root, how, capsys):
    """Ruled 2026-10-13: whose copy this is cannot be told, so a 「源自」 line
    is not written directly -- by the engine, the CLI or the endpoints --
    with its own code and state; nothing is written. Another kind of
    mapping still lands."""
    root = lines_root
    _unreadable(root, how)
    path = mappings_ingest.mappings_path(root)
    before = path.read_bytes()
    for write in (lambda: mappings_ingest.add_mapping(root, A18, A16, "derives_from"),
                  lambda: mappings_ingest.delete_mapping(root, A17, A16, "derives_from")):
        with pytest.raises(mappings_ingest.MappingsWriteError) as refused:
            write()
        assert refused.value.code == "role_unknown"
    assert cli.main(["link", A18, A16, "--type", "derives_from", str(root)]) == 1
    assert cli.main(["link", A17, A16, "--type", "derives_from", "--remove", str(root)]) == 1
    assert "role_unknown" in capsys.readouterr().err
    with _serve(root) as base:
        for endpoint, body in (("add", {"from": A18, "to": A16, "type": "derives_from"}),
                               ("delete", {"from": A17, "to": A16, "type": "derives_from"})):
            status, out = _call(f"{base}/api/mappings/{endpoint}", "POST", body)
            assert (status, out["state"]) == (400, "mapping_role_unknown")
    assert path.read_bytes() == before
    mappings_ingest.add_mapping(root, "figs/fig1.png", A16, "belongs_to")  # not a line between attempts: as before
    assert "belongs_to" in path.read_text()


def test_14_2_with_the_team_file_readable_the_pi_draws_and_a_member_proposes(lines_root, monkeypatch):
    """The rule's other side, unchanged: a single researcher and the PI draw
    directly; a member is told to propose (member_proposes)."""
    from researchos import people

    root = lines_root
    mappings_ingest.add_mapping(root, A18, A16, "derives_from")  # a single researcher
    (root / ".researchos" / "team.toml").write_text(
        '[[member]]\nid = "li@example.com"\nname = "李"\nrole = "pi"\n\n'
        '[[member]]\nid = "wang@example.com"\nname = "王"\nrole = "member"\n')
    monkeypatch.setattr(people, "current_member_id", lambda: "li@example.com")
    assert proposals.role_of(root)[0] == "pi"
    mappings_ingest.delete_mapping(root, A18, A16, "derives_from")
    monkeypatch.setattr(people, "current_member_id", lambda: "wang@example.com")
    with pytest.raises(mappings_ingest.MappingsWriteError) as refused:
        mappings_ingest.add_mapping(root, A18, A16, "derives_from")
    assert refused.value.code == "member_proposes"


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_14_2_the_map_offers_no_line_while_the_role_cannot_be_told(lang):
    """Role unknown: a drag from #16 onto #18 opens no popover and says the
    column's notice instead; the line card offers no 删除这条连线; the
    refusals team_unreadable and mapping_role_unknown have sentences of
    their own, in both languages. Nothing is posted."""
    notice = "成员文件（team.toml）当前无法读取，无法判断谁是 PI" if lang == "zh" else \
        "The members file (team.toml) cannot be read right now, so who the PI is cannot be told"
    steps = """
      dragLine(%(a16)s, %(a18)s);
      global.extra = { after_drag: status() };
      clickLine(%(a17)s, %(a16)s);
      extra.refusals = ["team_unreadable", "mapping_role_unknown"].map(window.RORouteMap.lineRefusal);
    """ % {"a16": json.dumps(A16), "a18": json.dumps(A18), "a17": json.dumps(A17)}
    out = _run_map(_desk("unknown", lines=[(A17, A16)], notice=notice), steps, lang=lang)
    assert out["pop"] == [] and out["posted"] == [] and out["extra"]["after_drag"] == notice
    assert out["buttons"] == []  # the line card says what the line is, and offers nothing
    zh = ["成员文件（team.toml）当前无法读取，无法判断谁是 PI", "成员文件（team.toml）当前无法读取，无法判断你能否直接画或删这条连线"]
    assert out["extra"]["refusals"] == (zh if lang == "zh" else [english_table()[s] for s in zh])
    if lang == "en":
        assert not any(CJK.search(s) for s in out["extra"]["refusals"])
    without = _run_map(_desk("unknown", lines=[(A17, A16)]), steps, lang=lang)  # no notice: the map's own sentence
    assert without["extra"]["after_drag"] == out["extra"]["refusals"][0]


# -- 5: a 源自 line on the node page -----------------------------------------------------------------------


_LINK = {"src": A17, "dst": A16, "type": "derives_from", "human": True, "relation": "源自", "src_label": "#17",
         "dst_label": "#16", "status": "confirmed", "extractor": "mapping"}


def _node_page_link(mode: str, steps: str = "") -> dict:
    """node.js's linkRow for #17 源自 #16 as `mode` sees it on #17's page,
    then `steps`."""
    from test_v10_desk_panel import _PANEL_DOM

    script = ("global.window = {};\n" + _PANEL_DOM + (WEBAPP / "node.js").read_text(encoding="utf-8")
              + "\nglobal.RODesk = { context: () => ({ mode: %s }) };\n" % json.dumps(mode)
              + "global.humanRecordsBlocked = () => null;\nglobal.markViewsStale = () => {};\n"
              + "global.apiPost = async (path, body) => { posted.push([path, body]); return { ok: true }; };\n"
              + "window.RORouteMap = { lineRefusal: (code) => code === 'mapping_member_proposes' ? 'MAP SAYS' : null };\n"
              + "global.RORouteMap = window.RORouteMap;\n"
              + f"(async () => {{ const li = window.RONode._linkRow({{ id: {json.dumps(A17)}, type: 'attempt' }}, "
              + json.dumps(_LINK, ensure_ascii=False) + ");\n"
              + "  const buttons = () => li.all().filter((k) => k.tag === 'BUTTON' && k.cls.has('btn'));\n"
              + "  global.extra = { buttons: buttons().map((b) => b.textContent) };\n" + steps
              + "\n  extra.status = li.all().filter((k) => k.cls.has('notice-status')).map((k) => k.className + ': ' + k.textContent);\n"
              + "  process.stdout.write(JSON.stringify({ extra, asked, posted })); })();\n")
    return _node(script)


@needs_node
def test_14_2_a_members_node_page_proposes_removing_a_line_and_the_pi_deletes_it():
    """A member's node page offers 「提议删除这条连线」 -- asked with the
    map's question, sent as a route_link removal, never the travels-
    without-approval sentence; the PI and a single researcher delete it
    directly; someone not in the team, or with team.toml unreadable, is
    offered nothing."""
    member = _node_page_link("member", "  buttons()[0].click(); await settle(); await settle();")
    assert member["extra"]["buttons"] == ["提议删除这条连线"]
    assert member["asked"] == ["提议删除这条连线？PI 批准后它才会从路线图上消失。"]
    assert member["posted"] == [["/api/proposals", {"kind": "route_link", "op": "remove", "child": A17, "parent": A16,
                                                    "write": True}]]
    assert member["extra"]["status"] == ["notice-status ok: 已把删除这条连线作为提议交给 PI。"]
    for mode in ("pi", "single"):
        direct = _node_page_link(mode, "  window.confirm = (q) => { asked.push(q); return false; }; buttons()[0].click();"
                                       " await settle();")
        assert direct["extra"]["buttons"] == ["删除标注"] and "不需要批准" not in direct["asked"][0]
    for mode in ("none", "unknown"):
        assert _node_page_link(mode)["extra"]["buttons"] == []
    refused = _node_page_link("member", "  global.apiPost = async () => { throw Object.assign(new Error('x'),"
                                        " { state: 'mapping_member_proposes' }); };\n"
                                        "  buttons()[0].click(); await settle(); await settle();")
    assert refused["extra"]["status"] == ["notice-status err: MAP SAYS"]


@needs_node
def test_8_8_a_members_line_refusal_and_an_unknown_role_have_their_own_sentences():
    """blockingErrorText says mapping_member_proposes -- worded for removing
    as well as drawing -- and mapping_role_unknown, each with English."""
    from test_webapp_canvas_js import _blocking_error_source

    states = ["mapping_member_proposes", "mapping_role_unknown"]
    said = _node("global.window = {};\n" + _blocking_error_source()
                 + f"\nprocess.stdout.write(JSON.stringify({json.dumps(states)}.map((s) => blockingErrorText('F', {{ state: s }}))));\n")
    assert said == ["你是这个项目的成员：连线的增删要作为提议交给 PI 批准",
                    "成员文件（team.toml）当前无法读取，无法判断你能否直接画或删这条连线"]
    table = english_table()
    assert all(s in table and not CJK.search(table[s]) for s in said)
    assert re.search(r'STATED = \(.*"role_unknown"\)', (WEBAPP / "server.py").read_text(encoding="utf-8"))

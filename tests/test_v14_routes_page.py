"""Research OS V14, task G3-1 (DESIGN.md 18.3): 研究路线 is one page. The route
map of Section 14 and the grouped list that 12.9 called 研究路线 are two ways
of looking at the same attempts, switched by 路线图 / 列表 in the page's
header, with the same 等你处理 column (21.4; it was 待办) beside either, folded to a strip when nothing waits; an
attempt opened from the list opens the desk's side panel; the way of looking is kept per project in
the browser; the sidebar has one item for it; everything that opened 工作台 or the old list opens it. The count of
what waits shows beside 项目首页, in a project of one too (21.4).
desk.js, team.js and node.js run under node with the stand-in DOM of the
desk's other tests; app.html is read as source where it is the thing under
test. Every test names its scenario."""

from __future__ import annotations

import json
import re

import pytest

from test_v9_language import APP_HTML, WEBAPP, _app_function, _node, needs_node
from test_v10_desk_panel import A16, A17, A18, _case, _st, with_needs
from test_v10_desk_proposals import _run_team, _team_case


def _routes() -> dict:
    """/api/routes as team.js draws it: the current routes, then an abandoned one with its reason."""

    def item(node: str, number: int, title: str, status: dict, **more) -> dict:
        return {"id": node, "number": str(number), "date": "07-26", "title": title, "result": "t=2.91", "verdict_status": status, **more}

    return {"groups": [
        {"status": "current", "label": "现行", "items": [item(A16, 16, "TopicShift 波动", _st("current", "现行")),
                                                          item(A18, 18, "Plain panel", _st("current", "现行"))]},
        {"status": "abandoned", "label": "已放弃", "items": [item(A17, 17, "事件轴", _st("abandoned", "已放弃", "方向反了"), reason="无信号")]}],
        "decisions_problem": None}


#: Before the steps: a class selector the stand-in DOM does not know, a browser's storage that keeps what it is
#: given, and the route list the page reads.
_PRELUDE = r"""
const mkBefore = global.mk;
global.mk = (tag, cls, text) => { const e = mkBefore(tag, cls, text); const q = e.querySelectorAll;
  e.querySelectorAll = (sel) => sel.startsWith(".") ? e.all().filter((k) => k.cls.has(sel.slice(1))) : q.call(e, sel);
  return e; };
global.state.attemptsConfig = true;
global.clearProjectState = () => {};
global.store = new Map();
window.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => { store.set(k, String(v)); } };
global.layout = () => views.desk.kids[0];
global.kinds = () => layout().kids.map((k) => k.className);
global.rows = () => layout().all().filter((k) => k.cls.has("route-row"));
global.title = (number) => layout().all().find((k) => k.tag === "BUTTON" && k.cls.has("route-title") && k.kids.join("") === number);
global.reads = (path) => gets.filter((g) => g === path).length;
"""


def _run(case: dict, steps: str, lang: str | None = None) -> dict:
    case["get"]["/api/routes"] = _routes()
    return _run_team(case, _PRELUDE + steps, lang=lang)


@needs_node
def test_18_3_the_page_opens_on_the_map_and_the_choice_is_kept_per_project_in_the_browser():
    """Nothing kept: the route map. 列表 chosen: kept under a key that names
    the project, and read back by a page that starts afresh; another
    project is on the map; a value that is neither mode is the map; a
    browser whose storage throws still switches (for this page's life) and
    never fails."""
    out = _run(_case("single"), r"""
      global.extra = { start: window.RODesk.mode(), key: window.RODesk._modeKey("p-1") };
      window.RODesk.setMode("list");
      extra.stored = Array.from(store.entries());
      extra.now = window.RODesk.mode();
      state.currentId = "p-2";
      extra.other = window.RODesk.mode();
      window.RODesk.setMode("map");
      state.currentId = "p-1";
      extra.back = window.RODesk.mode();
      extra.refused = window.RODesk.setMode("table");
      store.clear(); store.set("ro.routes.mode:p-3", "table"); store.set("ro.routes.mode:p-4", "list");
      state.currentId = "p-3"; extra.junk = window.RODesk.mode();
      state.currentId = "p-4"; extra.kept = window.RODesk.mode();
      window.localStorage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
      state.currentId = "p-5"; extra.blocked = window.RODesk.mode();
      window.RODesk.setMode("list"); extra.blockedNow = window.RODesk.mode();
    """)["extra"]
    assert out["start"] == "map" and out["key"] == "ro.routes.mode:p-1"
    assert out["stored"] == [["ro.routes.mode:p-1", "list"]] and out["now"] == "list"
    assert out["other"] == "map" and out["back"] == "list"  # the other project's choice does not move this one's
    assert out["refused"] is None
    assert out["junk"] == "map" and out["kept"] == "list"
    assert out["blocked"] == "map" and out["blockedNow"] == "list"


@needs_node
def test_18_3_the_list_stands_beside_the_same_column_and_the_map_is_kept_while_it_is_hidden():
    """On the map, the page is the map and the column (no list is made, and
    /api/routes is not read). Switched to 列表: the map is hidden, the
    grouped list -- by status, the abandoned route with its result and
    reason -- is in a pane between it and the column, which is the same
    element; switched back, the list is hidden and the map shown. A refresh
    of the page reads the list again in place. With nothing waiting the column is
    the folded strip (21.4): its title, 等你处理, and the sentence on hover."""
    out = _run(_case("single"), r"""
      global.extra = { map: kinds(), mapReads: reads("/api/routes") };
      const column = layout().kids[layout().kids.length - 1];
      window.RODesk.setMode("list"); await settle();
      extra.list = kinds();
      extra.sameColumn = layout().kids[layout().kids.length - 1] === column;
      extra.listReads = reads("/api/routes");
      extra.groups = layout().all().filter((k) => k.cls.has("route-group-title")).map((k) => k.textContent);
      extra.rows = rows().map((k) => k.dataset.node);
      extra.facts = layout().all().filter((k) => k.cls.has("route-fact")).map((k) => k.textContent);
      extra.columnTitle = column.all().filter((k) => k.cls.has("desk-column-title")).map((k) => k.textContent);
      extra.empty = [column.title, column.all().filter((k) => k.cls.has("desk-empty")).length];
      await window.RODesk.loadDesk(views.desk, true); await settle();
      extra.afterRefresh = [reads("/api/routes"), kinds()];
      window.RODesk.setMode("map"); await settle();
      extra.back = kinds();
    """)["extra"]
    assert out["map"] == ["desk-map", "desk-column folded"] and out["mapReads"] == 0
    assert out["list"] == ["desk-map hidden", "routes-pane", "desk-column folded"] and out["sameColumn"] is True
    assert out["listReads"] == 1
    assert out["groups"] == ["现行", "已放弃"] and out["rows"] == [A16, A18, A17]
    assert out["facts"] == ["结果：t=2.91", "理由：无信号"]
    assert out["columnTitle"] == ["等你处理"] and out["empty"] == ["现在没有要处理的事。", 0]
    assert out["afterRefresh"] == [2, ["desk-map hidden", "routes-pane", "desk-column folded"]]
    assert out["back"] == ["desk-map", "routes-pane hidden", "desk-column folded"]


@needs_node
def test_18_3_an_attempt_opened_from_the_list_opens_the_same_side_panel_as_the_map():
    """The title of a route in the list opens the desk's panel for that
    attempt -- the subject the map's card opens (no backdrop; its head,
    the verdict and result to change) -- and not the node page; the node
    page is still one click away inside the panel."""
    out = _run(_case("single"), r"""
      window.RODesk.setMode("list"); await settle();
      title("TopicShift 波动").click(); await settle();
      global.extra = { fromList: [state.panelSubject.kind, state.panelSubject.id], opened: opened.slice(),
                       head: panelLines().slice(0, 2), backdrop: panelPart("panel-backdrop").className,
                       shown: !panelPart("panel").cls.has("hidden") };
      const listLines = panelLines();
      state.panelSubject = null; panelPart("panel").classList.add("hidden");
      window.RODesk.setMode("map");
      await window.RODesk.openAttempt(%(a16)s);
      extra.fromMap = [state.panelSubject.kind, state.panelSubject.id];
      extra.same = JSON.stringify(panelLines()) === JSON.stringify(listLines);
      find("打开详情页", "BUTTON").click();
      extra.node = opened.slice();
    """ % {"a16": json.dumps(A16)})["extra"]
    assert out["fromList"] == ["desk-attempt", A16] and out["fromMap"] == ["desk-attempt", A16]
    assert out["opened"] == [] and out["shown"] is True  # the panel, not the node page
    assert out["head"] == ["panel-title plain-title: #16 TopicShift 波动", "panel-subtitle: 07-26"]
    assert out["backdrop"] == "hidden" and out["same"] is True
    assert out["node"] == [A16]


@needs_node
def test_18_3_a_picked_row_of_the_column_marks_its_attempts_in_the_list_not_on_the_hidden_map():
    """In list mode, picking a row of 等你处理 marks the rows of the attempts it
    touches (picked again, unmarks them) and leaves the hidden map alone;
    the hint on the row names the list, not the route map. The row stays
    marked when the list is read again."""
    out = _run(_team_case("pi"), r"""
      window.RODesk.setMode("list"); await settle();
      const before = highlighted.length;
      const hint = () => row("p:p1").all().find((k) => k.tag === "BUTTON" && k.title);
      const marked = () => rows().filter((k) => k.cls.has("picked")).map((k) => k.dataset.node);
      global.extra = { hint: hint().title };
      row("p:p1").all().find((k) => k.cls.has("desk-link")).click();
      extra.picked = marked();
      await window.RODesk.loadDesk(views.desk, true); await settle();
      extra.afterRefresh = marked();
      row("p:p1").all().find((k) => k.cls.has("desk-link")).click();
      extra.unpicked = marked();
      extra.mapTouched = highlighted.length - before;
      row("p:p1").all().find((k) => k.cls.has("desk-link")).click();
      window.RODesk.setMode("map"); await settle();
      extra.backOnMap = highlighted[highlighted.length - 1];
      extra.hintOnMap = hint().title;
    """)["extra"]
    assert out["hint"] == "在列表中标出它涉及的尝试"
    assert out["picked"] == [A16] and out["afterRefresh"] == [A16] and out["unpicked"] == []
    assert out["mapTouched"] == 0  # the hidden map is left alone
    assert out["backOnMap"] == [A16] and out["hintOnMap"] == "在路线图上标出它涉及的尝试"


@needs_node
def test_18_3_a_page_set_to_the_list_while_another_view_shows_is_drawn_as_the_list_when_it_comes_back():
    """`setMode` from a caller that means the list (the old tree command,
    the abandoned routes) while another section shows only keeps the mode;
    when the page is shown again (`resume`), it is the list, read once."""
    out = _run(_case("single"), r"""
      global.currentView = "results";
      window.RODesk.setMode("list");
      global.extra = { away: kinds(), reads: reads("/api/routes") };
      global.currentView = "desk";
      await window.RODesk.resume(); await settle();
      extra.back = kinds(); extra.readsBack = reads("/api/routes");
      global.currentView = "results"; global.currentView = "desk";
      await window.RODesk.resume(); await settle();
      extra.again = reads("/api/routes");
    """)["extra"]
    assert out["away"] == ["desk-map", "desk-column folded"] and out["reads"] == 0
    assert out["back"] == ["desk-map hidden", "routes-pane", "desk-column folded"] and out["readsBack"] == 1
    assert out["again"] == 1  # drawn already for this project: not read again for coming back


@needs_node
def test_18_3_the_text_zoom_reaches_the_list_even_when_it_is_made_after_the_zoom():
    """放大 / 缩小 on the page as a list scale the list like the reading
    pages (the map keeps its own camera): a zoom set before the list exists
    is there when it is made, and a reset clears it."""
    out = _run(_case("single"), r"""
      window.RODesk.listZoom("1.1");
      window.RODesk.setMode("list"); await settle();
      global.extra = { made: layout().kids[1].style.zoom };
      window.RODesk.listZoom("");
      extra.reset = layout().kids[1].style.zoom;
    """)["extra"]
    assert out == {"made": "1.1", "reset": ""}


# -- the sidebar's count of 等你处理 (DESIGN.md 21.4) ----------------------------------------------------


_SIDEBAR_STEPS = r"""
function fake() { return { textContent: "", title: "", kids: [], dataset: {}, hidden: false, classList: { toggle() {} }, append(...xs) { this.kids.push(...xs); } }; }
global.shown = { "side-count-proposals": fake(), "side-count-home": fake(), "side-count-routes": fake(), "share-line": fake(), "me-line": fake(), "new-attempt-btn": fake() };
const parts0 = document.getElementById;
document.getElementById = (id) => shown[id] || parts0(id);
const count = () => [shown["side-count-home"].textContent, shown["side-count-home"].title, shown["side-count-proposals"].textContent];
await window.RODesk.ensureContext();
window.RODesk.renderSidebar();
"""


def _sidebar(case: dict, lang: str | None = None, after: str = "") -> dict:
    return _run(case, _SIDEBAR_STEPS + "global.extra = { count: count() };\n" + after, lang=lang)["extra"]


_REVIEWED = {"src": "script:steps/16-build.py", "dst": "dataset:data/panel.csv", "type": "reads", "extractor": "python-ast",
             "outcome": "review", "reason": "basis_changed", "label": "依据已变化", "src_label": "16-build.py", "dst_label": "panel.csv"}


def _with_review(case: dict) -> dict:
    """A judgment to review under #16 and one that belongs to no attempt: two rows of 等你处理."""
    case["desk"]["column"]["review"] = {
        "count": 2, "groups": [{"id": A16, "items": [{**_REVIEWED, "attempts": [A16]}], "officialness": {"state": "official"}}],
        "none": {"items": [{**_REVIEWED, "src": "script:other.py", "attempts": []}], "cards": []}}
    return case


def _with_work(case: dict) -> dict:
    """The PI's column with 3 proposals waiting, a judgment to review under #16, one that belongs to no attempt and a line with
    a missing end: six rows."""
    _with_review(case)
    case["desk"]["lines_missing"] = [{"child": A18, "parent": A16, "child_number": "20", "parent_number": "16", "missing": ["child"],
                                      "note": "", "by_label": "x", "officialness": {"state": "official"}}]
    return with_needs(case)


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_21_4_the_count_of_what_waits_shows_beside_project_home_in_a_team_project(lang):
    """The PI's list holds 3 proposals, 2 rows of judgments to review and a line with a missing end: the sidebar's 项目首页 reads
    6, with the hover title in the screen's language, beside the 提议 count (3) which stays; 研究路线 carries no count of its own
    (18.3's is gone: one count, not two)."""
    out = _sidebar(_with_work(_team_case("pi", lang)), lang=None if lang == "zh" else lang, after="extra.routes = shown['side-count-routes'].textContent;")
    assert out["count"] == ["6", "6 项等你处理" if lang == "zh" else "6 need you", "3"] and out["routes"] == ""


@needs_node
def test_21_4_a_members_count_is_their_own_waiting_proposals_the_review_and_a_newer_official_version():
    """A member: their own proposal that waits (1), the two rows of judgments to review and a newer official version to sync (1) --
    4. A proposal of theirs the PI returned counts until they say 「知道了」, then leaves the count as it leaves the list."""
    case = _with_review(_team_case("member"))
    returned = {**case["desk"]["column"]["mine"][0], "id": "p9", "state": "returned", "reason": "先补数据", "touches": [A16],
                "outcomes": [{"act": "returned", "text": "先补数据"}]}
    case["desk"]["column"]["mine"].append(returned)
    case["desk"]["column"]["sync"] = {"shared": "/x", "reachable": True, "behind": True,
                                      "last_snapshot": {"at": "2024-03-01T12:00:00", "seq": 3, "note": ""}}
    out = _sidebar(with_needs(case), after=r"""
      extra.listed = views.desk.all().filter((k) => k.tag === "BUTTON" && k.kids.join("") === "知道了").length;
      views.desk.all().find((k) => k.tag === "BUTTON" && k.kids.join("") === "知道了").click();
      extra.afterGotIt = count();
    """)
    assert out["count"] == ["5", "5 项等你处理", ""] and out["listed"] == 1
    assert out["afterGotIt"] == ["4", "4 项等你处理", ""]


@needs_node
def test_21_4_a_project_of_one_person_shows_the_count_too_and_a_team_project_with_nothing_waiting_shows_none():
    """One person (no team file): the count is there -- the judgments to review are theirs to handle; the list is the same
    list, not a team's. A team project with nothing to handle shows none; a team project this Mac is not part of shows what it
    holds."""
    single = _with_review(_case("single"))
    assert _sidebar(with_needs(single))["count"] == ["2", "2 项等你处理", ""]
    one = _sidebar(with_needs(_with_review(_case("single"))), lang="en")
    assert one["count"] == ["2", "2 need you", ""]
    pi = _team_case("pi")
    pi["desk"]["column"]["waiting"] = []
    pi["get"]["/api/proposals"] = {"role": "pi", "proposals": []}
    assert _sidebar(with_needs(pi))["count"] == ["", "", ""]
    none = _with_review(_case("none"))
    none["desk"]["column"]["review"]["groups"] = []
    assert _sidebar(with_needs(none))["count"] == ["1", "1 项等你处理", ""]
    en_one = _with_review(_case("single"))
    en_one["desk"]["column"]["review"]["groups"] = []
    assert _sidebar(with_needs(en_one), lang="en")["count"] == ["1", "1 needs you", ""]


@needs_node
def test_21_4_from_another_section_the_count_is_read_with_the_list_alone_and_not_on_the_desk_or_the_home():
    """With another section showing, refreshing the sidebar reads /api/workspace/now?only=needs once (never /api/desk, and the
    page's 'last visit' is left alone); on the desk its own reading sets the count and on 项目首页 the home's does, so nothing
    more is read there; a project of one person reads it too."""
    for case in (_with_work(_team_case("pi")), with_needs(_with_review(_case("single")))):
        case["get"]["/api/workspace/now?only=needs"] = {"needs": case["desk"]["needs"], "role": case["desk"]["role"]}
        out = _sidebar(case, after=r"""
          shown["side-count-home"].textContent = ""; shown["side-count-home"].title = "";
          const since = Array.from(store.keys());
          const nowReads = () => reads("/api/workspace/now?only=needs");
          global.currentView = "results";
          const before = nowReads(), deskBefore = reads("/api/desk");
          await window.RODesk.refreshSidebar(); await settle();
          extra.elsewhere = [nowReads() - before, reads("/api/desk") - deskBefore, count()[0]];
          global.currentView = "desk";
          const mid = nowReads();
          await window.RODesk.refreshSidebar(); await settle();
          extra.onDesk = nowReads() - mid;
          global.currentView = "workspace";
          await window.RODesk.refreshSidebar(); await settle();
          extra.onHome = nowReads() - mid;
          extra.keys = Array.from(store.keys()).filter((k) => !since.includes(k));
        """)
        assert out["elsewhere"] == [1, 0, "6" if case["desk"]["role"] == "pi" else "2"], case["desk"]["role"]
        assert out["onDesk"] == 0 and out["onHome"] == 0 and out["keys"] == []


@needs_node
def test_21_4_the_home_hands_the_list_it_read_to_the_sidebar_and_the_count_follows_an_act_without_a_reload():
    """项目首页 reads the list itself and gives it to `RODesk.noteNeeds`: the count follows at once. A second list handed in (a
    handback opened, a run answered, a proposal approved: the home reads again) replaces it."""
    case = _with_review(_case("single"))
    out = _sidebar(with_needs(case), after=r"""
      const rows = c.desk.needs.rows;
      global.currentView = "workspace";
      window.RODesk.noteNeeds({ rows: rows.slice(0, 1) });
      extra.one = count();
      window.RODesk.noteNeeds({ rows: [] });
      extra.none = count();
      window.RODesk.noteNeeds({ rows: rows.concat(rows) });
      extra.four = count();
    """)
    assert out["one"] == ["1", "1 项等你处理", ""] and out["none"] == ["", "", ""] and out["four"] == ["4", "4 项等你处理", ""]


# -- app.html: the one item, the switch, the callers ---------------------------------------------------------


def test_18_3_the_sidebar_has_one_item_for_the_page_and_the_old_view_is_gone():
    """工作台 and 研究路线 were two items on two views; now `research routes`
    is one item on the view `desk` (Section 14's name for the page), 项目首页
    stays first, and nothing of the old `tree` view is left in the page. The count of what waits (21.4) is beside 项目首页
    (the home's item) and not beside 研究路线, whose 18.3 count is gone."""
    html = APP_HTML.read_text(encoding="utf-8")
    nav = html[html.index('<nav class="side-nav"'):html.index("</nav>")]
    assert nav.count('class="side-item"') == 8
    assert "工作台" not in nav and nav.count("研究路线") == 1
    assert re.search(r'data-section="routes" data-view="desk"><svg[^>]*><path d="[^"]+"/></svg><span class="side-label" data-i18n>研究路线</span></button>', nav)
    assert re.search(r'data-section="workspace" data-view="workspace"><svg[^>]*><path d="[^"]+"/></svg><span class="side-label" data-i18n>项目首页</span>'
                     r'<span class="side-count" id="side-count-home"></span></button>', nav)
    assert "side-count-routes" not in html
    assert nav.index('data-section="workspace"') < nav.index('data-section="routes"') < nav.index('data-section="results"')
    for old in ('id="view-tree"', 'views.tree', 'loadTreeView', '"tree"]', 'currentView === "tree"'):
        assert old not in html.replace('stored === "tree" ? "desk"', "").replace('"tree": () => showRoutes("list")', ""), old


def test_18_3_the_switch_is_in_the_page_header_marked_like_the_other_and_not_taken_for_a_section():
    """路线图 / 列表 are plain text buttons of the same `subnav` the other
    switch (数据与代码) uses, marked by aria-selected; shown for the page
    and not for a node page opened from it; the other switch's handlers
    read only its own buttons, so choosing a mode never navigates."""
    html = APP_HTML.read_text(encoding="utf-8")
    bar = html[html.index('<div id="routes-switch"'):]
    bar = bar[:bar.index("</div>")]
    assert 'class="subnav hidden" role="tablist" aria-label="研究路线" data-i18n-attr="aria-label"' in bar
    assert re.findall(r'<button type="button" class="subnav-item" role="tab" data-mode="(\w+)" aria-selected="(\w+)" data-i18n>([^<]+)</button>', bar) == [
        ("map", "true", "路线图"), ("list", "false", "列表")]
    assert html.index('id="data-switch"') < html.index('id="routes-switch"') < html.index('id="view-workspace"')
    view = _app_function("function activateView(")
    assert 'document.getElementById("routes-switch").classList.toggle("hidden", section !== "routes" || name === "node");' in view
    assert 'document.querySelectorAll(".subnav-item[data-subview]")' in view
    assert 'document.querySelectorAll(".subnav-item[data-subview]").forEach((btn) => {\n  btn.addEventListener("click", () => activateView(btn.dataset.subview));' in html
    assert 'document.querySelectorAll("#routes-switch .subnav-item").forEach((btn) => {\n  btn.addEventListener("click", () => RODesk.setMode(btn.dataset.mode));' in html
    assert 'if (name === "desk" && !views.desk.dataset.loaded) RODesk.loadDesk(views.desk);\n  else if (name === "desk") RODesk.resume();' in view


@needs_node
def test_18_3_a_page_left_on_the_old_list_opens_the_page_and_other_values_are_as_before():
    """`rce.view` may hold `tree` from an older session: the page opens on
    the one 研究路线; a value that is a view stays, `node` and anything
    unknown start on the project home."""
    code = _app_function("function storedView(")
    script = ("global.window = { localStorage: { getItem: () => global.value } };\n"
              "const VIEW_STORAGE_KEY = 'view'; const views = { workspace: 1, desk: 1, results: 1, node: 1 };\n"
              + code + "\nconst out = {};\n"
              "for (const v of ['tree', 'desk', 'results', 'node', 'bogus', null]) { global.value = v; out[String(v)] = storedView(); }\n"
              "global.window.localStorage.getItem = () => { throw new Error('blocked'); }; out.blocked = storedView();\n"
              "process.stdout.write(JSON.stringify(out));\n")
    assert _node(script) == {"tree": "desk", "desk": "desk", "results": "results", "node": None, "bogus": None, "null": None, "blocked": None}


def test_18_3_every_way_to_the_old_pages_opens_the_one_page():
    """The shell's `desk` and `routes` (the menu's one item sends `routes`),
    the old tree command of a menu older than V8 U5, the abandoned routes,
    the node page's way back, and the text zoom all reach the one page; no
    caller is left on the view `tree`."""
    html = APP_HTML.read_text(encoding="utf-8")
    team = (WEBAPP / "team.js").read_text(encoding="utf-8")
    node = (WEBAPP / "node.js").read_text(encoding="utf-8")
    assert '"desk": () => showRoutes(),\n  "routes": () => showRoutes(),' in html
    assert '"tree": () => showRoutes("list"),' in html
    show = _app_function("function showRoutes(")
    assert "if (mode) RODesk.setMode(mode);" in show and 'activateView("desk");' in show
    assert 'if (!document.getElementById("view-desk")) return false;' in html  # the stopped page has none
    # 12.9's 「在研究路线中查看」: the list, with the abandoned filter, on the one page.
    abandoned = team[team.index("function showAbandoned()"):]
    abandoned = abandoned[:abandoned.index("\n  }\n")]
    assert 'RODesk.setMode("list");' in abandoned and 'activateView("desk");' in abandoned and "tree" not in abandoned
    assert "views.tree" not in team and '"tree"' not in team and "views.tree" not in node and '"tree"' not in node
    # 返回研究路线: the node page opened from either mode returns to the page and names it.
    assert 'activateView(nav.fromView && nav.fromView !== "node" ? nav.fromView : "desk")' in node
    assert re.search(r"desk: \"routes\", results: \"results\"", html) and "tree:" not in html[html.index("const SECTION_OF_VIEW"):html.index("const SECTION_TITLES")]
    assert "RODesk.listZoom(z === 1 ? \"\" : String(z));" in _app_function("function shellZoom(")


def test_18_5_no_screen_says_the_desk_any_more():
    """18.5 item 4: 工作台 is retired -- not in the page's markup, not in a
    string of its scripts (app.html's or any served one), and not as a key of
    the English table, which would be an entry no screen uses."""
    from test_v8_u2 import _served_scripts, html_screen_text, js_strings

    html = APP_HTML.read_text(encoding="utf-8")
    found = [f"markup: {t.strip()!r}" for t in html_screen_text(html) if "工作台" in t]
    for block in re.findall(r"<script>(.*?)</script>", html, re.S):
        found += [f"app.html line {ln}: {s!r}" for ln, s in js_strings(block) if "工作台" in s]
    for path in _served_scripts():
        found += [f"{path.name} line {ln}: {s!r}" for ln, s in js_strings(path.read_text(encoding="utf-8")) if "工作台" in s]
    assert found == []

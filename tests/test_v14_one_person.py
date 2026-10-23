"""Research OS V14, task G3-2 (DESIGN.md 18.3, "A one-person project"): a
project the engine calls "single" with no team file listing anyone (12.1) has
no 提议 and no 成员 in the sidebar, no reminder of who you are, and none of
提议, 成员, 提交提议 or 发布 in the Mac's menus; the project menu gains
开始协作…, which opens the team setup; once there is a team file the items
return without a reload (a PI alone in it included); a team project this Mac
is not part of, and a role the page could not read, show them. Anything that
asks for the 提议 page in a project of one person lands on 开始协作 instead of
a page about nothing.
desk.js, team.js and node.js run under node with the stand-in DOM of the
desk's other tests; app.html and the shell are read as source where they are
the thing under test. Every test names its scenario."""

from __future__ import annotations

import json
import re

import pytest

from researchos import people
from test_v8_phase2 import _call, _project
from test_v8_phase3 import PI, TEAM, _serve
from test_v9_language import (
    APP_HTML, _MENU_DOM, _app_block, _app_function, _i18n_source, _node, needs_node,
)
from test_v10_desk_panel import _case
from test_v10_desk_proposals import _team_case
from test_v14_routes_page import _run

# -- the engine's answer: is there a team file ---------------------------------------------------------------------


def test_18_3_the_proposals_answer_says_whether_a_team_file_lists_anyone(tmp_path):
    """The role is "single" for a project with no team file, an empty one, and
    a PI alone in it (12.1); the answer adds `team`, true only when the file
    lists someone, so that the page can show 提议 and 成员 for a PI who has
    made the member list and tell that PI from a researcher on their own. A
    team file that cannot be read is neither (its role is "unknown")."""
    root = _project(tmp_path / "solo")
    people.write_me(*PI)
    team_file = root / ".researchos" / "team.toml"
    pi_alone = '[[member]]\nid = "li@example.com"\nname = "李"\nrole = "pi"\n'
    seen = []
    with _serve(root) as base:
        for text in (None, "", pi_alone, TEAM, "[[member"):
            if text == TEAM:
                people.write_me("赵", "zhao@example.com")  # a team project this Mac is not part of: nothing to read from the shared folder
            if text is None:
                team_file.unlink(missing_ok=True)
            else:
                team_file.write_text(text)
            status, body = _call(f"{base}/api/proposals")
            assert status == 200
            seen.append((body["role"], body["team"]))
    assert seen == [("single", False), ("single", False), ("single", True), ("none", True), ("unknown", False)]


# -- the sidebar: for each kind of project ----------------------------------------------------------------------

#: The sidebar's parts as plain records, and what the page tells the shell. `snap()` is what a researcher sees of
#: the two items (the nav's marker, which the stylesheet reads), the foot's identity line, and what the Mac is told.
_SIDEBAR = r"""
function fake() {
  const e = { title: "", kids: [], dataset: {}, hidden: false, classList: { toggle() {} }, append(...xs) { this.kids.push(...xs); } };
  Object.defineProperty(e, "textContent", { get() { return this.text || ""; }, set(v) { this.text = String(v); this.kids = []; } });
  return e;
}
global.shown = { "side-count-proposals": fake(), "side-count-routes": fake(), "share-line": fake(), "me-line": fake(), "new-attempt-btn": fake(), "side-nav": fake() };
const parts0 = document.getElementById;
document.getElementById = (id) => shown[id] || parts0(id);
global.toShell = [];
global.postShellMode = (mode) => { if (toShell[toShell.length - 1] !== mode) toShell.push(mode); }; // as the page's own does
global.snap = () => ({ team: shown["side-nav"].dataset.team, shell: toShell[toShell.length - 1],
  me: shown["me-line"].kids.map((k) => k.className + ": " + k.textContent), meTitle: shown["me-line"].title,
  one: window.RODesk.onePerson() });
await window.RODesk.ensureContext();
window.RODesk.renderSidebar();
"""


def _sidebar(case: dict, lang: str | None = None, after: str = "") -> dict:
    return _run(case, _SIDEBAR + "global.extra = { first: snap() };\n" + after, lang=lang)["extra"]


def _no_identity(case: dict) -> dict:
    case["get"]["/api/me"] = {"me": None}
    return case


def _unknown_role(case: dict) -> dict:
    """The team file cannot be read: the engine's role is "unknown", never taken for a project of one."""
    case["get"]["/api/proposals"] = {"role": "unknown", "proposals": []}
    return case


def _unreadable(case: dict) -> dict:
    """The role cannot be read at all (the request fails)."""
    del case["get"]["/api/proposals"]
    return case


def _words(lang: str) -> dict[str, str]:
    en = lang == "en"
    return {"member": "Member" if en else "成员", "none": "Not in the team yet" if en else "尚未加入团队",
            "unset": "Who you are is not set yet" if en else "还没有设置你是谁",
            "where": "Set it in “Members”" if en else "在「成员」一栏里设置"}


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_18_3_a_project_of_one_person_has_no_proposals_no_members_and_no_reminder(lang):
    """The engine says "single" and nobody has set who they are: the nav is
    marked one person (the stylesheet hides 提议 and 成员 under it), the
    foot says nothing and has no hover text, and the Mac is told "single"
    (no 提议, 成员, 提交提议 or 发布 there either). The same in both
    languages: there is nothing to say."""
    out = _sidebar(_no_identity(_case("single", lang)), lang=None if lang == "zh" else lang)["first"]
    assert out == {"team": "single", "shell": "single", "me": [], "meTitle": "", "one": True}


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_18_3_a_one_person_project_keeps_the_name_when_it_is_set_and_never_a_role(lang):
    """Who you are is shown as before once it is set (it is what every entry
    is signed with); there is no role word in a project of one."""
    out = _sidebar(_case("single", lang), lang=None if lang == "zh" else lang)["first"]
    assert out == {"team": "single", "shell": "single", "me": ["me-name: Li"], "meTitle": "li@example.com", "one": True}


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_18_3_a_team_project_shows_both_items_the_role_and_the_reminder(lang):
    """The PI's, a member's, and a team project this Mac is not part of: the
    nav is marked a team (both items shown), the Mac is told the role it
    always was, and the reminder of who you are is there where nobody has
    said (the hover text points at 成员, which is there)."""
    w = _words(lang)
    use = None if lang == "zh" else lang
    pi = _sidebar(_team_case("pi", lang), lang=use)["first"]
    assert pi == {"team": "team", "shell": "pi", "me": ["me-name: Li", "me-role: PI"], "meTitle": "li@example.com", "one": False}
    member = _sidebar(_team_case("member", lang), lang=use)["first"]
    assert member == {"team": "team", "shell": "member", "me": ["me-name: Li", f"me-role: {w['member']}"],
                      "meTitle": "li@example.com", "one": False}
    none = _sidebar(_no_identity(_case("none", lang)), lang=use)["first"]
    assert none == {"team": "team", "shell": "none", "me": [f"me-unset: {w['unset']}"], "meTitle": w["where"], "one": False}
    named = _sidebar(_case("none", lang), lang=use)["first"]
    assert named["me"] == ["me-name: Li", f"me-role: {w['none']}"] and named["team"] == "team" and named["shell"] == "none"


def _pi_alone(case: dict) -> dict:
    """A PI who made the member list and added nobody: the engine's role is "single", and a team file lists them."""
    case["get"]["/api/proposals"] = {"role": "single", "proposals": [], "team": True}
    return case


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_18_3_a_pi_alone_in_the_team_file_has_the_items_back_as_the_design_says_once_a_team_file_exists(lang):
    """Section 18.3: "once a team file exists, all of these return". The
    engine calls a PI alone in it single (12.1), so it says `team` beside the
    role: the nav is marked a team's (成员 is where the PI goes on to set the
    shared folder and add the first member), the Mac is told "none" (提议 and
    成员 show; 提交提议 and 发布 need a role the page did not read), and the
    reminder of who you are is the team's again. `mode` is "single" still,
    for the pages that read the role."""
    w = _words(lang)
    out = _sidebar(_no_identity(_pi_alone(_case("single", lang))), lang=None if lang == "zh" else lang,
                   after="extra.mode = window.RODesk.context().mode;\n")
    assert out["first"] == {"team": "team", "shell": "none", "me": [f"me-unset: {w['unset']}"], "meTitle": w["where"], "one": False}
    assert out["mode"] == "single"


@needs_node
@pytest.mark.parametrize("make", [_unknown_role, _unreadable], ids=["team file cannot be read", "request failed"])
def test_18_3_a_role_the_page_could_not_read_is_never_taken_for_a_project_of_one(make):
    """"Unknown" is not "single" (12.1: a team file that cannot be read is
    never assumed one person): both items show, the reminder shows where
    nobody has said who they are, and the Mac is told "none" -- 提议 and
    成员 there, 提交提议 and 发布 not (they need a role). The pages that
    always read this as a project of one still do (`mode` is as it was)."""
    out = _sidebar(make(_no_identity(_case("single"))), after="extra.mode = window.RODesk.context().mode;\n")
    assert out["first"] == {"team": "team", "shell": "none", "me": ["me-unset: 还没有设置你是谁"],
                            "meTitle": "在「成员」一栏里设置", "one": False}
    assert out["mode"] == "single"


@needs_node
def test_18_3_no_project_open_is_not_a_project_of_one_but_tells_the_mac_single_as_before():
    """With no project the sidebar is hidden whole by its own rule; the page
    does not call that a project of one person's, keeps the nav marked as it
    starts (so a project added next never shows the two items for a moment),
    and the Mac is told "single" as it was (so 提议 and 成员 are not in its
    menu either)."""
    out = _sidebar(_case("single"), after=r"""
      state.noProject = true; window.RODesk.renderSidebar();
      extra.noProject = snap();
    """)
    assert out["noProject"]["one"] is False and out["noProject"]["shell"] == "single" and out["noProject"]["team"] == "single"


# -- the items return without a reload -------------------------------------------------------------------------


@needs_node
def test_18_3_when_a_team_comes_the_items_return_without_a_reload_and_go_again_with_it():
    """A refresh of the sidebar (after the page's own acts, and on the desk's
    reload when the generation moves) reads the role again: single, then a
    PI once a member is added, then single when the team goes; the Mac is
    told each time. The desk's own reload does the same. And a read made for
    another project does not count: after the project is switched and before
    its role is read, nothing is taken for one person's."""
    team = _team_case("pi")
    out = _sidebar(_case("single"), after=r"""
      const pi = %(pi)s;
      c.get["/api/proposals"] = pi.proposals; c.get["/api/share"] = pi.share;
      await window.RODesk.refreshSidebar();
      extra.team = snap();
      c.get["/api/proposals"] = { role: "single", proposals: [] };
      await window.RODesk.refreshSidebar();
      extra.single = snap();
      c.get["/api/proposals"] = pi.proposals;
      await window.RODesk.loadDesk(views.desk, true); await settle();
      extra.desk = snap();
      extra.shell = toShell.slice();
      state.dataEpoch += 1;
      extra.switched = window.RODesk.onePerson();
      c.get["/api/proposals"] = { role: "single", proposals: [] };
      await window.RODesk.refreshSidebar();
      extra.afterSwitch = [window.RODesk.onePerson(), snap().team];
      state.currentId = "p-2";
      extra.otherProject = window.RODesk.onePerson();
    """ % {"pi": json.dumps({"proposals": team["get"]["/api/proposals"], "share": team["get"]["/api/share"]})})
    assert out["first"]["team"] == "single"
    assert (out["team"]["team"], out["team"]["shell"], out["team"]["one"]) == ("team", "pi", False)
    assert (out["single"]["team"], out["single"]["shell"], out["single"]["one"]) == ("single", "single", True)
    assert (out["desk"]["team"], out["desk"]["shell"]) == ("team", "pi")
    assert out["shell"] == ["single", "pi", "single", "pi"]
    assert out["switched"] is False and out["afterSwitch"] == [True, "single"] and out["otherProject"] is False


# -- the project menu -------------------------------------------------------------------------------------------


_CLICKABLE_MENU_DOM = _MENU_DOM.replace("addEventListener() {}", "addEventListener(type, fn) { this.on = this.on || {}; this.on[type] = fn; }")

_MENU_STEPS = r"""
(async () => {
  const P = { label: "我的论文", path: "/r/a", id: "p-1", initialized: true, available: true };
  const base = { noProject: false, projects: [P], currentPath: "/r/a", currentId: "p-1", currentEntry: P, projectLabel: "我的论文",
    dataEpoch: 0, currentBlocked: false, githubFetching: false };
  const labels = () => read(projectMenu).filter((r) => r.length === 5).map((r) => r[1]);
  const find = (n, text) => { for (const k of n.kids) { if (typeof k === "string") continue;
    if (k.tag === "button" && k.kids.some((x) => typeof x !== "string" && x.className === "pm-label" && x.textContent === text)) return k;
    const f = find(k, text); if (f) return f; } return null; };
  const out = {};
  for (const [name, one, no] of [["one", true, false], ["team", false, false], ["none", true, true]]) {
    global.state = Object.assign({}, base, no ? { noProject: true, projects: [], currentPath: "", currentEntry: null, projectLabel: "" } : {});
    global.RODesk = window.RODesk = { onePerson: () => one && !state.noProject };
    projectMenuMode = "list";
    renderProjectMenu();
    out[name] = labels();
  }
  // Clicking it closes the menu and opens the team setup.
  global.state = Object.assign({}, base);
  global.RODesk = window.RODesk = { onePerson: () => true };
  global.calls = [];
  global.startCollaborating = () => calls.push("start");
  renderProjectMenu();
  const before = projectMenu.kids.length;
  find(projectMenu, t("开始协作…")).on.click({ stopPropagation() {} });
  out.click = { calls, before: before > 0, after: projectMenu.kids.length };
  // A page without desk.js (the menu alone) has no such item and does not fail.
  delete global.RODesk; window.RODesk = undefined;
  renderProjectMenu();
  out.alone = labels();
  process.stdout.write(JSON.stringify(out));
})();
"""


def _menu(lang: str | None) -> dict:
    code = (_app_block("// -- The interface language (DESIGN.md 13.6)", "// -- Small shared state")
            + _app_function("function mk(") + _app_function("function basename(")
            + _app_block("// -- The project menu (DESIGN.md 10.1, 10.4; task V6)",
                         "// -- 「推送到 GitHub…」 (DESIGN.md 11.3, task V7)"))
    i18n = "" if lang is None else f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    return _node("global.window = {};\n" + i18n + _CLICKABLE_MENU_DOM + code + _MENU_STEPS)


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_18_3_the_project_menu_of_a_project_of_one_person_starts_collaborating_and_no_other_does(lang):
    """开始协作… ("Start collaborating…") stands right after 推送到 GitHub…
    in a project of one person's menu, and in no other: not a team's, not the
    menu with no project open, not a page that has no desk. Clicked, it
    closes the menu and calls the page's own way to the team setup."""
    en = lang == "en"
    push, start = ("Push to GitHub…", "Start collaborating…") if en else ("推送到 GitHub…", "开始协作…")
    tail = (["New project…", "Add project…", "Join a project from the shared folder…", "Rescan this project",
             "Change display name…", "Remove from the list…"] if en else
            ["新建项目…", "添加项目…", "从公盘加入项目…", "重新扫描这个项目", "重命名显示名称…", "从列表中移除…"])
    out = _menu(None if lang == "zh" else lang)
    assert out["one"] == ["我的论文", push, start, *tail] and start not in out["team"]
    assert out["team"] == ["我的论文", push, *tail] and out["alone"] == out["team"]
    assert start not in out["none"]
    assert out["click"] == {"calls": ["start"], "before": True, "after": 0}


@needs_node
def test_18_3_starting_collaborating_opens_the_member_page_read_afresh_and_leaves_it_alone_on_it():
    """From another section it clears what the 成员 page last drew (the
    sidebar's own click does the same, for the views that show shared state)
    and opens it; already on it, it reads nothing again."""
    code = _app_function("function startCollaborating(")
    script = ("global.views = { members: { dataset: { loaded: '1' } } }; global.opened = [];\n"
              "global.activateView = (n) => { opened.push([n, views.members.dataset.loaded || null]); return 'r'; };\n"
              + code + "\nconst out = {};\n"
              "global.currentView = 'results'; out.from = [startCollaborating(), ...opened.pop()];\n"
              "views.members.dataset.loaded = '1'; global.currentView = 'members'; out.on = [startCollaborating(), ...opened.pop()];\n"
              "process.stdout.write(JSON.stringify(out));\n")
    assert _node(script) == {"from": ["r", "members", None], "on": ["r", "members", "1"]}


# -- what asks for 提议 in a project of one person -------------------------------------------------------------


_VIEWS = r"""
global.window = { localStorage: { setItem() {} } };
const view = () => ({ dataset: {}, classList: { toggle() {} } });
global.views = {}; ["workspace", "desk", "results", "lineage", "canvas", "variables", "literature", "proposals", "members", "node"]
  .forEach((k) => { views[k] = view(); });
global.currentView = "workspace";
global.state = { noProject: false };
global.VIEW_STORAGE_KEY = "view-key"; global.FULL_WINDOW_VIEWS = []; global.lastDataView = "lineage";
global.document = { querySelectorAll: () => [], getElementById: () => ({ classList: { toggle() {} }, textContent: "" }),
  body: { classList: { toggle() {} } } };
global.sectionTitle = (s) => s;
global.RONode = { section: () => "routes" };
global.loads = [];
global.ROTeam = { loadProposals: () => loads.push("proposals"), loadMembers: () => loads.push("members") };
global.one = false;
global.RODesk = window.RODesk = { onePerson: () => one, leaveDesk: () => true };
"""


def _views_script(steps: str) -> str:
    return (_VIEWS + _app_block("const SECTION_OF_VIEW", "const SECTION_TITLES") + _app_function("function activateView(")
            + _app_function("function startCollaborating(") + "\n" + steps)


@needs_node
def test_18_3_the_proposals_page_of_a_project_of_one_person_is_the_team_setup_and_a_team_keeps_its_page():
    """Whatever opens 提议 -- the shell's `proposals` and `propose` from a
    menu built before 18.3, a view kept from another project -- lands on the
    成员 page (开始协作) when the project is one person's: that is the choice
    (route, not ignore: the researcher who asked for proposals is told how
    to have them). In a team project it opens 提议 as before. 成员 itself is
    always a page of its own, in either."""
    out = _node(_views_script(r"""
      const out = {};
      global.one = true; views.members.dataset.loaded = "1";
      activateView("proposals");
      out.one = [currentView, loads.slice(), views.members.dataset.loaded || null];
      global.currentView = "workspace"; loads.length = 0; global.one = false;
      activateView("proposals");
      out.team = [currentView, loads.slice()];
      global.currentView = "workspace"; loads.length = 0; global.one = true;
      activateView("members");
      out.members = [currentView, loads.slice()];
      process.stdout.write(JSON.stringify(out));
    """))
    assert out["one"] == ["members", ["members"], None]
    assert out["team"] == ["proposals", ["proposals"]]
    assert out["members"] == ["members", ["members"]]


@needs_node
def test_18_3_the_proposals_command_and_the_compose_command_do_not_leave_a_hidden_page_open():
    """The shell's `propose` (revealShellAnchor on 提议) in a project of one
    person ends on 成员, looks for an anchor on the hidden 提议 page once and
    stops: nothing is left waiting for a page that is not shown."""
    code = _app_function("function revealShellAnchor(")
    out = _node(_views_script(code + r"""
      global.timers = 0; global.setTimeout = () => { timers += 1; };
      views.proposals.classList = { contains: (c) => c === "hidden", toggle() {} };
      views.members.classList = { contains: () => false, toggle() {} };
      views.proposals.querySelector = () => { throw new Error("looked on the hidden page"); };
      global.one = true;
      revealShellAnchor("proposals", "compose");
      process.stdout.write(JSON.stringify({ view: currentView, timers }));
    """))
    assert out == {"view": "members", "timers": 0}


@needs_node
@pytest.mark.parametrize("known", [True, False], ids=["role known", "role not read yet"])
def test_18_3_the_proposals_page_asked_for_before_the_role_was_known_goes_on_to_the_team_setup(known):
    """At start the page opens the view it kept, and the role is not read yet:
    提议 opens, loads, finds the project one person's, and goes on to 成员
    (read afresh) instead of drawing 「这是单人项目…」 with a button to an item
    the sidebar does not show. A project whose role could not be read keeps
    that page and its button (the item is shown then)."""
    case = _case("single") if known else _unknown_role(_case("single"))
    out = _run(case, r"""
      const mk2 = global.mk; // the page the unread role draws reads `lastChild`, which the stand-in lacks
      global.mk = (tag, cls, text) => { const e = mk2(tag, cls, text); Object.defineProperty(e, "lastChild", { get: () => e.kids[e.kids.length - 1] }); return e; };
      global.navs = []; global.currentView = "proposals";
      global.activateView = (n) => navs.push([n, views.members.dataset.loaded || null]);
      views.proposals = el("div", "host"); views.members = el("div", "host"); views.members.dataset.loaded = "1";
      await window.ROTeam.loadProposals(views.proposals);
      global.extra = { navs, drawn: dump(views.proposals, []), loaded: views.proposals.dataset.loaded || null };
    """)["extra"]
    if known:
        # Left behind the hidden page: the loading line, and not marked loaded, so 提议 reads again when it is next opened.
        assert out == {"navs": [["members", None]], "drawn": ["empty-note: 载入中…"], "loaded": None}
    else:
        assert out["navs"] == [] and out["loaded"] == "1"
        assert [d.split(": ", 1)[1] for d in out["drawn"]] == [
            "提议", "这是单人项目：你的改动直接生效，不需要提议和批准。要和别人一起做，在「成员」一栏里开始协作。", "去成员一栏"]


@needs_node
def test_18_3_a_team_project_loading_proposals_stays_on_them():
    """The PI's 提议 page is the inbox as before: no redirect when the role is
    a team's."""
    out = _run(_team_case("pi"), r"""
      global.navs = []; global.currentView = "proposals";
      global.activateView = (n) => navs.push(n);
      views.proposals = el("div", "host");
      await window.ROTeam.loadProposals(views.proposals);
      global.extra = { navs, loaded: views.proposals.dataset.loaded || null };
    """)["extra"]
    assert out == {"navs": [], "loaded": "1"}


# -- app.html: the markup, the stylesheet, the callers -------------------------------------------------------


def test_18_3_the_sidebar_keeps_eight_items_and_hides_the_two_by_a_rule_of_its_own():
    """Nothing is removed from the markup (12.9's eight items, in order): the
    nav starts out marked one person's -- a project of one person never shows
    the two for a moment -- and the page's role reading flips the mark
    (desk.js). The stylesheet hides exactly those two, only under that mark."""
    html = APP_HTML.read_text(encoding="utf-8")
    nav = html[html.index('<nav class="side-nav"'):html.index("</nav>")]
    assert nav.startswith('<nav class="side-nav" id="side-nav" data-team="single" aria-label="栏目" data-i18n-attr="aria-label">')
    assert re.findall(r'class="side-item" data-section="(\w+)"', nav) == [
        "workspace", "routes", "results", "variables", "literature", "data", "proposals", "members"]
    css = html[html.index("<style>"):html.index("</style>")]
    assert ('.side-nav[data-team="single"] .side-item[data-section="proposals"],\n'
            '.side-nav[data-team="single"] .side-item[data-section="members"] { display: none; }') in css
    assert css.count('[data-team="single"]') == 2
    desk = (APP_HTML.parent / "desk.js").read_text(encoding="utf-8")
    assert 'nav.dataset.team = state.noProject || onePerson() ? "single" : "team";' in desk


def test_18_3_the_one_person_reading_is_the_engines_single_and_nothing_else():
    """`onePerson` is true only when the engine itself said "single" for the
    project this page is showing, with no team file listing anyone, in this
    project's epoch; every other reading (a failed request, an unreadable
    team file, a PI alone in the team file, no project, a role from another
    project) is not."""
    desk = (APP_HTML.parent / "desk.js").read_text(encoding="utf-8")
    assert 'ctx.single = role === "single" && !(proposals && proposals.team === true);' in desk
    one = desk[desk.index("  function onePerson() {"):]
    one = one[:one.index("\n  }\n")]
    assert one.split("\n")[1].strip() == ("return !state.noProject && ctx.single === true && ctx.project === projectKey() "
                                          "&& ctx.epoch === state.dataEpoch;")
    sidebar = desk[desk.index("  function renderSidebar() {"):]
    sidebar = sidebar[:sidebar.index("\n  }\n")]
    assert "} else if (!onePerson()) {\n      meLine.append(mk(\"span\", \"me-unset\"" in sidebar


def test_18_3_the_dispatcher_and_the_project_menu_reach_the_team_setup_through_one_function():
    """`activateView("proposals")` hands over to `startCollaborating()` for a
    project of one person before anything else is decided, and the menu's
    item calls the same function; the shell's commands are unchanged names."""
    html = APP_HTML.read_text(encoding="utf-8")
    view = _app_function("function activateView(")
    guard = 'if (name === "proposals" && window.RODesk && RODesk.onePerson && RODesk.onePerson()) return startCollaborating();'
    assert guard in view and view.index(guard) < view.index("if (currentView === name) return null;")
    menu = _app_function("function renderProjectMenu(")
    item = 'projectMenu.appendChild(pmItem(t("开始协作…"), () => { closeProjectMenu(false); startCollaborating(); }));'
    assert item in menu
    assert menu.index('pmItem(t("推送到 GitHub…")') < menu.index(item) < menu.index('pmItem(t("新建项目…")')
    assert 'if (window.RODesk && RODesk.onePerson && RODesk.onePerson()) {\n    ' + item in menu
    table = html[html.index("const SHELL_COMMANDS = {"):]
    table = table[:table.index("\n};\n")]
    assert '"proposals": () => activateView("proposals"),' in table and '"members": () => activateView("members"),' in table
    team = (APP_HTML.parent / "team.js").read_text(encoding="utf-8")
    assert 'if (RODesk.onePerson() && currentView === "proposals") {\n      delete views.members.dataset.loaded;\n      activateView("members");' in team

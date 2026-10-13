"""Research OS V10, increment B1 (DESIGN.md 14.1, 14.2): the desk's route
map -- its own served renderer, /routemap.js; its layout, a pure function
run here under node (a forest by generation, siblings in the map's order,
「还没标来处」 below it; a pinned view where nothing moves and a new attempt
joins the end of the row); its words in both languages; and its
arrangement, per person, as the view `routes` of canvas.json, read with the
desk and written through the layout endpoint. Every test names its
scenario."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from researchos.webapp import canvas, server

from test_v10_derives_from import _map
from test_v8_phase2 import _call, _conn, _project, _scan
from test_v8_phase3 import _serve
from test_v8_u2 import _served_scripts, js_strings

WEBAPP = Path(server.__file__).parent
ROUTEMAP_JS = WEBAPP / "routemap.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
A = {n: f"attempt:map.md#{n}" for n in range(1, 30)}


def _node(expr: str, *, lang: str | None = None) -> object:
    """`expr` evaluated after routemap.js -- after /i18n.js too, with the
    saved choice `lang`, when one is given. The script goes in on stdin."""
    script = "global.window = {};\n"
    if lang is not None:
        script += f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + (WEBAPP / "i18n.js").read_text(encoding="utf-8") + "\n"
    script += ROUTEMAP_JS.read_text(encoding="utf-8") + f"\nconst R = window.RORouteMap;\nprocess.stdout.write(JSON.stringify({expr}));\n"
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _layout(numbers, lines=(), fixed=None) -> dict:
    attempts = json.dumps([{"id": A[n]} for n in numbers])
    drawn = json.dumps([{"child": A[c], "parent": A[p]} for c, p in lines])
    got = _node(f"[R._layoutRoutes({attempts}, {drawn}, {{fixed: {json.dumps(fixed or {})}}}), R._geometry]")
    return {**got[0], "geometry": got[1]}


def _overlap(positions: dict, g: dict) -> list:
    boxes = list(positions.items())
    return [(a, b) for i, (a, p) in enumerate(boxes) for b, q in boxes[i + 1:]
            if abs(p[0] - q[0]) < g["CARD_W"] and abs(p[1] - q[1]) < g["CARD_H"]]


# -- the layout (14.1) ------------------------------------------------------------------------------------


@needs_node
def test_14_8_1_twenty_four_attempts_with_no_line_stand_in_the_row_by_date():
    """The researcher's map today: no 「源自」 line yet, so every card stands
    in 「还没标来处」 in the map's order, left to right and line by line, the
    caption above it, no two cards on top of each other."""
    numbers = list(range(1, 25))
    got = _layout(numbers)
    g, pos = got["geometry"], got["positions"]
    assert got["pinned"] is False and got["row"] == [A[n] for n in numbers]
    reading = sorted(numbers, key=lambda n: (pos[A[n]][1], pos[A[n]][0]))
    assert reading == numbers
    first = pos[A[1]]
    assert got["caption"] == [first[0], first[1] - g["CAPTION_H"]] and first == [0, g["CAPTION_H"]]
    per = sum(1 for n in numbers if pos[A[n]][1] == first[1])
    assert per >= 4 and pos[A[per + 1]] == [0, first[1] + g["ROW_Y"]]  # the row wraps toward the page
    assert _overlap(pos, g) == []


@needs_node
def test_14_8_1_one_line_makes_a_two_card_forest_above_the_row():
    """「#17 源自 #16」: #16 stands left of #17 on one row; the others stay
    in 「还没标来处」, below the forest, in order."""
    got = _layout([16, 17, 18, 19], [(17, 16)])
    g, pos = got["geometry"], got["positions"]
    assert pos[A[16]] == [0, 0] and pos[A[17]] == [g["COL_PITCH"], 0]
    assert got["row"] == [A[18], A[19]]
    assert pos[A[18]][1] == pos[A[19]][1] > g["CARD_H"] and pos[A[18]][0] < pos[A[19]][0]
    assert got["caption"][1] > g["CARD_H"]  # the caption sits below the forest, above the row


@needs_node
def test_14_1_generations_are_the_longest_path_and_siblings_follow_the_maps_order():
    """#3 grows out of #1 and of #2, which grows out of #1: #3 stands in the
    third column. #1's children #2 and #4 are in the map's order, the first
    on #1's row; a second tree (#5, #6) stands below the first; no overlap."""
    got = _layout([1, 2, 3, 4, 5, 6], [(2, 1), (3, 1), (3, 2), (4, 1), (6, 5)])
    g, pos = got["geometry"], got["positions"]
    assert [pos[A[n]][0] // g["COL_PITCH"] for n in (1, 2, 3, 4, 5, 6)] == [0, 1, 2, 1, 0, 1]
    assert pos[A[2]][1] == pos[A[1]][1] < pos[A[4]][1]
    assert pos[A[5]][1] > max(pos[A[n]][1] for n in (1, 2, 3, 4)) and pos[A[6]][1] == pos[A[5]][1]
    assert got["row"] == [] and got["caption"] is None and _overlap(pos, g) == []


@needs_node
def test_14_1_lines_the_map_cannot_draw_are_left_out_and_a_circle_never_hangs_it():
    """A line to itself, one naming an attempt the map does not have, a
    duplicate, and two lines closing a circle (a hand edit the engine would
    report): the layout still places every card once."""
    got = _layout([1, 2, 3], [(1, 1), (2, 9), (2, 3), (2, 3), (1, 3), (3, 1)])
    assert set(got["positions"]) == {A[1], A[2], A[3]} and got["row"] == []
    assert _overlap(got["positions"], got["geometry"]) == []


@needs_node
def test_14_1_a_pinned_view_moves_nothing_and_a_new_attempt_joins_the_end_of_the_row():
    """After the first move every card has a position. Nothing pinned moves;
    #20, new, takes the place after the last card of the lowest line of
    「还没标来处」 -- even where the researcher moved the row; with no card
    left in the row, a new one starts it below everything."""
    fixed = {A[16]: [0, 0], A[17]: [344, 0], A[18]: [40, 400], A[19]: [600, 520]}
    got = _layout([16, 17, 18, 19, 20], [(17, 16)], fixed)
    g, pos = got["geometry"], got["positions"]
    assert got["pinned"] is True and all(pos[k] == v for k, v in fixed.items())
    assert pos[A[20]] == [600 + g["ROW_X"], 520]
    assert got["caption"] == [40, 400 - g["CAPTION_H"]]
    only_forest = _layout([16, 17, 20], [(17, 16)], {A[16]: [0, 0], A[17]: [344, 0]})
    assert only_forest["positions"][A[20]] == [0, g["CARD_H"] + 72 + g["CAPTION_H"]]


# -- the words (14.1, 14.2, 14.6) -------------------------------------------------------------------------


@needs_node
def test_14_6_a_cards_marks_a_lines_words_and_each_refusal_in_both_languages():
    """Marks are the engine's words, each once, then 有改动; a line reads
    「#17 源自 #16」; each refusal has its own sentence; the local check
    names self, duplicate and circle before anything is asked."""
    card = {"officialness": {"state": "proposed", "label": "提议中"},
            "marks": {"proposals": [{"label": "提议中"}], "review": {"count": 2, "label": "待复核 2"}}}
    codes = ["self_link", "mapping_circle", "mapping_exists", "mapping_attempt_not_found", "mapping_member_proposes",
             "line_exists", "attempt_not_found", "no_such_line", "pi_changes_directly", "not_in_team", "project_busy"]
    expr = (f"[R._markWords({json.dumps(card)}, true), R._markWords({{}}, false), R._lineWords('17', '16'), "
            f"{json.dumps(codes)}.map(R._refusal), "
            "[R._checkLine([], 'a', 'a'), R._checkLine([{child: 'b', parent: 'a'}], 'b', 'a'), "
            "R._checkLine([{child: 'b', parent: 'a'}, {child: 'c', parent: 'b'}], 'a', 'c'), "
            "R._checkLine([{child: 'b', parent: 'a'}], 'c', 'b')]]")
    zh, en = _node(expr), _node(expr, lang="en")
    assert zh[:3] == [["提议中", "待复核 2", "有改动"], [], "#17 源自 #16"]
    assert zh[3][:3] == ["一个尝试不能源自它自己", "这条连线会绕成一个圈：它要连的来处本身就源自它", "这两条尝试之间已经有这条连线了"]
    assert en[0][2] == "Changed" and en[2] == "#17 grew out of #16"
    assert en[3][:3] == ["An attempt cannot grow out of itself",
                         "This line would close a circle: the attempt it would grow out of already grows out of it",
                         "These two attempts already have this line"]
    assert len(set(zh[3][:-1])) == len(codes) - 1 and zh[3][-1] is None  # each its own; any other cause: the framing
    assert all(s and not re.search("[一-鿿]", s) for s in en[3][:-1])
    assert zh[4] == en[4] == ["self_link", "mapping_exists", "circle", None]


def test_8_8_and_12_11_the_route_map_is_served_and_says_neither_node_nor_canvas():
    """Loaded by app.html with the tag the 12.11 scanner follows (so its
    strings are scanned for emoji and arrows, and its t() keys for English);
    packaged; never 节点 or 画布 on screen. The desk's zoom goes to the map."""
    assert "routemap.js" in [p.name for p in _served_scripts()]
    assert [s for _, s in js_strings(ROUTEMAP_JS.read_text(encoding="utf-8")) if "节点" in s or "画布" in s] == []
    assert '"webapp/routemap.js"' in (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    html = (WEBAPP / "app.html").read_text(encoding="utf-8")
    assert 'if (currentView === "desk" && RODesk.mapZoom(step)) return;' in html
    assert 'if (key !== "canvas" && key !== "desk") node.style.zoom' in html


# -- the desk around the map (14.4, 14.5; increment C1) ----------------------------------------------------


@needs_node
def test_14_4_the_desk_redraws_in_place_and_a_picked_row_marks_its_attempts_on_the_map():
    """The desk is drawn once -- the map's element and the column kept -- and
    a refresh or another visit redraws the column in place: 载入中… only
    before the first drawing, never over a desk already drawn. Picking a row
    of the column marks the attempts it touches on the map; picking it again
    unmarks them. The map gets the payload and 「有改动」's rule each time."""
    from test_v9_language import _DESK_DOM, _desk_cases

    case = json.dumps(_desk_cases("zh")[0], ensure_ascii=False)
    script = (
        "global.window = {};\n" + _DESK_DOM + "const calls = [];\n"
        "window.RORouteMap = { create() { return { mount() { calls.push(['mount']); },"
        " update(d, o) { calls.push(['update', d.role, typeof o.changed]); }, highlight(ids) { calls.push(['highlight', ids]); },"
        " visible() { return true; } }; } };\n"
        + (WEBAPP / "desk.js").read_text(encoding="utf-8")
        + "\nconst wiped = (h) => h.kids.some((k) => typeof k === 'string' && k.includes('载入中'));\n"
        "const find = (n, f) => { for (const k of n.kids) if (typeof k !== 'string') { if (f(k)) return k;"
        " const got = find(k, f); if (got) return got; } return null; };\n"
        f"(async () => {{ const c = {case}; const host = await deskHost(c); const layout = host.kids[0];\n"
        "  const seen = [];\n"
        "  global.apiGet = async (path) => { if (path === '/api/desk') { seen.push(wiped(host)); return c.desk; }"
        " return { '/api/me': c.me, '/api/proposals': c.proposals, '/api/share': c.share }[path]; };\n"
        "  await window.RODesk.loadDesk(host, false); await window.RODesk.loadDesk(host, true);\n"
        "  const line = find(host, (k) => k.className === 'desk-link strong' && k.textContent.startsWith('加上连线'));\n"
        "  line.onClick(); line.onClick();\n"
        "  process.stdout.write(JSON.stringify({ seen, same: host.kids.length === 1 && host.kids[0] === layout,"
        " column: layout.kids.map((k) => k.className), calls })); })();\n"
    )
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True)
    got = json.loads(out.stdout)
    assert got["seen"] == [False, False] and got["same"] is True
    assert got["column"] == ["desk-map", "desk-column"]
    assert got["calls"] == [["mount"], ["update", "pi", "function"], ["update", "pi", "function"],
                            ["update", "pi", "function"], ["highlight", [A[18], A[17]]], ["highlight", []]]


# -- the arrangement (14.1: canvas.json's view `routes`) ----------------------------------------------------


@pytest.fixture
def root(tmp_path):
    root = _project(tmp_path / "proj")
    (root / "map.md").write_text(_map())
    _scan(root)
    return root


def test_14_1_the_routes_view_holds_the_attempts_and_keeps_the_other_views(root):
    """`routes` shows the attempts and nothing else: a position for a file is
    skipped, other views are kept; 关系图 never draws it; the desk reads it."""
    conn = _conn(root)
    try:
        assert canvas.view_card_ids(conn, root, canvas.SCOPE_ROUTES) == {A[16], A[17], A[18]}
        with pytest.raises(canvas.UnknownScopeError):
            canvas.build_canvas(conn, root, canvas.SCOPE_ROUTES)
        canvas.save_layout(conn, root, {"scope": "all", "positions": {"script:steps/16-build.py": [7, 8]}})
        canvas.save_layout(conn, root, {"scope": "routes", "positions": {A[16]: [1, 2], "script:steps/16-build.py": [5, 5]},
                                        "viewport": {"x": 3, "y": 4, "zoom": 0.5}})
    finally:
        conn.close()
    assert canvas.routes_layout(root) == {"positions": {A[16]: [1.0, 2.0]}, "viewport": {"x": 3.0, "y": 4.0, "zoom": 0.5},
                                          "state": "ok", "writable": True}
    assert canvas.load_layout(root, "all")["positions"] == {"script:steps/16-build.py": [7.0, 8.0]}


def test_14_1_http_the_desk_carries_the_arrangement_and_the_page_writes_it(root):
    """GET /api/desk's `layout` names the project a write must echo; a write
    for `routes` lands and is read back with the desk; /routemap.js is
    served verbatim and origin-checked; GET /api/canvas?scope=routes is not
    a view of 关系图."""
    with _serve(root) as base:
        status, desk = _call(f"{base}/api/desk")
        assert status == 200 and desk["layout"] == {"positions": {}, "viewport": None, "state": "absent", "writable": True,
                                                    "project": str(root.resolve())}
        body = {"project": desk["layout"]["project"], "scope": "routes", "positions": {A[17]: [10, 20]}}
        assert _call(f"{base}/api/canvas/layout", "POST", body)[0] == 200
        status, desk = _call(f"{base}/api/desk")
        assert desk["layout"]["positions"] == {A[17]: [10.0, 20.0]} and desk["layout"]["state"] == "ok"
        assert _call(f"{base}/api/canvas?scope=routes")[0] == 404
        with urllib.request.urlopen(base + "/routemap.js") as resp:
            assert resp.headers.get("Content-Type", "").startswith("text/javascript")
            assert resp.read() == ROUTEMAP_JS.read_bytes()
        req = urllib.request.Request(base + "/routemap.js", headers={"Origin": "http://evil.example"})
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req)
        assert caught.value.code == 403

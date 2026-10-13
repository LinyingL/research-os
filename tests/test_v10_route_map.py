"""Research OS V10, increment B1 (DESIGN.md 14.1): the route map's
arrangement, per person, as the view `routes` of canvas.json -- its cards
are the attempts -- read with the desk and written through the layout
endpoint. Every test names its scenario."""

from __future__ import annotations

import pytest

from researchos.webapp import canvas

from test_v10_derives_from import _map
from test_v8_phase2 import _call, _conn, _project, _scan
from test_v8_phase3 import _serve

A = {n: f"attempt:map.md#{n}" for n in range(1, 30)}


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
    for `routes` lands and is read back with the desk; GET
    /api/canvas?scope=routes is not a view of 关系图."""
    with _serve(root) as base:
        status, desk = _call(f"{base}/api/desk")
        assert status == 200 and desk["layout"] == {"positions": {}, "viewport": None, "state": "absent", "writable": True,
                                                    "project": str(root.resolve())}
        body = {"project": desk["layout"]["project"], "scope": "routes", "positions": {A[17]: [10, 20]}}
        assert _call(f"{base}/api/canvas/layout", "POST", body)[0] == 200
        status, desk = _call(f"{base}/api/desk")
        assert desk["layout"]["positions"] == {A[17]: [10.0, 20.0]} and desk["layout"]["state"] == "ok"
        assert _call(f"{base}/api/canvas?scope=routes")[0] == 404

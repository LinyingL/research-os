"""Research OS V10, increment A3 (DESIGN.md 14.1, 14.3-14.5): one rule for
an attempt's files -- its step files and its 「关联文件」 -- shared by the
node page, 关系图's attempt view and the desk; and the desk's payload for
the route map: the cards with their marks, the 「源自」 lines and the column,
in a single-person, a PI's and a member's project, in either language, with
a shared folder that cannot be read. Every test names its scenario."""

from __future__ import annotations

from researchos import nodepage, trace
from researchos.ingest import mappings as mappings_ingest
from researchos.webapp import canvas

from test_v8_phase2 import _conn, _project, _scan

A16 = "attempt:map.md#16"
FIG1 = "figure:figs/fig1.png"


def test_14_3_one_membership_rule_for_the_node_page_the_relation_map_and_the_desk(tmp_path):
    """#16 names steps 16-17 and 「关联文件」 attaches figs/fig1.png: the node
    page's trace and the attempt view of 关系图 show the same files."""
    root = _project(tmp_path / "proj")
    mappings_ingest.add_mapping(root, "figs/fig1.png", A16, "belongs_to")
    _scan(root)
    conn = _conn(root)
    try:
        members = trace.attempt_members(conn, root)[A16]
        page = nodepage.node(conn, root, A16)["trace"]
        drawn = canvas.build_canvas(conn, root, A16)
    finally:
        conn.close()
    assert members == {"script:steps/16-build.py": {"via": "step_files"}, "script:steps/17-plot.py": {"via": "step_files"},
                       FIG1: {"via": "belongs_to", "link": {"src": FIG1, "dst": A16, "type": "belongs_to",
                                                            "extractor": "mapping"}}}
    on_page = {i["id"] for c in page["columns"] for i in c["items"] if i.get("member")}
    assert on_page == set(members)
    assert next(i for c in page["columns"] for i in c["items"] if i["id"] == FIG1)["attached"] is True
    (frame,) = drawn["frames"]
    assert set(frame["node_ids"]) == set(members)  # 关系图 now holds the attached figure too
    assert FIG1 in {n["id"] for n in drawn["nodes"]}

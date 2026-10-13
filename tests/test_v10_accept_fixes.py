"""Research OS V10, what the designer's acceptance of the desk on a copy of
the researcher's project found (DESIGN.md 14.3, 14.1, 12.11, 13.4, 9.3):
each with the scenario that would have caught it.

Every test names its scenario."""

from __future__ import annotations

import re

from researchos import proposals
from researchos.records import decisions as decisions_mod

from test_v8_phase2 import _project
from test_v9_language import APP_HTML


def _css_rule(selector: str) -> str:
    css = APP_HTML.read_text(encoding="utf-8")
    css = css[:css.index("</style>")]
    found = re.search(r"(?m)^" + re.escape(selector) + r" \{([^}]*)\}", css)
    assert found, selector
    return found.group(1)


# -- the route map's caption: the sans, as the other small captions (12.11) ------------------------------


def test_12_11_the_route_maps_origin_not_marked_caption_is_in_the_sans_not_the_mono():
    """「还没标来处」 over the row of attempts with no line is a caption in
    words, not a path, code or identifier: it takes the sans, as the map's
    other small caption (a line's 源自) does -- never the mono."""
    caption = _css_rule(".rm-caption")
    assert "--font-mono" not in caption
    assert "font-family: var(--font-sans)" in caption
    assert "font-family: var(--font-sans)" in _css_rule(".rm-line-label")


# -- decisions.toml's header: what the file holds (14.3, 9.3) --------------------------------------------

#: The line a new decisions.toml opens with, and the one files founded before this change open with.
HEADER = "# 经 PI 批准的提议和直接放弃的路线，各附依据。Research OS 只追加、不改写。\n"
OLD_HEADER = "# 经 PI 批准的决定。Research OS 只追加、不改写；放弃一条路线时，它当时的依据也记在这里。\n"


def test_14_3_a_new_decisions_file_says_it_holds_approved_proposals_and_routes_abandoned_directly(tmp_path):
    """A single researcher abandons #16 by writing the table directly: no
    PI approved anything, so the decisions.toml this founds does not open
    with 「经 PI 批准的决定」 -- it says it holds approved proposals and
    routes abandoned directly, each with its basis, and is only appended
    to."""
    root = _project(tmp_path / "proj")
    assert not decisions_mod.decisions_path(root).exists()
    proposals.write_directly(root, "update", "16", {"verdict": "☠️ 伪协整"}, "残差不平稳")
    raw = decisions_mod.decisions_path(root).read_text(encoding="utf-8")
    assert raw.startswith(HEADER + "\n[[decision]]\n")
    assert "经 PI 批准的决定" not in raw
    [entry] = decisions_mod.entries(root)
    assert (entry["act"], entry.get("proposal"), entry.get("approved_by")) == ("abandoned", None, None)


def test_9_3_a_decisions_file_founded_earlier_keeps_its_own_header(tmp_path):
    """A decisions.toml founded by an earlier Research OS opens with the old
    header: a direct abandonment appends its entry after what is there and
    never rewrites that line (append-only)."""
    root = _project(tmp_path / "proj")
    path = decisions_mod.decisions_path(root)
    path.write_text(OLD_HEADER, encoding="utf-8")
    proposals.write_directly(root, "update", "16", {"verdict": "☠️ 伪协整"}, "残差不平稳")
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith(OLD_HEADER + "\n[[decision]]\n")
    assert HEADER not in raw and len(decisions_mod.entries(root)) == 1

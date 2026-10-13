"""Research OS V10, what the designer's acceptance of the desk on a copy of
the researcher's project found (DESIGN.md 14.3, 14.1, 12.11, 13.4, 9.3):
each with the scenario that would have caught it.

Every test names its scenario."""

from __future__ import annotations

import re

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

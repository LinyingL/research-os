"""Research OS V10, what the designer's acceptance of the desk on a copy of
the researcher's project found (DESIGN.md 14.3, 14.1, 12.11, 13.4, 9.3):
each with the scenario that would have caught it.

Every test names its scenario."""

from __future__ import annotations

import json
import re

import pytest

from researchos import proposals
from researchos.records import decisions as decisions_mod

from test_v8_phase2 import _project
from test_v8_u2 import FORBIDDEN
from test_v9_language import APP_HTML, WEBAPP, _node, needs_node
from test_v10_desk_panel import A16, A17, A18, _case, _run, _st


def _css_rule(selector: str) -> str:
    css = APP_HTML.read_text(encoding="utf-8")
    css = css[:css.index("</style>")]
    found = re.search(r"(?m)^" + re.escape(selector) + r" \{([^}]*)\}", css)
    assert found, selector
    return found.group(1)


# -- the panel's status chooser: a button per status, the status's own marker written before the words (21.3, 14.3, 12.11) --


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_21_3_the_panel_offers_a_button_per_status_and_writes_the_statuss_marker_before_the_words(lang):
    """On the researcher's table the verdict field was a text box with a chip per status that only rewrote a marker
    inside it. Now the cell 「🕒 **活跃候选**」 is read as the status 待定 (pressed) and the words 「**活跃候选**」 (the
    reason field); the marker is in no field (12.11). Pressing 已放弃 writes the project's ☠️ before the same
    words; pressing 待定 again leaves the cell as it is -- nothing is written for it, byte for byte."""
    w = (lambda zh, en: en if lang == "en" else zh)
    case = _case("single", lang)
    page = next(p for path, p in case["get"].items() if path.startswith("/api/node?"))
    page["details"].update(verdict="🕒 **活跃候选**", verdict_status=_st("pending", "待定", "**活跃候选**"))
    case["post"] = {"/api/attempts/preview": {"file": "map.md", "old_row": "a", "new_row": "b", "abandons": False,
                                              "reason_required": False}}
    steps = """
      await window.RODesk.openAttempt(%(a16)s);
      const body = panelPart("panel-body");
      const chosen = () => body.all().filter((k) => k.cls.has("status-pick") && k.cls.has("selected")).map((k) => k.textContent);
      const reason = () => body.all().find((k) => k.cls.has("status-reason")).value;
      global.extra = { picks: body.all().filter((k) => k.cls.has("status-pick")).map((k) => [k.textContent, k.getAttribute("aria-pressed")]),
                       chosen: chosen(), reason: reason(), fields: body.all().filter((k) => k.tag === "INPUT" || k.tag === "TEXTAREA").map((k) => k.value) };
      find(%(abandoned)s, "BUTTON").click(); extra.afterAbandoned = chosen();
      find(%(pending)s, "BUTTON").click(); extra.afterPending = chosen();
      find(%(save)s).click(); await settle(); await settle();
      extra.sentNothing = posted.length; extra.said = panelLines().filter((l) => l.startsWith("notice-status"));
      find(%(abandoned)s, "BUTTON").click();
      find(%(save)s).click(); await settle(); await settle();
    """ % {"a16": json.dumps(A16), "abandoned": json.dumps(w("已放弃", "Abandoned")), "pending": json.dumps(w("待定", "Pending")),
           "save": json.dumps(w("保存", "Save"))}
    out = _run(case, steps, lang=None if lang == "zh" else lang)
    extra = out["extra"]
    assert extra["picks"] == [[w("现行", "Current"), "false"], [w("待定", "Pending"), "true"], [w("不利证据", "Adverse evidence"), "false"],
                              [w("已放弃", "Abandoned"), "false"], [w("被否", "Rejected"), "false"], [w("归档", "Archived"), "false"],
                              [w("其它", "Other"), "false"]]
    assert not any(FORBIDDEN.search(name) for name, _ in extra["picks"])
    assert extra["chosen"] == [w("待定", "Pending")] and extra["reason"] == "**活跃候选**"
    assert "🕒" not in "".join(extra["fields"]) and extra["fields"].count("**活跃候选**") == 1   # the marker is in no field
    assert extra["afterAbandoned"] == [w("已放弃", "Abandoned")] and extra["afterPending"] == [w("待定", "Pending")]
    assert extra["sentNothing"] == 0 and extra["said"] == [f"notice-status: {w('没有需要写入的改动', 'Nothing has changed to write')}"]
    assert out["posted"][0] == ["/api/attempts/preview", {"op": "update", "number": "16", "fields": {"verdict": "☠️ **活跃候选**"}}]
    assert out["posted"][1] == ["/api/attempts/write", out["posted"][0][1]]  # 18.3: the preview is not shown, the write follows


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

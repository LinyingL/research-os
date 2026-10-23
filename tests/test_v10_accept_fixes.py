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


# -- the panel's verdict chips: one per status, each with the table's own marker (14.3, 12.11) ----------

#: The researcher's verdict cells as /api/attempts gives them -- long, each its own, led by
#: their markers -- with the status the engine read from each (verdict_status).
_TABLE = [
    (A18, "✅ 主线：TopicShift 显著，稳健性 3/4", "current", "现行"),
    ("attempt:map.md#2", "✅ **活跃候选**;R2 已回复", "current", "现行"),
    (A16, "🕒 **活跃候选**", "pending", "待定"),
    ("attempt:map.md#4", "🕒 等 2025 年的数据", "pending", "待定"),
    ("attempt:map.md#5", "⚠️ 符号反了(β<0);需复核", "adverse", "不利证据"),
    ("attempt:map.md#6", "📋 归档：并入 #12", "archived", "归档"),
    ("attempt:map.md#7", "⛔ 审稿否决(R2)", "rejected", "被否"),
    ("attempt:map.md#8", "☠️ 审稿1轮 Major(last chance);诚信核查 FAIL;撤稿", "abandoned", "已放弃"),
    ("attempt:map.md#9", "☠️ 伪协整;残差不平稳", "abandoned", "已放弃"),
    # A marker nobody named, but the project counts it as abandoned (abandoned_verdicts):
    # its card's route_status says so, and the chips follow the map.
    (A17, "💀 方向反了", "other", "其它"),
    ("attempt:map.md#11", "待补", "other", "其它"),  # no marker: it offers nothing
]


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_14_3_the_verdict_chips_are_one_per_status_and_put_the_tables_own_marker_before_the_words(lang):
    """On the researcher's table the chips were every distinct verdict cell
    -- long sentences, noise. Now: one chip per status the project holds,
    in the order current, pending, adverse, archived, rejected, abandoned;
    each says the status word only (13.4; no emoji, 12.11); 其它 is not
    offered, for no cell of that status leads with a marker. Clicking
    已放弃 on 「🕒 **活跃候选**」 puts the table's own ☠️ in place of 🕒 and
    keeps the words; 待定 brings 🕒 back; what is previewed is that text."""
    w = (lambda zh, en: en if lang == "en" else zh)
    case = _case("single", lang)
    case["records"] = [{"id": i, "verdict": v, "verdict_status": _st(s, label, v.split(" ", 1)[-1])} for i, v, s, label in _TABLE]
    page = next(p for path, p in case["get"].items() if path.startswith("/api/node?"))
    page["details"].update(verdict="🕒 **活跃候选**", verdict_status=_st("pending", "待定", "**活跃候选**"))
    case["post"] = {"/api/attempts/preview": {"file": "map.md", "old_row": "a", "new_row": "b", "abandons": False,
                                              "reason_required": False}}
    steps = """
      await window.RODesk.openAttempt(%(a16)s);
      const body = panelPart("panel-body");
      const field = body.all().find((k) => k.tag === "INPUT" && k.value === "🕒 **活跃候选**");
      global.extra = { chips: body.all().filter((k) => k.cls.has("verdict-chips")).map((k) => k.kids.map((c) => c.textContent)) };
      find(%(abandoned)s, "BUTTON").click(); extra.abandoned = field.value;
      find(%(pending)s, "BUTTON").click(); extra.pending = field.value;
      find(%(abandoned)s, "BUTTON").click();
      find(%(preview)s).click(); await settle(); await settle();
    """ % {"a16": json.dumps(A16), "abandoned": json.dumps(w("已放弃", "Abandoned")),
           "pending": json.dumps(w("待定", "Pending")), "preview": json.dumps(w("保存", "Save"))}
    out = _run(case, steps, lang=None if lang == "zh" else lang)
    [chips] = out["extra"]["chips"]
    assert chips == [w("现行", "Current"), w("待定", "Pending"), w("不利证据", "Adverse evidence"), w("归档", "Archived"),
                     w("被否", "Rejected"), w("已放弃", "Abandoned")]
    assert not any(FORBIDDEN.search(c) for c in chips)
    assert (out["extra"]["abandoned"], out["extra"]["pending"]) == ("☠️ **活跃候选**", "🕒 **活跃候选**")
    assert out["posted"][0] == ["/api/attempts/preview", {"op": "update", "number": "16", "fields": {"verdict": "☠️ **活跃候选**"}}]
    assert out["posted"][1] == ["/api/attempts/write", out["posted"][0][1]]  # 18.3: the preview is not shown, the write follows


def _desk_alone(steps: str) -> object:
    """desk.js evaluated alone under node (no /i18n.js: the Chinese), then `steps`."""
    return _node("global.window = {};\nglobal.document = { getElementById: () => null };\n"
                 + (WEBAPP / "desk.js").read_text(encoding="utf-8") + "\n" + steps)


@needs_node
def test_14_3_a_statuss_marker_is_the_one_its_cells_lead_with_most_and_a_click_keeps_the_words():
    """The chips' choice, pure: per status the leading token most of its
    cells use, counted only when it is a marker (no letter, digit or CJK);
    a status the researcher named in [verdict_labels] is one chip per word,
    after 已放弃 and before 其它; a status none of whose cells leads with a
    marker gives no chip. A click replaces the field's leading marker, if
    it has one, and keeps every word after it as written."""
    st = lambda status, label: {"status": status, "label": label, "words": ""}  # noqa: E731
    rows = [
        {"verdict": "🧪 试跑", "status": st("other", "其它")},
        {"verdict": "🔬 跑到一半", "status": st("labelled", "进行中")},
        {"verdict": "☠ 方向反了", "status": st("abandoned", "已放弃")},
        {"verdict": "☠️ 伪协整", "status": st("abandoned", "已放弃")},
        {"verdict": "☠️ 撤稿", "status": st("abandoned", "已放弃")},
        {"verdict": "待定 先看", "status": st("pending", "待定")},
        {"verdict": "  ✅  主线", "status": st("current", "现行")},
        {"verdict": "**2** 轮以后 ✅", "status": st("current", "现行")},
        {"verdict": "❓ 不确定", "status": st("other", "其它")},
        {"verdict": "🧪 再跑", "status": st("other", "其它")},
        {"verdict": "🧊 冻结了", "status": st("labelled", "冻结")},
        {"verdict": "", "status": st("other", "其它")},
    ]
    clicks = [["🕒 **活跃候选**", "☠️"], ["⚠️ 符号反了;\n需复核", "✅"], ["  📋   归档：并入 #12", "⛔"],
              ["待定 先看", "☠"], ["**活跃候选**", "🕒"], ["2 轮以后", "✅"], ["", "📋"], ["☠️", "✅"]]
    out = _desk_alone("const D = window.RODesk;\nprocess.stdout.write(JSON.stringify({ choices: D._verdictChoices(%s)"
                      ".map((c) => [c.code, c.word, c.marker]), clicked: %s.map(([text, marker]) => D._withMarker(text, marker)) }));\n"
                      % (json.dumps(rows, ensure_ascii=False), json.dumps(clicks, ensure_ascii=False)))
    assert out["choices"] == [["current", "现行", "✅"], ["abandoned", "已放弃", "☠️"], ["labelled", "进行中", "🔬"],
                              ["labelled", "冻结", "🧊"], ["other", "其它", "🧪"]]
    assert out["clicked"] == ["☠️ **活跃候选**", "✅ 符号反了;\n需复核", "⛔   归档：并入 #12", "☠ 待定 先看", "🕒 **活跃候选**",
                              "✅ 2 轮以后", "📋", "✅"]


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

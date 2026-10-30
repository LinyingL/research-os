"""Research OS V16, task B5-1 (DESIGN.md 21.8, 12.11): the clay colour means something is wrong.

Clay (`--clay`, `--clay-soft`) is the colour of an error, a broken provenance, a warning 21.5 settled, a run out of bounds and a
problem line in 备份. Selection, the current section, links, hover and focus are ink and weight; an assessment's tint is its own
(12.11) and stays. This file lists every selector of the page's two style sheets that may use clay, with its reason, and every place
a script hands a tone named clay to the page, and fails on a new one: a new use is either added here with the reason it is a
problem, or written in ink. Every test names its scenario."""

from __future__ import annotations

import re
from pathlib import Path

from test_v9_language import APP_HTML, WEBAPP

REPO = WEBAPP.parents[2]
WORKSPACE_CSS = WEBAPP / "workspace.css"

#: What a use of clay may be for (each entry below names one, then says which thing it is).
ERROR = "a failure the person has to read"
BROKEN = "a break in the provenance"
WARNING = "a warning 21.5 settled: the paper relies on a figure or table that is broken"
OUT_OF_BOUNDS = "a run that went out of bounds (18.1)"
PROBLEM_LINE = "a problem line of 备份 or of a record file that cannot be read"
ASSESSMENT = "the tint of an assessment, 不利证据 (12.11)"
TOKEN = "the token itself"

#: Every selector that uses clay -- `var(--clay)`, `var(--clay-soft)` or the colour written out -- with the reason it is a problem.
CLAY_SELECTORS: dict[str, str] = {
    ":root": TOKEN + ": --clay and --clay-soft are defined here and nowhere else",
    # the frame
    ".pm-status": ERROR + ": the project menu's forms (a rescan, a rename, a removal that failed)",
    ".refresh-chip.err": ERROR + ": the page could not read the project again",
    ".project-state": ERROR + ": the project is degraded (8.10)",
    ".scan-chip.err": ERROR + ": the scan failed",
    ".state-note": ERROR + ": the in-view companion of the degraded project",
    ".state-note .state-note-title": ERROR + ": its title",
    ".state-note .notice-title": ERROR + ": its title",
    ".notice.bad": ERROR + ": a notice that something is wrong (a ledger Research OS cannot trust, a migration that stopped)",
    ".notice.bad .notice-title": ERROR + ": its title",
    ".notice-status.err": ERROR + ": a write that failed",
    ".panel-status.err": ERROR + ": a panel's write that failed",
    ".error-box": ERROR + ": an error the engine reports, in words with its detail behind 「详情」",
    ".error-box .error-title": ERROR + ": its title",
    ".add-lead.bad": ERROR + ": 添加项目 found the folder cannot be added",
    ".add-note.bad": ERROR + ": a blocker of the GitHub push or of adding a project",
    ".node-failed": ERROR + ": a node page that could not be read",
    ".lit-note": ERROR + ": drafts that could not be read, left out of the numbers above",
    # provenance
    ".status-label.tone-clay": BROKEN + " and " + ASSESSMENT + ": the tone the scripts name clay below; a failed or out-of-bounds run",
    ".desk-break": BROKEN + ": a line whose end the attempt table no longer has",
    ".trace-gap": BROKEN + ": a gap in a trace the paper relies on (21.5 draws the same gap quiet when it does not)",
    ".trace-gap-label": BROKEN + ": its label",
    ".trace-break": BROKEN + ": a trace that breaks",
    ".step-broken": BROKEN + ": a step whose file is gone",
    ".res-cause.warn": WARNING + ": why the source is missing",
    ".res-open.warn": WARNING + ": the way in to mend it",
    ".dc-rel.bad": BROKEN + ": a script that reads a file that is not there (断链)",
    ".dc-filter.problem": BROKEN + ": the count of 缺生成脚本 or 断链 when it is above none",
    ".cv-tag-missing": BROKEN + ": a card whose file is not on disk",
    ".cv-socket.orphan": WARNING + ": a table the chosen paper includes that no script writes",
    ".cv-link.machine.cycle": BROKEN + ": a link that closes a cycle in the lineage (8.4)",
    ".cv-status": ERROR + ": a write of 关系图 that failed",
    ".cv-notice": ERROR + ": the arrangement record cannot be read",
    ".cv-notice .cv-notice-title": ERROR + ": its title",
    ".var-chip.bad": ERROR + ": a card whose log cannot be read or has two histories",
    ".var-line.bad": ERROR + ": a check of a card that does not hold (不符)",
    ".team-line.warn": ERROR + ": the team pages' problems: an unreadable record, a shared folder that is not open, a proposal that cannot be approved",
    # assessments (12.11): the tint of 不利证据
    ".cv-frame.tone-clay .cv-frame-rect": ASSESSMENT + ": an attempt's frame in 关系图",
    ".cv-frame.tone-clay .cv-frame-title": ASSESSMENT + ": its title",
    ".rm-card.tone-clay": ASSESSMENT + ": an attempt's card on the route map",
    # 备份
    ".records-problem": PROBLEM_LINE,
    # the home page (workspace.css)
    ".ws-status.err": ERROR + ": a read or a write of 项目首页 that failed",
    ".ws-unavailable": ERROR + ": a document that cannot be read now",
    ".ws-agent-meta.bad": ERROR + ": a run's own line when it failed",
    ".ws-agent-oob": OUT_OF_BOUNDS + ": its sentence",
    ".ws-agent .status-label.oob": OUT_OF_BOUNDS + ": the state word is ringed",
    ".ws-run-group.ws-oob .ws-run-heading": OUT_OF_BOUNDS + ": the run log's heading",
    ".ws-run-group.ws-oob li": OUT_OF_BOUNDS + ": its lines",
    ".ws-step.bad .ws-step-kind": ERROR + ": a step of a run that failed",
    ".ws-step.bad .ws-step-text": ERROR + ": its words",
}

#: The scripts' own uses: a tone named "clay" that the page draws as `tone-clay` (a state word, a frame, a card) -- each line that
#: holds one is found by a snippet of it. A new line with one fails until it is listed with its reason.
CLAY_TONES: list[tuple[str, str, str]] = [
    ("desk.js", 'adverse: "clay"', ASSESSMENT),
    ("app.html", 'adverse: "clay"', ASSESSMENT),
    ("desk.js", 't("失败"), "clay"', "a run that failed (等你处理, the attempt's tasks)"),
    ("desk.js", 't("越过了限制"), "clay"', OUT_OF_BOUNDS),
    ("desk.js", 'rowShell(t("连线的一端找不到了"), "clay")', BROKEN + ": a line whose end is missing"),
    ("workspace.js", 't("失败"), "clay"', "a run that failed (the task's row)"),
    ("workspace.js", 't("越过了限制"), "clay"', OUT_OF_BOUNDS),
    ("node.js", 'no_source: "clay"', BROKEN + ": a trace with no source (a gap the paper relies on; 21.5 gives the rest `quiet`)"),
    ("node.js", '"plain" : "clay"', WARNING + ": a file that does not exist and a break the paper relies on (`quiet` is drawn plain)"),
    ("node.js", '"clay" : "plain"', WARNING + ": a file that does not exist, drawn clay only when the paper includes it"),
    ("node.js", 'value.tone === "warning" ? "clay"', WARNING + ": the engine's `warning` tone"),
    ("node.js", 'status-label tone-clay", t("缺生成脚本")', WARNING + ": 缺生成脚本 when a figure the paper includes is the one that has none"),
    ("team.js", 'tone: "clay"', BROKEN + " and " + ERROR + ": a record that contradicts itself, a signature that cannot be trusted"),
    ("team.js", 'v.trusted === false ? "clay"', ERROR + ": a signature that does not verify"),
    ("team.js", '"clay" : f.state === "apply"', ERROR + ": a file of a proposal that conflicts or has a problem"),
]

#: Files that hold the colour written out. Only the token's definition and the application icon's chart dot may.
LITERAL_CLAY = re.compile(r"#a64b2a|#f1dcd0|rgba?\(\s*166\s*,\s*75\s*,\s*42|rgba?\(\s*241\s*,\s*220\s*,\s*208|0xA64B2A", re.IGNORECASE)
LITERAL_ALLOWED = {
    ("app.html", "--clay: #A64B2A;"): "the token's definition",
    ("app.html", "--clay-soft: #F1DCD0;"): "the token's definition",
    ("shell/RCEIcon.swift", "let clay = rgb(0xA64B2A)"): "the application icon's chart dot: a mark of the brand, not of a state",
}


# -- reading the style sheets ----------------------------------------------------------------------------------------------


def _style_text(path: Path) -> str:
    """The CSS of `path`: a style sheet as it is, the `<style>` blocks of a page; comments taken out."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".html":
        text = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", text, re.DOTALL))
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _rules(css: str) -> list[tuple[str, str]]:
    """(selector list, declarations) of every rule, at-rules (`@media`) opened and their rules read in place."""
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(css):
        open_at = css.find("{", pos)
        if open_at < 0:
            break
        head = css[pos:open_at].strip()
        depth, end = 1, open_at + 1
        while end < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[end], 0)
            end += 1
        body = css[open_at + 1:end - 1]
        if head.startswith("@"):
            if head.startswith(("@media", "@supports")):
                out.extend(_rules(body))
        else:
            out.append((head, body))
        pos = end
    return out


def _all_rules() -> list[tuple[str, str]]:
    return _rules(_style_text(APP_HTML)) + _rules(_style_text(WORKSPACE_CSS))


def _uses_clay(body: str) -> bool:
    return bool(re.search(r"var\(--clay(?:-soft)?\)", body)) or bool(LITERAL_CLAY.search(body)) or bool(re.search(r"--clay(?:-soft)?\s*:", body))


def _selectors_using_clay() -> set[str]:
    found: set[str] = set()
    for head, body in _all_rules():
        if _uses_clay(body):
            found.update(" ".join(s.split()) for s in head.split(","))
    return found


def _declarations(selector: str) -> dict[str, str]:
    """The declarations of the rule whose selector list holds exactly `selector`, later rules over earlier ones."""
    out: dict[str, str] = {}
    for head, body in _all_rules():
        if selector in {" ".join(s.split()) for s in head.split(",")}:
            for declaration in body.split(";"):
                if ":" in declaration:
                    name, value = declaration.split(":", 1)
                    out[name.strip()] = value.strip()
    return out


# -- the style sheets --------------------------------------------------------------------------------------------------------


def test_clay_is_used_only_by_the_selectors_listed_with_their_reasons():
    """A rule that sets a colour from `--clay` or `--clay-soft` is one of the listed problem states. A new one fails: it is a
    problem (add it, with its reason) or it is not (ink and weight)."""
    new = sorted(_selectors_using_clay() - set(CLAY_SELECTORS))
    assert new == [], f"clay is for what is wrong (21.8); not listed with a reason: {new}"


def test_every_listed_selector_still_uses_clay_so_the_list_does_not_outlive_the_rules():
    """The list is the whole truth, not a growing allowance: a selector that went to ink leaves it."""
    gone = sorted(set(CLAY_SELECTORS) - _selectors_using_clay())
    assert gone == [], f"listed but no rule of these selectors uses clay any more: {gone}"


def test_every_reason_is_one_of_the_problems_clay_stands_for():
    """The reason an entry gives begins with what is wrong -- an error, a break in provenance, a settled warning, an out-of-bounds
    run, a problem line, an assessment's own tint -- never with a selection or a state of the interface."""
    kinds = (ERROR, BROKEN, WARNING, OUT_OF_BOUNDS, PROBLEM_LINE, ASSESSMENT, TOKEN)
    for selector, reason in CLAY_SELECTORS.items():
        assert reason.startswith(kinds), (selector, reason)
    for file, snippet, reason in CLAY_TONES:
        assert reason.startswith(kinds) or reason.startswith("a run that failed"), (file, snippet, reason)


def test_selection_the_current_section_links_hover_and_focus_are_ink_and_weight():
    """21.8: the sidebar's current item, a sub-navigation's current tab and filter, the version tab of a variable card, the
    selected route-map card and 关系图 node, links, hover and focus take no clay; a hand-drawn link of 关系图 is told from a
    machine one by weight (and its dot) in ink."""
    ink = "var(--ink)"
    side = _declarations('.side-item[aria-current="page"]')
    assert side["border-left-color"] == ink and side["font-weight"] == "600"
    assert _declarations(".side-item:focus-visible")["outline"] == "1px solid " + ink
    for selector, name in [('.subnav-item[aria-selected="true"]', "border-bottom-color"), ('.dc-filter[aria-pressed="true"]', "border-bottom-color"),
                           (".lit-tab.active", "border-bottom-color"), (".var-version-btn.active", "border-color"),
                           (".lit-library-item.active", "border-left-color"), (".desk-link:hover", "border-bottom-color"),
                           (".node-link:hover", "border-bottom-color"), (".route-title:hover", "border-bottom-color"),
                           (".lit-ref-open:hover", "border-bottom-color"), (".pm-github a:hover", "border-bottom-color")]:
        assert _declarations(selector)[name] == ink, selector
    assert _declarations(".var-version-btn.active")["box-shadow"] == "inset 0 -2px 0 " + ink
    assert _declarations(".rm-card.selected")["box-shadow"] == "0 0 0 2px " + ink
    assert _declarations(".cv-node.selected .cv-card")["stroke"] == ink
    for selector in (".node-link:focus-visible", ".desk-link:focus-visible", ".subnav-item:focus-visible", ".dc-filter:focus-visible",
                     ".trace-item:focus-visible", ".res-draft-summary:focus-visible", ".lit-search:focus-visible", ".cv-btn:focus-visible",
                     ".form-input:focus"):
        assert ink in _declarations(selector)["outline"], selector
    # links are ink: the literature links, the 撤销 and 详情 of a message, the text buttons of the canvas, the menu's own action
    for selector in (".lit-link", ".lit-link-btn", ".err-detail-toggle", ".cv-text-btn", ".cv-status-action", ".pm-row .pm-sub"):
        assert _declarations(selector)["color"] == ink, selector
    machine, human = _declarations(".cv-link.machine"), _declarations(".cv-link.human")
    assert human["stroke"] == ink and float(human["stroke-width"]) > float(machine["stroke-width"])
    assert _declarations(".cv-link-dot")["fill"] == ink and _declarations(".trace-line.human")["stroke"] == ink
    assert _declarations(".trace-dot")["fill"] == ink
    assert _declarations(".rm-line-path")["stroke"] == ink and _declarations(".rm-handle")["border"].endswith(ink)
    # the member's own mark and the deleted line of a diff are not a problem either
    assert _declarations(".own-mark")["color"] == ink and _declarations(".own-mark")["font-weight"] == "600"
    assert "clay" not in _declarations(".dl-row.del")["background"] and "clay" not in _declarations(".diff-row.old")["background"]


# -- the scripts -----------------------------------------------------------------------------------------------------------


def _script_lines(path: Path) -> list[str]:
    """The code lines of a served script (or of the script blocks of a page), comments and blank lines left out."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".html":
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)  # a comment that quotes a tag is not one
        text = "\n".join(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", text, re.DOTALL))
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("//", "/*", "*")):
            continue
        lines.append(re.sub(r"\s//\s.*$", "", line))
    return lines


def _scripts() -> list[Path]:
    return sorted(WEBAPP.glob("*.js")) + [APP_HTML]


def test_a_script_hands_out_the_clay_tone_only_for_what_is_wrong():
    """Every line of the page's scripts that names the tone clay (`"clay"`, `tone-clay`) or the colour is one of the listed uses; a
    new one fails, and so does a listed one that no line holds any more."""
    held = {(file, snippet): False for file, snippet, _ in CLAY_TONES}
    unlisted = []
    for path in _scripts():
        for line in _script_lines(path):
            if not re.search(r'"clay"|tone-clay|--clay|clay-soft', line):
                continue
            matches = [(f, s) for (f, s, _r) in CLAY_TONES if f == path.name and s in line]
            if not matches:
                unlisted.append((path.name, line[:140]))
            for key in matches:
                held[key] = True
    assert unlisted == [], f"a script names the tone clay for something that is not listed as wrong: {unlisted}"
    assert [key for key, found in held.items() if not found] == [], "listed, but no line of that script holds it any more"


def test_the_assessments_tone_comes_from_the_engine_with_adverse_as_the_one_clay():
    """12.11: the tone of an assessment is the engine's (`desk.TONES`): only 不利证据 is clay; the others are the olive, gray and
    ochre of 现行, 已放弃 and 待定."""
    source = (REPO / "src" / "researchos" / "desk.py").read_text(encoding="utf-8")
    assert re.search(r'TONES = \{"current": "olive", "abandoned": "gray", "pending": "ochre", "adverse": "clay"\}', source)
    clay_in_python = [p.name for p in sorted((REPO / "src" / "researchos").rglob("*.py"))
                      for line in p.read_text(encoding="utf-8").splitlines()
                      if re.search(r'"clay"', line) and not line.strip().startswith("#")]
    assert clay_in_python == ["desk.py"]


def test_the_colour_written_out_is_in_the_token_and_the_icon_alone():
    """`#A64B2A` and `#F1DCD0` (or their rgb) are the tokens' definition; the application icon's chart dot is the brand's mark."""
    found = []
    for path in sorted(WEBAPP.rglob("*")):
        if path.suffix not in (".html", ".js", ".css", ".swift"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if LITERAL_CLAY.search(line):
                found.append((str(path.relative_to(WEBAPP)), line.strip()))
    unlisted = [item for item in found if not any(item[0] == f and s in item[1] for (f, s) in LITERAL_ALLOWED)]
    assert unlisted == [], f"the colour is written out where only the token may: {unlisted}"
    assert {(f, s) for (f, s) in LITERAL_ALLOWED if any(f == a and s in b for a, b in found)} == set(LITERAL_ALLOWED)

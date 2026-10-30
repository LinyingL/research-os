"""Research OS V10, increment C3 (DESIGN.md 14.6, 13.7): the desk is a
converted surface -- with the panels it opens: 待复核, its judgments,
their links and bases in words, the acts and the variable cards' part, its
reasons composed by the engine in the request's language; the refusals
shown there and their 「详情」. In English nothing on them is Chinese but
the researcher's own text; in Chinese they read exactly as before. The
page's own helpers (app.html's blocks, desk.js, node.js) run under node
with a stand-in DOM; the engine's words come over HTTP. Every test names
its scenario."""

from __future__ import annotations

import hashlib
import json

import pytest

from researchos import db
from researchos.records import cards, implementation, judgements
from researchos.records.situation import index_db_path

from test_v10_desk_panel import _PANEL_DOM, _case
from test_v10_desk_payload import _single
from test_v5_phase9 import _two_on_one_script
from test_v8_phase2 import _call
from test_v8_phase3 import _serve
from test_v8_u2 import FORBIDDEN
from test_v9_language import APP_HTML, CJK, WEBAPP, _i18n_source, _node, _strings, needs_node

SCRIPT, DATA, DATA2 = "script:复现包/17-叙事.Rmd", "dataset:复现包/Data/panel.csv", "dataset:复现包/Data/panel2.csv"
#: The researcher's own text in the fixtures: file names, a note, a claim, a card's name.
_THEIRS = ["复现包", "17-叙事.Rmd", "先看缺失值", "精度为 0.87", "TopicShift 波动", "事件轴", "换轴", "先看",
           "这里的缺失值要先处理", "方向反了"]


def _app_slice(src: str, start: str, end: str) -> str:
    a = src.index(start)
    return src[a:src.index(end, a) + (len(end) if end == "\n}\n" else 0)]


def _page_helpers() -> str:
    """app.html's own code for what the desk opens: the language helpers,
    the judgment and variable wording, the error helper, setStatus and the
    待复核 panel with the cards' part -- as served."""
    src = APP_HTML.read_text(encoding="utf-8")
    parts = [("// -- The interface language (DESIGN.md 13.6)", "// -- Small shared state"),
             ("// -- Judgment wording (pure", "// -- end of judgment wording"),
             ("const BLOCKING_ERROR_TEXT = {", "// DESIGN.md 8.8: what the user reads"),
             ("function ownMark(item) {", "\n}\n"), ("function formFieldEl(", "\n}\n"),
             ("function keyOf(x) {", "// -- 你的记录与备份 (9.2"),
             ("// -- Variable wording (pure", "// -- end of variable wording"),
             ("const varState = {", "async function loadVariablesView("),
             ("function cardReviewMember(", "\n}\n"), ("function renderCardReviewGroups(", "\n}\n")]
    # In a scope of their own, then put on `global` over the stand-in's stubs.
    return "(function () {\n" + "\n".join(_app_slice(src, a, b) for a, b in parts) + r"""
var lastGeneration = 0;
const JUDGE_UNDO_MS = 10000;
const panelTitle = document.getElementById("panel-title");
const panelSubtitle = document.getElementById("panel-subtitle");
const panelBody = document.getElementById("panel-body");
function openVariableCard() {}
async function refreshCurrentView() {
  const s = state.panelSubject;
  if (s && s.kind === "review") await renderReviewPanel(s);
}
Object.assign(global, { setStatus, renderBlockingError, ownMark, formFieldEl, humanRecordsBlocked, openReviewPanel,
  refreshCurrentView });
})();
global.button = (text) => panelPart("panel-body").all().find((k) => k.tag === "BUTTON" && k.kids.join("") === text);
"""


def _w(lang: str):
    return lambda zh, en: en if lang == "en" else zh


def _review(lang: str) -> dict:
    """GET /api/review as the engine words it in `lang`: a judgment whose
    basis changed (a note, a candidate), one migrated whose machine no longer
    finds it, a conflict between a migrated and a recorded judgment, one held
    and one not in the index; the variable cards on one changed script, one
    of them waiting on its draft."""
    w = _w(lang)
    zh_en = lambda table_zh, table_en, key: w(table_zh[key], table_en[key])  # noqa: E731
    reason = lambda key: zh_en(judgements.REASON_LABELS, judgements.REASON_LABELS_EN, key)  # noqa: E731
    migrated_at = w(judgements.MIGRATED_AT_LABEL, judgements.MIGRATED_AT_LABEL_EN)
    link = {"src": SCRIPT, "dst": DATA, "type": "reads", "extractor": "python-ast"}
    review = [
        {**link, "outcome": "review", "reason": "basis_changed", "label": reason("basis_changed"), "verdict": "confirmed",
         "at": "2026-10-04T21:15:03+02:00", "note": "先看缺失值", "basis": {"calls": ["read.csv", "read_dta"]},
         "basis_now": None, "candidates": [{**link, "dst": DATA2, "status": "auto"}], "detail": {}, "migrated": False},
        {**link, "dst": "claim:paper.md#c1", "type": "backed_by", "outcome": "review", "reason": "not_produced",
         "label": reason("not_produced"), "verdict": "rejected", "at": "2026-09-01T08:00:00+02:00", "note": None,
         "basis": {"sentence": "精度为 0.87", "number": "0.87", "metrics": {"acc": 0.87}}, "basis_now": None,
         "candidates": [], "detail": {"migrated": True}, "migrated": True, "at_label": migrated_at},
        {**link, "dst": DATA2, "outcome": "conflict", "reason": "record_conflict", "label": reason("record_conflict"),
         "verdict": "rejected", "candidates": [], "migrated": False,
         "detail": {"contradicts": True, "common": [{"verdict": "confirmed", "at": "2026-08-01T10:00:00+02:00", "via": "canvas"}],
                    "branches": [[{"verdict": "confirmed", "at": "2026-08-02T10:00:00+02:00", "via": "cli", "note": "先看缺失值"}],
                                 [{"verdict": "rejected", "via": "migrated", "migrated": True, "at_label": migrated_at}]]}},
    ]
    held = [{**link, "outcome": "held", "verdict": "confirmed", "detail": {"sources": ["复现包/17-叙事.Rmd"]}}]
    absent = [{**link, "dst": DATA2, "outcome": "not_in_index", "verdict": "rejected", "at": "2026-10-01T08:00:00+02:00"}]

    def card(name: str, card_id: str, draft: int | None, code: str = "script_changed") -> dict:
        I = implementation  # noqa: N806
        script = {"path": "build.py", "state": "changed", "text": w(I.CHANGED_LABEL, "Changed")} if code == "script_changed" \
            else {"path": "build.py", "state": "same", "coverage": "full", "text": zh_en(I.COVERAGE_LABELS, I.COVERAGE_LABELS_EN, "full")}
        return {"card": card_id, "name": name, "version": 1, "signature": "s-" + card_id, "under_review": True,
                "script": script,
                "inputs": [{"dataset": "Data/x.csv", "state": "same", "coverage": "size",
                            "text": zh_en(I.COVERAGE_LABELS, I.COVERAGE_LABELS_EN, "size")}],
                "reasons": [{"code": code, "label": zh_en(I.REASON_LABELS, I.REASON_LABELS_EN, code)}],
                "needs_data_version": code == "input_changed", "draft": draft, "counted": draft is None,
                "draft_note": w(I.DRAFT_WAITING, I.DRAFT_WAITING_EN).format(n=draft) if draft else None,
                "cannot_tell": w(I.CANNOT_TELL, I.CANNOT_TELL_EN)}

    cards_part = {"groups": [{"key": "script:build.py", "script": "build.py", "counted": True,
                              "message": w("此脚本的改动涉及 3 个变量", "This script’s change touches 3 variables"),
                              "cards": [card("TopicShift 波动", "topicshift", None), card("RV", "rv", 2), card("Size", "size", None)]},
                             {"key": "card:solo", "script": None, "counted": True, "message": None,
                              "cards": [card("Solo", "solo", None, "input_changed")]}],
                  "count": 2, "cannot_tell": w(implementation.CANNOT_TELL, implementation.CANNOT_TELL_EN)}
    return {"review": review, "count": 3, "source_unreadable": held, "not_in_index": absent, "ledger": None, "cards": cards_part}


def _run_review(lang: str | None, steps: str, post: dict | None = None) -> dict:
    """The PI's desk of test_v10_desk_panel with 待复核 waiting: opens it,
    runs `steps` (JS, awaited) and reports the panel's lines and what was
    posted. `lang` None: no /i18n.js at all (the page before it loads)."""
    case = _case("pi", lang or "zh")
    case["get"]["/api/review"] = _review(lang or "zh")
    case["post"] = post or {}
    i18n = "" if lang is None else f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
    script = ("global.window = {};\n" + i18n + _PANEL_DOM + _page_helpers()
              + (WEBAPP / "desk.js").read_text(encoding="utf-8") + "\n" + (WEBAPP / "node.js").read_text(encoding="utf-8")
              + "\nglobal.RONode = Object.assign({}, window.RONode, { openNode: (id) => opened.push(id) });\n"
              + f"(async () => {{ const c = {json.dumps(case, ensure_ascii=False)}; await openDesk(c);\n{steps}\n"
              " process.stdout.write(JSON.stringify({ lines: panelLines(), posted, extra: global.extra || null })); })();\n")
    return _node(script)


def _chinese(lines: list[str]) -> list[str]:
    """Lines with Chinese once the researcher's own text is taken out."""
    out = []
    for line in lines:
        rest = line
        for theirs in sorted(_THEIRS, key=len, reverse=True):
            rest = rest.replace(theirs, "")
        if CJK.search(rest):
            out.append(line)
    return out


# -- the engine: /api/review in the request's language -------------------------------------------------


def test_14_6_api_review_words_its_reasons_in_the_requests_language(tmp_path):
    """Two judgments whose basis changed: their reason is 依据已变化 for a
    Chinese page (and the command line) and "The basis changed" for an
    English one; nothing else in the English list is Chinese."""
    root, _keys = _single(tmp_path)
    with _serve(root) as base:
        status, zh = _call(base + "/api/review")
        assert status == 200
        status, en = _call(base + "/api/review", headers={"X-ResearchOS-Lang": "en"})
        assert status == 200
    assert [i["label"] for i in zh["review"]] == ["依据已变化", "依据已变化"]
    assert [i["label"] for i in en["review"]] == ["The basis changed", "The basis changed"]
    assert [i["src"] for i in zh["review"]] == [i["src"] for i in en["review"]] and zh["count"] == en["count"] == 2
    assert [(k, s) for k, s in _strings(en) if CJK.search(s)] == []


def test_14_6_the_variable_cards_part_of_the_review_follows_the_language(tmp_path):
    """9.11 #17 on an English screen: one script changed under two cards --
    the group, each card's reason, its coverage and what Research OS cannot
    tell are English; the Chinese list is the one stored, as before."""
    root = _two_on_one_script(tmp_path)
    (root / "build.py").write_text((root / "build.py").read_text() + "df = df.dropna()\n")
    ident = cards._identity_now(root)
    conn = db.connect(index_db_path(ident.id))
    try:
        cards.apply_cards(conn, root, identity=ident)
        zh, default, en = (implementation.review_groups(conn, "zh"), implementation.review_groups(conn),
                           implementation.review_groups(conn, "en"))
    finally:
        conn.close()
    assert zh == default and zh["groups"][0]["message"] == "此脚本的改动涉及 2 个变量"
    (group,) = en["groups"]
    assert group["message"] == "This script’s change touches 2 variables" and en["cannot_tell"] == implementation.CANNOT_TELL_EN
    for member in group["cards"]:
        assert [r["label"] for r in member["reasons"]] == ["The implementing script changed after confirmation"]
        assert member["cannot_tell"] == implementation.CANNOT_TELL_EN and member["script"]["text"] == "Changed"
        assert member["inputs"][0]["text"] == "Whole file unchanged"
    theirs = ("name",)  # a card's name is the researcher's
    assert [(k, s) for k, s in _strings(en) if CJK.search(s) and k not in theirs] == [] and zh["count"] == en["count"] == 1


# -- the page: 待复核 as the desk opens it ------------------------------------------------------------------


#: What the column's 复核 does (desk.js renderReview): the whole list in the side panel.
_OPEN_FROM_THE_COLUMN = "  await openReviewPanel(null);\n  await settle();\n"


@needs_node
def test_14_6_the_review_panel_the_desk_opens_reads_in_english():
    """The column's 复核 opens 待复核 in the side panel: its title, the
    reasons, the basis then and now, the earlier judgment, the candidate, a
    conflict's two copies, the acts, what is not counted, the variable cards
    -- English, the researcher's own file names, note and claim as written."""
    out = _run_review("en", _OPEN_FROM_THE_COLUMN)
    lines = out["lines"]
    assert _chinese(lines) == []
    assert not [line for line in lines if FORBIDDEN.search(line)]
    for expected in (
        "panel-title: Needs review", "panel-subtitle: Earlier link judgments and variable cards whose basis changed, waiting for another look",
        "review-link: 17-叙事.Rmd reads panel.csv", "review-reason: The basis changed",
        "review-was: Earlier link judgment: Confirmed · 2026-10-04 21:15", "review-note: Comment: 先看缺失值",
        "basis-col-title: The basis then", ": Calls: read.csv, read_dta", "basis-col-title: Now",
        ": This scan gave no basis", "btn btn-small: Apply the earlier link judgment to it",
        "btn btn-small: Still holds", "btn btn-small: Change to rejected", "btn btn-small: Withdraw",
        "form-input:  =  (Comment (optional))",
        "review-was: Earlier link judgment: Rejected · Migrated from the old index (when the link judgment was made is not known)",
        ": Claim: “精度为 0.87”", ": Number: 0.87", ": Metrics: acc = 0.87",
        ": This scan read both ends but did not find this link",
        "review-reason: The records disagree; to be settled", "basis-col-title: Already in the records",
        "basis-col-title: Migrated", ": 2026-08-02 10:00 · Confirmed (in the command line) · 先看缺失值",
        ": Migrated from the old index (when the link judgment was made is not known) · Rejected",
        "review-link: 17-叙事.Rmd is based on Claim (paper.md) (title: script:复现包/17-叙事.Rmd\nclaim:paper.md#c1\n(backed_by · python-ast))",
        "review-note: Entries both share: 2026-08-01 10:00 · Confirmed (on the relationship map)",
        "btn btn-small: Settle as confirmed", "btn btn-small: Settle as rejected",
        "review-quiet-title: Not counted in Needs review",
        "review-quiet-title: Variable cards: the implementation changed",
        "review-reason: This script’s change touches 3 variables", "review-reason: The input data changed after confirmation",
        "form-input:  =  (The new data version (required), e.g. 2017-01 to 2024-12, downloaded again 2026-10)",
        "var-line soft: Implementing script build.py: Whole file unchanged",
        "answer-help: Records one entry for each of the 2 cards below; you can also answer them one by one.",
        "review-reason: The implementing script changed after confirmation",
        "var-line soft: Implementing script build.py: Changed",
        "var-line soft: Input Data/x.csv: Size unchanged (content not compared)",
        "btn btn-primary: All constructions unchanged (title: Record “Construction unchanged” for each card below)",
        "btn: Construction unchanged (title: Record that the implementation changed but this version’s construction did not (with the current fingerprint))",
        "review-note: Draft v2 is open: once it is confirmed, it replaces this version",
        "review-link: Input data",
    ):
        assert expected in lines, expected
    quiet = [line for line in lines if line.startswith("review-quiet: ")]
    assert quiet == ["review-quiet:  · Source file cannot be read right now: 复现包/17-叙事.Rmd · Link judgment kept (Confirmed)",
                     "review-quiet:  · This link is not recorded yet · Your link judgment: Rejected (2026-10-01 08:00)"]


@needs_node
def test_14_6_an_act_and_a_refusal_in_the_review_panel_read_in_english():
    """仍然成立 on the first judgment records it again and offers 撤销 in
    English; 撤回 on the conflict, refused because the judgments' record
    cannot be read, says so in English with the engine's own text behind
    “Details”."""
    steps = (_OPEN_FROM_THE_COLUMN
             + "  button('Still holds').click();\n  await settle(); await settle();\n"
             + "  global.extra = panelLines().filter((l) => l.includes('Recorded') || l.includes('Undo'));\n"
             + "  answers.length = 0;\n")
    out = _run_review("en", steps, post={"/api/judgements": {"ok": True, "generation": 4}})
    assert out["posted"] == [["/api/judgements", {"src": SCRIPT, "dst": DATA, "type": "reads", "extractor": "python-ast",
                                                 "verdict": "confirmed"}]]
    assert out["extra"] == [": Recorded: still holds: 17-叙事.Rmd reads panel.csv", "err-detail-toggle: Undo"]
    refused = (_OPEN_FROM_THE_COLUMN
               + "  const conflict = panelPart('panel-body').all().filter((k) => k.tag === 'BUTTON' && k.kids.join('') === 'Withdraw')[2];\n"
               + "  conflict.click();\n  await settle(); await settle();\n")
    out = _run_review("en", refused, post={"/api/judgements": {"refuse": "record_untrusted"}})
    shown = [line for line in out["lines"] if line.startswith("err-text") or line.startswith("err-detail-toggle")]
    assert shown == ["err-text: The link judgments’ record file cannot be read right now; restore it first",
                     "err-detail-toggle: Details"]
    assert _chinese(out["lines"]) == []


#: sha256 of the Chinese 待复核 panel of _review("zh"), drawn without /i18n.js -- the page as it
#: was before V10 made it English-ready (checked against that source, byte for byte): 13.8 #1, a
#: Chinese screen reads exactly as before.
#: Moved on purpose by DESIGN.md 18.2 (task V14): a person's confirming or rejecting of a machine
#: link is 连线确认 where the panel said 判断 (the subtitle, 原连线确认, the old-link buttons and notes,
#: the quiet rows, the migrated label) -- checked by putting the old words back: the old digest.
#: Moved again for the finding that the count also holds variable cards: the subtitle reads
#: 依据变了的旧连线确认和变量卡 (checked by putting the old subtitle back: the digest 818b8de6...).
_REVIEW_ZH_SHA256 = "1cd7bd2c88e6a24ce863dc6aa89d0fe5cc224c283ebbed31454248de8d24ded9"


@needs_node
def test_14_6_the_review_panel_in_chinese_reads_as_before():
    """No /i18n.js, and a Chinese screen: every line as the page wrote it
    before it was converted."""
    plain, chinese = _run_review(None, _OPEN_FROM_THE_COLUMN), _run_review("zh", _OPEN_FROM_THE_COLUMN)
    assert plain["lines"] == chinese["lines"]
    lines = chinese["lines"]
    for expected in ("panel-title: 待复核", "review-link: 17-叙事.Rmd 读取 panel.csv", "review-was: 原连线确认：确认 · 2026-10-04 21:15",
                     ": 调用：read.csv、read_dta", "btn btn-small: 仍然成立", "review-quiet-title: 不计入待复核",
                     "var-line soft: 实现脚本 build.py：有变化"):
        assert expected in lines, expected
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    assert digest == _REVIEW_ZH_SHA256


@needs_node
@pytest.mark.parametrize("lang, said", [
    ("en", ["1 item needs review", "2 items need review", "1,497 items need review"]),
    ("zh", ["1 项待复核", "2 项待复核", "1497 项待复核"]),
])
def test_14_6_the_top_bars_review_count_has_a_singular_and_a_plural(lang, said):
    """Ruled 2026-10-13: the header's 「N 项待复核」 is frame -- English in
    English mode with a singular and a plural and its thousands separated
    (13.5); the Chinese as before. app.html's own code for it, under node."""
    src = APP_HTML.read_text(encoding="utf-8")
    chip = _app_slice(src, 'if (typeof data.review === "number" && data.review > 0) {', "line.append(btn);")
    script = ("global.window = {};\n" + f"window.ROI18N_SAVED = {json.dumps(lang)};\n" + _i18n_source()
              + _app_slice(src, "// -- The interface language (DESIGN.md 13.6)", "// -- Small shared state")
              + _app_slice(src, "function groupedNumber(n) {", "\n}\n")
              + "\nconst said = [];\nglobal.document = { createElement: () => ({ addEventListener() {} }) };\n"
              + "for (const n of [1, 2, 1497]) {\n  const data = { review: n };\n"
              + "  const line = { append: (b) => said.push([b.className, b.textContent]) };\n"
              + chip + "line.append(btn);\n  }\n}\nprocess.stdout.write(JSON.stringify(said));\n")
    assert _node(script) == [["review-count-btn line-part has", text] for text in said]



# -- the count that holds more than links ---------------------------------------------------------------


def test_14_6_the_review_count_can_be_only_variable_cards_so_its_words_name_items_not_links(tmp_path):
    """DESIGN.md 18.2: 连线确认 is a person's confirming or rejecting of a machine
    link. A project with no link judgment to review and one script changed
    under two variable cards still shows a 待复核 count of 1 (the engine adds
    the cards' groups to the links), and its list holds only that group. So the
    top bar, the desk's line and the panel's subtitle say 项 / items, and the
    panel's subtitle names the variable cards beside the links."""
    root = _two_on_one_script(tmp_path)
    (root / "build.py").write_text((root / "build.py").read_text() + "df = df.dropna()\n")
    ident = cards._identity_now(root)
    conn = db.connect(index_db_path(ident.id))
    try:
        cards.apply_cards(conn, root, identity=ident)
    finally:
        conn.close()
    with _serve(root) as base:
        status, summary = _call(base + "/api/summary")
        assert status == 200
        status, review = _call(base + "/api/review")
        assert status == 200
    assert review["review"] == [] and review["cards"]["count"] == 1
    assert summary["review"] == 1  # one item, and it is not a link
    src = APP_HTML.read_text(encoding="utf-8")
    chip = _app_slice(src, 'if (typeof data.review === "number" && data.review > 0) {', "line.append(btn);")
    assert 't("{n} 项待复核"' in chip and "连线确认" not in chip.replace("查看依据变了的旧连线确认和变量卡", "")
    subtitle = _app_slice(src, 'panelTitle.textContent = t("待复核");', "panelBody.innerHTML")
    assert "变量卡" in subtitle and "旧连线确认" in subtitle
    desk = (WEBAPP / "desk.js").read_text(encoding="utf-8")
    assert 't("{n} 项的依据变了，等你复核。"' in desk and "条连线确认的依据变了" not in desk

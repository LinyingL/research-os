"""Research OS V17, task B3c (DESIGN.md 22.3, 22.0.4): a claim of the paper whose sentence lies in a found passage with a
supporting file shows 「人工声明的依据文件：{file}」, in quiet text and a column of its own, on 图表与论断 and on its page;
its state and its 缺来源 warning stay what `backed_by` makes them. Whether a claim lies in a passage is decided by
position: the claims reader gives each sentence its span (code points, a CRLF as two: 22.1's coordinates), tested
against the passage's found range. Reading writes nothing. Every test names its scenario."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from researchos import latex_edit, project
from researchos.ingest import claims as claims_ingest
from researchos.records import evidence, papers

from test_v16_provenance import _page, _results
from test_v16_provenance_js import _run as node_run
from test_v8_phase2 import _scan
from test_v9_language import needs_node

PATH = "paper/main.tex"
LINE = "引言里没有数字。效应是 0.42，而且显著。😀 另一句 12.5\\% 的样本。"
SENTENCE = "效应是 0.42，而且显著。"
LINES = ["\\documentclass{article}", "\\begin{document}", "\\section{结果}", LINE, "",
         "Unrelated text with 3.14 in it.", "\\end{document}", ""]
CRLF, LF = "\r\n".join(LINES), "\n".join(LINES)


def _sha(root: Path) -> str:
    return hashlib.sha256((root / PATH).read_bytes()).hexdigest()


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _link(root: Path, words: str, file: str) -> None:
    text = (root / PATH).read_bytes().decode("utf-8")
    start = text.index(words)
    latex_edit.evidence_act(root, "link", {"path": PATH, "revision": _sha(root), "from": start,
                                           "to": start + len(words), "file": file})


@pytest.fixture
def paper(tmp_path) -> Path:
    """A CRLF draft with Chinese text and an emoji, set as the paper and scanned. Three passages are linked: exactly the
    sentence of 0.42 (figures/f1.png); the whole line holding 0.42 and 12.5 (data/x.csv); only the number 0.42, part of
    its sentence (data/y.csv)."""
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    for rel in ("figures/f1.png", "data/x.csv", "data/y.csv"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"x")
    (root / PATH).parent.mkdir(parents=True)
    (root / PATH).write_bytes(CRLF.encode("utf-8"))
    _link(root, SENTENCE, "figures/f1.png")
    _link(root, LINE, "data/x.csv")
    _link(root, "0.42", "data/y.csv")
    papers.set_marked(root, PATH, True, via="cli")
    _scan(root)
    return root


def _claims(root: Path) -> dict[str, dict]:
    page = _results(root, PATH)
    return {c["number"]: c for g in page["sections"] for c in g["claims"]}


def test_each_claims_span_is_its_sentence_in_code_points_with_a_crlf_counted_as_two(tmp_path):
    """The same draft with CRLF and with LF endings: the span is where the sentence stands in the decoded file -- an
    emoji before it one code point, each CRLF before it two -- and the claims, their ids and lines are the same."""
    (tmp_path / "paper").mkdir()
    spans = {}
    for name, text in (("crlf", CRLF), ("lf", LF)):
        (tmp_path / PATH).write_bytes(text.encode("utf-8"))
        got = claims_ingest.parse_tex_claims(tmp_path, PATH)
        assert [c.printed_number for c in got] == ["0.42", "12.5", "3.14"]
        for claim in got:
            assert text[claim.span[0]:claim.span[1]] == claim.sentence
        spans[name] = got
    crlf, lf = spans["crlf"], spans["lf"]
    assert [(c.id, c.line, c.sentence) for c in crlf] == [(c.id, c.line, c.sentence) for c in lf]
    assert [c.span[0] - l.span[0] for c, l in zip(crlf, lf)] == [3, 3, 5]   # the CRLFs before each sentence
    assert crlf[0].span[0] == CRLF.index(SENTENCE)
    assert crlf[1].sentence == "😀 另一句 12.5\\% 的样本。"   # the emoji is one code point of it, as Python counts


def test_a_claim_inside_a_found_passage_names_its_supporting_files_and_keeps_its_missing_source_warning(paper):
    """0.42 lies in the passage that is exactly its sentence and in the whole line: both files, never the passage of the
    number alone, which covers only part of the sentence. 12.5 lies only in the line; 3.14 in no passage. The state of
    each is 缺来源 with its warning, as before: a declaration does not clear it (22.0.4), and nothing is put under 依据."""
    claims = _claims(paper)
    assert claims["0.42"]["declared"] == ["data/x.csv", "figures/f1.png"]
    assert claims["12.5"]["declared"] == ["data/x.csv"]
    assert claims["3.14"]["declared"] == []
    for claim in claims.values():
        assert (claim["trace"]["state"], claim["trace"]["label"], claim["trace"]["tone"]) == ("no_source", "缺来源", "warning")
        assert [c["code"] for c in claim["trace"]["causes"]] == ["no_backing"]
    page = _page(paper, claims["0.42"]["id"])
    assert page["details"]["declared"] == ["data/x.csv", "figures/f1.png"]
    before = page["trace"]["columns"][0]
    assert before["key"] == "before" and before["items"] == [] and before["gap"]["kind"] == "no_backing"
    assert _page(paper, claims["3.14"]["id"])["details"]["declared"] == []


def test_a_passage_waiting_for_review_or_withdrawn_declares_nothing(paper):
    """The line is reworded elsewhere: its passage is 待复核 「依据已变化」 and no longer declares a file for the claims in it,
    while the sentence of 0.42, still found, does; withdrawn, it declares nothing either."""
    text = (paper / PATH).read_bytes().decode("utf-8")
    (paper / PATH).write_bytes(text.replace("另一句", "另外一句").encode("utf-8"))
    _scan(paper)
    claims = _claims(paper)
    assert claims["0.42"]["declared"] == ["figures/f1.png"] and claims["12.5"]["declared"] == []
    sentence = next(item for item in evidence.links_now(paper)[0].values() if item["file"] == "figure:figures/f1.png")
    latex_edit.evidence_act(paper, "withdraw", {"link": sentence["link"]})
    assert all(c["declared"] == [] for c in _claims(paper).values())


def test_reading_the_declarations_writes_nothing_and_a_draft_that_is_not_tex_has_none(paper):
    """图表与论断's claims, a claim's page and the reader itself leave every file of the project as it was; a Markdown
    draft, a missing draft and an untrusted ledger declare nothing."""
    before = _tree(paper)
    _claims(paper)
    _page(paper, _claims(paper)["0.42"]["id"])
    assert latex_edit.declared_by_claim(paper, PATH)
    assert latex_edit.declared_by_claim(paper, "paper/none.tex") == {}
    assert latex_edit.declared_by_claim(paper, "notes.md") == {}
    assert _tree(paper) == before
    ledger = evidence.evidence_path(paper)
    ledger.write_bytes(ledger.read_bytes() + b"\n[[passage_evidence]]\nnot = valid\n")
    assert latex_edit.declared_by_claim(paper, PATH) == {}


# -- the row and the page (node.js) ---------------------------------------------------------------------------------------

_ROW = r"""
const listed = %(listed)s, draft = %(draft)s;
global.apiGet = async (path) => {
  if (path === "/api/results") return listed;
  if (path.startsWith("/api/results?draft=")) return draft;
  throw new Error("no fixture for " + path);
};
(async () => {
  const host = el("div", "host"); views.results = host;
  await window.RONode.loadResults(host, false);
  toggle(byClass(host, "res-draft")[0]);
  await settle(); await settle();
  const rows = byClass(byClass(host, "res-draft-body")[0], "res-row").map((r) => ({
    sentence: text(byClass(r, "node-link")[0]),
    columns: r.kids.filter((k) => typeof k !== "string").map((k) => k.className),
    declared: byClass(r, "res-declared").map((d) => byClass(d, "node-sub-inline").map(text)),
    inMain: byClass(byClass(r, "res-main")[0], "res-declared").length,
    state: byClass(r, "status-label").map((s) => [text(s), s.className]) }));
  process.stdout.write(JSON.stringify(rows));
})().catch((e) => { console.error(e); process.exit(1); });
"""

_PAGE = r"""
const baseMk = global.mk;
global.mk = (tag, cls, text) => { const e = baseMk(tag, cls, text); Object.defineProperty(e, "childElementCount", { get: () => e.kids.filter((k) => typeof k !== "string").length }); return e; };
const page = %(page)s;
global.apiGet = async (path) => {
  if (path.startsWith("/api/node?")) return page;
  if (path.startsWith("/api/history?")) return { versions: [] };
  if (path.startsWith("/api/notes?")) return { notes: [] };
  if (path === "/api/me") return { me: { id: "li@example.com" } };
  throw new Error("no fixture for " + path);
};
(async () => {
  views.node = el("div", "host");
  window.RONode.openNode(page.id);
  await window.RONode.loadNode(views.node, false);
  const host = views.node;
  const cols = byClass(host, "trace-col");
  process.stdout.write(JSON.stringify({
    declared: byClass(host, "trace-declared").map((d) => byClass(d, "node-sub-inline").map(text)),
    inColumns: cols.map((c) => byClass(c, "trace-declared").length + c.all().filter((e) => /x\.csv|f1\.png/.test(text(e))).length),
    gap: byClass(cols[0], "trace-gap").length }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_row_and_the_page_show_the_declared_file_quietly_in_a_column_of_their_own_beside_the_warning(paper, lang):
    """On 图表与论断 the row of 0.42 has, between the claim and its state, a column of its own with one quiet line per file;
    its state is still 缺来源 in the warning's colour. On the claim's page the files are under the trace, never in a column
    of it, and the gap in 依据 stays. A claim in no passage has no such column. Both languages, through t()."""
    claims = _claims(paper)
    word = {"zh": "人工声明的依据文件：{}", "en": "Supporting file declared by a person: {}"}[lang]
    rows = node_run(_ROW % {"listed": json.dumps(_results(paper), ensure_ascii=False),
                            "draft": json.dumps(_results(paper, PATH), ensure_ascii=False)}, language=lang, view="results")
    by = {row["sentence"]: row for row in rows}
    first = by[SENTENCE]
    assert first["declared"] == [[word.format("data/x.csv"), word.format("figures/f1.png")]] and first["inMain"] == 0
    assert first["columns"] == ["res-main", "res-declared", first["columns"][-1]] and "status-label" in first["columns"][-1]
    assert first["state"][0][0] == {"zh": "缺来源", "en": "Missing source"}[lang]
    assert by["Unrelated text with 3.14 in it."]["declared"] == []
    assert by["Unrelated text with 3.14 in it."]["state"] == first["state"]
    out = node_run(_PAGE % {"page": json.dumps(_page(paper, claims["0.42"]["id"]), ensure_ascii=False)},
                   language=lang, view="node")
    assert out["declared"] == [[word.format("data/x.csv"), word.format("figures/f1.png")]]
    assert out["inColumns"] == [0, 0, 0][:len(out["inColumns"])] and out["gap"] == 1

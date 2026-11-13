"""DESIGN.md 22.2 (task V17, B1e): passages and their supporting files in the editor and on the pages around it.

The editor's side runs under node on the vendored CodeMirror (the same driver as test_v17_latex_editor.py): the
server's positions (code points, a CRLF as two) turned into the editor's and back; a found passage carried through
edits; at a save, changed words make it 待复核 with 「仍然成立」, an emptied one is found again by the server (cut and
pasted: found; deleted: 「段落已被删掉」), and a link 待复核 at opening is left alone; a save never posts to the ledger;
「仍然成立」, 「关联」 and 撤回关联 each post one act. Every body the editor sent is then applied, in order, to a real
project through `latex_edit`, so the positions are checked against the server's own reading and `evidence.toml` is
shown to change only by the clicks. The reload after a save that emptied a passage: text typed while it is out,
and a failed or stale answer (review of B1f). Also the file search of the link panel and the hooks on a file's page and 论文正文.
Every test names its scenario."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from researchos import latex_edit, project
from researchos.records import evidence
from test_v17_latex_editor import _DRIVER, NODE, WEBAPP

DRAFT = "😀😀\r\nB 显著 c\r\nend\r\n"
PATH = "paper/main.tex"

_SCENARIO = r"""
const out = {}, P = window.ROLatex._passages;
const seen = () => P().map((p) => ({state: p.state, opened: p.opened, from: p.from, to: p.to, files: p.links.length,
  candidates: p.candidates.map((c) => ({text: c.text, still: !!c.still}))}));
let rev = FILE.revision, saves = 0;
evidenceAnswer = () => Object.assign({}, EVIDENCE, {revision: rev});
L.open(FILE.path); await tick();
gets.shift().res(FILE); await tick(); await tick(); await tick();
const V = viewsMade[0], doc = () => V.state.doc.toString();
const save = async () => {
  button('保存').fire('click'); await tick();
  const p = posts.shift(); rev = 's' + (++saves); p.res({revision: rev, changed: true, previous: 'x'}); await tick(); await tick();
  return p;
};
const sent = [];
const waitingLink = (text) => all.filter((e) => e.classList.contains('latex-waiting') && e.textContent === text).pop();
out.initial = seen();
out.conv = (() => { const c = L._pure.positions(V.state.doc, '\r\n'); return [c.toEditor(6), c.toServer(7), c.toEditor(9), c.toServer(12)]; })();
// typing before the passage: it moves with the text, stays found, and the save posts the save only
V.dispatch({changes: {from: 0, insert: 'XX'}}); sent.push(await save());
out.afterTyping = seen();
// a negation typed at the passage's edge changes its words: 待复核 at the save, the new words offered with 仍然成立
const at = P().find((p) => p.opened === 'found').from;
V.dispatch({changes: {from: at, insert: '不'}});
out.beforeSave = seen();
sent.push(await save());
out.afterRewording = seen();
// 仍然成立: one reanchor, then the draft's links read again
const changed = P().find((p) => p.local);
EVIDENCE.links = EVIDENCE.links.map((l) => l.link === changed.links[0].link ? Object.assign({}, l, {state: 'found', last: 'pe-later',
  quote: '不显著', from: null, to: null, candidates: []}) : l);
let fromServer = null;
evidenceAnswer = () => ({revision: rev, links: EVIDENCE.links.map((l) => l.quote === '不显著' ? Object.assign({}, l, fromServer) : l)});
waitingLink('显著').fire('click'); await tick();
const still = button('仍然成立');
out.stillOffered = !!still && !button('是这段');
still.fire('click'); await tick();
const re = posts.shift(); fromServer = {from: re.body.from, to: re.body.to}; re.res({act: 'reanchor'}); sent.push(re); await tick(); await tick(); await tick();
out.afterStill = seen();
// every character of the passage deleted: the save asks the server again, and its 段落已被删掉 is shown
const p2 = P().find((p) => p.opened === 'found');
V.dispatch({changes: {from: p2.from, to: p2.to}});
out.beforeDeleteSave = seen();
evidenceAnswer = () => ({revision: rev, links: EVIDENCE.links.map((l) => l.quote === '不显著'
  ? Object.assign({}, l, {state: 'deleted', from: null, to: null, candidates: []}) : l)});
const asked = evidenceGets.length;
sent.push(await save()); await tick();
out.deleteAsked = evidenceGets.length - asked;
out.afterDelete = seen();
// 关联: a selection, the file search (figures first), the chosen file, 确认关联
const b = V.state.doc.toString().indexOf('B');
V.dispatch({selection: {anchor: b, head: b + 1}});
byClass('latex-link-button').fire('click'); await tick();
out.searched = gets[0].url; gets.shift().res({files: [{path: 'figures/f1.png', group: 'figure'}, {path: 'data/x.csv', group: 'data'}]}); await tick();
out.groups = all.filter((e) => e.classList.contains('latex-group')).map((e) => e.textContent);
out.primaryBefore = button('确认关联').disabled;
button('figures/f1.png').fire('click');
out.primaryAfter = button('确认关联').disabled;
button('确认关联').fire('click'); await tick();
const ln = posts.shift(); ln.res({act: 'link'}); sent.push(ln); await tick(); await tick();
// 撤回关联 on the deleted passage, after its question
waitingLink('不显著').fire('click'); await tick();
button('撤回关联').fire('click'); await tick();
const wd = posts.shift(); wd.res({act: 'withdraw'}); sent.push(wd); await tick(); await tick();
out.sent = sent.map((p) => ({url: p.url, body: p.body}));
out.leftover = posts.length;
console.log(JSON.stringify(out));
"""


def _put(root: Path, text: str) -> str:
    (root / PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / PATH).write_bytes(text.encode("utf-8"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha(root: Path) -> str:
    return hashlib.sha256((root / PATH).read_bytes()).hexdigest()


@pytest.fixture
def run(tmp_path) -> tuple[Path, dict, bytes]:
    """Per test: the project's home is recorded in each test's own settings folder (conftest)."""
    if NODE is None:
        pytest.skip("node is not installed")
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    (root / "figures").mkdir()
    (root / "figures/f1.png").write_bytes(b"png")
    # 「显著」 is linked; 「end」 is linked and then reworded elsewhere, so it is 待复核 when the editor opens
    first = _put(root, DRAFT.replace("end", "end words"))
    for words in ("显著", "end words"):
        text = (root / PATH).read_bytes().decode()
        latex_edit.evidence_act(root, "link", {"path": PATH, "revision": _sha(root), "from": text.index(words),
                                               "to": text.index(words) + len(words), "file": "figures/f1.png"})
    assert first
    _put(root, DRAFT.replace("end", "fin words"))
    opened = latex_edit.read_tex(root, PATH)
    answer = latex_edit.evidence_of_draft(root, PATH)
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    script = (prelude + "(async () => {\nconst FILE = " + json.dumps(opened) + ", EVIDENCE = " + json.dumps(answer) + ";\n"
              + _SCENARIO + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });")
    got = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True, timeout=60).stdout
    out = json.loads(got.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    return root, out, evidence.evidence_path(root).read_bytes()


def test_the_servers_positions_become_the_editors_with_a_crlf_as_two_and_an_emoji_as_one(run):
    _root, out, _ledger = run
    # 「😀😀\r\n」 is 4 code points on the server and 5 UTF-16 units in the editor
    assert out["conv"] == [7, 6, 10, 12]
    found, waiting = out["initial"]
    assert found == {"state": "found", "opened": "found", "from": 7, "to": 9, "files": 1, "candidates": []}
    assert waiting["state"] == "changed" and waiting["opened"] == "changed" and waiting["from"] is None
    assert waiting["candidates"] and waiting["candidates"][0]["still"] is False


def test_a_found_passage_moves_with_text_typed_before_it_and_a_save_changes_nothing_else(run):
    _root, out, _ledger = run
    assert out["afterTyping"][0] == {"state": "found", "opened": "found", "from": 9, "to": 11, "files": 1, "candidates": []}


def test_a_rewording_waits_for_the_save_then_offers_still_holds_and_a_link_waiting_at_opening_is_left_alone(run):
    _root, out, _ledger = run
    assert out["beforeSave"][0]["state"] == "found" and out["beforeSave"][0]["to"] == 12   # the 「不」 at its edge is inside
    reworded, waiting = out["afterRewording"]
    assert reworded["state"] == "changed" and reworded["candidates"] == [{"text": "不显著", "still": True}]
    assert (waiting["state"], waiting["opened"], waiting["from"]) == ("changed", "changed", None)
    assert out["stillOffered"] is True
    assert out["afterStill"][0]["state"] == "found" and (out["afterStill"][0]["from"], out["afterStill"][0]["to"]) == (9, 12)


def test_a_passage_whose_every_character_was_deleted_says_so_at_the_save(run):
    _root, out, _ledger = run
    assert [p["state"] for p in out["beforeDeleteSave"]] == ["found", "changed"]   # not declared deleted by the editor
    assert out["deleteAsked"] == 1
    assert [p["state"] for p in out["afterDelete"]] == ["deleted", "changed"]


def test_the_link_panel_lists_figures_first_and_its_one_primary_waits_for_a_file(run):
    _root, out, _ledger = run
    assert out["searched"] == "/api/latex/files?q=" and out["groups"] == ["图和表", "数据"]
    assert out["primaryBefore"] is True and out["primaryAfter"] is False


def test_saves_never_post_to_the_ledger_and_each_click_posts_exactly_one_act(run):
    _root, out, _ledger = run
    assert [s["url"] for s in out["sent"]] == ["/api/latex/save", "/api/latex/save", "/api/latex/evidence/reanchor",
                                                 "/api/latex/save", "/api/latex/evidence/link",
                                                 "/api/latex/evidence/withdraw"]
    assert out["leftover"] == 0


def test_every_body_the_editor_sent_applies_to_the_real_file_and_only_the_clicks_write_evidence(run):
    """The editor's saves, spliced by the server, leave the ledger byte for byte; its 仍然成立 anchors the passage where
    the server then finds it; its 关联 links the selected word; its 撤回关联 withdraws the deleted passage."""
    root, out, ledger = run
    before = dict(evidence.links_now(root)[0])
    for item in out["sent"]:
        body = dict(item["body"], revision=_sha(root))
        action = item["url"].rsplit("/", 1)[1]
        if action == "save":
            latex_edit.save_tex(root, body)
            assert evidence.evidence_path(root).read_bytes() == ledger
        else:
            latex_edit.evidence_act(root, action, body)
            ledger = evidence.evidence_path(root).read_bytes()
        if action == "reanchor":
            (shown,) = [l for l in latex_edit.evidence_of_draft(root, PATH)["links"] if l["link"] == body["link"]]
            text = (root / PATH).read_bytes().decode()
            assert shown["state"] == "found" and text[shown["from"]:shown["to"]] == "不显著"
    assert (root / PATH).read_bytes().decode() == "XX😀😀\r\nB  c\r\nfin words\r\n"
    after = evidence.links_now(root)[0]
    new = [item for key, item in after.items() if key not in before]
    assert [item["anchor"]["quote"] for item in new] == ["B"]
    assert len(after) == len(before)    # one linked, one withdrawn


def test_the_link_panels_search_lists_figures_and_tables_first_then_data_scripts_documents(tmp_path):
    root = tmp_path / "p"
    for rel in ("notes.md", "code/a.py", "data/x.csv", "tables/t2.tex", "figures/f1.png", ".hidden/z.png", "paper/main.tex"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    project.init_project(root)
    got = latex_edit.supporting_files(root, "")
    assert [(f["path"], f["group"]) for f in got["files"]] == [
        ("figures/f1.png", "figure"), ("tables/t2.tex", "figure"), ("data/x.csv", "data"), ("code/a.py", "script"),
        ("notes.md", "document"), ("paper/main.tex", "document")]
    assert [f["path"] for f in latex_edit.supporting_files(root, "TAB")["files"]] == ["tables/t2.tex"]
    assert latex_edit.supporting_files(root, "", limit=2)["more"] is True


def test_a_files_page_and_the_papers_rows_show_the_passages_it_supports_and_the_counts():
    node = (WEBAPP / "node.js").read_text(encoding="utf-8")
    assert 'ROLatex.renderSupported(pageEl, page)' in node and 'ROLatex.paperCounts(main, d.file, countsSaid)' in node
    latex = (WEBAPP / "latex.js").read_text(encoding="utf-8")
    assert 'apiGet("/api/latex/supports?file=" + encodeURIComponent(page.id))' in latex
    assert 'mkButton(t("在编辑器里打开"), "ws-text-link", () => open(p.draft, { link: p.link }))' in latex
    # 22.7: 仍然成立 and 是这段 quiet buttons, 撤回关联 a text link, 待复核 in ochre, never clay
    assert 'mkButton(c.still ? t("仍然成立") : t("是这段"), "btn btn-small"' in latex
    assert 'mkButton(t("撤回关联"), "ws-text-link"' in latex
    assert 'mk("span", "status-label tone-ochre", t("{n} 段待复核", { n: waiting.size }))' in latex
    assert "clay" not in latex


def test_a_generated_table_with_odd_line_endings_is_read_only_for_its_endings_and_cannot_be_linked(tmp_path):
    """Review of B1e: a generated file whose text is not one ending per break says so, not 「generated」, so the editor
    hides 「关联依据文件」 and the server refuses a link it could not place (CodeMirror would count each break as one)."""
    root = tmp_path / "p"
    root.mkdir()
    project.init_project(root)
    text = "a\nb\nc\r\nTARGET word\r\n"
    (root / "tables").mkdir()
    (root / "tables/t.tex").write_bytes(text.encode())
    (root / "figures").mkdir()
    (root / "figures/f1.png").write_bytes(b"png")
    (root / "research/results").mkdir(parents=True)
    (root / "research/results/abc.outputs.json").write_text(json.dumps(
        {"version": 1, "items": [{"kind": "table", "path": "tables/t.tex"}]}))
    assert latex_edit.read_tex(root, "tables/t.tex")["read_only_reason"] == "mixed"
    with pytest.raises(latex_edit.WorkspaceError) as error:
        latex_edit.evidence_act(root, "link", {"path": "tables/t.tex", "revision": _sha_of(root / "tables/t.tex"),
                                               "from": 7, "to": 13, "file": "figures/f1.png"})
    assert error.value.code == "read_only_file"
    assert not evidence.evidence_path(root).exists()
    (root / "tables/t.tex").write_bytes(text.replace("\r\n", "\n").encode())   # one ending: generated, and linkable
    assert latex_edit.read_tex(root, "tables/t.tex")["read_only_reason"] == "generated"
    latex_edit.evidence_act(root, "link", {"path": "tables/t.tex", "revision": _sha_of(root / "tables/t.tex"),
                                           "from": 6, "to": 12, "file": "figures/f1.png"})
    (only,) = evidence.links_now(root)[0].values()
    assert only["anchor"]["quote"] == "TARGET"


def test_the_same_passage_linked_to_the_same_file_again_is_refused_and_another_file_is_not(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    project.init_project(root)
    for rel in ("figures/f1.png", "data/x.csv"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"x")
    revision = _put(root, "A 显著 b\n")
    body = {"path": PATH, "revision": revision, "from": 2, "to": 4, "file": "figures/f1.png"}
    latex_edit.evidence_act(root, "link", body)
    with pytest.raises(latex_edit.WorkspaceError) as error:
        latex_edit.evidence_act(root, "link", dict(body, file="file:figures/f1.png"))
    assert (error.value.code, error.value.status) == ("already_linked", 409)
    latex_edit.evidence_act(root, "link", dict(body, file="data/x.csv"))
    assert sorted(item["file"] for item in evidence.links_now(root)[0].values()) == ["dataset:data/x.csv",
                                                                                     "figure:figures/f1.png"]


def test_a_script_named_like_a_table_is_a_script_and_a_table_folder_holds_tables(tmp_path):
    root = tmp_path / "p"
    for rel in ("tab.py", "tables/t.csv", "tables/t.tex"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    project.init_project(root)
    got = {f["path"]: f["group"] for f in latex_edit.supporting_files(root, "")["files"]}
    assert got == {"tab.py": "script", "tables/t.csv": "figure", "tables/t.tex": "figure"}


def _sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# -- the review of B1e, driven under node -----------------------------------------------------------------------------

_REVIEW = r"""
FakeView.prototype.dispatch = function (spec) {   // as the driver's, telling the listeners when the selection moved
  const tr = this.state.update(spec); this.state = tr.state;
  for (const listen of tr.state.facet(real.EditorView.updateListener)) listen({docChanged: tr.docChanged, selectionSet: !!spec.selection, changes: tr.changes, view: this, state: tr.state});
};
const out = {};
const TEXT = 'A 显著 b\nmore words here\n';
const f = (path, extra) => Object.assign({path, text: TEXT, revision: 'r1', line_ending: 'lf', read_only: false, read_only_reason: null, links: [], mains: []}, extra);
const link = (id, extra) => Object.assign({link: id, last: 'pe-' + id, quote: '显著', file: 'figure:figures/f1.png', note: null, label: 'Li',
  at: null, state: 'found', from: 2, to: 4, candidates: [], file_missing: false}, extra);
let EV = {revision: 'r1', links: []};
evidenceAnswer = () => EV;
const view = () => viewsMade[viewsMade.length - 1];
const linkHidden = () => byClass('latex-link-button').classList.contains('hidden');
const side = () => byClass('latex-side');
const texts = (el) => typeof el === 'string' ? [el] : [el.textContent].concat(...el.children.filter(Boolean).map(texts));
const status = () => byClass('latex-tools').children[2].textContent;
const open = async (path, extra, at) => { L.open(path, at); await tick(); gets.shift().res(f(path, extra)); await tick(); await tick(); await tick(); };
// a read-only draft for its line endings offers no link; a generated one does
await open('odd.tex', {read_only: true, read_only_reason: 'mixed'});
view().dispatch({selection: {anchor: 2, head: 4}}); out.oddHidden = linkHidden();
await open('gen.tex', {read_only: true, read_only_reason: 'generated'});
view().dispatch({selection: {anchor: 2, head: 4}}); out.generatedShown = !linkHidden();
await open('ro.tex', {read_only: true, read_only_reason: 'encoding'});
view().dispatch({selection: {anchor: 2, head: 4}}); out.encodingHidden = linkHidden();
// a draft opened at a passage: its panel; the margin count and the underlines as drawn
EV = {revision: 'r1', links: [link('pl-1'), link('pl-2', {quote: 'gone words', state: 'changed', from: null, to: null,
  candidates: [{from: 7, to: 22, text: 'more words here'}]})]};
await open('a.tex', {}, {link: 'pl-1'});
const V = view();
const drawn = [];
for (const d of V.state.facet(real.EditorView.decorations)) {
  if (typeof d === 'function') continue;   // the include links' plugin, which needs a real view
  d.between(0, V.state.doc.length, (from, to, value) => {
    const w = value.spec.widget;
    drawn.push(w ? {at: from, count: w.toDOM().textContent, cls: w.toDOM().className} : {from, to, cls: value.spec.class});
  });
}
out.drawn = drawn;
out.panelAtOpen = texts(side()).includes('依据文件');
// typing before the passage, then the pages' refresh: the passage's panel stays open
V.dispatch({changes: {from: 0, insert: 'X'}});
await L.refresh(); await tick();
out.panelAfterRefresh = texts(side()).includes('依据文件') && texts(side()).includes('显著');
// with unsaved text: 是这段 is refused without a save; 撤回关联 posts the withdraw only, once however often pressed
const wait = all.filter((e) => e.classList.contains('latex-waiting')).pop();
wait.fire('click'); await tick();
button('是这段').fire('click'); await tick();
out.thisOneWhileDirty = {posts: posts.length, status: status()};
const withdraw = button('撤回关联');
withdraw.fire('click'); await tick(); withdraw.fire('click'); await tick();
out.withdrawPosts = posts.map((p) => p.url);
out.withdrawDisabled = withdraw.disabled;
EV = {revision: 'r1', links: [link('pl-1')]};
posts.shift().res({act: 'withdraw', link: 'pl-2', entry: 'pe-9'}); await tick(); await tick(); await tick();
out.undoShown = !button('撤销').classList.contains('hidden');
button('撤销').fire('click'); await tick();
const un = posts.shift(); out.undo = {url: un.url, link: un.body.link, undoes: un.body.undoes};
un.res({act: 'undone'}); await tick(); await tick();
out.undoAfter = {hidden: button('撤销').classList.contains('hidden'), status: status()};
out.dirtyStill = L.isDirty();
// saved; then two files chosen, the second refused: only it stays chosen, and pressing again sends it alone
button('保存').fire('click'); await tick();
posts.shift().res({revision: 'r2', changed: true, previous: 'r1'}); await tick(); await tick();
EV = {revision: 'r2', links: [link('pl-1', {from: 3, to: 5})]};
const more = V.state.doc.toString().indexOf('more');
V.dispatch({selection: {anchor: more, head: more + 4}});
byClass('latex-link-button').fire('click'); await tick();
gets.shift().res({files: [{path: 'figures/f1.png', group: 'figure'}, {path: 'data/x.csv', group: 'data'}]}); await tick();
button('figures/f1.png').fire('click'); button('data/x.csv').fire('click');
button('确认关联').fire('click'); await tick();
posts.shift().res({act: 'link', link: 'pl-a', entry: 'pe-a'}); await tick(); await tick();
posts.shift().rej({state: 'workspace_conflict'}); await tick(); await tick(); await tick();
out.chosenAfterFailure = byClass('latex-files').children.map((li) => li.children[0].textContent);
out.primaryAfterFailure = button('确认关联').disabled;
button('确认关联').fire('click'); await tick();
out.retried = posts.map((p) => [p.body.file, p.body.from, p.body.to]);
posts.shift().res({act: 'link', link: 'pl-b', entry: 'pe-b'}); await tick(); await tick(); await tick();
out.leftover = posts.length;
// 图表与论断's rows: the counts, and the ledger's own message said once for all rows
const row1 = mk('div'), row2 = mk('div'), row3 = mk('div'), once = {};
EV = {revision: 'r2', links: [link('pl-1'), link('pl-3'), link('pl-2', {state: 'changed', from: null, to: null})]};
L.paperCounts(row1, 'a.tex', {}); await tick();
EV = {revision: 'r2', links: [], message: 'The record cannot be read now'};
L.paperCounts(row2, 'a.tex', once); L.paperCounts(row3, 'b.tex', once); await tick(); await tick();
out.counts = texts(row1.children[0]).join('|');
out.messages = [row2, row3].map((r) => texts(r).filter((x) => x === 'The record cannot be read now').length);
// 以它为依据的段落 on a file's page
const page = mk('div');
L.renderSupported(page, {id: 'figure:figures/f1.png'}); await tick();
out.supportsUrl = gets[0].url;
gets.shift().res({passages: [{draft: 'a.tex', section: '引言', excerpt: '显著', state: 'changed', link: 'pl-1'}]}); await tick();
out.supports = texts(page.children[0]);
out.supportsHidden = page.children[0].classList.contains('hidden');
// a waiting passage's label cut at 40 characters never halves an emoji
EV = {revision: 'r2', links: [link('pl-4', {quote: '😀'.repeat(45), state: 'changed', from: null, to: null})]};
await L.refresh(); await tick();
out.label = all.filter((e) => e.classList.contains('latex-waiting')).pop().textContent;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def review() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    script = (prelude + "(async () => {\n" + _REVIEW
              + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });")
    got = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True, timeout=60).stdout
    out = json.loads(got.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    return out


def test_the_link_button_is_offered_on_a_generated_draft_and_never_on_one_read_only_for_its_text(review):
    assert review["oddHidden"] is True and review["encodingHidden"] is True and review["generatedShown"] is True


def test_a_found_passage_is_dotted_in_ink_with_its_count_in_the_margin_and_a_waiting_one_has_no_place(review):
    assert review["drawn"] == [{"at": 2, "count": "1", "cls": "latex-count"},
                               {"from": 2, "to": 4, "cls": "latex-passage"}]
    assert review["panelAtOpen"] is True


def test_an_open_passage_panel_stays_open_through_typing_and_a_refresh(review):
    assert review["panelAfterRefresh"] is True


def test_with_unsaved_text_a_reanchor_is_refused_and_a_withdraw_posts_alone_and_once(review):
    assert review["thisOneWhileDirty"] == {"posts": 0, "status": "先保存，再改依据文件"}
    assert review["withdrawPosts"] == ["/api/latex/evidence/withdraw"] and review["withdrawDisabled"] is True
    assert review["dirtyStill"] is True


def test_undo_is_offered_after_an_entry_and_takes_back_that_entry(review):
    assert review["undoShown"] is True
    assert review["undo"] == {"url": "/api/latex/evidence/undo", "link": "pl-2", "undoes": "pe-9"}
    assert review["undoAfter"] == {"hidden": True, "status": "已撤销"}


def test_a_link_to_several_files_that_fails_part_way_sends_only_the_rest_again(review):
    assert review["chosenAfterFailure"] == ["data/x.csv"] and review["primaryAfterFailure"] is False
    assert [r[0] for r in review["retried"]] == ["data/x.csv"] and review["leftover"] == 0


def test_the_papers_rows_count_passages_and_say_the_ledgers_message_once(review):
    # two links on one place are one passage; the waiting one is counted apart, in ochre
    assert review["counts"] == "|2 段有依据文件| |1 段待复核"
    assert sorted(review["messages"]) == [0, 1]


def test_a_files_page_lists_the_passages_it_supports_with_open_in_the_editor(review):
    assert review["supportsUrl"] == "/api/latex/supports?file=figure%3Afigures%2Ff1.png"
    assert "以它为依据的段落" in review["supports"] and "在编辑器里打开" in review["supports"] and "引言" in review["supports"]
    assert review["supportsHidden"] is False


def test_a_waiting_passages_label_is_cut_at_forty_characters_not_forty_code_units(review):
    assert review["label"] == "😀" * 40


# -- B1f: a linked paragraph cut and pasted elsewhere ------------------------------------------------------------------

_MOVED = r"""
const out = {}, P = window.ROLatex._passages;
const seen = () => P().map((p) => ({state: p.state, opened: p.opened, from: p.from, to: p.to}));
evidenceAnswer = () => BEFORE;
L.open(FILE.path); await tick();
gets.shift().res(FILE); await tick(); await tick(); await tick();
const V = viewsMade[0], doc = () => V.state.doc.toString();
out.opened = seen();
// cut the paragraph with its blank line, paste it after the last one
const at = doc().indexOf(PARA);
V.dispatch({changes: {from: at, to: at + PARA.length + 2}});
V.dispatch({changes: {from: doc().length, insert: '\n' + PARA + '\n'}});
out.text = doc();
out.beforeSave = seen();
evidenceAnswer = () => AFTER;
const asked = evidenceGets.length;
button('保存').fire('click'); await tick();
const p = posts.shift(); p.res({revision: AFTER.revision, changed: true, previous: FILE.revision});
await tick(); await tick(); await tick();
out.asked = evidenceGets.length - asked;
out.afterSave = seen();
out.shown = P().map((p) => doc().slice(p.from, p.to));
out.side = all.filter((e) => e.textContent === '段落已被删掉' && !e.removed).length;
out.sent = [{url: p.url, body: p.body}];
out.leftover = posts.length;
console.log(JSON.stringify(out));
"""


def test_a_linked_paragraph_cut_and_pasted_elsewhere_is_found_there_after_the_save_not_called_deleted(tmp_path):
    """The editor's range of the cut paragraph is empty, yet it is not 「段落已被删掉」: after the save the server finds
    it again in the saved text, at its new place, and that answer is what the editor shows. The save writes no entry."""
    if NODE is None:
        pytest.skip("node is not installed")
    para = "Para one is 显著 here."
    before_text, after_text = f"Intro.\n\n{para}\n\nPara two.\n", f"Intro.\n\nPara two.\n\n{para}\n"
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    (root / "figures").mkdir()
    (root / "figures/f1.png").write_bytes(b"png")
    _put(root, before_text)
    latex_edit.evidence_act(root, "link", {"path": PATH, "revision": _sha(root), "from": before_text.index(para),
                                           "to": before_text.index(para) + len(para), "file": "figures/f1.png"})
    ledger = evidence.evidence_path(root).read_bytes()
    opened, before = latex_edit.read_tex(root, PATH), latex_edit.evidence_of_draft(root, PATH)
    # what the server will answer once the editor's save is on disk, read from a copy holding the saved text
    twin = tmp_path / "twin"
    shutil.copytree(root, twin)
    _put(twin, after_text)
    after = latex_edit.evidence_of_draft(twin, PATH)
    assert [l["state"] for l in after["links"]] == ["found"]
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    script = (prelude + "(async () => {\nconst FILE = " + json.dumps(opened) + ", BEFORE = " + json.dumps(before)
              + ", AFTER = " + json.dumps(after) + ", PARA = " + json.dumps(para) + ";\n" + _MOVED
              + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });")
    got = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True, timeout=60).stdout
    out = json.loads(got.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    assert out["opened"][0]["state"] == "found" and out["text"] == after_text
    assert out["beforeSave"][0]["from"] == out["beforeSave"][0]["to"]           # the editor's own range is empty
    assert out["asked"] == 1                                                      # the save had the server look again
    assert out["afterSave"] == [{"state": "found", "opened": "found", "from": after_text.index(para),
                                 "to": after_text.index(para) + len(para)}]
    assert out["shown"] == [para] and out["side"] == 0
    assert [s["url"] for s in out["sent"]] == ["/api/latex/save"] and out["leftover"] == 0
    # the save the editor sent, applied to the real project: the text is the pasted one, the ledger byte for byte the
    # same, and the server's own reading is the answer the editor was given
    latex_edit.save_tex(root, dict(out["sent"][0]["body"], revision=_sha(root)))
    assert (root / PATH).read_bytes().decode() == after_text
    assert evidence.evidence_path(root).read_bytes() == ledger
    assert latex_edit.evidence_of_draft(root, PATH)["links"] == after["links"]


# -- review of B1f: the reload after a save that emptied a passage ---------------------------------------------------

_RELOAD = r"""
const out = {}, P = window.ROLatex._passages;
const seen = () => P().map((p) => ({state: p.state, local: p.local, from: p.from, to: p.to,
  text: p.from == null ? null : V.state.doc.sliceString(p.from, p.to), candidates: p.candidates.map((c) => ({text: c.text, still: !!c.still}))}));
const status = () => byClass('latex-tools').children[2].textContent;
// the side as rendered now: its counts and the 待复核 entries listed under them
const waitingNow = () => { const c = all.filter((e) => e.classList.contains('latex-counts')).pop();
  return [c.textContent].concat(c.children.map((k) => k.textContent || k).filter((x) => x.trim())); };
const sent = [];
let rev = FILE.revision, n = 0;
evidenceAnswer = () => EV;
L.open(FILE.path); await tick();
gets.shift().res(FILE); await tick(); await tick(); await tick();
const V = viewsMade[0], doc = () => V.state.doc.toString();
const save = async () => {
  button('保存').fire('click'); await tick();
  const p = posts.shift(); rev = 'r' + (++n); p.res({revision: rev, changed: true, previous: 'x'}); sent.push(p);
  await tick(); await tick(); await tick();
};
const answer = (text) => Object.assign({}, ANSWERS[text], {revision: rev});
// A reworded and saved: 待复核 in this editor, its new words offered with 仍然成立
const pa = P().find((p) => doc().slice(p.from, p.to) === A);
V.dispatch({changes: {from: pa.from, insert: 'Not '}});
await save();
out.afterReword = seen();
// B cut and pasted lower; the save's reload is held while text is typed at the top
const at = doc().indexOf(B);
V.dispatch({changes: {from: at, to: at + B.length + 2}});
V.dispatch({changes: {from: doc().length, insert: '\n' + B + '\n'}});
const pasted = doc();
let release; evidenceAnswer = () => new Promise((r) => { release = r; });
await save();
V.dispatch({changes: {from: 0, insert: 'TYPED '}});
out.whileOut = {saveDisabled: button('保存').disabled, posts: posts.length};
all.filter((e) => e.classList.contains('latex-waiting') && e.textContent === A).pop().fire('click'); await tick();
button('仍然成立').fire('click'); await tick();
out.whileOut.clicked = {posts: posts.length, status: status()};
release(answer(pasted)); await tick(); await tick(); await tick(); await tick();
out.afterReload = seen();
out.afterReloadStatus = status();
out.saveEnabled = !button('保存').disabled;
// the typing saved (nothing emptied: no reload), then 仍然成立 on A writes A's new words
evidenceAnswer = () => answer(doc());
const asked = evidenceGets.length;
await save();
out.thirdSaveAsked = evidenceGets.length - asked;
out.afterThird = seen();
all.filter((e) => e.classList.contains('latex-waiting') && e.textContent === A).pop().fire('click'); await tick();
button('仍然成立').fire('click'); await tick();
const re = posts.shift(); re.res({act: 'reanchor'}); sent.push(re); await tick(); await tick(); await tick();
button('关闭').fire('click');
// C deleted, and the reload fails: 段落已被删掉 here, not found
const pc = P().find((p) => doc().slice(p.from, p.to) === C);
V.dispatch({changes: {from: pc.from, to: pc.to}});
evidenceAnswer = () => Promise.reject({state: 'none'});
await save();
out.afterFailed = seen().filter((p) => p.text === '');
out.failedSide = waitingNow();
// D deleted, and the file changed elsewhere again: the answer is for another revision, twice; still 段落已被删掉
const pd = P().find((p) => doc().slice(p.from, p.to) === D);
V.dispatch({changes: {from: pd.from, to: pd.to}});
evidenceAnswer = () => ({revision: 'elsewhere', links: []});
const asked2 = evidenceGets.length;
await save();
out.mismatchAsked = evidenceGets.length - asked2;
out.afterMismatch = seen().filter((p) => p.text === '');
out.mismatchSide = waitingNow();
out.sent = sent.map((p) => ({url: p.url, body: p.body}));
out.leftover = posts.length;
console.log(JSON.stringify(out));
"""


def test_text_typed_while_the_saves_reload_is_out_moves_a_waiting_passage_and_a_failed_reload_never_reads_found(tmp_path):
    """Review of B1f. A passage this editor put under 待复核 keeps its place when text is typed while the reload after
    a save is out (its range and its 「仍然成立」 candidate are A's new words, not the text the typing pushed into the
    old range), and 仍然成立 is refused until that reload is in. A deleted passage whose reload fails, or whose answer is
    for another revision, reads 「段落已被删掉」 and is counted 待复核, not found. Every body is then applied to a real
    project: the one reanchor names A's new words, and the saves write nothing to the ledger."""
    if NODE is None:
        pytest.skip("node is not installed")
    a, b, c, d = "Alpha is 显著 here.", "Beta para text.", "Gamma line.", "Delta line."
    text = f"Intro.\n\n{a}\n\n{b}\n\n{c}\n\n{d}\n\nEnd.\n"
    root = tmp_path / "project"
    root.mkdir()
    project.init_project(root)
    (root / "figures").mkdir()
    (root / "figures/f1.png").write_bytes(b"png")
    _put(root, text)
    for q in (a, b, c, d):
        latex_edit.evidence_act(root, "link", {"path": PATH, "revision": _sha(root), "from": text.index(q),
                                               "to": text.index(q) + len(q), "file": "figures/f1.png"})
    opened, ev = latex_edit.read_tex(root, PATH), latex_edit.evidence_of_draft(root, PATH)
    # the server's answers, read from copies holding the texts the editor will save
    pasted = f"Intro.\n\nNot {a}\n\n{c}\n\n{d}\n\nEnd.\n\n{b}\n"
    answers = {}
    for i, saved in enumerate((pasted, "TYPED " + pasted)):
        twin = tmp_path / f"twin{i}"
        shutil.copytree(root, twin)
        _put(twin, saved)
        answers[saved] = latex_edit.evidence_of_draft(twin, PATH)
    prelude = _DRIVER.split("const out = {};")[0] % {"cm": json.dumps(str(WEBAPP / "vendor" / "codemirror.js")),
                                                     "latex": json.dumps(str(WEBAPP / "latex.js"))}
    script = (prelude + "(async () => {\nconst FILE = " + json.dumps(opened) + ", EV = " + json.dumps(ev)
              + ", ANSWERS = " + json.dumps(answers) + ", A = " + json.dumps(a) + ", B = " + json.dumps(b)
              + ", C = " + json.dumps(c) + ", D = " + json.dumps(d) + ";\n" + _RELOAD
              + "\n})().catch((err) => { console.log(JSON.stringify({error: String(err && err.stack || err)})); });")
    got = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True, timeout=60).stdout
    out = json.loads(got.strip().splitlines()[-1])
    assert "error" not in out, out.get("error")
    new_a = "Not " + a
    assert [(x["state"], x["local"], x["text"]) for x in out["afterReword"]][0] == ("changed", True, new_a)
    # while the reload is out: no save, and 仍然成立 refused without a post
    assert out["whileOut"] == {"saveDisabled": True, "posts": 0, "clicked": {"posts": 0, "status": "先保存，再改依据文件"}}
    # after it: A where its words are now (the typing moved it), its candidate those words; B found where it was pasted
    for seen in (out["afterReload"], out["afterThird"]):
        assert seen[0]["text"] == new_a and seen[0]["candidates"] == [{"text": new_a, "still": True}]
        assert [(x["state"], x["text"]) for x in seen[1:]] == [("found", b), ("found", c), ("found", d)]
    assert out["afterReloadStatus"] == "尚未保存" and out["saveEnabled"] is True
    assert out["thirdSaveAsked"] == 0   # nothing emptied, no reload
    # a failed reload, and one answered for another revision (asked again once): 段落已被删掉, counted 待复核
    assert [(x["state"], x["local"]) for x in out["afterFailed"]] == [("deleted", False)]
    assert out["failedSide"] == ["4 段有依据文件", "2 段待复核"]   # A still 待复核 here, C deleted
    assert out["mismatchAsked"] == 2
    assert [x["state"] for x in out["afterMismatch"]] == ["deleted", "deleted"]
    assert out["mismatchSide"] == ["4 段有依据文件", "3 段待复核"]
    assert out["leftover"] == 0
    # every body, in order, on the real project: the saves leave the ledger as it was, the reanchor names A's new words
    ledger = evidence.evidence_path(root).read_bytes()
    for item in out["sent"]:
        if item["url"] == "/api/latex/save":
            before = evidence.evidence_path(root).read_bytes()
            latex_edit.save_tex(root, dict(item["body"], revision=_sha(root)))
            assert evidence.evidence_path(root).read_bytes() == before
        else:
            body = dict(item["body"], revision=_sha(root))
            body.pop("project")
            on_disk = (root / PATH).read_bytes().decode()
            assert on_disk[body["from"]:body["to"]] == new_a
            latex_edit.evidence_act(root, "reanchor", body)
    assert evidence.evidence_path(root).read_bytes() != ledger
    assert (root / PATH).read_bytes().decode() == f"TYPED Intro.\n\nNot {a}\n\n\n\n\n\nEnd.\n\n{b}\n"
    states = {x["quote"]: x["state"] for x in latex_edit.evidence_of_draft(root, PATH)["links"]}
    assert states[new_a] == "found" and states[b] == "found"

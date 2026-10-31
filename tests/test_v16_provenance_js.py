"""Research OS V16, task B3-1 (DESIGN.md 21.5): the pages. 数据与代码, 图表与论断 and a node's 来龙去脉 under node, fed
the engine's own payloads for the thesis in miniature of test_v16_provenance, in both languages: the rows, the
actions, the folds and the filters. Every test names its scenario."""

from __future__ import annotations

import json

import pytest

from test_v10_desk_panel import _PANEL_DOM
from test_v16_provenance import (
    CHECK, FALLBACK, FIG, HAND, LOCK, PKG, RAW, WORD1, WORD3, _answer, _choose, _cite_table, _data_code, _page, _results,
    thesis,
)
from test_v9_language import CJK, WEBAPP, _i18n_source, _node, needs_node

NODE_JS = (WEBAPP / "node.js").read_text(encoding="utf-8")

#: The page's globals (app.html's) as the tests of the other pages stand them in, and a few helpers over the DOM.
_PRELUDE = r"""
global.window = { scrollTo() {}, scrollY: 0 };
%(i18n)s
%(dom)s
global.RODesk = { ensureContext: async () => ({}), context: () => ({ mode: "single" }), refreshSidebar() {},
  statusLabel: (s) => el("span", "status-label", s && s.label || ""), verdictInline: () => el("span", "v"),
  _whenWords: (at) => String(at).slice(0, 10) };  // the day, as the desk words it
global.clearProjectState = () => {};
global.renderBlockingError = (node, framing) => { node.textContent = framing; };
global.openFilePanel = () => {};
global.activateView = () => {};
global.SECTION_OF_VIEW = { results: "results", lineage: "data", node: "results" };
global.SECTION_TITLES = { results: "图表与论断" };
global.currentView = %(view)s;
global.lastGeneration = 5;
global.settle = () => new Promise((r) => setTimeout(r, 0));
global.text = (e) => e.textContent;
global.byClass = (host, c) => host.all().filter((k) => k.cls.has(c));
global.buttons = (host, label) => host.all().filter((k) => k.tag === "BUTTON" && k.textContent === label);
// a row is found by its path, which stands as the quiet second line under its title (21.7)
global.rowOf = (host, cls, path) => host.all().find((e) => e.cls.has(cls) && e.all().some((k) => k.cls.has("res-path") && !k.cls.has("dc-broken-path") && k.textContent === path));
global.toggle = (details) => { details.open = !details.open; (details.on.toggle || []).forEach((f) => f()); };
%(node)s
global.RONode = window.RONode;
global.calls = [];
"""


def _run(body: str, *, language: str | None, view: str = "lineage") -> object:
    script = _PRELUDE % {
        "i18n": (f"window.ROI18N_SAVED = {json.dumps(language)};\n" + _i18n_source()) if language else "",
        "dom": _PANEL_DOM, "view": json.dumps(view), "node": NODE_JS,
    }
    return _node(script + body)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


# -- 数据与代码 ---------------------------------------------------------------------------------------


_DATA_CODE_BODY = r"""
const before = %(before)s, after = %(after)s;
global.apiGet = async (path) => {
  calls.push("GET " + path);
  if (path === "/api/stata") return { has_do: false };
  if (path === "/api/data-code") return posted.length ? after : before;
  throw new Error("no fixture for " + path);
};
global.posted = [];
global.apiPost = async (path, body) => { calls.push("POST " + path); posted.push([path, body]); return { ok: true, generation: 6, ingest_error: null }; };
(async () => {
  const host = el("div", "host"); views.lineage = host;
  await window.RONode.loadDataCode(host, false);
  const out = {};
  out.filters = byClass(host, "dc-filter").map((b) => [text(b), b.cls.has("problem")]);
  const row = (path) => rowOf(host, "dc-row", path);
  const lines = (r) => byClass(r, "dc-rel").map(text);
  // a download nothing writes
  const raw = row(%(raw)s);
  out.raw = { lines: lines(raw), tags: byClass(raw, "status-label").map((e) => [text(e), e.className]), action: buttons(raw, %(assign)s).length,
    clay: raw.all().some((e) => e.cls.has("tone-clay")) };
  // the script that names a file that is not there
  const check = row(%(check)s);
  out.check = { bad: byClass(check, "bad").map(text), maybe: byClass(check, "dc-maybe").map(text), yes: buttons(check, %(yes)s).length,
    folds: byClass(check, "dc-fold").map((d) => [text(d.kids[0]), d.kids[1].all().filter((k) => k.cls.has("dc-candidate")).map(text), d.kids[1].all().filter((k) => k.tag === "BUTTON").length]) };
  out.checkBadClay = byClass(check, "bad").map((e) => e.className);
  // the file only that text names
  const phantom = row("environment/R_packages.lock.csv");
  out.phantom = { tags: byClass(phantom, "status-label").map((e) => [text(e), e.className]), bad: byClass(phantom, "bad").length,
    fold: byClass(phantom, "dc-fold").map((d) => text(d.kids[0])), word: byClass(phantom, "dc-fold").map((d) => text(d.kids[0].kids[d.kids[0].kids.length - 1])) };
  // three scripts of one name
  out.readers = lines(row(%(diagnostics)s));
  // the fold opens with 收起
  const fold = byClass(check, "dc-fold")[0];
  toggle(fold);
  out.opened = text(fold.kids[0]);
  // 是 on the nearest one
  const first = buttons(check, %(yes)s)[0];
  out.title = first.title;
  first.click();
  await settle(); await settle();
  out.posted = posted.slice();
  const again = row(%(check)s);
  out.after = { bad: byClass(again, "bad").length, undo: byClass(host, "dc-undo").map(text), counts: byClass(host, "dc-filter").map(text) };
  // 撤销 takes the entry back
  buttons(byClass(host, "dc-undo")[0], %(undo)s)[0].click();
  await settle(); await settle();
  out.undone = posted.slice(1);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_data_code_shows_a_download_quietly_a_broken_link_as_the_warning_with_the_file_it_may_mean_and_folds_the_rest(
        tmp_path, language):
    root = thesis(tmp_path / "thesis")
    before = _data_code(root)
    _answer(root, CHECK)
    after = _data_code(root)
    words = {"zh": dict(assign="指定生成它的脚本", yes="是", undo="撤销"), "en": dict(assign="Assign a generating script", yes="Yes", undo="Undo")}[language]
    out = _run(_DATA_CODE_BODY % {
        "before": _json(before), "after": _json(after), "raw": _json(RAW), "check": _json(CHECK), "diagnostics": _json(f"{PKG}/Data/diagnostics.csv"),
        "assign": _json(words["assign"]), "yes": _json(words["yes"]), "undo": _json(words["undo"])}, language=language)
    n = before["counts"]
    chains = sum(1 for f in before["files"] if f["type"] == "dataset" and f["written_by"] and f["read_by"])
    zh = language == "zh"
    # the filters: only the two that count problems can turn the warning colour, and 缺生成脚本 counts none
    assert out["filters"] == [
        [f"{'全部' if zh else 'All'} {n['all']}", False],
        [f"{'缺生成脚本' if zh else 'No generating script'} 0", False],
        [f"{'没有生成脚本' if zh else 'No script writes it'} {n['raw']}", False],
        [f"{'中间数据' if zh else 'Intermediate data'} {chains}", False],
        [f"{'断链' if zh else 'Broken links'} {n['broken']}", True],
        [f"{'同名文件' if zh else 'Same-name files'} {n['duplicates']}", False]]
    # a download: quiet text and the action, in no colour
    assert out["raw"]["lines"][1:] == [("没有生成脚本" if zh else "No script writes it") + words["assign"]]
    assert out["raw"]["tags"] == [] and out["raw"]["clay"] is False and out["raw"]["action"] == 1
    # the broken link: the line, the nearest candidate, and the rest folded
    line = ("断链：读取 environment/R_packages.lock.csv，文件不存在" if zh else
            "Broken link: reads environment/R_packages.lock.csv, file not found")
    assert out["check"]["bad"] == [line] and out["checkBadClay"] == ["dc-rel bad"]
    assert out["check"]["maybe"] == [("是这个吗？" if zh else "Is it this one?") + LOCK + words["yes"]]
    assert out["check"]["yes"] == 4  # the nearest, and the three others folded under it
    rest = ["00_Current/replication_v8/environment/R_packages.lock.csv", "Beyond_Persistence_1012/environment/R_packages.lock.csv",
            "Data and Code_20261012/environment/R_packages.lock.csv"]
    assert out["check"]["folds"] == [[("其他同名文件 3 个" if zh else "3 other same-name files") + ("展开" if zh else "Show more"),
                                      [path + words["yes"] for path in rest], 3]]
    assert out["opened"] == ("其他同名文件 3 个" if zh else "3 other same-name files") + ("收起" if zh else "Show less")
    # the file that only the script's text names: a plain fact with its same-name files folded; no warning of its own
    assert out["phantom"]["tags"] == [[("文件不存在" if zh else "File not found"), "status-label tone-plain"]] and out["phantom"]["bad"] == 0
    assert out["phantom"]["fold"] == [("其他同名文件 4 个" if zh else "4 other same-name files") + ("展开" if zh else "Show more")]
    # three scripts of one name, each with the folder that tells it from the others
    assert out["readers"][0] == ("被这些脚本读取：复核诊断.R（交付_20260927_审查修订）、复核诊断.R（交付_20261006）、复核诊断.R" if zh else
                                 "Read by: 复核诊断.R (交付_20260927_审查修订), 复核诊断.R (交付_20261006), 复核诊断.R")
    # 是 writes the entry the engine gave, then the page reads again; the row no longer says 断链
    assert out["posted"] == [["/api/mappings/add", {"from": LOCK, "to": CHECK, "type": "reads"}]]
    assert out["title"] == (f"将记下：{CHECK} 读取 {LOCK}" if zh else f"Record that {CHECK} reads {LOCK}")
    assert out["after"]["bad"] == 0
    assert out["after"]["undo"] == [(f"已记下：{CHECK} 读取 {LOCK}。" if zh else f"Recorded: {CHECK} reads {LOCK}.") + words["undo"]]
    # 撤销 removes that entry and nothing else
    assert out["undone"] == [["/api/mappings/delete", {"from": LOCK, "to": CHECK, "type": "reads"}]]


@needs_node
def test_data_code_counts_a_table_of_the_paper_that_no_script_writes_as_the_one_warning_of_the_filter(tmp_path):
    root = thesis(tmp_path / "thesis")
    _cite_table(root, RAW)
    _choose(root)
    data = _data_code(root)
    out = _run(r"""
global.apiGet = async (path) => (path === "/api/stata" ? { has_do: false } : %(data)s);
global.apiPost = async () => ({ ok: true });
(async () => {
  const host = el("div", "host"); views.lineage = host;
  await window.RONode.loadDataCode(host, false);
  const row = rowOf(host, "dc-row", %(raw)s), quiet = rowOf(host, "dc-row", %(fallback)s);
  const out = { filters: byClass(host, "dc-filter").map((b) => [text(b), b.cls.has("problem")]),
    tags: byClass(row, "status-label").map((e) => [text(e), e.className]), action: buttons(row, "指定生成它的脚本").length,
    quietTags: byClass(quiet, "status-label").length, quietText: byClass(quiet, "dc-nogen").map(text) };
  byClass(host, "dc-filter")[1].click();
  out.shown = byClass(host, "dc-row").map((r) => text(byClass(r, "dc-path")[0]));
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
""" % {"data": _json(data), "raw": _json(RAW), "fallback": _json(FALLBACK)}, language=None)
    assert out["filters"][1] == ["缺生成脚本 1", True] and out["filters"][2] == ["没有生成脚本 2", False]
    assert out["tags"] == [["缺生成脚本", "status-label tone-clay"]] and out["action"] == 1
    assert out["quietTags"] == 0 and out["quietText"] == ["没有生成脚本指定生成它的脚本"]
    assert out["shown"] == [RAW]  # the filter of warnings lists the warning and not the quiet ones


# -- 图表与论断 ---------------------------------------------------------------------------------------


_RESULTS_BODY = r"""
const data = %(data)s;
global.apiGet = async (path) => { calls.push("GET " + path); if (path === "/api/results") return data; throw new Error("no fixture for " + path); };
global.posted = [];
global.apiPost = async (path, body) => { calls.push("POST " + path); posted.push([path, body]); return { ok: true, generation: 6, ingest_error: null }; };
(async () => {
  const host = el("div", "host"); views.results = host;
  await window.RONode.loadResults(host, false);
  const row = (path) => host.all().find((e) => e.cls.has("res-row") && e.all().some((k) => k.cls.has("res-path") && k.textContent === path));
  const say = (path) => { const r = row(path);
    return { label: text(byClass(r, "status-label").pop()), tone: byClass(r, "status-label").pop().className,
      cause: byClass(r, "res-cause").map((e) => [text(e), e.className]), actions: byClass(r, "repair").map((e) => text(e)) }; };
  const out = { intro: text(byClass(host, "node-sub")[0]) };
  for (const [name, path] of %(paths)s) out[name] = say(path);
  // the action on the Word picture: choose a script, see the sentence, confirm
  const r = row(%(word1)s);
  const trigger = buttons(r, %(assign)s)[0];
  out.formBefore = byClass(r, "repair-form").length;
  trigger.click();
  const form = byClass(r, "repair-form")[0];
  const select = form.all().find((e) => e.tag === "SELECT");
  out.options = select.all().filter((e) => e.tag === "OPTION").map((e) => e.value);
  out.confirmOff = buttons(form, %(go)s)[0].disabled;
  select.value = %(script)s; (select.on.change || []).forEach((f) => f());
  out.sentence = text(byClass(form, "repair-sentence")[0]);
  buttons(form, %(go)s)[0].click();
  await settle(); await settle();
  out.posted = posted;
  out.reads = calls.filter((c) => c === "GET /api/results").length;
  out.lastGeneration = lastGeneration;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_figures_and_claims_draw_the_paper_s_gaps_as_warnings_with_their_action_and_the_rest_quietly(tmp_path, language):
    root = thesis(tmp_path / "thesis")
    _choose(root)
    data = _results(root)
    zh = language == "zh"
    script = f"{PKG}/code/01-build_data.py"
    paths = [["word", WORD1], ["other", WORD3], ["hand", HAND], ["stray", FIG["stray"]], ["main", FIG["main"]], ["needs", FIG["needs"]]]
    assign, attempt = ("指定生成它的脚本", "归入尝试") if zh else ("Assign a generating script", "File under an attempt")
    out = _run(_RESULTS_BODY % {
        "data": _json(data), "paths": _json(paths), "word1": _json(WORD1), "assign": _json(assign), "go": _json("确认" if zh else "Confirmed"),
        "script": _json(script)}, language=language, view="results")
    missing, lacking = ("缺来源", "缺尝试") if zh else ("Missing source", "Missing attempt")
    word_cause = "没有生成脚本：图是从 Word 文件里抽出来的" if zh else "No generating script: the figure was extracted from a Word file"
    # the paper's own figure: the warning colour, the cause in words on the row, the action beside it
    assert out["word"] == {"label": missing, "tone": "status-label tone-clay", "cause": [[word_cause, "res-cause warn"]], "actions": [assign]}
    assert out["hand"]["cause"] == [["没有生成脚本" if zh else "No script writes it", "res-cause warn"]] and out["hand"]["actions"] == [assign]
    # a figure only another draft includes says the same, in no colour
    assert out["other"] == {"label": missing, "tone": "status-label tone-plain", "cause": [[word_cause, "res-cause"]], "actions": [assign]}
    assert out["stray"] == {"label": lacking, "tone": "status-label tone-plain",
                            "cause": [["生成脚本没有归入尝试" if zh else "The generating script is not in an attempt", "res-cause"]], "actions": [attempt]}
    assert out["main"] == {"label": "完整" if zh else "Complete", "tone": "status-label tone-olive", "cause": [], "actions": []}
    # its script reads a file that is not there: the paper's warning, mended on the file's own page (no action here)
    assert out["needs"]["tone"] == "status-label tone-clay" and out["needs"]["actions"] == []
    assert out["needs"]["cause"] == [["生成它的脚本读的文件不在" if zh else "A file the generating script reads is not there", "res-cause warn"]]
    assert ("红色" in out["intro"]) if zh else ("marked in red" in out["intro"])
    # the action: the form is drawn when pressed; the scripts of the project to choose from; the sentence; the write
    assert out["formBefore"] == 0
    assert out["options"][0] == "" and script in out["options"] and out["confirmOff"] is True
    assert out["sentence"] == (f"将记下：{script} 生成 {WORD1}" if zh else f"Record that {script} generates {WORD1}")
    assert out["posted"] == [["/api/mappings/add", {"from": script, "to": WORD1, "type": "generates"}]]
    assert out["reads"] == 2 and out["lastGeneration"] == 6  # the page read again, once, keeping the generation the POST returned


@needs_node
def test_figures_and_claims_draw_a_claim_of_the_paper_with_nothing_behind_it_as_a_warning_without_an_action(tmp_path):
    root = thesis(tmp_path / "thesis")
    _choose(root)
    claims = _results(root, "00_Current/Beyond_Persistence_v9_reader.md")
    out = _run(r"""
global.apiGet = async (path) => { if (path.startsWith("/api/results?draft=")) return %(claims)s; return %(data)s; };
global.apiPost = async () => ({ ok: true });
(async () => {
  const host = el("div", "host"); views.results = host;
  await window.RONode.loadResults(host, false);
  const draft = byClass(host, "res-draft").find((d) => d.tag === "DETAILS");
  draft.open = true; (draft.on.toggle || []).forEach((f) => f());
  await settle(); await settle();
  const rows = byClass(host, "res-row").filter((r) => byClass(r, "claim").length);
  process.stdout.write(JSON.stringify(rows.map((r) => [text(byClass(r, "status-label").pop()), byClass(r, "status-label").pop().className,
    byClass(r, "res-cause").map(text), byClass(r, "repair").length])));
})().catch((e) => { console.error(e); process.exit(1); });
""" % {"claims": _json(claims), "data": _json(_results(root))}, language=None, view="results")
    assert len(out) == 4
    assert all(row[0] == "缺来源" and row[1] == "status-label tone-clay" and row[2] == ["没有找到支撑这个数字的结果"] and row[3] == 0 for row in out)


# -- the node page ------------------------------------------------------------------------------------


_NODE_BODY = r"""
(async () => {
  const out = {};
  for (const [name, page] of %(pages)s) {
    const host = el("div", "host");
    window.RONode.renderTrace(host, page, {});
    out[name] = {
      gaps: byClass(host, "trace-gap").map((g) => [g.className, byClass(g, "trace-gap-label").map(text), byClass(g, "trace-gap-cause").map(text),
        g.all().filter((k) => k.tag === "BUTTON").map(text)]),
      breaks: byClass(host, "trace-break").map((b) => [b.className, text(b)]),
      bad: byClass(host, "bad").map(text), yes: buttons(host, %(yes)s).length,
      maybe: byClass(host, "dc-maybe").map(text),
      flags: host.all().filter((k) => k.cls.has("status-label")).map((k) => [text(k), k.className]),
    };
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
@pytest.mark.parametrize("language", ["zh", "en"])
def test_a_node_page_draws_its_gaps_in_the_tone_the_paper_gives_them_and_a_broken_link_with_the_file_it_may_mean(tmp_path, language):
    root = thesis(tmp_path / "thesis")
    _choose(root)
    pages = [["word", _page(root, f"figure:{WORD1}")], ["other", _page(root, f"figure:{WORD3}")], ["raw", _page(root, f"dataset:{RAW}")],
             ["check", _page(root, f"script:{CHECK}")]]
    zh = language == "zh"
    out = _run(_NODE_BODY % {"pages": _json([[name, page] for name, page in pages]), "yes": _json("是" if zh else "Yes")},
               language=language, view="node")
    assign, attempt = ("指定生成它的脚本", "归入尝试") if zh else ("Assign a generating script", "File under an attempt")
    word_cause = "没有生成脚本：图是从 Word 文件里抽出来的" if zh else "No generating script: the figure was extracted from a Word file"
    missing = "缺生成脚本" if zh else "No generating script"
    # the paper's figure: a gap in the warning colour naming 缺生成脚本, the cause beneath it, the action
    assert out["word"]["gaps"][0] == ["trace-gap", [missing], [word_cause], [assign]]
    # the attempt it lacks is quiet: the source is what is missing
    assert out["word"]["gaps"][1][0] == "trace-gap wide quiet" and out["word"]["gaps"][1][3] == [attempt]
    # the same kind of figure of another draft, and a download: the gap in no colour, with its action
    assert out["other"]["gaps"][0] == ["trace-gap quiet", [word_cause], [], [assign]]
    assert out["raw"]["gaps"][0] == ["trace-gap quiet", ["没有生成脚本" if zh else "No script writes it"], [], [assign]]
    # a script in no attempt: quiet, with 归入尝试
    assert out["check"]["gaps"] == [["trace-gap wide quiet", ["还没有归入任何尝试" if zh else "Not linked to an attempt yet"], [], [attempt]]]
    # its broken link, always a warning, with the nearest file it may mean; the file it names is a plain fact in its column
    assert out["check"]["bad"] == ["断链：读取 environment/R_packages.lock.csv，文件不存在" if zh else
                                   "Broken link: reads environment/R_packages.lock.csv, file not found"]
    assert out["check"]["maybe"] == [("是这个吗？" if zh else "Is it this one?") + LOCK + ("是" if zh else "Yes")]
    assert out["check"]["yes"] == 4
    assert [f for f in out["check"]["flags"] if f[0] == ("文件不存在" if zh else "File not found")] == [
        [("文件不存在" if zh else "File not found"), "status-label tone-clay"]]  # the broken link's file: the warning's
    assert not [f for f in out["word"]["flags"] if "clay" in f[1]]
    english = [w for page in out.values() for w in [*sum((g[1] + g[2] + g[3] for g in page["gaps"]), []), *page["maybe"]]]
    if not zh:
        assert not [w for w in english if CJK.search(w) and w != LOCK + "Yes" and LOCK not in w]  # only the paths are Chinese

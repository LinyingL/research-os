"""Node-page interface labels follow the language; research text does not."""

import json

import pytest

from researchos import nodepage, provenance, trace

from test_v9_language import CJK, WEBAPP, _DESK_DOM, _i18n_source, _node, english_table, needs_node


def test_node_api_interface_words_have_english_entries():
    table = english_table()
    labels = [*nodepage.TYPE_WORDS.values(), *nodepage.RELATION_WORDS.values(),
              *nodepage.TRACE_STATES.values(), *trace.BREAK_LABELS.values(), *trace.REPAIR_LABELS.values(),
              *provenance.CAUSES.values(), *provenance.CLAIM_CAUSES.values()]
    assert [word for word in labels if word not in table] == []


@needs_node
@pytest.mark.parametrize("language", ["en", "zh"])
def test_node_page_translates_labels_and_keeps_research_text(language):
    # Authored text deliberately matches interface keys: it must still stay Chinese.
    page = {
        "id": "figure:figures/结果.png", "type": "figure", "type_label": "图表", "name": "改动与备注",
        "path": "figures/结果.png", "exists": True, "in_index": True, "details": {}, "links": [],
        "trace": {"columns": [
            {"key": "in", "title": "数据", "items": [{"id": "dataset:data.csv", "type": "dataset",
                "label": "data.csv", "path": "data.csv", "exists": True, "raw": True}]},
            {"key": "self", "title": "本页", "items": [{"id": "figure:figures/结果.png", "type": "figure",
                "label": "结果.png", "path": "figures/结果.png", "exists": True, "self": True}]},
            {"key": "cited", "title": "被引用", "items": [{"id": "section:paper.md#result", "type": "section",
                "label": "研究标题", "file": "paper.md", "exists": True}]},
        ], "lines": [], "breaks": [], "attempts": [], "variables": []},
    }
    script = (
        "global.window = {addEventListener(){}, scrollTo(){}, scrollY: 0};\n"
        f"window.ROI18N_SAVED = {json.dumps(language)};\n" + _i18n_source()
        + _DESK_DOM
        + r"""
const baseMk = mk;
global.mk = (tag, cls, text) => {
  const e = baseMk(tag, cls, text);
  e.style = {};
  e.attrs = {};
  e.setAttribute = (name, value) => { e.attrs[name] = String(value); };
  e.replaceChildren = (...kids) => { e.innerHTML = ''; e.append(...kids); };
  e.classList.contains = () => false;
  e.contains = (child) => e.kids.includes(child);
  Object.defineProperty(e, 'childElementCount', {get: () => e.kids.filter(k => typeof k !== 'string').length});
  return e;
};
global.mkButton = (label, cls, onClick) => Object.assign(mk('button', cls, label), {onClick});
document.createElementNS = (_ns, tag) => mk(tag);
document.activeElement = null;
global.requestAnimationFrame = () => {}; // Layout has no bearing on label rendering.
global.currentView = 'results';
global.SECTION_OF_VIEW = {results: 'results'};
global.SECTION_TITLES = {results: '图表与论断'};
global.activateView = name => { currentView = name; };
global.humanRecordsBlocked = () => null;
global.RODesk = {ensureContext: async () => ({}), context: () => ({mode: 'single'}), _whenWords: () => '14 Oct'};
"""
        + f"const page = {json.dumps(page, ensure_ascii=False)};\n"
        + r"""
global.apiGet = async path => {
  if (path.startsWith('/api/node?')) return page;
  if (path.startsWith('/api/history?')) return {versions: [{version: '1', at: '2026-10-14', source: 'scan'}]};
  if (path.startsWith('/api/notes?')) return {notes: [{id: 'n1', text: '查看内容', label: 'Author', at: '2026-10-14', by: 'author@example.com'}]};
  if (path === '/api/me') return {me: {id: 'author@example.com'}};
  throw Error(path);
};
"""
        + (WEBAPP / "node.js").read_text(encoding="utf-8")
        + r"""
(async () => {
  const host = mk('div', 'host'); views.node = host;
  window.RONode.openNode(page.id);
  await window.RONode.loadNode(host);
  const words = [];
  const walk = e => {
    for (const child of e.kids) {
      if (typeof child === 'string') words.push(child);
      else walk(child);
    }
    if (e.placeholder) words.push(e.placeholder);
    for (const key of ['title', 'aria-label']) if (e.attrs[key]) words.push(e.attrs[key]);
  };
  walk(host);
  process.stdout.write(JSON.stringify(words));
})().catch(e => { console.error(e); process.exit(1); });
"""
    )
    words = _node(script)
    for authored in ("改动与备注", "查看内容", "figures/结果.png", "结果.png", "研究标题"):
        assert authored in words
    if language == "en":
        for label in ("Back to Figures and claims", "Figure", "View content", "Show in Finder", "Data", "This item",
                      "Used in", "Changes and comments", "First recorded version", "Add comment", "Link judgments", "Comment",
                      "Write a comment: why it was done this way, what to watch for"):
            assert label in words
        authored = {"改动与备注", "查看内容", "figures/结果.png", "结果.png", "研究标题"}
        assert [word for word in words if CJK.search(word) and word not in authored] == []
    else:
        for label in ("返回图表与论断", "图表", "在 Finder 中显示", "数据", "本页", "被引用", "最早记下的版本", "添加备注", "连线确认", "备注",
                      "写一条备注：为什么这样做、要注意什么"):
            assert label in words


@needs_node
@pytest.mark.parametrize("language", ["en", "zh", None])
def test_data_code_broken_links_read_as_the_8_8_glossary_in_chinese(language):
    """数据与代码's broken links: in Chinese exactly the 8.8 glossary's
    「（写出，文件不存在）」/「（读取，文件不存在）」, chosen by the link's code,
    not its words (13.6); in English one sentence; node.js alone is Chinese."""
    data = {"files": [{"id": "script:steps/17.py", "type": "script", "type_label": "脚本", "path": "steps/17.py",
                       "exists": True, "reads": [], "writes": [], "orphan_input": False, "review": 0, "broken": [
                           {"script": "steps/17.py", "target": "figs/a.png", "type": "writes", "relation": "写出"},
                           {"script": "steps/17.py", "target": "data/gone.csv", "type": "reads", "relation": "读取"}]}],
            "counts": {"all": 1, "broken": 1}}
    i18n = "" if language is None else f"window.ROI18N_SAVED = {json.dumps(language)};\n" + _i18n_source()
    script = ("global.window = {addEventListener(){}, scrollTo(){}, scrollY: 0};\n" + i18n + _DESK_DOM + r"""
const baseMk = mk;
global.mk = (tag, cls, text) => { const e = baseMk(tag, cls, text); e.attrs = {};
  e.setAttribute = (name, value) => { e.attrs[name] = String(value); };
  e.replaceChildren = (...kids) => { e.innerHTML = ''; e.append(...kids); }; e.querySelectorAll = () => []; return e; };
global.mkButton = (label, cls, onClick) => Object.assign(mk('button', cls, label), {onClick});
document.createElementNS = (_ns, tag) => mk(tag);
global.currentView = 'data';
""" + f"global.apiGet = async () => ({json.dumps(data, ensure_ascii=False)});\n"
              + (WEBAPP / "node.js").read_text(encoding="utf-8") + r"""
(async () => {
  const host = mk('div', 'host'); await window.RONode.loadDataCode(host);
  const bad = []; const walk = (e) => e.kids.forEach((k) => { if (typeof k === 'string') return;
    if (k.className === 'dc-rel bad') bad.push(k.kids.join('')); walk(k); });
  walk(host); process.stdout.write(JSON.stringify(bad));
})().catch((e) => { console.error(e); process.exit(1); });
""")
    if language == "en":
        assert _node(script) == ["Broken link: steps/17.py writes figs/a.png (file not found)",
                                 "Broken link: steps/17.py reads data/gone.csv (file not found)"]
    else:
        assert _node(script) == ["断链：steps/17.py 写出 figs/a.png（写出，文件不存在）",
                                 "断链：steps/17.py 读取 data/gone.csv（读取，文件不存在）"]


@needs_node
@pytest.mark.parametrize("language, said", [
    (None, ["确认了这条关联：a reads b", "否决了这条关联：a reads b", "撤回了连线确认：a reads b",
            "撤销了上一次连线确认：a reads b", "连线确认：a reads b"]),
    ("zh", ["确认了这条关联：a reads b", "否决了这条关联：a reads b", "撤回了连线确认：a reads b",
            "撤销了上一次连线确认：a reads b", "连线确认：a reads b"]),
    ("en", ["Confirmed this link: a reads b", "Rejected this link: a reads b", "Withdrew the link judgment: a reads b",
            "Undid the previous link judgment: a reads b", "Link judgment: a reads b"]),
])
def test_18_2_a_link_judgment_in_the_timeline_is_one_sentence_per_verdict(language, said):
    """DESIGN.md 18.2: a person's confirming or rejecting of a machine link is 连线确认 ("Link
    judgment"); each verdict is one template (13.6), not a verb fragment joined to a shared tail."""
    i18n = "" if language is None else f"window.ROI18N_SAVED = {json.dumps(language)};\n" + _i18n_source()
    script = ("global.window = {addEventListener(){}}; global.document = {getElementById(){return null}};\n" + i18n
              + (WEBAPP / "node.js").read_text(encoding="utf-8")
              + "\nprocess.stdout.write(JSON.stringify(['confirmed', 'rejected', 'withdrawn', 'undone', 'other']"
              ".map((v) => window.RONode._judgmentSentence(v, 'a reads b'))));\n")
    assert _node(script) == said

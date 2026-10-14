"""Node-page interface labels follow the language; research text does not."""

import json

import pytest

from researchos import nodepage, trace

from test_v9_language import CJK, WEBAPP, _DESK_DOM, _i18n_source, _node, english_table, needs_node


def test_node_api_interface_words_have_english_entries():
    table = english_table()
    labels = [*nodepage.TYPE_WORDS.values(), *nodepage.RELATION_WORDS.values(),
              *nodepage.TRACE_STATES.values(), *trace.BREAK_LABELS.values(), *trace.REPAIR_LABELS.values()]
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
global.SECTION_TITLES = {results: '成果'};
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
        for label in ("Back to Outputs", "Figure", "View content", "Show in Finder", "Data", "This item",
                      "Used in", "Changes and notes", "First recorded version", "Add note", "Link judgments"):
            assert label in words
        authored = {"改动与备注", "查看内容", "figures/结果.png", "结果.png", "研究标题"}
        assert [word for word in words if CJK.search(word) and word not in authored] == []
    else:
        for label in ("返回成果", "图表", "在 Finder 中显示", "数据", "本页", "被引用", "最早记下的版本", "添加笔记", "判断"):
            assert label in words

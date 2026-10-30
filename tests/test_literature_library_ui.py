"""Drive missing-abstract actions in the real literature view."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_workspace_js import DOM
from test_v9_language import _app_block, _app_function, _i18n_source, _node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(scenario):
    code = DOM + '\nwindow.ROI18N_SAVED="en";\n' + _i18n_source()
    code += '\nfunction t(s,v){return window.ROI18N.t(s,v);}\n'
    code += _app_function("function mk(") + _app_function("function mkButton(")
    code += (Path(__file__).parents[1] / 'src/researchos/webapp/workspace.js').read_text()
    code += _app_block("// -- 文献 (DESIGN.md 11.4", "// -- end of the 文献 view")
    code += r'''
Object.defineProperty(Element.prototype,'innerHTML',{set(v){this.replaceChildren();}});
Object.defineProperty(Element.prototype,'isConnected',{get(){for(let n=this;n;n=n.parentNode)if(n===root)return true;return false;}});
function setStatus(node,kind,words,err){node.textContent=words;}
currentView='literature';views.literature=root;
const a={id:'a',title:'Already available',doi:'10.1234/a',abstract:'Existing abstract.',authors:[]};
const b={id:'b',title:'Missing',doi:'10.1234/b',abstract:null,abstract_checked:true,authors:[]};
const c={id:'c',title:'No DOI',doi:null,abstract:null,authors:[]};
const data={library:{items:[a,b,c],warnings:[]},lookup:{enabled:true},zotero:{installed:false},drafts:[]};
litState.data=data;renderLiterature(root,data);
'''
    code += '\n(async()=>{\n' + scenario + '\n})().catch(e=>{console.error(e.stack);process.exitCode=1;});'
    try:
        return _node(code)
    except subprocess.CalledProcessError as exc:
        pytest.fail(exc.stderr)


def test_negative_lookup_can_retry_and_displays_the_actual_source():
    shown = run(r'''
root.all().find(e=>e.classList.contains('lit-library-item')&&e.textContent.includes('Missing')).click();
const retry=find('Retry abstract lookup');
global.apiPost=async(path,body)=>{posted.push([path,body]);return {abstract:'Recovered abstract.',source:'OpenAlex',source_url:'https://openalex.org/W123'};};
await retry.click();
console.log(JSON.stringify({posted,words:byClass('lit-reader').textContent,
 source:byClass('lit-reader').all().find(e=>e.tagName==='A'&&e.textContent==='View source').href}));
''')
    assert shown["posted"] == [["/api/literature/abstract", {"doi": "10.1234/b", "refresh": True}]]
    assert "Recovered abstract." in shown["words"] and "Abstract source: OpenAlex" in shown["words"]
    assert shown["source"] == "https://openalex.org/W123"


def test_batch_skips_existing_abstracts_and_preserves_the_current_reader():
    shown = run(r'''
global.apiPost=async(path,body)=>{posted.push([path,body]);return {abstract:'Recovered abstract.',source:'OpenAlex'};};
await find('Fetch missing abstracts').click();
console.log(JSON.stringify({posted,selected:litState.selected,words:byClass('lit-reader').textContent,
 progress:byClass('lit-abstract-progress').textContent,disabled:find('Fetch missing abstracts').disabled}));
''')
    assert len(shown["posted"]) == 1 and shown["posted"][0][1]["doi"] == "10.1234/b"
    assert shown["selected"] == "a" and "Existing abstract." in shown["words"]
    assert shown["progress"] == "Checked 1/1; found 1; failed 0" and shown["disabled"]


def test_batch_distinguishes_a_failed_request_from_a_completed_negative_lookup():
    shown = run(r'''
data.library.items.push({id:'d',title:'Temporarily unavailable',doi:'10.1234/d',abstract:null,authors:[]});
global.apiPost=async(path,body)=>{posted.push([path,body]);if(body.doi.endsWith('/d'))throw Error('offline');return {abstract:null,source:null};};
await find('Fetch missing abstracts').click();
console.log(JSON.stringify({calls:posted.length,progress:byClass('lit-abstract-progress').textContent,
 checked:b.abstract_checked,failedChecked:data.library.items[3].abstract_checked||false,enabled:!find('Fetch missing abstracts').disabled}));
''')
    assert shown == {"calls": 2, "progress": "Checked 2/2; found 0; failed 1",
                     "checked": True, "failedChecked": False, "enabled": True}


def test_switching_projects_stops_remaining_batch_requests_and_does_not_apply_a_late_result():
    shown = run(r'''
data.library.items.push({id:'d',title:'Another missing',doi:'10.1234/d',abstract:null,authors:[]});
const waiting=deferred();global.apiPost=async(path,body)=>{posted.push([path,body]);return waiting.promise;};
const pending=fillMissingAbstracts(data);state.dataEpoch++;currentView='workspace';
waiting.resolve({abstract:'Late result.',source:'OpenAlex'});await pending;
console.log(JSON.stringify({calls:posted.length,abstract:b.abstract,running:litState.abstractBatch.running}));
''')
    assert shown == {"calls": 1, "abstract": None, "running": False}


def test_file_research_context_is_shown_under_its_source_file_without_doi_support_claims():
    shown = run(r"""
a.files=['sources.bib'];
data.research_records_by_file={'sources.bib':[{report:'research/results/manual.md',title:'Fictional reading check',question:'Which background fits?',matches:[{path:'sources.bib',purpose:'Background candidates only'}]}]};
const reader=byClass('lit-reader');reader.replaceChildren();renderLiteratureReader(reader,a,data);
console.log(JSON.stringify({words:reader.textContent,source:byClass('lit-reader-sources').textContent}));
""")
    assert 'Notes and reports that mention this file' in shown['source']
    assert 'Background candidates only' in shown['source']
    assert 'Fictional reading check' in shown['source']

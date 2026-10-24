"""Word file links stay inside the app for a bounded, read-only preview."""

import json
import shutil
import subprocess
import zipfile

import pytest

from researchos import cloud, docx_preview
from researchos.webapp import server
from test_webapp_server import live_server, _get  # noqa: F401

NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _write(path, body, *, namespace=NS, raw=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", raw if raw is not None else
                         f'<w:document xmlns:w="{namespace}"><w:body>{body}</w:body></w:document>')
        archive.writestr("../never-extract.txt", "not extracted")


@pytest.mark.parametrize("namespace", [NS, "http://purl.oclc.org/ooxml/wordprocessingml/main"])
def test_word_body_and_table_preview_preserves_order_and_never_changes_files(tmp_path, namespace):
    p = tmp_path / "paper.docx"
    _write(p, '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Finding</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>A &amp; B</w:t><w:tab/><w:t>0.25</w:t><w:br/><w:t>Next line</w:t></w:r></w:p>'
           '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Estimate</w:t></w:r></w:p></w:tc>'
           '<w:tc><w:p><w:r><w:t>0.25</w:t></w:r></w:p><w:p><w:r><w:t>(0.03)</w:t></w:r></w:p></w:tc></w:tr></w:tbl>',
           namespace=namespace)
    before = p.read_bytes()
    data = server.file_payload(tmp_path, "paper.docx")
    assert data["kind"] == "docx" and data["truncated"] is False
    assert data["blocks"] == [
        {"kind": "heading", "level": 1, "text": "Finding"},
        {"kind": "paragraph", "level": None, "text": "A & B\t0.25\nNext line"},
        {"kind": "table", "rows": [["Estimate", "0.25\n(0.03)"]]}]
    assert p.read_bytes() == before and not (tmp_path.parent / "never-extract.txt").exists()
    assert list(tmp_path.iterdir()) == [p]


def test_deleted_text_and_field_instructions_do_not_appear_but_insertions_do(tmp_path):
    p = tmp_path / "review.docx"
    _write(p, '<w:sdt><w:sdtContent><w:p><w:del><w:r><w:t>Deleted</w:t></w:r></w:del>'
           '<w:ins><w:r><w:t>Current</w:t></w:r></w:ins><w:r><w:instrText>EXECUTE</w:instrText></w:r>'
           '<w:hyperlink><w:r><w:t>Reference label</w:t></w:r></w:hyperlink></w:p></w:sdtContent></w:sdt>')
    assert docx_preview.read(p)["blocks"][0]["text"] == "CurrentReference label"


def test_equations_and_images_have_explicit_placeholders(tmp_path):
    p = tmp_path / "equations.docx"
    _write(p, '<w:p><w:r><w:t>See </w:t></w:r><m:oMath xmlns:m="urn:math"><m:t>x/y</m:t></m:oMath>'
           '<w:r><w:drawing><w:t>Object internals</w:t></w:drawing></w:r></w:p>')
    data = server.file_payload(tmp_path, p.name, "en")
    assert data["blocks"][0]["text"] == "See [Equation][Image or object]"
    assert "〔公式〕" in server.file_payload(tmp_path, p.name)["blocks"][0]["text"]


@pytest.mark.parametrize("broken", [b"not a zip", b"PK\x03\x04broken"])
def test_unreadable_word_gets_a_specific_localized_refusal(tmp_path, broken):
    (tmp_path / "broken.docx").write_bytes(broken)
    for lang in ("zh", "en"):
        with pytest.raises(server.DocxPreviewApiError) as err:
            server.file_payload(tmp_path, "broken.docx", lang)
        assert err.value.state == "docx_refused"
        assert ("Open" if lang == "en" else "打开") in err.value.extra["message"]


@pytest.mark.parametrize("body", [
    b'<document/>', b'<w:document xmlns:w="urn:unknown"/>',
    f'<w:document xmlns:w="{NS}"/>'.encode(),
    f'<!DOCTYPE w:document [<!ENTITY x "expansion">]><w:document xmlns:w="{NS}"><w:body>&x;</w:body></w:document>'.encode(),
    ('<?xml version="1.0" encoding="utf-16"?><!DOCTYPE x><w:document xmlns:w="'+NS+'"><w:body/></w:document>').encode('utf-16'),
])
def test_invalid_xml_missing_body_and_doctype_are_refused(tmp_path, body):
    p = tmp_path / "invalid.docx"
    _write(p, "", raw=body)
    with pytest.raises(docx_preview.PreviewError):
        docx_preview.read(p)


def test_word_text_limit_truncates_cleanly_and_size_limits_refuse(tmp_path, monkeypatch):
    p = tmp_path / "long.docx"
    _write(p, '<w:p><w:r><w:t>abcdefghij</w:t></w:r></w:p>')
    monkeypatch.setattr(docx_preview, "MAX_TEXT_CHARS", 5)
    data = docx_preview.read(p)
    assert data["truncated"] and data["blocks"][0]["text"] == "abcde"
    monkeypatch.setattr(docx_preview, "MAX_XML_BYTES", 20)
    with pytest.raises(docx_preview.PreviewError) as err:
        docx_preview.read(p)
    assert err.value.code == "too_large"


def test_deeply_nested_xml_does_not_reach_python_recursion_limit(tmp_path):
    p = tmp_path / "nested.docx"
    _write(p, '<w:sdt>' * 80 + '<w:p/>' + '</w:sdt>' * 80)
    with pytest.raises(docx_preview.PreviewError) as err:
        docx_preview.read(p)
    assert err.value.code == "too_large"


def test_word_cannot_escape_the_project_or_trigger_cloud_download(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    _write(tmp_path / "outside.docx", '<w:p/>')
    (root / "link.docx").symlink_to(tmp_path / "outside.docx")
    with pytest.raises(server.PathTraversalError):
        server.file_payload(root, "link.docx")
    _write(root / "cloud.docx", '<w:p/>')
    def blocked(path):
        raise cloud.CloudOnlyError(path, cloud.Provider("OneDrive", "OneDrive", "OneDrive", "/nowhere"))
    monkeypatch.setattr(cloud, "check_readable", blocked)
    with pytest.raises(server.CloudOnlyApiError):
        server.file_payload(root, "cloud.docx")


def test_http_word_preview_and_unsupported_binary_have_stable_types(live_server):  # noqa: F811
    url, root = live_server
    _write(root / "paper.docx", '<w:p><w:r><w:t>Readable body</w:t></w:r></w:p>')
    status, body = _get(url, "/api/file?path=paper.docx")
    assert status == 200 and body["kind"] == "docx" and body["blocks"][0]["text"] == "Readable body"
    (root / "other.bin").write_bytes(b"\x00binary")
    status, body = _get(url, "/api/file?path=other.bin")
    assert status == 415 and body["state"] == "preview_unsupported"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_word_preview_renders_paragraphs_and_cells_as_text_in_both_languages(lang):
    from test_v9_language import _MENU_DOM, _app_function, _i18n_source, _node
    code = ("global.window={};window.ROI18N_SAVED=" + json.dumps(lang) + ";\n" + _i18n_source()
            + _MENU_DOM + '\nfunction t(s,v){return window.ROI18N.t(s,v);}\n'
            + _app_function("function mk(") + _app_function("function docxPreview("))
    code += r'''
const node=docxPreview({blocks:[{kind:'heading',level:1,text:'Paper plane study'},
  {kind:'paragraph',text:'<script>untrusted()</script>'},
  {kind:'table',rows:[['Estimate','<img src=x onerror=evil()>']]}],truncated:true});
function read(n){return typeof n==='string'?n:[n.tag,n.className,...n.kids.map(read)];}
console.log(JSON.stringify(read(node)));
'''
    shown = _node(code)
    assert shown[4] == ["h1", "", "Paper plane study"]
    assert shown[5] == ["p", "", "<script>untrusted()</script>"]
    assert shown[6][2][2][3] == ["td", "", "<img src=x onerror=evil()>"]
    if lang == "en":
        assert "Click “Open”" in shown[2][2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("response", ["docx", "unsupported", "legacy_415", "docx_refused"])
def test_file_panel_shows_word_or_a_specific_message_and_keeps_open_available(response):
    from test_workspace_js import DOM
    from test_v9_language import _app_block, _app_function, _i18n_source, _node
    code = DOM + '\nwindow.ROI18N_SAVED="en";\n' + _i18n_source()
    code += '\nfunction t(s,v){return window.ROI18N.t(s,v);}\n'
    code += _app_block("const FILE_TYPE_RULES =", "function codePreview(")
    for fn in ("function mk(", "function buildFileActions(", "function isCurrentFilePanel(",
               "function docxPreview("):
        code += _app_function(fn)
    code += '\nconst actualOpenFilePanel=' + _app_function("async function openFilePanel(") + ';\n'
    code += r'''
Object.defineProperty(Element.prototype,'innerHTML',{set(v){this.replaceChildren();}});
let generic=0;const actions=[];
function renderGitHubLink(){}
function renderErrorBox(node,err){generic++;node.append('Generic error');}
function renderBlockingError(node,framing,err){node.append(framing);}
function runOpenAction(path,reveal,status){actions.push({path,reveal});}
global.apiGet=async()=>{
  if(RESPONSE==='docx') return {kind:'docx',blocks:[{kind:'paragraph',text:'Readable Word body.'}]};
  throw Object.assign(Error('refused'),RESPONSE==='legacy_415'?{status:415}:RESPONSE==='unsupported'?
    {state:'preview_unsupported'}:{state:'docx_refused',engineMessage:'Could not read this Word file.'});
};
(async()=>{
  await actualOpenFilePanel('paper.docx');
  const buttons=panelBody.all().filter(e=>e.tagName==='BUTTON');
  await buttons.find(e=>e.textContent==='Open').click();
  console.log(JSON.stringify({generic,words:panelBody.textContent,actions,
    preview:!!panelBody.all().find(e=>e.classList.contains('word-preview'))}));
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
'''.replace("RESPONSE", json.dumps(response))
    try:
        shown = _node(code)
    except subprocess.CalledProcessError as exc:
        pytest.fail(exc.stderr)
    assert shown["generic"] == 0
    assert shown["actions"] == [{"path": "paper.docx", "reveal": False}]
    assert shown["preview"] is (response == "docx")
    if response == "docx":
        assert "Readable Word body." in shown["words"]
    elif response == "docx_refused":
        assert "Could not read this Word file." in shown["words"]
    else:
        assert "This file format cannot be previewed yet." in shown["words"]

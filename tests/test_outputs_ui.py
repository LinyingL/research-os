"""Output-folder editing preserves drafts and ignores late project responses."""
import pytest

from test_workspace_js import NODE, run

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def test_output_folder_form_saves_the_bound_project_and_keeps_errors_editable():
    result = run(r'''
const folders={code:'scripts',data:'data',figure:'figures',table:'tables',literature:'references',document:'reports',variable:'research/variables'};
customGet=path=>path==='/api/outputs/folders'?{folders,revision:'r0'}:undefined;
let calls=0;
customPost=(path,body)=>{if(path==='/api/outputs/folders'){calls++;if(calls===1)throw Error('file changed');return {revision:'r1'};}};
await window.ROWorkspace.load(root);
await find('Output folders').click();
const screen=panelTitle.textContent+panelBody.textContent;
typed(field('Figure',panelBody),'outputs/charts');
await find('Save',panelBody).click();
const dirtyAfterFailure=window.ROWorkspace.isDirty(), editableAfterFailure=!field('Figure',panelBody).disabled;
await find('Save',panelBody).click();
console.log(JSON.stringify({screen,dirtyAfterFailure,editableAfterFailure,dirtyAfterSave:window.ROWorkspace.isDirty(),posted}));
''',lang="en")
    assert not any("\u4e00" <= c <= "\u9fff" for c in result["screen"])
    assert result["dirtyAfterFailure"] and result["editableAfterFailure"]
    assert not result["dirtyAfterSave"]
    body = result["posted"][-1][1]
    assert body["project"] == "/projects/study" and body["revision"] == "r0"
    assert body["folders"]["figure"] == "outputs/charts"


def test_unsaved_folder_settings_are_guarded_and_save_disables_inputs():
    result = run(r'''
const folders={code:'code',data:'data',figure:'figures',table:'tables',literature:'literature',document:'reports',variable:'research/variables'};
const pending=deferred();
customGet=path=>path==='/api/outputs/folders'?{folders,revision:null}:undefined;
customPost=path=>path==='/api/outputs/folders'?pending.promise:undefined;
await window.ROWorkspace.load(root);await find('输出目录').click();
typed(field('图',panelBody),'charts');
answers.push(false);const refused=!window.ROWorkspace.mayLeavePanel();
const saving=find('保存',panelBody).click();await sleepTick();
const disabled=field('图',panelBody).disabled;
pending.resolve({revision:'saved'});await saving;
console.log(JSON.stringify({refused,disabled,clean:!window.ROWorkspace.isDirty(),enabled:!field('图',panelBody).disabled}));
''')
    assert all(result.values())


def test_late_folder_read_never_replaces_a_new_project_panel():
    result = run(r'''
const late=deferred();customGet=path=>path==='/api/outputs/folders'?late.promise:undefined;
await window.ROWorkspace.load(root);const old=find('Output folders').click();await sleepTick();
state.dataEpoch++;state.currentPath='/projects/new';window.ROWorkspace.reset();
panelBody.replaceChildren(new Element('p'));panelBody.children[0].textContent='New project panel';
late.resolve({folders:{code:'old'},revision:null});await old;
console.log(JSON.stringify({text:panelBody.textContent}));
''',lang="en")
    assert result["text"] == "New project panel"

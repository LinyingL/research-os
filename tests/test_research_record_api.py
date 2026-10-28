"""Research context uses the existing guarded local workspace API."""
import dataclasses
import json

import pytest

from researchos import research_records, workspace
from test_workspace_api import writing_server
from test_webapp_server import _get, _post, _request_with_headers
from test_v8_phase3 import Team, _serve
from researchos import proposals

REPORT = 'research/results/' + 'a' * 32 + '.md'


def _report(root):
    (root / REPORT).parent.mkdir(parents=True, exist_ok=True)
    (root / REPORT).write_text('# Manual result\n')


def test_manual_research_record_roundtrip_and_revision_conflict(writing_server):
    base, root, _ = writing_server
    _report(root)
    endpoint = '/api/workspace/research-record'
    status, record = _get(base, endpoint + '?report=' + REPORT)
    assert status == 200 and record['revision'] is None
    body = {'project': str(root), 'report': REPORT, 'revision': None,
            'context': {'question': 'Why?', 'finding': 'Observation',
                        'attempt_draft': {'description': 'Compare again'}}}
    status, saved = _post(base, endpoint, body)
    assert status == 200, saved
    assert saved['context']['finding'] == 'Observation'
    assert _post(base, endpoint, body)[0] == 409
    assert _get(base, endpoint + '?report=' + REPORT)[1]['revision'] == saved['revision']
    assert (root / REPORT).read_text() == '# Manual result\n'
    assert not (root / '.researchos/attempts.toml').exists()


@pytest.mark.parametrize('endpoint', ['/api/workspace/research-record', '/api/workspace/research-record/variable'])
def test_research_record_writers_check_origin_project_and_readonly(writing_server, endpoint):
    base, root, httpd = writing_server
    _report(root)
    body = {'project': str(root), 'report': REPORT, 'revision': None, 'context': {},
            'path': 'variable.toml', 'id': 'distance'}
    assert _post(base, endpoint, {**body, 'project': '/other'})[0] == 409
    assert _request_with_headers(base, 'POST', endpoint,
                                 {'Origin': 'https://example.com', 'Content-Type': 'application/json'},
                                 json.dumps(body).encode())[0] == 403
    httpd.set_served(dataclasses.replace(httpd.get_served(), read_only=True))
    assert _post(base, endpoint, body)[0] == 409
    assert not (root / research_records.path_for(REPORT)).exists()


def test_task_input_context_save_clear_and_missing_reference_recovery(writing_server):
    base, root, _ = writing_server
    _report(root)
    task = {'id': 'compare', 'text': 'Compare again', 'context': {'report': REPORT}}
    endpoint = '/api/workspace/tasks'
    status, saved = _post(base, endpoint, {'project': str(root), 'revision': None, 'tasks': [task]})
    assert status == 200, saved
    assert saved['tasks'][0]['context'] == {'report': REPORT}
    (root / REPORT).unlink()
    assert _get(base, '/api/workspace')[1]['tasks'][0]['context'] == {'report': REPORT}
    task['text'] = 'Keep the earlier input reference'
    status, saved = _post(base, endpoint, {'project': str(root), 'revision': saved['tasks_revision'], 'tasks': [task]})
    assert status == 200, saved
    task['context'] = None
    status, saved = _post(base, endpoint, {'project': str(root), 'revision': saved['tasks_revision'], 'tasks': [task]})
    assert status == 200 and 'context' not in saved['tasks'][0]
    task['context'] = {'report': REPORT}
    assert _post(base, endpoint, {'project': str(root), 'revision': saved['tasks_revision'], 'tasks': [task]})[0] == 409


def test_member_task_context_and_research_record_submit_through_existing_proposal(tmp_path, monkeypatch):
    team = Team(tmp_path, monkeypatch)
    with team.member():
        _report(team.work)
        with _serve(team.work) as base:
            status, saved = _post(base, '/api/workspace/research-record', {
                'project': str(team.work), 'report': REPORT, 'revision': None,
                'context': {'question': 'Question for PI', 'attempt_draft': {'description': 'Candidate'}}})
            assert status == 200, saved
            status, tasks = _post(base, '/api/workspace/tasks', {'project': str(team.work), 'revision': None,
                'tasks': [{'id': 'followup', 'text': 'Follow up', 'context': {'report': REPORT}}]})
            assert status == 200 and REPORT in tasks['changed_task_reports']
        draft = proposals.make_proposal(team.work, 'files', 'Research handoff',
                                         only=[workspace.TASKS_PATH, *tasks['changed_task_reports']])
        stored = proposals.write_proposal(team.work, draft)
    with team.pi():
        assert not (team.official / REPORT).exists()
        full = proposals.open_for(team.official, stored.id)
        assert full['research_records'][0]['context']['question'] == 'Question for PI'
        assert full['changes']['attempt'] is None
        proposals.approve(team.official, stored.id)
        assert workspace.read_tasks(team.official)[0][0]['context'] == {'report': REPORT}
        assert research_records.read(team.official, REPORT)['context']['attempt_draft']['description'] == 'Candidate'


def test_research_record_writes_require_the_existing_browser_session(writing_server):
    base, root, httpd = writing_server
    _report(root)
    httpd.session = 'test-session-secret'
    for endpoint in ('/api/workspace/research-record', '/api/workspace/research-record/variable'):
        status, error = _post(base, endpoint, {'project': str(root), 'report': REPORT, 'revision': None, 'context': {}})
        assert status == 403 and error['state'] == 'session_required'
    assert not (root / research_records.path_for(REPORT)).exists()


def test_variable_import_from_manual_record_creates_only_draft(writing_server):
    from researchos import outputs
    from researchos.records import variables
    base, root, _ = writing_server
    _report(root)
    (root / 'distance.toml').write_text('name="Distance"\nmeaning="Distance travelled"\nunit="m"\n')
    entry = {'kind': 'variable', 'path': 'distance.toml', 'title': 'Distance',
             'fingerprint': outputs._fingerprint(root, 'distance.toml'), 'report': REPORT}
    (root / outputs.catalog_path(REPORT)).write_text(json.dumps({'version': 1, 'items': [entry]}))
    status, imported = _post(base, '/api/workspace/research-record/variable', {
        'project': str(root), 'report': REPORT, 'path': 'distance.toml', 'id': 'distance'})
    assert status == 200, imported
    card = variables.open_card(root, 'distance')
    assert card.draft == 1 and card.in_use is None
    seen = _get(base, '/api/workspace/research-record?report=' + REPORT)[1]
    assert seen['variable_drafts'][0]['variable_card'] == 'distance'


@pytest.mark.parametrize('context', [True, {}, {'report': None}, {'report': '../outside.md'},
                                      {'report': REPORT, 'verdict': 'accepted'}])
def test_task_context_rejects_malformed_refs_without_disk_writes(tmp_path, context):
    # Reading a task list checks shape only; a valid missing reference remains readable.
    with pytest.raises(workspace.WorkspaceError):
        workspace._tasks([{'id': 'x', 'text': 'Try', 'context': context}])
    assert workspace._tasks([{'id': 'x', 'text': 'Try', 'context': {'report': REPORT}}])[0]['context']['report'] == REPORT


def test_named_manual_result_can_be_task_input_and_team_proposal(tmp_path, monkeypatch):
    team = Team(tmp_path, monkeypatch)
    report = 'research/results/Interview findings.md'
    with team.member():
        (team.work / report).parent.mkdir(parents=True, exist_ok=True)
        (team.work / report).write_text('# Interview findings\n')
        with _serve(team.work):  # register this member's working copy as the open project
            research_records.save(team.work, {'report': report, 'revision': None, 'context': {'finding': 'Recurring theme'}})
            tasks = workspace.save_tasks(team.work, {'revision': None,
                'tasks': [{'id': 'followup', 'text': 'Check interviews', 'context': {'report': report}}]})
        assert report in tasks['changed_task_reports']
        draft = proposals.make_proposal(team.work, 'files', 'Review interviews',
                                         only=[workspace.TASKS_PATH, report])
        assert research_records.path_for(report) in {f['path'] for f in draft.data['file']}
        stored = proposals.write_proposal(team.work, draft)
    with team.pi():
        full = proposals.open_for(team.official, stored.id)
        assert full['research_records'][0]['context']['finding'] == 'Recurring theme'
        proposals.approve(team.official, stored.id)
        assert workspace.read_tasks(team.official)[0][0]['context']['report'] == report


def test_native_file_backlinks_read_through_workspace_api(writing_server):
    base, root, _ = writing_server
    _report(root)
    (root / 'sources.bib').write_text('@article{x,title={Source}}')
    research_records.save(root, {'report': REPORT, 'revision': None,
                                'context': {'references': ['sources.bib'], 'purposes': {'sources.bib': 'Background'}}})
    status, got = _get(base, '/api/workspace/research-records?path=sources.bib')
    assert status == 200 and len(got['records']) == 1
    assert got['records'][0]['report'] == REPORT
    assert _get(base, '/api/workspace/research-records?path=unlinked.csv')[1]['records'] == []

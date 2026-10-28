"""Research context travels through the existing team file review."""
import json

import pytest
from test_v9_language import needs_node

from researchos import proposals
from test_v8_phase3 import Team, CARD, _readers
from researchos.records import variables as V

REPORT = 'research/results/' + 'a' * 32 + '.md'
CONTEXT = REPORT.removesuffix('.md') + '.context.json'


def _write_record(root):
    (root / REPORT).parent.mkdir(parents=True, exist_ok=True)
    (root / REPORT).write_text('# Findings\n')
    (root / 'analysis.csv').write_text('x,y\n1,2\n')
    context = {'question': 'Does x predict y?', 'method': 'Compare observations',
               'references': ['analysis.csv'],
               'attempt_draft': {'description': 'Try a larger sample', 'result': '', 'parents': []}}
    (root / CONTEXT).write_text(json.dumps({'version': 1, 'context': context}))
    return context


def test_result_proposal_bundles_context_and_explicit_materials_without_creating_attempts(tmp_path, monkeypatch):
    team = Team(tmp_path, monkeypatch)
    original_table = (team.official / 'map.md').read_bytes()
    with team.pi():
        before = _readers(team.official)
    with team.member():
        context = _write_record(team.work)
        draft = proposals.make_proposal(team.work, 'files', 'Review this finding', only=[REPORT])
        assert {f['path'] for f in draft.data['file']} == {REPORT, CONTEXT, 'analysis.csv'}
        assert 'attempt' not in draft.data and 'variable' not in draft.data
        stored = proposals.write_proposal(team.work, draft)
    with team.pi():
        detail = proposals.open_for(team.official, stored.id)
        assert detail['research_records'][0]['context']['question'] == context['question']
        assert detail['research_records'][0]['context']['attempt_draft']['description'] == 'Try a larger sample'
        assert detail['changes']['attempt'] is None
        assert (team.official / 'map.md').read_bytes() == original_table
        assert not (team.official / REPORT).exists()
        assert _readers(team.official) == before
        proposals.approve(team.official, stored.id)
        assert (team.official / CONTEXT).is_file()
        assert (team.official / 'map.md').read_bytes() == original_table


def test_context_only_selection_includes_result_and_preserves_file_conflicts(tmp_path, monkeypatch):
    team = Team(tmp_path, monkeypatch)
    with team.member():
        _write_record(team.work)
        draft = proposals.make_proposal(team.work, 'files', 'Research context', only=[CONTEXT])
        assert {f['path'] for f in draft.data['file']} == {REPORT, CONTEXT, 'analysis.csv'}
        stored = proposals.write_proposal(team.work, draft)
    with team.pi():
        (team.official / 'analysis.csv').write_text('independent PI work\n')
        detail = proposals.open_for(team.official, stored.id)
        assert not detail['changes']['approvable']
        assert [c['path'] for c in detail['changes']['conflicts']] == ['analysis.csv']


def test_linked_variable_draft_keeps_its_separate_native_review(tmp_path, monkeypatch):
    team = Team(tmp_path, monkeypatch)
    with team.member():
        _write_record(team.work)
        card = team.work / '.researchos/variables/topicshift'
        card.mkdir(parents=True)
        (card / 'v1.toml').write_text(CARD)
        context = json.loads((team.work / CONTEXT).read_text())
        context['context']['variables'] = [{'id': 'topicshift', 'version': 1}]
        (team.work / CONTEXT).write_text(json.dumps(context))
        draft = proposals.make_proposal(team.work, 'files', 'Review research', only=[REPORT])
        assert not any(f['path'].startswith('.researchos/variables/') for f in draft.data['file'])
        research = proposals.write_proposal(team.work, draft)
        variable = proposals.write_proposal(team.work, proposals.make_proposal(
            team.work, 'variable_version', 'Review definition', card='topicshift', attested='unknown'))
    with team.pi():
        proposals.approve(team.official, research.id)
        assert not V.find_card_dirs(team.official, 'topicshift')
        assert proposals.open_for(team.official, variable.id)['changes']['variable']['state'] == 'apply'
        proposals.approve(team.official, variable.id)
        assert V.open_card(team.official, 'topicshift').in_use == 1


def test_unchanged_context_is_carried_with_changed_result(tmp_path, monkeypatch):
    team = Team(tmp_path, monkeypatch)
    with team.member():
        _write_record(team.work)
        files = proposals.manifest(team.work)
        proposals.write_base(team.pid, team.work, files,
                             texts={rel: team.work / rel for rel in files})
        (team.work / REPORT).write_text('# Revised findings\n')
        draft = proposals.make_proposal(team.work, 'files', 'Updated result', only=[REPORT])
        assert {f['path'] for f in draft.data['file']} == {REPORT, CONTEXT}
        stored = proposals.write_proposal(team.work, draft)
        detail = proposals.open_for(team.work, stored.id)
        assert detail['research_records'][0]['context']['method'] == 'Compare observations'


@needs_node
def test_pi_handoff_shows_research_summary_and_keeps_candidates_explicitly_draft():
    from test_v10_desk_proposals import _team_case, _run_team, FROM, A16
    case = _team_case('pi', 'en')
    page = case['get']['/api/proposals/p1' + FROM]
    page['research_records'] = [{'path': REPORT, 'context': {
        'question': 'Does x predict y?', 'method': 'Compare observations', 'finding': 'The pattern repeats',
        'limits': 'Small sample', 'next_step': 'Collect more',
        'variables': [{'id': 'topicshift', 'version': 2}],
        'attempt_draft': {'description': 'Try larger sample', 'result': '', 'parents': []}},
        'variable_versions': [{'id': 'topicshift', 'version': 2, 'current': 1, 'draft': None, 'available': False}]}]
    out = _run_team(case, '''
      await window.RODesk.openAttempt(%s);
      button(panelPart("panel-body"), "View").click(); await settle(); await settle();
    ''' % json.dumps(A16), lang='en')
    shown = '\n'.join(out['lines'])
    assert 'Does x predict y?' in shown and 'Compare observations' in shown
    assert 'Try larger sample' in shown and 'Attempt draft' in shown
    assert 'topicshift v2' in shown
    assert 'still needs to be submitted separately' in shown


def test_unchanged_result_and_context_do_not_create_empty_proposals(tmp_path, monkeypatch):
    team = Team(tmp_path, monkeypatch)
    with team.member():
        _write_record(team.work)
        files = proposals.manifest(team.work)
        proposals.write_base(team.pid, team.work, files,
                             texts={rel: team.work / rel for rel in files})
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.make_proposal(team.work, 'files', 'No changes', only=[REPORT])
        assert refused.value.code == 'nothing'


def test_unselected_registered_output_stays_outside_required_research_bundle(tmp_path, monkeypatch):
    from researchos import outputs
    team = Team(tmp_path, monkeypatch)
    with team.member():
        _write_record(team.work)
        (team.work / 'unused.csv').write_text('unused\n')
        item = {'kind': 'data', 'path': 'unused.csv', 'title': 'Unused output',
                'fingerprint': outputs._fingerprint(team.work, 'unused.csv'), 'report': REPORT}
        (team.work / outputs.catalog_path(REPORT)).write_text(json.dumps({'version': 1, 'items': [item]}))
        draft = proposals.make_proposal(team.work, 'files', 'Research only', only=[REPORT], leave=['unused.csv'])
        paths = {f['path'] for f in draft.data['file']}
        assert paths == {REPORT, CONTEXT, 'analysis.csv'}
        candidates = {f['path']: f['chosen'] for f in draft.preview['candidates']}
        assert candidates[CONTEXT] and candidates['analysis.csv'] and not candidates['unused.csv']


def test_context_conflict_refuses_entire_research_bundle_before_any_write(tmp_path, monkeypatch):
    team = Team(tmp_path, monkeypatch)
    with team.member():
        _write_record(team.work)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, 'files', 'Review', only=[REPORT]))
    with team.pi():
        (team.official / CONTEXT).parent.mkdir(parents=True, exist_ok=True)
        (team.official / CONTEXT).write_text('Independent PI context\n')
        with pytest.raises(proposals.ProposalRefused) as refused:
            proposals.approve(team.official, stored.id)
        assert refused.value.code == 'not_approvable'
        assert not (team.official / REPORT).exists()
        assert not (team.official / 'analysis.csv').exists()
        assert (team.official / CONTEXT).read_text() == 'Independent PI context\n'


def test_interrupted_research_bundle_approval_resumes_until_report_and_context_are_together(tmp_path, monkeypatch):
    team = Team(tmp_path, monkeypatch)
    with team.member():
        _write_record(team.work)
        stored = proposals.write_proposal(team.work, proposals.make_proposal(team.work, 'files', 'Review', only=[REPORT]))
    with team.pi():
        real = proposals._place_file
        placed = []
        def interrupted(root, proposal, change, tmp):
            if placed:
                raise OSError('Interrupted after first file placement')
            real(root, proposal, change, tmp)
            placed.append(change['path'])
        monkeypatch.setattr(proposals, '_place_file', interrupted)
        with pytest.raises(OSError):
            proposals.approve(team.official, stored.id, rescan=False)
        assert proposals.what_changes(stored, team.official)['resuming']
        monkeypatch.setattr(proposals, '_place_file', real)
        proposals.approve(team.official, stored.id)
        assert (team.official / REPORT).is_file() and (team.official / CONTEXT).is_file()
        assert (team.official / 'analysis.csv').read_text() == 'x,y\n1,2\n'


def test_unavailable_linked_variable_does_not_block_research_file_proposal(tmp_path, monkeypatch):
    team = Team(tmp_path, monkeypatch)
    with team.member():
        _write_record(team.work)
        context = json.loads((team.work / CONTEXT).read_text())
        context['context']['variables'] = [{'id': 'removed-card', 'version': 2}]
        (team.work / CONTEXT).write_text(json.dumps(context))
        draft = proposals.make_proposal(team.work, 'files', 'Review with unavailable definition', only=[REPORT])
        assert {f['path'] for f in draft.data['file']} == {REPORT, CONTEXT, 'analysis.csv'}
        stored = proposals.write_proposal(team.work, draft)
    with team.pi():
        page = proposals.open_for(team.official, stored.id)
        assert page['changes']['approvable']
        ref = page['research_records'][0]['variable_versions'][0]
        assert ref['id'] == 'removed-card' and ref['version'] == 2 and not ref['available']
        assert page['changes']['variable'] is None

"""Research context extends result records without taking scientific decisions."""
import json

import pytest

from researchos import project, research_records as records, workspace


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / 'project'
    folder.mkdir()
    project.init_project(folder)
    report = folder / 'research/results' / ('a' * 32 + '.md')
    report.parent.mkdir(parents=True)
    report.write_text('# A human result\n')
    return folder


def report():
    return 'research/results/' + 'a' * 32 + '.md'


def test_optional_context_roundtrip_and_conflict(root):
    original = records.read(root, report())
    assert original['revision'] is None
    assert not (root / original['path']).exists()
    got = records.save(root, {'report': report(), 'revision': None,
                             'context': {'question': '为什么？', 'finding': '待研究者判断'}})
    assert got['context']['question'] == '为什么？'
    assert got['revision']
    assert (root / report()).read_text() == '# A human result\n'
    with pytest.raises(workspace.WorkspaceError, match='changed'):
        records.save(root, {'report': report(), 'revision': None, 'context': {}})
    assert records.read(root, report())['revision'] == got['revision']


@pytest.mark.parametrize('context', [
    {'attempts': ['attempt:other.md#1']}, {'variables': ['foreign-card']},
    {'references': ['../outside.pdf']}, {'references': ['missing.pdf']},
    {'attempt_draft': {'description': 'Try', 'verdict': 'adopted'}},
    {'confirmed': True}, {'question': 12}, {'attempts': 'one'},
])
def test_invalid_and_foreign_context_never_written(root, context):
    with pytest.raises(workspace.WorkspaceError):
        records.save(root, {'report': report(), 'revision': None, 'context': context})
    assert not (root / records.path_for(report())).exists()


def test_project_file_reference_and_dependencies(root):
    (root / 'sources.bib').write_text('@article{x,title={A}}')
    got = records.save(root, {'report': report(), 'revision': None,
                             'context': {'references': ['sources.bib'],
                                         'attempt_draft': {'description': 'Compare samples', 'result': 'Pending'}}})
    assert got['context']['attempt_draft']['result'] == 'Pending'
    assert records.dependencies(root, report()) == {records.path_for(report()), 'sources.bib'}
    assert not (root / 'attempts.md').exists()


def test_damaged_sidecar_is_not_overwritten(root):
    target = root / records.path_for(report())
    target.write_text('{bad')
    with pytest.raises(workspace.WorkspaceError, match='JSON'):
        records.save(root, {'report': report(), 'revision': workspace._revision(target.read_bytes()), 'context': {}})
    assert target.read_text() == '{bad'


def test_import_requires_draft_registered_to_this_record(root):
    with pytest.raises(workspace.WorkspaceError, match='not registered'):
        records.import_variable(root, {'report': report(), 'path': 'variables/draft.toml', 'id': 'distance'})


def test_unknown_schema_and_symlink_reference_refused(root, tmp_path):
    with pytest.raises(workspace.WorkspaceError):
        records.parse(json.dumps({'version': 2, 'context': {}}).encode())
    outside = tmp_path / 'outside.pdf'
    outside.write_text('private')
    (root / 'linked.pdf').symlink_to(outside)
    with pytest.raises(workspace.WorkspaceError):
        records.save(root, {'report': report(), 'revision': None, 'context': {'references': ['linked.pdf']}})


def test_import_updates_only_this_reports_catalog_without_confirmation(root):
    from researchos import outputs
    from researchos.records import variables

    source = root / 'distance.toml'
    source.write_text('name="Distance"\nmeaning="Distance travelled"\nunit="m"\n')
    other = 'research/results/' + 'b' * 32 + '.md'
    (root / other).write_text('# Other')
    for owner in (report(), other):
        entry = {'kind': 'variable', 'path': 'distance.toml', 'title': 'Distance',
                 'fingerprint': outputs._fingerprint(root, 'distance.toml'), 'report': owner}
        (root / outputs.catalog_path(owner)).write_text(json.dumps({'version': 1, 'items': [entry]}))
    result = records.import_variable(root, {'report': report(), 'path': 'distance.toml', 'id': 'distance'})
    assert result['id'] == 'distance'
    card = variables.open_card(root, 'distance')
    assert card.draft == 1 and card.in_use is None
    assert records.read(root, report())['variable_drafts'][0]['variable_card'] == 'distance'
    assert 'variable_card' not in records.read(root, other)['variable_drafts'][0]


def test_variable_versions_are_pinned_and_dependencies_keep_that_definition(root):
    from researchos.records import cards
    path = cards.new_card(root, 'distance')
    got = records.save(root, {'report': report(), 'revision': None,
                             'context': {'variables': ['distance']}})
    assert got['context']['variables'] == [{'id': 'distance', 'version': 1}]
    assert path.relative_to(root).as_posix() in records.dependencies(root, report())
    assert got['choices']['variables'][0]['version'] == 1
    with pytest.raises(workspace.WorkspaceError, match='version'):
        records.validate_context(root, {'variables': [{'id': 'distance', 'version': 99}]})


def test_material_purposes_and_missing_link_notice_are_read_only(root):
    (root / 'plot.svg').write_text('<svg/>')
    got = records.save(root, {'report': report(), 'revision': None,
                             'context': {'materials': ['plot.svg'], 'purposes': {'plot.svg': '比较两组差异'}}})
    (root / 'plot.svg').unlink()
    read = records.read(root, report())
    assert read['notices'][0]['kind'] == 'unavailable'
    assert read['revision'] == got['revision']
    with pytest.raises(workspace.WorkspaceError, match='purposes'):
        records.validate_context(root, {'purposes': {'other.pdf': 'not linked'}})


def test_agent_context_write_obeys_revision_and_never_creates_attempt(root):
    got = records.save_agent(root, report(), {'next_step': '请研究者判断'}, {'source': 'agent', 'who': 'agent:claude-code:sonnet', 'note': 'run-test'})
    assert got['context']['next_step'] == '请研究者判断'
    with pytest.raises(workspace.WorkspaceError, match='changed'):
        records.save_agent(root, report(), {}, {'source': 'agent', 'who': 'agent:claude-code:sonnet', 'note': 'run-test'})


def test_named_manual_result_uses_same_context_without_task_catalog(root):
    manual = 'research/results/样本比较.md'
    (root / manual).write_text('# Sample comparison\n')
    got = records.save(root, {'report': manual, 'revision': None,
                             'context': {'question': 'Does the sample matter?'}})
    assert got['context']['question'] == 'Does the sample matter?'
    assert got['outputs'] == []
    assert records.dependencies(root, manual) == {'research/results/样本比较.context.json'}


def test_reading_relations_does_not_hash_materials(root, monkeypatch):
    from researchos import outputs

    (root / 'observations.csv').write_text('value\n1\n')
    def unexpected(*args, **kwargs):
        pytest.fail('A file relation must not read and hash the material')
    monkeypatch.setattr(outputs, '_fingerprint', unexpected)
    got = records.save(root, {'report': report(), 'revision': None,
                             'context': {'materials': ['observations.csv']}})
    assert records.read(root, report())['revision'] == got['revision']


def test_damaged_output_catalog_does_not_hide_or_block_authored_context(root):
    from researchos import outputs

    catalog = root / outputs.catalog_path(report())
    catalog.write_text('{invalid catalog')
    first = records.read(root, report())
    assert first['outputs'] == []
    assert first['notices'][0]['kind'] == 'outputs_unavailable'
    saved = records.save(root, {'report': report(), 'revision': None,
                               'context': {'finding': 'A recorded observation'}})
    assert saved['context']['finding'] == 'A recorded observation'
    assert records.read(root, report())['revision'] == saved['revision']
    assert catalog.read_text() == '{invalid catalog'

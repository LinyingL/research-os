"""Read-only research associations remain explicit, bounded and file-specific."""
import json

from researchos import project, research_records as records
from researchos.webapp import literature_api
from test_v7_citations import _conn, _scan


def make_root(tmp_path):
    root = tmp_path / 'fictional-project'
    root.mkdir()
    project.init_project(root)
    (root / 'research/results').mkdir(parents=True)
    return root


def record(root, name, context):
    report = f'research/results/{name}.md'
    (root / report).write_text('# Sample comparison\nFictional work only.')
    (root / records.path_for(report)).write_text(json.dumps({'version': 1, 'context': context}))
    return report


def test_backlinks_match_only_explicit_files_and_pinned_variables(tmp_path):
    root = make_root(tmp_path)
    report = record(root, '手工结果', {'question': 'Which sample?', 'materials': ['figures/sample.svg'],
        'purposes': {'figures/sample.svg': 'Compares two fictional samples'}, 'variables': [{'id': 'sample', 'version': 2}]})
    record(root, 'unrelated', {'question': 'Other question'})
    got = records.backlinks(root, paths=['figures/sample.svg'])
    assert [item['report'] for item in got] == [report]
    assert got[0]['matches'] == [{'path': 'figures/sample.svg', 'kind': 'materials', 'purpose': 'Compares two fictional samples'}]
    assert records.backlinks(root, variable_id='sample')[0]['variables'] == [{'id': 'sample', 'version': 2}]
    assert records.backlinks(root, paths=['missing.svg']) == []
    assert records.backlinks(root) == []


def test_backlinks_ignore_nested_corrupt_cloud_and_symlinked_context(tmp_path):
    root = make_root(tmp_path)
    record(root, 'valid', {'references': ['sources.bib']})
    nested = root / 'research/results/nested'
    nested.mkdir()
    (nested / 'hidden.context.json').write_text(json.dumps({'version': 1, 'context': {'references': ['sources.bib']}}))
    (root / 'research/results/broken.context.json').write_text('{bad')
    outer = tmp_path / 'outside.context.json'
    outer.write_text(json.dumps({'version': 1, 'context': {'references': ['sources.bib']}}))
    (root / 'research/results/linked.context.json').symlink_to(outer)
    assert len(records.backlinks(root, paths=['sources.bib'])) == 1
    (root / 'research/results/.valid.context.json.icloud').touch()
    assert records.backlinks(root, paths=['sources.bib']) == []
    research = root / 'research'
    research.rename(root / 'former-research')
    research.symlink_to(root / 'former-research', target_is_directory=True)
    assert records.backlinks(root, paths=['sources.bib']) == []


def test_literature_backlinks_stay_on_export_files_not_individual_dois(tmp_path):
    root = make_root(tmp_path)
    (root / 'sources.bib').write_text('@article{x,title={Fictional A},doi={10.1234/a}}\n@article{y,title={Fictional B},doi={10.1234/b}}')
    record(root, 'literature-check', {'references': ['sources.bib'], 'purposes': {'sources.bib': 'Background candidates only'}})
    _scan(root)
    conn = _conn(root)
    try:
        payload = literature_api.citations_payload(conn, root)
    finally:
        conn.close()
    assert payload['research_records_by_file']['sources.bib'][0]['matches'][0]['purpose'] == 'Background candidates only'
    assert len(payload['library']['items']) == 2
    assert all('research_records' not in item for item in payload['library']['items'])

"""Research handoffs survive real completion, guarded execution and undo."""
import json
import pytest

from researchos import mcp_server, outputs, research_records, workspace
from researchos.agents import brief, guard, research_context, runs, snapshot
from researchos.records import cards
from test_agents_mcp import _run, _tasks, _task, root, pid
from test_agents_manager import scene, init_step, finish_step


def background(root):
    rel = 'research/results/' + 'a' * 32 + '.md'
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('# Prior result\n')
    (root / 'source.txt').write_text('Evidence')
    research_records.save(root, {'report': rel, 'revision': None,
                                'context': {'question': 'Compare groups', 'method': 'Match samples',
                                            'finding': 'OLD FINDING', 'materials': ['source.txt']}})
    return rel


def test_completion_inherits_links_and_never_prior_finding(root, pid):
    rel = background(root)
    _tasks(root)
    run_id = _run(root, pid)
    frozen = research_context.freeze(root, {'context': {'report': rel}})
    runs.update_run(pid, run_id, research_handoff=frozen)
    answer = mcp_server.agent_complete_task(root, pid, run_id, 'New work.')
    assert 'marked the task done' in answer
    target = _task(root)['report']
    current = research_records.read(root, target)
    assert current['context']['materials'] == ['source.txt']
    assert current['context']['question'] == 'Compare groups'
    assert current['context']['finding'] == ''
    assert current['context']['method'] == ''
    signal = runs.read_signals(pid, run_id)[0]
    assert signal['context_sha'] == current['revision']
    assert signal['context_path'] in signal['paths']
    assert 'OLD FINDING' in brief.research_handoff(frozen, lang='en')
    assert 'never findings of this run' in brief.research_handoff(frozen, lang='en')


def test_invalid_completion_context_writes_nothing(root, pid):
    _tasks(root)
    run_id = _run(root, pid)
    before = (root / workspace.TASKS_PATH).read_bytes()
    answer = mcp_server.agent_complete_task(root, pid, run_id, 'Work', research_context={'materials': ['missing.pdf']})
    assert answer.startswith('Not written:')
    assert (root / workspace.TASKS_PATH).read_bytes() == before
    directory = root / 'research/results'
    assert not directory.exists() or not list(directory.glob('*.md'))
    assert runs.read_signals(pid, run_id) == []


def test_sidecar_conflict_precedes_report_and_task_writes(root, pid):
    rel = background(root)
    _tasks(root, report=rel)
    run_id = _run(root, pid)
    runs.update_run(pid, run_id, research_handoff=research_context.freeze(root, {'report': rel}))
    current = research_records.read(root, rel)
    research_records.save(root, {'report': rel, 'revision': current['revision'], 'context': {'finding': 'HUMAN EDIT'}})
    before = {name: (root / name).read_bytes() for name in (rel, workspace.TASKS_PATH, research_records.path_for(rel))}
    answer = mcp_server.agent_complete_task(root, pid, run_id, 'Overwrite', research_context={'finding': 'NEW'})
    assert answer.startswith('Not written:') and 'changed' in answer
    assert all((root / name).read_bytes() == data for name, data in before.items())


def test_handoff_pins_variable_versions_and_input_metadata(root):
    rel = background(root)
    cards.new_card(root, 'distance')
    current = research_records.read(root, rel)
    research_records.save(root, {'report': rel, 'revision': current['revision'],
                                'context': {'variables': ['distance'], 'materials': ['source.txt']}})
    frozen = research_context.freeze(root, {'context': {'report': rel}})
    assert frozen['source']['context']['variables'] == [{'id': 'distance', 'version': 1}]
    original_size = frozen['source']['inputs'][0]['size']
    (root / 'source.txt').write_text('Changed later')
    assert frozen['source']['inputs'][0]['size'] == original_size
    assert original_size != (root / 'source.txt').stat().st_size


def test_declarations_become_materials_but_variable_drafts_are_not_versions(root, pid):
    _tasks(root)
    run_id = _run(root, pid)
    locations = outputs.folders(root)['folders']
    path = root / locations['variable'] / 'draft.toml'
    path.parent.mkdir(parents=True)
    path.write_text('name="Distance"\nmeaning="Travelled distance"\nunit="m"\n')
    answer = mcp_server.agent_complete_task(root, pid, run_id, 'Definition drafted',
        outputs=[{'kind': 'variable', 'path': path.relative_to(root).as_posix(), 'title': 'Distance'}],
        research_context={'finding': 'Draft only'})
    assert 'marked the task done' in answer
    result = research_records.read(root, _task(root)['report'])
    assert result['context']['variables'] == []
    assert result['context']['materials'] == [path.relative_to(root).as_posix()]
    assert result['variable_drafts'][0]['path'] == path.relative_to(root).as_posix()


@pytest.mark.parametrize('name', ['ab' * 16, '手工结果'])
def test_context_sidecars_protected_in_file_shell_and_after_run(root, name):
    rel = f'research/results/{name}.context.json'
    prot = snapshot.protected(root, {'files': {}})
    config = {'root': str(root), 'dirs': prot['dirs'], 'files': prot['files'], 'engine_port': 4321}
    assert guard.check_tool(config, 'Write', {'file_path': rel, 'content': '{}'})
    assert guard.check_tool(config, 'Bash', {'command': f'echo bad > "{rel}"'})
    (root / 'research/results').mkdir(parents=True, exist_ok=True)
    assert guard.check_tool(config, 'Bash', {'command': f'cp /tmp/{name}.context.json research/results/'})
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{}')
    changed = {'created': [rel], 'modified': [], 'deleted': []}
    assert snapshot.violations(changed, prot, git_before=None, git_after=None, allowed={})[0]['reason'] == 'record_changed'
    sha = snapshot.fingerprint(root, rel)['sha256']
    assert snapshot.violations(changed, prot, git_before=None, git_after=None, allowed={rel: {sha}}) == []
    target.write_text('{"tampered": true}')
    assert snapshot.violations(changed, prot, git_before=None, git_after=None, allowed={rel: {sha}})[0]['reason'] == 'record_changed'


@pytest.mark.parametrize('existing_target', [False, True])
def test_real_manager_completion_sidecar_is_trusted_and_undoable(scene, existing_target):
    # Extend only this test's fake executable with a real MCP completion call.
    executable = scene.bins['codex']
    script = executable.read_text()
    script = script.replace('    elif op == "complete":', '''    elif op == "research_complete":
        from researchos import mcp_server
        print(mcp_server.agent_complete_task(Path.cwd(), scenario["project_id"],
              os.environ["RESEARCHOS_AGENT_RUN"], "New research", outputs=[],
              research_context={"finding": "New measured result"}), flush=True)
    elif op == "complete":''')
    executable.write_text(script)
    prior = background(scene.root)
    old_sidecar = (scene.root / research_records.path_for(prior)).read_bytes()
    task = {'id': 't1', 'text': 'Compare', 'owner': 'agent:codex:gpt-5.5', 'context': {'report': prior}}
    if existing_target:
        task['report'] = prior
    scene.write_tasks([task])
    run = scene.go([init_step(), {'op': 'research_complete'}, finish_step()], runner='codex')
    assert run['state'] == 'done', run
    assert run['violations'] == []
    rel = scene.tasks()['t1']['report']
    sidecar = research_records.path_for(rel)
    assert sidecar in run['tool_paths']
    assert research_records.read(scene.root, rel)['context']['finding'] == 'New measured result'
    instruction = (runs.run_dir(scene.pid, run['id']) / 'instruction.txt').read_text()
    assert 'OLD FINDING' in instruction and 'never findings of this run' in instruction
    result = scene.manager.revert(scene.root, scene.pid, run['id'], by=None)
    if existing_target:
        assert sidecar in result['restored']
        assert (scene.root / sidecar).read_bytes() == old_sidecar
    else:
        assert sidecar in result['removed']
        assert not (scene.root / sidecar).exists()
    assert (scene.root / prior).is_file()


def test_missing_background_is_explicit_notice_and_can_still_run(root, pid):
    _tasks(root)
    rel = 'research/results/missing.md'
    frozen = research_context.freeze(root, {'context': {'report': rel}})
    assert frozen['source']['notices'][0]['kind'] == 'missing_context'
    assert rel in brief.research_handoff(frozen, lang='zh')
    run_id = _run(root, pid)
    runs.update_run(pid, run_id, research_handoff=frozen)
    answer = mcp_server.agent_complete_task(root, pid, run_id, 'New independent work', research_context={'finding': 'New'})
    assert 'marked the task done' in answer


def test_explicit_association_list_can_correct_a_missing_inherited_material(root, pid):
    rel = background(root)
    _tasks(root)
    frozen = research_context.freeze(root, {'context': {'report': rel}})
    (root / 'source.txt').unlink()
    run_id = _run(root, pid)
    runs.update_run(pid, run_id, research_handoff=frozen)
    refused = mcp_server.agent_complete_task(root, pid, run_id, 'New')
    assert refused.startswith('Not written:')
    assert not _task(root)['done']
    answer = mcp_server.agent_complete_task(root, pid, run_id, 'New', research_context={'materials': [], 'finding': 'New'})
    assert 'marked the task done' in answer
    assert research_records.read(root, _task(root)['report'])['context']['materials'] == []


def test_oversized_merged_sidecar_is_refused_before_any_result_write(root, pid):
    _tasks(root)
    run_id = _run(root, pid)
    material_paths = []
    for number in range(200):
        path = f'input-{number}.txt'
        (root / path).write_text('input')
        material_paths.append(path)
    supplied = {field: 'x' * 12000 for field in ('question', 'method', 'finding', 'limits', 'next_step')}
    supplied.update(materials=material_paths, purposes={path: 'x' * 1000 for path in material_paths})
    before = (root / workspace.TASKS_PATH).read_bytes()
    answer = mcp_server.agent_complete_task(root, pid, run_id, 'Work', research_context=supplied)
    assert answer.startswith('Not written:') and 'larger' in answer
    assert (root / workspace.TASKS_PATH).read_bytes() == before
    directory = root / 'research/results'
    assert not directory.exists() or not list(directory.iterdir())


def test_sent_back_record_keeps_links_but_clears_earlier_scientific_claims(root, pid):
    rel = background(root)
    _tasks(root, report=rel)
    run_id = _run(root, pid)
    frozen = research_context.freeze(root, {'report': rel})
    runs.update_run(pid, run_id, research_handoff=frozen)
    assert frozen['source']['report'] == rel
    answer = mcp_server.agent_complete_task(root, pid, run_id, 'Reran the check')
    assert 'marked the task done' in answer
    current = research_records.read(root, rel)['context']
    assert current['materials'] == ['source.txt']
    assert current['question'] == 'Compare groups'
    assert current['method'] == '' and current['finding'] == ''


def test_running_handoff_keeps_source_context_revision_even_if_background_changes(root, pid):
    rel = background(root)
    _tasks(root)
    run_id = _run(root, pid)
    frozen = research_context.freeze(root, {'context': {'report': rel}})
    runs.update_run(pid, run_id, research_handoff=frozen)
    current = research_records.read(root, rel)
    research_records.save(root, {'report': rel, 'revision': current['revision'],
                                'context': {'question': 'Changed background question'}})
    answer = mcp_server.agent_complete_task(root, pid, run_id, 'New work', research_context={'finding': 'New'})
    assert 'marked the task done' in answer
    assert research_records.read(root, _task(root)['report'])['context']['question'] == 'Compare groups'
    assert runs.load_run(pid, run_id)['research_handoff']['source']['revision'] == current['revision']


def test_manual_result_without_sidecar_includes_frozen_body_and_report_hash(root):
    rel = 'research/results/人工结果.md'
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_text('# 人工结果\n\n真实背景内容：样本共有42人。\n')
    first = research_context.freeze(root, {'context': {'report': rel}})
    source = first['source']
    assert source['revision'] is None
    assert source['report_sha'] == workspace._revision(target.read_bytes())
    assert '真实背景内容' in source['report_content']
    assert '真实背景内容' in brief.research_handoff(first, lang='zh')
    old_hash = source['report_sha']
    target.write_text('# 修订结果\n\n样本复核为43人。\n')
    assert first['source']['report_sha'] == old_hash
    assert '42人' in first['source']['report_content']
    second = research_context.freeze(root, {'context': {'report': rel}})
    assert second['source']['report_sha'] != old_hash
    assert '43人' in second['source']['report_content']


def test_large_background_is_bounded_with_explicit_full_report_instruction(root):
    rel = background(root)
    (root / rel).write_text('# Background\n' + 'Evidence ' * 4000)
    handoff = research_context.freeze(root, {'context': {'report': rel}})
    assert len(handoff['source']['report_content']) == research_context.REPORT_TEXT_LIMIT
    assert handoff['source']['report_truncated'] is True
    assert 'truncated at 24,000 characters' in brief.research_handoff(handoff, lang='en')


def test_pinned_variable_handoff_names_its_specific_definition_file(root):
    rel = background(root)
    definition = cards.new_card(root, 'distance')
    current = research_records.read(root, rel)
    research_records.save(root, {'report': rel, 'revision': current['revision'],
                                'context': {'variables': [{'id': 'distance', 'version': 1}]}})
    handoff = research_context.freeze(root, {'context': {'report': rel}})
    pinned = handoff['source']['variable_inputs'][0]
    assert pinned['id'] == 'distance' and pinned['version'] == 1
    assert pinned['path'] == definition.relative_to(root).as_posix()
    text = brief.research_handoff(handoff, lang='en')
    assert pinned['path'] in text
    assert 'Never substitute the current version' in text


def test_agent_save_receipt_uses_written_bytes_without_rereading_live_sidecar(root, monkeypatch):
    rel = background(root)
    revision = research_records.read(root, rel)['revision']
    monkeypatch.setattr(research_records, 'read', lambda *args: (_ for _ in ()).throw(AssertionError('unexpected live read')))
    receipt = research_records.save_agent(root, rel, {'finding': 'Written result'},
        {'source': 'agent', 'who': 'agent:claude-code:sonnet', 'note': 'run'}, revision)
    assert receipt['written_revision'] == workspace._revision((root / receipt['path']).read_bytes())
    assert receipt['context']['finding'] == 'Written result'


def test_completion_signal_does_not_trust_an_external_edit_after_sidecar_write(root, pid, monkeypatch):
    _tasks(root)
    run_id = _run(root, pid)
    original = research_records.save_agent
    receipts = []

    def edited_after_write(*args, **kwargs):
        receipt = original(*args, **kwargs)
        receipts.append(receipt)
        (root / receipt['path']).write_text(json.dumps({'version': 1, 'context': {'finding': 'External edit'}}))
        return receipt

    monkeypatch.setattr(research_records, 'save_agent', edited_after_write)
    answer = mcp_server.agent_complete_task(root, pid, run_id, 'Actual result', research_context={'finding': 'Actual'})
    assert 'marked the task done' in answer
    signal = runs.read_signals(pid, run_id)[0]
    assert signal['context_sha'] == receipts[0]['written_revision']
    assert signal['context_sha'] != workspace._revision((root / signal['context_path']).read_bytes())


def test_definition_snapshot_is_frozen_and_never_requires_shell_guard_bypass(root):
    rel = background(root)
    definition = cards.new_card(root, 'height')
    definition.write_text('name="Height"\nmeaning="Standing height"\nunit="cm"\n')
    current = research_records.read(root, rel)
    research_records.save(root, {'report': rel, 'revision': current['revision'],
                                'context': {'variables': [{'id': 'height', 'version': 1}]}})
    frozen = research_context.freeze(root, {'context': {'report': rel}})
    pinned = frozen['source']['variable_inputs'][0]
    original_sha = workspace._revision(definition.read_bytes())
    assert 'Standing height' in pinned['content']
    assert pinned['content_sha'] == original_sha and pinned['truncated'] is False
    definition.write_text('name="Height"\nmeaning="A later edit"\nunit="m"\n')
    assert pinned['content_sha'] == original_sha and 'Standing height' in pinned['content']
    refreshed = research_context.freeze(root, {'context': {'report': rel}})
    assert 'A later edit' in refreshed['source']['variable_inputs'][0]['content']
    english = brief.research_handoff(frozen, lang='en')
    chinese = brief.research_handoff(frozen, lang='zh')
    assert 'read-only snapshots' in english and 'do not use Bash to read .researchos' in english
    assert 'if your runner provides one' in english and 'ask the researcher' in english
    assert '不要通过 Bash 读取 .researchos' in chinese and '询问研究者' in chinese


def test_definition_snapshots_obey_per_item_and_total_budget(root):
    rel = background(root)
    references = []
    for number in range(4):
        card_id = f'variable-{number}'
        definition = cards.new_card(root, card_id)
        definition.write_text(f'name="Variable {number}"\nmeaning="' + 'x' * 9000 + '"\n')
        references.append({'id': card_id, 'version': 1})
    current = research_records.read(root, rel)
    research_records.save(root, {'report': rel, 'revision': current['revision'], 'context': {'variables': references}})
    frozen = research_context.freeze(root, {'context': {'report': rel}})
    inputs = frozen['source']['variable_inputs']
    assert all(len(item['content']) <= research_context.DEFINITION_TEXT_LIMIT for item in inputs)
    assert sum(len(item['content']) for item in inputs) == research_context.DEFINITION_TOTAL_LIMIT
    assert all(item['truncated'] for item in inputs)
    assert inputs[-1]['content'] == '' and inputs[-1]['content_sha']
    assert 'ask the researcher' in brief.research_handoff(frozen, lang='en')

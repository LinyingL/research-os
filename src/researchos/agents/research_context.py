"""Frozen, explicit research handoffs for one agent run."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from researchos import outputs, research_records, workspace
from researchos.records import variables

REPORT_TEXT_LIMIT = 24000
DEFINITION_TEXT_LIMIT = 8000
DEFINITION_TOTAL_LIMIT = 24000


def freeze(root: Path, task: dict[str, Any]) -> dict[str, Any]:
    """Read once when launching; missing relations are notices, never silently rewritten."""
    result: dict[str, Any] = {'target_revision': None, 'target_report': task.get('report'), 'source': None}
    if task.get('report'):
        raw = workspace._read(root, research_records.path_for(task['report']))
        research_records.parse(raw)
        result['target_revision'] = workspace._revision(raw)
    selected = task.get('context')
    if not isinstance(selected, dict) or not selected.get('report'):
        if result['target_revision'] is None:
            return result
        selected = {'report': task['report']}  # a sent-back task keeps its record associations
    report = selected['report']
    rel = research_records.path_for(report)
    report_raw = workspace._read(root, report)
    if report_raw is None:
        result['source'] = {'report': report, 'path': rel, 'revision': None,
                            'context': research_records.normalize({}), 'outputs': [], 'inputs': [],
                            'report_content': '', 'report_sha': None, 'report_truncated': False,
                            'variable_inputs': [],
                            'notices': [{'kind': 'missing_context',
                                         'message': '作为背景的结果记录已缺失：' + report}]}
        return result
    loaded = research_records.read(root, report)
    record = copy.deepcopy({key: loaded.get(key) for key in
                            ('report', 'path', 'revision', 'context', 'outputs', 'variable_drafts', 'notices')})
    text = workspace._decode(report_raw)
    record.update(report_content=text[:REPORT_TEXT_LIMIT], report_sha=workspace._revision(report_raw),
                  report_truncated=len(text) > REPORT_TEXT_LIMIT)
    variable_inputs = []
    definition_budget = DEFINITION_TOTAL_LIMIT
    for ref in record['context']['variables']:
        definition = {**ref, 'path': None, 'state': 'unavailable', 'content': '',
                      'content_sha': None, 'truncated': False}
        try:
            card = variables.open_card(root, ref['id'])
            if ref['version'] is None:
                ref['version'] = card.in_use or card.draft
            definition['version'] = ref['version']
            view = card.versions.get(ref['version'])
            if view is not None:
                definition.update(path=view.path.relative_to(root).as_posix(), state=view.file_state)
                if view.data is not None:
                    definition_text = view.data.decode('utf-8', errors='replace')
                    kept = min(DEFINITION_TEXT_LIMIT, definition_budget)
                    shown = definition_text[:kept]
                    definition.update(content=shown, content_sha=workspace._revision(view.data),
                                      truncated=len(definition_text) > len(shown))
                    definition_budget -= len(shown)
        except variables.VariableError:
            pass
        variable_inputs.append(definition)
    record['variable_inputs'] = variable_inputs
    inputs = []
    for rel in dict.fromkeys(record['context']['references'] + record['context']['materials']):
        try:
            target = outputs._path(root, rel)
            stamp = target.stat()
            inputs.append({'path': rel, 'state': 'current' if target.is_file() else 'unavailable',
                           'size': stamp.st_size, 'mtime_ns': stamp.st_mtime_ns})
        except (workspace.WorkspaceError, OSError):
            inputs.append({'path': rel, 'state': 'unavailable'})
    record['inputs'] = inputs
    result['source'] = record
    return result


def prepare(root: Path, run: dict[str, Any], supplied: dict[str, Any] | None,
            registered: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Merge background associations, never the old finding, into this run's result."""
    frozen = run.get('research_handoff') or {}
    source = frozen.get('source') or {}
    if supplied is None and not source:
        return None  # old calls retain their exact behavior
    inherited = source.get('context') or {}
    context = research_records.normalize(supplied if supplied is not None else {})
    # Only the question supplies inherited prose. Methods and findings describe
    # this run, and a present list explicitly replaces that association category.
    supplied_fields = supplied if isinstance(supplied, dict) else {}
    if 'question' not in supplied_fields:
        context['question'] = inherited.get('question', '')
    for key in ('attempts', 'variables', 'references', 'materials'):
        if key not in supplied_fields:
            context[key] = copy.deepcopy(inherited.get(key, []))
    linked = set(context['references'] + context['materials'])
    context['purposes'] = {**{path: note for path, note in inherited.get('purposes', {}).items() if path in linked},
                           **context['purposes']}
    # Only actual, validated declarations add default associations. A variable
    # draft is a material; it never becomes an adopted variable version here.
    for item in registered:
        key = 'references' if item['kind'] == 'literature' else 'materials'
        if item['path'] not in context[key]:
            context[key].append(item['path'])
    normalized = research_records.validate_context(root, context)
    # The full merged record can exceed the document limit even when each field
    # is small. Refuse before the completion writes its Markdown or registrations.
    workspace._content(json.dumps({'version': 1, 'context': normalized}, ensure_ascii=False, indent=2) + '\n')
    return normalized

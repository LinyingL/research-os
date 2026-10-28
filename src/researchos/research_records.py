"""Optional research context beside an existing result record.

The Markdown result and output catalog remain the record of the work. This
sidecar adds editable context and explicit links; it makes no scientific decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from researchos import outputs, workspace
from researchos.records import situation, variables

TEXT_FIELDS = ('question', 'method', 'finding', 'limits', 'next_step')
LINK_FIELDS = ('attempts', 'references', 'materials')


def _bad(message: str) -> workspace.WorkspaceError:
    return workspace.WorkspaceError('research_record', message, 409, '研究记录的内容或关联无效，请检查后重试。')


def path_for(report: str) -> str:
    if workspace._kind(report) != 'result':
        raise _bad('Research context must belong to a result record')
    return report.removesuffix('.md') + '.context.json'


def _text(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 12000 or '\0' in value:
        raise _bad('Research context text must contain at most 12,000 characters')
    workspace._content(value)
    return value


def _links(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 200:
        raise _bad('Research context links must be a list of at most 200 entries')
    if any(not isinstance(v, str) or not v or len(v) > 1024 or '\0' in v for v in value):
        raise _bad('Each research context link must be a nonempty identifier or project path')
    return list(dict.fromkeys(value))


def normalize(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - set(TEXT_FIELDS + LINK_FIELDS + ('attempt_draft', 'variables', 'purposes')):
        raise _bad('Unknown research context fields')
    found = {key: _text(value.get(key, '')) for key in TEXT_FIELDS}
    found.update({key: _links(value.get(key, [])) for key in LINK_FIELDS})
    refs = value.get('variables', [])
    if not isinstance(refs, list) or len(refs) > 200:
        raise _bad('Variable references must be a list')
    found['variables'] = []
    for ref in refs:
        if isinstance(ref, str):
            found['variables'].append({'id': ref, 'version': None})
        elif (isinstance(ref, dict) and set(ref) == {'id', 'version'}
              and isinstance(ref['id'], str) and (ref['version'] is None
                  or (type(ref['version']) is int and ref['version'] > 0))):
            found['variables'].append(dict(ref))
        else:
            raise _bad('A variable reference needs a card ID and version number')
    purposes = value.get('purposes', {})
    if not isinstance(purposes, dict) or len(purposes) > 200:
        raise _bad('File purposes must map project paths to short notes')
    found['purposes'] = {}
    for path, note in purposes.items():
        if not isinstance(path, str) or not isinstance(note, str) or len(note) > 1000:
            raise _bad('Each file purpose needs a path and short note')
        found['purposes'][path] = _text(note)
    if 'attempt_draft' in value and value['attempt_draft'] is not None:
        draft = value['attempt_draft']
        if not isinstance(draft, dict) or set(draft) - {'description', 'result', 'parents'}:
            raise _bad('An attempt suggestion contains description, result and parents only')
        found['attempt_draft'] = {'description': _text(draft.get('description', '')),
                                  'result': _text(draft.get('result', '')),
                                  'parents': _links(draft.get('parents', []))}
    return found


def parse(raw: bytes | None) -> dict[str, Any]:
    if raw is None:
        return normalize({})
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _bad('Cannot read research context JSON') from exc
    if not isinstance(value, dict) or set(value) != {'version', 'context'} or type(value.get('version')) is not int or value['version'] != 1:
        raise _bad('Unknown research context version or fields')
    return normalize(value['context'])


def _report(root: Path, report: str) -> None:
    path_for(report)
    if workspace._read(root, report) is None:
        raise _bad('The result record does not exist in this project')


def _validate_links(root: Path, context: dict[str, Any]) -> None:
    attempts = {row['id'] for row in workspace._attempts(root)}
    wanted = context['attempts'] + context.get('attempt_draft', {}).get('parents', [])
    if any(ref not in attempts for ref in wanted):
        raise _bad('An attempt reference does not belong to this project')
    cards = {card.id: card for card in variables.read_cards(root)}
    for ref in context['variables']:
        card = cards.get(ref['id'])
        if card is None:
            raise _bad('A variable reference does not belong to this project')
        if ref['version'] is None:
            ref['version'] = card.in_use or card.draft
        if ref['version'] not in card.versions:
            raise _bad('A variable version does not exist in this project')
    paths = context['references'] + context['materials']
    if set(context['purposes']) - set(paths):
        raise _bad('File purposes must name a linked reference or material')
    for ref in paths:
        target = outputs._path(root, ref)
        if not target.is_file():
            raise _bad('A reference file does not exist in this project: ' + ref)
        # A relation names a material; it does not attest to its bytes or a run.
        # Avoid reading large datasets every time a record is opened or saved.
        # Output registration and run snapshots keep their own fingerprints.


def validate_context(root: str | Path, context: Any) -> dict[str, Any]:
    found = normalize(context)
    _validate_links(workspace._root(root), found)
    return found


def _entries(root: Path, report: str) -> list[dict[str, Any]]:
    if not workspace._REPORT_RE.fullmatch(report):
        return []  # Hand-written result names have context, but no task catalog.
    entries, _ = outputs._entries(root, outputs.catalog_path(report))
    return [{**item, 'state': outputs._state(root, item) or 'gone'} for item in entries]


def read(root: str | Path, report: str) -> dict[str, Any]:
    root = workspace._root(root)
    _report(root, report)
    rel = path_for(report)
    raw = workspace._read(root, rel)
    context = parse(raw)
    notices = _notices(root, context)
    try:
        entries = _entries(root, report)
    except (workspace.WorkspaceError, OSError) as exc:
        # The output catalog is independent of the authored research context.
        # A damaged catalog must not hide that record or turn a successful
        # context save into an error while building its response.
        entries = []
        notices.append({'kind': 'outputs_unavailable', 'message': 'Cannot read the registered outputs: ' + str(exc),
                        'message_zh': '已登记的产出暂时无法读取；研究记录仍可查看和保存。'})
    drafts = []
    for item in entries:
        if item['kind'] != 'variable':
            continue
        status = {key: item[key] for key in ('path', 'state', 'variable_card') if key in item}
        if item.get('variable_card'):
            try:
                card = variables.open_card(root, item['variable_card'])
                status.update(draft=card.draft, in_use=card.in_use, card_state=card.state)
            except variables.VariableError:
                status['card_state'] = 'unavailable'
        drafts.append(status)
    return {'report': report, 'path': rel, 'revision': workspace._revision(raw),
            'context': context, 'outputs': entries, 'variable_drafts': drafts,
            'choices': {'attempts': workspace._attempts(root),
                        'variables': [{'id': card.id, 'version': number, 'label': f'{card.id} v{number}'}
                                      for card in variables.read_cards(root) for number in sorted(card.versions)]},
            'notices': notices}


def save(root: str | Path, body: dict[str, Any]) -> dict[str, Any]:
    root = workspace._root(root)
    report = body.get('report')
    with situation.write_guard(root, human=True):
        pid = workspace._require_write(root)
        _report(root, report)
        rel = path_for(report)
        before = workspace._read(root, rel)
        workspace._check_revision(body, before)
        parse(before)  # never overwrite a damaged or unknown sidecar
        context = validate_context(root, body.get('context'))
        after = workspace._content(json.dumps({'version': 1, 'context': context}, ensure_ascii=False, indent=2) + '\n')
        _backup, warning = workspace._save(root, rel, before, after, pid)
    result = read(root, report)
    if warning:
        result['history_warning'] = warning
    return result


def import_variable(root: str | Path, body: dict[str, Any]) -> dict[str, Any]:
    root = workspace._root(root)
    report = body.get('report')
    _report(root, report)
    if not any(item['kind'] == 'variable' and item['path'] == body.get('path') for item in _entries(root, report)):
        raise _bad('The variable draft is not registered to this result record')
    result = outputs.import_variable(root, body)
    card = variables.open_card(root, result['id'])
    return {**result, 'version': card.draft, 'in_use': card.in_use}


def dependencies(root: str | Path, report: str) -> set[str]:
    """Explicit file dependencies for the existing member proposal preview."""
    root = workspace._root(root)
    rel = path_for(report)
    raw = workspace._read(root, rel)
    if raw is None:
        return set()
    context = parse(raw)
    found = {rel, *context['references'], *context['materials']}
    for ref in context['references'] + context['materials']:
        outputs._path(root, ref)
    for ref in context['variables']:
        card = variables.open_card(root, ref['id'])
        number = ref['version'] or card.in_use or card.draft
        if number in card.versions:
            found.add(card.versions[number].path.relative_to(root).as_posix())
    for item in _entries(root, report):
        found.add(item['path'])
    return found


def _notices(root: Path, context: dict[str, Any]) -> list[dict[str, Any]]:
    notices = []
    try:
        _validate_links(root, normalize(context))
    except (workspace.WorkspaceError, variables.VariableError) as exc:
        notices.append({'kind': 'unavailable', 'message': str(exc),
                        'message_zh': '部分关联已无法读取，请检查尝试、变量版本或材料文件。'})
    for ref in context['variables']:
        try:
            card = variables.open_card(root, ref['id'])
            if ref['version'] is not None and card.in_use is not None and card.in_use != ref['version']:
                notices.append({'kind': 'older_version', 'id': ref['id'], 'version': ref['version'],
                                'current': card.in_use,
                                'message': f"{ref['id']} v{ref['version']}: the version now in use is v{card.in_use}",
                                'message_zh': f"{ref['id']} v{ref['version']}：当前使用 v{card.in_use}"})
        except variables.VariableError:
            pass
    return notices


def save_agent(root: str | Path, report: str, context: Any, author: dict[str, Any],
               expected_revision: Any = None) -> dict[str, Any]:
    """Trusted agent completion path; caller enforces the active run and report binding."""
    root = workspace._root(root)
    attribution = workspace._author(author)
    if attribution is None:
        raise _bad("Agent context writes require agent attribution")
    with situation.write_guard(root):
        pid = workspace._require_write(root)
        _report(root, report)
        rel = path_for(report)
        before = workspace._read(root, rel)
        workspace._check_revision({'revision': expected_revision}, before)
        parse(before)
        content = validate_context(root, context)
        after = workspace._content(json.dumps({'version': 1, 'context': content}, ensure_ascii=False, indent=2) + '\n')
        _backup, warning = workspace._save(root, rel, before, after, pid, attribution)
    written_revision = workspace._revision(after)
    result = {'report': report, 'path': rel, 'revision': written_revision,
              'written_revision': written_revision, 'context': content, 'notices': []}
    if warning:
        result['history_warning'] = warning
    return result



def backlinks(root: str | Path, paths: list[str] | None = None,
              variable_id: str | None = None) -> list[dict[str, Any]]:
    """Explicit associations in at most 250 direct result sidecars.

    Limit directory entries, file size and results. Use the workspace read
    guard for symlinks/cloud files; never walk the project or infer support.
    """
    from itertools import islice

    root = workspace._root(root)
    wanted = {value for value in (paths or []) if isinstance(value, str)}
    if not wanted and not variable_id:
        return []
    try:
        base = workspace._confined(root, 'research/results')
        candidates = list(islice(base.iterdir(), 2000))
    except (OSError, workspace.WorkspaceError):
        return []
    found, read_count = [], 0
    for path in candidates:
        if read_count >= 250 or len(found) >= 100:
            break
        if not path.name.endswith('.context.json') or path.name.startswith('.'):
            continue
        read_count += 1
        try:
            report = 'research/results/' + path.name.removesuffix('.context.json') + '.md'
            rel = path_for(report)  # Only a valid direct result basename.
            guarded = workspace._confined(root, rel)
            if guarded.stat().st_size > 262144:
                continue
            raw = workspace._read(root, rel)
            if raw is None:
                continue
            context = parse(raw)
            matches = [{'path': value, 'kind': kind, 'purpose': context['purposes'].get(value, '')}
                       for kind in ('references', 'materials') for value in context[kind] if value in wanted]
            refs = [ref for ref in context['variables'] if ref['id'] == variable_id]
            if not matches and not refs:
                continue
            text = workspace._read(root, report)
            if text is None:
                continue
            title = workspace._heading(text[:4096].decode('utf-8', errors='ignore')) or Path(report).stem
            found.append({'report': report, 'title': title, 'question': context['question'],
                          'finding': context['finding'], 'matches': matches, 'variables': refs})
        except (OSError, UnicodeError, workspace.WorkspaceError):
            continue
    return sorted(found, key=lambda item: item['report'])

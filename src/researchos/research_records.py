"""Optional research context beside an existing result record.

The Markdown result and output catalog remain the record of the work. This
sidecar adds editable context and explicit links; it makes no scientific decision.

The handback's part of it (DESIGN.md 21.2) is the agent's own answer to the task: `headline`, `key_numbers`, `undone`,
`decide` and `suggested_assessment`. The suggestion is only a suggestion. `assessment_confirmed` is the record that a
person took it (or put another status in its place) -- the act that writes the attempt's research assessment -- and
only the page's confirm path writes it (`confirm_assessment`): the agent's tool path refuses it, and the editor's save
keeps what is stored and refuses a change to it. A field is written only when it holds something, so a context from
before 21 reads and saves byte for byte as it was.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from researchos import outputs, proposals, workspace
from researchos.records import situation, variables

TEXT_FIELDS = ('question', 'method', 'finding', 'limits', 'next_step')
LINK_FIELDS = ('attempts', 'references', 'materials')
#: The handback's fields (21.2) and the limits that keep them a few lines.
HEADLINE_LIMIT = 200
KEY_NUMBERS_LIMIT = 5
KEY_NUMBER_TEXT_LIMIT = 200
DECIDE_LIMIT = 3
DECIDE_TEXT_LIMIT = 1000
REASON_LIMIT = 1000
HANDBACK_FIELDS = ('headline', 'key_numbers', 'undone', 'decide', 'suggested_assessment', 'assessment_confirmed')
#: The word for a status that has no marker: a cell with no known marker reads as this (21.3).
OTHER_STATUS = '其它'
#: What the agent's tool path says when it is given `assessment_confirmed` (21.2).
AGENT_CONFIRMED = 'An agent cannot confirm an assessment: leave assessment_confirmed out; the researcher confirms it'


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


def _short(value: Any, limit: int, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\0' in value:
        raise _bad(f'{what} must be text of 1 to {limit} characters')
    return value


def _handback(value: dict[str, Any]) -> dict[str, Any]:
    """The handback's fields of `value` (21.2): the ones that hold something, checked in shape. Which statuses a
    suggestion may name, and that a cited file is a project path, need the project (`_validate_links`)."""
    found: dict[str, Any] = {}
    headline = value.get('headline', '')
    if not isinstance(headline, str) or len(headline) > HEADLINE_LIMIT or '\0' in headline:
        raise _bad(f'The headline must be one sentence of at most {HEADLINE_LIMIT} characters')
    if headline.strip():
        found['headline'] = headline.strip()
    numbers = value.get('key_numbers', [])
    if not isinstance(numbers, list) or len(numbers) > KEY_NUMBERS_LIMIT:
        raise _bad(f'The key numbers are a list of at most {KEY_NUMBERS_LIMIT}, each {{what, value, file}}')
    kept = []
    for item in numbers:
        if not isinstance(item, dict) or set(item) != {'what', 'value', 'file'}:
            raise _bad('Each key number needs what it is, its value written as in the file, and the file (what, value, file)')
        kept.append({'what': _short(item['what'], KEY_NUMBER_TEXT_LIMIT, 'What a key number is'),
                     'value': _short(item['value'], KEY_NUMBER_TEXT_LIMIT,
                                     "A key number's value, as text written exactly as in its file,"),
                     'file': _short(item['file'], 1024, "A key number's file (a project path)")})
    if kept:
        found['key_numbers'] = kept
    undone = _text(value.get('undone', ''))
    if undone.strip():
        found['undone'] = undone
    decide = value.get('decide', [])
    if not isinstance(decide, list) or len(decide) > DECIDE_LIMIT:
        raise _bad(f'The questions for the researcher are a list of at most {DECIDE_LIMIT}')
    if decide:
        found['decide'] = [_short(item, DECIDE_TEXT_LIMIT, 'A question for the researcher') for item in decide]
    suggestion = value.get('suggested_assessment')
    if suggestion is not None:
        if not isinstance(suggestion, dict) or set(suggestion) != {'status', 'reason'}:
            raise _bad('A suggested assessment is {status, reason}')
        reason = suggestion['reason']
        if not isinstance(reason, str) or len(reason) > REASON_LIMIT or '\0' in reason:
            raise _bad(f'The reason of a suggested assessment is text of at most {REASON_LIMIT} characters')
        found['suggested_assessment'] = {'status': _short(suggestion['status'], 64, 'A suggested status'), 'reason': reason}
    confirmed = value.get('assessment_confirmed')
    if confirmed is not None:
        if (not isinstance(confirmed, dict) or set(confirmed) != {'by', 'at', 'status', 'adopted'}
                or type(confirmed['adopted']) is not bool):
            raise _bad('A confirmed assessment is {by, at, status, adopted}')
        found['assessment_confirmed'] = {'by': _short(confirmed['by'], 256, 'Who confirmed'),
                                         'at': _short(confirmed['at'], 64, 'When it was confirmed'),
                                         'status': _short(confirmed['status'], 64, 'The confirmed status'),
                                         'adopted': confirmed['adopted']}
    return found


def normalize(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - set(TEXT_FIELDS + LINK_FIELDS + HANDBACK_FIELDS
                                                        + ('attempt_draft', 'variables', 'purposes')):
        raise _bad('Unknown research context fields')
    found = {key: _text(value.get(key, '')) for key in TEXT_FIELDS}
    found.update({key: _links(value.get(key, [])) for key in LINK_FIELDS})
    found.update(_handback(value))
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
    for item in context.get('key_numbers', []):
        # 21.2: a key number cites a file by a project path; whether its text holds the value is the page's check
        # (`handbacks.check_numbers`), so a file that is not there is shown as such, not refused here
        outputs._path(root, item['file'])


def assessment_statuses(root: str | Path) -> list[dict[str, Any]]:
    """The statuses an assessment can have in this project (21.3): the default words and any the researcher named in
    `[verdict_labels]`, each once, with the first marker the table writes for it, whether that marker abandons the
    route (14.3), and 其它 last, which has no marker. A status is its word as written; the words are the project's, so
    a suggestion that names one is the same on disk in either interface language (13.1)."""
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    root = workspace._root(root)
    labels, markers = attempts_ingest.verdict_labels(root), proposals.abandon_markers(root)
    out: list[dict[str, Any]] = []
    for marker, word in labels.items():
        if any(item['status'] == word for item in out):
            continue
        route = attempts_ingest.route_status(marker, labels, markers)
        out.append({'status': word, 'marker': marker, 'abandons': route['status'] == 'abandoned', 'other': False,
                    'code': route['status']})
    out.append({'status': OTHER_STATUS, 'marker': '', 'abandons': False, 'other': True, 'code': 'other'})
    return out


def validate_context(root: str | Path, context: Any, *, stored: dict[str, Any] | None = None) -> dict[str, Any]:
    """`context` normalized and checked against the project. A suggested assessment must name one of the project's
    statuses (21.2) -- unless it is the one `stored` already holds, so that a status the researcher has since taken out
    of `[verdict_labels]` does not stop them saving anything else of the record."""
    from researchos.ingest import attempts as attempts_ingest  # noqa: PLC0415

    root = workspace._root(root)
    found = normalize(context)
    _validate_links(root, found)
    suggestion = found.get('suggested_assessment')
    if suggestion is not None:
        # an agent writing in English may name a default status by its English name (13.4); the file keeps the word
        english = {name.casefold(): word for word, name in attempts_ingest.VERDICT_ENGLISH.items()}
        suggestion['status'] = english.get(suggestion['status'].strip().casefold(), suggestion['status'].strip())
        if suggestion != (stored or {}).get('suggested_assessment'):
            known = [item['status'] for item in assessment_statuses(root)]
            if suggestion['status'] not in known:
                raise _bad('A suggested assessment must name one of this project\'s statuses: '
                           + ', '.join(attempts_ingest.VERDICT_ENGLISH.get(word, word) for word in known))
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
        stored = parse(before)  # never overwrite a damaged or unknown sidecar
        context = validate_context(root, body.get('context'), stored=stored)
        # 21.2: that a person confirmed an assessment is recorded by confirming it and by nothing else: a save keeps
        # what is stored, and one that would change it is refused
        if context.get('assessment_confirmed', stored.get('assessment_confirmed')) != stored.get('assessment_confirmed'):
            raise _bad('The confirmation of an assessment is written only when the assessment is confirmed')
        if 'assessment_confirmed' in stored:
            context['assessment_confirmed'] = stored['assessment_confirmed']
        after = workspace._content(json.dumps({'version': 1, 'context': context}, ensure_ascii=False, indent=2) + '\n')
        _backup, warning = workspace._save(root, rel, before, after, pid)
    result = read(root, report)
    if warning:
        result['history_warning'] = warning
    return result


def confirmed_context(root: str | Path, report: str, confirmed: dict[str, Any]) -> dict[str, Any]:
    """The context of `report` as it will read once `confirmed` ({by, at, status, adopted}) is recorded in it,
    checked as a save is, or the refusal. Nothing is written: the confirm path asks first, so that an assessment is
    not written to the table when its record could not follow."""
    root = workspace._root(root)
    _report(root, report)
    stored = parse(workspace._read(root, path_for(report)))
    return validate_context(root, {**stored, 'assessment_confirmed': confirmed}, stored=stored)


def confirm_assessment(root: str | Path, report: str, confirmed: dict[str, Any]) -> dict[str, Any]:
    """Record that a person confirmed the attempt's assessment from this handback (DESIGN.md 21.2): `assessment_confirmed`
    {by, at, status, adopted} in the report's context -- `adopted` says the suggestion was taken unchanged. The one
    writer of that field. It is the existing human context write (`save`'s guard, backup and history), applied to the
    context as it is stored: the agent's words are not touched, and a context that does not exist yet is made."""
    root = workspace._root(root)
    with situation.write_guard(root, human=True):
        pid = workspace._require_write(root)
        _report(root, report)
        rel = path_for(report)
        before = workspace._read(root, rel)
        context = confirmed_context(root, report, confirmed)
        after = workspace._content(json.dumps({'version': 1, 'context': context}, ensure_ascii=False, indent=2) + '\n')
        _backup, warning = workspace._save(root, rel, before, after, pid)
    result = {'report': report, 'path': rel, 'revision': workspace._revision(after), 'context': context}
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
        if content.get('assessment_confirmed') is not None:  # 21.2: a person's act, never an agent's
            raise _bad(AGENT_CONFIRMED)
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

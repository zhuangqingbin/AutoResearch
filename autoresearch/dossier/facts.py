"""Typed dossier facts, conservative reuse, and append-only change history.

Evidence verdicts are supplied by the deterministic source verifier. A citation,
model-written freshness date, or dossier age alone cannot make a fact reusable.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from autoresearch.common.atomic import canonical_json, sha256_bytes

HEADER = '## 事实有效性账本'
FENCE = 'dossier-facts-v1'
KINDS = {'STABLE', 'DYNAMIC', 'HYPOTHESIS'}
FIELDS = {'fact_id', 'kind', 'section', 'statement', 'source_claim_ids',
          'effective_date', 'available_at', 'snapshot_at', 'proposed_at',
          'expires_at', 'falsifier'}


def _time(value):
    if not isinstance(value, str):
        raise ValueError('aware fact timestamp required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('aware fact timestamp required')
    return parsed


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('ISO fact date required')
    return value


def validate_fact(row):
    if not isinstance(row, dict) or set(row) != FIELDS:
        raise ValueError('invalid dossier fact fields')
    if row['kind'] not in KINDS or row['section'] not in [str(i) for i in range(1, 9)]:
        raise ValueError('invalid dossier fact kind or section')
    if not isinstance(row['fact_id'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', row['fact_id']):
        raise ValueError('invalid dossier fact identity')
    if not isinstance(row['statement'], str) or not row['statement'].strip():
        raise ValueError('fact statement required')
    refs = row['source_claim_ids']
    if not isinstance(refs, list) or any(not isinstance(x, str) or not x for x in refs) or len(set(refs)) != len(refs):
        raise ValueError('invalid fact evidence references')
    _day(row['effective_date'])
    _time(row['available_at'])
    for key in ('snapshot_at', 'proposed_at', 'expires_at'):
        if row[key] is not None:
            _time(row[key])
    if row['kind'] == 'DYNAMIC' and row['snapshot_at'] is None:
        raise ValueError('dynamic fact requires snapshot_at')
    if row['kind'] == 'HYPOTHESIS':
        if row['proposed_at'] is None or row['expires_at'] is None or not isinstance(row['falsifier'], str) or not row['falsifier'].strip():
            raise ValueError('hypothesis requires proposal, expiry and falsifier')
        if _time(row['expires_at']) <= _time(row['proposed_at']):
            raise ValueError('hypothesis expiry must follow proposal')
    elif row['proposed_at'] is not None or row['falsifier'] is not None:
        raise ValueError('facts cannot carry hypothesis-only fields')
    return row


def empty_ledger(subject):
    return {'schema_version': 1, 'subject': str(subject), 'facts': [], 'history': []}


def validate_ledger(value):
    if not isinstance(value, dict) or set(value) != {'schema_version', 'subject', 'facts', 'history'}:
        raise ValueError('invalid dossier fact ledger')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('unsupported dossier fact version')
    if not isinstance(value['subject'], str) or not value['subject']:
        raise ValueError('dossier subject required')
    if not isinstance(value['facts'], list) or not isinstance(value['history'], list):
        raise ValueError('fact and history lists required')
    ids = [validate_fact(row)['fact_id'] for row in value['facts']]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate fact identity')
    for event in value['history']:
        if not isinstance(event, dict) or set(event) != {'changed_at', 'change', 'before', 'after', 'reasons'}:
            raise ValueError('invalid dossier history event')
        _time(event['changed_at'])
        if event['change'] not in {'ADDED', 'CHANGED', 'CONFLICT', 'EXPIRED', 'MISSING_SOURCE'}:
            raise ValueError('invalid dossier change kind')
        for key in ('before', 'after'):
            if event[key] is not None:
                validate_fact(event[key])
        if not isinstance(event['reasons'], list) or any(not isinstance(x, str) for x in event['reasons']):
            raise ValueError('invalid dossier change reasons')
    return value


def parse_ledger(text):
    blocks = re.findall(r'(?m)^```dossier-facts-v1\s*\n(.*?)^```\s*$', text, re.S)
    if not blocks:
        return None
    if len(blocks) != 1:
        raise ValueError('exactly one dossier fact ledger required')
    return validate_ledger(json.loads(blocks[0]))


def render_ledger(value):
    return HEADER + '\n\n```' + FENCE + '\n' + canonical_json(validate_ledger(value)) + '\n```\n'


def replace_ledger(text, value):
    block = render_ledger(value)
    pattern = r'(?m)^## 事实有效性账本\n.*?(?=^## |\Z)'
    if re.search(pattern, text, re.S):
        return re.sub(pattern, lambda _: block + '\n', text, count=1, flags=re.S)
    index = text.find('## 摘要(注入用)')
    if index < 0:
        raise ValueError('dossier summary anchor missing')
    return text[:index] + block + '\n' + text[index:]


def evaluate_fact(row, *, analysis_date, knowledge_cutoff, evidence, corrected_claim_ids=(), timezone="Asia/Shanghai"):
    validate_fact(row)
    day, cutoff = _day(analysis_date), _time(knowledge_cutoff)
    reasons = []
    if row['effective_date'] > day or _time(row['available_at']) > cutoff:
        reasons.append('NOT_AVAILABLE_BY_CUTOFF')
    if row['expires_at'] is not None and _time(row['expires_at']) <= cutoff:
        reasons.append('EXPIRED')
    if row['kind'] == 'DYNAMIC':
        snapshot = _time(row['snapshot_at'])
        if snapshot.astimezone(ZoneInfo(timezone)).date().isoformat() != day or snapshot > cutoff:
            reasons.append('DYNAMIC_REFRESH_REQUIRED')
    if row['kind'] == 'HYPOTHESIS' and _time(row['proposed_at']) > cutoff:
        reasons.append('HYPOTHESIS_NOT_PROPOSED')
    refs = row['source_claim_ids']
    statement_hash = sha256_bytes(row['statement'].encode())
    if not refs:
        reasons.append('MISSING_SOURCE')
    for claim_id in refs:
        proof = evidence.get(claim_id)
        if claim_id in corrected_claim_ids:
            reasons.append('CORRECTION_RECHECK_REQUIRED')
        if not proof or proof.get('statement_sha256') != statement_hash:
            reasons.append('MISSING_SOURCE')
        elif proof.get('verdict') != 'PASS':
            reasons.append('CONFLICT' if proof.get('verdict') == 'FAIL' else 'SOURCE_UNVERIFIED')
        else:
            # These fields are projected from the verified event, not the fact
            # proposal. Editing a date or a label cannot refresh old evidence.
            effective = proof.get('source_effective_at')
            assertion = proof.get('source_assertion_kind')
            expected_kinds = {'forecast', 'conditional'} if row['kind'] == 'HYPOTHESIS' else {'actual'}
            if not isinstance(effective, dict) or assertion not in expected_kinds:
                reasons.append('FACT_SEMANTICS_UNVERIFIED')
            else:
                start, end = _time(effective['start']), _time(effective['end'])
                if row['effective_date'] != start.astimezone(ZoneInfo(timezone)).date().isoformat():
                    reasons.append('FACT_EFFECTIVE_TIME_UNBOUND')
                if row['kind'] == 'DYNAMIC' and not start <= _time(row['snapshot_at']) < end:
                    reasons.append('DYNAMIC_SOURCE_REFRESH_REQUIRED')
    return {'fact_id': row['fact_id'], 'kind': row['kind'], 'reusable': not reasons,
            'reasons': sorted(set(reasons)), 'statement_sha256': statement_hash}


def reusable_view(ledger, *, analysis_date, knowledge_cutoff, evidence, corrected_claim_ids=(), timezone="Asia/Shanghai"):
    validate_ledger(ledger)
    evaluations = [evaluate_fact(row, analysis_date=analysis_date, knowledge_cutoff=knowledge_cutoff,
                                evidence=evidence, corrected_claim_ids=corrected_claim_ids, timezone=timezone) for row in ledger['facts']]
    eligible = {row['fact_id'] for row in evaluations if row['reusable']}
    return {'schema_version': 1, 'subject': ledger['subject'], 'analysis_date': analysis_date,
            'knowledge_cutoff': knowledge_cutoff, 'evaluations': evaluations,
            'facts': [copy.deepcopy(row) for row in ledger['facts'] if row['fact_id'] in eligible],
            'coverage': {'declared': len(evaluations), 'reusable': len(eligible)},
            'semantic_coverage': 'UNKNOWN', 'fresh_inputs_required': ['slim', 'news', 'ruler', 'execution']}


def apply_updates(ledger, updates, *, changed_at, analysis_date, knowledge_cutoff, evidence,
                  corrected_claim_ids=(), timezone="Asia/Shanghai"):
    """Unknown changes are recorded without overwriting established facts."""
    validate_ledger(ledger)
    _time(changed_at)
    result = copy.deepcopy(ledger)
    by_id = {row['fact_id']: row for row in result['facts']}
    seen = set()
    if not isinstance(updates, list):
        raise ValueError('fact updates must be a list')
    for update in updates:
        validate_fact(update)
    proposed_ids = {row['fact_id'] for row in updates}
    # Existing facts remain in the denominator even when no replacement arrived.
    checked_updates = updates + [row for row in ledger['facts'] if row['fact_id'] not in proposed_ids]
    for update in checked_updates:
        validate_fact(update)
        key = update['fact_id']
        if key in seen:
            raise ValueError('duplicate update identity')
        seen.add(key)
        old = by_id.get(key)
        check = evaluate_fact(update, analysis_date=analysis_date, knowledge_cutoff=knowledge_cutoff,
                              evidence=evidence, corrected_claim_ids=corrected_claim_ids, timezone=timezone)
        reasons = check['reasons']
        if old == update and not reasons:
            continue
        if old and old['kind'] != update['kind']:
            reasons = sorted(set(reasons + ['FACT_KIND_CHANGE_REQUIRES_NEW_ID']))
        temporal = ('effective_date', 'available_at', 'snapshot_at', 'proposed_at', 'expires_at')
        if (old and any(old[key] != update[key] for key in temporal)
                and not set(update['source_claim_ids']) - set(old['source_claim_ids'])):
            reasons = sorted(set(reasons + ['NEW_SOURCE_REQUIRED_FOR_TIME_CHANGE']))
        if not reasons:
            change = 'CHANGED' if old else 'ADDED'
        elif 'EXPIRED' in reasons or 'DYNAMIC_REFRESH_REQUIRED' in reasons:
            change = 'EXPIRED'
        elif any(reason in reasons for reason in ('CONFLICT', 'FACT_KIND_CHANGE_REQUIRES_NEW_ID', 'NEW_SOURCE_REQUIRED_FOR_TIME_CHANGE', 'CORRECTION_RECHECK_REQUIRED')):
            change = 'CONFLICT'
        else:
            change = 'MISSING_SOURCE'
        event = {'changed_at': changed_at, 'change': change, 'before': copy.deepcopy(old),
                 'after': copy.deepcopy(update), 'reasons': reasons}
        if event not in result['history']:
            result['history'].append(event)
        if not reasons:
            by_id[key] = copy.deepcopy(update)
    result['facts'] = list(by_id.values())
    return validate_ledger(result)


def _fact_claim_context(handle, subject, consumer_task_id):
    """A research consumer sees its ancestors; standalone maintenance sees accepted tasks."""
    from autoresearch.news.card_claims import bound_claim_context

    workspace = getattr(handle, 'workspace', None)
    if workspace is None:
        return None
    owner = Path(workspace) / 'session/tasks.json'
    if not owner.is_file():
        return None
    document = json.loads(owner.read_bytes())
    if document['engine'] != handle.engine or document['run_id'] != handle.run_id:
        raise ValueError('dossier fact task owner belongs to another run')
    entries = document['tasks']
    if consumer_task_id is not None:
        entry = entries.get(consumer_task_id)
        if entry is None or entry.get('state') not in {'RUNNING', 'CLAIMED', 'SUCCEEDED'}:
            return None
        return bound_claim_context(handle, task=entry['spec'])
    accepted = {key: row['attempt'] for key, row in entries.items()
                if row.get('state') == 'SUCCEEDED' and row['spec'].get('subject') == subject}
    if not accepted:
        return None
    anchor = next(iter(accepted))
    return {'identity': {'engine': handle.engine, 'run_id': handle.run_id,
                         'task_id': anchor, 'attempt': accepted[anchor]},
            'accepted_attempts': accepted,
            'task_subjects': {key: row['spec'].get('subject') for key, row in entries.items()}}


def evidence_from_capsule(handle, frame, *, subject, consumer_task_id=None):
    """Root-owned accepted attempts and verified source semantics, never model PASS."""
    from autoresearch.news.card_claims import claim_population

    context = _fact_claim_context(handle, subject, consumer_task_id)
    if context is None:
        return {}
    population, _ = claim_population(handle.capsule, identity=context['identity'],
        accepted_attempts=context['accepted_attempts'], frame=frame, subject=subject,
        task_subjects=context['task_subjects'])
    evidence = {}
    for claim_id, row in population.items():
        semantics = []
        for sidecar_id in row['sidecar_ids']:
            path = Path(handle.capsule) / 'evidence/material_claims' / f'{sidecar_id}.json'
            event = json.loads(path.read_bytes()).get('claim_event') or {}
            if event.get('subject_code') != subject:
                continue
            semantics.append({'source_effective_at': event.get('effective_at'),
                              'source_assertion_kind': event.get('assertion_kind')})
        if semantics and len({canonical_json(item) for item in semantics}) == 1:
            evidence[claim_id] = {**row, **semantics[0]}
    return evidence


def render_reusable(view, *, sections=None):
    rows = [row for row in view['facts'] if sections is None or row['section'] in sections]
    header = (f"档案逐项复用：{len(rows)}/{view['coverage']['declared']}；"
              f"目标日 {view['analysis_date']}；未核项不进入本次事实。")
    lines = [header]
    for row in rows:
        label = '研究假设' if row['kind'] == 'HYPOTHESIS' else ('动态快照' if row['kind'] == 'DYNAMIC' else '稳定事实')
        lines.append(f"- [{label}] {row['statement']}（有效日 {row['effective_date']}；来源 {','.join(row['source_claim_ids'])}）")
        if row['kind'] == 'HYPOTHESIS':
            lines.append(f"  反证条件：{row['falsifier']}；到期：{row['expires_at']}")
    gaps = [f"{row['fact_id']}:{','.join(row['reasons'])}" for row in view['evaluations'] if not row['reusable']]
    if gaps:
        lines.append('未复用：' + '；'.join(gaps))
    lines.append('本次 slim、新闻、主尺与执行条件仍须重新生成；不复用整张决策卡。')
    return '\n'.join(lines)


def capture_fact_context(text, updates, *, analysis_date, knowledge_cutoff=None):
    """Freeze actual source bytes for maintenance; no caller verdict is accepted.

    An offline caller may supply a cutoff, but cannot supply evidence. Such an
    update is explicitly unverified. Replay re-runs the same source verifier on
    the captured receipts, claims and blobs, without consulting the live run.
    """
    import base64
    import os

    from autoresearch.common import workspace as ws
    from autoresearch.trace.blobs import blob_path
    from autoresearch.trace.capsule import require_active_run
    from autoresearch.trace.frozen_sources import frozen_source_context
    from autoresearch.trace.source_receipts import RECEIPTS_PATH

    ledger = parse_ledger(text)
    updates = [] if updates is None else updates
    if not isinstance(updates, list):
        raise ValueError('fact updates must be a list')
    for row in updates:
        validate_fact(row)
    active = ws.active_run_id()
    context = None
    if active:
        handle = require_active_run(active)
        context = frozen_source_context(handle.staging)
    if context is None and not updates and not (ledger or {}).get('facts'):
        return None
    if context is None and knowledge_cutoff is None:
        raise ValueError('fact maintenance requires a frozen frame or explicit knowledge_cutoff')
    files = {}
    task_owner = None
    consumer_task_id = None
    frame = None
    run_id = None
    engine = ws.ENGINE
    if context is not None:
        frame, handle = context['frame'], context['handle']
        if knowledge_cutoff is not None and _time(knowledge_cutoff) != _time(frame['knowledge_cutoff']):
            raise ValueError('fact cutoff differs from frozen frame')
        analysis_date, knowledge_cutoff = frame['analysis_session'], frame['knowledge_cutoff']
        engine, run_id = handle.engine, handle.run_id
        owner = Path(handle.workspace) / 'session/tasks.json'
        task_owner = base64.b64encode(owner.read_bytes()).decode('ascii') if owner.is_file() else None
        consumer_task_id = os.environ.get('AUTORESEARCH_TASK_ID') or None
        capsule = Path(handle.capsule)
        paths = list((capsule / 'evidence/material_claims').glob('*.json'))
        receipts_path = capsule / RECEIPTS_PATH
        if receipts_path.exists():
            paths.append(receipts_path)
        # Registered field reviews require the original root issuance chain.
        events_path = capsule / 'events/events.jsonl'
        if events_path.is_file():
            paths.append(events_path)
        paths.extend(blob_path(capsule, row['payload_hash']) for row in context['receipts'])
        for path in sorted(set(paths)):
            if path.is_symlink():
                raise ValueError('fact evidence symlink')
            path.resolve(strict=True).relative_to(capsule.resolve(strict=True))
            files[path.relative_to(capsule).as_posix()] = base64.b64encode(path.read_bytes()).decode('ascii')
    _day(analysis_date)
    _time(knowledge_cutoff)
    return {'schema_version': 1, 'engine': engine, 'run_id': run_id,
            'analysis_date': analysis_date, 'knowledge_cutoff': knowledge_cutoff,
            'changed_at': knowledge_cutoff, 'frame': frame, 'updates': copy.deepcopy(updates),
            'source_files': files, 'task_owner': task_owner, 'consumer_task_id': consumer_task_id}


def apply_fact_context(text, context, *, scratch_root):
    """Replay a root-captured fact context using original source bytes."""
    import base64
    from types import SimpleNamespace

    from autoresearch.common.atomic import atomic_write_bytes
    from autoresearch.contracts.execution import validate_decision_frame
    from autoresearch.dossier.delta import apply_fact_delta
    from autoresearch.dossier.schema import parse_frontmatter
    from autoresearch.trace.source_receipts import read_receipts

    required = {'schema_version', 'engine', 'run_id', 'analysis_date', 'knowledge_cutoff',
                'changed_at', 'frame', 'updates', 'source_files', 'task_owner', 'consumer_task_id'}
    if (not isinstance(context, dict) or set(context) != required
            or type(context['schema_version']) is not int or context['schema_version'] != 1
            or context['engine'] not in {'codex', 'claude'}):
        raise ValueError('invalid fact maintenance context')
    _day(context['analysis_date'])
    _time(context['knowledge_cutoff'])
    if context['changed_at'] != context['knowledge_cutoff']:
        raise ValueError('fact change time differs from frozen cutoff')
    files, frame = context['source_files'], context['frame']
    if not isinstance(files, dict):
        raise ValueError('fact source files must be an object')
    evidence = {}
    subject = parse_frontmatter(text).get('code')
    if frame is None:
        if files or context['run_id'] is not None or context['task_owner'] is not None or context['consumer_task_id'] is not None:
            raise ValueError('offline facts cannot contain claimed source verdicts')
    else:
        validate_decision_frame(frame)
        if (frame['analysis_session'] != context['analysis_date']
                or frame['knowledge_cutoff'] != context['knowledge_cutoff']
                or not isinstance(context['run_id'], str) or not context['run_id']):
            raise ValueError('fact context/frame identity mismatch')
        workspace = Path(scratch_root) / 'fact-sources'
        capsule = workspace / 'capsule'
        capsule.mkdir(parents=True, exist_ok=False)
        for relative, encoded in files.items():
            allowed = (relative in {'lineage/source_receipts.jsonl', 'events/events.jsonl'}
                       or re.fullmatch(r'evidence/material_claims/[0-9a-f]{64}\.json', relative)
                       or re.fullmatch(r'blobs/sha256/[0-9a-f]{2}/[0-9a-f]{64}', relative))
            if not allowed:
                raise ValueError('unregistered fact source path')
            atomic_write_bytes(capsule / relative, base64.b64decode(encoded, validate=True))
        receipts = read_receipts(capsule)
        if any(row['engine'] != context['engine'] or row['run_id'] != context['run_id'] for row in receipts):
            raise ValueError('fact receipt belongs to another run')
        if context['task_owner'] is not None:
            atomic_write_bytes(workspace / 'session/tasks.json', base64.b64decode(context['task_owner'], validate=True))
        handle = SimpleNamespace(capsule=capsule, workspace=workspace, engine=context['engine'], run_id=context['run_id'])
        evidence = evidence_from_capsule(handle, frame, subject=subject, consumer_task_id=context['consumer_task_id'])
    return apply_fact_delta(text, context['updates'], analysis_date=context['analysis_date'],
                            knowledge_cutoff=context['knowledge_cutoff'],
                            changed_at=context['changed_at'], evidence=evidence,
                            timezone=frame['timezone'] if frame is not None else 'Asia/Shanghai')

"""Read-only attempt metering from frozen handoffs and bound host archives.

Token counts use the existing adapters. Missing fields remain null; a dispatch is
not an API call and adapter usage records are not model messages or API calls.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.contracts.session_metering import METRICS
from autoresearch.research.run_usage import (
    RUN_TOKEN_FIELDS as RUN_TOKEN_FIELDS,
    RUN_USAGE_VERSION as RUN_USAGE_VERSION,
    build_run_usage as build_run_usage,
    join_usage_records as join_usage_records,
    native_usage_records as native_usage_records,
)
from autoresearch.trace.transcripts import TranscriptRef, adapter_for
from autoresearch.trace.transcripts.snapshot import snapshot_from_archive_bytes


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _model(spec, source):
    return {'model': spec.get('model'), 'effort': spec.get('effort') or spec.get('reasoning_effort'), 'source': source}


def _identity(value, handle, task, attempt):
    expected = {'engine': handle.engine, 'run_id': handle.run_id, 'task_id': task['task_id'],
                'attempt': attempt, 'role': task['role']}
    if any(value.get(k) != v for k, v in expected.items()):
        raise ValueError('metering attempt identity mismatch')


def _archive_stats(handle, task, attempt, request):
    from autoresearch.session_agent.host_evidence import _existing_binding, _load_binding_ref
    binding = _existing_binding(handle, task['task_id'], attempt)
    if binding is None:
        return None
    _identity(binding, handle, task, attempt)
    if binding['subject'] != task['subject'] or binding['invocation_id'] != f"session-{task['task_id']}-a{attempt}":
        raise ValueError('metering binding subject/invocation mismatch')
    context_path = Path(handle.workspace) / 'session/dispatch' / f"{task['task_id']}-a{attempt}.context.json"
    if context_path.is_file():
        context = _read(context_path)
        if context['engine'] != handle.engine or context['session_id'] != binding['session_ref'] or (context['agent_id'] and context['agent_id'] != binding['context_ref']):
            raise ValueError('metering frozen host context mismatch')
    elif request and request.get('host_session_ref') and request['host_session_ref'] != binding['session_ref']:
        raise ValueError('metering frozen host session mismatch')
    from autoresearch.session_agent.host_evidence import resolve_receipt_evidence
    for path in (Path(handle.capsule) / 'agents/session/host_receipts').glob('*.json'):
        receipt = _read(path)
        if receipt.get('task_id') == task['task_id'] and receipt.get('attempt') == attempt:
            if path.stem != sha256_bytes(canonical_json(receipt).encode('utf-8')):
                raise ValueError('metering host receipt hash mismatch')
            resolve_receipt_evidence(handle, task, receipt)
    # The same exported interval cannot be charged to multiple attempts.
    from autoresearch.contracts.forensic import validate_host_evidence_binding
    for path in (Path(handle.capsule) / 'agents/session/task_bindings').glob('*.json'):
        other = validate_host_evidence_binding(_read(path))
        same_source = (other['source_path'] == binding['source_path'] or
                       (other['session_ref'], other['context_ref']) == (binding['session_ref'], binding['context_ref']))
        if (other['binding_id'] != binding['binding_id'] and same_source
                and max(other['start_ordinal'], binding['start_ordinal']) <= min(other['end_ordinal'], binding['end_ordinal'])):
            raise ValueError('overlapping host intervals cannot be attributed to multiple attempts')
    # Do not follow a binding out of this capsule, even if its hash is valid.
    for field in ('raw_path', 'normalized_path'):
        path = (Path(handle.capsule) / binding[field]).resolve()
        if not path.is_relative_to(Path(handle.capsule).resolve()):
            raise ValueError('metering archive outside capsule')
    binding = _load_binding_ref(handle, f"host-binding:{binding['binding_id']}")
    path = Path(handle.capsule) / binding['raw_path']
    snapshot = snapshot_from_archive_bytes(path.read_bytes(), engine=handle.engine, path=path)
    if snapshot.bad_lines:
        raise ValueError('metering archive contains invalid rows')
    rows = list(snapshot.rows)
    start, end = binding['start_ordinal'], binding['end_ordinal']
    segment = [r for r in rows if type(r.get('ordinal')) is int and start <= r['ordinal'] <= end]
    if not segment:
        raise ValueError('metering archive has no bound interval')
    ref = TranscriptRef(engine=handle.engine, path=path, role=task['role'], subject=task['subject'],
                        invocation_id=binding['invocation_id'], session_ref=binding['session_ref'],
                        start_ordinal=start, end_ordinal=end)
    adapter = adapter_for(handle.engine)
    # Claude exports are segmented here; Codex needs preceding cumulative snapshots.
    stats = adapter.stats_from_rows(rows if handle.engine == 'codex' else segment, ref)
    observations = []
    for row in segment:
        if row.get('type') not in {'turn_context', 'assistant'}:
            continue
        observation = adapter.stats_from_rows([row], ref).normalized
        model = None if observation.model in {'—', '', 'UNKNOWN'} else observation.model
        effort = None if observation.effort in {'—', '', 'UNKNOWN'} else observation.effort
        if model is not None or effort is not None:
            observations.append({'ordinal': row['ordinal'], 'model': model, 'effort': effort})
    return binding, stats, observations, rows, segment


def _field_presence(engine, rows, segment):
    """Coverage only; token arithmetic remains exclusively adapter-owned."""
    if engine == 'codex':
        def snapshots(items):
            return [r['payload']['info']['total_token_usage'] for r in items
                    if r.get('type') == 'event_msg' and r.get('payload', {}).get('type') == 'token_count'
                    and isinstance((r.get('payload', {}).get('info') or {}).get('total_token_usage'), dict)]
        selected = snapshots(segment)
        if not selected:
            return set()
        before = snapshots([r for r in rows if type(r.get('ordinal')) is int and r['ordinal'] < segment[0]['ordinal']])
        relevant = [selected[-1]] + ([before[-1]] if before else [])
    else:
        latest = {}
        for row in segment:
            msg = row.get('message') or {}
            if row.get('type') == 'assistant' and msg.get('id'):
                latest[msg['id']] = msg.get('usage') or {}
        relevant = list(latest.values())
    if not relevant:
        return set()
    return set.intersection(*({k for k, v in item.items() if type(v) is int and v >= 0} for item in relevant))


def measure_attempt(handle, task: dict, key: dict) -> dict:
    attempt = key['attempt']
    request_path = Path(handle.capsule) / 'agents/session/requests' / f"session-{task['task_id']}-a{attempt}.json"
    value = {'task_id': task['task_id'], 'attempt': attempt, 'role': task['role'], 'state': key['state'],
             'dispatch_count': int(request_path.is_file()), 'evidence_status': 'MISSING', 'evidence_source': None,
             'requested': _model({}, 'UNKNOWN'), 'resolved': _model({}, 'UNKNOWN_LEGACY_REQUEST'),
             'observed': {**_model({}, 'UNKNOWN'), 'status': 'UNKNOWN', 'observations': []},
             'metrics': dict.fromkeys(METRICS), 'estimated_price': None, 'proxy_input_chars': None, 'errors': []}
    if not request_path.is_file():
        value['evidence_status'] = 'NOT_DISPATCHED'
        return value
    try:
        handoff = _read(request_path)
        _identity(handoff['envelope'], handle, task, attempt)
        request = handoff.get('dispatch_request')
        if request is not None:
            _identity(request, handle, task, attempt)
            value['resolved'] = _model(request, f"FROZEN_DISPATCH_REQUEST:{request.get('resolution', 'UNKNOWN')}")
            value['proxy_input_chars'] = len(request['prompt'])
            bundle = (handle.contract.user_config or {}).get('resolved_agent_bundle') or {}
            declared = bundle.get('declared_roles')
            if isinstance(declared, dict):
                value['requested'] = _model(declared.get(request.get('config_role')) or {}, 'FROZEN_DECLARED_CONFIG')
        captured = _archive_stats(handle, task, attempt, request)
        if captured is None:
            return value
        binding, stats, observations, rows, segment = captured
        value['evidence_status'] = 'PRESENT'
        value['evidence_source'] = {'binding_id': binding['binding_id'], 'archive_sha256': binding['archive_sha256'],
                                    'raw_path': binding['raw_path'], 'start_ordinal': binding['start_ordinal'], 'end_ordinal': binding['end_ordinal']}
        observed = value['observed']
        observed.update(source='BOUND_HOST_ARCHIVE', observations=observations)
        for field in ('model', 'effort'):
            values = {item[field] for item in observations if item[field] is not None}
            observed[field] = next(iter(values)) if len(values) == 1 else None
        observed['status'] = ('MIXED' if any(len({o[f] for o in observations if o[f] is not None}) > 1 for f in ('model', 'effort'))
                              else 'OBSERVED' if observed['model'] is not None or observed['effort'] is not None else 'UNKNOWN')
        usage, metrics = stats.usage, value['metrics']
        present = _field_presence(handle.engine, rows, segment)
        if usage.status != 'UNMEASURED':
            fields = {'input_tokens': ('input', {'input_tokens', 'cached_input_tokens'} if handle.engine == 'codex' else {'input_tokens'}),
                      'output_tokens': ('output', {'output_tokens'}),
                      'cached_input_tokens': ('cache_read', {'cached_input_tokens' if handle.engine == 'codex' else 'cache_read_input_tokens'}),
                      'cache_creation_tokens': ('cache_create', {'cache_write_input_tokens' if handle.engine == 'codex' else 'cache_creation_input_tokens'}),
                      'reasoning_output_tokens': ('reasoning_output', {'reasoning_output_tokens'})}
            for name, (attribute, required) in fields.items():
                if required <= present:
                    metrics[name] = getattr(usage, attribute)
        metrics['host_usage_records'] = usage.messages
        metrics['host_model_messages'] = sum(item.kind == 'message' and item.payload.get('role') == 'assistant' for item in stats.normalized.items)
        if stats.started_at and stats.ended_at:
            elapsed = (datetime.fromisoformat(stats.ended_at.replace('Z', '+00:00')) - datetime.fromisoformat(stats.started_at.replace('Z', '+00:00'))).total_seconds()
            metrics['duration_seconds'] = elapsed if elapsed >= 0 else None
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        value['evidence_status'] = 'INVALID'
        value['observed'] = {**_model({}, 'UNKNOWN'), 'status': 'UNKNOWN', 'observations': []}
        value['metrics'] = dict.fromkeys(METRICS)
        value['errors'].append(f'{type(exc).__name__}: {exc}')
    return value


def summarize_attempts(attempts):
    dispatched = [item for item in attempts if item['dispatch_count']]
    result = {}
    for field in METRICS:
        observed = [item['metrics'][field] for item in dispatched if item['metrics'][field] is not None]
        count, expected = len(observed), len(dispatched)
        result[field] = {'value': sum(observed) if count == expected and expected else None,
                         'observed_total': sum(observed) if observed else None, 'observed_count': count,
                         'expected_count': expected, 'coverage': count / expected if expected else None,
                         'status': 'COMPLETE' if expected and count == expected else 'PARTIAL' if count else 'MISSING'}
    return result


def build_metering(handle, *, evidence_plan=None):
    from autoresearch.contracts.forensic import validate_evidence_plan
    from autoresearch.contracts.session_plan import validate_plan
    from autoresearch.session_agent.evidence import _expanded_tasks, build_evidence_plan
    frozen_plan = Path(handle.capsule) / "evidence/evidence_plan.json"
    plan = validate_evidence_plan(evidence_plan if evidence_plan is not None else
                                  (_read(frozen_plan) if frozen_plan.is_file() else build_evidence_plan(handle)))
    if (plan['engine'], plan['run_id']) != (handle.engine, handle.run_id):
        raise ValueError('metering evidence plan identity mismatch')
    tasks, _ = _expanded_tasks(handle, validate_plan(_read(Path(handle.workspace) / "session/plan.json")))
    specs = {task["task_id"]: task for task in tasks}
    attempts = [measure_attempt(handle, specs[key['task_id']], key) for key in plan['task_keys']
                if specs[key['task_id']]['kind'] == 'INFERENCE']
    value = {'schema_version': 1, 'engine': handle.engine, 'run_id': handle.run_id,
             'evidence_plan_hash': plan['evidence_plan_hash'], 'attempts': attempts,
             'dispatch_count_basis': 'FROZEN_HANDOFF_NOT_MODEL_EXECUTION',
             'dispatch_count': sum(item['dispatch_count'] for item in attempts),
             'not_dispatched_count': sum(not item['dispatch_count'] for item in attempts),
             'metrics': summarize_attempts(attempts), 'estimated_price': None}
    from autoresearch.contracts.session_metering import validate_metering
    return validate_metering(value)


def materialize_metering(handle, *, evidence_plan=None):
    if (Path(handle.capsule) / 'verification/ROOT.json').exists():
        raise ValueError('cannot update metering in a sealed capsule')
    value = build_metering(handle, evidence_plan=evidence_plan)
    atomic_write_json(Path(handle.capsule) / 'agents/session/metering.json', value)
    return value

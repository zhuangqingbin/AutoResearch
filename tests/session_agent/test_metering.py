from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

import pytest

from autoresearch.common.atomic import atomic_write_json, sha256_file
from autoresearch.contracts.forensic import host_evidence_binding_hash

from .test_service import _handle


def _fixture(tmp_path, *, usage=True):
    handle = _handle(tmp_path)
    handle.contract.user_config['resolved_agent_bundle'] = {'declared_roles': {'l4_card': {'model': 'asked', 'effort': 'high'}}}
    task = {'task_id': 'stock.card', 'kind': 'INFERENCE', 'role': 'stock.card', 'subject': None}
    key = {'task_id': task['task_id'], 'attempt': 1, 'state': 'SUCCEEDED'}
    request = {'engine': handle.engine, 'run_id': handle.run_id, 'task_id': task['task_id'], 'attempt': 1,
               'role': task['role'], 'subject': None, 'config_role': 'l4_card', 'model': 'resolved',
               'effort': 'medium', 'resolution': 'FROZEN_RUN_CONTRACT', 'prompt': 'hello'}
    path = handle.capsule / 'agents/session/requests/session-stock.card-a1.json'
    atomic_write_json(path, {'envelope': {k: request[k] for k in ('engine', 'run_id', 'task_id', 'attempt', 'role')}, 'dispatch_request': request})
    rows = [{'ordinal': 0, 'type': 'turn_context', 'timestamp': '2026-09-13T00:00:00Z', 'payload': {'model': 'actual', 'effort': 'low'}},
            {'ordinal': 1, 'type': 'event_msg', 'timestamp': '2026-09-13T00:00:02Z', 'payload': {'type': 'task_complete'}}]
    if usage:
        rows.insert(1, {'ordinal': 1, 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'input_tokens': 20, 'output_tokens': 5, 'cached_input_tokens': 8}}}})
        rows[-1]['ordinal'] = 2
    raw = handle.capsule / 'raw.gz'
    raw.write_bytes(gzip.compress(('\n'.join(json.dumps(r) for r in rows) + '\n').encode()))
    normalized = handle.capsule / 'normalized.json'
    normalized.write_text('{}')
    binding = {'schema_version': 1, 'engine': handle.engine, 'run_id': handle.run_id, 'task_id': task['task_id'], 'attempt': 1,
               'role': task['role'], 'subject': None, 'session_ref': 'session', 'context_ref': 'child', 'parent_context_ref': 'parent',
               'context_source': 'SUBAGENT', 'invocation_id': 'session-stock.card-a1', 'start_ordinal': 0, 'end_ordinal': rows[-1]['ordinal'],
               'source_path': str(tmp_path / 'source.jsonl'), 'source_sha256': 'a'*64, 'archive_sha256': sha256_file(raw),
               'raw_path': 'raw.gz', 'normalized_path': 'normalized.json', 'normalized_sha256': sha256_file(normalized),
               'tool_call_ids': [], 'status': 'PRESENT', 'created_at': '2026-09-13T00:00:03Z', 'binding_id': '0'*64}
    _write_binding(handle, binding)
    return handle, task, key, binding


def _write_binding(handle, binding):
    root = handle.capsule / 'agents/session/task_bindings'
    if root.exists():
        for path in root.glob('*.json'):
            path.unlink()
    binding['binding_id'] = host_evidence_binding_hash(binding)
    atomic_write_json(root / f"{binding['binding_id']}.json", binding)


def test_attempt_models_are_distinct_and_usage_is_archive_derived(tmp_path):
    from autoresearch.session_agent.metering import measure_attempt
    handle, task, key, _ = _fixture(tmp_path)
    value = measure_attempt(handle, task, key)
    assert [value[name]['model'] for name in ('requested', 'resolved', 'observed')] == ['asked', 'resolved', 'actual']
    assert value['metrics']['input_tokens'] == 12
    assert value['metrics']['cached_input_tokens'] == 8
    assert value['metrics']['output_tokens'] == 5
    assert value['metrics']['duration_seconds'] == 2
    assert value['metrics']['model_calls'] is None
    assert value['dispatch_count'] == 1
    assert value['metrics']['host_usage_records'] == 1
    assert value['estimated_price'] is None


def test_missing_usage_and_old_request_never_become_zero_or_current_config(tmp_path):
    from autoresearch.session_agent.metering import measure_attempt
    handle, task, key, _ = _fixture(tmp_path, usage=False)
    path = handle.capsule / 'agents/session/requests/session-stock.card-a1.json'
    old = json.loads(path.read_text())
    del old['dispatch_request']
    atomic_write_json(path, old)
    value = measure_attempt(handle, task, key)
    assert value['resolved']['model'] is None
    assert value['resolved']['source'] == 'UNKNOWN_LEGACY_REQUEST'
    assert value['metrics']['input_tokens'] is None
    assert 'dispatch_request' not in json.loads(path.read_text())


@pytest.mark.parametrize('tamper', ['archive', 'engine', 'attempt'])
def test_invalid_binding_fails_closed(tmp_path, tamper):
    from autoresearch.session_agent.metering import measure_attempt
    handle, task, key, binding = _fixture(tmp_path)
    if tamper == 'archive':
        (handle.capsule / 'raw.gz').write_bytes(b'changed')
    else:
        binding[tamper] = 'claude' if tamper == 'engine' else 2
        _write_binding(handle, binding)
    value = measure_attempt(handle, task, key)
    assert value['observed']['model'] is None
    assert value['metrics']['input_tokens'] is None
    assert value['evidence_status'] in {'INVALID', 'MISSING'}


def test_partial_coverage_and_not_dispatched_are_distinct(tmp_path):
    from autoresearch.session_agent.metering import measure_attempt, summarize_attempts
    handle, task, key, _ = _fixture(tmp_path)
    first = measure_attempt(handle, task, key)
    second = measure_attempt(handle, task, {**key, 'attempt': 2, 'state': 'RUNNING'})
    # A frozen handoff exists even when host export is missing.
    first_path = handle.capsule / 'agents/session/requests/session-stock.card-a1.json'
    second_path = first_path.with_name('session-stock.card-a2.json')
    value = json.loads(first_path.read_text())
    value['envelope']['attempt'] = 2
    value['dispatch_request']['attempt'] = 2
    atomic_write_json(second_path, value)
    second = measure_attempt(handle, task, {**key, 'attempt': 2, 'state': 'FAILED'})
    assert second['observed']['model'] is None
    summary = summarize_attempts([first, second])
    assert summary['input_tokens'] == {'value': None, 'observed_total': 12, 'observed_count': 1, 'expected_count': 2, 'coverage': 0.5, 'status': 'PARTIAL'}


def test_mixed_models_remain_explicit_and_missing_fields_remain_null(tmp_path):
    from autoresearch.session_agent.metering import measure_attempt
    handle, task, key, binding = _fixture(tmp_path)
    path = handle.capsule / 'raw.gz'
    rows = [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]
    rows.insert(1, {'ordinal': 1, 'type': 'turn_context', 'payload': {'model': 'other', 'effort': 'high'}})
    del rows[2]['payload']['info']['total_token_usage']['output_tokens']
    path.write_bytes(gzip.compress(('\n'.join(json.dumps(r) for r in rows) + '\n').encode()))
    binding['archive_sha256'] = sha256_file(path)
    _write_binding(handle, binding)
    value = measure_attempt(handle, task, key)
    assert value['observed']['status'] == 'MIXED'
    assert value['observed']['model'] is None
    assert {o['model'] for o in value['observed']['observations']} == {'actual', 'other'}
    assert value['metrics']['output_tokens'] is None
    assert value['metrics']['input_tokens'] == 12


def test_requested_unspecified_is_null_and_sealed_metering_cannot_write(tmp_path):
    from autoresearch.session_agent.metering import materialize_metering, measure_attempt
    handle, task, key, _ = _fixture(tmp_path)
    handle.contract.user_config['resolved_agent_bundle']['declared_roles'] = {}
    assert measure_attempt(handle, task, key)['requested']['model'] is None
    atomic_write_json(handle.capsule / 'verification/ROOT.json', {})
    with pytest.raises(ValueError, match='sealed'):
        materialize_metering(handle)


def test_materialization_writes_sidecar_and_cli_does_not_change_sealed_bytes(tmp_path, monkeypatch, capsys):
    from autoresearch.session_agent.__main__ import main
    from autoresearch.session_agent.evidence import materialize_evidence
    from autoresearch.trace import capsule
    from tests.forensics.test_host_evidence import _running_case
    handle, _, _ = _running_case(tmp_path)
    materialize_evidence(handle)
    sidecar = handle.capsule / 'agents/session/metering.json'
    value = json.loads(sidecar.read_text())
    assert value['dispatch_count'] == 1
    assert value['attempts'][0]['observed']['model'] is None
    atomic_write_json(handle.capsule / 'verification/ROOT.json', {'sealed': True})
    before = {str(p): p.read_bytes() for p in handle.workspace.rglob('*') if p.is_file()}
    monkeypatch.setenv('AUTORESEARCH_ENGINE', 'codex')
    monkeypatch.delenv('AUTORESEARCH_RUN_ID', raising=False)
    monkeypatch.setattr(capsule, 'load_run', lambda run_id: handle)
    assert main(['metering', '--run-id', handle.run_id]) == 0
    assert os.environ.get('AUTORESEARCH_RUN_ID') is None
    assert json.loads(capsys.readouterr().out)['dispatch_count'] == 1
    assert before == {str(p): p.read_bytes() for p in handle.workspace.rglob('*') if p.is_file()}


def test_sidecar_rejects_wrong_metric_fields(tmp_path):
    from autoresearch.contracts.session_metering import validate_metering
    from autoresearch.session_agent.metering import build_metering
    from tests.forensics.test_host_evidence import _running_case
    handle, _, _ = _running_case(tmp_path)
    value = build_metering(handle)
    value['attempts'][0]['metrics']['invented_calls'] = 5
    with pytest.raises(ValueError, match='fields mismatch'):
        validate_metering(value)


def test_binding_must_match_frozen_host_session_and_context(tmp_path):
    from autoresearch.session_agent.metering import measure_attempt
    handle, task, key, binding = _fixture(tmp_path)
    context = Path(handle.workspace) / 'session/dispatch/stock.card-a1.context.json'
    atomic_write_json(context, {'engine': 'codex', 'session_id': 'other-session', 'agent_id': 'other-child'})
    value = measure_attempt(handle, task, key)
    assert value['evidence_status'] == 'INVALID'
    assert value['observed']['model'] is None


def test_overlapping_bound_attempts_do_not_double_count(tmp_path):
    from autoresearch.session_agent.metering import measure_attempt
    handle, task, key, binding = _fixture(tmp_path)
    second = {**binding, 'task_id': 'other.card', 'invocation_id': 'session-other.card-a1'}
    second['binding_id'] = host_evidence_binding_hash(second)
    atomic_write_json(handle.capsule / 'agents/session/task_bindings' / f"{second['binding_id']}.json", second)
    value = measure_attempt(handle, task, key)
    assert value['evidence_status'] == 'INVALID'
    assert value['metrics']['input_tokens'] is None


@pytest.mark.parametrize('field', ['dispatch_count', 'summary_count'])
def test_sidecar_rejects_boolean_counts(tmp_path, field):
    from autoresearch.contracts.session_metering import validate_metering
    from autoresearch.session_agent.metering import build_metering
    from tests.forensics.test_host_evidence import _running_case
    handle, _, _ = _running_case(tmp_path)
    value = build_metering(handle)
    if field == 'dispatch_count':
        value[field] = True
    else:
        value['metrics']['input_tokens']['observed_count'] = False
    with pytest.raises(ValueError):
        validate_metering(value)


def test_metering_rejects_tampered_evidence_denominator(tmp_path):
    from autoresearch.session_agent.evidence import build_evidence_plan
    from autoresearch.session_agent.metering import build_metering
    from tests.forensics.test_host_evidence import _running_case
    handle, _, _ = _running_case(tmp_path)
    plan = build_evidence_plan(handle)
    plan['task_keys'] = []
    with pytest.raises(ValueError):
        build_metering(handle, evidence_plan=plan)
    atomic_write_json(handle.capsule / 'evidence/evidence_plan.json', plan)
    with pytest.raises(ValueError):
        build_metering(handle)

"""Quota evidence keeps real research legs and exempts only impossible ones."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.session_agent import evidence, host_evidence, service
from tests.forensics.test_host_evidence import _running_case, _submission


def _native_thread(tmp_path, kind):
    fixture = Path(__file__).parents[1] / 'trace/fixtures/codex/rollout.jsonl'
    rows = [json.loads(line) for line in fixture.read_text().splitlines()]
    if kind == 'opening':
        rows = rows[:3]
    elif kind == 'partial_tool':
        rows = rows[:4]  # Native web request observed, result not yet reported.
    rows.append({'ordinal': max(row['ordinal'] for row in rows) + 1,
                 'timestamp': '2026-08-27T02:00:14.000Z', 'type': 'event_msg',
                 'payload': {'type': 'error', 'message': "You've hit your usage limit"}})
    path = tmp_path / f'{kind}.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return path, rows[-1]['ordinal']


def _bind(handle, tmp_path, kind, attempt=1):
    source, end = _native_thread(tmp_path, kind)
    return host_evidence.bind_task_transcript(handle.run_id, 'inference.one', attempt, source,
        context_ref='context-main', parent_context_ref=None, session_ref='session-main',
        start_ordinal=0, end_ordinal=end, context_source='MAIN',
        handle_loader=lambda _: handle)


def _quota(handle):
    service.fail(handle.run_id, 'inference.one', 1, 'USAGE_LIMIT', 'usage limit reached',
                 handle_loader=lambda _: handle)


def _key(handle, attempt=1):
    return next(row for row in evidence.build_evidence_plan(handle)['task_keys']
                if row['task_id'] == 'inference.one' and row['attempt'] == attempt)


def test_quota_before_thread_preserves_claim_input_and_failure(tmp_path):
    handle, _, _ = _running_case(tmp_path, role='stock.news')
    _quota(handle)
    key = _key(handle)
    assert key['evidence_kind'] == 'CAPACITY_DEFERRED'
    assert key['requirements'] == ['claim', 'input_snapshot']
    closure = evidence.materialize_evidence(handle)
    assert closure['completeness_ok'] is True
    frozen = handle.capsule / 'evidence/tasks/inference.one/a1/failure.json'
    assert json.loads(frozen.read_text())['error']['code'] == 'USAGE_LIMIT'


def test_quota_after_verified_opening_keeps_bound_transcript(tmp_path):
    handle, _, _ = _running_case(tmp_path, role='stock.news')
    bound = _bind(handle, tmp_path, 'opening')
    _quota(handle)
    key = _key(handle)
    assert 'transcript' in key['requirements']
    assert not {'tool_results', 'source_receipts'} & set(key['requirements'])
    assert host_evidence.transcript_refs_for_task(handle, 'inference.one', 1)[0]['sha256'] == bound['archive_sha256']
    assert evidence.materialize_evidence(handle)['completeness_ok'] is True


def test_partial_web_request_requires_result_and_source_evidence(tmp_path):
    handle, _, _ = _running_case(tmp_path, role='stock.news')
    _bind(handle, tmp_path, 'partial_tool')
    _quota(handle)
    key = _key(handle)
    assert {'transcript', 'tool_results', 'source_receipts'} <= set(key['requirements'])


def test_tampered_bound_archive_does_not_gain_quota_exemption(tmp_path):
    handle, _, _ = _running_case(tmp_path, role='stock.news')
    bound = _bind(handle, tmp_path, 'opening')
    _quota(handle)
    raw = handle.capsule / bound['raw_path']
    raw.write_bytes(raw.read_bytes() + b'tampered')
    key = _key(handle)
    assert {'transcript', 'tool_results', 'source_receipts'} <= set(key['requirements'])
    assert host_evidence.transcript_refs_for_task(handle, 'inference.one', 1)[0]['status'] == 'MISSING'


def test_tampered_binding_fails_closed(tmp_path):
    handle, _, _ = _running_case(tmp_path, role='stock.news')
    bound = _bind(handle, tmp_path, 'opening')
    _quota(handle)
    path = handle.capsule / 'agents/session/task_bindings' / f"{bound['binding_id']}.json"
    value = json.loads(path.read_text())
    value['archive_sha256'] = 'a' * 64
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        evidence.build_evidence_plan(handle)


def test_successful_retry_still_requires_normal_accepted_research_evidence(tmp_path):
    handle, _, _ = _running_case(tmp_path, role='stock.news')
    _quota(handle)
    service.recover_task(handle.run_id, 'inference.one', 1, 'subscription window reset',
                         handle_loader=lambda _: handle)
    claimed = service.claim(handle.run_id, 'inference.one', 2, handle_loader=lambda _: handle,
                            event_recorder=lambda *args, **kwargs: None)
    bound = _bind(handle, tmp_path, 'full', attempt=2)
    output = Path(claimed['result']['claim_receipt']['output_paths']['inference.output'])
    receipt = {'schema_version': 1, 'engine': 'codex', 'session_ref': 'session-main',
               'context_ref': 'context-main', 'parent_context_ref': None,
               'task_id': 'inference.one', 'attempt': 2, 'completed': True,
               'evidence_refs': [bound['evidence_ref']]}
    receipt_id = sha256_bytes(canonical_json(receipt).encode())
    service.submit(handle.run_id, _submission(claimed, output, handle, host_receipt_id=receipt_id),
                   host_receipt=receipt, handle_loader=lambda _: handle,
                   validator=lambda *args: None, event_recorder=lambda *args, **kwargs: None)
    keys = evidence.build_evidence_plan(handle)['task_keys']
    assert keys[0]['evidence_kind'] == 'CAPACITY_DEFERRED'
    assert keys[0]['state'] == 'SUPERSEDED'
    assert 'evidence_kind' not in keys[1]
    assert {'claim', 'input_snapshot', 'outputs', 'accepted_receipt', 'transcript',
            'tool_results', 'source_receipts'} <= set(keys[1]['requirements'])
    assert evidence.materialize_evidence(handle)['completeness_ok'] is True


def test_unbound_but_observed_thread_is_not_a_refusal_before_thread(tmp_path):
    handle, _, _ = _running_case(tmp_path, role='stock.news')
    source, _ = _native_thread(tmp_path, 'partial_tool')
    records = handle.staging / '_dispatch/headless'
    records.mkdir(parents=True)
    (records / 'inference.one.a1.json').write_text(json.dumps({
        'engine': 'codex', 'task_id': 'inference.one', 'attempt': 1,
        'session_id': 'observed-thread', 'transcript_path': str(source),
        'state': 'EXITED', 'exit_code': 1, 'error_class': 'USAGE_LIMIT',
    }))
    _quota(handle)
    key = _key(handle)
    assert {'transcript', 'tool_results', 'source_receipts'} <= set(key['requirements'])

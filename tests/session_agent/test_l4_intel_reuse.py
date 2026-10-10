from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from autoresearch.session_agent import artifacts, host_evidence, store
from autoresearch.session_agent.workflows.scan import build_scan_plan, l4_retry_expansion

from .test_scan_prelude import context, request


def _fixture(tmp_path, monkeypatch):
    from autoresearch.session_agent import intel_reuse
    handle = SimpleNamespace(workspace=tmp_path, capsule=tmp_path / 'capsule',
                             analysis_date='2026-10-08', engine='codex', run_id='run')
    raw, status = 'l4.600519.a1.intel', 'l4.600519.a1.intel_status'
    prompt, bundle, frame = 'scan.l4.600519.a1.prompt', 'scan.l4.source.bundle', 'research.frame'
    aids = [prompt, bundle, frame, 'scan.l4.600519.a1.intel',
            'scan.l4.600519.a1.intel_status', 'scan.l4.600519.a1.intel_doc',
            'scan.l4.600519.a1.intel_bundle']
    hashes = {aid: str(index) * 64 for index, aid in enumerate(aids, 1)}
    tasks = [{"task_id": raw, "role": "scan.l4.intel", "input_artifact_ids": [prompt, frame],
              "output_artifact_ids": [aids[3]]},
             {"task_id": status, "role": None,
              "input_artifact_ids": [bundle, aids[3], frame], "output_artifact_ids": aids[4:]}]
    entries = {task['task_id']: {"state": "SUCCEEDED", "attempt": 1,
               "submission_hash": 'a' * 64, 'spec': task,
               'outputs': [{'artifact_id': aid, 'sha256': hashes[aid]} for aid in task['output_artifact_ids']],
               'claim_receipt': {'input_snapshots': [{'artifact_id': aid, 'sha256': hashes[aid]}
                                  for aid in task['input_artifact_ids']]}} for task in tasks}
    entries['l4.600519.a1.card'] = {'state': 'FAILED', 'error': {'code': 'DOMAIN_VALIDATION'}}
    monkeypatch.setattr(store, 'read_entries', lambda path: entries)
    monkeypatch.setattr(artifacts, 'snapshot_artifact', lambda h, aid: {'artifact_id': aid, 'sha256': hashes[aid]})
    monkeypatch.setattr(artifacts, 'read_bytes', lambda h, aid: b'{"analysis_session":"2026-10-08"}' if aid == frame else b'{"code":"600519","guard":"KEPT","availability_for_card":"INTEL","acquisition":"FULL"}')
    monkeypatch.setattr(host_evidence, '_existing_binding', lambda *args: {'binding_id': 'b' * 64})
    monkeypatch.setattr(host_evidence, '_load_binding_ref', lambda *args: {'created_at': '2026-10-08T15:00:00Z'})
    monkeypatch.setattr('autoresearch.session_agent.evidence_bundle.require_read_evidence', lambda *args: {})
    monkeypatch.setattr('autoresearch.trace.events.verify_event_chain', lambda *args: {'ok': True})
    now = datetime(2026, 10, 8, 15, 15, tzinfo=timezone.utc)
    return intel_reuse, handle, tasks, entries, hashes, now


def test_accepted_same_day_intel_reused_with_exact_frozen_inputs(tmp_path, monkeypatch):
    module, handle, tasks, entries, hashes, now = _fixture(tmp_path, monkeypatch)
    retained = module.accepted_retry_intel(handle, '600519', 1, 'DOMAIN_VALIDATION', tasks, now=now)
    assert retained['intel_task_id'] == 'l4.600519.a1.intel'
    assert retained['status_task_id'] == 'l4.600519.a1.intel_status'
    assert {item['artifact_id'] for item in retained['snapshots']} == set(hashes)


@pytest.mark.parametrize('mutation', ['failed', 'hash', 'output_hash', 'rejected', 'next_day', 'timeout', 'future_binding', 'card_accepted', 'missing_read', 'bad_chain', 'wrong_date'])
def test_untrusted_or_changed_intel_falls_back_to_new_inference(tmp_path, monkeypatch, mutation):
    module, handle, tasks, entries, hashes, now = _fixture(tmp_path, monkeypatch)
    error = 'DOMAIN_VALIDATION'
    if mutation == 'failed':
        entries[tasks[0]['task_id']]['state'] = 'FAILED'
    elif mutation == 'hash':
        hashes['scan.l4.source.bundle'] = 'f' * 64
    elif mutation == 'output_hash':
        hashes['scan.l4.600519.a1.intel'] = 'f' * 64
    elif mutation == 'rejected':
        monkeypatch.setattr(artifacts, 'read_bytes', lambda h, aid: b'{"analysis_session":"2026-10-08"}' if aid == 'research.frame' else b'{"code":"600519","guard":"REJECTED","availability_for_card":"CARD_FALLBACK","acquisition":"FULL"}')
    elif mutation == 'card_accepted':
        entries['l4.600519.a1.card']['state'] = 'SUCCEEDED'
    elif mutation == 'missing_read':
        def unavailable(*args):
            raise ValueError('read evidence unavailable')
        monkeypatch.setattr('autoresearch.session_agent.evidence_bundle.require_read_evidence', unavailable)
    elif mutation == 'bad_chain':
        monkeypatch.setattr('autoresearch.trace.events.verify_event_chain', lambda *args: {'ok': False})
    elif mutation == 'wrong_date':
        handle.analysis_date = '2026-10-07'
    elif mutation == 'next_day':
        now = datetime(2026, 10, 8, 16, 1, tzinfo=timezone.utc)
    elif mutation == 'future_binding':
        now = datetime(2026, 10, 8, 14, 1, tzinfo=timezone.utc)
    else:
        error = 'TIMEOUT'
    assert module.accepted_retry_intel(handle, '600519', 1, error, tasks, now=now) is None


def test_retry_keeps_registered_guard_but_avoids_a_second_intel_session(tmp_path):
    plan = build_scan_plan(request(), context(tmp_path))
    retained = {'intel_task_id': 'l4.600519.a1.intel', 'status_task_id': 'l4.600519.a1.intel_status',
                'intel_artifact_id': 'scan.l4.600519.a1.intel',
                'status_artifact_id': 'scan.l4.600519.a1.intel_status',
                'snapshots': []}
    expansion = l4_retry_expansion(plan, '600519', 2, [], intel_enabled=True, retained_intel=retained)
    assert not any(task['role'] == 'scan.l4.intel' for task in expansion['tasks'])
    guard = next(task for task in expansion['tasks'] if task['task_id'].endswith('.intel_status'))
    assert guard['operation'] == 'scan.l4.intel.status'
    assert guard['dependencies'] == [retained['intel_task_id'], retained['status_task_id']]
    assert retained['intel_artifact_id'] in guard['input_artifact_ids']
    assert 'scan.l4.600519.a2.intel_doc' in guard['output_artifact_ids']


def test_new_guard_reads_declared_old_raw_and_writes_own_attempt(tmp_path, monkeypatch):
    import json

    from autoresearch.session_agent import domain_ops

    from .test_scan_prelude import context

    handle = context(tmp_path)
    stage = handle.staging
    stage.mkdir(parents=True, exist_ok=True)
    (stage / '_l4_tasks.json').write_text(json.dumps({'tasks': {'600519': {'attempt': 2}}}))
    old_raw = stage / 'session_attempts/600519/a1/intel.md'
    old_raw.parent.mkdir(parents=True)
    old_raw.write_text('accepted raw bytes\n')
    monkeypatch.setattr(artifacts, 'read_bytes', lambda h, aid: old_raw.read_bytes())

    def fake_guard(scan_dir, code, **kwargs):
        canonical = scan_dir / f'_l4_intel_{code}.md'
        assert canonical.read_text() == 'accepted raw bytes\n'
        canonical.write_text('new guarded bytes\n')
        return {'action': 'KEPT'}

    monkeypatch.setattr('autoresearch.scan.l4.intel_guard.guard_intel', fake_guard)
    monkeypatch.setattr(domain_ops, '_normalize_intel', lambda *args: None)
    monkeypatch.setattr('autoresearch.scan.l4.intel_status.from_guard',
                        lambda *args, **kwargs: SimpleNamespace(to_dict=lambda: {}))
    monkeypatch.setattr('autoresearch.scan.l4.intel_status.write_status', lambda *args: None)
    monkeypatch.setattr(domain_ops, '_copy_attempt_status', lambda *args: None)
    domain_ops.scan_l4_intel_status(handle, code='600519', claim_sources={},
                                   intel_artifact_id='scan.l4.600519.a1.intel')
    assert old_raw.read_text() == 'accepted raw bytes\n'
    assert (stage / 'session_attempts/600519/a2/intel_doc.md').read_text() == 'new guarded bytes\n'
    assert not (stage / 'session_attempts/600519/a2/intel.md').exists()


def test_retained_intel_stays_accepted_and_authorizes_prior_sources(tmp_path):
    import json

    from autoresearch.news.source_fields import admissible_attempts

    path = tmp_path / 'tasks.json'
    parent = {'subject': '600519', 'attempt': 1}
    raw, status, card = 'l4.600519.a1.intel', 'l4.600519.a1.intel_status', 'l4.600519.a1.card'
    entries = {
        raw: {'state': 'SUCCEEDED', 'attempt': 1, 'spec': {'task_id': raw, 'parent_task': parent,
              'expected_output_contract': 'scan.l4.intel.v1', 'dependencies': []}},
        status: {'state': 'SUCCEEDED', 'attempt': 1, 'spec': {'task_id': status, 'parent_task': parent,
                 'expected_output_contract': 'scan.l4.intel_status.v1', 'dependencies': [raw]}},
        card: {'state': 'FAILED', 'attempt': 1, 'spec': {'task_id': card, 'parent_task': parent,
                'expected_output_contract': 'stock.lite.v1', 'dependencies': [status]}},
        'l4.600519.a2.intel_status': {'state': 'RUNNING', 'attempt': 1,
            'spec': {'parent_task': {'subject': '600519', 'attempt': 2}, 'dependencies': [raw, status]}},
    }
    path.write_text(json.dumps({'schema_version': 1, 'tasks': entries}))
    store.prepare_l4_retry(path, '600519', 1, retained_task_ids=[raw, status])
    values = store.read_entries(path)
    assert values[raw]['state'] == values[status]['state'] == 'SUCCEEDED'
    assert values[card]['state'] == 'WAITING_RETRY'
    assert admissible_attempts(values, 'l4.600519.a2.intel_status', 1)[raw] == 1


@pytest.mark.parametrize('retention', ['missing', 'failed', 'other_subject'])
def test_retry_cannot_retain_an_unaccepted_or_unrelated_child(tmp_path, retention):
    import json
    path = tmp_path / 'tasks.json'
    key = 'l4.600519.a1.intel'
    entry = {'state': 'FAILED' if retention == 'failed' else 'SUCCEEDED',
             'spec': {'task_id': key, 'parent_task': {'subject': '000001' if retention == 'other_subject' else '600519', 'attempt': 1},
                      'expected_output_contract': 'scan.l4.intel.v1'}}
    path.write_text(json.dumps({'schema_version': 1, 'tasks': {key: entry}}))
    before = path.read_bytes()
    with pytest.raises((ValueError, store.TaskConflict)):
        store.prepare_l4_retry(path, '600519', 1,
                               retained_task_ids=['missing' if retention == 'missing' else key])
    assert path.read_bytes() == before

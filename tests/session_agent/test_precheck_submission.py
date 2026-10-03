"""SYNTHETIC candidates and exported transcripts; never real host acceptance."""
import json
from pathlib import Path

import pytest

from autoresearch.common.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.contracts.session_plan import plan_hash
from autoresearch.session_agent import artifacts, host_evidence, service, store
from tests.forensics.test_host_evidence import _inference_plan, _rollout
from tests.scan.test_research_card_migration import EARLY_STOP_CARD, FULL_CARD
from tests.session_agent.test_service import _handle, _request

EARLY = EARLY_STOP_CARD.replace('000001', '600519').replace('Underweight', 'Hold')
FULL = FULL_CARD.replace('600000', '600519').replace('估值不透支 ✗', '估值不透支 ✓') + '\n进入P4倾向: Overweight\n'


def case(tmp_path, text=EARLY, *, declare_deep=True, multi=False, current=False, task_id="inference.one"):
    handle = _handle(tmp_path)
    request = _request()
    def planner(request, handle):
        plan = _inference_plan(request, handle, input_id='stock.deep')
        task = plan['tasks'][0]
        task['task_id'] = task_id
        task['output_artifact_ids'] = ['stock.card.output']
        if not declare_deep:
            task['input_artifact_ids'] = ['stock.slim']
        if current:
            task['input_artifact_ids'].append('research.frame')
        if multi:
            task.update(role='stock.solvency', expected_output_contract='stock.section.v1',
                        output_artifact_ids=['section.one', 'section.two'])
        plan['plan_hash'] = plan_hash(plan)
        return plan
    def register(request, handle, plan):
        deep = handle.staging / 'deep.md'
        deep.write_text('SYNTHETIC frozen deep facts')
        artifacts.register_artifact(handle, 'stock.deep', deep, 'READ')
        artifacts.register_artifact(handle, 'stock.slim', deep, 'READ')
        if current:
            from autoresearch.common.execution_math import build_decision_frame
            frame = handle.staging / 'frame.json'
            atomic_write_json(frame, build_decision_frame(analysis_session=handle.analysis_date,
                knowledge_cutoff='2026-09-13T12:00:00Z', venue='XSHG', research_depth='LITE',
                usage='standalone', sessions=[], calendar_quality='UNKNOWN'))
            artifacts.register_artifact(handle, 'research.frame', frame, 'READ')
        for key in plan['tasks'][0]['output_artifact_ids']:
            artifacts.register_artifact(handle, key, handle.staging / f'{key}.md', 'WRITE')
    service.begin(request, begin_capsule=lambda _: handle, planner=planner, artifact_registrar=register)
    atomic_write_json(handle.capsule / 'verification/profile.json', {'card_rules_version': 'skills-gap-v3' if current else 'skills-gap-v2'})
    claimed = service.claim(handle.run_id, task_id, 1, handle_loader=lambda _: handle,
                            event_recorder=lambda *a, **k: None)['result']
    paths = claimed['claim_receipt']['output_paths']
    for path in paths.values():
        Path(path).write_text(text)
    submission = {'schema_version': 1, 'envelope': claimed['envelope'], 'plan_hash': claimed['plan_hash'],
                  'outputs': [{'artifact_id': key, 'sha256': sha256_bytes(Path(path).read_bytes())}
                              for key, path in paths.items()], 'host_receipt_id': None}
    return handle, submission, paths


def check(handle, submission, receipt=None):
    return service.precheck(handle.run_id, submission, handle_loader=lambda _: handle, host_receipt=receipt)['result']


def snapshot(handle):
    return {str(p.relative_to(handle.workspace)): p.read_bytes() for p in handle.workspace.rglob('*')
            if p.is_file() and p.suffix != '.lock' and '/session_outputs/precheck/' not in str(p)
            and '/evidence/card_claim_uses/' not in str(p)}


def bind(tmp_path, handle, submission, observed='SYNTHETIC frozen deep facts'):
    source = _rollout(tmp_path)
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    ordinal = max(row['ordinal'] for row in rows) + 1
    if observed is not None:
        rows.extend([
            {'timestamp': '2026-09-13T01:02:03Z', 'ordinal': ordinal, 'type': 'response_item',
             'payload': {'type': 'function_call', 'name': 'read_file', 'call_id': 'synthetic-read',
                         'arguments': json.dumps({'path': str(artifacts.artifact_path(handle, 'stock.deep'))})}},
            {'timestamp': '2026-09-13T01:02:04Z', 'ordinal': ordinal + 1, 'type': 'response_item',
             'payload': {'type': 'function_call_output', 'call_id': 'synthetic-read', 'output': observed}},
        ])
    source.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
    binding = host_evidence.bind_task_transcript(handle.run_id, submission['envelope']['task_id'], 1, source,
        context_ref='synthetic-agent', parent_context_ref='session-main', session_ref='session-main',
        start_ordinal=0, end_ordinal=max(row['ordinal'] for row in rows), context_source='SUBAGENT',
        handle_loader=lambda _: handle)
    receipt = {'schema_version': 1, 'engine': 'codex', 'session_ref': 'session-main',
               'context_ref': 'synthetic-agent', 'parent_context_ref': 'session-main',
               'task_id': submission['envelope']['task_id'], 'attempt': 1, 'completed': True,
               'evidence_refs': [binding['evidence_ref']]}
    submission['host_receipt_id'] = sha256_bytes(canonical_json(receipt).encode())
    return receipt, binding


@pytest.mark.parametrize('text,domain,error', [(EARLY, 'PASS', None), (FULL, 'PASS', None),
    ('analysis without rating', 'FAIL', 'Rating'),
    (FULL.replace('估值不透支 ✓', '估值不透支 ✗'), 'FAIL', 'PASS')])
def test_domain_and_unbound_host_are_independent_and_read_only(tmp_path, text, domain, error):
    handle, submission, _ = case(tmp_path, text)
    before = snapshot(handle)
    result = check(handle, submission)
    assert result['domain_status'] == domain
    assert result['host_evidence_status'] == 'PENDING_FINAL_BINDING'
    assert result['can_submit'] is False
    assert result['candidate_sha256'] == sha256_bytes(text.encode())
    assert result['schema_version'] == 1 and result['attempt'] == 1
    if error:
        assert error.lower() in ' '.join(result['errors']).lower()
    assert snapshot(handle) == before
    assert not (handle.staging / 'session_outputs/accepted').exists()
    assert check(handle, submission) == result


def test_full_card_missing_declared_deep_is_domain_failure(tmp_path):
    handle, submission, _ = case(tmp_path, FULL, declare_deep=False)
    result = check(handle, submission)
    assert result['domain_status'] == 'FAIL'
    assert 'declared task input' in ' '.join(result['errors'])


@pytest.mark.parametrize('observed,expected', [('SYNTHETIC frozen deep facts', 'VERIFIED'), ('SYNTHETIC', 'INVALID'), (None, 'INVALID')])
def test_full_card_requires_actual_bound_full_deep_read(tmp_path, observed, expected):
    handle, submission, _ = case(tmp_path, FULL)
    receipt, _ = bind(tmp_path, handle, submission, observed)
    before = snapshot(handle)
    result = check(handle, submission, receipt)
    assert result['domain_status'] == 'PASS'
    assert result['host_evidence_status'] == expected
    assert result['can_submit'] is (expected == 'VERIFIED')
    assert snapshot(handle) == before


@pytest.mark.parametrize('damage', ['receipt', 'archive', 'identity', 'receipt_hash', 'receipt_absent'])
def test_broken_host_evidence_is_invalid_not_pending(tmp_path, damage):
    handle, submission, _ = case(tmp_path)
    receipt, binding = bind(tmp_path, handle, submission)
    if damage == 'receipt':
        receipt['attempt'] = 2
    elif damage == 'archive':
        (handle.capsule / binding['raw_path']).write_bytes(b'bad archive')
    elif damage == 'identity':
        receipt['context_ref'] = 'wrong'
    elif damage == 'receipt_hash':
        submission['host_receipt_id'] = '0' * 64
    else:
        receipt = None
    result = check(handle, submission, receipt)
    assert result['host_evidence_status'] == 'INVALID'
    assert result['can_submit'] is False and result['errors']


@pytest.mark.parametrize('damage', ['stale', 'terminal', 'plan', 'abandoned', 'legacy', 'input'])
def test_invalid_attempt_or_layout_refused_without_capture(tmp_path, damage):
    handle, submission, _ = case(tmp_path)
    owner = handle.workspace / 'session/tasks.json'
    payload = json.loads(owner.read_text())
    if damage == 'stale':
        submission['envelope']['attempt'] = 2
    elif damage == 'terminal':
        payload['tasks']['inference.one']['state'] = 'SUCCEEDED'
        atomic_write_json(owner, payload)
    elif damage == 'plan':
        submission['plan_hash'] = '0' * 64
    elif damage == 'abandoned':
        from autoresearch.session_agent.executors import mailbox
        mailbox.abandon_request(handle.staging, 'inference.one', 1, reason='SYNTHETIC')
    elif damage == 'legacy':
        payload.pop('output_layout_version')
        atomic_write_json(owner, payload)
    else:
        artifacts.artifact_path(handle, 'stock.deep').write_text('mutated')
    before = snapshot(handle)
    with pytest.raises((ValueError, RuntimeError)):
        check(handle, submission)
    assert snapshot(handle) == before
    assert not (handle.staging / 'session_outputs/precheck').exists()


def test_real_candidate_hash_and_submit_recapture(tmp_path):
    handle, submission, paths = case(tmp_path)
    first = check(handle, submission)
    output = Path(next(iter(paths.values())))
    output.write_text(EARLY + '\nSYNTHETIC revision\n')
    second = check(handle, submission)
    assert second['candidate_sha256'] == sha256_bytes(output.read_bytes())
    assert second['candidate_sha256'] != first['candidate_sha256']
    assert second['domain_status'] == 'FAIL' and 'hash mismatch' in ' '.join(second['errors'])
    with pytest.raises(ValueError, match='hash mismatch'):
        service.submit(handle.run_id, submission, handle_loader=lambda _: handle,
                       event_recorder=lambda *a, **k: None)
    submission['outputs'][0]['sha256'] = second['candidate_sha256']
    assert check(handle, submission)['domain_status'] == 'PASS'


def test_multioutput_hash_is_canonical_artifact_to_hash_digest(tmp_path):
    handle, submission, _ = case(tmp_path, '置信度: 中\n', multi=True)
    result = check(handle, submission)
    expected = {row['artifact_id']: row['sha256'] for row in submission['outputs']}
    assert result['candidate_sha256'] == sha256_bytes(canonical_json(expected).encode())
    assert result['domain_status'] == 'PASS'
    submission['outputs'].reverse()
    assert check(handle, submission)['candidate_sha256'] == result['candidate_sha256']


def test_cli_precheck_uses_public_service(tmp_path, monkeypatch, capsys):
    from autoresearch.session_agent.__main__ import main
    handle, submission, _ = case(tmp_path)
    path = tmp_path / 'submission.json'
    atomic_write_json(path, submission)
    monkeypatch.setenv('AUTORESEARCH_RUN_ID', handle.run_id)
    from autoresearch.trace import capsule
    monkeypatch.setattr(capsule, 'require_active_run', lambda _: handle)
    assert main(['precheck', '--run-id', handle.run_id, '--submission-file', str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['command'] == 'precheck' and result['result']['domain_status'] == 'PASS'


def test_current_claim_audit_retains_rejected_candidates_without_acceptance(tmp_path):
    from autoresearch.news.material_claims import bind_material_claim
    from tests.common.test_card_decision_v3 import decision_text
    from tests.news.test_card_claims import usage

    text = decision_text(subject='600519.SS', venue='XSHG', rating='Buy', deviation='风险管理')
    mapping = {'schema_version': 1, 'declarations': [], 'uses': [usage(
        {'claim_id': 'claim', 'statement_sha256': sha256_bytes('公司确定受益'.encode())},
        'gates.业绩真兑现', 'REQUIRED')]}
    text += '\n```decision-claim-uses-v1\n' + json.dumps(mapping, ensure_ascii=False) + '\n```\n'
    handle, submission, paths = case(tmp_path, text, current=True)
    bind_material_claim(handle.capsule, engine=handle.engine, run_id=handle.run_id,
        task_id='inference.one', attempt=1, claim_id='claim', statement='公司确定受益',
        source_receipt_ids=[], quote_refs=[], calculation_ids=[], review_receipt_ids=[])
    before = snapshot(handle)
    result = check(handle, submission)
    assert result['domain_status'] == 'FAIL'
    assert 'required material support' in ' '.join(result['errors'])
    audits = handle.capsule / 'evidence/card_claim_uses'
    original = {p.name: p.read_bytes() for p in audits.glob('*.json')}
    assert len(original) == 1
    row = json.loads(next(iter(original.values())))
    assert row['identity']['task_id'] == 'inference.one'
    assert row['card_sha256'] == result['candidate_sha256']
    assert row['frame_sha256'] == artifacts.snapshot_artifact(handle, 'research.frame')['sha256']
    assert snapshot(handle) == before
    assert check(handle, submission) == result
    assert {p.name: p.read_bytes() for p in audits.glob('*.json')} == original
    path = Path(paths['stock.card.output'])
    path.write_text(text + '\nSYNTHETIC revised candidate\n')
    submission['outputs'][0]['sha256'] = sha256_bytes(path.read_bytes())
    revised = check(handle, submission)
    assert revised['candidate_sha256'] != result['candidate_sha256']
    assert len(list(audits.glob('*.json'))) == 2
    for name, data in original.items():
        assert (audits / name).read_bytes() == data
    assert store.read_states(handle.workspace / 'session/tasks.json')['inference.one'] == 'RUNNING'
    assert not (handle.staging / 'session_outputs/accepted').exists()


@pytest.mark.parametrize('damage', ['missing', 'symlink', 'hardlink', 'extra'])
def test_unsafe_candidate_has_no_invented_hash_or_state_change(tmp_path, damage):
    import os
    handle, submission, paths = case(tmp_path)
    path = Path(paths['stock.card.output'])
    if damage == 'missing':
        path.unlink()
    elif damage == 'symlink':
        path.unlink()
        path.symlink_to(artifacts.artifact_path(handle, 'stock.deep'))
    elif damage == 'hardlink':
        os.link(path, tmp_path / 'linked-candidate')
    else:
        (path.parent / 'undeclared.md').write_text('SYNTHETIC')
    before = snapshot(handle)
    with pytest.raises((ValueError, OSError)):
        check(handle, submission)
    assert snapshot(handle) == before
    assert not (handle.staging / 'session_outputs/accepted').exists()


def test_precheck_calls_none_of_the_mutating_acceptance_boundaries(tmp_path, monkeypatch):
    from autoresearch.session_agent import evidence
    handle, submission, _ = case(tmp_path)
    receipt, _ = bind(tmp_path, handle, submission)
    def forbidden(*args, **kwargs):
        pytest.fail('precheck entered a mutating acceptance boundary')
    for module, names in ((service, ['submit', '_freeze_json']), (store, ['accept']),
                           (host_evidence, ['observe_activity', 'bind_task_transcript']),
                           (evidence, ['freeze_receipt'])):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    assert check(handle, submission, receipt)['can_submit'] is True


def test_deterministic_stock_validate_reuses_canonical_producer_and_bound_full_read(tmp_path):
    from autoresearch.session_agent.domain_ops import stock_validate
    from autoresearch.session_agent.evidence_bundle import require_read_evidence

    handle, submission, _ = case(tmp_path, FULL, task_id='stock.card')
    receipt, _ = bind(tmp_path, handle, submission)
    accepted = service.submit(handle.run_id, submission, host_receipt=receipt,
        handle_loader=lambda _: handle, event_recorder=lambda *args, **kwargs: None)
    assert accepted['result']['receipt']['task_id'] == 'stock.card'
    # The existing reader resolves the original task and current accepted attempt
    # when deterministic stock.validate only supplies its compact domain contract.
    compact_task = {'expected_output_contract': 'stock.lite.v1'}
    assert require_read_evidence(handle, compact_task, {'outputs': []}, 'stock.deep')['call_id'] == 'synthetic-read'
    result = stock_validate(handle)
    assert result['rating'] == 'Overweight'
    assert result['card_sha256'] == submission['outputs'][0]['sha256']


def test_compact_domain_check_does_not_grant_undeclared_canonical_input(tmp_path):
    from autoresearch.session_agent.validation import _declared_deep_input

    handle, _, _ = case(tmp_path, FULL, task_id='stock.card', declare_deep=False)
    with pytest.raises(ValueError, match='declared task input'):
        _declared_deep_input(handle, {'expected_output_contract': 'stock.lite.v1'}, 'stock.deep')

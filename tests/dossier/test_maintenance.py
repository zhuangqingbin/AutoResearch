"""Dossier maintenance keeps published state authoritative and refuses broken history."""
import json

import pytest

from autoresearch.common.atomic import sha256_bytes
from autoresearch.dossier import delta, reconcile, schema
from tests.dossier.test_delta import _mk_dossier
from tests.dossier.test_reconcile import _fake_fetch


@pytest.fixture
def published(monkeypatch, tmp_path):
    path = _mk_dossier()
    state = {'payload': path.read_bytes()}
    monkeypatch.setattr(schema, 'read_committed_bytes', lambda *a, **k: state['payload'])
    monkeypatch.setattr(schema.ws, 'context_root', lambda: tmp_path / 'context_codex')
    return path, state


def test_reconcile_readback_uses_maintenance_not_old_publication(published):
    path, state = published
    before = state['payload']
    result = reconcile.reconcile_one('300857', '20260630', '2026-09-30', fetch=_fake_fetch())
    assert result['recorded']
    assert schema.read_dossier_bytes('300857') != before
    assert schema.read_dossier_bytes('300857') == path.read_bytes()
    path.write_text('untrusted mirror')
    assert '季度对账 20260630' in schema.read_dossier_text('300857')


def test_scan_delta_readback_and_replay(published, tmp_path):
    from autoresearch.session_agent.replay_adapters.services import replay_operation_evidence
    _, state = published
    result = delta.record_scan_delta('300857', '2026-09-30', rating='Hold', scan_root=tmp_path)
    assert schema.read_dossier_bytes('300857') != state['payload']
    assert '入围:评级 Hold' in schema.read_dossier_text('300857')
    replay = replay_operation_evidence(schema.DOSSIER_DIR / '_operation_evidence' / result['operation_id'], tmp_path / 'replay')
    assert replay['status'] == 'MATCH'


def test_corrupted_operation_never_falls_back_to_mirror(published):
    reconcile.reconcile_one('300857', '20260630', '2026-09-30', fetch=_fake_fetch())
    operation = next((schema.DOSSIER_DIR / '_operation_evidence').glob('*/evidence.json'))
    value = json.loads(operation.read_text())
    candidate = next(row for row in value['output_refs'] if row['artifact_id'] == 'dossier.candidate')
    (operation.parent / candidate['captured_path']).write_text('tamper')
    with pytest.raises(ValueError, match='hash mismatch'):
        schema.read_dossier_bytes('300857')


def test_new_publication_base_does_not_apply_old_maintenance(published):
    _, state = published
    reconcile.reconcile_one('300857', '20260630', '2026-09-30', fetch=_fake_fetch())
    state['payload'] += b'\nnew published base\n'
    assert schema.read_dossier_bytes('300857') == state['payload']


def test_concurrent_cas_refuses_stale_opening(published):
    from autoresearch.dossier import maintenance
    before = schema.read_dossier_bytes('300857')
    first = reconcile.reconcile_one('300857', '20260630', '2026-09-30', fetch=_fake_fetch())
    with pytest.raises(RuntimeError, match='CONFLICT'):
        maintenance.commit('300857', before, sha256_bytes(before), first['operation_id'])
    assert '季度对账' in schema.read_dossier_text('300857')


def test_reconcile_explicit_updates_without_source_remain_unknown(tmp_path):
    from autoresearch.dossier import facts
    from tests.dossier.test_facts import CUTOFF, fact
    path = _mk_dossier()
    result = reconcile.reconcile_one('300857', '20260630', '2026-09-30', fetch=_fake_fetch(),
                                     fact_updates=[fact()], knowledge_cutoff=CUTOFF)
    ledger = facts.parse_ledger(path.read_text())
    assert ledger['facts'] == []
    assert ledger['history'][-1]['change'] == 'MISSING_SOURCE'
    assert result['fact_delta']['reuse']['coverage'] == {'declared': 0, 'reusable': 0}
    from autoresearch.session_agent.replay_adapters.services import replay_operation_evidence
    replay = replay_operation_evidence(schema.DOSSIER_DIR / '_operation_evidence' / result['operation_id'], tmp_path / 'replay')
    assert replay['status'] == 'MATCH'


def test_scan_rechecks_expired_existing_fact_without_resubmission(tmp_path):
    from autoresearch.dossier import facts
    from tests.dossier.test_facts import CUTOFF, fact
    path = _mk_dossier()
    old = dict(fact('HYPOTHESIS'), expires_at=CUTOFF)
    ledger = facts.empty_ledger('300857')
    ledger['facts'] = [old]
    path.write_text(facts.replace_ledger(path.read_text(), ledger))
    result = delta.record_scan_delta('300857', '2026-09-30', rating='Hold', scan_root=tmp_path,
                                     knowledge_cutoff=CUTOFF)
    new = facts.parse_ledger(path.read_text())
    assert new['facts'] == [old]
    assert new['history'][-1]['change'] == 'EXPIRED'
    assert result['fact_delta']['reuse']['coverage']['reusable'] == 0


def test_no_model_verdict_parameter_is_accepted():
    from tests.dossier.test_facts import CUTOFF, fact
    _mk_dossier()
    with pytest.raises(TypeError):
        reconcile.reconcile_one('300857', '20260630', '2026-09-30', fetch=_fake_fetch(),
                                fact_updates=[fact()], knowledge_cutoff=CUTOFF, evidence={'claim-1': {'verdict': 'PASS'}})


@pytest.mark.parametrize('provider,expected', [('registered', 'ADDED'),
                                            ('registered-missing-event', 'MISSING_SOURCE'),
                                            ('registered-changed-inputs', 'MISSING_SOURCE'),
                                            ('deterministic', 'MISSING_SOURCE'),
                                            ('host_tool', 'MISSING_SOURCE')])
def test_fact_update_uses_frozen_sources_and_replays_without_live_capsule(tmp_path, monkeypatch, provider, expected):
    import shutil

    from autoresearch.dossier import facts
    from autoresearch.session_agent import artifacts
    from autoresearch.session_agent.replay_adapters.services import replay_operation_evidence
    from autoresearch.trace import capsule
    from tests.dossier.test_facts import fact
    from tests.news.test_frozen_claim_sources import CUTOFF, TEXT, frame, setup_claim

    path = _mk_dossier(code='600000')
    if provider.startswith('registered'):
        from tests.news.test_source_fields import bind, produce, setup_run
        handle, task_id, receipt = setup_run(tmp_path)
        claim = bind(handle, task_id, receipt, produce(handle, task_id, receipt))
        claim_id = claim['claim_id']
    else:
        handle, _, _, _ = setup_claim(tmp_path, provider=provider)
        handle.workspace = tmp_path
        handle.staging = tmp_path / 'staging'
        handle.staging.mkdir()
        task_id, claim_id = 'intel.600000', 'claim'
    source = handle.staging / 'frame.json'
    source.write_text(json.dumps(frame()))
    artifacts.register_artifact(handle, 'research.frame', source, 'READ')
    owner = handle.workspace / 'session/tasks.json'
    if provider.startswith('registered'):
        document = json.loads(owner.read_text())
        document['tasks'][task_id]['state'] = 'SUCCEEDED'
        if provider == 'registered-missing-event':
            (handle.capsule / 'events/events.jsonl').unlink()
        elif provider == 'registered-changed-inputs':
            document['tasks'][task_id]['claim_receipt']['input_snapshots'] = []
    else:
        document = {'schema_version': 1, 'engine': handle.engine, 'run_id': handle.run_id,
                    'tasks': {task_id: {'state': 'SUCCEEDED', 'attempt': 1,
                    'spec': {'task_id': task_id, 'subject': '600000', 'dependencies': []}}}}
    owner.write_text(json.dumps(document))
    monkeypatch.setenv('AUTORESEARCH_RUN_ID', handle.run_id)
    monkeypatch.setattr(capsule, 'require_active_run', lambda _: handle)
    row = dict(fact(), statement=TEXT, source_claim_ids=[claim_id],
               effective_date='2026-09-01', available_at=CUTOFF)
    result = reconcile.reconcile_one('600000', '20260630', '2026-09-02', fetch=_fake_fetch(), fact_updates=[row])
    ledger = facts.parse_ledger(path.read_text())
    assert ledger['history'][-1]['change'] == expected
    assert bool(ledger['facts']) == (provider == 'registered')
    shutil.rmtree(handle.capsule)
    source.write_text('changed live frame')
    evidence = schema.DOSSIER_DIR / '_operation_evidence' / result['operation_id']
    assert replay_operation_evidence(evidence, tmp_path / 'replay-fact')['status'] == 'MATCH'


def test_real_committed_pointer_and_concurrent_maintenance(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from autoresearch.contracts.publication import publication_bundle_hash
    from autoresearch.dossier import maintenance
    from tests.forensics.test_publication_transaction import _case, _execute

    path = _mk_dossier()
    case = _case(tmp_path / 'publication', state=path.read_bytes())
    case.bundle['state_mutations'][0]['target_key'] = 'dossier.stock.300857'
    case.bundle['state_mutations'][0]['apply_policy'] = 'CAS_REPLACE'
    case.bundle['bundle_hash'] = publication_bundle_hash(case.bundle)
    _execute(case)
    monkeypatch.setattr(schema.ws, 'context_root', lambda: case.states.parent)
    # Publication and maintenance deliberately share the same target lock root.
    case.states.rename(case.states.parent / '_published_state')
    monkeypatch.setattr(schema.ws, 'run_reports_root', lambda _: case.reports)
    assert schema.read_dossier_bytes('300857') == path.read_bytes()
    barrier = Barrier(2)
    original = reconcile.render_reconcile_candidate
    def render(*args, **kwargs):
        value = original(*args, **kwargs)
        barrier.wait(timeout=10)
        return value
    monkeypatch.setattr(reconcile, 'render_reconcile_candidate', render)
    def run(period):
        try:
            return reconcile.reconcile_one('300857', period, '2026-09-30', fetch=_fake_fetch())
        except maintenance.MaintenanceConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, ['20260331', '20260630']))
    assert results.count('conflict') == 1
    assert schema.read_dossier_bytes('300857') == path.read_bytes()
    assert schema.read_dossier_text('300857').count('季度对账') == 2


@pytest.mark.parametrize('change', ['engine', 'subject', 'status', 'effects', 'opening', 'operation'])
def test_validly_hashed_wrong_operation_cannot_enter_maintenance_chain(published, change):
    from autoresearch.trace.operation_evidence import (
        load_operation_evidence,
        record_operation_evidence,
    )

    result = reconcile.reconcile_one('300857', '20260630', '2026-09-30', fetch=_fake_fetch())
    root, value = load_operation_evidence(schema.DOSSIER_DIR / '_operation_evidence' / result['operation_id'])
    inputs = {row['artifact_id']: (root / row['captured_path']).read_bytes() for row in value['input_refs']}
    outputs = {row['artifact_id']: (root / row['captured_path']).read_bytes() for row in value['output_refs']}
    if change == 'subject':
        value['parameters']['code'] = '600000'
    if change == 'effects':
        value['effects'][0]['after_sha256'] = '0' * 64
    if change == 'opening':
        inputs['dossier.opening'] += b'\nnot opening\n'
    bad = record_operation_evidence('broker.reconcile' if change == 'operation' else 'dossier.reconcile',
        parameters=value['parameters'], inputs=inputs, outputs=outputs, effects=value['effects'],
        code_paths=[__file__], evidence_root=schema.DOSSIER_DIR / '_operation_evidence',
        engine='claude' if change == 'engine' else 'codex',
        status='FAILED' if change == 'status' else 'SUCCEEDED', error={} if change == 'status' else None)
    head = schema.DOSSIER_DIR / '_maintenance/300857.json'
    value = json.loads(head.read_text())
    value['operations'] = [bad['operation_id']]
    head.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        schema.read_dossier_text('300857')


def test_new_skeleton_freezes_visible_bytes_separately_from_publication_cas(tmp_path, monkeypatch):
    from autoresearch.session_agent import domain_ops
    from tests.forensics.test_sector_dossier_replay import _handle, _request

    handle = _handle(tmp_path, _request('dossier-init', 'INIT', '600519'))
    path = _mk_dossier(code='600519', initiated=False)
    opening = path.read_bytes()
    monkeypatch.setattr(schema, 'read_dossier_snapshot', lambda _: (opening, 'a' * 64))
    snapshot = domain_ops.collect_dossier_skeleton_snapshot(handle, scan_root=tmp_path / 'empty')
    assert snapshot['schema_version'] == 2
    assert snapshot['publication_base_sha256'] == 'a' * 64
    prefetch = tmp_path / 'prefetch.json'
    prefetch.write_text('{}')
    rendered = domain_ops.render_dossier_skeleton_snapshot(snapshot, prefetch_path=prefetch,
        output_dir=tmp_path / 'out', scratch_root=tmp_path / 'scratch')
    permissions = json.loads(rendered['permissions'].read_text())
    assert permissions['opening_target_sha256'] == sha256_bytes(opening)
    assert permissions['publication_base_sha256'] == 'a' * 64
    # Historical v1 snapshots keep the old field set and replay semantics.
    legacy = dict(snapshot, schema_version=1)
    legacy.pop('publication_base_sha256')
    rendered = domain_ops.render_dossier_skeleton_snapshot(legacy, prefetch_path=prefetch,
        output_dir=tmp_path / 'old', scratch_root=tmp_path / 'old-scratch')
    assert 'publication_base_sha256' not in json.loads(rendered['permissions'].read_text())


def test_reconcile_cli_accepts_typed_updates_but_not_model_verdicts(tmp_path, monkeypatch):
    from tests.dossier.test_facts import CUTOFF, fact
    source = tmp_path / 'facts.json'
    source.write_text(json.dumps([fact()]))
    seen = []
    monkeypatch.setattr(reconcile, 'reconcile_one', lambda *args, **kwargs: seen.append((args, kwargs)) or {'updated': True})
    assert reconcile.main(['20260630', '--code', '300857', '--today', '2026-09-30',
        '--fact-updates', str(source), '--knowledge-cutoff', CUTOFF]) == 0
    assert seen[0][1] == {'fact_updates': [fact()], 'knowledge_cutoff': CUTOFF}
    source.write_text(json.dumps([dict(fact(), verdict='PASS')]))
    with pytest.raises(ValueError, match='fact fields'):
        reconcile.main(['20260630', '--code', '300857', '--fact-updates', str(source)])
    assert len(seen) == 1


def test_full_chain_rejects_reordering_and_missing_predecessor(published):
    reconcile.reconcile_one('300857', '20260331', '2026-09-29', fetch=_fake_fetch())
    reconcile.reconcile_one('300857', '20260630', '2026-09-30', fetch=_fake_fetch())
    path = schema.DOSSIER_DIR / '_maintenance/300857.json'
    head = json.loads(path.read_text())
    assert len(head['operations']) == 2
    for operations in (list(reversed(head['operations'])), head['operations'][1:]):
        path.write_text(json.dumps(dict(head, operations=operations)))
        with pytest.raises(ValueError, match='opening chain mismatch'):
            schema.read_dossier_text('300857')


def _dossier_publication_pair(tmp_path, monkeypatch):
    from autoresearch.contracts.publication import publication_bundle_hash
    from autoresearch.session_agent import artifacts
    from tests.forensics.test_publication_transaction import _case, _execute

    path = _mk_dossier()
    first = _case(tmp_path / 'first', state=path.read_bytes())
    first.handle.contract.run_kind = first.bundle['run_kind'] = 'dossier-init'
    first.bundle['state_mutations'][0].update(target_key='dossier.stock.300857', apply_policy='CAS_REPLACE')
    first.bundle['bundle_hash'] = publication_bundle_hash(first.bundle)
    first.states = tmp_path / 'context_codex/_published_state'
    _execute(first)
    monkeypatch.setattr(schema.ws, 'context_root', lambda: first.states.parent)
    monkeypatch.setattr(schema.ws, 'run_reports_root', lambda _: first.reports)
    opening, base = schema.read_dossier_snapshot('300857')
    second = _case(tmp_path / 'second', state=opening + b'\nnew publication\n')
    second.states, second.reports = first.states, first.reports
    second.handle.run_id = second.bundle['run_id'] = '20260915T120000000000Z'
    second.handle.contract.run_kind = second.bundle['run_kind'] = 'dossier-init'
    second.bundle['state_mutations'][0].update(target_key='dossier.stock.300857',
        apply_policy='CAS_REPLACE', expected_before_hash=base)
    second.bundle['bundle_hash'] = publication_bundle_hash(second.bundle)
    (second.handle.workspace / 'session').mkdir()
    (second.handle.workspace / 'session/request.json').write_text(json.dumps({'subject': '300857'}))
    permissions = second.handle.workspace / 'permissions.json'
    permissions.write_text(json.dumps({'schema_version': 2, 'opening_target_sha256': sha256_bytes(opening),
        'publication_base_sha256': base}))
    artifacts.register_artifact(second.handle, 'dossier.permissions', permissions, 'READ')
    return second, _execute


@pytest.mark.parametrize('resume_phase', [None, 'PROMOTED', 'VIEWS_APPLIED'])
def test_dossier_publication_cannot_drop_intervening_maintenance(tmp_path, monkeypatch, resume_phase):
    from autoresearch.session_agent.workflows.dossier import publication_state_precondition
    from autoresearch.trace.publication import InjectedPublicationFault

    case, execute = _dossier_publication_pair(tmp_path, monkeypatch)
    precondition = publication_state_precondition(case.handle)
    if resume_phase:
        with pytest.raises(InjectedPublicationFault):
            execute(case, state_precondition=precondition, fault_after=resume_phase)
    reconcile.reconcile_one('300857', '20260630', '2026-09-30', fetch=_fake_fetch())
    before = schema.read_dossier_bytes('300857')
    with pytest.raises(RuntimeError, match='CONFLICT'):
        execute(case, state_precondition=precondition)
    assert schema.read_dossier_bytes('300857') == before
    assert '季度对账' in before.decode()


def test_committed_publication_resume_does_not_recheck_old_opening(tmp_path, monkeypatch):
    from autoresearch.session_agent.workflows.dossier import publication_state_precondition

    case, execute = _dossier_publication_pair(tmp_path, monkeypatch)
    precondition = publication_state_precondition(case.handle)
    receipt = execute(case, state_precondition=precondition)
    reconcile.reconcile_one('300857', '20260630', '2026-09-30', fetch=_fake_fetch())
    before = schema.read_dossier_bytes('300857')
    assert execute(case, state_precondition=precondition) == receipt
    assert schema.read_dossier_bytes('300857') == before


def test_session_finish_installs_dossier_precondition(tmp_path, monkeypatch):
    import contextlib

    from autoresearch.session_agent import publication
    from autoresearch.trace import write_guard

    case, _ = _dossier_publication_pair(tmp_path, monkeypatch)
    monkeypatch.setattr(publication, 'prepare_bundle', lambda _: (case.bundle, case.artifact_reader))
    monkeypatch.setattr(write_guard, 'run_write_lock', lambda _: contextlib.nullcontext())
    reconcile.reconcile_one('300857', '20260630', '2026-09-30', fetch=_fake_fetch())
    before = schema.read_dossier_bytes('300857')
    with pytest.raises(RuntimeError, match='CONFLICT'):
        publication.transactional_finish(case.handle)
    assert schema.read_dossier_bytes('300857') == before

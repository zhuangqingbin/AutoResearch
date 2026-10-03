"""Explicit current sets index signed proofs; all fixtures stay in tmp_path."""
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from autoresearch.common.atomic import canonical_json
from autoresearch.contracts.research_boundary import BOUNDARY_CASES
from autoresearch.session_agent import boundary_proof as bp
from .test_boundary_proof import observation, snap


@pytest.fixture
def proof_store(tmp_path, monkeypatch):
    monkeypatch.setattr(bp.ws, 'ENGINE', 'codex')
    monkeypatch.setattr(bp.ws, 'context_root', lambda: tmp_path / 'context')
    monkeypatch.setattr(bp.ws, 'reports_root', lambda: tmp_path / 'reports')
    monkeypatch.setattr(bp, 'policy_fingerprint', lambda host: 'a' * 64)
    audit = tmp_path / 'audit'
    # Independent keys are pinned for both imported host types in this one root.
    issuers = {}
    for engine in ('codex', 'claude'):
        key = tmp_path / f'{engine}.pem'
        bp._openssl(['genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', str(key)])
        public = bp._openssl(['pkey', '-in', str(key), '-pubout'])
        public_path = tmp_path / f'{engine}.pub.pem'
        public_path.write_bytes(public)
        identity = bp.sha256_bytes(public)
        bp.trust_issuer(public_path, engine=engine, expected_fingerprint=identity, evidence_root=audit)
        issuers[engine] = key, identity

    def add(case='allowed_read', engine='codex', session='host', version='fixture-v1', policy='a' * 64):
        c, event, rows, observed = observation(case, engine)
        c['session_id'] = event['session_id'] = session
        context = {'engine': engine, 'session_id': session, 'agent_id': 'child'}
        c['snapshots']['context_snapshot'] = event['context_snapshot'] = snap(context)
        c['policy_hash'] = event['policy_hash'] = policy
        if engine == 'codex':
            rows[0]['payload']['source']['subagent']['thread_spawn']['parent_thread_id'] = session
            rows[0]['payload']['cli_version'] = version
        else:
            for row in rows:
                row['sessionId'], row['version'] = session, version
        payload = {'challenge': c, 'event': event, 'native_rows': rows,
                   'source_prefix_sha256': 'c' * 64, 'observed_target_sha256': observed,
                   'issued_at': '2026-09-30T01:00:05+00:00'}
        key, identity = issuers[engine]
        signature = bp._openssl(['dgst', '-sha256', '-sign', str(key)], data=canonical_json(payload).encode())
        proof = {'schema_version': 1, 'issuer_id': identity, 'payload': payload,
                 'signature': base64.b64encode(signature).decode()}
        proof_hash = bp._hash(proof)
        path = audit / 'boundary/proofs' / f'{proof_hash}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(proof))
        return proof_hash

    return audit, add


def select(audit, hashes, engine='codex'):
    assert hasattr(bp, 'select_boundary_set'), 'explicit boundary set selection is missing'
    return bp.select_boundary_set(engine, hashes, evidence_root=audit)


def test_explicit_current_set_ignores_stale_duplicate_and_other_session_history(proof_store):
    audit, add = proof_store
    add(policy='b' * 64)
    add(session='old-host')
    hashes = {case: add(case) for case in BOUNDARY_CASES}
    assert bp.boundary_gate(evidence_root=audit, required_hosts=('codex',))['status'] == 'INVALID'
    chosen = select(audit, hashes)
    result = bp.boundary_gate(evidence_root=audit, required_hosts=('codex',))
    assert result['status'] == 'VALIDATED', result
    assert len(result['loaded_host_evidence']) == 11
    assert result['history_diagnostics']
    assert chosen['proof_hashes'] == hashes
    assert chosen['selection_hash'] == bp._hash({k: v for k, v in chosen.items() if k != 'selection_hash'})


def test_partial_selection_keeps_fixed_denominator_and_no_legacy_fill(proof_store):
    audit, add = proof_store
    hashes = {case: add(case, 'claude') for case in BOUNDARY_CASES}
    select(audit, {'allowed_read': add()})
    result = bp.boundary_gate(evidence_root=audit)
    assert result['status'] == 'PENDING_REAL_HOST_VALIDATION'
    assert len(result['missing']) == 21
    assert all(f'claude:{case}' in result['missing'] for case in hashes)


@pytest.mark.parametrize('mutation', [
    'absent', 'hash_mismatch', 'bad_signature', 'symlink', 'directory_symlink',
    'case_mismatch', 'engine_mismatch', 'session_mismatch', 'version_mismatch',
    'policy_mismatch', 'duplicate', 'unknown_case', 'path', 'empty', 'nonobject',
])
def test_selection_rejects_invalid_members_before_writing_active(proof_store, mutation):
    audit, add = proof_store
    value = add()
    hashes = {'allowed_read': value}
    path = audit / 'boundary/proofs' / f'{value}.json'
    if mutation == 'absent':
        path.unlink()
    elif mutation in {'hash_mismatch', 'bad_signature'}:
        proof = json.loads(path.read_text())
        proof['signature'] = base64.b64encode(b'invalid').decode()
        if mutation == 'bad_signature':
            value = bp._hash(proof)
            path = path.with_name(f'{value}.json')
            hashes['allowed_read'] = value
        path.write_text(json.dumps(proof))
    elif mutation == 'symlink':
        moved = audit / 'outside.json'
        path.rename(moved)
        path.symlink_to(moved)
    elif mutation == 'directory_symlink':
        path.parent.rename(audit / 'elsewhere')
        path.parent.symlink_to(audit / 'elsewhere', target_is_directory=True)
    elif mutation == 'case_mismatch':
        hashes = {'allowed_write': value}
    elif mutation == 'engine_mismatch':
        hashes = {'allowed_read': add(engine='claude')}
    elif mutation in {'session_mismatch', 'version_mismatch', 'policy_mismatch'}:
        kwargs = {'session': 'other'} if mutation == 'session_mismatch' else (
            {'version': 'v2'} if mutation == 'version_mismatch' else {'policy': 'b' * 64})
        hashes['allowed_write'] = add('allowed_write', **kwargs)
    elif mutation == 'duplicate':
        hashes['allowed_write'] = value
    elif mutation == 'unknown_case':
        hashes = {'self_reported_pass': value}
    elif mutation == 'path':
        hashes['allowed_read'] = '../proof.json'
    elif mutation == 'empty':
        hashes = {}
    else:
        hashes = []
    with pytest.raises((ValueError, OSError)):
        select(audit, hashes)
    assert not (audit / 'boundary/active.json').exists()


@pytest.mark.parametrize('mutation', ['missing_proof', 'signature', 'policy_drift', 'active_extra',
    'active_bad_hash', 'active_nonobject', 'selection_extra', 'selection_hash', 'selection_symlink'])
def test_active_selection_tampering_fails_closed(proof_store, monkeypatch, mutation):
    audit, add = proof_store
    proof_hash = add()
    chosen = select(audit, {'allowed_read': proof_hash})
    root = audit / 'boundary'
    active = root / 'active.json'
    selection = root / 'selections' / f'{chosen["selection_hash"]}.json'
    if mutation == 'missing_proof':
        (root / 'proofs' / f'{proof_hash}.json').unlink()
    elif mutation == 'signature':
        path = root / 'proofs' / f'{proof_hash}.json'
        value = json.loads(path.read_text())
        value['signature'] = 'bad'
        path.write_text(json.dumps(value))
    elif mutation == 'policy_drift':
        monkeypatch.setattr(bp, 'policy_fingerprint', lambda _: 'b' * 64)
    elif mutation.startswith('active_'):
        value = json.loads(active.read_text())
        if mutation == 'active_extra':
            value['status'] = 'PASS'
        elif mutation == 'active_bad_hash':
            value['selections']['codex'] = '../escape'
        else:
            value = []
        active.write_text(json.dumps(value))
    elif mutation == 'selection_symlink':
        moved = root / 'elsewhere.json'
        selection.rename(moved)
        selection.symlink_to(moved)
    else:
        value = json.loads(selection.read_text())
        if mutation == 'selection_extra':
            value['status'] = 'PASS'
        else:
            value['session_id'] = 'forged'
        selection.write_text(json.dumps(value))
    result = bp.boundary_gate(evidence_root=audit)
    assert result['status'] == 'INVALID'
    assert not result['acceptance_satisfied']
    assert len(result['missing']) == 22


def test_selection_immutable_history_chain_and_concurrent_host_updates(proof_store):
    audit, add = proof_store
    codex = {'allowed_read': add()}
    claude = {'allowed_read': add(engine='claude')}
    first = select(audit, codex)
    old_path = audit / 'boundary/selections' / f'{first["selection_hash"]}.json'
    old_bytes = old_path.read_bytes()
    barrier = Barrier(2)

    def update(engine, hashes):
        barrier.wait(timeout=10)
        return select(audit, hashes, engine)

    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(update, 'codex', codex)
        b = executor.submit(update, 'claude', claude)
        newer, other = a.result(timeout=30), b.result(timeout=30)
    active = json.loads((audit / 'boundary/active.json').read_text())
    assert active == {'schema_version': 1, 'selections': {
        'codex': newer['selection_hash'], 'claude': other['selection_hash']}}
    assert newer['previous_selection_hash'] == first['selection_hash']
    assert other['previous_selection_hash'] is None
    assert old_path.read_bytes() == old_bytes
    assert len(bp.boundary_gate(evidence_root=audit)['missing']) == 20


def test_no_active_preserves_legacy_gate(proof_store):
    audit, add = proof_store
    add()
    result = bp.boundary_gate(evidence_root=audit)
    assert result['status'] == 'PENDING_REAL_HOST_VALIDATION'
    assert len(result['missing']) == 21
    assert not (audit / 'boundary/active.json').exists()


@pytest.mark.parametrize('extra', [{'status': 'PASS'}, {'evidence_root': '/tmp'}, {'proof_path': '/tmp/x'}, {}])
def test_cli_select_exact_spec_schema(proof_store, tmp_path, monkeypatch, capsys, extra):
    audit, add = proof_store
    monkeypatch.setattr(bp.ws, 'reports_root', lambda: tmp_path / 'reports')
    # Place this synthetic store under the CLI's temporary default root.
    destination = tmp_path / 'reports/_acceptance/proofs'
    destination.parent.mkdir(parents=True)
    hashes = {'allowed_read': add()}
    audit.rename(destination)
    spec = tmp_path / 'spec.json'
    spec.write_text(json.dumps({'engine': 'codex', 'proof_hashes': hashes, **extra}))
    assert bp.main(['select', '--spec', str(spec)]) == (2 if extra else 0)
    value = json.loads(capsys.readouterr().out)
    if extra:
        assert value['status'] == 'REJECTED'
    else:
        assert value['proof_hashes'] == hashes


@pytest.mark.parametrize('with_active', [True, False])
def test_history_does_not_enumerate_redirected_proof_directory(tmp_path, monkeypatch, with_active):
    root = tmp_path / 'boundary'
    root.mkdir()
    if with_active:
        (root / 'active.json').write_text(json.dumps({'schema_version': 1, 'selections': {}}))
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'proofs').symlink_to(outside, target_is_directory=True)
    traversed = []
    original = Path.glob

    def observe_glob(path, pattern):
        if path.is_symlink():
            traversed.append(path)
        return original(path, pattern)

    monkeypatch.setattr(Path, 'glob', observe_glob)
    result = bp.boundary_gate(evidence_root=tmp_path)
    assert traversed == []
    if with_active:
        assert result['history_diagnostics'][0]['status'] == 'INVALID'
    else:
        assert result['status'] == 'INVALID'


@pytest.mark.parametrize('mutation', ['session', 'version', 'engine', 'previous', 'duplicate_field', 'active_symlink'])
def test_rehashed_selection_still_requires_signed_member_identity(proof_store, mutation):
    audit, add = proof_store
    chosen = select(audit, {'allowed_read': add()})
    root = audit / 'boundary'
    active = root / 'active.json'
    if mutation == 'active_symlink':
        other = root / 'elsewhere.json'
        active.rename(other)
        active.symlink_to(other)
    elif mutation == 'duplicate_field':
        active.write_text('{"schema_version":1,"schema_version":1,"selections":{}}')
    else:
        field, value = {'session': ('session_id', 'forged'), 'version': ('host_version', 'v2'),
                        'engine': ('engine', 'claude'), 'previous': ('previous_selection_hash', 'b' * 64)}[mutation]
        chosen[field] = value
        chosen['selection_hash'] = bp._hash({k: v for k, v in chosen.items() if k != 'selection_hash'})
        (root / 'selections' / f'{chosen["selection_hash"]}.json').write_text(json.dumps(chosen))
        active.write_text(json.dumps({'schema_version': 1, 'selections': {'codex': chosen['selection_hash']}}))
    before = active.read_bytes()
    result = bp.boundary_gate(evidence_root=audit)
    assert result['status'] == 'INVALID'
    assert len(result['missing']) == 22
    if mutation in {'previous', 'duplicate_field', 'active_symlink'}:
        with pytest.raises((ValueError, OSError)):
            select(audit, {'allowed_read': add()})
        assert active.read_bytes() == before


def test_complete_dual_host_sets_are_local_imports_with_distinct_epochs(proof_store):
    audit, add = proof_store
    for engine in ('codex', 'claude'):
        hashes = {case: add(case, engine=engine, version=f'{engine}-v1') for case in BOUNDARY_CASES}
        select(audit, hashes, engine)
    result = bp.boundary_gate(evidence_root=audit)
    assert result['status'] == 'VALIDATED'
    assert len(result['loaded_host_evidence']) == 22


@pytest.mark.parametrize('mutation', ['policy_drift', 'missing_proof', 'missing_selection'])
def test_nonrequired_host_failure_is_diagnostic_only(proof_store, monkeypatch, mutation):
    audit, add = proof_store
    selections = {}
    for engine in ('codex', 'claude'):
        hashes = {case: add(case, engine=engine) for case in BOUNDARY_CASES}
        selections[engine] = select(audit, hashes, engine)
    root = audit / 'boundary'
    before = (root / 'active.json').read_bytes()
    if mutation == 'policy_drift':
        monkeypatch.setattr(bp, 'policy_fingerprint', lambda host: ('b' if host == 'claude' else 'a') * 64)
    elif mutation == 'missing_proof':
        proof_hash = selections['claude']['proof_hashes']['allowed_read']
        (root / 'proofs' / f'{proof_hash}.json').unlink()
    else:
        selection_hash = selections['claude']['selection_hash']
        (root / 'selections' / f'{selection_hash}.json').unlink()
    current = bp.boundary_gate(evidence_root=audit, required_hosts=('codex',))
    assert current['status'] == 'VALIDATED', current
    assert current['missing'] == current['invalid'] == []
    assert len(current['loaded_host_evidence']) == 11
    assert any(row.get('engine') == 'claude' and row['status'] == 'INVALID'
               for row in current['history_diagnostics'])
    combined = bp.boundary_gate(evidence_root=audit)
    assert combined['status'] == 'INVALID'
    assert any(error.startswith('claude:') for error in combined['invalid'])
    assert (root / 'active.json').read_bytes() == before

"""SYNTHETIC tmp roots: never host acceptance evidence."""
import json
from pathlib import Path

import pytest

from autoresearch.common.atomic import canonical_json
from autoresearch.contracts.research_boundary import BOUNDARY_CASES
from autoresearch.session_agent import boundary_proof as proof, task_access as access


@pytest.fixture
def probe(tmp_path, monkeypatch):
    from autoresearch.session_agent import boundary_probe as module
    monkeypatch.setattr(module, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(module.ws, 'ENGINE', 'codex')
    monkeypatch.setattr(module.ws, 'context_root', lambda: tmp_path / 'context_codex')
    monkeypatch.setattr(module.ws, 'reports_root', lambda: tmp_path / 'reports_codex')
    monkeypatch.setattr(proof, '_native_source', lambda _: (_ for _ in ()).throw(ValueError('SYNTHETIC no native source')))
    return module


def bound(probe):
    batch = probe.create_batch()
    probe.bind_batch(batch['batch_id'], session_id='synthetic-session', agent_id='synthetic-child')
    return batch['batch_id']


def case_paths(probe, batch, case):
    root = probe.ws.context_root() / '_acceptance/boundary'
    prepared = probe.prepare_case(batch, case)
    return prepared, root / 'events' / (prepared['nonce'] + '.jsonl')


def test_create_bind_uses_private_paths_and_real_access(probe):
    batch = bound(probe)
    root = probe.ws.context_root() / '_acceptance/boundary/probes' / batch
    request = root / 'session/dispatch/stock.card-a1.json'
    manifest = access._load_manifest(request)
    assert manifest['identity']['role'] == 'stock.card'
    assert manifest['identity']['agent_type'] == 'L4 card'
    assert all(Path(row['path']).is_relative_to(root) for row in manifest['reads'] + manifest['conditional_reads'])
    assert all('boundary-canary' in Path(p).name for p in manifest['writes'])
    access.load_bound_access({'session_id': 'synthetic-session', 'agent_id': 'synthetic-child'}, 'codex', repo_root=probe.REPO_ROOT)
    assert not (probe.ws.context_root() / '_acceptance/boundary/events').exists()
    assert not (probe.ws.reports_root() / '_acceptance/proofs').exists()


@pytest.mark.parametrize('bad', ['../production', '/tmp/production', 'bad', 'f' * 33])
def test_rejects_caller_paths(probe, bad):
    with pytest.raises(ValueError):
        probe.bind_batch(bad, session_id='synthetic', agent_id='child')
    with pytest.raises(ValueError):
        probe.prepare_case(bad, 'stale_attempt')
    with pytest.raises(ValueError):
        probe.restore_case(bad)


def test_existing_binding_and_headless_binding_untouched(probe):
    batch = probe.create_batch()['batch_id']
    for agent in ['synthetic-child', '']:
        path = access._binding_path('codex', 'synthetic-session', agent, probe.REPO_ROOT)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('production binding')
        with pytest.raises(ValueError, match='existing binding'):
            probe.bind_batch(batch, session_id='synthetic-session', agent_id='synthetic-child')
        assert path.read_text() == 'production binding'
        path.unlink()


def test_pending_and_external_active_are_not_overwritten(probe):
    batch = bound(probe)
    first = probe.prepare_case(batch, 'allowed_read')
    with pytest.raises(ValueError, match='pending'):
        probe.prepare_case(batch, 'allowed_write')
    probe.restore_case(batch)
    active = probe.ws.context_root() / '_acceptance/boundary/active' / (proof._hash(['synthetic-session', 'synthetic-child']) + '.json')
    active.write_text('{"nonce":"' + 'a' * 32 + '"}')
    with pytest.raises(ValueError, match='active'):
        probe.prepare_case(batch, 'allowed_write')
    assert json.loads(active.read_text())['nonce'] != first['nonce']


def test_no_execution_is_not_observed_and_full_denominator(probe):
    batch = bound(probe)
    prepared = probe.prepare_case(batch, 'allowed_read')
    assert prepared['expected'] == 'ALLOW'
    result = probe.complete_case(batch)
    assert result['status'] == 'NOT_OBSERVED'
    assert 'issue_error' in result
    for case in ['outside_read', 'outside_write']:
        assert probe.prepare_case(batch, case)['status'] == 'UNSUPPORTED'
    summary = probe.summarize_batch(batch)
    assert set(summary['cases']) == set(BOUNDARY_CASES)
    assert len(summary['missing']) == 11
    assert summary['select_spec']['proof_hashes'] == {}
    assert summary['gate_scope'] == 'CURRENT_ACTIVE_SELECTION_OR_LEGACY_STORE_NOT_THIS_BATCH'
    assert not summary['gate']['acceptance_satisfied']


@pytest.mark.parametrize('case', ['stale_attempt', 'tampered_binding', 'missing_binding'])
def test_fault_restore_idempotence_and_conflict(probe, case):
    batch = bound(probe)
    binding = access._binding_path('codex', 'synthetic-session', 'synthetic-child', probe.REPO_ROOT)
    tasks = probe.ws.context_root() / '_acceptance/boundary/probes' / batch / 'session/tasks.json'
    target = tasks if case == 'stale_attempt' else binding
    before = (target.read_bytes(), target.stat().st_mode)
    prepared = probe.prepare_case(batch, case)
    assert prepared['expected'] == 'DENY'
    if case == 'missing_binding':
        assert not target.exists()
    else:
        assert target.read_bytes() != before[0]
    assert probe.restore_case(batch)['status'] == 'RESTORED'
    assert probe.restore_case(batch)['status'] == 'RESTORED'
    assert (target.read_bytes(), target.stat().st_mode) == before
    assert probe.summarize_batch(batch)['cases'][case]['status'] == 'NOT_OBSERVED'


def test_external_modification_is_preserved_and_failure_logged(probe):
    batch = bound(probe)
    probe.prepare_case(batch, 'tampered_binding')
    binding = access._binding_path('codex', 'synthetic-session', 'synthetic-child', probe.REPO_ROOT)
    binding.write_text('third party edit')
    with pytest.raises(ValueError, match='conflict'):
        probe.restore_case(batch)
    assert binding.read_text() == 'third party edit'
    summary = probe.summarize_batch(batch)
    assert summary['pending'] == 'tampered_binding'
    assert summary['cases']['tampered_binding']['restore_error']


def test_deep_order_and_exact_grant(probe):
    batch = bound(probe)
    with pytest.raises(ValueError, match='deep_before'):
        probe.prepare_case(batch, 'deep_after')
    probe.prepare_case(batch, 'deep_before')
    probe.complete_case(batch)
    probe.prepare_case(batch, 'deep_after')
    loaded = access.load_bound_access({'session_id': 'synthetic-session', 'agent_id': 'synthetic-child'}, 'codex', repo_root=probe.REPO_ROOT)
    assert loaded['deep_authorized']
    probe.restore_case(batch)
    with pytest.raises(ValueError):
        probe.prepare_case(batch, 'deep_before')


def test_hook_without_native_is_unprovable_and_real_issue_called(probe, monkeypatch):
    batch = bound(probe)
    prepared, events = case_paths(probe, batch, 'allowed_read')
    events.parent.mkdir(parents=True)
    events.write_text(canonical_json({'tool_name': prepared['proof_tool_name'], 'input_hash': proof._hash(prepared['proof_tool_input']), 'decision': 'ALLOW'}) + '\n')
    real_issue = proof.issue_proof
    calls = []
    def issue(path):
        calls.append(path)
        return real_issue(path)
    monkeypatch.setattr(proof, 'issue_proof', issue)
    result = probe.complete_case(batch)
    assert result['status'] == 'OBSERVED_UNPROVABLE'
    assert 'SYNTHETIC no native' in result['issue_error']
    assert len(calls) == 1
    assert not (probe.ws.reports_root() / '_acceptance/proofs/boundary/exports').exists()


def test_unexpected_allow_aborts_even_without_native(probe):
    batch = bound(probe)
    prepared, events = case_paths(probe, batch, 'identity_spoof')
    events.parent.mkdir(parents=True)
    events.write_text(canonical_json({'tool_name': prepared['proof_tool_name'], 'input_hash': proof._hash(prepared['proof_tool_input']), 'decision': 'ALLOW'}) + '\n')
    result = probe.complete_case(batch)
    assert result['status'] == 'ABORTED_UNEXPECTED_ALLOW'
    assert probe.summarize_batch(batch)['aborted']
    with pytest.raises(ValueError, match='aborted'):
        probe.prepare_case(batch, 'allowed_read')


def test_denied_canary_change_aborts(probe):
    batch = bound(probe)
    prepared = probe.prepare_case(batch, 'arbitrary_shell')
    challenge = json.loads(Path(prepared['challenge_path']).read_text())
    assert prepared['tool_input']['cmd'] == f"printf %s {prepared['nonce']} > {challenge['target']}"
    Path(challenge['target']).write_text('unexpected execution')
    assert probe.complete_case(batch)['status'] == 'ABORTED_CANARY_CHANGED'


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_linked_canary_rejected(probe, kind):
    batch = bound(probe)
    canary = probe.ws.context_root() / '_acceptance/boundary/probes' / batch / 'boundary-canary-read.txt'
    outside = probe.REPO_ROOT / 'production.txt'
    outside.write_text('do not touch')
    canary.unlink()
    if kind == 'symlink':
        canary.symlink_to(outside)
    else:
        canary.hardlink_to(outside)
    with pytest.raises(ValueError, match='link'):
        probe.prepare_case(batch, 'allowed_read')
    assert outside.read_text() == 'do not touch'


def test_bind_refuses_redirected_manifest_or_external_task_file(probe):
    batch = probe.create_batch()['batch_id']
    root = probe.ws.context_root() / '_acceptance/boundary/probes' / batch
    production = probe.REPO_ROOT / 'production.txt'
    production.write_text('never read or overwrite')
    manifest = root / 'session/dispatch/stock.card-a1.access.json'
    manifest.parent.mkdir(parents=True)
    manifest.symlink_to(production)
    with pytest.raises(ValueError, match='link'):
        probe.bind_batch(batch, session_id='synthetic-session', agent_id='synthetic-child')
    assert production.read_text() == 'never read or overwrite'
    manifest.unlink()
    tasks = root / 'session/tasks.json'
    tasks.write_text('external task file')
    with pytest.raises(ValueError, match='existing'):
        probe.bind_batch(batch, session_id='synthetic-session', agent_id='synthetic-child')
    assert tasks.read_text() == 'external task file'


def test_restore_refuses_changed_batch_identity_pointing_to_production(probe):
    batch = bound(probe)
    probe.prepare_case(batch, 'missing_binding')
    state_path = probe.ws.context_root() / '_acceptance/boundary/probes' / batch / 'batch.json'
    state = json.loads(state_path.read_text())
    state['agent_id'] = 'production-child'
    state_path.write_text(json.dumps(state))
    binding = access._binding_path('codex', 'synthetic-session', 'production-child', probe.REPO_ROOT)
    binding.write_text('production binding')
    with pytest.raises(ValueError):
        probe.restore_case(batch)
    assert binding.read_text() == 'production binding'


def test_denied_redirect_is_aborted_and_logged(probe):
    batch = bound(probe)
    prepared = probe.prepare_case(batch, 'arbitrary_shell')
    target = Path(json.loads(Path(prepared['challenge_path']).read_text())['target'])
    external = probe.REPO_ROOT / 'production.txt'
    external.write_text('production')
    target.unlink()
    target.symlink_to(external)
    result = probe.complete_case(batch)
    assert result['status'] == 'ABORTED_CANARY_CHANGED'
    assert result['completion_error']
    assert probe.summarize_batch(batch)['aborted']
    assert external.read_text() == 'production'


@pytest.mark.parametrize('trusted', [False, True])
@pytest.mark.parametrize('native_variant', ['observed_shape', 'noncanonical_wrapper', 'wrong_wrapper_command', 'missing_native'])
def test_real_issue_import_and_gate_preserve_trust_requirement(probe, monkeypatch, trusted, native_variant):
    """SYNTHETIC native rows: real export/import crypto and gate, tmp roots only."""
    batch = bound(probe)
    monkeypatch.setattr(proof, '_now', lambda: '2026-09-30T01:00:01+00:00')
    prepared = probe.prepare_case(batch, 'allowed_read')
    challenge = json.loads(Path(prepared['challenge_path']).read_text())
    request = Path(challenge['request_path'])
    def snapshot(path):
        text = path.read_text()
        return {'text': text, 'sha256': access.sha256_bytes(text.encode())}
    event = {key: challenge[key] for key in ['nonce', 'engine', 'session_id', 'agent_id', 'tool_name', 'policy_hash']}
    event.update(schema_version=1, decision='ALLOW', reason='', call_id='exec-synthetic', tool_name='Bash',
        input_hash=proof._hash({'command': prepared['tool_input']['cmd']}), observed_at='2026-09-30T01:00:03+00:00',
        manifest=snapshot(access.manifest_path(request)), context_snapshot=snapshot(request.with_suffix('.context.json')),
        binding=snapshot(access._binding_path('codex', 'synthetic-session', 'synthetic-child', probe.REPO_ROOT)),
        state=snapshot(request.parent.parent / 'tasks.json'), deep_grant=None)
    events = probe.ws.context_root() / '_acceptance/boundary/events' / (prepared['nonce'] + '.jsonl')
    events.parent.mkdir(parents=True)
    events.write_text(canonical_json(event) + '\n')
    command = prepared['tool_input']['cmd']
    rows = [
        {'type': 'session_meta', 'payload': {'id': 'synthetic-child', 'cli_version': 'SYNTHETIC-0.159.3',
            'timestamp': '2026-09-30T01:00:00+00:00',
            'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'synthetic-session'}}}}},
        {'type': 'response_item', 'timestamp': '2026-09-30T01:00:02+00:00', 'payload': {
            'type': 'custom_tool_call', 'name': 'exec', 'namespace': 'functions', 'call_id': 'synthetic-outer',
            'input': 'const result = await tools.exec_command(' + canonical_json({'cmd': command}) + '); text(JSON.stringify(result));',
            'internal_chat_message_metadata_passthrough': {'turn_id': 'synthetic-turn'}}},
        {'type': 'event_msg', 'timestamp': '2026-09-30T01:00:04+00:00', 'payload': {
            'type': 'item_completed', 'thread_id': 'synthetic-child', 'turn_id': 'synthetic-turn',
            'started_at_ms': 1790730003500, 'completed_at_ms': 1790730004000,
            'item': {'type': 'CommandExecution', 'id': 'exec-synthetic',
                'command': ['/bin/zsh', '-lc', command], 'source': 'unified_exec_startup',
                'status': 'completed', 'exit_code': 0,
                'stdout': canonical_json({'content': Path(challenge['target']).read_text()}),
                'stderr': '', 'aggregated_output': canonical_json({'content': Path(challenge['target']).read_text()})}}},
        {'type': 'response_item', 'timestamp': '2026-09-30T01:00:05+00:00', 'payload': {
            'type': 'custom_tool_call_output', 'call_id': 'synthetic-outer', 'output': 'SYNTHETIC outer result'}}]
    if native_variant == 'noncanonical_wrapper':
        rows[1]['payload']['input'] = rows[1]['payload']['input'].replace('text(JSON.stringify(result))', 'text(result)')
    elif native_variant == 'wrong_wrapper_command':
        rows[1]['payload']['input'] = 'const result = await tools.exec_command({cmd:"printf wrong-command"}); text(JSON.stringify(result));'
    elif native_variant == 'missing_native':
        rows.pop(2)
    native = probe.REPO_ROOT / 'SYNTHETIC-native.jsonl'
    native.write_text(''.join(canonical_json(row) + '\n' for row in rows))
    monkeypatch.setattr(proof, '_native_source', lambda _: native)
    issuer = proof.initialize_issuer()  # Explicit test fixture provisioning only.
    if trusted:
        proof.trust_issuer(issuer['public_key_path'], engine='codex', expected_fingerprint=issuer['issuer_id'])
    def forbidden(*args, **kwargs):
        pytest.fail('probe must never provision issuer/trust or select proofs')
    for name in ['initialize_issuer', 'trust_issuer', 'select_boundary_set']:
        monkeypatch.setattr(proof, name, forbidden)
    monkeypatch.setattr(proof, '_now', lambda: '2026-09-30T01:00:05+00:00')
    result = probe.complete_case(batch)
    if native_variant != 'observed_shape':
        assert result['status'] == 'OBSERVED_UNPROVABLE'
        assert result['issue_error']
        assert 'export' not in result and 'import' not in result
        assert len(probe.summarize_batch(batch)['missing']) == 11
        return
    assert result['status'] == ('IMPORTED' if trusted else 'EXPORTED_IMPORT_FAILED'), result.get('import_error', result.get('issue_error'))
    assert 'export' in result
    assert ('import_error' in result) is not trusted
    summary = probe.summarize_batch(batch)
    assert len(summary['missing']) == (10 if trusted else 11)
    assert not summary['gate']['acceptance_satisfied']
    if trusted:
        assert summary['select_spec']['proof_hashes'] == {'allowed_read': result['export']['proof_hash']}
        assert summary['gate']['loaded_host_evidence'][0]['proof_hash'] == result['export']['proof_hash']


@pytest.mark.parametrize('case', [case for case in BOUNDARY_CASES if not case.startswith('outside_')])
def test_prepared_calls_match_existing_case_semantics(probe, case):
    batch = bound(probe)
    if case == 'deep_after':
        probe.prepare_case(batch, 'deep_before')
        probe.complete_case(batch)
    prepared = probe.prepare_case(batch, case)
    challenge = json.loads(Path(prepared['challenge_path']).read_text())
    request = Path(challenge['request_path'])
    def snapshot(path):
        if not path.exists():
            return None
        text = path.read_text()
        return {'text': text, 'sha256': access.sha256_bytes(text.encode())}
    event = {'manifest': snapshot(access.manifest_path(request)),
             'context_snapshot': snapshot(request.with_suffix('.context.json')),
             'binding': snapshot(access._binding_path('codex', 'synthetic-session', 'synthetic-child', probe.REPO_ROOT)),
             'state': snapshot(request.parent.parent / 'tasks.json'),
             'deep_grant': snapshot(request.with_suffix('.deep-access.json'))}
    proof._case_semantics(challenge, event)  # Real verifier, no fake PASS substitution.


def test_completion_does_not_clear_another_active_nonce(probe):
    batch = bound(probe)
    probe.prepare_case(batch, 'allowed_read')
    active = probe.ws.context_root() / '_acceptance/boundary/active' / (proof._hash(['synthetic-session', 'synthetic-child']) + '.json')
    active.write_text('{"nonce":"' + 'b' * 32 + '"}')
    probe.complete_case(batch)
    assert json.loads(active.read_text())['nonce'] == 'b' * 32


def test_cli_errors_are_structured_and_no_path_options(probe, capsys):
    assert probe.main(['prepare', '/production', 'stale_attempt']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'ERROR'
    with pytest.raises(SystemExit):
        probe.main(['create', '--run-path', '/production'])


@pytest.mark.parametrize('which', ['read', 'outside'])
def test_unexpected_any_canary_change_aborts_allowed_read(probe, which):
    batch = bound(probe)
    probe.prepare_case(batch, 'allowed_read')
    canary = probe.ws.context_root() / '_acceptance/boundary/probes' / batch / f'boundary-canary-{which}.txt'
    canary.write_text('unexpected change')
    assert probe.complete_case(batch)['status'] == 'ABORTED_CANARY_CHANGED'
    assert probe.summarize_batch(batch)['aborted']


def test_expected_write_canary_change_does_not_imply_proof_or_abort(probe):
    batch = bound(probe)
    prepared = probe.prepare_case(batch, 'allowed_write')
    challenge = json.loads(Path(prepared['challenge_path']).read_text())
    manifest = json.loads(challenge['snapshots']['manifest']['text'])
    data = access.decode_broker_command(prepared['tool_input']['cmd'], manifest['broker'])
    Path(challenge['target']).write_text(data['content'])  # SYNTHETIC expected canary state, no native evidence.
    result = probe.complete_case(batch)
    assert result['status'] == 'NOT_OBSERVED'
    assert not probe.summarize_batch(batch)['aborted']
    assert result['before_snapshot']['sha256'] == challenge['before_sha256']
    probe.prepare_case(batch, 'allowed_read')
    assert probe.complete_case(batch)['status'] == 'NOT_OBSERVED'


def test_bind_preflights_canaries_before_any_freeze_or_registry_write(probe, monkeypatch):
    batch = probe.create_batch()['batch_id']
    root = probe.ws.context_root() / '_acceptance/boundary/probes' / batch
    canary = root / 'boundary-canary-read.txt'
    external = probe.REPO_ROOT / 'production.txt'
    external.write_text('never read')
    canary.unlink()
    canary.symlink_to(external)
    calls = []
    real_freeze = access.freeze_access
    def freeze(*args, **kwargs):
        calls.append('freeze')
        return real_freeze(*args, **kwargs)
    monkeypatch.setattr(access, 'freeze_access', freeze)
    with pytest.raises(ValueError, match='link'):
        probe.bind_batch(batch, session_id='synthetic-session', agent_id='synthetic-child')
    assert calls == []
    assert not (root / 'session/tasks.json').exists()
    assert not access._binding_path('codex', 'synthetic-session', 'synthetic-child', probe.REPO_ROOT).exists()


def test_unassociated_hook_is_observed_unprovable(probe):
    batch = bound(probe)
    prepared, events = case_paths(probe, batch, 'allowed_read')
    events.parent.mkdir(parents=True)
    events.write_text(canonical_json({'nonce': prepared['nonce'], 'tool_name': prepared['tool_name'], 'decision': 'ALLOW'}) + '\n')
    result = probe.complete_case(batch)
    assert result['status'] == 'OBSERVED_UNPROVABLE'
    assert 'exactly one actual hook' in result['issue_error']


@pytest.mark.parametrize('directory', ['exports', 'imports', 'proofs', 'trust'])
def test_proof_directories_preflight_before_issuer(probe, monkeypatch, directory):
    batch = bound(probe)
    probe.prepare_case(batch, 'allowed_read')
    store = proof._root()
    store.mkdir(parents=True)
    outside = probe.REPO_ROOT / 'outside-production'
    outside.mkdir()
    (store / directory).symlink_to(outside, target_is_directory=True)
    calls = []
    real_issue = proof.issue_proof
    def issue(*args, **kwargs):
        calls.append('issue')
        return real_issue(*args, **kwargs)
    monkeypatch.setattr(proof, 'issue_proof', issue)
    result = probe.complete_case(batch)
    assert calls == []
    assert 'link' in result['issue_error']
    assert not list(outside.iterdir())
    assert not probe.summarize_batch(batch)['select_spec']['proof_hashes']


def test_external_owner_challenge_between_prepare_check_and_create_survives(probe, monkeypatch):
    batch = bound(probe)
    real_create = proof.create_challenge
    external_nonce = 'e' * 32
    def racing_create(*args, **kwargs):
        external = dict(kwargs, nonce=external_nonce)
        external.pop('require_no_active', None)
        real_create(*args, **external)  # Normal public owner API, after D06's preliminary check.
        return real_create(*args, **kwargs)
    monkeypatch.setattr(proof, 'create_challenge', racing_create)
    with pytest.raises(ValueError, match='active'):
        probe.prepare_case(batch, 'allowed_read')
    active = probe.ws.context_root() / '_acceptance/boundary/active' / (proof._hash(['synthetic-session', 'synthetic-child']) + '.json')
    assert json.loads(active.read_text())['nonce'] == external_nonce
    probe.restore_case(batch)
    assert json.loads(active.read_text())['nonce'] == external_nonce


def test_clear_uses_owner_compare_and_preserves_new_nonce(probe, monkeypatch):
    batch = bound(probe)
    prepared = probe.prepare_case(batch, 'allowed_read')
    challenge = json.loads(Path(prepared['challenge_path']).read_text())
    real_clear = proof.clear_challenge
    def racing_clear(*args, **kwargs):
        proof.create_challenge(challenge['request_path'], case=challenge['case'],
            session_id=challenge['session_id'], agent_id=challenge['agent_id'],
            tool_name=challenge['tool_name'], tool_input=challenge['tool_input'],
            target=challenge['target'], expected_sha256=challenge['expected_sha256'], nonce='f' * 32)
        return real_clear(*args, **kwargs)
    monkeypatch.setattr(proof, 'clear_challenge', racing_clear)
    probe.complete_case(batch)
    active = probe.ws.context_root() / '_acceptance/boundary/active' / (proof._hash(['synthetic-session', 'synthetic-child']) + '.json')
    assert json.loads(active.read_text())['nonce'] == 'f' * 32


def test_fault_publish_interruption_restores_original_bytes_and_mode(probe, monkeypatch):
    import os
    import stat
    batch = bound(probe)
    binding = access._binding_path('codex', 'synthetic-session', 'synthetic-child', probe.REPO_ROOT)
    binding.chmod(0o640)
    before = binding.read_bytes()
    real_replace = os.replace
    interrupted = False
    def interrupt_after_publish(source, destination):
        nonlocal interrupted
        result = real_replace(source, destination)
        if Path(destination) == binding and not interrupted:
            interrupted = True
            raise KeyboardInterrupt('SYNTHETIC interruption immediately after publish')
        return result
    monkeypatch.setattr(os, 'replace', interrupt_after_publish)
    with pytest.raises(KeyboardInterrupt):
        probe.prepare_case(batch, 'tampered_binding')
    assert stat.S_IMODE(binding.stat().st_mode) == 0o640
    assert probe.restore_case(batch)['status'] == 'RESTORED'
    assert binding.read_bytes() == before
    assert stat.S_IMODE(binding.stat().st_mode) == 0o640
    assert probe.restore_case(batch)['status'] == 'RESTORED'


@pytest.mark.parametrize('operation', ['create', 'clear'])
def test_public_owner_apis_share_context_lock(probe, operation):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    batch = bound(probe)
    prepared = probe.prepare_case(batch, 'allowed_read')
    challenge = json.loads(Path(prepared['challenge_path']).read_text())
    started = Event()
    def owner_call():
        started.set()
        if operation == 'clear':
            return proof.clear_challenge(session_id='synthetic-session', agent_id='synthetic-child', nonce=prepared['nonce'])
        return proof.create_challenge(challenge['request_path'], case=challenge['case'],
            session_id=challenge['session_id'], agent_id=challenge['agent_id'],
            tool_name=challenge['tool_name'], tool_input=challenge['tool_input'],
            target=challenge['target'], expected_sha256=challenge['expected_sha256'], nonce='d' * 32)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with proof._active_lock('synthetic-session', 'synthetic-child'):
            future = pool.submit(owner_call)
            assert started.wait(1)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.05)
        assert future.result(timeout=2)


def test_probe_code_is_part_of_policy_fingerprint():
    from autoresearch.contracts.research_boundary import POLICY_FILES
    assert 'autoresearch/session_agent/boundary_probe.py' in POLICY_FILES


@pytest.mark.parametrize('case', [case for case in BOUNDARY_CASES if not case.startswith('outside_')])
def test_codex_exec_route_is_separate_from_frozen_bash_proof(probe, case):
    """Observed 0.159.3 route shape; these generated artifacts are SYNTHETIC."""
    batch = bound(probe)
    if case == 'deep_after':
        probe.prepare_case(batch, 'deep_before')
        probe.complete_case(batch)
    prepared = probe.prepare_case(batch, case)
    challenge = json.loads(Path(prepared['challenge_path']).read_text())
    assert prepared['tool_name'] == 'exec_command'
    assert set(prepared['tool_input']) == {'cmd'}
    assert challenge['tool_name'] == 'Bash'
    assert challenge['tool_input'] == {'command': prepared['tool_input']['cmd']}
    assert prepared['proof_tool_name'] == challenge['tool_name']
    assert prepared['proof_tool_input'] == challenge['tool_input']
    assert prepared['route_profile'] == 'CODEX_0_159_3_EXEC_COMMAND_TO_BASH'
    assert prepared['wrapper_tool_name'] == 'functions.exec'
    assert prepared['wrapper_program'] == 'const result = await tools.exec_command(' + canonical_json(prepared['tool_input']) + '); text(JSON.stringify(result));'


# ───────────── Claude route: structured Read/Write/Bash + a second shell identity ─────────────
#
# Observed on Claude Code 2.1.285 (2026-10-01 acceptance readout): research roles such as
# l4-card expose Read/Write but no shell, so arbitrary_shell and identity_spoof need a
# second bound identity (general-purpose); Write refuses to overwrite an unread file, so
# writable canaries start absent. Everything below is SYNTHETIC tmp-root evidence.

_SHELL = {'arbitrary_shell', 'identity_spoof'}
_WRITES = {'allowed_write', 'outside_write'}


@pytest.fixture
def claude_probe(tmp_path, monkeypatch):
    from autoresearch.session_agent import boundary_probe as module
    monkeypatch.setattr(module, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(module.ws, 'ENGINE', 'claude')
    monkeypatch.setattr(module.ws, 'context_root', lambda: tmp_path / 'context_claude')
    monkeypatch.setattr(module.ws, 'reports_root', lambda: tmp_path / 'reports_claude')
    monkeypatch.setattr(proof, '_native_source', lambda _: (_ for _ in ()).throw(ValueError('SYNTHETIC no native source')))
    return module


def claude_bound(probe):
    batch = probe.create_batch()['batch_id']
    probe.bind_batch(batch, session_id='synthetic-session', agent_id='synthetic-research',
                     shell_agent_id='synthetic-shell')
    return batch


def _snapshot(path):
    if not path.exists():
        return None
    text = path.read_text()
    return {'text': text, 'sha256': access.sha256_bytes(text.encode())}


def _claude_event_snapshots(probe, challenge):
    request = Path(challenge['request_path'])
    return {'manifest': _snapshot(access.manifest_path(request)),
            'context_snapshot': _snapshot(request.with_suffix('.context.json')),
            'binding': _snapshot(access._binding_path('claude', 'synthetic-session', challenge['agent_id'], probe.REPO_ROOT)),
            'state': _snapshot(request.parent.parent / 'tasks.json'),
            'deep_grant': _snapshot(request.with_suffix('.deep-access.json'))}


def test_claude_bind_freezes_research_and_shell_identities(claude_probe):
    batch = claude_bound(claude_probe)
    root = claude_probe.ws.context_root() / '_acceptance/boundary/probes' / batch
    research = access._load_manifest(root / 'session/dispatch/stock.card-a1.json')
    shell = access._load_manifest(root / 'session/dispatch/canary.shell-a1.json')
    assert (research['identity']['agent_type'], research['identity']['role']) == ('l4-card', 'stock.card')
    assert (shell['identity']['agent_type'], shell['identity']['role']) == ('general-purpose', 'canary.shell')
    assert shell['conditional_reads'] == [] and research['conditional_reads']
    for agent, role in (('synthetic-research', 'l4-card'), ('synthetic-shell', 'general-purpose')):
        access.load_bound_access({'session_id': 'synthetic-session', 'agent_id': agent, 'agent_type': role},
                                 'claude', repo_root=claude_probe.REPO_ROOT)
    with pytest.raises(ValueError, match='role'):
        access.load_bound_access({'session_id': 'synthetic-session', 'agent_id': 'synthetic-research',
                                  'agent_type': 'general-purpose'}, 'claude', repo_root=claude_probe.REPO_ROOT)
    # Claude Write cannot overwrite a file its agent has not read: writable canaries start absent.
    for name in ('boundary-canary-outside-write.txt',
                 'staging/session_outputs/attempts/stock.card/a0001/outputs/boundary-canary-write.txt',
                 'staging/session_outputs/attempts/canary.shell/a0001/outputs/boundary-canary-shell-write.txt'):
        assert not (root / name).exists() and (root / name).parent.is_dir()


def test_shell_identity_is_required_exactly_on_shell_less_hosts(claude_probe):
    batch = claude_probe.create_batch()['batch_id']
    with pytest.raises(ValueError, match='shell-capable'):
        claude_probe.bind_batch(batch, session_id='synthetic-session', agent_id='synthetic-research')
    with pytest.raises(ValueError, match='distinct'):
        claude_probe.bind_batch(batch, session_id='synthetic-session', agent_id='same', shell_agent_id='same')
    assert not (claude_probe.ws.context_root() / '_acceptance/boundary/probes' / batch / 'session').exists()


def test_codex_route_rejects_a_shell_identity(probe):
    batch = probe.create_batch()['batch_id']
    with pytest.raises(ValueError, match='shell-capable'):
        probe.bind_batch(batch, session_id='synthetic-session', agent_id='synthetic-child',
                         shell_agent_id='synthetic-shell')
    assert set(json.loads((probe.ws.context_root() / '_acceptance/boundary/probes' / batch / 'batch.json').read_text())['canaries']) == {
        'read', 'deep', 'outside', 'write'}


@pytest.mark.parametrize('case', list(BOUNDARY_CASES))
def test_claude_prepared_calls_match_existing_case_semantics(claude_probe, case):
    batch = claude_bound(claude_probe)
    if case == 'deep_after':
        claude_probe.prepare_case(batch, 'deep_before')
        claude_probe.complete_case(batch)
    prepared = claude_probe.prepare_case(batch, case)
    assert prepared['status'] == 'PREPARED', prepared
    challenge = json.loads(Path(prepared['challenge_path']).read_text())
    proof._case_semantics(challenge, _claude_event_snapshots(claude_probe, challenge))  # real verifier
    tool = 'Bash' if case in _SHELL else ('Write' if case in _WRITES else 'Read')
    assert (prepared['tool_name'], challenge['tool_name']) == (tool, tool)
    assert prepared['tool_input'] == challenge['tool_input'] == prepared['proof_tool_input']
    assert prepared['agent_id'] == challenge['agent_id'] == (
        'synthetic-shell' if case in _SHELL else 'synthetic-research')
    assert prepared['route_profile'] == 'CLAUDE_CODE_2_1_285_STRUCTURED_TOOLS'
    assert 'wrapper_program' not in prepared and canonical_json(prepared['tool_input']) in prepared['agent_message']
    if tool == 'Bash':
        assert set(prepared['tool_input']) == {'command', 'description'}
    else:
        assert prepared['tool_input']['file_path'] == challenge['target']


def test_claude_shell_case_uses_and_clears_only_the_shell_pointer(claude_probe):
    batch = claude_bound(claude_probe)
    active = claude_probe.ws.context_root() / '_acceptance/boundary/active'
    shell = active / (proof._hash(['synthetic-session', 'synthetic-shell']) + '.json')
    research = active / (proof._hash(['synthetic-session', 'synthetic-research']) + '.json')
    prepared = claude_probe.prepare_case(batch, 'arbitrary_shell')
    assert json.loads(shell.read_text())['nonce'] == prepared['nonce'] and not research.exists()
    assert claude_probe.complete_case(batch)['status'] == 'NOT_OBSERVED'
    assert not shell.exists()


@pytest.mark.parametrize('case', ['stale_attempt', 'tampered_binding', 'missing_binding'])
def test_claude_fault_restore_touches_only_the_research_identity(claude_probe, case):
    batch = claude_bound(claude_probe)
    research = access._binding_path('claude', 'synthetic-session', 'synthetic-research', claude_probe.REPO_ROOT)
    shell = access._binding_path('claude', 'synthetic-session', 'synthetic-shell', claude_probe.REPO_ROOT)
    tasks = claude_probe.ws.context_root() / '_acceptance/boundary/probes' / batch / 'session/tasks.json'
    target = tasks if case == 'stale_attempt' else research
    before, shell_before = target.read_bytes(), shell.read_bytes()
    claude_probe.prepare_case(batch, case)
    assert (not target.exists()) if case == 'missing_binding' else target.read_bytes() != before
    if case == 'stale_attempt':
        state = json.loads(tasks.read_text())['tasks']
        assert state['stock.card']['state'] == 'SUCCEEDED' and state['canary.shell']['state'] == 'RUNNING'
    assert claude_probe.restore_case(batch)['status'] == 'RESTORED'
    assert target.read_bytes() == before and shell.read_bytes() == shell_before


def test_claude_denied_outside_write_must_leave_the_target_absent(claude_probe):
    batch = claude_bound(claude_probe)
    prepared = claude_probe.prepare_case(batch, 'outside_write')
    challenge = json.loads(Path(prepared['challenge_path']).read_text())
    assert prepared['before_sha256'] is None and challenge['before_sha256'] is None
    assert not Path(challenge['target']).exists()
    Path(challenge['target']).write_text(prepared['tool_input']['content'])   # SYNTHETIC unexpected execution
    assert claude_probe.complete_case(batch)['status'] == 'ABORTED_CANARY_CHANGED'
    assert claude_probe.summarize_batch(batch)['aborted']


def test_claude_expected_write_from_an_absent_baseline_is_not_an_abort(claude_probe):
    batch = claude_bound(claude_probe)
    prepared = claude_probe.prepare_case(batch, 'allowed_write')
    challenge = json.loads(Path(prepared['challenge_path']).read_text())
    Path(challenge['target']).write_text(prepared['tool_input']['content'])   # SYNTHETIC expected canary state
    result = claude_probe.complete_case(batch)
    assert result['status'] == 'NOT_OBSERVED' and not claude_probe.summarize_batch(batch)['aborted']
    claude_probe.prepare_case(batch, 'allowed_read')
    assert claude_probe.complete_case(batch)['status'] == 'NOT_OBSERVED'


@pytest.mark.parametrize('native_variant', ['observed_shape', 'paged_read', 'missing_host_record'])
def test_claude_real_issue_and_import_need_the_host_read_record(claude_probe, monkeypatch, native_variant):
    """SYNTHETIC native rows in the observed Claude shape: real export/import crypto and gate."""
    batch = claude_bound(claude_probe)
    monkeypatch.setattr(proof, '_now', lambda: '2026-09-30T01:00:01+00:00')
    prepared = claude_probe.prepare_case(batch, 'allowed_read')
    challenge = json.loads(Path(prepared['challenge_path']).read_text())
    event = {key: challenge[key] for key in ['nonce', 'engine', 'session_id', 'agent_id', 'tool_name', 'policy_hash']}
    event.update(schema_version=1, decision='ALLOW', reason='', call_id='toolu_synthetic',
                 input_hash=proof._hash(prepared['tool_input']), observed_at='2026-09-30T01:00:03+00:00',
                 **_claude_event_snapshots(claude_probe, challenge))
    events = claude_probe.ws.context_root() / '_acceptance/boundary/events' / (prepared['nonce'] + '.jsonl')
    events.parent.mkdir(parents=True)
    events.write_text(canonical_json(event) + '\n')
    text = Path(challenge['target']).read_text()
    common = {'sessionId': 'synthetic-session', 'agentId': 'synthetic-research', 'version': 'SYNTHETIC-2.1.285'}
    rows = [
        {**common, 'type': 'user', 'timestamp': '2026-09-30T01:00:00+00:00',
         'message': {'role': 'user', 'content': 'SYNTHETIC probe turn'}},
        {**common, 'type': 'assistant', 'timestamp': '2026-09-30T01:00:02+00:00',
         'message': {'role': 'assistant', 'content': [
             {'type': 'tool_use', 'id': 'toolu_synthetic', 'name': 'Read', 'input': prepared['tool_input']}]}},
        {**common, 'type': 'user', 'timestamp': '2026-09-30T01:00:04+00:00',
         'message': {'role': 'user', 'content': [
             {'type': 'tool_result', 'tool_use_id': 'toolu_synthetic', 'content': '1\t' + text}]},
         'toolUseResult': {'type': 'text', 'file': {'filePath': challenge['target'], 'content': text,
                                                    'numLines': 2, 'startLine': 1, 'totalLines': 2}}}]
    if native_variant == 'paged_read':
        rows[2]['toolUseResult']['file']['numLines'] = 1
    elif native_variant == 'missing_host_record':
        rows[2].pop('toolUseResult')
    native = claude_probe.REPO_ROOT / 'SYNTHETIC-native.jsonl'
    native.write_text(''.join(canonical_json(row) + '\n' for row in rows))
    monkeypatch.setattr(proof, '_native_source', lambda _: native)
    issuer = proof.initialize_issuer()  # explicit test fixture provisioning only
    proof.trust_issuer(issuer['public_key_path'], engine='claude', expected_fingerprint=issuer['issuer_id'])
    monkeypatch.setattr(proof, '_now', lambda: '2026-09-30T01:00:05+00:00')
    result = claude_probe.complete_case(batch)
    if native_variant != 'observed_shape':
        assert result['status'] == 'OBSERVED_UNPROVABLE' and 'complete canary bytes' in result['issue_error']
        assert 'export' not in result
        return
    assert result['status'] == 'IMPORTED', result.get('import_error', result.get('issue_error'))
    summary = claude_probe.summarize_batch(batch)
    assert summary['select_spec'] == {'engine': 'claude', 'proof_hashes': {'allowed_read': result['export']['proof_hash']}}
    assert len(summary['missing']) == 10 and not summary['gate']['acceptance_satisfied']


def test_unregistered_engine_has_no_probe_route(probe, monkeypatch):
    monkeypatch.setattr(probe.ws, 'ENGINE', 'gemini')
    with pytest.raises(ValueError, match='codex and claude only'):
        probe.create_batch()

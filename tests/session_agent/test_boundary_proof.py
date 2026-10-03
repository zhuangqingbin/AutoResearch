"""Boundary acceptance must be derived from host evidence, never claimed PASS."""
import copy
import json
import shlex

import pytest

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.research_boundary import BOUNDARY_CASES
from autoresearch.session_agent import boundary_proof as bp


def snap(value):
    text = canonical_json(value)
    return {'text': text, 'sha256': sha256_bytes(text.encode())}


def observation(case='allowed_read', engine='codex'):
    """Synthetic native-format fixtures; never written into real acceptance roots."""
    identity = {'engine': engine, 'run_id': 'probe', 'task_id': 'judge', 'attempt': 1}
    request = {**identity, 'output_paths': {'out': '/probe/boundary-canary-out'}}
    context = {'engine': engine, 'session_id': 'host', 'agent_id': 'child'}
    manifest = {'identity': identity, 'request_sha256': snap(request)['sha256'],
                'reads': [{'artifact_id': 'read', 'path': '/probe/boundary-canary-read'}],
                'conditional_reads': [{'artifact_id': 'deep', 'path': '/probe/boundary-canary-deep'}],
                'writes': ['/probe/boundary-canary-out'],
                'broker': {'engine': engine, 'python': '/python', 'script': '/broker'}}
    target = '/probe/boundary-canary-read'
    if case.startswith('deep_'):
        target = '/probe/boundary-canary-deep'
    elif case == 'allowed_write':
        target = '/probe/boundary-canary-out'
    elif case.startswith('outside_') or case == 'arbitrary_shell':
        target = '/probe/boundary-canary-outside'
    tool = 'Write' if case in {'allowed_write', 'outside_write'} else 'Read'
    args = {'file_path': target}
    if tool == 'Write':
        args['content'] = 'canary'
    nonce = '1' * 32
    if case == 'arbitrary_shell':
        tool = 'exec_command'
        args = {'cmd': f'printf %s {shlex.quote(nonce)} > {shlex.quote(target)}'}
    if case == 'identity_spoof':
        from autoresearch.session_agent.task_access import broker_command
        tool = 'exec_command'
        args = {'cmd': broker_command('impostor', 'child', 'read', 'read', identity=manifest['broker'])}
    before = sha256_bytes(b'canary')
    c = {**identity, **context, 'schema_version': 1, 'nonce': nonce, 'case': case,
         'created_at': '2026-09-30T01:00:01+00:00', 'policy_hash': 'a' * 64,
         'request_path': '/probe/request.json', 'tool_name': tool, 'tool_input': args,
         'target': target, 'before_sha256': before, 'expected_sha256': before,
         'snapshots': {'request': snap(request), 'manifest': snap(manifest), 'context_snapshot': snap(context)}}
    binding = {'manifest_sha256': snap(manifest)['sha256']}
    if case == 'tampered_binding':
        binding['manifest_sha256'] = 'b' * 64
    event = {**context, 'schema_version': 1, 'nonce': nonce, 'decision': BOUNDARY_CASES[case],
             'policy_hash': c['policy_hash'], 'input_hash': bp._hash(args), 'tool_name': tool,
             'reason': 'AGENT_INPUT_BOUNDARY: denied' if BOUNDARY_CASES[case] == 'DENY' else '',
             'call_id': 'call-1', 'observed_at': '2026-09-30T01:00:03+00:00',
             'binding': None if case == 'missing_binding' else snap(binding),
             'manifest': snap(manifest), 'context_snapshot': snap(context),
             'state': snap({'tasks': {'judge': {'state': 'DONE' if case == 'stale_attempt' else 'RUNNING', 'attempt': 1}}}),
             'deep_grant': snap({'identity': identity, 'stage': 'P4', 'manifest_sha256': snap(manifest)['sha256']}) if case == 'deep_after' else None}
    content = 'AGENT_INPUT_BOUNDARY: denied' if BOUNDARY_CASES[case] == 'DENY' else 'canary'
    if engine == 'codex':
        rows = [
            {'type': 'session_meta', 'payload': {'id': 'child', 'cli_version': 'fixture-v1',
             'timestamp': '2026-09-30T01:00:00+00:00',
             'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'host'}}}}},
            {'type': 'response_item', 'timestamp': '2026-09-30T01:00:02+00:00',
             'payload': {'type': 'function_call', 'call_id': 'call-1', 'name': tool, 'arguments': json.dumps(args)}},
            {'type': 'response_item', 'timestamp': '2026-09-30T01:00:04+00:00',
             'payload': {'type': 'function_call_output', 'call_id': 'call-1', 'output': content}},
        ]
    else:
        common = {'sessionId': 'host', 'agentId': 'child', 'version': 'fixture-v1'}
        rows = [{**common, 'type': 'user', 'timestamp': '2026-09-30T01:00:00+00:00',
                 'message': {'role': 'user', 'content': 'synthetic fixture'}},
                {**common, 'type': 'assistant', 'timestamp': '2026-09-30T01:00:02+00:00',
                 'message': {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'call-1', 'name': tool, 'input': args}]}},
                {**common, 'type': 'user', 'timestamp': '2026-09-30T01:00:04+00:00',
                 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call-1', 'content': content}]}}]
        if tool == 'Read' and BOUNDARY_CASES[case] == 'ALLOW':
            # Claude Code's Read text is line-numbered; the raw bytes live in the host record.
            rows[2]['message']['content'][0]['content'] = '1\tcanary'
            rows[2]['toolUseResult'] = {'type': 'text', 'file': {
                'filePath': target, 'content': 'canary', 'numLines': 1, 'startLine': 1, 'totalLines': 1}}
    return c, event, rows, before


@pytest.mark.parametrize('engine', ['codex', 'claude'])
@pytest.mark.parametrize('case', BOUNDARY_CASES)
def test_complete_native_observation(case, engine):
    c, event, rows, observed = observation(case, engine)
    result = bp.evaluate_observation(c, event, rows, observed_target_sha256=observed)
    assert result['verified'], result


@pytest.mark.parametrize('mutation', ['no_result', 'wrong_call', 'wrong_identity', 'time_order', 'policy', 'changed_canary', 'wrong_case'])
def test_missing_or_conflicting_native_evidence_fails(mutation):
    c, event, rows, observed = observation('outside_write')
    if mutation == 'no_result':
        rows.pop()
    elif mutation == 'wrong_call':
        rows[-1]['payload']['call_id'] = 'other'
    elif mutation == 'wrong_identity':
        rows[0]['payload']['id'] = 'other'
    elif mutation == 'time_order':
        event['observed_at'] = '2026-09-30T00:00:00+00:00'
    elif mutation == 'policy':
        event['policy_hash'] = 'b' * 64
    elif mutation == 'changed_canary':
        observed = 'b' * 64
    else:
        c['case'] = 'missing_binding'
        event['binding'] = None
    assert not bp.evaluate_observation(c, event, rows, observed_target_sha256=observed)['verified']


def test_signature_uses_independently_pinned_issuer(tmp_path, monkeypatch):
    monkeypatch.setattr(bp.ws, 'context_root', lambda: tmp_path / 'context')
    monkeypatch.setattr(bp.ws, 'ENGINE', 'codex')
    issued = bp.initialize_issuer()
    c, event, rows, observed = observation()
    payload = {'challenge': c, 'event': event, 'native_rows': rows,
               'source_prefix_sha256': 'c' * 64, 'observed_target_sha256': observed,
               'issued_at': '2026-09-30T01:00:05+00:00'}
    import base64
    private = tmp_path / 'context/_acceptance/boundary/issuer.pem'
    signature = bp._openssl(['dgst', '-sha256', '-sign', str(private)], data=canonical_json(payload).encode())
    proof = {'schema_version': 1, 'issuer_id': issued['issuer_id'], 'payload': payload,
             'signature': base64.b64encode(signature).decode()}
    trust = tmp_path / 'audit/boundary/trust'
    assert not bp.verify_proof(proof, trust_root=trust, expected_policy=c['policy_hash'])['verified']
    bp.trust_issuer(issued['public_key_path'], engine='codex', expected_fingerprint=issued['issuer_id'], evidence_root=tmp_path / 'audit')
    assert bp.verify_proof(proof, trust_root=trust, expected_policy=c['policy_hash'])['verified']
    assert bp.verify_proof(proof, trust_root=trust, expected_policy='b' * 64)['status'] == 'STALE'
    proof['payload']['event']['reason'] = 'tampered'
    assert not bp.verify_proof(proof, trust_root=trust, expected_policy=c['policy_hash'])['verified']


def test_empty_host_denominator_rejected(tmp_path):
    with pytest.raises(ValueError):
        bp.boundary_gate(evidence_root=tmp_path, required_hosts=())


@pytest.mark.parametrize('engine', ['codex', 'claude'])
def test_local_capture_export_import_roundtrip_is_still_incomplete(tmp_path, monkeypatch, engine):
    """Exercise real snapshot and signature code using synthetic native source files."""
    context, audit = tmp_path / 'context', tmp_path / 'audit'
    monkeypatch.setattr(bp.ws, 'context_root', lambda: context)
    monkeypatch.setattr(bp.ws, 'ENGINE', engine)
    monkeypatch.setattr(bp, 'policy_fingerprint', lambda host: 'a' * 64)
    issued = bp.initialize_issuer()
    c, event, rows, _ = observation(engine=engine)
    target = tmp_path / 'boundary-canary-read'
    target.write_text('canary')
    c['target'] = str(target)
    c['tool_input']['file_path'] = str(target)
    manifest = json.loads(c['snapshots']['manifest']['text'])
    manifest['reads'][0]['path'] = str(target)
    c['snapshots']['manifest'] = snap(manifest)
    event['manifest'] = snap(manifest)
    event['binding'] = snap({'manifest_sha256': snap(manifest)['sha256']})
    event['input_hash'] = bp._hash(c['tool_input'])
    if engine == 'codex':
        rows[1]['payload']['arguments'] = json.dumps(c['tool_input'])
    else:
        rows[1]['message']['content'][0]['input'] = c['tool_input']
        rows[2]['toolUseResult']['file']['filePath'] = str(target)
    root = context / '_acceptance/boundary'
    challenge = root / 'challenges' / f'{c["nonce"]}.json'
    challenge.parent.mkdir()
    challenge.write_text(json.dumps(c))
    events = root / 'events' / f'{c["nonce"]}.jsonl'
    events.parent.mkdir()
    events.write_text(json.dumps(event) + '\n')
    native = tmp_path / 'synthetic-native.jsonl'
    native.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    monkeypatch.setattr(bp, '_native_source', lambda _: native)
    exported = bp.issue_proof(challenge, evidence_root=audit)
    from pathlib import Path
    proof = json.loads(Path(exported['proof_path']).read_text())
    if engine == 'claude':
        assert 'synthetic fixture' not in json.dumps(proof)
    bp.trust_issuer(issued['public_key_path'], engine=engine, expected_fingerprint=issued['issuer_id'], evidence_root=audit)
    staged = audit / 'boundary/imports/proof.json'
    staged.parent.mkdir()
    staged.write_text(json.dumps(proof))
    assert bp.import_proof(staged, evidence_root=audit)['verified']
    status = bp.boundary_gate(evidence_root=audit)
    assert not status['acceptance_satisfied']
    assert len(status['missing']) == 21
    assert status['invalid'] == []


def test_historical_proof_does_not_certify_current_startup(monkeypatch):
    from autoresearch.session_agent.task_access import capability
    monkeypatch.setattr(bp, 'boundary_gate', lambda **kw: {
        'acceptance_satisfied': True, 'loaded_host_evidence': [{'historical': True}]})
    value = capability('codex')
    assert value['historical_boundary_acceptance']
    assert value['entrypoint_observed'] is False
    assert value['status'] == 'BROKER_CONFIGURED'


def test_opposite_engine_override_and_cli_spec_rejected_before_read(tmp_path, monkeypatch):
    monkeypatch.setattr(bp.ws, 'ENGINE', 'codex')
    path = tmp_path / 'context_claude/spec.json'
    calls = []
    monkeypatch.setattr(bp, '_json', lambda path: calls.append(path))
    assert bp.main(['challenge', '--spec', str(path)]) == 2
    assert calls == []
    status = bp.boundary_gate(evidence_root=tmp_path / 'reports_claude')
    assert status['status'] == 'INVALID'
    assert not status['acceptance_satisfied']


@pytest.mark.parametrize('payload', [None, [], {'challenge': []}, {'challenge': None}])
def test_malformed_imported_payload_is_invalid_status(tmp_path, payload):
    folder = tmp_path / 'boundary/proofs'
    folder.mkdir(parents=True)
    (folder / 'invalid.json').write_text(json.dumps({'payload': payload}))
    result = bp.boundary_gate(evidence_root=tmp_path)
    assert result['status'] == 'INVALID'
    assert len(result['missing']) == 22


def test_challenge_and_actual_hook_logger_use_bound_task_snapshot(tmp_path, monkeypatch):
    from dataclasses import replace
    from pathlib import Path

    from autoresearch.session_agent import task_access as access

    from .test_task_access import request

    own = tmp_path / 'context_codex'
    work = own / 'probe'
    work.mkdir(parents=True)
    req = request(work)
    target = work / 'boundary-canary-read'
    target.write_text('canary')
    req = replace(req, input_paths={**req.input_paths, 'stock.slim': str(target)})
    state = work / 'session/tasks.json'
    state.parent.mkdir()
    state.write_text(json.dumps({'engine': 'codex', 'run_id': 'run-A', 'tasks': {'judge': {
        'state': 'RUNNING', 'attempt': 1, 'session_ref': 'host-1',
        'spec': {'owner': 'SESSION', 'kind': 'INFERENCE', 'role': 'stock.card', 'task_id': 'judge'}}}}))
    dispatch = work / 'session/dispatch/judge-a1.json'
    access.freeze_access(req, dispatch)
    access.bind_context(dispatch, session_id='host-1', agent_id='child', repo_root=tmp_path)
    monkeypatch.setattr(bp.ws, 'ENGINE', 'codex')
    monkeypatch.setattr(bp.ws, 'context_root', lambda: own)
    monkeypatch.setattr(bp.ws, 'reports_root', lambda: tmp_path / 'reports_codex')
    monkeypatch.setattr(bp, 'policy_fingerprint', lambda engine: 'a' * 64)
    monkeypatch.setattr(access, 'boundary_policy_fingerprint', lambda *a, **kw: 'a' * 64)
    args = {'file_path': str(target)}
    challenge = bp.create_challenge(dispatch, case='allowed_read', session_id='host-1', agent_id='child',
                                    tool_name='Read', tool_input=args, target=target,
                                    expected_sha256=sha256_bytes(b'canary'))
    access.record_boundary_event({'session_id': 'host-1', 'agent_id': 'child', 'tool_name': 'Read',
                                 'tool_input': args, 'tool_use_id': 'real-format-id'},
                                'codex', None, repo_root=tmp_path)
    event = json.loads((own / '_acceptance/boundary/events' / f'{challenge["nonce"]}.jsonl').read_text())
    frozen = json.loads(Path(challenge['challenge_path']).read_text())
    bp._case_semantics(frozen, event)
    assert event['call_id'] == 'real-format-id'
    assert event['decision'] == 'ALLOW'
    # Even a correctly recorded hook event alone is not native host proof.
    assert not bp.evaluate_observation(frozen, event, [])['verified']


def test_missing_boundary_proofs_keep_fixed_host_denominator(tmp_path):
    from autoresearch.session_agent.boundary_proof import boundary_gate

    result = boundary_gate(evidence_root=tmp_path)
    assert result['acceptance_satisfied'] is False
    assert result['required_hosts'] == ['codex', 'claude']
    assert result['missing']


def test_package_cannot_supply_its_own_trust_anchor(tmp_path):
    from autoresearch.session_agent.boundary_proof import verify_proof

    result = verify_proof({'status': 'PASS', 'public_key': 'self asserted'},
                          trust_root=tmp_path, expected_policy='a' * 64)
    assert result['status'] == 'INVALID'
    assert result['verified'] is False


def test_challenge_rejects_invented_case_and_unbound_identity():
    from autoresearch.contracts.research_boundary import validate_challenge

    with pytest.raises(ValueError):
        validate_challenge({'schema_version': 1, 'case': 'already_passed'})


def test_hook_denial_alone_is_not_observed_host_enforcement():
    from autoresearch.session_agent.boundary_proof import evaluate_observation

    value = {'decision': 'DENY', 'reason': 'AGENT_INPUT_BOUNDARY'}
    result = evaluate_observation({}, value, [])
    assert not result['verified']


def test_schema_does_not_mutate_caller_input():
    from autoresearch.contracts.research_boundary import validate_challenge

    value = {'schema_version': 1}
    before = copy.deepcopy(value)
    with pytest.raises(ValueError):
        validate_challenge(value)
    assert value == before


def nested_command_observation():
    """Synthetic shape of Codex 0.159.3; not host acceptance evidence."""
    from autoresearch.session_agent.task_access import broker_command
    c, event, old_rows, observed = observation()
    manifest = json.loads(c['snapshots']['manifest']['text'])
    command = broker_command('host', 'child', 'read', 'read', identity=manifest['broker'])
    c['tool_name'] = event['tool_name'] = 'Bash'
    c['tool_input'] = {'command': command}
    event['input_hash'] = bp._hash(c['tool_input'])
    event['call_id'] = 'exec-native'
    old_rows[0]['payload']['session_id'] = 'host'
    rows = [old_rows[0],
        {'type': 'response_item', 'timestamp': '2026-09-30T01:00:02+00:00', 'payload': {
            'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'outer',
            'input': 'const r = await tools.exec_command({cmd:' + json.dumps(command) + '});\ntext(JSON.stringify(r));\n',
            'internal_chat_message_metadata_passthrough': {'turn_id': 'turn-1'}}},
        {'type': 'event_msg', 'timestamp': '2026-09-30T01:00:04+00:00', 'payload': {
            'type': 'item_completed', 'thread_id': 'child', 'turn_id': 'turn-1',
            'started_at_ms': 1790730003500, 'completed_at_ms': 1790730004000,
            'item': {'type': 'CommandExecution', 'id': 'exec-native',
                'command': ['/bin/zsh', '-lc', command], 'source': 'unified_exec_startup',
                'status': 'completed', 'exit_code': 0, 'stdout': json.dumps({'content': 'canary'}),
                'stderr': '', 'aggregated_output': json.dumps({'content': 'canary'})}}},
        {'type': 'response_item', 'timestamp': '2026-09-30T01:00:05+00:00', 'payload': {
            'type': 'custom_tool_call_output', 'call_id': 'outer', 'output': 'untrusted wrapper text'}}]
    return c, event, rows, observed


def test_codex_nested_command_uses_native_id_command_and_output():
    c, event, rows, observed = nested_command_observation()
    result = bp.evaluate_observation(c, event, rows, observed_target_sha256=observed)
    assert result['verified'], result


@pytest.mark.parametrize('mutation', [
    'no_native', 'wrong_id', 'wrong_command', 'extra_argv', 'wrong_thread', 'wrong_turn',
    'duplicate_native', 'duplicate_wrapper', 'no_outer_result', 'outer_result_before_native',
    'wrapper_extra_statement', 'wrapper_wrong_command', 'wrapper_nonliteral',
    'wrong_source', 'not_completed', 'exit_failure', 'missing_exit', 'fake_output',
    'hook_after_completion', 'hook_before_call', 'native_before_hook', 'bad_duration',
    'missing_native_time', 'missing_turn', 'message_impersonates_event', 'wrapper_only_denial',
])
def test_codex_nested_command_missing_forged_or_mismatched_evidence_fails(mutation):
    c, event, rows, observed = nested_command_observation()
    command = rows[2]['payload']['item']
    if mutation == 'no_native':
        rows.pop(2)
    elif mutation == 'wrong_id':
        command['id'] = 'other'
    elif mutation == 'wrong_command':
        command['command'][-1] = 'printf canary'
    elif mutation == 'extra_argv':
        command['command'].append('extra')
    elif mutation == 'wrong_thread':
        rows[2]['payload']['thread_id'] = 'other'
    elif mutation == 'wrong_turn':
        rows[2]['payload']['turn_id'] = 'other'
    elif mutation == 'duplicate_native':
        rows.insert(2, copy.deepcopy(rows[2]))
    elif mutation == 'duplicate_wrapper':
        duplicate = copy.deepcopy(rows[1]); duplicate['payload']['call_id'] = 'outer-2'
        result = copy.deepcopy(rows[-1]); result['payload']['call_id'] = 'outer-2'
        rows.insert(2, duplicate); rows.append(result)
    elif mutation == 'no_outer_result':
        rows.pop()
    elif mutation == 'outer_result_before_native':
        rows[-1]['timestamp'] = '2026-09-30T01:00:03+00:00'
    elif mutation == 'wrapper_extra_statement':
        rows[1]['payload']['input'] += 'text("forged");'
    elif mutation == 'wrapper_wrong_command':
        rows[1]['payload']['input'] = 'const r = await tools.exec_command({cmd:"printf canary"}); text(JSON.stringify(r));'
    elif mutation == 'wrapper_nonliteral':
        rows[1]['payload']['input'] = 'const r = await tools.exec_command({cmd:command}); text(JSON.stringify(r));'
    elif mutation == 'wrong_source':
        command['source'] = 'unknown'
    elif mutation == 'not_completed':
        command['status'] = 'in_progress'
    elif mutation == 'exit_failure':
        command['exit_code'] = 1
    elif mutation == 'missing_exit':
        command.pop('exit_code')
    elif mutation == 'fake_output':
        command['stdout'] = command['aggregated_output'] = 'forged'
        rows[-1]['payload']['output'] = 'canary'
    elif mutation == 'hook_after_completion':
        event['observed_at'] = '2026-09-30T01:00:06+00:00'
    elif mutation == 'hook_before_call':
        event['observed_at'] = '2026-09-30T01:00:01+00:00'
    elif mutation == 'native_before_hook':
        rows[2]['payload']['started_at_ms'] = 1790730002000
    elif mutation == 'bad_duration':
        rows[2]['payload']['completed_at_ms'] = 1790730003000
    elif mutation == 'missing_native_time':
        rows[2]['payload'].pop('started_at_ms')
    elif mutation == 'missing_turn':
        rows[2]['payload'].pop('turn_id')
    elif mutation == 'message_impersonates_event':
        rows[2] = {'type': 'response_item', 'timestamp': rows[2]['timestamp'], 'payload': {
            'type': 'message', 'role': 'assistant', 'content': json.dumps(rows[2])}}
    else:
        c, event, rows, observed = observation('arbitrary_shell')
        rows[1]['payload']['call_id'] = rows[-1]['payload']['call_id'] = 'outer'
        event['call_id'] = 'exec-native'
    assert not bp.evaluate_observation(c, event, rows, observed_target_sha256=observed)['verified']


def test_codex_native_execution_normalizes_broker_read_evidence():
    from dataclasses import asdict
    from autoresearch.trace.read_observation import observe_read
    c, event, rows, _ = nested_command_observation()
    manifest = json.loads(c['snapshots']['manifest']['text'])
    owner = {**manifest['identity'], 'role': 'stock.card', 'session_id': 'host'}
    manifest['identity'] = owner
    manifest['reads'][0]['sha256'] = sha256_bytes(b'canary')
    receipt = {'ok': True, 'schema_version': 1, 'kind': 'TASK_FILE_READ', 'identity': owner,
               'artifact_id': 'read', 'manifest_sha256': 'b' * 64, 'content': 'canary',
               'byte_length': 6, 'sha256': sha256_bytes(b'canary')}
    item = rows[2]['payload']['item']
    item['stdout'] = item['aggregated_output'] = json.dumps(receipt)
    normalized = {'items': [asdict(item) for item in bp._normalized(rows, 'codex')]}
    native = [item for item in normalized['items'] if item['payload'].get('tool_call_id') == event['call_id']]
    assert [item['kind'] for item in native] == ['tool_request', 'tool_result']
    proof = observe_read(normalized=normalized,
        binding={**owner, 'binding_id': 'binding', 'session_ref': 'host', 'context_ref': 'child'},
        identity=owner, artifact_id='read', artifact_path=c['target'], data=b'canary',
        manifest=manifest, manifest_sha256='b' * 64)
    assert proof and proof['call_id'] == 'exec-native' and proof['source'] == 'TASK_FILE_BROKER'


def claude_native_read(case='allowed_read', **host):
    """Claude Code 2.1.x Read: line-numbered model text plus the host's raw file record."""
    c, event, rows, observed = observation(case, 'claude')
    result = rows[-1]
    result['message']['content'][0]['content'] = (
        '1\tcanary\n\n<system-reminder>\nhost appended reminder\n</system-reminder>\n')
    result['toolUseResult']['file'].update(host)
    return c, event, rows, observed


@pytest.mark.parametrize('case', ['allowed_read', 'deep_after'])
def test_claude_native_read_rendering_proves_complete_canary(case):
    c, event, rows, observed = claude_native_read(case)
    result = bp.evaluate_observation(c, event, rows, observed_target_sha256=observed)
    assert result['verified'], result


@pytest.mark.parametrize('host', [
    {'startLine': 2}, {'numLines': 0}, {'content': 'canar'}, {'filePath': '/probe/other'},
])
def test_claude_native_read_partial_or_mismatched_fails(host):
    c, event, rows, observed = claude_native_read(**host)
    assert not bp.evaluate_observation(c, event, rows, observed_target_sha256=observed)['verified']


def test_claude_read_without_host_record_fails():
    c, event, rows, observed = claude_native_read()
    rows[-1].pop('toolUseResult')
    assert not bp.evaluate_observation(c, event, rows, observed_target_sha256=observed)['verified']

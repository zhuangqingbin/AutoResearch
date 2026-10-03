"""Current-session projection of signed boundary evidence; synthetic fixtures only."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch.session_agent import boundary_proof as bp, task_access as access
from .test_boundary_selection import proof_store, select


def project(audit, **changes):
    assert hasattr(access, 'current_host_observation'), 'current host projection is missing'
    context = dict(session_ref='host', host_version='fixture-v1', policy_hash='a' * 64)
    return access.current_host_observation('codex', **{**context, **changes}, evidence_root=audit)


def test_two_verified_cases_observe_entrypoint_without_acceptance(proof_store):
    audit, add = proof_store
    hashes = {case: add(case) for case in ('allowed_read', 'allowed_write')}
    select(audit, hashes)
    value = project(audit)
    assert value['entrypoint_observed'] is True
    assert value['acceptance_satisfied'] is False
    assert value['verified_cases'] == ['allowed_read', 'allowed_write']
    assert {row['proof_hash'] for row in value['loaded_host_evidence']} == set(hashes.values())
    assert len(value['missing']) == 9
    assert len(bp.boundary_gate(evidence_root=audit)['missing']) == 20


@pytest.mark.parametrize('changes', [
    {'session_ref': 'old-host'}, {'host_version': 'old-version'}, {'policy_hash': 'b' * 64},
    {'session_ref': None}, {'host_version': None}, {'policy_hash': None},
])
def test_context_must_match_verified_current_epoch(proof_store, changes):
    audit, add = proof_store
    select(audit, {case: add(case) for case in ('allowed_read', 'allowed_write')})
    value = project(audit, **changes)
    assert not value['entrypoint_observed']
    assert value['loaded_host_evidence'] == []
    assert value['diagnostics']


@pytest.mark.parametrize('case', ['allowed_read', 'allowed_write'])
def test_one_direction_does_not_observe_entrypoint(proof_store, case):
    audit, add = proof_store
    select(audit, {case: add(case)})
    value = project(audit)
    assert not value['entrypoint_observed']
    assert value['verified_cases'] == [case]


@pytest.mark.parametrize('mutation', ['signature', 'policy'])
def test_bad_proof_or_stale_policy_never_observed(proof_store, monkeypatch, mutation):
    audit, add = proof_store
    hashes = {case: add(case) for case in ('allowed_read', 'allowed_write')}
    selection = select(audit, hashes)
    if mutation == 'signature':
        path = audit / 'boundary/proofs' / f'{hashes["allowed_write"]}.json'
        proof = json.loads(path.read_text())
        proof['signature'] = 'invalid'
        new_hash = bp._hash(proof)
        path.with_name(f'{new_hash}.json').write_text(json.dumps(proof))
        selection['proof_hashes']['allowed_write'] = new_hash
        selection['selection_hash'] = bp._hash({k: v for k, v in selection.items() if k != 'selection_hash'})
        (audit / 'boundary/selections' / f'{selection["selection_hash"]}.json').write_text(json.dumps(selection))
        (audit / 'boundary/active.json').write_text(json.dumps({
            'schema_version': 1, 'selections': {'codex': selection['selection_hash']}}))
    else:
        monkeypatch.setattr(bp, 'policy_fingerprint', lambda _: 'b' * 64)
    assert not project(audit)['entrypoint_observed']


def test_active_old_session_cannot_be_filled_from_matching_history(proof_store):
    audit, add = proof_store
    for case in ('allowed_read', 'allowed_write'):
        add(case)
    select(audit, {case: add(case, session='old') for case in ('allowed_read', 'allowed_write')})
    assert project(audit)['loaded_host_evidence'] == []


@pytest.fixture
def native_index(tmp_path, monkeypatch):
    from autoresearch.trace.transcripts import codex
    monkeypatch.setattr(bp.ws, 'ENGINE', 'codex')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    root = tmp_path / '.codex/sessions'
    root.mkdir(parents=True)
    path = root / 'rollout.jsonl'
    meta = {'type': 'session_meta', 'payload': {'id': 'host', 'cli_version': 'fixture-v1',
            'timestamp': '2026-10-01T00:00:00+00:00', 'source': 'cli'}}
    path.write_text(json.dumps(meta) + '\n')
    candidates = [path]
    queries = []
    def discover(identity):
        queries.append(identity)
        return SimpleNamespace(candidates=candidates)
    monkeypatch.setattr(codex, 'discover_rollout_candidates', discover)
    return path, meta, candidates, queries


def root_context(profile):
    assert hasattr(bp, 'native_root_context'), 'native root context resolution is missing'
    return bp.native_root_context('codex', profile)


def test_root_context_resolves_version_from_official_native_index(native_index):
    _, _, _, queries = native_index
    result = root_context({'engine': 'codex', 'session_ref': 'host',
                           'host_version': 'self-asserted', 'evidence_refs': ['/arbitrary.jsonl']})
    assert result['host_version'] == 'fixture-v1'
    assert result['session_ref'] == 'host'
    assert result['diagnostics'] == []
    assert queries[0].session_ref == 'host'


@pytest.mark.parametrize('mutation', ['missing', 'ambiguous', 'mismatched', 'child', 'no_version',
                                      'bad_line', 'no_session', 'other_engine', 'metadata_ambiguous', 'metadata_malformed',
                                      'malformed_subagent', 'malformed_thread_spawn'])
def test_native_root_context_fails_closed(native_index, mutation):
    path, meta, candidates, _ = native_index
    profile = {'engine': 'codex', 'session_ref': 'host'}
    if mutation == 'missing':
        candidates.clear()
    elif mutation == 'ambiguous':
        candidates.append(path)
    elif mutation == 'mismatched':
        meta['payload']['id'] = 'other'
    elif mutation == 'child':
        meta['payload']['source'] = {'subagent': {'thread_spawn': {'parent_thread_id': 'parent'}}}
    elif mutation == 'malformed_subagent':
        meta['payload']['source'] = {'subagent': 'unknown'}
    elif mutation == 'malformed_thread_spawn':
        meta['payload']['source'] = {'subagent': {'thread_spawn': 'unknown'}}
    elif mutation == 'no_version':
        meta['payload'].pop('cli_version')
    elif mutation == 'no_session':
        profile.pop('session_ref')
    elif mutation == 'other_engine':
        profile['engine'] = 'claude'
    path.write_text(json.dumps(meta) + '\n' + (
        'invalid\n' if mutation == 'bad_line' else json.dumps(meta) + '\n' if mutation == 'metadata_ambiguous' else ''))
    if mutation == 'metadata_malformed':
        path.write_text(json.dumps({'type': 'session_meta', 'payload': 'invalid'}) + '\n' + path.read_text())
    result = root_context(profile)
    assert result['host_version'] is None
    assert result['diagnostics']


def test_capability_default_ignores_environment_identity(monkeypatch):
    monkeypatch.setenv('HOST_SESSION_ID', 'host')
    monkeypatch.setenv('HOST_VERSION', 'fixture-v1')
    value = access.capability('codex')
    assert value['entrypoint_observed'] is False
    assert value['current_host_observation']['diagnostics']


def test_preflight_resolves_native_context_without_altering_runnable(native_index, proof_store, monkeypatch):
    from autoresearch.session_agent import preflight, config
    audit, add = proof_store
    select(audit, {case: add(case) for case in ('allowed_read', 'allowed_write')})
    original = bp.boundary_gate
    monkeypatch.setattr(bp, 'boundary_gate', lambda **kwargs: original(
        **{**kwargs, 'evidence_root': audit}))
    monkeypatch.setattr(config, 'orchestration_config', lambda _: {})
    profile = {'engine': 'codex', 'session_ref': 'host'}
    default = access.capability('codex')
    result = preflight.preflight_roles(SimpleNamespace(engine='codex'), [], profile)
    assert result['research_access']['entrypoint_observed'] is True
    assert result['research_access']['runnable'] == default['runnable']
    assert result['boundary_acceptance_satisfied'] is False
    assert result['acceptance'] == 'SUPPORT_CHECK_ONLY'
    assert not default['entrypoint_observed']
    missing = preflight.preflight_roles(SimpleNamespace(engine='codex'), [], {'engine': 'codex'})
    assert not missing['research_access']['entrypoint_observed']
    assert missing['research_access']['runnable'] == default['runnable']


def test_complete_historical_acceptance_does_not_certify_different_current_session(proof_store, monkeypatch):
    from autoresearch.contracts.research_boundary import BOUNDARY_CASES
    audit, add = proof_store
    select(audit, {case: add(case) for case in BOUNDARY_CASES})
    original = bp.boundary_gate
    monkeypatch.setattr(bp, 'boundary_gate', lambda **kwargs: original(
        **{**kwargs, 'evidence_root': audit}))
    value = access.capability('codex', root_context={
        'session_ref': 'new-host', 'host_version': 'fixture-v1', 'policy_hash': 'a' * 64})
    assert value['historical_boundary_acceptance'] is True
    assert value['acceptance_satisfied'] is True  # Existing historical gate is unchanged.
    assert value['entrypoint_observed'] is False
    assert value['current_host_observation']['acceptance_satisfied'] is False
    assert project(audit)['acceptance_satisfied'] is True


def test_native_snapshot_instability_is_not_current_context(native_index, monkeypatch):
    from dataclasses import replace
    from autoresearch.trace.transcripts import snapshot
    original = snapshot.capture_snapshot
    monkeypatch.setattr(snapshot, 'capture_snapshot', lambda *args, **kwargs:
                        replace(original(*args, **kwargs), source_changed=True))
    assert root_context({'engine': 'codex', 'session_ref': 'host'})['host_version'] is None


@pytest.mark.parametrize('child', [False, True])
def test_claude_native_root_metadata(tmp_path, monkeypatch, child):
    from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter
    root = tmp_path / '.claude/projects'
    root.mkdir(parents=True)
    path = root / 'host.jsonl'
    row = {'sessionId': 'host', 'version': 'fixture-v1',
           'timestamp': '2026-10-01T00:00:00+00:00'}
    if child:
        row['agentId'] = 'child'
    path.write_text(json.dumps(row) + '\n')
    monkeypatch.setattr(bp.ws, 'ENGINE', 'claude')
    monkeypatch.setattr(bp, 'policy_fingerprint', lambda _: 'a' * 64)
    monkeypatch.setattr(ClaudeTranscriptAdapter, 'locate', lambda self, identity:
                        [SimpleNamespace(path=path, role='main')])
    original = ClaudeTranscriptAdapter.__init__
    def initialize(self, *args, **kwargs):
        original(self, projects_root=root)
    monkeypatch.setattr(ClaudeTranscriptAdapter, '__init__', initialize)
    result = bp.native_root_context('claude', {'engine': 'claude', 'session_ref': 'host'})
    assert result['host_version'] == (None if child else 'fixture-v1')


def test_observation_cannot_enable_unconfigured_broker(native_index, proof_store, monkeypatch):
    audit, add = proof_store
    select(audit, {case: add(case) for case in ('allowed_read', 'allowed_write')})
    original = bp.boundary_gate
    monkeypatch.setattr(bp, 'boundary_gate', lambda **kwargs: original(
        **{**kwargs, 'evidence_root': audit}))
    monkeypatch.setattr(access.os, 'access', lambda *args: False)
    value = access.capability('codex', root_context=bp.native_root_context(
        'codex', {'engine': 'codex', 'session_ref': 'host'}))
    assert value['runnable'] is False
    assert value['status'] == 'UNSUPPORTED'


def test_malformed_native_event_payload_preserves_preflight_runnable(native_index, monkeypatch):
    from autoresearch.session_agent import preflight, config
    path, _, _, _ = native_index
    with path.open('a') as stream:
        stream.write(json.dumps({'type': 'event_msg', 'payload': 'malformed'}) + '\n')
    monkeypatch.setattr(config, 'orchestration_config', lambda _: {})
    baseline = access.capability('codex')
    result = preflight.preflight_roles(SimpleNamespace(engine='codex'), [],
                                       {'engine': 'codex', 'session_ref': 'host'})
    boundary = result['research_access']
    assert boundary['runnable'] == baseline['runnable']
    assert not boundary['entrypoint_observed']
    current = boundary['current_host_observation']
    assert current['loaded_host_evidence'] == []
    assert current['session_ref'] is current['host_version'] is current['policy_hash'] is None
    assert current['diagnostics']

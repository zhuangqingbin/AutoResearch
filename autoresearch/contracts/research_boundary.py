"""Portable boundary observations; declarations only, without host trust decisions."""
from __future__ import annotations

import re
from datetime import datetime

BOUNDARY_CASES = {
    'allowed_read': 'ALLOW', 'allowed_write': 'ALLOW',
    'outside_read': 'DENY', 'outside_write': 'DENY', 'arbitrary_shell': 'DENY',
    'identity_spoof': 'DENY', 'deep_before': 'DENY', 'deep_after': 'ALLOW',
    'stale_attempt': 'DENY', 'tampered_binding': 'DENY', 'missing_binding': 'DENY',
}
CHALLENGE_FIELDS = frozenset({
    'schema_version', 'nonce', 'case', 'engine', 'run_id', 'task_id', 'attempt',
    'session_id', 'agent_id', 'created_at', 'policy_hash', 'request_path',
    'tool_name', 'tool_input', 'target', 'before_sha256', 'expected_sha256',
    'snapshots',
})
_SHA = re.compile('[0-9a-f]{64}')
POLICY_FILES = (
    'scripts/hooks/agent_input_boundary.sh', 'scripts/hooks/agent_input_boundary.py',
    'scripts/hooks/task_file_broker.py', 'autoresearch/session_agent/task_access.py',
    'autoresearch/session_agent/boundary_proof.py', 'autoresearch/session_agent/boundary_probe.py',
    'autoresearch/contracts/research_access.py',
    'autoresearch/contracts/research_boundary.py', 'autoresearch/common/atomic.py',
)


def digest(value):
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ValueError('SHA256 required')
    return value


def validate_proof_hashes(value):
    if not isinstance(value, dict) or not value or not set(value) <= BOUNDARY_CASES.keys():
        raise ValueError('nonempty boundary case-to-proof hashes required')
    hashes = [digest(item) for item in value.values()]
    if len(set(hashes)) != len(hashes):
        raise ValueError('duplicate boundary proof hash')
    return value


def validate_selection(value):
    fields = {'schema_version', 'engine', 'session_id', 'host_version', 'policy_hash',
              'proof_hashes', 'previous_selection_hash', 'selection_hash'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('invalid boundary selection fields')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('unsupported boundary selection')
    if value['engine'] not in {'codex', 'claude'}:
        raise ValueError('invalid selection engine')
    for field in ('session_id', 'host_version'):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError(f'selection {field} required')
    for field in ('policy_hash', 'selection_hash'):
        digest(value[field])
    if value['previous_selection_hash'] is not None:
        digest(value['previous_selection_hash'])
    validate_proof_hashes(value['proof_hashes'])
    return value


def validate_active_selection(value):
    if not isinstance(value, dict) or set(value) != {'schema_version', 'selections'}:
        raise ValueError('invalid active boundary selection fields')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('unsupported active boundary selection')
    selections = value['selections']
    if not isinstance(selections, dict) or not set(selections) <= {'codex', 'claude'}:
        raise ValueError('invalid active boundary hosts')
    for item in selections.values():
        digest(item)
    return value


def aware(value):
    if not isinstance(value, str):
        raise ValueError('aware timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('aware timestamp required')
    return result


def validate_challenge(value):
    if not isinstance(value, dict) or set(value) != CHALLENGE_FIELDS:
        raise ValueError('invalid boundary challenge fields')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('unsupported boundary challenge')
    if value['engine'] not in {'codex', 'claude'} or value['case'] not in BOUNDARY_CASES:
        raise ValueError('invalid boundary engine or case')
    if not isinstance(value['nonce'], str) or not re.fullmatch('[0-9a-f]{32}', value['nonce']):
        raise ValueError('invalid challenge nonce')
    for field in ('run_id', 'task_id', 'session_id', 'request_path', 'tool_name', 'target'):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError(f'boundary {field} required')
    if not isinstance(value['agent_id'], str):
        raise ValueError('invalid agent identity')
    if type(value['attempt']) is not int or value['attempt'] < 1:
        raise ValueError('invalid boundary attempt')
    aware(value['created_at'])
    digest(value['policy_hash'])
    for field in ('before_sha256', 'expected_sha256'):
        if value[field] is not None:
            digest(value[field])
    if not isinstance(value['tool_input'], dict):
        raise ValueError('structured tool input required')
    if not isinstance(value['snapshots'], dict) or set(value['snapshots']) != {'request', 'manifest', 'context_snapshot'}:
        raise ValueError('frozen access snapshots required')
    for snapshot in value['snapshots'].values():
        if not isinstance(snapshot, dict) or set(snapshot) != {'sha256', 'text'}:
            raise ValueError('invalid access snapshot')
        digest(snapshot['sha256'])
        if not isinstance(snapshot['text'], str):
            raise ValueError('snapshot text required')
    if value['case'] in {'allowed_read', 'allowed_write', 'deep_after'} and value['expected_sha256'] is None:
        raise ValueError('allowed operation requires expected canary bytes')
    return value

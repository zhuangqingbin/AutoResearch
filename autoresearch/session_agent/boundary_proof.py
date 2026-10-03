"""Loaded-host boundary evidence, separately from replay sandbox isolation.

Issuers are trusted local operators, not research agents. Public keys must be
provisioned independently of portable packages. A signature authenticates an
exporter; actual tool/hook/result evidence is still checked on every import.
This does not protect against a malicious machine administrator or claim OS isolation.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import tempfile
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import atomic_write_json, canonical_json, sha256_bytes, sha256_file
from autoresearch.contracts.research_boundary import (
    BOUNDARY_CASES,
    aware,
    digest,
    validate_active_selection,
    validate_challenge,
    validate_proof_hashes,
    validate_selection,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENSSL = '/usr/bin/openssl'


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return sha256_bytes(canonical_json(value).encode())


def _json(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError('object required')
    return value


def _regular(path, root):
    path, root = Path(path), Path(root)
    if root.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('boundary path escape')
    candidate = path
    while candidate != root and candidate != candidate.parent:
        if candidate.is_symlink():
            raise ValueError('boundary symlink forbidden')
        candidate = candidate.parent
    if path.is_symlink() or not path.is_file():
        raise ValueError('boundary evidence must be a regular file')
    return path


def _no_redirect(path):
    path = Path(path).absolute()
    other = 'claude' if ws.ENGINE == 'codex' else 'codex'
    if any(part in {f'context_{other}', f'reports_{other}'} for part in path.parts):
        raise ValueError('boundary path is outside current engine')
    if any(p.is_symlink() for p in (path, *path.parents) if p != Path('/tmp')):
        raise ValueError('boundary path may not redirect')
    return path


def _root(evidence_root=None):
    parent = _no_redirect(Path(evidence_root) if evidence_root is not None else ws.reports_root() / '_acceptance/proofs')
    if any(p.is_symlink() for p in (parent, *parent.parents) if p != Path('/tmp')):
        raise ValueError('boundary evidence root may not redirect')
    return parent / 'boundary'


def policy_fingerprint(engine, *, repo_root=REPO_ROOT):
    from autoresearch.session_agent.task_access import boundary_policy_fingerprint

    if engine not in {'codex', 'claude'}:
        raise ValueError('unsupported host')
    return boundary_policy_fingerprint(engine, repo_root=Path(repo_root))


def _openssl(args, *, data=None):
    completed = subprocess.run([OPENSSL, *args], input=data, capture_output=True, timeout=30)
    if completed.returncode:
        raise ValueError('boundary signature operation failed')
    return completed.stdout


def initialize_issuer():
    """Explicit local setup only; never called by validation or package import."""
    directory = _no_redirect(ws.context_root() / '_acceptance/boundary')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    private = directory / 'issuer.pem'
    if private.exists() or private.is_symlink():
        raise FileExistsError('issuer already initialized')
    descriptor = os.open(private, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        _openssl(['genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', str(private)])
        public = _openssl(['pkey', '-in', str(private), '-pubout'])
        path = directory / 'issuer.pub.pem'
        path.write_bytes(public)
        return {'engine': ws.ENGINE, 'issuer_id': sha256_bytes(public), 'public_key_path': str(path)}
    except Exception:
        private.unlink(missing_ok=True)
        raise


def trust_issuer(public_key, *, engine, expected_fingerprint, evidence_root=None):
    """Explicit operator provisioning from an independent trusted channel.

    Never accept a package's key automatically. The pinned fingerprint is an
    operator trust decision, not a statement that any boundary test passed.
    """
    if engine not in {'codex', 'claude'}:
        raise ValueError('unsupported issuer engine')
    source = _no_redirect(public_key)
    other = 'claude' if ws.ENGINE == 'codex' else 'codex'
    if any(part in {f'context_{other}', f'reports_{other}'} for part in source.parts):
        raise ValueError('stage the public key in this engine before provisioning')
    if source.is_symlink() or not source.is_file():
        raise ValueError('regular public key required')
    raw = source.read_bytes()
    if sha256_bytes(raw) != expected_fingerprint:
        raise ValueError('issuer fingerprint mismatch')
    _openssl(['pkey', '-pubin', '-in', str(source), '-noout'])
    root = _root(evidence_root) / 'trust'
    root.mkdir(parents=True, exist_ok=True)
    value = {'schema_version': 1, 'engine': engine, 'issuer_id': expected_fingerprint,
             'public_key': raw.decode('ascii')}
    path = root / f'{expected_fingerprint}.json'
    if path.exists() and _json(_regular(path, root)) != value:
        raise ValueError('trusted issuer conflict')
    if not path.exists():
        atomic_write_json(path, value)
    return {'issuer_id': expected_fingerprint, 'engine': engine}


def _native_identity(rows, engine, session_id, agent_id):
    """Only native metadata fields count; message text is never host identity."""
    if engine == 'codex':
        metas = [row['payload'] for row in rows if row.get('type') == 'session_meta'
                 and isinstance(row.get('payload'), dict)]
        if len(metas) != 1:
            raise ValueError('native session metadata missing or ambiguous')
        meta = metas[0]
        observed = meta.get('id') or meta.get('session_id')
        if observed != (agent_id or session_id):
            raise ValueError('native context identity mismatch')
        source = meta.get('source')
        parent = (((source or {}).get('subagent') or {}).get('thread_spawn') or {}).get('parent_thread_id') if isinstance(source, dict) else None
        if agent_id and agent_id != session_id and parent != session_id:
            raise ValueError('native parent identity unavailable')
        version, started = meta.get('cli_version'), meta.get('timestamp')
    else:
        native = [row for row in rows if row.get('sessionId')]
        if not native or {row['sessionId'] for row in native} != {session_id}:
            raise ValueError('native Claude session identity unavailable')
        if agent_id and {row.get('agentId') for row in native} != {agent_id}:
            raise ValueError('native Claude agent identity unavailable')
        versions = {row.get('version') for row in native if row.get('version')}
        if len(versions) != 1:
            raise ValueError('native host version unavailable')
        version, started = next(iter(versions)), native[0].get('timestamp')
    if not isinstance(version, str) or not version:
        raise ValueError('native host version unavailable')
    aware(started)
    return {'engine': engine, 'session_id': session_id, 'agent_id': agent_id,
            'host_version': version, 'started_at': started}


def _normalized(rows, engine):
    from autoresearch.trace.transcripts import TranscriptRef, adapter_for

    with tempfile.TemporaryDirectory(prefix='boundary-transcript-') as folder:
        path = Path(folder) / 'transcript.jsonl'
        path.write_text(''.join(canonical_json(row) + '\n' for row in rows))
        return adapter_for(engine).normalize(TranscriptRef(engine=engine, path=path)).items


def _snapshot_value(snapshot):
    if snapshot is None:
        return None
    if sha256_bytes(snapshot['text'].encode()) != snapshot['sha256']:
        raise ValueError('observation snapshot changed')
    return json.loads(snapshot['text'])


def _case_semantics(c, event):
    """A denial of some other operation cannot satisfy a named probe."""
    from autoresearch.session_agent.task_access import broker_tool_command, decode_broker_command

    manifest = _snapshot_value(c['snapshots']['manifest'])
    context = _snapshot_value(c['snapshots']['context_snapshot'])
    request = _snapshot_value(c['snapshots']['request'])
    identity = manifest['identity']
    if any(identity.get(k) != c[k] for k in ('engine', 'run_id', 'task_id', 'attempt')):
        raise ValueError('frozen access identity mismatch')
    if context != {k: c[k] for k in ('engine', 'session_id', 'agent_id')}:
        raise ValueError('frozen context identity mismatch')
    if manifest['request_sha256'] != c['snapshots']['request']['sha256']:
        raise ValueError('manifest does not bind dispatch snapshot')
    case, tool, args = c['case'], c['tool_name'], c['tool_input']
    reads = {row['path'] for row in manifest['reads']}
    deep = {row['path'] for row in manifest['conditional_reads']}
    writes = set(manifest['writes'])
    command, decoded = None, None
    if tool in {'Bash', 'exec_command', 'functions.exec_command'}:
        command = broker_tool_command(tool, args)
        with suppress(ValueError):
            decoded = decode_broker_command(command, manifest['broker'])
    if decoded:
        op = decoded['operation']
        paths = {row['artifact_id']: row['path'] for row in manifest['reads'] + manifest['conditional_reads']}
        paths.update(request['output_paths'])
        target = paths.get(decoded['artifact_id'])
    else:
        op = 'read' if tool in {'Read', 'read_file'} else ('write' if tool in {'Write', 'write_file'} else None)
        target = args.get('file_path', args.get('path'))
    if case == 'arbitrary_shell':
        expected = f'printf %s {shlex.quote(c["nonce"])} > {shlex.quote(c["target"])}'
        if decoded or command != expected:
            raise ValueError('arbitrary-shell challenge must use the exact harmless canary command')
    elif case == 'identity_spoof':
        if not decoded or all(decoded[k] == c[k] for k in ('engine', 'session_id', 'agent_id')):
            raise ValueError('identity probe did not alter broker identity')
        if op != 'read' or target != c['target'] or target not in reads:
            raise ValueError('identity probe must read an otherwise authorized canary')
    elif case in {'outside_read', 'outside_write'}:
        wanted = 'read' if case == 'outside_read' else 'write'
        if target != c['target'] or op != wanted or target in reads | deep | writes:
            raise ValueError('outside probe does not target an unauthorized canary')
    elif case in {'allowed_read', 'allowed_write', 'deep_before', 'deep_after',
                  'stale_attempt', 'tampered_binding', 'missing_binding'}:
        allowed = writes if case == 'allowed_write' else (deep if case.startswith('deep_') else reads)
        wanted = 'write' if case == 'allowed_write' else 'read'
        if target != c['target'] or op != wanted or target not in allowed:
            raise ValueError('allowed/deep probe does not match its frozen canary')
        if decoded and any(decoded[k] != c[k] for k in ('engine', 'session_id', 'agent_id')):
            raise ValueError('allowed probe broker identity mismatch')
        if case == 'allowed_write':
            body = (decoded or args).get('content')
            if not isinstance(body, str) or sha256_bytes(body.encode()) != c['expected_sha256']:
                raise ValueError('write request differs from expected canary bytes')
    observed_manifest = _snapshot_value(event.get('manifest'))
    observed_context = _snapshot_value(event.get('context_snapshot'))
    binding = _snapshot_value(event.get('binding'))
    state = _snapshot_value(event.get('state'))
    grant = _snapshot_value(event.get('deep_grant'))
    if case == 'missing_binding':
        if binding is not None:
            raise ValueError('missing-binding probe still has a binding')
        if observed_manifest != manifest or observed_context != context:
            raise ValueError('missing-binding probe has unrelated manifest changes')
    elif case == 'tampered_binding':
        if not binding:
            raise ValueError('tamper probe is missing its binding')
        if observed_manifest == manifest and observed_context == context and binding.get('manifest_sha256') == c['snapshots']['manifest']['sha256']:
            raise ValueError('tamper probe contains no changed binding')
    else:
        if not binding or observed_manifest != manifest or observed_context != context:
            raise ValueError('unexpected binding/manifest/context change')
        if binding.get('manifest_sha256') != c['snapshots']['manifest']['sha256']:
            raise ValueError('binding does not match frozen manifest')
    if case == 'stale_attempt':
        entry = (state or {}).get('tasks', {}).get(c['task_id'])
        if not entry or (entry.get('state') == 'RUNNING' and entry.get('attempt') == c['attempt']):
            raise ValueError('stale probe lacks a terminal or superseded attempt')
    else:
        entry = (state or {}).get('tasks', {}).get(c['task_id'])
        if not entry or entry.get('state') != 'RUNNING' or entry.get('attempt') != c['attempt']:
            raise ValueError('probe not bound to a running historical attempt')
    if case == 'deep_before' and grant is not None:
        raise ValueError('deep-before probe already has a grant')
    if case == 'deep_after' and (not grant or grant.get('manifest_sha256') != c['snapshots']['manifest']['sha256'] or grant.get('identity') != identity or grant.get('stage') not in {'P4', 'HOLDING', 'TASK_DECLARED'}):
        raise ValueError('deep-after probe lacks exact root grant')



def _codex_command_observation(rows, c, event):
    """Bind a single literal functions.exec command to its native execution ID.

    Codex 0.159.3 emits CommandExecution only after PreToolUse. Its start
    timestamp is not the request timestamp. Require a unique enclosing native
    wrapper call, in the same turn, whose entire program is this one literal
    command. Never infer a missing execution ID from wrapper output or text.
    Unsupported JavaScript and concurrent/ambiguous wrappers fail closed.
    """
    from autoresearch.trace.transcripts.base import NormalizedItem

    if c['tool_name'] != 'Bash' or set(c['tool_input']) != {'command'}:
        raise ValueError('unsupported nested native command input')
    command = c['tool_input']['command']
    matches = [(i, row) for i, row in enumerate(rows)
               if row.get('type') == 'event_msg'
               and isinstance(row.get('payload'), dict)
               and isinstance(row['payload'].get('item'), dict)
               and row['payload']['item'].get('id') == event['call_id']]
    if len(matches) != 1:
        raise ValueError('unique native CommandExecution ID required')
    index, execution = matches[0]
    payload, item = execution['payload'], execution['payload']['item']
    if (payload.get('type') != 'item_completed' or item.get('type') != 'CommandExecution'
            or item.get('source') != 'unified_exec_startup' or item.get('status') != 'completed'
            or payload.get('thread_id') != (c['agent_id'] or c['session_id'])
            or not isinstance(payload.get('turn_id'), str) or not payload['turn_id']
            or type(item.get('exit_code')) is not int
            or not isinstance(item.get('aggregated_output'), str)):
        raise ValueError('incomplete native CommandExecution metadata')
    argv = item.get('command')
    if (not isinstance(argv, list) or len(argv) != 3
            or argv[:2] not in [['/bin/zsh', '-lc'], ['/bin/zsh', '-c'],
                              ['/bin/bash', '-lc'], ['/bin/bash', '-c']]
            or argv[2] != command):
        raise ValueError('native command differs from hook input')
    times = [payload.get('started_at_ms'), payload.get('completed_at_ms')]
    if any(type(value) is not int for value in times):
        raise ValueError('native execution timestamps missing')
    start, end = [datetime.fromtimestamp(value / 1000, timezone.utc) for value in times]
    if not (aware(event['observed_at']) <= start <= end <= aware(execution['timestamp'])):
        raise ValueError('invalid native execution time order')

    # Deliberately a small, fully anchored grammar, not a JavaScript evaluator.
    # It accepts the observed literal cmd-only call and the harmless result print.
    program = re.compile(
        r'\s*const\s+(?P<var>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*await\s+'
        r'tools\.exec_command\(\s*\{\s*(?:cmd|"cmd")\s*:\s*'
        r'(?P<cmd>"(?:[^"\\\x00-\x1f]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*")'
        r'\s*\}\s*\)\s*;\s*text\(\s*JSON\.stringify\(\s*(?P=var)\s*\)\s*\)\s*;?\s*')
    wrappers = []
    for call_index, row in enumerate(rows):
        call = row.get('payload')
        if not isinstance(call, dict) or row.get('type') != 'response_item':
            continue
        if (call.get('type') != 'custom_tool_call' or call.get('name') != 'exec'
                or call.get('namespace') not in (None, 'functions')
                or not isinstance(call.get('input'), str)):
            continue
        match = program.fullmatch(call['input'])
        if not match or json.loads(match['cmd']) != command:
            continue
        metadata = call.get('internal_chat_message_metadata_passthrough')
        if not isinstance(metadata, dict) or metadata.get('turn_id') != payload['turn_id']:
            continue
        outer_id = call.get('call_id')
        if not isinstance(outer_id, str) or not outer_id:
            continue
        traffic = [(i, r) for i, r in enumerate(rows) if r.get('type') == 'response_item'
                   and isinstance(r.get('payload'), dict) and r['payload'].get('call_id') == outer_id]
        requests = [(i, r) for i, r in traffic if r['payload'].get('type') in {'custom_tool_call', 'function_call'}]
        results = [(i, r) for i, r in traffic if r['payload'].get('type') in {'custom_tool_call_output', 'function_call_output'}]
        if len(requests) != 1 or len(results) != 1:
            raise ValueError('unique native wrapper call/result required')
        result_index, result = results[0]
        if (result['payload']['type'] != 'custom_tool_call_output'
                or (result.get('metadata') or {}).get('client_authored') is True):
            raise ValueError('native wrapper result required')
        if (call_index < index < result_index
                and aware(c['created_at']) <= aware(row['timestamp']) <= aware(event['observed_at'])
                and aware(execution['timestamp']) <= aware(result['timestamp'])):
            wrappers.append((call_index, row, result))
    if len(wrappers) != 1:
        raise ValueError('unique enclosing literal native command call required')
    call_index, call, outer_result = wrappers[0]
    return (
        NormalizedItem(index=call_index, kind='tool_request', timestamp=call['timestamp'],
                       payload={'tool_call_id': event['call_id'], 'tool_name': 'Bash',
                                'input': {'command': command}}),
        NormalizedItem(index=index, kind='tool_result', timestamp=execution['timestamp'],
                       payload={'tool_call_id': event['call_id'],
                                'content': item['aggregated_output'], 'is_error': item['exit_code'] != 0}),
        [call, execution, outer_result],
    )

def evaluate_observation(challenge, event, rows, *, observed_target_sha256=None):
    """Recompute one call/hook/result observation. Missing metadata fails closed."""
    from autoresearch.trace.transcripts.base import tool_call_id

    try:
        if not isinstance(event, dict) or not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('structured hook event and native rows required')
        c = validate_challenge(challenge)
        _case_semantics(c, event)
        for snapshot in c['snapshots'].values():
            if sha256_bytes(snapshot['text'].encode()) != snapshot['sha256']:
                raise ValueError('access snapshot hash mismatch')
        request = json.loads(c['snapshots']['request']['text'])
        for key in ('engine', 'run_id', 'task_id', 'attempt'):
            if request.get(key) != c[key]:
                raise ValueError('dispatch identity mismatch')
        expected = BOUNDARY_CASES[c['case']]
        if (event.get('nonce') != c['nonce'] or event.get('decision') != expected
                or event.get('policy_hash') != c['policy_hash']
                or event.get('input_hash') != _hash(c['tool_input'])
                or event.get('tool_name') != c['tool_name']):
            raise ValueError('hook observation differs from challenge')
        for key in ('engine', 'session_id', 'agent_id'):
            if event.get(key) != c[key]:
                raise ValueError('hook host identity mismatch')
        native = _native_identity(rows, c['engine'], c['session_id'], c['agent_id'])
        if aware(native['started_at']) > aware(c['created_at']):
            raise ValueError('challenge predates native host startup')
        call_id = event.get('call_id')
        if not isinstance(call_id, str) or not call_id:
            raise ValueError('hook did not expose a call identity')
        items = [item for item in _normalized(rows, c['engine']) if tool_call_id(item.payload) == call_id]
        calls = [item for item in items if item.kind == 'tool_request']
        results = [item for item in items if item.kind == 'tool_result']
        nested = any(item.payload.get('native_source') == 'CommandExecution' for item in items)
        if c['engine'] == 'codex' and (nested or (not calls and not results)):
            if nested and (len(calls) != 1 or len(results) != 1):
                raise ValueError('ambiguous native command evidence')
            call, result, _ = _codex_command_observation(rows, c, event)
        else:
            if len(calls) != 1 or len(results) != 1 or calls[0].index >= results[0].index:
                raise ValueError('unique ordered native call/result required')
            call, result = calls[0], results[0]
        args = call.payload.get('input')
        if isinstance(args, str):
            args = json.loads(args)
        if call.payload.get('tool_name') != c['tool_name'] or _hash(args) != _hash(c['tool_input']):
            raise ValueError('native call differs from hook input')
        if not (aware(c['created_at']) <= aware(call.timestamp) <= aware(event['observed_at']) <= aware(result.timestamp)):
            raise ValueError('invalid call/hook/result time order')
        content = result.payload.get('content')
        if isinstance(content, (list, tuple)) and all(isinstance(b, dict) and b.get('type') == 'text' for b in content):
            content = ''.join(b['text'] for b in content)
        rendered = content if isinstance(content, str) else canonical_json(content)
        if expected == 'DENY':
            if 'AGENT_INPUT_BOUNDARY' not in rendered or not event.get('reason', '').startswith('AGENT_INPUT_BOUNDARY'):
                raise ValueError('native result does not confirm this hook denial')
            if observed_target_sha256 != c['before_sha256']:
                raise ValueError('denied operation changed its canary')
        else:
            if result.payload.get('is_error') or 'AGENT_INPUT_BOUNDARY' in rendered:
                raise ValueError('allowed operation did not succeed')
            if c['case'] == 'allowed_write':
                if observed_target_sha256 != c['expected_sha256']:
                    raise ValueError('write canary differs from expected bytes')
            elif c['engine'] == 'claude' and c['tool_name'] == 'Read':
                # Claude shows the model line-numbered text; the complete canary
                # bytes are proven only by the host's own whole-file record.
                from autoresearch.trace.transcripts.base import complete_host_read
                if not complete_host_read(result.payload.get('host_read'), path=c['target'],
                                          sha256=c['expected_sha256']):
                    raise ValueError('read response does not contain complete canary bytes')
            else:
                try:
                    broker_result = json.loads(rendered)
                    if isinstance(broker_result.get('output'), str) and broker_result.get('exit_code') == 0:
                        broker_result = json.loads(broker_result['output'])
                    body = broker_result['content']
                except (ValueError, KeyError, TypeError):
                    body = rendered
                if sha256_bytes(body.encode()) != c['expected_sha256']:
                    raise ValueError('read response does not contain complete canary bytes')
        return {'verified': True, 'status': 'VALIDATED', 'case': c['case'], 'native_identity': native}
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return {'verified': False, 'status': 'INVALID', 'reason': str(exc)}


def verify_proof(proof, *, trust_root, expected_policy):
    try:
        if not isinstance(proof, dict) or set(proof) != {'schema_version', 'issuer_id', 'payload', 'signature'} or type(proof['schema_version']) is not int or proof['schema_version'] != 1:
            raise ValueError('invalid boundary proof envelope')
        issuer = proof['issuer_id']
        if not isinstance(issuer, str) or len(issuer) != 64 or any(c not in '0123456789abcdef' for c in issuer):
            raise ValueError('invalid issuer identity')
        trust_root = Path(trust_root)
        trusted = _json(_regular(trust_root / f'{issuer}.json', trust_root))
        raw_key = trusted['public_key'].encode('ascii')
        if sha256_bytes(raw_key) != issuer or trusted['issuer_id'] != issuer:
            raise ValueError('trusted issuer was changed')
        payload = proof['payload']
        if not isinstance(payload, dict) or set(payload) != {'challenge', 'event', 'native_rows', 'source_prefix_sha256', 'observed_target_sha256', 'issued_at'}:
            raise ValueError('invalid proof payload')
        challenge = validate_challenge(payload['challenge'])
        digest(payload['source_prefix_sha256'])
        if trusted['engine'] != challenge['engine']:
            raise ValueError('issuer engine mismatch')
        with tempfile.TemporaryDirectory(prefix='boundary-signature-') as folder:
            key, signature = Path(folder) / 'issuer.pem', Path(folder) / 'signature'
            key.write_bytes(raw_key)
            signature.write_bytes(base64.b64decode(proof['signature'], validate=True))
            _openssl(['dgst', '-sha256', '-verify', str(key), '-signature', str(signature)],
                     data=canonical_json(payload).encode())
        if challenge['policy_hash'] != expected_policy:
            return {'verified': False, 'status': 'STALE', 'reason': 'boundary policy changed'}
        result = evaluate_observation(challenge, payload['event'], payload['native_rows'],
                                      observed_target_sha256=payload['observed_target_sha256'])
        if aware(payload['issued_at']) < aware(payload['event']['observed_at']):
            raise ValueError('proof issued before observation')
        return {**result, 'engine': challenge['engine'], 'nonce': challenge['nonce'],
                'proof_hash': _hash(proof)}
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        return {'verified': False, 'status': 'INVALID', 'reason': str(exc)}


def _index_json(path):
    def distinct(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate boundary index field')
            value[key] = item
        return value

    return json.loads(Path(path).read_text(), object_pairs_hook=distinct)


def _active_selection(root):
    path = root / 'active.json'
    if not path.exists() and not path.is_symlink():
        return None
    return validate_active_selection(_index_json(_regular(path, root)))


def _load_selection(root, selection_hash, engine):
    """Validate the immutable index chain, without making old policies current."""
    current, seen, selected = digest(selection_hash), set(), None
    while current is not None:
        if current in seen:
            raise ValueError('cyclic boundary selection history')
        seen.add(current)
        path = root / 'selections' / f'{current}.json'
        value = validate_selection(_index_json(_regular(path, root)))
        actual = _hash({key: item for key, item in value.items() if key != 'selection_hash'})
        if value['selection_hash'] != current or actual != current or value['engine'] != engine:
            raise ValueError('boundary selection identity mismatch')
        if selected is None:
            selected = value
        current = value['previous_selection_hash']
    return selected


def _selected_proof(root, engine, case, proof_hash, policy):
    path = root / 'proofs' / f'{digest(proof_hash)}.json'
    proof = _json(_regular(path, root))
    if _hash(proof) != proof_hash:
        raise ValueError('boundary proof filename/content hash mismatch')
    result = verify_proof(proof, trust_root=root / 'trust', expected_policy=policy)
    if not result['verified']:
        raise ValueError(f'boundary proof {result["status"]}: {result.get("reason", "")}')
    native = result['native_identity']
    if result['engine'] != engine or native['engine'] != engine or result['case'] != case:
        raise ValueError('boundary proof case or engine mismatch')
    return result


def select_boundary_set(engine, proof_hashes, *, evidence_root=None):
    """Select imported signed proofs; this index supplies no additional trust."""
    if engine not in {'codex', 'claude'}:
        raise ValueError('unsupported selection engine')
    proof_hashes = dict(validate_proof_hashes(proof_hashes))
    root, policy = _root(evidence_root), policy_fingerprint(engine)
    epoch = None
    for case, proof_hash in proof_hashes.items():
        result = _selected_proof(root, engine, case, proof_hash, policy)
        native = result['native_identity']
        observed = (native['session_id'], native['host_version'])
        if epoch is not None and epoch != observed:
            raise ValueError('boundary selection host session/version mismatch')
        epoch = observed
    directory = _no_redirect(root / 'selections')
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = _no_redirect(root / '.selection.lock')
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        active = _active_selection(root) or {'schema_version': 1, 'selections': {}}
        # Reject corrupt pointers instead of silently replacing an audit chain.
        for host, selection_hash in active['selections'].items():
            _load_selection(root, selection_hash, host)
        value = {'schema_version': 1, 'engine': engine, 'session_id': epoch[0],
                 'host_version': epoch[1], 'policy_hash': policy, 'proof_hashes': proof_hashes,
                 'previous_selection_hash': active['selections'].get(engine)}
        value['selection_hash'] = _hash(value)
        validate_selection(value)
        target = directory / f'{value["selection_hash"]}.json'
        if target.exists() or target.is_symlink():
            if _index_json(_regular(target, root)) != value:
                raise ValueError('immutable boundary selection conflict')
        else:
            # Active cannot reference a partially written object: publish it last.
            with target.open('x') as stream:
                stream.write(canonical_json(value) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        active['selections'][engine] = value['selection_hash']
        atomic_write_json(root / 'active.json', active)
        return value
    finally:
        os.close(descriptor)


def _selection_evidence(root, active, required_hosts, missing, accepted, invalid, diagnostics):
    selected_hashes = set()
    for engine, selection_hash in active['selections'].items():
        errors = []
        try:
            value = _load_selection(root, selection_hash, engine)
            selected_hashes.update(value['proof_hashes'].values())
            policy = policy_fingerprint(engine)
            if value['policy_hash'] != policy:
                raise ValueError('boundary selection policy changed')
            for case, proof_hash in value['proof_hashes'].items():
                try:
                    result = _selected_proof(root, engine, case, proof_hash, policy)
                    native = result['native_identity']
                    if (native['session_id'], native['host_version']) != (value['session_id'], value['host_version']):
                        raise ValueError('boundary selection host session/version mismatch')
                    if engine in required_hosts:
                        label = f'{engine}:{case}'
                        missing.remove(label)
                        accepted.append({'label': label, 'proof_hash': proof_hash, 'native_identity': native})
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    errors.append(f'{engine}:{case}:{exc}')
        except (ValueError, TypeError, KeyError, OSError) as exc:
            errors.append(f'{engine}:{exc}')
        if engine in required_hosts:
            invalid.extend(errors)
        else:
            diagnostics.extend({'file': f'selections/{selection_hash}.json', 'engine': engine,
                                'status': 'INVALID', 'reason': reason} for reason in errors)
    return selected_hashes


def _history_diagnostics(root, selected_hashes):
    diagnostics = []
    try:
        paths = sorted(_no_redirect(root / 'proofs').glob('*.json'))
    except (ValueError, OSError) as exc:
        return [{'file': 'proofs', 'status': 'INVALID', 'reason': str(exc)}]
    for path in paths:
        if path.stem in selected_hashes:
            continue
        try:
            proof = _json(_regular(path, root))
            host = _proof_host(proof)
            result = verify_proof(proof, trust_root=root / 'trust', expected_policy=policy_fingerprint(host))
            diagnostics.append({'file': path.name, 'status': result['status'],
                                'reason': result.get('reason', '')})
        except (ValueError, TypeError, KeyError, OSError) as exc:
            diagnostics.append({'file': path.name, 'status': 'INVALID', 'reason': str(exc)})
    return diagnostics


def boundary_gate(*, evidence_root=None, required_hosts=('codex', 'claude')):
    if not required_hosts or len(set(required_hosts)) != len(required_hosts) or not set(required_hosts) <= {'codex', 'claude'}:
        raise ValueError('nonempty distinct supported hosts required')
    missing = [f'{host}:{case}' for host in required_hosts for case in BOUNDARY_CASES]
    accepted, invalid, epochs, history = [], [], {}, []
    try:
        root = _root(evidence_root)
        active = _active_selection(root)
        if active is not None:
            selected = _selection_evidence(root, active, required_hosts, missing, accepted, invalid, history)
            history.extend(_history_diagnostics(root, selected))
        paths = sorted(_no_redirect(root / 'proofs').glob('*.json')) if active is None else ()
        for path in paths:
            proof = _json(_regular(path, root))
            host = _proof_host(proof)
            if host not in {'codex', 'claude'}:
                raise ValueError('unexpected proof host')
            if host not in required_hosts:
                continue
            result = verify_proof(proof, trust_root=root / 'trust', expected_policy=policy_fingerprint(host))
            if not result['verified']:
                invalid.append(f'{path.name}:{result["status"]}:{result.get("reason", "")}')
                continue
            label = f'{host}:{result["case"]}'
            native = result['native_identity']
            epoch = (native['session_id'], native['host_version'])
            # All cases for a host must come from one actual host session/version.
            if host in epochs and epochs[host] != epoch:
                invalid.append(f'{host}:HOST_SESSION_SET_MISMATCH')
            epochs[host] = epoch
            if label in missing:
                missing.remove(label)
            else:
                invalid.append(f'{label}:DUPLICATE_PROOF')
            accepted.append({'label': label, 'proof_hash': result['proof_hash'], 'native_identity': native})
    except (ValueError, TypeError, KeyError, OSError) as exc:
        invalid.append(str(exc))
    return {'schema_version': 1, 'status': 'VALIDATED' if not missing and not invalid else ('INVALID' if invalid else 'PENDING_REAL_HOST_VALIDATION'),
            'acceptance_satisfied': not missing and not invalid, 'required_hosts': list(required_hosts),
            'loaded_host_evidence': accepted, 'missing': sorted(missing), 'invalid': invalid,
            'history_diagnostics': history,
            'scope': 'OBSERVED_HOST_HOOK_POLICY_NOT_OS_ISOLATION'}


def _proof_host(proof):
    payload = proof.get('payload') if isinstance(proof, dict) else None
    challenge = payload.get('challenge') if isinstance(payload, dict) else None
    if not isinstance(challenge, dict) or challenge.get('engine') not in {'codex', 'claude'}:
        raise ValueError('structured boundary proof identity required')
    return challenge['engine']


@contextmanager
def _active_lock(session_id, agent_id):
    """One owner lock for public challenge publication and compare-and-clear."""
    if not isinstance(session_id, str) or not session_id or not isinstance(agent_id, str):
        raise ValueError('host context identity required')
    root = _no_redirect(ws.context_root() / '_acceptance/boundary')
    index = _no_redirect(root / 'active' / f'{_hash([session_id, agent_id])}.json')
    lock_path = _no_redirect(index.with_suffix('.lock'))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('active lock must be a private regular file')
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        _no_redirect(index)
        if index.exists() and (not index.is_file() or index.stat().st_nlink != 1):
            raise ValueError('active pointer must be a private regular file')
        yield index


def clear_challenge(*, session_id, agent_id, nonce):
    """Remove only this nonce, serialized with all public owner publications."""
    if not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce):
        raise ValueError('valid challenge nonce required')
    with _active_lock(session_id, agent_id) as index:
        if index.exists() and _json(index).get('nonce') == nonce:
            index.unlink()
            return True
        return False


def create_challenge(request_path, *, case, session_id, agent_id, tool_name, tool_input,
                     target, expected_sha256=None, nonce=None, require_no_active=False):
    """Freeze before a real host call. Only dedicated non-sensitive canaries qualify."""
    from autoresearch.session_agent import task_access

    request_path = _no_redirect(request_path).resolve(strict=True)
    allowed = (ws.context_root().resolve(), ws.reports_root().resolve())
    if not any(request_path.is_relative_to(root) for root in allowed):
        raise ValueError('challenge dispatch must belong to this engine')
    request = _json(request_path)
    if request.get('engine') != ws.ENGINE:
        raise ValueError('cannot issue a challenge for another engine')
    manifest = task_access._load_manifest(request_path)
    context_path = request_path.with_suffix('.context.json')
    context = _json(context_path)
    if context != {'session_id': session_id, 'agent_id': agent_id, 'engine': ws.ENGINE}:
        raise ValueError('challenge requires actual bound host context')
    target = _no_redirect(target)
    # Input and private-output canaries must be intentionally named, never ordinary research data.
    if 'boundary-canary' not in target.name or target.is_symlink() or not any(target.resolve().is_relative_to(root) for root in allowed):
        raise ValueError('dedicated engine-owned boundary-canary target required')
    nonce = nonce or secrets.token_hex(16)
    def snapshot(path):
        text = Path(path).read_text()
        return {'sha256': sha256_bytes(text.encode()), 'text': text}
    value = validate_challenge({
        'schema_version': 1, 'nonce': nonce, 'case': case, 'engine': ws.ENGINE,
        'run_id': request['run_id'], 'task_id': request['task_id'], 'attempt': request['attempt'],
        'session_id': session_id, 'agent_id': agent_id, 'created_at': _now(),
        'policy_hash': policy_fingerprint(ws.ENGINE), 'request_path': str(request_path),
        'tool_name': tool_name, 'tool_input': tool_input, 'target': str(target.resolve()),
        'before_sha256': sha256_file(target) if target.is_file() else None,
        'expected_sha256': expected_sha256,
        'snapshots': {'request': snapshot(request_path),
                      'manifest': snapshot(task_access.manifest_path(request_path)),
                      'context_snapshot': snapshot(context_path)},
    })
    # Capture the initial manifest before intentionally invalidating an isolated probe.
    if manifest['identity']['run_id'] != value['run_id']:
        raise ValueError('challenge run mismatch')
    root = _no_redirect(ws.context_root() / '_acceptance/boundary')
    path = root / 'challenges' / f'{nonce}.json'
    with _active_lock(session_id, agent_id) as index:
        if require_no_active and index.exists():
            raise ValueError('existing active challenge cannot be overwritten')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x') as stream:
            stream.write(canonical_json(value))
        atomic_write_json(index, {'nonce': nonce})
    return {'challenge_path': str(path), 'nonce': nonce, 'tool_name': tool_name, 'tool_input': tool_input}


def _native_source(c):
    """Locate using the host index; the caller cannot nominate an arbitrary JSONL."""
    from autoresearch.trace.transcripts import RunIdentity

    if c['engine'] != ws.ENGINE:
        raise ValueError('native capture is restricted to the current engine')
    session = c['agent_id'] or c['session_id']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session):
        raise ValueError('unsupported native session identity')
    if c['engine'] == 'codex':
        from autoresearch.trace.transcripts.codex import discover_rollout_candidates
        result = discover_rollout_candidates(RunIdentity(c['run_id'], 'codex', session_ref=session))
        candidates = list(result.candidates)
        root = Path.home() / '.codex/sessions'
    else:
        from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter
        adapter = ClaudeTranscriptAdapter()
        refs = adapter.locate(RunIdentity(c['run_id'], 'claude', session_ref=c['session_id']))
        candidates = [r.path for r in refs if (r.path.stem == f'agent-{c["agent_id"]}' if c['agent_id'] else r.role == 'main')]
        root = adapter.projects_root
    if len(candidates) != 1:
        raise ValueError('native host source is missing or ambiguous')
    return _regular(candidates[0], root)


def native_root_context(engine, host_profile):
    """Resolve a trusted root profile through the official native source index.

    Profile evidence_refs and ad-hoc version/path fields are never source selectors.
    No metadata means no current observation; this does not change dispatch support.
    """
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    result = {'session_ref': None, 'host_version': None, 'policy_hash': None, 'diagnostics': []}
    try:
        if engine not in {'codex', 'claude'} or engine != ws.ENGINE or host_profile.get('engine') != engine:
            raise ValueError('current root profile engine mismatch')
        session = host_profile.get('session_ref')
        if not isinstance(session, str) or not session:
            raise ValueError('trusted current root session context missing')
        source = _native_source({'engine': engine, 'session_id': session,
                                 'agent_id': None, 'run_id': 'current-host-observation'})
        snapshot = capture_snapshot(source, engine=engine)
        if snapshot.source_changed or snapshot.bad_lines:
            raise ValueError('native transcript prefix is unstable or malformed')
        rows = [dict(row) for row in snapshot.rows]
        if engine == 'codex':
            metas = [row.get('payload') for row in rows if row.get('type') == 'session_meta']
            if len(metas) != 1 or not isinstance(metas[0], dict):
                raise ValueError('native root metadata malformed or ambiguous')
            meta = metas[0]
            if isinstance(meta.get('source'), dict) and 'subagent' in meta['source']:
                raise ValueError('native context is not a root session')
        elif any(row.get('agentId') or row.get('isSidechain') for row in rows):
            raise ValueError('native context is not a root session')
        identity = _native_identity(rows, engine, session, None)
        result.update(session_ref=session, host_version=identity['host_version'],
                      policy_hash=policy_fingerprint(engine))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result['diagnostics'].append(str(exc))
    return result


def issue_proof(challenge_path, *, evidence_root=None):
    """Capture locally observed native evidence and sign only a validated result."""
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    root = _no_redirect(ws.context_root() / '_acceptance/boundary')
    c = validate_challenge(_json(_regular(challenge_path, root / 'challenges')))
    if c['engine'] != ws.ENGINE or c['policy_hash'] != policy_fingerprint(ws.ENGINE):
        raise ValueError('challenge engine or policy is stale')
    event_path = _regular(root / 'events' / f'{c["nonce"]}.jsonl', root)
    events = [json.loads(line) for line in event_path.read_text().splitlines() if line.strip()]
    matching = [e for e in events if e.get('tool_name') == c['tool_name'] and e.get('input_hash') == _hash(c['tool_input'])]
    if len(matching) != 1:
        raise ValueError('challenge needs exactly one actual hook observation')
    event = matching[0]
    snapshot = capture_snapshot(_native_source(c), engine=ws.ENGINE)
    if snapshot.source_changed or snapshot.bad_lines:
        raise ValueError('native transcript prefix is unstable or malformed')
    rows = [dict(row) for row in snapshot.rows]
    target = _no_redirect(c['target'])
    if target.is_symlink():
        raise ValueError('canary redirected')
    observed = sha256_file(target) if target.is_file() else None
    result = evaluate_observation(c, event, rows, observed_target_sha256=observed)
    if not result['verified']:
        raise ValueError('boundary not observed: ' + result['reason'])
    # Only metadata and the observed call/result interval travel; no unrelated conversation.
    call_id = event['call_id']
    selected = []
    nested_rows = []
    if c['engine'] == 'codex' and not any(
            row.get('type') == 'response_item'
            and (row.get('payload') or {}).get('call_id') == call_id for row in rows):
        _, _, nested_rows = _codex_command_observation(rows, c, event)
    for row in rows:
        metadata = row.get('type') == 'session_meta'
        payload_row = row.get('payload') or {}
        blocks = (row.get('message') or {}).get('content') or []
        matching_call = payload_row.get('call_id') == call_id or (
            isinstance(blocks, list) and any(isinstance(b, dict) and
            (b.get('id') == call_id or b.get('tool_use_id') == call_id) for b in blocks))
        if metadata or matching_call or row in nested_rows:
            selected.append(row)
    # Claude identity needs an original row carrying native session/version metadata.
    if c['engine'] == 'claude' and rows and rows[0] not in selected:
        selected.insert(0, {key: rows[0][key] for key in
            ('sessionId', 'agentId', 'version', 'timestamp', 'type') if key in rows[0]})
    payload = {'challenge': c, 'event': event, 'native_rows': selected,
               'source_prefix_sha256': snapshot.source_prefix.sha256,
               'observed_target_sha256': observed, 'issued_at': _now()}
    if not evaluate_observation(c, event, selected, observed_target_sha256=observed)['verified']:
        raise ValueError('portable native segment lacks required identity or calls')
    private = _regular(root / 'issuer.pem', root)
    if private.stat().st_mode & 0o077:
        raise ValueError('issuer private key permissions must be 0600')
    public = _openssl(['pkey', '-in', str(private), '-pubout'])
    signature = _openssl(['dgst', '-sha256', '-sign', str(private)], data=canonical_json(payload).encode())
    proof = {'schema_version': 1, 'issuer_id': sha256_bytes(public), 'payload': payload,
             'signature': base64.b64encode(signature).decode('ascii')}
    path = _root(evidence_root) / 'exports' / f'{_hash(proof)}.json'
    atomic_write_json(path, proof)
    return {'proof_path': str(path), 'proof_hash': _hash(proof), 'status': 'EXPORTED_NOT_TRUSTED_OR_IMPORTED'}


def import_proof(path, *, evidence_root=None):
    root = _root(evidence_root)
    # Transfers are staged within this engine; never read another engine's raw tree.
    source = _regular(path, root / 'imports')
    proof = _json(source)
    engine = _proof_host(proof)
    result = verify_proof(proof, trust_root=root / 'trust', expected_policy=policy_fingerprint(engine))
    if not result['verified']:
        raise ValueError('boundary proof rejected: ' + result.get('reason', result['status']))
    target = root / 'proofs' / f'{result["proof_hash"]}.json'
    if target.exists() and _json(_regular(target, root)) != proof:
        raise ValueError('boundary proof identity collision')
    if not target.exists():
        atomic_write_json(target, proof)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    sub.add_parser('init-issuer')
    trust = sub.add_parser('trust-issuer')
    trust.add_argument('--public-key', required=True)
    trust.add_argument('--engine', choices=('codex', 'claude'), required=True)
    trust.add_argument('--expected-fingerprint', required=True)
    challenge = sub.add_parser('challenge')
    challenge.add_argument('--spec', required=True, help='Root-authored challenge arguments JSON')
    issue = sub.add_parser('issue')
    issue.add_argument('--challenge', required=True)
    imp = sub.add_parser('import')
    imp.add_argument('--proof', required=True)
    selection = sub.add_parser('select')
    selection.add_argument('--spec', required=True, help='Engine and case-to-proof hashes JSON')
    args = parser.parse_args(argv)
    try:
        if args.command == 'status':
            value = boundary_gate()
        elif args.command == 'init-issuer':
            value = initialize_issuer()
        elif args.command == 'trust-issuer':
            value = trust_issuer(args.public_key, engine=args.engine, expected_fingerprint=args.expected_fingerprint)
        elif args.command == 'challenge':
            value = create_challenge(**_json(_no_redirect(args.spec)))
        elif args.command == 'issue':
            value = issue_proof(args.challenge)
        elif args.command == 'select':
            spec = _index_json(_no_redirect(args.spec))
            if not isinstance(spec, dict) or set(spec) != {'engine', 'proof_hashes'}:
                raise ValueError('selection spec requires only engine and proof_hashes')
            value = select_boundary_set(**spec)
        else:
            value = import_proof(args.proof)
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'REJECTED', 'reason': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

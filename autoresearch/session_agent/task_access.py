"""Orchestrator-owned C4 file manifests. These are hook policy, not OS isolation.

Host hook payload session/agent IDs select a fixed registry entry. Model tool input,
command environment, and prompts never select a manifest. Authentic loading and
identity propagation still need real host observation; no function issues ENFORCED.
"""
from __future__ import annotations

import json
import os
import runpy
import shlex
import sys
from pathlib import Path

# The broker loads this module directly under -I -S. Load only this one frozen
# stdlib helper; package __init__ files and optional dotenv are not executed.
_ATOMIC = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'common/atomic.py'))
atomic_write_json = _ATOMIC['atomic_write_json']
atomic_write_bytes = _ATOMIC['atomic_write_bytes']
canonical_json = _ATOMIC['canonical_json']
sha256_bytes = _ATOMIC['sha256_bytes']
sha256_file = _ATOMIC['sha256_file']

REPO_ROOT = Path(__file__).resolve().parents[2]


def current_host_observation(engine: str, *, session_ref, host_version, policy_hash,
                             evidence_root=None) -> dict:
    """Root-owned read-only projection; inputs are trusted root context, not grants.

    Only boundary_gate supplies verified evidence (including its active selection
    rules). A caller cannot make historical evidence current by selecting a file,
    event, environment identity, or an older policy. The broker exposes no such API.
    """
    from autoresearch.contracts.research_boundary import BOUNDARY_CASES
    from autoresearch.session_agent.boundary_proof import boundary_gate, policy_fingerprint

    evidence, diagnostics, accepted = [], [], False
    context = {'session_ref': session_ref, 'host_version': host_version, 'policy_hash': policy_hash}
    if engine not in {'codex', 'claude'}:
        diagnostics.append('unsupported host engine')
    elif any(not isinstance(value, str) or not value.strip() for value in context.values()):
        diagnostics.append('trusted current root session/version/policy context missing')
    else:
        try:
            if policy_hash != policy_fingerprint(engine):
                diagnostics.append('current boundary policy mismatch')
            else:
                gate = boundary_gate(evidence_root=evidence_root, required_hosts=(engine,))
                diagnostics.extend(gate['invalid'])
                for row in gate['loaded_host_evidence']:
                    native = row['native_identity']
                    if (native['engine'], native['session_id'], native['host_version']) == (
                            engine, session_ref, host_version):
                        evidence.append(row)
                    else:
                        diagnostics.append(f"{row['label']}: current root session/version mismatch")
                accepted = gate['acceptance_satisfied'] and not diagnostics
        except (OSError, ValueError, KeyError, TypeError) as exc:
            diagnostics.append(str(exc))
    cases = sorted(row['label'].split(':', 1)[1] for row in evidence)
    missing = [f'{engine}:{case}' for case in BOUNDARY_CASES if case not in cases]
    return {'engine': engine, **context,
            'entrypoint_observed': not diagnostics and {'allowed_read', 'allowed_write'} <= set(cases),
            'acceptance_satisfied': accepted, 'verified_cases': cases,
            'loaded_host_evidence': evidence, 'missing': sorted(missing), 'diagnostics': diagnostics,
            'scope': 'CURRENT_ROOT_HOST_OBSERVATION_NOT_AUTHORIZATION'}


def capability(engine: str, *, root_context: dict | None = None) -> dict:
    configured = False
    available = False
    try:
        config = REPO_ROOT / ('.codex/hooks.json' if engine == 'codex' else '.claude/settings.json')
        doc = _json(config)
        configured = engine in {'codex', 'claude'} and any(
            row.get('matcher') == '*' and any(
                'agent_input_boundary.sh' in hook.get('command', '')
                and hook.get('command', '').split(' || ', 1)[0].rstrip().endswith(' ' + engine)
                for hook in row.get('hooks', [])) for row in doc['hooks']['PreToolUse'])
        identity = broker_identity(engine=engine)
        available = os.access(identity['python'], os.X_OK) and all(
            (REPO_ROOT / path).is_file() for path in
            ('scripts/hooks/agent_input_boundary.sh', 'scripts/hooks/agent_input_boundary.py'))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    runnable = configured and available
    # Imported lazily: the isolated broker itself never loads the application package.
    from autoresearch.session_agent.boundary_proof import boundary_gate
    observed = boundary_gate(required_hosts=(engine,)) if runnable else None
    verified = bool(observed and observed['acceptance_satisfied'])
    context = root_context or {}
    current = current_host_observation(engine, session_ref=context.get('session_ref'),
        host_version=context.get('host_version'), policy_hash=context.get('policy_hash'))
    if context.get('diagnostics'):
        current['diagnostics'].extend(context['diagnostics'])
        current['entrypoint_observed'] = current['acceptance_satisfied'] = False
    return {'status': (('BROKER_CONFIGURED' if engine == 'codex' else 'UNVERIFIED') if runnable else 'UNSUPPORTED'),
            'runnable': runnable, 'hook_configured': configured,
            'loaded_host_evidence': observed['loaded_host_evidence'] if observed else [], 'acceptance_satisfied': verified,
            'invocation_cwd_requirement': 'REPOSITORY_ROOT' if engine == 'codex' else 'CLAUDE_PROJECT_DIR',
            'entrypoint_observed': current['entrypoint_observed'],
            'current_host_observation': current,
            'historical_boundary_acceptance': verified,
            'reason': ('configured task file adapter; loaded-host enforcement unverified' if runnable
                       else 'task boundary hook or broker configuration unavailable')}


def boundary_policy_fingerprint(engine: str, *, repo_root: Path = REPO_ROOT) -> str:
    contract = runpy.run_path(str(repo_root / 'autoresearch/contracts/research_boundary.py'))
    paths = [*contract['POLICY_FILES'], '.codex/hooks.json' if engine == 'codex' else '.claude/settings.json']
    roles = repo_root / ('.codex/agents' if engine == 'codex' else '.claude/agents')
    paths.extend(str(p.relative_to(repo_root)) for p in sorted(roles.glob('*')) if p.is_file())
    return sha256_bytes(canonical_json({p: sha256_file(repo_root / p) for p in paths}).encode())


def record_boundary_event(payload: dict, engine: str, decision: dict | None,
                          *, repo_root: Path = REPO_ROOT) -> None:
    """Only explicit canary challenges get hook-side evidence; ordinary runs do not.

    This is a hook observation, not proof of host enforcement. Export additionally
    requires a matching native call and result from the actual host's source index.
    """
    import fcntl
    from datetime import datetime, timezone

    if engine not in {'codex', 'claude'} or not isinstance(payload, dict):
        return
    session, agent = payload.get('session_id'), payload.get('agent_id', '')
    if not isinstance(session, str) or not isinstance(agent, str):
        return
    root = repo_root / f'context_{engine}/_acceptance/boundary'
    key = sha256_bytes(canonical_json([session, agent]).encode())
    index = root / 'active' / f'{key}.json'
    if not index.exists():
        return
    if index.is_symlink() or root.is_symlink():
        raise ValueError('boundary challenge redirect')
    pointer = _json(index)
    nonce = pointer.get('nonce', '')
    if not isinstance(nonce, str) or len(nonce) != 32 or any(c not in '0123456789abcdef' for c in nonce):
        raise ValueError('invalid boundary challenge pointer')
    challenge = _json(root / 'challenges' / f'{nonce}.json')
    if challenge['session_id'] != session or challenge['agent_id'] != agent or challenge['engine'] != engine:
        raise ValueError('boundary challenge identity mismatch')
    request = Path(challenge['request_path'])
    manifest = json.loads(challenge['snapshots']['manifest']['text'])
    def snapshot(path):
        try:
            if path.is_symlink() or path.stat().st_size > 1_000_000:
                return None
            text = path.read_text()
            return {'sha256': sha256_bytes(text.encode()), 'text': text}
        except OSError:
            return None
    context_path = _binding_path(engine, session, agent, repo_root)
    if not context_path.exists() and not agent:
        context_path = _binding_path(engine, session, '', repo_root)
    output = (decision or {}).get('hookSpecificOutput') or {}
    event = {
        'schema_version': 1, 'nonce': nonce, 'engine': engine, 'session_id': session,
        'agent_id': agent, 'call_id': payload.get('tool_use_id') or payload.get('tool_call_id') or payload.get('call_id'),
        'tool_name': payload.get('tool_name'),
        'input_hash': sha256_bytes(canonical_json(payload.get('tool_input')).encode()),
        'decision': 'DENY' if output.get('permissionDecision') == 'deny' else 'ALLOW',
        'reason': output.get('permissionDecisionReason', ''),
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'policy_hash': boundary_policy_fingerprint(engine, repo_root=repo_root),
        'binding': snapshot(context_path), 'manifest': snapshot(manifest_path(request)),
        'context_snapshot': snapshot(request.with_suffix('.context.json')),
        'deep_grant': snapshot(request.with_suffix('.deep-access.json')),
        'state': snapshot(Path(manifest['state_path'])),
    }
    events = root / 'events' / f'{nonce}.jsonl'
    events.parent.mkdir(parents=True, exist_ok=True)
    if events.is_symlink():
        raise ValueError('boundary event redirect')
    with events.open('a') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(canonical_json(event) + '\n')
        stream.flush()


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('expected object')
    return value


def _freeze(path: Path, value: dict) -> Path:
    # Only the deterministic orchestrator writes here; guarded file tools cannot.
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + '.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.exists():
            if _json(path) != value:
                raise ValueError(f'access freeze conflict: {path.name}')
        else:
            atomic_write_json(path, value)
    return path


def manifest_path(request_path: Path) -> Path:
    return request_path.with_suffix('.access.json')


def _identity(request) -> dict:
    return {'run_id': request.run_id, 'engine': request.engine, 'task_id': request.task_id,
            'attempt': request.attempt, 'role': request.role, 'agent_type': request.agent_type,
            'session_id': request.host_session_ref}


def _read_file(path: str) -> dict:
    target = Path(path).resolve(strict=True)
    if not target.is_file():
        raise ValueError('access reads must be exact files')
    return {'path': str(target), 'sha256': sha256_file(target)}


def freeze_access(request, request_path: Path, *, abandonment_path: Path | None = None) -> dict:
    """Freeze a new request, never upgrade an old frozen request implicitly."""
    request_path = request_path.resolve()
    _freeze(request_path, request.to_json())
    reads = [{**_read_file(str(Path(path) if Path(path).is_absolute() else REPO_ROOT / path)), 'artifact_id': f'instruction:{index}'}
             for index, path in enumerate(request.instruction_refs)]
    conditional = []
    for key, path in request.input_paths.items():
        # Blind WEB_WRITE roles receive neutral inline instructions, not file access.
        if 'READ' not in request.tool_policy.split('_'):
            continue
        row = {**_read_file(path), 'artifact_id': key}
        (conditional if key.endswith('.deep') else reads).append(row)
    writes = [str(Path(path).resolve()) for path in request.output_paths.values()]
    expected_tail = ('session_outputs', 'attempts', request.task_id, f'a{request.attempt:04d}', 'outputs')
    if not writes or any(Path(path).parent.parts[-5:] != expected_tail for path in writes):
        raise ValueError('C4 requires C3 private attempt output paths')
    value = {'schema_version': 1, 'identity': _identity(request),
             'request_path': str(request_path), 'request_sha256': sha256_file(request_path),
             'state_path': str(request_path.parent.parent / 'tasks.json'),
             'reads': reads, 'conditional_reads': conditional, 'writes': writes,
             'tool_policy': request.tool_policy, 'enforcement': 'UNVERIFIED',
             'broker': broker_identity(engine=request.engine),
             'abandonment_path': str(abandonment_path.resolve()) if abandonment_path else None}
    _freeze(manifest_path(request_path), value)
    return value


def _load_manifest(request_path: Path) -> dict:
    value = _json(manifest_path(request_path))
    if value['schema_version'] != 1 or value['request_path'] != str(request_path):
        raise ValueError('invalid manifest identity')
    if sha256_file(request_path) != value['request_sha256']:
        raise ValueError('frozen dispatch changed')
    request = _json(request_path)
    identity = {key: request[key] for key in ('run_id', 'engine', 'task_id', 'attempt', 'role', 'agent_type')}
    identity['session_id'] = request['host_session_ref']
    if identity != value['identity']:
        raise ValueError('dispatch identity mismatch')
    return value


def _assert_live(value: dict) -> None:
    identity = value['identity']
    if value.get('abandonment_path') and Path(value['abandonment_path']).exists():
        raise ValueError('attempt abandoned')
    state = _json(Path(value['state_path']))
    entry = state['tasks'][identity['task_id']]
    spec = entry['spec']
    if (spec.get('owner') != 'SESSION' or spec.get('kind') != 'INFERENCE'
            or spec.get('task_id') != identity['task_id'] or spec.get('role') != identity['role']):
        raise ValueError('access owner is not the authoritative SESSION inference task')
    if (state['engine'] != identity['engine'] or state['run_id'] != identity['run_id']
            or entry['state'] != 'RUNNING' or entry['attempt'] != identity['attempt']
            or entry['session_ref'] != identity['session_id']
            or type(entry['attempt']) is not int):
        raise ValueError('access binding is stale or terminal')


def _binding_path(engine: str, session_id: str, agent_id: str, repo_root: Path) -> Path:
    if engine not in {'claude', 'codex'} or not session_id or not isinstance(agent_id, str):
        raise ValueError('host identity missing')
    digest = sha256_bytes(canonical_json([session_id, agent_id]).encode())
    return repo_root / f'context_{engine}' / 'session_access' / f'{digest}.json'


def bind_context(request_path: Path, *, session_id: str, agent_id: str,
                 repo_root: Path = REPO_ROOT, headless: bool = False) -> Path:
    """Root binds the actual host identity after spawn (or preselected headless UUID)."""
    request_path = Path(request_path).resolve(strict=True)
    value = _load_manifest(request_path)
    _assert_live(value)
    if not headless and session_id != value['identity']['session_id']:
        raise ValueError('host session differs from frozen dispatch')
    if headless and (value['identity']['engine'] != 'claude' or agent_id):
        raise ValueError('headless binding requires Claude session scope')
    if not headless and not agent_id:
        raise ValueError('child agent id required')
    path = _binding_path(value['identity']['engine'], session_id, agent_id, repo_root)
    _freeze(request_path.with_suffix('.context.json'),
            {'session_id': session_id, 'agent_id': agent_id, 'engine': value['identity']['engine']})
    return _freeze(path, {'schema_version': 1, 'identity': value['identity'],
                         'session_id': session_id, 'agent_id': agent_id,
                         'request_path': str(request_path),
                         'manifest_sha256': sha256_file(manifest_path(request_path))})


def has_binding(payload: dict, engine: str, *, repo_root: Path = REPO_ROOT) -> bool:
    session_id, agent_id = payload.get('session_id'), payload.get('agent_id', '')
    if not isinstance(session_id, str) or not isinstance(agent_id, str):
        return False
    return any(_binding_path(engine, session_id, key, repo_root).exists()
               for key in {agent_id, ''})


def load_bound_access(payload: dict, engine: str, *, repo_root: Path = REPO_ROOT) -> dict:
    session_id, agent_id = payload.get('session_id'), payload.get('agent_id', '')
    path = _binding_path(engine, session_id, agent_id, repo_root)
    # A headless session binding guards all its tool calls, including role-less payloads.
    if not path.exists():
        path = _binding_path(engine, session_id, '', repo_root)
    binding = _json(path)
    request_path = Path(binding['request_path'])
    if binding['manifest_sha256'] != sha256_file(manifest_path(request_path)):
        raise ValueError('manifest changed')
    value = _load_manifest(request_path)
    context = _json(request_path.with_suffix('.context.json'))
    if context != {'session_id': binding['session_id'], 'agent_id': binding['agent_id'], 'engine': engine}:
        raise ValueError('attempt context binding mismatch')
    if (binding['identity'] != value['identity'] or value['identity']['engine'] != engine
            or binding['session_id'] != session_id
            or binding['agent_id'] not in {agent_id, ''}):
        raise ValueError('host binding identity mismatch')
    from re import sub
    def normalize(name):
        return sub(r'[\s-]+', '_', name.strip().lower())

    role = payload.get('agent_type')
    if role is not None and normalize(str(role)) != normalize(value['identity']['agent_type']):
        raise ValueError('host role differs from bound role')
    _assert_live(value)
    grant_path = request_path.with_suffix('.deep-access.json')
    deep_authorized = False
    if grant_path.exists():
        grant = _json(grant_path)
        if (grant['manifest_sha256'] != binding['manifest_sha256']
                or grant['stage'] not in {'P4', 'HOLDING', 'TASK_DECLARED'}
                or grant['identity'] != value['identity']):
            raise ValueError('invalid deep authorization')
        deep_authorized = True
    return {'manifest': value, 'deep_authorized': deep_authorized}


def authorize_deep(request_path: Path, *, stage: str, reason: str) -> Path:
    if stage not in {'P4', 'HOLDING', 'TASK_DECLARED'} or not reason.strip():
        raise ValueError('deep stage must be P4, HOLDING or TASK_DECLARED with root rationale')
    request_path = Path(request_path).resolve(strict=True)
    value = _load_manifest(request_path)
    _assert_live(value)
    if not value['conditional_reads']:
        raise ValueError('no declared deep input')
    return _freeze(request_path.with_suffix('.deep-access.json'),
                   {'schema_version': 1, 'identity': value['identity'], 'stage': stage,
                    'reason': reason, 'manifest_sha256': sha256_file(manifest_path(request_path))})


def activate_claim_access(request_path: Path) -> None:
    """Restore only the frozen task's declared deep grant after live ownership."""
    request_path = Path(request_path).resolve(strict=True)
    value = _load_manifest(request_path)
    _assert_live(value)
    inputs = _json(request_path)['input_paths']
    for row in value['conditional_reads']:
        if (row['artifact_id'] not in inputs
                or str(Path(inputs[row['artifact_id']]).resolve()) != row['path']):
            raise ValueError('deep input differs from frozen dispatch')
    if value['conditional_reads']:
        authorize_deep(request_path, stage='TASK_DECLARED',
                       reason='frozen task stage authorization')


def path_allowed(bound: dict, path: str, mode: str, *, cwd: str) -> bool:
    if not isinstance(path, str) or not path or '\x00' in path:
        return False
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = Path(cwd) / target
    lexical = Path(os.path.abspath(target))
    resolved = target.resolve()
    value = bound['manifest']
    # Output read-back is limited to the current attempt's exact registered files.
    if str(resolved) in value['writes'] and lexical == resolved:
        return mode == 'write' or (mode == 'read' and resolved.is_file())
    if mode != 'read':
        return False
    rows = value['reads'] + (value['conditional_reads'] if bound['deep_authorized'] else [])
    return any(row['path'] == str(resolved) and resolved.is_file()
               and row['sha256'] == sha256_file(resolved) for row in rows)


def broker_identity(*, engine: str = 'codex') -> dict:
    paths = ['scripts/hooks/task_file_broker.py', 'autoresearch/session_agent/task_access.py',
             'autoresearch/common/atomic.py']
    return {'engine': engine, 'python': str(Path(sys.executable).resolve()),
            'script': str(REPO_ROOT / paths[0]),
            'interpreter_sha256': sha256_file(Path(sys.executable).resolve()),
            'code': {str(REPO_ROOT / path): sha256_file(REPO_ROOT / path) for path in paths}}


def encode_broker_argument(data: dict) -> str:
    # A literal apostrophe can never close the one shell argument.
    return canonical_json(data).replace("'", "\\u0027")


def broker_command(session_id: str, agent_id: str, operation: str, artifact_id: str,
                   content: str | None = None, *, identity: dict | None = None, engine: str | None = None) -> str:
    identity = identity or broker_identity(engine=engine or 'codex')
    data = {'engine': engine or identity['engine'], 'session_id': session_id, 'agent_id': agent_id, 'operation': operation,
            'artifact_id': artifact_id}
    if content is not None:
        data['content'] = content
    token = encode_broker_argument(data)
    return f"{shlex.quote(identity['python'])} -I -S {shlex.quote(identity['script'])} '{token}'"


def decode_broker_command(command: str, identity: dict) -> dict:
    if not isinstance(command, str):
        raise ValueError('canonical broker command required')
    prefix = f"{shlex.quote(identity['python'])} -I -S {shlex.quote(identity['script'])} '"
    if not command.startswith(prefix) or not command.endswith("'"):
        raise ValueError('canonical broker entry point required')
    token = command[len(prefix):-1]
    if "'" in token:
        raise ValueError('literal quote in broker argument')
    try:
        data = json.loads(token)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('invalid broker payload') from exc
    expected = {'engine', 'session_id', 'agent_id', 'operation', 'artifact_id'}
    if not isinstance(data, dict) or data.get('operation') not in {'read', 'write'}:
        raise ValueError('invalid broker operation')
    if data['operation'] == 'write':
        expected.add('content')
    if set(data) != expected or any(not isinstance(v, str) for v in data.values()):
        raise ValueError('invalid broker fields')
    if broker_command(**data, identity=identity) != command:
        raise ValueError('noncanonical broker command')
    return data


def broker_target(bound: dict, data: dict) -> str:
    value = bound['manifest']
    request = _json(Path(value['request_path']))
    if data['operation'] == 'write':
        path = request['output_paths'].get(data['artifact_id'])
    else:
        paths = {row['artifact_id']: row['path'] for row in value['reads'] + value['conditional_reads']}
        paths.update(request['output_paths'])
        path = paths.get(data['artifact_id'])
    if not path or not path_allowed(bound, path, data['operation'], cwd=str(REPO_ROOT)):
        raise ValueError('artifact is not authorized for this operation/stage')
    return path


def validate_broker_call(bound: dict, command: str, payload: dict) -> dict:
    identity = bound['manifest']['broker']
    if sha256_file(Path(identity['python'])) != identity['interpreter_sha256']:
        raise ValueError('broker interpreter changed')
    if any(sha256_file(Path(path)) != digest for path, digest in identity['code'].items()):
        raise ValueError('broker implementation changed; start a new task')
    data = decode_broker_command(command, identity)
    if data['engine'] != bound['manifest']['identity']['engine']:
        raise ValueError('broker engine differs from task')
    if data['session_id'] != payload.get('session_id') or data['agent_id'] != payload.get('agent_id', ''):
        raise ValueError('broker identity differs from host identity')
    broker_target(bound, data)
    return data


def execute_broker(data: dict, engine: str, *, repo_root: Path = REPO_ROOT) -> dict:
    bound = load_bound_access(data, engine, repo_root=repo_root)
    identity = bound['manifest']['broker']
    validate_broker_call(bound, broker_command(**data, identity=identity), data)
    target = Path(broker_target(bound, data))
    if data['operation'] == 'write':
        # O_NOFOLLOW protects the leaf; the second realpath check rejects parent changes.
        flags = os.O_WRONLY | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(target, flags, 0o600)
        try:
            if not path_allowed(bound, str(target), 'write', cwd=str(repo_root)):
                raise ValueError('output path changed')
            os.ftruncate(fd, 0)
            with os.fdopen(os.dup(fd), 'wb') as stream:
                stream.write(data['content'].encode('utf-8'))
        finally:
            os.close(fd)
        return {'schema_version': 1, 'kind': 'TASK_FILE_WRITE', 'artifact_id': data['artifact_id'], 'ok': True}
    content = target.read_bytes()
    # Recheck the actual bytes; never report a successful read from the request alone.
    rows = bound['manifest']['reads'] + bound['manifest']['conditional_reads']
    declared = next((row for row in rows if row['artifact_id'] == data['artifact_id']), None)
    if declared and sha256_bytes(content) != declared['sha256']:
        raise ValueError('input changed while reading')
    return {'schema_version': 1, 'kind': 'TASK_FILE_READ', 'identity': bound['manifest']['identity'],
            'artifact_id': data['artifact_id'], 'sha256': sha256_bytes(content),
            'manifest_sha256': sha256_file(manifest_path(Path(bound['manifest']['request_path']))),
            'byte_length': len(content), 'content': content.decode('utf-8'), 'ok': True}


def freeze_instructions(request_role: str, instruction_refs, destination: Path, outputs: dict) -> tuple[str, ...]:
    """Root selects needed contract fragments; model-followed links add no grants."""
    import re

    result = []
    for index, ref in enumerate(instruction_refs):
        source = Path(ref) if Path(ref).is_absolute() else REPO_ROOT / ref
        text = source.read_text(encoding='utf-8')
        if request_role.startswith('stock.') and request_role != 'stock.card':
            names = {'common', request_role}
        elif request_role == 'macro.research':
            groups = {'3_regional': 'regional', '4_crossasset': 'crossasset',
                      '5_sinous': 'sinous', '2_meso': 'meso', '6_meso_evidence': 'meso', '1_spine': 'spine'}
            names = {'common'} | {name for group, name in groups.items()
                                  if any(f'.{group}.' in key for key in outputs)}
            if any(key.endswith(('.decision', '.sector_map')) for key in outputs):
                names.add('allocation_contract')
        else:
            names = None
        if names:
            sections = {'## ' + part.split('\n', 1)[0]: '## ' + part
                        for part in re.split(r'(?m)^## ', text)[1:]}
            if not all('## ' + name in sections for name in names):
                raise ValueError('required instruction fragment missing')
            text = '\n'.join(section for heading, section in sections.items() if heading[3:] in names)
        target = destination / f'{index}-{source.name}'
        data = text.encode('utf-8')
        if target.exists() and target.read_bytes() != data:
            raise ValueError('frozen instructions conflict')
        if not target.exists():
            atomic_write_bytes(target, data)
        result.append(str(target.resolve()))
    return tuple(result)


def bind_headless(request, session_id: str, *, repo_root: Path = REPO_ROOT) -> dict:
    if not request.access_manifest_path:
        raise ValueError('legacy frozen request lacks C4 access manifest; start a new run')
    path = Path(request.access_manifest_path)
    dispatch = path.with_name(path.name.removesuffix('.access.json') + '.json')
    if _json(dispatch) != request.to_json():
        raise ValueError('headless request differs from frozen dispatch')
    binding = bind_context(dispatch, session_id=session_id, agent_id='', repo_root=repo_root, headless=True)
    return {'path': str(binding), 'sha256': sha256_file(binding), 'enforcement': 'UNVERIFIED',
            **bound_commands(dispatch, session_id, '')}


def broker_tool_command(tool: str, args: dict) -> str:
    if not isinstance(args, dict):
        raise ValueError('malformed broker tool input')
    schemas = {'Bash': ('command', {'command', 'description', 'timeout'}),
               'exec_command': ('cmd', {'cmd', 'workdir', 'max_output_tokens', 'yield_time_ms'}),
               'functions.exec_command': ('cmd', {'cmd', 'workdir', 'max_output_tokens', 'yield_time_ms'})}
    if tool not in schemas:
        raise ValueError('unregistered shell tool')
    key, fields = schemas[tool]
    if key not in args or not set(args) <= fields:
        raise ValueError('unregistered shell fields or execution override')
    return args[key]


def orchestration_preflight(engine: str, orchestration: str) -> dict:
    from autoresearch.contracts.research_access import LEGACY_ACCESS_REASON

    report = capability(engine)
    runnable = orchestration == 'session_v1' and report['runnable']
    return {'schema_version': 1, 'engine': engine, 'orchestration': orchestration,
            'status': 'CONFIGURED_UNVERIFIED' if runnable else 'HOST_CAPABILITY_REQUIRED',
            'runnable': runnable, 'research_access': report, 'acceptance_satisfied': False,
            'reason': (report['reason'] if orchestration == 'session_v1' else
                       LEGACY_ACCESS_REASON)}


def require_orchestration_access(engine: str, orchestration: str) -> dict:
    report = orchestration_preflight(engine, orchestration)
    if not report['runnable']:
        raise ValueError(f"HOST_CAPABILITY_REQUIRED: {report['reason']}")
    return report


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    preflight = commands.add_parser('preflight')
    preflight.add_argument('--orchestration', required=True, choices=('legacy', 'session_v1'))
    args = parser.parse_args(argv)
    engine = os.environ.get('AUTORESEARCH_ENGINE', '')
    report = orchestration_preflight(engine, args.orchestration)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report['runnable'] else 2



def bound_commands(request_path: Path, session_id: str, agent_id: str) -> dict:
    manifest = _load_manifest(request_path.resolve())
    return {'read_commands': {row['artifact_id']: broker_command(session_id, agent_id,
                'read', row['artifact_id'], identity=manifest['broker'])
                for row in manifest['reads'] + manifest['conditional_reads']},
            'write_commands': {key: broker_command(session_id, agent_id, 'write', key,
                'REPLACE_WITH_MARKDOWN', identity=manifest['broker'])
                for key in _json(request_path)['output_paths']}}


if __name__ == '__main__':
    raise SystemExit(main())

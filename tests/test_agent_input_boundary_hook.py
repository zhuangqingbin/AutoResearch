"""C4 exact task boundary, entirely synthetic: no loaded-host evidence."""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import tomllib

from tests.session_agent.test_task_access import manifest, request

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/hooks/agent_input_boundary.py'
WRAPPER = SCRIPT.with_suffix('.sh')


def load_hook():
    spec = importlib.util.spec_from_file_location('agent_input_boundary', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(tmp_path, monkeypatch):
    from autoresearch.session_agent import task_access as access
    req, value = manifest(tmp_path)
    access.bind_context(tmp_path / 'session/dispatch/judge-a1.json', session_id='host-1',
                        agent_id='child-1', repo_root=tmp_path)
    hook = load_hook()
    monkeypatch.setattr(hook, 'REPO_ROOT', tmp_path)
    payload = {'session_id': 'host-1', 'agent_id': 'child-1', 'agent_type': 'L4 card',
               'cwd': str(tmp_path), 'tool_name': 'Read',
               'tool_input': {'file_path': req.input_paths['stock.slim']}}
    return hook, req, payload, tmp_path


def denied(result):
    assert result['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert 'AGENT_INPUT_BOUNDARY' in result['hookSpecificOutput']['permissionDecisionReason']


@pytest.mark.parametrize('engine', ['codex', 'claude'])
@pytest.mark.parametrize('policy', ['READ_WRITE', 'WEB_WRITE', 'READ_WEB_WRITE'])
def test_codex_native_web_tool_name_requires_registered_web_policy(tmp_path, monkeypatch, engine, policy):
    from autoresearch.session_agent import task_access as access

    req = replace(request(tmp_path), engine=engine, tool_policy=policy)
    state = tmp_path / 'session/tasks.json'
    state.parent.mkdir()
    state.write_text(json.dumps({'engine': engine, 'run_id': req.run_id, 'tasks': {
        req.task_id: {'state': 'RUNNING', 'attempt': req.attempt, 'session_ref': 'host-1',
                     'spec': {'owner': 'SESSION', 'kind': 'INFERENCE', 'role': req.role,
                              'task_id': req.task_id}}}}))
    dispatch = tmp_path / 'session/dispatch/judge-a1.json'
    access.freeze_access(req, dispatch)
    access.bind_context(dispatch, session_id='host-1', agent_id='child-1', repo_root=tmp_path)
    hook = load_hook()
    monkeypatch.setattr(hook, 'REPO_ROOT', tmp_path)
    payload = {'session_id': 'host-1', 'agent_id': 'child-1', 'agent_type': req.agent_type,
               'cwd': str(tmp_path), 'tool_name': 'webrun',
               'tool_input': {'search_query': [{'q': 'company announcement'}]}}

    result = hook.decide(payload, engine)
    if engine == 'codex' and 'WEB' in policy.split('_'):
        assert result is None
    else:
        denied(result)


def test_normal_declared_input_and_same_attempt_output_readback(env):
    hook, req, payload, root = env
    assert hook.decide(payload, 'codex') is None
    output = req.output_paths['card']
    assert hook.decide({**payload, 'tool_name': 'Write', 'tool_input': {'file_path': output, 'content': 'x'}}, 'codex') is None
    Path(output).write_text('x')
    assert hook.decide({**payload, 'tool_input': {'file_path': output}}, 'codex') is None


@pytest.mark.parametrize('path', ['stock-B/private.md', 'context_codex/other-run/pack.md',
    'context_claude/private.md', 'autoresearch/source.py', '.claude/agents/other.md',
    'staging/session_outputs/attempts/judge/a0002/outputs/card.md', 'session/dispatch/judge-a1.access.json'])
def test_undeclared_files_never_gain_access(env, path):
    hook, req, payload, root = env
    denied(hook.decide({**payload, 'tool_input': {'file_path': str(root / path)}}, 'codex'))
    denied(hook.decide({**payload, 'tool_name': 'Write', 'tool_input': {'file_path': str(root / path), 'content': 'x'}}, 'codex'))


@pytest.mark.parametrize('command', ["cat slim.md", "cat 'slim.md'", 'cat "slim.md"',
    "cat <<'EOF'\nslim.md\nEOF", "cat $(printf slim.md)",
    "python3 -c \"open('slim.md').read()\"", "python3 - <<'PY'\nopen('slim.md').read()\nPY",
    'x=slim.md; cat "$x"', 'printf x > staging/session_outputs/attempts/judge/a0001/outputs/card.md'])
def test_generic_shell_is_not_lexically_authorized(env, command):
    hook, req, payload, root = env
    denied(hook.decide({**payload, 'tool_name': 'Bash', 'tool_input': {'command': command}}, 'codex'))


@pytest.mark.parametrize('tool,args', [('Grep', {'path': '.', 'pattern': 'secret'}),
    ('Glob', {'path': '.', 'pattern': '*.md'}), ('Read', {}), ('Read', 'bad'),
    ('unknown_read_tool', {'path': 'slim.md'}), ('Edit', {'file_path': 'slim.md'})])
def test_missing_fields_searches_and_unknown_tools_fail_closed(env, tool, args):
    hook, req, payload, root = env
    denied(hook.decide({**payload, 'tool_name': tool, 'tool_input': args}, 'codex'))


def test_missing_manifest_and_spoofed_or_missing_role(env):
    hook, req, payload, root = env
    assert hook.decide({key: value for key, value in payload.items() if key != 'agent_type'}, 'codex') is None
    denied(hook.decide({**payload, 'agent_type': 'deterministic command relay'}, 'codex'))
    (root / 'session/dispatch/judge-a1.access.json').unlink()
    denied(hook.decide(payload, 'codex'))


@pytest.mark.parametrize('change', [{'state': 'SUCCEEDED'}, {'state': 'FAILED'}, {'state': 'ABANDONED'}, {'attempt': 2}])
def test_stale_terminal_binding_denied(env, change):
    hook, req, payload, root = env
    state = root / 'session/tasks.json'
    doc = json.loads(state.read_text())
    doc['tasks']['judge'].update(change)
    state.write_text(json.dumps(doc))
    denied(hook.decide(payload, 'codex'))


def test_symlink_to_other_stock_denied(env):
    hook, req, payload, root = env
    other = root / 'stock-B.md'
    other.write_text('secret')
    link = root / 'slim-link.md'
    link.symlink_to(other)
    denied(hook.decide({**payload, 'tool_input': {'file_path': str(link)}}, 'codex'))


@pytest.mark.parametrize('raw', ['', 'not json', '[]', '{"agent_type":"L4 card",',
    '{"agent_type":"L4 card"}'])
def test_parseerror_is_denied(raw):
    hook = load_hook()
    out = io.StringIO()
    hook.main(io.StringIO(raw), out, ['codex'])
    denied(json.loads(out.getvalue()))


@pytest.mark.parametrize('role', [None, 'default', 'general-purpose', 'Explore', 'Plan', 'worker', 'explorer',
    'deterministic command relay', 'deterministic JSON relay'])
def test_explicit_unbound_root_and_relay_exemptions(role):
    hook = load_hook()
    payload = {'tool_name': 'Bash', 'tool_input': {'command': 'python3 arbitrary.py'}}
    if role:
        payload.update(agent_type=role, agent_id='unbound')
    assert hook.decide(payload, 'codex') is None


@pytest.mark.parametrize('role', ['worker', 'explorer'])
def test_bound_research_cannot_switch_to_development_role(env, role):
    hook, req, payload, root = env
    denied(hook.decide({**payload, 'agent_type': role}, 'codex'))


def test_unknown_child_identity_is_not_root():
    hook = load_hook()
    denied(hook.decide({'agent_id': 'unknown', 'tool_name': 'Read', 'tool_input': {'file_path': '/tmp/x'}}, 'codex'))


def test_role_registry_and_hook_configuration_cover_all_tools():
    hook = load_hook()
    claude = {hook.normalize_agent_type(p.stem) for p in (ROOT / '.claude/agents').glob('*.md')}
    codex = {hook.normalize_agent_type(tomllib.loads(p.read_text())['name'])
             for p in (ROOT / '.codex/agents').glob('*.toml') if p.stem not in {'gp_shell', 'gp_shell_json'}}
    assert claude | codex == hook.GUARDED_AGENT_TYPES
    for path in [ROOT / '.codex/hooks.json', ROOT / '.claude/settings.json']:
        hooks = json.loads(path.read_text())['hooks']['PreToolUse']
        assert any(row['matcher'] == '*' and any('agent_input_boundary.sh' in h['command']
                   for h in row['hooks']) for row in hooks)


def test_wrapper_python_failure_does_not_fail_open(tmp_path):
    fake = tmp_path / 'python3'
    fake.write_text('#!/bin/sh\nexit 17\n')
    fake.chmod(0o755)
    import os
    proc = subprocess.run(['/bin/sh', str(WRAPPER), 'codex'],
                          input='{"agent_type":"L4 card"}', text=True, capture_output=True,
                          env={**os.environ, 'PATH': f'{tmp_path}:/bin:/usr/bin'}, check=False)
    denied(json.loads(proc.stdout))


def test_broker_canonical_command_checks_real_identity_and_artifact(env):
    from autoresearch.session_agent import task_access as access
    hook, req, payload, root = env
    command = access.broker_command('host-1', 'child-1', 'read', 'stock.slim')
    call = {**payload, 'tool_name': 'Bash', 'tool_input': {'command': command}}
    assert hook.decide(call, 'codex') is None
    for bad in [command + '; id', ' ' + command, command + '\n', command.replace(' -I ', ' -c '),
                access.broker_command('host-1', 'other', 'read', 'stock.slim'),
                access.broker_command('host-1', 'child-1', 'read', 'other.artifact')]:
        denied(hook.decide({**call, 'tool_input': {'command': bad}}, 'codex'))


@pytest.mark.parametrize('extra', [{'shell': '/tmp/evil'}, {'env': {'PYTHONPATH': '/tmp/evil'}},
                                  {'cmd': 'id'}, {'dangerouslyDisableSandbox': True}])
def test_broker_rejects_shell_execution_overrides(env, extra):
    from autoresearch.session_agent import task_access as access
    hook, req, payload, root = env
    command = access.broker_command('host-1', 'child-1', 'read', 'stock.slim')
    denied(hook.decide({**payload, 'tool_name': 'Bash', 'tool_input': {'command': command, **extra}}, 'codex'))

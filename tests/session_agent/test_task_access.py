"""C4 task access decisions use synthetic files, never live host evidence."""
from dataclasses import replace
from pathlib import Path

import pytest

from autoresearch.session_agent.executors.base import DispatchRequest


def request(tmp_path):
    source = tmp_path / 'instructions.md'
    source.write_text('Only the assigned inputs.')
    slim = tmp_path / 'slim.md'
    slim.write_text('stock A facts')
    deep = tmp_path / 'deep.md'
    deep.write_text('stock A deep')
    out = tmp_path / 'staging/session_outputs/attempts/judge/a0001/outputs/card.md'
    out.parent.mkdir(parents=True)
    return DispatchRequest(run_id='run-A', engine='codex', task_id='judge', attempt=1,
        role='stock.card', agent_type='L4 card', config_role='l4_card', model=None, effort=None,
        agent_spec={}, tier=None, max_turns=None, prompt='Read slim and conditionally deep.',
        instruction_refs=(str(source),), input_paths={'stock.slim': str(slim), 'stock.deep': str(deep)},
        output_paths={'card': str(out)}, subject='A', independent_context=False,
        tool_policy='READ_WRITE', timeout_seconds=30, host_session_ref='host-1')


def manifest(tmp_path):
    from autoresearch.session_agent.task_access import freeze_access
    req = request(tmp_path)
    import json
    state = tmp_path / 'session/tasks.json'
    state.parent.mkdir(exist_ok=True)
    state.write_text(json.dumps({'engine': 'codex', 'run_id': 'run-A', 'tasks': {'judge': {'state': 'RUNNING', 'attempt': 1, 'session_ref': 'host-1', 'spec': {'owner': 'SESSION', 'kind': 'INFERENCE', 'role': 'stock.card', 'task_id': 'judge'}}}}))
    return req, freeze_access(req, tmp_path / 'session/dispatch/judge-a1.json')


def test_manifest_is_exact_frozen_and_attempt_scoped(tmp_path):
    req, value = manifest(tmp_path)
    assert value['identity'] == {'run_id': 'run-A', 'engine': 'codex', 'task_id': 'judge',
                                  'attempt': 1, 'role': 'stock.card', 'agent_type': 'L4 card', 'session_id': 'host-1'}
    assert {row['path'] for row in value['reads']} == {str(tmp_path / 'slim.md'), str(tmp_path / 'instructions.md')}
    assert [row['path'] for row in value['conditional_reads']] == [str(tmp_path / 'deep.md')]
    assert value['writes'] == list(req.output_paths.values())
    assert value['enforcement'] == 'UNVERIFIED'


def test_frozen_dispatch_without_c4_fields_remains_readable(tmp_path):
    req = request(tmp_path)
    assert DispatchRequest.from_json(req.to_json()) == req


def test_binding_is_host_identity_not_payload_manifest(tmp_path):
    from autoresearch.session_agent import task_access as access
    req, value = manifest(tmp_path)
    path = tmp_path / 'session/dispatch/judge-a1.json'
    access.bind_context(path, session_id='host-1', agent_id='child-1', repo_root=tmp_path)
    payload = {'session_id': 'host-1', 'agent_id': 'child-1', 'agent_type': 'L4 card',
               'manifest_path': '/attacker.json'}
    assert access.load_bound_access(payload, 'codex', repo_root=tmp_path)['manifest'] == value
    for change in ({'session_id': 'other'}, {'agent_id': 'other'}, {'agent_type': 'L3 rank'}):
        with pytest.raises((ValueError, OSError)):
            access.load_bound_access({**payload, **change}, 'codex', repo_root=tmp_path)
    with pytest.raises(ValueError, match='session'):
        access.bind_context(path, session_id='other', agent_id='child-2', repo_root=tmp_path)


def test_deep_requires_separate_root_authorization(tmp_path):
    from autoresearch.session_agent import task_access as access
    req, value = manifest(tmp_path)
    path = tmp_path / 'session/dispatch/judge-a1.json'
    access.bind_context(path, session_id='host-1', agent_id='child', repo_root=tmp_path)
    payload = {'session_id': 'host-1', 'agent_id': 'child', 'agent_type': 'L4 card'}
    bound = access.load_bound_access(payload, 'codex', repo_root=tmp_path)
    assert not access.path_allowed(bound, req.input_paths['stock.deep'], 'read', cwd=str(tmp_path))
    with pytest.raises(ValueError, match='stage'):
        access.authorize_deep(path, stage='initial', reason='agent wants it')
    access.authorize_deep(path, stage='P4', reason='root accepted depth escalation')
    bound = access.load_bound_access(payload, 'codex', repo_root=tmp_path)
    assert access.path_allowed(bound, req.input_paths['stock.deep'], 'read', cwd=str(tmp_path))
    assert not access.path_allowed(bound, str(tmp_path / 'other.deep'), 'read', cwd=str(tmp_path))


def test_realpaths_content_and_output_symlinks_are_checked(tmp_path):
    from autoresearch.session_agent import task_access as access
    req, value = manifest(tmp_path)
    bound = {'manifest': value, 'deep_authorized': False}
    source = tmp_path / 'slim.md'
    alias = tmp_path / 'alias.md'
    alias.symlink_to(source)
    assert access.path_allowed(bound, str(alias), 'read', cwd=str(tmp_path))
    source.write_text('changed')
    assert not access.path_allowed(bound, str(alias), 'read', cwd=str(tmp_path))
    output = Path(req.output_paths['card'])
    assert access.path_allowed(bound, str(output), 'write', cwd=str(tmp_path))
    output.symlink_to(tmp_path / 'elsewhere.md')
    assert not access.path_allowed(bound, str(output), 'write', cwd=str(tmp_path))


def test_manifests_and_bindings_cannot_be_rebound(tmp_path):
    from autoresearch.session_agent import task_access as access
    req, value = manifest(tmp_path)
    path = tmp_path / 'session/dispatch/judge-a1.json'
    access.bind_context(path, session_id='host-1', agent_id='child', repo_root=tmp_path)
    req2 = replace(req, task_id='another', output_paths={'card': str(tmp_path / 'staging/session_outputs/attempts/another/a0001/outputs/card.md')})
    other = tmp_path / 'session/dispatch/another-a1.json'
    access.freeze_access(req2, other)
    import json
    state = tmp_path / 'session/tasks.json'
    doc = json.loads(state.read_text())
    doc['tasks']['another'] = {**doc['tasks']['judge'], 'spec': {**doc['tasks']['judge']['spec'], 'task_id': 'another'}}
    state.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='conflict'):
        access.bind_context(other, session_id='host-1', agent_id='child', repo_root=tmp_path)
    with pytest.raises(ValueError, match='conflict'):
        access.freeze_access(replace(req, subject='B'), path)


def test_configured_hooks_are_not_observed_enforcement(tmp_path, monkeypatch):
    from autoresearch.session_agent import boundary_proof
    from autoresearch.session_agent.task_access import capability
    boundary_gate = boundary_proof.boundary_gate
    monkeypatch.setattr(boundary_proof, 'boundary_gate',
                        lambda **kwargs: boundary_gate(evidence_root=tmp_path, **kwargs))
    for engine, expected in [('codex', 'BROKER_CONFIGURED'), ('claude', 'UNVERIFIED')]:
        value = capability(engine)
        assert value['status'] == expected
        assert value['hook_configured'] is True
        assert value['acceptance_satisfied'] is False
        assert value['loaded_host_evidence'] == []


def test_broker_actual_read_write_and_reject_undeclared(tmp_path):
    from autoresearch.session_agent import task_access as access
    req, value = manifest(tmp_path)
    access.bind_context(tmp_path / 'session/dispatch/judge-a1.json', session_id='host-1', agent_id='child', repo_root=tmp_path)
    data = {'engine': 'codex', 'session_id': 'host-1', 'agent_id': 'child', 'operation': 'read', 'artifact_id': 'stock.slim'}
    result = access.execute_broker(data, 'codex', repo_root=tmp_path)
    assert result['content'] == 'stock A facts'
    assert result['ok'] and result['byte_length'] == len(b'stock A facts')
    with pytest.raises(ValueError):
        access.execute_broker({**data, 'artifact_id': 'stock.deep'}, 'codex', repo_root=tmp_path)
    access.execute_broker({**data, 'operation': 'write', 'artifact_id': 'card', 'content': "$(id)\n'hello'"}, 'codex', repo_root=tmp_path)
    assert Path(req.output_paths['card']).read_text() == "$(id)\n'hello'"
    with pytest.raises(ValueError):
        access.execute_broker({**data, 'operation': 'write', 'content': 'changed'}, 'codex', repo_root=tmp_path)


def test_old_frozen_request_cannot_be_bound_without_manifest(tmp_path):
    import json

    from autoresearch.session_agent import task_access as access
    req = request(tmp_path)
    path = tmp_path / 'old.json'
    path.write_text(json.dumps(req.to_json()))
    with pytest.raises(FileNotFoundError):
        access.bind_context(path, session_id='host-1', agent_id='child', repo_root=tmp_path)


def test_actual_claim_freezes_manifest_instructions_and_c3_outputs(tmp_path, monkeypatch):
    import json

    from autoresearch.session_agent import service, task_access as access

    from ._runner_support import begin_synthetic_run, inf
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf('judge')])
    result = service.claim(run.run_id, 'judge', 1, handle_loader=lambda _: run.handle,
                           event_recorder=lambda *args, **kwargs: None)['result']
    dispatch = result['dispatch_request']
    path = run.handle.workspace / 'session/dispatch/judge-a1.json'
    value = json.loads(access.manifest_path(path).read_text())
    assert value['writes'] == list(dispatch['output_paths'].values())
    assert '/session_outputs/attempts/judge/a0001/outputs/' in value['writes'][0]
    assert value['identity']['role'] == 'stock.card'
    assert all('/session/instructions/' in ref for ref in dispatch['instruction_refs'])
    assert result['access_manifest']['sha256']
    access.bind_context(path, session_id='session-main', agent_id='child', repo_root=tmp_path)


def test_capability_requires_present_hook_and_broker(tmp_path, monkeypatch):
    from autoresearch.session_agent import task_access as access
    monkeypatch.setattr(access, 'REPO_ROOT', tmp_path)
    value = access.capability('codex')
    assert value['runnable'] is False
    assert value['hook_configured'] is False
    assert value['status'] == 'UNSUPPORTED'


def test_headless_session_binding_guards_roleless_requests(tmp_path):
    import json

    from autoresearch.session_agent import task_access as access
    req = replace(request(tmp_path), engine='claude')
    state = tmp_path / 'session/tasks.json'
    state.parent.mkdir()
    state.write_text(json.dumps({'engine': 'claude', 'run_id': 'run-A', 'tasks': {'judge': {'state': 'RUNNING', 'attempt': 1, 'session_ref': 'host-1', 'spec': {'owner': 'SESSION', 'kind': 'INFERENCE', 'role': 'stock.card', 'task_id': 'judge'}}}}))
    path = tmp_path / 'session/dispatch/judge-a1.json'
    access.freeze_access(req, path)
    access.bind_context(path, session_id='headless-uuid', agent_id='', repo_root=tmp_path, headless=True)
    assert access.has_binding({'session_id': 'headless-uuid'}, 'claude', repo_root=tmp_path)
    assert access.load_bound_access({'session_id': 'headless-uuid'}, 'claude', repo_root=tmp_path)['manifest']['identity']['engine'] == 'claude'


def test_broker_write_command_keeps_long_markdown_readable(tmp_path):
    from autoresearch.session_agent import task_access as access
    content = ('长中文卡片：盈利与估值，$() `id` <<EOF\n单双引号\'\"\n' * 300)
    command = access.broker_command('host', 'child', 'write', 'card', content)
    assert '长中文卡片' in command
    assert '\\u0027' in command
    assert "'" not in command.split(" '", 1)[1][:-1]
    assert access.decode_broker_command(command, access.broker_identity())['content'] == content


def test_broker_long_card_roundtrip_through_real_shell_process(tmp_path):
    import json
    import runpy
    import shutil
    import subprocess

    from autoresearch.session_agent import task_access as access
    isolated = tmp_path / 'isolated'
    for relative in ['scripts/hooks/task_file_broker.py', 'autoresearch/session_agent/task_access.py',
                     'autoresearch/common/atomic.py', 'autoresearch/__init__.py', 'autoresearch/common/__init__.py']:
        target = isolated / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(access.REPO_ROOT / relative, target)
    policy = runpy.run_path(str(isolated / 'autoresearch/session_agent/task_access.py'))
    req = request(isolated)
    owner = isolated / 'session/tasks.json'
    owner.parent.mkdir(exist_ok=True)
    owner.write_text(json.dumps({'engine': 'codex', 'run_id': 'run-A', 'tasks': {'judge': {'state': 'RUNNING', 'attempt': 1, 'session_ref': 'host-1', 'spec': {'owner': 'SESSION', 'kind': 'INFERENCE', 'role': 'stock.card', 'task_id': 'judge'}}}}))
    dispatch = isolated / 'session/dispatch/judge-a1.json'
    policy['freeze_access'](req, dispatch)
    policy['bind_context'](dispatch, session_id='host-1', agent_id='child')
    content = ("长中文正文\n单引号' 双引号\"\n$(touch NEVER_CREATED) `touch NEVER_CREATED` <<'EOF'\n" * 200)
    command = policy['broker_command']('host-1', 'child', 'write', 'card', content)
    result = subprocess.run(command, shell=True, executable='/bin/sh', cwd=isolated,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr + result.stdout
    assert Path(req.output_paths['card']).read_text() == content
    assert not (isolated / 'NEVER_CREATED').exists()
    read = policy['broker_command']('host-1', 'child', 'read', 'card')
    response = subprocess.run(read, shell=True, executable='/bin/sh', cwd=isolated,
                              capture_output=True, text=True, check=True)
    assert json.loads(response.stdout)['content'] == content
    assert '长中文正文' in response.stdout


def test_legacy_headless_binding_is_explicitly_unsupported(tmp_path):
    from autoresearch.session_agent import task_access as access
    with pytest.raises(ValueError, match='legacy'):
        access.bind_headless(request(tmp_path), 'child-session', repo_root=tmp_path)


def test_one_attempt_cannot_bind_two_child_contexts(tmp_path):
    from autoresearch.session_agent import task_access as access
    manifest(tmp_path)
    path = tmp_path / 'session/dispatch/judge-a1.json'
    access.bind_context(path, session_id='host-1', agent_id='child', repo_root=tmp_path)
    with pytest.raises(ValueError, match='conflict'):
        access.bind_context(path, session_id='host-1', agent_id='different-child', repo_root=tmp_path)


def test_actual_claim_mailbox_cli_binding_and_broker(tmp_path, monkeypatch):
    from argparse import Namespace

    from autoresearch.session_agent import mailbox_cli, service, task_access as access

    from ._runner_support import begin_synthetic_run, inf
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf('judge')])
    service.claim(run.run_id, 'judge', 1, handle_loader=lambda _: run.handle,
                  event_recorder=lambda *a, **k: None)
    monkeypatch.setattr(mailbox_cli, '_handle', lambda _: run.handle)
    monkeypatch.setattr(access, 'REPO_ROOT', tmp_path)
    result = mailbox_cli.mailbox_command(Namespace(mailbox_command='bind-access', run_id=run.run_id,
        task_id='judge', attempt=1, session_ref=None, context_ref='mailbox-child'))
    assert result['kind'] == 'ACCESS_BOUND'
    value = access._load_manifest(run.handle.workspace / 'session/dispatch/judge-a1.json')
    data = access.decode_broker_command(result['read_commands']['synthetic.brief'], value['broker'])
    assert access.execute_broker(data, 'codex', repo_root=tmp_path)['content'] == 'frozen input synthetic.brief\n'


def test_actual_claim_headless_dispatch_binds_uuid_and_prompt(tmp_path, monkeypatch):
    from autoresearch.session_agent import service, task_access as access
    from autoresearch.session_agent.executors.headless_claude import HeadlessClaudeExecutor

    from . import _runner_support as support
    from .test_headless_claude import _WRITE_OUTPUT_AND_SUCCEED, _fake_claude
    monkeypatch.setattr(support, 'ENGINE', 'claude')
    run = support.begin_synthetic_run(tmp_path, monkeypatch, [support.inf('judge')])
    claimed = service.claim(run.run_id, 'judge', 1, handle_loader=lambda _: run.handle,
                            event_recorder=lambda *a, **k: None)['result']
    req = DispatchRequest.from_json(claimed['dispatch_request'])
    out = Path(next(iter(req.output_paths.values())))
    executable = _fake_claude(tmp_path, _WRITE_OUTPUT_AND_SUCCEED.format(out_dir=out.parent,
        out=out, projects=tmp_path / 'projects'))
    executor = HeadlessClaudeExecutor(run.handle.staging, claude_bin=executable, cwd=tmp_path,
                                     transcript_root=tmp_path / 'projects')
    result = executor.dispatch(req)
    assert result.ok, result.error
    argv = (tmp_path / 'bin/argv.txt').read_text()
    assert 'task_file_broker.py' in argv and result.session_ref in argv
    bound = access.load_bound_access({'session_id': result.session_ref}, 'claude', repo_root=tmp_path)
    assert bound['manifest']['request_path'] == str(run.handle.workspace / 'session/dispatch/judge-a1.json')
    observed = access.execute_broker({'engine': 'claude', 'session_id': result.session_ref,
        'agent_id': '', 'operation': 'read', 'artifact_id': 'synthetic.brief'}, 'claude', repo_root=tmp_path)
    assert observed['content'] == 'frozen input synthetic.brief\n'


def test_binding_requires_authoritative_session_owner(tmp_path):
    import json

    from autoresearch.session_agent import task_access as access
    manifest(tmp_path)
    state = tmp_path / 'session/tasks.json'
    doc = json.loads(state.read_text())
    doc['tasks']['judge']['spec'] = {'owner': 'L4', 'kind': 'INFERENCE', 'role': 'stock.card', 'task_id': 'judge'}
    state.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='owner'):
        access.bind_context(tmp_path / 'session/dispatch/judge-a1.json', session_id='host-1', agent_id='child', repo_root=tmp_path)


@pytest.mark.parametrize('restart', ['fresh', 'prefrozen_ready', 'running_missing_grant', 'running_missing_manifest'])
def test_runner_activates_only_live_declared_deep_on_dispatch(tmp_path, monkeypatch, restart):
    from autoresearch.session_agent import runner, service, task_access as access
    from autoresearch.session_agent.dispatch import build_request

    from ._runner_support import begin_synthetic_run, inf, profile
    from .test_runner import _FakeExecutor

    task = inf('judge', inputs=['stock.deep'])
    run = begin_synthetic_run(tmp_path, monkeypatch, [task])
    path = run.handle.workspace / 'session/dispatch/judge-a1.json'
    grant = path.with_suffix('.deep-access.json')
    if restart == 'prefrozen_ready':
        build_request(run.handle, task, 1, host_profile=profile())
        assert not grant.exists()
        with pytest.raises(ValueError, match='stale|terminal'):
            access.bind_context(path, session_id='session-main', agent_id='child', repo_root=tmp_path)
    elif restart.startswith('running_'):
        service.claim(run.run_id, 'judge', 1, handle_loader=lambda _: run.handle,
                      event_recorder=lambda *a, **k: None)
        if restart == 'running_missing_grant':
            grant.unlink()
        else:
            access.manifest_path(path).unlink()

    class ReadingExecutor(_FakeExecutor):
        supports_reattach = True

        def dispatch(self, request):
            assert grant.exists()
            access.bind_context(path, session_id='session-main', agent_id='child', repo_root=tmp_path)
            result = access.execute_broker({'engine': 'codex', 'session_id': 'session-main',
                'agent_id': 'child', 'operation': 'read', 'artifact_id': 'stock.deep'},
                'codex', repo_root=tmp_path)
            assert result['content'] == 'frozen input stock.deep\n'
            return super().dispatch(request)

    executor = ReadingExecutor()
    result = runner.run_loop(run.run_id, executor, poll_seconds=0.01, max_rounds=200, hooks=run.hooks())
    if restart == 'running_missing_manifest':
        assert not result['finished']
        assert not executor.calls
    else:
        assert result['finished'], result
        assert executor.calls == [('judge', 1)]


@pytest.mark.parametrize('policy,expected', [
    ('READ_WRITE', '本任务可用工具:Read、Grep(只限上列输入)· Write(只限上列输出);'
                   '其余工具(含 Glob、WebSearch、WebFetch)会被宿主拒绝,不要尝试。'),
    ('READ_WEB_WRITE', '本任务可用工具:Read、Grep(只限上列输入)· Write(只限上列输出)· WebSearch、WebFetch;'
                       '其余工具(含 Glob)会被宿主拒绝,不要尝试。'),
    ('WEB_WRITE', '本任务可用工具:Write(只限上列输出)· WebSearch、WebFetch;'
                  '其余工具(含 Read、Grep、Glob)会被宿主拒绝,不要尝试。'),
])
def test_tool_allowance_states_exactly_what_the_boundary_hook_permits(policy, expected):
    """A11(2026-10-03):10-02 首跑 macro-brief / l4-card 的 WebSearch、sector-brief 的 Glob 全部被拒 ——
    agent 定义里有、任务没登记。网查权限归 E5 待裁;这里只把「这次能用什么」写进派发,省掉白跑的轮次。"""
    from autoresearch.session_agent.task_access import tool_allowance

    assert tool_allowance(policy) == expected


@pytest.mark.parametrize('policy,expected', [
    ('READ_WRITE', '本任务的文件读写只经宿主绑定的 task_file_broker 命令(读只限上列输入、写只限上列输出);'
                   '不联网;普通 shell / 解释器调用会被宿主拒绝,不要尝试。'),
    ('WEB_WRITE', '本任务的文件读写只经宿主绑定的 task_file_broker 命令(写只限上列输出);'
                  '可以联网检索;普通 shell / 解释器调用会被宿主拒绝,不要尝试。'),
])
def test_codex_dispatch_names_the_broker_not_claude_tools(policy, expected):
    """复审 I-4:Codex 没有 Read / Grep / Write,文件读写只经 task_file_broker(toml 与 access-boundary
    同口径);照抄 Claude 那句会叫它别读输入、别写输出。"""
    from autoresearch.session_agent.task_access import tool_allowance

    assert tool_allowance(policy, 'codex') == expected
    assert 'Read' not in expected and 'Write' not in expected


def test_actual_claim_prompt_carries_the_engines_tool_allowance(tmp_path, monkeypatch):
    from autoresearch.session_agent import service

    from ._runner_support import ENGINE, begin_synthetic_run, inf
    assert ENGINE == 'codex'
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf('judge')])
    result = service.claim(run.run_id, 'judge', 1, handle_loader=lambda _: run.handle,
                           event_recorder=lambda *args, **kwargs: None)['result']
    prompt = result['dispatch_request']['prompt']
    assert '本任务的文件读写只经宿主绑定的 task_file_broker 命令' in prompt
    assert '本任务可用工具:' not in prompt


def test_a_blind_role_is_not_told_to_read_its_provenance_inputs():
    """A11:l4-intel(WEB_WRITE,无 Read)登记的输入是卡任务包(只作溯源)—— 派发不该叫它去读,
    更不该把含 L3 论点的任务包路径摆在它面前(它被设计成盲于 L3)。"""
    from autoresearch.session_agent.dispatch import artifact_scope

    inputs = {'scan.l4.600519.a1.prompt': '/s/_l4_prompt_600519.md'}
    outputs = {'scan.l4.600519.a1.intel': '/s/out/intel.md'}
    blind = artifact_scope('WEB_WRITE', inputs, outputs)
    assert '_l4_prompt_600519' not in blind and '/s/out/intel.md' in blind
    assert blind.startswith('本任务不读任何文件')
    reader = artifact_scope('READ_WRITE', inputs, outputs)
    assert '_l4_prompt_600519' in reader and reader.startswith('本次只读取以下 artifact 输入')


# ── B8(2026-10-03):按引用派发(opt-in,`session.mailbox.by_reference`,缺省关)──────────
# 10-02 首跑:主会话把 44 份派发 prompt 逐字复述进 Agent 工具入参,共 23.3 万字符,约占主会话
# 输出四成。开启后宿主只传一行指针,全文冻结成文件、登记进该任务的授权读取。


def _claim(tmp_path, monkeypatch, *, by_reference: bool):
    from autoresearch.session_agent import service

    from ._runner_support import begin_synthetic_run, inf
    cfg = {"session": {"mailbox": {"by_reference": by_reference}}}
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf('judge')], user_config=cfg)
    result = service.claim(run.run_id, 'judge', 1, handle_loader=lambda _: run.handle,
                           event_recorder=lambda *args, **kwargs: None)['result']
    return run, result


def test_by_reference_freezes_the_full_prompt_and_hands_the_host_a_pointer(tmp_path, monkeypatch):
    import json

    from autoresearch.session_agent import task_access as access

    run, result = _claim(tmp_path, monkeypatch, by_reference=True)
    dispatch = result['dispatch_request']
    prompt_file = Path(dispatch['instruction_refs'][-1])
    assert prompt_file.name == 'judge-a1.prompt.md'
    assert prompt_file.read_text(encoding='utf-8') == dispatch['prompt']
    assert str(prompt_file) in dispatch['host_prompt'] and len(dispatch['host_prompt']) < 300
    manifest = json.loads(access.manifest_path(
        run.handle.workspace / 'session/dispatch/judge-a1.json').read_text())
    assert str(prompt_file.resolve()) in {row['path'] for row in manifest['reads']}


def test_by_reference_is_off_by_default(tmp_path, monkeypatch):
    _run, result = _claim(tmp_path, monkeypatch, by_reference=False)
    dispatch = result['dispatch_request']
    assert dispatch['host_prompt'] is None
    assert not dispatch['instruction_refs'][-1].endswith('.prompt.md')


def test_the_mailbox_hands_out_host_prompt_falling_back_to_the_full_prompt(tmp_path):
    from autoresearch.session_agent.executors import mailbox

    base = request(tmp_path)
    mailbox.issue_request(tmp_path, base)
    doc = mailbox.wait_request(tmp_path, timeout=0, poll_seconds=0)
    assert doc['host_prompt'] == base.prompt
    pointed = replace(base, task_id='judge2', host_prompt='先 Read /x/judge2-a1.prompt.md')
    mailbox.issue_request(tmp_path, pointed)
    doc = mailbox.wait_request(tmp_path, timeout=0, poll_seconds=0)
    assert doc['host_prompt'] == '先 Read /x/judge2-a1.prompt.md'


def test_headless_session_binding_serves_a_codex_thread_too(tmp_path):
    """2026-10-08:codex headless(`codex exec`)按线程 id 做 session 级绑定,与 Claude 的 --session-id 同构;
    hook 负载里 session_id = 线程 id、没有 agent_id,照样命中。"""
    import json

    from autoresearch.session_agent import task_access as access
    req = replace(request(tmp_path), engine='codex')
    state = tmp_path / 'session/tasks.json'
    state.parent.mkdir()
    state.write_text(json.dumps({'engine': 'codex', 'run_id': 'run-A', 'tasks': {'judge': {'state': 'RUNNING', 'attempt': 1, 'session_ref': 'host-1', 'spec': {'owner': 'SESSION', 'kind': 'INFERENCE', 'role': 'stock.card', 'task_id': 'judge'}}}}))
    path = tmp_path / 'session/dispatch/judge-a1.json'
    access.freeze_access(req, path)
    thread = '019a2c1e-7b6a-7c3b-9f1d-5c2f3a4b6d7e'
    access.bind_context(path, session_id=thread, agent_id='', repo_root=tmp_path, headless=True)
    assert access.has_binding({'session_id': thread}, 'codex', repo_root=tmp_path)
    assert access.load_bound_access({'session_id': thread}, 'codex', repo_root=tmp_path)['manifest']['identity']['engine'] == 'codex'
    with pytest.raises(ValueError, match='session-scoped'):
        access.bind_context(path, session_id=thread, agent_id='child', repo_root=tmp_path, headless=True)

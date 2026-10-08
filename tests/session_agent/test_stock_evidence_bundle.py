from __future__ import annotations

import json

import pytest

from autoresearch.session_agent import artifacts
from autoresearch.session_agent.workflows.stock import build_stock_plan
from .test_service import _handle, _request


def _plan(handle):
    request = {**_request(), "requested_mode": "FULL"}
    return build_stock_plan(request, handle)


def test_full_downstream_declares_original_evidence_and_all_analysts(tmp_path):
    tasks = {t["task_id"]: t for t in _plan(_handle(tmp_path))["tasks"]}
    bundle = tasks["stock.evidence_bundle"]
    sources = set(bundle["input_artifact_ids"])
    assert {"research.frame", "stock.context", "stock.indicators", "stock.full.intel",
            "stock.full.1_analysts.solvency", "stock.full.1_analysts.quality"} <= sources
    for name in ("reality_check", "bull", "bear", "manager", "risk", "premortem", "pm"):
        task = tasks[f"stock.{name}"]
        assert sources | {"stock.evidence_bundle"} <= set(task["input_artifact_ids"])
        assert task["independent_context"] is True
    assert "stock.full.3_risk.debate" in tasks["stock.pm"]["input_artifact_ids"]
    assert "stock.full.2_research.reality_check" in tasks["stock.bear"]["input_artifact_ids"]


def _sources(handle):
    for artifact_id, text in {"research.frame": '{}', "stock.context": 'raw inputs\n',
                              "stock.full.1_analysts.solvency": '债务到期风险\n来源: https://example.com/filing\n数据缺口: 担保明细未核\n'}.items():
        path = handle.staging / f"{artifact_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        artifacts.register_artifact(handle, artifact_id, path, "READ")
    artifacts.register_artifact(handle, "stock.evidence_bundle", handle.staging / 'bundle.json', 'WRITE')
    return ["research.frame", "stock.context", "stock.full.1_analysts.solvency"]


def test_bundle_preserves_solvency_and_rejects_changed_sources(tmp_path):
    from autoresearch.session_agent.evidence_bundle import build_bundle, validate_bundle
    handle = _handle(tmp_path)
    ids = _sources(handle)
    bundle = build_bundle(handle, ids)
    assert {x['artifact_id'] for x in bundle['sources']} == set(ids)
    assert bundle['source_index'][0]['artifact_id'] == 'stock.full.1_analysts.solvency'
    assert '担保明细未核' in bundle['data_gaps'][0]['text']
    artifacts.artifact_path(handle, 'stock.evidence_bundle').write_text(json.dumps(bundle))
    artifacts.bind_artifact_hash(handle, 'stock.evidence_bundle')
    validate_bundle(handle, ['stock.evidence_bundle', *ids])
    with pytest.raises(ValueError, match='declared'):
        validate_bundle(handle, ['stock.evidence_bundle'])
    artifacts.artifact_path(handle, ids[-1]).write_text('manager omitted debt')
    with pytest.raises(artifacts.ArtifactConflict):
        validate_bundle(handle, ['stock.evidence_bundle', *ids])


def test_bundle_accepts_cwd_relative_handle(tmp_path, monkeypatch):
    # Production handles are cwd-relative (`ws.context_root()` is `context_<engine>`), while
    # `declared_path` returns resolved absolute paths; the bundle must index both alike.
    from autoresearch.session_agent.evidence_bundle import build_bundle, validate_bundle
    reference = _handle(tmp_path / "abs")
    absolute = build_bundle(reference, _sources(reference))
    handle = _handle(tmp_path / "rel")
    monkeypatch.chdir(tmp_path / "rel")
    handle.workspace = handle.workspace.relative_to(tmp_path / "rel")
    handle.staging = handle.staging.relative_to(tmp_path / "rel")
    handle.capsule = handle.capsule.relative_to(tmp_path / "rel")
    ids = _sources(handle)
    bundle = build_bundle(handle, ids)
    assert [x["relative_path"] for x in bundle["sources"]] == [x["relative_path"] for x in absolute["sources"]]
    artifacts.artifact_path(handle, "stock.evidence_bundle").write_text(json.dumps(bundle))
    artifacts.bind_artifact_hash(handle, "stock.evidence_bundle")
    validate_bundle(handle, ["stock.evidence_bundle", *ids])


def test_evidence_bundle_operation_is_closed_and_replayable():
    from autoresearch.session_agent.operations import build_argv
    from autoresearch.contracts.operation_replay import operation_replay_classification
    assert build_argv('stock.evidence_bundle', {})[-1] == 'stock-evidence-bundle'
    assert operation_replay_classification('stock.evidence_bundle') == 'COMPUTE'
    with pytest.raises(ValueError):
        build_argv('stock.evidence_bundle', {'path': '/tmp/arbitrary'})


def test_full_prompt_loads_only_common_and_selected_role(tmp_path):
    from autoresearch.session_agent.dispatch import _render_domain_prompt
    handle = _handle(tmp_path)
    task = {"task_id": "stock.solvency", "role": "stock.solvency", "subject": "600519.SS"}
    prompt = _render_domain_prompt(handle, task, 1, inputs={"stock.context": '/declared/raw.md'},
                                   outputs={"solvency": '/declared/solvency.md'})
    assert 'full_role=stock.solvency' in prompt
    assert 'usage=standalone' in prompt and 'depth=FULL' in prompt
    assert '## common' in prompt and '## stock.solvency' in prompt
    assert '债务期限' in prompt and '/declared/raw.md' in prompt
    assert '## stock.pm' not in prompt


@pytest.mark.parametrize('kind', ['READ_REQUESTED', 'READ_FAILED', 'READ_PARTIAL', 'EXEC_SUCCEEDED', 'READ_SUCCEEDED'])
def test_deep_requires_observed_successful_full_read(tmp_path, monkeypatch, kind):
    from autoresearch.session_agent.evidence_bundle import require_read_evidence
    from autoresearch.session_agent import host_evidence
    handle = _handle(tmp_path)
    ids = _sources(handle)
    target = artifacts.artifact_path(handle, ids[-1])
    normalized = handle.capsule / 'observed.json'
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_text(json.dumps({'operations': [{'kind': kind, 'path': str(target), 'path_source': 'tool_input', 'call_id': 'partial-read', 'response': {'encoding': 'utf8_text', 'sha256': '0' * 64, 'byte_count': 1}}]}))
    binding = {'binding_id': 'x', 'normalized_path': 'observed.json', 'role': 'stock.card', 'subject': '600519.SS', 'engine': handle.engine, 'run_id': handle.run_id, 'task_id': 'stock.card', 'attempt': 1}
    monkeypatch.setattr(host_evidence, '_existing_binding', lambda *args: binding)
    monkeypatch.setattr(host_evidence, '_load_binding_ref', lambda *args: binding)
    with pytest.raises(ValueError, match='未核'):
        require_read_evidence(handle, {'task_id': 'stock.card', 'role': 'stock.card', 'subject': '600519.SS',
                                     'input_artifact_ids': ids}, {'envelope': {'attempt': 1}}, ids[-1])


def test_deep_accepts_bound_read_but_not_undeclared_file(tmp_path, monkeypatch):
    from autoresearch.session_agent.evidence_bundle import require_read_evidence
    from autoresearch.session_agent import host_evidence
    handle = _handle(tmp_path)
    ids = _sources(handle)
    target = artifacts.artifact_path(handle, ids[-1])
    normalized = handle.capsule / 'observed.json'
    normalized.parent.mkdir(parents=True, exist_ok=True)
    from dataclasses import asdict
    from autoresearch.trace.transcripts.base import hash_tool_response
    response = asdict(hash_tool_response(target.read_text()))
    normalized.write_text(json.dumps({'operations': [{'kind': 'READ_SUCCEEDED', 'path': str(target), 'path_source': 'tool_input', 'call_id': 'read-1', 'response': response}],
        'items': [{'kind': 'tool_request', 'payload': {'tool_call_id': 'read-1', 'tool_name': 'Read', 'input': {'file_path': str(target)}}},
                  {'kind': 'tool_result', 'payload': {'tool_call_id': 'read-1', 'content': target.read_text(), 'is_error': False}}]}))
    binding = {'binding_id': 'x', 'normalized_path': 'observed.json', 'role': 'stock.card', 'subject': '600519.SS', 'engine': handle.engine, 'run_id': handle.run_id, 'task_id': 'stock.card', 'attempt': 1}
    monkeypatch.setattr(host_evidence, '_existing_binding', lambda *args: binding)
    monkeypatch.setattr(host_evidence, '_load_binding_ref', lambda *args: binding)
    task = {'task_id': 'stock.card', 'role': 'stock.card', 'subject': '600519.SS', 'input_artifact_ids': ids}
    assert require_read_evidence(handle, task, {'envelope': {'attempt': 1}}, ids[-1])['call_id'] == 'read-1'
    with pytest.raises(ValueError, match='declared'):
        require_read_evidence(handle, {**task, 'input_artifact_ids': []}, {'envelope': {'attempt': 1}}, ids[-1])


@pytest.mark.parametrize('observed,accepted', [('frozen task input', True), ('frozen', False)])
def test_deep_read_proof_uses_real_bound_transcript_response_bytes(tmp_path, observed, accepted):
    from autoresearch.session_agent import host_evidence, service
    from autoresearch.session_agent.evidence_bundle import require_read_evidence
    from tests.forensics.test_host_evidence import _running_case, _rollout

    handle, _, claimed = _running_case(tmp_path)
    source = _rollout(tmp_path)
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    ordinal = max(row['ordinal'] for row in rows) + 1
    path = artifacts.artifact_path(handle, 'inference.input')
    rows.extend([
        {'timestamp': '2026-09-13T01:02:03Z', 'ordinal': ordinal, 'type': 'response_item',
         'payload': {'type': 'function_call', 'name': 'read_file', 'call_id': 'synthetic-read',
                     'arguments': json.dumps({'path': str(path)})}},
        {'timestamp': '2026-09-13T01:02:04Z', 'ordinal': ordinal + 1, 'type': 'response_item',
         'payload': {'type': 'function_call_output', 'call_id': 'synthetic-read', 'output': observed}},
    ])
    source.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
    host_evidence.bind_task_transcript(
        handle.run_id, 'inference.one', 1, source, context_ref='synthetic-agent',
        parent_context_ref='session-main', session_ref='session-main', start_ordinal=0,
        end_ordinal=ordinal + 1, context_source='SUBAGENT', handle_loader=lambda _: handle,
    )
    task = service._task(handle, 'inference.one')
    submission = {'envelope': claimed['result']['envelope']}
    if accepted:
        proof = require_read_evidence(handle, task, submission, 'inference.input')
        assert proof['artifact_sha256'] == artifacts.binding_sha256(handle, 'inference.input')
    else:
        with pytest.raises(ValueError, match='未核'):
            require_read_evidence(handle, task, submission, 'inference.input')


@pytest.mark.parametrize('case', ['ok', 'truncated', 'hash_only', 'error', 'missing', 'duplicate', 'wrong_agent', 'ordinary_shell', 'result_first', 'raw', 'missing_exit', 'exit_failure', 'truncated_flag'])
def test_broker_full_read_uses_bound_actual_response(tmp_path, case):
    from autoresearch.session_agent import host_evidence, service, task_access as access
    from autoresearch.session_agent.evidence_bundle import require_read_evidence
    from tests.forensics.test_host_evidence import _rollout, _running_case

    handle, _, claimed = _running_case(tmp_path, input_id="stock.deep")
    dispatch = handle.workspace / 'session/dispatch/inference.one-a1.json'
    access.bind_context(dispatch, session_id='session-main', agent_id='synthetic-agent', repo_root=tmp_path)
    data = {'engine': 'codex', 'session_id': 'session-main', 'agent_id': 'synthetic-agent',
            'operation': 'read', 'artifact_id': 'stock.deep'}
    observed = access.execute_broker(data, 'codex', repo_root=tmp_path)
    command = access.broker_command(**data)
    if case == 'truncated':
        observed['content'] = 'frozen'
    if case == 'hash_only':
        observed.pop('content')
    if case == 'wrong_agent':
        command = access.broker_command(**{**data, 'agent_id': 'other'})
    if case == 'ordinary_shell':
        command = 'echo forged'
    wrapper = {'exit_code': 0, 'output': json.dumps(observed, ensure_ascii=False)}
    if case == 'raw':
        wrapper = observed
    if case == 'missing_exit':
        wrapper.pop('exit_code')
    if case == 'exit_failure':
        wrapper['exit_code'] = 2
    if case == 'truncated_flag':
        wrapper['truncated'] = True
    source = _rollout(tmp_path)
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    ordinal = max(row['ordinal'] for row in rows) + 1
    call = {'timestamp': '2026-09-13T01:02:03Z', 'ordinal': ordinal, 'type': 'response_item',
            'payload': {'type': 'function_call', 'name': 'Bash', 'call_id': 'broker-read',
                        'arguments': json.dumps({'command': command})}}
    rows.append(call)
    if case == 'duplicate':
        rows.append({**call, 'ordinal': ordinal + 1})
    if case != 'missing':
        rows.append({'timestamp': '2026-09-13T01:02:04Z', 'ordinal': ordinal + 2, 'type': 'response_item',
                     'payload': {'type': 'function_call_output', 'call_id': 'broker-read',
                                 'is_error': case == 'error', 'output': json.dumps(wrapper, ensure_ascii=False)}})
    if case == 'result_first':
        rows[-1], rows[-2] = rows[-2], rows[-1]
        rows[-2]['ordinal'], rows[-1]['ordinal'] = ordinal, ordinal + 2
    source.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
    host_evidence.bind_task_transcript(handle.run_id, 'inference.one', 1, source,
        context_ref='synthetic-agent', parent_context_ref='session-main', session_ref='session-main',
        start_ordinal=0, end_ordinal=ordinal + 2, context_source='SUBAGENT', handle_loader=lambda _: handle)
    task = service._task(handle, 'inference.one')
    submission = {'envelope': claimed['result']['envelope']}
    if case == 'ok':
        proof = require_read_evidence(handle, task, submission, 'stock.deep')
        assert proof['call_id'] == 'broker-read'
        assert proof['artifact_sha256'] == artifacts.binding_sha256(handle, 'stock.deep')
    else:
        with pytest.raises(ValueError, match='未核'):
            require_read_evidence(handle, task, submission, 'stock.deep')


@pytest.mark.parametrize('tool', ['Read', 'read_file'])
@pytest.mark.parametrize('case', ['ordered', 'result_first', 'duplicate_request', 'duplicate_result', 'error'])
def test_native_full_read_requires_unique_ordered_bound_pair(tmp_path, tool, case):
    from autoresearch.session_agent import host_evidence, service
    from autoresearch.session_agent.evidence_bundle import require_read_evidence
    from tests.forensics.test_host_evidence import _rollout, _running_case

    handle, _, claimed = _running_case(tmp_path)
    source = _rollout(tmp_path)
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    ordinal = max(row['ordinal'] for row in rows) + 1
    target = artifacts.artifact_path(handle, 'inference.input')
    request = {'type': 'function_call', 'name': tool, 'call_id': 'native-proof',
               'arguments': json.dumps({'file_path': str(target)})}
    result = {'type': 'function_call_output', 'call_id': 'native-proof',
              'output': target.read_text(), 'is_error': case == 'error'}
    calls = [request, result]
    if case == 'result_first':
        calls = [result, request]
    elif case == 'duplicate_request':
        calls = [request, request, result]
    elif case == 'duplicate_result':
        calls = [request, result, result]
    for index, payload in enumerate(calls):
        rows.append({'timestamp': '2026-09-13T01:02:03Z', 'ordinal': ordinal + index,
                     'type': 'response_item', 'payload': payload})
    source.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
    host_evidence.bind_task_transcript(handle.run_id, 'inference.one', 1, source,
        context_ref='synthetic-agent', parent_context_ref='session-main', session_ref='session-main',
        start_ordinal=0, end_ordinal=ordinal + len(calls) - 1, context_source='SUBAGENT',
        handle_loader=lambda _: handle)
    task = service._task(handle, 'inference.one')
    submission = {'envelope': claimed['result']['envelope']}
    if case == 'ordered':
        assert require_read_evidence(handle, task, submission, 'inference.input')['call_id'] == 'native-proof'
    else:
        with pytest.raises(ValueError, match='未核'):
            require_read_evidence(handle, task, submission, 'inference.input')

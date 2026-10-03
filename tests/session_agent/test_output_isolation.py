import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch.common.atomic import atomic_write_json
from autoresearch.session_agent import artifacts, store
from tests.session_agent.conftest import HASH, RUN_ID, make_plan, make_task


def setup_run(tmp_path, count=2):
    handle = SimpleNamespace(workspace=tmp_path, staging=tmp_path / 'staging', engine='codex', run_id=RUN_ID)
    handle.staging.mkdir()
    atomic_write_json(tmp_path / 'session/storage.json', {'schema_version': 1, 'output_layout_version': 2})
    task = make_task(kind='INFERENCE', role='test.writer', operation=None, input_artifact_ids=['input'],
                     output_artifact_ids=[f'out.{n}' for n in range(count)])
    plan = make_plan(tasks=[task])
    path = tmp_path / 'session/tasks.json'
    store.initialize(path, plan)
    for key in task['output_artifact_ids']:
        artifacts.register_artifact(handle, key, handle.staging / f'{key}.md', 'WRITE')
    store.claim(path, task['task_id'], 1, 'test')
    return handle, task, plan, path


def submission(handle, task, plan, attempt=1):
    return {'schema_version': 1, 'envelope': {'schema_version': 1, 'engine': 'codex', 'run_id': RUN_ID,
            'task_id': task['task_id'], 'role': task['role'], 'input_artifact_ids': ['input'],
            'input_contract_hash': HASH, 'expected_output_contract': task['expected_output_contract'], 'attempt': attempt},
            'plan_hash': plan['plan_hash'], 'outputs': [{'artifact_id': key, 'sha256': hashlib.sha256(Path(value).read_bytes()).hexdigest()}
            for key, value in artifacts.output_paths(handle, task, attempt).items()], 'host_receipt_id': None}


def prepare(handle, task, value):
    manifest = artifacts.capture_outputs(handle, task, value['envelope']['attempt'], expected=value['outputs'])
    with artifacts.candidate_view(handle, manifest):
        for key in task['output_artifact_ids']:
            with artifacts.open_artifact(handle, key) as stream:
                assert stream.read()
    return manifest


def test_first_attempt_private_multioutput_atomic_and_immutable(tmp_path):
    handle, task, plan, owner = setup_run(tmp_path)
    paths = artifacts.output_paths(handle, task, 1)
    for key, path in paths.items():
        assert '/attempts/step.one/a0001/outputs/' in path
        Path(path).write_text(key)
        with pytest.raises(artifacts.ArtifactConflict):
            artifacts.artifact_path(handle, key)
    value = submission(handle, task, plan)
    receipt = store.accept(owner, value, lambda value, task: None, prepare=lambda value, task: prepare(handle, task, value))
    for key, path in paths.items():
        Path(path).write_text('late write')
        with artifacts.open_artifact(handle, key) as stream:
            assert stream.read().decode() == key
    shutil.rmtree(Path(next(iter(paths.values()))).parent)
    assert store.accept(owner, value, lambda *_: pytest.fail('duplicate validated'), prepare=lambda *_: pytest.fail('duplicate captured')) == receipt
    artifacts.materialize_outputs(handle)
    assert (handle.staging / 'out.0.md').read_text() == 'out.0'


def test_missing_or_symlink_or_extra_output_has_zero_visibility(tmp_path):
    handle, task, plan, owner = setup_run(tmp_path)
    paths = artifacts.output_paths(handle, task, 1)
    first, second = map(Path, paths.values())
    first.write_text('one')
    with pytest.raises((ValueError, OSError)):
        artifacts.capture_outputs(handle, task, 1)
    second.symlink_to(first)
    with pytest.raises((ValueError, OSError)):
        artifacts.capture_outputs(handle, task, 1)
    second.unlink()
    second.write_text('two')
    (first.parent / 'extra.md').write_text('extra')
    with pytest.raises((ValueError, OSError)):
        artifacts.capture_outputs(handle, task, 1)
    assert store.read_states(owner)[task['task_id']] == 'RUNNING'
    for key in paths:
        with pytest.raises(artifacts.ArtifactConflict):
            artifacts.artifact_path(handle, key)


def test_second_invalid_output_and_before_owner_crash_leave_no_partial_accept(tmp_path, monkeypatch):
    handle, task, plan, owner = setup_run(tmp_path)
    for path in artifacts.output_paths(handle, task, 1).values():
        Path(path).write_text('valid')
    value = submission(handle, task, plan)
    def invalid(value, spec):
        prepare(handle, spec, value)
        raise ValueError('second output invalid')
    with pytest.raises(ValueError, match='second output'):
        store.accept(owner, value, lambda *_: None, prepare=invalid)
    write = store.atomic_write_json
    def crash(path, payload):
        if Path(path) == owner:
            raise RuntimeError('crash before owner')
        return write(path, payload)
    monkeypatch.setattr(store, 'atomic_write_json', crash)
    with pytest.raises(RuntimeError, match='before owner'):
        store.accept(owner, value, lambda *_: None, prepare=lambda value, spec: prepare(handle, spec, value))
    assert store.read_states(owner)[task['task_id']] == 'RUNNING'
    for key in task['output_artifact_ids']:
        with pytest.raises(artifacts.ArtifactConflict):
            artifacts.artifact_path(handle, key)


def test_owner_commit_before_receipt_crash_recovers_without_private_files(tmp_path, monkeypatch):
    handle, task, plan, owner = setup_run(tmp_path)
    paths = artifacts.output_paths(handle, task, 1)
    for path in paths.values():
        Path(path).write_text('accepted')
    value = submission(handle, task, plan)
    write = store.atomic_write_json
    def crash(path, payload):
        if str(path).endswith('.accepted.json'):
            raise RuntimeError('crash before receipt')
        return write(path, payload)
    monkeypatch.setattr(store, 'atomic_write_json', crash)
    with pytest.raises(RuntimeError, match='before receipt'):
        store.accept(owner, value, lambda *_: None, prepare=lambda value, spec: prepare(handle, spec, value))
    shutil.rmtree(Path(next(iter(paths.values()))).parent)
    monkeypatch.setattr(store, 'atomic_write_json', write)
    receipt = store.recover_receipt(owner, task['task_id'])
    assert store.accept(owner, value, lambda *_: pytest.fail('must be idempotent')) == receipt
    for key in paths:
        assert artifacts.read_bytes(handle, key) == b'accepted'
    different = {**value, 'outputs': [dict(row, sha256='0' * 64) for row in value['outputs']]}
    with pytest.raises(store.TaskConflict, match='different result'):
        store.accept(owner, different, lambda *_: None)


def test_timeout_retry_latewrite_and_rebuild_projection(tmp_path):
    handle, task, plan, owner = setup_run(tmp_path)
    old = artifacts.output_paths(handle, task, 1)
    for path in old.values():
        Path(path).write_text('abandoned')
    store.mark_failed(owner, task['task_id'], 1, {'code': 'TIMEOUT'}, retryable=True)
    store.claim(owner, task['task_id'], 2, 'test')
    for path in artifacts.output_paths(handle, task, 2).values():
        Path(path).write_text('winner')
    value = submission(handle, task, plan, 2)
    store.accept(owner, value, lambda *_: None, prepare=lambda value, spec: prepare(handle, spec, value))
    for path in old.values():
        Path(path).write_text('late')
    artifacts.materialize_outputs(handle)
    (handle.staging / 'out.0.md').write_text('projection interrupted')
    artifacts.materialize_outputs(handle)
    assert (handle.staging / 'out.0.md').read_text() == 'winner'
    with pytest.raises(store.TaskConflict):
        store.accept(owner, submission(handle, task, plan, 1), lambda *_: None)


@pytest.mark.parametrize('attack', ['during_copy', 'hardlink', 'parent_symlink'])
def test_capture_rejects_mutation_and_links(tmp_path, monkeypatch, attack):
    handle, task, plan, owner = setup_run(tmp_path, 1)
    path = Path(next(iter(artifacts.output_paths(handle, task, 1).values())))
    path.write_text('original')
    if attack == 'during_copy':
        write = artifacts.atomic_write_bytes
        def mutate(target, data):
            write(target, data)
            path.write_text('changed')
        monkeypatch.setattr(artifacts, 'atomic_write_bytes', mutate)
    elif attack == 'hardlink':
        import os
        os.link(path, tmp_path / 'linked')
    else:
        directory = path.parent
        moved = directory.with_name('escaped')
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        artifacts.capture_outputs(handle, task, 1)


def test_layout_is_owned_by_store_and_legacy_owner_cannot_be_upgraded(tmp_path):
    handle, task, plan, owner = setup_run(tmp_path)
    (tmp_path / 'session/storage.json').unlink()
    assert artifacts.layout_version(handle) == 2
    legacy = json.loads(owner.read_text())
    del legacy['output_layout_version']
    atomic_write_json(owner, legacy)
    before = owner.read_bytes()
    atomic_write_json(tmp_path / 'session/storage.json', {'output_layout_version': 2})
    assert artifacts.layout_version(handle) == 1
    assert owner.read_bytes() == before


def test_deterministic_same_basename_outputs_capture_distinct_bytes(tmp_path):
    handle, task, plan, owner = setup_run(tmp_path)
    registry = tmp_path / 'session/artifacts.json'
    rows = json.loads(registry.read_text())
    for index, key in enumerate(task['output_artifact_ids']):
        path = handle.staging / str(index) / 'same.json'
        path.parent.mkdir()
        path.write_text(str(index))
        rows['artifacts'][key]['relative_path'] = path.relative_to(tmp_path).as_posix()
    atomic_write_json(registry, rows)
    manifest = artifacts.capture_outputs(handle, dict(task, kind='DETERMINISTIC'), 1)
    assert len({row['relative_path'] for row in manifest.values()}) == 2
    with artifacts.candidate_view(handle, manifest):
        assert artifacts.read_bytes(handle, 'out.0') == b'0'
        assert artifacts.read_bytes(handle, 'out.1') == b'1'


@pytest.mark.parametrize('version', [True, '2', 0, 3, None])
def test_invalid_layout_version_fails_closed(tmp_path, version):
    handle, task, plan, owner = setup_run(tmp_path)
    payload = json.loads(owner.read_text())
    payload['output_layout_version'] = version
    atomic_write_json(owner, payload)
    with pytest.raises(artifacts.ArtifactConflict, match='layout version'):
        artifacts.output_paths(handle, task, 1)


def test_stable_declarations_do_not_rebind_changed_projection(tmp_path):
    handle, task, plan, owner = setup_run(tmp_path)
    path = artifacts.declared_path(handle, 'out.0')
    registry = tmp_path / 'session/artifacts.json'
    before = registry.read_bytes()
    path.write_text('untrusted projection')
    artifacts.register_artifact(handle, 'out.0', path, 'READ')
    assert registry.read_bytes() == before
    with pytest.raises(artifacts.ArtifactConflict):
        artifacts.open_artifact(handle, 'out.0')


def test_alias_and_retry_outputs_commit_together_preserving_previous_bytes(tmp_path):
    handle = SimpleNamespace(workspace=tmp_path, staging=tmp_path / 'staging', engine='codex', run_id=RUN_ID)
    handle.staging.mkdir()
    atomic_write_json(tmp_path / 'session/storage.json', {'output_layout_version': 2})
    tasks = [make_task(task_id=f'l4.600519.a{n}.card', kind='INFERENCE', role='test.writer', operation=None,
                       input_artifact_ids=['input'], output_artifact_ids=[f'scan.l4.600519.a{n}.card']) for n in [1, 2]]
    plan = make_plan(tasks=tasks)
    owner = tmp_path / 'session/tasks.json'
    store.initialize(owner, plan)
    for task in tasks:
        key = task['output_artifact_ids'][0]
        artifacts.register_artifact(handle, key, handle.staging / f'{key}.md', 'WRITE')
    first = tasks[0]
    store.claim(owner, first['task_id'], 1, 'test')
    Path(next(iter(artifacts.output_paths(handle, first, 1).values()))).write_text('original')
    value = submission(handle, first, plan)
    store.accept(owner, value, lambda *_: None, prepare=lambda value, spec: prepare(handle, spec, value))
    old_hash = value['outputs'][0]['sha256']
    payload = json.loads(owner.read_text())
    payload['tasks'][first['task_id']]['state'] = 'WAITING_RETRY'
    atomic_write_json(owner, payload)
    second = tasks[1]
    store.claim(owner, second['task_id'], 1, 'test')
    Path(next(iter(artifacts.output_paths(handle, second, 1).values()))).write_text('replacement')
    value = submission(handle, second, plan)
    store.accept(owner, value, lambda *_: None, prepare=lambda value, spec: prepare(handle, spec, value))
    assert store.read_states(owner)[first['task_id']] == 'SUPERSEDED'
    assert artifacts.read_bytes(handle, first['output_artifact_ids'][0]) == b'replacement'
    with artifacts.open_artifact_version(handle, first['output_artifact_ids'][0], old_hash) as stream:
        assert stream.read() == b'original'


@pytest.mark.parametrize('version', [1, 2])
def test_registered_replay_unit_preserves_bundle_bytes_across_output_layouts(tmp_path, version):
    from autoresearch.common.atomic import canonical_json, sha256_bytes
    from autoresearch.contracts.replay import validate_replay_unit
    from autoresearch.session_agent.evidence_bundle import build_bundle
    from autoresearch.session_agent.replay_adapters.common import safe_name
    from autoresearch.session_agent.replay_adapters.stock import execute
    from autoresearch.session_agent.workflows.stock import register_stock_artifacts
    from tests.session_agent.test_service import _handle, _request

    handle = _handle(tmp_path / 'live')
    request = dict(_request(), requested_mode='FULL')
    keys = ['stock.context', 'stock.full.1_analysts.solvency']
    task = make_task(kind='INFERENCE', role='test.writer', operation=None,
                     input_artifact_ids=['input'], output_artifact_ids=keys)
    plan = make_plan(tasks=[task])
    if version == 2:
        atomic_write_json(handle.workspace / 'session/storage.json', {'output_layout_version': 2})
    store.initialize(handle.workspace / 'session/tasks.json', plan)
    register_stock_artifacts(request, handle, plan)
    for path in artifacts.output_paths(handle, task, 1).values():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text('raw fact\n来源: https://example.test/filing\n缺口: 未核\n')
    if version == 2:
        store.claim(handle.workspace / 'session/tasks.json', task['task_id'], 1, 'test')
        value = submission(handle, task, plan)
        store.accept(handle.workspace / 'session/tasks.json', value, lambda *_: None,
                     prepare=lambda value, spec: prepare(handle, spec, value))
    else:
        for key in keys:
            artifacts.bind_artifact_hash(handle, key)
    expected = (canonical_json(build_bundle(handle, keys)) + '\n').encode()
    refs = [{'artifact_id': key, 'sha256': sha256_bytes(artifacts.read_bytes(handle, key)),
             'captured_path': f'evidence/{key}'} for key in keys]
    unit = validate_replay_unit({
        'unit_id': 'stock.evidence_bundle:a1', 'task_id': 'stock.evidence_bundle', 'attempt': 1,
        'operation': 'stock.evidence_bundle', 'mode': 'COMPUTE', 'dependencies': [],
        'input_refs': refs, 'expected_outputs': [{'artifact_id': 'stock.evidence_bundle',
            'sha256': sha256_bytes(expected), 'captured_path': 'evidence/bundle.json'}],
        'source_receipt_ids': [], 'comparison_policy': {'policy': 'EXACT_BYTES', 'version': 1, 'ignored_fields': []},
        'failure_expectation': None})
    root = tmp_path / 'replay'
    inputs = root / 'inputs/artifacts'
    inputs.mkdir(parents=True)
    (inputs / safe_name('session.request')).write_text(canonical_json(request))
    for key in keys:
        (inputs / safe_name(key)).write_bytes(artifacts.read_bytes(handle, key))
        target = artifacts.declared_path(handle, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('changed disposable projection')
    outputs = root / 'outputs'
    outputs.mkdir()
    context = SimpleNamespace(unit=unit, replay_run_id=handle.run_id, inputs=inputs.parent,
        work=root / 'work', outputs=outputs, env={'AUTORESEARCH_ENGINE': 'codex'},
        output_path=lambda key: outputs / safe_name(key))
    execute(unit, context)
    assert context.output_path('stock.evidence_bundle').read_bytes() == expected
    if version == 2:
        assert '/accepted/' in str(artifacts.artifact_path(handle, keys[0]))
        assert b'changed disposable' not in artifacts.read_bytes(handle, keys[0])


def test_service_resume_rebuilds_completion_after_owner_commit(tmp_path, monkeypatch):
    from autoresearch.session_agent import service

    from ._runner_support import begin_synthetic_run, inf
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf('synthetic.card')])
    def loader(_):
        return run.handle
    claimed = service.claim(run.run_id, 'synthetic.card', 1, handle_loader=loader,
                            event_recorder=lambda *_args, **_kwargs: None)['result']
    key, path = next(iter(claimed['claim_receipt']['output_paths'].items()))
    Path(path).write_text('valid candidate')
    value = {'schema_version': 1, 'envelope': claimed['envelope'], 'plan_hash': claimed['plan_hash'],
             'outputs': [{'artifact_id': key, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}],
             'host_receipt_id': None}
    write = store.atomic_write_json
    def crash(path, payload):
        if str(path).endswith('.accepted.json'):
            raise RuntimeError('crash after owner')
        return write(path, payload)
    monkeypatch.setattr(store, 'atomic_write_json', crash)
    with pytest.raises(RuntimeError, match='after owner'):
        service.submit(run.run_id, value, handle_loader=loader, validator=lambda *_: None,
                       event_recorder=lambda *_args, **_kwargs: None)
    shutil.rmtree(Path(path).parent)
    monkeypatch.setattr(store, 'atomic_write_json', write)
    service.resume(run.run_id, handle_loader=loader, event_recorder=lambda *_args, **_kwargs: None)
    completion = json.loads((run.handle.capsule / 'agents/session/completions/synthetic.card-a1.json').read_text())
    assert completion['outputs'] == value['outputs']
    assert artifacts.read_bytes(run.handle, key) == b'valid candidate'
    result = service.submit(run.run_id, value, handle_loader=loader, validator=lambda *_: pytest.fail('repeat'),
                            event_recorder=lambda *_args, **_kwargs: None)
    assert result['result']['receipt']['receipt_id'] == completion['receipt_id']


def test_legacy_duplicate_submission_recovers_completion_and_promotion(tmp_path, monkeypatch):
    from autoresearch.session_agent import service

    from .test_service import _handle, _request
    handle = _handle(tmp_path)
    task = make_task(kind='INFERENCE', role='stock.card', operation=None, expected_output_contract='stock.lite.v1',
                     input_artifact_ids=['input'], output_artifact_ids=['output'])
    plan = make_plan(tasks=[task])
    session = handle.workspace / 'session'
    for name, value in [('request', _request()), ('host_profile', _request()['host_profile']), ('plan', plan)]:
        atomic_write_json(session / f'{name}.json', value)
    store.initialize(session / 'tasks.json', plan)
    source = handle.staging / 'input.md'
    source.write_text('frozen')
    artifacts.register_artifact(handle, 'input', source, 'READ')
    target = handle.staging / 'output.md'
    artifacts.register_artifact(handle, 'output', target, 'WRITE')
    claimed = service.claim(handle.run_id, task['task_id'], 1, handle_loader=lambda _: handle,
                            event_recorder=lambda *_args, **_kwargs: None)['result']
    target.write_text('legacy candidate')
    digest = artifacts.bind_artifact_hash(handle, 'output')['sha256']
    value = {'schema_version': 1, 'envelope': claimed['envelope'], 'plan_hash': claimed['plan_hash'],
             'outputs': [{'artifact_id': 'output', 'sha256': digest}], 'host_receipt_id': None}
    freeze = service._freeze_json
    def crash(path, value):
        if '/completions/' in str(path):
            raise RuntimeError('legacy crash before completion')
        return freeze(path, value)
    monkeypatch.setattr(service, '_freeze_json', crash)
    with pytest.raises(RuntimeError, match='legacy crash'):
        service.submit(handle.run_id, value, handle_loader=lambda _: handle, validator=lambda *_: None,
                       event_recorder=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, '_freeze_json', freeze)
    promotions = []
    monkeypatch.setattr(service, '_promote_l4_retry_output', lambda *_: promotions.append(True))
    before = (session / 'artifacts.json').read_bytes()
    service.submit(handle.run_id, value, handle_loader=lambda _: handle, validator=lambda *_: None,
                   event_recorder=lambda *_args, **_kwargs: None)
    assert promotions == [True]
    assert (handle.capsule / 'agents/session/completions/step.one-a1.json').is_file()
    assert (session / 'artifacts.json').read_bytes() == before
    assert 'output_layout_version' not in json.loads((session / 'tasks.json').read_text())


def test_abandoned_mailbox_attempt_cannot_submit_before_runner_marks_failure(tmp_path, monkeypatch):
    from autoresearch.session_agent import service
    from autoresearch.session_agent.executors import mailbox
    from autoresearch.session_agent.executors.base import DispatchRequest

    from ._runner_support import begin_synthetic_run, inf
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf('synthetic.card')])
    claimed = service.claim(run.run_id, 'synthetic.card', 1, handle_loader=lambda _: run.handle,
                            event_recorder=lambda *_args, **_kwargs: None)['result']
    mailbox.issue_request(run.handle.staging, DispatchRequest.from_json(claimed['dispatch_request']))
    assert mailbox.abandon_request(run.handle.staging, 'synthetic.card', 1, reason='TIMEOUT')
    key, path = next(iter(claimed['claim_receipt']['output_paths'].items()))
    Path(path).write_text('late candidate')
    value = {'schema_version': 1, 'envelope': claimed['envelope'], 'plan_hash': claimed['plan_hash'],
             'outputs': [{'artifact_id': key, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}],
             'host_receipt_id': None}
    with pytest.raises(store.TaskConflict, match='abandoned'):
        service.submit(run.run_id, value, handle_loader=lambda _: run.handle, validator=lambda *_: None,
                       event_recorder=lambda *_args, **_kwargs: None)
    assert store.read_states(run.handle.workspace / 'session/tasks.json')['synthetic.card'] == 'RUNNING'
    with pytest.raises(artifacts.ArtifactConflict):
        artifacts.artifact_path(run.handle, key)


def test_degraded_l3_repair_restores_accepted_original_after_partial_write(tmp_path):
    from autoresearch.session_agent import domain_ops
    handle, task, plan, owner = setup_run(tmp_path, 1)
    registry = tmp_path / 'session/artifacts.json'
    rows = json.loads(registry.read_text())
    rows['artifacts']['scan.l3.judged'] = dict(rows['artifacts'].pop('out.0'), artifact_id='scan.l3.judged',
                                           relative_path='staging/_l3_judged.json')
    atomic_write_json(registry, rows)
    # Use the declared owner output identity for the accepted source.
    task['output_artifact_ids'] = ['scan.l3.judged']
    payload = json.loads(owner.read_text())
    payload['tasks'][task['task_id']]['spec'] = task
    atomic_write_json(owner, payload)
    Path(next(iter(artifacts.output_paths(handle, task, 1).values()))).write_text('original')
    value = submission(handle, task, plan)
    store.accept(owner, value, lambda *_: None, prepare=lambda value, spec: prepare(handle, spec, value))
    (handle.staging / '_l3_judged.json').write_text('half-applied before SIGKILL')
    result = domain_ops.scan_l3_repair_degraded({'code': 'OPERATION_FAILED'}, handle=handle)
    assert result['preserved_original'] is True
    assert (handle.staging / '_l3_effective_judged.json').read_text() == 'original'
    assert artifacts.read_bytes(handle, 'scan.l3.judged') == b'original'


def test_acceptance_serializes_abandonment_and_duplicate_stays_idempotent(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from autoresearch.session_agent import service
    from autoresearch.session_agent.executors import mailbox

    from ._runner_support import begin_synthetic_run, inf
    run = begin_synthetic_run(tmp_path, monkeypatch, [inf('synthetic.card')])
    claimed = service.claim(run.run_id, 'synthetic.card', 1, handle_loader=lambda _: run.handle,
                            event_recorder=lambda *_args, **_kwargs: None)['result']
    key, path = next(iter(claimed['claim_receipt']['output_paths'].items()))
    Path(path).write_text('candidate')
    value = {'schema_version': 1, 'envelope': claimed['envelope'], 'plan_hash': claimed['plan_hash'],
             'outputs': [{'artifact_id': key, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}],
             'host_receipt_id': None}
    started = Event()
    pending = []
    def abandon():
        started.set()
        return mailbox.abandon_request(run.handle.staging, 'synthetic.card', 1, reason='racing timeout')
    with ThreadPoolExecutor(max_workers=1) as pool:
        def validate(*_):
            pending.append(pool.submit(abandon))
            assert started.wait(2)
            assert not pending[0].done(), 'abandonment must wait until acceptance leaves mailbox lock'
        first = service.submit(run.run_id, value, handle_loader=lambda _: run.handle, validator=validate,
                               event_recorder=lambda *_args, **_kwargs: None)
        assert pending[0].result(timeout=2) is True
    Path(path).unlink()
    second = service.submit(run.run_id, value, handle_loader=lambda _: run.handle,
                            event_recorder=lambda *_args, **_kwargs: None)
    assert first['result']['receipt'] == second['result']['receipt']

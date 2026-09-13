from __future__ import annotations

from types import SimpleNamespace

import pytest

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.session_plan import plan_hash
from autoresearch.session_agent import artifacts, service


def _profile():
    return {
        "schema_version": 1,
        "engine": "codex",
        "session_ref": "session-main",
        "deterministic_exec": True,
        "capture_binding": True,
        "inference_handoff": True,
        "safe_resume": True,
        "independent_context": False,
        "native_dispatch": False,
        "web_search": False,
        "web_fetch": False,
        "observed_model": "subscription-session",
        "observed_effort": None,
        "evidence_refs": ["test-host"],
    }


def _request():
    return {
        "schema_version": 1,
        "kind": "stock-research",
        "requested_mode": "LITE",
        "analysis_date": "2026-09-13",
        "subject": "600519.SS",
        "peers": [],
        "asset_type": "stock",
        "name": None,
        "host_profile": _profile(),
        "predecessor_run_id": None,
    }


def _handle(tmp_path):
    workspace = tmp_path / "context_codex" / "analyze_runs" / "20260913T010203000000Z"
    staging = workspace / "staging" / "2026-09-13"
    staging.mkdir(parents=True)
    capsule = workspace / "capsule"
    (capsule / "events").mkdir(parents=True)
    contract = SimpleNamespace(contract_hash="a" * 64, run_kind="stock-research")
    return SimpleNamespace(
        workspace=workspace,
        staging=staging,
        capsule=capsule,
        engine="codex",
        run_id="20260913T010203000000Z",
        analysis_date="2026-09-13",
        contract=contract,
    )


def _planner(request, handle):
    task_one = {
        "task_id": "step.one",
        "kind": "DETERMINISTIC",
        "role": None,
        "operation": "test.noop",
        "dependencies": [],
        "input_artifact_ids": [],
        "output_artifact_ids": ["step.one.output"],
        "expected_output_contract": "test.output.v1",
        "owner": "SESSION",
        "subject": None,
        "independent_context": False,
        "parent_task": None,
    }
    task_two = {
        "task_id": "step.two",
        "kind": "INFERENCE",
        "role": "stock.card",
        "operation": None,
        "dependencies": ["step.one"],
        "input_artifact_ids": ["step.one.output"],
        "output_artifact_ids": ["step.two.output"],
        "expected_output_contract": "stock.lite.v1",
        "owner": "SESSION",
        "subject": "600519.SS",
        "independent_context": False,
        "parent_task": None,
    }
    value = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "run_kind": request["kind"],
        "requested_mode": request["requested_mode"],
        "analysis_date": request["analysis_date"],
        "orchestration_version": "session_v1",
        "input_contract_hash": handle.contract.contract_hash,
        "config_hash": sha256_bytes(canonical_json({}).encode()),
        "host_profile_hash": sha256_bytes(canonical_json(request["host_profile"]).encode()),
        "roles_hash": "d" * 64,
        "tasks": [task_one, task_two],
        "task_templates": [],
        "plan_hash": "0" * 64,
    }
    value["plan_hash"] = plan_hash(value)
    return value


def test_synthetic_workflow_runs_begin_to_finish(tmp_path):
    handle = _handle(tmp_path)
    result = service.begin(_request(), begin_capsule=lambda request: handle, planner=_planner)
    assert result["state"] == "READY"
    output_one = handle.staging / "one.txt"
    output_two = handle.staging / "two.md"
    artifacts.register_artifact(handle, "step.one.output", output_one, "WRITE")
    artifacts.register_artifact(handle, "step.two.output", output_two, "WRITE")

    first = service.next(handle.run_id, handle_loader=lambda run_id: handle)
    assert [task["task_id"] for task in first["tasks"]] == ["step.one"]
    service.claim(handle.run_id, "step.one", 1, handle_loader=lambda run_id: handle)

    def runner(*args, **kwargs):
        output_one.write_text("deterministic")
        return SimpleNamespace(exit_code=0, invocation={"status": "COMPLETED"})

    executed = service.execute(
        handle.run_id,
        "step.one",
        1,
        {"message": "ok"},
        handle_loader=lambda run_id: handle,
        runner=runner,
    )
    assert executed["state"] == "READY"
    claimed = service.claim(handle.run_id, "step.two", 1, handle_loader=lambda run_id: handle)
    assert claimed["result"]["envelope"]["role"] == "stock.card"

    output_two.write_text("**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: HOLD\n")
    digest = artifacts.bind_artifact_hash(handle, "step.two.output")["sha256"]
    submission = {
        "schema_version": 1,
        "envelope": claimed["result"]["envelope"],
        "plan_hash": claimed["result"]["plan_hash"],
        "outputs": [{"artifact_id": "step.two.output", "sha256": digest}],
        "host_receipt_id": None,
    }
    submitted = service.submit(
        handle.run_id,
        submission,
        handle_loader=lambda run_id: handle,
        validator=lambda submitted, task: None,
    )
    assert submitted["state"] == "DONE"
    calls = []
    finished = service.finish(
        handle.run_id,
        handle_loader=lambda run_id: handle,
        publisher=lambda current: calls.append(("publish", current.run_id)) or None,
        finalizer=lambda current, report: (
            calls.append(("finalize", current.run_id)) or {"ok": True}
        ),
    )
    assert finished["state"] == "DONE"
    assert calls == [("publish", handle.run_id), ("finalize", handle.run_id)]


def test_finish_rejects_incomplete_graph_without_publishing(tmp_path):
    handle = _handle(tmp_path)
    service.begin(_request(), begin_capsule=lambda request: handle, planner=_planner)
    calls = []
    with pytest.raises(RuntimeError, match="incomplete"):
        service.finish(
            handle.run_id,
            handle_loader=lambda run_id: handle,
            publisher=lambda current: calls.append(current),
        )
    assert calls == []


def test_begin_rejects_other_engine_and_frozen_request_changes(tmp_path):
    handle = _handle(tmp_path)
    wrong_engine = _request()
    wrong_engine["host_profile"] = {**wrong_engine["host_profile"], "engine": "claude"}
    with pytest.raises(ValueError, match="process engine"):
        service.begin(wrong_engine, begin_capsule=lambda request: handle, planner=_planner)

    request = _request()
    service.begin(request, begin_capsule=lambda current: handle, planner=_planner)
    changed = {**request, "name": "changed"}
    with pytest.raises(RuntimeError, match="frozen session input changed"):
        service.begin(changed, begin_capsule=lambda current: handle, planner=_planner)


def test_execute_rejects_unknown_operation(tmp_path):
    handle = _handle(tmp_path)

    def planner(request, current):
        value = _planner(request, current)
        value["tasks"] = [{**value["tasks"][0], "operation": "shell.arbitrary"}]
        value["plan_hash"] = plan_hash(value)
        return value

    service.begin(_request(), begin_capsule=lambda request: handle, planner=planner)
    service.claim(handle.run_id, "step.one", 1, handle_loader=lambda run_id: handle)
    with pytest.raises(KeyError, match="unknown operation"):
        service.execute(
            handle.run_id,
            "step.one",
            1,
            {},
            handle_loader=lambda run_id: handle,
        )

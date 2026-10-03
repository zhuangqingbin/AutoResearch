from __future__ import annotations

import gzip
import json
from types import SimpleNamespace

import pytest

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.session_plan import plan_hash
from autoresearch.session_agent import artifacts, service, store
from tests.session_agent.test_service import _handle, _request


def _deterministic_plan(request, handle, *, include_waiting=False):
    first = {
        "task_id": "step.one",
        "kind": "DETERMINISTIC",
        "role": None,
        "operation": "test.noop",
        "dependencies": [],
        "input_artifact_ids": ["step.input"],
        "output_artifact_ids": ["step.output"],
        "expected_output_contract": "test.output.v1",
        "owner": "SESSION",
        "subject": None,
        "independent_context": False,
        "parent_task": None,
    }
    tasks = [first]
    if include_waiting:
        tasks.append({
            **first,
            "task_id": "step.two",
            "dependencies": ["step.one"],
            "input_artifact_ids": ["step.output"],
            "output_artifact_ids": ["step.two.output"],
        })
    value = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "run_kind": request["kind"],
        "requested_mode": request["requested_mode"],
        "analysis_date": request["analysis_date"],
        "orchestration_version": "session_v1",
        "input_contract_hash": handle.contract.contract_hash,
        "config_hash": sha256_bytes(canonical_json({}).encode("utf-8")),
        "host_profile_hash": sha256_bytes(
            canonical_json(request["host_profile"]).encode("utf-8")
        ),
        "roles_hash": "d" * 64,
        "tasks": tasks,
        "task_templates": [],
        "plan_hash": "0" * 64,
    }
    value["plan_hash"] = plan_hash(value)
    return value


def _case(tmp_path, *, include_waiting=False, retry=False, stale=False):
    handle = _handle(tmp_path)
    input_path = handle.staging / "input.txt"
    input_path.write_text("frozen input", encoding="utf-8")

    def register(request, current, plan):
        artifacts.register_artifact(current, "step.input", input_path, "READ")
        artifacts.register_artifact(
            current,
            "step.output",
            current.staging / "output.txt",
            "WRITE",
        )
        if include_waiting:
            artifacts.register_artifact(
                current,
                "step.two.output",
                current.staging / "two.txt",
                "WRITE",
            )

    service.begin(
        _request(),
        begin_capsule=lambda request: handle,
        planner=lambda request, current: _deterministic_plan(
            request, current, include_waiting=include_waiting
        ),
        artifact_registrar=register,
    )
    service.claim(handle.run_id, "step.one", 1, handle_loader=lambda unused: handle)
    attempt = 1
    if stale:
        # The operator's hint for a deterministic orphan (runner.py): the child process
        # died with the attempt RUNNING, so no command capture was ever written.
        service.fail(
            handle.run_id,
            "step.one",
            1,
            "STALE_TASK",
            "orphaned RUNNING attempt; child process is gone",
            handle_loader=lambda unused: handle,
        )
        attempt = 2
        service.claim(
            handle.run_id, "step.one", attempt, handle_loader=lambda unused: handle
        )
    elif retry:
        store.mark_failed(
            handle.workspace / "session/tasks.json",
            "step.one",
            1,
            {"code": "TIMEOUT", "message": "first attempt failed"},
            retryable=True,
        )
        attempt = 2
        service.claim(
            handle.run_id,
            "step.one",
            attempt,
            handle_loader=lambda unused: handle,
        )
    output = handle.staging / "output.txt"
    output.write_text("deterministic output", encoding="utf-8")
    log_dir = handle.capsule / "logs/session"
    log_dir.mkdir(parents=True)
    stdout = log_dir / "session-step-one-a1.stdout.log.gz"
    stderr = log_dir / "session-step-one-a1.stderr.log.gz"
    with gzip.open(stdout, "wb") as stream:
        stream.write(b"ok\n")
    with gzip.open(stderr, "wb") as stream:
        stream.write(b"")
    invocation_id = f"session-step-one-a{attempt}"
    invocation = {
        "run_id": handle.run_id,
        "engine": handle.engine,
        "stage": "session",
        "invocation_id": invocation_id,
        "attempt": attempt,
        "subject": None,
        "argv": ["python", "-c", "print('ok')"],
        "cwd": ".",
        "started_at": "2026-09-14T12:00:00Z",
        "ended_at": "2026-09-14T12:00:01Z",
        "environment": {},
        "stdout_log": stdout.relative_to(handle.capsule).as_posix(),
        "stderr_log": stderr.relative_to(handle.capsule).as_posix(),
        "status": "COMPLETED",
        "exit_code": 0,
        "signal": None,
    }
    def runner(*args, **kwargs):
        (handle.capsule / "events/invocations.json").write_text(
            json.dumps({invocation_id: invocation}),
            encoding="utf-8",
        )
        return SimpleNamespace(exit_code=0, invocation=invocation)

    service.execute(
        handle.run_id,
        "step.one",
        attempt,
        {"message": "ok"},
        handle_loader=lambda unused: handle,
        runner=runner,
    )
    return handle


def test_complete_deterministic_task_materializes_a_closed_evidence_set(tmp_path):
    from autoresearch.session_agent.evidence import materialize_evidence

    handle = _case(tmp_path)
    closure = materialize_evidence(handle)

    assert closure["completeness_ok"] is True
    accepted = artifacts.artifact_path(handle, "step.output")
    assert accepted != artifacts.declared_path(handle, "step.output")
    assert accepted.read_bytes() == b"deterministic output"
    assert closure["required_tasks"] == 1
    assert closure["present_tasks"] == 1
    plan = json.loads(
        (handle.capsule / "evidence/evidence_plan.json").read_text(encoding="utf-8")
    )
    assert plan["task_keys"][0]["requirements"] == [
        "claim",
        "input_snapshot",
        "outputs",
        "accepted_receipt",
        "command_capture",
    ]


@pytest.mark.parametrize("leg", ["input_refs", "output_refs", "claim_ref", "receipt_ref"])
def test_missing_consumed_evidence_fails_recomputed_closure(tmp_path, leg):
    from autoresearch.session_agent.evidence import evaluate_closure, materialize_evidence

    handle = _case(tmp_path)
    materialize_evidence(handle)
    evidence_path = handle.capsule / "evidence/tasks/step.one/a1/evidence.json"
    task = json.loads(evidence_path.read_text(encoding="utf-8"))
    ref = task[leg][0] if isinstance(task[leg], list) else task[leg]
    (handle.capsule / ref["captured_path"]).unlink()

    result = evaluate_closure(handle.capsule)

    assert result["completeness_ok"] is False
    assert any("MISSING" in reason or "HASH" in reason for reason in result["missing"])


def test_unreached_planned_task_stays_in_the_evidence_denominator(tmp_path):
    from autoresearch.session_agent.evidence import build_evidence_plan

    handle = _case(tmp_path, include_waiting=True)
    plan = build_evidence_plan(handle)

    assert [(task["task_id"], task["state"]) for task in plan["task_keys"]] == [
        ("step.one", "SUCCEEDED"),
        ("step.two", "NOT_REACHED"),
    ]


def test_present_label_cannot_hide_a_missing_required_reference(tmp_path):
    from autoresearch.session_agent.evidence import evaluate_closure, materialize_evidence

    handle = _case(tmp_path)
    materialize_evidence(handle)
    evidence_path = handle.capsule / "evidence/tasks/step.one/a1/evidence.json"
    task = json.loads(evidence_path.read_text(encoding="utf-8"))
    task["claim_ref"] = None
    task["status"] = "PRESENT"
    task["reasons"] = []
    evidence_path.write_text(json.dumps(task), encoding="utf-8")

    result = evaluate_closure(handle.capsule)

    assert result["completeness_ok"] is False
    assert "CLAIM_MISSING:step.one:a1" in result["missing"]


def test_failed_attempt_remains_in_denominator_after_retry_succeeds(tmp_path):
    from autoresearch.session_agent.evidence import materialize_evidence

    handle = _case(tmp_path, retry=True)
    materialize_evidence(handle)
    plan = json.loads(
        (handle.capsule / "evidence/evidence_plan.json").read_text(encoding="utf-8")
    )
    first = json.loads(
        (handle.capsule / "evidence/tasks/step.one/a1/evidence.json").read_text(
            encoding="utf-8"
        )
    )

    assert [(key["attempt"], key["state"]) for key in plan["task_keys"]] == [
        (1, "SUPERSEDED"),
        (2, "SUCCEEDED"),
    ]
    assert first["claim_ref"] is not None


def test_operator_stale_orphan_needs_no_command_capture_it_never_got(
    tmp_path, monkeypatch
):
    """N3 (small): ``fail --error-class STALE_TASK`` on a deterministic orphan records
    why a1 has no command capture; the closure keeps the attempt (claim + inputs) but
    stops requiring a capture that cannot exist — like an abandoned inference attempt."""
    from autoresearch.common import workspace as ws
    from autoresearch.session_agent.evidence import materialize_evidence

    monkeypatch.setattr(ws, "ENGINE", "codex")
    handle = _case(tmp_path, stale=True)
    closure = materialize_evidence(handle)
    plan = json.loads(
        (handle.capsule / "evidence/evidence_plan.json").read_text(encoding="utf-8")
    )
    first, second = plan["task_keys"]

    assert (first["attempt"], first["state"]) == (1, "SUPERSEDED")
    assert first["evidence_kind"] == "STALE_ORPHAN"
    assert first["requirements"] == ["claim", "input_snapshot"]
    assert "evidence_kind" not in second
    assert "command_capture" in second["requirements"]
    assert closure["completeness_ok"] is True, closure["missing"]
    recorded = json.loads(
        (handle.capsule / "evidence/tasks/step.one/a1/failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert recorded["error"]["code"] == "STALE_TASK"            # the reason travels


def test_a_non_stale_deterministic_failure_still_needs_its_command_capture(
    tmp_path, monkeypatch
):
    from autoresearch.common import workspace as ws
    from autoresearch.session_agent.evidence import materialize_evidence

    monkeypatch.setattr(ws, "ENGINE", "codex")
    handle = _case(tmp_path, retry=True)
    closure = materialize_evidence(handle)
    plan = json.loads(
        (handle.capsule / "evidence/evidence_plan.json").read_text(encoding="utf-8")
    )

    assert "evidence_kind" not in plan["task_keys"][0]
    assert "command_capture" in plan["task_keys"][0]["requirements"]
    assert "COMMAND_CAPTURE_MISSING:step.one:a1" in closure["missing"]

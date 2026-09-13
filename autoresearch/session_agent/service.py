"""Application service for subscription-session research task graphs."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import (
    atomic_write_json,
    canonical_json,
    sha256_bytes,
    sha256_file,
)
from autoresearch.contracts.session_task import (
    validate_begin_request,
    validate_submission,
    validate_tool_result,
)
from autoresearch.session_agent import artifacts, executor, plan as plan_service, store
from autoresearch.session_agent.hosts.base import render_request, validate_receipt
from autoresearch.session_agent.publication import publish as publish_run, session_profile
from autoresearch.session_agent.roles import role_manifest, role_stage
from autoresearch.session_agent.validation import validate_submission_outputs


def _session_dir(handle) -> Path:
    return Path(handle.workspace) / "session"


def _plan_path(handle) -> Path:
    return _session_dir(handle) / "plan.json"


def _store_path(handle) -> Path:
    return _session_dir(handle) / "tasks.json"


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _freeze_json(path: Path, value: dict) -> Path:
    if path.is_file():
        current = _read_json(path)
        if canonical_json(current) != canonical_json(value):
            raise RuntimeError(f"frozen session input changed: {path.name}")
        return path
    return atomic_write_json(path, value)


def _mirror_identity(handle, name: str, value: dict) -> None:
    _freeze_json(_session_dir(handle) / name, value)
    _freeze_json(Path(handle.capsule) / "identity" / "session" / name, value)


def _load_plan(handle) -> dict:
    value = _read_json(_plan_path(handle))
    from autoresearch.contracts.session_plan import validate_plan

    return validate_plan(value)


def _all_tasks(handle, frozen_plan: dict | None = None) -> list[dict]:
    current = frozen_plan or _load_plan(handle)
    tasks = list(current["tasks"])
    root = _session_dir(handle) / "expansions"
    if root.is_dir():
        for path in sorted(root.glob("*.json")):
            expansion = _read_json(path)
            tasks = plan_service.apply_expansion({**current, "tasks": tasks}, expansion)
    return tasks


def _task(handle, task_id: str) -> dict:
    matches = [task for task in _all_tasks(handle) if task["task_id"] == task_id]
    if len(matches) != 1:
        raise KeyError(task_id)
    return matches[0]


def _result(
    command: str,
    run_id: str,
    state: str,
    *,
    tasks: list[dict] | None = None,
    result: object = None,
    errors: list[dict] | None = None,
) -> dict:
    value = {
        "schema_version": 1,
        "command": command,
        "run_id": run_id,
        "state": state,
        "tasks": tasks or [],
        "result": result,
        "errors": errors or [],
    }
    return validate_tool_result(value)


def _default_begin_capsule(request: dict):
    from autoresearch.trace.capsule import begin_run

    kind = request["kind"]
    config = None
    bootstrap = None
    if kind == "stock-research":
        from autoresearch.analyze.run_bootstrap import prepare_analyze_run

        config = {
            "mode": request["requested_mode"],
            "ticker": request["subject"],
            "peers": request["peers"],
            "asset_type": request["asset_type"],
            "name": request["name"],
        }
        bootstrap = prepare_analyze_run
    elif kind != "scan-market":
        raise ValueError(f"run kind is not registered with workspace yet: {kind}")
    return begin_run(
        kind,
        request["analysis_date"],
        request["host_profile"]["engine"],
        config,
        session_ref=request["host_profile"]["session_ref"],
        bootstrap=bootstrap,
    )


def _default_planner(request: dict, handle) -> dict:
    from autoresearch.session_agent.workflows import build_plan

    return build_plan(request, handle)


def _predecessor_evidence(request: dict, loader=None) -> dict | None:
    predecessor_id = request["predecessor_run_id"]
    if predecessor_id is None:
        return None
    if loader is None:
        from autoresearch.trace.capsule import load_run

        loader = load_run
    predecessor = loader(predecessor_id)
    if predecessor.engine != ws.ENGINE:
        raise ValueError("predecessor engine does not match current engine")
    state_path = Path(getattr(predecessor, "workspace", "")) / "state.json"
    if state_path.is_file():
        state = _read_json(state_path)
        business_status = str(state.get("business_status") or "")
    else:
        business_status = str(getattr(predecessor, "business_status", ""))
    if business_status == "ACTIVE" or not business_status:
        raise ValueError("predecessor must be a terminal run")
    return {
        "schema_version": 1,
        "run_id": predecessor_id,
        "engine": predecessor.engine,
        "business_status": business_status,
    }


def begin(
    request: dict,
    *,
    begin_capsule: Callable[[dict], object] | None = None,
    planner: Callable[[dict, object], dict] | None = None,
    predecessor_loader=None,
) -> dict:
    """Validate and freeze a request before exposing its first ready task."""
    validate_begin_request(request, expected_engine=ws.ENGINE)
    predecessor = _predecessor_evidence(request, predecessor_loader)
    handle = (begin_capsule or _default_begin_capsule)(request)
    if handle.engine != ws.ENGINE:
        raise ValueError("capsule engine does not match process engine")
    _mirror_identity(handle, "request.json", request)
    _mirror_identity(handle, "host_profile.json", request["host_profile"])
    if predecessor is not None:
        _mirror_identity(handle, "predecessor.json", predecessor)
    frozen_plan = (planner or _default_planner)(request, handle)
    plan_service.freeze_plan(_plan_path(handle), frozen_plan)
    _freeze_json(
        Path(handle.capsule) / "identity" / "session" / "plan.json", frozen_plan
    )
    role_ids = [
        task["role"]
        for task in frozen_plan["tasks"]
        if task["kind"] == "INFERENCE"
    ]
    _freeze_json(
        Path(handle.capsule) / "identity" / "session" / "roles.json",
        role_manifest(role_ids),
    )
    store.initialize(_store_path(handle), frozen_plan)
    return status(handle.run_id, handle_loader=lambda unused: handle, command="begin")


def _state(handle) -> tuple[str, list[dict], list[dict]]:
    frozen_plan = _load_plan(handle)
    tasks = _all_tasks(handle, frozen_plan)
    states = store.read_states(_store_path(handle))
    ready = plan_service.ready_tasks(tasks, states)
    if ready:
        return "READY", ready, []
    blocked = [task for task in tasks if states.get(task["task_id"]) in {"BLOCKED", "FAILED"}]
    if blocked:
        return (
            "BLOCKED",
            [],
            [{"code": "TASK_BLOCKED", "task_id": task["task_id"]} for task in blocked],
        )
    session_tasks = [task for task in tasks if task["owner"] == "SESSION"]
    if session_tasks and all(states.get(task["task_id"]) == "SUCCEEDED" for task in session_tasks):
        expanded = {
            _read_json(path)["template_id"]
            for path in sorted((_session_dir(handle) / "expansions").glob("*.json"))
        }
        missing = [
            template["template_id"]
            for template in frozen_plan["task_templates"]
            if template["template_id"] not in expanded
        ]
        if not missing:
            return "DONE", [], []
        return (
            "WAITING",
            [],
            [{"code": "EXPANSION_PENDING", "template_id": item} for item in missing],
        )
    running = [task for task in session_tasks if states.get(task["task_id"]) == "RUNNING"]
    return "WAITING", running, []


def status(
    run_id: str,
    *,
    handle_loader: Callable[[str], object] | None = None,
    command: str = "status",
) -> dict:
    from autoresearch.trace.capsule import require_active_run

    handle = (handle_loader or require_active_run)(run_id)
    state, tasks, errors = _state(handle)
    return _result(command, handle.run_id, state, tasks=tasks, errors=errors)


def next(run_id: str, *, handle_loader: Callable[[str], object] | None = None) -> dict:
    return status(run_id, handle_loader=handle_loader, command="next")


def _subject_kwargs(task: dict) -> dict:
    subject = task.get("subject")
    if subject is None or re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", subject
    ):
        return {"subject": subject}
    return {"subject_display": subject}


def _boundary_recorder(recorder):
    if recorder is not None:
        return recorder
    from autoresearch.trace.capsule import record_agent_boundary

    return record_agent_boundary


def _record_completion(handle, task: dict, attempt: int, payload: dict, recorder) -> None:
    _boundary_recorder(recorder)(
        handle.run_id,
        "AGENT_COMPLETED",
        role=task["role"],
        invocation_id=f"session-{task['task_id']}-a{attempt}",
        attempt=attempt,
        result=payload,
        stage=role_stage(task["role"]),
        **_subject_kwargs(task),
    )


def claim(
    run_id: str,
    task_id: str,
    expected_attempt: int,
    *,
    handle_loader: Callable[[str], object] | None = None,
    event_recorder=None,
) -> dict:
    from autoresearch.trace.capsule import require_active_run

    handle = (handle_loader or require_active_run)(run_id)
    task = _task(handle, task_id)
    host_profile = _read_json(_session_dir(handle) / "host_profile.json")
    receipt = store.claim(
        _store_path(handle), task_id, expected_attempt, host_profile["session_ref"]
    )
    claim_result: dict = {"claim_receipt": receipt}
    if task["kind"] == "INFERENCE":
        envelope = {
            "schema_version": 1,
            "engine": handle.engine,
            "run_id": handle.run_id,
            "task_id": task["task_id"],
            "role": task["role"],
            "input_artifact_ids": task["input_artifact_ids"],
            "input_contract_hash": handle.contract.contract_hash,
            "expected_output_contract": task["expected_output_contract"],
            "attempt": expected_attempt,
        }
        claim_result.update(
            {
                "envelope": envelope,
                "plan_hash": _load_plan(handle)["plan_hash"],
                "request": render_request(task, host_profile),
            }
        )
        invocation_id = f"session-{task['task_id']}-a{expected_attempt}"
        handoff = {
            "schema_version": 1,
            "envelope": envelope,
            "plan_hash": claim_result["plan_hash"],
            "request": claim_result["request"],
            "claim_receipt": receipt,
        }
        request_path = (
            Path(handle.capsule)
            / "agents/session/requests"
            / f"{invocation_id}.json"
        )
        _freeze_json(request_path, handoff)
        _boundary_recorder(event_recorder)(
            handle.run_id,
            "AGENT_DISPATCHED",
            role=task["role"],
            invocation_id=invocation_id,
            attempt=expected_attempt,
            result={
                "request_ref": request_path.relative_to(handle.capsule).as_posix(),
                "request_sha256": sha256_file(request_path),
            },
            stage=role_stage(task["role"]),
            **_subject_kwargs(task),
        )
    return _result("claim", handle.run_id, "WAITING", result=claim_result)


def execute(
    run_id: str,
    task_id: str,
    attempt: int,
    params: dict,
    *,
    handle_loader: Callable[[str], object] | None = None,
    runner=None,
) -> dict:
    from autoresearch.trace.capsule import require_active_run

    handle = (handle_loader or require_active_run)(run_id)
    task = _task(handle, task_id)
    if task["kind"] != "DETERMINISTIC":
        raise ValueError("execute requires a deterministic task")
    kwargs = {} if runner is None else {"runner": runner}
    execution = executor.execute_operation(handle, task, attempt, params, **kwargs)
    if execution["status"] != "SUCCEEDED":
        store.mark_failed(
            _store_path(handle),
            task_id,
            attempt,
            {"code": "OPERATION_FAILED", "execution": execution},
            retryable=bool(executor.operation_spec(task["operation"])["idempotent"]),
        )
        return status(run_id, handle_loader=lambda unused: handle, command="execute")
    outputs = []
    for artifact_id in task["output_artifact_ids"]:
        descriptor = artifacts.bind_artifact_hash(handle, artifact_id)
        outputs.append({"artifact_id": artifact_id, "sha256": descriptor["sha256"]})
    receipt = store.complete_deterministic(
        _store_path(handle), task_id, attempt, outputs, execution
    )
    current = status(run_id, handle_loader=lambda unused: handle, command="execute")
    current["result"] = {"execution": execution, "receipt": receipt}
    return current


def submit(
    run_id: str,
    submission: dict,
    *,
    handle_loader: Callable[[str], object] | None = None,
    validator: Callable[[dict, dict], object] | None = None,
    host_receipt: dict | None = None,
    event_recorder=None,
) -> dict:
    from autoresearch.trace.capsule import require_active_run

    validate_submission(submission)
    handle = (handle_loader or require_active_run)(run_id)
    if submission["envelope"]["run_id"] != handle.run_id:
        raise ValueError("submission run_id does not match command run_id")
    task = _task(handle, submission["envelope"]["task_id"])
    if task["kind"] != "INFERENCE":
        raise ValueError("submit requires an inference task")
    host_profile = _read_json(_session_dir(handle) / "host_profile.json")
    if host_receipt is not None:
        validate_receipt(task, host_receipt, host_profile)
        if host_receipt["attempt"] != submission["envelope"]["attempt"]:
            raise ValueError("host receipt attempt mismatch")
        host_receipt_id = sha256_bytes(canonical_json(host_receipt).encode("utf-8"))
        if submission["host_receipt_id"] != host_receipt_id:
            raise ValueError("host_receipt_id does not match the verified receipt")
        _freeze_json(
            _session_dir(handle) / "receipts" / f"{host_receipt_id}.json",
            host_receipt,
        )
        _freeze_json(
            Path(handle.capsule)
            / "agents/session/host_receipts"
            / f"{host_receipt_id}.json",
            host_receipt,
        )
    elif submission["host_receipt_id"] is not None:
        raise ValueError("host_receipt_id requires the matching host receipt")
    elif task["independent_context"]:
        raise ValueError("independent task requires a verified host receipt")

    def checked(value: dict, spec: dict) -> None:
        validate_submission_outputs(handle, value, spec, domain_validator=validator)

    receipt = store.accept(_store_path(handle), submission, checked)
    receipt_path = (
        Path(handle.capsule)
        / "agents/session/receipts"
        / f"{receipt['receipt_id']}.json"
    )
    _freeze_json(receipt_path, receipt)
    attempt = submission["envelope"]["attempt"]
    completion = {
        "receipt_ref": receipt_path.relative_to(handle.capsule).as_posix(),
        "receipt_id": receipt["receipt_id"],
        "outputs": submission["outputs"],
        "host_receipt_id": submission["host_receipt_id"],
    }
    completion_path = (
        Path(handle.capsule)
        / "agents/session/completions"
        / f"{task['task_id']}-a{attempt}.json"
    )
    _freeze_json(completion_path, completion)
    _record_completion(handle, task, attempt, completion, event_recorder)
    current = status(run_id, handle_loader=lambda unused: handle, command="submit")
    current["result"] = {"receipt": receipt}
    return current


def resume(
    run_id: str,
    *,
    handle_loader: Callable[[str], object] | None = None,
    event_recorder=None,
) -> dict:
    from autoresearch.trace.capsule import require_active_run

    handle = (handle_loader or require_active_run)(run_id)
    states = store.read_states(_store_path(handle))
    recovered = []
    running = []
    for task in _all_tasks(handle):
        state = states.get(task["task_id"])
        if state == "SUCCEEDED":
            receipt = store.recover_receipt(_store_path(handle), task["task_id"])
            if receipt is not None:
                recovered.append(receipt)
                if task["kind"] == "INFERENCE":
                    completion_path = (
                        Path(handle.capsule)
                        / "agents/session/completions"
                        / f"{task['task_id']}-a{receipt['attempt']}.json"
                    )
                    if completion_path.is_file():
                        _record_completion(
                            handle,
                            task,
                            receipt["attempt"],
                            _read_json(completion_path),
                            event_recorder,
                        )
        elif state == "RUNNING" and task["kind"] == "DETERMINISTIC":
            running.append(executor.probe_execution(handle, task["task_id"], 1))
    current = status(run_id, handle_loader=lambda unused: handle, command="resume")
    current["result"] = {"recovered_receipts": recovered, "running": running}
    return current


def _default_finalizer(handle, report):
    from autoresearch.trace.capsule import BusinessStatus, finalize

    report_dir = report if isinstance(report, (str, Path)) else None
    return finalize(
        handle.run_id,
        BusinessStatus.SUCCEEDED,
        report_dir=report_dir,
        profile=session_profile(handle),
    )


def finish(
    run_id: str,
    *,
    handle_loader: Callable[[str], object] | None = None,
    publisher: Callable[[object], object] | None = None,
    finalizer: Callable[[object, object], object] | None = None,
) -> dict:
    from autoresearch.trace.capsule import require_active_run

    handle = (handle_loader or require_active_run)(run_id)
    current = status(run_id, handle_loader=lambda unused: handle)
    if current["state"] != "DONE":
        raise RuntimeError("cannot finish an incomplete task graph")
    report = (publisher or publish_run)(handle)
    finalized = (finalizer or _default_finalizer)(handle, report)
    return _result(
        "finish",
        handle.run_id,
        "DONE",
        result={"publication": report, "finalization": finalized},
    )


__all__ = ["begin", "claim", "execute", "finish", "next", "resume", "status", "submit"]

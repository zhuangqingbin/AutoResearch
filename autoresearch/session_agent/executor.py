"""Bind static operations to the existing forensic command capture."""
from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

from autoresearch.session_agent.operations import build_argv, operation_spec
from autoresearch.trace import process_probe
from autoresearch.trace.exec_capture import run_captured


class OperationRunning(RuntimeError):
    """A matching invocation still has live ownership evidence."""


def _invocation_id(task_id: str, attempt: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", task_id)
    return f"session-{safe}-a{attempt}"


def probe_execution(handle, task_id: str, attempt: int) -> dict:
    invocation_id = _invocation_id(task_id, attempt)
    index_path = Path(handle.capsule) / "events" / "invocations.json"
    if not index_path.is_file():
        return {"state": "NOT_FOUND", "invocation_id": invocation_id}
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    invocation = payload.get(invocation_id)
    if not isinstance(invocation, dict):
        return {"state": "NOT_FOUND", "invocation_id": invocation_id}
    status = str(invocation.get("status") or "UNKNOWN")
    lease = invocation.get("lease") or invocation.get("process")
    if status in {"STARTING", "RUNNING"} and process_probe.matches(lease):
        state = "RUNNING"
    elif status == "COMPLETED":
        state = "SUCCEEDED"
    elif status == "FAILED":
        state = "FAILED"
    else:
        state = "UNKNOWN"
    return {"state": state, "invocation_id": invocation_id, "invocation": invocation}


def execute_operation(
    handle,
    task: dict,
    attempt: int,
    params: dict,
    *,
    runner=run_captured,
    owner_callback=None,
) -> dict:
    if task.get("kind") != "DETERMINISTIC":
        raise ValueError("execute only accepts deterministic tasks")
    if type(attempt) is not int or attempt < 1:
        raise ValueError("invalid attempt")
    current = probe_execution(handle, task["task_id"], attempt)
    if current["state"] == "RUNNING":
        raise OperationRunning(
            f"operation is still running: {current.get('invocation_id', task['task_id'])}"
        )
    if current["state"] in {"SUCCEEDED", "FAILED"}:
        raise RuntimeError("operation attempt already has terminal capture evidence")
    operation = task["operation"]
    argv = build_argv(operation, params, subject=task.get("subject"))
    from autoresearch.session_agent.evidence import freeze_operation_request

    freeze_operation_request(handle, task, attempt, params)
    prepared = None
    kwargs = {}
    if operation == "research.card.facts" and runner is run_captured:
        from autoresearch.common.atomic import sha256_file
        from autoresearch.session_agent.card_facts import prepare_projection

        path = prepare_projection(handle, task, attempt)
        prepared = {"ref": path.relative_to(handle.capsule).as_posix(), "sha256": sha256_file(path)}
        argv = [sys.executable, "-m", "autoresearch.session_agent.card_facts",
                "--prepared-request", str(path), "--sha256", prepared["sha256"]]
        kwargs["owner_callback"] = owner_callback
    spec = operation_spec(operation)
    invocation_id = _invocation_id(task["task_id"], attempt)
    result = runner(
        handle,
        str(spec["stage"]),
        argv,
        invocation_id,
        attempt,
        task.get("subject"),
        task_id=task["task_id"],
        **kwargs,
    )
    if result.invocation.get("forwarded_signals"):
        # Captured children have already been reaped and handlers restored.
        # Owner-thread execution must stop the runner, including exclusive ops.
        raise KeyboardInterrupt("deterministic command interrupted")
    execution = {
        "schema_version": 1,
        "operation": operation,
        "invocation_id": invocation_id,
        "attempt": attempt,
        "argv": argv,
        "status": "SUCCEEDED" if result.exit_code == 0 else "FAILED",
        "exit_code": result.exit_code,
        "capture_status": result.invocation.get("status"),
    }

    if prepared is not None:
        execution["prepared_request"] = prepared
        if result.exit_code == 0:
            with gzip.open(Path(handle.capsule) / result.invocation["stdout_log"], "rt") as stream:
                execution["prepared_output"] = json.load(stream)
    return execution


__all__ = [
    "OperationRunning", "build_argv", "execute_operation", "probe_execution",
]

"""Narrow adapter to the existing scan L4 taskbook owner."""
from __future__ import annotations

import re
from pathlib import Path

from autoresearch.scan import l4_tasks


def taskbook_path(handle) -> Path:
    return Path(handle.staging) / "_l4_tasks.json"


def initialize_tickets(
    handle,
    codes: list[str],
    *,
    meta: dict[str, dict] | None = None,
    caps: dict | None = None,
) -> dict:
    result = l4_tasks.initialize(
        handle.analysis_date,
        codes,
        root=Path(handle.staging).parent,
        context_root=Path(handle.staging) / "_external_inputs",
        meta=meta,
        caps=caps,
    )
    if not result.get("ok"):
        raise RuntimeError(str(result.get("reason") or "L4 taskbook initialization failed"))
    return result


def claim_ticket(handle, code: str, expected_attempt: int) -> dict:
    value = l4_tasks.preflight(
        taskbook_path(handle), code, expected_attempt=expected_attempt
    )
    if value.get("action") in {"WAIT", "BLOCKED"}:
        raise RuntimeError(
            f"parent ticket {code} is {value.get('action')}: {value.get('reason')}"
        )
    return value


def _payload(handle) -> dict:
    _, value = l4_tasks._read(taskbook_path(handle))
    return value


def ticket_states(handle, owner_tasks: list[dict] | None = None) -> dict[str, str]:
    payload = _payload(handle)
    if owner_tasks is None:
        owner_tasks = [
            {
                "task_id": f"l4.{code}.a{max(1, int(task.get('attempt') or 0))}",
                "subject": code,
                "parent_task": None,
            }
            for code, task in payload["tasks"].items()
        ]
    defined_attempts: dict[str, int] = {}
    for spec in owner_tasks:
        code = str(spec["subject"])
        match = re.fullmatch(rf"l4\.{code}\.a(\d+)", spec["task_id"])
        if match:
            defined_attempts[code] = max(
                defined_attempts.get(code, 0), int(match.group(1))
            )
    result = {}
    for spec in owner_tasks:
        code = str(spec["subject"])
        task = payload["tasks"].get(code)
        if task is None:
            result[spec["task_id"]] = "PENDING"
            continue
        match = re.fullmatch(rf"l4\.{code}\.a(\d+)", spec["task_id"])
        spec_attempt = int(match.group(1)) if match else 1
        current_attempt = int(task.get("attempt") or 0)
        if spec_attempt < defined_attempts.get(code, spec_attempt):
            result[spec["task_id"]] = "SUPERSEDED"
        elif current_attempt < spec_attempt:
            result[spec["task_id"]] = "PENDING"
        elif current_attempt > spec_attempt:
            result[spec["task_id"]] = "SUPERSEDED"
        else:
            result[spec["task_id"]] = str(task.get("status") or "PENDING")
    return result


def validate_parent(handle, parent: dict) -> dict:
    if parent.get("owner") != "L4_TASKBOOK":
        raise ValueError("parent ticket owner mismatch")
    code = str(parent.get("subject") or "")
    expected_attempt = parent.get("attempt")
    task = _payload(handle)["tasks"].get(code)
    if (
        task is None
        or task.get("status") != "RUNNING"
        or task.get("attempt") != expected_attempt
    ):
        raise ValueError(
            f"parent ticket is not RUNNING at expected attempt: {code}/a{expected_attempt}"
        )
    return task


def verify_child_handoff(handle, task: dict, envelope: dict) -> dict:
    from autoresearch.scan.deterministic_runner import verify_handoff

    parent = task.get("parent_task")
    authoritative = validate_parent(handle, parent)
    mapped = {**envelope, "task_id": parent["subject"]}
    return verify_handoff(
        mapped,
        engine=handle.engine,
        run_id=handle.run_id,
        task=authoritative,
        input_hash=handle.contract.contract_hash,
        available_artifacts=set(task["input_artifact_ids"]),
        registered_contracts={
            (task["role"], task["expected_output_contract"])
        },
    )


def prepare_slim(handle, code: str) -> dict:
    return l4_tasks.prepare_slim(taskbook_path(handle), code)


def complete_ticket(handle, code: str, attempt: int) -> dict:
    return l4_tasks.mark_success(
        taskbook_path(handle), code, expected_attempt=attempt
    )


def fail_ticket(handle, code: str, attempt: int, error_class: str, error: str) -> dict:
    return l4_tasks.mark_failure(
        taskbook_path(handle),
        code,
        error_class,
        error=error,
        expected_attempt=attempt,
    )


__all__ = [
    "claim_ticket",
    "complete_ticket",
    "fail_ticket",
    "initialize_tickets",
    "prepare_slim",
    "taskbook_path",
    "ticket_states",
    "validate_parent",
    "verify_child_handoff",
]

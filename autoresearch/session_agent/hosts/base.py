"""Host-neutral request and receipt validation."""
from __future__ import annotations

from autoresearch.contracts.session_task import (
    require_exact_fields,
    require_version,
    validate_host_profile,
    validate_task,
)
from autoresearch.session_agent.roles import get_role

RECEIPT_FIELDS = frozenset({
    "schema_version", "engine", "session_ref", "context_ref", "parent_context_ref",
    "task_id", "attempt", "completed", "evidence_refs",
})


class HostCapabilityError(RuntimeError):
    """The current official session cannot honestly perform a requested task."""


def observe_host(value: dict) -> dict:
    validate_host_profile(value)
    if any(
        value[field] is True
        for field in (
            "deterministic_exec", "capture_binding", "inference_handoff", "safe_resume",
            "independent_context", "native_dispatch", "web_search", "web_fetch",
        )
    ) and not value["evidence_refs"]:
        raise ValueError("true host capabilities require evidence_refs")
    return value


def render_request(task: dict, host_profile: dict) -> dict:
    validate_task(task)
    observe_host(host_profile)
    if task["kind"] != "INFERENCE":
        raise ValueError("only inference tasks render host requests")
    if host_profile["inference_handoff"] is not True:
        raise HostCapabilityError("inference_handoff capability is unavailable or unknown")
    if task["independent_context"] and host_profile["independent_context"] is not True:
        raise HostCapabilityError("independent_context capability is unavailable or unknown")
    role = get_role(task["role"])
    if role["output_contract"] != task["expected_output_contract"]:
        raise ValueError("role output contract does not match task")
    return {
        "schema_version": 1,
        "task_id": task["task_id"],
        "role": task["role"],
        "instruction_refs": role["instruction_refs"],
        "input_artifact_ids": task["input_artifact_ids"],
        "output_artifact_ids": task["output_artifact_ids"],
        "expected_output_contract": task["expected_output_contract"],
        "independent_context": task["independent_context"],
        "tool_policy": role["tool_policy"],
        "host": {
            "engine": host_profile["engine"],
            "session_ref": host_profile["session_ref"],
        },
    }


def validate_receipt(task: dict, receipt: dict, host_profile: dict) -> dict:
    validate_task(task)
    observe_host(host_profile)
    require_exact_fields(receipt, RECEIPT_FIELDS)
    require_version(receipt["schema_version"])
    if receipt["engine"] != host_profile["engine"]:
        raise ValueError("receipt engine does not match host")
    if receipt["task_id"] != task["task_id"]:
        raise ValueError("receipt task identity mismatch")
    if type(receipt["attempt"]) is not int or receipt["attempt"] < 1:
        raise ValueError("invalid receipt attempt")
    if receipt["completed"] is not True:
        raise ValueError("receipt is not completed")
    for field in ("session_ref", "context_ref"):
        if type(receipt[field]) is not str or not receipt[field]:
            raise ValueError(f"receipt {field} required")
    parent = receipt["parent_context_ref"]
    if parent is not None and (type(parent) is not str or not parent):
        raise ValueError("invalid parent_context_ref")
    evidence = receipt["evidence_refs"]
    if not isinstance(evidence, list) or not evidence or any(
        type(item) is not str or not item for item in evidence
    ):
        raise ValueError("receipt evidence_refs required")
    if task["independent_context"]:
        if host_profile["independent_context"] is not True:
            raise ValueError("host did not support independent context")
        if parent is None or parent == receipt["context_ref"]:
            raise ValueError("independent receipt must prove a different context")
    return receipt


__all__ = [
    "HostCapabilityError", "RECEIPT_FIELDS", "observe_host", "render_request",
    "validate_receipt",
]

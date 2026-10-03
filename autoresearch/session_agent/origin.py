"""Generate and verify evidence of the orchestration that actually started a run."""

from __future__ import annotations

import json
from pathlib import Path

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.session_plan import validate_plan
from autoresearch.session_agent.hosts.base import HostCapabilityError, observe_host

_SESSION_ENTRYPOINT = "autoresearch.session_agent.begin"
_REQUIRED_SESSION_CAPABILITIES = (
    "deterministic_exec",
    "capture_binding",
    "inference_handoff",
)


class EntrypointSelectionError(RuntimeError):
    """A stable CLI error for an explicitly selected but separate entrypoint."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def preflight_session_host(request: dict) -> dict:
    """Reject a session plan before allocating a run when its host cannot drive it."""
    profile = observe_host(request["host_profile"])
    missing = [name for name in _REQUIRED_SESSION_CAPABILITIES if profile[name] is not True]
    if missing:
        raise HostCapabilityError(
            "required session capability unavailable or unknown: " + ", ".join(missing)
        )
    return profile


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def freeze_session_origin(handle, request: dict, plan: dict) -> dict:
    """Bind the actual validated plan and observed host profile to one run."""
    checked_plan = validate_plan(plan)
    profile = preflight_session_host(request)
    expected_host_hash = sha256_bytes(canonical_json(profile).encode("utf-8"))
    if checked_plan["engine"] != handle.engine or checked_plan["run_id"] != handle.run_id:
        raise ValueError("plan identity does not match run")
    if checked_plan["run_kind"] != handle.contract.run_kind:
        raise ValueError("plan run_kind does not match run")
    if checked_plan["host_profile_hash"] != expected_host_hash:
        raise ValueError("plan host_profile_hash does not match observed host")

    from autoresearch.trace.capsule import freeze_execution_origin

    return freeze_execution_origin(
        handle,
        {
            "schema_version": 1,
            "engine": handle.engine,
            "run_id": handle.run_id,
            "run_kind": handle.contract.run_kind,
            "orchestration": "session_v1",
            "entrypoint": _SESSION_ENTRYPOINT,
            "plan_hash": checked_plan["plan_hash"],
            "host_profile_hash": expected_host_hash,
            "legacy_reason": None,
        },
    )


def verify_execution_origin(handle) -> dict:
    """Recompute session origin links instead of trusting an orchestration label."""
    missing: list[str] = []
    path = Path(handle.capsule) / "identity/execution_origin.json"
    try:
        from autoresearch.contracts.forensic import validate_execution_origin

        value = validate_execution_origin(_read_json(path))
    except Exception as exc:
        return {"verified": False, "missing": [f"EXECUTION_ORIGIN_INVALID:{exc}"]}

    if (
        value["engine"] != handle.engine
        or value["run_id"] != handle.run_id
        or value["run_kind"] != handle.contract.run_kind
    ):
        missing.append("EXECUTION_ORIGIN_IDENTITY_MISMATCH")
    if value["orchestration"] == "session_v1":
        try:
            plan = validate_plan(_read_json(Path(handle.workspace) / "session/plan.json"))
            profile = observe_host(
                _read_json(Path(handle.workspace) / "session/host_profile.json")
            )
        except Exception as exc:
            missing.append(f"SESSION_ORIGIN_REFERENCE_INVALID:{exc}")
        else:
            if value["entrypoint"] != _SESSION_ENTRYPOINT:
                missing.append("SESSION_ENTRYPOINT_MISMATCH")
            if value["plan_hash"] != plan["plan_hash"]:
                missing.append("SESSION_PLAN_HASH_MISMATCH")
            host_hash = sha256_bytes(canonical_json(profile).encode("utf-8"))
            if value["host_profile_hash"] != host_hash:
                missing.append("SESSION_HOST_PROFILE_HASH_MISMATCH")
            if plan["host_profile_hash"] != host_hash:
                missing.append("PLAN_HOST_PROFILE_HASH_MISMATCH")
    return {"verified": not missing, "missing": missing}


def begin_via_entry(
    request: dict,
    *,
    orchestration: str,
    legacy_reason: str | None = None,
    executor: str = "mailbox",
) -> dict:
    """Select the session entry without ever invoking a legacy workflow implicitly."""
    if orchestration == "session_v1":
        if legacy_reason is not None:
            raise ValueError("legacy_reason is only valid with legacy orchestration")
        from autoresearch.session_agent import service

        return service.begin(request, executor=executor)
    if orchestration != "legacy":
        raise ValueError(f"unknown orchestration: {orchestration!r}")
    if not str(legacy_reason or "").strip():
        raise ValueError("legacy_reason is required for legacy orchestration")
    raise EntrypointSelectionError(
        "LEGACY_ENTRYPOINT_REQUIRED",
        "session_agent does not execute legacy workflows; use the explicit workflow "
        "legacy entrypoint",
    )


__all__ = [
    "EntrypointSelectionError",
    "begin_via_entry",
    "freeze_session_origin",
    "preflight_session_host",
    "verify_execution_origin",
]

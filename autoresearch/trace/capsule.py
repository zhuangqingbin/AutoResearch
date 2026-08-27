#!/usr/bin/env python3
"""Forensic run spool lifecycle and its small operator CLI."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.scan.artifacts import CRITICAL_ARTIFACTS, ArtifactSpec
from autoresearch.scan.run_contract import load_run_contract, write_run_contract
from autoresearch.trace.atomic import (
    atomic_write_json,
    canonical_json,
    sha256_file,
)
from autoresearch.trace.capsule_models import (
    BusinessStatus,
    Checkpoint,
    EvidenceStatus,
    Replayability,
    RunHandle,
    RunState,
)
from autoresearch.trace.events import (
    append_event,
    append_guarded_event,
    verify_event_chain,
)
from autoresearch.trace.identity import redact_value, scan_for_secrets, snapshot_identity

_STAGE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$", re.ASCII)
_AGENT_EVENT_TYPES = frozenset(
    {"AGENT_DISPATCHED", "AGENT_COMPLETED", "AGENT_FAILED"}
)
_UTC_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
_ARTIFACT_SPECS = {spec.name: spec for spec in CRITICAL_ARTIFACTS}
_TERMINAL_EVENTS = {
    "SUCCEEDED": "STAGE_COMPLETED",
    "DEGRADED": "STAGE_COMPLETED",
    "FAILED": "STAGE_FAILED",
    "SKIPPED": "STAGE_SKIPPED",
}


def _utc_now(value: datetime | None = None) -> datetime:
    stamp = value or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _validate_stage(stage: object) -> str:
    if type(stage) is not str or not _STAGE_RE.fullmatch(stage):
        raise ValueError(f"invalid stage name: {stage!r}")
    return stage


def _validate_status(status: object) -> str:
    if type(status) is not str or status not in _TERMINAL_EVENTS:
        raise ValueError(
            f"invalid checkpoint status: {status!r}; expected "
            f"{sorted(_TERMINAL_EVENTS)!r}"
        )
    return status


def _state_from_path(path: Path, *, run_id: str) -> RunState:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("state root must be an object")
        if raw.get("run_id") != run_id:
            raise RuntimeError(
                f"state run_id mismatch: expected={run_id!r}, actual={raw.get('run_id')!r}"
            )
        state = RunState(
            run_id=run_id,
            business_status=BusinessStatus(raw["business_status"]),
            evidence_status=EvidenceStatus(raw["evidence_status"]),
            replayability=Replayability(raw["replayability"]),
            created_at=str(raw["created_at"]),
            updated_at=str(raw["updated_at"]),
        )
        for field in (state.created_at, state.updated_at):
            if not _UTC_TIMESTAMP_RE.fullmatch(field):
                raise ValueError("state timestamps must be canonical UTC with six digits")
        created = datetime.fromisoformat(state.created_at.replace("Z", "+00:00"))
        updated = datetime.fromisoformat(state.updated_at.replace("Z", "+00:00"))
        if updated < created:
            raise ValueError("state updated_at is before created_at")
        return state
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"invalid state.json: {exc}") from exc


def _write_contract_copies(handle: RunHandle) -> None:
    paths = (
        handle.workspace / "run_contract.json",
        handle.staging / "run_contract.json",
        handle.capsule / "identity/run_contract.json",
    )
    for path in paths:
        write_run_contract(path, handle.contract)
    payloads = {path.read_bytes() for path in paths}
    if len(payloads) != 1 or any(load_run_contract(path) != handle.contract for path in paths):
        raise RuntimeError("RunContract copies are not identical and verified")


def _write_state(workspace: Path, state: RunState) -> Path:
    return atomic_write_json(workspace / "state.json", state.to_dict())


def _create_run_layout(handle: RunHandle) -> None:
    handle.staging.mkdir(parents=True, exist_ok=False)
    for relative in ("identity", "events", "stages", "products/staging"):
        (handle.capsule / relative).mkdir(parents=True, exist_ok=True)


def _run_started_fields(handle: RunHandle) -> dict:
    return {
        "run_id": handle.run_id,
        "engine": handle.engine,
        "stage": "run",
        "invocation_id": f"run-{handle.run_id}",
        "attempt": 1,
        "subject": None,
        "event_type": "RUN_STARTED",
        "payload": {
            "analysis_date": handle.analysis_date,
            "contract_hash": handle.contract.contract_hash,
            "kind": handle.contract.run_kind,
            "workspace": str(handle.workspace),
        },
    }


def _safe_exception_text(error: BaseException) -> str:
    text = str(error) or type(error).__name__
    redacted = str(redact_value(text).value)
    if not scan_for_secrets(redacted.encode("utf-8"))["ok"]:
        return "[REDACTED]"
    return redacted


def _require_secret_free_contract(contract) -> None:
    """Reject the exact effective contract; never persist a redacted substitute."""
    payload = contract.to_dict()
    redaction = redact_value(payload)
    serialized = canonical_json(payload).encode("utf-8")
    if redaction.hits or not scan_for_secrets(serialized)["ok"]:
        raise ValueError("effective RunContract contains suspected secret material")


def _identity_event_payload(result: Mapping) -> dict:
    """Expose component outcomes without allowing capture errors to leak secrets."""
    components = result.get("components", {})
    summary = {
        str(name): str(component.get("status", "MISSING"))
        for name, component in components.items()
        if isinstance(component, Mapping)
    }
    payload = {
        "ok": bool(result.get("ok")),
        "components": summary,
        "missing": sorted(str(item) for item in result.get("missing", [])),
        "errors": result.get("errors", []),
    }
    redacted = redact_value(payload).value
    if not isinstance(redacted, dict):  # pragma: no cover - recursive shape invariant
        raise TypeError("identity result did not normalize to an object")
    if not scan_for_secrets(canonical_json(redacted).encode("utf-8"))["ok"]:
        return {
            "ok": False,
            "components": {},
            "missing": ["identity_snapshot"],
            "errors": ["[REDACTED]"],
        }
    return redacted


def _persist_identity_event_failure(
    handle: RunHandle,
    *,
    attempted_event: str,
    error: BaseException,
) -> None:
    path = handle.capsule / "identity/identity_event_failure.json"
    failures: list[dict[str, str]] = []
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(current, dict) and isinstance(current.get("failures"), list):
            failures = [row for row in current["failures"] if isinstance(row, dict)]
    except FileNotFoundError:
        pass
    failure = {
        "attempted_event": attempted_event,
        "component": "identity_events",
        "error_type": type(error).__name__,
    }
    failures.append(failure)
    marker = {"schema_version": 1, "failures": failures}
    safe = redact_value(marker).value
    serialized = canonical_json(safe).encode("utf-8")
    if not scan_for_secrets(serialized)["ok"]:
        raise ValueError("identity event failure metadata contains secret material")
    atomic_write_json(path, safe)


def _append_identity_event(
    handle: RunHandle,
    *,
    event_type: str,
    payload: dict,
) -> bool:
    try:
        append_event(
            handle.capsule / "events/events.jsonl",
            run_id=handle.run_id,
            engine=handle.engine,
            stage="identity",
            invocation_id=f"identity-{handle.run_id}",
            attempt=1,
            subject=None,
            event_type=event_type,
            payload=payload,
        )
        return True
    except Exception as exc:
        _persist_identity_event_failure(
            handle,
            attempted_event=event_type,
            error=exc,
        )
        return False


def _record_identity_snapshot(handle: RunHandle) -> None:
    """Capture identity after RUN_STARTED; identity gaps never kill the business run."""
    try:
        repo_root = Path(__file__).resolve().parents[2]
        result = snapshot_identity(
            repo_root,
            handle.capsule / "identity",
            engine=handle.engine,
        )
    except Exception as exc:
        result = {
            "ok": False,
            "components": {},
            "missing": ["identity_snapshot"],
            "errors": [
                {
                    "component": "identity_snapshot",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            ],
        }
        payload = _identity_event_payload(result)
        _append_identity_event(handle, event_type="EVIDENCE_MISSING", payload=payload)
        return

    payload = _identity_event_payload(result)
    snapshotted = _append_identity_event(
        handle,
        event_type="IDENTITY_SNAPSHOTTED",
        payload=payload,
    )
    if not result.get("ok") or not snapshotted:
        gap_payload = dict(payload)
        if not snapshotted:
            gap_payload["ok"] = False
            gap_payload["missing"] = sorted(
                {*gap_payload.get("missing", []), "identity_snapshot_event"}
            )
            gap_payload["errors"] = [
                *gap_payload.get("errors", []),
                {
                    "component": "identity_events",
                    "error_type": "EVENT_APPEND_FAILED",
                },
            ]
        _append_identity_event(
            handle,
            event_type="EVIDENCE_MISSING",
            payload=gap_payload,
        )


def _record_bootstrap_failure(
    handle: RunHandle,
    *,
    phase: str,
    error: BaseException,
    now: datetime,
) -> None:
    """Best-effort evidence for failures after the collision-safe allocation."""
    safe_error = _safe_exception_text(error)
    marker = {
        "analysis_date": handle.analysis_date,
        "contract_hash": handle.contract.contract_hash,
        "engine": handle.engine,
        "error": safe_error,
        "error_type": type(error).__name__,
        "phase": phase,
        "run_id": handle.run_id,
    }
    with contextlib.suppress(Exception):
        atomic_write_json(handle.workspace / "bootstrap_failure.json", marker)
    with contextlib.suppress(Exception):
        failed = RunState.build(
            run_id=handle.run_id,
            business_status=BusinessStatus.FAILED,
            evidence_status=EvidenceStatus.EVIDENCE_INCOMPLETE,
            replayability=Replayability.NONE,
            now=now,
        )
        _write_state(handle.workspace, failed)
    try:
        _create_run_layout(handle)
    except Exception:
        # The layout may be partially present.  Establish each required directory
        # independently so one failed mkdir does not hide all later evidence.
        for path in (
            handle.staging,
            handle.capsule / "identity",
            handle.capsule / "events",
            handle.capsule / "stages",
            handle.capsule / "products/staging",
        ):
            with contextlib.suppress(Exception):
                path.mkdir(parents=True, exist_ok=True)
    for path in (
        handle.workspace / "run_contract.json",
        handle.staging / "run_contract.json",
        handle.capsule / "identity/run_contract.json",
    ):
        with contextlib.suppress(Exception):
            write_run_contract(path, handle.contract)
    event_path = handle.capsule / "events/events.jsonl"
    try:
        chain = verify_event_chain(event_path)
        if chain["ok"] and chain["n"] == 0:
            append_event(event_path, **_run_started_fields(handle))
        append_event(
            event_path,
            run_id=handle.run_id,
            engine=handle.engine,
            stage="bootstrap",
            invocation_id=f"bootstrap-{handle.run_id}",
            attempt=1,
            subject=None,
            event_type="STAGE_FAILED",
            payload=marker,
        )
    except Exception:
        pass


def begin_run(
    kind: str,
    analysis_date: str,
    engine: str,
    config,
    *,
    now: datetime | None = None,
    session_ref: str | None = None,
) -> RunHandle:
    """Allocate one collision-safe active run and persist its identity first."""
    if kind != "scan-market":
        raise ValueError(f"unsupported run kind: {kind!r}")
    resolved_date = ws.validate_scan_date(analysis_date)
    if engine not in ws.ENGINES or engine != ws.ENGINE:
        raise ValueError(
            f"engine mismatch: requested={engine!r}, current={ws.ENGINE!r}"
        )
    stamp = _utc_now(now)
    run_id = ws.validate_run_id(stamp.strftime("%Y%m%dT%H%M%S%fZ"))
    workspace = ws.scan_run_root(run_id)
    staging = workspace / "staging" / resolved_date
    capsule = workspace / "capsule"

    # All configuration/git/prompt probing is completed before the run directory is
    # published.  Invalid configuration therefore cannot leave an anonymous orphan.
    from autoresearch.scan.run_bootstrap import prepare_scan_run

    contract = prepare_scan_run(
        resolved_date,
        config=config,
        run_id=run_id,
        engine=engine,
        workspace_path=workspace,
        session_ref=session_ref,
        now=stamp,
    )
    _require_secret_free_contract(contract)
    handle = RunHandle(
        run_id=run_id,
        analysis_date=resolved_date,
        engine=engine,
        workspace=workspace,
        staging=staging,
        capsule=capsule,
        contract=contract,
    )
    workspace.parent.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(exist_ok=False)
    state = RunState.build(
        run_id=run_id,
        business_status=BusinessStatus.ACTIVE,
        evidence_status=EvidenceStatus.PENDING,
        replayability=Replayability.NONE,
        now=stamp,
    )
    phase = "state"
    try:
        # The recovery-visible state is the first write after mkdir(exist_ok=False).
        _write_state(workspace, state)
        phase = "layout"
        _create_run_layout(handle)
        phase = "contract"
        _write_contract_copies(handle)
        phase = "event"
        append_event(capsule / "events/events.jsonl", **_run_started_fields(handle))
    except Exception as exc:
        _record_bootstrap_failure(handle, phase=phase, error=exc, now=stamp)
        safe_error = _safe_exception_text(exc)
        raise RuntimeError(
            f"run bootstrap failed at {phase}; recoverable workspace={workspace}: "
            f"{safe_error}"
        ) from None
    _record_identity_snapshot(handle)
    return handle


def _require_workspace_path(
    workspace: Path,
    path: Path,
    *,
    kind: str,
) -> Path:
    """Require a real non-symlink path lexically and physically inside workspace."""
    try:
        relative = path.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(f"required {kind} escapes workspace: {path}") from exc
    current = workspace
    if current.is_symlink():
        raise ValueError(f"required workspace is a symlink: {workspace}")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"required {kind} contains a symlink: {current}")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(workspace.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise RuntimeError(f"required {kind} is missing or escapes workspace: {path}") from exc
    if kind == "directory" and not path.is_dir():
        raise RuntimeError(f"required directory is not a directory: {path}")
    if kind == "file" and not path.is_file():
        raise RuntimeError(f"required file is not a regular file: {path}")
    return path


def load_run(run_id: str) -> RunHandle:
    """Load a run strictly beneath the current engine's active spool root."""
    resolved_id = ws.validate_run_id(run_id)
    parent = ws.context_root() / "scan_runs"
    workspace = parent / resolved_id
    if not workspace.is_dir():
        raise FileNotFoundError(f"unknown run_id: {resolved_id}")
    try:
        workspace.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise ValueError("run workspace escapes current engine root") from exc
    if workspace.is_symlink():
        raise ValueError("run workspace cannot be a symlink")

    _require_workspace_path(workspace, workspace, kind="directory")
    workspace_contract = _require_workspace_path(
        workspace, workspace / "run_contract.json", kind="file"
    )
    _require_workspace_path(workspace, workspace / "state.json", kind="file")
    capsule = _require_workspace_path(workspace, workspace / "capsule", kind="directory")
    _require_workspace_path(workspace, capsule / "identity", kind="directory")
    _require_workspace_path(workspace, capsule / "events", kind="directory")
    _require_workspace_path(workspace, capsule / "stages", kind="directory")
    _require_workspace_path(workspace, capsule / "products/staging", kind="directory")
    contract_paths = (
        workspace_contract,
        _require_workspace_path(
            workspace, capsule / "identity/run_contract.json", kind="file"
        ),
    )
    contracts = [load_run_contract(path) for path in contract_paths]
    contract = contracts[0]
    staging = workspace / "staging" / contract.analysis_date
    _require_workspace_path(workspace, workspace / "staging", kind="directory")
    _require_workspace_path(workspace, staging, kind="directory")
    staging_contract = _require_workspace_path(
        workspace, staging / "run_contract.json", kind="file"
    )
    contracts.append(load_run_contract(staging_contract))
    contract_paths = (*contract_paths, staging_contract)
    if any(item != contract for item in contracts[1:]):
        raise RuntimeError("RunContract copies disagree")
    if len({path.read_bytes() for path in contract_paths}) != 1:
        raise RuntimeError("RunContract copies are not byte-identical")
    if contract.schema_version != 3:
        raise RuntimeError(f"RunContract v3 required, got {contract.schema_version}")
    if contract.run_id != resolved_id:
        raise RuntimeError("RunContract run_id does not match workspace")
    if contract.engine != ws.ENGINE:
        raise RuntimeError(
            f"RunContract engine mismatch: {contract.engine!r} != {ws.ENGINE!r}"
        )
    if Path(contract.workspace_path).resolve() != workspace.resolve():
        raise RuntimeError("RunContract workspace_path does not match loaded workspace")
    state = _state_from_path(workspace / "state.json", run_id=resolved_id)
    if state.created_at != contract.created_at:
        raise RuntimeError(
            "state created_at does not match RunContract: "
            f"state={state.created_at!r}, contract={contract.created_at!r}"
        )
    event_path = _require_workspace_path(
        workspace, capsule / "events/events.jsonl", kind="file"
    )
    chain = verify_event_chain(event_path)
    if not chain["ok"]:
        raise RuntimeError(f"invalid event chain: {chain['error']}")
    if chain["n"] < 1:
        raise RuntimeError("RUN_STARTED event is missing")
    handle = RunHandle(
        run_id=resolved_id,
        analysis_date=contract.analysis_date,
        engine=contract.engine,
        workspace=workspace,
        staging=staging,
        capsule=capsule,
        contract=contract,
    )
    first_event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[0])
    expected_started = _run_started_fields(handle)
    actual_started = {key: first_event.get(key) for key in expected_started}
    if actual_started != expected_started:
        raise RuntimeError("first event must be the matching RUN_STARTED fact")
    return handle


def require_active_run(run_id: str) -> RunHandle:
    """Load a valid run and reject terminal business states."""
    handle = load_run(run_id)
    state = _state_from_path(handle.workspace / "state.json", run_id=handle.run_id)
    if state.business_status != BusinessStatus.ACTIVE:
        raise RuntimeError(
            f"run {handle.run_id} is not ACTIVE: {state.business_status.value}"
        )
    return handle


def _validate_agent_identifier(name: str, value: object) -> str:
    if type(value) is not str or not _AGENT_ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}: {value!r}")
    return value


def _redact_agent_value(value: object) -> object:
    return redact_value(value).value


def _agent_payload(name: str, value: Mapping | None) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a JSON object or None")
    redacted = _redact_agent_value(dict(value))
    try:
        normalized = json.loads(canonical_json(redacted))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain canonical JSON values") from exc
    if type(normalized) is not dict:
        raise TypeError(f"{name} must be a JSON object or None")
    if not scan_for_secrets(canonical_json(normalized).encode("utf-8"))["ok"]:
        raise ValueError(f"{name} contains suspected secret material")
    return normalized


def _validate_agent_payload_rules(
    event_type: str,
    *,
    result: dict | None,
    error: dict | None,
) -> None:
    if event_type == "AGENT_DISPATCHED" and error is not None:
        raise ValueError("AGENT_DISPATCHED forbids error payload")
    if event_type == "AGENT_COMPLETED" and error is not None:
        raise ValueError("AGENT_COMPLETED forbids error payload")
    if event_type == "AGENT_FAILED":
        if error is None:
            raise ValueError("AGENT_FAILED requires error payload")
        if result is not None:
            raise ValueError("AGENT_FAILED forbids result payload")


def _agent_semantic(event: Mapping) -> dict:
    return {
        key: event[key]
        for key in (
            "run_id",
            "engine",
            "stage",
            "invocation_id",
            "attempt",
            "subject",
            "event_type",
            "payload",
        )
    }


def _agent_lifecycle_guard(history: tuple[dict, ...], proposed: dict) -> dict | None:
    related = [
        event
        for event in history
        if event["run_id"] == proposed["run_id"]
        and event["invocation_id"] == proposed["invocation_id"]
        and event["event_type"] in _AGENT_EVENT_TYPES
    ]
    dispatches = [
        event for event in related if event["event_type"] == "AGENT_DISPATCHED"
    ]
    terminals = [
        event for event in related if event["event_type"] != "AGENT_DISPATCHED"
    ]
    if len(dispatches) > 1 or len(terminals) > 1:
        raise ValueError("agent lifecycle history contains duplicate semantic events")

    if proposed["event_type"] == "AGENT_DISPATCHED":
        if terminals and not dispatches:
            raise ValueError("agent lifecycle has terminal without dispatch")
        if dispatches:
            if _agent_semantic(dispatches[0]) == proposed:
                return dispatches[0]
            raise ValueError("conflicting agent dispatch for invocation_id")
        return None

    if not dispatches:
        raise ValueError("agent terminal requires prior dispatch")
    dispatched = dispatches[0]
    dispatch_binding = (
        dispatched["engine"],
        dispatched["stage"],
        dispatched["subject"],
        dispatched["attempt"],
        dispatched["payload"]["role"],
    )
    proposed_binding = (
        proposed["engine"],
        proposed["stage"],
        proposed["subject"],
        proposed["attempt"],
        proposed["payload"]["role"],
    )
    if proposed_binding != dispatch_binding:
        raise ValueError("agent terminal binding differs from dispatch")
    if terminals:
        if _agent_semantic(terminals[0]) == proposed:
            return terminals[0]
        raise ValueError("conflicting agent terminal for invocation_id")
    return None


def record_agent_boundary(
    run_id: str,
    event_type: str,
    *,
    role: str,
    subject: str,
    invocation_id: str,
    attempt: int,
    result: Mapping | None = None,
    error: Mapping | None = None,
) -> dict:
    """Append one authoritative agent dispatch/terminal binding to an active run."""
    if type(event_type) is not str or event_type not in _AGENT_EVENT_TYPES:
        raise ValueError(
            f"invalid event_type: {event_type!r}; expected {sorted(_AGENT_EVENT_TYPES)!r}"
        )
    resolved_role = _validate_agent_identifier("role", role)
    resolved_subject = _validate_agent_identifier("subject", subject)
    resolved_invocation = _validate_agent_identifier("invocation_id", invocation_id)
    if type(attempt) is not int or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    normalized_result = _agent_payload("result", result)
    normalized_error = _agent_payload("error", error)
    _validate_agent_payload_rules(
        event_type,
        result=normalized_result,
        error=normalized_error,
    )
    ambient_run_id = ws.active_run_id()
    if ambient_run_id is not None and ambient_run_id != run_id:
        raise ValueError(
            f"AUTORESEARCH_RUN_ID={ambient_run_id!r} does not match {run_id!r}"
        )
    handle = require_active_run(run_id)
    stage = _validate_stage(
        str(os.environ.get("AUTORESEARCH_STAGE", "")).strip() or "l4"
    )
    return append_guarded_event(
        handle.capsule / "events/events.jsonl",
        guard=_agent_lifecycle_guard,
        run_id=handle.run_id,
        engine=handle.engine,
        stage=stage,
        invocation_id=resolved_invocation,
        attempt=attempt,
        subject=resolved_subject,
        event_type=event_type,
        payload={
            "error": normalized_error,
            "result": normalized_result,
            "role": resolved_role,
        },
    )


def record_controlled_agent_boundary(
    run_id: str,
    event_type: str,
    *,
    role: str,
    subject: str,
    invocation_id: str,
    attempt: int,
    control_invocation_id: str,
    result: Mapping | None = None,
    error: Mapping | None = None,
) -> dict:
    """Self-register one trace-control agent around its target boundary append."""
    binding = {
        "target_event_type": event_type,
        "target_invocation_id": invocation_id,
        "target_role": role,
    }
    control_dispatched = record_agent_boundary(
        run_id,
        "AGENT_DISPATCHED",
        role="trace-control",
        subject=subject,
        invocation_id=control_invocation_id,
        attempt=attempt,
        result=binding,
    )
    try:
        target = record_agent_boundary(
            run_id,
            event_type,
            role=role,
            subject=subject,
            invocation_id=invocation_id,
            attempt=attempt,
            result=result,
            error=error,
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            record_agent_boundary(
                run_id,
                "AGENT_FAILED",
                role="trace-control",
                subject=subject,
                invocation_id=control_invocation_id,
                attempt=attempt,
                error={
                    "error_type": type(exc).__name__,
                    "phase": "target-boundary",
                    **binding,
                },
            )
        raise
    try:
        control_completed = record_agent_boundary(
            run_id,
            "AGENT_COMPLETED",
            role="trace-control",
            subject=subject,
            invocation_id=control_invocation_id,
            attempt=attempt,
            result={
                "target_event_hash": target["event_hash"],
                **binding,
            },
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            record_agent_boundary(
                run_id,
                "AGENT_FAILED",
                role="trace-control",
                subject=subject,
                invocation_id=control_invocation_id,
                attempt=attempt,
                error={
                    "error_type": type(exc).__name__,
                    "phase": "control-terminal",
                    **binding,
                },
            )
        raise
    return {
        "control_events": [control_dispatched, control_completed],
        "event": target,
    }


def _validate_report_dir(report_dir: Path | str | None) -> Path | None:
    if report_dir is None:
        return None
    report = Path(report_dir)
    allowed = ws.reports_root() / "scan"
    try:
        relative = report.absolute().relative_to(allowed.absolute())
    except ValueError as exc:
        raise ValueError(f"report_dir escapes current engine reports root: {report}") from exc
    current = allowed.absolute()
    if current.is_symlink():
        raise ValueError(f"reports scan root is a symlink: {current}")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"report_dir contains a symlink: {current}")
    if not report.is_dir():
        raise ValueError(f"report_dir is not an existing directory: {report}")
    try:
        report.resolve(strict=True).relative_to(allowed.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(f"report_dir escapes current engine reports root: {report}") from exc
    return report


def _reject_symlink_components(base: Path, path: Path, *, label: str) -> None:
    try:
        relative = path.absolute().relative_to(base.absolute())
    except ValueError as exc:
        raise ValueError(f"{label} escapes its declared root: {path}") from exc
    current = base.absolute()
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} contains a symlink: {current}")


def _literal_artifact(
    handle: RunHandle,
    value: Path | str,
    *,
    report_dir: Path | None,
) -> dict:
    if not isinstance(value, (str, Path)):
        raise TypeError(f"artifact must be a path string, got {type(value).__name__}")
    text = str(value)
    if not text or "\x00" in text:
        raise ValueError(f"invalid artifact path: {text!r}")
    raw = Path(text)
    if ".." in raw.parts:
        raise ValueError(f"artifact path traverses staging: {text!r}")
    roots = [("scan", handle.staging)]
    if report_dir is not None:
        roots.append(("report", report_dir))
    candidates = []
    positioned = raw if raw.is_absolute() else raw.absolute()
    for root_name, root in roots:
        try:
            positioned.relative_to(root.absolute())
            candidates.append((root_name, root, positioned))
        except ValueError:
            pass
    if not candidates and not raw.is_absolute():
        relative_candidates = [
            (root_name, root, root / raw) for root_name, root in roots
        ]
        existing = [item for item in relative_candidates if item[2].exists()]
        if len(existing) > 1:
            raise ValueError(f"literal artifact path is ambiguous across roots: {text!r}")
        if len(existing) == 1:
            candidates = existing
        elif len(relative_candidates) == 1:
            candidates = relative_candidates
        else:
            raise ValueError(
                f"missing literal artifact path is ambiguous; use an absolute path: {text!r}"
            )
    if len(candidates) != 1:
        raise ValueError(f"artifact path is outside or ambiguous: {text!r}")
    root_name, root, source = candidates[0]
    _reject_symlink_components(root, source, label="artifact source")
    resolved = source.resolve(strict=False)
    try:
        relative = resolved.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise ValueError(f"artifact path escapes {root_name} root: {text!r}") from exc
    if not relative.parts:
        raise ValueError("artifact path cannot be the staging root")
    try:
        source_info = source.lstat()
    except FileNotFoundError:
        captured_source = None
        source_signature = None
    else:
        if stat.S_ISLNK(source_info.st_mode) or not stat.S_ISREG(source_info.st_mode):
            raise ValueError(f"artifact must be a regular file: {text!r}")
        captured_source = resolved
        source_signature = _capture_signature(source_info)
    if captured_source is not None and not source.is_file():
        raise ValueError(f"artifact must be a regular file: {text!r}")
    return {
        "logical_id": None,
        "pattern": None,
        "root": root_name,
        "path": relative.as_posix(),
        "source": captured_source,
        "source_signature": source_signature,
    }


def _registered_artifact_rows(
    handle: RunHandle,
    spec: ArtifactSpec,
    *,
    report_dir: Path | None,
) -> list[dict]:
    base = handle.staging if spec.root == "scan" else report_dir
    if base is None:
        return [
            {
                "logical_id": spec.name,
                "pattern": spec.path,
                "root": spec.root,
                "path": spec.path,
                "source": None,
                "source_signature": None,
            }
        ]
    base_resolved = base.resolve(strict=True)
    matches = sorted(base.glob(spec.path))
    rows = []
    for source in matches:
        _reject_symlink_components(base, source, label="artifact source")
        if not source.is_file():
            continue
        resolved = source.resolve(strict=True)
        try:
            relative = resolved.relative_to(base_resolved)
        except ValueError as exc:
            raise ValueError(f"artifact source escapes {spec.root} root: {source}") from exc
        rows.append(
            {
                "logical_id": spec.name,
                "pattern": spec.path,
                "root": spec.root,
                "path": relative.as_posix(),
                "source": resolved,
                "source_signature": _capture_signature(resolved.lstat()),
            }
        )
    if rows:
        return rows
    return [
        {
            "logical_id": spec.name,
            "pattern": spec.path,
            "root": spec.root,
            "path": spec.path,
            "source": None,
            "source_signature": None,
        }
    ]


def _resolve_artifacts(
    handle: RunHandle,
    artifacts: Sequence[Path | str],
    *,
    report_dir: Path | str | None,
) -> list[dict]:
    report = _validate_report_dir(report_dir)
    rows = []
    seen: set[tuple[str, str]] = set()
    for artifact in artifacts:
        spec = _ARTIFACT_SPECS.get(artifact) if type(artifact) is str else None
        resolved = (
            _registered_artifact_rows(handle, spec, report_dir=report)
            if spec is not None
            else [_literal_artifact(handle, artifact, report_dir=report)]
        )
        for row in resolved:
            # Every artifact is copied below ``<attempt>/<root>/<path>``.  Logical
            # IDs are provenance, not a namespace, so they cannot make duplicate
            # destinations distinct.
            key = (row["root"], row["path"])
            if key in seen:
                raise ValueError(f"artifact path collision: {key!r}")
            seen.add(key)
            rows.append(row)
    return rows


def _safe_directory(root: Path, relative: Path | str, *, create: bool) -> Path:
    """Walk one relative directory path without accepting symlink components."""
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"directory escapes trusted root: {relative_path}")
    root_info = root.lstat()
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ValueError(f"trusted directory is not a real directory: {root}")
    root_resolved = root.resolve(strict=True)
    current = root
    for part in relative_path.parts:
        if part in ("", "."):
            continue
        candidate = current / part
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            if not create:
                raise
            # Another process may win the first-use mkdir race.  Its entry is
            # trusted only after the same lstat/type/containment checks below.
            with contextlib.suppress(FileExistsError):
                candidate.mkdir(exist_ok=False)
            info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"destination directory contains a symlink: {candidate}")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"destination component is not a directory: {candidate}")
        try:
            candidate.resolve(strict=True).relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError(f"destination directory escapes trusted root: {candidate}") from exc
        current = candidate
    return current


def _allocate_attempt(capsule: Path, stage: str) -> tuple[int, Path, Path]:
    # Validate both destination branches before claiming an attempt.  In particular,
    # a pre-created products symlink must fail before stages/attempt-N exists.
    stage_root = _safe_directory(capsule, Path("stages") / stage, create=True)
    product_stage_root = _safe_directory(
        capsule, Path("products/staging") / stage, create=True
    )
    while True:
        numbers = []
        for path in stage_root.iterdir():
            match = re.fullmatch(r"attempt-([1-9][0-9]*)", path.name)
            if match:
                numbers.append(int(match.group(1)))
        attempt = max(numbers, default=0) + 1
        path = stage_root / f"attempt-{attempt}"
        try:
            path.mkdir(exist_ok=False)
            _safe_directory(capsule, path.relative_to(capsule), create=False)
            return attempt, path, product_stage_root
        except FileExistsError:
            continue


_CAPTURE_STAT_FIELDS = (
    "st_mode",
    "st_dev",
    "st_ino",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


def _capture_signature(info: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(info, field) for field in _CAPTURE_STAT_FIELDS)


def _write_capture(fd: int, block: bytes) -> None:
    view = memoryview(block)
    while view:
        written = os.write(fd, view)
        if written == 0:
            raise OSError("short write while capturing artifact")
        view = view[written:]


def _copy_artifact(
    source: Path,
    destination: Path,
    *,
    expected_signature: tuple[int, ...] | None,
) -> tuple[int, bool]:
    """Capture from one no-follow descriptor and prove the source stayed stable."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(source, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise RuntimeError(f"artifact source changed during capture: {source}") from exc
    destination_fd = -1
    try:
        before = os.fstat(source_fd)
        if (
            expected_signature is not None
            and _capture_signature(before) != expected_signature
        ):
            raise RuntimeError(f"artifact source changed during capture: {source}")
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"artifact source is not a regular file: {source}")
        captured_size = 0
        if before.st_size == 0:
            # A read is still part of the protocol for EMPTY: a file populated
            # after the first fstat cannot be silently recorded as empty.
            captured_size = len(os.read(source_fd, 1))
        else:
            destination_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                0o600,
            )
            while True:
                block = os.read(source_fd, 1024 * 1024)
                if not block:
                    break
                _write_capture(destination_fd, block)
                captured_size += len(block)
            os.fsync(destination_fd)
            destination_info = os.fstat(destination_fd)
            if destination_info.st_size != captured_size:
                raise RuntimeError(
                    "artifact source changed during capture: "
                    f"captured={captured_size}, destination={destination_info.st_size}"
                )

        after = os.fstat(source_fd)
        try:
            path_after = source.lstat()
        except OSError as exc:
            raise RuntimeError(f"artifact source changed during capture: {source}") from exc
        if (
            _capture_signature(before) != _capture_signature(after)
            or _capture_signature(after) != _capture_signature(path_after)
            or captured_size != after.st_size
        ):
            raise RuntimeError(f"artifact source changed during capture: {source}")
        return captured_size, before.st_size > 0
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(source_fd)


def checkpoint(
    run_id: str,
    stage: str,
    status: str,
    artifacts: Sequence[Path | str],
    metrics: Mapping,
    error: str | None = None,
    *,
    report_dir: Path | str | None = None,
) -> Checkpoint:
    """Persist one immutable attempt and its two facts.

    Concurrent pairs may interleave globally; consumers join them by
    ``(stage, attempt)``.  Within each pair the terminal event is always appended
    before ``CHECKPOINT_WRITTEN`` and each appears exactly once.
    """
    handle = require_active_run(run_id)
    resolved_stage = _validate_stage(stage)
    resolved_status = _validate_status(status)
    if isinstance(artifacts, (str, bytes)) or not isinstance(artifacts, Sequence):
        raise TypeError("artifacts must be a sequence of paths")
    if not isinstance(metrics, Mapping):
        raise TypeError("metrics must be a mapping")
    if error is not None and type(error) is not str:
        raise TypeError("error must be a string or None")
    normalized_metrics = json.loads(canonical_json(dict(metrics)))
    normalized_artifacts = _resolve_artifacts(
        handle, artifacts, report_dir=report_dir
    )

    attempt, attempt_path, product_stage_root = _allocate_attempt(
        handle.capsule, resolved_stage
    )
    product_root = product_stage_root / f"attempt-{attempt}"
    output_rows = []
    for artifact in normalized_artifacts:
        source = artifact["source"]
        row = {
            "logical_id": artifact["logical_id"],
            "pattern": artifact["pattern"],
            "root": artifact["root"],
            "path": artifact["path"],
            "status": "MISSING",
            "bytes": None,
            "sha256": None,
        }
        if source is not None:
            product_root = _safe_directory(
                handle.capsule,
                product_root.relative_to(handle.capsule),
                create=True,
            )
            relative_destination = Path(artifact["root"]) / artifact["path"]
            destination_parent = _safe_directory(
                product_root, relative_destination.parent, create=True
            )
            destination = destination_parent / relative_destination.name
            captured_size, present = _copy_artifact(
                source,
                destination,
                expected_signature=artifact["source_signature"],
            )
            row["bytes"] = captured_size
            if present:
                row.update(
                    {
                        "status": "PRESENT",
                        "sha256": sha256_file(destination),
                        "captured_path": destination.relative_to(handle.capsule).as_posix(),
                    }
                )
            else:
                row["status"] = "EMPTY"
        output_rows.append(row)

    created_at = (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    item = Checkpoint(
        run_id=handle.run_id,
        stage=resolved_stage,
        attempt=attempt,
        status=resolved_status,
        path=attempt_path,
        created_at=created_at,
        artifacts=tuple(str(artifact) for artifact in artifacts),
        metrics=normalized_metrics,
        error=error,
    )
    atomic_write_json(attempt_path / "inputs.json", {"schema_version": 1, "inputs": []})
    atomic_write_json(
        attempt_path / "outputs.json",
        {"schema_version": 1, "artifacts": output_rows},
    )
    # result.json is the completion marker and is intentionally written last.
    atomic_write_json(attempt_path / "result.json", item.to_dict())

    invocation_id = str(os.environ.get("AUTORESEARCH_INVOCATION_ID", "")).strip()
    if not invocation_id:
        invocation_id = f"checkpoint-{resolved_stage}-{attempt}"
    event_path = handle.capsule / "events/events.jsonl"
    event_payload = {
        "checkpoint": attempt_path.relative_to(handle.capsule).as_posix(),
        "error": error,
        "status": resolved_status,
    }
    append_event(
        event_path,
        run_id=handle.run_id,
        engine=handle.engine,
        stage=resolved_stage,
        invocation_id=invocation_id,
        attempt=attempt,
        subject=None,
        event_type=_TERMINAL_EVENTS[resolved_status],
        payload=event_payload,
    )
    append_event(
        event_path,
        run_id=handle.run_id,
        engine=handle.engine,
        stage=resolved_stage,
        invocation_id=invocation_id,
        attempt=attempt,
        subject=None,
        event_type="CHECKPOINT_WRITTEN",
        payload=event_payload,
    )
    return item


def inspect_run(run_id: str) -> dict:
    """Return a read-only summary of one active spool."""
    handle = load_run(run_id)
    state = json.loads((handle.workspace / "state.json").read_text(encoding="utf-8"))
    chain = verify_event_chain(handle.capsule / "events/events.jsonl")
    attempts = []
    for result in sorted((handle.capsule / "stages").glob("*/attempt-*/result.json")):
        attempts.append(json.loads(result.read_text(encoding="utf-8")))
    return {
        "analysis_date": handle.analysis_date,
        "contract_hash": handle.contract.contract_hash,
        "engine": handle.engine,
        "event_chain": chain,
        "run_id": handle.run_id,
        "state": state,
        "attempts": attempts,
        "workspace": str(handle.workspace),
    }


def _emit(value: object) -> None:
    sys.stdout.write(canonical_json(value) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autoresearch.trace.capsule")
    commands = parser.add_subparsers(dest="command", required=True)
    begin = commands.add_parser("begin")
    begin.add_argument("kind")
    begin.add_argument("analysis_date")
    begin.add_argument("--engine", required=True, choices=ws.ENGINES)
    begin.add_argument("--config-file")
    begin.add_argument("--session-ref")
    save = commands.add_parser("checkpoint")
    save.add_argument("run_id")
    save.add_argument("stage")
    save.add_argument("status")
    save.add_argument("--artifact", action="append", default=[])
    save.add_argument("--metrics-json", default="{}")
    save.add_argument("--error")
    save.add_argument("--report-dir")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("run_id")
    agent_event = commands.add_parser("agent-event")
    agent_event.add_argument("run_id")
    agent_event.add_argument("event_type", choices=sorted(_AGENT_EVENT_TYPES))
    agent_event.add_argument("--role", required=True)
    agent_event.add_argument("--subject", required=True)
    agent_event.add_argument("--invocation-id", required=True)
    agent_event.add_argument("--attempt", required=True, type=int)
    agent_event.add_argument("--result-json")
    agent_event.add_argument("--error-json")
    agent_event.add_argument("--control-invocation-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "begin":
            handle = begin_run(
                args.kind,
                args.analysis_date,
                args.engine,
                args.config_file,
                session_ref=args.session_ref,
            )
            result = {
                "analysis_date": handle.analysis_date,
                "contract_hash": handle.contract.contract_hash,
                "engine": handle.engine,
                "run_id": handle.run_id,
                "workspace": str(handle.workspace),
            }
        elif args.command == "checkpoint":
            metrics = json.loads(args.metrics_json)
            result = checkpoint(
                args.run_id,
                args.stage,
                args.status,
                args.artifact,
                metrics,
                error=args.error,
                report_dir=args.report_dir,
            ).to_dict()
        elif args.command == "inspect":
            result = inspect_run(args.run_id)
        else:
            parsed_result = (
                json.loads(args.result_json) if args.result_json is not None else None
            )
            parsed_error = (
                json.loads(args.error_json) if args.error_json is not None else None
            )
            if args.control_invocation_id:
                controlled = record_controlled_agent_boundary(
                    args.run_id,
                    args.event_type,
                    role=args.role,
                    subject=args.subject,
                    invocation_id=args.invocation_id,
                    attempt=args.attempt,
                    control_invocation_id=args.control_invocation_id,
                    result=parsed_result,
                    error=parsed_error,
                )
                result = {**controlled, "ok": True}
            else:
                event = record_agent_boundary(
                    args.run_id,
                    args.event_type,
                    role=args.role,
                    subject=args.subject,
                    invocation_id=args.invocation_id,
                    attempt=args.attempt,
                    result=parsed_result,
                    error=parsed_error,
                )
                result = {"event": event, "ok": True}
        _emit(result)
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI converts failure into honest exit
        message = f"{type(exc).__name__}: {exc}"
        print(f"[capsule] {message}", file=sys.stderr)
        _emit({"error": message, "ok": False})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "begin_run",
    "checkpoint",
    "inspect_run",
    "load_run",
    "main",
    "record_agent_boundary",
    "record_controlled_agent_boundary",
]

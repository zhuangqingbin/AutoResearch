#!/usr/bin/env python3
"""Forensic run spool lifecycle and its small operator CLI."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.run_identity import load_run_contract, write_run_contract
from autoresearch.contracts.profiles import profile_factory
from autoresearch.contracts.stages import RUN_KINDS
from autoresearch.scan.artifacts import CRITICAL_ARTIFACTS, ArtifactSpec
from autoresearch.trace import process_probe
from autoresearch.trace.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    sha256_bytes,
    sha256_file,
)
from autoresearch.trace.blobs import put_bytes
from autoresearch.trace.capsule_models import (
    BusinessStatus,
    Checkpoint,
    EvidenceStatus,
    FinalizationResult,
    Replayability,
    RunHandle,
    RunState,
)
from autoresearch.trace.events import (
    append_event,
    append_guarded_event,
    verify_event_chain,
)
from autoresearch.trace.identity import (
    load_snapshot_result,
    redact_residual_secrets,
    redact_value,
    scan_for_secrets,
    snapshot_identity,
)
from autoresearch.trace.transcripts import (
    TranscriptRef,
    adapter_for,
    is_external_tool,
    tool_call_id,
)

# Submodule-direct (not the package re-export above): the transcript *schema*
# version is `trace/transcripts/base.py`'s vocabulary, not adapter machinery —
# importing it here keeps this file's writer and that module's reader-side
# `require_known_transcript_schema_version` guard sharing one literal instead
# of two that could silently drift apart.
from autoresearch.trace.transcripts.base import (
    CURRENT_TRANSCRIPT_SCHEMA_VERSION as _TRANSCRIPT_SCHEMA_VERSION,
)
from autoresearch.trace.transcripts.snapshot import TranscriptSnapshot, capture_snapshot

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


def _write_state(workspace: Path, state: RunState, *, lease: Mapping | None = None) -> Path:
    """Persist the state, carrying the ownership lease across transitions.

    The lease lives beside the state machine's own fields; dropping it on a
    transition would make a live run look ownerless to the recovery pass.
    """
    path = workspace / "state.json"
    carried = lease
    if carried is None and path.is_file():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(previous.get("lease"), dict):
                carried = previous["lease"]
        except Exception:  # noqa: BLE001 - an unreadable lease is simply not carried
            carried = None
    payload = state.to_dict()
    if carried is not None:
        payload["lease"] = dict(carried)
    return atomic_write_json(path, payload)


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
        try:
            _persist_identity_event_failure(
                handle,
                attempted_event=event_type,
                error=exc,
            )
        except Exception:
            print("identity evidence persistence degraded", file=sys.stderr)
        return False


def _record_identity_snapshot(handle: RunHandle) -> None:
    """Capture identity after RUN_STARTED; identity gaps never kill the business run."""
    try:
        repo_root = Path(__file__).resolve().parents[2]
        identity_root = handle.capsule / "identity"
        result = snapshot_identity(
            repo_root,
            identity_root,
            engine=handle.engine,
        )
        if (identity_root / "snapshot_result.json").is_file():
            result = load_snapshot_result(identity_root)
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


#: The Claude harness exports its session id into every tool shell it runs.  Binding
#: used to be a manual `runctl bind` step that **no production path ever called**:
#: the 2026-09-01 and 09-02 scan runs both wrote `session_ref: null`, so
#: `usage_harvest.collect_run` short-circuited on the adapter's missing-session guard
#: and `token_usage.md` reported `1 主会话 + 0 subagent · UNMEASURED` for runs that had
#: really spent ~18.7M weighted input across 316 subagents.  Reading the id the harness
#: itself exports is an assertion *from* the harness, not an mtime guess over candidate
#: files, so it satisfies the binding doctrine in `bind_transcript`'s docstring.
_HARNESS_SESSION_REF_RE = re.compile(r"[0-9a-fA-F][0-9a-fA-F-]{7,63}\Z")


def harness_session_ref(engine: str) -> str | None:
    """Return the current harness session id for ``engine``, or None when absent.

    Only Claude is wired: Codex binds its rollout explicitly through the capsule and
    must not inherit a Claude session id.  A malformed value is dropped rather than
    written into the contract, because a wrong binding is worse than an absent one --
    it would make the adapter meter *somebody else's* transcript.
    """
    if engine != "claude":
        return None
    raw = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    return raw if _HARNESS_SESSION_REF_RE.match(raw) else None


def begin_run(
    kind: str,
    analysis_date: str,
    engine: str,
    config,
    *,
    now: datetime | None = None,
    session_ref: str | None = None,
    bootstrap=None,
) -> RunHandle:
    """Allocate one collision-safe active run and persist its identity first.

    ``bootstrap`` builds the run's :class:`RunContract` from its configuration and must
    accept ``prepare_scan_run``'s keyword shape.  ``scan-market`` keeps its in-module
    default (that edge is already on the layering allowlist); every other kind
    **must supply one**, because `trace` importing `analyze` would be a brand-new
    upward edge and the ratchet only moves one way.
    """
    if kind not in RUN_KINDS:
        raise ValueError(f"unsupported run kind: {kind!r}")
    resolved_date = ws.validate_scan_date(analysis_date)
    if engine not in ws.ENGINES or engine != ws.ENGINE:
        raise ValueError(
            f"engine mismatch: requested={engine!r}, current={ws.ENGINE!r}"
        )
    if bootstrap is None:
        if kind != "scan-market":
            raise ValueError(
                f"run kind {kind!r} must supply its own bootstrap; trace does not "
                "import the skill packages it observes"
            )
        # All configuration/git/prompt probing is completed before the run directory is
        # published.  Invalid configuration therefore cannot leave an anonymous orphan.
        from autoresearch.scan.run_bootstrap import prepare_scan_run

        bootstrap = prepare_scan_run
    # Explicit wins; otherwise self-bind from the harness so the run can be metered
    # without an operator remembering `runctl bind` (which nothing ever ran).
    if session_ref is None:
        session_ref = harness_session_ref(engine)
    stamp = _utc_now(now)
    run_id = ws.validate_run_id(stamp.strftime("%Y%m%dT%H%M%S%fZ"))
    workspace = ws.run_root(kind, run_id)
    staging = workspace / "staging" / resolved_date
    capsule = workspace / "capsule"

    contract = bootstrap(
        resolved_date,
        config=config,
        run_id=run_id,
        engine=engine,
        workspace_path=workspace,
        session_ref=session_ref,
        now=stamp,
    )
    if contract.run_kind != kind:
        raise ValueError(
            f"bootstrap built a {contract.run_kind!r} contract for a {kind!r} run"
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
        _write_state(
            workspace,
            state,
            lease=process_probe.current_lease(
                invocation_id=f"run-{run_id}",
                heartbeat=state.created_at,
            ),
        )
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
    # run_id 自身不带 kind,所以「这趟住在哪个池子」只能问文件系统(`find_run_root`
    # 逐个 kind 探)。找到之后仍要与契约里的 `run_kind` 对账 —— 一份 scan 契约躺在
    # `analyze_runs/` 里是身份坏了,不是路径巧合。
    workspace = ws.find_run_root(resolved_id)
    if workspace is None:
        raise FileNotFoundError(f"unknown run_id: {resolved_id}")
    parent = workspace.parent
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
    if ws.run_root(contract.run_kind, resolved_id).resolve() != workspace.resolve():
        raise RuntimeError(
            f"RunContract run_kind {contract.run_kind!r} does not match the spool "
            f"holding this run: {parent}"
        )
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


def subject_key(display: str) -> str:
    """Derive a collision-safe ASCII subject key from a non-ASCII display name.

    Event subjects are ASCII identifiers, but real subjects (申万一级行业名) are
    Chinese.  The key is the first 12 hex characters of the display name's
    SHA-256 digest; the display name itself travels in the event payload so the
    index can show it verbatim.
    """
    if type(display) is not str or not display.strip():
        raise ValueError("subject_display must be a non-empty string")
    return hashlib.sha256(display.encode("utf-8")).hexdigest()[:12]


def record_agent_boundary(
    run_id: str,
    event_type: str,
    *,
    role: str,
    invocation_id: str,
    attempt: int,
    subject: str | None = None,
    subject_display: str | None = None,
    result: Mapping | None = None,
    error: Mapping | None = None,
) -> dict:
    """Append one authoritative agent dispatch/terminal binding to an active run.

    ``subject`` is ``None`` for market-wide roles (strategist, L3 rank).  When a
    subject only exists as a non-ASCII display name, pass ``subject_display``
    and the ASCII key is derived deterministically.
    """
    if type(event_type) is not str or event_type not in _AGENT_EVENT_TYPES:
        raise ValueError(
            f"invalid event_type: {event_type!r}; expected {sorted(_AGENT_EVENT_TYPES)!r}"
        )
    resolved_role = _validate_agent_identifier("role", role)
    if subject_display is not None:
        derived = subject_key(subject_display)
        if subject is not None and subject != derived:
            raise ValueError("subject does not match subject_display key")
        subject = derived
    resolved_subject = (
        None if subject is None else _validate_agent_identifier("subject", subject)
    )
    resolved_invocation = _validate_agent_identifier("invocation_id", invocation_id)
    if type(attempt) is not int or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    if subject_display is not None:
        result = {**dict(result or {}), "subject_display": subject_display}
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
    invocation_id: str,
    attempt: int,
    control_invocation_id: str,
    subject: str | None = None,
    subject_display: str | None = None,
    result: Mapping | None = None,
    error: Mapping | None = None,
) -> dict:
    """Self-register one trace-control agent around its target boundary append."""
    binding = {
        "target_event_type": event_type,
        "target_invocation_id": invocation_id,
        "target_role": role,
    }
    control_subject = (
        subject if subject_display is None else subject_key(subject_display)
    )
    control_dispatched = record_agent_boundary(
        run_id,
        "AGENT_DISPATCHED",
        role="trace-control",
        subject=control_subject,
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
            subject_display=subject_display,
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
                subject=control_subject,
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
            subject=control_subject,
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
                subject=control_subject,
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


def _validate_report_dir(
    report_dir: Path | str | None, *, kind: str = "scan-market"
) -> Path | None:
    """发布目录必须真的在**这个 kind** 的发布根底下。

    kind 化之前这里写死 `reports_<engine>/scan`,于是一趟 stock-research 想把
    `reports_<engine>/analyze/<YYYYMMDD_HHMM>/` 交上来就会被判成「逃出发布根」。
    """
    if report_dir is None:
        return None
    report = Path(report_dir)
    allowed = ws.run_reports_root(kind)
    try:
        relative = report.absolute().relative_to(allowed.absolute())
    except ValueError as exc:
        raise ValueError(f"report_dir escapes current engine reports root: {report}") from exc
    current = allowed.absolute()
    if current.is_symlink():
        raise ValueError(f"reports root is a symlink: {current}")
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


# ------------------------------------------------------------------ transcripts

_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+")


def _bindings_path(handle: RunHandle) -> Path:
    return handle.capsule / "agents/bindings.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path.name} rows must be JSON objects")
        rows.append(row)
    return rows


def _append_locked_jsonl(
    path: Path,
    rows: Sequence[Mapping],
    *,
    guard=None,
):
    """Append canonical JSONL rows while holding one exclusive lock.

    ``guard`` sees the rows already on disk and may return a replacement result
    (making the call idempotent) or raise (rejecting it) before anything is
    written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            existing = [
                json.loads(line)
                for line in handle.read().decode("utf-8").splitlines()
                if line.strip()
            ]
            if guard is not None:
                decided = guard(existing)
                if decided is not None:
                    return decided
            payload = b"".join(
                (canonical_json(dict(row)) + "\n").encode("utf-8") for row in rows
            )
            if payload:
                handle.seek(0, os.SEEK_END)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    if rows:
        _fsync_dir(path.parent)
    return list(rows)


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _binding_identity(row: Mapping) -> tuple:
    return (
        row.get("engine"),
        row.get("invocation_id"),
        row.get("role"),
        row.get("subject"),
        row.get("path"),
        row.get("start_ordinal"),
        row.get("end_ordinal"),
    )


def _require_external_source(handle: RunHandle, source: Path) -> Path:
    """Require a readable regular file that is not part of the run's own state."""
    if source.is_symlink():
        raise ValueError(f"transcript path is a symlink: {source}")
    if not source.is_file():
        raise ValueError(f"transcript path is not a regular file: {source}")
    resolved = source.resolve(strict=True)
    for owned, label in (
        (handle.capsule, "inside the capsule"),
        (handle.workspace, "inside the run workspace"),
    ):
        try:
            resolved.relative_to(owned.resolve())
        except ValueError:
            continue
        # A run may not cite its own evidence as an external harness transcript.
        raise ValueError(f"transcript path is {label}: {source}")
    return resolved


def bind_transcript(
    run_id: str,
    path: Path | str,
    *,
    role: str,
    invocation_id: str,
    subject: str | None = None,
    engine: str | None = None,
    start_ordinal: int | None = None,
    end_ordinal: int | None = None,
) -> dict:
    """Record one authoritative transcript binding for an active run.

    Binding is the *only* way a harness transcript becomes evidence: locators
    may enumerate candidates but never promote one by mtime.  Re-binding the
    same identity is idempotent; binding a different path to an invocation that
    already has one is rejected.
    """
    handle = require_active_run(run_id)
    resolved_role = _validate_agent_identifier("role", role)
    resolved_invocation = _validate_agent_identifier("invocation_id", invocation_id)
    resolved_subject = (
        None if subject is None else _validate_agent_identifier("subject", subject)
    )
    resolved_engine = handle.engine if engine is None else str(engine)
    if resolved_engine not in ws.ENGINES:
        raise ValueError(f"engine must be one of {ws.ENGINES!r}")
    for name, value in (("start_ordinal", start_ordinal), ("end_ordinal", end_ordinal)):
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"{name} must be a non-negative integer or None")
    if (
        start_ordinal is not None
        and end_ordinal is not None
        and end_ordinal < start_ordinal
    ):
        raise ValueError("end_ordinal must not precede start_ordinal")
    source = _require_external_source(handle, Path(path))
    stage = _validate_stage(
        str(os.environ.get("AUTORESEARCH_STAGE", "")).strip() or "l4"
    )
    row = {
        "schema_version": _TRANSCRIPT_SCHEMA_VERSION,
        "engine": resolved_engine,
        "invocation_id": resolved_invocation,
        "role": resolved_role,
        "subject": resolved_subject,
        "stage": stage,
        "path": str(source),
        "start_ordinal": start_ordinal,
        "end_ordinal": end_ordinal,
        "session_ref": handle.contract.session_ref,
    }

    def guard(existing: list[dict]):
        for item in existing:
            if item.get("invocation_id") != resolved_invocation:
                continue
            if _binding_identity(item) == _binding_identity(row):
                return item
            raise ValueError(
                "conflicting transcript binding for invocation_id "
                f"{resolved_invocation!r}"
            )
        return None

    decided = _append_locked_jsonl(_bindings_path(handle), [row], guard=guard)
    bound = decided[0] if isinstance(decided, list) else decided
    if bound is row:
        append_event(
            handle.capsule / "events/events.jsonl",
            run_id=handle.run_id,
            engine=handle.engine,
            stage=stage,
            invocation_id=resolved_invocation,
            attempt=1,
            subject=resolved_subject,
            event_type="TRANSCRIPT_BOUND",
            payload={
                "engine": resolved_engine,
                "role": resolved_role,
                "source": str(source),
            },
        )
    return bound


def _archive_plain_bytes(body: bytes) -> bytes:
    """Same mtime=0 + secret-redaction discipline the transcript snapshot
    archive path uses (`transcripts.snapshot._archive_bytes`), for arbitrary
    (non-JSONL harness-row) file content such as `tool-results/*`.

    The residual, byte-level redaction pass (fix round 1, 2026-09-13) now
    lives in `trace.identity.redact_residual_secrets` -- shared with
    `transcripts.snapshot` instead of each module keeping its own
    byte-identical copy of the same blanking loop.
    """
    safe = redact_residual_secrets(body)
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as archive:
        archive.write(safe)
    return buffer.getvalue()


def _archive_tool_results_spill(handle: RunHandle, main_transcript: Path) -> None:
    """Archive `<session>/tool-results/*` next to a bound Claude `role=main` transcript.

    收割盲区(D6.4③):the harness spills large tool outputs to sibling files under
    a `tool-results/` directory that `ClaudeTranscriptAdapter.locate` never
    enumerates — it only walks `<session>.jsonl` and `subagents/`.  Those files are
    therefore an evidence blind spot exactly like the subagent transcripts were
    before binding existed.  Best-effort: an unreadable spill directory degrades
    silently rather than failing the whole archive step (the bound main transcript
    itself is still the primary evidence).
    """
    spill_dir = main_transcript.parent / main_transcript.stem / "tool-results"
    if not spill_dir.is_dir():
        return
    dest_root = _safe_directory(handle.capsule, Path("agents/tool_results"), create=True)
    for path in sorted(spill_dir.iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            body = path.read_bytes()
        except OSError:  # noqa: BLE001 - one unreadable spill file must not sink the rest
            continue
        atomic_write_bytes(dest_root / f"{path.name}.gz", _archive_plain_bytes(body))


def _first_url(*values: object) -> str | None:
    for value in values:
        if value is None:
            continue
        match = _URL_RE.search(value if isinstance(value, str) else canonical_json(value))
        if match:
            return match.group().rstrip(".,;)]}")
    return None


def _external_tool_rows(
    handle: RunHandle,
    binding: Mapping,
    normalized,
) -> list[dict]:
    """One row per visible external tool call, with its response blobbed."""
    results: dict[str, dict] = {}
    for item in normalized.items:
        if item.kind != "tool_result":
            continue
        key = tool_call_id(item.payload)
        if key is not None:
            results[key] = {"payload": dict(item.payload), "timestamp": item.timestamp}
    rows: list[dict] = []
    for item in normalized.items:
        if item.kind != "tool_request":
            continue
        payload = dict(item.payload)
        tool_name = payload.get("tool_name")
        if not is_external_tool(tool_name):
            continue
        key = tool_call_id(payload)
        result = results.get(key) if key is not None else None
        request_text = payload.get("input")
        if not isinstance(request_text, str):
            request_text = canonical_json(request_text)
        result_hash = None
        result_bytes = None
        status = "INCOMPLETE"
        completed_at = None
        content = None
        if result is not None:
            content = result["payload"].get("content")
            body = (
                content if isinstance(content, str) else canonical_json(content)
            ).encode("utf-8")
            result_hash = put_bytes(handle.capsule, body)
            result_bytes = len(body)
            status = "FAILED" if result["payload"].get("is_error") else "COMPLETED"
            completed_at = result["timestamp"]
        rows.append(
            {
                "schema_version": _TRANSCRIPT_SCHEMA_VERSION,
                "capture_level": "HARNESS_RESPONSE",
                "completed_at": completed_at,
                "engine": binding.get("engine"),
                "invocation_id": binding.get("invocation_id"),
                "namespace": payload.get("namespace"),
                "request": request_text,
                "requested_at": item.timestamp,
                "result_bytes": result_bytes,
                "result_hash": result_hash,
                "role": binding.get("role"),
                "stage": binding.get("stage"),
                "status": status,
                "subject": binding.get("subject"),
                "title": payload.get("title"),
                "tool_call_id": key,
                "tool_name": tool_name,
                "url": _first_url(request_text, content),
            }
        )
    return rows


def _operation_row(operation) -> dict:
    """One `ObservedOperation` -> a JSON-safe dict for `agents/normalized/*.json`.

    `item_index` (fix-round-1, 2026-09-13): position in this call's
    `NormalizedTranscript.items`, *not* an index into the snapshot's `rows`
    -- see `ObservedOperation`'s own docstring in `transcripts/base.py` for
    why the two sequences differ in length and cannot be conflated.
    """
    return {
        "kind": operation.kind,
        "call_id": operation.call_id,
        "tool_name": operation.tool_name,
        "path": operation.path,
        "path_source": operation.path_source,
        "item_index": operation.item_index,
        "response": (
            None
            if operation.response is None
            else {
                "sha256": operation.response.sha256,
                "byte_count": operation.response.byte_count,
                "encoding": operation.response.encoding,
            }
        ),
        "artifact": (
            None
            if operation.artifact is None
            else {
                "sha256": operation.artifact.sha256,
                "byte_count": operation.artifact.byte_count,
            }
        ),
    }


def _archive_bound_transcripts(
    handle: RunHandle,
    *,
    snapshot_cache: dict[str, TranscriptSnapshot] | None = None,
) -> dict[str, dict]:
    """Archive every bound transcript and return one evidence row per binding.

    Design §5.1 ("单源一次快照"): every fact derived below -- raw archive,
    normalized items/operations, per-invocation usage, all hashes -- comes
    from exactly one `snapshot.capture_snapshot` per unique source path,
    cached in ``snapshot_cache`` (built locally when the caller does not
    supply one, e.g. a direct test call; shared with `_write_usage` when
    called from `finalize()`, so one `finalize()` pass reads each bound
    source's bytes off disk exactly once in total). Two invocations bound to
    the same source (spec's "两个 invocation 共享源") therefore reuse the same
    in-memory snapshot rather than each triggering a fresh read, and the raw
    archive on disk is keyed by ``snapshot_id`` (a content hash) so the same
    source is written once regardless of how many invocations reference it.
    """
    bindings = _read_jsonl(_bindings_path(handle))
    raw_root = _safe_directory(handle.capsule, Path("agents/raw"), create=True)
    normalized_root = _safe_directory(
        handle.capsule, Path("agents/normalized"), create=True
    )
    lineage_path = handle.capsule / "lineage/external_tools.jsonl"
    recorded = {
        (row.get("invocation_id"), row.get("tool_call_id"))
        for row in _read_jsonl(lineage_path)
    }
    cache: dict[str, TranscriptSnapshot] = (
        {} if snapshot_cache is None else snapshot_cache
    )
    written_raw: set[str] = set()
    invocations: list[dict] = []
    pending_lineage: list[dict] = []
    for binding in bindings:
        engine = str(binding.get("engine"))
        invocation_id = str(binding.get("invocation_id"))
        row = {
            "engine": engine,
            "invocation_id": invocation_id,
            "role": binding.get("role"),
            "subject": binding.get("subject"),
            "stage": binding.get("stage"),
            "source_path": binding.get("path"),
            "status": "PRESENT",
            "reason": None,
            "raw": None,
            "normalized": None,
            "snapshot_id": None,
            "source_sha256": None,
            "source_bytes": None,
            "rows": None,
            "unparsed_rows": None,
            "items": None,
            "model": None,
            "effort": None,
            "usage": None,
        }
        source = Path(str(binding.get("path")))
        if source.is_symlink() or not source.is_file():
            row["status"] = "GONE"
            row["reason"] = "bound transcript is not a readable regular file"
            invocations.append(row)
            continue
        ref = TranscriptRef(
            engine=engine,
            path=source,
            status="PRESENT",
            role=str(binding.get("role") or "subagent"),
            subject=binding.get("subject"),
            invocation_id=invocation_id,
            session_ref=binding.get("session_ref"),
            start_ordinal=binding.get("start_ordinal"),
            end_ordinal=binding.get("end_ordinal"),
        )
        try:
            adapter = adapter_for(engine)
            cache_key = str(source)
            snapshot = cache.get(cache_key)
            if snapshot is None:
                snapshot = capture_snapshot(source, engine=engine)
                cache[cache_key] = snapshot
            if snapshot.source_changed:
                # spec §5.1: "无法保证同一前缀时 SOURCE_CHANGED，不混合重试内容" --
                # a truthful degrade, not a silent best-effort archive of
                # content that may not be one coherent prefix.
                raise RuntimeError(
                    "transcript source changed identity during capture "
                    "(SOURCE_CHANGED); not trusted as evidence"
                )
            stats = adapter.stats_from_rows(snapshot.rows, ref)
        except Exception as exc:  # noqa: BLE001 - an unreadable transcript is a fact
            row["status"] = "UNSUPPORTED"
            row["reason"] = _safe_exception_text(exc)
            invocations.append(row)
            continue
        normalized = stats.normalized
        usage = stats.usage
        raw_path = raw_root / f"{snapshot.snapshot_id}.jsonl.gz"
        normalized_path = normalized_root / f"{invocation_id}.json"
        if snapshot.snapshot_id not in written_raw:
            atomic_write_bytes(raw_path, snapshot.archive_bytes)
            written_raw.add(snapshot.snapshot_id)
        atomic_write_json(
            normalized_path,
            {
                "schema_version": _TRANSCRIPT_SCHEMA_VERSION,
                "engine": engine,
                "invocation_id": invocation_id,
                "role": normalized.ref.role,
                "subject": normalized.ref.subject,
                "status": normalized.status,
                "model": normalized.model,
                "effort": normalized.effort,
                "snapshot_id": snapshot.snapshot_id,
                "items": [
                    {
                        "index": item.index,
                        "kind": item.kind,
                        "payload": json.loads(canonical_json(dict(item.payload))),
                        "timestamp": item.timestamp,
                    }
                    for item in normalized.items
                ],
                "operations": [_operation_row(op) for op in stats.operations],
            },
        )
        if engine == "claude" and str(binding.get("role")) == "main":
            _archive_tool_results_spill(handle, source)
        row.update(
            {
                "raw": raw_path.relative_to(handle.capsule).as_posix(),
                "normalized": normalized_path.relative_to(handle.capsule).as_posix(),
                "snapshot_id": snapshot.snapshot_id,
                "source_sha256": snapshot.source_prefix.sha256,
                "source_bytes": snapshot.source_prefix.byte_count,
                "rows": len(snapshot.rows),
                "unparsed_rows": snapshot.bad_lines,
                "items": len(normalized.items),
                "model": normalized.model,
                "effort": normalized.effort,
                "transcript_status": normalized.status,
                "usage": {
                    "messages": usage.messages,
                    "input": usage.input,
                    "output": usage.output,
                    "cache_read": usage.cache_read,
                    "cache_create": usage.cache_create,
                    "reasoning_output": usage.reasoning_output,
                    "status": usage.status,
                },
            }
        )
        invocations.append(row)
        pending_lineage.extend(
            item
            for item in _external_tool_rows(handle, binding, normalized)
            if (item["invocation_id"], item["tool_call_id"]) not in recorded
        )
    if pending_lineage:
        _safe_directory(handle.capsule, Path("lineage"), create=True)
        _append_locked_jsonl(lineage_path, pending_lineage)
    return {row["invocation_id"]: row for row in invocations}


# Deterministic relays: their evidence is the captured command in ``logs/``, not
# an LLM transcript, so requiring one would make completeness permanently false.
_NON_TRANSCRIPT_ROLES = frozenset({"trace-control", "gp-shell"})


def _agent_expectations(handle: RunHandle) -> dict[str, dict]:
    """One row per *reached* agent invocation, taken from the event chain.

    The chain is the authority for what ran: a failed dispatch still owes a row,
    and a leg that was never reached simply has no event.
    """
    rows: dict[str, dict] = {}
    path = handle.capsule / "events/events.jsonl"
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event_type") not in _AGENT_EVENT_TYPES:
            continue
        payload = event.get("payload") or {}
        result = payload.get("result") or {}
        invocation_id = str(event.get("invocation_id"))
        row = rows.setdefault(
            invocation_id,
            {
                "invocation_id": invocation_id,
                "role": payload.get("role"),
                "subject": event.get("subject"),
                "subject_key": event.get("subject"),
                "attempt": event.get("attempt"),
                "dispatched": False,
                "terminal": None,
            },
        )
        display = result.get("subject_display") if isinstance(result, Mapping) else None
        if isinstance(display, str) and display:
            row["subject"] = display
        event_type = event["event_type"]
        if event_type == "AGENT_DISPATCHED":
            row["dispatched"] = True
        elif event_type == "AGENT_COMPLETED":
            row["terminal"] = "COMPLETED"
        elif event_type == "AGENT_FAILED":
            row["terminal"] = "FAILED"
    return rows


def materialize_agent_index(
    run_id: str,
    *,
    not_expected: Sequence[str] = (),
    snapshot_cache: dict[str, TranscriptSnapshot] | None = None,
) -> dict:
    """Archive bound transcripts and account for **every** reached invocation.

    Coverage is never inferred from an absent directory: a dispatch with no
    bound transcript is an explicit ``GONE`` row, a role that structurally has
    no transcript is ``NOT_EXPECTED``, and a failed dispatch still gets a row.

    ``snapshot_cache``: optional, keyed by resolved source path -- passed
    through unchanged to `_archive_bound_transcripts`. ``None`` (every
    pre-Task-2 caller, and this function's own default) keeps this call
    self-contained (it builds and discards its own cache); `finalize()`
    passes one shared dict so this call and the `_write_usage` call moments
    later read each bound source exactly once in total.
    """
    handle = require_active_run(run_id)
    skipped_roles = _NON_TRANSCRIPT_ROLES | {str(role) for role in not_expected}
    evidence = _archive_bound_transcripts(handle, snapshot_cache=snapshot_cache)
    expectations = _agent_expectations(handle)

    invocations: list[dict] = []
    for invocation_id in sorted(set(expectations) | set(evidence)):
        expectation = expectations.get(invocation_id, {})
        row = dict(
            evidence.get(invocation_id)
            or {
                "engine": handle.engine,
                "invocation_id": invocation_id,
                "role": expectation.get("role"),
                "subject": expectation.get("subject"),
                "stage": None,
                "source_path": None,
                "status": "GONE",
                "reason": "reached dispatch has no bound transcript",
                "raw": None,
                "normalized": None,
                "snapshot_id": None,
                "source_sha256": None,
                "source_bytes": None,
                "rows": None,
                "unparsed_rows": None,
                "items": None,
                "model": None,
                "effort": None,
                "usage": None,
            }
        )
        if expectation:
            row["role"] = expectation.get("role") or row.get("role")
            row["subject"] = expectation.get("subject")
            row["attempt"] = expectation.get("attempt")
            row["dispatched"] = expectation.get("dispatched", False)
            row["terminal"] = expectation.get("terminal")
        else:
            row.setdefault("attempt", 1)
            row["dispatched"] = False
            row["terminal"] = None
            row["reason"] = row.get("reason") or "bound without a dispatch event"
        if str(row.get("role")) in skipped_roles:
            row["status"] = "NOT_EXPECTED"
            row["reason"] = "role has no transcript evidence by construction"
        row["expected"] = row["status"] != "NOT_EXPECTED"
        invocations.append(row)

    invocations.sort(
        key=lambda item: (
            str(item["role"]),
            str(item["subject"] or ""),
            str(item["invocation_id"]),
        )
    )
    expected = [item for item in invocations if item["expected"]]
    present = sum(1 for item in expected if item["status"] == "PRESENT")
    index = {
        "schema_version": _TRANSCRIPT_SCHEMA_VERSION,
        "run_id": handle.run_id,
        "invocations": invocations,
        "coverage": {
            "expected": len(expected),
            "present": present,
            "missing": len(expected) - present,
        },
    }
    atomic_write_json(handle.capsule / "agents/index.json", index)
    append_event(
        handle.capsule / "events/events.jsonl",
        run_id=handle.run_id,
        engine=handle.engine,
        stage=_validate_stage(
            # `materialize_agent_index` is finalize's own step 1 (see `finalize`'s
            # docstring/body above) -- the fallback used to be the literal "cp7",
            # a name `contracts.stages.STAGES` has never heard of (2026-09-12
            # scene-reconstruction Task 1: "stage 来自契约词表").
            str(os.environ.get("AUTORESEARCH_STAGE", "")).strip() or "finalize"
        ),
        invocation_id=f"transcripts-{handle.run_id}",
        attempt=1,
        subject=None,
        event_type="TRANSCRIPTS_MATERIALIZED",
        payload={"coverage": index["coverage"]},
    )
    return index


def materialize_transcripts(run_id: str, *, not_expected: Sequence[str] = ()) -> dict:
    """Compatibility name for :func:`materialize_agent_index`."""
    return materialize_agent_index(run_id, not_expected=not_expected)


# ------------------------------------------------------------------ finalization

MANIFEST_NAME = "MANIFEST.sha256"
ROOT_NAME = "ROOT.json"
LEDGER_NAME = "run_capsules.jsonl"
CAPSULE_SCHEMA_VERSION = 1
_GENESIS_ROW_HASH = "0" * 64
# Excluded from the manifest they would otherwise have to describe: the manifest
# cannot list its own hash, and ROOT must stay *outside* the cycle it anchors.
_MANIFEST_EXCLUSIONS = (
    f"capsule/verification/{MANIFEST_NAME}",
    f"capsule/verification/{ROOT_NAME}",
)


# 四个跨 run 的根都按 kind 分家:`reports_<engine>/scan/_ledger` 与
# `reports_<engine>/analyze/_ledger` 是两本独立账本。合本会让 `_find_final_path`
# 在一本账里找另一个技能的 run,而 `revision` 的连号校验(`read_valid_ledger`)
# 也会被另一个技能的写入打断 —— 两个技能并发跑时那是必然,不是偶然。
# 缺省 `scan-market` 保证既有调用点(`scan/ledger_views.py`、测试)一字不改。


def ledger_path(kind: str = "scan-market") -> Path:
    return ws.run_reports_root(kind) / "_ledger" / LEDGER_NAME


def failed_root(kind: str = "scan-market") -> Path:
    return ws.run_reports_root(kind) / "_failed"


def archive_root(kind: str = "scan-market") -> Path:
    return ws.run_reports_root(kind) / "_capsule_archive"


def _iter_manifest_files(final_path: Path) -> list[Path]:
    files = []
    for path in sorted(final_path.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(final_path).as_posix()
        if relative in _MANIFEST_EXCLUSIONS:
            continue
        files.append(path)
    return files


def write_manifest(final_path: Path | str) -> Path:
    """Hash every frozen file into the standard ``sha256sum`` format."""
    root = Path(final_path)
    lines = [
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}\n"
        for path in _iter_manifest_files(root)
    ]
    target = root / "capsule" / "verification" / MANIFEST_NAME
    return atomic_write_bytes(target, "".join(lines).encode("utf-8"))


def read_manifest(final_path: Path | str) -> dict[str, str]:
    path = Path(final_path) / "capsule" / "verification" / MANIFEST_NAME
    entries: dict[str, str] = {}
    if not path.is_file():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, _, relative = line.partition("  ")
        if digest and relative:
            entries[relative] = digest
    return entries


def verify_manifest(final_path: Path | str) -> dict:
    """Compare the frozen listing against what is on disk right now."""
    root = Path(final_path)
    listed = read_manifest(root)
    actual = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in _iter_manifest_files(root)
    }
    changed = sorted(k for k, v in listed.items() if k in actual and actual[k] != v)
    missing = sorted(set(listed) - set(actual))
    extra = sorted(set(actual) - set(listed))
    return {
        "ok": not (changed or missing or extra) and bool(listed),
        "changed": changed,
        "missing": missing,
        "extra": extra,
        "files": len(listed),
    }


def _event_chain_tail(capsule: Path) -> str | None:
    path = capsule / "events/events.jsonl"
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return None
    return json.loads(lines[-1]).get("event_hash")


def read_valid_ledger(
    path: Path | str | None = None, *, kind: str = "scan-market"
) -> list[dict]:
    """Return the ledger prefix that is still a valid append-only chain.

    Validation stops at the first broken ``prev_hash`` or per-run revision gap:
    a corrupted tail must not be able to erase or rewrite the rows before it.
    """
    target = Path(path) if path is not None else ledger_path(kind)
    if not target.is_file():
        return []
    rows: list[dict] = []
    previous = _GENESIS_ROW_HASH
    seen: dict[str, int] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001 - a corrupt tail truncates, never rewrites
            break
        body = {key: value for key, value in row.items() if key != "row_hash"}
        if row.get("prev_hash") != previous:
            break
        if sha256_bytes(canonical_json(body).encode("utf-8")) != row.get("row_hash"):
            break
        run_id = str(row.get("run_id"))
        revision = row.get("revision")
        if type(revision) is not int or revision != seen.get(run_id, 0) + 1:
            break
        seen[run_id] = revision
        previous = row["row_hash"]
        rows.append(row)
    return rows


def append_ledger_revision(row: Mapping, *, kind: str = "scan-market") -> dict:
    """Append one revision under an exclusive lock; identical rows are a no-op."""
    target = ledger_path(kind)

    def guard(existing: list[dict]):
        for item in existing:
            if (
                item.get("run_id") == row.get("run_id")
                and item.get("revision") == row.get("revision")
                and item.get("root_hash") == row.get("root_hash")
            ):
                return [item]
        return None

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            existing = [
                json.loads(line)
                for line in handle.read().decode("utf-8").splitlines()
                if line.strip()
            ]
            duplicate = guard(existing)
            if duplicate is not None:
                return duplicate[0]
            valid = read_valid_ledger(target, kind=kind)
            previous = valid[-1]["row_hash"] if valid else _GENESIS_ROW_HASH
            revision = (
                max(
                    (
                        int(item.get("revision") or 0)
                        for item in valid
                        if item.get("run_id") == row.get("run_id")
                    ),
                    default=0,
                )
                + 1
            )
            body = {**dict(row), "prev_hash": previous, "revision": revision}
            body.pop("row_hash", None)
            final_row = {
                **body,
                "row_hash": sha256_bytes(canonical_json(body).encode("utf-8")),
            }
            handle.seek(0, os.SEEK_END)
            handle.write((canonical_json(final_row) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    _fsync_dir(target.parent)
    return final_row


def build_archive(
    final_path: Path | str, run_id: str, *, kind: str = "scan-market"
) -> Path:
    """Write one deterministic, self-contained ``.tar.zst`` outside the report tree."""
    import tarfile

    import zstandard

    root = Path(final_path)
    destination = archive_root(kind) / f"{run_id}.tar.zst"
    destination.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                continue
            info = archive.gettarinfo(str(path), arcname=path.relative_to(root).as_posix())
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if path.is_dir() else 0o644
            if path.is_file():
                with path.open("rb") as handle:
                    archive.addfile(info, handle)
            else:
                archive.addfile(info)
    payload = zstandard.ZstdCompressor(level=10).compress(buffer.getvalue())
    return atomic_write_bytes(destination, payload)


def verify_archive(archive: Path | str, root_hash: str | None = None) -> dict:
    """Read the archive back and confirm it still carries the anchored ROOT."""
    import tarfile

    import zstandard

    path = Path(archive)
    if not path.is_file():
        return {"ok": False, "reason": "archive is missing", "members": 0}
    try:
        raw = zstandard.ZstdDecompressor().decompress(
            path.read_bytes(), max_output_size=1 << 31
        )
        members: list[str] = []
        found_root = None
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as bundle:
            for info in bundle.getmembers():
                members.append(info.name)
                if info.name == f"capsule/verification/{ROOT_NAME}":
                    extracted = bundle.extractfile(info)
                    if extracted is not None:
                        found_root = json.loads(extracted.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - an unreadable archive is a finding
        return {"ok": False, "reason": _safe_exception_text(exc), "members": 0}
    if root_hash is not None and (found_root or {}).get("root_hash") != root_hash:
        return {
            "ok": False,
            "reason": "archived ROOT does not carry the expected root hash",
            "members": len(members),
        }
    return {"ok": True, "reason": None, "members": len(members)}


def _last_reliable_checkpoint(capsule: Path) -> str | None:
    """The newest stage that actually completed — ordered by time, not by name.

    Globbing ``stages/*`` yields alphabetical order, where ``prelude`` sorts
    after ``gate4``; a run through the whole pipeline would then report that it
    stopped at prelude.  The attempt's own ``created_at`` is the only ordering
    that means anything here.
    """
    best: tuple[str, str] | None = None
    for result in (capsule / "stages").glob("*/attempt-*/result.json"):
        try:
            payload = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") not in {"SUCCEEDED", "DEGRADED"}:
            continue
        stamp = str(payload.get("created_at") or "")
        stage = payload.get("stage")
        if stage and (best is None or stamp > best[0]):
            best = (stamp, str(stage))
    return best[1] if best else None


def _freeze_tree(final_path: Path) -> None:
    """Read-only after every write: a frozen run must not be edited in place."""
    for path in sorted(final_path.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        with contextlib.suppress(OSError):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
    with contextlib.suppress(OSError):
        os.chmod(final_path, 0o555)


def _thaw_tree(path: Path) -> None:
    for item in sorted(path.rglob("*")):
        with contextlib.suppress(OSError):
            os.chmod(item, 0o755 if item.is_dir() else 0o644)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o755)


def _write_capsule_manifest(
    handle: RunHandle,
    *,
    final_path: Path,
    business_status: BusinessStatus,
    evidence_status: EvidenceStatus,
    replayability: Replayability,
    checkpoint_name: str | None,
) -> Path:
    return atomic_write_json(
        handle.capsule / "capsule.json",
        {
            "capsule_schema_version": CAPSULE_SCHEMA_VERSION,
            "run_id": handle.run_id,
            "analysis_date": handle.analysis_date,
            "engine": handle.engine,
            "business_status": business_status.value,
            "evidence_status": evidence_status.value,
            "replayability": replayability.value,
            "contract_hash": handle.contract.contract_hash,
            "final_path": str(final_path),
            "last_reliable_checkpoint": checkpoint_name,
        },
    )


def _write_usage(
    handle: RunHandle,
    *,
    snapshot_cache: dict[str, TranscriptSnapshot] | None = None,
) -> None:
    """Write the run-level usage ledger.

    ``snapshot_cache``: optional, passed through to
    `usage_harvest.collect_run` so a source this run already captured (via
    `materialize_agent_index` moments earlier in `finalize()`) is not read a
    second time. ``None`` keeps every other, non-`finalize()` caller of this
    function reading through `collect_run`'s own self-contained cache,
    unchanged from before this task -- the standalone `usage_harvest` CLI
    never reaches this function at all (ruling 2: it must not gain a
    bindings dependency), so this parameter is exercised only inside
    `finalize()`.
    """
    from autoresearch.trace import usage_harvest

    rows = usage_harvest.collect_run(
        handle.run_id, engine=handle.engine, snapshot_cache=snapshot_cache
    )
    source = f"run:{handle.run_id}"
    atomic_write_json(
        handle.capsule / "usage/_token_usage.json",
        usage_harvest.build_ledger(rows, source=source),
    )
    atomic_write_bytes(
        handle.capsule / "usage/token_usage.md",
        (usage_harvest.render(rows, sub_dir=source) + "\n").encode("utf-8"),
    )


def _degrade_evidence(handle: RunHandle, endpoint: str, reason: str) -> None:
    """Book one B-tier evidence degradation — 「降级不留痕」才是真病。

    The endpoints are namespaced `capsule.*` on purpose: they are *evidence* legs,
    not data endpoints, and nobody should mistake them for a lake fetch failing.
    """
    try:
        from autoresearch.data.contracts import record_degradation

        record_degradation(endpoint, reason, key=handle.run_id)
    except Exception:  # noqa: BLE001 - a bookkeeping failure must still be visible
        print(f"[capsule·B级降级] {endpoint}[{handle.run_id}]:{reason}", file=sys.stderr)


def _resolve_run_mode(handle: RunHandle) -> str:
    """Which mode this run actually ran in — one half of the completeness denominator.

    `finalize` used to call `scan_profile(business_status=…, last_stage=…)` with **no
    mode** (spec 2026-08-29 §2.2 K3), so every run was expanded as `FULL`: a sentinel
    run has no L4 leg, yet `l4-card` / `l4-intel` were marked REQUIRED and the
    completeness verdict was false for every sentinel run by construction.

    The fallback is `"FULL"` because it is the **widest** expectation — failing to read
    the mode can only make the verdict stricter, never manufacture a false green.  It is
    still a degradation, and it is booked.

    `stock-research` 没有 `run_mode.json`:它的档(FULL / LITE)在开跑那一刻就冻进了
    契约的 `user_config`(`analyze/run_bootstrap.prepare_analyze_run` 写的 config echo),
    再去 staging 里找一个永远不存在的文件,只会给每一趟单票研究记一条假降级。
    """
    if handle.contract.run_kind != "scan-market":
        from autoresearch.contracts.stages import ANALYZE_MODES

        mode = str((handle.contract.user_config or {}).get("mode") or "")
        if mode in ANALYZE_MODES:
            return mode
        _degrade_evidence(
            handle,
            "capsule.run_mode",
            f"契约 config echo 的 mode={mode!r} 不在 {ANALYZE_MODES} → 按 FULL 展开",
        )
        return "FULL"

    # 模式词汇取自契约层(`scan.run_profile.MODES` 本来就是 `vocab.MODES` 的同一个对象);
    # 从这里取少一条 trace → scan 的边,棘轮方向正确。
    from autoresearch.contracts.stages import MODES

    path = handle.staging / "run_mode.json"
    if not path.is_file():
        _degrade_evidence(
            handle, "capsule.run_mode", "run_mode.json 缺席 → 完整性按最宽的 FULL 展开"
        )
        return "FULL"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        mode = str((payload or {}).get("mode") or "")
    except Exception as exc:  # noqa: BLE001 - unreadable mode ≠ no mode
        _degrade_evidence(
            handle,
            "capsule.run_mode",
            f"run_mode.json 读不动/非法 JSON({_safe_exception_text(exc)})→ 按 FULL 展开",
        )
        return "FULL"
    if mode not in MODES:
        _degrade_evidence(
            handle,
            "capsule.run_mode",
            f"run_mode.json mode={mode!r} 不在 {MODES} → 按 FULL 展开",
        )
        return "FULL"
    return mode


def _materialize_external_evidence(handle: RunHandle) -> None:
    """D-5 留痕(外源扩面稿 2026-08-28 §6.1/§6.2)的第 2、3 步 —— 此前**建成未接线**。

    两个 materializer 有模块、有测试,`autoresearch/` 内**零调用者**(spec 2026-08-29
    §1.3),于是真跑既没有 `web_budget.json` 也没有 `external_evidence_index.json`。

    顺序是有意的:两者都读 `materialize_agent_index` 才生成的 `lineage/external_tools.jsonl`,
    倒着跑会安静地产出一份「什么都没查」的现场(`tests/trace/test_evidence_index.py` 的
    定序用例)。位置也是有意的:在 MANIFEST 之前,清单才盖得住它俩。

    它们是 **B 级证据**:失败记账、**不阻断** finalize —— 一个索引 bug 不该毙掉整趟现场。

    参数只给**capsule 自己推得出来**的那几个(`run_id`、`staging`)。多给一个 finalize 才
    知道的字段(engine / caps),冻结后按 §11 「二次 materialize 字节不变」重跑就会产出不同
    字节,而 `write_if_changed` 对冻结文件的正确反应是**抛**(不偷改历史)—— 那等于把审计
    路堵死。engine 在 `identity/run_contract.json` 里本来就有,不必在预算里再抄一份。
    """
    from autoresearch.trace import (
        evidence_index as evidence_index_mod,
        web_budget as web_budget_mod,
    )

    try:
        budget = web_budget_mod.materialize_web_budget(
            handle.capsule,
            run_id=handle.run_id,
        )
        # materializer 落的是设计稿 §6.1 的原址 `capsule/lineage/web_budget.json`;
        # 生产 lint 的读点定在 `capsule/usage/web_budget.json`(计划 2026-08-29 P0 T1/T4)。
        # 同一份 canonical 字节写两处,读侧不会读到第二个真身。
        web_budget_mod.write_budget(
            handle.capsule / "usage" / web_budget_mod.BUDGET_NAME, budget
        )
    except Exception as exc:  # noqa: BLE001 - B-tier evidence never kills the scene
        _degrade_evidence(
            handle, "capsule.web_budget", f"web 预算物化失败:{_safe_exception_text(exc)}"
        )

    try:
        evidence_index_mod.materialize_evidence_index(
            handle.capsule,
            run_id=handle.run_id,
            staging=handle.staging,
        )
    except Exception as exc:  # noqa: BLE001 - B-tier evidence never kills the scene
        _degrade_evidence(
            handle,
            "capsule.external_evidence_index",
            f"外源证据索引物化失败:{_safe_exception_text(exc)}",
        )


def _resolve_final_path(
    handle: RunHandle,
    business_status: BusinessStatus,
    report_dir: Path | None,
) -> Path:
    if business_status == BusinessStatus.SUCCEEDED:
        if report_dir is None:
            raise ValueError("a SUCCEEDED run must name its published report_dir")
        return report_dir
    target = failed_root(handle.contract.run_kind) / handle.run_id
    target.mkdir(parents=True, exist_ok=True)
    return target


def _publish_capsule(handle: RunHandle, final_path: Path) -> Path:
    destination = final_path / "capsule"
    if destination.exists():
        _thaw_tree(destination)
        shutil.rmtree(destination)
    shutil.copytree(handle.capsule, destination, symlinks=False)
    return destination


def finalize(
    run_id: str,
    business_status: BusinessStatus | str,
    report_dir: Path | str | None = None,
    *,
    error: Mapping | None = None,
    replay_stages: Sequence[str] = (),
    profile=None,
    now: datetime | None = None,
) -> FinalizationResult:
    """Freeze one run into a read-only, root-anchored, self-contained capsule.

    The order is fixed and each step depends only on the ones before it:
    transcripts and usage → expected/completeness/replay → capsule.json and
    terminal state → MANIFEST → root → ROOT → archive → ledger → freeze →
    reopen and verify.  Archive failure degrades *evidence*, never the business
    report: the published run stays exactly where it is.
    """
    from autoresearch.trace import completeness as completeness_mod, replay as replay_mod

    resolved_status = BusinessStatus(business_status)
    if resolved_status == BusinessStatus.ACTIVE:
        raise ValueError("finalize needs a terminal business status")
    handle = load_run(run_id)
    run_kind = handle.contract.run_kind
    state = _state_from_path(handle.workspace / "state.json", run_id=handle.run_id)
    resolved_report = _validate_report_dir(report_dir, kind=run_kind)
    final_path = _resolve_final_path(handle, resolved_status, resolved_report)

    if state.business_status != BusinessStatus.ACTIVE:
        # Already frozen: re-finalizing must be a no-op that returns the same root.
        existing = _load_root(final_path)
        if existing is not None and state.business_status == resolved_status:
            return FinalizationResult(
                run_id=handle.run_id,
                business_status=state.business_status,
                evidence_status=state.evidence_status,
                replayability=state.replayability,
                final_path=final_path,
                root_hash=existing.get("root_hash"),
                archive=(
                    archive_root(run_kind) / f"{handle.run_id}.tar.zst"
                    if (archive_root(run_kind) / f"{handle.run_id}.tar.zst").is_file()
                    else None
                ),
                durability=str(existing.get("durability") or ""),
                last_reliable_checkpoint=existing.get("last_reliable_checkpoint"),
            )
        raise RuntimeError(
            f"run {handle.run_id} is already {state.business_status.value}"
        )

    # 1. transcripts and truthful usage, then the two D-5 indexes that read them
    # One snapshot cache shared across both calls (design §5.1): every bound
    # source this finalize pass touches is read off disk at most once in
    # total, not once per call that happens to need it. Ordering unchanged
    # (materialize before usage) -- only what each call is handed differs.
    finalize_snapshot_cache: dict[str, TranscriptSnapshot] = {}
    materialize_agent_index(handle.run_id, snapshot_cache=finalize_snapshot_cache)
    _write_usage(handle, snapshot_cache=finalize_snapshot_cache)
    _materialize_external_evidence(handle)

    # 2. 阶段产物快照:capsule 必须自带业务产物,否则重放没有比对基准、
    #    归档也不是自足的(设计稿 §7 products/)。
    if handle.staging.is_dir():
        shutil.copytree(
            handle.staging,
            handle.capsule / "products/staging",
            dirs_exist_ok=True,
        )

    # 3. expected / completeness / replay
    # profile 工厂按 run kind 现取(`contracts.profiles.PROFILE_FACTORIES`)——
    # 静态 import 任何一个技能包都是 `trace` 向上的边。
    resolved_profile = profile or profile_factory(run_kind)(
        mode=_resolve_run_mode(handle),
        business_status=resolved_status.value,
        last_stage=_last_reliable_checkpoint(handle.capsule),
    )
    completeness_mod.write_expected(handle.capsule, resolved_profile)
    replay_mod.replay(
        handle.run_id,
        capsule=handle.capsule,
        analysis_date=handle.analysis_date,
        stages=tuple(replay_stages),
        keep_scratch=False,
        kind=run_kind,
    )
    checkpoint_name = _last_reliable_checkpoint(handle.capsule)
    if resolved_status != BusinessStatus.SUCCEEDED:
        atomic_write_json(
            handle.capsule / "failure.json",
            {
                "schema_version": CAPSULE_SCHEMA_VERSION,
                "run_id": handle.run_id,
                "business_status": resolved_status.value,
                "last_reliable_checkpoint": checkpoint_name,
                "error": json.loads(canonical_json(redact_value(dict(error or {})).value)),
            },
        )
    # capsule.json 也是一条 REQUIRED 规则,所以先写一版 PENDING 再评估,
    # 评估完再把终态证据状态覆写回去(两次都在 MANIFEST 之前,清单只见终稿)。
    _write_capsule_manifest(
        handle,
        final_path=final_path,
        business_status=resolved_status,
        evidence_status=EvidenceStatus.PENDING,
        replayability=Replayability.NONE,
        checkpoint_name=checkpoint_name,
    )
    evidence = completeness_mod.write_completeness(
        handle.capsule, resolved_profile, durability="PENDING"
    )
    replayability = Replayability(
        completeness_mod.replay_state(handle.capsule)
        if completeness_mod.replay_state(handle.capsule) in {"FULL", "PARTIAL", "NONE"}
        else "NONE"
    )
    evidence_status = (
        EvidenceStatus.COMPLETE
        if evidence["completeness_ok"]
        else EvidenceStatus.EVIDENCE_INCOMPLETE
    )

    # 4. capsule.json 终稿 + terminal state
    _write_capsule_manifest(
        handle,
        final_path=final_path,
        business_status=resolved_status,
        evidence_status=evidence_status,
        replayability=replayability,
        checkpoint_name=checkpoint_name,
    )
    append_event(
        handle.capsule / "events/events.jsonl",
        run_id=handle.run_id,
        engine=handle.engine,
        stage="finalize",
        invocation_id=f"finalize-{handle.run_id}",
        attempt=1,
        subject=None,
        event_type=f"RUN_{resolved_status.value}",
        payload={
            "completeness_ok": evidence["completeness_ok"],
            "final_path": str(final_path),
        },
    )
    terminal = RunState.build(
        run_id=handle.run_id,
        business_status=resolved_status,
        evidence_status=evidence_status,
        replayability=replayability,
        now=now,
        previous=state,
    )
    _write_state(handle.workspace, terminal)

    published = _publish_capsule(handle, final_path)

    # 5-7. MANIFEST → root → detached ROOT
    durability, archive_path, archive_hash, archive_reason = "LOCAL_ONLY", None, None, None
    try:
        manifest_bytes = write_manifest(final_path).read_bytes()
        root_hash = sha256_bytes(manifest_bytes)
        _write_root(
            published,
            {
                "schema_version": CAPSULE_SCHEMA_VERSION,
                "run_id": handle.run_id,
                "root_hash": root_hash,
                "manifest_hash": sha256_bytes(manifest_bytes),
                "manifest_files": len(read_manifest(final_path)),
                "event_chain_tail": _event_chain_tail(published),
                "completeness_hash": sha256_file(
                    published / "verification/completeness.json"
                ),
                "business_status": resolved_status.value,
                "evidence_status": evidence_status.value,
                "durability": durability,
                "last_reliable_checkpoint": checkpoint_name,
                "archive_hash": None,
            },
        )
        # 8. archive outside the report directory
        archive_path = build_archive(final_path, handle.run_id, kind=run_kind)
        archive_hash = sha256_file(archive_path)
    except Exception as exc:  # noqa: BLE001 - the business report survives this
        archive_path = None
        archive_hash = None
        durability = "ARCHIVE_FAILED"
        archive_reason = _safe_exception_text(exc)
        evidence_status = EvidenceStatus.EVIDENCE_INCOMPLETE
        append_event(
            handle.capsule / "events/events.jsonl",
            run_id=handle.run_id,
            engine=handle.engine,
            stage="finalize",
            invocation_id=f"finalize-archive-{handle.run_id}",
            attempt=1,
            subject=None,
            event_type="EVIDENCE_MISSING",
            payload={"phase": "archive", "reason": archive_reason},
        )
        # Those three files changed, so completeness → MANIFEST → ROOT are redone.
        evidence = completeness_mod.evaluate(
            handle.capsule, resolved_profile, durability=durability
        )
        evidence["completeness_ok"] = False
        evidence["warnings"] = [
            *evidence.get("warnings", []),
            f"archive could not be written: {archive_reason}",
        ]
        atomic_write_json(handle.capsule / "verification/completeness.json", evidence)
        # capsule.json 与 state.json 也必须跟着降级 —— 否则 evidence_facts() 读到的
        # 还是归档失败**之前**那句 COMPLETE,展示层就又有了一个假绿灯。
        _write_capsule_manifest(
            handle,
            final_path=final_path,
            business_status=resolved_status,
            evidence_status=evidence_status,
            replayability=replayability,
            checkpoint_name=checkpoint_name,
        )
        _write_state(
            handle.workspace,
            RunState.build(
                run_id=handle.run_id,
                business_status=resolved_status,
                evidence_status=evidence_status,
                replayability=replayability,
                now=now,
                previous=terminal,
            ),
        )
        published = _publish_capsule(handle, final_path)
        manifest_bytes = write_manifest(final_path).read_bytes()
        root_hash = sha256_bytes(manifest_bytes)
        _write_root(
            published,
            {
                "schema_version": CAPSULE_SCHEMA_VERSION,
                "run_id": handle.run_id,
                "root_hash": root_hash,
                "manifest_hash": sha256_bytes(manifest_bytes),
                "manifest_files": len(read_manifest(final_path)),
                "event_chain_tail": _event_chain_tail(published),
                "completeness_hash": sha256_file(
                    published / "verification/completeness.json"
                ),
                "business_status": resolved_status.value,
                "evidence_status": evidence_status.value,
                "durability": durability,
                "last_reliable_checkpoint": checkpoint_name,
                "archive_hash": None,
            },
        )

    # 9. one locked ledger revision
    append_ledger_revision(
        {
            "schema_version": CAPSULE_SCHEMA_VERSION,
            "run_id": handle.run_id,
            "analysis_date": handle.analysis_date,
            "engine": handle.engine,
            "business_status": resolved_status.value,
            "evidence_status": evidence_status.value,
            "final_path": str(final_path),
            "root_hash": root_hash,
            "archive_hash": archive_hash,
            "durability": durability,
            "failure_class": archive_reason,
            "archived_at": terminal.updated_at,
        },
        kind=run_kind,
    )

    # 10. freeze, then 11. reopen and verify
    _freeze_tree(final_path)
    verify(handle.run_id, final_path=final_path, kind=run_kind)
    return FinalizationResult(
        run_id=handle.run_id,
        business_status=resolved_status,
        evidence_status=evidence_status,
        replayability=replayability,
        final_path=final_path,
        root_hash=root_hash,
        archive=archive_path,
        durability=durability,
        last_reliable_checkpoint=checkpoint_name,
    )


def _write_root(published: Path, payload: Mapping) -> Path:
    return atomic_write_json(published / "verification" / ROOT_NAME, dict(payload))


def _load_root(final_path: Path) -> dict | None:
    path = Path(final_path) / "capsule" / "verification" / ROOT_NAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_run_kind(run_id: str) -> str:
    """这趟 run 是哪个 kind —— 先问 spool 目录,再问两本账本,最后落回 `scan-market`。

    冻结之后 spool 目录仍在(`repair` 就是从那里取源),所以第一问通常够。
    两个都问不出来时落回 `scan-market`:那是 kind 化之前**唯一**存在过的 kind,
    也就是全部历史 run 的正确答案。
    """
    resolved = ws.validate_run_id(run_id)
    for kind in RUN_KINDS:
        if ws.run_root(kind, resolved).is_dir():
            return kind
    for kind in RUN_KINDS:
        if any(row.get("run_id") == resolved for row in read_valid_ledger(kind=kind)):
            return kind
        if (failed_root(kind) / resolved).is_dir():
            return kind
    return "scan-market"


def _find_final_path(run_id: str, *, kind: str = "scan-market") -> Path | None:
    for row in reversed(read_valid_ledger(kind=kind)):
        if row.get("run_id") == run_id:
            return Path(str(row.get("final_path")))
    candidate = failed_root(kind) / run_id
    return candidate if candidate.is_dir() else None


def verify(
    run_id: str,
    *,
    final_path: Path | str | None = None,
    kind: str | None = None,
) -> dict:
    """Answer integrity, completeness and replay **separately**, never as one ✓."""
    resolved_kind = kind or resolve_run_kind(run_id)
    root_path = (
        Path(final_path)
        if final_path is not None
        else _find_final_path(run_id, kind=resolved_kind)
    )
    if root_path is None or not root_path.is_dir():
        return {
            "run_id": run_id,
            "ok": False,
            "reason": "no frozen capsule for this run",
            "integrity_ok": False,
            "completeness_ok": False,
            "root_ledger_ok": False,
            "replayability": "NONE",
        }
    published = root_path / "capsule"
    manifest = verify_manifest(root_path)
    stored_root = _load_root(root_path) or {}
    manifest_path = published / "verification" / MANIFEST_NAME
    current_root = (
        sha256_bytes(manifest_path.read_bytes()) if manifest_path.is_file() else None
    )
    root_ok = bool(stored_root) and current_root == stored_root.get("root_hash")
    ledger_rows = [
        row
        for row in read_valid_ledger(kind=resolved_kind)
        if row.get("run_id") == run_id
    ]
    ledger_ok = bool(ledger_rows) and ledger_rows[-1].get(
        "root_hash"
    ) == stored_root.get("root_hash")
    completeness_path = published / "verification/completeness.json"
    evidence = (
        json.loads(completeness_path.read_text(encoding="utf-8"))
        if completeness_path.is_file()
        else {}
    )
    chain = verify_event_chain(published / "events/events.jsonl")
    archive = archive_root(resolved_kind) / f"{run_id}.tar.zst"
    return {
        "run_id": run_id,
        "final_path": str(root_path),
        "integrity_ok": bool(manifest["ok"]) and root_ok,
        "manifest": manifest,
        "root_ledger_ok": bool(root_ok and ledger_ok),
        "root_hash": stored_root.get("root_hash"),
        "event_chain_ok": bool(chain.get("ok")),
        "completeness_ok": bool(evidence.get("completeness_ok")),
        "missing_required": evidence.get("missing_required", []),
        "replayability": str(
            (evidence.get("coverage") or {}).get("replay") or "NONE"
        ),
        "durability": str(stored_root.get("durability") or ""),
        "business_status": stored_root.get("business_status"),
        "evidence_status": stored_root.get("evidence_status"),
        "archive": str(archive) if archive.is_file() else None,
        "archive_ok": verify_archive(archive, stored_root.get("root_hash"))["ok"]
        if archive.is_file()
        else False,
    }


# ---------------------------------------------------------- lease and recovery

HEARTBEAT_INTERVAL_SECONDS = 30
DEFAULT_STALE_AFTER = timedelta(minutes=5)


def read_lease(workspace: Path | str) -> dict | None:
    path = Path(workspace) / "state.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    lease = raw.get("lease")
    return lease if isinstance(lease, dict) else None


def lease_is_live(lease: Mapping | None) -> bool:
    """A lease is live only while its exact owning process still runs."""
    return process_probe.matches(lease)


def refresh_heartbeat(
    run_id: str,
    *,
    invocation_id: str | None = None,
    now: datetime | None = None,
    min_interval: float = HEARTBEAT_INTERVAL_SECONDS,
    force: bool = False,
) -> dict | None:
    """Renew this run's lease, at most once per ``min_interval`` seconds.

    Best-effort by construction: a run whose heartbeat cannot be written is a
    run that will look stale later, which is the safe direction.  It must never
    take down the work it is only observing.
    """
    try:
        workspace = ws.find_run_root(run_id)
        if workspace is None:
            return None
        state_path = workspace / "state.json"
        if not state_path.is_file():
            return None
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        if raw.get("business_status") != BusinessStatus.ACTIVE.value:
            return None
        stamp = _utc_now(now)
        previous = raw.get("lease") if isinstance(raw.get("lease"), dict) else None
        if not force and previous:
            try:
                last = datetime.fromisoformat(
                    str(previous.get("heartbeat")).replace("Z", "+00:00")
                )
                if (stamp - last).total_seconds() < min_interval:
                    return previous
            except (TypeError, ValueError):
                pass
        lease = process_probe.current_lease(
            invocation_id=invocation_id
            or (str(os.environ.get("AUTORESEARCH_INVOCATION_ID", "")).strip() or None),
            heartbeat=stamp.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        )
        atomic_write_json(state_path, {**raw, "lease": lease})
        return lease
    except Exception:  # noqa: BLE001 - observation must never break the observed
        return None


@dataclass(frozen=True)
class RecoveryResult:
    run_id: str
    business_status: str
    final_path: Path | None
    reason: str
    finalized: bool


def recover_stale_runs(
    *,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    engine_root: Path | None = None,
) -> list[RecoveryResult]:
    """Freeze runs whose owning process is gone, and only those.

    Two independent conditions must both hold: the heartbeat is older than
    ``stale_after`` *and* the recorded (pid, start time) identity no longer
    matches a live process.  Either one alone produces false positives — a
    paused run looks silent, and a recycled pid looks alive.
    """
    # `engine_root` 显式给了就只扫那一个(测试与运维定点用);否则**每个 kind 的池子
    # 都要扫** —— 一趟中断的单票研究和一趟中断的扫描一样会占着 ACTIVE 状态,漏扫等于
    # 它永远不会被冻结。
    roots = (
        [Path(engine_root)]
        if engine_root is not None
        else [ws.context_root() / spool for spool in ws.RUN_SPOOLS.values()]
    )
    stamp = _utc_now(now)
    results: list[RecoveryResult] = []
    workspaces = [
        item
        for root in roots
        if root.is_dir()
        for item in sorted(root.iterdir())
    ]
    for workspace in workspaces:
        if not workspace.is_dir() or workspace.is_symlink():
            continue
        state_path = workspace / "state.json"
        if not state_path.is_file():
            continue
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - an unreadable spool is left alone
            continue
        if raw.get("business_status") != BusinessStatus.ACTIVE.value:
            continue
        lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else None
        heartbeat = str((lease or {}).get("heartbeat") or raw.get("updated_at") or "")
        try:
            last = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
        except ValueError:
            continue
        if (stamp - last) < stale_after:
            continue
        if lease_is_live(lease):
            continue
        run_id = str(raw.get("run_id") or workspace.name)
        try:
            outcome = finalize(
                run_id,
                BusinessStatus.INTERRUPTED,
                error={
                    "error_type": "RunInterrupted",
                    "reason": "owning process is gone and the heartbeat went stale",
                    "last_heartbeat": heartbeat,
                },
                now=stamp,
            )
        except Exception as exc:  # noqa: BLE001 - never delete what we could not freeze
            results.append(
                RecoveryResult(
                    run_id=run_id,
                    business_status=BusinessStatus.ACTIVE.value,
                    final_path=None,
                    reason=_safe_exception_text(exc),
                    finalized=False,
                )
            )
            continue
        results.append(
            RecoveryResult(
                run_id=run_id,
                business_status=outcome.business_status.value,
                final_path=outcome.final_path,
                reason="stale lease",
                finalized=True,
            )
        )
    return results


def recover_stale_runs_quietly(**kwargs) -> list[RecoveryResult]:
    """Recovery for pipeline entrypoints: it warns, it never blocks a new run."""
    try:
        results = recover_stale_runs(**kwargs)
    except Exception as exc:  # noqa: BLE001 - a new run must not die of an old one
        print(f"[capsule] 陈旧 run 恢复失败(不影响本次运行):{_safe_exception_text(exc)}")
        return []
    for item in results:
        state = "已冻结" if item.finalized else "未能冻结"
        print(f"[capsule] 恢复中断 run {item.run_id}:{state}({item.reason})")
    return results


# ------------------------------------------------------------------ repairs


def repairs_root(kind: str = "scan-market") -> Path:
    return ws.run_reports_root(kind) / "_repairs"


@dataclass(frozen=True)
class RepairResult:
    run_id: str
    revision: int
    base_root_hash: str
    composite_root_hash: str
    overlay_path: Path
    added: tuple[str, ...]
    reason: str


def repair(
    run_id: str,
    *,
    reason: str,
    source: Path | str | None = None,
) -> RepairResult:
    """Add missing evidence as an append-only overlay; never touch the base.

    A frozen capsule is frozen.  Restored evidence therefore lands in
    ``_repairs/<run_id>/revision-N/`` with its own MANIFEST, and the ledger gains
    a new revision whose composite root covers base plus every overlay.  Any
    path that already exists in the base view is a collision and fails.
    """
    if type(reason) is not str or not reason.strip():
        raise ValueError("a repair must state its reason")
    run_kind = resolve_run_kind(run_id)
    final_path = _find_final_path(run_id, kind=run_kind)
    if final_path is None or not final_path.is_dir():
        raise FileNotFoundError(f"no frozen capsule for run {run_id}")
    base_root = _load_root(final_path) or {}
    base_root_hash = str(base_root.get("root_hash") or "")
    if not base_root_hash:
        raise RuntimeError(f"run {run_id} has no anchored base root to repair against")

    ledger_rows = [
        row for row in read_valid_ledger(kind=run_kind) if row.get("run_id") == run_id
    ]
    revision = (ledger_rows[-1]["revision"] if ledger_rows else 0) + 1
    overlay = repairs_root(run_kind) / run_id / f"revision-{revision}"
    if overlay.exists():
        raise FileExistsError(f"overlay already exists: {overlay}")

    origin = (
        Path(source) if source is not None else ws.run_root(run_kind, run_id) / "capsule"
    )
    if not origin.is_dir():
        raise FileNotFoundError(f"no repair source for run {run_id}: {origin}")
    base_view = {
        path.relative_to(final_path).as_posix()
        for path in final_path.rglob("*")
        if path.is_file()
    }
    planned: list[tuple[Path, str]] = []
    collisions: list[str] = []
    for path in sorted(origin.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = (Path("capsule") / path.relative_to(origin)).as_posix()
        if relative in base_view:
            collisions.append(relative)
            continue
        planned.append((path, relative))
    # 先判后写:碰撞件被跳过、其余照补,会让操作者以为证据都补回来了。整笔失败才诚实。
    if collisions:
        shown = ", ".join(sorted(collisions)[:5])
        if len(collisions) > 5:
            shown += f", …(共 {len(collisions)} 件)"
        raise FileExistsError(
            f"repair collides with the frozen base, nothing was written: {shown}"
        )
    if not planned:
        raise RuntimeError("repair adds nothing: the source has no files to restore")
    added: list[str] = []
    for path, relative in planned:
        destination = overlay / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        added.append(relative)

    lines = [
        f"{sha256_file(overlay / name)}  {name}\n" for name in sorted(added)
    ]
    manifest = atomic_write_bytes(
        overlay / "verification" / MANIFEST_NAME, "".join(lines).encode("utf-8")
    )
    overlay_root = sha256_bytes(manifest.read_bytes())
    previous_overlays = sorted(
        item for item in (repairs_root(run_kind) / run_id).iterdir() if item.is_dir()
    )
    overlay_roots = []
    for item in previous_overlays:
        candidate = item / "verification" / MANIFEST_NAME
        if candidate.is_file():
            overlay_roots.append(sha256_bytes(candidate.read_bytes()))
    composite = sha256_bytes(
        canonical_json(
            {"base": base_root_hash, "overlays": overlay_roots}
        ).encode("utf-8")
    )
    atomic_write_json(
        overlay / "verification" / ROOT_NAME,
        {
            "schema_version": CAPSULE_SCHEMA_VERSION,
            "run_id": run_id,
            "revision": revision,
            "base_root_hash": base_root_hash,
            "overlay_root_hash": overlay_root,
            "composite_root_hash": composite,
            "reason": reason,
            "added": sorted(added),
        },
    )
    append_ledger_revision(
        {
            "schema_version": CAPSULE_SCHEMA_VERSION,
            "run_id": run_id,
            "analysis_date": base_root.get("analysis_date"),
            "engine": ws.ENGINE,
            "business_status": base_root.get("business_status"),
            "evidence_status": base_root.get("evidence_status"),
            "final_path": str(final_path),
            "root_hash": composite,
            "base_root_hash": base_root_hash,
            "overlay_path": str(overlay),
            "archive_hash": None,
            "durability": str(base_root.get("durability") or ""),
            "failure_class": None,
            "repair_reason": reason,
            "archived_at": _utc_now()
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
        },
        kind=run_kind,
    )
    _freeze_tree(overlay)
    return RepairResult(
        run_id=run_id,
        revision=revision,
        base_root_hash=base_root_hash,
        composite_root_hash=composite,
        overlay_path=overlay,
        added=tuple(sorted(added)),
        reason=reason,
    )


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
    # kind 从词汇表来 —— CLI 不再是第二份 kind 名单。注意 `begin` 走 CLI 只对
    # `scan-market` 有意义(别的 kind 必须由调用方传 bootstrap;stock-research 的
    # 入口是 `python -m autoresearch.analyze.runctl begin`)。
    begin.add_argument("kind", choices=list(RUN_KINDS))
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
    bind = commands.add_parser("bind-transcript")
    bind.add_argument("run_id")
    bind.add_argument("path")
    bind.add_argument("--role", required=True)
    bind.add_argument("--subject")
    bind.add_argument("--invocation-id", required=True)
    bind.add_argument("--engine", choices=ws.ENGINES)
    bind.add_argument("--from-ordinal", type=int)
    bind.add_argument("--to-ordinal", type=int)
    materialize = commands.add_parser("materialize-agents")
    materialize.add_argument("run_id")
    replay_cmd = commands.add_parser("replay")
    replay_cmd.add_argument("run_id")
    replay_cmd.add_argument("--stage", action="append", default=[])
    done = commands.add_parser("finalize")
    done.add_argument("run_id")
    done.add_argument(
        "--business-status",
        required=True,
        choices=["SUCCEEDED", "FAILED", "INTERRUPTED"],
    )
    done.add_argument("--report-dir")
    done.add_argument("--error-json")
    done.add_argument("--replay-stage", action="append", default=[])
    check = commands.add_parser("verify")
    check.add_argument("run_id")
    recover = commands.add_parser("recover")
    recover.add_argument("--stale-after-minutes", type=float, default=5.0)
    fix = commands.add_parser("repair")
    fix.add_argument("run_id")
    fix.add_argument("--reason", required=True)
    fix.add_argument("--source")
    agent_event = commands.add_parser("agent-event")
    agent_event.add_argument("run_id")
    agent_event.add_argument("event_type", choices=sorted(_AGENT_EVENT_TYPES))
    agent_event.add_argument("--role", required=True)
    agent_event.add_argument("--subject")
    agent_event.add_argument("--subject-display")
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
        elif args.command == "bind-transcript":
            result = bind_transcript(
                args.run_id,
                args.path,
                role=args.role,
                subject=args.subject,
                invocation_id=args.invocation_id,
                engine=args.engine,
                start_ordinal=args.from_ordinal,
                end_ordinal=args.to_ordinal,
            )
        elif args.command == "materialize-agents":
            result = materialize_agent_index(args.run_id)
        elif args.command == "replay":
            from autoresearch.trace import replay as replay_mod

            handle = load_run(args.run_id)
            result = replay_mod.replay(
                handle.run_id,
                capsule=handle.capsule,
                analysis_date=handle.analysis_date,
                stages=tuple(args.stage) or ("l0", "l1", "l2", "l5"),
                kind=handle.contract.run_kind,
            )
        elif args.command == "finalize":
            outcome = finalize(
                args.run_id,
                args.business_status,
                args.report_dir,
                error=json.loads(args.error_json) if args.error_json else None,
                replay_stages=tuple(args.replay_stage),
            )
            result = {
                "run_id": outcome.run_id,
                "business_status": outcome.business_status.value,
                "evidence_status": outcome.evidence_status.value,
                "replayability": outcome.replayability.value,
                "final_path": str(outcome.final_path),
                "root_hash": outcome.root_hash,
                "archive": str(outcome.archive) if outcome.archive else None,
                "durability": outcome.durability,
                "last_reliable_checkpoint": outcome.last_reliable_checkpoint,
            }
        elif args.command == "verify":
            result = verify(args.run_id)
        elif args.command == "recover":
            result = {
                "recovered": [
                    {
                        "run_id": item.run_id,
                        "business_status": item.business_status,
                        "final_path": str(item.final_path) if item.final_path else None,
                        "finalized": item.finalized,
                        "reason": item.reason,
                    }
                    for item in recover_stale_runs(
                        stale_after=timedelta(minutes=args.stale_after_minutes)
                    )
                ]
            }
        elif args.command == "repair":
            fixed = repair(args.run_id, reason=args.reason, source=args.source)
            result = {
                "run_id": fixed.run_id,
                "revision": fixed.revision,
                "base_root_hash": fixed.base_root_hash,
                "composite_root_hash": fixed.composite_root_hash,
                "overlay_path": str(fixed.overlay_path),
                "added": list(fixed.added),
                "reason": fixed.reason,
            }
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
                    subject_display=args.subject_display,
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
                    subject_display=args.subject_display,
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
    "MANIFEST_NAME",
    "ROOT_NAME",
    "append_ledger_revision",
    "begin_run",
    "bind_transcript",
    "checkpoint",
    "inspect_run",
    "load_run",
    "main",
    "build_archive",
    "finalize",
    "ledger_path",
    "materialize_agent_index",
    "materialize_transcripts",
    "read_valid_ledger",
    "recover_stale_runs",
    "recover_stale_runs_quietly",
    "refresh_heartbeat",
    "repair",
    "verify",
    "verify_archive",
    "verify_manifest",
    "write_manifest",
    "record_agent_boundary",
    "record_controlled_agent_boundary",
]

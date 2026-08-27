#!/usr/bin/env python3
"""Forensic run spool lifecycle and its small operator CLI."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
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
from autoresearch.trace.events import append_event, verify_event_chain

_STAGE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
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
            datetime.fromisoformat(field.replace("Z", "+00:00"))
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
    workspace.parent.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(exist_ok=False)
    staging = workspace / "staging" / resolved_date
    capsule = workspace / "capsule"
    staging.mkdir(parents=True, exist_ok=False)
    for relative in (
        "identity",
        "events",
        "stages",
        "products/staging",
    ):
        (capsule / relative).mkdir(parents=True, exist_ok=True)

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
    handle = RunHandle(
        run_id=run_id,
        analysis_date=resolved_date,
        engine=engine,
        workspace=workspace,
        staging=staging,
        capsule=capsule,
        contract=contract,
    )
    _write_contract_copies(handle)
    state = RunState.build(
        run_id=run_id,
        business_status=BusinessStatus.ACTIVE,
        evidence_status=EvidenceStatus.PENDING,
        replayability=Replayability.NONE,
        now=stamp,
    )
    atomic_write_json(workspace / "state.json", state.to_dict())
    append_event(
        capsule / "events/events.jsonl",
        run_id=run_id,
        engine=engine,
        stage="run",
        invocation_id=f"run-{run_id}",
        attempt=1,
        subject=None,
        event_type="RUN_STARTED",
        payload={
            "analysis_date": resolved_date,
            "contract_hash": contract.contract_hash,
            "kind": kind,
            "workspace": str(workspace),
        },
    )
    return handle


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

    contract_paths = (
        workspace / "run_contract.json",
        workspace / "capsule/identity/run_contract.json",
    )
    contracts = [load_run_contract(path) for path in contract_paths]
    contract = contracts[0]
    staging = workspace / "staging" / contract.analysis_date
    staging_contract = staging / "run_contract.json"
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
    if not staging.is_dir():
        raise RuntimeError("RunContract staging directory is missing")
    capsule = workspace / "capsule"
    if not capsule.is_dir():
        raise RuntimeError("capsule directory is missing")
    _state_from_path(workspace / "state.json", run_id=resolved_id)
    event_path = capsule / "events/events.jsonl"
    if not event_path.is_file():
        raise RuntimeError("RUN_STARTED event log is missing")
    chain = verify_event_chain(event_path)
    if not chain["ok"]:
        raise RuntimeError(f"invalid event chain: {chain['error']}")
    if chain["n"] < 1:
        raise RuntimeError("RUN_STARTED event is missing")
    first_event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[0])
    if (
        first_event.get("event_type") != "RUN_STARTED"
        or first_event.get("run_id") != resolved_id
        or first_event.get("engine") != contract.engine
    ):
        raise RuntimeError("first event must be the matching RUN_STARTED fact")
    return RunHandle(
        run_id=resolved_id,
        analysis_date=contract.analysis_date,
        engine=contract.engine,
        workspace=workspace,
        staging=staging,
        capsule=capsule,
        contract=contract,
    )


def _artifact_source(handle: RunHandle, value: Path | str) -> tuple[str, Path]:
    if not isinstance(value, (str, Path)):
        raise TypeError(f"artifact must be a path string, got {type(value).__name__}")
    text = str(value)
    if not text or "\x00" in text:
        raise ValueError(f"invalid artifact path: {text!r}")
    raw = Path(text)
    if ".." in raw.parts:
        raise ValueError(f"artifact path traverses staging: {text!r}")
    staging = handle.staging.resolve()
    if raw.is_absolute():
        source = raw
    else:
        cwd_candidate = raw.resolve()
        try:
            cwd_candidate.relative_to(staging)
            source = cwd_candidate
        except ValueError:
            source = handle.staging / raw
    resolved = source.resolve(strict=False)
    try:
        relative = resolved.relative_to(staging)
    except ValueError as exc:
        raise ValueError(f"artifact path escapes staging: {text!r}") from exc
    if not relative.parts:
        raise ValueError("artifact path cannot be the staging root")
    if source.exists() and (source.is_symlink() or not source.is_file()):
        raise ValueError(f"artifact must be a regular file: {text!r}")
    return relative.as_posix(), resolved


def _allocate_attempt(capsule: Path, stage: str) -> tuple[int, Path]:
    stage_root = capsule / "stages" / stage
    stage_root.mkdir(parents=True, exist_ok=True)
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
            return attempt, path
        except FileExistsError:
            continue


def _copy_artifact(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, destination.open("xb") as writer:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            writer.write(block)
        writer.flush()
        os.fsync(writer.fileno())


def checkpoint(
    run_id: str,
    stage: str,
    status: str,
    artifacts: Sequence[Path | str],
    metrics: Mapping,
    error: str | None = None,
) -> Checkpoint:
    """Persist one immutable stage attempt, then append its two terminal facts."""
    handle = load_run(run_id)
    resolved_stage = _validate_stage(stage)
    resolved_status = _validate_status(status)
    if isinstance(artifacts, (str, bytes)) or not isinstance(artifacts, Sequence):
        raise TypeError("artifacts must be a sequence of paths")
    if not isinstance(metrics, Mapping):
        raise TypeError("metrics must be a mapping")
    if error is not None and type(error) is not str:
        raise TypeError("error must be a string or None")
    normalized_metrics = json.loads(canonical_json(dict(metrics)))
    normalized_artifacts: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for artifact in artifacts:
        relative, source = _artifact_source(handle, artifact)
        if relative in seen:
            raise ValueError(f"artifact path collision: {relative!r}")
        seen.add(relative)
        normalized_artifacts.append((relative, source))

    attempt, attempt_path = _allocate_attempt(handle.capsule, resolved_stage)
    product_root = (
        handle.capsule
        / "products/staging"
        / resolved_stage
        / f"attempt-{attempt}"
    )
    output_rows = []
    for relative, source in normalized_artifacts:
        row = {"path": relative, "status": "MISSING", "bytes": None, "sha256": None}
        if source.is_file():
            size = source.stat().st_size
            row["bytes"] = size
            if size == 0:
                row["status"] = "EMPTY"
            else:
                destination = product_root / relative
                _copy_artifact(source, destination)
                row.update(
                    {
                        "status": "PRESENT",
                        "sha256": sha256_file(destination),
                        "captured_path": destination.relative_to(handle.capsule).as_posix(),
                    }
                )
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
        artifacts=tuple(relative for relative, _ in normalized_artifacts),
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
    inspect = commands.add_parser("inspect")
    inspect.add_argument("run_id")
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
            ).to_dict()
        else:
            result = inspect_run(args.run_id)
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
]

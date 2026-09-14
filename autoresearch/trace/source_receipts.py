"""Typed, occurrence-preserving source response capture and replay."""

from __future__ import annotations

import fcntl
import io
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from autoresearch.common.atomic import canonical_json
from autoresearch.contracts.source_receipt import (
    source_receipt_id,
    validate_source_receipt,
    validate_source_receipts,
)
from autoresearch.trace.blobs import blob_path, dataframe_bytes, put_bytes
from autoresearch.trace.identity import redact_value

RECEIPTS_PATH = "lineage/source_receipts.jsonl"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


class SourcePayloadMissing(RuntimeError):
    """A receipt exists but its exact response bytes are unavailable."""


class SourceSequenceMismatch(RuntimeError):
    """Replay asked for a response occurrence that was never captured."""


class RecordedSourceFailure(RuntimeError):
    """A captured provider call failed with a known, replayed message."""


class RecordedSourceUnavailable(RecordedSourceFailure):
    """A source was explicitly unmeasured rather than observed as successful."""


@dataclass(frozen=True)
class UnmeasuredSource:
    category: str
    message: str


class _RecordedToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapsuleSourceHook:
    """ExecutionContext adapter that keeps the lower call boundary trace-agnostic."""

    handle: object

    def record_response(
        self,
        context: dict,
        outcome: object,
        *,
        raw_bytes: bytes | None = None,
    ) -> dict:
        return record_response(self.handle, context, outcome, raw_bytes=raw_bytes)


def _safe_message(value: object) -> str:
    try:
        raw = str(value) or type(value).__name__
    except Exception:
        raw = "[unavailable]"
    safe = redact_value(raw).value
    return safe if isinstance(safe, str) and safe else "[unavailable]"


def _encode(outcome: object) -> tuple[str, str, bytes, dict | None]:
    if isinstance(outcome, UnmeasuredSource):
        error = {"category": outcome.category, "message": _safe_message(outcome.message)}
        payload = canonical_json(error).encode("utf-8")
        return "UNMEASURED", "failure.json.v1", payload, error
    if isinstance(outcome, BaseException):
        error = {
            "category": type(outcome).__name__,
            "message": _safe_message(outcome),
        }
        payload = canonical_json(error).encode("utf-8")
        return "FAILED", "failure.json.v1", payload, error
    if isinstance(outcome, pd.DataFrame):
        return "SUCCEEDED", "dataframe.parquet.v1", dataframe_bytes(outcome), None
    if isinstance(outcome, bytes):
        return "SUCCEEDED", "bytes.v1", outcome, None
    if isinstance(outcome, str):
        return "SUCCEEDED", "text.utf8.v1", outcome.encode("utf-8"), None
    try:
        payload = canonical_json(outcome).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TypeError("source outcome has no allowed non-executable codec") from exc
    return "SUCCEEDED", "json.canonical.v1", payload, None


def _rows(capsule: Path) -> list[dict]:
    path = capsule / RECEIPTS_PATH
    try:
        info = path.lstat()
    except FileNotFoundError:
        return []
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("source receipt target must be a regular file")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_receipts(capsule: Path | str) -> list[dict]:
    return validate_source_receipts(_rows(Path(capsule)))


def _base_key(context: dict) -> str:
    return canonical_json({
        "engine": context["engine"],
        "run_id": context["run_id"],
        "task_id": context["task_id"],
        "attempt": context["attempt"],
        "provider": context["provider"],
        "endpoint": context["endpoint"],
        "normalized_params": context["normalized_params"],
    })


def _append_with_occurrence(capsule: Path, context: dict, build) -> dict:
    path = capsule / RECEIPTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    capsule_info = capsule.lstat()
    lineage_info = path.parent.lstat()
    if (
        stat.S_ISLNK(capsule_info.st_mode)
        or not stat.S_ISDIR(capsule_info.st_mode)
        or stat.S_ISLNK(lineage_info.st_mode)
        or not stat.S_ISDIR(lineage_info.st_mode)
    ):
        raise ValueError("source receipt directories must be real directories")
    path.parent.resolve(strict=True).relative_to(capsule.resolve(strict=True))
    lock_path = path.with_suffix(".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | _NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        rows = _rows(capsule)
        key = _base_key(context)
        occurrence = 1 + sum(
            1
            for row in rows
            if _base_key(row) == key
        )
        receipt = build(occurrence)
        validate_source_receipts([*rows, receipt])
        payload = (canonical_json(receipt) + "\n").encode("utf-8")
        stream_fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | _NOFOLLOW,
            0o600,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(stream_fd, view)
                if written <= 0:
                    raise OSError("short write while appending source receipt")
                view = view[written:]
            os.fsync(stream_fd)
            directory_fd = os.open(path.parent, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            os.close(stream_fd)
        return receipt
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def record_response(
    handle,
    context: dict,
    outcome: object,
    *,
    raw_bytes: bytes | None = None,
) -> dict:
    """Freeze one exact provider return without collapsing repeated calls."""
    required = {
        "engine", "run_id", "task_id", "attempt", "provider", "endpoint",
        "normalized_params", "started_at", "ended_at", "as_of", "available_at",
        "consumer_refs",
    }
    context = dict(context)
    missing = sorted(required - set(context))
    if missing:
        raise ValueError(f"source response context missing fields: {missing}")
    if context["engine"] != handle.engine or context["run_id"] != handle.run_id:
        raise ValueError("source response run identity mismatch")
    normalized = redact_value(context["normalized_params"]).value
    if not isinstance(normalized, dict):
        raise ValueError("normalized source params must be an object")
    context["normalized_params"] = normalized
    status, codec, payload, error = _encode(outcome)
    payload_hash = put_bytes(handle.capsule, payload)
    raw_hash = put_bytes(handle.capsule, raw_bytes) if raw_bytes is not None else None

    def build(occurrence: int) -> dict:
        value = {
            "schema_version": 1,
            "receipt_id": "0" * 64,
            "engine": context["engine"],
            "run_id": context["run_id"],
            "task_id": context["task_id"],
            "attempt": context["attempt"],
            "provider": context["provider"],
            "endpoint": context["endpoint"],
            "normalized_params": context["normalized_params"],
            "occurrence": occurrence,
            "started_at": context["started_at"],
            "ended_at": context["ended_at"],
            "status": status,
            "codec": codec,
            "payload_hash": payload_hash,
            "raw_hash": raw_hash,
            "error": error,
            "as_of": context["as_of"],
            "available_at": context["available_at"],
            "consumer_refs": context["consumer_refs"],
        }
        value["receipt_id"] = source_receipt_id(value)
        return validate_source_receipt(value)

    return _append_with_occurrence(Path(handle.capsule), context, build)


def record_active_response(
    *,
    provider: str,
    endpoint: str,
    params: dict,
    outcome: object,
    consumer_artifact_ids: list[str],
    raw_bytes: bytes | None = None,
) -> dict | None:
    """Record one high-level supplier snapshot for the active operation, if any.

    This is the bridge for legacy suppliers that do not yet flow through the data-lake
    cache (notably direct yfinance/akshare calls).  The payload codec remains one of the
    non-executable SourceReceipt codecs, and the task/attempt identity comes from the
    captured child environment rather than caller prose.
    """
    from autoresearch.common import workspace as ws
    from autoresearch.common.execution_context import current_execution_context
    from autoresearch.trace.capsule import require_active_run
    from autoresearch.trace.source_lineage import normalized_params

    run_id = ws.active_run_id()
    if run_id is None:
        return None
    handle = require_active_run(run_id)
    execution = current_execution_context()
    if execution is not None:
        task_id = execution.task_id
        attempt = execution.attempt
        stamp = execution.clock.now()
    else:
        task_id = str(os.environ.get("AUTORESEARCH_TASK_ID") or "").strip()
        attempt_raw = str(os.environ.get("AUTORESEARCH_ATTEMPT") or "1").strip()
        attempt = int(attempt_raw)
        stamp = datetime.now(timezone.utc)
    if not task_id or attempt < 1:
        raise ValueError("active source snapshot requires task and attempt identity")
    timestamp = stamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    consumers = [
        {
            "task_id": task_id,
            "attempt": attempt,
            "artifact_id": artifact_id,
            "consumption_kind": "INPUT",
        }
        for artifact_id in consumer_artifact_ids
    ]
    return record_response(
        handle,
        {
            "engine": handle.engine,
            "run_id": handle.run_id,
            "task_id": task_id,
            "attempt": attempt,
            "provider": str(provider),
            "endpoint": str(endpoint),
            "normalized_params": normalized_params(params),
            "started_at": timestamp,
            "ended_at": timestamp,
            "as_of": str(params.get("analysis_date") or params.get("date") or "") or None,
            "available_at": timestamp,
            "consumer_refs": consumers,
        },
        outcome,
        raw_bytes=raw_bytes,
    )


def _failure(error: dict, *, unmeasured: bool) -> BaseException:
    category = error["category"]
    message = error["message"]
    if category == "DataContractError":
        from autoresearch.data.contracts import DataContractError

        return DataContractError(message)
    if category == "TimeoutError":
        return TimeoutError(message)
    if category == "ConnectionError":
        return ConnectionError(message)
    if category == "PermissionError":
        return PermissionError(message)
    error_type = RecordedSourceUnavailable if unmeasured else RecordedSourceFailure
    return error_type(f"{category}: {message}")


def replay_response(capsule: Path | str, receipt_id: str) -> object:
    root = Path(capsule)
    matches = [row for row in read_receipts(root) if row["receipt_id"] == receipt_id]
    if len(matches) != 1:
        raise SourceSequenceMismatch(f"source receipt not found: {receipt_id}")
    receipt = matches[0]
    payload_path = blob_path(root, receipt["payload_hash"])
    if not payload_path.is_file():
        raise SourcePayloadMissing(
            f"source response payload is missing: {receipt['payload_hash']}"
        )
    payload = payload_path.read_bytes()
    codec = receipt["codec"]
    if codec == "failure.json.v1":
        error = json.loads(payload.decode("utf-8"))
        raise _failure(error, unmeasured=receipt["status"] == "UNMEASURED")
    if codec == "dataframe.parquet.v1":
        return pd.read_parquet(io.BytesIO(payload))
    if codec == "json.canonical.v1":
        return json.loads(payload.decode("utf-8"))
    if codec == "text.utf8.v1":
        return payload.decode("utf-8")
    if codec == "bytes.v1":
        return payload
    raise SourcePayloadMissing(f"unsupported source codec: {codec}")


def _jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def materialize_tool_receipts(handle) -> list[dict]:
    """Convert host-captured external tool calls into task-bound source receipts."""
    capsule = Path(handle.capsule)
    bindings: dict[str, tuple[str, int]] = {}
    binding_root = capsule / "agents/session/task_bindings"
    if binding_root.is_dir():
        for path in sorted(binding_root.glob("*.json")):
            binding = json.loads(path.read_text(encoding="utf-8"))
            bindings[str(binding["invocation_id"])] = (
                str(binding["task_id"]),
                int(binding["attempt"]),
            )
    existing = read_receipts(capsule)
    existing_calls = {
        (
            row["task_id"],
            row["attempt"],
            row["normalized_params"].get("tool_call_id"),
        )
        for row in existing
        if row["provider"] == "host_tool"
    }
    created = []
    for row in _jsonl(capsule / "lineage/external_tools.jsonl"):
        invocation_id = str(row.get("invocation_id") or "")
        task_id, attempt = bindings.get(invocation_id, ("session.main", 1))
        call_id = str(row.get("tool_call_id") or "")
        if (task_id, attempt, call_id) in existing_calls:
            continue
        requested_at = str(
            row.get("requested_at")
            or row.get("completed_at")
            or "1970-01-01T00:00:00Z"
        )
        completed_at = str(row.get("completed_at") or requested_at)
        digest = row.get("result_hash")
        payload_path = blob_path(capsule, str(digest)) if digest else None
        raw = payload_path.read_bytes() if payload_path and payload_path.is_file() else None
        status = str(row.get("status") or "INCOMPLETE")
        if status == "COMPLETED" and raw is not None:
            outcome: object = raw
        elif status == "FAILED":
            outcome = _RecordedToolError(
                raw.decode("utf-8", errors="replace") if raw is not None else "tool failed"
            )
        else:
            outcome = UnmeasuredSource(
                "ToolResultUnavailable",
                "external tool response was not captured",
            )
        receipt = record_response(
            handle,
            {
                "engine": handle.engine,
                "run_id": handle.run_id,
                "task_id": task_id,
                "attempt": attempt,
                "provider": "host_tool",
                "endpoint": str(row.get("tool_name") or "external_tool"),
                "normalized_params": {
                    "request": str(row.get("request") or ""),
                    "tool_call_id": call_id,
                },
                "started_at": requested_at,
                "ended_at": completed_at,
                "as_of": None,
                "available_at": completed_at,
                "consumer_refs": [{
                    "task_id": task_id,
                    "attempt": attempt,
                    "artifact_id": f"tool.{call_id or 'unknown'}",
                    "consumption_kind": "DISCOVERY",
                }],
            },
            outcome,
            raw_bytes=raw,
        )
        created.append(receipt)
        existing_calls.add((task_id, attempt, call_id))
    return [*existing, *created]


__all__ = [
    "RecordedSourceFailure",
    "RecordedSourceUnavailable",
    "SourcePayloadMissing",
    "SourceSequenceMismatch",
    "UnmeasuredSource",
    "CapsuleSourceHook",
    "record_active_response",
    "materialize_tool_receipts",
    "read_receipts",
    "record_response",
    "replay_response",
]

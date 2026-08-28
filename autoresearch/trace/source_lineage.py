"""Exact source-read lineage captured at the data cache's real return points."""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import stat
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.data.endpoints import policy
from autoresearch.trace.atomic import canonical_json, sha256_bytes
from autoresearch.trace.blobs import blob_path, put_bytes, put_dataframe
from autoresearch.trace.capsule import require_active_run
from autoresearch.trace.capsule_models import RunHandle
from autoresearch.trace.events import append_event
from autoresearch.trace.identity import redact_value, scan_for_secrets

LINEAGE_SCHEMA_VERSION = 1
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_safe(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = value if not isinstance(value, (set, frozenset)) else sorted(value, key=str)
        return [_json_safe(item) for item in items]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        with contextlib.suppress(Exception):
            return _json_safe(value.item())
    return str(value)


def _safe_value(value: Any) -> Any:
    redacted = redact_value(_json_safe(value)).value
    payload = canonical_json(redacted).encode("utf-8")
    if not scan_for_secrets(payload)["ok"]:
        return "[REDACTED]"
    return json.loads(payload)


def _safe_error(error: BaseException) -> tuple[str, str]:
    error_type = type(error).__name__
    try:
        raw = str(error) or error_type
    except BaseException:
        return error_type, "[unavailable]"
    try:
        message = _safe_value(raw)
    except BaseException:
        return error_type, "[unavailable]"
    return error_type, message if isinstance(message, str) else "[unavailable]"


def normalized_params(params: object) -> dict:
    """The exact params shape the lineage writer records, so replay can key on it."""
    try:
        normalized = _safe_value(params)
    except BaseException:  # noqa: BLE001 - unserializable params are still a fact
        normalized = "[UNSERIALIZABLE]"
    if not isinstance(normalized, dict):
        normalized = {"params": normalized}
    return normalized


def _generic_evidence_warning() -> None:
    with contextlib.suppress(BaseException):
        os.write(2, b"source lineage evidence incomplete\n")


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _lineage_directory(capsule: Path) -> Path:
    info = capsule.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("capsule must be a real directory")
    directory = capsule / "lineage"
    created = False
    try:
        info = directory.lstat()
    except FileNotFoundError:
        try:
            directory.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            pass
        info = directory.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("lineage directory must be a real directory")
    directory.resolve(strict=True).relative_to(capsule.resolve(strict=True))
    if created:
        _fsync_directory(capsule)
    return directory


def _durable_directory(path: Path) -> Path:
    missing: list[Path] = []
    current = path
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            missing.append(current)
            current = current.parent
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"emergency evidence directory is unsafe: {current}")
        break
    for candidate in reversed(missing):
        with contextlib.suppress(FileExistsError):
            candidate.mkdir(mode=0o700)
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"emergency evidence directory is unsafe: {candidate}")
        _fsync_directory(candidate.parent)
    return path


def _record_binding_gap(error: BaseException) -> None:
    """Persist a safe out-of-capsule marker when an explicit run cannot be bound."""
    _generic_evidence_warning()
    try:
        root = _durable_directory(ws.context_root() / "scan_runs" / "_evidence_gaps")
        row = {
            "schema_version": 1,
            "reason": "invalid_active_run_binding",
            "error_type": type(error).__name__,
            "recorded_at": _utc_now(),
        }
        path = root / "source_lineage.jsonl"
        payload = (canonical_json(row) + "\n").encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | _NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short emergency lineage write")
                view = view[written:]
            os.fsync(fd)
            _fsync_directory(root)
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except BaseException:
        return


def _append_jsonl(capsule: Path, name: str, row: dict) -> None:
    directory = _lineage_directory(capsule)
    path = directory / name
    try:
        info = path.lstat()
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError(f"lineage target must be a regular file: {path}")
    payload = (canonical_json(row) + "\n").encode("utf-8")
    fd = os.open(
        path,
        os.O_RDWR | os.O_CREAT | os.O_APPEND | _NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write while appending source lineage")
            view = view[written:]
        os.fsync(fd)
        _fsync_directory(directory)
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _append_read(capsule: Path, row: dict) -> None:
    _append_jsonl(capsule, "reads.jsonl", row)


def _columns_hash(frame: pd.DataFrame | None) -> str | None:
    if not isinstance(frame, pd.DataFrame):
        return None
    columns = [
        {"name": str(name), "dtype": str(dtype)}
        for name, dtype in zip(frame.columns, frame.dtypes, strict=True)
    ]
    return sha256_bytes(canonical_json(columns).encode("utf-8"))


def _event_type(status: str, access: str | None) -> str:
    if status == "FAILED":
        return "SOURCE_FAILED"
    return "SOURCE_READ" if access == "CACHE_HIT" else "SOURCE_FETCHED"


@dataclass
class SourceAccess:
    handle: RunHandle | None
    endpoint: str
    normalized_params: dict | str
    policy_key: str | None
    policy_settle: str | None
    started_at: str
    stage: str = "data"
    invocation_id: str = "source-read"
    attempt: int = 1
    subject: str | None = None
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _finished: bool = field(default=False, init=False, repr=False)
    _setup_error: BaseException | None = field(default=None, repr=False)

    @property
    def enabled(self) -> bool:
        return self.handle is not None

    def finish_success(
        self,
        frame: pd.DataFrame | None,
        access: str,
        path: Path | str | None,
        *,
        source_bytes: bytes | None = None,
    ) -> bool:
        if self._finished or self.handle is None:
            return False
        self._finished = True
        try:
            return self._finish_success(frame, access, path, source_bytes=source_bytes)
        except BaseException as exc:
            self._record_unexpected_gap(
                status="SUCCEEDED", access=access, error=exc
            )
            return False

    def _finish_success(
        self,
        frame: pd.DataFrame | None,
        access: str,
        path: Path | str | None,
        *,
        source_bytes: bytes | None,
    ) -> bool:
        exact_path = Path(path) if path is not None else None
        blob_hash: str | None = None
        blob_bytes: int | None = None
        blob_role: str | None = None
        normalized_blob_hash: str | None = None
        normalized_blob_bytes: int | None = None
        evidence_error = self._setup_error
        try:
            if exact_path is not None:
                if not isinstance(source_bytes, bytes):
                    raise ValueError("path-backed source is missing its stable byte snapshot")
                blob_hash = put_bytes(self.handle.capsule, source_bytes)
                blob_bytes = len(source_bytes)
                blob_role = "SOURCE_FILE_SNAPSHOT"
                if not isinstance(frame, pd.DataFrame):
                    raise TypeError("successful source result is not a DataFrame")
                normalized_blob_hash = put_dataframe(self.handle.capsule, frame)
                normalized_blob_bytes = blob_path(
                    self.handle.capsule, normalized_blob_hash
                ).stat().st_size
            elif isinstance(frame, pd.DataFrame):
                blob_hash = put_dataframe(self.handle.capsule, frame)
                blob_role = "NORMALIZED_RESULT"
            else:
                raise TypeError("successful source result is not a DataFrame")
            if blob_bytes is None:
                blob_bytes = blob_path(self.handle.capsule, blob_hash).stat().st_size
        except BaseException as exc:  # evidence cannot replace a successful data result
            evidence_error = evidence_error or exc
        return self._persist(
            status="SUCCEEDED",
            access=access,
            path=exact_path,
            frame=frame,
            blob_hash=blob_hash,
            blob_bytes=blob_bytes,
            blob_role=blob_role,
            normalized_blob_hash=normalized_blob_hash,
            normalized_blob_bytes=normalized_blob_bytes,
            business_error=None,
            evidence_error=evidence_error,
        )

    def finish_failure(self, error: BaseException) -> bool:
        if self._finished or self.handle is None:
            return False
        self._finished = True
        try:
            return self._persist(
                status="FAILED",
                access=None,
                path=None,
                frame=None,
                blob_hash=None,
                blob_bytes=None,
                blob_role=None,
                normalized_blob_hash=None,
                normalized_blob_bytes=None,
                business_error=error,
                evidence_error=self._setup_error,
            )
        except BaseException as exc:
            self._record_unexpected_gap(status="FAILED", access=None, error=exc)
            return False

    def _persist(
        self,
        *,
        status: str,
        access: str | None,
        path: Path | None,
        frame: pd.DataFrame | None,
        blob_hash: str | None,
        blob_bytes: int | None,
        blob_role: str | None,
        normalized_blob_hash: str | None,
        normalized_blob_bytes: int | None,
        business_error: BaseException | None,
        evidence_error: BaseException | None,
    ) -> bool:
        assert self.handle is not None
        error_type, error_message = (
            _safe_error(business_error) if business_error is not None else (None, None)
        )
        evidence_error_type = type(evidence_error).__name__ if evidence_error is not None else None
        row = {
            "schema_version": LINEAGE_SCHEMA_VERSION,
            "run_id": self.handle.run_id,
            "engine": self.handle.engine,
            "stage": self.stage,
            "invocation_id": self.invocation_id,
            "attempt": self.attempt,
            "subject": self.subject,
            "correlation_id": self.correlation_id,
            "endpoint": self.endpoint,
            "normalized_params": self.normalized_params,
            "policy_key": self.policy_key,
            "policy_settle": self.policy_settle,
            "access": access,
            "path": str(_safe_value(str(path))) if path is not None else None,
            "blob_hash": blob_hash,
            "bytes": blob_bytes,
            "blob_role": blob_role,
            "normalized_blob_hash": normalized_blob_hash,
            "normalized_bytes": normalized_blob_bytes,
            "rows": len(frame) if isinstance(frame, pd.DataFrame) else None,
            "columns_hash": _columns_hash(frame),
            "started_at": self.started_at,
            "ended_at": _utc_now(),
            "status": status,
            "error_type": error_type,
            "error_message": error_message,
            "evidence_complete": evidence_error is None,
            "evidence_error_type": evidence_error_type,
        }
        lineage_row_hash = sha256_bytes(canonical_json(row).encode("utf-8"))
        row_ok = True
        try:
            _append_read(self.handle.capsule, row)
        except BaseException as exc:
            row_ok = False
            evidence_error = evidence_error or exc
        event_ok = True
        event_payload = {
            "endpoint": self.endpoint,
            "access": access,
            "blob_hash": blob_hash,
            "rows": row["rows"],
            "status": status,
            "lineage_row_hash": lineage_row_hash,
            "correlation_id": self.correlation_id,
            "lineage_persisted": row_ok,
            "evidence_complete": evidence_error is None and row_ok,
            "error_type": error_type,
        }
        try:
            append_event(
                self.handle.capsule / "events/events.jsonl",
                run_id=self.handle.run_id,
                engine=self.handle.engine,
                stage=self.stage,
                invocation_id=self.invocation_id,
                attempt=self.attempt,
                subject=self.subject,
                event_type=_event_type(status, access),
                payload=event_payload,
            )
        except BaseException as exc:
            event_ok = False
            evidence_error = evidence_error or exc
        complete = evidence_error is None and row_ok and event_ok
        if not complete:
            self._mark_gap(
                status=status,
                access=access,
                row_persisted=row_ok,
                event_persisted=event_ok,
                lineage_row_hash=lineage_row_hash,
                error=evidence_error or RuntimeError("source evidence incomplete"),
            )
        return complete

    def _mark_gap(
        self,
        *,
        status: str,
        access: str | None,
        row_persisted: bool,
        event_persisted: bool,
        lineage_row_hash: str | None,
        error: BaseException,
    ) -> None:
        self._record_gap(
            status=status,
            access=access,
            row_persisted=row_persisted,
            event_persisted=event_persisted,
            lineage_row_hash=lineage_row_hash,
            error=error,
        )

    def _record_unexpected_gap(
        self,
        *,
        status: str,
        access: str | None,
        error: BaseException,
    ) -> None:
        self._record_gap(
            status=status,
            access=access,
            row_persisted=False,
            event_persisted=False,
            lineage_row_hash=None,
            error=error,
        )

    def _record_gap(
        self,
        *,
        status: str,
        access: str | None,
        row_persisted: bool,
        event_persisted: bool,
        lineage_row_hash: str | None,
        error: BaseException,
    ) -> None:
        if self.handle is None:
            _generic_evidence_warning()
            return
        try:
            gap = {
                "schema_version": 1,
                "run_id": self.handle.run_id,
                "stage": self.stage,
                "invocation_id": self.invocation_id,
                "attempt": self.attempt,
                "endpoint": self.endpoint,
                "correlation_id": self.correlation_id,
                "lineage_row_hash": lineage_row_hash,
                "access": access,
                "source_status": status,
                "row_persisted": row_persisted,
                "event_persisted": event_persisted,
                "error_type": type(error).__name__,
                "recorded_at": _utc_now(),
            }
        except BaseException:
            _generic_evidence_warning()
            return
        marker_ok = False
        event_ok = False
        try:
            _append_jsonl(self.handle.capsule, "evidence_gaps.jsonl", gap)
            marker_ok = True
        except BaseException:
            pass
        try:
            append_event(
                self.handle.capsule / "events/events.jsonl",
                run_id=self.handle.run_id,
                engine=self.handle.engine,
                stage=self.stage,
                invocation_id=self.invocation_id,
                attempt=self.attempt,
                subject=self.subject,
                event_type="EVIDENCE_MISSING",
                payload=gap,
            )
            event_ok = True
        except BaseException:
            pass
        if not marker_ok and not event_ok:
            _generic_evidence_warning()


def trace_access(
    endpoint: str,
    params: dict,
    *,
    today: str | None = None,
) -> SourceAccess:
    """Start one best-effort source trace, or return a true no-op without an active run."""
    started_at = _utc_now()
    raw_run_id = str(os.environ.get("AUTORESEARCH_RUN_ID", "")).strip()
    try:
        run_id = ws.active_run_id()
    except ValueError as exc:
        _record_binding_gap(exc)
        run_id = None
    if run_id is None:
        return SourceAccess(None, str(endpoint), {}, None, None, started_at)
    try:
        handle = require_active_run(run_id)
    except BaseException as exc:
        # The data path must remain available even when its evidence control plane is not.
        if raw_run_id:
            _record_binding_gap(exc)
        return SourceAccess(None, str(endpoint), {}, None, None, started_at)
    try:
        pol = policy(endpoint)
    except (KeyError, TypeError, ValueError):
        pol = {}
    try:
        attempt_raw = str(os.environ.get("AUTORESEARCH_ATTEMPT", "1")).strip()
        attempt = int(attempt_raw)
        if attempt < 1:
            raise ValueError("attempt must be positive")
    except (TypeError, ValueError):
        attempt = 1
    stage = str(os.environ.get("AUTORESEARCH_STAGE", "")).strip() or "data"
    invocation_id = (
        str(os.environ.get("AUTORESEARCH_INVOCATION_ID", "")).strip() or f"source-{os.getpid()}"
    )
    subject = str(os.environ.get("AUTORESEARCH_SUBJECT", "")).strip() or None
    setup_error = None
    try:
        _safe_value(params)
    except BaseException as exc:
        setup_error = exc
    # One normalization for both writer and replay reader: if these two ever
    # disagreed, every replay lookup would miss and look like absent evidence.
    normalized = normalized_params(params)
    return SourceAccess(
        handle=handle,
        endpoint=str(endpoint),
        normalized_params=normalized,
        policy_key=pol.get("key"),
        policy_settle=pol.get("settle"),
        started_at=started_at,
        stage=stage,
        invocation_id=invocation_id,
        attempt=attempt,
        subject=subject,
        _setup_error=setup_error,
    )

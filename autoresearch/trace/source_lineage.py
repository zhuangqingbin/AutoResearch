"""Exact source-read lineage captured at the data cache's real return points."""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import stat
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.data.endpoints import policy
from autoresearch.trace.atomic import canonical_json, sha256_bytes
from autoresearch.trace.blobs import blob_path, put_dataframe, put_file
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
    message = _safe_value(str(error) or error_type)
    return error_type, str(message)


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
    try:
        info = directory.lstat()
    except FileNotFoundError:
        with contextlib.suppress(FileExistsError):
            directory.mkdir(mode=0o700)
        info = directory.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("lineage directory must be a real directory")
    directory.resolve(strict=True).relative_to(capsule.resolve(strict=True))
    return directory


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
    _finished: bool = field(default=False, init=False, repr=False)

    @property
    def enabled(self) -> bool:
        return self.handle is not None

    def finish_success(
        self,
        frame: pd.DataFrame | None,
        access: str,
        path: Path | str | None,
    ) -> bool:
        if self._finished or self.handle is None:
            return False
        self._finished = True
        exact_path = Path(path) if path is not None else None
        blob_hash: str | None = None
        blob_bytes: int | None = None
        evidence_error: BaseException | None = None
        try:
            if exact_path is not None:
                blob_hash = put_file(self.handle.capsule, exact_path)
            elif isinstance(frame, pd.DataFrame):
                blob_hash = put_dataframe(self.handle.capsule, frame)
            else:
                raise TypeError("successful source result is not a DataFrame")
            blob_bytes = blob_path(self.handle.capsule, blob_hash).stat().st_size
        except BaseException as exc:  # evidence cannot replace a successful data result
            evidence_error = exc
        return self._persist(
            status="SUCCEEDED",
            access=access,
            path=exact_path,
            frame=frame,
            blob_hash=blob_hash,
            blob_bytes=blob_bytes,
            business_error=None,
            evidence_error=evidence_error,
        )

    def finish_failure(self, error: BaseException) -> bool:
        if self._finished or self.handle is None:
            return False
        self._finished = True
        return self._persist(
            status="FAILED",
            access=None,
            path=None,
            frame=None,
            blob_hash=None,
            blob_bytes=None,
            business_error=error,
            evidence_error=None,
        )

    def _persist(
        self,
        *,
        status: str,
        access: str | None,
        path: Path | None,
        frame: pd.DataFrame | None,
        blob_hash: str | None,
        blob_bytes: int | None,
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
            "endpoint": self.endpoint,
            "normalized_params": self.normalized_params,
            "policy_key": self.policy_key,
            "policy_settle": self.policy_settle,
            "access": access,
            "path": str(_safe_value(str(path))) if path is not None else None,
            "blob_hash": blob_hash,
            "bytes": blob_bytes,
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
            "lineage_row_hash": sha256_bytes(canonical_json(row).encode("utf-8")),
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
        error: BaseException,
    ) -> None:
        assert self.handle is not None
        error_type, _ = _safe_error(error)
        gap = {
            "schema_version": 1,
            "run_id": self.handle.run_id,
            "stage": self.stage,
            "invocation_id": self.invocation_id,
            "attempt": self.attempt,
            "endpoint": self.endpoint,
            "access": access,
            "source_status": status,
            "row_persisted": row_persisted,
            "event_persisted": event_persisted,
            "error_type": error_type,
            "recorded_at": _utc_now(),
        }
        with contextlib.suppress(BaseException):
            _append_jsonl(self.handle.capsule, "evidence_gaps.jsonl", gap)
        if event_persisted:
            with contextlib.suppress(BaseException):
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


def trace_access(
    endpoint: str,
    params: dict,
    *,
    today: str | None = None,
) -> SourceAccess:
    """Start one best-effort source trace, or return a true no-op without an active run."""
    started_at = _utc_now()
    try:
        run_id = ws.active_run_id()
    except ValueError:
        run_id = None
    if run_id is None:
        return SourceAccess(None, str(endpoint), {}, None, None, started_at)
    try:
        handle = require_active_run(run_id)
    except BaseException:
        # The data path must remain available even when its evidence control plane is not.
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
    try:
        normalized = _safe_value(params)
    except BaseException:
        normalized = "[UNSERIALIZABLE]"
    if not isinstance(normalized, dict):
        normalized = {"params": normalized}
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
    )

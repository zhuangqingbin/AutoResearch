"""Resolve the wall clock frozen before a deterministic operation launch."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from autoresearch.common.execution_context import current_execution_context
from autoresearch.trace.capsule import require_active_run


def operation_clock(handle=None) -> datetime:
    """Return the execution clock, frozen request clock, or standalone wall time.

    Reading capsule evidence belongs to the trace layer.  Keeping this adapter
    here prevents the lower ``common`` package from depending back on forensics.
    """
    execution = current_execution_context()
    if execution is not None:
        return execution.clock.now()
    raw = str(os.environ.get("AUTORESEARCH_FROZEN_CLOCK") or "").strip()
    if not raw:
        task_id = str(os.environ.get("AUTORESEARCH_TASK_ID") or "").strip()
        attempt = str(os.environ.get("AUTORESEARCH_ATTEMPT") or "1").strip()
        current = handle
        if current is None and task_id and os.environ.get("AUTORESEARCH_RUN_ID"):
            try:
                current = require_active_run(str(os.environ["AUTORESEARCH_RUN_ID"]))
            except Exception:  # noqa: BLE001 - standalone callers use wall time
                current = None
        if current is not None and task_id and attempt.isdigit():
            path = (
                Path(current.capsule)
                / "evidence/attempt_records"
                / task_id
                / f"a{attempt}"
                / "operation_request.json"
            )
            try:
                raw = str(json.loads(path.read_text(encoding="utf-8"))["frozen_clock"])
            except (OSError, ValueError, TypeError, KeyError):
                raw = ""
    if raw:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("frozen operation clock must be timezone-aware")
        return value
    return datetime.now().astimezone()


__all__ = ["operation_clock"]

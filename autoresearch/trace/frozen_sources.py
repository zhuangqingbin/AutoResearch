"""Read a run's already frozen source inputs without depending on its orchestrator."""
from __future__ import annotations

import json
import os
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import sha256_bytes
from autoresearch.contracts.execution import validate_decision_frame
from autoresearch.trace.source_receipts import materialize_tool_receipts, read_receipts


def active_source_handle(scan_dir: Path | str):
    from autoresearch.trace.capsule import require_active_run

    run_id = ws.active_run_id()
    if run_id is None:
        return None
    handle = require_active_run(run_id)
    if Path(handle.staging).resolve() != Path(scan_dir).resolve():
        raise ValueError("source directory does not belong to active run")
    return handle


def frozen_source_context(scan_dir: Path | str, *, capture: bool = False) -> dict | None:
    handle = active_source_handle(scan_dir)
    if handle is None:
        return None
    workspace = Path(handle.workspace).resolve(strict=True)
    registry = json.loads((workspace / "session/artifacts.json").read_text(encoding="utf-8"))
    if registry["engine"] != handle.engine or registry["run_id"] != handle.run_id:
        raise ValueError("frozen artifact registry belongs to another run")
    descriptor = registry["artifacts"].get("research.frame")
    if descriptor is None:
        return None  # a historical plan never acquired a frame
    if descriptor["access"] != "READ" or not descriptor["sha256"]:
        raise ValueError("research.frame must be a frozen read input")
    path = workspace / descriptor["relative_path"]
    path.resolve(strict=True).relative_to(workspace)
    if any(item.is_symlink() for item in (path, *path.parents) if item != workspace):
        raise ValueError("frozen source path contains symlink")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        stat = os.fstat(stream.fileno())
        raw = stream.read()
    if (sha256_bytes(raw) != descriptor["sha256"]
            or (stat.st_dev, stat.st_ino) != (descriptor["device"], descriptor["inode"])):
        raise ValueError("frozen research.frame identity changed")
    frame = validate_decision_frame(json.loads(raw))
    if capture:
        materialize_tool_receipts(handle)
    receipts = read_receipts(handle.capsule)
    if any(row["engine"] != handle.engine or row["run_id"] != handle.run_id for row in receipts):
        raise ValueError("source receipt does not belong to active run")
    return {"handle": handle, "frame": frame, "receipts": receipts}


def intel_claim_sources(scan_dir: Path | str) -> dict | None:
    context = frozen_source_context(scan_dir, capture=True)
    if context is None:
        return None
    task_id = os.environ.get("AUTORESEARCH_TASK_ID", "").strip()
    attempt = int(os.environ.get("AUTORESEARCH_ATTEMPT", "1"))
    if not task_id or attempt < 1:
        raise ValueError("intel evidence requires explicit task/attempt identity")
    return {**context, "task_id": task_id, "attempt": attempt}

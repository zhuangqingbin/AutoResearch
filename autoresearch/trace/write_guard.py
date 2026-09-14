"""Run-scoped write barrier shared by publication and finalization.

Tracked runs fail closed: their engine, active state, workflow ownership, and
workspace identity are checked before a business writer may create a path.  A
small compatibility context is retained for old in-process callers that never
created a run ``state.json``; those callers are untracked and cannot acquire a
session identity through this module.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import threading
from collections.abc import Iterator
from pathlib import Path

from autoresearch.common import workspace as ws

_OPERATION_PREFIX = {
    "scan-market": "scan.",
    "stock-research": "stock.",
    "macro-research": "macro.",
    "sector-research": "sector.",
    "dossier-init": "dossier.",
}
_LOCAL = threading.local()


class RunWriteViolation(RuntimeError):
    """A stable error code for writes rejected before business mutation."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def _workspace(run_id: str) -> Path:
    try:
        resolved = ws.validate_run_id(run_id)
    except ValueError as exc:
        raise RunWriteViolation("RUN_ID_INVALID", str(exc)) from exc
    workspace = ws.find_run_root(resolved)
    if workspace is None:
        raise RunWriteViolation("RUN_NOT_FOUND", f"run {resolved} has no workspace")
    return workspace


def _held_locks() -> dict[str, int]:
    held = getattr(_LOCAL, "held", None)
    if held is None:
        held = {}
        _LOCAL.held = held
    return held


@contextlib.contextmanager
def run_write_lock(run_id: str) -> Iterator[None]:
    """Serialize business writes and sealing with one re-entrant per-run lock."""
    workspace = _workspace(run_id)
    lock_path = workspace / ".run-write.lock"
    key = str(lock_path.absolute())
    held = _held_locks()
    if held.get(key, 0):
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    with lock_path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        held[key] = 1
        try:
            yield
        finally:
            held.pop(key, None)
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _registered_operations(workspace: Path) -> set[str] | None:
    plan = workspace / "session/plan.json"
    if not plan.is_file():
        return None
    try:
        payloads = [json.loads(plan.read_text(encoding="utf-8"))]
        expansion_root = workspace / "session/expansions"
        if expansion_root.is_dir():
            payloads.extend(
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(expansion_root.glob("*.json"))
            )
    except Exception as exc:
        raise RunWriteViolation("RUN_PLAN_INVALID", str(exc)) from exc
    return {
        str(task["operation"])
        for payload in payloads
        for task in payload.get("tasks", [])
        if task.get("operation")
    }


def assert_write_allowed(run_id: str, operation: str, engine: str):
    """Return the verified active handle or raise a stable pre-write error."""
    if engine != ws.ENGINE:
        raise RunWriteViolation(
            "RUN_ENGINE_MISMATCH",
            f"requested engine {engine!r} != process engine {ws.ENGINE!r}",
        )
    if type(operation) is not str or not operation:
        raise RunWriteViolation("RUN_OPERATION_INVALID", "operation is required")
    workspace = _workspace(run_id)
    state_path = workspace / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RunWriteViolation("RUN_STATE_INVALID", str(exc)) from exc
    if state.get("run_id") != run_id:
        raise RunWriteViolation("RUN_ID_MISMATCH", "state belongs to another run")
    if state.get("business_status") != "ACTIVE":
        raise RunWriteViolation(
            "RUN_NOT_ACTIVE",
            f"run {run_id} is {state.get('business_status') or 'UNKNOWN'}",
        )

    from autoresearch.trace.capsule import load_run

    try:
        handle = load_run(run_id)
    except Exception as exc:
        raise RunWriteViolation("RUN_IDENTITY_INVALID", str(exc)) from exc
    if handle.engine != engine or handle.contract.engine != engine:
        raise RunWriteViolation("RUN_ENGINE_MISMATCH", "run contract engine disagrees")
    if Path(handle.workspace).resolve() != workspace.resolve():
        raise RunWriteViolation("RUN_WORKSPACE_MISMATCH", "run handle escaped its workspace")
    try:
        workspace.resolve().relative_to(ws.context_root().resolve())
    except ValueError as exc:
        raise RunWriteViolation(
            "RUN_WORKSPACE_MISMATCH", "workspace is outside the current engine root"
        ) from exc

    registered = _registered_operations(workspace)
    if registered is not None:
        owned = operation in registered
    else:
        owned = operation.startswith(_OPERATION_PREFIX[handle.contract.run_kind])
    if not owned:
        raise RunWriteViolation(
            "RUN_OPERATION_NOT_OWNED",
            f"{operation!r} does not belong to {handle.contract.run_kind!r}",
        )
    return handle


def assert_output_path(path: Path | str, allowed_root: Path | str) -> Path:
    """Validate a prospective path without creating either it or its parents."""
    target = Path(path).resolve(strict=False)
    root = Path(allowed_root).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RunWriteViolation(
            "OUTPUT_ROOT_MISMATCH", f"{target} is outside {root}"
        ) from exc
    current = root
    if current.is_symlink():
        raise RunWriteViolation("OUTPUT_ROOT_MISMATCH", f"symlink root: {current}")
    relative = target.relative_to(root)
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise RunWriteViolation("OUTPUT_ROOT_MISMATCH", f"symlink path: {current}")
    return target


@contextlib.contextmanager
def guarded_handle_write(handle, operation: str) -> Iterator[object | None]:
    """Guard a real run; preserve structural legacy test doubles as untracked."""
    from autoresearch.trace.capsule_models import RunHandle

    if not isinstance(handle, RunHandle):
        yield None
        return
    with run_write_lock(handle.run_id):
        yield assert_write_allowed(handle.run_id, operation, handle.engine)


@contextlib.contextmanager
def guarded_ambient_write(operation: str) -> Iterator[object | None]:
    """Guard a CLI bound through ``AUTORESEARCH_RUN_ID``; no id means untracked."""
    run_id = ws.active_run_id()
    if run_id is None:
        yield None
        return
    with run_write_lock(run_id):
        yield assert_write_allowed(run_id, operation, ws.ENGINE)


__all__ = [
    "RunWriteViolation",
    "assert_output_path",
    "assert_write_allowed",
    "guarded_ambient_write",
    "guarded_handle_write",
    "run_write_lock",
]

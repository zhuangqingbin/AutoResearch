"""Locked, idempotent state for non-L4 session handoff tasks."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json, canonical_json
from autoresearch.contracts.session_plan import validate_plan
from autoresearch.contracts.session_task import validate_submission


class TaskConflict(RuntimeError):
    """A stale attempt, different owner, or conflicting result was supplied."""


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeError("invalid session task store")
    return value


def _entry(spec: dict) -> dict:
    return {
        "spec": spec,
        "state": "PENDING",
        "attempt": 0,
        "session_ref": None,
        "claim_receipt": None,
        "submission_hash": None,
        "outputs": [],
        "error": None,
    }


def initialize(path: Path | str, plan: dict) -> Path:
    validate_plan(plan)
    target = Path(path)
    payload = {
        "schema_version": 1,
        "engine": plan["engine"],
        "run_id": plan["run_id"],
        "input_contract_hash": plan["input_contract_hash"],
        "plan_hash": plan["plan_hash"],
        "tasks": {
            task["task_id"]: _entry(task)
            for task in plan["tasks"]
            if task["owner"] == "SESSION"
        },
    }
    with _locked(target):
        if target.is_file():
            current = _load(target)
            if canonical_json(current) != canonical_json(payload):
                raise TaskConflict("task store already initialized with different plan")
            return target
        return atomic_write_json(target, payload)


def read_states(path: Path | str) -> dict[str, str]:
    payload = _load(Path(path))
    return {task_id: entry["state"] for task_id, entry in payload["tasks"].items()}


def read_entry(path: Path | str, task_id: str) -> dict:
    target = Path(path)
    with _locked(target):
        payload = _load(target)
        if task_id not in payload["tasks"]:
            raise KeyError(task_id)
        return json.loads(canonical_json(payload["tasks"][task_id]))


def claim(
    path: Path | str,
    task_id: str,
    expected_attempt: int,
    session_ref: str,
    input_snapshots: list[dict] | None = None,
) -> dict:
    if type(expected_attempt) is not int or expected_attempt < 1:
        raise ValueError("expected_attempt must be a positive integer")
    if type(session_ref) is not str or not session_ref:
        raise ValueError("session_ref required")
    target = Path(path)
    with _locked(target):
        payload = _load(target)
        if task_id not in payload["tasks"]:
            raise KeyError(task_id)
        entry = payload["tasks"][task_id]
        if entry["state"] == "RUNNING":
            if entry["attempt"] == expected_attempt and entry["session_ref"] == session_ref:
                return entry["claim_receipt"]
            raise TaskConflict("task is already claimed")
        if entry["state"] == "SUCCEEDED":
            raise TaskConflict("task already succeeded")
        if entry["state"] == "BLOCKED":
            raise TaskConflict("blocked task requires explicit recovery")
        if expected_attempt != entry["attempt"] + 1:
            raise TaskConflict("expected attempt does not match next attempt")
        for dependency in entry["spec"]["dependencies"]:
            dependency_entry = payload["tasks"].get(dependency)
            if dependency_entry is not None and dependency_entry["state"] != "SUCCEEDED":
                raise TaskConflict(f"dependency has not succeeded: {dependency}")
        receipt = {
            "schema_version": 1,
            "task_id": task_id,
            "attempt": expected_attempt,
            "session_ref": session_ref,
            "spec": entry["spec"],
            "input_snapshots": input_snapshots or [],
        }
        entry.update({
            "state": "RUNNING",
            "attempt": expected_attempt,
            "session_ref": session_ref,
            "claim_receipt": receipt,
            "submission_hash": None,
            "outputs": [],
            "error": None,
        })
        atomic_write_json(target, payload)
        return receipt


def _submission_digest(submission: dict) -> str:
    return hashlib.sha256(canonical_json(submission).encode("utf-8")).hexdigest()


def _receipt(entry: dict, task_id: str) -> dict:
    identity = {
        "task_id": task_id,
        "attempt": entry["attempt"],
        "submission_hash": entry["submission_hash"],
        "outputs": entry["outputs"],
    }
    receipt_id = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "receipt_id": receipt_id,
        "task_id": task_id,
        "attempt": entry["attempt"],
        "status": "ACCEPTED",
        "submission_hash": entry["submission_hash"],
        "outputs": entry["outputs"],
    }


def _receipt_path(path: Path, task_id: str) -> Path:
    return path.parent / "receipts" / f"{task_id}.accepted.json"


def _intent_path(path: Path, task_id: str) -> Path:
    return path.parent / "receipts" / f"{task_id}.intent.json"


def _verify_submission(payload: dict, entry: dict, submission: dict) -> str:
    envelope = submission["envelope"]
    spec = entry["spec"]
    if submission["plan_hash"] != payload["plan_hash"]:
        raise TaskConflict("submission plan_hash mismatch")
    if envelope["engine"] != payload["engine"] or envelope["run_id"] != payload["run_id"]:
        raise TaskConflict("submission run identity mismatch")
    if envelope["input_contract_hash"] != payload["input_contract_hash"]:
        raise TaskConflict("submission input contract mismatch")
    for field in ("task_id", "role", "input_artifact_ids", "expected_output_contract"):
        expected = spec[field]
        if envelope[field] != expected:
            raise TaskConflict(f"submission {field} mismatch")
    if envelope["attempt"] != entry["attempt"]:
        raise TaskConflict("submission attempt mismatch")
    output_ids = [item["artifact_id"] for item in submission["outputs"]]
    if set(output_ids) != set(spec["output_artifact_ids"]):
        raise TaskConflict("submission output artifact mismatch")
    return _submission_digest(submission)


def accept(
    path: Path | str,
    submission: dict,
    validator: Callable[[dict, dict], object],
) -> dict:
    validate_submission(submission)
    target = Path(path)
    task_id = submission["envelope"]["task_id"]
    with _locked(target):
        payload = _load(target)
        if task_id not in payload["tasks"]:
            raise KeyError(task_id)
        entry = payload["tasks"][task_id]
        digest = _submission_digest(submission)
        if entry["state"] == "SUCCEEDED":
            if entry["submission_hash"] != digest:
                raise TaskConflict("different result already accepted")
            receipt = _receipt(entry, task_id)
            receipt_path = _receipt_path(target, task_id)
            if not receipt_path.is_file():
                atomic_write_json(receipt_path, receipt)
            return receipt
        if entry["state"] != "RUNNING":
            raise TaskConflict("task is not running")
        digest = _verify_submission(payload, entry, submission)
        validator(submission, entry["spec"])
        intent = {
            "schema_version": 1,
            "task_id": task_id,
            "attempt": entry["attempt"],
            "submission_hash": digest,
            "outputs": submission["outputs"],
        }
        atomic_write_json(_intent_path(target, task_id), intent)
        entry.update({
            "state": "SUCCEEDED",
            "submission_hash": digest,
            "outputs": submission["outputs"],
            "error": None,
        })
        atomic_write_json(target, payload)
        receipt = _receipt(entry, task_id)
        atomic_write_json(_receipt_path(target, task_id), receipt)
        return receipt


def recover_receipt(path: Path | str, task_id: str) -> dict | None:
    target = Path(path)
    with _locked(target):
        payload = _load(target)
        if task_id not in payload["tasks"]:
            raise KeyError(task_id)
        entry = payload["tasks"][task_id]
        if entry["state"] != "SUCCEEDED" or not entry["submission_hash"]:
            return None
        receipt = _receipt(entry, task_id)
        receipt_path = _receipt_path(target, task_id)
        if receipt_path.is_file():
            current = json.loads(receipt_path.read_text(encoding="utf-8"))
            if current != receipt:
                raise TaskConflict("accepted receipt conflicts with owner state")
            return current
        atomic_write_json(receipt_path, receipt)
        return receipt


def complete_deterministic(
    path: Path | str,
    task_id: str,
    attempt: int,
    outputs: list[dict],
    execution: dict,
) -> dict:
    """Commit a captured deterministic result without forging an inference envelope."""
    target = Path(path)
    with _locked(target):
        payload = _load(target)
        if task_id not in payload["tasks"]:
            raise KeyError(task_id)
        entry = payload["tasks"][task_id]
        if entry["spec"]["kind"] != "DETERMINISTIC":
            raise ValueError("task is not deterministic")
        identity = {"outputs": outputs, "execution": execution}
        digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        if entry["state"] == "SUCCEEDED":
            if entry["submission_hash"] != digest:
                raise TaskConflict("different deterministic result already accepted")
            return _receipt(entry, task_id)
        if entry["state"] != "RUNNING" or entry["attempt"] != attempt:
            raise TaskConflict("deterministic completion attempt mismatch")
        if execution.get("status") != "SUCCEEDED" or execution.get("exit_code") != 0:
            raise ValueError("deterministic execution did not succeed")
        expected = set(entry["spec"]["output_artifact_ids"])
        actual = {item.get("artifact_id") for item in outputs}
        if actual != expected or any(not item.get("sha256") for item in outputs):
            raise ValueError("deterministic outputs do not match task")
        intent = {
            "schema_version": 1,
            "task_id": task_id,
            "attempt": attempt,
            "submission_hash": digest,
            "outputs": outputs,
        }
        atomic_write_json(_intent_path(target, task_id), intent)
        entry.update({
            "state": "SUCCEEDED",
            "submission_hash": digest,
            "outputs": outputs,
            "error": None,
        })
        atomic_write_json(target, payload)
        receipt = _receipt(entry, task_id)
        atomic_write_json(_receipt_path(target, task_id), receipt)
        return receipt


def mark_failed(
    path: Path | str,
    task_id: str,
    attempt: int,
    error: dict,
    *,
    retryable: bool,
) -> None:
    target = Path(path)
    with _locked(target):
        payload = _load(target)
        entry = payload["tasks"][task_id]
        if entry["state"] != "RUNNING" or entry["attempt"] != attempt:
            raise TaskConflict("failure attempt mismatch")
        entry["state"] = "FAILED" if retryable else "BLOCKED"
        entry["error"] = error
        atomic_write_json(target, payload)


__all__ = [
    "TaskConflict", "accept", "claim", "complete_deterministic", "initialize",
    "mark_failed", "read_entry", "read_states", "recover_receipt",
]

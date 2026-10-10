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
from autoresearch.contracts.session_task import validate_submission, validate_task


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
    storage = target.parent / 'storage.json'
    if storage.is_file():
        declaration = json.loads(storage.read_text())
        version = declaration['output_layout_version']
        if type(version) is not int or version not in {1, 2}:
            raise ValueError('unsupported output layout version')
        if (declaration.get('run_id', plan['run_id']) != plan['run_id']
                or declaration.get('plan_hash', plan['plan_hash']) != plan['plan_hash']):
            raise TaskConflict('output storage does not match frozen plan')
        payload['output_layout_version'] = version
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


def read_entries(path: Path | str) -> dict[str, dict]:
    """Every entry (copy) under the lock — the durable state a restarted runner reads."""
    target = Path(path)
    with _locked(target):
        return json.loads(canonical_json(_load(target)["tasks"]))


def register_tasks(
    path: Path | str,
    tasks: list[dict],
    *,
    plan_hash: str,
) -> None:
    """Add expanded SESSION tasks without taking ownership of L4 taskbook state."""
    if not isinstance(tasks, list):
        raise ValueError("tasks must be a list")
    for task in tasks:
        validate_task(task)
    target = Path(path)
    with _locked(target):
        payload = _load(target)
        if payload["plan_hash"] != plan_hash:
            raise TaskConflict("dynamic task plan_hash mismatch")
        changed = False
        for task in tasks:
            if task["owner"] != "SESSION":
                continue
            task_id = task["task_id"]
            current = payload["tasks"].get(task_id)
            if current is None:
                payload["tasks"][task_id] = _entry(task)
                changed = True
            elif canonical_json(current["spec"]) != canonical_json(task):
                raise TaskConflict(f"dynamic task has different spec: {task_id}")
        if changed:
            atomic_write_json(target, payload)


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
        if ((entry.get("error") or {}).get("code") == "USAGE_LIMIT"
                and not recovery_authorized(entry)):
            raise TaskConflict("usage limit requires explicit recovery")
        if recovery_authorized(entry):
            def identities(rows):
                return {row["artifact_id"]: row["sha256"] for row in rows}
            if identities(input_snapshots or []) != identities(entry["claim_receipt"]["input_snapshots"]):
                raise TaskConflict("recovery input snapshots changed")
        if expected_attempt != entry["attempt"] + 1:
            raise TaskConflict("expected attempt does not match next attempt")
        for dependency in entry["spec"]["dependencies"]:
            dependency_entry = payload["tasks"].get(dependency)
            # Same rule as plan.ready_tasks: SUPERSEDED (retried L4 child replaced by its
            # verified retry, or a released optional L3 repair) satisfies dependents.
            if dependency_entry is not None and dependency_entry["state"] not in {
                "SUCCEEDED", "SUPERSEDED",
            }:
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


def precheck(
    path: Path | str, submission: dict,
    validator: Callable[[dict, dict, dict, Callable[[str], dict]], object],
):
    """Validate a current attempt under its owner lock without committing anything."""
    validate_submission(submission)
    target = Path(path)
    task_id = submission["envelope"]["task_id"]
    with _locked(target):
        payload = _load(target)
        entry = payload["tasks"][task_id]
        if entry["state"] != "RUNNING":
            raise TaskConflict("task is not running")
        _verify_submission(payload, entry, submission)
        # Nested validators must read this snapshot rather than reacquire our lock.
        def read_locked_entry(task_id):
            return json.loads(canonical_json(payload["tasks"][task_id]))
        result = validator(submission, entry["spec"], entry, read_locked_entry)
        _verify_submission(payload, entry, submission)
        return result


def accept(
    path: Path | str,
    submission: dict,
    validator: Callable[[dict, dict], object],
    *, prepare: Callable | None = None,
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
        if payload.get('output_layout_version', 1) >= 2 and prepare is None:
            raise TaskConflict('new layout requires captured outputs')
        manifest = prepare(submission, entry['spec']) if prepare else None
        validator(submission, entry["spec"])
        _verify_submission(payload, entry, submission)
        if manifest is not None:
            _attach_manifest(payload, entry, manifest)
            entry['accepted_submission'] = submission
            _promote_alias(payload, entry, manifest)
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
    *, accepted_artifacts: dict | None = None,
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
        if payload.get('output_layout_version', 1) >= 2 and accepted_artifacts is None:
            raise TaskConflict('new layout requires captured deterministic outputs')
        if accepted_artifacts is not None:
            _attach_manifest(payload, entry, accepted_artifacts)
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


def recovery_authorized(entry: dict) -> bool:
    """A grant applies only to the exact still-failed attempt/error/input receipt."""
    grants = entry.get("recovery_authorizations") or []
    if not grants or entry["state"] != "FAILED":
        return False
    grant = grants[-1]
    return (grant["failed_attempt"] == entry["attempt"]
            and grant["next_attempt"] == entry["attempt"] + 1
            and grant["failure_hash"] == _submission_digest(entry["error"])
            and grant["claim_hash"] == _submission_digest(entry["claim_receipt"]))


def authorize_recovery(path: Path | str, task_id: str, failed_attempt: int, reason: str) -> dict:
    from datetime import datetime, timezone
    target = Path(path)
    with _locked(target):
        payload = _load(target)
        entry = payload["tasks"][task_id]
        if entry["state"] != "FAILED" or entry["attempt"] != failed_attempt:
            raise TaskConflict("recovery failed attempt mismatch")
        if recovery_authorized(entry):
            return entry["recovery_authorizations"][-1]
        grant = {
            "failed_attempt": failed_attempt, "next_attempt": failed_attempt + 1,
            "reason": reason, "authorized_at": datetime.now(timezone.utc).isoformat(),
            "failure_hash": _submission_digest(entry["error"]),
            "claim_hash": _submission_digest(entry["claim_receipt"]),
        }
        entry.setdefault("recovery_authorizations", []).append(grant)
        atomic_write_json(target, payload)
        return grant


def supersede_optional_failure(
    path: Path | str,
    task_ids: list[str],
    error: dict,
    *, accepted_artifacts: dict | None = None,
) -> None:
    """Release a declared optional branch while preserving its failure evidence."""
    if not task_ids:
        raise ValueError("optional failure requires at least one task")
    target = Path(path)
    with _locked(target):
        payload = _load(target)
        entries = []
        for task_id in task_ids:
            entry = payload["tasks"].get(task_id)
            if entry is None:
                raise KeyError(task_id)
            if entry["state"] not in {"PENDING", "FAILED", "BLOCKED", "SUPERSEDED"}:
                raise TaskConflict(
                    f"optional task cannot be superseded from {entry['state']}: {task_id}"
                )
            entries.append(entry)
        if accepted_artifacts is not None:
            _attach_manifest(payload, entries[-1], accepted_artifacts)
        for entry in entries:
            entry["state"] = "SUPERSEDED"
            if entry["error"] is None:
                entry["error"] = {
                    "code": "OPTIONAL_PREDECESSOR_FAILED",
                    "cause": error,
                }
        atomic_write_json(target, payload)


def prepare_l4_retry(path: Path | str, code: str, previous_attempt: int,
                     *, retained_task_ids: list[str] | None = None) -> None:
    """Retire one failed child subtree while its replacement remains auditable."""
    target = Path(path)
    with _locked(target):
        payload = _load(target)
        matching = [
            entry
            for entry in payload["tasks"].values()
            if (entry["spec"].get("parent_task") or {}).get("subject") == code
            and (entry["spec"].get("parent_task") or {}).get("attempt")
            == previous_attempt
        ]
        if any(entry["state"] == "RUNNING" for entry in matching):
            raise TaskConflict("L4 retry requires every previous child to be quiescent")
        retained = set(retained_task_ids or [])
        eligible = {entry["spec"]["task_id"] for entry in matching
                    if entry["state"] == "SUCCEEDED"}
        if not retained <= eligible:
            raise TaskConflict("retained L4 evidence must be accepted children of the previous attempt")
        matched = False
        for entry in matching:
            matched = True
            if entry["spec"]["task_id"] in retained:
                continue
            if (entry["spec"].get("expected_output_contract") == "research.card.initial.v1"
                    and entry["state"] == "SUCCEEDED"):
                continue
            entry["state"] = (
                "WAITING_RETRY"
                if entry["spec"]["task_id"].endswith(".card")
                else "SUPERSEDED"
            )
        if not matching or not matched:
            raise KeyError(f"L4 child subtree is missing: {code}/a{previous_attempt}")
        atomic_write_json(target, payload)


def complete_l4_retry_alias(path: Path | str, code: str, previous_attempt: int) -> None:
    """Release the original review dependency after the retry card is verified."""
    target = Path(path)
    task_id = f"l4.{code}.a{previous_attempt}.card"
    with _locked(target):
        payload = _load(target)
        entry = payload["tasks"].get(task_id)
        if entry is None:
            raise KeyError(task_id)
        if entry["state"] not in {"WAITING_RETRY", "SUPERSEDED"}:
            raise TaskConflict("original L4 card is not waiting for retry")
        entry["state"] = "SUPERSEDED"
        atomic_write_json(target, payload)


__all__ = [
    "TaskConflict", "accept", "precheck", "claim", "complete_deterministic", "initialize",
    "complete_l4_retry_alias", "mark_failed", "prepare_l4_retry", "read_entries", "read_entry",
    "read_states", "recover_receipt", "register_tasks", "supersede_optional_failure",
]


def _promote_alias(payload, entry, manifest):
    import re
    match = re.fullmatch(r'l4\.(\d{6})\.a(\d+)\.card', entry['spec']['task_id'])
    if match is None or int(match.group(2)) < 2:
        return
    code, attempt = match.groups()
    previous = int(attempt) - 1
    original = payload['tasks'][f'l4.{code}.a{previous}.card']
    if original['state'] != 'WAITING_RETRY':
        raise TaskConflict('original L4 card is not waiting for retry')
    key = f'scan.l4.{code}.a{previous}.card'
    replacement = f'scan.l4.{code}.a{attempt}.card'
    if original.get('accepted_artifacts'):
        original.setdefault('accepted_history', []).append(original['accepted_artifacts'])
    original['accepted_artifacts'] = {key: {**manifest[replacement], 'artifact_id': key}}
    original['state'] = 'SUPERSEDED'


def _attach_manifest(payload, entry, manifest):
    payload['accepted_revision'] = payload.get('accepted_revision', 0) + 1
    entry['accepted_revision'] = payload['accepted_revision']
    entry['accepted_artifacts'] = manifest

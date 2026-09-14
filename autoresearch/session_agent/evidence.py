"""Project the frozen task graph into a hash-checked forensic evidence closure."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    sha256_bytes,
    sha256_file,
)
from autoresearch.contracts.forensic import (
    evidence_plan_hash,
    validate_evidence_plan,
    validate_task_evidence,
)
from autoresearch.contracts.session_plan import validate_expansion, validate_plan
from autoresearch.session_agent import artifacts, plan as plan_service

_SOURCE_OPERATIONS = frozenset({
    "stock.harvest",
    "macro.harvest",
    "macro.lite.frame",
    "sector.prepare",
    "dossier.prefetch",
    "scan.frame",
    "scan.prelude",
    "scan.sector.prepare",
    "scan.l4.slim",
})


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _timestamp(handle, now: datetime | None) -> str:
    if now is not None:
        stamp = now
    else:
        state_path = Path(handle.workspace) / "state.json"
        if state_path.is_file():
            return str(_read_json(state_path)["updated_at"])
        stamp = datetime.now(timezone.utc)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("closure timestamp must be timezone-aware")
    return stamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _expanded_tasks(handle, plan: dict) -> tuple[list[dict], list[str]]:
    tasks = list(plan["tasks"])
    pending: list[dict] = []
    for folder in ("expansions", "recoveries"):
        root = Path(handle.workspace) / "session" / folder
        if root.is_dir():
            pending.extend(_read_json(path) for path in sorted(root.glob("*.json")))
    hashes = [str(item["expansion_hash"]) for item in pending]
    while pending:
        known = {task["task_id"] for task in tasks}
        ready = []
        waiting = []
        for expansion in pending:
            validate_expansion(expansion)
            own = {task["task_id"] for task in expansion["tasks"]}
            external = {
                dependency
                for task in expansion["tasks"]
                for dependency in task["dependencies"]
            } - own
            (ready if external <= known else waiting).append(expansion)
        if not ready:
            raise ValueError("expanded evidence graph has unresolved dependencies")
        for expansion in ready:
            tasks = plan_service.apply_expansion(plan, expansion, existing_tasks=tasks)
        pending = waiting
    return tasks, hashes


def _state_view(handle, tasks: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
    store_path = Path(handle.workspace) / "session/tasks.json"
    payload = _read_json(store_path)
    entries = dict(payload.get("tasks") or {})
    states = {task_id: str(entry.get("state") or "PENDING") for task_id, entry in entries.items()}
    owner_tasks = [task for task in tasks if task["owner"] == "L4_TASKBOOK"]
    if owner_tasks:
        from autoresearch.session_agent import legacy_scan

        if legacy_scan.taskbook_path(handle).is_file():
            states.update(legacy_scan.ticket_states(handle, owner_tasks))
        else:
            states.update({task["task_id"]: "PENDING" for task in owner_tasks})
    return entries, states


def _normalized_state(value: str) -> str:
    if value == "PENDING":
        return "NOT_REACHED"
    if value == "WAITING_RETRY":
        return "SUPERSEDED"
    if value in {
        "READY",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "BLOCKED",
        "SUPERSEDED",
        "CANCELLED",
        "NOT_REACHED",
    }:
        return value
    raise ValueError(f"unsupported owner task state: {value}")


def _requirements(task: dict, state: str) -> list[str]:
    if state == "NOT_REACHED":
        return []
    required = ["claim"]
    if task["input_artifact_ids"]:
        required.append("input_snapshot")
    if state == "SUCCEEDED":
        required.extend(["outputs", "accepted_receipt"])
    if task["kind"] == "DETERMINISTIC":
        required.append("command_capture")
        if task["operation"] in _SOURCE_OPERATIONS:
            required.append("source_receipts")
    else:
        required.append("transcript")
        from autoresearch.session_agent.roles import get_role

        if "WEB" in get_role(task["role"])["tool_policy"].split("_"):
            required.extend(["tool_results", "source_receipts"])
    return required


def _replacement(tasks: list[dict], task: dict, attempt: int) -> dict | None:
    if task["owner"] == "SESSION":
        return {"task_id": task["task_id"], "attempt": attempt + 1}
    prefix = re.sub(r"\.a\d+$", "", task["task_id"])
    candidates = []
    for item in tasks:
        match = re.fullmatch(rf"{re.escape(prefix)}\.a(\d+)", item["task_id"])
        if match and int(match.group(1)) > attempt:
            candidates.append((int(match.group(1)), item["task_id"]))
    if not candidates:
        return None
    replacement_attempt, task_id = min(candidates)
    return {"task_id": task_id, "attempt": replacement_attempt}


def build_evidence_plan(handle, *, now: datetime | None = None) -> dict:
    """Build the immutable denominator from plans and authoritative owner state."""
    plan = validate_plan(_read_json(Path(handle.workspace) / "session/plan.json"))
    tasks, expansion_hashes = _expanded_tasks(handle, plan)
    entries, states = _state_view(handle, tasks)
    keys = []
    for task in tasks:
        entry = entries.get(task["task_id"], {})
        current_attempt = max(1, int(entry.get("attempt") or 0))
        attempts = range(1, current_attempt + 1)
        for attempt in attempts:
            if attempt < current_attempt:
                state = "SUPERSEDED"
            else:
                state = _normalized_state(states.get(task["task_id"], "PENDING"))
            superseded_by = _replacement(tasks, task, attempt) if state == "SUPERSEDED" else None
            if state == "SUPERSEDED" and superseded_by is None:
                state = "CANCELLED"
            keys.append({
                "task_id": task["task_id"],
                "attempt": attempt,
                "owner": task["owner"],
                "subject": task["subject"],
                "state": state,
                "superseded_by": superseded_by,
                "requirements": _requirements(task, state),
            })
    value = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "plan_hash": plan["plan_hash"],
        "expansion_hashes": expansion_hashes,
        "task_keys": keys,
        "closure_cutoff": _timestamp(handle, now),
        "scope": [handle.contract.run_kind],
        "evidence_plan_hash": "0" * 64,
    }
    value["evidence_plan_hash"] = evidence_plan_hash(value)
    return validate_evidence_plan(value)


def _task_root(capsule: Path, task_id: str, attempt: int) -> Path:
    return capsule / "evidence/tasks" / task_id / f"a{attempt}"


def _attempt_record_path(handle, task_id: str, attempt: int, kind: str) -> Path:
    return (
        Path(handle.capsule)
        / "evidence/attempt_records"
        / task_id
        / f"a{attempt}"
        / f"{kind}.json"
    )


def _freeze_attempt_record(
    handle,
    task_id: str,
    attempt: int,
    kind: str,
    value: dict,
) -> Path:
    path = _attempt_record_path(handle, task_id, attempt, kind)
    if path.is_file():
        current = _read_json(path)
        if canonical_json(current) != canonical_json(value):
            raise RuntimeError(
                f"frozen {kind} changed for {task_id}:a{attempt}"
            )
        return path
    return atomic_write_json(path, value)


def freeze_claim(handle, task: dict, attempt: int, receipt: dict) -> Path:
    """Persist the exact claim before a later retry can replace owner state."""
    return _freeze_attempt_record(
        handle, task["task_id"], attempt, "claim", receipt
    )


def freeze_receipt(handle, task: dict, attempt: int, receipt: dict) -> Path:
    """Persist an accepted receipt under its immutable task-attempt identity."""
    return _freeze_attempt_record(
        handle, task["task_id"], attempt, "accepted_receipt", receipt
    )


def freeze_operation_request(
    handle,
    task: dict,
    attempt: int,
    params: dict,
) -> Path:
    """Freeze structured operation parameters before the child process starts."""
    if task.get("kind") != "DETERMINISTIC" or not isinstance(params, dict):
        raise ValueError("operation request requires a deterministic task and params")
    frozen_clock = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    return _freeze_attempt_record(
        handle,
        task["task_id"],
        attempt,
        "operation_request",
        {
            "schema_version": 1,
            "task_id": task["task_id"],
            "attempt": attempt,
            "operation": task["operation"],
            "subject": task.get("subject"),
            "params": json.loads(canonical_json(params)),
            "frozen_clock": frozen_clock,
        },
    )


def freeze_failure(
    handle,
    task: dict,
    attempt: int,
    error: dict,
) -> Path:
    """Keep a retry's terminal failure after mutable owner state advances."""
    if not isinstance(error, dict):
        raise ValueError("failure evidence must be an object")
    from autoresearch.trace.identity import redact_value

    safe = redact_value(error).value
    if not isinstance(safe, dict):
        raise ValueError("redacted failure evidence must remain an object")
    return _freeze_attempt_record(
        handle,
        task["task_id"],
        attempt,
        "failure",
        {
            "schema_version": 1,
            "task_id": task["task_id"],
            "attempt": attempt,
            "error": safe,
        },
    )


def _captured_ref(
    handle,
    artifact_id: str,
    destination: Path,
    *,
    expected_hash: str | None = None,
) -> tuple[dict | None, str | None]:
    try:
        with artifacts.open_artifact(handle, artifact_id) as stream:
            payload = stream.read()
        digest = sha256_bytes(payload)
        if expected_hash is not None and digest != expected_hash:
            return None, f"ARTIFACT_HASH_MISMATCH:{artifact_id}"
        atomic_write_bytes(destination, payload)
        return {
            "artifact_id": artifact_id,
            "sha256": digest,
            "captured_path": destination.relative_to(handle.capsule).as_posix(),
        }, None
    except Exception as exc:
        return None, f"ARTIFACT_MISSING:{artifact_id}:{type(exc).__name__}"


def _json_ref(
    handle,
    artifact_id: str,
    destination: Path,
    value: dict | None,
) -> tuple[dict | None, str | None]:
    if not isinstance(value, dict):
        return None, f"EVIDENCE_MISSING:{artifact_id}"
    atomic_write_json(destination, value)
    return {
        "artifact_id": artifact_id,
        "sha256": sha256_file(destination),
        "captured_path": destination.relative_to(handle.capsule).as_posix(),
    }, None


def _command_ref(handle, task: dict, attempt: int, root: Path) -> tuple[dict | None, list[str]]:
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", task["task_id"])
    invocation_id = f"session-{safe}-a{attempt}"
    index_path = Path(handle.capsule) / "events/invocations.json"
    try:
        invocation = _read_json(index_path)[invocation_id]
        if invocation.get("status") != "COMPLETED":
            raise ValueError("invocation is not completed")
        command_root = root / "command"
        hashes = {}
        for channel in ("stdout", "stderr"):
            source = Path(handle.capsule) / invocation[f"{channel}_log"]
            target = command_root / f"{channel}.log.gz"
            atomic_write_bytes(target, source.read_bytes())
            hashes[channel] = sha256_file(target)
        atomic_write_json(command_root / "invocation.json", invocation)
        return {
            "argv": list(invocation["argv"]),
            "cwd": ".",
            "exit_code": invocation.get("exit_code"),
            "signal": invocation.get("signal"),
            "stdout_sha256": hashes["stdout"],
            "stderr_sha256": hashes["stderr"],
            "operation_version": f"{task['operation']}.v1",
        }, []
    except Exception as exc:
        return None, [f"COMMAND_CAPTURE_MISSING:{invocation_id}:{type(exc).__name__}"]


def _task_evidence(handle, task: dict, key: dict, entry: dict | None) -> dict:
    root = _task_root(Path(handle.capsule), task["task_id"], key["attempt"])
    reasons: list[str] = []
    current = entry if entry and int(entry.get("attempt") or 0) == key["attempt"] else {}
    claim_path = _attempt_record_path(
        handle, task["task_id"], key["attempt"], "claim"
    )
    claim = _read_json(claim_path) if claim_path.is_file() else current.get("claim_receipt")
    claim_ref, reason = _json_ref(
        handle,
        f"claim:{task['task_id']}:a{key['attempt']}",
        root / "claim.json",
        claim,
    )
    if reason:
        reasons.append(reason)

    input_refs = []
    snapshots = {
        item["artifact_id"]: item["sha256"]
        for item in (claim or {}).get("input_snapshots", [])
    }
    for artifact_id in task["input_artifact_ids"]:
        ref, reason = _captured_ref(
            handle,
            artifact_id,
            root / "inputs" / artifact_id,
            expected_hash=snapshots.get(artifact_id),
        )
        if ref:
            input_refs.append(ref)
        if reason:
            reasons.append(reason)

    output_refs = []
    requirements = set(key["requirements"])
    outputs = {
        item["artifact_id"]: item["sha256"] for item in current.get("outputs", [])
    }
    for artifact_id in (
        task["output_artifact_ids"] if "outputs" in requirements else []
    ):
        ref, reason = _captured_ref(
            handle,
            artifact_id,
            root / "outputs" / artifact_id,
            expected_hash=outputs.get(artifact_id),
        )
        if ref:
            output_refs.append(ref)
        if reason:
            reasons.append(reason)

    receipt_path = _attempt_record_path(
        handle, task["task_id"], key["attempt"], "accepted_receipt"
    )
    receipt = _read_json(receipt_path) if receipt_path.is_file() else None
    if receipt is None and "accepted_receipt" in requirements:
        current_receipt = (
            Path(handle.workspace)
            / "session/receipts"
            / f"{task['task_id']}.accepted.json"
        )
        if current_receipt.is_file():
            candidate = _read_json(current_receipt)
            if int(candidate.get("attempt") or 0) == key["attempt"]:
                receipt = candidate
    receipt_ref, reason = _json_ref(
        handle,
        f"receipt:{task['task_id']}:a{key['attempt']}",
        root / "accepted_receipt.json",
        receipt,
    )
    if reason:
        reasons.append(reason)

    command_ref = None
    if task["kind"] == "DETERMINISTIC" and key["state"] != "NOT_REACHED":
        command_ref, command_reasons = _command_ref(handle, task, key["attempt"], root)
        reasons.extend(command_reasons)

    transcript_refs = []
    if task["kind"] == "INFERENCE" and key["state"] != "NOT_REACHED":
        from autoresearch.session_agent.host_evidence import transcript_refs_for_task

        transcript_refs = transcript_refs_for_task(
            handle, task["task_id"], key["attempt"]
        )

    from autoresearch.trace.source_receipts import read_receipts

    task_receipts = [
        row
        for row in read_receipts(handle.capsule)
        if row["task_id"] == task["task_id"] and row["attempt"] == key["attempt"]
    ]
    source_receipt_ids = [row["receipt_id"] for row in task_receipts]

    relevant = []
    if "claim" in requirements and claim_ref is None:
        relevant.append(f"CLAIM_MISSING:{task['task_id']}:a{key['attempt']}")
    if "input_snapshot" in requirements and len(input_refs) != len(task["input_artifact_ids"]):
        relevant.append(f"INPUT_SNAPSHOT_MISSING:{task['task_id']}:a{key['attempt']}")
    if "outputs" in requirements and len(output_refs) != len(task["output_artifact_ids"]):
        relevant.append(f"OUTPUTS_MISSING:{task['task_id']}:a{key['attempt']}")
    if "accepted_receipt" in requirements and receipt_ref is None:
        relevant.append(f"ACCEPTED_RECEIPT_MISSING:{task['task_id']}:a{key['attempt']}")
    if "command_capture" in requirements and command_ref is None:
        relevant.append(f"COMMAND_CAPTURE_MISSING:{task['task_id']}:a{key['attempt']}")
    if "transcript" in requirements and not any(
        ref["status"] == "PRESENT" for ref in transcript_refs
    ):
        relevant.append(f"TRANSCRIPT_MISSING:{task['task_id']}:a{key['attempt']}")
    if "tool_results" in requirements and not any(
        row["provider"] == "host_tool" for row in task_receipts
    ):
        relevant.append(f"TOOL_RESULTS_MISSING:{task['task_id']}:a{key['attempt']}")
    if "source_receipts" in requirements and not source_receipt_ids:
        relevant.append(f"SOURCE_RECEIPTS_MISSING:{task['task_id']}:a{key['attempt']}")
    reasons = sorted({*reasons, *relevant})
    if key["state"] == "NOT_REACHED":
        status = "NOT_REACHED"
        reasons = []
    else:
        status = "MISSING" if relevant else "PRESENT"
        if status == "PRESENT":
            reasons = []
    value = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "task_id": task["task_id"],
        "attempt": key["attempt"],
        "owner": task["owner"],
        "subject": task["subject"],
        "input_refs": input_refs,
        "output_refs": output_refs,
        "claim_ref": claim_ref,
        "receipt_ref": receipt_ref,
        "command_ref": command_ref,
        "transcript_refs": transcript_refs,
        "source_receipt_ids": source_receipt_ids,
        "status": status,
        "reasons": reasons,
    }
    return validate_task_evidence(value)


def evaluate_closure(capsule: Path | str, evidence_plan: dict | None = None) -> dict:
    """Recompute task evidence coverage from frozen bytes without changing them."""
    from autoresearch.trace.evidence_closure import evaluate_task_evidence_closure

    return evaluate_task_evidence_closure(capsule, evidence_plan)


def materialize_evidence(handle, *, now: datetime | None = None) -> dict:
    """Freeze the denominator, capture referenced bytes, and write a recomputed verdict."""
    from autoresearch.session_agent.host_evidence import capture_main_context
    from autoresearch.trace.source_receipts import materialize_tool_receipts

    capture_main_context(handle)
    materialize_tool_receipts(handle)
    plan = build_evidence_plan(handle, now=now)
    atomic_write_json(Path(handle.capsule) / "evidence/evidence_plan.json", plan)
    tasks, _ = _expanded_tasks(handle, validate_plan(
        _read_json(Path(handle.workspace) / "session/plan.json")
    ))
    specs = {task["task_id"]: task for task in tasks}
    entries, _ = _state_view(handle, tasks)
    for key in plan["task_keys"]:
        evidence = _task_evidence(
            handle,
            specs[key["task_id"]],
            key,
            entries.get(key["task_id"]),
        )
        atomic_write_json(
            _task_root(Path(handle.capsule), key["task_id"], key["attempt"])
            / "evidence.json",
            evidence,
        )
    result = evaluate_closure(handle.capsule, plan)
    atomic_write_json(
        Path(handle.capsule) / "verification/evidence_closure.json",
        result,
    )
    return result


__all__ = [
    "build_evidence_plan",
    "evaluate_closure",
    "freeze_claim",
    "freeze_failure",
    "freeze_operation_request",
    "freeze_receipt",
    "materialize_evidence",
]

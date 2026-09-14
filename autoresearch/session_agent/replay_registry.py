"""Project frozen session evidence into the complete offline replay denominator."""

from __future__ import annotations

import json
import re
from pathlib import Path

from autoresearch.common.atomic import canonical_json, sha256_bytes, sha256_file
from autoresearch.contracts.forensic import validate_evidence_plan, validate_task_evidence
from autoresearch.contracts.replay import replay_plan_hash, validate_replay_plan
from autoresearch.contracts.session_plan import validate_expansion, validate_plan
from autoresearch.session_agent.operations import replay_classification


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _task_specs(capsule: Path) -> tuple[dict, dict[str, dict]]:
    plan = validate_plan(_read_json(capsule / "identity/session/plan.json"))
    tasks = list(plan["tasks"])
    expansion_root = capsule / "identity/session/expansions"
    pending = []
    if expansion_root.is_dir():
        pending = [_read_json(path) for path in sorted(expansion_root.glob("*.json"))]
    while pending:
        known = {task["task_id"] for task in tasks}
        ready = []
        waiting = []
        for expansion in pending:
            validate_expansion(expansion)
            own = {task["task_id"] for task in expansion["tasks"]}
            external = {
                dependency for task in expansion["tasks"] for dependency in task["dependencies"]
            } - own
            (ready if external <= known else waiting).append(expansion)
        if not ready:
            raise ValueError("frozen replay task graph has unresolved dependencies")
        for expansion in ready:
            tasks.extend(expansion["tasks"])
        pending = waiting
    return plan, {task["task_id"]: task for task in tasks}


def _artifact_ref(path: Path, capsule: Path, artifact_id: str) -> dict:
    return {
        "artifact_id": artifact_id,
        "sha256": sha256_file(path) if path.is_file() else "0" * 64,
        "captured_path": path.relative_to(capsule).as_posix(),
    }


def _operation_request_ref(capsule: Path, task_id: str, attempt: int) -> dict:
    path = capsule / "evidence/attempt_records" / task_id / f"a{attempt}" / "operation_request.json"
    return _artifact_ref(path, capsule, f"operation.request:{task_id}:a{attempt}")


def _session_request_ref(capsule: Path) -> dict | None:
    path = capsule / "identity/session/request.json"
    return _artifact_ref(path, capsule, "session.request") if path.is_file() else None


def _failure_expectation(capsule: Path, task_id: str, attempt: int) -> dict | None:
    path = capsule / "evidence/attempt_records" / task_id / f"a{attempt}" / "failure.json"
    if not path.is_file():
        return None
    payload = _read_json(path)
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    category = str(error.get("code") or error.get("category") or "RECORDED_FAILURE")
    return {
        "category": category,
        "message_hash": sha256_bytes(canonical_json(error).encode("utf-8")),
    }


def _policy(refs: list[dict]) -> dict:
    suffixes = {Path(ref["captured_path"]).suffix.lower() for ref in refs}
    if suffixes == {".json"}:
        name = "CANONICAL_JSON"
    elif suffixes == {".parquet"}:
        name = "PARQUET_VALUES"
    elif suffixes and suffixes <= {".md", ".txt", ".csv"}:
        name = "TEXT_NORMALIZED"
    else:
        name = "EXACT_BYTES"
    return {"policy": name, "version": 1, "ignored_fields": []}


def _missing_operation(task_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.:-]", "-", task_id)
    return f"unsupported.task-spec:{safe}"


def _mode(task: dict | None, key: dict) -> tuple[str, str | None]:
    if task is None:
        return "COMPUTE", _missing_operation(key["task_id"])
    if task["kind"] == "INFERENCE":
        return "EVIDENCE_ONLY", None
    operation = task.get("operation")
    if key["state"] in {"NOT_REACHED", "CANCELLED"}:
        return "CONTROL_ONLY", operation
    try:
        classification = replay_classification(operation)
    except KeyError:
        # Unknown operations stay executable in the denominator.  The executor will
        # return UNSUPPORTED_OPERATION instead of silently dropping the unit.
        return "COMPUTE", operation
    if classification == "TEST_ONLY":
        return "CONTROL_ONLY", operation
    return classification, operation


def build_replay_plan(handle) -> dict:
    """Build one ReplayUnit for every frozen EvidencePlan task-attempt key."""
    capsule = Path(handle.capsule).resolve()
    evidence_plan = validate_evidence_plan(_read_json(capsule / "evidence/evidence_plan.json"))
    if evidence_plan["engine"] != handle.engine or evidence_plan["run_id"] != handle.run_id:
        raise ValueError("evidence plan identity does not match replay handle")
    session_plan, specs = _task_specs(capsule)
    if session_plan["plan_hash"] != evidence_plan["plan_hash"]:
        raise ValueError("session and evidence plan hashes disagree")

    attempts_by_task: dict[str, list[int]] = {}
    for key in evidence_plan["task_keys"]:
        attempts_by_task.setdefault(key["task_id"], []).append(key["attempt"])
    terminal_attempt = {task_id: max(attempts) for task_id, attempts in attempts_by_task.items()}

    units = []
    for key in evidence_plan["task_keys"]:
        task_id = key["task_id"]
        attempt = key["attempt"]
        task = specs.get(task_id)
        mode, operation = _mode(task, key)
        evidence_path = capsule / "evidence/tasks" / task_id / f"a{attempt}" / "evidence.json"
        evidence = None
        if evidence_path.is_file():
            evidence = validate_task_evidence(_read_json(evidence_path))
            if evidence["task_id"] != task_id or evidence["attempt"] != attempt:
                raise ValueError("task evidence identity mismatch")
        input_refs = list((evidence or {}).get("input_refs") or [])
        output_refs = list((evidence or {}).get("output_refs") or [])
        for control_ref in (
            (evidence or {}).get("claim_ref"),
            (evidence or {}).get("receipt_ref"),
        ):
            if control_ref is not None and (
                control_ref["artifact_id"], control_ref["captured_path"]
            ) not in {
                (ref["artifact_id"], ref["captured_path"]) for ref in input_refs
            }:
                input_refs.append(control_ref)
        if task is not None and mode != "CONTROL_ONLY":
            have_inputs = {ref["artifact_id"] for ref in input_refs}
            for artifact_id in task["input_artifact_ids"]:
                if artifact_id not in have_inputs:
                    input_refs.append(
                        _artifact_ref(
                            capsule
                            / "evidence/tasks"
                            / task_id
                            / f"a{attempt}"
                            / "inputs"
                            / artifact_id,
                            capsule,
                            artifact_id,
                        )
                    )
            have_outputs = {ref["artifact_id"] for ref in output_refs}
            if key["state"] == "SUCCEEDED":
                for artifact_id in task["output_artifact_ids"]:
                    if artifact_id not in have_outputs:
                        output_refs.append(
                            _artifact_ref(
                                capsule
                                / "evidence/tasks"
                                / task_id
                                / f"a{attempt}"
                                / "outputs"
                                / artifact_id,
                                capsule,
                                artifact_id,
                            )
                        )
        source_receipt_ids = list((evidence or {}).get("source_receipt_ids") or [])
        if mode in {"COMPUTE", "SOURCE_REPLAY", "EFFECT_PLAN"}:
            request_ref = _operation_request_ref(capsule, task_id, attempt)
            if request_ref["artifact_id"] not in {ref["artifact_id"] for ref in input_refs}:
                input_refs.append(request_ref)
            session_ref = _session_request_ref(capsule)
            if session_ref is not None and session_ref["artifact_id"] not in {
                ref["artifact_id"] for ref in input_refs
            }:
                input_refs.append(session_ref)
        if mode == "SOURCE_REPLAY" and not source_receipt_ids:
            source_receipt_ids = ["0" * 64]
        if mode in {"EVIDENCE_ONLY", "CONTROL_ONLY"}:
            source_receipt_ids = []
        dependencies = []
        if task is not None:
            dependencies = [
                f"{dependency}:a{terminal_attempt[dependency]}"
                for dependency in task["dependencies"]
                if dependency in terminal_attempt
            ]
        unit_id = f"{task_id}:a{attempt}"
        units.append(
            {
                "unit_id": unit_id,
                "task_id": task_id,
                "attempt": attempt,
                "operation": operation,
                "mode": mode,
                "dependencies": dependencies,
                "input_refs": input_refs,
                "expected_outputs": output_refs,
                "source_receipt_ids": source_receipt_ids,
                "comparison_policy": _policy(output_refs),
                "failure_expectation": _failure_expectation(capsule, task_id, attempt),
            }
        )

    source_manifest = capsule / "identity/source_tree_manifest.json"
    if source_manifest.is_file():
        code_tree_hash = str(_read_json(source_manifest).get("code_tree_hash") or "0" * 64)
    else:
        code_tree_hash = "0" * 64
    runtime_path = capsule / "identity/runtime_manifest.json"
    runtime_ref = _artifact_ref(runtime_path, capsule, "runtime.manifest")
    value = {
        "schema_version": 1,
        "engine": evidence_plan["engine"],
        "run_id": evidence_plan["run_id"],
        "plan_hash": evidence_plan["plan_hash"],
        "evidence_plan_hash": evidence_plan["evidence_plan_hash"],
        "code_tree_hash": code_tree_hash,
        "runtime_ref": runtime_ref,
        "frozen_clock": evidence_plan["closure_cutoff"],
        "units": units,
        "replay_plan_hash": "0" * 64,
    }
    value["replay_plan_hash"] = replay_plan_hash(value)
    return validate_replay_plan(value)


__all__ = ["build_replay_plan"]

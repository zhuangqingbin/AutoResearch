"""Read-only verification of a frozen session task evidence denominator."""

from __future__ import annotations

import json
from pathlib import Path

from autoresearch.contracts.forensic import (
    validate_evidence_plan,
    validate_task_evidence,
)
from autoresearch.trace.atomic import sha256_file


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _task_root(capsule: Path, task_id: str, attempt: int) -> Path:
    return capsule / "evidence/tasks" / task_id / f"a{attempt}"


def _verify_ref(root: Path, ref: dict, missing: list[str]) -> None:
    path = root / ref["captured_path"]
    if not path.is_file():
        missing.append(f"EVIDENCE_REF_MISSING:{ref['captured_path']}")
    elif sha256_file(path) != ref["sha256"]:
        missing.append(f"EVIDENCE_REF_HASH_MISMATCH:{ref['captured_path']}")


def _verify_required_legs(key: dict, evidence: dict, missing: list[str]) -> None:
    """Derive presence from refs, never trust a stored PRESENT label."""
    requirements = set(key["requirements"])
    identity = f"{key['task_id']}:a{key['attempt']}"
    if "claim" in requirements and evidence["claim_ref"] is None:
        missing.append(f"CLAIM_MISSING:{identity}")
    if "input_snapshot" in requirements and not evidence["input_refs"]:
        missing.append(f"INPUT_SNAPSHOT_MISSING:{identity}")
    if "outputs" in requirements and not evidence["output_refs"]:
        missing.append(f"OUTPUTS_MISSING:{identity}")
    if "accepted_receipt" in requirements and evidence["receipt_ref"] is None:
        missing.append(f"ACCEPTED_RECEIPT_MISSING:{identity}")
    if "command_capture" in requirements and evidence["command_ref"] is None:
        missing.append(f"COMMAND_CAPTURE_MISSING:{identity}")
    if "transcript" in requirements and not any(
        ref["status"] == "PRESENT" for ref in evidence["transcript_refs"]
    ):
        missing.append(f"TRANSCRIPT_MISSING:{identity}")
    if "tool_results" in requirements and not evidence["source_receipt_ids"]:
        missing.append(f"TOOL_RESULTS_MISSING:{identity}")
    if "source_receipts" in requirements and not evidence["source_receipt_ids"]:
        missing.append(f"SOURCE_RECEIPTS_MISSING:{identity}")


def evaluate_task_evidence_closure(
    capsule: Path | str,
    evidence_plan: dict | None = None,
) -> dict:
    """Re-hash task evidence from frozen bytes without changing the capsule."""
    root = Path(capsule)
    plan = validate_evidence_plan(
        evidence_plan
        if evidence_plan is not None
        else _read_json(root / "evidence/evidence_plan.json")
    )
    missing: list[str] = []
    present = 0
    required = 0
    for key in plan["task_keys"]:
        if key["state"] == "NOT_REACHED":
            continue
        required += 1
        path = _task_root(root, key["task_id"], key["attempt"]) / "evidence.json"
        try:
            evidence = validate_task_evidence(_read_json(path))
        except Exception as exc:
            missing.append(
                f"TASK_EVIDENCE_MISSING:{key['task_id']}:a{key['attempt']}:"
                f"{type(exc).__name__}"
            )
            continue
        if (
            evidence["engine"] != plan["engine"]
            or evidence["run_id"] != plan["run_id"]
            or evidence["task_id"] != key["task_id"]
            or evidence["attempt"] != key["attempt"]
            or evidence["owner"] != key["owner"]
            or evidence["subject"] != key["subject"]
        ):
            missing.append(
                f"TASK_EVIDENCE_IDENTITY_MISMATCH:{key['task_id']}:a{key['attempt']}"
            )
            continue
        before = len(missing)
        _verify_required_legs(key, evidence, missing)
        for ref in [*evidence["input_refs"], *evidence["output_refs"]]:
            _verify_ref(root, ref, missing)
        for ref in (evidence["claim_ref"], evidence["receipt_ref"]):
            if ref is not None:
                _verify_ref(root, ref, missing)
        for ref in evidence["transcript_refs"]:
            if ref["captured_path"] is not None and ref["sha256"] is not None:
                _verify_ref(root, ref, missing)
        if evidence["command_ref"] is not None:
            command_root = path.parent / "command"
            for channel in ("stdout", "stderr"):
                log = command_root / f"{channel}.log.gz"
                expected = evidence["command_ref"][f"{channel}_sha256"]
                if not log.is_file():
                    missing.append(f"COMMAND_LOG_MISSING:{log.relative_to(root)}")
                elif expected is None or sha256_file(log) != expected:
                    missing.append(
                        f"COMMAND_LOG_HASH_MISMATCH:{log.relative_to(root)}"
                    )
        if evidence["status"] not in {"PRESENT", "NOT_APPLICABLE"}:
            missing.extend(
                evidence["reasons"] or [f"TASK_EVIDENCE_{evidence['status']}"]
            )
        if len(missing) == before:
            present += 1
    return {
        "schema_version": 1,
        "engine": plan["engine"],
        "run_id": plan["run_id"],
        "evidence_plan_hash": plan["evidence_plan_hash"],
        "required_tasks": required,
        "present_tasks": present,
        "completeness_ok": not missing,
        "missing": sorted(set(missing)),
    }


__all__ = ["evaluate_task_evidence_closure"]

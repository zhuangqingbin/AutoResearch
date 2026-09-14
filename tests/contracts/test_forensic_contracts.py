from __future__ import annotations

from copy import deepcopy

import pytest

from autoresearch.contracts.forensic import (
    evidence_plan_hash,
    validate_evidence_plan,
    validate_execution_origin,
    validate_task_evidence,
    validate_verification_result,
)

H = "a" * 64
RUN_ID = "20260914T120000000000Z"


@pytest.fixture
def valid_origin():
    return {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "run_kind": "stock-research",
        "orchestration": "session_v1",
        "entrypoint": "autoresearch.session_agent.begin",
        "plan_hash": H,
        "host_profile_hash": "b" * 64,
        "legacy_reason": None,
        "created_at": "2026-09-14T12:00:00+00:00",
    }


def _evidence_plan():
    value = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "plan_hash": H,
        "expansion_hashes": ["b" * 64],
        "task_keys": [{
            "task_id": "stock.harvest",
            "attempt": 1,
            "owner": "SESSION",
            "subject": "600519.SS",
            "state": "SUCCEEDED",
            "superseded_by": None,
            "requirements": ["claim", "input_snapshot", "outputs", "accepted_receipt"],
        }],
        "closure_cutoff": "2026-09-14T12:01:00Z",
        "scope": ["all"],
        "evidence_plan_hash": "0" * 64,
    }
    value["evidence_plan_hash"] = evidence_plan_hash(value)
    return value


def _artifact_ref(artifact_id="stock.context"):
    return {"artifact_id": artifact_id, "sha256": H, "captured_path": "inputs/context.md"}


def _task_evidence():
    return {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "task_id": "stock.harvest",
        "attempt": 1,
        "owner": "SESSION",
        "subject": "600519.SS",
        "input_refs": [_artifact_ref()],
        "output_refs": [_artifact_ref("stock.slim")],
        "claim_ref": _artifact_ref("stock.claim"),
        "receipt_ref": _artifact_ref("stock.receipt"),
        "command_ref": {
            "argv": ["python", "-m", "autoresearch.analyze.harvest"],
            "cwd": ".",
            "exit_code": 0,
            "signal": None,
            "stdout_sha256": "b" * 64,
            "stderr_sha256": "c" * 64,
            "operation_version": "stock.harvest.v1",
        },
        "transcript_refs": [],
        "source_receipt_ids": ["d" * 64],
        "status": "PRESENT",
        "reasons": [],
    }


def _verification_result():
    return {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "report_path": "runs/20260914T120000000000Z/p1/report.md",
        "report_sha256": H,
        "publication_id": "p1",
        "orchestration": "session_v1",
        "orchestration_verified": True,
        "report_covered": True,
        "integrity_ok": True,
        "publication_ok": True,
        "completeness_ok": False,
        "compute_status": "PARTIAL",
        "model_status": "EVIDENCE_ONLY",
        "scope": ["all"],
        "missing": ["MAIN_TRANSCRIPT"],
        "diffs": [],
    }


def test_valid_forensic_objects_are_returned_without_mutation(valid_origin):
    for validator, value in (
        (validate_execution_origin, valid_origin),
        (validate_evidence_plan, _evidence_plan()),
        (validate_task_evidence, _task_evidence()),
        (validate_verification_result, _verification_result()),
    ):
        before = deepcopy(value)
        assert validator(value) is value
        assert value == before


def test_legacy_cannot_claim_session_plan(valid_origin):
    value = dict(valid_origin, orchestration="legacy", legacy_reason=None)
    with pytest.raises(ValueError, match="legacy_reason"):
        validate_execution_origin(value)


def test_session_origin_requires_both_frozen_hashes(valid_origin):
    with pytest.raises(ValueError, match="plan_hash"):
        validate_execution_origin(dict(valid_origin, plan_hash=None))


def test_untracked_origin_cannot_borrow_a_run_id(valid_origin):
    value = dict(
        valid_origin,
        orchestration="untracked",
        run_id=RUN_ID,
        plan_hash=None,
        host_profile_hash=None,
    )
    with pytest.raises(ValueError, match="run_id"):
        validate_execution_origin(value)


def test_evidence_plan_hash_excludes_only_itself():
    value = _evidence_plan()
    value["scope"] = ["assemble"]
    with pytest.raises(ValueError, match="evidence_plan_hash"):
        validate_evidence_plan(value)


def test_duplicate_task_attempt_is_rejected():
    value = _evidence_plan()
    value["task_keys"].append(deepcopy(value["task_keys"][0]))
    value["evidence_plan_hash"] = evidence_plan_hash(value)
    with pytest.raises(ValueError, match="duplicate task attempt"):
        validate_evidence_plan(value)


def test_task_evidence_rejects_duplicate_source_receipts():
    value = _task_evidence()
    value["source_receipt_ids"].append(value["source_receipt_ids"][0])
    with pytest.raises(ValueError, match="duplicate source_receipt_ids"):
        validate_task_evidence(value)


def test_captured_paths_are_relative_and_cannot_escape():
    value = _task_evidence()
    value["input_refs"][0]["captured_path"] = "../outside"
    with pytest.raises(ValueError, match="captured_path"):
        validate_task_evidence(value)


def test_exact_fields_reject_self_reported_success(valid_origin):
    value = dict(valid_origin, orchestration_verified=True)
    with pytest.raises(ValueError, match="fields mismatch"):
        validate_execution_origin(value)


def test_unbound_verification_cannot_claim_publication_success():
    value = _verification_result()
    value.update({
        "run_id": None,
        "publication_id": None,
        "orchestration": "UNKNOWN",
        "orchestration_verified": False,
        "report_covered": False,
        "publication_ok": True,
        "missing": ["UNBOUND_REPORT"],
    })
    with pytest.raises(ValueError, match="publication_ok"):
        validate_verification_result(value)

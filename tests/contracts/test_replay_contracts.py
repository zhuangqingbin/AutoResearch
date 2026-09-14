from __future__ import annotations

from copy import deepcopy

import pytest

from autoresearch.contracts.replay import (
    replay_plan_hash,
    validate_replay_plan,
    validate_replay_result,
    validate_replay_unit,
)

H = "a" * 64
RUN_ID = "20260914T120000000000Z"


def _ref(artifact_id):
    return {"artifact_id": artifact_id, "sha256": H, "captured_path": f"artifacts/{artifact_id}"}


def _unit(unit_id="u1", dependencies=None):
    return {
        "unit_id": unit_id,
        "task_id": "stock.harvest",
        "attempt": 1,
        "operation": "stock.harvest",
        "mode": "SOURCE_REPLAY",
        "dependencies": dependencies or [],
        "input_refs": [_ref("stock.input")],
        "expected_outputs": [_ref("stock.output")],
        "source_receipt_ids": ["b" * 64],
        "comparison_policy": {
            "policy": "EXACT_BYTES",
            "version": 1,
            "ignored_fields": [],
        },
        "failure_expectation": None,
    }


def _plan():
    value = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "plan_hash": H,
        "evidence_plan_hash": "b" * 64,
        "code_tree_hash": "c" * 64,
        "runtime_ref": _ref("runtime.bundle"),
        "frozen_clock": "2026-09-14T12:00:00Z",
        "units": [_unit()],
        "replay_plan_hash": "0" * 64,
    }
    value["replay_plan_hash"] = replay_plan_hash(value)
    return value


def _result():
    return {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "run_mode": "LITE",
        "replay_plan_hash": H,
        "requested_scope": ["all"],
        "required_units": 1,
        "executed_units": 1,
        "scene_status": "COMPLETE",
        "compute_status": "FULL",
        "model_status": "EVIDENCE_ONLY",
        "identity_status": "LOCAL_ENV_MATCHED",
        "isolation_status": "ENFORCED",
        "unit_results": [{
            "unit_id": "u1",
            "status": "MATCH",
            "matched": True,
            "exit_code": 0,
            "output_diffs": [],
            "reason": None,
        }],
        "effects": [],
        "missing": [],
        "diffs": [],
    }


def test_valid_replay_objects_are_returned_without_mutation():
    for validator, value in (
        (validate_replay_unit, _unit()),
        (validate_replay_plan, _plan()),
        (validate_replay_result, _result()),
    ):
        before = deepcopy(value)
        assert validator(value) is value
        assert value == before


def test_empty_replay_denominator_is_not_full():
    value = dict(_result(), required_units=0, executed_units=0, unit_results=[])
    with pytest.raises(ValueError, match="required_units"):
        validate_replay_result(value)


def test_full_replay_requires_every_unit_to_execute():
    value = dict(_result(), executed_units=0)
    with pytest.raises(ValueError, match="executed_units"):
        validate_replay_result(value)


def test_evidence_only_units_do_not_enter_compute_denominator():
    value = _unit()
    value.update({"mode": "EVIDENCE_ONLY", "operation": None, "source_receipt_ids": []})
    assert validate_replay_unit(value) is value


def test_duplicate_unit_id_and_dependency_cycles_are_rejected():
    value = _plan()
    value["units"] = [_unit("u1", ["u2"]), _unit("u2", ["u1"])]
    value["replay_plan_hash"] = replay_plan_hash(value)
    with pytest.raises(ValueError, match="dependency cycle"):
        validate_replay_plan(value)


def test_replay_plan_hash_covers_frozen_clock():
    value = _plan()
    value["frozen_clock"] = "2026-09-14T12:01:00Z"
    with pytest.raises(ValueError, match="replay_plan_hash"):
        validate_replay_plan(value)


def test_mismatch_cannot_claim_matched_true():
    value = _result()
    value.update({"compute_status": "PARTIAL", "diffs": ["price"]})
    value["unit_results"][0].update({"status": "MISMATCH", "matched": True})
    with pytest.raises(ValueError, match="matched"):
        validate_replay_result(value)


def test_full_replay_requires_identity_and_isolation():
    value = dict(_result(), identity_status="UNAVAILABLE")
    with pytest.raises(ValueError, match="identity_status"):
        validate_replay_result(value)

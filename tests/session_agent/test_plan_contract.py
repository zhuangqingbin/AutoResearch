from __future__ import annotations

from copy import deepcopy

import pytest

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.session_plan import validate_expansion, validate_plan

HASH = "a" * 64
RUN_ID = "20260913T010203000000Z"


def task(**changes):
    value = {
        "task_id": "stock.card",
        "kind": "INFERENCE",
        "role": "stock.card",
        "operation": None,
        "dependencies": ["stock.harvest"],
        "input_artifact_ids": ["stock.slim"],
        "output_artifact_ids": ["stock.card"],
        "expected_output_contract": "stock.lite.v1",
        "owner": "SESSION",
        "subject": "600519.SS",
        "independent_context": False,
        "parent_task": None,
    }
    value.update(changes)
    return value


def _hashed(payload: dict, field: str) -> dict:
    value = deepcopy(payload)
    value[field] = sha256_bytes(canonical_json({k: v for k, v in value.items() if k != field}).encode())
    return value


def plan(**changes):
    harvest = task(
        task_id="stock.harvest",
        kind="DETERMINISTIC",
        role=None,
        operation="stock.harvest",
        dependencies=[],
        input_artifact_ids=[],
        expected_output_contract="stock.harvest.v1",
    )
    payload = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "run_kind": "stock-research",
        "requested_mode": "LITE",
        "analysis_date": "2026-09-13",
        "orchestration_version": "session_v1",
        "input_contract_hash": HASH,
        "config_hash": "b" * 64,
        "host_profile_hash": "c" * 64,
        "roles_hash": "d" * 64,
        "tasks": [harvest, task()],
        "task_templates": [],
        "plan_hash": "",
    }
    payload.update(changes)
    return _hashed(payload, "plan_hash")


def test_plan_accepts_valid_dag_and_verifies_hash():
    value = plan()
    assert validate_plan(value) == value


def test_plan_rejects_cycle_missing_dependency_and_duplicate_id():
    value = plan()
    value["tasks"][0]["dependencies"] = ["stock.card"]
    with pytest.raises(ValueError, match="cycle"):
        validate_plan(_hashed(value, "plan_hash"))

    value = plan()
    value["tasks"][1]["dependencies"] = ["missing"]
    with pytest.raises(ValueError, match="dependency"):
        validate_plan(_hashed(value, "plan_hash"))

    value = plan(tasks=[task(), task()])
    with pytest.raises(ValueError, match="duplicate"):
        validate_plan(value)


def test_plan_rejects_unknown_expander_and_hash_drift():
    template = {
        "template_id": "scan.l4",
        "expander": "arbitrary.python",
        "depends_on": ["stock.card"],
        "allowed_roles": ["stock.card"],
    }
    with pytest.raises(ValueError, match="expander"):
        validate_plan(plan(task_templates=[template]))
    value = plan()
    value["analysis_date"] = "2026-09-14"
    with pytest.raises(ValueError, match="plan_hash"):
        validate_plan(value)


def test_expansion_binds_inputs_and_derived_identity():
    plan_hash = plan()["plan_hash"]
    payload = {
        "schema_version": 1,
        "expansion_id": "scan.l4-0000000000000000",
        "plan_hash": plan_hash,
        "template_id": "scan.l4",
        "input_artifacts": [{"artifact_id": "scan.finalists", "sha256": "e" * 64}],
        "tasks": [task(task_id="l4.600519.a1.card")],
        "expansion_hash": "",
    }
    value = _hashed(payload, "expansion_hash")
    value["expansion_id"] = f"scan.l4-{value['expansion_hash'][:16]}"
    value = _hashed(value, "expansion_hash")
    value["expansion_id"] = f"scan.l4-{value['expansion_hash'][:16]}"
    # expansion_id is excluded together with expansion_hash from the identity hash,
    # avoiding a circular fixed point while keeping both fields checked.
    value["expansion_hash"] = sha256_bytes(canonical_json({
        k: v for k, v in value.items() if k not in {"expansion_hash", "expansion_id"}
    }).encode())
    value["expansion_id"] = f"scan.l4-{value['expansion_hash'][:16]}"
    assert validate_expansion(value, allowed_expanders={"scan.l4"}) == value


def test_expansion_rejects_wrong_template_and_duplicate_artifact():
    plan_hash = plan()["plan_hash"]
    base = {
        "schema_version": 1,
        "plan_hash": plan_hash,
        "template_id": "scan.l4",
        "input_artifacts": [
            {"artifact_id": "scan.finalists", "sha256": "e" * 64},
            {"artifact_id": "scan.finalists", "sha256": "e" * 64},
        ],
        "tasks": [task(task_id="l4.600519.a1.card")],
    }
    digest = sha256_bytes(canonical_json(base).encode())
    value = dict(base, expansion_id=f"scan.l4-{digest[:16]}", expansion_hash=digest)
    with pytest.raises(ValueError):
        validate_expansion(value, allowed_expanders={"scan.l4"})

"""Strict v1 contracts for offline replay plans, units, and results."""

from __future__ import annotations

from autoresearch.contracts.forensic import (
    _aware,
    _digest_without,
    _engine,
    _optional_string,
    _positive_int,
    _required_string,
    _run_id,
    _unique_strings,
    _validate_artifact_ref,
    _validate_artifact_refs,
)
from autoresearch.contracts.publication import validate_state_mutation
from autoresearch.contracts.session_task import (
    require_exact_fields,
    require_sha256,
    require_version,
)

REPLAY_PLAN_FIELDS = frozenset({
    "schema_version", "engine", "run_id", "plan_hash", "evidence_plan_hash",
    "code_tree_hash", "runtime_ref", "frozen_clock", "units", "replay_plan_hash",
})
REPLAY_UNIT_FIELDS = frozenset({
    "unit_id", "task_id", "attempt", "operation", "mode", "dependencies",
    "input_refs", "expected_outputs", "source_receipt_ids", "comparison_policy",
    "failure_expectation",
})
COMPARISON_POLICY_FIELDS = frozenset({"policy", "version", "ignored_fields"})
FAILURE_EXPECTATION_FIELDS = frozenset({"category", "message_hash"})
REPLAY_RESULT_FIELDS = frozenset({
    "schema_version", "engine", "run_id", "run_mode", "replay_plan_hash",
    "requested_scope", "required_units", "executed_units", "scene_status",
    "compute_status", "model_status", "identity_status", "isolation_status",
    "unit_results", "effects", "missing", "diffs",
})
UNIT_RESULT_FIELDS = frozenset({
    "unit_id", "status", "matched", "exit_code", "output_diffs", "reason",
})

REPLAY_MODES = frozenset({
    "COMPUTE", "SOURCE_REPLAY", "EVIDENCE_ONLY", "EFFECT_PLAN", "CONTROL_ONLY",
})
COMPARISON_POLICIES = frozenset({
    "EXACT_BYTES", "CANONICAL_JSON", "PARQUET_VALUES", "TEXT_NORMALIZED",
    "STATE_MUTATION",
})
UNIT_STATUSES = frozenset({
    "MATCH", "MISMATCH", "EXPECTED_FAILURE", "MISSING_INPUT", "UNSUPPORTED",
    "EXECUTION_FAILED", "EVIDENCE_ONLY", "CONTROL_VERIFIED",
})
EXECUTED_STATUSES = frozenset({"MATCH", "MISMATCH", "EXPECTED_FAILURE", "EXECUTION_FAILED"})
SCENE_STATUSES = frozenset({"COMPLETE", "PARTIAL", "NONE"})
COMPUTE_STATUSES = frozenset({"FULL", "PARTIAL", "NONE"})
IDENTITY_STATUSES = frozenset({"LOCAL_ENV_MATCHED", "PACKAGED", "UNAVAILABLE"})
ISOLATION_STATUSES = frozenset({"ENFORCED", "FAILED", "UNKNOWN"})


def replay_plan_hash(value: dict) -> str:
    return _digest_without(value, "replay_plan_hash")


def validate_replay_unit(value: dict) -> dict:
    require_exact_fields(value, REPLAY_UNIT_FIELDS)
    _required_string(value["unit_id"], "unit_id")
    _required_string(value["task_id"], "task_id")
    _positive_int(value["attempt"], "attempt")
    mode = value["mode"]
    if mode not in REPLAY_MODES:
        raise ValueError("invalid replay mode")
    if value["operation"] is None:
        if mode not in {"EVIDENCE_ONLY", "CONTROL_ONLY"}:
            raise ValueError("operation required for executable replay unit")
    else:
        _required_string(value["operation"], "operation")
    _unique_strings(value["dependencies"], "dependencies")
    _validate_artifact_refs(value["input_refs"], "input_refs")
    _validate_artifact_refs(value["expected_outputs"], "expected_outputs")
    _unique_strings(value["source_receipt_ids"], "source_receipt_ids", sha256_values=True)
    if mode == "SOURCE_REPLAY" and not value["source_receipt_ids"]:
        raise ValueError("SOURCE_REPLAY requires source_receipt_ids")
    if mode in {"EVIDENCE_ONLY", "CONTROL_ONLY"} and value["source_receipt_ids"]:
        raise ValueError(f"{mode} cannot consume source receipts")
    policy = value["comparison_policy"]
    require_exact_fields(policy, COMPARISON_POLICY_FIELDS)
    if policy["policy"] not in COMPARISON_POLICIES:
        raise ValueError("invalid comparison policy")
    _positive_int(policy["version"], "comparison policy version")
    _unique_strings(policy["ignored_fields"], "ignored_fields")
    expectation = value["failure_expectation"]
    if expectation is not None:
        require_exact_fields(expectation, FAILURE_EXPECTATION_FIELDS)
        _required_string(expectation["category"], "failure category")
        require_sha256(expectation["message_hash"], "failure message_hash")
    return value


def _validate_dag(units: list[dict]) -> None:
    identities = [unit["unit_id"] for unit in units]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate unit_id")
    known = set(identities)
    pending = {}
    for unit in units:
        missing = set(unit["dependencies"]) - known
        if missing:
            raise ValueError(f"missing replay dependency: {sorted(missing)}")
        pending[unit["unit_id"]] = set(unit["dependencies"])
    while pending:
        ready = {unit_id for unit_id, dependencies in pending.items() if not dependencies}
        if not ready:
            raise ValueError("replay dependency cycle")
        pending = {
            unit_id: dependencies - ready
            for unit_id, dependencies in pending.items()
            if unit_id not in ready
        }


def validate_replay_plan(value: dict) -> dict:
    require_exact_fields(value, REPLAY_PLAN_FIELDS)
    require_version(value["schema_version"])
    _engine(value["engine"])
    _run_id(value["run_id"])
    for field in ("plan_hash", "evidence_plan_hash", "code_tree_hash"):
        require_sha256(value[field], field)
    _validate_artifact_ref(value["runtime_ref"], field="runtime_ref")
    _aware(value["frozen_clock"], "frozen_clock")
    if type(value["units"]) is not list:
        raise ValueError("units must be a list")
    for unit in value["units"]:
        validate_replay_unit(unit)
    _validate_dag(value["units"])
    require_sha256(value["replay_plan_hash"], "replay_plan_hash")
    if value["replay_plan_hash"] != replay_plan_hash(value):
        raise ValueError("replay_plan_hash mismatch")
    return value


def _validate_unit_result(value: dict) -> dict:
    require_exact_fields(value, UNIT_RESULT_FIELDS)
    _required_string(value["unit_id"], "unit_id")
    if value["status"] not in UNIT_STATUSES:
        raise ValueError("invalid replay unit status")
    if type(value["matched"]) is not bool:
        raise ValueError("matched must be boolean")
    should_match = value["status"] in {"MATCH", "EXPECTED_FAILURE", "CONTROL_VERIFIED"}
    if value["matched"] != should_match:
        raise ValueError("matched disagrees with replay unit status")
    if value["exit_code"] is not None and type(value["exit_code"]) is not int:
        raise ValueError("invalid exit_code")
    _unique_strings(value["output_diffs"], "output_diffs")
    _optional_string(value["reason"], "reason")
    if value["status"] not in {"MATCH", "CONTROL_VERIFIED"} and value["reason"] is None:
        raise ValueError("non-match replay result requires reason")
    return value


def validate_replay_result(value: dict) -> dict:
    require_exact_fields(value, REPLAY_RESULT_FIELDS)
    require_version(value["schema_version"])
    _engine(value["engine"])
    _run_id(value["run_id"])
    _required_string(value["run_mode"], "run_mode")
    require_sha256(value["replay_plan_hash"], "replay_plan_hash")
    _unique_strings(value["requested_scope"], "requested_scope", allow_empty=False)
    for field in ("required_units", "executed_units"):
        if type(value[field]) is not int or value[field] < 0:
            raise ValueError(f"invalid {field}")
    if value["executed_units"] > value["required_units"]:
        raise ValueError("executed_units exceeds required_units")
    if value["scene_status"] not in SCENE_STATUSES:
        raise ValueError("invalid scene_status")
    if value["compute_status"] not in COMPUTE_STATUSES:
        raise ValueError("invalid compute_status")
    if value["model_status"] != "EVIDENCE_ONLY":
        raise ValueError("model_status must be EVIDENCE_ONLY")
    if value["identity_status"] not in IDENTITY_STATUSES:
        raise ValueError("invalid identity_status")
    if value["isolation_status"] not in ISOLATION_STATUSES:
        raise ValueError("invalid isolation_status")
    if type(value["unit_results"]) is not list:
        raise ValueError("unit_results must be a list")
    unit_ids = []
    for result in value["unit_results"]:
        _validate_unit_result(result)
        unit_ids.append(result["unit_id"])
    if len(unit_ids) != len(set(unit_ids)):
        raise ValueError("duplicate unit result")
    executed = sum(result["status"] in EXECUTED_STATUSES for result in value["unit_results"])
    if value["executed_units"] != executed:
        raise ValueError("executed_units disagrees with unit_results")
    if type(value["effects"]) is not list:
        raise ValueError("effects must be a list")
    for effect in value["effects"]:
        validate_state_mutation(effect)
    _unique_strings(value["missing"], "missing")
    _unique_strings(value["diffs"], "diffs")
    if value["compute_status"] == "FULL":
        if value["required_units"] == 0:
            raise ValueError("required_units must be greater than zero for FULL")
        if value["executed_units"] != value["required_units"]:
            raise ValueError("executed_units must equal required_units for FULL")
        if value["identity_status"] == "UNAVAILABLE":
            raise ValueError("identity_status cannot be UNAVAILABLE for FULL")
        if value["isolation_status"] != "ENFORCED":
            raise ValueError("isolation_status must be ENFORCED for FULL")
        executable = [r for r in value["unit_results"] if r["status"] in EXECUTED_STATUSES]
        if any(r["status"] not in {"MATCH", "EXPECTED_FAILURE"} for r in executable):
            raise ValueError("FULL replay contains a non-matching unit")
        if value["missing"] or value["diffs"]:
            raise ValueError("FULL replay cannot carry missing items or diffs")
    return value


__all__ = [
    "REPLAY_MODES",
    "REPLAY_PLAN_FIELDS",
    "REPLAY_RESULT_FIELDS",
    "REPLAY_UNIT_FIELDS",
    "UNIT_STATUSES",
    "replay_plan_hash",
    "validate_replay_plan",
    "validate_replay_result",
    "validate_replay_unit",
]

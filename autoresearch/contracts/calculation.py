"""Strict contract for evidence emitted by registered research calculators."""

from __future__ import annotations

from autoresearch.contracts.forensic import _digest_without, _positive_int, _required_string
from autoresearch.contracts.session_task import (
    require_exact_fields,
    require_sha256,
    require_version,
)

CALCULATOR_IDS = frozenset(
    {
        "financial_period_ratios.v1",
        "ah_premium.v1",
        "conditional_base_rates.v1",
        "dcf_sensitivity.v1",
    }
)
CALCULATION_FIELDS = frozenset(
    {
        "schema_version",
        "calculation_id",
        "calculator_id",
        "calculator_version",
        "code_hash",
        "task_id",
        "attempt",
        "input_refs",
        "parameters",
        "values",
        "assumptions",
        "status",
        "error",
    }
)
INPUT_REF_FIELDS = frozenset({"artifact_id", "sha256"})
ERROR_FIELDS = frozenset({"category", "message"})


def calculation_id(value: dict) -> str:
    return _digest_without(value, "calculation_id")


def _validate_input_refs(value: object) -> None:
    if type(value) is not list or not value:
        raise ValueError("calculation input_refs must be a non-empty list")
    artifact_ids: set[str] = set()
    for ref in value:
        require_exact_fields(ref, INPUT_REF_FIELDS)
        artifact_id = _required_string(ref["artifact_id"], "input artifact_id")
        require_sha256(ref["sha256"], "input sha256")
        if artifact_id in artifact_ids:
            raise ValueError("duplicate calculation input artifact_id")
        artifact_ids.add(artifact_id)


def validate_calculation(value: dict) -> dict:
    require_exact_fields(value, CALCULATION_FIELDS)
    require_version(value["schema_version"])
    require_sha256(value["calculation_id"], "calculation_id")
    require_sha256(value["code_hash"], "code_hash")
    if value["calculator_id"] not in CALCULATOR_IDS:
        raise ValueError("unsupported calculation contract")
    _required_string(value["calculator_version"], "calculator_version")
    _required_string(value["task_id"], "task_id")
    _positive_int(value["attempt"], "attempt")
    _validate_input_refs(value["input_refs"])
    for field in ("parameters", "values", "assumptions"):
        if type(value[field]) is not dict:
            raise ValueError(f"calculation {field} must be an object")
    if value["status"] not in {"SUCCEEDED", "FAILED"}:
        raise ValueError("invalid calculation status")
    if value["status"] == "SUCCEEDED":
        if value["error"] is not None:
            raise ValueError("successful calculation cannot contain an error")
    else:
        require_exact_fields(value["error"], ERROR_FIELDS)
        _required_string(value["error"]["category"], "calculation error category")
        _required_string(value["error"]["message"], "calculation error message")
    if value["calculation_id"] != calculation_id(value):
        raise ValueError("calculation identity mismatch")
    return value


__all__ = ["CALCULATOR_IDS", "calculation_id", "validate_calculation"]

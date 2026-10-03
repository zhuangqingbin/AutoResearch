"""Pure session comparison schema and verdict construction; no host acceptance IO."""
from __future__ import annotations

import re

from autoresearch.contracts.session_task import require_exact_fields

COMPARISON_FIELDS = frozenset(
    {
        "schema_version",
        "engine",
        "workflow",
        "mode",
        "baseline_run_id",
        "candidate_run_id",
        "input_identity_equal",
        "config_identity_equal",
        "deterministic_diffs",
        "research_diffs",
        "missing_evidence",
        "verdict",
    }
)


_RUN_ID = re.compile(r"[0-9]{8}T[0-9]{12}Z")


_VERDICTS = frozenset({"PASS", "FAIL", "INCOMPLETE"})


def _run_id(value: object, field: str, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if type(value) is not str or _RUN_ID.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")


def validate_comparison(value: dict) -> dict:
    require_exact_fields(value, COMPARISON_FIELDS)
    if value["schema_version"] != 1:
        raise ValueError("unsupported comparison schema")
    if value["engine"] not in {"codex", "claude"}:
        raise ValueError("invalid comparison engine")
    for field in ("workflow", "mode"):
        if type(value[field]) is not str or not value[field]:
            raise ValueError(f"{field} required")
    _run_id(value["baseline_run_id"], "baseline_run_id", optional=True)
    _run_id(value["candidate_run_id"], "candidate_run_id")
    for field in ("input_identity_equal", "config_identity_equal"):
        if type(value[field]) is not bool:
            raise ValueError(f"{field} must be boolean")
    for field in ("deterministic_diffs", "research_diffs", "missing_evidence"):
        if type(value[field]) is not list:
            raise ValueError(f"{field} must be a list")
    if value["verdict"] not in _VERDICTS:
        raise ValueError("invalid comparison verdict")
    return value


def build_comparison(
    *,
    engine: str,
    workflow: str,
    mode: str,
    baseline_run_id: str | None,
    candidate_run_id: str,
    input_identity_equal: bool,
    config_identity_equal: bool,
    deterministic_diffs: list,
    research_diffs: list,
    missing_evidence: list,
) -> dict:
    if missing_evidence or baseline_run_id is None:
        verdict = "INCOMPLETE"
    elif not input_identity_equal or not config_identity_equal or deterministic_diffs:
        verdict = "FAIL"
    else:
        verdict = "PASS"
    value = {
        "schema_version": 1,
        "engine": engine,
        "workflow": workflow,
        "mode": mode,
        "baseline_run_id": baseline_run_id,
        "candidate_run_id": candidate_run_id,
        "input_identity_equal": input_identity_equal,
        "config_identity_equal": config_identity_equal,
        "deterministic_diffs": deterministic_diffs,
        "research_diffs": research_diffs,
        "missing_evidence": missing_evidence,
        "verdict": verdict,
    }
    return validate_comparison(value)

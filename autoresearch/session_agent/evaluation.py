"""Fail-closed comparison and host acceptance records for session_v1."""
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
_RUNTIME_METADATA = frozenset(
    {
        "run_id",
        "generated_at",
        "created_at",
        "updated_at",
        "runtime",
        "report_dir",
        "capsule_path",
        "workspace",
    }
)
_RUN_ID = re.compile(r"[0-9]{8}T[0-9]{12}Z")
_VERDICTS = frozenset({"PASS", "FAIL", "INCOMPLETE"})
_EVIDENCE_KINDS = frozenset({"REAL_SESSION", "SYNTHETIC", "NONE"})


def _without_runtime_metadata(value):
    if isinstance(value, dict):
        return {
            key: _without_runtime_metadata(item)
            for key, item in value.items()
            if key not in _RUNTIME_METADATA
        }
    if isinstance(value, list):
        return [_without_runtime_metadata(item) for item in value]
    return value


def _diff(left, right, path: str, out: list[dict]) -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else key
            if key not in left:
                out.append({"path": child, "baseline": None, "candidate": right[key]})
            elif key not in right:
                out.append({"path": child, "baseline": left[key], "candidate": None})
            else:
                _diff(left[key], right[key], child, out)
        return
    if isinstance(left, list) and isinstance(right, list):
        length = max(len(left), len(right))
        for index in range(length):
            child = f"{path}[{index}]"
            if index >= len(left):
                out.append({"path": child, "baseline": None, "candidate": right[index]})
            elif index >= len(right):
                out.append({"path": child, "baseline": left[index], "candidate": None})
            else:
                _diff(left[index], right[index], child, out)
        return
    if left != right:
        out.append({"path": path or "$", "baseline": left, "candidate": right})


def compare_manifests(baseline: object, candidate: object) -> list[dict]:
    """Compare products after removing only declared runtime/path metadata."""
    diffs: list[dict] = []
    _diff(
        _without_runtime_metadata(baseline),
        _without_runtime_metadata(candidate),
        "",
        diffs,
    )
    return diffs


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


def _validate_acceptance_case(value: dict) -> dict:
    fields = frozenset(
        {"host", "scenario", "status", "evidence_kind", "run_id", "notes"}
    )
    require_exact_fields(value, fields)
    if value["host"] not in {"codex", "claude"}:
        raise ValueError("invalid acceptance host")
    if type(value["scenario"]) is not str or not value["scenario"]:
        raise ValueError("acceptance scenario required")
    if value["status"] not in _VERDICTS:
        raise ValueError("invalid acceptance status")
    if value["evidence_kind"] not in _EVIDENCE_KINDS:
        raise ValueError("invalid acceptance evidence kind")
    _run_id(value["run_id"], "acceptance run_id", optional=True)
    if type(value["notes"]) is not str:
        raise ValueError("acceptance notes must be a string")
    if value["status"] == "PASS" and (
        value["evidence_kind"] != "REAL_SESSION" or value["run_id"] is None
    ):
        raise ValueError("PASS requires REAL_SESSION evidence and run_id")
    return value


def _rollup(statuses: list[str]) -> str:
    if "FAIL" in statuses:
        return "FAIL"
    if "INCOMPLETE" in statuses or not statuses:
        return "INCOMPLETE"
    return "PASS"


def build_acceptance_matrix(cases: list[dict]) -> dict:
    if not isinstance(cases, list) or not cases:
        raise ValueError("acceptance cases required")
    checked = [_validate_acceptance_case(dict(item)) for item in cases]
    keys = [(item["host"], item["scenario"]) for item in checked]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate host/scenario acceptance case")
    hosts = {
        host: _rollup([item["status"] for item in checked if item["host"] == host])
        for host in sorted({item["host"] for item in checked})
    }
    return {
        "schema_version": 1,
        "overall": _rollup(list(hosts.values())),
        "hosts": hosts,
        "cases": sorted(checked, key=lambda item: (item["host"], item["scenario"])),
    }


__all__ = [
    "COMPARISON_FIELDS",
    "build_acceptance_matrix",
    "build_comparison",
    "compare_manifests",
    "validate_comparison",
]

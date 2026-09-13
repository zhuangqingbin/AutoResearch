"""Pure v1 contracts for subscription-session task handoffs."""
from __future__ import annotations

import re
from datetime import date

from autoresearch.contracts.inference_task import validate_envelope

SCHEMA_VERSION = 1
TASK_FIELDS = frozenset({
    "task_id", "kind", "role", "operation", "dependencies", "input_artifact_ids",
    "output_artifact_ids", "expected_output_contract", "owner", "subject",
    "independent_context", "parent_task",
})
SUBMISSION_FIELDS = frozenset({
    "schema_version", "envelope", "plan_hash", "outputs", "host_receipt_id",
})
TOOL_RESULT_FIELDS = frozenset({
    "schema_version", "command", "run_id", "state", "tasks", "result", "errors",
})
HOST_PROFILE_FIELDS = frozenset({
    "schema_version", "engine", "session_ref", "deterministic_exec", "capture_binding",
    "inference_handoff", "safe_resume", "independent_context", "native_dispatch",
    "web_search", "web_fetch", "observed_model", "observed_effort", "evidence_refs",
})
BEGIN_REQUEST_FIELDS = frozenset({
    "schema_version", "kind", "requested_mode", "analysis_date", "subject", "peers",
    "asset_type", "name", "force_full", "host_profile", "predecessor_run_id",
})

TASK_KINDS = ("DETERMINISTIC", "INFERENCE")
TASK_OWNERS = ("SESSION", "L4_TASKBOOK")
TOOL_STATES = ("READY", "WAITING", "BLOCKED", "DONE")
RUN_MODES = {
    "scan-market": frozenset({"AUTO"}),
    "stock-research": frozenset({"FULL", "LITE"}),
    "macro-research": frozenset({"FULL", "LITE"}),
    "sector-research": frozenset({"FULL", "LITE"}),
    "dossier-init": frozenset({"INIT"}),
}

_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,127}", re.ASCII)
_ARTIFACT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", re.ASCII)
_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_RUN_ID_RE = re.compile(r"[0-9]{8}T[0-9]{12}Z", re.ASCII)
_CODE_RE = re.compile(r"[0-9]{6}", re.ASCII)


def require_exact_fields(value: dict, fields: frozenset[str]) -> None:
    if not isinstance(value, dict):
        raise ValueError("object required")
    missing = fields - value.keys()
    extra = value.keys() - fields
    if missing or extra:
        raise ValueError(f"fields mismatch: missing={sorted(missing)}, extra={sorted(extra)}")


def require_version(value: object) -> None:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise ValueError("unsupported schema version")


def require_sha256(value: object, field: str = "hash") -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _required_string(value: object, field: str, *, pattern: re.Pattern | None = None) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field} required")
    if pattern is not None and not pattern.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _optional_string(value: object, field: str) -> None:
    if value is not None and (type(value) is not str or not value):
        raise ValueError(f"invalid {field}")


def _unique_strings(value: object, field: str, *, pattern: re.Pattern | None = None) -> None:
    if type(value) is not list:
        raise ValueError(f"{field} must be a list")
    if any(type(item) is not str or not item for item in value):
        raise ValueError(f"invalid {field}")
    if pattern is not None and any(not pattern.fullmatch(item) for item in value):
        raise ValueError(f"invalid {field}")
    if len(value) != len(set(value)):
        raise ValueError(f"duplicate {field}")


def _validate_parent(value: object) -> None:
    if value is None:
        return
    require_exact_fields(value, frozenset({"owner", "subject", "attempt"}))
    if value["owner"] != "L4_TASKBOOK":
        raise ValueError("parent owner must be L4_TASKBOOK")
    _required_string(value["subject"], "parent subject", pattern=_CODE_RE)
    if type(value["attempt"]) is not int or value["attempt"] < 1:
        raise ValueError("invalid parent attempt")


def validate_task(value: dict) -> dict:
    require_exact_fields(value, TASK_FIELDS)
    _required_string(value["task_id"], "task_id", pattern=_ID_RE)
    if value["kind"] not in TASK_KINDS:
        raise ValueError("invalid task kind")
    if value["owner"] not in TASK_OWNERS:
        raise ValueError("invalid task owner")
    _unique_strings(value["dependencies"], "dependencies", pattern=_ID_RE)
    _unique_strings(value["input_artifact_ids"], "input_artifact_ids", pattern=_ARTIFACT_RE)
    _unique_strings(value["output_artifact_ids"], "output_artifact_ids", pattern=_ARTIFACT_RE)
    if not value["output_artifact_ids"]:
        raise ValueError("output_artifact_ids required")
    _required_string(
        value["expected_output_contract"], "expected_output_contract", pattern=_ARTIFACT_RE
    )
    if type(value["independent_context"]) is not bool:
        raise ValueError("independent_context must be boolean")
    _optional_string(value["subject"], "subject")
    _validate_parent(value["parent_task"])

    if value["kind"] == "INFERENCE":
        _required_string(value["role"], "role", pattern=_ID_RE)
        if value["operation"] is not None:
            raise ValueError("inference task cannot have operation")
        if not value["input_artifact_ids"]:
            raise ValueError("inference task inputs required")
    else:
        _required_string(value["operation"], "operation", pattern=_ID_RE)
        if value["role"] is not None:
            raise ValueError("deterministic task cannot have role")
        if value["independent_context"]:
            raise ValueError("deterministic task cannot require independent context")

    if value["owner"] == "L4_TASKBOOK":
        _required_string(value["subject"], "L4 subject", pattern=_CODE_RE)
    return value


def validate_submission(value: dict) -> dict:
    require_exact_fields(value, SUBMISSION_FIELDS)
    require_version(value["schema_version"])
    validate_envelope(value["envelope"])
    require_sha256(value["plan_hash"], "plan_hash")
    if type(value["outputs"]) is not list or not value["outputs"]:
        raise ValueError("outputs required")
    seen: set[str] = set()
    for output in value["outputs"]:
        require_exact_fields(output, frozenset({"artifact_id", "sha256"}))
        artifact_id = _required_string(output["artifact_id"], "artifact_id", pattern=_ARTIFACT_RE)
        require_sha256(output["sha256"], "output sha256")
        if artifact_id in seen:
            raise ValueError("duplicate output artifact")
        seen.add(artifact_id)
    _optional_string(value["host_receipt_id"], "host_receipt_id")
    return value


def validate_host_profile(value: dict) -> dict:
    require_exact_fields(value, HOST_PROFILE_FIELDS)
    require_version(value["schema_version"])
    if value["engine"] not in {"claude", "codex"}:
        raise ValueError("invalid host engine")
    _required_string(value["session_ref"], "session_ref")
    for field in (
        "deterministic_exec", "capture_binding", "inference_handoff", "safe_resume",
        "independent_context", "native_dispatch", "web_search", "web_fetch",
    ):
        if value[field] not in {True, False, None} or (
            value[field] is not None and type(value[field]) is not bool
        ):
            raise ValueError(f"invalid host capability {field}")
    _optional_string(value["observed_model"], "observed_model")
    _optional_string(value["observed_effort"], "observed_effort")
    _unique_strings(value["evidence_refs"], "evidence_refs")
    return value


def validate_begin_request(value: dict, *, expected_engine: str | None = None) -> dict:
    require_exact_fields(value, BEGIN_REQUEST_FIELDS)
    require_version(value["schema_version"])
    kind = value["kind"]
    if kind not in RUN_MODES:
        raise ValueError("invalid run kind")
    if value["requested_mode"] not in RUN_MODES[kind]:
        raise ValueError("invalid kind/mode combination")
    try:
        date.fromisoformat(_required_string(value["analysis_date"], "analysis_date"))
    except ValueError as exc:
        raise ValueError("invalid analysis_date") from exc
    validate_host_profile(value["host_profile"])
    if expected_engine is not None and value["host_profile"]["engine"] != expected_engine:
        raise ValueError("host engine does not match process engine")
    _unique_strings(value["peers"], "peers")
    _optional_string(value["name"], "name")
    if type(value["force_full"]) is not bool:
        raise ValueError("force_full must be boolean")
    if value["force_full"] and kind != "scan-market":
        raise ValueError("force_full only applies to scan-market")
    _optional_string(value["predecessor_run_id"], "predecessor_run_id")
    if value["predecessor_run_id"] is not None:
        _required_string(value["predecessor_run_id"], "predecessor_run_id", pattern=_RUN_ID_RE)

    subject = value["subject"]
    asset_type = value["asset_type"]
    if kind == "stock-research":
        _required_string(subject, "stock subject")
        if asset_type not in {"stock", "crypto"}:
            raise ValueError("invalid stock asset_type")
    elif kind == "sector-research":
        _required_string(subject, "sector subject")
        if asset_type is not None:
            raise ValueError("sector asset_type must be null")
    elif kind == "dossier-init":
        _required_string(subject, "dossier subject", pattern=_CODE_RE)
        if asset_type is not None:
            raise ValueError("dossier asset_type must be null")
    elif subject is not None or asset_type is not None:
        raise ValueError("subject and asset_type must be null for this kind")
    if kind != "stock-research" and value["peers"]:
        raise ValueError("peers only apply to stock-research")
    return value


def validate_tool_result(value: dict) -> dict:
    require_exact_fields(value, TOOL_RESULT_FIELDS)
    require_version(value["schema_version"])
    _required_string(value["command"], "command", pattern=_ID_RE)
    _required_string(value["run_id"], "run_id", pattern=_RUN_ID_RE)
    if value["state"] not in TOOL_STATES:
        raise ValueError("invalid tool state")
    if type(value["tasks"]) is not list or any(not isinstance(item, dict) for item in value["tasks"]):
        raise ValueError("tasks must be objects")
    if type(value["errors"]) is not list or any(not isinstance(item, dict) for item in value["errors"]):
        raise ValueError("errors must be objects")
    return value


__all__ = [
    "BEGIN_REQUEST_FIELDS", "HOST_PROFILE_FIELDS", "RUN_MODES", "SCHEMA_VERSION",
    "SUBMISSION_FIELDS", "TASK_FIELDS", "TASK_KINDS", "TASK_OWNERS", "TOOL_RESULT_FIELDS",
    "TOOL_STATES", "require_exact_fields", "require_sha256", "require_version",
    "validate_begin_request", "validate_host_profile", "validate_submission", "validate_task",
    "validate_tool_result",
]

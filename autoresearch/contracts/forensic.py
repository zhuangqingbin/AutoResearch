"""Strict v1 contracts for orchestration identity and forensic evidence sidecars."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import PurePosixPath

from autoresearch.contracts.session_task import (
    require_exact_fields,
    require_sha256,
    require_version,
)
from autoresearch.contracts.stages import RUN_KINDS

EXECUTION_ORIGIN_FIELDS = frozenset({
    "schema_version", "engine", "run_id", "run_kind", "orchestration",
    "entrypoint", "plan_hash", "host_profile_hash", "legacy_reason", "created_at",
})
EVIDENCE_PLAN_FIELDS = frozenset({
    "schema_version", "engine", "run_id", "plan_hash", "expansion_hashes",
    "task_keys", "closure_cutoff", "scope", "evidence_plan_hash",
})
TASK_KEY_FIELDS = frozenset({
    "task_id", "attempt", "owner", "subject", "state", "superseded_by",
    "requirements",
})
TASK_EVIDENCE_FIELDS = frozenset({
    "schema_version", "engine", "run_id", "task_id", "attempt", "owner", "subject",
    "input_refs", "output_refs", "claim_ref", "receipt_ref", "command_ref",
    "transcript_refs", "source_receipt_ids", "status", "reasons",
})
ARTIFACT_REF_FIELDS = frozenset({"artifact_id", "sha256", "captured_path"})
COMMAND_REF_FIELDS = frozenset({
    "argv", "cwd", "exit_code", "signal", "stdout_sha256", "stderr_sha256",
    "operation_version",
})
TRANSCRIPT_REF_FIELDS = frozenset({
    "engine", "status", "role", "subject", "invocation_id", "session_ref",
    "start_ordinal", "end_ordinal", "captured_path", "sha256", "context_source",
})
VERIFICATION_RESULT_FIELDS = frozenset({
    "schema_version", "engine", "run_id", "report_path", "report_sha256",
    "publication_id", "orchestration", "orchestration_verified", "report_covered",
    "integrity_ok", "publication_ok", "completeness_ok", "compute_status",
    "model_status", "scope", "missing", "diffs",
})
OPERATION_EVIDENCE_FIELDS = frozenset({
    "schema_version", "operation_id", "engine", "operation", "input_refs",
    "code_hash", "parameters", "output_refs", "effects", "status", "error",
})

ENGINES = frozenset({"claude", "codex"})
ORCHESTRATIONS = frozenset({"session_v1", "legacy", "untracked"})
TASK_OWNERS = frozenset({"SESSION", "L4_TASKBOOK"})
TASK_STATES = frozenset({
    "PENDING", "READY", "RUNNING", "SUCCEEDED", "FAILED", "BLOCKED",
    "SUPERSEDED", "CANCELLED", "NOT_REACHED",
})
EVIDENCE_REQUIREMENTS = frozenset({
    "claim", "input_snapshot", "outputs", "accepted_receipt", "command_capture",
    "transcript", "tool_results", "source_receipts",
})
EVIDENCE_STATUSES = frozenset({
    "PRESENT", "PARTIAL", "MISSING", "NOT_REACHED", "NOT_APPLICABLE",
})
TRANSCRIPT_STATUSES = frozenset({"PRESENT", "PARTIAL", "MISSING", "UNAVAILABLE", "AMBIGUOUS"})
CONTEXT_SOURCES = frozenset({"MAIN", "SUBAGENT", "SHARED", "UNKNOWN"})

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}", re.ASCII)
_RUN_ID_RE = re.compile(r"[0-9]{8}T[0-9]{12}Z", re.ASCII)


def _canonical(value: object) -> bytes:
    """Match ``common.atomic.canonical_json`` without an upward layer import."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest_without(value: dict, field: str) -> str:
    return hashlib.sha256(_canonical({k: v for k, v in value.items() if k != field})).hexdigest()


def _required_string(value: object, field: str, *, pattern: re.Pattern | None = None) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field} required")
    if pattern is not None and not pattern.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _optional_string(value: object, field: str) -> None:
    if value is not None and (type(value) is not str or not value):
        raise ValueError(f"invalid {field}")


def _engine(value: object) -> str:
    if value not in ENGINES:
        raise ValueError("invalid engine")
    return str(value)


def _run_id(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    return _required_string(value, "run_id", pattern=_RUN_ID_RE)


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"invalid {field}")
    return value


def _aware(value: object, field: str, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if type(value) is not str or not value:
        raise ValueError(f"{field} must be an aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} timezone required")
    return parsed


def _safe_relative(value: object, field: str, *, allow_dot: bool = False) -> str:
    text = _required_string(value, field)
    if "\\" in text:
        raise ValueError(f"invalid {field}: backslash is not portable")
    raw = PurePosixPath(text)
    if raw.is_absolute() or ".." in raw.parts or (not allow_dot and str(raw) == "."):
        raise ValueError(f"invalid {field}: relative path required")
    return str(raw)


def _unique_strings(
    value: object,
    field: str,
    *,
    allow_empty: bool = True,
    sha256_values: bool = False,
) -> list[str]:
    if type(value) is not list or (not allow_empty and not value):
        raise ValueError(f"{field} must be a{' non-empty' if not allow_empty else ''} list")
    if any(type(item) is not str or not item for item in value):
        raise ValueError(f"invalid {field}")
    if len(value) != len(set(value)):
        raise ValueError(f"duplicate {field}")
    if sha256_values:
        for item in value:
            require_sha256(item, field)
    return value


def _validate_artifact_ref(value: dict, *, field: str = "artifact ref") -> dict:
    require_exact_fields(value, ARTIFACT_REF_FIELDS)
    _required_string(value["artifact_id"], "artifact_id", pattern=_ID_RE)
    require_sha256(value["sha256"], "artifact sha256")
    _safe_relative(value["captured_path"], "captured_path")
    return value


def _validate_artifact_refs(value: object, field: str) -> list[dict]:
    if type(value) is not list:
        raise ValueError(f"{field} must be a list")
    identities: list[tuple[str, str]] = []
    for item in value:
        _validate_artifact_ref(item, field=field)
        identities.append((item["artifact_id"], item["captured_path"]))
    if len(identities) != len(set(identities)):
        raise ValueError(f"duplicate {field}")
    return value


def evidence_plan_hash(value: dict) -> str:
    return _digest_without(value, "evidence_plan_hash")


def validate_execution_origin(value: dict) -> dict:
    require_exact_fields(value, EXECUTION_ORIGIN_FIELDS)
    require_version(value["schema_version"])
    _engine(value["engine"])
    if value["run_kind"] not in RUN_KINDS:
        raise ValueError("invalid run_kind")
    orchestration = value["orchestration"]
    if orchestration not in ORCHESTRATIONS:
        raise ValueError("invalid orchestration")
    _required_string(value["entrypoint"], "entrypoint", pattern=_ID_RE)
    _aware(value["created_at"], "created_at")
    if orchestration == "session_v1":
        _run_id(value["run_id"])
        require_sha256(value["plan_hash"], "plan_hash")
        require_sha256(value["host_profile_hash"], "host_profile_hash")
        if value["legacy_reason"] is not None:
            raise ValueError("legacy_reason must be null for session_v1")
    elif orchestration == "legacy":
        _run_id(value["run_id"])
        _required_string(value["legacy_reason"], "legacy_reason")
        for field in ("plan_hash", "host_profile_hash"):
            if value[field] is not None:
                require_sha256(value[field], field)
    else:
        if value["run_id"] is not None:
            raise ValueError("untracked origin run_id must be null")
        if value["plan_hash"] is not None or value["host_profile_hash"] is not None:
            raise ValueError("untracked origin cannot claim plan hashes")
        if value["legacy_reason"] is not None:
            raise ValueError("untracked origin legacy_reason must be null")
    return value


def _validate_task_key(value: dict) -> dict:
    require_exact_fields(value, TASK_KEY_FIELDS)
    _required_string(value["task_id"], "task_id", pattern=_ID_RE)
    _positive_int(value["attempt"], "attempt")
    if value["owner"] not in TASK_OWNERS:
        raise ValueError("invalid task owner")
    _optional_string(value["subject"], "subject")
    if value["state"] not in TASK_STATES:
        raise ValueError("invalid task state")
    superseded = value["superseded_by"]
    if superseded is not None:
        require_exact_fields(superseded, frozenset({"task_id", "attempt"}))
        _required_string(superseded["task_id"], "superseded task_id", pattern=_ID_RE)
        _positive_int(superseded["attempt"], "superseded attempt")
    if value["state"] == "SUPERSEDED" and superseded is None:
        raise ValueError("superseded_by required")
    requirements = _unique_strings(value["requirements"], "requirements")
    unknown = set(requirements) - EVIDENCE_REQUIREMENTS
    if unknown:
        raise ValueError(f"unknown requirements: {sorted(unknown)}")
    return value


def validate_evidence_plan(value: dict) -> dict:
    require_exact_fields(value, EVIDENCE_PLAN_FIELDS)
    require_version(value["schema_version"])
    _engine(value["engine"])
    _run_id(value["run_id"])
    require_sha256(value["plan_hash"], "plan_hash")
    _unique_strings(value["expansion_hashes"], "expansion_hashes", sha256_values=True)
    if type(value["task_keys"]) is not list or not value["task_keys"]:
        raise ValueError("task_keys must be a non-empty list")
    identities = []
    for task in value["task_keys"]:
        _validate_task_key(task)
        identities.append((task["task_id"], task["attempt"]))
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate task attempt")
    _aware(value["closure_cutoff"], "closure_cutoff")
    _unique_strings(value["scope"], "scope", allow_empty=False)
    require_sha256(value["evidence_plan_hash"], "evidence_plan_hash")
    if value["evidence_plan_hash"] != evidence_plan_hash(value):
        raise ValueError("evidence_plan_hash mismatch")
    return value


def _validate_command_ref(value: dict | None) -> None:
    if value is None:
        return
    require_exact_fields(value, COMMAND_REF_FIELDS)
    _unique_strings(value["argv"], "argv", allow_empty=False)
    _safe_relative(value["cwd"], "cwd", allow_dot=True)
    if value["exit_code"] is not None and type(value["exit_code"]) is not int:
        raise ValueError("invalid exit_code")
    if value["signal"] is not None and type(value["signal"]) is not int:
        raise ValueError("invalid signal")
    for field in ("stdout_sha256", "stderr_sha256"):
        if value[field] is not None:
            require_sha256(value[field], field)
    _required_string(value["operation_version"], "operation_version", pattern=_ID_RE)


def _validate_transcript_ref(value: dict) -> dict:
    require_exact_fields(value, TRANSCRIPT_REF_FIELDS)
    _engine(value["engine"])
    if value["status"] not in TRANSCRIPT_STATUSES:
        raise ValueError("invalid transcript status")
    _required_string(value["role"], "transcript role", pattern=_ID_RE)
    for field in ("subject", "invocation_id", "session_ref"):
        _optional_string(value[field], field)
    start, end = value["start_ordinal"], value["end_ordinal"]
    for field, ordinal in (("start_ordinal", start), ("end_ordinal", end)):
        if ordinal is not None and (type(ordinal) is not int or ordinal < 0):
            raise ValueError(f"invalid {field}")
    if start is not None and end is not None and end < start:
        raise ValueError("end_ordinal precedes start_ordinal")
    if value["captured_path"] is not None:
        _safe_relative(value["captured_path"], "captured_path")
    if value["sha256"] is not None:
        require_sha256(value["sha256"], "transcript sha256")
    if value["context_source"] not in CONTEXT_SOURCES:
        raise ValueError("invalid context_source")
    if value["status"] == "PRESENT" and (
        value["captured_path"] is None or value["sha256"] is None
    ):
        raise ValueError("present transcript requires captured_path and sha256")
    return value


def validate_task_evidence(value: dict) -> dict:
    require_exact_fields(value, TASK_EVIDENCE_FIELDS)
    require_version(value["schema_version"])
    _engine(value["engine"])
    _run_id(value["run_id"])
    _required_string(value["task_id"], "task_id", pattern=_ID_RE)
    _positive_int(value["attempt"], "attempt")
    if value["owner"] not in TASK_OWNERS:
        raise ValueError("invalid task owner")
    _optional_string(value["subject"], "subject")
    _validate_artifact_refs(value["input_refs"], "input_refs")
    _validate_artifact_refs(value["output_refs"], "output_refs")
    for field in ("claim_ref", "receipt_ref"):
        if value[field] is not None:
            _validate_artifact_ref(value[field], field=field)
    _validate_command_ref(value["command_ref"])
    if type(value["transcript_refs"]) is not list:
        raise ValueError("transcript_refs must be a list")
    transcript_ids = []
    for ref in value["transcript_refs"]:
        _validate_transcript_ref(ref)
        transcript_ids.append((ref["sha256"], ref["start_ordinal"], ref["end_ordinal"]))
    if len(transcript_ids) != len(set(transcript_ids)):
        raise ValueError("duplicate transcript_refs")
    _unique_strings(
        value["source_receipt_ids"], "source_receipt_ids", sha256_values=True
    )
    if value["status"] not in EVIDENCE_STATUSES:
        raise ValueError("invalid evidence status")
    reasons = _unique_strings(value["reasons"], "reasons")
    if value["status"] in {"PARTIAL", "MISSING"} and not reasons:
        raise ValueError("partial or missing evidence requires reasons")
    return value


def validate_verification_result(value: dict) -> dict:
    require_exact_fields(value, VERIFICATION_RESULT_FIELDS)
    require_version(value["schema_version"])
    _engine(value["engine"])
    _run_id(value["run_id"], optional=True)
    _safe_relative(value["report_path"], "report_path")
    require_sha256(value["report_sha256"], "report_sha256")
    _optional_string(value["publication_id"], "publication_id")
    if value["orchestration"] not in ORCHESTRATIONS | {"UNKNOWN"}:
        raise ValueError("invalid orchestration")
    for field in (
        "orchestration_verified", "report_covered", "integrity_ok", "publication_ok",
        "completeness_ok",
    ):
        if type(value[field]) is not bool:
            raise ValueError(f"{field} must be boolean")
    if value["compute_status"] not in {"FULL", "PARTIAL", "NONE", "UNKNOWN"}:
        raise ValueError("invalid compute_status")
    if value["model_status"] not in {"EVIDENCE_ONLY", "UNKNOWN"}:
        raise ValueError("invalid model_status")
    _unique_strings(value["scope"], "scope", allow_empty=False)
    _unique_strings(value["missing"], "missing")
    _unique_strings(value["diffs"], "diffs")
    if not value["report_covered"]:
        if value["publication_ok"]:
            raise ValueError("publication_ok cannot be true for an unbound report")
        if "UNBOUND_REPORT" not in value["missing"]:
            raise ValueError("unbound report must include UNBOUND_REPORT")
    if value["report_covered"] and (
        value["run_id"] is None or value["publication_id"] is None
    ):
        raise ValueError("covered report requires run and publication identity")
    return value


def validate_operation_evidence(value: dict) -> dict:
    require_exact_fields(value, OPERATION_EVIDENCE_FIELDS)
    require_version(value["schema_version"])
    require_sha256(value["operation_id"], "operation_id")
    _engine(value["engine"])
    _required_string(value["operation"], "operation", pattern=_ID_RE)
    _validate_artifact_refs(value["input_refs"], "input_refs")
    require_sha256(value["code_hash"], "code_hash")
    if type(value["parameters"]) is not dict:
        raise ValueError("parameters must be an object")
    _canonical(value["parameters"])
    _validate_artifact_refs(value["output_refs"], "output_refs")
    if type(value["effects"]) is not list:
        raise ValueError("effects must be a list")
    if value["status"] not in {"SUCCEEDED", "FAILED", "UNMEASURED"}:
        raise ValueError("invalid operation evidence status")
    if value["error"] is not None and type(value["error"]) is not dict:
        raise ValueError("error must be an object or null")
    return value


__all__ = [
    "ARTIFACT_REF_FIELDS",
    "EVIDENCE_PLAN_FIELDS",
    "EXECUTION_ORIGIN_FIELDS",
    "OPERATION_EVIDENCE_FIELDS",
    "TASK_EVIDENCE_FIELDS",
    "VERIFICATION_RESULT_FIELDS",
    "evidence_plan_hash",
    "validate_evidence_plan",
    "validate_execution_origin",
    "validate_operation_evidence",
    "validate_task_evidence",
    "validate_verification_result",
]

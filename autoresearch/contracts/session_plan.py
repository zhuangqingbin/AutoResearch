"""Pure validation and identity rules for frozen session-agent plans."""
from __future__ import annotations

import hashlib
import json
import re

from autoresearch.contracts.session_task import (
    RUN_MODES,
    require_exact_fields,
    require_sha256,
    require_version,
    validate_task,
)

PLAN_FIELDS = frozenset({
    "schema_version", "engine", "run_id", "run_kind", "requested_mode", "analysis_date",
    "orchestration_version", "input_contract_hash", "config_hash", "host_profile_hash",
    "roles_hash", "tasks", "task_templates", "plan_hash",
})
TEMPLATE_FIELDS = frozenset({"template_id", "expander", "depends_on", "allowed_roles"})
EXPANSION_FIELDS = frozenset({
    "schema_version", "expansion_id", "plan_hash", "template_id", "input_artifacts",
    "tasks", "expansion_hash",
})
REGISTERED_EXPANDERS = frozenset({
    "scan.sectors", "scan.l3", "scan.l4", "scan.reviews", "stock.optional_lenses",
    "macro.sections", "sector.sections",
})

_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,127}", re.ASCII)
_RUN_ID_RE = re.compile(r"[0-9]{8}T[0-9]{12}Z", re.ASCII)
_EXPANSION_ID_RE = re.compile(r"([a-z0-9][a-z0-9_.-]{0,127})-([0-9a-f]{16})", re.ASCII)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest_without(value: dict, *fields: str) -> str:
    return hashlib.sha256(_canonical({k: v for k, v in value.items() if k not in fields})).hexdigest()


def _string(value: object, field: str, pattern: re.Pattern = _ID_RE) -> str:
    if type(value) is not str or not pattern.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _unique_strings(value: object, field: str) -> list[str]:
    if type(value) is not list or any(type(item) is not str or not _ID_RE.fullmatch(item) for item in value):
        raise ValueError(f"invalid {field}")
    if len(value) != len(set(value)):
        raise ValueError(f"duplicate {field}")
    return value


def _validate_dag(tasks: list[dict], *, allow_external_dependencies: bool = False) -> None:
    ids = [task["task_id"] for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task_id")
    known = set(ids)
    for task in tasks:
        missing = set(task["dependencies"]) - known
        if missing and not allow_external_dependencies:
            raise ValueError(f"missing dependency: {sorted(missing)}")
        if task["task_id"] in task["dependencies"]:
            raise ValueError("task dependency cycle")

    pending = {
        task["task_id"]: set(task["dependencies"]) & known for task in tasks
    }
    while pending:
        ready = {task_id for task_id, deps in pending.items() if not deps}
        if not ready:
            raise ValueError("task dependency cycle")
        pending = {
            task_id: deps - ready for task_id, deps in pending.items() if task_id not in ready
        }


def validate_template(value: dict) -> dict:
    require_exact_fields(value, TEMPLATE_FIELDS)
    _string(value["template_id"], "template_id")
    if value["expander"] not in REGISTERED_EXPANDERS:
        raise ValueError("unregistered expander")
    _unique_strings(value["depends_on"], "template depends_on")
    _unique_strings(value["allowed_roles"], "allowed_roles")
    return value


def validate_plan(value: dict) -> dict:
    require_exact_fields(value, PLAN_FIELDS)
    require_version(value["schema_version"])
    if value["engine"] not in {"claude", "codex"}:
        raise ValueError("invalid plan engine")
    _string(value["run_id"], "run_id", _RUN_ID_RE)
    kind = value["run_kind"]
    if kind not in RUN_MODES or value["requested_mode"] not in RUN_MODES[kind]:
        raise ValueError("invalid plan kind/mode")
    if value["orchestration_version"] != "session_v1":
        raise ValueError("invalid orchestration_version")
    for field in (
        "input_contract_hash", "config_hash", "host_profile_hash", "roles_hash", "plan_hash"
    ):
        require_sha256(value[field], field)
    if type(value["tasks"]) is not list:
        raise ValueError("tasks must be a list")
    for task in value["tasks"]:
        validate_task(task)
    _validate_dag(value["tasks"])
    if type(value["task_templates"]) is not list:
        raise ValueError("task_templates must be a list")
    for template in value["task_templates"]:
        validate_template(template)
    template_ids = [template["template_id"] for template in value["task_templates"]]
    if len(template_ids) != len(set(template_ids)):
        raise ValueError("duplicate template_id")
    task_ids = {task["task_id"] for task in value["tasks"]}
    if task_ids & set(template_ids):
        raise ValueError("task and template identities overlap")
    for template in value["task_templates"]:
        missing = set(template["depends_on"]) - task_ids
        if missing:
            raise ValueError(f"missing template dependency: {sorted(missing)}")
    expected = _digest_without(value, "plan_hash")
    if value["plan_hash"] != expected:
        raise ValueError("plan_hash mismatch")
    return value


def validate_expansion(
    value: dict, *, allowed_expanders: set[str] | frozenset[str] | None = None
) -> dict:
    require_exact_fields(value, EXPANSION_FIELDS)
    require_version(value["schema_version"])
    require_sha256(value["plan_hash"], "plan_hash")
    template_id = _string(value["template_id"], "template_id")
    if allowed_expanders is not None and template_id not in allowed_expanders:
        raise ValueError("template is not allowed by plan")
    if type(value["input_artifacts"]) is not list or not value["input_artifacts"]:
        raise ValueError("input_artifacts required")
    artifact_ids: list[str] = []
    for artifact in value["input_artifacts"]:
        require_exact_fields(artifact, frozenset({"artifact_id", "sha256"}))
        artifact_ids.append(_string(artifact["artifact_id"], "artifact_id"))
        require_sha256(artifact["sha256"], "artifact sha256")
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ValueError("duplicate expansion input artifact")
    if type(value["tasks"]) is not list or not value["tasks"]:
        raise ValueError("expanded tasks required")
    for task in value["tasks"]:
        validate_task(task)
    _validate_dag(value["tasks"], allow_external_dependencies=True)
    require_sha256(value["expansion_hash"], "expansion_hash")
    expected = _digest_without(value, "expansion_id", "expansion_hash")
    if value["expansion_hash"] != expected:
        raise ValueError("expansion_hash mismatch")
    match = _EXPANSION_ID_RE.fullmatch(value["expansion_id"])
    if not match or match.group(1) != template_id or match.group(2) != expected[:16]:
        raise ValueError("expansion_id mismatch")
    return value


def plan_hash(value: dict) -> str:
    return _digest_without(value, "plan_hash")


def expansion_hash(value: dict) -> str:
    return _digest_without(value, "expansion_id", "expansion_hash")


__all__ = [
    "EXPANSION_FIELDS", "PLAN_FIELDS", "REGISTERED_EXPANDERS", "TEMPLATE_FIELDS",
    "expansion_hash", "plan_hash", "validate_expansion", "validate_plan", "validate_template",
]

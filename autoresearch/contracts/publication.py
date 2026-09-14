"""Strict v1 contracts for immutable publication bundles and receipts."""

from __future__ import annotations

import re

from autoresearch.contracts.forensic import (
    _aware,
    _digest_without,
    _engine,
    _required_string,
    _run_id,
    _safe_relative,
)
from autoresearch.contracts.session_task import (
    require_exact_fields,
    require_sha256,
    require_version,
)
from autoresearch.contracts.stages import RUN_KINDS

STATE_MUTATION_FIELDS = frozenset(
    {
        "target_key",
        "expected_before_hash",
        "after_artifact_id",
        "after_hash",
        "apply_policy",
    }
)
BUSINESS_FILE_FIELDS = frozenset(
    {
        "artifact_id",
        "relative_path",
        "sha256",
        "bytes",
        "media_type",
    }
)
PREDECESSOR_FIELDS = frozenset({"engine", "run_id", "publication_id", "root_hash"})
PUBLICATION_BUNDLE_FIELDS = frozenset(
    {
        "schema_version",
        "engine",
        "run_id",
        "run_kind",
        "publication_id",
        "predecessor",
        "origin_hash",
        "plan_hash",
        "evidence_plan_hash",
        "business_files",
        "state_mutations",
        "generated_at",
        "bundle_hash",
    }
)
STATE_EFFECT_FIELDS = frozenset({"target_key", "status", "before_hash", "after_hash"})
PUBLICATION_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "engine",
        "run_id",
        "publication_id",
        "bundle_hash",
        "capsule_root_hash",
        "canonical_path",
        "committed_at",
        "state_effects",
        "previous_receipt_hash",
        "receipt_hash",
    }
)

APPLY_POLICIES = frozenset({"CAS_REPLACE", "IDEMPOTENT_APPEND", "ADVANCE_IF_NEWER"})
EFFECT_STATUSES = frozenset(
    {
        "APPLIED",
        "ALREADY_APPLIED",
        "SUPERSEDED_BY_NEWER",
        "CONFLICT",
    }
)


def publication_bundle_hash(value: dict) -> str:
    return _digest_without(value, "bundle_hash")


def publication_receipt_hash(value: dict) -> str:
    return _digest_without(value, "receipt_hash")


def _optional_sha256(value: object, field: str) -> None:
    if value is not None:
        require_sha256(value, field)


def _publication_id(value: object) -> str:
    resolved = _required_string(value, "publication_id")
    if not re.fullmatch(r"p[1-9][0-9]*", resolved):
        raise ValueError("invalid publication_id")
    return resolved


def validate_state_mutation(value: dict) -> dict:
    require_exact_fields(value, STATE_MUTATION_FIELDS)
    _required_string(value["target_key"], "target_key")
    _optional_sha256(value["expected_before_hash"], "expected_before_hash")
    _required_string(value["after_artifact_id"], "after_artifact_id")
    require_sha256(value["after_hash"], "after_hash")
    if value["apply_policy"] not in APPLY_POLICIES:
        raise ValueError("invalid apply_policy")
    return value


def _validate_business_file(value: dict) -> str:
    require_exact_fields(value, BUSINESS_FILE_FIELDS)
    _required_string(value["artifact_id"], "artifact_id")
    normalized = _safe_relative(value["relative_path"], "relative_path")
    require_sha256(value["sha256"], "business file sha256")
    if type(value["bytes"]) is not int or value["bytes"] < 0:
        raise ValueError("invalid business file bytes")
    _required_string(value["media_type"], "media_type")
    return normalized


def _validate_predecessor(value: dict | None) -> None:
    if value is None:
        return
    require_exact_fields(value, PREDECESSOR_FIELDS)
    _engine(value["engine"])
    _run_id(value["run_id"])
    _publication_id(value["publication_id"])
    require_sha256(value["root_hash"], "predecessor root_hash")


def validate_publication_bundle(value: dict) -> dict:
    require_exact_fields(value, PUBLICATION_BUNDLE_FIELDS)
    require_version(value["schema_version"])
    _engine(value["engine"])
    _run_id(value["run_id"])
    if value["run_kind"] not in RUN_KINDS:
        raise ValueError("invalid run_kind")
    _publication_id(value["publication_id"])
    _validate_predecessor(value["predecessor"])
    for field in ("origin_hash", "plan_hash", "evidence_plan_hash"):
        require_sha256(value[field], field)
    if type(value["business_files"]) is not list or not value["business_files"]:
        raise ValueError("business_files must be a non-empty list")
    artifact_ids, paths = [], []
    for item in value["business_files"]:
        paths.append(_validate_business_file(item))
        artifact_ids.append(item["artifact_id"])
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ValueError("duplicate business artifact_id")
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate business file path")
    if type(value["state_mutations"]) is not list:
        raise ValueError("state_mutations must be a list")
    targets = []
    for mutation in value["state_mutations"]:
        validate_state_mutation(mutation)
        targets.append(mutation["target_key"])
    if len(targets) != len(set(targets)):
        raise ValueError("duplicate state target")
    _aware(value["generated_at"], "generated_at")
    require_sha256(value["bundle_hash"], "bundle_hash")
    if value["bundle_hash"] != publication_bundle_hash(value):
        raise ValueError("bundle_hash mismatch")
    return value


def _validate_state_effect(value: dict) -> dict:
    require_exact_fields(value, STATE_EFFECT_FIELDS)
    _required_string(value["target_key"], "target_key")
    if value["status"] not in EFFECT_STATUSES:
        raise ValueError("invalid state effect status")
    _optional_sha256(value["before_hash"], "before_hash")
    require_sha256(value["after_hash"], "after_hash")
    return value


def validate_publication_receipt(value: dict) -> dict:
    require_exact_fields(value, PUBLICATION_RECEIPT_FIELDS)
    require_version(value["schema_version"])
    _engine(value["engine"])
    _run_id(value["run_id"])
    _publication_id(value["publication_id"])
    for field in ("bundle_hash", "capsule_root_hash"):
        require_sha256(value[field], field)
    _safe_relative(value["canonical_path"], "canonical_path")
    if value["canonical_path"] != f"runs/{value['run_id']}/{value['publication_id']}":
        raise ValueError("canonical_path does not match publication identity")
    _aware(value["committed_at"], "committed_at")
    if type(value["state_effects"]) is not list:
        raise ValueError("state_effects must be a list")
    targets = []
    for effect in value["state_effects"]:
        _validate_state_effect(effect)
        if effect["status"] == "CONFLICT":
            raise ValueError("CONFLICT state effect cannot be committed")
        targets.append(effect["target_key"])
    if len(targets) != len(set(targets)):
        raise ValueError("duplicate state effect target")
    _optional_sha256(value["previous_receipt_hash"], "previous_receipt_hash")
    require_sha256(value["receipt_hash"], "receipt_hash")
    if value["receipt_hash"] != publication_receipt_hash(value):
        raise ValueError("receipt_hash mismatch")
    return value


__all__ = [
    "APPLY_POLICIES",
    "BUSINESS_FILE_FIELDS",
    "EFFECT_STATUSES",
    "PUBLICATION_BUNDLE_FIELDS",
    "PUBLICATION_RECEIPT_FIELDS",
    "STATE_MUTATION_FIELDS",
    "publication_bundle_hash",
    "publication_receipt_hash",
    "validate_publication_bundle",
    "validate_publication_receipt",
    "validate_state_mutation",
]

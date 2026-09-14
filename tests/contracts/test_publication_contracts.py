from __future__ import annotations

from copy import deepcopy

import pytest

from autoresearch.contracts.publication import (
    publication_bundle_hash,
    publication_receipt_hash,
    validate_publication_bundle,
    validate_publication_receipt,
    validate_state_mutation,
)

H = "a" * 64
RUN_ID = "20260914T120000000000Z"


def _mutation():
    return {
        "target_key": "macro.latest_state",
        "expected_before_hash": None,
        "after_artifact_id": "macro.state.candidate",
        "after_hash": H,
        "apply_policy": "ADVANCE_IF_NEWER",
    }


def _bundle():
    value = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "run_kind": "macro-research",
        "publication_id": "p1",
        "predecessor": None,
        "origin_hash": H,
        "plan_hash": "b" * 64,
        "evidence_plan_hash": "c" * 64,
        "business_files": [{
            "artifact_id": "macro.report",
            "relative_path": "report/macro.md",
            "sha256": "d" * 64,
            "bytes": 123,
            "media_type": "text/markdown",
        }],
        "state_mutations": [_mutation()],
        "generated_at": "2026-09-14T12:02:00Z",
        "bundle_hash": "0" * 64,
    }
    value["bundle_hash"] = publication_bundle_hash(value)
    return value


def _receipt():
    value = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "publication_id": "p1",
        "bundle_hash": H,
        "capsule_root_hash": "b" * 64,
        "canonical_path": "runs/20260914T120000000000Z/p1",
        "committed_at": "2026-09-14T12:03:00Z",
        "state_effects": [{
            "target_key": "macro.latest_state",
            "status": "APPLIED",
            "before_hash": None,
            "after_hash": "c" * 64,
        }],
        "previous_receipt_hash": None,
        "receipt_hash": "0" * 64,
    }
    value["receipt_hash"] = publication_receipt_hash(value)
    return value


def test_valid_publication_objects_are_returned_without_mutation():
    for validator, value in (
        (validate_state_mutation, _mutation()),
        (validate_publication_bundle, _bundle()),
        (validate_publication_receipt, _receipt()),
    ):
        before = deepcopy(value)
        assert validator(value) is value
        assert value == before


@pytest.mark.parametrize("path", ["/absolute/report.md", "../escape.md", "a/../../b"])
def test_business_file_path_must_be_safe_and_relative(path):
    value = _bundle()
    value["business_files"][0]["relative_path"] = path
    value["bundle_hash"] = publication_bundle_hash(value)
    with pytest.raises(ValueError, match="relative_path"):
        validate_publication_bundle(value)


def test_duplicate_normalized_business_path_is_rejected():
    value = _bundle()
    duplicate = dict(value["business_files"][0], artifact_id="macro.report.copy")
    duplicate["relative_path"] = "report/./macro.md"
    value["business_files"].append(duplicate)
    value["bundle_hash"] = publication_bundle_hash(value)
    with pytest.raises(ValueError, match="duplicate business file path"):
        validate_publication_bundle(value)


def test_bundle_hash_changes_when_business_content_identity_changes():
    value = _bundle()
    value["business_files"][0]["sha256"] = "e" * 64
    with pytest.raises(ValueError, match="bundle_hash"):
        validate_publication_bundle(value)


def test_duplicate_state_target_is_rejected():
    value = _bundle()
    value["state_mutations"].append(deepcopy(value["state_mutations"][0]))
    value["bundle_hash"] = publication_bundle_hash(value)
    with pytest.raises(ValueError, match="duplicate state target"):
        validate_publication_bundle(value)


def test_conflicted_effect_cannot_be_a_committed_receipt():
    value = _receipt()
    value["state_effects"][0]["status"] = "CONFLICT"
    value["receipt_hash"] = publication_receipt_hash(value)
    with pytest.raises(ValueError, match="CONFLICT"):
        validate_publication_receipt(value)


def test_receipt_hash_covers_the_previous_chain_link():
    value = _receipt()
    value["previous_receipt_hash"] = "f" * 64
    with pytest.raises(ValueError, match="receipt_hash"):
        validate_publication_receipt(value)

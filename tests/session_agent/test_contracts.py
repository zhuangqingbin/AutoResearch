from __future__ import annotations

from copy import deepcopy

import pytest

from autoresearch.contracts.session_task import (
    validate_begin_request,
    validate_host_profile,
    validate_submission,
    validate_task,
    validate_tool_result,
)

HASH = "a" * 64
RUN_ID = "20260913T010203000000Z"


def host_profile(**changes):
    value = {
        "schema_version": 1,
        "engine": "codex",
        "session_ref": "session-1",
        "deterministic_exec": True,
        "capture_binding": True,
        "inference_handoff": True,
        "safe_resume": None,
        "independent_context": False,
        "native_dispatch": False,
        "web_search": True,
        "web_fetch": None,
        "observed_model": None,
        "observed_effort": None,
        "evidence_refs": ["tool-inventory"],
    }
    value.update(changes)
    return value


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


def envelope(**changes):
    value = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "task_id": "stock.card",
        "role": "stock.card",
        "input_artifact_ids": ["stock.slim"],
        "input_contract_hash": HASH,
        "expected_output_contract": "stock.lite.v1",
        "attempt": 1,
    }
    value.update(changes)
    return value


def begin_request(**changes):
    value = {
        "schema_version": 1,
        "kind": "stock-research",
        "requested_mode": "LITE",
        "analysis_date": "2026-09-13",
        "subject": "600519.SS",
        "peers": [],
        "asset_type": "stock",
        "name": None,
        "force_full": False,
        "host_profile": host_profile(),
        "predecessor_run_id": None,
    }
    value.update(changes)
    return value


def test_task_accepts_inference_and_deterministic_shapes():
    assert validate_task(task()) == task()
    deterministic = task(
        task_id="stock.harvest",
        kind="DETERMINISTIC",
        role=None,
        operation="stock.harvest",
        dependencies=[],
        input_artifact_ids=[],
        expected_output_contract="stock.harvest.v1",
        independent_context=False,
    )
    assert validate_task(deterministic) == deterministic


@pytest.mark.parametrize(
    "changes",
    [
        {"task_id": "../card"},
        {"kind": "MODEL"},
        {"role": None},
        {"operation": "shell"},
        {"dependencies": ["a", "a"]},
        {"output_artifact_ids": ["a", "a"]},
        {"independent_context": 1},
        {"parent_task": {"owner": "SESSION", "subject": "600519", "attempt": 1}},
        {"parent_task": {"owner": "L4_TASKBOOK", "subject": "600519", "attempt": True}},
    ],
)
def test_task_rejects_invalid_identity_and_ownership(changes):
    with pytest.raises(ValueError):
        validate_task(task(**changes))


def test_l4_owner_and_parent_have_exact_shapes():
    external = task(owner="L4_TASKBOOK", subject="600519")
    assert validate_task(external) == external
    child = task(parent_task={"owner": "L4_TASKBOOK", "subject": "600519", "attempt": 2})
    assert validate_task(child) == child
    bad = deepcopy(child)
    bad["parent_task"]["run_id"] = RUN_ID
    with pytest.raises(ValueError):
        validate_task(bad)


def test_submission_reuses_exact_nine_field_envelope():
    value = {
        "schema_version": 1,
        "envelope": envelope(),
        "plan_hash": "b" * 64,
        "outputs": [{"artifact_id": "stock.card", "sha256": "c" * 64}],
        "host_receipt_id": None,
    }
    assert validate_submission(value) == value
    value["envelope"]["session_model"] = "fake"
    with pytest.raises(ValueError):
        validate_submission(value)


@pytest.mark.parametrize("version", [True, False, "1", 0, 2, None])
def test_all_contract_versions_require_exact_integer_one(version):
    with pytest.raises(ValueError):
        validate_host_profile(host_profile(schema_version=version))


def test_host_profile_preserves_unknown_capability_and_rejects_claimed_strings():
    assert validate_host_profile(host_profile())["safe_resume"] is None
    with pytest.raises(ValueError):
        validate_host_profile(host_profile(native_dispatch="yes"))


@pytest.mark.parametrize(
    ("kind", "mode", "subject", "asset_type"),
    [
        ("scan-market", "AUTO", None, None),
        ("stock-research", "FULL", "NVDA", "stock"),
        ("macro-research", "LITE", None, None),
        ("sector-research", "FULL", "801080", None),
        ("dossier-init", "INIT", "600519", None),
    ],
)
def test_begin_request_enforces_kind_mode_matrix(kind, mode, subject, asset_type):
    value = begin_request(
        kind=kind,
        requested_mode=mode,
        subject=subject,
        asset_type=asset_type,
    )
    assert validate_begin_request(value, expected_engine="codex") == value


def test_begin_request_rejects_engine_crossing_and_irrelevant_peers():
    with pytest.raises(ValueError):
        validate_begin_request(begin_request(), expected_engine="claude")
    with pytest.raises(ValueError):
        validate_begin_request(
            begin_request(kind="macro-research", subject=None, asset_type=None, peers=["NVDA"]),
            expected_engine="codex",
        )


def test_force_full_is_an_explicit_scan_only_request_option():
    value = begin_request(
        kind="scan-market",
        requested_mode="AUTO",
        subject=None,
        asset_type=None,
        force_full=True,
    )
    assert validate_begin_request(value, expected_engine="codex")["force_full"] is True
    with pytest.raises(ValueError, match="force_full"):
        validate_begin_request(begin_request(force_full=True), expected_engine="codex")


def test_tool_result_uses_interface_state_not_business_stage_state():
    value = {
        "schema_version": 1,
        "command": "next",
        "run_id": RUN_ID,
        "state": "WAITING",
        "tasks": [],
        "result": None,
        "errors": [],
    }
    assert validate_tool_result(value) == value
    with pytest.raises(ValueError):
        validate_tool_result(dict(value, state="SUCCEEDED"))

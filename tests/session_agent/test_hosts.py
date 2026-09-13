from __future__ import annotations

import pytest

from autoresearch.session_agent.hosts import (
    HostCapabilityError,
    observe_host,
    render_request,
    validate_receipt,
)


def _profile(**changes):
    value = {
        "schema_version": 1,
        "engine": "codex",
        "session_ref": "session-main",
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
        "evidence_refs": ["current-tool-inventory"],
    }
    value.update(changes)
    return value


def _task(**changes):
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


def test_observed_profile_preserves_unknown_and_declared_evidence():
    observed = observe_host(_profile())
    assert observed["safe_resume"] is None
    assert observed["observed_model"] is None


def test_render_request_returns_handoff_data_without_invoking_model():
    request = render_request(_task(), _profile())
    assert request["task_id"] == "stock.card"
    assert request["instruction_refs"][-1] == ".claude/agents/l4-card.md"
    assert request["host"]["engine"] == "codex"


def test_missing_inference_or_independent_context_capability_blocks():
    with pytest.raises(HostCapabilityError, match="inference_handoff"):
        render_request(_task(), _profile(inference_handoff=None))
    with pytest.raises(HostCapabilityError, match="independent_context"):
        render_request(
            _task(independent_context=True),
            _profile(independent_context=False),
        )


def test_receipt_requires_actual_context_identity_for_independent_task():
    receipt = {
        "schema_version": 1,
        "engine": "codex",
        "session_ref": "session-review",
        "context_ref": "context-review",
        "parent_context_ref": "context-main",
        "task_id": "stock.card",
        "attempt": 1,
        "completed": True,
        "evidence_refs": ["transcript:review"],
    }
    assert validate_receipt(
        _task(independent_context=True), receipt, _profile(independent_context=True)
    ) == receipt
    receipt["context_ref"] = "context-main"
    with pytest.raises(ValueError, match="independent"):
        validate_receipt(
            _task(independent_context=True), receipt, _profile(independent_context=True)
        )


def test_receipt_from_other_engine_is_rejected():
    receipt = {
        "schema_version": 1,
        "engine": "claude",
        "session_ref": "session-other",
        "context_ref": "context-other",
        "parent_context_ref": None,
        "task_id": "stock.card",
        "attempt": 1,
        "completed": True,
        "evidence_refs": ["transcript:other"],
    }
    with pytest.raises(ValueError, match="engine"):
        validate_receipt(_task(), receipt, _profile())

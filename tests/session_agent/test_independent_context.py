from __future__ import annotations

import pytest

from autoresearch.session_agent.hosts import validate_receipt


def _profile():
    return {
        "schema_version": 1,
        "engine": "codex",
        "session_ref": "session-main",
        "deterministic_exec": True,
        "capture_binding": True,
        "inference_handoff": True,
        "safe_resume": None,
        "independent_context": True,
        "native_dispatch": False,
        "web_search": True,
        "web_fetch": None,
        "observed_model": None,
        "observed_effort": None,
        "evidence_refs": ["current-tool-inventory"],
    }


def _task():
    return {
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
        "independent_context": True,
        "parent_task": None,
    }


def test_role_rename_in_same_context_is_not_independent_review():
    receipt = {
        "schema_version": 1,
        "engine": "codex",
        "session_ref": "session-main",
        "context_ref": "context-main",
        "parent_context_ref": "context-main",
        "task_id": "stock.card",
        "attempt": 1,
        "completed": True,
        "evidence_refs": ["self-declared-reviewer-role"],
    }
    with pytest.raises(ValueError, match="independent"):
        validate_receipt(_task(), receipt, _profile())

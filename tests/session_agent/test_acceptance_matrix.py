from __future__ import annotations

import pytest

from autoresearch.session_agent.evaluation import build_acceptance_matrix


def test_acceptance_matrix_keeps_each_host_and_evidence_kind_separate():
    value = build_acceptance_matrix(
        [
            {
                "host": "codex",
                "scenario": "stock-lite",
                "status": "PASS",
                "evidence_kind": "REAL_SESSION",
                "run_id": "20260913T010203000000Z",
                "notes": "verified",
            },
            {
                "host": "claude",
                "scenario": "stock-lite",
                "status": "INCOMPLETE",
                "evidence_kind": "NONE",
                "run_id": None,
                "notes": "not yet run by Claude",
            },
        ]
    )
    assert value["overall"] == "INCOMPLETE"
    assert value["hosts"] == {"claude": "INCOMPLETE", "codex": "PASS"}


def test_synthetic_evidence_cannot_mark_a_real_host_scenario_passed():
    with pytest.raises(ValueError, match="REAL_SESSION"):
        build_acceptance_matrix(
            [
                {
                    "host": "codex",
                    "scenario": "scan-full",
                    "status": "PASS",
                    "evidence_kind": "SYNTHETIC",
                    "run_id": None,
                    "notes": "fixture only",
                }
            ]
        )

from __future__ import annotations

import pytest

from autoresearch.session_agent.evaluation import (
    build_comparison,
    compare_manifests,
    validate_comparison,
)


def test_only_explicit_runtime_metadata_is_normalized():
    baseline = {
        "run_id": "old",
        "generated_at": "2026-09-13T01:00:00Z",
        "decision": {"rating": "Buy", "target": 12.3},
    }
    candidate = {
        "run_id": "new",
        "generated_at": "2026-09-13T02:00:00Z",
        "decision": {"rating": "Hold", "target": 12.3},
    }
    diffs = compare_manifests(baseline, candidate)
    assert diffs == [
        {"path": "decision.rating", "baseline": "Buy", "candidate": "Hold"}
    ]


def test_comparison_pass_requires_equal_identity_no_deterministic_diff_and_full_evidence():
    value = build_comparison(
        engine="codex",
        workflow="stock-research",
        mode="LITE",
        baseline_run_id="20260913T010203000000Z",
        candidate_run_id="20260913T020304000000Z",
        input_identity_equal=True,
        config_identity_equal=True,
        deterministic_diffs=[],
        research_diffs=[{"path": "wording", "baseline": "a", "candidate": "b"}],
        missing_evidence=[],
    )
    assert value["verdict"] == "PASS"
    assert validate_comparison(value) == value


@pytest.mark.parametrize(
    ("changes", "verdict"),
    [
        ({"input_identity_equal": False}, "FAIL"),
        ({"config_identity_equal": False}, "FAIL"),
        ({"deterministic_diffs": [{"path": "finalists", "baseline": 8, "candidate": 7}]}, "FAIL"),
        ({"missing_evidence": ["measurement"]}, "INCOMPLETE"),
        ({"baseline_run_id": None, "missing_evidence": ["baseline"]}, "INCOMPLETE"),
    ],
)
def test_comparison_verdict_is_fail_closed(changes, verdict):
    kwargs = {
        "engine": "codex",
        "workflow": "scan-market",
        "mode": "FULL",
        "baseline_run_id": "20260913T010203000000Z",
        "candidate_run_id": "20260913T020304000000Z",
        "input_identity_equal": True,
        "config_identity_equal": True,
        "deterministic_diffs": [],
        "research_diffs": [],
        "missing_evidence": [],
    }
    kwargs.update(changes)
    assert build_comparison(**kwargs)["verdict"] == verdict


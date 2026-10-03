import importlib

import pytest


def module():
    return importlib.import_module("autoresearch.research.stage_effort")


def row(**changes):
    return dict({"engine": "codex", "run_id": "b1", "event_id": "event1", "role": "stock.card",
        "difficulty": "conflict", "input_hash": "a" * 64, "allowed_tools_hash": "b" * 64,
        "source_coverage": "COMPLETE", "cache_condition": "cold", "real_session": True,
        "observed_model": "model-a", "observed_effort": "high", "settings": {"model": "model-a", "effort": "high",
            "context_profile": "full", "search_profile": "baseline"},
        "quality_loss": 0.0, "critical_errors": 0, "access_faults": 0, "publication_faults": 0,
        "total_tokens": 100, "wall_seconds": 20, "metering_complete": True,
        "escalated": False, "retry_count": 0}, **changes)


def protocol(**changes):
    return dict({"factor": "effort", "baseline": "high", "candidate": "medium",
        "min_events": 10, "noninferiority_margin": 0.0, "bootstrap_samples": 200,
        "seed": 42, "confidence": 0.95}, **changes)


def pair(index=1, **changes):
    baseline = row(run_id=f"b{index}", event_id=f"e{index}")
    candidate = row(run_id=f"c{index}", event_id=f"e{index}", total_tokens=60, observed_effort="medium",
        settings=dict(baseline["settings"], effort="medium"), **changes)
    return {"baseline": baseline, "candidate": candidate}


def test_single_factor_and_unknown_model_gate():
    result = module().compare_stage_effort([pair(observed_model="UNKNOWN")], protocol=protocol())
    assert result["admitted_pairs"] == 0
    assert "OBSERVED_MODEL_UNKNOWN" in result["pairs"][0]["reasons"]
    bad = pair()
    bad["candidate"]["settings"]["search_profile"] = "short"
    with pytest.raises(ValueError, match="factor"):
        module().compare_stage_effort([bad], protocol=protocol())


def test_same_event_repeats_do_not_inflate_sample_count():
    pairs = [pair(i) for i in range(1, 21)]
    for item in pairs:
        item["baseline"]["event_id"] = item["candidate"]["event_id"] = "same-event"
    result = module().compare_stage_effort(pairs, protocol=protocol())
    assert result["effective_events"] == 1
    assert result["quality_noninferiority"] == "INSUFFICIENT"


def test_quality_failure_cannot_be_offset_by_cost():
    result = module().compare_stage_effort([pair(i, quality_loss=1.0) for i in range(10)], protocol=protocol())
    assert result["quality_noninferiority"] == "FAIL"
    assert result["candidate_eligible"] is False


def test_fixed_event_cluster_bootstrap_and_cost_separate():
    result = module().compare_stage_effort([pair(i) for i in range(10)], protocol=protocol())
    assert result["quality_noninferiority"] == "PASS"
    assert result["mean_token_difference"] == -40
    assert result["production_adoption"] == "NOT_AUTHORIZED_BY_THIS_READOUT"


def test_duplicate_runs_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        module().compare_stage_effort([pair(), pair()], protocol=protocol())


def test_failure_in_incomplete_run_remains_hard_failure():
    pairs = [pair(i) for i in range(11)]
    pairs[-1]["candidate"].update(access_faults=1, metering_complete=False)
    result = module().compare_stage_effort(pairs, protocol=protocol())
    assert result["quality_noninferiority"] == "FAIL"
    assert not result["candidate_eligible"]


def test_context_treatment_cannot_change_actual_effort():
    pairs = []
    for i in range(10):
        baseline = row(run_id=f"b{i}", event_id=f"e{i}")
        candidate = row(run_id=f"c{i}", event_id=f"e{i}", total_tokens=60,
            observed_effort="low", settings=dict(baseline["settings"], context_profile="index"))
        pairs.append({"baseline": baseline, "candidate": candidate})
    result = module().compare_stage_effort(pairs, protocol=protocol(factor="context_profile", baseline="full", candidate="index"))
    assert result["admitted_pairs"] == 0
    assert not result["candidate_eligible"]

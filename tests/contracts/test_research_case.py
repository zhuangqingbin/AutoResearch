import copy
import importlib

import pytest


def case(**changes):
    return dict({"schema_version": 1, "case_id": "case1", "engine": "codex",
        "event_family": "repurchase", "security": "600000.SS", "analysis_date": "2026-01-02",
        "workflow": "stock-lite", "profile": "single-stage-v1",
        "knowledge_cutoff": "2026-01-02T15:00:00+08:00", "question": "是否已完成回购？",
        "expected_behavior": "区分计划与完成", "input_refs": [], "attempt_refs": [],
        "claim_refs": [], "decision_refs": [], "outcome_refs": [], "review_refs": [],
        "label_state": "PROPOSED", "gold_label": None, "reviewer": [], "reviewed_at": None,
        "label_version": "v1", "disagreements": [], "failure_type": "FACT_ERROR",
        "severity": "HIGH", "suspected_stage": "intel", "confirmed_cause": None,
        "counterexample": "计划公告被写成完成", "split": "train", "group_id": "event1",
        "eligibility": "CANDIDATE", "exclusion_reason": "NEEDS_HUMAN_REVIEW"}, **changes)


def validate(value):
    return importlib.import_module("autoresearch.contracts.research_case").validate_case(value)


def test_proposal_is_not_gold():
    assert validate(case())["gold_label"] is None
    with pytest.raises(ValueError):
        validate(case(gold_label="SUPPORTED"))


def test_unknown_fields_and_naive_cutoff_rejected():
    with pytest.raises(ValueError):
        validate(case(secret_score=99))
    with pytest.raises(ValueError):
        validate(case(knowledge_cutoff="2026-01-02T15:00:00"))


def test_severe_single_review_cannot_be_eligible():
    value = case(label_state="HUMAN_SINGLE", gold_label="CONTRADICTED", reviewer=["human1"],
                 reviewed_at="2026-03-01T10:00:00+08:00", eligibility="ELIGIBLE", exclusion_reason=None)
    with pytest.raises(ValueError):
        validate(value)


def test_validation_does_not_mutate_case():
    value = case()
    original = copy.deepcopy(value)
    validate(value)
    assert value == original

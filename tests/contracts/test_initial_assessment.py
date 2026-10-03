"""B3: a frozen initial assessment is distinct from a publishable decision card."""
from copy import deepcopy

import pytest

from autoresearch.contracts.agent_output import OW_GATES, RUBRIC_DIMENSIONS
from autoresearch.contracts.research_card import (
    validate_decision_changes,
    validate_initial_assessment,
)


def initial_fixture(**changes):
    return dict({
        "schema_version": 1, "subject": "600519", "frame_hash": "a" * 64,
        "fact_manifest_hash": "b" * 64,
        "initial_dimensions": dict.fromkeys(RUBRIC_DIMENSIONS, "未核"),
        "initial_gates": dict.fromkeys(OW_GATES, "UNKNOWN"),
        "initial_rating": "Hold", "key_risks": ["来源不足"],
        "missing_evidence": ["待核验公告"], "evidence_refs": [],
    }, **changes)


def changes_fixture(**changes):
    return dict({"schema_version": 1, "subject": "600519", "initial_hash": "c" * 64,
                 "changed_fields": [], "change_reason": "维持初判", "new_evidence_refs": []}, **changes)


def test_initial_does_not_change_the_callers_object():
    value = initial_fixture()
    before = deepcopy(value)
    assert validate_initial_assessment(value) is value
    assert value == before


@pytest.mark.parametrize("subject", ["NVDA", "BRK-B", "^GSPC", "GC=F", "EURUSD=X", "BTC-USD"])
def test_initial_accepts_non_a_share_subject_without_faking_six_digit_code(subject):
    assert validate_initial_assessment(initial_fixture(subject=subject))["subject"] == subject


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"schema_version": 2}, {"subject": "../600519"},
    {"frame_hash": "bad"}, {"fact_manifest_hash": None}, {"initial_rating": "Strong Buy"},
    {"initial_dimensions": {}}, {"initial_gates": {}},
    {"initial_dimensions": dict.fromkeys(RUBRIC_DIMENSIONS, [])},
    {"initial_gates": dict.fromkeys(OW_GATES, True)},
    {"key_risks": "text"}, {"missing_evidence": [None]}, {"evidence_refs": [""]},
    {"conviction": 99},
])
def test_invalid_initial_rejected(changes):
    with pytest.raises(ValueError):
        validate_initial_assessment(initial_fixture(**changes))


@pytest.mark.parametrize("field", list(initial_fixture()))
def test_missing_initial_field_rejected(field):
    value = initial_fixture()
    del value[field]
    with pytest.raises(ValueError):
        validate_initial_assessment(value)


def test_decision_changes_are_a_separate_strict_record():
    value = changes_fixture(changed_fields=["rating", "gates.主力真在"],
                            change_reason="补充新来源后改判", new_evidence_refs=["source.receipt.1"])
    assert validate_decision_changes(value) is value


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"initial_hash": "bad"}, {"subject": ""},
    {"changed_fields": ["conviction"]}, {"changed_fields": ["rating", "rating"]},
    {"changed_fields": ["rating"], "change_reason": "  "},
    {"new_evidence_refs": [None]}, {"extra": True},
])
def test_invalid_changes_rejected(changes):
    with pytest.raises(ValueError):
        validate_decision_changes(changes_fixture(**changes))

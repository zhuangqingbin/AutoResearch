"""B4 绑定器:四步任一不成立就停在那一步的原因码上;空 trusted_fields 合法且只能 UNKNOWN。"""
from hashlib import sha256

import pytest

from autoresearch.news.claim_binding import support_bound_claim
from autoresearch.news.claim_extract import bundle_from_line

TEXT = "公告:公司已完成回购 10 亿元。"
DECISION = "2026-09-02T14:45:00+08:00"


def claim():
    return bundle_from_line("2026-09-01 已完成回购 10 亿元", subject_code="600000", claim_id="c")["event"]


def bound_bundle(**changes):
    b = bundle_from_line("2026-09-01 已完成回购 10 亿元", subject_code="600000", claim_id="c")
    b["source_observation_ids"] = ["obs-1"]
    b["quote_spans"] = [{"source_observation_id": "obs-1", "start": 3, "end": len(TEXT),
                         "text": TEXT[3:], "blob_hash": sha256(TEXT.encode()).hexdigest()}]
    b["verification_basis"] = "structured_source"
    return dict(b, **changes)


def observations(seen=DECISION.replace("14:45", "09:00")):
    return {"obs-1": {"available_at": seen}}


ALL = {"subject_code", "event_id", "predicate", "lifecycle", "assertion_kind", "polarity",
       "amount_value", "amount_unit", "amount_basis", "effective_at"}


def test_unbound_bundle_is_source_not_bound():
    b = bundle_from_line("已完成回购 10 亿元", subject_code="600000", claim_id="c")
    got = support_bound_claim(b["event"], b, observations={}, texts={}, trusted_fields=ALL,
                              decision_at=DECISION)
    assert got == {"verdict": "UNKNOWN", "reason": "SOURCE_NOT_BOUND", "rule_version": "claim_support.v2"}


def test_non_actionable_run_has_no_decision_time():
    got = support_bound_claim(claim(), bound_bundle(), observations=observations(),
                              texts={"obs-1": TEXT}, trusted_fields=ALL, decision_at=None)
    assert got["reason"] == "RUN_NOT_ACTIONABLE"


def test_future_observation_is_not_available_at_decision():
    got = support_bound_claim(claim(), bound_bundle(), observations=observations(seen="2026-09-02T15:00:00+08:00"),
                              texts={"obs-1": TEXT}, trusted_fields=ALL, decision_at=DECISION)
    assert got["reason"] == "NOT_AVAILABLE_AT_DECISION"


def test_missing_available_at_is_not_available():
    got = support_bound_claim(claim(), bound_bundle(), observations={"obs-1": {"available_at": None}},
                              texts={"obs-1": TEXT}, trusted_fields=ALL, decision_at=DECISION)
    assert got["reason"] == "NOT_AVAILABLE_AT_DECISION"


def test_tampered_quote_is_not_verified():
    got = support_bound_claim(claim(), bound_bundle(), observations=observations(),
                              texts={"obs-1": TEXT + "更正"}, trusted_fields=ALL, decision_at=DECISION)
    assert got["reason"] == "QUOTE_NOT_VERIFIED"


def test_fully_bound_and_trusted_passes():
    got = support_bound_claim(claim(), bound_bundle(), observations=observations(),
                              texts={"obs-1": TEXT}, trusted_fields=ALL, decision_at=DECISION)
    assert got["verdict"] == "PASS" and got["reason"] == "COMPARED"


def test_bound_but_untrusted_fields_cannot_pass():
    """来源绑定齐全、但没有一个字段经复核 → UNKNOWN,不是「引用在就算证实」。"""
    got = support_bound_claim(claim(), bound_bundle(), observations=observations(),
                              texts={"obs-1": TEXT}, trusted_fields=(), decision_at=DECISION)
    assert got["verdict"] == "UNKNOWN" and got["reason"] == "COMPARED"


def test_same_event_refutation_fails():
    b = bound_bundle()
    b["event"] = dict(b["event"], lifecycle="terminated")
    got = support_bound_claim(claim(), b, observations=observations(),
                              texts={"obs-1": TEXT}, trusted_fields=ALL, decision_at=DECISION)
    assert got["verdict"] == "FAIL"


def test_bundle_for_another_subject_is_refused():
    b = bound_bundle()
    b["event"] = dict(b["event"], subject_code="600001")
    with pytest.raises(ValueError):
        support_bound_claim(claim(), b, observations=observations(), texts={"obs-1": TEXT},
                            trusted_fields=ALL, decision_at=DECISION)


def test_naive_decision_time_is_refused():
    with pytest.raises(ValueError):
        support_bound_claim(claim(), bound_bundle(), observations=observations(),
                            texts={"obs-1": TEXT}, trusted_fields=ALL,
                            decision_at="2026-09-02T14:45:00")

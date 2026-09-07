"""B2/B3:事件字段契约 + 保守比较器 —— 关键词命中不是支持,反证只能来自同一已知事件。"""
from hashlib import sha256

import pytest

from autoresearch.contracts.claim_evidence import (
    ENUMS,
    IDENTITY_FIELDS,
    SEMANTIC_FIELDS,
    validate_bundle,
    validate_event,
)
from autoresearch.news.claim_support import compare_events, merge_sources, quote_matches

CHECKED = set(IDENTITY_FIELDS + SEMANTIC_FIELDS)


def event(**changes):
    row = {
        "subject_code": "600000", "event_id": "repurchase-2026-01", "predicate": "回购",
        "lifecycle": "completed", "assertion_kind": "actual", "polarity": "affirmed",
        "amount_value": "1000000000", "amount_unit": "CNY", "amount_basis": "executed_total",
        "effective_at": {"start": "2026-09-01T00:00:00+08:00",
                         "end": "2026-09-02T00:00:00+08:00", "precision": "day"},
    }
    return dict(row, **changes)


# ───────────────────────── B2 契约 ─────────────────────────

def test_valid_event_passes_and_is_returned_unchanged():
    assert validate_event(event()) == event()


def test_first_predicate_batch_includes_insider_buying():
    """「增持」是 A 股最常被拿来拉抬的正面断言,首批不能缺席。"""
    assert ENUMS["predicate"] == {"回购", "增持", "减持", "中标"}


@pytest.mark.parametrize("changes", [
    {"subject_code": "unknown"}, {"subject_code": "60000"}, {"predicate": "传闻"},
    {"lifecycle": "done"}, {"polarity": "yes"},
    {"amount_value": "NaN"}, {"amount_value": "Infinity"}, {"amount_value": "-1"},
    {"amount_value": 1000000000}, {"amount_value": "100", "amount_unit": None},
    {"amount_value": "100", "amount_basis": "unknown"}, {"event_id": ""},
])
def test_invalid_event_rejected(changes):
    with pytest.raises(ValueError):
        validate_event(event(**changes))


@pytest.mark.parametrize("field", sorted(IDENTITY_FIELDS + SEMANTIC_FIELDS))
def test_missing_field_rejected(field):
    row = event()
    del row[field]
    with pytest.raises(ValueError):
        validate_event(row)


def test_unknown_field_rejected():
    with pytest.raises(ValueError):
        validate_event(event(confidence=0.9))


@pytest.mark.parametrize("value", [
    {"start": "2026-09-01T00:00:00", "end": "2026-09-02T00:00:00+08:00", "precision": "day"},
    {"start": "2026-09-02T00:00:00+08:00", "end": "2026-09-01T00:00:00+08:00", "precision": "day"},
    {"start": "2026-09-01T00:00:00+08:00", "end": "2026-09-01T00:00:00+08:00", "precision": "day"},
    {"start": "2026-09-01T00:00:00+08:00", "end": "2026-09-02T00:00:00+08:00", "precision": "week"},
    {"start": "2026-09-01T00:00:00+08:00", "end": "2026-09-02T00:00:00+08:00"},
])
def test_invalid_effective_time_rejected(value):
    with pytest.raises(ValueError):
        validate_event(event(effective_at=value))


def test_effective_time_may_be_unknown():
    validate_event(event(effective_at=None))


def bundle(**changes):
    text = "公告:本次回购已完成。"
    row = {
        "schema_version": 2, "claim_id": "cl_abc", "event": event(),
        "source_observation_ids": ["obs-1"],
        "quote_spans": [{"source_observation_id": "obs-1", "start": 3, "end": len(text),
                         "text": text[3:], "blob_hash": sha256(text.encode()).hexdigest()}],
        "extraction_origin": "regex_v1", "verification_basis": "none",
        "rule_version": "claim_support.v2",
    }
    return dict(row, **changes)


def test_valid_bundle_passes():
    validate_bundle(bundle())


@pytest.mark.parametrize("changes", [
    {"schema_version": 1}, {"claim_id": ""}, {"source_observation_ids": "obs-1"},
    {"quote_spans": [{"start": 0}]}, {"extraction_origin": "llm"},
    {"verification_basis": "trust_me"}, {"event": event(predicate="传闻")},
])
def test_invalid_bundle_rejected(changes):
    with pytest.raises(ValueError):
        validate_bundle(bundle(**changes))


# ───────────────────────── B3 比较器 ─────────────────────────

def test_complete_same_event_supports_claim():
    assert compare_events(event(), event(), checked_fields=CHECKED)["verdict"] == "PASS"


@pytest.mark.parametrize("changes,expected", [
    ({"lifecycle": "terminated"}, "FAIL"),
    ({"lifecycle": "plan", "amount_basis": "planned_cap"}, "FAIL"),
    ({"polarity": "negated"}, "FAIL"),
    ({"amount_value": "500000000"}, "FAIL"),
    ({"event_id": "another-plan"}, "UNKNOWN"),
    ({"subject_code": "600001"}, "UNKNOWN"),
    ({"polarity": "uncertain"}, "UNKNOWN"),
    ({"lifecycle": "unknown"}, "UNKNOWN"),
    ({"amount_value": None, "amount_unit": None, "amount_basis": None}, "UNKNOWN"),
])
def test_counterevidence_requires_same_known_event(changes, expected):
    got = compare_events(event(), event(**changes), checked_fields=CHECKED)
    assert got["verdict"] == expected, got["fields"]


def test_another_event_never_yields_fail_on_any_field():
    """身份对不上时语义字段一律 UNKNOWN —— 拿别的事件比出来的 FAIL 是错杀。"""
    got = compare_events(event(), event(event_id="other", lifecycle="terminated"),
                         checked_fields=CHECKED)
    assert got["verdict"] == "UNKNOWN"
    assert {got["fields"][k] for k in SEMANTIC_FIELDS} == {"UNKNOWN"}


def test_unreviewed_session_extraction_is_unknown():
    assert compare_events(event(), event(), checked_fields=set())["verdict"] == "UNKNOWN"


def test_partially_checked_fields_cannot_reach_pass():
    """只核了身份没核语义 → UNKNOWN,不是「其余默认通过」。"""
    got = compare_events(event(), event(), checked_fields=set(IDENTITY_FIELDS))
    assert got["verdict"] == "UNKNOWN"
    assert all(got["fields"][k] == "PASS" for k in IDENTITY_FIELDS)


def test_claim_without_amount_does_not_invent_one():
    claim = event(amount_value=None, amount_unit=None, amount_basis=None)
    got = compare_events(claim, event(), checked_fields=CHECKED)
    assert got["fields"]["amount_value"] == "PASS" and got["verdict"] == "PASS"


def test_amount_compares_as_decimal_not_string():
    got = compare_events(event(amount_value="1000000000.00"), event(), checked_fields=CHECKED)
    assert got["fields"]["amount_value"] == "PASS"


def test_planned_cap_equal_to_executed_total_is_not_completion():
    """计划上限 10 亿 与 已执行 10 亿 金额相等,但 basis 不同 → FAIL,不是 PASS。"""
    got = compare_events(event(), event(amount_basis="planned_cap"), checked_fields=CHECKED)
    assert got["fields"]["amount_basis"] == "FAIL" and got["verdict"] == "FAIL"


def test_time_mismatch_is_unknown_not_auto_welded():
    other = {"start": "2026-09-03T00:00:00+08:00", "end": "2026-09-04T00:00:00+08:00",
             "precision": "day"}
    got = compare_events(event(), event(effective_at=other), checked_fields=CHECKED)
    assert got["fields"]["effective_at"] == "UNKNOWN" and got["verdict"] == "UNKNOWN"


def test_cumulative_totals_at_different_dates_are_not_a_contradiction():
    later = {"start": "2026-09-03T00:00:00+08:00", "end": "2026-09-04T00:00:00+08:00",
             "precision": "day"}
    got = compare_events(event(lifecycle="in_progress", amount_value="100000000"),
                         event(lifecycle="in_progress", amount_value="200000000", effective_at=later),
                         checked_fields=CHECKED)
    assert got["fields"]["amount_value"] == "UNKNOWN"
    assert got["fields"]["effective_at"] == "UNKNOWN"
    assert got["verdict"] == "UNKNOWN"


def test_result_carries_rule_version():
    assert compare_events(event(), event(), checked_fields=CHECKED)["rule_version"] == "claim_support.v2"


# ───────────────────────── 引用定位 + 多源归并 ─────────────────────────

def test_quote_is_relocatable_and_tamper_evident():
    text = "公告:本次回购已完成。"
    span = {"start": 3, "end": len(text), "text": text[3:],
            "blob_hash": sha256(text.encode("utf-8")).hexdigest()}
    assert quote_matches(text, span)
    assert not quote_matches(text + "更正", span)
    assert not quote_matches(text, dict(span, start=0))
    assert not quote_matches(text, dict(span, end=len(text) + 5))
    assert not quote_matches(text, dict(span, start=True))


def test_conflicting_sources_are_a_conflict_not_first_wins():
    got = merge_sources([{"verdict": "PASS"}, {"verdict": "FAIL"}])
    assert got["verdict"] == "UNKNOWN" and got["reason"] == "source_conflict"


def test_agreeing_sources_pass_and_any_refutation_fails():
    assert merge_sources([{"verdict": "PASS"}, {"verdict": "PASS"}])["verdict"] == "PASS"
    assert merge_sources([{"verdict": "UNKNOWN"}, {"verdict": "FAIL"}])["verdict"] == "FAIL"
    assert merge_sources([{"verdict": "UNKNOWN"}, {"verdict": "PASS"}])["verdict"] == "UNKNOWN"


def test_no_sources_is_unknown():
    assert merge_sources([])["verdict"] == "UNKNOWN"

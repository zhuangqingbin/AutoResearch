"""B4 抽取器(regex_v1):词表匹配、不猜;抽不出来是正确结果。"""
import pytest

from autoresearch.contracts.claim_evidence import validate_bundle
from autoresearch.news.claim_extract import PREDICATES, bundle_from_line, extract_event


def ev(line):
    got = extract_event(line, subject_code="600000")
    assert got is not None, line
    return got["event"]


def test_no_predicate_word_yields_nothing():
    assert extract_event("公司发布三季报业绩预告", subject_code="600000") is None


def test_completed_buyback_with_amount_and_date():
    e = ev("| 2026-09-01 | T0 | 公司公告已完成回购 10 亿元 | http://x | 1.0 |")
    assert (e["predicate"], e["lifecycle"], e["polarity"]) == ("回购", "completed", "affirmed")
    assert (e["amount_value"], e["amount_unit"], e["amount_basis"]) == ("1000000000", "CNY", "executed_total")
    assert e["effective_at"]["start"] == "2026-09-01T00:00:00+08:00"
    assert e["effective_at"]["end"] == "2026-09-02T00:00:00+08:00"


def test_planned_cap_is_a_cap_not_an_execution():
    e = ev("公司拟以不超过 10 亿元回购股份")
    assert e["lifecycle"] == "plan" and e["amount_basis"] == "planned_cap"


def test_planned_completion_is_not_actual_completion():
    e = ev("公司拟完成回购 1 亿元。")
    assert (e["lifecycle"], e["assertion_kind"], e["amount_basis"]) == (
        "plan", "forecast", "planned_cap")


def test_comma_amount_is_not_truncated():
    e = ev("公司已完成回购 1,000 万元。")
    assert e["amount_value"] == "10000000"


def test_invalid_calendar_date_is_unknown_with_diagnostic():
    got = extract_event("公司于 2026-09-31 完成回购 1 亿元。", subject_code="600000")
    assert got["event"]["effective_at"] is None
    assert got["notes"] == ["invalid_date:2026-09-31"]


def test_termination_is_terminated_not_completed():
    e = ev("公司公告终止回购计划,尚未实施回购。")
    assert e["lifecycle"] == "terminated" and e["polarity"] == "negated"


def test_month_end_date_rolls_over_correctly():
    e = ev("2026年8月31日 控股股东已完成增持 500 万股")
    assert e["effective_at"]["end"].startswith("2026-09-01")
    assert (e["amount_value"], e["amount_unit"]) == ("5000000", "shares")


def test_past_tense_without_lifecycle_word_stays_unknown_and_drops_the_amount():
    """「控股股东增持 500 万股」没有生命周期词 —— regex_v1 不把它猜成已完成,金额随之丢弃留痕。
    这是保守的代价,不是缺陷:猜错方向的金额进比较器只会造假 FAIL。"""
    got = extract_event("2026年8月31日 控股股东增持 500 万股", subject_code="600000")
    assert got["event"]["lifecycle"] == "unknown"
    assert got["event"]["amount_value"] is None and got["notes"]


def test_contract_win_amount_is_contract_total():
    e = ev("中标 3.5 亿元项目")
    assert e["amount_basis"] == "contract_total" and e["amount_value"] == "350000000"


def test_amount_without_a_basis_is_dropped_not_guessed():
    """生命周期 unknown 时金额没有口径 —— 丢掉并留痕,不进比较器造假 FAIL。"""
    got = extract_event("减持 2%", subject_code="600000")
    assert got["event"]["amount_value"] is None
    assert got["notes"] and got["notes"][0].startswith("amount_dropped")


def test_rumour_is_uncertain_and_forecast_is_forecast():
    assert ev("据悉公司或将增持")["polarity"] == "uncertain"
    assert ev("预计完成回购")["assertion_kind"] == "forecast"


def test_event_id_is_deterministic_and_distinguishes_dates():
    a = ev("2026-09-01 完成回购 1 亿元")
    b = ev("2026-09-01 完成回购 1 亿元")
    c = ev("2026-09-02 完成回购 1 亿元")
    assert a["event_id"] == b["event_id"] != c["event_id"]


def test_bundle_is_unbound_and_valid():
    b = bundle_from_line("完成回购 1 亿元", subject_code="600000", claim_id="cl_1")
    validate_bundle(b)
    assert b["source_observation_ids"] == [] and b["quote_spans"] == []
    assert b["extraction_origin"] == "regex_v1" and b["verification_basis"] == "none"


def test_predicates_match_the_contract():
    from autoresearch.contracts.claim_evidence import ENUMS
    assert set(PREDICATES) == ENUMS["predicate"]


@pytest.mark.parametrize("line", ["回购", "增持股份", "减持计划终止", "中标"])
def test_every_first_batch_predicate_extracts(line):
    assert extract_event(line, subject_code="600000") is not None

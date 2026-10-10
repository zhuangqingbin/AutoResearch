import json

import pytest

from autoresearch.common.card_decision import card_from_decision_text, validate_card_decision
from tests.common.test_card_decision_v3 import decision_text


def estimate(entry=None):
    return {
        "schema_version": 1,
        "entry": entry,
        "probability_basis": "not_provided",
        "scenarios": [
            {"name": "base", "exit_price": "10.20", "return_range": None, "probability": None}
        ],
        "ev_range": None,
        "rr": None,
    }


def parse(value, suffix=""):
    text = (
        decision_text() + "\n```conditional-scenarios-v1\n" + json.dumps(value) + "\n```\n" + suffix
    )
    return card_from_decision_text(text, subject="NVDA", venue="XNAS", analysis_date="2026-09-13")


def test_point_and_interval_denominators_are_declared():
    value = estimate({"low": "10.00", "high": "10.00"})
    value["scenarios"][0]["return_range"] = {"low": "0.02", "high": "0.02"}
    assert parse(value)["scenario_estimate"] == value
    value["entry"]["low"] = "8.50"
    value["scenarios"][0]["return_range"]["high"] = "0.20"
    assert parse(value)["scenario_estimate"] == value


def test_missing_entry_retains_rating_but_not_precise_returns():
    card = parse(estimate())
    assert validate_card_decision(card)[0] == "Buy"
    assert card["scenario_estimate"]["ev_range"] is None
    value = estimate()
    value["scenarios"][0]["return_range"] = {"low": "0.02", "high": "0.02"}
    with pytest.raises(ValueError, match="entry"):
        parse(value)
    with pytest.raises(ValueError, match="entry"):
        parse(estimate(), "**EV**: 2%\n**R:R**: 2.0")


@pytest.mark.parametrize("price", ["0", "-1", "NaN", "Infinity"])
def test_bad_prices_fail(price):
    value = estimate()
    value["scenarios"][0]["exit_price"] = price
    with pytest.raises(ValueError):
        parse(value)


def test_contradictory_return_fails():
    value = estimate({"low": "10", "high": "10"})
    value["scenarios"][0]["return_range"] = {"low": "0.03", "high": "0.03"}
    with pytest.raises(ValueError, match="return"):
        parse(value)


def test_v2_contract_itself_rejects_tampered_scenario():
    from autoresearch.contracts.research_card import validate_card

    card = parse(estimate())
    card["scenario_estimate"]["scenarios"][0]["exit_price"] = "NaN"
    with pytest.raises(ValueError):
        validate_card(card)


def test_displayed_ev_cannot_conflict_with_machine_scenarios():
    value = estimate({"low": "10", "high": "10"})
    value["scenarios"][0]["return_range"] = {"low": "0.02", "high": "0.02"}
    with pytest.raises(ValueError, match="EV"):
        parse(value, "**EV**: 99%")


@pytest.mark.parametrize(
    "display", ["**EV**: **5%**", "| R:R | **2.5** |", "**EV**：`0.02`", "| EV | _5%_ |"]
)
def test_markdown_formatting_does_not_hide_precise_missing_denominator(display):
    with pytest.raises(ValueError, match="entry"):
        parse(estimate(), display)


@pytest.mark.parametrize(
    "display",
    [
        "**EV**: **未核**",
        "| R:R | — |",
        "**EV**: 缺入场价，未核",
        "R:R requires 2 more observations",
    ],
)
def test_non_numeric_unknown_displays_are_allowed(display):
    assert parse(estimate(), display)["scenario_estimate"]["entry"] is None


# ── R5-1(2026-10-07 第 5 场唯一阻断):卡面数字按声明精度四舍五入,校验按声明精度留容差 ──────
# 被拒的 688578 review2 卡:entry 111.58、exit 114.26/111.80/109.13 → 精确收益 0.024018…/0.001971…/
# −0.021957…;卡面写 0.0240/0.0020/−0.0220、rr 1.09。agent 没有计算工具,比值与四舍五入必然对不上 1e-9。


def three_scenarios(returns, *, entry="111.58", exits=("114.26", "111.80", "109.13"),
                    probabilities=("0.3", "0.4", "0.3"), ev=None, rr=None):
    return {
        "schema_version": 1,
        "entry": {"low": entry, "high": entry},
        "probability_basis": "subjective",
        "scenarios": [
            {"name": name, "exit_price": exit_price, "return_range": {"low": ret, "high": ret},
             "probability": probability}
            for name, exit_price, ret, probability in zip(("bull", "base", "bear"), exits, returns, probabilities)
        ],
        "ev_range": None if ev is None else {"low": ev, "high": ev},
        "rr": rr,
    }


def test_returns_ev_and_rr_rounded_to_four_decimals_are_accepted():
    # EV 按卡面已舍入的收益算:0.3·0.0240 + 0.4·0.0020 + 0.3·(−0.0220) = 0.0014;R:R = 0.0240/0.0220 = 1.0909…
    value = three_scenarios(("0.0240", "0.0020", "-0.0220"), ev="0.0014", rr="1.0909")
    assert parse(value)["scenario_estimate"] == value


def test_rr_declared_to_two_decimals_is_within_the_propagated_rounding_of_the_returns():
    # 收益各自舍入 ±0.00005 传播到比值 ≈ ±0.0048,所以「1.09」与 1.0909… 相容;「1.2」不相容。
    assert parse(three_scenarios(("0.0240", "0.0020", "-0.0220"), rr="1.09"))["scenario_estimate"]["rr"] == "1.09"
    with pytest.raises(ValueError, match="R:R"):
        parse(three_scenarios(("0.0240", "0.0020", "-0.0220"), rr="1.2"))


def test_two_decimal_rounding_of_an_inexact_return_is_still_rejected():
    # 声明精度不足 4 位时容差仍只有半个万分位:0.02 对 0.024018… 差 0.004,拒。
    with pytest.raises(ValueError, match="return"):
        parse(three_scenarios(("0.02", "0.00", "-0.02")))


def test_ev_off_by_more_than_the_propagated_rounding_is_rejected():
    with pytest.raises(ValueError, match="EV"):
        parse(three_scenarios(("0.0240", "0.0020", "-0.0220"), ev="0.0024"))


def test_exact_returns_remain_valid_at_any_precision():
    exact = [format(__import__("decimal").Decimal(x) / __import__("decimal").Decimal("111.58") - 1, "f")
             for x in ("114.26", "111.80", "109.13")]
    value = three_scenarios(tuple(exact))
    assert parse(value)["scenario_estimate"] == value


def test_declared_tolerance_is_half_the_last_place_capped_at_four_decimals():
    from decimal import Decimal

    from autoresearch.contracts.execution import SCENARIO_PRECISION, declared_tolerance

    assert SCENARIO_PRECISION == Decimal("0.0001")
    assert declared_tolerance("0.0240") == Decimal("0.00005")
    assert declared_tolerance("0.02") == Decimal("0.00005")           # 更粗的声明不放宽
    assert declared_tolerance("0.024018") == Decimal("0.0000005")     # 更细的声明更严
    assert declared_tolerance("1") == Decimal("0.00005")


def test_card_contract_states_the_rounding_rule():
    from autoresearch.common.card_decision import decision_instruction

    text = decision_instruction(subject="688578", venue="XSHG")
    assert "4 位小数" in text and "容差" in text

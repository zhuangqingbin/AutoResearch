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

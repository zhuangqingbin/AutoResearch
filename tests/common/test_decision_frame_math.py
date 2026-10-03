import pytest

from autoresearch.common import execution_math as math


def test_conditional_gap_uses_declared_entry_not_analysis_price():
    assert math.conditional_gap("10.20", "10.00") == "0.02"
    assert math.conditional_gap("9", "10") == "-0.1"
    assert math.conditional_gap("10.20", None) is None


@pytest.mark.parametrize("price", ["0", "-1", "NaN", "Infinity", 10.0, "bad"])
def test_conditional_gap_rejects_invalid_decimal_prices(price):
    with pytest.raises(ValueError):
        math.conditional_gap(price, "10")


def test_frame_builder_uses_supplied_trading_calendar_across_holiday():
    value = math.build_decision_frame(
        analysis_session="2026-09-30", knowledge_cutoff="2026-09-30T21:30:00+08:00",
        venue="XSHG", research_depth="FULL", usage="standalone",
        sessions=["2026-09-30", "2026-10-09", "2026-10-12"],
        calendar_quality="trade_cal",
    )
    assert (value["entry_session"], value["exit_session"]) == ("2026-10-09", "2026-10-12")
    assert value["return_basis"] == "ENTRY_PRICE"


@pytest.mark.parametrize("sessions,quality", [
    (["2026-09-30", "2026-10-01", "2026-10-02"], "weekday_heuristic"),
    (["2026-09-30", "2026-10-09"], "trade_cal"),
    (["2026-10-09", "2026-10-12"], "trade_cal"),
])
def test_unverified_or_incomplete_calendar_yields_unknown(sessions, quality):
    value = math.build_decision_frame(
        analysis_session="2026-09-30", knowledge_cutoff="2026-09-30T21:30:00+08:00",
        venue="XSHG", research_depth="LITE", usage="scan", sessions=sessions,
        calendar_quality=quality,
    )
    assert value["entry_session"] is None
    assert value["exit_session"] is None
    assert value["calendar_quality"] == "UNKNOWN"


def test_us_frame_has_venue_timezone_and_preserves_aware_cutoff():
    value = math.build_decision_frame(
        analysis_session="2026-09-30", knowledge_cutoff="2026-10-01T01:30:00+00:00",
        venue="XNYS", research_depth="FULL", usage="standalone",
        sessions=["2026-09-30", "2026-10-01", "2026-10-02"],
        calendar_quality="exchange_calendar",
    )
    assert value["timezone"] == "America/New_York"
    assert value["knowledge_cutoff"] == "2026-10-01T01:30:00+00:00"


@pytest.mark.parametrize("field", ["venue", "calendar_quality", "research_depth", "usage"])
@pytest.mark.parametrize("bad", [[], {}, 1, None, True])
def test_builder_rejects_bad_enum_types_with_value_error(field, bad):
    values = dict(analysis_session="2026-09-30", knowledge_cutoff="2026-09-30T21:00:00+08:00",
                  venue="XSHG", research_depth="LITE", usage="scan",
                  sessions=["2026-09-30", "2026-10-09", "2026-10-12"], calendar_quality="trade_cal")
    values[field] = bad
    with pytest.raises(ValueError):
        math.build_decision_frame(**values)


def test_builder_rejects_unsettled_analysis_day_without_inventing_foreign_close():
    values = dict(analysis_session="2026-09-30", knowledge_cutoff="2026-09-30T14:59:00+08:00",
                  venue="XSHG", research_depth="LITE", usage="scan",
                  sessions=["2026-09-30", "2026-10-09", "2026-10-12"], calendar_quality="trade_cal")
    with pytest.raises(ValueError, match="settled"):
        math.build_decision_frame(**values)
    # Foreign session close times belong to the injected calendar owner.
    values.update(venue="XNYS", knowledge_cutoff="2026-09-30T13:00:00-04:00",
                  calendar_quality="exchange_calendar")
    assert math.build_decision_frame(**values)["venue"] == "XNYS"

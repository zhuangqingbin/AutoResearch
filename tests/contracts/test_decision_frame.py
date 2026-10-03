from copy import deepcopy

import pytest

from autoresearch.contracts import execution


def frame():
    return {
        "schema_version": 1, "analysis_session": "2026-09-30",
        "knowledge_cutoff": "2026-09-30T21:30:00+08:00",
        "venue": "XSHG", "timezone": "Asia/Shanghai", "ruler": "gap_c1_o2",
        "research_depth": "FULL", "usage": "standalone",
        "entry_session": "2026-10-09", "entry_phase": "CLOSE",
        "exit_session": "2026-10-12", "exit_phase": "OPEN",
        "return_basis": "ENTRY_PRICE", "calendar_quality": "trade_cal",
    }


def test_decision_frame_accepts_full_depth_without_changing_horizon():
    value = frame()
    assert execution.validate_decision_frame(value) == value


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("ruler", "fwd_10_oc"), ("return_basis", "CURRENT_PRICE"),
    ("entry_phase", "OPEN"), ("exit_phase", "CLOSE"), ("research_depth", "SWING"),
    ("usage", "unknown"), ("knowledge_cutoff", "2026-09-30T21:30:00"),
    ("entry_session", "2026-09-30"), ("exit_session", "2026-10-08"),
    ("timezone", "Invalid/Zone"), ("timezone", "America/New_York"),
    ("venue", "UNKNOWN"), ("calendar_quality", "weekday_heuristic"),
])
def test_decision_frame_rejects_ambiguous_or_wrong_clock(field, value):
    candidate = frame()
    candidate[field] = value
    with pytest.raises(ValueError):
        execution.validate_decision_frame(candidate)


def test_decision_frame_unknown_calendar_does_not_invent_sessions():
    value = frame()
    value.update(entry_session=None, exit_session=None, calendar_quality="UNKNOWN")
    assert execution.validate_decision_frame(value) == value
    value["entry_session"] = "2026-10-01"
    with pytest.raises(ValueError):
        execution.validate_decision_frame(value)


def test_decision_frame_strict_fields_and_no_mutation():
    value = frame()
    before = deepcopy(value)
    execution.validate_decision_frame(value)
    assert value == before
    for key in value:
        partial = dict(value)
        del partial[key]
        with pytest.raises(ValueError):
            execution.validate_decision_frame(partial)
    with pytest.raises(ValueError):
        execution.validate_decision_frame(dict(value, holding_days=10))


@pytest.mark.parametrize("field", [
    "ruler", "entry_phase", "exit_phase", "return_basis", "research_depth",
    "usage", "venue", "timezone", "calendar_quality",
])
@pytest.mark.parametrize("bad", [[], {}, 1, None, True])
def test_frame_enum_bad_types_raise_value_error(field, bad):
    value = frame()
    value[field] = bad
    with pytest.raises(ValueError):
        execution.validate_decision_frame(value)


@pytest.mark.parametrize("venue", ["XSHG", "XSHE", "XBSE"])
def test_verified_ashare_analysis_requires_settled_close(venue):
    value = frame()
    value["venue"] = venue
    value["knowledge_cutoff"] = "2026-09-30T14:59:59+08:00"
    with pytest.raises(ValueError, match="settled"):
        execution.validate_decision_frame(value)
    value["knowledge_cutoff"] = "2026-09-30T07:00:00Z"
    assert execution.validate_decision_frame(value) == value


def test_unknown_calendar_remains_nonexecutable_before_close():
    value = frame()
    value.update(knowledge_cutoff="2026-09-30T09:00:00+08:00", calendar_quality="UNKNOWN",
                 entry_session=None, exit_session=None)
    assert execution.validate_decision_frame(value)["entry_session"] is None

import hashlib
import json
from types import SimpleNamespace

import pytest

from autoresearch.contracts.session_task import validate_begin_request
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.decision_frame import register_frame

from .test_service import _handle, _request


def calendar():
    return {
        "schema_version": 1,
        "venue": "XNAS",
        "timezone": "America/New_York",
        "source_id": "exchange-published-test-fixture",
        "published_at": "2026-11-01T00:00:00-04:00",
        "available_at": "2026-11-01T00:00:00-04:00",
        "sessions": [
            {
                "date": "2026-11-27",
                "open_at": "2026-11-27T09:30:00-05:00",
                "close_at": "2026-11-27T13:00:00-05:00",
            },
            {
                "date": "2026-11-30",
                "open_at": "2026-11-30T09:30:00-05:00",
                "close_at": "2026-11-30T16:00:00-05:00",
            },
            {
                "date": "2026-12-01",
                "open_at": "2026-12-01T09:30:00-05:00",
                "close_at": "2026-12-01T16:00:00-05:00",
            },
        ],
    }


def setup(tmp_path, *, cutoff="2026-11-27T18:01:00Z", source=True):
    handle = _handle(tmp_path)
    handle.analysis_date = "2026-11-27"
    handle.contract = SimpleNamespace(created_at=cutoff)
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps(calendar()))
    request = dict(
        _request(),
        schema_version=3,
        subject="NVDA",
        analysis_date=handle.analysis_date,
        card_research_profile="single-stage-v1",
        research_context={
            "venue": "XNAS",
            "usage": "standalone",
            "calendar_source_path": str(path) if source else None,
        },
    )
    return handle, request, path


def test_explicit_external_calendar_freezes_bytes_and_early_close(tmp_path):
    handle, request, path = setup(tmp_path)
    validate_begin_request(request, expected_engine="codex")
    frame = register_frame(request, handle)
    assert frame["entry_session"] == "2026-11-30"
    assert frame["exit_session"] == "2026-12-01"
    assert (
        frame["calendar_evidence"]["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    with artifacts.open_artifact(handle, "research.calendar") as stream:
        assert stream.read() == path.read_bytes()
    before = (handle.staging / "session_outputs/decision_frame.json").read_bytes()
    path.write_text("{}")
    assert register_frame(request, handle) == frame
    assert (handle.staging / "session_outputs/decision_frame.json").read_bytes() == before


def test_external_calendar_before_early_close_is_rejected(tmp_path):
    handle, request, _ = setup(tmp_path, cutoff="2026-11-27T17:59:00Z")
    with pytest.raises(ValueError, match="settled"):
        register_frame(request, handle)


@pytest.mark.parametrize("change", ["venue", "timezone", "future_source", "unordered"])
def test_calendar_evidence_conflicts_fail(tmp_path, change):
    handle, request, path = setup(tmp_path)
    data = calendar()
    if change == "venue":
        data["venue"] = "XNYS"
    if change == "timezone":
        data["timezone"] = "Asia/Shanghai"
    if change == "future_source":
        data["published_at"] = "2026-11-27T18:02:00Z"
    if change == "unordered":
        data["sessions"].reverse()
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        register_frame(request, handle)


def test_no_calendar_remains_unknown_for_explicit_venue(tmp_path):
    handle, request, _ = setup(tmp_path, source=False)
    frame = register_frame(request, handle)
    assert frame["venue"] == "XNAS"
    assert frame["calendar_quality"] == "UNKNOWN"
    assert frame["entry_session"] is None


def test_review_freezes_prior_frame_and_keeps_original_analysis_anchor(tmp_path):
    from autoresearch.common.atomic import atomic_write_json
    from autoresearch.session_agent.service import _predecessor_evidence

    first, request, _ = setup(tmp_path / "first")
    old = register_frame(request, first)
    first.business_status = "SUCCEEDED"
    atomic_write_json(first.workspace / "session/request.json", request)
    later, review, _ = setup(tmp_path / "review", cutoff="2026-11-30T16:00:00Z")
    later.run_id = "20261130T160000000000Z"
    review["predecessor_run_id"] = first.run_id
    review["research_context"]["usage"] = "holding_review"
    prior = _predecessor_evidence(review, loader=lambda _: first)
    atomic_write_json(later.workspace / "session/predecessor.json", prior)
    new = register_frame(review, later)
    assert new["analysis_session"] == old["analysis_session"]
    assert new["knowledge_cutoff"] != old["knowledge_cutoff"]
    assert (
        new["predecessor_frame_hash"]
        == artifacts.snapshot_artifact(first, "research.frame")["sha256"]
    )
    assert new["usage"] == "holding_review"
    assert register_frame(request, first) == old
    review["analysis_date"] = "2026-11-30"
    with pytest.raises(ValueError, match="anchor"):
        _predecessor_evidence(review, loader=lambda _: first)


def test_v2_registered_frame_cannot_be_changed_on_reentry(tmp_path):
    from autoresearch.common.atomic import atomic_write_json

    handle, request, _ = setup(tmp_path)
    atomic_write_json(
        handle.workspace / "session/storage.json",
        {
            "schema_version": 1,
            "output_layout_version": 2,
            "run_id": handle.run_id,
            "plan_hash": "a" * 64,
        },
    )
    frame = register_frame(request, handle)
    frame.update(calendar_quality="UNKNOWN", entry_session=None, exit_session=None)
    (handle.staging / "session_outputs/decision_frame.json").write_text(json.dumps(frame))
    with pytest.raises(RuntimeError, match="changed"):
        register_frame(request, handle)


def test_long_holiday_calendar_keeps_consecutive_exchange_sessions(tmp_path):
    handle = _handle(tmp_path)
    handle.analysis_date = "2026-09-30"
    handle.contract.created_at = "2026-09-30T12:00:00Z"
    request = dict(
        _request(),
        schema_version=3,
        analysis_date=handle.analysis_date,
        card_research_profile="single-stage-v1",
        research_context={"venue": "XSHG", "usage": "standalone", "calendar_source_path": None},
    )
    frame = register_frame(
        request,
        handle,
        calendar_loader=lambda *_: (["2026-09-30", "2026-10-08", "2026-10-09"], "trade_cal"),
    )
    assert (frame["entry_session"], frame["exit_session"]) == ("2026-10-08", "2026-10-09")
    assert frame["calendar_evidence"]["source"]["source_id"] == "trade_cal"


def test_calendar_engine_boundary_is_relative_to_handle_engine():
    from autoresearch.session_agent.decision_frame import check_calendar_engine

    check_calendar_engine(("declared", "context_codex", "calendar.json"), engine="codex")
    check_calendar_engine(("declared", "context_claude", "calendar.json"), engine="claude")
    with pytest.raises(ValueError, match="engine"):
        check_calendar_engine(("declared", "reports_claude", "calendar.json"), engine="codex")
    with pytest.raises(ValueError, match="engine"):
        check_calendar_engine(("declared", "reports_codex", "calendar.json"), engine="claude")

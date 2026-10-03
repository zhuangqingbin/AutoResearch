"""Mocked historical FRED contract checks; these do not validate live ALFRED data."""

import json
from unittest.mock import Mock

import pytest

from autoresearch.dataflows import fred

pytestmark = pytest.mark.unit


@pytest.fixture
def requests_seen(monkeypatch):
    calls = []

    def request(path, params):
        calls.append((path, params))
        if path == "series":
            return {"seriess": [{"title": "CPI"}]}
        return {"observations": [{"date": "2025-06-01", "value": "100"}]}

    monkeypatch.setattr(fred, "_request", request)
    return calls


def test_default_vintage_pins_metadata_and_observations(requests_seen):
    fred.get_macro_data("cpi", "2025-07-15")
    assert len(requests_seen) == 2
    for _, params in requests_seen:
        assert params.get("realtime_start") == "2025-07-15"
        assert params.get("realtime_end") == "2025-07-15"


def test_revision_selected_by_vintage_not_observation_date(monkeypatch):
    def request(path, params):
        if path == "series":
            return {"seriess": [{"title": "CPI"}]}
        vintage = params.get("realtime_start")
        return {
            "observations": [
                {"date": "2025-06-01", "value": "100" if vintage == "2025-07-15" else "200"}
            ]
        }

    monkeypatch.setattr(fred, "_request", request)
    original = fred.get_macro_data("cpi", "2025-08-15", vintage_date="2025-07-15")
    revised = fred.get_macro_data("cpi", "2025-08-15", vintage_date="2025-08-15")
    assert "**Latest:** 100 " in original
    assert "**Latest:** 200 " in revised


@pytest.mark.parametrize(
    "kwargs",
    [
        {"vintage_date": "2025-07-16"},
        {"vintage_date": "2025-07-15", "knowledge_cutoff": "2025-07-14"},
        {"vintage_date": "2025-7-15"},
        {"vintage_date": "bad"},
        {"knowledge_cutoff": "2025-07-15T12:00:00"},
        {"knowledge_cutoff": "2025-07-15T12:00:00+25:00"},
        {"knowledge_cutoff": "not-a-date"},
    ],
)
def test_invalid_or_future_vintage_rejected_before_network(requests_seen, kwargs):
    with pytest.raises(ValueError):
        fred.get_macro_data("cpi", "2025-07-15", **kwargs)
    assert requests_seen == []


def test_date_precision_is_explicit_and_intraday_is_unknown(requests_seen):
    daily = fred.get_macro_data("cpi", "2025-07-15")
    assert "Vintage: 2025-07-15 (day precision)" in daily
    assert "intraday availability unknown" in daily
    intraday = fred.get_macro_data(
        "cpi", "2025-07-15", knowledge_cutoff="2025-07-15T23:00:00-05:00"
    )
    assert "MACRO_DATA_UNAVAILABLE" in intraday
    assert "intraday availability unknown" in intraday
    assert "**Latest:**" not in intraday


def test_cutoff_uses_fred_timezone_and_previous_complete_day(requests_seen):
    # UTC July 16 is still July 15 in St Louis; no same-day certainty.
    out = fred.get_macro_data("cpi", "2025-07-15", knowledge_cutoff="2025-07-16T01:00:00Z")
    assert "MACRO_DATA_UNAVAILABLE" in out
    out = fred.get_macro_data("cpi", "2025-07-15", knowledge_cutoff="2025-07-16T05:00:00Z")
    assert "**Latest:** 100 " in out


def test_response_with_later_release_is_excluded(monkeypatch):
    def request(path, params):
        if path == "series":
            return {"seriess": [{"title": "CPI"}]}
        return {
            "observations": [
                {
                    "date": "2025-06-01",
                    "value": "200",
                    "realtime_start": "2025-07-16",
                    "realtime_end": "9999-12-31",
                },
            ]
        }

    monkeypatch.setattr(fred, "_request", request)
    out = fred.get_macro_data("cpi", "2025-07-15")
    assert "MACRO_DATA_UNAVAILABLE" in out
    assert "**Latest:**" not in out


def test_empty_observations_explicit_unavailable(monkeypatch):
    monkeypatch.setattr(
        fred,
        "_request",
        lambda path, params: {"seriess": [{}]} if path == "series" else {"observations": []},
    )
    assert "MACRO_DATA_UNAVAILABLE" in fred.get_macro_data("cpi", "2025-07-15")


def test_actual_tool_and_router_forward_vintage(requests_seen):
    from autoresearch.agents.utils.agent_utils import get_macro_indicators

    get_macro_indicators.invoke(
        {
            "indicator": "cpi",
            "curr_date": "2025-07-15",
            "vintage_date": "2025-07-14",
            "knowledge_cutoff": "2025-07-14",
        }
    )
    assert all(params.get("realtime_start") == "2025-07-14" for _, params in requests_seen)


def test_request_captures_raw_realtime_fields_and_unknown_release_time(monkeypatch):
    import autoresearch.common.source_capture as receipts

    payload = {"realtime_start": "2025-07-15", "realtime_end": "2025-07-15", "observations": []}
    raw = json.dumps(payload).encode()
    response = Mock(status_code=200, content=raw)
    response.json.return_value = payload
    monkeypatch.setenv("FRED_API_KEY", "secret")
    monkeypatch.setattr(fred.requests, "get", lambda *a, **kw: response)
    capture = Mock()
    monkeypatch.setattr(receipts, "record_source_response", capture)
    assert (
        fred._request(
            "series/observations", {"realtime_start": "2025-07-15", "realtime_end": "2025-07-15"}
        )
        == payload
    )
    kwargs = capture.call_args.kwargs
    assert kwargs["consumer_artifact_ids"] == []
    assert kwargs["raw_bytes"] == raw
    assert kwargs["outcome"] == payload
    assert kwargs["source_timing"]["published_at"] is None
    assert kwargs["source_timing"]["first_available_at"] is None
    assert kwargs["source_timing"]["received_at"] == kwargs["ended_at"]
    assert kwargs["params"]["adapter_version"] == fred.ADAPTER_VERSION
    assert "secret" not in str(kwargs)


def test_active_capture_preserves_v2_timing_and_raw_payload(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from autoresearch.common import workspace
    from autoresearch.trace import capsule, source_receipts

    monkeypatch.setattr(workspace, "active_run_id", lambda: "20260914T120000000000Z")
    handle = SimpleNamespace(engine="codex", run_id="20260914T120000000000Z", capsule=tmp_path)
    monkeypatch.setattr(capsule, "require_active_run", lambda run_id: handle)
    monkeypatch.setenv("AUTORESEARCH_TASK_ID", "macro.harvest")
    timing = {
        "published_at": None,
        "first_available_at": None,
        "received_at": "2026-09-14T12:00:01Z",
        "timestamp_precision": {
            "published_at": None,
            "first_available_at": None,
            "received_at": "second",
        },
    }
    row = source_receipts.record_active_response(
        provider="fred",
        endpoint="series",
        params={"realtime_start": "2025-07-15"},
        outcome={"seriess": []},
        consumer_artifact_ids=["macro.data"],
        raw_bytes=b'{"seriess": []}',
        started_at="2026-09-14T12:00:00Z",
        ended_at="2026-09-14T12:00:01Z",
        source_timing=timing,
    )
    assert row["schema_version"] == 2
    assert row["source_timing"] == timing
    assert row["available_at"] is None
    assert row["started_at"] == "2026-09-14T12:00:00Z"
    assert row["raw_hash"] is not None
    assert source_receipts.replay_response(tmp_path, row["receipt_id"]) == {"seriess": []}


@pytest.mark.parametrize("cancel", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("capture_fails", [False, True])
def test_cancellation_survives_response_capture(monkeypatch, cancel, capture_fails):
    import autoresearch.common.source_capture as receipts
    from autoresearch.macro.harvest import us_macro_block

    original = cancel("stop now")
    request = Mock(side_effect=original)
    capture = Mock(side_effect=RuntimeError("receipt failed") if capture_fails else None)
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    monkeypatch.setattr(fred.requests, "get", request)
    monkeypatch.setattr(receipts, "record_source_response", capture)
    with pytest.raises(cancel) as raised:
        us_macro_block("2025-07-15")
    assert raised.value is original
    assert request.call_count == 1
    assert capture.call_args.kwargs["outcome"] is original


@pytest.mark.parametrize("caller", ["us", "global", "stock", "tool"])
@pytest.mark.parametrize("threaded", [False, True])
def test_harvest_boundaries_capture_actual_tool_raw_receipts(monkeypatch, tmp_path, caller, threaded):
    from types import SimpleNamespace

    from autoresearch.analyze import harvest as stock
    from autoresearch.common import workspace
    from autoresearch.macro import harvest as macro
    from autoresearch.trace import capsule, source_receipts

    run_id = "20260914T120000000000Z"
    handle = SimpleNamespace(engine="codex", run_id=run_id, capsule=tmp_path)
    monkeypatch.setattr(workspace, "active_run_id", lambda: run_id)
    monkeypatch.setattr(capsule, "require_active_run", lambda _: handle)
    monkeypatch.setenv("AUTORESEARCH_TASK_ID", "stock.harvest" if caller == "stock" else "macro.harvest")
    monkeypatch.setenv("AUTORESEARCH_ATTEMPT", "2")
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    monkeypatch.setattr(macro, "US_FRED", ["cpi"])
    monkeypatch.setattr(macro, "INTL_FRED", {"CPI": "cpi"})
    monkeypatch.setattr(stock, "MACRO", ["cpi"])
    def response(url, **kwargs):
        value = {"seriess": [{"title": "CPI", "units": "Index"}]} if url.endswith("/series") else {"observations": [{"date": "2025-07-01", "value": "1"}]}
        result = Mock(status_code=200, content=json.dumps(value).encode())
        result.json.return_value = value
        return result
    monkeypatch.setattr(fred.requests, "get", response)
    def invoke():
        if caller == "tool":
            from autoresearch.agents.utils.macro_data_tools import get_macro_indicators
            return get_macro_indicators.invoke({"indicator": "cpi", "curr_date": "2025-07-15"})
        if caller == "stock":
            return stock._blk_macro_series({"end": "2025-07-15"})
        return (macro.us_macro_block if caller == "us" else macro.global_macro_block)("2025-07-15")
    if threaded:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as executor:
            output = executor.submit(invoke).result()
    else:
        output = invoke()
    assert "CPI" in output
    rows = source_receipts.read_receipts(tmp_path)
    assert {row["endpoint"] for row in rows} == {"series", "series/observations"}
    assert len(rows) == 2
    assert all(row["attempt"] == 2 and row["raw_hash"] is not None for row in rows)
    assert all(row["normalized_params"]["realtime_start"] == "2025-07-15" for row in rows)
    assert all(row["source_timing"]["first_available_at"] is None for row in rows)


def test_source_recorder_requires_binding_only_for_active_runs(monkeypatch):
    from autoresearch.common import source_capture, workspace
    monkeypatch.setattr(source_capture, "_DEFAULT_RECORDER", None)
    monkeypatch.setattr(workspace, "active_run_id", lambda: None)
    assert source_capture.record_source_response(outcome={}) is None
    monkeypatch.setattr(workspace, "active_run_id", lambda: "active")
    with pytest.raises(RuntimeError, match="requires a source recorder"):
        source_capture.record_source_response(outcome={})
    seen = []
    with source_capture.use_source_recorder(lambda **kw: seen.append(kw)):
        source_capture.record_source_response(outcome={})
    assert seen == [{"outcome": {}}]
    with pytest.raises(RuntimeError, match="requires a source recorder"):
        source_capture.record_source_response(outcome={})


@pytest.mark.parametrize("failure", ["http", "cancel"])
@pytest.mark.parametrize("caller", ["harvest", "tool"])
def test_harvest_bridge_preserves_failed_raw_and_cancellation(monkeypatch, tmp_path, failure, caller):
    from types import SimpleNamespace

    from autoresearch.common import workspace
    from autoresearch.macro import harvest
    from autoresearch.trace import capsule, source_receipts

    run_id = "20260914T120000000000Z"
    handle = SimpleNamespace(engine="codex", run_id=run_id, capsule=tmp_path)
    monkeypatch.setattr(workspace, "active_run_id", lambda: run_id)
    monkeypatch.setattr(capsule, "require_active_run", lambda _: handle)
    monkeypatch.setenv("AUTORESEARCH_TASK_ID", "macro.harvest")
    monkeypatch.setenv("AUTORESEARCH_ATTEMPT", "1")
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    monkeypatch.setattr(harvest, "US_FRED", ["cpi"])
    raw = b'{"error_message":"synthetic unavailable"}'
    response = Mock(status_code=400, content=raw)
    response.json.return_value = json.loads(raw)
    stop = KeyboardInterrupt("synthetic cancellation")
    request = Mock(side_effect=stop) if failure == "cancel" else Mock(return_value=response)
    monkeypatch.setattr(fred.requests, "get", request)
    def invoke():
        if caller == "tool":
            from autoresearch.agents.utils.macro_data_tools import get_macro_indicators
            return get_macro_indicators.invoke({"indicator": "cpi", "curr_date": "2025-07-15"})
        return harvest.us_macro_block("2025-07-15")
    if failure == "cancel":
        with pytest.raises(KeyboardInterrupt) as caught:
            invoke()
        assert caught.value is stop
    elif caller == "tool":
        assert "MACRO_DATA_UNAVAILABLE" in invoke()
    else:
        assert "unavailable" in invoke()
    assert request.call_count == 1
    rows = source_receipts.read_receipts(tmp_path)
    assert len(rows) == 1 and rows[0]["status"] == "FAILED"
    assert rows[0]["error"]["category"] == ("KeyboardInterrupt" if failure == "cancel" else "ValueError")
    if failure == "http":
        from autoresearch.trace.blobs import blob_path
        assert blob_path(tmp_path, rows[0]["raw_hash"]).read_bytes() == raw


def test_source_recorder_override_precedes_default_and_restores(monkeypatch):
    from autoresearch.common import source_capture

    calls = []
    monkeypatch.setattr(source_capture, "_DEFAULT_RECORDER", lambda **kw: calls.append("default"))
    with source_capture.use_source_recorder(lambda **kw: calls.append("override")):
        source_capture.record_source_response()
    source_capture.record_source_response()
    assert calls == ["override", "default"]
    with pytest.raises(TypeError, match="callable"):
        source_capture.register_source_recorder(None)


def test_harvest_adapter_preserves_explicit_recorder_override():
    from autoresearch.common.source_capture import record_source_response, use_source_recorder
    from autoresearch.trace.source_receipts import capture_active_responses

    seen = []
    @capture_active_responses
    def operation():
        record_source_response(outcome="synthetic")
    with use_source_recorder(lambda **kw: seen.append(kw)):
        operation()
    assert seen == [{"outcome": "synthetic"}]

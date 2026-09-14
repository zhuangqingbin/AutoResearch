from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from autoresearch.data.contracts import DataContractError


def _handle(tmp_path):
    capsule = tmp_path / "capsule"
    capsule.mkdir()
    return SimpleNamespace(
        capsule=capsule,
        engine="codex",
        run_id="20260914T120000000000Z",
    )


def _context(handle):
    return {
        "engine": handle.engine,
        "run_id": handle.run_id,
        "task_id": "stock.harvest",
        "attempt": 1,
        "provider": "tushare",
        "endpoint": "moneyflow",
        "normalized_params": {"trade_date": "20260912"},
        "started_at": "2026-09-14T12:00:00Z",
        "ended_at": "2026-09-14T12:00:01Z",
        "as_of": "2026-09-12",
        "available_at": "2026-09-14T11:59:00Z",
        "consumer_refs": [
            {
                "task_id": "stock.harvest",
                "attempt": 1,
                "artifact_id": "stock.context",
                "consumption_kind": "INPUT",
            }
        ],
    }


def test_replay_preserves_failure_then_success_for_same_source_key(tmp_path):
    from autoresearch.trace.source_receipts import record_response, replay_response

    handle = _handle(tmp_path)
    context = _context(handle)
    first = record_response(
        handle,
        context,
        DataContractError("A-level endpoint returned empty"),
    )
    expected = {"rows": 2, "codes": ["600000", "600519"]}
    second = record_response(handle, context, expected)

    assert [first["occurrence"], second["occurrence"]] == [1, 2]
    with pytest.raises(DataContractError, match="A-level endpoint"):
        replay_response(handle.capsule, first["receipt_id"])
    assert replay_response(handle.capsule, second["receipt_id"]) == expected


@pytest.mark.parametrize(
    ("value", "expected_type"),
    [
        (pd.DataFrame({"code": ["600000"], "value": [1.5]}), pd.DataFrame),
        ({"ok": True, "values": [1, 2]}, dict),
        ("source text", str),
        (b"source bytes", bytes),
    ],
)
def test_allowed_source_codecs_round_trip_without_pickle(
    tmp_path, value, expected_type
):
    from autoresearch.trace.source_receipts import record_response, replay_response

    handle = _handle(tmp_path)
    receipt = record_response(handle, _context(handle), value)
    restored = replay_response(handle.capsule, receipt["receipt_id"])

    assert isinstance(restored, expected_type)
    if isinstance(value, pd.DataFrame):
        pd.testing.assert_frame_equal(restored, value)
    else:
        assert restored == value


def test_successful_source_receipt_with_a_missing_payload_cannot_replay(tmp_path):
    from autoresearch.trace.blobs import blob_path
    from autoresearch.trace.source_receipts import (
        SourcePayloadMissing,
        record_response,
        replay_response,
    )

    handle = _handle(tmp_path)
    receipt = record_response(handle, _context(handle), {"rows": 2})
    blob_path(handle.capsule, receipt["payload_hash"]).unlink()

    with pytest.raises(SourcePayloadMissing, match="payload"):
        replay_response(handle.capsule, receipt["receipt_id"])


def test_context_clock_is_scoped_and_does_not_patch_global_datetime():
    from autoresearch.common.execution_context import (
        ExecutionContext,
        RunClock,
        current_execution_context,
        use_execution_context,
    )

    stamp = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
    context = ExecutionContext(
        engine="codex",
        run_id="20260914T120000000000Z",
        task_id="stock.harvest",
        attempt=1,
        clock=RunClock(stamp),
    )
    assert current_execution_context() is None
    with use_execution_context(context):
        assert current_execution_context() is context
        assert context.clock.now() == stamp
    assert current_execution_context() is None

"""Idempotent post-run consumer receipts and selective replay."""
from __future__ import annotations

import json

import pytest

from autoresearch.scan.outbox import OutboxEvent, emit_events
from autoresearch.scan.post_run import (
    consumer_status,
    initialize_consumer_state,
    load_consumer_receipts,
    run_consumers,
)


def _scan_with_run_event(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    event = OutboxEvent.build(
        event_type="RUN_FINALIZED",
        analysis_date=scan.name,
        run_id="run-1",
        contract_hash=None,
        aggregate_id="run-1",
        payload={"n_buys": 0, "n_decisions": 1},
        created_at="2026-07-28T10:00:00Z",
    )
    emit_events(scan, [event])
    return scan, event


def test_initialize_consumer_state_is_atomic_and_idempotent(tmp_path):
    scan, _ = _scan_with_run_event(tmp_path)
    path = initialize_consumer_state(scan)
    before = path.read_bytes()
    assert initialize_consumer_state(scan).read_bytes() == before
    assert load_consumer_receipts(path) == {}
    assert not path.with_name("consumer_state.json.tmp").exists()


def test_successful_consumer_is_not_called_twice(tmp_path):
    scan, event = _scan_with_run_event(tmp_path)
    calls = []

    def handler(received, _scan):
        calls.append(received.event_id)

    first = run_consumers(scan, registry={"journal": handler})
    second = run_consumers(scan, registry={"journal": handler})
    assert first.succeeded == 1 and first.failed == 0
    assert second.skipped == 1 and second.succeeded == 0
    assert calls == [event.event_id]


def test_failed_consumer_can_be_retried_alone(tmp_path):
    scan, _ = _scan_with_run_event(tmp_path)

    def failing(_event, _scan):
        raise RuntimeError("boom")

    calls = []

    def succeeding(event, _scan):
        calls.append(event.event_id)

    first = run_consumers(scan, registry={"journal": failing})
    second = run_consumers(
        scan,
        registry={"journal": succeeding},
        only={"journal"},
        retry_failed=True,
    )
    assert first.failed == 1
    assert second.succeeded == 1
    assert calls
    receipt = next(iter(load_consumer_receipts(
        scan / "outbox" / "consumer_state.json"
    ).values()))
    assert receipt.status == "SUCCEEDED"
    assert receipt.attempts == 2


def test_one_consumer_failure_does_not_block_another(tmp_path):
    scan, _ = _scan_with_run_event(tmp_path)

    def failing(_event, _scan):
        raise RuntimeError("boom")

    calls = []

    def succeeding(event, _scan):
        calls.append(event.event_id)

    result = run_consumers(
        scan,
        registry={"journal": failing, "buy_ledger": succeeding},
    )
    assert result.failed == 1 and result.succeeded == 1
    assert len(calls) == 1
    status = consumer_status(
        scan,
        registry={"journal": failing, "buy_ledger": succeeding},
    )
    assert status["status"] == "BACKLOG"
    assert status["failed_consumers"] == ["journal"]
    assert status["pending"] == 0


def test_only_filter_leaves_other_expected_consumer_pending(tmp_path):
    scan, _ = _scan_with_run_event(tmp_path)
    registry = {
        "journal": lambda _event, _scan: None,
        "buy_ledger": lambda _event, _scan: None,
    }
    run_consumers(scan, registry=registry, only={"journal"})
    status = consumer_status(scan, registry=registry)
    assert status["status"] == "BACKLOG"
    assert status["pending"] == 1
    assert status["pending_consumers"] == ["buy_ledger"]


def test_pending_counts_event_consumer_pairs_not_unique_names(tmp_path):
    scan, _ = _scan_with_run_event(tmp_path)
    dossier_events = [
        OutboxEvent.build(
            event_type="DOSSIER_DELTA_READY",
            analysis_date=scan.name,
            run_id="run-1",
            contract_hash=None,
            aggregate_id=code,
            payload={"code": code, "rating": "Hold", "conviction": "50"},
            created_at="2026-07-28T10:00:00Z",
        )
        for code in ("000001", "000002", "000003")
    ]
    emit_events(scan, dossier_events)
    status = consumer_status(
        scan,
        registry={"dossier_delta": lambda _event, _scan: None},
    )
    assert status["pending"] == 3
    assert status["pending_consumers"] == ["dossier_delta"]


def test_load_rejects_tampered_receipt(tmp_path):
    scan, _ = _scan_with_run_event(tmp_path)
    run_consumers(
        scan,
        registry={"journal": lambda _event, _scan: None},
    )
    path = scan / "outbox" / "consumer_state.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["receipts"][0]["status"] = "FAILED"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_consumer_receipts(path)


def test_status_cli_prints_deterministic_backlog_json(tmp_path, capsys):
    from autoresearch.scan.post_run import main

    scan, _ = _scan_with_run_event(tmp_path)
    assert main([str(scan), "status"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BACKLOG"
    assert result["pending"] == 8


def test_status_cli_reports_corrupt_control_file(tmp_path, capsys):
    from autoresearch.scan.post_run import main

    scan, _ = _scan_with_run_event(tmp_path)
    path = scan / "outbox" / "events.json"
    path.write_text("{", encoding="utf-8")
    assert main([str(scan), "status"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "INVALID"
    assert "JSONDecodeError" in result["error"]

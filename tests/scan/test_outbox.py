"""Stable local post-run events and atomic outbox persistence."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoresearch.scan.decision_record import DecisionRecord, write_decision_records
from autoresearch.scan.outbox import (
    OutboxEvent,
    build_finalization_events,
    build_retro_finalized_event,
    emit_events,
    load_events,
)
from autoresearch.scan.run_contract import RunContract, write_run_contract
from autoresearch.scan.stage_result import record_stage_result


def _event(created_at="2026-07-28T10:00:00Z", aggregate_id="run-1"):
    return OutboxEvent.build(
        event_type="RUN_FINALIZED",
        analysis_date="2026-07-28",
        run_id="run-1",
        contract_hash="a" * 64,
        aggregate_id=aggregate_id,
        payload={"n_decisions": 2},
        created_at=created_at,
    )


def _finalized_scan(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    contract = RunContract.build(
        analysis_date=scan.name,
        user_config={},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"},
        stage_budgets={},
        artifact_schema_versions={},
        git_sha="abc",
        now=datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc),
    )
    write_run_contract(scan / "run_contract.json", contract)
    records = [
        DecisionRecord.build(
            analysis_date=scan.name,
            contract_hash=contract.contract_hash,
            code="000001",
            source_rating="Overweight",
            rubric_rating="Overweight",
            gate_states={"主力真在": "PASS"},
            early_stop=None,
            ensemble_ratings=[],
            final_rating="Overweight",
            proposal="BUY",
            reason="qualified",
            evidence_refs=[],
            first_rejection_stage=None,
        ),
        DecisionRecord.build(
            analysis_date=scan.name,
            contract_hash=contract.contract_hash,
            code="000002",
            source_rating="Hold",
            rubric_rating="Hold",
            gate_states={"主力真在": "UNKNOWN"},
            early_stop={"phase": "P2", "reason": "数据不足"},
            ensemble_ratings=[],
            final_rating="Hold",
            proposal="HOLD",
            reason="early_stop:P2:数据不足",
            evidence_refs=[],
            first_rejection_stage="L4_P2_EARLY_STOP",
        ),
    ]
    write_decision_records(scan, records)
    (scan / "finalists.csv").write_text(
        "code,conviction\n000001,80\n000002,50\n",
        encoding="utf-8",
    )
    record_stage_result(
        scan,
        stage="gate4",
        status="FAILED",
        artifacts=["gate_fires"],
        metrics={},
        warnings=[],
        error="coverage",
    )
    return scan


def test_event_id_is_semantic_and_stable():
    left = _event("2026-07-28T10:00:00Z")
    right = _event("2026-07-28T11:00:00Z")
    assert left.event_id == right.event_id
    assert left.event_hash != right.event_hash


def test_event_rejects_unknown_type():
    with pytest.raises(ValueError, match="event_type"):
        OutboxEvent.build(
            event_type="UNKNOWN",
            analysis_date="2026-07-28",
            run_id=None,
            contract_hash=None,
            aggregate_id="x",
            payload={},
            created_at="2026-07-28T10:00:00Z",
        )


def test_emit_is_atomic_sorted_and_idempotent(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    later = _event(aggregate_id="run-2")
    earlier = _event(aggregate_id="run-1")
    path = emit_events(scan, [later, earlier])
    before = path.read_bytes()
    emit_events(
        scan,
        [
            _event("2026-07-28T12:00:00Z", aggregate_id="run-1"),
            later,
        ],
    )
    assert path.read_bytes() == before
    assert [event.aggregate_id for event in load_events(path)] == [
        "run-1",
        "run-2",
    ]
    assert not path.with_name("events.json.tmp").exists()


def test_load_rejects_tampered_payload(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    path = emit_events(scan, [_event()])
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["events"][0]["payload"]["n_decisions"] = 99
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_events(path)


def test_build_finalization_events_from_control_facts(tmp_path):
    scan = _finalized_scan(tmp_path)
    events = build_finalization_events(scan)
    types = [event.event_type for event in events]
    assert types.count("RUN_FINALIZED") == 1
    assert types.count("DECISION_FINALIZED") == 2
    assert types.count("EARLY_STOPPED") == 1
    assert types.count("DOSSIER_DELTA_READY") == 2
    assert types.count("GATE_FAILED") == 1
    run = next(event for event in events if event.event_type == "RUN_FINALIZED")
    assert run.payload == {"n_buys": 1, "n_decisions": 2}
    assert run.run_id and run.contract_hash


def test_emitted_finalization_events_are_semantically_idempotent(tmp_path):
    scan = _finalized_scan(tmp_path)
    first = emit_events(scan, build_finalization_events(scan)).read_bytes()
    second = emit_events(scan, build_finalization_events(scan)).read_bytes()
    assert second == first


def test_retro_finalized_event_carries_stable_fact_hashes(tmp_path):
    scan = _finalized_scan(tmp_path)
    (scan / "retro").mkdir()
    attribution = scan / "retro" / "attribution.csv"
    rejection = scan / "retro" / "rejection_attribution.csv"
    attribution.write_text("code,fwd_2_oc\n000001,0.1\n", encoding="utf-8")
    rejection.write_text(
        "code,first_rejection_stage\n000001,BOUGHT\n",
        encoding="utf-8",
    )

    event = build_retro_finalized_event(scan)
    again = build_retro_finalized_event(scan)

    assert event.event_type == "RETRO_FINALIZED"
    assert event.event_id == again.event_id
    assert len(event.payload["attribution_hash"]) == 64
    assert len(event.payload["rejection_attribution_hash"]) == 64
    attribution.write_text("code,fwd_2_oc\n000001,0.2\n", encoding="utf-8")
    assert build_retro_finalized_event(scan).event_id != event.event_id

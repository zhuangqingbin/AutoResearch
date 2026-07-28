"""DecisionRecord domain facts, hashes, and atomic book persistence."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoresearch.scan.decision_record import (
    DecisionRecord,
    load_decision_records,
    write_decision_records,
)
from autoresearch.scan.run_contract import RunContract, write_run_contract

NOW = datetime(2026, 7, 28, 17, 0, tzinfo=timezone.utc)


def _record(code="000001", final="Hold", contract_hash=None):
    return DecisionRecord.build(
        analysis_date="2026-07-28",
        contract_hash=contract_hash,
        code=code,
        source_rating="Hold",
        rubric_rating="Hold",
        gate_states={
            "主力真在": "PASS",
            "业绩真兑现": "FAIL",
            "估值不透支": "PASS",
        },
        early_stop=None,
        ensemble_ratings=[],
        final_rating=final,
        proposal="HOLD",
        reason="rubric:业绩真兑现",
        evidence_refs=[f"finalists.csv#{code}", f"details/{code}.md"],
        first_rejection_stage="L4_RUBRIC",
    )


def _contract(scan):
    contract = RunContract.build(
        analysis_date=scan.name,
        user_config={},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"},
        stage_budgets={},
        artifact_schema_versions={},
        git_sha="abc",
        now=NOW,
    )
    write_run_contract(scan / "run_contract.json", contract)
    return contract


def test_record_round_trip_and_hash(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    contract = _contract(scan)
    record = _record(contract_hash=contract.contract_hash)
    path = write_decision_records(scan, [record])
    loaded = load_decision_records(path)
    assert path == scan / "decision_records.json"
    assert loaded["000001"].to_dict() == record.to_dict()
    assert len(record.record_hash) == 64


def test_book_is_sorted_and_semantically_stable(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    left = write_decision_records(scan, [_record("600000"), _record("000001")])
    before = left.read_bytes()
    right = write_decision_records(scan, [_record("000001"), _record("600000")])
    assert right.read_bytes() == before
    raw = json.loads(right.read_text(encoding="utf-8"))
    assert [row["code"] for row in raw["records"]] == ["000001", "600000"]


def test_load_rejects_tampered_record(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    path = write_decision_records(scan, [_record()])
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["records"][0]["final_rating"] = "Buy"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_decision_records(path)


def test_write_rejects_contract_mismatch(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    contract = _contract(scan)
    assert contract.contract_hash != "a" * 64
    with pytest.raises(ValueError, match="contract_hash"):
        write_decision_records(scan, [_record(contract_hash="a" * 64)])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", "../1"),
        ("final_rating", "Strong Buy"),
        ("proposal", "WAIT"),
        ("gate_states", {"主力真在": "YES"}),
    ],
)
def test_record_rejects_invalid_domain_values(field, value):
    kwargs = {
        "analysis_date": "2026-07-28",
        "contract_hash": None,
        "code": "000001",
        "source_rating": "Hold",
        "rubric_rating": "Hold",
        "gate_states": {},
        "early_stop": None,
        "ensemble_ratings": [],
        "final_rating": "Hold",
        "proposal": "HOLD",
        "reason": "x",
        "evidence_refs": [],
        "first_rejection_stage": "L4_RUBRIC",
    }
    kwargs[field] = value
    with pytest.raises(ValueError):
        DecisionRecord.build(**kwargs)

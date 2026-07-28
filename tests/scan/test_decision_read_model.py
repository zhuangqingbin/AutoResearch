"""DecisionRecord-first rating reads with explicit historical fallbacks."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoresearch.scan.decision_read_model import read_final_ratings
from autoresearch.scan.decision_record import DecisionRecord, write_decision_records
from autoresearch.scan.run_contract import RunContract, write_run_contract


def _scan_with_decision(tmp_path, final_rating="Hold"):
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
    record = DecisionRecord.build(
        analysis_date=scan.name,
        contract_hash=contract.contract_hash,
        code="000001",
        source_rating="Hold",
        rubric_rating="Hold",
        gate_states={},
        early_stop=None,
        ensemble_ratings=[],
        final_rating=final_rating,
        proposal="HOLD",
        reason="rubric:Hold",
        evidence_refs=[],
        first_rejection_stage="L4_RUBRIC",
    )
    write_decision_records(scan, [record])
    return scan


def test_valid_decision_book_wins_over_legacy_file(tmp_path):
    scan = _scan_with_decision(tmp_path, final_rating="Hold")
    (scan / "_final_ratings.json").write_text(
        '{"000001":"Overweight"}',
        encoding="utf-8",
    )
    assert read_final_ratings(scan) == {"000001": "Hold"}


def test_missing_decision_book_falls_back_to_legacy_json(tmp_path):
    (tmp_path / "_final_ratings.json").write_text(
        '{"1":"Overweight"}',
        encoding="utf-8",
    )
    assert read_final_ratings(tmp_path) == {"000001": "Overweight"}


def test_invalid_decision_book_is_loud_by_default(tmp_path):
    scan = _scan_with_decision(tmp_path)
    path = scan / "decision_records.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["records"][0]["final_rating"] = "Buy"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="decision book"):
        read_final_ratings(scan)


@pytest.mark.parametrize("legacy_text", ["{}", "{broken"])
def test_empty_or_malformed_legacy_uses_explicit_card_fallback(
    tmp_path, legacy_text,
):
    (tmp_path / "_final_ratings.json").write_text(
        legacy_text,
        encoding="utf-8",
    )
    assert read_final_ratings(
        tmp_path,
        card_fallback=lambda _: {"000001": "Sell"},
    ) == {"000001": "Sell"}


def test_no_facts_and_no_fallback_returns_empty(tmp_path):
    assert read_final_ratings(tmp_path) == {}

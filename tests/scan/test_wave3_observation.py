"""成本/时延观测接线：缺失不冒充 $0，预算不反向影响决策。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.scan.artifacts import build_artifact_index
from autoresearch.scan.decision_record import DecisionRecord, write_decision_records
from autoresearch.scan.post_run import publish_run_observation

DATE = "2026-07-28"


def _record(code: str, *, buy: bool) -> DecisionRecord:
    rating = "Overweight" if buy else "Hold"
    return DecisionRecord.build(
        analysis_date=DATE,
        contract_hash=None,
        code=code,
        source_rating=rating,
        rubric_rating=rating,
        gate_states={
            "主力真在": "PASS" if buy else "FAIL",
            "盈利趋势": "PASS",
            "估值有垫": "PASS",
        },
        early_stop=None,
        ensemble_ratings=[],
        final_rating=rating,
        proposal="BUY" if buy else "HOLD",
        reason="fixture",
        evidence_refs=[f"details/{code}.md"],
        first_rejection_stage=None if buy else "L4_RUBRIC",
    )


def _scan(tmp_path: Path) -> Path:
    scan = tmp_path / "context" / "scan" / DATE
    scan.mkdir(parents=True)
    write_decision_records(
        scan,
        [_record("000001", buy=True), _record("000002", buy=False)],
    )
    (scan / "finalists.csv").write_text(
        "code,name\n000001,甲\n000002,乙\n",
        encoding="utf-8",
    )
    (scan / "_final_ratings.json").write_text(
        json.dumps({"000001": "Overweight", "000002": "Hold"}),
        encoding="utf-8",
    )
    (scan / "_stage_timing.json").write_text(
        json.dumps({"总计": {"wall_s": 4200}, "L3精排": {"wall_s": 700}}),
        encoding="utf-8",
    )
    retro = scan / "retro"
    retro.mkdir()
    pd.DataFrame([
        {
            "code": "000001",
            "first_rejection_stage": "BOUGHT",
            "mature": True,
            "buyable": True,
            "excess_2": 0.01,
        },
        {
            "code": "000002",
            "first_rejection_stage": "L4_RUBRIC",
            "mature": True,
            "buyable": True,
            "excess_2": -0.03,
        },
    ]).to_csv(retro / "rejection_attribution.csv", index=False)
    return scan


def _usage(scan: Path, *, cost: float = 12.0, cache: float = 0.9) -> Path:
    path = scan / "_token_usage.json"
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "cache_hit_rate": cache,
            "totals": {"transcripts": 3, "estimated_usd": cost},
            "rows": [
                {"agent": "l4-card", "estimated_usd": cost},
            ],
        }),
        encoding="utf-8",
    )
    return path


def test_available_cost_and_timing_publish_observation_and_denominators(tmp_path):
    scan = _scan(tmp_path)
    _usage(scan)

    got = publish_run_observation(scan, real_scan=False)
    index = build_artifact_index(scan)
    artifacts = {row["name"]: row for row in index["artifacts"]}

    assert got["measurement_status"] == "MEASURED"
    assert got["maturity"]["status"] == "IMMATURE"
    assert got["effectiveness"]["denominators"] == {
        "mature_decision_records": 2,
        "final_buy_candidates": 1,
        "verified_correct_rejections": 1,
    }
    assert got["effectiveness"]["usd_per_mature_decision_record"] == 6.0
    assert got["effectiveness"]["usd_per_final_buy_candidate"] == 12.0
    assert got["effectiveness"]["usd_per_verified_correct_rejection"] == 12.0
    assert "$12.0000" in got["markdown"]
    assert artifacts["budget_observation"]["status"] == "PRESENT"


def test_missing_usage_is_explicitly_unmeasured_never_zero_cost(tmp_path):
    scan = _scan(tmp_path)

    got = publish_run_observation(scan, real_scan=False)

    assert got["measurement_status"] == "UNMEASURED"
    assert got["estimated_usd"] is None
    assert "成本 JSON 未计量" in got["warnings"]
    assert "UNMEASURED" in got["markdown"]
    assert "$0" not in got["markdown"]


def test_budget_degradation_does_not_mutate_decision_or_rating_artifacts(tmp_path):
    scan = _scan(tmp_path)
    _usage(scan, cost=99.0, cache=0.4)
    watched = [
        scan / "decision_records.json",
        scan / "finalists.csv",
        scan / "_final_ratings.json",
    ]
    before = {path: path.read_bytes() for path in watched}

    got = publish_run_observation(
        scan,
        budgets={
            "cache_hit_min": 0.9,
            "stage_cost_usd": {"l4-card": 10},
        },
        real_scan=False,
    )

    assert got["status"] == "DEGRADED"
    assert got["truncated"] is False
    assert {path: path.read_bytes() for path in watched} == before


def test_post_run_replaces_managed_report_section_after_usage_arrives(tmp_path):
    scan = _scan(tmp_path)
    report = tmp_path / "reports" / "scan" / "run-1"
    report.mkdir(parents=True)
    summary = report / "summary.md"
    summary.write_text("# report\n\nbody\n", encoding="utf-8")

    first = publish_run_observation(scan, report_dir=report, real_scan=False)
    assert first["measurement_status"] == "UNMEASURED"
    _usage(scan, cost=7.0)
    second = publish_run_observation(scan, report_dir=report, real_scan=False)
    text = summary.read_text(encoding="utf-8")

    assert second["measurement_status"] == "MEASURED"
    assert text.count("<!-- run-observation:start -->") == 1
    assert "$7.0000" in text
    assert "UNMEASURED" not in text

"""成本/时延观测接线：缺失不冒充 $0，预算不反向影响决策。"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pandas as pd

from autoresearch.scan.artifacts import build_artifact_index
from autoresearch.scan.decision_record import DecisionRecord, write_decision_records
from autoresearch.scan.post_run import (
    OBSERVATION_END,
    OBSERVATION_START,
    publish_run_observation,
    refresh_run_observation,
)
from autoresearch.scan.report_model import RUN_OBSERVATION_DETAIL_MARKERS
from autoresearch.scan.retention import read_manifest

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


def _usage(
    scan: Path,
    *,
    cost: float = 12.0,
    cache: float = 0.9,
    weighted: int = 4_000_000,
) -> Path:
    path = scan / "_token_usage.json"
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "cache_hit_rate": cache,
            "totals": {
                "transcripts": 3,
                "estimated_usd": cost,
                "weighted_input_proxy": weighted,
            },
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
    # 2026-08-21 learning 层退役:`mature_decision_records` / `verified_correct_rejections`
    # 两个分母算自 `retro/rejection_attribution.csv`(已无生产者),随之删除。
    assert got["effectiveness"]["denominators"] == {"final_buy_candidates": 1}
    assert got["effectiveness"]["usd_per_final_buy_candidate"] == 12.0
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


def _report_bundle(report: Path) -> tuple[Path, Path]:
    report.mkdir(parents=True)
    summary = report / "summary.md"
    appendix = report / "appendix.md"
    summary.write_text(
        f"# report\n\n{OBSERVATION_START}\nold summary\n{OBSERVATION_END}\n",
        encoding="utf-8",
    )
    detail_start, detail_end = RUN_OBSERVATION_DETAIL_MARKERS
    appendix.write_text(
        f"# appendix\n\n{detail_start}\nold detail\n{detail_end}\n",
        encoding="utf-8",
    )
    return summary, appendix


def test_post_run_replaces_managed_report_section_after_usage_arrives(tmp_path):
    scan = _scan(tmp_path)
    report = tmp_path / "reports" / "scan" / "run-1"
    summary, appendix = _report_bundle(report)

    first = publish_run_observation(scan, report_dir=report, real_scan=False)
    assert first["measurement_status"] == "UNMEASURED"
    _usage(scan, cost=7.0)
    second = publish_run_observation(scan, report_dir=report, real_scan=False)
    text = summary.read_text(encoding="utf-8")

    assert second["measurement_status"] == "MEASURED"
    assert text.count("<!-- run-observation:start -->") == 1
    assert "$7.0000" in text
    assert "UNMEASURED" not in text
    assert "加权输入 4000000" in text
    assert "预算带:GREEN" in text
    assert "加权输入:4000000" in appendix.read_text(encoding="utf-8")


def test_unmeasured_rendering_is_dash_and_red(tmp_path):
    scan = _scan(tmp_path)
    report = tmp_path / "reports" / "scan" / "run-unmeasured"
    summary, appendix = _report_bundle(report)

    got = publish_run_observation(scan, report_dir=report, real_scan=False)

    assert got["weighted_input_proxy"] is None
    assert got["budget_band"] == "RED"
    assert "加权输入 —" in summary.read_text(encoding="utf-8")
    assert "预算带:RED" in summary.read_text(encoding="utf-8")
    assert "加权输入:—" in appendix.read_text(encoding="utf-8")


def test_markerless_report_is_unchanged_and_degrades_observation(tmp_path):
    scan = _scan(tmp_path)
    _usage(scan)
    report = tmp_path / "reports" / "scan" / "legacy"
    report.mkdir(parents=True)
    summary = report / "summary.md"
    summary.write_text("# legacy\n\nbody\n", encoding="utf-8")
    before = summary.read_bytes()

    got = publish_run_observation(scan, report_dir=report, real_scan=False)

    assert summary.read_bytes() == before
    assert got["status"] == "DEGRADED"
    assert any("summary.md" in warning and "managed 标记" in warning
               for warning in got["warnings"])
    persisted = json.loads((scan / "_budget_observation.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "DEGRADED"
    assert persisted["warnings"] == got["warnings"]
    stage = json.loads((scan / "stage_results" / "budget.json").read_text(encoding="utf-8"))
    assert stage["status"] == "DEGRADED"
    assert stage["metrics"]["truncated"] is False
    assert stage["warnings"] == got["warnings"]
    # 账本口径锁死:发布线写的是「最终态全量」,比 budget.observe_run 的自持久化多三项。
    # 少一项都是静默丢账(不是「测出来是零」),多一项则是没登记就上桌——两个方向都要红。
    assert set(stage["metrics"]) == {
        "truncated", "measurement_status", "weighted_input_proxy", "budget_band",
        "estimated_usd", "interactive_wall_s", "cache_hit_rate",
        "maturity_status", "denominators",
        # 2026-10-03 A3/A5/A6:跨 run 身份漂移、相对预算带、行为指纹(三个只读标签,不拥有门)。
        "identity_drift", "relative_band", "behavior",
    }
    assert stage["metrics"]["measurement_status"] == got["measurement_status"]
    assert stage["metrics"]["maturity_status"] == got["maturity"]["status"]
    assert stage["metrics"]["denominators"] == got["effectiveness"]["denominators"]


def test_refresh_rejects_malformed_marker_pairs_without_mutating_text():
    observation = {
        "measurement_status": "MEASURED",
        "status": "SUCCEEDED",
        "weighted_input_proxy": 1,
        "budget_band": "GREEN",
        "warnings": [],
    }
    malformed = [
        f"prefix\n{OBSERVATION_START}\nbody\n",
        f"prefix\n{OBSERVATION_END}\nbody\n",
        f"{OBSERVATION_END}\nbody\n{OBSERVATION_START}\n",
        f"{OBSERVATION_START}\na\n{OBSERVATION_START}\nb\n{OBSERVATION_END}\n",
        f"{OBSERVATION_START}\na\n{OBSERVATION_END}\nb\n{OBSERVATION_END}\n",
    ]

    for text in malformed:
        candidate = {**observation, "warnings": []}
        got, _, warnings = refresh_run_observation(text, None, candidate)
        assert got == text
        assert warnings
        assert candidate["status"] == "DEGRADED"
        assert any("managed 标记" in warning for warning in candidate["warnings"])


def test_successful_refresh_updates_hashes_and_is_byte_idempotent(tmp_path):
    scan = _scan(tmp_path)
    _usage(scan, weighted=6_000_000)
    from autoresearch.scan.health import write_run_health

    write_run_health(scan)
    report = tmp_path / "reports" / "scan" / "run-hashes"
    summary, appendix = _report_bundle(report)

    first = publish_run_observation(scan, report_dir=report, real_scan=False)
    watched = [summary, appendix, scan / "artifact_index.json",
               scan / "_budget_observation.json",
               scan / "_report_budget.json",
               scan / "run_health.json",
               report / "trace" / "artifact_index.json",
               report / "trace" / "_budget_observation.json",
               report / "trace" / "MANIFEST.sha256"]
    before = {path: path.read_bytes() for path in watched}
    mtimes = {path: path.stat().st_mtime_ns for path in watched}
    index = json.loads((scan / "artifact_index.json").read_text(encoding="utf-8"))
    rows = {row["name"]: row for row in index["artifacts"]}
    assert rows["summary"]["content_hash"] == hashlib.sha256(summary.read_bytes()).hexdigest()
    assert (report / "trace" / "artifact_index.json").read_bytes() == before[scan / "artifact_index.json"]
    assert (report / "trace" / "_budget_observation.json").read_bytes() == (
        scan / "_budget_observation.json").read_bytes()
    manifest = read_manifest(report)
    assert manifest["summary.md"] == hashlib.sha256(summary.read_bytes()).hexdigest()
    assert manifest["appendix.md"] == hashlib.sha256(appendix.read_bytes()).hexdigest()
    assert first["budget_band"] == "YELLOW"

    time.sleep(1.05)  # 跨过 artifact index 的秒级 generated_at，证明不是时钟碰巧相同。
    second = publish_run_observation(scan, report_dir=report, real_scan=False)

    assert second == first
    before_index = json.loads(before[scan / "artifact_index.json"])
    after_index = json.loads((scan / "artifact_index.json").read_text(encoding="utf-8"))
    assert after_index["artifacts"] == before_index["artifacts"]
    after_manifest = read_manifest(report)
    assert after_manifest == manifest
    assert [path for path in watched if path.read_bytes() != before[path]] == []
    assert [path for path in watched if path.stat().st_mtime_ns != mtimes[path]] == []

    _usage(scan, cost=8.0, weighted=4_000_000)
    changed = publish_run_observation(scan, report_dir=report, real_scan=False)
    changed_index = json.loads((scan / "artifact_index.json").read_text(encoding="utf-8"))
    changed_rows = {row["name"]: row for row in changed_index["artifacts"]}
    assert changed["budget_band"] == "GREEN"
    assert summary.read_bytes() != before[summary]
    assert changed_rows["summary"]["content_hash"] == hashlib.sha256(
        summary.read_bytes()).hexdigest()
    assert (report / "trace" / "artifact_index.json").read_bytes() == (
        scan / "artifact_index.json").read_bytes()
    assert read_manifest(report)["summary.md"] == hashlib.sha256(
        summary.read_bytes()).hexdigest()

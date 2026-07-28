"""Wave 3 成本/墙钟预算：只观测，不截断研究。"""
from __future__ import annotations

import json

from autoresearch.scan.budget import (
    DEFAULT_BUDGETS,
    evaluate_history,
    normalize_budgets,
    observe_run,
)
from autoresearch.scan.stage_result import load_stage_result


def _usage(cost=80.0, cache=0.9, rows=None):
    return {
        "schema_version": 1,
        "cache_hit_rate": cache,
        "totals": {"estimated_usd": cost},
        "rows": rows or [],
    }


def _timing(total=4_200, l3=800):
    return {
        "总计": {"wall_s": total},
        "L3精排": {"wall_s": l3},
    }


def _observation(i, *, cost=80.0, wall=4_200, cache=0.9):
    return {
        "schema_version": 1,
        "run_id": "20260727_2140" if i == 0 else f"run-{i}",
        "analysis_date": f"2026-07-{i + 1:02d}",
        "real_scan": True,
        "estimated_usd": 100.0 if i == 0 else cost,
        "interactive_wall_s": wall,
        "cache_hit_rate": cache,
    }


def test_normalize_budgets_keeps_explicit_limits_and_defaults():
    got = normalize_budgets({
        "cache_hit_min": 0.9,
        "stage_wall_seconds": {"L3精排": 900},
    })
    assert got["cache_hit_min"] == 0.9
    assert got["stage_wall_seconds"] == {"L3精排": 900}
    assert got["min_real_scans"] == 10
    assert got["baseline_run"] == "20260727_2140"
    assert got["concurrency"] == DEFAULT_BUDGETS["concurrency"]


def test_observe_run_warns_and_degrades_without_truncation(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    budgets = normalize_budgets({
        "cache_hit_min": 0.85,
        "stage_cost_usd": {"l4-card": 10},
        "stage_wall_seconds": {"L3精排": 600},
    })
    usage = _usage(
        cost=90,
        cache=0.8,
        rows=[{"agent": "l4-card", "estimated_usd": 12.0}],
    )

    got = observe_run(
        scan,
        usage,
        _timing(l3=700),
        budgets=budgets,
        run_id="run-over",
        real_scan=True,
    )

    assert got["status"] == "DEGRADED"
    assert got["truncated"] is False
    assert len(got["warnings"]) == 3
    assert (scan / "_budget_observation.json").exists()
    stage = load_stage_result(scan / "stage_results" / "budget.json")
    assert stage.status == "DEGRADED"
    assert stage.metrics["truncated"] is False


def test_observe_run_succeeds_inside_budget(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    got = observe_run(
        scan,
        _usage(),
        _timing(),
        budgets=normalize_budgets({}),
        run_id="run-ok",
        real_scan=True,
    )
    assert got["status"] == "SUCCEEDED"
    assert got["warnings"] == []


def test_history_is_immature_before_ten_distinct_real_scans():
    got = evaluate_history([_observation(i) for i in range(9)])
    assert got["status"] == "IMMATURE"
    assert got["n_real_scans"] == 9


def test_history_uses_median_p50_p90_and_baseline_not_best_run():
    observations = [_observation(0, wall=5_340)]
    observations += [_observation(i, cost=80, wall=4_200) for i in range(1, 10)]

    got = evaluate_history(observations, phase=1)

    assert got["status"] == "PASS"
    assert got["n_real_scans"] == 10
    assert got["median_cost_usd"] == 80
    assert got["cost_reduction"] == 0.2
    assert got["p50_minutes"] == 70
    assert got["p90_minutes"] == 70
    assert got["targets"]["cache"] is True


def test_history_without_priced_baseline_stays_immature():
    observations = [_observation(i) for i in range(1, 11)]
    got = evaluate_history(observations)
    assert got["status"] == "IMMATURE"
    assert "baseline" in got["reason"]


def test_observation_json_is_machine_readable(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    observe_run(
        scan,
        _usage(),
        _timing(),
        budgets=normalize_budgets({}),
        run_id="run-json",
    )
    raw = json.loads((scan / "_budget_observation.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert raw["run_id"] == "run-json"

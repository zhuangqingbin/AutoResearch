"""Wave 3 成本/墙钟预算：只观测，不截断研究。"""
from __future__ import annotations

import json
import math

import pytest

from autoresearch.scan.budget import (
    DEFAULT_BUDGETS,
    evaluate_history,
    normalize_budgets,
    observe_run,
)
from autoresearch.scan.stage_result import load_stage_result


def _usage(cost=80.0, cache=0.9, rows=None, weighted=5_000_000):
    totals = {"estimated_usd": cost}
    if weighted is not None:
        totals["weighted_input_proxy"] = weighted
    return {
        "schema_version": 1,
        "cache_hit_rate": cache,
        "totals": totals,
        "rows": rows or [],
    }


def _timing(total=4_200, l3=800):
    return {
        "总计": {"wall_s": total},
        "L3精排": {"wall_s": l3},
    }


def _observation(i, *, cost=80.0, wall=4_200, cache=0.9, weighted=5_000_000):
    return {
        "schema_version": 1,
        "run_id": "20260727_2140" if i == 0 else f"run-{i}",
        "analysis_date": f"2026-07-{i + 1:02d}",
        "real_scan": True,
        "estimated_usd": 100.0 if i == 0 else cost,
        "interactive_wall_s": wall,
        "cache_hit_rate": cache,
        "weighted_input_proxy": weighted,
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
    assert got["run_weighted_warn"] == 7_000_000
    assert got["run_weighted_target"] == 5_000_000


@pytest.mark.parametrize(
    "raw",
    [
        {"run_weighted_warn": True},
        {"run_weighted_warn": 0},
        {"run_weighted_target": -1},
        {"run_weighted_warn": 5, "run_weighted_target": 6},
    ],
)
def test_normalize_budgets_rejects_invalid_weighted_limits(raw):
    with pytest.raises(ValueError, match="weighted budget"):
        normalize_budgets(raw)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("key", ["run_weighted_warn", "run_weighted_target"])
def test_normalize_budgets_rejects_non_finite_weighted_limits(key, value):
    with pytest.raises(ValueError, match="weighted budget"):
        normalize_budgets({key: value})


@pytest.mark.parametrize(
    ("weighted", "band", "status"),
    [
        (None, "RED", "DEGRADED"),
        (5_000_000, "GREEN", "SUCCEEDED"),
        (5_000_001, "YELLOW", "SUCCEEDED"),
        (7_000_000, "YELLOW", "SUCCEEDED"),
        (7_000_001, "RED", "DEGRADED"),
    ],
)
def test_observe_run_weighted_budget_boundaries(
    tmp_path, weighted, band, status
):
    scan = tmp_path / str(weighted)
    scan.mkdir()

    got = observe_run(scan, _usage(weighted=weighted), _timing())

    assert got["weighted_input_proxy"] == weighted
    assert got["budget_band"] == band
    assert got["status"] == status
    assert got["truncated"] is False
    if band == "YELLOW":
        assert len(got["advisories"]) == 1
        assert not any("weighted_input_proxy" in warning for warning in got["warnings"])
    elif band == "GREEN":
        assert got["advisories"] == []
        assert not any("weighted_input_proxy" in warning for warning in got["warnings"])
    else:
        assert got["advisories"] == []
        assert any("weighted_input_proxy" in warning for warning in got["warnings"])


@pytest.mark.parametrize("weighted", [True, False, -1, math.nan, math.inf, -math.inf])
def test_observe_run_invalid_weighted_value_is_unmeasured(tmp_path, weighted):
    scan = tmp_path / f"invalid-{repr(weighted)}"
    scan.mkdir()

    got = observe_run(scan, _usage(weighted=weighted), _timing())

    assert got["measurement_status"] == "UNMEASURED"
    assert got["weighted_input_proxy"] is None
    assert got["budget_band"] == "RED"
    assert got["status"] == "DEGRADED"
    assert got["truncated"] is False


def test_observe_run_zero_weighted_value_is_valid(tmp_path):
    scan = tmp_path / "zero"
    scan.mkdir()
    got = observe_run(scan, _usage(weighted=0), _timing())
    assert got["measurement_status"] == "MEASURED"
    assert got["weighted_input_proxy"] == 0
    assert got["budget_band"] == "GREEN"
    assert got["status"] == "SUCCEEDED"


def test_observe_run_weighted_proxy_precedes_compatibility_alias(tmp_path):
    scan = tmp_path / "precedence"
    scan.mkdir()
    usage = _usage(weighted=5_000_000)
    usage["totals"]["weighted_in"] = 8_000_000
    got = observe_run(scan, usage, _timing())
    assert got["weighted_input_proxy"] == 5_000_000
    assert got["budget_band"] == "GREEN"


def test_observe_run_falls_back_to_weighted_in(tmp_path):
    scan = tmp_path / "fallback"
    scan.mkdir()
    usage = _usage(weighted=None)
    usage["totals"]["weighted_in"] = 6_000_000
    got = observe_run(scan, usage, _timing())
    assert got["weighted_input_proxy"] == 6_000_000
    assert got["budget_band"] == "YELLOW"
    assert got["status"] == "SUCCEEDED"


def test_yellow_band_does_not_mask_existing_warning(tmp_path):
    scan = tmp_path / "yellow-existing-warning"
    scan.mkdir()
    got = observe_run(scan, _usage(cache=0.8, weighted=6_000_000), _timing())
    assert got["budget_band"] == "YELLOW"
    assert got["status"] == "DEGRADED"
    assert len(got["advisories"]) == 1
    assert any("cache_hit_rate" in warning for warning in got["warnings"])


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
    assert got["weighted_p50"] == 5_000_000
    assert got["weighted_p90"] == 5_000_000
    assert got["targets"]["weighted_p50"] is True
    assert got["targets"]["weighted_p90"] is True


def test_history_weighted_nearest_rank_boundaries_pass():
    weighted = [4_000_000] * 4 + [5_000_000] + [7_000_000] * 5
    observations = [
        _observation(i, cost=80, weighted=value) for i, value in enumerate(weighted)
    ]

    got = evaluate_history(observations)

    assert got["status"] == "PASS"
    assert got["weighted_p50"] == 5_000_000
    assert got["weighted_p90"] == 7_000_000
    assert got["targets"]["weighted_p50"] is True
    assert got["targets"]["weighted_p90"] is True


def test_history_requires_weighted_value_for_every_real_scan():
    observations = [_observation(i) for i in range(10)]
    observations[-1]["weighted_input_proxy"] = None
    got = evaluate_history(observations)
    assert got["status"] == "IMMATURE"
    assert "weighted" in got["reason"]


@pytest.mark.parametrize("weighted", [True, False, -1, math.nan, math.inf, -math.inf])
def test_history_rejects_invalid_weighted_observation(weighted):
    observations = [_observation(i) for i in range(10)]
    observations[-1]["weighted_input_proxy"] = weighted
    got = evaluate_history(observations)
    assert got["status"] == "IMMATURE"
    assert "weighted" in got["reason"]


def test_history_accepts_zero_weighted_observation():
    observations = [_observation(i, weighted=0) for i in range(10)]
    got = evaluate_history(observations)
    assert got["status"] == "PASS"
    assert got["weighted_p50"] == 0
    assert got["weighted_p90"] == 0


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

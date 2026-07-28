#!/usr/bin/env python3
"""scan-market 成本/墙钟预算控制面。

预算只产生事实、warning 和 DEGRADED StageResult，不拥有截断研究的权限。性能晋升至少读取
10 次真实扫描的中位数/P50/P90，不能拿单次最佳 run 代替。
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

from autoresearch.scan.stage_result import safe_record_stage_result

BUDGET_OBSERVATION_SCHEMA_VERSION = 1
DEFAULT_BUDGETS = {
    "cache_hit_min": 0.85,
    "stage_cost_usd": {},
    "stage_wall_seconds": {},
    "concurrency": {
        "tushare": 4,
        "web_search": 4,
        "web_fetch": 4,
        "l4_stock": 4,
    },
    "min_real_scans": 10,
    "baseline_run": "20260727_2140",
}


def normalize_budgets(raw: dict | None) -> dict:
    """用户预算块 + 稳定默认值；输入不被原地修改。"""
    raw = raw or {}
    concurrency = {
        **DEFAULT_BUDGETS["concurrency"],
        **(raw.get("concurrency") or {}),
    }
    return {
        "cache_hit_min": float(
            raw.get("cache_hit_min", DEFAULT_BUDGETS["cache_hit_min"])
        ),
        "stage_cost_usd": {
            str(k): float(v) for k, v in (raw.get("stage_cost_usd") or {}).items()
        },
        "stage_wall_seconds": {
            str(k): int(v) for k, v in (raw.get("stage_wall_seconds") or {}).items()
        },
        "concurrency": {str(k): max(1, int(v)) for k, v in concurrency.items()},
        "min_real_scans": max(
            1, int(raw.get("min_real_scans", DEFAULT_BUDGETS["min_real_scans"]))
        ),
        "baseline_run": str(
            raw.get("baseline_run", DEFAULT_BUDGETS["baseline_run"])
        ),
    }


def _wall_seconds(timing: dict, key: str) -> int | None:
    value = timing.get(key)
    if isinstance(value, dict):
        value = value.get("wall_s")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _atomic_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)
    return path


def observe_run(
    scan_dir: Path | str,
    usage_ledger: dict,
    timing: dict,
    *,
    budgets: dict | None = None,
    run_id: str | None = None,
    real_scan: bool = True,
) -> dict:
    """生成一次观测并双写 budget StageResult；永远 `truncated=False`。"""
    scan = Path(scan_dir)
    policy = normalize_budgets(budgets)
    warnings: list[str] = []
    hit = usage_ledger.get("cache_hit_rate")
    estimated = (usage_ledger.get("totals") or {}).get("estimated_usd")
    measured = usage_ledger.get("schema_version") == 1 and estimated is not None
    total_wall = _wall_seconds(timing, "总计")

    if not measured:
        warnings.append("成本 JSON 未计量")
    if hit is None:
        warnings.append("cache_hit_rate 未计量")
    elif float(hit) < policy["cache_hit_min"]:
        warnings.append(
            f"cache_hit_rate {float(hit):.1%} < {policy['cache_hit_min']:.1%}"
        )
    if estimated is None:
        warnings.append("estimated_usd 未计量")
    if total_wall is None:
        warnings.append("总计 wall_s 未计量")

    cost_by_agent: dict[str, float] = {}
    for row in usage_ledger.get("rows") or []:
        value = row.get("estimated_usd")
        if value is None:
            continue
        name = str(row.get("agent") or "(未标注)")
        cost_by_agent[name] = cost_by_agent.get(name, 0.0) + float(value)
    for stage, cap in policy["stage_cost_usd"].items():
        actual = cost_by_agent.get(stage)
        if actual is not None and actual > cap:
            warnings.append(f"{stage} cost ${actual:.4f} > ${cap:.4f}")

    for stage, cap in policy["stage_wall_seconds"].items():
        actual = _wall_seconds(timing, stage)
        if actual is not None and actual > cap:
            warnings.append(f"{stage} wall {actual}s > {cap}s")

    status = "DEGRADED" if warnings else "SUCCEEDED"
    observation = {
        "schema_version": BUDGET_OBSERVATION_SCHEMA_VERSION,
        "run_id": run_id or scan.name,
        "analysis_date": scan.name,
        "real_scan": bool(real_scan),
        "measurement_status": "MEASURED" if measured else "UNMEASURED",
        "status": status,
        "truncated": False,
        "estimated_usd": None if estimated is None else float(estimated),
        "interactive_wall_s": total_wall,
        "cache_hit_rate": None if hit is None else float(hit),
        "stage_cost_usd": cost_by_agent,
        "stage_wall_seconds": {
            key: _wall_seconds(timing, key) for key in timing
        },
        "budgets": policy,
        "warnings": warnings,
    }
    _atomic_json(scan / "_budget_observation.json", observation)
    safe_record_stage_result(
        scan,
        stage="budget",
        status=status,
        artifacts=["budget_observation"],
        metrics={
            "truncated": False,
            "estimated_usd": observation["estimated_usd"],
            "interactive_wall_s": total_wall,
            "cache_hit_rate": observation["cache_hit_rate"],
        },
        warnings=warnings,
        error=None,
    )
    return observation


def _nearest_rank(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def evaluate_history(
    observations: list[dict],
    *,
    phase: int = 1,
    budgets: dict | None = None,
) -> dict:
    """至少十次真实 run 后计算成本中位数与墙钟 P50/P90。"""
    policy = normalize_budgets(budgets)
    unique: dict[str, dict] = {}
    for row in observations:
        if row.get("real_scan") is not True:
            continue
        run_id = str(row.get("run_id") or "")
        if run_id:
            unique[run_id] = row
    rows = list(unique.values())
    base = {
        "phase": int(phase),
        "n_real_scans": len(rows),
        "required_real_scans": policy["min_real_scans"],
    }
    if len(rows) < policy["min_real_scans"]:
        return {
            **base,
            "status": "IMMATURE",
            "reason": f"real_scans<{policy['min_real_scans']}",
        }
    baseline = unique.get(policy["baseline_run"])
    if baseline is None or baseline.get("estimated_usd") is None:
        return {
            **base,
            "status": "IMMATURE",
            "reason": f"baseline missing/unpriced:{policy['baseline_run']}",
        }
    if any(
        row.get("estimated_usd") is None
        or row.get("interactive_wall_s") is None
        or row.get("cache_hit_rate") is None
        for row in rows
    ):
        return {
            **base,
            "status": "IMMATURE",
            "reason": "cost/wall/cache observation incomplete",
        }

    costs = [float(row["estimated_usd"]) for row in rows]
    walls_min = [float(row["interactive_wall_s"]) / 60 for row in rows]
    caches = [float(row["cache_hit_rate"]) for row in rows]
    median_cost = float(statistics.median(costs))
    baseline_cost = float(baseline["estimated_usd"])
    reduction = 1 - median_cost / baseline_cost if baseline_cost else None
    p50 = float(statistics.median(walls_min))
    p90 = float(_nearest_rank(walls_min, 0.9))
    cache_median = float(statistics.median(caches))

    if int(phase) == 2:
        targets = {
            "cost": reduction is not None and reduction >= 0.25,
            "p50": p50 <= 65,
            "p90": p90 <= 90,
            "cache": cache_median >= policy["cache_hit_min"],
        }
    else:
        targets = {
            "cost": reduction is not None and reduction >= 0.15,
            "p50": p50 <= 75,
            "p90": p90 <= 100,
            "cache": cache_median >= policy["cache_hit_min"],
        }
    return {
        **base,
        "status": "PASS" if all(targets.values()) else "FAIL",
        "reason": "all targets passed" if all(targets.values()) else "target breach",
        "baseline_run": policy["baseline_run"],
        "baseline_cost_usd": baseline_cost,
        "median_cost_usd": median_cost,
        "cost_reduction": None if reduction is None else round(reduction, 6),
        "p50_minutes": round(p50, 4),
        "p90_minutes": round(p90, 4),
        "median_cache_hit_rate": round(cache_median, 6),
        "targets": targets,
    }

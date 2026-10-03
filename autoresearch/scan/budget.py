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
from autoresearch.contracts.scan_config import DEFAULT_CONCURRENCY

BUDGET_OBSERVATION_SCHEMA_VERSION = 1
DEFAULT_BUDGETS = {
    "cache_hit_min": 0.85,
    "run_weighted_warn": 7_000_000,
    "run_weighted_target": 5_000_000,
    "stage_cost_usd": {},
    "stage_wall_seconds": {},
    "concurrency": dict(DEFAULT_CONCURRENCY),
    "min_real_scans": 10,
    "baseline_run": "20260727_2140",
    "maturity": {"phase1": {"cost_reduction": 0.15, "p50": 75, "p90": 100},
                 "phase2": {"cost_reduction": 0.25, "p50": 65, "p90": 90}},
    # 相对预算带(`scan.run_drift.relative`):对近期已发布真实扫描中位数的倍数。
    "relative": {"window": 10, "min_runs": 3, "warn_ratio": 1.5, "alarm_ratio": 2.0},
}


def _relative_policy(raw: dict | None) -> dict:
    """`budgets.relative` → 校验后的完整组;非法即 ValueError(配置错误不得静默变成另一把尺)。"""
    merged = {**DEFAULT_BUDGETS["relative"], **(raw or {})}
    window, min_runs = merged["window"], merged["min_runs"]
    warn, alarm = merged["warn_ratio"], merged["alarm_ratio"]
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in (window, min_runs)):
        raise ValueError("budgets.relative.window / min_runs 须为正整数")
    if min_runs > window:
        raise ValueError("budgets.relative 须满足 min_runs <= window")
    if (_finite_number(warn, allow_zero=False) is None or _finite_number(alarm, allow_zero=False) is None
            or not 1 < float(warn) <= float(alarm)):
        raise ValueError("budgets.relative 须满足 1 < warn_ratio <= alarm_ratio")
    return {"window": window, "min_runs": min_runs, "warn_ratio": float(warn),
            "alarm_ratio": float(alarm)}


def _finite_number(value, *, allow_zero: bool) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        return None
    return number


def normalize_budgets(raw: dict | None) -> dict:
    """用户预算块 + 稳定默认值；输入不被原地修改。"""
    raw = raw or {}
    concurrency = {
        **DEFAULT_BUDGETS["concurrency"],
        **(raw.get("concurrency") or {}),
    }
    warn_raw = raw.get("run_weighted_warn", DEFAULT_BUDGETS["run_weighted_warn"])
    target_raw = raw.get(
        "run_weighted_target", DEFAULT_BUDGETS["run_weighted_target"]
    )
    warn = _finite_number(warn_raw, allow_zero=False)
    target = _finite_number(target_raw, allow_zero=False)
    if warn is None or target is None or target > warn:
        raise ValueError("weighted budget 须满足 0 < target <= warn")
    return {
        "cache_hit_min": float(
            raw.get("cache_hit_min", DEFAULT_BUDGETS["cache_hit_min"])
        ),
        "run_weighted_warn": warn,
        "run_weighted_target": target,
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
        "maturity": {
            ph: {**DEFAULT_BUDGETS["maturity"][ph], **((raw.get("maturity") or {}).get(ph) or {})}
            for ph in ("phase1", "phase2")
        },
        "relative": _relative_policy(raw.get("relative")),
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
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        if path.read_text(encoding="utf-8") == body:
            return path
    except FileNotFoundError:
        pass
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(body, encoding="utf-8")
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
    persist: bool = True,
) -> dict:
    """生成一次观测；默认双写 JSON/StageResult，永远 `truncated=False`。

    编排层需在补齐成熟度、报告刷新告警后一次性发布时传 ``persist=False``，避免先写
    半成品再覆盖最终态，破坏重复运行的字节幂等。
    """
    scan = Path(scan_dir)
    policy = normalize_budgets(budgets)
    warnings: list[str] = []
    advisories: list[str] = []
    hit = usage_ledger.get("cache_hit_rate")
    totals = usage_ledger.get("totals") or {}
    estimated = totals.get("estimated_usd")
    weighted_raw = (
        totals.get("weighted_input_proxy")
        if "weighted_input_proxy" in totals
        else totals.get("weighted_in")
    )
    weighted = _finite_number(weighted_raw, allow_zero=True)
    cost_measured = usage_ledger.get("schema_version") == 1 and estimated is not None
    measured = cost_measured and weighted is not None
    total_wall = _wall_seconds(timing, "总计")

    if weighted is None:
        budget_band = "RED"
        warnings.append("weighted_input_proxy 未计量")
    else:
        if weighted > policy["run_weighted_warn"]:
            budget_band = "RED"
            warnings.append(
                "weighted_input_proxy "
                f"{weighted:.0f} > {policy['run_weighted_warn']:.0f}"
            )
        elif weighted > policy["run_weighted_target"]:
            budget_band = "YELLOW"
            advisories.append(
                "weighted_input_proxy "
                f"{weighted:.0f} > target {policy['run_weighted_target']:.0f}"
            )
        else:
            budget_band = "GREEN"

    if not cost_measured:
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
        "weighted_input_proxy": weighted,
        "budget_band": budget_band,
        "estimated_usd": None if estimated is None else float(estimated),
        "interactive_wall_s": total_wall,
        "cache_hit_rate": None if hit is None else float(hit),
        "stage_cost_usd": cost_by_agent,
        "stage_wall_seconds": {
            key: _wall_seconds(timing, key) for key in timing
        },
        "budgets": policy,
        "warnings": warnings,
        "advisories": advisories,
    }
    if persist:
        _atomic_json(scan / "_budget_observation.json", observation)
        safe_record_stage_result(
            scan,
            stage="budget",
            status=status,
            artifacts=["budget_observation"],
            metrics={
                "truncated": False,
                "weighted_input_proxy": observation["weighted_input_proxy"],
                "budget_band": budget_band,
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

    weighted_values: list[float] = []
    for row in rows:
        weighted_raw = (
            row.get("weighted_input_proxy")
            if "weighted_input_proxy" in row
            else row.get("weighted_in")
        )
        weighted = _finite_number(weighted_raw, allow_zero=True)
        if weighted is None:
            return {
                **base,
                "status": "IMMATURE",
                "reason": "weighted observation incomplete",
            }
        weighted_values.append(weighted)

    costs = [float(row["estimated_usd"]) for row in rows]
    walls_min = [float(row["interactive_wall_s"]) / 60 for row in rows]
    caches = [float(row["cache_hit_rate"]) for row in rows]
    median_cost = float(statistics.median(costs))
    baseline_cost = float(baseline["estimated_usd"])
    reduction = 1 - median_cost / baseline_cost if baseline_cost else None
    p50 = float(statistics.median(walls_min))
    p90 = float(_nearest_rank(walls_min, 0.9))
    cache_median = float(statistics.median(caches))
    weighted_p50 = float(_nearest_rank(weighted_values, 0.5))
    weighted_p90 = float(_nearest_rank(weighted_values, 0.9))

    mt = (policy.get("maturity") or DEFAULT_BUDGETS["maturity"])
    if int(phase) == 2:
        targets = {
            "cost": reduction is not None and reduction >= mt["phase2"]["cost_reduction"],
            "p50": p50 <= mt["phase2"]["p50"],
            "p90": p90 <= mt["phase2"]["p90"],
            "cache": cache_median >= policy["cache_hit_min"],
            "weighted_p50": weighted_p50 <= policy["run_weighted_target"],
            "weighted_p90": weighted_p90 <= policy["run_weighted_warn"],
        }
    else:
        targets = {
            "cost": reduction is not None and reduction >= mt["phase1"]["cost_reduction"],
            "p50": p50 <= mt["phase1"]["p50"],
            "p90": p90 <= mt["phase1"]["p90"],
            "cache": cache_median >= policy["cache_hit_min"],
            "weighted_p50": weighted_p50 <= policy["run_weighted_target"],
            "weighted_p90": weighted_p90 <= policy["run_weighted_warn"],
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
        "weighted_p50": round(weighted_p50, 4),
        "weighted_p90": round(weighted_p90, 4),
        "targets": targets,
    }

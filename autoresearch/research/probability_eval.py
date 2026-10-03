#!/usr/bin/env python3
"""F7:主观概率的记分 + 复核错误重合率 —— 两把尺,分开量,不相加。

实施计划:`docs/superpowers/plans/2026-09-06-research-methods-and-stage-value.md` Task F7。

**事件定义必须先写死**:首个概率实验的事件是「计划隔夜窗、已声明执行模式与成本模型下,
完整样本的净收益 > 0」。缺成交 / 缺费用**不是 y=0**,是**未标注** —— 把「没成交」记成
「没赚到」,概率评价就在给一个根本没发生的赌局打分。

**Brier 不是校准度**。它同时受区分能力与校准影响,分数下降可能来自任一侧;所以本模块永远
把可靠性分组、样本量与基准概率一起给出。校准器若要拟合,训练与评价样本必须分开。
对照:https://scikit-learn.org/stable/modules/calibration.html

**事实可信度与概率不混算**:B 包的「证据支持率」量的是「这条陈述是否被原文支持」,
这里量的是「这个赌局是否兑现」。两者相加会让事实错误被偶然盈利抵消。
"""
from __future__ import annotations

import math

#: 固定分箱。**先定后看** —— 跑完再挑分箱边界,可靠性曲线要多好看有多好看。
DEFAULT_EDGES: tuple[float, ...] = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def _check_edges(edges) -> tuple[float, ...]:
    values = tuple(float(edge) for edge in edges)
    if len(values) < 2 or values[0] != 0.0 or values[-1] != 1.0:
        raise ValueError("invalid fixed bins")
    pairs = list(zip(values, values[1:], strict=False))     # 相邻配对,天然差一项
    if any(a >= b for a, b in pairs):
        raise ValueError("invalid fixed bins")
    return values


def probability_metrics(rows, *, event_id: str, edges=DEFAULT_EDGES) -> dict:
    """一组 `(p, y)` 的 Brier + 固定分箱可靠性 → 读数 dict。

    - `event_id` 不一致 → 抛错。把两个事件的概率混进一个分数,分数就不指任何东西。
    - `p is None` 或 `y is None` → **计入 `missing`,不计入分母**。缺标注不是 0。
    - `p` 必须是 [0,1] 内有限数,`y` 必须是 0/1(`True`/`False` 不收:布尔在这里通常意味着
      上游把「未知」压成了 False)。
    - 最后一箱**右闭**,好让 `p == 1.0` 有地方落;其余左闭右开。
    """
    bins_edges = _check_edges(edges)
    valid: list[tuple[float, int]] = []
    missing = 0
    for row in rows:
        if row["event_id"] != event_id:
            raise ValueError("mixed probability targets")
        p, y = row["p"], row["y"]
        if p is None or y is None:
            missing += 1
            continue
        if type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError(f"invalid probability: {p!r}")
        if type(y) is bool or y not in (0, 1):
            raise ValueError(f"invalid outcome: {y!r}")
        valid.append((float(p), int(y)))
    bins = []
    last = len(bins_edges) - 2
    for index, (low, high) in enumerate(zip(bins_edges, bins_edges[1:], strict=False)):
        pairs = [(p, y) for p, y in valid
                 if low <= p < high or (index == last and p == high)]
        bins.append({"low": low, "high": high, "n": len(pairs),
                     "mean_p": sum(p for p, _ in pairs) / len(pairs) if pairs else None,
                     "observed_rate": sum(y for _, y in pairs) / len(pairs) if pairs else None})
    n = len(valid)
    return {
        "event_id": event_id, "n": n, "missing": missing,
        "brier": sum((p - y) ** 2 for p, y in valid) / n if n else None,
        "base_rate": sum(y for _, y in valid) / n if n else None,
        "bins": bins,
    }


def error_overlap(first: dict, second: dict, labelled: dict) -> dict:
    """两位复核者在**共同可标注案例**上的错误重合(Jaccard)。

    三票一致不是三份独立证据:两个复核者读同一份来源、用同一个模型,错在同一处是常态。
    重合率高 ⇒ 「多轮复核」提供的独立信息比看上去少得多。

    只有双方错误集合的并集为空时 Jaccard 记 `None` —— 分母为空,不能据此宣称「独立」。
    """
    common = set(first) & set(second) & set(labelled)
    wrong_first = {key for key in common if first[key] != labelled[key]}
    wrong_second = {key for key in common if second[key] != labelled[key]}
    union = wrong_first | wrong_second
    return {"n_common": len(common), "n_wrong_first": len(wrong_first),
            "n_wrong_second": len(wrong_second), "both_wrong": len(wrong_first & wrong_second),
            "error_jaccard": len(wrong_first & wrong_second) / len(union) if union else None,
            "first_error_rate": len(wrong_first) / len(common) if common else None,
            "second_error_rate": len(wrong_second) / len(common) if common else None}


PLANNED_OVERNIGHT_EVENT = "planned_overnight_net_positive_v1"


def execution_probability_row(
    declaration: dict, *, plan: dict, fills: list[dict], sizing: dict | None = None
) -> dict:
    """Label only an explicitly predeclared p for the actual planned overnight event.

    `declaration` contains event_id, p, declared_at and the frozen plan_hash. `plan` contains the ledger's
    planned windows, execution mode and cost model. Conviction is never converted.
    The resulting row feeds probability_metrics unchanged (fixed bins/base rate).
    """
    from autoresearch.research.execution_ledger import (
        _aware_time,
        execution_plan_hash,
        observed_execution,
    )

    execution = observed_execution(fills, plan=plan)
    reasons = list(execution["missing_reasons"])
    plan_hash = execution_plan_hash(plan)
    if not declaration.get("plan_hash"):
        reasons.append("MISSING_DECLARED_PLAN_HASH")
    elif declaration["plan_hash"] != plan_hash:
        reasons.append("DECLARED_PLAN_HASH_MISMATCH")
    if declaration.get("event_id") != PLANNED_OVERNIGHT_EVENT:
        reasons.append("EVENT_NOT_DECLARED")
    p = declaration.get("p")
    if p is None:
        reasons.append("MISSING_DECLARED_PROBABILITY")
    elif type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("invalid declared probability")
    declared_at = _aware_time(declaration.get("declared_at"))
    entry_at = _aware_time(plan.get("entry_window_start"))
    if declared_at is None or entry_at is None or declared_at >= entry_at:
        reasons.append("PROBABILITY_NOT_PREDECLARED")
    if sizing is not None:
        from decimal import Decimal

        from autoresearch.research.execution_ledger import _decimal, _fees

        if declaration.get("sizing_hash") != execution_sizing_hash(
            plan, qty=sizing["qty"], weight=sizing["weight"]
        ):
            reasons.append("DECLARED_SIZING_HASH_MISMATCH")
        expected = _decimal(sizing["qty"], field="planned quantity")
        actual = {
            side: sum(
                (
                    _decimal(row["qty"], field="fill quantity")
                    for row in fills
                    if row["side"] == side
                ),
                Decimal(0),
            )
            for side in ("BUY", "SELL")
        }
        if any(quantity != expected for quantity in actual.values()):
            reasons.append("QUANTITY_PLAN_MISMATCH")
        capital = sizing.get("initial_capital")
        if capital is None:
            reasons.append("ALLOCATION_BASE_UNKNOWN")
        elif any(_fees(row) is None for row in fills if row["side"] == "BUY"):
            reasons.append("FEES_MISSING")
        else:
            spent = sum(
                (
                    _decimal(row["amount"], field="buy amount") + _fees(row)
                    for row in fills
                    if row["side"] == "BUY"
                ),
                Decimal(0),
            )
            if spent > Decimal(capital) * Decimal(sizing["weight"]):
                reasons.append("FROZEN_ALLOCATION_EXCEEDED")
    net = execution["net_pnl_cash"]
    return {
        "event_id": PLANNED_OVERNIGHT_EVENT,
        "p": p,
        "plan_hash": plan_hash,
        "y": int(net > 0) if not reasons and net is not None else None,
        "missing_reasons": sorted(set(reasons)),
        "execution": execution,
    }


def clustered_probability_metrics(rows, *, event_id, training_rows, split):
    """Unique event cases; equal date clusters; baseline fitted only on frozen train.

    No calibrator is selected here. Validation is an explicitly reserved window;
    only the preregistered training base-rate method is evaluated on test dates.
    Repeated runs with contradictory p/y remain missing, never independent trials.
    """
    from collections import defaultdict

    windows = [split[key] for key in ("train", "validation", "test")]
    if (
        any(len(w) != 2 or w[0] >= w[1] for w in windows)
        or windows[0][1] > windows[1][0]
        or windows[1][1] > windows[2][0]
    ):
        raise ValueError("overlapping probability splits")

    def unique(values, bounds, label):
        groups = defaultdict(list)
        for r in values:
            if not bounds[0] <= r["analysis_date"] < bounds[1]:
                raise ValueError(label + " row outside frozen split")
            groups[(r["analysis_date"], r["event_id"], r["case_id"])].append(r)
        result = []
        for values in groups.values():
            first = dict(values[0])
            if any((r["p"], r["y"]) != (first["p"], first["y"]) for r in values):
                first["y"] = None
            result.append(first)
        return result

    test = unique(rows, windows[2], "test")
    train = unique(training_rows, windows[0], "training")
    raw = probability_metrics(test, event_id=event_id)
    train_metrics = probability_metrics(train, event_id=event_id)
    clusters = defaultdict(list)
    for r in test:
        clusters[r["analysis_date"]].append(r)
    scored = [probability_metrics(m, event_id=event_id) for m in clusters.values()]
    valid = [m for m in scored if m["brier"] is not None]
    train_dates = defaultdict(list)
    for row in train:
        train_dates[row["analysis_date"]].append(row)
    train_rates = [
        probability_metrics(values, event_id=event_id)["base_rate"]
        for values in train_dates.values()
    ]
    train_rates = [rate for rate in train_rates if rate is not None]
    baseline = sum(train_rates) / len(train_rates) if train_rates else None
    baseline_scores = []
    for values in clusters.values():
        ys = [r["y"] for r in values if r["p"] is not None and r["y"] is not None]
        if ys and baseline is not None:
            baseline_scores.append(sum((baseline - y) ** 2 for y in ys) / len(ys))
    return {
        **raw,
        "brier": sum(m["brier"] for m in valid) / len(valid) if valid else None,
        "n_clusters": len(clusters),
        "n_scored_clusters": len(valid),
        "n_repeated": len(rows) - len(test),
        "baseline_probability": baseline,
        "baseline_training_n": train_metrics["n"],
        "baseline_brier": sum(baseline_scores) / len(baseline_scores) if baseline_scores else None,
        "weighting": "date_equal_unique_event",
        "population": len(test),
        "coverage": raw["n"] / len(test) if test else None,
    }


def import_training_outcomes(value, *, registered_at=None):
    """Recompute training y from explicitly imported, hash-bound broker exports."""
    from pathlib import Path

    from autoresearch.common.atomic import sha256_file
    from autoresearch.research.execution_import import load_trades
    from autoresearch.research.execution_ledger import (
        _aware_time,
        build_episodes,
        execution_plan_hash,
        observed_execution,
    )
    from autoresearch.research.forward_study import _safe

    path = _safe(Path(value["trades_path"]))
    if sha256_file(path) != value["trades_sha256"]:
        raise ValueError("training trades hash mismatch")
    fills, errors = load_trades(path)
    if errors:
        raise ValueError("training trades import incomplete")
    episodes, _ = build_episodes(fills)
    result = []
    for plan in value["plans"]:
        exit_at = _aware_time(plan["exit_window_end"])
        if registered_at is not None and (exit_at is None or exit_at >= registered_at):
            raise ValueError("training outcome not available before registration")
        matching = [
            ep
            for ep in episodes
            if ep["code"] == plan["code"] and ep["session"] == plan["entry_window_start"][:10]
        ]
        ids = set(matching[0]["fill_ids"]) if len(matching) == 1 else set()
        execution = observed_execution([r for r in fills if r["fill_id"] in ids], plan=plan)
        net = execution["net_pnl_cash"]
        result.append(
            {
                "event_id": PLANNED_OVERNIGHT_EVENT,
                "analysis_date": plan["analysis_session"],
                "case_id": execution_plan_hash(plan),
                "p": 0.5,
                "y": int(net > 0) if net is not None else None,
                "source_sha256": value["trades_sha256"],
                "missing_reasons": execution["missing_reasons"],
            }
        )
    return result


def execution_sizing_hash(plan: dict, *, qty, weight) -> str:
    """Bind a predeclared p to quantity and maximum capital allocation separately.

    The old execution_plan_hash and legacy probability API remain unchanged.
    New prospective candidates must carry this additional declaration hash.
    """
    from autoresearch.common.atomic import canonical_json, sha256_bytes
    from autoresearch.research.execution_ledger import _decimal, execution_plan_hash

    quantity = _decimal(qty, field="planned quantity")
    allocation = _decimal(weight, field="planned weight")
    if quantity <= 0 or not 0 < allocation <= 1:
        raise ValueError("invalid probability sizing")
    value = {
        "plan_hash": execution_plan_hash(plan),
        "qty": format(quantity.normalize(), "f"),
        "weight": format(allocation.normalize(), "f"),
    }
    return sha256_bytes(canonical_json(value).encode())

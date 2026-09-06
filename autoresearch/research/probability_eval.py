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

    任何一方零错误时 Jaccard 记 `None` —— 分母为空,不能据此宣称「独立」。
    """
    common = set(first) & set(second) & set(labelled)
    wrong_first = {key for key in common if first[key] != labelled[key]}
    wrong_second = {key for key in common if second[key] != labelled[key]}
    union = wrong_first | wrong_second
    return {"n_common": len(common), "n_wrong_first": len(wrong_first),
            "n_wrong_second": len(wrong_second), "both_wrong": len(wrong_first & wrong_second),
            "error_jaccard": len(wrong_first & wrong_second) / len(union) if union else None}

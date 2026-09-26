#!/usr/bin/env python3
"""10 日尺普查的判读统计 —— 少块、重叠窗口下校准过的均值检验(确定性、零 LLM、零 scipy)。

registration: docs/research/2026-09-26-swing-ruler-family-v2.spec.json(FAM_SWING_RULER_V2_20260926)

## 治的病(2026-09-26 批 5 复审 I1)

旧登记(FAM_SWING_RULER_20260926)在样本门 n = 40 天、块长 10 上用块 bootstrap 判读:每次
重采样只有 4 个块,方差被严重低估。复审用本仓原语做的蒙特卡洛(真均值 0):

- MA(9) 零假设(相邻分析日的 10 日窗口重叠 9 个 session)下,块 10 的 95% 区间只覆盖 0 约
  65–70%,`common.stats.block_mean_test` 的 p ≤ 0.05 实际发生 27–29%(名义 5%);
- 区间(非循环 moving-block 分位)与 p 值(中心化循环块)是两套方法 —— 基线里 H1 区间
  整段在 0 以下,同一格 p = 0.051。

t 表也救不了:Newey–West(Bartlett,滞后 9)t 配 df ≈ n/b − 1 的 t 临界值,同一零假设下
假阳仍有 10–12%(n = 40..130;本波离线模拟,见登记 spec 的 `purge_rule`)。

## 本模块的检验:一个统计量、一个临界值,区间与 p 值同源

- **统计量**:HAC t = 日序列均值 / Newey–West 标准误(Bartlett 核,滞后 `HAC_LAG` = 9)。
  日序列 = 按分析日排序的日等权超额(普查已经先日内等权)。
- **临界值**:不查表,而在**同一 n** 上模拟普查自己的最坏重叠零假设 —— 纯 MA(9):逐日
  iid N(0,1) 冲击的 10 日重叠等权和,即「日收益独立、相邻分析日窗口重叠 9 个 session」
  时日序列的样子。HAC t 对尺度不变,模拟分布只依赖 n。固定种子、`NULL_REPS` 次。
- **p 值与区间**:p = (1 + #{|t*| ≥ |t|}) / (R + 1);临界值 c_n 取模拟 |t*| 升序的第
  R − ⌊α(R+1)⌋ 个(0 起),于是 **p ≤ α ⇔ |t| > c_n ⇔ 区间 均值 ± c_n·SE 不含 0** ——
  同一个 t、同一个 c_n,不会再出现「区间不含 0 而 p > α」。
- **保守方向**:扫描日有缺口(一个观察步跨多个交易日)、个股噪声占比更高、乃至逐日独立,
  统计量的尾巴都比纯 MA(9) 轻 → 这个临界值只会更严(模拟:iid 时假阳约 1%)。

尺寸校准由 `tests/research/test_swing_ruler_decision.py` 锁住:MA(9) 零假设、判读样本量
(n = 40)上逐格假判率 ≤ 7%,且换成正态临界值 1.96 同一测试必红(测试有牙)。
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

#: 登记的检验名 —— spec `selection_rule.decision_test` 必须恰好等于它。
DECISION_TEST = "hac_bartlett_overlap_null"
#: Newey–West 滞后:fwd_10 相邻分析日窗口最多重叠 9 个 session。
HAC_LAG = 9
#: 零假设模拟次数与种子(登记值;p 值分辨率 1/(R+1))。
NULL_REPS = 20_000
NULL_SEED = 20260926
#: 最少观察数:装不下两个完整的重叠窗口(2·(滞后+1))就不算,区间与 p 值都给 None。
MIN_OBS_FACTOR = 2

POSITIVE = "POSITIVE"
NEGATIVE = "NEGATIVE"
UNPROVEN = "UNPROVEN"
INSUFFICIENT = "INSUFFICIENT"
EXPLORATORY = "EXPLORATORY"


def hac_t_rows(values: np.ndarray, *, lag: int = HAC_LAG) -> tuple[np.ndarray, np.ndarray]:
    """逐行(每行一条按日排序的序列)→ `(均值, Newey–West 标准误)`,Bartlett 权 1 − h/(lag+1)。"""
    x = np.atleast_2d(np.asarray(values, dtype=float))
    n = x.shape[1]
    mean = x.mean(axis=1)
    e = x - mean[:, None]
    lrv = (e * e).sum(axis=1) / n
    for h in range(1, min(lag, n - 1) + 1):
        lrv = lrv + 2.0 * (1.0 - h / (lag + 1.0)) * (e[:, h:] * e[:, :-h]).sum(axis=1) / n
    return mean, np.sqrt(np.maximum(lrv, 0.0) / n)


def overlap_null_series(rng: np.random.Generator, reps: int, n: int, *,
                        lag: int = HAC_LAG) -> np.ndarray:
    """纯 MA(`lag`) 零假设序列:iid N(0,1) 冲击的 `lag+1` 日重叠等权和(单位方差)。"""
    shocks = rng.standard_normal((reps, n + lag))
    csum = np.concatenate([np.zeros((reps, 1)), np.cumsum(shocks, axis=1)], axis=1)
    return (csum[:, lag + 1:] - csum[:, :-(lag + 1)]) / math.sqrt(lag + 1.0)


def _abs_t(mean: np.ndarray, se: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.abs(mean / se)
    return np.where(se > 0, t, np.where(mean == 0, 0.0, np.inf))


@lru_cache(maxsize=64)
def _null_abs_t(n: int, lag: int, reps: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    mean, se = hac_t_rows(overlap_null_series(rng, reps, n, lag=lag), lag=lag)
    out = np.sort(_abs_t(mean, se))
    out.flags.writeable = False
    return out


def critical_value(n: int, *, lag: int = HAC_LAG, reps: int = NULL_REPS,
                   seed: int = NULL_SEED, alpha: float = 0.05) -> float:
    """c_n:p ≤ α ⇔ |t| > c_n(见模块 docstring 的次序统计量)。"""
    draws = _null_abs_t(int(n), int(lag), int(reps), int(seed))
    m = int(math.floor(alpha * (reps + 1) + 1e-9))
    if m < 1:
        raise ValueError(f"reps={reps} too small to resolve alpha={alpha}")
    return float(draws[reps - m])


def overlap_test(values, *, lag: int = HAC_LAG, reps: int = NULL_REPS, seed: int = NULL_SEED,
                 alpha: float = 0.05) -> dict:
    """一条按日排序的日序列 → `{point, se, t, crit, lo, hi, p, reject, n, status, method}`。

    `reject` ⇔ `p ≤ alpha` ⇔ `lo > 0 or hi < 0`(同一个 t 与 c_n)。样本装不下
    `MIN_OBS_FACTOR·(lag+1)` 个观察 → `status = INSUFFICIENT_OBSERVATIONS`,区间与 p 为 None。
    """
    x = np.asarray(values, dtype=float)
    if x.ndim != 1:
        raise ValueError("ordered one-dimensional daily observations required")
    if not np.isfinite(x).all():
        raise ValueError("ordered finite daily observations required")
    n = int(len(x))
    method = f"{DECISION_TEST}(lag={lag},R={reps},seed={seed})"
    base = {"point": float(x.mean()) if n else None, "n": n, "method": method,
            "se": None, "t": None, "crit": None, "lo": None, "hi": None, "p": None,
            "reject": False}
    if n < MIN_OBS_FACTOR * (lag + 1):
        return {**base, "status": "INSUFFICIENT_OBSERVATIONS"}
    mean, se = hac_t_rows(x, lag=lag)
    point, se_v = float(mean[0]), float(se[0])
    abs_t = float(_abs_t(mean, se)[0])
    crit = critical_value(n, lag=lag, reps=reps, seed=seed, alpha=alpha)
    draws = _null_abs_t(n, int(lag), int(reps), int(seed))
    exceed = int(reps - np.searchsorted(draws, abs_t, side="left"))     # #{|t*| ≥ |t|}
    p = (1 + exceed) / (reps + 1)
    reject = abs_t > crit
    t = math.copysign(abs_t, point) if point != 0 else 0.0
    return {**base, "status": "COMPUTED", "point": point, "se": se_v, "t": t, "crit": crit,
            "lo": point - crit * se_v, "hi": point + crit * se_v, "p": float(p),
            "reject": bool(reject)}


def decide(*, n_days: int, min_days: int, test: dict | None, q: float | None,
           fdr_alpha: float, registered: bool) -> str:
    """登记的判读规则:窗口 → 样本门 → (校准检验拒绝 ∧ BY q ≤ fdr_alpha)→ 方向。"""
    if not registered:
        return EXPLORATORY
    if int(n_days or 0) < int(min_days):
        return INSUFFICIENT
    if not test or test.get("status") != "COMPUTED" or q is None:
        return UNPROVEN
    if not (test.get("reject") and q <= fdr_alpha):
        return UNPROVEN
    return POSITIVE if float(test["point"]) > 0 else NEGATIVE


__all__ = [
    "DECISION_TEST", "HAC_LAG", "NULL_REPS", "NULL_SEED",
    "POSITIVE", "NEGATIVE", "UNPROVEN", "INSUFFICIENT", "EXPLORATORY",
    "hac_t_rows", "overlap_null_series", "critical_value", "overlap_test", "decide",
]

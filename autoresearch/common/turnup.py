#!/usr/bin/env python3
"""低位转强 —— 面板因子 + 画像谓词的**单一实现**(L1 帧装配 / factor_lab 研究 / L3 旗三处共用)。

design: docs/specs/2026-08-21-lowturn-recall-l3-picture-display-design.md §4.1 / §6.1。
仓库已两次为「两层各造一套词表」付过学费(`l2_stratify.py:39-52`、`l3/triage.py:8-10`),
所以这里只有一份数学,三个消费者 import。

面板因子(输入 = code×date pivot,列为交易日升序且全部 ≤D,D = dates[-1];严格无前视):
  vol_ratio_20       D 日成交额 / 近 20 日(含 D)均成交额(镜像 factor_lab.reversal_confirm_factors)
  dist_low_60        (close[D] / 60 日滚动最低 low − 1)×100,恒 ≥0
  dist_high_60       (close[D] / 60 日滚动最高 high − 1)×100,恒 ≤0(「跌过」的广义判据;缺 high → NaN)
  days_no_new_low    截至 D 连续未创 60 日新低天数(D 当日创新低 → 0)
  vol_ma5_prev / vol_ma20_prev   截止 **D−1** 的 5/20 日均成交额(起爆前是否缩量;不含 D;窗口不足 → NaN)
  pct_5d / pct_20d   close[D]/close[D−5]−1、close[D]/close[D−20]−1(%;窗口不足 → NaN)
  above_ma20 / ma5_gt_ma10   由**同一 close 面板**算的 MA20 / MA5 / MA10(同序列同口径,不与
                     tushare 复权 MA 混用;窗口不足 → NaN)。

画像谓词 `lowturn_flag(row, cfg)`:低位 ∧ 转强 ∧ 放量 ∧ 资金 ∧ ¬健康上涨 ∧ ¬落刀 ∧ 非 ST/退,
阈值来自 `LOWTURN_DEFAULTS`(jsonc `l3.lowturn` 覆盖);与 `reversal_confirm` 通道门
(`scoring.lens_reversal_confirm`,仍是那里的单一实现)是**同一组列的两档**,不是两套词表。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

PANEL_COLS = ("vol_ratio_20", "dist_low_60", "dist_high_60", "days_no_new_low",
              "vol_ma5_prev", "vol_ma20_prev", "pct_5d", "pct_20d", "above_ma20", "ma5_gt_ma10")


def _cols(piv: pd.DataFrame, dates: list[str]) -> pd.DataFrame:
    return piv.reindex(columns=dates)


def panel_factors(piv: dict, dates: list[str]) -> pd.DataFrame:
    """code×date pivot(`close`/`amount`/`low` 必需,`high` 可选)+ 交易日升序窗口 → `PANEL_COLS`。"""
    dates = list(dates)
    if not dates:
        raise ValueError("panel_factors: dates 为空")
    D = dates[-1]
    C, A, L = piv["close"], piv["amount"], piv["low"]
    H = piv.get("high")
    codes = C.index
    out = pd.DataFrame(index=codes)
    out.index.name = "code"
    close_d = _cols(C, [D]).iloc[:, 0]

    win20 = dates[-20:]
    denom20 = _cols(A, win20).mean(axis=1).replace(0, np.nan)
    out["vol_ratio_20"] = _cols(A, [D]).iloc[:, 0] / denom20

    low_hist = _cols(L, dates)
    roll_min60 = low_hist.T.rolling(60, min_periods=1).min().T
    out["dist_low_60"] = (close_d / roll_min60[D] - 1.0) * 100

    if H is not None:
        roll_max60 = _cols(H, dates).T.rolling(60, min_periods=1).max().T
        out["dist_high_60"] = (close_d / roll_max60[D] - 1.0) * 100
    else:
        out["dist_high_60"] = np.nan

    is_new_low = (low_hist <= roll_min60 + 1e-9).to_numpy()
    out["days_no_new_low"] = is_new_low[:, ::-1].argmax(axis=1).astype(float)

    nan = pd.Series(np.nan, index=codes)
    out["vol_ma5_prev"] = _cols(A, dates[-6:-1]).mean(axis=1) if len(dates) >= 6 else nan
    out["vol_ma20_prev"] = _cols(A, dates[-21:-1]).mean(axis=1) if len(dates) >= 21 else nan

    def _lag_pct(k: int) -> pd.Series:
        if len(dates) < k + 1:
            return nan
        return (close_d / _cols(C, [dates[-(k + 1)]]).iloc[:, 0] - 1.0) * 100

    out["pct_5d"] = _lag_pct(5)
    out["pct_20d"] = _lag_pct(20)

    def _ma(k: int) -> pd.Series:
        return _cols(C, dates[-k:]).mean(axis=1) if len(dates) >= k else nan

    ma5, ma10, ma20 = _ma(5), _ma(10), _ma(20)
    out["above_ma20"] = (close_d > ma20).where(ma20.notna() & close_d.notna()).astype(float)
    out["ma5_gt_ma10"] = (ma5 > ma10).where(ma5.notna() & ma10.notna()).astype(float)
    return out[list(PANEL_COLS)]

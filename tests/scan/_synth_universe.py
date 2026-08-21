"""合成 post-gate universe —— composite_score + 8 channel 所需全列。NO network。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def synth_universe(n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    inds = rng.choice(["半导体", "光模块", "白酒", "煤炭", "医药", "电力"], n)
    df = pd.DataFrame({
        "code": [f"{600000 + i:06d}" for i in range(n)],
        "name": [f"股票{i}" for i in range(n)], "industry": inds,
        "close": rng.uniform(5, 300, n), "mktcap_yi": rng.uniform(40, 4000, n),
        "amount_yi": rng.uniform(0.5, 200, n), "pct_60d": rng.uniform(-50, 300, n),
        "pct_ytd": rng.uniform(-60, 400, n), "vol_ratio": rng.uniform(0.3, 5, n),
        "turnover": rng.uniform(0.1, 30, n), "pe": rng.uniform(-50, 200, n),
        "pb": rng.uniform(0.5, 30, n), "rev": rng.uniform(1e8, 5e10, n),
        "rev_yoy": rng.uniform(-40, 120, n), "np_yoy": rng.uniform(-100, 300, n),
        "np_qoq": rng.uniform(-50, 80, n), "roe": rng.uniform(-10, 35, n),
        "gross_margin": rng.uniform(5, 70, n), "cfo_ps": rng.uniform(-1, 3, n),
        "np_yoy_prev": rng.uniform(-100, 200, n), "main_inflow_yi": rng.uniform(-5, 8, n),
        "dv_ratio": rng.uniform(0, 6, n), "ma_bull": rng.integers(0, 2, n).astype(float),
        "above_ma60": rng.integers(0, 2, n).astype(float), "rsi6": rng.uniform(10, 95, n),
        "rsi12": rng.uniform(10, 95, n), "winner_rate": rng.uniform(0, 100, n),
        "main_net_ratio": rng.uniform(-0.1, 0.1, n), "retail_net_yi": rng.uniform(-2, 2, n),
        "chip_concentration": rng.uniform(0.1, 2.0, n), "price_to_cost": rng.uniform(0.7, 1.5, n),
        "hk_ratio": rng.uniform(0, 30, n), "cmf_20": rng.uniform(-0.5, 0.5, n),
        "obv_mom_20": rng.uniform(-1, 1, n), "is_st": False,
        # 2026-08-21 低位转强波:turnup.PANEL_COLS(生产由 frame._harvest_vol_series 60 日面板算出)
        "vol_ratio_20": rng.uniform(0.3, 3.0, n), "dist_low_60": rng.uniform(0, 60, n),
        "dist_high_60": rng.uniform(-60, 0, n), "days_no_new_low": rng.integers(0, 60, n).astype(float),
        "vol_ma5_prev": rng.uniform(1e4, 1e6, n), "vol_ma20_prev": rng.uniform(1e4, 1e6, n),
        "pct_5d": rng.uniform(-15, 15, n), "pct_20d": rng.uniform(-30, 30, n),
        "above_ma20": rng.integers(0, 2, n).astype(float), "ma5_gt_ma10": rng.integers(0, 2, n).astype(float),
    })
    return df

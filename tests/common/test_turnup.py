"""低位转强单一实现:面板因子(镜像 factor_lab 三因子 + 七个新列)与画像谓词。合成,无网络。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoresearch.common import turnup


def _piv(rows: dict[str, list[float]], dates: list[str], *, amount=None, low=None, high=None):
    close = pd.DataFrame.from_dict(rows, orient="index", columns=dates, dtype=float)
    out = {"close": close,
           "amount": pd.DataFrame.from_dict(amount, orient="index", columns=dates, dtype=float)
           if amount else close.copy() * 0 + 1.0,
           "low": pd.DataFrame.from_dict(low, orient="index", columns=dates, dtype=float)
           if low else close.copy()}
    if high is not None:
        out["high"] = pd.DataFrame.from_dict(high, orient="index", columns=dates, dtype=float)
    return out


def test_vol_ratio_20_mirrors_factor_lab_definition():
    P = [f"e{i}" for i in range(1, 22)]
    amt = {"A": [1.0] * 20 + [3.0]}
    out = turnup.panel_factors(_piv({"A": [10.0] * 21}, P, amount=amt), P)
    assert np.isclose(out.loc["A", "vol_ratio_20"], 3.0 / ((19 + 3) / 20))


def test_dist_low_and_days_no_new_low_mirror_factor_lab():
    P = ["d1", "d2", "d3", "d4", "d5", "d6"]
    low = {"A": [10, 9, 9.5, 9.2, 9.0, 9.1], "B": [5, 6, 7, 8, 9, 10]}
    out = turnup.panel_factors(_piv(low, P, low=low), P)
    assert np.isclose(out.loc["A", "dist_low_60"], (9.1 / 9.0 - 1.0) * 100)
    assert out.loc["A", "days_no_new_low"] == 1 and out.loc["B", "days_no_new_low"] == 5
    out_d1 = turnup.panel_factors(_piv(low, P, low=low), P[:1])
    assert out_d1.loc["A", "days_no_new_low"] == 0


def test_dist_high_60_nonpositive_and_value():
    P = ["d1", "d2", "d3"]
    high = {"A": [20.0, 18.0, 16.0]}
    out = turnup.panel_factors(_piv({"A": [15.0, 14.0, 15.0]}, P, high=high), P)
    assert out.loc["A", "dist_high_60"] <= 0
    assert np.isclose(out.loc["A", "dist_high_60"], (15.0 / 20.0 - 1.0) * 100)


def test_dist_high_60_nan_without_high_pivot():
    P = ["d1", "d2"]
    out = turnup.panel_factors(_piv({"A": [1.0, 2.0]}, P), P)
    assert np.isnan(out.loc["A", "dist_high_60"])


def test_vol_ma_prev_excludes_D_and_needs_full_window():
    P = [f"d{i}" for i in range(1, 23)]                      # 22 日
    amt = {"A": [1.0] * 16 + [2.0] * 5 + [100.0]}             # 最后 5 个「前日」=2,D 日巨量 100
    out = turnup.panel_factors(_piv({"A": [10.0] * 22}, P, amount=amt), P)
    assert np.isclose(out.loc["A", "vol_ma5_prev"], 2.0)      # 不含 D 的 100
    assert np.isclose(out.loc["A", "vol_ma20_prev"], (15 * 1.0 + 5 * 2.0) / 20)
    short = turnup.panel_factors(_piv({"A": [10.0] * 5}, P[:5], amount={"A": [1.0] * 5}), P[:5])
    assert np.isnan(short.loc["A", "vol_ma5_prev"]) and np.isnan(short.loc["A", "vol_ma20_prev"])


def test_pct_5d_20d_values_and_short_window_nan():
    P = [f"d{i}" for i in range(1, 22)]
    close = {"A": [100.0] * 16 + [100.0, 101.0, 102.0, 103.0, 110.0]}
    out = turnup.panel_factors(_piv(close, P), P)
    assert np.isclose(out.loc["A", "pct_5d"], (110.0 / 100.0 - 1) * 100)   # close[D]/close[D-5]
    assert np.isclose(out.loc["A", "pct_20d"], (110.0 / 100.0 - 1) * 100)
    short = turnup.panel_factors(_piv({"A": [1.0, 2.0, 3.0]}, P[:3]), P[:3])
    assert np.isnan(short.loc["A", "pct_5d"])


def test_above_ma20_and_ma5_gt_ma10_from_same_close_panel():
    P = [f"d{i}" for i in range(1, 21)]
    rising = {"A": list(np.linspace(10, 20, 20))}             # 单边上涨:close>MA20,MA5>MA10
    falling = {"B": list(np.linspace(20, 10, 20))}
    out = turnup.panel_factors(_piv({**rising, **falling}, P), P)
    assert out.loc["A", "above_ma20"] == 1.0 and out.loc["A", "ma5_gt_ma10"] == 1.0
    assert out.loc["B", "above_ma20"] == 0.0 and out.loc["B", "ma5_gt_ma10"] == 0.0
    short = turnup.panel_factors(_piv({"A": [1.0] * 9}, P[:9]), P[:9])
    assert np.isnan(short.loc["A", "above_ma20"]) and np.isnan(short.loc["A", "ma5_gt_ma10"])


def test_panel_cols_contract_and_empty_dates_raise():
    P = ["d1", "d2"]
    out = turnup.panel_factors(_piv({"A": [1.0, 2.0]}, P), P)
    assert list(out.columns) == list(turnup.PANEL_COLS) and out.index.name == "code"
    with pytest.raises(ValueError):
        turnup.panel_factors(_piv({"A": [1.0, 2.0]}, P), [])

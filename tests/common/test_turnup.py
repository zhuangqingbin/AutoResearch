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


# ───────────────────────── lowturn 画像谓词 ─────────────────────────


def _lt_row(**kw):
    base = {"name": "甲", "dist_high_60": -22.0, "pct_60d": -12.0, "pct_5d": 4.0,
            "above_ma20": 1.0, "ma5_gt_ma10": 1.0, "vol_ratio_20": 1.6,
            "main_inflow_yi": 0.8, "cmf_20": 0.05, "main_net_ratio": 0.03}
    base.update(kw)
    return base


def test_lowturn_perfect_row_is_flagged():
    assert turnup.lowturn_flag(_lt_row()) is True
    assert turnup.lowturn_label(_lt_row()) == "转强"


@pytest.mark.parametrize("kw", [
    {"dist_high_60": -8.0},            # 离高点太近 = 没跌过
    {"pct_60d": 12.0},                 # 60 日已涨回去
    {"pct_5d": -1.0},                  # 近 5 日不是正
    {"above_ma20": 0.0},               # 没站回 MA20
    {"ma5_gt_ma10": 0.0},              # 短均线没拐头
    {"vol_ratio_20": 1.1},             # 没放量
    {"main_inflow_yi": -0.2, "cmf_20": -0.01},   # 资金两腿皆负
    {"pct_60d": -40.0, "main_inflow_yi": 0.0, "cmf_20": 0.1},   # 落刀:深跌且无主力
    {"name": "ST甲"}, {"name": "甲退"},
    {"vol_ratio_20": float("nan")}, {"dist_high_60": None},
])
def test_lowturn_single_violation_unflags(kw):
    assert turnup.lowturn_flag(_lt_row(**kw)) is False


def test_lowturn_excludes_healthy_riser():
    """与健康上涨互斥(分账干净):0<pct_60d<40 ∧ main_net_ratio>0 ∧ cmf_20>0 的票归 healthy。"""
    row = _lt_row(pct_60d=5.0, dist_high_60=-16.0)            # 60 日正、主力占比正、cmf 正 = 健康上涨
    assert turnup.lowturn_flag(row) is False
    assert turnup.lowturn_flag(_lt_row(pct_60d=5.0, dist_high_60=-16.0, main_net_ratio=-0.01)) is True


def test_lowturn_thresholds_come_from_cfg():
    assert turnup.lowturn_flag(_lt_row(vol_ratio_20=1.1), {"min_vol_ratio_20": 1.0}) is True
    assert turnup.lowturn_flag(_lt_row(above_ma20=0.0), {"require_above_ma20": False}) is True
    assert turnup.lowturn_flag(_lt_row(main_inflow_yi=-1.0), {"fund": "cmf"}) is True
    assert turnup.lowturn_flag(_lt_row(cmf_20=-1.0), {"fund": "main"}) is True
    with pytest.raises(ValueError):
        turnup.lowturn_flag(_lt_row(), {"fund": "bogus"})


def test_lowturn_mask_matches_rowwise_and_healthy_agrees_with_scoring():
    from autoresearch.common.scoring import healthy_riser_mask
    from tests.scan._synth_universe import synth_universe
    df = synth_universe(n=300, seed=5)
    rng = np.random.default_rng(5)
    df["dist_high_60"] = rng.uniform(-60, 0, len(df))
    df["pct_5d"] = rng.uniform(-10, 10, len(df))
    df["above_ma20"] = rng.integers(0, 2, len(df)).astype(float)
    df["ma5_gt_ma10"] = rng.integers(0, 2, len(df)).astype(float)
    df["vol_ratio_20"] = rng.uniform(0.3, 3, len(df))
    mask = turnup.lowturn_mask(df)
    assert mask.dtype == bool and len(mask) == len(df)
    assert mask.tolist() == [turnup.lowturn_flag(r) for _, r in df.iterrows()]
    healthy = healthy_riser_mask(df)
    assert not (mask & healthy).any()                         # 互斥
    rowwise = pd.Series([turnup._is_healthy_row(r) for _, r in df.iterrows()], index=df.index)
    assert rowwise.equals(healthy.astype(bool))               # 行级判定与 scoring 同阈值

"""Gate 0 前置证伪器:单日/跨日聚合、四组掩码、停机规则三态、渲染固定脚注。合成,无网络。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.research import lowturn_precheck as lp


def _frame(n=200, seed=0, planted=0.0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({"code": [f"{600000 + i:06d}" for i in range(n)],
                       "gap_c1_o2": rng.normal(0, 0.02, n), "fwd_5_oc": rng.normal(0, 0.04, n),
                       "fwd_10_oc": rng.normal(0, 0.06, n), "buyable_c1": True, "buyable": True})
    df["flag"] = False
    df.loc[: n // 10, "flag"] = True
    df.loc[df["flag"], "gap_c1_o2"] += planted
    return df


def test_daily_stats_excess_vs_market_median_and_min_sample():
    df = _frame(planted=0.05)
    st = lp.daily_stats(df, df["flag"], "gap_c1_o2")
    assert st["n"] == 21 and st["excess"] > 0.03
    small = _frame(n=30)
    assert lp.daily_stats(small, small["flag"], "gap_c1_o2") is None   # 截面 <50 不算


def test_aggregate_t_stat_sign_and_counts():
    days = [{"n": 5, "mean": 0.01, "median": 0.01, "hit": 0.6, "excess": 0.01, "market_median": 0.0}] * 10
    agg = lp.aggregate(days)
    assert agg["n_days"] == 10 and np.isclose(agg["excess_mean_pp"], 1.0) and agg["n_med_per_day"] == 5
    neg = [dict(d, excess=-0.01) for d in days]
    assert lp.aggregate(neg)["excess_mean_pp"] < 0


def test_stop_rule_three_states():
    def tbl(excess, t, n_days=60, n_med=5):
        return pd.DataFrame([{"group": "lowturn", "ruler": "gap_c1_o2", "n_days": n_days,
                              "n_med_per_day": n_med, "excess_mean_pp": excess, "t": t,
                              "hit_mean": 0.5, "mean_pp": excess}])
    assert lp.stop_rule(tbl(-0.8, -2.5))["verdict"] == "STOP_P3"
    assert lp.stop_rule(tbl(-0.8, -2.5, n_days=20))["verdict"] == "PROCEED"       # 天数不够不判死
    assert lp.stop_rule(tbl(0.2, 0.5, n_med=2))["verdict"] == "SPARSE"
    assert lp.stop_rule(tbl(0.2, 0.5))["verdict"] == "PROCEED"
    assert lp.stop_rule(pd.DataFrame())["verdict"] == "NO_DATA"


def test_group_masks_keys_and_dtype():
    from tests.scan._synth_universe import synth_universe
    df = synth_universe(n=120, seed=2)
    masks = lp.group_masks(df)
    assert set(masks) == {"lowturn", "healthy", "reversal_old", "reversal_confirm"}
    for m in masks.values():
        assert m.dtype == bool and len(m) == len(df)


def test_render_has_fixed_observation_footnote_and_optional_regime_section():
    table = pd.DataFrame([{"group": "lowturn", "ruler": "gap_c1_o2", "n_days": 60, "n_med_per_day": 4.0,
                           "excess_mean_pp": 0.1, "t": 0.4, "hit_mean": 0.5, "mean_pp": 0.0}])
    md = lp.render(table, lp.stop_rule(table))
    assert "fwd_5_oc" in md and "只观察" in md and "决策尺仍为 gap_c1_o2" in md
    assert "PROCEED" in md and "分 regime" not in md
    reg = pd.DataFrame([{"regime": "range", "n_days": 40, "n_med_per_day": 4.0, "excess_mean_pp": 0.2,
                         "t": 0.8, "hit_mean": 0.52, "mean_pp": 0.1}])
    md2 = lp.render(table, lp.stop_rule(table), reg)
    assert "分 regime" in md2 and "| range |" in md2


def test_unmeasurable_group_is_reported_not_silently_zero():
    """判据缺列算不出的组必须显式标 UNMEASURABLE,**不得**渲染成一行 nan 冒充「量过了、是 0」。

    真事故:factor_lab 研究面板无 np_yoy/np_qoq/np_yoy_prev → lens_reversal KeyError → 旧实现
    把该组静默折成全 False,报告里印出「n_days 0 / nan」,读起来像「旧 reversal 门一只都不召回」,
    而真相是「这块面板压根量不了它」。同族家训:UNMEASURED ≠ CLEAN(structural_audit §纪律1)。"""
    df = pd.DataFrame({"code": ["000001"], "pct_60d": [-30.0], "gap_c1_o2": [0.01]})
    masks = lp.group_masks(df)
    assert masks["reversal_old"] is None                   # 缺 np_yoy 家族 → 算不出,不是 False
    table = pd.DataFrame([{"group": "lowturn", "ruler": "gap_c1_o2", "n_days": 60,
                           "n_med_per_day": 4.0, "excess_mean_pp": 0.1, "t": 0.4,
                           "hit_mean": 0.5, "mean_pp": 0.0}])
    md = lp.render(table, lp.stop_rule(table), None, unmeasurable={"reversal_old": "缺列 np_yoy"})
    assert "UNMEASURABLE" in md and "reversal_old" in md and "缺列 np_yoy" in md

"""Pure scoring primitives (autoresearch.common.scoring) — no network, no I/O.

Mirrors the pure-math assertions of the old screen_market `_selftest` now that the
primitives live in the package: composite in [0,100], _factor_groups → 9 groups,
the four lenses produce in-range scores + bool gates, and the report-quarter
helpers hit the A-share disclosure-deadline cases.
"""

import numpy as np
import pandas as pd

from autoresearch.common.scoring import (
    _GROUPS,
    _PRIOR_WEIGHTS,
    _factor_groups,
    _pct,
    _wsum,
    composite_score,
    latest_reported_quarter,
    lens_growth,
    lens_momentum,
    lens_reversal,
    lens_reversal_confirm,
    lens_value,
    prev_quarter,
)


def _synthetic(n: int = 200) -> pd.DataFrame:
    """Synthetic canonical frame exercising momentum/growth/value/reversal + group inputs."""
    rng = np.random.default_rng(42)
    inds = rng.choice(["半导体", "光模块", "白酒", "煤炭", "医药", "未分类"], n)
    df = pd.DataFrame({
        "code": [f"{600000 + i:06d}" for i in range(n)],
        "name": [f"股票{i}" for i in range(n)],
        "industry": inds,
        "close": rng.uniform(5, 300, n),
        "mktcap_yi": rng.uniform(20, 4000, n),
        "amount_yi": rng.uniform(0.5, 200, n),
        "pct_1d": rng.uniform(-10, 10, n),
        "pct_60d": rng.uniform(-50, 300, n),
        "pct_ytd": rng.uniform(-60, 400, n),
        "vol_ratio": rng.uniform(0.3, 5, n),
        "turnover": rng.uniform(0.1, 30, n),
        "pe": rng.uniform(-50, 200, n),
        "pb": rng.uniform(0.5, 30, n),
        "rev": rng.uniform(1e8, 5e10, n),
        "np_": rng.uniform(-1e9, 5e9, n),
        "rev_yoy": rng.uniform(-40, 120, n),
        "np_yoy": rng.uniform(-100, 300, n),
        "np_qoq": rng.uniform(-50, 80, n),
        "roe": rng.uniform(-10, 35, n),
        "gross_margin": rng.uniform(5, 70, n),
        "cfo_ps": rng.uniform(-1, 3, n),
        "np_yoy_prev": rng.uniform(-100, 200, n),
        "rev_yoy_prev": rng.uniform(-40, 100, n),
        "main_inflow_yi": rng.uniform(-5, 8, n),
        "dv_ratio": rng.uniform(0, 6, n),
        "ma_bull": rng.integers(0, 2, n).astype(float),
        "above_ma60": rng.integers(0, 2, n).astype(float),
        "rsi6": rng.uniform(10, 95, n),
        "rsi12": rng.uniform(10, 95, n),
        "winner_rate": rng.uniform(0, 100, n),
        "cost_50pct": rng.uniform(5, 300, n),
        "main_net_ratio": rng.uniform(-0.1, 0.1, n),
        "retail_net_yi": rng.uniform(-2, 2, n),
        "chip_concentration": rng.uniform(0.1, 2.0, n),
        "price_to_cost": rng.uniform(0.7, 1.5, n),
        "hk_ratio": rng.uniform(0, 30, n),
        "is_st": False,
    })
    return df


def test_lenses_scores_in_range_and_bool_gates():
    df = _synthetic()
    for lens, fn in [("momentum", lens_momentum), ("growth", lens_growth),
                     ("value", lens_value), ("reversal", lens_reversal)]:
        g = fn(df)
        sc, gate = g[f"{lens}_score"], g[f"{lens}_gate"]
        assert (sc.dropna() >= 0).all() and (sc.dropna() <= 100).all(), f"{lens} out of [0,100]"
        assert gate.dtype == bool, f"{lens} gate not bool"
        assert gate.sum() > 0, f"{lens} loose gate passed nobody (suspicious)"


def test_factor_groups_returns_nine_groups():
    df = _synthetic()
    groups = _factor_groups(df)
    assert set(groups.keys()) == set(_GROUPS)
    assert len(groups) == 9
    for name, s in groups.items():
        present = s.dropna()
        if len(present):
            assert (present >= 0).all() and (present <= 1).all(), f"group {name} not a [0,1] percentile"


def test_composite_in_0_100_and_has_subscores():
    df = _synthetic()
    comp = composite_score(df, _PRIOR_WEIGHTS)
    cs = comp["composite"]
    assert (cs.dropna() >= 0).all() and (cs.dropna() <= 100).all()
    for gname in _GROUPS:
        assert f"score_{gname}" in comp.columns, f"missing score_{gname}"


def test_factor_groups_missing_columns_yield_nan_group():
    """A group whose inputs are all absent must come back all-NaN (and not raise)."""
    df = pd.DataFrame({"code": ["600000", "600001"], "industry": ["白酒", "煤炭"],
                       "pct_60d": [10.0, -5.0], "pct_ytd": [20.0, -10.0]})
    groups = _factor_groups(df)
    assert len(groups) == 9
    assert groups["north"].isna().all()       # hk_ratio absent
    assert groups["fund_retail"].isna().all()  # retail_net_yi absent
    assert groups["momentum"].notna().any()    # pct_60d/pct_ytd present


def test_wsum_renormalizes_around_all_nan_subfactor():
    idx = pd.RangeIndex(4)
    good = pd.Series([0.0, 0.25, 0.75, 1.0], index=idx)
    allnan = pd.Series([np.nan] * 4, index=idx)
    with_nan = _wsum({"a": (good, 50), "b": (allnan, 50)})
    only_good = _wsum({"a": (good, 50)})
    pd.testing.assert_series_equal(with_nan, only_good)  # NaN subfactor reweighted out


def test_pct_is_cross_sectional_rank():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    p = _pct(s)
    assert p.is_monotonic_increasing
    assert p.iloc[-1] == p.max()


def test_latest_reported_quarter_cases():
    cases = {"2026-06-20": "20260331", "2026-09-15": "20260630",
             "2026-11-01": "20260930", "2026-02-01": "20250930"}
    for d, exp in cases.items():
        assert latest_reported_quarter(d) == exp, f"latest_reported_quarter({d})"


def test_prev_quarter_wraps_year_at_q1():
    assert prev_quarter("20260331") == "20251231"
    assert prev_quarter("20260630") == "20260331"
    assert prev_quarter("20260930") == "20260630"
    assert prev_quarter("20261231") == "20260930"


# ───────────────────────── lens_reversal_confirm(四段确认通道,Plan A1-T3) ─────────────────────────
#
# design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §2.1。
# 四段:①前置低位 ②衰竭企稳 ③确认起爆(硬门,vol_ratio_20/ma_bull 缺列或不达标 → 整段判 False,
# 不像 ①②④ 那样"presence-gated 跳过=不拦")④可交易。评分只用 ①②③(低位30+企稳30+确认40)。


def _confirm_row(**overrides) -> dict:
    """一行"完美四段"反转确认候选的默认值;overrides 覆写单字段来构造反例,精确定位失败段。"""
    row = {
        "code": "600001", "name": "股票甲",
        "pct_60d": -30.0, "dist_low_60": 8.0,
        "days_no_new_low": 15.0, "rsi6": 35.0,
        "vol_ratio_20": 2.0, "ma_bull": 1.0,
    }
    row.update(overrides)
    return row


def _confirm_frame(rows: list) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_reversal_confirm_admits_perfect_four_stage_stock():
    """①②③④全过 → gate=True,score 落在 (0,100]。"""
    g = lens_reversal_confirm(_confirm_frame([_confirm_row()]))
    assert bool(g["reversal_confirm_gate"].iloc[0]) is True
    assert 0 < g["reversal_confirm_score"].iloc[0] <= 100


def test_reversal_confirm_rejects_no_volume_breakout_hard_gate():
    """①②④全过、只③的量比不足(0.8<1.5)→ 硬门必拒,不能被①②④的高分冲抵(核心:硬门非软加分)。"""
    g = lens_reversal_confirm(_confirm_frame([_confirm_row(vol_ratio_20=0.8)]))
    assert bool(g["reversal_confirm_gate"].iloc[0]) is False


def test_reversal_confirm_rejects_no_trend_break_hard_gate():
    """①②④全过、只③的 ma_bull=0(未站上均线/未破高)→ 硬门同样必拒(AND 的另一半也不可绕过)。"""
    g = lens_reversal_confirm(_confirm_frame([_confirm_row(ma_bull=0.0)]))
    assert bool(g["reversal_confirm_gate"].iloc[0]) is False


def test_reversal_confirm_rejects_still_making_new_lows():
    """①③④全过、只②的 days_no_new_low=0(今日仍创新低,未衰竭企稳)→ 必拒,即便③硬门已过。"""
    g = lens_reversal_confirm(_confirm_frame([_confirm_row(days_no_new_low=0.0)]))
    assert bool(g["reversal_confirm_gate"].iloc[0]) is False


def test_reversal_confirm_dist_low_60_is_alternative_to_pct_60d():
    """①是 OR:pct_60d 不达标(+50%)但 dist_low_60 达标(5%贴近低点)→ 仍应过①(需②③④配合过)。"""
    g = lens_reversal_confirm(_confirm_frame([_confirm_row(pct_60d=50.0, dist_low_60=5.0)]))
    assert bool(g["reversal_confirm_gate"].iloc[0]) is True


def test_reversal_confirm_missing_vol_ratio_20_column_rejects_all():
    """现场 vol_ratio_20 尚未接入 L1 帧(A1-T3 范围外)→ 硬门列缺,诚实全拒,不悄悄放行凑数。"""
    frame = _confirm_frame([_confirm_row()]).drop(columns=["vol_ratio_20"])
    g = lens_reversal_confirm(frame)
    assert not g["reversal_confirm_gate"].any()


def test_reversal_confirm_score_bounds_and_gate_dtype():
    g = lens_reversal_confirm(_confirm_frame([_confirm_row(), _confirm_row(code="600002", vol_ratio_20=0.5)]))
    sc = g["reversal_confirm_score"]
    assert (sc.dropna() >= 0).all() and (sc.dropna() <= 100).all()
    assert g["reversal_confirm_gate"].dtype == bool

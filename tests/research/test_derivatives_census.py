"""衍生品领先性普查(2026-08-24):合成序列 + 合成缓存,零网络。

覆盖:统计原语(NW / t-p / Fisher)/ 无前视分位 / 有效起点验定(谎报起点 + 0 不是波动率)/
年化基差换月毛刺 / 到期日历三旗 / 合成连续合约不得被当主力 / 判读四态 / 缺日不伪造 /
**两条变异探针**(全样本分位、NW lag=0 —— 它们必须让对应断言变红,否则那两个断言是死灯)。

预注册见 `docs/research/2026-08-24-derivatives-lead-census.md` §0。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoresearch.research import derivatives_census as dc

# ───────────────────────── 统计原语 ─────────────────────────


def test_nw_ols_recovers_known_slope():
    """无自相关时 NW 回归应还原真斜率(先证它算得对,再谈它稳不稳)。"""
    rng = np.random.default_rng(11)
    x = rng.normal(size=400)
    y = 0.5 * x + rng.normal(scale=0.1, size=400)
    res = dc.nw_ols(y, np.column_stack([np.ones(400), x]))
    assert res["coef"][1] == pytest.approx(0.5, abs=0.05)
    assert abs(res["t"][1]) > 5


def test_t_pvalue_matches_known_points():
    assert dc.t_pvalue(0.0, 100) == pytest.approx(1.0, abs=1e-6)
    assert dc.t_pvalue(1.984, 100) == pytest.approx(0.05, abs=0.005)   # 双侧 5% 临界
    assert dc.t_pvalue(None, 100) is None


def test_fisher_greater_extremes():
    """全部旗日都出事件、非旗日一个都没有 → 单侧 p 必须很小;完全无关联 → p 接近 1。"""
    assert dc.fisher_greater(10, 0, 0, 30) < 0.001
    assert dc.fisher_greater(2, 8, 20, 80) > 0.5
    assert dc.fisher_greater(0, 0, 0, 0) is None


# ───────────────────────── 无前视分位 + 变异探针 ─────────────────────────


def _lookahead_leaks(pctile_fn) -> bool:
    """探针:只改**未来**的值,早期分位若跟着变 → 该实现有前视。返回 True = 漏了未来。"""
    s = pd.Series(np.linspace(1.0, 5.0, 60) + np.sin(np.arange(60)))
    a = pctile_fn(s)
    s2 = s.copy()
    s2.iloc[-10:] = 999.0            # 未来十天暴涨,过去不该有任何反应
    b = pctile_fn(s2)
    head = slice(0, 50)
    return not a.iloc[head].fillna(-1).equals(b.iloc[head].fillna(-1))


def _full_sample_pctile(s: pd.Series, win: int = 250, min_periods: int = 10) -> pd.Series:
    """**变异体**:用全样本分位定档(典型的前视写法)。"""
    return s.rank(pct=True)


def test_rolling_pctile_has_no_lookahead():
    assert not _lookahead_leaks(lambda s: dc.rolling_pctile(s, win=250, min_periods=10))


def test_mutation_full_sample_pctile_is_caught():
    """变异探针:把档位切点换成全样本分位,上面那条断言必须变红 —— 否则它是永不变红的绿灯。"""
    assert _lookahead_leaks(_full_sample_pctile), "无前视探针对全样本分位无鉴别力"


def test_rolling_pctile_value_and_min_periods():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = dc.rolling_pctile(s, win=5, min_periods=3)
    assert out.iloc[:2].isna().all()             # 历史不足不给数
    assert out.iloc[4] == pytest.approx(1.0)     # 末值最大 → 之前 4 个全低于它
    assert dc.rolling_pctile(pd.Series([5.0, 4.0, 3.0]), win=3, min_periods=3).iloc[2] == 0.0


# ───────────────────────── NW 对自相关的控制 + 变异探针 ─────────────────────────


def _ar_false_rejection_rate(lag: int, n_draws: int = 40, n: int = 300) -> float:
    """两条**互相独立**的 AR(1) 序列回归 —— 真斜率为 0,|t|>2 的比例就是假阳率。"""
    hits = 0
    for seed in range(n_draws):
        rng = np.random.default_rng(1000 + seed)
        def ar1(rng=rng, phi=0.92):
            e = rng.normal(size=n)
            out = np.zeros(n)
            for i in range(1, n):
                out[i] = phi * out[i - 1] + e[i]
            return out
        x, y = ar1(), ar1()
        res = dc.nw_ols(y, np.column_stack([np.ones(n), x]), lag=lag)
        t = res["t"][1]
        if t is not None and abs(t) > dc.POS_T:
            hits += 1
    return hits / n_draws


def test_nw_controls_autocorrelation_false_rejections():
    """NW(lag=5)的假阳率必须低于裸 OLS(lag=0)—— 否则 t 门槛量的是自相关不是信号。"""
    assert _ar_false_rejection_rate(dc.NW_LAG) < _ar_false_rejection_rate(0)


def test_mutation_nw_lag_zero_inflates_t():
    """变异探针:把 NW lag 改成 0,假阳率必须显著抬头(≥0.25),否则上面那条断言是死灯。"""
    assert _ar_false_rejection_rate(0) >= 0.25


# ───────────────────────── 有效起点验定 ─────────────────────────


def _qvix_frame(n_head_nan=1200, n_valid=800, zeros_at=()):
    """合成 akshare 谎报起点的形状:整段 NaN 回填头 + 真实数据段。"""
    dates = pd.bdate_range("2015-02-09", periods=n_head_nan + n_valid).strftime("%Y%m%d")
    close = [np.nan] * n_head_nan + list(np.linspace(15.0, 25.0, n_valid))
    for i in zeros_at:
        close[n_head_nan + i] = 0.0
    return pd.DataFrame({"date": dates, "close": close})


def test_valid_start_ignores_backfilled_head():
    f = _qvix_frame()
    vs = dc.valid_start(f)
    assert vs == f["date"].iloc[1200]        # 起点 = 真实数据首日,不是接口自报的 2015-02-09
    assert vs > "20150209"


def test_valid_start_treats_zero_as_invalid():
    """0 不是波动率 —— 1000 指数序列里真夹着 34 个 0(§0.5)。"""
    f = _qvix_frame(n_head_nan=10, n_valid=200, zeros_at=(5,))
    assert dc.valid_start(f, min_run=60) == f["date"].iloc[16]   # 0 之后才起算


def test_valid_start_requires_min_run():
    f = _qvix_frame(n_head_nan=10, n_valid=30)
    assert dc.valid_start(f, min_run=60) is None                 # 短段不够 → 不给起点


# ───────────────────────── 年化基差 ─────────────────────────


def test_annualized_basis_sign_and_scale():
    assert dc.annualized_basis(3900.0, 4000.0, 30) == pytest.approx((3900 / 4000 - 1) * 365 / 30)
    assert dc.annualized_basis(4100.0, 4000.0, 90) > 0           # 升水为正


def test_annualized_basis_drops_near_expiry():
    """剩余 <7 日 → None:分母趋零会让年化值炸成一个每月准时出现一次的假信号。"""
    assert dc.annualized_basis(3900.0, 4000.0, 6) is None
    assert dc.annualized_basis(3900.0, 4000.0, 7) is not None
    assert dc.annualized_basis(3900.0, 0.0, 30) is None


def test_vrp_units():
    """QVIX 以百分点计:20 → 0.2² = 0.04 隐含方差。"""
    out = dc.vrp(pd.Series([20.0]), pd.Series([0.01]))
    assert out.iloc[0] == pytest.approx(0.04 - 0.01)


# ───────────────────────── 到期日历 ─────────────────────────


def test_expiry_flags_week_day_post():
    td = ["20260810", "20260811", "20260812", "20260813", "20260814",   # 周一→周五
          "20260817", "20260818"]
    out = dc.expiry_flags({"20260813"}, td).set_index("date")
    assert out.loc["20260813", "expiry_day"]
    assert out.loc[["20260810", "20260811", "20260812", "20260813"], "expiry_week"].all()
    assert not out.loc["20260814", "expiry_week"]      # 到期日之后的同周日子不算「到期周」
    assert out.loc["20260814", "post_expiry"]
    assert not out.loc["20260817", "expiry_week"]


def test_expiry_flags_ignores_non_trading_expiry():
    """到期日落在非交易日(或不在轴上)→ 不造旗,也不报错。"""
    out = dc.expiry_flags({"20260815"}, ["20260813", "20260814", "20260817"])
    assert not out[["expiry_day", "expiry_week", "post_expiry"]].to_numpy().any()


# ───────────────────────── 合成连续合约(最易静默出错的一处)─────────────────────────


def test_real_futures_regex_rejects_synthetic():
    """`IF.CFX` / `IFL1.CFX` 的 oi 是整族加总 —— 混进来会永远当选「最大 OI 主力」。"""
    assert dc._REAL_FUT.match("IF2609.CFX")
    assert dc._REAL_FUT.match("IM2212.CFX")
    for synth in ("IF.CFX", "IFL.CFX", "IFL1.CFX", "ICL3.CFX", "T.CFX", "TF2609.CFX"):
        assert not dc._REAL_FUT.match(synth), synth


# ───────────────────────── 档位 / 判读 ─────────────────────────


def _linked(n=600, effect=0.01, seed=3):
    """构造「信号高 → 次日 target 高」的合成对,用来验 bucket_hl 真能量出效应。"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n).strftime("%Y%m%d")
    s = pd.Series(rng.normal(size=n), index=idx)
    y = pd.Series(effect * s.to_numpy() + rng.normal(scale=0.002, size=n), index=idx)
    return s, y


def test_bucket_hl_measures_planted_effect():
    s, y = _linked()
    out = dc.bucket_hl(s, y, win=250, min_periods=120)
    assert out["n_h"] > 50 and out["n_l"] > 50
    assert out["diff"] > 0 and out["t"] > 2.0
    assert out["mean_h"] > out["mean_l"]


def test_bucket_hl_since_does_not_shrink_warmup():
    """`since` 只截判读行,不截暖机 —— 否则判读窗头 250 天全无分位。"""
    s, y = _linked(n=700)
    cut = s.index[300]
    full = dc.bucket_hl(s, y, win=250, min_periods=120)
    cut_run = dc.bucket_hl(s, y, since=cut, win=250, min_periods=120)
    assert cut_run["n_h"] + cut_run["n_l"] < full["n_h"] + full["n_l"]
    # 判读起点当天就该有档位(暖机没被一起砍掉)
    assert cut_run["n_h"] + cut_run["n_l"] > 0.4 * (len(s) - 300) * (dc.HI_Q + (1 - dc.LO_Q) - 1)


def test_verdict_needs_all_three_conditions():
    ok_hl = {"n_h": 150, "n_l": 150, "t": 3.0}
    assert dc.verdict(ok_hl, {"t": 2.5}) == dc.POS
    assert dc.verdict(ok_hl, {"t": 1.0}) == dc.UNPROVEN          # 基线增量不过 → 未证
    assert dc.verdict(ok_hl, {"t": -2.5}) == dc.UNPROVEN         # 符号相反 → 未证
    assert dc.verdict({"n_h": 10, "n_l": 150, "t": 9.0}, {"t": 9.0}) == dc.THIN
    assert dc.verdict(ok_hl, {"t": None}) == dc.UNPROVEN


def test_flag_verdict_uses_flag_threshold():
    hl = {"n_h": dc.MIN_FLAG_N + 1, "n_l": 400, "t": 2.5}
    assert dc.flag_verdict(hl, {"t": 2.5}) == dc.POS
    assert dc.verdict(hl, {"t": 2.5}) == dc.THIN                 # 同样的数在连续信号口径下算样本不足
    assert dc.flag_verdict({"n_h": 3, "n_l": 400, "t": 9.0}, {"t": 9.0}) == dc.THIN


def test_flag_hl_two_sample():
    idx = pd.bdate_range("2022-01-03", periods=200).strftime("%Y%m%d")
    flag = pd.Series([i % 20 == 0 for i in range(200)], index=idx)
    y = pd.Series([0.01 if f else 0.0 for f in flag], index=idx)
    out = dc.flag_hl(flag, y)
    assert out["n_h"] == 10 and out["n_l"] == 190
    assert out["diff"] == pytest.approx(0.01)


def test_tail_lift_detects_conditional_tail():
    idx = pd.bdate_range("2022-01-03", periods=100).strftime("%Y%m%d")
    flag = pd.Series([i < 20 for i in range(100)], index=idx)
    tail = pd.Series([i < 15 for i in range(100)], index=idx)     # 尾部全落在旗日里
    out = dc.tail_lift(flag, tail)
    assert out["n_flag"] == 20
    assert out["p_flag"] == pytest.approx(0.75)
    assert out["lift"] > 1.0 and out["fisher_p"] < 0.001


# ───────────────────────── 缓存:缺日不伪造 ─────────────────────────


def _fake_cache(tmp_path, monkeypatch, days, missing=()):
    root = tmp_path / "derivatives"
    monkeypatch.setattr(dc, "deriv_root", lambda: root)
    basic = pd.DataFrame({
        "ts_code": ["A.SH", "B.SH"], "call_put": ["C", "P"], "opt_code": ["OP510050.SH"] * 2,
        "maturity_date": ["20260828"] * 2, "per_unit": [10000.0] * 2,
        "exchange": ["SSE"] * 2, "name": ["购", "沽"], "delist_date": ["20260828"] * 2,
    })
    dc._save(basic, root / "opt_basic" / "SSE.parquet")
    for day in days:
        if day in missing:
            continue
        dc._save(pd.DataFrame({
            "ts_code": ["A.SH", "B.SH"], "trade_date": [day] * 2, "exchange": ["SSE"] * 2,
            "vol": [100.0, 60.0], "oi": [1000.0, 800.0], "amount": [1.0, 1.0],
        }), root / "opt_daily" / "SSE" / f"{day}.parquet")
    return root


def test_pcr_panel_skips_missing_days_without_faking(tmp_path, monkeypatch):
    days = ["20260817", "20260818", "20260819"]
    _fake_cache(tmp_path, monkeypatch, days, missing=("20260818",))
    panel, meta = dc.build_pcr_panel(days)
    assert sorted(panel["date"].unique()) == ["20260817", "20260819"]   # 缺的那天不出现
    assert "20260818" not in set(panel["date"])                          # 更不会伪造成 0
    assert panel["pcr_vol"].iloc[0] == pytest.approx(0.6)                # 60 put / 100 call
    assert panel["pcr_oi"].iloc[0] == pytest.approx(0.8)
    assert meta["coverage_missing_this_run"] == 0
    assert meta["panel_days"] == 2                                       # 面板规模与本次构建数分开报


def test_pcr_panel_is_incremental(tmp_path, monkeypatch):
    days = ["20260817", "20260818"]
    _fake_cache(tmp_path, monkeypatch, days)
    first, m1 = dc.build_pcr_panel(days)
    second, m2 = dc.build_pcr_panel(days)
    assert m1["days_built"] == 2 and m2["days_built"] == 0     # 第二次全部命中缓存
    assert len(first) == len(second)


def test_build_pcr_signals_names_dropped_products():
    """被数据质量门挡下的品种必须**点名**,不能悄悄消失(否则读数看起来「全覆盖」)。"""
    panel = pd.DataFrame({
        "date": [f"2026{i:04d}" for i in range(10)],
        "underlying": ["OP510050.SH"] * 10,
        "pcr_vol": [1.0] * 10, "pcr_oi": [1.0] * 10,
    })
    sigs, report = dc.build_pcr_signals(panel)
    assert sigs == []
    assert "OP510050.SH" in report["dropped"]
    assert str(dc.PCR_MIN_DAYS) in report["dropped"]["OP510050.SH"]["reason"]


def test_build_targets_alignment_and_clip():
    """`gap[T]` 必须是 open(T+2)/close(T+1)−1,且指数极值被剔除并计数。"""
    f = pd.DataFrame({
        "date": ["20260810", "20260811", "20260812", "20260813"],
        "open": [10.0, 11.0, 12.0, 13.0], "close": [10.5, 11.5, 12.5, 13.5],
    })
    out, n_clip = dc.build_targets(f)
    assert out["gap"].iloc[0] == pytest.approx(12.0 / 11.5 - 1)   # T=0810 → open(0812)/close(0811)
    assert pd.isna(out["gap"].iloc[2])                            # 末尾两天没有 T+2
    assert n_clip == 0                                            # +4.3% 在阈内,不该被当数据错
    big = pd.DataFrame({"date": ["a", "b", "c"], "open": [10.0, 10.0, 20.0],
                        "close": [10.0, 10.0, 10.0]})
    out2, n2 = dc.build_targets(big)
    assert n2 == 1 and pd.isna(out2["gap"].iloc[0])               # +100% 被当数据错剔除


# ───────────────────────── FDR 粘合(错位不会报错,只会让 q 挂到别人身上)─────────────────────────


def test_attach_fdr_maps_q_to_the_right_rows():
    table = pd.DataFrame({
        "signal": ["a", "b", "c", "d"],
        "p": [0.001, 0.90, 0.02, 0.001],
        "verdict": [dc.POS, dc.UNPROVEN, dc.UNPROVEN, dc.THIN],   # d 是样本不足,不进 FDR 池
    })
    out = dc.attach_fdr(table)
    assert pd.isna(out.loc[3, "fdr_q"])                   # 未判的格不占检验名额
    qs = [out.loc[i, "fdr_q"] for i in (0, 1, 2)]
    assert all(q is not None for q in qs)
    assert qs[0] < qs[2] < qs[1]                          # q 的序必须跟着各自的 p 走
    assert qs[0] == pytest.approx(0.003, abs=1e-9)        # BH:0.001 × 3/1


def test_attach_fdr_on_empty_and_all_thin():
    assert "fdr_q" in dc.attach_fdr(pd.DataFrame()).columns
    thin = pd.DataFrame({"signal": ["a"], "p": [0.01], "verdict": [dc.THIN]})
    assert dc.attach_fdr(thin)["fdr_q"].isna().all()


def test_render_never_prints_literal_nan():
    """pandas 把 None 列转成 NaN —— 渲染只判 `is None` 的话,读数表里会出现字面 `nan`。"""
    table = pd.DataFrame([{
        "family": "A1", "signal": "s", "target": "000300.SH", "n_h": 5, "n_l": 5,
        "mean_h": np.nan, "mean_l": None, "diff": np.nan, "t": None, "p": None,
        "inc_coef": None, "inc_t": np.nan, "inc_n": 0, "obs_oc_diff": np.nan,
        "obs_oc_t": None, "obs_dbreadth_diff": None, "obs_dbreadth_t": np.nan,
        "verdict": dc.THIN, "fdr_q": np.nan,
    }])
    meta = {"rule_version": "t", "judge_since": "20220302", "backfill_since": "20210101",
            "axis": {"n": 1, "first": "20220302", "last": "20260821"},
            "breadth": {"days": 1, "first": "20220302", "last": "20260821"},
            "gap_clipped": {}, "qvix": {}, "pcr": {}, "basis": {},
            "calendar": {"n_expiry_dates": 0, "flag_days_in_window": {}},
            "n_cells": 1, "n_judged": 0, "n_positive": 0,
            "thresholds": {"pctile_win": 250, "hi_q": 0.8, "lo_q": 0.2, "nw_lag": 5,
                           "pos_t": 2.0, "min_bucket_n": 100, "min_flag_n": 20,
                           "gap_clip": 0.08}}
    md = dc.render(table, [], meta)
    assert "nan" not in md.lower().replace("nan 一视同仁", "")

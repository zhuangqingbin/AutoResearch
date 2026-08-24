"""factor_lab IC / 十分位 / 层级收缩 / GBDT 数学 —— 离线自测(NO network, NO cache).

Ports the `_selftest` / `_selftest_shrink` / `_selftest_gbdt` assertions of
`autoresearch.research.factor_lab` into pytest now that the module lives in the package
(was `scripts/factor_lab.py --selftest`). Pure math on synthetic frames; the LightGBM
training portion is skipped if lightgbm is unavailable.
"""
from __future__ import annotations

import importlib.util

import numpy as np
import pandas as pd
import pytest

import autoresearch.research.factor_lab as fl
from autoresearch.common import ruler
from autoresearch.common.sw_sector_map import super_sector


def test_rank_ic_signal_vs_noise():
    """已知正相关 → IC 显著为正(~0.15–0.40);纯噪声 → IC≈0。"""
    rng = np.random.default_rng(7)
    n, days = 800, 20
    ics = []
    for _ in range(days):
        fac = rng.normal(size=n)
        ret = 0.3 * fac + rng.normal(size=n)  # 信噪 0.3
        ics.append(fl._spearman(pd.Series(fac), pd.Series(ret)))
    assert 0.15 < np.mean(ics) < 0.40, f"已知正相关 IC 均值异常: {np.mean(ics):.3f}"

    noise = [fl._spearman(pd.Series(rng.normal(size=n)), pd.Series(rng.normal(size=n)))
             for _ in range(days)]
    assert abs(np.mean(noise)) < 0.05, f"纯噪声 IC 偏离 0: {np.mean(noise):.3f}"


def test_spearman_below_min_n_is_nan():
    """有效样本 < 30 → NaN(避免小样本伪相关)。"""
    a = pd.Series(np.arange(10, dtype=float))
    assert np.isnan(fl._spearman(a, a))


def test_board_limit():
    """涨跌停板幅度:主板 10 / 科创(688)20 / 创业板(30)20 / 北交所(8/4/920)30。"""
    assert fl._board_limit("600519") == 10
    assert fl._board_limit("688111") == 20
    assert fl._board_limit("300750") == 20
    assert fl._board_limit("830799") == 30


def test_shrink_weights_hierarchy():
    """大样本贴自身 IC、小样本回落 parent、n=0 回落基准。"""
    w_big = fl._shrink_weights(0.10, 2000, 0.02, 0.0, k=200)
    w_small = fl._shrink_weights(0.10, 20, 0.02, 0.0, k=200)
    assert abs(w_big - 0.10) < abs(w_small - 0.10), \
        f"大样本应更贴自身 IC: big={w_big:.4f} small={w_small:.4f}"
    assert abs(fl._shrink_weights(0.9, 0, 0.0, 0.0, k=200)) < 1e-9, "n=0 应回落基准"


def _synth_features_frame(n: int = 600) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    return pd.DataFrame({
        "code": [f"{600000 + i:06d}" for i in range(n)], "industry": rng.choice(["A", "B", "C"], n),
        "pct_60d": rng.normal(size=n), "pct_ytd": rng.normal(size=n), "vol_ratio": rng.uniform(0.3, 5, n),
        "turnover": rng.uniform(0.1, 30, n), "winner_rate": rng.uniform(0, 100, n),
        "chip_concentration": rng.uniform(0.1, 2, n), "price_to_cost": rng.uniform(0.7, 1.5, n),
        "main_inflow_yi": rng.normal(size=n), "main_net_ratio": rng.normal(size=n) * 0.05,
        "retail_net_yi": rng.normal(size=n), "hk_ratio": rng.uniform(0, 30, n),
        "rsi6": rng.uniform(10, 95, n), "rsi12": rng.uniform(10, 95, n), "pe": rng.uniform(-50, 200, n),
        "pb": rng.uniform(0.5, 30, n), "dv_ratio": rng.uniform(0, 6, n), "cmf_20": rng.normal(size=n) * 0.2,
        "obv_mom_20": rng.normal(size=n) * 0.3, "ma_bull": rng.integers(0, 2, n).astype(float),
        "above_ma60": rng.integers(0, 2, n).astype(float),
    })


def test_gbdt_features_shape():
    """gbdt_features 列 = 8 组分位 g_* + GBDT_RAW + composite 锚定槽。"""
    n = 600
    feat = fl.gbdt_features(_synth_features_frame(n))
    exp_cols = len(fl.GBDT_GROUPS) + len(fl.GBDT_RAW) + 1   # +1 = composite 锚定特征
    assert feat.shape == (n, exp_cols), f"gbdt_features 形状 {feat.shape} 期望 ({n},{exp_cols})"


def test_predict_scores_missing_model_falls_back_none():
    """模型文件缺失 → predict_scores 返回 None(调用方回落线性)。"""
    df = _synth_features_frame()
    assert fl.predict_scores(df, model_path="context/factor_lab/__nonexistent__.pkl") is None


@pytest.mark.skipif(importlib.util.find_spec("lightgbm") is None, reason="lightgbm 不可用")
def test_gbdt_learns_synthetic_signal():
    """合成可学信号(y 与 g_momentum 正相关)→ oos IC > 0.1(GBDT 真在学,非噪声)。"""
    import lightgbm as lgb
    n = 600
    feat = fl.gbdt_features(_synth_features_frame(n))
    rng = np.random.default_rng(11)
    sig = feat["g_momentum"].fillna(0.5).to_numpy()
    y = sig + rng.normal(scale=0.5, size=n)
    cut = int(n * 0.7)
    dtr = lgb.Dataset(feat.iloc[:cut], label=y[:cut])
    m = lgb.train({"objective": "regression", "num_leaves": 15, "min_data_in_leaf": 30,
                   "verbosity": -1, "seed": 7}, dtr, num_boost_round=80)
    ic = fl._spearman(pd.Series(m.predict(feat.iloc[cut:])), pd.Series(y[cut:]))
    assert ic > 0.1, f"合成信号 oos IC 偏低 {ic:.3f}"


def test_forward_returns_missing_future_column_degrades_to_nan():
    """P 含日历交易日但 pivot 缺该列(当日 EOD 未发布)→ 对应 fwd 降级 NaN,不抛 KeyError。

    真实场景:D+5=今天,盘中跑 retro,daily 缓存只到昨天 → load_price_pivots 无该列。
    """
    codes = ["000001", "600000"]
    P = ["20260625", "20260626", "20260629", "20260630", "20260701", "20260702"]
    have = P[:-1]  # 最后一个交易日 EOD 未发布

    def piv_of(base):
        return pd.DataFrame({d: base + i for i, d in enumerate(have)}, index=codes)

    piv = {"close": piv_of(10.0), "open": piv_of(9.8), "high": piv_of(10.5), "low": piv_of(9.6),
           "pct_chg": pd.DataFrame(dict.fromkeys(have, 1.0), index=codes)}
    res = fl.forward_returns(piv, P, "20260625", 10)
    assert res["fwd_5_oc"].isna().all()      # 需 P[5]=20260702 close → 缺列 → NaN
    assert res["fwd_1_oo"].notna().all()     # 只用 26/29 开盘 → 有数


# ── _cache 空结果护栏(2026-07-09:20260708.pkl 空 pickle 毒化 07-06 复盘) ──


def _fake_fetch(df):
    return lambda: df


def test_cache_daily_empty_is_not_persisted(tmp_path, monkeypatch):
    """`daily` 调用点只喂交易日 → 空结果必是拉取失败,不许落盘(否则永不重拉)。"""
    monkeypatch.setattr(fl, "CACHE", tmp_path)
    monkeypatch.setattr(fl, "time", type("T", (), {"sleep": staticmethod(lambda _: None)}))

    got = fl._cache("daily", "20260708", _fake_fetch(pd.DataFrame()))
    assert got.empty
    assert not (tmp_path / "daily" / "20260708.pkl").exists(), "空 daily 被缓存 = 毒化"

    # 下次拉到真数据 → 正常落盘
    real = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260708"]})
    got = fl._cache("daily", "20260708", _fake_fetch(real))
    assert len(got) == 1
    assert (tmp_path / "daily" / "20260708.pkl").exists()


def test_cache_daily_purges_poisoned_empty_pickle(tmp_path, monkeypatch):
    """已存在的空 daily pickle(历史毒化)→ 读时清掉并重拉,而不是返回空。"""
    monkeypatch.setattr(fl, "CACHE", tmp_path)
    monkeypatch.setattr(fl, "time", type("T", (), {"sleep": staticmethod(lambda _: None)}))
    fp = tmp_path / "daily" / "20260708.pkl"
    fp.parent.mkdir(parents=True)
    pd.DataFrame(columns=["ts_code", "trade_date"]).to_pickle(fp)   # 毒化现场

    real = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260708"]})
    got = fl._cache("daily", "20260708", _fake_fetch(real))
    assert len(got) == 1, "毒化空 pickle 未被清除 → 仍返回空"
    assert len(pd.read_pickle(fp)) == 1


def test_cache_other_endpoints_still_cache_empty(tmp_path, monkeypatch):
    """非 daily 端点空结果是真相(无权限/当日无数据)→ 仍缓存,避免每次重拉。"""
    monkeypatch.setattr(fl, "CACHE", tmp_path)
    monkeypatch.setattr(fl, "time", type("T", (), {"sleep": staticmethod(lambda _: None)}))

    fl._cache("hk_hold", "20260619", _fake_fetch(pd.DataFrame()))
    assert (tmp_path / "hk_hold" / "20260619.pkl").exists(), "非 daily 空结果应缓存"


def test_forward_returns_fwd2_hi2():
    """超短主尺两窗:fwd_2_oc=close[D+2]/open[D+1]−1;hi_2_oc=max(high[D+1..D+2])/open[D+1]−1。"""
    P = ["20260701", "20260702", "20260703", "20260706", "20260707", "20260708"]
    codes = ["000001", "600519"]

    def _piv(rows):
        return pd.DataFrame(rows, index=codes, columns=P, dtype=float)

    piv = {
        "open":    _piv([[10, 10.5, 11.0, 11.5, 12, 12.5], [100, 101, 102, 103, 104, 105]]),
        "close":   _piv([[10.2, 10.8, 11.55, 11.6, 12.1, 12.6], [100.5, 101.5, 103, 103.5, 104.5, 105.5]]),
        "high":    _piv([[10.3, 11.0, 11.9, 11.7, 12.2, 12.7], [101, 102, 104, 104, 105, 106]]),
        "low":     _piv([[9.8, 10.3, 10.8, 11.3, 11.8, 12.3], [99.5, 100.5, 101.5, 102.5, 103.5, 104.5]]),
        "pct_chg": _piv([[1, 2, 3, 1, 1, 1], [1, 1, 1, 1, 1, 1]]),
    }
    fr = fl.forward_returns(piv, P, "20260701", fwd=10)
    # D=07-01 → o1=open[07-02];fwd_2_oc 用 close[07-03];hi_2 用 high[07-02..07-03]
    assert np.isclose(fr.loc["000001", "fwd_2_oc"], 11.55 / 10.5 - 1.0)
    assert np.isclose(fr.loc["000001", "hi_2_oc"], 11.9 / 10.5 - 1.0)
    assert np.isclose(fr.loc["600519", "fwd_2_oc"], 103 / 101 - 1.0)
    assert np.isclose(fr.loc["600519", "hi_2_oc"], 104 / 101 - 1.0)
    # 强化:检查 FWDS 成员与 hi_2_oc 不在 FWDS(触价指标不是收益,不得进 IC 循环)
    assert fl.FWDS == ["fwd_1_cc", "fwd_1_oo", "fwd_2_oc", "fwd_5_oc", "fwd_10_oc", "gap_c1_o2"]
    assert "hi_2_oc" not in fl.FWDS


def test_ultrashort_label_defaults():
    """主尺契约:校准/GBDT label 默认跟随 MAIN_RULER —— 现 gap_c1_o2(2026-08-05 用户裁定
    T16 换值,取代 2026-07-10 的 fwd_2_oc);IC 表主排序同尺。

    末两行是 Wave12-T4 补的**真断言**:此前 docstring 就写着「IC 表主排序同尺」,测试体却
    只锁了 4 个 label 默认值、一条都没锁排序列 —— 假锁,而 `evaluate` 里 sortcol 至今写死
    `ICIR_fwd_2_oc`(因子晋升 100% 由旧尺裁决)。
    """
    import inspect

    import autoresearch.research.factor_lab as fl

    assert inspect.signature(fl.calibrate).parameters["label_col"].default == "gap_c1_o2"
    assert inspect.signature(fl.calibrate_regimes).parameters["label_col"].default == "gap_c1_o2"
    assert inspect.signature(fl._build_calib_panel).parameters["label_col"].default == "gap_c1_o2"
    assert fl.GBDT_LABEL == "gap_c1_o2"
    assert fl.promotion_sortcol() == "ICIR_gap_c1_o2", "IC 表主排序列必须是主尺的 ICIR"
    assert fl.judgment_rulers()[0] == "gap_c1_o2", "判据族第一位(主位)必须是主尺"


def test_build_calib_panel_buyable_follows_label_col_entry_flag(monkeypatch):
    """C1 修复(final-review 2026-08-08):`_build_calib_panel` 的样本口径必须跟随
    `label_col` 选入场旗——**这是 T16 重校准权重吃的那份面板**。gap_c1_o2 分支要用
    `buyable_c1`(T+1 收盘旗),不是 `buyable`(T+1 开盘旗);修复前恒用 "buyable" 字面量,
    换尺后系统性纳入"收盘封死买不进"的票(review §9 实测 +1.23pp 偏差)。fwd_2_oc 分支
    (入场腿仍是 D+1 开盘)不受影响,用来做对照。
    """
    import autoresearch.common.regime as regime_mod
    import autoresearch.common.scoring as scoring_mod

    class _Regime:
        label = "range"

    monkeypatch.setattr(regime_mod, "classify_regime", lambda fr: _Regime())
    monkeypatch.setattr(scoring_mod, "_factor_groups",
                        lambda fr: {"a": pd.Series([0.1, 0.2, 0.3], index=fr.index)})

    fr = pd.DataFrame({
        "industry": ["半导体", "半导体", "半导体"],
        "date": ["2026-08-01"] * 3,
        "gap_c1_o2": [0.05, 0.06, 0.07],
        "fwd_2_oc": [0.05, 0.06, 0.07],
        "buyable": [True, True, True],           # 旧旗全放行
        "buyable_c1": [True, True, False],       # 新旗:第三只收盘封死,拦下
    })

    panel_gap, _ = fl._build_calib_panel([fr], label_col="gap_c1_o2")
    assert len(panel_gap) == 2, ("buyable_c1=False 的那只必须被剔出面板"
                                 "(C1 修复前会被旧 buyable 误放行)")

    panel_oc, _ = fl._build_calib_panel([fr], label_col="fwd_2_oc")
    assert len(panel_oc) == 3, "fwd_2_oc 分支入场腿仍是 D+1 开盘(buyable),不受 buyable_c1 影响"


def test_forward_returns_hi2_nan_when_d2_missing():
    """D+2 EOD 未发布 → hi_2_oc 必须 NaN(与 fwd_2_oc 成熟配对),不得用 high[D+1] 冒充。"""
    codes = ["000001", "600000"]
    P = ["20260625", "20260626", "20260629", "20260630", "20260701", "20260702"]
    have = P[:-1]  # 最后一个交易日 EOD 未发布

    def piv_of(base):
        return pd.DataFrame({d: base + i for i, d in enumerate(have)}, index=codes)

    piv = {"close": piv_of(10.0), "open": piv_of(9.8), "high": piv_of(10.5), "low": piv_of(9.6),
           "pct_chg": pd.DataFrame(dict.fromkeys(have, 1.0), index=codes)}
    # D=06-30 → D+1=07-01(有数)、D+2=07-02(缺)→ 两列必须整列 NaN,不得降级 1 日读数
    res = fl.forward_returns(piv, P, "20260630", 10)
    assert res["hi_2_oc"].isna().all(), "D+2 缺列 → hi_2_oc 必须全 NaN"
    assert res["fwd_2_oc"].isna().all(), "D+2 缺列 → fwd_2_oc 必须全 NaN"


# ── 反转确认三因子(vol_ratio_20/dist_low_60/days_no_new_low,Plan A1-T2:2026-07-11) ──


def test_reversal_confirm_vol_ratio_20_value():
    """vol_ratio_20 = D 日成交额 / 近 20 个交易日(含 D)成交额均值;量能骤增 → 比值数值精确。"""
    P = [f"e{i}" for i in range(1, 22)]          # 21 个交易日,D = 最后一日
    amount = pd.DataFrame([[1.0] * 20 + [3.0]], index=["A"], columns=P)
    piv = {"amount": amount, "low": amount, "close": amount}   # low/close 本测试不用,占位同形状
    out = fl.reversal_confirm_factors(piv, P, "e21")
    # 窗口 = e2..e21(19 个 1.0 + 1 个 3.0),均值 (19+3)/20=1.1;3.0/1.1
    assert np.isclose(out.loc["A", "vol_ratio_20"], 3.0 / 1.1)


def test_reversal_confirm_dist_low_60_nonneg_and_value():
    """dist_low_60 = (close[D]/60 日内最低价 − 1)×100;现价不低于历史低点 → 恒非负,数值精确。"""
    P = ["d1", "d2", "d3", "d4", "d5", "d6"]
    low = pd.DataFrame([[10, 9, 9.5, 9.2, 9.0, 9.1]], index=["A"], columns=P, dtype=float)
    amount = pd.DataFrame([[1.0] * 6], index=["A"], columns=P)
    piv = {"low": low, "close": low, "amount": amount}
    out = fl.reversal_confirm_factors(piv, P, "d6")
    assert out.loc["A", "dist_low_60"] >= 0, "现价不低于 60 日低点 → dist_low_60 恒非负"
    assert np.isclose(out.loc["A", "dist_low_60"], (9.1 / 9.0 - 1.0) * 100)


def test_reversal_confirm_days_no_new_low_boundary():
    """边界:①窗口首个可得交易日恒创新低(min_periods=1 平凡)→ 0(防越界/防误判无穷);
    ②D 当日本身创新低 → 0;③平历史低点算创新低,截断计数;④多股向量化互不干扰。"""
    P = ["d1", "d2", "d3", "d4", "d5", "d6"]
    low = pd.DataFrame(
        [[10, 9, 9.5, 9.2, 9.0, 9.1],     # A: d2 创新低、d5 平历史低(=9)、d6 未创新低
         [5, 6, 7, 8, 9, 10]],            # B: 只 d1 创新低,此后单调走高
        index=["A", "B"], columns=P, dtype=float)
    amount = pd.DataFrame([[1.0] * 6] * 2, index=["A", "B"], columns=P)
    piv = {"low": low, "close": low, "amount": amount}

    out_d1 = fl.reversal_confirm_factors(piv, P, "d1")
    assert out_d1.loc["A", "days_no_new_low"] == 0, "窗口首个可用交易日恒创新低(边界,防越界/防误判无穷)"

    out_d2 = fl.reversal_confirm_factors(piv, P, "d2")
    assert out_d2.loc["A", "days_no_new_low"] == 0, "D 当日本身创新低 → 0"

    out_d6 = fl.reversal_confirm_factors(piv, P, "d6")
    assert out_d6.loc["A", "days_no_new_low"] == 1, "d5 平历史低点截断计数,d6 未创新低 → 仅 1 天"
    assert out_d6.loc["B", "days_no_new_low"] == 5, "B 只 d1 创新低,此后 d2..d6 共 5 天未再创新低"


def test_reversal_confirm_factors_registered_in_candidates():
    """三因子须挂进 CANDIDATES(IC 三门读数的唯一入口),防漏挂/防手滑改名。"""
    cand = dict(fl.CANDIDATES)
    for name in ("vol_ratio_20", "dist_low_60", "days_no_new_low"):
        assert name in cand, f"{name} 未注册进 CANDIDATES(IC 三门验证读不到它)"


# ───────────────── extend_plan(pr_20260716_001:面板增量续,勿重跑 harvest) ─────────────────


def _mk_plan(out_dir, F, P):
    import pandas as pd
    pd.Series({"F": F, "P": P, "end_anchor": "2026-07-15", "step": 1,
               "form_span": 24, "back": 64, "fwd": 10}).to_pickle(out_dir / "plan.pkl")


def _wire_extend(monkeypatch, tmp_path, cal, fetched):
    """OUT/CACHE 指向 tmp;日历与取数全 fake(离线)。fetched 收集 (endpoint, day)。"""
    import pandas as pd

    import autoresearch.research.factor_lab as fl
    out, cache = tmp_path / "out", tmp_path / "cache"
    out.mkdir(), cache.mkdir()
    monkeypatch.setattr(fl, "OUT", out)
    monkeypatch.setattr(fl, "CACHE", cache)
    monkeypatch.setattr(fl, "_pro", lambda: object())
    monkeypatch.setattr("autoresearch.data.tushare_source._trade_days",
                        lambda pro, s, e: [d for d in cal if s <= d <= e])
    # 真 _fetch 返回「可调用」(lambda),_cache 只在缓存 miss 时才调它 → fetched 只记真拉取
    monkeypatch.setattr(fl, "_fetch", lambda pro, ep, d: (lambda: fetched.append((ep, d)) or pd.DataFrame(
        {"ts_code": ["000001.SZ"], "open": [1.0], "high": [1.0], "low": [1.0],
         "close": [1.0], "pct_chg": [0.0], "amount": [1.0]})))
    return fl, out, cache


def test_extend_plan_appends_f_and_p_with_holdback_2(tmp_path, monkeypatch):
    """新成型日推进到 last−2(对齐 fwd_2_oc 主尺,不被旧参 fwd=10 拖到 last−10);P 推进到 last。"""
    cal = [f"202607{d:02d}" for d in (1, 2, 3, 6, 7, 8, 9, 10, 13, 14, 15, 16, 17)]
    fetched = []
    fl, out, cache = _wire_extend(monkeypatch, tmp_path, cal, fetched)
    _mk_plan(out, F=["20260701"], P=["20260701", "20260702"])
    res = fl.extend_plan(anchor="2026-07-17")
    assert res["f_last"] == "20260715" and res["added_f"] == 10         # 07-02..07-15(留 16/17 做 holdback)
    assert res["added_p"] == 11 and res["n_f"] == 11
    import pandas as pd
    plan = pd.read_pickle(out / "plan.pkl")
    assert plan["F"][-1] == "20260715" and plan["P"][-1] == "20260717"
    assert plan["F"][0] == "20260701" and len(plan["F"]) == 11          # 历史面板没被冲掉
    assert plan["end_anchor"] == "2026-07-17"
    assert ("moneyflow", "20260715") in fetched                          # 新成型日拉了因子端点
    assert (cache / "daily" / "20260717.pkl").exists()


def test_extend_plan_idempotent_and_heals_holes(tmp_path, monkeypatch):
    """再跑一次 = 0 新增(幂等);P 里缓存缺失的洞会被回补(自愈)。"""
    cal = ["20260701", "20260702", "20260703", "20260706", "20260707"]
    fetched = []
    fl, out, cache = _wire_extend(monkeypatch, tmp_path, cal, fetched)
    _mk_plan(out, F=["20260701"], P=["20260701", "20260702", "20260703"])
    r1 = fl.extend_plan(anchor="2026-07-07")
    assert r1["added_f"] == 2 and r1["added_p"] == 2                     # F→07-03,P→07-07
    assert r1["healed"] == 3                                             # 旧 P 三日缓存本来就缺 → 回补
    n_before = len(fetched)
    r2 = fl.extend_plan(anchor="2026-07-07")
    assert r2["added_f"] == 0 and r2["added_p"] == 0 and r2["healed"] == 0
    assert len(fetched) == n_before                                      # 幂等:一个端点都没重拉


def _day_rows(date: str, regime: str, sign_a: int, sign_b: int, n: int = 30) -> pd.DataFrame:
    """单日 n 行(≥30,`_spearman` 的最小样本门槛):grp_a/grp_b 与 fwd 的相关性符号可独立指定
    (grp_x = fwd 若 sign_x>0,否则 = 倒序 fwd)—— 保证当日 IC 恰好是 +1.0 或 -1.0,无噪声。
    """
    fwd = np.arange(n, dtype=float)
    grp_a = fwd if sign_a > 0 else (n - 1 - fwd)
    grp_b = fwd if sign_b > 0 else (n - 1 - fwd)
    return pd.DataFrame({"grp_a": grp_a, "grp_b": grp_b, "fwd": fwd, "industry": "半导体",
                         "sector": super_sector("半导体"), "date": date, "regime": regime})


def _steady_and_onehalf_panel() -> pd.DataFrame:
    """20 日面板:`onehalf`(5 日,全落最早日期→排序后整桶落前半)+ `steady`(15 日,横跨两半、
    两半符号一致 a=+1/b=-1)。切半点 mid=10:前半= onehalf 全 5 日 + steady 前 5 日;
    后半= steady 后 10 日。`onehalf` 在后半 0 日 → 两个规律都达标(≥5)的只有 `steady`。"""
    rows = [_day_rows(f"2026010{i + 1}", "onehalf", sign_a=1, sign_b=1) for i in range(5)]
    rows += [_day_rows(f"202602{i + 1:02d}", "steady", sign_a=1, sign_b=-1) for i in range(15)]
    return pd.concat(rows, ignore_index=True)


def test_split_half_regime_gate_validates_consistent_regime_and_ignores_single_half_regime():
    panel = _steady_and_onehalf_panel()
    validated, detail = fl.split_half_regime_gate(panel, k=200.0, min_dates=5)

    assert validated == {"steady"}
    assert "onehalf" not in detail, "两半里只有一半有数据的 regime,门判不了它,不该出现在 detail 里(不是判了不过)"
    assert detail["steady"] == {"comparable": 2, "agree": 2, "rate": 1.0}


def test_split_half_regime_gate_rejects_regime_when_majority_of_signs_flip():
    """`flippy`:前 6 日 (a=+1,b=+1),后 6 日 (a=-1,b=+1) —— grp_a 翻号、grp_b 不翻,
    一致率 1/2=50%,不满足严格多数(>50%)门槛 → 不过门,但读数如实留在 detail 里。"""
    rows = [_day_rows(f"202603{i + 1:02d}", "flippy", sign_a=1, sign_b=1) for i in range(6)]
    rows += [_day_rows(f"202604{i + 1:02d}", "flippy", sign_a=-1, sign_b=1) for i in range(6)]
    panel = pd.concat(rows, ignore_index=True)

    validated, detail = fl.split_half_regime_gate(panel, k=200.0, min_dates=5)

    assert validated == set()
    assert detail["flippy"] == {"comparable": 2, "agree": 1, "rate": 0.5}


def test_split_half_regime_gate_too_few_total_dates_returns_empty():
    """总日数 < 2×min_dates → 连切半都没意义,直接空(不是硬凑一个假门槛)。"""
    panel = pd.concat([_day_rows(f"2026010{i + 1}", "steady", 1, 1) for i in range(4)], ignore_index=True)
    validated, detail = fl.split_half_regime_gate(panel, k=200.0, min_dates=5)
    assert validated == set() and detail == {}


def test_calibrate_regimes_require_split_half_filters_unvalidated_into_pending(tmp_path, monkeypatch):
    """calibrate_regimes(require_split_half=True,默认)真的把两半门接上了:
    过门的桶(steady)落盘进 regimes;判不了/没过的桶(onehalf)不落盘,记进 meta.regimes_pending。
    """
    import json

    panel = _steady_and_onehalf_panel()
    regime_by_date = {d: sub["regime"].iloc[0] for d, sub in panel.groupby("date")}
    panel_no_regime = panel.drop(columns=["regime"])

    monkeypatch.setattr(fl, "_all_frames", lambda cap_floor: [pd.DataFrame({"x": [1]})])  # 只需非空
    monkeypatch.setattr(fl, "_build_calib_panel",
                        lambda frames, label_col: (panel_no_regime, regime_by_date))

    out_path = tmp_path / "weights.json"
    result = fl.calibrate_regimes(out_path=str(out_path), min_dates=5)

    assert result["meta"]["regimes_present"] == ["steady"]
    assert result["meta"]["regimes_pending"] == ["onehalf"]
    assert set(result["regimes"]) == {"steady"}
    assert result["meta"]["split_half_gate"]["steady"]["rate"] == 1.0
    assert "onehalf" not in result["meta"]["split_half_gate"]

    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert set(saved["regimes"]) == {"steady"}, "写盘的 weights.json 不能含未过门的桶"


def test_calibrate_regimes_require_split_half_false_keeps_old_behavior(tmp_path, monkeypatch):
    """`require_split_half=False`(仅供研究/对照)退回旧行为:单桶达标即落盘,不经两半门。"""
    panel = _steady_and_onehalf_panel()
    regime_by_date = {d: sub["regime"].iloc[0] for d, sub in panel.groupby("date")}
    panel_no_regime = panel.drop(columns=["regime"])

    monkeypatch.setattr(fl, "_all_frames", lambda cap_floor: [pd.DataFrame({"x": [1]})])
    monkeypatch.setattr(fl, "_build_calib_panel",
                        lambda frames, label_col: (panel_no_regime, regime_by_date))

    out_path = tmp_path / "weights.json"
    result = fl.calibrate_regimes(out_path=str(out_path), min_dates=5, require_split_half=False)

    assert set(result["regimes"]) == {"steady", "onehalf"}
    assert result["meta"]["regimes_pending"] == []


# ═════════════ 因子晋升判据族切主尺(Wave12-T4 / 设计稿 A2)═════════════
#
# 2026-08-05 用户裁定换尺(fwd_2_oc → gap_c1_o2)后,「某因子该不该进 composite」这一面
# 的整族判据(IC 均值 / ICIR / t / hit / 前后两半同号)仍**只**对旧尺产出、主排序还写死
# `ICIR_fwd_2_oc` —— 晋升 100% 由旧尺裁决。这里锁四件事:
#   ① 判据族(含无后缀主位列 t/hit/IC_h1/IC_h2/n_days)绑 `ruler.MAIN_RULER`;
#   ② 旧尺整族保留为**并列参考**(带尺后缀,列名不删,只是不在主位);
#   ③ 主排序 sortcol 跟随 MAIN_RULER(回滚 MAIN_RULER 后排序真的翻过来);
#   ④ 十分位主表的资格旗与截尾阈也跟尺走(entry_tradable(ruler_name=…) / GAP_CLIP)。


def _dual_ruler_frames(n_days: int = 6, n: int = 120) -> list[pd.DataFrame]:
    """双尺合成面板:`gap_c1_o2` 与 `fwd_2_oc` 次序**相反** → 同一因子在两尺下 IC 符号相反。

    单调构造 → 每日 rank IC 精确 ±1.0(秩相关不受幅度影响),t/hit/两半的期望值可手算:
      * `pct_60d`(sign +1,值=base):对 gap IC=+1.0、对 fwd_2_oc IC=−1.0
      * `pe`(sign −1,取向后=−base):对 gap IC=−1.0、对 fwd_2_oc IC=+1.0
    两只入场旗全放行(资格口径另有专测),免得与排序断言纠缠。
    """
    base = np.arange(n, dtype=float)
    return [pd.DataFrame({
        "code": [f"{600000 + i:06d}" for i in range(n)],
        "pct_60d": base, "pe": base,
        "gap_c1_o2": base / 1000.0, "fwd_2_oc": -base / 1000.0,
        "buyable": True, "buyable_c1": True,
        "date": f"2026070{d + 1}",
    }) for d in range(n_days)]


def test_eval_promotion_family_on_main_ruler():
    """判据族(t/hit/两半/n_days)必须对主尺产出并占主位;旧尺同族降为带后缀的并列参考。"""
    tbl = fl.ic_promotion_table(_dual_ruler_frames(), buyable_only=True).set_index("factor")
    main = ruler.MAIN_RULER                      # 现 gap_c1_o2

    for stat in ("t", "hit", "IC_h1", "IC_h2", "n_days"):
        assert f"{stat}_{main}" in tbl.columns, f"{stat} 未对主尺 {main} 产出"
    assert f"ICIR_{main}" in tbl.columns
    # 自述列只在主尺真有读数时才填(M-2):合成面板里只有 pct_60d/pe 两个因子有列,
    # 其余 CANDIDATES 整行无读数 —— 它们不得自称"由主尺裁决"。
    has_main = tbl[f"ICIR_{main}"].notna()
    assert has_main.sum() == 2, "合成面板应只有 2 个因子有主尺读数"
    assert (tbl.loc[has_main, "ruler"] == main).all(), "有读数的行未自报主尺"
    assert tbl.loc[~has_main, "ruler"].isna().all(), "主位全空的行不得自称由主尺裁决"

    row = tbl.loc["pct_60d"]
    # 主位无后缀列 = 主尺读数。切尺前它们是 fwd_2_oc 的读数(−1.0 / 0.0)→ 此处必红。
    assert row["IC_h1"] == 1.0 and row["IC_h2"] == 1.0, "两半稳定性没绑主尺"
    assert row["hit"] == 1.0 and row["n_days"] == 6
    assert row["t"] > 0 and row["IC_gap_c1_o2"] == 1.0
    # 旧尺整族保留(降参考不删):同一因子在旧尺下方向相反,读数照旧摆在表里
    assert row["IC_fwd_2_oc"] == -1.0 and row["IC_h1_fwd_2_oc"] == -1.0
    assert row["hit_fwd_2_oc"] == 0.0 and row["n_days_fwd_2_oc"] == 6

    ordered = tbl.dropna(subset=[f"ICIR_{main}"]).index.tolist()
    assert ordered[0] == "pct_60d" and ordered[-1] == "pe", "主排序不是按主尺 ICIR 降序"


def test_eval_sortcol_follows_main_ruler(monkeypatch):
    """回滚杆:`MAIN_RULER` 改回 fwd_2_oc → sortcol/主位/排序整体跟着回,新尺对称降参考。"""
    monkeypatch.setattr(ruler, "MAIN_RULER", "fwd_2_oc")

    assert fl.promotion_sortcol() == "ICIR_fwd_2_oc"
    assert fl.judgment_rulers()[0] == "fwd_2_oc"

    tbl = fl.ic_promotion_table(_dual_ruler_frames(), buyable_only=True)
    ordered = tbl.dropna(subset=["ICIR_fwd_2_oc"])["factor"].tolist()
    assert ordered[0] == "pe" and ordered[-1] == "pct_60d", "排序没跟着 MAIN_RULER 翻过来"

    row = tbl.set_index("factor").loc["pct_60d"]
    assert row["ruler"] == "fwd_2_oc" and row["IC_h1"] == -1.0
    assert row["IC_h1_gap_c1_o2"] == 1.0, "回滚后新尺应对称降为并列参考,不是被删"


def _leg_frames(n: int = 40) -> list[pd.DataFrame]:
    """真价格面板 → `forward_returns` 出隔夜腿(不手写 gap 列)。

    构造:开盘随 code 序**递增**、收盘随 code 序**递减**(逐日系数各异,免得出现零方差列)
    → 正腿 `gap=o2/c1−1` 与因子同向(IC=+1.0),错腿 `c2/c1−1` 与因子反向 → 错腿变异探针在
    **判据族这一层**必红。
    """
    codes = [f"{600000 + i:06d}" for i in range(n)]
    b = np.arange(n, dtype=float) / 1000.0
    P = ["D0", "D1", "D2", "D3"]
    o = {"D0": 10.0 + 0 * b, "D1": 10.0 + 0 * b, "D2": 10.0 * (1 + b), "D3": 10.0 * (1 + 2 * b)}
    c = {"D0": 10.0 + 0 * b, "D1": 10.0 * (1 + 0.1 * b), "D2": 10.0 * (1 - b),
         "D3": 10.0 * (1 - 0.5 * b)}
    piv = {
        "open": pd.DataFrame(o, index=codes), "close": pd.DataFrame(c, index=codes),
        "high": pd.DataFrame({d: np.maximum(o[d], c[d]) + 1.0 for d in P}, index=codes),
        "low": pd.DataFrame({d: np.minimum(o[d], c[d]) - 1.0 for d in P}, index=codes),
        "pct_chg": pd.DataFrame({d: np.zeros(n) for d in P}, index=codes),
    }
    frames = []
    for D in ("D0", "D1"):
        fr = pd.DataFrame({"pct_60d": np.arange(n, dtype=float)}, index=codes)
        frames.append(fr.join(fl.forward_returns(piv, P, D, 10)))
    return frames


def test_promotion_family_reads_real_gap_leg():
    """判据族吃的是**真隔夜腿**(open[D+2]/close[D+1]):把腿算成 close/close 则 IC 反号必红。"""
    tbl = fl.ic_promotion_table(_leg_frames(), buyable_only=True).set_index("factor")
    assert tbl.loc["pct_60d", "IC_gap_c1_o2"] == 1.0, "隔夜腿取错(c2/c1)→ 这里会翻成负号/消失"
    assert tbl.loc["pct_60d", "IC_h2"] == 1.0 and tbl.loc["pct_60d", "n_days"] == 2


def _decile_frames(n_days: int = 3, n: int = 120, *, outlier_unbuyable: bool = False,
                   n_outliers: int = 12) -> list[pd.DataFrame]:
    """十分位面板(≥100 只/日,过 `m.sum() < 100` 护栏):因子=base,最高的 `n_outliers` 只
    前瞻收益 +0.9(远超两尺各自的截尾阈)。`outlier_unbuyable` = 让这些离群票「D+1 开盘买
    得到、T+1 收盘封死买不进」(buyable=True / buyable_c1=False),用来验资格旗跟尺走。
    """
    base = np.arange(n, dtype=float)
    ret = np.where(base >= n - n_outliers, 0.9, 0.001)
    return [pd.DataFrame({
        "code": [f"{600000 + i:06d}" for i in range(n)],
        "pct_60d": base, "gap_c1_o2": ret, "fwd_2_oc": ret,
        "buyable": True,
        "buyable_c1": np.where(outlier_unbuyable & (base >= n - n_outliers), False, True),
        "date": f"2026070{d + 1}",
    }) for d in range(n_days)]


def test_decile_clip_follows_ruler():
    """截尾阈跟尺走:隔夜尺用 `ruler.GAP_CLIP`(单日板极值+容差),多日 open→close 尺用 ±0.30。"""
    assert fl._return_clip("gap_c1_o2") == ruler.GAP_CLIP
    assert fl._return_clip("fwd_2_oc") == 0.30

    frames = _decile_frames()
    gap = fl.decile_table(frames, "gap_c1_o2").set_index("factor")
    oc = fl.decile_table(frames, "fwd_2_oc").set_index("factor")
    assert gap.loc["pct_60d", "top_decile_ret"] == round(ruler.GAP_CLIP * 100, 3)   # 31.0
    assert oc.loc["pct_60d", "top_decile_ret"] == 30.0
    assert (gap["ruler"] == "gap_c1_o2").all() and (oc["ruler"] == "fwd_2_oc").all()


def test_decile_entry_flag_follows_ruler():
    """资格旗跟尺走:隔夜尺剔「收盘封死买不进」(buyable_c1),旧尺仍按 D+1 开盘旗(buyable)。"""
    frames = _decile_frames(outlier_unbuyable=True, n_outliers=1)
    gap = fl.decile_table(frames, "gap_c1_o2").set_index("factor")
    oc = fl.decile_table(frames, "fwd_2_oc").set_index("factor")
    assert gap.loc["pct_60d", "top_decile_ret"] == 0.1, "隔夜尺没剔掉收盘封死的那只(旧旗漏放行)"
    assert oc.loc["pct_60d", "top_decile_ret"] > 2.0, "旧尺入场腿是 D+1 开盘,不该被 buyable_c1 拦"


def test_evaluate_writes_main_ruler_tables(tmp_path, monkeypatch, capsys):
    """接线(不只是有个纯函数):`evaluate()` 真把主尺判据表写进 ic_table.csv,
    十分位主表=主尺、旧尺另存并列参考文件。"""
    monkeypatch.setattr(fl, "OUT", tmp_path)
    monkeypatch.setattr(fl, "_all_frames", lambda cap_floor: _dual_ruler_frames())

    fl.evaluate(30.0, buyable_only=True)

    ic = pd.read_csv(tmp_path / "ic_table.csv").set_index("factor")
    assert ic.loc["pct_60d", "ruler"] == ruler.MAIN_RULER
    assert pd.isna(ic.loc["turnover", "ruler"]), "无主尺读数的行不得自称由主尺裁决"
    assert ic.index[0] == "pct_60d", "写盘的 ic_table 未按主尺 ICIR 降序"
    assert "IC_h1_fwd_2_oc" in ic.columns, "旧尺判据族必须保留在产物里(降参考不删列)"

    dec = pd.read_csv(tmp_path / "decile_table.csv")
    assert (dec["ruler"] == ruler.MAIN_RULER).all(), "十分位主表未切主尺"
    ref = pd.read_csv(tmp_path / "decile_table_fwd_2_oc.csv")
    assert (ref["ruler"] == "fwd_2_oc").all(), "旧尺十分位表未作为并列参考产出"
    assert ruler.MAIN_RULER in capsys.readouterr().out, "控制台读数未标尺"


def test_render_ic_by_regime_title_carries_ruler(tmp_path, monkeypatch):
    """标题按**实参**判定主尺/参考尺,且 `run_ic_by_regime` 把真用的 label_col 传下去。

    断言一律由 `ruler.MAIN_RULER` 现算(M-6):写死 `"fwd_2_oc" not in head` 那种形式在回滚杆
    (MAIN_RULER 改回 fwd_2_oc)下会自相矛盾必红 —— 测试不能自己造一个永远修不好的失败。
    """
    head = fl.render_ic_by_regime(pd.DataFrame()).splitlines()[0]
    assert f"主尺 {ruler.MAIN_RULER}" in head

    # 故意挑一把**不是**主尺的参考 horizon:标题必须标「参考尺」,不得冒充主尺
    ref = "fwd_5_oc" if ruler.MAIN_RULER != "fwd_5_oc" else "fwd_10_oc"
    ref_head = fl.render_ic_by_regime(pd.DataFrame(), ruler_name=ref).splitlines()[0]
    assert f"参考尺 {ref}" in ref_head and "主尺" not in ref_head

    panel = pd.concat([_day_rows(f"2026030{i + 1}", "steady", 1, -1) for i in range(6)],
                      ignore_index=True)
    regime_by_date = dict.fromkeys(panel["date"].unique(), "steady")
    monkeypatch.setattr(fl, "_all_frames", lambda cap_floor: [pd.DataFrame({"x": [1]})])
    monkeypatch.setattr(fl, "_build_calib_panel",
                        lambda frames, label_col: (panel.drop(columns=["regime"]), regime_by_date))

    fl.run_ic_by_regime(label_col=ref, out_csv=str(tmp_path / "ic.csv"),
                        out_md=str(tmp_path / "ic.md"))
    md_head = (tmp_path / "ic.md").read_text(encoding="utf-8").splitlines()[0]
    assert f"参考尺 {ref}" in md_head, "报表标题没跟着真用的 label_col 走(死字符串又长回来了)"


def _legacy_reversal_confirm_factors(piv: dict, P: list[str], D: str) -> pd.DataFrame:
    """2026-08-21 前的实现,逐字复制作对照(委托 turnup 后数值不得漂移)。"""
    idx = P.index(D)
    A, L, C = piv["amount"], piv["low"], piv["close"]
    codes = C.index
    out = pd.DataFrame(index=codes)
    win20 = P[max(0, idx - 19):idx + 1]
    denom20 = A.reindex(columns=win20).mean(axis=1).replace(0, np.nan)
    amtD = A.reindex(columns=[D]).iloc[:, 0]
    out["vol_ratio_20"] = amtD / denom20
    hist = P[:idx + 1]
    low_hist = L.reindex(columns=hist)
    roll_min60 = low_hist.T.rolling(60, min_periods=1).min().T
    low60_D = roll_min60.reindex(columns=[D]).iloc[:, 0]
    closeD = C.reindex(columns=[D]).iloc[:, 0]
    out["dist_low_60"] = (closeD / low60_D - 1.0) * 100
    is_new_low = (low_hist <= roll_min60 + 1e-9).to_numpy()
    rev = is_new_low[:, ::-1]
    out["days_no_new_low"] = rev.argmax(axis=1).astype(float)
    return out


def test_reversal_confirm_factors_delegation_is_value_identical():
    """委托 turnup.panel_factors 后三因子逐元素等于旧实现(含 NaN 位置;80 日 × 30 码随机面板)。"""
    rng = np.random.default_rng(7)
    P = [f"{20260101 + i}" for i in range(80)]
    codes = [f"{600000 + i:06d}" for i in range(30)]
    close = pd.DataFrame(rng.uniform(5, 50, (30, 80)), index=codes, columns=P)
    low = close * rng.uniform(0.95, 1.0, (30, 80))
    amount = pd.DataFrame(rng.uniform(0, 1e6, (30, 80)), index=codes, columns=P)
    amount.iloc[3, 70:] = np.nan                     # 缺值位置也要一致
    piv = {"close": close, "low": low, "amount": amount}
    for D in (P[0], P[19], P[59], P[79]):
        old = _legacy_reversal_confirm_factors(piv, P, D)
        new = fl.reversal_confirm_factors(piv, P, D)
        pd.testing.assert_frame_equal(new[old.columns], old, check_names=False)

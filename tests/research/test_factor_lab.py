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
    T16 换值,取代 2026-07-10 的 fwd_2_oc);IC 表主排序同尺。"""
    import inspect

    import autoresearch.research.factor_lab as fl

    assert inspect.signature(fl.calibrate).parameters["label_col"].default == "gap_c1_o2"
    assert inspect.signature(fl.calibrate_regimes).parameters["label_col"].default == "gap_c1_o2"
    assert inspect.signature(fl._build_calib_panel).parameters["label_col"].default == "gap_c1_o2"
    assert fl.GBDT_LABEL == "gap_c1_o2"


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


def test_recalibrate_and_log_calls_extend_before_calibrate(tmp_path, monkeypatch):
    """接线序:extend_plan 先于 calibrate(否则续了也白续);extend 炸不阻断 calibrate。"""
    calls = []
    import autoresearch.learning.feedback_store as fs
    import autoresearch.learning.retro as retro
    import autoresearch.research.factor_lab as fl
    wp = tmp_path / "weights.json"
    wp.write_text('{"weights": {"__global__": {}}, "meta": {"n_dates": 1}}', encoding="utf-8")
    monkeypatch.setattr(retro, "Path", lambda p: wp if "weights.json" in str(p) else __import__("pathlib").Path(p))
    monkeypatch.setattr(fl, "extend_plan", lambda: calls.append("extend") or {"added_f": 1, "added_p": 1, "healed": 0, "f_last": "20260715", "n_f": 108})
    monkeypatch.setattr(fl, "calibrate", lambda **k: calls.append("calibrate") or {})
    monkeypatch.setattr(fs, "snapshot_weights", lambda: "aaa")
    monkeypatch.setattr(fs, "log_change", lambda *a, **k: calls.append("log"))
    retro.recalibrate_and_log("2026-07-16")
    assert calls == ["extend", "calibrate", "log"]

    calls.clear()
    monkeypatch.setattr(fl, "extend_plan", lambda: (_ for _ in ()).throw(RuntimeError("网络断")))
    retro.recalibrate_and_log("2026-07-16")
    assert calls == ["calibrate", "log"]                                 # 退化但不死,探针兜底


# ═══════════════ split_half_regime_gate / calibrate_regimes(require_split_half) ═══════════════
#
# T16 review 修复(2026-08-07 用户裁定):单桶样本量够 min_dates 不等于该桶权重方向在时间上
# 稳定——trend/risk_off 两桶曾在未经两半符号一致门检验的情况下直接落盘。这里锁住两件事:
# ①门函数本身对「两半都达标+符号一致」「只在单半出现」「两半都达标但符号翻多数」三种情形
#   分别给出正确判定;②calibrate_regimes() 真的把门接上了(过门的桶落盘,没过/判不了的桶
#   进 meta.regimes_pending,不出现在 weights.json 的 regimes 里)。


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

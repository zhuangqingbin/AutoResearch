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

    piv = {"close": piv_of(10.0), "open": piv_of(9.8), "high": piv_of(10.5),
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
        "pct_chg": _piv([[1, 2, 3, 1, 1, 1], [1, 1, 1, 1, 1, 1]]),
    }
    fr = fl.forward_returns(piv, P, "20260701", fwd=10)
    # D=07-01 → o1=open[07-02];fwd_2_oc 用 close[07-03];hi_2 用 high[07-02..07-03]
    assert np.isclose(fr.loc["000001", "fwd_2_oc"], 11.55 / 10.5 - 1.0)
    assert np.isclose(fr.loc["000001", "hi_2_oc"], 11.9 / 10.5 - 1.0)
    assert np.isclose(fr.loc["600519", "fwd_2_oc"], 103 / 101 - 1.0)
    assert np.isclose(fr.loc["600519", "hi_2_oc"], 104 / 101 - 1.0)
    assert "fwd_2_oc" in fl.FWDS

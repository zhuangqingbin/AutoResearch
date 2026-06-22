"""DataHandler.materialize ↔ factor_lab.factor_frame **golden parity** (NO network).

Builds one small synthetic dataset and writes it BOTH as factor_lab pkls (tmp CACHE) AND
as lake parquet (tmp LAKE), then runs:
  * factor_lab.factor_frame(D, ...)         — current pipeline (reads pkl)
  * DataHandler().materialize([D], ...)      — new pipeline (reads lake parquet)
on the SAME data and asserts the shared core feature columns are numerically identical
(same `code` set, same fwd_1_oo, per-column max abs diff < 1e-9). This is the invariant
E3 relies on when comparing the two pipelines.

Also asserts the FEATURE_SETS["core"] column list matches factor_lab's live constants
(CANDIDATES / GBDT_GROUPS / GBDT_RAW) so the registry can't silently drift away.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import autoresearch.research.factor_lab as factor_lab
from autoresearch.data import cache
from autoresearch.data.features import LABEL, feature_columns
from autoresearch.data.handler import DataHandler

CAP_FLOOR = 30.0
FWD = 10


# ───────────────────────── synthetic raw dataset ─────────────────────────


def _trade_days(n: int) -> list[str]:
    """n consecutive business days (compact YYYYMMDD), the lake/pkl key form."""
    days = pd.bdate_range("2026-01-01", periods=n)
    return [d.strftime("%Y%m%d") for d in days]


def _build_dataset(n_stocks: int = 400, n_days: int = 80):
    """Return (price_dates, formation_dates, {endpoint: {key: DataFrame}}).

    Codes are all plain-board 600xxx (board_limit 10, not BJ, not ST), listed long ago,
    big caps with real turnover — so they survive factor_frame's hard gates and clear the
    >=300 cross-section floor. A smooth random-walk price panel feeds momentum + fwd returns.
    """
    rng = np.random.default_rng(20260622)
    P = _trade_days(n_days)
    codes6 = [f"{600000 + i:06d}" for i in range(n_stocks)]
    ts_codes = [f"{c}.SH" for c in codes6]
    inds = rng.choice(["半导体", "光模块", "白酒", "煤炭", "医药", "电力"], n_stocks)

    # daily price panel: geometric random walk per stock, by trade day.
    base = rng.uniform(8, 120, n_stocks)
    steps = rng.normal(0.0, 0.02, size=(n_days, n_stocks))
    closes = base[None, :] * np.exp(np.cumsum(steps, axis=0))
    daily: dict[str, pd.DataFrame] = {}
    for di, d in enumerate(P):
        cl = closes[di]
        op = cl * (1 + rng.normal(0, 0.005, n_stocks))
        hi = np.maximum(op, cl) * (1 + np.abs(rng.normal(0, 0.006, n_stocks)))
        lo = np.minimum(op, cl) * (1 - np.abs(rng.normal(0, 0.006, n_stocks)))
        prev = closes[di - 1] if di > 0 else cl
        pct = (cl / prev - 1.0) * 100
        amount = rng.uniform(2e5, 5e7, n_stocks)  # 千元
        daily[d] = pd.DataFrame({
            "ts_code": ts_codes, "open": op, "high": hi, "low": lo, "close": cl,
            "pct_chg": pct, "amount": amount,
        })

    # two formation dates with >=60 back days and >=10 fwd days inside the panel.
    F = [P[62], P[66]]

    def _per_form(make):
        return {d: make(d) for d in F}

    daily_basic = _per_form(lambda d: pd.DataFrame({
        "ts_code": ts_codes,
        "close": closes[P.index(d)],
        "turnover_rate": rng.uniform(0.5, 25, n_stocks),
        "volume_ratio": rng.uniform(0.4, 4.5, n_stocks),
        "pe_ttm": rng.uniform(-40, 180, n_stocks),
        "pb": rng.uniform(0.6, 22, n_stocks),
        "dv_ratio": rng.uniform(0, 6, n_stocks),
        "total_mv": rng.uniform(40 * 1e4, 3000 * 1e4, n_stocks),  # 万元 → /1e4 = 亿 ≥ 40
        "circ_mv": rng.uniform(30 * 1e4, 2000 * 1e4, n_stocks),
    }))

    def _mk_sf(d):
        cl = closes[P.index(d)]
        return pd.DataFrame({
            "ts_code": ts_codes, "close": cl,
            "ma_qfq_5": cl * rng.uniform(0.95, 1.05, n_stocks),
            "ma_qfq_10": cl * rng.uniform(0.9, 1.08, n_stocks),
            "ma_qfq_20": cl * rng.uniform(0.85, 1.1, n_stocks),
            "ma_qfq_60": cl * rng.uniform(0.8, 1.12, n_stocks),
            "rsi_qfq_6": rng.uniform(10, 95, n_stocks),
            "rsi_qfq_12": rng.uniform(15, 90, n_stocks),
            "macd_qfq": rng.normal(0, 1.5, n_stocks),
        })
    stk_factor_pro = _per_form(_mk_sf)

    def _mk_cy(d):
        cl = closes[P.index(d)]
        c50 = cl * rng.uniform(0.8, 1.2, n_stocks)
        return pd.DataFrame({
            "ts_code": ts_codes,
            "winner_rate": rng.uniform(0, 100, n_stocks),
            "cost_15pct": c50 * rng.uniform(0.8, 0.95, n_stocks),
            "cost_50pct": c50,
            "cost_85pct": c50 * rng.uniform(1.05, 1.3, n_stocks),
            "weight_avg": c50 * rng.uniform(0.95, 1.05, n_stocks),
        })
    cyq_perf = _per_form(_mk_cy)

    moneyflow = _per_form(lambda d: pd.DataFrame({
        "ts_code": ts_codes,
        "buy_sm_amount": rng.uniform(0, 5e4, n_stocks),
        "sell_sm_amount": rng.uniform(0, 5e4, n_stocks),
        "buy_lg_amount": rng.uniform(0, 8e4, n_stocks),
        "sell_lg_amount": rng.uniform(0, 8e4, n_stocks),
        "buy_elg_amount": rng.uniform(0, 6e4, n_stocks),
        "sell_elg_amount": rng.uniform(0, 6e4, n_stocks),
        "net_mf_amount": rng.normal(0, 3e4, n_stocks),
    }))

    # hk_hold + margin_detail cover a subset (left-join semantics → NaN for the rest).
    hk_codes = ts_codes[: n_stocks // 2]
    hk_hold = _per_form(lambda d: pd.DataFrame({
        "ts_code": hk_codes, "ratio": rng.uniform(0, 30, len(hk_codes)),
    }))
    mg_codes = ts_codes[: int(n_stocks * 0.6)]
    margin_detail = _per_form(lambda d: pd.DataFrame({
        "ts_code": mg_codes,
        "rzye": rng.uniform(1e7, 5e8, len(mg_codes)),
        "rqye": rng.uniform(0, 1e8, len(mg_codes)),
        "rzmre": rng.uniform(1e6, 8e7, len(mg_codes)),
        "rzche": rng.uniform(1e6, 7e7, len(mg_codes)),
        "rzrqye": rng.uniform(1e7, 5e8, len(mg_codes)),
    }))
    # block_trade + top_inst: sparse events (only some codes, multiple rows for top_inst).
    blk_codes = ts_codes[:40]
    block_trade = _per_form(lambda d: pd.DataFrame({
        "ts_code": blk_codes,
        "price": closes[P.index(d)][:40] * rng.uniform(0.9, 1.05, 40),
        "vol": rng.uniform(1e4, 1e6, 40),
        "amount": rng.uniform(5e6, 5e8, 40),
    }))

    def _mk_ti(d):
        sub = ts_codes[:30]
        exalter = (["机构专用"] * 20) + ["某营业部"] * 10
        return pd.DataFrame({
            "ts_code": sub, "exalter": exalter,
            "buy": rng.uniform(0, 5e7, 30), "sell": rng.uniform(0, 5e7, 30),
            "net_buy": rng.normal(0, 3e7, 30),
        })
    top_inst = _per_form(_mk_ti)

    stock_basic = pd.DataFrame({
        "ts_code": ts_codes, "name": [f"股票{i}" for i in range(n_stocks)],
        "list_date": ["20100101"] * n_stocks, "market": ["主板"] * n_stocks,
        "industry": inds,
    })

    data = {
        "daily": daily, "daily_basic": daily_basic, "stk_factor_pro": stk_factor_pro,
        "cyq_perf": cyq_perf, "moneyflow": moneyflow, "hk_hold": hk_hold,
        "margin_detail": margin_detail, "block_trade": block_trade, "top_inst": top_inst,
        "stock_basic": {"static": stock_basic},
    }
    return P, F, data


def _write_pkls(cache_root: Path, data: dict) -> None:
    for endpoint, by_key in data.items():
        for key, df in by_key.items():
            fp = cache_root / endpoint / f"{key}.pkl"
            fp.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(fp)


def _write_lake(lake_root: Path, data: dict) -> None:
    for endpoint, by_key in data.items():
        for key, df in by_key.items():
            fp = lake_root / endpoint / f"{key}.parquet"
            fp.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pandas(df, preserve_index=False), fp, compression="zstd")


@pytest.fixture
def synth(tmp_path, monkeypatch):
    """Write the same synthetic dataset to a tmp CACHE (pkl) and tmp LAKE (parquet)."""
    P, F, data = _build_dataset()
    cache_root = tmp_path / "cache"
    lake_root = tmp_path / "lake"
    _write_pkls(cache_root, data)
    _write_lake(lake_root, data)
    monkeypatch.setattr(factor_lab, "CACHE", cache_root)
    monkeypatch.setattr(cache, "LAKE", lake_root)
    return P, F


# ───────────────────────── tests ─────────────────────────


def test_core_feature_set_matches_factor_lab_constants():
    """FEATURE_SETS["core"] must equal factor_lab's CANDIDATES ∪ g_* ∪ GBDT_RAW ∪ {composite}."""
    cand = [c for c, _ in factor_lab.CANDIDATES]
    g_cols = [f"g_{g}" for g in factor_lab.GBDT_GROUPS]
    seen, expected = set(), []
    for c in [*cand, *g_cols, *factor_lab.GBDT_RAW, "composite"]:
        if c not in seen:
            seen.add(c)
            expected.append(c)
    assert feature_columns("core") == expected
    assert LABEL == factor_lab.GBDT_LABEL


def test_materialize_contains_full_core_plus_label_and_buyable(synth):
    P, F = synth
    panel = DataHandler().materialize([F[0]], price_dates=P, cap_floor=CAP_FLOOR, fwd=FWD)
    assert not panel.empty
    for col in feature_columns("core"):
        assert col in panel.columns, f"materialize missing core col {col}"
    assert LABEL in panel.columns
    assert "buyable" in panel.columns
    assert {"date", "code"} <= set(panel.columns)


def test_materialize_seq_window_and_columns(synth):
    """kind='seq' → 每股 SEQ_WINDOW 日滚动窗 × SEQ_FEATURES,展平 {feat}_t{w} + 标签/门齐。"""
    from autoresearch.data.features import SEQ_FEATURES, SEQ_WINDOW
    P, F = synth
    panel = DataHandler().materialize([F[-1]], feature_set="seq", kind="seq",
                                      price_dates=P, cap_floor=CAP_FLOOR, fwd=FWD)
    assert not panel.empty
    seq_cols = feature_columns("seq")
    assert len(seq_cols) == SEQ_WINDOW * len(SEQ_FEATURES)
    for col in seq_cols:
        assert col in panel.columns, f"seq materialize missing {col}"
    assert {"date", "code", LABEL, "buyable"} <= set(panel.columns)


def test_materialize_graph_self_and_context_columns(synth):
    """kind='graph' → 自身 gbdt 特征 + 行业邻接上下文(ctx_,行业均值),列齐 + 标签/门。"""
    from autoresearch.data.features import GRAPH_SELF
    P, F = synth
    panel = DataHandler().materialize([F[0]], feature_set="graph", kind="graph",
                                      price_dates=P, cap_floor=CAP_FLOOR, fwd=FWD)
    assert not panel.empty
    graph_cols = feature_columns("graph")
    assert len(graph_cols) == 2 * len(GRAPH_SELF)
    for col in graph_cols:
        assert col in panel.columns, f"graph materialize missing {col}"
    assert {"date", "code", LABEL, "buyable"} <= set(panel.columns)
    assert any(c.startswith("ctx_") for c in panel.columns)


def test_parity_factor_frame_vs_materialize(synth):
    """The shared factor_frame columns must be numerically identical across both pipelines."""
    P, F = synth
    basic = factor_lab._load_basic()
    piv = factor_lab.load_price_pivots(P)

    handler = DataHandler()
    for D in F:
        ff = factor_lab.factor_frame(D, piv, P, basic, CAP_FLOOR, FWD)
        assert ff is not None, f"factor_frame returned None for {D} (cross-section too small?)"
        mat = handler.materialize([D], price_dates=P, cap_floor=CAP_FLOOR, fwd=FWD)
        assert not mat.empty

        # identical code sets
        assert set(ff["code"]) == set(mat["code"]), f"code set mismatch on {D}"

        ff_s = ff.set_index("code").sort_index()
        mat_s = mat.set_index("code").sort_index()

        # identical forward label (the thing E3 ranks against)
        lab_diff = (ff_s[LABEL].astype(float) - mat_s[LABEL].astype(float)).abs()
        assert lab_diff.max() < 1e-9, f"{LABEL} diverges on {D}: max {lab_diff.max()}"
        assert (ff_s["buyable"].astype(bool) == mat_s["buyable"].astype(bool)).all()

        # every numeric core column factor_frame produces must match to < 1e-9
        shared = [c for c in feature_columns("core") if c in ff.columns and c in mat.columns]
        assert len(shared) >= 30, f"too few shared core columns: {len(shared)}"
        worst = {}
        for c in shared:
            a = pd.to_numeric(ff_s[c], errors="coerce")
            b = pd.to_numeric(mat_s[c], errors="coerce")
            # NaN pattern must match, then compare the finite entries
            assert (a.isna() == b.isna()).all(), f"NaN pattern mismatch in {c} on {D}"
            both = a.notna() & b.notna()
            d = (a[both] - b[both]).abs()
            worst[c] = float(d.max()) if len(d) else 0.0
        assert max(worst.values()) < 1e-9, f"core feature parity broke on {D}: {worst}"


def test_parity_also_covers_derived_g_and_composite(synth):
    """g_* and composite are derived downstream of factor_frame; verify the handler reproduces
    factor_lab.gbdt_features (g_*) and composite_score (composite) on the same rows."""
    from autoresearch.common.scoring import _load_weights, composite_score

    P, F = synth
    basic = factor_lab._load_basic()
    piv = factor_lab.load_price_pivots(P)
    D = F[0]
    ff = factor_lab.factor_frame(D, piv, P, basic, CAP_FLOOR, FWD)
    gbdt_feat = factor_lab.gbdt_features(ff)                       # g_* (+ raw + composite slot)
    comp = composite_score(ff, _load_weights())["composite"]

    mat = DataHandler().materialize([D], price_dates=P, cap_floor=CAP_FLOOR, fwd=FWD)
    mat_s = mat.set_index("code").sort_index()
    gbdt_by_code = gbdt_feat.assign(code=ff["code"].to_numpy()).set_index("code").sort_index()
    comp_by_code = pd.Series(comp.to_numpy(), index=ff["code"].to_numpy()).sort_index()

    for g in factor_lab.GBDT_GROUPS:
        a = pd.to_numeric(gbdt_by_code[f"g_{g}"], errors="coerce")
        b = pd.to_numeric(mat_s[f"g_{g}"], errors="coerce")
        both = a.notna() & b.notna()
        assert (a.isna() == b.isna()).all(), f"g_{g} NaN pattern mismatch"
        assert (a[both] - b[both]).abs().max() < 1e-9, f"g_{g} parity broke"

    cm_b = pd.to_numeric(mat_s["composite"], errors="coerce")
    assert (comp_by_code - cm_b).abs().max() < 1e-9, "composite parity broke"


def test_materialize_retains_all_three_fwd_labels(synth):
    """core/seq/graph 三视图都保留 fwd_1_oo/fwd_5_oc/fwd_10_oc(多 horizon 训练前置)。"""
    from autoresearch.data.features import FWD_LABELS
    P, F = synth
    for fs in ("core", "seq", "graph"):
        panel = DataHandler().materialize([F[0]], feature_set=fs, kind=fs,
                                          price_dates=P, cap_floor=CAP_FLOOR, fwd=FWD)
        assert not panel.empty, f"{fs} materialize 空"
        for lab in FWD_LABELS:
            assert lab in panel.columns, f"{fs} 缺标签 {lab}"


def test_materialize_memoized_and_returns_copy(synth):
    """同参 materialize 命中缓存(zoo 多模型复用);返回副本,改它不污染缓存。"""
    P, F = synth
    h = DataHandler()
    a = h.materialize([F[0]], price_dates=P, cap_floor=CAP_FLOOR, fwd=FWD)
    b = h.materialize([F[0]], price_dates=P, cap_floor=CAP_FLOOR, fwd=FWD)
    assert len(h.__dict__.get("_mat_cache", {})) == 1            # 只物化一次
    pd.testing.assert_frame_equal(a, b)
    a.loc[a.index[0], "composite"] = -999.0                      # 改副本
    c = h.materialize([F[0]], price_dates=P, cap_floor=CAP_FLOOR, fwd=FWD)
    assert c.loc[c.index[0], "composite"] != -999.0             # 缓存未被污染

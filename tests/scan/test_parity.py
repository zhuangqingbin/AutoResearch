"""GOLDEN PARITY —— 新 Stage 管道(L0/L1/L2)≡ screen_market.run 的确定性产物。NO network.

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §E;Plan 3.4。

构造一个**固定合成 universe**(canonical 列,足够喂 composite_score),把 screen_market 的两条取数
入口都 monkeypatch 成它:`fetch_universe_tushare`(L0 取数)+ `_harvest_vol_series`(L1 多日量价,
返回空帧)。于是:
  * 现 `screen_market.run(date, outdir=tmp)` → L1_recall_top1000.csv + L2_gbdt_top200.csv。
  * 新 `Pipeline.run(ctx)` → trace 的 L1_recall + L2_rank。
两条跑在同一份 universe 上,断言:召回集合一致、L1 名次一致、composite 逐值 1e-9、L2 top-l2_n 集合
+ 名次一致。两边都该产出 composite-top(默认 champion=linear,GBDT 模型文件不存在 → predict_scores
回落线性)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO, _REPO / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import screen_market  # noqa: E402  (scripts/ — current deterministic funnel under test)

from autoresearch.data import tushare_source  # noqa: E402  (where fetch_universe_tushare lives)
from autoresearch.scan.config import ScanConfig  # noqa: E402
from autoresearch.scan.context import RunContext  # noqa: E402
from autoresearch.scan.pipeline import Pipeline  # noqa: E402
from autoresearch.trace import schema  # noqa: E402
from autoresearch.trace.store import TraceStore  # noqa: E402

DATE = "2026-06-20"


# ───────────────────────── fixed synthetic universe ─────────────────────────


def _fixed_universe(n: int = 600) -> pd.DataFrame:
    """A canonical post-gate universe (the columns screen_market's L1 composite_score reads).

    Plain-board 600xxx codes, all non-ST big caps with liquidity so _recall_gate_a keeps them all.
    Deterministic RNG → identical frame every run (parity needs a frozen input).
    """
    rng = np.random.default_rng(20260620)
    inds = rng.choice(["半导体", "光模块", "白酒", "煤炭", "医药", "电力"], n)
    df = pd.DataFrame({
        "code": [f"{600000 + i:06d}" for i in range(n)],
        "name": [f"股票{i}" for i in range(n)],
        "industry": inds,
        "close": rng.uniform(5, 300, n),
        "mktcap_yi": rng.uniform(40, 4000, n),
        "amount_yi": rng.uniform(0.5, 200, n),       # >0 → clears recall gate
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
    })
    # tushare 富因子(覆盖 composite 的 fund/chip/north/tech/volprice/value 各组分支)
    df["dv_ratio"] = rng.uniform(0, 6, n)
    df["ma_bull"] = rng.integers(0, 2, n).astype(float)
    df["above_ma60"] = rng.integers(0, 2, n).astype(float)
    df["rsi6"] = rng.uniform(10, 95, n)
    df["rsi12"] = rng.uniform(10, 95, n)
    df["winner_rate"] = rng.uniform(0, 100, n)
    df["cost_50pct"] = rng.uniform(5, 300, n)
    df["main_net_ratio"] = rng.uniform(-0.1, 0.1, n)
    df["retail_net_yi"] = rng.uniform(-2, 2, n)
    df["chip_concentration"] = rng.uniform(0.1, 2.0, n)
    df["price_to_cost"] = rng.uniform(0.7, 1.5, n)
    df["hk_ratio"] = rng.uniform(0, 30, n)
    df["cmf_20"] = rng.uniform(-0.5, 0.5, n)
    df["obv_mom_20"] = rng.uniform(-1, 1, n)
    df["is_st"] = False
    return df


@pytest.fixture
def patched_universe(monkeypatch):
    """Patch BOTH 取数入口 → fixed universe;_harvest_vol_series → 空帧。

    现 run() 在 run() 内 `from autoresearch.data.tushare_source import fetch_universe_tushare`,新
    L0Universe 也从同一模块取 → patch `tushare_source.fetch_universe_tushare` 覆盖两条路径。
    `_harvest_vol_series` 是 screen_market 的模块级函数,两条路径都经它 → patch 在 screen_market 上。
    同时把 GBDT 模型路径指到 tmp(不存在)→ predict_scores 回落线性,与默认 champion(linear)对齐。
    """
    uni = _fixed_universe()

    def _fake_fetch(date, cap_floor_yi=30.0, include_bj=False, **kw):
        return uni.copy()

    def _fake_vol(codes, analysis_date, lookback=20):
        return pd.DataFrame(columns=["code"])

    monkeypatch.setattr(tushare_source, "fetch_universe_tushare", _fake_fetch, raising=True)
    monkeypatch.setattr(screen_market, "_harvest_vol_series", _fake_vol, raising=True)
    # GBDT 自保门:确保现 run() 的 L2 走线性回落(模型文件指向不存在的 tmp 路径)。
    try:
        import factor_lab
        monkeypatch.setattr(factor_lab, "GBDT_MODEL", "/nonexistent/gbdt_model.pkl", raising=False)
    except Exception:  # noqa: BLE001 — factor_lab 仅 run() 内 import,patch 不到则其自有门兜底
        pass
    return uni


# ───────────────────────── the acceptance gate ─────────────────────────


def _run_current(tmp_path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """现 screen_market.run → (L1_recall_top1000, L2_gbdt_top200)。"""
    outdir = tmp_path / "current"
    screen_market.run(DATE, cap_floor_yi=30.0, include_bj=True,
                      recall_n=1000, l2_n=200, outdir=outdir, source="tushare")
    l1 = pd.read_csv(outdir / "L1_recall_top1000.csv")
    l2 = pd.read_csv(outdir / "L2_gbdt_top200.csv")
    return l1, l2


def _run_new(tmp_path) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """新 Pipeline → trace 的 (L1_recall, L2_rank, run_id)。"""
    store = TraceStore(tmp_path / "trace")
    ctx = RunContext(analysis_date=DATE, config=ScanConfig(recall_n=1000, l2_n=200), trace=store)
    run_id = Pipeline().run(ctx)
    return (store.get_df(run_id, schema.L1_RECALL),
            store.get_df(run_id, schema.L2_RANK), run_id)


def _codes(df) -> list[str]:
    return df["code"].astype(str).str.zfill(6).tolist()


def test_golden_parity_l1_recall_set_and_order(patched_universe, tmp_path):
    """L1: identical recalled code set AND identical ranking order."""
    cur_l1, _ = _run_current(tmp_path)
    new_l1, _, _ = _run_new(tmp_path)
    assert set(_codes(cur_l1)) == set(_codes(new_l1)), "L1 recalled code set differs"
    assert _codes(cur_l1) == _codes(new_l1), "L1 ranking order differs"


def test_golden_parity_l1_composite_values(patched_universe, tmp_path):
    """L1: identical composite values to 1e-9 (per code)."""
    cur_l1, _ = _run_current(tmp_path)
    new_l1, _, _ = _run_new(tmp_path)
    a = cur_l1.assign(code=lambda d: d["code"].astype(str).str.zfill(6)).set_index("code")["composite"]
    b = new_l1.assign(code=lambda d: d["code"].astype(str).str.zfill(6)).set_index("code")["composite"]
    common = a.index.intersection(b.index)
    assert len(common) == len(a) == len(b)
    assert (a.loc[common].astype(float) - b.loc[common].astype(float)).abs().max() < 1e-9


def test_golden_parity_l2_set_and_order(patched_universe, tmp_path):
    """L2: identical top-l2_n set AND identical order (both = composite top200 via linear fallback)."""
    _, cur_l2 = _run_current(tmp_path)
    _, new_l2, _ = _run_new(tmp_path)
    assert len(cur_l2) == len(new_l2) == 200
    assert set(_codes(cur_l2)) == set(_codes(new_l2)), "L2 top-200 set differs"
    assert _codes(cur_l2) == _codes(new_l2), "L2 ranking order differs"


def test_golden_parity_via_parity_module(patched_universe, tmp_path):
    """The autoresearch.scan.parity capture→check round-trip reports ok=True (no diffs)."""
    from autoresearch.scan import parity

    golden = tmp_path / "golden"
    parity.capture(DATE, golden, config=ScanConfig(recall_n=1000, l2_n=200))
    res = parity.check(DATE, golden, config=ScanConfig(recall_n=1000, l2_n=200),
                       trace_root=tmp_path / "trace_check")
    assert res.ok, res.summary()
    assert not res.l1_set_diff and not res.l1_order_diff and not res.l1_composite_diff
    assert not res.l2_set_diff and not res.l2_order_diff
    assert res.notes.get("l1_composite_max_abs_diff", 1.0) < 1e-9

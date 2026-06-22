"""9 路 channel:返回列契约 / 过门 / top-k / 缺列降级。NO network(合成 universe)。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.scan.recall import build, registered_channels
from autoresearch.scan.recall.registry import CHANNEL_DEFAULTS
from tests.scan._synth_universe import synth_universe

_CHANNELS = {"composite", "momentum", "reversal", "growth", "value",
             "main_fund", "northbound", "accumulation", "heat"}


def test_all_channels_registered():
    assert set(registered_channels()) >= _CHANNELS


def test_each_channel_returns_contract_and_respects_k():
    uni = synth_universe(n=400, seed=1)
    from autoresearch.common.scoring import _load_weights, composite_score
    scored = composite_score(uni, _load_weights())
    for name in _CHANNELS:
        out = build(name)(scored, "2026-06-20", 50)
        assert list(out.columns) == ["code", "channel_rank", "channel_score"], f"{name} 列契约破"
        assert len(out) <= 50, f"{name} 超 k"
        if len(out):
            assert out["channel_rank"].tolist() == list(range(1, len(out) + 1)), f"{name} rank 非连续"
            assert np.isfinite(out["channel_score"].to_numpy()).all(), f"{name} score 非有限"


def test_channel_missing_column_degrades_to_empty():
    uni = pd.DataFrame({"code": [f"{i:06d}" for i in range(10)], "composite": range(10)})
    # 缺 hk_ratio/main_inflow 等 → northbound/accumulation 空帧不抛
    for name in ("northbound", "accumulation"):
        out = build(name)(uni, "2026-06-20", 50)
        assert out.empty and list(out.columns) == ["code", "channel_rank", "channel_score"]


# ───────────────────────── heat 通道(高热:成交额主导)─────────────────────────


def _heat_frame(rows):
    """rows: (code, amount_yi, turnover, vol_ratio, composite)。"""
    df = pd.DataFrame(rows, columns=["code", "amount_yi", "turnover", "vol_ratio", "composite"])
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def test_heat_registered_with_quota_and_floor():
    assert "heat" in registered_channels()
    assert CHANNEL_DEFAULTS["heat"].quota == 200 and CHANNEL_DEFAULTS["heat"].floor == 50


def test_heat_ranks_monotonic_in_amount():
    """换手/量比相同 → 成交额更大者 heat 名次更前(amount 0.45 是正向主驱动)。"""
    rows = [("000001", 100.0, 5.0, 1.0, 50.0), ("000002", 10.0, 5.0, 1.0, 50.0),
            ("000003", 1.0, 5.0, 1.0, 50.0), ("000004", 2.0, 5.0, 1.0, 50.0)]
    out = build("heat")(_heat_frame(rows), "2026-06-20", 50)
    assert out.iloc[0]["code"] == "000001"                      # 最大成交额排第 1
    order = out["code"].tolist()
    assert order.index("000001") < order.index("000002") < order.index("000003")


def test_heat_surfaces_amount_leader_that_composite_buries():
    """中际旭创式:巨额成交 + 低 composite。heat 顶到前列,composite 通道把它埋到尾部(正交性)。"""
    rows = [
        ("099308", 1000.0, 8.0, 1.5, 8.0),    # 龙头:成交额碾压、换手中、composite 垫底
        ("066666", 30.0, 25.0, 4.0, 20.0),    # 换手战场:高换手高量比、composite 次低
        ("011111", 15.0, 6.0, 1.0, 92.0),     # 高 composite 冷门:成交不热
        ("022222", 8.0, 3.0, 0.9, 78.0),
        ("033333", 6.0, 2.0, 0.8, 70.0),
        ("044444", 4.0, 1.5, 0.7, 65.0),
        ("055555", 2.0, 1.0, 0.6, 60.0),
    ]
    df = _heat_frame(rows)
    heat = build("heat")(df, "2026-06-20", 50)
    comp = build("composite")(df, "2026-06-20", 50)
    top2_heat = heat.head(2)["code"].tolist()
    assert "099308" in top2_heat                                # 龙头被 heat 顶到前 2
    assert set(top2_heat) == {"099308", "066666"}              # heat 前 2 = 两只高热
    comp_top3 = comp.head(3)["code"].tolist()
    assert "099308" not in comp_top3 and "066666" not in comp_top3   # composite 把高热埋到尾部
    assert "011111" in comp_top3                                # composite 顶端是高分冷门


def test_heat_degrades_without_heat_columns():
    df = pd.DataFrame({"code": [f"{i:06d}" for i in range(5)], "composite": range(5)})
    out = build("heat")(df, "2026-06-20", 50)
    assert out.empty and list(out.columns) == ["code", "channel_rank", "channel_score"]

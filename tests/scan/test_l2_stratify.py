"""L2 分层多样性采样器(l2_stratify)单测。design: 2026-06-25-l2-stratified-sampler。

覆盖:floor 保底 / sector_cap / 多 channel 归桶 / sector-neutral 排序 / 回落(无分层)/ 行数。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoresearch.scan.recall.l2_stratify import (DEFAULT_FLOORS, sector_neutral,
                                                  select_l2, stratified_l2)


def _universe(n=600, seed=7):
    rng = np.random.default_rng(seed)
    chans = ["composite", "momentum", "reversal", "value", "growth", "accumulation", "main_fund", "heat", "healthy"]  # healthy: 2026-07-03 第10路
    inds = rng.choice(["半导体", "白酒", "医药", "电力", "煤炭", "汽车"], n)
    # 每只随机命中 1–3 路(composite 保底)
    rc = []
    for _ in range(n):
        k = rng.integers(1, 4)
        picks = set(rng.choice(chans, k)) | {"composite"}
        rc.append("|".join(sorted(picks)))
    return pd.DataFrame({
        "code": [f"{600000+i:06d}" for i in range(n)],
        "industry": inds,
        "composite": rng.uniform(0, 100, n),
        "recall_channels": rc,
    })


def test_returns_exactly_l2n():
    out = stratified_l2(_universe(600), l2_n=200)
    assert len(out) == 200
    assert out["code"].is_unique


def test_small_universe_returns_all():
    out = stratified_l2(_universe(120), l2_n=200)
    assert len(out) == 120


def test_floors_guaranteed_per_style():
    """每个风格桶在最终 200 中 ≥ floor(成员充足时)。"""
    from autoresearch.scan.recall.l2_stratify import STYLE_CHANNELS
    df = _universe(800)
    out = select_l2(df, 200)[0]
    sets = out["recall_channels"].fillna("").map(lambda s: set(str(s).split("|")))
    for st, chs in STYLE_CHANNELS.items():
        have = sets.map(lambda cs, c=set(chs): bool(cs & c)).sum()
        assert have >= DEFAULT_FLOORS[st], f"{st} 仅 {have} < floor {DEFAULT_FLOORS[st]}"


def test_sector_cap_enforced():
    out = stratified_l2(_universe(800), l2_n=200, sector_cap_frac=0.20)
    top = out["industry"].value_counts().iloc[0]
    assert top <= int(np.floor(0.20 * 200)), f"最大行业 {top} 破 cap 40"


def test_no_floors_is_sn_top():
    """floors={} + cap 关 → 纯 sector-neutral composite top-N(确定性)。"""
    df = _universe(500)
    out = stratified_l2(df, l2_n=200, floors={}, sector_cap_frac=1.0)
    sn = sector_neutral(df["composite"], df["industry"])
    want = set(df.assign(_s=sn.to_numpy()).nlargest(200, "_s")["code"].str.zfill(6))
    assert set(out["code"]) == want
    assert not out["l2_lane_reserved"].any()      # 无分层 → 无 reserved


def test_lane_reserved_flag_present():
    out = select_l2(_universe(800), 200)[0]
    assert out["l2_lane_reserved"].sum() > 0       # floor 救回若干
    assert "l2_rank" in out.columns and out["l2_rank"].tolist() == list(range(1, 201))


def test_sector_neutral_demeans_within_industry():
    df = _universe(300)
    sn = sector_neutral(df["composite"], df["industry"])
    # 每个行业组内 sn 均值 ≈ 0
    g = pd.DataFrame({"sn": sn.to_numpy(), "ind": df["industry"].to_numpy()}).groupby("ind")["sn"].mean()
    assert g.abs().max() < 1e-9

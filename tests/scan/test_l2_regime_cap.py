"""stratified_l2 regime cap + sector_mom 透传单测 —— 合成召回帧,无网络。

覆盖 spec §Leaf L2:
  - 输出带 sector_mom(行业 median pct_60d;补 sector-neutral 抹掉的行业 beta)
  - regime_caps 放宽 → 单行业占比可更高;收紧 → 更低
  - regime=None → 与不传一致(parity)
  - select_l2 透传 regime/regime_caps
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.recall.l2_stratify import select_l2, stratified_l2


def _recall(n=100):
    rows = []
    for i in range(n):
        rows.append({"code": f"{i:06d}", "composite": float(100 - i * 0.1),
                     "industry": "半导体" if i % 2 == 0 else "白酒",
                     "recall_channels": "composite", "pct_60d": float(i)})
    return pd.DataFrame(rows)


def test_sector_mom_present():
    out = stratified_l2(_recall(100), l2_n=20, floors={})
    assert "sector_mom" in out.columns and out["sector_mom"].notna().any()


def test_regime_cap_loosen_allows_more_concentration():
    r = _recall(100)
    base = stratified_l2(r, l2_n=20, floors={}, sector_cap_frac=0.20)
    loose = stratified_l2(r, l2_n=20, floors={}, sector_cap_frac=0.20,
                          regime="trend", regime_caps={"trend": 0.9})
    assert loose["industry"].value_counts().iloc[0] >= base["industry"].value_counts().iloc[0]


def test_regime_none_is_parity_cap():
    r = _recall(100)
    a = stratified_l2(r, l2_n=20, floors={}, sector_cap_frac=0.20)
    b = stratified_l2(r, l2_n=20, floors={}, sector_cap_frac=0.20, regime=None, regime_caps={"trend": 0.9})
    assert list(a["code"]) == list(b["code"])


def test_select_l2_threads_regime():
    out, _eng = select_l2(_recall(100), 20, regime="risk_off", regime_caps={"risk_off": 0.1})
    assert len(out) == 20 and "sector_mom" in out.columns

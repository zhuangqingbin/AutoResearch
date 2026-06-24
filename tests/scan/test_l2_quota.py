"""apply_l2_lane_quota 单测 —— L2 给多样性 lane 保留席。合成帧,无网络。

覆盖 spec §8 自检:
  - Q=0 严格复现 head(l2_n)(parity 锚)
  - Q>0:reserve 来自 lane_channels∩below;hybrid 半分半动量;输出恰 l2_n(不足回填)
  - 边界:无 eligible / 缺 recall_channels 列 → 退化不抛
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.recall.l2_quota import apply_l2_lane_quota


def _ranked(n=10):
    """l2_score 降序;前 6 无 lane(composite/value),后段隔位塞 momentum;009 给巨高 pct_60d。"""
    rows = []
    for i in range(n):
        rows.append({"code": f"{i:06d}", "l2_score": 100 - i,
                     "recall_channels": "composite" if i < 6 else ("momentum" if i % 2 else "value"),
                     "pct_60d": 5.0 + (50.0 if i == 9 else 0.0)})
    return pd.DataFrame(rows)


def test_quota_zero_is_parity():
    out = apply_l2_lane_quota(_ranked(10), l2_n=5, quota=0, lane_channels=("momentum",))
    assert list(out["code"]) == [f"{i:06d}" for i in range(5)]
    assert out["l2_lane_reserved"].eq(False).all()


def test_quota_reserves_lane_below_cut():
    r = _ranked(10)                       # core_cut = 5-2 = 3
    out = apply_l2_lane_quota(r, l2_n=5, quota=2, lane_channels=("momentum",))
    assert len(out) == 5
    res = set(out[out["l2_lane_reserved"]]["code"])
    assert res, "应有被救回的 momentum 票"
    assert res <= set(out["code"])
    for c in res:                         # 救回的必是 momentum 通道
        assert "momentum" in r.set_index("code").loc[c, "recall_channels"]


def test_hybrid_half_momentum_picks_high_pct60d():
    out = apply_l2_lane_quota(_ranked(10), l2_n=5, quota=2, lane_channels=("momentum",))
    assert "000009" in set(out[out["l2_lane_reserved"]]["code"]), "动量半应捞到高 pct_60d 的 009"


def test_output_exactly_l2n_backfill_when_few_eligible():
    out = apply_l2_lane_quota(_ranked(10), l2_n=5, quota=2, lane_channels=("northbound",))  # 无 eligible
    assert len(out) == 5
    assert out["l2_lane_reserved"].eq(False).all()


def test_missing_recall_channels_degrades():
    r = _ranked(10).drop(columns=["recall_channels"])
    out = apply_l2_lane_quota(r, l2_n=5, quota=2, lane_channels=("momentum",))
    assert len(out) == 5 and out["l2_lane_reserved"].eq(False).all()

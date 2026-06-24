#!/usr/bin/env python3
"""L2 lane 配额 —— champion 单分排序后,给多样性 lane(momentum/heat/growth/accumulation)
保留 quota 席穿过 L2,使其到达 L3/L4 的 Claude 判断,而非被反转倾向的单分排序抹平。

design: docs/specs/2026-06-24-l2-lane-quota-design.md。
quota<=0 / 帧不足 l2_n / 无 recall_channels → 逐值复现 head(l2_n)(parity 锚)。
"""
from __future__ import annotations

import pandas as pd


def _has_lane(s, lane_channels) -> bool:
    """recall_channels 串(`a|b|c`)是否命中任一多样性 lane。backfill/空 → False。"""
    if not isinstance(s, str) or not s or s == "(backfill)":
        return False
    return bool(set(s.split("|")) & set(lane_channels))


def apply_l2_lane_quota(ranked: pd.DataFrame, l2_n: int, quota: int, lane_channels) -> pd.DataFrame:
    """ranked 已按 L2 分降序。返回恰 min(l2_n, len) 行 + l2_lane_reserved(bool)。见 spec §4.1。

    core = top(l2_n−quota);reserve = core 线下、recall_channels∩lane_channels 的票按 hybrid
    (半按 L2 分 + 半按 pct_60d)取 quota;reserve 不足由 below 按分回填到 l2_n。
    """
    r = ranked.reset_index(drop=True).copy()
    if quota <= 0 or len(r) <= l2_n or "recall_channels" not in r.columns:
        out = r.head(l2_n).copy()
        out["l2_lane_reserved"] = False
        return out.reset_index(drop=True)

    core_cut = max(0, l2_n - quota)
    core, below = r.iloc[:core_cut], r.iloc[core_cut:]
    eligible = below[below["recall_channels"].map(lambda s: _has_lane(s, lane_channels))]

    n_score = quota // 2
    by_score = eligible.head(n_score)                                   # below 已降序 → 前 n_score = 分最高
    rest = eligible.drop(by_score.index)
    by_mom = (rest.sort_values("pct_60d", ascending=False, kind="stable")
              if "pct_60d" in rest.columns else rest).head(quota - n_score)
    reserve = pd.concat([by_score, by_mom])
    reserve_codes = set(reserve["code"])

    need = l2_n - len(core) - len(reserve)
    filler = below[~below["code"].isin(reserve_codes)].head(max(0, need))
    out = pd.concat([core, reserve, filler]).head(l2_n).copy()
    out["l2_lane_reserved"] = out["code"].isin(reserve_codes)
    return out.reset_index(drop=True)

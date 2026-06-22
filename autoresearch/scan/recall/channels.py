#!/usr/bin/env python3
"""8 路内置 channel —— 全复用 common.scoring(零新因子数学)。

design: docs/specs/2026-06-22-l1-multi-recall-design.md §8 路 channel 表。
每路:对 scored 帧(已含 composite + 因子列)过门 + 按策略信号降序 + 截 top-k。
accumulation 复用 composite_score 既有吸筹判据(底部放量 + 主力未撤),不重写。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.scoring import (
    _num,
    lens_growth,
    lens_momentum,
    lens_reversal,
    lens_value,
)
from autoresearch.scan.recall.base import gate_rank
from autoresearch.scan.recall.registry import channel


@channel("composite", quota=500, floor=100, desc="IC 校准复合分(=今天)")
def composite(frame, date, k):
    return gate_rank(frame, None, "composite", k)


@channel("momentum", quota=250, floor=50, desc="趋势龙头(lens_momentum 过门)")
def momentum(frame, date, k):
    g = lens_momentum(frame)
    return gate_rank(g, g["momentum_gate"], "momentum_score", k)


@channel("reversal", quota=200, floor=50, desc="困境反转(lens_reversal 过门)")
def reversal(frame, date, k):
    g = lens_reversal(frame)
    return gate_rank(g, g["reversal_gate"], "reversal_score", k)


@channel("growth", quota=150, floor=40, desc="成长加速(lens_growth 过门)")
def growth(frame, date, k):
    g = lens_growth(frame)
    return gate_rank(g, g["growth_gate"], "growth_score", k)


@channel("value", quota=200, floor=50, desc="行业内低估(lens_value 过门)")
def value(frame, date, k):
    g = lens_value(frame)
    return gate_rank(g, g["value_gate"], "value_score", k)


@channel("main_fund", quota=200, floor=50, desc="主力净流入")
def main_fund(frame, date, k):
    score_col = "main_net_ratio" if "main_net_ratio" in frame.columns else "main_inflow_yi"
    mask = (_num(frame["main_inflow_yi"]) > 0) if "main_inflow_yi" in frame.columns else None
    return gate_rank(frame, mask, score_col, k)


@channel("northbound", quota=120, floor=30, desc="北向(hk_ratio)")
def northbound(frame, date, k):
    mask = (_num(frame["hk_ratio"]) > 0) if "hk_ratio" in frame.columns else None
    return gate_rank(frame, mask, "hk_ratio", k)


@channel("accumulation", quota=120, floor=30, desc="底部吸筹(投机高召回,交下游证伪)")
def accumulation(frame, date, k):
    if "vol_ratio" not in frame.columns:
        return gate_rank(frame, None, "vol_ratio", k)   # -> 空帧
    low_pos = pd.Series(False, index=frame.index)
    if "winner_rate" in frame.columns:
        low_pos = low_pos | (_num(frame["winner_rate"]) < 40)
    if "price_to_cost" in frame.columns:
        low_pos = low_pos | (_num(frame["price_to_cost"]) < 1.0)
    not_high = (_num(frame["pct_60d"]) < 20) if "pct_60d" in frame.columns else pd.Series(True, index=frame.index)
    main_ok = (_num(frame["main_net_ratio"]) >= 0) if "main_net_ratio" in frame.columns else pd.Series(True, index=frame.index)
    mask = (_num(frame["vol_ratio"]) >= 1.5) & low_pos & not_high & main_ok
    return gate_rank(frame, mask, "vol_ratio", k)

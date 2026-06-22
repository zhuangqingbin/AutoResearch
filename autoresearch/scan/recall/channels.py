#!/usr/bin/env python3
"""9 路内置 channel —— 全复用 common.scoring(零新因子数学)。

design: docs/specs/2026-06-22-l1-multi-recall-design.md §9 路 channel 表。
每路:对 scored 帧(已含 composite + 因子列)过门 + 按策略信号降序 + 截 top-k。
accumulation 复用 composite_score 既有吸筹判据(底部放量 + 主力未撤),不重写。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.scoring import (
    _num,
    _pct,
    lens_growth,
    lens_momentum,
    lens_reversal,
    lens_value,
)
from autoresearch.scan.recall.base import gate_rank
from autoresearch.scan.recall.registry import channel


@channel("composite", quota=400, floor=100, desc="IC 校准复合分(=今天)")
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


@channel("heat", quota=200, floor=50,
         desc="高热(成交额量级主轴 × 换手/量比 kicker;捞巨额成交龙头,免疫 composite 的 IC froth 惩罚,交下游证伪)")
def heat(frame, date, k):
    """按成交额绝对体量排序,不过门(top-k 即资金最集中的 k 只)。

    composite 是 T+1 IC 校准——它**故意压抑**抛物线龙头(过热 −8/−15 + 主力出逃拖累),
    像中际旭创(成交额全市场第 2、composite 仅 32)在召回近乎隐形。本路与 composite 正交:
    只看『钱在哪』,floor 保底把成交额最大的 ~50 只无条件送进 L2,让 Claude 定性判断,而非被
    froth 统计惩罚提前筛掉。

    **机制(成交额主导)**:实测百分位混合(amount/turnover/vol_ratio 各取分位再加权)行不通——
    rank 把 386亿 压成 0.9998(与第 100 名仅差 2pt),换手/量比却能 0→1 全摆,于是 surfaces 的全是
    小盘换手异动股,中际旭创(换手仅 2.5%、量比 0.98 偏低)反而进不来。改用**成交额量级当乘法主轴**:
    `heat = amount_yi × (1 + 0.15·pct(换手) + 0.10·pct(量比))`。kicker ≤1.25×,压不过量级,只在
    成交额相近时让换手/量比更高者靠前(东方财富式今日异动)——既锁定中际旭创/龙头,又兼顾活跃度。
    缺 amount_yi → 空帧降级(与其他路一致)。
    """
    if "amount_yi" not in frame.columns:
        return gate_rank(frame, None, "heat_score", k)   # 无成交额主轴 → 空帧
    g = frame.copy()
    kicker = pd.Series(1.0, index=g.index)
    if "turnover" in g.columns:
        kicker = kicker + 0.15 * _pct(g["turnover"]).fillna(0.0)
    if "vol_ratio" in g.columns:
        kicker = kicker + 0.10 * _pct(g["vol_ratio"]).fillna(0.0)
    g["heat_score"] = _num(g["amount_yi"]).fillna(0.0) * kicker
    return gate_rank(g, None, "heat_score", k)

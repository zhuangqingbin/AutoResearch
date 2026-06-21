#!/usr/bin/env python3
"""trace 产物 schema —— 每段 typed 产物的列契约(防字段漂移)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §D("产物 typed")。

只做**轻量**列契约:每个产物名 → 期望列集合(分 required / optional)。`validate`/`coerce`
不强转 dtype、不删多余列(确定性段产物列随 weights/源演化,过严会脆),只在**缺 required 列**时
warn,把缺的列补 NaN 占位以便 schema 稳定。表格落 parquet,文本 md/json(本模块只管表格)。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ── 各段产物的 key 列(parquet stem = stage_name)──
L0_UNIVERSE = "L0_universe"
L1_RECALL = "L1_recall"
L1_SCORED_FULL = "L1_scored_full"
L2_RANK = "L2_rank"


@dataclass(frozen=True)
class ArtifactSchema:
    """一个 trace 产物的列契约:name + required 列(缺则 warn+补 NaN)+ optional 列(有则保留)。"""

    name: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = field(default_factory=tuple)

    def columns(self) -> tuple[str, ...]:
        return (*self.required, *self.optional)


# canonical 复合分 9 因子组(与 common.scoring._GROUPS 对齐)的派生子分列。
_SCORE_GROUP_COLS = (
    "score_momentum", "score_fund_main", "score_fund_retail", "score_chip",
    "score_north", "score_tech", "score_growth", "score_value", "score_volprice",
)

# screen_market.run 落盘 L1/L2 时的展示列(keep 列表),作为 optional —— 有则带上、无则不强求。
_DISPLAY_COLS = (
    "mktcap_yi", "close", "amount_yi", "vol_ratio", "turnover", "cmf_20", "obv_mom_20",
    "pct_60d", "pct_ytd", "main_inflow_yi", "main_net_ratio", "retail_net_yi",
    "winner_rate", "chip_concentration", "price_to_cost", "hk_ratio",
    "rsi6", "rsi12", "pe", "pb", "dv_ratio", "np_yoy", "rev_yoy", "roe",
    "ma_bull", "above_ma60",
)

SCHEMAS: dict[str, ArtifactSchema] = {
    # L0 过门后的全 A(canonical 列;打分前)——下游 L1 读它。
    L0_UNIVERSE: ArtifactSchema(
        name=L0_UNIVERSE,
        required=("code",),
        optional=("name", "industry", "close", "amount_yi", "mktcap_yi",
                  "pct_60d", "pct_ytd", *_DISPLAY_COLS),
    ),
    # L1 召回:复合分降序 top recall_n。
    L1_RECALL: ArtifactSchema(
        name=L1_RECALL,
        required=("code", "composite"),
        optional=("name", "industry", *_SCORE_GROUP_COLS, *_DISPLAY_COLS),
    ),
    # L1 全量打分(所有过门股,rank + recalled 标记)——trace 留全阶段不截断。
    L1_SCORED_FULL: ArtifactSchema(
        name=L1_SCORED_FULL,
        required=("rank", "recalled", "code", "composite"),
        optional=("name", "industry", *_SCORE_GROUP_COLS, *_DISPLAY_COLS),
    ),
    # L2 粗排:champion 重排 top l2_n。
    L2_RANK: ArtifactSchema(
        name=L2_RANK,
        required=("l2_rank", "code", "composite"),
        optional=("name", "industry", "l2_score", *_SCORE_GROUP_COLS, *_DISPLAY_COLS),
    ),
}


def get_schema(name: str) -> ArtifactSchema | None:
    """取产物 schema;未登记 → None(放行,只是不校验)。"""
    return SCHEMAS.get(name)


def validate(df: pd.DataFrame, name: str) -> list[str]:
    """对照 schema 检查 df,返回缺失的 required 列列表(空=合规)。不抛错,调用方决定。"""
    sch = get_schema(name)
    if sch is None:
        return []
    return [c for c in sch.required if c not in df.columns]


def coerce(df: pd.DataFrame, name: str, *, warn: bool = True) -> pd.DataFrame:
    """把 df 对齐到 schema:缺 required 列 → warn 并补 NaN 占位;不动多余列、不强转 dtype。

    返回新帧(原帧不变)。未登记产物名 → 原样返回。
    """
    sch = get_schema(name)
    if sch is None:
        return df
    missing = [c for c in sch.required if c not in df.columns]
    if missing and warn:
        print(f"[trace.schema] {name}: 缺 required 列 {missing} → 补 NaN 占位", file=sys.stderr)
    if not missing:
        return df
    out = df.copy()
    for c in missing:
        out[c] = np.nan
    return out

#!/usr/bin/env python3
"""GBDT 特征构造 —— 树模型(lgbm/xgb/cat/dbl)共用的特征矩阵(PORT 自 factor_lab.gbdt_features)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C④。

与 `scripts/factor_lab.py` 的 `gbdt_features` **同口径**:8 组分位 g_*(_factor_groups 取
GBDT_GROUPS 这 8 组)+ 双侧都有的原始因子 GBDT_RAW + **composite 线性锚定特征**(GBDT 至少能
复刻线性,再叠非线性 → 不该弱于线性)。NaN 保留(树原生分裂处理);列名/顺序固定 → 预测时
reindex 对齐。常量镜像 features.py / factor_lab,任一处改了由 tests 锁死。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.common.scoring import _factor_groups, _num

# 镜像 factor_lab.GBDT_GROUPS / GBDT_RAW(也即 features._GBDT_GROUPS / _GBDT_RAW)。
GBDT_GROUPS: list[str] = [
    "momentum", "fund_main", "fund_retail", "chip", "north", "tech", "value", "volprice",
]
GBDT_RAW: list[str] = [
    "pct_60d", "pct_ytd", "vol_ratio", "turnover",
    "winner_rate", "chip_concentration", "price_to_cost",
    "main_inflow_yi", "main_net_ratio", "retail_net_yi", "hk_ratio",
    "rsi6", "rsi12", "pe", "pb", "dv_ratio",
    "cmf_20", "obv_mom_20", "ma_bull", "above_ma60",
]


def gbdt_features(df: pd.DataFrame) -> pd.DataFrame:
    """train/predict 共用特征矩阵:8 组分位 g_* + GBDT_RAW 原始因子 + composite 锚定。

    与 factor_lab.gbdt_features 逐行同构:groups = _factor_groups(df) 取 GBDT_GROUPS 这 8 组;
    原始因子 + composite 缺列则补 NaN。列顺序固定(g_* → GBDT_RAW → composite)。
    """
    groups = _factor_groups(df)
    feat = pd.DataFrame({f"g_{k}": groups[k].to_numpy() for k in GBDT_GROUPS}, index=df.index)
    for c in [*GBDT_RAW, "composite"]:
        feat[c] = _num(df[c]).to_numpy() if c in df.columns else np.nan
    return feat


def feature_names() -> list[str]:
    """GBDT 特征列名(g_* → GBDT_RAW → composite),与 gbdt_features 输出列同序。"""
    return [f"g_{k}" for k in GBDT_GROUPS] + [*GBDT_RAW, "composite"]

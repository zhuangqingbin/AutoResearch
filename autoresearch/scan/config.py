#!/usr/bin/env python3
"""ScanConfig —— 扫描管道的确定性参数(L0/L1/L2 漏斗口径)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A。

把 screen_market.run 的关键字参数(recall_n / l2_n / cap_floor / include_bj / source)收成一个
dataclass,作为 RunContext 的一部分随 run 走;`l2_model` 选 L2 粗排用哪个 champion(默认 "l2_fwd5"
= zoo 训的 swing champion,无则回落 GBDT/线性)。值与 screen_market CLI 默认逐一对齐。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class ScanConfig:
    """扫描确定性配置(与 screen_market.run 默认对齐)。"""

    recall_n: int = 1000          # L1 复合分召回 top N
    l2_n: int = 200               # L2 粗排重排 top N
    cap_floor: float = 30.0       # 市值地板(亿)
    include_bj: bool = True       # 是否纳入北交所
    source: str = "tushare"       # universe 取数源:tushare(默认)| em
    l2_model: str = "l2_fwd5"     # L2 champion 名(zoo 训的 swing champion;无→回落 GBDT/线性)
    recall_mode: str = "multi"                       # L1 召回:multi(多路)| composite(单复合分,对拍)
    recall_channels: list[str] | None = None         # 启用的 channel 子集(None=全注册)
    channel_quotas: dict[str, int] | None = None     # 覆盖各路 quota(None=CHANNEL_DEFAULTS)
    channel_floors: dict[str, int] | None = None     # 覆盖各路 floor(None=CHANNEL_DEFAULTS)

    def to_dict(self) -> dict:
        """落 manifest 的纯 dict(可 JSON 序列化)。"""
        return asdict(self)

#!/usr/bin/env python3
"""ScanConfig —— 扫描管道的确定性参数(L0/L1/L2 漏斗口径)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A。

把漏斗关键字参数(recall_n / l2_n / cap_floor / include_bj / source)收成一个 dataclass。

**消费方勘误(Wave12-T33,2026-08-09)**:这里原先写着「现存消费方 =
`user_config.apply_to_scan_config`」——那是一句**过时且误导**的话。该函数自落地起生产零
调用点(只有测试调它),本波已删除。本 dataclass 的真实消费路径是 `universe.run` 等直接
按字段取值;`scan_config.jsonc` 的各块由各消费点自己 `load_user_config()` 现读,
从来没有真的经由一个"映射函数"喂进来过。typed-trace 平行实现(Pipeline/RunContext/cli)
已于 2026-07-13 移除(生产真身一直是 prelude→universe.run 直调)。
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
    recall_mode: str = "multi"                       # L1 召回:multi(多路)| composite(单复合分,对拍)
    recall_channels: list[str] | None = None         # 启用的 channel 子集(None=全注册)
    regime_aware: bool = False                        # L1 权重按 regime 选(需 weights.json regimes 块);默认关=parity
    l0_min_amount_yi: float = 0.0                      # L0 流动性门(成交额亿 >);默认 0=关=parity
    l0_min_list_days: int = 0                          # L0 次新门(上市天数 ≥);默认 0=关=parity(需 list_days 列)
    channel_quotas: dict[str, int] | None = None     # 覆盖各路 quota(None=CHANNEL_DEFAULTS)
    channel_floors: dict[str, int] | None = None     # 覆盖各路 floor(None=CHANNEL_DEFAULTS)
    # L2 分层多样性采样器(ML-free;sector-neutral composite;design 2026-06-25-l2-stratified-sampler)
    l2_floors: dict | None = None                     # 各风格 floor(None=l2_stratify.DEFAULT_FLOORS;{}=不分层)
    l2_sector_cap: float = 0.20                        # 任一申万一级 ≤ 此比例(0.20=40/200);≥1.0=关

    # ── 用户配置层映射(scan_config.json 白名单;全默认 None=parity;design 2026-07-11 §4.2)──
    agents: dict | None = None        # {stage: {model, effort}} 覆盖 workflow 内建(Task 2 消费)
    pinned: dict | None = None        # {cap, ttl_days} 保送票参数(Task 3+ 消费)
    l4_intel: dict | None = None      # {enabled,max_queries} 活体情报参数
    l3: dict | None = None            # {two_pass,pass1_target,finalist_max}
    learning: dict | None = None      # {shrink,shrink_k}
    budgets: dict | None = None       # 成本/墙钟/并发观测预算；不拥有截断权限
    performance: dict | None = None   # 流式调度/稳定上下文/行业 brief A/B；不拥有评级语义

    def to_dict(self) -> dict:
        """落 manifest 的纯 dict(可 JSON 序列化)。"""
        return asdict(self)

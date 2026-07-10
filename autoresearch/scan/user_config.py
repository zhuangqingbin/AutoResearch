#!/usr/bin/env python3
"""scan_config.json —— 用户配置层(白名单加载 + ScanConfig 映射)。全波地基(Plan A3 Task 1)。

design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §4.2。

用户在 `.claude/skills/scan-market/scan_config.json` 里管控 scan-market 全程用到的 agent
model/effort、召回旋钮、L3.5 闸选择、保送参数、红队触发率、卡片复用参数——**白名单外的键一律
raise**(防拼写错静默失效,是本文件存在的唯一理由);缺文件 = 现行为(`{}`,一切默认关=parity)。

装载链(技术约束:workflow 脚本无文件系统访问):`frame --json`(Stage 0)读入本模块 → 回显进
market_pack/run meta(trace 记录本次跑用的配置=可复现)→ workflow 经 `args` 消费(Task 2)→
Python 侧 `apply_to_scan_config` 喂 `ScanConfig`(Task 3+ 的 L1/L2/L3.5/L4 各消费点)。
"""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.scan.config import ScanConfig

DEFAULT_PATH = Path(".claude/skills/scan-market/scan_config.json")

# 顶层白名单;funnel/pinned/reuse 额外校验子键(agents/l4_gate 内部形状由各自消费方解释:
# agents={stage: {model, effort}} 无固定 stage 集,Task 2 workflow 直接按需取;l4_gate=
# {name, params},params 形状随 gate 实现而变,Task 3+ gate registry 各自校验)。
_TOP_WHITELIST = {"agents", "l4_gate", "funnel", "pinned", "redteam_prob", "reuse"}
_SUB_WHITELIST = {
    "funnel": {"recall_channels", "channel_quotas", "channel_floors"},
    "pinned": {"cap", "ttl_days"},
    "reuse": {"max_age_days", "price_delta_pct"},
}


def load_user_config(path: str | Path | None = None) -> dict:
    """读 scan_config.json → 白名单校验后的 dict;缺文件 → `{}`(=现行为,parity)。

    未知顶层键、或 funnel/pinned/reuse 内未知子键 → `ValueError`(消息含具体键名)。
    """
    p = Path(path) if path is not None else DEFAULT_PATH
    if not p.exists():
        return {}
    cfg = json.loads(p.read_text(encoding="utf-8"))

    unknown_top = sorted(set(cfg) - _TOP_WHITELIST)
    if unknown_top:
        raise ValueError(f"scan_config.json 含未知顶层键: {unknown_top}(白名单={sorted(_TOP_WHITELIST)})")

    for key, sub_whitelist in _SUB_WHITELIST.items():
        block = cfg.get(key)
        if isinstance(block, dict):
            unknown_sub = sorted(set(block) - sub_whitelist)
            if unknown_sub:
                raise ValueError(f"scan_config.json 的 {key} 含未知子键: {unknown_sub}"
                                 f"(白名单={sorted(sub_whitelist)})")
    return cfg


def apply_to_scan_config(cfg: dict, sc: ScanConfig) -> ScanConfig:
    """把 `load_user_config()` 出的白名单 dict 映射进既有 `ScanConfig`(原地改,返回同一实例)。

    `funnel` 拆到既有字段(recall_channels/channel_quotas/channel_floors);其余键(agents/
    l4_gate/pinned/redteam_prob/reuse)整块挂同名新字段。cfg 中未出现的键保留 sc 原值不动
    (缺配置=parity,不用 None 覆盖已设值)。
    """
    funnel = cfg.get("funnel")
    if funnel:
        if "recall_channels" in funnel:
            sc.recall_channels = funnel["recall_channels"]
        if "channel_quotas" in funnel:
            sc.channel_quotas = funnel["channel_quotas"]
        if "channel_floors" in funnel:
            sc.channel_floors = funnel["channel_floors"]
    for key in ("agents", "l4_gate", "pinned", "redteam_prob", "reuse"):
        if key in cfg:
            setattr(sc, key, cfg[key])
    return sc

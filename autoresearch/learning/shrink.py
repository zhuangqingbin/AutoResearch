#!/usr/bin/env python3
"""基率收缩估计(shrinkage / partial pooling)—— 零 LLM 原语。

design: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4 P0-3(C9-C12)。

病灶:"n<10 一律禁注"(`write_base_rates`/`cross_calib.flip_stats`/`buy_ledger` 的
`_HI2_MIN_N`/`gate_ledger` 的 tail_rate)把小样本桶的真实观测直接弃用——n=3-9 的桶比
"什么都不说"更有信息量,只是方差大。收缩估计(部分池化的最简形式)把桶均值向全局均值拉
一把,拉力由 `k` 控制、随 `n` 增长自动衰减(n→∞ 时收缩值→原始桶值)。

**双轨语义**(brainstorm §4 排序原则,不可混淆):裁决门槛(改不改机制/退役门/切换召回路)
仍用硬 n(≥10/20 门槛文化不变,见各 ledger 自己的 `min_n`/`thin` 字段);本模块只服务
**注入锚**(喂 LLM 读的数字)。`MIN_N_INJECT`(=3)是唯一的绝对下限:n<3 时连收缩值也不
展示(上游按"该行不显示"处理),不受 `shrink`/`shrink_k` 配置开关影响。
"""
from __future__ import annotations

MIN_N_INJECT = 3          # 绝对禁注 floor(双轨语义:不随 shrink/shrink_k 配置变化)
DEFAULT_K = 15.0          # scan_config.jsonc `learning.shrink_k` 缺省值(新基线)


def shrink(p_bucket: float | None, n_bucket: int | float | None,
          p_global: float | None, k: float = DEFAULT_K) -> float | None:
    """p̂ = (n·p_桶 + k·p_全局) / (n+k) —— 部分池化收缩估计(C9-C12)。

    退化处理(优于二值禁注——桶没数据不该交白卷):
    - `n_bucket` 缺失/≤0,或 `p_bucket` 为 `None`(桶本身无观测)→ 原样返回 `p_global`
      (有全局锚就给全局锚,好过什么都没有)。
    - `p_global` 为 `None`(无全局锚可退)→ 原样返回 `p_bucket`(有桶用桶)。
    - 两者皆 `None` → `None`(真无米下炊,调用方按"该行不注入"处理)。
    - `k<=0` → 视为关闭收缩:桶有数据原样回桶值,否则回全局值(等价 raw,不必调用方另包 if)。
    """
    if k is None or k <= 0:
        return p_bucket if p_bucket is not None else p_global
    n = float(n_bucket) if n_bucket else 0.0
    if p_bucket is None or n <= 0:
        return p_global
    if p_global is None:
        return p_bucket
    return (n * p_bucket + k * p_global) / (n + k)


def n_tag(n_bucket: int | float | None, thin_n: int) -> str:
    """四消费点共享的注入格式尾巴:`(n=X)` / 薄样本 `(n=X⚠)`。

    `thin_n` = 调用方所在点**现有**的 ⚠ 阈值(cross_calib/l4_card/target_calib 惯例=10,
    gate_ledger 惯例=5)——本函数不硬编码任何一个点的数字,只统一格式。
    """
    n_i = int(n_bucket) if n_bucket else 0
    return f"(n={n_i}⚠)" if n_i < thin_n else f"(n={n_i})"


def shrink_config(cfg: dict | None = None) -> tuple[bool, float]:
    """读 `scan_config.json` 的 `learning:{shrink, shrink_k}`(白名单见 `user_config.py`)。

    缺配置/缺块/缺子键 → 默认 `(True, DEFAULT_K)`(shrink=true·k=15=新基线,brainstorm §4
    P0-3 Global Constraints)。`cfg` 可传入已 `load_user_config()` 过的 dict 省一次文件 IO;
    传 `None` → 本函数自己 lazy import 再读一次(四个消费点各自独立调用场景更方便)。
    """
    if cfg is None:
        from autoresearch.scan.user_config import load_user_config
        cfg = load_user_config() or {}
    block = (cfg or {}).get("learning") or {}
    return bool(block.get("shrink", True)), float(block.get("shrink_k", DEFAULT_K))

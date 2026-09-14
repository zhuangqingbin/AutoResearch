#!/usr/bin/env python3
"""stock-research 的证据 profile(D6.1)。analyze 不 import scan(分层棘轮)。

`RunProfile` / `ArtifactRule` / `_BASE_RULES` 都来自 `contracts.profiles`——**不是**
`scan.run_profile`:`tests/contracts/test_layering.py` 把 `analyze` 排在 `scan` 之下,
`analyze` import `scan` 会造一条新的向上边,当场被 `test_no_new_upward_edges` 判红。
`_BASE_RULES` 描述的是 capsule 的通用相对路径(identity/、events/、products/ …),
与 run 的 kind 无关,所以两个技能共用同一份完全安全。
"""
from __future__ import annotations

from dataclasses import replace

from autoresearch.contracts import stages as vocab
from autoresearch.contracts.profiles import (
    _BASE_RULES as _SCAN_BASE_RULES,
    ArtifactRule,
    RunProfile,
)

TERMINAL = ("SUCCEEDED", "FAILED", "INTERRUPTED")

#: analyze 复用 scan 的 `_BASE_RULES` 形状,只把 `agent_index` 标 L1(D6.5)——
#: `agents/index.json` 是 harness 自己写的**摘要视图**(每条 invocation 的
#: model/effort/usage 汇总),不是原文;L2(fetch 工具抓到的页面全文)此波没有生产者,
#: 留给 P2。只换 `evidence_level` 一个字段,`key`/`selector`/`source`/`required_when`
#: 逐字不变 —— `evaluate()` 的判定只读那四个字段,所以这一步不可能改变任何一条
#: 规则的 disposition(task-15 红线:只加字段不改判定)。
_ANALYZE_ARTIFACT_RULES: tuple[ArtifactRule, ...] = tuple(
    replace(rule, evidence_level="L1") if rule.key == "agent_index" else rule
    for rule in _SCAN_BASE_RULES
)


def analyze_profile(
    *,
    mode: str = "FULL",
    business_status: str = "SUCCEEDED",
    last_stage: str | None = None,
    agent_roles: tuple[str, ...] | None = None,
    card_source: str = "legacy_md",
    role_stages: dict[str, str] | None = None,
) -> RunProfile:
    """Build the `stock-research` evidence profile for one run's mode and terminal state.

    `agent_roles` 与 `scan_profile` 同款语义(`None` = 按模式取默认):
    `completeness.profile_from_capsule` 要从冻结的 `verification/profile.json` 里把
    **当时那趟**的角色集原样恢复出来,两个 kind 的工厂签名不一致就得在调用点写
    `if kind == …` 分支——那正是 kind 派发想消灭的东西。
    """
    if mode not in vocab.ANALYZE_MODES:
        raise ValueError(f"unknown analyze mode: {mode!r}")
    if business_status not in TERMINAL:
        raise ValueError(f"unknown business status: {business_status!r}")
    lite = mode == "LITE"
    default_roles = () if lite else tuple(vocab.ANALYZE_ROLE_STAGES)
    return RunProfile(
        kind="stock-research",
        expected_stages=vocab.ANALYZE_LITE_STAGES if lite else vocab.ANALYZE_STAGES,
        agent_roles=tuple(agent_roles) if agent_roles is not None else default_roles,
        artifact_rules=_ANALYZE_ARTIFACT_RULES,
        # Legacy runs have no Session ReplayPlan.  session_agent.publication projects
        # this field from the actual frozen deterministic tasks before finalization.
        replayable_stages=(),
        mode=mode,
        business_status=business_status,
        last_stage=last_stage,
        conditional_roles=vocab.ANALYZE_CONDITIONAL_ROLES,
        # 单票研究**不套** traced 壳(设计稿 §1.4/Q2:每条命令 3 次 agent spawn 的成本病),
        # 所以没有任何阶段欠 `logs/<stage>/*.log.gz`。留痕走 `analyze/runctl.record_stage`
        # 的进程内 checkpoint。声明成 `()` 而不是让它恒判缺失 —— 假警报不是发现。
        captured_stages=(),
        card_source=card_source,
        role_stages=role_stages,
    )


__all__ = ["analyze_profile"]

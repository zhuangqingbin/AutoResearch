#!/usr/bin/env python3
"""契约层 —— 「该有什么」「产出长什么样」的唯一声明,零业务逻辑、零 IO。

design: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.4 A1/A2。

本包**只被 import,不 import 上层**:`scan/`、`trace/`、`sector/`、`macro/` 从这里派生
自己的登记表与解析器 pattern,反向依赖(contracts → scan)一律是分层测试要逮的东西。

- `stages`       —— 阶段 / 模式 / 角色词汇(五份互不派生的登记表的共同上游)
- `artifacts`    —— 产物登记表(名字、路径、根、阶段、形态、在场条件、可否重放)
- `agent_output` —— agent 产出语法(评级序、卡面 keyed 行、L3 judged 键、地形段锚)
"""
from autoresearch.contracts import agent_output, artifacts, stages
from autoresearch.contracts.agent_output import (
    CONTRACTS,
    OW_GATES,
    PROPOSALS,
    RATING_ORDER,
    STOP_REASONS,
    OutputContract,
    contract,
    rank_of,
)
from autoresearch.contracts.artifacts import (
    ARTIFACTS,
    Artifact,
    by_name,
    for_root,
    for_stage,
    replayable,
)
from autoresearch.contracts.stages import (
    MODES,
    REPLAYABLE_STAGES,
    ROLE_STAGES,
    STAGES,
    normalize_stage,
    skips_l4,
)

__all__ = [
    "ARTIFACTS", "CONTRACTS", "MODES", "OW_GATES", "PROPOSALS", "RATING_ORDER",
    "REPLAYABLE_STAGES", "ROLE_STAGES", "STAGES", "STOP_REASONS",
    "Artifact", "OutputContract",
    "agent_output", "artifacts", "stages",
    "by_name", "contract", "for_root", "for_stage", "normalize_stage",
    "rank_of", "replayable", "skips_l4",
]

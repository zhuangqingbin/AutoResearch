#!/usr/bin/env python3
"""L5Assemble —— 整合段:漏斗溯源 + 三段 summary + trace/ 发布(Stage 契约薄封装)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A/§D;Plan 4.1。

L5 是**确定性**段(零 LLM),但产物形态与 L0/L1/L2 不同:它读 `context/scan/<date>/` 的 staging
漏斗产物(meta + L1/L2 csv + finalists + L4 决策卡 + verify),用 `parse_rating` 提五档评级,产出
人看的 `reports/scan/<run>/`(summary.md + details/ + trace/ 溯源)。全部逻辑在
`autoresearch.scan.assemble`;本 Stage 只把它接进 pipeline 的 Stage 契约(让 L5 也能被 Pipeline 编排)。

`inputs()`/`outputs()` 返回 []:L5 不读/写 **typed trace parquet 段**(它读 staging dir、写 reports
渲染视图),故对 pipeline 的断点续跑判定恒"不可跳过"(每次都重新发布最新视图)。
"""
from __future__ import annotations

from autoresearch.scan import assemble
from autoresearch.scan.context import RunContext
from autoresearch.scan.stages.base import Stage


class L5Assemble(Stage):
    """L5:调 assemble.run(analysis_date) 发布 summary + details + trace/(确定性)。"""

    name = "L5Assemble"

    def inputs(self) -> list[str]:
        return []

    def outputs(self) -> list[str]:
        return []

    def run(self, ctx: RunContext) -> None:
        assemble.run(ctx.analysis_date)

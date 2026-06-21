#!/usr/bin/env python3
"""Pipeline —— 按序跑确定性扫描段(L0→L1→L2),支持断点续跑 / --from。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A("pipeline.py")。

段间**只经 trace 产物通信**:每段从 ctx.trace 读上游产物 → 写自己的产物。`run(resume=...)`:
若 `resume` 且某段全部 outputs 已在 trace 且 manifest status=done → 跳过该段(断点续跑);
`from_stage` 指定从某段起强制重跑(其前的段若产物在则不重跑、其后照常)。返回 run_id。
"""
from __future__ import annotations

import sys

from autoresearch.scan.context import RunContext
from autoresearch.scan.stages.base import Stage
from autoresearch.scan.stages.l0_universe import L0Universe
from autoresearch.scan.stages.l1_recall import L1Recall
from autoresearch.scan.stages.l2_rank import L2Rank


class Pipeline:
    """有序确定性段流水线:[L0Universe, L1Recall, L2Rank]。"""

    def __init__(self, stages: list[Stage] | None = None):
        self.stages: list[Stage] = stages if stages is not None else [
            L0Universe(), L1Recall(), L2Rank()]

    def _can_skip(self, ctx: RunContext, stage: Stage) -> bool:
        """该段可跳过(续跑):全部 outputs 已物化 且 manifest 标记 done。"""
        outs = stage.outputs()
        if not outs:
            return False
        return all(ctx.trace.has_stage(ctx.run_id, o) and ctx.trace.stage_done(ctx.run_id, o)
                   for o in outs)

    def run(self, ctx: RunContext, *, resume: bool = False, from_stage: str | None = None) -> str:
        """按序跑各段;返回 run_id。

        - `from_stage` 指定后,命中该段名起的所有段**强制重跑**(forcing);其前的段仍按 resume 判定。
        - `resume`:某段全部 outputs 已物化且 manifest done → 跳过(断点续跑)。
        - 二者皆缺:每段都跑。
        """
        forcing = False
        for stage in self.stages:
            if from_stage is not None and stage.name == from_stage:
                forcing = True           # 命中起点 → 从这段起强制重跑
            if forcing:
                stage.run(ctx)
                continue
            if resume and self._can_skip(ctx, stage):
                print(f"[pipeline] skip {stage.name}(产物已在 trace,done)", file=sys.stderr)
                continue
            stage.run(ctx)
        return ctx.run_id

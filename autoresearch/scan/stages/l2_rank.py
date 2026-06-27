#!/usr/bin/env python3
"""L2Rank —— 粗排段:champion 模型重排 recall → top l2_n → 写 L2_rank。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A/§C;Plan 3.3。

等价于 `screen_market.run` 的 L2:那里用 `factor_lab.predict_scores`(GBDT;oos 未胜线性即回落
`recall.head(l2_n)` = composite top200)。本段改用统一模型框架的 **champion**(models store 现任;
缺 store → 默认 `LinearComposite`)。LinearComposite.predict == composite_score 的 composite 列,
而 recall 已按 composite 降序 → 重排 top l2_n **逐值复现** screen_market 的线性回落路径(parity 锁死)。

换模型 = store 里晋升一个赢过线性的 champion(Trainer + champion 门),本段不动。
"""
from __future__ import annotations

import sys

from autoresearch.scan.context import RunContext
from autoresearch.scan.stages.base import Stage
from autoresearch.scan.stages.l1_recall import _KEEP
from autoresearch.trace import schema


class L2Rank(Stage):
    """L2:champion.predict(recall) → top l2_n。写 L2_rank,manifest 记 l2_engine。"""

    name = "L2Rank"

    def inputs(self) -> list[str]:
        return [schema.L1_RECALL]

    def outputs(self) -> list[str]:
        return [schema.L2_RANK]

    def run(self, ctx: RunContext) -> None:
        recall = ctx.trace.get_df(ctx.run_id, schema.L1_RECALL)
        recall["code"] = recall["code"].astype(str).str.zfill(6)
        l2_n = ctx.config.l2_n

        # L2 确定性分层多样性采样(与 universe.run 共用 select_l2 → golden parity);ML-free。
        from autoresearch.scan.recall.l2_stratify import select_l2
        l2, engine = select_l2(recall, l2_n, floors=ctx.config.l2_floors,
                               sector_cap_frac=ctx.config.l2_sector_cap)

        cols = ["l2_rank", "l2_score", "l2_lane_reserved", "sector_mom", *_KEEP]
        l2_out = l2[[c for c in cols if c in l2.columns]]
        ctx.trace.put_df(ctx.run_id, schema.L2_RANK, l2_out)
        ctx.trace.put_meta(ctx.run_id, {"l2_n": int(len(l2)), "l2_engine": engine,
                                        "l2_sector_cap": float(ctx.config.l2_sector_cap)})
        print(f"[L2 粗排] recall {len(recall)} → {engine} top {len(l2)}", file=sys.stderr)

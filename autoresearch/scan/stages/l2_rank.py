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

from autoresearch.models.linear import LinearComposite
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

        # champion 重排(与 universe.run 共用 champion_scores,口径一致 → golden parity);
        # 无 champion / predict 失败 → 回落线性复合分(= composite,绝不比线性差)。
        from autoresearch.scan.l2_model import champion_scores
        scores, engine = champion_scores(recall, ctx.config.l2_model)
        if scores is None:
            scores = LinearComposite().predict(recall)
            engine = "composite-linear(default)"
        # 稳定排序:recall 已按 composite 降序,无 champion 时 l2_score==composite → 逐位复现 head(l2_n)。
        l2 = recall.assign(l2_score=scores.to_numpy()).sort_values(
            "l2_score", ascending=False, kind="stable").head(l2_n).reset_index(drop=True)
        l2.insert(0, "l2_rank", range(1, len(l2) + 1))

        cols = ["l2_rank", "l2_score", *_KEEP]
        l2_out = l2[[c for c in cols if c in l2.columns]]
        ctx.trace.put_df(ctx.run_id, schema.L2_RANK, l2_out)
        ctx.trace.put_meta(ctx.run_id, {"l2_n": int(len(l2)), "l2_engine": engine})
        print(f"[L2 粗排] recall {len(recall)} → {engine} top {len(l2)}", file=sys.stderr)

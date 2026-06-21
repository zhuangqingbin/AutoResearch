#!/usr/bin/env python3
"""L1Recall —— 召回段:多日量价富化 → 复合分 → top recall_n → 写 L1_recall + L1_scored_full。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A;Plan 3.3。

**逐行照搬 `scan.universe.run` 的 L1 召回块**(复用 `_harvest_vol_series` / `_load_weights` /
`composite_score`,绝不重写打分):
  vps = _harvest_vol_series(codes, date); merge → composite_score(uni, weights)
  → recall = scored.sort_values("composite").head(recall_n)
  → full   = scored.sort_values("composite") + rank/recalled
唯一区别:读上游 L0_universe 来自 trace、产物写回 trace(L1_recall / L1_scored_full),而非内存传参。
展示列(keep)与 scan.universe.run 落 CSV 时的列表一致。
"""
from __future__ import annotations

import sys

from autoresearch.common.scoring import _GROUPS, _load_weights, composite_score
from autoresearch.scan.context import RunContext
from autoresearch.scan.stages.base import Stage
from autoresearch.trace import schema

# scan.universe.run 落 L1/L2 CSV 时的 keep 列(逐字一致)——trace 产物按此裁列(有则保留)。
_KEEP = (["code", "name", "industry", "composite"] + [f"score_{g}" for g in _GROUPS]
         + ["mktcap_yi", "close", "amount_yi", "vol_ratio", "turnover", "cmf_20", "obv_mom_20",
            "pct_60d", "pct_ytd", "main_inflow_yi", "main_net_ratio",
            "retail_net_yi", "winner_rate", "chip_concentration", "price_to_cost", "hk_ratio",
            "rsi6", "rsi12", "pe", "pb", "dv_ratio", "np_yoy", "rev_yoy", "roe",
            "ma_bull", "above_ma60"])


class L1Recall(Stage):
    """L1:_harvest_vol_series + composite_score → top recall_n。写 L1_recall + L1_scored_full。"""

    name = "L1Recall"

    def inputs(self) -> list[str]:
        return [schema.L0_UNIVERSE]

    def outputs(self) -> list[str]:
        return [schema.L1_RECALL, schema.L1_SCORED_FULL]

    def run(self, ctx: RunContext) -> None:
        from autoresearch.scan import universe as smu
        uni = ctx.trace.get_df(ctx.run_id, schema.L0_UNIVERSE)
        uni["code"] = uni["code"].astype(str).str.zfill(6)
        recall_n = ctx.config.recall_n

        # 多日量价序列(CMF/OBV/...)→ volprice 组(scan.universe.run 同款,失败返回空帧不破)。
        vps = smu._harvest_vol_series(uni["code"], ctx.analysis_date)
        if len(vps):
            uni = uni.merge(vps, on="code", how="left")

        weights = _load_weights()
        scored = composite_score(uni, weights)
        recall = scored.sort_values("composite", ascending=False).head(recall_n).reset_index(drop=True)

        # 全量打分(过门股按 composite 降序 + rank + recalled 标记)——trace 留全阶段不截断。
        full = scored.sort_values("composite", ascending=False).reset_index(drop=True)
        full.insert(0, "rank", range(1, len(full) + 1))
        full.insert(1, "recalled", full["rank"] <= recall_n)

        # L1_recall 留**全列**(_KEEP 在前、其余因子列在后):L2 champion 需在同一帧上 re-predict
        # 复合分才能逐值复现 screen_market.run 的 L2 回落(只裁列会让缺失因子组变 NaN → 破 parity)。
        keep_first = [c for c in _KEEP if c in recall.columns]
        rest = [c for c in recall.columns if c not in keep_first]
        recall_full = recall[keep_first + rest]
        full_keep = full[["rank", "recalled"] + [c for c in _KEEP if c in full.columns]]
        ctx.trace.put_df(ctx.run_id, schema.L1_RECALL, recall_full)
        ctx.trace.put_df(ctx.run_id, schema.L1_SCORED_FULL, full_keep)
        ctx.trace.put_meta(ctx.run_id, {
            "recall_n": int(len(recall)),
            "weights_source": weights.get("meta", {}).get("source", "weights.json"),
        })
        print(f"[L1 召回] 轻门 {len(uni)} → 复合分 top {len(recall)}", file=sys.stderr)

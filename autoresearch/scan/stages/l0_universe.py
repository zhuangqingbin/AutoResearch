#!/usr/bin/env python3
"""L0Universe —— 选集段:取全 A universe → 轻召回门 → 写 L0_universe。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A;Plan 3.3。

**复用包内确定性取数/门函数,绝不重写**:
  - universe:`tushare_source.fetch_universe_tushare`(source=tushare,默认)或
    `akshare_universe.fetch_universe`(em 路径)。
  - 轻召回门:`scan.universe._recall_gate_a`(只去真不可交易/无核心数据的尾部)。
等价于 `scan.universe.run` 的 "L0 取数 + `uni = uni[_recall_gate_a(uni)]`" 这一段——逐函数照搬,
唯一区别是产物写进 typed trace(L0_universe)而非内存里传给 L1。
"""
from __future__ import annotations

import sys

from autoresearch.scan.context import RunContext
from autoresearch.scan.stages.base import Stage
from autoresearch.trace import schema


class L0Universe(Stage):
    """L0:fetch_universe(_tushare) + _recall_gate_a → L0_universe(过门后的 canonical 全 A)。"""

    name = "L0Universe"

    def inputs(self) -> list[str]:
        return []

    def outputs(self) -> list[str]:
        return [schema.L0_UNIVERSE]

    def run(self, ctx: RunContext) -> None:
        from autoresearch.scan import universe as smu
        cfg = ctx.config
        if cfg.source == "tushare":
            # 与 scan.universe.run 同款:fetch_universe_tushare 是 tushare_source 的函数(run() 内局部
            # import 它,并不挂为模块属性)→ 从同一模块取,patch 点也在那。
            from autoresearch.data import tushare_source as ts
            uni = ts.fetch_universe_tushare(
                ctx.analysis_date, cap_floor_yi=cfg.cap_floor, include_bj=cfg.include_bj)
            n_raw = ts._RAW_COUNT.get("n", len(uni))
        else:
            from autoresearch.data import akshare_universe as aku
            uni = aku.fetch_universe(
                ctx.analysis_date, cap_floor_yi=cfg.cap_floor, include_bj=cfg.include_bj)
            n_raw = aku._GATE_INFO.get("n_raw", len(uni))
        n_l0 = len(uni)

        # 轻召回门(scan.universe.run 的 `uni = uni[_recall_gate_a(uni)]`)+ code 规整。
        uni = uni[smu._recall_gate_a(uni)].reset_index(drop=True)
        uni["code"] = uni["code"].astype(str).str.zfill(6)

        ctx.trace.put_df(ctx.run_id, schema.L0_UNIVERSE, uni)
        ctx.trace.put_meta(ctx.run_id, {
            "analysis_date": ctx.analysis_date,
            "config": cfg.to_dict(),
            "universe_raw": int(n_raw),
            "universe": int(n_l0),
            "after_gate_a": int(len(uni)),
        })
        print(f"[L0] universe_raw={n_raw} → L0 {n_l0} → 轻门 {len(uni)}", file=sys.stderr)

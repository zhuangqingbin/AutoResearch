#!/usr/bin/env python3
"""golden 对拍 —— 锁住"新 Stage 管道 ≡ scan.universe.run 的确定性产物"。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §E。

- `capture(date, out)`:跑现 `scan.universe.run(date)`,把它的 L1_recall_top1000.csv +
  L2_gbdt_top200.csv 快照成 golden(out/<...>.csv)。
- `check(date)`:在**同一 lake / 同 weights / 同源**上跑新 `Pipeline`,把 trace 的 L1_recall /
  L2_rank 与 golden 对拍。**不变量按集合 + 排序对**(非逐位浮点):召回集合一致、L1 名次一致、
  composite 1e-9 容差、L2 top-l2_n 集合 + 名次一致即过(§E "不变量按集合+排序对")。

返回 `ParityResult`(ok + 各项 diff 明细),不抛错——调用方/测试据此 assert。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from autoresearch.scan.config import ScanConfig
from autoresearch.scan.context import RunContext
from autoresearch.scan.pipeline import Pipeline
from autoresearch.trace import schema
from autoresearch.trace.store import TraceStore

_COMPOSITE_TOL = 1e-9


@dataclass
class ParityResult:
    """一次对拍结果:ok + 逐项 diff 说明(空 list = 该项一致)。"""

    ok: bool = True
    l1_set_diff: list[str] = field(default_factory=list)     # 召回集合不一致(symmetric diff codes)
    l1_order_diff: list[str] = field(default_factory=list)   # L1 名次不一致(codes where rank differs)
    l1_composite_diff: list[str] = field(default_factory=list)  # composite 超 1e-9 容差的 code
    l2_set_diff: list[str] = field(default_factory=list)     # L2 top-l2_n 集合不一致
    l2_order_diff: list[str] = field(default_factory=list)   # L2 名次不一致
    notes: dict = field(default_factory=dict)

    def summary(self) -> str:
        bits = []
        for k in ("l1_set_diff", "l1_order_diff", "l1_composite_diff", "l2_set_diff", "l2_order_diff"):
            v = getattr(self, k)
            bits.append(f"{k}={len(v)}")
        return f"parity ok={self.ok} | " + " ".join(bits)


def capture(date: str, out: str | Path, *, config: ScanConfig | None = None) -> dict:
    """跑现 scan.universe.run(date),把 L1_recall / L2 CSV 快照到 out。返回各产物路径。"""
    from autoresearch.scan import universe as smu

    cfg = config or ScanConfig()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    smu.run(date, cap_floor_yi=cfg.cap_floor, include_bj=cfg.include_bj,
            recall_n=cfg.recall_n, l2_n=cfg.l2_n, outdir=out, source=cfg.source)
    return {"L1_recall": out / "L1_recall_top1000.csv", "L2_rank": out / "L2_gbdt_top200.csv"}


def _ordered_codes(df: pd.DataFrame) -> list[str]:
    return df["code"].astype(str).str.zfill(6).tolist()


def diff_recall(golden_l1: pd.DataFrame, new_l1: pd.DataFrame) -> ParityResult:
    """对拍 L1_recall:召回集合 / 名次顺序 / composite(1e-9)。"""
    res = ParityResult()
    g = golden_l1.copy()
    n = new_l1.copy()
    g["code"] = g["code"].astype(str).str.zfill(6)
    n["code"] = n["code"].astype(str).str.zfill(6)
    gset, nset = set(g["code"]), set(n["code"])
    if gset != nset:
        res.ok = False
        res.l1_set_diff = sorted(gset ^ nset)
    gc, nc = _ordered_codes(g), _ordered_codes(n)
    if gc != nc:
        res.ok = False
        # 名次不一致的 code:同位不同码(逐位比对到较短长度;集合不一致时长度可不同,故 strict=False)
        res.l1_order_diff = [f"{i}:{a}!={b}"
                             for i, (a, b) in enumerate(zip(gc, nc, strict=False)) if a != b][:50]
    # composite 逐 code 比(1e-9)
    if "composite" in g.columns and "composite" in n.columns:
        gm = g.set_index("code")["composite"].astype(float)
        nm = n.set_index("code")["composite"].astype(float)
        common = gm.index.intersection(nm.index)
        d = (gm.loc[common] - nm.loc[common]).abs()
        bad = d[d > _COMPOSITE_TOL]
        if len(bad):
            res.ok = False
            res.l1_composite_diff = sorted(bad.index.tolist())[:50]
        res.notes["l1_composite_max_abs_diff"] = float(d.max()) if len(d) else 0.0
    return res


def diff_l2(golden_l2: pd.DataFrame, new_l2: pd.DataFrame, res: ParityResult | None = None) -> ParityResult:
    """对拍 L2:top-l2_n 集合 + 名次顺序。"""
    res = res or ParityResult()
    g = golden_l2.copy()
    n = new_l2.copy()
    g["code"] = g["code"].astype(str).str.zfill(6)
    n["code"] = n["code"].astype(str).str.zfill(6)
    gset, nset = set(g["code"]), set(n["code"])
    if gset != nset:
        res.ok = False
        res.l2_set_diff = sorted(gset ^ nset)
    gc, nc = _ordered_codes(g), _ordered_codes(n)
    if gc != nc:
        res.ok = False
        res.l2_order_diff = [f"{i}:{a}!={b}"
                             for i, (a, b) in enumerate(zip(gc, nc, strict=False)) if a != b][:50]
    return res


def check(date: str, golden_dir: str | Path, *, config: ScanConfig | None = None,
          trace_root: str | Path | None = None) -> ParityResult:
    """跑新 Pipeline → 对拍 trace 的 L1_recall / L2_rank 与 golden CSV。返回 ParityResult。"""
    cfg = config or ScanConfig()
    golden_dir = Path(golden_dir)
    store = TraceStore(trace_root) if trace_root is not None else TraceStore()
    ctx = RunContext(analysis_date=date, config=cfg, trace=store)
    run_id = Pipeline().run(ctx)

    new_l1 = store.get_df(run_id, schema.L1_RECALL)
    new_l2 = store.get_df(run_id, schema.L2_RANK)
    golden_l1 = pd.read_csv(golden_dir / "L1_recall_top1000.csv")
    golden_l2 = pd.read_csv(golden_dir / "L2_gbdt_top200.csv")

    res = diff_recall(golden_l1, new_l1)
    res = diff_l2(golden_l2, new_l2, res)
    res.notes["run_id"] = run_id
    return res

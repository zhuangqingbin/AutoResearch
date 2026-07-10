#!/usr/bin/env python3
"""L2 引擎 A/B 评估 —— stratified(分层采样)vs champion(学习排序)前向收益对照。零 LLM。

design: docs/specs/2026-06-27-scan-l0-l5-optimizations-design.md §B。

动机:L2 现用确定性分层采样(回测"确定性 L2 无稳健 alpha");但"确定性无 alpha ≠ 学习排序无 alpha"——
champion(zoo 训的 l2_fwd5)有无 alpha 未充分验证就弃用了。本 harness 在**同一召回帧**上比两者
top-N 的前向收益(mean_fwd / hit),给"是否把 champion 接回 L2"一个数据判据(而非拍脑袋)。

verdict 需真数据跑(本模块只交付可复现机制 + 单测核心)。结论为正再考虑把 ScanConfig.l2_engine 接到线上。

用法:uv run --no-sync python -m autoresearch.research.l2_eval [--l2-n 200] [--label fwd_5_oc] [--cap-floor 30]
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd


def _topn_metrics(sub: pd.DataFrame, label_col: str) -> dict:
    """选中子集 → {mean_fwd, hit(>0 占比), n}。缺 label 列/空 → NaN/0。"""
    fwd = (pd.to_numeric(sub[label_col], errors="coerce").dropna()
           if label_col in sub.columns else pd.Series([], dtype=float))
    if not len(fwd):
        return {"mean_fwd": float("nan"), "hit": float("nan"), "n": 0}
    return {"mean_fwd": round(float(fwd.mean()), 5), "hit": round(float((fwd > 0).mean()), 4), "n": int(len(fwd))}


def forward_compare(panel: pd.DataFrame, *, l2_n: int = 200, label_col: str = "fwd_2_oc",
                    l2_model: str = "l2_fwd5", floors: dict | None = None,
                    sector_cap_frac: float = 0.20, champion_fn=None) -> dict:
    """同一 panel 上 stratified vs champion 各选 top l2_n,比前向 `label_col`。

    `champion_fn(panel)->scores` 可注入(测试);否则用真 champion(`l2_model`,store 无→champion=None,优雅)。
    返回 {stratified, champion, delta(champion−stratified), l2_n, label_col, champion_engine}。
    """
    from autoresearch.scan.recall.l2_stratify import select_l2

    strat, _eng = select_l2(panel, l2_n, floors=floors, sector_cap_frac=sector_cap_frac)
    out: dict = {"stratified": _topn_metrics(strat, label_col), "champion": None,
                 "l2_n": int(l2_n), "label_col": label_col,
                 "champion_engine": "injected" if champion_fn is not None else None}

    if champion_fn is not None:
        scores = champion_fn(panel)
    else:
        from autoresearch.scan.l2_model import champion_scores
        scores, out["champion_engine"] = champion_scores(panel, l2_model)

    if scores is not None:
        ranked = panel.assign(_s=np.asarray(scores)).sort_values("_s", ascending=False).head(l2_n)
        out["champion"] = _topn_metrics(ranked, label_col)
        sm, cm = out["stratified"]["mean_fwd"], out["champion"]["mean_fwd"]
        out["delta"] = round(cm - sm, 5) if (sm == sm and cm == cm) else float("nan")
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    from autoresearch.common.scoring import _load_weights, composite_score
    from autoresearch.research import factor_lab as fl

    ap = argparse.ArgumentParser(prog="python -m autoresearch.research.l2_eval",
                                 description="L2 stratified vs champion 前向对照")
    ap.add_argument("--l2-n", type=int, default=200)
    ap.add_argument("--label", default="fwd_5_oc", help="前向标签列(fwd_1_oo/fwd_5_oc/fwd_10_oc)")
    ap.add_argument("--cap-floor", type=float, default=30.0)
    ap.add_argument("--l2-model", default="l2_fwd5")
    args = ap.parse_args(argv)

    frames = fl._all_frames(args.cap_floor)
    if not frames:
        print("无成型日(先 factor_lab harvest)", file=sys.stderr)
        return 1
    weights = _load_weights()
    deltas = []
    for fr in frames:
        scored = composite_score(fr, weights)
        if args.label not in scored.columns:
            continue
        res = forward_compare(scored, l2_n=args.l2_n, label_col=args.label, l2_model=args.l2_model)
        if res.get("champion") and res.get("delta") == res.get("delta"):   # delta 非 NaN
            deltas.append(res["delta"])
            print(f"  {fr['date'].iloc[0]}: strat {res['stratified']['mean_fwd']:+.4f} "
                  f"champ {res['champion']['mean_fwd']:+.4f} Δ {res['delta']:+.4f}")
    if deltas:
        mean_d = float(np.mean(deltas))
        verdict = "champion 占优 → 可考虑接回 L2" if mean_d > 0 else "stratified 占优/打平 → 维持现状"
        print(f"[l2_eval] {len(deltas)} 日;champion−stratified 平均 Δ {mean_d:+.5f}({verdict})")
    else:
        print(f"[l2_eval] champion 不可用(store 无 {args.l2_model})或无 {args.label} 列 → 无对照", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

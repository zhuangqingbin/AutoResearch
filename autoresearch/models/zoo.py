#!/usr/bin/env python3
"""zoo 训练 runner —— horizons × 全 zoo 模型,每 horizon 晋升胜线性的 champion。

design: docs/specs/2026-06-22-l2-zoo-champion-design.md §P-B。

- 外层 horizon(fwd_1_oo/fwd_5_oc/fwd_10_oc)× 内层 catalog.ported()(20):按 model.feature_set
  物化 core/seq/graph,Trainer(label=horizon).train → oos rank-IC。
- **故障隔离**:单模型异常(torch OOM/不收敛/接口不符)→ status=error 跳过,不中断全 zoo。
- **champion 门**:该 horizon 下 oos rank-IC 最高且 **严格 > 线性基线** → save_champion(l2_<h>);
  无人胜线性 → 不晋升(L2 回落线性,铁律不自欺)。
- 产出 leaderboard(model×horizon×IC vs 线性)→ out_csv,champion 落 models/store/l2_<h>/。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from autoresearch.models.catalog import MODELS, ported
from autoresearch.models.registry import ModelConfig
from autoresearch.models.trainer import STORE_ROOT, Trainer, save_champion

# horizon → champion 名(L2 默认加载 l2_fwd5)。
_TAGS = {"fwd_1_oo": "l2_fwd1", "fwd_5_oc": "l2_fwd5", "fwd_10_oc": "l2_fwd10"}


def _tag(horizon: str) -> str:
    return _TAGS.get(horizon, f"l2_{horizon}")


def _resolve_models(names):
    """[(name, kind, feature_set)];names 缺省 = catalog.ported()(20)。"""
    names = names or ported()
    return [(n, MODELS[n]["kind"], MODELS[n]["feature_set"]) for n in names]


def _train_one(handler, cfg, dates, label, *, price_dates=None, cap_floor=30.0):
    """训练单模型(seam:测试可 monkeypatch 注入故障)。返回 TrainedModel。"""
    return Trainer(handler, label=label).train(cfg, dates, price_dates=price_dates, cap_floor=cap_floor)


def train_zoo(handler, dates, horizons, model_names=None, *, price_dates=None,
              cap_floor=30.0, store_root=None, out_csv=None) -> pd.DataFrame:
    """对 horizons × model_names 逐个训练;每 horizon 晋升胜线性的 champion。返回 leaderboard。"""
    store_root = Path(store_root) if store_root else STORE_ROOT
    rows = []
    base_by_h: dict[str, float] = {}
    for horizon in horizons:
        results: dict[str, tuple] = {}   # name -> (TrainedModel, ic)
        for name, kind, fset in _resolve_models(model_names):
            cfg = ModelConfig(kind=kind, feature_set=fset)
            try:
                trained = _train_one(handler, cfg, dates, horizon,
                                     price_dates=price_dates, cap_floor=cap_floor)
                ic = float(trained.oos_rank_ic)
                results[name] = (trained, ic)
                rows.append({"horizon": horizon, "model": name, "feature_set": fset,
                             "oos_rank_ic": ic, "status": "ok"})
            except Exception as e:  # noqa: BLE001 — 单模型隔离,不毁全 zoo
                rows.append({"horizon": horizon, "model": name, "feature_set": fset,
                             "oos_rank_ic": float("nan"), "status": f"error:{type(e).__name__}"})
                print(f"[zoo] {horizon}/{name} 失败: {e!r}", file=sys.stderr)
        # 线性基线(NaN/缺 → 0.0);gate 与 leaderboard vs_linear 共用同一 base。
        lin_ic = results.get("linear", (None, float("nan")))[1]
        base = lin_ic if lin_ic == lin_ic else 0.0
        base_by_h[horizon] = base
        # champion 必须**实际有预测力**(ic>0)且严格胜线性 —— 否则负 IC = 反预测,部署反伤;
        # 无合格者 → 不晋升,L2 回落 composite(中性召回序,交 L3 做真判断)。
        winners = {n: ic for n, (_, ic) in results.items()
                   if ic == ic and ic > base and ic > 0 and n != "linear"}
        if winners:
            best = max(winners, key=winners.get)
            save_champion(_tag(horizon), results[best][0], "v1", root=store_root)
            print(f"[zoo] {horizon} champion = {best} (ic {winners[best]:+.4f} > 线性 {base:+.4f}, 正)",
                  file=sys.stderr)
        else:
            print(f"[zoo] {horizon} 无正-IC 模型胜线性(线性 {base:+.4f}) → 不晋升,L2 回落 composite",
                  file=sys.stderr)

    lb = pd.DataFrame(rows)
    lb["vs_linear"] = lb.apply(
        lambda r: (r["oos_rank_ic"] - base_by_h.get(r["horizon"], 0.0)) if r["status"] == "ok"
        else float("nan"), axis=1)
    if out_csv:
        Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
        lb.to_csv(out_csv, index=False)
    return lb


if __name__ == "__main__":
    import argparse

    from autoresearch.data.handler import DataHandler
    from autoresearch.data.harvest import _trade_days_live, plan_harvest

    ap = argparse.ArgumentParser(description="zoo 训练 + champion 晋升")
    ap.add_argument("cmd", choices=["train"])
    ap.add_argument("--dates-from", required=True)
    ap.add_argument("--dates-to", required=True)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--horizons", default="fwd_1_oo,fwd_5_oc,fwd_10_oc")
    ap.add_argument("--models", default="", help="逗号分隔;缺省 = 全 zoo")
    ap.add_argument("--out", default="context/factor_lab/zoo_leaderboard.csv")
    a = ap.parse_args()

    cal = _trade_days_live(a.dates_to)
    F, P = plan_harvest(cal, a.dates_from, a.dates_to, a.step)
    names = [m for m in a.models.split(",") if m] or None
    lb = train_zoo(DataHandler(), F, a.horizons.split(","), names, price_dates=P, out_csv=a.out)
    print(lb.to_string(index=False))

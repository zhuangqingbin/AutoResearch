#!/usr/bin/env python3
"""P7 一次性回算:逐日 top3 → 成分等权 fwd_2_oc 中位 vs 合格宇宙中位 → 超额(零 LLM)。

design: docs/specs/2026-07-12-scan-speed-perimeter-design.md §P7 验收前置。
一次性读数脚本(非常驻 harness——遵守 gate_backtest 已删的裁定);分数 = 生产同一函数
`market.sector_healthy_top3`(单一事实源,带参改动自动同步)。数据 = factor_lab CACHE
(daily/daily_basic/moneyflow 逐日 pkl + stock_basic/static.pkl 的 industry)。

  uv run --no-sync python -m autoresearch.research.sector_top3_backtest --days 60
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.data.tushare_source import _code6
from autoresearch.research.factor_lab import (
    CACHE,
    _moneyflow_struct_cols,
    forward_returns,
    load_price_pivots,
)
from autoresearch.scan.market import sector_healthy_top3


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _day_frame(D: str, piv: dict, P: list[str], industry: pd.DataFrame) -> pd.DataFrame | None:
    """D 日横截面:industry/pct_60d/main_net_ratio/cmf_20/pe(与生产 frame 同名列,喂同一分数函数)。"""
    idx = P.index(D)
    if idx < 60:
        return None
    fp_db, fp_mf = CACHE / "daily_basic" / f"{D}.pkl", CACHE / "moneyflow" / f"{D}.pkl"
    if not fp_db.exists() or not fp_mf.exists():
        return None
    db, mf = pd.read_pickle(fp_db), pd.read_pickle(fp_mf)
    if db.empty or mf.empty:
        return None
    f = pd.DataFrame({"code": _code6(db["ts_code"]), "pe": _num(db["pe_ttm"])})
    C = piv["close"]
    f["pct_60d"] = ((C[D] / C[P[idx - 60]] - 1.0) * 100).reindex(f["code"]).to_numpy()
    win = P[idx - 19:idx + 1]
    import autoresearch.common.vol_series as vs
    H, L, Cc, A = (piv[k][win] for k in ("high", "low", "close", "amount"))
    f["cmf_20"] = vs.cmf(H, L, Cc, A, win).reindex(f["code"]).to_numpy()
    flow = _moneyflow_struct_cols(mf)
    f = f.merge(flow[["code", "main_net_yi"]], on="code", how="left")
    amt_yi = piv["amount"][D].reindex(f["code"]).to_numpy() / 1e5
    f["main_net_ratio"] = f["main_net_yi"] / np.where(amt_yi > 0, amt_yi, np.nan)
    f = f.merge(industry, on="code", how="left")
    return f.dropna(subset=["industry"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P7 top3 一次性回算(fwd_2_oc 超额)")
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args(argv)
    sb = pd.read_pickle(CACHE / "stock_basic" / "static.pkl")
    if "industry" not in sb.columns:
        raise SystemExit("static.pkl 无 industry 列 —— 先跑 factor_lab harvest 刷新 stock_basic")
    industry = pd.DataFrame({"code": _code6(sb["ts_code"]), "industry": sb["industry"].astype(str)})
    P = sorted(p.stem for p in (CACHE / "daily").glob("*.pkl"))
    piv = load_price_pivots(P)
    rows = []
    for D in [d for d in P if P.index(d) >= 60 and P.index(d) + 2 < len(P)][-args.days:]:
        f = _day_frame(D, piv, P, industry)
        if f is None or not len(f):
            continue
        top3 = sector_healthy_top3(f)
        if not top3:
            rows.append({"date": D, "top3": "", "n_top3": 0})
            continue
        names = [r["industry"] for r in top3]
        fr = forward_returns(piv, P, D, fwd=10)["fwd_2_oc"]
        in_top3 = f["industry"].isin(names)
        top_ret = fr.reindex(f.loc[in_top3, "code"]).median() * 100
        mkt_ret = fr.reindex(f["code"]).median() * 100
        rows.append({"date": D, "top3": "|".join(names), "n_top3": len(names),
                     "top3_med_fwd2": round(float(top_ret), 3),
                     "mkt_med_fwd2": round(float(mkt_ret), 3),
                     "excess": round(float(top_ret - mkt_ret), 3)})
    out = pd.DataFrame(rows)
    dst = Path("reports/research/sector_top3_backtest.csv")
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dst, index=False)
    m = out.dropna(subset=["excess"]) if "excess" in out.columns else out.iloc[0:0]
    if len(m):
        print(f"[top3-backtest] n={len(m)} 日 · 平均超额 {m['excess'].mean():+.3f}pp · "
              f"命中率 {(m['excess'] > 0).mean():.0%} · 中位 {m['excess'].median():+.3f}pp → {dst}")
    else:
        print(f"[top3-backtest] 无可算日(CACHE 覆盖不足)→ {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

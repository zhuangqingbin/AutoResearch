#!/usr/bin/env python3
"""0买日市场对照 ledger —— 回答"连续 0 买是纪律还是失明"(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-watchlist-and-health-metrics-design.md §2.3

逐 retro 日取全市场已实现 fwd 均值(attribution.csv,与 channel_eval 同源)+ 当日买单数
→ 跨日对照:0买日之后市场平均怎么走。0买日 mkt_fwd5 为负 = 空仓对了;持续显著为正 =
失明预警(该查召回/门,而不是庆祝纪律)。镜像 channel_ledger 的用法:

  uv run --no-sync python -m autoresearch.learning.zero_buy_ledger   # → reports/learning/zero_buy_ledger.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_COLS = ["date", "n_bought", "n_stocks", "mkt_fwd1", "mkt_fwd5"]


def roll(scan_root: Path | None = None) -> pd.DataFrame:
    """聚合 context/scan/*/retro/attribution.csv → 每日 [date,n_bought,n_stocks,mkt_fwd1,mkt_fwd5]。"""
    scan_root = scan_root or Path("context/scan")
    rows = []
    for p in sorted(Path(scan_root).glob("*/retro/attribution.csv")):
        try:
            df = pd.read_csv(p)
        except Exception:
            continue
        if "fwd_1_oo" not in df.columns or not len(df):
            continue
        bought = df["bought"].astype(str).str.lower().isin(("true", "1")) if "bought" in df.columns \
            else pd.Series(False, index=df.index)
        f1 = pd.to_numeric(df["fwd_1_oo"], errors="coerce")
        f5 = pd.to_numeric(df.get("fwd_5_oc"), errors="coerce") if "fwd_5_oc" in df.columns else pd.Series(dtype=float)
        rows.append({"date": p.parent.parent.name, "n_bought": int(bought.sum()),
                     "n_stocks": int(f1.notna().sum()),
                     "mkt_fwd1": round(float(f1.mean()), 6) if f1.notna().any() else None,
                     "mkt_fwd5": round(float(f5.mean()), 6) if len(f5) and f5.notna().any() else None})
    return pd.DataFrame(rows, columns=_COLS).sort_values("date").reset_index(drop=True)


def render(ledger: pd.DataFrame) -> list[str]:
    """ledger → markdown(逐日表 + 0买日 vs 有买日市场后市对照)。"""
    out = ["# 0买日市场对照(纪律 vs 失明)", ""]
    if ledger is None or not len(ledger):
        return out + ["_无 retro attribution 数据(先跑 scan-retro)_"]

    def f(x):
        return "—" if x is None or pd.isna(x) else f"{x * 100:+.2f}%"

    out += ["| 日期 | 买单 | 全市场fwd_1 | 全市场fwd_5 |", "|---|---|---|---|"]
    for r in ledger.itertuples(index=False):
        out.append(f"| {r.date} | {int(r.n_bought)} | {f(r.mkt_fwd1)} | {f(r.mkt_fwd5)} |")
    zero, some = ledger[ledger["n_bought"] == 0], ledger[ledger["n_bought"] > 0]
    out.append("")
    if len(zero):
        v1, v5 = zero["mkt_fwd1"].mean(), zero["mkt_fwd5"].mean()
        verdict = "空仓方向正确" if (pd.notna(v5) and v5 < 0) or (pd.isna(v5) and pd.notna(v1) and v1 < 0) \
            else "⚠️ 0买日后市为正——查召回/门(失明预警),别只归因纪律"
        out.append(f"- **0买日**({len(zero)} 日):市场 fwd_1 均值 {f(v1)}、fwd_5 均值 {f(v5)} → {verdict}")
    if len(some):
        out.append(f"- **有买日**({len(some)} 日):市场 fwd_1 均值 {f(some['mkt_fwd1'].mean())}、"
                   f"fwd_5 均值 {f(some['mkt_fwd5'].mean())}")
    out.append("")
    out.append("_口径:attribution.csv 全市场等权均值(与 channel_eval 同源);仅供研究。_")
    return out


def main() -> int:
    ledger = roll()
    out = Path("reports/learning/zero_buy_ledger.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(render(ledger)) + "\n", encoding="utf-8")
    print(f"[zero_buy_ledger] {len(ledger)} 日 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""门的跨日 mark-to-market ledger —— 每道 self_review 硬门"拦得对不对"(确定性,零 LLM)。

design: docs/specs/2026-07-02-learning-mtm-design.md §R3

聚合各 scan 日 gate_fires.csv × retro/attribution.csv 的已实现 fwd → 每门 n_fires/被拦票
excess 均值/拦对率。某门持续 excess>0(拦的票反而跑赢)→ 第 4 步提松阈/退役建议(人批)。
门拦对了是功劳簿,拦错了是退役依据——没有这本账,门只会累积成保守棘轮。

  uv run --no-sync python -m autoresearch.learning.gate_ledger   # → reports/learning/gate_ledger.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_COLS = ["check", "n_days", "n_fires", "mean_ex1", "mean_ex5", "hit_rate"]


def roll(scan_root: Path | None = None) -> pd.DataFrame:
    """聚合 context/scan/*/{gate_fires.csv × retro/attribution.csv} → 每门跨日汇总。"""
    scan_root = Path(scan_root or "context/scan")
    rows = []
    for gf in sorted(scan_root.glob("*/gate_fires.csv")):
        attr_p = gf.parent / "retro" / "attribution.csv"
        if not attr_p.exists():
            continue
        try:
            fires = pd.read_csv(gf, dtype={"code": str})
            attr = pd.read_csv(attr_p, dtype={"code": str})
        except Exception:
            continue
        fires = fires[fires["code"].astype(str).str.len() > 0]
        if not len(fires) or "fwd_1_oo" not in attr.columns:
            continue
        fires["code"] = fires["code"].astype(str).str.zfill(6)
        attr["code"] = attr["code"].astype(str).str.zfill(6)
        f1 = pd.to_numeric(attr["fwd_1_oo"], errors="coerce")
        f5 = pd.to_numeric(attr.get("fwd_5_oc"), errors="coerce") if "fwd_5_oc" in attr.columns \
            else pd.Series(dtype=float)
        m1, m5 = f1.mean(), (f5.mean() if len(f5) else float("nan"))
        j = fires.merge(attr[[c for c in ("code", "fwd_1_oo", "fwd_5_oc") if c in attr.columns]],
                        on="code", how="left")
        j["ex1"] = pd.to_numeric(j.get("fwd_1_oo"), errors="coerce") - m1
        j["ex5"] = (pd.to_numeric(j.get("fwd_5_oc"), errors="coerce") - m5) if "fwd_5_oc" in j.columns else None
        j["date"] = gf.parent.name
        rows.append(j)
    if not rows:
        return pd.DataFrame(columns=_COLS)
    alld = pd.concat(rows, ignore_index=True)
    out = alld.groupby("check").agg(
        n_days=("date", "nunique"), n_fires=("code", "size"),
        mean_ex1=("ex1", "mean"), mean_ex5=("ex5", "mean"),
        hit_rate=("ex1", lambda s: float((s.dropna() < 0).mean()) if s.notna().any() else None),
    ).reset_index()
    for c in ("mean_ex1", "mean_ex5", "hit_rate"):
        out[c] = pd.to_numeric(out[c], errors="coerce").round(4)
    return out.sort_values("n_fires", ascending=False).reset_index(drop=True)


def render(ledger: pd.DataFrame) -> list[str]:
    out = ["# 门审计 ledger(被拦票 vs 市场;ex<0 = 拦对)", ""]
    if ledger is None or not len(ledger):
        return out + ["_无 gate_fires × attribution 数据(需 assemble 留痕 + retro 归因)_"]

    def f(x):
        return "—" if x is None or pd.isna(x) else f"{x * 100:+.2f}%"

    out += ["| 门 | 天数 | 拦次 | 被拦ex1 | 被拦ex5 | 拦对率 |", "|---|---|---|---|---|---|"]
    for r in ledger.itertuples(index=False):
        thin = " ⚠样本少" if (r.n_fires or 0) < 5 else ""
        hr = "—" if pd.isna(r.hit_rate) else f"{r.hit_rate:.0%}"
        out.append(f"| {r.check}{thin} | {int(r.n_days)} | {int(r.n_fires)} | "
                   f"{f(r.mean_ex1)} | {f(r.mean_ex5)} | {hr} |")
    out += ["", "_某门持续 ex>0 → 提松阈/退役建议(proposals,人批);别让门无问责地累积。_"]
    return out


def main() -> int:
    ledger = roll()
    out = Path("reports/learning/gate_ledger.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(render(ledger)) + "\n", encoding="utf-8")
    print(f"[gate_ledger] {len(ledger)} 门 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

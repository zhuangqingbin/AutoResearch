#!/usr/bin/env python3
"""scan-market · L2 菜单体检(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-watchlist-and-health-metrics-design.md §2.2

L2 只做多样性采样不做预测 → 它的可优化目标是"菜单质量"。本模块把 06-30 式菜单病
("健康上涨 0 只、全是落刀/贵票")在 L2 一出就用确定性报表喊出来,而不是 L4 烧完
token 才发现。L2 vs 全市场同口径对照;缺文件 → "",缺列 → 对应行降级消失。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def _num(df: pd.DataFrame, col: str) -> pd.Series | None:
    return pd.to_numeric(df[col], errors="coerce") if col in df.columns else None


def _healthy(df: pd.DataFrame) -> int | None:
    """健康上涨计数(06-30 病灶指标;谓词=`scoring.healthy_riser_mask`,与 healthy 召回通道单一事实源)。"""
    from autoresearch.common.scoring import healthy_riser_mask
    m = healthy_riser_mask(df)
    return None if m is None else int(m.sum())


def _knife_share(df: pd.DataFrame) -> float | None:
    p = _num(df, "pct_60d")
    if p is None or not p.notna().any():
        return None
    p = p.dropna()
    return float((p < -20).mean())


def menu_health(scan_dir: Path | str) -> str:
    """L2_gbdt_top200 vs L1_scored_full 的菜单体检块;缺文件 → ""。"""
    scan_dir = Path(scan_dir)
    f2, f1 = scan_dir / "L2_gbdt_top200.csv", scan_dir / "L1_scored_full.csv"
    if not f2.exists() or not f1.exists():
        return ""
    l2, l1 = pd.read_csv(f2), pd.read_csv(f1)
    if not len(l2) or not len(l1):
        return ""
    lines = ["### 🍱 L2 菜单体检(vs 全市场)"]
    if "industry" in l2.columns:
        top = l2["industry"].value_counts().head(3)
        lines.append("- **行业集中度 top3**:" + "、".join(
            f"{k} {v / len(l2):.0%}({v})" for k, v in top.items()))
    k2, k1 = _knife_share(l2), _knife_share(l1)
    if k2 is not None and k1 is not None:
        lines.append(f"- **落刀面**(pct_60d<−20):L2 {k2:.0%} vs 全市场 {k1:.0%}")
    h2, h1 = _healthy(l2), _healthy(l1)
    if h2 is not None and h1 is not None:
        warn = " ⚠️菜单病:健康上涨断供" if h2 == 0 else ""
        lines.append(f"- **健康上涨**(0<pct60<40·主力+·cmf+):L2 {h2}/{len(l2)} "
                     f"vs 全市场 {h1}/{len(l1)}{warn}")
    pe2, pe1 = _num(l2, "pe"), _num(l1, "pe")
    if pe2 is not None and pe1 is not None:
        p2, p1 = pe2[pe2 > 0], pe1[pe1 > 0]
        if len(p2) and len(p1):
            lines.append(f"- **估值**:L2 中位 PE {p2.median():.1f}(PE>60 占 {(p2 > 60).mean():.0%})"
                         f" vs 全市场 {p1.median():.1f}({(p1 > 60).mean():.0%})")
    rsv = _num(l2, "l2_lane_reserved")
    if rsv is not None:
        lines.append(f"- **floor 救回**:{int((rsv > 0).sum())} 只(l2_lane_reserved,风格保底)")
    return "\n".join(lines) + "\n" if len(lines) > 1 else ""


def l4_budget(scan_dir: Path | str, base: int = 30, floor: int = 12) -> tuple[int, str]:
    """菜单感知 L4 预算:病菜单/risk_off 的日子少烧 Opus(design: 2026-07-02-scan-l4-economy §2)。

    三旗:落刀>60% / 健康涨≤2 / regime==risk_off。0 旗=base、1 旗=3/4、≥2 旗=1/2(≥floor)。
    **只降不升**;L2/meta 缺 → (base, parity 注)。机会成本红队 + 观察单兜底防错过。
    """
    scan_dir = Path(scan_dir)
    f2 = scan_dir / "L2_gbdt_top200.csv"
    if not f2.exists():
        return base, f"L2 缺 → 预算={base}(基准,parity)"
    try:
        l2 = pd.read_csv(f2)
    except Exception:  # noqa: BLE001
        return base, f"L2 不可读 → 预算={base}(基准,parity)"
    flags: list[str] = []
    k, h = _knife_share(l2), _healthy(l2)
    if k is not None and k > 0.60:
        flags.append(f"落刀{k:.0%}")
    if h is not None and h <= 2:
        flags.append(f"健康涨仅{h}只")
    mp = scan_dir / "meta.json"
    if mp.exists():
        try:
            import json
            if json.loads(mp.read_text(encoding="utf-8")).get("regime") == "risk_off":
                flags.append("risk_off")
        except Exception:  # noqa: BLE001
            pass
    if not flags:
        return base, f"菜单健康 → 预算={base}(基准)"
    n = max(floor, round(base * 0.75)) if len(flags) == 1 else max(floor, base // 2)
    return n, f"⚠️ {'+'.join(flags)} → L4 预算降至 {n}(基准 {base};省 Opus 于低产日,红队/观察单兜底)"


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="L2 菜单体检 + L4 预算(确定性,零 LLM)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    args = ap.parse_args(argv)
    d = Path("context/scan") / args.date
    print(menu_health(d) or "(菜单体检:staging 缺)")
    n, why = l4_budget(d)
    print(f"[l4_budget] target={n} —— {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

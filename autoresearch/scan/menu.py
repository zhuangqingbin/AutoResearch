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


def zero_buy_streak(scan_dir: Path | str, lookback: int = 10) -> int:
    """今日之前连续 0 买 scan 日数(只数出过卡的日;哨兵/未跑 L4 的日子跳过、不断链)。

    源 = details 卡最终评级(`health.final_ratings`,与 assemble 同口径含 verify 折回);
    碰到最近一个有 Buy/OW 的日即停。lookback 限回看深度(成本上限)。
    2026-07-03 病灶:9 连 0 买日预算仍=30 基准——连败从不是预算函数的输入。
    """
    scan_dir = Path(scan_dir)
    root = scan_dir.parent
    if not root.exists():
        return 0
    from autoresearch.scan.health import final_ratings  # lazy:避免 import cycle
    streak = seen = 0
    for d in sorted((p for p in root.iterdir()
                     if p.is_dir() and p.name[:2] == "20" and p.name < scan_dir.name),
                    reverse=True):
        if seen >= lookback:
            break
        try:
            ratings = final_ratings(d)
        except Exception:  # noqa: BLE001
            continue
        if not ratings:
            continue
        seen += 1
        if any(r in ("Buy", "Overweight") for r in ratings.values()):
            break
        streak += 1
    return streak


def l4_budget(scan_dir: Path | str, base: int = 30, floor: int = 12) -> tuple[int, str]:
    """菜单感知 L4 预算:病菜单/risk_off/0买连败的日子少烧 Opus(design: l4-economy §2 + 2026-07-04 加旗)。

    五旗:落刀>60% / **相对落刀**(>40% 且 >2×全市场,07-03 病灶 45% vs 20% 绝对门抓不住)/
    健康涨≤2 / regime==risk_off / **0买连败≥3**(≥5 计重旗=双份)。
    权重 1=3/4 档、≥2=1/2 档(≥floor)。**只降不升**;L2/meta 缺 → (base, parity 注)。
    机会成本红队 + 观察单兜底防错过。
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
    weight = 0
    k, h = _knife_share(l2), _healthy(l2)
    k1 = None
    f1 = scan_dir / "L1_scored_full.csv"
    if f1.exists():
        try:
            k1 = _knife_share(pd.read_csv(f1))
        except Exception:  # noqa: BLE001
            k1 = None
    if k is not None and k > 0.60:
        flags.append(f"落刀{k:.0%}")
    elif k is not None and k1 and k > 0.40 and k > 2 * k1:
        flags.append(f"落刀{k:.0%}(全市场{k1:.0%}×2=召回错配)")
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
    streak = zero_buy_streak(scan_dir)
    if streak >= 3:
        flags.append(f"0买连败{streak}日" + ("·重旗" if streak >= 5 else ""))
        if streak >= 5:
            weight += 1                      # 重旗:连败≥5 单独就该压到 1/2 档
    if not flags:
        return base, f"菜单健康 → 预算={base}(基准)"
    weight += len(flags)
    n = max(floor, round(base * 0.75)) if weight == 1 else max(floor, base // 2)
    if streak >= 7:                      # 长连败硬压(2026-07-06):再降到 ~1/3 档(≥8),低产日别烧 20 卡
        n = min(n, max(8, base // 3))
        flags.append(f"连败≥7硬压→{n}")
    return n, f"⚠️ {'+'.join(flags)} → L4 预算降至 {n}(基准 {base};省 Opus 于低产日,红队/观察单兜底)"


def should_run_opportunity_redteam(scan_dir: Path | str, every: int = 3) -> tuple[bool, str]:
    """0 买日机会成本红队抽检(2026-07-06):连续 0 买日边际价值递减 → 每 `every` 个 0 买日跑 1 次。
    判据 = zero_buy_streak % every == 1(第 1、1+every、1+2·every… 个连续 0 买日跑)。
    有买单的日子走买单侧,本函数只节流 0 买日红队的频率(编排层据此决定当日是否派红队)。"""
    s = zero_buy_streak(scan_dir)
    if s <= 0:
        return True, "非连续 0 买(首日)→ 跑红队"
    run = (s % every) == 1
    return run, f"0买连败第{s}日 · 每{every}日抽检 → {'跑' if run else '跳过(本轮抽检不覆盖)'}"


def sentinel_advice(scan_dir: Path | str, frac_lo: float = 0.03,
                    frac_hi: float = 0.05) -> tuple[str, str]:
    """哨兵建议(design: 2026-07-03-scan-sentinel-economy §1)。人拍板,不自动降档。

    判据用**全市场**健康上涨占比(L1_scored_full × healthy_riser_mask——不受自家 L2 采样
    影响;healthy 通道上线后 L2 健康数被桶保底"治愈",不能再当判据)× regime:
    <frac_lo → sentinel;frac_lo–frac_hi ∧ risk_off → sentinel;frac_lo–frac_hi → consider;
    其余 → full。07-02(6.2%,range)→ full:通道修好后该日应全扫。
    """
    scan_dir = Path(scan_dir)
    p = scan_dir / "L1_scored_full.csv"
    if not p.exists():
        return "full", "无 L1_scored_full → 全扫(降级)"
    try:
        from autoresearch.common.scoring import healthy_riser_mask
        df = pd.read_csv(p)
        m = healthy_riser_mask(df)
    except Exception:  # noqa: BLE001
        m = None
    if m is None:
        return "full", "健康谓词缺列 → 全扫(降级)"
    frac = float(m.mean())
    regime = None
    mp = scan_dir / "meta.json"
    if mp.exists():
        import contextlib
        import json
        with contextlib.suppress(Exception):
            regime = json.loads(mp.read_text(encoding="utf-8")).get("regime")
    return _sentinel_verdict(frac, regime, frac_lo, frac_hi)


def _sentinel_verdict(frac: float, regime: str | None,
                      frac_lo: float, frac_hi: float) -> tuple[str, str]:
    """哨兵判据核(scan-dir 与帧两入口共用;文案逐字保持)。"""
    pct = f"{frac:.1%}"
    if frac < frac_lo:
        return "sentinel", f"全市场健康上涨仅 {pct}(<{frac_lo:.0%})= 材料枯竭 → 建议哨兵档(跳 L3/L4)"
    if frac < frac_hi and regime == "risk_off":
        return "sentinel", f"健康上涨 {pct} + risk_off → 建议哨兵档"
    if frac < frac_hi:
        return "consider", f"健康上涨 {pct}(<{frac_hi:.0%})材料偏薄 → 可考虑哨兵档,人拍板"
    return "full", f"全市场健康上涨 {pct} 材料充足 → 全扫"


def sentinel_advice_from_frame(frame: pd.DataFrame, frac_lo: float = 0.03,
                               frac_hi: float = 0.05) -> tuple[str, str]:
    """帧入口(盘前 cron / `python -m autoresearch.scan.frame`):同谓词同口径,scan staging 无需存在。

    regime 由 `classify_regime(frame)` 现算(scan-dir 版读 meta.json——那是 universe 落的同一标签)。
    design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.1。
    """
    try:
        from autoresearch.common.scoring import healthy_riser_mask
        m = healthy_riser_mask(frame)
    except Exception:  # noqa: BLE001
        m = None
    if m is None:
        return "full", "健康谓词缺列 → 全扫(降级)"
    from autoresearch.common.regime import classify_regime
    return _sentinel_verdict(float(m.mean()), classify_regime(frame).label, frac_lo, frac_hi)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="L2 菜单体检 + L4 预算 + 哨兵建议(确定性,零 LLM)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    args = ap.parse_args(argv)
    d = Path("context/scan") / args.date
    print(menu_health(d) or "(菜单体检:staging 缺)")
    n, why = l4_budget(d)
    print(f"[l4_budget] target={n} —— {why}")
    level, reason = sentinel_advice(d)
    print(f"[sentinel] {level} —— {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

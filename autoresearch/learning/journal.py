#!/usr/bin/env python3
"""扫描日记 —— 每 scan 日一行的纵向叙事主干(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-observability-design.md §2

各 ledger 按仪器纵览(channel/gate/zero_buy/watchlist/changelog),本模块按日横切:
regime / 菜单健康 / 漏斗出量 / 买单 / 触发 / 市场 fwd(retro 成熟后自动回填)。
一个文件看完这个月的故事。

  uv run --no-sync python -m autoresearch.learning.journal   # → reports/learning/journal.md
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pandas as pd

_COLS = ["date", "regime", "knife", "healthy", "l2", "finalists", "cards", "buys",
         "triggers", "mkt_fwd1", "mkt_fwd5", "retro_done"]


def _day_row(d: Path) -> dict:
    from autoresearch.scan.health import count_buys, l4_phase_stats  # lazy 防环
    from autoresearch.scan.menu import _healthy, _knife_share
    row: dict = {"date": d.name, "regime": None, "knife": None, "healthy": None,
                 "l2": None, "finalists": None, "cards": None, "buys": None,
                 "triggers": None, "mkt_fwd1": None, "mkt_fwd5": None,
                 "retro_done": (d / "retro" / "done.json").exists()}
    mp = d / "meta.json"
    if mp.exists():
        with contextlib.suppress(Exception):
            row["regime"] = json.loads(mp.read_text(encoding="utf-8")).get("regime")
    p2 = d / "L2_gbdt_top200.csv"
    if p2.exists():
        try:
            l2 = pd.read_csv(p2)
            row["l2"] = len(l2)
            k, h = _knife_share(l2), _healthy(l2)
            row["knife"] = None if k is None else round(k, 3)
            row["healthy"] = h
        except Exception:  # noqa: BLE001
            pass
    pf = d / "finalists.csv"
    if pf.exists():
        with contextlib.suppress(Exception):
            row["finalists"] = len(pd.read_csv(pf))
    ph = l4_phase_stats(d)
    row["cards"] = ph["n_cards"] if ph else None
    if row["finalists"] is not None:
        row["buys"] = count_buys(d)
    ws = d / "watchlist_status.csv"
    if ws.exists():
        try:
            st = pd.read_csv(ws)
            row["triggers"] = int(st["status"].astype(str).str.startswith("触发").sum())
        except Exception:  # noqa: BLE001
            pass
    pa = d / "retro" / "attribution.csv"
    if pa.exists():
        try:
            attr = pd.read_csv(pa)
            for src, dst in [("fwd_1_oo", "mkt_fwd1"), ("fwd_5_oc", "mkt_fwd5")]:
                if src in attr.columns:
                    v = pd.to_numeric(attr[src], errors="coerce").dropna()
                    if len(v):
                        row[dst] = round(float(v.mean()), 6)
        except Exception:  # noqa: BLE001
            pass
    return row


def roll(scan_root: Path | str | None = None) -> pd.DataFrame:
    scan_root = Path(scan_root or "context/scan")
    if not scan_root.exists():
        return pd.DataFrame(columns=_COLS)
    days = sorted(p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20")
    rows = [_day_row(d) for d in days
            if (d / "meta.json").exists() or (d / "finalists.csv").exists()]
    return pd.DataFrame(rows, columns=_COLS)


def render(df: pd.DataFrame) -> list[str]:
    out = ["# 扫描日记(每日一行;retro 成熟后 fwd 自动回填)", ""]
    if df is None or not len(df):
        return out + ["_无 scan 日_"]

    def _p(x, pct=False, frac=False):
        """pct=收益(±x.xx%)、frac=占比(x%)、其余=计数(pandas 混 None 会浮点化,取整显示)。"""
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return "—"
        if pct:
            return f"{x * 100:+.2f}%"
        if frac:
            return f"{x:.0%}"
        return str(int(x)) if isinstance(x, float) else str(x)

    out += ["| 日期 | regime | 落刀 | 健康涨 | L2 | finalists | 卡 | 买 | 触发 | 市场fwd1 | fwd5 | retro |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in df.itertuples(index=False):
        out.append(f"| {r.date} | {r.regime or '—'} | {_p(r.knife, frac=True)} | {_p(r.healthy)} "
                   f"| {_p(r.l2)} | {_p(r.finalists)} | {_p(r.cards)} | {_p(r.buys)} "
                   f"| {_p(r.triggers)} | {_p(r.mkt_fwd1, pct=True)} | {_p(r.mkt_fwd5, pct=True)} "
                   f"| {'✅' if r.retro_done else '…'} |")
    buys = pd.to_numeric(df["buys"], errors="coerce").fillna(0)
    zero = int((buys == 0).sum())
    out += ["", f"- **汇总**:{len(df)} 个 scan 日;0 买日 {zero};"
            f"触发累计 {int(pd.to_numeric(df['triggers'], errors='coerce').fillna(0).sum())}。"
            "落刀/健康涨看菜单质量,fwd 列回答\"那天市场到底给不给钱\"。"]
    return out


def main() -> int:
    df = roll()
    out = Path("reports/learning/journal.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(render(df)) + "\n", encoding="utf-8")
    print(f"[journal] {len(df)} 日 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

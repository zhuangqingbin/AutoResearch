#!/usr/bin/env python3
"""买单 ledger —— 买后管理度量 + 评级基率(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-portfolio-memory-design.md §2

系统推了买单之后从没人记账:T+1/T+5/T+10 走成什么样?目标价现实吗?开盘 gap 吃掉多少
edge?本模块逐买单落账(来源=attribution 已实现 fwd + 卡片目标价 + L1 收盘),并聚出
**评级基率**("本系统 OW 历史 T+5 胜率 X%")——样本 ≥min_n 后注入 skeptic/PM 当先验。

  uv run --no-sync python -m autoresearch.learning.buy_ledger   # → reports/learning/buy_ledger.md
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

_COLS = ["date", "code", "name", "rating", "gap_open", "fwd_1", "fwd_5", "fwd_10",
         "target_ret", "target_hit"]
_TARGET_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _target_ret(scan_dir: Path, code: str) -> float | None:
    """卡片目标价(仪表盘『目标/EV目标』首个数字)÷ 当日收盘 − 1;缺 → None。"""
    p = scan_dir / "details" / f"{code}.md"
    if not p.exists():
        return None
    from autoresearch.scan.assemble import _get, _parse_dashboard
    dash = _parse_dashboard(p.read_text(encoding="utf-8"))
    m = _TARGET_RE.search(_get(dash, "EV目标", "目标") or "")
    if not m:
        return None
    target = float(m.group(1))
    lp = scan_dir / "L1_scored_full.csv"
    if not lp.exists():
        lp = scan_dir / "L1_recall_top1000.csv"
    if not lp.exists():
        return None
    try:
        l1 = pd.read_csv(lp, dtype={"code": str})
        sub = l1[l1["code"].astype(str).str.zfill(6) == code]
        close = pd.to_numeric(sub.iloc[0]["close"], errors="coerce") if len(sub) else None
    except Exception:  # noqa: BLE001
        return None
    if close is None or pd.isna(close) or not close:
        return None
    return round(float(target / close - 1.0), 4)


def roll(scan_root: Path | str | None = None) -> pd.DataFrame:
    """逐 scan 日抽 ≥OW 买单 × attribution 已实现 fwd → ledger 帧。无买单日自然无行。"""
    from autoresearch.scan.health import final_ratings  # lazy 防环
    scan_root = Path(scan_root or "context/scan")
    rows = []
    if not scan_root.exists():
        return pd.DataFrame(columns=_COLS)
    for d in sorted(p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20"):
        ratings = {c: r for c, r in final_ratings(d).items() if r in ("Buy", "Overweight")}
        if not ratings:
            continue
        attr = None
        ap = d / "retro" / "attribution.csv"
        if ap.exists():
            try:
                attr = pd.read_csv(ap, dtype={"code": str})
                attr["code"] = attr["code"].astype(str).str.zfill(6)
                attr = attr.set_index("code")
            except Exception:  # noqa: BLE001
                attr = None
        names = {}
        fp = d / "finalists.csv"
        if fp.exists():
            try:
                fin = pd.read_csv(fp, dtype={"code": str})
                names = dict(zip(fin["code"].astype(str).str.zfill(6),
                                 fin.get("name", ""), strict=False))
            except Exception:  # noqa: BLE001
                pass
        for code, rating in ratings.items():
            def _a(col, code=code, attr=attr):
                if attr is None or code not in attr.index or col not in attr.columns:
                    return None
                v = pd.to_numeric(pd.Series([attr.at[code, col]]), errors="coerce").iloc[0]
                return None if pd.isna(v) else round(float(v), 6)
            tr = _target_ret(d, code)
            f10, f5 = _a("fwd_10_oc"), _a("fwd_5_oc")
            hit = None
            if tr is not None and (f10 is not None or f5 is not None):
                hit = bool((f10 if f10 is not None else f5) >= tr)
            rows.append({"date": d.name, "code": code, "name": names.get(code, ""),
                         "rating": rating, "gap_open": _a("gap_d1"), "fwd_1": _a("fwd_1_oo"),
                         "fwd_5": f5, "fwd_10": f10, "target_ret": tr, "target_hit": hit})
    return pd.DataFrame(rows, columns=_COLS)


def rating_base_rates(ledger: pd.DataFrame, min_n: int = 10) -> list[dict]:
    """按评级聚基率:n / T+5 胜率 / T+5 均值 / 目标命中率;n<min_n 标 thin(先验别急着用)。"""
    if ledger is None or not len(ledger):
        return []
    out = []
    for rating, g in ledger.groupby("rating"):
        f5 = pd.to_numeric(g["fwd_5"], errors="coerce").dropna()
        th = g["target_hit"].dropna()
        out.append({"rating": rating, "n": len(g), "n_realized": len(f5),
                    "win5": round(float((f5 > 0).mean()), 3) if len(f5) else None,
                    "mean5": round(float(f5.mean()), 4) if len(f5) else None,
                    "target_hit": round(float(th.mean()), 3) if len(th) else None,
                    "thin": len(f5) < min_n})
    return sorted(out, key=lambda r: r["rating"])


def render(ledger: pd.DataFrame) -> list[str]:
    out = ["# 买单 ledger(买后 T+1/5/10 + 目标命中 + 开盘 gap;评级基率供 skeptic 先验)", ""]
    if ledger is None or not len(ledger):
        return out + ["_尚无 ≥OW 买单入账(0 买期,机制就绪等首单)_"]

    def f(x, pct=True):
        if x is None or pd.isna(x):
            return "—"
        return f"{x * 100:+.2f}%" if pct else str(x)

    out += ["| 日期 | 股票 | 评级 | gap开盘 | fwd_1 | fwd_5 | fwd_10 | 目标幅 | 命中 |",
            "|---|---|---|---|---|---|---|---|---|"]
    for r in ledger.itertuples(index=False):
        hit = "—" if r.target_hit is None or pd.isna(r.target_hit) else ("✅" if r.target_hit else "✗")
        out.append(f"| {r.date} | {r.name}({r.code}) | {r.rating} | {f(r.gap_open)} "
                   f"| {f(r.fwd_1)} | {f(r.fwd_5)} | {f(r.fwd_10)} | {f(r.target_ret)} | {hit} |")
    br = rating_base_rates(ledger)
    if br:
        out += ["", "## 评级基率(n≥10 才可注入 skeptic/PM 当先验)"]
        for b in br:
            thin = " ⚠样本少" if b["thin"] else ""
            out.append(f"- **{b['rating']}**:n={b['n']}(已实现 {b['n_realized']}),"
                       f"T+5 胜率 {f(b['win5'], False) if b['win5'] is None else format(b['win5'], '.0%')},"
                       f"均值 {f(b['mean5'])},目标命中 "
                       f"{('—' if b['target_hit'] is None else format(b['target_hit'], '.0%'))}{thin}")
    out += ["", "> fwd 列 `—` = 该日 attribution 在 fwd 成熟前写盘(retro 一次性落账)。刷新:对已成熟老日"
            "手动 `retro.attribute('<date>')` 重写 attribution 再重跑本 ledger(拉数走 factor_lab cache,幂等)。"]
    return out


def main() -> int:
    ledger = roll()
    out = Path("reports/learning/buy_ledger.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(render(ledger)) + "\n", encoding="utf-8")
    print(f"[buy_ledger] {len(ledger)} 单 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

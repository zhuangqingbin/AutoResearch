#!/usr/bin/env python3
"""观察单触发→后市度量 —— 触发单本身准不准(确定性,零 LLM;R8)。

聚合各 scan 日 watchlist_status.csv 的**触发**行 × 同日 retro/attribution.csv 的已实现
fwd(触发日起算的后市,口径天然对齐)→ 逐触发 + 汇总。触发后持续为负 = 触发条件太松/
叙事失效,回头修 conds 词表或到期纪律。

  uv run --no-sync python -m autoresearch.learning.watchlist_ledger   # → reports/learning/watchlist_ledger.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_COLS = ["date", "code", "name", "status", "fwd_1", "fwd_2", "fwd_5"]


def roll(scan_root: Path | None = None) -> pd.DataFrame:
    scan_root = Path(scan_root or "context/scan")
    rows = []
    for ws in sorted(scan_root.glob("*/watchlist_status.csv")):
        try:
            st = pd.read_csv(ws, dtype={"code": str})
        except Exception:
            continue
        trig = st[st.get("status", pd.Series(dtype=str)).astype(str).str.startswith("触发")]
        if not len(trig):
            continue
        attr_p = ws.parent / "retro" / "attribution.csv"
        attr = None
        if attr_p.exists():
            try:
                attr = pd.read_csv(attr_p, dtype={"code": str})
                attr["code"] = attr["code"].astype(str).str.zfill(6)
                attr = attr.set_index("code")
            except Exception:
                attr = None
        for _, r in trig.iterrows():
            code = str(r["code"]).zfill(6)
            f1 = f2 = f5 = None
            if attr is not None and code in attr.index:
                f1 = pd.to_numeric(pd.Series([attr.at[code, "fwd_1_oo"]]), errors="coerce").iloc[0]
                if "fwd_2_oc" in attr.columns:
                    f2 = pd.to_numeric(pd.Series([attr.at[code, "fwd_2_oc"]]), errors="coerce").iloc[0]
                if "fwd_5_oc" in attr.columns:
                    f5 = pd.to_numeric(pd.Series([attr.at[code, "fwd_5_oc"]]), errors="coerce").iloc[0]
            rows.append({"date": ws.parent.name, "code": code, "name": r.get("name", ""),
                         "status": r.get("status", ""),
                         "fwd_1": None if pd.isna(f1) else round(float(f1), 6) if f1 is not None else None,
                         "fwd_2": None if pd.isna(f2) else round(float(f2), 6) if f2 is not None else None,
                         "fwd_5": None if pd.isna(f5) else round(float(f5), 6) if f5 is not None else None})
    return pd.DataFrame(rows, columns=_COLS).sort_values(["date", "code"]).reset_index(drop=True)


def render(ledger: pd.DataFrame) -> list[str]:
    out = ["# 观察单触发 ledger(触发日起算后市)", ""]
    if ledger is None or not len(ledger):
        return out + ["_无触发记录(观察单尚未有条目走到触发,或 retro 未归因)_"]

    def f(x):
        return "—" if x is None or pd.isna(x) else f"{x * 100:+.2f}%"

    out += ["| 日期 | 股票 | 状态 | fwd_1 | fwd_2 | fwd_5 |", "|---|---|---|---|---|---|"]
    for r in ledger.itertuples(index=False):
        out.append(f"| {r.date} | {r.name}({r.code}) | {r.status} | {f(r.fwd_1)} | {f(r.fwd_2)} | {f(r.fwd_5)} |")
    f2 = pd.to_numeric(ledger["fwd_2"], errors="coerce").dropna()
    f5 = pd.to_numeric(ledger["fwd_5"], errors="coerce").dropna()
    if len(f2):
        out += ["", f"- **汇总**:{len(ledger)} 次触发;**fwd_2 均值 {f(f2.mean())}(主尺)**、"
                    f"胜率 {(f2 > 0).mean():.0%},fwd_5 均值 {f(f5.mean()) if len(f5) else '—'}(参考)"
                    " —— 持续为负 = 触发条件太松,回修 conds/到期纪律。"]
    elif len(f5):     # fwd_2 全列缺(旧数据)→ 回退只出 fwd_5
        out += ["", f"- **汇总**:{len(ledger)} 次触发;fwd_5 均值 {f(f5.mean())}、胜率 {(f5 > 0).mean():.0%}"
                    " —— 持续为负 = 触发条件太松,回修 conds/到期纪律。"]
    return out


def monitoring_section(scan_root: Path | None = None) -> list[str]:
    """最新一日 watchlist_status 的 born→今 巡检(错过审计;spec 2026-07-05 wave §C3)。

    ledger 主表要等首个触发样本;本节让 ledger 从第一天就有读数——在监控条目此刻涨了多少。
    """
    scan_root = Path(scan_root or "context/scan")
    days = sorted(scan_root.glob("*/watchlist_status.csv"), reverse=True)
    if not days:
        return []
    try:
        st = pd.read_csv(days[0], dtype={"code": str})
    except Exception:  # noqa: BLE001
        return []
    if "since_born" not in st.columns:
        return []
    sub = st.dropna(subset=["since_born"])
    if not len(sub):
        return []
    out = ["", f"## 在监控 born→今(错过审计;{days[0].parent.name})",
           "| 股票 | 状态 | born→今 |", "|---|---|---|"]
    for r in sub.to_dict("records"):
        fire = " 🔥" if bool(r.get("fire")) else ""
        out.append(f"| {r.get('name', '')}({r['code']}) | {r.get('status', '')} "
                   f"| {float(r['since_born']):+.0%}{fire} |")
    return out


def main() -> int:
    ledger = roll()
    out = Path("reports/learning/watchlist_ledger.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(render(ledger) + monitoring_section()) + "\n", encoding="utf-8")
    print(f"[watchlist_ledger] {len(ledger)} 触发 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

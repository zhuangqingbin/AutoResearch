#!/usr/bin/env python3
"""催化取证 ledger —— 催化旗票 vs 无旗票的 fwd_5 对照(确定性,零 LLM)。

spec: 2026-07-05 wave §WS-B3。cat 列是 advisory 事件面;本 ledger 回答"带正催化事件的票
后市是否真的更好"。**fwd_2 主对照(+fwd_5 参考)**——成熟门从 fwd_5 提前到 fwd_2,attr
只有 fwd_2_oc(无 fwd_5_oc)也出行。n(成熟对照)≥30 才可读数;IC 过硬(factor_lab 两半稳+
符号一致)前不入 composite、不设门——与 consensus 同姿势。

  uv run --no-sync python -m autoresearch.learning.catalyst_ledger  # → reports/learning/catalyst_ledger.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_COLS = ["date", "n_flag", "n_unflag", "f2_flag", "f2_unflag", "f5_flag", "f5_unflag"]
_POS = ["rep_impl", "rep_plan", "holder_in", "surv_n"]     # 正催化(减持不算)


def _day(d: Path) -> dict | None:
    cp, ap = d / "L3_catalyst.csv", d / "retro" / "attribution.csv"
    if not cp.exists() or not ap.exists():
        return None
    try:
        cat = pd.read_csv(cp, dtype={"code": str})
        attr = pd.read_csv(ap, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return None
    if (("fwd_2_oc" not in attr.columns and "fwd_5_oc" not in attr.columns)
            or "code" not in attr.columns or "code" not in cat.columns):
        return None
    cat["code"] = cat["code"].astype(str).str.zfill(6)
    attr["code"] = attr["code"].astype(str).str.zfill(6)
    pos_cols = [c for c in _POS if c in cat.columns]
    cat["_flag"] = cat[pos_cols].fillna(0).sum(axis=1) > 0 if pos_cols else False
    fwd_cols = [c for c in ("fwd_2_oc", "fwd_5_oc") if c in attr.columns]
    m = cat.merge(attr[["code", *fwd_cols]], on="code", how="inner")
    primary = "fwd_2_oc" if "fwd_2_oc" in fwd_cols else "fwd_5_oc"   # 成熟门:fwd_2 优先,无则退回 fwd_5
    for c in fwd_cols:
        m[c] = pd.to_numeric(m[c], errors="coerce")
    m = m.dropna(subset=[primary])
    if not len(m):
        return None
    fl, un = m[m["_flag"]], m[~m["_flag"]]
    row = {"date": d.name, "n_flag": int(len(fl)), "n_unflag": int(len(un))}
    row["f2_flag"] = round(float(fl["fwd_2_oc"].mean()), 6) if "fwd_2_oc" in fwd_cols and len(fl) else None
    row["f2_unflag"] = round(float(un["fwd_2_oc"].mean()), 6) if "fwd_2_oc" in fwd_cols and len(un) else None
    row["f5_flag"] = round(float(fl["fwd_5_oc"].mean()), 6) if "fwd_5_oc" in fwd_cols and len(fl) else None
    row["f5_unflag"] = round(float(un["fwd_5_oc"].mean()), 6) if "fwd_5_oc" in fwd_cols and len(un) else None
    return row


def roll(scan_root: Path | str | None = None) -> pd.DataFrame:
    scan_root = Path(scan_root or "context/scan")
    if not scan_root.exists():
        return pd.DataFrame(columns=_COLS)
    rows = [r for d in sorted(p for p in scan_root.iterdir()
                              if p.is_dir() and p.name[:2] == "20")
            if (r := _day(d)) is not None]
    return pd.DataFrame(rows, columns=_COLS)


def render(df: pd.DataFrame, min_n: int = 30) -> list[str]:
    out = ["# 催化取证 ledger(催化旗票 vs 无旗票 fwd_2 主对照 + fwd_5 参考;advisory 事件面的前向对照)", ""]
    if df is None or not len(df):
        return out + ["_无现场(L3_catalyst.csv 或成熟 attribution 缺)_"]

    def f(x):
        return "—" if x is None or pd.isna(x) else f"{x * 100:+.2f}%"

    out += ["| 日期 | 旗票n | 无旗n | 旗fwd_2 | 无旗fwd_2 | 旗fwd_5(参考) | 无旗fwd_5(参考) |",
            "|---|---|---|---|---|---|---|"]
    for r in df.itertuples(index=False):
        out.append(f"| {r.date} | {r.n_flag} | {r.n_unflag} | {f(r.f2_flag)} | {f(r.f2_unflag)} | "
                   f"{f(r.f5_flag)} | {f(r.f5_unflag)} |")
    n = int(df["n_flag"].sum())
    if n < min_n:
        out += ["", f"- ⚠ **取证中**(旗票累计 n={n} < {min_n}):只记账不下结论;"
                    "IC 过硬前不入 composite、不设门。"]
    else:
        fl = pd.to_numeric(df["f2_flag"], errors="coerce").dropna()
        un = pd.to_numeric(df["f2_unflag"], errors="coerce").dropna()
        out += ["", f"- **汇总**(旗票 n={n}):旗 fwd_2 日均 {f(fl.mean())} vs 无旗 {f(un.mean())}(主尺);"
                    "持续为正差 → 提 proposal(factor_lab IC 门验后再谈入线)。"]
    return out


def main() -> int:
    df = roll()
    out = Path("reports/learning/catalyst_ledger.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(render(df)) + "\n", encoding="utf-8")
    print(f"[catalyst_ledger] {len(df)} 日 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

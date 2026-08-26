"""Spike(只读、抛弃型):在决策尺 gap_c1_o2 上,
A. composite top-K(K=5/10/20/50,全 L0 截面 & L2 菜单内)相对截面中位的超额;
B. T+1 日内强度(收/开−1、收盘在当日区间位置)对 gap_c1_o2 的条件读数——
   分别在「全市场可交易」「L2 菜单」「composite top-20」「L3 finalist」四个人口上;
C. 两者合成:composite top-20 ∧ T+1 日内强(r1>0)。
口径与 edge_census 对齐:市场基准 = 当日全湖可交易截面中位;配对 t = 逐日超额 one-sample t。
"""
from __future__ import annotations
import sys, json
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "/Users/qingbin.zhuang/Personal/TradingAgents")
from autoresearch.common import workspace as ws
from autoresearch.common import ruler as _ruler
from autoresearch.research import edge_census as ec

MAIN = "gap_c1_o2"
root = ws.scan_root()
days = ec.scan_days(root)
P = ec.lake_trade_days()
need = sorted({d.replace("-", "") for d in days})
lo = min(need); hi_i = min(len(P) - 1, P.index(max(d for d in need if d in P)) + 4) if any(d in P for d in need) else len(P) - 1
dates = [p for p in P if lo <= p <= P[hi_i]]
piv = ec.load_lake_pivots(dates)

def pos_in_range(D1):
    h, l, c = piv["high"][D1], piv["low"][D1], piv["close"][D1]
    return ((c - l) / (h - l)).where((h - l) > 0)

rows = []          # per-day per-family stats
for day in days:
    D = day.replace("-", "")
    if D not in P or P.index(D) + 2 >= len(P):
        continue
    D1 = P[P.index(D) + 1]
    fr = ec.forward_frame(piv, P, D)
    if fr is None:
        continue
    ok = _ruler.entry_tradable(fr, ruler_name=MAIN)
    fwd = pd.to_numeric(fr[MAIN], errors="coerce")
    base = fwd[ok & fwd.notna()]
    if len(base) < 500:
        continue
    med = base.median()
    sd = Path(root) / day
    l1 = ec._read_csv(sd / "L1_scored_full.csv")
    l2 = ec._read_csv(sd / "L2_gbdt_top200.csv")
    fin = ec._read_csv(sd / "finalists.csv")
    pin = ec.pinned_codes(sd)
    fams: dict[str, set[str]] = {"市场·全体": set(base.index)}
    if l1 is not None and "composite" in l1.columns:
        l1 = l1.assign(code=l1["code"].astype(str).str.zfill(6))
        l1s = l1.sort_values("composite", ascending=False)
        for K in (5, 10, 20, 50):
            fams[f"L0·composite·top{K}"] = set(l1s["code"].head(K))
        if "pct_1d" in l1.columns:
            nochase = l1s[pd.to_numeric(l1s["pct_1d"], errors="coerce") < 9.5]
            fams["L0·composite·top20·非当日大涨"] = set(nochase["code"].head(20))
    if l2 is not None and "gbdt_score" in l2.columns:
        l2 = l2.assign(code=l2["code"].astype(str).str.zfill(6))
        l2s = l2.sort_values("gbdt_score", ascending=False)
        fams["L2·全体"] = set(l2s["code"])
        for K in (5, 10, 20):
            fams[f"L2·composite·top{K}"] = set(l2s["code"].head(K))
    if fin is not None:
        fams["L3·finalist(非📌)"] = ec._codes(fin) - pin
    # T+1 日内强度
    o1, c1 = piv["open"].get(D1), piv["close"].get(D1)
    if o1 is None or c1 is None:
        continue
    r1 = (c1 / o1 - 1.0)
    pir = pos_in_range(D1)
    mkt_r1 = r1.reindex(base.index).median()
    for fam, codes in fams.items():
        idx = [c for c in codes if c in base.index]
        if not idx:
            continue
        g = base.reindex(idx)
        rr = r1.reindex(idx); pp = pir.reindex(idx)
        def stat(mask, tag):
            s = g[mask.reindex(idx).fillna(False).values] if mask is not None else g
            s = s.dropna()
            if len(s) == 0:
                return
            rows.append({"day": day, "family": fam, "cond": tag, "n": len(s),
                         "excess": float(s.mean() - med), "hit": float((s > 0).mean())})
        stat(None, "无条件")
        stat(rr > 0, "T+1 收>开")
        stat(rr <= 0, "T+1 收≤开")
        stat(rr > mkt_r1, "T+1 日内跑赢市场中位")
        stat(pp >= 0.7, "T+1 收在区间上 30%")
        stat(pp <= 0.3, "T+1 收在区间下 30%")
        stat((rr > 0) & (pp >= 0.7), "T+1 收>开 ∧ 区间上30%")

df = pd.DataFrame(rows)
out = []
for (fam, cond), g in df.groupby(["family", "cond"], sort=False):
    ex = g["excess"].values
    t = ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex))) if len(ex) > 1 and ex.std(ddof=1) > 1e-12 else np.nan
    out.append({"family": fam, "cond": cond, "n_days": len(g), "n_med": float(g["n"].median()),
                "excess_pp": ex.mean() * 100, "t": t, "hit": g["hit"].mean()})
res = pd.DataFrame(out)
order = ["无条件", "T+1 收>开", "T+1 收≤开", "T+1 日内跑赢市场中位", "T+1 收在区间上 30%", "T+1 收在区间下 30%", "T+1 收>开 ∧ 区间上30%"]
res["cond"] = pd.Categorical(res["cond"], order)
res = res.sort_values(["family", "cond"])
pd.set_option("display.width", 200)
print(res.to_string(index=False, float_format=lambda x: f"{x:+.2f}" if abs(x) < 100 else f"{x:.0f}"))
sp = Path("/private/tmp/claude-503/-Users-qingbin-zhuang-Personal-TradingAgents/d24e438c-ef7a-4c62-8c70-39a2e9d93051/scratchpad")
res.to_csv(sp / "spike_buy_owner.csv", index=False)
print("\ndays used:", df["day"].nunique(), "first", df["day"].min(), "last", df["day"].max())

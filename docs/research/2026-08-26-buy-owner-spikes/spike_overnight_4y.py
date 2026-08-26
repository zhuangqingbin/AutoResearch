"""Spike 2(只读、抛弃型):4 年全湖验证「日内弱收盘 → 隔夜 gap 为正」是否稳定。
对每个交易日 D(2022-03 → 2026-08):gap = open[D+1]/close[D] − 1(= 决策尺 gap_c1_o2 的同一段,
D 扮演 T+1);条件 = D 日收盘在当日区间位置 pos=(c−l)/(h−l) 与 c/o−1。
人口 = 当日有成交、非一字/涨跌停封板收盘(|pct_chg|<9.5)、成交额 ≥ 5000 万(流动性地板)。
读数 = 条件子集均值 − 当日截面中位(pp),逐日配对 t;按年分层。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "/Users/qingbin.zhuang/Personal/TradingAgents")
from autoresearch.common import workspace as ws

d = ws.lake_root() / "daily"
days = sorted(p.stem[:8] for p in d.glob("*.parquet") if p.stem[:8].isdigit())

def load(day):
    df = pd.read_parquet(d / f"{day}.parquet", columns=["ts_code", "open", "high", "low", "close", "pct_chg", "amount"])
    df = df.assign(code=df["ts_code"].astype(str).str[:6]).set_index("code")
    for c in ("open", "high", "low", "close", "pct_chg", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

rows = []
prev = None
for i in range(len(days) - 1):
    D, D1 = days[i], days[i + 1]
    cur = prev if prev is not None else load(D)
    nxt = load(D1)
    prev = nxt
    j = cur.join(nxt[["open"]].rename(columns={"open": "open_next"}), how="inner")
    j = j[(j["amount"] >= 50_000) & (j["pct_chg"].abs() < 9.5) & (j["high"] > j["low"]) & (j["open_next"] > 0)]
    if len(j) < 1000:
        continue
    gap = j["open_next"] / j["close"] - 1.0
    gap = gap[(gap.abs() < 0.31)]
    j = j.loc[gap.index]
    pos = (j["close"] - j["low"]) / (j["high"] - j["low"])
    r = j["close"] / j["open"] - 1.0
    med = gap.median()
    def ex(mask, tag):
        s = gap[mask]
        if len(s) >= 20:
            rows.append({"day": D, "cond": tag, "n": len(s), "excess": s.mean() - med, "hit": (s > 0).mean()})
    ex(pd.Series(True, index=gap.index), "全体")
    ex(pos >= 0.7, "收在区间上30%")
    ex(pos <= 0.3, "收在区间下30%")
    ex(pos <= 0.15, "收在区间下15%")
    ex(r > 0, "收>开")
    ex(r <= 0, "收≤开")
    ex((r <= -0.02), "收≤开−2%")
    ex((pos <= 0.3) & (j["pct_chg"] > 0), "收在区间下30% ∧ 当日仍收涨")
    ex((pos <= 0.3) & (j["pct_chg"] <= 0), "收在区间下30% ∧ 当日收跌")
    ex((pos >= 0.7) & (j["pct_chg"] > 3), "收在区间上30% ∧ 当日涨>3%")

df = pd.DataFrame(rows)
df["year"] = df["day"].str[:4]
def agg(g):
    ex_ = g["excess"].values
    t = ex_.mean() / (ex_.std(ddof=1) / np.sqrt(len(ex_))) if len(ex_) > 1 else np.nan
    return pd.Series({"n_days": len(g), "n_med": g["n"].median(), "excess_pp": ex_.mean() * 100, "t": t, "hit": g["hit"].mean()})
pd.set_option("display.width", 200)
print("=== 全期 ===")
print(df.groupby("cond", sort=False).apply(agg).to_string(float_format=lambda x: f"{x:+.2f}"))
print("\n=== 按年 ===")
print(df.groupby(["cond", "year"], sort=False).apply(agg).to_string(float_format=lambda x: f"{x:+.2f}"))
sp = Path("/private/tmp/claude-503/-Users-qingbin-zhuang-Personal-TradingAgents/d24e438c-ef7a-4c62-8c70-39a2e9d93051/scratchpad")
df.to_csv(sp / "spike_overnight_4y_daily.csv", index=False)

# ruff: noqa  -- 抛弃型 spike 脚本(2026-08-26 结果账本首读),只求可复跑,不按生产代码标准整理
"""Step 1: ledger + lake → merged frame with market-relative fwd_5/fwd_10 (READ-ONLY on repo/lake)."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]  # 仓库根(脚本位于 docs/research/<dir>/)
OUT = Path(__file__).resolve().parent / "_out"  # 中间产物落本目录 _out/(已 gitignore 与否请自查;merged.csv ≈600KB)
sys.path.insert(0, str(REPO))
import os
os.chdir(REPO)  # workspace roots are relative Paths
from autoresearch.research import edge_census as ec
from autoresearch.research import factor_lab as fl
from autoresearch.common import ruler as _ruler

L = pd.read_csv(REPO / "reports_claude/scan/_ledger/recommendations.csv", dtype={"code": str})
L["code"] = L["code"].str.zfill(6)
dates = sorted(L["analysis_date"].unique())
P = ec.lake_trade_days()
D0 = dates[0].replace("-", "")
i0 = P.index(D0)
window = P[i0 - 1: len(P)]          # 2026-06-17 .. last lake day
print("lake window", window[0], window[-1], len(window), "files")
piv = ec.load_lake_pivots(window)
print("pivot codes", len(piv["close"]))

rows = []
frames = {}
for d in dates:
    D = d.replace("-", "")
    fr = ec.forward_frame(piv, P, D)
    idx = P.index(D)
    # aligned-to-T+1-close horizons (for Q4): close[D+k]/close[D+1]-1
    def col(p, k):
        j = idx + k
        return p[P[j]] if (0 <= j < len(P) and P[j] in p.columns) else pd.Series(np.nan, index=p.index)
    c1 = col(piv["close"], 1)
    fr["fwd_5_c1c"] = col(piv["close"], 5) / c1 - 1.0
    fr["fwd_10_c1c"] = col(piv["close"], 10) / c1 - 1.0
    frames[d] = fr
    rec = {"analysis_date": d}
    for r, flag_ruler in (("gap_c1_o2", "gap_c1_o2"), ("fwd_5_oc", "fwd_5_oc"), ("fwd_10_oc", "fwd_10_oc"),
                          ("fwd_5_c1c", "gap_c1_o2"), ("fwd_10_c1c", "gap_c1_o2")):
        ok = _ruler.entry_tradable(fr, ruler_name=flag_ruler)
        v = pd.to_numeric(fr[r], errors="coerce")
        base = v[ok & v.notna()]
        rec[f"med_{r}"] = base.median() if len(base) >= ec.MIN_CROSS_SECTION else np.nan
        rec[f"mean_{r}"] = base.mean() if len(base) >= ec.MIN_CROSS_SECTION else np.nan
        rec[f"n_{r}"] = len(base)
    rows.append(rec)
B = pd.DataFrame(rows)
B.to_csv(OUT / "market_baseline.csv", index=False)
print(B.to_string())

# merge per (date, code): recomputed fwd + buyable flag (D+1 open leg) + aligned horizons
parts = []
for d, fr in frames.items():
    sub = L[L.analysis_date == d]
    f = fr.reindex(sub["code"].values)
    parts.append(pd.DataFrame({
        "analysis_date": d, "code": sub["code"].values,
        "buyable_o1": _ruler.entry_tradable(f, ruler_name="fwd_5_oc").values,
        "buyable_c1_re": _ruler.entry_tradable(f, ruler_name="gap_c1_o2").values,
        "gap_re": f["gap_c1_o2"].values, "fwd_5_re": f["fwd_5_oc"].values, "fwd_10_re": f["fwd_10_oc"].values,
        "fwd_5_c1c": f["fwd_5_c1c"].values, "fwd_10_c1c": f["fwd_10_c1c"].values,
    }).drop_duplicates(["analysis_date", "code"]))
R = pd.concat(parts, ignore_index=True)
M = L.merge(R, on=["analysis_date", "code"], how="left").merge(B, on="analysis_date", how="left")
M["fwd_5_rel"] = M["fwd_5_oc"] - M["med_fwd_5_oc"]
M["fwd_10_rel"] = M["fwd_10_oc"] - M["med_fwd_10_oc"]
M["fwd_5_relmean"] = M["fwd_5_oc"] - M["mean_fwd_5_oc"]
M["fwd_10_relmean"] = M["fwd_10_oc"] - M["mean_fwd_10_oc"]
M["fwd_5_c1c_rel"] = M["fwd_5_c1c"] - M["med_fwd_5_c1c"]
M["fwd_10_c1c_rel"] = M["fwd_10_c1c"] - M["med_fwd_10_c1c"]
M["gap_rel_re"] = M["gap_c1_o2"] - M["med_gap_c1_o2"]

# sanity: ledger excess_med_market vs recomputed; ledger fwd vs recomputed
chk = M.dropna(subset=["excess_med_market", "gap_rel_re"])
print("gap excess: max abs diff ledger vs recomputed:", (chk["excess_med_market"] - chk["gap_rel_re"]).abs().max(), "n", len(chk))
c5 = M.dropna(subset=["fwd_5_oc", "fwd_5_re"]); print("fwd_5 max abs diff:", (c5["fwd_5_oc"] - c5["fwd_5_re"]).abs().max(), "n", len(c5))
c10 = M.dropna(subset=["fwd_10_oc", "fwd_10_re"]); print("fwd_10 max abs diff:", (c10["fwd_10_oc"] - c10["fwd_10_re"]).abs().max(), "n", len(c10))
print("buyable_c1 ledger vs recomputed mismatches:", int((M["buyable_c1"].astype(str).str.lower().eq("true") != M["buyable_c1_re"]).sum()))
print("rows fwd_5 in ledger but not recomputed:", int((M.fwd_5_oc.notna() & M.fwd_5_re.isna()).sum()),
      "| recomputed but not ledger:", int((M.fwd_5_oc.isna() & M.fwd_5_re.notna()).sum()))
print("rows fwd_10 in ledger but not recomputed:", int((M.fwd_10_oc.notna() & M.fwd_10_re.isna()).sum()),
      "| recomputed but not ledger:", int((M.fwd_10_oc.isna() & M.fwd_10_re.notna()).sum()))
M.to_csv(OUT / "merged.csv", index=False)
print("merged", M.shape)

# ruff: noqa  -- 抛弃型 spike 脚本(2026-08-26 结果账本首读),只求可复跑,不按生产代码标准整理
"""Step 2: answer Q1–Q7 from merged.csv; write readout.md. READ-ONLY on repo/lake."""
import os, sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats as ss

OUT = Path(__file__).resolve().parent / "_out"  # 中间产物落本目录 _out/(已 gitignore 与否请自查;merged.csv ≈600KB)
REPO = Path(__file__).resolve().parents[3]  # 仓库根(脚本位于 docs/research/<dir>/)
M0 = pd.read_csv(OUT / "merged.csv", dtype={"code": str})
M0["code"] = M0["code"].str.zfill(6)
M0["buyable_c1_b"] = M0["buyable_c1"].astype(str).str.lower().eq("true")
M0["buyable_o1_b"] = M0["buyable_o1"].astype(str).str.lower().eq("true")
M0["month"] = M0["analysis_date"].str[:7]

def period(d):
    if d <= "2026-07-02": return "P1 ≤07-02 reversal"
    if d <= "2026-08-18": return "P2 07-03..08-18"
    return "P3 ≥08-19 E6-active"
M0["period"] = M0["analysis_date"].map(period)

# dedupe: LAST run_id per analysis_date
last_run = M0.groupby("analysis_date")["run_id"].max()
M = M0[M0["run_id"] == M0["analysis_date"].map(last_run)].copy()
first_run = M0.groupby("analysis_date")["run_id"].min()
MF = M0[M0["run_id"] == M0["analysis_date"].map(first_run)].copy()

# role groups
def role_group(r):
    if r["role"] == "BUY":
        return "BUY-active" if r["mode"] == "active" else "BUY-shadow(excluded)"
    return r["role"]
M["rg"] = M.apply(role_group, axis=1)
MF["rg"] = MF.apply(role_group, axis=1)

MEAS = {  # column -> (label, tradable filter col, NW lag in date-steps)
    "excess_med_market": ("gap_c1_o2 − mkt median (pp)", "buyable_c1_b", 0),
    "fwd_5_rel": ("fwd_5_oc − mkt median (pp)", "buyable_o1_b", 4),
    "fwd_10_rel": ("fwd_10_oc − mkt median (pp)", "buyable_o1_b", 9),
}

def nw_t(x, lag):
    x = np.asarray(x, float); n = len(x)
    if n < 3: return np.nan
    e = x - x.mean(); S = (e * e).sum() / n
    for k in range(1, min(lag, n - 1) + 1):
        S += 2 * (1 - k / (lag + 1)) * (e[k:] * e[:-k]).sum() / n
    return x.mean() / np.sqrt(S / n) if S > 0 else np.nan

def block(df, col, filt=None, lag=0):
    d = df.dropna(subset=[col])
    if filt: d = d[d[filt]]
    n = len(d)
    if n == 0: return dict(n=0, days=0, mean=np.nan, t_pool=np.nan, day_mean=np.nan, t_day=np.nan, t_nw=np.nan, hit=np.nan, days_neg=np.nan)
    pdm = d.groupby("analysis_date")[col].mean().sort_index()
    nd = len(pdm)
    t_pool = d[col].mean() / (d[col].std(ddof=1) / np.sqrt(n)) if n > 1 and d[col].std(ddof=1) > 0 else np.nan
    t_day = pdm.mean() / (pdm.std(ddof=1) / np.sqrt(nd)) if nd > 1 and pdm.std(ddof=1) > 0 else np.nan
    return dict(n=n, days=nd, mean=d[col].mean() * 100, t_pool=t_pool, day_mean=pdm.mean() * 100, t_day=t_day,
                t_nw=nw_t(pdm.values, lag) if lag else np.nan, hit=(d[col] > 0).mean(), days_neg=(pdm < 0).mean())

def fmt(v, nd=2):
    return "—" if (v is None or (isinstance(v, float) and np.isnan(v))) else (f"{v:+.{nd}f}" if isinstance(v, float) else str(v))

def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows: out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)

def table_by(df, groupcol, groups=None, measures=MEAS, min_n=1):
    rows = []
    groups = groups or list(pd.unique(df[groupcol].dropna()))
    for g in groups:
        sub = df[df[groupcol] == g]
        for col, (lab, filt, lag) in measures.items():
            b = block(sub, col, filt, lag)
            if b["n"] < min_n: continue
            rows.append([g, lab, b["n"], b["days"], fmt(b["mean"]), fmt(b["t_pool"]), fmt(b["day_mean"]), fmt(b["t_day"]),
                         fmt(b["t_nw"]) if lag else "n/a", f"{b['hit']:.2f}", f"{b['days_neg']:.2f}"])
    return md_table(["group", "measure", "n_rows", "n_days", "pooled mean", "t_pool", "day-mean", "t_day", "t_NW", "hit>0", "share days<0"], rows)

R = []  # readout lines
def H(s): R.append("\n## " + s + "\n")
def P(s): R.append(s)

P("# Ledger spike readout — recommendations.csv × lake market baseline (2026-08-26, read-only)\n")
P("**Definitions used (verified in `autoresearch/research/factor_lab.py::forward_returns`, `autoresearch/scan/outcome.py`, `autoresearch/research/edge_census.py`):**")
P("- `gap_c1_o2` = open[D+2]/close[D+1] − 1 (T+1 close buy → T+2 open sell; MAIN ruler). `excess_med_market` (ledger col) = gap − cross-sectional **median** of gap over all lake stocks with `buyable_c1` (T+1 close not sealed limit-up). Group stat restricted to `buyable_c1==True` (edge_census `daily_stats` convention).")
P("- `fwd_5_oc` = close[D+5]/open[D+1] − 1; `fwd_10_oc` = close[D+10]/open[D+1] − 1, D+k = k-th **lake** trading day after analysis_date D (entry = D+1 open, **not** T+1 close). Ledger values are RAW. I recomputed them from `lake/daily` (max |diff| vs ledger = 5e-7 → identical) and built the baseline: per-date cross-sectional **median** over all lake stocks with `buyable` (D+1 not one-word limit-up: open==close==high ∧ pct≥board×0.98), n≈5,490–5,530/day. `fwd_5_rel`/`fwd_10_rel` = raw − that median; group stats restricted to rows with `buyable==True`.")
P("- For Q4 an additionally **aligned** pair `fwd_5_c1c`/`fwd_10_c1c` = close[D+5]/close[D+1] − 1 and close[D+10]/close[D+1] − 1 (entry at the T+1 close that `t1_pos_in_range` describes), relative to lake median over `buyable_c1`.")
P("- Stats: `pooled mean`/`t_pool` over rows; `day-mean`/`t_day` = mean and one-sample t of per-date group means (edge_census `aggregate` convention; primary); `t_NW` = Newey-West (Bartlett, lag 4 for fwd_5, lag 9 for fwd_10, in date-steps) on the per-date series — the honest t for overlapping windows. `hit>0` share of rows >0; `share days<0` share of per-date means <0.")
P("- Dedupe: **LAST run_id per analysis_date** (9 dates had reruns). Sensitivity with FIRST run in Q7.")
P(f"- Lake last day = 20260824 → `fwd_5` exists for analysis_date ≤ 2026-08-17 (37 dates), `fwd_10` for ≤ 2026-08-10 (33 dates). **P3 (E6 active, 08-19/08-20) has NO fwd_5/fwd_10 yet**, only gap.")
P(f"- Rows: {len(M0)} total → {len(M)} after dedupe. mode=shadow BUY rows (08-13, 08-17×2, 08-18) are **excluded** from every BUY stat (shown once, separately, for the record).")
P("- Population break: ≤07-09 each run logged 30 'finalist' rows (pre-L3.5-merge era: finalist = L3 top-30, no L4 rating), from 07-10 on 6–10 finalists + 2–4 pinned per run with L4 ratings. 'finalist' in P1/early-P2 is therefore a wider, shallower population than in late P2.")

# ───────────── Q1
H("Q1 — by role × period (dedupe LAST run; BUY = mode=active only)")
periods = ["P1 ≤07-02 reversal", "P2 07-03..08-18", "P3 ≥08-19 E6-active"]
rows = []
for rg in ["finalist", "pinned", "BUY-active"]:
    for per in periods:
        sub = M[(M.rg == rg) & (M.period == per)]
        for col, (lab, filt, lag) in MEAS.items():
            b = block(sub, col, filt, lag)
            if b["n"] == 0: continue
            rows.append([rg, per, lab, b["n"], b["days"], fmt(b["mean"]), fmt(b["t_pool"]), fmt(b["day_mean"]), fmt(b["t_day"]),
                         fmt(b["t_nw"]) if lag else "n/a", f"{b['hit']:.2f}", f"{b['days_neg']:.2f}"])
    sub = M[M.rg == rg]
    for col, (lab, filt, lag) in MEAS.items():
        b = block(sub, col, filt, lag)
        if b["n"] == 0: continue
        rows.append([rg, "ALL", lab, b["n"], b["days"], fmt(b["mean"]), fmt(b["t_pool"]), fmt(b["day_mean"]), fmt(b["t_day"]),
                     fmt(b["t_nw"]) if lag else "n/a", f"{b['hit']:.2f}", f"{b['days_neg']:.2f}"])
P(md_table(["role", "period", "measure", "n_rows", "n_days", "pooled mean", "t_pool", "day-mean", "t_day", "t_NW", "hit>0", "share days<0"], rows))
# shadow BUY for the record
sh = M0[(M0.role == "BUY") & (M0["mode"] == "shadow")]
P("\nShadow-BUY rows (excluded above, for the record): " + "; ".join(
    f"{r.analysis_date} {r.code} {r['name']} lane={r.lane} rating={r.rating} gap_rel={r.excess_med_market*100:+.2f}pp fwd5_rel={fmt(r.fwd_5_rel*100 if pd.notna(r.fwd_5_rel) else np.nan)}pp" for _, r in sh.iterrows()))
ab = M[(M.rg == "BUY-active")]
P("\nActive-BUY rows (n=2): " + "; ".join(
    f"{r.analysis_date} {r.code} {r['name']} lane={r.lane} rating={r.rating} gap={r.gap_c1_o2*100:+.2f}pp gap_rel={r.excess_med_market*100:+.2f}pp fwd5/10=not mature" for _, r in ab.iterrows()))

# sub-split P2 into 07-03..07-09 (30-finalist era) vs 07-10..08-18 (rated era)
H("Q1b — finalist, P2 split at the 07-10 population break (30/run unrated → 6–10/run rated)")
M["p2split"] = np.where(M.analysis_date <= "2026-07-09", "≤07-09 (30-finalist era)", "07-10..08-18 (rated era)")
sub = M[(M.rg == "finalist") & (M.period == "P2 07-03..08-18")]
P(table_by(sub, "p2split", ["≤07-09 (30-finalist era)", "07-10..08-18 (rated era)"]))

H("Q1c — sign stability by month (finalist)")
sub = M[M.rg == "finalist"]
P(table_by(sub, "month", ["2026-06", "2026-07", "2026-08"]))
# per-date finalists list for fwd_10_rel
pdm = sub[sub.buyable_o1_b].groupby("analysis_date")[["fwd_5_rel", "fwd_10_rel"]].mean() * 100
pdg = sub[sub.buyable_c1_b].groupby("analysis_date")["excess_med_market"].mean() * 100
pdd = pd.concat([pdg.rename("gap_rel_pp"), pdm], axis=1).round(2)
pdd["n_final"] = sub.groupby("analysis_date").size()
P("\nPer-date finalist means (pp):\n")
P(md_table(["date", "gap_rel", "fwd5_rel", "fwd10_rel", "n"], [[d, fmt(r.gap_rel_pp), fmt(r.fwd_5_rel), fmt(r.fwd_10_rel), int(r.n_final)] for d, r in pdd.iterrows()]))

# ───────────── Q2
H("Q2 — finalists by lane and by early_stop_reason (dedupe LAST; role=finalist only)")
sub = M[M.rg == "finalist"]
lanes = sub.lane.value_counts().index.tolist()
P("### by lane\n")
P(table_by(sub, "lane", lanes, min_n=5))
P("\n### by early_stop_reason (rows with a reason = L4 early-stopped the DD; 'none' = full card)\n")
sub2 = sub.copy(); sub2["esr"] = sub2["early_stop_reason"].fillna("none")
P(table_by(sub2, "esr", sub2.esr.value_counts().index.tolist(), min_n=3))
P("\nNote: early_stop_reason only exists from 07-10 (rated era). 'none' therefore mixes the 30-finalist unrated era with full-card rows; below the same table restricted to the rated era (≥07-10):\n")
P(table_by(sub2[sub2.analysis_date >= "2026-07-10"], "esr", sub2.esr.value_counts().index.tolist(), min_n=3))

# ───────────── Q3
H("Q3 — L4 rating face vs outcome (all rated rows: finalist+pinned+active BUY; shadow BUY excluded)")
RO = {"Overweight": 4, "Hold": 3, "Underweight": 2, "Sell": 1}
rated = M[M.rating.isin(RO.keys()) & (M.rg != "BUY-shadow(excluded)")].copy()
rated["rord"] = rated.rating.map(RO)
P(table_by(rated, "rating", ["Overweight", "Hold", "Underweight", "Sell"]))
P("\nFinalist-only rated rows:\n")
P(table_by(rated[rated.rg == "finalist"], "rating", ["Overweight", "Hold", "Underweight", "Sell"]))
def spearman_block(df, xcol, ycol, filt, min_day=5):
    d = df.dropna(subset=[xcol, ycol]); d = d[d[filt]]
    if len(d) < 3 or d[xcol].nunique() < 2: return dict(n=len(d), rho=np.nan, p=np.nan, days=0, ic_mean=np.nan, ic_t=np.nan)
    rho, p = ss.spearmanr(d[xcol], d[ycol])
    ics = []
    for dt, g in d.groupby("analysis_date"):
        if len(g) >= min_day and g[xcol].nunique() >= 2:
            ics.append(ss.spearmanr(g[xcol], g[ycol])[0])
    ics = np.array([i for i in ics if pd.notna(i)])
    ic_t = ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics))) if len(ics) > 2 and ics.std(ddof=1) > 0 else np.nan
    return dict(n=len(d), rho=rho, p=p, days=len(ics), ic_mean=ics.mean() if len(ics) else np.nan, ic_t=ic_t)
rows = []
for scope, df in (("all rated", rated), ("finalist rated", rated[rated.rg == "finalist"])):
    for col, (lab, filt, lag) in MEAS.items():
        b = spearman_block(df, "rord", col, filt)
        rows.append([scope, lab, b["n"], fmt(b["rho"], 3), fmt(b["p"], 3), b["days"], fmt(b["ic_mean"], 3), fmt(b["ic_t"])])
P("\nSpearman(rating ordinal Sell=1..OW=4, outcome): pooled rho/p, and per-date rank-IC (dates with ≥5 rated rows) mean & t:\n")
P(md_table(["scope", "outcome", "n", "pooled rho", "p", "n_days IC", "mean IC", "t(IC)"], rows))

# ───────────── Q4
H("Q4 — T+1 strong close (t1_pos_in_range ≥0.7) vs weak (<0.3)")
def posgrp(v):
    if pd.isna(v): return np.nan
    return "≥0.7 strong" if v >= 0.7 else ("<0.3 weak" if v < 0.3 else "mid")
M["posg"] = M.t1_pos_in_range.map(posgrp)
MEAS4 = dict(MEAS)
MEAS4["fwd_5_c1c_rel"] = ("ALIGNED c1→c5 − mkt median (pp)", "buyable_c1_b", 4)
MEAS4["fwd_10_c1c_rel"] = ("ALIGNED c1→c10 − mkt median (pp)", "buyable_c1_b", 9)
for scope, df in (("all roles (finalist+pinned+BUY-active)", M[M.rg != "BUY-shadow(excluded)"]), ("finalist only", M[M.rg == "finalist"])):
    P(f"\n### {scope}\n")
    P(table_by(df, "posg", ["≥0.7 strong", "mid", "<0.3 weak"], measures=MEAS4))
    # difference strong − weak: Welch t pooled, and per-date paired
    rows = []
    for col, (lab, filt, lag) in MEAS4.items():
        d = df.dropna(subset=[col]); d = d[d[filt]]
        a = d[d.posg == "≥0.7 strong"][col]; b = d[d.posg == "<0.3 weak"][col]
        if len(a) > 2 and len(b) > 2:
            tw, pw = ss.ttest_ind(a, b, equal_var=False)
            pa = d[d.posg == "≥0.7 strong"].groupby("analysis_date")[col].mean()
            pb = d[d.posg == "<0.3 weak"].groupby("analysis_date")[col].mean()
            dd = (pa - pb).dropna()
            td = dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd))) if len(dd) > 2 else np.nan
            rows.append([lab, len(a), len(b), fmt((a.mean() - b.mean()) * 100), fmt(tw), fmt(pw, 3), len(dd), fmt(dd.mean() * 100), fmt(td)])
    P("\nStrong − weak difference:\n")
    P(md_table(["measure", "n_strong", "n_weak", "diff pooled (pp)", "Welch t", "p", "n_days paired", "diff day-mean", "t_day"], rows))

# all-lake control for the same 40 dates (is the ledger pattern just the market-wide pattern?)
P("\n### All-lake control on the same analysis dates (every tradable stock, same definitions)\n")
sys.path.insert(0, str(REPO)); os.chdir(REPO)
from autoresearch.research import edge_census as ec
from autoresearch.common import ruler as _ruler
Pd = ec.lake_trade_days(); dates = sorted(M.analysis_date.unique())
piv = ec.load_lake_pivots(Pd[Pd.index(dates[0].replace("-", "")) - 1:])
lake_rows = []
for d in dates:
    D = d.replace("-", ""); idx = Pd.index(D)
    fr = ec.forward_frame(piv, Pd, D)
    def col(p, k):
        j = idx + k
        return p[Pd[j]] if (0 <= j < len(Pd) and Pd[j] in p.columns) else pd.Series(np.nan, index=p.index)
    c1, h1, l1 = col(piv["close"], 1), col(piv["high"], 1), col(piv["low"], 1)
    span = h1 - l1
    pos = ((c1 - l1) / span).where(span > 0)
    fr["c1c5"] = col(piv["close"], 5) / c1 - 1; fr["c1c10"] = col(piv["close"], 10) / c1 - 1
    okc1 = _ruler.entry_tradable(fr, "gap_c1_o2"); oko1 = _ruler.entry_tradable(fr, "fwd_5_oc")
    for meas, flag in (("gap_c1_o2", okc1), ("fwd_5_oc", oko1), ("fwd_10_oc", oko1), ("c1c5", okc1), ("c1c10", okc1)):
        v = pd.to_numeric(fr[meas], errors="coerce"); base = v[flag & v.notna()]
        if len(base) < 50: continue
        med = base.median()
        for gname, mask in (("≥0.7 strong", pos >= 0.7), ("<0.3 weak", pos < 0.3)):
            g = base[mask.reindex(base.index).fillna(False)]
            lake_rows.append(dict(date=d, meas=meas, grp=gname, n=len(g), rel=(g.mean() - med) * 100))
LK = pd.DataFrame(lake_rows)
rows = []
for meas in ["gap_c1_o2", "fwd_5_oc", "fwd_10_oc", "c1c5", "c1c10"]:
    for gname in ["≥0.7 strong", "<0.3 weak"]:
        s = LK[(LK.meas == meas) & (LK.grp == gname)]
        t = s.rel.mean() / (s.rel.std(ddof=1) / np.sqrt(len(s))) if len(s) > 2 else np.nan
        rows.append([meas, gname, len(s), int(s.n.median()), fmt(s.rel.mean()), fmt(t), f"{(s.rel < 0).mean():.2f}"])
    a = LK[(LK.meas == meas) & (LK.grp == "≥0.7 strong")].set_index("date").rel
    b = LK[(LK.meas == meas) & (LK.grp == "<0.3 weak")].set_index("date").rel
    dd = (a - b).dropna()
    rows.append([meas, "strong−weak", len(dd), "", fmt(dd.mean()), fmt(dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd)))), f"{(dd < 0).mean():.2f}"])
P(md_table(["measure", "group", "n_days", "median n/day", "day-mean rel (pp)", "t_day", "share days<0"], rows))

# ───────────── Q5
H("Q5 — pinned rows (role=pinned; dedupe LAST)")
pin = M[M.rg == "pinned"]
P(f"n_rows={len(pin)}, n_days={pin.analysis_date.nunique()}, distinct codes={pin.code.nunique()}: " + ", ".join(f"{c}({n})" for c, n in pin.code.value_counts().items()))
pin_all = M0[M0.role == "pinned"]
P(f"(before dedupe: n_rows={len(pin_all)}, distinct codes={pin_all.code.nunique()}; plus 2 shadow-BUY rows on 688766 that were pinned-lane)")
P(table_by(pin, "month", ["2026-07", "2026-08"]))
P("\nALL months:\n")
pin2 = pin.copy(); pin2["all"] = "ALL"
P(table_by(pin2, "all", ["ALL"]))
P("\nPer code:\n")
P(table_by(pin, "code", pin.code.value_counts().index.tolist()))

# ───────────── Q6
H("Q6 — L3 conviction (0–100) vs outcome, Spearman (finalists only; pinned have conviction too — shown separately)")
rows = []
for scope, df in (("finalist ALL", M[M.rg == "finalist"]),) + tuple((f"finalist {p}", M[(M.rg == "finalist") & (M.period == p)]) for p in periods) + (("finalist rated era ≥07-10", M[(M.rg == "finalist") & (M.analysis_date >= "2026-07-10")]), ("pinned ALL", M[M.rg == "pinned"])):
    for col, (lab, filt, lag) in MEAS.items():
        b = spearman_block(df, "conviction", col, filt)
        rows.append([scope, lab, b["n"], fmt(b["rho"], 3), fmt(b["p"], 3), b["days"], fmt(b["ic_mean"], 3), fmt(b["ic_t"])])
P(md_table(["scope", "outcome", "n", "pooled rho", "p", "n_days IC", "mean IC", "t(IC)"], rows))
cv = M[M.rg == "finalist"].conviction
P(f"\nconviction distribution (finalist): n={cv.notna().sum()}, mean={cv.mean():.1f}, sd={cv.std():.1f}, p10/50/90={cv.quantile(.1):.0f}/{cv.quantile(.5):.0f}/{cv.quantile(.9):.0f}")

# ───────────── Q7
H("Q7 — src=shared sanity")
ds = M0.groupby("analysis_date")["src"].agg(lambda s: set(s))
shared_only = sorted(d for d, s in ds.items() if s == {"shared"})
mixed = sorted(d for d, s in ds.items() if s == {"shared", "run"})
run_only = sorted(d for d, s in ds.items() if s == {"run"})
P(f"analysis_dates: {len(ds)} total; **shared-only = {len(shared_only)}** ({shared_only[0]}..{shared_only[-1]}); mixed = {len(mixed)} ({mixed}); run-only = {len(run_only)} ({run_only[0]}..{run_only[-1]}).")
P("Every date from 07-10 onward is shared-only → excluding shared rows removes the entire rated/E6 era; what is left is the ≤07-09 30-finalist era. Q1 finalist stats on src=run rows only (dedupe LAST):\n")
runonly = M[(M.src == "run") & (M.rg == "finalist")].copy(); runonly["s"] = "src=run only"
sharedonly = M[(M.src == "shared") & (M.rg == "finalist")].copy(); sharedonly["s"] = "src=shared only"
allf = M[M.rg == "finalist"].copy(); allf["s"] = "all finalist"
P(table_by(pd.concat([allf, runonly, sharedonly]), "s", ["all finalist", "src=run only", "src=shared only"]))
P("\nSensitivity — dedupe by FIRST run per date instead of LAST (finalist):\n")
ff = MF[MF.rg == "finalist"].copy(); ff["s"] = "FIRST-run dedupe"
lf = M[M.rg == "finalist"].copy(); lf["s"] = "LAST-run dedupe"
nf = M0[M0.role == "finalist"].copy(); nf["s"] = "no dedupe (all runs)"
P(table_by(pd.concat([lf, ff, nf]), "s", ["LAST-run dedupe", "FIRST-run dedupe", "no dedupe (all runs)"]))

(OUT / "readout.md").write_text("\n".join(R), encoding="utf-8")
print("\n".join(R))

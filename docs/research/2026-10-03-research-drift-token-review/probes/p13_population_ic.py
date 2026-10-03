import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import pandas as pd, numpy as np, glob, warnings
warnings.filterwarnings("ignore")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 80); pd.set_option("display.max_rows", 200)
L="reports_claude/scan/_ledger"
frames=[]
for f in sorted(glob.glob(L+"/populations/*.parquet")):
    try: p=pd.read_parquet(f)
    except Exception as e: continue
    frames.append(p)
P=pd.concat(frames,ignore_index=True)
print("pop rows",len(P),"runs",P.run_key.nunique(),"dates",P.analysis_date.nunique())
P=P[P.status_gap_c1_o2=="MATURE"].copy()
# one run per analysis_date: keep last run_key
last=P.groupby("analysis_date").run_key.max()
P=P[P.run_key.isin(last.values)]
print("mature rows",len(P),"dates",P.analysis_date.nunique(), P.analysis_date.min(), P.analysis_date.max())
def daily_ic(d, x, y, min_n=8):
    out=[]
    for dt,g in d.groupby("analysis_date"):
        g=g[[x,y]].dropna()
        if len(g)>=min_n and g[x].nunique()>1:
            out.append((dt, g[x].rank().corr(g[y].rank()), len(g)))
    s=pd.DataFrame(out,columns=["date","ic","n"])
    if len(s)==0: return None
    m=s.ic.mean(); sd=s.ic.std(); t=m/(sd/np.sqrt(len(s))) if sd>0 else np.nan
    return dict(days=len(s), mean_ic=round(m,4), sd=round(sd,3), t=round(t,2), pos=round((s.ic>0).mean(),2), avg_n=int(s.n.mean()))
rows=[]
for lab,(d,x) in {
 "L1池(≈1000) composite": (P[P.in_l1==True],"composite"),
 "L2池(≈208) composite": (P[P.in_l2==True],"composite"),
 "L2池 l2_rank(反向)": (P[P.in_l2==True].assign(l2r=lambda z:-z.l2_rank),"l2r"),
 "L3 judged conviction": (P[P.l3_judged==True],"l3_conviction"),
 "L1池 n_channels": (P[P.in_l1==True],"n_channels"),
}.items():
    for y in ["gap_c1_o2","rel_gap_sector","fwd_10_oc","fwd_2_oc"]:  # 主尺 gap_c1_o2;fwd_2_oc 只作参考尺
        if y not in d: continue
        dd=d[d["status_"+y]=="MATURE"] if "status_"+y in d else d
        r=daily_ic(dd,x,y)
        if r: rows.append(dict(signal=lab,label=y,**r))
print(pd.DataFrame(rows).to_string(index=False))
# membership contrasts (day-paired mean diff) on gap
def paired(d, flag_a, flag_b, y="gap_c1_o2"):
    out=[]
    for dt,g in d.groupby("analysis_date"):
        a=g[flag_a(g)][y].dropna(); b=g[flag_b(g)][y].dropna()
        if len(a)>=3 and len(b)>=3: out.append(a.mean()-b.mean())
    s=pd.Series(out)*100
    return dict(days=len(s), diff_pp=round(s.mean(),3), t=round(s.mean()/(s.std()/np.sqrt(len(s))),2), pos=round((s>0).mean(),2))
print("\npaired contrasts on gap_c1_o2 (pp/day):")
print(" L2 vs L1-not-L2 :", paired(P, lambda g:g.in_l2==True, lambda g:(g.in_l1==True)&(g.in_l2!=True)))
print(" pass1_kept vs L2-cut :", paired(P, lambda g:g.pass1_kept==True, lambda g:(g.in_l2==True)&(g.pass1_kept!=True)))
print(" finalist vs bench :", paired(P, lambda g:g.is_finalist==True, lambda g:g.is_bench==True))
print(" finalist vs L2-rest :", paired(P, lambda g:g.is_finalist==True, lambda g:(g.in_l2==True)&(g.is_finalist!=True)))
for y in ["fwd_10_oc"]:
    Q=P[P["status_"+y]=="MATURE"]
    print(" [fwd10] finalist vs L2-rest :", paired(Q, lambda g:g.is_finalist==True, lambda g:(g.in_l2==True)&(g.is_finalist!=True), y))
    print(" [fwd10] L2 vs L1-not-L2 :", paired(Q, lambda g:g.in_l2==True, lambda g:(g.in_l1==True)&(g.in_l2!=True), y))

import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import pandas as pd, numpy as np, glob, warnings
warnings.filterwarnings("ignore")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 80); pd.set_option("display.max_rows", 200)
L="reports_claude/scan/_ledger"
U=pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(L+"/universe/*.parquet"))],ignore_index=True)
print("universe rows",len(U),"dates",U.analysis_date.nunique(),"runs",U.run_key.nunique())
U=U[U.status_gap_c1_o2=="MATURE"]
last=U.groupby("analysis_date").run_key.max(); U=U[U.run_key.isin(last.values)]
print("mature",len(U),"dates",U.analysis_date.nunique(),U.analysis_date.min(),U.analysis_date.max())
def era(d):
    return "A ≤08-19 (calibrated·样本内为主)" if d<="2026-08-19" else ("B 08-20→09-17 (calibrated·样本外)" if d<="2026-09-17" else "C ≥09-24 (preference)")
U["era"]=U.analysis_date.map(era)
def daily_ic(d,x,y):
    out=[]
    for dt,g in d.groupby("analysis_date"):
        g=g[[x,y]].dropna()
        if len(g)>=30: out.append((dt,g[x].rank().corr(g[y].rank()),len(g)))
    return pd.DataFrame(out,columns=["date","ic","n"])
def summ(s):
    if len(s)==0: return {}
    m=s.ic.mean(); sd=s.ic.std()
    return dict(days=len(s),mean_ic=round(m,4),t=round(m/(sd/np.sqrt(len(s))),2) if len(s)>1 and sd>0 else None,pos=round((s.ic>0).mean(),2),avg_n=int(s.n.mean()))
for y in ["gap_c1_o2","fwd_10_oc"]:
    V=U[U["status_"+y]=="MATURE"]
    print(f"\n== full universe composite IC vs {y}")
    print(" ALL", summ(daily_ic(V,"composite",y)))
    for e,g in V.groupby("era"): print(" ",e, summ(daily_ic(g,"composite",y)))
    print(" in_l1 only:", summ(daily_ic(V[V.in_l1==True],"composite",y)))
# decile spread on gap, full universe, by era, demeaned by day
def decile(d,y="gap_c1_o2",q=10):
    rows=[]
    for dt,g in d.groupby("analysis_date"):
        g=g[["composite",y]].dropna()
        if len(g)<200: continue
        g["q"]=pd.qcut(g.composite.rank(method="first"),q,labels=False)
        m=g.groupby("q")[y].mean()-g[y].mean()
        rows.append(m)
    R=pd.DataFrame(rows)*100
    return R.mean().round(3), (R[q-1]-R[0]).agg(["mean","std","count"])
for e,g in list(U.groupby("era"))+[("ALL",U)]:
    m,ls=decile(g)
    t=ls["mean"]/(ls["std"]/np.sqrt(ls["count"])) if ls["count"]>1 else np.nan
    print(f"\n{e}: composite decile excess overnight (pp, day-demeaned):\n  ", m.to_dict(), f"\n   top-bottom {ls['mean']:.3f}pp/day t={t:.2f} n_days={int(ls['count'])}")
# top-20 by composite: mean excess gap & abs gap (buyable only)
rows=[]
for dt,g in U.groupby("analysis_date"):
    g=g.dropna(subset=["composite","gap_c1_o2"])
    b=g[g.buyable_c1==True] if g.buyable_c1.notna().any() else g
    top=b.nlargest(20,"composite")
    rows.append(dict(date=dt,era=era(dt),top20=top.gap_c1_o2.mean()*100,mkt=g.gap_c1_o2.mean()*100))
T=pd.DataFrame(rows); T["ex"]=T.top20-T.mkt
print("\nTop-20 composite (buyable) overnight: abs %.3f pp/day, market %.3f, excess %.3f (t=%.2f, pos %.2f, n=%d)"%(T.top20.mean(),T.mkt.mean(),T.ex.mean(),T.ex.mean()/(T.ex.std()/np.sqrt(len(T))),(T.ex>0).mean(),len(T)))
print(T.groupby("era").agg(n=("ex","size"),top20=("top20","mean"),mkt=("mkt","mean"),ex=("ex","mean")).round(3))

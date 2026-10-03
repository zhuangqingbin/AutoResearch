"""4.5y EOD-proxy robustness: T-day heat features & T+1 close-location vs overnight gap c1->o2. Read-only on lake/."""
import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)

import pandas as pd, numpy as np, glob, os, warnings, pickle
warnings.filterwarnings("ignore")
S=_OUT+"/"
def load(t, cols):
    import pyarrow.parquet as pq
    fs=sorted(f for f in glob.glob(f"lake/{t}/*.parquet"))
    out=[]; skipped=0
    for f in fs:
        names=pq.read_schema(f).names
        use=[c for c in cols if c in names]
        d=pd.read_parquet(f, columns=use)
        d["trade_date"]=os.path.basename(f)[:8]
        for c in cols:
            if c not in d: d[c]=np.nan
        out.append(d[cols])
    print(t,"files loaded",len(out),"of",len(fs))
    return pd.concat(out, ignore_index=True)
D=load("daily",["ts_code","trade_date","open","high","low","close","pre_close","pct_chg","amount"])
D["pre_close"]=D.pre_close.fillna(D.close/(1+D.pct_chg/100))
B=load("daily_basic",["ts_code","trade_date","turnover_rate","volume_ratio","total_mv"])
B=B.rename(columns={"turnover_rate":"turnover_rate_f","total_mv":"circ_mv"})
D["trade_date"]=D.trade_date.astype(str); B["trade_date"]=B.trade_date.astype(str)
D=D.drop_duplicates(["ts_code","trade_date"]); B=B.drop_duplicates(["ts_code","trade_date"])
D=D.merge(B,on=["ts_code","trade_date"],how="left")
D=D[~D.ts_code.str.endswith(".BJ")]
D=D.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
g=D.groupby("ts_code",sort=False)
D["lr"]=np.log1p(D.pct_chg/100)
D["ret60"]=(np.exp(g.lr.transform(lambda s:s.rolling(60,min_periods=40).sum()))-1)*100
D["ret5"]=(np.exp(g.lr.transform(lambda s:s.rolling(5,min_periods=5).sum()))-1)*100
D["rng"]=(D.high-D.low)
D["pos"]=np.where(D.rng>0,(D.close-D.low)/D.rng,np.nan)
D["ogap"]=(D.open/D.pre_close-1)*100          # overnight gap INTO this day (prev close -> this open)
# labels relative to analysis day T (row): T+1 values and gap c1->o2
for k,src in [("pct1","pct_chg"),("pos1","pos"),("amt1","amount")]:
    D[k]=g[src].shift(-1)
D["gap_c1_o2"]=g.ogap.shift(-2)
D["lim"]=np.where(D.ts_code.str.startswith(("300","301","688","689")),19.7,9.7)
D["buyable1"]=(D.pct1<D.lim)&(D.amt1>0)
X=D[(D.amount>0)&D.gap_c1_o2.notna()&D.buyable1&(D.gap_c1_o2.abs()<25)].copy()
print("rows",len(X),"days",X.trade_date.nunique(),X.trade_date.min(),X.trade_date.max())
X["year"]=X.trade_date.str[:4]
def ic_table(d,feats,y="gap_c1_o2",minn=300):
    rows=[]
    for x in feats:
        out=[]
        for dt,gg in d.groupby("trade_date"):
            gg=gg[[x,y]].dropna()
            if len(gg)>=minn: out.append((dt[:4],gg[x].rank().corr(gg[y].rank())))
        s=pd.DataFrame(out,columns=["yr","ic"])
        r=dict(feature=x,days=len(s),IC=round(s.ic.mean(),4),t=round(s.ic.mean()/(s.ic.std()/np.sqrt(len(s))),1),pos=round((s.ic>0).mean(),2))
        for yr,gy in s.groupby("yr"): r[yr]=round(gy.ic.mean(),3)
        rows.append(r)
    return pd.DataFrame(rows)
pd.set_option("display.width",250); pd.set_option("display.max_columns",40)
print("\n[A] T-day features -> gap c1->o2 (full buyable universe)")
print(ic_table(X,["turnover_rate_f","volume_ratio","pct_chg","ret5","ret60","amount","circ_mv","pos"]).to_string(index=False))
print("\n[B] T+1-day (execution day) features -> gap c1->o2")
print(ic_table(X,["pos1","pct1"]).to_string(index=False))
def spread(d,x,q=10,y="gap_c1_o2",minn=300):
    rows=[]
    for dt,gg in d.groupby("trade_date"):
        gg=gg[[x,y]].dropna()
        if len(gg)<minn: continue
        gg["q"]=pd.qcut(gg[x].rank(method="first"),q,labels=False)
        rows.append(gg.groupby("q")[y].mean()-gg[y].mean())
    R=pd.DataFrame(rows); ls=R[q-1]-R[0]
    return R.mean().round(3).to_dict(), round(ls.mean(),3), round(ls.mean()/(ls.std()/np.sqrt(len(ls))),1), len(ls)
print("\n[C] decile excess overnight (pp, day-demeaned): top-bottom")
for x in ["turnover_rate_f","pct_chg","ret60","pos1","pct1"]:
    m,ls,t,n=spread(X,x); print(f"  {x:16s} deciles={m}  top-bottom={ls}pp t={t} days={n}")
# healthy proxy & knife vs market
rows=[]
for dt,gg in X.groupby("trade_date"):
    mk=gg.gap_c1_o2.mean()
    h=gg[(gg.ret60>0)&(gg.ret60<40)]; k=gg[gg.ret60<-20]; hot=gg[gg.ret60>=40]
    rows.append(dict(date=dt,yr=dt[:4],mkt=mk,healthy=h.gap_c1_o2.mean()-mk,knife=k.gap_c1_o2.mean()-mk,hot=hot.gap_c1_o2.mean()-mk,nh=len(h),nk=len(k)))
R=pd.DataFrame(rows)
print("\n[D] market mean overnight gap c1->o2: %.3f pp/day (t=%.1f, pos %.2f, days=%d)"%(R.mkt.mean(),R.mkt.mean()/(R.mkt.std()/np.sqrt(len(R))),(R.mkt>0).mean(),len(R)))
print(R.groupby("yr").mkt.mean().round(3).to_dict())
for c in ["healthy","knife","hot"]:
    s=R[c].dropna(); print(f"  {c:8s} (ret60 bucket) minus market: {s.mean():+.3f}pp/day t={s.mean()/(s.std()/np.sqrt(len(s))):.1f} pos={(s>0).mean():.2f} by year {R.groupby('yr')[c].mean().round(3).to_dict()}")
# inside healthy proxy: heat quintiles (turnover rank + pct_chg rank)
H=X[(X.ret60>0)&(X.ret60<40)].copy()
rk=H.groupby("trade_date")[["turnover_rate_f","pct_chg","volume_ratio"]].rank(pct=True); H["heat"]=rk.mean(axis=1)
rows=[]
for dt,gg in H.groupby("trade_date"):
    gg=gg.dropna(subset=["heat"]); 
    if len(gg)<200: continue
    gg["q"]=pd.qcut(gg.heat.rank(method="first"),5,labels=False)
    mk=X[X.trade_date==dt].gap_c1_o2.mean() if False else None
    rows.append(gg.groupby("q").gap_c1_o2.mean())
Q=pd.DataFrame(rows); Q["yr"]=[d[:4] for d in H.groupby("trade_date").filter(lambda g: g.heat.notna().sum()>=200).trade_date.drop_duplicates().sort_values()][:len(Q)]
mk=R.set_index("date").mkt
print("\n[E] inside healthy proxy (0<ret60<40): absolute overnight by heat quintile (0=coolest):",Q[[0,1,2,3,4]].mean().round(3).to_dict())
ls=Q[0]-Q[4]; print("   coolest−hottest %.3f pp/day t=%.1f days=%d ; by year: %s"%(ls.mean(),ls.mean()/(ls.std()/np.sqrt(len(ls))),len(ls),(Q.assign(ls=ls).groupby('yr').ls.mean().round(3).to_dict())))
# exec-line joint: pos1<0.7 & pct1<=3 vs others, day-paired
rows=[]
for dt,gg in X.groupby("trade_date"):
    a=gg[(gg.pos1<0.7)&(gg.pct1<=3)].gap_c1_o2; b=gg[~((gg.pos1<0.7)&(gg.pct1<=3))].gap_c1_o2
    lo=gg[gg.pos1<=0.3].gap_c1_o2; hi=gg[gg.pos1>=0.7].gap_c1_o2
    rows.append(dict(yr=dt[:4],inout=a.mean()-b.mean(),lohi=lo.mean()-hi.mean(),lo=lo.mean(),hi=hi.mean()))
E=pd.DataFrame(rows)
for c in ["inout","lohi"]:
    s=E[c].dropna(); print(f"[F] {c}: {s.mean():+.3f}pp/day t={s.mean()/(s.std()/np.sqrt(len(s))):.1f} pos={(s>0).mean():.2f} by year {E.groupby('yr')[c].mean().round(3).to_dict()}")
print("    abs overnight: close in lower30%% = %.3f ; upper30%% = %.3f"%(E.lo.mean(),E.hi.mean()))
X[["ts_code","trade_date","gap_c1_o2","turnover_rate_f","pct_chg","ret60","pos1","pct1","volume_ratio","circ_mv"]].to_parquet(S+"long_panel.parquet")

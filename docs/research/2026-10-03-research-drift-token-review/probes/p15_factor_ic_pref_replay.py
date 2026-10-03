import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import pandas as pd, numpy as np, glob, os, warnings
warnings.filterwarnings("ignore")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 80); pd.set_option("display.max_rows", 200)
L="reports_claude/scan/_ledger"
U=pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(L+"/universe/*.parquet"))],ignore_index=True)
U=U[U.status_gap_c1_o2=="MATURE"]
last=U.groupby("analysis_date").run_key.max(); U=U[U.run_key.isin(last.values)]
lab=U[["run_key","analysis_date","code","gap_c1_o2","fwd_10_oc","status_fwd_10_oc","buyable_c1"]]
frames=[]
for rk in sorted(lab.run_key.unique()):
    f=f"reports_claude/scan/{rk}/trace/L1_scored_full.csv"
    if not os.path.exists(f): continue
    s=pd.read_csv(f,dtype={"code":str}); s["code"]=s.code.str.zfill(6); s["run_key"]=rk
    frames.append(s)
S=pd.concat(frames,ignore_index=True)
M=S.merge(lab,on=["run_key","code"],how="inner")
print("merged",len(M),"dates",M.analysis_date.nunique(),M.analysis_date.min(),M.analysis_date.max())
pref={"momentum":0.20,"tech":0.15,"volprice":0.15,"fund_main":0.15,"chip":0.05,"north":0.05,"growth":0.05,"value":0.05,"fund_retail":-0.05,"rz":0.0}
sc=[c for c in M.columns if c.startswith("score_")]
M["pref_composite"]=sum(M["score_"+k].fillna(50)*w for k,w in pref.items() if "score_"+k in M)
def ic(d,x,y):
    out=[]
    for dt,g in d.groupby("analysis_date"):
        g=g[[x,y]].dropna()
        if len(g)>=200 and g[x].nunique()>5: out.append(g[x].rank().corr(g[y].rank()))
    s=pd.Series(out)
    if len(s)<2: return (len(s),np.nan,np.nan,np.nan)
    return (len(s), round(s.mean(),4), round(s.mean()/(s.std()/np.sqrt(len(s))),2), round((s>0).mean(),2))
rows=[]
feats=["composite","pref_composite"]+sc+["pct_1d","pct_5d","pct_20d","pct_60d","turnover","vol_ratio","rsi6","winner_rate","amount_yi","mktcap_yi","main_net_ratio","cmf_20","pe","dv_ratio"]
for x in feats:
    if x not in M: continue
    a=ic(M,x,"gap_c1_o2"); b=ic(M[M.status_fwd_10_oc=="MATURE"],x,"fwd_10_oc")
    rows.append(dict(feature=x,days=a[0],IC_gap=a[1],t_gap=a[2],pos_gap=a[3],IC_f10=b[1],t_f10=b[2]))
print(pd.DataFrame(rows).to_string(index=False))
# out-of-sample era for calibrated (>= 08-20)
B=M[M.analysis_date>="2026-08-20"]
print("\n≥08-20 (calibrated weights OOS + 1 preference day):")
for x in ["composite","pref_composite","score_momentum","score_tech","score_volprice","score_fund_main","score_value","pct_1d","turnover","pct_60d"]:
    a=ic(B,x,"gap_c1_o2"); print(f"  {x:18s} days={a[0]} IC={a[1]} t={a[2]} pos={a[3]}")
# decile of pref_composite
def dec(d,x,y="gap_c1_o2"):
    rows=[]
    for dt,g in d.groupby("analysis_date"):
        g=g[[x,y]].dropna()
        if len(g)<500: continue
        g["q"]=pd.qcut(g[x].rank(method="first"),10,labels=False)
        rows.append(g.groupby("q")[y].mean()-g[y].mean())
    R=pd.DataFrame(rows)*100; ls=R[9]-R[0]
    return R.mean().round(3).to_dict(), round(ls.mean(),3), round(ls.mean()/(ls.std()/np.sqrt(len(ls))),2), len(ls)
print("\npref_composite deciles (overnight excess pp):",dec(M,"pref_composite"))
print("calibrated composite deciles:",dec(M,"composite"))
print("pct_1d deciles:",dec(M,"pct_1d"))
print("turnover deciles:",dec(M,"turnover"))

print("\n\n########## inside preference-eligible sets")
H=M[(M.pct_60d>0)&(M.pct_60d<40)&(M.main_net_ratio>0)&(M.cmf_20>0)]
NK=M[(M.pct_60d>-20)]
print("healthy rows/day:",int(H.groupby('analysis_date').size().mean())," non-knife rows/day:",int(NK.groupby('analysis_date').size().mean()))
def ic2(d,x,y,minn=60):
    out=[]
    for dt,g in d.groupby("analysis_date"):
        g=g[[x,y]].dropna()
        if len(g)>=minn and g[x].nunique()>5: out.append(g[x].rank().corr(g[y].rank()))
    s=pd.Series(out)
    if len(s)<2: return (len(s),np.nan,np.nan,np.nan)
    return (len(s), round(s.mean(),4), round(s.mean()/(s.std()/np.sqrt(len(s))),2), round((s>0).mean(),2))
rows=[]
for x in ["pref_composite","composite","pct_1d","turnover","vol_ratio","rsi6","winner_rate","amount_yi","score_tech","score_momentum","score_volprice","score_fund_main","pct_60d","cmf_20","main_net_ratio","dist_high_60"]:
    if x not in M: continue
    a=ic2(H,x,"gap_c1_o2"); b=ic2(NK,x,"gap_c1_o2",200)
    rows.append(dict(feature=x,H_days=a[0],H_IC=a[1],H_t=a[2],H_pos=a[3],NK_IC=b[1],NK_t=b[2]))
print(pd.DataFrame(rows).to_string(index=False))
# healthy set vs market overnight (day-paired)
d=[]
for dt,g in M.groupby("analysis_date"):
    h=g[(g.pct_60d>0)&(g.pct_60d<40)&(g.main_net_ratio>0)&(g.cmf_20>0)]
    kn=g[(g.pct_60d<-20)]
    d.append(dict(date=dt,mkt=g.gap_c1_o2.mean()*100,healthy=h.gap_c1_o2.mean()*100,knife=kn.gap_c1_o2.mean()*100,nh=len(h),nk=len(kn)))
D=pd.DataFrame(d); 
for c in ["healthy","knife"]:
    e=D[c]-D.mkt; print(f"{c} − market overnight: {e.mean():+.3f}pp/day t={e.mean()/(e.std()/np.sqrt(len(e))):.2f} pos={ (e>0).mean():.2f} (avg n={int(D['n'+c[0]].mean())})")
# within healthy: quintiles of 'heat today' (rank avg of pct_1d,turnover,rsi6) 
H2=H.copy()
rk=H2.groupby("analysis_date")[["turnover","rsi6","winner_rate"]].rank(pct=True)
H2["heat"]=rk.mean(axis=1)
rows=[]
for dt,g in H2.groupby("analysis_date"):
    g=g.dropna(subset=["heat","gap_c1_o2"])
    if len(g)<50: continue
    g["q"]=pd.qcut(g.heat.rank(method="first"),5,labels=False)
    mk=M[M.analysis_date==dt].gap_c1_o2.mean()
    rows.append((g.groupby("q").gap_c1_o2.mean()-mk)*100)
R=pd.DataFrame(rows); ls=R[0]-R[4]
print("healthy set, quintiles by heat(turnover/rsi6/winner rank-avg): excess vs market overnight pp:",R.mean().round(3).to_dict()," coolest−hottest %.3f t=%.2f days=%d"%(ls.mean(),ls.mean()/(ls.std()/np.sqrt(len(ls))),len(ls)))

import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import re, glob, os, warnings
import pandas as pd, numpy as np
warnings.filterwarnings("ignore")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60)
S=_OUT+"/"
i=pd.read_csv(S+"intel.csv")
rows=[]
for f in glob.glob("reports_claude/scan/2026*/details/*.md"):
    t=open(f,encoding="utf-8").read()[:300]; m=re.search(r"^# 决策卡 — (\d{6})", t, re.M)
    rows.append(dict(run=f.split("/")[2],name=os.path.basename(f)[:-3],code=m.group(1) if m else None))
i=i.merge(pd.DataFrame(rows),on=["run","name"])
L=pd.read_csv("reports_claude/scan/_ledger/recommendations.csv",dtype={"code":str})
L=L[L.role!="BUY"].drop_duplicates(["run_id","code"])
m=i.merge(L,left_on=["run","code"],right_on=["run_id","code"]).dropna(subset=["t1_open","t1_close","t1_pct_chg"])
m["t0_close"]=m.t1_close/(1+m.t1_pct_chg/100)
m["gap_o1"]=(m.t1_open/m.t0_close-1)*100
m["intra1"]=(m.t1_close/m.t1_open-1)*100
m["c1o2"]=m.gap_c1_o2*100
# market means for T+1 day from lake
mk={}
for d in m.t1.dropna().astype(int).astype(str).unique():
    p=f"lake/daily/{d}.parquet"
    if not os.path.exists(p): continue
    x=pd.read_parquet(p); pc=x.pre_close if "pre_close" in x else x.close/(1+x.pct_chg/100)
    mk[d]=(((x.open/pc-1)*100).mean(), ((x.close/x.open-1)*100).mean())
m["t1s"]=m.t1.astype(int).astype(str)
m["gap_o1_rel"]=m.gap_o1-m.t1s.map(lambda d: mk.get(d,(np.nan,np.nan))[0])
m["intra1_rel"]=m.intra1-m.t1s.map(lambda d: mk.get(d,(np.nan,np.nan))[1])
m["c1o2_rel"]=m.rel_gap_market*100
m["cls"]=np.where(m.neg>0,"neg",np.where(m.pos>0,"pos_only","none"))
g=m.groupby("cls").agg(n=("c1o2","size"),days=("analysis_date","nunique"),T_to_o1_rel=("gap_o1_rel","mean"),o1_to_c1_rel=("intra1_rel","mean"),c1_to_o2_rel=("c1o2_rel","mean"))
print(g.round(3).to_string())
def tt(a):
    a=a.dropna(); return f"{a.mean():+.3f} (t={a.mean()/(a.std()/np.sqrt(len(a))):+.2f}, n={len(a)})"
for c in ["neg","pos_only","none"]:
    x=m[m.cls==c]; print(c,"| T→o1:",tt(x.gap_o1_rel),"| o1→c1:",tt(x.intra1_rel),"| c1→o2:",tt(x.c1o2_rel))

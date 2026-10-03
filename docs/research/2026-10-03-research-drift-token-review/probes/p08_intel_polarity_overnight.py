import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import re, glob, os, warnings
import pandas as pd, numpy as np
warnings.filterwarnings("ignore")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60)
S=_OUT+"/"
i=pd.read_csv(S+"intel.csv")
# map name->code via cards
rows=[]
for f in glob.glob("reports_claude/scan/2026*/details/*.md"):
    t=open(f,encoding="utf-8").read()[:300]
    m=re.search(r"^# 决策卡 — (\d{6})", t, re.M)
    rows.append(dict(run=f.split("/")[2],name=os.path.basename(f)[:-3],code=m.group(1) if m else None))
c=pd.DataFrame(rows)
i=i.merge(c,on=["run","name"])
L=pd.read_csv("reports_claude/scan/_ledger/recommendations.csv",dtype={"code":str})
L=L[L.role!="BUY"].drop_duplicates(["run_id","code"])
m=i.merge(L,left_on=["run","code"],right_on=["run_id","code"])
m["g"]=m.gap_c1_o2*100; m["rm"]=m.rel_gap_market*100; m["f5"]=m.fwd_5_oc*100
def agg(d,by):
    return d.groupby(by,dropna=False).agg(n=("g","size"),days=("analysis_date","nunique"),gap=("g","mean"),rel=("rm","mean"),relwin=("rm",lambda s:(s>0).mean()),f5=("f5","mean")).round(3)
m["neg"]=m.neg>0; m["pos"]=m.pos>0; m["t0inc"]=m.t0.fillna("").str.startswith("有增量")
print("joined",len(m), m.analysis_date.min(), m.analysis_date.max())
print(agg(m,"neg")); print(agg(m,"pos")); print(agg(m,"t0inc"))
m["cls"]=np.where(m.neg,"neg",np.where(m.pos,"pos_only","none"))
print(agg(m,"cls"))

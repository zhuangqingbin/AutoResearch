import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import pandas as pd, numpy as np
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60); pd.set_option("display.max_rows", 500)
df = pd.read_csv("reports_claude/scan/_ledger/recommendations.csv", dtype={"code": str})
p = df[(df.role=="pinned")&(df.analysis_date>="2026-08-10")].sort_values(["code","analysis_date","run_id"]).drop_duplicates(["code","analysis_date"],keep="last")
for code in ["688981","300750"]:
    x=p[p.code==code][["analysis_date","name","rating","conviction","t1_close","t1_pct_chg","gap_c1_o2","fwd_5_oc","fwd_10_oc"]].copy()
    x["gap"]=(x.gap_c1_o2*100).round(2); x["f5"]=(x.fwd_5_oc*100).round(1); x["f10"]=(x.fwd_10_oc*100).round(1)
    x["flip"]=(x.rating!=x.rating.shift()).map({True:"<<",False:""})
    print(x.drop(columns=["gap_c1_o2","fwd_5_oc","fwd_10_oc"]).to_string(index=False)); print()
# flips count for all pinned
q=p.sort_values(["code","analysis_date"])
q["prev"]=q.groupby("code").rating.shift()
q2=q.dropna(subset=["prev","rating"])
print("pinned consecutive pairs:",len(q2)," flips:",(q2.rating!=q2.prev).sum())
# finalists day-over-day persistence & rating stability for repeated non-pinned names
f=df[(df.role=="finalist")&(df.analysis_date>="2026-08-10")].sort_values(["code","analysis_date","run_id"]).drop_duplicates(["code","analysis_date"],keep="last")
f["prev"]=f.groupby("code").rating.shift(); f["prev_date"]=f.groupby("code").analysis_date.shift()
r=f.dropna(subset=["prev","rating"])
print("non-pinned repeated appearances:",len(r)," rating changed:",(r.rating!=r.prev).sum())
print(pd.crosstab(r.prev,r.rating))

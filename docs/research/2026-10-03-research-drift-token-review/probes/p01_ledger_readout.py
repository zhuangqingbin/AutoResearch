import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import pandas as pd, numpy as np
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60); pd.set_option("display.max_rows", 400)
df = pd.read_csv("reports_claude/scan/_ledger/recommendations.csv", dtype={"code": str})
df["g"] = df.gap_c1_o2*100; df["rm"] = df.rel_gap_market*100; df["rs"]=df.rel_gap_sector*100
print("=== all E6 BUY rows")
b = df[df.e6_buy==True][["run_id","analysis_date","mode","src","code","name","role","rating","buy_tier","e6_rank","t1","t2","t1_pos_in_range","exec_ok","g","rm","rs","fwd_5_oc","fwd_10_oc"]]
print(b.to_string())
act = b[b["mode"]=="active"]
print("active BUY n=%d mean gap=%.3f rel_mkt=%.3f win=%d/%d" % (len(act), act.g.mean(), act.rm.mean(), (act.g>0).sum(), len(act)))
def agg(d, by):
    r = d.groupby(by, dropna=False).agg(n=("g","size"), days=("analysis_date","nunique"), gap=("g","mean"), rel_mkt=("rm","mean"), rel_sec=("rs","mean"), win=("g", lambda s:(s>0).mean()), f5=("fwd_5_oc", lambda s: s.mean()*100), f10=("fwd_10_oc", lambda s:s.mean()*100))
    return r.round(3)
# dedupe: one row per (analysis_date, code) — prefer src=run
d = df.sort_values(["analysis_date","code","src"]).drop_duplicates(["run_id","code","role"])
print("\n=== by role (all)"); print(agg(d,"role"))
e6 = d[d.analysis_date>="2026-08-19"]
print("\n=== since 08-19 by role"); print(agg(e6,"role"))
np_ = e6[e6.role.isin(["finalist","composite_seat","sector_seat"])]
print("\n=== since 08-19 non-pinned by rating"); print(agg(np_,"rating"))
print("\n=== since 08-19 pinned by rating"); print(agg(e6[e6.role=="pinned"],"rating"))
print("\n=== all-time non-pinned finalists by rating"); print(agg(d[d.role=="finalist"],"rating"))
print("\n=== since 08-19 finalists by lane"); print(agg(np_,"lane"))
print("\n=== per analysis_date since 09-01 (finalist+seat, non-pinned)")
x = d[(d.analysis_date>="2026-09-01")&(d.role!="pinned")&(d.role!="BUY")]
print(agg(x,"analysis_date"))
print("\n=== early_stop_reason since 08-19 non-pinned"); print(agg(np_,"early_stop_reason"))
# conviction buckets
np2 = np_.copy(); np2["cb"]=pd.cut(np2.conviction,[0,40,50,60,70,80,100])
print("\n=== conviction buckets since 08-19 non-pinned"); print(agg(np2,"cb"))
np2["pos"]=pd.cut(np2.t1_pos_in_range,[-0.01,0.3,0.7,1.01])
print("\n=== t1 pos in range"); print(agg(np2,"pos"))
print("\n=== exec_ok"); print(agg(np2,"exec_ok"))

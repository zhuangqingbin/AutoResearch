"""Preference composite: (a) replay vs production composite, (b) realized IC on matured preference-era days, (c) heat percentiles of L2 pool vs finalists."""
import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)

import pandas as pd, numpy as np
pref={"momentum":0.20,"tech":0.15,"volprice":0.15,"fund_main":0.15,"chip":0.05,"north":0.05,"growth":0.05,"value":0.05,"fund_retail":-0.05,"rz":0.0}
print("== (a) replayed preference composite vs production composite")
for rk in ["20260917-0917_2152","20260924-0927_2232","20260928-0928_2148","20260929-0929_2213"]:
    s=pd.read_csv(f"reports_claude/scan/{rk}/trace/L1_scored_full.csv",dtype={"code":str})
    mine=sum(s["score_"+k].fillna(50)*w for k,w in pref.items())
    print(" ",rk,"n",len(s),"spearman =",round(mine.rank().corr(s.composite.rank()),4))
def gap(t1,t2):
    a=pd.read_parquet(f"lake/daily/{t1}.parquet"); b=pd.read_parquet(f"lake/daily/{t2}.parquet")
    a["code"]=a.ts_code.str[:6]; b["code"]=b.ts_code.str[:6]
    pc=b.pre_close if "pre_close" in b else b.close/(1+b.pct_chg/100)
    b["gap"]=(b.open/pc-1)*100
    lim=np.where(a.code.str.startswith(("300","301","688","689")),19.7,9.7)
    a["buyable"]=a.pct_chg<lim
    return a[["code","buyable"]].merge(b[["code","gap"]],on="code")
print("== (b) realized on matured preference-era days (production composite)")
for rk,t1,t2 in [("20260924-0927_2232","20260928","20260929"),("20260928-0928_2148","20260929","20260930")]:
    s=pd.read_csv(f"reports_claude/scan/{rk}/trace/L1_scored_full.csv",dtype={"code":str}); s["code"]=s.code.str.zfill(6)
    m=s.merge(gap(t1,t2),on="code"); m=m[m.buyable]
    m["q"]=pd.qcut(m.composite.rank(method="first"),10,labels=False)
    d=(m.groupby("q").gap.mean()-m.gap.mean()).round(3)
    l2=pd.read_csv(f"reports_claude/scan/{rk}/trace/L2_gbdt_top200.csv",dtype={"code":str}); l2c=set(l2.code.astype(str).str.zfill(6))
    print(" ",rk,"T+1",t1,"T+2",t2,"IC(composite,gap)=",round(m.composite.rank().corr(m.gap.rank()),4),"| top decile",d[9],"bottom",d[0],"| market",round(m.gap.mean(),3),"| L2 pool",round(m[m.code.isin(l2c)].gap.mean(),3))
print("== (c) median percentile (full universe) of L2 pool vs non-pinned finalists")
for rk in ["20260917-0917_2152","20260924-0927_2232","20260928-0928_2148","20260929-0929_2213"]:
    s=pd.read_csv(f"reports_claude/scan/{rk}/trace/L1_scored_full.csv",dtype={"code":str}); s["code"]=s.code.str.zfill(6)
    cols=["turnover","rsi6","winner_rate","pct_1d","pct_60d","composite"]
    for c in cols: s[c+"_p"]=s[c].rank(pct=True)
    l2c=set(pd.read_csv(f"reports_claude/scan/{rk}/trace/L2_gbdt_top200.csv",dtype={"code":str}).code.astype(str).str.zfill(6))
    fc=set(pd.read_csv(f"reports_claude/scan/{rk}/trace/L3_fine_finalists.csv",dtype={"code":str}).code.astype(str).str.zfill(6))-{"688981","300750"}
    pc=[c+"_p" for c in cols]
    print(" ",rk,"L2:",s[s.code.isin(l2c)][pc].median().round(2).to_dict())
    print(" "*len(rk),"  FIN:",s[s.code.isin(fc)][pc].median().round(2).to_dict())

"""Day-paired stage contrasts from ledger populations, pinned/seat excluded."""
import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)

import pandas as pd, numpy as np, glob, warnings
warnings.filterwarnings("ignore")
L="reports_claude/scan/_ledger"
P=pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(L+"/populations/*.parquet"))],ignore_index=True)
P=P[P.status_gap_c1_o2=="MATURE"]; last=P.groupby("analysis_date").run_key.max(); P=P[P.run_key.isin(last.values)]
def paired(d, fa, fb, y="gap_c1_o2", mina=3, minb=3):
    out=[]
    for dt,g in d.groupby("analysis_date"):
        a=g[fa(g)][y].dropna(); b=g[fb(g)][y].dropna()
        if len(a)>=mina and len(b)>=minb: out.append(a.mean()-b.mean())
    s=pd.Series(out)*100
    return f"days={len(s)} diff={s.mean():+.3f}pp t={s.mean()/(s.std()/np.sqrt(len(s))):+.2f} pos={(s>0).mean():.2f}"
np_fin=lambda g:(g.is_finalist==True)&(g.is_pinned!=True)&(g.is_composite_seat!=True)
print("[gap] finalist(non-pinned) vs bench      :",paired(P,np_fin,lambda g:g.is_bench==True))
print("[gap] finalist(non-pinned) vs L2-rest    :",paired(P,np_fin,lambda g:(g.in_l2==True)&(g.is_finalist!=True)))
print("[gap] L3-judged vs L2-not-judged         :",paired(P,lambda g:g.l3_judged==True,lambda g:(g.in_l2==True)&(g.l3_judged!=True)))
print("[gap] L2 vs L1-not-L2                    :",paired(P,lambda g:g.in_l2==True,lambda g:(g.in_l1==True)&(g.in_l2!=True)))
Q=P[P.status_fwd_10_oc=="MATURE"]
print("[f10] finalist(non-pinned) vs bench      :",paired(Q,np_fin,lambda g:g.is_bench==True,"fwd_10_oc"))
print("[f10] finalist(non-pinned) vs L2-rest    :",paired(Q,np_fin,lambda g:(g.in_l2==True)&(g.is_finalist!=True),"fwd_10_oc"))
print("[f10] L2 vs L1-not-L2                    :",paired(Q,lambda g:g.in_l2==True,lambda g:(g.in_l1==True)&(g.in_l2!=True),"fwd_10_oc"))
for lab,cond in {"<=08-19":P.analysis_date<="2026-08-19",">=08-20":P.analysis_date>="2026-08-20"}.items():
    print(f"[gap {lab}] finalist(np) vs L2-rest:",paired(P[cond],np_fin,lambda g:(g.in_l2==True)&(g.is_finalist!=True)))
out=[]
for dt,g in P[(P.l3_judged==True)&(P.is_pinned!=True)].groupby("analysis_date"):
    g=g[["l3_conviction","gap_c1_o2"]].dropna()
    if len(g)>=8: out.append(g.l3_conviction.rank().corr(g.gap_c1_o2.rank()))
s=pd.Series(out); print("L3 conviction IC (non-pinned) days=%d mean=%.4f t=%.2f pos=%.2f"%(len(s),s.mean(),s.mean()/(s.std()/np.sqrt(len(s))),(s>0).mean()))

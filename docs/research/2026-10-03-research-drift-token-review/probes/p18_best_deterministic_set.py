"""4.5y EOD proxy: best achievable deterministic overnight baseline (needs p16 output long_panel.parquet)."""
import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)

import pandas as pd, numpy as np, warnings
warnings.filterwarnings("ignore")
X=pd.read_parquet(_OUT+"/long_panel.parquet")
X["yr"]=X.trade_date.str[:4]
X["mv_p"]=X.groupby("trade_date").circ_mv.rank(pct=True)
X["to_p"]=X.groupby("trade_date").turnover_rate_f.rank(pct=True)
def rep(name,d):
    s=d.groupby("trade_date").gap_c1_o2.mean(); n=d.groupby("trade_date").size().mean()
    by=d.groupby(["yr","trade_date"]).gap_c1_o2.mean().groupby("yr").mean().round(3).to_dict()
    print(f"{name:58s} abs {s.mean():+.3f}pp t={s.mean()/(s.std()/np.sqrt(len(s))):+.1f} win(day)={(s>0).mean():.2f} n/day={n:.0f} {by}")
ex=(X.pos1<0.7)&(X.pct1<=3)
ex2=ex&(X.pct1>-5)&(X.pos1>0.1)
healthy=(X.ret60>0)&(X.ret60<40)
liq=X.mv_p>=0.2
rep("market (all buyable)",X)
rep("exec line in",X[ex])
rep("exec line in + not-crash(pct1>-5, pos1>0.1)",X[ex2])
rep("healthy",X[healthy])
rep("healthy + exec in",X[healthy&ex])
rep("healthy + exec in(not-crash) + liq",X[healthy&ex2&liq])
rep("healthy + exec(not-crash) + liq + coolest 20% turnover",X[healthy&ex2&liq&(X.to_p<=0.2)])
rep("healthy + exec(not-crash) + liq + hottest 20% turnover",X[healthy&ex2&liq&(X.to_p>=0.8)])
rep("all + exec(not-crash) + liq + coolest 20% turnover",X[ex2&liq&(X.to_p<=0.2)])
rep("hot(ret60>=40) + exec in",X[(X.ret60>=40)&ex])
print("\n-- exec line in/out: day-mean vs pooled (composition effect)")
for name,m in {"in":ex,"out":~ex,"all":X.gap_c1_o2.notna()}.items():
    s=X[m].groupby("trade_date").gap_c1_o2.mean(); print(f"{name:6s} day-mean {s.mean():+.3f}  pooled {X[m].gap_c1_o2.mean():+.3f}  n/day {m.sum()/X.trade_date.nunique():.0f}")

"""4.5y EOD proxy: which 1-2 day leg carries what; read-only on lake/."""
import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)

import pandas as pd, numpy as np, glob, os, warnings
import pyarrow.parquet as pq
warnings.filterwarnings("ignore")
pd.set_option("display.width",250); pd.set_option("display.max_columns",40)
def load(t, cols):
    out=[]
    for f in sorted(glob.glob(f"lake/{t}/*.parquet")):
        names=pq.read_schema(f).names
        d=pd.read_parquet(f, columns=[c for c in cols if c in names]); d["trade_date"]=os.path.basename(f)[:8]
        for c in cols:
            if c not in d: d[c]=np.nan
        out.append(d[cols])
    return pd.concat(out, ignore_index=True)
D=load("daily",["ts_code","trade_date","open","high","low","close","pre_close","pct_chg","amount"])
D["pre_close"]=D.pre_close.fillna(D.close/(1+D.pct_chg/100))
B=load("daily_basic",["ts_code","trade_date","turnover_rate"])
D=D.drop_duplicates(["ts_code","trade_date"]).merge(B.drop_duplicates(["ts_code","trade_date"]),on=["ts_code","trade_date"],how="left")
D=D[~D.ts_code.str.endswith(".BJ")].sort_values(["ts_code","trade_date"]).reset_index(drop=True)
g=D.groupby("ts_code",sort=False)
D["lr"]=np.log1p(D.pct_chg/100)
D["ret60"]=(np.exp(g.lr.transform(lambda s:s.rolling(60,min_periods=40).sum()))-1)*100
D["on"]=(D.open/D.pre_close-1)*100            # overnight into day
D["intra"]=(D.close/D.open-1)*100             # intraday of day
D["on1"]=g.on.shift(-1); D["in1"]=g.intra.shift(-1); D["on2"]=g.on.shift(-2); D["in2"]=g.intra.shift(-2); D["pct1"]=g.pct_chg.shift(-1); D["amt1"]=g.amount.shift(-1); D["amt2"]=g.amount.shift(-2)
D["lim"]=np.where(D.ts_code.str.startswith(("300","301","688","689")),19.7,9.7)
X=D[(D.amount>0)&(D.amt1>0)&(D.amt2>0)&D.on2.notna()&(D.on2.abs()<25)].copy()
X["c1o2"]=X.on2
X["c1c2"]=((1+X.on2/100)*(1+X.in2/100)-1)*100
X["o1o2"]=((1+X.in1/100)*(1+X.on2/100)-1)*100
X["o1c2"]=((1+X.in1/100)*(1+X.on2/100)*(1+X.in2/100)-1)*100
X["o2c2"]=X.in2
X["buy_c1"]=X.pct1<X.lim                      # can buy at T+1 close (not limit-up)
X["buy_o1"]=X.on1<(X.lim-0.2)                 # can buy at T+1 open (not limit-up open)
legs=["c1o2","o2c2","c1c2","o1o2","o1c2"]
def mk(d,col):
    s=d.groupby("trade_date")[col].mean(); return s.mean(), s.mean()/(s.std()/np.sqrt(len(s))), (s>0).mean(), len(s)
print("== equal-weight market mean per leg (pp), buyable at the leg's entry")
for l in legs:
    d=X[X.buy_c1] if l.startswith("c1") or l=="o2c2" else X[X.buy_o1]
    m,t,p,n=mk(d,l); print(f"  {l}: {m:+.3f} t={t:+.1f} pos={p:.2f} days={n}")
def icrow(d,x,l):
    out=[]
    for dt,gg in d.groupby("trade_date"):
        gg=gg[[x,l]].dropna()
        if len(gg)>=300: out.append(gg[x].rank().corr(gg[l].rank()))
    s=pd.Series(out); return round(s.mean(),4), round(s.mean()/(s.std()/np.sqrt(len(s))),1)
print("\n== daily rank IC of T-day features with each leg")
for x in ["turnover_rate","ret60","pct_chg"]:
    r={l:icrow(X[X.buy_c1] if l.startswith('c1') or l=='o2c2' else X[X.buy_o1],x,l) for l in legs}
    print(f"  {x:14s}", r)
print("\n== bucket mean minus market (pp/day) per leg")
for name,mask in {"healthy(0<ret60<40)":(X.ret60>0)&(X.ret60<40),"hot(ret60>=40)":X.ret60>=40,"knife(ret60<-20)":X.ret60<-20}.items():
    row={}
    for l in legs:
        d=X[X.buy_c1] if l.startswith("c1") or l=="o2c2" else X[X.buy_o1]
        a=d[mask.reindex(d.index)].groupby("trade_date")[l].mean(); b=d.groupby("trade_date")[l].mean()
        e=(a-b).dropna(); row[l]=(round(e.mean(),3), round(e.mean()/(e.std()/np.sqrt(len(e))),1))
    print(f"  {name:20s}",row)
print("\n== absolute mean (pp/day) healthy bucket per leg, by year")
H=X[(X.ret60>0)&(X.ret60<40)].copy(); H["yr"]=H.trade_date.str[:4]
for l in legs:
    d=H[H.buy_c1] if l.startswith("c1") or l=="o2c2" else H[H.buy_o1]
    s=d.groupby(["yr","trade_date"])[l].mean().groupby("yr").mean().round(3).to_dict(); print(f"  {l}:",s)

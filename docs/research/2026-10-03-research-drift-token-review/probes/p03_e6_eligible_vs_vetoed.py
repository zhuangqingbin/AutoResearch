import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import pandas as pd, numpy as np, warnings
warnings.filterwarnings("ignore")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60); pd.set_option("display.max_rows", 400)
df = pd.read_csv("reports_claude/scan/_ledger/recommendations.csv", dtype={"code": str})
df["g"]=df.gap_c1_o2*100; df["rm"]=df.rel_gap_market*100
x=df[(df["mode"].notna())&(df.role!="BUY")&(df.role!="pinned")]
def agg(d,by):
    r=d.groupby(by,dropna=False).agg(n=("g","size"),days=("analysis_date","nunique"),gap=("g","mean"),rel=("rm","mean"),win=("g",lambda s:(s>0).mean()),f10=("fwd_10_oc",lambda s:s.mean()*100)).round(3)
    return r
print("rows with mode:",len(x), x.analysis_date.min(), x.analysis_date.max())
print(agg(x,["mode","e6_eligible"]))
print(agg(x[x["mode"]=="active"],"e6_eligible"))
# day-paired: eligible mean minus ineligible mean per day
a=x[x["mode"]=="active"]
p=a.groupby(["analysis_date","e6_eligible"]).rm.mean().unstack()
p["diff"]=p[True]-p[False]
d=p["diff"].dropna()
print("paired days:",len(d),"mean diff(pp)",round(d.mean(),3),"t",round(d.mean()/(d.std()/len(d)**0.5),2),"pos share",round((d>0).mean(),2))
# costs: round trip cost assumption
b=df[(df.e6_buy==True)&(df["mode"]=="active")]
print("\nactive BUY gross mean %.3f ; net of 0.15pp cost %.3f ; n=%d"%(b.g.mean(), b.g.mean()-0.15, len(b)))
print("exec_ok among BUY:", b.exec_ok.value_counts().to_dict())
print(b[["analysis_date","name","g","rm","exec_ok","t1_pos_in_range","t1_pct_chg"]].to_string(index=False))
# market overnight baseline
m=pd.read_csv("reports_claude/scan/_ledger/views/market.csv"); print(m.columns.tolist()); print(m.tail(3).to_string())

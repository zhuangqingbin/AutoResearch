import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import re, glob, os
import pandas as pd
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60); pd.set_option("display.max_rows", 500)
rows=[]
for f in sorted(glob.glob("reports_claude/scan/2026*/details/*.md")):
    run=f.split("/")[2]
    if run < "20260811": continue
    t=open(f,encoding="utf-8").read()
    parts=re.split(r"\n## 🕵️", t, maxsplit=1)
    if len(parts)<2: continue
    intel=parts[1]
    t0=re.search(r"T0面\s*=\s*([^｜|\n]+)", intel)
    ev=[]
    for line in intel.split("\n"):
        if line.startswith("|") and re.search(r"\|\s*(T0|24h|催化挂|背景)\s*\|", line):
            cells=[c.strip() for c in line.strip().strip("|").split("|")]
            win=cells[1]; 
            try: sc=float(cells[-1].replace("−","-").replace("+",""))
            except: sc=None
            ev.append((win,sc))
    n_t0=sum(1 for w,s in ev if w=="T0"); n_24=sum(1 for w,s in ev if w=="24h")
    hot=[(w,s) for w,s in ev if w in ("T0","24h") and s not in (None,0.0)]
    neg=[(w,s) for w,s in ev if w in ("T0","24h") and s is not None and s<0]
    pos=[(w,s) for w,s in ev if w in ("T0","24h") and s is not None and s>0]
    wq=re.search(r"网查\s*(\d+)\s*条", intel)
    rows.append(dict(run=run,name=os.path.basename(f)[:-3],t0=(t0.group(1).strip() if t0 else None),n_ev=len(ev),n_t0=n_t0,n_24=n_24,hot=len(hot),neg=len(neg),pos=len(pos),webq=int(wq.group(1)) if wq else None))
df=pd.DataFrame(rows)
print("cards with intel:",len(df))
print(df.t0.fillna("缺").str.slice(0,8).value_counts().head(8).to_string())
print("\nshare with any nonzero T0/24h event:", round((df.hot>0).mean(),3), " any negative T0/24h:", round((df.neg>0).mean(),3), " any positive:", round((df.pos>0).mean(),3))
print("mean rows: events %.1f, T0 %.1f, 24h %.1f ; median webq %s"%(df.n_ev.mean(),df.n_t0.mean(),df.n_24.mean(),df.webq.median()))
g=df.groupby("run").agg(n=("hot","size"),hot=("hot",lambda s:(s>0).mean()),neg=("neg",lambda s:(s>0).mean()),pos=("pos",lambda s:(s>0).mean()),webq=("webq","median")).round(2)
print(g.tail(14).to_string())
df.to_csv(_OUT+"/intel.csv",index=False)
# join with card decisions
c=pd.read_csv(_OUT+"/cards.csv")
m=df.merge(c,on=["run","name"])
m["hotflag"]=m.hot>0; m["negflag"]=m.neg>0
print("\nrating by intel-negative flag"); print(pd.crosstab(m.negflag,m.rating,normalize="index").round(2).to_string())
print("\nearly-stop share by any-hot flag"); print(m.groupby("hotflag").early.agg(["mean","size"]).round(2).to_string())
print("\nstop reason by negflag"); print(pd.crosstab(m.negflag,m.reason.fillna("满卡")).to_string())

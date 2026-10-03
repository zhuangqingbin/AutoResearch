import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import re, glob, os, sys
import pandas as pd
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60); pd.set_option("display.max_rows", 500)
def num(s):
    s=s.strip().replace("*","").replace("$","")
    if s in ("","—","-"): return 0.0
    m=re.match(r"([\d.]+)([kM]?)",s)
    if not m: return 0.0
    v=float(m.group(1)); u=m.group(2)
    return v*(1e3 if u=="k" else 1e6 if u=="M" else 1)
rows=[]
files=sorted(glob.glob("reports_claude/scan/*/token_usage.md"))
for f in files:
    run=f.split("/")[2]
    txt=open(f,encoding="utf-8").read()
    hdr=txt.split("\n")[2:5]
    for line in txt.split("\n"):
        if line.startswith("| main") or line.startswith("| subagent"):
            c=[x.strip() for x in line.strip().strip("|").split("|")]
            if len(c)<12: continue
            rows.append(dict(run=run,role=c[0],agent=c[1],model=c[2],effort=c[3],status=c[4],msgs=num(c[5]),out=num(c[6]),cread=num(c[7]),w5=num(c[8]),w1h=num(c[9]),raw=num(c[10]),cost=num(c[11])))
df=pd.DataFrame(rows)
df["weighted"]=df.cread*0.1+df.w5*1.25+df.w1h*2+df.raw
print("files:",len(files)); print([os.path.basename(os.path.dirname(f)) for f in files])
tot=df.groupby("run").agg(n=("cost","size"),cost=("cost","sum"),out=("out","sum"),weighted=("weighted","sum")).round(1)
print(tot.to_string())
g=df.groupby(["run","agent","model","effort"]).agg(n=("cost","size"),cost=("cost","sum"),out=("out","sum"),out_med=("out","median"),msgs_med=("msgs","median"),weighted=("weighted","sum"),w5_med=("w5","median"),cread_med=("cread","median")).round(1)
for run in sorted(df.run.unique())[-8:]:
    print("\n=====",run); print(g.loc[run].sort_values("cost",ascending=False).to_string())

print("\n\n######## per-run role summary (research roles only)")
r=df[df.agent.isin(["l4-card","l4-intel","l3-rank","macro-brief","sector-brief","(主会话)"])]
piv=r.groupby(["run","agent","model","effort"]).agg(n=("cost","size"),out_med=("out","median"),cost=("cost","sum"),msgs_med=("msgs","median")).round(1).reset_index()
for ag in ["l4-card","l4-intel","l3-rank","macro-brief","sector-brief","(主会话)"]:
    print("\n---",ag); print(piv[piv.agent==ag].drop(columns=["agent"]).to_string(index=False))

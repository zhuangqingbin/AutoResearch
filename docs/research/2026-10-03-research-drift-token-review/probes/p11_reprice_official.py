"""Reprice token_usage tables with official list prices fetched 2026-10-03 (Opus 5.5 $4/$20, read $0.20; Sonnet 5/5.5 $2/$10)."""
import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import re, glob, os
import pandas as pd
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60); pd.set_option("display.max_rows", 500)
S=_OUT+"/"
# official list prices fetched 2026-10-03 (input, out, w5, w1h, read) USD/MTok
P={"claude-opus-5-5":(4,20,5,8,0.20),"claude-opus-5":(5,25,6.25,10,0.50),"claude-sonnet-5-5":(2,10,2.5,4,0.20),"claude-sonnet-5":(2,10,2.5,4,0.20),"claude-fable-5-1":(10,50,12.5,20,0.25),"claude-haiku-4-5":(1,5,1.25,2,0.10)}
def num(s):
    s=s.strip().replace("*","").replace("$","")
    m=re.match(r"([\d.]+)([kM]?)",s)
    return float(m.group(1))*(1e3 if m.group(2)=="k" else 1e6 if m.group(2)=="M" else 1) if m else 0.0
rows=[]
files={r:f"reports_claude/scan/{r}/token_usage.md" for r in ["20260911-0912_1248","20260917-0917_2152","20260928-0928_2148","20260929-0929_2213"]}
if _os.path.exists(S+"usage_1002.md"): files["20261002(session_v1·含调试)"]=S+"usage_1002.md"  # 由 usage_harvest --session 05ed57b4-… --out 生成
for run,f in files.items():
    for line in open(f,encoding="utf-8"):
        if line.startswith("| main") or line.startswith("| subagent"):
            c=[x.strip() for x in line.strip().strip("|").split("|")]
            model=c[2]; p=P.get(model)
            out,cread,w5,w1h,raw,cost=num(c[6]),num(c[7]),num(c[8]),num(c[9]),num(c[10]),num(c[11])
            off=(raw*p[0]+out*p[1]+w5*p[2]+w1h*p[3]+cread*p[4])/1e6 if p else float("nan")
            rows.append(dict(run=run,agent=c[1],model=model,out=out,inp_equiv=raw+w5*1.25+w1h*2+cread*0.1,repo=cost,official=off,out_usd=out*p[1]/1e6 if p else 0))
df=pd.DataFrame(rows)
t=df.groupby("run").agg(agents=("repo","size"),out_tok=("out","sum"),repo_usd=("repo","sum"),official_usd=("official","sum"),out_usd=("out_usd","sum")).round(1)
t["out_share"]=(t.out_usd/t.official_usd).round(2)
print(t.to_string())
g=df.groupby(["run","agent"]).agg(n=("repo","size"),out_tok=("out","sum"),repo=("repo","sum"),official=("official","sum"),out_usd=("out_usd","sum")).round(1)
for run in files:
    print("\n==",run); print(g.loc[run].sort_values("official",ascending=False).to_string())

import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import re, glob, os, json
import pandas as pd
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60); pd.set_option("display.max_rows", 500)
rows=[]
for f in sorted(glob.glob("reports_claude/scan/2026*/details/*.md")):
    run=f.split("/")[2]
    t=open(f,encoding="utf-8").read()
    parts=re.split(r"\n## 🕵️", t, maxsplit=1)
    body=parts[0]; intel=parts[1] if len(parts)>1 else ""
    m=re.search(r"\*\*Rating\*\*[:：]\s*\**([A-Za-z—]+)", body)
    rating=m.group(1) if m else None
    es=re.search(r"\*\*早停\*\*[:：]\s*停于\s*(P\d)\s*[｜|]\s*停因[:：]\s*(\S+)", body)
    entry=re.search(r"\*\*入场\*\*[:：]\s*(允许|禁止|条件)", body)
    hdr=body.split("\n")[0]
    early="早停" in hdr
    nums=re.findall(r"[+\-−]?\d+(?:\.\d+)?%?", body)
    uniq=len(set(nums)); tot=len(nums)
    wq=re.search(r"网查\s*(\d+)\s*条", intel)
    rows.append(dict(run=run,name=os.path.basename(f)[:-3],body=len(body.encode()),intel=len(intel.encode()),early=early,phase=es.group(1) if es else None,reason=es.group(2) if es else None,rating=rating,entry=entry.group(1) if entry else None,nums=tot,uniq=uniq,webq=int(wq.group(1)) if wq else None,pinned=("持仓" in body[:400] or "📌" in body[:400])))
df=pd.DataFrame(rows)
df["date"]=df.run.str[:8]
g=df.groupby("run").agg(n=("body","size"),body_med=("body","median"),body_sum=("body","sum"),intel_med=("intel","median"),early=("early","mean"),webq_med=("webq","median"),rep=("nums",lambda s: s.sum()),uniq=("uniq","sum"))
g["rep_ratio"]=(g.rep/g.uniq).round(2)
print(g.tail(22).to_string())
print()
print(pd.crosstab(df.run, df.rating).tail(16).to_string())
print()
print(pd.crosstab(df.run, df.entry.fillna("无入场行")).tail(12).to_string())
print()
print(pd.crosstab(df.run, df.reason.fillna("(满卡/无)")).tail(14).to_string())
df.to_csv(_OUT+"/cards.csv",index=False)

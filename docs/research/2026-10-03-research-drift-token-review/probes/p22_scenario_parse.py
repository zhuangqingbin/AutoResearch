"""满卡「三档情景」行能否机读计分:82 张满卡里 78 张有情景行,固定格式能解析的只有 10 张(仪器缺口);能对上账本的 8 张的概率几乎是固定先验。"""
import re, glob, os, warnings
import pandas as pd, numpy as np
warnings.filterwarnings("ignore")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60); pd.set_option("display.max_rows", 200)
def f(x): return float(x.replace("−","-").replace("+","").replace("–","-"))
rows=[]; nfull=0; nline=0
for path in sorted(glob.glob("reports_claude/scan/2026*/details/*.md")):
    run=path.split("/")[2]
    if run[:8] < "20260805": continue
    t=open(path,encoding="utf-8").read()
    body=re.split(r"\n## 🕵️", t, maxsplit=1)[0]
    if "早停" in body.split("\n")[0]: continue
    nfull+=1
    m=re.search(r"\*\*三档情景[^\n]*\n?[^\n]*", body)
    if not m: continue
    line=m.group(0); nline+=1
    sc={}
    for k in ["Bull","Base","Bear"]:
        mm=re.search(k+r"[^0-9+\-−]{0,12}(\d{1,3})\s*%[^+\-−\d]{0,10}([+\-−]\s?\d+(?:\.\d+)?)\s*%", line)
        if mm: sc[k]=(float(mm.group(1))/100, f(mm.group(2).replace(" ","")))
    ev=re.search(r"EV[^+\-−\d]{0,12}([+\-−]\s?\d+(?:\.\d+)?)\s*%", line)
    code=re.search(r"^# 决策卡 — (\d{6})", body, re.M)
    if len(sc)==3:
        rows.append(dict(run_id=run,code=code.group(1) if code else None,pb=sc["Bull"][0],rb=sc["Bull"][1],pm=sc["Base"][0],rm_=sc["Base"][1],pd_=sc["Bear"][0],rd=sc["Bear"][1],ev=f(ev.group(1).replace(" ","")) if ev else None))
print("full cards since 08-05:",nfull,"with scenario line:",nline,"parsed 3-scenario:",len(rows))
s=pd.DataFrame(rows)
L=pd.read_csv("reports_claude/scan/_ledger/recommendations.csv",dtype={"code":str})
L=L[L.role!="BUY"].drop_duplicates(["run_id","code"])
m=s.merge(L,on=["run_id","code"])
m["g"]=m.gap_c1_o2*100
m["psum"]=m.pb+m.pm+m.pd_
m["ev_calc"]=m.pb*m.rb+m.pm*m.rm_+m.pd_*m.rd
print("joined with outcomes:",len(m),"days",m.analysis_date.nunique(),"pinned share",(m.role=="pinned").mean().round(2))
print(m[["pb","rb","pm","rm_","pd_","rd","ev","ev_calc","g"]].describe().round(3).to_string())
print("prob sums != 1:",(abs(m.psum-1)>0.011).sum())
# coverage: realized gap within [bear, bull] levels
inside=((m.g>=m.rd)&(m.g<=m.rb)).mean()
below=(m.g<m.rd).mean(); above=(m.g>m.rb).mean()
print("realized gap inside [Bear,Bull] levels: %.2f ; below Bear level: %.2f ; above Bull level: %.2f"%(inside,below,above))
print("mean |Bull level|, |Bear level| vs realized |gap| mean/90pct:", m.rb.mean().round(2), m.rd.mean().round(2), m.g.abs().mean().round(2), m.g.abs().quantile(0.9).round(2))
# which scenario nearest
near=np.argmin(np.abs(np.vstack([m.g-m.rb,m.g-m.rm_,m.g-m.rd])),axis=0)
print("nearest scenario realized share Bull/Base/Bear:", [(near==i).mean().round(2) for i in range(3)], " stated mean probs:", m.pb.mean().round(2), m.pm.mean().round(2), m.pd_.mean().round(2))
print("corr(EV stated, realized gap):", m[["ev","g"]].corr().iloc[0,1].round(3), " n=",m.ev.notna().sum())
print("EV>0 share:",(m.ev>0).mean().round(2)," realized gap>0 share:",(m.g>0).mean().round(2))
print(m.groupby(m.ev>0).g.agg(["mean","size"]).round(3))

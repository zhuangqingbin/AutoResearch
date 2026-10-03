"""「L3 论点裁决」里兑现机制行的 ✓/✗。
⚠️ 语义坑:✓ 的极性取决于前提本身——08-05 以来 41 个 ✓ 里 40 个是「确认『无兑现机制』」(空头前提被证实)。
本脚本前半段把 ✓ 当成「机制成立」算出的收益差是伪信号,保留仅为复现「该字段不可机读」这一结论;末段逐行列出 ✓ 行原文。"""
import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import re, glob, os, warnings
import pandas as pd, numpy as np
warnings.filterwarnings("ignore")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60); pd.set_option("display.max_rows", 500)
rows=[]
for f in sorted(glob.glob("reports_claude/scan/2026*/details/*.md")):
    run=f.split("/")[2]
    t=open(f,encoding="utf-8").read()
    body=re.split(r"\n## 🕵️", t, maxsplit=1)[0]
    m=re.search(r"^# 决策卡 — (\d{6})", body, re.M)
    code=m.group(1) if m else None
    mech=None; n_ok=n_bad=0
    sec=re.search(r"## L3 论点裁决(.*?)(?:\n## |\Z)", body, re.S)
    if sec:
        for line in sec.group(1).split("\n"):
            if not line.startswith("|"): continue
            cells=[c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells)<3: continue
            v=cells[1]
            ok = "✓" in v and "✗" not in v
            bad = "✗" in v and "✓" not in v
            if ok: n_ok+=1
            if bad: n_bad+=1
            if "兑现机制" in cells[0]:
                mech = "ok" if ok else "bad" if bad else "mixed"
    rows.append(dict(run_id=run,code=code,mech=mech,n_ok=n_ok,n_bad=n_bad))
c=pd.DataFrame(rows)
print(c.mech.value_counts(dropna=False).to_string())
L=pd.read_csv("reports_claude/scan/_ledger/recommendations.csv",dtype={"code":str})
L=L[L.role.isin(["finalist","composite_seat","sector_seat","pinned"])]
m=c.merge(L,on=["run_id","code"],how="inner")
m["g"]=m.gap_c1_o2*100; m["rm"]=m.rel_gap_market*100; m["f10"]=m.fwd_10_oc*100
print("joined:",len(m),"dates",m.analysis_date.nunique(), m.analysis_date.min(), m.analysis_date.max())
def agg(d,by):
    return d.groupby(by,dropna=False).agg(n=("g","size"),days=("analysis_date","nunique"),gap=("g","mean"),rel=("rm","mean"),win=("g",lambda s:(s>0).mean()),relwin=("rm",lambda s:(s>0).mean()),f10=("f10","mean")).round(3)
print(agg(m,"mech"))
print(agg(m[m.role!="pinned"],"mech"))
# day-paired
x=m[(m.role!="pinned")&m.mech.isin(["ok","bad"])]
p=x.groupby(["analysis_date","mech"]).rm.mean().unstack().dropna()
d=p["ok"]-p["bad"]
print("paired days",len(d),"mean diff pp",round(d.mean(),3),"t",round(d.mean()/(d.std()/len(d)**0.5),2),"pos",round((d>0).mean(),2))
m["share_ok"]=m.n_ok/(m.n_ok+m.n_bad).replace(0,np.nan)
m["sb"]=pd.cut(m.share_ok,[-0.01,0.34,0.67,1.0])
print(agg(m[m.role!="pinned"],"sb"))

print("\n=== non-pinned mechanism-ok rows")
o=m[(m.role!="pinned")&(m.mech=="ok")][["analysis_date","code","name","rating","early_stop_reason","conviction","e6_eligible","e6_buy","exec_ok","t1_pos_in_range","g","rm","f10"]]
print(o.round(3).to_string(index=False))
print("\n=== by exec_ok x mech (non-pinned, since 08-07 where exec_ok known)")
z=m[(m.role!="pinned")&m.mech.isin(["ok","bad"])&m.exec_ok.notna()]
print(agg(z,["mech","exec_ok"]))
print("\n=== era split of mech=bad/ok (non-pinned)")
m["era"]=np.where(m.analysis_date<"2026-08-05","pre0805(旧尺期)","post0805(隔夜尺期)")
print(agg(m[(m.role!="pinned")&m.mech.isin(["ok","bad"])],["era","mech"]))

print("\n=== 08-05 以来全部兑现机制行的极性核对")
import re as _re, glob as _glob, os as _o
tot=bad=0; ok=[]
for f in sorted(_glob.glob("reports_claude/scan/2026*/details/*.md")):
    run=f.split("/")[2]
    if run[:8] < "20260805": continue
    body=_re.split(r"\n## 🕵️", open(f,encoding="utf-8").read(), maxsplit=1)[0]
    sec=_re.search(r"## L3 论点裁决(.*?)(?:\n## |\Z)", body, _re.S)
    if not sec: continue
    for line in sec.group(1).split("\n"):
        if not line.startswith("|"): continue
        cells=[c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells)<3 or "兑现机制" not in cells[0]: continue
        tot+=1; v=cells[1]
        if "✓" in v and "✗" not in v: ok.append((run[:8],_o.path.basename(f)[:-3],cells[0][:60],v[:20]))
        elif "✗" in v: bad+=1
print("rows",tot,"✗",bad,"✓",len(ok))
for r in ok: print(r)

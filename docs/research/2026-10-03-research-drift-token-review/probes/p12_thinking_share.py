import os as _os
_OUT = _os.environ.get("PROBE_OUT", "context_claude/development/20261003-research-review"); _os.makedirs(_OUT, exist_ok=True)
import json,glob,os,collections,sys,statistics
base=os.path.expanduser("~/.claude/projects/-Users-qingbin-zhuang-Personal-TradingAgents/")
def analyze(f):
    seen={}; kinds=collections.Counter(); chars=collections.Counter(); model=None; tools=collections.Counter()
    for line in open(f,encoding="utf-8"):
        try: o=json.loads(line)
        except: continue
        m=o.get("message") or {}
        if o.get("type")=="assistant":
            mid=m.get("id"); u=m.get("usage") or {}
            seen[mid]=u.get("output_tokens",0)
            model=m.get("model",model)
            for b in m.get("content",[]) or []:
                t=b.get("type"); kinds[t]+=1
                if t=="thinking": chars[t]+=len(b.get("thinking") or "")
                elif t=="text": chars[t]+=len(b.get("text") or "")
                elif t=="tool_use":
                    tools[b.get("name")]+=1; chars["tool_use"]+=len(json.dumps(b.get("input"),ensure_ascii=False))
    meta={}
    mf=f.replace(".jsonl",".meta.json")
    if os.path.exists(mf):
        try: meta=json.load(open(mf))
        except: pass
    return dict(agent=meta.get("agentType") or meta.get("agent_type") or "?",model=model,out=sum(seen.values()),msgs=len(seen),chars=dict(chars),tools=dict(tools))
for sess,label in [("0836165b-2b2a-4cf8-baac-4aae7e446ea2","09-29 opus-5-5"),(sys.argv[1] if len(sys.argv)>1 else "", "older")]:
    if not sess: continue
    files=glob.glob(base+sess+"/subagents/**/*.jsonl",recursive=True)
    res=[analyze(f) for f in files]
    by=collections.defaultdict(list)
    for r in res: by[(r["agent"],r["model"])].append(r)
    print("=====",label,sess,len(files))
    for k,v in sorted(by.items(), key=lambda kv:-sum(r["out"] for r in kv[1])):
        if k[0] in ("general-purpose","?") and len(v)>20: 
            print(k,len(v),"out_sum",sum(r["out"] for r in v)); continue
        outs=[r["out"] for r in v]
        th=sum(r["chars"].get("thinking",0) for r in v); tx=sum(r["chars"].get("text",0) for r in v); tu=sum(r["chars"].get("tool_use",0) for r in v)
        tl=collections.Counter()
        for r in v: tl.update(r["tools"])
        print(k,"n",len(v),"out_med",int(statistics.median(outs)),"out_sum",sum(outs),"| chars thinking",th,"text",tx,"tool_input",tu,"| tools",dict(tl))

"""Claude Code version + resolved subagent models per scan session (machine-specific: reads ~/.claude/projects transcripts)."""
import json, os, glob, collections
base=os.path.expanduser("~/.claude/projects/-Users-qingbin-zhuang-Personal-TradingAgents/")
SESS={"692bccef-0f1a-47db-b525-122169ca42a3":"09-27 run (analysis 09-24)","f0b0f21e-ecf5-46ec-b561-5cf0f7d0e086":"09-28 run","0836165b-2b2a-4cf8-baac-4aae7e446ea2":"09-29 run","05ed57b4-bb8a-42cb-8d8f-64b0265c23fc":"10-02 session_v1 runs"}
for sid,label in SESS.items():
    f=base+sid+".jsonl"
    if not os.path.exists(f): print(label,"transcript missing"); continue
    vers=collections.Counter()
    with open(f,encoding="utf-8") as fh:
        for i,line in enumerate(fh):
            try: o=json.loads(line)
            except Exception: continue
            if o.get("version"): vers[o["version"]]+=1
            if i>4000: break
    models=collections.Counter()
    for sf in glob.glob(base+sid+"/subagents/**/*.jsonl",recursive=True):
        agent="?"; mf=sf.replace(".jsonl",".meta.json")
        if os.path.exists(mf):
            try: agent=json.load(open(mf)).get("agentType","?")
            except Exception: pass
        model=None
        for line in open(sf,encoding="utf-8"):
            try: o=json.loads(line)
            except Exception: continue
            if o.get("type")=="assistant": model=(o.get("message") or {}).get("model",model)
        if agent in ("l4-card","l4-intel","l3-rank","macro-brief","sector-brief"): models[(agent,model)]+=1
    print(label,"| Claude Code",dict(vers),"|",dict(models))

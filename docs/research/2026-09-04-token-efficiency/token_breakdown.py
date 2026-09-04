"""Second cut: (1) dev vs scan split, (2) OMC tail-loop waste across the month,
(3) which tools fill main-session context, (4) top subagents in the biggest dev sessions."""
from __future__ import annotations
import json, os, glob, statistics, datetime as dt
from collections import defaultdict

PROJ = os.path.expanduser("~/.claude/projects/-Users-qingbin-zhuang-Personal-TradingAgents")
SINCE = dt.datetime(2026, 8, 5)


def iter_msgs(path):
    with open(path, "rb") as f:
        for line in f:
            try:
                yield json.loads(line)
            except Exception:
                continue


def weighted(u):
    i = u.get("input_tokens", 0) or 0
    cr = u.get("cache_read_input_tokens", 0) or 0
    cw = u.get("cache_creation_input_tokens", 0) or 0
    cc = u.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens", 0) or 0
    w1 = cc.get("ephemeral_1h_input_tokens", 0) or 0
    if not (w5 or w1):
        w5 = cw
    return i + 0.1 * cr + 1.25 * w5 + 2.0 * w1, i + cr + w5 + w1, u.get("output_tokens", 0) or 0


def analyze_agent(path):
    """Return dict: weighted total, calls, tail_loop_calls, tail_loop_weighted, agent_type guess."""
    msgs = []  # sequence of (type, message-id, usage, has_tool_result, has_tool_use)
    seen = {}
    order = []
    for rec in iter_msgs(path):
        typ = rec.get("type")
        msg = rec.get("message") or {}
        if typ == "assistant":
            mid = msg.get("id") or rec.get("uuid")
            u = msg.get("usage") or {}
            has_tu = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in (msg.get("content") or []))
            if mid not in seen:
                order.append(("a", mid, has_tu))
            if u:
                seen[mid] = u
        elif typ == "user":
            content = msg.get("content")
            has_tr = isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
            order.append(("u", None, has_tr))
    tot_w = tot_ctx = tot_out = 0.0
    for u in seen.values():
        w, c, o = weighted(u)
        tot_w += w; tot_ctx += c; tot_out += o
    # tail loop: consecutive assistant messages at the very end with no user message between
    tail = 0
    for kind, mid, _ in reversed(order):
        if kind == "a":
            tail += 1
        else:
            break
    # OMC loop: those tail assistants beyond the first final answer
    loop_calls = max(0, tail - 1)
    loop_w = 0.0
    if loop_calls:
        tail_ids = [mid for kind, mid, _ in order if kind == "a"][-loop_calls:]
        for mid in tail_ids:
            if mid in seen:
                loop_w += weighted(seen[mid])[0]
    return dict(w=tot_w, calls=len(seen), loop_calls=loop_calls, loop_w=loop_w, out=tot_out)


def main():
    sessions = []
    for path in glob.glob(os.path.join(PROJ, "*.jsonl")):
        mtime = dt.datetime.fromtimestamp(os.path.getmtime(path))
        if mtime < SINCE:
            continue
        sid = os.path.basename(path)[:-6]
        # main session usage + tool breakdown
        seen = {}
        tool_in = defaultdict(int)
        tool_out = defaultdict(int)
        id2tool = {}
        for rec in iter_msgs(path):
            typ = rec.get("type")
            msg = rec.get("message") or {}
            if typ == "assistant":
                mid = msg.get("id") or rec.get("uuid")
                if msg.get("usage"):
                    seen[mid] = msg["usage"]
                for b in msg.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        name = b.get("name", "?")
                        id2tool[b.get("id")] = name
                        tool_in[name] += len(json.dumps(b.get("input", {}), ensure_ascii=False))
            elif typ == "user":
                content = msg.get("content")
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            name = id2tool.get(b.get("tool_use_id"), "?")
                            c = b.get("content")
                            n = len(c) if isinstance(c, str) else sum(len(x.get("text", "")) for x in c if isinstance(x, dict)) if isinstance(c, list) else 0
                            tool_out[name] += n
        if not seen:
            continue
        main_w = sum(weighted(u)[0] for u in seen.values())
        ctxs = [weighted(u)[1] for u in seen.values()]
        subs = []
        for sp in glob.glob(os.path.join(PROJ, sid, "subagents", "**", "agent-*.jsonl"), recursive=True):
            a = analyze_agent(sp)
            a["path"] = sp
            subs.append(a)
        sub_w = sum(a["w"] for a in subs)
        loop_w = sum(a["loop_w"] for a in subs)
        loop_calls = sum(a["loop_calls"] for a in subs)
        med_calls = statistics.median([a["calls"] for a in subs]) if subs else 0
        kind = "scan" if (len(subs) >= 60 and med_calls <= 12) else ("dev" if len(seen) >= 30 else "small")
        sessions.append(dict(sid=sid, short=sid[:8], mtime=mtime, kind=kind, turns=len(seen),
                             ctx_med=statistics.median(ctxs), main_w=main_w, sub_w=sub_w,
                             n_sub=len(subs), loop_w=loop_w, loop_calls=loop_calls,
                             tool_in=dict(tool_in), tool_out=dict(tool_out), subs=subs))
    # (1) split
    agg = defaultdict(lambda: defaultdict(float))
    for s in sessions:
        a = agg[s["kind"]]
        a["n"] += 1; a["main_w"] += s["main_w"]; a["sub_w"] += s["sub_w"]; a["loop_w"] += s["loop_w"]; a["turns"] += s["turns"]
    print("=== (1) dev vs scan split (weighted M) ===")
    tot = sum(a["main_w"] + a["sub_w"] for a in agg.values())
    for k, a in sorted(agg.items(), key=lambda kv: -(kv[1]["main_w"] + kv[1]["sub_w"])):
        t = a["main_w"] + a["sub_w"]
        print(f"{k:6} n={a['n']:3.0f} turns={a['turns']:6.0f} main={a['main_w']/1e6:7.1f}M sub={a['sub_w']/1e6:7.1f}M total={t/1e6:7.1f}M ({100*t/tot:4.1f}%)  omc_loop_waste={a['loop_w']/1e6:6.1f}M")
    # (2) OMC loop across month
    LW = sum(s["loop_w"] for s in sessions); LC = sum(s["loop_calls"] for s in sessions)
    SW = sum(s["sub_w"] for s in sessions)
    print(f"\n=== (2) OMC tail-loop waste: {LC} extra calls, {LW/1e6:.1f}M weighted = {100*LW/max(SW,1):.1f}% of all subagent weighted ===")
    # per-kind share
    for k in ("scan", "dev"):
        sw = sum(s["sub_w"] for s in sessions if s["kind"] == k); lw = sum(s["loop_w"] for s in sessions if s["kind"] == k)
        print(f"   {k}: loop {lw/1e6:.1f}M / sub {sw/1e6:.1f}M = {100*lw/max(sw,1):.1f}%")
    # (3) tools filling main context, dev sessions only
    TI = defaultdict(int); TO = defaultdict(int)
    for s in sessions:
        if s["kind"] != "dev":
            continue
        for k, v in s["tool_in"].items(): TI[k] += v
        for k, v in s["tool_out"].items(): TO[k] += v
    ti = sum(TI.values()) or 1; to = sum(TO.values()) or 1
    print(f"\n=== (3) dev sessions: chars by tool — tool_use INPUT total {ti/1e6:.1f}M, tool RESULT total {to/1e6:.1f}M ===")
    print("   input side:")
    for k, v in sorted(TI.items(), key=lambda kv: -kv[1])[:8]:
        print(f"     {k:22} {v/1e6:6.2f}M ({100*v/ti:4.1f}%)")
    print("   result side:")
    for k, v in sorted(TO.items(), key=lambda kv: -kv[1])[:8]:
        print(f"     {k:22} {v/1e6:6.2f}M ({100*v/to:4.1f}%)")
    # (4) top subagents in the 3 biggest dev sessions
    print("\n=== (4) biggest dev sessions: subagent cost distribution ===")
    for s in sorted([s for s in sessions if s["kind"] == "dev"], key=lambda s: -(s["main_w"] + s["sub_w"]))[:4]:
        subs = sorted(s["subs"], key=lambda a: -a["w"])
        ws = [a["w"] for a in subs]
        top5 = sum(ws[:5]);
        print(f"{s['mtime'].strftime('%m-%d')} {s['short']} n_sub={len(subs)} sub_w={s['sub_w']/1e6:.1f}M  top5 share={100*top5/max(s['sub_w'],1):.0f}%  "
              f"calls med={statistics.median([a['calls'] for a in subs]) if subs else 0:.0f} max={max((a['calls'] for a in subs), default=0)}  "
              f"agent_w med={statistics.median(ws)/1e6 if ws else 0:.2f}M max={max(ws, default=0)/1e6:.1f}M")
        for a in subs[:3]:
            print(f"      {a['w']/1e6:5.1f}M calls={a['calls']:3d} loop={a['loop_calls']} out={a['out']/1e3:.0f}k  {os.path.relpath(a['path'], PROJ)[:90]}")
    # (5) how much main_w sits in turns with ctx>300k
    big = sum(1 for s in sessions for _ in [0] if s["ctx_med"] > 300e3)
    print(f"\n=== (5) sessions with median ctx > 300k: {big}; their main_w share = "
          f"{100*sum(s['main_w'] for s in sessions if s['ctx_med']>300e3)/max(sum(s['main_w'] for s in sessions),1):.0f}% ===")


if __name__ == "__main__":
    main()

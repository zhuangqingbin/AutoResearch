"""30-day token panorama over Claude Code transcripts for this project.

Per session: main-session usage (deduped by message.id), subagent usage, turns,
context size per turn, and a rough char breakdown of what fills the context.
Weighted input follows the project's own convention: cache_read x0.1,
5m write x1.25, 1h write x2.0, raw input x1.  Output priced separately (x5 of input).
"""
from __future__ import annotations
import json, os, sys, glob, statistics, datetime as dt
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


def usage_of_file(path):
    """Return (usage dict, turns, ctx_sizes, chars_by_kind, first_ts, last_ts, model)."""
    seen = {}
    ctx = []
    chars = defaultdict(int)
    first_ts = last_ts = None
    model = None
    for rec in iter_msgs(path):
        ts = rec.get("timestamp")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        typ = rec.get("type")
        msg = rec.get("message") or {}
        if typ == "assistant":
            u = msg.get("usage") or {}
            mid = msg.get("id") or rec.get("uuid")
            if u and mid:
                seen[mid] = u  # last write wins (streaming partials)
            model = msg.get("model") or model
            for blk in msg.get("content") or []:
                if isinstance(blk, dict):
                    if blk.get("type") == "text":
                        chars["assistant_text"] += len(blk.get("text", ""))
                    elif blk.get("type") == "thinking":
                        chars["assistant_thinking"] += len(blk.get("thinking", ""))
                    elif blk.get("type") == "tool_use":
                        chars["tool_use_input"] += len(json.dumps(blk.get("input", {}), ensure_ascii=False))
        elif typ == "user":
            content = msg.get("content")
            if isinstance(content, str):
                chars["user_text"] += len(content)
            elif isinstance(content, list):
                for blk in content:
                    if not isinstance(blk, dict):
                        continue
                    if blk.get("type") == "tool_result":
                        c = blk.get("content")
                        if isinstance(c, str):
                            chars["tool_result"] += len(c)
                        elif isinstance(c, list):
                            for cc in c:
                                if isinstance(cc, dict) and cc.get("type") == "text":
                                    chars["tool_result"] += len(cc.get("text", ""))
                    elif blk.get("type") == "text":
                        t = blk.get("text", "")
                        if t.startswith("<system-reminder>") or "<system-reminder>" in t[:40]:
                            chars["system_reminder"] += len(t)
                        else:
                            chars["user_text"] += len(t)
    tot = defaultdict(int)
    for u in seen.values():
        i = u.get("input_tokens", 0) or 0
        cr = u.get("cache_read_input_tokens", 0) or 0
        cw = u.get("cache_creation_input_tokens", 0) or 0
        cc = u.get("cache_creation") or {}
        w5 = cc.get("ephemeral_5m_input_tokens", 0) or 0
        w1 = cc.get("ephemeral_1h_input_tokens", 0) or 0
        if not (w5 or w1):
            w5 = cw
        o = u.get("output_tokens", 0) or 0
        tot["input"] += i; tot["cache_read"] += cr; tot["w5"] += w5; tot["w1"] += w1; tot["output"] += o
        ctx.append(i + cr + w5 + w1)
    tot["calls"] = len(seen)
    tot["weighted"] = tot["input"] + 0.1 * tot["cache_read"] + 1.25 * tot["w5"] + 2.0 * tot["w1"]
    return tot, ctx, chars, first_ts, last_ts, model


def main():
    rows = []
    for path in glob.glob(os.path.join(PROJ, "*.jsonl")):
        mtime = dt.datetime.fromtimestamp(os.path.getmtime(path))
        if mtime < SINCE:
            continue
        sid = os.path.basename(path)[:-6]
        tot, ctx, chars, f, l, model = usage_of_file(path)
        if tot["calls"] == 0:
            continue
        sub_tot = defaultdict(int)
        sub_files = glob.glob(os.path.join(PROJ, sid, "subagents", "**", "agent-*.jsonl"), recursive=True)
        sub_calls_per_agent = []
        for sp in sub_files:
            st, sctx, _, _, _, _ = usage_of_file(sp)
            for k in ("input", "cache_read", "w5", "w1", "output", "calls", "weighted"):
                sub_tot[k] += st[k]
            sub_calls_per_agent.append(st["calls"])
        rows.append(dict(
            sid=sid[:8], mtime=mtime, model=(model or "?").replace("claude-", ""),
            calls=tot["calls"], ctx_med=int(statistics.median(ctx)) if ctx else 0,
            ctx_max=max(ctx) if ctx else 0, ctx_first=ctx[0] if ctx else 0,
            main_w=tot["weighted"], main_cr=tot["cache_read"], main_out=tot["output"],
            main_w5=tot["w5"], main_w1=tot["w1"], main_in=tot["input"],
            n_sub=len(sub_files), sub_calls=sub_tot["calls"], sub_w=sub_tot["weighted"],
            sub_cr=sub_tot["cache_read"], sub_out=sub_tot["output"],
            sub_calls_med=int(statistics.median(sub_calls_per_agent)) if sub_calls_per_agent else 0,
            chars=dict(chars),
        ))
    rows.sort(key=lambda r: r["mtime"])
    # ---- per session table
    print(f"{'date':11}{'sid':9}{'model':16}{'turns':>6}{'ctx_med':>9}{'ctx_max':>9}{'ctx_1st':>8}"
          f"{'main_w(M)':>10}{'main_cr(M)':>11}{'out(k)':>8}{'nsub':>6}{'subcalls':>9}{'sub_w(M)':>9}{'sub_cr(M)':>10}{'sub_out(k)':>11}{'sub_calls_med':>14}")
    for r in rows:
        print(f"{r['mtime'].strftime('%m-%d %H:%M'):11}{r['sid']:9}{r['model'][:15]:16}{r['calls']:6d}{r['ctx_med']:9d}{r['ctx_max']:9d}{r['ctx_first']:8d}"
              f"{r['main_w']/1e6:10.2f}{r['main_cr']/1e6:11.1f}{r['main_out']/1e3:8.0f}{r['n_sub']:6d}{r['sub_calls']:9d}{r['sub_w']/1e6:9.2f}{r['sub_cr']/1e6:10.1f}{r['sub_out']/1e3:11.0f}{r['sub_calls_med']:14d}")
    # ---- totals
    T = defaultdict(float)
    for r in rows:
        for k in ("main_w", "main_cr", "main_out", "main_w5", "main_w1", "main_in", "sub_w", "sub_cr", "sub_out", "calls", "sub_calls", "n_sub"):
            T[k] += r[k]
    print("\n=== TOTALS since", SINCE.date(), f"({len(rows)} sessions) ===")
    print(f"main: calls={T['calls']:.0f} weighted={T['main_w']/1e6:.1f}M cache_read={T['main_cr']/1e6:.1f}M raw_in={T['main_in']/1e6:.2f}M w5={T['main_w5']/1e6:.1f}M w1={T['main_w1']/1e6:.1f}M out={T['main_out']/1e6:.2f}M")
    print(f"sub : agents={T['n_sub']:.0f} calls={T['sub_calls']:.0f} weighted={T['sub_w']/1e6:.1f}M cache_read={T['sub_cr']/1e6:.1f}M out={T['sub_out']/1e6:.2f}M")
    # cost-ish: weighted input @ $5/M (fable/opus-class guess) + output @ $25/M -> only relative
    # ---- top sessions by weighted
    print("\n=== top 8 sessions by (main_w + sub_w) ===")
    for r in sorted(rows, key=lambda r: -(r["main_w"] + r["sub_w"]))[:8]:
        print(f"{r['mtime'].strftime('%m-%d')} {r['sid']} main_w={r['main_w']/1e6:.1f}M sub_w={r['sub_w']/1e6:.1f}M turns={r['calls']} ctx_med={r['ctx_med']/1e3:.0f}k nsub={r['n_sub']}")
    # ---- char breakdown across all main sessions
    C = defaultdict(int)
    for r in rows:
        for k, v in r["chars"].items():
            C[k] += v
    tot_c = sum(C.values()) or 1
    print("\n=== main-session context composition (chars, all sessions) ===")
    for k, v in sorted(C.items(), key=lambda kv: -kv[1]):
        print(f"{k:20} {v/1e6:8.1f}M chars  {100*v/tot_c:5.1f}%")
    # ---- first-turn baseline trend
    print("\n=== first-turn context (baseline) by session ===")
    for r in rows:
        print(f"{r['mtime'].strftime('%m-%d')} {r['sid']} first_ctx={r['ctx_first']/1e3:.0f}k")


if __name__ == "__main__":
    main()

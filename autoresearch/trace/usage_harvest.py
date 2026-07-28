#!/usr/bin/env python3
"""token 真计量 —— 从 subagent transcript 抽 per-agent usage(确定性,零 LLM)。

design: docs/specs/2026-07-25-scan-wave5-live-mainruler-macro-metering-design.md §④A

**为什么需要它**:此前"贵在哪"全靠 `assemble` 的**落盘字节 ÷2.8** 下界估算
(`assemble.py:500-536`)—— 它不含 intel、不含任何 WebSearch、不含每个 subagent ~15k 的
系统前缀、不含 ensemble 复核,项目自估真实量级约是它的 6 倍。OTEL 那条路
(`trace/telemetry.py`)从 2026-07 建成起**零生产调用点**、`STAGES.md:263` 自述"未实跑"、
全仓找不到一个 `token_telemetry.md`。这里改走 harness 自己落盘的 transcript:
每条 assistant 消息都带 `message.usage`,含 `cache_read_input_tokens` /
`cache_creation_input_tokens` —— cache 命中率也一并有了读数。

**🚨去重是硬要求**:流式更新会让**同一条 message.id 的 usage 重复出现多行**(实测一个
Explore agent:109 行 usage / 49 条唯一 id)。直接求和会把 cache_read 从 4.81M 虚报成
9.83M —— 整整一倍。按 `message.id` 分组、取每组最后一条(累计值)。

  uv run --no-sync python -m autoresearch.trace.usage_harvest --session <sessionId> --out reports/...
  uv run --no-sync python -m autoresearch.trace.usage_harvest --dir <subagents 目录>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def _iter_rows(path: Path):
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:  # noqa: BLE001 — 半截行跳过,不废整份 transcript
            continue


# 计价倍率(相对 base input token 的**倍数**,不是价格;来源:官方 prompt caching 计价)。
# cache 读 ≈0.1×、5 分钟 TTL 写 1.25×、1 小时 TTL 写 2×。
# 为什么必须加权:一个 agent 的「计费输入」里 90%+ 是 cache_read,而它只按 0.1 倍计价——
# 拿原始 token 总量比大小,会把"贵"的排序排反。
_W_READ, _W_WRITE_5M, _W_WRITE_1H = 0.1, 1.25, 2.0


def usage_of(path: Path) -> dict:
    """单份 transcript → 去重后的 usage 合计 + agent 身份。

    去重键 = `message.id`;同 id 取**最后**一条(流式累计值,前面的都是中间态)。
    """
    latest: dict[str, dict] = {}
    agent = effort = model = None
    for row in _iter_rows(path):
        agent = agent or row.get("attributionAgent")
        effort = effort or row.get("effort")
        msg = row.get("message") or {}
        model = model or msg.get("model")
        u = msg.get("usage")
        if u and msg.get("id"):
            latest[msg["id"]] = u
    tot = {"messages": len(latest), "input": 0, "output": 0,
           "cache_read": 0, "cache_create": 0, "cache_create_1h": 0}
    for u in latest.values():
        tot["input"] += int(u.get("input_tokens") or 0)
        tot["output"] += int(u.get("output_tokens") or 0)
        tot["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
        tot["cache_create"] += int(u.get("cache_creation_input_tokens") or 0)
        # 5m/1h 两种 TTL 的写入倍率不同(1.25× vs 2×),transcript 分开记了就别混算
        tot["cache_create_1h"] += int((u.get("cache_creation") or {})
                                      .get("ephemeral_1h_input_tokens") or 0)
    tot["agent"] = agent or "(未标注)"
    tot["effort"] = effort or "—"
    tot["model"] = model or "—"
    tot["file"] = path.name
    tot["billed_in"] = tot["input"] + tot["cache_create"] + tot["cache_read"]
    w5 = max(tot["cache_create"] - tot["cache_create_1h"], 0)
    tot["weighted_in"] = round(tot["input"] + w5 * _W_WRITE_5M
                               + tot["cache_create_1h"] * _W_WRITE_1H
                               + tot["cache_read"] * _W_READ)
    return tot


# 模型价差(相对 opus 输入价的**倍率**,仅供「贵在哪」定序,不冒充账单)。
# 为什么必须单列:上面的加权口径只含 **cache 倍率**,不含模型价差 —— 把一个纯壳 agent
# 从 opus 降到 haiku,加权 token 数几乎不变而真实成本降一个量级。没有这一维,Wave6 T1
# 那类降档改动在表上完全看不出来,等于无法验收。
_MODEL_MULT = {"haiku": 0.1, "sonnet": 0.33, "opus": 1.0}


def model_family(model: str | None) -> str:
    """完整 model id → 家族名(haiku/sonnet/opus);认不出 → `(未标注)`。

    取家族而非原始 id:否则同族跨版本(`claude-haiku-4-5-20251001` vs `claude-haiku-…`)
    会分裂成多行,汇总失去意义。
    """
    m = str(model or "").lower()
    for fam in ("haiku", "sonnet", "opus"):
        if fam in m:
            return fam
    return "(未标注)"


def collect_glob(pattern: str) -> list[dict]:
    """按 glob 收 transcript(追溯模式)→ 逐 agent usage(按加权降序)。

    计量代码晚于某次 run 落地时(Wave6 附录 A 的处境:`73981` 比那次 run 晚 4h40m),
    transcript 仍存活 —— 这里让补账成为官方入口,而不是每次手写驱动脚本。
    """
    import glob as _glob

    rows = [usage_of(Path(p)) for p in sorted(_glob.glob(pattern, recursive=True))]
    return sorted(rows, key=lambda r: -r["weighted_in"])


def cache_hit_rate(rows: list[dict]) -> float | None:
    """cache_read / (cache_read + cache_create + input);分母 0 → None(不编 0%)。"""
    denom = sum(r["cache_read"] + r["cache_create"] + r["input"] for r in rows)
    return None if denom <= 0 else sum(r["cache_read"] for r in rows) / denom


def collect(sub_dir: Path | str) -> list[dict]:
    """目录下(**递归**)所有 `agent-*.jsonl` → 逐 agent usage(按加权降序)。

    递归是必需的,不是保险:harness 把 **Agent 工具直派**的 subagent 放在 `subagents/` 扁平层,
    把 **workflow 派**的放在 `subagents/workflows/wf_<id>/` —— 而本项目一次扫描的 agent 几乎
    全部来自 workflow。原先的非递归 `glob("agent-*.jsonl")` 只看得见扁平层,于是
    2026-07-27 的 CP7 首读对着 45 个真实 transcript 报「无 transcript」,只能改走
    `--transcripts '<dir>/**/agent-*.jsonl'` 后门手搓(Wave7 B′-a)。
    正门与后门给出同一张表是 CP7 作为裁决基础的前提 —— 读数不该取决于走了哪个入口。

    `rglob` 天然按路径去重(同一份 transcript 不会被两条 glob 各收一次 = 账单翻倍);
    session 目录下的一切按构造都属于该 session,不存在「别的 session 混入」问题
    (那是手写 `--transcripts` glob 才需要自己当心的事)。
    """
    d = Path(sub_dir)
    if not d.is_dir():
        return []
    rows = [usage_of(p) for p in sorted(d.rglob("agent-*.jsonl"))]
    return sorted(rows, key=lambda r: -r["weighted_in"])   # 按真实贵不贵排,不按原始量


def _k(n: int) -> str:
    return f"{n / 1000:.1f}k" if n < 1_000_000 else f"{n / 1_000_000:.2f}M"


def render(rows: list[dict], sub_dir: str | None = None) -> str:
    """→ markdown(逐 agent 表 + 按 agent 类型汇总 + 覆盖率声明)。"""
    out = ["# token 真计量(subagent transcript · 按 message.id 去重)", ""]
    if not rows:
        out += [f"_无 transcript(目录:{sub_dir or '—'})—— 本次没有 subagent,"
                "或 transcript 落在别的 session 目录下。_"]
        return "\n".join(out)
    tot_out = sum(r["output"] for r in rows)
    tot_billed = sum(r["billed_in"] for r in rows)
    tot_w = sum(r["weighted_in"] for r in rows)
    hit = cache_hit_rate(rows)
    out += [f"- **{len(rows)} 个 subagent** · 原始输入 **{_k(tot_billed)}** → "
            f"**加权 {_k(tot_w)}**(cache读 ×{_W_READ}、5m写 ×{_W_WRITE_5M}、1h写 ×{_W_WRITE_1H})· "
            f"输出合计 **{_k(tot_out)}** · cache 命中率 "
            + (f"**{hit:.1%}**" if hit is not None else "—"),
            "",
            "| agent | model | effort | 消息 | 输出 | cache读 | cache写 | 生输入 | 原始输入 | **加权输入** |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        out.append(f"| {r['agent']} | {r['model']} | {r['effort']} | {r['messages']} "
                   f"| {_k(r['output'])} | {_k(r['cache_read'])} | {_k(r['cache_create'])} "
                   f"| {_k(r['input'])} | {_k(r['billed_in'])} | **{_k(r['weighted_in'])}** |")
    by: dict[str, dict] = {}
    for r in rows:
        b = by.setdefault(r["agent"], {"n": 0, "w": 0, "out": 0})
        b["n"] += 1
        b["w"] += r["weighted_in"]
        b["out"] += r["output"]
    out += ["", "**按 agent 类型汇总**(加权输入降序 —— 这才是「贵在哪」的答案):", "",
            "| agent 类型 | 个数 | 加权输入 | 占比 | 输出 |", "|---|---:|---:|---:|---:|"]
    for name, b in sorted(by.items(), key=lambda kv: -kv[1]["w"]):
        share = f"{b['w'] / tot_w:.0%}" if tot_w else "—"
        out.append(f"| {name} | {b['n']} | {_k(b['w'])} | {share} | {_k(b['out'])} |")
    bym: dict[str, dict] = {}
    for r in rows:
        b = bym.setdefault(model_family(r.get("model")), {"n": 0, "w": 0, "out": 0})
        b["n"] += 1
        b["w"] += r["weighted_in"]
        b["out"] += r["output"]
    out += ["", "**按模型汇总**(加权 × 模型价差 ≈ 真实成本方向 —— 上面的加权口径本身"
            "**不含**模型价差,壳从 opus 降 haiku 时加权几乎不变而成本降一个量级):", "",
            "| 模型 | 个数 | 加权输入 | 价差倍率 | 折算(相对 opus) | 输出 |",
            "|---|---:|---:|---:|---:|---:|"]
    for fam, b in sorted(bym.items(), key=lambda kv: -kv[1]["w"]):
        mult = _MODEL_MULT.get(fam)
        adj = _k(int(b["w"] * mult)) if mult else "—"
        out.append(f"| {fam} | {b['n']} | {_k(b['w'])} | {mult if mult else '—'} "
                   f"| {adj} | {_k(b['out'])} |")
    out += ["", "_**覆盖声明**:本表只覆盖上表列出的 subagent transcript —— "
            "**主会话自身的消耗不在内**,跑在别的 session 目录下的 agent 也不在内。"
            "产物能证明跑过什么,不能证明没跑过什么:表里没有的不等于没花钱。_"]
    return "\n".join(out)


def find_session_dir(session_id: str, projects_root: Path | str | None = None) -> Path | None:
    """sessionId → `<projects>/<slug>/<sessionId>/subagents`(跨项目 slug 搜一遍)。"""
    root = Path(projects_root or PROJECTS_ROOT)
    if not root.is_dir():
        return None
    for slug in root.iterdir():
        cand = slug / session_id / "subagents"
        if cand.is_dir():
            return cand
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="subagent token 真计量(零 LLM)")
    ap.add_argument("--dir", default=None, help="subagents 目录(与 --session 二选一)")
    ap.add_argument("--session", default=None, help="sessionId(自动定位 subagents 目录)")
    ap.add_argument("--transcripts", default=None,
                    help="transcript glob(追溯模式,与 --dir/--session 三选一)")
    ap.add_argument("--out", default=None, help="落盘 md 路径(缺省只打印)")
    a = ap.parse_args(argv)
    if a.transcripts:
        md = render(collect_glob(a.transcripts), sub_dir=a.transcripts)
    else:
        sub = Path(a.dir) if a.dir else (find_session_dir(a.session) if a.session else None)
        if sub is None:
            print("[usage_harvest] 需要 --dir / --session / --transcripts 之一(且目录存在)")
            return 1
        md = render(collect(sub), sub_dir=str(sub))
    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md + "\n", encoding="utf-8")
        print(f"[usage_harvest] → {p}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

from autoresearch.trace.pricing import (
    PRICE_SOURCE_EFFECTIVE_DATE,
    PRICE_SOURCE_URL,
    estimate_usd,
)

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


def usage_of(path: Path, role: str = "subagent") -> dict:
    """单份 transcript → 去重后的 usage 合计 + agent 身份。

    去重键 = `message.id`;同 id 取**最后**一条(流式累计值,前面的都是中间态)。
    """
    latest: dict[str, dict] = {}
    agent = effort = model = None
    speed = "standard"
    failures: list[int] = []
    terminals: list[int] = []
    for idx, row in enumerate(_iter_rows(path)):
        if row.get("error") or row.get("isApiErrorMessage"):
            failures.append(idx)
        agent = agent or row.get("attributionAgent")
        effort = effort or row.get("effort")
        msg = row.get("message") or {}
        candidate_model = msg.get("model")
        if candidate_model and candidate_model != "<synthetic>":
            model = model or candidate_model
        u = msg.get("usage")
        if u and msg.get("id"):
            latest[msg["id"]] = u
            speed = u.get("speed") or speed
        if (
            not (row.get("error") or row.get("isApiErrorMessage"))
            and msg.get("stop_reason") in {"end_turn", "stop_sequence"}
        ):
            terminals.append(idx)
    tot = {"messages": len(latest), "input": 0, "output": 0,
           "cache_read": 0, "cache_create": 0, "cache_create_1h": 0,
           "cache_create_5m": 0}
    for u in latest.values():
        tot["input"] += int(u.get("input_tokens") or 0)
        tot["output"] += int(u.get("output_tokens") or 0)
        tot["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
        cache_total = int(u.get("cache_creation_input_tokens") or 0)
        tot["cache_create"] += cache_total
        # 5m/1h 两种 TTL 的写入倍率不同(1.25× vs 2×),transcript 分开记了就别混算
        cache_split = u.get("cache_creation") or {}
        c1h = int(cache_split.get("ephemeral_1h_input_tokens") or 0)
        c5m = int(cache_split.get("ephemeral_5m_input_tokens") or 0)
        tot["cache_create_1h"] += c1h
        tot["cache_create_5m"] += c5m if c5m else max(cache_total - c1h, 0)
    failure_count = len(failures)
    terminal_after_failure = bool(
        terminals and (not failures or terminals[-1] > failures[-1])
    )
    if failure_count and terminal_after_failure:
        status = "RETRIED_SUCCEEDED"
    elif failure_count:
        status = "FAILED"
    elif terminals:
        status = "SUCCEEDED"
    else:
        status = "INCOMPLETE"
    discarded = status == "FAILED"
    retry_count = failure_count if terminal_after_failure else max(failure_count - 1, 0)
    tot["role"] = role
    tot["agent"] = "(主会话)" if role == "main" else (agent or "(未标注)")
    tot["effort"] = effort or "—"
    tot["model"] = model or "—"
    tot["speed"] = speed
    tot["file"] = path.name
    tot["path"] = str(path)
    tot["status"] = status
    tot["failure_count"] = failure_count
    tot["retry_count"] = retry_count
    tot["discarded"] = discarded
    tot["billed_in"] = tot["input"] + tot["cache_create"] + tot["cache_read"]
    tot["weighted_in"] = round(tot["input"] + tot["cache_create_5m"] * _W_WRITE_5M
                               + tot["cache_create_1h"] * _W_WRITE_1H
                               + tot["cache_read"] * _W_READ)
    priced = estimate_usd(
        tot["model"],
        tot,
        as_of=PRICE_SOURCE_EFFECTIVE_DATE,
        speed=speed,
    )
    tot.update({k: v for k, v in priced.items() if k != "total_usd"})
    tot["estimated_usd"] = priced["total_usd"]
    opus = estimate_usd(
        "claude-opus-5",
        tot,
        as_of=PRICE_SOURCE_EFFECTIVE_DATE,
        speed="standard",
    )["total_usd"]
    tot["relative_opus_cost"] = (
        None
        if priced["total_usd"] is None or not opus
        else priced["total_usd"] / opus
    )
    tot["discarded_usd"] = priced["total_usd"] if discarded else 0.0
    tot["retry_usd"] = None if retry_count else 0.0
    tot["retry_cost_status"] = "UNATTRIBUTED" if retry_count else "NONE"
    return tot


# 模型价差(相对 opus 输入价的**倍率**,仅供「贵在哪」定序,不冒充账单)。
# 为什么必须单列:上面的加权口径只含 **cache 倍率**,不含模型价差 —— 把一个纯壳 agent
# 从 opus 降到 haiku,加权 token 数几乎不变而真实成本降一个量级。没有这一维,Wave6 T1
# 那类降档改动在表上完全看不出来,等于无法验收。
_MODEL_MULT = {"haiku": 0.2, "sonnet": 0.4, "opus": 1.0, "fable": 2.0}


def model_family(model: str | None) -> str:
    """完整 model id → 家族名(haiku/sonnet/opus);认不出 → `(未标注)`。

    取家族而非原始 id:否则同族跨版本(`claude-haiku-4-5-20251001` vs `claude-haiku-…`)
    会分裂成多行,汇总失去意义。
    """
    m = str(model or "").lower()
    for fam in ("haiku", "sonnet", "opus", "fable", "mythos"):
        if fam in m:
            return "fable" if fam == "mythos" else fam
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


def find_session_files(
    session_id: str,
    projects_root: Path | str | None = None,
) -> tuple[Path | None, Path | None]:
    """sessionId → (主 transcript, subagents 目录)。"""
    root = Path(projects_root or PROJECTS_ROOT)
    if not root.is_dir():
        return None, None
    for slug in sorted(root.iterdir()):
        main_path = slug / f"{session_id}.jsonl"
        sub_dir = slug / session_id / "subagents"
        if main_path.is_file() or sub_dir.is_dir():
            return (
                main_path if main_path.is_file() else None,
                sub_dir if sub_dir.is_dir() else None,
            )
    return None, None


def collect_session(
    session_id: str,
    projects_root: Path | str | None = None,
) -> list[dict]:
    """主会话和该 session 的全部 subagent 合并为一张成本表。"""
    main_path, sub_dir = find_session_files(session_id, projects_root)
    rows: list[dict] = []
    if main_path is not None:
        rows.append(usage_of(main_path, role="main"))
    if sub_dir is not None:
        rows.extend(collect(sub_dir))
    return sorted(rows, key=lambda r: -r["weighted_in"])


def build_ledger(rows: list[dict], *, source: str | None = None) -> dict:
    """逐 transcript facts → 可机读总账。"""
    priced = [r for r in rows if r.get("estimated_usd") is not None]
    totals = {
        "transcripts": len(rows),
        "main_transcripts": sum(r.get("role") == "main" for r in rows),
        "subagent_transcripts": sum(r.get("role") == "subagent" for r in rows),
        "messages": sum(int(r.get("messages") or 0) for r in rows),
        "input": sum(int(r.get("input") or 0) for r in rows),
        "output": sum(int(r.get("output") or 0) for r in rows),
        "cache_read": sum(int(r.get("cache_read") or 0) for r in rows),
        "cache_create_5m": sum(int(r.get("cache_create_5m") or 0) for r in rows),
        "cache_create_1h": sum(int(r.get("cache_create_1h") or 0) for r in rows),
        "weighted_in": sum(int(r.get("weighted_in") or 0) for r in rows),
        "failure_count": sum(int(r.get("failure_count") or 0) for r in rows),
        "retry_count": sum(int(r.get("retry_count") or 0) for r in rows),
        "discarded_transcripts": sum(bool(r.get("discarded")) for r in rows),
        "estimated_usd": sum(float(r["estimated_usd"]) for r in priced),
        "discarded_usd": sum(float(r.get("discarded_usd") or 0) for r in priced),
        "unpriced_transcripts": len(rows) - len(priced),
    }
    return {
        "schema_version": 1,
        "pricing": {
            "source": PRICE_SOURCE_URL,
            "effective_date": PRICE_SOURCE_EFFECTIVE_DATE,
            "scope": "Claude API standard global list-price estimate",
        },
        "source": source,
        "cache_hit_rate": cache_hit_rate(rows),
        "totals": totals,
        "rows": rows,
    }


def _k(n: int) -> str:
    return f"{n / 1000:.1f}k" if n < 1_000_000 else f"{n / 1_000_000:.2f}M"


def _usd(value: float | None) -> str:
    return "—" if value is None else f"${value:.4f}"


def render(rows: list[dict], sub_dir: str | None = None) -> str:
    """→ markdown(逐 agent 表 + 按 agent 类型汇总 + 覆盖率声明)。"""
    has_main = any(r.get("role") == "main" for r in rows)
    scope = "主会话 + subagent" if has_main else "subagent"
    out = [f"# token 真计量({scope} transcript · 按 message.id 去重)", ""]
    if not rows:
        out += [f"_无 transcript(目录:{sub_dir or '—'})—— 本次没有 subagent,"
                "或 transcript 落在别的 session 目录下。_"]
        return "\n".join(out)
    tot_out = sum(r["output"] for r in rows)
    tot_billed = sum(r["billed_in"] for r in rows)
    tot_w = sum(r["weighted_in"] for r in rows)
    hit = cache_hit_rate(rows)
    ledger = build_ledger(rows, source=sub_dir)
    facts = ledger["totals"]
    role_note = (
        f"{facts['main_transcripts']} 主会话 + {facts['subagent_transcripts']} subagent"
        if has_main
        else f"{facts['subagent_transcripts']} 个 subagent"
    )
    out += [f"- **{role_note}** · 原始输入 **{_k(tot_billed)}** → "
            f"**加权 {_k(tot_w)}**(cache读 ×{_W_READ}、5m写 ×{_W_WRITE_5M}、1h写 ×{_W_WRITE_1H})· "
            f"输出合计 **{_k(tot_out)}** · cache 命中率 "
            + (f"**{hit:.1%}**" if hit is not None else "—"),
            f"- **估算成本 {_usd(facts['estimated_usd'])}**"
            f"(失败 {facts['failure_count']} · 重试 {facts['retry_count']} · "
            f"废弃 {facts['discarded_transcripts']} 份/{_usd(facts['discarded_usd'])} · "
            f"未定价 {facts['unpriced_transcripts']} 份)",
            f"- 价格口径:Claude API standard global list price · "
            f"{PRICE_SOURCE_EFFECTIVE_DATE} 快照 · {PRICE_SOURCE_URL}",
            "",
            "| role | agent | model | effort | 状态 | 消息 | 输出 | cache读 | 5m写 | 1h写 | 生输入 | **估算成本** |",
            "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        out.append(
            f"| {r.get('role', 'subagent')} | {r['agent']} | {r['model']} | "
            f"{r['effort']} | {r.get('status', '—')} | {r['messages']} "
            f"| {_k(r['output'])} | {_k(r['cache_read'])} "
            f"| {_k(r.get('cache_create_5m', 0))} "
            f"| {_k(r.get('cache_create_1h', 0))} | {_k(r['input'])} "
            f"| **{_usd(r.get('estimated_usd'))}** |"
        )
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
        b = bym.setdefault(
            model_family(r.get("model")),
            {"n": 0, "w": 0, "out": 0, "usd": 0.0, "unpriced": 0},
        )
        b["n"] += 1
        b["w"] += r["weighted_in"]
        b["out"] += r["output"]
        if r.get("estimated_usd") is None:
            b["unpriced"] += 1
        else:
            b["usd"] += float(r["estimated_usd"])
    out += ["", "**按模型汇总**(加权 × 模型价差 ≈ 真实成本方向 —— 上面的加权口径本身"
            "**不含**模型价差,壳从 opus 降 haiku 时加权几乎不变而成本降一个量级):", "",
            "| 模型 | 个数 | 加权输入 | 价差倍率 | 折算(相对 opus) | 输出 | 估算成本 | 未定价 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for fam, b in sorted(bym.items(), key=lambda kv: -kv[1]["w"]):
        mult = _MODEL_MULT.get(fam)
        adj = _k(int(b["w"] * mult)) if mult else "—"
        out.append(f"| {fam} | {b['n']} | {_k(b['w'])} | {mult if mult else '—'} "
                   f"| {adj} | {_k(b['out'])} | {_usd(b['usd'])} | {b['unpriced']} |")
    if has_main:
        coverage = (
            "_**覆盖声明**:本表覆盖已定位到的主会话与其 session 目录下 subagent transcript；"
            "跑在别的 session 目录下的 agent 不在内。产物能证明跑过什么,不能证明没跑过什么："
            "表里没有的不等于没花钱。_"
        )
    else:
        coverage = (
            "_**覆盖声明**:本表只覆盖上表列出的 subagent transcript —— "
            "**主会话自身的消耗不在内**,跑在别的 session 目录下的 agent 也不在内。"
            "产物能证明跑过什么,不能证明没跑过什么:表里没有的不等于没花钱。_"
        )
    out += ["", coverage]
    return "\n".join(out)


def find_session_dir(session_id: str, projects_root: Path | str | None = None) -> Path | None:
    """sessionId → `<projects>/<slug>/<sessionId>/subagents`(跨项目 slug 搜一遍)。"""
    return find_session_files(session_id, projects_root)[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="subagent token 真计量(零 LLM)")
    ap.add_argument("--dir", default=None, help="subagents 目录(与 --session 二选一)")
    ap.add_argument("--session", default=None, help="sessionId(自动定位 subagents 目录)")
    ap.add_argument("--transcripts", default=None,
                    help="transcript glob(追溯模式,与 --dir/--session 三选一)")
    ap.add_argument("--out", default=None, help="落盘 md 路径(缺省只打印)")
    ap.add_argument("--json-out", default=None, help="落盘 canonical JSON ledger")
    a = ap.parse_args(argv)
    if a.transcripts:
        rows = collect_glob(a.transcripts)
        source = a.transcripts
    elif a.session:
        rows = collect_session(a.session)
        source = f"session:{a.session}"
    else:
        sub = Path(a.dir) if a.dir else None
        if sub is None:
            print("[usage_harvest] 需要 --dir / --session / --transcripts 之一(且目录存在)")
            return 1
        rows = collect(sub)
        source = str(sub)
    if not rows and a.session and find_session_files(a.session) == (None, None):
        print(f"[usage_harvest] session 不存在:{a.session}")
        return 1
    md = render(rows, sub_dir=source)
    if a.json_out:
        jp = Path(a.json_out)
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(
            json.dumps(build_ledger(rows, source=source), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        print(f"[usage_harvest] JSON → {jp}")
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

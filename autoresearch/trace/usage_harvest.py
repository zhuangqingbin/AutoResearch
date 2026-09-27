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
from collections.abc import Sequence
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.trace.pricing import (
    PRICE_SOURCE_EFFECTIVE_DATE,
    PRICE_SOURCE_URL,
    estimate_usd,
)
from autoresearch.trace.transcripts import adapter_for
from autoresearch.trace.transcripts.base import (
    RunIdentity,
    TranscriptRef,
    UsageRecord,
)
from autoresearch.trace.transcripts.snapshot import TranscriptSnapshot, capture_snapshot

PROJECTS_ROOT = Path.home() / ".claude" / "projects"


# 计价倍率(相对 base input token 的**倍数**,不是价格;来源:官方 prompt caching 计价)。
# cache 读 ≈0.1×、5 分钟 TTL 写 1.25×、1 小时 TTL 写 2×。
# 为什么必须加权:一个 agent 的「计费输入」里 90%+ 是 cache_read,而它只按 0.1 倍计价——
# 拿原始 token 总量比大小,会把"贵"的排序排反。
_W_READ, _W_WRITE_5M, _W_WRITE_1H = 0.1, 1.25, 2.0


def legacy_usage_dict(record: UsageRecord) -> dict:
    """Convert stable adapter usage into the historical public dict schema."""
    path = record.ref.path
    tot = {
        "messages": record.messages,
        "input": record.input,
        "output": record.output,
        "cache_read": record.cache_read,
        "cache_create": record.cache_create,
        "cache_create_1h": record.cache_create_1h,
        "cache_create_5m": record.cache_create_5m,
        "role": record.role,
        "agent": record.agent,
        "effort": record.effort,
        "model": record.model,
        "speed": record.speed,
        "file": path.name if path is not None else "—",
        "path": str(path) if path is not None else "—",
        "status": record.status,
        "failure_count": record.failure_count,
        "retry_count": record.retry_count,
        "discarded": record.discarded,
        "reasoning_output": record.reasoning_output,
    }
    tot["billed_in"] = tot["input"] + tot["cache_create"] + tot["cache_read"]
    tot["weighted_in"] = round(tot["input"] + tot["cache_create_5m"] * _W_WRITE_5M
                               + tot["cache_create_1h"] * _W_WRITE_1H
                               + tot["cache_read"] * _W_READ)
    if record.status == "UNMEASURED":
        # 证据缺失/无法解析:不定价、不折算,也绝不渲染成 $0 —— 未计量不是免费。
        tot.update(
            {
                "estimated_usd": None,
                "relative_opus_cost": None,
                "discarded_usd": 0.0,
                "retry_usd": 0.0,
                "retry_cost_status": "NONE",
            }
        )
        return tot
    priced = estimate_usd(
        tot["model"],
        tot,
        as_of=PRICE_SOURCE_EFFECTIVE_DATE,
        speed=tot["speed"],
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
    tot["discarded_usd"] = priced["total_usd"] if record.discarded else 0.0
    tot["retry_usd"] = None if record.retry_count else 0.0
    tot["retry_cost_status"] = "UNATTRIBUTED" if record.retry_count else "NONE"
    return tot


def usage_of(path: Path, role: str = "subagent") -> dict:
    """单份 Claude transcript → 保持旧 schema 的 usage dict。"""
    ref = TranscriptRef(engine="claude", path=Path(path), role=role)
    return legacy_usage_dict(adapter_for("claude").usage(ref))


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


def unmeasured_row(ref: TranscriptRef, *, reason: str) -> dict:
    """一条诚实的「没量到」行:状态 UNMEASURED、成本 None,绝不是 0。"""
    record = UsageRecord(
        ref=ref,
        messages=0,
        input=0,
        output=0,
        cache_read=0,
        cache_create=0,
        cache_create_1h=0,
        cache_create_5m=0,
        role=ref.role,
        agent=ref.role,
        effort="—",
        model="—",
        speed="standard",
        status="UNMEASURED",
        failure_count=0,
        retry_count=0,
        discarded=False,
    )
    row = legacy_usage_dict(record)
    row["reason"] = reason
    row["invocation_id"] = ref.invocation_id
    row["subject"] = ref.subject
    return row


#: `session_agent.executors.headless_claude` 的调用记录,相对 run 的 staging(批 4,spec R3)。
#: headless 场每个推理任务是一个**顶级** `claude -p` 会话(`<projects>/<slug>/<session-id>.jsonl`),
#: 不在任何宿主 session 的 subagents 目录下 —— 按 session 目录找永远是空表,只能从记录反查。
HEADLESS_RECORDS = Path("_dispatch") / "headless"


def _headless_record_files(source: Path | str) -> list[Path]:
    """run staging 目录 / 记录目录本身 / 记录文件 glob,三种写法都认。"""
    import glob as _glob

    path = Path(source)
    if path.is_dir():
        folder = path / HEADLESS_RECORDS if (path / HEADLESS_RECORDS).is_dir() else path
        return sorted(folder.glob("*.json"))
    return sorted(Path(item) for item in _glob.glob(str(source)) if item.endswith(".json"))


def _reported_cost(record: dict) -> float | None:
    value = record.get("total_cost_usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _headless_row(record: dict, projects_root: Path | str | None) -> dict:
    session_id = str(record.get("session_id") or record.get("requested_session_id") or "")
    agent = str(record.get("agent_type") or "headless")
    declared = record.get("transcript_path")
    path = Path(declared) if declared else None
    if (path is None or not path.is_file()) and session_id:
        path = find_session_files(session_id, projects_root)[0]
    cost = _reported_cost(record)
    if path is None or not path.is_file():
        row = unmeasured_row(
            TranscriptRef(engine="claude", path=None, status="GONE", role="headless"),
            reason=f"headless transcript missing for session {session_id or '—'}",
        )
        row["cost_source"] = None
    else:
        row = usage_of(path, role="headless")
        if cost is not None and row["status"] != "UNMEASURED":
            # CLI 自己算的钱(结果 JSON 的 total_cost_usd)比按 transcript 估的更真。
            row["estimated_usd"] = cost
            row["discarded_usd"] = cost if row.get("discarded") else 0.0
            row["cost_source"] = "result_json"
        else:
            row["cost_source"] = "estimate"
    row.update(
        agent=agent,
        dispatcher="headless",
        task_id=record.get("task_id"),
        attempt=record.get("attempt"),
        session_id=session_id or None,
        reported_cost_usd=cost,
        exit_code=record.get("exit_code"),
        call_state=record.get("state"),
    )
    return row


def collect_headless(
    source: Path | str,
    *,
    projects_root: Path | str | None = None,
) -> list[dict]:
    """headless 执行器的调用记录 → 逐次 `claude -p` 的 usage 行(``dispatcher=headless``)。

    token 读 transcript(按 message.id 去重,与 subagent 同一把尺);成本取结果 JSON 的
    ``total_cost_usd``(没有则按 transcript 估)。transcript 找不到 → UNMEASURED 行,
    ``reported_cost_usd`` 保留为事实但不计入合计 —— 未计量不是免费,也不是「按自报算」。
    """
    rows = []
    for record_path in _headless_record_files(source):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            row = unmeasured_row(
                TranscriptRef(engine="claude", path=None, status="GONE", role="headless"),
                reason=f"unreadable headless record {record_path.name}: {type(exc).__name__}",
            )
            row.update(dispatcher="headless", agent="headless", cost_source=None)
            rows.append(row)
            continue
        # SPAWN_FAILED = the CLI never started: no session ran, nothing to meter.
        if (isinstance(record, dict) and record.get("task_id")
                and record.get("state") != "SPAWN_FAILED"):
            rows.append(_headless_row(record, projects_root))
    return sorted(rows, key=lambda r: -r["weighted_in"])


def _ordinal_span(ref: TranscriptRef) -> tuple[float, float]:
    start = ref.start_ordinal if ref.start_ordinal is not None else float("-inf")
    end = ref.end_ordinal if ref.end_ordinal is not None else float("inf")
    return start, end


def _segments_overlap(a: TranscriptRef, b: TranscriptRef) -> bool:
    """True when two refs' ``[start_ordinal, end_ordinal]`` windows share any
    ordinal -- touching boundaries count as overlap (conservative: a shared
    boundary ordinal would otherwise get counted in both segments' usage
    deltas)."""
    a_start, a_end = _ordinal_span(a)
    b_start, b_end = _ordinal_span(b)
    return a_start <= b_end and b_start <= a_end


def _has_overlapping_segments(refs: Sequence[TranscriptRef]) -> bool:
    return any(
        _segments_overlap(refs[i], refs[j])
        for i in range(len(refs))
        for j in range(i + 1, len(refs))
    )


def collect_run(
    run_id: str,
    *,
    engine: str | None = None,
    snapshot_cache: dict[str, TranscriptSnapshot] | None = None,
) -> list[dict]:
    """一个 run 的全部**显式绑定** transcript → 逐 agent usage(按加权降序)。

    定位权只在 adapter 手里:Claude 走 session 目录,Codex 走 capsule 里的显式绑定。
    绑定在、文件不在 → UNMEASURED 行;绝不因为「目录里没文件」就当作没花钱。

    🚨 `session_ref` 必须从 run 的 contract 读(D6.4①):此前这里永远传
    `RunIdentity(session_ref=None)`,而 `ClaudeTranscriptAdapter.locate` 在
    `session_ref` 缺席时**短路返回 `[]`**——Claude 引擎的 `collect_run` 因此从没
    真正定位过任何 transcript。contract 里仍读到 `None`(极少数没绑过 session 的
    run)时也不能让整函数安静地退化成 `[]`:那正是「表里没有的看起来像没花钱」的
    反面教材,所以改吐一行诚实的 UNMEASURED。

    **单源一次快照(2026-09-12 task 2)**:每个唯一 source path 只
    `snapshot.capture_snapshot` 一次(``snapshot_cache`` 可选,由调用方共享 —— 见
    `capsule._write_usage`;这里独立调用时会自建并丢弃自己的 cache,**不要求**
    调用方先绑定任何东西,standalone `usage_harvest` CLI 因此不获得新的 binding
    依赖)。Codex 的一份 session 文件可能被多个角色段共享
    (`start_ordinal`/`end_ordinal` 相邻或交错);当同一 path 下两个及以上引用的
    区段**重叠**,任何一个都不能各自独立求 usage delta(会把重叠窗口的 token
    算两遍)—— 这些引用各记一行 UNMEASURED,并额外产出**一行覆盖整份源文件**的
    合计行,使 run 总量仍然真实(不是不计、也不是均分/填零)。互不重叠的共享段
    仍各自正常计量(它们的差分窗口本就不相交)。
    """
    resolved_engine = str(engine or ws.ENGINE)
    # 未知 run 必须炸,不能安静地变成「0 份 transcript」= 免费。
    if ws.find_run_root(run_id) is None:
        raise FileNotFoundError(f"unknown run_id: {run_id}")
    from autoresearch.trace.capsule import load_run

    run = load_run(run_id)
    session_ref = run.contract.session_ref
    # headless 场(批 4):每个推理任务一个顶级 `claude -p` 会话,记录在 staging 的
    # `_dispatch/headless/`;宿主 session 目录里找不到它们。没有记录 = 空列表 = 旧行为。
    staging = getattr(run, "staging", None)
    headless_rows = collect_headless(staging) if staging is not None and (
        Path(staging) / HEADLESS_RECORDS).is_dir() else []
    adapter = adapter_for(resolved_engine)
    identity = RunIdentity(run_id=run_id, engine=resolved_engine, session_ref=session_ref)
    refs = adapter.locate(identity)
    if not refs and resolved_engine == "claude" and not session_ref:
        return [
            unmeasured_row(
                TranscriptRef(engine="claude", path=None, status="GONE", role="main"),
                reason="run contract 没有绑定 session_ref，Claude adapter 无法定位 transcript",
            ),
            *headless_rows,
        ]

    cache: dict[str, TranscriptSnapshot] = {} if snapshot_cache is None else snapshot_cache

    def snapshot_for(path: Path) -> TranscriptSnapshot:
        key = str(path)
        snapshot = cache.get(key)
        if snapshot is None:
            snapshot = capture_snapshot(path, engine=resolved_engine)
            cache[key] = snapshot
        return snapshot

    by_path: dict[str, list[TranscriptRef]] = {}
    for ref in refs:
        if ref.status == "PRESENT" and ref.path is not None:
            by_path.setdefault(str(ref.path), []).append(ref)
    overlapping_paths = {
        path for path, siblings in by_path.items()
        if len(siblings) > 1 and _has_overlapping_segments(siblings)
    }

    rows: list[dict] = []
    for ref in refs:
        if ref.status != "PRESENT":
            rows.append(unmeasured_row(ref, reason=f"transcript {ref.status}"))
            continue
        if str(ref.path) in overlapping_paths:
            rows.append(
                unmeasured_row(
                    ref,
                    reason=(
                        "source shared with an overlapping segment; cannot "
                        "attribute usage to this invocation individually "
                        "(see the combined row for this source)"
                    ),
                )
            )
            continue
        try:
            snapshot = snapshot_for(Path(ref.path))
            usage = adapter.stats_from_rows(snapshot.rows, ref).usage
            rows.append(legacy_usage_dict(usage))
        except Exception as exc:  # noqa: BLE001 - 不可解析也必须留一行
            rows.append(
                unmeasured_row(ref, reason=f"{type(exc).__name__}: {exc}")
            )

    for path in sorted(overlapping_paths):
        siblings = by_path[path]
        combined_ref = TranscriptRef(
            engine=resolved_engine, path=Path(path), status="PRESENT", role="shared_source",
        )
        try:
            snapshot = snapshot_for(Path(path))
            usage = adapter.stats_from_rows(snapshot.rows, combined_ref).usage
            combined_row = legacy_usage_dict(usage)
            combined_row["merged_invocation_ids"] = sorted(
                {ref.invocation_id for ref in siblings if ref.invocation_id}
            )
            rows.append(combined_row)
        except Exception as exc:  # noqa: BLE001 - 就算整份源文件都读不出来也要留痕
            rows.append(unmeasured_row(combined_ref, reason=f"{type(exc).__name__}: {exc}"))

    rows.extend(headless_rows)
    return sorted(rows, key=lambda r: -r["weighted_in"])


def build_ledger(rows: list[dict], *, source: str | None = None) -> dict:
    """逐 transcript facts → 可机读总账。"""
    priced = [r for r in rows if r.get("estimated_usd") is not None]
    totals = {
        "transcripts": len(rows),
        "main_transcripts": sum(r.get("role") == "main" for r in rows),
        "subagent_transcripts": sum(r.get("role") == "subagent" for r in rows),
        "headless_transcripts": sum(r.get("dispatcher") == "headless" for r in rows),
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
        "unmeasured_transcripts": sum(
            r.get("status") == "UNMEASURED" for r in rows
        ),
        "reasoning_output": sum(int(r.get("reasoning_output") or 0) for r in rows),
        "priced_transcripts": len(priced),
    }
    totals["weighted_input_proxy"] = totals["weighted_in"]
    return {
        "schema_version": 1,
        "metric_version": "weighted-input-v1",
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


def _cost_cell(row: dict) -> str:
    """未计量的证据必须自报家门 —— `$0.0000` 是「量到了,是零」的意思。"""
    if row.get("status") == "UNMEASURED":
        return "— (UNMEASURED)"
    return _usd(row.get("estimated_usd"))


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
    has_priced = bool(facts["priced_transcripts"])
    total_cost = _usd(facts["estimated_usd"]) if has_priced else "—"
    discarded_cost = _usd(facts["discarded_usd"]) if has_priced else "—"
    headless = facts["headless_transcripts"]
    if headless:
        role_note = " + ".join(
            part for part in (
                f"{facts['main_transcripts']} 主会话" if has_main else "",
                f"{facts['subagent_transcripts']} subagent"
                if facts["subagent_transcripts"] else "",
                f"{headless} headless 会话",
            ) if part
        )
    else:
        role_note = (
            f"{facts['main_transcripts']} 主会话 + {facts['subagent_transcripts']} subagent"
            if has_main
            else f"{facts['subagent_transcripts']} 个 subagent"
        )
    out += [f"- **{role_note}** · 原始输入 **{_k(tot_billed)}** → "
            f"**加权 {_k(tot_w)}**(cache读 ×{_W_READ}、5m写 ×{_W_WRITE_5M}、1h写 ×{_W_WRITE_1H})· "
            f"输出合计 **{_k(tot_out)}** · cache 命中率 "
            + (f"**{hit:.1%}**" if hit is not None else "—"),
            f"- **估算成本 {total_cost}**"
            f"(失败 {facts['failure_count']} · 重试 {facts['retry_count']} · "
            f"废弃 {facts['discarded_transcripts']} 份/{discarded_cost} · "
            f"未定价 {facts['unpriced_transcripts']} 份 · "
            f"未计量 {facts['unmeasured_transcripts']} 份)",
            f"- 价格口径:Claude API standard global list price · "
            f"{PRICE_SOURCE_EFFECTIVE_DATE} 快照 · {PRICE_SOURCE_URL}",
            ""]
    # dispatcher 列只在有 headless 行时出现:宿主场的 token_usage.md 逐字不变(parity)。
    if headless:
        out += ["| role | dispatcher | agent | model | effort | 状态 | 消息 | 输出 | cache读 "
                "| 5m写 | 1h写 | 生输入 | **估算成本** |",
                "|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    else:
        out += ["| role | agent | model | effort | 状态 | 消息 | 输出 | cache读 | 5m写 | 1h写 "
                "| 生输入 | **估算成本** |",
                "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        dispatcher = f"{r.get('dispatcher', 'host')} | " if headless else ""
        out.append(
            f"| {r.get('role', 'subagent')} | {dispatcher}{r['agent']} | {r['model']} | "
            f"{r['effort']} | {r.get('status', '—')} | {r['messages']} "
            f"| {_k(r['output'])} | {_k(r['cache_read'])} "
            f"| {_k(r.get('cache_create_5m', 0))} "
            f"| {_k(r.get('cache_create_1h', 0))} | {_k(r['input'])} "
            f"| **{_cost_cell(r)}** |"
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
        b["priced"] = b["n"] - b["unpriced"]
    out += ["", "**按模型汇总**(加权 × 模型价差 ≈ 真实成本方向 —— 上面的加权口径本身"
            "**不含**模型价差,壳从 opus 降 haiku 时加权几乎不变而成本降一个量级):", "",
            "| 模型 | 个数 | 加权输入 | 价差倍率 | 折算(相对 opus) | 输出 | 估算成本 | 未定价 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for fam, b in sorted(bym.items(), key=lambda kv: -kv[1]["w"]):
        mult = _MODEL_MULT.get(fam)
        adj = _k(int(b["w"] * mult)) if mult else "—"
        cost = _usd(b["usd"]) if b.get("priced") else "—"
        out.append(f"| {fam} | {b['n']} | {_k(b['w'])} | {mult if mult else '—'} "
                   f"| {adj} | {_k(b['out'])} | {cost} | {b['unpriced']} |")
    if headless:
        coverage = (
            "_**覆盖声明**:headless 行 = 每个推理任务一个 `claude -p` 会话(按执行器调用记录"
            "`_dispatch/headless/*.json` 反查 transcript;成本取该会话结果 JSON 的 "
            "`total_cost_usd`,缺时按 transcript 估);驱动它们的 runner 是零 LLM 的 Python 进程。"
            "transcript 找不到的调用记为 UNMEASURED、不计入合计。产物能证明跑过什么,不能证明"
            "没跑过什么:表里没有的不等于没花钱。_"
        )
    elif has_main:
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
    ap.add_argument("--engine", default=None, choices=list(ws.ENGINES),
                    help="引擎(与 --run-id 同用;缺省取当前引擎)")
    ap.add_argument("--run-id", default=None,
                    help="run_id(读该 run 的显式 transcript 绑定,与 --dir/--session 互斥)")
    ap.add_argument("--transcripts-from", default=None,
                    help="headless 调用记录:run staging 目录、其 _dispatch/headless/ 目录"
                         "或记录 glob(每条记录的 session_id → 顶级 transcript)")
    ap.add_argument("--out", default=None, help="落盘 md 路径(缺省只打印)")
    ap.add_argument("--json-out", default=None, help="落盘 canonical JSON ledger")
    a = ap.parse_args(argv)
    if a.run_id:
        try:
            rows = collect_run(a.run_id, engine=a.engine)
        except Exception as exc:  # noqa: BLE001 - CLI 把失败变成诚实退出码
            print(f"[usage_harvest] run 取数失败:{type(exc).__name__}: {exc}")
            return 1
        source = f"run:{a.run_id}"
    elif a.transcripts_from:
        rows = collect_headless(a.transcripts_from)
        source = f"headless:{a.transcripts_from}"
    elif a.transcripts:
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

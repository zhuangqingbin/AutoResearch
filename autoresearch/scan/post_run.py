#!/usr/bin/env python3
"""Idempotent local consumers for post-run outbox events."""
from __future__ import annotations

import argparse
import contextlib
import csv
import json
import shutil
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.scan.outbox import OutboxEvent, load_events, outbox_path
from autoresearch.scan.report_model import (
    APPENDIX_FILENAME,
    APPENDIX_SECTIONS,
    RUN_OBSERVATION_DETAIL_MARKERS,
    RUN_OBSERVATION_MARKERS,
    appendix_link,
)
from autoresearch.scan.run_contract import sha256_json

CONSUMER_RECEIPT_SCHEMA_VERSION = 1
CONSUMER_STATE_SCHEMA_VERSION = 1
RECEIPT_STATUSES = {"SUCCEEDED", "FAILED", "SKIPPED"}
# 2026-08-21(用户裁定「整个 learning 层退役」):`RUN_FINALIZED` / `RETRO_FINALIZED` 两个
# 事件下挂的 13 个学习账本 consumer 随闭环一并删除,outbox 只剩档案 δ 回写这一个真消费者。
# 事件本身保留(`RUN_FINALIZED` 仍是发布收尾的语义锚,且 `artifact_index`/receipts 契约
# 都挂在它上面);没有订阅者的事件走 `_expected_pairs` 天然产生零 pair,不报错。
SUBSCRIPTIONS = {
    "DOSSIER_DELTA_READY": {"dossier_delta"},
}
ConsumerHandler = Callable[[OutboxEvent, Path], object]
# managed 标记的**单一事实源**在 `report_model`(§6.5):渲染侧(report_sections /
# report_appendix)与刷新侧(这里)各抄一份字面量,就是给「两边写的不是同一个标记 →
# 注入静默 no-op → 报告留成占位」开了口子。两组标记,两个文件,一个 observation。
OBSERVATION_START, OBSERVATION_END = RUN_OBSERVATION_MARKERS
OBSERVATION_DETAIL_START, OBSERVATION_DETAIL_END = RUN_OBSERVATION_DETAIL_MARKERS
#: 标记缺席时各自的稳定回退锚:summary 用「诚实局限」标题,appendix 用 F 节标题。
SUMMARY_OBSERVATION_ANCHOR = "\n## 诚实局限"
APPENDIX_OBSERVATION_ANCHOR = "\n## " + dict(APPENDIX_SECTIONS)["methods"]


@dataclass(frozen=True)
class ConsumerReceipt:
    schema_version: int
    receipt_id: str
    event_id: str
    consumer: str
    status: str
    attempts: int
    error: str | None
    updated_at: str
    receipt_hash: str

    def _identity_payload(self) -> dict:
        return {"event_id": self.event_id, "consumer": self.consumer}

    def _hash_payload(self) -> dict:
        payload = asdict(self)
        payload.pop("receipt_hash")
        return payload

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def build(
        cls,
        *,
        event_id: str,
        consumer: str,
        status: str,
        attempts: int,
        error: str | None,
        updated_at: str,
    ) -> ConsumerReceipt:
        if status not in RECEIPT_STATUSES:
            raise ValueError(f"invalid consumer receipt status: {status!r}")
        if attempts < 1:
            raise ValueError("consumer receipt attempts must be positive")
        base = cls(
            schema_version=CONSUMER_RECEIPT_SCHEMA_VERSION,
            receipt_id="",
            event_id=str(event_id),
            consumer=str(consumer),
            status=status,
            attempts=int(attempts),
            error=None if error is None else str(error),
            updated_at=str(updated_at),
            receipt_hash="",
        )
        identified = replace(
            base,
            receipt_id=sha256_json(base._identity_payload()),
        )
        return replace(
            identified,
            receipt_hash=sha256_json(identified._hash_payload()),
        )

    @classmethod
    def from_dict(cls, raw: dict) -> ConsumerReceipt:
        receipt = cls(**raw)
        if receipt.schema_version != CONSUMER_RECEIPT_SCHEMA_VERSION:
            raise ValueError(
                "unsupported consumer receipt schema_version="
                f"{receipt.schema_version}"
            )
        rebuilt = cls.build(
            event_id=receipt.event_id,
            consumer=receipt.consumer,
            status=receipt.status,
            attempts=receipt.attempts,
            error=receipt.error,
            updated_at=receipt.updated_at,
        )
        if receipt.receipt_id != rebuilt.receipt_id:
            raise ValueError("consumer receipt_id hash mismatch")
        if receipt.receipt_hash != rebuilt.receipt_hash:
            raise ValueError("consumer receipt hash mismatch")
        return receipt


@dataclass(frozen=True)
class ConsumerRunResult:
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0


def consumer_state_path(scan_dir: Path | str) -> Path:
    return Path(scan_dir) / "outbox" / "consumer_state.json"


def load_consumer_receipts(
    path: Path | str,
) -> dict[str, ConsumerReceipt]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != CONSUMER_STATE_SCHEMA_VERSION
    ):
        raise ValueError("unsupported consumer state book")
    rows = raw.get("receipts")
    if not isinstance(rows, list) or raw.get("receipts_hash") != sha256_json(rows):
        raise ValueError("consumer state receipts hash mismatch")
    receipts = [ConsumerReceipt.from_dict(row) for row in rows]
    if len({receipt.receipt_id for receipt in receipts}) != len(receipts):
        raise ValueError("consumer state duplicate receipt_id")
    return {receipt.receipt_id: receipt for receipt in receipts}


def _write_consumer_receipts(
    scan_dir: Path | str,
    receipts: dict[str, ConsumerReceipt],
) -> Path:
    scan = Path(scan_dir)
    target = consumer_state_path(scan)
    ordered = sorted(receipts.values(), key=lambda receipt: receipt.receipt_id)
    payloads = [receipt.to_dict() for receipt in ordered]
    book = {
        "schema_version": CONSUMER_STATE_SCHEMA_VERSION,
        "analysis_date": scan.name,
        "receipts": payloads,
        "receipts_hash": sha256_json(payloads),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(book, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def initialize_consumer_state(scan_dir: Path | str) -> Path:
    target = consumer_state_path(scan_dir)
    if target.exists():
        load_consumer_receipts(target)
        return target
    return _write_consumer_receipts(scan_dir, {})


def _dossier_delta(event: OutboxEvent, scan: Path) -> object:
    from autoresearch.dossier.delta import record_scan_delta

    payload = event.payload
    conviction = payload.get("conviction")
    result = record_scan_delta(
        str(payload["code"]).zfill(6),
        event.analysis_date,
        rating=str(payload["rating"]),
        conviction=conviction if conviction not in {"", None} else None,
        scan_root=scan.parent,
    )
    code = str(payload["code"]).zfill(6)
    if result.get("updated"):
        print(f"[dossier] δ 回写 {code} → context/knowledge/dossiers/")
    if result.get("issues"):
        print(f"[dossier] ⚠️ 档案 lint:{code} {result['issues']}")
    if result.get("sections_skipped"):
        print(
            "[dossier] ℹ️ §4/§6 跳过刷新(素材缺,保留旧值):"
            f"{code} {result['sections_skipped']}"
        )
    return result


def default_registry() -> dict[str, ConsumerHandler]:
    return {"dossier_delta": _dossier_delta}


def _expected_pairs(
    events: list[OutboxEvent],
    registry: dict[str, ConsumerHandler],
    subscriptions: dict[str, set[str]],
) -> list[tuple[OutboxEvent, str]]:
    pairs = []
    for event in events:
        for consumer in sorted(subscriptions.get(event.event_type, set())):
            if consumer in registry:
                pairs.append((event, consumer))
    return pairs


def _receipt_id(event_id: str, consumer: str) -> str:
    return sha256_json({"event_id": event_id, "consumer": consumer})


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def run_consumers(
    scan_dir: Path | str,
    *,
    registry: dict[str, ConsumerHandler] | None = None,
    subscriptions: dict[str, set[str]] | None = None,
    only: set[str] | None = None,
    retry_failed: bool = False,
) -> ConsumerRunResult:
    scan = Path(scan_dir)
    handlers = registry or default_registry()
    routes = subscriptions or SUBSCRIPTIONS
    events = load_events(outbox_path(scan))
    state_path = initialize_consumer_state(scan)
    receipts = load_consumer_receipts(state_path)
    succeeded = failed = skipped = 0

    for event, consumer in _expected_pairs(events, handlers, routes):
        if only is not None and consumer not in only:
            continue
        receipt_id = _receipt_id(event.event_id, consumer)
        prior = receipts.get(receipt_id)
        if prior is not None and (
            prior.status == "SUCCEEDED"
            or (prior.status == "FAILED" and not retry_failed)
        ):
            skipped += 1
            continue
        attempts = (prior.attempts if prior is not None else 0) + 1
        try:
            handlers[consumer](event, scan)
            receipt = ConsumerReceipt.build(
                event_id=event.event_id,
                consumer=consumer,
                status="SUCCEEDED",
                attempts=attempts,
                error=None,
                updated_at=_now(),
            )
            succeeded += 1
        except Exception as exc:  # noqa: BLE001 — consumers fail independently
            receipt = ConsumerReceipt.build(
                event_id=event.event_id,
                consumer=consumer,
                status="FAILED",
                attempts=attempts,
                error=f"{type(exc).__name__}: {exc}",
                updated_at=_now(),
            )
            failed += 1
        receipts[receipt_id] = receipt
        _write_consumer_receipts(scan, receipts)
    return ConsumerRunResult(
        succeeded=succeeded,
        failed=failed,
        skipped=skipped,
    )


def consumer_status(
    scan_dir: Path | str,
    *,
    registry: dict[str, ConsumerHandler] | None = None,
    subscriptions: dict[str, set[str]] | None = None,
    event_types: set[str] | None = None,
) -> dict:
    scan = Path(scan_dir)
    handlers = registry or default_registry()
    routes = subscriptions or SUBSCRIPTIONS
    events = load_events(outbox_path(scan))
    if event_types is not None:
        events = [
            event for event in events if event.event_type in event_types
        ]
    state_path = consumer_state_path(scan)
    receipts = (
        load_consumer_receipts(state_path)
        if state_path.exists()
        else {}
    )
    pending_consumers = []
    failed_consumers = []
    succeeded = 0
    for event, consumer in _expected_pairs(events, handlers, routes):
        receipt = receipts.get(_receipt_id(event.event_id, consumer))
        if receipt is None:
            pending_consumers.append(consumer)
        elif receipt.status == "FAILED":
            failed_consumers.append(consumer)
        elif receipt.status == "SUCCEEDED":
            succeeded += 1
    pending = len(pending_consumers)
    pending_consumers = sorted(set(pending_consumers))
    failed_consumers = sorted(set(failed_consumers))
    backlog = bool(pending_consumers or failed_consumers)
    return {
        "status": "BACKLOG" if backlog else "OK",
        "n_events": len(events),
        "expected": len(_expected_pairs(events, handlers, routes)),
        "succeeded": succeeded,
        "pending": pending,
        "pending_consumers": pending_consumers,
        "failed_consumers": failed_consumers,
    }


def safe_run_consumers(
    scan_dir: Path | str,
    **kwargs,
) -> ConsumerRunResult | None:
    try:
        return run_consumers(scan_dir, **kwargs)
    except Exception as exc:  # noqa: BLE001 — consumers cannot block publication
        print(f"[post_run] consumer 调度失败: {exc}", file=sys.stderr)
        return None


def _finalize_forensic_run(report_dir: str | None) -> dict:
    """Freeze the run's capsule and report the verdict; never break publishing.

    A run without an active forensic spool (legacy path, or a rerun of an old
    date) simply reports ``skipped`` —— it must not be able to claim a capsule
    it does not have.
    """
    from autoresearch.common import workspace as ws

    run_id = None
    try:
        run_id = ws.active_run_id()
    except ValueError as exc:
        return {"finalized": False, "reason": f"invalid AUTORESEARCH_RUN_ID: {exc}"}
    if not run_id:
        return {"finalized": False, "reason": "no active forensic run"}
    try:
        from autoresearch.trace.capsule import BusinessStatus, finalize, verify

        outcome = finalize(run_id, BusinessStatus.SUCCEEDED, report_dir)
        verdict = verify(run_id, final_path=outcome.final_path)
        return {
            "finalized": True,
            "run_id": run_id,
            "root_hash": outcome.root_hash,
            "evidence_status": outcome.evidence_status.value,
            "durability": outcome.durability,
            "integrity_ok": verdict["integrity_ok"],
            "completeness_ok": verdict["completeness_ok"],
            "replayability": verdict["replayability"],
        }
    except Exception as exc:  # noqa: BLE001 - 冻结失败必须说出来,但不毁掉已发布的报告
        return {
            "finalized": False,
            "run_id": run_id,
            "reason": f"{type(exc).__name__}: {exc}",
        }


def _atomic_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)
    return path


def _load_json(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name} root must be an object")
    return raw


def _run_identity_and_budgets(scan: Path) -> tuple[str, dict | None]:
    path = scan / "run_contract.json"
    if not path.exists():
        return scan.name, None
    from autoresearch.scan.run_contract import load_run_contract

    contract = load_run_contract(path)
    return contract.run_id, contract.stage_budgets


def _effectiveness(scan: Path, estimated_usd: float | None) -> dict:
    """成本分母只读领域事实。

    2026-08-21(用户裁定「整个 learning 层退役」):原来三个分母里有两个
    (`mature_decision_records` / `verified_correct_rejections`)算自
    `retro/rejection_attribution.csv` —— 那个产物已无生产者,留着只会让两行恒为 0、
    把「没量到」渲染成「一次都没对过」。两个分母连同它们的 USD/单位 一并删除,
    只留 `final_buy_candidates`(源自 `decision_records.json`,活的)。
    """
    decisions = {}
    decision_path = scan / "decision_records.json"
    if decision_path.exists():
        from autoresearch.scan.decision_record import load_decision_records

        decisions = load_decision_records(decision_path)
    final_buys = sum(record.proposal == "BUY" for record in decisions.values())
    denominators = {"final_buy_candidates": final_buys}

    def per(name: str) -> float | None:
        denominator = denominators[name]
        if estimated_usd is None or denominator == 0:
            return None
        return round(float(estimated_usd) / denominator, 6)

    return {
        "denominators": denominators,
        "usd_per_final_buy_candidate": per("final_buy_candidates"),
    }


def _money(value: object) -> str:
    return "—" if value is None else f"${float(value):.4f}"


def _degraded_fields(scan: Path) -> list[str]:
    """已落盘的 `run_health.degraded_fields`(读盘、不现算 —— 现算会造第二个口径)。"""
    path = scan / "run_health.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    fields = payload.get("degraded_fields") if isinstance(payload, dict) else None
    return [str(f) for f in fields] if isinstance(fields, list) else []


def render_run_observation(observation: dict) -> str:
    """预算/成本视图；`—` 与 UNMEASURED 永不格式化成零。"""
    maturity = observation.get("maturity") or {}
    effectiveness = observation.get("effectiveness") or {}
    denominators = effectiveness.get("denominators") or {}
    cache = observation.get("cache_hit_rate")
    wall = observation.get("interactive_wall_s")
    weighted = observation.get("weighted_input_proxy")
    weighted_text = "—" if weighted is None else f"{float(weighted):.0f}"
    lines = [
        "## 💸 成本与时延观测",
        "",
        f"- 计量:{observation.get('measurement_status', 'UNMEASURED')} · "
        f"预算状态:{observation.get('status', 'DEGRADED')} · "
        f"晋升证据:{maturity.get('status', 'IMMATURE')}",
        f"- 当前估算成本:{_money(observation.get('estimated_usd'))}"
        "（Claude API 标准公开价估算，不等于实际账单） · "
        + (
            f"cache 命中率:{float(cache):.1%}"
            if cache is not None
            else "cache 命中率:—"
        )
        + " · "
        + (f"交互墙钟:{int(wall)}s" if wall is not None else "交互墙钟:—"),
        f"- 加权输入:{weighted_text} · "
        f"预算带:{observation.get('budget_band') or 'RED'}",
    ]
    if maturity.get("status") in {"PASS", "FAIL"}:
        lines.append(
            f"- {maturity['n_real_scans']} 次真实扫描:成本中位 "
            f"{_money(maturity.get('median_cost_usd'))} · "
            f"墙钟 P50/P90 {maturity.get('p50_minutes', '—')}/"
            f"{maturity.get('p90_minutes', '—')} 分钟 · "
            f"cache 中位 {float(maturity.get('median_cache_hit_rate')):.1%}"
        )
    else:
        lines.append(
            f"- 成熟度:{maturity.get('reason', '未计量')}；"
            f"真实扫描 {maturity.get('n_real_scans', 0)}/"
            f"{maturity.get('required_real_scans', 10)}，不以单次最佳 run 晋升。"
        )
    lines += [
        "",
        "| 成本效率分母 | 数量 | USD/单位 |",
        "|---|---:|---:|",
        f"| 最终 BUY 候选 | {denominators.get('final_buy_candidates', 0)}"
        f" | {_money(effectiveness.get('usd_per_final_buy_candidate'))} |",
    ]
    warnings = observation.get("warnings") or []
    if warnings:
        lines += ["", "> ⚠️ " + "；".join(str(value) for value in warnings)]
    lines += [
        "",
        "_预算只告警/降级，不截断候选、卡片、查询或阶段；0 BUY 不会被成本分母改写。_",
    ]
    return "\n".join(lines)


def _fmt_wall(seconds: object) -> str:
    if seconds is None:
        return "—"
    try:
        total = int(float(seconds))          # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    minutes, secs = divmod(max(total, 0), 60)
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def render_run_observation_line(observation: dict) -> str:
    """summary 侧的**紧凑一行**(§6.5):墙钟 · LLM 调用 · 计量状态 · 数据降级 + 明细链接。

    与 appendix E 的完整块**同一个 observation 对象**渲染 —— 各自重读/重算就是给
    「决策层与现场层说两个数」开口子。这里只印读数,成本表整体住在附录 E。
    未计量的量一律 `—`,**永不格式化成 0**(「没量到」不等于「量到了是零」)。
    """
    calls = observation.get("n_llm_calls")
    cache = observation.get("cache_hit_rate")
    degraded = observation.get("degraded_fields") or []
    cost = _money(observation.get("estimated_usd"))
    weighted = observation.get("weighted_input_proxy")
    weighted_text = "—" if weighted is None else f"{float(weighted):.0f}"
    parts = [
        f"墙钟 {_fmt_wall(observation.get('interactive_wall_s'))}",
        f"LLM 调用 {'—' if calls is None else int(calls)}",
        f"加权输入 {weighted_text}",
        f"预算带:{observation.get('budget_band') or 'RED'}",
        f"计量:{observation.get('measurement_status', 'UNMEASURED')} {cost}"
        + (f"(cache {float(cache):.1%})" if cache is not None else "(cache —)"),
        "数据降级:" + ("、".join(str(f) for f in degraded) if degraded else "无"),
    ]
    return " · ".join(parts) + " → " + appendix_link("运行明细", "runtime")


def _inject_managed(text: str, markdown: str, start: str, end: str,
                    anchor: str) -> tuple[str, str | None]:
    """原位替换一对 managed 标记;标记缺席按**稳定锚**回退,并把回退如实报出来。

    回退不是失败(报告仍然拿到内容),但**必须留痕** —— 静默回退会让「渲染层把标记
    改名了」这种事永远看不见,而 managed 注入 no-op 的病史(仪表盘/组合/overlay 留成
    占位)正出在这里。返回 `(新文本, warn 或 None)`。
    """
    managed = f"{start}\n{markdown.strip()}\n{end}"
    if start in text and end in text:
        before, rest = text.split(start, 1)
        _, after = rest.split(end, 1)
        return before.rstrip() + "\n\n" + managed + after, None
    if anchor in text:
        before, after = text.split(anchor, 1)
        return (before.rstrip() + "\n\n" + managed + "\n" + anchor + after,
                f"{start} 缺席,按稳定锚 `{anchor.strip()}` 回退")
    return (text.rstrip() + "\n\n" + managed + "\n",
            f"{start} 与锚 `{anchor.strip()}` 均缺席,已追加到文末")


def _managed_marker_problem(text: str, start: str, end: str) -> str | None:
    starts, ends = text.count(start), text.count(end)
    if starts != 1 or ends != 1:
        return (
            "managed 标记必须各出现一次"
            f"(begin={starts},end={ends})，报告保持原字节"
        )
    begin, finish = text.index(start), text.index(end)
    if begin >= finish:
        return "managed 标记顺序错误，报告保持原字节"
    return None


def _replace_managed_strict(
    text: str,
    markdown: str,
    start: str,
    end: str,
) -> tuple[str, str | None]:
    """只替换唯一、顺序正确的 managed 块；不做锚点回退或追加。"""
    problem = _managed_marker_problem(text, start, end)
    if problem:
        return text, problem
    begin, finish = text.index(start), text.index(end)
    managed = f"{start}\n{markdown.strip()}\n{end}"
    return text[:begin] + managed + text[finish + len(end):], None


def inject_run_observation_section(summary: str, markdown: str) -> str:
    """summary 的 `run-observation` managed 块(兼容面:签名与返回值不变)。"""
    return _inject_managed(summary, markdown, OBSERVATION_START, OBSERVATION_END,
                           SUMMARY_OBSERVATION_ANCHOR)[0]


def inject_run_observation_detail(appendix: str, markdown: str) -> str:
    """appendix E 的 `run-observation-detail` managed 块(完整成本表)。"""
    return _inject_managed(appendix, markdown, OBSERVATION_DETAIL_START,
                           OBSERVATION_DETAIL_END, APPENDIX_OBSERVATION_ANCHOR)[0]


def observation_after_failure(exc: BaseException) -> dict:
    """观测控制面炸了也要有**一个** observation —— 两处渲染的同源前提不能因异常破掉。

    诚实为 UNMEASURED,并明说这个异常不改变任何评级或候选(展示层故障不许伪装成
    研究结论变化)。
    """
    reason = f"观测控制面异常:{type(exc).__name__}"
    return {
        "measurement_status": "UNMEASURED",
        "status": "DEGRADED",
        "estimated_usd": None,
        "interactive_wall_s": None,
        "cache_hit_rate": None,
        "n_llm_calls": None,
        "degraded_fields": [],
        "warnings": [reason],
        "markdown": ("## 💸 成本与时延观测\n\n"
                     f"- 计量:UNMEASURED · {reason}\n\n"
                     "_未计量不等于零成本；该异常不改变任何评级或候选。_"),
    }


def refresh_run_observation(
    summary_text: str | None,
    appendix_text: str | None,
    observation: dict,
) -> tuple[str | None, str | None, list[str]]:
    """**双刷新**(§6.5):同一个 observation → summary 紧凑一行 + appendix E 完整块。

    返回 `(新 summary, 新 appendix, warns)`;入参为 `None`(文件不在盘上)时对应返回
    `None` 并落 warn —— **不回填**:给一个 2026-08-28 之前的旧 run 凭空造一份 appendix,
    等于拿今天的义务改写昨天的现场。任一边刷不动都必须能被看见,
    「静默只刷新一边」正是本节要拦的形状。已有文件也只认唯一且顺序正确的标记对；
    标记损坏时原文逐字节保留，不使用稳定锚或文末追加。
    """
    warns: list[str] = []
    if summary_text is None:
        warns.append("summary.md 缺席,运行观测未刷新")
        summary_problem = None
        new_summary = None
    else:
        summary_problem = _managed_marker_problem(
            summary_text, OBSERVATION_START, OBSERVATION_END)
        if summary_problem:
            warns.append(f"summary.md:{summary_problem}")
    if appendix_text is None:
        warns.append(f"{APPENDIX_FILENAME} 缺席,运行观测明细未刷新(旧 run 不回填)")
        appendix_problem = None
        new_appendix = None
    else:
        appendix_problem = _managed_marker_problem(
            appendix_text, OBSERVATION_DETAIL_START, OBSERVATION_DETAIL_END)
        if appendix_problem:
            warns.append(f"{APPENDIX_FILENAME}:{appendix_problem}")
    if warns:
        observation["warnings"] = list(dict.fromkeys([
            *(observation.get("warnings") or []), *warns,
        ]))
        observation["status"] = "DEGRADED"
    observation["markdown"] = render_run_observation(observation)
    line_md = render_run_observation_line(observation)
    if summary_text is not None:
        new_summary = summary_text
        if summary_problem is None:
            new_summary, _ = _replace_managed_strict(
                summary_text, line_md, OBSERVATION_START, OBSERVATION_END)
    if appendix_text is not None:
        new_appendix = appendix_text
        if appendix_problem is None:
            new_appendix, _ = _replace_managed_strict(
                appendix_text, observation["markdown"],
                OBSERVATION_DETAIL_START, OBSERVATION_DETAIL_END)
    return new_summary, new_appendix, warns


def refresh_run_observation_files(report_dir: Path | str, observation: dict) -> list[str]:
    """把双刷新落到盘上(两文件各自原子替换);返回 warns。"""
    report = Path(report_dir)
    summary_path, appendix_path = report / "summary.md", report / APPENDIX_FILENAME

    def _read(path: Path) -> str | None:
        return path.read_text(encoding="utf-8") if path.is_file() else None

    old_summary, old_appendix = _read(summary_path), _read(appendix_path)
    new_summary, new_appendix, warns = refresh_run_observation(
        old_summary, old_appendix, observation)
    for path, text, old in (
        (summary_path, new_summary, old_summary),
        (appendix_path, new_appendix, old_appendix),
    ):
        if text is None or text == old:
            continue
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    for warn in warns:
        print(f"[post_run] ⚠️ 运行观测刷新:{warn}", file=sys.stderr)
    return warns


def publish_run_observation(
    scan_dir: Path | str,
    *,
    report_dir: Path | str | None = None,
    usage_path: Path | str | None = None,
    timing_path: Path | str | None = None,
    budgets: dict | None = None,
    real_scan: bool | None = None,
    phase: int = 1,
    decision_write: str = "write",
) -> dict:
    """从 canonical cost/timing JSON 发布观测；不导入也不写任何评级逻辑。

    `decision_write`(P0-2,`docs/research/2026-08-19-decision-file-two-writers-and-
    taskbook-hash.md` §4)—— `_relative_buy_decision.json` 有两个合法调用点(writer-1
    `publisher._run_publish`,brief 之前;writer-2 `post_run observe` CLI,brief 之后),
    显式声明各自要哪种写入语义,**不**从「文件是否已存在」隐式猜:同日重跑 assemble 是
    合法操作,那时文件必须被重写,存在与否分不出这两种场景。
      - `"write"`  —— 现算并原子覆盖(`relative_buy.safe_write_decision`,writer-1 用)。
      - `"verify"` —— 现算并与盘上逐字节比较,相等静默、不等绝不覆盖只留证据+报警
        (`relative_buy.safe_verify_decision`,writer-2 用)。
    非法值立即 `ValueError`——这是调用方的编程契约,不是运行期可以吞掉的异常。
    """
    if decision_write not in {"write", "verify"}:
        raise ValueError(
            "decision_write 必须是 'write' 或 'verify'(P0-2 显式模式参数,不接受隐式推断);"
            f"收到 {decision_write!r}")
    scan = Path(scan_dir)
    usage_file = Path(usage_path) if usage_path else scan / "_token_usage.json"
    timing_file = Path(timing_path) if timing_path else scan / "_stage_timing.json"
    external_warnings: list[str] = []
    try:
        usage = _load_json(usage_file) if usage_file.exists() else {}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        usage = {}
        external_warnings.append(f"成本 JSON 损坏:{type(exc).__name__}")
    try:
        timing = _load_json(timing_file) if timing_file.exists() else {}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        timing = {}
        external_warnings.append(f"时延 JSON 损坏:{type(exc).__name__}")
    run_id, contract_budgets = _run_identity_and_budgets(scan)
    policy = budgets if budgets is not None else contract_budgets
    if real_scan is None:
        real_scan = scan.resolve() == (
            ws.scan_root() / scan.name
        ).resolve()
    from autoresearch.scan.budget import evaluate_history, observe_run

    observation = observe_run(
        scan,
        usage,
        timing,
        budgets=policy,
        run_id=run_id,
        real_scan=real_scan,
    )
    observation["warnings"] = list(dict.fromkeys(
        [*observation["warnings"], *external_warnings]
    ))
    history = []
    for path in sorted(scan.parent.glob("*/_budget_observation.json")):
        try:
            history.append(_load_json(path))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    observation["maturity"] = evaluate_history(
        history,
        phase=phase,
        budgets=policy,
    )
    observation["effectiveness"] = _effectiveness(
        scan,
        observation.get("estimated_usd"),
    )
    # summary 紧凑一行要的两个读数(§4.4 样张):调用次数与降级字段。两者都**只读已在盘上的
    # 事实**,取不到就留 None/[] —— 紧凑行会印 `—`/`无`,不猜。
    observation["n_llm_calls"] = len(usage.get("rows") or []) if usage else None
    observation["degraded_fields"] = _degraded_fields(scan)
    observation["markdown"] = render_run_observation(observation)
    _atomic_json(scan / "_budget_observation.json", observation)
    # E1b(2026-08-18 设计稿):护照/相对决策**现算**之前先对 task-book 做收尾自愈 ——
    # 卡在盘而 book 停 RUNNING 的票按盘上事实补记(recovered 标记),防 08-12 型
    # contract 团灭。失败只打一行,不阻断发布(与护照/决策同一失败纪律)。
    try:
        from autoresearch.scan.l4_tasks import reconcile
        _rec = reconcile(scan / "_l4_tasks.json")
        if _rec.get("recovered"):
            print(f"[l4_tasks] reconcile 补记 {len(_rec['recovered'])} 票: "
                  f"{','.join(_rec['recovered'])}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[l4_tasks] reconcile 失败: {type(exc).__name__}: {exc}", file=sys.stderr)
    # 候选护照(Wave12 T19):L1→L4 全轨迹的**纯派生**视图,零 LLM/零联网、byte 稳定。
    # 挂在这里是因为 post_run observe 是 STAGES 步骤 5 的最后一条命令(assemble → gate4 →
    # usage_harvest → usage_reconcile → 本命令),此刻 decision_records/早停/intel 状态
    # 全部已定稿。失败只打一行:护照没有任何上游依赖它,不能反过来阻断发布。
    from autoresearch.scan.passport import safe_write_passport

    safe_write_passport(scan)
    # 统一相对决策层 finalizer(Wave12 T23/T24,E6):**同点**挂在护照之后 —— 它现算护照
    # (`build_passport`)再判四门四面,所以必须等 decision_records/早停/intel 全部定稿,
    # 与护照是同一个时刻的两个派生视图。生产默认仍是**影子**(mode=shadow):只写
    # `_relative_buy_decision.json`,不写 buy ledger、不改 publisher、不碰
    # decision_records;失败只打一行(`safe_write_decision`/`safe_verify_decision` 自带),
    # 不能反过来阻断发布。(前向观测账本 `relative_ledger` 已于 2026-08-21
    # 随 learning 层退役删除。)P0-2:`decision_write` 显式选写入语义 —— write 原子覆盖
    # (writer-1);verify 现算校验,不一致时绝不覆盖、只留证据+报警(writer-2)。
    #
    # E6 转正瘦身波 task-2.2(2026-08-19):mode/exclude_pinned 从 `scan_config.jsonc` 的
    # `relative_buy` 块解析(缺文件/缺块 = shadow/False = 内建默认,与代码形参默认一致 =
    # parity)——**两个写者必须用同一份开关**:writer-1 若按 config 用 active 算、
    # writer-2 却永远拿 shadow 去比,"两次现算是否一致"就会天天误报。配置层故障(文件坏/
    # 白名单外键)不得阻断影子决策发布,但降级必须留痕(同 `user_config.knob` 纪律)。
    #
    # task-2.4:解析下沉到 `relative_buy.configured_relative_buy()`(消费侧同一入口)——
    # 写者与消费者用两份各自解析的 config,就会出现「渲染层以为在 shadow、写者按 active 写」
    # 这种半开状态,那是本波要防的分家的另一种形状。
    from autoresearch.scan.relative_buy import configured_relative_buy

    _rb_mode, _rb_exclude_pinned, _, _rb_pool = configured_relative_buy()
    if decision_write == "write":
        from autoresearch.scan.relative_buy import safe_write_decision

        safe_write_decision(scan, mode=_rb_mode, exclude_pinned=_rb_exclude_pinned,
                            pool=_rb_pool)
    else:
        from autoresearch.scan.relative_buy import safe_verify_decision

        safe_verify_decision(scan, mode=_rb_mode, exclude_pinned=_rb_exclude_pinned,
                             pool=_rb_pool)
    report = Path(report_dir) if report_dir is not None else None
    if report is not None:
        # 先刷新报告。标记损坏会把同一 observation 降级；该事实必须先写回预算产物，
        # 再由 run_health / artifact index / trace / retention 依次读取最终态。
        refresh_run_observation_files(report, observation)
        _atomic_json(scan / "_budget_observation.json", observation)

    from autoresearch.scan.stage_result import safe_record_stage_result

    safe_record_stage_result(
        scan,
        stage="budget",
        status=observation["status"],
        artifacts=["budget_observation"],
        metrics={
            "truncated": False,
            "measurement_status": observation["measurement_status"],
            "weighted_input_proxy": observation["weighted_input_proxy"],
            "budget_band": observation["budget_band"],
            "estimated_usd": observation["estimated_usd"],
            "interactive_wall_s": observation["interactive_wall_s"],
            "cache_hit_rate": observation["cache_hit_rate"],
            "maturity_status": observation["maturity"]["status"],
            "denominators": observation["effectiveness"]["denominators"],
        },
        warnings=observation["warnings"],
        error=None,
    )
    if report is not None:
        # 最终态字节预算:这一次才是验收读数(token 计量此刻才到)。展示层 warn,不截断。
        with contextlib.suppress(Exception):
            from autoresearch.scan.health import measure_report_budget, write_run_health

            measure_report_budget(scan, report)
            write_run_health(scan)
        from autoresearch.scan.artifacts import write_artifact_index

        index = write_artifact_index(scan, report_dir=report)
        trace = report / "trace"
        trace.mkdir(parents=True, exist_ok=True)
        shutil.copy2(index, trace / "artifact_index.json")
        shutil.copy2(
            scan / "_budget_observation.json",
            trace / "_budget_observation.json",
        )
        # 现场留存复跑(2026-08-26 §4 R1/R5):observe 是**最后一个**写 run 目录的步骤 ——
        # 它刚写完 token_usage.md / 刷新了 artifact_index 与 _budget_observation,而
        # `_token_usage.json` 也是此刻才在 staging 里出现。发布时那次镜像看不到它们,
        # 所以这里再镜像一次并**重写清单**(否则清单会把 observe 自己的产物报成 extra)。
        # 幂等:同输入两次调用结果一致。失败只留痕,不改变 observe 的返回。
        with contextlib.suppress(Exception):
            from autoresearch.scan.retention import retain

            ret = retain(scan, report)
            if ret["errors"]:
                print(f"[post_run] ⚠️ 现场留存:{'; '.join(ret['errors'])}", file=sys.stderr)
    return observation


def _pool_path() -> Path:
    from autoresearch.dossier import pool

    return Path(pool.POOL_PATH)


def _pending_entry_code(entry: object) -> tuple[str, dict] | None:
    """把 `pending_init` 数组里的一条既有条目规整成 (code6, dict)。

    既有条目可能是纯字符串(如 `"600018"`),不是本波才有的 dict 形态(Wave9 R6 歧义3)——
    两种形态都要容忍:既不能崩溃,也不能在刷新时把它悄悄弄丢。返回的 dict 对纯字符串条目
    是空 `{}`(调用方据此判断"要不要就地升级成 dict"，而不是直接 `.get()` 崩在 str 上)。
    """
    if isinstance(entry, dict):
        raw = str(entry.get("code", "") or "").strip()
        meta: dict = entry
    elif isinstance(entry, str):
        raw = entry.strip()
        meta = {}
    else:
        return None
    if not raw:
        return None
    return raw.split(".")[0].zfill(6), meta


def enqueue_finalist_dossiers(scan_dir: Path | str, analysis_date: str) -> list[str]:
    """当日无档案的 finalist 插队进建档队列(Wave9 R6,B-3 续)。

    总帽 ≤3 只/晚**不变**——该帽子本来就不是代码强制的,是
    `.claude/skills/scan-market/SKILL.md`/`STAGES.md` 里人工逐票派发 `dossier-init.js` 的既定
    约定(`pool.py` 从未有任何 `[:3]` 式硬切片);本函数只改**顺序**:高频入围票先覆盖,下次
    再遇即有料。幂等:已在队列的票只刷新 `last_seen`,不重复入队。

    `lane == "pinned"`(持仓强制注入 finalists.csv,不代表 L3 真选)不算这里的"入围"——与
    `autoresearch.dossier.pool._selections()` 的 `lane≠pinned` 口径保持一致;把持仓错标成
    "finalist" 优先没有意义(持仓本就通过 pinned 即入池的路径拿到 pending_init 曝光)。

    `_dossier_present.json` 缺失、损坏、或**语法合法但形状不对**(如内容是一个 JSON 字符串
    或对象而不是列表):视为"无法判定谁已有档案",保守跳过(返回 `[]`,不写入任何条目)——
    插队只是 advisory 排序优化,过度激进(把当日全部 finalist 都当"无档案"插队)会把整条
    队列的既有优先级搅乱,详见 task-9-report.md 歧义2。

    形状检查必须显式做(`isinstance(present_raw, list)`):`set(json.loads(...))` 对"合法 JSON
    但不是 list"这类输入**不会抛异常**——`set("000651 already有")` 按字符拆、`set({"a": 1})`
    按 key 拆,两者都会产出语义完全错误却"看似正常"的集合,静默复活上面这段要避免的激进
    分支(复核 Important-1,见 task-9-report.md「FIX-1」)。

    列表内部分元素类型不是 `str`(如 `[651, None]`):逐个丢弃、不做整数→code 的猜测式强转,
    不因为个别坏元素让其余已确认为字符串的合法条目也被牵连——每个字符串元素各自独立地
    断言"这个 code 有档案",丢弃坏元素只是少一条断言,不是推翻其它断言。
    """
    sd = Path(scan_dir)
    try:
        present_raw = json.loads((sd / "_dossier_present.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(present_raw, list):
        return []
    present = {str(e).split(".")[0].zfill(6) for e in present_raw if isinstance(e, str)}

    codes: list[str] = []
    with contextlib.suppress(Exception), (sd / "finalists.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = str(row.get("code", "") or "").strip()
            if not raw or str(row.get("lane", "") or "").strip() == "pinned":
                continue
            codes.append(raw.split(".")[0].zfill(6))

    # 去重(同码可能在 finalists.csv 里以多个 lane 行出现),保留原序
    want = [c for c in dict.fromkeys(codes) if c not in present]
    if not want:
        return []

    p = _pool_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []

    pend = data.setdefault("pending_init", [])
    if not isinstance(pend, list):
        return []

    # Wave9 final-fix I-1 附带项(final-review 提):已建档的排队条目会永久占位、数组
    # 无限累积(`pending_init()` 靠 `dossier_path().exists()` 在读时把它们过滤掉,但
    # 写侧从不清理)。每次入队顺手扫一遍摘掉"确认已建档"的条目;认不出的元素(既不是
    # dict 也不是非空 str)保守保留,不因看不懂形态就丢数据。
    from autoresearch.dossier import (
        schema as _schema,  # lazy:与本文件其它 dossier 子模块导入同款风格
    )
    pend[:] = [e for e in pend
               if (parsed := _pending_entry_code(e)) is None
               or not _schema.dossier_path(parsed[0]).exists()]

    have_idx: dict[str, int] = {}
    for i, e in enumerate(pend):
        parsed = _pending_entry_code(e)
        if parsed is not None:
            have_idx[parsed[0]] = i

    added: list[str] = []
    for c in want:
        i = have_idx.get(c)
        if i is None:
            pend.append({"code": c, "priority": "finalist", "last_seen": analysis_date})
            have_idx[c] = len(pend) - 1
            added.append(c)
            continue
        existing = pend[i]
        if isinstance(existing, dict):
            existing["last_seen"] = analysis_date
            existing.setdefault("priority", "finalist")
            existing.setdefault("code", c)
        else:
            # 既有纯字符串条目——就地升级成 dict,不重复追加、不丢原有位置(歧义3)
            pend[i] = {"code": c, "priority": "finalist", "last_seen": analysis_date}

    _atomic_json(p, data)
    return added


def enqueue_receipt(scan_dir: Path | str, analysis_date: str) -> dict:
    """建档插队的**三数回执**(Wave10 A9):`requested / inserted / newly_visible`。

    为什么必须是三个数:Wave9 I-1 的病灶正是「写进去了 ≠ 消费者看得见」——
    `enqueue_finalist_dossiers` 往 `pending_init` 数组里写,而 `pool.pending_init()`
    的候选集当时只看 `stocks`,于是回执报「新增 N 只」而消费者可见新增 0 只。
    一个数说不清这件事:**报告只把 `newly_visible` 称「新增可见」**,另两个数用来
    定位断在哪一段(想插几只 → 真写进去几只 → 消费者真看见几只)。

    不改 `enqueue_finalist_dossiers` 的 `list[str]` 返回契约(20 个用例锁着它),
    本函数是它的旁路读数。
    """
    from autoresearch.dossier import pool as _pool

    sd = Path(scan_dir)
    before = set(_pool.pending_init(_pool.load_pool()))
    inserted = enqueue_finalist_dossiers(sd, analysis_date)
    after = set(_pool.pending_init(_pool.load_pool()))

    requested: list[str] = []
    with contextlib.suppress(Exception), (sd / "finalists.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = str(row.get("code", "") or "").strip()
            if raw and str(row.get("lane", "") or "").strip() != "pinned":
                requested.append(raw.split(".")[0].zfill(6))
    return {
        "requested": len(dict.fromkeys(requested)),
        "inserted": len(inserted),
        "newly_visible": len(after - before),
        "inserted_codes": inserted,
    }


def _resolve_scan(value: str) -> Path:
    explicit = Path(value)
    return explicit if explicit.exists() else ws.scan_root() / value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="post-run consumer replay")
    parser.add_argument("scan", help="analysis date or scan directory")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--consumer", action="append", default=[])
    run_parser.add_argument("--retry-failed", action="store_true")
    observe_parser = sub.add_parser("observe")
    observe_parser.add_argument("--usage", default=None)
    observe_parser.add_argument("--timing", default=None)
    observe_parser.add_argument("--report-dir", default=None)
    observe_parser.add_argument("--phase", type=int, choices=[1, 2], default=1)
    args = parser.parse_args(argv)
    scan = _resolve_scan(args.scan)
    try:
        if args.command == "status":
            result = consumer_status(scan)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "observe":
            # P0-2:CLI `observe` 是 writer-2(STAGES 步骤 5 最后一条命令,brief 落盘之后)——
            # 必须传 verify,不能重犯「无条件原子重写」那个旧毛病(§4 P0-2)。
            result = publish_run_observation(
                scan,
                report_dir=args.report_dir,
                usage_path=args.usage,
                timing_path=args.timing,
                phase=args.phase,
                decision_write="verify",
            )
            # CP7 定序(设计稿 §9):gate4 → 引擎感知计量 → usage_reconcile → observe →
            # expected/replay/completeness → finalize → 冻结后复验。finalize 必须在
            # observe **之后**:它要把此刻定稿的观测一起冻进 capsule。
            finalization = _finalize_forensic_run(args.report_dir)
            with contextlib.suppress(Exception):  # 插队建档失败不挡成本观测发布(Wave9 R6)
                receipt = enqueue_receipt(scan, scan.name)
                queued = receipt["inserted_codes"]
                if queued:
                    print(f"[dossier] 插队 {len(queued)} 只:{', '.join(queued)}")
            print(json.dumps({
                "ok": True,
                "status": result["status"],
                "measurement_status": result["measurement_status"],
                "maturity": result["maturity"],
                "observation": str(scan / "_budget_observation.json"),
                "capsule": finalization,
            }, ensure_ascii=False, sort_keys=True))
            return 0
        result = run_consumers(
            scan,
            only=set(args.consumer) or None,
            retry_failed=bool(args.retry_failed),
        )
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
        return 1 if result.failed else 0
    except Exception as exc:  # noqa: BLE001 — CLI emits one structured error
        print(
            json.dumps(
                {"status": "INVALID", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

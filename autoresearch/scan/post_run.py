#!/usr/bin/env python3
"""Idempotent local consumers for post-run outbox events."""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import shutil
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.scan.outbox import OutboxEvent, load_events, outbox_path
from autoresearch.scan.run_contract import sha256_json

CONSUMER_RECEIPT_SCHEMA_VERSION = 1
CONSUMER_STATE_SCHEMA_VERSION = 1
RECEIPT_STATUSES = {"SUCCEEDED", "FAILED", "SKIPPED"}
SUBSCRIPTIONS = {
    "RUN_FINALIZED": {
        "journal",
        "buy_ledger",
        "zero_buy_ledger",
        "paper_nav",
        "gate_ledger",
        "earlystop_ledger",
        "pinned_ledger",
        "precedents",
    },
    "RETRO_FINALIZED": {
        "abstention_ledger",
        "cross_calib",
        "earlystop_shadow",
        "ensemble_ledger",
        "gate_ledger",
        "l3_audit_ledger",
        "zero_buy_ledger",
    },
    "DOSSIER_DELTA_READY": {"dossier_delta"},
}
ConsumerHandler = Callable[[OutboxEvent, Path], object]
OBSERVATION_START = "<!-- run-observation:start -->"
OBSERVATION_END = "<!-- run-observation:end -->"


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


def _module_main(module_name: str) -> ConsumerHandler:
    def _handler(_event: OutboxEvent, _scan: Path) -> object:
        module = importlib.import_module(f"autoresearch.learning.{module_name}")
        return module.main()

    return _handler


def _precedents(_event: OutboxEvent, _scan: Path) -> object:
    from autoresearch.learning.precedents import build_index

    return build_index()


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
    names = (
        "abstention_ledger",
        "journal",
        "buy_ledger",
        "cross_calib",
        "zero_buy_ledger",
        "paper_nav",
        "gate_ledger",
        "earlystop_ledger",
        "earlystop_shadow",
        "ensemble_ledger",
        "l3_audit_ledger",
        "pinned_ledger",
    )
    return {
        **{name: _module_main(name) for name in names},
        "precedents": _precedents,
        "dossier_delta": _dossier_delta,
    }


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
    except Exception as exc:  # noqa: BLE001 — learning cannot block publication
        print(f"[post_run] consumer 调度失败: {exc}", file=sys.stderr)
        return None


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


def _bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _effectiveness(scan: Path, estimated_usd: float | None) -> dict:
    """成本分母只读领域事实；缺成熟前向样本时诚实为零/`None`。"""
    decisions = {}
    decision_path = scan / "decision_records.json"
    if decision_path.exists():
        from autoresearch.scan.decision_record import load_decision_records

        decisions = load_decision_records(decision_path)
    final_buys = sum(record.proposal == "BUY" for record in decisions.values())
    mature_codes: set[str] = set()
    correct_rejections = 0
    retro_path = scan / "retro" / "rejection_attribution.csv"
    if retro_path.exists():
        with retro_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                code = str(row.get("code") or "").zfill(6)
                mature = _bool(row.get("mature"))
                if mature and code in decisions:
                    mature_codes.add(code)
                try:
                    excess = float(row.get("excess_2") or "")
                except (TypeError, ValueError):
                    excess = None
                if (
                    mature
                    and _bool(row.get("buyable"))
                    and row.get("first_rejection_stage") != "BOUGHT"
                    and excess is not None
                    and excess <= -0.02
                ):
                    correct_rejections += 1
    denominators = {
        "mature_decision_records": len(mature_codes),
        "final_buy_candidates": final_buys,
        "verified_correct_rejections": correct_rejections,
    }

    def per(name: str) -> float | None:
        denominator = denominators[name]
        if estimated_usd is None or denominator == 0:
            return None
        return round(float(estimated_usd) / denominator, 6)

    return {
        "denominators": denominators,
        "usd_per_mature_decision_record": per("mature_decision_records"),
        "usd_per_final_buy_candidate": per("final_buy_candidates"),
        "usd_per_verified_correct_rejection": per(
            "verified_correct_rejections"
        ),
        "verified_correct_rejection_definition": (
            "mature & buyable & rejected & excess_2<=-2pp"
        ),
    }


def _money(value: object) -> str:
    return "—" if value is None else f"${float(value):.4f}"


def render_run_observation(observation: dict) -> str:
    """预算/成本视图；`—` 与 UNMEASURED 永不格式化成零。"""
    maturity = observation.get("maturity") or {}
    effectiveness = observation.get("effectiveness") or {}
    denominators = effectiveness.get("denominators") or {}
    cache = observation.get("cache_hit_rate")
    wall = observation.get("interactive_wall_s")
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
        f"| 成熟 DecisionRecord | {denominators.get('mature_decision_records', 0)}"
        f" | {_money(effectiveness.get('usd_per_mature_decision_record'))} |",
        f"| 最终 BUY 候选 | {denominators.get('final_buy_candidates', 0)}"
        f" | {_money(effectiveness.get('usd_per_final_buy_candidate'))} |",
        f"| 已验证正确拒绝 | {denominators.get('verified_correct_rejections', 0)}"
        f" | {_money(effectiveness.get('usd_per_verified_correct_rejection'))} |",
    ]
    warnings = observation.get("warnings") or []
    if warnings:
        lines += ["", "> ⚠️ " + "；".join(str(value) for value in warnings)]
    lines += [
        "",
        "_预算只告警/降级，不截断候选、卡片、查询或阶段；0 BUY 不会被成本分母改写。_",
    ]
    return "\n".join(lines)


def inject_run_observation_section(summary: str, markdown: str) -> str:
    managed = f"{OBSERVATION_START}\n{markdown.strip()}\n{OBSERVATION_END}"
    if OBSERVATION_START in summary and OBSERVATION_END in summary:
        before, rest = summary.split(OBSERVATION_START, 1)
        _, after = rest.split(OBSERVATION_END, 1)
        return before.rstrip() + "\n\n" + managed + after
    marker = "\n## 诚实局限"
    if marker in summary:
        before, after = summary.split(marker, 1)
        return before.rstrip() + "\n\n" + managed + "\n" + marker + after
    return summary.rstrip() + "\n\n" + managed + "\n"


def publish_run_observation(
    scan_dir: Path | str,
    *,
    report_dir: Path | str | None = None,
    usage_path: Path | str | None = None,
    timing_path: Path | str | None = None,
    budgets: dict | None = None,
    real_scan: bool | None = None,
    phase: int = 1,
) -> dict:
    """从 canonical cost/timing JSON 发布观测；不导入也不写任何评级逻辑。"""
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
            Path("context/scan") / scan.name
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
    observation["markdown"] = render_run_observation(observation)
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
            "estimated_usd": observation["estimated_usd"],
            "interactive_wall_s": observation["interactive_wall_s"],
            "cache_hit_rate": observation["cache_hit_rate"],
            "maturity_status": observation["maturity"]["status"],
            "denominators": observation["effectiveness"]["denominators"],
        },
        warnings=observation["warnings"],
        error=None,
    )
    if report_dir is not None:
        report = Path(report_dir)
        summary = report / "summary.md"
        if summary.exists():
            summary.write_text(
                inject_run_observation_section(
                    summary.read_text(encoding="utf-8"),
                    observation["markdown"],
                ),
                encoding="utf-8",
            )
        from autoresearch.scan.artifacts import write_artifact_index

        index = write_artifact_index(scan, report_dir=report)
        trace = report / "trace"
        trace.mkdir(parents=True, exist_ok=True)
        shutil.copy2(index, trace / "artifact_index.json")
        shutil.copy2(
            scan / "_budget_observation.json",
            trace / "_budget_observation.json",
        )
    return observation


def _resolve_scan(value: str) -> Path:
    explicit = Path(value)
    return explicit if explicit.exists() else Path("context/scan") / value


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
            result = publish_run_observation(
                scan,
                report_dir=args.report_dir,
                usage_path=args.usage,
                timing_path=args.timing,
                phase=args.phase,
            )
            print(json.dumps({
                "ok": True,
                "status": result["status"],
                "measurement_status": result["measurement_status"],
                "maturity": result["maturity"],
                "observation": str(scan / "_budget_observation.json"),
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

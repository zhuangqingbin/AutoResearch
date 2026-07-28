#!/usr/bin/env python3
"""Idempotent local consumers for post-run outbox events."""
from __future__ import annotations

import argparse
import importlib
import json
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
    "DOSSIER_DELTA_READY": {"dossier_delta"},
}
ConsumerHandler = Callable[[OutboxEvent, Path], object]


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
    return record_scan_delta(
        str(payload["code"]).zfill(6),
        event.analysis_date,
        rating=str(payload["rating"]),
        conviction=conviction if conviction not in {"", None} else None,
        scan_root=scan.parent,
    )


def default_registry() -> dict[str, ConsumerHandler]:
    names = (
        "journal",
        "buy_ledger",
        "zero_buy_ledger",
        "paper_nav",
        "gate_ledger",
        "earlystop_ledger",
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
) -> dict:
    scan = Path(scan_dir)
    handlers = registry or default_registry()
    routes = subscriptions or SUBSCRIPTIONS
    events = load_events(outbox_path(scan))
    state_path = initialize_consumer_state(scan)
    receipts = load_consumer_receipts(state_path)
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
    pending_consumers = sorted(set(pending_consumers))
    failed_consumers = sorted(set(failed_consumers))
    backlog = bool(pending_consumers or failed_consumers)
    return {
        "status": "BACKLOG" if backlog else "OK",
        "n_events": len(events),
        "expected": len(_expected_pairs(events, handlers, routes)),
        "succeeded": succeeded,
        "pending": len(pending_consumers),
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
    args = parser.parse_args(argv)
    scan = _resolve_scan(args.scan)
    try:
        if args.command == "status":
            result = consumer_status(scan)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
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

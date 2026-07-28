#!/usr/bin/env python3
"""Local post-run event facts with stable IDs and atomic persistence."""
from __future__ import annotations

import csv
import json
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from autoresearch.scan.decision_read_model import read_decisions
from autoresearch.scan.run_contract import load_run_contract, sha256_json
from autoresearch.scan.stage_result import load_stage_result

EVENT_SCHEMA_VERSION = 1
OUTBOX_SCHEMA_VERSION = 1
EVENT_TYPES = {
    "RUN_FINALIZED",
    "DECISION_FINALIZED",
    "GATE_FAILED",
    "EARLY_STOPPED",
    "DOSSIER_DELTA_READY",
}


@dataclass(frozen=True)
class OutboxEvent:
    schema_version: int
    event_id: str
    event_type: str
    analysis_date: str
    run_id: str | None
    contract_hash: str | None
    aggregate_id: str
    payload: dict
    created_at: str
    event_hash: str

    def _identity_payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "event_type": self.event_type,
            "analysis_date": self.analysis_date,
            "run_id": self.run_id,
            "contract_hash": self.contract_hash,
            "aggregate_id": self.aggregate_id,
            "payload": self.payload,
        }

    def _hash_payload(self) -> dict:
        payload = asdict(self)
        payload.pop("event_hash")
        return payload

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def build(
        cls,
        *,
        event_type: str,
        analysis_date: str,
        run_id: str | None,
        contract_hash: str | None,
        aggregate_id: str,
        payload: dict,
        created_at: str,
    ) -> OutboxEvent:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"invalid event_type: {event_type!r}")
        aggregate_id = str(aggregate_id).strip()
        if not aggregate_id:
            raise ValueError("event aggregate_id must not be empty")
        normalized_payload = json.loads(
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
        base = cls(
            schema_version=EVENT_SCHEMA_VERSION,
            event_id="",
            event_type=event_type,
            analysis_date=str(analysis_date),
            run_id=None if run_id is None else str(run_id),
            contract_hash=(
                None if contract_hash is None else str(contract_hash)
            ),
            aggregate_id=aggregate_id,
            payload=normalized_payload,
            created_at=str(created_at),
            event_hash="",
        )
        identified = replace(
            base,
            event_id=sha256_json(base._identity_payload()),
        )
        return replace(
            identified,
            event_hash=sha256_json(identified._hash_payload()),
        )

    @classmethod
    def from_dict(cls, raw: dict) -> OutboxEvent:
        event = cls(**raw)
        if event.schema_version != EVENT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported outbox event schema_version={event.schema_version}"
            )
        rebuilt = cls.build(
            event_type=event.event_type,
            analysis_date=event.analysis_date,
            run_id=event.run_id,
            contract_hash=event.contract_hash,
            aggregate_id=event.aggregate_id,
            payload=event.payload,
            created_at=event.created_at,
        )
        if event.event_id != rebuilt.event_id:
            raise ValueError("outbox event_id hash mismatch")
        if event.event_hash != rebuilt.event_hash:
            raise ValueError("outbox event hash mismatch")
        return event


def outbox_path(scan_dir: Path | str) -> Path:
    return Path(scan_dir) / "outbox" / "events.json"


def _sort_key(event: OutboxEvent) -> tuple[str, str, str]:
    return event.event_type, event.aggregate_id, event.event_id


def load_events(path: Path | str) -> list[OutboxEvent]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != OUTBOX_SCHEMA_VERSION:
        raise ValueError("unsupported outbox book")
    rows = raw.get("events")
    if not isinstance(rows, list) or raw.get("events_hash") != sha256_json(rows):
        raise ValueError("outbox book events hash mismatch")
    events = [OutboxEvent.from_dict(row) for row in rows]
    if len({event.event_id for event in events}) != len(events):
        raise ValueError("outbox book duplicate event_id")
    return sorted(events, key=_sort_key)


def emit_events(
    scan_dir: Path | str,
    events: list[OutboxEvent],
) -> Path:
    scan = Path(scan_dir)
    target = outbox_path(scan)
    existing = load_events(target) if target.exists() else []
    merged = {event.event_id: event for event in existing}
    for event in events:
        if event.analysis_date != scan.name:
            raise ValueError("outbox event analysis_date mismatch")
        merged.setdefault(event.event_id, event)
    ordered = sorted(merged.values(), key=_sort_key)
    payloads = [event.to_dict() for event in ordered]
    book = {
        "schema_version": OUTBOX_SCHEMA_VERSION,
        "analysis_date": scan.name,
        "events": payloads,
        "events_hash": sha256_json(payloads),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(book, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def _identity(scan: Path) -> tuple[str | None, str | None, str]:
    path = scan / "run_contract.json"
    if not path.exists():
        return None, None, f"{scan.name}T00:00:00Z"
    contract = load_run_contract(path)
    return contract.run_id, contract.contract_hash, contract.created_at


def _convictions(scan: Path) -> dict[str, str]:
    path = scan / "finalists.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return {
            str(row.get("code", "")).zfill(6): str(
                row.get("conviction", "")
            )
            for row in csv.DictReader(handle)
            if row.get("code")
        }


def build_finalization_events(
    scan_dir: Path | str,
) -> list[OutboxEvent]:
    scan = Path(scan_dir)
    records = read_decisions(scan)
    run_id, contract_hash, created_at = _identity(scan)
    convictions = _convictions(scan)

    def _event(event_type: str, aggregate_id: str, payload: dict) -> OutboxEvent:
        return OutboxEvent.build(
            event_type=event_type,
            analysis_date=scan.name,
            run_id=run_id,
            contract_hash=contract_hash,
            aggregate_id=aggregate_id,
            payload=payload,
            created_at=created_at,
        )

    events = [
        _event(
            "RUN_FINALIZED",
            run_id or scan.name,
            {
                "n_buys": sum(
                    record.final_rating in {"Buy", "Overweight"}
                    for record in records.values()
                ),
                "n_decisions": len(records),
            },
        )
    ]
    for code, record in sorted(records.items()):
        facts = {
            "code": code,
            "final_rating": record.final_rating,
            "first_rejection_stage": record.first_rejection_stage,
            "proposal": record.proposal,
            "reason": record.reason,
            "record_hash": record.record_hash,
        }
        events.append(_event("DECISION_FINALIZED", code, facts))
        if record.early_stop is not None:
            events.append(
                _event(
                    "EARLY_STOPPED",
                    code,
                    {"code": code, **record.early_stop},
                )
            )
        if record.final_rating != "—":
            events.append(
                _event(
                    "DOSSIER_DELTA_READY",
                    code,
                    {
                        "code": code,
                        "conviction": convictions.get(code, ""),
                        "rating": record.final_rating,
                    },
                )
            )

    stage_dir = scan / "stage_results"
    for path in sorted(stage_dir.glob("gate*.json")) if stage_dir.is_dir() else []:
        result = load_stage_result(path)
        if result.status == "FAILED":
            events.append(
                _event(
                    "GATE_FAILED",
                    result.stage,
                    {
                        "error": result.error,
                        "result_hash": result.result_hash,
                        "stage": result.stage,
                    },
                )
            )
    return sorted(events, key=_sort_key)


def safe_emit_finalization_events(scan_dir: Path | str) -> Path | None:
    try:
        return emit_events(
            scan_dir,
            build_finalization_events(scan_dir),
        )
    except Exception as exc:  # noqa: BLE001 — post-run facts cannot block report
        print(f"[outbox] 写入失败: {exc}", file=sys.stderr)
        return None

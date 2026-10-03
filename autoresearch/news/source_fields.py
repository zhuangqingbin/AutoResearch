"""Closed, replayable field adapters over original frozen SourceReceipt payloads.

The root service owns permission and issuance. Provider strings and model JSON
cannot issue reviews. Consumers recompute fields and require the root event.
Repurchase v1 uses Tushare's documented default columns; unsupported predicates,
ambiguous rows and unknown public timing remain unverified.
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.claim_evidence import validate_event
from autoresearch.contracts.execution import parse_aware, validate_decision_frame
from autoresearch.contracts.source_receipt import validate_source_receipt
from autoresearch.contracts.source_time import latest_possible
from autoresearch.trace.blobs import blob_path

ADAPTER_ID = "tushare.repurchase.v1"
PRODUCER_VERSION = "claim_fields.root.v1"
_SELECTOR = frozenset({"ts_code", "ann_date", "end_date"})
_COLUMNS = frozenset(
    {
        "ts_code",
        "ann_date",
        "end_date",
        "proc",
        "exp_date",
        "vol",
        "amount",
        "high_limit",
        "low_limit",
    }
)
_REGISTRY = {ADAPTER_ID: ("tushare", "repurchase", "dataframe.parquet.v1", _COLUMNS)}
_PHASES = {
    "董事会预案": "plan",
    "股东大会通过": "plan",
    "实施": "in_progress",
    "完成": "completed",
    "停止实施": "terminated",
}


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError("source date must be YYYYMMDD")
    return datetime.strptime(value, "%Y%m%d").date()


def _interval(value):
    day = _day(value)
    return {
        "start": f"{day.isoformat()}T00:00:00+08:00",
        "end": f"{(day + timedelta(days=1)).isoformat()}T00:00:00+08:00",
        "precision": "day",
    }


def checked_payload(root: Path, receipt: dict) -> bytes:
    validate_source_receipt(receipt)
    payload = None
    for key in ("payload_hash", "raw_hash"):
        digest = receipt[key]
        if digest is None:
            continue
        path = blob_path(root, digest)
        if path.is_symlink():
            raise ValueError("source payload must not be a symlink")
        raw = path.read_bytes()
        if sha256_bytes(raw) != digest:
            raise ValueError("source payload hash mismatch")
        if key == "payload_hash":
            payload = raw
    return payload


def _table(receipt, raw, adapter_id):
    validate_source_receipt(receipt)
    registration = _REGISTRY.get(adapter_id)
    if (
        registration is None
        or tuple(receipt[key] for key in ("provider", "endpoint", "codec")) != registration[:3]
    ):
        raise ValueError("unregistered source provider/endpoint/codec/adapter")
    if receipt["status"] != "SUCCEEDED" or receipt.get("source_status", "CURRENT") != "CURRENT":
        raise ValueError("source is not current and successful")
    if sha256_bytes(raw) != receipt["payload_hash"]:
        raise ValueError("source payload hash mismatch")
    try:
        table = pd.read_parquet(io.BytesIO(raw))
    except Exception as exc:
        raise ValueError("invalid typed source payload") from exc
    if set(table.columns) != registration[3] or len(table.columns) != len(registration[3]):
        raise ValueError("unregistered source column schema")
    return table


def derive_fields(receipt, raw: bytes, adapter_id: str, selector: dict) -> tuple[dict, dict]:
    """Pure typed projection. The source hash always names the original receipt bytes."""
    if not isinstance(selector, dict) or set(selector) != _SELECTOR:
        raise ValueError("unregistered row selector")
    if not isinstance(selector["ts_code"], str) or not re.fullmatch(
        r"\d{6}\.(SH|SZ|BJ)", selector["ts_code"]
    ):
        raise ValueError("invalid selector subject")
    _day(selector["ann_date"])
    if selector["end_date"] is not None:
        _day(selector["end_date"])
    table = _table(receipt, raw, adapter_id)
    matches = []
    for index, row in enumerate(table.to_dict("records")):
        if all(
            (None if pd.isna(row[key]) else row[key]) == expected
            for key, expected in selector.items()
        ):
            matches.append((index, row))
    if len(matches) != 1:
        raise ValueError("source row selector is not unique")
    index, row = matches[0]
    phase = _PHASES.get(row["proc"], "unknown")
    effective = None if pd.isna(row["end_date"]) else _interval(row["end_date"])
    event_id = "repurchase:" + sha256_bytes(canonical_json(selector).encode())[:24]
    event = {
        "subject_code": row["ts_code"][:6],
        "event_id": event_id,
        "predicate": "回购",
        "lifecycle": phase,
        "assertion_kind": "forecast" if phase == "plan" else "actual",
        "polarity": "affirmed",
        "amount_value": None,
        "amount_unit": None,
        "amount_basis": None,
        "effective_at": effective,
    }
    field_paths = {
        "subject_code": f"/rows/{index}/ts_code",
        "predicate": "/endpoint/repurchase",
        "event_id": [f"/rows/{index}/{key}" for key in sorted(_SELECTOR)],
    }
    if phase != "unknown":
        field_paths.update(
            dict.fromkeys(("lifecycle", "assertion_kind", "polarity"), f"/rows/{index}/proc")
        )
    if effective is not None:
        field_paths["effective_at"] = f"/rows/{index}/end_date"
    # The generic amount column does not establish a plan's upper-bound semantics.
    # Only implementation/completion rows establish executed amounts.
    if phase in {"in_progress", "completed"} and not pd.isna(row["amount"]):
        try:
            amount = Decimal(str(row["amount"]))
        except InvalidOperation as exc:
            raise ValueError("invalid source amount") from exc
        if not amount.is_finite() or amount < 0:
            raise ValueError("invalid source amount")
        event.update(
            amount_value=format(amount.normalize(), "f"),
            amount_unit="CNY",
            amount_basis="executed_total",
        )
        field_paths.update(
            amount_value=f"/rows/{index}/amount",
            amount_unit="/endpoint/repurchase/amount:CNY",
            amount_basis=f"/rows/{index}/proc",
        )
    validate_event(event)
    payload = {
        "schema_version": 1,
        "source_receipt_id": receipt["receipt_id"],
        "source_hash": receipt["payload_hash"],
        "event": event,
        "checked_fields": sorted(field_paths),
        "reviewer": None,
    }
    return payload, field_paths


def require_timing(receipt, frame):
    cutoff = parse_aware(validate_decision_frame(frame)["knowledge_cutoff"])
    timing = receipt.get("source_timing")
    if timing:
        available = latest_possible(
            timing["first_available_at"], timing["timestamp_precision"]["first_available_at"]
        )
        published = latest_possible(
            timing["published_at"], timing["timestamp_precision"]["published_at"]
        )
    else:
        available, published = parse_aware(receipt["available_at"]), None
    if available is None or available > cutoff or published is not None and published > cutoff:
        raise ValueError("source availability at decision is unknown")
    return available


def admissible_attempts(entries, task_id, attempt):
    entry = entries[task_id]
    if entry["attempt"] != attempt or entry["state"] not in {"RUNNING", "SUCCEEDED"}:
        raise ValueError("source review task attempt is not current")
    allowed = {task_id: attempt}
    pending, seen = list(entry["spec"]["dependencies"]), set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        ancestor = entries.get(key)
        if ancestor is None or ancestor["state"] != "SUCCEEDED":
            continue
        allowed[key] = ancestor["attempt"]
        pending.extend(ancestor["spec"]["dependencies"])
    return allowed


def require_source_identity(source, *, engine, run_id, allowed):
    if (
        source["engine"] != engine
        or source["run_id"] != run_id
        or allowed.get(source["task_id"]) != source["attempt"]
    ):
        raise ValueError("source receipt is outside admitted run/task/attempt")


def matching_requests(root, receipts, claim_event, *, frame=None, owner=None):
    """Find a unique raw row independently of amount, lifecycle, URL or model ID."""
    if (
        claim_event is None
        or claim_event["predicate"] != "回购"
        or claim_event["effective_at"] is None
    ):
        return []
    found = []
    for source in receipts:
        if (source["provider"], source["endpoint"], source["codec"]) != _REGISTRY[ADAPTER_ID][:3]:
            continue
        try:
            if frame is not None:
                require_timing(source, frame)
            if owner is not None:
                require_source_identity(source, **owner)
            raw = checked_payload(Path(root), source)
            table = _table(source, raw, ADAPTER_ID)
            for row in table.to_dict("records"):
                if (
                    row["ts_code"][:6] != claim_event["subject_code"]
                    or pd.isna(row["end_date"])
                    or _interval(row["end_date"]) != claim_event["effective_at"]
                ):
                    continue
                selector = {key: None if pd.isna(row[key]) else row[key] for key in _SELECTOR}
                payload, _ = derive_fields(source, raw, ADAPTER_ID, selector)
                found.append(
                    {
                        "source_receipt_id": source["receipt_id"],
                        "adapter_id": ADAPTER_ID,
                        "selector": selector,
                    }
                )
        except (OSError, ValueError, TypeError):
            continue
    # A generic dated claim cannot choose among multiple programs/announcements.
    anchors = {canonical_json(row["selector"]) for row in found}
    return found if len(anchors) == 1 else []


def replay_review(root: Path, review_receipt: dict, source: dict, frame: dict) -> dict:
    """Recompute a root-issued review. Neither provider labels nor reviewer prose grant trust."""
    from autoresearch.trace.events import verify_event_chain

    if (review_receipt["provider"], review_receipt["endpoint"], review_receipt["codec"]) != (
        "deterministic",
        "claim_fields.v1",
        "json.canonical.v1",
    ):
        raise ValueError("unregistered field review producer")
    if review_receipt["status"] != "SUCCEEDED":
        raise ValueError("field review failed")
    params = review_receipt["normalized_params"]
    if (
        set(params)
        != {
            "producer_version",
            "adapter_id",
            "selector",
            "source_receipt_id",
            "field_paths",
            "frame_sha256",
            "input_snapshots",
        }
        or params["producer_version"] != PRODUCER_VERSION
    ):
        raise ValueError("field review lacks registered provenance")
    if params["source_receipt_id"] != source["receipt_id"] or params[
        "frame_sha256"
    ] != sha256_bytes(canonical_json(frame).encode()):
        raise ValueError("field review source/frame mismatch")
    tasks = json.loads((root.parent / "session/tasks.json").read_bytes())
    if tasks["engine"] != review_receipt["engine"] or tasks["run_id"] != review_receipt["run_id"]:
        raise ValueError("field review owner mismatch")
    entries = owner_entries(root)
    allowed = admissible_attempts(entries, review_receipt["task_id"], review_receipt["attempt"])
    require_source_identity(
        source, engine=review_receipt["engine"], run_id=review_receipt["run_id"], allowed=allowed
    )
    if (
        params["input_snapshots"]
        != entries[review_receipt["task_id"]]["claim_receipt"]["input_snapshots"]
    ):
        raise ValueError("field review frozen input mismatch")
    path = root / "events/events.jsonl"
    if not verify_event_chain(path)["ok"]:
        raise ValueError("field review owner events invalid")
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not any(
        row["event_type"] == "SOURCE_FIELDS_VERIFIED"
        and row["run_id"] == review_receipt["run_id"]
        and row["engine"] == review_receipt["engine"]
        and row["payload"]
        == {
            "review_receipt_id": review_receipt["receipt_id"],
            "source_receipt_id": source["receipt_id"],
            "task_id": review_receipt["task_id"],
            "attempt": review_receipt["attempt"],
        }
        for row in events
    ):
        raise ValueError("field review has no root issuance event")
    require_timing(source, frame)
    raw = checked_payload(root, source)
    expected, paths = derive_fields(source, raw, params["adapter_id"], params["selector"])
    actual = json.loads(checked_payload(root, review_receipt))
    if actual != expected or params["field_paths"] != paths:
        raise ValueError("field review differs from raw source")
    return actual


def linked_claim(claim, reviewed_event):
    """Bind a dated subject/predicate to the independently unique source row anchor."""
    if (
        any(
            claim[key] != reviewed_event[key]
            for key in ("subject_code", "predicate", "effective_at")
        )
        or claim["effective_at"] is None
    ):
        raise ValueError("claim does not identify this source event")
    return {**claim, "event_id": reviewed_event["event_id"]}


def owner_entries(root: Path) -> dict:
    """Read the atomically published owner snapshot without orchestrator coupling."""
    value = json.loads((Path(root).parent / "session/tasks.json").read_bytes())
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("invalid session task store")
    return value["tasks"]

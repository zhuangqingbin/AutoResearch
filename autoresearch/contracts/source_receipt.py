"""Strict v1 contract for replayable instances of provider responses."""

from __future__ import annotations

import json

from autoresearch.contracts.forensic import (
    _aware,
    _canonical,
    _digest_without,
    _engine,
    _optional_string,
    _positive_int,
    _required_string,
    _run_id,
)
from autoresearch.contracts.session_task import (
    require_exact_fields,
    require_sha256,
)

SOURCE_RECEIPT_FIELDS = frozenset({
    "schema_version", "receipt_id", "engine", "run_id", "task_id", "attempt",
    "provider", "endpoint", "normalized_params", "occurrence", "started_at", "ended_at",
    "status", "codec", "payload_hash", "raw_hash", "error", "as_of", "available_at",
    "consumer_refs",
})
SOURCE_RECEIPT_V2_FIELDS = SOURCE_RECEIPT_FIELDS | {"source_timing", "source_status", "supersedes_receipt_ids"}
ERROR_FIELDS = frozenset({"category", "message"})
CONSUMER_REF_FIELDS = frozenset({
    "task_id", "attempt", "artifact_id", "consumption_kind",
})

STATUSES = frozenset({"SUCCEEDED", "FAILED", "UNMEASURED"})
CODECS = frozenset({
    "dataframe.parquet.v1", "json.canonical.v1", "text.utf8.v1", "bytes.v1",
    "failure.json.v1",
})
CONSUMPTION_KINDS = frozenset({"INPUT", "CALCULATION", "QUOTE", "DISCOVERY"})


def source_receipt_id(value: dict) -> str:
    return _digest_without(value, "receipt_id")


def _optional_sha256(value: object, field: str) -> None:
    if value is not None:
        require_sha256(value, field)


def _validate_error(value: dict | None, *, required: bool) -> None:
    if value is None:
        if required:
            raise ValueError("failure receipt requires error")
        return
    require_exact_fields(value, ERROR_FIELDS)
    _required_string(value["category"], "error category")
    _required_string(value["message"], "error message")


def _validate_consumer_ref(value: dict) -> tuple:
    require_exact_fields(value, CONSUMER_REF_FIELDS)
    _required_string(value["task_id"], "consumer task_id")
    _positive_int(value["attempt"], "consumer attempt")
    _required_string(value["artifact_id"], "consumer artifact_id")
    if value["consumption_kind"] not in CONSUMPTION_KINDS:
        raise ValueError("invalid consumption_kind")
    return (
        value["task_id"], value["attempt"], value["artifact_id"], value["consumption_kind"]
    )


def validate_source_receipt(value: dict) -> dict:
    version = value.get("schema_version")
    if type(version) is not int or version not in {1, 2}:
        raise ValueError("unsupported source receipt schema")
    require_exact_fields(value, SOURCE_RECEIPT_FIELDS if version == 1 else SOURCE_RECEIPT_V2_FIELDS)
    if version == 2:
        from autoresearch.contracts.source_time import validate_source_times
        validate_source_times(value["source_timing"])
        if value["source_status"] not in {"CURRENT", "CORRECTED", "RETRACTED"}:
            raise ValueError("invalid source_status")
        ids = value["supersedes_receipt_ids"]
        if type(ids) is not list or len(ids) != len(set(ids)):
            raise ValueError("invalid supersedes_receipt_ids")
        for item in ids:
            require_sha256(item, "supersedes receipt")
    require_sha256(value["receipt_id"], "receipt_id")
    _engine(value["engine"])
    _run_id(value["run_id"])
    _required_string(value["task_id"], "task_id")
    _positive_int(value["attempt"], "attempt")
    _required_string(value["provider"], "provider")
    _required_string(value["endpoint"], "endpoint")
    if type(value["normalized_params"]) is not dict:
        raise ValueError("normalized_params must be an object")
    try:
        json.loads(_canonical(value["normalized_params"]).decode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError("normalized_params must contain canonical JSON values") from exc
    _positive_int(value["occurrence"], "occurrence")
    started = _aware(value["started_at"], "started_at")
    ended = _aware(value["ended_at"], "ended_at")
    if ended < started:
        raise ValueError("ended_at precedes started_at")
    if value["status"] not in STATUSES:
        raise ValueError("invalid source receipt status")
    if value["codec"] not in CODECS:
        raise ValueError("invalid source receipt codec")
    require_sha256(value["payload_hash"], "payload_hash")
    _optional_sha256(value["raw_hash"], "raw_hash")
    _optional_string(value["as_of"], "as_of")
    _aware(value["available_at"], "available_at", optional=True)
    failed = value["status"] in {"FAILED", "UNMEASURED"}
    if failed and value["codec"] != "failure.json.v1":
        raise ValueError("failure receipt requires failure.json.v1 codec")
    if not failed and value["codec"] == "failure.json.v1":
        raise ValueError("successful receipt cannot use failure codec")
    _validate_error(value["error"], required=failed)
    if not failed and value["error"] is not None:
        raise ValueError("successful receipt error must be null")
    if type(value["consumer_refs"]) is not list:
        raise ValueError("consumer_refs must be a list")
    consumers = [_validate_consumer_ref(item) for item in value["consumer_refs"]]
    if len(consumers) != len(set(consumers)):
        raise ValueError("duplicate consumer reference")
    if value["receipt_id"] != source_receipt_id(value):
        raise ValueError("receipt_id mismatch")
    return value


def _base_key(value: dict) -> tuple:
    return (
        value["engine"], value["run_id"], value["task_id"], value["attempt"],
        value["provider"], value["endpoint"], _canonical(value["normalized_params"]),
    )


def validate_source_receipts(values: list[dict]) -> list[dict]:
    if type(values) is not list:
        raise ValueError("source receipts must be a list")
    by_instance: dict[tuple, dict] = {}
    sequences: dict[tuple, list[int]] = {}
    receipt_ids = []
    for value in values:
        validate_source_receipt(value)
        key = (*_base_key(value), value["occurrence"])
        if key in by_instance:
            if by_instance[key] != value:
                raise ValueError("conflicting source receipt for task/attempt occurrence")
            raise ValueError("duplicate source receipt")
        by_instance[key] = value
        sequences.setdefault(_base_key(value), []).append(value["occurrence"])
        receipt_ids.append(value["receipt_id"])
    if len(receipt_ids) != len(set(receipt_ids)):
        raise ValueError("duplicate receipt_id")
    for occurrences in sequences.values():
        ordered = sorted(occurrences)
        if ordered != list(range(1, len(ordered) + 1)):
            raise ValueError("source receipt occurrence sequence must be contiguous from one")
    return values


__all__ = [
    "CODECS",
    "CONSUMPTION_KINDS",
    "SOURCE_RECEIPT_FIELDS",
    "STATUSES",
    "source_receipt_id",
    "validate_source_receipt",
    "validate_source_receipts",
]

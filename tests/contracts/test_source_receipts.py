from __future__ import annotations

from copy import deepcopy

import pytest

from autoresearch.contracts.source_receipt import (
    source_receipt_id,
    validate_source_receipt,
    validate_source_receipts,
)

H = "a" * 64
RUN_ID = "20260914T120000000000Z"


def _receipt(*, occurrence=1, status="SUCCEEDED"):
    value = {
        "schema_version": 1,
        "receipt_id": "0" * 64,
        "engine": "codex",
        "run_id": RUN_ID,
        "task_id": "stock.harvest",
        "attempt": 1,
        "provider": "tushare",
        "endpoint": "moneyflow",
        "normalized_params": {"trade_date": "20260914"},
        "occurrence": occurrence,
        "started_at": "2026-09-14T12:00:00Z",
        "ended_at": "2026-09-14T12:00:01Z",
        "status": status,
        "codec": "dataframe.parquet.v1",
        "payload_hash": H,
        "raw_hash": None,
        "error": None,
        "as_of": "2026-09-14",
        "available_at": "2026-09-14T11:59:00+00:00",
        "consumer_refs": [{
            "task_id": "stock.harvest",
            "attempt": 1,
            "artifact_id": "stock.context",
            "consumption_kind": "INPUT",
        }],
    }
    if status != "SUCCEEDED":
        value.update({
            "codec": "failure.json.v1",
            "error": {"category": "DATA_CONTRACT_EMPTY", "message": "empty A-tier frame"},
        })
    value["receipt_id"] = source_receipt_id(value)
    return value


def test_valid_source_receipt_is_returned_without_mutation():
    value = _receipt()
    before = deepcopy(value)
    assert validate_source_receipt(value) is value
    assert value == before


def test_failed_response_is_a_first_class_replay_fact():
    value = _receipt(status="FAILED")
    assert validate_source_receipt(value) is value


def test_success_requires_a_payload_blob_hash():
    value = _receipt()
    value["payload_hash"] = None
    value["receipt_id"] = source_receipt_id(value)
    with pytest.raises(ValueError, match="payload_hash"):
        validate_source_receipt(value)


def test_failure_requires_registered_failure_codec_and_error():
    value = _receipt(status="FAILED")
    value.update({"codec": "json.canonical.v1", "error": None})
    value["receipt_id"] = source_receipt_id(value)
    with pytest.raises(ValueError, match="failure"):
        validate_source_receipt(value)


def test_receipt_id_covers_occurrence_and_status():
    value = _receipt()
    value["occurrence"] = 2
    with pytest.raises(ValueError, match="receipt_id"):
        validate_source_receipt(value)


def test_duplicate_consumer_reference_is_rejected():
    value = _receipt()
    value["consumer_refs"].append(deepcopy(value["consumer_refs"][0]))
    value["receipt_id"] = source_receipt_id(value)
    with pytest.raises(ValueError, match="duplicate consumer"):
        validate_source_receipt(value)


def test_same_task_attempt_source_occurrence_cannot_conflict():
    first = _receipt()
    second = _receipt()
    second["payload_hash"] = "b" * 64
    second["receipt_id"] = source_receipt_id(second)
    with pytest.raises(ValueError, match="conflicting source receipt"):
        validate_source_receipts([first, second])


def test_occurrence_sequence_must_be_contiguous():
    with pytest.raises(ValueError, match="occurrence sequence"):
        validate_source_receipts([_receipt(occurrence=1), _receipt(occurrence=3)])

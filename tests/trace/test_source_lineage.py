from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from multiprocessing import get_context

import pandas as pd
import pytest

from autoresearch.common import workspace as ws
from autoresearch.data import cache
from autoresearch.data.contracts import DataContractError
from autoresearch.trace.blobs import blob_path
from autoresearch.trace.capsule import begin_run
from autoresearch.trace.source_lineage import trace_access

DATE = "2026-08-27"
NOW = datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=timezone.utc)


def _daily(close: float = 10.5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [close],
            "amount": [1e5],
            "pct_chg": [1.2],
        }
    )


@pytest.fixture
def active_run(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_codex")
    monkeypatch.setattr(
        "autoresearch.scan.user_config.DEFAULT_PINNED_PATH",
        tmp_path / "missing-pinned.jsonc",
    )
    monkeypatch.setattr(
        "autoresearch.trace.capsule.snapshot_identity",
        lambda *args, **kwargs: {
            "ok": True,
            "components": {},
            "missing": [],
            "errors": [],
        },
    )
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    handle = begin_run("scan-market", DATE, "codex", {}, now=NOW)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setenv("AUTORESEARCH_STAGE", "l1")
    monkeypatch.setenv("AUTORESEARCH_INVOCATION_ID", "lineage-l1-1")
    monkeypatch.setenv("AUTORESEARCH_ATTEMPT", "2")
    monkeypatch.setenv("AUTORESEARCH_SUBJECT", "market")
    return handle


def _reads(handle) -> list[dict]:
    path = handle.capsule / "lineage/reads.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _events(handle) -> list[dict]:
    path = handle.capsule / "events/events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _record_lineage_process(number: int) -> bool:
    access = trace_access("daily", {"trade_date": f"202607{number:02d}"})
    return access.finish_success(_daily(float(number)), "FETCHED_UNSETTLED", None)


def test_trace_access_redacts_params_and_records_exact_context(active_run, monkeypatch):
    secret = "trace-secret-value"
    monkeypatch.setenv("TUSHARE_TOKEN", secret)
    frame = _daily()
    access = trace_access(
        "daily",
        {"trade_date": "20260825", "token": secret, "nested": {"z": 2, "a": 1}},
        today="20260827",
    )
    assert access.finish_success(frame, "FETCHED_LIVE", None) is True

    row = _reads(active_run)[-1]
    encoded = json.dumps(row, ensure_ascii=False)
    assert secret not in encoded
    assert row == {
        **row,
        "schema_version": 1,
        "run_id": active_run.run_id,
        "engine": "codex",
        "stage": "l1",
        "invocation_id": "lineage-l1-1",
        "attempt": 2,
        "subject": "market",
        "endpoint": "daily",
        "policy_key": "date",
        "policy_settle": "eod",
        "access": "FETCHED_LIVE",
        "status": "SUCCEEDED",
        "error_type": None,
        "error_message": None,
        "path": None,
        "rows": 1,
        "evidence_complete": True,
    }
    assert row["normalized_params"]["token"] == "[REDACTED]"
    assert list(row["normalized_params"]["nested"]) == ["a", "z"]
    assert len(row["columns_hash"]) == 64
    assert blob_path(active_run.capsule, row["blob_hash"]).is_file()
    assert row["bytes"] == blob_path(active_run.capsule, row["blob_hash"]).stat().st_size
    assert _events(active_run)[-1]["event_type"] == "SOURCE_FETCHED"


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("hit", "CACHE_HIT"),
        ("fetch_write", "FETCHED_CACHED"),
        ("live", "FETCHED_LIVE"),
        ("unsettled", "FETCHED_UNSETTLED"),
        ("refused", "FETCHED_REFUSED_LAKE"),
    ],
)
def test_get_or_fetch_records_every_success_path(active_run, tmp_path, monkeypatch, case, expected):
    lake = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", lake)
    if case == "hit":
        path = cache.lake_path("daily", {"trade_date": "20260825"})
        cache._atomic_write(path, _daily())
        df = cache.get_or_fetch(
            "daily", {"trade_date": "20260825"}, today="20260827", fetch=lambda *_: None
        )
    elif case == "fetch_write":
        df = cache.get_or_fetch(
            "daily", {"trade_date": "20260825"}, today="20260827", fetch=lambda *_: _daily()
        )
    elif case == "live":
        df = cache.get_or_fetch(
            "stock_zh_a_spot_em", {}, today="20260827", fetch=lambda *_: pd.DataFrame({"x": [1]})
        )
    elif case == "unsettled":
        df = cache.get_or_fetch(
            "daily", {"trade_date": "20260827"}, today="20260827", fetch=lambda *_: _daily()
        )
    else:
        monkeypatch.setattr(cache, "_real_today", lambda: "20260827")
        df = cache.get_or_fetch(
            "eastmoney_hot_rank", {}, today="20260827", fetch=lambda *_: pd.DataFrame()
        )

    row = _reads(active_run)[-1]
    assert row["access"] == expected
    assert row["rows"] == len(df)
    assert row["status"] == "SUCCEEDED"
    assert row["blob_hash"]
    assert blob_path(active_run.capsule, row["blob_hash"]).is_file()
    expected_event = "SOURCE_READ" if expected == "CACHE_HIT" else "SOURCE_FETCHED"
    assert _events(active_run)[-1]["event_type"] == expected_event


def test_get_or_fetch_records_contract_exception(active_run, tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    with pytest.raises(DataContractError):
        cache.get_or_fetch(
            "daily", {"trade_date": "20260825"}, today="20260827", fetch=lambda *_: pd.DataFrame()
        )

    row = _reads(active_run)[-1]
    assert row["status"] == "FAILED"
    assert row["error_type"] == "DataContractError"
    assert row["blob_hash"] is None
    assert _events(active_run)[-1]["event_type"] == "SOURCE_FAILED"


def test_failure_error_is_redacted_without_changing_original_exception(
    active_run, tmp_path, monkeypatch
):
    secret = "failure-secret-value"
    monkeypatch.setenv("TUSHARE_TOKEN", secret)
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")

    with pytest.raises(RuntimeError, match=secret):
        cache.get_or_fetch(
            "daily",
            {"trade_date": "20260825"},
            today="20260827",
            fetch=lambda *_: (_ for _ in ()).throw(RuntimeError(f"vendor said {secret}")),
        )

    persisted = json.dumps(_reads(active_run)[-1], ensure_ascii=False)
    assert secret not in persisted
    assert "[REDACTED]" in persisted


def test_get_or_fetch_records_snapshot_guard_before_fetch(active_run, tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    monkeypatch.setattr(cache, "_real_today", lambda: "20260828")
    with pytest.raises(cache.SnapshotDateError):
        cache.get_or_fetch("eastmoney_hot_rank", {}, today="20260827", fetch=lambda *_: None)
    assert _reads(active_run)[-1]["error_type"] == "SnapshotDateError"


def test_get_or_fetch_records_unknown_policy_failure_from_function_entry(active_run):
    with pytest.raises(KeyError):
        cache.get_or_fetch("not_registered", {}, fetch=lambda *_: pd.DataFrame())
    row = _reads(active_run)[-1]
    assert row["endpoint"] == "not_registered"
    assert row["policy_key"] is None
    assert row["status"] == "FAILED"
    assert row["error_type"] == "KeyError"


def test_no_active_run_is_true_noop_and_preserves_cache_bytes(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    frame = _daily()
    first = cache.get_or_fetch(
        "daily", {"trade_date": "20260825"}, today="20260827", fetch=lambda *_: frame
    )
    path = cache.lake_path("daily", {"trade_date": "20260825"})
    before = path.read_bytes()
    second = cache.get_or_fetch(
        "daily", {"trade_date": "20260825"}, today="20260827", fetch=lambda *_: None
    )
    assert path.read_bytes() == before
    pd.testing.assert_frame_equal(first, second)
    assert not list(tmp_path.rglob("reads.jsonl"))
    assert not list(tmp_path.rglob("blobs"))


def test_concurrent_lineage_appends_are_complete_and_untorn(active_run):
    def record(number: int) -> bool:
        access = trace_access("daily", {"trade_date": f"202608{number:02d}"})
        return access.finish_success(_daily(float(number)), "FETCHED_UNSETTLED", None)

    with ThreadPoolExecutor(max_workers=10) as pool:
        assert all(pool.map(record, range(1, 31)))

    rows = _reads(active_run)
    assert len(rows) == 30
    assert len({row["normalized_params"]["trade_date"] for row in rows}) == 30
    assert all(row["status"] == "SUCCEEDED" for row in rows)


def test_multiprocess_lineage_appends_are_complete_and_untorn(active_run):
    with get_context("fork").Pool(6) as pool:
        assert all(pool.map(_record_lineage_process, range(1, 21)))
    rows = _reads(active_run)
    assert len(rows) == 20
    assert len({row["normalized_params"]["trade_date"] for row in rows}) == 20
    assert len([row for row in _events(active_run) if row["event_type"] == "SOURCE_FETCHED"]) == 20


def test_trace_persistence_failure_does_not_replace_success_or_double_terminal(
    active_run, monkeypatch
):
    import autoresearch.trace.source_lineage as lineage

    monkeypatch.setattr(
        lineage, "_append_read", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk"))
    )
    access = trace_access("daily", {"trade_date": "20260825"})
    assert access.finish_success(_daily(), "FETCHED_UNSETTLED", None) is False
    assert access.finish_failure(RuntimeError("must not append twice")) is False
    terminals = [
        row
        for row in _events(active_run)
        if row["event_type"] in {"SOURCE_FETCHED", "SOURCE_FAILED"}
    ]
    assert len(terminals) == 1
    assert terminals[0]["event_type"] == "SOURCE_FETCHED"
    assert terminals[0]["payload"]["evidence_complete"] is False
    assert (active_run.capsule / "lineage/evidence_gaps.jsonl").is_file()


def test_lineage_append_never_follows_existing_reads_symlink(active_run, tmp_path):
    lineage = active_run.capsule / "lineage"
    lineage.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("untouched\n", encoding="utf-8")
    (lineage / "reads.jsonl").symlink_to(outside)

    access = trace_access("daily", {"trade_date": "20260825"})
    assert access.finish_success(_daily(), "FETCHED_UNSETTLED", None) is False
    assert outside.read_text(encoding="utf-8") == "untouched\n"
    gap = json.loads((lineage / "evidence_gaps.jsonl").read_text(encoding="utf-8"))
    assert gap["row_persisted"] is False

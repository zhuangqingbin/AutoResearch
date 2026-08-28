from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path

import pandas as pd
import pytest

from autoresearch.common import workspace as ws
from autoresearch.data import cache
from autoresearch.data.contracts import DataContractError
from autoresearch.scan import retention
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


class _HostileBusinessError(RuntimeError):
    def __str__(self) -> str:
        raise RuntimeError("hostile __str__ must never run outside evidence containment")


class _HostileColumn:
    def __hash__(self) -> int:
        return 7

    def __str__(self) -> str:
        raise RuntimeError("hostile column stringification")


class _HostilePath:
    def __fspath__(self) -> str:
        raise RuntimeError("hostile path coercion")


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


def test_cache_hit_blob_comes_from_returned_frame_not_reopened_path(
    active_run, tmp_path, monkeypatch
):
    lake = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", lake)
    path = cache.lake_path("daily", {"trade_date": "20260825"})
    cache._atomic_write(path, _daily(10.5))
    original_bytes = path.read_bytes()
    original_finish = cache._finish_source_success

    def replace_path_before_evidence(trace, frame, access, exact_path, **kwargs):
        cache._atomic_write(path, _daily(99.0))
        return original_finish(trace, frame, access, exact_path, **kwargs)

    monkeypatch.setattr(cache, "_finish_source_success", replace_path_before_evidence)
    returned = cache.get_or_fetch(
        "daily", {"trade_date": "20260825"}, today="20260827", fetch=lambda *_: None
    )

    row = _reads(active_run)[-1]
    captured_path = blob_path(active_run.capsule, row["blob_hash"])
    captured = pd.read_parquet(captured_path)
    pd.testing.assert_frame_equal(captured, returned)
    assert captured.iloc[0]["close"] == 10.5
    assert captured_path.read_bytes() == original_bytes
    assert row["blob_hash"] == hashlib.sha256(original_bytes).hexdigest()
    assert row["bytes"] == len(original_bytes)
    assert row["blob_role"] == "SOURCE_FILE_SNAPSHOT"


def test_fetched_cached_blob_preserves_returned_named_index(active_run, tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    frame = _daily()
    frame.index = pd.DatetimeIndex(["2026-08-25T00:00:00Z"], name="trade_ts")

    returned = cache.get_or_fetch(
        "daily", {"trade_date": "20260825"}, today="20260827", fetch=lambda *_: frame
    )

    row = _reads(active_run)[-1]
    assert row["blob_role"] == "SOURCE_FILE_SNAPSHOT"
    assert row["normalized_blob_hash"]
    captured = pd.read_parquet(
        blob_path(active_run.capsule, row["normalized_blob_hash"])
    )
    pd.testing.assert_frame_equal(captured, returned)


def test_fetch_return_and_blob_share_copy_isolated_from_producer_mutation(
    active_run, tmp_path, monkeypatch
):
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    producer_frame = _daily(10.5)
    original_finish = cache._finish_source_success

    def mutate_producer_before_evidence(trace, stable_frame, access, path, **kwargs):
        producer_frame.loc[:, "close"] = 99.0
        return original_finish(trace, stable_frame, access, path, **kwargs)

    monkeypatch.setattr(cache, "_finish_source_success", mutate_producer_before_evidence)
    returned = cache.get_or_fetch(
        "daily",
        {"trade_date": "20260825"},
        today="20260827",
        fetch=lambda *_: producer_frame,
    )

    row = _reads(active_run)[-1]
    captured = pd.read_parquet(blob_path(active_run.capsule, row["blob_hash"]))
    assert producer_frame.iloc[0]["close"] == 99.0
    assert returned.iloc[0]["close"] == 10.5
    pd.testing.assert_frame_equal(captured, returned)


@pytest.mark.parametrize("case", ["hit", "write"])
def test_source_byte_snapshot_failure_preserves_business_result(
    active_run, tmp_path, monkeypatch, case
):
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    path = cache.lake_path("daily", {"trade_date": "20260825"})
    if case == "hit":
        cache._atomic_write(path, _daily())
    monkeypatch.setattr(
        cache,
        "_read_file_bytes",
        lambda *_: (_ for _ in ()).throw(OSError("snapshot evidence failed")),
    )

    returned = cache.get_or_fetch(
        "daily",
        {"trade_date": "20260825"},
        today="20260827",
        fetch=lambda *_: _daily(),
    )

    pd.testing.assert_frame_equal(returned.reset_index(drop=True), _daily())
    row = _reads(active_run)[-1]
    assert row["status"] == "SUCCEEDED"
    assert row["evidence_complete"] is False
    assert (active_run.capsule / "lineage/evidence_gaps.jsonl").is_file()


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


def test_hostile_exception_string_never_replaces_original_failure(
    active_run, tmp_path, monkeypatch
):
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    original = _HostileBusinessError()

    def fail(*_):
        raise original

    with pytest.raises(_HostileBusinessError) as caught:
        cache.get_or_fetch(
            "daily", {"trade_date": "20260825"}, today="20260827", fetch=fail
        )

    assert caught.value is original
    assert caught.value.__traceback__ is not None
    row = _reads(active_run)[-1]
    assert row["error_type"] == "_HostileBusinessError"
    assert row["error_message"] == "[unavailable]"


def test_hostile_column_hash_failure_never_changes_successful_return(active_run):
    frame = pd.DataFrame([[1]])
    frame.columns = [_HostileColumn()]

    out = cache.get_or_fetch(
        "stock_zh_a_spot_em", {}, today="20260827", fetch=lambda *_: frame
    )

    assert out.iloc[0, 0] == frame.iloc[0, 0]
    assert out.columns[0] is frame.columns[0]
    gap = json.loads(
        (active_run.capsule / "lineage/evidence_gaps.jsonl").read_text(encoding="utf-8")
    )
    assert gap["error_type"] == "RuntimeError"


def test_hostile_path_coercion_is_contained_by_finish_success(active_run):
    access = trace_access("daily", {"trade_date": "20260825"})
    assert access.finish_success(_daily(), "CACHE_HIT", _HostilePath()) is False
    assert (active_run.capsule / "lineage/evidence_gaps.jsonl").is_file()


@pytest.mark.parametrize("failing_channel", ["blob", "row", "event", "canonical"])
def test_any_success_evidence_failure_preserves_exact_dataframe(
    active_run, monkeypatch, failing_channel
):
    import autoresearch.trace.source_lineage as lineage

    frame = pd.DataFrame({"x": [1]})
    if failing_channel == "blob":
        monkeypatch.setattr(
            lineage,
            "put_dataframe",
            lambda *_: (_ for _ in ()).throw(OSError("blob failed")),
        )
    elif failing_channel == "row":
        monkeypatch.setattr(
            lineage,
            "_append_read",
            lambda *_: (_ for _ in ()).throw(OSError("row failed")),
        )
    elif failing_channel == "event":
        original_append = lineage.append_event

        def fail_source_event(*args, **kwargs):
            if str(kwargs.get("event_type", "")).startswith("SOURCE_"):
                raise OSError("event failed")
            return original_append(*args, **kwargs)

        monkeypatch.setattr(lineage, "append_event", fail_source_event)
    else:
        original_canonical = lineage.canonical_json
        calls = 0

        def fail_first_canonical(value):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TypeError("canonical failed")
            return original_canonical(value)

        monkeypatch.setattr(lineage, "canonical_json", fail_first_canonical)

    out = cache.get_or_fetch(
        "stock_zh_a_spot_em", {}, today="20260827", fetch=lambda *_: frame
    )

    pd.testing.assert_frame_equal(out, frame)
    assert (active_run.capsule / "lineage/evidence_gaps.jsonl").is_file() or any(
        row["event_type"] == "EVIDENCE_MISSING" for row in _events(active_run)
    )


def test_raising_trace_adapter_cannot_replace_success_or_failure(
    active_run, monkeypatch
):
    import autoresearch.trace.source_lineage as lineage

    class BrokenTrace:
        def finish_success(self, *_args, **_kwargs):
            raise OSError("success evidence failed")

        def finish_failure(self, *_args, **_kwargs):
            raise OSError("failure evidence failed")

    monkeypatch.setattr(lineage, "trace_access", lambda *_args, **_kwargs: BrokenTrace())
    frame = pd.DataFrame({"x": [1]})
    assert (
        cache.get_or_fetch(
            "stock_zh_a_spot_em", {}, today="20260827", fetch=lambda *_: frame
        )
        is frame
    )

    original = _HostileBusinessError()

    def fail(*_):
        raise original

    with pytest.raises(_HostileBusinessError) as caught:
        cache.get_or_fetch("stock_zh_a_spot_em", {}, today="20260827", fetch=fail)
    assert caught.value is original


def test_gap_recording_failure_degrades_to_generic_stderr_without_leak(
    active_run, monkeypatch, capfd
):
    import autoresearch.trace.source_lineage as lineage

    access = trace_access("daily", {"trade_date": "20260825"})
    monkeypatch.setattr(
        lineage,
        "_append_jsonl",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("sensitive persistence detail")
        ),
    )
    monkeypatch.setattr(
        lineage,
        "append_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("sensitive event detail")
        ),
    )

    assert access.finish_failure(_HostileBusinessError()) is False
    stderr = capfd.readouterr().err
    assert "source lineage evidence incomplete" in stderr
    assert "sensitive" not in stderr
    assert "hostile" not in stderr


def test_gap_metadata_construction_failure_is_also_contained(
    active_run, monkeypatch, capfd
):
    import autoresearch.trace.source_lineage as lineage

    access = trace_access("daily", {"trade_date": "20260825"})
    monkeypatch.setattr(
        lineage,
        "_utc_now",
        lambda: (_ for _ in ()).throw(RuntimeError("clock detail must not leak")),
    )

    assert access.finish_success(_daily(), "FETCHED_UNSETTLED", None) is False
    stderr = capfd.readouterr().err
    assert "source lineage evidence incomplete" in stderr
    assert "clock detail" not in stderr


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


def test_manifest_never_calls_event_append_failure_complete(active_run, monkeypatch):
    import autoresearch.trace.source_lineage as lineage

    original_append = lineage.append_event

    def fail_source_event(*args, **kwargs):
        if str(kwargs.get("event_type", "")).startswith("SOURCE_"):
            raise OSError("source event unavailable")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(lineage, "append_event", fail_source_event)
    frame = pd.DataFrame({"x": [1]})
    returned = cache.get_or_fetch(
        "stock_zh_a_spot_em", {}, today="20260827", fetch=lambda *_: frame
    )
    pd.testing.assert_frame_equal(returned, frame)
    scan = active_run.staging
    scan.mkdir(parents=True, exist_ok=True)

    doc, reason = retention._read_exact_manifest(scan)

    assert reason == ""
    assert doc["n_total_reads"] == 1
    assert doc["n_incomplete_reads"] == 1
    assert doc["n_missing_source_events"] == 1
    assert doc["n_evidence_gaps"] == 1


def test_invalid_explicit_run_binding_warns_and_writes_emergency_gap(
    tmp_path, monkeypatch, capfd
):
    import autoresearch.trace.source_lineage as lineage

    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "../../wrong")
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    synced = []
    real_fsync = lineage._fsync_directory

    def record_fsync(path):
        synced.append(Path(path))
        real_fsync(path)

    monkeypatch.setattr(lineage, "_fsync_directory", record_fsync)
    frame = pd.DataFrame({"x": [1]})

    returned = cache.get_or_fetch(
        "stock_zh_a_spot_em", {}, today="20260827", fetch=lambda *_: frame
    )

    pd.testing.assert_frame_equal(returned, frame)
    assert "source lineage evidence incomplete" in capfd.readouterr().err
    emergency = tmp_path / "context_codex/scan_runs/_evidence_gaps/source_lineage.jsonl"
    rows = [json.loads(line) for line in emergency.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["reason"] == "invalid_active_run_binding"
    assert rows[-1]["error_type"] == "ValueError"
    assert "../../wrong" not in emergency.read_text(encoding="utf-8")
    assert {
        tmp_path,
        tmp_path / "context_codex",
        tmp_path / "context_codex/scan_runs",
        emergency.parent,
    }.issubset(set(synced))


def test_lineage_directory_creation_fsync_failure_preserves_business_result(
    active_run, monkeypatch, capfd
):
    import autoresearch.trace.source_lineage as lineage

    real_fsync = lineage._fsync_directory

    def fail_capsule_parent(path):
        if Path(path) == active_run.capsule:
            raise OSError("directory fsync failed")
        return real_fsync(path)

    monkeypatch.setattr(lineage, "_fsync_directory", fail_capsule_parent)
    frame = pd.DataFrame({"x": [1]})
    returned = cache.get_or_fetch(
        "stock_zh_a_spot_em", {}, today="20260827", fetch=lambda *_: frame
    )

    pd.testing.assert_frame_equal(returned, frame)
    stderr = capfd.readouterr().err
    assert "directory fsync failed" not in stderr
    assert any(row["event_type"] == "EVIDENCE_MISSING" for row in _events(active_run))


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

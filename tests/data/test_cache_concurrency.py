"""Process-level lake contention with synthetic sources and isolated storage."""
import multiprocessing as mp
import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from autoresearch.data import cache


def _frame(value=10.5):
    return pd.DataFrame({"ts_code": ["600000.SH"], "open": [10.0], "high": [11.0],
                         "low": [9.0], "close": [value], "amount": [100000.0],
                         "pct_chg": [1.2]})


def _worker(root, day, counter, results, miss_barrier=None, fetch_barrier=None,
            crash=False, engine="codex"):
    from autoresearch.data import contracts

    os.environ["AUTORESEARCH_ENGINE"] = engine
    os.environ.pop("AUTORESEARCH_RUN_ID", None)
    contracts.CHECK_ROWS = False
    cache.LAKE = Path(root)
    path = cache.lake_path("daily", {"trade_date": day}, today="20261001")
    original_exists = Path.exists
    first_check = True

    def synchronized_exists(self):
        nonlocal first_check
        exists = original_exists(self)
        if self == path and first_check and miss_barrier is not None:
            first_check = False
            miss_barrier.wait(timeout=15)
        return exists

    if miss_barrier is not None:
        Path.exists = synchronized_exists

    def fetch(*_):
        with counter.get_lock():
            counter.value += 1
        if crash:
            os._exit(23)
        if fetch_barrier is not None:
            fetch_barrier.wait(timeout=15)
        return _frame()

    try:
        out = cache.get_or_fetch("daily", {"trade_date": day}, today="20261001", fetch=fetch)
        results.put(("ok", float(out.iloc[0]["close"])))
    except Exception as exc:
        results.put(("error", repr(exc)))


def _join(processes):
    try:
        for process in processes:
            process.join(timeout=20)
        assert all(not process.is_alive() for process in processes), "cache process hung"
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)


def test_same_key_cold_miss_fetches_once_across_engines(tmp_path):
    ctx = mp.get_context("spawn")
    counter, results, barrier = ctx.Value("i", 0), ctx.Queue(), ctx.Barrier(3)
    processes = [ctx.Process(target=_worker, args=(str(tmp_path), "20260929", counter,
                 results, barrier), kwargs={"engine": engine}) for engine in ("codex", "claude")]
    for process in processes:
        process.start()
    try:
        barrier.wait(timeout=15)
    finally:
        _join(processes)
    assert counter.value == 1
    assert [results.get(timeout=2) for _ in processes] == [("ok", 10.5), ("ok", 10.5)]
    assert all(process.exitcode == 0 for process in processes)


def test_distinct_keys_can_fetch_concurrently(tmp_path):
    ctx = mp.get_context("spawn")
    counter, results, barrier = ctx.Value("i", 0), ctx.Queue(), ctx.Barrier(3)
    processes = [ctx.Process(target=_worker, args=(str(tmp_path), day, counter, results),
                 kwargs={"fetch_barrier": barrier}) for day in ("20260928", "20260929")]
    for process in processes:
        process.start()
    try:
        barrier.wait(timeout=15)
    finally:
        _join(processes)
    assert counter.value == 2
    assert [results.get(timeout=2) for _ in processes] == [("ok", 10.5), ("ok", 10.5)]


def test_crashed_fetcher_releases_lock_for_next_process(tmp_path):
    ctx = mp.get_context("spawn")
    counter, results = ctx.Value("i", 0), ctx.Queue()
    crashed = ctx.Process(target=_worker, args=(str(tmp_path), "20260929", counter, results),
                         kwargs={"crash": True})
    crashed.start()
    _join([crashed])
    assert crashed.exitcode == 23
    assert not (tmp_path / "daily/20260929.parquet").exists()
    recovered = ctx.Process(target=_worker, args=(str(tmp_path), "20260929", counter, results))
    recovered.start()
    _join([recovered])
    assert results.get(timeout=2) == ("ok", 10.5)
    assert counter.value == 2


def test_atomic_write_uses_unique_temporary_and_cleans_its_failure(tmp_path, monkeypatch):
    target = tmp_path / "daily/20260929.parquet"
    observed = []
    original = pq.write_table

    def fail_write(table, where, **kwargs):
        # Accept a file handle or path; retain the actual writer destination.
        observed.append(Path(where if isinstance(where, (str, Path)) else where.name))
        original(table, where, **kwargs)
        raise OSError("synthetic interrupted parquet write")

    monkeypatch.setattr(cache.pq, "write_table", fail_write)
    for _ in range(2):
        with pytest.raises(OSError, match="synthetic interrupted"):
            cache._atomic_write(target, _frame())
    assert observed[0] != observed[1]
    assert not target.exists()
    assert not list(target.parent.glob("*.tmp"))
    assert not list(target.parent.glob(".*.tmp"))


def test_failed_replace_preserves_previous_file_and_cleans_owned_temp(tmp_path, monkeypatch):
    target = tmp_path / "snapshot.parquet"
    cache._atomic_write(target, _frame(10.0))
    original = target.read_bytes()
    unrelated = tmp_path / "unrelated.tmp"
    unrelated.write_bytes(b"other writer")

    def fail_replace(*_):
        raise OSError("synthetic rename failure")

    monkeypatch.setattr(cache.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic rename"):
        cache._atomic_write(target, _frame(20.0))
    assert target.read_bytes() == original
    assert unrelated.read_bytes() == b"other writer"
    assert {path for path in tmp_path.iterdir() if path.is_file()} == {target, unrelated}


def test_each_consumer_keeps_coherent_captured_bytes(tmp_path, monkeypatch):
    class Trace:
        enabled = True

        def __init__(self):
            self.successes = []

        def finish_success(self, frame, access, path, *, source_bytes):
            pd.testing.assert_frame_equal(frame, pq.read_table(pa.BufferReader(source_bytes)).to_pandas())
            self.successes.append((access, source_bytes))

        def finish_failure(self, error):
            raise AssertionError(error)

    traces = [Trace(), Trace()]
    queue = iter(traces)
    monkeypatch.setattr(cache, "LAKE", tmp_path)
    monkeypatch.setattr(cache, "_source_trace", lambda *_: next(queue))
    for _ in range(2):
        cache.get_or_fetch("daily", {"trade_date": "20260929"}, today="20261001",
                           fetch=lambda *_: _frame())
    assert traces[0].successes[0][0] == "FETCHED_CACHED"
    assert traces[1].successes[0][0] == "CACHE_HIT"
    assert traces[0].successes[0][1] == traces[1].successes[0][1]


@pytest.mark.parametrize("failure", ["exception", "empty"])
def test_failed_fetch_does_not_poison_cache_or_hold_lock(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(cache, "LAKE", tmp_path)

    def fetch(*_):
        if failure == "exception":
            raise RuntimeError("source unavailable")
        return pd.DataFrame()

    from autoresearch.data.contracts import DataContractError

    with pytest.raises((RuntimeError, DataContractError)):
        cache.get_or_fetch("daily", {"trade_date": "20260929"}, today="20261001", fetch=fetch)
    assert not cache.lake_path("daily", {"trade_date": "20260929"}).exists()
    result = cache.get_or_fetch("daily", {"trade_date": "20260929"}, today="20261001",
                                fetch=lambda *_: _frame())
    assert result.iloc[0]["close"] == 10.5

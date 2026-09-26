"""stk_factor_pro readiness probe (batch 4 Task 3, spec §6 C2).

tushare fills ``stk_factor_pro`` for the day until ~21:10 (09-07: 19:32 = 238 rows,
20:52 = 4457, 21:10 = 5547). A half snapshot passes the 3000-row A-level contract and
poisons the lake, so the unattended run waits until the row count is ≥5300 AND unchanged
over two consecutive polls, and gives up at the deadline. No network: rows are injected.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from autoresearch.scan import readiness


class _Clock:
    def __init__(self, start: datetime):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


def _probe(counts, *, start="2026-09-28 21:20", deadline="22:30", **kwargs):
    clock = _Clock(datetime.strptime(start, "%Y-%m-%d %H:%M"))
    seq = iter(counts)
    polled: list[str] = []

    def count(date: str):
        polled.append(date)
        value = next(seq)
        if isinstance(value, Exception):
            raise value
        return value

    ready = readiness.factor_rows_ready(
        "2026-09-28", deadline=deadline, count_rows=count, now=clock, sleep=clock.sleep,
        log=lambda line: None, **kwargs)
    return ready, clock, polled


def test_ready_only_after_two_equal_polls_at_or_above_the_floor():
    ready, clock, polled = _probe([5200, 5300, 5300])
    assert ready is True
    assert len(polled) == 3 and clock.sleeps == [300, 300]


def test_equal_polls_below_the_floor_are_not_ready():
    ready, _, polled = _probe([5000] * 20)
    assert ready is False
    assert len(polled) > 2          # kept polling until the deadline


def test_growing_count_is_never_ready_before_the_deadline():
    ready, clock, _ = _probe(list(range(5300, 5300 + 45 * 40, 45)))
    assert ready is False
    assert clock.now <= datetime(2026, 9, 28, 22, 30) + timedelta(seconds=1)


def test_last_sleep_is_clipped_to_the_deadline():
    _, clock, _ = _probe([5000] * 10, start="2026-09-28 22:22")
    assert clock.sleeps == [300, 180] and sum(clock.sleeps) == pytest.approx(480)


def test_a_failed_poll_breaks_stability_but_not_the_probe():
    ready, _, polled = _probe([5547, RuntimeError("DNS"), 5547, 5547])
    assert ready is True and len(polled) == 4


def test_parameters_default_to_the_spec_values():
    assert (readiness.MIN_ROWS, readiness.STABLE_POLLS, readiness.INTERVAL_S,
            readiness.DEADLINE) == (5300, 2, 300, "22:30")


def test_cli_exit_codes(monkeypatch):
    monkeypatch.setattr(readiness, "wait_and_guard", lambda date, **kw: date == "2026-09-28")
    assert readiness.main(["2026-09-28"]) == 0
    assert readiness.main(["2026-09-25"]) == 1


# ── late start: two readings ~30 s apart before giving up (review I4) ─────────────

def test_past_the_deadline_takes_two_readings_30s_apart_before_giving_up():
    ready, clock, polled = _probe([5547, 5547], start="2026-09-28 22:31")
    assert ready is True and len(polled) == 2 and clock.sleeps == [readiness.LATE_RECHECK_S]
    assert readiness.LATE_RECHECK_S == 30


def test_past_the_deadline_still_gives_up_when_the_two_readings_differ():
    ready, _, polled = _probe([5400, 5547, 5547], start="2026-09-28 22:31")
    assert ready is False and len(polled) == 2


# ── the lake partition the scan will read (review I1) ─────────────────────────────

def _factor_frame(rows: int):
    import pandas as pd

    return pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(rows)],
                         "close": 1.0, "ma_qfq_5": 1.0, "rsi_qfq_6": 50.0})


@pytest.fixture
def lake(tmp_path, monkeypatch):
    from autoresearch.data import cache

    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    monkeypatch.delenv("LAKE_ASSUME_SETTLED", raising=False)
    return tmp_path / "lake"


def _prewarm_writes(rows: int, monkeypatch) -> None:
    """The 21:00 prewarm path: same-day write allowed, 4800 rows pass the 3000-row gate."""
    from autoresearch.data import cache

    monkeypatch.setenv("LAKE_ASSUME_SETTLED", "1")
    cache.get_or_fetch("stk_factor_pro", {"trade_date": "20260928"}, today="2026-09-28",
                       fetch=lambda ep, p: _factor_frame(rows))
    monkeypatch.delenv("LAKE_ASSUME_SETTLED")


def test_partial_prewarm_partition_is_quarantined_so_the_scan_refetches(lake, monkeypatch):
    from autoresearch.data import cache

    _prewarm_writes(4800, monkeypatch)
    partition = lake / "stk_factor_pro" / "20260928.parquet"
    assert partition.is_file()
    moved = readiness.quarantine_partial_partition("2026-09-28", 5547, log=lambda line: None)
    assert moved == partition.with_name("20260928.parquet.partial")
    assert not partition.exists() and moved.is_file()
    fetched = []
    frame = cache.get_or_fetch(                      # what the scan's frame build does
        "stk_factor_pro", {"trade_date": "20260928"}, today="2026-09-28",
        fetch=lambda ep, p: fetched.append(ep) or _factor_frame(5547))
    assert fetched == ["stk_factor_pro"] and len(frame) == 5547


def test_complete_partition_is_left_alone(lake, monkeypatch):
    _prewarm_writes(5547, monkeypatch)
    assert readiness.quarantine_partial_partition("2026-09-28", 5547,
                                                  log=lambda line: None) is None
    assert (lake / "stk_factor_pro" / "20260928.parquet").is_file()


def test_no_partition_is_nothing_to_do(lake):
    assert readiness.quarantine_partial_partition("2026-09-28", 5547,
                                                  log=lambda line: None) is None


def test_wait_and_guard_quarantines_after_readiness(lake, monkeypatch):
    _prewarm_writes(4800, monkeypatch)
    clock = _Clock(datetime(2026, 9, 28, 21, 25))
    assert readiness.wait_and_guard(
        "2026-09-28", count_rows=lambda date: 5547, now=clock, sleep=clock.sleep,
        log=lambda line: None) is True
    assert not (lake / "stk_factor_pro" / "20260928.parquet").exists()


def test_wait_and_guard_not_ready_leaves_the_lake_alone(lake, monkeypatch):
    _prewarm_writes(4800, monkeypatch)
    clock = _Clock(datetime(2026, 9, 28, 22, 29))
    assert readiness.wait_and_guard(
        "2026-09-28", count_rows=lambda date: 4800, now=clock, sleep=clock.sleep,
        log=lambda line: None) is False
    assert (lake / "stk_factor_pro" / "20260928.parquet").is_file()

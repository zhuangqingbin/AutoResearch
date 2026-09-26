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


def test_deadline_already_passed_polls_once_then_gives_up():
    ready, clock, polled = _probe([5547, 5547], start="2026-09-28 22:31")
    assert ready is False and len(polled) == 1 and clock.sleeps == []


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
    monkeypatch.setattr(readiness, "factor_rows_ready", lambda date, **kw: date == "2026-09-28")
    assert readiness.main(["2026-09-28"]) == 0
    assert readiness.main(["2026-09-25"]) == 1

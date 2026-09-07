"""Observed broker fills are reconciled into independent FIFO position episodes."""
from decimal import Decimal

from autoresearch.research.execution_ledger import build_episodes


def fill(side, qty, amount, trade_date, *, fill_id=None, code="600000", fees="0"):
    return {
        "account_hash": "acct", "position_id": f"acct:{code}", "code": code,
        "fill_id": fill_id or f"{side}-{trade_date}-{amount}", "side": side,
        "trade_date": trade_date, "trade_time": "09:30:00", "qty": qty,
        "amount": amount, "commission": fees, "stamp_tax": "0",
        "transfer_fee": "0", "other_fee": "0",
    }


def test_two_round_trips_are_two_episodes():
    rows, coverage = build_episodes([
        fill("BUY", "100", "1000", "2026-09-01", fill_id="b1"),
        fill("SELL", "100", "1100", "2026-09-02", fill_id="s1"),
        fill("BUY", "100", "2000", "2026-09-03", fill_id="b2"),
        fill("SELL", "100", "1800", "2026-09-04", fill_id="s2"),
    ])
    assert [row["net_return_realized"] for row in rows] == [Decimal("0.1"), Decimal("-0.1")]
    assert len({row["position_id"] for row in rows}) == 2
    assert coverage == []


def test_oversell_is_coverage_not_a_return():
    rows, coverage = build_episodes([
        fill("BUY", "100", "1000", "2026-09-01", fill_id="b1"),
        fill("SELL", "200", "2200", "2026-09-02", fill_id="s1"),
    ])
    assert rows == []
    assert coverage == [{"account_hash": "acct", "code": "600000", "fill_id": "s1",
                         "reason": "UNMATCHED_OPENING_INVENTORY"}]


def test_partial_exit_uses_fifo_basis_and_retains_remaining_risk():
    rows, coverage = build_episodes([
        fill("BUY", "100", "1000", "2026-09-01", fill_id="b1"),
        fill("BUY", "100", "2000", "2026-09-01", fill_id="b2"),
        fill("SELL", "150", "2400", "2026-09-02", fill_id="s1"),
    ])
    assert coverage == []
    assert len(rows) == 1
    row = rows[0]
    assert row["exit_state"] == "PARTIAL_FILL"
    assert row["remaining_qty"] == Decimal("50")
    assert row["realized_pnl"] == Decimal("400")
    assert row["net_return_realized"] == Decimal("0.2")


def test_missing_fees_keep_episode_but_not_a_return():
    rows, coverage = build_episodes([
        fill("BUY", "100", "1000", "2026-09-01", fill_id="b1", fees=None),
        fill("SELL", "100", "1100", "2026-09-02", fill_id="s1"),
    ])
    assert coverage == []
    assert rows[0]["entry_reason"] == "FEES_MISSING"
    assert rows[0]["net_return_realized"] is None


def test_cash_event_is_coverage_not_a_trade_leg():
    cash = fill("OTHER", "0", "30", "2026-09-02", fill_id="d1")
    cash["qty"] = None
    rows, coverage = build_episodes([cash])
    assert rows == []
    assert coverage == [{"account_hash": "acct", "code": "600000", "fill_id": "d1",
                         "amount": "30", "reason": "UNRESOLVED_CASH_EVENT"}]

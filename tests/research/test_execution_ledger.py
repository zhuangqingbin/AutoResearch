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


def plan():
    import hashlib
    import json
    source={"schema_version":1,"venue":"XSHG","timezone":"Asia/Shanghai",
            "source_id":"fixture:exchange-calendar-export","published_at":"2026-08-30T00:00:00+08:00",
            "available_at":"2026-08-30T00:00:00+08:00",
            "sessions":[{"date":d,"open_at":d+"T09:30:00+08:00","close_at":d+"T15:00:00+08:00"}
                        for d in ["2026-08-31","2026-09-01","2026-09-02","2026-09-03"]]}
    digest=hashlib.sha256(json.dumps(source,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
    return {"analysis_session":"2026-08-31","calendar":{"source_sha256":digest,"source":source},"code": "600000", "ruler": "gap_c1_o2", "execution_mode": "OBSERVED_FILL",
            "cost_model_version": "actual_fees_v1",
            "entry_window_start": "2026-09-01T14:57:00+08:00",
            "entry_window_end": "2026-09-01T15:00:00+08:00",
            "exit_window_start": "2026-09-02T09:25:00+08:00",
            "exit_window_end": "2026-09-02T09:31:00+08:00"}


def overnight_fills():
    buy = fill("BUY", "100", "1000", "2026-09-01", fill_id="b1", fees="5")
    buy["trade_time"] = "14:59:00"
    return [buy, fill("SELL", "100", "1100", "2026-09-02", fill_id="s1", fees="5")]


def test_observed_execution_requires_complete_actual_fills_in_plan_window():
    from autoresearch.research import execution_ledger as ledger
    row = ledger.observed_execution(overnight_fills(), plan=plan())
    assert row["missing_reasons"] == []
    assert row["net_pnl_cash"] == Decimal("90")
    assert row["exit_complete"] is True and row["window_breached"] is False


def test_observed_execution_does_not_replace_missing_exit_with_open_price():
    from autoresearch.research import execution_ledger as ledger
    row = ledger.observed_execution(overnight_fills()[:1], plan=dict(plan(), exit_open=11))
    assert row["net_pnl_cash"] is None
    assert "INCOMPLETE_EXIT" in row["missing_reasons"]


def test_observed_execution_missing_fee_and_late_exit_are_not_labels():
    from autoresearch.research import execution_ledger as ledger
    fills = overnight_fills()
    fills[0]["commission"] = None
    fills[1]["trade_time"] = "10:00:00"
    row = ledger.observed_execution(fills, plan=plan())
    assert row["net_pnl_cash"] is None
    assert {"FEES_MISSING", "EXIT_OUTSIDE_WINDOW"} <= set(row["missing_reasons"])
    assert row["window_breached"] is True


def test_observed_execution_requires_plan_mode_cost_and_times():
    from autoresearch.research import execution_ledger as ledger
    row = ledger.observed_execution(overnight_fills(), plan={"code": "600000"})
    assert row["net_pnl_cash"] is None
    assert {"MISSING_EXECUTION_MODE", "MISSING_COST_MODEL", "MISSING_PLANNED_WINDOW"} <= set(row["missing_reasons"])


def test_observed_execution_duplicate_fill_ids_cannot_label_profit():
    from autoresearch.research import execution_ledger as ledger
    fills = overnight_fills()
    fills[1]["fill_id"] = fills[0]["fill_id"]
    result = ledger.observed_execution(fills, plan=plan())
    assert result["net_pnl_cash"] is None
    assert "DUPLICATE_FILL_ID" in result["missing_reasons"]


def test_observed_execution_missing_fill_time_and_id_are_named():
    from autoresearch.research import execution_ledger as ledger
    fills = overnight_fills()
    fills[0]["fill_id"] = ""
    fills[1]["trade_time"] = None
    result = ledger.observed_execution(fills, plan=plan())
    assert {"MISSING_FILL_ID", "MISSING_FILL_TIME"} <= set(result["missing_reasons"])
    assert result["window_breached"] is None and result["net_pnl_cash"] is None


def test_observed_execution_rejects_same_day_round_trip_as_overnight():
    from autoresearch.research import execution_ledger as ledger
    same_day = dict(plan(), entry_window_start="2026-09-02T09:00:00+08:00",
                    entry_window_end="2026-09-02T09:20:00+08:00")
    fills = overnight_fills()
    fills[0].update(trade_date="2026-09-02", trade_time="09:10:00")
    result = ledger.observed_execution(fills, plan=same_day)
    assert result["net_pnl_cash"] is None
    assert "INVALID_OVERNIGHT_DATES" in result["missing_reasons"]


def test_observed_execution_rejects_a_leg_window_spanning_local_dates():
    from autoresearch.research import execution_ledger as ledger
    for field, value in (("entry_window_start", "2026-08-31T14:57:00+08:00"),
                         ("exit_window_end", "2026-09-03T09:31:00+08:00")):
        result = ledger.observed_execution(overnight_fills(), plan=dict(plan(), **{field: value}))
        assert result["net_pnl_cash"] is None
        assert "INVALID_OVERNIGHT_DATES" in result["missing_reasons"]


def test_observed_execution_compares_exchange_local_dates_not_timestamp_dates():
    from autoresearch.research import execution_ledger as ledger
    same_day = dict(plan(), entry_window_start="2026-09-01T23:00:00+00:00",
                    entry_window_end="2026-09-01T23:30:00+00:00")
    fills = overnight_fills()
    fills[0].update(trade_date="2026-09-02", trade_time="07:10:00")
    result = ledger.observed_execution(fills, plan=same_day)
    assert result["net_pnl_cash"] is None
    assert "INVALID_OVERNIGHT_DATES" in result["missing_reasons"]


def test_actual_execution_requires_adjacent_frozen_calendar_sessions():
    from autoresearch.research import execution_ledger as ledger
    p=plan()
    fills=overnight_fills()
    p.update(exit_window_start='2026-10-02T09:25:00+08:00',exit_window_end='2026-10-02T09:31:00+08:00')
    fills[1]['trade_date']='2026-10-02'
    row=ledger.observed_execution(fills,plan=p)
    assert row['net_pnl_cash'] is None
    assert 'INVALID_D1_D2_CALENDAR' in row['missing_reasons']


def test_actual_execution_without_calendar_is_unlabelled():
    from autoresearch.research import execution_ledger as ledger
    p=plan()
    p.pop('calendar',None)
    row=ledger.observed_execution(overnight_fills(),plan=p)
    assert row['net_pnl_cash'] is None
    assert 'MISSING_FROZEN_CALENDAR' in row['missing_reasons']

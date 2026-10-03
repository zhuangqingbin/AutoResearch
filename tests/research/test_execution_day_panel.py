"""Synthetic fixtures only: complete calendar, no live brokerage or trades."""

from decimal import Decimal

import pytest

from autoresearch.research import execution_ledger as ledger
from tests.research.test_execution_ledger import fill

DAYS = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]


def policy():
    return {
        "schema_version": 1,
        "initial_cash": "10000",
        "initial_positions": [],
        "account_hash": "acct",
        "cash_daily_return": "0",
        "max_positions": 2,
        "max_weight": "0.5",
        "duplicate_plan_policy": "REJECT",
        "cost_model_version": "test.v1",
        "benchmark": "CASH",
        "simulation_rule": "snapshot_last.v1",
    }


def panel(*, decisions=None, fills=None, marks=None, rules=None, mode="OBSERVED_FILL"):
    return ledger.execution_day_panel(
        DAYS,
        decisions=decisions or {},
        fills=fills or [],
        marks=marks or {},
        policy=rules or policy(),
        evidence_mode=mode,
    )


def test_complete_days_keep_failure_abstention_and_unknown_execution_separate():
    result = panel(
        decisions={
            DAYS[0]: {"status": "ABSTAIN"},
            DAYS[1]: {"status": "TASK_FAILED"},
            DAYS[2]: {
                "status": "SELECTED",
                "execution_status": "NO_FILL",
                "execution_evidence": "order:cancelled",
            },
        }
    )
    rows = result["days"]
    assert [r["status"] for r in rows] == ["ABSTAIN", "TASK_FAILED", "NO_FILL", "DATA_UNKNOWN"]
    assert rows[0]["cash_return"] == "0"
    assert rows[1]["net_return"] is None
    assert result["summary"]["n_days"] == 4
    assert result["summary"]["total_return"] is None
    assert (
        panel(decisions={DAYS[0]: {"status": "SELECTED"}})["days"][0]["status"]
        == "EXECUTION_UNKNOWN"
    )
    assert (
        panel(decisions={DAYS[0]: {"status": "SELECTED", "execution_status": "NO_FILL"}})["days"][
            0
        ]["status"]
        == "EXECUTION_UNKNOWN"
    )


def test_partial_exit_carries_real_inventory_across_days_and_missing_marks():
    fills = [
        fill("BUY", "100", "1000", DAYS[0], fees="5"),
        fill("SELL", "40", "440", DAYS[1], fees="5"),
    ]
    decisions = {d: {"status": "ABSTAIN"} for d in DAYS}
    marks = {(DAYS[0], "600000"): "10", (DAYS[1], "600000"): "11", (DAYS[3], "600000"): "12"}
    result = panel(decisions=decisions, fills=fills, marks=marks)
    rows = result["days"]
    assert [r["positions"].get("600000", "0") for r in rows] == ["100", "60", "60", "60"]
    assert Decimal(rows[1]["realized_pnl"]) == 33
    assert rows[2]["nav"] is None and "MISSING_MARK" in rows[2]["missing_reasons"]
    assert rows[3]["nav"] == "10150"
    assert rows[3]["net_return"] is None  # Never bridge an unknown daily valuation.
    assert result["summary"]["total_return"] is None
    assert result["summary"]["n_marked_days"] == 3


def test_cash_only_complete_panel_has_defined_zero_and_benchmark():
    result = panel(decisions={d: {"status": "ABSTAIN"} for d in DAYS})
    assert result["summary"]["total_return"] == "0"
    assert result["summary"]["max_drawdown"] == "0"
    assert result["summary"]["cash_days"] == 4
    assert result["summary"]["benchmark_return"] == "0"


def test_missing_fees_persist_as_unknown_cash_and_do_not_erase_inventory():
    rows = panel(fills=[fill("BUY", "100", "1000", DAYS[0], fees=None)])["days"]
    assert all(r["cash"] is None and r["nav"] is None for r in rows)
    assert all(r["positions"]["600000"] == "100" for r in rows)


def test_initial_holdings_survive_and_partial_sales_use_registered_cost_basis():
    rules = policy()
    rules["initial_positions"] = [
        {
            "code": "600000",
            "qty": "100",
            "cost_basis": "900",
            "market_value": "1000",
            "sector": "银行",
        }
    ]
    result = panel(
        rules=rules,
        fills=[fill("SELL", "50", "550", DAYS[1])],
        decisions={d: {"status": "ABSTAIN"} for d in DAYS},
        marks={(d, "600000"): "11" for d in DAYS},
    )
    assert result["days"][1]["realized_pnl"] == "100"
    assert result["days"][3]["positions"]["600000"] == "50"


@pytest.mark.parametrize(
    "change",
    [
        "negative_cash",
        "unknown_rule",
        "duplicate_inventory",
        "account",
        "mode",
        "duplicate_fill",
        "off_calendar",
    ],
)
def test_panel_refuses_ambiguous_or_unregistered_inputs(change):
    rules = policy()
    fills = []
    mode = "OBSERVED_FILL"
    if change == "negative_cash":
        rules["initial_cash"] = "-1"
    if change == "unknown_rule":
        rules["duplicate_plan_policy"] = "BEST_RETURN"
    if change == "duplicate_inventory":
        rules["initial_positions"] = [
            {"code": "600000", "qty": "1", "cost_basis": "1", "market_value": "1"}
        ] * 2
    if change == "account":
        fills = [{**fill("BUY", "1", "1", DAYS[0]), "account_hash": "other"}]
    if change == "mode":
        mode = "MIXED"
    if change == "duplicate_fill":
        fills = [fill("BUY", "1", "1", DAYS[0])] * 2
    if change == "off_calendar":
        fills = [fill("BUY", "1", "1", "2020-01-01")]
    with pytest.raises(ValueError):
        panel(rules=rules, fills=fills, mode=mode)


def test_risk_limits_report_actual_breach_without_dropping_real_fill():
    result = panel(
        decisions={d: {"status": "ABSTAIN"} for d in DAYS},
        fills=[fill("BUY", "900", "9000", DAYS[0])],
        marks={(DAYS[0], "600000"): "10"},
    )
    assert "MAX_WEIGHT" in result["days"][0]["constraint_breaches"]
    assert result["days"][0]["positions"]["600000"] == "900"


def test_execution_audit_consumes_frozen_forward_days(tmp_path, monkeypatch):
    """The CLI consumer keeps all study dates, even without orders or market files."""
    import json

    from autoresearch.research import execution_audit as audit, forward_study as fs
    from tests.research.test_execution_audit import POLICY

    frozen = {
        "protocol_sha256": "a" * 64,
        "cost_model": dict(POLICY),
        "policy": policy(),
        "days": [{"date": d, "status": "ABSTAIN", "candidates": []} for d in DAYS],
        "protocol": {
            "days": [{"date": d} for d in DAYS],
            "template": {
                "split": {
                    "train": ["2025-01-01", "2025-02-01"],
                    "validation": ["2025-02-01", "2026-01-01"],
                    "test": ["2026-01-01", "2027-01-01"],
                }
            },
        },
    }
    frozen["policy"]["cost_model_version"] = POLICY["cost_model_version"]
    monkeypatch.setattr(fs, "execution_inputs", lambda directory: frozen)
    cost = tmp_path / "cost.json"
    cost.write_text(json.dumps(POLICY))
    output = audit.run(
        experiment_id="synthetic-panel",
        snapshots=None,
        trades=None,
        policy=cost,
        runs_root=None,
        lake_daily=None,
        max_age_seconds=60,
        parent=tmp_path / "output",
        engine="codex",
        forward_study=tmp_path / "registered",
    )
    value = json.loads((output / "execution_day_panel.json").read_bytes())
    assert len(value["modes"]) == 3
    assert all(len(p["days"]) == 4 for p in value["modes"].values())
    assert value["modes"]["OBSERVED_FILL"]["summary"]["total_return"] == "0"
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert "execution_day_panel.json" in manifest["outputs"]
    assert value["probability"]["population"] == 0


def test_simulation_requires_window_snapshots_never_daily_proxy(tmp_path):
    from autoresearch.research import execution_audit as audit
    from tests.research.test_execution_audit import POLICY
    from tests.research.test_execution_ledger import plan

    frozen = {
        "policy": {**policy(), "cost_model_version": POLICY["cost_model_version"]},
        "days": [
            {
                "date": "2026-08-31",
                "status": "SELECTED",
                "candidates": [
                    {
                        "plan": {**plan(), "execution_mode": "SNAPSHOT_SIMULATED"},
                        "qty": "100",
                        "weight": ".2",
                        "run_id": "synthetic",
                        "declaration": {},
                    }
                ],
            }
        ],
    }
    fills, coverage = audit.study_simulated_fills(frozen, [], POLICY)
    assert fills == [] and coverage[0]["reason"] == "MISSING_WINDOW_SNAPSHOT"


def test_simulated_entry_without_exit_snapshot_carries_position():
    from autoresearch.research import execution_audit as audit
    from tests.research.test_execution_audit import POLICY
    from tests.research.test_execution_ledger import plan

    planned = {**plan(), "execution_mode": "SNAPSHOT_SIMULATED"}
    frozen = {
        "policy": policy(),
        "days": [
            {
                "date": "2026-08-31",
                "status": "SELECTED",
                "candidates": [
                    {"plan": planned, "qty": "100", "weight": ".2", "run_id": "synthetic"}
                ],
            }
        ],
    }
    snapshot = {
        "snapshot_id": "s",
        "code": "600000",
        "run_id": "synthetic",
        "session_date": "2026-09-01",
        "market_event_at": "2026-09-01T14:59:50+08:00",
        "provider_published_at": "2026-09-01T14:59:51+08:00",
        "received_at": "2026-09-01T14:59:52+08:00",
        "persisted_at": "2026-09-01T14:59:53+08:00",
        "timestamp_precision": "second",
        "quality_flags": [],
        "suspended": False,
        "last": "10",
        "limit_down_price": "9",
        "limit_up_price": "11",
        "source_observation_id": "synthetic:tick",
    }
    legs, coverage = audit.study_simulated_fills(frozen, [snapshot], POLICY)
    assert [leg["side"] for leg in legs] == ["BUY"]
    assert coverage[-1]["side"] == "SELL"
    result = panel(
        mode="SNAPSHOT_SIMULATED", fills=legs, decisions={d: {"status": "ABSTAIN"} for d in DAYS}
    )
    assert all(r["positions"]["600000"] == "100" for r in result["days"])
    assert all(r["nav"] is None for r in result["days"])
    assert all(leg["evidence_mode"] == "SNAPSHOT_SIMULATED" for leg in legs)


def test_unknown_execution_cannot_be_relabelled_complete_by_one_partial_fill():
    result = panel(
        decisions={DAYS[0]: {"status": "SELECTED", "requested_qty": "200"}},
        fills=[fill("BUY", "100", "1000", DAYS[0])],
        marks={(DAYS[0], "600000"): "10"},
    )
    assert result["days"][0]["status"] == "PARTIAL_FILL"


def test_freeze_reader_keeps_all_60_calendar_days_and_verifies_candidate_bytes(
    tmp_path, monkeypatch
):
    import json

    from autoresearch.common.atomic import sha256_file
    from autoresearch.research import forward_study as fs

    root = tmp_path.resolve() / "study"
    root.mkdir()
    cfg = root / "portfolio.json"
    cfg.write_text(json.dumps(policy()))
    from tests.research.test_execution_audit import POLICY

    cost_file = root / "cost.json"
    cost_file.write_text(
        json.dumps({**POLICY, "cost_model_version": policy()["cost_model_version"]})
    )
    protocol = {
        "days": [{"date": d} for d in DAYS],
        "config_files": {
            "execution_portfolio": {"path": str(cfg), "sha256": sha256_file(cfg)},
            "execution_cost": {"path": str(cost_file), "sha256": sha256_file(cost_file)},
        },
        "registered_at": "2026-08-01T00:00:00Z",
    }
    (root / "protocol.json").write_text(json.dumps(protocol))
    (root / "registration.json").write_text(
        json.dumps({"protocol_sha256": sha256_file(root / "protocol.json")})
    )
    (root / "events.jsonl").write_text(json.dumps({"event": "DAY_FAILED", "date": DAYS[1]}) + "\n")
    data = fs.execution_inputs(root)
    assert [r["status"] for r in data["days"]] == [
        "DATA_UNKNOWN",
        "TASK_FAILED",
        "DATA_UNKNOWN",
        "DATA_UNKNOWN",
    ]
    cfg.write_text("{}")
    with pytest.raises(ValueError, match="policy changed"):
        fs.execution_inputs(root)


def test_mode_tag_on_foreign_fills_cannot_cross_into_observed_panel():
    with pytest.raises(ValueError, match="mixed evidence"):
        panel(fills=[{**fill("BUY", "100", "1000", DAYS[0]), "evidence_mode": "EOD_PROXY"}])


def test_initial_cash_zero_returns_unknown_without_division_error():
    rules = policy()
    rules["initial_cash"] = "0"
    result = panel(rules=rules, decisions={d: {"status": "ABSTAIN"} for d in DAYS})
    assert result["summary"]["total_return"] is None


def test_received_future_snapshot_cannot_simulate_entry():
    from autoresearch.research import execution_audit as audit
    from tests.research.test_execution_audit import POLICY
    from tests.research.test_execution_ledger import plan

    frozen = {
        "policy": policy(),
        "days": [
            {
                "candidates": [
                    {
                        "plan": {**plan(), "execution_mode": "SNAPSHOT_SIMULATED"},
                        "qty": "100",
                        "weight": ".2",
                        "run_id": "synthetic",
                    }
                ]
            }
        ],
    }
    snap = {
        "code": "600000",
        "run_id": "synthetic",
        "market_event_at": "2026-09-01T14:59:50+08:00",
        "provider_published_at": "2026-09-01T14:59:51+08:00",
        "received_at": "2026-09-02T14:59:52+08:00",
        "persisted_at": "2026-09-02T15:00:00+08:00",
        "timestamp_precision": "second",
    }
    fills, coverage = audit.study_simulated_fills(frozen, [snap], POLICY)
    assert fills == [] and coverage


def test_complete_actual_roundtrip_net_is_cashflow_not_plan_return():
    rows = [
        fill("BUY", "100", "1000", DAYS[0], fees="5"),
        fill("SELL", "100", "1100", DAYS[1], fees="5"),
    ]
    result = panel(
        fills=rows,
        decisions={d: {"status": "ABSTAIN"} for d in DAYS},
        marks={(DAYS[0], "600000"): "10"},
    )
    assert Decimal(result["summary"]["total_return"]) == Decimal(".009")
    assert Decimal(result["days"][1]["realized_pnl"]) == 90


def test_unexited_position_is_breached_on_exit_due_day():
    decisions = {d: {"status": "ABSTAIN", "exit_dates": {"600000": DAYS[1]}} for d in DAYS}
    result = panel(fills=[fill("BUY", "100", "1000", DAYS[0])], decisions=decisions)
    assert result["days"][1]["holding_window_breached"] is True


def test_entry_execution_does_not_erase_that_days_research_failure(monkeypatch):
    from autoresearch.research import execution_audit as audit, forward_study as fs
    from autoresearch.research.execution_ledger import execution_plan_hash
    from autoresearch.research.probability_eval import PLANNED_OVERNIGHT_EVENT
    from tests.research.test_execution_audit import POLICY
    from tests.research.test_execution_ledger import plan

    frozen = {
        "protocol_sha256": "a" * 64,
        "cost_model": dict(POLICY),
        "policy": {**policy(), "cost_model_version": POLICY["cost_model_version"]},
        "protocol": {
            "template": {
                "split": {
                    "train": ["2025-01-01", "2025-02-01"],
                    "validation": ["2025-02-01", "2026-01-01"],
                    "test": ["2026-01-01", "2027-01-01"],
                }
            }
        },
        "days": [
            {
                "date": "2026-08-31",
                "status": "SELECTED",
                "candidates": [
                    {
                        "plan": plan(),
                        "qty": "100",
                        "weight": ".2",
                        "run_id": "synthetic",
                        "declaration": {
                            "event_id": PLANNED_OVERNIGHT_EVENT,
                            "p": None,
                            "plan_hash": execution_plan_hash(plan()),
                            "declared_at": "2026-08-31T00:00:00Z",
                        },
                    }
                ],
            },
            {"date": "2026-09-01", "status": "TASK_FAILED", "candidates": []},
        ],
    }
    monkeypatch.setattr(fs, "execution_inputs", lambda directory: frozen)
    result = audit.execution_study_readout("synthetic", fills=[], snapshots=[], cost=POLICY)
    row = result["modes"]["OBSERVED_FILL"]["days"][1]
    assert row["research_status"] == "TASK_FAILED"
    assert row["status"] == "TASK_FAILED"
    assert row["execution_status"] == "EXECUTION_UNKNOWN"


def test_unrealized_position_value_is_separate_from_realized_cash_pnl():
    result = panel(
        fills=[
            fill("BUY", "100", "1000", DAYS[0], fees="5"),
            fill("SELL", "40", "440", DAYS[1], fees="5"),
        ],
        decisions={d: {"status": "ABSTAIN"} for d in DAYS},
        marks={(DAYS[0], "600000"): "10", (DAYS[1], "600000"): "11"},
    )
    assert Decimal(result["days"][1]["realized_pnl"]) == 33
    assert Decimal(result["days"][1]["unrealized_pnl"]) == 57
    assert Decimal(result["days"][1]["market_value_change"]) == -340


def test_future_plan_cannot_extend_current_positions_exit_window():
    decisions = {
        d: {
            "status": "ABSTAIN",
            "plan_windows": [
                {"code": "600000", "entry_date": DAYS[0], "exit_date": DAYS[1]},
                {"code": "600000", "entry_date": DAYS[2], "exit_date": DAYS[3]},
            ],
        }
        for d in DAYS
    }
    result = panel(decisions=decisions, fills=[fill("BUY", "100", "1000", DAYS[0])])
    assert result["days"][1]["holding_window_breached"] is True


def frozen_order_study(codes=("600000", "600001")):
    """Synthetic registered decisions for multi-order counterexamples."""
    from autoresearch.research.execution_ledger import execution_plan_hash
    from autoresearch.research.probability_eval import (
        PLANNED_OVERNIGHT_EVENT,
        execution_sizing_hash,
    )
    from tests.research.test_execution_audit import POLICY
    from tests.research.test_execution_ledger import plan

    candidates = []
    for code in codes:
        planned = {**plan(), "code": code, "cost_model_version": POLICY["cost_model_version"]}
        candidates.append(
            {
                "plan": planned,
                "qty": "100",
                "weight": ".2",
                "run_id": "synthetic",
                "declaration": {
                    "event_id": PLANNED_OVERNIGHT_EVENT,
                    "p": 0.5,
                    "declared_at": "2026-08-31T00:00:00Z",
                    "plan_hash": execution_plan_hash(planned),
                    "sizing_hash": execution_sizing_hash(planned, qty="100", weight=".2"),
                },
            }
        )
    return {
        "protocol_sha256": "a" * 64,
        "policy": {**policy(), "cost_model_version": POLICY["cost_model_version"]},
        "cost_model": dict(POLICY),
        "protocol": {
            "template": {
                "split": {
                    "train": ["2025-01-01", "2025-02-01"],
                    "validation": ["2025-02-01", "2026-01-01"],
                    "test": ["2026-01-01", "2027-01-01"],
                }
            }
        },
        "days": [
            {
                "date": day,
                "status": "SELECTED" if i == 0 else "ABSTAIN",
                "candidates": candidates if i == 0 else [],
            }
            for i, day in enumerate(["2026-08-31", "2026-09-01", "2026-09-02"])
        ],
    }


@pytest.mark.parametrize("reversed_order", [False, True])
def test_one_cancelled_order_does_not_erase_another_unknown_order(monkeypatch, reversed_order):
    from autoresearch.research import execution_audit as audit, forward_study as fs
    from tests.research.test_execution_audit import POLICY

    frozen = frozen_order_study()
    if reversed_order:
        frozen["days"][0]["candidates"].reverse()
    monkeypatch.setattr(fs, "execution_inputs", lambda _: frozen)
    result = audit.execution_study_readout(
        "synthetic",
        fills=[],
        snapshots=[],
        cost=POLICY,
        order_status=[
            {
                "session": "2026-09-01",
                "code": "600001",
                "evidence_mode": "OBSERVED_FILL",
                "state": "NO_FILL",
                "source_observation_id": "synthetic:cancel-second",
            }
        ],
    )
    value = result["modes"]["OBSERVED_FILL"]
    assert value["days"][1]["status"] == "EXECUTION_UNKNOWN"
    assert {r["code"]: r["state"] for r in value["days"][1]["order_states"]} == {
        "600000": "EXECUTION_UNKNOWN",
        "600001": "NO_FILL",
    }
    assert value["summary"]["total_return"] is None


def test_same_cost_version_cannot_replace_frozen_commission(monkeypatch):
    from autoresearch.research import execution_audit as audit, forward_study as fs
    from tests.research.test_execution_audit import POLICY

    frozen = frozen_order_study(("600000",))
    monkeypatch.setattr(fs, "execution_inputs", lambda _: frozen)
    with pytest.raises(ValueError, match="frozen cost"):
        audit.execution_study_readout(
            "synthetic", fills=[], snapshots=[], cost={**POLICY, "commission_rate": ".01"}
        )


@pytest.mark.parametrize("basis", [0, "0"])
def test_zero_initial_cost_basis_is_not_replaced_or_rejected(basis):
    rules = policy()
    rules["initial_positions"] = [
        {"code": "600000", "qty": "100", "cost_basis": basis, "market_value": "1000"}
    ]
    result = panel(
        rules=rules,
        decisions={d: {"status": "ABSTAIN"} for d in DAYS},
        fills=[fill("SELL", "100", "1100", DAYS[1])],
        marks={(DAYS[0], "600000"): "10"},
    )
    assert Decimal(result["days"][1]["realized_pnl"]) == 1100


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_any_fill_does_not_prove_execution_of_other_candidates(monkeypatch, side):
    from autoresearch.research import execution_audit as audit, forward_study as fs
    from tests.research.test_execution_audit import POLICY

    frozen = frozen_order_study()
    if side == "SELL":
        frozen["policy"]["initial_positions"] = [
            {"code": "600000", "qty": "100", "cost_basis": "1000", "market_value": "1000"}
        ]
    monkeypatch.setattr(fs, "execution_inputs", lambda _: frozen)
    result = audit.execution_study_readout(
        "synthetic",
        fills=[fill(side, "100", "1000", "2026-09-01")],
        snapshots=[],
        cost=POLICY,
        trade_evidence={"authorization": "synthetic explicit file"},
    )
    value = result["modes"]["OBSERVED_FILL"]
    assert value["days"][1]["status"] == "EXECUTION_UNKNOWN"
    assert value["summary"]["complete"] is False


def test_changed_actual_size_cannot_label_original_probability(monkeypatch):
    from autoresearch.research import execution_audit as audit, forward_study as fs
    from tests.research.test_execution_audit import POLICY

    frozen = frozen_order_study(("600000",))
    monkeypatch.setattr(fs, "execution_inputs", lambda _: frozen)
    buy = fill("BUY", "200", "2000", "2026-09-01", fees="5")
    buy["trade_time"] = "14:59:30"
    sell = fill("SELL", "200", "2015", "2026-09-02", fees="5")
    result = audit.execution_study_readout(
        "synthetic",
        fills=[buy, sell],
        snapshots=[],
        cost=POLICY,
        trade_evidence={"authorization": "synthetic"},
    )
    row = result["probability_rows"][0]
    assert row["y"] is None and "QUANTITY_PLAN_MISMATCH" in row["missing_reasons"]
    assert row["candidate_sha256"]
    assert result["modes"]["OBSERVED_FILL"]["planned_window_returns"][0]["net_return"] is None


def test_missing_sector_prevents_full_portfolio_concentration_readout():
    rules = policy()
    rules["initial_positions"] = [
        {
            "code": "600000",
            "qty": "100",
            "cost_basis": "1000",
            "market_value": "1000",
            "sector": "bank",
        },
        {
            "code": "600001",
            "qty": "100",
            "cost_basis": "1000",
            "market_value": "1000",
            "sector": None,
        },
    ]
    result = panel(
        rules=rules,
        decisions={d: {"status": "ABSTAIN"} for d in DAYS},
        marks={(d, c): "10" for d in DAYS for c in ("600000", "600001")},
    )
    assert result["days"][0]["max_sector_weight"] is None
    assert result["days"][0]["sector_coverage"] == {"known_positions": 1, "positions": 2}


def test_future_candidate_sector_does_not_rewrite_prior_holdings(monkeypatch):
    from autoresearch.research import execution_audit as audit, forward_study as fs
    from tests.research.test_execution_audit import POLICY

    frozen = frozen_order_study(("600000",))
    candidate = frozen["days"][0]["candidates"][0]
    candidate["sector"] = None
    frozen["days"][0].update(status="ABSTAIN", candidates=[])
    frozen["days"][-1].update(status="SELECTED", candidates=[candidate])
    frozen["policy"]["initial_positions"] = [
        {
            "code": "600000",
            "qty": "100",
            "cost_basis": "1000",
            "market_value": "1000",
            "sector": "bank",
        }
    ]
    snapshots = []
    for day in frozen["days"]:
        stamp = day["date"] + "T15:00:00+08:00"
        snapshots.append(
            {
                "code": "600000",
                "run_id": "synthetic",
                "session_date": day["date"],
                "last": "10",
                "market_event_at": stamp,
                "provider_published_at": stamp,
                "received_at": stamp,
                "persisted_at": stamp,
            }
        )
    monkeypatch.setattr(fs, "execution_inputs", lambda _: frozen)
    result = audit.execution_study_readout("synthetic", fills=[], snapshots=snapshots, cost=POLICY)
    first = result["modes"]["OBSERVED_FILL"]["days"][0]
    assert first["sector_coverage"] == {"known_positions": 1, "positions": 1}
    assert first["max_sector_weight"] is not None


@pytest.mark.parametrize("key,value", [("qty", "200"), ("weight", ".3")])
def test_candidate_sizing_cannot_change_after_probability_declaration(key, value):
    from datetime import datetime

    from autoresearch.research.forward_study import validate_execution_candidates

    candidate = frozen_order_study(("600000",))["days"][0]["candidates"][0]
    candidate[key] = value
    with pytest.raises(ValueError, match="sizing hash"):
        validate_execution_candidates(
            {"schema_version": 1, "status": "SELECTED", "candidates": [candidate]},
            analysis_date="2026-08-31",
            captured_at=datetime.fromisoformat("2026-08-31T01:00:00Z"),
        )

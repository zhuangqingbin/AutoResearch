"""Outcome accounting for OW and pinned-SELL ensemble folds."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.learning.ensemble_ledger import (
    fold_outcome,
    roll,
    trigger_summary,
)
from autoresearch.scan.decision_record import (
    DecisionRecord,
    write_decision_records,
)


def _record(
    code: str,
    *,
    source: str,
    final: str,
    ratings: list[str],
) -> DecisionRecord:
    return DecisionRecord.build(
        analysis_date="2026-07-28",
        contract_hash=None,
        code=code,
        source_rating=source,
        rubric_rating=source,
        gate_states={},
        early_stop=None,
        ensemble_ratings=ratings,
        final_rating=final,
        proposal="BUY" if final in {"Buy", "Overweight"} else "HOLD",
        reason="test",
        evidence_refs=[f"_ensemble_{code}.json"],
        first_rejection_stage="ENSEMBLE",
    )


def test_fold_outcome_uses_market_relative_two_point_band():
    assert fold_outcome("Overweight", "Hold", "ow_review", 0.02) == "FOLD_WRONG"
    assert fold_outcome("Buy", "Hold", "ow_review", -0.02) == "FOLD_RIGHT"
    assert fold_outcome("Overweight", "Hold", "ow_review", 0.019) == "FOLD_NEUTRAL"
    assert fold_outcome("Sell", "Hold", "sell_review", 0.02) == "FOLD_RIGHT"
    assert fold_outcome("Sell", "Hold", "sell_review", -0.02) == "FOLD_WRONG"
    assert fold_outcome("Overweight", "Hold", "ow_review", None) == "IMMATURE"


def _write_day(tmp_path):
    scan = tmp_path / "2026-07-28"
    (scan / "details").mkdir(parents=True)
    rows = [
        ("000001", "Overweight", "Hold", "ow_review", False, False, 0.04),
        ("000002", "Buy", "Hold", "ow_review", False, False, -0.04),
        ("000003", "Overweight", "Hold", "ow_review", False, False, 0.01),
        ("000004", "Overweight", "Overweight", "ow_review", True, False, 0.03),
        ("000005", "Underweight", "Underweight", "sell_review", False, True, 0.02),
        ("000006", "Sell", "Hold", "sell_review", False, False, None),
    ]
    records = []
    attr = [
        {
            "code": f"9{i:05d}",
            "fwd_2_oc": 0.0,
            "buyable": True,
            "tradable": True,
        }
        for i in range(20)
    ]
    for code, source, final, trigger, degraded, early, fwd in rows:
        ratings = [source, final] if early else [source, final, final]
        records.append(
            _record(code, source=source, final=final, ratings=ratings)
        )
        (scan / f"_ensemble_{code}.json").write_text(
            json.dumps(
                {
                    "code": code,
                    "ratings": ratings,
                    "median": final,
                    "spread": 1 if source != final else 0,
                    "degraded": degraded,
                    "trigger": trigger,
                    "n_runs": len(ratings),
                    "early_stopped": early,
                }
            ),
            encoding="utf-8",
        )
        # Deliberately contradictory card text: structured facts must win.
        (scan / "details" / f"{code}.md").write_text(
            "**Rating**: Sell\n",
            encoding="utf-8",
        )
        attr.append(
            {
                "code": code,
                "fwd_2_oc": fwd,
                "buyable": True,
                "tradable": True,
            }
        )
    write_decision_records(scan, records)
    (scan / "retro").mkdir()
    pd.DataFrame(attr).to_csv(scan / "retro" / "attribution.csv", index=False)
    return scan


def test_roll_uses_structured_facts_and_keeps_trigger_families_separate(tmp_path):
    _write_day(tmp_path)
    rows = roll(tmp_path).set_index("code")

    assert rows.loc["000001", "source_rating"] == "Overweight"
    assert rows.loc["000001", "final_rating"] == "Hold"
    assert rows.loc["000001", "verdict"] == "FOLD_WRONG"
    assert rows.loc["000002", "verdict"] == "FOLD_RIGHT"
    assert rows.loc["000003", "verdict"] == "FOLD_NEUTRAL"
    assert rows.loc["000004", "verdict"] == "UNDECIDABLE"
    assert bool(rows.loc["000005", "early_stopped"]) is True
    assert rows.loc["000005", "trigger"] == "sell_review"
    assert rows.loc["000006", "verdict"] == "IMMATURE"
    assert set(rows["trigger"]) == {"ow_review", "sell_review"}


def test_trigger_summary_blocks_rule_changes_until_ten_mature_folds():
    rows = pd.DataFrame(
        [
            {"trigger": "ow_review", "verdict": "FOLD_RIGHT"}
            for _ in range(9)
        ]
        + [{"trigger": "sell_review", "verdict": "FOLD_WRONG"} for _ in range(10)]
    )
    summary = trigger_summary(rows).set_index("trigger")
    assert summary.loc["ow_review", "status"] == "IMMATURE"
    assert summary.loc["sell_review", "status"] == "MATURE"


def test_present_corrupt_decision_book_is_loud(tmp_path):
    scan = _write_day(tmp_path)
    (scan / "decision_records.json").write_text("{", encoding="utf-8")
    with pytest.raises(Exception):
        roll(tmp_path)

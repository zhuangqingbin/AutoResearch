"""Causal zero-BUY day verdicts and hash-verified ledger."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from autoresearch.learning.abstention_ledger import (
    classify_abstention,
    load_abstention_verdict,
    render,
    roll,
    write_abstention_verdict,
)

NOW = datetime(2026, 7, 28, 18, 0, tzinfo=timezone.utc)


def _rows(excesses, *, bought=False, mature=True, buyable=True):
    return pd.DataFrame(
        [
            {
                "date": "2026-07-28",
                "code": f"{i:06d}",
                "first_rejection_stage": (
                    "BOUGHT" if bought and i == 1 else "L3_BENCH"
                ),
                "final_action": "BUY" if bought and i == 1 else "ABSTAIN",
                "gate_state_quality": "NOT_APPLICABLE",
                "buyable": buyable,
                "mature": mature,
                "excess_2": value,
                "opportunity": (
                    value is not None
                    and value >= 0.02
                    and not (bought and i == 1)
                ),
            }
            for i, value in enumerate(excesses, start=1)
        ]
    )


def test_absent_forward_returns_are_immature():
    verdict = classify_abstention(
        _rows([None, None], mature=False),
        now=NOW,
    )
    assert verdict.status == "IMMATURE"
    assert verdict.n_opportunities == 0


def test_rejected_buyable_plus_two_pp_is_false_abstention():
    verdict = classify_abstention(_rows([0.03, -0.01]), now=NOW)
    assert verdict.status == "FALSE"
    assert verdict.opportunity_codes == ["000001"]


def test_all_rejected_candidates_underperforming_is_correct():
    verdict = classify_abstention(_rows([-0.03, -0.02]), now=NOW)
    assert verdict.status == "CORRECT"
    assert verdict.data_quality == "COMPLETE"


def test_inside_economic_band_is_neutral():
    verdict = classify_abstention(_rows([-0.01, 0.01]), now=NOW)
    assert verdict.status == "NEUTRAL"


def test_degraded_control_facts_cannot_claim_correct():
    rows = _rows([-0.03, -0.02])
    rows.loc[0, "first_rejection_stage"] = "DATA_UNDECIDABLE"
    rows.loc[0, "gate_state_quality"] = "UNKNOWN"
    verdict = classify_abstention(rows, now=NOW)
    assert verdict.status == "NEUTRAL"
    assert verdict.data_quality == "DEGRADED"
    assert "undecidable_candidates" in verdict.reasons


def test_real_buy_day_is_not_abstained():
    verdict = classify_abstention(
        _rows([0.03, -0.02], bought=True),
        now=NOW,
    )
    assert verdict.status == "NOT_ABSTAINED"
    assert verdict.n_bought == 1


def test_verdict_round_trip_is_atomic_idempotent_and_tamper_loud(
    tmp_path,
):
    scan = tmp_path / "2026-07-28"
    (scan / "retro").mkdir(parents=True)
    path = write_abstention_verdict(
        scan,
        _rows([-0.03, -0.02]),
        now=NOW,
    )
    before = path.read_bytes()
    assert write_abstention_verdict(
        scan,
        _rows([-0.03, -0.02]),
        now=datetime(2026, 7, 28, 19, 0, tzinfo=timezone.utc),
    ).read_bytes() == before
    assert load_abstention_verdict(path).status == "CORRECT"
    assert not path.with_name(f"{path.name}.tmp").exists()

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["status"] = "FALSE"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_abstention_verdict(path)


def test_roll_and_render_separate_maturity_from_outcome(tmp_path):
    for date, excess in (
        ("2026-07-27", [None]),
        ("2026-07-28", [0.03]),
    ):
        scan = tmp_path / date
        (scan / "retro").mkdir(parents=True)
        rows = _rows(
            excess,
            mature=excess[0] is not None,
        )
        rows["date"] = date
        write_abstention_verdict(scan, rows, now=NOW)
    ledger = roll(tmp_path)
    assert list(ledger["status"]) == ["IMMATURE", "FALSE"]
    text = "\n".join(render(ledger))
    assert "IMMATURE" in text and "FALSE" in text

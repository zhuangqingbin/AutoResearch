"""Candidate first-death attribution on the T+2 primary horizon."""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.learning.rejection_attribution import (
    build_rejection_attribution,
    classify_first_death,
    write_rejection_attribution,
)
from autoresearch.scan.decision_record import (
    DecisionRecord,
    write_decision_records,
)

GATES = ("主力真在", "业绩真兑现", "估值不透支")


def _decision(
    code="000001",
    *,
    final="Hold",
    gates=None,
    early=None,
    first="L4_RUBRIC",
    source="Hold",
):
    return DecisionRecord.build(
        analysis_date="2026-07-28",
        contract_hash=None,
        code=code,
        source_rating=source,
        rubric_rating=source,
        gate_states=gates or dict.fromkeys(GATES, "PASS"),
        early_stop=early,
        ensemble_ratings=[],
        final_rating=final,
        proposal="BUY" if final in {"Buy", "Overweight"} else "HOLD",
        reason="test",
        evidence_refs=[f"details/{code}.md"],
        first_rejection_stage=first,
    )


def _classify(decision=None, **overrides):
    facts = {
        "in_l1": True,
        "recalled": True,
        "in_l2": True,
        "in_pass1_cut": False,
        "in_judged": True,
        "in_finalists": True,
        "decision": decision,
    }
    facts.update(overrides)
    return classify_first_death(**facts)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"in_l1": False}, "L0_EXCLUDED"),
        ({"recalled": False}, "L1_NOT_RECALLED"),
        ({"in_l2": False}, "L2_NOT_SELECTED"),
        ({"in_pass1_cut": True}, "PASS1_CUT"),
        ({"in_finalists": False}, "L3_BENCH"),
    ],
)
def test_upstream_first_death_is_unique(overrides, expected):
    stage, quality = _classify(**overrides)
    assert stage == expected
    assert quality == "NOT_APPLICABLE"


def test_l4_early_stop_precedes_gate_attribution():
    decision = _decision(
        early={"phase": "P3", "reason": "资金流出"},
        gates={
            "主力真在": "FAIL",
            "业绩真兑现": "PASS",
            "估值不透支": "PASS",
        },
        first="L4_P3_EARLY_STOP",
    )
    assert _classify(decision)[0] == "L4_EARLY_STOP"


@pytest.mark.parametrize(
    ("failed_gate", "expected"),
    [
        ("主力真在", "L4_GATE_MAIN"),
        ("业绩真兑现", "L4_GATE_EARNINGS"),
        ("估值不透支", "L4_GATE_VALUATION"),
    ],
)
def test_exactly_one_failed_gate_gets_unique_credit(
    failed_gate,
    expected,
):
    states = dict.fromkeys(GATES, "PASS")
    states[failed_gate] = "FAIL"
    assert _classify(_decision(gates=states)) == (
        expected,
        "COMPLETE",
    )


def test_multiple_failed_gates_form_one_multi_gate_sample():
    decision = _decision(
        gates={
            "主力真在": "FAIL",
            "业绩真兑现": "FAIL",
            "估值不透支": "PASS",
        }
    )
    assert _classify(decision) == ("L4_MULTI_GATE", "COMPLETE")


def test_unknown_gate_never_becomes_a_unique_fail():
    decision = _decision(
        gates={
            "主力真在": "FAIL",
            "业绩真兑现": "UNKNOWN",
            "估值不透支": "PASS",
        }
    )
    assert _classify(decision) == (
        "DATA_UNDECIDABLE",
        "UNKNOWN",
    )


def test_final_buy_and_post_card_fold_are_distinct():
    bought = _decision(
        final="Overweight",
        source="Overweight",
        first=None,
    )
    folded = _decision(
        final="Hold",
        source="Overweight",
        first="ENSEMBLE",
    )
    assert _classify(bought)[0] == "BOUGHT"
    assert _classify(folded)[0] == "ENSEMBLE_FOLDED"


def test_all_pass_but_low_rubric_score_has_own_bucket():
    decision = _decision(
        gates=dict.fromkeys(GATES, "PASS"),
        first="L4_RUBRIC",
    )
    assert _classify(decision) == (
        "L4_RUBRIC_SCORE",
        "COMPLETE",
    )


def _scan_fixture(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    codes = [f"{i:06d}" for i in range(1, 9)]
    pd.DataFrame({"code": codes[1:]}).to_csv(
        scan / "L1_scored_full.csv",
        index=False,
    )
    pd.DataFrame({"code": codes[2:]}).to_csv(
        scan / "L1_recall_top1000.csv",
        index=False,
    )
    pd.DataFrame({"code": codes[3:]}).to_csv(
        scan / "L2_gbdt_top200.csv",
        index=False,
    )
    pd.DataFrame({"code": [codes[3]]}).to_csv(
        scan / "_l3_pass1_cut.csv",
        index=False,
    )
    pd.DataFrame({"code": codes[4:]}).to_csv(
        scan / "L3_judged_full.csv",
        index=False,
    )
    pd.DataFrame({"code": codes[5:]}).to_csv(
        scan / "finalists.csv",
        index=False,
    )
    records = [
        _decision(
            codes[5],
            early={"phase": "P3", "reason": "资金流出"},
            first="L4_P3_EARLY_STOP",
        ),
        _decision(
            codes[6],
            gates={
                "主力真在": "FAIL",
                "业绩真兑现": "PASS",
                "估值不透支": "PASS",
            },
        ),
        _decision(
            codes[7],
            final="Overweight",
            source="Overweight",
            first=None,
        ),
    ]
    write_decision_records(scan, records)
    attr = pd.DataFrame(
        {
            "code": codes,
            "buyable": [True] * 8,
            "tradable": [True] * 8,
            "fwd_2_oc": [0.0] * 6 + [0.05, 0.01],
            "rating": [""] * 7 + ["Overweight"],
            "bought": [False] * 7 + [True],
        }
    )
    return scan, attr


def test_build_joins_funnel_decisions_and_market_relative_returns(
    tmp_path,
):
    scan, attr = _scan_fixture(tmp_path)
    result = build_rejection_attribution(scan, attr)
    by_code = result.set_index("code")

    assert list(result["first_rejection_stage"]) == [
        "L0_EXCLUDED",
        "L1_NOT_RECALLED",
        "L2_NOT_SELECTED",
        "PASS1_CUT",
        "L3_BENCH",
        "L4_EARLY_STOP",
        "L4_GATE_MAIN",
        "BOUGHT",
    ]
    assert by_code.at["000007", "market_fwd_2"] == 0.0
    assert by_code.at["000007", "excess_2"] == 0.05
    assert bool(by_code.at["000007", "opportunity"]) is True
    assert bool(by_code.at["000008", "opportunity"]) is False


def test_write_is_atomic_and_invalid_decision_book_is_loud(tmp_path):
    scan, attr = _scan_fixture(tmp_path)
    path = write_rejection_attribution(scan, attr)
    assert path == scan / "retro" / "rejection_attribution.csv"
    assert not path.with_name(f"{path.name}.tmp").exists()

    (scan / "decision_records.json").write_text("{", encoding="utf-8")
    with pytest.raises(Exception):
        build_rejection_attribution(scan, attr)

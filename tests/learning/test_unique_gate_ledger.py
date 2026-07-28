"""DecisionRecord-first unique/multi/unknown gate accountability."""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.learning.cross_calib import gate_stats
from autoresearch.learning.gate_ledger import roll as gate_roll
from autoresearch.learning.rejection_attribution import decision_gate_bucket
from autoresearch.scan.decision_record import (
    DecisionRecord,
    write_decision_records,
)

GATES = ("主力真在", "业绩真兑现", "估值不透支")


def _record(code, states):
    return DecisionRecord.build(
        analysis_date="2026-07-28",
        contract_hash=None,
        code=code,
        source_rating="Hold",
        rubric_rating="Hold",
        gate_states=states,
        early_stop=None,
        ensemble_ratings=[],
        final_rating="Hold",
        proposal="HOLD",
        reason="test",
        evidence_refs=[f"details/{code}.md"],
        first_rejection_stage="L4_RUBRIC",
    )


def test_gate_bucket_separates_unique_multi_unknown_and_pass():
    unique = _record(
        "000001",
        {
            "主力真在": "FAIL",
            "业绩真兑现": "PASS",
            "估值不透支": "PASS",
        },
    )
    multi = _record(
        "000002",
        {
            "主力真在": "FAIL",
            "业绩真兑现": "FAIL",
            "估值不透支": "PASS",
        },
    )
    unknown = _record(
        "000003",
        {
            "主力真在": "FAIL",
            "业绩真兑现": "UNKNOWN",
            "估值不透支": "PASS",
        },
    )
    passed = _record("000004", dict.fromkeys(GATES, "PASS"))

    assert decision_gate_bucket(unique) == "主力真在"
    assert decision_gate_bucket(multi) == "多门"
    assert decision_gate_bucket(unknown) == "不可判"
    assert decision_gate_bucket(passed) is None


def _day(tmp_path):
    scan = tmp_path / "2026-07-28"
    (scan / "retro").mkdir(parents=True)
    (scan / "details").mkdir()
    records = [
        _record(
            "000001",
            {
                "主力真在": "FAIL",
                "业绩真兑现": "PASS",
                "估值不透支": "PASS",
            },
        ),
        _record(
            "000002",
            {
                "主力真在": "FAIL",
                "业绩真兑现": "FAIL",
                "估值不透支": "PASS",
            },
        ),
        _record(
            "000003",
            {
                "主力真在": "UNKNOWN",
                "业绩真兑现": "PASS",
                "估值不透支": "PASS",
            },
        ),
    ]
    write_decision_records(scan, records)
    pd.DataFrame(
        [
            {
                "code": code,
                "fwd_1_oo": value,
                "fwd_2_oc": value,
                "fwd_5_oc": value,
            }
            for code, value in (
                ("000001", 0.03),
                ("000002", -0.03),
                ("000003", 0.0),
                ("999999", 0.0),
            )
        ]
    ).to_csv(scan / "retro" / "attribution.csv", index=False)
    # Contradictory legacy facts: valid DecisionRecord must win.
    (scan / "gate_fires.csv").write_text(
        "date,check,code,level\n"
        "2026-07-28,OW三门·业绩真兑现,000001,binding\n",
        encoding="utf-8",
    )
    for code in ("000001", "000002", "000003"):
        (scan / "details" / f"{code}.md").write_text(
            "OW三门:主力真在✓ · 业绩真兑现✗ · 估值不透支✓\n"
            "**Rating**: Hold\n",
            encoding="utf-8",
        )
    return scan


def test_cross_calib_uses_decision_facts_over_markdown(tmp_path):
    _day(tmp_path)
    stats = gate_stats(tmp_path, min_n=1).set_index("gate")
    assert set(stats.index) == {"主力真在", "多门", "不可判"}
    assert "业绩真兑现" not in stats.index
    assert stats.loc["主力真在", "n_blocked"] == 1


def test_gate_ledger_replaces_legacy_rubric_rows_without_double_count(
    tmp_path,
):
    _day(tmp_path)
    ledger = gate_roll(tmp_path, shrink=False).set_index("check")
    assert set(ledger.index) == {
        "OW三门·主力真在",
        "OW三门·多门",
        "OW三门·不可判",
    }
    assert ledger["n_fires"].sum() == 3


def test_present_corrupt_decision_book_is_loud(tmp_path):
    scan = _day(tmp_path)
    (scan / "decision_records.json").write_text("{", encoding="utf-8")
    with pytest.raises(Exception):
        gate_stats(tmp_path, min_n=1)
    with pytest.raises(Exception):
        gate_roll(tmp_path)

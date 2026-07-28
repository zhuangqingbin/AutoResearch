"""Early-stop deep reviews are stable, sampled, and shadow-only."""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from autoresearch.learning.earlystop_shadow import (
    build_shadow_ledger,
    reason_summary,
    sample_score,
    store_shadow_card,
    write_shadow_queue,
)
from autoresearch.scan.decision_record import (
    DecisionRecord,
    write_decision_records,
)


def _record(code: str, *, stopped: bool = True) -> DecisionRecord:
    return DecisionRecord.build(
        analysis_date="2026-07-28",
        contract_hash=None,
        code=code,
        source_rating="Hold",
        rubric_rating="Hold",
        gate_states={},
        early_stop=(
            {"phase": "P1", "reason": "赔率不够"} if stopped else None
        ),
        ensemble_ratings=[],
        final_rating="Hold",
        proposal="HOLD",
        reason="test",
        evidence_refs=[f"details/{code}.md"],
        first_rejection_stage="L4_EARLY_STOP" if stopped else "L4_RUBRIC",
    )


def test_sampling_is_sha256_stable():
    expected = int(
        hashlib.sha256(b"2026-07-28:000001").hexdigest()[:16],
        16,
    ) / 2**64
    assert sample_score("2026-07-28", "000001") == expected
    assert sample_score("2026-07-28", "000001") == sample_score(
        "2026-07-28",
        "000001",
    )


def test_queue_contains_only_sampled_early_stops_and_is_byte_stable(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    records = [_record(f"{i:06d}") for i in range(1, 51)]
    records.append(_record("999999", stopped=False))
    write_decision_records(scan, records)
    (scan / "details").mkdir()
    (scan / "details" / "sentinel.md").write_text("production", encoding="utf-8")
    before_book = (scan / "decision_records.json").read_bytes()
    before_card = (scan / "details" / "sentinel.md").read_bytes()

    path = write_shadow_queue(scan, sample_rate=0.15)
    first = path.read_bytes()
    second = write_shadow_queue(scan, sample_rate=0.15).read_bytes()
    payload = json.loads(first)
    expected = {
        record.code
        for record in records
        if record.early_stop is not None
        and sample_score(scan.name, record.code) < 0.15
    }

    assert first == second
    assert {item["code"] for item in payload["items"]} == expected
    assert "999999" not in expected
    assert (scan / "decision_records.json").read_bytes() == before_book
    assert (scan / "details" / "sentinel.md").read_bytes() == before_card
    assert not (scan / "shadow" / "earlystop_details").exists()


@pytest.mark.parametrize("rate", [0.09, 0.21])
def test_queue_rejects_out_of_policy_sample_rate(tmp_path, rate):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    write_decision_records(scan, [])
    with pytest.raises(ValueError, match="0.10"):
        write_shadow_queue(scan, sample_rate=rate)


def test_shadow_card_writer_is_confined_to_queue(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    records = [_record(f"{i:06d}") for i in range(1, 101)]
    write_decision_records(scan, records)
    queue = json.loads(write_shadow_queue(scan, sample_rate=0.20).read_text())
    code = queue["items"][0]["code"]

    path = store_shadow_card(
        scan,
        code,
        f"# 决策卡 — {code} test\n**Rating**: Overweight\n",
    )

    assert path == scan / "shadow" / "earlystop_details" / f"{code}.md"
    assert not (scan / "details").exists()
    with pytest.raises(ValueError, match="not queued"):
        store_shadow_card(scan, "999999", "**Rating**: Buy\n")


def test_ledger_compares_production_shadow_t2_and_stop_reason(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    records = [_record(f"{i:06d}") for i in range(1, 101)]
    write_decision_records(scan, records)
    queue = json.loads(write_shadow_queue(scan, sample_rate=0.20).read_text())
    code = queue["items"][0]["code"]
    store_shadow_card(
        scan,
        code,
        f"# 决策卡 — {code} test\n**Rating**: Overweight\n",
    )
    attr = pd.DataFrame(
        [
            {"code": code, "fwd_2_oc": 0.04, "buyable": True, "tradable": True},
            {"code": "999999", "fwd_2_oc": 0.00, "buyable": True, "tradable": True},
        ]
    )

    rows = build_shadow_ledger(scan, attr)

    assert len(rows) == 1
    row = rows.iloc[0]
    assert row["production_rating"] == "Hold"
    assert row["shadow_rating"] == "Overweight"
    assert row["stop_reason"] == "赔率不够"
    assert row["excess_2"] == 0.02


def test_stop_reason_stays_immature_until_ten_mature_shadow_reviews():
    nine = pd.DataFrame(
        [{"stop_reason": "赔率不够", "excess_2": 0.03}] * 9
    )
    ten = pd.concat(
        [nine, pd.DataFrame([{"stop_reason": "赔率不够", "excess_2": -0.01}])],
        ignore_index=True,
    )
    assert reason_summary(nine).iloc[0]["status"] == "IMMATURE"
    assert reason_summary(ten).iloc[0]["status"] == "MATURE"

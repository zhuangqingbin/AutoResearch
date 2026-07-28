"""L3 bench shadow basket stays deterministic and outside production decisions."""
from __future__ import annotations

import json
from math import ceil

import pandas as pd

from autoresearch.learning.l3_audit_ledger import (
    build_day_ledger,
    select_audit_candidates,
    write_audit_candidates,
    write_day_ledger,
)


def _bench() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"code": "000004", "conviction": 70, "fragility": 20, "lane": "value"},
            {"code": "000002", "conviction": 80, "fragility": 40, "lane": "trend"},
            {"code": "000001", "conviction": 80, "fragility": 40, "lane": "healthy"},
            {"code": "000003", "conviction": 80, "fragility": 10, "lane": "value"},
        ]
    )


def test_selector_caps_at_twenty_percent_and_uses_stable_tiebreaks():
    selected = select_audit_candidates(_bench(), finalist_max=10)
    assert len(selected) == ceil(10 * 0.20)
    assert selected["code"].tolist() == ["000001", "000002"]


def test_writer_is_byte_stable_and_does_not_touch_production_files(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    _bench().to_csv(scan / "_l3_bench.csv", index=False)
    (scan / "finalists.csv").write_text("code\n999999\n", encoding="utf-8")
    (scan / "decision_records.json").write_text('{"sentinel":true}\n', encoding="utf-8")
    before = {
        name: (scan / name).read_bytes()
        for name in ("finalists.csv", "decision_records.json")
    }

    first = write_audit_candidates(scan, finalist_max=10).read_bytes()
    second = write_audit_candidates(scan, finalist_max=10).read_bytes()

    assert first == second
    assert {
        name: (scan / name).read_bytes()
        for name in ("finalists.csv", "decision_records.json")
    } == before
    assert not (scan / "details").exists()


def test_day_ledger_measures_market_relative_opportunities_and_main_finalists(
    tmp_path,
):
    scan = tmp_path / "2026-07-28"
    (scan / "shadow").mkdir(parents=True)
    pd.DataFrame(
        [
            {"code": "000001", "conviction": 80, "fragility": 40},
            {"code": "000002", "conviction": 70, "fragility": 30},
        ]
    ).to_csv(scan / "shadow" / "l3_audit_candidates.csv", index=False)
    pd.DataFrame(
        [
            {"code": "000010", "lane": "trend"},
            {"code": "000011", "lane": "pinned"},
        ]
    ).to_csv(scan / "finalists.csv", index=False)
    attr = pd.DataFrame(
        [
            {"code": "000001", "fwd_2_oc": 0.04, "buyable": True, "tradable": True},
            {"code": "000002", "fwd_2_oc": -0.01, "buyable": True, "tradable": True},
            {"code": "000010", "fwd_2_oc": 0.01, "buyable": True, "tradable": True},
            {"code": "000011", "fwd_2_oc": 0.50, "buyable": True, "tradable": True},
            {"code": "999999", "fwd_2_oc": 0.00, "buyable": True, "tradable": True},
        ]
    )

    ledger = build_day_ledger(scan, attr)

    assert ledger["summary"] == {
        "candidate_n": 2,
        "mature_n": 2,
        "opportunity_n": 1,
        "mean_excess_2": 0.005,
        "main_finalist_mature_n": 1,
        "main_finalist_mean_excess_2": 0.0,
    }
    assert [row["opportunity"] for row in ledger["candidates"]] == [True, False]
    assert ledger["sample_status"] == "IMMATURE"
    assert ledger["minimum_forward_scan_days"] == 20

    path = write_day_ledger(scan, attr)
    assert json.loads(path.read_text(encoding="utf-8")) == ledger


def test_day_ledger_is_immature_without_t2_values(tmp_path):
    scan = tmp_path / "2026-07-28"
    (scan / "shadow").mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "conviction": 80}]).to_csv(
        scan / "shadow" / "l3_audit_candidates.csv",
        index=False,
    )
    ledger = build_day_ledger(
        scan,
        pd.DataFrame(
            [{"code": "000001", "fwd_2_oc": None, "buyable": True}]
        ),
    )
    assert ledger["summary"]["mature_n"] == 0
    assert ledger["summary"]["mean_excess_2"] is None
    assert ledger["sample_status"] == "IMMATURE"

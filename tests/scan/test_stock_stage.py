"""Per-stock L3/L4 StageResult facts."""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.stage_result import load_stage_result
from autoresearch.scan.stock_stage import (
    main,
    record_l3_results,
    record_l4_result,
)


def _write_judged_fixture(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    pd.DataFrame(
        [
            {
                "code": "000001",
                "lane": "trend",
                "conviction": 81,
                "fragility": "low",
            },
            {
                "code": "000002",
                "lane": "value",
                "conviction": 62,
                "fragility": "high",
            },
        ]
    ).to_csv(scan / "L3_judged_full.csv", index=False)
    pd.DataFrame([{"code": "000001"}]).to_csv(
        scan / "finalists.csv",
        index=False,
    )
    return scan


def _write_l4_fixture(
    tmp_path,
    *,
    with_card: bool,
    with_ensemble: bool,
):
    scan = tmp_path / "2026-07-28"
    (scan / "details").mkdir(parents=True)
    if with_card:
        (scan / "details" / "000001.md").write_text(
            "# 决策卡\n**Rating**: Overweight\n",
            encoding="utf-8",
        )
    if with_ensemble:
        (scan / "_ensemble_000001.json").write_text(
            json.dumps(
                {
                    "code": "000001",
                    "ratings": ["Overweight", "Hold", "Hold"],
                    "median": "Hold",
                    "spread": 1,
                    "degraded": False,
                    "trigger": "ow_review",
                }
            ),
            encoding="utf-8",
        )
    return scan


def test_record_l3_writes_one_result_per_judged_stock(tmp_path):
    scan = _write_judged_fixture(tmp_path)
    paths = record_l3_results(scan)

    assert len(paths) == 2
    selected = load_stage_result(
        scan / "stage_results" / "l3_000001.json"
    )
    bench = load_stage_result(
        scan / "stage_results" / "l3_000002.json"
    )
    assert selected.status == bench.status == "SUCCEEDED"
    assert selected.metrics["selected"] is True
    assert selected.metrics["lane"] == "trend"
    assert bench.metrics["selected"] is False


def test_record_l4_success_captures_card_and_ensemble(tmp_path):
    scan = _write_l4_fixture(
        tmp_path,
        with_card=True,
        with_ensemble=True,
    )
    result = record_l4_result(scan, "000001")

    assert result.status == "SUCCEEDED"
    assert result.metrics["source_rating"] == "Overweight"
    assert result.metrics["provisional_rating"] == "Hold"
    assert result.metrics["ensemble_present"] is True
    assert "final_rating" not in result.metrics


def test_record_l4_missing_card_is_failed_not_inferred_success(tmp_path):
    scan = _write_l4_fixture(
        tmp_path,
        with_card=False,
        with_ensemble=False,
    )
    result = record_l4_result(scan, "000001")

    assert result.status == "FAILED"
    assert result.metrics["card_present"] is False
    assert result.error == "L4 card missing"


def test_record_l4_marks_reused_card(tmp_path):
    scan = _write_l4_fixture(
        tmp_path,
        with_card=True,
        with_ensemble=False,
    )
    result = record_l4_result(scan, "000001", reused=True)
    assert result.metrics["reused"] is True


def test_l4_cli_is_argv_testable(tmp_path, capsys):
    scan = _write_l4_fixture(
        tmp_path,
        with_card=True,
        with_ensemble=False,
    )
    assert main(
        ["l4", scan.name, "000001", "--root", str(scan.parent)]
    ) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["stage"] == "l4_000001"
    assert shown["status"] == "SUCCEEDED"

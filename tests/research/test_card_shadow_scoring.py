"""卡上影子机读行的事后计分(2026-10-03 B5 / Q4 第二证据)。只读已发布记录与结果账本。"""
from __future__ import annotations

import csv
import json

from autoresearch.research import card_shadow_scoring as cs


def _run(root, name, cards):
    staging = root / name / "trace" / "staging"
    staging.mkdir(parents=True)
    (staging / "_card_shadow_fields.json").write_text(
        json.dumps({"schema_version": 1, "n_cards": len(cards), "cards": cards}), encoding="utf-8")


def _ledger(root, rows):
    path = root / "_ledger" / "recommendations.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["run_id", "code", "outcome_status", "actionability", "calendar_quality", "t1_close",
            "gap_c1_o2"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow({"outcome_status": "MATURE", "actionability": "ACTIONABLE",
                             "calendar_quality": "trade_cal", **row})


def test_each_card_is_scored_against_its_own_outcome(tmp_path):
    _run(tmp_path, "20261008-1008_2201", {
        "600001": {"mechanism": "成立", "ev": 1.2, "entry_vetoes": [
            {"op": "<", "threshold": 10.0, "raw": "close < 10 → 放弃"}]},
        "600002": {"mechanism": "不成立", "ev": -0.5, "entry_vetoes": []},
    })
    _ledger(tmp_path, [
        {"run_id": "20261008-1008_2201", "code": "600001", "t1_close": "9.5", "gap_c1_o2": "0.01"},
        {"run_id": "20261008-1008_2201", "code": "600002", "t1_close": "20", "gap_c1_o2": "-0.02"},
    ])

    rows = cs.readout(tmp_path)

    by_code = {r["code"]: r for r in rows}
    assert by_code["600001"]["veto_fired"] is True            # 9.5 < 10:按卡自己的否决就不该买
    assert by_code["600002"]["veto_fired"] is None            # 没写否决行:不是「没触发」
    summary = cs.summarize(rows)
    assert summary["mechanism"] == {"成立": {"n": 1, "mean_gap": 0.01},
                                    "不成立": {"n": 1, "mean_gap": -0.02}}
    assert summary["veto_fired"] == {"n": 1, "mean_gap": 0.01}


def test_unmatured_or_unknown_rows_are_left_out(tmp_path):
    _run(tmp_path, "20261008-1008_2201", {"600001": {"mechanism": "成立", "ev": 1.0,
                                                     "entry_vetoes": []}})
    _ledger(tmp_path, [{"run_id": "20261008-1008_2201", "code": "600001", "t1_close": "9",
                        "gap_c1_o2": "", "outcome_status": "PENDING"}])
    assert cs.readout(tmp_path) == []

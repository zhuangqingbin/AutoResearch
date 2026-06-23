"""跨日 channel ledger:聚合多日 channel_eval.csv → 每路滚动边际超额。NO network。"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.channel_ledger import render, roll


def _write_day(root, date, heat_ue, comp_ue):
    rd = root / date / "retro"
    rd.mkdir(parents=True)
    pd.DataFrame([
        {"channel": "heat", "n_recalled": 10, "n_unique": 3, "n_unbuyable": 0,
         "mean_excess_t5": heat_ue, "unique_excess_t5": heat_ue, "mean_excess_t1": 0.0, "hit_rate_t5": 0.6},
        {"channel": "composite", "n_recalled": 50, "n_unique": 5, "n_unbuyable": 0,
         "mean_excess_t5": comp_ue, "unique_excess_t5": comp_ue, "mean_excess_t1": 0.0, "hit_rate_t5": 0.4},
    ]).to_csv(rd / "channel_eval.csv", index=False)


def test_roll_aggregates_across_days(tmp_path):
    _write_day(tmp_path, "2026-06-18", 0.02, -0.01)
    _write_day(tmp_path, "2026-06-19", 0.04, -0.03)
    _write_day(tmp_path, "2026-06-20", 0.06, -0.02)
    led = roll(scan_root=tmp_path)
    heat = led[led["channel"] == "heat"].iloc[0]
    assert int(heat["n_days"]) == 3 and int(heat["sum_unique"]) == 9
    assert abs(heat["mean_unique_excess_t5"] - 0.04) < 1e-9          # mean(0.02,0.04,0.06)
    assert led.iloc[0]["channel"] == "heat"                          # 降序:heat 在 composite 前


def test_roll_empty_when_no_files(tmp_path):
    led = roll(scan_root=tmp_path)
    assert led.empty and "channel" in led.columns


def test_render_flags_thin_sample():
    led = pd.DataFrame([{"channel": "heat", "n_days": 2, "sum_unique": 4,
                         "mean_unique_excess_t5": 0.03, "mean_excess_t5": 0.02, "mean_hit_rate_t5": 0.6}])
    md = "\n".join(render(led))
    assert "⚠样本少" in md and "heat" in md and "+3.0%" in md

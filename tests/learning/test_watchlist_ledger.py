"""watchlist 触发→后市度量:roll join fwd / render / 空目录。合成,无网络。"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.watchlist_ledger import render, roll


def _mk_day(root, date, statuses, attr_rows=None):
    d = root / date
    d.mkdir(parents=True)
    pd.DataFrame(statuses).to_csv(d / "watchlist_status.csv", index=False)
    if attr_rows is not None:
        (d / "retro").mkdir()
        pd.DataFrame(attr_rows).to_csv(d / "retro" / "attribution.csv", index=False)


def test_roll_triggers_join_fwd(tmp_path):
    _mk_day(tmp_path, "2026-07-01",
            [{"code": "300476", "name": "胜宏科技", "status": "触发", "detail": "d",
              "narrative": "n", "born": "2026-06-30", "expiry": "2026-08-31"},
             {"code": "000001", "name": "甲", "status": "临近", "detail": "d",
              "narrative": "n", "born": "2026-06-30", "expiry": "2026-08-31"}],
            [{"code": "300476", "fwd_1_oo": 0.03, "fwd_5_oc": 0.08},
             {"code": "000001", "fwd_1_oo": 0.0, "fwd_5_oc": 0.0}])
    df = roll(tmp_path)
    assert len(df) == 1 and df.iloc[0]["code"] == "300476"        # 只统计触发行
    assert abs(df.iloc[0]["fwd_5"] - 0.08) < 1e-9
    md = "\n".join(render(df))
    assert "胜宏科技" in md and "触发" in md


def test_empty_graceful(tmp_path):
    assert len(roll(tmp_path)) == 0
    assert any("无" in ln for ln in render(roll(tmp_path)))

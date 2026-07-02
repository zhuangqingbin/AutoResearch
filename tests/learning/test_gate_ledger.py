"""门的跨日 ledger:roll 聚合 gate_fires×attribution / render / 空目录。合成,无网络。"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.gate_ledger import render, roll


def _mk_day(root, date, fires, attr_rows):
    d = root / date
    (d / "retro").mkdir(parents=True)
    pd.DataFrame(fires).to_csv(d / "gate_fires.csv", index=False)
    pd.DataFrame(attr_rows).to_csv(d / "retro" / "attribution.csv", index=False)


def test_roll_and_render(tmp_path):
    _mk_day(tmp_path, "2026-07-01",
            [{"date": "2026-07-01", "code": "000001", "check": "经验红线·获利盘满", "severity": "fail", "detail": "d"}],
            [{"code": "000001", "fwd_1_oo": -0.05, "fwd_5_oc": -0.10},
             {"code": "000002", "fwd_1_oo": 0.01, "fwd_5_oc": 0.02}])
    _mk_day(tmp_path, "2026-07-02",
            [{"date": "2026-07-02", "code": "000003", "check": "经验红线·获利盘满", "severity": "fail", "detail": "d"}],
            [{"code": "000003", "fwd_1_oo": 0.06, "fwd_5_oc": 0.08},
             {"code": "000004", "fwd_1_oo": 0.0, "fwd_5_oc": 0.0}])
    df = roll(tmp_path)
    row = df.set_index("check").loc["经验红线·获利盘满"]
    assert row["n_fires"] == 2 and row["n_days"] == 2
    md = "\n".join(render(df))
    assert "获利盘满" in md and "拦对率" in md


def test_empty_graceful(tmp_path):
    assert len(roll(tmp_path)) == 0
    assert any("无" in ln for ln in render(roll(tmp_path)))

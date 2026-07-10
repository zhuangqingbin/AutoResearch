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
            [{"code": "000001", "fwd_1_oo": -0.05, "fwd_2_oc": -0.07, "fwd_5_oc": -0.10},
             {"code": "000002", "fwd_1_oo": 0.01, "fwd_2_oc": 0.015, "fwd_5_oc": 0.02}])
    _mk_day(tmp_path, "2026-07-02",
            [{"date": "2026-07-02", "code": "000003", "check": "经验红线·获利盘满", "severity": "fail", "detail": "d"}],
            [{"code": "000003", "fwd_1_oo": 0.06, "fwd_2_oc": 0.07, "fwd_5_oc": 0.08},
             {"code": "000004", "fwd_1_oo": 0.0, "fwd_2_oc": 0.0, "fwd_5_oc": 0.0}])
    df = roll(tmp_path)
    row = df.set_index("check").loc["经验红线·获利盘满"]
    assert row["n_fires"] == 2 and row["n_days"] == 2
    assert "mean_ex2" in df.columns
    md = "\n".join(render(df))
    assert "获利盘满" in md and "拦对率" in md


def test_hit_rate_computed_from_ex2(tmp_path):
    # 单门单次拦截:ex1(按 fwd_1)为正(拦错),ex2(按 fwd_2,主尺)为负(拦对) → hit_rate 应按 ex2 判对。
    _mk_day(tmp_path, "2026-07-01",
            [{"date": "2026-07-01", "code": "000001", "check": "门X", "severity": "fail", "detail": "d"}],
            [{"code": "000001", "fwd_1_oo": 0.10, "fwd_2_oc": -0.05, "fwd_5_oc": 0.20},
             {"code": "000002", "fwd_1_oo": 0.0, "fwd_2_oc": 0.05, "fwd_5_oc": 0.0}])
    df = roll(tmp_path)
    row = df.set_index("check").loc["门X"]
    # market mean: ex1=0.05→ex1=+0.05(m1=0.05); ex2 mean=0(m2=0)→ex2=-0.05<0 → hit_rate=1.0
    assert abs(row["hit_rate"] - 1.0) < 1e-9


def test_empty_graceful(tmp_path):
    assert len(roll(tmp_path)) == 0
    assert any("无" in ln for ln in render(roll(tmp_path)))

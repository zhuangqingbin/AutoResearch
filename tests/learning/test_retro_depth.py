"""retro 度量深化:T+5 盲区(winner_5/bucket_5)/ L3 错杀验尸 / floor 自然实验。合成,无网络。

spec: docs/specs/2026-07-02-scan-retro-depth-metrics-design.md
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.learning.retro import attribute_frame, floor_experiment, l3_miss_autopsy


def _l1(n=40):
    return pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "composite": np.linspace(80, 20, n),
        "rank": range(1, n + 1),
        "recalled": [True] * 20 + [False] * (n - 20),   # 前 20 进 top1000
    })


def _realized(n=40, fwd5=True):
    f1 = np.linspace(-0.05, 0.15, n)                     # 后段是 T+1 赢家
    df = pd.DataFrame({"code": [f"{i:06d}" for i in range(n)],
                       "fwd_1_oo": f1, "buyable": True, "gap_d1": 0.0})
    if fwd5:
        df["fwd_5_oc"] = np.linspace(0.20, -0.10, n)     # 反向:前段是 T+5 赢家
    else:
        df["fwd_5_oc"] = np.nan                          # 未成熟
    return df


def test_winner5_and_bucket5():
    attr = attribute_frame(_l1(), _realized(), buylist={})
    w5 = attr[attr["winner_5"]]
    assert len(w5) > 0 and w5["fwd_5_oc"].min() >= 0.05          # 前10% ∧ ≥5%
    assert set(w5["bucket_5"]) <= {"caught", "recalled_cut", "missed_l1", "missed_l0"}
    assert (attr.loc[~attr["winner_5"], "bucket_5"] == "").all()
    # T+5 赢家在 codes 前段(recalled=True)→ 应归 recalled_cut 而非 missed
    assert (w5.iloc[0]["bucket_5"] == "recalled_cut")


def test_winner5_degrades_when_fwd5_immature():
    attr = attribute_frame(_l1(), _realized(fwd5=False), buylist={})
    assert not attr["winner_5"].any() and (attr["bucket_5"] == "").all()


def test_floor_experiment_groups():
    l2 = pd.DataFrame({"code": ["000001", "000002", "000003"],
                       "l2_lane_reserved": [1, 0, 0]})
    attr = pd.DataFrame({"code": ["000001", "000002", "000003", "000004"],
                         "recalled_flag": [True, True, True, True],
                         "fwd_1_oo": [0.01, 0.02, 0.03, -0.04],
                         "fwd_5_oc": [0.10, 0.20, 0.30, -0.40]})
    r = floor_experiment(l2, attr)
    assert r["floor"]["n"] == 1 and abs(r["floor"]["fwd5"] - 0.10) < 1e-9
    assert r["merit"]["n"] == 2 and abs(r["merit"]["fwd5"] - 0.25) < 1e-9
    assert r["cut"]["n"] == 1 and abs(r["cut"]["fwd5"] - (-0.40)) < 1e-9


def test_l3_miss_autopsy_joins_risk_text():
    l2 = pd.DataFrame({"code": ["000001", "000002", "000003"]})
    finalists = pd.DataFrame({"code": ["000003"]})
    judged = pd.DataFrame({"code": ["000001", "000002"],
                           "thesis": ["t1", "t2"], "risk": ["获利盘满", "PE高"],
                           "triage_lean": ["观察", "回避"], "lane": ["trend", "reversal"],
                           "conviction": [60, 55], "fragility": [50, 70]})
    attr = pd.DataFrame({"code": ["000001", "000002", "000003"],
                         "name": ["甲", "乙", "丙"],
                         "fwd_5_oc": [0.30, 0.01, 0.40], "winner_5": [True, False, True]})
    out = l3_miss_autopsy(attr, l2, finalists, judged)
    assert list(out["code"]) == ["000001"]               # 000003 是 finalist、000002 非 winner_5
    assert out.iloc[0]["risk"] == "获利盘满"
    empty = l3_miss_autopsy(attr, l2, finalists, judged.iloc[0:0])
    assert len(empty) == 0

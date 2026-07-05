"""催化旗票 vs 无旗票 fwd_5 对照(取证环;n<30 只记账不下结论)。合成,无网络。

spec: 2026-07-05 wave §WS-B3 —— IC 过硬前不入 composite、不设门。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.catalyst_ledger import render, roll


def _mk_day(root, date):
    d = root / date
    (d / "retro").mkdir(parents=True)
    pd.DataFrame([
        {"code": "000001", "rep_impl": 1, "rep_plan": 0, "holder_in": 0, "holder_de": 0, "surv_n": 0},
        {"code": "000002", "rep_impl": 0, "rep_plan": 0, "holder_in": 0, "holder_de": 3, "surv_n": 0},
        {"code": "000003", "rep_impl": 0, "rep_plan": 0, "holder_in": 0, "holder_de": 0, "surv_n": 0},
    ]).to_csv(d / "L3_catalyst.csv", index=False)
    pd.DataFrame([
        {"code": "000001", "fwd_5_oc": 0.05},
        {"code": "000002", "fwd_5_oc": -0.02},
        {"code": "000003", "fwd_5_oc": 0.01},
    ]).to_csv(d / "retro" / "attribution.csv", index=False)


def test_roll_flag_vs_unflag(tmp_path):
    _mk_day(tmp_path, "2026-07-03")
    df = roll(tmp_path)
    assert len(df) == 1
    r = df.iloc[0]
    # 正催化旗 = rep_impl+rep_plan+holder_in+surv_n > 0 → 只有 000001;减持不算正催化
    assert r["n_flag"] == 1 and r["n_unflag"] == 2
    assert abs(r["f5_flag"] - 0.05) < 1e-9 and abs(r["f5_unflag"] - (-0.005)) < 1e-9


def test_render_thin_gate(tmp_path):
    _mk_day(tmp_path, "2026-07-03")
    text = "\n".join(render(roll(tmp_path)))
    assert "取证中" in text and "< 30" in text          # 样本薄 → 只记账,不下结论
    assert "催化旗" in text


def test_roll_empty(tmp_path):
    assert len(roll(tmp_path)) == 0

"""催化旗票 vs 无旗票 fwd_5 对照(取证环;n<30 只记账不下结论)。合成,无网络。

spec: 2026-07-05 wave §WS-B3 —— IC 过硬前不入 composite、不设门。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.catalyst_ledger import render, roll


def _mk_day(root, date, fwd2=None, fwd5=(0.05, -0.02, 0.01), include_fwd5=True):
    d = root / date
    (d / "retro").mkdir(parents=True)
    pd.DataFrame([
        {"code": "000001", "rep_impl": 1, "rep_plan": 0, "holder_in": 0, "holder_de": 0, "surv_n": 0},
        {"code": "000002", "rep_impl": 0, "rep_plan": 0, "holder_in": 0, "holder_de": 3, "surv_n": 0},
        {"code": "000003", "rep_impl": 0, "rep_plan": 0, "holder_in": 0, "holder_de": 0, "surv_n": 0},
    ]).to_csv(d / "L3_catalyst.csv", index=False)
    rows = [{"code": "000001"}, {"code": "000002"}, {"code": "000003"}]
    if fwd2 is not None:
        for r, v in zip(rows, fwd2, strict=False):
            r["fwd_2_oc"] = v
    if include_fwd5:
        for r, v in zip(rows, fwd5, strict=False):
            r["fwd_5_oc"] = v
    pd.DataFrame(rows).to_csv(d / "retro" / "attribution.csv", index=False)


def test_roll_flag_vs_unflag(tmp_path):
    _mk_day(tmp_path, "2026-07-03", fwd2=(0.03, -0.01, 0.005))
    df = roll(tmp_path)
    assert len(df) == 1
    r = df.iloc[0]
    # 正催化旗 = rep_impl+rep_plan+holder_in+surv_n > 0 → 只有 000001;减持不算正催化
    assert r["n_flag"] == 1 and r["n_unflag"] == 2
    assert abs(r["f2_flag"] - 0.03) < 1e-9 and abs(r["f2_unflag"] - (-0.0025)) < 1e-9   # 主尺
    assert abs(r["f5_flag"] - 0.05) < 1e-9 and abs(r["f5_unflag"] - (-0.005)) < 1e-9    # 参考


def test_roll_matures_on_fwd2_only(tmp_path):
    """attr 只有 fwd_2_oc(无 fwd_5_oc)——成熟门提前到 fwd_2,仍应出行,f2_* 数值对。"""
    _mk_day(tmp_path, "2026-07-03", fwd2=(0.03, -0.01, 0.005), include_fwd5=False)
    df = roll(tmp_path)
    assert len(df) == 1
    r = df.iloc[0]
    assert r["n_flag"] == 1 and r["n_unflag"] == 2
    assert abs(r["f2_flag"] - 0.03) < 1e-9
    assert abs(r["f2_unflag"] - (-0.0025)) < 1e-9
    assert r["f5_flag"] is None and r["f5_unflag"] is None


def test_render_thin_gate(tmp_path):
    _mk_day(tmp_path, "2026-07-03")
    text = "\n".join(render(roll(tmp_path)))
    assert "取证中" in text and "< 30" in text          # 样本薄 → 只记账,不下结论
    assert "催化旗" in text


def test_roll_empty(tmp_path):
    assert len(roll(tmp_path)) == 0

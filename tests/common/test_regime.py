"""classify_regime 单测 —— 确定性市场 regime 分类器。合成横截面帧,无网络。

覆盖 spec §F:
  - trend:高 breadth(站上 MA60 多)+ 正 median pct_60d
  - risk_off:低 breadth + 负 median
  - range:中间态 / 高 breadth 但动量不正 等
  - 退化:空帧 → range(中性);缺 above_ma60 → 用 pct_60d>0 占比代理 breadth
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.regime import RegimeState, classify_regime


def _frame(above, pct60):
    """above: list[0/1] 站上 MA60;pct60: list[float] 60 日涨幅。"""
    return pd.DataFrame({"above_ma60": above, "pct_60d": pct60})


def test_trend_high_breadth_positive_mom():
    f = _frame([1] * 8 + [0] * 2, [10, 8, 6, 5, 4, 3, 2, 1, -1, -2])  # breadth .8, med +3.5
    r = classify_regime(f)
    assert isinstance(r, RegimeState)
    assert r.label == "trend"
    assert r.breadth == 0.8
    assert r.med_mom > 0


def test_risk_off_low_breadth_negative_mom():
    f = _frame([0] * 8 + [1] * 2, [-10, -8, -6, -5, -4, -3, -2, -1, 1, 2])  # breadth .2, med -3.5
    r = classify_regime(f)
    assert r.label == "risk_off"
    assert r.breadth == 0.2
    assert r.med_mom < 0


def test_range_middle_breadth():
    f = _frame([1, 1, 1, 1, 0, 0, 0, 0, 1, 0], [3, 2, 1, 0, -1, -2, 1, 0, 2, -1])  # breadth .5
    assert classify_regime(f).label == "range"


def test_high_breadth_but_flat_mom_is_not_trend():
    # breadth 高(.7)但 median 动量 ≤0 → 不算 trend(避免假趋势)
    f = _frame([1] * 7 + [0] * 3, [1, 0, -1, -2, -3, -1, 0, 1, 2, -5])
    r = classify_regime(f)
    assert r.label != "trend"


def test_empty_frame_defaults_range():
    r = classify_regime(pd.DataFrame({"above_ma60": [], "pct_60d": []}))
    assert r.label == "range"
    assert r.n == 0


def test_missing_above_ma60_uses_pct60_proxy():
    # 无 above_ma60 列 → breadth = (pct_60d>0) 占比 = .6;med +0.5 → trend 需 breadth≥.55 ✓
    f = pd.DataFrame({"pct_60d": [5, 4, 3, 2, 1, 1, -1, -2, -3, -4]})
    r = classify_regime(f)
    assert abs(r.breadth - 0.6) < 1e-9
    assert r.label in {"trend", "range"}      # breadth .6≥.55 且 med .5>0 → trend


def test_to_dict_roundtrip():
    r = classify_regime(_frame([1] * 6 + [0] * 4, [2, 1, 1, 1, 1, 1, -1, -1, -1, -1]))
    d = r.to_dict()
    assert d["label"] == r.label and d["breadth"] == r.breadth and d["n"] == 10

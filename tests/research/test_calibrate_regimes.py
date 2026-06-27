"""calibrate 的 regime 分桶核心单测 —— _weights_from_panel / _regimes_from_panel。合成 panel,无网络。

覆盖 spec §A(calibrate_regimes):
  - 同因子在 trend 桶 IC 正、risk_off 桶 IC 负(分桶能分离 regime 翻转)
  - 全样本 flat 把两 regime 冲洗成 ~0(印证静态 T+1 校准的 regime 错配根因)
  - min_dates 不足的 regime 不出块
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.sw_sector_map import super_sector
from autoresearch.research.factor_lab import _regimes_from_panel, _weights_from_panel


def _panel():
    """4 日:t1/t2=trend(momentum 与 fwd 正相关),t3/t4=risk_off(负相关)。grp_value 恒定(IC=NaN)。"""
    rows = []
    plan = [("t1", "trend", 1), ("t2", "trend", 1), ("t3", "risk_off", -1), ("t4", "risk_off", -1)]
    for date, regime, sign in plan:
        for i in range(40):                       # ≥30/日(_spearman 最小样本门)
            mom = i / 39.0
            rows.append({"grp_momentum": mom, "grp_value": (i * 7 % 13) / 13.0,  # 有方差噪声(IC≈0,免 corr warning)
                         "industry": "半导体", "sector": super_sector("半导体"),
                         "fwd": sign * mom, "date": date, "regime": regime})
    return pd.DataFrame(rows)


def test_regime_buckets_separate_sign():
    reg = _regimes_from_panel(_panel(), k=50.0, min_dates=1)
    assert reg["trend"]["weights"]["__global__"]["momentum"] > 0
    assert reg["risk_off"]["weights"]["__global__"]["momentum"] < 0


def test_flat_washes_out_regime():
    w, _icg = _weights_from_panel(_panel(), k=50.0)
    assert abs(w["__global__"]["momentum"]) < 0.2        # +1/-1 混合 → ~0(静态校准看不到 regime)


def test_min_dates_filters_thin_regime():
    reg = _regimes_from_panel(_panel(), k=50.0, min_dates=3)   # 每 regime 仅 2 日 < 3
    assert reg == {}


def test_weights_from_panel_has_global():
    w, _icg = _weights_from_panel(_panel(), k=50.0)
    assert "__global__" in w and "momentum" in w["__global__"]

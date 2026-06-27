"""detect_drift 单测 —— 今日 regime vs weights 校准基线。无网络。

覆盖 spec §D(regime drift):
  - 今日 regime ≠ 校准主导 regime → drift=True
  - 相同 → False
  - 无 regime_calib 基线(flat 校准)→ False + 提示
"""
from __future__ import annotations

from autoresearch.common.regime import RegimeState, detect_drift


def test_drift_when_regime_differs():
    d, reason = detect_drift(RegimeState("trend", 0.7, 3.0, 100), {"regime_calib": "range"})
    assert d is True
    assert "trend" in reason and "range" in reason


def test_no_drift_when_same():
    d, _ = detect_drift(RegimeState("range", 0.4, 0.0, 100), {"regime_calib": "range"})
    assert d is False


def test_no_baseline_no_drift():
    d, reason = detect_drift(RegimeState("trend", 0.7, 3.0, 100), {})
    assert d is False and "基线" in reason


def test_none_meta_no_drift():
    d, _ = detect_drift(RegimeState("risk_off", 0.2, -3.0, 50), None)
    assert d is False

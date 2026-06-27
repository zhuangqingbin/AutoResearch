"""assemble regime 标注 + drift 接线单测 —— L5 summary 顶 regime 行 + self_review warn。无网络。

覆盖 spec §Leaf L5 + §D(assemble 侧 drift 接线):
  - regime_and_drift 从 L1 帧算 regime 行;缺 L1 → 空(老路不破)
  - _self_review_banner 透传 regime_drift → banner 含 warn
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan import assemble


def test_regime_and_drift_empty_without_l1(tmp_path):
    line, drift = assemble.regime_and_drift(tmp_path)
    assert line == "" and drift == ""


def test_regime_and_drift_line_from_l1(tmp_path):
    pd.DataFrame({"above_ma60": [1] * 8 + [0] * 2,
                  "pct_60d": [10, 8, 6, 5, 4, 3, 2, 1, -1, -2]}).to_csv(
        tmp_path / "L1_scored_full.csv", index=False)
    line, _drift = assemble.regime_and_drift(tmp_path)
    assert "regime" in line and "趋势" in line          # breadth .8 + 正动量 → 趋势


def test_banner_surfaces_regime_drift(tmp_path):
    b = assemble._self_review_banner(
        tmp_path, rows=[{"code": "000001", "rating": "Hold"}],
        summary_text="x", regime_drift="当日 regime=trend ≠ 校准主导 range")
    assert "regime 漂移" in b

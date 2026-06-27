"""L1 regime-aware 权重接线单测 —— pick_weights + ScanConfig 默认。合成帧,无网络。

覆盖 spec §A 接线:
  - regime_aware=False(默认)→ 取 flat(regime=None),不分类(parity)
  - regime_aware=True → classify_regime(帧) → _load_weights(regime=label)
  - ScanConfig.regime_aware 默认 False(默认跑保形)
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.scoring import pick_weights
from autoresearch.scan.config import ScanConfig


def _fake_load(path, regime=None):
    return {"_path": path, "_regime": regime, "weights": {"__global__": {}}}


def test_off_uses_flat_no_classify():
    f = pd.DataFrame({"above_ma60": [1, 0], "pct_60d": [5, -5]})
    w, lab = pick_weights(f, False, load=_fake_load)
    assert lab is None
    assert w["_regime"] is None


def test_on_classifies_and_passes_regime():
    # breadth .8 + 正 median → trend
    f = pd.DataFrame({"above_ma60": [1] * 8 + [0] * 2, "pct_60d": [10, 8, 6, 5, 4, 3, 2, 1, -1, -2]})
    w, lab = pick_weights(f, True, load=_fake_load)
    assert lab == "trend"
    assert w["_regime"] == "trend"


def test_config_default_regime_aware_off():
    assert ScanConfig().regime_aware is False

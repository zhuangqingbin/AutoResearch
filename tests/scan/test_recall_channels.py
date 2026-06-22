"""8 路 channel:返回列契约 / 过门 / top-k / 缺列降级。NO network(合成 universe)。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.scan.recall import build, registered_channels
from tests.scan._synth_universe import synth_universe

_EIGHT = {"composite", "momentum", "reversal", "growth", "value",
          "main_fund", "northbound", "accumulation"}


def test_eight_channels_registered():
    assert set(registered_channels()) >= _EIGHT


def test_each_channel_returns_contract_and_respects_k():
    uni = synth_universe(n=400, seed=1)
    from autoresearch.common.scoring import _load_weights, composite_score
    scored = composite_score(uni, _load_weights())
    for name in _EIGHT:
        out = build(name)(scored, "2026-06-20", 50)
        assert list(out.columns) == ["code", "channel_rank", "channel_score"], f"{name} 列契约破"
        assert len(out) <= 50, f"{name} 超 k"
        if len(out):
            assert out["channel_rank"].tolist() == list(range(1, len(out) + 1)), f"{name} rank 非连续"
            assert np.isfinite(out["channel_score"].to_numpy()).all(), f"{name} score 非有限"


def test_channel_missing_column_degrades_to_empty():
    uni = pd.DataFrame({"code": [f"{i:06d}" for i in range(10)], "composite": range(10)})
    # 缺 hk_ratio/main_inflow 等 → northbound/accumulation 空帧不抛
    for name in ("northbound", "accumulation"):
        out = build(name)(uni, "2026-06-20", 50)
        assert out.empty and list(out.columns) == ["code", "channel_rank", "channel_score"]

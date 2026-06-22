"""recall registry + gate_rank:注册副作用 / build / defaults / 排序截断。NO network。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.scan.recall.base import gate_rank
from autoresearch.scan.recall.registry import CHANNEL_DEFAULTS, build, channel, registered_channels


def test_gate_rank_sorts_gates_and_truncates():
    frame = pd.DataFrame({"code": [f"{i:06d}" for i in range(5)],
                          "s": [1.0, 5.0, 3.0, float("nan"), 4.0],
                          "g": [True, True, True, True, False]})
    out = gate_rank(frame, frame["g"], "s", k=2)
    assert list(out.columns) == ["code", "channel_rank", "channel_score"]
    assert out["code"].tolist() == ["000001", "000002"]      # gated {000000:1,000001:5,000002:3,000003:nan},000004 门外;降序 top2
    assert out["channel_rank"].tolist() == [1, 2]


def test_gate_rank_missing_col_or_empty_returns_empty():
    frame = pd.DataFrame({"code": ["000001"], "s": [1.0]})
    assert gate_rank(frame, None, "nonexist", k=3).empty
    assert list(gate_rank(frame, None, "nonexist", k=3).columns) == ["code", "channel_rank", "channel_score"]


def test_channel_register_build_defaults():
    @channel("t_dummy", quota=7, floor=2, desc="d")
    def _dummy(frame, date, k):
        return gate_rank(frame, None, "s", k)
    assert "t_dummy" in registered_channels()
    assert build("t_dummy") is _dummy
    assert CHANNEL_DEFAULTS["t_dummy"].quota == 7 and CHANNEL_DEFAULTS["t_dummy"].floor == 2


def test_build_unknown_raises():
    with pytest.raises(KeyError):
        build("no_such_channel")

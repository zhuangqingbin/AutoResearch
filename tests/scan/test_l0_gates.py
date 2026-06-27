"""_recall_gate_a 流动性/次新硬门单测 —— 合成帧,无网络。

覆盖 spec §Leaf L0:
  - 默认(min=0)保留所有可交易(parity)
  - min_amount_yi 剔低流动性
  - min_list_days 剔次新(有 list_days 列才生效;缺列降级不剔)
  - ScanConfig 默认门 =0(parity)
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.config import ScanConfig
from autoresearch.scan.universe import _recall_gate_a


def _df(rows):
    return pd.DataFrame(rows)


def test_default_keeps_tradable():
    df = _df([{"amount_yi": 1.0, "close": 10, "pct_60d": 5, "pct_ytd": 3, "list_days": 1000}])
    assert bool(_recall_gate_a(df).iloc[0]) is True


def test_min_amount_filters_low_liquidity():
    df = _df([{"amount_yi": 0.1, "close": 10, "pct_60d": 5, "pct_ytd": 3},
              {"amount_yi": 2.0, "close": 10, "pct_60d": 5, "pct_ytd": 3}])
    assert list(_recall_gate_a(df, min_amount_yi=0.5)) == [False, True]


def test_min_list_days_filters_subnew():
    df = _df([{"amount_yi": 1, "close": 10, "pct_60d": 5, "pct_ytd": 3, "list_days": 20},
              {"amount_yi": 1, "close": 10, "pct_60d": 5, "pct_ytd": 3, "list_days": 400}])
    assert list(_recall_gate_a(df, min_list_days=60)) == [False, True]


def test_min_list_days_noop_when_column_absent():
    df = _df([{"amount_yi": 1, "close": 10, "pct_60d": 5, "pct_ytd": 3}])   # 无 list_days → 降级不剔
    assert bool(_recall_gate_a(df, min_list_days=60).iloc[0]) is True


def test_config_l0_gate_defaults_parity():
    c = ScanConfig()
    assert c.l0_min_amount_yi == 0.0 and c.l0_min_list_days == 0

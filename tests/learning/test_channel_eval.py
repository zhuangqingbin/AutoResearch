"""per-channel 前向归因 channel_edge + evaluate L1 段 + ratings 兜底。NO network(合成)。"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.stage_eval import channel_edge


def _recall():
    # 5 只,带 recall_channels provenance(| 分隔)
    return pd.DataFrame({
        "code": ["000001", "000002", "000003", "000004", "000005"],
        "recall_channels": ["composite|heat", "heat", "composite", "composite|momentum", "heat"],
        "n_channels": [2, 1, 1, 2, 1],
    })


def _realized():
    # 全市场(含 2 只未召回 000006/000007 以定全市场中位);000002 不可买
    return pd.DataFrame({
        "code": ["000001", "000002", "000003", "000004", "000005", "000006", "000007"],
        "fwd_1_oo": [0.05, 0.03, -0.02, 0.01, 0.04, 0.00, -0.01],
        "fwd_5_oc": [0.10, 0.06, -0.04, 0.02, 0.08, 0.00, -0.02],
        "buyable": [True, False, True, True, True, True, True],
    })


def test_channel_edge_unique_membership_buyable_excess():
    ce = channel_edge(_recall(), _realized())
    assert list(ce.columns) == ["channel", "n_recalled", "n_unique", "n_unbuyable",
                                "mean_excess_t5", "unique_excess_t5", "mean_excess_t1", "hit_rate_t5"]
    heat = ce[ce["channel"] == "heat"].iloc[0]
    # 全市场 fwd_5_oc 中位 = 0.02。heat members=000001/000002/000005,000002 不可买被剔。
    assert heat["n_recalled"] == 3 and heat["n_unique"] == 2 and heat["n_unbuyable"] == 1
    # mean_excess_t5(heat,buyable 000001/000005)=((0.10-0.02)+(0.08-0.02))/2 = 0.07
    assert abs(heat["mean_excess_t5"] - 0.07) < 1e-9
    # unique=只 heat 一路(000002 不可买、000005 可买)→ 仅 000005:0.08-0.02=0.06
    assert abs(heat["unique_excess_t5"] - 0.06) < 1e-9
    assert heat["hit_rate_t5"] == 1.0
    # composite unique=000003 一只:-0.04-0.02 = -0.06
    comp = ce[ce["channel"] == "composite"].iloc[0]
    assert abs(comp["unique_excess_t5"] - (-0.06)) < 1e-9
    # momentum 无独占票(000004 是 composite|momentum)→ unique_excess_t5 None
    mom = ce[ce["channel"] == "momentum"].iloc[0]
    assert mom["unique_excess_t5"] is None or pd.isna(mom["unique_excess_t5"])
    # 降序:heat(0.06) 排在 composite(-0.06) 前
    order = ce["channel"].tolist()
    assert order.index("heat") < order.index("composite")


def test_channel_edge_empty_or_no_provenance():
    assert channel_edge(pd.DataFrame(), _realized()).empty
    assert channel_edge(pd.DataFrame({"code": ["000001"]}), _realized()).empty  # 无 recall_channels 列

"""L3 误读三预警旗(spec 2026-07-08 T6;诊断:07-06 被打脸前提 22/31 证据纯 L3 表内可见)。"""
from __future__ import annotations

import math

import pandas as pd

from autoresearch.common.scoring import l3_misread_flags


def _row(**kw):
    base = {"np_yoy": 10.0, "roe": 12.0, "cmf_20": 0.0, "obv_mom_20": 0.0,
            "main_net_ratio": 0.01, "winner_rate": 60.0, "ma_bull": 1.0}
    base.update(kw)
    return base


def test_low_base_flag():
    assert "低基" in l3_misread_flags(_row(np_yoy=568.0, roe=4.1))
    assert "低基" not in l3_misread_flags(_row(np_yoy=568.0, roe=15.0))   # 高 ROE 真成长
    assert "低基" not in l3_misread_flags(_row(np_yoy=50.0, roe=4.0))


def test_flow_divergence_flag():
    assert "背离" in l3_misread_flags(_row(cmf_20=0.11, main_net_ratio=-0.02))
    assert "背离" in l3_misread_flags(_row(obv_mom_20=0.2, main_net_ratio=-0.01))
    assert "背离" not in l3_misread_flags(_row(cmf_20=0.11, main_net_ratio=0.02))


def test_trapped_flag():
    assert "套牢" in l3_misread_flags(_row(winner_rate=16.0, ma_bull=0.0))
    assert "套牢" not in l3_misread_flags(_row(winner_rate=16.0, ma_bull=1.0))  # 多头低 winner=真空间


def test_nan_never_flags_never_raises():
    assert l3_misread_flags(_row(np_yoy=math.nan, roe=math.nan)) == ""
    assert l3_misread_flags({"np_yoy": "x"}) == ""   # 缺列/脏值不抛


# staging 构造:整段复制自 tests/scan/test_l3_dist_flag.py 的 _mk/_row(勿跨测试文件 import
# 私有 fixture——house 裁例:宁可重复不共享脆弱内部),仅把因子列值改为可触发旗的组合
# (np_yoy=568/roe=4.1 → 低基)。
_DATE = "2026-07-08"


def _staging(tmp_path):
    d = tmp_path / _DATE
    d.mkdir(parents=True, exist_ok=True)
    row = {"code": "000001", "name": "甲", "industry": "电子", "composite": 80.0,
           "main_net_ratio": 0.01, "main_inflow_yi": 0.6, "cmf_20": 0.05,
           "pct_60d": 10.0, "pe": 30.0, "np_yoy": 568.0, "roe": 4.1,
           "obv_mom_20": 0.0, "winner_rate": 60.0, "ma_bull": 1.0}
    pd.DataFrame([row]).to_csv(d / "L2_gbdt_top200.csv", index=False)
    return d


def test_table_column_and_legend(tmp_path):
    from autoresearch.scan.agents.l3_select import l3_table_md
    date_dir = _staging(tmp_path)
    on = l3_table_md(date_dir.name, root=date_dir.parent, misread_flag=True)
    off = l3_table_md(date_dir.name, root=date_dir.parent, misread_flag=False)
    assert "misread" in on and "低基" in on and "自证" in on
    assert "misread" not in off


def test_l3_rank_agent_has_constraint_e():
    with open(".claude/agents/l3-rank.md", encoding="utf-8") as f:
        txt = f.read()
    assert "硬约束" in txt and "misread" in txt and "自证" in txt

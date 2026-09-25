# tests/analyze/test_index_membership.py
"""stock-research 确定性「指数成分 / 调样事件」行(design 2026-09-25 §2.6):只读湖,零网络。"""
from __future__ import annotations

import pandas as pd

from autoresearch.analyze.index_membership import index_membership_lines


def _lake(tmp_path):
    root = tmp_path / "lake"
    (root / "index_weight").mkdir(parents=True)
    (root / "csindex_rebalance_detail").mkdir(parents=True)
    pd.DataFrame({"index_code": "000300.SH", "con_code": ["600221.SH", "600000.SH"], "trade_date": "20261130",
                  "weight": 0.1}).to_parquet(root / "index_weight" / "000300_SH@20261130.parquet")
    pd.DataFrame({"index_code": "000510.SH", "con_code": ["600221.SH"], "trade_date": "20261130",
                  "weight": 0.2}).to_parquet(root / "index_weight" / "000510_SH@20261130.parquet")
    pd.DataFrame([{"ann_id": "3007001", "publish_date": "20261127", "title": "t",
                   "content_text": "将于2026年12月11日收市后生效", "attachment_url": "u",
                   "index_code": "000300", "index_name": "沪深300", "side": "add", "code": "600221", "name": "海航控股"}]
                 ).to_parquet(root / "csindex_rebalance_detail" / "3007001@20261127.parquet")
    return root


def test_membership_and_recent_event_lines(tmp_path):
    root = _lake(tmp_path)
    s = index_membership_lines("600221", "2026-12-10", lake_root=root)
    assert "当前属于 沪深300 · 中证A500" in s and "20261130" in s
    assert "2026-11-27 公告 沪深300 调入,2026-12-11 收盘生效" in s


def test_non_member_and_no_event_lines(tmp_path):
    root = _lake(tmp_path)
    s = index_membership_lines("600000", "2026-12-10", lake_root=root)
    assert "当前属于 沪深300" in s and "中证A500" not in s
    assert "近 60 日无调样事件(源可达)" in s


def test_absent_lake_is_named_not_faked(tmp_path):
    s = index_membership_lines("600221", "2026-12-10", lake_root=tmp_path / "nolake")
    assert "指数成分快照:湖内无" in s and "调样事件:源无近 60 日快照" in s

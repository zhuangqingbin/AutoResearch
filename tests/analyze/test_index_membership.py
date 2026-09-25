# tests/analyze/test_index_membership.py
"""stock-research 确定性「指数成分 / 调样事件」行(design 2026-09-25 §2.6):只读湖,零网络。"""
from __future__ import annotations

import pandas as pd

from autoresearch.analyze.index_membership import index_membership_lines


def _lake(tmp_path):
    root = tmp_path / "lake"
    (root / "index_weight").mkdir(parents=True)
    (root / "csindex_rebalance_detail").mkdir(parents=True)
    (root / "csindex_rebalance_list").mkdir(parents=True)
    pd.DataFrame({"index_code": "000300.SH", "con_code": ["600221.SH", "600000.SH"], "trade_date": "20261130",
                  "weight": 0.1}).to_parquet(root / "index_weight" / "000300_SH@20261130.parquet")
    pd.DataFrame({"index_code": "000510.SH", "con_code": ["600221.SH"], "trade_date": "20261130",
                  "weight": 0.2}).to_parquet(root / "index_weight" / "000510_SH@20261130.parquet")
    pd.DataFrame([{"ann_id": "3007001", "publish_date": "20261127", "title": "t",
                   "content_text": "将于2026年12月11日收市后生效", "attachment_url": "u",
                   "index_code": "000300", "index_name": "沪深300", "side": "add", "code": "600221", "name": "海航控股"}]
                 ).to_parquet(root / "csindex_rebalance_detail" / "3007001@20261127.parquet")
    # fix round 1 #2(2026-09-26,coordinator review):list 快照(每次 harvest 跑动都会落一份,
    # 不管当天有没有真事件)是"源有没有被查过"的信号,与 detail 是否命中这只票彼此独立——
    # 这份 fixture 原来没有 list 目录,靠"detail 文件存在即视为源可达"这条已被推翻的推断也能
    # 凑巧过关;现在两个信号分开算,凡是想表达"源可达"的用例都必须显式给一份窗内 list 快照。
    pd.DataFrame({"ann_id": ["3007001"], "title": ["t"], "publish_date": ["20261127"], "theme": [None]}
                 ).to_parquet(root / "csindex_rebalance_list" / "all@20261127.parquet")
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


def _lake_no_detail_but_a_list_snapshot(tmp_path, list_date):
    """成分照旧(600221 属于 000300),但**没有任何 detail 公告**——只放一份 list 快照,
    用来单独控制"源有没有被查过"这个信号,与 detail 是否命中这只票脱钩。"""
    root = tmp_path / "lake"
    (root / "index_weight").mkdir(parents=True)
    (root / "csindex_rebalance_detail").mkdir(parents=True)
    (root / "csindex_rebalance_list").mkdir(parents=True)
    pd.DataFrame({"index_code": "000300.SH", "con_code": ["600221.SH"], "trade_date": "20261130",
                  "weight": 0.1}).to_parquet(root / "index_weight" / "000300_SH@20261130.parquet")
    pd.DataFrame({"ann_id": ["x"], "title": ["t"], "publish_date": ["20261101"], "theme": [None]}
                 ).to_parquet(root / "csindex_rebalance_list" / f"all@{list_date}.parquet")
    return root


def test_quiet_window_with_a_consulted_source_is_not_confused_with_never_consulted(tmp_path):
    """fix round 1 #2(2026-09-26,coordinator review):六指数半年/季度才调一次样,近 60 日本来
    就大概率没有任何公告——那是正常的安静期,不是"从没跑过 harvest"。list 快照落在窗内,即使
    一条 detail 公告都没有,也必须读成"源可达",不能与"从没被查过"渲染成同一句话。"""
    root = _lake_no_detail_but_a_list_snapshot(tmp_path, "20261201")   # 窗内(60 日窗:20261011~20261210)
    s = index_membership_lines("600221", "2026-12-10", lake_root=root)
    assert "近 60 日无调样事件(源可达)" in s


def test_list_snapshot_outside_the_window_still_reads_as_never_consulted(tmp_path):
    """同一个"有没有被查过"信号,换成窗外的 list 快照(200+ 天前)——必须读成"源无近 60 日快照",
    不能因为湖里某处曾经有过一份 list 快照就误判成"最近查过"。"""
    root = _lake_no_detail_but_a_list_snapshot(tmp_path, "20260501")   # 窗外
    s = index_membership_lines("600221", "2026-12-10", lake_root=root)
    assert "调样事件:源无近 60 日快照" in s

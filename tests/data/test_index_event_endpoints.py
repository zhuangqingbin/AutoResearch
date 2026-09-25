"""指数调样事件源的数据层登记(design 2026-09-25 §2.1):端点 policy、B 级契约、缓存键。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import cache, contracts, endpoints

SEVEN = ("csindex_rebalance_list", "csindex_rebalance_detail", "index_weight",
         "index_basic", "fund_basic", "fund_share", "fund_nav")


@pytest.fixture(autouse=True)
def _clean_degradations():
    contracts.clear_degradations()
    yield
    contracts.clear_degradations()


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", root)
    return root


@pytest.mark.parametrize("name,key,source", [
    ("csindex_rebalance_list", "as_of", "csindex"),
    ("csindex_rebalance_detail", "as_of", "csindex"),
    ("index_weight", "as_of", "tushare"),
    ("index_basic", "static", "tushare"),
    ("fund_basic", "static", "tushare"),
    ("fund_share", "date", "tushare"),
    ("fund_nav", "date", "tushare"),
])
def test_index_event_endpoints_registered(name, key, source):
    pol = endpoints.policy(name)
    assert pol["key"] == key and pol["source"] == source and pol["settle"] == "eod"


def test_rebalance_list_is_a_snapshot_endpoint():
    assert endpoints.policy("csindex_rebalance_list").get("snapshot") is True
    assert endpoints.policy("csindex_rebalance_detail").get("snapshot") is None   # 详情不可变,不是快照


def test_all_seven_have_tier_b_contracts():
    for name in SEVEN:
        assert contracts.CONTRACTS[name].tier == contracts.TIER_DEGRADE, name
    for name in ("csindex_rebalance_list", "csindex_rebalance_detail"):
        assert contracts.CONTRACTS[name].persist_violations is False, name   # 脆源:半截/空不入湖
    assert contracts.CONTRACTS["fund_share"].empty_ok is True
    assert contracts.CONTRACTS["fund_nav"].empty_ok is True


def test_index_weight_key_is_index_code_at_month_end(lake):
    params = {"index_code": "000300.SH", "start_date": "20260601", "end_date": "20260630"}
    p = cache.lake_path("index_weight", params, today="20260630")
    assert p == lake / "index_weight" / "000300_SH@20260630.parquet"
    q = cache.lake_path("index_weight", {**params, "index_code": "000905.SH"}, today="20260630")
    assert p != q                                             # 两个指数同月不撞键


def test_detail_key_is_announcement_id(lake):
    p = cache.lake_path("csindex_rebalance_detail", {"ann_id": "3006227"}, today="20260925")
    q = cache.lake_path("csindex_rebalance_detail", {"ann_id": "3006244"}, today="20260925")
    assert p.name == "3006227@20260925.parquet" and p != q     # 两个公告不撞键


def test_fund_nav_keys_on_nav_date(lake):
    assert cache.lake_path("fund_nav", {"nav_date": "20260924"}).name == "20260924.parquet"


def test_list_key_is_all_at_today(lake):
    assert cache.lake_path("csindex_rebalance_list", {}, today="20260925").name == "all@20260925.parquet"


def test_empty_rebalance_list_degrades_and_refuses_lake(lake, monkeypatch):
    monkeypatch.setattr(cache, "_real_today", lambda: "20260925")
    empty = pd.DataFrame(columns=["ann_id", "title", "publish_date", "theme"])
    out = cache.get_or_fetch("csindex_rebalance_list", {}, today="20260925", fetch=lambda e, p: empty)
    assert out.empty
    assert any(r["endpoint"] == "csindex_rebalance_list" for r in contracts.degradations())
    assert not list((lake / "csindex_rebalance_list").glob("*.parquet"))   # 违约不入湖(C2)

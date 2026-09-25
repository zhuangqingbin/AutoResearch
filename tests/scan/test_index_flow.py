# tests/scan/test_index_flow.py
"""ETF 被动规模 → flow_adv_days 描述字段(design 2026-09-25 §2.2;批 B3)。合成湖 + 假取数,零网络。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import cache
from autoresearch.scan import index_events as ie, index_flow as fl

AS_OF = "20261210"
DAYS = [f"202611{d:02d}" for d in range(2, 31) if d not in (7, 8, 14, 15, 21, 22, 28, 29)] + \
       ["20261201", "20261202", "20261203", "20261204", "20261207", "20261208", "20261209", "20261210"]


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "lake"
    (root / "daily").mkdir(parents=True)
    (root / "daily_basic").mkdir(parents=True)
    monkeypatch.setattr(cache, "LAKE", root)
    for d in DAYS:
        pd.DataFrame({"ts_code": ["600221.SH", "600000.SH", "600036.SH"], "open": 10.0, "high": 10.0, "low": 10.0,
                      "close": 10.0, "pre_close": 10.0, "change": 0.0, "pct_chg": 0.0, "vol": 1.0,
                      "amount": [1e5, 5e5, 5e5]}).to_parquet(root / "daily" / f"{d}.parquet")   # 600221 日均 1 亿
    pd.DataFrame({"ts_code": ["600221.SH", "600000.SH", "600036.SH"], "close": 10.0, "turnover_rate": 1.0,
                  "pe_ttm": 10.0, "pb": 1.0, "total_mv": 1e6, "circ_mv": [1e6, 1e6, 1e6]}).to_parquet(
        root / "daily_basic" / f"{AS_OF}.parquet")                                            # 各 100 亿流通
    return root


def _fetch(endpoint, params):
    if endpoint == "fund_basic":
        return pd.DataFrame({"ts_code": ["510300.SH", "512100.SH"], "name": ["华泰柏瑞沪深300ETF", "南方中证1000ETF"],
                             "benchmark": ["沪深300指数收益率", "中证1000指数收益率"]})
    if endpoint == "fund_share":
        return pd.DataFrame({"ts_code": ["510300.SH", "512100.SH"], "trade_date": params["trade_date"],
                             "fd_share": [1_000_000.0, 100_000.0], "fund_type": "ETF", "market": "SH"})  # 万份
    if endpoint == "fund_nav":
        return pd.DataFrame({"ts_code": ["510300.SH", "512100.SH"], "nav_date": params["nav_date"],
                             "unit_nav": [1.0, 1.0]})
    if endpoint == "index_weight":
        return pd.DataFrame({"index_code": params["index_code"], "con_code": ["600000.SH", "600036.SH"],
                             "trade_date": "20261130", "weight": 50.0})
    raise AssertionError(endpoint)


def test_etf_aum_by_index_sums_share_times_nav(lake):
    aum = fl.etf_aum_by_index(AS_OF, fetch=_fetch)
    assert aum["000300"] == pytest.approx(100.0) and aum["000852"] == pytest.approx(10.0)   # 亿元


def test_etf_aum_returns_none_when_any_source_is_missing(lake):
    def broken(endpoint, params):
        if endpoint == "fund_nav":
            raise RuntimeError("no permission")
        return _fetch(endpoint, params)
    assert fl.etf_aum_by_index(AS_OF, fetch=broken) is None


def test_flow_adv_days_is_net_across_indices_over_adv20(lake):
    ev = pd.DataFrame([
        {"code": "600221", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
        {"code": "600221", "index_code": "000852", "index_name": "中证1000", "side": "drop", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
    ], columns=ie.EVENT_COLS)
    s = fl.flow_adv_days(ev, AS_OF, fetch=_fetch)
    # 沪深300:权重代理 = 100/(100+100+100) = 1/3 → +33.33 亿;中证1000 调出:−10 × 1/3 = −3.33 亿;净 30 亿 / ADV 1 亿
    assert s.tolist() == pytest.approx([30.0, 30.0], rel=1e-3)


def test_flow_adv_days_skips_an_index_whose_membership_snapshot_came_back_empty(lake):
    """`index_weight` 取到但 0 行(B 级合法空,不是取数失败)—— 空集绝不能被悄悄当成"这个指数
    本次的分母只有 touched 那几只":一个真实指数总有几十到几百个成分,空集只可能是"没能读出来"、
    从不是"真的没有成分"。把它当分母会让权重代理逼近 100%,把 flow_adv_days 撑大几十到几百倍——
    比留空更危险,因为它长得像一个算出来的数字。这个指数本次必须整个跳过。"""
    def fetch_no_members(endpoint, params):
        if endpoint == "index_weight":
            return pd.DataFrame(columns=["index_code", "con_code", "trade_date", "weight"])
        return _fetch(endpoint, params)
    ev = pd.DataFrame([
        {"code": "600221", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
    ], columns=ie.EVENT_COLS)
    s = fl.flow_adv_days(ev, AS_OF, fetch=fetch_no_members)
    assert s.isna().all()


def test_build_index_events_with_flow_fills_the_column(lake, monkeypatch):
    monkeypatch.setattr(cache, "_real_today", lambda: AS_OF)
    from autoresearch.data.sources.csindex import DETAIL_COLS, LIST_COLS

    def fl_list(e, p):
        return pd.DataFrame([["3007001", "关于调整沪深300等指数样本的公告", "20261127", "x"]], columns=LIST_COLS)

    def fl_detail(e, p):
        return pd.DataFrame([{"ann_id": "3007001", "publish_date": "20261127", "title": "t",
                              "content_text": "将于2026年12月11日收市后生效", "attachment_url": "u",
                              "index_code": "000300", "index_name": "沪深300", "side": "add",
                              "code": "600221", "name": "海航控股"}], columns=DETAIL_COLS)
    monkeypatch.setattr(fl, "_default_fetch", lambda: _fetch)
    tds = list(DAYS) + ["20261211", "20261214"]
    df = ie.build_index_events("2026-12-10", today=AS_OF, fetch_list=fl_list, fetch_detail=fl_detail,
                               trading_days=tds, with_flow=True)
    assert df.flow_adv_days.iloc[0] == pytest.approx(33.33, rel=1e-3)

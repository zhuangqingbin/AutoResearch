"""A股宏观/资金面结构化取数(Wave5 ③A):四端点此前只活在从未跑通的 macro full 里。

全部用假 `pro` 对象,零网络。重点守两件事:①单位换算(万元→亿 / 元→亿)②B 级降级留痕。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.data import macro_cn


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    """`_ts_call` 失败要退避重试 4 次(sleep 1.5+3+4.5≈9s)。本模块故意制造失败来测降级留痕,
    不掐掉就是每个失败端点真睡 9 秒(首版实测 6 个用例跑了 104s)。

    顺带记一笔生产侧观察:该退避**不区分可重试与不可重试**——「没有权限/参数错」这类
    重试必然再失败的错误也照睡 9 秒。真扫描里每个无权限端点都在白等。(未改,属 ④ 速度
    议题;改重试分类要单独评估,别顺手动。)
    """
    monkeypatch.setattr("autoresearch.data.tushare_source.time.sleep", lambda *_: None)


class FakePro:
    """只实现被调到的四个端点;`fail` 里的端点抛异常(测降级留痕)。"""

    def __init__(self, fail=()):
        self.fail = set(fail)

    def _guard(self, name):
        if name in self.fail:
            raise RuntimeError(f"{name} 端点炸了")

    def moneyflow_hsgt(self, **kw):
        self._guard("moneyflow_hsgt")
        return pd.DataFrame({"trade_date": ["20260720", "20260721", "20260722"],
                             # north_money 单位是万元:100000 万元 = 10 亿
                             "north_money": [100000.0, -50000.0, 250000.0],
                             "south_money": [10000.0, 20000.0, 30000.0]})

    def margin(self, exchange_id=None, **kw):
        self._guard("margin")
        if exchange_id == "SSE":
            return pd.DataFrame({"trade_date": ["20260721", "20260722"],
                                 "rzye": [1.0e12, 1.1e12], "rqye": [1e10, 1e10]})
        return pd.DataFrame({"trade_date": ["20260721", "20260722"],
                             "rzye": [0.8e12, 0.9e12], "rqye": [1e10, 1e10]})

    def moneyflow_ind_ths(self, **kw):
        self._guard("moneyflow_ind_ths")
        return pd.DataFrame({"industry": ["半导体", "银行", "地产"],
                             "net_amount": [12.5, -3.0, -8.0],
                             "lead_stock": ["寒武纪", "招商银行", "万科A"]})

    def index_dailybasic(self, ts_code=None, **kw):
        self._guard("index_dailybasic")
        if ts_code == "399006.SZ":
            raise RuntimeError("该指数无权限")
        return pd.DataFrame({"trade_date": ["20260720", "20260721", "20260722"],
                             "pe_ttm": [10.0, 12.0, 14.0], "pb": [1.1, 1.2, 1.3]})


def test_northbound_unit_conversion():
    """north_money 是万元 —— 250000 万元 = 25 亿。单位错了整块读数就是废的。"""
    got = macro_cn.northbound_data(FakePro(), "20260722")
    assert got["latest_yi"] == 25.0
    assert got["cum5_yi"] == pytest.approx(30.0)      # (10 - 5 + 25)
    assert len(got["rows"]) == 3


def test_margin_sums_both_exchanges_and_converts_to_yi():
    """rzye 单位是元:(1.1e12 + 0.9e12)/1e8 = 20000 亿。且必须两所齐全才计入。"""
    got = macro_cn.margin_data(FakePro(), "20260722")
    assert got["rzye_yi"] == pytest.approx(20000.0)
    assert got["d1_yi"] == pytest.approx(2000.0)      # 20000 - 18000
    assert got["as_of"] == "20260722"


def test_sector_flow_splits_top_bottom():
    got = macro_cn.sector_flow_data(FakePro(), "20260722", topn=2)
    assert [r["industry"] for r in got["top"]] == ["半导体", "银行"]
    assert got["n"] == 3 and got["n_pos"] == 1


def test_index_val_per_index_failure_does_not_cascade():
    """单指数无权限只缺该行,其余照出(逐指数独立 try 的存在理由)。"""
    got = macro_cn.index_val_data(FakePro(), "20260722")
    assert got["000300.SH"]["pe_ttm"] == 14.0
    assert got["000300.SH"]["pe_pct_1y"] == pytest.approx(66.6667, abs=1e-3)
    assert got["399006.SZ"]["pe_ttm"] is None
    assert "error" in got["399006.SZ"]


def test_fetch_records_degradation_per_block(monkeypatch):
    """B 级降级必留痕:炸掉的块置 None 且 _degraded 有名有姓。"""
    monkeypatch.setattr("autoresearch.data.tushare_source.resolve_momentum_dates",
                        lambda pro, d: ("20260722", "20260701"))
    got = macro_cn.fetch_macro_cn("2026-07-25", pro=FakePro(fail={"margin", "moneyflow_ind_ths"}))
    assert got["northbound"] is not None
    assert got["margin"] is None and got["sector_flow"] is None
    blocks = {d["block"] for d in got["_degraded"]}
    assert blocks == {"margin", "sector_flow"}
    assert all(d["why"] for d in got["_degraded"]), "降级必须带原因,不能只记个名字"


def test_write_produces_valid_json_without_nan(tmp_path, monkeypatch):
    """NaN 是非法 JSON 字面量 —— 漏一个整份文件就不可读(窄表毒化同族)。"""
    monkeypatch.setattr("autoresearch.data.tushare_source.resolve_momentum_dates",
                        lambda pro, d: ("20260722", "20260701"))
    p = macro_cn.write_macro_cn("2026-07-25", root=tmp_path, pro=FakePro())
    raw = p.read_text(encoding="utf-8")
    assert "NaN" not in raw
    data = json.loads(raw)               # 解析不了就直接炸在这
    assert data["as_of"] == "20260722"
    assert data["northbound"]["latest_yi"] == 25.0

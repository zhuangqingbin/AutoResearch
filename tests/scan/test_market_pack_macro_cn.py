"""market_pack 接入资金面/指数估值(Wave5 ③A)。

此前 pack 只有 24 个标量、且全部来自全 A 个股快照的横截面自聚合 —— **零真宏观变量**
(无北向/两融/指数估值分位),市场研判只能把同一批数字换个说法复述一遍。

两个 pack 入口(帧 / staging)必须**都**接上:本仓反复烧的 FN-1 家族就是"一个接了另一个没接"。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.market import market_pack, market_pack_from_frame

_MACRO_CN = {
    "date": "2026-07-25", "as_of": "20260724",
    "northbound": {"latest_yi": 25.0, "cum5_yi": 30.0, "south_latest_yi": 3.0, "rows": []},
    "margin": {"rzye_yi": 20000.0, "d1_yi": 12.0, "d5_yi": -80.0, "as_of": "20260724"},
    "sector_flow": {"top": [{"industry": "半导体", "net_yi": 12.5, "lead": "寒武纪"}],
                    "bottom": [{"industry": "地产", "net_yi": -8.0, "lead": "万科A"}],
                    "n": 90, "n_pos": 30},
    "index_val": {"000300.SH": {"name": "沪深300", "pe_ttm": 13.2, "pb": 1.4, "pe_pct_1y": 62.0}},
    "_degraded": [],
}


def _frame():
    return pd.DataFrame({"code": ["000001", "000002"], "industry": ["银行", "地产"],
                         "pct_60d": [5.0, -30.0], "close": [10.0, 8.0], "pe": [6.0, 9.0],
                         "pb": [0.7, 0.8], "ma60": [9.0, 9.0], "ma20": [9.5, 8.5]})


def _write(tmp_path, payload=None, date="2026-07-25"):
    d = tmp_path / date
    d.mkdir(parents=True, exist_ok=True)
    (d / "_macro_cn.json").write_text(
        json.dumps(payload if payload is not None else _MACRO_CN, ensure_ascii=False),
        encoding="utf-8")
    return d


def test_frame_entry_attaches_cross_money_and_index_val(tmp_path, monkeypatch):
    """帧入口用默认根 `context/scan` —— 建真目录 + chdir,别用 monkeypatch 把被测函数换掉
    (换掉了就等于没测它)。"""
    _write(tmp_path / "context" / "scan")
    monkeypatch.chdir(tmp_path)
    pack = market_pack_from_frame(_frame(), date="2026-07-25")
    assert pack["cross_money"]["north_cum5_yi"] == 30.0
    assert pack["cross_money"]["margin_rzye_yi"] == 20000.0
    assert pack["cross_money"]["sector_flow_top"] == [("半导体", 12.5)]
    assert pack["index_val"]["000300.SH"]["pe_pct_1y"] == 62.0


def test_staging_entry_attaches_too(tmp_path):
    """staging 入口(market_pack(scan_dir))必须同样接上——半接线是本仓 FN-1 常客。"""
    d = _write(tmp_path)
    (d / "L1_scored_full.csv").write_text(
        "code,industry,pct_60d,close,pe,pb,ma60,ma20\n"
        "000001,银行,5.0,10.0,6.0,0.7,9.0,9.5\n", encoding="utf-8")
    pack = market_pack(d)
    assert pack["cross_money"]["north_latest_yi"] == 25.0
    assert pack["index_val"]["000300.SH"]["name"] == "沪深300"


def test_missing_file_keeps_old_pack_shape(tmp_path):
    """缺文件 = 老 pack 形状逐字不变(不得凭空多出 None 键让下游误判"有这块但空")。"""
    d = tmp_path / "2026-07-25"
    (d).mkdir(parents=True)
    (d / "L1_scored_full.csv").write_text(
        "code,industry,pct_60d,close,pe,pb,ma60,ma20\n"
        "000001,银行,5.0,10.0,6.0,0.7,9.0,9.5\n", encoding="utf-8")
    pack = market_pack(d)
    assert "cross_money" not in pack
    assert "index_val" not in pack
    assert "macro_cn_degraded" not in pack


def test_degradation_is_surfaced_not_swallowed(tmp_path):
    """B 级降级必留痕:某块没取到,pack 里要看得见是哪块。"""
    payload = dict(_MACRO_CN, margin=None,
                   _degraded=[{"block": "margin", "why": "RuntimeError: 两所不齐"}])
    d = _write(tmp_path, payload)
    pack = market_pack(d)
    assert pack["macro_cn_degraded"][0]["block"] == "margin"
    assert pack["cross_money"]["margin_rzye_yi"] is None      # 该字段空,但块还在


def test_corrupt_json_does_not_break_pack(tmp_path):
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    (d / "_macro_cn.json").write_text("{半截文件", encoding="utf-8")
    pack = market_pack(d)                     # 不抛
    assert "cross_money" not in pack

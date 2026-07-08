"""Phase 3 —— sector_pack / select_briefing_sectors / sector_terrain_md。NO network。

锚:全部聚合 scan staging 既有产物,零新端点;缺文件/缺列逐字段降级(None/[]/'')不抛。
design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.3/§5.5。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.sector.pack import (
    sector_pack,
    sector_terrain_md,
    select_briefing_sectors,
)

DATE = "2026-07-03"


def _mk_scan(tmp_path: Path, date: str = DATE) -> Path:
    d = tmp_path / date
    d.mkdir(parents=True, exist_ok=True)
    l1 = pd.DataFrame([
        # 半导体×4:一只健康(0<pct60<40∧主力+∧cmf+),一只 pe<0(应被剔出估值中位)
        {"code": "600001", "name": "甲", "industry": "半导体", "close": 10, "pct_60d": 20.0,
         "pe": 50.0, "pb": 5.0, "main_net_ratio": 0.05, "cmf_20": 0.30, "winner_rate": 40,
         "mktcap_yi": 900, "np_yoy": 30, "roe": 15, "main_inflow_yi": 2.0},
        {"code": "600002", "name": "乙", "industry": "半导体", "close": 20, "pct_60d": -30.0,
         "pe": -10.0, "pb": 3.0, "main_net_ratio": -0.05, "cmf_20": -0.10, "winner_rate": 90,
         "mktcap_yi": 500, "np_yoy": -20, "roe": 5, "main_inflow_yi": -1.0},
        {"code": "600003", "name": "丙", "industry": "半导体", "close": 30, "pct_60d": -25.0,
         "pe": 100.0, "pb": 8.0, "main_net_ratio": -0.02, "cmf_20": -0.20, "winner_rate": 85,
         "mktcap_yi": 300, "np_yoy": 10, "roe": 8, "main_inflow_yi": -0.5},
        {"code": "600004", "name": "丁", "industry": "半导体", "close": 40, "pct_60d": -35.0,
         "pe": 60.0, "pb": 4.0, "main_net_ratio": -0.01, "cmf_20": -0.30, "winner_rate": 70,
         "mktcap_yi": 100, "np_yoy": 0, "roe": 3, "main_inflow_yi": -0.2},
        {"code": "600011", "name": "戊", "industry": "白酒", "close": 100, "pct_60d": 5.0,
         "pe": 25.0, "pb": 6.0, "main_net_ratio": 0.01, "cmf_20": 0.10, "winner_rate": 60,
         "mktcap_yi": 2000, "np_yoy": 8, "roe": 25, "main_inflow_yi": 0.5},
        {"code": "600012", "name": "己", "industry": "白酒", "close": 200, "pct_60d": 3.0,
         "pe": 30.0, "pb": 7.0, "main_net_ratio": -0.01, "cmf_20": 0.05, "winner_rate": 55,
         "mktcap_yi": 1500, "np_yoy": 6, "roe": 20, "main_inflow_yi": -0.3},
        {"code": "600021", "name": "庚", "industry": "煤炭", "close": 8, "pct_60d": -10.0,
         "pe": 6.0, "pb": 1.0, "main_net_ratio": 0.00, "cmf_20": -0.05, "winner_rate": 30,
         "mktcap_yi": 800, "np_yoy": -30, "roe": 12, "main_inflow_yi": 0.0},
        {"code": "600022", "name": "辛", "industry": "煤炭", "close": 9, "pct_60d": -12.0,
         "pe": 7.0, "pb": 1.1, "main_net_ratio": 0.02, "cmf_20": 0.01, "winner_rate": 35,
         "mktcap_yi": 600, "np_yoy": -25, "roe": 10, "main_inflow_yi": 0.4},
    ])
    l1.to_csv(d / "L1_scored_full.csv", index=False)
    l1.iloc[[0, 2, 3, 4]].to_csv(d / "L2_gbdt_top200.csv", index=False)   # 半导体×3 + 白酒×1
    pd.DataFrame([{"industry": "半导体", "n_recall": 4, "median_pct_60d": 5.0},
                  {"industry": "白酒", "n_recall": 2, "median_pct_60d": 4.0},
                  {"industry": "煤炭", "n_recall": 2, "median_pct_60d": -11.0}]
                 ).to_csv(d / "sectors.csv", index=False)
    (d / "meta.json").write_text(json.dumps({"regime": "range"}), encoding="utf-8")
    pd.DataFrame([{"code": "600001", "event_date": "2026-07-10", "kind": "disclosure"},
                  {"code": "600002", "event_date": "2026-07-08", "kind": "unlock"}]
                 ).to_csv(d / "calendar.csv", index=False)
    return d


def test_sector_pack_fields(tmp_path):
    d = _mk_scan(tmp_path)
    p = sector_pack("半导体", d)
    assert p["n_market"] == 4 and p["n_l2"] == 3
    assert p["median_pe"] == 60.0                      # pe<0 剔出 → 50/100/60 中位
    assert p["healthy_n"] == 1                         # 只有"甲"满足健康谓词
    assert p["leaders"][0]["code"] == "600001"         # 市值 top1
    assert p["main_pos_frac"] == 0.25                  # 4 只中 1 只主力净流入>0
    assert p["calendar"]["n_events"] == 2
    assert p["calendar"]["next_date"] == "2026-07-08"
    assert p["calendar"]["by_kind"] == {"disclosure": 1, "unlock": 1}


def test_sector_pack_degrades(tmp_path):
    d = _mk_scan(tmp_path)
    empty = sector_pack("不存在的行业", d)
    assert empty["n_market"] == 0 and empty["leaders"] == []
    bare = tmp_path / "bare"
    bare.mkdir()
    p = sector_pack("半导体", bare)                     # 无任何 staging → 默认包不抛
    assert p["n_market"] == 0 and p["calendar"] is None


def test_select_briefing_sectors(tmp_path):
    d = _mk_scan(tmp_path)
    wl = tmp_path / "watchlist.csv"
    pd.DataFrame([{"code": "600021", "name": "庚"}]).to_csv(wl, index=False)
    inds, prov = select_briefing_sectors(d, k=6, wl_path=wl)
    assert inds[0] == "半导体" and prov["半导体"] == "红榜top3"      # 红榜序先入
    assert "白酒" in inds and "煤炭" in inds
    assert prov["煤炭"] in ("红榜top3", "观察单")                    # 三行业全上红榜(仅3个)
    inds2, _ = select_briefing_sectors(d, k=1, wl_path=wl)
    assert inds2 == ["半导体"]                                       # cap 生效
    inds3, prov3 = select_briefing_sectors(tmp_path / "nope", wl_path=tmp_path / "no.csv")
    assert inds3 == [] and prov3 == {}                               # 全缺 → 空(降级)


def test_sector_terrain_md(tmp_path):
    d = _mk_scan(tmp_path)
    s = sector_terrain_md(d)
    assert s.startswith("## 全行业地形")
    assert "非选股指令" in s and "不因信息薄降权" in s               # 对称性铁律入文
    for ind in ("半导体", "白酒", "煤炭"):
        assert f"| {ind} |" in s                                     # 全行业对称覆盖
    assert "| 半导体 | 4 | 3 |" in s                                 # n全市场/入L2
    assert sector_terrain_md(tmp_path / "nope") == ""                # 缺 staging → ''(parity)


def test_sector_terrain_top200_only_filters_uncovered_industries(tmp_path):
    # L2 只覆盖 半导体/白酒(煤炭全无 L2 命中,见 _mk_scan) → top200_only=True 地形裁掉煤炭;
    # 默认 False 逐字 parity(三行业全出,同 test_sector_terrain_md)
    d = _mk_scan(tmp_path)
    on = sector_terrain_md(d, top200_only=True)
    off = sector_terrain_md(d)
    assert "| 半导体 |" in on and "| 白酒 |" in on and "| 煤炭 |" not in on
    assert "| 半导体 |" in off and "| 白酒 |" in off and "| 煤炭 |" in off

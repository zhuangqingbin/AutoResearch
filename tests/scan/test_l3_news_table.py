"""L3 表并入 news digest + recall provenance(缺则降级)。NO network。"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents.l3_select import l3_table_md, load_l3_input


def _make_l2(tmp_path, with_news=True, with_prov=True, with_web=False):
    d = tmp_path / "context/scan" / "2026-06-20"
    (d / "L3_news").mkdir(parents=True)
    rows = []
    for i in range(3):
        r = {"code": f"{i:06d}", "name": f"s{i}", "industry": "电子", "composite": 90 - i,
             "gbdt_score": 0.5, "score_momentum": 50, "pct_60d": 10.0, "main_net_ratio": 0.01,
             "winner_rate": 30.0, "np_yoy": 50.0}
        if with_prov:
            r["n_channels"] = 3 - i
            r["recall_channels"] = "composite|momentum"
        rows.append(r)
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    if with_news:
        (d / "L3_news" / "000000.json").write_text(json.dumps(
            [{"ann_date": "20260620", "title": "关于回购公司股份的公告"}]), encoding="utf-8")
        for c in ("000001", "000002"):
            (d / "L3_news" / f"{c}.json").write_text("[]", encoding="utf-8")
    if with_web:
        (d / "L3_webnews").mkdir(parents=True, exist_ok=True)
        (d / "L3_webnews" / "000000.json").write_text(json.dumps(
            [{"ann_date": "2026-06-20 09:00", "title": "公司中标 10 亿大单"}]), encoding="utf-8")
        for c in ("000001", "000002"):
            (d / "L3_webnews" / f"{c}.json").write_text("[]", encoding="utf-8")
    return tmp_path / "context/scan"


def test_load_l3_input_merges_news_digest(tmp_path):
    root = _make_l2(tmp_path)
    df = load_l3_input("2026-06-20", root=root)
    assert {"news_n", "news_tags", "news_head"} <= set(df.columns)
    row0 = df[df["code"] == "000000"].iloc[0]
    assert int(row0["news_n"]) == 1 and "利多" in str(row0["news_tags"])


def test_load_l3_input_degrades_without_news(tmp_path):
    root = _make_l2(tmp_path, with_news=False)
    df = load_l3_input("2026-06-20", root=root)
    assert {"news_n", "news_tags", "news_head"} <= set(df.columns)   # 列在,缺省 0/""/—
    assert int(df.iloc[0]["news_n"]) == 0


def test_l3_table_md_shows_news_and_provenance(tmp_path):
    root = _make_l2(tmp_path)
    md = l3_table_md("2026-06-20", root=root)
    assert "news_tags" in md and "n_channels" in md


def test_load_l3_input_merges_media_digest(tmp_path):
    root = _make_l2(tmp_path, with_web=True)
    df = load_l3_input("2026-06-20", root=root)
    assert {"med_n", "med_tags", "med_head"} <= set(df.columns)     # 媒体情感列在
    row0 = df[df["code"] == "000000"].iloc[0]
    assert int(row0["med_n"]) == 1 and "利多" in str(row0["med_tags"])


def test_load_l3_input_media_degrades_without_webnews(tmp_path):
    root = _make_l2(tmp_path, with_web=False)                       # 无 L3_webnews 目录
    df = load_l3_input("2026-06-20", root=root)
    assert {"med_n", "med_tags", "med_head"} <= set(df.columns)     # 列在,缺省 0/""/—
    assert int(df.iloc[0]["med_n"]) == 0

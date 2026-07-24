"""L3 表并入 news digest + recall provenance(缺则降级)。NO network。"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents.l3_select import l3_table_md, load_l3_input


def _make_l2(tmp_path, with_news=True, with_prov=True):
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
    assert "news_sent" in md and "n_channels" in md   # 07-06 瘦身:表显示 news_sent(净分);news_tags/n 已从表删(df 仍有)


# ─────────────────── anns_d 退役:news_sent/news_head 整列全空须可见标注 ───────────────────
# 断链可见性(Wave4 Task1):anns_d 已无权限退役,harvest_l3_news 现在会一次性告警(见
# test_l3_news.py),但那只在 harvest 阶段的 stderr 可见——L3 holistic agent 读的是这张表,
# 表本身也必须能让人一眼看出「这两列今天不可用」,不能留一整列 "—"/0.0 让人误判"今天无消息"。


def test_l3_table_md_flags_all_empty_news_column(tmp_path):
    """anns_d 退役当日:所有票 news_n=0(无 L3_news 落盘) → 表头须有可见标注。"""
    root = _make_l2(tmp_path, with_news=False)
    md = l3_table_md("2026-06-20", root=root)
    assert "anns_d" in md and "退役" in md and "不可用" in md


def test_l3_table_md_no_annotation_when_news_present(tmp_path):
    """至少一票有真实公告(非整列全空)→ 不误报"不可用",维持逐字 parity。"""
    root = _make_l2(tmp_path, with_news=True)
    md = l3_table_md("2026-06-20", root=root)
    assert "anns_d 已退役" not in md


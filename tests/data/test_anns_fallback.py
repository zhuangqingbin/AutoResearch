import pandas as pd

from autoresearch.data.sources import anns_fallback as af


def test_normalizes_rows_to_l3news_shape(monkeypatch):
    df = pd.DataFrame([
        {"代码": "000651", "名称": "格力电器", "公告标题": "关于回购股份的进展公告",
         "公告日期": "2026-07-29"},
        {"代码": "000651", "名称": "格力电器", "公告标题": "2026 年半年度报告披露提示",
         "公告日期": "2026-07-28"},
    ])
    monkeypatch.setattr(af, "_raw_notices", lambda code6, date: df)
    rows = af.fetch_anns("000651", "2026-07-29")
    assert rows == [
        {"ann_date": "20260729", "title": "关于回购股份的进展公告", "source": "em"},
        {"ann_date": "20260728", "title": "2026 年半年度报告披露提示", "source": "em"},
    ]


def test_drops_lookahead_rows(monkeypatch):
    """as-of 铁律:晚于分析日的公告一律丢弃(前视污染)。"""
    df = pd.DataFrame([
        {"公告标题": "未来公告", "公告日期": "2026-07-30"},
        {"公告标题": "当日公告", "公告日期": "2026-07-29"},
    ])
    monkeypatch.setattr(af, "_raw_notices", lambda code6, date: df)
    rows = af.fetch_anns("000651", "2026-07-29")
    assert [r["title"] for r in rows] == ["当日公告"]


def test_degrades_to_empty_on_source_error(monkeypatch):
    """B 级契约:取数炸了返回空,不抛异常阻断漏斗。"""
    def boom(code6, date):
        raise RuntimeError("network down")
    monkeypatch.setattr(af, "_raw_notices", boom)
    assert af.fetch_anns("000651", "2026-07-29") == []


def test_empty_frame_returns_empty(monkeypatch):
    monkeypatch.setattr(af, "_raw_notices", lambda code6, date: pd.DataFrame())
    assert af.fetch_anns("000651", "2026-07-29") == []

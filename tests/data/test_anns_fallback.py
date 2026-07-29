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
        {"ann_date": "20260729", "title": "关于回购股份的进展公告", "source": af.SOURCE_TAG},
        {"ann_date": "20260728", "title": "2026 年半年度报告披露提示", "source": af.SOURCE_TAG},
    ]


def test_normalizes_rows_using_real_cninfo_date_column(monkeypatch):
    """回归钉子(复核 Important 1):cninfo 真实列名是 `公告时间`(Step 5 实测),不是
    brief 原猜、已被证伪的 `公告日期`。只测 `公告日期` 夹具时,若日后 `_pick` 的候选键
    被误删/重排,4 个旧测试可能仍全绿,而 `fetch_anns` 对生产真实数据已静默退化为永远
    返回 `[]`——本模块存在的理由("无权限"与"当日故障"同形)会在它自己身上重演。
    这条独立钉住真实列名路径,不依赖 `公告日期` 这个候选键。
    """
    df = pd.DataFrame([
        {"代码": "000651", "简称": "格力电器",
         "公告标题": "关于持股5%以上股东股份补充质押的公告",
         "公告时间": "2026-07-16",
         "公告链接": "http://www.cninfo.com.cn/new/disclosure/detail?announcementId=1"},
        {"代码": "000651", "简称": "格力电器", "公告标题": "2025年年度股东会决议公告",
         "公告时间": "2026-07-01",
         "公告链接": "http://www.cninfo.com.cn/new/disclosure/detail?announcementId=2"},
    ])
    monkeypatch.setattr(af, "_raw_notices", lambda code6, date: df)
    rows = af.fetch_anns("000651", "2026-07-29")
    assert rows == [
        {"ann_date": "20260716", "title": "关于持股5%以上股东股份补充质押的公告",
         "source": af.SOURCE_TAG},
        {"ann_date": "20260701", "title": "2025年年度股东会决议公告",
         "source": af.SOURCE_TAG},
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


def test_raw_notices_none_returns_empty(monkeypatch):
    """守卫 `df is None` 分支的钉子(复核 Important 4:变异测试证实此前无测试触达这一半;
    `pd.DataFrame().to_dict("records")` 本就是 `[]`,`not len(df)` 半句对空帧场景是
    死码等价,只有 `_raw_notices` 真返回 `None`(而非空帧)才会触达 `df is None` 这半句
    ——`None.to_dict(...)` 会抛 AttributeError,如果没有这半句守卫,B 级契约就会被
    自己的正常路径打破)。"""
    monkeypatch.setattr(af, "_raw_notices", lambda code6, date: None)
    assert af.fetch_anns("000651", "2026-07-29") == []

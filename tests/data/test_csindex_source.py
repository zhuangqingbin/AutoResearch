"""中证指数公司公告 adapter(design 2026-09-25 §2.1 / F7):列表帧、详情帧、xlsx 名单解析、生效日文本解析、路由。
全部离线:接口返回体用 2026-09-25 实测的形态造 fixture;附件按实测版式现造 xlsx。"""
from __future__ import annotations

import io

import pandas as pd
import pytest

from autoresearch.data.sources import csindex

LIST_BODY = {"code": "200", "msg": "Success", "data": {
    "companyAnnouncements": [], "indexlaunchesAnnouncements": [],
    "indexrebalancingAnnouncements": [
        {"id": 3006244, "title": "关于调整三板成指样本股的公告", "theme": "index_rebalance",
         "publishDate": "2026-09-18", "noticeType": None, "fileUrl": None, "fileName": None},
        {"id": 3006227, "title": "关于沪深300等指数样本临时调整的公告", "theme": "index_rebalance",
         "publishDate": "2026-09-09", "noticeType": None, "fileUrl": None, "fileName": None},
    ]}}

DETAIL_DATA = {
    "imgList": [], "id": 3006227, "noticeStatus": None, "publishDate": "2026-09-09",
    "title": "关于沪深300等指数样本临时调整的公告", "contentSource": None,
    "content": ('<div class="weditor"><p><font face="微软雅黑" size="3">　　针对中金公司(601995)换股吸收合并'
                '东兴证券(601198)、信达证券(601059)事项,根据指数编制规则,中证指数有限公司决定自东兴证券、'
                '信达证券退市日起,对沪深300等指数进行样本调整,调整名单见附件。</font></p><p><br/></p>'
                '<p><a class="file iconfont 3002874 xlsx" href="https://oss-ch.csindex.com.cn/notice/x.xlsx">'
                '<span class="ml5">附件:指数样本调整名单</span></a></p><p style="text-align: right;">'
                '中证指数有限公司<br/>2026年9月9日</p></div>'),
    "enclosureList": [{"id": 3002874, "fileName": "附件:指数样本调整名单.xlsx",
                       "fileUrl": "https://oss-ch.csindex.com.cn/notice/x.xlsx"}],
}


def _xlsx(rows: list[list]) -> bytes:
    """2026-09-09 实测版式:行 0 分组标题(只在组首列),行 1 证券代码/简称,行 2 起数据。"""
    header0 = ["指数代码", "指数简称", "调出", None, None, None, "调入", None, None, None]
    header1 = [None, None, "证券代码", "证券简称", "证券代码", "证券简称", "证券代码", "证券简称", "证券代码", "证券简称"]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([header0, header1, *rows]).to_excel(xw, sheet_name="指数样本调整名单",
                                                          header=False, index=False)
    return buf.getvalue()


_ROWS = [
    ["000009", "上证380", "601198", "东兴证券", "688303", "大全能源", "600884", "杉杉股份", "603092", "德力佳"],
    ["000300", "沪深300", "601198", "东兴证券", "601059", "信达证券", "688303", "大全能源", "-", "-"],
    [None, None, "-", "-", "-", "-", "601995", "中金公司", "-", "-"],          # 续行:承接上一指数
    ["000905", "中证500", "-", "-", "-", "-", "600884", "杉杉股份", "-", "-"],
]


def test_list_frame_normalises_dates_and_ids():
    df = csindex.list_frame(LIST_BODY["data"]["indexrebalancingAnnouncements"])
    assert list(df.columns) == csindex.LIST_COLS
    assert df.iloc[1].to_dict() == {"ann_id": "3006227", "title": "关于沪深300等指数样本临时调整的公告",
                                    "publish_date": "20260909", "theme": "index_rebalance"}


def test_parse_adjustment_xlsx_long_table():
    out = csindex.parse_adjustment_xlsx(_xlsx(_ROWS))
    assert list(out.columns) == ["index_code", "index_name", "side", "code", "name"]
    hs300 = out[out.index_code == "000300"]
    assert set(hs300[hs300.side == "drop"].code) == {"601198", "601059"}
    assert set(hs300[hs300.side == "add"].code) == {"688303", "601995"}       # 续行归到沪深300
    assert (out[out.index_code == "000009"].side == "drop").sum() == 2
    assert (out[out.index_code == "000905"].side == "add").sum() == 1
    assert "-" not in set(out.code) and out.code.str.len().eq(6).all()


def test_parse_adjustment_xlsx_rejects_unrecognised_layout():
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([["a", "b"]]).to_excel(xw, header=False, index=False)
    assert csindex.parse_adjustment_xlsx(buf.getvalue()).empty


def test_detail_frame_joins_header_fields_onto_every_row():
    df = csindex.detail_frame(DETAIL_DATA, _xlsx(_ROWS), "https://oss-ch.csindex.com.cn/notice/x.xlsx")
    assert list(df.columns) == csindex.DETAIL_COLS
    assert (df.ann_id == "3006227").all() and (df.publish_date == "20260909").all()
    assert "自东兴证券、信达证券退市日起" in df.content_text.iloc[0]
    assert "<" not in df.content_text.iloc[0]                                  # HTML 已剥
    assert len(df) == 4 + 4 + 1                                                # 上证380 4 + 沪深300 4 + 中证500 1
    assert (df.attachment_url == "https://oss-ch.csindex.com.cn/notice/x.xlsx").all()


def test_detail_frame_without_attachment_is_one_text_row():
    data = {**DETAIL_DATA, "enclosureList": []}
    df = csindex.detail_frame(data, None, None)
    assert len(df) == 1 and df.index_code.isna().all() and df.content_text.iloc[0]


def test_detail_frame_with_empty_attachment_raises():
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([["a", "b"]]).to_excel(xw, header=False, index=False)
    with pytest.raises(RuntimeError, match="附件解析为空"):
        csindex.detail_frame(DETAIL_DATA, buf.getvalue(), "u")


@pytest.mark.parametrize("text,expect", [
    ("上述调整将于2026年6月12日收市后生效。", ("20260612", "after_close")),
    ("调整方案自 2026 年 6 月 15 日起正式实施。", ("20260615", "from_date")),
    ("本次调整于2026年12月14日正式生效", ("20261214", "from_date")),
    ("中证指数有限公司决定自东兴证券、信达证券退市日起,对沪深300等指数进行样本调整", (None, None)),
    ("中证指数有限公司 2026年9月9日", (None, None)),                       # 落款日期不是生效日
    # fix round 1(review Important):引文里规则自己的生效日 vs 本次调整的操作生效日,
    # 两者同形、位置先后不可靠 —— 多候选且不满足「唯一 after_close」→ 诚实退化,不猜。
    ("根据《指数编制细则》(2026年3月1日起生效),中证指数有限公司决定自2026年6月15日起正式实施本次样本调整。",
     (None, None)),
    # 镜像用例:两个候选、其中恰一个 after_close → kind 优先于位置,取「收市后」那个,
    # 不是取最后出现的(否则会错取「下次调整」的日期)。
    ("本次调整于2026年6月12日收市后生效,下次调整预计于2026年12月11日起实施。",
     ("20260612", "after_close")),
    # 两个 after_close 候选同时出现 → 唯一性也救不了,仍是 (None, None)。
    ("本次调整分两步实施:第一步2026年6月12日收市后生效,第二步2026年12月11日收市后生效。",
     (None, None)),
    # 单一 from_date、全文没有 after_close → 唯一性分支正常放行(与既有 case 2/3 同族,
    # 但用独立文本单独钉死,不依赖那两条恰好凑对新逻辑)。
    ("本次样本调整定于2026年10月9日起生效。", ("20261009", "from_date")),
])
def test_parse_effective_date(text, expect):
    assert csindex.parse_effective_date(text) == expect


class _Resp:
    def __init__(self, *, json=None, content=b""):
        self._json, self.content = json, content

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


def test_fetch_rebalance_detail_downloads_xlsx_attachment(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if url == csindex.DETAIL_URL:
            return _Resp(json={"code": "200", "data": DETAIL_DATA})
        return _Resp(content=_xlsx(_ROWS))

    monkeypatch.setattr(csindex.requests, "get", fake_get)
    df = csindex.fetch_rebalance_detail("3006227")
    assert calls == [csindex.DETAIL_URL, "https://oss-ch.csindex.com.cn/notice/x.xlsx"]
    assert len(df) == 9


def test_fetch_rebalance_list_rejects_malformed_body(monkeypatch):
    monkeypatch.setattr(csindex.requests, "get", lambda url, **kw: _Resp(json={"code": "500", "data": None}))
    with pytest.raises(RuntimeError, match="形态异常"):
        csindex.fetch_rebalance_list()


def test_sources_fetch_routes_csindex(monkeypatch):
    from autoresearch.data import sources
    monkeypatch.setattr(csindex, "fetch_rebalance_list", lambda: pd.DataFrame({"ann_id": ["1"]}))
    monkeypatch.setattr(csindex, "fetch_rebalance_detail", lambda ann_id: pd.DataFrame({"ann_id": [str(ann_id)]}))
    assert sources.fetch("csindex_rebalance_list", {}).ann_id.tolist() == ["1"]
    assert sources.fetch("csindex_rebalance_detail", {"ann_id": "3006227"}).ann_id.tolist() == ["3006227"]
    with pytest.raises(ValueError):
        sources._fetch_csindex("csindex_nope", {})

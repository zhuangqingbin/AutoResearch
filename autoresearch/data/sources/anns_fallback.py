#!/usr/bin/env python3
"""公告标题流 **B 级兜底源**(Wave9 A-1)。

主源 tushare `anns_d` 自 2026-07-18 起无接口权限,`L3_news/` 整目录空稿
(2026-07-29 实测 `anns_empty_rate=1.0`),而 self_review 把它当 expected 放行 ——
"无权限"与"当日故障"在产物上长得一模一样,公告面**静默单腿**。

本模块提供 akshare 侧兜底:拉个股公告标题,归一成 `L3_news/<code>.json` 同构行。
**B 级契约**:取不到就返回空 + 由调用方记账降级,绝不抛异常阻断漏斗。

Step 5 真接口冒烟裁决记录(2026-07-29):
  - `ak.stock_notice_report(symbol="全部", date=...)`(东财全市场单日公告)实测
    `ok=false`(耗时 ~70s 翻 12 页仍拉不到可用行,`columns=[]`)—— **弃用**。该接口本身
    也不吃 `code6`(只能按日期拉全市场,得自己按代码过滤),契约上就比下面这个差。
  - 改用 `ak.stock_zh_a_disclosure_report_cninfo(symbol=code6, market="沪深京",
    start_date, end_date)`(巨潮资讯个股公告查询)实测 **可用**。真实列名 =
    `['代码', '简称', '公告标题', '公告时间', '公告链接']`—— 注意日期列是
    **`公告时间`**,不是原猜的 `公告日期`(`_pick` 候选键已补上,`公告日期` 仍保留在
    候选表首位,兼容本文件单测夹具用的列名)。`market="沪深京"` 覆盖沪/深/京三所全部
    A 股(含北交所 92xxxx),不必按 code6 前缀分流。000651 用生产同款 `_LOOKBACK_DAYS`
    (90 天)窗口实测拉到 **22** 行(先前记录的「111 行」是一年窗口探查值,只用于确认
    接口本身可用,和生产窗口不是同一量纲——复核 Minor 2 指出两个数字对不上,此处已
    按生产窗口重新实测替换为真实数)。

复核轮 1(2026-07-29):`SOURCE_TAG` 曾误取值 `"em"`——本仓库既有惯例里 `"em"` 专指
东财(见 `scan/universe.py`/`scan/frame.py` 的 `--source` choices),而本模块实际连的
是巨潮 cninfo,标签与实际供应商不符会污染未来"按供应商做数据质量归因"的场景,
已改为 `"cninfo"`,与真实接口名一致。
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta

SOURCE_TAG = "cninfo"

_LOOKBACK_DAYS = 90   # 兜底源窗口:约一季度,够覆盖近期披露且不做无界历史查询


def _raw_notices(code6: str, date: str):
    """原始取数(测试 monkeypatch 此函数;真身走 akshare 巨潮资讯 cninfo 接口)。"""
    import akshare as ak
    end = date.replace("-", "")
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y%m%d")
    return ak.stock_zh_a_disclosure_report_cninfo(
        symbol=str(code6).zfill(6), market="沪深京", start_date=start, end_date=end)


def _pick(row: dict, *keys: str) -> str:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def fetch_anns(code6: str, date: str, *, limit: int = 20) -> list[dict]:
    """→ `[{"ann_date": "YYYYMMDD", "title": str, "source": SOURCE_TAG}, ...]`,失败/无数据 → `[]`。

    as-of 铁律:`ann_date > date` 的行一律丢弃(前视污染)。
    """
    cut = date.replace("-", "")
    try:
        df = _raw_notices(code6, date)
    except Exception:  # noqa: BLE001 — B 级源:取数失败降级为空,由调用方记账
        return []
    if df is None or not len(df):
        return []

    rows: list[dict] = []
    for rec in df.to_dict("records"):
        title = _pick(rec, "公告标题", "title", "名称")
        raw_date = _pick(rec, "公告日期", "公告时间", "ann_date", "日期")
        if not title or not raw_date:
            continue
        ann = raw_date.replace("-", "")[:8]
        if not ann.isdigit() or ann > cut:      # as-of ≤ 分析日
            continue
        rows.append({"ann_date": ann, "title": title, "source": SOURCE_TAG})
        if len(rows) >= limit:
            break
    return rows


def probe() -> dict:
    """冒烟自检:接口在不在、字段名对不对。CLI `python -m ...anns_fallback` 调。

    走 `_raw_notices` 同一条真实路径(000651 作探针票),而非另起一套断言——
    确保这条自检真的反映 `fetch_anns` 当下会打到的接口。
    """
    out = {"ok": False, "source": SOURCE_TAG, "columns": [], "error": None}
    with contextlib.suppress(Exception):
        today = datetime.now().strftime("%Y-%m-%d")
        df = _raw_notices("000651", today)
        out["columns"] = list(df.columns)[:12]
        out["ok"] = bool(len(df))
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(probe(), ensure_ascii=False))

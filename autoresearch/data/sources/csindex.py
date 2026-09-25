#!/usr/bin/env python3
"""中证指数公司公告 —— 指数样本调整(调入/调出)名单的**唯一确定性前瞻源**。

design: docs/specs/2026-09-25-index-inclusion-signal-design.md §2.1 / F7。

两个接口(2026-09-25 实测):
  GET queryAnnouncementByType?type=1&pageNum=1&pageSize=50
      → data.indexrebalancingAnnouncements[] = {id, title, theme, publishDate, noticeType, fileUrl, fileName}
        **只给最新 5 条、不分页** → 历史从上线日起自建湖累积(cache 快照键 all@today)。
  GET queryAnnouncementById?id=<id>
      → data = {id, title, publishDate, content(HTML), enclosureList[{id, fileName, fileUrl}], …}
        附件 xlsx(oss-ch.csindex.com.cn/notice/*.xlsx):行 0 =「指数代码 | 指数简称 | 调出 … | 调入 …」分组标题
        (组名只在组首列),行 1 = 每组重复「证券代码 | 证券简称」,行 2 起数据;一指数多于两个调入/调出时
        可能续行(指数代码为空 = 承接上一行);「-」= 空位。

**形态不对就抛,不返回空帧假装成功**:B 级契约 + `persist_violations=False` 在上层负责「断采只损失当日、
不阻断」,但那要求断采是显式的——静默空帧会被当成「这次没调样」。
"""
from __future__ import annotations

import io
import re

import pandas as pd
import requests

LIST_URL = "https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementByType"
DETAIL_URL = "https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementById"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.csindex.com.cn/"}
_TIMEOUT = 20          # 夜跑必须有超时:一次挂起的连接会拖死整个 prelude

LIST_COLS = ["ann_id", "title", "publish_date", "theme"]
DETAIL_COLS = ["ann_id", "publish_date", "title", "content_text", "attachment_url",
               "index_code", "index_name", "side", "code", "name"]
TABLE_COLS = ["index_code", "index_name", "side", "code", "name"]
_SIDE_OF = {"调出": "drop", "调入": "add"}


def _compact_date(s) -> str:
    return str(s or "").replace("-", "")[:8]


def list_frame(items: list[dict]) -> pd.DataFrame:
    rows = [{"ann_id": str(it.get("id")), "title": str(it.get("title") or ""),
             "publish_date": _compact_date(it.get("publishDate")), "theme": it.get("theme")}
            for it in items]
    return pd.DataFrame(rows, columns=LIST_COLS)


def fetch_rebalance_list(timeout: int = _TIMEOUT) -> pd.DataFrame:
    r = requests.get(LIST_URL, params={"type": 1, "pageNum": 1, "pageSize": 50},
                     headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    body = r.json() or {}
    items = (body.get("data") or {}).get("indexrebalancingAnnouncements")
    if not isinstance(items, list):
        raise RuntimeError(f"中证公告列表形态异常(indexrebalancingAnnouncements 不是 list):{str(body)[:160]}")
    return list_frame(items)


def html_to_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[　\xa0]+", " ", text).strip()


def _clean(v) -> str | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def parse_adjustment_xlsx(raw: bytes) -> pd.DataFrame:
    """附件 → 长表 `TABLE_COLS`。认不出版式(行数 <3 或列数 <4 或无「调出/调入」组)→ 空帧(由调用方判定)。"""
    sheet = pd.read_excel(io.BytesIO(raw), header=None, dtype=str)
    if sheet.shape[0] < 3 or sheet.shape[1] < 4:
        return pd.DataFrame(columns=TABLE_COLS)
    groups = [str(g).strip() if _clean(g) else None for g in sheet.iloc[0].ffill().tolist()]
    kinds = [str(k) if _clean(k) else "" for k in sheet.iloc[1].tolist()]
    rows: list[dict] = []
    last_index: tuple[str | None, str | None] = (None, None)
    for _, r in sheet.iloc[2:].iterrows():
        idx_code, idx_name = _clean(r.iloc[0]), _clean(r.iloc[1])
        if idx_code:
            last_index = (idx_code.zfill(6), idx_name)
        elif last_index[0] is None:
            continue
        j = 2
        while j < sheet.shape[1]:
            side = _SIDE_OF.get(groups[j] or "")
            if side and "代码" in kinds[j]:
                code = _clean(r.iloc[j])
                name = _clean(r.iloc[j + 1]) if j + 1 < sheet.shape[1] else None
                if code and code != "-":
                    rows.append({"index_code": last_index[0], "index_name": last_index[1],
                                 "side": side, "code": code.zfill(6), "name": name})
                j += 2
            else:
                j += 1
    return pd.DataFrame(rows, columns=TABLE_COLS)


def detail_frame(data: dict, xlsx_bytes: bytes | None, attachment_url: str | None) -> pd.DataFrame:
    base = {"ann_id": str(data.get("id")), "publish_date": _compact_date(data.get("publishDate")),
            "title": str(data.get("title") or ""), "content_text": html_to_text(str(data.get("content") or "")),
            "attachment_url": attachment_url}
    if xlsx_bytes is None:
        return pd.DataFrame([{**base, "index_code": None, "index_name": None, "side": None,
                              "code": None, "name": None}], columns=DETAIL_COLS)
    table = parse_adjustment_xlsx(xlsx_bytes)
    if table.empty:
        raise RuntimeError(f"中证公告 {base['ann_id']} 附件解析为空 —— 多半是附件版式改了,不是「这次没调样」")
    return pd.DataFrame([{**base, **row} for row in table.to_dict("records")], columns=DETAIL_COLS)


def fetch_rebalance_detail(ann_id: str | int, timeout: int = _TIMEOUT) -> pd.DataFrame:
    r = requests.get(DETAIL_URL, params={"id": ann_id}, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    data = (r.json() or {}).get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"中证公告详情 id={ann_id} 形态异常(data 不是 object)")
    encl = [e for e in (data.get("enclosureList") or [])
            if str(e.get("fileUrl") or "").lower().endswith(".xlsx")]
    raw, url = None, None
    if encl:
        url = encl[0]["fileUrl"]
        rr = requests.get(url, headers=_HEADERS, timeout=timeout)
        rr.raise_for_status()
        raw = rr.content
    return detail_frame(data, raw, url)


_DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def parse_effective_date(content_text: str) -> tuple[str | None, str | None]:
    """正文 → (YYYYMMDD, kind)。

    kind = "after_close":「X 日收市后生效」→ 被动调仓收盘日 = X;
           "from_date"  :「X 日起生效 / 正式实施」→ 被动调仓收盘日 = X 的前一交易日(由调用方查交易日历);
           None         :解析不出,或解析出多个互相竞争的候选(如「自退市日起」;落款日期不算)。

    正文里常同时出现两类日期候选:引用规则本身的生效日(常见于括注引文,写在决定句之前)与
    **本次调整**真正的操作生效日 —— 两者用词相同,**位置先后不是可靠信号**(引文可能在前也可能
    在后)。故用「kind 优先 + 唯一性」两级裁决,不做括注剥离/关键词邻近度打分/分句这类无实测样本
    撑腰的启发式:
    1) 若候选里恰有一个 after_close —— 不论 from_date 候选有多少个 —— 就是它(指数公司真实公告
       的操作句几乎总用「收市后」措辞,引文/次要提法多用较松的「起生效/实施」);
    2) 否则若候选总数恰为 1,就是它;
    3) 否则(0 个,或 ≥2 个仍不满足①)→ (None, None)——**宁可诚实退化,不猜**:调用方
       (Task 3 `eff_close_from`)会落回按公告周期推算的 `rule_eff_close_date`,或标记
       `unknown_eff`,这都好过在硬门上认错生效夜。
    """
    hits: list[tuple[str, str]] = []
    for m in _DATE_RE.finditer(content_text):
        tail = content_text[m.end(): m.end() + 12]
        d = f"{int(m.group(1)):04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"
        if "收市后" in tail or "收盘后" in tail:
            hits.append((d, "after_close"))
        elif re.match(r"\s*起?\s*(正式)?\s*(生效|实施)", tail):
            hits.append((d, "from_date"))
    after_close = [h for h in hits if h[1] == "after_close"]
    if len(after_close) == 1:
        return after_close[0]
    if len(hits) == 1:
        return hits[0]
    return None, None

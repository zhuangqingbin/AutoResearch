#!/usr/bin/env python3
"""scan-market · L3 公告情感 —— tushare anns_d 标题 harvest + 紧凑 digest(FinGPT 情感即特征)。

design: docs/specs/2026-06-22-l3-opus-sentiment-design.md §架构。
确定性、零 LLM:harvest 入湖(按 ann_date 不可变,L4 复用)+ 落 staging;digest 把每股近期公告
压成「数 + 方向标签 + 最新标题」。情感方向最终由 Opus 在 holistic 内细化(标题可中性/反讽)。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from autoresearch.data.cache import get_or_fetch

# 标题关键词 → 方向(粗;Claude 细化)。覆盖 A 股最常见材料事件。
_EVENT_TAGS = {
    "利多": ["回购", "增持", "中标", "股权激励", "业绩预增", "预增", "预盈", "扭亏",
             "定增", "重组", "收购", "签约", "订单", "获批"],
    "利空": ["减持", "质押", "问询", "关注函", "立案", "商誉减值", "业绩预减", "预减",
             "预亏", "退市", "违规", "诉讼", "处罚", "冻结", "终止"],
}


def _tag(title: str) -> str:
    for label, kws in _EVENT_TAGS.items():
        if any(kw in title for kw in kws):
            return label
    return ""


def news_digest(anns: list[dict], prefix: str = "news") -> dict:
    """近期新闻/公告 list → {<prefix>_n, <prefix>_tags("利多×2|利空×1"), <prefix>_head(最新标题≤24)}。

    prefix="news"(默认,anns_d 公告)/ "med"(akshare 媒体新闻)→ 两路情感列并存。空→缺省。
    """
    if not anns:
        return {f"{prefix}_n": 0, f"{prefix}_tags": "", f"{prefix}_head": "—"}
    counts: dict[str, int] = {}
    for a in anns:
        lab = _tag(str(a.get("title", "")))
        if lab:
            counts[lab] = counts.get(lab, 0) + 1
    tags = "|".join(f"{k}×{v}" for k, v in counts.items())
    latest = max(anns, key=lambda a: str(a.get("ann_date", "")))
    head = str(latest.get("title", ""))[:24] or "—"
    return {f"{prefix}_n": len(anns), f"{prefix}_tags": tags, f"{prefix}_head": head}


def _trade_days_for(date: str, lookback_days: int) -> list[str]:
    """最近 lookback_days 个交易日(YYYYMMDD)。失败 → 空(harvest 据此降级)。"""
    try:
        from autoresearch.data.tushare_source import _pro, _trade_days, resolve_momentum_dates
        pro = _pro()
        last = resolve_momentum_dates(pro, date)[0]
        start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
        return _trade_days(pro, start, last)[-lookback_days:]
    except Exception:  # noqa: BLE001
        return []


def harvest_l3_news(date: str, codes, root: Path | None = None, lookback_days: int = 10) -> dict:
    """对 codes 拉最近 ~lookback_days 公告(anns_d 按 ann_date 入湖)→ 按 code 分桶 + 落 staging。

    best-effort:任一 ann_date 拉取失败 → 跳过该日;全失败 → 各 code 空列表。返回 {code: [anns]}。
    """
    from autoresearch.data.tushare_source import _code6
    root = root or Path("context/scan")
    out_dir = root / date / "L3_news"
    out_dir.mkdir(parents=True, exist_ok=True)
    want = {str(c).zfill(6) for c in codes}
    buckets: dict[str, list] = {c: [] for c in want}

    for dd in _trade_days_for(date, lookback_days):
        try:
            df = get_or_fetch("anns_d", {"ann_date": dd}, today=date)
        except Exception:  # noqa: BLE001 — 无权限/无端点 → 跳过该日(降级)
            continue
        if df is None or not len(df) or "ts_code" not in df.columns:
            continue
        df = df.assign(_c=_code6(df["ts_code"]))
        for c, g in df[df["_c"].isin(want)].groupby("_c"):
            buckets[c].extend(g.drop(columns=["_c"]).to_dict("records"))

    for c in want:
        (out_dir / f"{c}.json").write_text(json.dumps(buckets[c], ensure_ascii=False, default=str),
                                           encoding="utf-8")
    return buckets


def harvest_l3_web_news(date: str, codes, root: Path | None = None) -> dict:
    """对 codes 逐股拉 akshare 个股新闻(stock_news_em,as_of 入湖)→ 归一 + 分桶 + 落 staging。

    归一:akshare 中文列 `新闻标题→title`、`发布时间→ann_date`(供 news_digest(prefix="med") 复用)。
    best-effort:单股取数失败/空 → 该 code 空列表(降级隔离,不阻塞)。产出
    context/scan/<date>/L3_webnews/<code>.json,返回 {code: [news]}。
    """
    root = root or Path("context/scan")
    out_dir = root / date / "L3_webnews"
    out_dir.mkdir(parents=True, exist_ok=True)
    buckets: dict[str, list] = {}
    for c in codes:
        code = str(c).zfill(6)
        try:
            df = get_or_fetch("stock_news_em", {"symbol": code}, today=date)
            rows = []
            if df is not None and len(df):
                for _, r in df.iterrows():
                    rows.append({"title": str(r.get("新闻标题", "")),
                                 "ann_date": str(r.get("发布时间", ""))})
            buckets[code] = rows
        except Exception:  # noqa: BLE001 — 单股降级隔离(无网/被限/无该股)
            buckets[code] = []
        (out_dir / f"{code}.json").write_text(
            json.dumps(buckets[code], ensure_ascii=False, default=str), encoding="utf-8")
    return buckets

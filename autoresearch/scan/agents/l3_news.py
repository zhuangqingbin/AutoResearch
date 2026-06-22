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


def news_digest(anns: list[dict]) -> dict:
    """近期公告 list → {news_n, news_tags("利多×2|利空×1"), news_head(最新标题≤24)}。空→缺省。"""
    if not anns:
        return {"news_n": 0, "news_tags": "", "news_head": "—"}
    counts: dict[str, int] = {}
    for a in anns:
        lab = _tag(str(a.get("title", "")))
        if lab:
            counts[lab] = counts.get(lab, 0) + 1
    tags = "|".join(f"{k}×{v}" for k, v in counts.items())
    latest = max(anns, key=lambda a: str(a.get("ann_date", "")))
    head = str(latest.get("title", ""))[:24] or "—"
    return {"news_n": len(anns), "news_tags": tags, "news_head": head}


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

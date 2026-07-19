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

# 标题关键词 → 方向(粗;Claude 在 holistic 内细化)。覆盖 A 股最常见材料事件。
_EVENT_TAGS = {
    "利多": ["回购", "增持", "中标", "股权激励", "业绩预增", "预增", "预盈", "扭亏",
             "定增", "重组", "收购", "签约", "订单", "获批"],
    "利空": ["减持", "质押", "问询", "关注函", "立案", "商誉减值", "业绩预减", "预减",
             "预亏", "退市", "违规", "诉讼", "处罚", "冻结", "终止"],
}
# 否定/澄清词:标题含之 → 中性化(保守不翻转,避免"不增持/澄清重组"误判方向)。
_NEGATORS = ("未", "不", "否认", "澄清", "辟谣", "无", "暂不", "拟不", "取消")
# 强信号词(intensity ×2):材料度高的真事件,区别于"签约/质押"这类弱噪声。
_STRONG = frozenset({"回购", "增持", "中标", "预增", "扭亏", "重组", "收购", "获批", "订单",
                     "立案", "退市", "商誉减值", "处罚", "诉讼", "冻结", "违规"})
# 监管事项词(⚠监管旗专用,含"监管/证监会/交易所"三扩展词)。**独立于 _EVENT_TAGS**:
# news_digest key 集合与情感口径被契约测试冻结(test_news_digest_default_prefix_unchanged),
# 旗只在 l3_table_md(reg_flag=True) 时按需计算,默认关 = parity。spec 2026-07-05 §5.3。
_REG_WORDS = ("立案", "问询", "关注函", "处罚", "违规", "诉讼", "监管", "证监会", "交易所")


def reg_hits(titles) -> str:
    """近期公告标题 → 命中的监管事项词(去重保序,"|" 连接);无命中/空 → ""。"""
    seen: list[str] = []
    for t in titles:
        for w in _REG_WORDS:
            if w in str(t) and w not in seen:
                seen.append(w)
    return "|".join(seen)


def reg_hits_for_code(day_dir: Path, code: str) -> str:
    """某票监管旗:扫 L3_news(anns_d 公告)标题。

    (原 L3_webnews 回退随其 producer 于 2026-07-13 移除——该目录从未有生产写入者。)
    坏 JSON/空 → ""(降级不抛)。
    """
    code6 = str(code).zfill(6)
    for sub in ("L3_news",):
        fp = Path(day_dir) / sub / f"{code6}.json"
        if not fp.exists():
            continue
        try:
            items = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 坏 JSON 降级下一源
            continue
        if items:
            return reg_hits([a.get("title", "") for a in items])
    return ""


def score_title(title: str) -> tuple[str, float]:
    """标题 → (direction, intensity)。direction∈{利多,利空,''};intensity≥0(强词×2 + 命中累加)。

    ① 否定/澄清词在标题 → 中性化((''、0.0),保守不翻转;② 强词权重 2、其余 1,正负净额定方向,
    势均 → 中性。比纯首词命中更稳:方向更准 + 给 holistic 一个数值强度先验(`*_sent`)。
    """
    t = str(title)
    if any(neg in t for neg in _NEGATORS):
        return "", 0.0
    pos = sum(2 if kw in _STRONG else 1 for kw in _EVENT_TAGS["利多"] if kw in t)
    neg = sum(2 if kw in _STRONG else 1 for kw in _EVENT_TAGS["利空"] if kw in t)
    if pos == neg:
        return "", 0.0
    return ("利多", float(pos)) if pos > neg else ("利空", float(neg))


def news_digest(anns: list[dict], prefix: str = "news") -> dict:
    """近期新闻/公告 list → {<prefix>_n, <prefix>_tags("利多×2|利空×1"), <prefix>_head(≤24), <prefix>_sent}。

    `<prefix>_sent`∈[-1,1]:按 intensity 加权的净情感(利多正/利空负;mass 归一)→ 给 holistic 数值先验。
    prefix="news"(anns_d 公告)/ "med"(akshare 媒体新闻)。空→缺省(sent=0.0)。
    """
    if not anns:
        return {f"{prefix}_n": 0, f"{prefix}_tags": "", f"{prefix}_head": "—", f"{prefix}_sent": 0.0}
    counts: dict[str, int] = {}
    net = mass = 0.0
    for a in anns:
        direction, inten = score_title(str(a.get("title", "")))
        if direction:
            counts[direction] = counts.get(direction, 0) + 1
            signed = inten if direction == "利多" else -inten
            net += signed
            mass += inten
    tags = "|".join(f"{k}×{v}" for k, v in counts.items())
    latest = max(anns, key=lambda a: str(a.get("ann_date", "")))
    head = str(latest.get("title", ""))[:24] or "—"
    sent = round(net / mass, 2) if mass > 0 else 0.0
    return {f"{prefix}_n": len(anns), f"{prefix}_tags": tags, f"{prefix}_head": head, f"{prefix}_sent": sent}


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
    P2b 有界降级:权限类异常(消息含"权限"/错误码 40203)必然日日同错 → 首次命中即 break;
    其余瞬时异常(网络抖动等)累计 ≥3 次同样 break,避免为 0 字节数据烧满全部 lookback_days 次退避。
    """
    from autoresearch.data.tushare_source import _code6
    root = root or Path("context/scan")
    out_dir = root / date / "L3_news"
    out_dir.mkdir(parents=True, exist_ok=True)
    want = {str(c).zfill(6) for c in codes}
    buckets: dict[str, list] = {c: [] for c in want}

    _PERM_MARKS = ("权限", "40203")
    fails = 0
    for dd in _trade_days_for(date, lookback_days):
        try:
            df = get_or_fetch("anns_d", {"ann_date": dd}, today=date)
        except Exception as e:  # noqa: BLE001 — 无权限/无端点 → 有界降级(P2b)
            fails += 1
            if any(m in repr(e) for m in _PERM_MARKS) or fails >= 3:
                break           # 权限错必然日日同错;瞬时错也别为 0 字节数据烧满 10×4 连退避
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


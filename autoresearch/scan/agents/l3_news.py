#!/usr/bin/env python3
"""scan-market · L3 公告情感 —— tushare anns_d 标题 harvest + 紧凑 digest(FinGPT 情感即特征)。

design: docs/specs/2026-06-22-l3-opus-sentiment-design.md §架构。
确定性、零 LLM:harvest 入湖(按 ann_date 不可变,L4 复用)+ 落 staging;digest 把每股近期公告
压成「数 + 方向标签 + 最新标题」。情感方向最终由 Opus 在 holistic 内细化(标题可中性/反讽)。

**Wave9 final-fix C-1(2026-07-30)**:主源(tushare `anns_d`)自 2026-07-18 起无权限,本文件
此前只会写空桶 —— `autoresearch.data.sources.anns_fallback.fetch_anns`(akshare 兜底源)存在
但全仓零生产调用点,`health.anns_source_status` 因此恒判 `blind`。现在 `harvest_l3_news` 在
主源 harvest 结束后,对**仍是空桶的每一只票**补调一次兜底源(不管空桶是权限/瞬时故障/还是
主源当天恰好真的没查到——B 级契约:兜底也失败就保持空桶 + stderr 记账,绝不抛异常阻断)。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.data.cache import get_or_fetch
from autoresearch.data.sources.anns_fallback import (
    SOURCE_TAG as _FALLBACK_SOURCE_TAG,
    fetch_anns as _fallback_fetch_anns,
)

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


def harvest_l3_news(date: str, codes, root: Path | None = None, lookback_days: int | None = None) -> dict:
    """对 codes 拉最近 ~lookback_days 公告(anns_d 按 ann_date 入湖)→ 按 code 分桶 + 落 staging。

    best-effort:任一 ann_date 拉取失败 → 跳过该日;全失败 → 各 code 空列表。返回 {code: [anns]}。
    P2b 有界降级:权限类异常(消息含"权限"/错误码 40203)必然日日同错 → 首次命中即一次性打印
    显式告警后停(不再逐日试探;降级必须留痕,anns_d 已于 2026-07-18 退役见 `contracts.py`);
    其余瞬时异常(网络抖动等)累计 ≥3 次同样 break,避免为 0 字节数据烧满全部 lookback_days 次退避
    ——**两类告警文案分叉**(Wave4 Task1 Minor-1):权限类才说"已退役",瞬时错误如实说"取数
    连续失败"并带 `repr(e)` 摘要,不把 unexpected 降级误报成 expected(那正是本 task 要治的病
    的镜像)。

    **Wave9 final-fix C-1**:主源 harvest 结束后,对**每一只当前仍是空桶的票**(不管空桶是
    因为权限/瞬时故障 break、还是主源当天就是恰好没查到——两种情况在产物上长得一样,这正是
    本条要治的病)补调一次 `anns_fallback.fetch_anns`;有料就写进该票的桶(行自带
    `source=="cninfo"`,`health.anns_source_status` 据此判 `fallback`)。B 级契约:兜底
    本身炸了/仍空 → 保持空桶,不阻断、只记账(见下方 stderr)。已有主源真数据的桶不覆盖。
    """
    if lookback_days is None:                       # l3.lookback_days.news(缺省 10 交易日)
        from autoresearch.scan.user_config import knob
        lookback_days = int((knob("l3", "lookback_days", None, {}) or {}).get("news", 10))
    from autoresearch.data.tushare_source import _code6
    root = root or ws.scan_root()
    out_dir = root / date / "L3_news"
    out_dir.mkdir(parents=True, exist_ok=True)
    want = {str(c).zfill(6) for c in codes}
    buckets: dict[str, list] = {c: [] for c in want}

    _PERM_MARKS = ("权限", "40203")
    fails = 0
    perm = False              # 权限类异常命中(anns_d 已退役,expected——contracts.py 已标)
    flaky = False             # 纯瞬时异常(网络抖动等)累计 ≥3 次(unexpected,勿贴"已退役"标签)
    last_err: Exception | None = None
    for dd in _trade_days_for(date, lookback_days):
        try:
            df = get_or_fetch("anns_d", {"ann_date": dd}, today=date)
        except Exception as e:  # noqa: BLE001 — 端点退役/无权限 → 一次性告警后停(降级必须留痕)
            fails += 1
            last_err = e
            if any(m in repr(e) for m in _PERM_MARKS):
                perm = True
                break           # 权限错必然日日同错;不再逐日试探
            if fails >= 3:
                flaky = True
                break           # 瞬时错也别为 0 字节数据烧满 10×4 连退避
            continue
        if df is None or not len(df) or "ts_code" not in df.columns:
            continue
        df = df.assign(_c=_code6(df["ts_code"]))
        for c, g in df[df["_c"].isin(want)].groupby("_c"):
            buckets[c].extend(g.drop(columns=["_c"]).to_dict("records"))

    if perm:
        # contracts.py 已标 anns_d 退役(2026-07-18);此处让它在**运行时**也可见——
        # 静默写空桶正是 news_n/news_sent/news_head 三列连续多个扫描日全为 0 而无人察觉的原因。
        print(f"[l3_news] ⚠️ anns_d 已退役/无权限 → 公告标题流为空({len(want)} 只票的 "
              f"news_n/news_sent/news_head 本日全为缺省值),L3 情感列不可用。", file=sys.stderr)
    elif flaky:
        # 纯瞬时错误(非权限类)——不得说成"已退役"(把 unexpected 降级误报成 expected 是
        # 本 task 要治的病的镜像,Minor-1);文案如实 + 带 repr(e) 摘要,便于事后区分真实成因。
        print(f"[l3_news] ⚠️ 公告取数连续失败({fails} 次,{last_err!r})→ 公告标题流为空"
              f"({len(want)} 只票的 news_n/news_sent/news_head 本日全为缺省值),疑为网络抖动,"
              f"非 anns_d 退役。", file=sys.stderr)

    # Wave9 final-fix C-1:兜底源补桶 —— 只补仍是空桶的票,已有主源真数据的桶不覆盖/不重复查询。
    # Wave10 A9:此前是**串行** —— 203 只 × 1.56s/call 实测 316.8s,L3 附加约 5min。
    # 改有界并发 + 逐票磁盘缓存(见 anns_fallback 模块头):同一分析日重跑几乎零网络。
    fb_hits = fb_rows = fb_errors = 0
    # 并发放在**调用侧**、逐票仍走 `_fallback_fetch_anns` 这个模块级名字 ——
    # 它是既有测试的 monkeypatch 点(直接 import 批量函数会绕过 patch,让那些锁住
    # 「兜底真被调用 / 异常降级不抛」的用例静默失效)。
    from concurrent.futures import ThreadPoolExecutor

    def _one(code):
        try:
            return code, _fallback_fetch_anns(code, date), None
        except Exception as exc:  # noqa: BLE001 — B 级契约:兜底炸了不得阻断漏斗
            return code, None, exc

    empty = [c for c in want if not buckets[c]]
    results = []
    if empty:
        with ThreadPoolExecutor(max_workers=min(8, len(empty))) as pool:
            results = list(pool.map(_one, empty))
    for c, fb, err in results:
        if err is not None or fb is None:
            fb_errors += 1
            continue
        if fb:
            buckets[c] = fb
            fb_hits += 1
            fb_rows += len(fb)
    if fb_hits or fb_errors:
        extra = f"、{fb_errors} 只票兜底取数异常(已降级为空)" if fb_errors else ""
        print(f"[l3_news] ℹ️ 兜底源({_FALLBACK_SOURCE_TAG})补桶:{fb_hits}/{len(want)} 只票有料"
              f"(共 {fb_rows} 行){extra}。", file=sys.stderr)

    for c in want:
        (out_dir / f"{c}.json").write_text(json.dumps(buckets[c], ensure_ascii=False, default=str),
                                           encoding="utf-8")
    return buckets


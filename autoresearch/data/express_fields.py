"""业绩快报(tushare `express`)字段语义的**单一事实源**(纯函数,零网络零依赖)。

为什么单开一个模块:同一个 `yoy_net_profit` 字段有两条独立消费腿——
`data/tushare_enrich.ashare_calendar_ts`(→ slim → L4 卡的"已核"级来源)与
`dossier/reconcile._fetch_actual`(→ 档案 §5)。2026-07-24 终审逮出**同源两处**
误读(金额当增速),修一处 ≠ 修完一类,故把语义收进此处,两侧 import 同一份
(不留第三份实现)。

两条契约:
  * `express_yoy_pct` —— 同比增速须自算,`yoy_net_profit` 是去年同期**金额**;
  * `express_expired` —— 快报有时效,`pro.express(ts_code=...)` 不带 period 会
    返回全部历史,"最新一行"可能是十几年前的。
"""
from __future__ import annotations

from datetime import datetime

EXPRESS_MAX_MONTHS = 15   # 快报时效上限 ≈5 个报告期(年报 4/30 披露截止后再留冗余)


def _f(v) -> float | None:
    """任意值 → float;None/NaN/非数 → None(NaN 会穿 `or 默认值` 与 format 两道防线)。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def express_yoy_pct(n_income, yoy_base) -> float | None:
    """业绩快报净利同比(%);tushare `yoy_net_profit` 是**去年同期净利润金额**非增速
    (2026-07-24 活体逮出:419 处 slim 印出 8 位数百分比)。去年同期 ≤0 → None
    (增速无意义),任一缺/NaN → None。

    真值回归:688766 普冉股份 20251231 快报 n_income=208232900、
    yoy_net_profit=292416600 → **−28.8%**,与该票 forecast 腿独立口径的
    "略减 −29.89%" 吻合(误读版本印的是 `+292416600.0%`)。
    """
    cur, base = _f(n_income), _f(yoy_base)
    if cur is None or base is None or base <= 0:
        return None
    return (cur / base - 1) * 100


def _parse_day(s) -> datetime | None:
    """'20260630' / '2026-06-30' → datetime;其它(含 NaN/空/非日期)→ None。"""
    t = "" if s is None else str(s).strip().replace("-", "")
    if len(t) != 8 or not t.isdigit():
        return None
    try:
        return datetime.strptime(t, "%Y%m%d")
    except ValueError:
        return None


def express_expired(end_date, asof=None, max_months: int = EXPRESS_MAX_MONTHS) -> bool:
    """快报报告期 `end_date` 距 `asof` 是否超过 `max_months` 个月(默认 15)。

    `pro.express(ts_code=...)` 不传 period 会取该股**全部历史**快报,`tail(1)` 只保证
    "最新那条存在",不保证它**近期**——活体:slim 里出现 `业绩快报(tushare,20121231)`
    /`20131231`/`20201231`,十几年前的数据被冠以"快报=未审计,早于正式财报"喂进 L4 卡。

    `asof` 缺/不可解析 → 用当前时钟;`end_date` 不可解析 → **视为过期**
    (无法证明其新鲜 = 不采用;降级留痕由调用方渲染,不静默丢)。
    """
    end = _parse_day(end_date)
    if end is None:
        return True
    ref = _parse_day(asof) or datetime.now()
    months = (ref.year - end.year) * 12 + (ref.month - end.month)
    return months > max_months

"""扫描数据日的唯一解析口径:缺省 = 最近已结算交易日(节假日/周末自动回退)。

2026-09-26(周六;9/25–9/27 中秋休市):主会话靠手工探 tushare trade_cal 才定出数据日 2026-09-24
—— 每次开扫都靠人记节假日。这里把「扫哪天」收成一条确定性命令,SKILL 步骤 0 直接吃它的输出:

- 不给日期 → 最近已结算交易日(复用夜间预热同一把尺 `prewarm.latest_settled_trade_date`:
  今天是交易日且已过 19:15 → 今天;否则上一交易日)。
- 给了日期 → 必须是已开市的交易日,否则非零退出并给出建议日期。非交易日写进 run 标签会让
  报告目录「数据日」段和结果账本的 T+1/T+2 锚都从错的日子起算。

stdout 只打印 ``YYYY-MM-DD``(可直接 ``DATE=$(...)``),说明一律走 stderr。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from autoresearch.scan.prewarm import latest_settled_trade_date


class NotATradingDay(ValueError):
    """显式给的日期不是(已开市的)交易日。"""


def resolve_scan_date(date: str | None, *, now: datetime | None = None) -> str:
    now = now or datetime.now()
    latest = latest_settled_trade_date(now)
    if date is None:
        return latest
    try:
        day = datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise NotATradingDay(f"{date!r} 不是 YYYY-MM-DD") from exc
    if day.date() > now.date():
        raise NotATradingDay(f"{date} 在未来;最近已结算交易日 = {latest}")
    from autoresearch.data.tushare_source import _pro, _trade_days
    compact = day.strftime("%Y%m%d")
    days = _trade_days(_pro(), (day - timedelta(days=30)).strftime("%Y%m%d"), compact)
    if not days or days[-1] != compact:
        prev = f"{days[-1][:4]}-{days[-1][4:6]}-{days[-1][6:]}" if days else "?"
        raise NotATradingDay(
            f"{date} 不是交易日(周末/休市);它之前最近的交易日 = {prev},最近已结算交易日 = {latest}")
    return date


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autoresearch.scan.trade_date",
                                 description="扫描数据日:缺省=最近已结算交易日(节假日/周末自动回退)")
    ap.add_argument("date", nargs="?", default=None, help="显式 YYYY-MM-DD(须为交易日)")
    ns = ap.parse_args(argv)
    try:
        resolved = resolve_scan_date(ns.date, now=now)
    except NotATradingDay as exc:
        print(f"[trade_date] ✗ {exc}", file=sys.stderr)
        return 2
    if ns.date is None:
        print(f"[trade_date] 最近已结算交易日 = {resolved}", file=sys.stderr)
    print(resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

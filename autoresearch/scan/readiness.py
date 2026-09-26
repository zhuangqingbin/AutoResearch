"""stk_factor_pro 就绪探针 —— 无人值守扫描开跑前的「湖灌齐了没」(确定性,零 LLM)。

design: docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md §6 C2。

**为什么要等**:tushare 盘后边灌边服务,`stk_factor_pro` 灌到 ~21:10 才齐(09-07 实测:
19:32 238 行 → 20:52 4457 行、每分钟 +45 → 21:10 稳定 5547 行)。4457 行能过 A 级契约
的 3000 行门,却会让 ~1100 只票的技术因子组静默成 NaN 并毒化当日湖分区 —— 漏斗照跑,
残废看不出。人工开扫一直靠「连续两次不变且 ≥5300 才开 frame」这条手工纪律,这里把它
变成调度器能执行的判据:

- 每 ``interval_s`` 秒数一次当日行数;最近 ``stable_polls`` 次读数**相等**且 **≥min_rows**
  → 就绪;
- 到 ``deadline``(本地 HH:MM)仍未就绪 → 放弃(调用方推送「未开」,不带病开扫);
- 单次取数失败(DNS/限流)记一行、打断稳定性,但不终止探针。

夜间预热(`scan.prewarm`)没有这道探针,也不调用它:预热的行为逐字不变。

  uv run --no-sync python -m autoresearch.scan.readiness 2026-09-28 --deadline 22:30
"""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from datetime import datetime

MIN_ROWS = 5300
STABLE_POLLS = 2
INTERVAL_S = 300
DEADLINE = "22:30"


def count_factor_rows(date: str) -> int:
    """tushare `stk_factor_pro` 在 ``date``(YYYY-MM-DD)的全市场行数(一次网络调用)。"""
    from autoresearch.data.tushare_source import _pro

    frame = _pro().query("stk_factor_pro", trade_date=date.replace("-", ""), fields="ts_code")
    return 0 if frame is None else len(frame)


def _deadline_at(now: datetime, hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def factor_rows_ready(
    date: str,
    *,
    min_rows: int = MIN_ROWS,
    stable_polls: int = STABLE_POLLS,
    interval_s: float = INTERVAL_S,
    deadline: str = DEADLINE,
    count_rows: Callable[[str], int] | None = None,
    now: Callable[[], datetime] = datetime.now,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] | None = None,
) -> bool:
    """轮询到「最近 ``stable_polls`` 次读数相等且 ≥``min_rows``」为止;过 ``deadline`` → False。"""
    counter = count_rows or count_factor_rows
    emit = log or (lambda line: print(line, flush=True))
    stop = _deadline_at(now(), deadline)
    readings: list[int | None] = []
    while True:
        try:
            rows: int | None = int(counter(date))
        except Exception as exc:  # noqa: BLE001 - a failed poll is a fact, not the end
            rows = None
            emit(f"[readiness] {now():%H:%M:%S} stk_factor_pro {date} 取数失败:"
                 f"{type(exc).__name__}: {exc}")
        else:
            emit(f"[readiness] {now():%H:%M:%S} stk_factor_pro {date} = {rows} 行")
        readings.append(rows)
        recent = readings[-stable_polls:]
        if (len(recent) == stable_polls and recent[0] is not None
                and all(value == recent[0] for value in recent) and recent[0] >= min_rows):
            emit(f"[readiness] 就绪:{recent[0]} 行,连续 {stable_polls} 次不变")
            return True
        remaining = (stop - now()).total_seconds()
        if remaining <= 0:
            emit(f"[readiness] 截至 {deadline} 未就绪(读数 {readings[-3:]})")
            return False
        sleep(min(float(interval_s), remaining))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autoresearch.scan.readiness",
                                 description="stk_factor_pro 就绪探针(0=就绪,1=截止未就绪)")
    ap.add_argument("date", help="YYYY-MM-DD(交易日)")
    ap.add_argument("--deadline", default=DEADLINE, help=f"本地 HH:MM,缺省 {DEADLINE}")
    ap.add_argument("--min-rows", type=int, default=MIN_ROWS)
    ap.add_argument("--interval", type=float, default=INTERVAL_S, help="轮询间隔秒")
    args = ap.parse_args(argv)
    ready = factor_rows_ready(args.date, min_rows=args.min_rows, interval_s=args.interval,
                              deadline=args.deadline)
    return 0 if ready else 1


__all__ = [
    "DEADLINE", "INTERVAL_S", "MIN_ROWS", "STABLE_POLLS", "count_factor_rows",
    "factor_rows_ready", "main",
]


if __name__ == "__main__":
    sys.exit(main())

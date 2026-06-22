#!/usr/bin/env python3
"""lake-native 历史 harvest —— 把 core 所需端点的全市场历史落进 parquet 湖(取一次永不重取)。

design: docs/specs/2026-06-22-l2-zoo-champion-design.md §P-A。

`plan_harvest` 规划 成型日 F(每 step 交易日)+ 连续价格面板 P(供 60d 动量回看 + 10d 前瞻);
`harvest` 对每个 (endpoint, date) 调 `get_or_fetch` 落湖。**断点续** = 湖命中即跳(零取数重跑)。
端点 = `DataHandler.materialize("core")` 所需:daily(价格面板)+ 8 因子端点 + stock_basic(static)。
"""
from __future__ import annotations

import sys
import time

from autoresearch.data.cache import get_or_fetch, lake_path

# 成型日因子快照端点(daily 走价格面板 P;stock_basic 是 static,单独取一次)。
_FACTOR_EPS = ("daily_basic", "stk_factor_pro", "cyq_perf", "moneyflow",
               "hk_hold", "margin_detail", "block_trade", "top_inst")


def plan_harvest(trade_days, start, end, step, back=60, fwd=10):
    """(F 成型日, P 价格面板)。trade_days = 升序紧凑(YYYYMMDD)交易日历。

    F = [start, end] 内每 step 个交易日取一个;P = [F[0]-back, F[-1]+fwd] 的连续交易日。
    范围内无交易日 → ([], [])。
    """
    cal = list(trade_days)
    start, end = start.replace("-", ""), end.replace("-", "")
    in_rng = [d for d in cal if start <= d <= end]
    F = in_rng[::step]
    if not F:
        return [], []
    i0, i1 = cal.index(F[0]), cal.index(F[-1])
    P = cal[max(0, i0 - back): min(len(cal), i1 + fwd + 1)]
    return F, P


def _trade_days_live(end: str) -> list[str]:
    """tushare 交易日历(2018 起 → end);CLI 实跑用。"""
    from autoresearch.data.tushare_source import _pro, _trade_days
    return _trade_days(_pro(), "20180101", end.replace("-", ""))


def harvest(start, end, step=3, *, today=None, sleep=0.0, fetch=None, trade_days=None) -> dict:
    """落湖 [start, end] 的 core 端点历史。

    today = 结算锚(date>=today 的盘中日不写,见 get_or_fetch);缺省 = end。
    fetch = 注入取数后端(测试用桩);缺省走 sources.fetch。trade_days 缺省 = tushare 日历。
    返回 {"F": 成型日数, "P": 价格面板日数, "calls": 本次实际新取数}。
    """
    today = today or end
    cal = trade_days if trade_days is not None else _trade_days_live(end)
    F, P = plan_harvest(cal, start, end, step)
    calls = {"n": 0}

    def _go(ep: str, params: dict) -> None:
        # 湖已存在 → 跳过(不计 call);否则 get_or_fetch 拉取 + 原子写。
        if lake_path(ep, params, today=today).exists():
            return
        get_or_fetch(ep, params, today=today, fetch=fetch)
        calls["n"] += 1
        if sleep:
            time.sleep(sleep)

    _go("stock_basic", {})
    for i, d in enumerate(P, 1):
        _go("daily", {"trade_date": d})
        if i % 50 == 0 or i == len(P):
            print(f"[harvest] daily {i}/{len(P)} ({d})", file=sys.stderr, flush=True)
    for i, d in enumerate(F, 1):
        for ep in _FACTOR_EPS:
            _go(ep, {"trade_date": d})
        if i % 10 == 0 or i == len(F):
            print(f"[harvest] factors {i}/{len(F)} ({d})", file=sys.stderr, flush=True)
    print(f"[harvest] done F={len(F)} P={len(P)} 新取={calls['n']}", file=sys.stderr)
    return {"F": len(F), "P": len(P), "calls": calls["n"]}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="lake-native 历史 harvest(core 端点)")
    ap.add_argument("start", help="起始日 YYYY-MM-DD / YYYYMMDD")
    ap.add_argument("end", help="结束日 YYYY-MM-DD / YYYYMMDD")
    ap.add_argument("--step", type=int, default=3, help="成型日间隔(交易日)")
    ap.add_argument("--sleep", type=float, default=0.35, help="每次取数后限频(秒)")
    a = ap.parse_args()
    harvest(a.start, a.end, step=a.step, sleep=a.sleep)

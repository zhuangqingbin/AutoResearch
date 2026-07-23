"""fina_mainbz 分业务拆分(dossier 业务模型节的数据腿;确定性,B 级降级)。"""
from __future__ import annotations

import contextlib


def _recent_periods(today: str, n: int) -> list[str]:
    """最近 n 个报告期(年报/中报:0630/1231),按新→旧。today=YYYY-MM-DD。"""
    y, m = int(today[:4]), int(today[5:7])
    ends: list[str] = []
    cur_y, cur_half = (y, 1) if m >= 7 else (y - 1, 2)   # 7 月起上一个可披露期=当年中报,否则去年年报
    for _ in range(n + 2):                                # 多备两期,容忍未披露
        ends.append(f"{cur_y}1231" if cur_half == 2 else f"{cur_y}0630")
        cur_y, cur_half = (cur_y - 1, 2) if cur_half == 1 else (cur_y, 1)
    return ends


def mainbz_latest(code6: str, today: str, *, periods: int = 2, fetch=None) -> list[dict]:
    if fetch is None:
        from autoresearch.data.sources import fetch as fetch
    ts_code = f"{code6}.SH" if code6.startswith(("6", "9")) else (
        f"{code6}.BJ" if code6.startswith(("4", "8")) else f"{code6}.SZ")
    out: list[dict] = []
    got = 0
    for period in _recent_periods(today, periods):
        if got >= periods:
            break
        with contextlib.suppress(Exception):
            df = fetch("fina_mainbz", {"ts_code": ts_code, "period": period, "type": "P"})
            if df is None or not len(df):
                continue
            for _, r in df.iterrows():
                out.append({"period": period, "bz_item": str(r.get("bz_item", "")),
                            "bz_sales": float(r.get("bz_sales") or 0.0),
                            "bz_profit": (None if r.get("bz_profit") is None else
                                          float(r.get("bz_profit") or 0.0))})
            got += 1
    return out

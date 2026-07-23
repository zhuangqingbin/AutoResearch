"""fina_mainbz 分业务拆分(dossier 业务模型节的数据腿;确定性,B 级降级)。"""
from __future__ import annotations

import contextlib

import pandas as pd

from autoresearch.dataflows.symbol_utils import to_ts_code


def _recent_periods(today: str, n: int) -> list[str]:
    """最近 n 个报告期(年报/中报:0630/1231),按新→旧,考虑披露滞后。today=YYYY-MM-DD。

    A股定期报告披露截止:年报 4/30、中报 8/31。据此首猜"最近已披露期":
      - m>=9(9-12月):当年中报(0630)已过 8/31 披露截止 → 首猜当年 0630
      - 5<=m<=8(5-8月):当年年报刚过 4/30 披露截止,当年中报未到期 → 首猜去年 1231
      - m<=4(1-4月):当年年报未到期,去年中报早已披露 → 首猜去年 0630
    此后按 0630/1231 交替回推。首猜命中率非 100%(早报/晚报公司存在),真正
    "是否已披露"由 mainbz_latest 对空帧的 continue 兜底跳过——本函数只给出
    合理起点,不保证首个元素必然已披露。
    """
    y, m = int(today[:4]), int(today[5:7])
    if m >= 9:
        cur_y, cur_half = y, 1
    elif m >= 5:
        cur_y, cur_half = y - 1, 2
    else:
        cur_y, cur_half = y - 1, 1
    ends: list[str] = []
    for _ in range(n + 2):                                # 多备两期,容忍未披露
        ends.append(f"{cur_y}1231" if cur_half == 2 else f"{cur_y}0630")
        cur_y, cur_half = (cur_y - 1, 2) if cur_half == 1 else (cur_y, 1)
    return ends


def mainbz_latest(code6: str, today: str, *, periods: int = 2, fetch=None) -> list[dict]:
    if fetch is None:
        from autoresearch.data.sources import fetch as fetch
    ts_code = to_ts_code(code6)
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
                sales, profit = r.get("bz_sales"), r.get("bz_profit")
                out.append({"period": period, "bz_item": str(r.get("bz_item", "")),
                            "bz_sales": 0.0 if pd.isna(sales) else float(sales),
                            "bz_profit": None if pd.isna(profit) else float(profit)})
            got += 1
    return out

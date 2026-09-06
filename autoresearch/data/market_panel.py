#!/usr/bin/env python3
"""数据湖日线 → 面板 pivot 的**读取** owner(IO 归 data 层)。

2026-09-06(工作包 E4)从 `research.edge_census` 机械搬入,函数体逐字未改;`edge_census`
的两个旧入口改为同对象转发。纯计算(前瞻收益/板制度)在 `common/forward_returns.py`,
两边职责不混:这里只负责「把湖里的东西读成 pivot」,不算任何收益。

纪律不变:交易日 = **湖里有日线文件的日子**,不查交易日历——湖里没有的日子对这些仪器
就是不存在(与已退役的 `paper_nav.trade_days()` 同口径)。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws


def lake_trade_days(lake_daily: Path | None = None) -> list[str]:
    """湖里有日线的交易日(文件名 YYYYMMDD 升序)——与 `paper_nav.trade_days()` 同口径(已退役),
    不查交易日历:湖里没有的日子对本仪器就是不存在。"""
    d = Path(lake_daily) if lake_daily else ws.lake_root() / "daily"
    return sorted(p.stem[:8] for p in d.glob("*.parquet") if p.stem[:8].isdigit())


def load_lake_pivots(dates: list[str], lake_daily: Path | None = None) -> dict[str, pd.DataFrame]:
    """`lake/daily/<d>.parquet`(tushare daily 列)→ {open/high/low/close/pct_chg/amount: pivot[code×date]}。
    只装 `dates`,不整湖加载(1087 日 × 5500 只 × 6 列装不起也不需要)。"""
    d = Path(lake_daily) if lake_daily else ws.lake_root() / "daily"
    frames = []
    for day in dates:
        fp = d / f"{day}.parquet"
        if not fp.exists():
            continue
        df = pd.read_parquet(fp)
        if df.empty:
            continue
        frames.append(pd.DataFrame({
            "code": df["ts_code"].astype(str).str[:6].str.zfill(6), "date": day,
            "open": pd.to_numeric(df["open"], errors="coerce"),
            "high": pd.to_numeric(df["high"], errors="coerce"),
            "low": pd.to_numeric(df["low"], errors="coerce"),
            "close": pd.to_numeric(df["close"], errors="coerce"),
            "pct_chg": pd.to_numeric(df["pct_chg"], errors="coerce"),
            "amount": pd.to_numeric(df["amount"], errors="coerce"),
        }))
    if not frames:
        return {}
    long = pd.concat(frames, ignore_index=True)
    return {f: long.pivot_table(index="code", columns="date", values=f)
            for f in ("open", "high", "low", "close", "pct_chg", "amount")}

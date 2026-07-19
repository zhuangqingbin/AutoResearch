#!/usr/bin/env python3
"""scan staging 产物的规范读取口 —— 把"代码是 6 位零填字符串"这条契约收在一处。

存在理由(2026-07-09 实跑事故):`finalists.csv` 的 `code`/`ticker` 两列都是股票代码,但
往返追加时只给 `code` 指定了 `dtype=str`,`ticker` 被 pandas 解析成 int64 → `002156` 写回成
`2156` → assemble 按 ticker glob 卡片,把两张真卡报成「⚠️卡片缺失」。**只有当日有追加时该路径
才跑**,所以是间歇性的。(当年的两个追加者:`append_carryover` 已随菜单滞回于 2026-07-16 退役;
`watchlist.append_express` 随观察单模块删除。)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_CODE_COLS = ("code", "ticker")


def read_finalists(fp: Path | str) -> pd.DataFrame:
    """读 finalists.csv:代码列一律 6 位零填字符串(防 CSV 往返吃掉前导零)。"""
    df = pd.read_csv(fp, dtype=dict.fromkeys(_CODE_COLS, str))
    for c in _CODE_COLS:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip().str.zfill(6)
    return df

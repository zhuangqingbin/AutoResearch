#!/usr/bin/env python3
"""发布目录名 —— **数据日在前,发布时刻在后**(2026-08-28 用户裁定)。

## 病灶

旧目录名 `YYYYMMDD_HHMM` 的第一段是**跑动日**(`publisher._run_publish` 的
`run_compact = now.strftime("%Y-%m-%d")`),数据日只藏在 `manifest.analysis_date` 里。
于是 `20260826_2000` 这个名字对人说的是"08-26",而它研究的是 **08-25** 的行情
(那晚 `cyq_perf` 未落,A 级数据就绪门把数据日退到了最近完整交易日)。
61 个已发布 run 里 **19 个**的数据日 ≠ 目录名首段 —— 名字天天在撒谎。

新格式把用户真正关心的那一维放前面::

    20260825-0826_2000
    └─数据日──┘ └发布 mmdd_hhmm┘
    "研究的是 08-25 的市场,08-26 20:00 写完的"

排序也顺带对了:按目录名字典序 = 按**数据日**排,而不是按跑动日排(同一数据日的
多次重跑自然聚在一起)。

## 兼容

`parse_run_dir` 同时认 legacy `YYYYMMDD_HHMM`,但 legacy 的 `analysis_date` 返回
`None`——**不猜**。旧目录名的首段是跑动日,把它当数据日读正是本模块要终结的那个错误;
要数据日就去读 `manifest.analysis_date`。历史目录**一律不改名**(run 目录发布后不再变
是 MANIFEST/ROOT 的不变量,改名会让每个历史 run 的 `verify` 当场变红)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

#: 新格式:`<数据日YYYYMMDD>-<发布MMDD>_<发布HHMM>`
RUN_DIR_RE = re.compile(r"^(\d{8})-(\d{4})_(\d{4})$")
#: legacy:`<跑动日YYYYMMDD>_<发布HHMM>`(2026-08-28 之前的全部 run)
LEGACY_RUN_DIR_RE = re.compile(r"^(\d{8})_(\d{4})$")


@dataclass(frozen=True)
class RunDirName:
    """一个发布目录名解析出来的事实。**只放名字里真有的东西,推断留给调用方。**"""

    name: str
    analysis_date: str | None      # 新格式才有(YYYY-MM-DD);legacy 恒 None
    published_mmdd: str            # 发布日 MMDD(legacy 取跑动日的 MMDD)
    hhmm: str                      # 发布时刻 HHMM
    legacy: bool
    run_local_date: str | None     # legacy 才有(YYYY-MM-DD 跑动日);新格式恒 None

    def published_date(self) -> str | None:
        """发布日 YYYY-MM-DD。新格式的年份从数据日推(跨年时 MMDD 变小 → 次年)。"""
        if self.legacy:
            return self.run_local_date
        if not self.analysis_date:
            return None
        year = int(self.analysis_date[:4])
        if self.published_mmdd < self.analysis_date[5:].replace("-", ""):
            year += 1          # 12-31 的数据日、01-02 才发布 → 发布年是次年
        return f"{year:04d}-{self.published_mmdd[:2]}-{self.published_mmdd[2:]}"


def parse_run_dir(name: str) -> RunDirName | None:
    """目录名 → `RunDirName`;两种格式都不匹配返回 `None`(不抛,调用点常在 glob 里)。"""
    text = str(name)
    m = RUN_DIR_RE.fullmatch(text)
    if m:
        day, mmdd, hhmm = m.groups()
        return RunDirName(name=text, analysis_date=f"{day[:4]}-{day[4:6]}-{day[6:]}",
                          published_mmdd=mmdd, hhmm=hhmm, legacy=False,
                          run_local_date=None)
    m = LEGACY_RUN_DIR_RE.fullmatch(text)
    if m:
        day, hhmm = m.groups()
        return RunDirName(name=text, analysis_date=None, published_mmdd=day[4:],
                          hhmm=hhmm, legacy=True,
                          run_local_date=f"{day[:4]}-{day[4:6]}-{day[6:]}")
    return None


def is_run_dir(name: str) -> bool:
    """这个名字是不是一个发布目录名(两种格式之一)。"""
    return parse_run_dir(name) is not None


def format_run_dir(analysis_date: str, published_at: datetime) -> str:
    """`("2026-08-25", 2026-08-26 20:00)` → `"20260825-0826_2000"`。

    `analysis_date` 必须是 `YYYY-MM-DD`——目录名的首段是**数据日**,传错了整条复盘链
    (账本锚点、chain_view、session view)都会跟着错,所以这里硬校验而不是宽容。
    """
    text = str(analysis_date)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"analysis_date 必须是 YYYY-MM-DD:{analysis_date!r}")
    return f"{text.replace('-', '')}-{published_at:%m%d_%H%M}"


__all__ = [
    "LEGACY_RUN_DIR_RE",
    "RUN_DIR_RE",
    "RunDirName",
    "format_run_dir",
    "is_run_dir",
    "parse_run_dir",
]

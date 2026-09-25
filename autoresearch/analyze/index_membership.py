#!/usr/bin/env python3
"""stock-research full 档的确定性「指数成分 / 调样事件」行(design 2026-09-25 §2.6)。

替代 `blocks_ashare` 里「指数调样 → WebSearch 补」的兜底。**只读湖、零网络**:成分看 `lake/index_weight/<idx>@*.parquet`
最新一份,事件看 `lake/csindex_rebalance_detail/*.parquet` 近 60 日。缺什么就写缺什么,不编。

**已知覆盖缺口**(同 `scan.index_events` 模块 docstring):`399006` 创业板指是深证/国证口径指数,
其调样公告不在本模块读的中证指数公司公告源(`csindex_rebalance_detail`)里发布——事件行永远不会
为它产出一条。成分行不受影响:`index_weight` 月末快照六指数一致覆盖,创业板指的成分归属照常判定。
这两条腿(成分/事件)彼此独立地 presence-gated,不是对称的——不要把"六指数"读成"六指数事件都
覆盖"。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.contracts.index_whitelist import INDEX_WHITELIST
from autoresearch.data.sources.csindex import parse_effective_date


def _fmt(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if d and len(d) == 8 else d


def _membership(code6: str, lake: Path) -> tuple[list[str], str | None]:
    names, snap = [], None
    for idx, name in INDEX_WHITELIST.items():
        files = sorted((lake / "index_weight").glob(f"{idx}_*@*.parquet"))
        if not files:
            continue
        df = pd.read_parquet(files[-1], columns=["con_code", "trade_date"])
        snap = max(snap or "", str(df["trade_date"].astype(str).max()))
        if code6 in set(df["con_code"].astype(str).str[:6]):
            names.append(name)
    return names, snap


def _recent_events(code6: str, curr_date: str, lake: Path, days: int = 60) -> tuple[list[str], bool]:
    cur = curr_date.replace("-", "")[:8]
    since = (dt.datetime.strptime(cur, "%Y%m%d").date() - dt.timedelta(days=days)).strftime("%Y%m%d")
    files = sorted((lake / "csindex_rebalance_detail").glob("*.parquet"))
    lines, any_recent = [], False
    for f in files:
        df = pd.read_parquet(f)
        if df.empty:
            continue
        pub = str(df["publish_date"].iloc[0])
        if not (since <= pub <= cur):
            continue
        any_recent = True
        eff, kind = parse_effective_date(str(df["content_text"].iloc[0] or ""))
        eff_txt = f"{_fmt(eff)} {'收盘' if kind == 'after_close' else '起'}生效" if eff else "生效日待公告"
        mine = df[(df["code"].astype(str) == code6) & df["index_code"].astype(str).str[:6].isin(INDEX_WHITELIST)]
        for r in mine.itertuples(index=False):
            lines.append(f"{_fmt(pub)} 公告 {INDEX_WHITELIST[str(r.index_code)[:6]]} {'调入' if r.side == 'add' else '调出'},{eff_txt}")
    return lines, any_recent


def index_membership_lines(code6: str, curr_date: str, *, lake_root: Path | None = None) -> str:
    """该票的「指数成分」+「调样事件(近 60 日)」两行(markdown),供 stock-research full 档日历块嵌入。

    presence-gated:成分快照缺席 → 明说"湖内无",不是"不属于";事件源缺席(近 60 日无任何公告快照,
    连别的票的都没有)→ 明说"源无近 60 日快照",与"源可达但这只票这段时间没有事件"区分开——后者
    才是"近 60 日无调样事件"。三种世界不能被同一句话吞掉。
    """
    lake = Path(lake_root) if lake_root else ws.lake_root()
    names, snap = _membership(code6, lake)
    if snap is None:
        m = "**指数成分**:指数成分快照:湖内无(未取数)。"
    elif names:
        m = f"**指数成分**:当前属于 {' · '.join(names)}(最新月末快照 {snap})。"
    else:
        m = f"**指数成分**:当前不属于六大指数(快照 {snap})。"
    lines, any_recent = _recent_events(code6, curr_date, lake)
    if lines:
        e = "**调样事件(近 60 日)**:" + ";".join(lines) + "。事实日期非方向;生效前夜买入历史隔夜为负(spec 2026-09-25 §1)。"
    elif any_recent:
        e = "**调样事件(近 60 日)**:近 60 日无调样事件(源可达)。"
    else:
        e = "**调样事件(近 60 日)**:调样事件:源无近 60 日快照。"
    return m + "\n\n" + e

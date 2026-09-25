#!/usr/bin/env python3
"""stock-research full 档的确定性「指数成分 / 调样事件」行(design 2026-09-25 §2.6)。

替代 `blocks_ashare` 里「指数调样 → WebSearch 补」的兜底。**只读湖、零网络**:成分看 `lake/index_weight/<idx>@*.parquet`
最新一份,事件看 `lake/csindex_rebalance_detail/*.parquet` 近 60 日命中,「源可达」看 `lake/csindex_rebalance_list/*.parquet`
近 60 日有没有快照(fix round 1 #2,2026-09-25 review;详见 `_list_snapshot_dates`)。缺什么就写缺什么,不编。

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


def _list_snapshot_dates(lake: Path) -> list[str]:
    """`csindex_rebalance_list` 快照日期(文件名 `<entity>@<date>.parquet` 的 `<date>` 段)。

    fix round 1 #2(2026-09-26,coordinator review):这是"公告源本身有没有被查过"的信号,
    与任何一条公告是否命中了某只票无关。`harvest_index_events` 每次跑动都会落一份新的 list
    快照——即使当天没有真实调样(六指数半年/季度才调一次样,绝大多数日子本就该是空)。用它
    (而不是 `csindex_rebalance_detail` 里有没有文件)判断"源可达",才能把"安静期"与
    "从没跑过 harvest"分开——这两者此前会渲染成同一句话,是这个仓库反复踩过的
    「缺席 ≠ 否」变种:`csindex_rebalance_detail` 只在真有公告时才落盘,它的缺席从不能
    单独证明"没查过",只能证明"没有公告"或"没查过"这两者之一,而读者需要知道是哪一个。
    """
    out = []
    for f in sorted((lake / "csindex_rebalance_list").glob("*.parquet")):
        _, _, tail = f.stem.partition("@")
        if tail:
            out.append(tail)
    return out


def _recent_events(code6: str, curr_date: str, lake: Path, days: int = 60) -> tuple[list[str], bool]:
    cur = curr_date.replace("-", "")[:8]
    since = (dt.datetime.strptime(cur, "%Y%m%d").date() - dt.timedelta(days=days)).strftime("%Y%m%d")
    # "源有没有被查过"看 list 快照(见 _list_snapshot_dates docstring),不是看是否命中过任何一条
    # 公告——即便下面一条 detail 都不落,只要 list 快照落在窗内,源就是可达的,只是这段时间安静。
    any_recent = any(since <= d <= cur for d in _list_snapshot_dates(lake))
    lines = []
    for f in sorted((lake / "csindex_rebalance_detail").glob("*.parquet")):
        df = pd.read_parquet(f)
        if df.empty:
            continue
        pub = str(df["publish_date"].iloc[0])
        if not (since <= pub <= cur):
            continue
        eff, kind = parse_effective_date(str(df["content_text"].iloc[0] or ""))
        eff_txt = f"{_fmt(eff)} {'收盘' if kind == 'after_close' else '起'}生效" if eff else "生效日待公告"
        mine = df[(df["code"].astype(str) == code6) & df["index_code"].astype(str).str[:6].isin(INDEX_WHITELIST)]
        for r in mine.itertuples(index=False):
            lines.append(f"{_fmt(pub)} 公告 {INDEX_WHITELIST[str(r.index_code)[:6]]} {'调入' if r.side == 'add' else '调出'},{eff_txt}")
    return lines, any_recent


def index_membership_lines(code6: str, curr_date: str, *, lake_root: Path | None = None) -> str:
    """该票的「指数成分」+「调样事件(近 60 日)」两行(markdown),供 stock-research full 档日历块嵌入。

    presence-gated:成分快照缺席 → 明说"湖内无",不是"不属于"。事件行三态互不合并(fix round 1 #2,
    2026-09-25 review):list 快照证明"源近 60 日被查过"这件事**独立于**是否有公告命中这只票——
    consulted+命中("XX 公告 调入/调出…")、consulted+窗内无命中("近 60 日无调样事件(源可达)",
    六指数半年/季度才调一次样,这是正常的安静期)、从未 consulted("调样事件:源无近 60 日快照")。
    第三态不能靠"detail 里有没有这只票的文件"来判定——那只能证明"没有公告"或"没查过"之一,
    分不清是哪个;必须靠 list 快照(每次跑动都会留一份,不管当天有没有真事件)。
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

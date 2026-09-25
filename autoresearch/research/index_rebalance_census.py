#!/usr/bin/env python3
"""指数调样事件 · 隔夜尺普查(design 2026-09-25 附录 A 的可复现版;**只读**,不进生产)。

问题:扫描日 D 落在一次指数调样的哪一段,调入/调出票在主尺 `gap_c1_o2`(D+1 收盘买 → D+2 开盘卖)上
相对**同指数未变动成分股**是正是负?

口径(预注册,跑前锁死):
  事件日按规则推:生效 E = 6/12 月第二个周五(科创50 另 3/9 月),公告 A = E − 14 天;
  调入/调出 = tushare `index_weight` 相邻月末快照之差(经 cache,一指数一月一份);
  收益 = `lake/daily`;对照 = 同指数未变动成分股当日等权 gap;
  相位:0.A-2 / 1.A-1(预测夜)/ 2.A(公告夜)/ 3.run(A+1..E-3)/ 4.E-2 / 5.E-1(买 E 收 → 卖 E+1 开)/ 6.E / 7.post(E+1..E+3);
  t_cell 按(调样, 指数)格聚合(股票行不是独立样本)。
边界:月末快照含临时调整污染;未扣成本;单一大周期。读数只回答「守卫该不该开」,不回答「能不能赚」。

截断可见性(Task 1 遗留发现,2026-09-25 落地):`index_weight` 契约(`autoresearch/data/contracts.py`)
只有 `required_cols`、没有 `min_rows` 下限——一份读到一半的成分股页(300 只读到 150 只)会正常通过
校验、正常入湖。本普查逐月做差,一份半截页会读成一堆假调入/假调出,形状与真事件完全一样,无法靠
后续统计反推。本模块**不**在此加契约下限(那不是本任务的范围),而是让截断在产物里可见:每次事件
的两侧月末快照行数随观测一起进 `snapshot_sizes`,并按该指数的固定样本量(300/500/1000/…)标一个
`suspect` 位——**只标记,不剔除、不改写任何统计**。见到「可疑」就自作主张丢观测是另一种更隐蔽的
静默失真(本仪器不越权替人判断「这份数据能不能用」);同时「源不可达」(两份快照都是空)与
「有数据但可疑地少」是两个不同的世界,不能编码成同一个值(`included` 与 `suspect` 分开记)。

窗口连续性可见性(fix round 1 #3,2026-09-25 落地):相位是**按位置**算的——`lake_trade_days()`
自己的 docstring 明说它只列现存 parquet、不查真实交易日历。湖内窗口中间缺一天,后面每个相位的
offset 会整体挪位(真实的 E−3 被读成 4.E-2),不报错、不留痕。本模块同样**不**在此把它变成门
(修复正确性超出本任务范围),而是把每次事件窗口的湖内天数与 tushare `trade_cal` 的真实交易日历
天数并排摆进 `calendar_gaps`:**只可见,不挡**。日历取不到(`calendar_fetch` 缺省不查,或查失败)
时 `calendar_days`/`shortfall` 记 `None`——「没检查」不能读成「检查过=0 缺口」。

  uv run --no-sync python -m autoresearch.research.index_rebalance_census --start 2022-06 --end 2026-06
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.data.market_panel import lake_trade_days
from autoresearch.scan.index_events import second_friday

INDEXES: dict[str, str] = {"000300.SH": "沪深300", "000905.SH": "中证500", "000852.SH": "中证1000",
                           "000510.SH": "中证A500", "000688.SH": "科创50", "399006.SZ": "创业板指"}
QUARTERLY = {"000688.SH"}
PHASES = ("0.A-2", "1.A-1", "2.A", "3.run", "4.E-2", "5.E-1", "6.E", "7.post")
POST = 3

# 六指数的固定样本量(编制规则明文规定,不是估出来的)——只用来给 snapshot_sizes 打一个「可疑地
# 少」的可见性标记,从不参与统计口径本身。低于该指数样本量 90% 记为 suspect;90% 留足了余量:
# 正常月份的调样只动个位数只,不会让总行数掉出这条线,而「读到一半」(如 300→150)远低于它。
_NOMINAL_SIZE: dict[str, int] = {"沪深300": 300, "中证500": 500, "中证1000": 1000,
                                 "中证A500": 500, "科创50": 50, "创业板指": 100}
_SIZE_FLOOR_RATIO = 0.9


def _default_fetch():
    return None                     # None → cache.get_or_fetch 走 sources.fetch(真 tushare)


def _production_calendar_fetch(endpoint: str, params: dict):
    """`main()` 的生产默认 `calendar_fetch`:经统一取数门面直连,不经 `cache.get_or_fetch`。

    `trade_cal` 已登记的 `key="static"` 只认一份全局快照(`_cache_key` 对该 kind 忽略全部
    params),与本模块「按 (start_date, end_date) 窗口查」的用法天然不匹配——真走 cache 会把
    第一次调用的窗口钉成永远的"static"答案,污染后来要别的窗口的调用方。`autoresearch.scan.
    index_events.trading_days_window` / `autoresearch.scan.exec_anchor.trading_sessions` 同样
    为此绕开 cache、直连取数门面,本函数照做。
    """
    from autoresearch.data.sources import fetch as sources_fetch

    return sources_fetch(endpoint, params)


def _calendar_trading_days(start: str, end: str, fetch=None) -> list[str] | None:
    """`start`~`end`(含)的真实交易日历(SSE,`is_open=1`),供 `calendar_gaps` 当地基。

    `fetch=None`(缺省)→ **不查**,返回 `None`——调用方据此知道"没检查",不是"检查过=0 缺口"
    (缺席 ≠ 否,08-29/09-25 两次撞过的坑)。取数失败/返回形状不对 → 同样降级为 `None`,可见
    但不阻断:这条检查从头到尾都是一份读数,不是门。
    """
    if fetch is None:
        return None
    try:
        df = fetch("trade_cal", {"exchange": "SSE", "start_date": start, "end_date": end, "is_open": "1"})
    except Exception:
        return None
    if df is None or df.empty or "cal_date" not in df.columns:
        return None
    return sorted(df["cal_date"].astype(str).tolist())


def _month_iter(start: str, end: str):
    y, m = int(start[:4]), int(start[5:7])
    ye, me = int(end[:4]), int(end[5:7])
    while (y, m) <= (ye, me):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def rebalance_events(start: str, end: str, indexes: dict[str, str]) -> list[dict]:
    """[{A, E, index_codes}]:6/12 月全体;3/9 月只季度指数。"""
    out = []
    for y, m in _month_iter(start, end):
        if m in (6, 12):
            codes = list(indexes)
        elif m in (3, 9):
            codes = [c for c in indexes if c in QUARTERLY]
        else:
            continue
        if not codes:
            continue
        e = second_friday(y, m)
        a = (dt.datetime.strptime(e, "%Y%m%d").date() - dt.timedelta(days=14)).strftime("%Y%m%d")
        out.append({"A": a, "E": e, "index_codes": codes})
    return out


def constituents(index_code: str, year: int, month: int, fetch=None) -> tuple[set[str], int]:
    """该指数该月月末快照的成分集 + 该快照原始行数(经湖:键 <index_code>@<月末>,一指数一月一份)。

    行数随集合一起返回,不在这里丢掉——调用方要能看见一份快照是「完整的 300 行」还是「可疑的
    150 行」,而不是只拿到差集后的产物。契约没有 min_rows 下限,半截页会正常走到这里。
    """
    import calendar as _cal

    from autoresearch.data import cache
    start = f"{year}{month:02d}01"
    end = f"{year}{month:02d}{_cal.monthrange(year, month)[1]:02d}"
    df = cache.get_or_fetch("index_weight", {"index_code": index_code, "start_date": start, "end_date": end},
                            today=end, fetch=fetch)
    if df is None or df.empty:
        return set(), 0
    last = df["trade_date"].astype(str).max()
    snap = df[df["trade_date"].astype(str) == last]
    return set(snap["con_code"].astype(str)), int(len(snap))


def _phase(i: int, i_a: int, i_e: int) -> str | None:
    oa, oe = i - i_a, i - i_e
    if oa == -2:
        return "0.A-2"
    if oa == -1:
        return "1.A-1"
    if oa == 0:
        return "2.A"
    if oe <= -3:
        return "3.run"
    if oe == -2:
        return "4.E-2"
    if oe == -1:
        return "5.E-1"
    if oe == 0:
        return "6.E"
    if 1 <= oe <= POST:
        return "7.post"
    return None


def _load_pivots(lake_daily: Path | None, days: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = Path(lake_daily) if lake_daily else ws.lake_root() / "daily"
    frames = []
    for day in days:
        fp = d / f"{day}.parquet"
        if fp.exists():
            f = pd.read_parquet(fp, columns=["ts_code", "open", "close"])
            f["trade_date"] = day
            frames.append(f)
    px = pd.concat(frames, ignore_index=True)
    op = px.pivot(index="trade_date", columns="ts_code", values="open").sort_index()
    cl = px.pivot(index="trade_date", columns="ts_code", values="close").sort_index()
    return op, cl


def _stats(obs: pd.DataFrame) -> dict:
    out = {}
    for ph, g in obs.groupby("phase"):
        n = len(g)
        mean = float(g.exc.mean() * 100)
        t_obs = float(mean / (g.exc.std(ddof=1) * 100 / np.sqrt(n))) if n > 2 and g.exc.std(ddof=1) > 0 else None
        cells = g.groupby(["E", "index"]).exc.mean()
        t_cell = (float(cells.mean() / (cells.std(ddof=1) / np.sqrt(len(cells))))
                  if len(cells) > 2 and cells.std(ddof=1) > 0 else None)
        out[ph] = {"n": int(n), "n_cells": int(len(cells)), "exc_pp": round(mean, 3),
                   "hit_pct": round(float((g.exc > 0).mean() * 100), 1),
                   "t_obs": None if t_obs is None else round(t_obs, 2),
                   "t_cell": None if t_cell is None else round(t_cell, 2)}
    return out


def run_census(start: str = "2022-06", end: str = "2026-06", *, lake_daily: Path | None = None,
               fetch=None, indexes: dict[str, str] | None = None, calendar_fetch=None) -> dict:
    indexes = indexes or INDEXES
    days = lake_trade_days(lake_daily)
    op, cl = _load_pivots(lake_daily, days)
    tds = list(op.index)
    pos = {d: i for i, d in enumerate(tds)}
    gap = (op.shift(-2) / cl.shift(-1) - 1.0).replace([np.inf, -np.inf], np.nan)
    cal_days = _calendar_trading_days(tds[0], tds[-1], calendar_fetch) if tds else None
    obs: list[dict] = []
    events_out: list[dict] = []
    snapshot_sizes: list[dict] = []
    calendar_gaps: list[dict] = []
    for ev in rebalance_events(start, end, indexes):
        if ev["A"] not in pos or ev["E"] not in pos:
            continue
        i_a, i_e = pos[ev["A"]], pos[ev["E"]]
        lo, hi = max(i_a - 2, 0), min(i_e + POST, len(tds) - 1)
        window_days = tds[lo:hi + 1]
        if cal_days is None:
            cal_n, shortfall = None, None
        else:
            cal_n = sum(1 for d in cal_days if window_days[0] <= d <= window_days[-1])
            shortfall = cal_n - len(window_days)
        calendar_gaps.append({"A": ev["A"], "E": ev["E"], "window_start": window_days[0],
                              "window_end": window_days[-1], "lake_days": len(window_days),
                              "calendar_days": cal_n, "shortfall": shortfall})
        y, m = int(ev["E"][:4]), int(ev["E"][4:6])
        py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
        names = []
        for code in ev["index_codes"]:
            name = indexes[code]
            prev, n_prev = constituents(code, py, pm, fetch)
            cur, n_cur = constituents(code, y, m, fetch)
            included = bool(prev) and bool(cur)
            nominal = _NOMINAL_SIZE.get(name)
            suspect = bool(included and nominal is not None
                          and (n_prev < nominal * _SIZE_FLOOR_RATIO or n_cur < nominal * _SIZE_FLOOR_RATIO))
            snapshot_sizes.append({"E": ev["E"], "index": name, "prev_n": n_prev, "cur_n": n_cur,
                                   "included": included, "suspect": suspect})
            if not included:
                continue
            names.append(name)
            unchanged = [c for c in (prev & cur) if c in gap.columns]
            for side, codes in (("add", cur - prev), ("drop", prev - cur)):
                for i in range(i_a - 2, min(i_e + POST + 1, len(tds))):
                    if i < 0:
                        continue
                    ph = _phase(i, i_a, i_e)
                    if ph is None:
                        continue
                    day = tds[i]
                    ctrl = gap.loc[day, unchanged].mean() if unchanged else np.nan
                    for c in codes:
                        if c not in gap.columns or pd.isna(gap.at[day, c]) or pd.isna(ctrl):
                            continue
                        obs.append({"E": ev["E"], "index": name, "side": side, "phase": ph,
                                    "code": c, "exc": float(gap.at[day, c] - ctrl)})
        events_out.append({"A": ev["A"], "E": ev["E"], "indexes": names})
    df = pd.DataFrame(obs, columns=["E", "index", "side", "phase", "code", "exc"])
    by_index = {name: {side: _stats(df[(df["index"] == name) & (df.side == side)])
                       for side in ("add", "drop")} for name in sorted(df["index"].unique())}
    return {"start": start, "end": end, "events": events_out, "n_obs": int(len(df)),
            "tables": {side: _stats(df[df.side == side]) for side in ("add", "drop")},
            "by_index": by_index, "snapshot_sizes": snapshot_sizes, "calendar_gaps": calendar_gaps}


def render(doc: dict) -> str:
    lines = [f"# 指数调样事件 · 隔夜尺普查({doc['start']} → {doc['end']},{len(doc['events'])} 次调样,{doc['n_obs']} 票日)",
             "", "主尺 gap_c1_o2;对照 = 同指数未变动成分股当日等权;t_cell 按(调样, 指数)格聚合。",
             "边界:月末快照含临时调整污染;**未扣成本**;单一大周期;事件日按规则推。", ""]
    for side in ("add", "drop"):
        lines += [f"## {'调入' if side == 'add' else '调出'}票", "",
                  "| 相位 | n | 格 | 超额 pp | 胜率 | t_obs | t_cell |", "|---|---|---|---|---|---|---|"]
        for ph in PHASES:
            s = doc["tables"][side].get(ph)
            if s:
                lines.append(f"| {ph} | {s['n']} | {s['n_cells']} | {s['exc_pp']:+.2f} | {s['hit_pct']:.0f}% | "
                             f"{s['t_obs']} | {s['t_cell']} |")
        lines.append("")
    lines.append("## 分指数(调入票 5.E-1 / 1.A-1)")
    lines.append("")
    for name, sides in doc["by_index"].items():
        e1, a1 = sides["add"].get("5.E-1"), sides["add"].get("1.A-1")
        lines.append(f"- {name}:E−1 {e1['exc_pp']:+.2f}pp(胜 {e1['hit_pct']:.0f}%,n={e1['n']})" if e1 else f"- {name}:E−1 无观测")
        if a1:
            lines[-1] += f";A−1 {a1['exc_pp']:+.2f}pp(胜 {a1['hit_pct']:.0f}%)"
    lines.append("")
    lines.append("## 月末快照行数(截断可见性)")
    lines.append("")
    lines.append("`index_weight` 契约只校验必需列,没有行数下限(Task 1 遗留发现);半截页会正常通过校验、")
    lines.append("正常入湖。下表把每次事件两侧快照的原始行数摆出来,`标记` 列按该指数的固定样本量给")
    lines.append("「可疑地少」打 ⚠——**只标记,不剔除、不改写任何统计**;`计入统计` = 否表示两侧快照有")
    lines.append("一侧完全没有数据(源不可达),与「有数据但可疑地少」是两个不同的世界,分列显示。")
    lines.append("")
    lines.append("| E | 指数 | 调前月行数 | 调后月行数 | 计入统计 | 标记 |")
    lines.append("|---|---|---|---|---|---|")
    for s in doc.get("snapshot_sizes", []):
        included = "是" if s["included"] else "否(源不可达)"
        flag = "⚠ 疑似截断" if s.get("suspect") else ""
        lines.append(f"| {s['E']} | {s['index']} | {s['prev_n']} | {s['cur_n']} | {included} | {flag} |")
    lines.append("")
    lines.append("## 调样窗口交易日连续性(可见性,非门)")
    lines.append("")
    lines.append("`lake_trade_days()` 只列现存 parquet,不查真实交易日历;窗口中间缺一天,后面每个相位")
    lines.append("会整体挪位、不报错。下表把每次事件窗口的湖内天数与 `trade_cal` 真实交易日历天数并排")
    lines.append("列出——`日历天数`=未检查 表示没查(不是「查过=0 缺口」);**只标记,不改写任何统计**。")
    lines.append("")
    lines.append("| A | E | 窗口 | 湖内天数 | 日历天数 | 缺口 |")
    lines.append("|---|---|---|---|---|---|")
    for g in doc.get("calendar_gaps", []):
        cal_disp = "未检查" if g["calendar_days"] is None else str(g["calendar_days"])
        if g["shortfall"] is None:
            short_disp = ""
        elif g["shortfall"] > 0:
            short_disp = f"⚠ 缺{g['shortfall']}日"
        else:
            short_disp = "0"
        lines.append(f"| {g['A']} | {g['E']} | {g['window_start']}~{g['window_end']} | {g['lake_days']} | "
                     f"{cal_disp} | {short_disp} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="指数调样事件隔夜尺普查(只读;不写生产)")
    ap.add_argument("--start", default="2022-06")
    ap.add_argument("--end", default="2026-06")
    ap.add_argument("--lake-daily", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--index", action="append", default=None, help="CODE=名,可重复;缺省六指数")
    a = ap.parse_args(argv)
    indexes = dict(x.split("=", 1) for x in a.index) if a.index else None
    doc = run_census(start=a.start, end=a.end, lake_daily=Path(a.lake_daily) if a.lake_daily else None,
                     fetch=_default_fetch(), indexes=indexes, calendar_fetch=_production_calendar_fetch)
    out = Path(a.out) if a.out else ws.reports_root() / "research" / "index_rebalance_census.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(doc), encoding="utf-8")
    (out.parent / "_index_rebalance_census.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"[done] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

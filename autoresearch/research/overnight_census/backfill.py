#!/usr/bin/env python3
"""隔夜集中信号普查 · 一次性回填 —— 本包**唯一联网**的模块(不接线、不回注、不改生产代码)。

设计稿 §4.2「Stage 0 数据可行性与回填清单」:
`docs/specs/2026-08-28-overnight-concentrated-signal-census-design.md`。
接口契约由主会话钉死(`backfill_all` / `coverage_report` / `TABLES` 签名不得单方面更改)。

## 立案事实(2026-08-28 只读盘点,**不是转述**)

`lake/daily` **1091** 个分区(20220302..20260827)完整 —— 本模块的交易日轴真值源。
`daily_basic / top_inst / block_trade / moneyflow / cyq_perf` 各 **464** 个分区、`hk_hold` **463**,
20220601..20260805 只有 **45.7%** 覆盖(464/1015 交易日),缺失段几乎全是「连续 2 天」的稀疏
采样,2022 年仅 25% —— 这是**未采**,不是真实空。`limit_list_d` 只有 **156** 个分区(20251231
起)。`top_list` 只 19 天且不可靠 → **本模块不回填、普查不使用**(F2 走 `top_inst`)。

不补齐就判读 = 在一个非随机子样本上判读:被采到的日子恰好是历史上跑过扫描的日子。

## 端点探针(2026-08-28 真实调用,每个端点最少次数)

| 端点 | 探针 | 结果 |
|---|---|---|
| `limit_list_d` | `trade_date=20250715` / `20220302` | ✅ 81 / 77 行,**18 列全列** |
| `disclosure_date` | `end_date=20250630` | ✅ 5437 行;`limit=3000` 分页 → 3000/2437/0 |
| `share_float` | `start_date=20250701 end_date=20250731` | ✅ **单月 15 页 85,133 行**(必须分页) |
| `dividend` | `ex_date=` 20250715/20250710/20240705/20230706/20220708 | ✅ 36/91/108/67/83 行 |

四个端点**权限全通**,无一 BLOCKED(故 `TABLES` 里没有 `available=False` 的表)。
另探 6 张逐日表在 `trade_date=20220302`(缺失段最早一天):`daily_basic` 4724 行 / `top_inst`
870 / `block_trade` 410 / `moneyflow` 4639 / `hk_hold` 3051 / `limit_list_d` 77 —— **历史全部
可回填**,缺口确系未采。

## 三个必须写进代码的判例

1. **全列落盘,不传 `fields`**(`data/cache.py::_lake_params` 的窄表毒化判例):cache key 不含
   `fields`,一次少列写入会把那一天钉成窄表,后来的读者静默拿到整组 NaN。本模块任何请求
   **都不带 `fields`**;需要窄列的读者自己 `df[cols]`。
   ⚠️ 湖里既有的 464 个分区正是窄表(`daily_basic` 只有 9 列 / `moneyflow` 只有 8 列 /
   `hk_hold` 只有 2 列,且**都缺 `trade_date`**);本模块回填的新分区是全列(18/20/7 列)。
   同一张表因此会**混合两种 schema** —— 下游按列读时必须容忍缺列,不能假定全湖同构。
   本模块不改写既有分区(重写生产湖不是研究仪器该做的事),只把事实报出来。
2. **空结果也落盘**,否则每次重跑都把空日重拉一遍(`cache.py`「存在 == 取过且为空」);
   但**失败(异常)绝不落空 parquet** —— 那会把一次网络抖动永久钉成「这天没有数据」。
3. **原子写**(tmp → fsync → `os.replace`):设计稿 §4.1 明确点名不得复用
   `derivatives_census._save` 的裸 `to_parquet`(非原子)。中断只会留 `.tmp`,不留半截分区。

## 未来窗不回填(刻意)

设计稿 §4.2 给 `share_float` 写的是「2022-03 → 2026-12(**含未来解禁**)」。本模块默认
**不越过 `until`**:未来月份的解禁记录会随新公告持续增长,而断点续传按「文件存在即跳过」,
一旦把半成品落盘就永久钉死(同族坑:tushare 盘后灌数窗口写到一半的快照)。要覆盖未来解禁,
显式传 `until="20261231"` —— `until` 只影响 `share_float` 的月窗与 `dividend`/逐日表的上界
(后两者仍被湖里的交易日截住),由调用方为「这个月已经稳定」负责。
同理 `disclosure_date` 的报告期上界取 `min(20260630, until)`,不给尚未开始披露的报告期落空盘。

## 调用量与耗时(2026-08-28 实测外推,不是拍的)

以 1091 个湖交易日、回填前的分区数(`limit_list_d` 156 / 五张 464 / `hk_hold` 463)算:

| 表 | 键数 | 每键页数 | 调用数 |
|---|---|---|---|
| 6 张逐日表 | 6546 | 1 | **4071**(=6546−2475 已有) |
| `disclosure_date` | 19 报告期 | 2~3(实测 3000/2437 → 2) | ≈ 40~57 |
| `share_float` | 54 月 | **≈15**(实测单月 85,133 行) | ≈ **810** |
| `dividend` | 1091 日 | 1(单日 ≤110 行) | **1091** |
| 合计 | | | **≈ 6000** |

单调用实测 ≈0.9~1.1 s(含 `sleep=0.2`);`share_float` 满页 ≈1.6 s/页。串行约
**90~110 分钟**;tushare 5000 积分档每分钟 200 次的限频本身就给了 30 分钟地板。
**必须后台跑,别用会 pkill 长命令的壳**(haiku bash 壳判例)。

用法:
  uv run --no-sync python -c "from autoresearch.research.overnight_census import backfill as bf; \
      print(bf.backfill_all())"
覆盖体检(零网络):`bf.coverage_report()`。
"""
from __future__ import annotations

import calendar
import os
import sys
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from autoresearch.common import workspace as ws
from autoresearch.research.edge_census import lake_trade_days

# ───────────────────────── 常量(跑前锁死)─────────────────────────

SINCE_DEFAULT = "20220302"          # = lake/daily 起点,交易日轴真值源的第一天
DISCLOSURE_FIRST_PERIOD = "20211231"   # 2021Q4:D=20220302 时最近一份年报计划已在披露中
DISCLOSURE_LAST_PERIOD = "20260630"    # 2026Q2(设计稿 §4.2 表)
PROGRESS_EVERY = 50                 # 默认进度行的间隔(长任务必须能看见活着)
MAX_PAGES = 400                     # 分页硬上限:offset 若被端点忽略会无限翻页,宁可报错
_COMPRESSION = "zstd"               # 与 data/cache.py::_COMPRESSION 一致(同表续接)

# 探针实测的单页上限(文档写的 3000/6000 与实测一致;dividend 单日 ≤110 行,不分页)。
DISCLOSURE_PAGE = 3000
SHARE_FLOAT_PAGE = 6000

# 表 → 落点 / 键法 / 抓取方式。`dest` 与 `key` 两个字段逐字保留契约表的文本。
# `available=False` 表示探针实测权限不足 → `backfill_all` 跳过并在返回里说明(当前无此表)。
TABLES: dict[str, dict] = {
    "limit_list_d": {
        "kind": "daily", "endpoint": "limit_list_d", "available": True,
        "dest": "lake/limit_list_d/<YYYYMMDD>.parquet", "key": "逐交易日 trade_date=",
    },
    "daily_basic": {
        "kind": "daily", "endpoint": "daily_basic", "available": True,
        "dest": "lake/daily_basic/<YYYYMMDD>.parquet", "key": "逐交易日",
    },
    "top_inst": {
        "kind": "daily", "endpoint": "top_inst", "available": True,
        "dest": "lake/top_inst/<YYYYMMDD>.parquet", "key": "逐交易日",
    },
    "block_trade": {
        "kind": "daily", "endpoint": "block_trade", "available": True,
        "dest": "lake/block_trade/<YYYYMMDD>.parquet", "key": "逐交易日",
    },
    "moneyflow": {
        "kind": "daily", "endpoint": "moneyflow", "available": True,
        "dest": "lake/moneyflow/<YYYYMMDD>.parquet", "key": "逐交易日",
    },
    "hk_hold": {
        "kind": "daily", "endpoint": "hk_hold", "available": True,
        "dest": "lake/hk_hold/<YYYYMMDD>.parquet", "key": "逐交易日",
    },
    "disclosure_date": {
        "kind": "event", "endpoint": "disclosure_date", "available": True,
        "page": DISCLOSURE_PAGE,
        "dest": "lake/events/disclosure_date/<end_date>.parquet",
        "key": "逐报告期 end_date=,分页 limit/offset",
    },
    "share_float": {
        "kind": "event", "endpoint": "share_float", "available": True,
        "page": SHARE_FLOAT_PAGE,
        "dest": "lake/events/share_float/<YYYYMM>.parquet",
        "key": "逐月 start_date/end_date(**必须分页**:实测单月 15 页)",
    },
    "dividend": {
        "kind": "event", "endpoint": "dividend", "available": True,
        "dest": "lake/events/dividend/<YYYYMMDD>.parquet", "key": "逐日 ex_date=",
    },
}

DAILY_TABLES = tuple(t for t, s in TABLES.items() if s["kind"] == "daily")
EVENT_TABLES = tuple(t for t, s in TABLES.items() if s["kind"] == "event")


class BackfillError(RuntimeError):
    """回填口径违约 —— 宁可少一天,不要一个来路不明的分区。"""


# ───────────────────────── 路径 / 键 ─────────────────────────


def _root(lake_root: Path | str | None = None) -> Path:
    """湖根 —— 唯一事实源 `common.workspace.lake_root()`(测试传 tmp_path 覆盖)。"""
    return Path(lake_root) if lake_root is not None else ws.lake_root()


def _ymd(value) -> str:
    return str(value).replace("-", "")[:8]


def partition_path(table: str, key: str, lake_root: Path | str | None = None) -> Path:
    """某表某键的落点。逐日表 `lake/<table>/<key>.parquet`(与既有分区同表续接);
    事件表 `lake/events/<table>/<key>.parquet`(新表,不与生产端点同名目录混住)。"""
    spec = TABLES.get(table)
    if spec is None:
        raise BackfillError(f"未登记的表:{table}")
    root = _root(lake_root)
    base = root / table if spec["kind"] == "daily" else root / "events" / table
    return base / f"{key}.parquet"


def trade_days(since: str | None = SINCE_DEFAULT, until: str | None = None, *,
               lake_root: Path | str | None = None) -> list[str]:
    """交易日清单 = `lake/daily/*.parquet` 的**文件名**(复用 `edge_census.lake_trade_days`)。

    **不查交易日历**:湖里没有的日子对本仪器就是不存在 —— 交易日历会告诉你 20220301 是
    交易日,但那天没有价格分区,回填出来的资金流/席位永远配不上一个 gap,只是白花调用额度。
    """
    days = lake_trade_days(_root(lake_root) / "daily")
    lo = _ymd(since) if since else ""
    hi = _ymd(until) if until else "99999999"
    return [d for d in days if lo <= d <= hi]


def quarter_ends(first: str = DISCLOSURE_FIRST_PERIOD,
                 last: str = DISCLOSURE_LAST_PERIOD) -> list[str]:
    """报告期清单(季末日 YYYYMMDD,含两端)。2021Q4..2026Q2 → 19 个。"""
    lo, hi = _ymd(first), _ymd(last)
    out = []
    for year in range(int(lo[:4]), int(hi[:4]) + 1):
        for mm, dd in (("03", "31"), ("06", "30"), ("09", "30"), ("12", "31")):
            day = f"{year}{mm}{dd}"
            if lo <= day <= hi:
                out.append(day)
    return out


def month_keys(first_ym: str, last_ym: str) -> list[str]:
    """月键清单 YYYYMM(含两端)。"""
    lo, hi = str(first_ym)[:6], str(last_ym)[:6]
    if lo > hi:
        return []
    out, y, m = [], int(lo[:4]), int(lo[4:6])
    while f"{y:04d}{m:02d}" <= hi:
        out.append(f"{y:04d}{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def month_span(ym: str) -> tuple[str, str]:
    """YYYYMM → (当月首日, 当月末日),两端都是 YYYYMMDD。"""
    y, m = int(str(ym)[:4]), int(str(ym)[4:6])
    return f"{y:04d}{m:02d}01", f"{y:04d}{m:02d}{calendar.monthrange(y, m)[1]:02d}"


# ───────────────────────── 落盘(原子;空帧也写,失败不写)─────────────────────────


def _save(df: pd.DataFrame, path: Path) -> None:
    """ZSTD parquet **原子写**:tmp → fsync → `os.replace`(同目录 rename)。

    设计稿 §4.1 点名不得复用 `derivatives_census._save` 的裸 `to_parquet`:那是非原子的,
    中断会留半截文件,而半截 parquet 在「文件存在即跳过」的续传里长得跟完成品一模一样。
    口径与 `data/cache.py::_atomic_write` 对齐(同压缩、同 `preserve_index=False`),
    但不 import 那个私有符号 —— 研究仪器不该把生产内部实现钉成自己的接口。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, tmp, compression=_COMPRESSION)
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


# ───────────────────────── 取数(可注入,测试零网络)─────────────────────────


def _resolve(pro=None, call=None):
    """→ (get_pro, call)。两者都**惰性**:全部命中续传时一次网络都不发生,也不 import tushare。"""
    if call is None:
        from autoresearch.data.tushare_source import _ts_call

        call = _ts_call
    holder = {"pro": pro}

    def get_pro():
        if holder["pro"] is None:
            from autoresearch.data.tushare_source import _pro as make

            holder["pro"] = make()
        return holder["pro"]

    return get_pro, call


def _fetch(get_pro, call, endpoint: str, kwargs: dict) -> pd.DataFrame:
    """一次调用。**kwargs 里永远没有 `fields`** —— 全列落盘是硬约束,不是默认值。"""
    if "fields" in kwargs:
        raise BackfillError(f"{endpoint} 请求带了 fields —— 窄表毒化判例禁止")
    df = call(lambda: getattr(get_pro(), endpoint)(**kwargs))
    return pd.DataFrame() if df is None else df


def _fetch_paged(get_pro, call, endpoint: str, kwargs: dict, *, page: int | None,
                 sleep: float) -> tuple[pd.DataFrame, int]:
    """`limit`/`offset` 翻页直到「返回行数 < limit」,各页 concat 后一次落盘 → (frame, 页数)。

    `page=None` → 单次调用(实测 `dividend` 单日 ≤110 行,没有分页必要)。
    翻到 `MAX_PAGES` 仍是满页 → 抛错:那说明 `offset` 没被端点接受,再翻下去只是把同一页
    抄 400 遍,而结果看起来完全正常。
    """
    if page is None:
        return _fetch(get_pro, call, endpoint, kwargs), 1
    frames, offset, pages = [], 0, 0
    while True:
        df = _fetch(get_pro, call, endpoint, {**kwargs, "limit": page, "offset": offset})
        pages += 1
        if len(df):
            frames.append(df)
        if len(df) < page:
            break
        if pages >= MAX_PAGES:
            raise BackfillError(f"{endpoint}{kwargs} 翻到 {pages} 页仍是满页 —— offset 可能无效")
        offset += page
        if sleep:
            time.sleep(sleep)
    if not frames:
        return pd.DataFrame(), pages
    return pd.concat(frames, ignore_index=True), pages


# ───────────────────────── 回填主体 ─────────────────────────


def _default_progress(event: dict) -> None:
    print(f"[oc-backfill] {event['table']} {event['done']}/{event['total']} @{event['key']} "
          f"fetched={event['fetched']} skipped={event['skipped']} "
          f"empty={event['empty']} failed={event['failed']}", file=sys.stderr, flush=True)


def _work_items(table: str, spec: dict, days: list[str], since: str | None,
                until: str | None) -> list[tuple[str, dict]]:
    """一张表的 (键, 请求参数) 清单。

    逐日表与 `dividend` 的键 = **湖交易日**(湖里没有的日子不存在);`share_float` 的键 =
    月窗(无湖日又无显式 `until` → 无锚,返回空,不猜)。**`disclosure_date` 是唯一不需要
    价格锚的表** —— 报告期是日历事实,湖空也照跑 2021Q4..min(2026Q2, until)。
    """
    if spec["kind"] == "daily":
        return [(d, {"trade_date": d}) for d in days]
    if table == "dividend":
        return [(d, {"ex_date": d}) for d in days]
    if table == "disclosure_date":
        hi = _ymd(until) if until else (days[-1] if days else DISCLOSURE_LAST_PERIOD)
        return [(p, {"end_date": p}) for p in quarter_ends(last=min(DISCLOSURE_LAST_PERIOD, hi))]
    if table == "share_float":
        lo_src = since if since else (days[0] if days else None)
        hi_src = until if until else (days[-1] if days else None)
        if lo_src is None or hi_src is None:
            return []                      # 湖里一天都没有 且没给显式窗 → 无锚,不猜
        lo, hi = _ymd(lo_src)[:6], _ymd(hi_src)[:6]
        out = []
        for ym in month_keys(lo, hi):
            start, end = month_span(ym)
            out.append((ym, {"start_date": start, "end_date": end}))
        return out
    raise BackfillError(f"未知事件表:{table}")


def backfill_all(*, since: str = SINCE_DEFAULT, until: str | None = None,
                 tables: list[str] | None = None, call=None, pro=None,
                 sleep: float = 0.12, progress=None) -> dict:
    """一次性回填 9 张表 → `{table: {"fetched","skipped","empty","failed","seconds",...}}`。

    - `call(fn)` / `pro` 可注入(测试零网络);缺省走 `data.tushare_source._ts_call` / `_pro`。
    - **断点续传**:目标文件已存在 → `skipped`,一次网络都不发。
    - **全列落盘,不传 `fields`**(窄表毒化判例;带了就抛 `BackfillError`)。
    - **空结果也落盘**并计 `empty`(`empty ⊆ fetched`,与 `derivatives_census._backfill_daily`
      同口径);**失败(异常)不落盘**、计 `failed`、键进 `failed_keys`,然后**继续下一个**
      —— 一个 403 不该毙掉剩下 5000 次调用。
    - `progress(event: dict)` 每 `PROGRESS_EVERY` 个键调一次(缺省打到 stderr);
      event = {table, key, done, total, fetched, skipped, empty, failed}。

    返回值每表额外带 `rows`(本次落盘行数)/ `pages`(本次翻页数)/ `failed_keys`
    (`[{"key","error"}]`)/ `keys`(计划键数),便于调用方对账「跑了多少 ≠ 该跑多少」。
    """
    lake = _root(None)
    names = list(TABLES) if tables is None else list(tables)
    unknown = [t for t in names if t not in TABLES]
    if unknown:
        raise BackfillError(f"未登记的表:{unknown} —— 加表要同改 TABLES 与测试")
    get_pro, call = _resolve(pro, call)
    on_progress = _default_progress if progress is None else progress
    days = trade_days(since, until, lake_root=lake)
    out: dict[str, dict] = {}

    for table in names:
        spec = TABLES[table]
        t0 = time.time()
        stat = {"fetched": 0, "skipped": 0, "empty": 0, "failed": 0, "rows": 0,
                "pages": 0, "failed_keys": [], "keys": 0, "seconds": 0.0}
        if not spec.get("available", True):
            stat["seconds"] = 0.0
            stat["unavailable"] = True
            stat["reason"] = spec.get("reason", "探针实测权限/积分不足 —— 本表整表跳过")
            out[table] = stat
            continue
        items = _work_items(table, spec, days, since, until)
        stat["keys"] = len(items)
        for i, (key, kwargs) in enumerate(items, start=1):
            fp = partition_path(table, key, lake)
            if fp.exists():
                stat["skipped"] += 1
            else:
                try:
                    df, pages = _fetch_paged(get_pro, call, spec["endpoint"], kwargs,
                                             page=spec.get("page"), sleep=sleep)
                except Exception as exc:  # noqa: BLE001 —— 单键失败不中断整批
                    stat["failed"] += 1
                    stat["failed_keys"].append(
                        {"key": key, "error": f"{type(exc).__name__}: {exc}"})
                else:
                    stat["pages"] += pages
                    if df.empty:
                        stat["empty"] += 1
                    _save(df, fp)             # 空帧也落:否则每次重跑都把空日重拉一遍
                    stat["fetched"] += 1
                    stat["rows"] += int(len(df))
                    if sleep:
                        time.sleep(sleep)
            if on_progress and (i % PROGRESS_EVERY == 0 or i == len(items)):
                on_progress({"table": table, "key": key, "done": i, "total": len(items),
                             **{k: stat[k] for k in ("fetched", "skipped", "empty", "failed")}})
        stat["seconds"] = round(time.time() - t0, 1)
        out[table] = stat
    return out


# ───────────────────────── 覆盖体检(零网络)─────────────────────────


def _partition_days(table: str, lake_root: Path | str | None) -> list[str]:
    spec = TABLES[table]
    root = _root(lake_root)
    base = root / table if spec["kind"] == "daily" else root / "events" / table
    if not base.exists():
        return []
    # `*.parquet` 天然滤掉中断留下的 `<key>.parquet.tmp`(它的后缀是 `.tmp`)——
    # 半截文件不算完成,续传下次会重取那个键。
    return sorted(p.stem for p in base.glob("*.parquet") if p.stem)


def coverage_report(tables: list[str] | None = None,
                    lake_root: Path | str | None = None) -> dict:
    """→ `{table: {"n_files","first","last","expected_days","coverage","missing_sample","kind"}}`。

    逐日表:`expected_days` = `lake/daily` 在该表 `[first,last]` 窗内的交易日数,
    `coverage` = 已有 / 应有,`missing_sample` = 缺日前 10 个。**分母来自湖,不是交易日历**
    —— 与 `trade_days` 同一真值源,否则「覆盖率」量的是两把不同的尺。
    事件表(`disclosure_date`/`share_float`/`dividend`)只报文件数与区间:它们的键不是交易日,
    没有「应有多少个」可言,`expected_days`/`coverage` 恒为 `None`(**不是 0**,不可读成没覆盖)。
    """
    names = list(TABLES) if tables is None else list(tables)
    unknown = [t for t in names if t not in TABLES]
    if unknown:
        raise BackfillError(f"未登记的表:{unknown}")
    axis = trade_days(None, None, lake_root=lake_root)
    out: dict[str, dict] = {}
    for table in names:
        spec = TABLES[table]
        stems = _partition_days(table, lake_root)
        first = stems[0] if stems else None
        last = stems[-1] if stems else None
        row = {"kind": spec["kind"], "n_files": len(stems), "first": first, "last": last,
               "expected_days": None, "coverage": None, "missing_sample": []}
        if spec["kind"] == "daily":
            expected = [d for d in axis if first is not None and first <= d <= last]
            have = set(stems)
            missing = [d for d in expected if d not in have]
            row["expected_days"] = len(expected)
            row["coverage"] = (
                (len(expected) - len(missing)) / len(expected) if expected else 0.0)
            row["missing_sample"] = missing[:10]
        out[table] = row
    return out

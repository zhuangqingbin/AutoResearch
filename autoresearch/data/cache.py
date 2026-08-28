"""Parquet 数据湖 —— 取一次永不重取(存在即命中)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §B。

  lake/<endpoint>/<key>.parquet   # ZSTD;key 由 policy 决定
  get_or_fetch(endpoint, params, today=None, fetch=None):
    live   → 总取新,绝不缓存。
    date   → 该交易日 < today(已结算)且文件存在 → 读 parquet 命中;否则拉;
             date >= today(盘中未结算)→ 拉新但不写;否则拉 + 原子写。
    其它   → 文件存在即命中;否则拉 + 原子写。
  空结果也写空 parquet:存在 == "取过且为空",避免反复重拉空端点。

原子写:写 `<path>.tmp` → os.replace(同目录 rename,原子),并发/中断不留半截文件。
lake 根 = 模块级 LAKE,测试 monkeypatch 成 tmp 目录,绝不污染真 context/lake/。
"""
from __future__ import annotations

import contextlib
import os
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from autoresearch.common import workspace as ws
from autoresearch.data.endpoints import policy

# 数据湖根目录(测试 monkeypatch 此常量以重定向到 tmp）。
LAKE = ws.lake_root()

_COMPRESSION = "zstd"

# 各 key 模式下,从 params 里找"日期/报告期/实体"用的候选键名(吸收 tushare/akshare 差异)。
_DATE_PARAM_KEYS = ("trade_date", "date", "ann_date", "cal_date")
_PERIOD_PARAM_KEYS = ("period", "date", "end_date")
_ENTITY_PARAM_KEYS = ("ts_code", "symbol", "code", "exchange_id", "exchange")


class _NoSourceTrace:
    """Zero-import no-op for ordinary cache calls outside a forensic run."""

    @staticmethod
    def finish_success(*args, **kwargs) -> bool:
        return False

    @staticmethod
    def finish_failure(*args, **kwargs) -> bool:
        return False


_NO_SOURCE_TRACE = _NoSourceTrace()


def _trace_warning() -> None:
    with contextlib.suppress(BaseException):
        os.write(2, b"source lineage evidence incomplete\n")


def _source_trace(endpoint: str, params: dict, today: str | None):
    if not str(os.environ.get("AUTORESEARCH_RUN_ID", "")).strip():
        return _NO_SOURCE_TRACE
    try:
        from autoresearch.trace.source_lineage import trace_access

        return trace_access(endpoint, params, today=today)
    except BaseException:
        _trace_warning()
        return _NO_SOURCE_TRACE


def _finish_source_success(trace, frame, access: str, path) -> None:
    try:
        trace.finish_success(frame, access, path)
    except BaseException:
        _trace_warning()


def _finish_source_failure(trace, error: BaseException) -> None:
    try:
        trace.finish_failure(error)
    except BaseException:
        _trace_warning()


# 快照型端点(`policy(...)["snapshot"]`)落盘时补的**观测出处**两列(Wave12 T2 Interfaces
# 逐字要求 `first_seen_basis="observed"`)。没有观测时刻,"这份分片到底什么时候抓的、是不是
# 半截、跟哪个交易日对齐"永久不可回答 —— 而快照数据事后无从复查(接口没有历史参数)。
#
# 列名/取值沿用本仓既有的 first_seen 词汇表(`autoresearch/news/catalog.py`:
# `BASIS_OBSERVED="observed"` / `BASIS_SNAPSHOT="snapshot_inferred"`)。这里恒为
# **observed**:值是我们**亲眼在那一刻抓到**的。事后拿旧分片的 mtime 反推只能算
# `snapshot_inferred`,两者不可混用 —— 隔离区里那个错标分区正因如此不能一改名了事。
# 不 import catalog 是刻意的:data 是最底层,不该反向依赖 news。
FIRST_SEEN_TS_COL = "first_seen_ts"
FIRST_SEEN_BASIS_COL = "first_seen_basis"
_BASIS_OBSERVED = "observed"


class SnapshotDateError(RuntimeError):
    """快照端点的 as-of 键 ≠ 真实今天 —— 拒绝把**今天**的观测写成过去某天的"历史"。

    快照接口只返回"此刻"(没有日期参数),所以任何"补跑 2026-08-07 的快照"在物理上都不成立:
    它拿到的是今天的内容,却会落成 `all@20260807.parquet`,**且事后不可甄别**
    (工作树里那个 mtime 08-09 03:03 的 08-07 分区正是这么来的)。
    """


def _real_today() -> str:
    """真实墙上时钟的今天(YYYYMMDD)——快照守门的唯一基准,测试 monkeypatch 此函数。"""
    return date.today().strftime("%Y%m%d")


def _stamp_observed(df: pd.DataFrame) -> pd.DataFrame:
    """给快照帧补观测出处两列(带时区的观测时刻 + `first_seen_basis="observed"`)。"""
    from datetime import datetime

    out = df.copy()
    out[FIRST_SEEN_TS_COL] = datetime.now().astimezone().isoformat(timespec="seconds")
    out[FIRST_SEEN_BASIS_COL] = _BASIS_OBSERVED
    return out


def _first(params: dict, keys) -> str | None:
    for k in keys:
        v = params.get(k)
        if v not in (None, ""):
            return str(v)
    return None


def _compact(d: str | None) -> str | None:
    """'2026-06-22' → '20260622';已是紧凑串则原样。None 透传。"""
    return d.replace("-", "") if d else d


def _today_compact(today: str | None) -> str:
    return _compact(today) if today else date.today().strftime("%Y%m%d")


def _cache_key(endpoint: str, params: dict, today: str) -> str | None:
    """按 policy 推 lake 文件名(不含扩展);live(key=None)返回 None=不入湖。"""
    pol = policy(endpoint)
    kind = pol["key"]
    if kind is None:                      # live
        return None
    if kind == "static":
        return "static"
    if kind == "date":
        d = _compact(_first(params, _DATE_PARAM_KEYS))
        return d if d else "unkeyed"
    if kind == "period":
        p = _compact(_first(params, _PERIOD_PARAM_KEYS))
        return p if p else "unkeyed"
    if kind == "as_of":
        entity = _first(params, _ENTITY_PARAM_KEYS) or "all"
        # ts_code/symbol 里的 '.' 不进文件名(避免被当扩展名);as_of 缺省=取数日。
        entity = str(entity).replace(".", "_")
        as_of = _compact(params.get("as_of")) or today
        return f"{entity}@{as_of}"
    raise ValueError(f"bad key kind {kind!r} for endpoint {endpoint!r}")


def lake_path(endpoint: str, params: dict, today: str | None = None) -> Path:
    """该 (endpoint, params) 在湖里的 parquet 路径(live 端点的 key 为 'live' 占位,
    仅用于路径推导,实际 get_or_fetch 对 live 不读写)。"""
    t = _today_compact(today)
    key = _cache_key(endpoint, params, t)
    if key is None:
        key = "live"
    return LAKE / endpoint / f"{key}.parquet"


def _read(path: Path) -> pd.DataFrame:
    return pq.read_table(path).to_pandas()


def _atomic_write(path: Path, df: pd.DataFrame) -> None:
    """ZSTD parquet 原子写:tmp → os.replace。空帧也写(存在==取过且为空)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, tmp, compression=_COMPRESSION)
    os.replace(tmp, path)


def _lake_params(params: dict) -> dict:
    """**入湖取数参数 = 原参数剥掉 `fields`。**

    湖里一个 key 只有一个 parquet,而 `_cache_key` **不含 `fields`** —— 所以带窄 `fields` 的
    查询一旦成为某 key 的首个写入者,就把窄表钉成了这一天的湖快照:后来要别的列的调用方只会
    读到缺列的表,而这类失败往往被上游的 try/except 静默吞成"降级"。

    2026-07-12 实证(漏斗回放器 M1 对拍逮到):`temperature.rollup` 用
    `fields='ts_code,pct_chg'` 回填 07-09/07-10 的 daily(那两天在扫描**当天**尚未结算,按
    "date>=today 拉新但不写"的规则没入湖),把这两天钉成了两列窄表 → `_harvest_vol_series`
    拿不到 high/low/amount → volprice 组整组 NaN → 全市场 composite 失真 98.8% → L2 名单
    面目全非。而这两天正落在下次扫描的近 20 日窗口里 —— 生产扫描本会静默中招。

    故:**写湖一律拉全字段**;要窄列的调用方自己 `df[cols]`(多几列无害,少一列是灾难)。
    不入湖的路径(live / date>=today)不走本函数,保留窄 `fields` 省流量。
    """
    return {k: v for k, v in params.items() if k != "fields"}


def get_or_fetch(
    endpoint: str,
    params: dict,
    today: str | None = None,
    fetch=None,
) -> pd.DataFrame:
    """湖命中即读,否则拉取 → **契约校验** → 原子写;按 policy 决定 key/是否缓存/今天是否取新。

    fetch(endpoint, params) -> DataFrame  缺省走 sources.fetch(可注入,便于离线测)。

    **契约校验**(`contracts.check`,2026-07-12 用户裁定"取数以后要有全面校验,为空抛异常阻断"):
    A 级(地基:行情/估值/资金/筹码/技术/证券基础)空或残缺 → `DataContractError` 阻断,且
    **拒绝入湖**——脏数据一旦落盘就被钉死,之后每次命中都是脏的,重跑也自愈不了(窄表毒化事故的
    根本教训)。B 级(增强)缺失 → 记账 + 降级,不阻断。

    校验挂在**三条路径**上,尤其是**湖命中**:历史脏数据(空帧/窄表)读出来照样毒化下游,而且它
    **不会**再经过取数路径的任何检查 —— 这是原设计的盲区。
    """
    trace = _source_trace(endpoint, params, today)
    try:
        if fetch is None:
            from autoresearch.data.sources import fetch as fetch  # 延迟导入,避开取数依赖

        # 延迟导入:契约表是纯数据,无取数依赖
        from autoresearch.data.contracts import check, refuses_lake

        pol = policy(endpoint)
        t = _today_compact(today)

        # ③ live:总取新,绝不缓存,不校验(盘中快照的完整性由调用方自负——它们本就不入湖)。
        if pol["settle"] == "live":
            result = fetch(endpoint, params)
            _finish_source_success(trace, result, "FETCHED_LIVE", None)
            return result

        key = _cache_key(endpoint, params, t)
        path = LAKE / endpoint / f"{key}.parquet"

        # 已结算(date < today)且文件存在 → 命中,零取数。**命中也要校验**(湖里可能躺着毒源)。
        if path.exists():
            result = check(endpoint, _read(path), key=str(key), source="lake")
            _finish_source_success(trace, result, "CACHE_HIT", path)
            return result

        # 快照端点的 PIT 守门(Wave12 复核 I1):只挡**写新分区**,历史读在上一行已经放行。
        # 快照接口只有"此刻",所以 as-of 键必须等于真实今天;否则这次取数会把今天的观测钉成
        # 过去某天的假历史(补跑 / 节假日 launchd 触发 / 手工传日期都会撞上),事后不可甄别。
        if pol.get("snapshot") and t != _real_today():
            raise SnapshotDateError(
                f"[快照 PIT] {endpoint} 的 as-of 键 {t} ≠ 今天 {_real_today()}:"
                f"快照接口只返回「此刻」,补不出 {t} 的历史 —— 强行取数会把**今天**的观测写成 "
                f"{t} 的假历史且事后不可甄别。要今天的快照就用今天的日期;要 {t} 的,它已经永远没有了。")

        # date 键:date >= today(盘中未结算)→ 拉新但不写(明天结算后才入湖)。**只查空、不查列**
        # (`cols=False`):这份数据不入湖、只服务当次调用,调用方要哪几列是它自己的事(温度计只要
        # `ts_code,pct_chg`)。但 A 级拉到空仍要抛 —— 数据还没发布,下游必然残废,与
        # `assert_tushare_ready` 同一立场("晚点再跑,或跑前一交易日")。
        if pol["key"] == "date":
            d = _compact(_first(params, _DATE_PARAM_KEYS))
            # 预热豁免(spec 2026-07-12-scan-speed-perimeter §P1):LAKE_ASSUME_SETTLED=1 且
            # d == today → 视为已结算,落到下方「拉取→契约→原子写」正常入湖(19:15 后 EOD 已发布,
            # 契约 min_rows 仍兜底);d > today(未来日)任何情况拒写。env 未设 = 现行为逐字节不变。
            if d and d >= t and not (d == t and os.environ.get("LAKE_ASSUME_SETTLED") == "1"):
                result = check(
                    endpoint,
                    fetch(endpoint, params),
                    key=str(key),
                    source="fetch",
                    cols=False,
                )
                _finish_source_success(trace, result, "FETCHED_UNSETTLED", None)
                return result

        # 拉取 → 校验 → 原子写。**入湖必须全字段**(`_lake_params`:窄 fields 会把窄表钉成该 key 的
        # 湖快照,毒化所有后来的调用方——2026-07-12 M1 对拍实证)。
        df = fetch(endpoint, _lake_params(params))
        if df is None:
            df = pd.DataFrame()
        df = check(endpoint, df, key=str(key), source="fetch")   # A 级违约 → 抛,下一行不执行 = 不入湖
        if pol.get("snapshot"):
            df = _stamp_observed(df)                             # 观测出处(I3):落盘前打戳
        # B 级快照端点的空/半截**同样不入湖**(C2):落了就 `path.exists()` 恒命中,这一天永远残缺;
        # 不落 → 同日重跑(或下一次夜采)还能救回来。契约已在上面 check() 里记过账,这里只管别钉死。
        if refuses_lake(endpoint, df):
            _finish_source_success(trace, df, "FETCHED_REFUSED_LAKE", None)
            return df
        _atomic_write(path, df)
        _finish_source_success(trace, df, "FETCHED_CACHED", path)
        return df
    except BaseException as exc:
        _finish_source_failure(trace, exc)
        raise

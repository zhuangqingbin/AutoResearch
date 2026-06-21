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

import os
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from autoresearch.data.endpoints import policy

# 数据湖根目录(测试 monkeypatch 此常量以重定向到 tmp）。
LAKE = Path("context/lake")

_COMPRESSION = "zstd"

# 各 key 模式下,从 params 里找"日期/报告期/实体"用的候选键名(吸收 tushare/akshare 差异)。
_DATE_PARAM_KEYS = ("trade_date", "date", "ann_date", "cal_date")
_PERIOD_PARAM_KEYS = ("period", "date", "end_date")
_ENTITY_PARAM_KEYS = ("ts_code", "symbol", "code", "exchange_id", "exchange")


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


def get_or_fetch(
    endpoint: str,
    params: dict,
    today: str | None = None,
    fetch=None,
) -> pd.DataFrame:
    """湖命中即读,否则拉取 → 原子写;按 policy 决定 key/是否缓存/今天是否取新。

    fetch(endpoint, params) -> DataFrame  缺省走 sources.fetch(可注入,便于离线测)。
    """
    if fetch is None:
        from autoresearch.data.sources import fetch as fetch  # 延迟导入,避开取数依赖

    pol = policy(endpoint)
    t = _today_compact(today)

    # ③ live:总取新,绝不缓存。
    if pol["settle"] == "live":
        return fetch(endpoint, params)

    key = _cache_key(endpoint, params, t)
    path = LAKE / endpoint / f"{key}.parquet"

    # 已结算(date < today)且文件存在 → 命中,零取数。
    if path.exists():
        return _read(path)

    # date 键:date >= today(盘中未结算)→ 拉新但不写(明天结算后才入湖)。
    if pol["key"] == "date":
        d = _compact(_first(params, _DATE_PARAM_KEYS))
        if d and d >= t:
            return fetch(endpoint, params)

    # 拉取 → 原子写(空帧也写)→ 返回。
    df = fetch(endpoint, params)
    if df is None:
        df = pd.DataFrame()
    _atomic_write(path, df)
    return df

#!/usr/bin/env python3
"""隔夜集中信号普查 · 面板层:湖 → 全湖 `(code, D)` 长表(零网络、只读、不回注)。

设计稿 §4.3(`docs/specs/2026-08-28-overnight-concentrated-signal-census-design.md`)。
一行 = 一个扫描日 D 上一只当日**有日线**的票;所有收益列一律 **pp(百分点)**。

**复用而非重写**(判例:两把尺同名不同身 = 口径分裂):

* 主尺 `gap_pp` / `buyable_c1` / `unsellable_o2` 全部来自
  `edge_census.forward_frame`(内部即 `factor_lab.forward_returns`),本模块**不写
  `open(D+2)/close(D+1)-1` 这条公式**;`|gap| > ruler.GAP_CLIP` 视数据错置 NaN 并计数进 meta。
* `t1_pos_in_range` 与 `exec_ok` 与 `scan/outcome.py` 逐字同义,阈值 import
  `EXEC_MAX_PCT_1D` / `EXEC_MAX_POS_IN_RANGE`,不复制字面量。
* 人口地板 `CAP_FLOOR_YI` 来自本包 `core.py`(全局常量单点)。
* `ts_code` 由 `dataflows.symbol_utils.to_ts_code` 派生(北交所 92xxxx 判例的单一事实源);
  2026-08-28 对全湖 5740 个真 `ts_code` 逐一比对,**0 处不一致**。

**已知偏差(写在这里就是为了不被静默吞掉)**:

1. `is_st` 读 `lake/stock_basic/static.parquet` 的**当前**名称快照,不是 point-in-time
   —— 今天叫 ST 的票在 2022 年未必是 ST,反之亦然。meta 里 `is_st_source =
   "static snapshot, not PIT"`。设计稿 §3.1 要求 PIT(`namechange`)才能判主格,本层只
   负责如实标注,不负责假装它是 PIT。快照里查不到的代码(已退市)按**非 ST** 处理。
   (匹配面:2026-08-28 全表 5529 只里 225 只命中,**无一是假阳**——命中的名字全部以
   `ST` / `*ST` 开头。)
2. `daily_basic`(`total_mv_yi` / `turnover_rate`)在湖里**远不是全覆盖**(立案时实测
   464/1015 日 = 45.7%,缺失段几乎全是「连续 2 天」的稀疏采样,2022 年仅 25%;回填在跑,
   覆盖率会变,所以判读一律读 meta 的实测值,不读这段文字)。故做 **≤10 交易日前向填充**;
   `mv_stale_days` 记填充距离(0 = 当日真值、NaN = 无值可填),meta 记 `mv_coverage`
   (有分区的建面板日占比)与 `mv_filled`(被填充的行数)。**填充是有损降级,必须留痕**
   (家训:数据契约 B 级降级不得不记账)。市值未知 → `in_pop=False`(不猜)。
3. `buyable_c1` 列按契约做**保守折叠**(未知 → False,另有 `buyable_known` 标未知),
   而 `rel_gap_pp` 的基准人口与 `exec_ok` 走**生产折叠** `ruler.entry_tradable`
   (未知 → True,`ruler.REL_MARKET` 同一单点)。两者只可能在 `buyable_known=False`
   的行上分叉;2026-08-28 全湖 574.3 万行实测「未知 ∧ gap 可算」= **1 行**
   (未知几乎恒因 D+1 缺数,而 D+1 缺数时 gap 本身也是 NaN)。meta 记
   `n_buyable_unknown` / `n_buyable_unknown_with_gap`,分叉真发生时看得见。
4. `edge_census.load_lake_pivots` 逐字段各跑一次 `pivot_table`,行轴可能不一致 → 见
   `_align_pivots`(本层补齐,`meta["n_pivot_realigned"]` 留痕)。

规模与开销(2026-08-28 实测,全湖 20220302–20260827):**1091 日 × 574.3 万行,建面板
23s、峰值 RSS ≈1.7GB、成品数组 436MB**(其中 `date`/`code`/`ts_code` 三列 str 指针数组
就占 138MB —— 契约规定这三列是 str;要更省内存请用 `since`/`until` 切窗,26 日约 14 万行)。
面板行数**恒等于**湖里 `daily` 分区的总行数(实测 5742622 = 5742622),没有幽灵行。

单位:`close_d` 元;`amount_d` **千元**(`lake/daily.amount` 原单位,不换算);
`total_mv_yi` **亿元**(`daily_basic.total_mv` 万元 ÷ 1e4);`turnover_rate` %;
`gap_pp` / `rel_gap_pp` / `pct_5d_pp` / `vol20` / `t1_pct_chg` 全是 pp。

用法(只读,不联网):

    from autoresearch.research.overnight_census.panel import build_panel
    df = build_panel(since="20260701", until="20260805", cache=False)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import ruler as _ruler, workspace as ws
from autoresearch.dataflows.symbol_utils import to_ts_code
from autoresearch.research import edge_census as ec, factor_lab as fl
from autoresearch.scan.outcome import EXEC_MAX_PCT_1D, EXEC_MAX_POS_IN_RANGE

from .core import CAP_FLOOR_YI

MAIN = _ruler.MAIN_RULER

#: `daily_basic` 前向填充上限(交易日);第 11 日起不填,留 NaN。
MV_FILL_MAX_DAYS = 10
#: `vol20` 窗口与最少有效观测(停牌日在湖里没有行 → pivot 为 NaN;要求 75% 在场)。
VOL20_WINDOW = 20
VOL20_MIN_OBS = 15
#: `pct_5d_pp` = close(D) / close(D−5) − 1;`limit_5d` 看含 D 的近 5 个交易日。
PCT_5D_LAG = 5
LIMIT_LOOKBACK = 5
#: 分块:一次只把 CHUNK_DAYS + 前后余量的 pivot 装进内存,逐日算完就 append。
CHUNK_DAYS = 250
_BACK = max(VOL20_WINDOW, PCT_5D_LAG, LIMIT_LOOKBACK, MV_FILL_MAX_DAYS) + 1
_FWD = 2                      # 主尺只吃到 D+2(gap / unsellable);fwd_5/10 不进本面板
IS_ST_SOURCE = "static snapshot, not PIT"
_META_NAME = "panel_meta.json"

_FLOAT_COLS = ("gap_pp", "rel_gap_pp", "close_d", "amount_d", "t1_pct_chg", "t1_pos_in_range",
               "pct_5d_pp", "turnover_rate", "total_mv_yi", "mv_stale_days", "vol20")
_BOOL_COLS = ("buyable_c1", "buyable_known", "unsellable_o2", "limit_5d", "is_st", "in_pop")
COLUMNS = ("date", "code", "ts_code", "gap_pp", "rel_gap_pp", "buyable_c1", "buyable_known",
           "unsellable_o2", "close_d", "amount_d", "t1_pct_chg", "t1_pos_in_range", "exec_ok",
           "pct_5d_pp", "turnover_rate", "total_mv_yi", "mv_stale_days", "vol20", "limit_5d",
           "is_st", "in_pop")


# ───────────────────────── 路径 ─────────────────────────

def panel_path() -> Path:
    """面板缓存落点(引擎隔离;`ws.reports_root()` 是路径唯一事实源)。"""
    return ws.reports_root() / "research" / "overnight_census" / "panel.parquet"


def _meta_path() -> Path:
    """parquet 不存 `df.attrs`,meta 另写同目录 sidecar,读缓存时装回。"""
    return panel_path().with_name(_META_NAME)


def _lake(lake_root: Path | None) -> Path:
    return Path(lake_root) if lake_root is not None else ws.lake_root()


# ───────────────────────── 辅助:静态表 / daily_basic ─────────────────────────

def _is_st_map(lake_root: Path | None) -> dict[str, bool]:
    """`code6 → 名称含 ST`(含 `*ST`)。**当前快照,不是 PIT**(见模块 docstring 偏差 1)。"""
    fp = _lake(lake_root) / "stock_basic" / "static.parquet"
    if not fp.exists():
        return {}
    df = pd.read_parquet(fp)
    if df.empty or "ts_code" not in df.columns or "name" not in df.columns:
        return {}
    code = df["ts_code"].astype(str).str[:6].str.zfill(6)
    flag = df["name"].astype(str).str.upper().str.contains("ST", na=False)
    return dict(zip(code, flag.tolist(), strict=False))


def _basic_pivots(days: list[str], lake_root: Path | None):
    """`lake/daily_basic/<d>.parquet` → (总市值亿元, 换手率, 填充距离, 有分区的日)。

    三张表列轴都已 reindex 到 `days`(缺分区的日 = 整列 NaN),再做 **≤`MV_FILL_MAX_DAYS`
    交易日**前向填充。`stale` 以**总市值**的真值位置为准:0 = 当日真值、k = 距上一个真值
    k 个交易日、NaN = 10 日内无值可填(此时 mv/turnover 也必为 NaN)。
    """
    root = _lake(lake_root) / "daily_basic"
    frames, present = [], []
    for day in days:
        fp = root / f"{day}.parquet"
        if not fp.exists():
            continue
        df = pd.read_parquet(fp)
        if df.empty or "ts_code" not in df.columns:
            continue
        present.append(day)
        frames.append(pd.DataFrame({
            "code": df["ts_code"].astype(str).str[:6].str.zfill(6),
            "date": day,
            "total_mv_yi": pd.to_numeric(df.get("total_mv"), errors="coerce") / 1e4,
            "turnover_rate": pd.to_numeric(df.get("turnover_rate"), errors="coerce"),
        }))
    empty = pd.DataFrame(index=pd.Index([], name="code"), columns=days, dtype="float64")
    if not frames:
        return empty, empty.copy(), empty.copy(), []
    long = pd.concat(frames, ignore_index=True)
    def _piv(col: str) -> pd.DataFrame:
        return long.pivot_table(index="code", columns="date", values=col).reindex(columns=days)

    mv, tr = _piv("total_mv_yi"), _piv("turnover_rate")
    axis = np.arange(len(days), dtype="float64")
    pos = pd.DataFrame(np.where(mv.notna().to_numpy(), axis, np.nan),
                       index=mv.index, columns=days)
    stale = axis - pos.ffill(axis=1, limit=MV_FILL_MAX_DAYS)
    return (mv.ffill(axis=1, limit=MV_FILL_MAX_DAYS),
            tr.ffill(axis=1, limit=MV_FILL_MAX_DAYS), stale, present)


def _col(piv: dict, key: str, day: str | None, codes: pd.Index) -> pd.Series:
    """pivot 某日某列 → 对齐 `codes` 的 float Series;越界/缺列 → 全 NaN(不伪造)。"""
    src = piv.get(key)
    if src is None or day is None or day not in src.columns:
        return pd.Series(np.nan, index=codes, dtype="float64")
    return pd.to_numeric(src[day], errors="coerce").reindex(codes)


def _window_cols(piv: dict, key: str, days: list[str]) -> pd.DataFrame | None:
    src = piv.get(key)
    if src is None:
        return None
    cols = [d for d in days if d in src.columns]
    return src[cols] if cols else None


def _align_pivots(piv: dict, acc: dict) -> dict:
    """六张 pivot 的**行轴对齐到并集**(只补 NaN,不改任何数值)。

    `edge_census.load_lake_pivots` 逐字段各跑一次 `pivot_table`,而 `pivot_table` 会把
    某字段全 NaN 的 code **整行丢掉** → 各字段行轴可能不一致;`factor_lab.forward_returns`
    随后拿 `close.index` 造的 `lim` 去比 `pct_chg`,直接
    `ValueError: Can only compare identically-labeled Series objects`。

    实测(2026-08-28,全湖首个 250 日块 20220302 起):`920570` 在该窗内只有 1 天日线且
    当天 `pct_chg` 为 NaN(北交所新股首日),`pct_chg` pivot 少这一行,整块构建当场炸。
    既有消费者没炸只是因为窗口短(`edge_census` 只装扫描日、`outcome.market_frame` 只装
    14 天),不是因为不存在。**本层不改生产**,只把输入补成同轴;repair 次数记进
    `meta["n_pivot_realigned"]`,别让它静默。
    """
    if not piv:
        return piv
    idx: pd.Index | None = None
    for v in piv.values():
        idx = v.index if idx is None else idx.union(v.index)
    out = {}
    for k, v in piv.items():
        if v.index.equals(idx):
            out[k] = v
        else:
            acc["n_pivot_realigned"] += 1
            out[k] = v.reindex(idx)
    return out


# ───────────────────────── 逐日一行块 ─────────────────────────

def _day_rows(piv: dict, P: list[str], D: str, *, mv, tr, stale, st_map: dict[str, bool],
              board: pd.Series, ts_map: dict[str, str], acc: dict) -> pd.DataFrame | None:
    """一个扫描日 D → 该日全湖行块;D 无前向帧 → None(缺日不伪造)。"""
    fr = ec.forward_frame(piv, P, D)
    if fr is None or fr.empty:
        return None
    acc["n_clipped"] += int(fr.attrs.get("n_clipped", 0))
    codes = fr.index
    idx = P.index(D)

    gap_pp = pd.to_numeric(fr[MAIN], errors="coerce") * 100.0
    # 基准人口 = 当日**全湖可交易**(`ruler.entry_tradable` 单点,不设市值地板、不问 L0),
    # 与 `ruler.REL_MARKET` / `scan/outcome._relative_columns` 同一口径。
    entry_ok = _ruler.entry_tradable(fr, ruler_name=MAIN)
    base = gap_pp[entry_ok & gap_pp.notna()]
    rel_pp = gap_pp - base.mean() if len(base) else pd.Series(np.nan, index=codes, dtype="float64")

    raw_buyable = fr["buyable_c1"]
    known = raw_buyable.notna()
    acc["n_buyable_unknown"] += int((~known).sum())
    acc["n_buyable_unknown_with_gap"] += int(((~known) & gap_pp.notna()).sum())

    close_d = _col(piv, "close", D, codes)
    amount_d = _col(piv, "amount", D, codes)

    t1 = P[idx + 1] if idx + 1 < len(P) else None
    t1_pct = _col(piv, "pct_chg", t1, codes)
    t1_high, t1_low, t1_close = (_col(piv, k, t1, codes) for k in ("high", "low", "close"))
    span = t1_high - t1_low
    # `scan/outcome.market_frame` 逐字同义:区间为 0(一字)→ NaN,不伪造 0/1。
    t1_pos = ((t1_close - t1_low) / span).where(span > 0)
    # `scan/outcome.compute_outcome` 逐字同义:三个条件全用生产折叠后的 buyable,
    # 阈值 import 自生产(`<= EXEC_MAX_PCT_1D` 与 `< EXEC_MAX_POS_IN_RANGE`,符号不同);
    # 两个 T+1 读数任一未知 → exec_ok 未知(pd.NA),不折成 False。
    known_exec = (t1_pct.notna() & t1_pos.notna()).to_numpy()
    hit = (entry_ok.to_numpy() & (t1_pct.to_numpy() <= EXEC_MAX_PCT_1D)
           & (t1_pos.to_numpy() < EXEC_MAX_POS_IN_RANGE))
    exec_ok = pd.array(hit, dtype="boolean")
    exec_ok[~known_exec] = pd.NA

    j = idx - PCT_5D_LAG
    prev = _col(piv, "close", P[j], codes) if j >= 0 else pd.Series(np.nan, index=codes)
    pct_5d = (close_d / prev.where(prev > 0) - 1.0) * 100.0

    vol_win = _window_cols(piv, "pct_chg", P[max(0, idx - VOL20_WINDOW + 1):idx + 1])
    if vol_win is None or vol_win.empty:
        vol20 = pd.Series(np.nan, index=codes, dtype="float64")
    else:
        vol20 = vol_win.std(axis=1, ddof=1).where(vol_win.notna().sum(axis=1) >= VOL20_MIN_OBS)
        vol20 = vol20.reindex(codes)

    lim_win = _window_cols(piv, "pct_chg", P[max(0, idx - LIMIT_LOOKBACK + 1):idx + 1])
    if lim_win is None or lim_win.empty:
        limit_5d = pd.Series(False, index=codes, dtype=bool)
    else:
        limit_5d = lim_win.ge(board * 0.98, axis=0).any(axis=1).reindex(codes).fillna(False)

    mv_d = mv[D].reindex(codes) if D in mv.columns else pd.Series(np.nan, index=codes)
    tr_d = tr[D].reindex(codes) if D in tr.columns else pd.Series(np.nan, index=codes)
    stale_d = stale[D].reindex(codes) if D in stale.columns else pd.Series(np.nan, index=codes)

    is_st = np.array([bool(st_map.get(c, False)) for c in codes], dtype=bool)
    ts_code = np.array([ts_map[c] for c in codes], dtype=object)
    # 契约:面板列 `buyable_c1` 做**保守折叠**(未知 → False),`buyable_known` 留未知痕迹。
    buyable = known.to_numpy() & raw_buyable.fillna(False).to_numpy().astype(bool)
    is_bj = np.array([str(t).endswith(".BJ") for t in ts_code], dtype=bool)
    in_pop = (~is_st) & (~is_bj) & (mv_d.to_numpy() >= CAP_FLOOR_YI) & buyable

    out = pd.DataFrame({
        "date": D, "code": np.asarray(codes, dtype=object), "ts_code": ts_code,
        "gap_pp": gap_pp.to_numpy(), "rel_gap_pp": rel_pp.to_numpy(),
        "buyable_c1": buyable, "buyable_known": known.to_numpy(),
        "unsellable_o2": fr["unsellable_o2"].fillna(False).to_numpy().astype(bool),
        "close_d": close_d.to_numpy(), "amount_d": amount_d.to_numpy(),
        "t1_pct_chg": t1_pct.to_numpy(), "t1_pos_in_range": t1_pos.to_numpy(),
        "exec_ok": exec_ok, "pct_5d_pp": pct_5d.to_numpy(),
        "turnover_rate": tr_d.to_numpy(), "total_mv_yi": mv_d.to_numpy(),
        "mv_stale_days": stale_d.to_numpy(), "vol20": vol20.to_numpy(),
        "limit_5d": limit_5d.to_numpy().astype(bool), "is_st": is_st, "in_pop": in_pop,
    })
    # 当日没有日线的票(停牌 / 未上市 / 已退市)不进面板 —— pivot 的 index 是窗口内**并集**,
    # 不过滤会凭空造出全 NaN 行(面板行数 ≠ 湖行数,后面每个族的分母都跟着错)。
    return out[close_d.notna().to_numpy()]


def _cast(df: pd.DataFrame) -> pd.DataFrame:
    """就地降 dtype(float32 / bool / 可空 boolean)+ 定列序;已是目标 dtype 的列不复制。"""
    for c in _FLOAT_COLS:
        if str(df[c].dtype) != "float32":
            df[c] = df[c].astype("float32")
    for c in _BOOL_COLS:
        if df[c].dtype != bool:
            df[c] = df[c].astype(bool)
    if str(df["exec_ok"].dtype) != "boolean":
        df["exec_ok"] = df["exec_ok"].astype("boolean")
    return df if list(df.columns) == list(COLUMNS) else df[list(COLUMNS)]


def _empty_panel() -> pd.DataFrame:
    return _cast(pd.DataFrame({
        **{c: pd.Series(dtype=object) for c in ("date", "code", "ts_code")},
        **{c: pd.Series(dtype="float64") for c in _FLOAT_COLS},
        **{c: pd.Series(dtype=bool) for c in _BOOL_COLS},
        "exec_ok": pd.Series(dtype="boolean"),
    }))


# ───────────────────────── 缓存 ─────────────────────────

def _read_cache(since: str | None, until: str | None, first: str, last: str):
    """缓存覆盖请求区间 → (切片后的面板, 装回的 meta);否则 None。"""
    fp, mp = panel_path(), _meta_path()
    if not fp.exists() or not mp.exists():
        return None
    try:
        meta = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    lo, hi = str(meta.get("built_first") or ""), str(meta.get("built_last") or "")
    if not lo or not hi or lo > first or hi < last:
        return None
    df = pd.read_parquet(fp)
    if since:
        df = df[df["date"] >= since]
    if until:
        df = df[df["date"] <= until]
    df = df.reset_index(drop=True)
    meta = {**meta, "since": since, "until": until, "from_cache": True}
    df.attrs["meta"] = meta
    return df


def _write_cache(df: pd.DataFrame, meta: dict) -> None:
    fp = panel_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(fp, index=False)
    _meta_path().write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


# ───────────────────────── 主入口 ─────────────────────────

def build_panel(since: str | None = None, until: str | None = None, *,
                lake_root: Path | None = None, rebuild: bool = False,
                cache: bool = True) -> pd.DataFrame:
    """扫描日 D 的全湖面板(一行 = 一个 `(D, code)`,该日有日线才在场)。

    `since` / `until` 为 `YYYYMMDD`(含),默认全湖。`lake_root` 指到含 `daily/` /
    `daily_basic/` / `stock_basic/` 的湖根(测试注入合成湖用);默认 `ws.lake_root()`。
    `cache=True ∧ rebuild=False ∧ 缓存存在且覆盖请求区间` → 直接读缓存(meta 从 sidecar
    装回);`cache=True` 时新建的面板会写回缓存,`cache=False` **既不读也不写**。

    列见模块 `COLUMNS`;收益列一律 pp。`df.attrs["meta"]` 至少含
    `n_clipped / n_days / lake_first / lake_last / mv_coverage / mv_filled / since / until`,
    另附 `built_first / built_last / n_rows / is_st_source / n_buyable_unknown /
    n_buyable_unknown_with_gap / n_pivot_realigned / mv_fill_max_days / cap_floor_yi /
    exec_line / seconds`。

    **缺日不伪造**:湖里没有日线分区的日子不会出现在面板里(不插空行、不前向填充价格)。
    """
    t0 = time.time()
    lake_daily = _lake(lake_root) / "daily"
    P = ec.lake_trade_days(lake_daily)
    targets = [d for d in P if (not since or d >= since) and (not until or d <= until)]
    meta: dict = {"n_clipped": 0, "n_days": 0,
                  "lake_first": P[0] if P else None, "lake_last": P[-1] if P else None,
                  "mv_coverage": None, "mv_filled": 0, "since": since, "until": until,
                  "built_first": None, "built_last": None, "n_rows": 0,
                  "is_st_source": IS_ST_SOURCE, "n_buyable_unknown": 0,
                  "n_buyable_unknown_with_gap": 0, "n_pivot_realigned": 0,
                  "mv_fill_max_days": MV_FILL_MAX_DAYS,
                  "cap_floor_yi": CAP_FLOOR_YI, "main_ruler": MAIN, "gap_clip": _ruler.GAP_CLIP,
                  "exec_line": {"max_pct_1d": EXEC_MAX_PCT_1D,
                                "max_pos_in_range": EXEC_MAX_POS_IN_RANGE},
                  "from_cache": False, "seconds": 0.0}
    if not targets:
        out = _empty_panel()
        out.attrs["meta"] = meta
        return out

    if cache and not rebuild:
        hit = _read_cache(since, until, targets[0], targets[-1])
        if hit is not None:
            return hit

    st_map = _is_st_map(lake_root)
    acc = {"n_clipped": 0, "n_buyable_unknown": 0, "n_buyable_unknown_with_gap": 0,
           "n_pivot_realigned": 0}
    ts_map: dict[str, str] = {}
    # 逐日算完立刻降到目标 dtype 再 append(全湖 1091 日 × ~5300 只 ≈ 574 万行;不降就是
    # float64 的两倍开销),最后**只 concat 一次** —— 分块 concat 会多留一份整块副本。
    parts: list[pd.DataFrame] = []
    built_days: list[str] = []
    basic_days: set[str] = set()

    for c0 in range(0, len(targets), CHUNK_DAYS):
        block = targets[c0:c0 + CHUNK_DAYS]
        lo = max(0, P.index(block[0]) - _BACK)
        hi = min(len(P), P.index(block[-1]) + _FWD + 1)
        window = P[lo:hi]
        piv = _align_pivots(ec.load_lake_pivots(window, lake_daily), acc)
        if not piv:
            continue
        mv, tr, stale, present = _basic_pivots(window, lake_root)
        basic_days.update(d for d in present if d in block)
        codes = piv["close"].index
        board = pd.Series([fl._board_limit(str(c)) for c in codes], index=codes, dtype="float64")
        for c in codes:
            key = str(c)
            if key not in ts_map:
                ts_map[key] = to_ts_code(key)
        for day in block:
            rows = _day_rows(piv, P, day, mv=mv, tr=tr, stale=stale, st_map=st_map,
                             board=board, ts_map=ts_map, acc=acc)
            if rows is None or rows.empty:
                continue
            parts.append(_cast(rows.reset_index(drop=True)))
            built_days.append(day)
        del piv, mv, tr, stale

    out = _cast(pd.concat(parts, ignore_index=True)) if parts else _empty_panel()
    del parts
    meta.update(acc)
    meta["n_days"] = len(built_days)
    meta["built_first"] = built_days[0] if built_days else None
    meta["built_last"] = built_days[-1] if built_days else None
    meta["n_rows"] = int(len(out))
    meta["mv_coverage"] = (len(basic_days) / len(built_days)) if built_days else None
    meta["mv_filled"] = int((out["mv_stale_days"] > 0).sum())
    meta["seconds"] = round(time.time() - t0, 2)
    out.attrs["meta"] = meta
    if cache:
        _write_cache(out, meta)
    return out

#!/usr/bin/env python3
"""隔夜集中信号普查 · 16 格族定义(设计稿 `docs/specs/2026-08-28-overnight-concentrated-signal-census-design.md`
§3.3 的机器实现)。

一格 = 一个「在扫描日 D 收盘后可判定的条件」+ 它选出的 (D, 票) 观测集合。收益列恒为面板的
主尺 `gap_c1_o2`(close(D+1) → open(D+2),单位 **pp**),本模块**不算收益**,只负责选人口、
选事件、对齐时点。统计与判读归 `core.cell_stats` / `core.judge`。

## 本模块最容易错的一件事:时点对齐

  * **R 族**(F1/F2/F4)= D 盘后、当晚报告生成前已知的事实。资金/成交类源表 join **D 当日表**
    (`lake/top_inst/<D>.parquet` 等);事件类源表(F1)按**事件日 == D+2** 反查 D,并在有
    `ann_date` 时用 `ann_date <= D` 做 PIT 过滤(缺 `ann_date` 的行保留但计数,不静默丢)。
  * **X_ORACLE 族**(F3)= 用 D+1 **收盘后**的 EOD 表构造的事后上界,join **D+1 当日表**
    (`limit_list_d.trade_date == D+1`)。F3 的任何读数都不是「14:57 可成交」的证明。

搞错方向就是前视(把 D+1 的表当 D 已知)或滞后(把 D 的表当 D+1 的结果),两种错都会让整张
读数表失真。`tests/research/test_oc_families.py::test_x_family_joins_next_day` 与
`::test_r_family_joins_same_day` 把这条锁死。

「D+1 / D+2」一律是**交易日**位移,轴取自面板自己的 `date` 唯一值(湖里没有的日子对本仪器
就是不存在;不查交易日历 —— 与 `edge_census.lake_trade_days` 同一纪律)。事件日若落在非交易日
(周末 / 停市),它就匹配不到任何 D,该事件不进表:这是设计稿「事件日 == D+2」的直译,**不做**
「顺延到下一个交易日」的猜测。

## 人口门(设计稿 §3.1)

  * F1 / F2 / F4 用面板的 `in_pop`(非 ST ∧ 非北交所 ∧ `total_mv_yi >= CAP_FLOOR_YI` ∧ `buyable_c1`);
  * **F3 不要求 `buyable_c1`** —— F3 量的正是生产因「D+1 封板买不进」而剔掉的那批镜像人口,
    要求可买等于把这一格自己掏空。F3 仍要非 ST ∧ 非 `.BJ` ∧ 市值地板,`notes` 标「生产不可见」。
  * R 族另报 `n_sealed_dropped` = 命中信号 ∧ 过了市值/ST/板块门,但因 `buyable_c1=False` 被剔的
    观测数。2026-08-08 判例:被剔的多是**继续封板**的票,剔了会美化账本 —— 所以必须显式计数,
    不能让它消失在一个 `&` 里。

## 单位(跨表比值前必须换算;搞错就是 1000 倍或 10000 倍)

  | 源 | 字段 | 单位 |
  |---|---|---|
  | `lake/daily`(→ 面板 `amount_d`) | `amount` | **千元** |
  | `lake/top_inst` | `buy`/`sell`/`net_buy` | **元** |
  | `lake/moneyflow` | `buy_elg_amount`/`sell_elg_amount` | **万元** |
  | `lake/block_trade` | `amount`(`vol`=万股,`price`=元/股) | **万元** |
  | `lake/limit_list_d` | `amount`/`fd_amount` | **元** |

  同表内的比值(`fd_amount / amount`)不换算;跨表比值一律先折算到 **元**。

## 缺表 / 缺列

缺源表或缺关键列 → 该格 `rows` 为空 + `notes` 记「数据缺席」,**不伪造为零集**(零集会被下游
当成「查过了,没有」),也不抛异常(一张表缺席不该让另外 15 格跑不出来)。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import workspace as ws

from . import core

# ───────────────────────── 阈值常量(全部照设计稿 §3.3,禁止散写字面量)─────────────────────────

NET_BUY_RATIO_MIN = 0.03        # F2a/F2b:Σ席位 net_buy(元)/ 当日成交额(元)≥ 3%
FLOAT_RATIO_MIN_PCT = 1.0       # F1b:解禁股占总股本比例 ≥ 1%(`share_float.float_ratio` 已是 %)
BLOCK_AMOUNT_RATIO_MIN = 0.01   # F2d:大宗日聚合金额(元)/ 当日成交额(元)≥ 1%
BLOCK_DISCOUNT_MIN = 0.05       # F2d2:折价格 ≥ 5%(VWAP ≤ close(D) × (1 − 5%))
ELG_TOP_PCT = 0.02              # F2e:超大单净流入占成交额,当日横截面前 2%
NORTH_DELTA_TOP_PCT = 0.01      # F2c2:北向持股比例 5 日增幅,当日横截面前 1%
RESONANCE_MIN_LIMITS = 3        # F3d:同日同行业涨停家数 ≥ 3 → 共振
LONE_LIMITS = 1                 # F3d:同日同行业涨停家数 == 1 → 孤板(配对对照)
F4_PCT5D_LO_PP = 2.0            # F4:`pct_5d(D)` 落在 [2, 5] pp(「中间态」= 温和上涨,不追强)
F4_PCT5D_HI_PP = 5.0

MIN_CROSS_SECTION = 50          # 横截面排序类格(F2c2/F2e)当日样本下限,不足则该日不选
                                # (同 `edge_census.MIN_CROSS_SECTION`:5 只票取「前 2%」= 取第一名)

AMOUNT_K_TO_YUAN = 1_000.0      # 千元 → 元
WAN_TO_YUAN = 10_000.0          # 万元 → 元

# ───────────────────────── 格清单(一格不多一格不少)─────────────────────────

#: 16 格的族键,渲染顺序即此顺序。改这张表 = 改预注册,必须同步设计稿 §3.3 与测试里的期望集合。
CELL_ORDER: tuple[str, ...] = (
    "F1a", "F1b", "F1c",
    "F2a", "F2b", "F2c1", "F2c2", "F2d1", "F2d2", "F2e",
    "F3a", "F3b", "F3c", "F3d", "F3e",
    "F4",
)
#: 6 个主格(设计稿里标「主格 / confirmatory 候选」的那几个);其余 10 格 exploratory。
MAIN_CELLS: tuple[str, ...] = ("F1a", "F2a", "F2b", "F3a", "F3b", "F4")

CELL_LABEL: dict[str, str] = {
    "F1a": "财报披露前夜", "F1b": "解禁前夜", "F1c": "除权除息前夜",
    "F2a": "知名游资净买", "F2b": "机构专用净买",
    "F2c1": "北向·股通席净买", "F2c2": "北向·持股比例 5 日增幅前 1%",
    "F2d1": "大宗·溢价", "F2d2": "大宗·折价",
    "F2e": "超大单净流入前 2%",
    "F3a": "首板·可能成交", "F3b": "首板·炸板", "F3c": "二板+·可能成交",
    "F3d": "板块共振增量", "F3e": "一字/早封厚单",
    "F4": "冷门中间态",
}

_MISSING = "数据缺席:{what} —— 本格不判读(空集 ≠ 查过了没有)"
_X_NOTE = "X_ORACLE:读 D+1 EOD 表事后构造,**生产不可见**(不要求 buyable_c1),只能判理论上界"

#: 面板必备列,缺任何一列本模块无法工作 —— 直接抛,不降级(降级会把口径错误藏进空表)。
REQUIRED_PANEL_COLS = ("date", "code", "ts_code", "gap_pp", "rel_gap_pp", "in_pop")

_DAILY_TABLES = {"top_inst": "top_inst", "block_trade": "block_trade",
                 "moneyflow": "moneyflow", "hk_hold": "hk_hold", "limit": "limit_list_d"}
_EVENT_TABLES = {"disclosure": "disclosure_date", "share_float": "share_float",
                 "dividend": "dividend"}
_DATE_LIKE = ("trade_date", "ann_date", "pre_date", "actual_date", "modify_date",
              "end_date", "float_date", "ex_date", "record_date", "pay_date",
              "imp_ann_date", "base_date", "div_listdate")


# ───────────────────────── 湖 → 事件表 ─────────────────────────

def _as_bool(s: pd.Series, default: bool = False) -> np.ndarray:
    """bool 列取值 —— 走 nullable `boolean` 而不是 `fillna(False).astype(bool)`:
    后者在 object dtype 上会触发 pandas 的静默降级(FutureWarning),而静默降级正是本项目
    「降级不留痕才是真病」判例的同一族问题。"""
    try:
        return s.astype("boolean").fillna(default).to_numpy(dtype=bool)
    except (TypeError, ValueError):
        return s.map(lambda x: default if pd.isna(x) else bool(x)).to_numpy(dtype=bool)


def _norm_dates(df: pd.DataFrame) -> pd.DataFrame:
    """把所有日期样的列统一成 `YYYYMMDD` 字符串(湖里有 int64 / `2026-08-05` 两种写法)。"""
    for c in _DATE_LIKE:
        if c in df.columns:
            s = df[c]
            if pd.api.types.is_numeric_dtype(s):
                s = s.astype("Int64").astype(str).replace("<NA>", "")
            else:
                s = s.astype(str)
            df[c] = s.str.replace("-", "", regex=False).str.strip().str.slice(0, 8)
            df.loc[~df[c].str.fullmatch(r"\d{8}", na=False), c] = ""
    return df


def _norm_codes(df: pd.DataFrame) -> pd.DataFrame:
    """`ts_code` 规范为 str;派生 6 位 `code`(与面板 join 用 `ts_code`,输出用 `code`)。"""
    if "ts_code" in df.columns:
        df["ts_code"] = df["ts_code"].astype(str).str.strip()
        df["code"] = df["ts_code"].str.slice(0, 6).str.zfill(6)
    return df


def _read_partitions(d: Path, *, since: str | None, until: str | None,
                     date_col: str = "trade_date") -> pd.DataFrame:
    """读 `<dir>/<YYYYMMDD>.parquet` 分区目录 → 长表。

    **分区键即日期**:湖里 `top_inst`/`block_trade`/`moneyflow`/`hk_hold` 的历史分区没有
    `trade_date` 列(窄表),日期只在文件名里 —— 所以缺列时从文件名补,补不出来就不要这份分区。
    分区间列不一致(`limit_list_d` 2026-07 前只有 5 列)由 `pd.concat` 对齐,缺的列填 NaN,
    消费点各自判「关键列缺席」。
    """
    if not d.exists():
        return pd.DataFrame()
    frames, files = [], sorted(p for p in d.glob("*.parquet") if p.stem[:8].isdigit())
    for fp in files:
        day = fp.stem[:8]
        if (since and day < since) or (until and day > until):
            continue
        try:
            df = pd.read_parquet(fp)
        except Exception:                                    # noqa: BLE001 — 坏分区不该毒死全表
            continue
        if df.empty:
            continue
        if date_col not in df.columns:
            df[date_col] = day
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return _norm_codes(_norm_dates(out))


def load_events(*, lake_root: Path | None = None, since: str | None = None,
                until: str | None = None) -> dict[str, pd.DataFrame]:
    """湖 → 8 张事件/资金/涨停表(零网络,只读)。

    → `{"disclosure","share_float","dividend","top_inst","block_trade","moneyflow",
        "hk_hold","limit"}`;每张表已规范 `ts_code`/`code` 与全部日期列(`YYYYMMDD` str)。
    缺表 → **空 DataFrame**(不抛);每张表的覆盖情况落在 `df.attrs["coverage"]`,
    用 `events_coverage()` 取出来进 meta。

    `since`/`until`(`YYYYMMDD`,含)只裁**逐日分区**表;事件表(disclosure/share_float/
    dividend)按报告期/月/公告日分区,整目录读进来后由各格自己按事件日过滤。
    """
    root = Path(lake_root) if lake_root else ws.lake_root()
    out: dict[str, pd.DataFrame] = {}
    for key, table in _DAILY_TABLES.items():
        df = _read_partitions(root / table, since=since, until=until)
        df.attrs["coverage"] = _coverage_of(root / table, df)
        out[key] = df
    for key, table in _EVENT_TABLES.items():
        df = _read_partitions(root / "events" / table, since=None, until=None,
                              date_col="_partition")
        df.attrs["coverage"] = _coverage_of(root / "events" / table, df)
        out[key] = df
    return out


def _coverage_of(d: Path, df: pd.DataFrame) -> dict:
    files = sorted(p.stem[:8] for p in d.glob("*.parquet")) if d.exists() else []
    return {"dir": str(d), "exists": d.exists(), "n_files": len(files),
            "first": files[0] if files else None, "last": files[-1] if files else None,
            "n_rows": int(len(df)), "columns": list(df.columns)}


def events_coverage(events: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """把 `load_events` 塞在 `df.attrs` 里的覆盖信息取出来(attrs 会在 copy/merge 中丢失,
    所以只在刚 load 完时取一次)。"""
    return {k: dict(v.attrs.get("coverage", {})) for k, v in events.items()}


# ───────────────────────── 面板上下文 ─────────────────────────

class _Ctx:
    """一次 `all_cells` 调用共享的面板派生量(交易日轴 / 人口掩码 / 成交额换算)。"""

    def __init__(self, panel: pd.DataFrame, events: dict[str, pd.DataFrame], youzi: set[str]):
        self.panel = panel.reset_index(drop=True)
        self.events = events or {}
        self.youzi = set(youzi or ())
        self.n = len(self.panel)

        self.days: list[str] = sorted(self.panel["date"].astype(str).unique())
        self.next1 = {d: self.days[i + 1] for i, d in enumerate(self.days[:-1])}
        self.next2 = {d: self.days[i + 2] for i, d in enumerate(self.days[:-2])}
        self.prev1 = {v: k for k, v in self.next1.items()}     # D+1 → D(X 族反查)
        self.prev2 = {v: k for k, v in self.next2.items()}     # D+2 → D(事件族反查)
        self.back5 = {d: self.days[i - 5] for i, d in enumerate(self.days) if i >= 5}

        self.keys = self.panel[["date", "ts_code"]].copy()
        self.gap = pd.to_numeric(self.panel["gap_pp"], errors="coerce")
        self.gap_ok = self.gap.notna().to_numpy()

        self.in_pop = self._bool("in_pop")
        self.buyable = self._bool("buyable_c1", default=True)
        self.pop_base = self._pop_base()
        self.amount_yuan = self._num("amount_d") * AMOUNT_K_TO_YUAN
        self.close_d = self._num("close_d")

    # -- 面板列存取(缺列 → 记账,由消费点决定是空格还是降级)-------------------------------
    def _bool(self, col: str, *, default: bool = False) -> np.ndarray:
        if col not in self.panel.columns:
            return np.full(self.n, default, dtype=bool)
        return _as_bool(self.panel[col])

    def _num(self, col: str) -> pd.Series:
        if col not in self.panel.columns:
            return pd.Series(np.nan, index=self.panel.index, dtype=float)
        return pd.to_numeric(self.panel[col], errors="coerce")

    def has(self, *cols: str) -> bool:
        return all(c in self.panel.columns for c in cols)

    def _pop_base(self) -> np.ndarray:
        """F3 人口 = 非 ST ∧ 非 `.BJ` ∧ `total_mv_yi >= CAP_FLOOR_YI`(**不含** buyable_c1)。
        也是 R 族 `n_sealed_dropped` 的分母口径。"""
        ok = np.ones(self.n, dtype=bool)
        if "is_st" in self.panel.columns:
            ok &= ~_as_bool(self.panel["is_st"])
        ok &= ~self.panel["ts_code"].astype(str).str.upper().str.endswith(".BJ").to_numpy()
        mv = self._num("total_mv_yi")
        ok &= (mv >= core.CAP_FLOOR_YI).fillna(False).to_numpy()
        return ok

    # -- 信号 → 面板行掩码 -----------------------------------------------------------------
    def mask(self, sig: pd.DataFrame) -> np.ndarray:
        """`sig[date, ts_code]`(会先去重)→ 与面板逐行对齐的 bool 掩码。"""
        empty = np.zeros(self.n, dtype=bool)
        if sig is None or len(sig) == 0:
            return empty
        s = sig[["date", "ts_code"]].dropna().drop_duplicates().copy()
        if s.empty:
            return empty
        s["_hit"] = True
        m = self.keys.merge(s, on=["date", "ts_code"], how="left")["_hit"]
        return m.notna().to_numpy(dtype=bool)

    def rows(self, mask: np.ndarray, *, x_pop: bool = False,
             extra: list[str] | None = None) -> pd.DataFrame:
        """掩码 + 人口门 + 收益非空 → 一格的观测表 `[date, code, gap_pp, rel_gap_pp] (+extra)`。"""
        sel = mask & (self.pop_base if x_pop else self.in_pop) & self.gap_ok
        cols = ["date", "code", "gap_pp", "rel_gap_pp"] + [c for c in (extra or [])
                                                           if c in self.panel.columns]
        out = self.panel.loc[sel, cols].copy()
        out["gap_pp"] = pd.to_numeric(out["gap_pp"], errors="coerce")
        out["rel_gap_pp"] = pd.to_numeric(out["rel_gap_pp"], errors="coerce")
        return out.reset_index(drop=True)

    def sealed(self, mask: np.ndarray) -> int:
        """因 D+1 不可买(封板/停牌)被剔的观测数 —— R 族必报(2026-08-08 判例)。"""
        return int((mask & self.pop_base & self.gap_ok & ~self.buyable).sum())


def _pack(family: str, *, kind: str, timing: str, tier: str | None, rows: pd.DataFrame,
          sample_kind: str = "event", notes: str = "", n_sealed_dropped: int = 0) -> dict:
    return {"family": family, "label": CELL_LABEL[family], "kind": kind, "timing": timing,
            "tier": tier, "rows": rows.reset_index(drop=True),
            "sample_kind": sample_kind, "notes": notes,
            "n_sealed_dropped": int(n_sealed_dropped)}


def _empty_rows(extra: list[str] | None = None) -> pd.DataFrame:
    cols = ["date", "code", "gap_pp", "rel_gap_pp"] + list(extra or [])
    return pd.DataFrame({c: pd.Series(dtype="float64" if "gap" in c else "object")
                         for c in cols})


def _kind(family: str) -> str:
    return "main" if family in MAIN_CELLS else "exploratory"


def _pit_filter(df: pd.DataFrame, scan_day_col: str) -> tuple[pd.DataFrame, int]:
    """事件族 PIT:只保留 `ann_date <= D` 的记录;`ann_date` 缺席的行**保留但计数**。

    设计稿 §3.2 要 `known_at ≤ decision_cutoff(D)`。湖里的事件表是「今天的快照」,拿不到
    历史 vintage —— 能做到的最强 PIT 就是这一条。丢掉无 `ann_date` 的行会凭空砍掉样本,
    留着又有前视嫌疑,所以两边都不猜:保留 + 计数,数字进 `notes`。
    """
    if df.empty or "ann_date" not in df.columns:
        return df, int(len(df))
    ann = df["ann_date"].astype(str)
    known = ann.str.fullmatch(r"\d{8}", na=False)
    ok = (~known) | (ann <= df[scan_day_col].astype(str))
    return df.loc[ok].copy(), int((~known).sum())


# ───────────────────────── F1 事件族(R;事件日 == D+2)─────────────────────────

_PERIOD = {"0331": "一季报", "0630": "中报", "0930": "三季报", "1231": "年报"}


def _f1a(ctx: _Ctx) -> dict:
    """F1a 财报披露前夜(主格)。

    条件:`disclosure_date.pre_date == D+2`。**用 `pre_date` 不用 `actual_date`** —— 预约
    披露日在 D 时点已经挂在交易所网站上,是 R 族事实;`actual_date` 是事后才知道的实际披露日,
    拿它建仓等于用明天的信息选今天的票。`actual_date` 只落成诊断列 `actual_date` /
    `on_time`,不参与选人。
    报告期(年报/中报/一季/三季)落成 `report_period` 诊断列 —— 设计稿提到的四个子格在本版
    以列的形式保留,不额外占格(总格数锁 16)。
    """
    fam = "F1a"
    df = ctx.events.get("disclosure")
    if df is None or df.empty or "pre_date" not in df.columns:
        return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=_empty_rows(),
                     notes=_MISSING.format(what="lake/events/disclosure_date 或 pre_date 列"))
    sig = df[df["pre_date"].astype(str).str.fullmatch(r"\d{8}", na=False)].copy()
    sig["date"] = sig["pre_date"].map(ctx.prev2)
    sig = sig[sig["date"].notna()]
    sig, n_no_ann = _pit_filter(sig, "date")
    keep = ["date", "ts_code"]
    if "end_date" in sig.columns:
        sig["report_period"] = sig["end_date"].astype(str).str.slice(4, 8).map(_PERIOD)
        keep.append("report_period")
    if "actual_date" in sig.columns:
        sig["on_time"] = sig["actual_date"].astype(str) == sig["pre_date"].astype(str)
        keep += ["actual_date", "on_time"]
    sig = sig[keep].drop_duplicates(subset=["date", "ts_code"], keep="first")
    mask = ctx.mask(sig)
    rows = ctx.rows(mask)
    rows = _attach(rows, ctx, sig, [c for c in keep if c not in ("date", "ts_code")])
    return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=rows,
                 n_sealed_dropped=ctx.sealed(mask),
                 notes=f"`pre_date == D+2`(预约披露日,D 已知);`actual_date` 仅诊断列。"
                       f"无 `ann_date` 无法证 PIT 的记录 {n_no_ann} 行(保留并计数)")


def _f1b(ctx: _Ctx) -> dict:
    """F1b 解禁前夜:`share_float.float_date == D+2` ∧ 解禁比例 ≥ 1%。

    同一 `(ts_code, float_date)` 的多个持有人行先按经济键去重、再**求和** `float_ratio`
    (一次解禁的总比例才是那件事的强度;逐行判 1% 会把一次 3% 的解禁拆成三次不达标)。
    """
    fam = "F1b"
    df = ctx.events.get("share_float")
    need = {"float_date", "float_ratio"}
    if df is None or df.empty or not need.issubset(df.columns):
        return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=_empty_rows(),
                     notes=_MISSING.format(what="lake/events/share_float 或 float_date/float_ratio"))
    sig = df[df["float_date"].astype(str).str.fullmatch(r"\d{8}", na=False)].copy()
    sig["date"] = sig["float_date"].map(ctx.prev2)
    sig = sig[sig["date"].notna()]
    sig, n_no_ann = _pit_filter(sig, "date")
    dedup = [c for c in ("ts_code", "float_date", "holder_name", "share_type", "float_share",
                         "float_ratio") if c in sig.columns]
    sig = sig.drop_duplicates(subset=dedup)
    sig["float_ratio"] = pd.to_numeric(sig["float_ratio"], errors="coerce")
    agg = sig.groupby(["date", "ts_code"], as_index=False)["float_ratio"].sum()
    hit = agg[agg["float_ratio"] >= FLOAT_RATIO_MIN_PCT]
    mask = ctx.mask(hit)
    rows = _attach(ctx.rows(mask), ctx, hit, ["float_ratio"])
    return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=rows,
                 n_sealed_dropped=ctx.sealed(mask),
                 notes=f"`float_date == D+2` ∧ Σ`float_ratio` ≥ {FLOAT_RATIO_MIN_PCT:.0f}%"
                       f";无 `ann_date` 的记录 {n_no_ann} 行(保留并计数)")


def _f1c(ctx: _Ctx) -> dict:
    """F1c 除权除息前夜:`dividend.ex_date == D+2`,gap 走 `core.adjust_ex_div` 机械校正。

    除权日开盘价天然带一个价格缺口,不校正的话这一格量的是「除权」而不是「隔夜」。校正需要
    `close(D+1)` 与 `open(D+2)`:面板只给 `close_d`,所以 `close(D+1)` 从面板**自己的 D+1 行**
    取,`open(D+2) = close(D+1) × (1 + gap/100)`(gap 的定义式反解,不另造口径)。
    `close(D+1)` 缺失 → 该观测无法校正,剔除并计数(`notes`),**不拿未校正值顶替**。
    """
    fam = "F1c"
    df = ctx.events.get("dividend")
    if df is None or df.empty or "ex_date" not in df.columns:
        return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=_empty_rows(),
                     notes=_MISSING.format(what="lake/events/dividend 或 ex_date 列"))
    sig = df[df["ex_date"].astype(str).str.fullmatch(r"\d{8}", na=False)].copy()
    if "div_proc" in sig.columns:
        sig = sig[sig["div_proc"].astype(str).str.contains("实施", na=False)]
    sig["date"] = sig["ex_date"].map(ctx.prev2)
    sig = sig[sig["date"].notna()]
    sig, n_no_ann = _pit_filter(sig, "date")
    for c in ("stk_div", "cash_div_tax"):
        sig[c] = pd.to_numeric(sig[c], errors="coerce").fillna(0.0) if c in sig.columns else 0.0
    dedup = [c for c in ("ts_code", "ex_date", "end_date", "stk_div", "cash_div_tax")
             if c in sig.columns]
    sig = sig.drop_duplicates(subset=dedup)
    agg = sig.groupby(["date", "ts_code"], as_index=False)[["stk_div", "cash_div_tax"]].sum()
    mask = ctx.mask(agg)
    rows = _attach(ctx.rows(mask, extra=["ts_code"]), ctx, agg, ["stk_div", "cash_div_tax"])
    if rows.empty:
        return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=rows,
                     n_sealed_dropped=ctx.sealed(mask), notes="`ex_date == D+2`(仅实施方案)")
    close_t1 = _close_next(ctx, rows)
    open_t2 = close_t1 * (1.0 + rows["gap_pp"].astype(float) / 100.0)
    adj = [core.adjust_ex_div(o, c, s, d) for o, c, s, d in
           zip(open_t2, close_t1, rows["stk_div"], rows["cash_div_tax"], strict=False)]
    rows["gap_raw_pp"] = rows["gap_pp"]
    rows["gap_pp"] = pd.to_numeric(pd.Series(adj, index=rows.index), errors="coerce")
    rows["rel_gap_pp"] = rows["rel_gap_pp"] + (rows["gap_pp"] - rows["gap_raw_pp"])
    n_uncorrectable = int(rows["gap_pp"].isna().sum())
    rows = rows[rows["gap_pp"].notna()].drop(columns=["ts_code"]).reset_index(drop=True)
    return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=rows,
                 n_sealed_dropped=ctx.sealed(mask),
                 notes=f"`ex_date == D+2`(仅实施方案);gap 已按 `(open×(1+stk_div)+cash_div_tax)"
                       f"/close − 1` 校正,原值留 `gap_raw_pp`;无法校正剔 {n_uncorrectable} 行;"
                       f"无 `ann_date` 的记录 {n_no_ann} 行")


def _close_next(ctx: _Ctx, rows: pd.DataFrame) -> pd.Series:
    """取每行 (D, ts_code) 对应的 `close(D+1)` —— 从面板自己的 D+1 行拿,不回湖。"""
    nxt = ctx.panel[["date", "ts_code"]].copy()
    nxt["close_t1"] = ctx.close_d.to_numpy()
    nxt["date"] = nxt["date"].map(ctx.prev1)          # D+1 行 → 挂到 D 上
    nxt = nxt[nxt["date"].notna()].drop_duplicates(subset=["date", "ts_code"])
    merged = rows[["date", "ts_code"]].merge(nxt, on=["date", "ts_code"], how="left")
    return pd.to_numeric(merged["close_t1"], errors="coerce")


def _attach(rows: pd.DataFrame, ctx: _Ctx, sig: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """把信号侧的诊断列贴回观测表(按 (date, ts_code) 左连;`rows` 只有 `code`,先补 ts_code)。"""
    cols = [c for c in cols if c in sig.columns]
    if rows.empty or not cols:
        return rows
    if "ts_code" not in rows.columns:
        m = ctx.panel[["date", "code", "ts_code"]].drop_duplicates(subset=["date", "code"])
        rows = rows.merge(m, on=["date", "code"], how="left")
        drop_ts = True
    else:
        drop_ts = False
    s = sig[["date", "ts_code", *cols]].drop_duplicates(subset=["date", "ts_code"])
    out = rows.merge(s, on=["date", "ts_code"], how="left")
    return out.drop(columns=["ts_code"]) if drop_ts else out


# ───────────────────────── F2 席位 / 资金族(R;join D 当日表)─────────────────────────

_TOP_INST_KEY = ("trade_date", "ts_code", "exalter", "buy", "sell", "net_buy")


def _top_inst_by_seat(ctx: _Ctx) -> pd.DataFrame | None:
    """`top_inst` → 每 (D, 票, 席位类) 的 Σ`net_buy`(元)/ 当日成交额(元)。

    **先按经济键去重再求和**(设计稿 §3.5):同一席位因 `reason`/`side` 多行展示不重复计钱;
    「机构专用」同名但金额不同的行是不同的匿名机构席,保留。
    `net_buy` 是**元**、面板 `amount_d` 是**千元** —— 比值前折算,忘了就是 1000 倍。
    """
    df = ctx.events.get("top_inst")
    if df is None or df.empty or "exalter" not in df.columns or "net_buy" not in df.columns:
        return None
    d = df.drop_duplicates(subset=[c for c in _TOP_INST_KEY if c in df.columns]).copy()
    uniq = d["exalter"].astype(str).unique()
    seat_of = {x: core.classify_seat(x, ctx.youzi) for x in uniq}
    d["seat"] = d["exalter"].astype(str).map(seat_of)
    d["net_buy"] = pd.to_numeric(d["net_buy"], errors="coerce").fillna(0.0)
    g = d.groupby(["trade_date", "ts_code", "seat"], as_index=False)["net_buy"].sum()
    g = g.rename(columns={"trade_date": "date"})
    amt = ctx.panel[["date", "ts_code"]].copy()
    amt["amount_yuan"] = ctx.amount_yuan.to_numpy()
    amt = amt.drop_duplicates(subset=["date", "ts_code"])
    g = g.merge(amt, on=["date", "ts_code"], how="left")
    g["ratio"] = g["net_buy"] / g["amount_yuan"].where(g["amount_yuan"] > 0)
    return g


def _f2_seat(ctx: _Ctx, fam: str, seat: str) -> dict:
    g = _top_inst_by_seat(ctx)
    if g is None:
        return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=_empty_rows(),
                     notes=_MISSING.format(what="lake/top_inst 或 exalter/net_buy 列"))
    sub = g[(g["seat"] == seat) & (g["ratio"] >= NET_BUY_RATIO_MIN)]
    mask = ctx.mask(sub)
    rows = _attach(ctx.rows(mask), ctx, sub, ["ratio", "net_buy"])
    return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=rows,
                 n_sealed_dropped=ctx.sealed(mask),
                 notes=f"D 日龙虎榜 Σ`{seat}` 席 `net_buy`(元)/ 当日成交额(元)"
                       f" ≥ {NET_BUY_RATIO_MIN:.0%};经济键去重先于求和")


def _f2c1(ctx: _Ctx) -> dict:
    """F2c1 北向·股通席净买 > 0(`top_inst` 的沪/深股通专用席)。"""
    fam = "F2c1"
    g = _top_inst_by_seat(ctx)
    if g is None:
        return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=_empty_rows(),
                     notes=_MISSING.format(what="lake/top_inst 或 exalter/net_buy 列"))
    sub = g[(g["seat"] == "north") & (g["net_buy"] > 0)]
    mask = ctx.mask(sub)
    rows = _attach(ctx.rows(mask), ctx, sub, ["net_buy", "ratio"])
    return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=rows,
                 n_sealed_dropped=ctx.sealed(mask),
                 notes="D 日龙虎榜沪/深股通专用席 Σ`net_buy` > 0(无金额门,只看方向)")


def _f2c2(ctx: _Ctx) -> dict:
    """F2c2 北向持股比例 5 日增幅当日前 1%(`hk_hold.ratio(D) − ratio(D−5)`)。

    ⚠️ 湖里现存的 `hk_hold` 分区只有 `*.HK`(南向港股持股),没有 A 股 `ratio` —— 与面板
    join 不上时本格照口径出空表并在 `notes` 记「数据缺席」,不静默变成「北向没动」。
    """
    fam = "F2c2"
    df = ctx.events.get("hk_hold")
    if df is None or df.empty or "ratio" not in df.columns:
        return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=_empty_rows(),
                     notes=_MISSING.format(what="lake/hk_hold 或 ratio 列"))
    d = df[["trade_date", "ts_code", "ratio"]].copy().rename(columns={"trade_date": "date"})
    d["ratio"] = pd.to_numeric(d["ratio"], errors="coerce")
    d = d.dropna(subset=["ratio"]).drop_duplicates(subset=["date", "ts_code"], keep="last")
    prev = d.copy()
    prev["date"] = prev["date"].map({v: k for k, v in ctx.back5.items()})
    prev = prev[prev["date"].notna()].rename(columns={"ratio": "ratio_p5"})
    j = d.merge(prev, on=["date", "ts_code"], how="inner")
    j["delta"] = j["ratio"] - j["ratio_p5"]
    hit = _top_pct_by_day(j, "delta", NORTH_DELTA_TOP_PCT)
    if hit.empty:
        return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=_empty_rows(),
                     notes=_MISSING.format(what="hk_hold 与面板无可 join 的 A 股 (D, D−5) 配对"))
    mask = ctx.mask(hit)
    rows = _attach(ctx.rows(mask), ctx, hit, ["delta"])
    return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=rows,
                 n_sealed_dropped=ctx.sealed(mask),
                 notes=f"`ratio(D) − ratio(D−5)` 当日横截面前 {NORTH_DELTA_TOP_PCT:.0%}"
                       f"(当日样本 < {MIN_CROSS_SECTION} 的日子不选)")


def _top_pct_by_day(df: pd.DataFrame, col: str, top_pct: float) -> pd.DataFrame:
    """逐日取 `col` 的前 `top_pct` 分位以上。当日横截面 < `MIN_CROSS_SECTION` → 该日不选
    (5 只票取「前 2%」等于取第一名,那不是「集中信号」而是「当日最大值」)。"""
    if df.empty:
        return df
    out = []
    for _day, g in df.groupby("date", sort=False):
        v = g[col].dropna()
        if len(v) < MIN_CROSS_SECTION:
            continue
        thr = float(v.quantile(1.0 - top_pct))
        out.append(g[g[col] >= thr])
    return pd.concat(out, ignore_index=True) if out else df.iloc[0:0]


def _f2d(ctx: _Ctx, side: str) -> dict:
    """F2d 大宗:按票日聚合 `block_trade`,成交价用金额加权 VWAP。

    `amount` 是**万元**、`vol` 是**万股** → `VWAP = Σamount / Σvol`(元/股,同表相除不换算),
    但 `Σamount` 与面板 `amount_d`(千元)比大小前必须双双折算到**元**。
    溢价格 = `VWAP ≥ close(D)`;折价格 = `VWAP ≤ close(D) × (1 − 5%)`;两格共用「总金额 ≥
    当日成交额 1%」的量门(不设量门的话一笔 50 万的大宗也算「大宗资金进场」)。
    """
    fam = "F2d1" if side == "premium" else "F2d2"
    df = ctx.events.get("block_trade")
    if df is None or df.empty or not {"amount", "vol"}.issubset(df.columns):
        return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=_empty_rows(),
                     notes=_MISSING.format(what="lake/block_trade 或 amount/vol 列"))
    d = df.copy()
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce")
    d["vol"] = pd.to_numeric(d["vol"], errors="coerce")
    g = (d.dropna(subset=["amount", "vol"]).groupby(["trade_date", "ts_code"], as_index=False)
         [["amount", "vol"]].sum().rename(columns={"trade_date": "date"}))
    g = g[g["vol"] > 0]
    g["vwap"] = g["amount"] / g["vol"]
    g["amount_yuan_blk"] = g["amount"] * WAN_TO_YUAN
    ref = ctx.panel[["date", "ts_code"]].copy()
    ref["amount_yuan"] = ctx.amount_yuan.to_numpy()
    ref["close_d"] = ctx.close_d.to_numpy()
    ref = ref.drop_duplicates(subset=["date", "ts_code"])
    g = g.merge(ref, on=["date", "ts_code"], how="left")
    big = g["amount_yuan_blk"] >= BLOCK_AMOUNT_RATIO_MIN * g["amount_yuan"]
    if side == "premium":
        sel, note = g["vwap"] >= g["close_d"], "VWAP ≥ close(D)"
    else:
        sel = g["vwap"] <= g["close_d"] * (1.0 - BLOCK_DISCOUNT_MIN)
        note = f"VWAP ≤ close(D) × (1 − {BLOCK_DISCOUNT_MIN:.0%})"
    sub = g[big.fillna(False) & sel.fillna(False)]
    mask = ctx.mask(sub)
    rows = _attach(ctx.rows(mask), ctx, sub, ["vwap", "amount_yuan_blk"])
    return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=rows,
                 n_sealed_dropped=ctx.sealed(mask),
                 notes=f"{note} ∧ 大宗总金额 ≥ 当日成交额 {BLOCK_AMOUNT_RATIO_MIN:.0%}"
                       "(万元/千元均已折算到元)")


def _f2e(ctx: _Ctx) -> dict:
    """F2e 超大单净流入占成交额当日前 2%(`moneyflow.buy_elg_amount − sell_elg_amount`)。

    `*_elg_amount` 是**万元**、面板 `amount_d` 是**千元**:两边都折算到元再相除。
    """
    fam = "F2e"
    df = ctx.events.get("moneyflow")
    need = {"buy_elg_amount", "sell_elg_amount"}
    if df is None or df.empty or not need.issubset(df.columns):
        return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=_empty_rows(),
                     notes=_MISSING.format(what="lake/moneyflow 或 buy/sell_elg_amount 列"))
    d = df[["trade_date", "ts_code", "buy_elg_amount", "sell_elg_amount"]].copy()
    d = d.rename(columns={"trade_date": "date"})
    for c in ("buy_elg_amount", "sell_elg_amount"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.drop_duplicates(subset=["date", "ts_code"], keep="last")
    d["elg_net_yuan"] = (d["buy_elg_amount"] - d["sell_elg_amount"]) * WAN_TO_YUAN
    ref = ctx.panel[["date", "ts_code"]].copy()
    ref["amount_yuan"] = ctx.amount_yuan.to_numpy()
    ref = ref.drop_duplicates(subset=["date", "ts_code"])
    d = d.merge(ref, on=["date", "ts_code"], how="inner")
    d["elg_ratio"] = d["elg_net_yuan"] / d["amount_yuan"].where(d["amount_yuan"] > 0)
    hit = _top_pct_by_day(d.dropna(subset=["elg_ratio"]), "elg_ratio", ELG_TOP_PCT)
    mask = ctx.mask(hit)
    rows = _attach(ctx.rows(mask), ctx, hit, ["elg_ratio"])
    return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=rows,
                 n_sealed_dropped=ctx.sealed(mask),
                 notes=f"超大单净流入 / 成交额 当日横截面前 {ELG_TOP_PCT:.0%}"
                       f"(当日样本 < {MIN_CROSS_SECTION} 的日子不选)")


# ───────────────────────── F3 涨停板族(X_ORACLE;join D+1 当日表)─────────────────────────

def _limit_at_next_day(ctx: _Ctx) -> pd.DataFrame | None:
    """`limit_list_d` → 挂到扫描日 D 的涨停表(**读 D+1 的行**)。

    D+1 的最终 `limit`/`last_time`/`fd_amount` 都是收盘后才存在的事实,所以整个 F3 族只能标
    `X_ORACLE`;把它们当 14:57 可知就是前视。反过来,读 D 当日的涨停表也是错的 —— 那量的是
    「今天涨停的票明晚怎么样」,不是设计稿要的「D+1 涨停 → D+1 尾盘挂单 → D+2 开盘卖」。
    """
    df = ctx.events.get("limit")
    if df is None or df.empty or "limit" not in df.columns:
        return None
    d = df.copy()
    d["date"] = d["trade_date"].map(ctx.prev1)              # D+1 → D
    d = d[d["date"].notna()]
    if d.empty:
        return None
    for c in ("fd_amount", "amount", "open_times", "limit_times"):
        d[c] = pd.to_numeric(d[c], errors="coerce") if c in d.columns else np.nan
    if "last_time" not in d.columns:
        d["last_time"] = None
    if "industry" not in d.columns:
        d["industry"] = None
    d["tier"] = [core.exec_tier(li, lt, ot, fd, am) for li, lt, ot, fd, am in
                 zip(d["limit"], d["last_time"], d["open_times"], d["fd_amount"], d["amount"],
                     strict=False)]
    return d.drop_duplicates(subset=["date", "ts_code"], keep="last")


def _f3(ctx: _Ctx, fam: str) -> dict:
    d = _limit_at_next_day(ctx)
    tiers = {"F3a": "T1", "F3b": "T0", "F3c": "T1", "F3e": "T2"}
    if d is None:
        return _pack(fam, kind=_kind(fam), timing="X", tier=tiers.get(fam),
                     rows=_empty_rows(), notes=_MISSING.format(
                         what="lake/limit_list_d 或 limit 列") + ";" + _X_NOTE)
    n_no_tier_input = int(d["amount"].isna().sum())
    lim, times = d["limit"].astype(str), d["limit_times"]
    if fam == "F3a":
        sub, note = d[(lim == "U") & (times == 1) & (d["tier"] == "T1")], "首板(`limit_times==1`)∧ T1"
    elif fam == "F3b":
        sub = d[lim == "Z"]
        note = ("`limit=='Z'`(收盘未封)= T0;⚠️ 湖里 Z 行的 `limit_times` 几乎恒空,"
                "「首板」这一限定**无法施加**,本格实为全部炸板")
    elif fam == "F3c":
        sub, note = d[(lim == "U") & (times >= 2) & (d["tier"] == "T1")], "`limit_times ≥ 2` ∧ T1"
    else:
        sub, note = d[(lim == "U") & (d["tier"] == "T2")], "其余 U(早封 / 一字 / 厚封单)= T2,只印"
    mask = ctx.mask(sub)
    rows = _attach(ctx.rows(mask, x_pop=True), ctx, sub, ["tier", "limit_times", "industry"])
    return _pack(fam, kind=_kind(fam), timing="X", tier=tiers.get(fam), rows=rows,
                 notes=f"{note};{_X_NOTE}"
                       + (f";缺 `amount` 无法定层的 D+1 行 {n_no_tier_input}" if n_no_tier_input
                          else ""))


def _f3d(ctx: _Ctx) -> dict:
    """F3d 板块共振增量:**同日配对** delta,不是两个不同日期集合的裸均值之差。

    共振 = D+1 同行业涨停(`limit=='U'`)家数 ≥ 3;孤板 = 恰 1 家。只在**同一天同时存在**共振组与
    孤板组时配对:共振成员的收益列减去当日孤板组的均值。`cell_stats` 先按日等权再跨日等权,所以
    这一列的日均值恰是当日 `mean(共振) − mean(孤板)`。
    ⚠️ 本格 `gap_pp` 是**增量**不是绝对收益,读数表里不能当作「隔夜能赚多少」;原值留 `gap_raw_pp`。
    """
    fam = "F3d"
    d = _limit_at_next_day(ctx)
    if d is None or d["industry"].isna().all():
        return _pack(fam, kind=_kind(fam), timing="X", tier=None, rows=_empty_rows(),
                     notes=_MISSING.format(what="lake/limit_list_d 或 industry 列(2026-07 前的"
                                                "窄表分区没有 industry)") + ";" + _X_NOTE)
    u = d[(d["limit"].astype(str) == "U") & d["industry"].notna()].copy()
    cnt = u.groupby(["date", "industry"], as_index=False).size().rename(columns={"size": "n_ind"})
    u = u.merge(cnt, on=["date", "industry"], how="left")
    reso, lone = u[u["n_ind"] >= RESONANCE_MIN_LIMITS], u[u["n_ind"] == LONE_LIMITS]
    r_rows = _attach(ctx.rows(ctx.mask(reso), x_pop=True), ctx, reso, ["industry", "n_ind"])
    l_rows = ctx.rows(ctx.mask(lone), x_pop=True)
    if r_rows.empty or l_rows.empty:
        return _pack(fam, kind=_kind(fam), timing="X", tier=None, rows=_empty_rows(),
                     notes="同日缺共振组或孤板组,无法配对;" + _X_NOTE)
    base = l_rows.groupby("date", as_index=False)[["gap_pp", "rel_gap_pp"]].mean()
    base = base.rename(columns={"gap_pp": "_lone_gap", "rel_gap_pp": "_lone_rel"})
    m = r_rows.merge(base, on="date", how="inner")
    n_unpaired = len(r_rows) - len(m)
    m["gap_raw_pp"] = m["gap_pp"]
    m["gap_pp"] = m["gap_pp"] - m["_lone_gap"]
    m["rel_gap_pp"] = m["rel_gap_pp"] - m["_lone_rel"]
    m = m.drop(columns=["_lone_gap", "_lone_rel"])
    return _pack(fam, kind=_kind(fam), timing="X", tier=None, rows=m,
                 notes=f"共振(同行业涨停 ≥ {RESONANCE_MIN_LIMITS})减**当日**孤板均值 = 同日配对增量;"
                       f"无孤板可配的共振观测剔 {n_unpaired};**本格数值是增量不是收益**;{_X_NOTE}")


# ───────────────────────── F4 冷门中间态(R;exploratory 对照基线)─────────────────────────

def _f4(ctx: _Ctx) -> dict:
    """F4:`pct_5d(D) ∈ [2, 5] pp` ∧ `turnover_rate(D) ≤ 当日中位` ∧ `vol20 ≤ 当日中位`
    ∧ 近 5 日无涨停。中位数在**候选人口(`in_pop`)当日横截面**上取。

    这是本普查唯一的宽族(`sample_kind="wide"`):它不是事件,是每天几百只票的常态人口,
    所以样本门走 `MIN_DAYS_WIDE` 而不是 `MIN_EVENTS`。设计稿明说它「因组合在看过附录后形成,
    不可列 confirmatory」—— 但按属主分工它仍是 6 个主格之一(对照基线),判读同表印出。
    """
    fam = "F4"
    need = ("pct_5d_pp", "turnover_rate", "vol20", "limit_5d")
    missing = [c for c in need if c not in ctx.panel.columns]
    if missing:
        return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=_empty_rows(),
                     sample_kind="wide",
                     notes=_MISSING.format(what="面板缺列 " + "/".join(missing)))
    p = ctx.panel
    pct5 = pd.to_numeric(p["pct_5d_pp"], errors="coerce")
    to = pd.to_numeric(p["turnover_rate"], errors="coerce")
    v20 = pd.to_numeric(p["vol20"], errors="coerce")
    lim5 = _as_bool(p["limit_5d"])
    pop = pd.Series(ctx.in_pop, index=p.index)
    med_to = to.where(pop).groupby(p["date"]).transform("median")
    med_v20 = v20.where(pop).groupby(p["date"]).transform("median")
    mask = ((pct5 >= F4_PCT5D_LO_PP) & (pct5 <= F4_PCT5D_HI_PP)
            & (to <= med_to) & (v20 <= med_v20)).fillna(False).to_numpy() & ~lim5
    rows = ctx.rows(mask)
    return _pack(fam, kind=_kind(fam), timing="R", tier=None, rows=rows, sample_kind="wide",
                 n_sealed_dropped=ctx.sealed(mask),
                 notes=f"`pct_5d ∈ [{F4_PCT5D_LO_PP:.0f}, {F4_PCT5D_HI_PP:.0f}]pp` ∧ 换手 ≤ 当日中位"
                       f" ∧ 20 日波动 ≤ 当日中位 ∧ 近 5 日无涨停;中位取自当日 `in_pop` 横截面")


# ───────────────────────── 入口 ─────────────────────────

def all_cells(panel: pd.DataFrame, events: dict[str, pd.DataFrame], *,
              youzi: set[str]) -> list[dict]:
    """16 格。返回顺序 = `CELL_ORDER`,每格一个 dict:

        {"family", "label", "kind": "main"|"exploratory", "timing": "R"|"X",
         "tier": "T0"|"T1"|"T2"|None, "rows": DataFrame[date, code, gap_pp, rel_gap_pp, ...],
         "sample_kind": "event"|"wide", "notes": str, "n_sealed_dropped": int}

    `rows` 已过人口门(F3 走 `pop_base`,其余走 `in_pop`)并剔掉收益缺失行;诊断列(比率 /
    行业 / 报告期…)按格附在后面,统计只读 `gap_pp` / `rel_gap_pp`。
    """
    miss = [c for c in REQUIRED_PANEL_COLS if c not in panel.columns]
    if miss:
        raise ValueError(f"面板缺必备列 {miss};contract 的 panel.build_panel 必须提供 "
                         f"{list(REQUIRED_PANEL_COLS)}")
    ctx = _Ctx(panel, events, youzi)
    cells = [
        _f1a(ctx), _f1b(ctx), _f1c(ctx),
        _f2_seat(ctx, "F2a", "youzi"), _f2_seat(ctx, "F2b", "inst"),
        _f2c1(ctx), _f2c2(ctx), _f2d(ctx, "premium"), _f2d(ctx, "discount"), _f2e(ctx),
        _f3(ctx, "F3a"), _f3(ctx, "F3b"), _f3(ctx, "F3c"), _f3d(ctx), _f3(ctx, "F3e"),
        _f4(ctx),
    ]
    got = [c["family"] for c in cells]
    if tuple(got) != CELL_ORDER:                       # 防「读完结果再加格」(设计稿 §3.6)
        raise AssertionError(f"格清单漂移:{got} != {list(CELL_ORDER)}")
    return cells


def exec_layer_split(cell_rows: pd.DataFrame, panel: pd.DataFrame, *,
                     family: str | None = None,
                     sample_kind: str = "event") -> tuple[dict, dict]:
    """F5 T+1 条件层:把一格的 `rows` 按面板的 `exec_ok` 分成 with / without 两份。

    `exec_ok` = `t1_pct_chg ≤ 3 ∧ t1_pos_in_range < 0.7 ∧ buyable_c1`,与 `scan/outcome.py`
    的**事后记账**定义锁相等 —— 它读的是 D+1 的收盘结果,所以 F5 整层是 X_ORACLE,
    **不能**说成「14:45 的过滤规则」。

    → `(with_cell, without_cell)`,两份都是可直接喂 `core.cell_stats` 的格 dict
    (`["rows"]` 即 frame)。面板没有 `exec_ok` 列 → 两份都空 + `notes` 留痕。
    """
    fam = family or "F5"
    base = {"kind": "exploratory", "timing": "X", "tier": None, "sample_kind": sample_kind,
            "n_sealed_dropped": 0}
    if cell_rows is None or len(cell_rows) == 0 or "exec_ok" not in panel.columns:
        note = ("面板缺 `exec_ok` 列" if "exec_ok" not in panel.columns else "上游格为空")
        return ({"family": f"{fam}·with", "label": "满足 T+1 弱收盘条件", "rows": _empty_rows(),
                 "notes": _MISSING.format(what=note), **base},
                {"family": f"{fam}·without", "label": "不满足", "rows": _empty_rows(),
                 "notes": _MISSING.format(what=note), **base})
    ok = panel[["date", "code", "exec_ok"]].copy()
    ok["exec_ok"] = _as_bool(ok["exec_ok"])
    ok = ok.drop_duplicates(subset=["date", "code"])
    j = cell_rows.merge(ok, on=["date", "code"], how="left", suffixes=("", "_panel"))
    col = j["exec_ok_panel"] if "exec_ok_panel" in j.columns else j["exec_ok"]
    flag = _as_bool(col)
    note = "`exec_ok` = t1_pct_chg ≤ 3 ∧ t1_pos_in_range < 0.7 ∧ buyable_c1(事后记账口径)"
    return ({"family": f"{fam}·with", "label": "满足 T+1 弱收盘条件",
             "rows": j.loc[flag].reset_index(drop=True), "notes": note, **base},
            {"family": f"{fam}·without", "label": "不满足 T+1 弱收盘条件",
             "rows": j.loc[~flag].reset_index(drop=True), "notes": note, **base})

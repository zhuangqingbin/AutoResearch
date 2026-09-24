#!/usr/bin/env python3
"""反事实人口(G3)—— 被拒绝的票也要有「事后」(确定性、零 LLM、零网络、**只记不学**)。

design: `docs/specs/2026-08-28-ruler-retention-optimization-brainstorm.md` §2.4 G3 + §1.4。

## 病灶

结果账本(`scan/outcome.py`)只记 finalist / 📌 / BUY 三种角色,于是复盘时最想问的那句
「**没被推荐的赢家在哪一级被丢的**」根本算不出来:bench 没有事后读数、pass1_cut 没有、
L2 没有、L1 没有、L0 未召回的赢家更没有。§1.4 想给每一级配一把 primary KPI(L1 的
Recall@1000、L2 的召回保持率、L3 的 finalist−bench、L4 的拒绝价值、E6 的池内选择、
整链的 abstention value),而这些 KPI 的**分母全部在账本外面**。

本模块只补分母,不补判断:

- **只记不学**:不回注 prompt、不改门/权重/评级/prompt,产物只被人和 `chain_view` 读;
- 口径不另造:前向收益、可交易折叠、家族日统计全部走 `research.edge_census` /
  `common.ruler` / `scan.outcome` 的**同一份实现**,区间走 `common.stats` 的
  `date_cluster_bootstrap`,**不复制第二套**;
- 所有「拒绝价值 / 空仓正确性」读数都是 **observational**:家族不是随机分配的,
  负号只说明「被否的那批事后确实更差」,**不构成「否决造成了更好结果」的因果主张**。

## 只读冻结副本(这条是硬的)

来源优先级:`trace/staging/` → `capsule/products/staging/` → `trace/`。三者全在 run 目录内、
受 `MANIFEST.sha256` / 脱钩 ROOT 覆盖,发布后不再变。**绝不读共享 staging
`context_<engine>/scan/<date>/`** —— 同数据日重跑会原地覆盖它:实测 2026-08-13/08-18 两天
brief 印的是 BLOCKED,而共享 staging 的决策文件被后来的影子回放改写成 `buys=[688766]`。
账本 `src` 列为此存在;本模块干脆不给自己留那条后路(`outcome.run_facts` 的第三级回退在
这里被**刻意删掉**)。

`trace/` 这一级是与设计稿的一处**仓库现实偏离**:设计稿只写了 `trace/staging` 与
`capsule/products/staging`,但实测 08-26 之前的已发布 run(如 `20260825_2149`)没有
`trace/staging/`,`L1_scored_full.csv` / `L1_recall_top1000.csv` 只存在于 `trace/` 白名单
副本里 —— 那里同样在 run 目录内、同样被 MANIFEST 覆盖,是冻结的。不认它 = 一半历史 run
拿不到 L0/L1 人口。

## 两层人口

    $RPT/scan/_ledger/universe/<analysis_date>.parquet     # L0 eligible 最小表(L1 召回率的分母)
    $RPT/scan/_ledger/populations/<run_key>.parquet        # L1 top1000 完整路径表(L2–E6)
    $RPT/scan/_ledger/views/stage_rulers.csv               # 逐级 KPI(long schema)

`L1_scored_full.csv` 是**全部 L0 过门股**(实测 4315 行),不是 1000 行;top1000 是
`L1_recall_top1000.csv`。两者行数分别就是两张表的行数(验收断言之一)。

## 正交 flags,不是互斥 role

`recommendations.csv` 的 `role` 把阶段、来源、动作揉成一列(finalist / pinned / BUY 三选一),
于是**一只同时是 finalist + 📌 + BUY 的票只剩一个身份**。这里改成一组正交布尔:
`in_l1,in_l2,pass1_kept,l3_judged,is_finalist,is_bench,l4_dispatched,l4_rejected,
e6_candidate,e6_eligible,is_buy,is_pinned,is_composite_seat`,外加 `terminal_stage`
(走到的最深一级)与 `terminal_disposition`(在那一级的去留)。

**`UNKNOWN` 不等于 `False`**:老 run 只有 finalists 的,`in_l2`/`pass1_kept` 是
`pd.NA`(pandas 可空 `boolean`),不是 `False` —— 折叠成 False 等于宣称「我们知道它没进
L2」,而事实是**没人留下过那份证据**。缺源一律 `null` + 记进 sidecar 的 `missing[]`,
**不猜、不拿后来的共享 staging 补**。

## 分尺成熟,互不阻塞

`outcome.py` 用一枚总的 `complete` 判成熟,D+2 一到就永久跳过重算 —— `fwd_5_oc`/`fwd_10_oc`
因此被永久冻成缺失(§2.6 最后一行的账)。这里每个指标带**自己**的
`status ∈ {PENDING, MATURE, UNAVAILABLE}` 与 `matures_on`:

| 指标 | 成熟日 | 为什么 |
|---|---|---|
| `exec_ok` | D+1 | 只用 T+1 盘口(涨幅/收盘位置/封板) |
| `gap_c1_o2` `fwd_1_oo` `fwd_2_oc` `ret_c1_c2` `rel_*` `excess_med_market` | D+2 | 卖腿在 T+2 |
| `fwd_5_oc` | D+5 | 参考尺,只观察 |
| `fwd_10_oc` | D+10 | 同上 |

`PENDING`(湖还没走到那天)与 `UNAVAILABLE`(那天在湖里、这只票没数:停牌/退市/新股)
是**两件事**,不许折叠成一个「缺失」。

  uv run --no-sync python -m autoresearch.scan.populations build [--limit N] [--run 20260826_2120]
  uv run --no-sync python -m autoresearch.scan.populations rulers
  uv run --no-sync python -m autoresearch.scan.populations size [--run 20260826_2120]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import (
    forward_returns as _fwd,
    ruler as _ruler,
    stats as _stats,
    workspace as ws,
)
from autoresearch.data import market_panel as _panel

# 只为 `MIN_CROSS_SECTION` 这一个研究阈值(它的 owner 仍是 edge_census);
# 收益与湖读取已在 E4 改走 common/data,这条边留在 E5 的残余台账里。
from autoresearch.research import edge_census as _ec
from autoresearch.scan import outcome as _outcome

POPULATION_SCHEMA_VERSION = 1
#: 口径版本 —— 随**定义**改动而升,不随实现重构升。`stage_rulers.csv` 每行都带它,
#: 于是「同一个 metric 名在不同日期下含义变了」这件事在表里是看得见的。
METRIC_DEFINITION_VERSION = "g3.v1"

LEDGER_DIRNAME = _outcome.LEDGER_DIRNAME
UNIVERSE_DIRNAME = "universe"
POPULATIONS_DIRNAME = "populations"
VIEWS_DIRNAME = "views"
STAGE_RULERS_CSV = "stage_rulers.csv"

MAIN = _ruler.MAIN_RULER

#: run 目录内的冻结副本,按优先级。**共享 staging 不在此列,也永远不会加进来**
#: (同数据日重跑会原地覆盖它 —— 见模块 docstring)。
FROZEN_BASES = ("trace/staging", "capsule/products/staging", "trace")

#: 每个事后指标的成熟 horizon(交易日数)。键即 parquet 里的列名。
#: `exec_ok` 只要 T+1 盘口 → D+1;gap 家族卖腿在 T+2 → D+2;两把参考尺按自己的窗口。
RULER_HORIZON: dict[str, int] = {
    "exec_ok": 1,
    MAIN: 2,              # gap_c1_o2:T+1 收买 → T+2 开卖(2026-08-05 用户裁定的主尺)
    "fwd_1_oo": 2,        # o1→o2(设计稿 §1.2 的 "o1o2")
    "fwd_2_oc": 2,        # o1→c2(设计稿 "o1c2";2026-07-10 旧主尺,降参考)
    "ret_c1_c2": 2,       # c1→c2(设计稿 "c1c2";factor_lab 没有这一列,本模块现算)
    _ruler.REL_MARKET: 2,
    _ruler.REL_SECTOR: 2,
    "excess_med_market": 2,
    "fwd_5_oc": 5,
    "fwd_10_oc": 10,
}
#: universe 表是**最小表**(逐日 4000+ 行 × 数百日),只带三把尺;完整十列在 populations。
UNIVERSE_RULERS = (MAIN, "fwd_5_oc", "fwd_10_oc")

PENDING, MATURE, UNAVAILABLE = "PENDING", "MATURE", "UNAVAILABLE"

#: 正交身份旗(pandas 可空 `boolean`:True / False / pd.NA=UNKNOWN)。
FLAG_COLUMNS = (
    "in_l1", "in_l2", "pass1_kept", "l3_judged", "is_finalist", "is_bench",
    "l4_dispatched", "l4_rejected", "e6_candidate", "e6_eligible", "is_buy",
    "is_pinned", "is_composite_seat",
)

#: L4 「否决」的评级面(早停是另一面)。与 `edge_census.RATING_GE_OW` 互补,不重叠。
REJECT_RATINGS = frozenset({"Underweight", "Sell"})
COMPARABLE_RATINGS = frozenset({"Hold", "Overweight", "Buy"})

#: `l3/merge.py::COMPOSITE_SEAT_GUARD` 的字面量(那里是唯一生产者)。
COMPOSITE_SEAT_GUARD = "composite_seat"

#: 事后主尺「赢家」= 当日可买截面的 top decile(§1.4 L1 行:top-decile 赢家)。
WINNER_DECILE = 0.9
#: 截面不足就不算(与 `edge_census.MIN_CROSS_SECTION` 同一把门,不另立)。
MIN_CROSS_SECTION = _ec.MIN_CROSS_SECTION


# ───────────────────────── 冻结副本读取 ─────────────────────────

class FrozenSources:
    """一次 run 的**冻结**产物定位器。三级都在 run 目录内;共享 staging 不在候选里。"""

    def __init__(self, run_dir: Path | str):
        self.run = Path(run_dir)
        self.bases = [self.run / rel for rel in FROZEN_BASES]
        self.manifest = self._json_at(self.run, "manifest.json") or {}
        self.origin: dict[str, str] = {}

    @staticmethod
    def _json_at(base: Path, name: str) -> object | None:
        path = base / name
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def find(self, *names: str) -> Path | None:
        """按 base 优先级找第一个存在的文件(`names` 是同一件东西的历代别名)。"""
        for base in self.bases:
            for name in names:
                path = base / name
                if path.is_file():
                    return path
        return None

    def rows(self, *names: str) -> list[dict] | None:
        """CSV → 行字典列表;**全部走 stdlib csv 读字符串**。

        不用 pandas:它的类型推断会把 `001283` 读成 `1283`(实测),六位代码的前导零一丢,
        整张表的 join 就全错位 —— 而且错得静默。
        """
        path = self.find(*names)
        if path is None:
            return None
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                got = list(csv.DictReader(handle))
        except (OSError, UnicodeDecodeError):
            return None
        self.origin[names[0]] = self._label(path)
        return got

    def json(self, *names: str) -> object | None:
        path = self.find(*names)
        if path is None:
            return None
        doc = self._json_at(path.parent, path.name)
        if doc is not None:
            self.origin[names[0]] = self._label(path)
        return doc

    def _label(self, path: Path) -> str:
        try:
            return path.relative_to(self.run).parent.as_posix()
        except ValueError:                      # 不该发生;真发生了如实报绝对路径
            return str(path.parent)

    # ── 身份(G0 canonical identity 的**过渡实现**)──────────────────────────
    @property
    def report_dir_id(self) -> str:
        return self.run.name

    @property
    def capsule_run_id(self) -> str:
        return str(self.manifest.get("run_id") or "")

    @property
    def analysis_date(self) -> str:
        return str(self.manifest.get("analysis_date") or "")

    @property
    def identity_quality(self) -> str:
        """有 capsule contract run_id → `full`;只有目录名 → `legacy`(G0 的措辞)。"""
        return "full" if self.capsule_run_id else "legacy"


def run_key(run_dir: Path | str) -> str:
    """产物文件名的主键。

    设计稿 G0 的 canonical identity 是 `(engine, capsule_run_id)`,但 G0 尚未实施(另一批)。
    **仓库现实优先**:这里用 `report_dir_id`(=`YYYYMMDD_HHMM` 目录名)做文件名 —— 它是
    `published_runs()` / `chain_view` / `recommendations.csv` 现在真正在用的那把钥匙,可排序、
    对人可读;`capsule_run_id` 与 `identity_quality` 作为**列**一起落表,G0 落地后可无损重键。
    """
    return Path(run_dir).name


def _z6(value: object) -> str:
    """`600188.SS` / `2345` / `1283` → 六位。前导零只有这一道防线。"""
    return str(value or "").strip().split(".")[0].zfill(6)


def _num(value: object) -> float | None:
    try:
        got = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return None if math.isnan(got) else got


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "是"}


# ───────────────────────── 湖 → 全尺帧 + 逐尺成熟 ─────────────────────────

def ruler_frame(date: str, *, lake_daily: Path | None = None,
                sectors: dict[str, str] | None = None) -> tuple[pd.DataFrame | None, dict]:
    """当日**全湖**十列事后指标 + 每列的 `status`/`matures_on`。

    与 `outcome.market_frame` 的关键差别:**未成熟不返回 None**。account 的老写法在
    D+2 没落湖时整份跳过,于是 `fwd_5/10` 永远等不到自己的成熟日(§2.6 那条账)。这里
    只要 D 本身在湖里就出帧,成熟与否由逐列 `status` 说话。

    收益口径全部来自 `common.forward_returns.forward_frame`(与 `factor_lab` 同一实现,
    含 `|gap|>GAP_CLIP` 的数据错剔除);三个相对列走 `outcome._relative_columns`
    ——**列名即口径**,分母各不相同,这里不重新推导。
    """
    days = _panel.lake_trade_days(lake_daily)
    day = str(date).replace("-", "")
    if day not in days:
        return None, {"reason": "NOT_IN_LAKE", "analysis_date": date}
    idx = days.index(day)
    horizon = max(RULER_HORIZON.values())
    window = days[max(0, idx - 1): min(len(days), idx + horizon + 2)]
    pivots = _panel.load_lake_pivots(window, lake_daily)
    frame = _fwd.forward_frame(pivots, days, day)
    if frame is None or frame.empty:
        return None, {"reason": "EMPTY_FORWARD_FRAME", "analysis_date": date}

    close, open_, high, low, pct = (pivots.get(k) for k in
                                    ("close", "open", "high", "low", "pct_chg"))
    d1 = days[idx + 1] if idx + 1 < len(days) else None
    d2 = days[idx + 2] if idx + 2 < len(days) else None

    def series(pivot: pd.DataFrame | None, column: str | None) -> pd.Series:
        if pivot is None or column is None or column not in pivot.columns:
            return pd.Series(np.nan, index=frame.index, dtype=float)
        return pd.to_numeric(pivot[column], errors="coerce").reindex(frame.index)

    c1, c2 = series(close, d1), series(close, d2)
    # c1→c2(设计稿的 "c1c2"):factor_lab 没有这一列,现算。**不另加 clip** —— 全仓
    # 只保留 `forward_frame` 对主尺那一条剔除规则,免得同一张表里存在两套数据错口径。
    frame["ret_c1_c2"] = c2 / c1 - 1.0
    frame["t1_open"] = series(open_, d1)
    frame["t1_high"] = series(high, d1)
    frame["t1_low"] = series(low, d1)
    frame["t1_close"] = c1
    frame["t1_pct_chg"] = series(pct, d1)
    frame["t2_open"] = series(open_, d2)
    span = frame["t1_high"] - frame["t1_low"]
    frame["t1_pos_in_range"] = ((frame["t1_close"] - frame["t1_low"]) / span).where(span > 0)

    rel = _outcome._relative_columns(frame, sectors or {})
    for column in (_ruler.REL_MARKET, _ruler.REL_SECTOR, "excess_med_market"):
        frame[column] = rel[column]

    # `exec_ok` = A4 执行线(阈值常量直接引 `outcome`,不复制数字)。可买性未知 → NaN,
    # 不折成 False:那是「不知道」,不是「不可执行」。
    buyable = _ruler.entry_tradable(frame, ruler_name=MAIN)
    ok = (buyable
          & (frame["t1_pct_chg"] <= _outcome.EXEC_MAX_PCT_1D)
          & (frame["t1_pos_in_range"] < _outcome.EXEC_MAX_POS_IN_RANGE))
    frame["exec_ok"] = ok.astype(float).where(frame["t1_pct_chg"].notna()
                                              & frame["t1_pos_in_range"].notna())
    frame["buyable_c1"] = buyable

    meta = {
        "analysis_date": date, "day": day, "n_lake_days": len(days), "idx": idx,
        "matures_on": {column: (days[idx + k] if idx + k < len(days) else None)
                       for column, k in RULER_HORIZON.items()},
        "lake_reached": {column: bool(idx + k < len(days))
                         for column, k in RULER_HORIZON.items()},
        "n": int(len(frame)),
    }
    return frame, meta


def _status_column(values: pd.Series, reached: bool) -> pd.Series:
    """逐票三态。

    `PENDING` = 湖还没走到成熟日(会变);`UNAVAILABLE` = 成熟日在湖里、这只票就是没数
    (停牌/退市/新股,不会变);`MATURE` = 有数。把前两者折成一个「缺失」= 每天重算
    整段历史,或者永远等一个不会来的值。
    """
    if not reached:
        return pd.Series(PENDING, index=values.index, dtype=object)
    return pd.Series(np.where(values.notna(), MATURE, UNAVAILABLE),
                     index=values.index, dtype=object)


def _attach_outcomes(table: pd.DataFrame, frame: pd.DataFrame | None, meta: dict,
                     rulers: tuple[str, ...]) -> pd.DataFrame:
    """把事后指标 + 每指标的 `status`/`matures_on` 贴到人口表(按 `code` 对齐)。"""
    index = pd.Index(table["code"], name="code")
    for column in rulers:
        if frame is None or column not in frame.columns:
            table[column] = pd.Series(np.nan, index=table.index, dtype=float)
            table[f"status_{column}"] = UNAVAILABLE
            table[f"matures_on_{column}"] = None
            continue
        values = pd.to_numeric(frame[column], errors="coerce").reindex(index)
        table[column] = values.to_numpy()
        table[f"status_{column}"] = _status_column(
            values, bool(meta.get("lake_reached", {}).get(column))).to_numpy()
        table[f"matures_on_{column}"] = (meta.get("matures_on", {}) or {}).get(column)
    if frame is not None and "buyable_c1" in frame.columns:
        buyable = _ruler.entry_tradable(frame, ruler_name=MAIN).reindex(index)
        table["buyable_c1"] = pd.array(buyable.to_numpy(), dtype="boolean")
    else:
        table["buyable_c1"] = pd.array([None] * len(table), dtype="boolean")
    return table


# ───────────────────────── L0 eligible 最小表 ─────────────────────────

def build_universe(run_dir: Path | str, *,
                   lake_daily: Path | None = None) -> tuple[pd.DataFrame | None, dict]:
    """`L1_scored_full.csv`(= 全部 L0 过门股)→ L1 召回率的分母表。

    `in_l1` 的**权威来源是 `L1_recall_top1000.csv` 的成员集**,不是 scored_full 里的
    `recalled` 列 —— 后者是生产者自己的旗,前者才是 L1 真正交给下游的那 1000 行。两者
    都缺才回落到 `recalled`,并记进 `missing[]`。
    """
    src = FrozenSources(run_dir)
    date = src.analysis_date
    scored = src.rows("L1_scored_full.csv")
    missing: list[str] = []
    if scored is None:
        return None, {"reason": "NO_L1_SCORED_FULL", "run_key": run_key(run_dir),
                      "analysis_date": date, "missing": ["L1_scored_full.csv"]}
    top1000 = src.rows("L1_recall_top1000.csv")
    recalled = {_z6(row.get("code")) for row in top1000} if top1000 is not None else None
    if recalled is None:
        missing.append("L1_recall_top1000.csv")

    sectors = {_z6(row.get("code")): str(row.get("industry") or "") for row in scored}
    frame, meta = ruler_frame(date, lake_daily=lake_daily, sectors=sectors)
    if frame is None:
        missing.append(f"lake:{meta.get('reason')}")

    table = pd.DataFrame({
        "code": [_z6(row.get("code")) for row in scored],
        "sector": [str(row.get("industry") or "") or None for row in scored],
        "composite": [_num(row.get("composite")) for row in scored],
        "rank": [_num(row.get("rank")) for row in scored],
    })
    if recalled is not None:
        table["in_l1"] = pd.array([code in recalled for code in table["code"]], dtype="boolean")
    else:
        table["in_l1"] = pd.array(
            [_truthy(row.get("recalled")) if "recalled" in row else None for row in scored],
            dtype="boolean")
    table["analysis_date"] = date
    table["run_key"] = run_key(run_dir)
    table = _attach_outcomes(table, frame, meta, UNIVERSE_RULERS)
    n_duplicate = len(table) - table["code"].nunique()
    table = table.drop_duplicates("code", keep="first")
    table = table.sort_values("code", kind="stable").reset_index(drop=True)
    return table, {
        "schema_version": POPULATION_SCHEMA_VERSION, "kind": "universe",
        "analysis_date": date, "run_key": run_key(run_dir),
        "report_dir_id": src.report_dir_id, "capsule_run_id": src.capsule_run_id,
        "identity_quality": src.identity_quality,
        "n_rows": int(len(table)), "n_in_l1": int(table["in_l1"].sum(skipna=True)),
        "n_source_rows": len(scored),
        "counts": {"duplicate_source_rows": int(n_duplicate)},
        "origin": dict(src.origin), "missing": sorted(set(missing)),
        "lake_reached": meta.get("lake_reached", {}),
        "matures_on": meta.get("matures_on", {}),
    }


# ───────────────────────── L1 top1000 路径表 ─────────────────────────

def _pinned_codes(finalist_rows: list[dict] | None, l1_rows: list[dict] | None) -> set[str]:
    """📌 保送票。判据与 `edge_census.pinned_codes` 逐字同义(`lane=="pinned"` 或
    `pinned_note` 非空),外加 L1 侧的 `pinned` 列 —— 两代标记都认。"""
    pinned: set[str] = set()
    for row in finalist_rows or []:
        if str(row.get("lane") or "").strip() == "pinned" or str(row.get("pinned_note") or "").strip():
            pinned.add(_z6(row.get("code") or row.get("ticker")))
    for row in l1_rows or []:
        if _truthy(row.get("pinned")) or str(row.get("pinned_note") or "").strip():
            pinned.add(_z6(row.get("code")))
    return pinned


def _passport_index(src: FrozenSources) -> tuple[dict[str, dict], bool]:
    doc = src.json("_candidate_passport.json")
    if not isinstance(doc, dict) or not isinstance(doc.get("candidates"), dict):
        return {}, False
    return {_z6(code): entry for code, entry in doc["candidates"].items()
            if isinstance(entry, dict)}, True


def _l4_facts(src: FrozenSources) -> tuple[dict[str, str] | None, dict[str, dict], list[str]]:
    """L4 评级 + 早停 —— **只走 `decision_read_model` 正门**,不解析卡片自然语言。

    仓库现实:08-26 之前的 run 的 `trace/` 白名单里**没有** `_final_ratings.json`,
    只有 `decision_records.json`(L4 的结构化事实簿)。直接读 legacy JSON = 半年历史
    全部无评级、`l4_reject_value_*` 恒为空。`read_final_ratings` 的优先级(事实簿 →
    legacy → 卡回退)正是为这件事写的,照用。
    """
    from autoresearch.scan.decision_read_model import read_decisions, read_final_ratings

    base = src.find("decision_records.json", "_final_ratings.json")
    if base is None:
        return None, {}, ["decision_records.json"]
    directory = base.parent
    src.origin["decision_records.json"] = src._label(base)
    missing: list[str] = []
    try:
        ratings = {_z6(code): str(value) for code, value in read_final_ratings(directory).items()}
    except Exception:                        # noqa: BLE001 — 坏事实簿 = 该 run 无评级,**记账**不静默吞
        return None, {}, ["decision_records.json:UNREADABLE"]
    early: dict[str, dict] = {}
    if (directory / "decision_records.json").is_file():
        try:
            for code, record in read_decisions(directory).items():
                stop = record.early_stop
                if isinstance(stop, dict):
                    early[_z6(code)] = stop
        except Exception:                    # noqa: BLE001
            missing.append("decision_records.json:early_stop")
    legacy = src.json("_early_stop.json")
    if isinstance(legacy, dict):
        for code, value in legacy.items():
            if isinstance(value, dict):
                early.setdefault(_z6(code), value)
    return ratings, early, missing


def _e6_index(src: FrozenSources) -> tuple[dict[str, dict], set[str], dict[str, object], bool]:
    doc = src.json("_relative_buy_decision.json")
    if not isinstance(doc, dict):
        return {}, set(), {}, False
    buys_list = [b for b in (doc.get("buys") or []) if isinstance(b, dict) and b.get("code")]
    buys = {_z6(b.get("code")) for b in buys_list}
    # `tiers`:A(card-backed)/R(relative_forced)(Task 19,2026-09-24 §2.7)。
    tiers = {_z6(b.get("code")): b.get("tier") for b in buys_list}
    cand = {_z6(c.get("code")): c for c in (doc.get("candidates") or [])
            if isinstance(c, dict) and c.get("code")}
    return cand, buys, tiers, True


def _terminal(flags: dict[str, object]) -> tuple[str, str]:
    """走到的最深一级 + 在那一级的去留。**只认能证明的**:`pd.NA` 不推进也不判死。"""
    def yes(name: str) -> bool:
        return flags.get(name) is True

    if yes("e6_candidate"):
        if yes("is_buy"):
            return "E6", "BUY"
        return "E6", ("ELIGIBLE_NOT_BOUGHT" if yes("e6_eligible") else "INELIGIBLE")
    if yes("l4_dispatched"):
        if yes("l4_rejected"):
            return "L4", "REJECTED"
        return "L4", ("CARDED" if flags.get("research_rating") else "DISPATCHED_NO_CARD")
    if yes("l3_judged") or yes("is_finalist"):
        return "L3", ("FINALIST" if yes("is_finalist") else "BENCH")
    if yes("pass1_kept"):
        return "PASS1", "KEPT"
    if yes("in_l2"):
        return "L2", ("CUT_AT_PASS1" if flags.get("pass1_kept") is False else "UNKNOWN")
    if yes("in_l1"):
        return "L1", ("CUT_AT_L2" if flags.get("in_l2") is False else "UNKNOWN")
    return "L0", "UNKNOWN"


def build_population(run_dir: Path | str, *,
                     lake_daily: Path | None = None) -> tuple[pd.DataFrame | None, dict]:
    """`L1_recall_top1000.csv` + 护照 + 任务簿 + 卡 + E6 → 一只票的完整路径 + 事后读数。

    行集合 = top1000 全体(`in_l1=True`)**加上**下游冒出来的孤儿(实测早期 run 有
    finalist/评级不在当日 L1 里)。孤儿以 `in_l1=False` 入表并计入 `counts.orphans`,
    不静默丢 —— 一只被 BUY 过的孤儿如果不进表,`e6_buy_minus_pool` 就少了它。
    """
    src = FrozenSources(run_dir)
    date = src.analysis_date
    key = run_key(run_dir)
    l1_rows = src.rows("L1_recall_top1000.csv")
    missing: list[str] = []
    if l1_rows is None:
        return None, {"reason": "NO_L1_TOP1000", "run_key": key, "analysis_date": date,
                      "missing": ["L1_recall_top1000.csv"]}

    l2_rows = src.rows("L2_gbdt_top200.csv")
    kept_rows = src.rows("_l3_pass1_kept.csv")
    cut_rows = src.rows("_l3_pass1_cut.csv")
    judged_rows = src.rows("L3_judged_full.csv")
    finalist_rows = src.rows("L3_fine_finalists.csv", "finalists.csv")
    bench_rows = src.rows("_l3_bench.csv")
    ratings, early, l4_missing = _l4_facts(src)
    missing += l4_missing
    tasks_doc = src.json("_l4_tasks.json")
    passport, has_passport = _passport_index(src)
    e6_cand, e6_buys, e6_tiers, has_e6 = _e6_index(src)

    for name, present in (("L2_gbdt_top200.csv", l2_rows is not None),
                          ("_l3_pass1_kept.csv", kept_rows is not None),
                          ("L3_judged_full.csv", judged_rows is not None),
                          ("finalists.csv", finalist_rows is not None),
                          ("_l4_tasks.json", isinstance(tasks_doc, dict)),
                          ("_relative_buy_decision.json", has_e6),
                          ("_candidate_passport.json", has_passport)):
        if not present:
            missing.append(name)

    l2_set = {_z6(r.get("code")) for r in l2_rows} if l2_rows is not None else None
    kept_set = {_z6(r.get("code")) for r in kept_rows} if kept_rows is not None else None
    cut_set = {_z6(r.get("code")) for r in cut_rows} if cut_rows is not None else None
    judged = ({_z6(r.get("code")): r for r in judged_rows}
              if judged_rows is not None else None)
    finalists = ({_z6(r.get("code") or r.get("ticker")): r for r in finalist_rows}
                 if finalist_rows is not None else None)
    bench_set = {_z6(r.get("code")) for r in bench_rows} if bench_rows is not None else None
    dispatched_set: set[str] | None = None
    if isinstance(tasks_doc, dict) and isinstance(tasks_doc.get("tasks"), dict):
        dispatched_set = {_z6(c) for c in tasks_doc["tasks"]}
    pinned = _pinned_codes(finalist_rows, l1_rows)
    # 「知不知道谁是 📌」本身也是三态:两个来源都没有 pinned 记号时,`is_pinned` 是
    # UNKNOWN 而不是 False —— 老 run 的 top1000 没有 `pinned` 列,写 False 等于替一份
    # 不存在的证据作证。
    pinned_known = (finalist_rows is not None
                    or any("pinned" in row or "pinned_note" in row for row in l1_rows[:1]))

    base = {_z6(row.get("code")): row for row in l1_rows}
    # 同 code 多出一行 = tushare 盘后灌数窗口写到一半的快照(实测 `20260826_2120` 的
    # top1000 是 1000 行 / 999 个 code,重复的是 601665)。dict 会静默吃掉它,所以**显式
    # 计数**并落进 meta —— 静默去重正是那次「炸在 L3 烧完 1.16M token 之后」的前半段。
    n_duplicate = len(l1_rows) - len(base)
    # 下游偶尔冒出 L1 之外的票(实测早期 run 有 45 只 finalist 不在当日 L2/L1 里)。
    # 不静默丢:以 `in_l1=False` 入表并计数 —— 一只被 BUY 过的孤儿如果不进表,
    # `e6_buy_minus_pool` 就少了它,而 counts 一切正常。
    downstream = set(finalists or ()) | set(ratings or ()) | set(e6_buys) | set(e6_cand or ())
    orphans = sorted(downstream - set(base))

    def maybe(value: bool | None) -> object:
        """`None` → `pd.NA`(UNKNOWN)。折成 False 就是替没人留过的证据作证。"""
        return pd.NA if value is None else bool(value)

    records: list[dict] = []
    for code in sorted(set(base) | set(orphans)):
        row = base.get(code, {})
        port = passport.get(code, {})
        recall = port.get("recall") or {}
        l2_block = port.get("l2") or {}
        pass1_block = port.get("pass1") or {}
        l3_block = port.get("l3") or {}
        l4_block = port.get("l4") or {}
        cand = e6_cand.get(code) if has_e6 else None
        fin_row = (finalists or {}).get(code)
        jd_row = (judged or {}).get(code)
        rating = (ratings or {}).get(code)
        stop = (early or {}).get(code) or (l4_block.get("earlystop_reason") and
                                           {"reason": l4_block.get("earlystop_reason"),
                                            "phase": l4_block.get("earlystop_phase")})

        in_l2 = None if l2_set is None else code in l2_set
        if in_l2 is None and l2_block:
            in_l2 = True                                # 护照在 = 它就是 L2 全集的行
        pass1_kept = None
        if kept_set is not None:
            pass1_kept = code in kept_set
        elif pass1_block.get("kept") is not None:
            pass1_kept = bool(pass1_block["kept"])
        elif cut_set is not None and code in cut_set:
            pass1_kept = False
        l3_judged = None if judged is None else code in judged
        if l3_judged is None and l3_block.get("judged") is not None:
            l3_judged = bool(l3_block["judged"])
        is_finalist = None if finalists is None else code in finalists
        if is_finalist is None and l3_block.get("finalist") is not None:
            is_finalist = bool(l3_block["finalist"])
        is_bench = None
        if bench_set is not None:
            is_bench = code in bench_set
        elif l3_judged is not None and is_finalist is not None:
            is_bench = bool(l3_judged and not is_finalist)

        l4_dispatched = None
        if dispatched_set is not None:
            l4_dispatched = code in dispatched_set
        elif ratings is not None:
            l4_dispatched = rating is not None
        elif l4_block.get("dispatched") is not None:
            l4_dispatched = bool(l4_block["dispatched"])
        l4_rejected = None
        if l4_dispatched:
            if rating is None and not stop and ratings is None:
                l4_rejected = None                      # 派了但读不到评级/早停 → 不知道
            else:
                l4_rejected = bool(stop) or (rating in REJECT_RATINGS)
        elif l4_dispatched is False:
            l4_rejected = False

        guard = str((fin_row or {}).get("guard") or l3_block.get("guard") or "")
        lane = str((fin_row or {}).get("lane") or (jd_row or {}).get("lane")
                   or l3_block.get("lane") or "")

        flags: dict[str, object] = {
            "in_l1": code in base,
            "in_l2": in_l2,
            "pass1_kept": pass1_kept,
            "l3_judged": l3_judged,
            "is_finalist": is_finalist,
            "is_bench": is_bench,
            "l4_dispatched": l4_dispatched,
            "l4_rejected": l4_rejected,
            "e6_candidate": (code in e6_cand) if has_e6 else None,
            "e6_eligible": bool(cand.get("eligible")) if cand else (False if has_e6 else None),
            "is_buy": (code in e6_buys) if has_e6 else None,
            "is_pinned": (code in pinned) if pinned_known else None,
            "is_composite_seat": (guard == COMPOSITE_SEAT_GUARD) if finalists is not None else None,
            "research_rating": rating,
        }
        stage, disposition = _terminal(flags)
        channels = recall.get("channels")
        if channels is None:
            raw = str(row.get("recall_channels") or "")
            channels = sorted({c for c in raw.split("|") if c and c != "pinned"})
        records.append({
            "code": code,
            "name": str(row.get("name") or (fin_row or {}).get("name")
                        or port.get("name") or "") or None,
            "sector": str(row.get("industry") or (fin_row or {}).get("sector")
                          or port.get("sector") or "") or None,
            "composite": _num(row.get("composite")),
            **{flag: maybe(flags[flag]) for flag in FLAG_COLUMNS},
            "terminal_stage": stage,
            "terminal_disposition": disposition,
            # ── 路径字段(护照优先;护照缺则回落 L1 行/CSV 原值)──
            "recall_channels": "|".join(channels) if channels else None,
            "n_channels": (recall.get("n_channels")
                           if recall.get("n_channels") is not None
                           else (len(channels) if channels else None)),
            "composite_pctl": recall.get("composite_pctl"),
            "l2_rank": l2_block.get("l2_rank"),
            "l2_selection_reason": l2_block.get("selection_reason"),
            "pass1_reason": pass1_block.get("reason"),
            "l3_lane": lane or None,
            "l3_conviction": _num((jd_row or {}).get("conviction")
                                  if jd_row else l3_block.get("conviction")),
            "l3_guard": guard or None,
            "research_rating": rating or l4_block.get("research_rating"),
            "l4_early_stop_reason": (stop or {}).get("reason") if stop else None,
            "e6_rank": (cand or {}).get("rank"),
            # 非旗字符串列(2026-09-24 §2.7):不进 FLAG_COLUMNS —— 那个元组只装
            # 「正交身份旗」(见 `FLAG_COLUMNS` 旁注),A/R/None 三值不是布尔,
            # `pd.array(..., dtype="boolean")` 装不下。
            "buy_tier": (e6_tiers.get(code) if (has_e6 and code in e6_buys) else None),
        })

    table = pd.DataFrame.from_records(records)
    for flag in FLAG_COLUMNS:
        table[flag] = pd.array(table[flag].tolist(), dtype="boolean")
    table["analysis_date"] = date
    table["run_key"] = key
    table["report_dir_id"] = src.report_dir_id
    table["capsule_run_id"] = src.capsule_run_id
    table["identity_quality"] = src.identity_quality
    # run 级在场性(逐行重复,parquet 字典编码近乎零成本):`rulers` 靠它区分
    # 「今天真的 0 BUY」与「E6 决策文件读不到」——后者绝不许算成 0-BUY 日。
    table["e6_present"] = has_e6
    table["l4_present"] = bool(ratings is not None or dispatched_set is not None)
    table["l3_present"] = bool(judged is not None or finalists is not None)
    table["l2_present"] = l2_set is not None

    sectors = {r["code"]: (r["sector"] or "") for r in records}
    frame, meta = ruler_frame(date, lake_daily=lake_daily, sectors=sectors)
    if frame is None:
        missing.append(f"lake:{meta.get('reason')}")
    table = _attach_outcomes(table, frame, meta, tuple(RULER_HORIZON))
    table = table.sort_values("code", kind="stable").reset_index(drop=True)

    unknown = {flag: int(table[flag].isna().sum()) for flag in FLAG_COLUMNS}
    return table, {
        "schema_version": POPULATION_SCHEMA_VERSION, "kind": "populations",
        "analysis_date": date, "run_key": key,
        "report_dir_id": src.report_dir_id, "capsule_run_id": src.capsule_run_id,
        "identity_quality": src.identity_quality,
        "n_rows": int(len(table)), "n_in_l1": int(table["in_l1"].sum(skipna=True)),
        "n_source_rows": len(l1_rows),
        "counts": {"orphans": len(orphans), "duplicate_source_rows": n_duplicate,
                   "in_l2": int(table["in_l2"].sum(skipna=True)),
                   "finalist": int(table["is_finalist"].sum(skipna=True)),
                   "buy": int(table["is_buy"].sum(skipna=True))},
        "flag_unknown": unknown,
        "origin": dict(src.origin), "missing": sorted(set(missing)),
        "lake_reached": meta.get("lake_reached", {}),
        "matures_on": meta.get("matures_on", {}),
    }


# ───────────────────────── 落盘(原子 + 幂等) ─────────────────────────

def ledger_root(reports_root: Path | None = None) -> Path:
    return Path(reports_root or (ws.reports_root() / "scan")) / LEDGER_DIRNAME


def universe_path(date: str, reports_root: Path | None = None) -> Path:
    return ledger_root(reports_root) / UNIVERSE_DIRNAME / f"{date}.parquet"


def population_path(key: str, reports_root: Path | None = None) -> Path:
    return ledger_root(reports_root) / POPULATIONS_DIRNAME / f"{key}.parquet"


def stage_rulers_path(reports_root: Path | None = None) -> Path:
    return ledger_root(reports_root) / VIEWS_DIRNAME / STAGE_RULERS_CSV


def _atomic_parquet(table: pd.DataFrame, target: Path, meta: dict) -> Path:
    """temp → `replace`。同输入重复跑 byte 稳定(无时间戳、列序与行序全定死)。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    table.to_parquet(tmp, index=False, compression="zstd")
    tmp.replace(target)
    side = target.with_suffix(".meta.json")
    tmp_side = side.with_name(f"{side.name}.tmp")
    tmp_side.write_text(json.dumps(meta, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                        encoding="utf-8")
    tmp_side.replace(side)
    return target


def _has_pending(target: Path) -> bool:
    """已落盘的表里还有 `PENDING` 吗?没有 = 再算也不会变,可跳过。

    这正是 `outcome.complete` 那枚总开关的替代:它按**整份**判成熟,于是 D+2 一到就
    再也不回来补 `fwd_5/10`;这里按**列**判,只要还有任何一列 PENDING 就重算。
    """
    try:
        got = pd.read_parquet(target)
    except (OSError, ValueError):
        return True
    cols = [c for c in got.columns if c.startswith("status_")]
    if not cols:
        return True
    return bool(any((got[c] == PENDING).any() for c in cols))


def build(*, reports_root: Path | None = None, lake_daily: Path | None = None,
          limit: int | None = None, only: str | None = None) -> dict:
    """遍历已发布 run,物化两层人口。返回 `{built, skipped, no_source, runs}`。"""
    runs = _outcome.published_runs(reports_root)
    if only:
        runs = [r for r in runs if r.name == only]
    built, skipped, no_source, touched = 0, 0, 0, []
    for run in runs:
        key = run_key(run)
        target = population_path(key, reports_root)
        # 先看能不能跳过,再动手建 —— 已落盘且**一列 PENDING 都没有**的 run,再算也不会变。
        if target.is_file() and not _has_pending(target):
            skipped += 1
            continue
        table, meta = build_population(run, lake_daily=lake_daily)
        if table is None:
            no_source += 1
            continue
        _atomic_parquet(table, target, meta)
        u_table, u_meta = build_universe(run, lake_daily=lake_daily)
        if u_table is not None and u_meta.get("analysis_date"):
            _atomic_parquet(u_table, universe_path(u_meta["analysis_date"], reports_root),
                            u_meta)
        built += 1
        touched.append(key)
        if limit and built >= limit:
            break
    return {"built": built, "skipped": skipped, "no_source": no_source, "runs": touched}


# ───────────────────────── 阶段 KPI(long schema) ─────────────────────────

RULER_BLOCK = {MAIN: 1, "fwd_5_oc": 5, "fwd_10_oc": 10}


def _definition_version(ruler: str) -> str:
    """重叠窗口的处理方式是**定义的一部分**,所以写进 `metric_definition_version`。

    `fwd_5_oc`/`fwd_10_oc` 的相邻分析日窗口重叠 5/10 个 session,逐日 delta 不独立 →
    区间用**不重叠 block bootstrap**(把连续 5/10 日折成一个 cluster 交给
    `common.stats.date_cluster_bootstrap` 重采样,复用同一实现,不写第二套)。
    主尺 `gap_c1_o2` 的窗口(T+1 收 → T+2 开)在相邻分析日之间不重叠,block=1。
    """
    block = RULER_BLOCK.get(ruler, 1)
    return METRIC_DEFINITION_VERSION if block == 1 else f"{METRIC_DEFINITION_VERSION}+block{block}"


def _blocked(days: list[str], block: int) -> dict[str, str]:
    """有序交易日 → block id(连续 `block` 天一簇)。block=1 时退化为逐日聚簇。"""
    ordered = sorted(days)
    return {day: f"b{i // max(1, block):04d}" for i, day in enumerate(ordered)}


def _interval(rows: list[dict], ruler: str) -> _stats.Interval:
    """逐日 delta → 点估计 + date-cluster(或 block)bootstrap 区间。

    `common.stats.date_cluster_bootstrap` 是唯一实现:paired unit 是**扫描日**,
    行级重采样会把「1 天 × 60 只」当 60 个独立样本,区间窄到假。
    """
    if not rows:
        return _stats.Interval(None, None, None, 0, 0, "empty")
    block = _blocked([r["session"] for r in rows], RULER_BLOCK.get(ruler, 1))
    frame = pd.DataFrame({"date": [block[r["session"]] for r in rows],
                          "value": [r["value"] for r in rows]})
    return _stats.date_cluster_bootstrap(frame, "value", date_col="date")


def _measurable(table: pd.DataFrame, ruler: str) -> pd.Series:
    """一票在这把尺上「算得出」= 成熟 ∧ 有值 ∧ 买腿可执行。

    买腿过滤走 `ruler.entry_tradable` 的同一折叠(封涨停收盘买不进 = 不该进任何家族
    的均值)。**删掉这一层,读数就不再与 `edge_census` 同源** —— 那正是「两套口径」
    的入口。
    """
    frame = pd.DataFrame({_ruler.entry_flag_for(MAIN): table["buyable_c1"]},
                         index=table.index)
    ok = _ruler.entry_tradable(frame, ruler_name=MAIN)
    return ok & table[f"status_{ruler}"].eq(MATURE) & table[ruler].notna()


def _family_mean(table: pd.DataFrame, mask: pd.Series, ruler: str) -> tuple[float | None, int, int]:
    """家族在一把尺上的均值 + (可算数, 家族总数)。"""
    total = int(mask.fillna(False).sum())
    usable = mask.fillna(False) & _measurable(table, ruler)
    values = pd.to_numeric(table.loc[usable, ruler], errors="coerce").dropna()
    if not len(values):
        return None, 0, total
    return float(values.mean()), int(len(values)), total


def _plain(table: pd.DataFrame) -> pd.Series:
    """剔 📌 与 composite 席位 —— **保送不是排序的产物**(2026-07-16 判例)。

    把它们算进 finalist 家族,量到的是「用户选的持仓 + 确定性兜底席位」的表现,
    不是 L3 的排序能力;这正是 retro 那次 L3 edge 被污染的形状。
    """
    return ~(table["is_pinned"].fillna(False) | table["is_composite_seat"].fillna(False))


def _row(session: str, stage: str, metric: str, *, value, n_days: int, n_names: int,
         coverage, status: str, ruler: str, interval: _stats.Interval | None = None) -> dict:
    return {
        "session": session, "stage": stage, "metric": metric,
        "value": None if value is None else round(float(value), 8),
        "n_days": int(n_days), "n_names": int(n_names),
        "coverage": None if coverage is None else round(float(coverage), 6),
        "ci_low": None if (interval is None or interval.lo is None) else round(interval.lo, 8),
        "ci_high": None if (interval is None or interval.hi is None) else round(interval.hi, 8),
        "status": status, "metric_definition_version": _definition_version(ruler),
    }


def _pooled(per_day: list[dict], stage: str, metric: str, ruler: str,
            pending: int) -> list[dict]:
    """逐日行 + 一行 `session=ALL` 的汇总(带 CI)。

    `grep <date> stage_rulers.csv`(设计稿 §2.5 第 4 步)要能捞到当天;跨日效应量与区间
    则只在 `ALL` 行出现 —— 一天的数据没有跨日方差,给它一个区间就是伪精确。
    """
    rows = [_row(r["session"], stage, metric, value=r["value"], n_days=1,
                 n_names=r["n_names"], coverage=r["coverage"], status=MATURE, ruler=ruler)
            for r in per_day]
    if not per_day:
        rows.append(_row("ALL", stage, metric, value=None, n_days=0, n_names=0,
                         coverage=None, ruler=ruler,
                         status=PENDING if pending else UNAVAILABLE))
        return rows
    interval = _interval(per_day, ruler)
    coverages = [r["coverage"] for r in per_day if r["coverage"] is not None]
    rows.append(_row("ALL", stage, metric, value=interval.point, n_days=len(per_day),
                     n_names=sum(r["n_names"] for r in per_day),
                     coverage=(float(np.mean(coverages)) if coverages else None),
                     status=MATURE, ruler=ruler, interval=interval))
    return rows


def _paired_metric(sessions: dict[str, pd.DataFrame], stage: str, metric: str,
                   left, right, ruler: str = MAIN) -> list[dict]:
    """同日配对超额(左家族 − 右家族)。两边任一为空 → 当日不计(不拿空集当 0)。

    `pending` 只数「家族在场、但这把尺还没成熟」的日子 —— 家族**根本不在场**(源文件
    不在冻结副本里)不是 PENDING,那是 UNAVAILABLE:等下去也不会有。
    """
    per_day, pending = [], 0
    for session in sorted(sessions):
        table = sessions[session]
        left_mask = left(table).fillna(False)
        right_mask = right(table).fillna(False)
        if not left_mask.any() or not right_mask.any():
            continue
        if table.loc[left_mask | right_mask, f"status_{ruler}"].eq(PENDING).any():
            pending += 1
        a, na, ta = _family_mean(table, left_mask, ruler)
        b, nb, _tb = _family_mean(table, right_mask, ruler)
        if a is None or b is None:
            continue
        per_day.append({"session": session, "value": a - b, "n_names": na + nb,
                        "coverage": (na / ta) if ta else None})
    return _pooled(per_day, stage, metric, ruler, pending)


def _winners(table: pd.DataFrame, ruler: str = MAIN) -> set[str] | None:
    """当日事后主尺 top-decile 赢家(限**可买** ∧ 成熟)。截面不足 → `None`(不算)。"""
    usable = _measurable(table, ruler)
    values = pd.to_numeric(table.loc[usable, ruler], errors="coerce").dropna()
    if len(values) < MIN_CROSS_SECTION:
        return None
    cut = float(values.quantile(WINNER_DECILE))
    return set(table.loc[values.index[values >= cut], "code"])


def _ratio_metric(per_day: list[dict], stage: str, metric: str) -> list[dict]:
    return _pooled(per_day, stage, metric, MAIN, 0)


def _load_tables(reports_root: Path | None) -> tuple[dict[str, pd.DataFrame],
                                                     dict[str, pd.DataFrame], dict]:
    """读回两层人口;同一 analysis_date 多个 run 时按 **selected run** 去重。"""
    root = ledger_root(reports_root)
    universes: dict[str, pd.DataFrame] = {}
    for path in sorted((root / UNIVERSE_DIRNAME).glob("*.parquet")):
        universes[path.stem] = pd.read_parquet(path)
    by_date: dict[str, list[pd.DataFrame]] = {}
    for path in sorted((root / POPULATIONS_DIRNAME).glob("*.parquet")):
        table = pd.read_parquet(path)
        if not len(table):
            continue
        by_date.setdefault(str(table["analysis_date"].iloc[0]), []).append(table)
    selected: dict[str, pd.DataFrame] = {}
    for date, tables in by_date.items():
        selected[date] = select_run(tables)
    return universes, selected, {"n_sessions": len(selected)}


def select_run(tables: list[pd.DataFrame]) -> pd.DataFrame:
    """同一 analysis_date 的多个 run 里选哪一个。

    G2 的正式规则是「截止前最后一个 SUCCEEDED ∧ approved ∧ ACTIONABLE」,但 G1/G2 尚未
    实施(另一批),批准时点与 actionability 现在还不存在。**过渡规则 = `run_key` 字典序
    最大者**,即当天最后发布的那个 run —— 与设计稿附录 A 探针的 legacy 口径一致
    (`if r["run_id"] > last[d]`)。颠倒它(取第一个)会换掉整张 stage_rulers 的读数,
    所以它有一条专门的测试盯着。
    """
    return max(tables, key=lambda t: str(t["run_key"].iloc[0]))


def stage_rulers(*, reports_root: Path | None = None) -> pd.DataFrame:
    """§1.4 的逐级 primary KPI,long schema。

    **全部是 observational**:家族不是随机分配的(L4 只否决它自己怀疑的票,E6 只在自己
    判为 eligible 的池里选),所以「拒绝价值为负」只说明**被否的那批事后更差**,不说明
    否决这个动作造成了更好的结果;`chain_buyday_minus_zeroday` 同理,不是「空仓正确」的
    因果证明。`status` 说的是**数据成熟度**,不是显著性。
    """
    universes, sessions, _meta = _load_tables(reports_root)
    rows: list[dict] = []

    # ── L1:Recall@1000(分母 = 当日 L0 eligible ∧ 可买的事后赢家)───────────────
    recall_days, lift_days, keep_days, keep_lift_days = [], [], [], []
    for date in sorted(universes):
        uni = universes[date]
        winners = _winners(uni)
        if winners is None:
            continue
        in_l1 = set(uni.loc[uni["in_l1"].fillna(False), "code"])
        n_uni = int(_measurable(uni, MAIN).sum())
        hit = len(winners & in_l1)
        recall = hit / len(winners)
        recall_days.append({"session": date, "value": recall, "n_names": len(winners),
                            "coverage": (n_uni / len(uni)) if len(uni) else None})
        baseline = (len(in_l1) / n_uni) if n_uni else None
        if baseline:
            lift_days.append({"session": date, "value": recall / baseline,
                              "n_names": len(winners),
                              "coverage": (n_uni / len(uni)) if len(uni) else None})
        # ── L2:L1 里的事后赢家有多少活到 L2 ──
        table = sessions.get(date)
        if table is None or not bool(table["l2_present"].iloc[0]):
            continue
        l1_winners = winners & set(table.loc[table["in_l1"].fillna(False), "code"])
        if not l1_winners:
            continue
        l2_codes = set(table.loc[table["in_l2"].fillna(False), "code"])
        keep = len(l1_winners & l2_codes) / len(l1_winners)
        keep_days.append({"session": date, "value": keep, "n_names": len(l1_winners),
                          "coverage": None})
        n_l1 = int(table["in_l1"].fillna(False).sum())
        if n_l1 and l2_codes:
            keep_lift_days.append({"session": date, "value": keep / (len(l2_codes) / n_l1),
                                   "n_names": len(l1_winners), "coverage": None})
    rows += _ratio_metric(recall_days, "L1", "l1_recall_at_1000")
    rows += _ratio_metric(lift_days, "L1", "l1_recall_at_1000_lift")
    rows += _ratio_metric(keep_days, "L2", "l2_keep_rate")
    rows += _ratio_metric(keep_lift_days, "L2", "l2_keep_rate_lift")

    # ── L3:finalist − bench(剔 📌/席位)────────────────────────────────────
    rows += _paired_metric(
        sessions, "L3", "l3_finalist_minus_bench",
        lambda t: t["is_finalist"].fillna(False) & _plain(t),
        lambda t: t["is_bench"].fillna(False) & _plain(t))

    # ── L4:拒绝价值(负 = 否决对了)。两把 horizon 各一行,互不阻塞 ──────────
    def rejected(t: pd.DataFrame) -> pd.Series:
        return t["l4_rejected"].fillna(False) & _plain(t)

    def comparable(t: pd.DataFrame) -> pd.Series:
        return (t["l4_dispatched"].fillna(False) & ~t["l4_rejected"].fillna(False)
                & t["research_rating"].isin(COMPARABLE_RATINGS) & _plain(t))

    rows += _paired_metric(sessions, "L4", "l4_reject_value_gap", rejected, comparable, MAIN)
    rows += _paired_metric(sessions, "L4", "l4_reject_value_fwd10", rejected, comparable,
                           "fwd_10_oc")

    # ── E6:BUY − **其余 eligible 池成员**(不与已判 ineligible 的票混比)──────
    rows += _paired_metric(
        sessions, "E6", "e6_buy_minus_pool",
        lambda t: t["is_buy"].fillna(False),
        lambda t: t["e6_eligible"].fillna(False) & ~t["is_buy"].fillna(False))

    # ── E6:A 级天数占比(2026-09-24 §2.7 的成功尺;card-backed 允许才算 A)──────
    a_days = []
    for session in sorted(sessions):
        t = sessions[session]
        buy_rows = t[t["is_buy"].fillna(False).astype(bool)]
        if not len(buy_rows) or "buy_tier" not in t.columns:
            continue
        a_days.append({"session": session, "value": float((buy_rows["buy_tier"] == "A").any()),
                       "n_names": int(len(buy_rows)), "coverage": None})
    rows += _ratio_metric(a_days, "E6", "e6_a_tier_day_share")

    # ── 执行线:只在**前夜 BUY 候选**(E6 eligible 池)内比 ────────────────────
    def exec_in(t: pd.DataFrame) -> pd.Series:
        return t["e6_eligible"].fillna(False) & t["exec_ok"].eq(1.0)

    def exec_out(t: pd.DataFrame) -> pd.Series:
        return t["e6_eligible"].fillna(False) & t["exec_ok"].eq(0.0)

    rows += _paired_metric(sessions, "EXEC", "exec_line_in_minus_out", exec_in, exec_out)

    # ── 整条链:BUY 日 vs 0-BUY 日 ─────────────────────────────────────────
    rows += _chain_metric(sessions)

    frame = pd.DataFrame(rows, columns=[
        "session", "stage", "metric", "value", "n_days", "n_names", "coverage",
        "ci_low", "ci_high", "status", "metric_definition_version"])
    return frame.sort_values(["stage", "metric", "session"], kind="stable").reset_index(drop=True)


def zero_buy_status(table: pd.DataFrame) -> str:
    """一个 session 是 BUY 日 / 0-BUY 日 / 还是**读不出来**。

    `NO_RUN` 与 `UNKNOWN_E6` 绝不能算成 0-BUY:没跑的日子和 E6 决策文件读不到的日子,
    系统并没有「决定空仓」——把它们算进 0-BUY 家族,等于给空仓成绩单塞进一批从来没有
    发生过的决定(§1.4 整条链行的原话:「`NO_RUN` 不能算 0-BUY」)。
    """
    if table is None or not len(table):
        return "NO_RUN"
    if not bool(table["e6_present"].iloc[0]):
        return "UNKNOWN_E6"
    return "BUY_DAY" if bool(table["is_buy"].fillna(False).any()) else "ZERO_BUY"


def _chain_metric(sessions: dict[str, pd.DataFrame]) -> list[dict]:
    """BUY 日 vs 0-BUY 日的市场主尺(**observational**,不是空仓正确性的证明)。

    市场行口径:当日 **L0 eligible ∧ 可买** 截面等权均值 —— 这不是 E6 `rel_gap_market`
    的全湖分母(那是 G2 `market.csv` 的活,尚未实施),名字与本注释是它的唯一声明。

    区间也走 `date_cluster_bootstrap`:两组均值差用「按组规模缩放的逐日序列」表达
    (A 组乘 n/n_A、B 组乘 −n/n_B,全体均值恰等于 mean_A − mean_B),于是**复用同一个
    实现**而不是再写一个两样本 bootstrap。代价是重采样偶尔抽出全 A/全 B 的日集合,
    区间因此略偏保守 —— 如实记在这里。
    """
    buy_days, zero_days, pending, unknown = [], [], 0, 0
    for session in sorted(sessions):
        table = sessions[session]
        state = zero_buy_status(table)
        if state == "UNKNOWN_E6":
            unknown += 1
            continue
        if table[f"status_{MAIN}"].eq(PENDING).any():
            pending += 1
        usable = _measurable(table, MAIN)
        values = pd.to_numeric(table.loc[usable, MAIN], errors="coerce").dropna()
        if not len(values):
            continue
        (buy_days if state == "BUY_DAY" else zero_days).append(
            {"session": session, "value": float(values.mean()), "n": int(len(values))})
    total = len(buy_days) + len(zero_days)
    coverage = (total / (total + unknown)) if (total + unknown) else None
    if not buy_days or not zero_days:
        return [_row("ALL", "CHAIN", "chain_buyday_minus_zeroday", value=None,
                     n_days=total, n_names=0, coverage=coverage, ruler=MAIN,
                     status=PENDING if pending else UNAVAILABLE)]
    scale_a, scale_b = total / len(buy_days), total / len(zero_days)
    per_day = ([{"session": d["session"], "value": d["value"] * scale_a,
                 "n_names": d["n"], "coverage": None} for d in buy_days]
               + [{"session": d["session"], "value": -d["value"] * scale_b,
                   "n_names": d["n"], "coverage": None} for d in zero_days])
    interval = _interval(per_day, MAIN)
    rows = [_row(d["session"], "CHAIN", "chain_buyday_minus_zeroday", value=d["value"],
                 n_days=1, n_names=d["n"], coverage=None, status=MATURE, ruler=MAIN)
            for d in sorted(buy_days + zero_days, key=lambda x: x["session"])]
    rows.append(_row("ALL", "CHAIN", "chain_buyday_minus_zeroday", value=interval.point,
                     n_days=total, n_names=sum(d["n"] for d in buy_days + zero_days),
                     coverage=coverage, status=MATURE, ruler=MAIN, interval=interval))
    return rows


def write_stage_rulers(*, reports_root: Path | None = None) -> Path:
    """物化 `views/stage_rulers.csv`(原子替换;它是**可重建视图**,不是事实源)。"""
    frame = stage_rulers(reports_root=reports_root)
    target = stage_rulers_path(reports_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    frame.to_csv(tmp, index=False, lineterminator="\n")
    tmp.replace(target)
    return target


# ───────────────────────── size:先量再决定分区策略 ─────────────────────────

def measure_size(run_dir: Path | str, *, lake_daily: Path | None = None) -> dict:
    """用一个**真实 run** 量两张 parquet 的体积与墙钟。

    设计稿 §2.4 G3「体积」行的原话:不先拍「几十 MB」,用一个真实 run dry-run 量,再决定
    年度分区/压缩。写进 `TemporaryDirectory` —— 量尺寸不该顺手往账本里塞一份产物。
    """
    run = Path(run_dir)
    out: dict = {"run": run.name, "run_key": run_key(run)}
    with tempfile.TemporaryDirectory() as scratch:
        base = Path(scratch)
        started = time.perf_counter()
        pop, pop_meta = build_population(run, lake_daily=lake_daily)
        out["populations_build_s"] = round(time.perf_counter() - started, 3)
        if pop is None:
            out["populations"] = {"status": "NO_SOURCE", "reason": pop_meta.get("reason")}
        else:
            path = base / "populations.parquet"
            _atomic_parquet(pop, path, pop_meta)
            out["populations"] = {"rows": int(len(pop)), "cols": int(pop.shape[1]),
                                  "bytes": path.stat().st_size,
                                  "meta_bytes": path.with_suffix(".meta.json").stat().st_size}
        started = time.perf_counter()
        uni, uni_meta = build_universe(run, lake_daily=lake_daily)
        out["universe_build_s"] = round(time.perf_counter() - started, 3)
        if uni is None:
            out["universe"] = {"status": "NO_SOURCE", "reason": uni_meta.get("reason")}
        else:
            path = base / "universe.parquet"
            _atomic_parquet(uni, path, uni_meta)
            out["universe"] = {"rows": int(len(uni)), "cols": int(uni.shape[1]),
                               "bytes": path.stat().st_size,
                               "meta_bytes": path.with_suffix(".meta.json").stat().st_size}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="反事实人口(G3;确定性、只记不学)")
    parser.add_argument("command", choices=["build", "rulers", "size"])
    parser.add_argument("--limit", type=int, default=None, help="build:本次最多物化几个 run")
    parser.add_argument("--run", default=None, help="只处理这一个已发布 run 目录名")
    args = parser.parse_args(argv)

    if args.command == "build":
        result = build(limit=args.limit, only=args.run)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "rulers":
        target = write_stage_rulers()
        frame = pd.read_csv(target, dtype=str)
        pooled = frame[frame["session"] == "ALL"]
        print(json.dumps({"ok": True, "path": str(target), "rows": int(len(frame)),
                          "metrics": int(len(pooled))}, ensure_ascii=False, sort_keys=True))
        for _, row in pooled.iterrows():
            print(f"  {row['stage']:<5} {row['metric']:<28} {row['value'] or '—':>12}"
                  f"  n_days={row['n_days']:<4} status={row['status']}")
        return 0
    runs = _outcome.published_runs()
    if args.run:
        runs = [r for r in runs if r.name == args.run]
    if not runs:
        print(json.dumps({"ok": False, "reason": "没有已发布 run 可量"}, ensure_ascii=False))
        return 1
    print(json.dumps(measure_size(runs[-1]), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

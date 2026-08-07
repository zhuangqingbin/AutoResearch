#!/usr/bin/env python3
"""两尺对照(gap_c1_o2 vs fwd_2_oc)—— 换尺认知底片。自包含,只读现成产物,零 LLM。

design: `.superpowers/sdd/2026-08-06-wave11-implementation-plan/task-15-brief.md`
        (沿 `autoresearch/common/ruler.py` 的隔夜主尺裁定:2026-08-05)

**动机**:2026-08-05 用户裁定评判主尺从 `fwd_2_oc`(D+1开买→D+2收卖)改为隔夜尺
`gap_c1_o2`(D+1收买→D+2开卖)。这个项目的账本/经验库/设计稿里有大量基于旧尺的结论——
换尺之后哪些还成立、哪些翻了,不问清楚就会拿错误的先验指挥新尺生产。本模块**不是**论证
新尺更优,只产出一份认知底片:同一批历史数据,两把尺子量出来的四类结论(门的价值/召回
排序/L3选股edge/弃权日裁决)一不一致。

**数据现状**(2026-08-07 探清):`retro/attribution.csv` 目前只有 `fwd_2_oc`,没有
`gap_c1_o2`(`autoresearch.learning.retro.realized_returns` 的落盘白名单还没收编这一列
——那是它自己的事,本模块不碰)。`gap_c1_o2` 由本模块从 `context/lake/daily/*.parquet`
现算(open[D+2]/close[D+1] − 1;D+1/D+2 按湖文件名排序推断,口径与
`autoresearch.learning.paper_nav.trade_days()` 相同)。已用 2026-08-03/000001 做过
逐位比特验证:湖重算的 `fwd_2_oc`(close[D+2]/open[D+1]−1)与 `attribution.csv` 原值
完全一致,说明湖的交易日序列与 retro 的口径同源,gap_c1_o2 的 D+1/D+2 定位可信。

**四节**(判据见 brief):
  ① 门的价值 —— 真实(bought)/影子(shadow_buys.csv)/市场(eligible universe)三条日均值,
     两尺下的 `真实 − 影子`。是 `paper_nav` 口径的**简化**对照(逐日等权均值,非 NAV 复利
     模拟)——`paper_nav.simulate()` 的入/出场价只支持开盘腿,隔夜尺是收盘买/开盘卖的单腿
     结构,直接套用会错配价格腿;本节改用与 `channel_audit`/`rejection_attribution` 同款
     的"简单日频均值"读法,牺牲 NAV 曲线换取可验证性(数字都能在 selftest 里手算核对)。
  ② 九路召回 unique 超额排序 —— 读 `L1_channels.csv` 长表,口径镜像
     `autoresearch.research.channel_audit.day_channel_stats`(未直接 import——那个模块
     此刻正被同波另一个 agent 做主尺常量 sweep,本模块只读它的方法论,自带一份参数化
     ruler_col 的实现,两尺共用同一函数、同一 eligible 定义,避免"口径本身不同"污染对比)。
  ③ L3 真选 edge —— 读 `L3_judged_full.csv` 的 `finalist` 列(finalist vs bench 均值差),
     **不是** `l3_marginal.py` 的分层匹配反事实估计(那套需要 `_l3_pass1_kept.csv`,29 个
     有 attribution 的历史日里一个都没有——本波才新增,provenance 从这波才开始留痕)。
  ④ 弃权日裁决翻转 —— 镜像 `autoresearch.learning.abstention_ledger.classify_abstention`
     的 v2(shadow 口径)判定规则,对 `rejection_attribution.csv` 的机会/excess 用 gap 重算,
     `data_quality`(degraded)与 ruler 无关,直接复用存量 `abstention_verdict.json` 的值。
     自检:本模块的 `_verdict()` 套用存量 fwd_2_oc 的 opportunity/excess_2,应精确复现存量
     `status_v2`——复现失败即说明重实现有偏差(结果见报告"自检"行,不是断言式失败)。

**市场基准口径**(全模块统一,与其中任一既有模块都不完全同名同姓,是本报告内部自洽的
选择,详见各函数 docstring):① 用"均值"(brief 原话);②③④ 用"eligible 子集中位数"
(eligible = 可执行 [entry 腿未封板] & 该尺前瞻收益非空,两尺各自成对定义,不混用)。

用法:
  uv run --no-sync python -m autoresearch.research.ruler_compare run [--days 60]
  uv run --no-sync python -m autoresearch.research.ruler_compare --selftest   # 离线合成数据自测
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import ruler

LAKE_DAILY = Path("context/lake/daily")
SCAN_ROOT = Path("context/scan")
SHADOW_BUYS = Path("context/learning/shadow_buys.csv")
DEFAULT_OUT = Path("docs/research/2026-08-07-ruler-gap-vs-oc-baseline.md")

GAP_COL = "gap_c1_o2"
# T16 修复(2026-08-07 flip 后发现):此前这里用 `ruler.MAIN_RULER` 指代"oc 侧"列名——在
# MAIN_RULER 仍是 "fwd_2_oc" 的年代恰好等价,但本模块存在的唯一目的就是把"旧主尺 fwd_2_oc"
# 与"新主尺 gap_c1_o2"当两个永远不同的东西并列对照;flip 后 MAIN_RULER == GAP_COL,继续用
# MAIN_RULER 会让两侧撞名(`{MAIN_RULER: ..., GAP_COL: ...}` 变成同一个 dict key 互相覆盖;
# `day_universe` 的 merge 会把两侧都改名成 `gap_c1_o2_x`/`_y`)。固定字面量,不随 MAIN_RULER
# 漂移 —— `attribution.csv` 的 schema 本就只产 fwd_2_oc(gap_c1_o2 由本模块从湖现算)。
OC_COL = "fwd_2_oc"
_OPP_THRESH = 0.02        # ≥+2pp = "机会"(与 rejection_attribution.build_rejection_attribution 同阈值)
_CORRECT_THRESH = -0.02   # 全部 ≤−2pp 才判 CORRECT(与 abstention_ledger._verdict_from 同阈值)
_THIN_DAYS = 10           # 门槛同 channel_audit._THIN_DAYS:样本天数 < 此值 → 报告里标 ⚠薄样本


# ───────────────────────── 基础工具(无 IO) ─────────────────────────


def _code6(s: pd.Series) -> pd.Series:
    return s.astype(str).str.split(".").str[0].str.zfill(6)


def _bool_col(df: pd.DataFrame, name: str, default: bool) -> pd.Series:
    """镜像 `abstention_ledger._bool_series`:object dtype 且值为真正 NaN(非字符串)时读成
    "nan" 字符串 → 落在真值集合外 → False(不是 `default`)。这是既有生产模块的现行为,本
    模块的读法与它保持一致,不在这里"顺手修掉"。"""
    if name not in df.columns:
        return pd.Series(default, index=df.index, dtype=bool)
    values = df[name]
    if values.dtype == bool:
        return values.fillna(default)
    return values.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "是"})


def _board_limit(code: str) -> float:
    """涨跌停幅度(%):科创(688)/创业板(30)=20;北交所(8/4/920)=30;其余主板=10。

    与 `autoresearch.research.factor_lab._board_limit` 同一判据,重实现而非 import——
    factor_lab.py 是本波并发 sweep 的文件之一,不依赖它当前的磁盘状态。"""
    if code.startswith("688") or code.startswith("30"):
        return 20.0
    if code.startswith(("8", "4", "920")):
        return 30.0
    return 10.0


def _fmt_pct(x) -> str:
    return "—" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x * 100:+.2f}%"


def _masked_mean(mask: pd.Series, values: pd.Series) -> float | None:
    s = pd.to_numeric(values[mask.fillna(False).astype(bool)], errors="coerce").dropna()
    return float(s.mean()) if len(s) else None


# ───────────────────────── 湖 OHLC → gap_c1_o2(唯一现算点) ─────────────────────────


def lake_days(lake: Path | None = None) -> list[str]:
    """湖内全部 YYYYMMDD 分区名,升序。口径同 `paper_nav.trade_days()`(按湖文件名推断交易日,
    不查交易日历——两者本就该是同一份湖,保持一致口径)。"""
    lake = Path(lake or LAKE_DAILY)
    if not lake.exists():
        return []
    return sorted(p.stem for p in lake.glob("*.parquet") if len(p.stem) == 8 and p.stem.isdigit())


def _read_lake_cols(day: str, cols: list[str], lake: Path | None = None) -> pd.DataFrame:
    """单日湖分区 → `[code, *cols]`;文件缺失/损坏 → 空表(同名列,调用方无需分支)。"""
    lake = Path(lake or LAKE_DAILY)
    p = lake / f"{day}.parquet"
    empty = pd.DataFrame(columns=["code", *cols])
    if not p.exists():
        return empty
    try:
        df = pd.read_parquet(p, columns=["ts_code", *cols])
    except Exception:  # noqa: BLE001 — 坏分区当缺失处理,不中断整条流水线
        return empty
    if df.empty:
        return empty
    out = df.assign(code=_code6(df["ts_code"])).drop(columns=["ts_code"])
    return out[["code", *cols]]


def gap_frame(scan_date: str, days: list[str] | None = None, lake: Path | None = None) -> pd.DataFrame:
    """scan 日 D(YYYY-MM-DD)→ `[code, gap_c1_o2, buyable_c1, eligible_gap]`。

    `gap_c1_o2` = open[D+2]/close[D+1] − 1(隔夜主尺,2026-08-05 裁定);D+1/D+2 = 湖文件名
    排序中 D 之后的第 1/2 个交易日(D 不在湖里 / D+2 越界 → 空表)。

    `buyable_c1`(入场腿可执行,同 `ruler.ENTRY_FLAG` 语义,公式与
    `factor_lab.forward_returns` 的 `buyable_c1` 完全一致,独立重实现):T+1 收盘未封涨停
    (收盘≈日高 且 当日涨幅≈板)。用 pandas 可空 Float64/boolean 参与比较,D+1 缺数时正确
    传染成 `<NA>`(未知),不伪造 True/False。

    `eligible_gap` = `buyable_c1.fillna(True)`(未知按可执行处理,显式选择,镜像
    `attribution.csv` 的 `tradable = buyable.fillna(True) & MAIN_RULER.notna()`)
    `& gap_c1_o2.notna()`。

    返回列**未 clip**——`ruler.GAP_CLIP` 的裁剪只在取均值/中位数的消费点做(同
    `factor_lab.evaluate()` 的既有做法:`forward_returns()` 出的是生值,`decile_table_gap`
    才 clip),不在这个现算点污染原始数值。
    """
    cols = ["code", GAP_COL, "buyable_c1", "eligible_gap"]
    all_days = days if days is not None else lake_days(lake)
    d0 = scan_date.replace("-", "")
    if d0 not in all_days:
        return pd.DataFrame(columns=cols)
    i = all_days.index(d0)
    if i + 2 >= len(all_days):
        return pd.DataFrame(columns=cols)
    d1, d2 = all_days[i + 1], all_days[i + 2]
    f1 = _read_lake_cols(d1, ["close", "high", "pct_chg"], lake)
    f2 = _read_lake_cols(d2, ["open"], lake)
    if f1.empty or f2.empty:
        return pd.DataFrame(columns=cols)
    m = f1.merge(f2, on="code", how="outer")
    close1 = pd.to_numeric(m["close"], errors="coerce").astype("Float64")
    high1 = pd.to_numeric(m["high"], errors="coerce").astype("Float64")
    pc1 = pd.to_numeric(m["pct_chg"], errors="coerce").astype("Float64")
    open2 = pd.to_numeric(m["open"], errors="coerce").astype("Float64")
    lim = m["code"].map(_board_limit).astype("Float64")
    gap = open2 / close1 - 1.0
    m[GAP_COL] = gap.astype("float64")
    buy_sealed = (pc1 >= lim * 0.98) & (close1 >= high1 - 1e-6)
    m["buyable_c1"] = ~buy_sealed
    m["eligible_gap"] = (m["buyable_c1"].fillna(True) & m[GAP_COL].notna()).astype(bool)
    return m[cols]


# ───────────────────────── 单日 universe(两尺并列 + eligible 标记) ─────────────────────────


def day_universe(attr: pd.DataFrame, gap: pd.DataFrame) -> pd.DataFrame:
    """`attribution.csv` 单日帧 × `gap_frame()` 单日帧 → `[code, fwd_2_oc, elig_oc, bought,
    gap_c1_o2, elig_gap]`。两尺各自的 eligible 独立定义、独立生效,不互相污染。

    `gap_c1_o2` 在这里做**唯一**的 `ruler.GAP_CLIP` 裁剪(单日板极值+容差),下游①②③三节
    都读这份 universe,裁剪只做一次、处处一致。
    """
    a = attr.copy()
    a["code"] = _code6(a["code"])
    a[OC_COL] = pd.to_numeric(a.get(OC_COL), errors="coerce")
    buyable_oc = _bool_col(a, "buyable", True)
    a["elig_oc"] = buyable_oc & a[OC_COL].notna()
    a["bought"] = _bool_col(a, "bought", False)
    g = gap.copy()
    if len(g):
        g["code"] = _code6(g["code"])
    else:
        g = pd.DataFrame(columns=["code", GAP_COL, "buyable_c1", "eligible_gap"])
    out = a[["code", OC_COL, "elig_oc", "bought"]].merge(
        g[["code", GAP_COL, "eligible_gap"]], on="code", how="left")
    out = out.rename(columns={"eligible_gap": "elig_gap"})
    # gap 侧完全缺失时(空 gap_frame)合并出的是 object dtype 全 NaN 列;先转 pandas 可空
    # "boolean" 扩展dtype 再 fillna,避免在**旧式** object dtype 上 fillna 触发的隐式降型
    # FutureWarning(该警告只在 legacy object dtype 上出现,可空扩展 dtype 不受影响)。
    out["elig_gap"] = out["elig_gap"].astype("boolean").fillna(False).astype(bool)
    out[GAP_COL] = pd.to_numeric(out[GAP_COL], errors="coerce").clip(-ruler.GAP_CLIP, ruler.GAP_CLIP)
    return out


# ───────────────────────── scan 目录发现 / 影子买单 ─────────────────────────


def scan_dates_with_attribution(scan_root: Path | None = None, limit: int | None = None) -> list[str]:
    """有 `retro/attribution.csv`(fwd_2_oc 已实现)的 scan 日,升序;`limit` 取最近 N 个。"""
    scan_root = Path(scan_root or SCAN_ROOT)
    if not scan_root.exists():
        return []
    dates = sorted(p.name for p in scan_root.iterdir()
                   if p.is_dir() and len(p.name) == 10 and p.name[4] == "-" and p.name[7] == "-"
                   and (p / "retro" / "attribution.csv").exists())
    return dates[-limit:] if limit else dates


def load_shadow_buys(path: Path | None = None) -> pd.DataFrame:
    p = Path(path or SHADOW_BUYS)
    if not p.exists():
        return pd.DataFrame(columns=["date", "code"])
    df = pd.read_csv(p, dtype={"code": str})
    if "code" in df.columns:
        df["code"] = _code6(df["code"])
    return df


def shadow_codes_by_date(df: pd.DataFrame) -> dict[str, set[str]]:
    if df is None or not len(df) or not {"date", "code"}.issubset(df.columns):
        return {}
    return {str(d): set(sub["code"]) for d, sub in df.groupby("date")}


# ═══════════════════════════ ① 门的价值(真实 vs 影子 vs 市场) ═══════════════════════════


def gate_day_stats(universe: pd.DataFrame, shadow_codes: set[str]) -> dict:
    """单日:真实(bought)/影子(shadow_codes)/市场(elig_*)三条**均值**(brief 原话"均值",
    非中位数),两尺并列。纯函数。"""
    bought = universe["bought"].astype(bool)
    shadow = universe["code"].isin(shadow_codes)
    elig_oc = universe["elig_oc"].astype(bool)
    elig_gap = universe["elig_gap"].astype(bool)
    return {
        "n_real": int(bought.sum()), "n_shadow": int(shadow.sum()),
        "real_oc": _masked_mean(bought, universe[OC_COL]),
        "shadow_oc": _masked_mean(shadow, universe[OC_COL]),
        "market_oc": _masked_mean(elig_oc, universe[OC_COL]),
        "real_gap": _masked_mean(bought, universe[GAP_COL]),
        "shadow_gap": _masked_mean(shadow, universe[GAP_COL]),
        "market_gap": _masked_mean(elig_gap, universe[GAP_COL]),
    }


def _agg_column(table: pd.DataFrame, col: str) -> dict:
    """跨日简单日频均值(同 `channel_audit.cumulative_ledger` 的"不按当日样本数加权"口径)。
    纯函数,模块级(Important-3 review fix,2026-08-07:此前是 `gate_value()` 内的嵌套
    closure,`--selftest` 从不调用 `gate_value()` 本身,导致这段聚合代码从未被 selftest
    执行过——① 的 `real−shadow` 符号反写这类变异能悄悄通过。提到模块级后 `_selftest()`
    直接调用它,变异会被抓)。`table` 空 / `col` 缺失 / 全 NaN → `{"value": None, "n_days": 0}`。
    """
    if table is None or not len(table) or col not in table.columns:
        return {"value": None, "n_days": 0}
    s = pd.to_numeric(table[col], errors="coerce").dropna()
    return {"value": round(float(s.mean()), 6) if len(s) else None, "n_days": int(len(s))}


def _gate_diff(real: dict, shadow: dict) -> float | None:
    """`真实 − 影子`(门的价值单点差)。任一侧 `value` 为 None → None。模块级纯函数
    (Important-3 review fix,理由同 `_agg_column`)——独立拆出来是为了让 selftest 能直接
    锁定"是 real−shadow,不是 shadow−real"这个符号,不依赖对 `gate_value()` 的整体黑盒断言。
    """
    if real["value"] is None or shadow["value"] is None:
        return None
    return real["value"] - shadow["value"]


def gate_value(dates: list[str], scan_root: Path | None = None,
               shadow_df: pd.DataFrame | None = None,
               gap_cache: dict[str, pd.DataFrame] | None = None,
               lake: Path | None = None) -> dict:
    """逐日 `gate_day_stats` → 简单日频均值聚合(同 `channel_audit.cumulative_ledger` 的
    "不按当日样本数加权"口径)。真实/影子每日各自可能无数据(0买日无真实signal;shadow_buys
    理论上每日都有 top-k,但历史缺口不保证),聚合时各自独立 dropna,分别报 n_days。

    **配对版(Important-2 review fix,2026-08-07)**:`agg`(全样本,`real` 只在有买单的
    日子有数、`shadow`/`market` 在全窗口有数)分母不同,`real−shadow` 直接相减混入了"哪几天
    下了单"的选择效应(review 实测:配对前 oc 侧 -3.09%,配对后收窄到 -0.92%,缩 3.3 倍)。
    `agg_paired`/`gate_value_paired_*` 只用**同时有买单的那些日子**(`n_real>0`)计算 real 与
    shadow,是真正同分母的配对差,**这是本函数现在唯一对外称"门的价值"的数字**——`agg`
    仍返回(渲染层展示为"非配对,仅供参照",不再相减)。
    """
    scan_root = Path(scan_root or SCAN_ROOT)
    shadow_df = shadow_df if shadow_df is not None else load_shadow_buys()
    shadow_map = shadow_codes_by_date(shadow_df)
    rows, used = [], []
    for d in dates:
        ap = scan_root / d / "retro" / "attribution.csv"
        if not ap.exists():
            continue
        attr = pd.read_csv(ap, dtype={"code": str})
        if attr.empty:
            continue
        gap = (gap_cache or {}).get(d)
        if gap is None:
            gap = gap_frame(d, lake=lake)
        u = day_universe(attr, gap)
        stats = gate_day_stats(u, shadow_map.get(d, set()))
        stats["date"] = d
        rows.append(stats)
        used.append(d)
    table = pd.DataFrame(rows)

    cols = ("real_oc", "shadow_oc", "market_oc", "real_gap", "shadow_gap", "market_gap")
    agg = {c: _agg_column(table, c) for c in cols}

    paired = table[table["n_real"] > 0] if len(table) else table
    agg_paired = {c: _agg_column(paired, c) for c in cols}
    gate_paired_oc = _gate_diff(agg_paired["real_oc"], agg_paired["shadow_oc"])
    gate_paired_gap = _gate_diff(agg_paired["real_gap"], agg_paired["shadow_gap"])
    return {"dates": used, "n_days": len(used), "table": table, "agg": agg,
            "agg_paired": agg_paired, "n_paired_days": int((table["n_real"] > 0).sum()) if len(table) else 0,
            "gate_value_paired_oc": gate_paired_oc, "gate_value_paired_gap": gate_paired_gap}


# ═══════════════════════════ ② 九路召回 unique 超额排序 ═══════════════════════════


def channel_day_stats(channels: pd.DataFrame, universe: pd.DataFrame,
                      ruler_col: str, elig_col: str) -> pd.DataFrame:
    """单日 `L1_channels.csv` 长表 × universe → 每路 `[n_recalled, n_unique, mean_excess,
    unique_excess, hit_rate]`。方法论镜像 `channel_audit.day_channel_stats`,参数化
    `ruler_col`/`elig_col` 让两尺共用同一份实现(唯一差异是喂哪一列,不会有"两套口径悄悄
    长歪"的风险)。market 基准 = eligible 子集中位数(该函数自己的 `elig_col`,两尺独立)。
    """
    out_cols = ["channel", "n_recalled", "n_unique", "mean_excess", "unique_excess", "hit_rate"]
    if channels is None or not len(channels) or not {"channel", "code"}.issubset(channels.columns):
        return pd.DataFrame(columns=out_cols)
    ch = channels.copy()
    ch["code"] = _code6(ch["code"])
    u = universe.copy()
    ret = pd.to_numeric(u[ruler_col], errors="coerce")
    elig = u[elig_col].astype(bool)
    mkt = ret[elig].median()
    u = u.assign(_excess=ret - mkt, _elig=elig)
    n_channels_by_code = ch.groupby("code")["channel"].nunique()
    m = ch.merge(u[["code", "_excess", "_elig"]], on="code", how="left")
    m["n_channels"] = m["code"].map(n_channels_by_code)
    m["_elig"] = m["_elig"].fillna(False)

    rows = []
    for c, sub in m.groupby("channel"):
        uniq = sub[sub["n_channels"] == 1]
        elig_all, elig_uniq = sub[sub["_elig"]], uniq[uniq["_elig"]]
        ex_all, ex_uniq = elig_all["_excess"].dropna(), elig_uniq["_excess"].dropna()
        rows.append({
            "channel": c, "n_recalled": int(len(sub)), "n_unique": int(len(uniq)),
            "mean_excess": round(float(ex_all.mean()), 6) if len(ex_all) else None,
            "unique_excess": round(float(ex_uniq.mean()), 6) if len(ex_uniq) else None,
            "hit_rate": round(float((ex_all > 0).mean()), 4) if len(ex_all) else None,
        })
    return pd.DataFrame(rows, columns=out_cols)


def _cumulative_channel(daily: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """{date: channel_day_stats 输出} → 跨日简单日频均值(同 `channel_audit.cumulative_ledger`)。"""
    cols = ["channel", "n_days", "mean_excess", "unique_excess", "hit_rate"]
    frames = [df.assign(date=d) for d, df in daily.items() if df is not None and len(df)]
    if not frames:
        return pd.DataFrame(columns=cols)
    alld = pd.concat(frames, ignore_index=True)

    def _avg(s: pd.Series):
        s = pd.to_numeric(s, errors="coerce").dropna()
        return round(float(s.mean()), 6) if len(s) else None

    rows = [{"channel": c, "n_days": int(sub["date"].nunique()), "mean_excess": _avg(sub["mean_excess"]),
             "unique_excess": _avg(sub["unique_excess"]), "hit_rate": _avg(sub["hit_rate"])}
            for c, sub in alld.groupby("channel")]
    return pd.DataFrame(rows, columns=cols)


def channel_ranking(dates: list[str], scan_root: Path | None = None,
                    gap_cache: dict[str, pd.DataFrame] | None = None,
                    lake: Path | None = None) -> dict:
    """逐日两尺 `channel_day_stats` → 两份跨日账本 → 按 `unique_excess` 排名 → 排名对照表
    (`rank_delta` = gap排名−oc排名;`sign_flip` = 两尺 unique_excess 符号是否相反)。
    """
    scan_root = Path(scan_root or SCAN_ROOT)
    daily_oc, daily_gap, used = {}, {}, []
    for d in dates:
        ap = scan_root / d / "retro" / "attribution.csv"
        cp = scan_root / d / "L1_channels.csv"
        if not (ap.exists() and cp.exists()):
            continue
        attr = pd.read_csv(ap, dtype={"code": str})
        channels = pd.read_csv(cp, dtype={"code": str})
        if attr.empty or channels.empty:
            continue
        gap = (gap_cache or {}).get(d)
        if gap is None:
            gap = gap_frame(d, lake=lake)
        u = day_universe(attr, gap)
        daily_oc[d] = channel_day_stats(channels, u, OC_COL, "elig_oc")
        daily_gap[d] = channel_day_stats(channels, u, GAP_COL, "elig_gap")
        used.append(d)
    led_oc = _cumulative_channel(daily_oc).sort_values(
        "unique_excess", ascending=False, na_position="last").reset_index(drop=True)
    led_gap = _cumulative_channel(daily_gap).sort_values(
        "unique_excess", ascending=False, na_position="last").reset_index(drop=True)
    led_oc["rank_oc"] = led_oc.index + 1
    led_gap["rank_gap"] = led_gap.index + 1
    compare = led_oc[["channel", "n_days", "unique_excess", "rank_oc"]].merge(
        led_gap[["channel", "unique_excess", "rank_gap"]], on="channel",
        suffixes=("_oc", "_gap"), how="outer")
    compare["rank_delta"] = compare["rank_gap"] - compare["rank_oc"]
    compare["sign_flip"] = (np.sign(compare["unique_excess_oc"].fillna(0))
                            != np.sign(compare["unique_excess_gap"].fillna(0)))
    compare = compare.sort_values("rank_oc").reset_index(drop=True)
    return {"dates": used, "n_days": len(used), "ledger_oc": led_oc, "ledger_gap": led_gap, "compare": compare}


# ═══════════════════════════ ③ L3 真选 edge(finalist vs bench) ═══════════════════════════


def pinned_codes_from_finalists(finalists: pd.DataFrame) -> set[str]:
    """`finalists.csv` 的 `pinned_note` 非空行 → 保送代码集合。口径同 `l3_marginal.day_frame`
    (`note = finalists["pinned_note"].fillna("").astype(str).str.strip(); ... note != ""`)。
    缺列/缺文件/空表 → 空集合(presence-gated,不是硬依赖)。

    `fillna("")` 不能省:`read_csv` 把空单元读成 NaN,`str(nan)` 是 `"nan"` 不是 `""`——
    漏这步会把每一行都误判成"有 pinned_note"(同 `l3_marginal.py` 踩过的坑)。
    """
    if finalists is None or not len(finalists) or not {"code", "pinned_note"}.issubset(finalists.columns):
        return set()
    note = finalists["pinned_note"].fillna("").astype(str).str.strip()
    return set(_code6(finalists.loc[note != "", "code"]))


def l3_day_stats(judged: pd.DataFrame, universe: pd.DataFrame, ruler_col: str, elig_col: str,
                 pinned_codes: frozenset[str] | set[str] = frozenset()) -> dict | None:
    """单日 `L3_judged_full.csv`(`finalist` 布尔列)× universe → finalist/bench 均值差。

    **不是** `l3_marginal.day_estimate` 的分层匹配反事实(那套需要 `_l3_pass1_kept.csv`
    的 `selection_reason` provenance,本任务窗口内的历史日一个都没有)——这里是更朴素的
    "真选 vs 陪跑"直接对照,brief 原话"finalists 真选 vs bench"。只统计 eligible 子集
    (与②同一 elig_col 定义),finalist 或 bench 任一侧空 → None(presence-gated)。

    **`pinned_codes`(Important-1 review fix,2026-08-07)**:📌保送票**两侧**剔除,同
    `l3_marginal.py` 的 `FORCED_REASONS` 设计原则("pinned/conviction_guard 从两侧同时剔除"
    ——它们不是 L3 排序的产物,留在 bench 就是拿"保送票恰好跑输"的收益去坐实排序有 edge,
    历史判例 `retro-l3-edge-contaminated-by-pinned-20260716`)。`L3_judged_full.csv` 本身
    在 pinned 注入**之前**落盘(`autoresearch/scan/l3/merge.py::write_finalists` 的
    `jd.to_csv(...)` 先于 `_inject_pinned_finalists()`),所以 finalist 桶(分子)架构性地
    已经干净;这个参数**主要**清理 bench 桶(分母)——保送票以 `finalist=False` 的身份留在
    未剔除前的 bench 里,拉低/拉高 bench 均值,从而放大/缩小表观 edge。
    """
    if judged is None or not len(judged) or not {"code", "finalist"}.issubset(judged.columns):
        return None
    j = judged.copy()
    j["code"] = _code6(j["code"])
    j["finalist"] = _bool_col(j, "finalist", False)
    if pinned_codes:
        j = j[~j["code"].isin(pinned_codes)]
    u = universe.copy()
    ret = pd.to_numeric(u[ruler_col], errors="coerce")
    elig = u[elig_col].astype(bool)
    mkt = ret[elig].median()
    u = u.assign(_ret=ret, _elig=elig)
    m = j.merge(u[["code", "_ret", "_elig"]], on="code", how="left")
    m["_elig"] = m["_elig"].fillna(False)
    m = m[m["_elig"]]
    fin_ret = m.loc[m["finalist"], "_ret"].dropna()
    bench_ret = m.loc[~m["finalist"], "_ret"].dropna()
    if not len(fin_ret) or not len(bench_ret):
        return None
    return {
        "n_finalist": int(len(fin_ret)), "n_bench": int(len(bench_ret)),
        "finalist_mean": float(fin_ret.mean()), "bench_mean": float(bench_ret.mean()),
        "market_median": float(mkt) if mkt == mkt else None,
        "edge": float(fin_ret.mean() - bench_ret.mean()),
    }


def l3_edge(dates: list[str], scan_root: Path | None = None,
           gap_cache: dict[str, pd.DataFrame] | None = None,
           lake: Path | None = None) -> dict:
    """逐日两尺 `l3_day_stats` → 简单日频均值聚合(同②的跨日聚合口径)。

    每日额外读 `finalists.csv` 取 📌 保送代码集合(presence-gated:缺文件/缺列 → 空集合,
    不阻断该日),两侧剔除后再算 finalist/bench(见 `l3_day_stats` docstring Important-1)。
    """
    scan_root = Path(scan_root or SCAN_ROOT)
    rows_oc, rows_gap, used = [], [], []
    for d in dates:
        ap = scan_root / d / "retro" / "attribution.csv"
        jp = scan_root / d / "L3_judged_full.csv"
        if not (ap.exists() and jp.exists()):
            continue
        attr = pd.read_csv(ap, dtype={"code": str})
        judged = pd.read_csv(jp, dtype={"code": str})
        if attr.empty or judged.empty:
            continue
        fp = scan_root / d / "finalists.csv"
        finalists = pd.read_csv(fp, dtype={"code": str}) if fp.exists() else None
        pinned = pinned_codes_from_finalists(finalists)
        gap = (gap_cache or {}).get(d)
        if gap is None:
            gap = gap_frame(d, lake=lake)
        u = day_universe(attr, gap)
        s_oc = l3_day_stats(judged, u, OC_COL, "elig_oc", pinned_codes=pinned)
        s_gap = l3_day_stats(judged, u, GAP_COL, "elig_gap", pinned_codes=pinned)
        if s_oc is None or s_gap is None:
            continue
        rows_oc.append({**s_oc, "date": d, "n_pinned_excluded": len(pinned)})
        rows_gap.append({**s_gap, "date": d, "n_pinned_excluded": len(pinned)})
        used.append(d)

    def _agg(rows: list[dict]) -> dict:
        if not rows:
            return {"n_days": 0, "finalist_mean": None, "bench_mean": None, "edge": None,
                    "n_pinned_excluded_total": 0}
        df = pd.DataFrame(rows)
        return {"n_days": len(df),
                "finalist_mean": round(float(df["finalist_mean"].mean()), 6),
                "bench_mean": round(float(df["bench_mean"].mean()), 6),
                "edge": round(float(df["edge"].mean()), 6),
                "n_pinned_excluded_total": int(df["n_pinned_excluded"].sum())}

    return {"dates": used, "n_days": len(used), "daily_oc": rows_oc, "daily_gap": rows_gap,
            "agg_oc": _agg(rows_oc), "agg_gap": _agg(rows_gap)}


# ═══════════════════════════ ④ 弃权日裁决翻转(abstention v2) ═══════════════════════════


def _verdict(opportunity: pd.Series, excess: pd.Series, eligible: pd.Series,
            codes: pd.Series, shadow_codes: set[str], degraded: bool) -> str | None:
    """镜像 `abstention_ledger.classify_abstention` 的 v2(shadow 口径)判定规则(该模块
    `_verdict_from` 的逐字翻译):shadow 机会命中 → FALSE;否则数据降级 → NEUTRAL;否则
    shadow 集合(eligible 内)全部 ≤`_CORRECT_THRESH` → CORRECT;否则 NEUTRAL。
    `shadow_codes` 为空(该日无 shadow 数据)→ None(不判,同原函数)。`degraded` 与 ruler
    无关(数据质量而非收益口径),由调用方从存量 verdict 直接传入,不在本函数重算。
    """
    if not shadow_codes:
        return None
    in_shadow = codes.isin(shadow_codes)
    hit = opportunity.fillna(False).astype(bool) & in_shadow
    if hit.any():
        return "FALSE"
    if degraded:
        return "NEUTRAL"
    scope = excess[eligible.fillna(False).astype(bool) & in_shadow & excess.notna()]
    if len(scope) and bool((scope <= _CORRECT_THRESH).all()):
        return "CORRECT"
    return "NEUTRAL"


def abstention_flip(dates: list[str], scan_root: Path | None = None,
                    lake: Path | None = None, shadow_df: pd.DataFrame | None = None,
                    gap_cache: dict[str, pd.DataFrame] | None = None) -> dict:
    """逐个真弃权日(0买,且有 `rejection_attribution.csv` + `abstention_verdict.json`):
    oc 侧直接读存量 `excess_2`/`opportunity`/`status_v2`(零重算风险的 ground truth);
    gap 侧用 `gap_frame` 重算 eligible/excess/opportunity,套用同一 `_verdict()`。

    **自检**(不是断言,是诊断读数):对 oc 侧存量 `excess_2`/`opportunity` 套用本模块的
    `_verdict()`,理应精确复现存量 `status_v2` —— `n_reproduced/n_checked_reproduction`
    进结果,复现失败说明本模块的判定重实现有偏差(报告里如实列出,不吞)。
    """
    scan_root = Path(scan_root or SCAN_ROOT)
    shadow_df = shadow_df if shadow_df is not None else load_shadow_buys()
    shadow_map = shadow_codes_by_date(shadow_df)
    rows: list[dict] = []
    n_checked = n_reproduced = 0
    for d in dates:
        rejp = scan_root / d / "retro" / "rejection_attribution.csv"
        vp = scan_root / d / "retro" / "abstention_verdict.json"
        if not (rejp.exists() and vp.exists()):
            continue
        rej = pd.read_csv(rejp, dtype={"code": str})
        if rej.empty or "code" not in rej.columns or "final_action" not in rej.columns:
            continue
        rej["code"] = _code6(rej["code"])
        if (rej["final_action"] == "BUY").any():
            continue   # NOT_ABSTAINED 日,与 abstention_ledger.roll() 同样跳过(不可比对)
        verdict = json.loads(vp.read_text(encoding="utf-8"))
        status_oc = verdict.get("status_v2")
        degraded = verdict.get("data_quality") == "DEGRADED"
        shadow_codes = shadow_map.get(d, set())

        # Minor review fix(2026-08-07):production `classify_abstention` 的 eligible 定义是
        # `rejected & mature & buyable`(见 abstention_ledger.py);此处 `rejected` 在本函数
        # 语境下恒真(已过滤到全 ABSTAIN 日),故应为 `buyable & mature`。此前误写成
        # `buyable & tradable`——`rejection_attribution.csv` 根本没有 `tradable` 列,
        # `_bool_col` 缺列即回退默认值 True,恒真、从未真正过滤,漏了 `mature`。11 天自检
        # 11/11 精确复现存量证明这个疏漏经验上不改变任何一天的判定,但口径应该写对。
        elig_oc = _bool_col(rej, "buyable", True) & _bool_col(rej, "mature", True)
        excess_oc = pd.to_numeric(rej.get("excess_2"), errors="coerce")
        opp_oc = _bool_col(rej, "opportunity", False)
        v_oc_reproduced = _verdict(opp_oc, excess_oc, elig_oc, rej["code"], shadow_codes, degraded)
        n_checked += 1
        if v_oc_reproduced == status_oc:
            n_reproduced += 1

        gap = (gap_cache or {}).get(d)
        if gap is None:
            gap = gap_frame(d, lake=lake)
        g = gap.copy()
        if len(g):
            g["code"] = _code6(g["code"])
        else:
            g = pd.DataFrame(columns=["code", GAP_COL, "buyable_c1", "eligible_gap"])
        m = rej[["code", "final_action"]].merge(g, on="code", how="left")
        ret_gap = pd.to_numeric(m[GAP_COL], errors="coerce").clip(-ruler.GAP_CLIP, ruler.GAP_CLIP)
        elig_gap = m["buyable_c1"].fillna(True).astype(bool) & ret_gap.notna()
        mkt_gap = ret_gap[elig_gap].median()
        excess_gap = ret_gap - mkt_gap
        opp_gap = ((m["final_action"] != "BUY") & elig_gap
                  & excess_gap.notna() & (excess_gap >= _OPP_THRESH))
        status_gap = _verdict(opp_gap, excess_gap, elig_gap, m["code"], shadow_codes, degraded)

        flipped = status_gap is not None and status_oc is not None and status_gap != status_oc
        rows.append({"date": d, "status_oc": status_oc, "status_oc_reproduced": v_oc_reproduced,
                     "status_gap": status_gap, "flipped": bool(flipped), "n_shadow": len(shadow_codes)})
    table = pd.DataFrame(rows)
    flipped_dates = table.loc[table["flipped"], "date"].tolist() if len(table) else []
    return {"n_days": len(table), "n_checked_reproduction": n_checked, "n_reproduced": n_reproduced,
            "table": table, "flipped_dates": flipped_dates}


# ═══════════════════════════ 编排 / 渲染 / CLI ═══════════════════════════


def analyze(days: int = 60, scan_root: Path | None = None, lake: Path | None = None,
           shadow_path: Path | None = None) -> dict:
    """四节总编排。`gap_cache` 只算一次(每 scan 日 2 次湖读取),四节共用,避免重复 IO。"""
    scan_root = Path(scan_root or SCAN_ROOT)
    dates = scan_dates_with_attribution(scan_root, limit=days)
    gap_cache = {d: gap_frame(d, lake=lake) for d in dates}
    shadow_df = load_shadow_buys(shadow_path)
    gate = gate_value(dates, scan_root=scan_root, shadow_df=shadow_df, gap_cache=gap_cache, lake=lake)
    channels = channel_ranking(dates, scan_root=scan_root, gap_cache=gap_cache, lake=lake)
    l3 = l3_edge(dates, scan_root=scan_root, gap_cache=gap_cache, lake=lake)
    abstention = abstention_flip(dates, scan_root=scan_root, lake=lake,
                                 shadow_df=shadow_df, gap_cache=gap_cache)
    return {"dates": dates, "n_dates": len(dates), "gate": gate, "channels": channels,
            "l3": l3, "abstention": abstention}


def _retirement_notes(result: dict) -> list[str]:
    """尾节"作废/待重验清单"——两尺符号相反的历史结论逐条点名。至少覆盖 value 路第一 /
    momentum 相位条件性 / 0买日空仓正确性(brief 硬性要求)。每条的数字都来自 `result`,
    没有算出来的(如 momentum 相位条件性的分 regime IC)不编数字,只说明为何本报告没有直接
    复算。"""
    notes: list[str] = []
    compare = result["channels"]["compare"]

    def _row(name: str):
        r = compare[compare["channel"] == name]
        return r.iloc[0] if len(r) else None

    v = _row("value")
    if v is None:
        notes.append("**value 路「unique 超额 T2 全路第一」**:窗口内 `value` 路数据不足,无法判定。")
    elif bool(v["sign_flip"]):
        notes.append(
            "**value 路「unique 超额 T2 全路第一」—— 作废/待重验**:fwd_2_oc 下 unique_excess="
            f"{_fmt_pct(v['unique_excess_oc'])}(第{int(v['rank_oc'])}名),gap_c1_o2 下 "
            f"unique_excess={_fmt_pct(v['unique_excess_gap'])}(第{int(v['rank_gap'])}名)—— **符号相反**,"
            "旧尺下的名次不能再直接引用。")
    else:
        rank_note = "排名不变" if v["rank_delta"] == 0 else f"排名 {int(v['rank_oc'])}→{int(v['rank_gap'])}"
        notes.append(
            "value 路「unique 超额」两尺**符号一致**(" + rank_note + f"):fwd_2_oc "
            f"{_fmt_pct(v['unique_excess_oc'])} vs gap_c1_o2 {_fmt_pct(v['unique_excess_gap'])}——"
            "方向性结论未被推翻;若排名有变动,「全路第一」这个具体措辞仍建议以本报告②节读数复核。")

    m = _row("momentum")
    mom_extra = ""
    if m is not None:
        mom_extra = (f"本报告②节的 momentum 路读数供参考:fwd_2_oc unique_excess="
                     f"{_fmt_pct(m['unique_excess_oc'])} vs gap_c1_o2 {_fmt_pct(m['unique_excess_gap'])}"
                     f"({'符号相反' if bool(m['sign_flip']) else '符号一致'})——"
                     "这是渠道层面的粗读数,不等于分 regime IC 的直接复算。")
    notes.append(
        "**momentum 相位条件性(`docs/research/2026-08-04-momentum-phase-conditional-ic.md`)"
        "—— 待重验,本任务未直接复算**:该结论建立在 fwd_2_oc 的分 regime rank IC 上,本报告四节"
        "都没有重跑分相位 IC,不构成对该结论的验证或推翻。" + (" " + mom_extra if mom_extra else ""))

    abst = result["abstention"]
    if abst["n_days"] == 0:
        notes.append(
            "**0买日「空仓正确性」**:窗口内没有同时具备 `rejection_attribution.csv` + "
            "`abstention_verdict.json` 的可比对弃权日,无法判定(该产物是本波才开始留痕,"
            "更早的 scan 日没有)。")
    elif abst["flipped_dates"]:
        notes.append(
            "**0买日「空仓正确性」—— 部分作废**:" + f"{abst['n_days']} 个可比对弃权日中 "
            f"{len(abst['flipped_dates'])} 日两尺裁决翻转:{', '.join(abst['flipped_dates'])}"
            "——这些日子引用旧尺 v1/v2 裁决前需要用 gap 尺重判。")
    else:
        notes.append(
            f"0买日「空仓正确性」结论**两尺一致**:{abst['n_days']} 个可比对弃权日,0 日翻转"
            f"(自检:{abst['n_reproduced']}/{abst['n_checked_reproduction']} 日精确复现存量 "
            "status_v2,本模块的判定重实现可信)。")

    # 第 4 条(brief"至少覆盖"三条之外,本报告③节自己的数据支持就一并点名;不是对
    # l3_marginal.py 那套分层匹配反事实估计的复算,只是本报告 finalist-vs-bench 直接对照的读数)
    l3 = result["l3"]
    _NEAR_ZERO = 0.002   # 0.2pp 内视为"约等于 0",避免把噪声级差异夸大成"符号翻转"
    if l3["n_days"] == 0:
        notes.append("L3 真选 edge(finalist vs bench,本报告③节口径):窗口内数据不足,无法判定。")
    else:
        edge_oc, edge_gap = l3["agg_oc"]["edge"], l3["agg_gap"]["edge"]
        pinned_note = (f"(📌保送票已两侧剔除,累计 {l3['agg_oc']['n_pinned_excluded_total']} "
                       "票次;剔除前 oc 侧头条曾读 +1.59%,约 42% 来自 bench 被保送已知跑输"
                       "仓位拉低——见 task-15-report.md v2 修订说明)")
        if abs(edge_gap) < _NEAR_ZERO <= abs(edge_oc):
            notes.append(
                f"**L3 真选 edge(finalist vs bench)—— 待重验**:fwd_2_oc 下 {_fmt_pct(edge_oc)}"
                f",gap_c1_o2 下坍缩到近乎 0({_fmt_pct(edge_gap)})——不是干净的符号翻转,是"
                f"「有意义的正 edge」在隔夜尺下消失,同样不能直接沿用旧尺读数。{pinned_note}")
        elif (edge_oc > 0) != (edge_gap > 0):
            notes.append(
                f"**L3 真选 edge(finalist vs bench)—— 作废/待重验**:fwd_2_oc {_fmt_pct(edge_oc)} "
                f"vs gap_c1_o2 {_fmt_pct(edge_gap)}—— 符号相反。{pinned_note}")
        else:
            notes.append(
                f"L3 真选 edge(finalist vs bench)两尺**符号一致**:fwd_2_oc {_fmt_pct(edge_oc)} "
                f"vs gap_c1_o2 {_fmt_pct(edge_gap)}。{pinned_note}")
    return notes


def render(result: dict) -> str:
    dates = result["dates"]
    span = f"{dates[0]} ~ {dates[-1]}（{len(dates)} 日）" if dates else "无数据"
    out = [
        "# 两尺对照报告(gap_c1_o2 vs fwd_2_oc)—— 换尺认知底片",
        "",
        "> **v2 修订(2026-08-07,独立 reviewer 复核后)**:reviewer 独立只读复算四节数字"
        "**零差异**,但指出 3 处 Important 口径问题,已全部修正——"
        "①「门的价值」原是非配对相减(真实 6 日 vs 影子独立聚合的全窗口),现改为**配对**"
        "(只用同一批买单日,见下方「非配对/配对」两张表);"
        "③ L3 edge 的 bench(分母)桶原未剔除 📌 保送票,现两侧剔除"
        "(`retro-l3-edge-contaminated-by-pinned-20260716` 判例——保送不是 L3 排序的产物),"
        "oc 头条从 **+1.59% 修正为 +0.93%**(gap 侧 -0.01%→-0.06%,仍近零,方向性结论不变);"
        "`--selftest` 补上此前 3 处鉴别力盲区(①聚合函数此前从未被调用、④shadow门槛/"
        "CORRECT阈值符号此前无边界用例)并对 5 处已知变异逐一重跑确认现在全部报红。"
        "v1 的具体数字(如 +1.59%)不应再被引用,详见 task-15-report.md 的 review 回复。", "",
        "> 2026-08-05 用户裁定:评判主尺从 `fwd_2_oc`(D+1开买→D+2收卖)改为隔夜尺 "
        "`gap_c1_o2`(D+1收买→D+2开卖)。**本报告不是论证新尺更优**,只回答:换尺之后,"
        "哪些基于旧尺的历史结论会翻?", "",
        f"窗口:{span}(有 `retro/attribution.csv` 的 scan 日;更晚的日期 T+2 尚未成熟)。"
        "`gap_c1_o2` 由 `context/lake/daily` 现算(attribution.csv 现无此列)——"
        "已用 2026-08-03/000001 逐位比特验证湖重算的 `fwd_2_oc` 与 attribution.csv 原值一致,"
        "确认湖的交易日序列与 retro 同源、D+1/D+2 定位可信(细节见 task-15-report.md)。", "",
    ]

    # ── ① 门的价值 ──
    gate = result["gate"]
    out += ["## ① 门的价值(真实 vs 影子 vs 市场)", ""]
    if not gate["n_days"]:
        out += ["_窗口内无可用数据。_", ""]
    else:
        a = gate["agg"]
        out += ["**非配对(仅供参照,不构成可比差值)**:三条线各自独立聚合,分母不同——"
               "「真实」只在有买单的日子有数,「影子」/「市场」是全窗口。**不要把这一段的"
               "任意两行相减**(见下方配对版说明)。", "",
               "| 线 | fwd_2_oc 均值 | (n日) | gap_c1_o2 均值 | (n日) |", "|---|---:|---:|---:|---:|"]
        for label, oc_k, gap_k in (("真实(bought)", "real_oc", "real_gap"),
                                   ("影子(shadow_buys),全窗口", "shadow_oc", "shadow_gap"),
                                   ("市场(eligible),全窗口", "market_oc", "market_gap")):
            out.append(f"| {label} | {_fmt_pct(a[oc_k]['value'])} | {a[oc_k]['n_days']} | "
                      f"{_fmt_pct(a[gap_k]['value'])} | {a[gap_k]['n_days']} |")
        ap_ = gate["agg_paired"]
        out += ["",
               f"**配对版(同 {gate['n_paired_days']} 个买单日,真正的门的价值 —— 本报告唯一"
               "可引用的「门的价值」数字)**:", "",
               "| 线(同一批日子) | fwd_2_oc 均值 | gap_c1_o2 均值 |", "|---|---:|---:|",
               f"| 真实(bought) | {_fmt_pct(ap_['real_oc']['value'])} | {_fmt_pct(ap_['real_gap']['value'])} |",
               f"| 影子(shadow_buys,限同 {gate['n_paired_days']} 日) | "
               f"{_fmt_pct(ap_['shadow_oc']['value'])} | {_fmt_pct(ap_['shadow_gap']['value'])} |",
               "", f"- **门的价值(真实−影子,配对)**:fwd_2_oc "
               f"{_fmt_pct(gate['gate_value_paired_oc'])} vs gap_c1_o2 "
               f"{_fmt_pct(gate['gate_value_paired_gap'])}。"]
        if gate["n_paired_days"] < _THIN_DAYS:
            out.append(f"- ⚠薄样本:仅 {gate['n_paired_days']} 个买单日(0买日占绝大多数)——"
                      "即便是配对版本,这两个数字的统计功效依然弱,不建议直接当结论引用,只作"
                      "方向性参考。")
        out.append("")

    # ── ② 九路召回 unique 超额排序 ──
    out += ["## ② 九路召回 unique 超额排序(两尺对照)", ""]
    compare = result["channels"]["compare"]
    if not len(compare):
        out += ["_窗口内无 L1_channels.csv × attribution.csv 可配对数据。_", ""]
    else:
        out += ["| 路 | 天数 | unique超额(oc) | 排名(oc) | unique超额(gap) | 排名(gap) | Δ排名 | 符号翻转 |",
               "|---|---:|---:|---:|---:|---:|---:|---|"]
        for r in compare.itertuples(index=False):
            flip = "⚠是" if bool(r.sign_flip) else "否"
            rd = "—" if pd.isna(r.rank_delta) else f"{int(r.rank_delta):+d}"
            thin = " ⚠薄样本" if r.n_days < _THIN_DAYS else ""
            out.append(f"| {r.channel}{thin} | {r.n_days} | {_fmt_pct(r.unique_excess_oc)} | "
                      f"{int(r.rank_oc) if pd.notna(r.rank_oc) else '—'} | "
                      f"{_fmt_pct(r.unique_excess_gap)} | "
                      f"{int(r.rank_gap) if pd.notna(r.rank_gap) else '—'} | {rd} | {flip} |")
        out.append("")

    # ── ③ L3 真选 edge ──
    l3 = result["l3"]
    out += ["## ③ L3 真选 edge(finalist vs bench,两尺对照;📌保送票两侧已剔除)", ""]
    if not l3["n_days"]:
        out += ["_窗口内无 L3_judged_full.csv × attribution.csv 可配对数据。_", ""]
    else:
        oc, gap = l3["agg_oc"], l3["agg_gap"]
        out += [f"finalist(分子)桶因读取 pinned 注入前的 `L3_judged_full.csv` 架构性干净;"
               f"bench(分母)桶另有 📌 保送票剔除(累计 {oc['n_pinned_excluded_total']} 票次,"
               "同 `l3_marginal.py` 的 `FORCED_REASONS` 两侧剔除原则,`retro-l3-edge-"
               "contaminated-by-pinned-20260716` 判例——保送票不是 L3 排序的产物,留着就是拿"
               "它的收益去证明/证伪排序有 edge)。", "",
               "| 尺 | 天数 | finalist均值 | bench均值 | edge(finalist−bench) |",
               "|---|---:|---:|---:|---:|",
               f"| fwd_2_oc | {oc['n_days']} | {_fmt_pct(oc['finalist_mean'])} | "
               f"{_fmt_pct(oc['bench_mean'])} | {_fmt_pct(oc['edge'])} |",
               f"| gap_c1_o2 | {gap['n_days']} | {_fmt_pct(gap['finalist_mean'])} | "
               f"{_fmt_pct(gap['bench_mean'])} | {_fmt_pct(gap['edge'])} |", ""]
        if l3["n_days"] < _THIN_DAYS:
            out += [f"- ⚠薄样本:仅 {l3['n_days']} 个可配对日(需同时有 attribution.csv × "
                   "L3_judged_full.csv)——edge 数字方向性参考,不宜当定量结论引用。", ""]

    # ── ④ 弃权日裁决翻转 ──
    abst = result["abstention"]
    out += ["## ④ 弃权日裁决翻转(abstention v2,shadow 口径重算)", ""]
    if not abst["n_days"]:
        out += ["_窗口内无可比对的弃权裁决日(缺 rejection_attribution.csv / abstention_verdict.json)。_", ""]
    else:
        out += [f"- 自检:{abst['n_reproduced']}/{abst['n_checked_reproduction']} 日,"
               "本模块 `_verdict()` 套用存量 fwd_2_oc 读数精确复现存量 `status_v2`。", "",
               "| 日期 | 裁决(oc,存量) | 裁决(gap,重算) | 翻转 |", "|---|---|---|---|"]
        for r in abst["table"].itertuples(index=False):
            flag = "⚠是" if r.flipped else "否"
            out.append(f"| {r.date} | {r.status_oc or '—'} | {r.status_gap or '—'} | {flag} |")
        out += ["", f"- **{len(abst['flipped_dates'])}/{abst['n_days']} 日翻转**"
               + (f":{', '.join(abst['flipped_dates'])}" if abst["flipped_dates"] else "。")]
        if abst["n_days"] < _THIN_DAYS:
            out.append(f"- ⚠薄样本:仅 {abst['n_days']} 个可比对弃权日(该产物本波才开始留痕,"
                      "更早的 scan 日没有 rejection_attribution.csv/abstention_verdict.json)。")
        out.append("")

    # ── ⑤ 作废/待重验清单 ──
    out += ["## ⑤ 作废/待重验清单", ""]
    out += [f"- {n}" for n in _retirement_notes(result)]
    out += ["", "## 方法论 / 局限", "",
           "- 市场基准口径全篇不统一命名同一套:①按 brief 原话用**均值**;②③④用 "
           "**eligible 子集中位数**(与 `channel_audit`/`rejection_attribution`/`l3_marginal` "
           "各自的既有产物最接近的口径),两尺各自独立定义 eligible,不混用。",
           "- `gap_c1_o2` 的 D+1/D+2 由湖(`context/lake/daily`)文件名排序推断,不查交易日历——"
           "与 `paper_nav.trade_days()` 同一口径;已做真实数据逐位比特验证(见上)。",
           "- 本报告①节是 `paper_nav`(真实−影子=门的价值)的**简化**对照,不是 NAV 复利模拟——"
           "理由与限制见模块 docstring。",
           "- ③节是 finalist-vs-bench 直接对照,不是 `l3_marginal.py` 的分层匹配反事实估计"
           "(窗口内历史日缺 `_l3_pass1_kept.csv` provenance,做不了那一套)。",
           "- 样本量:见各节 n_days;不足的地方本报告如实标注,不外推。",
           "- **复现**:`uv run --no-sync python -m autoresearch.research.ruler_compare run "
           "--days 60`(全量重算并覆盖本文件);`uv run --no-sync python -m "
           "autoresearch.research.ruler_compare --selftest`(离线合成数据自测四节数值,"
           "含①②③④共 5 处已知算法级变异的边界用例);`uv run --no-sync python -m pytest -q "
           "tests/research/test_ruler_compare.py`(49 例,IO 层 + 端到端 + 边界情形)。", ""]
    return "\n".join(out)


# ───────────────────────── selftest(离线合成两日数据,无 IO,手算核对) ─────────────────────────


def _synthetic_universe(day: int) -> pd.DataFrame:
    """两日合成 universe(5 只/日,day∈{1,2})——数值见模块内 `_selftest()` 的手算注释。"""
    if day == 1:
        rows = [
            {"code": "000001", OC_COL: 0.05, GAP_COL: 0.03, "bought": True},
            {"code": "000002", OC_COL: 0.09, GAP_COL: -0.02, "bought": False},
            {"code": "000003", OC_COL: 0.01, GAP_COL: 0.01, "bought": False},
            {"code": "000004", OC_COL: -0.03, GAP_COL: 0.06, "bought": False},
            {"code": "000005", OC_COL: 0.02, GAP_COL: -0.01, "bought": False},
        ]
    else:
        rows = [
            {"code": "000001", OC_COL: 0.02, GAP_COL: 0.05, "bought": True},
            {"code": "000002", OC_COL: 0.03, GAP_COL: -0.02, "bought": False},
            {"code": "000003", OC_COL: -0.01, GAP_COL: 0.03, "bought": False},
            {"code": "000006", OC_COL: 0.06, GAP_COL: -0.04, "bought": False},
            {"code": "000007", OC_COL: 0.00, GAP_COL: 0.08, "bought": False},
        ]
    df = pd.DataFrame(rows)
    df["elig_oc"] = True
    df["elig_gap"] = True
    return df


def _selftest() -> int:
    """合成两日数据,手算核对四节数值(brief Step2 硬性要求)。全离线,不读文件系统。"""
    fails: list[str] = []

    def _check(name: str, got, want, tol: float = 1e-6):
        if got is None or want is None:
            if got != want:
                fails.append(f"{name}: got={got} want={want}")
            return
        if abs(got - want) > tol:
            fails.append(f"{name}: got={got:.6f} want={want:.6f}")

    u1, u2 = _synthetic_universe(1), _synthetic_universe(2)

    # ── ① 门的价值:手算见模块 docstring 同款推导(day1/day2 均值 → 简单日频均值聚合) ──
    shadow1, shadow2 = {"000002", "000003"}, {"000002", "000006"}
    g1, g2 = gate_day_stats(u1, shadow1), gate_day_stats(u2, shadow2)
    _check("gate.day1.real_oc", g1["real_oc"], 0.05)
    _check("gate.day1.shadow_oc", g1["shadow_oc"], 0.05)      # mean(0.09,0.01)
    _check("gate.day1.market_oc", g1["market_oc"], 0.028)
    _check("gate.day1.shadow_gap", g1["shadow_gap"], -0.005)  # mean(-0.02,0.01)
    _check("gate.day2.shadow_oc", g2["shadow_oc"], 0.045)     # mean(0.03,0.06)
    _check("gate.day2.shadow_gap", g2["shadow_gap"], -0.03)   # mean(-0.02,-0.04)

    # Important-3 review fix(2026-08-07,M1):此前这里手写了一遍聚合/相减,`gate_value()`
    # 真正用的模块级聚合函数 `_agg_column`/`_gate_diff` 从未被 `--selftest` 调用过——
    # "real−shadow 反写成 shadow−real" 这类符号变异因此漏检。现在直接调用被测函数本身。
    # 三日(非两日)夹具:两日时 mean 恒等于 median,mean→median 这类变异也测不出来。
    table3 = pd.DataFrame([
        {"date": "d1", "real_oc": 0.05, "shadow_oc": 0.09, "real_gap": 0.03, "shadow_gap": -0.02, "n_real": 1},
        {"date": "d2", "real_oc": 0.02, "shadow_oc": 0.03, "real_gap": 0.05, "shadow_gap": -0.02, "n_real": 1},
        {"date": "d3", "real_oc": 0.20, "shadow_oc": 0.03, "real_gap": -0.10, "shadow_gap": 0.03, "n_real": 1},
    ])
    agg_real_oc = _agg_column(table3, "real_oc")
    agg_shadow_oc = _agg_column(table3, "shadow_oc")
    agg_real_gap = _agg_column(table3, "real_gap")
    agg_shadow_gap = _agg_column(table3, "shadow_gap")
    _check("agg_column.real_oc.n_days", agg_real_oc["n_days"], 3, tol=0)
    _check("agg_column.real_oc.mean_not_median", agg_real_oc["value"], (0.05 + 0.02 + 0.20) / 3)
    if abs(agg_real_oc["value"] - 0.05) < 1e-6:   # 0.05 恰是三值的中位数——若命中说明被换成了 median
        fails.append("agg_column: real_oc 读数等于中位数 0.05,疑似 mean 被换成了 median")
    _check("agg_column.shadow_oc.mean", agg_shadow_oc["value"], (0.09 + 0.03 + 0.03) / 3)
    gate_diff_oc = _gate_diff(agg_real_oc, agg_shadow_oc)
    gate_diff_gap = _gate_diff(agg_real_gap, agg_shadow_gap)
    _check("gate_diff.oc", gate_diff_oc, (0.05 + 0.02 + 0.20) / 3 - (0.09 + 0.03 + 0.03) / 3)
    _check("gate_diff.gap", gate_diff_gap,
          (0.03 + 0.05 - 0.10) / 3 - (-0.02 - 0.02 + 0.03) / 3)
    if not (gate_diff_oc > 0 > gate_diff_gap):
        fails.append("gate_diff: 期望本合成样例门的价值两尺符号相反(oc>0>gap),用于验证符号翻转检测")
    if _gate_diff(agg_shadow_oc, agg_real_oc) == gate_diff_oc:  # 顺序颠倒应给出不同(相反符号)的数
        fails.append("gate_diff: real/shadow 参数顺序颠倒应改变结果符号,当前没有变化——疑似恒等函数")
    _check("agg_column.empty_table.n_days", _agg_column(pd.DataFrame(), "real_oc")["n_days"], 0, tol=0)

    # ── ② 九路召回 unique 超额:chan_a/chan_b 两路,000001 两日皆双路重叠(非 unique) ──
    ch1 = pd.DataFrame([
        {"channel": "chan_a", "code": "000001"}, {"channel": "chan_a", "code": "000002"},
        {"channel": "chan_b", "code": "000001"}, {"channel": "chan_b", "code": "000003"},
    ])
    ch2 = pd.DataFrame([
        {"channel": "chan_a", "code": "000001"}, {"channel": "chan_a", "code": "000006"},
        {"channel": "chan_b", "code": "000001"}, {"channel": "chan_b", "code": "000007"},
    ])
    day_a_oc = {"d1": channel_day_stats(ch1, u1, OC_COL, "elig_oc"),
                "d2": channel_day_stats(ch2, u2, OC_COL, "elig_oc")}
    day_a_gap = {"d1": channel_day_stats(ch1, u1, GAP_COL, "elig_gap"),
                "d2": channel_day_stats(ch2, u2, GAP_COL, "elig_gap")}
    led_oc, led_gap = _cumulative_channel(day_a_oc), _cumulative_channel(day_a_gap)
    a_oc = led_oc.set_index("channel").loc["chan_a", "unique_excess"]
    b_oc = led_oc.set_index("channel").loc["chan_b", "unique_excess"]
    a_gap = led_gap.set_index("channel").loc["chan_a", "unique_excess"]
    b_gap = led_gap.set_index("channel").loc["chan_b", "unique_excess"]
    _check("channel.chan_a.unique_excess_oc", a_oc, 0.055)     # mean(0.07, 0.04)
    _check("channel.chan_a.unique_excess_gap", a_gap, -0.05)   # mean(-0.03, -0.07)
    _check("channel.chan_b.unique_excess_oc", b_oc, -0.015)    # mean(-0.01, -0.02)
    _check("channel.chan_b.unique_excess_gap", b_gap, 0.025)   # mean(0.00, 0.05)
    if not (a_oc > b_oc and b_gap > a_gap):
        fails.append("channel: 期望本合成样例排名两尺互换(oc: a>b;gap: b>a)")

    # ── ③ L3 真选 edge:finalist={000001,+新票} vs bench=其余 ──
    j1 = pd.DataFrame([{"code": c, "finalist": c in {"000001", "000002"}}
                       for c in u1["code"]])
    j2 = pd.DataFrame([{"code": c, "finalist": c in {"000001", "000006"}}
                       for c in u2["code"]])
    l3_1_oc = l3_day_stats(j1, u1, OC_COL, "elig_oc")
    l3_2_oc = l3_day_stats(j2, u2, OC_COL, "elig_oc")
    l3_1_gap = l3_day_stats(j1, u1, GAP_COL, "elig_gap")
    l3_2_gap = l3_day_stats(j2, u2, GAP_COL, "elig_gap")
    _check("l3.day1.edge_oc", l3_1_oc["edge"], 0.07)            # mean(0.05,0.09) - mean(0.01,-0.03,0.02)
    _check("l3.day1.edge_gap", l3_1_gap["edge"], -0.015)        # mean(0.03,-0.02) - mean(0.01,0.06,-0.01)
    _check("l3.day2.edge_oc", l3_2_oc["edge"], 0.04 - (0.03 - 0.01 + 0.00) / 3)
    _check("l3.day2.edge_gap", l3_2_gap["edge"], -0.025)        # mean(0.05,-0.04) - mean(-0.02,0.03,0.08)
    edge_oc_agg = (l3_1_oc["edge"] + l3_2_oc["edge"]) / 2
    edge_gap_agg = (l3_1_gap["edge"] + l3_2_gap["edge"]) / 2
    if not (edge_oc_agg > 0 > edge_gap_agg):
        fails.append("l3.agg: 期望本合成样例 edge 两尺符号相反(oc>0>gap)")

    # ── ④ 弃权日裁决:同一组 shadow 机会,oc 侧全部达标 CORRECT,gap 侧命中机会转 FALSE ──
    codes = pd.Series(["000001", "000002", "000003"])
    elig3 = pd.Series([True, True, True])
    shadow34 = {"000001", "000002"}
    opp_oc = pd.Series([False, False, False])
    excess_oc = pd.Series([-0.05, -0.03, 0.10])
    v_oc = _verdict(opp_oc, excess_oc, elig3, codes, shadow34, False)
    opp_gap = pd.Series([True, False, False])
    excess_gap = pd.Series([0.03, -0.03, 0.10])
    v_gap = _verdict(opp_gap, excess_gap, elig3, codes, shadow34, False)
    if v_oc != "CORRECT":
        fails.append(f"abstention: oc 侧期望 CORRECT,got {v_oc}")
    if v_gap != "FALSE":
        fails.append(f"abstention: gap 侧期望 FALSE(命中 shadow 机会),got {v_gap}")
    if _verdict(opp_oc, excess_oc, elig3, codes, set(), False) is not None:
        fails.append("abstention: 空 shadow_codes 应返回 None(不判)")
    if _verdict(opp_oc, excess_oc, elig3, codes, shadow34, True) != "NEUTRAL":
        fails.append("abstention: degraded=True 应强制 NEUTRAL,不可判 CORRECT")

    # Important-3 review fix(2026-08-07,M4):机会命中存在,但命中的那只**不在** shadow 里
    # → 不该触发 FALSE。此前的夹具里 opp_gap 唯一为 True 的那行本来就在 shadow 集合内,
    # 去掉 `_verdict()` 里的 `& in_shadow` 门槛后结果不变,漏检了 v2(shadow 口径)相对 v1
    # (全市场口径)的核心设计点。这里 000001 有机会但不在 shadow(shadow 只含 000002)。
    codes4 = pd.Series(["000001", "000002"])
    elig4 = pd.Series([True, True])
    opp4 = pd.Series([True, False])                 # 000001 有机会…
    excess4 = pd.Series([0.05, -0.05])               # …但 000002(shadow 内)excess ≤ -0.02
    v4 = _verdict(opp4, excess4, elig4, codes4, {"000002"}, False)
    if v4 != "CORRECT":
        fails.append(f"abstention: 机会命中但不在 shadow 内不应触发 FALSE(缺 `&in_shadow` 的"
                     f"变异应在此报红),期望 CORRECT,got {v4}")

    # Important-3 review fix(2026-08-07,M5):`_CORRECT_THRESH` 的符号边界。excess=-0.01
    # 是负的但没有 ≤-0.02 那么负——正确阈值(-0.02)下应判 NEUTRAL;若阈值符号反写成 +0.02,
    # -0.01 ≤ +0.02 恒真,会误判 CORRECT。此前夹具用 -0.05/-0.03(离 ±0.02 都很远),两种
    # 阈值符号都得到同样的判定,测不出符号错误。
    codes5 = pd.Series(["000001"])
    elig5 = pd.Series([True])
    opp5 = pd.Series([False])
    excess5 = pd.Series([-0.01])
    v5 = _verdict(opp5, excess5, elig5, codes5, {"000001"}, False)
    if v5 != "NEUTRAL":
        fails.append(f"abstention: excess=-0.01 未达 -0.02 门槛不应判 CORRECT(阈值符号反的"
                     f"变异应在此报红),期望 NEUTRAL,got {v5}")
    # 边界值本身(恰好 -0.02)应该算作"达标"(`<=`,闭区间)
    if _verdict(opp5, pd.Series([-0.02]), elig5, codes5, {"000001"}, False) != "CORRECT":
        fails.append("abstention: excess 恰好等于 -0.02(闭区间边界)应判 CORRECT")

    if fails:
        print("SELFTEST ❌")
        for x in fails:
            print("  -", x)
        return 1
    print("SELFTEST ✅  ①门的价值(真调 _agg_column/_gate_diff·mean≠median 三日夹具·符号相反"
         "已捕获) ②九路召回(排名互换已捕获) ③L3edge(符号相反已捕获) "
         "④弃权裁决(CORRECT→FALSE 翻转·shadow门槛M4反例·CORRECT阈值符号M5边界已捕获)")
    return 0


# ───────────────────────── CLI ─────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autoresearch.research.ruler_compare",
                                 description="两尺对照(gap_c1_o2 vs fwd_2_oc)—— 换尺认知底片,证据件,零 LLM")
    ap.add_argument("mode", nargs="?", choices=["run"], help="run=读现存 scan 历史,产出 docs/research 报告")
    ap.add_argument("--days", type=int, default=60, help="回看最近 N 个有 attribution.csv 的 scan 日(默认 60)")
    ap.add_argument("--scan-root", default=None)
    ap.add_argument("--lake", default=None)
    ap.add_argument("--shadow-path", default=None)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--selftest", action="store_true", help="合成数据自测四节数值(离线,无 IO)")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.mode != "run":
        ap.print_help()
        return 1

    result = analyze(days=args.days, scan_root=args.scan_root, lake=args.lake,
                     shadow_path=args.shadow_path)
    body = render(result)
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(body, encoding="utf-8")
    print(f"[ruler_compare] {result['n_dates']} 个 scan 日 → {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

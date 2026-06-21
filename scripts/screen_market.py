#!/usr/bin/env python3
"""scan-market · L0–L2 — A股全市场确定性漏斗(零 LLM)。

design: docs/specs/2026-06-20-scan-market-design.md

把 ~5,400 只 A股用纯 pandas + akshare bulk 端点砍到 top 板块内 ~100 只排序
survivors(喂给 L3a 的 LLM 轻量分诊)。**零 token。** 真正的深挖(全量
analyze-ticker)只发生在 L3b 的 ~30 只 finalists。

分层:
  L0 universe  ── 全 A股快照(spot)+ 业绩(yjbb)+ 资金流(fundflow)富化 + 硬门
  L1 四透镜    ── 动量 / 成长 / 价值 / 反转,各自"门→分位打分→top N"
  L2 板块聚合  ── survivors 映射行业,板块按 广度+跨透镜+资金+动量 排名 → top 板块

设计要点:
  * 横截面分位,不用绝对阈值;估值类按行业内分位,动量/资金按全市场分位。
  * 高召回(松门):每透镜 top ~50;去重后 ~150 进 L2。
  * 缺失不插补:缺核心因子 → 该透镜内剔除。
  * **只用 bulk 端点,绝不逐只拉历史**(wall-clock/限流杀手);需历史的因子用
    snapshot 自带 60日/YTD 涨跌幅当代理,真历史留给 L3b。

数据坑(已在 §4.4 记):扣非 bulk 不可得→用头条净利+质量门;MA结构/52周回撤需
历史→用 60日/YTD 代理;akshare 列名跨版本漂→`_col` 防御取列;业绩披露滞后→用
最近可得报告期 + 标注。股息率不在这些 bulk 端点→价值透镜暂不含股息。

用法:
  uv run --no-sync python scripts/screen_market.py 2026-06-20
  uv run --no-sync python scripts/screen_market.py --selftest   # 离线验证打分逻辑
  选项:--cap-floor 30 (市值地板,亿) --include-bj (纳入北交所) --top-per-lens 50
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# ───────────────────────── akshare 防御层 ─────────────────────────


def _ak_call(fn, tries: int = 3, backoff: float = 1.5):
    """Retry akshare bulk calls (东财 push2 端点偶发限流/断连)。"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — akshare 抛各种网络/解析异常
            last = e
            if i < tries - 1:
                time.sleep(backoff * (i + 1))
    raise last


def _col(df: pd.DataFrame, *cands: str, required: bool = False, default=None):
    """按候选顺序解析列名,吸收 akshare 版本漂移(精确→子串包含)。"""
    for c in cands:
        if c in df.columns:
            return c
    for c in cands:
        for col in df.columns:
            if c in col:
                return col
    if required:
        raise KeyError(f"none of {cands} present in {list(df.columns)}")
    return default


# ───────────────────────── 归一化 helpers ─────────────────────────


def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _winsor(s: pd.Series, lo: float = 0.01, hi: float = 0.99) -> pd.Series:
    s = _num(s)
    return s.clip(s.quantile(lo), s.quantile(hi))


def _pct(s: pd.Series, ascending: bool = True) -> pd.Series:
    """横截面百分位 [0,1];NaN 保持 NaN。ascending=True → 值越大分位越高。"""
    return _winsor(s).rank(pct=True, ascending=ascending)


def _pct_within(df: pd.DataFrame, col: str, group: str, ascending: bool = True) -> pd.Series:
    """行业内百分位(估值类用)。组内 winsorize 略过,直接 rank。"""
    return df.groupby(group)[col].transform(lambda x: _num(x).rank(pct=True, ascending=ascending))


def _wsum(parts: dict[str, tuple[pd.Series, float]]) -> pd.Series:
    """加权和;权重按"有值的子因子"重新归一(某因子全 NaN 不拖累)。→ 0–100。"""
    total = pd.Series(0.0, index=next(iter(parts.values()))[0].index)
    wsum = pd.Series(0.0, index=total.index)
    for _name, (series, w) in parts.items():
        s = series.fillna(0.0)
        present = series.notna().astype(float)
        total += s * w
        wsum += present * w
    return (total / wsum.replace(0, np.nan) * 100).round(1)


# ───────────────────────── 报告期 helpers ─────────────────────────


def latest_reported_quarter(analysis_date: str) -> str:
    """给定分析日,返回最近"已过披露截止"的报告期 YYYYMMDD。

    A股截止:Q1(0331)→4/30、H1(0630)→8/31、Q3(0930)→10/31、年报(1231)→次年4/30。
    4/30 前年报/Q1 均未稳,保守用上一年 Q3。
    """
    y, m, d = (int(x) for x in analysis_date.split("-"))
    cur = date(y, m, d)
    deadlines = [(date(y, 4, 30), f"{y}0331"), (date(y, 8, 31), f"{y}0630"), (date(y, 10, 31), f"{y}0930")]
    passed = [(dl, q) for dl, q in deadlines if dl <= cur]
    return max(passed)[1] if passed else f"{y - 1}0930"


def prev_quarter(q: str) -> str:
    """上一个报告期(用于成长加速度的二阶比较)。"""
    y, md = int(q[:4]), q[4:]
    order = ["0331", "0630", "0930", "1231"]
    i = order.index(md)
    return f"{y - 1}1231" if i == 0 else f"{y}{order[i - 1]}"


# ───────────────────────── L0 universe ─────────────────────────

# canonical 列(L1 透镜只认这些名字,fetch 层负责从 akshare 列映射过来):
#   code name industry close mktcap_yi amount_yi
#   pct_1d pct_60d pct_ytd vol_ratio turnover pe pb
#   rev np_ rev_yoy np_yoy np_qoq roe gross_margin cfo_ps
#   np_yoy_prev rev_yoy_prev main_inflow_yi is_st


def fetch_universe(analysis_date: str, cap_floor_yi: float = 30.0, include_bj: bool = False) -> pd.DataFrame:
    """L0:拉 spot + yjbb(当期&上期)+ fundflow,映射成 canonical 列,过硬门。

    需要网络(akshare)。资金流/部分端点失败时优雅降级(该列置 NaN,打分自动重归一)。
    """
    import akshare as ak  # 延迟导入:--selftest 不需要 akshare/网络

    spot = _ak_call(ak.stock_zh_a_spot_em)
    c_code = _col(spot, "代码", required=True)
    df = pd.DataFrame(
        {
            "code": spot[c_code].astype(str).str.zfill(6),
            "name": spot[_col(spot, "名称", required=True)].astype(str),
            "close": _num(spot[_col(spot, "最新价")]),
            "pct_1d": _num(spot[_col(spot, "涨跌幅")]),
            "pct_60d": _num(spot[_col(spot, "60日涨跌幅")]),
            "pct_ytd": _num(spot[_col(spot, "年初至今涨跌幅")]),
            "vol_ratio": _num(spot[_col(spot, "量比")]),
            "turnover": _num(spot[_col(spot, "换手率")]),
            "pe": _num(spot[_col(spot, "市盈率-动态", "市盈率")]),
            "pb": _num(spot[_col(spot, "市净率")]),
            "mktcap_yi": _num(spot[_col(spot, "总市值")]) / 1e8,
            "amount_yi": _num(spot[_col(spot, "成交额")]) / 1e8,
        }
    )

    # 业绩(当期):成长/价值/质量/行业
    q = latest_reported_quarter(analysis_date)
    yj = _ak_call(lambda: ak.stock_yjbb_em(date=q))
    yj_code = _col(yj, "股票代码", required=True)
    fin = pd.DataFrame(
        {
            "code": yj[yj_code].astype(str).str.zfill(6),
            "rev": _num(yj[_col(yj, "营业总收入-营业总收入", "营业总收入")]),
            "rev_yoy": _num(yj[_col(yj, "营业总收入-同比增长")]),
            "np_": _num(yj[_col(yj, "净利润-净利润", "净利润")]),
            "np_yoy": _num(yj[_col(yj, "净利润-同比增长")]),
            "np_qoq": _num(yj[_col(yj, "净利润-季度环比增长")]),
            "roe": _num(yj[_col(yj, "净资产收益率")]),
            "gross_margin": _num(yj[_col(yj, "销售毛利率")]),
            "cfo_ps": _num(yj[_col(yj, "每股经营现金流量")]),
            "industry": yj[_col(yj, "所处行业")].astype("object") if _col(yj, "所处行业") else "未分类",
        }
    )
    df = df.merge(fin, on="code", how="left")

    # 业绩(上期):仅取 YoY 算加速度
    try:
        yjp = _ak_call(lambda: ak.stock_yjbb_em(date=prev_quarter(q)))
        prev = pd.DataFrame(
            {
                "code": yjp[_col(yjp, "股票代码", required=True)].astype(str).str.zfill(6),
                "np_yoy_prev": _num(yjp[_col(yjp, "净利润-同比增长")]),
                "rev_yoy_prev": _num(yjp[_col(yjp, "营业总收入-同比增长")]),
            }
        )
        df = df.merge(prev, on="code", how="left")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 上期业绩取数失败({e!r})→ 成长加速度降级", file=sys.stderr)
        df["np_yoy_prev"] = np.nan
        df["rev_yoy_prev"] = np.nan

    # 主力资金流(今日):动量/反转的资金确认
    try:
        ff = _ak_call(lambda: ak.stock_individual_fund_flow_rank(indicator="今日"))
        ff_code = _col(ff, "代码", required=True)
        flow = pd.DataFrame(
            {
                "code": ff[ff_code].astype(str).str.zfill(6),
                "main_inflow_yi": _num(ff[_col(ff, "今日主力净流入-净额", "主力净流入-净额", "主力净流入")]) / 1e8,
            }
        )
        df = df.merge(flow, on="code", how="left")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 主力资金流取数失败({e!r})→ 资金因子降级(置 NaN)", file=sys.stderr)
        df["main_inflow_yi"] = np.nan

    df["industry"] = df["industry"].fillna("未分类").replace("", "未分类")
    return _apply_universe_gates(df, cap_floor_yi=cap_floor_yi, include_bj=include_bj)


_GATE_INFO: dict = {}   # 跨函数传 L0 原始数(全A,硬门前);pandas .attrs 不可靠故用模块级


def _apply_universe_gates(df: pd.DataFrame, cap_floor_yi: float = 30.0, include_bj: bool = True) -> pd.DataFrame:
    """硬门:剔 ST/退市/停牌/次新代理 + 市值地板 + 北交所开关。"""
    df = df.copy()
    name = df["name"].fillna("")
    df["is_st"] = name.str.contains("ST", case=False) | name.str.contains("退")
    keep = ~df["is_st"]
    keep &= df["mktcap_yi"] >= cap_floor_yi
    # 停牌代理:无成交额/无最新价
    keep &= df["amount_yi"].fillna(0) > 0
    keep &= df["close"].notna()
    if not include_bj:
        # 北交所:8/4 开头(及 920 新段)
        keep &= ~df["code"].str.match(r"^(8|4|920)")
    out = df[keep].reset_index(drop=True)
    _GATE_INFO["n_raw"] = len(df)   # 全A 原始数(供漏斗显示 全A → 硬门)
    print(f"[L0] universe: {len(df)} → 过门 {len(out)} "
          f"(cap≥{cap_floor_yi}亿, 北交所={'纳入' if include_bj else '排除'})", file=sys.stderr)
    return out


# ───────────────────────── L1 四透镜 ─────────────────────────

LENS_NAMES = ["momentum", "growth", "value", "reversal"]


def lens_momentum(df: pd.DataFrame) -> pd.DataFrame:
    """趋势动量:RS40 + 主力净流入30 + 趋势结构30;过热 −15。松门:60日或YTD涨幅>0。

    量能项(原 15)经 factor_lab 实证剔除:vol_ratio 对 T+1 收益显著**负**相关(rank IC t=-2.31,
    样本前后半皆负)=放量滞涨/派发,turnover 近噪声;剔后复合 T+1 ICIR +32% 且 T+5/10 不降。
    主力净流入对 T+1 近中性、对 T+5/10 最强 → 作为 swing 信号保留高权重。(见 factor_lab.py / spec §实证)
    """
    g = df.copy()
    gate = (g["pct_60d"].fillna(-1) > 0) | (g["pct_ytd"].fillna(-1) > 0)
    rs = 0.6 * _pct(g["pct_60d"]) + 0.4 * _pct(g["pct_ytd"])
    if {"ma_bull", "above_ma60"} <= set(g.columns):  # 真趋势结构(tushare 多头排列 + 站上 MA60)
        trend = 0.5 * _num(g["above_ma60"]).fillna(0.0) + 0.5 * _num(g["ma_bull"]).fillna(0.0)
    else:
        trend = 0.5 * (g["pct_60d"].fillna(0) > 0).astype(float) + 0.5 * _pct(g["pct_ytd"])
    score = _wsum({"rs": (rs, 40), "inflow": (_pct(g["main_inflow_yi"]), 30), "trend": (trend, 30)})
    overheat = _pct(g["pct_60d"]) > 0.95          # 60日涨幅顶 5% = 抛物线顶
    if "rsi6" in g.columns:                        # + RSI6 过热(真技术面确认)
        overheat = overheat | (_num(g["rsi6"]) > 85)
    score = (score - overheat.astype(float) * 15).clip(lower=0)
    g["momentum_score"] = score
    g["momentum_gate"] = gate
    g["momentum_signals"] = np.where(overheat, "强势·过热", "强势")
    return g


def lens_growth(df: pd.DataFrame) -> pd.DataFrame:
    """成长加速:加速度30 + 净利YoY25 + 营收YoY20 + ROE15 + 质量10。门:成长在+CFO>0+营收≥3亿/季。"""
    g = df.copy()
    accel = g["np_yoy"] - g["np_yoy_prev"]           # YoY 的二阶导
    quality = 0.5 * _pct(g["cfo_ps"]) + 0.5 * _pct(g["gross_margin"])
    score = _wsum({"accel": (_pct(accel), 30), "np_yoy": (_pct(g["np_yoy"]), 25),
                   "rev_yoy": (_pct(g["rev_yoy"]), 20), "roe": (_pct(g["roe"]), 15),
                   "quality": (quality, 10)})
    # 估值惩罚:成长已被定价(PE 全市场顶 10%)
    pe_pos = g["pe"].where(g["pe"] > 0)
    score = (score - (_pct(pe_pos) > 0.90).astype(float) * 10).clip(lower=0)
    gate = ((g["np_yoy"].fillna(-1) > 0) | (g["rev_yoy"].fillna(-1) > 15)) \
        & (g["cfo_ps"].fillna(-1) > 0) & (g["rev"].fillna(0) >= 3e8)
    g["growth_score"] = score
    g["growth_gate"] = gate
    g["growth_signals"] = np.where(accel.fillna(0) > 0, "加速", "高增")
    return g


def lens_value(df: pd.DataFrame) -> pd.DataFrame:
    """价值低估(行业内):PE35 + ROE30 + PB25 + 利润率10。门:PE>0、ROE>0、营收未崩塌。

    注:股息率不在 bulk 端点 → 暂不含;原 15 权重并入 PE/ROE。
    """
    g = df.copy()
    g["_pe_pos"] = g["pe"].where(g["pe"] > 0)
    pe_lo = _pct_within(g, "_pe_pos", "industry", ascending=False)   # 低 PE = 高分
    pb_lo = _pct_within(g, "pb", "industry", ascending=False)
    parts = {"pe": (pe_lo, 35), "roe": (_pct(g["roe"]), 30),
             "pb": (pb_lo, 25), "margin": (_pct(g["gross_margin"]), 10)}
    if "dv_ratio" in g.columns:   # 股息率可得(tushare)→ 恢复股息因子(原 push2 端点缺)
        parts = {"pe": (pe_lo, 30), "roe": (_pct(g["roe"]), 25), "pb": (pb_lo, 20),
                 "margin": (_pct(g["gross_margin"]), 10), "div": (_pct(g["dv_ratio"]), 15)}
    score = _wsum(parts)
    gate = (g["pe"].fillna(-1) > 0) & (~g["is_st"]) \
        & (g["rev_yoy"].fillna(0) > -15) & (g["roe"].fillna(-1) > 0)
    g["value_score"] = score
    g["value_gate"] = gate
    g["value_signals"] = "低估·实赚"
    return g.drop(columns=["_pe_pos"])


def lens_reversal(df: pd.DataFrame) -> pd.DataFrame:
    """困境反转:边际改善40 + 超跌30 + 资金确认30。门:(改善∨资金)亮。

    超跌需历史 → bulk 用 60日/YTD 跌幅代理,真结构留给 L3b。
    原「底部结构」base 用 winner_rate(低获利盘=套牢=超跌底)——factor_lab 实测该用法 **regime 翻转**
    (弱市低获利盘反弹、强市续跌)且全样本净**负**相关,不宜作静态正向因子 → 剔除,权重并入
    改善/超跌/资金;筹码数据(winner_rate/cost)保留在 survivors 输出供 L3b 定性核。(见 spec §实证)
    """
    g = df.copy()
    accel = g["np_yoy"] - g["np_yoy_prev"]
    improving = (g["np_qoq"].fillna(-1) > 0) | (accel.fillna(-1) > 0)      # 拐点
    inflow_on = g["main_inflow_yi"].fillna(-1) > 0
    oversold = 0.5 * _pct(g["pct_60d"], ascending=False) + 0.5 * _pct(g["pct_ytd"], ascending=False)
    improve_sc = 0.6 * improving.astype(float) + 0.4 * _pct(accel)
    fund_sc = 0.6 * inflow_on.astype(float) + 0.4 * _pct(g["main_inflow_yi"])
    score = _wsum({"improve": (improve_sc, 40), "oversold": (oversold, 30), "fund": (fund_sc, 30)})
    gate = (improving | inflow_on) & (~df["name"].fillna("").str.contains("退"))
    g["reversal_score"] = score
    g["reversal_gate"] = gate
    g["reversal_signals"] = np.where(improving, "超跌·拐点", "超跌·资金确认")
    return g


def run_lenses(uni: pd.DataFrame, top_per_lens: int = 50) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """跑四透镜,返回(去重 survivors, 各透镜 topN 榜)。survivors 带 lens 命中标签 + 复合分。"""
    fns = {"momentum": lens_momentum, "growth": lens_growth,
           "value": lens_value, "reversal": lens_reversal}
    tops: dict[str, pd.DataFrame] = {}
    hit_cols = []
    base = uni.copy()
    for lens, fn in fns.items():
        scored = fn(uni)
        passed = scored[scored[f"{lens}_gate"]].nlargest(top_per_lens, f"{lens}_score")
        tops[lens] = passed
        # 合并该透镜分/命中回 base
        base = base.merge(passed[["code", f"{lens}_score", f"{lens}_signals"]], on="code", how="left")
        base[f"hit_{lens}"] = base["code"].isin(passed["code"])
        hit_cols.append(f"hit_{lens}")
    base["n_lens"] = base[hit_cols].sum(axis=1)
    survivors = base[base["n_lens"] > 0].copy()
    # 复合确信度 = 命中透镜数(主) + 各透镜分均值(次)
    score_cols = [f"{lens}_score" for lens in fns]
    survivors["lens_mean"] = survivors[score_cols].mean(axis=1, skipna=True).round(1)
    survivors["conviction"] = (survivors["n_lens"] * 100 + survivors["lens_mean"].fillna(0)).round(1)
    survivors = survivors.sort_values("conviction", ascending=False).reset_index(drop=True)
    print(f"[L1] survivors(命中≥1透镜,去重): {len(survivors)} "
          f"(各透镜 top {top_per_lens})", file=sys.stderr)
    return survivors, tops


# ───────────────────────── L2 板块聚合 ─────────────────────────


def aggregate_sectors(survivors: pd.DataFrame, uni: pd.DataFrame, top_sectors: int = 5) -> pd.DataFrame:
    """板块按 广度 + 跨透镜 + 资金 + 动量 排名。广度+跨透镜权重最高(确信度信号)。"""
    hit_cols = [f"hit_{lens}" for lens in LENS_NAMES]
    sector_size = uni.groupby("industry")["code"].count().rename("sector_size")
    rows = []
    for ind, grp in survivors.groupby("industry"):
        lenses_present = sum(int(grp[h].any()) for h in hit_cols)
        rows.append({
            "industry": ind,
            "n_survivors": len(grp),
            "n_lenses": lenses_present,                       # 跨透镜:1–4
            "median_inflow_yi": grp["main_inflow_yi"].median(skipna=True),
            "median_pct_60d": grp["pct_60d"].median(skipna=True),
            "median_conviction": grp["conviction"].median(),
        })
    sec = pd.DataFrame(rows).merge(sector_size, on="industry", how="left")
    sec["breadth"] = (sec["n_survivors"] / sec["sector_size"]).round(3)
    sec["sector_score"] = _wsum({
        "breadth": (_pct(sec["breadth"]), 30),
        "cross_lens": (sec["n_lenses"] / 4.0, 30),
        "inflow": (_pct(sec["median_inflow_yi"]), 20),
        "momentum": (_pct(sec["median_pct_60d"]), 20),
    })
    sec = sec[sec["industry"] != "未分类"].sort_values("sector_score", ascending=False).reset_index(drop=True)
    sec["is_top"] = sec.index < top_sectors
    print(f"[L2] 板块: {len(sec)} 个有 survivors,取 top {top_sectors}", file=sys.stderr)
    return sec


# ───────────────────────── L1 召回:轻门 + 行业条件化复合分 ─────────────────────────

# 9 因子组(自然朝向:高=常规看多;真方向由 weights.json 的 IC 符号决定)。
# volprice = 多日量价资金流(CMF+OBV;序列指标,IC 实证 decile +40bps/t≈2,远胜已剔的单日 vol_ratio)。
_GROUPS = ("momentum", "fund_main", "fund_retail", "chip", "north", "tech", "growth", "value", "volprice")

# weights.json 缺失时的先验(仅 __global__;慢因子 growth/value 给小权重——T+1 近噪声但仍纳入)。
_PRIOR_WEIGHTS = {"meta": {"source": "prior(无 weights.json)"}, "weights": {"__global__": {
    "momentum": 0.10, "fund_main": 0.06, "fund_retail": -0.02, "chip": 0.02,
    "north": 0.03, "tech": -0.03, "growth": 0.03, "value": 0.03, "volprice": 0.04}}}


def _load_weights(path: str = "context/factor_lab/weights.json") -> dict:
    """读 factor_lab 校准产物;缺失则回落内置先验(并提示)。"""
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    print(f"[warn] {path} 不存在 → 用内置先验权重(建议先 factor_lab calibrate)", file=sys.stderr)
    return _PRIOR_WEIGHTS


def _blend(*parts) -> pd.Series:
    """加权平均,跳过 NaN 子项(权重重归一)。parts = [(series, w), ...]。"""
    num = den = None
    for s, w in parts:
        sv = s.fillna(0.0) * w
        pv = s.notna().astype(float) * w
        num = sv if num is None else num + sv
        den = pv if den is None else den + pv
    return num / den.replace(0, np.nan)


def _factor_groups(df: pd.DataFrame) -> dict[str, pd.Series]:
    """9 组子分(各 0–1 横截面分位,自然朝向)。缺列的组返回全 NaN。calibrate 与 composite 共用。"""
    nan = pd.Series(np.nan, index=df.index)

    def p(col, asc=True):
        return _pct(df[col], ascending=asc) if col in df.columns else nan

    has = set(df.columns)
    return {
        "momentum": _blend((p("pct_60d"), 0.6), (p("pct_ytd"), 0.4)),
        "fund_main": p("main_net_ratio") if "main_net_ratio" in has else p("main_inflow_yi"),
        "fund_retail": p("retail_net_yi"),
        "chip": _blend((p("chip_concentration"), 0.5), (p("price_to_cost"), 0.5)),
        "north": p("hk_ratio"),
        "tech": _blend((p("rsi6"), 0.5), (p("rsi12"), 0.5)),
        "growth": _blend((p("np_yoy"), 0.5), (p("rev_yoy"), 0.3), (p("roe"), 0.2)),
        "value": _pct_within(df, "pe", "industry", ascending=False) if {"pe", "industry"} <= has else nan,
        # 多日量价资金流(CMF 买/卖压 + OBV 资金方向;缺列→NaN,组重归一,recall 不破)
        "volprice": _blend((p("cmf_20"), 0.5), (p("obv_mom_20"), 0.5)),
    }


def composite_score(df: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """行业条件化复合分:Σ (组分位−0.5) × 该行业组权重(signed IC);归一映射 0–100。

    权重符号由校准 IC 决定(正=该组高分看多、负=看空);排序用 composite 即可。
    """
    groups = _factor_groups(df)
    wmap = weights.get("weights", {})
    glob = wmap.get("__global__", {})
    ind = df["industry"] if "industry" in df.columns else pd.Series("", index=df.index)
    out = df.copy()
    comp = pd.Series(0.0, index=df.index)
    wabs = pd.Series(0.0, index=df.index)
    for name, s in groups.items():
        out[f"score_{name}"] = (s * 100).round(1)
        w = ind.map(lambda x, n=name: float(wmap.get(x, {}).get(n, glob.get(n, 0.0))))
        comp += (s - 0.5).fillna(0.0) * w
        wabs += s.notna().astype(float) * w.abs()
    raw = comp / wabs.replace(0, np.nan)
    comp100 = 50 + 50 * raw.clip(-1, 1)
    # 过热抑制(风险叠加,**不改 IC 权重**):高动量 **且** 超买/获利盘满 = "见顶 leader" → 压低,
    # 避免 T+1 动量校准把这类 froth 堆到召回顶端(swing 视角多为见顶,L4 实测被打回)。
    if "pct_60d" in df.columns:
        high_mom = _pct(df["pct_60d"]) > 0.90
        exhausted = pd.Series(False, index=df.index)
        if "rsi6" in df.columns:
            exhausted = exhausted | (_num(df["rsi6"]) > 80)
        if "winner_rate" in df.columns:
            exhausted = exhausted | (_num(df["winner_rate"]) > 85)
        comp100 = comp100 - (high_mom & exhausted).fillna(False).astype(float) * 8
    # 吸筹加成(froth 抑制的多头镜像,同为风险叠加、**不改 IC 权重**):低位(获利盘低/破成本)+ 放量
    # + 主力未撤 = 底部疑似吸筹(量价『顶部=派发、底部=吸筹』)→ 小幅加分**保召回**(进 top recall_n),
    # 交 L2/L3/L4 做基本面『三维验证』。研究:底部放量 >70% 无基本面会败,故 +5 < froth −8——只保召回、不越级多报。
    if "vol_ratio" in df.columns:
        low_pos = pd.Series(False, index=df.index)
        if "winner_rate" in df.columns:
            low_pos = low_pos | (_num(df["winner_rate"]) < 40)
        if "price_to_cost" in df.columns:
            low_pos = low_pos | (_num(df["price_to_cost"]) < 1.0)
        not_high = (_num(df["pct_60d"]) < 20) if "pct_60d" in df.columns else pd.Series(True, index=df.index)
        main_ok = (_num(df["main_net_ratio"]) >= 0) if "main_net_ratio" in df.columns else pd.Series(True, index=df.index)
        accum = (_num(df["vol_ratio"]) >= 1.5) & low_pos & not_high & main_ok
        comp100 = comp100 + accum.fillna(False).astype(float) * 5
    out["composite"] = comp100.clip(lower=0, upper=100).round(1)   # upper 夹 100:吸筹加成不溢出
    return out


def _recall_gate_a(df: pd.DataFrame, min_amount_yi: float = 0.0) -> pd.Series:
    """L1 召回轻门:只去真正不可交易/无核心数据的尾部(召回优先,尽量不误杀)。"""
    keep = df["amount_yi"].fillna(0) > min_amount_yi       # 有流动性/非停牌
    keep &= df["close"].notna()                            # 有价
    keep &= df["pct_60d"].notna() | df["pct_ytd"].notna()  # 有动量价(打分核心)
    return keep


def aggregate_sectors_overview(recall: pd.DataFrame, uni: pd.DataFrame) -> pd.DataFrame:
    """板块概览(L2 不再聚合截断;仅供 L5 描述):各行业召回数 / 中位复合分 / 中位动量 / 中位主力净占比。"""
    if "industry" not in recall.columns or not len(recall):
        return pd.DataFrame(columns=["industry", "n_recall", "median_composite", "is_top"])
    g = recall.groupby("industry")
    sec = pd.DataFrame({"industry": g.size().index, "n_recall": g.size().to_numpy(),
                        "median_composite": g["composite"].median().to_numpy()})
    for col, name in [("pct_60d", "median_pct_60d"), ("main_net_ratio", "median_main_net_ratio")]:
        if col in recall.columns:
            sec = sec.merge(g[col].median().rename(name).reset_index(), on="industry", how="left")
    sec = sec.sort_values("n_recall", ascending=False).reset_index(drop=True)
    sec["is_top"] = sec.index < 8
    return sec


def _harvest_vol_series(codes, analysis_date: str, lookback: int = 20) -> pd.DataFrame:
    """拉近 ~lookback 交易日 daily(high/low/close/amount)→ vol_series 算多日量价因子 per code。

    供 L1 召回的 volprice 组(快照层本来无序列)。tushare bulk by date(~lookback 次)→ pivot → 序列指标。
    无权限/失败 → 返回空帧(volprice 列缺失 → 组 NaN 重归一,recall 不破)。
    """
    try:
        from datetime import datetime, timedelta

        import vol_series
        from tushare_source import _code6, _pro, _trade_days, _ts_call, resolve_momentum_dates
        pro = _pro()
        last = resolve_momentum_dates(pro, analysis_date)[0]
        start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=lookback * 2 + 15)).strftime("%Y%m%d")
        days = _trade_days(pro, start, last)[-lookback:]
        if len(days) < 10:
            return pd.DataFrame(columns=["code"])
        want = {str(c).zfill(6) for c in codes}
        recs = []
        for d in days:
            df = _ts_call(lambda d=d: pro.daily(trade_date=d, fields="ts_code,high,low,close,amount"))
            if df is None or not len(df):
                continue
            df = df.assign(code=_code6(df["ts_code"]), date=d)
            recs.append(df[df["code"].isin(want)][["code", "date", "high", "low", "close", "amount"]])
        if not recs:
            return pd.DataFrame(columns=["code"])
        long = pd.concat(recs, ignore_index=True)
        piv = {f: long.pivot_table(index="code", columns="date", values=f)
               for f in ("high", "low", "close", "amount")}
        win = sorted(piv["close"].columns)
        H, L, C, A = (piv[f][win] for f in ("high", "low", "close", "amount"))
        out = pd.DataFrame({"code": list(C.index)})
        out["cmf_20"] = vol_series.cmf(H, L, C, A, win).to_numpy()
        out["obv_mom_20"] = vol_series.obv_momentum(C, A, win).to_numpy()
        out["price_vs_vwap_20"] = vol_series.price_vs_vwap(H, L, C, A, win).to_numpy()
        out["breakout_vol_20"] = vol_series.breakout_on_volume(C, A, win).to_numpy()
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 多日量价序列取数失败 → volprice 组置 NaN: {e}", file=sys.stderr)
        return pd.DataFrame(columns=["code"])


# ───────────────────────── 编排 + 输出 ─────────────────────────


def run(analysis_date: str, cap_floor_yi: float = 30.0, include_bj: bool = True,
        recall_n: int = 1000, l2_n: int = 200, outdir: Path | None = None,
        source: str = "tushare") -> dict:
    """L0 选集 + L1 召回 + L2 粗排(GBDT 学习重排 → top l2_n)。全确定性,零 LLM。"""
    if source == "tushare":
        from tushare_source import _RAW_COUNT, fetch_universe_tushare  # 默认源(东财 push2 常被封)
        uni = fetch_universe_tushare(analysis_date, cap_floor_yi=cap_floor_yi, include_bj=include_bj)
        n_raw = _RAW_COUNT.get("n", len(uni))
    else:
        uni = fetch_universe(analysis_date, cap_floor_yi=cap_floor_yi, include_bj=include_bj)
        n_raw = _GATE_INFO.get("n_raw", len(uni))   # em 路径同模块,可靠
    n_l0 = len(uni)

    # L1 召回:Step A 轻门 → Step B 复合分 → top recall_n
    uni = uni[_recall_gate_a(uni)].reset_index(drop=True)
    uni["code"] = uni["code"].astype(str).str.zfill(6)
    vps = _harvest_vol_series(uni["code"], analysis_date)          # 多日量价序列(CMF/OBV/...)→ volprice 组
    if len(vps):
        uni = uni.merge(vps, on="code", how="left")
    weights = _load_weights()
    scored = composite_score(uni, weights)
    recall = scored.sort_values("composite", ascending=False).head(recall_n).reset_index(drop=True)
    print(f"[L1 召回] L0 {n_l0} → 轻门 {len(uni)} → 复合分 top {len(recall)}", file=sys.stderr)
    sectors = aggregate_sectors_overview(recall, uni)

    outdir = outdir or Path("context/scan") / analysis_date
    outdir.mkdir(parents=True, exist_ok=True)
    keep = (["code", "name", "industry", "composite"] + [f"score_{g}" for g in _GROUPS]
            + ["mktcap_yi", "close", "amount_yi", "vol_ratio", "turnover", "cmf_20", "obv_mom_20",
               "pct_60d", "pct_ytd",
               "main_inflow_yi", "main_net_ratio",
               "retail_net_yi", "winner_rate", "chip_concentration", "price_to_cost", "hk_ratio",
               "rsi6", "rsi12", "pe", "pb", "dv_ratio", "np_yoy", "rev_yoy", "roe",
               "ma_bull", "above_ma60"])
    recall[[c for c in keep if c in recall.columns]].to_csv(outdir / "L1_recall_top1000.csv", index=False)
    # 全量打分(所有过门股,按 composite 降序 + recalled 标记)→ trace/ 留全阶段数据,不截断
    full = scored.sort_values("composite", ascending=False).reset_index(drop=True)
    full.insert(0, "rank", range(1, len(full) + 1))
    full.insert(1, "recalled", full["rank"] <= recall_n)
    full[["rank", "recalled"] + [c for c in keep if c in full.columns]].to_csv(
        outdir / "L1_scored_full.csv", index=False)

    # ── L2 粗排:GBDT 学习重排 recall(top recall_n)→ top l2_n(确定性,替旧 L2-AI keep/cut)──
    # 模型缺失 / oos 未胜线性 → predict_scores 返回 None → 回落 composite top(自保,绝不比线性差)。
    import factor_lab
    gscore = factor_lab.predict_scores(recall)
    if gscore is not None:
        l2 = recall.assign(gbdt_score=gscore.to_numpy()).sort_values(
            "gbdt_score", ascending=False).head(l2_n).reset_index(drop=True)
        l2_engine = "gbdt"
    else:
        l2 = recall.head(l2_n).reset_index(drop=True)
        l2_engine = "composite-linear(回落)"
    l2.insert(0, "l2_rank", range(1, len(l2) + 1))
    l2_cols = ["l2_rank", "gbdt_score", *keep]
    l2[[c for c in l2_cols if c in l2.columns]].to_csv(outdir / "L2_gbdt_top200.csv", index=False)
    print(f"[L2 粗排] recall {len(recall)} → {l2_engine} top {len(l2)}", file=sys.stderr)

    sectors.to_csv(outdir / "sectors.csv", index=False)
    (outdir / "meta.json").write_text(json.dumps({
        "analysis_date": analysis_date, "universe_raw": n_raw, "universe": n_l0, "after_gate_a": len(uni),
        "recall_n": len(recall), "l2_n": len(l2), "l2_engine": l2_engine,
        "cap_floor_yi": cap_floor_yi, "include_bj": include_bj, "source": source,
        "weights_source": weights.get("meta", {}).get("source", "weights.json"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] L1 召回 → {outdir}/L1_recall_top1000.csv ({len(recall)})", file=sys.stderr)
    return {"universe": n_l0, "after_gate_a": len(uni), "recall_n": len(recall),
            "l2_n": len(l2), "l2_engine": l2_engine, "sectors": len(sectors), "outdir": str(outdir)}


# ───────────────────────── 离线自测(无网络) ─────────────────────────


def _selftest() -> int:
    """用合成 canonical DataFrame 验证打分逻辑(分位/门/加权/板块聚合)——不碰 akshare/网络。"""
    rng = np.random.default_rng(42)
    n = 200
    inds = rng.choice(["半导体", "光模块", "白酒", "煤炭", "医药", "未分类"], n)
    df = pd.DataFrame({
        "code": [f"{600000 + i:06d}" for i in range(n)],
        "name": [f"股票{i}" for i in range(n)],
        "industry": inds,
        "close": rng.uniform(5, 300, n),
        "mktcap_yi": rng.uniform(20, 4000, n),
        "amount_yi": rng.uniform(0.5, 200, n),
        "pct_1d": rng.uniform(-10, 10, n),
        "pct_60d": rng.uniform(-50, 300, n),
        "pct_ytd": rng.uniform(-60, 400, n),
        "vol_ratio": rng.uniform(0.3, 5, n),
        "turnover": rng.uniform(0.1, 30, n),
        "pe": rng.uniform(-50, 200, n),
        "pb": rng.uniform(0.5, 30, n),
        "rev": rng.uniform(1e8, 5e10, n),
        "np_": rng.uniform(-1e9, 5e9, n),
        "rev_yoy": rng.uniform(-40, 120, n),
        "np_yoy": rng.uniform(-100, 300, n),
        "np_qoq": rng.uniform(-50, 80, n),
        "roe": rng.uniform(-10, 35, n),
        "gross_margin": rng.uniform(5, 70, n),
        "cfo_ps": rng.uniform(-1, 3, n),
        "np_yoy_prev": rng.uniform(-100, 200, n),
        "rev_yoy_prev": rng.uniform(-40, 100, n),
        "main_inflow_yi": rng.uniform(-5, 8, n),
    })
    # tushare 增强列(覆盖 value/momentum/reversal 的增强分支)
    df["dv_ratio"] = rng.uniform(0, 6, n)
    df["ma_bull"] = rng.integers(0, 2, n).astype(float)
    df["above_ma60"] = rng.integers(0, 2, n).astype(float)
    df["rsi6"] = rng.uniform(10, 95, n)
    df["winner_rate"] = rng.uniform(0, 100, n)
    df["cost_50pct"] = rng.uniform(5, 300, n)
    # v2 富因子(资金结构/筹码集中度/北向/RSI12)
    df["rsi12"] = rng.uniform(10, 95, n)
    df["main_net_ratio"] = rng.uniform(-0.1, 0.1, n)
    df["retail_net_yi"] = rng.uniform(-2, 2, n)
    df["chip_concentration"] = rng.uniform(0.1, 2.0, n)
    df["price_to_cost"] = rng.uniform(0.7, 1.5, n)
    df["hk_ratio"] = rng.uniform(0, 30, n)
    df["is_st"] = False

    fails = []
    # 1) 各透镜产出分 + 门,分在 [0,100],门是 bool
    for lens, fn in [("momentum", lens_momentum), ("growth", lens_growth),
                     ("value", lens_value), ("reversal", lens_reversal)]:
        g = fn(df)
        sc, gate = g[f"{lens}_score"], g[f"{lens}_gate"]
        if not ((sc.dropna() >= 0).all() and (sc.dropna() <= 100).all()):
            fails.append(f"{lens}: score 越界 [{sc.min()},{sc.max()}]")
        if gate.dtype != bool:
            fails.append(f"{lens}: gate 非 bool")
        if gate.sum() == 0:
            fails.append(f"{lens}: 松门竟无人通过(可疑)")

    # 2) 编排:survivors 有确信度排序 + 板块聚合
    survivors = run_lenses(df, top_per_lens=30)[0]
    if survivors.empty:
        fails.append("survivors 为空")
    if not survivors["conviction"].is_monotonic_decreasing:
        fails.append("survivors 未按 conviction 降序")
    if not (survivors["n_lens"].between(1, 4)).all():
        fails.append("n_lens 越界")
    sectors = aggregate_sectors(survivors, df, top_sectors=3)
    if "未分类" in set(sectors["industry"]):
        fails.append("板块榜未剔除 未分类")
    if not sectors["sector_score"].is_monotonic_decreasing:
        fails.append("板块未按 sector_score 降序")

    # 4) v2 召回:轻门 + 行业条件化复合分
    ga = _recall_gate_a(df)
    if ga.dtype != bool or ga.sum() == 0:
        fails.append("recall gate_a 异常(非 bool 或全剔)")
    comp = composite_score(df, _PRIOR_WEIGHTS)
    cs = comp["composite"]
    if not ((cs.dropna() >= 0).all() and (cs.dropna() <= 100).all()):
        fails.append(f"composite 越界 [{cs.min()},{cs.max()}]")
    for gname in ("momentum", "fund_main", "chip", "tech", "value"):
        if f"score_{gname}" not in comp.columns:
            fails.append(f"缺子分列 score_{gname}")

    # 3) 报告期 helper
    cases = {"2026-06-20": "20260331", "2026-09-15": "20260630",
             "2026-11-01": "20260930", "2026-02-01": "20250930"}
    for d, exp in cases.items():
        got = latest_reported_quarter(d)
        if got != exp:
            fails.append(f"latest_reported_quarter({d})={got} 期望 {exp}")
    if prev_quarter("20260331") != "20251231":
        fails.append("prev_quarter(Q1) 错")

    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print("  -", f)
        return 1
    print(f"SELFTEST ✅  四透镜 + v2召回(轻门/复合分)/编排/板块/报告期 全过 "
          f"(survivors={len(survivors)}, sectors={len(sectors)})")
    return 0


# ───────────────────────── CLI ─────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="scan-market L0 选集 + L1 召回(确定性,零 LLM)")
    ap.add_argument("date", nargs="?", help="分析日 YYYY-MM-DD(缺省=今天)")
    ap.add_argument("--cap-floor", type=float, default=30.0, help="市值地板(亿),默认 30")
    ap.add_argument("--exclude-bj", action="store_true", help="排除北交所(默认纳入)")
    ap.add_argument("--recall-n", type=int, default=1000, help="召回数(复合分 top N),默认 1000")
    ap.add_argument("--l2-n", type=int, default=200, help="L2 粗排数(GBDT 重排 top N),默认 200")
    ap.add_argument("--source", choices=["em", "tushare"], default="tushare",
                    help="universe 取数源:tushare=默认(push2 常被封);em=东财 push2")
    ap.add_argument("--selftest", action="store_true", help="离线验证打分逻辑(无网络)")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    analysis_date = args.date or date.today().isoformat()
    res = run(analysis_date, cap_floor_yi=args.cap_floor, include_bj=not args.exclude_bj,
              recall_n=args.recall_n, l2_n=args.l2_n, source=args.source)
    print(f"\nL0 universe={res['universe']} → 轻门 {res['after_gate_a']} → 召回 top{res['recall_n']} "
          f"→ L2 {res['l2_engine']} top{res['l2_n']} (板块概览 {res['sectors']} 个)"
          f"\n→ {res['outdir']}/L2_gbdt_top200.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""纯打分原语 —— 横截面分位 / 加权 / 9 因子组 / 复合分 / 四透镜 / 报告期。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A(common)。

从 `scripts/screen_market.py` 抽出的 **纯函数**(无网络、无 I/O、无编排):scan 的 L1
打分逻辑与 factor_lab 的 IC 校准 / GBDT 特征 **同口径** 复用同一份。screen_market /
factor_lab 都 `from autoresearch.common.scoring import ...`,以便 `autoresearch.data`
包(handler)可平直复用——package 不能 flat-import scripts/ 模块,故下沉到 common。

留在 screen_market 的是 I/O / 网络 / 编排:`_ak_call / _col / fetch_universe /
_apply_universe_gates / run / _recall_gate_a / aggregate_sectors* / _harvest_vol_series /
run_lenses / main / _selftest`。本模块只放可离线复现的打分数学。

铁律:**与线上同口径**——校准(factor_lab)与打分(scan)、新管道(handler)三处共用同一
份组定义 / 复合分,任何一处改逻辑三处一致变。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

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


def _blend(*parts) -> pd.Series:
    """加权平均,跳过 NaN 子项(权重重归一)。parts = [(series, w), ...]。"""
    num = den = None
    for s, w in parts:
        sv = s.fillna(0.0) * w
        pv = s.notna().astype(float) * w
        num = sv if num is None else num + sv
        den = pv if den is None else den + pv
    return num / den.replace(0, np.nan)


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


# ───────────────────────── L1 四透镜 ─────────────────────────


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


# ───────────────────────── 9 因子组 + 行业条件化复合分 ─────────────────────────

# 9 因子组(自然朝向:高=常规看多;真方向由 weights.json 的 IC 符号决定)。
# volprice = 多日量价资金流(CMF+OBV;序列指标,IC 实证 decile +40bps/t≈2,远胜已剔的单日 vol_ratio)。
_GROUPS = ("momentum", "fund_main", "fund_retail", "chip", "north", "tech", "growth", "value", "volprice")

# weights.json 缺失时的先验(仅 __global__;慢因子 growth/value 给小权重——T+1 近噪声但仍纳入)。
_PRIOR_WEIGHTS = {"meta": {"source": "prior(无 weights.json)"}, "weights": {"__global__": {
    "momentum": 0.10, "fund_main": 0.06, "fund_retail": -0.02, "chip": 0.02,
    "north": 0.03, "tech": -0.03, "growth": 0.03, "value": 0.03, "volprice": 0.04}}}


def _load_weights(path: str = "context/factor_lab/weights.json") -> dict:
    """读 factor_lab 校准产物;缺失则回落内置先验(并提示)。"""
    import sys

    p = Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    print(f"[warn] {path} 不存在 → 用内置先验权重(建议先 factor_lab calibrate)", file=sys.stderr)
    return _PRIOR_WEIGHTS


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

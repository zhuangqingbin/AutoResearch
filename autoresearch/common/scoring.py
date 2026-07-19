#!/usr/bin/env python3
"""纯打分原语 —— 横截面分位 / 加权 / 10 因子组 / 复合分 / 四透镜 / 报告期。

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
    主力净流入对 T+1/T+2 近中性、对 T+5/10 最强;超短主尺(fwd_2_oc)下其权重由 calibrate 重定,
    不再预设 swing 高权重。(见 factor_lab.py / spec §实证)
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


def lens_reversal_confirm(df: pd.DataFrame) -> pd.DataFrame:
    """反转**确认**(四段确认通道,起爆日硬门):前置低位30 + 衰竭企稳30 + 确认起爆40。

    与旧 `lens_reversal`(:167,困境反转:边际改善∨资金即放行,门内无企稳段、无量价确认,可召回
    仍在下跌途中的票)的本质区别、也是本通道价值所在——Plan A1-T2 用 107 个成型日真实回测坐实:
    `dist_low_60` 对 fwd_2_oc **反预测**(decile spread_t=−2.06,方向与 IC 反号)——贴 60 日低点
    一档 fwd_2_oc 反而显著**跑输**贴高点一档,即"光有『前置低位』=接刀"。故③必须是真 AND 硬门:
    无量突破的票**一律不入召回**,不能降级成软加分让低位票混进来对冲低分。两路双路并跑(影子对照,
    不动旧 reversal),channel_eval 按 lane 分行裁决。

    四段(全 EOD 可算;①②④缺列 presence-gated 跳过=不拦,③缺列/缺值=整段判 False,不可跳):
      ① 前置低位:pct_60d≤−25 或 dist_low_60≤15(后者 Plan A1-T2 新因子,尚未接入现场 L1 帧,
         presence-gated;两条本是 OR,pct_60d 视作恒在核心列,同 lens_momentum/lens_reversal
         的既有用法不做存在性判断)。
      ② 衰竭企稳:days_no_new_low≥10(presence-gated)∧ 5日均量<20日均量(`vol_ma5`/`vol_ma20`,
         现场尚无此列,presence-gated 跳过,接入前恒放行)∧ RSI6 从超卖回升——本帧只有『当日』
         RSI6 快照、无逐日序列做不了真"从…回升",用 presence-gated 代理:已脱离本代码库既定的
         超卖线(rsi6<20,见 analyze/harvest.py "超卖"判词)但仍处 20–50 的低位回升带。
      ③ 确认起爆硬门:vol_ratio_20≥1.5(Plan A1-T2 新因子)∧ ma_bull>0(现场无逐日『破20日高/
         站上MA20』专列,用既有『多头排列』ma_bull 作最近似代理)。**两者任一缺列/缺值 →
         该段整段判 False**——硬门不可 presence-gated 跳过:vol_ratio_20 若尚未接入现场,本通道
         会诚实地空召回,而不是悄悄放行凑数(这正是"无量突破不入池"延伸到"没证据也不入池")。
      ④ 可交易:镜像 `lens_reversal`(:167)既有的 `~name.str.contains("退")` 过滤,叠加
         presence-gated 的既有『涨跌停可交易性』`buyable` 列(factor_lab.forward_returns /
         data.handler 同源,列缺→默认可交易不拦)。

    **禁用 CMF-20 作确认信号**:07-02 汇川/柳工机会成本红队实证,CMF-20 是窗口累积指标,对反转
    方向确认存在 day-1/2 滞后(反应慢半拍),不适合当"当日确认"用;③只用当日 vol_ratio_20 +
    ma_bull,不掺 cmf_20/obv_mom_20。

    评分(仅 ①②③;④是纯可交易性门,不进分,与 lens_reversal 对 tradability 的处理一致):
    低位30 + 企稳30 + 确认40,`_wsum` 按"有值子项"重新归一(某段全 NaN 不拖累)。
    """
    g = df.copy()
    nan = pd.Series(np.nan, index=g.index)

    # ① 前置低位(30):pct_60d 视为恒在核心列;dist_low_60 presence-gated——列缺时 `<=15`
    # 天然 NaN 比较→False,OR 自动退化为只看 pct_60d。
    dist_low = _num(g["dist_low_60"]) if "dist_low_60" in g.columns else nan
    lowpos_gate = (_num(g["pct_60d"]) <= -25) | (dist_low <= 15)
    lowpos_sc = _blend((_pct(g["pct_60d"], ascending=False), 0.6), (_pct(dist_low, ascending=False), 0.4))

    # ② 衰竭企稳(30):三子条件独立 presence-gated——gate 侧列缺→显式 True(不拦),
    # score 侧列缺→NaN(交 _blend 重新归一)。vol_ma5/vol_ma20 现场尚无该列,恒走 else 分支。
    has_days = "days_no_new_low" in g.columns
    days_ok = (_num(g["days_no_new_low"]) >= 10) if has_days else pd.Series(True, index=g.index)
    days_sc = _pct(g["days_no_new_low"]) if has_days else nan

    has_vol_ma = {"vol_ma5", "vol_ma20"} <= set(g.columns)
    shrink_ok = (_num(g["vol_ma5"]) < _num(g["vol_ma20"])) if has_vol_ma else pd.Series(True, index=g.index)

    has_rsi = "rsi6" in g.columns
    rsi = _num(g["rsi6"]) if has_rsi else nan
    rebound_ok = ((rsi >= 20) & (rsi <= 50)) if has_rsi else pd.Series(True, index=g.index)
    rebound_sc = ((rsi >= 20) & (rsi <= 50)).astype(float) if has_rsi else nan

    stabilize_gate = days_ok & shrink_ok & rebound_ok
    stabilize_sc = _blend((days_sc, 0.5), (rebound_sc, 0.5))

    # ③ 确认起爆硬门(40):vol_ratio_20/ma_bull 缺列或缺值 → 比较天然 NaN→False,硬门自动
    # "不可跳"(与①②故意不同,这里不写 presence-gated 的 else 分支去放行)。
    vol20 = _num(g["vol_ratio_20"]) if "vol_ratio_20" in g.columns else nan
    has_ma_bull = "ma_bull" in g.columns
    ma_bull = _num(g["ma_bull"]) if has_ma_bull else nan
    confirm_gate = (vol20 >= 1.5) & (ma_bull > 0)
    ma_bull_sc = (ma_bull > 0).astype(float) if has_ma_bull else nan
    confirm_sc = _blend((_pct(vol20), 0.5), (ma_bull_sc, 0.5))

    # ④ 可交易:镜像 lens_reversal(:167)既有过滤 + presence-gated 现有『buyable』涨跌停可交易性列。
    tradable = ~g["name"].fillna("").str.contains("退")
    if "buyable" in g.columns:
        tradable = tradable & g["buyable"].fillna(True).astype(bool)

    gate = lowpos_gate & stabilize_gate & confirm_gate & tradable
    score = _wsum({"lowpos": (lowpos_sc, 30), "stabilize": (stabilize_sc, 30), "confirm": (confirm_sc, 40)})

    g["reversal_confirm_score"] = score
    g["reversal_confirm_gate"] = gate
    g["reversal_confirm_signals"] = np.where(confirm_gate, "起爆确认·硬门过", "未过起爆硬门")
    return g


def healthy_riser_mask(frame: pd.DataFrame) -> pd.Series | None:
    """健康上涨谓词(**单一事实源**:menu_health 病灶指标 = healthy 召回通道同一定义):
    0<pct_60d<40(温和上涨,未过热)∧ main_net_ratio>0(主力真进)∧ cmf_20>0(多日资金共振)。

    07-02 取证:全市场 261 只健康上涨 **0 只**过召回线(rank 中位 3760)——momentum 路被
    pct60 100%+ 猛票占满、吸筹路要底部、T+1 IC composite 在 range 下偏爱接刀价值,该品相
    是 9 路召回的结构性空洞。缺任一列 → None(调用方降级)。
    """
    if not all(c in frame.columns for c in ("pct_60d", "main_net_ratio", "cmf_20")):
        return None
    p, m, c = _num(frame["pct_60d"]), _num(frame["main_net_ratio"]), _num(frame["cmf_20"])
    return (p > 0) & (p < 40) & (m > 0) & (c > 0)


def main_net_distortion_label(ratio, inflow_yi, ratio_min: float = 0.02,
                              abs_min_yi: float = 0.5) -> str:
    """主力占比失真判型(**单一事实源**:L3 表 dist_flag 列 = L4 简报标注同一定义)。

    2026-07-03 取证:两型精确命中 L4 逐卡辟谣的 ~18/30 finalist(重复深核的浪费源):
      反号 = main_net_ratio>0 而绝对净额(main_inflow_yi,亿)<0 —— 占比与绝对矛盾(窗口/口径放大);
      微量 = 占比≥ratio_min 而 |绝对|<abs_min_yi —— 绝对量撑不起占比读数(微盘放大,
             如东北证券 +8.7% 实为 +0.03亿)。
    规则:两型下「主力净流入」不得单独作核心多头论点,须绝对净额+cmf/obv 同向共振才算确认。
    占比≤0 无多头读数不标;缺值/非数容错返回 ""。
    """
    try:
        r, a = float(ratio), float(inflow_yi)
    except (TypeError, ValueError):
        return ""
    if r != r or a != a or r <= 0:          # NaN / 占比非正
        return ""
    if a < 0:
        return "反号"
    if r >= ratio_min and abs(a) < abs_min_yi:
        return "微量"
    return ""


def l3_misread_flags(row) -> str:
    """L3 误读三预警旗(07-08 诊断:L4 打脸 L3 的证据 22/31 纯表内可见,三模式确定性可预检)。
    低基=np_yoy>100∧roe<8(低基数幻觉);背离=cmf/obv 任一>0∧当日主力<0(拉高派发嫌疑);
    套牢=winner_rate<25∧ma_bull=0∧pct_60d>0(低获利盘反弹撞套牢盘≠上行空间;07-08 校准加
    pct_60d>0——risk_off 深跌市前二条覆盖 ~90% 是 wallpaper,一路下跌股可见地跌不属该误读)。
    任一输入缺/NaN → 该旗不亮(不冤枉)。"""
    import pandas as pd

    def _f(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return None if pd.isna(v) else v

    g = row.get if hasattr(row, "get") else (lambda k, d=None: None)
    flags = []
    np_yoy, roe = _f(g("np_yoy")), _f(g("roe"))
    if np_yoy is not None and roe is not None and np_yoy > 100 and roe < 8:
        flags.append("低基")
    cmf, obv, main = _f(g("cmf_20")), _f(g("obv_mom_20")), _f(g("main_net_ratio"))
    if main is not None and main < 0 and ((cmf is not None and cmf > 0) or (obv is not None and obv > 0)):
        flags.append("背离")
    wr, ma, pct60 = _f(g("winner_rate")), _f(g("ma_bull")), _f(g("pct_60d"))
    if wr is not None and ma is not None and pct60 is not None and wr < 25 and ma == 0 and pct60 > 0:
        flags.append("套牢")
    return "·".join(flags)


def pledge_flag_label(ratio, high: float = 40.0, warn: float = 20.0) -> str:
    """股权质押比例判型(**单一事实源**:L4 简报质押旗 = 深核 slim 质押段同一阈值)。

    >high(%)= 爆雷红旗(平仓线连锁风险);>warn = 偏高;低/缺值 → ""(不加噪)。
    spec: 2026-07-05 计量·校准四件套 §5.2。
    """
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return ""
    if r != r:                               # NaN
        return ""
    return "爆雷红旗" if r > high else ("偏高" if r > warn else "")


# ───────────────────────── 10 因子组 + 行业条件化复合分 ─────────────────────────

# 10 因子组(自然朝向:高=常规看多;真方向由 weights.json 的 IC 符号决定)。
# volprice = 多日量价资金流(CMF+OBV;序列指标,IC 实证 decile +40bps/t≈2,远胜已剔的单日 vol_ratio)。
# rz = 融资买入强度(rz_buy_intensity;07-10 T7 六因子重审三条全过门,pr_20260710_001,唯一过线
# 机构因子——见 _factor_groups 该组注释)。
_GROUPS = ("momentum", "fund_main", "fund_retail", "chip", "north", "tech", "growth", "value",
          "volprice", "rz")

# weights.json 缺失时的先验(仅 __global__;慢因子 growth/value 给小权重——T+1 近噪声但仍纳入)。
_PRIOR_WEIGHTS = {"meta": {"source": "prior(无 weights.json)"}, "weights": {"__global__": {
    "momentum": 0.10, "fund_main": 0.06, "fund_retail": -0.02, "chip": 0.02,
    "north": 0.03, "tech": -0.03, "growth": 0.03, "value": 0.03, "volprice": 0.04, "rz": 0.02}}}


def _load_weights(path: str = "context/factor_lab/weights.json", regime: str | None = None) -> dict:
    """读 factor_lab 校准产物;缺失则回落内置先验(并提示)。

    `regime` 给定且文件含 `regimes[regime]["weights"]` → 返回该 regime 的权重块(meta 标 regime);
    否则返回 flat(现行为)。**parity 锚**:无 regimes 块 / regime=None / 未知 regime 一律退 flat,
    与改动前逐值一致(regime-aware 默认关时不破 golden)。
    """
    import sys

    p = Path(path)
    if not p.exists():
        print(f"[warn] {path} 不存在 → 用内置先验权重(建议先 factor_lab calibrate)", file=sys.stderr)
        return _PRIOR_WEIGHTS
    data = json.loads(p.read_text(encoding="utf-8"))
    if regime:
        block = (data.get("regimes") or {}).get(regime)
        if block and block.get("weights"):
            meta = dict(data.get("meta", {}))
            meta["regime"] = regime
            return {"meta": meta, "weights": block["weights"]}
    return data


def pick_weights(frame: pd.DataFrame, regime_aware: bool, *,
                 path: str = "context/factor_lab/weights.json", load=_load_weights):
    """L1 权重选择(L1Recall stage 与 universe.run 共用 → 两路一致)。

    `regime_aware` 关(默认)→ flat 权重(regime=None,**parity**:不分类、与改动前一致);
    开 → `classify_regime(frame)` 得 regime → `_load_weights(regime=label)` 取该 regime 权重块。
    返回 (weights_dict, regime_label|None)。`load` 可注入(测试)。
    """
    if not regime_aware:
        return load(path), None
    from autoresearch.common.regime import classify_regime
    label = classify_regime(frame).label
    return load(path, regime=label), label


def _factor_groups(df: pd.DataFrame) -> dict[str, pd.Series]:
    """10 组子分(各 0–1 横截面分位,自然朝向)。缺列的组返回全 NaN。calibrate 与 composite 共用。"""
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
        # 融资买入强度(rz_buy_intensity = rzmre/成交额;自然朝向+,杠杆资金买入越猛越看多)——
        # 07-10 T7 六因子重审三条全过门(pr_20260710_001,ICIR 0.134/IC 两半同号/decile spread_t
        # 2.11,唯一过线机构因子),真方向随 calibrate 的 IC 符号定。生产帧已接 margin_detail
        # (fetch_universe_tushare,FN-1 第五修——2026-07-11 终审逮到"入组无数据面"的 no-op);
        # 缺权限/端点失败 → NaN 降级,composite_score 重归一自动跳过(wabs 不计入),不破 parity。
        "rz": p("rz_buy_intensity"),
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

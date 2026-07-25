#!/usr/bin/env python3
"""macro-research · A股中观增强 —— tushare 版(绕开被封 push2 的 akshare 中观取数)。

design: docs/specs/2026-06-20-... (macro Phase 2 的"补全两融/行业PE")。

供 autoresearch.macro.harvest 在 A股中观块**优先**调用(失败回退 akshare `meso_ashare_block`):
  * 北向/南向官方汇总(moneyflow_hsgt)—— 解决"个股实时披露 2024-08 已停",这是可靠汇总口径。
  * 两融余额趋势(margin)—— 风险偏好计(融资余额↑=加杠杆/risk-on);playbook 标的 Phase 2 项。
  * 行业资金净流入(moneyflow_ind_ths,90 申万级,net_amount 已是亿)—— sector_map 逐行主力资金。
  * 涨停情绪(limit_list_d:家数/最高连板/最热行业)—— 情绪周期档。
  * 指数估值(index_dailybasic:沪深300/创业板/中证500 PE_ttm + 近1年分位)—— regime 估值锚。

全部 tushare(api.tushare.pro,非 push2),失败/无 token → 返回 None,harvest 回退 akshare。
单位已按 schema 实测对齐(north_money 万元/100… 实测 /1e4=亿;rzye 元/1e8=亿;THS net_amount 已=亿)。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

import pandas as pd

from autoresearch.data.tushare_source import _pro, _ts_call, resolve_momentum_dates


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _back(yyyymmdd: str, days: int) -> str:
    return (datetime.strptime(yyyymmdd, "%Y%m%d") - timedelta(days=days)).strftime("%Y%m%d")


def _pctile(hist: pd.Series, cur: float) -> float:
    h = _num(hist).dropna()
    return float((h < cur).mean() * 100) if len(h) else float("nan")


# ───────────────────────── 各中观子块 ─────────────────────────


def _northbound(pro, last: str) -> str:
    """北向/南向官方汇总 —— 取数走 `data.macro_cn`(单一事实源),本函数只渲染。"""
    from autoresearch.data.macro_cn import northbound_data
    d = northbound_data(pro, last)
    rows = ["| 日期 | 北向净流入(亿) | 南向净流入(亿) |", "|---|---:|---:|"]
    for r in d["rows"]:
        rows.append(f"| {r['date']} | {r['north_yi']:+.1f} | {r['south_yi']:+.1f} |")
    return ("**北向/南向(tushare moneyflow_hsgt,官方汇总;个股实时披露 2024-08 已停,此为可靠汇总口径)**:"
            f"最新北向 **{d['latest_yi']:+.1f} 亿**、近5日累计 **{d['cum5_yi']:+.1f} 亿**"
            f"(>0=外资净买、风险偏好升)。\n" + "\n".join(rows))


def _margin(pro, last: str) -> str:
    """两融余额 —— 取数走 `data.macro_cn`,本函数只渲染。"""
    from autoresearch.data.macro_cn import margin_data
    d = margin_data(pro, last)
    latest, d5 = d["rzye_yi"], d["d5_yi"]
    arrow = "↑加杠杆(risk-on)" if d5 > 0 else "↓去杠杆(risk-off)"
    return (f"**两融融资余额(tushare margin,沪深合计)**:最新 **{latest / 1e4:.2f} 万亿**"
            f"(较上日 {d['d1_yi']:+.0f} 亿、近5日 {d5:+.0f} 亿 → **{arrow}**)。")


def _sector_flow(pro, last: str, topn: int = 8) -> str:
    """行业资金净流入排名 —— 取数走 `data.macro_cn`,本函数只渲染。"""
    from autoresearch.data.macro_cn import sector_flow_data
    d = sector_flow_data(pro, last, topn=topn)
    rows = ["| 行业 | 主力净流入(亿) | 领涨股 |", "|---|---:|---|"]
    rows += [f"| {r['industry']} | {r['net_yi']:+.1f} | {r['lead']} |" for r in d["top"]]
    rows.append("| … | … | … |")
    rows += [f"| {r['industry']} | {r['net_yi']:+.1f} | {r['lead']} |" for r in d["bottom"]]
    return (f"**行业资金净流入(tushare moneyflow_ind_ths,{last};top{topn} 入 / bottom5 出)**:\n"
            + "\n".join(rows))


def _limit_sentiment(pro, last: str) -> str:
    """涨停情绪(limit_list_d:家数/最高连板/连板数/最热行业)。"""
    df = _ts_call(lambda: pro.limit_list_d(trade_date=last, limit_type="U"))
    n = len(df)
    lt = _num(df["limit_times"])
    maxlb = int(lt.max()) if n else 0
    n_lianban = int((lt >= 2).sum())
    hot = df["industry"].value_counts().head(3)
    hot_s = "、".join(f"{k}({v})" for k, v in hot.items())
    mood = "亢奋" if n > 80 else "活跃" if n > 40 else "退潮" if n < 20 else "中性"
    return (f"**涨停情绪(tushare limit_list_d,{last})**:涨停 **{n}** 家、其中连板 **{n_lianban}** 家、"
            f"最高 **{maxlb} 连板** → 情绪 **{mood}**;涨停最集中行业:{hot_s}。"
            "(家数多+连板高=情绪亢奋/题材发酵;少=退潮避险)")


def _index_valuation(pro, last: str) -> str:
    """指数估值 —— 取数走 `data.macro_cn`,本函数只渲染。"""
    from autoresearch.data.macro_cn import index_val_data
    rows = ["| 指数 | PE(ttm) | 近1年分位 | PB |", "|---|---:|---:|---:|"]
    for v in index_val_data(pro, last).values():
        if v.get("pe_ttm") is None:
            rows.append(f"| {v['name']} | 取数失败 | {v.get('error', '')} | |")
            continue
        pct = v["pe_pct_1y"]
        tag = "" if pct is None else ("(低估区)" if pct < 30 else "(高估区)" if pct > 70 else "")
        pct_s = "—" if pct is None else f"{pct:.0f}%"
        rows.append(f"| {v['name']} | {v['pe_ttm']:.1f} | {pct_s}{tag} | {v['pb']:.2f} |")
    return "**指数估值(tushare index_dailybasic;PE_ttm 近1年分位=regime 估值锚)**:\n" + "\n".join(rows)


# ───────────────────────── 编排 ─────────────────────────


def meso_block_ts(curr_date: str) -> str | None:
    """A股中观(tushare):北向汇总 + 两融 + 行业资金 + 涨停情绪 + 指数估值。失败/无 token → None。"""
    try:
        pro = _pro()
    except Exception:
        return None
    last = resolve_momentum_dates(pro, curr_date)[0]
    blocks = [
        ("北向/南向", _northbound), ("两融", _margin), ("行业资金", _sector_flow),
        ("涨停情绪", _limit_sentiment), ("指数估值", _index_valuation),
    ]
    out: list[str] = []
    for label, fn in blocks:
        try:
            out.append(fn(pro, last))
        except Exception as e:  # noqa: BLE001
            out.append(f"_{label}(tushare)取数失败: {e} → 回退 akshare/WebSearch。_")
    if not out:
        return None
    return (f"_A股中观增强源:tushare(as-of 交易日 {last});个股实时北向已停,汇总口径可靠。_\n\n"
            + "\n\n".join(out))


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "2026-06-20"
    print(meso_block_ts(d) or "_无 token / 全失败_")

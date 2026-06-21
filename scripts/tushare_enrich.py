#!/usr/bin/env python3
"""A股单只增强 —— tushare 版(替代被封 push2 的 akshare 个股富化)。

供 harvest_context.py 在 A股标的上**优先**调用(失败则回退 akshare):
  * ashare_market_context_ts —— 主力资金流(10日)+ 技术(多头排列/RSI/MACD)+
    筹码(获利比例/套牢)+ 北向(沪深股通持股)。
  * ashare_shareholder_ts   —— 股东户数趋势 + 质押比例(爆雷红旗)。
  * ashare_calendar_ts      —— 业绩预告 / 业绩快报(前瞻成长催化)。

全部 per-ticker(ts_code),决策驱动,slim 也保留。任一失败/无 token → 返回 None,
harvest 自动回退 akshare 或 WebSearch 提示。token 走项目 .env 的 TUSHARE_TOKEN。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

# 复用 tushare_source 的句柄/重试/日期解析(同一 token、同一防御层)
from autoresearch.data.tushare_source import _pro, _ts_call, resolve_momentum_dates


def _tscode(sym: str) -> str:
    """normalize_symbol 的 .SS/.SZ/.BJ → tushare 的 .SH/.SZ/.BJ。"""
    code = sym.split(".")[0]
    suf = {".SS": "SH", ".SZ": "SZ", ".BJ": "BJ"}.get(sym[-3:].upper(), "SH")
    return f"{code}.{suf}"


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _last_trade(pro, curr_date: str) -> str:
    return resolve_momentum_dates(pro, curr_date)[0]


# ───────────────────────── 市场上下文(主力/技术/筹码/北向) ─────────────────────────


def ashare_market_context_ts(sym: str, curr_date: str) -> str | None:
    """主力资金流(10日)+ 技术结构(多头排列/RSI/MACD)+ 筹码(获利比例)+ 北向。"""
    try:
        pro = _pro()
    except Exception:
        return None
    tc = _tscode(sym)
    last = _last_trade(pro, curr_date)
    out: list[str] = []

    # 1) 主力资金流(近 10 交易日)
    try:
        start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=28)).strftime("%Y%m%d")
        mf = _ts_call(lambda: pro.moneyflow(ts_code=tc, start_date=start, end_date=last))
        mf = mf.sort_values("trade_date").tail(10)
        net = _num(mf["net_mf_amount"]) / 1e4  # 万元 → 亿
        if len(net):
            cum, lastd, pos = net.sum(), net.iloc[-1], int((net > 0).sum())
            rows = ["| 日期 | 主力净流入(亿) |", "|---|---:|"]
            for dt, v in zip(mf["trade_date"].tail(5), net.tail(5), strict=True):
                rows.append(f"| {dt} | {v:+.2f} |")
            out.append(f"**主力资金流(个股,tushare moneyflow)**:近10日合计 **{cum:+.2f} 亿**"
                       f"({pos}/10 日净流入),最新日 {lastd:+.2f} 亿。\n" + "\n".join(rows))
    except Exception as e:  # noqa: BLE001
        out.append(f"_tushare 主力资金流取数失败: {e}_")

    # 2) 技术结构(stk_factor_pro,前复权)
    try:
        f = "ts_code,close,ma_qfq_5,ma_qfq_10,ma_qfq_20,ma_qfq_60,rsi_qfq_6,rsi_qfq_12,macd_qfq,macd_dif_qfq,macd_dea_qfq"
        sf = _ts_call(lambda: pro.stk_factor_pro(ts_code=tc, trade_date=last, fields=f))
        if len(sf):
            r = sf.iloc[0]
            c, m5, m10, m20, m60 = (float(_num(pd.Series([r[k]])).iloc[0]) for k in
                                    ("close", "ma_qfq_5", "ma_qfq_10", "ma_qfq_20", "ma_qfq_60"))
            bull = "是" if (m5 > m10 > m20 > m60) else "否"
            pos60 = "上方" if c > m60 else "下方"
            rsi6 = float(_num(pd.Series([r["rsi_qfq_6"]])).iloc[0])
            dif = float(_num(pd.Series([r["macd_dif_qfq"]])).iloc[0])
            dea = float(_num(pd.Series([r["macd_dea_qfq"]])).iloc[0])
            cross = "金叉" if dif > dea else "死叉"
            out.append(f"**技术结构(tushare stk_factor_pro,前复权)**:多头排列 **{bull}**、"
                       f"价在 MA60 **{pos60}**(收{c:.2f}/MA60 {m60:.2f})、RSI6 **{rsi6:.0f}**"
                       f"({'过热' if rsi6 > 80 else '超卖' if rsi6 < 20 else '中性'})、MACD **{cross}**(DIF{dif:+.3f}/DEA{dea:+.3f})。")
    except Exception as e:  # noqa: BLE001
        out.append(f"_tushare 技术因子取数失败: {e}_")

    # 3) 筹码(每日筹码及胜率)
    try:
        cy = _ts_call(lambda: pro.cyq_perf(ts_code=tc, trade_date=last,
                                           fields="ts_code,his_low,his_high,cost_50pct,winner_rate"))
        if len(cy):
            r = cy.iloc[0]
            wr = float(_num(pd.Series([r["winner_rate"]])).iloc[0])
            c50 = float(_num(pd.Series([r["cost_50pct"]])).iloc[0])
            out.append(f"**筹码(tushare cyq_perf)**:获利比例 **{wr:.0f}%**"
                       f"({'高位获利盘重' if wr > 85 else '深度套牢/超跌' if wr < 15 else '中性'})、"
                       f"筹码平均成本(中位) {c50:.2f}。")
    except Exception as e:  # noqa: BLE001
        out.append(f"_tushare 筹码取数失败: {e}_")

    # 4) 北向(沪深股通持股)
    try:
        hk = _ts_call(lambda: pro.hk_hold(ts_code=tc, trade_date=last, fields="ts_code,vol,ratio"))
        if len(hk):
            ratio = float(_num(pd.Series([hk.iloc[0]["ratio"]])).iloc[0])
            out.append(f"**北向(沪深股通)**:持股占比 **{ratio:.2f}%**(聪明钱仓位;趋势需对比历史)。")
        else:
            out.append("**北向(沪深股通)**:非标的/无持股记录。")
    except Exception as e:  # noqa: BLE001
        out.append(f"_tushare 北向取数失败: {e}_")

    return "\n\n".join(out) if out else None


# ───────────────────────── 股东户数 + 质押 ─────────────────────────


def ashare_shareholder_ts(sym: str) -> str | None:
    """股东户数趋势(集中度)+ 质押比例(爆雷红旗)。"""
    try:
        pro = _pro()
    except Exception:
        return None
    tc = _tscode(sym)
    out: list[str] = []
    try:
        hn = _ts_call(lambda: pro.stk_holdernumber(ts_code=tc))
        if len(hn):
            hn = hn.sort_values("end_date").tail(4)
            seq = [(r["end_date"], int(_num(pd.Series([r["holder_num"]])).iloc[0])) for _, r in hn.iterrows()]
            trend = "减少(筹码集中→偏多)" if seq[-1][1] < seq[0][1] else "增加(筹码分散→偏空)"
            s = " → ".join(f"{d}:{n:,}" for d, n in seq)
            out.append(f"**股东户数(tushare)**:{s};近趋势 **{trend}**。")
    except Exception as e:  # noqa: BLE001
        out.append(f"_tushare 股东户数取数失败: {e}_")
    try:
        pl = _ts_call(lambda: pro.pledge_stat(ts_code=tc))
        if len(pl):
            pl = pl.sort_values("end_date").tail(1).iloc[0]
            pr = float(_num(pd.Series([pl["pledge_ratio"]])).iloc[0])
            flag = "⚠️高质押(爆雷红旗)" if pr > 40 else "偏高" if pr > 20 else "可控"
            out.append(f"**股权质押(tushare)**:质押比例 **{pr:.1f}%**({flag}),截至 {pl['end_date']}。")
    except Exception as e:  # noqa: BLE001
        out.append(f"_tushare 质押取数失败: {e}_")
    return "\n\n".join(out) if out else None


# ───────────────────────── 业绩预告 / 快报(前瞻催化) ─────────────────────────


def ashare_calendar_ts(sym: str, curr_date: str) -> str | None:
    """业绩预告(forecast)+ 业绩快报(express):比定期报告更前瞻的成长信号。"""
    try:
        pro = _pro()
    except Exception:
        return None
    tc = _tscode(sym)
    out: list[str] = []
    try:
        fc = _ts_call(lambda: pro.forecast(ts_code=tc))
        if len(fc):
            r = fc.sort_values("ann_date").tail(1).iloc[0]
            lo = _num(pd.Series([r["p_change_min"]])).iloc[0]
            hi = _num(pd.Series([r["p_change_max"]])).iloc[0]
            rng = f"{lo:+.0f}%~{hi:+.0f}%" if pd.notna(lo) and pd.notna(hi) else "—"
            out.append(f"**业绩预告(tushare,{r['end_date']})**:类型 **{r.get('type', '—')}**、"
                       f"净利同比 **{rng}**。原因:{str(r.get('change_reason') or '—')[:60]}")
    except Exception as e:  # noqa: BLE001
        out.append(f"_tushare 业绩预告取数失败: {e}_")
    try:
        ex = _ts_call(lambda: pro.express(ts_code=tc))
        if len(ex):
            r = ex.sort_values("ann_date").tail(1).iloc[0]
            yoy = _num(pd.Series([r["yoy_net_profit"]])).iloc[0]
            roe = _num(pd.Series([r["diluted_roe"]])).iloc[0]
            out.append(f"**业绩快报(tushare,{r['end_date']})**:净利同比 **{yoy:+.1f}%**、"
                       f"摊薄ROE {roe:.2f}%(快报=未审计,早于正式财报)。")
    except Exception as e:  # noqa: BLE001
        out.append(f"_tushare 业绩快报取数失败: {e}_")
    return "\n\n".join(out) if out else None

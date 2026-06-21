#!/usr/bin/env python3
"""scan-market · L0 备用取数源 —— tushare(替代被网络封锁的东财 push2)。

design: docs/specs/2026-06-20-scan-market-design.md(§4.4 坑③ 的"切换 universe 源")

背景:东财实时快照 `stock_zh_a_spot_em` / 资金流 `stock_individual_fund_flow_rank`
都在 `push2.eastmoney.com` 上,该主机常被中国大陆以外/部分 ISP 网络级封锁。tushare
(`api.tushare.pro`)是另一条链路,且 `daily_basic` 一把覆盖 市值/PE/PB/量比/换手/
**股息率**,`daily` 给价/量、算 60日·YTD 动量,`moneyflow` 给主力净流入;高权限 token
还能拿 `stk_factor_pro`(MA多头排列/RSI/MACD)与 `cyq_perf`(筹码获利比例)——比原
push2 设计更厚。

本模块只负责"取数 + 富化成 canonical 列",**打分/板块/输出全部复用 screen_market**:
canonical 列与 `screen_market.fetch_universe`(东财路径)完全一致,外加可选增强列
(`dv_ratio` / `ma_bull` / `above_ma60` / `rsi6` / `winner_rate` / `cost_50pct`),供
价值/动量/反转透镜(列存在才用,缺则降级)与 L3a/L3b 使用。

基本面(营收/净利/YoY/ROE/毛利/CFO/所处行业)仍走能跑通的东财 datacenter 端点
`stock_yjbb_em`(akshare,**非** push2,未被封)——与东财路径同源,口径一致。

用法(被 screen_market 调用):
  uv run --no-sync python scripts/screen_market.py 2026-06-20 --source tushare
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
from screen_market import _ak_call, _apply_universe_gates, _col

# 纯打分原语走包内(数值化 / 报告期推算);I/O·网络 helper(防御取列 / akshare 重试 / 硬门)
# 仍在 scripts/screen_market(经 sources._ensure_scripts_on_path 桥接,E5/E6 搬完即并入包)。
from autoresearch.common.scoring import _num, latest_reported_quarter, prev_quarter

# ───────────────────────── token / pro 句柄 ─────────────────────────


def _load_env_token() -> str:
    """从环境或项目 .env 读 TUSHARE_TOKEN(不打印值)。"""
    tok = os.environ.get("TUSHARE_TOKEN")
    if not tok:
        envp = Path(".env")
        if envp.exists():
            for ln in envp.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    k, v = ln.split("=", 1)
                    if k.strip() == "TUSHARE_TOKEN":
                        tok = v.strip().strip('"').strip("'")
                        break
    if not tok:
        raise RuntimeError("TUSHARE_TOKEN 未配置(环境变量或项目根目录 .env)")
    return tok


def _pro():
    import tushare as ts

    return ts.pro_api(_load_env_token())


def _ts_call(fn, tries: int = 4, backoff: float = 1.5):
    """tushare 调用重试:限频(每分钟上限)→ 长睡;其它网络错 → 线性退避。"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            msg = str(e)
            if any(s in msg for s in ("每分钟", "频繁", "抱歉,您")):
                time.sleep(max(backoff * (i + 1), 8.0))
            else:
                time.sleep(backoff * (i + 1))
    raise last


def _code6(ts_code: pd.Series) -> pd.Series:
    """'600519.SH' → '600519'。"""
    return ts_code.astype(str).str.split(".").str[0].str.zfill(6)


# ───────────────────────── 交易日历 ─────────────────────────


def _trade_days(pro, start: str, end: str) -> list[str]:
    cal = _ts_call(lambda: pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1"))
    return sorted(cal["cal_date"].astype(str).tolist())


def resolve_momentum_dates(pro, analysis_date: str) -> tuple[str, str, str]:
    """返回(最近交易日 last, 60交易日前 d60, 年初首个交易日 dys)。

    A股以分析日所在自然日为界,取 ≤ 分析日 的最近开市日做"现价"基准;动量用真实
    交易日间隔(非自然日),避免节假日扭曲。
    """
    yyyymmdd = analysis_date.replace("-", "")
    year = int(yyyymmdd[:4])
    days = _trade_days(pro, f"{year - 1}0101", yyyymmdd)
    if not days:
        raise RuntimeError(f"无交易日(start={year - 1}0101 end={yyyymmdd})")
    last = days[-1]
    idx = days.index(last)
    d60 = days[max(0, idx - 60)]
    ys_days = [d for d in days if d[:4] == str(year)]
    dys = ys_days[0] if ys_days else days[max(0, idx - 120)]
    return last, d60, dys


# ───────────────────────── 基本面(东财 datacenter,非 push2) ─────────────────────────


def fetch_fundamentals_yjbb(analysis_date: str) -> pd.DataFrame:
    """业绩(当期+上期 YoY 算加速度)+ 所处行业,经 akshare `stock_yjbb_em`。

    与 screen_market.fetch_universe 的东财路径同源、同口径(同样的列)。该端点在
    datacenter-web.eastmoney.com,**不在被封的 push2**。
    """
    import akshare as ak

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
    try:
        yjp = _ak_call(lambda: ak.stock_yjbb_em(date=prev_quarter(q)))
        prev = pd.DataFrame(
            {
                "code": yjp[_col(yjp, "股票代码", required=True)].astype(str).str.zfill(6),
                "np_yoy_prev": _num(yjp[_col(yjp, "净利润-同比增长")]),
                "rev_yoy_prev": _num(yjp[_col(yjp, "营业总收入-同比增长")]),
            }
        )
        fin = fin.merge(prev, on="code", how="left")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 上期业绩取数失败({e!r})→ 成长加速度降级", flush=True)
        fin["np_yoy_prev"] = np.nan
        fin["rev_yoy_prev"] = np.nan
    return fin


# ───────────────────────── tushare 增强因子(可选,高权限 token) ─────────────────────────


def _fetch_factors(pro, last: str) -> pd.DataFrame | None:
    """stk_factor_pro(MA多头排列/RSI)+ cyq_perf(筹码获利比例)。失败则返回 None(降级)。"""
    try:
        sf = _ts_call(lambda: pro.stk_factor_pro(
            trade_date=last,
            fields="ts_code,close,ma_qfq_5,ma_qfq_10,ma_qfq_20,ma_qfq_60,rsi_qfq_6,rsi_qfq_12,macd_qfq",
        ))
        c = _num(sf["close"])
        m5, m10, m20, m60 = (_num(sf[f"ma_qfq_{n}"]) for n in (5, 10, 20, 60))
        fac = pd.DataFrame(
            {
                "code": _code6(sf["ts_code"]),
                "ma_bull": ((m5 > m10) & (m10 > m20) & (m20 > m60)).astype(float),  # 多头排列
                "above_ma60": (c > m60).astype(float),
                "rsi6": _num(sf["rsi_qfq_6"]),
                "rsi12": _num(sf["rsi_qfq_12"]),
                "macd": _num(sf["macd_qfq"]),
            }
        )
    except Exception as e:  # noqa: BLE001
        print(f"[warn] stk_factor_pro 取数失败({e!r})→ 趋势结构降级为代理", flush=True)
        return None
    try:
        cy = _ts_call(lambda: pro.cyq_perf(
            trade_date=last, fields="ts_code,winner_rate,cost_15pct,cost_50pct,cost_85pct,weight_avg"))
        c50 = _num(cy["cost_50pct"])
        cyf = pd.DataFrame(
            {
                "code": _code6(cy["ts_code"]),
                "winner_rate": _num(cy["winner_rate"]),  # 获利比例(0–100)
                "cost_50pct": c50,                        # 筹码平均成本(中位)
                # 筹码集中度 = (cost_85-cost_15)/cost_50;越小越集中(主力控盘),越大越分散
                "chip_concentration": (_num(cy["cost_85pct"]) - _num(cy["cost_15pct"])) / c50,
            }
        )
        fac = fac.merge(cyf, on="code", how="left")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] cyq_perf 取数失败({e!r})→ 筹码因子缺省", flush=True)
    return fac


def _moneyflow_struct_cols(mf: pd.DataFrame) -> pd.DataFrame:
    """moneyflow 全单结构 → 大单+特大单净额(主力)+ 小单净额(散户),单位亿。纯函数,

    便于离线测且与 factor_lab 共用同一口径。万元 /1e4=亿;主力净占比(/成交额)在调用处算
    (moneyflow 端点未必带 amount,用 daily 的成交额更稳)。
    """
    def g(c):
        return _num(mf[c]) if c in mf.columns else 0.0

    main_net = g("buy_lg_amount") + g("buy_elg_amount") - g("sell_lg_amount") - g("sell_elg_amount")
    retail_net = g("buy_sm_amount") - g("sell_sm_amount")
    return pd.DataFrame({
        "code": _code6(mf["ts_code"]),
        "main_net_yi": main_net / 1e4,        # 大单+特大单净(亿)= 主力
        "retail_net_yi": retail_net / 1e4,    # 小单净(亿)= 散户
    })


def _fetch_moneyflow_struct(pro, last: str) -> pd.DataFrame | None:
    """moneyflow 结构(主力/散户净额 + 主力净流入)。失败 → None(降级)。"""
    try:
        mf = _ts_call(lambda: pro.moneyflow(
            trade_date=last,
            fields="ts_code,buy_sm_amount,sell_sm_amount,buy_lg_amount,sell_lg_amount,"
                   "buy_elg_amount,sell_elg_amount,net_mf_amount"))
        out = _moneyflow_struct_cols(mf)
        out["main_inflow_yi"] = _num(mf["net_mf_amount"]) / 1e4   # 沿用原 canonical 列
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[warn] moneyflow 结构取数失败({e!r})→ 资金结构因子降级", flush=True)
        return None


def _fetch_hk_hold(pro, last: str) -> pd.DataFrame | None:
    """北向持股占比(hk_hold;ratio = 占流通股比 %)。失败 → None(降级)。"""
    try:
        hk = _ts_call(lambda: pro.hk_hold(trade_date=last, fields="ts_code,ratio"))
        return pd.DataFrame({"code": _code6(hk["ts_code"]), "hk_ratio": _num(hk["ratio"])})
    except Exception as e:  # noqa: BLE001
        print(f"[warn] hk_hold 取数失败({e!r})→ 北向因子降级", flush=True)
        return None


# ───────────────────────── L0 universe(tushare) ─────────────────────────

_RAW_COUNT: dict = {}   # 全A(硬门前)原始数;放本模块(单次 import)避开 __main__/screen_market 双模块陷阱


def fetch_universe_tushare(
    analysis_date: str,
    cap_floor_yi: float = 30.0,
    include_bj: bool = False,
    with_factors: bool = True,
) -> pd.DataFrame:
    """tushare 版 L0:daily_basic + daily(×3) + moneyflow + yjbb(基本面) → canonical df。

    单位换算:total_mv/net_mf_amount 为**万元**(/1e4→亿);daily.amount 为**千元**
    (/1e5→亿)。动量用原始收盘价 60日/YTD 涨跌(高召回粗筛代理,除权噪声留 L3b 核)。
    """
    pro = _pro()
    last, d60, dys = resolve_momentum_dates(pro, analysis_date)
    print(f"[L0·tushare] as-of 交易日={last}  60日前={d60}  年初={dys}", flush=True)

    # 每日指标:市值/PE/PB/量比/换手/股息率
    db = _ts_call(lambda: pro.daily_basic(
        trade_date=last,
        fields="ts_code,close,turnover_rate,volume_ratio,pe_ttm,pb,dv_ratio,total_mv,circ_mv,total_share",
    ))
    # 日线:价/涨跌/成交额(+ 60日前、年初 收盘价算动量)
    dl = _ts_call(lambda: pro.daily(trade_date=last, fields="ts_code,close,pct_chg,amount"))
    dl60 = _ts_call(lambda: pro.daily(trade_date=d60, fields="ts_code,close"))
    dlys = _ts_call(lambda: pro.daily(trade_date=dys, fields="ts_code,close"))
    # 名称 + 上市日(剔次新)
    sb = _ts_call(lambda: pro.stock_basic(list_status="L", fields="ts_code,name,list_date"))

    df = pd.DataFrame(
        {
            "code": _code6(db["ts_code"]),
            "close": _num(db["close"]),
            "turnover": _num(db["turnover_rate"]),
            "vol_ratio": _num(db["volume_ratio"]),
            "pe": _num(db["pe_ttm"]),
            "pb": _num(db["pb"]),
            "dv_ratio": _num(db["dv_ratio"]),
            "mktcap_yi": _num(db["total_mv"]) / 1e4,   # 万元 → 亿元
        }
    )
    # 日线价/涨跌/成交额
    dl_ = pd.DataFrame(
        {
            "code": _code6(dl["ts_code"]),
            "pct_1d": _num(dl["pct_chg"]),
            "amount_yi": _num(dl["amount"]) / 1e5,     # 千元 → 亿元
            "close_now": _num(dl["close"]),
        }
    )
    df = df.merge(dl_, on="code", how="left")
    df = df.merge(pd.DataFrame({"code": _code6(dl60["ts_code"]), "c60": _num(dl60["close"])}), on="code", how="left")
    df = df.merge(pd.DataFrame({"code": _code6(dlys["ts_code"]), "cys": _num(dlys["close"])}), on="code", how="left")
    df["pct_60d"] = (df["close_now"] / df["c60"] - 1) * 100
    df["pct_ytd"] = (df["close_now"] / df["cys"] - 1) * 100
    # 资金结构(主力净流入 + 大单+特大单净 + 散户净;主力净占比 = 主力净额/成交额)
    mfs = _fetch_moneyflow_struct(pro, last)
    if mfs is not None:
        df = df.merge(mfs, on="code", how="left")
        df["main_net_ratio"] = df["main_net_yi"] / df["amount_yi"].replace(0, np.nan)
    else:
        for c in ("main_inflow_yi", "main_net_yi", "retail_net_yi", "main_net_ratio"):
            df[c] = np.nan
    # 名称 + 上市日
    sb_ = pd.DataFrame({"code": _code6(sb["ts_code"]), "name": sb["name"].astype(str), "list_date": sb["list_date"].astype(str)})
    df = df.merge(sb_, on="code", how="left")

    # 增强因子(可选):技术/筹码 + 北向 + 现价相对筹码成本
    if with_factors:
        fac = _fetch_factors(pro, last)
        if fac is not None:
            df = df.merge(fac, on="code", how="left")
        hk = _fetch_hk_hold(pro, last)
        if hk is not None:
            df = df.merge(hk, on="code", how="left")
        if "cost_50pct" in df.columns:
            df["price_to_cost"] = df["close"] / df["cost_50pct"]   # >1 浮盈、<1 套牢

    # 基本面(yjbb,东财 datacenter)
    fin = fetch_fundamentals_yjbb(analysis_date)
    df = df.merge(fin, on="code", how="left")
    df["industry"] = df["industry"].fillna("未分类").replace("", "未分类")

    # 剔次新:上市不足 60 交易日(list_date > d60);缺失上市日 → 保留(NaN>x=False)
    before = len(df)
    ld = pd.to_numeric(df["list_date"], errors="coerce")   # 'YYYYMMDD'→int;缺/“nan”→NaN
    df = df[~(ld > int(d60))].reset_index(drop=True)
    if before - len(df):
        print(f"[L0·tushare] 剔次新(上市>{d60}): -{before - len(df)}", flush=True)

    _RAW_COUNT["n"] = len(df)   # 全A(剔次新后、硬门前)→ 供漏斗显示 全A → 硬门
    # 复用东财路径同一套硬门(ST/市值地板/停牌代理/北交所)
    return _apply_universe_gates(df, cap_floor_yi=cap_floor_yi, include_bj=include_bj)


# ───────────────────────── 离线自测(无网络) ─────────────────────────


def _selftest_struct() -> int:
    """验 moneyflow 结构纯函数(主力/散户净额单位)。无网络。"""
    mf = pd.DataFrame({
        "ts_code": ["600000.SH"], "buy_lg_amount": [5000.0], "buy_elg_amount": [3000.0],
        "sell_lg_amount": [2000.0], "sell_elg_amount": [1000.0],
        "buy_sm_amount": [800.0], "sell_sm_amount": [1500.0],
    })
    s = _moneyflow_struct_cols(mf).iloc[0]
    fails = []
    if abs(s["main_net_yi"] - 0.5) > 1e-9:        # (5000+3000-2000-1000)万 = 5000万 = 0.5亿
        fails.append(f"main_net_yi={s['main_net_yi']}")
    if abs(s["retail_net_yi"] - (-0.07)) > 1e-9:  # (800-1500)万 = -700万 = -0.07亿
        fails.append(f"retail_net_yi={s['retail_net_yi']}")
    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print(" -", f)
        return 1
    print("SELFTEST ✅  moneyflow 结构(主力净/散户净 单位亿)正确")
    return 0


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        raise SystemExit(_selftest_struct())

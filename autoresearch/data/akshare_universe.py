#!/usr/bin/env python3
"""akshare universe 取数(DATA 层)—— 东财 push2 路径的全 A 快照 + 硬门 + 防御取列。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §B(数据层)。

从 `scripts/screen_market.py` 搬入的 **I/O / 网络 / 防御** helper(非纯打分,故归 data 层,
不归 common.scoring):
  * `_ak_call`   —— akshare bulk 调用重试(东财 push2 偶发限流/断连)。
  * `_col`       —— 按候选顺序解析列名,吸收 akshare 版本漂移。
  * `_apply_universe_gates` / `_GATE_INFO` —— 硬门(ST/退市/停牌/次新/市值地板/北交所)。
  * `fetch_universe` —— em(东财)路径的 L0:spot + yjbb(当期&上期)+ fundflow → canonical 列。

打分原语(`_num` / 报告期推算)仍走 `autoresearch.common.scoring`;`autoresearch.data.tushare_source`
从本模块复用 `_ak_call / _col / _apply_universe_gates`(同 data 层,无 scripts/ 桥)。
"""
from __future__ import annotations

import sys
import time

import numpy as np
import pandas as pd

from autoresearch.common.scoring import _num, latest_reported_quarter, prev_quarter

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


# ───────────────────────── L0 universe(em / 东财 push2) ─────────────────────────

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

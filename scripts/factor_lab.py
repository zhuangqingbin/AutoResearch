#!/usr/bin/env python3
"""factor_lab —— scan-market L1 打分逻辑的**点对点实证验证**(tushare 全市场历史)。

design: docs/specs/2026-06-20-scan-market-design.md(§4 打分)的实证回路。

动机:screen_market 的四透镜权重是**作者先验**(无回测)。本工具把"分数 → 未来收益"
的关系量化出来:对一组历史成型日 D,算全市场横截面因子值,join D 之后的前瞻收益,
计算 **rank IC**(因子与未来收益的 Spearman 相关)、IC-IR、t 值、十分位多空价差,从而
回答:**哪些因子真带预测力、哪些是噪声/反向、新加的 tushare 因子(多头排列/RSI/筹码/
股息)有没有用?** 据此迭代权重。

只验**快因子**(价/量/技术/筹码/资金/估值乘数)——这些 tushare 全市场历史可得,且
正是驱动 T+1 的因子。慢的季度基本面(成长/价值的 ROE)不驱动 T+1,留长周期另验。

铁律(避免自欺):
  * **无前视**:D 收盘算信号 → **D+1 开盘买入**(非 D 收盘),前瞻收益从 D+1 开盘起算。
  * **A股可交易性**:剔除 D+1 一字涨停(买不到)——否则动量 IC 虚高。
  * **缓存**:每个(endpoint, date)落 pickle;拉一次,之后离线迭代打分逻辑零成本。

用法:
  uv run --no-sync python scripts/factor_lab.py harvest [--step 4] [--form-span 90]
  uv run --no-sync python scripts/factor_lab.py eval
  uv run --no-sync python scripts/factor_lab.py --selftest      # 离线验 IC/十分位数学
  uv run --no-sync python scripts/factor_lab.py harvest --dry   # 只打印计划(日期/调用数)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# 复用 screen_market 的打分原语 + 真·动量透镜(验证"出厂逻辑"本身)
from screen_market import _factor_groups, _pct, _wsum, lens_momentum
from autoresearch.common.sw_sector_map import super_sector
from autoresearch.data.tushare_source import _moneyflow_struct_cols

CACHE = Path("context/factor_lab/cache")
OUT = Path("context/factor_lab")


# ───────────────────────── tushare 句柄 / 缓存 ─────────────────────────


def _pro():
    from autoresearch.data.tushare_source import _pro as src_pro

    return src_pro()


def _ts_call(fn, tries: int = 5, backoff: float = 2.0):
    from autoresearch.data.tushare_source import _ts_call as src_call

    return src_call(fn, tries=tries, backoff=backoff)


def _cache(endpoint: str, day: str, fetch_fn) -> pd.DataFrame:
    """(endpoint, day) → pickle 缓存;命中即读,否则拉取 + 落盘。空结果也缓存(避免重拉)。"""
    fp = CACHE / endpoint / f"{day}.pkl"
    if fp.exists():
        return pd.read_pickle(fp)
    fp.parent.mkdir(parents=True, exist_ok=True)
    df = _ts_call(fetch_fn)
    if df is None:
        df = pd.DataFrame()
    df.to_pickle(fp)
    time.sleep(0.35)  # 礼貌限频
    return df


def _code6(s: pd.Series) -> pd.Series:
    return s.astype(str).str.split(".").str[0].str.zfill(6)


def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


# ───────────────────────── 取数 endpoints ─────────────────────────

_FIELDS = {
    "daily": "ts_code,trade_date,open,high,low,close,pct_chg,amount",
    "daily_basic": "ts_code,close,turnover_rate,volume_ratio,pe_ttm,pb,dv_ratio,total_mv,circ_mv",
    "stk_factor_pro": "ts_code,close,ma_qfq_5,ma_qfq_10,ma_qfq_20,ma_qfq_60,rsi_qfq_6,rsi_qfq_12,macd_qfq",
    "cyq_perf": "ts_code,winner_rate,cost_15pct,cost_50pct,cost_85pct,weight_avg",
    "moneyflow": ("ts_code,buy_sm_amount,sell_sm_amount,buy_lg_amount,sell_lg_amount,"
                  "buy_elg_amount,sell_elg_amount,net_mf_amount"),
    "hk_hold": "ts_code,ratio",
    # UZI 增量(实测本机 tushare 可达):融资融券 / 大宗交易 / 龙虎榜机构席位
    "margin_detail": "ts_code,rzye,rqye,rzmre,rzche,rzrqye",
    "block_trade": "ts_code,price,vol,amount",
    "top_inst": "ts_code,exalter,buy,sell,net_buy",
}


def _fetch(pro, endpoint: str, day: str):
    f = _FIELDS[endpoint]
    return lambda: getattr(pro, endpoint)(trade_date=day, fields=f)


def _stock_basic(pro) -> pd.DataFrame:
    def fn():
        return pro.stock_basic(list_status="L", fields="ts_code,name,list_date,market,industry")

    df = _cache("stock_basic", "static", fn)
    return pd.DataFrame({
        "code": _code6(df["ts_code"]),
        "name": df["name"].astype(str),
        "list_date": df["list_date"].astype(str),
        "market": df.get("market", pd.Series(["主板"] * len(df))).astype(str),
        "industry": df.get("industry", pd.Series(["未分类"] * len(df))).astype(str),
    })


# ───────────────────────── 日期规划 ─────────────────────────


def plan_dates(pro, end_anchor: str, form_span: int, step: int, back: int, fwd: int):
    """返回(成型日 F, 价格面板日 P)。

    F = 在 [anchor 往前 form_span 个交易日, anchor 往前 fwd 个交易日] 内每 step 取一个。
    P = [min(F) 往前 back 个交易日, max(F) 往后 fwd 个交易日] 的全部交易日(供动量回看 + 前瞻收益)。
    """
    from autoresearch.data.tushare_source import _trade_days

    yyyymmdd = end_anchor.replace("-", "")
    year = int(yyyymmdd[:4])
    days = _trade_days(pro, f"{year - 2}0101", yyyymmdd)  # 足够长的日历
    if len(days) < back + form_span + fwd + 5:
        raise RuntimeError("交易日历不足")
    last_i = len(days) - 1
    # 成型日:留出 fwd 做前瞻;往前数 form_span 个
    hi = last_i - fwd
    lo = hi - form_span
    F = [days[i] for i in range(lo, hi + 1, step)]
    pmin = max(0, lo - back)
    pmax = min(last_i, hi + fwd)
    P = days[pmin:pmax + 1]
    return F, P


# ───────────────────────── harvest ─────────────────────────


def harvest(end_anchor: str, form_span: int, step: int, back: int, fwd: int, dry: bool) -> None:
    pro = _pro()
    F, P = plan_dates(pro, end_anchor, form_span, step, back, fwd)
    n_calls = len(P) + len(F) * 8 + 1  # daily×P + 8 因子端点×F(含 UZI margin/block/top_inst)+ stock_basic
    print(f"[plan] 成型日 F={len(F)} 个({F[0]}→{F[-1]}, 每 {step} 交易日)")
    print(f"[plan] 价格面板 P={len(P)} 个({P[0]}→{P[-1]})")
    print(f"[plan] 预计 tushare 调用 ≈ {n_calls}(已缓存的跳过)")
    if dry:
        return
    _stock_basic(pro)
    # 价格面板(每个交易日一次 daily)
    for i, d in enumerate(P, 1):
        _cache("daily", d, _fetch(pro, "daily", d))
        if i % 20 == 0 or i == len(P):
            print(f"[daily] {i}/{len(P)}  ({d})", flush=True)
    # 成型日因子快照
    for i, d in enumerate(F, 1):
        for ep in ("daily_basic", "stk_factor_pro", "cyq_perf", "moneyflow", "hk_hold",
                   "margin_detail", "block_trade", "top_inst"):
            _cache(ep, d, _fetch(pro, ep, d))
        print(f"[factors] {i}/{len(F)}  ({d})", flush=True)
    (OUT).mkdir(parents=True, exist_ok=True)
    pd.Series({"F": F, "P": P, "end_anchor": end_anchor, "step": step,
               "form_span": form_span, "back": back, "fwd": fwd}).to_pickle(OUT / "plan.pkl")
    print(f"[done] harvest 完成 → {CACHE}/  (plan → {OUT}/plan.pkl)")


# ───────────────────────── 价格面板 → pivots ─────────────────────────


def load_price_pivots(P: list[str]) -> dict[str, pd.DataFrame]:
    """把缓存的 daily(逐日)拼成 {字段: pivot[code × date]}。"""
    frames = []
    for d in P:
        fp = CACHE / "daily" / f"{d}.pkl"
        if not fp.exists():
            continue
        df = pd.read_pickle(fp)
        if df.empty:
            continue
        df = pd.DataFrame({
            "code": _code6(df["ts_code"]),
            "date": d,
            "open": _num(df["open"]), "high": _num(df["high"]),
            "low": _num(df["low"]), "close": _num(df["close"]),
            "pct_chg": _num(df["pct_chg"]), "amount": _num(df["amount"]),
        })
        frames.append(df)
    long = pd.concat(frames, ignore_index=True)
    piv = {f: long.pivot_table(index="code", columns="date", values=f) for f in
           ("open", "high", "low", "close", "pct_chg", "amount")}
    return piv


def _board_limit(code: str) -> float:
    """涨跌停幅度(%):科创(688)/创业板(30)=20;北交所(8/4/920)=30;其余主板=10。"""
    if code.startswith("688") or code.startswith("30"):
        return 20.0
    if code.startswith(("8", "4", "920")):
        return 30.0
    return 10.0


def forward_returns(piv: dict, P: list[str], D: str, fwd: int) -> pd.DataFrame:
    """D 的前瞻收益(D+1 开盘进):cc=收盘到收盘;oo=次日开到再次日开;oc/ocN=开盘到第N日收盘。

    并标 D+1 一字涨停(open==close==high 且涨幅近板)= 买不到 → unbuyable。
    """
    idx = P.index(D)
    c, o, h = piv["close"], piv["open"], piv["high"]
    pc = piv["pct_chg"]
    codes = c.index
    res = pd.DataFrame(index=codes)
    cD = c[D]

    def col(piv_, k):
        j = idx + k
        return piv_[P[j]] if 0 <= j < len(P) else pd.Series(np.nan, index=codes)

    o1 = col(o, 1)
    res["fwd_1_cc"] = col(c, 1) / cD - 1.0
    res["fwd_1_oo"] = col(o, 2) / o1 - 1.0
    res["fwd_5_oc"] = col(c, 5) / o1 - 1.0
    res["fwd_10_oc"] = col(c, min(10, fwd)) / o1 - 1.0
    # D+1 一字涨停(开=收=高,且涨幅≥板*0.98)→ 买不到
    pc1, o1h, c1, h1 = col(pc, 1), o1, col(c, 1), col(h, 1)
    lim = pd.Series([_board_limit(x) for x in codes], index=codes)
    sealed = (pc1 >= lim * 0.98) & (c1 >= h1 - 1e-6) & (o1h >= h1 - 1e-6)
    res["buyable"] = ~sealed.fillna(False)
    return res


# ───────────────────────── 因子帧(每个成型日) ─────────────────────────


def factor_frame(D: str, piv: dict, P: list[str], basic: pd.DataFrame,
                 cap_floor: float, fwd: int) -> pd.DataFrame | None:
    """组装 D 的横截面:canonical 因子 + tushare 增强 + 真·动量透镜分 + 前瞻收益 + 门。"""
    fp_db = CACHE / "daily_basic" / f"{D}.pkl"
    if not fp_db.exists():
        return None
    db = pd.read_pickle(fp_db)
    if db.empty:
        return None
    f = pd.DataFrame({
        "code": _code6(db["ts_code"]),
        "close": _num(db["close"]),
        "turnover": _num(db["turnover_rate"]),
        "vol_ratio": _num(db["volume_ratio"]),
        "pe": _num(db["pe_ttm"]),
        "pb": _num(db["pb"]),
        "dv_ratio": _num(db["dv_ratio"]),
        "mktcap_yi": _num(db["total_mv"]) / 1e4,
        "circ_mv": _num(db["circ_mv"]),                   # 流通市值(万元),UZI 融资/大宗因子去规模用
    })
    # 技术因子
    sf = pd.read_pickle(CACHE / "stk_factor_pro" / f"{D}.pkl")
    if not sf.empty:
        cc = _num(sf["close"])
        m5, m10, m20, m60 = (_num(sf[f"ma_qfq_{n}"]) for n in (5, 10, 20, 60))
        tech = pd.DataFrame({
            "code": _code6(sf["ts_code"]),
            "ma_bull": ((m5 > m10) & (m10 > m20) & (m20 > m60)).astype(float),
            "above_ma60": (cc > m60).astype(float),
            "rsi6": _num(sf["rsi_qfq_6"]),
            "rsi12": _num(sf["rsi_qfq_12"]),
            "macd": _num(sf["macd_qfq"]),
        })
        f = f.merge(tech, on="code", how="left")
    # 筹码
    cy = pd.read_pickle(CACHE / "cyq_perf" / f"{D}.pkl")
    if not cy.empty:
        c50 = _num(cy["cost_50pct"])
        chip = pd.DataFrame({
            "code": _code6(cy["ts_code"]),
            "winner_rate": _num(cy["winner_rate"]),
            "cost_50pct": c50,
            "chip_concentration": (_num(cy["cost_85pct"]) - _num(cy["cost_15pct"])) / c50,  # 越小越集中
        })
        f = f.merge(chip, on="code", how="left")
        f["cost_premium"] = f["close"] / f["cost_50pct"] - 1.0  # 现价相对筹码均成本溢价
        f["price_to_cost"] = f["close"] / f["cost_50pct"]       # >1 浮盈 / <1 套牢
    # 资金
    mf = pd.read_pickle(CACHE / "moneyflow" / f"{D}.pkl")
    if not mf.empty:
        flow = _moneyflow_struct_cols(mf)                      # main_net_yi / retail_net_yi(亿)
        flow["main_inflow_yi"] = _num(mf["net_mf_amount"]) / 1e4
        f = f.merge(flow, on="code", how="left")
    else:
        for c in ("main_net_yi", "retail_net_yi", "main_inflow_yi"):
            f[c] = np.nan
    amt_yi = piv["amount"][D].reindex(f["code"]).to_numpy() / 1e5   # 千元 → 亿
    f["main_net_ratio"] = f["main_net_yi"] / pd.Series(np.where(amt_yi > 0, amt_yi, np.nan), index=f.index)
    f["inflow_to_cap"] = f["main_inflow_yi"] / f["mktcap_yi"]  # 流入/市值(去规模)
    # 北向持股占比
    hkfp = CACHE / "hk_hold" / f"{D}.pkl"
    if hkfp.exists():
        hk = pd.read_pickle(hkfp)
        if not hk.empty:
            f = f.merge(pd.DataFrame({"code": _code6(hk["ts_code"]), "hk_ratio": _num(hk["ratio"])}),
                        on="code", how="left")

    # ── UZI 增量因子:融资融券(高覆盖)/ 大宗交易 / 龙虎榜机构席位(稀疏事件;方向由 IC 定)──
    def _uzi_load(ep: str) -> pd.DataFrame:
        fp = CACHE / ep / f"{D}.pkl"
        return pd.read_pickle(fp) if fp.exists() else pd.DataFrame()

    amt_yuan = piv["amount"][D].reindex(f["code"]).to_numpy() * 1e3      # 千元 → 元
    amt_pos = np.where(amt_yuan > 0, amt_yuan, np.nan)
    circ_pos = (f["circ_mv"] * 1e4).replace(0, np.nan)                   # 万元 → 元
    mg = _uzi_load("margin_detail")
    if not mg.empty:
        mg2 = pd.DataFrame({"code": _code6(mg["ts_code"]),
                            "rzye": _num(mg["rzye"]), "rzmre": _num(mg["rzmre"])})
        f = f.merge(mg2, on="code", how="left")
        f["rz_ratio"] = f["rzye"] / circ_pos                            # 融资余额 / 流通市值
        f["rz_buy_intensity"] = f["rzmre"] / amt_pos                    # 融资买入 / 当日成交额
    blk = _uzi_load("block_trade")
    if not blk.empty:
        b = blk.assign(code=_code6(blk["ts_code"]))
        g = b.groupby("code").agg(blk_amt=("amount", "sum"), blk_px=("price", "mean")).reset_index()
        f = f.merge(g, on="code", how="left")
        f["block_premium"] = f["blk_px"] / f["close"] - 1.0            # 大宗均价/收盘 − 1(折溢价)
        f["block_intensity"] = f["blk_amt"] / f["circ_mv"]            # 大宗额 / 流通市值(同万元)
    ti = _uzi_load("top_inst")
    if not ti.empty:
        t = ti.assign(code=_code6(ti["ts_code"]))
        inst = t[t["exalter"].astype(str).str.contains("机构专用", na=False)]
        if len(inst):
            gi = inst.groupby("code")["net_buy"].sum().reset_index()
            gi.columns = ["code", "inst_net"]
            f = f.merge(gi, on="code", how="left")
            f["lhb_inst_net"] = _num(f["inst_net"]) / amt_pos          # 龙虎榜机构净买 / 当日成交额

    # 动量(从价格面板算 pct_60d / pct_ytd / 短动量)
    close_piv = piv["close"]
    idx = P.index(D)
    cD = close_piv[D].reindex(f["code"]).values
    def lag_ret(k):
        j = idx - k
        if j < 0:
            return np.full(len(f), np.nan)
        return cD / close_piv[P[j]].reindex(f["code"]).values - 1.0
    f["pct_5d"] = lag_ret(5) * 100
    f["pct_20d"] = lag_ret(20) * 100
    f["pct_60d"] = lag_ret(60) * 100
    # YTD:年内首个面板日
    ys = next((d for d in P[:idx + 1] if d[:4] == D[:4]), P[0])
    f["pct_ytd"] = (cD / close_piv[ys].reindex(f["code"]).values - 1.0) * 100

    # 名称/板块/次新/ST
    f = f.merge(basic, on="code", how="left")
    f["is_st"] = f["name"].fillna("").str.contains("ST", case=False) | f["name"].fillna("").str.contains("退")

    # 硬门(与 screen_market 一致):非 ST、市值地板、非次新、剔北交所、有量
    amtD = piv["amount"][D].reindex(f["code"]).values
    keep = (~f["is_st"]) & (f["mktcap_yi"] >= cap_floor) & (np.nan_to_num(amtD) > 0)
    keep &= ~f["code"].str.match(r"^(8|4|920)")
    ld = pd.to_numeric(f["list_date"], errors="coerce")
    d60 = P[max(0, idx - 60)]
    keep &= ~(ld > int(d60))
    f = f[keep].reset_index(drop=True)
    if len(f) < 300:
        return None

    # 真·动量透镜分(出厂 lens_momentum:需要 main_inflow_yi/vol_ratio/turnover/pct_*/ma_bull/above_ma60/rsi6)
    try:
        lm = lens_momentum(f)
        f["momentum_score"] = lm["momentum_score"]
        f["momentum_gate"] = lm["momentum_gate"]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] lens_momentum({D}) 失败: {e!r}", file=sys.stderr)

    # 动量变体(实证迭代:剔反向的 vol_ratio 量能项 / 试去规模资金 / RS 加重)
    if {"above_ma60", "ma_bull"} <= set(f.columns):
        rs = 0.6 * _pct(f["pct_60d"]) + 0.4 * _pct(f["pct_ytd"])
        trend = 0.5 * _num(f["above_ma60"]).fillna(0.0) + 0.5 * _num(f["ma_bull"]).fillna(0.0)
        overheat = _pct(f["pct_60d"]) > 0.95
        if "rsi6" in f.columns:
            overheat = overheat | (_num(f["rsi6"]) > 85)

        def _pen(s):
            return (s - overheat.astype(float) * 15).clip(lower=0)

        inflow = _pct(f["main_inflow_yi"])
        f["mom_v2_noVol"] = _pen(_wsum({"rs": (rs, 40), "inflow": (inflow, 30), "trend": (trend, 30)}))
        f["mom_v3_capnorm"] = _pen(_wsum({"rs": (rs, 40), "inflow": (_pct(f["inflow_to_cap"]), 30), "trend": (trend, 30)}))
        f["mom_v4_rsHeavy"] = _pen(_wsum({"rs": (rs, 50), "trend": (trend, 30), "inflow": (inflow, 20)}))

    # 多日量价序列因子(OBV/CMF/VWAP偏离/量价突破):从价格面板算,补单日 vol_ratio 分不清的资金流方向
    import autoresearch.common.vol_series as vol_series
    win = P[max(0, idx - 19):idx + 1]
    if len(win) >= 10:
        H, L, C, A = piv["high"], piv["low"], piv["close"], piv["amount"]
        f["cmf_20"] = vol_series.cmf(H, L, C, A, win).reindex(f["code"]).to_numpy()
        f["obv_mom_20"] = vol_series.obv_momentum(C, A, win).reindex(f["code"]).to_numpy()
        f["price_vs_vwap_20"] = vol_series.price_vs_vwap(H, L, C, A, win).reindex(f["code"]).to_numpy()
        f["breakout_vol_20"] = vol_series.breakout_on_volume(C, A, win).reindex(f["code"]).to_numpy()

    # 前瞻收益
    fr = forward_returns(piv, P, D, fwd)
    f = f.merge(fr, left_on="code", right_index=True, how="left")
    f["date"] = D
    return f


# ───────────────────────── IC / 十分位 ─────────────────────────

# 候选因子:(列, 方向)  方向 +1 = 值越大越看多;-1 = 值越小越看多(IC 已按方向取符号)
CANDIDATES = [
    ("pct_5d", -1),    # 短期反转?
    ("pct_20d", +1), ("pct_60d", +1), ("pct_ytd", +1),
    ("main_inflow_yi", +1), ("inflow_to_cap", +1),
    ("ma_bull", +1), ("above_ma60", +1), ("rsi6", +1), ("macd", +1),
    ("vol_ratio", +1), ("turnover", +1),
    ("winner_rate", -1), ("cost_premium", -1),   # 筹码:套牢/低获利 = 反转看多(出厂用法)
    ("pe", -1), ("pb", -1), ("dv_ratio", +1),
    ("momentum_score", +1),
    ("mom_v2_noVol", +1), ("mom_v3_capnorm", +1), ("mom_v4_rsHeavy", +1),
    # v2 新富因子(符号先验,真权重由 calibrate 的 IC 符号定)
    ("main_net_ratio", +1), ("retail_net_yi", -1), ("chip_concentration", -1),
    ("price_to_cost", -1), ("rsi12", -1), ("hk_ratio", +1),
    # UZI 增量候选(方向先验;真符号/去留由 IC 定):融资融券(高覆盖)/ 大宗 / 龙虎榜机构(稀疏)
    ("rz_ratio", +1), ("rz_buy_intensity", +1),
    ("block_premium", -1), ("block_intensity", +1), ("lhb_inst_net", +1),
    # 多日量价序列(OBV/CMF/VWAP偏离/量价突破;符号先验,真符号/去留由 IC 定)——补单日 vol_ratio(已剔)
    ("cmf_20", +1), ("obv_mom_20", +1), ("price_vs_vwap_20", -1), ("breakout_vol_20", +1),
]
FWDS = ["fwd_1_cc", "fwd_1_oo", "fwd_5_oc", "fwd_10_oc"]


def _spearman(a: pd.Series, b: pd.Series) -> float:
    m = a.notna() & b.notna()
    if m.sum() < 30:
        return np.nan
    return a[m].rank().corr(b[m].rank())


def _shrink_weights(ic_ind: float, n_ind: int, ic_parent: float, ic_global: float, k: float = 200.0) -> float:
    """两级经验贝叶斯收缩:行业 IC →(大类)parent IC → 全市场 IC。

    λ = n/(n+k):样本足 → 贴行业自身;样本少 → 拉向 parent(parent 再以 0.5/0.5 向 global 收缩)。
    返回收缩后的 IC,作为该(行业, 因子/组)的权重基。
    """
    lam1 = n_ind / (n_ind + k)
    parent = 0.5 * ic_parent + 0.5 * ic_global
    return lam1 * ic_ind + (1 - lam1) * parent


def evaluate(cap_floor: float, buyable_only: bool) -> None:
    plan = pd.read_pickle(OUT / "plan.pkl")
    F, P, fwd = plan["F"], plan["P"], plan["fwd"]
    basic = _load_basic()
    piv = load_price_pivots(P)
    frames = []
    for D in F:
        fr = factor_frame(D, piv, P, basic, cap_floor, fwd)
        if fr is not None:
            frames.append(fr)
    if not frames:
        print("无可用成型日(先 harvest)")
        return
    print(f"[eval] 成型日 {len(frames)}/{len(F)} 可用,横截面均值 ~{int(np.mean([len(x) for x in frames]))} 只/日")

    # 每日每因子 IC,再跨日聚合
    rows = []
    for col, sign in CANDIDATES:
        rec = {"factor": col, "sign": sign}
        for fwdcol in FWDS:
            ics = []
            for fr in frames:
                sub = fr if not buyable_only else fr[fr["buyable"].fillna(True)]
                if col not in sub or fwdcol not in sub:
                    continue
                ic = _spearman(sub[col] * sign, sub[fwdcol])
                if not np.isnan(ic):
                    ics.append(ic)
            ics = np.array(ics)
            if len(ics):
                rec[f"IC_{fwdcol}"] = round(ics.mean(), 4)
                rec[f"ICIR_{fwdcol}"] = round(ics.mean() / (ics.std() + 1e-9), 3)
                if fwdcol == "fwd_1_cc":
                    rec["t"] = round(ics.mean() / (ics.std() + 1e-9) * np.sqrt(len(ics)), 2)
                    rec["hit"] = round((ics > 0).mean(), 2)
                    h = len(ics) // 2  # 前半 vs 后半:regime 稳定性(同号=稳健,反号=可能过拟合)
                    rec["IC_h1"] = round(ics[:h].mean(), 4)
                    rec["IC_h2"] = round(ics[h:].mean(), 4)
                    rec["n_days"] = len(ics)
        rows.append(rec)
    ic_tbl = pd.DataFrame(rows)

    # 十分位多空价差(T+1 cc,买得到的)
    dec_rows = []
    for col, sign in CANDIDATES:
        d1, d10, spreads = [], [], []
        for fr in frames:
            sub = fr if not buyable_only else fr[fr["buyable"].fillna(True)]
            s = (sub[col] * sign)
            r = sub["fwd_1_cc"].clip(-0.21, 0.21)
            m = s.notna() & r.notna()
            if m.sum() < 100:
                continue
            q = pd.qcut(s[m].rank(method="first"), 10, labels=False)
            top = r[m][q == 9].mean()
            bot = r[m][q == 0].mean()
            d1.append(top)
            d10.append(bot)
            spreads.append(top - bot)
        if spreads:
            dec_rows.append({"factor": col, "top_decile_ret": round(np.mean(d1) * 100, 3),
                             "bot_decile_ret": round(np.mean(d10) * 100, 3),
                             "LS_spread_bps": round(np.mean(spreads) * 1e4, 1),
                             "spread_t": round(np.mean(spreads) / (np.std(spreads) + 1e-9) * np.sqrt(len(spreads)), 2)})
    dec_tbl = pd.DataFrame(dec_rows)

    OUT.mkdir(parents=True, exist_ok=True)
    ic_tbl.to_csv(OUT / "ic_table.csv", index=False)
    dec_tbl.to_csv(OUT / "decile_table.csv", index=False)

    # 排序打印(按 T+1 cc 的 ICIR 降序;缺列的因子排末尾)
    sortcol = "ICIR_fwd_1_cc"
    if sortcol not in ic_tbl:
        ic_tbl[sortcol] = np.nan
    show = ic_tbl.sort_values(sortcol, ascending=False, na_position="last")
    pd.set_option("display.width", 200, "display.max_columns", 30)
    print("\n================ rank IC(因子已按 sign 取向;正=看多有效)================")
    cols = ["factor", "IC_fwd_1_cc", "ICIR_fwd_1_cc", "t", "hit", "IC_h1", "IC_h2",
            "IC_fwd_5_oc", "IC_fwd_10_oc", "n_days"]
    cols = [c for c in cols if c in show.columns]
    print(show[cols].to_string(index=False))
    print("\n================ 十分位多空价差(T+1 收到收, bps;买得到的)================")
    print(dec_tbl.sort_values("LS_spread_bps", ascending=False).to_string(index=False))
    print(f"\n[done] → {OUT}/ic_table.csv, decile_table.csv  (buyable_only={buyable_only})")


def _load_basic() -> pd.DataFrame:
    fp = CACHE / "stock_basic" / "static.pkl"
    df = pd.read_pickle(fp)
    return pd.DataFrame({
        "code": _code6(df["ts_code"]), "name": df["name"].astype(str),
        "list_date": df["list_date"].astype(str),
        "market": df.get("market", pd.Series(["主板"] * len(df))).astype(str),
        "industry": df.get("industry", pd.Series(["未分类"] * len(df))).astype(str),
    })


# ───────────────────────── calibrate(T+1 IC → 层级收缩 → weights.json) ─────────────────────────


def _nz(x) -> float:
    return 0.0 if (x is None or pd.isna(x)) else float(x)


def _all_frames(cap_floor: float) -> list[pd.DataFrame]:
    plan = pd.read_pickle(OUT / "plan.pkl")
    F, P, fwd = plan["F"], plan["P"], plan["fwd"]
    basic = _load_basic()
    piv = load_price_pivots(P)
    frames = []
    for D in F:
        fr = factor_frame(D, piv, P, basic, cap_floor, fwd)
        if fr is not None:
            frames.append(fr)
    return frames


def calibrate(cap_floor: float = 30.0, k: float = 200.0,
              out_path: str = "context/factor_lab/weights.json") -> dict:
    """每"因子组"对 T+1(开到开)的 rank-IC,按申万/东财行业 + 大类层级收缩 → weights.json。

    组定义复用 screen_market._factor_groups(校准与线上同口径)。factor_lab 无季度基本面 →
    growth 组 IC=NaN 跳过(权重 0);value 组用 pe 行业内分位可校准。权重 = signed IC(线上
    composite_score 用其符号+大小)。
    """
    import json

    from screen_market import _factor_groups

    frames = _all_frames(cap_floor)
    if not frames:
        print("无可用成型日(先 harvest)")
        return {}
    rows = []
    for fr in frames:
        groups = _factor_groups(fr)
        g = pd.DataFrame({f"grp_{name}": v.to_numpy() for name, v in groups.items()})
        g["industry"] = fr["industry"].to_numpy()
        g["sector"] = g["industry"].map(super_sector)
        g["fwd"] = fr["fwd_1_oo"].to_numpy()
        g["buyable"] = fr["buyable"].fillna(True).to_numpy()
        g["date"] = fr["date"].to_numpy()
        rows.append(g)
    panel = pd.concat(rows, ignore_index=True)
    panel = panel[panel["buyable"]]
    grp_cols = [c for c in panel.columns if c.startswith("grp_")]

    def _ic_by(df_) -> dict:
        out = {}
        for c in grp_cols:
            ics = [ic for _, dd in df_.groupby("date") if not np.isnan(ic := _spearman(dd[c], dd["fwd"]))]
            out[c] = float(np.mean(ics)) if ics else float("nan")
        return out

    ic_global = _ic_by(panel)
    ic_sector = {sec: _ic_by(gp) for sec, gp in panel.groupby("sector")}

    weights = {}
    for ind, gi in panel.groupby("industry"):
        ic_i, ic_p = _ic_by(gi), ic_sector.get(super_sector(ind), {})
        weights[ind] = {c[4:]: round(_shrink_weights(_nz(ic_i.get(c)), len(gi), _nz(ic_p.get(c)),
                                                      _nz(ic_global.get(c)), k=k), 5) for c in grp_cols}
    weights["__global__"] = {c[4:]: round(_nz(ic_global.get(c)), 5) for c in grp_cols}

    meta = {"horizon": "fwd_1_oo(T+1 开到开)", "k": k, "n_dates": int(panel["date"].nunique()),
            "n_rows": int(len(panel)), "n_industries": int(panel["industry"].nunique()),
            "ic_global": {c[4:]: round(_nz(v), 4) for c, v in ic_global.items()},
            "source": "factor_lab.calibrate"}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({"meta": meta, "weights": weights}, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"[calibrate] weights → {out_path}")
    print(f"  组×行业: {len(grp_cols)} × {len(weights) - 1};全市场组 IC(T+1 oo): {meta['ic_global']}")
    return {"meta": meta, "weights": weights}


# ───────────────────────── L2 GBDT 学习重排(LightGBM 横截面) ─────────────────────────
#
# 把 L1 线性复合分的"加权"换成 GBDT 非线性:同一批因子组 + 双侧都有的原始因子为特征,学每日
# 横截面 rank-norm 后的 T+1(开到开)收益。screen_market.run() 在 L1 召回后调 predict_scores()
# 把 top1000 重排成 top200(= L2 粗排,确定性,替代旧 L2-AI keep/cut);模型缺失 → 回落 composite top。
#
# 特征对齐:gbdt_features 在 factor_lab 帧(带前瞻收益=训练)与 screen_market L1 输出(=预测)上
# **同口径**——组分来自共享的 _factor_groups,原始因子取两侧交集。**剔 growth 组**:factor_lab 帧无
# 季度基本面 → 训练恒 NaN,与预测端有值不一致,故排除(成长不驱动 T+1,本就该长周期另验)。

GBDT_GROUPS = ["momentum", "fund_main", "fund_retail", "chip", "north", "tech", "value", "volprice"]
# 原始因子:factor_lab.factor_frame 与 screen_market L1_recall **都产出**的列(去掉只在一侧的)。
GBDT_RAW = [
    "pct_60d", "pct_ytd", "vol_ratio", "turnover",
    "winner_rate", "chip_concentration", "price_to_cost",
    "main_inflow_yi", "main_net_ratio", "retail_net_yi", "hk_ratio",
    "rsi6", "rsi12", "pe", "pb", "dv_ratio",
    "cmf_20", "obv_mom_20", "ma_bull", "above_ma60",
]
GBDT_LABEL = "fwd_1_oo"                          # T+1 开到开,与 calibrate 同口径(可交易、无前视)
GBDT_MODEL = "context/factor_lab/gbdt_model.pkl"
_GBDT_CACHE: dict = {}


def gbdt_features(df: pd.DataFrame) -> pd.DataFrame:
    """train/predict 共用特征矩阵:8 组分位分(_factor_groups,去 growth)+ 双侧都有的原始因子
    + **线性 composite 锚定特征**(GBDT 至少能复刻线性,再在其上叠非线性交互 → 不该弱于线性)。

    NaN 保留(LightGBM 原生分裂处理);列名/顺序固定 → 预测时 reindex 对齐。
    `composite` 两侧都有:训练端由 train_gbdt 注入(composite_score),预测端即 L1_recall 自带列。
    """
    groups = _factor_groups(df)                 # 9 组 0–1 横截面分位(与线上同口径);取其中 8 组
    feat = pd.DataFrame({f"g_{k}": groups[k].to_numpy() for k in GBDT_GROUPS}, index=df.index)
    for c in [*GBDT_RAW, "composite"]:
        feat[c] = _num(df[c]).to_numpy() if c in df.columns else np.nan
    return feat


def _rank_ic_by_date(score: pd.Series, fwd: pd.Series, date: pd.Series) -> float:
    """跨日平均 rank-IC(每日横截面 Spearman(score, 实现收益))。"""
    d = pd.DataFrame({"s": np.asarray(score), "f": np.asarray(fwd), "d": np.asarray(date)})
    ics = []
    for _, g in d.groupby("d"):
        ic = _spearman(g["s"], g["f"])
        if not np.isnan(ic):
            ics.append(ic)
    return float(np.mean(ics)) if ics else float("nan")


def train_gbdt(cap_floor: float = 30.0, valid_dates: int = 5, out_path: str = GBDT_MODEL) -> dict:
    """LightGBM 横截面排序模型(L2 粗排引擎)。

    标签 = 每日横截面 rank-norm 的 fwd_1_oo(学相对排序,免 regime 水平位移,Qlib CSRankNorm 思路)。
    时序留出最后 valid_dates 个成型日做 oos 验证 + 早停;打印 GBDT vs 线性 composite 的 oos rank-IC
    (**不胜线性就直说**,不自欺)。模型 + 特征名 + meta(oos IC/重要度)落 out_path。
    """
    import pickle

    import lightgbm as lgb
    from screen_market import _load_weights, composite_score

    frames = _all_frames(cap_floor)
    if len(frames) < 8:
        print(f"[gbdt] 成型日仅 {len(frames)}(<8)→ 不训(先 harvest 更多日)")
        return {}
    weights = _load_weights()
    parts = []
    for fr in frames:
        sub = fr[fr["buyable"].fillna(True)].copy()
        y = _num(sub[GBDT_LABEL])
        m = y.notna()
        if m.sum() < 100:
            continue
        sub = sub[m].reset_index(drop=True)
        sub["composite"] = composite_score(sub, weights)["composite"].to_numpy()  # 线性基线:既作 GBDT 锚定特征,又作 oos 对照
        feat = gbdt_features(sub)
        feat["__date"] = sub["date"].to_numpy()
        feat["__fwd"] = _num(sub[GBDT_LABEL]).to_numpy()
        feat["__y"] = _num(sub[GBDT_LABEL]).rank(pct=True).to_numpy()       # 每日横截面 rank-norm 标签
        feat["__lin"] = sub["composite"].to_numpy()
        parts.append(feat)
    if not parts:
        print("[gbdt] 无可用成型日(先 harvest)")
        return {}
    panel = pd.concat(parts, ignore_index=True)
    feat_cols = [c for c in panel.columns if not c.startswith("__")]
    udates = sorted(panel["__date"].unique())
    valid_dates = min(valid_dates, max(1, len(udates) // 4))
    val = set(udates[-valid_dates:])
    is_val = panel["__date"].isin(val).to_numpy()
    # 原生 lgb.train(Dataset) API——不依赖 scikit-learn,且即 Qlib LGBModel 同路。
    dtrain = lgb.Dataset(panel.loc[~is_val, feat_cols], label=panel.loc[~is_val, "__y"])
    dvalid = lgb.Dataset(panel.loc[is_val, feat_cols], label=panel.loc[is_val, "__y"], reference=dtrain)
    # 薄面板(~18 训练日)→ 偏强正则:浅树 + 大叶 + 强 L2,压过拟合(否则 oos 输线性)。
    params = {"objective": "regression", "metric": "l2", "learning_rate": 0.03,
              "num_leaves": 15, "max_depth": 5, "min_data_in_leaf": 200,
              "bagging_fraction": 0.8, "bagging_freq": 1, "feature_fraction": 0.8,
              "lambda_l2": 10.0, "lambda_l1": 1.0, "seed": 7, "num_threads": 0, "verbosity": -1}
    model = lgb.train(params, dtrain, num_boost_round=800, valid_sets=[dvalid],
                      callbacks=[lgb.early_stopping(60, verbose=False)])
    best_iter = int(model.best_iteration or 800)
    # oos rank-IC:GBDT vs 线性 composite(都对真实 fwd_1_oo)
    pred = model.predict(panel.loc[is_val, feat_cols], num_iteration=best_iter)
    d_val, f_val = panel.loc[is_val, "__date"], panel.loc[is_val, "__fwd"]
    ic_gbdt = _rank_ic_by_date(pred, f_val, d_val)
    ic_lin = _rank_ic_by_date(panel.loc[is_val, "__lin"], f_val, d_val)
    imp = sorted(zip(feat_cols, model.feature_importance(importance_type="gain"), strict=False),
                 key=lambda kv: kv[1], reverse=True)
    meta = {"n_rows": int(len(panel)), "n_dates": len(udates), "valid_dates": valid_dates,
            "oos_rank_ic_gbdt": round(ic_gbdt, 4), "oos_rank_ic_linear": round(ic_lin, 4),
            "beats_linear": bool(ic_gbdt > ic_lin), "best_iteration": best_iter,
            "label": GBDT_LABEL, "features": feat_cols,
            "top_importance": [[k, int(v)] for k, v in imp[:10]], "source": "factor_lab.train_gbdt"}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(pickle.dumps({"model": model, "features": feat_cols, "meta": meta}))
    _GBDT_CACHE.pop(out_path, None)
    verdict = "✅ GBDT 胜线性" if ic_gbdt > ic_lin else "⚠️ 未胜线性 → 建议 harvest 更多成型日,或 L2 暂留线性"
    print(f"[gbdt] oos rank-IC  GBDT {ic_gbdt:+.4f}  vs  线性 {ic_lin:+.4f}   {verdict}")
    print(f"[gbdt] rows={len(panel)} dates={len(udates)}(val={valid_dates}) best_iter={best_iter} "
          f"feats={len(feat_cols)}")
    print(f"[gbdt] top 特征: {', '.join(f'{k}={v}' for k, v in imp[:6])}")
    print(f"[gbdt] → {out_path}")
    return meta


def predict_scores(df: pd.DataFrame, model_path: str = GBDT_MODEL,
                   force: bool = False) -> pd.Series | None:
    """对带原始因子的帧打 GBDT 分(越高越看多);模型缺失/失败 → None(调用方回落线性)。

    **自保门**:若模型 meta.beats_linear=False(oos 未胜线性),默认返回 None → L2 回落线性,
    绝不部署比线性差的模型(铁律:不自欺)。harvest 更多成型日重训、一旦胜线性即自动启用;
    force=True 强制使用(实验用)。
    """
    p = Path(model_path)
    if not p.exists():
        return None
    try:
        import pickle
        if model_path not in _GBDT_CACHE:
            _GBDT_CACHE[model_path] = pickle.loads(p.read_bytes())
        bundle = _GBDT_CACHE[model_path]
        if not force and not bundle.get("meta", {}).get("beats_linear", False):
            print("[gbdt] 模型 oos 未胜线性 → L2 回落线性(harvest 更多日重训以启用)", file=sys.stderr)
            return None
        X = gbdt_features(df).reindex(columns=bundle["features"])
        n_iter = bundle.get("meta", {}).get("best_iteration")
        return pd.Series(bundle["model"].predict(X, num_iteration=n_iter), index=df.index)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] GBDT 预测失败 → 回落线性: {e}", file=sys.stderr)
        return None


# ───────────────────────── 离线自测(IC/十分位数学) ─────────────────────────


def _selftest() -> int:
    rng = np.random.default_rng(7)
    fails = []
    # 构造:因子与未来收益正相关 → IC 应显著为正
    n, days = 800, 20
    ics = []
    for _ in range(days):
        fac = rng.normal(size=n)
        ret = 0.3 * fac + rng.normal(size=n)  # 信噪 0.3
        ics.append(_spearman(pd.Series(fac), pd.Series(ret)))
    if not (0.15 < np.mean(ics) < 0.40):
        fails.append(f"已知正相关 IC 均值异常: {np.mean(ics):.3f}")
    # 纯噪声 → IC≈0
    noise = [_spearman(pd.Series(rng.normal(size=n)), pd.Series(rng.normal(size=n))) for _ in range(days)]
    if abs(np.mean(noise)) > 0.05:
        fails.append(f"纯噪声 IC 偏离 0: {np.mean(noise):.3f}")
    # 涨跌停幅度
    if _board_limit("600519") != 10 or _board_limit("688111") != 20 or _board_limit("300750") != 20:
        fails.append("涨跌停板幅度判定错")
    if fails:
        print("SELFTEST ❌")
        for x in fails:
            print("  -", x)
        return 1
    print(f"SELFTEST ✅  IC(信号){np.mean(ics):.3f} / IC(噪声){np.mean(noise):.3f} / 板幅判定 OK")
    return 0


def _selftest_shrink() -> int:
    """层级收缩:大样本贴自身 IC、小样本回落 parent、n=0 回落基准。无网络。"""
    fails = []
    w_big = _shrink_weights(0.10, 2000, 0.02, 0.0, k=200)
    w_small = _shrink_weights(0.10, 20, 0.02, 0.0, k=200)
    if not (abs(w_big - 0.10) < abs(w_small - 0.10)):
        fails.append(f"大样本应更贴自身 IC: big={w_big:.4f} small={w_small:.4f}")
    if abs(_shrink_weights(0.9, 0, 0.0, 0.0, k=200)) > 1e-9:
        fails.append("n=0 应回落基准")
    if fails:
        print("SELFTEST ❌")
        for x in fails:
            print("  -", x)
        return 1
    print(f"SELFTEST ✅  层级收缩(大样本贴自身 {w_big:.3f} / 小样本回落 {w_small:.3f})")
    return 0


def _selftest_gbdt() -> int:
    """GBDT 特征形状 + 缺模型回落 None + 合成可学信号 oos IC>0(无网络/无缓存)。"""
    import importlib.util
    fails = []
    rng = np.random.default_rng(11)
    n = 600
    df = pd.DataFrame({
        "code": [f"{600000 + i:06d}" for i in range(n)], "industry": rng.choice(["A", "B", "C"], n),
        "pct_60d": rng.normal(size=n), "pct_ytd": rng.normal(size=n), "vol_ratio": rng.uniform(0.3, 5, n),
        "turnover": rng.uniform(0.1, 30, n), "winner_rate": rng.uniform(0, 100, n),
        "chip_concentration": rng.uniform(0.1, 2, n), "price_to_cost": rng.uniform(0.7, 1.5, n),
        "main_inflow_yi": rng.normal(size=n), "main_net_ratio": rng.normal(size=n) * 0.05,
        "retail_net_yi": rng.normal(size=n), "hk_ratio": rng.uniform(0, 30, n),
        "rsi6": rng.uniform(10, 95, n), "rsi12": rng.uniform(10, 95, n), "pe": rng.uniform(-50, 200, n),
        "pb": rng.uniform(0.5, 30, n), "dv_ratio": rng.uniform(0, 6, n), "cmf_20": rng.normal(size=n) * 0.2,
        "obv_mom_20": rng.normal(size=n) * 0.3, "ma_bull": rng.integers(0, 2, n).astype(float),
        "above_ma60": rng.integers(0, 2, n).astype(float),
    })
    feat = gbdt_features(df)
    exp_cols = len(GBDT_GROUPS) + len(GBDT_RAW) + 1   # +1 = composite 锚定特征
    if feat.shape != (n, exp_cols):
        fails.append(f"gbdt_features 形状 {feat.shape} 期望 ({n},{exp_cols})")
    if predict_scores(df, model_path="context/factor_lab/__nonexistent__.pkl") is not None:
        fails.append("缺模型时 predict_scores 应回落 None")
    if importlib.util.find_spec("lightgbm"):
        import lightgbm as lgb
        sig = feat["g_momentum"].fillna(0.5).to_numpy()
        y = sig + rng.normal(scale=0.5, size=n)                  # y 与 g_momentum 正相关
        cut = int(n * 0.7)
        dtr = lgb.Dataset(feat.iloc[:cut], label=y[:cut])
        m = lgb.train({"objective": "regression", "num_leaves": 15, "min_data_in_leaf": 30,
                       "verbosity": -1, "seed": 7}, dtr, num_boost_round=80)
        ic = _spearman(pd.Series(m.predict(feat.iloc[cut:])), pd.Series(y[cut:]))
        if not (ic > 0.1):
            fails.append(f"合成信号 oos IC 偏低 {ic:.3f}")
    if fails:
        print("SELFTEST ❌")
        for x in fails:
            print("  -", x)
        return 1
    print(f"SELFTEST ✅  GBDT(特征 {feat.shape[1]} 列 / 缺模型回落 None / 合成 oos IC OK)")
    return 0


# ───────────────────────── CLI ─────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="factor_lab — scan-market 打分逻辑实证验证")
    ap.add_argument("mode", nargs="?", choices=["harvest", "eval", "calibrate", "train"],
                    help="harvest=取数缓存;eval=离线评估;calibrate=T+1 IC→weights.json;"
                         "train=LightGBM 横截面排序→gbdt_model.pkl(L2 粗排引擎)")
    ap.add_argument("--valid-dates", type=int, default=5, help="train:留作 oos 验证/早停的末尾成型日数")
    ap.add_argument("--anchor", default=None, help="结束锚定日 YYYY-MM-DD(缺省=今天)")
    ap.add_argument("--form-span", type=int, default=90, help="成型日跨度(交易日),默认 90")
    ap.add_argument("--step", type=int, default=4, help="成型日间隔(交易日),默认 4")
    ap.add_argument("--back", type=int, default=64, help="动量回看(交易日),默认 64")
    ap.add_argument("--fwd", type=int, default=10, help="前瞻收益最大跨度(交易日),默认 10")
    ap.add_argument("--cap-floor", type=float, default=30.0, help="市值地板(亿)")
    ap.add_argument("--k", type=float, default=200.0, help="收缩常数(λ=n/(n+k)),默认 200")
    ap.add_argument("--dry", action="store_true", help="harvest 只打印计划")
    ap.add_argument("--all-names", action="store_true", help="eval 不剔除 D+1 一字涨停(默认剔除)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        rc = _selftest()
        rc = _selftest_shrink() or rc
        return _selftest_gbdt() or rc
    from datetime import date as _date
    anchor = args.anchor or _date.today().isoformat()
    if args.mode == "harvest":
        harvest(anchor, args.form_span, args.step, args.back, args.fwd, args.dry)
    elif args.mode == "eval":
        evaluate(args.cap_floor, buyable_only=not args.all_names)
    elif args.mode == "calibrate":
        calibrate(args.cap_floor, k=args.k)
    elif args.mode == "train":
        train_gbdt(args.cap_floor, valid_dates=args.valid_dates)
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

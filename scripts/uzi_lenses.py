#!/usr/bin/env python3
"""UZI-Skill 增量透镜(L4 单票深研用)· tushare 优先,纯函数可自测。

Phase A 实测:UZI 的市场级数据(融资/大宗/龙虎榜机构)在 T+1 **无 L1 alpha** → 改作 L4 决策卡的
定性证据 / 估值透镜(它本就是单票深研插件的强项):
  * simple_dcf / dcf_sensitivity —— 简版两阶段 DCF + WACC×growth 敏感性(纯函数,补你缺的内在价值)。
  * trap_signals —— 杀猪盘/风险 8 信号轻量版(纯函数,复用 L1 因子行,零取数;内化 IC 校准经验)。
  * ashare_fundamentals_ts —— A股原生财报(fina_indicator 5y + dividend)补 yfinance 稀疏。
  * lhb_seats —— 龙虎榜机构 vs 游资席位识别;**Phase A 实测:机构上榜买入后续偏弱 → 标注反指**。
  * margin_trend_ts —— 近 20 日融资余额趋势(杠杆资金进出)。

用法:uv run --no-sync python scripts/uzi_lenses.py --selftest
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta


def _tscode(code: str) -> str:
    """6 位代码 → tushare ts_code(.SH/.SZ/.BJ)。"""
    c = str(code).split(".")[0].zfill(6)
    if c[:2] == "92" or c[0] in ("4", "8"):
        return f"{c}.BJ"
    return f"{c}.SH" if c[0] in ("6", "9") else f"{c}.SZ"


# ───────────────────────── 纯函数:DCF + 杀猪盘信号(可自测) ─────────────────────────


def simple_dcf(fcf_base: float, growth_5y: float, terminal_growth: float, wacc: float,
               shares: float, net_debt: float = 0.0, years: int = 5) -> dict:
    """简版两阶段 DCF:前 years 年按 growth_5y 增长,永续按 terminal_growth(Gordon)。

    返回 {ev, equity_value, per_share, pv_explicit, pv_terminal};入参非法 → {}。
    """
    if shares <= 0 or wacc <= terminal_growth or wacc <= 0:
        return {}
    pv, fcf = 0.0, float(fcf_base)
    for t in range(1, years + 1):
        fcf *= (1 + growth_5y)
        pv += fcf / (1 + wacc) ** t
    terminal = fcf * (1 + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal / (1 + wacc) ** years
    ev = pv + pv_terminal
    equity = ev - net_debt
    return {"ev": round(ev, 2), "equity_value": round(equity, 2),
            "per_share": round(equity / shares, 2),
            "pv_explicit": round(pv, 2), "pv_terminal": round(pv_terminal, 2)}


def dcf_sensitivity(fcf_base: float, shares: float, net_debt: float,
                    waccs: list[float], growths: list[float],
                    terminal_growth: float = 0.03, years: int = 5) -> dict:
    """WACC × growth 的每股内在价值敏感性矩阵。"""
    matrix = [[simple_dcf(fcf_base, g, terminal_growth, w, shares, net_debt, years).get("per_share")
               for g in growths] for w in waccs]
    return {"waccs": waccs, "growths": growths, "matrix": matrix}


def trap_signals(row: dict) -> dict:
    """杀猪盘/派发风险 8 信号轻量版 —— 纯函数,复用 L1 因子行(零取数)。

    内化 factor_lab 的 T+1 IC 校准经验(winner_rate 满=抛压、过热=回避、主力流出+涨幅高=派发)。
    返回 {n_flags, level, flags:[{signal,reason}]}。
    """
    def g(k):
        try:
            v = float(row.get(k))
            return None if v != v else v
        except (TypeError, ValueError):
            return None

    wr, mnr, p60 = g("winner_rate"), g("main_net_ratio"), g("pct_60d")
    rsi, ptc = g("rsi6"), g("price_to_cost")
    flags: list[tuple[str, str]] = []
    if wr is not None and wr > 85:
        flags.append(("高位获利盘抛压", f"winner_rate {wr:.0f}%>85,获利盘近满=抛压/见顶(IC −42bps)"))
    if p60 is not None and p60 > 40 and mnr is not None and mnr < 0:
        flags.append(("放量滞涨/派发", f"60日涨 {p60:.0f}% 但主力净占比 {mnr * 100:+.1f}%(流出)=拉高派发嫌疑"))
    if p60 is not None and rsi is not None and p60 > 50 and rsi > 80:
        flags.append(("过热抛物线顶", f"60日涨 {p60:.0f}% + RSI6 {rsi:.0f}>80=透支,T+1 偏弱"))
    if ptc is not None and ptc > 1.5 and mnr is not None and mnr < 0:
        flags.append(("浮盈了结风险", f"现价/筹码成本 {ptc:.2f}>1.5 且主力流出=易获利了结"))
    level = "高" if len(flags) >= 3 else "中" if len(flags) == 2 else "低" if len(flags) == 1 else "无"
    return {"n_flags": len(flags), "level": level,
            "flags": [{"signal": s, "reason": r} for s, r in flags]}


def classify_regime(row: dict) -> dict:
    """L2a 确定性 regime 分类(纯函数,复用 L1 因子行,零取数)。

    区分"健康强势"vs"衰竭顶"——根治 L2 把 winner_rate 满/超买的强势股一刀切的问题:
    满获利盘/超买**只在主力流出或业绩证伪时**才算衰竭,主力还在+业绩跟得上则归"趋势"。
    返回 {regime, resonance, healthy_strong, exhausted, reasons}。
    regime ∈ {趋势, 回归, 过热衰竭, 平庸}。
    """
    def g(k):
        try:
            v = float(row.get(k))
            return None if v != v else v
        except (TypeError, ValueError):
            return None

    p60, mnr, wr = g("pct_60d"), g("main_net_ratio"), g("winner_rate")
    rsi, npy, sm = g("rsi6"), g("np_yoy"), g("score_momentum")

    # 共振:看多因子组 ≥60 的个数(0–7)
    groups = ("score_momentum", "score_fund_main", "score_chip", "score_north",
              "score_tech", "score_growth", "score_value")
    resonance = sum(1 for k in groups for v in [g(k)] if v is not None and v >= 60)

    reasons: list[str] = []
    # 衰竭顶(真该砍的强势)
    exhausted = False
    if p60 is not None and p60 >= 40 and mnr is not None and mnr < -0.04:
        exhausted = True
        reasons.append(f"放量滞涨/派发(60日{p60:.0f}%+主力净{mnr * 100:+.1f}%)")
    if p60 is not None and p60 >= 50 and npy is not None and npy < 0:
        exhausted = True
        reasons.append(f"业绩证伪(涨{p60:.0f}%但np{npy:.0f}%)")
    if wr is not None and wr >= 85 and mnr is not None and mnr < 0:
        exhausted = True
        reasons.append(f"满获利盘+主力流出(winner{wr:.0f}%)")
    if p60 is not None and p60 >= 80 and rsi is not None and rsi >= 85 and (mnr is None or mnr < 0):
        exhausted = True
        reasons.append(f"抛物线顶(涨{p60:.0f}%+RSI{rsi:.0f})")

    # 健康强势(涨但主力还在 + 业绩不证伪)
    healthy_strong = bool(
        ((p60 is not None and p60 >= 40) or (sm is not None and sm >= 70))
        and (mnr is not None and mnr >= -0.01)
        and (npy is None or npy > 0)
    )
    if healthy_strong:
        reasons.append("健康强势(主力还在+业绩跟得上)")

    # regime 级联(优先级:衰竭 > 健康强势 > 低位回归 > 高共振 > 平庸)
    if exhausted and not healthy_strong:
        regime = "过热衰竭"
    elif healthy_strong:
        regime = "趋势"
    elif wr is not None and wr < 40 and mnr is not None and mnr > 0:
        regime = "回归"
        reasons.append(f"低获利盘有空间(winner{wr:.0f}%)+主力进")
    elif resonance >= 4:
        regime = "趋势" if (sm is not None and sm >= 70) else "回归"
    else:
        regime = "平庸"

    return {"regime": regime, "resonance": resonance,
            "healthy_strong": healthy_strong, "exhausted": exhausted, "reasons": reasons}


def render_dcf_block(per_share: float, sens: dict, price: float | None = None) -> str:
    """DCF 结果 + 敏感性矩阵 → markdown(给 analyze-ticker 全量卡)。"""
    lines = [f"**简版 DCF 内在价值**:每股 ~{per_share:.2f}"
             + (f"(现价 {price:.2f},{'低估' if per_share > price else '高估'} "
                f"{abs(per_share / price - 1) * 100:.0f}%)" if price else "")]
    lines.append("\n敏感性(行=WACC,列=永续前 5 年增长):")
    lines.append("| WACC＼g | " + " | ".join(f"{g * 100:.0f}%" for g in sens["growths"]) + " |")
    lines.append("|---|" + "|".join(["---"] * len(sens["growths"])) + "|")
    for w, rowv in zip(sens["waccs"], sens["matrix"], strict=True):
        lines.append(f"| {w * 100:.0f}% | " + " | ".join(f"{v:.1f}" if v is not None else "—" for v in rowv) + " |")
    lines.append("\n_DCF 对 WACC/增长极敏感,作区间参照、非点估计。_")
    return "\n".join(lines)


def render_trap_block(trap: dict) -> str:
    """trap_signals → markdown 风险标。"""
    if not trap["flags"]:
        return "**杀猪盘/派发风险**:无明显信号(未触发获利盘满/放量滞涨/过热/浮盈了结)。"
    head = f"**杀猪盘/派发风险:{trap['level']}({trap['n_flags']} 信号)**"
    return head + "\n" + "\n".join(f"- ⚠️ {x['signal']}:{x['reason']}" for x in trap["flags"])


# ───────────────────────── tushare 取数透镜(失败降级 None) ─────────────────────────


def ashare_fundamentals_ts(code: str) -> str | None:
    """A股原生财报:fina_indicator(5y ROE/利润率/负债率/同比)+ dividend(最新分红)。补 yfinance 稀疏。"""
    try:
        from tushare_source import _pro, _ts_call
        pro = _pro()
    except Exception:  # noqa: BLE001
        return None
    tc = _tscode(code)
    out: list[str] = []
    try:
        fi = _ts_call(lambda: pro.fina_indicator(
            ts_code=tc, fields="ts_code,end_date,roe,netprofit_margin,grossprofit_margin,"
                               "debt_to_assets,or_yoy,netprofit_yoy"))
        if fi is not None and len(fi):
            fi = fi.sort_values("end_date").drop_duplicates("end_date")
            ann = fi[fi["end_date"].str.endswith("1231")].tail(5)
            roes = [(r["end_date"][:4], r["roe"]) for _, r in ann.iterrows() if r["roe"] == r["roe"]]
            roe_s = " → ".join(f"{y}:{float(v):.1f}%" for y, v in roes)
            last = fi.iloc[-1]
            out.append(f"**A股原生财报(tushare fina_indicator)**:5年 ROE {roe_s or '—'};"
                       f"最新({last['end_date']})毛利率 {_f(last.get('grossprofit_margin'))}%、"
                       f"净利率 {_f(last.get('netprofit_margin'))}%、资产负债率 {_f(last.get('debt_to_assets'))}%、"
                       f"营收同比 {_f(last.get('or_yoy'))}%、净利同比 {_f(last.get('netprofit_yoy'))}%。")
    except Exception as e:  # noqa: BLE001
        out.append(f"_tushare 财报指标取数失败: {e}_")
    try:
        dv = _ts_call(lambda: pro.dividend(ts_code=tc, fields="end_date,div_proc,cash_div_tax,ann_date"))
        if dv is not None and len(dv):
            d = dv[dv["div_proc"].astype(str).str.contains("实施|预案", na=False)].sort_values("end_date")
            if len(d):
                r = d.iloc[-1]
                out.append(f"**分红(tushare)**:最近 {r['end_date']} 每10股税前 {_f(r.get('cash_div_tax'))} 元"
                           f"({r.get('div_proc', '—')})。")
    except Exception as e:  # noqa: BLE001
        out.append(f"_tushare 分红取数失败: {e}_")
    return "\n\n".join(out) if out else None


def margin_trend_ts(code: str, lookback: int = 30) -> str | None:
    """近 ~20 交易日融资余额趋势(两融标的;非标的返回 None)。"""
    try:
        from tushare_source import _pro, _ts_call
        pro = _pro()
    except Exception:  # noqa: BLE001
        return None
    tc = _tscode(code)
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=lookback + 20)).strftime("%Y%m%d")
    try:
        mg = _ts_call(lambda: pro.margin_detail(ts_code=tc, start_date=start, end_date=end,
                                                fields="trade_date,rzye,rzrqye"))
        if mg is None or len(mg) == 0:
            return None
        mg = mg.sort_values("trade_date").tail(20)
        rz = mg["rzye"].astype(float) / 1e8  # 元 → 亿
        if len(rz) < 2:
            return None
        chg = (rz.iloc[-1] / rz.iloc[0] - 1) * 100 if rz.iloc[0] else 0.0
        trend = "增(杠杆资金进场)" if chg > 3 else "降(杠杆资金撤离)" if chg < -3 else "平"
        return (f"**融资余额趋势(tushare,近{len(rz)}日)**:{rz.iloc[0]:.2f}亿 → **{rz.iloc[-1]:.2f}亿**"
                f"({chg:+.1f}%,{trend})。_(Phase A 实测:融资余额对 T+1 无预测力,作中期资金背景。)_")
    except Exception:  # noqa: BLE001
        return None


def lhb_seats(code: str, date: str, lookback_days: int = 20) -> str | None:
    """龙虎榜机构 vs 游资席位识别(近窗口);Phase A 实测机构上榜买入后续偏弱 → 标注反指。"""
    try:
        from tushare_source import _code6, _pro, _trade_days, _ts_call, resolve_momentum_dates
        pro = _pro()
    except Exception:  # noqa: BLE001
        return None
    c6 = str(code).split(".")[0].zfill(6)
    last = resolve_momentum_dates(pro, date)[0]
    start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=lookback_days)).strftime("%Y%m%d")
    inst_net = retail_net = 0.0
    appeared: list[str] = []
    try:
        for d in _trade_days(pro, start, last)[-15:]:
            df = _ts_call(lambda d=d: pro.top_inst(trade_date=d))
            if df is None or len(df) == 0:
                continue
            sub = df[_code6(df["ts_code"]) == c6]
            if not len(sub):
                continue
            appeared.append(d)
            for _, r in sub.iterrows():
                net = float(r.get("net_buy") or 0)
                if "机构专用" in str(r.get("exalter", "")):
                    inst_net += net
                else:
                    retail_net += net
    except Exception:  # noqa: BLE001
        return None
    if not appeared:
        return "**龙虎榜席位**:近窗口未上榜 → 无单日异动席位痕迹。"
    note = "(⚠️ Phase A 实测:机构上龙虎榜净买后续 T+1~T+10 反而偏弱,勿当强利好)" if inst_net > 0 else ""
    return (f"**龙虎榜席位识别(tushare,近 {len(appeared)} 次上榜)**:机构专用净买 **{inst_net / 1e4:+.0f}万**"
            f"{note}、游资/营业部净买 **{retail_net / 1e4:+.0f}万**。")


def _f(v) -> str:
    try:
        x = float(v)
        return "—" if x != x else f"{x:.1f}"
    except (TypeError, ValueError):
        return "—"


# ───────────────────────── 离线自测(DCF + trap 纯函数) ─────────────────────────


def _selftest() -> int:
    fails: list[str] = []

    # DCF:已知输入 → 合理每股;WACC↑ → 估值↓
    d = simple_dcf(fcf_base=100, growth_5y=0.10, terminal_growth=0.03, wacc=0.09, shares=100, net_debt=0)
    if not (d and d["per_share"] > 0):
        fails.append(f"DCF 基本算错: {d}")
    d_hi = simple_dcf(100, 0.10, 0.03, 0.12, 100, 0)
    if not (d_hi["per_share"] < d["per_share"]):
        fails.append("WACC↑ 应使每股↓")
    if simple_dcf(100, 0.1, 0.05, 0.04, 100):  # wacc<=tg → 非法
        fails.append("wacc<=terminal_growth 应返回 {}")
    sens = dcf_sensitivity(100, 100, 0, [0.08, 0.10, 0.12], [0.06, 0.10])
    if len(sens["matrix"]) != 3 or len(sens["matrix"][0]) != 2:
        fails.append(f"敏感性矩阵形状错: {sens}")
    if not (sens["matrix"][0][1] > sens["matrix"][2][0]):  # 低WACC高g > 高WACC低g
        fails.append("敏感性单调性错")

    # trap:获利盘满 + 主力流出 + 过热 → 多信号高风险
    t = trap_signals({"winner_rate": 95, "main_net_ratio": -0.05, "pct_60d": 80, "rsi6": 85, "price_to_cost": 1.8})
    if t["n_flags"] < 3 or t["level"] != "高":
        fails.append(f"trap 高风险应≥3信号: {t}")
    sigs = {x["signal"] for x in t["flags"]}
    if "高位获利盘抛压" not in sigs or "过热抛物线顶" not in sigs:
        fails.append(f"trap 缺关键信号: {sigs}")
    # 干净票 → 无信号
    t0 = trap_signals({"winner_rate": 30, "main_net_ratio": 0.02, "pct_60d": 10, "rsi6": 55, "price_to_cost": 0.9})
    if t0["n_flags"] != 0 or t0["level"] != "无":
        fails.append(f"干净票不应触发 trap: {t0}")
    # NaN 容错
    if trap_signals({"winner_rate": float("nan")})["n_flags"] != 0:
        fails.append("trap NaN 容错失败")

    # classify_regime:健康强势 vs 衰竭顶 的判别(L2 不再一刀切)
    cr_strong = classify_regime({"pct_60d": 205, "main_net_ratio": 0.01, "winner_rate": 86,
                                 "rsi6": 92, "np_yoy": 105, "score_momentum": 78,
                                 "score_fund_main": 65, "score_growth": 80, "score_tech": 70})
    if cr_strong["regime"] != "趋势" or cr_strong["exhausted"]:
        fails.append(f"健康强势(主力还在+np正,winner满)应判趋势∧非衰竭: {cr_strong}")
    cr_dead = classify_regime({"pct_60d": 346, "main_net_ratio": -0.03, "winner_rate": 77,
                               "rsi6": 74, "np_yoy": -112, "score_momentum": 99})
    if cr_dead["regime"] != "过热衰竭" or not cr_dead["exhausted"]:
        fails.append(f"涨346%但np-112%应判过热衰竭∧衰竭: {cr_dead}")
    cr_rev = classify_regime({"pct_60d": 15, "main_net_ratio": 0.03, "winner_rate": 35,
                              "rsi6": 50, "np_yoy": 20, "score_growth": 65})
    if cr_rev["regime"] != "回归":
        fails.append(f"低获利盘35+主力进应判回归: {cr_rev}")
    cr_mid = classify_regime({"pct_60d": 5, "main_net_ratio": -0.005, "winner_rate": 50,
                              "rsi6": 48, "np_yoy": 3, "score_momentum": 30})
    if cr_mid["regime"] != "平庸" or cr_mid["resonance"] != 0:
        fails.append(f"无共振无边际应判平庸: {cr_mid}")

    # _tscode
    if _tscode("600519") != "600519.SH" or _tscode("000001") != "000001.SZ" or _tscode("920981") != "920981.BJ":
        fails.append("_tscode 映射错")

    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print("  -", f)
        return 1
    print("SELFTEST ✅  DCF(单调/敏感性)+ trap(获利盘满/过热/派发 + NaN容错)+ "
          "regime(健康强势≠衰竭顶/回归/平庸)+ 代码映射 全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest() if "--selftest" in sys.argv else 0)

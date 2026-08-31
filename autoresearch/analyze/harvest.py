"""Deterministic data harvester for the "Claude-as-engine" workflow (v3).

Calls the SAME data tools the real TradingAgents analysts call (via
``@tool`` wrappers -> ``route_to_vendor`` -> yfinance / FRED / Polymarket),
PLUS yfinance enrichments fetched directly (v2: options/IV, analyst
consensus, earnings calendar, peer-relative; v3: ownership/short-interest,
earnings-quality metrics), for one ticker + date, and dumps every raw output
to a single markdown file. No LLM is instantiated, so this needs NO paid LLM
API key — only free data vendors (yfinance/Polymarket are keyless; FRED needs
FRED_API_KEY).

The yfinance enrichments are US-centric: options chains, analyst coverage,
earnings calendars and short-interest are sparse-to-absent for many non-US
tickers, so each block degrades gracefully and says so.

Usage:
    python -m autoresearch.analyze.harvest TICKER [YYYY-MM-DD] [stock|crypto] [PEER1,PEER2,...]
        [--slim [--out-dir PATH]] [--name A股中文简称]
"""

import os
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

from autoresearch.common import workspace as ws

ROOT = Path(__file__).resolve().parents[2]  # repo root (autoresearch/analyze/ → ../../)


def _load_env(env_path: Path) -> None:
    """Minimal .env loader (no dependency): KEY=VALUE lines, '#' comments,
    optional 'export ' prefix and surrounding quotes. Never overrides a value
    already present in the real environment."""
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_env(ROOT / ".env")

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402

from autoresearch.agents.utils.agent_utils import (  # noqa: E402
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_global_news,
    get_income_statement,
    get_indicators,
    get_insider_transactions,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
    get_stock_data,
    get_verified_market_snapshot,
    resolve_instrument_identity,
)
from autoresearch.contracts.agent_output import L1_REUSE_COLUMNS  # noqa: E402
from autoresearch.data.keyless import consensus_eps_block  # noqa: E402
from autoresearch.dataflows.config import set_config  # noqa: E402
from autoresearch.dataflows.stockstats_utils import filter_financials_by_date  # noqa: E402
from autoresearch.dataflows.symbol_utils import normalize_symbol  # noqa: E402
from autoresearch.default_config import DEFAULT_CONFIG  # noqa: E402

# The standard indicator menu the market analyst chooses from (its system prompt).
INDICATORS = [
    "close_50_sma", "close_200_sma", "close_10_ema",
    "macd", "macds", "macdh", "rsi",
    "boll", "boll_ub", "boll_lb", "atr", "vwma",
]
# Key macro series the news analyst can pull from FRED.
MACRO = [
    "fed_funds_rate", "10y_treasury", "yield_curve",
    "cpi", "core_pce", "unemployment", "real_gdp", "vix",
]
# Forward-looking event probabilities (Polymarket, keyless).
PREDICTION_TOPICS = ["Fed rate cut", "recession 2026"]

# Built-in peer hints (override via the 4th CLI arg). Kept small on purpose;
# peer SELECTION is the easiest thing to get wrong, so when unknown we fall
# back to the benchmark only rather than guessing.
PEER_MAP = {
    "NVDA": ["AMD", "AVGO", "MU", "TSM"],
    "AMD": ["NVDA", "AVGO", "INTC", "TSM"],
    "AVGO": ["NVDA", "AMD", "QCOM", "TXN"],
    "AAPL": ["MSFT", "GOOGL", "AMZN", "META"],
    "MSFT": ["AAPL", "GOOGL", "AMZN", "AMD"],
    "TSLA": ["GM", "F", "RIVN", "BYDDY"],
}
# Extra sector benchmark beyond SPY, when we can map it.
SECTOR_ETF = dict.fromkeys(("NVDA", "AMD", "AVGO", "MU", "INTC", "TSM", "QCOM", "TXN"), "SOXX")


def _is_ashare(ticker: str) -> bool:
    """True for mainland-China A-shares (Shanghai/Shenzhen/Beijing)."""
    return normalize_symbol(ticker).endswith((".SS", ".SZ", ".BJ"))


def _benchmarks(symbol: str) -> list[str]:
    """Market-appropriate index benchmarks for peer-relative returns."""
    sym = normalize_symbol(symbol)
    if sym.endswith((".SS", ".SZ", ".BJ")):
        code = sym.split(".")[0]
        bench = ["000300.SS"]                       # CSI 300 (broad A-share)
        if code[:3] in ("300", "301"):
            bench.append("159915.SZ")               # ChiNext ETF (index 399006.SZ has no yfinance history)
        return bench
    return ["SPY"] + ([SECTOR_ETF[symbol.upper()]] if symbol.upper() in SECTOR_ETF else [])


# --- v2 yfinance enrichments (US-centric; degrade gracefully) ----------------

def _spot(t: "yf.Ticker") -> float | None:
    try:
        return float(t.fast_info["last_price"])
    except Exception:
        try:
            return float(t.history(period="5d")["Close"].dropna().iloc[-1])
        except Exception:
            return None


def _hist_returns(symbol: str, end_date: str):
    """1m/3m/6m % returns (≈21/63/126 trading rows) on/before end_date."""
    d_end = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    d_start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=220)).strftime("%Y-%m-%d")
    closes = yf.Ticker(normalize_symbol(symbol)).history(start=d_start, end=d_end)["Close"].dropna()

    def ret(days):
        if len(closes) < days + 1:
            return None
        return round(float((closes.iloc[-1] - closes.iloc[-1 - days]) / closes.iloc[-1 - days] * 100), 1)

    return ret(21), ret(63), ret(126)


# ═══════════ 外源扩面 D-3(设计稿 2026-08-28 §5.2 / §7.2 / §7.3 / §8)═══════════
#
# 取代 v1 `options_iv_summary` 的三处病:只取"最近一个到期" / 跨式用 `lastPrice` / PCR 被直接
# 渲染成"偏防御"。数值口径的唯一事实源是 `data/sources/yf_options.py`;本文件只负责**渲染**
# 与**消费纪律**:
#
#   1. 隐含波动 = 预期**量级**,不是方向 —— 必须与「财报历史实际波动」对照写一句
#      「市场定价 ±x% vs 过去 8 次中位 ±y%」。只给一半 = 给了一个无法解读的数。
#   2. `UNMEASURED` 原样展示 + 原因,**永远不显示 0**(0 会被读成"波动为零")。
#   3. 偏度 / PCR 只描述**仓位结构**;**不得由期权推出评级**(§5.1 阶段裁定)。
#   4. 财报历史实际波动按时段对齐:AMC 用 D close→D+1 open/close;BMO 用 D−1 close→D
#      open/close;**时段未知不纳入**(猜 AMC/BMO = 量错对象)。
#
# slim / LITE 档整档关闭 —— 两道门:`external_sections(..., slim=True)` 返回空 + main() 里
# 原有的 `if not slim:`(§5.2「slim 关」;微观 lite 属 B 类,受判断层冻结)。

_TITLE_OPTIONS = "期权与仓位地形 (options v2)"
_TITLE_EDGAR = "外源事件账 · EDGAR 近90日 (T1官方)"
_TITLE_ANALYST_ACTIONS = "外源事件账 · 分析师行动 近30日"
_TITLE_GNEWS_EN = "外源事件账 · Google News en 14日候选 (T4发现源·UNVERIFIED)"
_TITLE_GNEWS_ZH = "外源事件账 · Google News zh 14日候选 (T4发现源·UNVERIFIED)"
_TITLE_READTHROUGH = "海外映射 (readthrough·≤4名·事实行)"

#: v1 的「无挂牌期权」降级文案 —— **原样保留**(下游 playbook / 分析师按这句话认降级)。
NO_OPTIONS_DEGRADE = (
    "_该标的在 yfinance 无挂牌期权（A股/港股等常见）→ 期权/IV/定位信号不可用，"
    "催化剂&定位分析师需注明降级。_"
)

_EARNINGS_HIST_N = 8            # §5.2:财报历史实际波动对照取最近 8 次
_GNEWS_DAYS = 14
_GNEWS_MAX_ROWS = 12
_EDGAR_DAYS = 90
_EDGAR_MAX_ROWS = 15
_ANALYST_ACTION_DAYS = 30

_RT_MAX = 4                     # §8:单层 ≤4 项(load_map 已截,这里再截一次 = 纵深防御)
_RT_KINDS = ("company", "etf", "index")
_RT_RELATIONS = ("customer", "supplier", "peer", "theme")
_RT_DIRECTIONS = ("downstream", "upstream", "peer")
_RT_UNTRADABLE = ("delisted", "suspended", "halted", "unlisted", "expired")

_T4_NOTE = (
    "> **T4 发现源 · UNVERIFIED**(§3.1):聚合标题 / 摘要**不可单独支持任何断言**,"
    "只是**候选,须跟到原文** —— WebFetch canonical 原文、按 T1/T2/T3 重新标级后才可引用。"
)


def _degrade_note(endpoint: str, reason: str, key: str = "") -> None:
    """B 级降级记账(懒导入;记账本身失败也不许影响 harvest)。"""
    try:
        from autoresearch.data.contracts import record_degradation
        record_degradation(endpoint, reason, key=key)
    except Exception:  # noqa: BLE001
        print(f"[harvest] {endpoint}:{reason}(记账失败)", file=sys.stderr)


def _num(v, nd: int = 4):
    """标量 → round 后的 float;None / NaN / 不可解析 → None(**不是 0**)。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, nd)


def _cell(v, nd: int = 2, suffix: str = "") -> str:
    n = _num(v, nd)
    return "—" if n is None else f"{n:.{nd}f}{suffix}"


def _signed(v, nd: int = 2, suffix: str = "%") -> str:
    n = _num(v, nd)
    return "—" if n is None else f"{n:+.{nd}f}{suffix}"


def _abs_pct(v, nd: int = 2) -> str:
    n = _num(v, nd)
    return "—" if n is None else f"±{abs(n):.{nd}f}%"


def _median(values: list[float]) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


# ───────────────────── 财报时点:时段(AMC/BMO)与历史实际波动 ─────────────────────


def _et_session(ts) -> str | None:
    """财报时刻 → `AMC` / `BMO`;**不猜**:00:00 占位与盘中时刻一律 None(§2 事件契约)。

    判据是交易所本地时刻:≥16:00 = 盘后(AMC),<09:30 = 盘前(BMO)。yfinance 在只知道
    日期时给 00:00 占位 —— 那是 `DATE_ONLY`,拿它当 BMO 就是**量错对象**(锚会整体错一天)。
    """
    try:
        t = pd.Timestamp(ts)
    except Exception:  # noqa: BLE001
        return None
    if getattr(t, "tzinfo", None) is not None:
        try:
            t = t.tz_convert("America/New_York")
        except Exception:  # noqa: BLE001
            return None
    try:
        h, m = int(t.hour), int(t.minute)
    except Exception:  # noqa: BLE001
        return None
    if (h, m) == (0, 0):
        return None
    if h >= 16:
        return "AMC"
    if (h, m) < (9, 30):
        return "BMO"
    return None


def earnings_events(symbol: str, curr_date: str, *, frame=None) -> list[dict]:
    """`earnings_dates` → `[{date, session, time_quality, ts}]`(日期降序);取数失败 → []。

    `session` 为 None 时 `time_quality = DATE_ONLY` —— 下游据此把它排除在锚对齐之外。
    """
    df = frame
    if df is None:
        try:
            df = yf.Ticker(normalize_symbol(symbol)).earnings_dates
        except Exception as e:  # noqa: BLE001
            _degrade_note("us_earnings_dates", f"取数失败({type(e).__name__}: {e})", key=symbol)
            df = None
    if df is None or not len(df):
        return []
    out: list[dict] = []
    for ts in list(getattr(df, "index", [])):
        try:
            d = pd.Timestamp(ts).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            continue
        sess = _et_session(ts)
        out.append({"date": d, "session": sess,
                    "time_quality": "TIMED" if sess else "DATE_ONLY", "ts": str(ts)})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def next_earnings_spec(symbol: str, curr_date: str, *, events=None) -> dict:
    """下次财报 `{date, session, time_quality}`(严格晚于分析日);无 → date=None。"""
    evs = events if events is not None else earnings_events(symbol, curr_date)
    future = sorted((e for e in evs if str(e.get("date") or "") > curr_date),
                    key=lambda e: e["date"])
    if not future:
        return {"date": None, "session": None, "time_quality": "UNKNOWN"}
    e = future[0]
    return {"date": e["date"], "session": e.get("session"),
            "time_quality": e.get("time_quality") or "DATE_ONLY"}


def _bar_days(bars) -> tuple[list[str], dict]:
    """日线帧 → (升序日期表, {日期: (open, close)});空 / 缺列 → ([], {})。"""
    if bars is None or not len(bars):
        return [], {}
    cols = {str(c).lower(): c for c in list(getattr(bars, "columns", []))}
    o_col, c_col = cols.get("open"), cols.get("close")
    if o_col is None or c_col is None:
        return [], {}
    by_day: dict[str, tuple] = {}
    for idx, row in bars.iterrows():
        try:
            d = pd.Timestamp(idx).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            continue
        by_day[d] = (_num(row[o_col], 6), _num(row[c_col], 6))
    return sorted(by_day), by_day


def earnings_realized_moves(events, bars, *, limit: int = _EARNINGS_HIST_N) -> dict:
    """按**时段对齐**的财报历史实际波动(§5.2 字段表逐行)。

        AMC(D 盘后披露): D close → D+1 open / D+1 close
        BMO(D 盘前披露): D−1 close → D open / D close
        时段未知        : **不纳入**(不猜 AMC/BMO —— 猜错 = 整条对照句量错对象)

    返回 `{"rows", "n", "median_abs_cc_pct", "median_abs_co_pct", "skipped_unknown_session",
    "skipped_no_bars", "status", "reason"}`;一条都对不齐 → `status="UNMEASURED"`(**不是 0**)。
    """
    days, by_day = _bar_days(bars)
    pos = {d: i for i, d in enumerate(days)}
    rows: list[dict] = []
    skipped_unknown = skipped_bars = 0
    for ev in sorted(events or [], key=lambda e: str(e.get("date") or ""), reverse=True):
        if len(rows) >= int(limit):
            break
        d = str(ev.get("date") or "")
        sess = ev.get("session")
        if sess not in ("AMC", "BMO"):
            skipped_unknown += 1
            continue
        i = pos.get(d)
        j = (i + 1) if (sess == "AMC" and i is not None) else i
        base_i = i if sess == "AMC" else (i - 1 if i is not None else None)
        if i is None or j is None or base_i is None or base_i < 0 or j >= len(days):
            skipped_bars += 1
            continue
        base = by_day[days[base_i]][1]
        o, c = by_day[days[j]]
        if not base or o is None or c is None:
            skipped_bars += 1
            continue
        rows.append({
            "date": d,
            "session": sess,
            "anchor": ("D close→D+1 open/close" if sess == "AMC"
                       else "D−1 close→D open/close"),
            "base_date": days[base_i],
            "move_date": days[j],
            "close_to_open_pct": _num((o / base - 1.0) * 100.0, 3),
            "close_to_close_pct": _num((c / base - 1.0) * 100.0, 3),
        })
    cc = [abs(r["close_to_close_pct"]) for r in rows if r["close_to_close_pct"] is not None]
    co = [abs(r["close_to_open_pct"]) for r in rows if r["close_to_open_pct"] is not None]
    out = {
        "rows": rows, "n": len(rows),
        "median_abs_cc_pct": _num(_median(cc), 3),
        "median_abs_co_pct": _num(_median(co), 3),
        "skipped_unknown_session": skipped_unknown,
        "skipped_no_bars": skipped_bars,
        "status": "ok" if rows else "UNMEASURED",
        "reason": "",
    }
    if not rows:
        out["reason"] = (f"无可对齐的历史财报(时段未知 {skipped_unknown} 次 / "
                         f"缺日线 {skipped_bars} 次)——**UNMEASURED 不是 0**")
    return out


# ───────────────────────── 期权与仓位地形(options v2)─────────────────────────


def _iv_pct(v) -> str:
    """yfinance 的 impliedVolatility 已被源模块折算成年化 %(`iv_unit`)。"""
    n = _num(v, 2)
    return "—" if n is None else f"{n:.2f}%"


def _um(block, field: str, fmt=_iv_pct) -> str:
    """`ok` → 数字;否则 `UNMEASURED(原因)`。**任何情况下都不写 0**(纪律 1)。"""
    if not isinstance(block, dict):
        return "UNMEASURED(无数据块)"
    if str(block.get("status")) != "ok":
        return f"UNMEASURED({block.get('reason') or block.get('note') or '未给原因'})"
    return fmt(block.get(field))


def options_block(symbol: str, curr_date: str, *, chain=None, earn=None,
                  realized=None, iv_history=None) -> str:
    """报告块「期权与仓位地形」(美股 full;A 股/港股 → 原降级文案)。

    数值全部来自 `data/sources/yf_options.full_chain`(口径 = `METHOD_VERSION`);本函数只渲染。

    铁律(测试逐字锁):隐含波动 = **预期量级**,不是方向 → 必须与财报历史实际波动对照写出
    「市场定价 ±x% vs 过去 N 次中位 ±y%」;`UNMEASURED` 原样展示带原因、**不显示 0**;
    偏度 / PCR 只描述仓位结构,**不得由期权推出评级**。

    注入点(测试用,**禁真网络**):`chain` / `earn` / `realized` / `iv_history`。
    """
    sym = normalize_symbol(symbol)
    if earn is None:
        earn = next_earnings_spec(sym, curr_date)
    if chain is None:
        try:
            from autoresearch.data.sources import yf_options as _yfo
            chain = _yfo.full_chain(sym, curr_date,
                                    earnings_dt=(earn if earn.get("date") else None))
        except Exception as e:  # noqa: BLE001 — B 级:取数炸了也不许阻断 harvest
            _degrade_note("us_options", f"full_chain 失败({type(e).__name__}: {e})", key=sym)
            return f"_期权取数失败(B 级降级,不阻断):{type(e).__name__}: {e}_"
    if not isinstance(chain, dict):
        return f"_期权数据块形态异常({type(chain).__name__})→ 降级注明。_"

    reason = str(chain.get("reason") or "")
    if "无挂牌期权" in reason or "无挂牌期权" in " ".join(map(str, chain.get("degraded") or [])):
        return NO_OPTIONS_DEGRADE
    if chain.get("spot") is None and str(chain.get("status")) != "ok":
        return f"_期权块降级(B 级,不阻断):{reason or '未给原因'}_"

    if realized is None:
        realized = earnings_realized_moves(
            [e for e in earnings_events(sym, curr_date) if str(e.get("date") or "") <= curr_date],
            _earnings_bars(sym, curr_date))

    interp = chain.get("interpolation") or {}
    ts_block = chain.get("term_structure") or {}
    eim = chain.get("earnings_implied_move") or {}
    volp = chain.get("vol_premium") or {}
    skew = chain.get("skew_proxy") or {}
    pcr = chain.get("pcr") or {}

    out: list[str] = []
    out.append(
        f"**口径**:method_version=`{chain.get('method_version')}` ｜ spot "
        f"{_cell(chain.get('spot'))}(as-of {chain.get('spot_as_of') or '—'})｜ "
        f"chain_fetched_at {chain.get('chain_fetched_at') or '—'} ｜ market_state "
        f"{chain.get('market_state') or '—'} ｜ session_complete "
        f"{bool(chain.get('session_complete'))} ｜ as_of {chain.get('as_of')}")
    out.append(
        "> **消费纪律**:隐含波动 = 市场对**波动量级**的定价,**不是方向**;偏度 / PCR 只描述"
        "**仓位结构**;**不得由期权推出评级**(评级只由 rubric 三门决定)。`UNMEASURED` 是"
        "「没测到」,**不等于 0**。")

    out.append("\n### 30D ATM IV(一致口径,可跨日比较)")
    out.append(f"- 30D ATM IV: {_um(interp, 'atm_iv_30d')} ｜ 插值 "
               f"{interp.get('method') or '—'} ｜ 腿 {'/'.join(interp.get('legs') or []) or '—'}")
    pct = _iv_percentile_row(chain, iv_history)
    out.append(f"- 1 年分位(同 method_version 同 tenor 才比): {pct}")

    out.append("\n### 期限结构(最近有效到期)")
    pts = list(ts_block.get("points") or [])
    if pts:
        out.append("| 到期 | DTE | ATM IV(call/put 中值) |")
        out.append("|---|---:|---:|")
        for p in pts:
            out.append(f"| {p.get('expiry')} | {p.get('dte')} | {_iv_pct(p.get('atm_iv'))} |")
        out.append(f"- slope = IV_near − IV_far: "
                   f"{_signed(ts_block.get('slope'), 2, 'pp') if ts_block.get('slope') is not None else 'UNMEASURED(' + str(ts_block.get('reason') or '') + ')'}"
                   f" ｜ {ts_block.get('slope_def') or ''}")
    else:
        out.append(f"- UNMEASURED({ts_block.get('reason') or '无有效到期'})")

    out.append("\n### 财报隐含波动 vs 历史实际波动(**必读对照**)")
    out.append(f"- 下次财报: {earn.get('date') or '未确认'}"
               f"({earn.get('session') or '时段未知'} ｜ time_quality="
               f"{earn.get('time_quality') or 'UNKNOWN'})")
    implied = (f"{_abs_pct(eim.get('implied_move_pct'))}" if str(eim.get("status")) == "ok"
               else f"UNMEASURED({eim.get('reason') or '未给原因'})")
    hist_n = int((realized or {}).get("n") or 0)
    hist = (_abs_pct((realized or {}).get("median_abs_cc_pct"))
            if hist_n else f"UNMEASURED({(realized or {}).get('reason') or '无可对齐样本'})")
    out.append(f"- **市场定价 {implied} vs 过去 {hist_n} 次中位 {hist}**"
               f"(实际口径 = close→close;close→open 中位 "
               f"{_abs_pct((realized or {}).get('median_abs_co_pct'))})")
    out.append("- 口径:隐含 = ATM **有效 bid-ask midpoint** 跨式 / spot(**禁用 lastPrice**;"
               "任一腿 crossed / stale / 缺报价 → UNMEASURED);实际 = AMC 用 D close→D+1 "
               "open/close、BMO 用 D−1 close→D open/close,**时段未知不纳入**。")
    rows = list((realized or {}).get("rows") or [])
    if rows:
        out.append("")
        out.append("| 财报日 | 时段 | 锚 | close→open % | close→close % |")
        out.append("|---|---|---|---:|---:|")
        for r in rows:
            out.append(f"| {r['date']} | {r['session']} | {r['anchor']} | "
                       f"{_signed(r['close_to_open_pct'])} | {_signed(r['close_to_close_pct'])} |")
    out.append(f"- 未纳入:时段未知 {(realized or {}).get('skipped_unknown_session', 0)} 次 ｜ "
               f"缺日线 {(realized or {}).get('skipped_no_bars', 0)} 次")

    out.append("\n### 波动率溢价")
    out.append(f"- {volp.get('def') or 'vol_premium = ATM IV(30D) − 20 日已实现年化波动'}: "
               f"{_um(volp, 'premium_pp', lambda v: _signed(v, 2, 'pp'))} "
               f"(20 日已实现 {_iv_pct(volp.get('realized_vol_20d'))})")

    out.append(f"\n### 偏度代理({(skew.get('label') or '10% 价外偏度代理')})")
    out.append(f"- IV(put K≈0.9·S) − IV(call K≈1.1·S) = "
               f"{_um(skew, 'skew_pp', lambda v: _signed(v, 2, 'pp'))} ｜ 参考到期 "
               f"{skew.get('expiry') or '—'}")
    out.append(f"- {skew.get('note') or '无 Greeks → 不是 25Δ 偏度;只描述**仓位结构**,不含方向'}")

    out.append("\n### PCR(未平仓 OI)")
    by_exp = list(pcr.get("by_expiry") or [])
    if by_exp:
        out.append("| 到期 | DTE | call OI | put OI | PCR |")
        out.append("|---|---:|---:|---:|---:|")
        for p in by_exp:
            out.append(f"| {p.get('expiry')} | {p.get('dte')} | {_cell(p.get('call_oi'), 0)} | "
                       f"{_cell(p.get('put_oi'), 0)} | {_cell(p.get('pcr_oi'))} |")
    total = (pcr.get("total") or {}).get("pcr_oi")
    out.append(f"- 合计 PCR(OI): {_cell(total) if total is not None else 'UNMEASURED(' + str(pcr.get('reason') or '') + ')'}")
    out.append(f"- {pcr.get('semantics') or 'PCR 只描述**对冲 / 持仓压力**的量级;不含方向,不跨品种加总'}")

    out.append("\n### 明确不做(§5.2 末行)")
    out.append("- " + " / ".join(chain.get("not_computed") or
                                 ["max_pain", "oi_magnet", "unusual_activity"])
               + " —— 普查裁定的噪声源,不要加回来。")
    degraded = list(chain.get("degraded") or [])
    if degraded:
        out.append(f"\n> ⚠️ B 级降级留痕({len(degraded)} 条):" + " ｜ ".join(map(str, degraded[:4])))
    return "\n".join(out)


def _iv_percentile_row(chain: dict, iv_history) -> str:
    """30D ATM IV 的 1 年分位 —— 观测为空(湖里还没历史)→ 「样本不足(0)」,不编。"""
    try:
        from autoresearch.data.sources.yf_options import iv_percentile
    except Exception:  # noqa: BLE001
        return "UNMEASURED(分位口径模块不可用)"
    obs = list(iv_history or [])
    try:
        r = iv_percentile(obs, chain.get("atm_iv_30d"),
                          method_version=str(chain.get("method_version") or ""))
    except Exception as e:  # noqa: BLE001
        return f"UNMEASURED({type(e).__name__}: {e})"
    if str(r.get("status")) != "ok":
        drop = ", ".join(f"{k}={v}" for k, v in (r.get("dropped") or {}).items() if v)
        return (f"UNMEASURED({r.get('note') or '样本不足'}"
                + (f";已丢弃 {drop}" if drop else "")
                + " —— 口径不同的观测不进同一条历史)")
    return f"{r.get('pctile')}%(n={r.get('obs')})"


def _earnings_bars(symbol: str, curr_date: str, *, lookback_days: int = 900):
    """财报锚要用的日线(含 D+1,故右端取分析日次日);取数失败 → None(B 级)。"""
    try:
        d = datetime.strptime(curr_date, "%Y-%m-%d")
        d_end = (d + timedelta(days=2)).strftime("%Y-%m-%d")
        d_start = (d - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        return yf.Ticker(normalize_symbol(symbol)).history(start=d_start, end=d_end)
    except Exception as e:  # noqa: BLE001
        _degrade_note("us_options", f"财报历史日线取数失败({type(e).__name__}: {e})", key=symbol)
        return None


# ───────────────────────── 美股确定层:EDGAR / 分析师行动 / gnews ─────────────────────────


def edgar_block(ticker: str, curr_date: str, *, outcome=None, days: int = _EDGAR_DAYS) -> str | None:
    """EDGAR 近 90 日申报(T1 官方)。presence-gated:非美股(NO_CIK)→ 整块省略(返回 None)。

    「源成功但真空」与「请求 / 解析失败」分开渲染 —— 二者都落空表就分不清是"没有申报"还是
    "我们坏了"(§9 逐字)。任何情况都不抛。
    """
    res = outcome
    if res is None:
        try:
            from autoresearch.data.sources.edgar import fetch_submissions_safe
            res = fetch_submissions_safe(ticker, days=days,
                                         as_of=datetime.strptime(curr_date, "%Y-%m-%d").date())
        except Exception as e:  # noqa: BLE001
            _degrade_note("edgar", f"调用失败({type(e).__name__}: {e})", key=ticker)
            return f"_EDGAR 取数失败(B 级降级,不阻断):{type(e).__name__}: {e}_"
    status = str(getattr(res, "status", "") or "")
    df = getattr(res, "rows", None)       # `FetchOutcome.rows`(不是 frame/df)
    if status == "NO_CIK":
        return None                       # 非美股本来就没有 EDGAR → 不写空节
    if status == "FAILED":
        return (f"_EDGAR 取数失败(B 级降级,不阻断):{getattr(res, 'reason', '') or '未给原因'}_")
    if status == "EMPTY" or df is None or not len(df):
        return (f"_源成功、近 {days} 日 **0 条申报**(合法空,不是取数失败)。_")
    out = [f"近 {days} 日 filings(T1 原始 / 官方;可支持 material claim,仍需时点与字段匹配):",
           "", "| form | 申报日 | 报告期 | 受理时刻 | 链接 |", "|---|---|---|---|---|"]
    for _, r in df.head(_EDGAR_MAX_ROWS).iterrows():
        out.append(f"| {r.get('form')} | {r.get('filing_date')} | {r.get('report_date') or '—'} | "
                   f"{r.get('acceptance_datetime') or '—'} | {r.get('url') or '—'} |")
    if len(df) > _EDGAR_MAX_ROWS:
        out.append(f"\n> 另有 {len(df) - _EDGAR_MAX_ROWS} 条未列(按申报日降序截断)。")
    return "\n".join(out)


def analyst_actions_block(ticker: str, curr_date: str, *, actions=None, targets=None,
                          prev_targets=None, days: int = _ANALYST_ACTION_DAYS) -> str | None:
    """分析师行动近 30 日:评级变动窄表 + 目标价区间(变化需历史快照,缺 → UNMEASURED)。

    presence-gated:既无评级动作又无目标价字段(非美标的常见)→ 整块省略。
    """
    sym = normalize_symbol(ticker)
    if actions is None:
        try:
            actions = yf.Ticker(sym).upgrades_downgrades
        except Exception as e:  # noqa: BLE001
            _degrade_note("us_ticker", f"upgrades_downgrades 失败({type(e).__name__}: {e})", key=sym)
            actions = None
    if targets is None:
        try:
            info = yf.Ticker(sym).info or {}
        except Exception as e:  # noqa: BLE001
            _degrade_note("us_ticker", f"info 失败({type(e).__name__}: {e})", key=sym)
            info = {}
        targets = {k: info.get(k) for k in
                   ("targetLowPrice", "targetMeanPrice", "targetMedianPrice",
                    "targetHighPrice", "numberOfAnalystOpinions")}
    rows = _recent_actions(actions, curr_date, days)
    has_targets = any(v is not None for v in (targets or {}).values())
    if not rows and not has_targets:
        return None
    out: list[str] = []
    if rows:
        out += [f"评级变动(近 {days} 日,as-of ≤ {curr_date};窄表 = 只留窗内动作):", "",
                "| 日期 | 机构 | 动作 | From → To |", "|---|---|---|---|"]
        out += [f"| {r['date']} | {r['firm']} | {r['action']} | {r['from']} → {r['to']} |"
                for r in rows]
    else:
        out.append(f"_近 {days} 日**无评级变动**(源成功,窗内真空)。_")
    lo, hi = (targets or {}).get("targetLowPrice"), (targets or {}).get("targetHighPrice")
    out.append("")
    out.append(f"目标价区间: {_cell(lo)} – {_cell(hi)} ｜ 均值 "
               f"{_cell((targets or {}).get('targetMeanPrice'))} ｜ 中位 "
               f"{_cell((targets or {}).get('targetMedianPrice'))} ｜ 覆盖分析师 "
               f"{(targets or {}).get('numberOfAnalystOpinions') or '—'}")
    if prev_targets:
        out.append(f"区间变化 vs 上一快照({prev_targets.get('as_of') or '—'}): 低 "
                   f"{_signed(_delta(lo, prev_targets.get('targetLowPrice')), 2, '')} ｜ 高 "
                   f"{_signed(_delta(hi, prev_targets.get('targetHighPrice')), 2, '')} ｜ 均值 "
                   f"{_signed(_delta((targets or {}).get('targetMeanPrice'), prev_targets.get('targetMeanPrice')), 2, '')}")
    else:
        out.append("区间变化: **UNMEASURED**(无上一快照可比 —— 变化要历史快照才算得出;"
                   "**UNMEASURED 不是 0、不是「没变」**)")
    out.append("> 消费纪律:目标价是**卖方口径的预期**,不是事实;动作背后的论点要靠 "
               "`us-intel` 第②面追原文。")
    return _REALTIME_DISCLAIMER + "\n\n" + "\n".join(out)


def _delta(now_v, prev_v):
    a, b = _num(now_v), _num(prev_v)
    return None if (a is None or b is None) else round(a - b, 4)


def _recent_actions(actions, curr_date: str, days: int) -> list[dict]:
    """`upgrades_downgrades` → 窗内行(as-of ≤ 分析日,禁前视);形态不对 → []。"""
    if actions is None or not len(actions):
        return []
    try:
        start = (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=int(days))).date()
        end = datetime.strptime(curr_date, "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        return []
    recs = []
    it = actions.iterrows() if hasattr(actions, "iterrows") else enumerate(actions)
    for idx, row in it:
        raw = row.get("GradeDate") if hasattr(row, "get") else None
        try:
            d = pd.Timestamp(raw if raw is not None else idx).date()
        except Exception:  # noqa: BLE001
            continue
        if not (start <= d <= end):
            continue
        get = row.get if hasattr(row, "get") else (lambda k, _r=row: None)
        recs.append({"date": d.isoformat(), "firm": str(get("Firm") or "—"),
                     "action": str(get("Action") or "—"),
                     "from": str(get("FromGrade") or "—"), "to": str(get("ToGrade") or "—")})
    recs.sort(key=lambda r: r["date"], reverse=True)
    return recs


def gnews_block(query: str, curr_date: str, *, lang: str = "en", items=None,
                days: int = _GNEWS_DAYS) -> str | None:
    """Google News RSS 近 14 日**候选**(T4 发现源)。presence-gated:零条 → 整块省略。

    §3.1 / §12 Q6:聚合标题只能证明「搜到过」,**不能**令 `content_supports=true`;每条都标
    `T4 / UNVERIFIED`,必须跟到 canonical 原文才可作证据。取数失败由源模块 B 级记账,不抛。
    """
    rows = items
    if rows is None:
        try:
            from autoresearch.data.sources.gnews_rss import search
            rows = search(str(query), lang=lang, limit=60)
        except Exception as e:  # noqa: BLE001
            _degrade_note("gnews_rss", f"发现源调用失败({type(e).__name__}: {e})", key=str(query))
            return None
    rows = _news_window(rows or [], curr_date, days)
    if not rows:
        return None
    out = [_T4_NOTE, "",
           f"查询 `{query}`(lang={lang})｜ 窗 {days} 日 ｜ as-of ≤ {curr_date} ｜ 共 {len(rows)} 条候选",
           "", "| 发布时间 | 标题 | 来源站 | canonical 待核 URL |", "|---|---|---|---|"]
    for r in rows[:_GNEWS_MAX_ROWS]:
        title = str(r.get("title") or "").replace("|", "｜")
        out.append(f"| {str(r.get('published_ts') or '日期不明')} | {title} | "
                   f"{r.get('source_name') or '—'} | {r.get('url') or '—'} |")
    if len(rows) > _GNEWS_MAX_ROWS:
        out.append(f"\n> 另有 {len(rows) - _GNEWS_MAX_ROWS} 条候选未列(按发布时间降序截断)。")
    out.append("\n> 每条状态恒为 `T4 / UNVERIFIED` —— **候选,须跟到原文**;未追到 canonical "
               "原文之前不得进入正文判断(只能进证据附录 / 待核)。")
    return "\n".join(out)


def _news_window(rows, curr_date: str, days: int) -> list[dict]:
    """窗内过滤 + 降序;`published_ts` 缺失的条目保留(标「日期不明」)但排末尾。"""
    try:
        end = datetime.strptime(curr_date, "%Y-%m-%d").date()
        start = end - timedelta(days=int(days))
    except Exception:  # noqa: BLE001
        return list(rows)
    dated, undated = [], []
    for r in rows:
        raw = r.get("published_ts")
        if not raw:
            undated.append(r)
            continue
        try:
            d = pd.Timestamp(str(raw)).tz_localize(None).date()
        except Exception:  # noqa: BLE001
            try:
                d = pd.Timestamp(str(raw)).date()
            except Exception:  # noqa: BLE001
                undated.append(r)
                continue
        if start <= d <= end:                       # 晚于分析日 = 前视,丢弃
            dated.append((d, r))
    dated.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in dated] + undated


# ───────────────────────── A 股:海外映射(readthrough)─────────────────────────


def _rt_date_ok(v) -> str | None:
    s = str(v or "").strip()[:10]
    return s if len(s) == 10 and s[4] == "-" and s[7] == "-" else None


def _rt_valid(item, as_of: str) -> bool:
    """消费者侧独立准入(**不信任 load_map 已经筛过**):枚举 / 证据 URL / 有效期 / 可交易。"""
    if not isinstance(item, dict) or not str(item.get("symbol") or "").strip():
        return False
    if str(item.get("kind") or "") not in _RT_KINDS:
        return False
    if str(item.get("relation") or "") not in _RT_RELATIONS:
        return False
    if str(item.get("direction") or "") not in _RT_DIRECTIONS:
        return False
    url = str(item.get("evidence_url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    if item.get("tradable") is False:
        return False
    if str(item.get("status") or "").strip().lower() in _RT_UNTRADABLE:
        return False
    eff_from, eff_to, today = (_rt_date_ok(item.get("effective_from")),
                               _rt_date_ok(item.get("effective_to")), _rt_date_ok(as_of))
    if eff_from is None:
        return False
    if today is None:
        return True
    if eff_from > today:
        return False
    return not (eff_to is not None and eff_to < today)


def _readthrough_items(ticker: str, as_of: str, industry: str | None = None) -> list[dict]:
    """人工映射表 → 本票名单(**个股映射优先于行业**,§8);模块未接线 / 无映射 → []。"""
    try:
        from autoresearch.data.readthrough import load_map
    except Exception:  # noqa: BLE001 — D-1 source soak 前该模块可以整个不存在
        return []
    try:
        m = load_map(as_of) or {}
    except Exception as e:  # noqa: BLE001
        _degrade_note("readthrough_map", f"load_map 失败({type(e).__name__}: {e})", key=str(as_of))
        return []
    if not isinstance(m, dict):
        return []
    sym = normalize_symbol(ticker)
    codes = m.get("codes") if isinstance(m.get("codes"), dict) else {}
    for key in (sym.split(".")[0], sym):
        got = codes.get(key) if isinstance(codes, dict) else None
        if isinstance(got, (list, tuple)) and got:
            return list(got)
    inds = m.get("industries") if isinstance(m.get("industries"), dict) else {}
    got = inds.get(str(industry)) if (industry and isinstance(inds, dict)) else None
    return list(got) if isinstance(got, (list, tuple)) else []


def _readthrough_tape(symbols: list[str], as_of: str) -> tuple[dict, str | None]:
    """映射名的隔夜 tape → {SYMBOL: 行};失败 / 空 → ({}, stale_reason) 且记账,不抛。"""
    if not symbols:
        return {}, None
    try:
        from autoresearch.data.sources.yf_tape import fetch_global_tape
    except Exception as e:  # noqa: BLE001
        reason = f"tape 源未接线({type(e).__name__})"
        _degrade_note("global_tape", reason, key=str(as_of))
        return {}, reason
    try:
        df = fetch_global_tape(as_of=as_of, symbols=list(symbols))
    except Exception as e:  # noqa: BLE001
        reason = f"tape 取数失败({type(e).__name__}: {e})"
        _degrade_note("global_tape", reason, key=str(as_of))
        return {}, reason
    if df is None or not len(df) or "symbol" not in list(getattr(df, "columns", [])):
        reason = "tape 空返回(无 symbol 行)"
        _degrade_note("global_tape", reason, key=str(as_of))
        return {}, reason
    rows: dict[str, dict] = {}
    for _, r in df.iterrows():
        s = str(r.get("symbol") or "").strip().upper()
        if s:
            rows.setdefault(s, dict(r))
    return rows, None


def _readthrough_iv(symbol: str, as_of: str) -> dict:
    """映射名的**窄快照**(30D ATM IV;§9:映射名单走窄快照,不为一个量级把全链抓爆)。"""
    try:
        from autoresearch.data.sources.yf_options import narrow_snapshot
        return narrow_snapshot(symbol, as_of) or {}
    except Exception as e:  # noqa: BLE001
        _degrade_note("us_options", f"{symbol} 窄快照失败({type(e).__name__}: {e})", key=symbol)
        return {"status": "UNMEASURED", "reason": f"{type(e).__name__}"}


def readthrough_block(ticker: str, as_of: str, *, items=None, industry: str | None = None,
                      tape=None, iv=None, earnings=None) -> str | None:
    """A 股 full 的「海外映射」块:≤4 个美股名,每名一行**事实**。

    **渲染禁忌(§8,测试逐字探针)**:映射只表示「值得观察的关系」,不表示因果、不表示涨跌
    方向、不表示评级方向 —— 本块**不产生任何推论句**(禁词「所以 / 传导 / 带动」)。
    无有效映射 → 返回 None,**整块省略**(不写空表)。

    注入点(测试用,禁真网络):`items` 名单 / `tape` {SYMBOL: 行} / `iv` {SYMBOL: 窄快照}
    / `earnings` {SYMBOL: {"date","session"}}。
    """
    raw = items if items is not None else _readthrough_items(ticker, as_of, industry)
    valid = [it for it in (raw or []) if _rt_valid(it, as_of)][:_RT_MAX]
    if not valid:
        return None
    syms = [str(it["symbol"]).strip() for it in valid]
    if tape is None:
        tape, stale = _readthrough_tape(syms, as_of)
    else:
        tape, stale = {str(k).upper(): v for k, v in dict(tape).items()}, None

    out = [
        "> 名单来自人工维护的 `readthrough_map.yaml`(有效期内 + 证据 URL 齐,单层 ≤4 项)。",
        "> 映射只表示「值得观察的关系」:**不表示因果、不表示涨跌方向、不表示评级方向**。",
        "> 下面每一行都是**事实行**(行情由确定性 tape 供给;隐含波动是**预期量级**,不是方向),"
        "不是推论链 —— 要不要把它写进论点,由分析师自己举证。",
        "",
        "| 美股名 | 类型 | relation | direction | 上一完整交易日% | 5日% | 下次财报日 | 隐含波动量级(30D ATM IV) | 数据状态 |",
        "|---|---|---|---|---:|---:|---|---|---|",
    ]
    notes: list[str] = []
    for it in valid:
        sym = str(it["symbol"]).strip()
        kind = str(it["kind"])
        row = tape.get(sym.upper())
        reason = stale
        if reason is None and row is None:
            reason = f"tape 无 {sym} 行"
        elif reason is None and _rt_incomplete(row):
            reason = "美股时段未收(session_complete=False)"
        if kind == "index":
            iv_cell = "不适用(指数非可挂牌主体)"        # 不为它去抓一条注定为空的链
        else:
            snap = (iv or {}).get(sym) if iv is not None else _readthrough_iv(sym, as_of)
            iv_cell = (f"{_iv_pct((snap or {}).get('atm_iv_30d'))}(年化)"
                       if str((snap or {}).get("status")) == "ok"
                       else f"UNMEASURED({(snap or {}).get('reason') or '未给原因'})")
        if kind == "company":
            e = ((earnings or {}).get(sym) if earnings is not None
                 else next_earnings_spec(sym, as_of))
            earn_cell = (f"{e.get('date')}({e.get('session') or '时段未知'})"
                         if (e or {}).get("date") else "未确认")
        else:
            # §8:ETF / 指数不得伪装成公司或财报主体 —— 硬门,不是提示。
            earn_cell = "不适用(非公司主体)"
        out.append(f"| **{sym}** | {kind} | {it['relation']} | {it['direction']} | "
                   f"{_signed(_rt_cell(row, 'pct_1d'))} | {_signed(_rt_cell(row, 'pct_5d'))} | "
                   f"{earn_cell} | {iv_cell} | {reason or '—'} |")
        notes.append(f"- **{sym}** ｜ relation={it['relation']} ｜ direction={it['direction']} "
                     f"｜ rationale(原样): {str(it.get('rationale') or '—')} ｜ 证据: "
                     f"{str(it['evidence_url'])}")
    out.append("")
    out += notes
    out.append("")
    out.append("> `relation` / `direction` / `rationale` 一律**原样搬运**人工映射表,harvest "
               "不改写、不加解释。缺行 / 未收盘的一律标在「数据状态」列,**不用 0 填充**。")
    return "\n".join(out)


def _rt_cell(row, col: str):
    if not row or col not in row:
        return None
    return _num(row[col], 2)


def _rt_incomplete(row) -> bool:
    """`session_complete` 三态判活 —— pandas 取出来是 `numpy.bool_`,`is False` 判不出。"""
    if not row or "session_complete" not in row:
        return False
    v = row["session_complete"]
    try:
        if v is None or pd.isna(v):
            return False
    except (TypeError, ValueError):
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("false", "0", "no")
    return not bool(v)


def _company_query_name(explicit_name: str | None, identity: dict) -> str | None:
    """gnews 查询词的公司名来源(D1.6 #3):显式 `--name`(A股中文简称,Claude 在 session 内
    已知,同 `analyze.assemble --name` 的约定)优先 —— yfinance `longName`/`shortName`
    对 A 股常是英文/拼音,Google News zh 用它检索基本查不到东西;缺省时 fallback 回
    `identity.get("company_name")`(英文 longName,美股这就是对的查询词)。"""
    name = (explicit_name or "").strip()
    return name or identity.get("company_name")


# ───────────────────────── 派发清单(单一事实源)─────────────────────────


def external_sections(ticker: str, curr_date: str, *, slim: bool,
                      company_name: str | None = None) -> list[tuple]:
    """外源扩面块的派发清单 —— **单一事实源**(main() 照它派发,测试照它断言)。

    `slim=True`(scan L4 / stock-research lite)→ **返回空列表**:§5.2「slim 关」,且微观 lite
    的外源事实属 B 类(受 08-26 判断层冻结,要开关灰度)。这是第二道门,main() 里原有的
    `if not slim:` 是第一道。

    美股票:期权 v2 + EDGAR + 分析师行动 + Google News en 候选。
    A 股票:期权 v2(→ 无挂牌期权降级文案)+ 海外映射 + Google News zh 候选。
    """
    if slim:
        return []
    q = (company_name or "").strip() or ticker
    out: list[tuple] = [(_TITLE_OPTIONS, options_block, (ticker, curr_date), {})]
    if _is_ashare(ticker):
        out.append((_TITLE_READTHROUGH, readthrough_block, (ticker, curr_date), {}))
        out.append((_TITLE_GNEWS_ZH, gnews_block, (q, curr_date), {"lang": "zh"}))
    else:
        out.append((_TITLE_EDGAR, edgar_block, (ticker, curr_date), {}))
        out.append((_TITLE_ANALYST_ACTIONS, analyst_actions_block, (ticker, curr_date), {}))
        out.append((_TITLE_GNEWS_EN, gnews_block, (q, curr_date), {"lang": "en"}))
    return out


def _opt_section(title: str, fn, *args, **kwargs) -> str:
    """presence-gated 版 `_section`:块函数返回 None → **整节省略**(不写空表)。"""
    print(f"  - {title} ...", flush=True)
    try:
        out = fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 — 外源块一律 B 级,炸了也不许阻断 harvest
        _degrade_note("external_block", f"{title} 渲染失败({type(e).__name__}: {e})")
        return f"\n## {title}\n\n_ERROR fetching this section: {e}_\n"
    if out is None:
        return ""
    body = str(out).strip() or "_(empty)_"
    return f"\n## {title}\n\n{body}\n"


#: `.info` 是 yfinance 的**实时**快照字段 —— 没有历史版本可取,取到的永远是"此刻"。
#: 4 处消费点(D1.4 附注 #19/#30 等)不改取数(改了也没有历史可回放),只诚实标注,
#: 别让读者以为这些数字是「curr_date 当天」的已核数据。
_REALTIME_DISCLAIMER = "_as-of: 运行时刻(实时字段,不可回放)_"


def analyst_consensus(symbol: str) -> str:
    t = yf.Ticker(normalize_symbol(symbol))
    try:
        info = t.info or {}
    except Exception:
        info = {}
    rows = []
    label = {
        "currentPrice": "现价",
        "targetMeanPrice": "目标价(均值)",
        "targetMedianPrice": "目标价(中位)",
        "targetHighPrice": "目标价(高)",
        "targetLowPrice": "目标价(低)",
        "recommendationKey": "评级",
        "recommendationMean": "评级均值(1强买…5强卖)",
        "numberOfAnalystOpinions": "覆盖分析师数",
    }
    for k, lab in label.items():
        v = info.get(k)
        if v is not None:
            rows.append(f"| {lab} | {v} |")
    if not rows:
        return "_无分析师一致预期数据（非美标的常见）→ 降级注明。_"
    out = ["| 字段 | 值 |", "|---|---|", *rows]
    try:
        rec = t.recommendations
        if rec is not None and len(rec):
            out.append("\n近期评级/升降级（尾部）:\n```\n" + str(rec.tail(8)) + "\n```")
    except Exception:
        pass
    return _REALTIME_DISCLAIMER + "\n\n" + "\n".join(out)


def earnings_calendar(symbol: str) -> str:
    t = yf.Ticker(normalize_symbol(symbol))
    out = []
    try:
        cal = t.calendar
    except Exception:
        cal = None
    if isinstance(cal, dict) and cal:
        for key in ("Earnings Date", "Ex-Dividend Date", "Dividend Date"):
            if cal.get(key):
                out.append(f"- {key}: {cal.get(key)}")
    elif cal is not None and hasattr(cal, "empty") and not cal.empty:
        out.append("```\n" + str(cal) + "\n```")
    try:
        ed = t.earnings_dates
        if ed is not None and len(ed):
            out.append("\n近期财报（含 EPS 预期/实际/惊喜）:\n```\n" + str(ed.head(8)) + "\n```")
    except Exception:
        pass
    return "\n".join(out) or "_无财报日历数据（非美标的常见）→ 降级注明。_"


def peer_relative(symbol: str, peers: list[str], curr_date: str) -> str:
    bench = _benchmarks(symbol)
    names = [symbol] + peers + bench
    out = ["| 标的 | 1月% | 3月% | 6月% | 前瞻PE |", "|---|---:|---:|---:|---:|"]
    for n in names:
        try:
            r1, r3, r6 = _hist_returns(n, curr_date)
        except Exception:
            r1 = r3 = r6 = None
        fpe = ""
        if n not in bench:
            try:
                fpe = yf.Ticker(normalize_symbol(n)).info.get("forwardPE") or ""
                if fpe:
                    fpe = f"{float(fpe):.1f}"
            except Exception:
                fpe = ""
        tag = "(基准)" if n in bench else ""
        out.append(f"| {n}{tag} | {r1 if r1 is not None else '—'} | {r3 if r3 is not None else '—'} | "
                   f"{r6 if r6 is not None else '—'} | {fpe or '—'} |")
    note = "" if peers else "\n_未指定同业(第4参数)，仅对基准；相对估值受限。_"
    return _REALTIME_DISCLAIMER + "\n\n" + "\n".join(out) + note


# --- v3 yfinance enrichments (ownership/short-interest, earnings quality) -----

def ownership_short(symbol: str) -> str:
    """Short interest, float and institutional holdings (yfinance .info + holders)."""
    t = yf.Ticker(normalize_symbol(symbol))
    try:
        info = t.info or {}
    except Exception:
        info = {}
    pct = {"shortPercentOfFloat", "heldPercentInstitutions", "heldPercentInsiders"}
    cnt = {"sharesShort", "sharesShortPriorMonth", "floatShares", "sharesOutstanding"}
    label = {
        "sharesShort": "做空股数",
        "sharesShortPriorMonth": "上月做空股数(趋势)",
        "shortPercentOfFloat": "做空占流通比",
        "shortRatio": "回补天数 days-to-cover",
        "floatShares": "流通股 float",
        "sharesOutstanding": "总股本",
        "heldPercentInstitutions": "机构持股比",
        "heldPercentInsiders": "内部人持股比",
    }
    rows = []
    for k, lab in label.items():
        v = info.get(k)
        if v is None:
            continue
        if k in pct:
            shown = f"{float(v) * 100:.1f}%"
        elif k in cnt:
            shown = f"{float(v):,.0f}"
        else:
            shown = f"{v}"
        rows.append(f"| {lab} | {shown} |")

    out = []
    if rows:
        out += ["| 字段 | 值 |", "|---|---|", *rows]
        flags = []
        spf, sr = info.get("shortPercentOfFloat"), info.get("shortRatio")
        if spf is not None and float(spf) >= 0.10:
            flags.append(f"做空占流通 {float(spf) * 100:.1f}% 偏高 → 拥挤空头/逼空风险")
        if sr is not None and float(sr) >= 5:
            flags.append(f"回补天数 {float(sr):.1f} 天偏长 → 空头平仓不易")
        if flags:
            out.append("\n信号提示：" + "；".join(flags))
    else:
        out.append("_无做空/持股数据（非美标的常见）→ 持仓分析师需注明降级。_")
    try:
        ih = t.institutional_holders
        if ih is not None and len(ih):
            out.append("\n机构持仓(Top):\n```\n" + str(ih.head(8)) + "\n```")
    except Exception:
        pass
    try:
        mh = t.major_holders
        if mh is not None and len(mh):
            out.append("\n持股结构 major_holders:\n```\n" + str(mh) + "\n```")
    except Exception:
        pass
    return _REALTIME_DISCLAIMER + "\n\n" + "\n".join(out)


def _latest(df, *names):
    """Most-recent value of the first matching row label in a yfinance statement."""
    if df is None or not hasattr(df, "index"):
        return None
    for nm in names:
        if nm in df.index:
            row = df.loc[nm].dropna()
            if len(row):
                return float(row.iloc[0])
    return None


def earnings_quality_metrics(symbol: str, curr_date: str) -> str:
    """Accruals / cash-conversion / SBC dilution derived from quarterly statements.

    `curr_date`(D1.4 #7)PIT:直取 `yf.Ticker(...).quarterly_*` 属性绕开了
    `get_income_statement`/`get_balance_sheet`/`get_cashflow` 已经在用的
    `filter_financials_by_date`(同一裁定,同一函数)——历史回填会把**尚未发生**的报告期
    当"最新一季"读进 NI/CFO/FCF 等。复用同一把过滤器补齐,不重造一套口径。
    """
    t = yf.Ticker(normalize_symbol(symbol))

    def _stmt(attr):
        try:
            df = getattr(t, attr, None)
            return filter_financials_by_date(df, curr_date) if df is not None else None
        except Exception:
            return None

    inc = _stmt("quarterly_income_stmt")
    cf = _stmt("quarterly_cashflow")
    bs = _stmt("quarterly_balance_sheet")

    ni = _latest(inc, "Net Income", "Net Income Common Stockholders",
                 "Net Income Continuous Operations")
    rev = _latest(inc, "Total Revenue", "Operating Revenue")
    cfo = _latest(cf, "Operating Cash Flow",
                  "Cash Flow From Continuing Operating Activities",
                  "Total Cash From Operating Activities",
                  "Cash Flowsfromusedin Operating Activities Direct")
    fcf = _latest(cf, "Free Cash Flow")
    sbc = _latest(cf, "Stock Based Compensation")
    capex = _latest(cf, "Capital Expenditure")
    shares = _latest(bs, "Ordinary Shares Number", "Share Issued")

    raw = []
    for lab, val in [("净利 NI", ni), ("营收", rev), ("经营现金流 CFO", cfo),
                     ("自由现金流 FCF", fcf), ("SBC 股权激励", sbc),
                     ("资本开支 capex", capex), ("股本(股)", shares)]:
        if val is not None:
            raw.append(f"| {lab} | {val:,.0f} |")

    derived = []
    if ni is not None and cfo is not None:
        accr = ni - cfo
        tag = "NI>CFO,盈利偏非现金" if accr > 0 else "NI<CFO,现金支撑强"
        derived.append(f"| **应计 = NI − CFO** | {accr:,.0f} ({tag}; 占NI {accr / ni * 100:+.0f}%) |")
        if ni:
            derived.append(f"| 现金转化 CFO/NI | {cfo / ni:.2f} (越低盈利质量越弱) |")
    if ni and fcf is not None:
        derived.append(f"| FCF/NI | {fcf / ni:.2f} |")
    if rev and sbc is not None:
        derived.append(f"| SBC/营收 | {sbc / rev * 100:.1f}% (越高稀释压力越大) |")

    out = []
    if raw:
        out += ["最新单季原始项（单位同报表）:", "", "| 项 | 值 |", "|---|---|", *raw]
    if derived:
        out += ["", "派生质量比率:", "", "| 指标 | 读数 |", "|---|---|", *derived]
    if not out:
        return ("_盈利质量派生指标不可用（财报字段缺失/非美标的）→ 盈利质量分析师改从"
                "已取的利润表/现金流表人工读并注明降级。_")
    out.append("\n_口径=最新单季；与利润表 GAAP 摊薄 EPS 交叉看 GAAP vs 调整后缺口。_")
    return "\n".join(out)


def prediction_markets_or_websearch_note(topics: list[str]) -> str:
    """Polymarket odds per topic; if ALL topics fail at the network layer
    (Polymarket geo/SNI-blocks many clients with a TLS reset), emit a directive
    to fetch forward odds via WebSearch at reasoning time instead."""
    blocks, all_failed = [], True
    for topic in topics:
        try:
            out = get_prediction_markets.invoke({"topic": topic})
        except Exception as e:
            out = f"_(topic '{topic}' raised: {e})_"
        text = (out or "").strip()
        if "unavailable" not in text.lower() and "network error" not in text.lower():
            all_failed = False
        blocks.append(f"### {topic}\n\n{text or '_(empty)_'}")
    body = "\n\n".join(blocks)
    if all_failed:
        body += (
            "\n\n> ⚠️ **预测市场(Polymarket)全部取数失败**（环境网络层 RST/封锁，非代码问题）。\n"
            "> → 推理时改用 **WebSearch** 取前瞻赔率：FedWatch 降息概率、2026 衰退概率、"
            "标的近端催化/最新卖方动作；结果标注『实时网查 (WebSearch)』，**不计入确定性 context**。"
        )
    return body


def ashare_news_akshare(sym: str, limit: int = 12, *, start_date: str | None = None,
                        end_date: str | None = None) -> str | None:
    """East-money individual-stock news via akshare (OPTIONAL dependency).
    Returns markdown bullets, or None if akshare is absent / returns nothing.

    D1.6 #1:`stock_news_em` 不接受日期参数——返回的是"最近若干条"而非"窗内条",
    回填历史日会把窗外(更旧/更新)的条目当成当天新闻。这里按
    `start_date<=日期<=end_date` client 侧过滤 + 按标题去重(同新闻多来源转载常见)+
    按时间倒序,`limit` 在过滤/去重/排序**之后**才截断,不然窗外的行可能先占满配额。
    """
    try:
        import akshare as ak
    except ImportError:
        return None
    code = sym.split(".")[0]
    try:
        df = ak.stock_news_em(symbol=code)
    except Exception as e:
        return f"_akshare 东财新闻取数失败: {e}_"
    if df is None or not len(df):
        return None
    seen_titles: set[str] = set()
    rows: list[tuple[str, str]] = []
    for _, r in df.iterrows():
        title = str(r.get("新闻标题", "") or "").strip()
        when = str(r.get("发布时间", "") or "").strip()
        src = str(r.get("文章来源", "") or "").strip()
        if not title:
            continue
        day = when[:10]
        if start_date and day and day < start_date:
            continue
        if end_date and day and day > end_date:
            continue
        if title in seen_titles:
            continue
        seen_titles.add(title)
        rows.append((when, f"- [{when}] **{title}**" + (f" ({src})" if src else "")))
    if not rows:
        return None
    rows.sort(key=lambda t: t[0], reverse=True)
    return "\n".join(line for _, line in rows[:limit])


def ticker_news_block(ticker: str, start_date: str, end_date: str) -> str:
    """Ticker news with A-share enrichment: yfinance first; for A-shares add
    akshare (Eastmoney); if the deterministic sources are empty, emit a
    WebSearch directive for the reasoning layer to fill (same pattern as the
    Polymarket fallback)."""
    try:
        out = get_news.invoke({"ticker": ticker, "start_date": start_date, "end_date": end_date})
    except Exception as e:
        out = f"_(get_news raised: {e})_"
    yf_text = (out or "").strip()
    yf_empty = (not yf_text) or ("no news found" in yf_text.lower())
    is_cn = _is_ashare(ticker)

    parts = ["### yfinance 个股新闻\n\n"
             + (yf_text if not yf_empty else "_未抓到（A股/非美在 yfinance 新闻覆盖薄）。_")]
    ak_ok = False
    if is_cn:
        ak_news = ashare_news_akshare(normalize_symbol(ticker),
                                      start_date=start_date, end_date=end_date)
        parts.append("### akshare 东方财富个股新闻\n\n" + (ak_news or
                     "_未启用（akshare 未安装；`uv add akshare` 后得确定性东财新闻）→ 见下方 WebSearch 兜底。_"))
        ak_ok = bool(ak_news) and not ak_news.startswith("_")
    if yf_empty and not ak_ok:
        parts.append(
            f"> ⚠️ **个股新闻确定性源为空**（{ticker}）。\n"
            "> → 推理时用 **WebSearch** 取『公司中文名 + 最新消息/公告/研报/资金面』，"
            "标注『实时网查 (WebSearch)』、**不计入确定性 context**。"
        )
    return "\n\n".join(parts)


def china_backdrop(curr_date: str) -> str:
    """China macro backdrop for A-shares: RMB + China/HK index momentum (yfinance)."""
    rows = ["| 指标 | 1月% | 3月% | 6月% |", "|---|---:|---:|---:|"]
    for name, sym in [("沪深300", "000300.SS"), ("上证综指", "000001.SS"),
                      ("创业板ETF", "159915.SZ"), ("恒生指数", "^HSI"), ("USD/CNY", "CNY=X")]:
        try:
            r1, r3, r6 = _hist_returns(sym, curr_date)
        except Exception:
            r1 = r3 = r6 = None
        rows.append(f"| {name} | {r1 if r1 is not None else '—'} | "
                    f"{r3 if r3 is not None else '—'} | {r6 if r6 is not None else '—'} |")
    return ("\n".join(rows)
            + "\n\n_A股宏观底色：人民币汇率 + 中港股指动量；美国 FRED 宏观见上，作全球风险背景。_")


def _pct(x) -> str:
    """Format a possibly-None percent cell."""
    return f"{x}%" if x is not None else "n/a"


def _ak_call(fn, tries: int = 3, backoff: float = 1.5):
    """Call a flaky akshare endpoint with retries + linear backoff (RemoteDisconnected
    / rate-limiting is common on the Eastmoney/THS scrapers)."""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(backoff * (i + 1))
    raise last


def ashare_market_context(sym: str, curr_date: str) -> str | None:
    """A-share MARKET context via akshare (OPTIONAL dep): stock money-flow
    (主力净流入), Dragon-Tiger participation (龙虎榜), and limit-up sentiment
    (涨停池). Returns None if akshare is absent."""
    try:
        import akshare as ak
    except ImportError:
        return None
    code = sym.split(".")[0]
    market = {".SS": "sh", ".BJ": "bj"}.get(sym[-3:], "sz")
    out = []
    try:
        ff = _ak_call(lambda: ak.stock_individual_fund_flow(stock=code, market=market)).tail(10)
        net = ff["主力净流入-净额"].astype(float)
        cum, last, pos = net.sum() / 1e8, net.iloc[-1] / 1e8, int((net > 0).sum())
        rows = ["| 日期 | 收盘 | 涨跌% | 主力净流入(亿) | 净占比% |", "|---|---:|---:|---:|---:|"]
        for _, r in ff.tail(5).iterrows():
            rows.append(f"| {r['日期']} | {r['收盘价']} | {r['涨跌幅']} | "
                        f"{float(r['主力净流入-净额']) / 1e8:+.2f} | {r['主力净流入-净占比']} |")
        out.append(f"**主力资金流（个股）**：近10日主力净流入合计 **{cum:+.2f} 亿**"
                   f"（{pos}/10 日净流入），最新日 {last:+.2f} 亿。\n" + "\n".join(rows))
    except Exception as e:
        out.append(f"_主力资金流取数失败: {e}_")
    try:
        stat = _ak_call(lambda: ak.stock_lhb_stock_statistic_em(symbol="近三月"))
        row = stat[stat["代码"].astype(str) == code]
        if len(row):
            r = row.iloc[0]
            out.append(f"**龙虎榜（近三月）**：上榜 {r['上榜次数']} 次，最近 {r['最近上榜日']}，"
                       f"净买额 {float(r['龙虎榜净买额']) / 1e8:+.2f} 亿，机构买/卖 "
                       f"{r['买方机构次数']}/{r['卖方机构次数']} 次（席位明细可进一步查游资/机构专用）。")
        else:
            out.append("**龙虎榜（近三月）**：未上榜 → 无单日异动触发，走势更像资金温和推动/机构配置，"
                       "**无明显游资接力痕迹**。")
    except Exception as e:
        out.append(f"_龙虎榜取数失败: {e}_")
    try:
        base = datetime.strptime(curr_date, "%Y-%m-%d")
        zt = used = None
        for back in range(6):
            d = (base - timedelta(days=back)).strftime("%Y%m%d")
            try:
                z = ak.stock_zt_pool_em(date=d)
            except Exception:
                z = None
            if z is not None and len(z):
                zt, used = z, d
                break
        if zt is not None:
            maxlb = int(zt["连板数"].astype(int).max())
            hot = zt["所属行业"].value_counts().head(3)
            hot_s = "、".join(f"{k}({v})" for k, v in hot.items())
            out.append(f"**市场情绪（{used}）**：涨停 **{len(zt)}** 家、最高 **{maxlb} 连板**；"
                       f"涨停最集中行业：{hot_s}。（涨停多+连板高=情绪亢奋；少=退潮）")
    except Exception as e:
        out.append(f"_涨停池取数失败: {e}_")
    return "\n\n".join(out) if out else None


def ashare_market_context_or_note(ticker: str, curr_date: str) -> str:
    """A-share market context, or a WebSearch directive when akshare is absent."""
    body = ashare_market_context(normalize_symbol(ticker), curr_date)
    if body:
        return body
    return ("_akshare 未安装 → A股市场分析（主力资金/龙虎榜/涨停情绪）确定性源不可用。_\n\n"
            f"> → 推理时用 **WebSearch** 取『{ticker} 主力资金流向 / 龙虎榜 游资 / 所属板块情绪』，"
            "标注『实时网查 (WebSearch)』。")


def _vix_latest(curr_date: str) -> float | None:
    """VIX 收盘,PIT 锚定 curr_date(D1.4 #1)——旧版 `period="5d"` 从**真实"现在"**倒数
    5 天,回填历史日会静默读到未来的 VIX(period 不认 curr_date,只认墙钟)。

    与 `_hist_returns`/SPY regime 同一惯例:`end=curr_date+1天`(yfinance `end` 半开区间,
    +1 天才把 curr_date 自己纳入)。`.tail(2)` 只是防边界的余量,实际只取最后一行。
    """
    end = (datetime.strptime(curr_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    start = (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    vix = yf.Ticker("^VIX").history(start=start, end=end)["Close"].dropna().tail(2)
    return float(vix.iloc[-1]) if len(vix) else None


def us_market_context(ticker: str, curr_date: str) -> str:
    """US MARKET context (yfinance): SPY regime, breadth proxy (RSP/SPY), the
    stock's sector-ETF rotation, and VIX."""
    out = []
    try:
        end = (datetime.strptime(curr_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        start = (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=400)).strftime("%Y-%m-%d")
        spy = yf.Ticker("SPY").history(start=start, end=end)["Close"].dropna()
        last, ma50, ma200 = float(spy.iloc[-1]), float(spy.tail(50).mean()), float(spy.tail(200).mean())
        regime = ("risk-on（多头排列 价>50>200）" if last > ma50 > ma200
                  else "risk-off（空头排列 价<50<200）" if last < ma50 < ma200 else "震荡/混合")
        out.append(f"**大盘 regime (SPY)**：{last:.2f} vs 50DMA {ma50:.2f} / 200DMA {ma200:.2f} → **{regime}**。")
    except Exception as e:
        out.append(f"_SPY regime 取数失败: {e}_")
    try:
        spy6, rsp6 = _hist_returns("SPY", curr_date)[2], _hist_returns("RSP", curr_date)[2]
        verdict = "等权领先=广度健康" if (rsp6 or 0) >= (spy6 or 0) else "等权落后=少数权重股拉指数（窄幅领涨，脆弱）"
        out.append(f"**广度代理 (RSP 等权 vs SPY 市值权, 6月)**：RSP {_pct(rsp6)} vs SPY {_pct(spy6)} → {verdict}。")
    except Exception:
        pass
    etf = SECTOR_ETF.get(ticker.upper())
    if etf:
        try:
            e6, s6 = _hist_returns(etf, curr_date)[2], _hist_returns("SPY", curr_date)[2]
            out.append(f"**板块轮动 ({etf} vs SPY, 6月)**：{etf} {_pct(e6)} vs SPY {_pct(s6)} → "
                       + ("板块在风口" if (e6 or 0) >= (s6 or 0) else "板块跑输大盘") + "。")
        except Exception:
            pass
    try:
        v = _vix_latest(curr_date)
        if v is not None:
            out.append(f"**VIX**：{v:.1f} → "
                       + ("低波动/risk-on" if v < 18 else "高波动/避险" if v > 25 else "中性") + "。")
    except Exception:
        pass
    out.append("> 可补：用 **WebSearch** 取当日广度细节（%>200DMA、涨跌家数/新高新低）与市场基调，标注『实时网查』。")
    return "\n\n".join(out)


# --- v4: tradeability, solvency, A-share shareholder count & lockup calendar --

def _board_limit(symbol: str) -> tuple[str, float | None]:
    """Daily price-limit band for the stock's board → (label, fraction or None)."""
    sym = normalize_symbol(symbol)
    if sym.endswith(".BJ"):
        return "北交所 ±30%", 0.30
    if sym.endswith((".SS", ".SZ")):
        code = sym.split(".")[0]
        if code[:3] in ("300", "301") or code[:3] == "688":
            return "创业板/科创板 ±20%", 0.20
        return "沪深主板 ±10%（ST 为 ±5%，未自动识别）", 0.10
    return "美股无涨跌停（仅全市场熔断）", None


def tradeability_block(symbol: str, curr_date: str) -> str:
    """Liquidity (ADV) + daily price-limit reality + recent limit hits, so the PM
    can sanity-check whether a 'hard stop' is actually reachable — A-share
    limit-down lock / trading halts can make a nominal stop gap straight through."""
    sym = normalize_symbol(symbol)
    end = (datetime.strptime(curr_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    start = (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=120)).strftime("%Y-%m-%d")
    try:
        h = yf.Ticker(sym).history(start=start, end=end)[["Close", "Volume"]].dropna()
    except Exception as e:
        return f"_行情取数失败，可交易性不可用: {e}_"
    if h.empty:
        return "_无行情数据，可交易性不可用。_"
    turn = (h["Close"] * h["Volume"]).dropna()
    is_cn = sym.endswith((".SS", ".SZ", ".BJ"))
    unit, scale = ("亿元", 1e8) if is_cn else ("百万$", 1e6)
    adv20, adv60 = turn.tail(20).mean() / scale, turn.tail(60).mean() / scale
    label, frac = _board_limit(sym)
    out = [
        f"- **日均成交额 ADV**：20日 ~{adv20:,.2f} {unit} ｜ 60日 ~{adv60:,.2f} {unit}（可建/可退仓容量）。",
        f"- **涨跌停制度**：{label}。",
    ]
    if frac is not None:
        chg = h["Close"].pct_change().dropna().tail(60)
        ups, downs = int((chg >= frac - 0.005).sum()), int((chg <= -(frac - 0.005)).sum())
        out.append(f"- **近60日触板**：约 涨停 {ups} 次 / 跌停 {downs} 次"
                   f"（|日涨跌| ≥ {frac * 100:.0f}%−0.5pp 近似）。")
        out.append("- ⚠️ **止损可达性**：A股涨跌停为**硬封板**——连续跌停时**可能卖不出**，"
                   "硬止损价在跌停连环里会被**跳空穿越**；叠加**随时停牌**风险 → 名义止损 ≠ 可执行止损，"
                   "仓位/止损需为此预留缓冲（执行段须消化）。")
    else:
        out.append("- 止损可达性：美股无涨跌停、流动性通常充裕，按价位止损一般可执行（极端熔断除外）。")
    out.append("- 做空/融券：" + ("A股融券标的有限、成本高、做空表达受限" if is_cn
                                  else "美股可融券做空（借券费率视个券）") + "。")
    return "\n".join(out)


def solvency_block(symbol: str, curr_date: str) -> str:
    """Balance-sheet solvency / refinancing lens — leverage, liquidity runway,
    interest coverage, goodwill: the mechanism behind most blow-ups. A-share
    share-pledge (股权质押) is left to WebSearch (per-stock akshare is unreliable).

    `curr_date`(D1.4 #8)PIT:同 `earnings_quality_metrics` 的病(#7)—— 直取
    `yf.Ticker(...).quarterly_*` 绕开了 `filter_financials_by_date`,回填历史日会把
    未来报告期的负债/权益读成"最新"。
    """
    t = yf.Ticker(normalize_symbol(symbol))

    def _stmt(attr):
        try:
            df = getattr(t, attr, None)
            return filter_financials_by_date(df, curr_date) if df is not None else None
        except Exception:
            return None

    bs = _stmt("quarterly_balance_sheet")
    if bs is None or not hasattr(bs, "index"):
        bs = _stmt("balance_sheet")
    inc = _stmt("quarterly_income_stmt")

    debt = _latest(bs, "Total Debt", "Total Debt And Capital Lease Obligation")
    cash = _latest(bs, "Cash And Cash Equivalents",
                   "Cash Cash Equivalents And Short Term Investments")
    equity = _latest(bs, "Stockholders Equity", "Common Stock Equity",
                     "Total Equity Gross Minority Interest")
    ca = _latest(bs, "Current Assets", "Total Current Assets")
    cl = _latest(bs, "Current Liabilities", "Total Current Liabilities")
    goodwill = _latest(bs, "Goodwill", "Goodwill And Other Intangible Assets")
    ebit = _latest(inc, "EBIT", "Operating Income", "Total Operating Income As Reported")
    interest = _latest(inc, "Interest Expense", "Interest Expense Non Operating")

    rows = []
    if debt is not None:
        rows.append(f"| 总债务 Total Debt | {debt:,.0f} |")
    if cash is not None:
        rows.append(f"| 现金及等价物 | {cash:,.0f} |")
    if debt is not None and cash is not None:
        rows.append(f"| **净债务 Net Debt** | {debt - cash:,.0f} |")
    if equity:
        if debt is not None:
            rows.append(f"| 债务/权益 D/E | {debt / equity:.2f} |")
        if debt is not None and cash is not None:
            rows.append(f"| 净债务/权益 | {(debt - cash) / equity:.2f} |")
    if ca is not None and cl:
        cr = ca / cl
        rows.append(f"| 流动比率 CA/CL | {cr:.2f}（{'短期偿付吃紧' if cr < 1 else '短期偿付稳健'}；基准1.0） |")
    if ebit is not None and interest:
        cov = abs(ebit / interest)
        tag = "偏脆弱" if cov < 3 else "覆盖尚可" if cov < 8 else "覆盖充裕"
        rows.append(f"| 利息覆盖 EBIT/利息 | {cov:.1f}x（{tag}；<3 警戒） |")
    if goodwill is not None:
        gw = f"{goodwill:,.0f}"
        if equity:
            gw += f"（占权益 {goodwill / equity * 100:.0f}%，越高减值冲击越大）"
        rows.append(f"| 商誉 Goodwill | {gw} |")

    out = []
    if rows:
        out += ["| 项 | 值/读数 |", "|---|---|", *rows]
    else:
        out.append("_资产负债表字段缺失 → 偿付分析师改从已取的资产负债表/利润表人工读并注明降级。_")
    if _is_ashare(symbol):
        out.append("\n> **(A股) 股权质押率**：个股质押口径在 yfinance/akshare 不稳 → 推理时用 **WebSearch** 取"
                   "『公司名 + 控股股东 股权质押 比例』（高质押=控制权/平仓风险），标注『实时网查』。")
    out.append("\n_口径=最新报告期；净债务/利息覆盖/商誉占权益是空头的资产负债表抓手。_")
    return "\n".join(out)


def ashare_shareholder_count(sym: str) -> str:
    """A-share 股东户数 (retail-dispersion / chip-concentration proxy) via akshare
    (OPTIONAL). Falling 户数 = chips concentrating (often constructive); rising =
    retail dispersing / possible distribution near highs."""
    code = sym.split(".")[0]
    try:
        import akshare as ak
    except ImportError:
        return f"_akshare 未安装 → 股东户数不可用；推理时 WebSearch『{code} 股东户数 最新』兜底。_"
    try:
        df = _ak_call(lambda: ak.stock_zh_a_gdhs_detail_em(symbol=code))
    except Exception as e:
        return f"_股东户数取数失败: {e}（WebSearch『{code} 股东户数』兜底）_"
    if df is None or not len(df):
        return "_akshare 未返回股东户数（可能无披露）。_"

    def _col(*cands):
        for c in cands:
            if c in df.columns:
                return c
        return None

    def _cell(r, c):
        return str(r[c]) if (c and c in r and r[c] == r[c]) else "—"

    c_date = _col("股东户数统计截止日", "截止日期", "股东户数统计截止日期")
    c_now = _col("股东户数-本次", "股东户数")
    c_chg = _col("股东户数-增减", "股东户数增减")
    c_pct = _col("股东户数-增减比例", "增减比例", "股东户数增减比例")
    c_avg = _col("户均持股市值", "户均持股市值-元", "户均持股市值(元)")

    def _num(r, c, kind):
        if not c or c not in r or r[c] != r[c]:
            return "—"
        try:
            v = float(r[c])
            return {"int": f"{int(v):,}", "sign": f"{int(v):+,}",
                    "pct": f"{v:+.2f}%", "wan": f"{v / 1e4:,.1f}"}.get(kind, str(r[c]))
        except Exception:
            return str(r[c])

    recent = df.tail(4).iloc[::-1]                       # most-recent period first
    rows = ["| 截止日 | 股东户数 | 较上期 | 增减% | 户均持股市值(万) |", "|---|---:|---:|---:|---:|"]
    for _, r in recent.iterrows():
        rows.append(f"| {_cell(r, c_date)} | {_num(r, c_now, 'int')} | {_num(r, c_chg, 'sign')} | "
                    f"{_num(r, c_pct, 'pct')} | {_num(r, c_avg, 'wan')} |")
    note = ""
    try:
        pct = float(df.iloc[-1][c_pct]) if c_pct else None
        if pct is not None:
            if pct <= -2:
                note = f"最新一期户数 **{pct:+.2f}%（减少）→ 筹码集中**（常伴主力吸筹，偏积极；结合价位）。"
            elif pct >= 2:
                note = (f"最新一期户数 **{pct:+.2f}%（增加）→ 筹码分散/散户进场**"
                        "（高位放量增加=派发嫌疑，偏警示）。")
            else:
                note = f"最新一期户数变动 {pct:+.2f}%（基本平稳）。"
    except Exception:
        pass
    tail = "\n\n_户数↓=集中(偏多)、↑=分散(高位警惕派发)；看趋势而非单期，与价位/龙虎榜/主力资金流交叉。_"
    return "\n".join(rows) + (f"\n\n{note}" if note else "") + tail


def ashare_corporate_calendar(sym: str, curr_date: str) -> str:
    """A-share forward catalysts: UPCOMING share-lockup expiries 解禁 (supply
    overhang on/after curr_date) via akshare (OPTIONAL); 业绩预告/政策窗口/调样
    left to WebSearch at reasoning time."""
    code = sym.split(".")[0]
    try:
        import akshare as ak
    except ImportError:
        return (f"_akshare 未安装 → 解禁队列不可用；WebSearch『{code} 限售解禁 时间表』兜底。_\n\n"
                "> 业绩预告（A股 1月底/4月底强制）、政策窗口、指数调样 → 推理时 WebSearch 补，标注『实时网查』。")
    out = []
    try:
        rel = _ak_call(lambda: ak.stock_restricted_release_queue_em(symbol=code))
        if rel is not None and len(rel):
            cols = rel.columns

            def _col(*cands):
                return next((c for c in cands if c in cols), None)

            c_date = _col("解禁时间", "解禁日期")
            c_num = _col("解禁数量", "实际解禁数量")
            c_pct = _col("占流通市值比例", "占总市值比例")
            c_type = _col("限售股类型", "解禁类型")
            upc = rel
            if c_date:
                tmp = rel.copy()
                tmp["_d"] = tmp[c_date].astype(str)
                upc = tmp[tmp["_d"] >= curr_date].sort_values("_d")    # upcoming only, nearest first
            if len(upc):
                rows = ["| 解禁时间 | 解禁数量(万股) | 占流通市值% | 类型 |", "|---|---:|---:|---|"]
                for _, r in upc.head(6).iterrows():
                    num = f"{float(r[c_num]) / 1e4:,.0f}" if c_num and r[c_num] == r[c_num] else "—"
                    pct = f"{float(r[c_pct]) * 100:.2f}%" if c_pct and r[c_pct] == r[c_pct] else "—"
                    typ = str(r[c_type]) if c_type and r[c_type] == r[c_type] else "—"
                    rows.append(f"| {r[c_date]} | {num} | {pct} | {typ} |")
                out.append("**限售解禁队列（未来供给压力，近端在前）**：\n" + "\n".join(rows))
            else:
                out.append("**限售解禁**：未来无新解禁（队列仅历史）→ 近端无解禁供给压力。")
        else:
            out.append("**限售解禁**：akshare 未返回队列（可能无数据）。")
    except Exception as e:
        out.append(f"_解禁队列取数失败: {e}（WebSearch『{code} 限售解禁 时间表』兜底）_")
    out.append("> 业绩预告窗口（A股 1月底/4月底强制）、政策窗口（政治局会议/两会/降准降息）、"
               "指数调样 → 推理时用 **WebSearch** 补成完整催化日历，标注『实时网查』。")
    return "\n\n".join(out)


# -----------------------------------------------------------------------------

def _slug(title: str) -> str:
    """节标题 → 端点账本的兜底 slug(`analyze:` 前缀 + kebab-case)。

    只在调用方没给 `endpoint=` 时用 —— 宁可粗(泛用 slug)也不可错(悄悄不记账)。
    """
    return "analyze:" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _section(title: str, fn, *args, endpoint: str | None = None, **kwargs) -> str:
    """Run one data call, capturing output or a readable error per section.

    异常 → **先记账(B 级降级,`contracts.record_degradation`)再**写人读的降级文案 —— 29 处
    T 级(裸报错,只有人读)升 B 级(可审计,`degradations()`/`main()` 尾账都能读到,D1.3)。
    """
    print(f"  - {title} ...", flush=True)
    try:
        out = fn.invoke(*args, **kwargs) if hasattr(fn, "invoke") else fn(*args, **kwargs)
        body = (out or "").strip() or "_(empty)_"
    except Exception as e:  # noqa: BLE001 — one flaky vendor must not kill the harvest
        from autoresearch.data.contracts import record_degradation
        record_degradation(endpoint or _slug(title), f"{type(e).__name__}: {e}")
        body = f"_ERROR fetching this section: {e}_\n```\n{traceback.format_exc()}```"
    return f"\n## {title}\n\n{body}\n"


# --- scan-market L4:复用 L1 召回因子行,消除富因子(主力/技术/筹码/北向)重复取数 -----

def _l1_float(row: dict, key: str) -> float | None:
    """Float from an L1 row dict, or None if absent/NaN."""
    try:
        f = float(row.get(key))
    except (TypeError, ValueError):
        return None
    return None if f != f else f   # NaN != NaN


def _l1_flag(row: dict, key: str) -> bool | None:
    """Bool-ish L1 flag (ma_bull/above_ma60 persisted as 0/1/True/False/是)."""
    v = row.get(key)
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "是", "yes")
    try:
        return bool(float(v))
    except (TypeError, ValueError):
        return None


def _load_l1_row(ticker: str, trade_date: str, root: Path | None = None) -> dict | None:
    """This ticker's L1 召回因子行 from scan artifacts (L1_scored_full superset,
    fallback L1_recall_top1000). None when no scan ran that date / code absent →
    caller falls back to the live tushare fetch (standalone lite / 全量 analyze)。"""
    code = normalize_symbol(ticker).split(".")[0].zfill(6)
    base = (root or (ROOT / ws.scan_root())) / trade_date
    for fname in ("L1_scored_full.csv", "L1_recall_top1000.csv"):
        fp = base / fname
        if not fp.exists():
            continue
        try:
            df = pd.read_csv(fp, dtype={"code": str})
        except Exception:  # noqa: BLE001 — 坏文件 → 当未命中,走 live
            continue
        df["code"] = df["code"].astype(str).str.zfill(6)
        hit = df[df["code"] == code]
        if len(hit):
            return hit.iloc[0].to_dict()
    return None


def ashare_market_context_from_l1(row: dict) -> str:
    """用 L1 召回因子行重建『主力/技术/筹码/北向』块 —— 与召回打分同源、零重复取数。

    L1(screen_market)已对全市场取过 tushare 富因子并落盘;scan-market L4 决策卡直接复用
    该行,不再二次 round-trip(单一真值,L4 与召回数字一致)。10 日资金序列 / MACD 金叉死叉 /
    股东户数趋势等 L1 未存的细节,如需 → 对该票跑全量 analyze-ticker。

    D1.6 #6:`row` 缺 `L1_REUSE_COLUMNS`(single source,`contracts/agent_output.py`)
    里的列时,原先静默回退(该项就是缺该行数据,读者看不出是"L1 没存"还是"这票真没有")——
    现在缺列仍回退渲染(不炸),但先 `record_degradation("L1_scored_full", ...,
    kind="legit_empty")` 留痕。
    """
    missing = [c for c in L1_REUSE_COLUMNS if c not in row]
    if missing:
        from autoresearch.data.contracts import record_degradation
        record_degradation("L1_scored_full", f"列缺失: {missing}",
                           key=str(row.get("code", "")), kind="legit_empty")
    out: list[str] = []

    # 0) L1 召回打分(复合分 + 8 子分):卡片自带召回理由
    comp = _l1_float(row, "composite")
    if comp is not None:
        subs = [("动量", "score_momentum"), ("主力", "score_fund_main"),
                ("散户", "score_fund_retail"), ("筹码", "score_chip"),
                ("北向", "score_north"), ("技术", "score_tech"),
                ("成长", "score_growth"), ("价值", "score_value")]
        cells = []
        for lab, k in subs:
            v = _l1_float(row, k)
            if v is not None:
                cells.append(f"{lab} {v:.0f}")
        out.append(f"**L1 召回复合分 {comp:.1f}**(行业条件化,0–100)｜子分:" + " / ".join(cells))

    # 1) 主力资金流(L1:最新主力净流入 + 净占比;10日序列见全量)
    main_yi, main_ratio, retail_yi = (_l1_float(row, k) for k in
                                      ("main_inflow_yi", "main_net_ratio", "retail_net_yi"))
    if main_yi is not None or main_ratio is not None:
        bits = []
        if main_yi is not None:
            bits.append(f"最新主力净流入 **{main_yi:+.2f} 亿**")
        if main_ratio is not None:
            bits.append(f"主力净占比 **{main_ratio * 100:+.1f}%**({'净流入' if main_ratio > 0 else '净流出'})")
        if retail_yi is not None:
            bits.append(f"散户(小单)净 {retail_yi:+.2f} 亿")
        out.append("**主力资金流(L1·tushare moneyflow)**:" + "、".join(bits) + "。")

    # 2) 技术结构(L1:多头排列/价在MA60/RSI)
    bull, above60 = _l1_flag(row, "ma_bull"), _l1_flag(row, "above_ma60")
    rsi6, rsi12 = _l1_float(row, "rsi6"), _l1_float(row, "rsi12")
    if bull is not None or above60 is not None or rsi6 is not None:
        bits = []
        if bull is not None:
            bits.append(f"多头排列 **{'是' if bull else '否'}**")
        if above60 is not None:
            bits.append(f"价在 MA60 **{'上方' if above60 else '下方'}**")
        if rsi6 is not None:
            bits.append(f"RSI6 **{rsi6:.0f}**({'过热' if rsi6 > 80 else '超卖' if rsi6 < 20 else '中性'})")
        if rsi12 is not None:
            bits.append(f"RSI12 {rsi12:.0f}")
        out.append("**技术结构(L1·stk_factor_pro 前复权)**:" + "、".join(bits)
                   + "。_(MACD 金叉/死叉明细见全量 analyze-ticker)_")

    # 3) 筹码(L1:获利比例/集中度/相对成本)
    wr, conc, ptc = (_l1_float(row, k) for k in ("winner_rate", "chip_concentration", "price_to_cost"))
    close = _l1_float(row, "close")
    if wr is not None or conc is not None or ptc is not None:
        bits = []
        if wr is not None:
            t = "高位获利盘重(抛压/见顶风险)" if wr > 85 else "深度套牢/超跌(上行有空间)" if wr < 15 else "中性"
            bits.append(f"获利比例 **{wr:.0f}%**({t})")
        if ptc is not None:
            cost50 = (close / ptc) if (close is not None and ptc) else None
            bits.append(f"现价/平均成本 **{ptc:.2f}**({'浮盈' if ptc > 1 else '浮亏'}"
                        + (f",均成本 {cost50:.2f}" if cost50 is not None else "") + ")")
        if conc is not None:
            bits.append(f"筹码集中度 {conc:.2f}")
        out.append("**筹码(L1·cyq_perf)**:" + "、".join(bits) + "。")

    # 4) 北向(L1:沪深股通持股占比;多为小盘 NaN)
    hk = _l1_float(row, "hk_ratio")
    out.append(f"**北向(沪深股通,L1)**:持股占比 **{hk:.2f}%**(聪明钱仓位)。" if hk is not None
               else "**北向(沪深股通,L1)**:非标的/无持股记录(小盘常见)。")

    body = "\n\n".join(out) if out else "_L1 召回行无富因子字段(异常)。_"
    return (body + "\n\n_复用 L1 召回因子(与召回打分同源,零重复取数);"
            "10 日资金序列 / MACD / 股东户数趋势如需 → 跑全量 analyze-ticker。_")


# --- A股富化:优先 tushare(绕开被封的东财 push2),失败回退 akshare ---------------

def ashare_market_context_best(ticker: str, curr_date: str) -> str:
    """A股市场上下文:tushare(主力/技术/筹码/北向)优先,失败回退 akshare(资金/龙虎榜/涨停)。"""
    try:
        from autoresearch.data.tushare_enrich import ashare_market_context_ts
        b = ashare_market_context_ts(normalize_symbol(ticker), curr_date)
        if b:
            return b + "\n\n_(tushare 源;akshare 东财 push2 在本机被网络封锁时自动走此路。)_"
    except Exception:  # noqa: BLE001 — tushare 不可用 → 回退
        pass
    return ashare_market_context_or_note(ticker, curr_date)


def ashare_shareholder_best(ticker: str, curr_date: str) -> str:
    """股东户数:tushare(含质押爆雷红旗)优先,失败回退 akshare。"""
    try:
        from autoresearch.data.tushare_enrich import ashare_shareholder_ts
        b = ashare_shareholder_ts(normalize_symbol(ticker), curr_date=curr_date)
        if b:
            return b
    except Exception:  # noqa: BLE001
        pass
    return ashare_shareholder_count(normalize_symbol(ticker))


def ashare_calendar_best(ticker: str, curr_date: str) -> str:
    """A股日历:tushare(业绩预告/快报=前瞻成长)+ akshare(解禁),各自降级。"""
    parts: list[str] = []
    try:
        from autoresearch.data.tushare_enrich import ashare_calendar_ts
        b = ashare_calendar_ts(normalize_symbol(ticker), curr_date)
        if b:
            parts.append(b)
    except Exception:  # noqa: BLE001
        pass
    try:
        ak_cal = ashare_corporate_calendar(normalize_symbol(ticker), curr_date)
        if ak_cal:
            parts.append(ak_cal)
    except Exception:  # noqa: BLE001
        pass
    return "\n\n".join(parts) if parts else "_A股日历(业绩预告/解禁)暂不可用。_"


# --- UZI 增量透镜(L4 单票深研:A股原生财报 / 融资趋势 / 龙虎榜席位 / 杀猪盘)---

def _uzi_fundamentals(ticker: str, curr_date: str) -> str:
    from autoresearch.common.uzi_lenses import ashare_fundamentals_ts
    return (ashare_fundamentals_ts(ticker, curr_date=curr_date)
            or "_UZI A股原生财报暂不可用(非A股/取数失败)。_")


def _uzi_margin(ticker: str, curr_date: str) -> str:
    from autoresearch.common.uzi_lenses import margin_trend_ts
    return margin_trend_ts(ticker, curr_date=curr_date) or "_非两融标的或融资数据暂无。_"


def _uzi_seats(ticker: str, curr_date: str) -> str:
    from autoresearch.common.uzi_lenses import lhb_seats
    return lhb_seats(ticker, curr_date) or "_龙虎榜席位数据暂不可用。_"


def _uzi_trap(row: dict) -> str:
    from autoresearch.common.uzi_lenses import render_trap_block, trap_signals
    return render_trap_block(trap_signals(row))


def _uzi_volprice(row: dict) -> str:
    """量价形态(position-conditioned 吸筹/派发)+ 多日资金流(CMF/OBV,若 L1 行带 vol_series 因子)。

    补 trap(派发空半)缺的**吸筹多半**:顶部放量=派发、底部放量=吸筹——裸量比对 T+1 负正因没分位置。
    """
    from autoresearch.common.uzi_lenses import render_volume_price_block, volume_price_signals
    block = render_volume_price_block(volume_price_signals(row))
    extra = []
    for key, lab, pos, neg in (("cmf_20", "CMF·20日买卖压", "买压/吸筹侧", "卖压/派发侧"),
                               ("obv_mom_20", "OBV·20日资金方向", "资金净进", "资金净出")):
        try:
            v = float(row.get(key))
        except (TypeError, ValueError):
            continue
        if v != v:           # NaN
            continue
        extra.append(f"{lab} {v:+.2f}({pos if v > 0 else neg})")
    if extra:
        block += ("\n**多日量价资金流(vol_series·IC 实证 decile +40bps/t≈2)**:"
                  + " ｜ ".join(extra) + " — 与上面快照位置共振更可信,仍须基本面背书。")
    return block


_P4_DEEP_TITLES = ("Income statement", "Earnings quality", "Solvency")
_P4_POINTER = ("\n<!-- P4 深核分界:深核块(利润表全表/盈利质量/偿付)已拆到同目录 `{deep_name}`。"
               "P1–P3/早停不读;survivor 进 P4 才 Read -->\n")


def _split_slim_for_progressive(parts: list[str]) -> tuple[list[str], list[str]]:
    """slim 二段式:深核块(P4 陷阱维)与表面块分离,表面保序。早停率 ~90% 下深核随文件
    推送 = 多数卡白烧;survivor 用 Read 按需拉 deep 文件。无深核 → deep 空表。"""
    def _is_deep(p: str) -> bool:
        head = p[:120]
        return any(f"## {t}" in head for t in _P4_DEEP_TITLES)

    deep = [p for p in parts if _is_deep(p)]
    surface = [p for p in parts if not _is_deep(p)]
    return surface, deep


def _write_slim_files(out_dir: Path, ticker: str, trade_date: str, parts: list[str]) -> Path:
    """slim 落盘(二段式):表面块写 *_slim.md(尾插 deep 指针),深核块写 *_slim_deep.md。
    无深核块 → 只写单文件不插指针(老路不破)。纯函数式落盘,可 tmp_path 测。"""
    surface, deep = _split_slim_for_progressive(parts)
    out_path = out_dir / f"{ticker}_{trade_date}_slim.md"
    if deep:
        deep_path = out_dir / f"{ticker}_{trade_date}_slim_deep.md"
        deep_path.write_text("\n".join([
            f"# Deep 深核块(P4 陷阱核用) — {ticker} @ {trade_date}\n",
            "_survivor 进 P4 才 Read 本文件;早停卡不读。_\n", *deep]), encoding="utf-8")
        surface = [*surface, _P4_POINTER.format(deep_name=deep_path.name)]
    out_path.write_text("\n".join(surface), encoding="utf-8")
    return out_path


# ───────────────────────── D1.6 #2:fwd-PE 补价 ─────────────────────────

#: 从「Verified market snapshot」`_section` 已渲染出的正文里抠 Close —— 同进程已取的
#: 价,不再二次网络往返(同款正则,与 `scan/l4/producers._SLIM_CLOSE_RE` 各自独立维护:
#: 后者读**落盘的 slim 文件**,这里读**同一进程内存里刚生成的 body 字符串**,场景不同,
#: 不强并成单源)。
_SNAPSHOT_CLOSE_RE = re.compile(r"\|\s*Close\s*\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|")


def _snapshot_close(section_text: str) -> float | None:
    """已渲染的 verified-snapshot 节文本 →「Latest verified OHLCV row」表里的 Close。"""
    m = _SNAPSHOT_CLOSE_RE.search(section_text or "")
    return float(m.group(1)) if m else None


def _consensus_eps_price(l1_row: dict | None, snapshot_section: str) -> float | None:
    """A股卖方一致预期 fwd-PE 算价用的 price=:优先 L1 复用行的 close(slim 同源,已在手);
    否则退到本进程已取的 verified-snapshot close(非 slim / 无 L1 行时的唯一价来源 ——
    此前这条路 price 恒 None,fwd-PE 列永不出现,D1.6 #2)。"""
    px = _l1_float(l1_row, "close") if l1_row is not None else None
    return px if px is not None else _snapshot_close(snapshot_section)


def _output_dir(trade_date: str, *, slim: bool, explicit: Path | None = None) -> Path:
    if explicit is not None and not slim:
        raise ValueError("--out-dir 仅支持 --slim，不得迁移 full 报告")
    relative = explicit if explicit is not None else (
        ws.scan_input_dir(trade_date) if slim else ws.context_root())
    out_dir = ROOT / relative
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def main() -> int:
    # D1.6 #4:离线开关(措辞对齐 data/sources/yf_options.py 等既有 AUTORESEARCH_OFFLINE=1
    # 语义)——离线模式下不取任何网络,提前退出,免得跑一半才在各处炸成一串降级账。
    if os.environ.get("AUTORESEARCH_OFFLINE"):
        print("[harvest] AUTORESEARCH_OFFLINE=1:离线模式不取网络,提前退出", flush=True)
        return 2
    args = list(sys.argv[1:])
    explicit_out_dir = None
    if "--out-dir" in args:
        if args.count("--out-dir") != 1:
            raise ValueError("--out-dir 只能指定一次")
        option_index = args.index("--out-dir")
        if option_index + 1 >= len(args) or args[option_index + 1].startswith("--"):
            raise ValueError("--out-dir 缺 PATH")
        explicit_out_dir = Path(args[option_index + 1])
        del args[option_index:option_index + 2]
    # D1.6 #3:A股中文简称(同 `analyze.assemble --name` 的约定,Claude 在 session 内已知,
    # 显式传最稳)——喂 gnews zh 查询词,不然只能退到 yfinance longName(A 股常是英文/拼音)。
    explicit_name = None
    if "--name" in args:
        if args.count("--name") != 1:
            raise ValueError("--name 只能指定一次")
        name_index = args.index("--name")
        if name_index + 1 >= len(args) or args[name_index + 1].startswith("--"):
            raise ValueError("--name 缺 NAME")
        explicit_name = args[name_index + 1]
        del args[name_index:name_index + 2]
    flags = {a for a in args if a.startswith("--")}
    pos = [a for a in args if not a.startswith("--")]
    if not pos:
        print(__doc__)
        return 1
    # --slim:轻量模式,只 harvest 决策驱动块(scan-market L3b / analyze-ticker-lite 用),体积/token 大幅下降
    slim = "--slim" in flags
    ticker = normalize_symbol(pos[0])   # 入口归一(.SH→.SS 等):取数/staging 文件名/下游指针三处口径一致
    trade_date = pos[1] if len(pos) > 1 else date.today().isoformat()
    d = datetime.strptime(trade_date, "%Y-%m-%d")
    asset_type = pos[2] if len(pos) > 2 else "stock"
    peers_arg = pos[3] if len(pos) > 3 else ""
    peers = [normalize_symbol(p.strip()) for p in peers_arg.split(",") if p.strip()] \
        or PEER_MAP.get(ticker.upper(), [])
    out_dir = _output_dir(trade_date, slim=slim, explicit=explicit_out_dir)

    set_config(DEFAULT_CONFIG)

    end = trade_date
    price_start = (d - timedelta(days=400)).strftime("%Y-%m-%d")  # >200 trading days for 200 SMA
    news_start = (d - timedelta(days=14)).strftime("%Y-%m-%d")

    print(f"[harvest v4{' SLIM' if slim else ''}] {ticker} @ {trade_date} "
          f"(asset_type={asset_type}, peers={peers or 'none'})", flush=True)

    identity = resolve_instrument_identity(ticker)
    instrument_context = build_instrument_context(ticker, asset_type, identity)

    parts: list[str] = [
        f"# Data context — {ticker} @ {trade_date}\n",
        f"_Harvested {datetime.now().isoformat(timespec='seconds')} via project data tools + yfinance v2 "
        f"enrichments. No LLM used._\n",
        f"\n## Instrument identity\n\n{instrument_context}\n",
    ]

    print("[market]", flush=True)
    if not slim:  # OHLCV 400天(最大块)+ 30天指标多序列:slim 用 snapshot 的当前指标值即可(去冗余)
        parts.append(_section(
            f"Price history (OHLCV) {price_start} → {end}",
            get_stock_data, {"symbol": ticker, "start_date": price_start, "end_date": end},
            endpoint="yfinance"))
        parts.append(_section(
            "Technical indicators (full menu)",
            get_indicators, {"symbol": ticker, "indicator": ",".join(INDICATORS),
                             "curr_date": end, "look_back_days": 30},
            endpoint="analyze:yf-technical-indicators"))
    # 变量留手:同一节的文本供本函数末尾 fwd-PE 补价复用(D1.6 #2,零二次网络往返)。
    _snapshot_section = _section(
        "Verified market snapshot (source of truth)",
        get_verified_market_snapshot, {"symbol": ticker, "curr_date": end, "look_back_days": 30},
        endpoint="analyze:verified-snapshot")
    parts.append(_snapshot_section)
    if _is_ashare(ticker):
        # scan-market L4:有 L1 召回行 → 复用(零富因子重复取数,与召回同源);
        # 全量 analyze-ticker / 无 scan → live tushare(10日资金序列+MACD 更全)。
        l1_row = _load_l1_row(ticker, trade_date) if slim else None
        if l1_row is not None:
            parts.append(_section("Market context — A股 (主力/技术/筹码/北向 · 复用L1召回)",
                                  ashare_market_context_from_l1, l1_row,
                                  endpoint="analyze:l1-market-context"))
        else:
            parts.append(_section("Market context — A股 (主力/技术/筹码/北向)",
                                  ashare_market_context_best, ticker, end,
                                  endpoint="analyze:ashare-market-context"))
    else:
        parts.append(_section("Market context — US (regime/breadth/sector/VIX)",
                              us_market_context, ticker, end,
                              endpoint="analyze:yf-us-market-context"))
    parts.append(_section("Tradeability & price-limit reality (v4)",
                          tradeability_block, ticker, end, endpoint="analyze:tradeability"))

    print("[news / social]", flush=True)
    parts.append(_section(
        f"Ticker news {news_start} → {end}",
        ticker_news_block, ticker, news_start, end, endpoint="analyze:ticker-news"))
    if not slim:  # 全球宏观新闻 / 内部交易 / 持仓做空:决策卡用不上
        parts.append(_section("Global / macro news", get_global_news, {"curr_date": end}))
        parts.append(_section("Insider transactions", get_insider_transactions, {"ticker": ticker},
                              endpoint="analyze:yf-insider-transactions"))
        parts.append(_section("Ownership & short interest (v3)", ownership_short, ticker,
                              endpoint="analyze:yf-ownership-short"))
    if _is_ashare(ticker):
        parts.append(_section("股东户数 / 质押 (A股, v4)",
                              ashare_shareholder_best, ticker, end,
                              endpoint="analyze:ashare-shareholder"))
        # UZI 增量透镜:便宜的(财报1调/融资1调/trap零调)slim 也取;席位识别(多日 top_inst)给全量
        parts.append(_section("A股原生财报 (UZI·tushare)", _uzi_fundamentals, ticker, end,
                              endpoint="analyze:uzi-fundamentals"))
        parts.append(_section("融资余额趋势 (UZI·tushare)", _uzi_margin, ticker, end,
                              endpoint="analyze:uzi-margin"))
        # 量价机械底(**仅 scan L4 的 slim 路径**复用 L1 因子行,零取数):trap=派发空半 + volprice=吸筹多半 + 多日 CMF/OBV。
        # 全量 analyze-ticker 与 scan **完全解耦——不取 L1**,改由分析师对上方 live 市场上下文(主力/技术/筹码)自行套用 trap/volume_price 判读。
        if l1_row is not None:
            parts.append(_section("杀猪盘/派发风险 (UZI·复用L1)", _uzi_trap, l1_row,
                                  endpoint="analyze:uzi-trap"))
            parts.append(_section("量价形态/吸筹·多日资金流 (UZI·复用L1)", _uzi_volprice, l1_row,
                                  endpoint="analyze:uzi-volprice"))
        if not slim:
            parts.append(_section("龙虎榜席位识别 (UZI·tushare)", _uzi_seats, ticker, end,
                                  endpoint="analyze:uzi-seats"))

    if not slim:  # 8 个 FRED 宏观 + 中国背景 + 预测市场:对单只决策卡是背景噪音
        print("[macro]", flush=True)
        for series in MACRO:
            parts.append(_section(f"Macro: {series}", get_macro_indicators,
                                  {"indicator": series, "curr_date": end}, endpoint="fred"))
        if _is_ashare(ticker):
            parts.append(_section("China market backdrop (A-share)", china_backdrop, end,
                                  endpoint="analyze:yf-china-backdrop"))

        print("[prediction markets]", flush=True)
        parts.append(_section("Prediction markets (Polymarket; WebSearch fallback if blocked)",
                              prediction_markets_or_websearch_note, PREDICTION_TOPICS,
                              endpoint="analyze:polymarket"))

    print("[fundamentals]", flush=True)
    parts.append(_section("Fundamentals overview", get_fundamentals, {"ticker": ticker, "curr_date": end},
                          endpoint="analyze:yf-fundamentals"))
    parts.append(_section("Income statement (quarterly)", get_income_statement,
                          {"ticker": ticker, "freq": "quarterly", "curr_date": end},
                          endpoint="analyze:yf-income-statement"))
    if not slim:  # 资产负债表/现金流量表全表:slim 用 solvency + earnings-quality 的摘要替代
        parts.append(_section("Balance sheet (quarterly)", get_balance_sheet,
                              {"ticker": ticker, "freq": "quarterly", "curr_date": end},
                              endpoint="analyze:yf-balance-sheet"))
        parts.append(_section("Cash flow (quarterly)", get_cashflow,
                              {"ticker": ticker, "freq": "quarterly", "curr_date": end},
                              endpoint="analyze:yf-cashflow"))
    parts.append(_section("Earnings quality / forensics (v3)", earnings_quality_metrics, ticker, end,
                          endpoint="analyze:yf-earnings-quality"))
    parts.append(_section("Solvency & refinancing (v4)", solvency_block, ticker, end,
                          endpoint="analyze:yf-solvency"))

    # --- v2 enrichments (yfinance direct; US-centric, degrade gracefully) ---
    print("[v2: analyst / earnings / calendar]", flush=True)
    if not slim:  # 期权链(A股空)+ 外源扩面(EDGAR/分析师行动/映射/gnews):决策卡不需要
        # 派发清单的单一事实源 = `external_sections`(slim 那边它自己也返回空,两道门)。
        for _title, _fn, _args, _kw in external_sections(
                ticker, end, slim=slim,
                company_name=_company_query_name(explicit_name, identity)):
            parts.append(_opt_section(_title, _fn, *_args, **_kw))
    parts.append(_section("Analyst consensus & price targets (v2)", analyst_consensus, ticker,
                          endpoint="analyze:yf-analyst-consensus"))
    parts.append(_section("Earnings & events calendar (v2)", earnings_calendar, ticker,
                          endpoint="analyze:yf-earnings-calendar"))
    if _is_ashare(ticker):
        parts.append(_section("Corporate calendar — A股 业绩预告·快报/解禁 (v4)",
                              ashare_calendar_best, ticker, end, endpoint="analyze:ashare-calendar"))
        # A股卖方一致预期 EPS → 真 fwd-PE(补 yfinance 对 A 股 forwardPE 的缺口;同花顺 keyless)。
        # D1.6 #2:price= 优先 L1 复用行 close,否则退到同进程已取的 snapshot close ——
        # 此前非 slim(全量 analyze-ticker)路径 l1_row 恒 None,price 恒 None,fwd-PE 列永不出现。
        _eps_px = _consensus_eps_price(l1_row, _snapshot_section)
        parts.append(_section("A股卖方一致预期 EPS / fwd-PE (同花顺·keyless)",
                              consensus_eps_block, ticker, _eps_px,
                              endpoint="analyze:keyless-consensus-eps"))
    if not slim:
        parts.append(_section("Peer-relative valuation & strength (v2)", peer_relative, ticker, peers, end,
                              endpoint="analyze:yf-peer-relative"))

    if slim:
        out_path = _write_slim_files(out_dir, ticker, trade_date, parts)
    else:
        out_path = out_dir / f"{ticker}_{trade_date}.md"
        out_path.write_text("".join(parts), encoding="utf-8")
    print(f"\n[saved] {out_path}  ({out_path.stat().st_size:,} bytes)", flush=True)

    from autoresearch.data.contracts import degradations, render as _deg_render
    degs = degradations()
    if degs:
        print(f"[数据降级账] {len(degs)} 条:{_deg_render(degs)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

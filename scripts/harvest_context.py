"""Deterministic data harvester for the "Claude-as-engine" workflow (v2).

Calls the SAME data tools the real TradingAgents analysts call (via
``@tool`` wrappers -> ``route_to_vendor`` -> yfinance / FRED / Polymarket),
PLUS four v2 enrichments fetched directly from yfinance (options/IV,
analyst consensus, earnings calendar, peer-relative), for one ticker + date,
and dumps every raw output to a single markdown file. No LLM is instantiated,
so this needs NO paid LLM API key — only free data vendors (yfinance/Polymarket
are keyless; FRED needs FRED_API_KEY).

The v2 enrichments are US-centric: options chains, analyst coverage, and
earnings calendars are sparse-to-absent for many non-US tickers on yfinance,
so each block degrades gracefully and says so.

Usage:
    python scripts/harvest_context.py TICKER [YYYY-MM-DD] [stock|crypto] [PEER1,PEER2,...]
"""

import os
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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

import yfinance as yf  # noqa: E402

from tradingagents.agents.utils.agent_utils import (  # noqa: E402
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
from tradingagents.dataflows.config import set_config  # noqa: E402
from tradingagents.dataflows.symbol_utils import normalize_symbol  # noqa: E402
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402

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
SECTOR_ETF = {t: "SOXX" for t in ("NVDA", "AMD", "AVGO", "MU", "INTC", "TSM", "QCOM", "TXN")}


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


def options_iv_summary(symbol: str, curr_date: str) -> str:
    t = yf.Ticker(normalize_symbol(symbol))
    expiries = list(getattr(t, "options", []) or [])
    if not expiries:
        return "_该标的在 yfinance 无挂牌期权（A股/港股等常见）→ 期权/IV/定位信号不可用，催化剂&定位分析师需注明降级。_"
    future = [e for e in expiries if e >= curr_date] or expiries
    expiry = future[0]
    chain = t.option_chain(expiry)
    calls, puts = chain.calls.copy(), chain.puts.copy()
    spot = _spot(t)
    if spot is None or calls.empty or puts.empty:
        return f"_期权数据稀疏（到期 {expiry}），无法计算 ATM/隐含波动。_"
    calls["d"] = (calls["strike"] - spot).abs()
    puts["d"] = (puts["strike"] - spot).abs()
    ac = calls.nsmallest(1, "d").iloc[0]
    ap = puts.nsmallest(1, "d").iloc[0]
    atm_iv = (float(ac["impliedVolatility"]) + float(ap["impliedVolatility"])) / 2 * 100
    straddle = float(ac.get("lastPrice", 0)) + float(ap.get("lastPrice", 0))
    implied_move = (straddle / spot * 100) if spot else None
    call_oi = float(calls["openInterest"].fillna(0).sum())
    put_oi = float(puts["openInterest"].fillna(0).sum())
    pc_oi = round(put_oi / call_oi, 2) if call_oi else None
    return "\n".join([
        f"- 现价 spot: {spot:.2f} ｜ 最近到期: {expiry}",
        f"- ATM 隐含波动率(年化): {atm_iv:.1f}%",
        f"- 到期前隐含波动幅度(ATM跨式/现价): ±{implied_move:.1f}%" if implied_move else "- 隐含波动幅度: n/a",
        f"- Put/Call 未平仓比(OI): {pc_oi}  (>1 偏防御/看空持仓, <1 偏看多)" if pc_oi is not None else "- Put/Call OI: n/a",
        f"- 总未平仓: calls {call_oi:,.0f} / puts {put_oi:,.0f}",
    ])


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
    return "\n".join(out)


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
    bench = ["SPY"] + ([SECTOR_ETF[symbol.upper()]] if symbol.upper() in SECTOR_ETF else [])
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
    return "\n".join(out) + note


# -----------------------------------------------------------------------------

def _section(title: str, fn, *args, **kwargs) -> str:
    """Run one data call, capturing output or a readable error per section."""
    print(f"  - {title} ...", flush=True)
    try:
        out = fn.invoke(*args, **kwargs) if hasattr(fn, "invoke") else fn(*args, **kwargs)
        body = (out or "").strip() or "_(empty)_"
    except Exception as e:  # noqa: BLE001 — one flaky vendor must not kill the harvest
        body = f"_ERROR fetching this section: {e}_\n```\n{traceback.format_exc()}```"
    return f"\n## {title}\n\n{body}\n"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    ticker = sys.argv[1]
    trade_date = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
    asset_type = sys.argv[3] if len(sys.argv) > 3 else "stock"
    peers_arg = sys.argv[4] if len(sys.argv) > 4 else ""
    peers = [p.strip() for p in peers_arg.split(",") if p.strip()] or PEER_MAP.get(ticker.upper(), [])

    set_config(DEFAULT_CONFIG)

    end = trade_date
    d = datetime.strptime(trade_date, "%Y-%m-%d")
    price_start = (d - timedelta(days=400)).strftime("%Y-%m-%d")  # >200 trading days for 200 SMA
    news_start = (d - timedelta(days=14)).strftime("%Y-%m-%d")

    print(f"[harvest v2] {ticker} @ {trade_date} (asset_type={asset_type}, peers={peers or 'none'})", flush=True)

    identity = resolve_instrument_identity(ticker)
    instrument_context = build_instrument_context(ticker, asset_type, identity)

    parts: list[str] = [
        f"# Data context — {ticker} @ {trade_date}\n",
        f"_Harvested {datetime.now().isoformat(timespec='seconds')} via project data tools + yfinance v2 "
        f"enrichments. No LLM used._\n",
        f"\n## Instrument identity\n\n{instrument_context}\n",
    ]

    print("[market]", flush=True)
    parts.append(_section(
        f"Price history (OHLCV) {price_start} → {end}",
        get_stock_data, {"symbol": ticker, "start_date": price_start, "end_date": end}))
    parts.append(_section(
        "Technical indicators (full menu)",
        get_indicators, {"symbol": ticker, "indicator": ",".join(INDICATORS),
                         "curr_date": end, "look_back_days": 30}))
    parts.append(_section(
        "Verified market snapshot (source of truth)",
        get_verified_market_snapshot, {"symbol": ticker, "curr_date": end, "look_back_days": 30}))

    print("[news / social]", flush=True)
    parts.append(_section(
        f"Ticker news {news_start} → {end}",
        get_news, {"ticker": ticker, "start_date": news_start, "end_date": end}))
    parts.append(_section("Global / macro news", get_global_news, {"curr_date": end}))
    parts.append(_section("Insider transactions", get_insider_transactions, {"ticker": ticker}))

    print("[macro]", flush=True)
    for series in MACRO:
        parts.append(_section(f"Macro: {series}", get_macro_indicators,
                              {"indicator": series, "curr_date": end}))

    print("[prediction markets]", flush=True)
    for topic in PREDICTION_TOPICS:
        parts.append(_section(f"Prediction markets: {topic}", get_prediction_markets, {"topic": topic}))

    print("[fundamentals]", flush=True)
    parts.append(_section("Fundamentals overview", get_fundamentals, {"ticker": ticker, "curr_date": end}))
    parts.append(_section("Income statement (quarterly)", get_income_statement,
                          {"ticker": ticker, "freq": "quarterly", "curr_date": end}))
    parts.append(_section("Balance sheet (quarterly)", get_balance_sheet,
                          {"ticker": ticker, "freq": "quarterly", "curr_date": end}))
    parts.append(_section("Cash flow (quarterly)", get_cashflow,
                          {"ticker": ticker, "freq": "quarterly", "curr_date": end}))

    # --- v2 enrichments (yfinance direct; US-centric, degrade gracefully) ---
    print("[v2: options / analyst / earnings / peers]", flush=True)
    parts.append(_section("Options & implied volatility (v2)", options_iv_summary, ticker, end))
    parts.append(_section("Analyst consensus & price targets (v2)", analyst_consensus, ticker))
    parts.append(_section("Earnings & events calendar (v2)", earnings_calendar, ticker))
    parts.append(_section("Peer-relative valuation & strength (v2)", peer_relative, ticker, peers, end))

    out_dir = ROOT / "context"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{ticker}_{trade_date}.md"
    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"\n[saved] {out_path}  ({out_path.stat().st_size:,} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic data harvester for the "Claude-as-engine" workflow.

Calls the SAME data tools the real TradingAgents analysts call (via
``@tool`` wrappers -> ``route_to_vendor`` -> yfinance / FRED / Polymarket),
for one ticker + date, and dumps every raw tool output to a single markdown
file. No LLM is instantiated, so this needs NO paid LLM API key — only the
free data vendors (yfinance/Polymarket are keyless; FRED needs FRED_API_KEY).

The resulting ``context/<TICKER>_<DATE>.md`` is the real, auditable input
that an in-session agent (Claude) then reasons over to produce the 12-stage
report — replacing the metered LLM calls with this session's own cognition.

Usage:
    python scripts/harvest_context.py TICKER [YYYY-MM-DD] [stock|crypto]
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


def _section(title: str, fn, *args, **kwargs) -> str:
    """Run one tool call, capturing output or a readable error per section."""
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

    set_config(DEFAULT_CONFIG)

    end = trade_date
    d = datetime.strptime(trade_date, "%Y-%m-%d")
    price_start = (d - timedelta(days=400)).strftime("%Y-%m-%d")  # >200 trading days for 200 SMA
    news_start = (d - timedelta(days=14)).strftime("%Y-%m-%d")

    print(f"[harvest] {ticker} @ {trade_date} (asset_type={asset_type})", flush=True)

    identity = resolve_instrument_identity(ticker)
    instrument_context = build_instrument_context(ticker, asset_type, identity)

    parts: list[str] = [
        f"# Data context — {ticker} @ {trade_date}\n",
        f"_Harvested {datetime.now().isoformat(timespec='seconds')} via project data tools "
        f"(yfinance / FRED / Polymarket). No LLM used._\n",
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
    parts.append(_section(
        "Global / macro news",
        get_global_news, {"curr_date": end}))
    parts.append(_section(
        "Insider transactions",
        get_insider_transactions, {"ticker": ticker}))

    print("[macro]", flush=True)
    for series in MACRO:
        parts.append(_section(
            f"Macro: {series}",
            get_macro_indicators, {"indicator": series, "curr_date": end}))

    print("[prediction markets]", flush=True)
    for topic in PREDICTION_TOPICS:
        parts.append(_section(
            f"Prediction markets: {topic}",
            get_prediction_markets, {"topic": topic}))

    print("[fundamentals]", flush=True)
    parts.append(_section(
        "Fundamentals overview",
        get_fundamentals, {"ticker": ticker, "curr_date": end}))
    parts.append(_section(
        "Income statement (quarterly)",
        get_income_statement, {"ticker": ticker, "freq": "quarterly", "curr_date": end}))
    parts.append(_section(
        "Balance sheet (quarterly)",
        get_balance_sheet, {"ticker": ticker, "freq": "quarterly", "curr_date": end}))
    parts.append(_section(
        "Cash flow (quarterly)",
        get_cashflow, {"ticker": ticker, "freq": "quarterly", "curr_date": end}))

    out_dir = ROOT / "context"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{ticker}_{trade_date}.md"
    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"\n[saved] {out_path}  ({out_path.stat().st_size:,} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic macro + 中观 harvester for the "Claude-as-engine" workflow.

Top-down sibling of scripts/harvest_context.py. Harvests REGIONAL macro
(US via FRED aliases, China via akshare macro_china_*, Global via FRED
international series by raw ID), the CROSS-ASSET price basket (yfinance), and
A-share 中观 (sector fund-flow, Dragon-Tiger, limit-up sentiment, northbound),
then dumps every raw output to one markdown file. No LLM is instantiated — only
free vendors (yfinance keyless; FRED needs FRED_API_KEY; akshare optional).

Usage:
    python scripts/harvest_macro.py [YYYY-MM-DD]
"""
import os
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env(env_path: Path) -> None:
    """Minimal .env loader (no dependency); never overrides the real environment.
    Verbatim from scripts/harvest_context.py."""
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

from tradingagents.agents.utils.agent_utils import get_macro_indicators  # noqa: E402
from tradingagents.dataflows.config import set_config  # noqa: E402
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402

# US macro — friendly aliases already resolved by fred.py.
US_FRED = [
    "fed_funds_rate", "2y_treasury", "10y_treasury", "yield_curve",
    "cpi", "core_cpi", "core_pce", "inflation_expectations",
    "unemployment", "nonfarm_payrolls", "initial_claims",
    "real_gdp", "industrial_production", "m2",
    "NFCI", "DFII10",   # financial conditions + 10y real yield (raw FRED IDs)
]
# Global outer layer — FRED international series by RAW ID (passthrough). Any ID
# that returns MACRO_DATA_UNAVAILABLE at smoke time is dropped (see Task 2).
INTL_FRED = {
    "China CPI (YoY index, OECD)": "CHNCPIALLMINMEI",
    "Japan CPI (index, OECD)": "JPNCPIALLMINMEI",
    "Euro Area deposit facility rate": "ECBDFR",
}
# Cross-asset price basket (yfinance). Label -> symbol.
CROSS_ASSET = {
    "US Dollar Index": "DX-Y.NYB", "USDCNY": "CNY=X", "USDJPY": "JPY=X",
    "Gold": "GC=F", "WTI Oil": "CL=F", "Copper": "HG=F",
    "UST 10y yield (x10)": "^TNX", "VIX": "^VIX",
    "S&P500 (SPY)": "SPY", "CSI300": "000300.SS", "Hang Seng": "^HSI",
    "Bitcoin": "BTC-USD", "Ether": "ETH-USD",
}


def _pct_change(first: float, last: float) -> str:
    """Signed percent change; 'n/a' when the base is zero (never raises)."""
    try:
        if float(first) == 0:
            return "n/a"
        return f"{(float(last) - float(first)) / float(first) * 100:+.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _ak_call(fn, tries: int = 3, backoff: float = 1.5):
    """Call a flaky akshare endpoint with retries + linear backoff.
    Verbatim from scripts/harvest_context.py."""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(backoff * (i + 1))
    raise last


def _section(title: str, fn, *args, **kwargs) -> str:
    """Run one data call, capturing output or a readable error per section.
    Verbatim from scripts/harvest_context.py."""
    print(f"  - {title} ...", flush=True)
    try:
        out = fn.invoke(*args, **kwargs) if hasattr(fn, "invoke") else fn(*args, **kwargs)
        body = (out or "").strip() or "_(empty)_"
    except Exception as e:  # noqa: BLE001 — one flaky vendor must not kill the harvest
        body = f"_ERROR fetching this section: {e}_\n```\n{traceback.format_exc()}```"
    return f"\n## {title}\n\n{body}\n"


def main() -> int:
    trade_date = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    datetime.strptime(trade_date, "%Y-%m-%d")  # validate / fail loud on bad date
    set_config(DEFAULT_CONFIG)
    end = trade_date

    print(f"[harvest-macro] @ {trade_date}", flush=True)
    parts: list[str] = [
        f"# Macro data context — {trade_date}\n",
        f"_Harvested {datetime.now().isoformat(timespec='seconds')} via project data tools "
        f"+ yfinance + akshare. No LLM used._\n",
    ]

    # Blocks are wired in Tasks 2–4.

    out_dir = ROOT / "context" / "macro" / trade_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "data.md"
    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"\n[saved] {out_path}  ({out_path.stat().st_size:,} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

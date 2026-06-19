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


def _recent_rows(df, n: int = 6):
    """Most-recent n rows from an akshare macro frame, robust to sort order.
    akshare endpoints are inconsistently ordered (CPI ascending, PPI/PMI
    descending) and name their date column differently; trust the frame's own
    monotonic order and pick the recent END, so we never show ancient rows."""
    date_col = next(
        (c for c in ("日期", "月份", "时间", "数据日期", "发布时间", "date") if c in df.columns),
        df.columns[0],
    )
    try:
        s = df[date_col].astype(str)
        ascending = s.iloc[0] <= s.iloc[-1]
        return df.tail(n) if ascending else df.head(n)
    except Exception:  # noqa: BLE001 — never let recency selection crash the block
        return df.tail(n)


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


def us_macro_block(curr_date: str) -> str:
    """US regional macro: every US_FRED series via the project's FRED tool."""
    out = []
    for series in US_FRED:
        try:
            md = get_macro_indicators.invoke({"indicator": series, "curr_date": curr_date})
        except Exception as e:  # noqa: BLE001
            md = f"_({series} unavailable: {e})_"
        out.append(f"### {series}\n\n{md}")
    return "\n\n".join(out)


def global_macro_block(curr_date: str) -> str:
    """Global outer layer: FRED international series by raw ID (passthrough).
    Series that FRED does not carry return MACRO_DATA_UNAVAILABLE — kept inline so
    the build-time smoke run can spot and drop them."""
    out = []
    for label, series_id in INTL_FRED.items():
        try:
            md = get_macro_indicators.invoke({"indicator": series_id, "curr_date": curr_date})
        except Exception as e:  # noqa: BLE001
            md = f"_({series_id} unavailable: {e})_"
        out.append(f"### {label} ({series_id})\n\n{md}")
    out.append(
        "\n_BOJ/ECB forward guidance, EM policy rates, and any series returning "
        "MACRO_DATA_UNAVAILABLE above → fetch via WebSearch at reasoning time, tag '实时网查'._"
    )
    return "\n\n".join(out)


def china_macro_block(curr_date: str) -> str:
    """China regional macro via akshare macro_china_* (OPTIONAL dep). Defensive:
    endpoint/column drift across akshare versions → degrade + WebSearch directive,
    never silently collapse."""
    try:
        import akshare as ak
    except ImportError:
        return ("_akshare 未安装(`uv add akshare`)→ 中国宏观走 WebSearch:CPI/PPI/PMI(官+财新)/"
                "社融·M2/LPR/外储/进出口/GDP/工增/社零/地产投资,标『实时网查』。_")
    # (label, callable) — each guarded independently so one bad endpoint can't kill the block.
    specs = [
        ("CPI 当月同比", lambda: ak.macro_china_cpi_monthly()),
        ("PPI 当月同比", lambda: ak.macro_china_ppi()),
        ("制造业 PMI", lambda: ak.macro_china_pmi()),
        ("社融规模存量", lambda: ak.macro_china_shrzgm()),
        ("货币供应 M2", lambda: ak.macro_china_money_supply()),
        ("LPR 利率", lambda: ak.macro_china_lpr()),
    ]
    out = []
    for label, fn in specs:
        try:
            df = _ak_call(fn)
            out.append(f"### {label}\n\n```\n{_recent_rows(df).to_string(index=False)}\n```")
        except Exception as e:  # noqa: BLE001
            out.append(f"### {label}\n\n_取数失败({e})→ WebSearch『{label} 最新』,标『实时网查』。_")
    return "\n\n".join(out)


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

    print("[regional macro]", flush=True)
    parts.append(_section("US macro (FRED)", us_macro_block, end))
    parts.append(_section("China macro (akshare macro_china)", china_macro_block, end))
    parts.append(_section("Global outer layer (FRED international + WebSearch)", global_macro_block, end))

    out_dir = ROOT / "context" / "macro" / trade_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "data.md"
    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"\n[saved] {out_path}  ({out_path.stat().st_size:,} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

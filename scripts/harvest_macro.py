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


def _basket_table(rows: list[dict]) -> str:
    """Render the cross-asset basket as a markdown table (pure; None -> 'n/a')."""
    head = "| Asset | Symbol | Last | Δ1m | ΔYTD |\n|---|---|---:|---:|---:|"
    body = [
        f"| {r['label']} | {r['symbol']} | {'n/a' if r['last'] is None else r['last']} "
        f"| {r['chg_1m']} | {r['chg_ytd']} |"
        for r in rows
    ]
    return head + "\n" + "\n".join(body)


def cross_asset_block(curr_date: str) -> str:
    """Cross-asset price basket via yfinance: last + 1-month + YTD change.
    Windows end at curr_date (lookahead-safe). yfinance returns a tz-aware index;
    we normalize to naive so date-string masks don't raise tz-compare errors."""
    end = datetime.strptime(curr_date, "%Y-%m-%d")
    start = (end - timedelta(days=400)).strftime("%Y-%m-%d")
    m_cut = (end - timedelta(days=30)).strftime("%Y-%m-%d")
    ytd_anchor = f"{end.year}-01-01"
    rows = []
    for label, symbol in CROSS_ASSET.items():
        last = chg_1m = chg_ytd = None
        try:
            hist = yf.Ticker(symbol).history(start=start, end=curr_date)["Close"].dropna()
            if getattr(hist.index, "tz", None) is not None:
                hist.index = hist.index.tz_localize(None)
            if len(hist):
                last = round(float(hist.iloc[-1]), 4)
                m_ago = hist[hist.index <= m_cut]
                ytd = hist[hist.index >= ytd_anchor]
                chg_1m = _pct_change(float(m_ago.iloc[-1]), last) if len(m_ago) else "n/a"
                chg_ytd = _pct_change(float(ytd.iloc[0]), last) if len(ytd) else "n/a"
        except Exception:  # noqa: BLE001 — degrade per-symbol
            pass
        rows.append({"label": label, "symbol": symbol,
                     "last": last, "chg_1m": chg_1m or "n/a", "chg_ytd": chg_ytd or "n/a"})
    return _basket_table(rows)


def meso_ashare_block(curr_date: str) -> str:
    """A-share 中观骨架 via akshare (OPTIONAL dep): sector fund-flow, Dragon-Tiger
    (游资), limit-up sentiment, northbound summary. Each guarded independently;
    failures degrade to an explicit WebSearch directive, never silent collapse."""
    try:
        import akshare as ak
    except ImportError:
        return ("_akshare 未安装 → A股中观走 WebSearch:行业资金流入流出排名 / 龙虎榜游资 / "
                "涨停家数·连板 / 北向资金,标『实时网查』。_")
    out = []
    # 1) 行业资金流排名(主力净流入)— try Eastmoney, fall back to THS, then WebSearch.
    #    Both sources sort by net flow, so head=净流入领先、tail=净流出领先.
    ff_md = None
    for src, call in (
        ("Eastmoney", lambda: ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")),
        ("THS", lambda: ak.stock_fund_flow_industry(symbol="即时")),
    ):
        try:
            ff = _ak_call(call)
            ff_md = (f"**行业主力资金流(今日, {src};头部=净流入,尾部=净流出)**\n\n```\n"
                     + ff.head(10).to_string(index=False)
                     + "\n...\n" + ff.tail(5).to_string(index=False) + "\n```")
            break
        except Exception:  # noqa: BLE001 — try next source
            continue
    out.append(ff_md or "_行业资金流取数失败(Eastmoney+THS 均失败)→ "
               "WebSearch『今日行业主力资金净流入排名 净流出』,标『实时网查』。_")
    # 2) 龙虎榜 / 游资(近三月统计)
    try:
        lhb = _ak_call(lambda: ak.stock_lhb_stock_statistic_em(symbol="近三月"))
        out.append("**龙虎榜活跃个股(近三月,游资/机构席位线索)**\n\n```\n"
                   + lhb.head(12).to_string(index=False) + "\n```")
    except Exception as e:  # noqa: BLE001
        out.append(f"_龙虎榜取数失败({e})→ WebSearch『近期龙虎榜 游资 营业部』,标『实时网查』。_")
    # 3) 涨停情绪(回看最近有数据的交易日)
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
            hot = "、".join(f"{k}({v})" for k, v in zt["所属行业"].value_counts().head(5).items())
            out.append(f"**涨停情绪({used})**:涨停 **{len(zt)}** 家、最高 **{maxlb} 连板**;"
                       f"涨停最集中行业:{hot}。(涨停多+连板高=情绪亢奋;少=退潮)")
    except Exception as e:  # noqa: BLE001
        out.append(f"_涨停池取数失败({e})→ WebSearch『今日涨停家数 最高连板 涨停行业』,标『实时网查』。_")
    # 4) 北向资金(汇总;个股实时披露 2024-08 已停)
    try:
        nb = _ak_call(lambda: ak.stock_hsgt_fund_flow_summary_em())
        out.append("**北向资金(汇总;注:个股实时披露 2024-08 已停,仅汇总/板块/季度口径)**\n\n```\n"
                   + nb.tail(8).to_string(index=False) + "\n```")
    except Exception as e:  # noqa: BLE001
        out.append(f"_北向资金取数失败({e})→ WebSearch『北向资金 今日净流入 行业』,标『实时网查』。_")
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

    print("[cross-asset]", flush=True)
    parts.append(_section("Cross-asset price basket (yfinance)", cross_asset_block, end))

    print("[A股中观]", flush=True)
    parts.append(_section("A股中观骨架 (行业资金/游资/涨停情绪/北向)", meso_ashare_block, end))

    out_dir = ROOT / "context" / "macro" / trade_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "data.md"
    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"\n[saved] {out_path}  ({out_path.stat().st_size:,} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    python scripts/harvest_context.py TICKER [YYYY-MM-DD] [stock|crypto] [PEER1,PEER2,...]
"""

import os
import sys
import time
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
from autoresearch.dataflows.config import set_config  # noqa: E402
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
    return "\n".join(out) + note


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
    return "\n".join(out)


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


def earnings_quality_metrics(symbol: str) -> str:
    """Accruals / cash-conversion / SBC dilution derived from quarterly statements."""
    t = yf.Ticker(normalize_symbol(symbol))

    def _stmt(attr):
        try:
            return getattr(t, attr, None)
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


def ashare_news_akshare(sym: str, limit: int = 12) -> str | None:
    """East-money individual-stock news via akshare (OPTIONAL dependency).
    Returns markdown bullets, or None if akshare is absent / returns nothing."""
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
    rows = []
    for _, r in df.head(limit).iterrows():
        title = str(r.get("新闻标题", "") or "").strip()
        when = str(r.get("发布时间", "") or "").strip()
        src = str(r.get("文章来源", "") or "").strip()
        if title:
            rows.append(f"- [{when}] **{title}**" + (f" ({src})" if src else ""))
    return "\n".join(rows) if rows else None


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
        ak_news = ashare_news_akshare(normalize_symbol(ticker))
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
        vix = yf.Ticker("^VIX").history(period="5d")["Close"].dropna()
        if len(vix):
            v = float(vix.iloc[-1])
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


def solvency_block(symbol: str) -> str:
    """Balance-sheet solvency / refinancing lens — leverage, liquidity runway,
    interest coverage, goodwill: the mechanism behind most blow-ups. A-share
    share-pledge (股权质押) is left to WebSearch (per-stock akshare is unreliable)."""
    t = yf.Ticker(normalize_symbol(symbol))

    def _stmt(attr):
        try:
            return getattr(t, attr, None)
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

def _section(title: str, fn, *args, **kwargs) -> str:
    """Run one data call, capturing output or a readable error per section."""
    print(f"  - {title} ...", flush=True)
    try:
        out = fn.invoke(*args, **kwargs) if hasattr(fn, "invoke") else fn(*args, **kwargs)
        body = (out or "").strip() or "_(empty)_"
    except Exception as e:  # noqa: BLE001 — one flaky vendor must not kill the harvest
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
    base = (root or (ROOT / "context" / "scan")) / trade_date
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
    """
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
        from tushare_enrich import ashare_market_context_ts
        b = ashare_market_context_ts(normalize_symbol(ticker), curr_date)
        if b:
            return b + "\n\n_(tushare 源;akshare 东财 push2 在本机被网络封锁时自动走此路。)_"
    except Exception:  # noqa: BLE001 — tushare 不可用 → 回退
        pass
    return ashare_market_context_or_note(ticker, curr_date)


def ashare_shareholder_best(ticker: str) -> str:
    """股东户数:tushare(含质押爆雷红旗)优先,失败回退 akshare。"""
    try:
        from tushare_enrich import ashare_shareholder_ts
        b = ashare_shareholder_ts(normalize_symbol(ticker))
        if b:
            return b
    except Exception:  # noqa: BLE001
        pass
    return ashare_shareholder_count(normalize_symbol(ticker))


def ashare_calendar_best(ticker: str, curr_date: str) -> str:
    """A股日历:tushare(业绩预告/快报=前瞻成长)+ akshare(解禁),各自降级。"""
    parts: list[str] = []
    try:
        from tushare_enrich import ashare_calendar_ts
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

def _uzi_fundamentals(ticker: str) -> str:
    from autoresearch.common.uzi_lenses import ashare_fundamentals_ts
    return ashare_fundamentals_ts(ticker) or "_UZI A股原生财报暂不可用(非A股/取数失败)。_"


def _uzi_margin(ticker: str) -> str:
    from autoresearch.common.uzi_lenses import margin_trend_ts
    return margin_trend_ts(ticker) or "_非两融标的或融资数据暂无。_"


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


def main() -> int:
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not pos:
        print(__doc__)
        return 1
    # --slim:轻量模式,只 harvest 决策驱动块(scan-market L3b / analyze-ticker-lite 用),体积/token 大幅下降
    slim = "--slim" in flags
    ticker = pos[0]
    trade_date = pos[1] if len(pos) > 1 else date.today().isoformat()
    asset_type = pos[2] if len(pos) > 2 else "stock"
    peers_arg = pos[3] if len(pos) > 3 else ""
    peers = [p.strip() for p in peers_arg.split(",") if p.strip()] or PEER_MAP.get(ticker.upper(), [])

    set_config(DEFAULT_CONFIG)

    end = trade_date
    d = datetime.strptime(trade_date, "%Y-%m-%d")
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
            get_stock_data, {"symbol": ticker, "start_date": price_start, "end_date": end}))
        parts.append(_section(
            "Technical indicators (full menu)",
            get_indicators, {"symbol": ticker, "indicator": ",".join(INDICATORS),
                             "curr_date": end, "look_back_days": 30}))
    parts.append(_section(
        "Verified market snapshot (source of truth)",
        get_verified_market_snapshot, {"symbol": ticker, "curr_date": end, "look_back_days": 30}))
    if _is_ashare(ticker):
        # scan-market L4:有 L1 召回行 → 复用(零富因子重复取数,与召回同源);
        # 全量 analyze-ticker / 无 scan → live tushare(10日资金序列+MACD 更全)。
        l1_row = _load_l1_row(ticker, trade_date) if slim else None
        if l1_row is not None:
            parts.append(_section("Market context — A股 (主力/技术/筹码/北向 · 复用L1召回)",
                                  ashare_market_context_from_l1, l1_row))
        else:
            parts.append(_section("Market context — A股 (主力/技术/筹码/北向)",
                                  ashare_market_context_best, ticker, end))
    else:
        parts.append(_section("Market context — US (regime/breadth/sector/VIX)",
                              us_market_context, ticker, end))
    parts.append(_section("Tradeability & price-limit reality (v4)",
                          tradeability_block, ticker, end))

    print("[news / social]", flush=True)
    parts.append(_section(
        f"Ticker news {news_start} → {end}",
        ticker_news_block, ticker, news_start, end))
    if not slim:  # 全球宏观新闻 / 内部交易 / 持仓做空:决策卡用不上
        parts.append(_section("Global / macro news", get_global_news, {"curr_date": end}))
        parts.append(_section("Insider transactions", get_insider_transactions, {"ticker": ticker}))
        parts.append(_section("Ownership & short interest (v3)", ownership_short, ticker))
    if _is_ashare(ticker):
        parts.append(_section("股东户数 / 质押 (A股, v4)",
                              ashare_shareholder_best, ticker))
        # UZI 增量透镜:便宜的(财报1调/融资1调/trap零调)slim 也取;席位识别(多日 top_inst)给全量
        parts.append(_section("A股原生财报 (UZI·tushare)", _uzi_fundamentals, ticker))
        parts.append(_section("融资余额趋势 (UZI·tushare)", _uzi_margin, ticker))
        # 量价机械底(**仅 scan L4 的 slim 路径**复用 L1 因子行,零取数):trap=派发空半 + volprice=吸筹多半 + 多日 CMF/OBV。
        # 全量 analyze-ticker 与 scan **完全解耦——不取 L1**,改由分析师对上方 live 市场上下文(主力/技术/筹码)自行套用 trap/volume_price 判读。
        if l1_row is not None:
            parts.append(_section("杀猪盘/派发风险 (UZI·复用L1)", _uzi_trap, l1_row))
            parts.append(_section("量价形态/吸筹·多日资金流 (UZI·复用L1)", _uzi_volprice, l1_row))
        if not slim:
            parts.append(_section("龙虎榜席位识别 (UZI·tushare)", _uzi_seats, ticker, end))

    if not slim:  # 8 个 FRED 宏观 + 中国背景 + 预测市场:对单只决策卡是背景噪音
        print("[macro]", flush=True)
        for series in MACRO:
            parts.append(_section(f"Macro: {series}", get_macro_indicators,
                                  {"indicator": series, "curr_date": end}))
        if _is_ashare(ticker):
            parts.append(_section("China market backdrop (A-share)", china_backdrop, end))

        print("[prediction markets]", flush=True)
        parts.append(_section("Prediction markets (Polymarket; WebSearch fallback if blocked)",
                              prediction_markets_or_websearch_note, PREDICTION_TOPICS))

    print("[fundamentals]", flush=True)
    parts.append(_section("Fundamentals overview", get_fundamentals, {"ticker": ticker, "curr_date": end}))
    parts.append(_section("Income statement (quarterly)", get_income_statement,
                          {"ticker": ticker, "freq": "quarterly", "curr_date": end}))
    if not slim:  # 资产负债表/现金流量表全表:slim 用 solvency + earnings-quality 的摘要替代
        parts.append(_section("Balance sheet (quarterly)", get_balance_sheet,
                              {"ticker": ticker, "freq": "quarterly", "curr_date": end}))
        parts.append(_section("Cash flow (quarterly)", get_cashflow,
                              {"ticker": ticker, "freq": "quarterly", "curr_date": end}))
    parts.append(_section("Earnings quality / forensics (v3)", earnings_quality_metrics, ticker))
    parts.append(_section("Solvency & refinancing (v4)", solvency_block, ticker))

    # --- v2 enrichments (yfinance direct; US-centric, degrade gracefully) ---
    print("[v2: analyst / earnings / calendar]", flush=True)
    if not slim:  # 期权链(A股空)+ 同业全表(取数慢):决策卡靠自身估值 + 卖方目标即可
        parts.append(_section("Options & implied volatility (v2)", options_iv_summary, ticker, end))
    parts.append(_section("Analyst consensus & price targets (v2)", analyst_consensus, ticker))
    parts.append(_section("Earnings & events calendar (v2)", earnings_calendar, ticker))
    if _is_ashare(ticker):
        parts.append(_section("Corporate calendar — A股 业绩预告·快报/解禁 (v4)",
                              ashare_calendar_best, ticker, end))
    if not slim:
        parts.append(_section("Peer-relative valuation & strength (v2)", peer_relative, ticker, peers, end))

    out_dir = ROOT / "context"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{ticker}_{trade_date}{'_slim' if slim else ''}.md"
    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"\n[saved] {out_path}  ({out_path.stat().st_size:,} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

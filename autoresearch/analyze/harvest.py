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

task-10(D1.1)拆分:块函数本体搬进 `blocks_external.py`(D-3 外源块)/`blocks_ashare.py`
(akshare 老路+tushare 包装+UZI+L1 复用渲染+个股新闻分发+中国底色)/`blocks_us.py`
(分析师/日历/同业/做空/US regime+预测市场)/`blocks_statements.py`(可交易性/盈利质量/
偿付)/`slim_io.py`(L1 行读取+二段式落盘)。本文件只留 env/常量/`_section`/`_slug`/
(market,tier)派发表/`main`——本文件是这张模块图的**顶端**,只单向消费五个 block
文件,不被任何一个回引(`_is_ashare`/`_REALTIME_DISCLAIMER`/`_l1_float`/`_l1_flag`
等跨文件共享的原语因此下沉到叶子 block 文件自己定义,而不是留在本文件被回引——
`python -m` 直跑时任何回引都会把本文件的代码执行两遍并撞 ImportError,细节见
`blocks_statements.py` 模块 docstring)。
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
    get_verified_market_snapshot,
    resolve_instrument_identity,
)
from autoresearch.data.keyless import consensus_eps_block  # noqa: E402
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


# ── 拆分后的块模块(cross-import)──
#
# harvest.py 严格是这张有向无环图的**顶端**:只被 `python -m autoresearch.analyze.harvest`
# 当 `__main__` 跑,从不被其它模块 `import`。任何 block 文件若在**模块顶层**反过来
# `from autoresearch.analyze.harvest import X`,`python -m` 直跑时会把 harvest.py 的
# 代码执行两遍(一次注册成 `__main__`,一次因被下游 import 触发、注册成真实模块名
# `autoresearch.analyze.harvest`),第二次执行会在这批 `from .blocks_* import` 语句上
# 撞见"部分初始化模块缺属性" ImportError——`import autoresearch.analyze.harvest` 方式
# 的冒烟测不出这条,只有真用 `-m` 跑才会踩(2026-08-31 §LIVE parity 实测逮到)。故
# `_is_ashare`/`_REALTIME_DISCLAIMER`/`_l1_float`/`_l1_flag` 这几个跨文件共享的原语都
# 下沉到叶子模块(`blocks_statements.py`/`blocks_us.py`/`slim_io.py`)自己定义,
# harvest.py 只单向消费,不被回引。
from autoresearch.analyze.blocks_ashare import (  # noqa: E402
    ashare_calendar_best,
    ashare_market_context_best,
    ashare_market_context_from_l1,
    ashare_news_akshare,
    ashare_shareholder_best,
    china_backdrop,
    ticker_news_block,
    _uzi_fundamentals,
    _uzi_margin,
    _uzi_seats,
    _uzi_trap,
    _uzi_volprice,
)
from autoresearch.analyze.blocks_external import (  # noqa: E402
    NO_OPTIONS_DEGRADE,
    _RT_MAX,
    _TITLE_ANALYST_ACTIONS,
    _TITLE_EDGAR,
    _TITLE_GNEWS_EN,
    _TITLE_GNEWS_ZH,
    _TITLE_OPTIONS,
    _TITLE_READTHROUGH,
    _company_query_name,
    _et_session,
    _iv_percentile_row,
    _opt_section,
    _readthrough_items,
    analyst_actions_block,
    earnings_realized_moves,
    edgar_block,
    external_sections,
    gnews_block,
    options_block,
    readthrough_block,
)
from autoresearch.analyze.blocks_statements import (  # noqa: E402
    _board_limit,
    _is_ashare,
    earnings_quality_metrics,
    solvency_block,
    tradeability_block,
)
from autoresearch.analyze.blocks_us import (  # noqa: E402
    _benchmarks,
    _hist_returns,
    _spot,
    _vix_latest,
    analyst_consensus,
    earnings_calendar,
    indicator_summary_table,
    ownership_short,
    peer_relative,
    prediction_markets_or_websearch_note,
    price_history_compact_block,
    render_indicator_summary_table,
    us_market_context,
)
from autoresearch.analyze.slim_io import (  # noqa: E402
    _l1_flag,
    _l1_float,
    _load_l1_row,
    _split_slim_for_progressive,
    _write_slim_files,
)

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
    """独立 slim 不入 run 现场(D8.5):没有 `--out-dir` 时**一律**落 `ws.context_root()`,

    不看 `slim` 是不是 True、也不问 `AUTORESEARCH_RUN_ID` 是不是恰好指向一趟活跃的
    scan run。此前 slim 分支缺省调 `ws.scan_input_dir(trade_date)`——那个函数按
    `active_run_kind()` 判断,只要环境里的 `AUTORESEARCH_RUN_ID` 恰好命中一趟真实
    scan run(哪怕这次 harvest 调用跟那趟 run 毫无关系),就会把产物写进
    `<run>/_external_inputs/`,污染别人的法证现场。scan 自己的调用点
    (`scan/l4/producers._default_harvest_slim`)一律显式传 `--out-dir`——那是唯一
    该把 slim 写进某个 run 目录的路径,不能靠缺省猜。
    """
    if explicit is not None and not slim:
        raise ValueError("--out-dir 仅支持 --slim，不得迁移 full 报告")
    session_dir = None
    if explicit is None and os.environ.get("AUTORESEARCH_TASK_ID") == "stock.harvest":
        try:
            from autoresearch.trace.capsule import require_active_run

            handle = require_active_run(str(os.environ.get("AUTORESEARCH_RUN_ID") or ""))
            if handle.contract.run_kind == "stock-research":
                session_dir = Path(handle.staging)
        except Exception:  # noqa: BLE001 - a stale ambient id must keep standalone routing
            session_dir = None
    relative = explicit if explicit is not None else session_dir or ws.context_root()
    out_dir = Path(relative) if Path(relative).is_absolute() else ROOT / relative
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


# ═══════════════════════ (market,tier) 派发表(task-10 D1.1) ═══════════════════════
#
# 每个块就是 `main()` 原先内联的一段 `_section(...)`/`_opt_section(...)` 调用,改接受
# 共享 `ctx` dict;返回 `None` = 整节省略(不 append),其余返回值直接进 `parts`。`ctx`
# 由 `main()` 在派发循环之前一次性建好(不可变的取数参数 + 两个贯穿多块的可变状态:
# `l1_row`——是否复用 scan L1 召回行,`snapshot_section`——verified_snapshot 块渲染出
# 的正文,供 fwd_pe 块复用同一次取数)。带分支/循环/副作用的几个留作具名函数;纯
# "填参数调 `_section`"的直接量作 `BLOCKS` 里的 lambda 值,不占单独的 `def`。


def _blk_verified_snapshot(ctx: dict) -> str:
    # 变量留手:同一节的文本供本函数末尾 fwd-PE 补价复用(D1.6 #2,零二次网络往返)。
    section = _section(
        "Verified market snapshot (source of truth)",
        get_verified_market_snapshot, {"symbol": ctx["ticker"], "curr_date": ctx["end"], "look_back_days": 30},
        endpoint="analyze:verified-snapshot")
    ctx["snapshot_section"] = section
    return section


def _blk_ashare_market_context(ctx: dict) -> str:
    # scan-market L4:有 L1 召回行 → 复用(零富因子重复取数,与召回同源);
    # 全量 analyze-ticker / 无 scan → live tushare(10日资金序列+MACD 更全)。
    if ctx["l1_row"] is not None:
        return _section("Market context — A股 (主力/技术/筹码/北向 · 复用L1召回)",
                        ashare_market_context_from_l1, ctx["l1_row"],
                        endpoint="analyze:l1-market-context")
    return _section("Market context — A股 (主力/技术/筹码/北向)",
                    ashare_market_context_best, ctx["ticker"], ctx["end"],
                    endpoint="analyze:ashare-market-context")


def _blk_uzi_trap(ctx: dict) -> str | None:
    # 量价机械底(**仅 scan L4 的 slim 路径**复用 L1 因子行,零取数);
    # 全量 analyze-ticker 与 scan 完全解耦——不取 L1,l1_row 恒 None → 整节省略。
    if ctx["l1_row"] is None:
        return None
    return _section("杀猪盘/派发风险 (UZI·复用L1)", _uzi_trap, ctx["l1_row"],
                    endpoint="analyze:uzi-trap")


def _blk_uzi_volprice(ctx: dict) -> str | None:
    if ctx["l1_row"] is None:
        return None
    return _section("量价形态/吸筹·多日资金流 (UZI·复用L1)", _uzi_volprice, ctx["l1_row"],
                    endpoint="analyze:uzi-volprice")


def _blk_macro_series(ctx: dict) -> str:
    return "".join(
        _section(f"Macro: {series}", get_macro_indicators,
                {"indicator": series, "curr_date": ctx["end"]}, endpoint="fred")
        for series in MACRO)


def _blk_external_evidence(ctx: dict) -> str:
    # 派发清单的单一事实源 = `external_sections`(slim 那边它自己也返回空,两道门)。
    out: list[str] = []
    for _title, _fn, _args, _kw in external_sections(
            ctx["ticker"], ctx["end"], slim=ctx["slim"],
            company_name=_company_query_name(ctx["explicit_name"], ctx["identity"])):
        out.append(_opt_section(_title, _fn, *_args, **_kw))
    return "".join(out)


def _blk_fwd_pe(ctx: dict) -> str:
    # A股卖方一致预期 EPS → 真 fwd-PE(补 yfinance 对 A 股 forwardPE 的缺口;同花顺 keyless)。
    # D1.6 #2:price= 优先 L1 复用行 close,否则退到同进程已取的 snapshot close。
    eps_px = _consensus_eps_price(ctx["l1_row"], ctx["snapshot_section"])
    return _section("A股卖方一致预期 EPS / fwd-PE (同花顺·keyless)",
                    consensus_eps_block, ctx["ticker"], eps_px,
                    endpoint="analyze:keyless-consensus-eps")


def _blk_technical_indicators_compact(ctx: dict) -> str:
    """D1.5(Q7)瘦身:12 个 `## <ind> values` 30 天序列块 → 摘要表(末值/5日前值/方向)
    + deep 附件指针。取数**不变**(同一次 `get_indicators.invoke(...)`,与旧块同源
    同数字);变的只是主文件里怎么摆——全序列原样写进 `<TICKER>_<date>_indicators.md`
    (已在 `contracts.artifacts.ARTIFACTS` 登记 `analyze_indicators`),主文件只留汇总表
    + 指向 deep 文件的指针注释。摘要/落盘失败(B 级)不阻断 harvest,退回内联全文本。
    """
    raw_section = _section(
        "Technical indicators (full menu)",
        get_indicators, {"symbol": ctx["ticker"], "indicator": ",".join(INDICATORS),
                         "curr_date": ctx["end"], "look_back_days": 30},
        endpoint="analyze:yf-technical-indicators")
    try:
        deep_name = f"{ctx['ticker']}_{ctx['trade_date']}_indicators.md"
        ctx["side_artifacts"][deep_name] = (
            f"# 技术指标 30 天全序列(deep 附件,按需读)— {ctx['ticker']} @ {ctx['trade_date']}\n"
            + raw_section
        )
        rows = indicator_summary_table(raw_section, INDICATORS)
        pointer = f"\n<!-- 指标全 30 天序列已拆到同目录 `{deep_name}`,按需 Read -->\n"
        return (f"\n## Technical indicators (summary; full series → {deep_name})\n\n"
                + render_indicator_summary_table(rows) + pointer)
    except Exception as e:  # noqa: BLE001 — 摘要/落盘失败也不许阻断 harvest(B 级)
        from autoresearch.data.contracts import record_degradation
        record_degradation("analyze:indicators-summary", f"{type(e).__name__}: {e}")
        return f"\n## Technical indicators (summary)\n\n_ERROR building summary: {e}_\n" + raw_section


#: 名 → callable 注册表(task-10 interfaces)。
BLOCKS: dict[str, "callable"] = {
    "price_history_compact": lambda ctx: _section(
        "Price history (OHLCV, compact: 60d daily + 52w weekly)",
        price_history_compact_block, ctx["ticker"], ctx["end"], endpoint="yfinance"),
    "technical_indicators_compact": _blk_technical_indicators_compact,
    "verified_snapshot": _blk_verified_snapshot,
    "ashare_market_context": _blk_ashare_market_context,
    "us_market_context": lambda ctx: _section(
        "Market context — US (regime/breadth/sector/VIX)", us_market_context, ctx["ticker"], ctx["end"],
        endpoint="analyze:yf-us-market-context"),
    "tradeability": lambda ctx: _section(
        "Tradeability & price-limit reality (v4)", tradeability_block, ctx["ticker"], ctx["end"],
        endpoint="analyze:tradeability"),
    "ticker_news": lambda ctx: _section(
        f"Ticker news {ctx['news_start']} → {ctx['end']}",
        ticker_news_block, ctx["ticker"], ctx["news_start"], ctx["end"], endpoint="analyze:ticker-news"),
    "global_macro_news": lambda ctx: _section(
        "Global / macro news", get_global_news, {"curr_date": ctx["end"]}),
    "insider_transactions": lambda ctx: _section(
        "Insider transactions", get_insider_transactions, {"ticker": ctx["ticker"]},
        endpoint="analyze:yf-insider-transactions"),
    "ownership_short": lambda ctx: _section(
        "Ownership & short interest (v3)", ownership_short, ctx["ticker"],
        endpoint="analyze:yf-ownership-short"),
    "ashare_shareholder": lambda ctx: _section(
        "股东户数 / 质押 (A股, v4)", ashare_shareholder_best, ctx["ticker"], ctx["end"],
        endpoint="analyze:ashare-shareholder"),
    "uzi_fundamentals": lambda ctx: _section(
        "A股原生财报 (UZI·tushare)", _uzi_fundamentals, ctx["ticker"], ctx["end"],
        endpoint="analyze:uzi-fundamentals"),
    "uzi_margin": lambda ctx: _section(
        "融资余额趋势 (UZI·tushare)", _uzi_margin, ctx["ticker"], ctx["end"], endpoint="analyze:uzi-margin"),
    "uzi_trap": _blk_uzi_trap,
    "uzi_volprice": _blk_uzi_volprice,
    "uzi_seats": lambda ctx: _section(
        "龙虎榜席位识别 (UZI·tushare)", _uzi_seats, ctx["ticker"], ctx["end"], endpoint="analyze:uzi-seats"),
    "macro_series": _blk_macro_series,
    "china_backdrop": lambda ctx: _section(
        "China market backdrop (A-share)", china_backdrop, ctx["end"], endpoint="analyze:yf-china-backdrop"),
    "prediction_markets": lambda ctx: _section(
        "Prediction markets (Polymarket; WebSearch fallback if blocked)",
        prediction_markets_or_websearch_note, PREDICTION_TOPICS, endpoint="analyze:polymarket"),
    "fundamentals_overview": lambda ctx: _section(
        "Fundamentals overview", get_fundamentals, {"ticker": ctx["ticker"], "curr_date": ctx["end"]},
        endpoint="analyze:yf-fundamentals"),
    "income_statement": lambda ctx: _section(
        "Income statement (quarterly)", get_income_statement,
        {"ticker": ctx["ticker"], "freq": "quarterly", "curr_date": ctx["end"]},
        endpoint="analyze:yf-income-statement"),
    "balance_sheet": lambda ctx: _section(
        "Balance sheet (quarterly)", get_balance_sheet,
        {"ticker": ctx["ticker"], "freq": "quarterly", "curr_date": ctx["end"]},
        endpoint="analyze:yf-balance-sheet"),
    "cash_flow": lambda ctx: _section(
        "Cash flow (quarterly)", get_cashflow,
        {"ticker": ctx["ticker"], "freq": "quarterly", "curr_date": ctx["end"]},
        endpoint="analyze:yf-cashflow"),
    "earnings_quality": lambda ctx: _section(
        "Earnings quality / forensics (v3)", earnings_quality_metrics, ctx["ticker"], ctx["end"],
        endpoint="analyze:yf-earnings-quality"),
    "solvency": lambda ctx: _section(
        "Solvency & refinancing (v4)", solvency_block, ctx["ticker"], ctx["end"], endpoint="analyze:yf-solvency"),
    "external_evidence": _blk_external_evidence,
    "analyst_consensus": lambda ctx: _section(
        "Analyst consensus & price targets (v2)", analyst_consensus, ctx["ticker"],
        endpoint="analyze:yf-analyst-consensus"),
    "earnings_calendar": lambda ctx: _section(
        "Earnings & events calendar (v2)", earnings_calendar, ctx["ticker"],
        endpoint="analyze:yf-earnings-calendar"),
    "ashare_calendar": lambda ctx: _section(
        "Corporate calendar — A股 业绩预告·快报/解禁 (v4)", ashare_calendar_best, ctx["ticker"], ctx["end"],
        endpoint="analyze:ashare-calendar"),
    "fwd_pe": _blk_fwd_pe,
    "peer_relative": lambda ctx: _section(
        "Peer-relative valuation & strength (v2)", peer_relative, ctx["ticker"], ctx["peers"], ctx["end"],
        endpoint="analyze:yf-peer-relative"),
}

#: 有序块名清单——逐字对应 `main()` 拆分前的内联调用顺序(§LIVE parity 已核对逐字节相同)。
_ASHARE_FULL: tuple[str, ...] = (
    "price_history_compact", "technical_indicators_compact", "verified_snapshot", "ashare_market_context",
    "tradeability", "ticker_news", "global_macro_news", "insider_transactions", "ownership_short",
    "ashare_shareholder", "uzi_fundamentals", "uzi_margin", "uzi_seats",
    "macro_series", "china_backdrop", "prediction_markets",
    "fundamentals_overview", "income_statement", "balance_sheet", "cash_flow",
    "earnings_quality", "solvency", "external_evidence",
    "analyst_consensus", "earnings_calendar", "ashare_calendar", "fwd_pe", "peer_relative",
)
#: slim 不拉 400 天 OHLCV / 指标序列(既有语义)——`uzi_trap`/`uzi_volprice` 留在表里,
#: 但只在 `ctx["l1_row"]` 命中时才真正产出(见 `_blk_uzi_trap`/`_blk_uzi_volprice`)。
_ASHARE_SLIM: tuple[str, ...] = (
    "verified_snapshot", "ashare_market_context", "tradeability", "ticker_news",
    "ashare_shareholder", "uzi_fundamentals", "uzi_margin", "uzi_trap", "uzi_volprice",
    "fundamentals_overview", "income_statement", "earnings_quality", "solvency",
    "analyst_consensus", "earnings_calendar", "ashare_calendar", "fwd_pe",
)
_US_FULL: tuple[str, ...] = (
    "price_history_compact", "technical_indicators_compact", "verified_snapshot", "us_market_context",
    "tradeability", "ticker_news", "global_macro_news", "insider_transactions", "ownership_short",
    "macro_series", "prediction_markets",
    "fundamentals_overview", "income_statement", "balance_sheet", "cash_flow",
    "earnings_quality", "solvency", "external_evidence",
    "analyst_consensus", "earnings_calendar", "peer_relative",
)
_US_SLIM: tuple[str, ...] = (
    "verified_snapshot", "us_market_context", "tradeability", "ticker_news",
    "fundamentals_overview", "income_statement", "earnings_quality", "solvency",
    "analyst_consensus", "earnings_calendar",
)

#: `(market, tier)` → 有序块名 tuple。这张表就是 SKILL.md 手抄清单的机器真身。
#: `market ∈ {"ashare","us","crypto","other"}`——今天 `_is_ashare()` 只做二分类判定
#: (main() 用它选 "ashare"/"us"),`crypto`/`other` 与 `us` 内容相同(现状即如此:main()
#: 从不区分三者,`asset_type` 只喂 `build_instrument_context` 的措辞,不影响块选择)——
#: 声明齐全四个市场键是为了不让表的 key domain 撒谎,不是新增行为。
HARVEST_PLAN: dict[tuple[str, str], tuple[str, ...]] = {
    ("ashare", "full"): _ASHARE_FULL,
    ("ashare", "slim"): _ASHARE_SLIM,
    ("us", "full"): _US_FULL,
    ("us", "slim"): _US_SLIM,
    ("crypto", "full"): _US_FULL,
    ("crypto", "slim"): _US_SLIM,
    ("other", "full"): _US_FULL,
    ("other", "slim"): _US_SLIM,
}


def market_key_for(ticker: str, asset_type: str) -> str:
    """Return the explicit market branch without changing today's block menus."""
    if _is_ashare(ticker):
        return "ashare"
    if asset_type == "crypto":
        return "crypto"
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]*", ticker) and not ticker.upper().endswith(
        (".HK", ".L", ".TO", ".AX")
    ):
        return "us"
    return "other"


def collect_harvest_snapshot(
    ticker: str,
    trade_date: str,
    asset_type: str,
    peers: list[str],
    *,
    slim: bool,
    explicit_name: str | None = None,
    output_dir: Path | str | None = None,
) -> dict:
    """Collect live domain-source returns into one non-executable snapshot.

    The block suppliers remain the production suppliers.  Joining, headers, clocks and
    file placement happen later in :func:`render_harvest_snapshot`, which is the only
    function used by offline replay.
    """
    ticker = normalize_symbol(ticker)
    d = datetime.strptime(trade_date, "%Y-%m-%d")
    peers = [normalize_symbol(peer) for peer in peers]
    set_config(DEFAULT_CONFIG)
    end = trade_date
    news_start = (d - timedelta(days=14)).strftime("%Y-%m-%d")
    identity = resolve_instrument_identity(ticker)
    instrument_context = build_instrument_context(ticker, asset_type, identity)
    is_ashare = _is_ashare(ticker)
    l1_row = _load_l1_row(ticker, trade_date) if (slim and is_ashare) else None
    side_artifacts: dict[str, str] = {}
    ctx: dict = {
        "ticker": ticker,
        "trade_date": trade_date,
        "end": end,
        "news_start": news_start,
        "peers": peers,
        "identity": identity,
        "slim": slim,
        "explicit_name": explicit_name,
        "l1_row": l1_row,
        "snapshot_section": "",
        "out_dir": Path(output_dir) if output_dir is not None else Path("."),
        "side_artifacts": side_artifacts,
    }
    market_key = market_key_for(ticker, asset_type)
    blocks = []
    for name in HARVEST_PLAN[(market_key, "slim" if slim else "full")]:
        text = BLOCKS[name](ctx)
        if text is not None:
            blocks.append({"name": name, "text": text})
    return {
        "schema_version": 1,
        "ticker": ticker,
        "trade_date": trade_date,
        "asset_type": asset_type,
        "peers": peers,
        "slim": slim,
        "market": market_key,
        "instrument_context": instrument_context,
        "blocks": blocks,
        "side_artifacts": side_artifacts,
    }


def render_harvest_snapshot(
    snapshot: dict,
    *,
    output_dir: Path | str,
    clock: datetime,
) -> dict[str, Path]:
    """Render a frozen stock snapshot without reading ambient context or wall time."""
    if not isinstance(clock, datetime):
        raise TypeError("clock must be a datetime")
    ticker = str(snapshot["ticker"])
    trade_date = str(snapshot["trade_date"])
    slim = bool(snapshot["slim"])
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = [
        f"# Data context — {ticker} @ {trade_date}\n",
        f"_Harvested {clock.isoformat(timespec='seconds')} via project data tools + yfinance v2 "
        f"enrichments. No LLM used._\n",
        f"\n## Instrument identity\n\n{snapshot['instrument_context']}\n",
        *(str(block["text"]) for block in snapshot["blocks"]),
    ]
    for name, body in (snapshot.get("side_artifacts") or {}).items():
        target = Path(name)
        if target.name != name or target.is_absolute():
            raise ValueError(f"unsafe stock side artifact: {name!r}")
        (out_dir / name).write_text(str(body), encoding="utf-8")
    if slim:
        primary = _write_slim_files(out_dir, ticker, trade_date, parts)
        deep = out_dir / f"{ticker}_{trade_date}_slim_deep.md"
        return {"primary": primary, **({"deep": deep} if deep.is_file() else {})}
    primary = out_dir / f"{ticker}_{trade_date}.md"
    primary.write_text("".join(parts), encoding="utf-8")
    indicators = out_dir / f"{ticker}_{trade_date}_indicators.md"
    return {
        "primary": primary,
        **({"indicators": indicators} if indicators.is_file() else {}),
    }


def _main_unlocked() -> int:
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
    datetime.strptime(trade_date, "%Y-%m-%d")
    asset_type = pos[2] if len(pos) > 2 else "stock"
    peers_arg = pos[3] if len(pos) > 3 else ""
    peers = [normalize_symbol(p.strip()) for p in peers_arg.split(",") if p.strip()] \
        or PEER_MAP.get(ticker.upper(), [])
    out_dir = _output_dir(trade_date, slim=slim, explicit=explicit_out_dir)

    print(f"[harvest v4{' SLIM' if slim else ''}] {ticker} @ {trade_date} "
          f"(asset_type={asset_type}, peers={peers or 'none'})", flush=True)
    snapshot = collect_harvest_snapshot(
        ticker,
        trade_date,
        asset_type,
        peers,
        slim=slim,
        explicit_name=explicit_name,
        output_dir=out_dir,
    )
    try:
        from autoresearch.trace.source_receipts import record_active_response

        record_active_response(
            provider="stock_harvest",
            endpoint="stock.harvest.snapshot.v1",
            params={
                "ticker": ticker,
                "analysis_date": trade_date,
                "asset_type": asset_type,
                "peers": peers,
                "slim": slim,
            },
            outcome=snapshot,
            consumer_artifact_ids=["stock.slim" if slim else "stock.context"],
        )
    except Exception:  # noqa: BLE001 - evidence failure must not replace business output
        print("[warn] stock harvest source snapshot evidence incomplete", file=sys.stderr)
    from autoresearch.trace.operation_clock import operation_clock

    rendered = render_harvest_snapshot(snapshot, output_dir=out_dir, clock=operation_clock())
    out_path = rendered["primary"]
    print(f"\n[saved] {out_path}  ({out_path.stat().st_size:,} bytes)", flush=True)

    from autoresearch.data.contracts import degradations, render as _deg_render
    degs = degradations()
    if degs:
        print(f"[数据降级账] {len(degs)} 条:{_deg_render(degs)}", flush=True)

    # D6.4:`AUTORESEARCH_RUN_ID` 在场时把这一步记进法证现场(**进程内** checkpoint,
    # 不套 scan 那层 traced 壳)。不开 run 时 `record_stage` 是真 no-op —— 零留痕,
    # 与今天逐字相同。取证故障只走 stderr,永不改本函数的返回码。
    from autoresearch.analyze.runctl import record_stage
    tier_key = "slim" if slim else "full"
    record_stage(
        "harvest",
        outputs=[path for path in _harvest_outputs(out_dir, ticker, trade_date, slim)
                 if path.is_file()],
        inputs=[ticker, trade_date],
        metrics={
            "tier": tier_key, "market": snapshot["market"], "blocks": len(snapshot["blocks"]) + 3,
            "bytes": out_path.stat().st_size, "degradations": len(degs),
            "peers": len(peers),
        },
        status="DEGRADED" if degs else "SUCCEEDED",
    )
    return 0


def main() -> int:
    from autoresearch.trace.write_guard import guarded_ambient_write

    with guarded_ambient_write("stock.harvest"):
        return _main_unlocked()


def _harvest_outputs(out_dir: Path, ticker: str, trade_date: str, slim: bool) -> list[Path]:
    """这一趟 harvest **可能**写出的全部文件(在场与否由调用方过滤)。

    两档各自的侧文件是真实存在的第二产物(slim 的 `_slim_deep.md`、full 的
    `_indicators.md`),漏掉它们等于让现场少一半 —— 而它们正是 P4 深核与指标全序列
    的落点。名字在这里派生一次,不在 checkpoint 调用点再手抄一遍。
    """
    stem = f"{ticker}_{trade_date}"
    if slim:
        return [out_dir / f"{stem}_slim.md", out_dir / f"{stem}_slim_deep.md"]
    return [out_dir / f"{stem}.md", out_dir / f"{stem}_indicators.md"]


if __name__ == "__main__":
    raise SystemExit(main())

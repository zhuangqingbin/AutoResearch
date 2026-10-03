"""Deterministic macro + 中观 harvester for the "Claude-as-engine" workflow.

Top-down sibling of autoresearch.analyze.harvest. Harvests REGIONAL macro
(US via FRED aliases, China via akshare macro_china_*, Global via FRED
international series by raw ID), the CROSS-ASSET price basket (yfinance), and
A-share 中观 (sector fund-flow, Dragon-Tiger, limit-up sentiment, northbound),
then dumps every raw output to one markdown file. No LLM is instantiated — only
free vendors (yfinance keyless; FRED needs FRED_API_KEY; akshare optional).

Usage:
    python -m autoresearch.macro.harvest [YYYY-MM-DD]
"""
import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from autoresearch.common import workspace as ws

ROOT = Path(__file__).resolve().parents[2]  # repo root (autoresearch/macro/ → ../../)


def _load_env(env_path: Path) -> None:
    """Minimal .env loader (no dependency); never overrides the real environment.
    Verbatim from autoresearch.analyze.harvest."""
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

from autoresearch.agents.utils.agent_utils import get_macro_indicators  # noqa: E402
from autoresearch.dataflows.config import set_config  # noqa: E402
from autoresearch.default_config import DEFAULT_CONFIG  # noqa: E402
from autoresearch.trace.source_receipts import capture_active_responses  # noqa: E402

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
    Verbatim from autoresearch.analyze.harvest."""
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
    Verbatim from autoresearch.analyze.harvest."""
    print(f"  - {title} ...", flush=True)
    body = _section_body(fn, *args, **kwargs)
    return f"\n## {title}\n\n{body}\n"


def _section_body(fn, *args, **kwargs) -> str:
    """Return the supplier result/error body without applying report layout."""
    try:
        out = fn.invoke(*args, **kwargs) if hasattr(fn, "invoke") else fn(*args, **kwargs)
        body = (out or "").strip() or "_(empty)_"
    except Exception as e:  # noqa: BLE001 — one flaky vendor must not kill the harvest
        body = f"_ERROR fetching this section: {e}_\n```\n{traceback.format_exc()}```"
    return body


@capture_active_responses
def us_macro_block(
    curr_date: str, *, vintage_date: str | None = None,
    knowledge_cutoff: str | None = None,
) -> str:
    """US regional macro: every US_FRED series via the project's FRED tool."""
    out = []
    for series in US_FRED:
        try:
            md = get_macro_indicators.invoke({
                "indicator": series, "curr_date": curr_date,
                "vintage_date": vintage_date, "knowledge_cutoff": knowledge_cutoff,
            })
        except Exception as e:  # noqa: BLE001
            md = f"_({series} unavailable: {e})_"
        out.append(f"### {series}\n\n{md}")
    return "\n\n".join(out)


@capture_active_responses
def global_macro_block(
    curr_date: str, *, vintage_date: str | None = None,
    knowledge_cutoff: str | None = None,
) -> str:
    """Global outer layer: FRED international series by raw ID (passthrough).
    Series that FRED does not carry return MACRO_DATA_UNAVAILABLE — kept inline so
    the build-time smoke run can spot and drop them."""
    out = []
    for label, series_id in INTL_FRED.items():
        try:
            md = get_macro_indicators.invoke({
                "indicator": series_id, "curr_date": curr_date,
                "vintage_date": vintage_date, "knowledge_cutoff": knowledge_cutoff,
            })
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


def meso_ashare_best(curr_date: str) -> str:
    """A股中观:tushare(北向汇总/两融/行业资金/涨停/指数估值)优先 + akshare 补龙虎榜游资。

    tushare(api.tushare.pro,非 push2)更可靠,且独有**两融余额 + 指数估值分位**;akshare meso
    块补**龙虎榜(游资席位,tushare 未覆盖)**,push2 被封时自身降级为 WebSearch 指令。两者皆失败才纯降级。
    """
    parts: list[str] = []
    try:
        from autoresearch.macro.tushare_macro import meso_block_ts
        b = meso_block_ts(curr_date)
        if b:
            parts.append(b)
    except Exception:  # noqa: BLE001 — tushare 不可用 → 走 akshare
        pass
    try:
        parts.append("---\n\n**akshare 补充(龙虎榜游资 + 交叉验证;push2 被封时自动降级为网查指令)**:\n\n"
                     + meso_ashare_block(curr_date))
    except Exception as e:  # noqa: BLE001
        if not parts:
            return (f"_A股中观取数失败({e})→ WebSearch:行业资金流入流出排名 / 龙虎榜游资 / "
                    "涨停家数·连板 / 北向资金 / 两融余额,标『实时网查』。_")
    return "\n\n".join(parts)


# ═════════════ 外源三段(D-4:波动率地形 / 隔夜 tape / 未来 7 日海外日历)═════════════
#
# design: docs/specs/2026-08-28-external-evidence-expansion-design.md §5.3 + §4「宏观 full」行。
# 三条边界写在最前面,因为它们就是这一段存在的形状:
#
#  ① **全部 B 级**:任一源失败 → 记账 + 该段整块省略,**永不抛**(取数挂了不许阻断宏观报告)。
#     且必须分得清「源成功但真空」与「请求/解析失败」——§9 明令二者不可都落成空数组,否则
#     「源坏了」和「今天真没有」在账上长得一模一样。
#  ② **只进研究/展示层**:落 `$CTX/macro/<date>/global_tape.json`(机读)+ `macro_state` 只写;
#     **不进 scan 判断层**(`strategist_pack.ALLOWED_KEYS` 里没有本产物的任何键;策略师是否读
#     属 B-1,受 08-26 冻结)。
#  ③ **ZQ 只有一种合法读法**:`100−价` = **合约月平均有效利率**。把它翻译成政策路径的概率,
#     需要另做基于 EFFR 与相邻月合约的加权求解 + 独立验算(§5.3 明令本波不做)—— 所以本模块
#     连那种措辞都不许出现(`tests/macro/test_global_tape.py` 有负锚断言)。

#: 隔夜 tape 的**展示顺序**(§5.3 / 附录 A 的 19 标的篮子)。
#: ⚠️ 这不是取数篮子的事实源 —— 取哪些标的由 `data/sources/yf_tape.fetch_global_tape` 决定;
#: 本元组只管「渲染时按什么顺序排」,源多给的符号按帧序附在表尾(不丢),源少给的直接不出现
#: (presence-gated,不印 n/a 占位行)。两个地方各管一件事,不构成第二个事实源。
GLOBAL_TAPE_SYMBOLS: tuple[str, ...] = (
    "^VIX", "^VIX3M", "^SKEW", "^MOVE",
    "^GSPC", "^NDX", "^SOX", "^HSI", "000001.SS",
    "DX-Y.NYB", "^TNX", "CL=F", "GC=F", "USDCNH=X",
    "KWEB", "FXI", "ASHR", "SMH", "XLK",
)

#: 三段标题(machine anchor:playbook 与测试都按它定位;改名 = 改契约)。
SEC_VOL = "波动率与仓位地形(VIX 族 + ZQ 合约月平均有效利率)"
SEC_TAPE = "隔夜 tape(19 标的 · 1 日 / 5 日 %)"
SEC_CAL = "未来 7 日海外日历(FRED releases + FOMC · 标 time_quality)"
EXTERNAL_SECTION_TITLES: tuple[str, ...] = (SEC_VOL, SEC_TAPE, SEC_CAL)

#: ZQ 的唯一合法口径词 + 禁用措辞(§5.3)。
ZQ_LABEL = "合约月平均有效利率"

GLOBAL_TAPE_JSON = "global_tape.json"
CALENDAR_DAYS = 7

#: 进 `macro_state` 的 ≤8 个数(§5.3),及各自的取数位置。
MACRO_STATE_TAPE_KEYS: tuple[str, ...] = (
    "vix", "vix_term_ratio", "skew", "move", "ust10y", "dxy", "usdcnh",
    "zq_front_month_avg_rate",
)
#: 直接取 tape 行 `close` 的键;不在表里的键(term_ratio / zq)取源的派生量。
_TAPE_SYMBOL_FOR_KEY: dict[str, str] = {
    "vix": "^VIX", "skew": "^SKEW", "move": "^MOVE",
    "ust10y": "^TNX", "dxy": "DX-Y.NYB", "usdcnh": "USDCNH=X",
}
_DERIVED_KEYS = ("vix_term_ratio", "vix_pct_1y", "vix_pct_1y_n", "zq_front_month_avg_rate")
#: 本模块的派生量名 → 源模块里可能用的名字(顺序即优先级)。
#: `yf_tape` 把 VIX 分位叫 `vix_1y_pctile` / `vix_1y_pctile_obs`,只认本地名就等于
#: 读不到 —— 而读不到的表现是「n/a(源未派生)」和「样本不足(n=未知)」,看起来
#: 完全像是「今天源没给」,不像接线断了。
_DERIVED_ALIASES: dict[str, tuple[str, ...]] = {
    "vix_pct_1y": ("vix_pct_1y", "vix_1y_pctile"),
    "vix_pct_1y_n": ("vix_pct_1y_n", "vix_1y_pctile_obs"),
}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _degrade(endpoint: str, reason: str, *, kind: str = "degraded") -> None:
    """B 级降级记账 —— 记账本身失败也不许炸取数(降级不留痕才是真病,但记账不是硬依赖)。"""
    try:
        from autoresearch.data.contracts import record_degradation
        record_degradation(endpoint, reason, kind=kind)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 降级记账失败({endpoint}:{reason}):{e}", file=sys.stderr)


def _fnum(v):
    """→ float | None(None/NaN/空/不可解析一律 None,永不抛)。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f       # NaN


def _fmt(v, nd: int = 2) -> str:
    f = _fnum(v)
    return "n/a" if f is None else f"{f:.{nd}f}"


def _fmt_pct(v) -> str:
    """`pct_1d`/`pct_5d` 单位 = **百分数**(3.5 = +3.5%),与 tushare `pct_chg` 同口径。"""
    f = _fnum(v)
    return "n/a" if f is None else f"{f:+.2f}%"


def _tape_rows(df) -> list[dict]:
    """DataFrame(或 list[dict])→ list[dict];形状不认识就当空(不猜)。"""
    if df is None:
        return []
    if hasattr(df, "to_dict"):
        try:
            return [dict(r) for r in df.to_dict(orient="records")]
        except Exception:  # noqa: BLE001
            return []
    try:
        return [dict(r) for r in df]
    except Exception:  # noqa: BLE001
        return []


def _tape_attr(df, key):
    """派生量:`df.attrs[名]` → `df.attrs["derived"][名]` → 同名列首行。

    三处都找、且按 `_DERIVED_ALIASES` 试所有别名 —— 源模块把整块派生量放在
    `attrs["derived"]` 里(而不是摊在顶层),只看顶层就会四个量全 None。
    """
    names = _DERIVED_ALIASES.get(key, (key,))
    try:
        attrs = getattr(df, "attrs", None) or {}
    except Exception:  # noqa: BLE001
        attrs = {}
    derived = attrs.get("derived") if isinstance(attrs.get("derived"), dict) else {}
    for name in names:
        if name in attrs:
            return attrs[name]
        if name in derived:
            return derived[name]
    for name in names:
        try:
            if hasattr(df, "columns") and name in df.columns and len(df):
                return df[name].iloc[0]
        except Exception:  # noqa: BLE001
            pass
    return None


def _macro_state_numbers(by_symbol: dict, derived: dict) -> dict:
    """→ `macro_state` 的 ≤8 个数(缺 → None)。

    **`ust10y` 写 `^TNX` 原始报价,不做任何乘除**:yfinance 的 ^TNX 口径历史上在「×10」与
    「%」之间变过,静默换算就是下一个「窄表毒化」(单位错 10 倍且全程无人报警)。单位说明
    随 `global_tape.json` 一起落盘,读的人自己看口径。
    """
    return {
        key: (by_symbol.get(_TAPE_SYMBOL_FOR_KEY[key], {}).get("close")
              if key in _TAPE_SYMBOL_FOR_KEY else derived.get(key))
        for key in MACRO_STATE_TAPE_KEYS
    }


def global_tape_payload(as_of: str) -> dict:
    """`fetch_global_tape` → 机读 payload。**永不抛**;失败/空 → `ok=False` + B 级降级记账。"""
    payload: dict = {"as_of": as_of, "ok": False, "status": "failure", "reason": None,
                     "us_date": None, "fetched_at": None, "session_complete": None,
                     "rows": [], "derived": {}, "numbers": {}, "pct_unit": "percent"}
    try:
        from autoresearch.data.sources.yf_tape import fetch_global_tape
    except Exception as e:  # noqa: BLE001 — 源模块缺席 = B 级降级,不是 ImportError 崩栈
        payload["reason"] = f"源模块不可用:{e}"
        _degrade("global_tape", payload["reason"])
        return payload
    try:
        df = fetch_global_tape(as_of)
    except Exception as e:  # noqa: BLE001
        payload["reason"] = f"取数失败:{e}"
        _degrade("global_tape", payload["reason"])
        return payload
    rows = _tape_rows(df)
    clean = []
    for r in rows:
        sym = str(r.get("symbol") or "").strip()
        if not sym:
            continue
        sc = r.get("session_complete")
        clean.append({
            "symbol": sym,
            "close": _fnum(r.get("close")),
            "pct_1d": _fnum(r.get("pct_1d")),
            "pct_5d": _fnum(r.get("pct_5d")),
            "us_date": None if r.get("us_date") is None else str(r.get("us_date"))[:10],
            "session_complete": None if sc is None else bool(sc),
            "fetched_at": None if r.get("fetched_at") is None else str(r.get("fetched_at")),
        })
    if not clean:
        # 19 标的一行不剩不是「合法空」——按降级记(进告警面),别和真空混在一起。
        payload["status"] = "empty"
        payload["reason"] = "源返回空帧(0 行)"
        _degrade("global_tape", payload["reason"])
        return payload
    derived: dict = {}
    for key in _DERIVED_KEYS:
        raw = _tape_attr(df, key)
        if key == "vix_pct_1y_n":
            try:
                derived[key] = None if raw is None else int(raw)
            except (TypeError, ValueError):
                derived[key] = None
        else:
            derived[key] = _fnum(raw)
    sessions = [r["session_complete"] for r in clean if r["session_complete"] is not None]
    payload.update({
        "ok": True, "status": "ok", "rows": clean, "derived": derived,
        "us_date": next((r["us_date"] for r in clean if r["us_date"]), None),
        "fetched_at": next((r["fetched_at"] for r in clean if r["fetched_at"]), None),
        "session_complete": (all(sessions) if sessions else None),
        "numbers": _macro_state_numbers({r["symbol"]: r for r in clean}, derived),
    })
    return payload


def _ev_get(ev, key, default=None):
    """事件对象取字段:dict / dataclass / 普通对象三种都吃(`ExternalEvent` 的实现在别处)。"""
    if isinstance(ev, dict):
        return ev.get(key, default)
    return getattr(ev, key, default)


def _norm_event(ev) -> dict | None:
    """`ExternalEvent` → 渲染用行(日期不可解析 → None,整行丢弃,不猜)。"""
    raw_date = _ev_get(ev, "local_date") or _ev_get(ev, "date")
    d = str(raw_date or "")[:10]
    if not _DATE_RE.match(d):
        return None
    sched = _ev_get(ev, "scheduled_at_utc")
    return {
        "event_id": str(_ev_get(ev, "event_id") or ""),
        "revision": str(_ev_get(ev, "revision") or ""),
        "local_date": d,
        "time_quality": str(_ev_get(ev, "time_quality") or "UNKNOWN").upper(),
        "event_type": str(_ev_get(ev, "event_type") or "other"),
        "subject": str(_ev_get(ev, "subject") or "—").strip(),
        "scheduled_at_utc": None if sched is None else str(sched),
        "timezone": str(_ev_get(ev, "timezone") or ""),
        "status": str(_ev_get(ev, "status") or "scheduled"),
        "source_url": str(_ev_get(ev, "source_url") or "").strip(),
    }


def overseas_calendar_payload(as_of: str, days: int = CALENDAR_DAYS) -> dict:
    """FRED releases + FOMC 年表 → 未来 `days` 日事件行。**永不抛**;每源独立记账。

    只做**过滤 + 规范化,不猜时刻**:源给不出时刻的行保持 `DATE_ONLY`,渲染时明写
    「仅日期,无时刻」——§2 明令 `DATE_ONLY` 不得伪装成精确时点(更不得据此推 BMO/AMC)。
    两源都失败 → `ok=False` → 该段整块省略;任一源成功即出段(另一源的失败写在段内)。
    """
    try:
        end_dt = datetime.strptime(as_of, "%Y-%m-%d") + timedelta(days=days)
    except (TypeError, ValueError):
        return {"as_of": as_of, "ok": False, "status": "failure", "events": [],
                "sources": {}, "reason": f"as_of 不可解析:{as_of!r}", "window": {}}
    end = end_dt.strftime("%Y-%m-%d")
    payload: dict = {"as_of": as_of, "ok": False, "status": "failure", "reason": None,
                     "window": {"start": as_of, "end": end, "days": days},
                     "events": [], "sources": {}}
    raw: list = []
    ok_any = False

    try:
        # ⚠️ 必须走 `fetch_events`(→ `ExternalEvent` 列表)。`fetch_releases` 返回的是**原始帧**:
        # `df or []` 先炸在 DataFrame 真值判定上,即便不炸,`list(df)` 得到的也是**列名**——
        # 那条腿会恒零事件,而 `sources` 那行还写着「ok(3 条)」(数的是列)。
        from autoresearch.data.sources.fred_calendar import fetch_events
        got, outcome = fetch_events(as_of, end, first_seen_ts=datetime.now(timezone.utc))
        if str(getattr(outcome, "status", "")) == "FAILED":
            payload["sources"]["fred_releases"] = f"失败:{getattr(outcome, 'reason', '')}"
        else:
            got = list(got or [])
            raw += got
            ok_any = True
            payload["sources"]["fred_releases"] = f"ok({len(got)} 条)"
    except Exception as e:  # noqa: BLE001
        payload["sources"]["fred_releases"] = f"失败:{e}"
        _degrade("fred_calendar", f"releases 取数失败:{e}")

    try:
        from autoresearch.data.sources.fomc_calendar import load_fomc
        got_fomc: list = []
        failed_years: list[str] = []
        for year in sorted({int(as_of[:4]), int(end[:4])}):
            try:
                got_fomc += list(load_fomc(year) or [])
            except Exception as e:  # noqa: BLE001 — 一年挂了不拖累另一年
                failed_years.append(str(year))
                _degrade("fomc_calendar", f"{year} 年表不可用:{e}")
        if failed_years and not got_fomc:
            # 每一年都没读到 = 这条腿没成立。报「ok(0 条)」会让「源坏了」和「窗内真没有」
            # 在账上长得一模一样 —— §9 明令二者必须可区分。
            payload["sources"]["fomc"] = f"失败:{'、'.join(failed_years)} 年表不可用"
        else:
            raw += got_fomc
            ok_any = True
            payload["sources"]["fomc"] = (
                f"ok({len(got_fomc)} 条)" if not failed_years
                else f"部分({len(got_fomc)} 条;{'、'.join(failed_years)} 年表不可用)")
    except Exception as e:  # noqa: BLE001
        payload["sources"]["fomc"] = f"失败:{e}"
        _degrade("fomc_calendar", f"年表模块不可用:{e}")

    if not ok_any:
        payload["reason"] = "FRED releases 与 FOMC 年表均不可用"
        return payload

    seen: set[tuple] = set()
    events: list[dict] = []
    for ev in raw:
        row = _norm_event(ev)
        if row is None or not (as_of <= row["local_date"] <= end):
            continue
        key = (row["event_id"], row["revision"]) if row["event_id"] else \
            (row["local_date"], row["subject"], row["event_type"])
        if key in seen:
            continue
        seen.add(key)
        events.append(row)
    events.sort(key=lambda r: (r["local_date"], r["event_type"], r["subject"]))
    payload.update({"ok": True, "status": "ok" if events else "empty", "events": events})
    if not events:
        payload["reason"] = "窗内 0 条(源成功但真空)"
        _degrade("overseas_calendar", f"{as_of}..{end} 窗内 0 条事件", kind="legit_empty")
    return payload


def vol_positioning_block(payload: dict) -> str:
    """段①:波动率与仓位地形(纯渲染;**不含任何政策路径措辞** —— 见本节 ③)。"""
    by = {r["symbol"]: r for r in payload.get("rows") or []}
    d = payload.get("derived") or {}

    pct, n = d.get("vix_pct_1y"), d.get("vix_pct_1y_n")
    if pct is None:
        pct_cell = f"样本不足(n={n if n is not None else '未知'};需 ≥60 个有效观测)"
    elif pct <= 1:
        pct_cell = f"{pct * 100:.0f}%(源 0–1 口径原值 {pct:.3f})"
    else:
        pct_cell = f"{pct:.1f}(源口径非 0–1,原样呈现)"

    ratio = d.get("vix_term_ratio")
    ratio_cell = "n/a(源未派生)" if ratio is None else f"{ratio:.3f}"
    zq = d.get("zq_front_month_avg_rate")
    zq_cell = "n/a(源未派生)" if zq is None else f"{zq:.2f}%"

    def close(sym: str) -> str:
        return _fmt(by.get(sym, {}).get("close"))

    rows = [
        ("`^VIX` 点值", close("^VIX"), "指数波动率"),
        ("`^VIX` 1 年分位", pct_cell, "自算分位;不足 60 观测 → 样本不足"),
        ("`^VIX3M`", close("^VIX3M"), "3 个月期波动率"),
        ("期限比 `VIX/VIX3M`", ratio_cell, "**>1 = 倒挂 / 压力**(源派生)"),
        ("`^SKEW`", close("^SKEW"), "尾部定价"),
        ("`^MOVE`", close("^MOVE"), "美债波动"),
        (f"`ZQ=F` {ZQ_LABEL}", zq_cell, f"`100−价`;口径到此为止,**只读作{ZQ_LABEL}**"),
    ]
    head = "| 项 | 读数 | 口径 |\n|---|---:|---|"
    body = "\n".join(f"| {a} | {b} | {c} |" for a, b, c in rows)
    tail = (
        f"\n\n_口径纪律:本表只描述**地形与量级**,不含方向,也不是配置建议。"
        f"`ZQ` 一栏只读作**{ZQ_LABEL}**;要由它推出政策路径的概率,须另做基于 EFFR 与相邻月"
        f"合约的加权求解并独立验算(设计稿 §5.3 明令本波不做)——本段不作此推断。_"
        f"\n\n_as-of {payload.get('as_of')} ｜ 美股日 {payload.get('us_date') or '未记'} "
        f"｜ session_complete={payload.get('session_complete')} ｜ 取数 "
        f"{payload.get('fetched_at') or '未记'}_"
    )
    return head + "\n" + body + tail


def overnight_tape_block(payload: dict) -> str:
    """段②:隔夜 tape 表(顺序 = `GLOBAL_TAPE_SYMBOLS`,源多给的按帧序附表尾)。"""
    rows = payload.get("rows") or []
    by = {r["symbol"]: r for r in rows}
    ordered = [by[s] for s in GLOBAL_TAPE_SYMBOLS if s in by]
    ordered += [r for r in rows if r["symbol"] not in set(GLOBAL_TAPE_SYMBOLS)]
    head = ("| 标的 | 收 | Δ1d | Δ5d | 美股日 | session_complete |\n"
            "|---|---:|---:|---:|---|---|")
    body = "\n".join(
        f"| `{r['symbol']}` | {_fmt(r['close'], 4)} | {_fmt_pct(r['pct_1d'])} "
        f"| {_fmt_pct(r['pct_5d'])} | {r['us_date'] or 'n/a'} "
        f"| {'—' if r['session_complete'] is None else r['session_complete']} |"
        for r in ordered)
    tail = (
        f"\n\n_{len(ordered)} 行 ｜ Δ 单位 = **百分数**(3.5 = +3.5%,与 tushare `pct_chg` 同口径)"
        f" ｜ `session_complete=False` = 该标的当日尚未收盘,读数会变。_"
        f"\n\n_主尺横跨一整个美股交易日(T+1 15:00 CST 买 → T+2 09:30 CST 卖),"
        f"所以这张表是**隔夜窗内的已知地形**,不是方向指令。_"
    )
    return head + "\n" + body + tail


def _time_cell(ev: dict) -> str:
    tq = ev.get("time_quality")
    if tq == "TIMED":
        stamp = (ev.get("scheduled_at_utc") or "")[:16]
        return f"TIMED({stamp or '时刻见源'} UTC)"
    if tq == "DATE_ONLY":
        return "DATE_ONLY(仅日期,无时刻)"
    return f"{tq or 'UNKNOWN'}(时刻未知)"


def overseas_calendar_block(payload: dict) -> str:
    """段③:未来 7 日海外日历(每行标 `time_quality`;`DATE_ONLY` 明写无时刻)。"""
    win = payload.get("window") or {}
    events = payload.get("events") or []
    srcs = "、".join(f"{k}={v}" for k, v in (payload.get("sources") or {}).items())
    if not events:
        body = (f"_窗内 0 条({win.get('start')} → {win.get('end')});"
                f"这是「源成功但真空」,不是取数失败。_")
    else:
        head = "| 日期 | 时刻质量 | 类型 | 事件 | 源 |\n|---|---|---|---|---|"
        body = head + "\n" + "\n".join(
            f"| {e['local_date']} | {_time_cell(e)} | {e['event_type']} | {e['subject']} "
            f"| {e['source_url'] or '_(源未给 URL)_'} |" for e in events)
    tail = (
        f"\n\n_窗:{win.get('start')} → {win.get('end')}({win.get('days')} 日)｜源:{srcs or '—'}_"
        f"\n\n_`DATE_ONLY` = 源只给了日期、**无时刻**:只能当「当日风险」读,不得当精确时点,"
        f"也不得据此推 BMO/AMC 或触发窄窗哨兵(设计稿 §2)。日历是**人工复核提示**,"
        f"不自动改仓位、不改评级。_"
    )
    return body + tail


def external_sections(tape: dict, cal: dict) -> list[tuple[str, str]]:
    """→ `[(标题, 正文)]`,**presence-gated**:某源失败 → 它的段整块不出现。

    不印「空表 + 一句取数失败」是刻意的:一段空表在报告里长得像「今天没事」,而降级已经在
    `degraded` 账上留了痕 —— 展示层的沉默 + 账本的留痕,比展示层的噪声更诚实。
    """
    out: list[tuple[str, str]] = []
    if tape.get("ok"):
        out.append((SEC_VOL, vol_positioning_block(tape)))
        out.append((SEC_TAPE, overnight_tape_block(tape)))
    if cal.get("ok"):
        out.append((SEC_CAL, overseas_calendar_block(cal)))
    return out


def write_global_tape_json(
    out_dir: Path | str,
    tape: dict,
    cal: dict,
    *,
    clock: datetime | None = None,
) -> Path:
    """机读产物 `$CTX/macro/<date>/global_tape.json`(**失败也落**,带 `ok=false` + 原因)。

    `macro_state_numbers` 是给 `macro/state.py` 的唯一入口 —— 抽数逻辑只有这一处,state 侧
    只做「≤8 个数」的上限守卫,不重算(两处各算一遍 = 两个事实源)。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "as_of": tape.get("as_of"),
        "generated_at": (clock or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "ok": bool(tape.get("ok")),
        "pct_unit": "percent",
        "units": {
            "ust10y": "`^TNX` 原始报价(yfinance 口径,未做任何乘除)",
            "zq_front_month_avg_rate": f"`ZQ=F` 的 `100−价` = {ZQ_LABEL}(%)",
            "vix_term_ratio": "`^VIX`/`^VIX3M`(源派生;>1 = 倒挂/压力)",
            "vix_pct_1y": "1 年分位(源派生;不足 60 观测 → null)",
        },
        "consumption_boundary": (
            "研究/展示层 + macro_state 只写;不进 scan 判断层"
            "(strategist_pack.ALLOWED_KEYS 无本产物任何键 —— B-1 冻结中)"),
        "tape": tape,
        "calendar": cal,
        "macro_state_numbers": tape.get("numbers") or {},
    }
    path = out_dir / GLOBAL_TAPE_JSON
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def collect_harvest_snapshot(
    trade_date: str,
    *,
    scan_root: Path | str | None = None,
    vintage_date: str | None = None,
    knowledge_cutoff: str | None = None,
) -> dict:
    """Collect macro suppliers into a structured snapshot, without rendering files."""
    datetime.strptime(trade_date, "%Y-%m-%d")
    set_config(DEFAULT_CONFIG)
    sections = []
    for title, fn in (
        ("US macro (FRED)", us_macro_block),
        ("China macro (akshare macro_china)", china_macro_block),
        ("Global outer layer (FRED international + WebSearch)", global_macro_block),
        ("Cross-asset price basket (yfinance)", cross_asset_block),
    ):
        print(f"  - {title} ...", flush=True)
        fred_options = {}
        if fn in (us_macro_block, global_macro_block) and (
            vintage_date is not None or knowledge_cutoff is not None
        ):
            fred_options = {"vintage_date": vintage_date, "knowledge_cutoff": knowledge_cutoff}
        sections.append({"title": title, "body": _section_body(fn, trade_date, **fred_options)})
    tape = global_tape_payload(trade_date)
    cal = overseas_calendar_payload(trade_date)
    for title, body in external_sections(tape, cal):
        print(f"  - {title} ...", flush=True)
        sections.append({"title": title, "body": body})
    if not tape.get("ok"):
        print(f"  - (略过 tape 两段:{tape.get('reason')})", flush=True)
    if not cal.get("ok"):
        print(f"  - (略过海外日历段:{cal.get('reason')})", flush=True)
    meso_title = "A股中观 (北向/两融/行业资金/涨停/指数估值 — tushare 优先;akshare 补龙虎榜游资)"
    print(f"  - {meso_title} ...", flush=True)
    sections.append({"title": meso_title, "body": _section_body(meso_ashare_best, trade_date)})
    scan_meta_path = Path(scan_root or ws.scan_root()) / trade_date / "meta.json"
    try:
        scan_meta = json.loads(scan_meta_path.read_text(encoding="utf-8"))
        if not isinstance(scan_meta, dict):
            raise ValueError("scan meta must be an object")
    except FileNotFoundError:
        scan_meta = {"schema_version": 1, "present": False}
    except (OSError, ValueError, TypeError):
        scan_meta = {"schema_version": 1, "present": False, "unreadable": True}
    return {
        "schema_version": 1,
        "analysis_date": trade_date,
        "sections": sections,
        "tape": tape,
        "calendar": cal,
        "scan_meta": scan_meta,
    }


def render_harvest_snapshot(
    snapshot: dict,
    *,
    output_dir: Path | str,
    clock: datetime,
) -> dict[str, Path]:
    """Render only from the frozen macro snapshot and an explicit clock/root."""
    if not isinstance(clock, datetime):
        raise TypeError("clock must be a datetime")
    trade_date = str(snapshot["analysis_date"])
    parts = [
        f"# Macro data context — {trade_date}\n",
        f"_Harvested {clock.isoformat(timespec='seconds')} via project data tools "
        f"+ yfinance + akshare. No LLM used._\n",
    ]
    for section in snapshot["sections"]:
        body = str(section["body"]).strip() or "_(empty)_"
        parts.append(f"\n## {section['title']}\n\n{body}\n")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / "data.md"
    data_path.write_text("".join(parts), encoding="utf-8")
    tape_path = write_global_tape_json(
        out_dir,
        snapshot["tape"],
        snapshot["calendar"],
        clock=clock,
    )
    scan_meta_path = out_dir / "scan_meta.json"
    scan_meta_path.write_text(
        json.dumps(snapshot["scan_meta"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"data": data_path, "global_tape": tape_path, "scan_meta": scan_meta_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="宏观确定性数据采集")
    parser.add_argument("date", nargs="?", help="分析日 YYYY-MM-DD(缺省=今天)")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="显式工作目录(session adapter 使用);缺省保持历史 context 宏观目录",
    )
    parser.add_argument("--vintage-date", help="FRED historical information date YYYY-MM-DD")
    parser.add_argument("--knowledge-cutoff", help="FRED research cutoff date or timezone-aware ISO timestamp")
    args = parser.parse_args(argv)
    trade_date = args.date or date.today().isoformat()
    datetime.strptime(trade_date, "%Y-%m-%d")  # validate / fail loud on bad date
    print(f"[harvest-macro] @ {trade_date}", flush=True)
    print("[regional macro]", flush=True)
    print("[cross-asset]", flush=True)
    print("[外源:波动率地形 / 隔夜 tape / 海外日历]", flush=True)
    print("[A股中观]", flush=True)
    out_dir = (
        Path(args.output_dir)
        if args.output_dir is not None
        else ROOT / ws.context_root() / "macro" / trade_date
    )
    fred_options = {}
    if args.vintage_date is not None or args.knowledge_cutoff is not None:
        fred_options = {"vintage_date": args.vintage_date, "knowledge_cutoff": args.knowledge_cutoff}
    snapshot = collect_harvest_snapshot(trade_date, **fred_options)
    try:
        from autoresearch.trace.source_receipts import record_active_response

        record_active_response(
            provider="macro_harvest",
            endpoint="macro.harvest.snapshot.v1",
            params={"analysis_date": trade_date, **fred_options},
            outcome=snapshot,
            consumer_artifact_ids=["macro.data", "macro.global_tape", "macro.scan_meta"],
        )
    except Exception:  # noqa: BLE001
        print("[warn] macro harvest source snapshot evidence incomplete", file=sys.stderr)
    from autoresearch.trace.operation_clock import operation_clock

    rendered = render_harvest_snapshot(
        snapshot,
        output_dir=out_dir,
        clock=operation_clock(),
    )
    out_path = rendered["data"]
    print(f"\n[saved] {out_path}  ({out_path.stat().st_size:,} bytes)", flush=True)
    tape_path = rendered["global_tape"]
    tape = snapshot["tape"]
    cal = snapshot["calendar"]
    print(f"[saved] {tape_path}(ok={tape.get('ok')} · tape {len(tape.get('rows') or [])} 行 · "
          f"日历 {len(cal.get('events') or [])} 条)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

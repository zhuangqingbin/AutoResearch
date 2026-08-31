"""harvest 美股块:分析师一致预期/日历/同业相对估值/做空持仓/US regime/预测市场/
full 档瘦身后的价格·指标紧凑块。

design: docs/superpowers/plans/2026-08-31-stock-research-p0-p1.md task-10(D1.1 拆分)/
task-11(D1.5,Q7 瘦身)。

**纯搬家**(task-10 红线):函数体逐字节照搬自拆分前的 `autoresearch/analyze/harvest.py`,
只有 import 行按新文件位置调整。`prediction_markets_or_websearch_note` 原属拆分前
harvest.py 的宏观节(两市场通用),为把 harvest.py 压到 <500 行归到此处——它紧邻 FRED
宏观序列(同批"背景噪音"块),与 D1.1 拆分范围无冲突。

`_REALTIME_DISCLAIMER` **本模块自己定义**(不回引 harvest.py,见 `blocks_statements.py`
模块 docstring 的 DAG 说明——harvest.py 严格在顶端,`python -m` 直跑时任何回引都会
触发"模块执行两遍"的 ImportError)。3/4 消费点(`analyst_consensus`/`peer_relative`/
`ownership_short`)都在本文件,第 4 个(`analyst_actions_block`,blocks_external.py)
回引这里。

task-11(**有意的 golden 变化**,Q7 已裁,只动 full 档,lite/slim 一个字节不许变):
`weekly_aggregate`/`price_history_compact_block` 用 `dataflows.stockstats_utils
.load_ohlcv` **直接拿原始 DataFrame**(而不是走 `get_stock_data` 那个 `@tool`,它把
DataFrame 格式化成 CSV 文本就回不去了)——这正是 A股(tushare qfq)/美股(yfinance)
两条 OHLCV 路径本来就统一成 `Date/Open/High/Low/Close/Volume` 同形的那个函数
(`StockstatsUtils.get_stock_stats` 算指标用的也是它),"由同一 OHLCV 源 DataFrame
纯函数聚合" 指的就是这份缓存好的、PIT 过滤过的帧,不是重新取数。
`indicator_summary_table` 反过来**不碰底层计算**:直接解析 `get_indicators` 已经
渲染出的多指标文本(`## {indicator} values from … to …:` + 逐日 `{date}: {value}`
行,含 N/A 非交易日占位),按指标分段抠出"末值"(最近一个数值读数)与"5 日前值"
(从最新值起往回数第 5 个数值读数,跳过 N/A 行——即 5 个**交易日**前,不是 5 个
自然日前),算方向。这样 full 档的读数与旧版 100% 同源(同一次 `get_indicators`
调用产出的同一份文本),只是换了个摘要方式,不会因为另起一套指标计算而产生新的
数值分歧。
"""
import re
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from autoresearch.agents.utils.agent_utils import get_prediction_markets
from autoresearch.dataflows.stockstats_utils import load_ohlcv
from autoresearch.dataflows.symbol_utils import NoMarketDataError, normalize_symbol

#: `.info` 是 yfinance 的**实时**快照字段 —— 没有历史版本可取,取到的永远是"此刻"。
#: 4 处消费点(D1.4 附注 #19/#30 等,3 处在本文件、1 处在 blocks_external.py)不改取数
#: (改了也没有历史可回放),只诚实标注,别让读者以为这些数字是「curr_date 当天」的已核数据。
_REALTIME_DISCLAIMER = "_as-of: 运行时刻(实时字段,不可回放)_"


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

# Extra sector benchmark beyond SPY, when we can map it.
SECTOR_ETF = dict.fromkeys(("NVDA", "AMD", "AVGO", "MU", "INTC", "TSM", "QCOM", "TXN"), "SOXX")


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


def _pct(x) -> str:
    """Format a possibly-None percent cell."""
    return f"{x}%" if x is not None else "n/a"


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


# ═══════════════════════ task-11(D1.5,Q7):full 档瘦身 ═══════════════════════


def weekly_aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV 日线(`Date/Open/High/Low/Close/Volume` 列,与 `stockstats_utils.load_ohlcv`
    同形——A股 tushare qfq 路径与美股 yfinance 路径已统一成这个形状)→ 周线聚合
    (`W-FRI` 周五收盘对齐):O=周内首日开,H=周内最高,L=周内最低,C=周内末日收,
    V=周内成交量之和。纯函数,不取网不落盘;整周无交易日(如短周)聚合出的全 NaN 行
    会被丢弃(`dropna(subset=["Open"])`)。
    """
    d = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
    d["Date"] = pd.to_datetime(d["Date"])
    d = d.set_index("Date").sort_index()
    weekly = d.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    )
    return weekly.dropna(subset=["Open"]).reset_index()


def _render_ohlcv_table(df: pd.DataFrame) -> str:
    """OHLCV 帧(日线或 `weekly_aggregate` 的周线)→ markdown 表,日期格式统一 YYYY-MM-DD。"""
    lines = ["| Date | Open | High | Low | Close | Volume |", "|---|---:|---:|---:|---:|---:|"]
    for _, r in df.iterrows():
        lines.append(
            f"| {pd.Timestamp(r['Date']).strftime('%Y-%m-%d')} | {r['Open']:.2f} | "
            f"{r['High']:.2f} | {r['Low']:.2f} | {r['Close']:.2f} | {r['Volume']:,.0f} |"
        )
    return "\n".join(lines)


def price_history_compact_block(ticker: str, curr_date: str) -> str:
    """替代旧 `price_history_400d`(400 天日线全表)的紧凑价格块:近 60 交易日日线 +
    近 52 周周线聚合(D1.5,Q7 瘦身——旧块是 full 档里最大的单一块,决策密度却最低)。

    走 `load_ohlcv`(同一份 A股 tushare qfq / 美股 yfinance 缓存帧,PIT 已按 curr_date
    过滤),不经 `get_stock_data` 那个把 DataFrame 格式化成 CSV 文本的 `@tool`
    ——要做 pandas 周线聚合就必须拿到原始帧。`NoMarketDataError`(未知/退市代码、
    无行情)在此降级为一行说明,不阻断 harvest(B 级,同旧路径的 fail-open 语义)。
    """
    try:
        df = load_ohlcv(normalize_symbol(ticker), curr_date)
    except NoMarketDataError as e:
        return f"_OHLCV 取数失败(无市场数据,可交易性/技术面确定性源不可用):{e}_"
    if df is None or df.empty:
        return "_OHLCV 取数失败(空返回)。_"
    daily = df.tail(60)
    weekly = weekly_aggregate(df).tail(52)
    out = [
        f"### 近 60 交易日日线({daily['Date'].min().strftime('%Y-%m-%d')} → "
        f"{daily['Date'].max().strftime('%Y-%m-%d')})",
        "",
        _render_ohlcv_table(daily),
        "",
        "### 近 52 周周线聚合(W-FRI;O=周内首日开/H=周内最高/L=周内最低/"
        "C=周内末日收/V=周内成交量之和)",
        "",
        _render_ohlcv_table(weekly),
    ]
    return "\n".join(out)


#: `get_stock_stats_indicators_window`(dataflows/y_finance.py)渲染的分段头,
#: 逐字匹配它的输出格式:`## {indicator} values from {start} to {end}:`。
_IND_HEADER_RE = re.compile(r"^## (\S+) values from \S+ to \S+:$", re.MULTILINE)
#: 逐日一行:`{YYYY-MM-DD}: {value 或 "N/A: ..." 或其它错误文案}`。
_IND_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}): (.+)$", re.MULTILINE)


def _parse_indicator_segments(raw_text: str) -> dict[str, list[tuple[str, str]]]:
    """`get_indicators` 已渲染的多指标文本 → `{indicator: [(date, value_str), ...]}`
    (原文降序:每段第一行是 curr_date,依次往回;value_str 可能是数值也可能是
    "N/A: Not a trading day" 等非数值占位——由调用方过滤)。"""
    segments: dict[str, list[tuple[str, str]]] = {}
    headers = list(_IND_HEADER_RE.finditer(raw_text))
    for i, m in enumerate(headers):
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(raw_text)
        segments[m.group(1)] = _IND_LINE_RE.findall(raw_text[start:end])
    return segments


def _latest_and_prior(rows: list[tuple[str, str]], back: int = 5):
    """`rows` 降序(最新在前);跳过非数值(N/A/错误文案)行只看真读数,返回
    `(latest_val, latest_date, prior_val, prior_date)`——`prior` = 从 latest 往回数
    第 `back` 个有效读数(默认 5 = 5 个**交易日**前,不是 5 个自然日前,因为 N/A
    的非交易日行已被跳过)。一条有效读数都没有,或有效读数不足 `back+1` 条时,
    对应位置返回 `None`(**不是** 0 —— "没测到"与"测出来是零"是两件事)。
    """
    valid: list[tuple[str, float]] = []
    for d, v in rows:
        try:
            valid.append((d, float(v)))
        except ValueError:
            continue
    if not valid:
        return None, None, None, None
    latest_date, latest_val = valid[0]
    if len(valid) > back:
        prior_date, prior_val = valid[back]
    else:
        prior_date, prior_val = None, None
    return latest_val, latest_date, prior_val, prior_date


def indicator_summary_table(raw_text: str, indicators: list[str]) -> list[dict]:
    """替代旧 12 个 `## <ind> values` 30 天序列块的汇总表(D1.5,Q7 瘦身):每个指标
    一行 `{indicator, latest, latest_date, prior_5d, prior_date, direction}`。

    数字与旧块**同源**(同一次 `get_indicators.invoke(...)` 产出的 `raw_text`,这里
    只解析、不重算)——full 档瘦身只改摘要方式,不改数值来源。序列本身(30 天全量)
    应由调用方原样写进 deep 附件 `<TICKER>_<date>_indicators.md`(先在
    `contracts.artifacts.ARTIFACTS` 登记为 `analyze_indicators`)。

    `direction`:↑ 最新 > 5 日前、↓ 最新 < 5 日前、→ 持平;两个读数缺一个 → `—`
    (不猜方向)。输出行数恒等于 `len(indicators)`(某指标解析不出段落 → 该行全 `None`
    /`—`,不丢行——调用方按名对齐渲染)。
    """
    segments = _parse_indicator_segments(raw_text)
    rows = []
    for ind in indicators:
        latest, latest_date, prior, prior_date = _latest_and_prior(segments.get(ind, []))
        if latest is None or prior is None:
            direction = "—"
        elif latest > prior:
            direction = "↑"
        elif latest < prior:
            direction = "↓"
        else:
            direction = "→"
        rows.append({
            "indicator": ind, "latest": latest, "latest_date": latest_date,
            "prior_5d": prior, "prior_date": prior_date, "direction": direction,
        })
    return rows


def render_indicator_summary_table(rows: list[dict]) -> str:
    """`indicator_summary_table(...)` 的行 → markdown 表。缺读数一律 `—`,不写 0。"""
    lines = ["| 指标 | 末值 | 5 日前值 | 方向 |", "|---|---:|---:|---|"]
    for r in rows:
        latest = f"{r['latest']:.4f}" if r["latest"] is not None else "—"
        prior = f"{r['prior_5d']:.4f}" if r["prior_5d"] is not None else "—"
        lines.append(f"| {r['indicator']} | {latest} | {prior} | {r['direction']} |")
    return "\n".join(lines)

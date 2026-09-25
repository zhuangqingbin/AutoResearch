"""harvest A股块:akshare 老路 + tushare 优先包装 + UZI 增量透镜 + L1 复用渲染。

design: docs/superpowers/plans/2026-08-31-stock-research-p0-p1.md task-10(D1.1 拆分)。

**纯搬家**(task-10 红线):函数体逐字节照搬自拆分前的 `autoresearch/analyze/harvest.py`,
只有 import 行按新文件位置调整。`ticker_news_block`/`china_backdrop` 原属拆分前
harvest.py 的新闻/宏观节(两市场通用/A股专属但混在通用节里),为把 harvest.py 压到
<500 行归到此处——前者直接调用同文件的 `ashare_news_akshare`,后者是纯 A股块,与
D1.1 拆分范围无冲突。

`_l1_float`/`_l1_flag`/`_is_ashare`/`_hist_returns` **均不回引 harvest.py**(见
`blocks_statements.py` 模块 docstring 的 DAG 说明——harvest.py 严格在顶端,`python -m`
直跑时任何回引都会触发"模块执行两遍"的 ImportError):`_l1_float`/`_l1_flag` 回引
`slim_io.py`,`_is_ashare` 回引 `blocks_statements.py`,`_hist_returns` 回引
`blocks_us.py`(peer_relative/us_market_context 同源)。
"""
import time
from datetime import datetime, timedelta

from autoresearch.agents.utils.agent_utils import get_news
from autoresearch.analyze.blocks_statements import _is_ashare
from autoresearch.analyze.blocks_us import _hist_returns
from autoresearch.analyze.slim_io import _l1_flag, _l1_float
from autoresearch.contracts.agent_output import L1_REUSE_COLUMNS
from autoresearch.dataflows.symbol_utils import normalize_symbol


def ashare_news_akshare(sym: str, limit: int = 12, *, start_date: str | None = None,
                        end_date: str | None = None) -> str | None:
    """East-money individual-stock news via akshare (OPTIONAL dependency).
    Returns markdown bullets, or None if akshare is absent / returns nothing.

    D1.6 #1:`stock_news_em` 不接受日期参数——返回的是"最近若干条"而非"窗内条",
    回填历史日会把窗外(更旧/更新)的条目当成当天新闻。这里按
    `start_date<=日期<=end_date` client 侧过滤 + 按标题去重(同新闻多来源转载常见)+
    按时间倒序,`limit` 在过滤/去重/排序**之后**才截断,不然窗外的行可能先占满配额。
    """
    code = sym.split(".")[0]
    try:
        # D1.1:stock_news_em 已登记 policy key="as_of"(entity=symbol,按取数日快照)——
        # 直传 symbol 即可,天然与本函数原有"整表拉取、client 侧按窗口过滤"的用法吻合。
        from autoresearch.data import cache
        df = cache.get_or_fetch("stock_news_em", {"symbol": code}, today=end_date)
    except ImportError:
        return None
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
        # D1.1:stock_lhb_stock_statistic_em 已登记 policy key="as_of"。`symbol="近三月"`
        # 是一个**固定字面量**(选择"近三月"这个统计口径,不是逐票的实体参数)——所有票的调用
        # 都命中同一个 entity="近三月" 键,天然是"当天首个调用者取全表、其余免费命中"的共享
        # 快照,与本函数原有"整表拉、client 侧按代码过滤"的用法完全吻合。
        from autoresearch.data import cache
        stat = cache.get_or_fetch("stock_lhb_stock_statistic_em", {"symbol": "近三月"}, today=curr_date)
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


def ashare_shareholder_count(sym: str) -> str:
    """A-share 股东户数 (retail-dispersion / chip-concentration proxy) via akshare
    (OPTIONAL). Falling 户数 = chips concentrating (often constructive); rising =
    retail dispersing / possible distribution near highs."""
    code = sym.split(".")[0]
    try:
        # D1.1:stock_zh_a_gdhs_detail_em 已登记 policy key="as_of"(entity=symbol)——
        # 直传 symbol 即可,与本函数原有"整表拉取"用法吻合。ImportError(akshare 未安装)与
        # 取数失败仍分开渲染(原语义)。
        from autoresearch.data import cache
        df = cache.get_or_fetch("stock_zh_a_gdhs_detail_em", {"symbol": code}, today=None)
    except ImportError:
        return f"_akshare 未安装 → 股东户数不可用；推理时 WebSearch『{code} 股东户数 最新』兜底。_"
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
    overhang on/after curr_date) via akshare (OPTIONAL); 业绩预告/政策窗口
    left to WebSearch at reasoning time; 指数成分/调样事件 deterministic
    (index_membership)."""
    code = sym.split(".")[0]
    out = []
    try:
        # D1.1:stock_restricted_release_queue_em 已登记 policy key="as_of"(entity=symbol)——
        # ImportError(akshare 未安装)与其它取数失败原先分两个 try 块渲染不同文案,合并进
        # 同一个 try 后用 except 顺序(ImportError 在前)保留两条不同的降级文案。
        from autoresearch.data import cache
        rel = cache.get_or_fetch("stock_restricted_release_queue_em", {"symbol": code}, today=curr_date)
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
    except ImportError:
        return (f"_akshare 未安装 → 解禁队列不可用；WebSearch『{code} 限售解禁 时间表』兜底。_\n\n"
                "> 业绩预告（A股 1月底/4月底强制）、政策窗口 → 推理时 WebSearch 补，标注『实时网查』。")
    except Exception as e:
        out.append(f"_解禁队列取数失败: {e}（WebSearch『{code} 限售解禁 时间表』兜底）_")
    try:
        from autoresearch.analyze.index_membership import index_membership_lines
        out.append(index_membership_lines(code, curr_date))
    except Exception as e:  # noqa: BLE001 — 只读湖失败不挡日历块
        out.append(f"_指数成分/调样事件读湖失败: {e}_")
    out.append("> 业绩预告窗口（A股 1月底/4月底强制）、政策窗口（政治局会议/两会/降准降息）→ 推理时用 **WebSearch** 补，"
               "标注『实时网查』。指数调样已由上方确定性行供给，**不再网查**。")
    return "\n\n".join(out)


# --- scan-market L4:复用 L1 召回因子行,消除富因子(主力/技术/筹码/北向)重复取数 -----

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

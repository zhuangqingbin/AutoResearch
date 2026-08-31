"""harvest 财报/风险衍生块:可交易性(涨跌停/ADV) + 盈利质量(应计/SBC) + 偿付(净债务/商誉)。

design: docs/superpowers/plans/2026-08-31-stock-research-p0-p1.md task-10(D1.1 拆分)。

**纯搬家**(task-10 红线):函数体逐字节照搬自拆分前的 `autoresearch/analyze/harvest.py`,
只有 import 行按新文件位置调整。`_board_limit`/`tradeability_block` 原属拆分前
harvest.py 的"v4"节(两市场通用,不属于任何单一 block 文件),为把 harvest.py 压到
<500 行归到此处——按风险维度分组(涨跌停可达性与偿付/盈利质量同属"仓位风险"透镜),
与 D1.1 拆分范围无冲突。

`_is_ashare` **本模块自己定义**(不回引 harvest.py):`autoresearch.analyze` 5 个 block
文件构成一张纯粹的**有向无环图**,harvest.py 严格在顶端(只被 `python -m` 当
`__main__` 跑,从不被其它模块 import)——若某个 block 文件反过来在**模块顶层**
`from autoresearch.analyze.harvest import X`,`python -m autoresearch.analyze.harvest`
会把 harvest.py 的代码执行两遍(一次注册成 `__main__`,一次因为被下游 import 触发、
注册成真实模块名 `autoresearch.analyze.harvest`),第二次执行会在拆分前那批
`from .blocks_* import` 语句上撞见"部分初始化模块缺属性" ImportError(2026-08-31
§LIVE parity 真跑实测逮到,`import autoresearch.analyze.harvest` 方式的冒烟测试
测不出这条——只有真用 `-m` 跑才会踩)。故 `_is_ashare` 以及另外两个真正跨多个 block
文件共享的原语(`_REALTIME_DISCLAIMER`→`blocks_us.py`、`_l1_float`/`_l1_flag`→
`slim_io.py`)一律**下沉到某个不回引 harvest.py 的叶子模块**,harvest.py 只单向消费。
"""
from datetime import datetime, timedelta

import yfinance as yf

from autoresearch.dataflows.stockstats_utils import filter_financials_by_date
from autoresearch.dataflows.symbol_utils import normalize_symbol


def _is_ashare(ticker: str) -> bool:
    """True for mainland-China A-shares (Shanghai/Shenzhen/Beijing)."""
    return normalize_symbol(ticker).endswith((".SS", ".SZ", ".BJ"))


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


def earnings_quality_metrics(symbol: str, curr_date: str) -> str:
    """Accruals / cash-conversion / SBC dilution derived from quarterly statements.

    `curr_date`(D1.4 #7)PIT:直取 `yf.Ticker(...).quarterly_*` 属性绕开了
    `get_income_statement`/`get_balance_sheet`/`get_cashflow` 已经在用的
    `filter_financials_by_date`(同一裁定,同一函数)——历史回填会把**尚未发生**的报告期
    当"最新一季"读进 NI/CFO/FCF 等。复用同一把过滤器补齐,不重造一套口径。
    """
    t = yf.Ticker(normalize_symbol(symbol))

    def _stmt(attr):
        try:
            df = getattr(t, attr, None)
            return filter_financials_by_date(df, curr_date) if df is not None else None
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


def solvency_block(symbol: str, curr_date: str) -> str:
    """Balance-sheet solvency / refinancing lens — leverage, liquidity runway,
    interest coverage, goodwill: the mechanism behind most blow-ups. A-share
    share-pledge (股权质押) is left to WebSearch (per-stock akshare is unreliable).

    `curr_date`(D1.4 #8)PIT:同 `earnings_quality_metrics` 的病(#7)—— 直取
    `yf.Ticker(...).quarterly_*` 绕开了 `filter_financials_by_date`,回填历史日会把
    未来报告期的负债/权益读成"最新"。
    """
    t = yf.Ticker(normalize_symbol(symbol))

    def _stmt(attr):
        try:
            df = getattr(t, attr, None)
            return filter_financials_by_date(df, curr_date) if df is not None else None
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

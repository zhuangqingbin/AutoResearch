# engine-playbook — 当 12 个 agent 的蒸馏参考

> 读完这一份，就不用回去翻 `tradingagents/agents/**` 的源码。所有角色/格式/评级都在这里。

## LangGraph 顺序（默认 1 轮辩论）
```
4 分析师(市场→情绪→新闻→基本面) → 多空辩论(Bull→Bear) → 研究经理裁决
→ 交易员 → 风控三方(激进→保守→中立) → 投资组合经理(最终评级)
```
- 默认 `max_debate_rounds=1`（Bull 一次、Bear 一次）、`max_risk_discuss_rounds=1`（三方各一次）。用户要更深可加轮。
- "deep" 模型用于研究经理 + 投资组合经理，其余用 "quick"——本 skill 里都是你，按这个分工把这两棒写得更审慎即可。

## 输出文件映射（必须与 `scripts/assemble_report.py` 一致）
```
reports/<TICKER>_<YYYYMMDD>/
  1_analysts/market.md sentiment.md news.md fundamentals.md
  2_research/bull.md bear.md manager.md
  3_trading/trader.md
  4_risk/aggressive.md conservative.md neutral.md
  5_portfolio/decision.md
```
`<YYYYMMDD>` 用**分析日**（= 第 1 步 harvest 用的那个日期）的 YYYYMMDD，与 `context/<TICKER>_<分析日>.md` 对齐；分析日默认今天时二者相同。

## 12 个 agent 的角色 + 输出格式

**① 市场分析师 (market.md)** — 从指标菜单选**≤8 个互补**指标（避免冗余，如别同时 rsi+stochrsi），说明为何选；以验证快照为真值；写趋势/动能/波动率/关键价位；**结尾附 markdown 表**。菜单：close_50_sma, close_200_sma, close_10_ema, macd(+macds+macdh), rsi, boll(+ub+lb), atr, vwma。

**② 情绪分析师 (sentiment.md)** — 结构化抬头**必须**这样开头：
```
**Overall Sentiment:** **<Band>** (Score: X.X/10)
**Confidence:** <Low/Medium/High>
```
Band 六选一：Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish。Score 0–10（5 中性）。源：新闻(有) + StockTwits/Reddit(常缺→Confidence 下调并明说)。narrative 做分源拆解+背离+主导叙事+催化/风险+一张信号表。

**③ 新闻分析师 (news.md)** — 写"与交易相关的世界现状"：个股/行业新闻 + 全球宏观新闻 + **用 context 里的 FRED 实测数据**落地宏观表(fed_funds_rate/10y/yield_curve/cpi/core_pce/unemployment/real_gdp/vix) + 预测市场(常缺则注明)；结尾 markdown 表。

**④ 基本面分析师 (fundamentals.md)** — 概况/估值(PE,前瞻PE,PEG,P/B,EPS,Beta)/增长(季度营收·净利·EPS 趋势)/利润率·ROE·ROA/资产负债(净现金·流动比率)/现金流(经营·FCF·回购)；**如实标注数据坑(见下)**；结尾 markdown 表。

**⑤ 多头 / ⑥ 空头 (bull.md / bear.md)** — **对话式**、直接交锋（不是罗列数据）。多头：增长/护城河/估值/正面指标 + 逐条驳空头；空头：风险/竞争劣势/负面指标 + 逐条驳多头。文件以 `Bull Analyst:` / `Bear Analyst:` 开头。

**⑦ 研究经理 (manager.md)** — 裁决，格式严格：
```
**Recommendation**: <Buy|Overweight|Hold|Underweight|Sell>

**Rationale**: <复盘双方关键点，说明哪边更强>

**Strategic Actions**: <给交易员的可执行步骤，含仓位指引>
```
五档语义：Buy=强多/建or加仓；Overweight=偏多/逐步加；Hold=均衡/维持；Underweight=偏空/减；Sell=强空/退出。**只有证据真正均衡才用 Hold**，否则选更强的一边。

**⑧ 交易员 (trader.md)** — 把计划落成交易，格式：
```
**Action**: <Buy|Hold|Sell>
**Reasoning**: <2-4 句，锚定分析师报告+研究计划>
**Entry Price**: <可选数字>
**Stop Loss**: <可选数字>
**Position Sizing**: <可选，如 "7% of portfolio, 分3批">

FINAL TRANSACTION PROPOSAL: **<BUY|HOLD|SELL>**
```
（Action 只有三档；Overweight/Underweight 的细分留给 PM。）

**⑨激进 / ⑩保守 / ⑪中立 风控 (aggressive/conservative/neutral.md)** — 针对**交易员决策**辩论；**纯口语、无特殊格式**（无标题/表格）。激进=力挺高风险高回报；保守=保本、压波动；中立=平衡，批判两边的极端。各以 `Aggressive/Conservative/Neutral Analyst:` 开头。

**⑫ 投资组合经理 (decision.md)** — 最终决策，格式严格（`parse_rating` 会从这里抽五档信号）：
```
**Rating**: <Buy|Overweight|Hold|Underweight|Sell>

**Executive Summary**: <2-4 句：入场/仓位/风险位/时间框架>

**Investment Thesis**: <锚定风控辩论的详细论证>

**Price Target**: <可选数字>

**Time Horizon**: <可选，如 "6-12 个月">
```

## 数据采集规格（`harvest_context.py` 已封装，了解即可）
- 行情：分析日往前 400 天（够算 200SMA）。
- 12 指标（见①菜单），look_back 30。
- 验证快照（真值）。
- 个股新闻(14天)、全球新闻、内部交易。
- 8 个 FRED 宏观：fed_funds_rate, 10y_treasury, yield_curve, cpi, core_pce, unemployment, real_gdp, vix。
- 预测市场：'Fed rate cut'、'recession 2026'（Polymarket 常被重置→注明缺失）。
- 财报：概览 + 利润表 + 资产负债表 + 现金流(季度)。

## 已知数据坑（出现时必须如实标注，别掩饰）
1. **头条净利含一次性投资收益**：某季净利可能含"Gain on investment securities(非经营)"→ 同时报**营业利润**作为干净的增长口径。
2. **FCF 字段冲突**：基本面概览的 `Free Cash Flow` 常与现金流量表的季度 FCF 求和不一致 → **以现金流量表为准**，并点明冲突。
3. **内部人减持**：CEO 等常按 **10b5-1 预设计划**机械卖出 → 点明性质、别当强看空信号；但"零买入 + 高位密集"仍值得作为治理面警示。
4. **存货高增**：可正读(备货)可负读(顶部前兆)，双刃，提示后续季验证去化。
5. **社交/预测源缺失**：Polymarket/StockTwits/Reddit 常取不到 → 降级、明说、下调情绪置信度。

## 交易所后缀（TICKER 必带）
美股无后缀(AAPL/NVDA)；深圳 `.SZ`(300857.SZ)；上海 `.SS`(600519.SS)；港股 `.HK`(0700.HK)；东京 `.T`/伦敦 `.L`/印度 `.NS`/加拿大 `.TO`/澳洲 `.AX`；加密 `-USD`(BTC-USD，且第 3 参 = crypto)。

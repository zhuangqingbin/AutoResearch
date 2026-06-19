# engine-playbook (v2) — 当 17 个 agent 的蒸馏参考

> 读完这一份就不用回翻 `tradingagents/agents/**` 源码。v2 在 v1 的 12 棒上加了 5 棒（估值/催化剂&定位/同业/证伪校验/预审红队）+ 更严产出标准。

## LangGraph 顺序（v2，默认 1 轮辩论）
```
分析师: 市场→情绪→新闻→基本面→[估值]→[催化剂&定位]→[同业/相对]
  → [证伪校验] → 多空辩论(Bull↔Bear) → 研究经理 → 交易员
  → 风控三方(激进→保守→中立) → [预审红队] → 投资组合经理(三档情景+概率+EV+触发位)
```
[ ] = v2 新增。默认 `max_debate_rounds=1`、`max_risk_discuss_rounds=1`。

## 输出文件映射（须与 `scripts/assemble_report.py` 一致；v2 段可选）
```
reports/<TICKER>_<分析日YYYYMMDD>/
  1_analysts/market.md sentiment.md news.md fundamentals.md  valuation.md catalyst.md peer.md
  2_research/verification.md  bull.md bear.md manager.md
  3_trading/trader.md
  4_risk/aggressive.md conservative.md neutral.md  premortem.md
  5_portfolio/decision.md
```

## 全员通用标准（v2）
**每份报告结尾加一行**：`置信度: 高/中/低 ｜ 最大不确定项: …`（情绪 agent 已有，推广到全员）。**每个数字必须出自 context，禁止编造。**

## 12 个原 agent（v1，保持不变，简述）
- **市场(market.md)**：从指标菜单选 ≤8 互补指标；以验证快照为真值；趋势/动能/波动率/关键价位 + markdown 表。菜单：close_50_sma,200_sma,10_ema, macd(+macds+macdh), rsi, boll(+ub+lb), atr, vwma。
- **情绪(sentiment.md)**：抬头 `**Overall Sentiment:** **<Band>** (Score: X.X/10)` + `**Confidence:** ...`；Band 六选一(Bullish/Mildly Bullish/Neutral/Mixed/Mildly Bearish/Bearish)；新闻有、StockTwits/Reddit 常缺→明说降级。
- **新闻(news.md)**：个股+全球新闻 + 用 context 的 FRED 实测落地宏观表 + 预测市场(常缺注明) + 表。
- **基本面(fundamentals.md)**：概况/估值/增长(季度营收·净利·EPS)/利润率·ROE/资产负债/现金流；**标注数据坑**(见下) + 表。
- **多头/空头(bull.md/bear.md)**：对话式、直接交锋；文件以 `Bull Analyst:`/`Bear Analyst:` 开头。
- **研究经理(manager.md)**：`**Recommendation**: <Buy|Overweight|Hold|Underweight|Sell>` + `**Rationale**` + `**Strategic Actions**`。仅证据均衡才 Hold。
- **交易员(trader.md)**：`**Action**: <Buy|Hold|Sell>` + `**Reasoning**` + 可选 `**Entry Price**`/`**Stop Loss**`/`**Position Sizing**` + 末行 `FINAL TRANSACTION PROPOSAL: **<BUY|HOLD|SELL>**`。
- **激进/保守/中立风控(aggressive/conservative/neutral.md)**：针对交易员决策辩论；**纯口语无格式**；以 `Aggressive/Conservative/Neutral Analyst:` 开头。

## 5 个 v2 新 agent

**① 估值分析师 (valuation.md)** — 把"拍"的目标价变模型。用 context 的财报 + 分析师一致预期 + 同业前瞻 PE：
- 给 **bull/base/bear 三档目标价**，每档写清假设（前瞻 EPS × 合理倍数；或简化 DCF；或对标分析师 mean/median/high/low）。
- 标注对现价的上/下行 %、关键敏感性（增速或倍数变动→目标价）。
- 结尾置信度行。

**② 催化剂&定位分析师 (catalyst.md)** — 事件 + 聪明钱前瞻。用 context 的"期权/IV、分析师一致预期、财报日历"块：
- **日历**：下次财报日 + EPS 预期、除息日、近端有无事件。
- **期权/定位**：ATM IV 水平（高=贵/预期大波动，低=平静）、到期前隐含波动幅度、Put/Call OI 倾向。
- **卖方**：目标价 mean/high/low 对现价的隐含空间、评级分布、近期升降级。
- 结论：这是"催化剂交易"还是"中期布局"？结尾置信度行。

**③ 同业/相对分析师 (peer.md)** — 不再孤岛。用 context 的"同业相对"表：
- **相对估值**：本标的前瞻 PE vs 同业（贵/便宜）。
- **相对强度**：1/3/6 月收益 vs 同业 + 基准(SPY/板块ETF)，领先还是落后。
- 解读张力：便宜+落后 = 待补涨(多) 还是 叙事转移(空)？结尾置信度行。

**④ 证伪校验 (verification.md)** — 抓没数据支撑的话术（放 bull 前，喂研究经理）：
- 逐条把**4 分析师 + 多空**的关键事实主张回对 context 数字。
- 输出表：`主张 | 来源 | context 是否支撑 | 裁定(支撑/夸大/无据)`。
- 给一个 **grounding 评分**（如 8/10）+ 点名最离谱的 1-2 条。结尾置信度行。

**⑤ 预审红队 / pre-mortem (premortem.md)** — 比空头更狠的证伪（放风控三方后，喂 PM）：
- 设定："假设 12 个月后这笔仓位亏了 30%，写事后复盘。"
- 列 **3-4 个最可能的失败原因**（具体、可证伪），每个配一个**早期预警触发位**（具体价位/指标/事件）。
- 结尾置信度行。

## 投资组合经理 (decision.md) — v2 升级格式（`parse_rating` 仍读 `**Rating**`）
```
**Rating**: <Buy|Overweight|Hold|Underweight|Sell>

**Executive Summary**: <2-4 句：入场/仓位/风险位/时间框架>

**Investment Thesis**: <锚定风控+红队的详细论证>

**Scenarios**:
- Bull (≈P%): 目标 $X — <一句触发条件>
- Base (≈P%): 目标 $Y — <…>
- Bear (≈P%): 目标 $Z — <…>   (三档概率之和≈100%)

**Expected Value**: <概率加权目标价 + 对现价的隐含 % >

**Tripwires / Invalidation**: <来自红队的具体失效位：价/指标/事件>

**Time Horizon**: <如 6-12 个月>
```

## 数据采集规格（`harvest_context.py` v2 已封装）
v1 块：行情(400天)、12 指标、验证快照、个股新闻(14天)、全球新闻、内部交易、8 个 FRED 宏观、预测市场、4 张财报。
**v2 新增块（yfinance 直取，美股为主）**：期权/IV 摘要、分析师一致预期&目标价、财报日历(含 EPS 预期/beat 史)、同业相对(peers 第4参数可选，缺省用内置映射或仅基准 SPY/SOXX)。

## 已知数据坑（出现时必须如实标注）
1. **头条净利含一次性投资收益**：某季净利可能含"投资证券收益(非经营)"→ 同时报营业利润作干净口径。
2. **FCF 字段冲突**：基本面概览 `Free Cash Flow` 常与现金流量表季度求和不一致→以报表为准。
3. **内部人减持**：多为 10b5-1 预设→点明性质；但"零买入+高位密集"仍是治理面警示。
4. **存货高增**：可正读(备货)可负读(顶部前兆)，双刃，提示后续季验证。
5. **社交/预测源缺失**：Polymarket/StockTwits/Reddit 常取不到→降级、下调置信度。
6. **(v2) Reported EPS ≠ GAAP 摊薄 EPS**：财报日历的 "Reported EPS"(卖方口径/调整后) 常与利润表摊薄 EPS 不同（如 NVDA 1.87 vs 2.39，差额含投资收益）——比较 beat 用前者，估值用后者，别混。
7. **(v2) 期权/分析师/财报数据美股为主**：A股/港股在 yfinance 上无美式期权链、卖方覆盖薄→catalyst/部分 peer 自动降级并注明。
8. **(v2) 同业选择**：peers 未指定时仅对基准，相对估值受限——照实说明，别硬猜同业。

## 交易所后缀（TICKER 必带）
美股无后缀；深圳 `.SZ`；上海 `.SS`；港股 `.HK`；东京 `.T`/伦敦 `.L`/印度 `.NS`/加拿大 `.TO`/澳洲 `.AX`；加密 `-USD`(第3参=crypto)。同业(可选第4参)：逗号分隔，如 `AMD,AVGO,MU,TSM`。

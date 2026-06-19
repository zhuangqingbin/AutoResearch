# engine-playbook (v4) — agent 蒸馏参考 + 报告骨架

> 读完这一份就不用回翻 `tradingagents/agents/**` 源码。
> 演进：v3 重平衡(17→15,合并冗余辩论+加证据 lens)；v3.1 并出 **定位&资金流**(15→14);v3.4 市场棒加**市场环境**。
> **v4(本版)**：报告从「组织架构序」重排为「**决策论证序**」,拆成 **决策主线 / 证据附录** 两层;新增 **预期差 / 催化剂日历 / 持仓监控 / 偿付&再融资** 四维度 + **决策仪表盘 / 维度评分卡 / 多空对撞表** 三速览件;A股加 **股东户数(散户数量)**。

---

## 报告结构（v4 核心：决策主线 + 证据附录）

`assemble_report.py` 把各段拼成 `complete_report.md`,顺序 = **目录 → 决策主线 → 证据附录**。
**决策主线 = 读它就能下单(目标 ~2 页);证据附录 = 读它来核实(按需下钻)。** 14+ 个 agent 照常产出全部明细,只是**分析师重活沉到附录**。

```
▸ 决策主线 (Decision Spine)
  S1  执行摘要 · PM 决策    ← 4_portfolio/decision.md   (顶部含 决策仪表盘 + 维度评分卡)
  S2  投资逻辑 & 预期差     ← 2_research/variant.md      【新】
  S3  多空对撞 (一张表)     ← 2_research/faceoff.md      【新】(散文版 bull/bear 入附录)
  S4  催化剂日历 & 触发位   ← 4_portfolio/calendar.md    【新】
  S5  风险 · 认错 · 持仓监控 ← 3_risk/premortem.md(+监控KPI表) + 3_risk/debate.md

▸ 证据附录 (Evidence Appendix)
  A  分析师证据  ← market / news / fundamentals / [quality] / [valuation]
                  / [positioning] / [peer] / [solvency 新]
  B  研究与验证  ← [reality_check] / bull / bear / manager
```

## LangGraph 顺序（默认 1 轮辩论；先产明细,再做综合,最后 PM 封装）
```
分析师: 市场 → News&Narrative → 基本面 → [盈利质量] → [估值] → [定位&资金流(含股东户数)] → [同业] → [偿付&再融资]
  → [Reality Check: 证伪 + 基率]
  → 多空辩论(Bull↔Bear) → 研究经理
  → [Risk Debate 三合一] → [预审红队(含监控KPI)]
  → 投资组合经理(仪表盘 + 评分卡 + 预期差 + 多空对撞 + 催化日历 + 三档情景/EV/触发位/执行)
```
`[ ]` = optional lens（缺了 assemble 自动跳过）。

## 输出文件映射（须与 `scripts/assemble_report.py` 一致）
```
reports/<分析日YYYYMMDD>/<TICKER>/        # 按日期分组,无此日期目录则新建
  1_analysts/  market.md  news.md  fundamentals.md  quality.md  valuation.md
               positioning.md  peer.md  solvency.md(新)
  2_research/  reality_check.md  variant.md(新)  bull.md  bear.md  faceoff.md(新)  manager.md
  3_risk/      debate.md  premortem.md
  4_portfolio/ decision.md  calendar.md(新)
```
**必需**（缺则 assemble 报 `[MISSING]`）：`decision / variant / faceoff / calendar / premortem / market / news / fundamentals / bull / bear / manager`。
**optional lens**：`quality / valuation / positioning / peer / solvency / reality_check / debate`（尽量写齐）。

## 全员通用标准
**每份结尾一行**：`置信度: 高/中/低 ｜ 最大不确定项: …`。**每个数字必须出自 context**（WebSearch 实时网查的数字除外,但须显式标注来源/日期）。**附录每个分析师段首行加一句「→ 对决策的影响(so-what)」**,再展开。

---

## 决策主线新段（v4 — 这些是"拿来下单"的核心,务必精要）

**① 决策仪表盘（写在 `decision.md` 最顶,一张表）** — 5 秒看懂这笔交易：

| 评级 | 现价 | EV目标(+%) | 上行/下行 | R:R | 时间框架 | 建议仓位 | 止损 | 置信度 |
|---|---|---|---|---|---|---|---|---|
| Overweight | 369 | 417 (+13%) | +44% / −23% | ~1.9:1(至极值) | 6–12月 | 上限½ | 335→294 | 中 |
> R:R = (上行空间)/(下行空间);**至 base 的 R:R 也算一遍**(常 <1,别只报极值那个好看的)。

**② 维度评分卡（紧随仪表盘,6 行表）** — 一眼看出论点强在哪、弱在哪：

| 维度 | 评分 | 一句话依据 |
|---|---|---|
| 基本面 | 强 | 营收+28%/净利+40%/ROE34% |
| 估值 | 弱 | 24x 前瞻 PE 建立在 EPS 翻3倍假设 |
| 技术/市场 | 中 | 多头排列但距高点8%、广度窄 |
| 资金/筹码 | 中 | 主力温和净流入、股东户数↓集中、未上龙虎榜 |
| 盈利质量 | 强 | CFO/NI 1.64 现金背书 |
| 催化 | 中 | 下季报兑现度是关键闸门 |
> 评分用 强/中/弱 三档;依据必须落 context 数字。

**③ 预期差 `variant.md`（≤180 字,主线 S2,alpha 本体）**：
- **市场在 price-in 什么**（共识:卖方目标/forward EPS 隐含的增长、估值倍数代表的预期）。
- **我们哪里不同**（更乐观/更悲观,具体在哪个变量:增速兑现度? 倍数? 资金面?）。
- **差异靠什么/何时收敛**（下季报 EPS、某催化、某数据点）→ 这就是"赚的是什么钱"。
- 没有差异化观点 = 没有 edge → 该如实说"跟随共识,无 alpha,仅 beta/趋势"。

**④ 多空对撞 `faceoff.md`（一张表,≤8 行,主线 S3）** — 把 bull/bear 压成逐条交锋：

| 争点 | 多方 | 空方 | 谁占上风(据 context) |
|---|---|---|---|
| EPS 兑现 | 产能周期支撑 | 4 连 miss 履历 | 空(未兑现 vs 已兑现) |
> 散文版 bull.md/bear.md 仍写(入附录 B),faceoff 只做浓缩裁决。

**⑤ 催化剂日历 `calendar.md`（表,主线 S4）** — 按日期排,每条标 多/空 + 是否=加减仓触发：

| 日期/窗口 | 事件 | 方向 | 是否触发加/减仓 |
|---|---|---|---|
| 下季报 | EPS 兑现度 | 多/空 | 加仓权解锁闸门 |
| A股 1月底/4月底 | 业绩预告(强制) | ? | 监控 |
| <解禁日> | 限售解禁(供给) | 空 | 减仓预警 |
> 数据来源:context 的「财报日历」「Corporate calendar—A股 解禁」+ 推理时 WebSearch 补政策窗口/调样(标注『实时网查』)。

**⑥ 持仓监控（写进 `premortem.md` 末尾,一张 KPI 表,主线 S5）** — 买入后每周/季盯什么：

| KPI | 当前 | 健康阈值 | 破位含义 |
|---|---|---|---|
| 下季 EPS 兑现 | — | 不再两位数 miss | 减仓/重估 |
| 毛利率 | 33%+ | ≥33% | 跌破=降级 |
| CFO | 正 | 单季为正 | 转负=基本面降级 |
| 相对强度 | 领先 | 不破板块 | 背离=独木难支 |
> 红队四死因 → 每个配一个可监控 KPI/触发位,把"离场价"升级成"持仓跟踪表"。

---

## 核心棒（保持原样,简述）
- **市场(market.md)**：**先市场环境 → 再个股技术 → 判共振/背离**。
  - **① 市场环境**(context 的 "Market context" 块)：A股看 大盘 regime + **主力资金净流入** + **龙虎榜**(游资/机构) + **涨停家数·连板·热门行业**;美股看 SPY regime(vs 50/200DMA) + 广度(RSP vs SPY) + 板块ETF + VIX。
    - **主力资金流要成『逐日表』(近5日:日期/收盘/涨跌%/主力净流入/净占比),别只报一个累计数**——day-by-day 序列才是信号:**涨停日大额流入 + 随后连续净流出 = 拉高出货(顶部派发嫌疑)**,持续净流入=吸筹。context 已成表→照搬 + 读出模式 + 与股东户数/龙虎榜交叉印证。
  - **② 个股技术**：菜单选 ≤8 个互补指标(close_50_sma,200_sma,10_ema, macd+macds+macdh, rsi, boll+ub+lb, atr, vwma);以验证快照为真值。
  - **③ 共振判断**：个股 setup 是被 大盘/板块/资金 **共振**(强强可信)还是 **背离**(独木难支/出货嫌疑)。+ 表 + 置信度行。
- **基本面(fundamentals.md)**：概况/估值/增长/利润率·ROE/资产负债/现金流;标注数据坑 + 表。
- **估值(valuation.md)**：bull/base/bear 三档目标价,每档写清假设(前瞻 EPS×倍数 / 简化 DCF / 对标卖方) + 上/下行 % + 敏感性。
- **同业/相对(peer.md)**：相对估值(前瞻 PE vs 同业) + 相对强度(1/3/6 月)。解读"便宜+落后=待补涨 or 叙事转移"。
- **多头/空头(bull.md/bear.md)**：对话式直接交锋,文件以 `Bull Analyst:` / `Bear Analyst:` 开头,**必须有真实张力**;浓缩版进 faceoff。
- **研究经理(manager.md)**：`**Recommendation**: <Buy|Overweight|Hold|Underweight|Sell>` + `**Rationale**` + `**Strategic Actions**`。仅证据均衡才 Hold。
- **预审红队(premortem.md)**：设"12 个月后亏 30%,写复盘";列 **3-4 个最可能失败原因**(可证伪) + 每个配**早期预警触发位**;**末尾接 ⑥ 持仓监控 KPI 表**。喂 PM。

## 合并棒（4）
**① News & Narrative (news.md)** ← News + Sentiment：个股新闻 + 全球/宏观(FRED 实测) + 社交情绪三合一;抬头 `**Overall Sentiment:** **<Band>**`(Bullish/Mildly Bullish/Neutral/Mixed/Mildly Bearish/Bearish)。社交常缺→明说降级。预测市场取数失败→WebSearch 取 FedWatch/衰退概率,标注『实时网查』。**A股个股新闻**走 akshare 东财/WebSearch 兜底。
**② Reality Check (reality_check.md)** ← 证伪 + 基率：A.证伪表 `主张|来源|context是否支撑|裁定` + grounding 评分(如 9/10);B.外部视角 base rate + 点名 inside-view 偏差。放多空辩论**之前**。
**③ Risk Debate (debate.md)** ← 激进/保守/中立三段短立场(各 2-4 句,以 `Aggressive:`/`Conservative:`/`Neutral:` 开头) + 一段综合。喂 PM。
**④ 交易执行 → 并入 PM**(不单列 trader.md)：入场阶梯/止损/仓位上限 + `FINAL TRANSACTION PROPOSAL` 行。

## lens 棒（3）
**⑤ 盈利质量 (quality.md)** — context 的 "Earnings quality" 派生块：应计=NI−CFO、CFO/NI、FCF/NI、SBC/营收、股本同比;GAAP vs 调整后缺口(头条净利是否含一次性投资收益)。结论:利润干净还是被美化?
**⑥ 定位 & 资金流 (positioning.md)** ← 催化&定位 + 持仓/做空：事件/催化(下次财报+EPS);期权/IV(美股);卖方目标价分布;做空占比/回补天数(美股);float/机构/内部人%。**A股加股东户数(散户数量)**:context 的 "股东户数" 块——**户数↓=筹码集中(偏多)、↑=分散(高位警惕派发)**,结合户均持股市值 + 龙虎榜 + **主力资金流逐日表(读涨停日大额流入→随后连续净流出=拉高出货)**读筹码结构。结论:聪明钱怎么站位?
**⑦ 偿付 & 再融资 (solvency.md)【新】** — context 的 "Solvency & refinancing" 块：净债务/D-E、流动比率、利息覆盖(EBIT/利息)、商誉占权益。这是**空头的资产负债表机制**。(A股)股权质押率走 WebSearch 兜底,高质押=控制权/平仓风险。结论:有无偿付/再融资/稀释/减值的爆雷风险?

---

## 投资组合经理 (decision.md) — v4 格式（`parse_rating` 仍读 `**Rating**`）
**顶部先放 ① 决策仪表盘表 + ② 维度评分卡表**（见上),随后：
```
**Rating**: <Buy|Overweight|Hold|Underweight|Sell>

**Executive Summary**: <2-4 句:入场/仓位/风险位/时间框架>

**Investment Thesis**: <锚定 Reality Check + 预期差 + Risk Debate + 红队>

**Scenarios**:
- Bull (≈P%): 目标 $X — <触发条件>
- Base (≈P%): 目标 $Y — <…>
- Bear (≈P%): 目标 $Z — <…>   (三档概率之和≈100%)

**Expected Value**: <概率加权目标 + 对现价隐含 %> ｜ **R:R**: <上行/下行;至 base 也算>

**Tripwires / Invalidation**: <红队失效位:价/指标/事件>

**Execution**: <入场阶梯 + 止损 + 仓位上限;**消化可交易性**(A股涨跌停/停牌→名义止损可能跳空穿越,需缓冲) + **组合相关性**(同类暴露需降配)>

**Time Horizon**: <如 6-12 个月>

FINAL TRANSACTION PROPOSAL: **<BUY|HOLD|SELL>**
```

## 数据采集规格（`harvest_context.py` v4 已封装）
核心块：行情(400天)、12 指标、验证快照、个股新闻(14天)、全球新闻、内部交易、8 FRED 宏观、预测市场(失败→WebSearch)、4 张财报。
**v2 块**(yfinance,美股为主)：期权/IV、分析师一致预期&目标价、财报日历、同业相对。
**v3 块**：持仓/做空、盈利质量。**v3.3(A股本地化)**：个股新闻三层、同业基准换沪深300/创业板ETF、China backdrop、CFO 补 A股现金流行名。**v3.4(市场环境)**：A股主力资金流/龙虎榜/涨停池(akshare);美股 SPY regime/RSP广度/板块ETF/VIX。
**v4 块**：① **可交易性**(`tradeability_block`:ADV 20/60日 + 按板块涨跌停规则 + 近60日触板次数 + 止损可达性,A股/美股都有);② **偿付&再融资**(`solvency_block`:净债务/D-E/流动比率/利息覆盖/商誉);③ **股东户数**(`ashare_shareholder_count`,A股,akshare);④ **A股解禁日历**(`ashare_corporate_calendar`,akshare)。akshare 缺/限流→优雅降级 + WebSearch 兜底。
> 数据→棒：可交易性→执行段;偿付→偿付棒;股东户数→定位&资金流;解禁→催化日历。

## 已知数据坑（出现时必须如实标注）
1. **头条净利含一次性投资收益**→ 同时报营业利润(盈利质量棒系统化)。
2. **FCF 字段冲突**(概览 vs 现金流量表)→ 以报表为准。
3. **内部人减持**多为 10b5-1 预设→点明性质;但零买入+高位密集仍是治理警示。
4. **存货高增**双刃(备货 vs 顶部前兆)→提示后续季验证。
5. **社交/预测源缺失**(Polymarket 美区 RST 封锁、StockTwits/Reddit)→预测市场失败用 WebSearch 取前瞻赔率,标注『实时网查』;社交缺失则降级。
6. **Reported EPS ≠ GAAP 摊薄 EPS**(卖方/调整后口径)→比 beat 用前者、估值用后者,勿混。
7. **期权/分析师/财报/做空 美股为主**→A股/港股自动降级注明。
8. **同业选择**:peers 未指定仅对基准→照实说明,别硬猜。
9. **做空数据滞后**(双月结算)→当方向性背景,别当实时。
10. **(A股) yfinance 个股新闻近乎零**→akshare 东财/WebSearch 兜底;社交(雪球)也缺。
11. **(A股) yfinance insider 金额不可信**(单位 bug)→只采信方向,绝不引金额/价位;A股无做空数据。
12. **(A股) 同业基准已自动换沪深300/创业板ETF**;宏观另给 China backdrop,美国 FRED 仅全球背景。
13. **(市场环境) Market 棒先大盘/板块/资金、再个股技术、判共振/背离**;A股资金/龙虎榜/涨停走 akshare。**东财个股资金流偶发限流/连接中断 = A股决策关键缺口**(主力**逐日**流向是『拉高出货』的核心证据)——context 显示『主力资金流取数失败』时务必 WebSearch『<代码> 主力资金流向 近5日 / 涨停日主力净流入』把**逐日颗粒度**补回 + 显式标注降级,**别塌缩成一个累计新闻数(如『某日净流入 X 亿』)就算交差**;**近三月未上龙虎榜=无游资异动**(也是信息)。
14. **(v4 可交易性/涨跌停) 名义止损 ≠ 可执行止损**：A股涨跌停为硬封板,连续跌停**可能卖不出**、硬止损价被跳空穿越;创业板/科创板 ±20%、北交所 ±30%、主板 ±10%(ST ±5% 未自动识别);叠加随时停牌→**执行段须为止损预留缓冲**,别承诺一个打不出的价。美股无涨跌停(仅熔断)。
15. **(v4 股东户数, A股) 户数↓=筹码集中(偏多)、↑=分散(高位警惕派发)**;akshare 列名跨版本会变,已做防御性取列,缺则 WebSearch『代码 股东户数』兜底。
16. **(v4 偿付, A股) 股权质押率**个股口径在 yfinance/akshare 不稳→WebSearch 兜底;商誉占权益高=减值冲击大;利息覆盖 <3 / 流动比率 <1 = 偿付脆弱信号。

## 交易所后缀（TICKER 必带）
美股无后缀；**A股可省略后缀**(裸 6 位:6/9→`.SS`,0/2/3→`.SZ`,4/8→`.BJ`;也可显式 `600519.SS`);港股 `.HK`;东京 `.T`/伦敦 `.L`/印度 `.NS`/加拿大 `.TO`/澳洲 `.AX`;加密 `-USD`(第3参=crypto)。同业(第4参,可选):逗号分隔,如 `AMD,AVGO,MU,TSM`。

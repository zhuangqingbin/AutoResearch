# analyze-ticker v3 — 设计稿（重平衡：砍辩论冗余 + 加证据 lens）

- 日期：2026-06-19
- 状态：待用户复核
- 关联：`.claude/skills/analyze-ticker/`、`scripts/harvest_context.py`、`scripts/assemble_report.py`
- 前序：[v2 设计稿](./2026-06-19-analyze-ticker-v2-design.md)（17 棒）

## 目标
v2 有 17 棒，但近一半是"站一边说话"的散文 agent（Bull/Bear/激进/保守/中立/红队），token 重、信号重叠；同时缺便宜的**硬数据 lens**。v3 不增不减地**重平衡**：砍掉冗余辩论散文、加回数据密的证据 lens，让 **signal/token 同时改善**。结果 **17 → 15 棒**，输出侧净减、context 仅微增两个小数据块。

> 诚实的 token 账：大头在"我写的 agent 输出"，不在 harvested context。v3 砍 4 份冗余散文（sentiment、风控 2 份、trader）、加 2 份数据密文件（ownership、quality）+ 1 份折叠（base-rate 并进 Reality Check）→ 输出侧净减；context 只新增 ownership / earnings-quality 两个小块（~25 行）。

## 一、砍 / 并（−token，砍的全是散文重的）

| # | 改动 | 文件层面 | 理由 |
|---|---|---|---|
| 1 | **Sentiment + News → 「News & Narrative」** | 删 `1_analysts/sentiment.md`；`news.md` 吸收情绪口径 | 同一坨 context（个股+全球新闻+社交），原框架分开是因各有独立工具，我们没有 |
| 2 | **激进/保守/中立 → 「Risk Debate」** | 删 `aggressive/conservative/neutral.md`；新 `4_risk/debate.md` | 三段镜像独白→一个 agent 出三段短立场 + 综合。保留辩证、杀三倍散文 |
| 3 | **Trader → 并入 PM** | 删整个 `3_trading/`；PM 加 **Execution** 段 | v2 PM 已做 sizing/情景/tripwire；入场-止损阶梯并进去。`FINAL TRANSACTION PROPOSAL` 行移到 PM |
| 4 | **Verification → 「Reality Check」** | `verification.md` → `reality_check.md`（吸收 base-rate） | 证伪 + 外部视角都是 debias 元检查，天然合一 |

## 二、加（+signal，几乎全是免费数据）

**① 持仓/做空（`1_analysts/ownership.md`，新数据块）**
- 数据（yfinance `.info`）：`sharesShort`、`shortPercentOfFloat`、`shortRatio`(days-to-cover)、`sharesShortPriorMonth`(趋势)、`floatShares`、`heldPercentInstitutions/Insiders`；`.institutional_holders`、`.major_holders`。
- 读法：crowded short（逼空/空头确信度）、低 float（波动放大）、机构增减持。与已取的**内部交易**交叉，给"谁在怎么站位"的合并图。
- 结尾置信度行。

**② 盈利质量/取证（`1_analysts/quality.md`，派生指标块）**
- 指标（由已取的季度利润表/现金流/资产负债表派生）：**应计 = NI − CFO**、现金转化 `CFO/NI`、`FCF/NI`、**SBC 稀释** `SBC/营收`、股本同比（回购 vs 摊薄）、GAAP vs 调整后缺口、存货/应收 vs 营收增速。
- 读法：利润是干净还是被一次性项/低现金转化"美化"。把 v2 在 NVDA 上**手动**抓的 $15.9B 投资收益缺口**系统化**成固定一棒。
- 结尾置信度行。

**③ 基率/外部视角（折叠进 Reality Check，无新文件）**
- 对当前 setup（这种估值/增速/这波涨幅之后）给历史同类的 base rate；点名 bull/bear 可能落入的 inside-view 偏差。几乎不加 token。

## 三、v3 阵容（15 棒）
- **I. 分析（8）**：Market · **News&Narrative**⊕ · Fundamentals · **Earnings-Quality**▲ · Valuation · Catalyst&Positioning · **Ownership/Short-Interest**▲ · Peer-Relative
- **II. 研究（4）**：**Reality Check**（证伪+基率⊕▲）· Bull · Bear · Research Manager
- **III. 风险（2）**：**Risk Debate**（三辩合一⊕）· Pre-Mortem
- **IV. 组合（1）**：Portfolio Manager（并入 Trader 执行段）

`⊕`=合并 `▲`=新 lens。Section 由 v2 的 I–V 收为 **I–IV**（Trading 团队消失）。

## 四、harvester 改动（`harvest_context.py`）
新增 2 个 helper（各自 try/except 降级，沿用现有模式）：
- `ownership_short(symbol)` — `.info` 做空/float/持股比 + `.institutional_holders` + `.major_holders`。在 "Insider transactions" 后追加一段。
- `earnings_quality_metrics(symbol)` — 由 `.quarterly_income_stmt` / `.quarterly_cashflow` / `.balance_sheet` 派生上面②的比率表。在 "Cash flow (quarterly)" 后追加一段。
- 非美标的常缺 → 同 v2 降级注明。

## 五、assemble 改动（`assemble_report.py`）
重写 `SECTIONS` 为 v3 四段（renumber I–IV）。required 仅留核心：`market.md`、`news.md`、`fundamentals.md`、`bull.md`、`bear.md`、`manager.md`、`decision.md`；其余（quality/valuation/catalyst/ownership/peer/reality_check/debate/premortem）为 optional，缺则跳过 + `[note]`。`parse_rating(decision)` 不变。
- 清断（clean break）：旧 v1/v2 报告目录是 gitignore 的一次性产物，不强求向后组装；新报告按 v3 文件名走。

## 六、playbook / SKILL 改动
- `engine-playbook.md`：角色表 17→15、合并 4 棒的写法、新增 2 lens 规格、文件映射、流水线顺序、PM 加 **Execution** 段（含 `FINAL TRANSACTION PROPOSAL: **<BUY|HOLD|SELL>**` 行）。
- `SKILL.md`：步骤里的"17 个 agent"→"15 个 agent"、文件清单同步。

## 七、验证
- **冒烟**（轻量、不重跑全盘推理）：① harvest 某标的 → 新 ownership / earnings-quality 两块出现且非空（美股）；② assemble 一个含 v3 文件名的目录 → 四段成形 + `parse_rating` 正常 + optional 缺失走 `[note]`。
- **可选实跑**：用户触发时在某标的上跑完整 15 棒（token 较重，不在本轮自动做）。

## 八、YAGNI / 不做
- **组合/因子契合** lens 本轮不做：需用户喂现有持仓才 full value，否则只降级成 beta/风格（已有）。留待用户提供 book 时再加。
- 不引入新付费数据源；所有新 lens 走 yfinance 免费字段或已取报表派生。

---

## v3.1 增补（同日迭代，用户确认）

1. **进一步合并**：把 **催化剂&定位 (catalyst) + 持仓/做空 (ownership)** 合并成一个 **定位&资金流 (Positioning & Flow, `positioning.md`)** 棒——两者本质同为"聪明钱怎么站位 + 前瞻设置"。**15 → 14 棒**。harvester 数据块不变（期权/卖方/财报日历/做空 全喂这一棒）。
2. **报告结构**：`assemble_report.py` 重排为 **目录(TOC) + PM 执行摘要置顶 → I–III 明细**；TOC 用显式 `<a id>` 锚点（不依赖渲染器 slug 算法），PM 不在结尾重复。
3. **Polymarket 失败**：复现确认是**环境网络层在 TLS 握手阶段 RST**（Polymarket 美区/SNI 封锁），非代码问题、非超时。改为 harvester 检测到**全部话题失败**时在 context 写入 **WebSearch 兜底指令**（FedWatch 降息/衰退概率 + 催化，标注『实时网查』、不计入确定性 context）；实际 WebSearch 在 Claude 推理层执行（确定性脚本无 LLM/工具）。
4. **暂不加**新维度（Macro-Regime / 供应链跨读 / 估值敏感性网格）——留待下轮。

验证：ruff clean、py_compile OK、Polymarket 兜底指令实测触发、14-棒 assemble + `parse_rating` 正常、TOC/PM 置顶顺序正确。

---

## v3.2 增补（同日迭代，用户确认）

1. **报告按日期分组**：报告目录约定从 `reports/<TICKER>_<YYYYMMDD>/` 改为 **`reports/<YYYYMMDD>/<TICKER>/`**（无日期目录则自动新建；Write 写嵌套文件时自动建）。`assemble_report.py` 的 ticker 推断对新路径天然兼容（`root.name` = TICKER）。
2. **A股可只传裸代码**：在 `tradingagents.dataflows.symbol_utils.normalize_symbol` 解析链里加规则——bare 6 位纯数字按首位补交易所后缀（6/9→`.SS`，0/2/3→`.SZ`，4/8→`.BJ`），其余形态不动；附单测。这是全项目共享 resolver，CLI/批量路同样受益。

验证：`pytest tests/test_symbol_utils.py tests/test_cli_symbol_handling.py` → 39 passed；`600519`→`600519.SS` 实拉 yfinance 得真实收盘（茅台 ¥1215）；assemble 新路径产出正常。

---

## v3.3 增补（A股数据本地化，用户确认）

实跑 `300476.SZ`（胜宏科技）后发现：A股**定量/财务脊柱其实满血**（含卖方一致预期），缺口集中在 **news/社交/同业基准/宏观**（US-centric）。评价：14 棒推理诚实、无幻觉（reality_check 独立抓出 yfinance insider 金额单位 bug + 前瞻PE/4连miss对撞），问题在数据层。修复（均在 `harvest_context.py`）：

1. **个股新闻三层**（`ticker_news_block`）：yfinance(常空) → akshare 东财(可选依赖) → **WebSearch 兜底指令**（公司中文名，推理层填，同 Polymarket 范式）。akshare 设为**可选导入**，不动用户在改的 `uv.lock`；`uv add akshare` 后启用确定性源。
2. **同业基准按市场切换**（`_benchmarks`）：A股→沪深300(`000300.SS`) + 创业板ETF(`159915.SZ`，因指数 `399006.SZ` 无 yfinance 历史)；美股保持 SPY/SOXX。
3. **China backdrop**（`china_backdrop`）：A股加人民币 + 中港股指(沪深300/上证/创业板ETF/恒生)动量。
4. **盈利质量 CFO 补行名**：加 A股现金流 `Cash Flowsfromusedin Operating Activities Direct` → 300476 现正确算出 CFO/NI 1.64、负应计。
5. **数据坑固化**（playbook + SKILL）：A股 insider 金额不可信(只看方向)、无做空数据、新闻走 akshare/WebSearch。

验证：ruff clean；300476 实测——CFO/应计算出、peer 对沪深300/创业板ETF（**6m +29.4% vs 创业板 +34.6%，实为跑输**，SPY 对比时完全看不见）、China backdrop 全列、news 三层兜底触发；akshare 未装时自动 WebSearch 兜底。

---

## v3.4 增补（市场环境：Market 棒不再孤岛，用户确认）

Market 棒从"只看个股技术面"升级为 **市场环境 → 个股技术 → 共振/背离** 三层。新增 harvester "Market context" 块（按市场切换，紧跟验证快照后）：

- **A股（akshare；`uv pip install akshare` 装进 venv，不动 uv.lock/pyproject）**：
  - 主力资金流 `stock_individual_fund_flow`（近10日主力净流入趋势）
  - 龙虎榜 `stock_lhb_stock_statistic_em("近三月")`（是否上榜 / 机构参与 / 游资；**未上榜本身=无单日异动信息**）
  - 涨停池 `stock_zt_pool_em`（涨停家数 / 连板高度 / 热门行业 = 情绪温度，自动回溯最近交易日）
  - flaky 端点经 `_ak_call` 重试 + 退避；失败优雅降级 + WebSearch 兜底。
- **美股（yfinance）**：SPY regime(vs 50/200DMA)、广度代理(RSP 等权 vs SPY 市值权)、板块ETF轮动、VIX。

playbook Market 棒规范改三层 + 共振判断；数据坑 #13 固化。

验证（300476 + NVDA 实测）：US 全绿（SPY 多头排列 / RSP 落后=窄幅领涨脆弱 / SOXX +116% 在风口 / VIX 16.9）；A股 龙虎榜(未上榜=无游资) + 涨停情绪(91家/4连板/汽车零部最热) 正常；主力资金流函数+数据经探针证实正确，唯反复测试触发东财 IP 限流→优雅降级（单次 harvest 不触发）。akshare 仅装入 venv，未改依赖文件。

---

## v4 增补（决策主线重构 + 4 维度 + A股股东户数；用户全选 + 大改，已批准）

把"研报为投资决策服务"做到底：正文从**组织架构序**重排为**决策论证序**，并拆**决策主线 / 证据附录**两层；补四个决策维度 + 三个速览件 + A股散户数量。

### 报告结构（`assemble_report.py` 重写）
- **决策主线**（读它就能下单，~2 页）：S1 PM决策(顶部含**决策仪表盘+维度评分卡**) → S2 **预期差** → S3 **多空对撞表** → S4 **催化剂日历** → S5 风险·认错·**持仓监控**。
- **证据附录**（按需核实）：A 分析师证据(market/news/fundamentals/quality/valuation/positioning/peer/**solvency**) + B 研究验证(reality_check/bull/bear/manager)。
- 两层 TOC + 分隔横幅；`decision.md` 仍首位且 `parse_rating`。新必需文件：`variant.md`/`faceoff.md`/`calendar.md`；`solvency.md` optional。每个附录分析师段首行加「→ 对决策的影响」。

### 四维度 + 速览件（playbook 出模板）
- **预期差 variant.md**（≤180字）：市场 price-in 什么 → 我们哪不同 → 何时/靠什么收敛（alpha 本体）。
- **催化剂日历 calendar.md**（表）：按日期，每条标 多/空 + 是否=加减仓触发。
- **持仓监控**（写进 premortem.md 末尾，KPI 表）：红队四死因 → 可监控 KPI+阈值。
- **偿付&再融资 solvency.md**：净债务/D-E/流动比率/利息覆盖/商誉占权益（空头的资产负债表机制）。
- **决策仪表盘+维度评分卡**（写进 decision.md 顶部）：5 秒速览 + **R:R**（至 base 也算，常 <1，别只报极值）。
- **多空对撞表 faceoff.md**：bull/bear 压成"争点｜多方｜空方｜谁占上风"，散文版入附录。

### harvester v4 数据块（多数从已抓数据派生，低风险）
- **可交易性 `tradeability_block`**（A股+美股）：ADV 20/60日 + 按板块涨跌停规则(创300/301·科688 ±20%，北 ±30%，主板 ±10%) + 近60日触板次数 + **止损可达性**（A股硬封板/跳空穿越/停牌→名义止损≠可执行，执行段须缓冲）。
- **偿付 `solvency_block`**（A股+美股）：净债务/D-E/流动比率/利息覆盖/商誉，**读数标签改条件式**（不再硬编码"<1吃紧/<3脆弱"误导健康值）。
- **股东户数 `ashare_shareholder_count`**（A股，akshare）：`stock_zh_a_gdhs_detail_em`，**tail 取最近期、newest-first**，户数↓集中/↑分散；防御性取列名。
- **A股解禁 `ashare_corporate_calendar`**（akshare）：`stock_restricted_release_queue_em`，**按 curr_date 过滤未来解禁**（占流通市值%），无则报"近端无解禁压力"。
- akshare 全程 `_ak_call` 重试+退避 + try/except 优雅降级 + WebSearch 兜底；仍 venv-only，未动 uv.lock/pyproject。

### 验证（实测）
- 代码 ruff clean + py_compile。assembler MISSING-check 对旧 v3 报告正确报缺 variant/faceoff/calendar。
- 300476：股东户数（**2026-06 连续 +12%/+11.9%/+7.7%/+2.95% = 散户高位涌入/派发警示**，v3 完全缺此信号）、解禁（未来无→近端无供给压力）、偿付（净债 52亿/利息覆盖33.9x/商誉占权益7%）、可交易性（创业板±20%、ADV~179亿）。
- NVDA：可交易性（美股无涨跌停、ADV~$37B）、偿付（净现金 −8.9亿、流动比率 3.44 稳健、利息覆盖 686x 充裕、商誉占权益11%）——条件式标签正确。
- 数据坑 #14–16 固化（涨跌停止损现实性 / 股东户数 / 偿付质押）。

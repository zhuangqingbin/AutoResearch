# macro-research — 全球宏观 + 中美宏观 + A股中观 → 跨资产/行业配置

> 设计文档 · 2026-06-20 · 状态:待评审
> 关联:复用 `analyze-ticker` 的三段式骨架(`harvest_*.py` / `assemble_*.py` / playbook)与 `parse_rating`;与 `scan-market` 组成望远镜(macro → 中观 → 选股 → 深挖)。

## 1. 动机与核心原理

**需求**:出一份**自上而下的宏观研究**——全球 + 中美双核——并落到**可执行的配置**:每个资产类(利率/美股/A股·港股/USD·CNY·JPY/黄金/大宗/加密/信用)一个超-中-低配倾向;再往下钻一层 **A股中观**(行业轮动 / 板块资金流入流出 / 游资龙虎榜 / 涨停情绪周期 / 题材风格),把"该超配 A股"落成"该超配哪些行业/题材"。

**核心原理(同项目 philosophy)**:`确定性数据(免费)` + `LLM 多 agent 推理(本来要钱)`。本 skill 保留前者(FRED/akshare/yfinance 取真宏观+中观数据),把后者**换成 Claude(本 session)**——零付费 LLM API,用真实数据 + 真实 prompt 产出完整的宏观研究 + 配置决策。

**与 analyze-ticker 的关系**:三段式骨架(取数 → Claude 按 playbook 推理 → 组装+`parse_rating` 校验)已被 analyze-ticker / scan-market 验证可复用。本 skill **复用骨架,playbook 全新**:宏观没有单票 bull/bear 对撞,取而代之的是 regime 四象限、政策反应函数、跨资产传导、中美对撞、A股中观轮动。

**望远镜定位**:
```
macro-research(宏观 regime → 跨资产配置 → A股中观:行业/资金/情绪/题材)
   → scan-market(在目标行业内全市场选股)
   → analyze-ticker(深挖 finalist 单票)
```
中观 tier 接力 scan-market 的 L2 板块聚合;未来可让 scan-market L2 反过来读本 skill 的中观输出(见 §12)。

## 2. 架构:新 skill,复用三段式骨架

`macro-research` 是独立 skill,**编排** 取数 → 推理 → 组装。与 analyze-ticker 同构、playbook 独立。

```
.claude/skills/macro-research/
  SKILL.md            触发词 + 6 步流程 + 何时用/不用 + 铁律
  macro-playbook.md   报告骨架 + 各 agent 角色/顺序/输出格式 + 数据坑(读它,不回翻代码)
scripts/
  harvest_macro.py    零 LLM 取数 → context/macro/<date>/*.md(+ 缓存)
  assemble_macro.py   克隆 assemble_report.py;对两张配置表逐行跑 parse_rating 校验
产物:
  context/macro/<date>/     区域宏观 + 跨资产价 + 中观板块/资金/龙虎榜/涨停(缓存)
  reports/macro/<date>/      各 agent 分段 .md
  reports/macro/<date>/macro_compass.md   组装后的完整报告
```
(`context/`、`reports/` 已 gitignore。)

**接口解耦**:harvest 产 `context/macro/<date>/`(纯数据);Claude 按 playbook 读 context 产分段;assemble 拼装 + 校验。三者通过文件解耦,与 analyze-ticker 完全一致。

## 3. 数据层 `harvest_macro.py`

### 3.1 Vendor 映射

| 块 | 数据 | Vendor | 备注 |
|---|---|---|---|
| 美国宏观 | 政策利率(FEDFUNDS)/2-10-30Y(DGS2/10/30)/曲线(T10Y2Y)/CPI·core(CPIAUCSL/CPILFESL)/PCE·core(PCEPI/PCEPILFE)/通胀预期(T10YIE)/就业(PAYEMS·ICSA·UNRATE)/GDP/工业(INDPRO)/M2/金融条件(NFCI)/实际利率(DFII10) | **FRED** | 现成别名 + 补 NFCI/DFII10(raw ID 透传即可) |
| 中国宏观 | CPI/PPI/PMI(官方+财新)/社融·M2/LPR/外储/进出口/GDP/工增/社零/地产投资 | **akshare** `macro_china_*` | 防御取列(版本漂)→失败 WebSearch |
| 全球外层 | 欧元区政策利率/日本利率·CPI·BOJ/EM 关键利率 | **FRED 国际 series**(raw ID 透传)+ WebSearch | 日本因 JPY·套息单列重点;series ID 建库时逐个确认 |
| 跨资产价格 | DXY(DX-Y.NYB)/CNY(CNY=X)/JPY(JPY=X)/金(GC=F)/油(CL=F)/铜(HG=F)/UST(^TNX)/VIX(^VIX)/SPY/CSI300(000300.SS)/HSI(^HSI)/BTC(BTC-USD)/ETH/信用(HYG·LQD) | **yfinance** | 与 analyze-ticker 同 vendor;取价+近 400 日趋势 |
| 前瞻/赔率/定位/地缘 | FedWatch 降息赔率·衰退概率/CFTC 持仓/关税·选举·政治局·央行讲话 | **WebSearch** | 标『实时网查』,不当确定性数据 |

> **关于 FRED「US-only」**:`fred.py` 的 `_resolve_series_id` 对未知输入按 **raw FRED ID 透传**,`get_macro_data` 对任意合法 series 都工作——所以国际 series(中国 CPI `CHNCPIALLMINMEI`、日本、欧元区等)**今天就能取**,只是别名表没有、错误文案写着 "US-only"。本 skill **不改 `fred.py` 行为**,仅可选地补几个友好别名,不影响现有 US 调用方。

### 3.2 中观取数(A股,akshare bulk;端点名以建库时确认)

| 维度 | 端点(示意) | 备注 |
|---|---|---|
| 行业行情/资金流 | `stock_sector_fund_flow_rank` · `stock_board_industry_name_em` · `stock_fund_flow_industry` | 申万一级行业 主力净流入(今/5/10日)、涨跌幅 |
| 概念/题材资金流 | `stock_board_concept_name_em` + 资金流 | 热门题材排名 |
| 龙虎榜/游资 | `stock_lhb_detail_em` · 机构 `stock_lhb_jgmmtj_em` · 活跃营业部 `stock_lhb_hyyyb_em` | 游资席位/机构席位净买/异动 |
| 涨停池/情绪 | `stock_zt_pool_em` · 昨日涨停今日表现 `stock_zt_pool_previous_em` · 强势 `stock_zt_pool_strong_em` · 跌停 `stock_zt_pool_dtgc_em` | 连板梯队/封板率/炸板率/打板赚钱效应 |
| 两融 | `stock_margin_sse` · `stock_margin_szse`(+行业口径) | 融资余额变化=杠杆情绪 |
| 北向/外资 | `stock_hsgt_fund_flow_summary_em` · `stock_hsgt_hist_em` | **坑:个股实时北向 2024-08 已停,仅汇总/板块/季度口径,标 staleness** |
| ETF 申赎/份额 | `fund_etf_*` | 被动盘/国家队边际定价 |
| 行业估值分位 | `stock_industry_pe_ratio_cninfo` 或自算 | PE/PB 历史分位(挡"便宜因为烂") |
| 风格指数 | 沪深300 / 中证1000(000852) / 中证2000(932000) / 微盘股 / 红利(000015) | 大小盘·价值成长·红利轮动 |

**采集铁律**:as-of 分析日、不取未来数据;只用 bulk 端点,不对全市场逐个拉历史;akshare 失败/限流 → 优雅降级 + WebSearch 兜底 + 显式标注,**绝不静默塌缩**(如把"主力逐日流向"塌成"某日净流入 X 亿")。

## 4. 报告骨架:决策主线 + 中观落地 + 证据附录

`assemble_macro.py` 拼装顺序 = **目录 → 决策主线 → 中观落地 → 证据附录**。决策主线 + 中观落地 = "拿来配置"(目标 ~3 页);证据附录 = "拿来核实"。

### 4.1 LangGraph 风格顺序(先明细 → 综合 → 配置封装)
```
区域宏观: 美国 → 中国 → 全球外层(欧/日/EM)
  → 跨资产&传导: 利率&央行反应函数 → 外汇 → 权益 → 大宗&黄金 → 加密 → [信用&流动性]
  → 中美四专题: 货币分化 → 增长通胀错位 → 贸易关税地缘 → 相对资产&资本流
  → A股中观: 行业配置图 → 资金&游资 → 情绪周期&涨停 → 题材&风格 → [景气桥]
  → 综合: 预期差 → 中美对撞&情景矩阵 → 催化日历 → 红队&监控 → Risk Debate
  → 配置封装(两张 5 档表:跨资产 + A股行业)
```

### 4.2 文件映射(须与 `assemble_macro.py` 一致)
```
reports/macro/<date>/
  1_spine/      decision.md  variant.md  crossfire.md  calendar.md  premortem.md  debate.md(opt)
  2_meso/       sector_map.md  flows.md  sentiment.md  themes.md
  3_regional/   us.md  china.md  global.md
  4_crossasset/ rates.md  fx.md  equities.md  commodities.md  crypto.md  credit.md(opt)
  5_sinous/     divergence.md  desync.md  geopolitics.md  relative.md
  6_meso_evidence/  industry_cycle.md(opt)
```
**必需**(缺则 assemble 报 `[MISSING]`):`decision variant crossfire calendar premortem` · `sector_map flows sentiment themes` · `us china global` · `rates fx equities commodities crypto` · `divergence desync geopolitics relative`。
**optional lens**(缺则跳过):`debate credit industry_cycle`。

### 4.3 各段内容

**▸ 决策主线**

| 段 | 文件 | 内容 |
|---|---|---|
| **S1 执行摘要·配置决策** | `decision.md` | ① **宏观仪表盘**(一行表:regime 象限 / 美政策档 / 中政策档 / 全球流动性 / 风险偏好档 risk-on·neutral·risk-off / 关键假设 / 置信度)② **跨资产配置表**(美债·美股·A/H·USD·CNY·JPY·黄金·大宗·加密·信用,每行:5 档倾向 + 关键驱动 + 主要表达 + 触发/失效位)+ 整体风险档(keyed `OVERALL`,见 §6)+ 执行摘要 2–4 句 |
| **S2 论点 & 预期差** | `variant.md` | 市场在 price-in 什么(FedWatch 隐含降息次数 / 曲线隐含 / 估值隐含增长 / CNY forward / 商品 backwardation)vs 我们哪里不同 vs 靠什么、何时收敛(下次 CPI/NFP/FOMC/中国 PMI·社融/NPC)。**宏观 alpha 本体**;无差异化 = 跟随 beta,如实说 |
| **S3 中美对撞 & 情景矩阵** | `crossfire.md` | ① **中美对撞表**(四行 = 四 lens:货币分化/增长错位/贸易地缘/相对资产,列 美向·中向·净含义→对 CNY/相对股/大宗)② **情景矩阵**:增长×通胀四象限为骨架 → base / 再通胀 / 滞胀 / 硬着陆 + 各概率(和≈100%),每情景下各资产方向;**可叠 1–2 个自定义中美情景行**(如"中国强刺激 vs 失速")→ 配置 = 概率加权 |
| **S4 催化日历** | `calendar.md` | 按日期:FOMC·CPI·PCE·NFP·JOLTS / 中国 PMI(官+财新)·社融·CPI-PPI·LPR·NPC·政治局·中央经济工作会议 / BOJ·ECB / 关税·选举·地缘。每条标方向 + 是否=调仓触发 |
| **S5 风险·认错·监控** | `premortem.md`(+KPI 表)`debate.md`(opt) | 红队"6–12 月后这套配置错得最惨的 3–4 个原因"(政策误判/通胀再加速/地缘黑天鹅/流动性事件/中国超预期刺激或失速),每个配早期预警触发位;末尾 **配置监控 KPI 表**(KPI/当前/健康阈值/破位含义);Risk Debate 激进-保守-中立三段 |

**▸ 中观落地(A股内部:给定 regime + A股超配,落到行业/资金/情绪/题材)**

| 段 | 文件 | 内容 |
|---|---|---|
| **M1 行业配置图** | `sector_map.md` | 申万一级行业排名表:相对强度(1/5/20日)+ 主力净流入(今/5/10日)+ 北向行业配置变化 + 估值历史分位 + 景气方向 → **每行业一个 5 档倾向**(第二张 `parse_rating` 表)。= A股版的"跨资产配置表" |
| **M2 资金 & 游资** | `flows.md` | ① **板块资金流逐日**(主力净流入 top/bottom 行业,day-by-day 读 吸筹 vs 拉高出货)② **龙虎榜/游资**(知名游资席位·机构专用席位净买·活跃营业部·近期异动板块)③ 两融余额变化(杠杆情绪)+ ETF 申赎(被动盘/国家队) |
| **M3 情绪周期 & 涨停结构** | `sentiment.md` | 涨停/跌停家数·连板梯队/最高连板·封板率/炸板率·昨日涨停今日表现(打板赚钱效应)·首板晋级率 → 判断情绪周期在 **启动/发酵/高潮/退潮** 哪一档(定短期 risk 偏好) |
| **M4 题材 & 风格轮动** | `themes.md` | 概念板块资金/涨幅 top(热门题材生命周期:AI/机器人/低空/央企改革…)+ 大小盘·价值成长·红利 风格轮动 + 行业拥挤度/换手 |

**▸ 证据附录(按需下钻)**

- **A 区域宏观**:`us.md`(增长/通胀/就业/金融条件/政策路径,FRED 实测)· `china.md`(增长/通胀/信用/政策/地产,akshare)· `global.md`(欧/日/EM;**日本重点**:BOJ/JPY/套息)
- **B 跨资产 & 传导**:`rates.md`(美债曲线·实际利率·Fed/PBoC/BOJ 反应函数;信用并入或单列 `credit.md`)· `fx.md`(DXY·CNY 利差/干预·JPY 套息)· `equities.md`(美股 vs A/H 相对·估值-盈利-风险偏好)· `commodities.md`(油铜=增长·金=实际利率+避险)· `crypto.md`(BTC=流动性/风险偏好/美元替代 beta;与实际利率、纳指、DXY 相关性)
- **C 中美专题(四 lens 详证,对应 S3 四行)**:`divergence.md`(利差→汇率→资本流传导链)· `desync.md`(美再通胀 vs 中通缩)· `geopolitics.md`(WebSearch 事件树→资产映射)· `relative.md`(A/H vs US·北向·全球配置轮动)
- **D 中观明细**:`industry_cycle.md`(景气桥:macro regime → 受益/受损产业链,半导体/地产链/出口链/猪周期…,部分 WebSearch)

> **B(资产视角:金在做什么)与 C(论点视角:Fed-PBoC 分化怎么驱动 CNY)是两种切法,不重复**——同 analyze-ticker 的 faceoff 表 vs bull/bear 全文。

## 5. 中观规格(M1–M4 细则)

### 5.1 M1 行业配置图(核心,产出第二张校验表)
- 申万一级行业逐行打分:相对强度(短/中/长)+ 主力资金(逐日方向)+ 北向变化(标 staleness)+ 估值分位(行业内/历史)+ 景气方向(来自 D 景气桥)。
- 每行业落 **5 档倾向**(强超配/超配/中性/低配/强低配);依据必须落 context 数字。
- 与 S1 跨资产表呼应:S1 说"A股 超配",M1 说"A股内部超配 X/Y/Z 行业、低配 A/B"。

### 5.2 M2 资金 & 游资(用户点名)
- **板块资金流逐日**:近 5 日 日期/板块净流入/净占比 → 读 day-by-day 模式(持续净流入=吸筹;涨后连续净流出=拉高出货),与龙虎榜/情绪交叉印证。**akshare 取数失败时 WebSearch 补逐日颗粒度,绝不塌成单个累计数。**
- **龙虎榜/游资**:知名游资席位活跃度、机构专用席位净买卖、活跃营业部、上榜板块分布;近三月某板块**未上龙虎榜=无游资异动**(也是信息)。
- **两融 + ETF**:融资余额方向(杠杆情绪 proxy)、宽基/行业 ETF 申赎(被动/国家队边际)。

### 5.3 M3 情绪周期 & 涨停结构
- 涨停/跌停家数、连板梯队(连板数分布)、最高连板高度、封板率、炸板率、昨日涨停今日表现(赚钱效应)、首板晋级率。
- 综合判断 A股情绪周期档位(启动→发酵→高潮→退潮),作为**短期 risk 偏好闸门**(高潮退潮=减少题材暴露)。

### 5.4 M4 题材 & 风格轮动
- 概念板块资金/涨幅 top + 题材生命周期(萌芽/发酵/分歧/退潮)。
- 风格:大盘 vs 小盘(沪深300 vs 中证2000/微盘)、价值 vs 成长、红利 vs 题材;A股风格漂移剧烈,是中观重要一维。
- 行业拥挤度/换手(挡"上车即套")。

## 6. 输出 & 校验(双 `parse_rating` 表)

- "决策" = **两张 5 档表**:S1 跨资产(`decision.md`)+ M1 A股行业(`sector_map.md`)。
- 复用项目 `parse_rating`(找 `**Rating**: <Buy|Overweight|Hold|Underweight|Sell>` 标签):
  - `assemble_macro.py` 对每张表的**每一行**校验——按固定 key 列表(资产 keys / 申万一级行业 keys)逐个 grep 其 rating 行、确认 ∈ 5 档,打印"N 资产 + M 行业"的配置信号。
  - 渲染约定:表后为每行补一行机器可读 `- <key>: **Rating**: <band> — <一句依据>`,供逐行解析。
  - **避免 `parse_rating`「首个标签胜出」碰撞**:**所有** rating 行都带 key 前缀(含整体风险档,用保留 key `OVERALL`,如 `- OVERALL 风险档: **Rating**: Hold`)。assemble 不对整段裸跑 `parse_rating`,而是先按 key 切行、再对每行单独跑——每行有且仅有一个标签,无歧义。
- 5 档语义:Buy=强超配 / Overweight=超配 / Hold=中性 / Underweight=低配 / Sell=强低配。
- `assemble_macro.py` 基本是 `assemble_report.py` 的克隆:把 `SPINE/APPENDIX` 换成 `SPINE / MESO / APPENDIX` 三段列表 + 必需/可选机制 + TOC/锚点 + 双表逐行校验。

## 7. 全员通用标准 & 铁律(防幻觉)

- **每份分段结尾一行**:`置信度: 高/中/低 ｜ 最大不确定项: …`。
- **每个数字必须出自 context**(FRED/akshare/yfinance 实测);WebSearch 实时数据须显式标来源/日期。
- **宏观判断性内容**(情景概率、政策路径、央行反应函数)**必须显式标"判断"或"实时网查"**,不冒充确定性数据。
- 分析窗口**钉死分析日**,绝不用未来数据。
- 中美对撞 / Risk Debate 必须有**真实张力**,不许橡皮图章一边倒。
- **跨资产相关性随 regime 漂移**(通胀期股债相关性翻正)→ 配置表须声明当前相关性假设。
- 收尾写明:**这是 Claude 的推理产出、非自动引擎;仅供研究,非投资建议。**

## 8. 新增 / 复用组件

**新增**
- `.claude/skills/macro-research/SKILL.md` — 触发词("研究全球宏观/中美宏观/现在该超配什么/A股哪些行业值得配")、6 步流程、何时用/不用、铁律。
- `.claude/skills/macro-research/macro-playbook.md` — 报告骨架(§4)+ 中观规格(§5)+ 各 agent 角色/顺序/输出格式 + 数据坑;让 Claude 读它而非回翻代码。
- `scripts/harvest_macro.py` — 区域宏观 + 跨资产价 + 中观板块/资金/龙虎榜/涨停;零 LLM,产出 `context/macro/<date>/`。
- `scripts/assemble_macro.py` — 组装 + 双表逐行 `parse_rating` 校验。

**复用(不改)**
- `tradingagents/dataflows/fred.py`(国际 series 走 raw ID 透传)、`y_finance.py`、`tradingagents/agents/utils/rating.py`(`parse_rating`)。
- 模式参考:`scripts/harvest_context.py`(akshare 防御取列 `_ak_call` 风格)、`scripts/assemble_report.py`(SPINE/APPENDIX 结构)。

**可选小改**
- `fred.py` 补几个国际友好别名(纯增量,不影响 US 调用方)。

## 9. 锁定的决策(来自 brainstorm)

| 维度 | 决策 |
|---|---|
| 形态 | 新 skill,Approach A(镜像三段式骨架;regime 四象限/情景矩阵作为章节,不持久化) |
| 产出 | 跨资产配置倾向 + A股行业配置倾向(两张 5 档表,均 `parse_rating` 校验) |
| 资产宇宙 | 利率 / 权益(美·中) / 外汇 USD·CNY·**JPY** / 黄金 / 大宗 / **加密** / 信用 |
| 地理 | 中美双核 + 全球外层(欧/日/EM;日本因 JPY·套息单列) |
| 中美四维 | 货币分化 · 增长通胀错位 · 贸易关税地缘 · 相对资产&资本流(全要;S3 四行 + 附录 C 四篇) |
| 中观 | macro-research 内 ▸中观 tier:M1 行业配置图 · M2 资金&游资 · M3 情绪周期&涨停 · M4 题材&风格 · D 景气桥(保留单抽接缝) |
| 命名 | `macro-research`(`analyze-macro` 为备选) |
| crypto/credit | crypto 必写;credit 作 optional |
| 情景集 | 增长×通胀四象限为骨架 + 可叠 1–2 自定义中美情景 |
| 校验 | 复用 `parse_rating` 逐行校验两张表;无新校验框架 |

## 10. 分两期落地

- **Phase 1(先做,可独立交付)**:`harvest_macro.py`(区域宏观 + 跨资产价 + 中观取数骨架)+ 区域 regime read(美/中/全球)→ 产出 S1 仪表盘 + A 区域证据 + draft 跨资产表 + M1 行业配置图骨架。可独立测取数 + regime 判断,先把"世界观 + A股行业图"立起来。
- **Phase 2(完整)**:补 B 跨资产 + C 中美四专题 + M2–M4 中观细节 + S2–S5(预期差/对撞/情景/日历/红队)+ `assemble_macro.py`(双表校验)。
- **phasing 直接回应 build 复杂度:取数骨架 + 区域读数先跑通,再叠综合层。**

## 11. 风险 / 开放问题

- **akshare 版本漂/限流**(`macro_china_*` 与中观板块/龙虎榜/涨停端点)→ 防御取列 + 缓存 `context/macro/<date>/` + 重试 + WebSearch 兜底(沿用 `harvest_context.py` 风格)。
- **FRED 国际 series ID** 需建库时逐个确认(透传机制已支持,只需确认 ID 有效、口径一致)。
- **宏观确定性数据少、判断性强** → 比单票更依赖 Claude + WebSearch 前瞻;铁律对实测数仍适用,但情景概率/政策路径须显式标"判断/实时网查"。
- **北向个股实时披露 2024-08 已停** → 中观北向只用汇总/板块/季度口径,标 staleness。
- **跨资产相关性随 regime 漂移** → 配置表声明当前相关性假设(尤其股债)。
- as-of 分析日、无未来数据(沿用 analyze-ticker 铁律)。
- **开放**:中观是否同时做美股/全球 GICS 行业轮动(本期仅 A股,US 行业留 future);情景矩阵自定义情景的固定集 vs 每次现定;`debate/credit/industry_cycle` 三个 optional 是否升为必需。

## 12. 不在本期范围(future)

- **持久化 `regime-state.json`**:把 regime 象限/政策档/风险偏好落成结构化 state,供 analyze-ticker / scan-market 当背景板消费(升级现在内嵌的 China backdrop),并支持周期 diff(本期 vs 上期)。
- **scan-market L2 反向读中观**:让选股的板块聚合直接消费本 skill 的 M1 行业配置图。
- **事件驱动触发**(围绕 FOMC/CPI/中国数据/NPC 自动刷新)。
- **美股/全球行业轮动**(GICS 11 板块版中观)。
- **情景概率回测 / 因子有效性验证**(当前为判断性框架)。

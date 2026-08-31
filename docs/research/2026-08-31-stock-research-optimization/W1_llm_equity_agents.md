# W1 · LLM 多 agent 单标的股票研究系统:工程与论文调研(2025-01 ~ 2026-08)

调研日期:2026-08-30/31。方法:WebSearch 定位 + WebFetch 原文核实。全文标注两档可信度:
- **[F]** = 已 fetch 原文核实(arXiv abs/html、官方 GitHub/文档/博客、NBER 页);共 69 个来源 fetch 成功。
- **[S]** = 仅搜索摘要(未打开原文或原文 403),关键数字谨慎引用;确实存疑处另标「未核实」。

背景对齐:我方系统 = Claude-in-session 引擎 + 免费数据层(零付费 LLM API),单标的 skill `stock-research`(full 报告 / lite 决策卡),持仓超短(T+1 尾盘买 → T+2 开盘卖,A 股)。本报告一切「启示」按此约束过滤。

---

## ① 一页摘要:10 条最有用的发现

1. **「LLM agent 报的 alpha 不可信」已成 2026 年主流结论**:The Alpha Illusion 用 2025-01~2026-01 真窗口复现 TradingAgents/QuantAgent,Sharpe 从论文宣称的 8.21 掉到 0.43(毛)/0.22(净),双双跑输 buy-and-hold(+9.61%)。[F] https://arxiv.org/abs/2605.16895
2. **第一病因是训练语料信息泄漏**:Profit Mirage 定量显示回测一跨过模型知识截止日收益即消失(FinMem 收益 −72%、QuantAgent Sharpe −51%);KTD-Fin 在 CSI300 上把票名/日期打码后,agent 收益基本只剩市场+风格暴露、几无选股 alpha。[F] https://arxiv.org/abs/2510.07920 · https://arxiv.org/abs/2605.28359
3. **长窗大样本回测否定 LLM 择时**:FINSABER(KDD 2026 oral)20 年、100+ 标的:LLM 策略长期不跑赢,牛市过度保守、熊市过度激进;作者结论是「做趋势识别与 regime 感知的风控,别堆框架复杂度」。[F] https://arxiv.org/abs/2505.07078
4. **多 agent 辩论的通用证据偏负**:MAD 常打不过 CoT/self-consistency 且贵 2~4 倍 token(Stop Overvaluing MAD;Cost of Consensus 实测 85.5% 从众采纳、oracle gap 32pp);金融综述要求凡称多 agent 增益必须报单 agent 消融。唯一较稳的正面因子是**模型/视角异质性**。[F] https://arxiv.org/abs/2502.08788 · https://arxiv.org/abs/2605.00914
5. **但「协调结构 > 模型规模」有中等强度证据**:金融多 agent 综述(PAKDD 2026)汇总:去掉协调机制 Sharpe −15~30%,而把大模型换小只 −5~8%;Agent Market Arena 实盘同样发现 agent 架构差异远大于底模差异。[F] https://arxiv.org/abs/2603.27539 · https://arxiv.org/abs/2510.11695
6. **「研究质量」评测已产品化、可抄作业**:Vals Finance Agent(537 真实分析师任务,2026-06 榜首 Claude Opus 4.7 = 64.4%)、BigFinanceBench(928 任务 / 36,241 个 rubric 点,按「推导过程」计分,最好系统仅 58.8%)、Deep FinResearch Bench(四家 Deep Research agent 全面落后人类分析师:幻觉率 11~34%、目标价系统性乐观 +8~34%)。[F] https://www.vals.ai/benchmarks/finance_agent · https://arxiv.org/abs/2606.03829 · https://arxiv.org/abs/2604.21006
7. **通用预测上 AI 已与超级预测员统计打平,但金融决策的置信度仍失准**:ForecastBench 2026-07 官宣多系统与 superforecaster 不可区分;而 LLM 口头置信度普遍过自信,Alpha Illusion 明确要求「任何用于仓位的概率必须先过 ECE 校准」。[F] https://forecastingresearch.substack.com/p/ai-models-have-likely-reached-parity · https://arxiv.org/abs/2605.16895
8. **面向短线/隔夜的专门证据稀少且偏负**:A 股隔夜新闻 LLM 指标在控制 A50 期货后增量有限(Finance Research Letters 2026 [S]);Tan et al. 发现 A 股新闻信号需 ~2 天才被价格吸收 [S];唯一强正读数是 Agentic Nowcasting(Russell 1000 日频、事前实时收集、五因子+动量 alpha 18.4bp/日、Sharpe 2.43,但只在 top-20 多头腿、未在 A 股复现)。[F] https://arxiv.org/abs/2601.11958
9. **工程模式在头部商用产品上已收敛**:句级可点击引用 + 双层引用验证(Brightwave:字符区间/PDF bounding box/单元格;结构校验 + LLM 支持性校验)、检索与产出分离 + 上下文蒸馏 agent(Hebbia:上下文压缩 >90%)、模型无关 harness + 内部真实任务评测集(Rogo Felix)。[F] https://www.brightwave.io/blog/citations · https://www.hebbia.com/blog/inside-hebbias-deeper-research-agent
10. **A 股开源生态繁荣但零可信收益证据**:上游 TradingAgents(101.8k star,2026-07 v0.3.1 已支持 A 股与前视过滤)、TradingAgents-CN(31.5k star,tushare/akshare/baostock)、TradingAgents-AShare(15 agent 辩论可视化 + Claude Code skill 集成)、ContestTrade(唯一「A 股 + 明示时间协议」的多 agent 系统);没有任何一个给出可信 out-of-sample 收益。[F] 各 GitHub README

---

## ② 分节详述

### 2.1 开源多 agent 交易/研究框架现状

#### 2.1.1 学术/开源框架总览

| 框架 | 架构要点 | 数据源 | 可信 OOS 收益证据 | A 股 | 2026 进展 |
|---|---|---|---|---|---|
| TradingAgents (Tauric) [F] | 分析师团(基本面/情绪/新闻/技术)→ 多空研究员辩论 → 交易员 → 风控三视角 → 基金经理;快/慢双模型(quick=gpt-4o-mini,deep=o1-preview);ReAct | yfinance、Alpha Vantage、FRED、Polymarket、Reddit/新闻 | **无**。论文仅 AAPL/GOOGL/AMZN 三票 3 个月(2024-01~03),Sharpe 5.6~8.2 作者自己标注异常;第三方净化复现掉到 0.22 | v0.3.x 起官方支持中国 A 股 | v0.3.1(2026-07):Alpha Vantage 前视过滤、checkpoint 续跑、结构化输出 agent、12+ provider;101.8k star |
| Trading-R1 (Tauric) [F] | Qwen3-4B,SFT+GRPO 三阶段课程(结构→证据→决策),波动率归一 + 非对称分位五档标签(SB15/B32/H38/S12/SS3) | Tauric-TR1-DB:100k 样本、14 票、18 个月、5 源 | 6 票 held-out 窗上 Sharpe 优于 GPT-4.1/o3-mini(NVDA 2.72 vs 0.85);**注意**:HTML 抓取显示测试窗 2024-06~08 与训练窗 2024-01~2025-05 描述有出入(疑为 2025-06~08,未复核 PDF) | 否 | 权重/数据/Terminal **均未发布**(repo 仅 README,474 star);论文自认「只当研究与论点生成工具」 |
| TradingAgents-CN (hsliuping) [F] | 上游架构汉化 + 工程化(FastAPI+Vue3+MongoDB/Redis+Docker) | tushare/akshare/baostock,港股;DeepSeek/Qwen/豆包/火山方舟 | 无(README 明示仅供研究学习) | **是**(核心卖点) | v1.1.0(2026-07-24);31.5k star |
| TradingAgents-AShare (KylinMountain) [F] | 14-15 agent 三层(6 分析师 + 多空辩论主张驱动 + 三方风控仲裁),辩论全程 token 级可视化 | A 股行情/公告(README 未列明 API) | 无 | **是** | 提供 OpenClaw/Claude Code skill(`tradingagents-analysis`,输出方向/信心/目标价/止损结构化结果);804 star |
| ContestTrade (FinStep) [F] | 数据团队(海量数据→文本因子)+ 研究团队(并行多路径决策);**内部竞赛机制**:按真实市场结果对 agent 持续打分排名,只采纳头部 agent 输出 | A 股数据 | 自报「优于多 agent 基线与传统量化」,v4/v5(2026-07/08)补时间协议与局限声明;仍属自评 | **是** | v5 2026-08-20;开源 |
| FinMem / FinAgent / FinCon [F] | 单 agent 分层记忆(FinMem)→ 多模态+双层反思+工具(FinAgent,自称利润 +36%)→ 经理-分析师层级+「概念性言语强化」风控(FinCon) | 美股/crypto | **反面教材**:FinMem 23% 收益在受控重评下反转为 −22%;跨截止日后 −72%(泄漏);FinAgent/FinCon 未过 5 项最低评测标准(2/5 与 0/5) | 否 | 无实质 2026 更新;被各评测论文当靶子 |
| FinRobot / FinGPT / FinRL-X (AI4Finance) [F] | FinRobot 四层平台 + 股票研报专版(Data-CoT/Concept-CoT/Thesis-CoT 三 agent 出研报,2411.08804 [S]);FinGPT 2023 框架;FinRL-X(2025)AI-native 量化基础设施(PAKDD 2026 最佳展示) | 多源 | 无收益证据;FinRobot 研报质量自称「可比券商」但无第三方评测 | 页面未见 A 股专项 | 重心已转向 FinRL-X 基础设施 |
| StockAgent [S/F] | 大规模 LLM 投资者行为**模拟**(政策/宏观外因对交易行为影响),非选股工具 | 模拟环境 | 不适用(无真实市场证据,论文自认) | 否 | v5(2026-06)仍在维护 |
| HedgeAgents [F] | 基金经理 + 多资产对冲专家 + 三类会议 | 多资产 | 自报 3 年 400% / 年化 70%(WWW 2025 oral)——**未过泄漏与成本检验,请勿采信** | 否 | 被 2026 评测综述列为「2/5 项标准」 |
| AlphaAgents (BlackRock) [F] | 基本面(10-K/Q RAG)/情绪(Bloomberg 新闻)/估值(量价)三 agent,AutoGen 轮转辩论至共识 | Bloomberg + filings | 弱:仅 15 只科技股、2024-02~05 四个月、等权基准、无成本、无辩论消融 | 否 | 机构署名的意义大于结果本身 |
| FLAG-Trader / Fin-R1 / FinRL-DeepSeek [S] | RL 微调路线:LLM 作 policy 梯度微调(FLAG-Trader,ACL 2025 findings);7B 金融推理 SFT+RL(Fin-R1,60,091 CoT 样本);新闻信号注入 CVaR-PPO(FinRL-DeepSeek,Nasdaq-100) | — | 均为学术自报;FinRL-DeepSeek 承认跨 run 随机性大 | 否 | 需 GPU 训练,与零 API 约束无交集 |
| TwinMarket [S] | LLM 模拟社会化市场(泡沫/踩踏涌现),NeurIPS 2025 & ICLR 2025 Financial AI 最佳论文 | 模拟 | 不适用 | 部分 A 股行为设定 | 用途是市场机理研究,非选股 |

#### 2.1.2 Deep Research 类产品用于金融

- **OpenAI Deep Research**(2025-02-02 发布,o3 系);5~30 分钟自主浏览出带引用报告;官方明示局限:幻觉、难辨权威源、**置信度校准差**。[S](官网 403,多方转述一致)第三方称其独立 API 模型 2026-07-23 下线(未核实)。
- **Gemini Deep Research**(API 版 2026 预览,文档 2026-08-26 更新 [F]):仅走 Interactions API、必须 background 异步;可配 Google Search/URL/代码执行/MCP/File Search;**不支持结构化输出**;60 分钟上限;估价 $1–3/标准任务、$3–7/深度任务。「Deep Research Max、DeepSearchQA 93.3%」为第三方转述 [S,未核实]。
- **Perplexity Finance**(2024-10 起,2025-02 独立 dashboard [S]):行情/财报日历/电话会转写/自然语言选股器/Plaid 组合;数据方 FactSet、S&P、LSEG、Morningstar、Quartr 等。
- **对金融任务的第三方评测结论**(Deep FinResearch Bench [F]):四家 DR agent(OpenAI/Gemini/Grok/Perplexity)写 pre-earnings 研报,**质性维度全面低于人类分析师**(最好 2.31 vs 2.84),优势仅在 6 个月方向命中(Gemini 61.9% vs 人 53.3%);幻觉率 11.2~34.1%,目标价平均乐观偏差 +8~34%。

#### 2.1.3 商用研究 agent 的公开架构

- **Hebbia「Deeper」**(2025-07-30 [F]):七类 agent(编排/规划/检索/文档分析/**蒸馏**/推理/产出),explore-exploit 两段式;ISD(迭代源分解)把每条结论绑到原文引句;**上下文蒸馏 agent 压缩 >90%**;产出 agent 分节多跳写报告以保引用完整。
- **Rogo「Felix」**(2026-05-01 [F]):明确定位「护城河是 harness 不是模型」——编排/工具/引用/输出格式(Excel/PPT/Word house style)/审计轨迹/权限;跨 GPT/Claude/Gemini 按内部「Big Finance Benchmark」(前金融从业者出的真实任务)换模;E2B 案例 [F]:Claude Managed Agents 编排 + 万级并发沙箱;Google Cloud 案例称换 Gemini 2.5 Flash 后幻觉率 34.1%→3.9% [S]。
- **AlphaSense**(Deep Research 2025-06-10 发布 [F]):5 亿+ 文档(券商研报/专家访谈 Tegus/filings)上的 agentic 工作流,粒度化引用;2026-01 起 Generative Search 升级为多 agent「Workflow Agents」(公司 primer/竞争格局/SWOT 模板化)[S]。
- **Brightwave**(工程博客 [F]):2025-08 Research Agents(推理时生成研究计划、后台长任务);**2026-03-18 引用工程文**:引用解析到字符区间/PDF bounding box/表格单元格/JSON 路径,双层验证(结构 + LLM 支持性),月百万级生产验证。
- **Fintool**:SEC filings/财报电话会专用 copilot [S,未深核]。

### 2.2 论文:有效/证伪/评测设计/短线

#### (a) 哪些做法有正证据、哪些被证伪

**有正证据(按强度排序)**
1. **检索增强 + 工具核算数值**:Halawi 2024 消融:去检索 Brier 0.179→0.206;New Quant 综述(50+ 篇)总结「retrieval-first prompting + tool-verified numerics」是唯一被反复验证的可靠性设计。[F]
2. **信息抽取层的价值(而非决策层)**:Koijen & Levy(NBER w35431,2026-07):优化过的 agentic 系统从财报电话会抽结构化信号,对公告日**同期**收益解释力 R² 8%→20%,真实时 out-of-sample;Lv 2025:LLM 读 120 万份卖方研报叙事,叙事信号在数字评级之外仍有显著增量。[F]
3. **细粒度任务分解**:Miyazaki/Roberts/Zohren(2026-02,日股、控泄漏回测):细粒度任务分解显著优于「粗粒度角色扮演」;「分析输出与下游决策偏好对齐」是首要性能驱动。[F]
4. **协调结构设计**(中等):见摘要第 5 条;结构化辩论 2~4 轮内有边际、超过即「思维退化」。[F]
5. **异质性**(中等):MAD 唯一稳定增益来自模型/视角异质(Heter-MAD);匿名化发言几乎消除从众。[F/S]
6. **实时 nowcasting 形态**(单点强证据,待复现):Agentic Nowcasting(2601.11958):每天实时让 agent 网搜并排名 Russell 1000,top-20 多头 alpha 18.4bp/日、Sharpe 2.43、成本 <10% 毛 alpha;**alpha 高度集中于头部,空头腿无信号**。[F]

**被证伪/强负证据**
1. **端到端 LLM 交易 agent 的回测 alpha**:Alpha Illusion(六维效度 + P1-P6 报告协议)、FINSABER(20 年否定)、StockBench(14 模型大多输给 buy-and-hold)、LiveTradeBench(LMArena 分数与交易表现不相关)、AMA(架构>模型)。[F]
2. **泄漏主导历史回测**:Profit Mirage;Gao/Jiang/Yan 的 LAP 统计检验(训练窗内前视倾向显著为正、截止日后瞬间归零);SPY Lab 列的四类评测坑(逻辑泄漏/检索泄漏/假知识截止/榜单赌博)。[F]
3. **反思/记忆自动学习**:综述结论:RAG 记忆引发 experience-following 放大锚定;分层时间记忆在结构断裂期失效;FinMem/FinAgent 的「反思增益」与泄漏混同。[F](与我方 2026-08-21 learning 层退役裁定同向)
4. **更强推理 ≠ 更好交易**:StockBench(reasoning 版反不如 instruct 版)、TraderBench(extended thinking:知识检索 +26 分、交易 +0.3/−0.1 分)、TradeTrap(单组件小扰动即穿透决策环)。[F]
5. **匿名化防泄漏得不偿失**:Wu/Yang/Ying/Zhou(2511.15364):匿名化造成的信息损失「比前视偏差本身更普遍更严重」。[F]
6. **多 agent 辩论堆轮数**:见上;金融侧还要过 CBS(协调收支平衡价差)经济性门槛——低个位数 bp,仅高流动性标的可行。[F]

#### (b) 评测设计共识(2025-2026 收敛出的清单)

金融多 agent 综述 [F] 的**五项最低标准**(受评 12 系统无一全过):
1. 污染控制(评测窗后于训练截止,或做截止日消融);
2. Point-in-time 股票池(按历史成分,防幸存者偏差,估算约 0.9%/年);
3. 多个不重叠滚动窗 + 方差;
4. 净成本收益(佣金/半价差/冲击;日频系统年拖累可达 25~50pp);
5. regime 覆盖或对抗压力测试。

Alpha Illusion 的 **P1-P6** 按声称强度分级(研究辅助只需 P1/P3 轻量版;宣称「可部署 alpha」需全套):P1 时间完整性(披露模型版本/截止日/检索时间戳 + 至少一个截止日后窗口)、P2 动态股票池、P3 反事实鲁棒(反向证据下方向/信心/仓位单调)、P4 校准(ECE/可靠性曲线/分 regime)、P5 现实摩擦(含 **token 成本与推理延迟**)、P6 多 agent 拆解(单 agent 基线/角色相似度/分歧率/净增量)。短窗 Sharpe 天生不可比:250 个日度观测、SR≈2.4 时 95% CI 半宽 ≈1.0。[F]

Agentic Trading 系统性审计 [F]:77 篇仅 19 篇达最低评测门槛,其中只有 2 篇给出时间一致、可提取的协议;15/19 处于最低可复现级 R0。评测已从「静态回测」转向**实时前向**(LiveTradeBench 50 天、AMA lifelong、Koijen-Levy 公告窗、ForecastBench 动态出题)。

#### (c) 面向短线/隔夜的专门工作

- **A 股隔夜**:「LLM 隔夜新闻指标是否在 A50 期货之外增值?」(Finance Research Letters 2026 [S]):分钟级隔夜新闻 + 微调 RoBERTa 预测 CSI300 隔夜收益,单独有效,**控制 A50 期货后增量有限**——市场价格已把隔夜新闻大体定价。
- **A 股新闻横截面**:Tan/Wu/Zhang(SSRN [S]):7 个中文 LLM 读新闻,VW 多空年化 35~67%(未见净成本口径),信号**需约 2 天**被吸收、在高摩擦/高散户/复杂新闻股更强——兑现窗与我方 T+1→T+2 隔夜尺错位,更贴 5~10 日窗。
- **财报事件窗**:FinCall-Surprise(2,688 场电话会,26 模型,多模态增量有限);Koijen-Levy(解释性而非预测性)。[F]
- **日频 nowcasting**:Agentic Nowcasting(见上)是目前唯一强读数,但形态是「流动性大票 + top-N 多头 + 每日重排」,与 A 股 T+1 制度和排板可成交性问题正交。[F]
- **结论**:2025-2026 文献里**没有**与「T+1 尾盘买→T+2 开盘卖」同构的 LLM 研究;最接近的证据都指向「隔夜窗的公开信息增量已被期货/开盘价吃掉」——与我方隔夜集中信号普查(六主格正证据 0)互为印证。

### 2.3 「研究质量」的可量化评测

**LLM-as-judge 评研报的 rubric 工程**
- **Vals Finance Agent Benchmark**(arXiv 2508.00828 + 官网 [F]):537 道专家题、九类任务(定量/定性检索、数值推理、GAAP 调整、beat-or-miss、趋势、建模、市场分析);评分 = GPT-4o 自动抽 rubric checkpoint + 人工复核 + 矛盾检测。论文期(2025-05)最好 o3 46.8% @ $3.79/query;官网 2026-06-04 更新:Claude Opus 4.7 64.37% 居首。发现:**工具调用的策略质量 > 调用量**(最高错误率的模型恰是调用最多的)。
- **BigFinanceBench**(2606.03829 [F]):928 任务、36,241 rubric 点,**按推导过程给部分分**(数据源选择/口径/假设/算法逐步可验),能做失败定位;最好 58.8%。
- **FinResearchBench II**(2607.12252 [F]):**共识派生 rubric**——三 LLM judge 全一致(consistency filter)+ 能区分系统优劣(distinguishability filter)双过滤,14,450 候选 rubric 砍到 2,600;与人类专家标签一致率 98.67%;10 个 deep research 系统通过率 22.2%~58.6%。
- **ICBCBench**(2606.17458 [F],50+ 专家/40+ 机构):双轨(客观可验答案 + 主观长报告);rubric 4-6 维 / 12-16 子维 / 100 分制,总分 = 0.8·专家维 + 0.1·引用一致性 + 0.1·来源质量;LLM judge 与专家 Spearman 0.643 ≈ 专家间 0.638。核心发现:**主观报告分远高于客观题分(「能力错觉」)**——报告写得像样不等于事实对。
- **CNFinBench**(KDD 2026 [F]):中文金融 agent 全链路测评,整链执行比孤立模块掉 15.4 分;多轮对抗下第 2 轮违规率 +159%。

**研报与后续收益**:Lv 2025(narratives 有增量、慢扩散)[F];Deep FinResearch Bench 直接量化 AI 研报的预测面(SMAPE/目标价命中/方向),给出「质性差、方向命中略胜」的分裂画像 [F]。卖方目标价本身长期过乐观是老结论(我方触价校准 39% 达成读数同向),此处不展开。

**预测校准(Brier/ECE)**
- Halawi 2024 [F]:系统 0.179 vs 人群 0.149(914 题);选择性出手(人群不确定 0.3-0.7 区)0.238 vs 0.240 打平乃至反超;**裸模型无检索/微调时 ≈0.208 接近盲猜**。
- Lu 2025 [F]:o3 = 0.1352 优于 Metaculus 人群 0.149,但专家中位 0.0225(157 题子集)远优;LLM 高概率区系统性过自信。
- ForecastBench(ICLR 2025 论文 + 2026-07 更新 [F]):多系统与 superforecaster 统计不可区分(bootstrap p 0.14~0.41);头名 Cassi AI 是「子问题分解→检索→过滤→集成→LLM 复审」流水线,**不是单模型**。
- Prophet Arena [F]:按 Brier、校准、市场收益三尺排名各不同——「准」「校准」「能赚」三件事要分开量。
- 内部表征探针(2607.08046 [F]):模型隐层比口头概率**校准更好**,答案在推理文本生成前已大体确定;按答案分布宽度路由可省 30-47% token。

### 2.4 工程模式

**结构化输出契约(JSON 决策卡)**
- Anthropic 官方结构化输出 [F]:constrained decoding 保证 schema 合法(JSON output + strict tool use 两механизм);注意不支持递归 schema/数值范围约束;**改 output_config.format 会打穿 prompt cache**,语法编译结果缓存 24h。
- Structured Output Benchmark(2604.25359 [F])与多方 2025-2026 评测共识:**schema 合规 ≈100% 但值正确率上限 83%(文本)**;弱模型在语法约束下会坍缩到「schema 合法但语义最平庸」的输出。⇒ 契约层管形状,数值必须另有确定性核算(我方 parse_rating + 价格断言对账即此模式)。

**证据引用与可验证性**
- Brightwave [F]:引用 = 精确坐标(字符区间/bbox/单元格/路径)+ 双层验证(结构解析成功 + LLM 判「引文确实支持该主张」);RAG 式「chunk 级引用」被明确判为不够。
- Hebbia ISD [F]:检索与产出分离,每主张回链原文引句;上下文蒸馏专职 agent。
- 评测端同构:Deep FinResearch Bench 的 claim 级 factuality/hallucination/non-verification 三率、ICBCBench 的引用一致性 10% 权重。⇒ 「引用率」已是研究质量的一等公民指标。

**Prompt cache 友好的任务包拼装**(Anthropic 官方文档 [F])
- 前缀层级 tools→system→messages,任何早段字节变化全部击穿;显式断点最多 4 个,回看窗 20 块;5 分钟 TTL 写价 1.25x、1 小时 2x、读 0.1x;最小可缓存 512~4096 token(按模型)。
- 直接可用的三条:①按变更频率分层放断点(稳定工具/日更上下文/每票请求);②时间戳等易变字段严禁进共享前缀(我方「逐票标题排共享块前致首字节断裂」教训与此完全同构);③长前缀可用 max_tokens=0 预热。

**早停/渐进深度**
- Doomed from the Start(2607.06503 [F]):隐层探针 + 召回受控级联,90% 召回下省 55~60% token——「失败预判早停」可保守校准。
- CAM-DF(2607.27083 [F]):停止取数是**成本感知决策**而非分数阈值;少暴露 37% 工具不掉成功率。
- LearnStop(2606.30852 [F]):早停收益强依赖基础设施——KV 缓存分叉下省 32%,黑盒重复 prefill 下反贵 121%;小样本难题「无可认证的激进策略」。
- 金融特化警示:TraderBench——extended thinking 对交易决策无增益 ⇒ 深度应花在**取证与核算**,不是花在最终决策的思考长度。

**成本-质量公开数据点**
- Vals:任务级 Pareto 曲线,难题 >$5/次;FAB 论文:o3 46.8%@$3.79 vs Claude 3.7 Thinking 45.9%@$1.02(4 倍性价比差)。
- 金融综述:七 agent 系统 $0.50~2.00/天/决策;每轮辩论 1~3 秒延迟;Alpha Illusion 实测五票组合年 token 成本 ≈$3,000(必须计入净收益)。
- TradingAgents 论文自述每次预测 11 次 LLM 调用 + 20+ 次工具调用——这是它只敢回测 3 个月的原因。
- Gemini DR $1–3/任务;Hebbia 蒸馏 90%;探针路由省 30–47%。

---

## ③ 对我们 stock-research 的直接启示(12 条,标证据强度)

1. **【强】维持并显式化「LLM=可审计信息接口,决策权在确定性层」的架构自我定位**。Alpha Illusion 的六段模块化替代方案(抽取→特征→信号→独立校准→风控 sizing→执行审计)与我方「lite 卡 + parse_rating + E6 唯一 BUY owner + 门体系」同构;这是 2026 年文献唯一背书的角色分工。可把这句话写进 skill 契约,防止未来把决策权漂移回 prompt。
2. **【强】给五档评级建「校准账本」**:对历史决策卡按评级档位统计隔夜尺命中率与 Brier/ECE(我方 outcome 账本已有原料,加派生列即可)。文献一致结论:LLM 口头置信度不可直接当概率;E6/仓位相关判断引用卡片信心前应查该表。P4 协议原文:「任何用于 sizing 的概率对象必须 ECE≈0」。
3. **【强】评测/复盘一律用「截止日后 + 实时前向」证据,拒绝历史回测**。我方「决策日实时产卡 + 账本事后对账」的既有流程恰好是 Profit Mirage/LAP 指定的唯一干净形态;任何「用 Claude 回看历史日期出卡」的提议都应被 CLAUDE.md 级禁止(泄漏必致假阳)。
4. **【中】给 lite 卡加「反事实翻转探针」进 self_review 抽查**:随机抽卡,注入反向证据摘要重打一次,检查方向/信心变动是否单调合理(P3;TradeTrap 显示单点扰动可穿透全链)。低频抽查即可,零新增依赖。
5. **【中-强】辩论机制保「异质+独立先答+匿名互评」,砍同质轮数**:MAD 文献的三条稳健结论——异质性是唯一稳定增益、匿名化消从众、2 轮以上边际为负。我方 L4 双复核折回(仅 ≥OW 触发)方向正确;不要再加轮数,若要强化就换「视角异质」(如强制一个纯风控视角引用不同数据切面)。并按 P6 留单 agent 对照的账(我方 E6 影子/账本已可派生)。
6. **【中】数值「工具核算」全覆盖**:schema 合规≠数值对(SOB:值正确率上限 83%)。卡片中每个进决策的数字(R:R、触发价、pct 分位)都应由确定性脚本产生或复核,LLM 只许引用——我方价格断言对账已做,可扩到 R:R 计算链。
7. **【中】关键主张绑「精确证据坐标」**:full 报告的决策主线每条关键 claim 标注来源文件+字段/行级读点(我方 capsule source_lineage 已留痕,差的是把它回填进报告正文的引用标记);评测侧用「引用可解析率 + 支持性抽查」当 lint 指标。这是 Brightwave/Hebbia/ICBCBench 三方收敛的质量地板。
8. **【强】任务包拼装的 cache 契约按官方机制再锁一圈**:分层断点(稳定 skill 文本/当日市场包/逐票 slim)、易变字段(时间戳、run_id)永不进共享前缀、byte-identical 测试保留;这与 Anthropic 文档的 20 块回看窗/断点语义完全对齐,我方 token-economy 契约测试有官方依据可引。
9. **【中】早停信号用「答案稳定性」而非「思考预算」**:L4 渐进深度早停(只向下)已符合文献方向;可加一条廉价判据——连续两级深度评级与关键理由不变即停(答案收敛早停在推理文献反复验证);同时明确「不为最终决策加长思考」(TraderBench:thinking 对交易 +0.3 分)。
10. **【中】新闻/情绪信号定位改为「5~10 日观察单侧」而非隔夜**:A 股新闻兑现 ~2 天 + 隔夜增量被 A50 定价 + 我方低位转强周级尺读数,三方向同一结论:lite 卡的隔夜 R:R 不应主要押注新闻面;新闻强度更适合喂观察单/周窗提名。
11. **【中】评级标签基准借鉴 Trading-R1 的「波动率调整 + 非对称分位」**:五档占比(SB15/B32/H38/S12/SS3)与按 20 期滚动波动率归一的 3/7/15 日前向收益分位,可直接用作我方账本上「评级应有分布」的对照尺(检查我方卡是否 Hold 挤压/方向偏斜),零 LLM 成本。
12. **【中】rubric 自检加「区分度过滤」**:FinResearchBench II 的两道过滤(judge 一致 + 能区分好坏)是防「永绿假灯」的通用配方——我方 brief_lint/self_review 新增检查项时,先验证它在历史好/坏样本上确实给出不同判定再上线(与我方变异探针方法论同族,文献背书)。

---

## ④ 「别做」清单(已证伪或与零 API 成本 + 超短窗约束不符)

1. **别做端到端「LLM 直接给买卖、拿回测收益当证据」**——Alpha Illusion/FINSABER/StockBench/LiveTradeBench 四路齐否;凡此类外部框架宣称的 Sharpe(TradingAgents 8.21、HedgeAgents 年化 70%、FinAgent +36%)一律按「未过 P1-P6」处理。
2. **别用跨知识截止日的历史回测验证任何 prompt/流程改动**——泄漏红利会把坏改动测成好改动(Profit Mirage:截止日后收益消失;LAP:截止日后前视倾向归零)。
3. **别走实体匿名化防泄漏路线**——信息损失比前视偏差更伤(2511.15364);我方实时前向流程根本不需要。
4. **别加同质多 agent 辩论轮数/扩大辩论面**——token 2~4 倍、从众 85%、金融侧还有协调收支门槛(CBS 低个位数 bp);我方早已裁定的「简报只定向不判、早停只向下」应保持。
5. **别碰 RL 微调路线(Fin-R1/Trading-R1/FLAG-Trader 复刻)**——需 GPU/付费数据管线,Trading-R1 官方自己都只当「论点生成工具」且权重未放;与零付费 API 铁律冲突。
6. **别重建自动反思/经验记忆学习环**——外部文献(experience-following 放大锚定、反思增益与泄漏混同)与我方 2026-08-21 learning 层退役裁定互证;lessons 只走人批。
7. **别做「LLM 预测隔夜方向」类特性**——A 股隔夜公开信息增量已被 A50/开盘价定价(FRL 2026),与我方隔夜普查(正证据 0)一致;隔夜 R:R 只应来自确定性结构(位置/成交模型),不来自模型观点。
8. **别为决策质量堆推理预算/换更大思考档**——三个基准一致:QA/推理分数与交易决策质量不相关,extended thinking 对交易 ≈0 增益;深度预算应流向取证与核算。
9. **别在无执行/成本模型时输出仓位建议**——文献日频系统成本拖累 25~50pp/年;我方「BUY owner=14:45 可成交性」裁定优先级高于任何卡片仓位字段。
10. **别把 Deep Research 类产品输出当研究底稿直接采信**——幻觉率 11~34%、目标价乐观 +8~34%、来源可验证率最低 53%;引用其结论必须逐条回源(我方外源两层一账制度适用)。
11. **别追「多 agent 数量」型升级(15 agent 类 fork 的方向)**——AMA/综述:架构与协调质量主导,agent 数量与角色戏剧化无证据;A 股社区 fork 的价值在数据接线与工程化,不在其 agent 编制表。

---

## ⑤ 参考文献表

| # | 标题 | 机构/作者 | 日期 | URL | 核实方式 |
|---|---|---|---|---|---|
| 1 | TradingAgents: Multi-Agents LLM Financial Trading Framework (arXiv 2412.20138, v1-v7) | Tauric Research / UCLA / MIT | 2024-12-28~2025-06 | https://arxiv.org/abs/2412.20138 | 已 fetch(abs+html v6) |
| 2 | TradingAgents GitHub(v0.3.1) | TauricResearch | 2026-07 | https://github.com/TauricResearch/TradingAgents | 已 fetch |
| 3 | Trading-R1: Financial Trading with LLM Reasoning via RL (2509.11420) | Tauric Research | 2025-09-14 | https://arxiv.org/abs/2509.11420 | 已 fetch(abs+html;测试窗年份存疑已标注) |
| 4 | Trading-R1 GitHub(未发布) | TauricResearch | 2026-08 快照 | https://github.com/TauricResearch/Trading-R1 | 已 fetch |
| 5 | TradingAgents-CN(v1.1.0) | hsliuping 社区 | 2026-07-24 | https://github.com/hsliuping/TradingAgents-CN | 已 fetch |
| 6 | TradingAgents-AShare | KylinMountain | 2026 快照 | https://github.com/KylinMountain/TradingAgents-AShare | 已 fetch |
| 7 | ContestTrade (2508.00554, v5) | FinStep-AI | 2025-08~2026-08-20 | https://arxiv.org/abs/2508.00554 | 已 fetch |
| 8 | The Alpha Illusion (2605.16895) | Ye, Han et al. | 2026-05-16 | https://arxiv.org/abs/2605.16895 | 已 fetch(abs+html 全文) |
| 9 | Profit Mirage: Revisiting Information Leakage in LLM-based Financial Agents (2510.07920) | Li, Zeng et al. | 2025-10-09 | https://arxiv.org/abs/2510.07920 | 已 fetch |
| 10 | Can LLM-based Financial Investing Strategies Outperform the Market in Long Run?(FINSABER, KDD 2026 oral) | Li, Kim, Cucuringu, Ma | 2025-05-11/2026-06-26 | https://arxiv.org/abs/2505.07078 | 已 fetch |
| 11 | StockBench (2510.02209 v2) | Chen, Yao et al.(清华系) | 2025-10-02/2026-03-02 | https://arxiv.org/abs/2510.02209 | 已 fetch(abs+html) |
| 12 | From Knowing to Doing: Memory-Controlled Benchmark on CSI300 (2605.28359) | Zhu, Zhao et al. | 2026-05-27 | https://arxiv.org/abs/2605.28359 | 已 fetch |
| 13 | Toward Reliable Evaluation of LLM-Based Financial MAS(PAKDD 2026 wkshp, 2603.27539) | Nguyen & Pham | 2026-03-29 | https://arxiv.org/abs/2603.27539 | 已 fetch(abs+html) |
| 14 | Agentic Trading: When LLM Agents Meet Financial Markets (2605.19337) | Xia, You et al. | 2026-05-19 | https://arxiv.org/abs/2605.19337 | 已 fetch |
| 15 | The New Quant: Survey of LLMs in Financial Prediction and Trading (2510.05533) | Weilong Fu | 2025-10-07 | https://arxiv.org/abs/2510.05533 | 已 fetch |
| 16 | A Review of LLMs for Stock Price Forecasting from a Hedge-Fund Perspective (2605.05211, IEEE CAI 2026) | Zhang & Zhang | 2026-04-10 | https://arxiv.org/abs/2605.05211 | 已 fetch |
| 17 | Detecting Lookahead Bias in LLM Forecasts (2512.23847) | Gao, Jiang, Yan | 2025-12-29/2026-06-12 | https://arxiv.org/abs/2512.23847 | 已 fetch |
| 18 | Anonymization and Information Loss (2511.15364) | Wu, Yang, Ying, Zhou | 2025-11-19 | https://arxiv.org/abs/2511.15364 | 已 fetch |
| 19 | LLM Forecasting Evaluations Need Fixing | SPY Lab (Paleka, Goel, Geiping, Tramèr) | 2025-06-05 | https://spylab.ai/blog/forecasting-pitfalls/ | 已 fetch |
| 20 | Finance Agent Benchmark (2508.00828) + 官网 leaderboard v1.1 | Vals AI / Stanford / G-SIB | 2025-05-20;榜 2026-06-04 | https://arxiv.org/abs/2508.00828 · https://www.vals.ai/benchmarks/finance_agent | 已 fetch(论文 html + 官网);v2 榜(2026-08)仅搜索摘要 |
| 21 | BigFinanceBench (2606.03829) | Wang, Meinhardt et al. | 2026-06-02 | https://arxiv.org/abs/2606.03829 | 已 fetch |
| 22 | FinResearchBench II (2607.12252) | Luan, Sun et al. | 2026-07-14 | https://arxiv.org/abs/2607.12252 | 已 fetch |
| 23 | Deep FinResearch Bench (2604.21006) | Haque, Papadimitriou et al. | 2026-04-22 | https://arxiv.org/abs/2604.21006 | 已 fetch(abs+html 全文) |
| 24 | ICBCBench (2606.17458) | 工行牵头 40+ 机构联盟 | 2026-06-16 | https://arxiv.org/abs/2606.17458 | 已 fetch(abs+html 全文) |
| 25 | FinSearchComp (2509.13160) | Hu, Jiao 等 23 人 | 2025-09-16 | https://arxiv.org/abs/2509.13160 | 已 fetch |
| 26 | BizFinBench GitHub(v1/v2) | HiThink Research | v1 2025-05-16;v2 官宣 2026-09-01(ICML 2026 接收) | https://github.com/HiThink-Research/BizFinBench | 已 fetch(v2 日期为 repo 自述,晚于本调研日,标注存此) |
| 27 | CNFinBench (2512.09506, KDD 2026) | Ding, Ding et al. | 2025-12-10/2026-06-08 | https://arxiv.org/abs/2512.09506 | 已 fetch |
| 28 | FinTradeBench (2603.19225) | Agrawal, Dutta et al. | 2026-03-19 | https://arxiv.org/abs/2603.19225 | 已 fetch |
| 29 | LiveTradeBench (2511.03628) | Yu, Li, You (UIUC) | 2025-11-05 | https://arxiv.org/abs/2511.03628 | 已 fetch |
| 30 | When Agents Trade: Agent Market Arena (2510.11695) | Qian 等 17 人 | 2025-10-13 | https://arxiv.org/abs/2510.11695 | 已 fetch |
| 31 | TraderBench (2603.00285) | Yuan, Xu et al. | 2026-02-27 | https://arxiv.org/abs/2603.00285 | 已 fetch |
| 32 | TradeTrap (2512.02261) | Yan, Mei et al.(上海 AI Lab 系) | 2025-12-01 | https://arxiv.org/abs/2512.02261 | 已 fetch |
| 33 | InvestorBench (2412.18174, ACL 2025) | Li 等 | 2024-12 | https://arxiv.org/abs/2412.18174 | 仅搜索摘要 |
| 34 | FinMem (2311.13743, AAAI-SS) | Yu 等 | 2023-11 | https://arxiv.org/abs/2311.13743 | 仅搜索摘要 |
| 35 | FinAgent (2402.18485) | Zhang, Zhao 等 | 2024-02/2024-06 | https://arxiv.org/abs/2402.18485 | 已 fetch |
| 36 | FinCon (2407.06567, NeurIPS 2024) | Yu, Yao 等 | 2024-07/2024-11 | https://arxiv.org/abs/2407.06567 | 已 fetch |
| 37 | StockAgent (2407.18957, v5) | Zhang, Liu 等 | 2024-07/2026-06-23 | https://arxiv.org/abs/2407.18957 | 已 fetch |
| 38 | HedgeAgents (2502.13165, WWW 2025 oral) | Li, Zeng 等 | 2025-02-17 | https://arxiv.org/abs/2502.13165 | 已 fetch |
| 39 | AlphaAgents (2508.11152) | BlackRock | 2025-08-15 | https://arxiv.org/abs/2508.11152 | 已 fetch(abs+html 全文) |
| 40 | FinRobot 平台 (2405.14767) / FinRobot 股票研报 agent (2411.08804) | AI4Finance | 2024-05 / 2024-11 | https://arxiv.org/abs/2411.08804 · https://ai4finance.org/research | 平台页已 fetch;2411.08804 仅搜索摘要 |
| 41 | Fin-R1 (2503.16252) | 上财系 | 2025-03-20 | https://arxiv.org/abs/2503.16252 | 仅搜索摘要 |
| 42 | FLAG-Trader (2502.11433, ACL 2025 findings) | — | 2025-02 | https://arxiv.org/abs/2502.11433 | 仅搜索摘要 |
| 43 | FinRL-DeepSeek (2502.07393) | benstaf/FinRL 社区 | 2025-02 | https://arxiv.org/abs/2502.07393 | 仅搜索摘要 |
| 44 | TwinMarket (2502.01506, NeurIPS 2025;ICLR 2025 Financial AI 最佳论文) | FreedomIntelligence | 2025-02 | https://arxiv.org/abs/2502.01506 | 仅搜索摘要 |
| 45 | Stop Overvaluing Multi-Agent Debate (2502.08788) | Zhang, Cui 等 | 2025-02-12/06-21 | https://arxiv.org/abs/2502.08788 | 已 fetch |
| 46 | Can LLM Agents Really Debate? (2511.07784) | Wu, Li, Li | 2025-11-11 | https://arxiv.org/abs/2511.07784 | 已 fetch |
| 47 | The Cost of Consensus (2605.00914) | Bertalanič & Fortuna | 2026-04-29 | https://arxiv.org/abs/2605.00914 | 已 fetch |
| 48 | Toward Expert Investment Teams: Fine-Grained Trading Tasks (2602.23330) | Miyazaki, Kawahara, Roberts, Zohren(Oxford 系) | 2026-02-26 | https://arxiv.org/abs/2602.23330 | 已 fetch |
| 49 | Assessing the Benefits of Optimized Agentic AI Systems for Asset Pricing (NBER w35431 / SSRN 6474601) | Koijen (Chicago) & Levy | 2026-06/07 | https://www.nber.org/papers/w35431 | 已 fetch(NBER 页;SSRN 403) |
| 50 | Do Sell-side Analyst Reports Have Investment Value? (2502.20489) | Linying Lv | 2025-02-27/08-29 | https://arxiv.org/abs/2502.20489 | 已 fetch |
| 51 | Autonomous Market Intelligence: Agentic AI Nowcasting Predicts Stock Returns (2601.11958) | Chen & Pu | 2026-01-17 | https://arxiv.org/abs/2601.11958 | 已 fetch |
| 52 | FinCall-Surprise (2510.03965) | Shu, Liu, Zhang, Du | 2025-10-04 | https://arxiv.org/abs/2510.03965 | 已 fetch |
| 53 | Large Language Models and Return Prediction in China (SSRN 4712248) | Tan, Wu, Zhang(清华五道口系) | 2023-11 首发 | https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4712248 | 仅搜索摘要(SSRN 403) |
| 54 | Do LLM-based overnight news indicators add value beyond A50 futures?(Finance Research Letters) | — | 2026 | https://www.sciencedirect.com/science/article/abs/pii/S1544612326008950 | 仅搜索摘要(403) |
| 55 | Quantitative Factors + LLM Newsflow Fusion (2510.15691) | Guo & Hauptmann(RAM Active 系) | 2025-10-17 | https://arxiv.org/abs/2510.15691 | 已 fetch |
| 56 | Approaching Human-Level Forecasting with Language Models (2402.18563) | Halawi, Zhang, Chen, Steinhardt (UC Berkeley) | 2024-02-28 | https://arxiv.org/abs/2402.18563 | 已 fetch(abs+html 全文) |
| 57 | Evaluating LLMs on Real-World Forecasting Against Expert Forecasters (2507.04562) | Janna Lu | 2025-07-06/08-04 | https://arxiv.org/abs/2507.04562 | 已 fetch(html v3) |
| 58 | ForecastBench(ICLR 2025)+ 2026-07 平价公告 | Forecasting Research Institute 等 | 2024~2026-07-16 | https://www.forecastbench.org/ · https://forecastingresearch.substack.com/p/ai-models-have-likely-reached-parity | 公告已 fetch;ICLR 论文仅搜索摘要 |
| 59 | LLM-as-a-Prophet: Prophet Arena (2510.17638) | Yang, Mahns 等(UChicago 系) | 2025-10-20 | https://arxiv.org/abs/2510.17638 | 已 fetch |
| 60 | What LLM Forecasters Know but Don't Say (2607.08046) | Sarfati 等 | 2026-07-09 | https://arxiv.org/abs/2607.08046 | 已 fetch |
| 61 | Forecasting Ability of LLMs Depends on What We're Asking (2511.18394) | Karkar & Chopra | 2025-11-23 | https://arxiv.org/abs/2511.18394 | 已 fetch |
| 62 | The Structured Output Benchmark (2604.25359) | Singh, Khurdula 等 | 2026-04-28 | https://arxiv.org/abs/2604.25359 | 已 fetch |
| 63 | Anthropic 官方文档:Prompt caching / Structured outputs | Anthropic | 2026 现行 | https://platform.claude.com/docs/en/build-with-claude/prompt-caching · .../structured-outputs | 已 fetch(两页) |
| 64 | Doomed from the Start: Early Abort via Recall-Controlled Probe Cascade (2607.06503) | Ruan 等 | 2026-07-07 | https://arxiv.org/abs/2607.06503 | 已 fetch |
| 65 | Scores Are Not Decisions: CAM-DF (2607.27083) | Feng 等 | 2026-07-29 | https://arxiv.org/abs/2607.27083 | 已 fetch |
| 66 | When Does Learning to Stop Help? (2606.30852) | Dong, Qin, Shah | 2026-06-29 | https://arxiv.org/abs/2606.30852 | 已 fetch |
| 67 | Inside Hebbia's "Deeper" Research Agent | Hebbia | 2025-07-30 | https://www.hebbia.com/blog/inside-hebbias-deeper-research-agent | 已 fetch |
| 68 | How We Built AI Citations That Actually Work / 工程博客索引 | Brightwave | 2026-03-18 / 2026-06 | https://www.brightwave.io/blog/citations · https://www.brightwave.io/engineering | 已 fetch(两页) |
| 69 | Felix Is a Harness, Not a Model(Rogo)/ Rogo×E2B 案例 | The AI Runtime / E2B | 2026-05-01 / 2026-06-12 | https://theairuntime.com/p/felix-is-a-harness-not-a-model-how · https://e2b.dev/blog/rogo | 已 fetch(两页);Gemini 幻觉率 34.1%→3.9% 出自 Google Cloud 案例页,仅搜索摘要 |
| 70 | AlphaSense Launches Deep Research(新闻稿) | AlphaSense | 2025-06-10 | https://www.alpha-sense.com/press/alphasense-launches-deep-research-automating-in-depth-analysis-with-agentic-ai-on-high-value-content | 已 fetch |
| 71 | OpenAI Introducing deep research | OpenAI | 2025-02-02 | https://openai.com/index/introducing-deep-research/ | 仅搜索摘要(官网 403;多源一致) |
| 72 | Gemini Deep Research agent 文档 | Google | 2026-08-26 更新 | https://ai.google.dev/gemini-api/docs/deep-research | 已 fetch |
| 73 | Perplexity Finance 生态(功能沿革) | Perplexity / 第三方 | 2024-10~2026 | https://www.perplexity.ai/finance | 仅搜索摘要 |
| 74 | Evaluation & Benchmarking Suite for FinLLMs and Agents (2602.19073) | SecureFinAI Lab / Linux Foundation 等 | 2026-02-22 | https://arxiv.org/abs/2602.19073 | 已 fetch |
| 75 | Alpha-R1 (2512.23515) | FinStep-AI 系 | 2025-12-29 | https://arxiv.org/abs/2512.23515 | 已 fetch |
| 76 | 其余仅搜索级线索:QuantAgents(2510.04643)、AlphaCrafter(2605.05580)、QuantaAlpha(2602.07085)、Signal or Noise in Multi-Agent LLM Stock Recommendations?(2604.17327)、SHARP(2605.06822)、FinAgentBench(2508.14052)、LLM Agent in Financial Trading Survey(2408.06361) | — | 2024~2026 | 见各 arXiv 编号 | 仅搜索摘要,内容未核实 |

> 防伪声明:表中所有 [已 fetch] 条目均在 2026-08-30/31 打开过对应 URL 并核对了标题/作者/日期/关键数字;[仅搜索摘要] 条目的数字(如 Tan et al. 35~67% 年化、Rogo 幻觉率、Deep Research Max 93.3%)未经原文核实,引用请降级处理。未发现任何需要标「可能不存在」的条目;BizFinBench v2 的 2026-09-01 日期晚于调研日,以 repo 自述为准。

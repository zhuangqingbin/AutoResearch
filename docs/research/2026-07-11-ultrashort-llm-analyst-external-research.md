# 外部调研报告：超短周期A股选股 × LLM-as-analyst 设计（2026-07-11）

调研方法：18 次 WebSearch + 3 次原文抽取（TradingAgents 原论文 HTML、LLM 股票评级论文摘要页、Blitz 短信号论文检索确认）。

**证据标注**：【学术-多篇】独立学术文献互证 /【学术】单篇或少数学术来源 /【实务共识】多个实务来源一致但无顶刊背书 /【单一来源】需复核

---

# 主题A：超短周期（1-3日）A股选股的已知有效方法/因子

**A1. 短期反转是A股最强日频截面异象，但 lag 结构特殊：基于"最近1日"反而是动量，反转利润由更早几日（约一周前）贡献**——召回线的 reversal 通道应拆 day-1 动量 + day-2~5 反转，而非单一窗口反转分。来源：[FRL: Is short-term reversal driven by liquidity provision? Evidence from China](https://www.sciencedirect.com/science/article/pii/S1544612322004251)（中国反转模式与美股流动性提供理论不符）、[IRFA: 五因子+STR](https://www.sciencedirect.com/science/article/abs/pii/S1057521922001120)、[IREF: Only strong short-term contrarian effect exists…role of T+1](https://www.sciencedirect.com/science/article/abs/pii/S1059056024006452)。【学术-多篇】

**A2. 隔夜与日内必须拆开看：A股异象溢价集中在隔夜段，T+1 制度造成系统性负隔夜收益（开盘流动性折价）**——T+2 open-to-close 尺子恰好绕开了买入侧隔夜折价（open 为可成交价），这是尺子的隐含优点，应保持；同时候选票的历史隔夜收益结构本身可做特征（负隔夜集中于彩票股）。来源：[The overnight return puzzle and the T+1 trading rule](https://www.sciencedirect.com/science/article/abs/pii/S1386418120300033)、[Overnight versus intraday returns of anomalies in China](https://www.sciencedirect.com/science/article/abs/pii/S0927538X23000732)、[T+1 causes negative overnight return](https://www.sciencedirect.com/science/article/abs/pii/S0264999319307023)。【学术-多篇】

**A3. 隔夜信息的可预测性不对称：负向冲击的后续预测力强于正向（市场对隔夜信息反应不足、对日内信息过度反应）**——排除/卖出侧信号在 T+1~T+2 尺度天然比买入侧可靠，风险门优先级应高于机会门。来源：同 A2 文献群 + [Day-night anomaly returns in China: the role of institutions](https://www.sciencedirect.com/science/article/abs/pii/S0275531925000327)。【学术】

**A4. 涨停后次日有正的 close-to-open 溢价（聚合统计约 +2.4%）、次日续涨概率大于反转，但账户级数据证明大资金在涨停日买入、次日卖出"骑泡沫"，其涨停日净买越强、长线反转越强**——1-2日持仓正好落在这个博弈的兑现窗口：涨停后效应在 T+1 有正期望，但必须区分"接力"与"接大资金派发"（涨停日换手/量能结构、买方席位是条件变量）。来源：[Chen, Gao, He, Jiang, Xiong: Daily Price Limits and Destructive Market Behavior, J. Econometrics 2019](https://www.sciencedirect.com/science/article/abs/pii/S0304407618301799)（深交所账户级，[NBER w24014](https://www.nber.org/papers/w24014)）、[Pre-hit dynamics of price limit hits](https://pmc.ncbi.nlm.nih.gov/articles/PMC4395215/)、[+2.44% 数字出自聚合页引述](https://grokipedia.com/page/Limit-up_Chinese_stock_market)（该数字建议自查湖内数据复核）。【学术-顶刊 + 数字为单一来源】

**A5. 连板高度越高次日胜率越差；首板/低位换手板的次日盈利概率最高，"三板成妖"是极端右尾不是期望**——若接入涨停后效应，只做低位首板/二板层，高度≥3 默认排除。来源：[量化统计1.5万条涨停数据](https://zhuanlan.zhihu.com/p/6213610494)、[换手板/撬板量化研究](https://www.cnblogs.com/sljsz/p/15969026.html)、[打板策略收益风险比](https://blog.csdn.net/liuyun12139/article/details/147920429)。【实务共识（散量化多来源一致，无学术背书）】

**A6. 炸板（涨停回封失败）是强负信号：炸板股次日普遍低开，高位炸板次日 -15~-20% 大面常见；全市场炸板率逼近/超过 30% 标志资金严重分歧**——"当日炸板"应做 L4 决策卡的一票否决 tripwire；全市场炸板率应进情绪仪表。来源：[昨日涨停/炸板表现指标解析](https://zhuanlan.zhihu.com/p/78724117)、[炸板次日走势讨论](https://www.zhihu.com/question/571616774)、[炸板率解读](https://stock.hexun.com/2024-10-03/214824240.html)。【实务共识】

**A7. 游资情绪周期有结构化仪表且全部确定性可算：最高连板高度、连板家数（>15≈高潮、≤10≈启动）、炸板率、涨停/跌停家数、昨日涨停股今日溢价（赚钱效应）、大长腿修复**——这些指标可零 token 做成"情绪温度计"注入 regime 层（在 risk_off/range/trend 之上加一维情绪相位：启动-发酵-高潮-退潮-冰点），高潮后退潮期与冰点后修复期的买入期望完全不同。来源：[情绪周期与狙击点](https://zhuanlan.zhihu.com/p/492992454)、[龙头战法就是做情绪周期](https://www.goodgupiao.com/article/chaogujingyan/info-10411.html)、[最高连板数和昨日连板指数](https://www.xiarj.com/26216.html)、[韭研公社龙头与情绪周期](https://www.jiuyangongshe.com/a/7378c93cec674631bebb719bfb63b6c7)。【实务共识（方法论多来源高度一致）】

**A8. 龙虎榜席位预测力有限且衰减：知名游资席位净买对次日冲高有确定性影响，但顶级游资短中期胜率普遍不过半，个别席位短窗胜率 70% 后也回落到 50% 以下**——席位信号维持 presence-gated advisory（系统现状）是对的，不应升格为 alpha 因子；机构席位与游资席位必须分开记账（持仓周期与含义相反），且 A4 的学术结论提示"游资席位大买涨停票"长线是反指。来源：[证券时报：顶级游资胜率统计](https://www.stcn.com/article/detail/3573512.html)、[财联社龙虎榜使用方法](https://www.cls.cn/detail/251144)。【实务统计（单一媒体统计 + 学术侧 A4 佐证）】

**A9. 散户融资盘（两融买入）不是 smart money：散户融资交易者整体追涨且收益差，日频融资流对次日收益无稳健正向预测，两融更多是情绪放大器（收益 Granger 导致情绪而非反向）**——系统 rz_buy_intensity 过三门的自证实证应解读为"情绪接力资金代理"而非基本面确认，宜按情绪周期相位条件化，退潮期慎用。来源：[He 等: The Drivers and Implications of Retail Margin Trading](https://zhiguohe.net/wp-content/uploads/2024/06/China_Leverage.pdf)、[CSI300 两融与情绪 Granger 研究](https://www.scirp.org/journal/paperinformation?paperid=91249)。【学术（方向一致，日频口径证据偏弱）】

**A10. 北向资金短窗领先效应存在但不持续、高度依赖信息环境；其真实优势在基本面质量选股（中长线），不在日频择时**——加上 2024-08 起北向盘中实时流向已停止披露（制度事实，数据及时性下降），日频扫描不应把北向流当 T+2 信号，最多做行业配置背景。来源：[IRFA 2026: Northbound flows time-varying dependence](https://www.sciencedirect.com/science/article/abs/pii/S1057521926000827)、[Smart Money or Chasing Stars (IJFE 2024)](https://onlinelibrary.wiley.com/doi/10.1002/ijfe.2751)。【学术-多篇】

**A11. 日度"主力净流入/大单净买"对次日收益预测不显著、滞后订单不平衡甚至负向预测；订单不平衡的正预测力只在分钟级（5-30min 正、60-120min 反转），小票/高换手更强**——把日度大单净流入当正向因子文献不支持，当反向/拥挤指标更合理；系统"主力失真旗"（反号/微量）方向与文献一致，可加"大单净买过热=反转风险"读法。来源：[Order imbalance and stock returns: China (Accounting & Finance 2021)](https://onlinelibrary.wiley.com/doi/10.1111/acfi.12684)、[Do order imbalances predict Chinese stock returns? (PBFJ 2015)](https://www.sciencedirect.com/science/article/abs/pii/S0927538X15300056)。【学术-多篇】

**A12. 彩票偏好/MAX 效应在A股显著：近期有极端单日大涨、高换手、吸睛的票系统性跑输，且该异象主要由隔夜段驱动、散户持股占比高的票更强**——"光有吸睛涨幅=接落刀"与系统已证伪的"光有低位=接刀"互为镜像：追高候选必须要求梯队/情绪相位确认，否则默认负 drift。来源：[Dissecting the lottery-like anomaly: China (Accounting & Finance 2025)](https://onlinelibrary.wiley.com/doi/10.1111/acfi.13354)（[SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4433510)）、[MAX Effect in China A-share](https://www.scirp.org/pdf/tel_2023081014211370.pdf)。【学术-多篇】

**A13. 基本面/质量因子在 <1 月尺度基本无截面 alpha，预测力集中在年级 horizon；短尺度由微观结构与行为因素主导**——CFO/PE/质押门在 T+2 尺度应正式定位为**尾部风险过滤器**（避雷：爆仓螺旋、监管黑天鹅、流动性死亡）而非收益来源，考核口径应为"避免的左尾"（如避免的 -5% 以下日）而非平均收益差——直接回答主题 A 附加问题。来源：[Qian: Information Horizon, Portfolio Turnover, and Optimal Alpha Models (JPM)](http://gyanresearch.wdfiles.com/local--files/alpha/JPM_FA_07_Qian.pdf)、[Cross-Market Alpha: Alpha191 因子 t 值随 horizon 向 1 月单调上升](https://arxiv.org/html/2601.06499)。【学术 + 实务共识】

**A14. 短周期信号可以净赚，但前提是"多信号组合 + 控换手交易规则"：短反转+短动量+分析师修正+短风险+月内季节性的组合，在流动性池用聪明买卖规则后年净 alpha >6%，实施滞后几天仍稳健**——单一短信号裸打过不了成本；漏斗应显式做多通道信号叠加打分而非单通道择优，且"隔一天再执行也不衰减"意味着 L4 深研的一天延迟不毁 alpha。来源：[Blitz et al.: Beyond Fama-French Factors: Alpha from Short-Term Signals, FAJ 2023](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4115411)（[Robeco 解读](https://www.robeco.com/en-int/insights/2022/05/beyond-fama-french-alpha-from-short-term-signals)；全球流动性池，非A股专属）。【学术（FAJ 发表）】

**A15. T+1 + 涨跌停的制度含义要写进决策卡：买入日无法止损（最短被迫持有到 T+1 open）、一字板买不进、跌停卖不出（执行断层），且价格限制有磁吸效应**——决策卡应显式包含"隔夜跳空风险预算"（T+1 无法止损时最大可承受 gap）与"可成交性"判断（一字板/流动性不足票剔除）。来源：[A unique T+1 trading rule in China: Theory and evidence](https://www.researchgate.net/publication/257211645_A_unique_T_1_trading_rule_in_China_Theory_and_evidence)、[VoxChina: Daily Price Limits and the Magnet Effect](https://voxchina.org/show-3-49.html)。【学术 + 制度事实】

---

# 主题B：LLM-as-analyst 的 prompt/context 设计已知结论

**B1. 锚定效应在绝大多数 LLM 上广泛存在：先出现的数字/结论会不成比例地拉动最终判断**——L4 输入包中任何上游倾向性信息（L3 排名、入选理由的方向性措辞）都是锚；系统"只喂描述性地形、不喂方向指令"的不变量有直接文献支持，应扩展到检查入选理由的措辞。来源：[Anchoring Bias in LLMs: An Experimental Study](https://arxiv.org/pdf/2412.06593)（[J Comput Soc Sci 2026](https://ideas.repec.org/a/spr/jcsosc/v9y2026i1d10.1007_s42001-025-00435-2.html)）。【学术】

**B2. 给出他人评级会让 LLM 从众率飙升：GPT-4 不给分析师评级时 herding 分 89.5%，给出后跳到 95.9%**——绝不把任何前置评级/倾向放进 L4 prompt；即便 L3→L4 的"为何进入决选"也应改写成中性特征清单。来源：[Fin-Bias: LLM Decision-Making under Human Bias in Finance](https://arxiv.org/html/2605.09106v1)。【学术（单一 benchmark，数字待复现）】

**B3. Lost in the middle：长 context 中间位置的信息利用率显著劣化，头尾最好——即使是长上下文模型**——slim 包应把一票否决类风险旗与核心资金结构放头/尾，行业背景类放中间；L3 一次读 200 只候选表尤其要防"中段票被系统性略读"（分块处理或关键列重申）。来源：[Liu et al.: Lost in the Middle, TACL 2024](https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00638/119630/Lost-in-the-Middle-How-Language-Models-Use-Long)（[arXiv 2307.03172](https://arxiv.org/abs/2307.03172)）。【学术-多篇复现，共识级】

**B4. LLM 的口头置信度系统性过度自信（训练目标奖励流利的确定性，"用流畅散文掩盖无知"），金融域校准失败尤其危险**——LLM 自报 conviction 不能当概率用，必须过外部校准环；系统已实测"trend lane 高确信被翻案 33%"正是文献预测的现象，应把频率主义证据（历史三门通过率）的权重置于自报确信之上。来源：[Evaluating LLMs in Finance Requires Explicit Bias Consideration](https://arxiv.org/html/2602.14233v1)、[Are LLMs Rational Investors?](https://arxiv.org/pdf/2402.12713)。【学术】

**B5. 多agent辩论在事实性/推理上普遍优于单agent自省；单agent"自己再想想"最不可靠（会强化初始错误，Degeneration of Thought）——但也有对照研究发现辩论并非稳定占优**——高厉害关系节点（买单）用**独立**对手方审视有文献支持，用同一 agent 自查基本无效；辩论的边际收益在低分歧场景会消失（这与已回测证实的"L3.5 中间 band 是噪声"一致）。来源：[Du et al.: Multiagent Debate Improves LLM Factuality](https://www.emergentmind.com/papers/2305.14325)、[Can LLM Agents Really Debate? A Controlled Study](https://arxiv.org/html/2511.07784v1)（反例证据）。【学术（正反证据并存，方向偏正）】

**B6. 强制反方论证有效且形态重要：引入一个持异议的独立意见显著降低 sycophancy 并提高准确率；反方必须被要求"找出毁灭性论据"而非"检查一下"，且反方不应拥有最终裁决权**——opp 红队"从 bull 方反挖出致命担保"的形态正确；红队产出保持 advisory 证据清单、由 PM 透镜裁决（系统现状）与文献一致。来源：[Challenging the Evaluator: LLM Sycophancy Under User Rebuttal](https://arxiv.org/html/2509.16533v1)、[LLM-Powered Devil's Advocate for Group Decision Making](https://www.researchgate.net/publication/379615420_Enhancing_AI-Assisted_Group_Decision_Making_through_LLM-Powered_Devil's_Advocate)、[Confirmation Bias as a Cognitive Resource in LLM-Supported Deliberation](https://arxiv.org/html/2509.14824)。【学术】

**B7. 格式约束会伤推理：JSON-mode 下数学/多跳推理任务性能掉 10-15%，"先自由推理、最后转结构"可避免；分类型任务反而受益于严格格式**——决策卡应"证据段+推理段自由写，评级与机器可读块放最后填"（evidence-before-verdict 的格式学基础）；注意 Outlines 团队反驳指出退化部分来自"立即作答"式提示而非格式本身——所以关键是**别让第一个生成 token 是结论**。来源：[Let Me Speak Freely? (arXiv 2408.02442)](https://arxiv.org/pdf/2408.02442)、[Dylan Castillo 复现与辨析](https://dylancastillo.co/posts/say-what-you-mean-sometimes.html)。【学术（有争议，"推理先行"方向稳）】

**B8. 结构化 rubric 逐项评分显著压低 judge 偏差：逐条 criteria 打分再聚合优于整体印象分（verbosity 相关性从 0.376 降至 0.291）；位置偏差的标准缓解=随机化呈现顺序/双序平均**——rubric_rating 派生评级（系统现状）有依据；L3 精排 200 只候选表应随机化或按无信息键排序，防"表头位置被高估"这一免费 bug。来源：[Reliability without Validity: Large-Scale Evaluation of LLM-as-a-Judge](https://arxiv.org/html/2606.19544)、[Position Bias in LLM Judges: Measurement and Mitigation](https://mbrenndoerfer.com/writing/position-bias-in-llm-judges)。【学术共识级】

**B9. LLM 评分有中枢化/宽容化倾向：比人类评分更集中、方差更小、更宽容（ordinal 评分中枢偏置有跨域证据）**——五档评级不能靠 LLM 自由裁量给档，要用硬门槛定义档位边界（OW=必须满足可验证条件 X/Y/Z）对抗"全 Hold 漂移"；系统用 binding gates 压评级的做法优于自由评级，文献支持将其保持为一等公民。来源：[Auditing MLLM Raters: Central Tendency Bias in Clinical Ordinal Scoring](https://arxiv.org/pdf/2605.16386)、[AI-Driven Review Systems（LLM 评分更高更集中）](https://arxiv.org/pdf/2408.10365)。【学术（跨域证据，金融域为推断）】

**B10. LLM 预测的最优配方已收敛为四件套：证据质量优先的检索 → 多个独立 run 集成（中位数） → supervisor 调和分歧 → 统计校准/基率注入；此配置在 ForecastBench 上与人类 superforecaster 统计不可区分（单模型仍差约 20% Brier）**——对关键买单可用"3 个独立短 run 取中位"替代单个长 run；把历史基率（"过三门票的 T+2 胜率 X%、OW 卡历史胜率 Y%"）作为显式锚注入 prompt。来源：[AIA Forecaster Technical Report](https://arxiv.org/html/2511.07678v1)、[Wisdom of the Silicon Crowd (PNAS Nexus)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11800985/)、[Good Judgment: Human vs AI Forecasts](https://goodjudgment.com/human-vs-ai-forecasts/)。【学术】

**B11. 金融 LLM agent 框架（FinMem/FinAgent/TradingAgents 类）的回测数字不可作为设计依据：只在个别票上稳定跑赢、高波动大回撤、回测窗短且长期跑不赢市场的证据在积累**——从这些框架借**架构构件**（分层记忆、反思模块、结构化通信协议）而非收益结论。来源：[Can LLM-based Financial Investing Strategies Outperform the Market in Long Run?](https://arxiv.org/html/2505.07078v2)、[InvestorBench](https://arxiv.org/html/2412.18174v1)、[FinMem](https://arxiv.org/pdf/2311.13743)。【学术】

**B12. TradingAgents 论文本身最有用的教训是通信协议而非辩论：纯自然语言多agent对话产生"电话效应"信息劣化，解法=分析报告结构化落全局状态、agent 直查状态而非从消息历史提取，辩论只限于研究员/风控段且结果落结构化条目；注意其 bull/bear 辩论有效性没有消融实验（5 个月回测/3 只大盘科技股/无交易成本/无敏感性分析）**——本项目"确定性 staging 文件 + agent 只读文件"的架构正是该教训的实现，应保持；不要因原框架有辩论而认为辩论本身被验证过。来源：[TradingAgents (arXiv 2412.20138)](https://arxiv.org/html/2412.20138v1)（原文抽取确认）。【学术（单一来源，含明确局限）】

**B13. 数据消融结论（直接对口单票评级）：基本面数据对 LLM 评级质量贡献最大；新闻全文摘要换成情感分/净分不掉性能省 token；有时完全去掉新闻反而更好（减少叙事偏置）**——L4 slim 包的新闻段可压成"事件一行+净分"；对短周期决策，警惕长新闻原文诱发叙事性确认偏误。来源：[AI in Investment Analysis: LLMs for Equity Stock Ratings (arXiv 2411.00856)](https://arxiv.org/pdf/2411.00856)（GPT-4-32k，2022-2024，forward returns 评估，摘要页确认）。【学术（单一来源但任务同构）】

**B14. 结构化输出内部的字段顺序也载有因果：结论字段排在前面会锁死后续"推理"为事后合理化**——机器可读块中 rating/conviction 字段必须排在证据字段之后（与 B7 同源但独立可查）。来源：[Order of fields in structured output can hurt LLMs](https://www.dsdev.in/order-of-fields-in-structured-output-can-hurt-llms-output)。【实务共识（博客级，与 B7 学术结论同向）】

**B15. LLM 金融评估的偏差清单应作为 self_review lint 的检查维度：金融域文献总结的五类系统性偏差=前视偏差、幸存者偏差、叙事偏差、目标偏差、成本偏差，外加行为五件套（过度自信/损失厌恶/从众/锚定/确认）**——可把"卡片是否引用了 as-of 日期之后的信息（前视）""是否只引用支持性证据（确认）"做成决策卡的机检 lint 项。来源：[Evaluating LLMs in Finance Requires Explicit Bias Consideration](https://arxiv.org/html/2602.14233v1)、[Fin-Bias](https://arxiv.org/html/2605.09106v1)。【学术】

---

## 与系统现状的三点交叉印证

1. **系统已自证的读数与文献互证**：momentum 用 T+1 尺子召回错配（A1 的 lag 结构）、"光有低位=接刀"（A12 镜像）、trend 高确信翻案 33%（B4）、删买单 skeptic 但保 Tier-3 辩论的边界（B5/B6：独立对手方有效、自省无效、低分歧场景辩论无增益）——外部证据基本站在系统已做裁决的一边。
2. **最便宜的三个未做动作**：情绪周期温度计（A7，全确定性零 token）、L3 候选表顺序随机化（B8，防位置偏差）、决策卡机器块字段重排+基率注入（B7/B10/B14）。
3. **质量门考核口径要改**：A13 明确支持"CFO/PE/质押门在 T+2 尺度只应考核左尾避免量，不应考核平均收益"——这能终结"质量门在超短尺度有没有用"的争论方式。

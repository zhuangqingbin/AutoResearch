# 日频小样本 LLM/量化选股系统自学习机制——外部调研报告

调研目的:为已有闭环骨架(retro 归因 / 教训库 MTM / 提案人批 / 权重再校准 / 影子对照)的日频小样本(10-30 决策、0-3 笔交易/日)系统找增量方向。共执行 24 次 WebSearch,覆盖 6 个问题。证据强度标注:【学术-多篇】>【学术】>【实务共识】>【单一来源】。

## 1. LLM agent 反思/记忆机制

正面证据:Reflexion 把环境反馈转成文字反思存入情景记忆缓冲区,免微调即可跨试次改进,在代码生成等任务上达到 SOTA【学术】;Voyager 把经验存成可执行、可组合的"技能库"代码而非自由文本教训,原生抗灾难性遗忘【学术】;Devil's Advocate 用"预判失败→行动中纠偏→事后复盘"三段式反思,减少 45% 试错轮次【学术】;金融领域 FinMem/FinAgent 用浅/中/深三层记忆按不同衰减率分层,自称跑赢多基准,但收益数字系自评,需与下条对照【学术,单系统自证需谨慎】。

反面证据(重点):持续更新的文本记忆会自我强化错误信念、过度泛化、错配情景归类——agent 在"用来生成该记忆的那批问题"上反而变差【学术】;交易领域专门实验(ATLAS)发现反思对能力较弱的模型有害,会"自信地误读市场信号",关闭反思反而提升表现【学术,交易场景专证】;"反思缺口"研究显示即便给出具体环境反馈,agent 仍系统性误判自己动作的好坏【学术】;更根本的是,LLM 交易 agent 样本外 Sharpe 普遍衰减 51%-62%,164 篇相关论文中没有一种偏差被超过 28% 的研究讨论过——"反思学到了东西"这一自我报告本身可能是信息泄漏的幻觉【学术-多篇】。

对本系统:教训库做 MTM(用后续证据 support/refute)、要求教训引用具体触发事件而非笼统总结,方向正确;应continue坚持"教训→规则/技能化条目"而非"教训→自由文本印象"路线,且弱模型/低置信场景下反思本身可能是噪声源,需要监控其边际收益而非默认有益。

出处:[Reflexion](https://arxiv.org/abs/2303.11366) · [Voyager](https://arxiv.org/abs/2305.16291) · [Devil's Advocate](https://arxiv.org/abs/2405.16334) · [FinMem](https://arxiv.org/abs/2311.13743) · [Useful Memories Become Faulty](https://arxiv.org/pdf/2605.12978) · [ATLAS](https://arxiv.org/html/2510.15949v1) · [Closing the Reflection Gap](https://arxiv.org/pdf/2606.14211) · [Profit Mirage](https://arxiv.org/html/2510.07920v1) · [Alpha Illusion](https://arxiv.org/html/2605.16895)

## 2. 小样本下的校准与基率方法

收缩估计是统计学对"n 太小"最成熟的对策:James-Stein 证明只要同时估计 ≥3 个组,向共同目标收缩的估计量均方误差必然不劣于各组独立估计【学术-多篇】;金融里的具体形态是 Bayes-Stein/Black-Litterman——把样本均值收益向全局均值或市场均衡先验收缩,收缩强度随样本量自动减弱,小样本下明显更稳健【学术-多篇】。预测模型文献给出可操作的最小样本惯例:每个预测变量至少 10-20 个"事件"(EPP 法则),即便有 200 个事件,校准斜率的估计仍不够精确,不能只看总 n 不看事件数【学术】。分层贝叶斯/部分池化(partial pooling)把两者结合:每桶估计值 = 自身数据与全局均值的加权混合,桶内 n 越小权重越偏向全局均值,n 越大自动"解放"给自身数据,天然避免人工设一刀切阈值【学术-多篇】。

对本系统:按评级档/regime 分桶的胜率不应等 n 够了才启用,而应从第一天起用 shrinkage 加权(向上一级 regime 或全局基率收缩),权重由桶内 n 自动决定;现有 cap-floor/l2-n 等硬阈值可保留作"最低限速",但桶内统计口径应默认 partial pooling 而非独立频率估计。

出处:[Hierarchical/partial pooling notes](https://jrnold.github.io/bayesian_notes/shrinkage-and-hierarchical-models.html) · [rstanarm pooling vignette](https://cran.r-project.org/web/packages/rstanarm/vignettes/pooling.html) · [Black-Litterman as Bayesian shrinkage](https://arxiv.org/abs/2308.09264) · [James-Stein in finance](https://web-docs.stern.nyu.edu/old_web/emplibrary/shrink3.pdf) · [Minimum sample size for prediction models](https://pmc.ncbi.nlm.nih.gov/articles/PMC6519266/)

## 3. 人类预测精英的复盘法

Tetlock 的 superforecaster 核心行为是"记分+复盘":用 Brier 分数量化每次预测准确度,靠决策日志逐条追溯误差来源,并"频繁更新但不过度更新"、做颗粒化(granular)而非粗档的贝叶斯式概率调整【学术+实务共识】。GJP 聚合算法额外做"极端化"(extremizing)——按历史准确率和更新频率加权后把群体概率推向更自信一端,但至少一位研究者认为该手法当年成功可能只是巧合,证据强度打折【学术,有争议】。另一条独立但高度契合的框架是 Annie Duke 的 "resulting" 批判:决策质量必须与结果质量分开打分,只有在足够大样本下技能才会压过运气,单次样本里两者关系很松【实务共识】。Bridgewater 的 Issue Log 是把这套复盘制度化的机构案例:任何失误都要记录严重度和责任人,系统化分析后再产出改进项【单一机构案例,广泛报道】。

对本系统:五档评级卡+复盘天然对应"决策日志",但应补一个 Tetlock/Duke 都强调的分离动作——对每张卡先打"过程质量分"(证据是否引用充分、是否符合当日 regime 校准)再打"结果分"(T+2 涨跌),两者独立记账,使 0-3 笔成交也能持续复盘过程分,不被结果分的小样本噪声绑架。

出处:[Good Judgment Project evidence](https://aiimpacts.org/evidence-on-good-forecasting-practices-from-the-good-judgment-project/) · [Ten Commandments for Superforecasters](https://fs.blog/ten-commandments-for-superforecasters/) · [Superforecasting reality check](https://pmc.ncbi.nlm.nih.gov/articles/PMC7333631/) · [Thinking in Bets notes](https://grahammann.net/book-notes/thinking-in-bets-annie-duke/) · [Bridgewater Issue Log](https://thehedgefundjournal.com/50-giants-bridgewaters-ray-dalio/)

## 4. 量化机构的研究闭环

Champion/Challenger 是最通用的生产范式:挑战者模型必须在与冠军相同的 holdout 集上显著胜出才能顶替,否则冠军留任【实务共识】。Walk-forward optimization 用滚动窗口反复"训练一段+验证紧邻下一段",自带一条过拟合警报——若重新优化后的参数表现不如原始未优化版本,即为曲线拟合信号而非该继续调参的信号【实务共识】。但凡涉及"试了不止一版参数/权重",Deflated Sharpe Ratio 提醒必须为多重检验做统计修正,因为测试的变体越多,纯靠运气也会有一版看起来很好,这已是量化文献里接近教科书地位的修正方法【学术,广泛引用】。样本量惯例上,实务界底线是 30 笔交易起做统计推断,100-200 笔才谈得上可靠,但反复强调"regime 多样性比笔数更重要"——跨牛熊 15 年的 80 笔比单边牛市的 150 笔更可信【实务共识】;头部量化基金因此要求信号在 5-10 年、多个明显不同 regime 上都跑通样本外表现,而非只看聚合 n【单一来源,具体年限需谨慎】。纸面账户/孵化期是策略转正前的标准关卡,与 Champion/Challenger 配合使用。

对本系统:现有影子 NAV + 提案人批已经是 Champion/Challenger + 纸面账户的正确形状;缺的一环是把"改了几次权重"计入多重检验修正——同一窗口内若对同一参数尝试了 N 版校准,批准阈值应按 N 收紧,而非就着最近一次看起来最好的版本裁决。

出处:[Champion-Challenger](https://www.wallstreetmojo.com/champion-challenger-model/) · [Walk-Forward Optimization](https://blog.quantinsti.com/walk-forward-optimization-introduction/) · [Deflated Sharpe Ratio (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) · [Minimum trades for statistical significance](https://medium.com/@trading.dude/how-many-trades-are-enough-a-guide-to-statistical-significance-in-backtesting-093c2eac6f05) · [Quant funds regime validation](https://youngandcalculated.substack.com/p/how-quant-hedge-funds-actually-build)

## 5. 反馈循环的病理

推荐系统文献对"系统只从自己敢做的动作学习"这条病理研究得最系统:系统历史推荐塑造未来训练数据,热门项目越推越热,形成自我放大的选择偏差闭环,标准缓解手段是注入一层均匀随机的"日志策略"打散数据,让离线策略评估有据可依【学术-多篇】。这一点在因果推断上有精确刻画:逆概率加权(IPS)等离线策略评估方法都要求"common support"——日志策略从未或极少采取的动作,理论上无法被无偏评估,不管下游怎么记账都补不回来【学术-多篇】。一篇新论文(PRFS)提出对被拒绝候选做"事后跟踪抽样",在真实市场环境里(而非另建模拟器)持续记录被拒动作的前向结果,以避免模拟器与实盘的系统性差异【单一来源,方法新颖但逻辑站得住】。此外,组合回测本身存在"实现风险"——换一个回测引擎跑同一策略同一数据,结果就会分化,目前无研究系统比较过这种引擎间分歧【学术,单一来源但问题具共识性】。

对本系统:现有影子/反事实记账的结构性盲区在漏斗最前端——凡 L1 就被筛掉、从未进入 L3/L4 的名字,天然零 propensity,不管后面记账多细都无法被去偏。对策是从 L1 淘汰池每天抽一小撮"均匀随机臂"照样跑一遍 lite 卡,专门用于反事实计量,而不是只反事实记账"差点选中"的边缘案例。

出处:[Feedback Loop Bias Amplification](https://ar5iv.labs.arxiv.org/html/2007.13019) · [Correcting Feedback-Loop Bias](https://arxiv.org/pdf/2109.06037) · [IPS common support survey](https://arxiv.org/pdf/1703.06180) · [PRFS](https://arxiv.org/pdf/2606.08228) · [Implementation Risk in Portfolio Backtesting](https://arxiv.org/pdf/2603.20319)

## 6. LLM 自我改进 prompt 的证据

OPRO/TextGrad/APE 这类"LLM 当优化器"的自动 prompt 优化在窄基准任务上确有效,OPRO 甚至能在部分任务上超过遗传算法基线【学术-多篇】,但代价不小——TextGrad 每一步都要多次调用 LLM 做"文本梯度",生产成本高【学术】。更致命的是两条限制:第一,自动 prompt 优化在小验证集上(n=5、20 量级)验证分数波动极大,容易把噪声当规律优化进去,缓解手段是扩大验证集、显式要求"泛化不要死记"、加早停,而非无脑跑优化循环【学术-多篇】。第二,"自我修正幻觉"研究发现 LLM 能可靠纠正别人生成的内容,但没有外部真值信号时无法可靠纠正自己——内在自我修正经常不但不改善、反而让结果变差【学术,被广泛引用】。

对本系统:n=10-30/天意味着可用于"教训→prompt"回路的验证集天然小于文献里已报告不稳定的量级(n=5-20 都嫌小),而且现有回路的信号来源(retro 归因)本质上是同一套 agent 对自己决策的事后评估,正好撞上"自我修正幻觉"的盲区——缺一个外部/独立的真值校验环节。结论:不建议把"教训→prompt 注入"升级成自动改写;若要引入 OPRO 式重写,应把重写结果当作一版需要过完整校准/retro 门槛的候选提案,而非自动生效的更新,且验证集应聚合足够多天(而非单日 n)才能过审。

出处:[OPRO](https://arxiv.org/pdf/2309.03409) · [TextGrad](https://arxiv.org/pdf/2406.07496) · [Prompt tuning overfitting small validation](https://arxiv.org/pdf/2211.02219) · [Self-Correction Illusion](https://arxiv.org/pdf/2606.05976)

---

## 弹药清单(C1-C27)

- C1. Reflexion:verbal RL + 情景记忆缓冲区,免微调改进【学术】https://arxiv.org/abs/2303.11366
- C2. Voyager:可执行技能库,组合式存储抗灾难性遗忘【学术】https://arxiv.org/abs/2305.16291
- C3. FinMem:浅/中/深三层记忆按衰减率分层处理金融信息【学术,自证收益需对照C8谨慎】https://arxiv.org/abs/2311.13743
- C4. Devil's Advocate:预判失败→行动中纠偏→事后复盘三段反思,减少45%返工【学术】https://arxiv.org/abs/2405.16334
- C5. 持续更新的文本记忆会自我强化错误信念、过度泛化、错配情景,agent在自己生成记忆的问题集上反而变差【学术】https://arxiv.org/pdf/2605.12978
- C6. 交易场景反思实验(ATLAS):反思对弱模型有害,关闭反思反而提升表现【学术,交易专门证据】https://arxiv.org/html/2510.15949v1
- C7. "反思缺口":agent即便有具体环境反馈仍系统性误判自己动作【学术】https://arxiv.org/pdf/2606.14211
- C8. LLM交易agent样本外Sharpe普遍衰减51%-62%,164篇论文无一种偏差被超28%研究讨论【学术-多篇】https://arxiv.org/html/2510.07920v1 / https://arxiv.org/html/2605.16895
- C9. James-Stein:≥3组同时估计时收缩估计量均方误差必然不劣于独立估计【学术-多篇】https://web-docs.stern.nyu.edu/old_web/emplibrary/shrink3.pdf
- C10. Black-Litterman本质是贝叶斯收缩,把观点向市场均衡先验收缩,小样本更稳健【学术-多篇】https://arxiv.org/abs/2308.09264
- C11. 预测模型最小样本惯例:每预测变量≥10-20事件(EPP法则),200事件校准斜率仍不精确【学术】https://pmc.ncbi.nlm.nih.gov/articles/PMC6519266/
- C12. 分层贝叶斯/部分池化:桶内估计=自身数据与全局均值按n自动加权混合【学术-多篇】https://jrnold.github.io/bayesian_notes/shrinkage-and-hierarchical-models.html
- C13. Superforecaster:Brier分决策日志+颗粒化贝叶斯更新+"更新很多但不过度"【学术+实务共识】https://aiimpacts.org/evidence-on-good-forecasting-practices-from-the-good-judgment-project/
- C14. GJP极端化聚合算法推高群体置信度,但被质疑成功系巧合【学术,有争议】https://aiimpacts.org/evidence-on-good-forecasting-practices-from-the-good-judgment-project/
- C15. Annie Duke "resulting":决策质量应与结果质量分开评分,小样本下二者关系松散【实务共识】https://grahammann.net/book-notes/thinking-in-bets-annie-duke/
- C16. Bridgewater Issue Log:失误必须记录严重度+责任人,系统化分析产出改进【单一机构案例,广泛报道】https://thehedgefundjournal.com/50-giants-bridgewaters-ray-dalio/
- C17. Champion/Challenger:挑战者须在共享holdout显著胜出才能顶替冠军【实务共识】https://www.wallstreetmojo.com/champion-challenger-model/
- C18. Walk-forward优化:重新优化后表现不如原始版本=过拟合红灯【实务共识】https://blog.quantinsti.com/walk-forward-optimization-introduction/
- C19. Deflated Sharpe Ratio:修正多重检验下的选择偏差,试的策略越多越可能纯凭运气看起来好【学术,近教科书地位】https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- C20. 交易策略最小样本惯例:底线30笔/理想100-200笔,但regime多样性比笔数更重要【实务共识】https://medium.com/@trading.dude/how-many-trades-are-enough-a-guide-to-statistical-significance-in-backtesting-093c2eac6f05
- C21. 推荐系统反馈循环:系统只从自己曾推荐内容学习,热者更热,需注入均匀随机日志策略打散【学术-多篇】https://arxiv.org/pdf/2109.06037
- C22. IPS离线策略评估要求"common support":日志策略从未采取的动作无法被无偏评估【学术-多篇】https://arxiv.org/pdf/1703.06180
- C23. PRFS:对被拒绝候选在真实市场环境(非模拟器)做事后跟踪抽样,消除模拟器-实盘gap【单一来源,方法可迁移】https://arxiv.org/pdf/2606.08228
- C24. 组合回测"实现风险":换回测引擎跑同一策略同一数据结果就分化,尚无研究系统比较过【学术,单一来源】https://arxiv.org/pdf/2603.20319
- C25. OPRO/TextGrad:LLM当优化器在窄基准超遗传算法,但TextGrad每步多次调用成本高【学术-多篇】https://arxiv.org/pdf/2309.03409
- C26. 小验证集(n=5/20)下自动prompt优化验证分数高度不稳定,需更大验证集+泛化指令+早停缓解【学术-多篇】https://arxiv.org/pdf/2211.02219
- C27. "自我修正幻觉":LLM能纠正别人却难以在无外部信号下可靠纠正自己【学术,被广泛引用】https://arxiv.org/pdf/2606.05976

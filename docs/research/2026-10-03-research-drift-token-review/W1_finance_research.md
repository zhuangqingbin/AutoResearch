# W1: External evidence for an LLM research layer on an A-share overnight ruler (as of 2026-10-03)

**Tags:** `[FETCHED]` means I opened the abstract, page or PDF in this session. `[SNIPPET]` means I saw only a search-engine summary (the page was not opened, or returned 403), so its numbers need checking before anyone relies on them. `[MEMORY]` means prior knowledge that I could not re-verify in this session. Text under "Evidence" reports what the sources say. Text under "Implications" is my own hypothesis and is not evidence.

**Summary.** (1) Once leakage, costs and longer windows are controlled, end-to-end LLM trading agents do not reliably beat simple baselines, and returns are mostly market and style exposure. That includes a 2024–26 CSI300 study. (2) The strongest positive evidence is narrower. LLMs that read news can predict 1–2-day returns, and one live agentic study found a top-decile effect in US large caps. These signals concentrate in small, retail-heavy, negative-news names and decay as adoption grows. (3) A-share overnight returns are negative on average for a structural reason: a T+1-induced opening discount of about 14 bp. End-of-day price pressure reverting overnight is documented in the US. I found **no peer-reviewed A-share paper** on "close location → next open." (4) With ≤1 trade per day, live P&L cannot validate an edge for years. Statistical power has to come from cross-sectional breadth and proper scoring.

---

## 1. Rigorous evidence on LLM trading/investing agents (2024–2026)

**Evidence**
- **FINSABER.** Li, Kim, Cucuringu, Ma. "Can LLM-based Financial Investing Strategies Outperform the Market in the Long Run?" arXiv 2505.07078, v1 May 2025 to v6 Jun 2026, KDD 2026 D&B. https://arxiv.org/abs/2505.07078 `[FETCHED]`. The authors re-tested timing strategies (FinAgent/FinMem-style) over about 20 years and 100+ symbols. Previously reported LLM advantages "deteriorate significantly." LLM strategies were too conservative in bull markets and too aggressive in bear markets. The authors call for "regime-aware risk controls over mere scaling of framework complexity."
- **StockBench.** Chen et al., arXiv 2510.02209, 2025. https://arxiv.org/abs/2510.02209 `[FETCHED]`. A contamination-free, multi-month benchmark (the window was Mar–Jul 2025 per a `[SNIPPET]`). "Most models struggle to outperform the simple buy-and-hold baseline," and strong static financial-QA scores "do not necessarily translate into effective trading."
- **AI-Trader (HKU).** Fan…Huang, arXiv 2512.10971, Dec 2025. https://arxiv.org/abs/2512.10971 `[FETCHED]`. A live, uncontaminated benchmark across US stocks, **A-shares** and crypto. "General intelligence does not automatically translate to effective trading". Most agents showed poor returns and weak risk management. Risk control determined robustness. Excess returns came "more readily in highly liquid markets than policy-driven environments", i.e., A-shares were harder.
- **KTD-Fin.** Zhu et al., "From Knowing to Doing", arXiv 2605.28359, May 2026. https://arxiv.org/abs/2605.28359 `[FETCHED]`. Ten frontier LLM agents traded **CSI300 over 2024–2026**, with tickers and dates masked and returns decomposed Barra-style. Masking "substantially changes agent rationales." Returns were "largely explained by passive market and style exposure, with limited evidence of persistent stock-selection alpha." This is the closest external analogue to this system.
- **DeepFund.** Li et al., "Time Travel is Cheating", arXiv 2505.11065, 2025. https://arxiv.org/abs/2505.11065 `[SNIPPET]`. In live post-cutoff trading, DeepSeek-V3 and Claude-3.7-Sonnet lost money. Reported figures: DeepSeek-V3 −5.7%, GPT-4.1 −5.9%, Doubao −8.1%, and only Grok-3-mini positive at +1.1%.
- **Agent Market Arena.** arXiv 2510.11695, Oct 2025. https://arxiv.org/abs/2510.11695 `[SNIPPET]`. This is a live crypto and stock arena. **Agent architecture, not the LLM backbone,** was the main driver of outcomes.
- **Production fleets (DXAP / DX Terminal).** Barton et al., arXiv 2609.05663, Sep 2026. https://arxiv.org/abs/2609.05663 `[FETCHED]`. The record covers 7.5M invocations over 6 months. The operating layer, such as the risk slider, determined behavior more than strategy text. Neither fleet had a directional edge (41% vs 50% round-trip win rate against matched retail). A *mechanical* bracket exit recovered +39 bp per position.
- **Survey and critique.** Nguyen & Pham, arXiv 2603.27539, Mar 2026. https://arxiv.org/abs/2603.27539 `[FETCHED]`. They identify five common evaluation failures: look-ahead, survivorship, backtest overfitting, cost neglect and regime blindness. These "can reverse the sign of reported returns." The claim that multi-agent coordination beats model scaling is explicitly **unvalidated**. A separate survey, "Agentic Trading," arXiv 2605.19337 `[SNIPPET]`, asks for role and communication ablations at equal compute.
- **Robustness.** TradeTrap, Yan et al., arXiv 2512.02261. https://arxiv.org/abs/2512.02261 `[FETCHED]`. A small perturbation in one component propagates into concentration, runaway exposure and drawdowns.
- **Alpha Arena S1 (nof1).** Six models each traded $10k of crypto perps on Hyperliquid, Oct 18 – Nov 3 2025. Results: Qwen3-Max +22.3%, DeepSeek-V3.1 +4.9%, Claude Sonnet 4.5 −42%, Gemini 2.5 Pro −45.6%, Grok 4 −57.9%, GPT-5 −58.7%. Sources are secondary (https://www.iweaver.ai/blog/alpha-arena-ai-trading-season-1-results/, SCMP) `[SNIPPET]`. With n=6, 17 days and leverage, this is risk-setting dispersion, not skill evidence.
- **Original frameworks.** TradingAgents (arXiv 2412.20138), FinMem, FinCon and FinAgent reported gains from debate, memory and reflection. Those results come from short, few-ticker backtests `[MEMORY]`. **I found no out-of-sample, leakage-controlled ablation showing that debate, memory or reflection adds return.** One crypto multi-agent paper (arXiv 2501.00826) reports large in-sample ablation deltas, such as −42.6 pp after removing one agent `[SNIPPET]`. That is not credible evidence at that sample size. I could not verify InvestorBench or FinTSB findings in this session.
- **Model size.** It helps headline interpretation (Lopez-Lira & Tang, §3) but matters less than architecture and risk settings for end-to-end trading (AMA, AI-Trader).

**Implications for the system (hypotheses)**
- 外部最严格的证据（FINSABER、StockBench、AI-Trader、KTD-Fin（CSI300）、DXAP 生产记录）口径一致：控制泄漏、成本和窗口后，LLM 端到端交易约等于市场加风格暴露，A 股更难。系统 50 天"各级无选股能力"与外部基准一致，不宜直接解读为实现缺陷。
- 决定结果的是执行层、风控层、退出规则这类确定性部分，而不是研究叙述。DXAP 中机械止盈止损一项就贡献 +39bp/仓。研究精力应向"执行日结构"倾斜，而不是继续加深 L4 卡。
- 辩论、记忆、反思在金融场景没有样本外消融支持。若要保留，必须做同算力消融：同算力下的独立采样加投票对照。

## 2. Look-ahead / memorization bias and how to evaluate

**Evidence**
- **The memorization problem.** Lopez-Lira, Tang, Zhu, arXiv 2504.14765, rev. Dec 2025. https://arxiv.org/abs/2504.14765 `[FETCHED]`. LLMs recall exact economic and financial values before their cutoff. Instructions to respect dates fail, and **masking fails** because models "reconstruct entities and dates from minimal context." There is no recall after the cutoff. Pre-cutoff skill is *non-identified*.
- **Lookahead Propensity (LAP) test.** Gao, Jiang, Yan, arXiv 2512.23847, rev. Jun 2026. https://arxiv.org/abs/2512.23847 `[FETCHED]`. A date-only recall query yields LAP. LAP is positive in-sample and collapses to about 0 after the cutoff. In headline→return and earnings-call→capex tasks, forecast power is amplified on high-LAP firm-dates, and the effect vanishes after the cutoff.
- **Distraction effect.** Glasserman & Lin, arXiv 2309.17322, 2023. https://arxiv.org/abs/2309.17322 `[FETCHED]`. In-sample, **anonymized headlines outperform** raw ones, so "the distraction effect has a greater impact than look-ahead bias". The effect is strongest for large firms. Out of sample, distraction "remains possible."
- **Entity neutering.** Engelberg, Manela, Mullins, Vulicevic, SSRN 5182756, 2025. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5182756 `[SNIPPET]`. LLMs iteratively mask and paraphrase text. Across 250k articles, firm identification falls to chance, sentiment agrees more than 95% of the time, and predictability is similar. The difference bounds the look-ahead bias.
- **Cost of anonymizing.** Wu, Yang, Ying, Zhou, arXiv 2511.15364. https://arxiv.org/abs/2511.15364 `[FETCHED]`. Anonymizing degrades signal by masking informative numbers and entities, and the authors propose an "anonymization gap" metric. Blanket masking therefore has a cost.
- **Chronologically consistent LLMs.** ChronoBERT/ChronoGPT, He, Lv, Manela, Wu, arXiv 2502.21206. https://arxiv.org/abs/2502.21206 `[FETCHED]`. Models are trained only on text available at each date. Their news-return Sharpe is comparable to a larger Llama, so "lookahead bias is modest" in that application, though the bias is model- and task-specific. Time Machine GPT (Drinkall et al., NAACL Findings 2024, arXiv 2404.18543) takes the same approach `[MEMORY]`.
- **Recommended protocol** (synthesis): (i) count as skill evidence only forecasts collected live at "the current edge of time," or post-cutoff samples (DeepFund, StockBench, Chen & Pu §3); (ii) run LAP-style recall diagnostics on any pre-cutoff test; (iii) use neutering or masking as a diagnostic, while measuring information loss; (iv) apply Barra-style attribution (KTD-Fin) so beta and style are not mistaken for selection.

**Implications for the system**
- 系统每晚的实盘记录生成于模型截止之后，当晚检索，天然无前视偏差，是最有价值的证据资产。反过来，对 LLM 层的任何历史回放或补跑（含 web 检索）都会同时受模型记忆和检索泄漏污染，结果不可识别，不能用来评估技能。
- 分心效应可以诊断：抽样把同一张卡用"实体中性化"输入重跑，看评级翻转率。若大公司翻转更多，说明先验知识在主导卡片。这只作诊断用，不进常规流程，因为匿名化有信息损失。

## 3. What LLMs are demonstrably good at in equity research

**Evidence**
- **News headlines → returns.** Lopez-Lira & Tang, arXiv 2304.07619, v6 Oct 2025. https://arxiv.org/abs/2304.07619 `[FETCHED]`. Using post-cutoff headlines, GPT-4 captures the *initial* reaction (about 90% portfolio-day hit rate, "non-tradable") and also predicts the subsequent drift, "especially for small stocks and negative news." Skill rises with model size. **"Strategy returns decline as LLM adoption rises."**
- **China.** Tan, Wu, Zhang, "Large Language Models and Return Prediction in China," 2024, ABFER. https://www.abfer.org/component/edocman/main-webinar-series/large-language-models-and-return-prediction-in-china `[FETCHED]`. They tested BERT, FinBERT, Baichuan, ChatGLM, InternLM and an ensemble on Chinese news. Value-weighted long-short returns were **35–67% p.a.**, and signals are incorporated within about **2 days** (next day plus one). Predictability concentrates in firms with information frictions, **high retail participation** and complex news. Many investors initially trade against the signal. No cost analysis was visible. Other A-share LLM-sentiment papers exist, e.g., arXiv 2306.14222 and a ChatGPT-vs-DeepSeek A-share study `[SNIPPET]`.
- **Live agentic nowcasting** (this contradicts the "no selection skill" framing). Chen & Pu, arXiv 2601.11958, Jan 2026. https://arxiv.org/abs/2601.11958 `[FETCHED]`. An LLM autonomously web-searches and rates each Russell 1000 stock daily from Apr 2025, with forecasts collected live. Going long the **top 20** earned FF5+UMD alpha of **18.4 bp/day**, Sharpe 2.43, with costs under 10% of gross alpha. The effect is "highly concentrated": alpha dilutes quickly beyond the top tier, and **bottom-ranked stocks ≈ market**. Caveats: a short sample of months, one model, US large caps, and a daily holding period rather than close→open.
- **Multilingual news embeddings.** Chen, Kelly, Xiu, "Expected Returns and Large Language Models," SSRN 4416687. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416687 `[MEMORY]`. LLM text embeddings beat simple sentiment across 16 markets, and the predictability is short-lived. I did not re-verify horizon details.
- **Financial statements.** Kim, Muhn, Nikolaev, "Financial Statement Analysis with LLMs," arXiv 2407.17866. https://arxiv.org/abs/2407.17866 `[FETCHED]`. **Withdrawn Feb 2025:** "a co-author identified inconsistencies in the data and analyses." Its claims that GPT-4 beats analysts should not be relied on.
- **Earnings calls and risk extraction.** LLM reads of calls predict capex (Jha, Qian, Weber, Yang, NBER w32161 `[MEMORY]`). LLM-derived risk exposures from calls are informative (Kim, Muhn, Nikolaev, arXiv 2310.17721 `[MEMORY]`). The LAP paper above shows the call→capex task is contaminated in-sample `[FETCHED]`.
- **Horizon summary.** Most LLM-news signal is in the reaction on day 0 and day +1 to +2. It is concentrated in small, retail-heavy, negative-news names and decays with adoption. The agentic long-only effect is daily. I found no study isolating close→next-open as the horizon.

**Implications for the system**
- 文献里 LLM 读新闻的信号主要在 T 日到 T+2 内兑现，大头在发布后的首个交易时段。系统的 T 晚情报，很可能在 T+1 开盘到收盘之间就被价格吸收，等不到"T+1 收盘到 T+2 开盘"。值得做的低成本检验：情报的正负面与 T+1 日内收益（开到收）以及 T+1 开盘缺口的关系，而不是只看隔夜尺。
- "否决在 10 日尺上有价值"与文献中负面信息和风险识别更可靠一致（Lopez-Lira–Tang：负面新闻更可预测）。但 Chen–Pu 显示只有顶部有效、底部无效，说明方向依赖样本与尺度。建议把 LLM 定位为否决和风险过滤器，并按其适配的尺度计分。
- 若保留"选股"职能，外部最佳证据指向"极少数高置信顶部"，而不是对 208 行做全表排序。
- 不要以已撤回的 KMN 财报论文为依据扩大基本面模块。

## 4. Overnight vs intraday returns, especially in A-shares

**Evidence: the baseline**
- **Qiao & Dam (2020).** "The overnight return puzzle and the 'T+1' trading rule in Chinese stock markets," *Journal of Financial Markets* 50:100534. https://doi.org/10.1016/j.finmar.2020.100534 (full PDF: https://pure.rug.nl/ws/files/132141807/1_s2.0_S1386418120300033_main.pdf) `[FETCHED]`.
  - Average overnight returns are **significantly negative for all 8 indices (≈ −6 bp/day)**. The pattern holds across exchanges, share types, bull and bear markets, and size groups, and is unique to China. Overnight returns are *positive* for bonds and futures and during T+0 periods (1993–94, and B-shares Mar–Nov 2001).
  - Mechanism: under T+1, buying at the open forfeits the option to sell intraday, so the open is priced at a discount. The **T+1 discount is ≈14 bp**, against a "fundamental" overnight return of about 9 bp. The discount scales with the **expected intraday log-range**, i.e., expected volatility.
- **Asymmetric overnight anomaly.** JRFM 2022, https://doi.org/10.3390/jrfm15110534 `[SNIPPET]`. At index level, overnight returns are significantly negative after a *negative* daytime return, and not after a positive one.

**(a) What measured by the close predicts close→next-open (cross-section)**
- **Overnight component persistence.** In the US, momentum profits accrue overnight while most other anomalies accrue intraday, and the overnight and intraday components persist cross-sectionally (Lou, Polk, Skouras, "A tug of war," *JFE* 2019, https://doi.org/10.1016/j.jfineco.2019.03.011 `[MEMORY]`). For China, search summaries state that stocks with higher past overnight returns have **higher future overnight but lower intraday returns**, stronger with retail trading. The source papers (not opened): "Overnight versus intraday returns of anomalies in China" (*PBFJ* 2023, https://www.sciencedirect.com/science/article/abs/pii/S0927538X23000732), "The nexus of overnight trend and asset prices in China" (*JEDC* 2024, https://www.sciencedirect.com/science/article/pii/S0165188924001891), and "Day-night anomaly returns in China: the role of institutions" (2025, https://www.sciencedirect.com/science/article/abs/pii/S0275531925000327) `[SNIPPET]`. These are mostly monthly-horizon results.
- **Overnight→daytime reversal patterns.** In the US, frequent "positive overnight then negative daytime" patterns predict higher future monthly returns (Akbas, Boehmer, Jiang, Koch, *JFE* 2022, https://doi.org/10.1016/j.jfineco.2021.11.003 `[MEMORY]`).
- **Attention.** In the US, attention-grabbing stocks have high overnight returns from retail buying at the open, followed by an intraday reversal (Berkman, Koch, Tuttle, Zhang, *JFQA* 2012 `[MEMORY]`, URL not verified). In Shanghai, upper-limit hits draw attention-driven individual buying the next day, a temporary rise, and then reversal (Seasholes & Wu, *J. Empirical Finance* 2007, https://doi.org/10.1016/j.jempfin.2007.03.002 `[MEMORY]`). A sealed limit-up close is generally not buyable.
- **Post-close announcements.** "Earnings announcements in China: Overnight-intraday disparity" (*JCF* 2023, https://www.sciencedirect.com/science/article/abs/pii/S0929119923001207) `[SNIPPET]`; not opened. **Margin trading → overnight:** no source found.

**(b) Does a weak close (near the day's low) predict a better overnight return?**
- **US end-of-day price pressure reverses overnight.** Bogousslavsky & Muravyev, "Who Trades at the Close?" (WP Dec 2020, AEA 2021). https://www.aeaweb.org/conference/2021/preliminary/paper/H9T4hef7 `[FETCHED]`. Closing-auction price deviations average **8.1 bp** (20.6 bp for small stocks, 2.7 bp for large). **85% is reversed by the next morning** (110% for large, 85% for small), and a third to a half of that happens within 30 minutes after the close. Only **19% of the last-5-minute return** reverses. Price pressure at the close is mostly uninformed.
- **A-share practitioner evidence (not peer-reviewed).** A Zhihu backtest of the popular "尾盘买入法" ("buy strong names late in the day", https://zhuanlan.zhihu.com/p/1911378448959674349, `[SNIPPET]`, page returned 403):
  - Buying at the close and selling at the next open: **36.2% win rate, −0.47% average per trade.**
  - Buying at 14:30 and selling at 9:30 next day: 22.5% win rate, −1.08% average.

  This is close in size to the system's −0.5 pp for top-30% close-location names.
- **Mechanism consistent with both.** The T+1 opening discount scales with expected volatility (Qiao–Dam). Strong closes plausibly carry more locked-in T+1 buyers and higher expected range. This link is my inference, not a tested result.
- **Not found.** I found no peer-reviewed A-share paper documenting close-location→next-open specifically.

**(c) Chinese sell-side quant research (2019–2025)** `[SNIPPET]` throughout:
- **东吴金工, "订单簿的温度" series.** Splits daily returns into overnight plus four hourly segments, with the 20-day sum of overnight returns as "M0" (https://bigquant.com/wiki/doc/wA2Xh0NuAT). The related "日与夜的殊途同归" momentum factor has a decile long-short return of 20.95% p.a. and IR 2.60 since 2014-02.
- **方正金工.** "A股'跳一跳'" finds overnight-gap extremes are both negative over the next *month* (https://zhuanlan.zhihu.com/p/61049670). "日内动量与隔夜反转" covers industry rotation (https://zhuanlan.zhihu.com/p/61734428).
- **广发 (2025-11).** "基于隔夜相关性的因子研究" (https://finance.sina.com.cn/stock/stockzmt/2025-11-21/doc-infycink0184687.shtml).
- All of these are **monthly-rebalanced selection factors**. I found no sell-side report testing a T+1-close→T+2-open holding period.

**Implications for the system**
- A 股多头隔夜持有先吃结构性逆风：指数约 −6bp/日，T+1 开盘折价约 14bp，且随预期振幅放大。BUY 的基准应当是同日候选池或匹配对照的隔夜均值，而不是 0。高波动、高关注的票，结构性折价可能更深。
- "收在区间下部隔夜更好"有三条独立的旁证：美股收盘价格压力隔夜反转、T+1 折价机制、坊间"尾盘买强势股"回测约 −0.47%/笔。但没有 A 股同行评审直证，n≈110 也偏小。它是确定性价格特征，可在全 A 历史截面（2016–2026，千万级 stock-day）做预注册样本外检验，不必等实盘。
- 检验要控制当日涨幅、振幅、换手、涨跌停、近期隔夜收益（持续性），并区分市场项与截面项（按日去均值）。可实施性按 14:56 快照决策、收盘集合竞价成交来做。注意沪市 2018 年前收盘价是最后一分钟成交量加权均价（VWAP），2018 年起才改为收盘集合竞价 `[MEMORY]`，跨期口径会变。
- 卖方隔夜和日内因子多是月频选股尺，不能直接移植到隔夜持有尺。

## 5. LLM forecasting and judgment reliability

**Evidence**
- **ForecastBench.** Karger et al., arXiv 2409.19839, v5 Feb 2025. https://arxiv.org/abs/2409.19839 `[FETCHED]`. On leakage-free future questions, **expert forecasters beat the top LLM (p<0.001)**.
- **Halawi et al. (2024).** "Approaching Human-Level Forecasting with Language Models," arXiv 2402.18563 `[MEMORY]`. A retrieval-augmented system scored Brier ≈0.179 against the crowd's ≈0.149, and the combination improved on both.
- **Prophet Arena.** arXiv 2510.17638, 2025. https://arxiv.org/abs/2510.17638 `[SNIPPET]`. Across 1,367 events, the best LLM Brier was ≈0.184 against a market baseline of 0.187. ECE was 0.041–0.067 against the market's 0.069. Rankings change by metric.
- **Ensembling.** "Wisdom of the silicon crowd" (Schoenegger, Tuminauskas, Park, Tetlock, *Science Advances* 2024, https://www.science.org/doi/10.1126/sciadv.adp1528 `[MEMORY]`). The median of 12 LLMs was statistically indistinguishable from a 925-person human crowd. Individual models showed acquiescence bias, and averaging with human forecasts helped.
- **Verbalized confidence.** LLMs tend to be overconfident when stating their confidence (Xiong et al., ICLR 2024, arXiv 2306.13063 `[MEMORY]`).
- **Debate vs vote.** "Debate or Vote," NeurIPS 2025, arXiv 2508.17536. https://arxiv.org/abs/2508.17536 `[SNIPPET]`. **Majority voting accounts for most of the gains from multi-agent debate.** Debate alone induces a martingale over beliefs, i.e., no expected improvement. This agrees with "Should we be going MAD?" (Smit et al., ICML 2024, arXiv 2311.17371 `[MEMORY]`). Self-consistency (Wang et al., ICLR 2023, arXiv 2203.11171 `[MEMORY]`) is the simple baseline that debate must beat.
- **Listwise ranking position bias.** "Found in the Middle: Permutation Self-Consistency" (Tang, Zhang, Ma, Lin, Ture, NAACL 2024, arXiv 2310.07712 `[MEMORY]`). Shuffling the list order repeatedly and aggregating the rankings removes position bias and improved listwise ranking substantially. Separately, models under-use information in the middle of long contexts ("Lost in the Middle," TACL 2024, arXiv 2307.03172 `[MEMORY]`).
- **Test–retest instability.**
  - "Multifaceted variability in LLM-driven stock recommendations" (*Finance Research Letters* 2025, https://www.sciencedirect.com/science/article/abs/pii/S1544612325021762) `[SNIPPET]`: identical prompts to the ChatGPT and Claude APIs gave statistically significant repetition, rephrasing and system-prompt variability.
  - "The Coin Flip Judge?" (arXiv 2606.13685) `[SNIPPET]`: a fixed LLM judge **flipped its verdict 13.6%** of the time on average, and 28% of items exceeded a 20% flip rate.
  - Temperature-0 outputs are also non-deterministic (Atil et al., arXiv 2408.04667 `[MEMORY]`).

**Implications for the system**
- L3 用 listwise 从 208 行表里挑约 10 只，存在位置偏差和"中间遗失"风险。低成本改法：k 次随机打乱行序重复排序再聚合（排列自洽），或改成逐行打分加确定性排序。先量化重测稳定性：同输入跑两次，看 finalist 集合的 Jaccard 和秩相关。
- 用重测翻转率衡量五档评级的判决噪声。若 Hold/UW 重跑翻转率达到 10–20%，那么"评级不区分隔夜收益"本身就有一部分是测量噪声。单次评级不应单独驱动 BUY。
- 让卡片输出概率，例如 P(隔夜超额>0)、P(10 日超额>0)，对所有 finalists 用 Brier 或 log score 计分并画校准曲线。这比五档标签更有统计功效。
- 在投票基线之上没有证据支持辩论层。如果保留多空角色，要用同算力的"独立采样加投票"对照证明它的增量。

## 6. Statistics for a ≤1-trade-per-day strategy

**Evidence and methods** (all `[MEMORY]` unless noted)
- **Minimum Track Record Length.** Bailey & López de Prado, "The Sharpe Ratio Efficient Frontier," *J. Risk* 2012, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1821643:
  MinTRL = 1 + [1 − γ₃·SR + (γ₄−1)/4·SR²]·(z_α/(SR−SR*))²
  Here SR is the Sharpe ratio *per observation*, γ₃ is skew and γ₄ is kurtosis. Related tools:
  - **Deflated Sharpe Ratio**, which corrects for the number of trials and non-normality (*JPM* 2014, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551).
  - **PBO via CSCV** (Bailey, Borwein, López de Prado, Zhu, *J. Comp. Finance* 2017, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253).
  - Harvey, Liu, Zhu (*RFS* 2016, https://doi.org/10.1093/rfs/hhv059): new factors need **t > 3.0**.
  - Harvey & Liu, "Backtesting" (*JPM* 2015): haircut Sharpe ratios for multiple testing.
- **Sequential testing.** Always-valid p-values (Johari et al., arXiv 1512.04922) and e-values / test martingales (Ramdas, Grünwald, Vovk, Shafer, *Stat. Sci.* 2023, arXiv 2210.01948) allow continuous monitoring without inflating error rates.
- **Gaining power.**
  - Strictly proper scoring rules for every forecast (Gneiting & Raftery, *JASA* 2007).
  - Paired forecast comparisons (Diebold–Mariano, *JBES* 1995).
  - Daily cross-sectional regressions or rank IC with Newey–West or Fama–MacBeth errors.
  - Breadth: IR ≈ IC·√breadth (Grinold, *JPM* 1989).
  - Matched controls on the same day and in the same industry, which remove the common overnight market move.

**Worked arithmetic** (my calculation; assumes per-trade overnight excess σ ≈ 2.5%, normal returns, one-sided α = 5%). "N for significance" is the MinTRL: the track length at which the observed edge would just reach significance.

| Edge per trade | SR per trade | N for significance | N for 80% power | At ≤1 BUY/day |
|---|---|---|---|---|
| 0.30 pp | 0.12 | ≈190 | ≈430 | ~1–2 years |
| 0.15 pp | 0.06 | ≈750 | ≈1,700 | ~3–7 years |

Negative skew or deflating for multiple trials pushes these numbers higher.

By contrast, suppose all 208 names are scored daily, the true daily rank IC is 0.03 and the daily IC standard deviation is about 0.10. Then 80% power needs **about 70 trading days**; with IC = 0.02, about 155 days.

On the system's current numbers: if "0.00 ± 0.2 pp/day" is ±1 SE, the result only rules out edges larger than about 0.4 pp/day. Economically valuable edges of 0.1–0.2 pp remain undetermined.

The close-location result: if n≈110 per tail, the t-statistic is ≈2.4 before clustering by day. If n≈110 in total, t ≈ 1.7. Either way it is suggestive, not established.

**Implications for the system**
- BUY 级实盘 P&L 无法在合理时间内验收研究层（1–7 年）。不应以"BUY 是否赚钱"作为研究层是否有效的判据。
- 改用截面计分：每天对全部 208 只（乃至 1,000 只）L3 和 L4 产出打分或给概率，按隔夜、日内、10 日三尺分别算日度 rank IC 和 Brier，用 Newey–West 或 Fama–MacBeth 推断。2–3 个月可以得出有功效的结论。
- BUY 与同日同行业匹配对照做配对差，去掉隔夜市场共同项，可以显著缩小方差。
- 对"收盘位置"类发现做预注册，计入试过的切点数后算 DSR 或 t>3 门槛。上线后用 e-value 序贯监控，避免反复偷看导致的假阳性。

---

**Not found or not verified in this session**
- A peer-reviewed A-share study of close-location→next-open returns.
- Sell-side reports (2022–26) that test close→open holding periods.
- Margin trading → overnight returns.
- Details of InvestorBench and FinTSB.
- Alpha Arena's primary write-up; only press coverage was seen.
- Several ScienceDirect China papers, seen only as titles and snippets.

`[SNIPPET]` numbers should be checked before they are cited externally.

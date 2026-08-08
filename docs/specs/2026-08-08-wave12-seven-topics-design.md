# Wave12 设计稿 —— 七问:统一相对 BUY × 尺收口 × 隔夜引擎 × 热度召回 × 粗排连贯 × 报告双层 × 新闻事实层 × 编排收口(2026-08-08)

> **状态**:brainstorm 修订稿,**只落文档、不做开发**;2026-08-08 已完成方向裁定,待用户 review 后另立实施 plan。
> **调度定位**:本稿接任**总调度权威**(批A–F + 运营即办清单)。Wave10(2026-08-01 稿)A0–A12/B/C3 已全部落地,其**实验成熟窗与 monitor 照旧运行**、不收编;Wave9 批 C/D/E/F 未清债仍归 Wave9 稿;2026-08-03 候选池仍是候选池,本稿只把其中若干件**排进批次**(B 类逐件走 registry,状态单一事实源仍是 `autoresearch/research/candidates.py`)。Wave11(2026-08-05 稿)四批已全部落地(08-05→08-08,含 C1–C5 尾修),本稿批A 是其换尺工程的**收口审计产物**。
> **实施纪律**:逐 task 先跑 premise-check(立案诊断动工一查可能全错,2026-07-28 家训);冲突以源码为准;所有比率带 numerator/denominator/as-of;**读数一律标尺**(`oc`=fwd_2_oc 旧尺 / `gap`=gap_c1_o2 现主尺)。

---

## 0. 任务由来与裁定状态

用户 2026-08-08 提出七个议题(原文编号保留),本稿逐题给出方案。经二轮 brainstorm,用户追加裁定:①对外只有一个 `BUY` 信号;②每个完成的交易日扫描至少给出一只 `BUY`;③最低一只是**相对 BUY**而非绝对上涨承诺;④相对基准以全市场可交易股票等权为主、行业中性超额为辅;⑤现有 OW 三门允许重构;⑥采用「统一相对决策层」而非多策略 BUY。已裁定项与仍待后续实证/人批的行为变更分列在 §13。

| # | 用户议题(原文意) | 处置批 | 既有裁定约束 | 本稿状态/处置 |
|---|---|---|---|---|
| Q1 | 每个阶段是否做成独立 workflow;agent 配置规范化进 json | 批B | R3(08-05):model/effort 收口 scan_config 且可验证生效——**已落地** | **方向已裁:单壳多 phase+resolved JSON config**;R-B2(full 档收编否) |
| Q2 | 宏/中/微研究里新闻独立成 agent 还是融合 | 批D | intel 只拒稿不拒票;B 类走 registry | **方向已裁:事实层独立,判断不拆** |
| Q3 | summary 分详细版+核心速读版;是否专职 agent | 批C | R6(Wave10):漏肉可见但分母同屏 | **已裁:核心 brief 确定性生成,不设收编官 agent** |
| Q4 | **最重要**:为什么一直没有 buy | 批E | 旧 R3「不为凑单松门」继续有效,但「三门不动」被本次裁定替代 | **已裁:统一相对决策层;完成扫描日至少 1 BUY** |
| Q5 | 当日游资/龙虎榜/雪球高热做召回;召回要不要 agent | 批E | 零 LLM 层铁律;追当日大涨负结果(oc 尺);「不要跌势票」=偏好非预测 | 召回确定性已裁;R-E5(快照数据执行批准) |
| Q6 | 粗排太简单?(不上模型)召回↔粗排连贯性与筛选质量 | 批F | L2 上模型负结果;L2-200 菜单内无确定性信号 | **候选护照升为主契约**;R-F1(吸筹死配额)仍需名单 delta 人批 |
| Q7 | **最重要**:所有阶段度量指标 = T+2 开盘 / T+1 收盘 | 批A | R1(08-05):主尺=gap_c1_o2——**已落地**,本批清尾差 | 无(执行审计) |

**一句话总判**:本波不再把「增加供给」和「为什么仍不 BUY」分开修。L1/L2 负责保真供给,L3/L4 负责核证与风险,L5 的确定性统一相对决策层横向选出最终 `BUY`;每个成功完成的交易日扫描至少一只,其余所有读数统一按 `gap_c1_o2` 评价。Q7/A 是地基,Q4+Q5+Q6/E+F 是一条主闭环,Q1/Q2/Q3/B+C+D 降为配套工程。

---

## 1. 证据底片(2026-08-08 实测;全部带产物路径)

### 1.1 换尺后的世界(Wave11 落地态)

- 主尺单点:`autoresearch/common/ruler.py:12` `MAIN_RULER="gap_c1_o2"`;`ENTRY_FLAG="buyable_c1"`(T+1 收盘未封涨停,剔)、`EXIT_FLAG="unsellable_o2"`(T+2 一字跌停开,标旗不剔)、`SCHEMA_SWITCH_V4="2026-08-07"`(卡契约 v4 分界)。资格过滤单点 `ruler.entry_flag_for()/entry_tradable()`(C1 修复,11 个消费点)。
- 权重已按新尺重校准两次(A4 快照 040f6202;C1 后 7ab909f1→61df7711);`weights.json.regimes` 按用户裁定只留 `range` 桶(commit f10102c)。
- **A8 两尺对照**(`docs/research/2026-08-07-ruler-gap-vs-oc-baseline.md` v2,29 日窗):
  - 九路召回 unique 超额(gap):composite +0.13% 第1、growth +0.06%、reversal +0.05%;**value −0.07%(oc 尺 +1.04% 第1 → 符号翻转,王座作废)**;heat −0.14%(oc −1.85%,大幅改善);momentum −0.26%;healthy −0.41% 垫底。
  - **L3 真选 edge:oc +0.93% → gap −0.06%,归零**(剔📌两侧后)。
  - 门的价值配对版(仅 6 个买单日):真实 −1.25% vs 影子 −0.57%(gap)——**买单日的真实买入跑输被门拦下的影子**(薄样本,方向参考)。
  - 弃权日裁决 4/11 翻转(07-16/17/21 FALSE→NEUTRAL;07-29 反向)。
  - ⚠️ 该报告三处引用 `task-15-report.md`,仓库 find 零命中=断链引用(批A A12)。
- **momentum/heat 相位条件性**(`docs/research/2026-08-04-momentum-phase-conditional-ic.md`,**oc 尺**,26 日):上涨相位 momentum −2.32%[整区间<0]、heat −4.08%[整区间<0];回撤相位两者跨 0。**gap 尺未复算,已列 A8「待重验」**。

### 1.2 0买事实链(Q4 的立案现场)

- **17 个连续 0买扫描日**(07-15→08-06);34 个 scan 日 27 日零买(`reports/learning/journal.md:40`);最后一笔买单 2026-07-14 格力电器(`reports/learning/buy_ledger.md:13`)。
- **评级分布**:07-15 起 178 张卡 **Overweight=0**(`context/scan/*/_final_ratings.json` 逐日抄录)。最近 6 run 每天 9–11 张卡全 Hold/UW(+偶发 Sell)。
- **门 vs 判断层的相对贡献**(近 6 run,`reports/scan/<run>/details/*.md` 逐卡净分):
  - 4 个 run 里共 **7 张卡净分 ≥+2 被 OW 三门压回 Hold**(其中 6 张压在「主力真在」);
  - **最近 2 个 run(08-05/08-06)全场最高净分只有 +1** ——rubric 自己没到 OW 档,门没有出手机会;
  - 每 run 另有 4–6 张早停卡(按定义 ≤Hold,无 OW 资格);早停账本 42 张成熟:资金流出桶 n=17 均值 −0.14%(拦得对)、题材透支桶 n=13 均值 +0.03%(边缘,继续攒)(`reports/learning/earlystop_ledger.md`)。
- **门的错杀率(换尺后)**:主力真在 participation 口径 **FALSE_KILL 6/94=6.4%**、单门 attribution 口径 **0/25=0.0%**(`context/learning/gate_participation_v3.csv`,ruler 列全 gap,as-of 08-07);MULTI_GATE 71 拦 8.5%。⚠️ 08-06 报告里印的「主力 26/81≈32%」是换尺前渲染的旧数(`reports/scan/20260806_2308/summary.md:203`),下次 run 会自动按新 CSV 重算——**换尺让门的错杀读数从 32% 掉到 6.4%,「门在杀肉」的叙事在隔夜尺下大幅减弱**。
- **两把尺对 0买日给出相反的市场背景**(`context/scan/<date>/retro/attribution.csv` 直算,全市场):

| 数据日 | gap_c1_o2 均值/中位 | fwd_2_oc 均值/中位 |
|---|---|---|
| 07-31 | +0.54% / +0.34% | +3.08% / +2.58% |
| 08-03 | −0.09% / 0.00% | +3.15% / +1.73% |
| 08-04 | −0.38% / −0.28% | +2.45% / +1.02% |
| 08-05 | −0.05% / −0.01% | +1.82% / +0.60% |

  近期市场的肉几乎全在**日内**(D+1 开→D+2 收),隔夜缺口≈0。**在用户裁定的隔夜尺下,最近的 0买大体是对的;"错过反弹"主要错过在系统授权之外的日内窗。**
- paper NAV(隔夜主表,至 08-07,`reports/learning/paper_nav.md:39`):真实 −0.21%(9 笔)vs 影子(门不拦最想买3只)−3.37%(96 笔)vs sized −4.95% vs 市场等权 −3.97%。**弃权政策在 NAV 层仍然赢**。
- **OW 基率**:历史全部 9 笔 OW,T+2 胜率 **0%**、均值 −0.70%(`reports/learning/buy_ledger.md:16`)——这台机器的「买」从未被证明会赢。
- 弃权因果账(v2,13 日已裁):**CORRECT 0 · FALSE 4 · NEUTRAL 9**(`reports/learning/abstention_ledger.md:3-4`)——弃权也从未被证明「弃对」,只是「没弃错」居多。
- **Wave10 四个影子实验的真相**:registry 4 条全 `PREREGISTERED`、`observations=[]`(`context/learning/experiments/registry.json`)。**EXP-1(主力5日)与 EXP-2(板块动量影子通道)的 challenger 数据腿从未实现**——`sector_momentum` 通道在 `autoresearch/scan/recall/channels.py` 不存在,`gate_attribution.py` 无 `positive_days/main_net_yi` 逻辑;被删的 `wave10_experiments.py`(dc73d90)只是注册脚本。**预注册 D+6 天,零观测**。两个 monitor(EXP-0/EXP-3)在 `context/learning/monitors.json`,08-01 后未更新。
- t1 快环 gap 终判(Wave11-A6):代码已落(`learning/t1_review.py:765-845` + nightly 接线),但 **scorecard/账本至今零 gap 列落盘**;`context/learning/t1_review.jsonl` 最近 15 行 ruler 值仍为 `fwd_2_oc`(待查,批A A11 首验)。
- near-miss「差一点」节 6 run 里只有 08-06 渲染出来:`shadow_buys` 生成器**不在 nightly 账本链**(`learning/nightly_close.py:94-103` names 无它,2026-08-08 复核),前五 run 渲染时 CSV 尚无当日行→整节静默跳过。
- 哨兵连续 4 run 判「材料枯竭」被人工 force_full 拉满(`summary.md:201` 各 run)。

### 1.3 漏斗与粗排现状(Q5/Q6 的立案现场)

- 召回:12 路注册、9 路启用,**有效 8 路**——`reversal_confirm` 硬门要 `vol_ratio_20`,该因子从未接入生产 L1 帧,5 个连续扫描日 0 召回(`common/scoring.py:209-212`;`context/scan/*/L1_channels.csv` 实测)。
- L2 分层采样(`scan/recall/l2_stratify.py`):merit 核 107 + 风格桶 floors Σ93 + 行业 cap 40;**吸筹桶 floor=12 是死配额**(accumulation 通道 07-11 已停用,桶恒空,12 席白扣 merit_need 落 backfill 且被记 `l2_lane_reserved=True`)——与「未启用通道 floor 必须=0」的既有判例(`l2_stratify.py:28-35` 事件桶注释、Wave4 critical)自相矛盾。实测 08-06 菜单 `l2_lane_reserved` True 96/203 ≈ 半张菜单是配额救回。
- **`selection_reason/selection_detail` 在 L2 算了但没落盘**(`universe.py:459` l2_cols 漏投影)→ `l2_slo` 的 guards 分布分支 31 天从未触发。
- L2 SLO 读数(31 日 MATURE,`reports/scan/l2_slo.md`):端到端 wc_l1_all **20.5%**、**wc_l2_all 3.3%**;条件召回 wc_l1_given_l0 26.2%、wc_l2_given_l1 **16.0%**;近期报警日 07-27/28/30、08-04。⚠️ 该报表是 C1 修复前生成(winner 定义串还是旧旗),需重跑。
- `channel_audit`/`stage_eval` 列名仍叫 `*_t2`,值已是 gap(`channel_audit.py:36-38`)——跨 08-05 的账本趋势线有未记档的定义断层。
- 热度/游资数据面:**在库** top_list(L3 lhb_n 列)、top_inst(L4 席位简报行,不进门)、rz_buy_intensity(唯一进 composite 的两融因子,权重 0.02)、limit_list_d(S1 情绪温度计五相位,喂 market_pack,非召回);**不在库**:东财人气榜/雪球关注(akshare 端点零代码)、游资席位 seat_db(设计过未建)、集合竞价(全仓零命中)。lhb_inst_net 研究结论=反指(机构上榜后 T+1~T+10 偏弱,`common/uzi_lenses.py:4-10`)。
- event 路裁决(pr_20260725_001,open):影子长表 10 日、可配对 9 日,**差 08-06 的 retro 归因一天到 ≥10 日判据**(`context/scan/2026-08-06/retro/` 为空,08-08 复核)。
- quota advisory 腿(人批)由 `mean_unique_excess_t2`(值已 gap)驱动;heat 缩额 200→150 已实施(scan_config `channel_quotas`)。**无显式 L0 召回率目标**;missed_l0≈赢家 9%(小盘/次新/北交所,STAGES.md:52)。

### 1.4 报告与编排现状(Q1/Q3 的立案现场)

- `reports/scan/20260806_2308/`:总 3.1M/129 文件;**summary.md 47,814B/295 行/19 节**;最大三节=行业研判 13.4KB(28%,8 份 brief 研判段原文嵌入)+经验/未决反馈 8.0KB(17%)+投资建议 6.1KB(13%),合计 57%。决策卡 11 张 9.2–17.7KB 在 `details/`。**没有任何 digest 类产物**(index.md 是导航页,CP7 播报不落盘)。
- **机器消费者不解析 summary.md 正文**:t1_review/retro/gate_attribution/abstention 全部读结构化文件(`finalists.csv`/`decision_records.json`/`L1_scored_full.csv`/`shadow_buys.csv`);卡契约红线在 `details/*.md`(Rating 行、OW三门 ✗ 标记、进入P4倾向行)。→ **summary.md 重排自由度大**。
- 编排:4 个 workflow。`scan-market.js` 已经包住 L0→L3+L4prep 全段(prelude 壳/macro-brief/GATE1/sector-brief×N/l3-rank/L4 生产者);**L4 每股派发在主会话**(Workflow tool × N 个 `l4-stock.js`,Wave11-C 已全并发单批);L5=主会话内联五条 CLI。主会话成本份额 32.7%($12.82,08-03 实测)超 25% 挂账线,大头是 L4 派发与领取通知的唤醒回合。
- 配置:12 role 闭集 + 三层回退(config > workflow AGENT_DEFAULTS > frontmatter)+ `usage_reconcile` 生效对账——**Wave11-B 已收口**。残留游离点:①`t1-review.js` 无空 cfg throw 守卫(忘传→静默吃 'high');②`lite-playbook.md:178` 硬编码 `Agent(model='opus')` 无 lint 覆盖;③`apply_to_scan_config()` 生产零调用(文档-实现落差);④`usage_reconcile` 分不清 ens_review/l4_card(同 agentType,靠同值蒙混);⑤gp_shell 三处字面量靠单测同步非单一源。
- 嵌套 workflow:harness 支持一级 `workflow()` 子调用;本仓 `research/nested_probe.py` 10 项探针**全 UNTESTED**(08-04 批5 建好未跑)。
- stock-research full / macro-research full 档 = 主会话内联扮演,完全在 scan_config 体系外。

### 1.5 新闻现状(Q2 的立案现场)

- 18 个新闻进入点已盘全(微观 l4-intel 六面盲搜活/L3 公告 cninfo 兜底活 99.5% 命中/中观 sector-brief ≤2 网查/宏观 harvest 零新闻端点全靠网查占位)。
- **`autoresearch/news/` 四模块(catalog/typed_events/claim_ledger/fulltext)代码活、生产死**:零调用点,连 `catalog inventory` 都没跑过(08-04 落地记录自认);三个 B 类消费接口(intel 先读目录/L3 第二源/typed-event 进 prompt)全 OPEN。既有资产 ~1,891 个 `stock_news_em` parquet 分片待编目。
- 文档滞后 bug:`.claude/agents/l3-rank.md:24` 仍写「news_sent 整列恒 0」,而 cninfo 兜底 07-30 已接线(该文件最后编辑 07-28)——rubric 在按过时事实指挥 L3。

### 1.6 尺子尾差(Q7 的立案现场;详表见批A)

9 处 🔴 漏网 + 文档层大面积滞后 + 1 处测试假锁,见 §2。核心一条:**因子晋升判据(IC/ICIR/t/两半同号)整面还在 fwd_2_oc**(`research/factor_lab.py:634,703,709`)——今天任何新因子(包括 Q5 的热度族)都会被旧尺裁决。

---

## 2. 批A · 尺一致性收口(Q7)

**原则**:①决策面优先于展示面(先修「谁在裁决」,再修「谁在显示」);②历史产物不改写,展示层现算(gate-status 家训);③每修一处必配变异探针(错尺变异必须让测试变红);④修一处必 grep 全部消费者(wave3 家训)。

### A1 ensemble 折回账本资格旗(功能 🔴,C1 唯一真遗漏)

- 现状:`learning/ensemble_ledger.py:153-155,185` 收益已按 MAIN_RULER 取,入场旗仍读旧腿 `buyable`(D+1 开盘旗);attribution.csv 里 `buyable` 与 `buyable_c1` 并存语义不同,确凿跨腿混用,污染折回 verdict 的分母分子。
- 改法:两处过 `ruler.entry_tradable()`;比照 C1 手法。验收:构造「D+1 开盘一字板但收盘未封」假票,断言修后进样本、修前被误剔(变异探针)。

### A2 因子晋升面切主尺(决策面 🔴,批A 最重要一条,E3 的前置)

- 现状:`factor_lab.py:634` 只在 `fwdcol=="fwd_2_oc"` 时产 t/hit/IC 两半;`:703` `sortcol="ICIR_fwd_2_oc"`;十分位主表 `:644-653` 也是旧尺。A1(Wave11)只加了 gap 的 decile 并列表,**没有** gap 的 IC/ICIR/t/两半——「因子该不该入组」100% 由旧尺裁决。
- 改法:eval 的完整判据族(IC 均值/ICIR/t/两半同号/decile spread_t)对 `MAIN_RULER` 产出并作主排序;旧尺列降并列参考(列名保留)。`render_ic_by_regime` 标题模板改为插值 `MAIN_RULER`(修 `:932` 死字符串)。
- 验收:①`tests/research/test_factor_lab.py` 补 `sortcol` 断言(现状 docstring 声称锁了、测试体没锁=假锁,`:198`);②对同一面板跑双尺 eval,人工抽 3 因子核对 gap ICIR 与手算一致;③错腿变异(gap 算成 close/close)必红。
- ⚠️ 连带认知:切换后现有 `_GROUPS` 的入组资格要按 gap 重审一遍——预期会有因子换位(A8 已示 value/momentum 排序大挪移),重审结论落 `docs/research/<实施日>-factor-regroup-gap.md`,**改组本身是 B 类走 registry**,本条只换裁决尺不改组。

### A3 0买裁决面切主尺(🔴)

- `learning/zero_buy_ledger.py:47,80-84`:fwd_2 字面量被渲染成「(主尺)」并直接决定「空仓方向正确/失明预警」判词——注释自辩「三档并列勿随主尺漂移」与用法(单档当主判据)自相矛盾。改法:增 gap 列为主判据、fwd_2/fwd_5 降参考列;verdict 由 gap 符号出;表补 08-06 起行。
- `learning/journal.py:24-25,81`:每日总账无主尺列(只有 fwd_1/fwd_5)。补 `mkt_gap` 列(回填历史,n 不清零)。
- 验收:重放 §1.2 四个 0买日,断言 verdict 按 gap 翻转符合 A8 ④(07-29 应转 FALSE)。
- **定义断层**:本项只修复 E6 activate 前的历史与 shadow 对照。E6 上线后,成功 run 不再产生 zero-buy 新行;该账本冻结为 legacy,新日级主账改记 `action_coverage`、relative BUY 收益与 BLOCKED 原因。

### A4 心跳 IC 切主尺(🔴)

`learning/changelog_ledger.py:36-45` `_day_ic()` 用 `fwd_1_oo`(比旧主尺还旧一代)评价「重标定有没有改善排序」,而权重按 gap 校准——尺完全错配。改 `MAIN_RULER`;心跳文案带尺名(会变的量断言,07-16 家训)。

### A5 evidence_manifest 受控语义表(🔴)

`learning/evidence_manifest.py:42,70,84,593` 定义串写 fwd_2_oc、取值已是 gap(受控语义表自身失控)。改法:定义串插值 `MAIN_RULER` + 每指标带 `ruler` 字段;历史 `wave10-gate0-evidence.json` 不改写,新档案起用新串。验收:注入「定义串与取值源不一致」的映射,schema test 必红(A0 同款手法)。

### A6 EXIT_FLAG 接消费(🔴,C1 的孪生病)

`unsellable_o2` 生产/落盘齐全、**全仓零读侧**(`ruler.py:32` 自己写着「B 没消费」)。最小消费面(裁定「标旗不剔」的落法):①retro 渲染层给命中票加 ⚠️卖不出旗;②paper_nav 隔夜模式对 unsellable 票按「顺延到可卖开盘」计;③账本聚合行报 `unsellable_n`。验收:构造 T+2 一字跌停开假票,三处可见。

### A7 lessons 打尺标 + 旧尺经验重验(🔴)

`context/knowledge/lessons.jsonl` 6 条中 4 条引用旧尺读数且无 ruler 字段(第 5 条 `ls_momentum_recall_quota_swing_horizon` 整条建在 `unique_excess_t5` 上,早该退);而 lessons 会注入 L3/L4 prompt。改法:①schema 加 `ruler` 字段(缺省按写入日期推断回填);②逐条重验:读数能在 gap 尺复算的复算,翻转的改写或 retire(走 lesson_yield/decay 正门,不硬删);③`feedback_store` 注入渲染带尺标。

### A8 t1 gap 终判首验 + 调度补漏(活体验收)

- Wave11-A6 代码已落但零读数落盘(§1.2);首验=对 2026-08-05/08-06 两日真跑 `gap_finalize_pending`,断言 scorecard 三列(`gap_c1_o2/z_gap/final_verdict`)出现、账本幂等整替、ruler tag 更正;查明现存 15 行 `ruler=fwd_2_oc` 的来源(premise-check:可能是初判行的合法标注,也可能是漏改——先查再改)。
- `shadow_buys` 加入 `nightly_close._ledgers` names 表(排 gate_attribution 之后)——修 near-miss 节 5/6 run 静默缺席的根因;验收=下一真实 run 的 summary 必出「🎯 差一点」行(有 shadow 时)。

### A9 名实不符与断层记档(🟡→收口)

- `channel_audit`/`stage_eval` 的 `*_t2` 列名装 gap 值:**不改列名**(改名会破全部读者),改法=①两账本渲染层标题注明「列名沿革,值=MAIN_RULER」;②`channel_eval.csv` 增 `ruler` 列;③在 STAGES.md 记「2026-08-05 账本定义断层」条目(比照 `pre_healthy` 先例)。
- 同型标签修正:`sector_top3_backtest.py:2,61` 文案、`precedents` 卡内 `fwd_2` 显示标签、`cross_calib` docstring(:9,151,315)。

### A10 文档大扫(按「活指令改、纯沿革标注」两分)

- **活指令(必改)**:`docs/PANORAMA.md`(0 次 gap_c1_o2!:682/:704/:710/:729/:873/:971/:1043 七处,其中 :704「权重校准主尺仍 fwd_2_oc 两把尺勿混」直接与现行裁定冲突);`.claude/skills/scan-market/SKILL.md:147-148`;`STAGES.md:259/307/308/368/376`(:307 落后两代:写 hi_10 判据,代码已是 ex2+日期分界);`.claude/skills/scan-retro/SKILL.md:13,53` + `retro-playbook.md:7,34,97`;`.claude/agents/l3-rank.md:24`(新闻文案,批D D2 同源,一并修)。
- **代码活 docstring**(15 处,agent 审计清单在案):逐个改为引 `MAIN_RULER` 或加「参考尺,固定列名」注。
- 验收:`grep -rn "fwd_2_oc" docs/ .claude/` 的每处命中都能归类为「沿革注记/参考尺注记/历史 research 报告」三类之一,归不进=fail(做成一次性审计脚本,不进常驻 lint——常驻见 A11)。

### A11 防复发 lint(product_shape_lint 家族新成员)

规则:`autoresearch/{learning,research,scan}` **新增文件**(git diff 粒度)中出现 `"fwd_2_oc"` 字面量且同文件无 `参考尺` 注记 → lint fail;`.claude/` 文本出现「主尺 fwd_2_oc」措辞 → fail。存量文件不追溯(A10 清完即净)。验收:变异=新建一个裸写旧尺的假模块,lint 必红。

### A12 报表重跑与断链修

①`l2_slo` 全量重跑(C1 后 winner 定义已换旗);②`channel_audit` 主表重出;③两尺对照报告(Wave11-A8)的 `task-15-report.md` 断链:找回落库或把三处引用改为「review 记录见 git log f58c3cd/23ed1b2」;④08-06 报告里的旧口径门归因行不回写(历史产物),下次 run 自动按新 CSV 现算——首验时人工核对一次。

**批A 回滚杆**:每条独立可回滚(单 commit);无整体杆——本批不改变任何评级/名单语义,只把「量的尺」与「裁定的尺」对齐。
**批A 体量**:~1.5–2 天(A2 最重);全部 M 类(测量/展示/文档),无 B 类。

---

## 3. 批B · 编排收口(Q1)

### B0 结论与被拒方案

**推荐:不全拆,做「派发下沉 + 尾差清零」。**

- **被拒方案甲「每阶段一个独立 workflow」**(prelude/briefs/L3/L4prep 各自成 workflow,主会话逐段拉起):被拒理由——①主会话唤醒次数从 1 变 4+,32.7% 成本份额的病根(唤醒回合全上下文 cache 读计费)反而加重;②段间传参面(config/日期/staging 路径)×4 份,08-05 空 config 事故的复发面变大;③「独立可恢复/进度可见/配置一致」三个好处 scan-market.js 内部已有(phase 归组+StageResult+task book)。**「每个阶段都是 workflow」在本仓的正确形态是「每个阶段都是 workflow 里的一个 phase」,而不是「每个阶段一个 workflow 文件」。**
- **被拒方案乙「stages.json 阶段注册表」**(阶段→载体→配置声明式清单):被拒理由——scan-market.js 本身就是这份清单的可执行形态,再立一份 JSON = 第二事实源,漂移风险大于收益(instruction-vs-check 家训:先别造要对账的东西)。阶段→载体表以 STAGES.md 文档形式维护(A10 顺手核对)。

### B0.5 agent 配置规范(用户已裁方向)

- `scan_config.jsonc.agents` 是 scan 路线 model/effort/enabled 的唯一用户配置面,采用闭集 schema;生产文件显式列全角色,未知 role/字段、缺生产必填 role、空 `{}` 均 fail-fast。
- `user_config.py` 只做 schema 校验与 materialize,落 `_resolved_agent_config.json`;所有 workflow 只消费 resolved object,不得各自再解释默认值。`user_config_echo.json` 与 `usage_reconcile` 都对同一 resolved artifact 对账。
- `.claude/agents/*.md` 只拥有角色职责、工具权限、输出契约和 prompt;阶段顺序由 `scan-market.js` phase 拥有;运行档位由 JSONC 拥有。三者职责不交叉,因此不再需要 `stages.json`。
- stock-research/macro-research full 档本波不纳入该闭集(B5);scan 调用的 lite 角色必须全部纳入。

### B1 nested_probe 裁决(前置件)

跑 `autoresearch/research/nested_probe.py` 10 项(基本调用/args-result/并发上限共享/父取消/子失败/timeout/parent death/resumeFromRunId/task-book lease/幂等),三态账本落盘。**任一 FAIL/UNTESTED → B2 整条不做**,主会话派发维持现状(这不是失败,是证伪省钱——Wave4 家训)。

### B2 L4 派发下沉(B 类,速度 family)

- 设计:scan-market.js 在 L4-prep 后直接 `workflow('l4-stock', {...})` × N(一级嵌套,harness 契约允许;子 agent 计入同一并发帽与 token 预算);主会话职责压缩为「拉起 1 个 workflow → 等完成 → L5 统一相对决策+五条 CLI → CP7」。滑窗语义不回来(Wave11-C 已裁全并发)。
- 失败语义(承 08-03 §4.3 裁定):每股仍是独立可恢复单元;子失败按 task book 重放;重放后仍失败 → assemble 默认阻断,不允许静默缺票。
- 实验治理:registry 登记 `exp_l4_dispatch_nested`(family=speed,与 `exp_l4_full_parallel` 同族串行处置——**先关旧实验再开新实验**,每 family 至多一个 ACTIVE);guards:完成率 N/N、评级产物 schema 零错、主会话成本份额(usage_harvest 可测)下降、总 token 不升、墙钟不升。
- **回滚杆(按 C5 家训写准)**:回滚=①scan_config `performance.l4_dispatch="main_session"`(新开关,缺省 `nested` 只在实验 ACTIVE 后)+②SKILL.md 步骤 4 保留「主会话派发」段为回滚附录并注明两处必须同时动——**config 单独拉不动主会话行为,这是两处联动杆,不是一行杆**。
- 预期收益写为待测假设:主会话唤醒 N+1→2,份额 32.7%→≤15%(待 A/B 实测,不作承诺)。

### B3 配置尾差清零

①所有 scan/t1 workflow 补空 cfg 与缺 role throw(比照 scan-market.js:22-24);②`workflow_literal_lint` 扩展到 `.claude/workflows/**/*.js` 与 `.claude/skills/**/*.md`,禁止 scan 角色出现 `Agent(model=`/`effort=` 字面量(显式测试 fixture 可注豁免);③gp_shell/gp_shell_json 属执行壳机械参数,不属于 agent 档位,允许保留 `SHELL_DEFAULTS`,但三文件常量用 AST 相等断言机器锁同步;④`apply_to_scan_config()` 两选一:接线进 materialize 链或删除+docstring 更正(premise-check 后定,倾向删除);⑤resolved artifact 缺任一实际派发 role 时 `usage_reconcile` 直接 `ok=false`。

### B4 role 归因修(usage_reconcile 盲区)

ens_review/l3_repair 复用父 agentType 导致对账分不清。改法:l4-stock.js/scan-market.js 派发这两类时在 task book 子记录写 `role` 字段;`usage_reconcile` 优先按 task book role 归行,agentType 集合断言降为兜底。验收:离线造 ens_review 与 l4_card effort 不同的 echo,reconcile 必须分行报——现状「同值蒙混」的假阴测试转正。

### B5 full 档收编(裁定点 R-B2,默认不做)

stock-research/macro-research full 档收进 subagent+config 体系 = 把主会话内联扮演改为 workflow 派发。**推荐本波不做**:full 档是低频人触发路径,无 0买/成本病灶;收编收益(effort 可配)不抵改造+回归成本。留候选。

**批B 体量**:B1 半天;B2 1–1.5 天(依赖 B1 全 PASS);B3/B4 半天。B2 是唯一 B 类。

---

## 4. 批C · 报告双层(Q3)

### C0 结论

**双产物 + 确定性核心摘要。** `brief.md`(核心速读,新产物)+ `summary.md`(详细版,重排不减料)。机器消费者不读 summary 正文(§1.4),分层零风险;红线文件(`details/`、`finalists.csv`、`decision_records.json`、`shadow_buys.csv`)一字不动。

**不设收编官 agent(已裁)**:brief 的内容全是结构化结论、计数、评级、tripwire 与账本状态,让 LLM 再压缩会新增编数面、对账 lint 和一次调用成本,却不增加决策信息。核心摘要由模板确定性生成,保证同一 run 重放字节稳定;详细叙事仍留在 summary/details。将来若要润色,只能生成非权威 commentary,不得改写 BUY、数字或风险结论。

### C1 brief.md 内容契约

- **输入白名单**:`decision_records.json`、`finalists.csv`、`run_mode.json`、`market_view.md` 首段、menu_health 行、relative-buy finalizer 产物、near_miss facts、pinned 卡的结构化结论行。生成器禁止读取 trace 大文件和卡片自由文本。
- **结构(硬预算 ≤3,000 字节,self_review 量)**:①市场一句(regime+温度+两尺分歧日提示);②漏斗一行(L0→finalists 数);③**BUY 结论区**(至少一只,展示 `basis=relative`、全市场等权超额、行业中性超额、绝对 gap 预期/实测口径与硬否决状态);④持仓动作表(pinned 逐票:评级/变化/tripwire);⑤风险哨;⑥昨日 delta;⑦欠账红行。
- **语义纪律**:相对 BUY 不得写成「预计绝对上涨」;绝对 gap 为负时必须显示「弱市相对最优」;每个数字都来自白名单字段,禁止生成器自行推断。

### C2 summary.md 重排(减层不减料)

- 决策主线前置:仪表盘(=brief 的 ①②③④ 同源渲染,确定性)→ 投资建议 → 保送持仓 → 差一点/弃权 banner。
- **行业研判节(13.4KB,28%)降级**:每行业一行地形摘要(确定性抽取 brief 的「地形段」首句)+ 指向 `trace/sector_briefs/<行业>.md` 的链接;研判段全文不再嵌入(文件本就单独存在)。**预期 summary 直接 −10KB+**。
- 经验/未决反馈节(8.0KB)压成表格(lesson id/一句话/guard 状态),全文留 `context/knowledge/`。
- 其余节保序;`near_miss` 附录、诚实局限、成本观测不动。
- **实现层**:全部在 `report_sections.py` 确定性完成(节顺序、摘要抽取和 brief 均零 LLM)。

### C3 一致性 lint(self_review 新 check)

①brief.md 存在且 ≤ 预算,缺失=fail;②brief 中每个评级/数字与结构化输入逐项对账,不符=fail;③brief 与 summary 的 BUY 数、BUY code、`basis` 和基准读数一致;④完成扫描日 BUY 数 <1=fail,数据完整性阻断日则整个 run 必须 BLOCKED 而非发布 0 BUY。变异验收:篡改 brief 里一个评级或基准读数,lint 必红。

### C4 入口切换

`index.md` 首行指向 brief.md;CP7 播报改为「读 brief.md 原文 + 附 summary/index 路径」(播报内容=brief,不再主会话现编);`t1-review`/`retro` 不受影响(不读 summary,§1.4 已证)并在两个 skill 文档写明这一点。

**验收(批C 总)**:对 07-31~08-06 六个历史 run 回放渲染 brief+新 summary;同一输入重复生成 hash 一致;人工读 6 份 brief 判「能否 30 秒明白今天该买谁、为什么只是相对 BUY、何时不能买」;对账 lint 变异测试;近 5 run near-miss 缺席问题随批A A8 修复后在回放中出现。
**批C 体量**:1 天;零新增 LLM 调用点。

---

## 5. 批D · 新闻事实层(Q2)

### D0 结论与被拒方案

**推荐:新闻的「独立」落在事实层,不落在判断层。** 微观已经有独立新闻 agent(l4-intel,采集独立+结构性盲,防确认偏误的成功先例)——保持;宏观/中观**不增设**常驻新闻 agent。

- **被拒方案「每海拔一个新闻 agent」**:①判断型新闻 agent = 二手转述损耗+单点确认偏误源(l4-intel 的价值恰恰在「只采集不判断」);②三海拔各自网查的病是**重复采集+不可回放+不可核验**(08-03 §1.0 四点诊断),这是数据层的病,加 agent 治不了;③macro/sector 的研究判断必须由懂该海拔上下文的研究者做,拆给新闻专员会把「新闻→观点」的关键推理断开。
- 真正缺的三件:统一观测清单(catalog)、claim 可核验链路(claim_ledger)、采集去重(intel 先读目录)——**全部已有代码,没通电**(§1.5)。批D 就是通电顺序。

### D1 catalog 通电(I 类,三步)

①`python -m autoresearch.news.catalog inventory` 对既有资产(~1,891 stock_news_em 分片+anns_d/fallback cache)建 manifest,历史分片 `first_seen_basis=snapshot_inferred`(08-03 D1 纪律);②夜间快讯 ingest 进 `nightly_runner`(固定口径全局 feed:东财/新浪/财经早餐三源,B 级降级记账;财联社 404 不做);③覆盖/freshness/非空率报表行进 prelude。**消费者继续关闭**。验收:同一 run+stage 回放稳定;late-arrival fixture 不泄漏;快讯源断线在报表可见。

### D2 文档滞后修(即刻,零风险)

`l3-rank.md:24` 的 news_sent 恒 0 文案按 07-30 后实况改写(先用近 3 个扫描日真数据核实 news_sent 非零率再落笔——premise-check);顺手把 rubric 里新闻列的现行语义(cninfo 兜底、标题级、方向 tag)写准。

### D3 intel 先读目录(B 类,registry 影子)

l4-intel prompt 注入「近 10 日已知事件摘要(来自 catalog)」,指令改「先复用,对更正/缺口再查」;**网查数以真实 tool telemetry 计,不用稿件自报**(claim_ledger 已备该预算件)。实验对照:查询数/耗时/覆盖/错引率/终局卡质量。依赖 D1 跑稳 ≥5 个扫描日。

### D4 L3 公告第二源(B 类,小)

`l3_news` 空桶兜底顺序 目录→cninfo 实时(命中免网络)。依赖 D1。

### D5 宏/中观摘要行(B 类,依赖 D1 数据积累)

macro-brief/sector-brief 的 pack 增「当日快讯 top-N 确定性摘要行」(固定 feed、覆盖归一,选择性逐票抓取禁入——温度 v2 guard 已锁);agent 指令改「先读 pack 摘要行,网查只补增量」。**这一步做完,三海拔的网查从『各自盲搜』变成『共享底座+定向增量』——Q2 的最终形态。**

**批D 体量**:D1/D2 一天(I+文档);D3–D5 各半天但都是 B 类,排在 D1 数据积累后,逐件 registry。

---

## 6. 批E · 隔夜引擎 × 热度召回(Q4+Q5,主战场)

### E0 诊断:「为什么没有 buy」的三层结构答案(证据见 §1.2)

1. **市场层(gap 尺下大体无罪)**:17 连零买期间市场隔夜缺口 ≈0 或微负;肉在日内,而日内不在系统授权内(超短隔夜尺=用户裁定)。弃权 FALSE 只有 4 次且集中在旧尺口径(A8 ④ 翻转后进一步减少)。
2. **供给与决策语义层(真正的病)**:L4 rubric 是「质量-资金-估值」六维绝对框架,弱市里 finalists 净分最高 +1~+3,OW 门槛 +2 + 三门必要条件叠加 + 早停,**正向供给天然稀薄**;而更上游,L1/L2 没有任何已证的隔夜正 alpha 信号可供(A8:全部通道 unique 超额最高 +0.13%,L3 真选 edge 归零)。旧系统实际上回答「有没有绝对强到值得买」,用户现在要求它同时回答「今天全市场相对最值得买谁」;拿一套绝对门处理两个问题,必然长期 0 BUY。换尺后门的错杀率 6.4%/0%说明不能简单放宽,但也不能继续让三门垄断最终 BUY。
3. **仪器层(该回答问题的实验没在跑)**:为「漏没漏肉」立的 EXP-1/EXP-2 预注册后数据腿从未实现(FN-1 家族:消费者在等没人生产的产物);t1 gap 终判零落盘;near-miss 节 5/6 run 静默缺席。**系统性结论被推迟不是因为数据不够,而是因为仪器没接线。**

**因此 Q4 的处方不是调低一个阈值,而是四件事:把仪器接上(E1)、把证据换到对的尺上重问(E2)、给漏斗隔夜原生供给(E3/E4)、把最终 BUY 所有权从单票 OW 三门移到统一相对决策层(E6)。** 旧 R3 中「不为凑数随意松门」继续有效;「三门不动、0 BUY 可作为正常终局」由本次用户裁定替代。

### E0.5 单一 BUY 语义(用户已裁)

- **对外只有一个信号**:`BUY`。不设「质量 BUY / 游资 BUY / 绝对 BUY / 相对 BUY」多套评级;来源差异只写在证据字段。
- **每个成功完成的交易日扫描至少一只**。该契约自 E6 人工 activate 的 schema switch 日起生效;shadow 期旧生产线仍可 0 BUY,但 challenger 必须产至少一只。市场休市不产信号;A 级数据完整性失败、候选合同破坏或最终产物不完整时整个 run 进入 `BLOCKED`,不得以 0 BUY 冒充成功。
- 每日第一只由横截面相对最优产生,元数据写 `basis=relative`。这表示「在今日可交易全集里相对最值得买」,**不承诺绝对上涨**。
- 主评价尺仍是 `gap_c1_o2 = O(T+2)/C(T+1)-1`;主相对标签为 `rel_gap_market = gap_c1_o2 - 当日L0可交易全集等权gap`;辅助标签为 `rel_gap_sector = gap_c1_o2 - 同行业可交易全集等权gap`。所有阶段的 IC、capture、门归因、实验晋升和报告都必须显式绑定这三列中的一列,不得再用含糊的 `t2` 代称。
- brief/summary 同屏显示相对基准和绝对 gap。`expected_abs_gap` 只能取锁定规则版本在 OOS 历史同 score bucket×regime 的均值/区间;样本不足写 `UNMEASURED`,不得让 agent 主观估数。绝对预期为负时固定标「弱市相对最优」,不能改写为绝对看涨。
- 第 2 只及以后不是配额:只有其锁定的相对分数达到已验证阈值,且对应历史样本的绝对 gap 扣成本后为正,才追加同一个 `BUY` 信号。

### E1 影子实验数据腿补接线(修 FN-1,先于一切新建)

- **EXP-2 `sector_momentum` 影子通道**:按现行 `@channel` 装饰器模式落 `scan/recall/channels.py`(registry 的 challenger_pointer 指向 recall 注册表,实现处与之对齐),**floor=0、不占 quota、不写生产 finalists,只落 `shadow/L1_channels_plus_sectormom.csv` 长表**(「默认不启用连副作用一起不启用」判例逐条自查:不碰 DEFAULT_FLOORS、不碰 merit_need、不产 lane 标签);channel_audit `--variant` 即可裁决。registry 观测开始进账。
- **EXP-1 主力 5 日持续性**:建 PIT 5 交易日 loader(moneyflow 湖分区派生,缺任一日→UNMEASURED,assemble 不联网补——Gate0 裁决表原话),challenger 判据(`sum(main_net_yi,T-4..T)>0 ∧ positive_days≥3 ∧ main_distortion=false`)对 gate_participation 人口逐行落 shadow verdict。
- 验收:registry 两条实验 `observations` 数从 0 开始逐扫描日 +1(会变的量断言);变异=把 loader 改错一天窗,UNMEASURED 计数必变。
- **注意**:两实验的成熟门(20 forward days/50 events/2 regimes)从**首条观测**起算,不是从预注册日起算——写进 registry note,防「已经等了 6 天」的错觉复发。

### E2 证据重验:把三个旧尺结论在 gap 尺上重问(M 类,零生产影响)

`ruler_compare` 扩三节(或独立 research 脚本,复用其湖直算原语):

1. **追当日大涨 × gap**:07-21 及全窗口,当日涨幅分桶(≥9.5%/5-9.5%/…)× `gap_c1_o2` 超额。旧结论(−3.67pp t=−11.9)是 oc 尺;gap 尺下「当日强势→隔夜溢价」是完全不同的假设(接近打板策略的隔夜溢价问题)。**结论无论正负都直接决定 E4 通道设计空间**;为负则铁律原样延续,为正也只开影子不开生产(统一成熟门)。
2. **通道 × 相位 × gap**:08-04 相位条件性(momentum/heat 上涨侧有害)是 oc 尺;按 gap 重算两侧区间。喂批F F4 的 quota challenger 预注册。
3. **温度计相位 × 隔夜溢价**:S1 五相位(发酵/高潮/退潮/冰点/修复)对次日 `市场 gap` 的条件分布——回答「隔夜 edge 是否本质上是相位现象」(若是,E3 因子应做相位条件特征而非全时因子)。

产物落 `docs/research/<实施日>-overnight-evidence-gap.md`,每节带 date-cluster CI 与成熟度标注。

### E3 gap 因子工厂第一批(依赖批A A2:晋升面先切尺;喂 E6)

**数据可用即刻(零新采集)**,全部走 O5 统一闸(capability/PIT → factor_lab harvest → calibrate 锁定 OOS → replay → registry):

| 候选因子 | 数据源(已在库) | 假设方向(预注册用) | 备注 |
|---|---|---|---|
| `lhb_net_ratio` | top_list(净买额/成交额) | 游资净买→隔夜溢价(+);机构席位净买反指先例(uzi_lenses)→分席位类型两列 | 龙虎榜盘后披露,PIT 干净 |
| `limit_ladder` | limit_list_d(连板高度/首板/晋级) | 高度×晋级率→隔夜溢价(+),炸板→(−) | 温度计已拉数据,个股化即可 |
| `sealed_strength` | 湖 OHLC(收盘=最高∧涨停价) | 尾盘封死→隔夜溢价(+) | `_board_limit` 原语现成 |
| `rz_buy_intensity` 强化 | margin_detail | 已在 composite(0.02);gap 尺下重验权重 | 07-10 曾是唯一过三门机构因子(oc 尺) |

**需新采集(E4 数据步启动后 ≥20 日才可算)**:人气榜排名Δ、雪球关注Δ。因子通过统一成熟门后进入 E6 的目标对齐证据面,不再作为「是否给 L4 加第七维」的前置件。
**明确不做**:竞价数据(全仓零端点,capability 未证,列 OPEN-Q 探针步:akshare 竞价接口可用性+PIT 语义);seat_db 游资名册(L4 证据面候选,非因子必需)。

### E4 热度/游资影子召回通道族(Q5 正面回答)

- **E4a 快照数据即刻积累(P0,时间敏感,可先于一切独立上线)**:东财人气榜(`stock_hot_rank_em` 族)+ 雪球关注(`stock_hot_follow_xq` 族)新登记 endpoints+contracts(B 级),**as-of lake 每晚快照,`first_seen_basis=observed`**。快照型数据不可回填——**每晚不采就永远没有历史**;即使后续通道被证伪,快照湖也是低成本保险。验收:连续 5 晚非空+日期单调;断采在 prelude B 级降级可见。
- **E4b 影子通道注册**(依赖 E2 结论与 E3 首读,逐条 registry):`youzi_lhb`(龙虎榜净买,排除机构席位反指腿)、`limit_ladder`(连板梯队)、`pop_surge`(人气排名跃升Δ,依赖 E4a ≥20 日)。**全部 floor=0 影子,零生产副作用**(E1 同款自查清单);裁决=channel_audit unique 超额(gap)+ 统一成熟门(≥20 真实扫描日/关键细分 ≥10/unique ≥30/≥2 regime)。
- **E4c 与既有 heat 通道的关系**:heat(成交额×换手分位)gap 尺 −0.14%,是「拥挤度」不是「情绪方向」;新通道与它做 Jaccard 重叠审计(channel_audit 现成),高重叠(≥0.3)时二选一,不并存计数。
- **召回不需要 agent(Q5 后半句的定论)**:五类数据端点全部自带 ts_code、字段结构化,分类逻辑确定性(Wave4 判例:事件端点自带代码,LLM 无事可做);零 LLM 层铁律不破。**LLM 在热度线的位置只有两个**:L4 端已有的 intel 题材梯队面(已在跑),以及(可选、默认不做)批D D5 的快讯摘要行。

### E5 报告诚实面(与批C 联动)

①brief/summary 常驻「旧 OW 基率:历史 9 笔 T+2 胜率 0%」,并与新 relative BUY 账本分开展示,不把定义断层连成一条趋势线;②「两尺分歧日」提示行:当日 `市场 gap` 与 `fwd_2_oc` 符号相反时一句话提示——大涨若发生在日内,不在本系统授权内;③每只 BUY 固定展示 `basis`、横截面名次、`rel_gap_market`/`rel_gap_sector` 的目标口径、绝对 gap 口径、硬否决检查和主要证据来源;④不再存在成功 run 的「0买文案」,只有 BUY 结论或 `BLOCKED` 故障说明。

### E6 统一相对决策层(本波主件)

**所有权**:L1/L2 只提供候选,L3/L4 只提供结构化证据与风险;L5 `decision_finalize` 是 scan 路线唯一拥有最终 `BUY` 的组件。L4 的 `rubric_rating` 在 scan 内降为 `research_rating` 证据字段,不得单独发布为第二套买入信号;持仓的 Sell/UW/Hold 管理语义不变。

**输入**:finalists 的候选护照(F2)+L3 机制/确信度+L4 研究分/证据完整度/风险旗+E3 已过成熟门的 gap 原生因子+当日 regime/temperature 描述性字段。所有字段都来自锁定的结构化产物,finalizer 不读自由文本、不调 LLM、不联网。

**两步决策**:

1. 硬资格只保留四类:入场可交易、A 级数据完整、价格/代码契约完整、重大监管/审计/退市/流动性风险无未解决红灯。原「主力真在/基本面/估值」三门改为有方向的证据分与风险扣分,不再各自拥有一票否决。
2. 对合格票做日内百分位 rank fusion。v1 只合四个等权、可解释的面:隔夜目标对齐证据、召回原生强度与共振、L3/L4 证据完整度、风险安全度;用平均百分位(Borda)得 `relative_decision_score`,并输出逐面贡献。权重、方向、缺失惩罚在观察前锁定;日后任何非等权改动均走 registry,不得边看结果边调。

**出单**:合格票最高分恒为第一只 `BUY(basis=relative)`;并列按目标对齐证据→流动性→代码稳定决胜。第 2 只及以后按 E0.5 的已验证绝对门追加。若全部候选被硬资格否决,run 必须 `BLOCKED` 并暴露各否决分桶,不得发布 0 BUY。

**哨兵改义**:原 sentinel「跳过 L3/L4→0 BUY」与新契约冲突,改为风险预算档——可以缩小深研宽度,但至少保留 3 只非 pinned 候选完成横向比较;有 pinned 时照常全做持仓卡。哨兵只改变研究成本与提示语,不能改变 relative BUY 数量下限。

**产物**:`context/scan/<date>/_relative_buy_decision.json`(候选逐面分、硬资格、排名、basis、基准定义、规则版本、`expected_abs_gap`/CI/样本量或 `UNMEASURED`)+`decision_records.json` 的最终 BUY 行;publisher 只认该文件,缺失或与 records 不一致即 fail。

**上线纪律**:行为属于 rating/decision family B 类。先对既有历史 run 离线回放,再以 shadow 方式前向至少 20 个真实扫描日;报告同时展示旧生产结果与 challenger,但不把 challenger 写进正式 buy ledger。研究/决策/Token/速度/架构五守卫全过并经人工 approve/activate 后,统一切换 BUY 所有权;切换日写 `decision_schema_version` 断层,旧 OW 与新 relative BUY 不混算。

**批E 体量**:E0 即诊断;E1 1 天;E2 1 天;E4a 半天(**最先做**);E3 1–1.5 天(依赖 A2);E4b 半天+等待窗;E6 1–1.5 天+前向观察窗。M/I 类为主,E4b/E6 均走 registry。

---

## 7. 批F · 召回-粗排连贯(Q6)

### F0 粗排质量的重定义

L2 的负结果链完整(模型 OOS 负、composite-top200 四年 ≈0、菜单内无确定性信号)——**「粗排简单」不是病,病是「信息在 L2 被抹平 + 四处实债」**。粗排质量 := ①条件 winner-capture(`wc_l2_given_l1`,现 16.0%)②多样性守卫(行业/通道覆盖)③**信息保真**(每票「因何而来」原样传到 L3/L4/E6)④端到端决策保真(某通道召回的相对赢家最终有没有进入 BUY 比较集)。排序 IC 不是 L2 的单独目标(负结果墓碑),但目标对齐信息不能再被 composite 一列覆盖。

### F1 实债四修

1. **吸筹死配额**(floor 12→0):accumulation 停用 13 个月后桶恒空,12 席经 backfill 回流且被误记 `l2_lane_reserved=True`(三个真消费者被污染:L3 分块/force_full_card/floor_experiment)。**这会改变菜单构成(merit_need 107→119)= 触名单**,处置=replay VariantSpec 对照(工具现成)出菜单 delta 报告 → 附报告请一次性人批(R-F1),不走 20 日实验(修「未启用副作用」判例与 Wave4 critical 同型,修复优先)。
2. **reversal_confirm 处置**:推荐从 `recall_channels` 摘除(显式停用)+ 登记 reopen 条件(vol_ratio_20 接入 L1 帧后重开 A/B)。理由:名义启用实际恒空已 4 周,A/B 对照组空转,channel_audit 读成「没样本」;接因子=新生产因子面(有成本),证据不支持优先。摘除是配置行为,回滚=加回一行。
3. **selection_reason 落盘**:`universe.py:459` l2_cols 补投影两列(修 31 天死分支);l2_slo guards 分布分支活转;`l3_marginal` 的 lane/sector 匹配反事实从此有料。零名单影响(纯新增列)。
4. **列名断层记档**:批A A9 执行,此处引用。

### F2 候选护照贯穿 L1→E6(信息保真主件)

每只 L1 候选生成稳定 `candidate_passport`,在 L2 只增量追加选择信息,不得重建或丢字段。最小契约:

- 召回 provenance:全部 `recall_channels`、每通道原生 score/rank/percentile、unique vs resonance、当日通道 quota/floor 与规则版本;
- 目标对齐证据:各通道在 `gap_c1_o2`、`rel_gap_market`、`rel_gap_sector` 上的成熟度与历史效应桶,薄样本明确 `IMMATURE`;
- 粗排轨迹:`l2_rank`、merit/lane/sector-cap/backfill 的真实 `selection_reason/selection_detail`,禁止把 backfill 冒充 lane 救回;
- 判断轨迹:L3 mechanism/conviction/bench reason,L4 research_rating/证据完整度/风险旗;
- 决策轨迹:E6 四面分、硬资格、最终排名与 BUY 原因。

**两段上线**:I 类先把护照落 CSV/JSON 并保证 prompt byte diff=0;B 类再把必要的 provenance 摘要渲染给 L3/L4,必须先处置现存 `exp_20260729_l3_hard_constraint_f`,同 family 串行。E6 从第一天只读结构化护照,不依赖 prompt 变更。

**边界**:护照保存原生通道语义,但不允许每条通道生成自己的 BUY。所有通道最终在 E6 同一规则下比较。

### F3 per-channel 端到端 capture(M 类)

`l2_slo` 增每通道漏斗曲线:`recall → L2 → L3-pass1 → finalist → L4-qualified → E6-top1/BUY`;每一跳分别算条件 capture、相对赢家 capture 与通道间赢家重叠矩阵。至少回答三问:哪条通道的肉最常被 L2 切掉、哪条在 L3/L4 被证伪、哪条能进入最终 BUY。报警线用 expanding P25,并同时显示分子/分母/as-of/ruler。

### F4 相位条件 quota challenger(B 类,依赖 E2-2)

`recall_quota_regime` family 预注册:上涨相位(发酵/高潮/修复)压 momentum/heat quota,回撤相位不动(08-04 研究方向,**待 gap 复算确认后**才注册——旧尺结论不直接搬)。生成器手法同 consensus 触发器:E2-2 结论满足条件即自动产 PREREGISTERED spec,人批激活。

### F5 非目标(墓碑重申)

composite 整体退役或由单一通道原生排序键替换(被拒:gap 尺下 composite 仍是当前唯一正 unique 超额的路)、L2 上 ML、52 周高距离族(REJECTED 维持)、L3.5 复活、为每个召回通道建立独立 BUY 语义。

**批F 体量**:F1 半天+人批;F2-I/F3 半天;F2-B/F4 依赖前置,各半天+等待窗。

---

## 8. 运营即办清单(零开发,本周内)

1. **今晚跑 `retro` 补 2026-08-06 归因** → event 路(pr_20260725_001)凑满 ≥10 数据日,**裁决从「延期」变「可执行」**(原定裁决日就是 2026-08-08);裁决走 feedback skill 正门。
2. **提案批量裁决 session**:16 条 open(P0 `pr_20260714_006` intel 捏造已挂 22+ 天)——病灶⑥的堵点在人批通道,建议一次 `/feedback 裁决提案` 清账。
3. `l2_slo`/`channel_audit` 报表重跑(C1 换旗后口径已变,旧报表在误导,批A A12 的运营前置)。
4. t1 快环 gap 终判活体核验(批A A8 的运营前置:看今晚 nightly 是否开始回填三列)。

---

## 9. 实施排序、依赖与体量

| 批 | 内容 | 量级 | 前置 | 变更类别 |
|---|---|---:|---|---|
| **E4a** | 快照数据积累(人气/雪球) | 0.5 天 | 无(**最先,每晚不采就没历史**) | I |
| **A** | 尺一致性收口 A1–A12 | 1.5–2 天 | 无 | M |
| **F1/F2-I/F3** | 粗排实债修 + 候选护照 + 端到端 capture | 1–1.5 天 | F1-1 需人批 | M + 1 项触名单修复 |
| **E1** | EXP-1/2 数据腿接线 | 1 天 | 无 | I(影子) |
| **E2** | 三个旧尺结论 gap 重验 | 1 天 | 无 | M |
| **E6-shadow** | 统一相对决策层+历史回放+前向影子起账 | 1–1.5 天 | A2 + F2-I | B(影子) |
| **C** | 确定性 brief + summary 重排 | 1 天 | A8 + E6 产物契约 | M,零 LLM |
| **E3** | gap 因子工厂第一批 | 1–1.5 天 | **←A2(晋升面切尺)** | M(研究) |
| **E4b** | 热度影子通道注册 | 0.5 天 | ←E2/E3 首读 | B(影子,零副作用) |
| **B** | 编排收口(probe→下沉→尾差) | 2 天 | B2←B1 全 PASS | B2 是 B 类 |
| **D** | 新闻事实层 D1→D5 | 1 天 + 等待窗 | D3-5←D1 积累 | I→B 逐件 |
| **F2-B/F4** | 护照摘要进 prompt / 相位 quota | 各 0.5 天 | ←E2-2;l3_prompt family 串行 | B |
| **E6-activate** | 人批切换最终 BUY 所有权 | 等待窗后 | ≥20 日+五守卫 PASS | B,人工批准 |

**推荐主线**分两层,不再让配套工程稀释 0 BUY 主问题:

1. **核心闭环先成形**:`E4a → A → {F1/F2-I/F3 ∥ E1/E2} → E6-shadow → C → {E3/E4b}`。E6 影子尽早起账,新证据成熟后按版本增量接入。
2. **配套随后**:`B → D → F2-B/F4 → E6-activate`。B/D 不阻塞相对 BUY 的取证;任何 B 类仍按 family 串行。

工程量仍约 10–12 个工作日,另加 E4a/E4b 数据积累与 E6 ≥20 日前向观察窗。每批独立可回滚;正式 BUY 所有权在 E6 人工 activate 前不切换。

---

## 10. 验收总表(实施批照抄)

- **A**:错尺/错腿变异探针逐条红→绿;`sortcol` 断言进 test_factor_lab;0买 verdict 重放与两尺对照报告 ④ 一致;lessons 全带 ruler 字段;文档 grep 三分类归尽;lint 对新建旧尺裸写文件能红;t1 gap 三列真实落盘 ≥2 日;shadow_buys 入 nightly 后 near-miss 节回放出现。
- **B**:nested_probe 10/10 三态落盘;(若 PASS)N=10 真实扫描完成率 N/N、评级 schema 零错、主会话份额实测对比;t1-review.js 空 cfg throw 测试;reconcile 分行报 ens_review 变异。
- **C**:六历史 run 回放出 brief;同输入 hash 一致;对账 lint 变异必红;brief ≤3,000B;BUY code/basis/两类相对基准与 E6 产物一致;CP7 播报=brief 原文。
- **D**:inventory manifest 与分片逐源对账;快讯 ingest 连续 5 晚非空;D3 起逐件 registry 记录在案。
- **E**:E4a 连续 5 晚快照非空+断采可见;E1 两实验 observations 逐日递增;E2 报告三节带 CI 落盘;E3 每因子 capability/PIT 结论+gap 晋升判据;E4b 每通道 floor=0 自查清单过+shadow 长表出现;E6 历史回放同输入同排名,每个非 BLOCKED 交易日 `BUY_n≥1`,全部候选硬否决时 run=BLOCKED,绝不发布 0 BUY;前向账同时报绝对 gap、`rel_gap_market`、`rel_gap_sector`、左尾与 action coverage。
- **F**:F1-1 replay 菜单 delta 报告+人批记录;selection_reason 列出现在 L2 CSV 且 l2_slo guards 活转;F2-I prompt byte diff=0 且候选护照从 L1 到 E6 code/版本/provenance 无断行;F3 每通道六跳 capture 带分子/分母/as-of/ruler;F4 spec 生成条件与 E2-2 结论逐字对应。

---

## 11. 风险与对冲

| 风险 | 批 | 对冲 |
|---|---|---|
| A2 换晋升尺后现有因子组资格大洗牌,权重震荡 | A | 改组另案 B 类走 registry;本批只换裁决尺出对照报告,不动 `_GROUPS` |
| 隔夜尺样本噪声大(单夜窗),E3 因子假阳 | E | 统一成熟门不放松;date-cluster CI;两半同号;负结果照记 |
| 热度快照数据源脆弱(爬虫类接口) | E4a | B 级降级记账;双源(东财+雪球)互备;断采只损失该日,不阻断扫描 |
| 追涨 gap 复算若为正,被误读为「可以追涨」 | E2 | 报告顶写死「影子取证专用,生产铁律未变;启用唯一路径=registry」 |
| 相对 BUY 被误读为绝对上涨承诺 | E6/C | BUY 行强制同屏 `basis=relative`、绝对 gap 与两类相对基准;绝对为负固定写「弱市相对最优」 |
| 每日最低一只把弱证据包装成高确信 | E6 | action coverage 与收益质量分账;第一只只承诺横截面最优,不承诺绝对 edge;前向账单列左尾与绝对胜率 |
| rank fusion 等权先验没有 alpha | E6 | v1 明示 baseline,先历史回放+20 日 shadow;只允许成熟证据进入目标面,任何改权重走 registry |
| L4 research_rating 与最终 BUY 形成双信号 | E6 | publisher 只认 `_relative_buy_decision.json`;research_rating 仅作为证据字段,不得独立渲染成买入建议 |
| 嵌套下沉后子 workflow 故障语义与主会话派发不同 | B2 | B1 十项探针全 PASS 才动;assemble 阻断缺票语义不变;两处联动回滚杆写准 |
| 确定性 brief 过度压缩语境 | C | 只放可执行结论并链接 summary/details;不允许为了文采扩大 brief 输入面 |
| 吸筹 floor 修除改变菜单被低估为「纯修复」 | F1 | replay delta 报告先行+显式人批;lane_reserved 三消费者回放对照 |
| 文档大扫误伤沿革记录 | A10 | 「活指令改/纯沿革标注」两分法;历史 specs/research 不改写(R7 继承) |
| 产品语义已裁被误当成允许直接上线 | 全 | §13 分开「产品裁定」与「生产激活」;B 类仍须 registry+人工 approve/activate |

---

## 12. 非目标(本稿不碰)

- 不把 OW 三门逐项机械降阈值来凑单;本稿重构的是最终 BUY 所有权与门的职责,不是给旧门开后门;
- 不推 swing/T+5(07-10/08-05 裁定);不做日内(隔夜尺=授权边界;两尺分歧只做可见性);
- 不重启负结果:L2 上模型、业绩预告通道、northbound/accumulation、52 周高线性因子、追当日大涨(oc 证伪;gap 侧只做 E2 取证);
- 不复活:观察单日检、L4 TTL 复用、L3.5;
- 不做:多套 BUY 信号、LLM 最终选票、港美股、付费 API、seat_db 游资名册(候选)、竞价通道(数据未证)、全拆四段 workflow、stages.json 第二事实源、每海拔新闻判断 agent;
- 不承诺 relative BUY 一定绝对上涨;「每天至少一只」只验 action coverage,收益质量仍由 `gap_c1_o2`、两类相对超额与左尾独立验收。

---

## 13. 裁定记录与生产批准边界

| # | 状态 | 裁定/推荐 | 生产边界 |
|---|---|---|---|
| R-E1 | **用户已裁** | 对外只有一个统一 `BUY`,不按策略拆信号 | E6 为唯一最终 BUY owner |
| R-E2 | **用户已裁** | 每个成功完成的交易日扫描至少一只;最低一只是相对 BUY | 无合格票则 BLOCKED,不能发布 0 BUY |
| R-E3 | **用户已裁** | 主相对基准=全市场可交易股票等权;行业中性超额为辅 | 主尺仍严格是 `gap_c1_o2` |
| R-E4 | **用户已裁** | 现有 OW 三门可以调整;改为证据分/风险扣分,只留四类硬资格 | E6 属 B 类,shadow+五守卫+人工 activate 后才切生产 |
| R-C1 | **用户已裁** | brief 双层保留,核心 brief 确定性生成,不设收编官 agent | 零新增 LLM;对账 lint 必过 |
| R-B1 | **方向已裁** | 不按每阶段拆 workflow;保单壳,嵌套探针全过后才下沉 L4 派发 | B1 任一非 PASS 则维持现状 |
| R-D1 | **方向已裁** | 新闻事实层独立,判断留在宏/中/微研究者,不设常驻新闻判断 agent | D3–D5 逐件 registry |
| R-B2 | 本波不做 | stock-research/macro-research full 档暂不收编 | 留候选 |
| R-E5 | 待执行批准 | 人气榜/雪球快照从今晚起积累 | E4a 写外部湖,实施前确认端点与降级契约 |
| R-F1 | 待名单 delta 人批 | 吸筹死配额 floor 12→0 | replay delta 报告随附后批 |
| R-F2 | 待配置人批 | reversal_confirm 摘除并登记 reopen 条件 | 回滚=配置加回 |
| R-X1 | 设计内采用 | 「两尺分歧日」提示行进 brief/summary | 纯展示 |

---

_沿革:承接 Wave11(2026-08-05 稿,四批已落地)、Wave10(2026-08-01 稿,A/B/C 已落地、实验成熟窗继续)、2026-08-03 候选池(26 件已代码化,B 类 0 激活);0买诊断承接 07-11 漏斗六问、07-12 L3 无 alpha、07-25 Wave4 事件路实证、2026-08-07 两尺对照报告。六路现状取证(workflow 配置/召回粗排/0买/报告/尺子/新闻)完成于 2026-08-08,数字均抄自当日真实产物。仅供研究,非投资建议。_

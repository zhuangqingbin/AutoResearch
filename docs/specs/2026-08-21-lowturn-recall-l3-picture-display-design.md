# 低位转强 —— 召回补腿 × L3 第三画像 × 出手线展示对齐 —— 设计稿

- 日期：2026-08-21
- 状态：**已实施**（2026-08-21，分支 `feat/lowturn-recall-l3-picture`，19 commits，4075 绿 / 基线 4003）。Gate 0 已跑：**PROCEED**，但决策尺读数为显著负——见 `docs/research/2026-08-21-lowturn-precheck.md` §3 与下方 §16。**§10 裁决表 R1–R12 按建议默认值实施；用户仍可逐项改判**（P3 回滚 = `scan_config.jsonc` 的 `l3.lowturn.enabled` 翻 false 一行）
- 调度权威：本稿只管本波四个批次（P0–P4）；总调度权威仍是 `docs/specs/2026-08-18-e6-activation-learning-slimdown-design.md`（E6 规则、删除面、裁决表 A/B/C/D 均不在本稿重开）
- 立案依据：2026-08-10→08-20 八个扫描日 staging 全量实测（§2.1）+ 源码侦察（§2.2–2.5，全部带 file:line）+ 用户 2026-08-21 原话（§1）
- 配套实施计划：`docs/plans/2026-08-21-lowturn-implementation-plan.md`（本稿批准后由 writing-plans 产出）

---

## 0. 一句话

八个扫描日的产物证明：漏斗前半段是**偏低位**的（L2 菜单 60 日涨幅中位 −14%~−20%，比全市场还低，反转路每天 12~21 席），而 L3 的两条硬约束把它**整个翻转**成「只要已经涨起来的」（finalist 落全市场 80~98 分位、above_ma60 全 1，reversal/momentum/heat/main_fund 四路 8 日 **0** finalist）。用户想要的「低位向上趋势」对应的唯一通道 `reversal_confirm` **从未在生产跑通**（起爆硬门列 `vol_ratio_20` 从没接进 L1 帧，恒空 4 周后于 08-19 摘除），且即便接上，它的门还有**第二处自相矛盾**（§2.3）会让它继续近空。同一个病的另一面：候选池=「已涨 + 当日刚冲高」，在 1~2 日隔夜尺下 L4 正确地一直否，E6 只能在里面挑「最不差」贴 BUY，而 `summary.md` 的旧「0 买·空仓观望」段与 brief 的 ✅ relative BUY 同一份报告里打架。

本波做四件事，按依赖排序：**P0 展示层同源**（30 分钟的事，先止血）→ **P1 数据腿**（60 日面板 + 十个新列，所有后续的共同前提）→ **Gate 0 前置证伪**（零 LLM，≥60 成型日回测，先问「这把尺子量得到它吗」）→ **P2 修门重开 `reversal_confirm` A/B + 恒空探针** ∥ **P3 L3「低位转强」第三画像（旗 + 硬约束改写 + 确定性守卫⑥）** → **P4 双尺分账（只观察不决策）**。

---

## 1. 用户裁定与继承约束

| # | 裁定 / 约束 | 来源 | 对本稿的含义 |
|---|---|---|---|
| U1 | 「为什么每次推荐的股票感觉都是在高位的，没有低位向上趋势的股票挖掘吗」 | 2026-08-21 原话 | 产品诉求：要看到**低位刚转强**的票。与 07-17「不要跌势票」裁定**不矛盾**，是它的精确化：**不要「还在跌的」≠「只要已经涨的」** |
| U2 | 「怎么还是没有 buy 的」 | 同上 | 出手线**可读性**问题：研究评级（证据）与 E6 相对 BUY（决策）两套口径在报告里未对齐（§2.5） |
| U3 | 「那去修复，先做出详细的开发文档」 | 同上 | 本稿；批准后走 plan → 实施 |
| C1 | 持仓=超短 1~2 日；主尺 `gap_c1_o2`（T+1 收盘买 → T+2 开盘卖） | 07-10 / 08-05 裁定 | **决策尺不变**。低位趋势票的兑现天然是周级，1~2 日尺量不到——本稿用「双尺分账、只观察不决策」（P4）说破这条边界，不推 swing |
| C2 | 不要跌势票（死叉/均线下/主力净出的票不当 pick） | 07-17 裁定，落在 `l3-rank.md:30` B 条 | 保留 B 条主体；为「已确定性核过站回均线、放量、主力转正」的票开**明确定义的例外**（§6.2） |
| C3 | `.claude/**` 只在用户显式发起的开发会话中修改（B 档） | 08-13 裁定，`tests/test_skill_docs_refs.py:63-89` | 本波是用户发起的开发波，**可以**改 `l3-rank.md`；`tests/test_agent_defs.py:110-119` 的锚必须保留 |
| C4 | `scan_config.jsonc` = 唯一参数事实源；新参数三件套=白名单+消费点+测试 | 08-11 裁定 | `l3.lowturn` 新键按三件套落（§6.1） |
| C5 | E6 规则观察前锁定：改打分/选择 = 新 `RULE_VERSION` + 人批 | `relative_buy.py:27-36` | 本波**不碰** E6 打分与选择（`RULE_VERSION` 维持 `e6.v2.0`）；只改报告渲染 |
| C6 | 召回/L3 改动属行为变更，先走实验治理：影子呈证 → proposal 人批 → 开发会话落地 | `STAGES.md:226-238` | 本稿即 proposal 形态；P2/P3 上线后 ≥10 扫描日账本裁决（§8） |
| C7 | 任何新因子/新通道先进 `docs/research/factor-backlog.md` 过 IC 门 | 该文件 :3 | Gate 0 就是这道门（§7），负结果必须归档 |

---

## 2. 数据诊断（立案证据）

### 2.1 八个扫描日的「位置」读数（2026-08-10→08-20，`L1_scored_full.csv` 实测，中位数）

| 层 | n | pct_60d | pct_ytd | rsi6 | winner_rate | above_ma60 |
|---|---:|---:|---:|---:|---:|---:|
| 全市场 | ~4300 | −11.1 ~ −14.1 | −12.2 ~ −14.7 | 39 ~ 64 | 32 ~ 67 | 0 |
| L1 召回 1000 | 1000 | −13.3 ~ **−21.8** | −15.1 ~ −26.3 | 27 ~ 55 | 15 ~ 54 | 0 |
| L2 菜单 200 | 202 | −13.3 ~ **−20.3** | −13.6 ~ −24.2 | 28 ~ 51 | 15 ~ 49 | 0 |
| L3 pass1 留 40 | 40 | −8.4 ~ **+0.2** | −11.2 ~ −4.3 | 37 ~ 51 | 28 ~ 62 | 0/1 |
| **finalist** | 7~8 | **+1.1 ~ +10.2** | −15.2 ~ +17.0 | 45 ~ 65 | 43 ~ 67 | **1** |

- finalist 的 pct_60d 落全市场 **80~98 分位**（58 只里仅 5 只 <80 分位）；当日中位 +1.0 ~ +3.7%，个股 +5 ~ +16% 的冲高日被选中常见（西藏药业 +10.0%、雪榕生物 +16.4%、甘咨询 +10.0%、普冉 +13.4%）。
- 通道构成（8 日 58 只 finalist）：**healthy 41**、value 10、growth 4、accumulation 2、main 1（📌持仓长飞）、**reversal 0、momentum 0、heat 0、main_fund 0**；L2 菜单里 reversal 每天 12~21 席。
- 08-20 pass1：切掉 162 只里 **151 只 pct_60d<0**（reversal 路 14 切 / 3 留）；留 40 里 20 只负 → judged 7 → finalist **0**；`reversion` lane 5 只全在 bench，conviction 36~50，triage 全 Hold/UW。

### 2.2 翻转发生在 L3，且是写死的

- `.claude/agents/l3-rank.md:29`（硬约束 A）：finalist 中「健康上涨」（pct_60d 0~40 + main_net>0 + cmf/obv 同向正）占比 ≥1/3，稀缺时**优先纳入并排前列**。
- `l3-rank.md:30`（硬约束 B）：死叉/价在所有均线下/main_net<0 一律不选；pct_60d<−20 且无主力直接弃；「用户不想在报告里看到任何下跌趋势票被当 pick」。
- `l3-rank.md:31`（C）：超卖反转簇可留 1–2 只龙头，但仍须满足 B 的吸筹/催化门槛——8 日实测 0 只满足。
- `autoresearch/scan/l3/triage.py:116-121`：pass1 把 `recall_channels` 含 `healthy` 的行**全量强留**（mandatory），其余通道轮询补位。
- `autoresearch/scan/l3/merge.py:166-169`：守卫④ `healthy_quota`（`ceil(n/3)`）确定性兜底；守卫⑤ trend soft 2 席。**没有任何一条守卫为低位侧留席。**
- L4 侧（`.claude/agents/l4-card.md`、`_l4_shared_instructions.md`、`scan/l4/*`）**没有**跌势偏置——翻转只在 L3，修 L3 即可让新画像到达 L4。

### 2.3 「低位 + 企稳 + 起爆」通道从未跑通，且门有第二处矛盾

- 通道 `reversal_confirm`（`scan/recall/channels.py:46-51`，注册 quota 200/floor 50）于 08-19 从 `scan_config.jsonc:90` 摘除，reopen 条件（`STAGES.md:298`）：「`vol_ratio_20` 接入生产 L1 帧后重开 A/B」。
- `common/scoring.py:194-275 lens_reversal_confirm` 四段：① 前置低位 `pct_60d≤−25 ∨ dist_low_60≤15`（:234-236）② 衰竭企稳 `days_no_new_low≥10 ∧ vol_ma5<vol_ma20 ∧ 20≤rsi6≤50`（:238-252，三子项 presence-gated，缺列=不拦）③ **起爆硬门** `vol_ratio_20≥1.5 ∧ ma_bull>0`（:255-260，缺列=整段 False，不可跳）④ 可交易（:263-267）。
- 生产 L1 帧缺的列：`vol_ratio_20`（**致命**）、`dist_low_60`、`days_no_new_low`、`vol_ma5`/`vol_ma20`、`buyable`——所以 ①②④ 全退化成最宽、③ 恒 False，通道诚实地每天空召回。
- **第二处矛盾（本稿新发现）**：③ 用 `ma_bull`（`tushare_source.py:196` = MA5>MA10>MA20>**MA60** 全多头排列）当「起爆日站上 MA20」的代理。一只 60 日跌 ≥25% 的票在起爆当天 **MA20 不可能高于 MA60**（那要再涨几周）——①与③在定义上几乎互斥。**只接 `vol_ratio_20` 不改门，通道会继续近空**，reopen 等于白开。
- **第三处**：② 的 `vol_ma5<vol_ma20` 若两者都截止到 D 日，起爆日的巨量会把 5 日均量顶上去，恰好在起爆日让「缩量企稳」判 False；`20≤rsi6≤50` 在放量起爆日也多半不成立（RSI6 跳到 55~80）。两条都得按「D−1 截止 / 放宽上限」重写（§5.1）。
- `factor_lab.reversal_confirm_factors`（`research/factor_lab.py:336-376`）已有三因子离线参考实现（`vol_ratio_20`=D 日成交额/近 20 日均成交额；`dist_low_60`=收盘相对 60 日滚动最低 low 的溢价%；`days_no_new_low`=连续未创 60 日新低天数），且在 `CANDIDATES`（:689）里——**但 IC 只在旧尺 `fwd_2_oc` 下跑过**（docstring :200-201 自述「重启该因子前须先复跑」）。旧结论：`dist_low_60` 对 `fwd_2_oc` **反预测**（decile spread t=−2.06）=「光有前置低位=接刀」。
- lesson `ls_l2_cuts_oversold_sector_rotation`（conf 0.87，`context_claude/knowledge/lessons.jsonl`）早记录了这条拒绝梯度（06-22 被切 51 只医药超卖反转后来 +10~22%；07-15 拒绝梯度 38%→83%→100%）。

### 2.4 数据腿现状（实施可行性）

- L1 帧唯一装配路径 `scan/frame.py:120 build_market_frame` → `:47 _harvest_vol_series(lookback=20)`：从 `lake/daily/`（**1085 个全宽分区，2022-03-02→2026-08-19**，`open/high/low/close/pre_close/change/pct_chg/vol/amount`）拉 20 个交易日 → pivot → `cmf_20/obv_mom_20/price_vs_vwap_20/breakout_vol_20`（:107-116）。**把 `lookback` 调到 60 即有 60 日面板**，已结算日全部湖命中零网络；`prewarm._frame_lake`（`scan/prewarm.py:47-50`）夜间预填。
- CSV 列是硬白名单 `scan/universe.py:476-483 keep`：**算了不等于落盘**（`price_vs_vwap_20`/`breakout_vol_20` 至今算了没落）。新列=两处改：算 + `keep`。
- `ma_bull`/`above_ma60` 来自 `stk_factor_pro` 单日快照的 `ma_qfq_5/10/20/60`（`tushare_source.py:196-197`），原始 MA 被丢弃——`above_ma20`/`ma5_gt_ma10` 在同一处旁加两行即可，与现有两旗**同源同复权口径**。
- 契约：A 级出帧契约只锁 `_FRAME_CORE`/`_FRAME_VOLPRICE`（`data/contracts.py:333-334`）；新列走 B 级（缺=记账不阻断）。lake 窄表毒化规则（`data/cache.py:141-157`）对 `daily` 派生列无影响（分区全宽，**不需 purge**）。
- 测试地基：`tests/scan/_synth_universe.py`（合成帧，新列必须加）、`tests/common/test_scoring.py:158-224`（reversal_confirm 7 条，含 `:212 missing_vol_ratio_20_column_rejects_all`）、`tests/scan/test_recall_channels.py:94-128`（含 `:123 degrades_to_empty_without_vol_ratio_20`）、`tests/scan/test_universe_l2_cols.py`（加列进 `keep` 的先例）。

### 2.5 出手线展示（U2 的直接病灶）

- `scan/market.py:560-585`「📉 今日漏斗读数」按**卡面评级 ≥OW** 数买单，0 则印「0 买：N 只 finalist 深核后无一进买单 —— <regime> regime 下当前采取**空仓观望**」。08-19 起 E6 已 `active`（`scan_config.jsonc:168`），同一天 brief ③ 印 ✅ relative BUY：08-19 中国石油（卡面 Hold）、08-20 金螳螂（卡面 Underweight；#1/#2 是协创、厦钨两只📌持仓被 `exclude_pinned` 剔除）。**同一份 summary.md 里「空仓观望」与「BUY 1 只」并存。**
- 旧绝对门成绩（`reports_claude/learning/relative_buy.md`）：旧 OW 9 笔、已实现 3 笔 **T+2 胜率 0%、均值 −0.70%**；影子「若门不拦」−4.07% vs 真实 −0.21%——**旧门不出 BUY 是在正确地拒绝**。
- 相对 BUY 账：成熟 n=11，`gap_c1_o2` 均值 +0.99% / **中位 0.00%**，`rel_gap_market` 6/11 为负；正收益主要来自 07-29 普冉 +20%（📌持仓）一笔。**转正是产品裁定，不是 alpha 证明**（08-18 稿 U2 已明言）。
- brief 侧已有 `WEAK_MARKET_PHRASE="弱市相对最优"`（绝对 gap 为负且 MEASURED 时触发）与禁词表（`tests/scan/test_brief.py:216-250`）；`MAX_BYTES=3000`。**brief 本波不动**（预算 + sources 边表 lint），只修 summary 段（§3）。

---

## 3. P0 · 展示层同源（E0，独立 commit，先止血）

**目标**：`summary.md`「📉 今日漏斗读数」与 E6 决策同源；研究评级是证据、BUY 是决策，两句话分开写，**不再出现与 ✅ relative BUY 矛盾的「空仓观望」**。

**改动点**（`scan/market.py:560-585`，函数 `render_funnel_readout`，由 `report_sections.py:1067` 懒加载）：

- 判定 `e6_live := load_user_config().relative_buy.mode=="active" ∧ date ≥ activate_date ∧ _relative_buy_decision.json 存在`。相对 BUY 事实的读取**复用 brief 现有的 `_relative_facts`**（`scan/brief.py:486`，读 `relative_buy.DECISION_FILENAME`）——把它与 `BANNED_RELATIVE_PHRASES`/`WEAK_MARKET_PHRASE`（`brief.py:110-112`）一起抽到 `scan/relative_buy.py`（或新 `scan/relative_facts.py`），brief 与 market **同一个函数、同一张禁词表**；**不另写第二个解析器**（两处解析各自错的前科：brief↔summary 一起错一起绿，见 memory `brief-reads-provisional-relative-buy`）。抽取后 brief 侧 `test_semantic_constants_are_pinned_literals`（`test_brief.py:226`）的字面量断言照旧成立。注意 `report_sections` 对 `market`/`brief` 都是懒 import 防环（:983/:1067），market 不得直接 import brief。
- `e6_live` 且 `buys[]` 非空 → 首行改为：
  `- **研究评级 ≥OW 0 只**（证据，非决策）· **相对 BUY 1 只**：金螳螂 002081（卡面 Underweight；basis=relative，不承诺绝对收益为正）—— 决策口径见 🧭 ③`
  保留「机制拆分」与「日级弃权裁决」两行（它们回答的是「为什么没有 ≥OW」，仍成立）。
- `e6_live` 且 `blocked` → `- **相对 BUY BLOCKED**（硬否决：<blocked_reasons>）· 研究评级 ≥OW 0 只`。
- 非 `e6_live`（08-19 前的历史 run、shadow、决策文件缺席）→ **逐字 parity**（现有 `tests/scan/test_zero_buy_narrative.py` 三条不改即绿，因其 fixture 无决策文件）。
- 禁词：与 brief 同一张 `BANNED_RELATIVE_PHRASES` 表（从抽出的公共模块 import，不复制字面量），新段渲染后机检不得含禁词。

**测试**（`tests/scan/test_zero_buy_narrative.py` 追加）：
1. active + BUY → 输出含「相对 BUY 1 只」与票名，**不含**「空仓观望」「无一进买单」；
2. active + BLOCKED → 含「BLOCKED」与原因；
3. 无决策文件 → 与改前字节一致（parity 锁）；
4. 变异探针：把 `e6_live` 判定恒 False，测试 1 必须变红。

**不做**：brief ③ 行不动；`details/` 卡不动；E6 `rank` 语义不动（「合格内 #3/6」是 `exclude_pinned` 保留排名的既定行为，见 §10 R11 可选项）。

---

## 4. P1 · 数据腿（D1，所有后续批次的共同前提）

### 4.1 单一实现模块 `autoresearch/common/turnup.py`（新）

把「低位 / 企稳 / 起爆 / 转强」的全部数学放在**一个模块**，三个消费者共用：L1 帧装配（生产）、`factor_lab`（研究，改为委托）、L3 旗（`lowturn_flag`）。理由：仓库已两次为「两层各造一套词表」付过学费（`l2_stratify.py:39-52`、`triage.py:8-10` 原文自述）。

面板因子（输入 = `_harvest_vol_series` 的 code×date pivot：`high/low/close/amount`，窗口 ≤D，无前视）：

| 列 | 定义 | 口径说明 |
|---|---|---|
| `vol_ratio_20` | amount[D] / mean(amount[D−19..D]) | 镜像 `factor_lab:363`；分母 0/NaN→NaN。**不是** `vol_ratio`（tushare 5 日量比，已被 IC 门剔除，勿复用旧名） |
| `dist_low_60` | (close[D] / min(low[D−59..D]) − 1)×100 | 镜像 `factor_lab:370`，恒 ≥0 |
| `dist_high_60` | (close[D] / max(high[D−59..D]) − 1)×100 | 新增，恒 ≤0；「跌过」的广义判据 |
| `days_no_new_low` | 截至 D 连续未创 60 日新低天数 | 镜像 `factor_lab:374` |
| `vol_ma5_prev` / `vol_ma20_prev` | mean(amount[D−5..D−1]) / mean(amount[D−20..D−1]) | **截止 D−1**，专供「起爆前缩量」判定（§2.3 第三处矛盾的修法） |
| `pct_5d` / `pct_20d` | close[D]/close[D−5]−1、close[D]/close[D−20]−1（%） | 转强幅度 |

快照旗（`data/tushare_source.py:196-197` 旁加两行，与 `ma_bull`/`above_ma60` 同源 `ma_qfq_*`）：`above_ma20 = close > ma_qfq_20`、`ma5_gt_ma10 = ma_qfq_5 > ma_qfq_10`。

两个谓词（同模块，纯函数，行级可单测）：

- `reversal_confirm_gate(row)`：供 L1 通道（§5.1 修后四段）。
- `lowturn_flag(row, cfg)`：供 L3 旗（§6.1），阈值来自 `scan_config.jsonc` `l3.lowturn`。

`factor_lab.reversal_confirm_factors` 改为委托 `turnup` 同名实现；**先加一条等值契约测试**（旧实现 vs 新实现在同一 fixture 上逐元素相等），再删旧体——研究侧历史读数不得因重构漂移。

### 4.2 L1 帧接线

- `scan/frame.py:47` `lookback` 20→**60**。20 日组（`cmf_20/obv_mom_20/price_vs_vwap_20/breakout_vol_20`）**仍只喂最后 20 列**——加一条契约测试：同一 fixture 下 `lookback=60` 与 `lookback=20` 的四个 20 日列**逐元素相等**（composite 与 L1/L2 名单 byte-identical，P1 自身零行为变更）。
- `_VOL_MIN_DAYS=10` 不动（A 级 20 日组的底线）；新增 `_TURNUP_MIN_DAYS=40`：面板不足 40 日 → 新列整列 NaN，写入 `run_health.json` 降级记账（B 级，**不抛** `DataContractError`）。
- `scan/universe.py:476-483 keep` 追加：`vol_ratio_20, dist_low_60, dist_high_60, days_no_new_low, vol_ma5_prev, vol_ma20_prev, pct_5d, pct_20d, above_ma20, ma5_gt_ma10`（10 列）。`scan/artifacts.py:39` `l1_full` `schema_version` 1→2。
- `scan/health.py:28-30 _FACTOR_COLS` 追加 `vol_ratio_20`（NaN 率体检，B 级）。
- `research/replay.py:76 _CRITICAL_COLS` **不动**（新列非 replay 完整性门）。

### 4.3 测试

- `tests/common/test_turnup.py`（新）：每个因子的边界（窗口不足、分母 0、创新低日 `days_no_new_low=0`、`dist_high_60≤0`、`vol_ma*_prev` 不含 D 日）；两个谓词的真值表；变异探针（把 `above_ma20` 条件删掉，`lowturn_flag` 测试必须红）。
- `tests/scan/test_frame.py` 追加：lookback=60 的 20 日列等值锁；`_TURNUP_MIN_DAYS` 降级路径记账不阻断。
- `tests/scan/_synth_universe.py` 加新列；`tests/scan/test_universe_l2_cols.py` 加 10 列存在性断言。
- `tests/research/test_factor_lab.py:269-320` 三因子测试保持绿（委托后数值不变）。

### 4.4 成本

60 日面板 = 5000 码 × 60 日 × 4 字段的 pivot，内存 MB 级；已结算日零网络；首次运行多拉 40 个分区（全部已在湖里到 08-19）。prelude 的 L0/L1/L2 段实测 17 分钟（08-20），预期增量 < 1 分钟。

---

## 5. P2 · 召回层：修门、重开 `reversal_confirm` A/B、恒空探针

### 5.1 门修法（`common/scoring.py:194-275`，行为变更，属本波实验）

| 段 | 现状 | 修后 | 为什么 |
|---|---|---|---|
| ① 前置低位 | `pct_60d≤−25 ∨ dist_low_60≤15` | 不变 | 通道是「困境反转」，保持严 |
| ② 衰竭企稳 | `days_no_new_low≥10 ∧ vol_ma5<vol_ma20 ∧ 20≤rsi6≤50` | `days_no_new_low≥10 ∧ vol_ma5_prev<vol_ma20_prev ∧ 20≤rsi6≤85` | 缩量看**起爆前**；RSI 上限对齐 `lens_momentum` 过热线 85（:119），下限 20 仍挡「还在超卖里掉」 |
| ③ 起爆硬门 | `vol_ratio_20≥1.5 ∧ ma_bull>0` | `vol_ratio_20≥1.5 ∧ above_ma20>0 ∧ ma5_gt_ma10>0` | `ma_bull` 含 MA20>MA60，与 ① 互斥（§2.3）；新代理=「站回 20 日线且短均线拐头」。**仍不 presence-gated**：三列任一缺 → 整段 False，诚实空召回 |
| ④ 可交易 | 不变 | 不变 | — |

评分权重（30/30/40）不变；`reversal_confirm_signals` 文案同步。`tests/common/test_scoring.py:158-224` 七条按新门改 fixture；新增「ma_bull=0 但 above_ma20∧ma5_gt_ma10=1 的 −30% 票应过 ③」与「vol_ma5_prev≥vol_ma20_prev 应拒」两条。

### 5.2 重开（`scan_config.jsonc`，两行）

- `funnel.recall_channels` 加回 `"reversal_confirm"`（9 路）；`funnel.channel_quotas` 增 `"reversal_confirm": 150`（六键全写纪律见 :94-98 注释，新键一并写）。
- `scan/recall/l2_stratify.py:17-25 STYLE_CHANNELS["反转"] = ("reversal", "reversal_confirm")`——否则它的独有召回不入任何风格桶、零 floor 保护（侦察实测的真缺口）。`DEFAULT_FLOORS` 不动（12 席两路共用）。
- A/B 口径：**两路同时活体**，`stage_eval.channel_edge`（`learning/stage_eval.py:106-168`）按 `recall_channels` 独有行分 lane 出 `unique_excess_t2/t5`，`channel_ledger` 跨日累计；`channel_audit --days 30` 用 `L1_channels.csv` 长表独立复核。**不需要 shadow variant**（通道活体即入账）。
- 文档：`STAGES.md:53-54` 通道表与 `:298` 开放线头同步改「已重开（本稿）」；`SKILL.md` 配置表同步。

### 5.3 恒空探针（新，防同族复发）

- `scan/structural_audit.py` 增探针 `channel_liveness`：对 config `recall_channels` 每一路，在 `L1_channels.csv` 计行；**0 行 → self_review `warn`「通道 X 名义启用实际空召回」**；连续 ≥3 个扫描日 0 行 → `fail` 级并进 brief ⑤ 风险哨。
- 这是 memory「自动学习的腿必须有一个会变的量做断言,否则它死了也像活着」的制度化——`reversal_confirm` 空转 4 周、`event` 桶 29 日天天改生产输入，都是同一种盲。
- 测试：合成 `L1_channels.csv` 缺某通道 → warn；三日历史 → fail；变异：把计数阈值改成 `<0`，测试必须红。

---

## 6. P3 · L3 第三画像「低位转强」

### 6.1 确定性旗（喂表，不靠 agent 自觉）

`l3.lowturn` 配置块（`user_config.py:104-122 _SUB_WHITELIST["l3"]` 加 `"lowturn"`；`_KNOB_TYPES` 加 dict 型校验；`tests/scan/test_user_config.py:183-198` 与 `test_config_knobs.py` 各加一条）：

```jsonc
"lowturn": {                    // 【生效点】scan/l3/prompt.py l3_table_md(lowturn_flag) ← common/turnup.lowturn_flag
  "enabled": true,
  "max_dist_high_60": -15.0,    // 低位:距 60 日高 ≥15%(仍在水下)
  "max_pct_60d": 10.0,          //   且 60 日涨幅 <10(没涨回去)
  "min_vol_ratio_20": 1.2,      // 放量(通道硬门 1.5 的放宽档;L3 还有 agent 复核)
  "min_pct_5d": 0.0,            // 近 5 日为正(转强)
  "require_above_ma20": true,   // 站回 MA20
  "require_ma5_gt_ma10": true,  // 短均线拐头
  "fund": "main_or_cmf",        // 主力净额>0 或 cmf_20>0(资金转正)
  "pass1_cap": 8                // pass1 强留上限(保护 40 席预算)
}
```

`lowturn_flag := 低位 ∧ 转强 ∧ 放量 ∧ 资金 ∧ ¬healthy_riser_mask ∧ ¬(落刀: pct_60d<−35 ∧ main_inflow≤0) ∧ 非 ST/退`。与「健康上涨」**互斥**（分账干净）；与 `reversal_confirm_gate` 是**同模块两档**（通道档严、画像档宽），不是两套词表。

### 6.2 表与 prompt（`scan/l3/prompt.py`）

- `l3_table_md(..., lowturn_flag=False)` 新 kwarg，默认 False=逐字 parity（与 `dist_flag/misread_flag` 同款模板 :326-333）：`df["lowturn"] = df.apply(turnup.lowturn_flag, axis=1)` → 列值 `转强`/空；`cols += ["lowturn"]`；`header` 追加图例：
  > lowturn 低位转强（确定性旗）= 距 60 日高 ≥15% 且 60 日涨幅 <10 ∧ 站回 MA20 且 MA5>MA10 ∧ 近 5 日为正 ∧ vol_ratio_20≥1.2 ∧ 主力或 CMF 转正，且非健康上涨。**旗亮票不算「下跌趋势票」，硬约束 B 不适用**；仍须过②资金真与⑥兑现机制。今日旗亮 N 只。
- `prepare_l3_table`（:409-412）加 `lowturn_flag=cfg.enabled`；**同步改** `tests/scan/test_l3_pass1.py:389-404` 与 `tests/scan/test_l3_prepare.py:95` 的逐字 parity 调用（侦察点名的会红项）。`pf` 位置词（:52-61）**不动**（`test_l3_profile_lint.py` 锁字节）。
- `triage.py` 规则③b：`lowturn` 旗亮行 mandatory 强留，`cap=l3.lowturn.pass1_cap`（超出按 composite 取前 cap），`selection_detail` 记 `lane:lowturn`。

### 6.3 `.claude/agents/l3-rank.md`（开发会话修改，C3 合规）

- B 条末尾追加：「**例外**：表内 `lowturn` 旗亮的票不算下跌趋势票（已确定性核过：站回 MA20、短均线拐头、放量、主力/CMF 转正），B 条不适用；仍须过②资金真与⑥兑现机制，thesis 必须写明『低位转强』并答 D+1 买家是谁。」
- 新硬约束 **G**：「**低位转强席位**：finalist 中 `lowturn` 旗亮且 conviction≥55 的票 **1–2 席**（有够格候选才给，无则 0，不硬凑）；`lane` 写 `lowturn`；与健康上涨分账。」
- 输出 `lane` 枚举（:38）加 `lowturn`。
- `tests/test_agent_defs.py:110-119` 现有七个锚保留，追加 `"lowturn"`、`"低位转强"` 两锚。

### 6.4 确定性守卫⑥（`scan/l3/merge.py`）

在守卫⑤之后：`_swap_lane_quota(m, conv, fin_idx, "lowturn", target=1, guard="lowturn_quota", protect_lanes={"healthy","trend"})`，候选资格 = `lane=="lowturn" ∧ conviction≥55`（守卫②已剔 <55）。**1 席确定性 soft 下限，prompt 允许至多 2**。`tests/scan/test_l3_merge_v3.py` 加：有够格 lowturn 时换入尾票且不吃 healthy/trend 行；无够格时无操作；lowturn 自己 conviction≥75 时不被换出。

### 6.5 下游零改动（确认过）

L4 卡 prompt、OW 三门、t1_review、E6 四面（`relative_buy.py:389-431`）均不看 lane，lowturn finalist 自动进入 L4 与 E6 候选；`_l3_judged.json` 的 `lane` 随 `l3_audit_ledger.py:146` 入账，lane=lowturn 天然可分账。

---

## 7. Gate 0 · 前置证伪门（零 LLM，P3 上线的先决条件；观察前锁定）

**问题**：在 `gap_c1_o2` 下，「低位转强」这一档是不是负 edge？旧读数只说了「光有前置低位=接刀」（`dist_low_60` 单因子、旧尺），**没人测过「低位+企稳+起爆」的组合，也没在新尺下测过**——是没跑，不是被否。

**方法**（`research/lowturn_precheck.py`，新；复用 `factor_lab` 成型日面板 ≥60 日、`load_price_pivots`、`forward_returns`、`ruler.entry_tradable`）：

- 每个成型日 D 按 §6.1 默认阈值算 `lowturn_flag` 与 §5.1 修后 `reversal_confirm_gate`；对照组：`healthy_riser_mask`、旧 `reversal` 门。
- 读数（buyable_only，逐日截面后跨日汇总）：每日旗亮只数的中位；组内 `gap_c1_o2` 均值/中位/胜率；相对全市场截面**中位**的超额均值与 t（与 `stage_eval.channel_edge` 同口径）；同表并列 `fwd_5_oc`/`fwd_10_oc`（**只观察**）；regime 分桶（trend/range/risk_off）。
- 落稿 `docs/research/<跑动日期>-lowturn-precheck.md`（**先写 §0 假设与停机规则并落盘，再跑读数**）；`factor-backlog.md` 队列加「低位转强（组合旗）」一行，状态随裁决更新。

**停机规则（先写后看）**：

| 读数 | 裁决 |
|---|---|
| 相对超额均值 ≤ −0.5pp 且 t ≤ −2.0（n_days = 有旗亮票的成型日数 ≥ 40） | **P3 不上线**（L3 席位与 l3-rank.md G 条不做）；P1 保留、P2 仅作 L2 多样性通道并继续账本；负结果归档 |
| 其余（含不显著） | P3 上线，进入 ≥10 扫描日活体裁决（§8）。**不显著 ≠ 有 alpha**，报告措辞照 E6 账的「只承诺相对、不承诺绝对」 |
| 每日旗亮中位 < 3 只 | 定义过严 → 在**预登记备选阈值**内放宽一档（`min_vol_ratio_20` 1.2→1.0、`max_dist_high_60` −15→−10，仅此两项）重跑一次；仍 <3 → 记「样本稀疏」，P3 仍可上但守卫⑥ target 保持 1 |

---

## 8. 活体裁决（≥10 扫描日）

| 账本 | 量什么 | 裁决 |
|---|---|---|
| `channel_ledger`（`unique_excess_t2` by lane） | `reversal_confirm` 独有召回 vs `reversal` 独有召回 vs 0 | 新路 ≥ 旧路且 ≥0 → 旧 `reversal` 提退役 proposal；新路 <0 且旧路 ≥0 → 新路回影子；两路皆负 → 两路皆提退役（与 07-11 accumulation/northbound 同法） |
| `l3_audit_ledger`（lane=lowturn） | finalist 中 lowturn 的 `gap_c1_o2` 与其 bench 对照 | 守卫⑥ 留/撤 |
| `relative_buy.md`（P4 加 `lane` 列） | BUY 落在 lowturn 上的次数与读数 | 只记录，不改 E6 规则 |
| `t1_review` | lowturn 卡的 D+1/D+2 判断准确度 | 只记录 |

裁决走 `feedback` skill 裁决提案通道（C6），开发会话落地；本稿不预设结论。

---

## 9. P4 · 双尺分账（只观察不决策）

- `l3_audit_ledger` 与 `lowturn_precheck` 报告对 lane=lowturn 行并列 `fwd_5_oc`/`fwd_10_oc`（`ruler.py` 保留的参考尺，`factor_lab.FWDS` 已含），表头固定注记「**参考尺·只观察；决策尺仍为 gap_c1_o2（07-10/08-05 裁定）**」。
- `relative_buy.md` 逐日表加 `lane` 列（来自当日 `_l3_judged.json`），**不改写任何已登记观测**（账本「冻结观测不可改写」契约 `relative_ledger` 不变）。
- 这一批的意义：让用户 10 天后能看到「低位转强在 1~2 日尺下 vs 5~10 日尺下」两组数并排——如果后者明显更好，那是**换尺或加尺的立案证据**，属另一个设计稿，不在本波决定。

---

## 10. 裁决表（用户逐项过目；裁决权在用户，本表只给建议 + 证据）

| # | 事项 | 建议 | 备选 | 证据 / 代价 |
|---|---|---|---|---|
| R1 | 「低位转强」v1 定义（§6.1 默认阈值） | **采纳默认**：距 60 日高 ≥15% ∧ 60 日涨幅 <10 ∧ 站回 MA20 ∧ MA5>MA10 ∧ 近 5 日为正 ∧ vol_ratio_20≥1.2 ∧ 主力或 CMF 转正 ∧ 非健康上涨 | 只用 `pct_60d<0` 当低位（更宽，会与 healthy 边界打架）；要求 20 日涨幅>0（更像趋势型，会漏掉起爆第 1~3 日） | 阈值全部在 config，可调不改码；互斥于 healthy 才能分账 |
| R2 | P3 上线时机 | **先过 Gate 0（§7），再上 L3 席** | 直接上席同时攒账 | 「开工前证伪省下整条 LLM 路」（07-25 event 路教训 −1.01pp）；Gate 0 零 LLM、半天 |
| R3 | 席位机制 | **守卫⑥ 确定性 1 席 + prompt 至多 2** | 纯 prompt（只写 G 条） | 「记进 lessons ≠ 会生效」（08-13）；守卫④/⑤ 已是现成模式 |
| R4 | `reversal_confirm` 门修法（§5.1） | **改③为 above_ma20∧ma5_gt_ma10，②缩量看 D−1、RSI 上限 85** | 只接 `vol_ratio_20` 不改门 | 不改门 = 与①互斥继续近空，reopen 白开 |
| R5 | L2 风格桶 | **并入「反转」桶（floor 12 共用）** | 新桶「转强」floor 8 | 新桶=改 `DEFAULT_FLOORS`（不入 config 的行为归属），先看 10 日再说 |
| R6 | `reversal_confirm` 配额 | **150**（与 main_fund 同档） | 200（注册默认） | advisory 档 36 日账本减码的都是 150 以下；新路先中档 |
| R7 | 双尺观察列（P4） | **加 `fwd_5_oc`/`fwd_10_oc` 只观察列** | 不加 | 不加就永远答不了「是不是尺的问题」；决策尺不变，不违 07-10 |
| R8 | 展示层（P0） | **summary 0 买段改 E6 同源；brief ③ 不动** | brief ③ 也加「卡面≤Hold」标注 | brief `MAX_BYTES=3000` + sources 边表 lint，改它的代价不成比例 |
| R9 | 恒空探针（§5.3） | **warn + 连续 3 日升 fail** | 只 warn | `reversal_confirm` 空转 4 周无人知=同族第二次 |
| R10 | `lookback` 20→60 | **采纳**（20 日列等值锁） | 单独为新列再拉一次 20~60 日 | 单面板两用，零重复取数 |
| R11 | （可选）brief「合格内 #3/6」改印「剔📌后 #1/4」 | 可选，低优先 | 不做 | 纯渲染，不动 E6 `rank` 字段；做也只在 P0 顺手 |
| R12 | 实施顺序 | **P0 → P1 → Gate 0 → (P2 ∥ P3) → P4** | P0 → P3 直上（跳 Gate 0） | P3 的旗列依赖 P1 的列，顺序没有别的可能；跳 Gate 0 见 R2 |

---

## 11. 实施顺序、commit 粒度与验收

| 批 | commit | 验收（机检） | 验收（活体，首个真实扫描日） |
|---|---|---|---|
| P0 | `feat(report): 0买段与 E6 相对 BUY 同源` | `test_zero_buy_narrative` 新 4 条 + 全量绿 | summary 无「空仓观望」且 BUY 票名与 brief ③ 一致 |
| P1-a | `feat(common): turnup 单一实现 + factor_lab 委托` | `test_turnup` + `test_factor_lab:269-320` 等值 | — |
| P1-b | `feat(frame): 60 日面板 + 10 新列入 keep(schema v2)` | 20 日列等值锁；`test_universe_l2_cols` 10 列 | `L1_scored_full.csv` 出现 10 列且 NaN 率 <5%（`run_health`） |
| G0 | `docs(research): lowturn precheck 读数 + backlog 行` | 报告含停机规则判定 | 用户读数裁决 |
| P2 | `feat(recall): reversal_confirm 修门重开 + 反转桶 + 恒空探针` | `test_scoring` 新 2 条；`test_recall_channels` 改；探针 3 条 | `L1_channels.csv` 有 `reversal_confirm` 行（>0）；self_review 无 liveness warn |
| P3 | `feat(l3): lowturn 旗/强留/守卫⑥ + l3-rank G 条` | parity 调用同步；`test_l3_merge_v3` 3 条；`test_agent_defs` 2 锚 | `_l3_table.md` 有 `lowturn` 列与图例；`_l3_judged.json` 出现 `lane=lowturn`（当日有旗亮票时） |
| P4 | `feat(learning): lane 分账 + 双尺观察列` | `test_relative_ledger` 冻结观测不变 | `relative_buy.md` 有 `lane` 列 |

全量测试命令：`uv run --no-sync python -m pytest -q`（基线 4003 绿，2026-08-19 E6 转正合入时读数；动工前重跑取当日基线）。每批独立 commit、可单独 revert；**agent def 改动下个 session 生效**（会话启动装载）。

---

## 12. 回滚

- P0：revert commit（无状态）。
- P1：`lookback` 改回 20 即回到旧帧（新列整列消失，B 级消费者 presence-gated）；`schema_version` 回 1。
- P2：`scan_config.jsonc` 摘回 `reversal_confirm`（一行）；`STYLE_CHANNELS` 多一个名字无害（`test_disabled_channel_buckets_have_zero_floor` 语义：桶仍有 `reversal` 成员，floor 不空转）。
- P3：`l3.lowturn.enabled=false`（旗列与强留同关）+ revert `l3-rank.md` G 条 + 守卫⑥ 在无 `lowturn` lane 时天然无操作。
- P4：纯追加列，revert 即可。

---

## 13. 不做清单（本波显式排除）

- 不改主尺、不推 swing 决策（C1）；`fwd_5/10` 只出现在观察列。
- 不改 E6 打分/选择/硬门（C5）；`RULE_VERSION` 不变。
- 不改 L4 卡 prompt / OW 三门 / 早停规则。
- 不动 brief ③ 行（R8）；不动 `pf` 位置词。
- 不重排 `DEFAULT_FLOORS`（吸筹桶 floor 12 空转是既有欠账，另案，见 `test_l2_stratify.py:57` 注）。
- 不建任何自动注入 lesson / 自动改 prompt 的腿（08-13）。
- 不把新因子接进 `composite`（改 `_GROUPS` 属另一次 IC 晋升裁决，`factor_lab.ic_promotion_table`）。

---

## 14. 风险与开放问题

1. **1~2 日尺下低位起爆可能就是负 edge**——这正是 Gate 0 存在的理由；若停机，用户得到的是一条归档的负结果而不是更多 UW 卡。
2. **L4 三门会否决多数低位转强票**（困境反转的「业绩真兑现」多半 ✗）——画像会到达卡，但难出 ≥OW；它能成为 E6 相对 BUY 的候选。预期要先说破：本波改的是**候选池的形状**，不是出手线的胜率。
3. **样本稀疏**：旗亮票可能很多天 <3 只；守卫⑥ target=1 与「无则 0」保证不凑数。
4. **未复权 60 日低/高**：`daily` 分区未复权，除权日附近 `dist_low_60/dist_high_60` 有毛刺；60 日窗内除权概率低，先记账（B 级）不修，P4 账本若见异常再议。
5. **`above_ma20`/`ma5_gt_ma10` 的 close 口径**：`tushare_source.py:196-197` 现行写法用的 close 与 `ma_qfq_*` 是否同复权，实施计划第一步核一次（premise 先查，08-18 稿 Gate 0 家训）。
6. **prompt 字节**：`_l3_table.md` 加一列 + 一行图例，L3 表不在 L4 prompt-cache 前缀契约内（`test_l4_prompt_cache_prefix` 只管 L4），但 L3 表有 `two_pass` 逐字 parity 测试两处要同步（§6.2）。
7. **「合格内 #3/6」可读性**（R11）与「卡面 UW 却 BUY」的语义——后者是 E6 的定义本身（相对最优≠研究看多），本稿用 P0 的措辞把两者分开，不试图消灭它。

---

## 15. 实施计划偏离记录（2026-08-21，写 `docs/plans/2026-08-21-lowturn-implementation-plan.md` 时定）

计划落到代码级别后，对本稿四处做了**减少事实源**方向的调整，以计划为准：

1. §4.1 的 `above_ma20` / `ma5_gt_ma10` 不改 `tushare_source._fetch_factors` 取复权 MA，改由 `common/turnup.panel_factors` 用同一份 60 日 close 面板算（研究回测与生产同源同口径，§14 第 5 条的 close 口径疑问随之消失）。
2. §4.1 的 `reversal_confirm_gate` 谓词不在 `turnup` 复制一份；通道门仍只住在 `scoring.lens_reversal_confirm`（修法照 §5.1），`turnup` 只持有面板因子与 `lowturn_flag`。
3. §5.3 / R9 的恒空探针恒为 `warn`（`fail` 会触发 GATE4 阻断发布，探针职责是可见性不是停机）；连续 ≥3 个扫描日 0 行 → detail 加 🔴 前缀。
4. §9 的双尺观察不改 `l3_audit_ledger`（那本账量的是 bench 篮，不是 finalist lane），改为 `research/lowturn_precheck.py --live` 只读报告（与 Gate 0 同一套聚合代码），手动运行，入 STAGES「运维细节」。

另：Gate 0 增加 lowturn × 主尺的 regime 分桶表（只读，不进停机规则）；`l1_full` 产物 `schema_version` 1→2 随十列一起落。


---

## 16. 实施收尾（2026-08-21）

**Gate 0 读数改变了本波的理由陈述**（全文 `docs/research/2026-08-21-lowturn-precheck.md`）：

| 组 | `gap_c1_o2`（决策尺） | `fwd_5_oc` | `fwd_10_oc` |
|---|---|---|---|
| lowturn | **−0.24pp t=−6.36** | **+0.63pp t=3.19** | **+0.99pp t=3.66** |
| healthy（现任） | −0.17pp t=−5.74 | +0.05 t=0.23 | +0.15 t=0.40 |
| reversal_confirm（修后门） | −0.10pp t=−2.19 | +0.84pp t=2.41 | +1.29pp t=2.89 |

- §14 风险 1「1~2 日尺下低位起爆可能就是负 edge」**已被证实**（−0.24pp，t=−6.36，132 日）。
- 但同一张表也证伪了一个此前没人量过的前提：**现任 healthy 画像在决策尺上同样显著为负**（−0.17pp）。所以 P3 的理由从「它有 alpha」改为「**它不比现任差，且打开候选池形状**」——这一句已写进 `scan_config.jsonc` 的 `l3.lowturn` 块注释与 `l3-rank.md` 硬约束 G，防止将来被引用成「低位转强有效」。
- **周级尺符号翻正且显著**，而 healthy 在同尺不显著 —— 这是「换尺/加尺」立案的首份定量证据，属独立设计稿，本波不动主尺。

**实施与设计的差异**（除 §15 四条外）：

1. `l1_full` 产物 `schema_version` 1→2（十列进 CSV 是契约变更）。
2. Gate 0 增加 **UNMEASURABLE** 节：`reversal_old` 在研究面板上缺 `np_yoy` 家族算不出，首版把它折成全 False、印成「n_days 0 / nan」——已改为显式点名（UNMEASURED ≠ CLEAN）。故本表**没有**旧 `reversal` 门的对照读数，两路优劣只能由活体 `channel_ledger` 分 lane 裁。
3. `tests/scan/test_sector_momentum_shadow.py` 的「生产启用路数 == 8」绊线更新为 9，并在测试里写明是哪一波、哪张设计稿改的。
4. 计划 §Task 1.2 Step 4 点的验证文件不全（只写了 `test_factor_lab.py`，漏了真正覆盖 `lhb_seat_net` 的 `test_overnight_factors.py`），导致一个 `NameError` 潜伏了一个 commit，由全量测试 + Gate 0 真面板跑动逮出（commit ea31868）。**计划缺陷，非实施手滑**。

**未做**：§11 批次 5 的活体验收（需要一个真实扫描日）。清单原样保留在实施计划里，下次跑 `scan-market` 时逐条核。

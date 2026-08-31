# 三问 brainstorm(2026-08-28):各阶段的主标尺与守卫 · 复盘现场够不够 · 还能往哪优化

> **性质**:讨论稿,**零实施**。不是调度权威;与 08-26 全项目 brainstorm(`2026-08-26-project-wide-optimization-brainstorm.md`,候选池 A–E / Q1–Q8 仍待裁)互补,**不重复**那里已列的条目,只在被本稿改变优先级处引用。
> **用户裁定(本 session,2026-08-28)**:实际执行时点 = **T+1 尾盘买 → T+2 开盘卖(现状)**。主尺 `gap_c1_o2` 不换;本稿所有建议都在这把尺**之内**。
> **证据来源**:代码现场(`file:line` 均为本次读到)、原会话在 Claude 引擎根内生成的结果账本读数(896 行 / 42 数据日)、61 个已发布 run 的 `manifest.json`、一个只读探针(附录 A,抛弃型,读湖不写盘)。附录现已改成读取**当前引擎** `$RPT`;任何引擎都不得跨读另一引擎根。标 **UNVERIFIED** 的是推断。2026-08-28 Codex review 未复读 `reports_claude/`,只核代码与文档契约。

---

## 0. 边界(不重提)

| 类 | 内容 |
|---|---|
| 用户裁定 | 主尺 `gap_c1_o2`;**5–10 日窗口三次裁不换**(07-10 / 08-05 / 08-22b);learning 层整体退役且真删(08-21);L4 复用不恢复;`scan_config.jsonc` 唯一参数事实源;双引擎隔离;**B 类改动冻结到 09-中攒 20 个结果日**(08-26 A0,本稿沿用) |
| 刚合入、未真跑验收 | 08-26 现场留存 × BUY 所有权五批(§9.4 七条活体验收待跑);08-28 法证 run capsule 18 任务(review 结论 REQUEST CHANGES:**真实生产验收未执行**;`repair` 混合冲突缺陷已在工作树修好未提交) |
| 本稿不做 | 不换主尺、不提 swing、不重开 learning、不写代码 |

---

## 1. 第一问:各阶段现在的唯一衡量标尺是什么,有没有更好的

### 1.1 现状盘点:每一级此刻"按什么在做事"、"被什么量着"

| 级 | 它在优化什么(代码/prompt 里的目标函数) | 谁在量它 · 用什么尺 | 最近读数 |
|---|---|---|---|
| L0 选集 | A 级数据契约齐备 + 可交易(`data/contracts.py:118` `cyq_perf` 是 TIER_BLOCKING) | 无收益尺;只有 `degraded.json` | 08-26 19:07 `cyq_perf` 未落 → 退到 08-25 数据日跑(见 §1.3) |
| L1 召回 | `composite` 权重 = `factor_lab.calibrate(label_col=MAIN_RULER)` 的 rank-IC(`factor_lab.py:1126`),**面板 F 止 08-05**;各路 quota 按 36 日版 unique 超额拍板(混有旧尺证据) | `research/edge_census.py`(手动跑) · 家族均值 − 全湖可买截面中位 | L1 全体 −0.00;composite **+0.14pp(t 3.05,30/39 日样本内)**;healthy −0.37(t −5.58) |
| L2 菜单 | **显式不预测**:sector-neutral composite + 8 桶 floor + 行业帽 20%(STAGES L2 节) | 菜单体检四项(形状,非收益) | L2 全体 +0.06(n.s.) |
| L3 精排 | 6 维 rubric + **「T+2 兑现机制:明天、后天谁来买」**(`l3-rank.md:26`)+ conviction 行为化(≥70 = 愿真金买,`:45`)+ 守卫链 ①…⑨(`l3/merge.py`) | edge_census · finalist vs bench | finalist **−0.27(t −3.94)**,bench −0.27 → **无区分力**;conviction 与 fwd_10 逐日 IC **−0.25** |
| L4 决策卡 | 卡契约 v4「隔夜 c1→o2」:三档情景 / EV / R:R 全按 T+2 开盘写(`l4-card.md:17,63`);rubric 三门出评级;**执行线两行**(`:130-135`) | 评级 rank-IC;早停停因家族(`prelude` 的 `l4_rejection` 日读) | rank-IC +0.118(t 1.68);≥OW 40 天只 4 天;早停「基本面恶化」fwd_10 −6.85(t_NW −5.8) |
| E6 相对 BUY | `rel_gap_market` = gap − 全市场可交易等权均值(`relative_buy.py:17-18`,字面量钉死);`pool=composite`(`scan_config.jsonc:217-218`);硬门 UW/SELL | `outcome.ledger_line`(≥20 笔才印) | active BUY 可读 n=2(+1.04 / −0.78) |
| 执行线 | `exec_ok` = 当日涨 ≤3% ∧ 收盘不在区间上 30% ∧ 未封板(`outcome.py` `EXEC_MAX_*`)——**量尺不是门** | 账本列 | 全湖四年逐年同号(收强票隔夜差 0.13~0.27pp);账本里 finalist 70% 过线;**8 笔 BUY 里 5 笔过不了执行线** |
| L5 报告 | GATE4 `self_review`(报告有没有说假话) | fail/warn 计数 | 08-25:fail 0 / warn 0 |
| 持仓 📌 | `tripwire_watch` 三型 + 执行线(`tripwire_watch.py` 头) | 无收益尺 | 📌 家族隔夜 +0.64(t 0.81),全表最好 |
| 结果账本 | `gap_c1_o2` + `rel_gap_market/sector` + `excess_med_market` + `fwd_5/10` 参考(`outcome.py` `LEDGER_COLUMNS`) | 只记不学 | 896 行;role 只有 finalist 785 / pinned 103 / BUY 8 |

读法:**每级都有"在做什么"的定义,但只有 L3/L4/E6 被同一把尺量过,L1/L2 从没被按"召回"量过,整条链从没被按"该不该出手"量过。** 主尺是统一的(gap_c1_o2),"每级唯一标尺"不是。

### 1.2 本次新量到的两件事(附录 A 探针,42 个账本日,read-only;**待按修正版重跑**)

> ⚠️ **review 修正**:初版附录只对市场基线应用了逐尺可买过滤,家族样本 `v` 没过滤对应买腿,所以表中数值保留作**旧探针初读**,不能再称为最终的「四把可执行尺」结论。附录已补家族过滤;本节所有数值需在原引擎会话内重跑后替换。`finalist∧exec_ok` 跨看 o1 尺仍只是条件形状诊断,不是 o1 买腿策略。

「T+1 买 / T+2 卖」这个框里只有四把可执行的尺子。把账本家族在四把尺上并排量一遍(家族均值 − 当日可买人口中位,逐日配对 t):

| 家族 | o1→o2(开买开卖) | o1→c2(开买收卖,旧尺 fwd_2_oc) | **c1→o2(现主尺)** | c1→c2(收买收卖) | o1→c1(T+1 日内,对照) |
|---|---:|---:|---:|---:|---:|
| plain finalist(`role=finalist`,n_days 42) | −0.23(t −0.7) | −0.45(t −1.1) | **−0.21(t −2.7)** | −0.36(t −1.3) | +0.02 |
| plain finalist ∧ exec_ok(41) | −1.48(t −5.0) | −1.55(t −3.9) | −0.10(t −1.1) | −0.09(t −0.3) | −1.35(t −4.9) |
| 📌 pinned(28) | −0.50 | −1.21 | **+0.54**(t +0.8) | −0.16 | −0.97 |
| BUY active(4) | −1.47 | −2.75 | −0.38 | −1.57 | −1.08 |
| 市场基线(等权均值) | −0.12 | −0.12 | −0.14(t −2.0) | −0.13 | +0.01 |

1. **旧探针未显示「换尺即可救判断层」,但要等修正版重跑才定量。** 用户裁定的执行时点仍然就是主尺,不按哪把看起来好看来换尺。
2. **执行线是 T+1 的判断,不是 T 晚报告的判断。** `exec_ok` 条件于 T+1 收盘信息,跨看 o1 起算尺天然带选择偏差。这仍足以说明报告与执行应分账:T 晚负责候选与否决条件,T+1 决策截止点负责买不买;不能用一条混合曲线让两段互相连坐。
3. **📌 的「先跌后反弹」形状同样只保留作待复核线索。** 修正版重跑前,不再引用「全表最好」作为结论。

### 1.3 时间锚错位:账本给 13% 的 run 记了一笔根本下不了的单

账本按 `analysis_date` 锚定 T+1(`outcome.py::market_frame`:`t1 = P[idx+1]`)。但报告是**晚上**跑的,数据日不一定是当天:61 个已发布 run 里 **19 个 run 的数据日 ≠ 跑动日**。旧探针用报告目录 `hhmm` 近似 ready、以 T+1 14:57 为 exchange cutoff 核(两者都只是 estimated;正式口径改走 G1 的 approved 时点 + operational cutoff):

- 52/61 在 T+1 开盘前就绪(正常);
- **8/61(13%)在 T+1 收盘之后才就绪** → 账本记的"T+1 尾盘买"是那一刻已经过去的价格。名单:`20260711_1808/1809/2008/2133`(数据日 07-09,T+1=07-10)、`20260723_0018/0104`(07-21→07-22)、`20260730_2132`(07-29→07-30)、**`20260826_2000`(08-25→08-26;当晚 `cyq_perf` 未落退日跑的那次)**。
- `manifest.json` 只有发布早段生成的 naive `generated_at`,没有 GATE4 approved/actionability;capsule 的 `events.jsonl` 有逐事件 `ts`,但没有人把它折成 first available 与有效性。

这不是账本 bug 修一下就完:**它是"批准时点 + 数据新鲜度 + 是否仍有效"三个维度从没进过任何尺子。** 一份 20:00 才出、对应昨天数据的报告,最早只能在更后的 session 被人获得,但卡里的三档情景、执行线、tripwire 仍锚旧数据日;它应先标 `LATE_REVALIDATION_REQUIRED/EXPIRED`,不能把日期平移后直接当可执行。

### 1.4 建议:每级一个 primary KPI + guardrails/SLO(全部零 LLM)

原则只有一条:**主 KPI 必须量这一级做的事;守卫负责防止主 KPI 被刷。** 「唯一标尺」不等于只留一个数字:L2 的多样性、L4 的证据完整性、L5 的真实性都是不可被收益替代的 guardrail。

| 级 | 这一级的产品是什么 | primary KPI | guardrails / 不能用什么量它 | 现在缺什么 |
|---|---|---|---|---|
| L0 选集 | 一份新鲜、契约齐、可进入召回的 point-in-time universe | **eligible coverage/freshness**:A 级端点通过率、数据日滞后、L0 人口覆盖 | 收益;L0 数据缺口不能算给 L1 | 只有 `degraded.json`,无逐日 coverage/freshness SLO |
| Stage0 地形 | 描述性市场/行业地形 | 结构化事实覆盖率 + 数字断言对账通过率 | 个股收益;`sector_healthy_top3` 仍只准进 L5 | 有防锚定规则,无长期质量行 |
| 报告时点 | 一份**已获 GATE4 批准且仍有效**的报告 | `decision_approved_at` 到 operational cutoff 的 lead time;迟到/过期 run 数 | 收益;`brief_written_at` 不能冒充 approved | manifest/账本无批准时点、数据新鲜度、有效性状态 |
| L1 召回 | 把值得在主尺上评估的票装进 1000 | **Recall@1000**:事后 `gap_c1_o2 ∧ buyable_c1` 的 top-decile/净成本 hurdle 赢家中,有多少被 L1 召回 | 家族均值;分母只限当日 L0 eligible universe | 从没日常算过;需 L0 全人口最小表 |
| L2 菜单 | 多样性 + 把 L1 的召回传到 200 | **Recall 保持率**:L1 里的事后赢家有多少活到 L2 | 桶 floor/行业帽/覆盖率是 guardrail;不用绝对收益量它 | 菜单体检只有形状指标 |
| L3 精排 | 从 ~40 里挑 8–10,其余 bench | **finalist − bench 配对超额**(同日同尺,剔 📌/席位) | conviction rank-IC 只诊断;相对全市场不是 L3 的尺 | 只在 edge_census 手动跑;账本无 bench 行 |
| L4 卡 | **否决**(早停/UW/SELL)+ 评级 | **拒绝价值** = 被否决家族 − 可比满卡家族的逐日配对超额(负 = 否决对了) | 证据/三门完整性是 guardrail;`fwd_10` 只诊断且必须 HAC/block bootstrap | 现有日读有雏形,但家族非随机,结论只能写 observational |
| E6 | 在可选候选里选 1 只或 abstain | **BUY − 其余 eligible pool**(同日配对,净成本)+ 0-BUY 的候选机会成本 | 不与已被 L4/E6 判 ineligible 的票混比;不只看绝对涨跌 | `ledger_line` 只有 BUY 相对市场,无池内选择/abstention 值 |
| 执行线 | T+1 operational cutoff 买不买 | 前夜 BUY 候选内「执行 vs 始终买」的净策略差、覆盖率、被否决 BUY 的事后 gap | 不把 EOD `exec_ok` 冒充 14:45 实时判断;不量报告候选质量 | 只有 EOD 事后字段,无 point-in-time 影子事实 |
| 持仓 📌 | 盯梢 | tripwire 命中后 vs 未命中的 gap | 持仓本身涨跌 | 无 |
| 整条链 | **今天该不该出手** | **abstention value**:0-BUY 日的 eligible pool 机会成本 + 市场 gap;BUY 日策略净收益 | `NO_RUN` 不能算 0-BUY;只看市场涨跌不足以证明空仓正确 | 账本无市场行、运行日历、0-BUY 候选反事实 |
| L5 报告 | 说真话 | GATE4 fail 数 + 数字断言对账 | 收益 | 已有 |

输入大多已在,但实现不再称「代价很低」:除了扩人口/市场行/时间锚,还必须有①冻结 run 外的 revision 账本②分 horizon 成熟度③湖输入 hash④并发单写者⑤口径版本。它们仍是 M/I 类,前提是只记不学、不改变用户可见执行行为。

### 1.5 在"不换主尺"之内还有哪些真正的空间

- **把执行线从"卡片文本 + 事后 EOD 打分"搬到"14:45 执行前核对"**。先做真正的影子 20 日:14:45 保存 point-in-time 原始响应/hash/source_ts/received_at/staleness,但**收盘前不展示给用户**、不拦单;盘后才读翻转率与被翻掉票的事后 gap。若 14:45 输出被人看到并影响下单,它当日就已经是 B 类行为改动,不能再叫 I 类影子。
- **报告的角色重述**:T 晚的信息到 T+1 开盘已定价大半(08-26 设计稿 §1.7 第 3 条),报告在隔夜尺上能负责的是"候选 + 否决条件",挑哪只、买不买由 T+1 尾盘的信息定。按 §1.4 分尺之后这件事会自然显出来:L3/L4 的尺不再要求它们预测隔夜。
- **成本净额**:候选/过程诊断可保留 gross,但 E6/执行/整链主 KPI 必须同时落 `gross_return,assumed_cost_bps,net_return,cost_model_version`;真实成交接线后再以费用 + 滑点覆盖假设成本,不能覆盖原始 gross。

---

## 2. 第二问:整个推荐链路的现场保存够不够(复盘用)

### 2.1 现在有两层,互为兼容

| 层 | 覆盖 | 状态 |
|---|---|---|
| **现场留存**(08-26,`scan/retention.py`;`publisher.py:471` + `post_run.py:689` 各调一次) | `trace/staging/` 整目录镜像 · `trace/inputs/{slim,sector_packs,prompts,dossiers,temperature_row}` · `trace/transcripts/*.jsonl.gz` · `trace/lake_manifest.json` · `trace/MANIFEST.sha256` · `chain_view` ①–⑩ | 已合入;**七条活体验收待真跑**(设计稿 §9.4);`lake_manifest` 接线缺口见 08-26 brainstorm A7 |
| **法证 capsule**(08-28,`autoresearch/trace/`) | `identity/`(代码 patch + 脏树 + prompt 语料 `.claude/{agents,skills,workflows}` + 环境)· `events.jsonl` hash 链 · `stages/` 逐 attempt 命令/stdout/stderr/信号 · `lineage/reads.jsonl` + 内容寻址 blob · `agents/` transcript 索引 · `usage/` 真计量 · `products/staging` 快照(`capsule.py:2374-2380`)· expected/completeness/replay · MANIFEST + 脱钩 ROOT + 账本 revision + `.tar.zst` · 失败/中断也冻结 · 修复走叠加层 | 已合入 main;**真实生产验收未跑**(review HIGH);Codex 侧合成 fixture 验过 CLI 链、Claude 侧只有单测 |

对照 capsule 设计稿 §3.1 的八个问题,**设计上八问全有答案的落点**;实证上零问被真跑证明过。

### 2.2 逐阶段核对:输入 → 输出 → 留存点 → 复盘时还缺什么

| 阶段 | 输入 | 输出 | 留存点 | 复盘时缺什么 |
|---|---|---|---|---|
| 报告时点 | 湖数据就绪时刻 | run_id、数据日 | `events.ts`、`manifest.generated_at` | **没有 GATE4 approved / first available / actionability**(§1.3);数据日 ≠ 跑动日时报告头不声明 |
| Stage 0 市场研判 | market_pack / strategist_pack(湖派生) | `market_view.md` | staging → products;strategist transcript | — |
| 行业 brief | `context_<engine>/sector/<date>/*.json`(**staging 之外**) | brief md | `trace/inputs/sector_packs`(留存层);capsule 只快照 staging | capsule 单独看时缺行业 pack 原件(靠 transcript 里 agent Read 到的内容间接留存,**UNVERIFIED**) |
| L0–L2 | 湖 A 级端点 | 帧 / L1_scored / L2_top200 / passport | staging + lineage blobs;replay 目标 FULL | 真跑前不知 A 级读取是否**全部**有 blob(review HIGH 的核心) |
| L3 | L3 表、证据、新闻、pass1 kept/cut | judged / finalists / repair patch / 守卫留痕 | staging;l3-rank transcript | — |
| L4 | slim(引擎根目录,staging 外)、档案(as-read)、intel、prompts | 卡 / 早停 / ensemble / 任务簿 | `trace/inputs/slim,dossiers`;staging;逐票 transcript | **网页证据只到 `HARNESS_RESPONSE`**(`capsule.py:1673`,`lineage/external_tools.jsonl`):留的是 harness 给 agent 看的摘要,不是原网页 |
| E6 | passport、任务簿、卡 | `_relative_buy_decision.json`(mode/pool/rule_version) | staging;MANIFEST | — |
| L5 | 全部 | brief/summary/details/index | 发布目录 + MANIFEST + ROOT + 归档 | — |
| 事后 | 湖 T+1/T+2 | `_ledger/outcome/<run>.json` + `recommendations.csv` | 账本(run 目录外,JSON 原子替换 + CSV upsert) | 只记 finalist/📌/BUY,**bench / L2 / pass1_cut / L1 的事后读数不记**;`computed_at` 有、**T+1/T+2 湖文件 hash 没有**;D+2 complete 后 fwd5/10 不再更新 |
| 市场 | 湖 | — | — | **无市场行** → 0-BUY 日、哨兵日、没跑的日子在账本里是空白,不是"空仓正确/错误" |
| 人 | `pinned.jsonc`(脏树 patch 已捕获)· 用户真实成交 · 用户对 brief 的动作 | — | 成交:券商取数分支 **未合并**(08-27 设计,等三选一) | **推荐 vs 实际**这条线还没有 |

### 2.3 结论

**现场契约覆盖较完整;账本层不够;生产实证层为零。** 不再写「现场层够了」:目前能说的是八问都有设计落点,不能说真实生产已经证明这些落点都被写出来。

任一可信 run + 代码,设计上应能拉出 L1 名次 → L2 进场理由 → pass1 去留 → L3 判断 → L4 卡/早停/复核 → E6 四面与硬门 → brief 原句 → 事后读数(`chain_view` ①–⑩),且 prompt/config/slim/档案 as-read/湖读点/transcript 有不可变副本、篡改能被脱钩 root 逮住。真实链是否满足这句话仍由 G6 证明。

不够的七件分布在「账本地基 / 时间 / 人 / 实证」四层:

1. **账本地基**:run 有报告目录 ID 与 capsule contract run_id 两套身份;事后数据无来源 hash/口径版本;多入口写 CSV 无并发契约;D+2 `complete` 会把 fwd5/10 永久冻成缺失。
2. **时间与有效性**:没有 GATE4 批准时点、first available session、数据新鲜度与 actionability;13% 的 run 旧账锚到已经过去的价格。
3. **市场事实 + 运行日历**:没跑、0-BUY、哨兵、失败、周末跑动、同日多 run 没有无损身份。
4. **反事实人口**:bench / pass1_cut / L2 / L1 / L0 未召回赢家缺逐日事后读数;阶段尺算不出来。
5. **真实成交**:券商取数分支未合并;推荐、分笔成交、实际 round trip 对不上表。
6. **网页证据层级**:harness response 在,原网页不在;引用覆盖率尚未实测。
7. **生产实证**:capsule 与留存保证没有在真实 Codex full scan 上被观察过。

### 2.4 怎么做:逐缺口设计(用户追问「2 详细说明要怎么做」,2026-08-28 补)

先说边界,免得与 08-21 裁定打架:**08-21 退役的是「从历史里学 → 回注 prompt → 自动提案」**;下面 G0–G6 都是「把现场摆好给人看」——零 LLM、只记不学。只有在不改变用户可见输出/门/权重/prompt/评级/执行时点时才算 M/I;R3 收盘前展示、R4 数据降级属于 B 类。每件按同一骨架写:记什么 → 落哪 → 谁生产 → 历史迁移 → 验收 → 回滚。

先把本次 dry-run 的两个实测摆在前面,因为它们决定了 G2 的形状:

- Codex 侧 dry-run:`chain_view 20260826_2000 603259` 的 ①–⑨ 有内容、⑩ 缺席——当前引擎账本没被 `fill`。
- 原 Claude 会话记录:`chain_view 20260825_2149 601766` 同样 ⑩ 缺席——`outcome_fill` 只挂在 prelude,没有扫描的日子结果不成熟。本次 Codex review 不复读 Claude 根,只保留原记录作立案证据。

#### G0 · 账本地基:身份、revision、来源、成熟度、并发

| 项 | 设计 |
|---|---|
| canonical identity | 新 run 主键 = `(engine, capsule_run_id)`;同时保留 `report_dir_id=YYYYMMDD_HHMM`。legacy 无 capsule ID 时主键 = `(engine, report_dir_id)`,明写 `identity_quality=legacy`。所有 population/outcome/execution join 不再只叫含糊的 `run_id` |
| 原始事实与视图 | 原始事实 append-only/partitioned:`_ledger/revisions/{runs,outcome,populations,executions}/…`;`recommendations.csv`、`market.csv`、`session_calendar.csv`、`stage_rulers.csv` 是**可重建 materialized views**,可原子替换但不是事实源 |
| 来源 lineage | 每个 outcome revision 记录实际读取的 lake endpoint/key/path/hash、`trade_cal` hash、计算 commit/dirty hash、`computed_at`、`ledger_schema_version`、`metric_definition_version`、`cost_model_version`;湖订正只新增 revision |
| 分尺成熟 | 不设一枚总 `complete`;每个 metric 落 `status ∈ {PENDING,MATURE,UNAVAILABLE}`、`matures_on`、`observed_at`、`revision`。gap 到 D+2 成熟,fwd5/fwd10 分别到 D+5/D+10 后补,互不阻塞 |
| 并发 | prelude 与 launchd 共用 ledger lock;一批写入先落 temp/revision,全部成功后原子 publish views,共享 `batch_revision`。幂等键不等于并发安全 |
| 历史 | **永不回写历史 run/MANIFEST/ROOT**。任何补建写 run 外 sidecar/revision;新 run 的字段必须在 capsule finalize 前定稿 |
| 验收 | 两 writer 并发不丢行;中途 kill 后旧 view 完整且可恢复;D+2/D+5/D+10 分段成熟;改 lake 分区产生新 revision;旧 frozen root 全程不变;Codex/Claude 根零越界 |
| 回滚 | 停止生成新 revision并切回旧 view reader;append-only 原始事实保留,无需改冻结 run |

#### G1 · 时间锚:让每个 run 知道自己"什么时候才能执行"

| 项 | 设计 |
|---|---|
| 记什么 | `data_as_of/max_input_as_of`·`brief_written_at`·`decision_approved_at`(GATE4 PASS;执行可用性的唯一时点)·`capsule_frozen_at`·`first_available_session`·`exec_lag=max(0,trade_index(first_available)-trade_index(analysis_date)-1)`·`staleness_sessions`·`actionability_status ∈ {ACTIONABLE,LATE_REVALIDATION_REQUIRED,EXPIRED,FAILED}`·`ready_source/ready_quality/timezone_assumed`。全部新时戳为带 offset RFC3339 |
| cutoff | operational cutoff 用 `EXEC_DECISION_CUTOFF` 单一事实源,建议 **14:45 Asia/Shanghai**(留人工下单缓冲);14:57 只记 exchange cutoff。`first_available_session` 要求 `decision_approved_at < D′ 14:45` 且 D′>analysis_date |
| 双视图 | `signal_quality` 保留 analysis_date 原主尺,明写是否可执行;`executable_policy` 只纳入 `ACTIONABLE` run。迟到 run 可另算 `late_execution_counterfactual`,**绝不混进主 BUY 均值**。first available ≠ 仍有效,不得仅平移卡片的 T+1/T+2 |
| 落哪 | 新 run 在 finalize 前把 execution/actionability 块写入 manifest;历史 run 只写 `_ledger/revisions/runs/<legacy_id>/revision-N.json`。`chain_view ①` 同时印数据日、批准时刻、first available、有效性、来源质量 |
| brief | 首行印「数据日 / 批准时刻 / 可执行状态」。`LATE_REVALIDATION_REQUIRED/EXPIRED` 明写「不可按原卡直接下单,需重跑/执行前复核」;不再写「把 T+1/T+2 直接读作 D′/D′+1」 |
| 历史迁移 | `manifest.generated_at` 是发布早段 naive 时间,只能作 estimated lower bound;优先级=`capsule gate4 SUCCEEDED ts` > 可证明的 gate4/CP7 时刻 > brief mtime > manifest generated_at。后两级标 `ready_quality=estimated`;临近 cutoff 一律保守判下一 session/unknown。历史 manifest 不动 |
| 验收 | 已知晚跑名单被标 late/expired而非主策略成交;合成三例覆盖 cutoff 前、cutoff 后、周末/节假日;去时区或把 cutoff 改 23:59 测试红;失败/GATE4 未过 run 无 ACTIONABLE 状态;历史 root hash 不变 |
| 回滚 | 停用 execution overlay reader即回到旧 signal view;没有任何 frozen run 需要恢复 |

#### G2 · 市场行 + run 日历 + 独立于扫描的夜间回填入口

| 项 | 设计 |
|---|---|
| run facts | **`_ledger/views/runs.csv`** 一行一个 run,周末/同日重跑/失败后重跑全部保留:`engine,capsule_run_id,report_dir_id,run_local_date,analysis_date,run_mode,business_status,evidence_status,actionability_status,decision_approved_at,first_available_session,n_finalist,n_buy`。不再「同日只取最后一个」 |
| session view | **`session_calendar.csv`** 一交易 session 一行,来源是 `trade_cal` 而非 lake 分区。列出当日所有 `run_ids_ready_before_cutoff`,并按显式规则选 `selected_run_id=截止前最后一个 SUCCEEDED∧approved∧ACTIONABLE`;没有时区分 `NO_RUN/NO_APPROVED_RUN/DATA_MISSING` |
| market view | **`market.csv`** 每交易日一行:`n_buyable_*`,mean/median × 四尺,regime/temperature presence-gated;每尺按自己的成熟日与 source revision 更新,不是整行 D+2 后永久 complete |
| 来源 | manifest + failure.json + run capsule ledger + G1 overlay;冲突按 terminal event/state machine 折叠,不拿目录名词典序猜终态。人工 note 仍只进 append-only `_ledger/notes.jsonl`,机器不编 NO_RUN 原因 |
| 生效点 | `outcome fill` 物化 G0 views;prelude 与夜间入口都走同一 lock/batch revision。夜间任务每个引擎独立 label,命令分别显式 `AUTORESEARCH_ENGINE=codex` / `AUTORESEARCH_ENGINE=claude`;不得依赖普通 launchd 默认 Claude 的行为 |
| 运维健康 | 每次任务写 `last_attempt_at,last_success_at,batch_revision,filled,pending,blocked_by_data,engine`;"0 filled" 只有在成熟欠账确实为 0 时才绿,避免新任务再次安静死亡 |
| 读数 | `BUY day / 0-BUY day / NO_RUN / NO_APPROVED_RUN` 分桶;空仓判断同时看市场 gap 与当日 eligible pool 机会成本。≥20 **有效日**只表示开始展示,不是显著性门 |
| 历史迁移 | runs 保留 61 个全部尝试及周末 run;session view 从首账本交易日起按 `trade_cal` 回建;无法证明 ready 时点的 legacy 行明写 estimated/unknown |
| 验收 | 同日失败→成功两 run 都在 facts、session view 正确选成功者;周末 run 不消失;NO_RUN 不混 0-BUY;两个引擎各写各根;并发/kill 不产生半张表 |
| 回滚 | 停物化新 views/卸载对应 engine job;append-only facts 保留 |

#### G3 · 反事实人口:被拒绝的票也要有"事后"

| 项 | 设计 |
|---|---|
| 两层人口 | **`universe/<analysis_date>.parquet`**:当日全部 L0 eligible 的最小列(`code,sector,composite,rank,in_l1` + 逐尺 outcome/status),用于 L1 recall 分母。**`populations/<canonical_run_id>.parquet`**:L1 top1000 的完整路径字段,用于 L2–E6。`L1_scored_full.csv` 是全部 L0 过门股,不是 1000 行;top1000 是 `L1_recall_top1000.csv` |
| 正交身份 | 不再用互斥 `role` 混合阶段、来源和动作。分开 `in_l1,in_l2,pass1_kept,l3_judged,is_finalist,l4_dispatched,l4_rejected,e6_eligible,is_buy,is_pinned,is_composite_seat` + 单独 `terminal_stage/terminal_disposition`;同一票可以同时 finalist+pinned+BUY |
| 来源 | L0/L1 来自冻结 `L1_scored_full.csv` + `L1_recall_top1000.csv`;L2+ 来自 passport/任务簿/卡/E6。只读 trace/products 冻结副本,不读共享 staging |
| 派生 | **`stage_rulers.csv` 用 long schema**:`session,stage,metric,value,n_days,n_names,coverage,ci_low,ci_high,status,metric_definition_version,batch_revision`。主指标按 §1.4;E6 只比 eligible pool;执行线只在前夜 BUY 候选内;0-BUY 同时量 pool 机会成本 |
| 统计 | gap 先聚合为逐日 paired delta,报 effect size + date-cluster bootstrap CI;fwd5/10 用 HAC/Newey-West 或 block bootstrap;同 analysis/session 多 run 按 G2 selected view 去重;分早停原因时标多重比较。所有「拒绝价值/空仓正确性」只写 observational,不冒充因果 |
| 生效点 | G0 的 outcome revisions 逐 horizon 成熟后物化;复用 `edge_census.daily_stats` 的 entry filter/家族定义与 `common.stats.date_cluster_bootstrap`,不复制第二套 |
| 历史迁移 | 有冻结 L0/L1 staging 的 run 才建完整两层;只有 finalists 的 legacy run 只写可证明 membership,其余状态 `UNKNOWN`,不拿后来的共享 staging 补 |
| 验收 | L0 universe 行数与冻结 full 文件一致;L1 行数与 top1000 一致;正交 flags 能还原每级去留;gap 与 edge_census 逐日六位小数同源;fwd10 在 D+10 后由 PENDING→MATURE;删 entry filter/颠倒 selected run/把 UNKNOWN 当 False 均须红 |
| 体积 | 不先拍「几十 MB」:用一个真实 run dry-run 量 minimal L0 + rich L1 parquet 大小,再决定年度分区/压缩;体积不是删证据的理由 |
| 回滚 | 停物化 stage views;revision facts 不被上游消费 |

#### G4 · 真实成交:推荐 vs 实际

| 项 | 设计 |
|---|---|
| 前置 | 券商取数分支合并(08-27 稿,等三选一);复用标准字段 `account` 别名、`trade_id`、aware trade time、分笔成交、费用,不另造缩水 schema |
| 三张事实表 | `fills.parquet` 一行一 fill(原始 broker trade_id);`recommendation_matches.parquet` 一行一推荐↔fill 匹配(`recommendation_id=engine/capsule_run_id/code/policy_version`);`round_trips.parquet` 按账户/lot/FIFO 或显式批次聚合买卖。卖出/partial sell 不塞回买入行 |
| 匹配 | `(code,trade_date)` 只作候选条件,不能作唯一 join。按 account、aware timestamp、推荐有效窗、trade_id/qty 匹配;同票同日多 run 必须落 `match_rule_version` 与 ambiguous 状态,不猜 |
| 派生 | fill 层算 `slippage_vs_reference`;round-trip 层算含费 `realized_ret`/`vs_ruler`;`bought_before_approved` 比较 `decision_approved_at`,不是 brief/generated_at。原始 fill、匹配、收益三层互不覆盖 |
| 读数 | BUY 执行率、partial fill、滑点、真实净收益、`user_only` 家族;📌 只是一种来源 flag,不再当互斥 role |
| 隐私 | 继承 broker 稿:真实账号/身份证/金额明细只在 `context_<engine>/broker`,报告账本只存 account alias/必要派生;不进 capsule、git、日志或跨引擎根 |
| 不做 | 自动改 `pinned.jsonc`(08-27 §14 已延后)、任何 LLM 叙事 |
| 验收 | 同价分笔、多账户、同票同日多 run、partial sell、缺 trade_time、ambiguous match 均有合成样本;一个真实周人工对表;删 account/time/run 约束导致误配时测试红 |

#### G5 · 网页证据:先量再决定要不要抓原文

| 项 | 设计 |
|---|---|
| 现状 | `lineage/external_tools.jsonl`(`capsule.py:1702`)每次 WebSearch/WebFetch 一行:`url/title/requested_at/result_hash→blob/status`,`capture_level=HARNESS_RESPONSE`(`:1673`)——复盘能看到 agent 当时看到的响应正文,看不到原网页 |
| (a) 引用对账(建议先做) | 产 `citation_audit.json`:卡片/intel 中每个 URL → canonical URL/redirect chain → `external_tools.jsonl` 的 COMPLETED 行/response blob。最好在 GATE4 前 lint;若只能 post-run,只写 sidecar 与 chain_view,**不得原位改已验卡片** |
| (b) 发布时快照(真跑后再定) | 对引用 URL 各抓一次:status/final_url/content_type/fetched_at/hash/blob,`capture_level=POST_RUN_FETCH`;失败、付费墙、JS 页面如实记。它只是事后快照,永不冒充 agent 当时看到的 HARNESS_RESPONSE |
| 安全/边界 | 设 URL 数、响应大小、MIME、超时与重定向上限;不执行脚本;正文 blob 受引用/版权与 retention 策略约束 |
| 验收 | 先真跑量覆盖率再选 (a) 或 (a)+(b);删一行 lineage、URL 带 tracking 参数、301、抓取失败均能准确落 audit,且 published card/root 不被 post-run 改写 |

#### G6 · 真跑验收:一次真跑同时核三件事

| 项 | 设计 |
|---|---|
| 用哪次 | capsule 设计稿的生产验收必须是**下一次真实 Codex full scan**。Claude run 只能证明共享确定性层 + Claude adapter,不能替 Codex transcript/usage/路径隔离背书;各引擎验收状态分开记 |
| 前置 | G0/G1/G2 合入;`repair` 缺陷在可命名 commit 上(或明确记录预期 dirty patch hash);不在一个含未知脏改的工作树上宣称 main 已验收 |
| 清单 | capsule 设计稿 §17.3 + 08-26 稿 §9.4 + G0/G1/G2 新断言,不再写死「17 条」。另有 machine-readable `acceptance.json` 记录 check/command/expected/observed/artifact_hash/run_id/engine |
| 步骤 | SKILL 步骤 0 `capsule begin` → 正常跑到 CP7(`observe` 自动 finalize + verify)→ `capsule verify` / `capsule replay` / `retention verify` / `chain_view` 四条命令 → **负例全部在克隆上做**(改一字节 → `integrity_ok=false`;删一份 required transcript → `completeness_ok=false`;同日第二次 `begin` → 第一个 root 不变;`outcome fill` → root 不变) |
| 追加负例 | 并发 writer/中途 kill;D+2/D+5/D+10 分段成熟;lake 分区变化新增 revision;历史 root 不变;同日失败→成功两 run 不丢;engine root 零越界;schema downgrade/旧 reader 回滚 |
| 记到哪 | `docs/research/2026-08-27-scan-forensic-capsule-acceptance.md` §7 + 08-26 稿 §9.4 + machine-readable acceptance artifact;只有 Codex 项全绿才把 Codex 设计状态改「生产已验收」 |
| 顺带 | 量 G5 引用覆盖率、`cyq_perf` 落地时刻、G0 views 体积与 fill 墙钟 |

### 2.5 复盘怎么跑(人工、只读、只记不学)

机器负责把现场摆好,人负责看;看完不自动改任何东西——要改 config/prompt 是普通开发改动(人判断 → 改 → 测试锁 → 合入),不走任何自动通道。

```bash
# 1. 选 run/交易 session:保留全部尝试,再看当天选中了哪一份
cat $RPT/scan/_ledger/views/runs.csv                                      # G2 run facts
cat $RPT/scan/_ledger/views/session_calendar.csv                          # G2 selected session view

# 2. 先验现场可信度(不绿就先知道缺什么,再看结论)
uv run --no-sync python -m autoresearch.trace.capsule verify <run_id>    # 完好 / 完整 / 可重放,三个结论分开
uv run --no-sync python -m autoresearch.scan.retention verify $RPT/scan/<run_id>   # 老 run 的兼容核验

# 3. 逐票链路(BUY、📌、以及"被否决但事后涨"的票)
uv run --no-sync python -m autoresearch.scan.chain_view <run_id> <code>  # ①身份(含可执行日)…⑩结果

# 4. 当日全景:每一级那天做得怎样;效应量/覆盖/CI;事后赢家在哪一级被丢
grep <date> $RPT/scan/_ledger/views/stage_rulers.csv                     # G3
#   populations/<run_id>.parquet:按正交 flags + terminal_disposition 看赢家在哪级被丢

# 5. 与自己对表(券商接线后)
grep <date> $RPT/scan/_ledger/views/executions.csv                       # G4 materialized view

# 6. 写一行人话(可选,append-only,机器永不读它做决策)
uv run --no-sync python -m autoresearch.scan.outcome note <date> "…"
```

### 2.6 验收:拿复盘问题当测试(dry-run 实测 + 修哪件后能答)

| 复盘问题 | 现在(本次实测) | 修哪件后能答 |
|---|---|---|
| 这只票怎么进来的、在哪一级被降级 | ✅ `chain_view` ②–⑥ 有内容(两个 run 都实测) | — |
| L4 看了什么数据(slim/档案 as-read) | ✅ 08-26 后的 run;❌ 之前的 run ⑦ 缺席 | 历史无解,诚实缺席 |
| agent 的推理链 | ✅ `trace/transcripts`(08-26 后)/ capsule `agents/raw` | G6 真跑证实 |
| 事后涨没涨、读了哪版湖 | ❌ 两个 run ⑩ 缺席;无 outcome source hash | **G0 + G2 夜间入口** |
| 报告何时获批、何时可获得、是否仍有效 | ❌ 无字段;13% run 旧账锚错 | **G1 双视图** |
| 没推荐的赢家在哪一级被丢 | ❌ 只有 finalist/📌/BUY 有事后 | **G3** |
| 今天空仓对不对、没跑的日子市场怎样 | ❌ 无市场行 | **G2** |
| 我实际买了什么、滑点多少 | ❌ | **G4**(等三选一) |
| 卡片引用的新闻原文 | ⚠️ 只有 harness 响应正文 | **G5** 先量后定 |
| 现场有没有被改 | ✅ 契约有 MANIFEST + 脱钩 ROOT | **G6 Codex 真跑** |
| 确定性层能否重放 | ✅ 设计有 `replay` | **G6 Codex 真跑** |
| 当时的 prompt/config/脏树是哪版 | ✅ 设计有 `identity/` + `run_contract` | **G6 Codex 真跑** |
| fwd5/fwd10 后来是否成熟、订正有没有覆写旧数 | ❌ 当前 D+2 complete 会永久跳过 | **G0 分尺 revision** |
| 同日多 run/周末 run/失败后重跑有没有丢 | ❌ 单日日历设计会压扁 | **G2 runs facts + session view** |

### 2.7 顺序与工量(建议)

| 批 | 内容 | 类 | 工量 |
|---|---|---|---|
| 地基批 | **G0 + G1**(revision/来源/成熟/并发 + 时间双视图) | M/I | 先实现与历史 dry-run,不改 frozen run |
| 第一批 | **G2**(runs/session/market + 显式 engine 夜间入口)+ **G6 Codex 真跑** | M/I | 一次正常 Codex full scan |
| 第二批 | **G3**(L0 minimal + L1 rich 人口 + stage rulers) | I | 先用一 run 量体积/墙钟再展开历史 |
| 第三批 | **G4**(等券商分支三选一;fills/matches/round-trips) | I | 合并后单独立里程碑 |
| 真跑后定 | **G5** (a) 或 (a)+(b) | I | 真跑覆盖率出来后单独估 |

G0–G3/G5/G6 在纯观测形态下不重置行为样本钟;任何收盘前可见的 R3 输出、数据门降级或执行策略变化都另列 B 类并重置对应策略版本的样本钟。

---

## 3. 第三问:还有什么优化方向(只列本稿新增或被本稿改了优先级的)

| # | 方向 | 类 | 一句话 | 与 08-26 池的关系 |
|---|---|---|---|---|
| R0 | **账本地基 G0**:canonical run identity + append-only revisions + lake hash + 分 horizon 成熟 + 单 writer | I,P0 | 没有它,G1–G4 会各自造身份/版本/并发语义 | 新增;应先于所有账本扩展 |
| R1 | **时间双视图**:GATE4 approved 时点 + first available + actionability;信号质量与可执行策略分账;历史只写 sidecar | M,P0 | 修正 13% 假单且不把过期卡平移成新策略;不碰 frozen MANIFEST | 新增,替换原「原位重算」 |
| R2 | **每级 primary KPI + guardrails**落成一屏:L0 minimal + L1 rich 反事实人口、市场行、runs/session view、统计 CI | I | §1.4 变成日常可读、版本化、可复现的观测 | 承接 A1/E3,但先过 G0 |
| R3 | **14:45 point-in-time 核对**:影子 20 日,收盘前不展示不拦 | I → B | 保存真实时点响应;一旦收盘前可见/影响下单即升级 B 类并开新策略版本 | 新增;先做实时源与 staleness 探针 |
| R4a | **数据就绪时点测量**:量 20 日 `cyq_perf` 落地时刻/失败原因/last success | M | 先知道问题是晚、缺、还是任务死 | 与 08-26 B1 同批 |
| R4b | **调度/数据门行为选择**:改跑动时间、等待、或把 `cyq_perf` 从 blocking 降级 | **B** | 会改变 as-of、候选人口或报告可用性,不能再归 M/I | A0 冻结后单独裁 |
| R5 | **券商成交接线** → 推荐 vs 实际对表 | I | 08-27 设计稿已落,分支等三选一 | 已有,提优先级 |
| R6 | **真实 Codex full scan 验收** + 08-26 §9.4 活体验收 + G0/G1/G2 新断言 | M,P0 | Claude 不能替 Codex adapter/路径隔离背书 | 真实成本由用户决定 |
| R7 | **A0 冻结重申**:纯观测 G0–G3/G5/G6 不重置;R3 可见化与 R4b 必须另起策略版本 | 治理 | 不让「影子」或数据门变化偷穿冻结 | 08-26 Q1 的可执行化 |
| R8 | 5–10 日尺的**唯一合法出现处** = L4 拒绝价值 / 避雷单 / L3 候选形状;永不进 BUY | 规则 | 三次裁定的可执行表述 | 08-26 A4 |
| R9 | **引用 audit sidecar**:先量 harness 覆盖,不 post-run 改卡;原网页快照与当时响应分层 | I | 审计证据不反向污染被审计产物 | 新增,真跑后裁抓不抓原文 |

不在本稿:成本/时长(08-26 B 类)、代码债(D 类)、路由(E 类)——那里已列全。

---

## 4. 待裁(用户)

| # | 问题 | 本稿建议 | 不选的后果 |
|---|---|---|---|
| Q0 | operational cutoff 用 14:45 还是 14:57? | **14:45**(给人读/下单留缓冲);14:57 只记 exchange cutoff | 14:56:59 批准也会被伪称可执行,且与 R3 核对时点冲突 |
| Q1 | 历史错位 run 怎么处理? | **双视图 + run 外 revision**:原 signal outcome 保留;主 executable policy 排除 late/expired;late counterfactual 单列;不回写 manifest | 原位重算会破坏冻结现场并把过期卡伪装成可执行策略;只标不分账又污染主均值 |
| Q2 | 账本扩到哪层人口? | 先建 G0;再做 L0 eligible minimal + L1 top1000 rich 两层,正交 flags,市场/runs/session views | 只存 1000 行算不出 L1 真召回;互斥 role 丢多重身份 |
| Q3 | 14:45 执行前核对先做影子? | 做 20 日**不可见影子**;收盘后才展示 | 收盘前展示会影响交易,等同未经裁决的 B 类上线 |
| Q4 | 数据日 ≠ 今日时怎么办? | 先做 R4a 量 20 日;等待/改时刻/降级作为 R4b 另裁 | 把降级写成 I 类会偷改候选人口和策略版本 |
| Q5 | capsule 真跑验收什么时候跑、用哪个引擎? | G0/G1/G2 合入后,下一次真实 **Codex full scan**;Claude 单列自己的验收状态 | Codex transcript/usage/engine-root 路径永远停在 fixture 证据 |
| Q6 | 券商成交三选一(08-27 稿) | — | 推荐 vs 实际永远对不上 |
| Q7 | 网页证据抓到哪层? | 先做 citation audit + 真跑量覆盖;再裁是否 POST_RUN_FETCH | 一上来抓全文增加成本/版权/失败面,仍不能证明当时看到什么 |

---

## 5. 局限

- 附录 A 旧探针:42 个数据日、单一 regime、BUY n=4 不可读,且初版家族样本漏 entry filter;§1.2 数字必须按修正版在原引擎会话内重跑。`exec_ok` 跨看 o1 尺是条件形状,不是可交易信号。
- 旧时间锚统计以目录 `hhmm`/manifest generated_at 近似 ready,两者都早于可证明的 GATE4 approved;历史只可标 estimated,不能当精确人类可读时刻。
- 20 有效日只是展示门槛,不是统计显著/因果证据门槛;多 horizon、同票重复、同日多 run 都需按 §2.4 统计约束处理。
- 所有阶段读数先存 gross;E6/执行/整链主读数必须印带版本净成本。真实成交未接线前,净值只是成本模型假设。
- §2 全部仍为设计态核对;真实 Codex full scan 前不构成对完整性/可重放性/adapter/usage 的生产保证。
- 引擎隔离优先于复核便利:Codex 不读 `context_claude/reports_claude`,Claude 同理;跨引擎比较只用显式导出的非可变摘要,不借账本状态。
- 仅供研究,非投资建议。

---

## 附录 A · 只读探针(抛弃型;复现即贴回一个 .py 跑,零写盘)

```python
"""四把可执行尺子 × 账本家族(读 lake/daily 与 _ledger/recommendations.csv,不写盘)。"""
import csv, sys, collections, math
import numpy as np, pandas as pd
sys.path.insert(0, ".")
from autoresearch.common import workspace as ws
from autoresearch.research import edge_census as ec
from autoresearch.research.factor_lab import _board_limit

ledger = ws.reports_root() / "scan" / "_ledger" / "recommendations.csv"
rows = list(csv.DictReader(ledger.open(encoding="utf-8-sig")))
last = {}
for r in rows:                                   # legacy 探针:同 analysis_date 按 report_dir_id 取最后
    d = r["analysis_date"].replace("-", "")
    if d not in last or r["run_id"] > last[d]: last[d] = r["run_id"]
fam = collections.defaultdict(lambda: collections.defaultdict(set))
for r in rows:
    d = r["analysis_date"].replace("-", "")
    if r["run_id"] != last[d]: continue
    if r["role"] == "finalist":
        fam[d]["plain_finalist"].add(r["code"])
        if r.get("exec_ok") == "True": fam[d]["plain_finalist∧exec_ok"].add(r["code"])
    elif r["role"] == "composite_seat":
        fam[d]["composite_seat"].add(r["code"])
    elif r["role"] == "pinned": fam[d]["pinned"].add(r["code"])
    elif r["role"] == "BUY" and r.get("mode") == "active": fam[d]["BUY_active"].add(r["code"])

P = ec.lake_trade_days(); RULERS = ("o1o2", "o1c2", "c1o2", "c1c2", "o1c1")
per_day = collections.defaultdict(lambda: collections.defaultdict(list)); mkt = collections.defaultdict(list)
for D in sorted(fam):
    if D not in P or P.index(D) + 2 >= len(P): continue
    i = P.index(D); piv = ec.load_lake_pivots(P[i:i + 3])
    if not piv: continue
    o, c, h, pc = piv["open"], piv["close"], piv["high"], piv["pct_chg"]; d1, d2 = P[i + 1], P[i + 2]
    if d1 not in o.columns or d2 not in o.columns: continue
    o1, c1, h1, pc1, o2, c2 = o[d1], c[d1], h[d1], pc[d1], o[d2], c[d2]
    lim = pd.Series([_board_limit(x) for x in o.index], index=o.index, dtype=float)
    sealed_o1 = (pc1 >= lim * .98) & (c1 >= h1 - 1e-6) & (o1 >= h1 - 1e-6)   # 一字板开盘买不进
    sealed_c1 = (pc1 >= lim * .98) & (c1 >= h1 - 1e-6)                          # 封板收盘买不进
    fr = pd.DataFrame({"o1o2": o2/o1-1, "o1c2": c2/o1-1, "c1o2": o2/c1-1, "c1c2": c2/c1-1, "o1c1": c1/o1-1})
    fr = fr.where(fr.abs() <= 0.31)
    ok = {"o1o2": ~sealed_o1, "o1c2": ~sealed_o1, "o1c1": ~sealed_o1, "c1o2": ~sealed_c1, "c1c2": ~sealed_c1}
    for rn in RULERS:
        base = fr.loc[ok[rn].fillna(False), rn].dropna(); med = base.median(); mkt[rn].append(base.mean())
        for fn, codes in fam[D].items():
            # 旧探针漏了这一层:市场基线过滤了买腿可执行性,家族却没有。
            # 每把尺都按自己的买腿过滤,否则是在拿不可成交票比可成交基线。
            members = [x for x in codes if x in fr.index and bool(ok[rn].get(x, False))]
            v = fr.loc[members, rn].dropna()
            if len(v): per_day[rn][fn].append(v.mean() - med)
t = lambda xs: np.mean(xs) / (np.std(xs, ddof=1) / math.sqrt(len(xs))) if len(xs) > 1 else float("nan")
for rn in RULERS: print(rn, f"{100*np.mean(mkt[rn]):+.2f}pp t{t(mkt[rn]):+.2f} n{len(mkt[rn])}")
for fn in ("plain_finalist", "plain_finalist∧exec_ok", "composite_seat", "pinned", "BUY_active"):
    print(fn, " ".join(f"{rn}:{100*np.mean(per_day[rn][fn]):+.2f}(t{t(per_day[rn][fn]):+.1f},n{len(per_day[rn][fn])})"
                       for rn in RULERS if per_day[rn].get(fn)))
```

旧时间锚核对(§1.3)仅把报告目录 `YYYYMMDD_hhmm` 当 estimated ready,不能替代 G1 的 `decision_approved_at`。修正版口径:当前引擎根内运行;逐尺同时过滤市场与家族买腿;operational cutoff 由 Q0 裁定(本稿建议 14:45 Asia/Shanghai)。

---

## 6. 实施记录(2026-08-28,本 session)

> 用户「按稿开发 + 加一个改名需求」。本节只记**已落地**的,与稿子的偏离逐条写明。

### 6.1 新需求 · 发布目录改名(用户当面提出,不在原稿里)

**裁定**:目录名 = `<数据日YYYYMMDD>-<发布MMDD_HHMM>`,如 `20260825-0826_2000` = 研究 08-25 的市场、08-26 20:00 写完。

**为什么值得改**:旧格式首段是**跑动日**(`publisher._run_publish` 的 `run_compact = now`),数据日只藏在 `manifest.analysis_date` 里 —— `20260826_2000` 这个名字对人说"08-26",而它研究的是 08-25。61 个已发布 run 里 **19 个**数据日 ≠ 目录名首段。顺带修好排序:按目录名字典序现在 = 按数据日排,同一数据日的多次重跑自然聚在一起。

**用户第二问「为什么 `20260826_2000` 的数据日不是 08-26」的答案**(两层,别混):
1. **名字层**:首段本来就是跑动日,不是数据日 —— 就是上面这个病;
2. **数据层**:那晚(08-26 19:07)`cyq_perf` 分区没落,而它是 `TIER_BLOCKING`(`data/contracts.py:118`),A 级数据就绪门正确地阻断了 08-26,操作者退到最近完整交易日 08-25。**这次门是对的**;真正的问题是退日之后没有任何东西提醒读者"这份报告已经追不上 T+1 了"——即 §1.3 的时间锚,见 6.2。

**落地**:新模块 `autoresearch/scan/run_naming.py`(`format_run_dir`/`parse_run_dir`/`is_run_dir`)。`publisher._run_publish` 用它产名;`outcome.published_runs` 的判据从「`[:2]=="20"` 且含 `_`」收紧到 `is_run_dir`。**legacy 名只读兼容,历史目录一律不改名**(run 目录发布后不再变是 MANIFEST/ROOT 的不变量,改名会让每个历史 run 的 `verify` 当场变红);legacy 的 `analysis_date` 一律返回 `None` 而不是拿首段顶替 —— 那正是本波要终结的错误。

### 6.2 G1 时间锚(按稿实施)

新模块 `autoresearch/scan/exec_anchor.py`:`EXEC_DECISION_CUTOFF=14:45`(运营截止,Q0 按建议取 14:45;`EXCHANGE_CUTOFF=14:57` 只留档不作判据)、`trading_sessions` 三级回退(trade_cal → lake 分区 → 工作日启发,**每级自报 `calendar_quality`**)、`build_execution_block`、`read_execution`。

- `manifest.json` 新增 `execution` 块;发布时先按发布时刻写并标 `ready_source=publish_time`/`ready_quality=estimated`(GATE4 还没跑,**不冒充**批准时刻)。
- 结果账本新增四列 `anchor_session` / `exec_lag` / `actionability` / `exec_gap_c1_o2`;`ledger_line` 的均值**只算 `ACTIONABLE`**,并明说排除了几笔(迟到 / 锚未知),不静默。
- 迟到 run 的反事实走独立列 `exec_gap_c1_o2`(`outcome.exec_anchor_frame`:同一 `forward_returns`、买腿改到第一个真正来得及的尾盘),**两列绝不混算** —— 稿子 Q1 的「双视图」。
- `chain_view ①` 加印批准时刻/第一个可执行尾盘/迟到 session/可执行状态。

**与稿子 Q1 的关系**:稿子建议「双视图 + run 外 revision + 不回写 manifest」。本次实现了双视图与「历史 manifest 一个字不改」;**run 外 revision 账本(G0)未做** —— 老 run 的锚由 `read_execution` 每次按 `generated_at` 估算(标 `estimated`),不落盘、不回写。

**真数据核对(只读,61 个 run,真 `trade_cal`)**:53 `ACTIONABLE` + 8 `LATE_REVALIDATION_REQUIRED`,迟到名单与 §1.3 逐个对上 —— `20260711_1808/1809/2008/2133`、`20260723_0018/0104`、`20260730_2132`、`20260826_2000`。

### 6.3 已知缺口(本次**没做**,别当已完成读)

1. **brief 首行不印可执行状态**。稿子 G1 要求 brief 印「数据日 / 批准时刻 / 可执行状态」,本次**未做**:brief 有 ≤3,000B 硬预算 + 「同 run 重放 byte 稳定」契约 + 四条 fail 级 lint,而 brief 渲染跑在 manifest 落盘**之前**,要接进去得改 `collect_facts→build→_sections` 的签名链并重新论证 byte 稳定性。锚点现在只在 manifest / 账本 / `chain_view ①` 三处可见。
2. **G0 账本地基整体未做**(canonical identity / append-only revisions / lake hash / 分 horizon 成熟 / 单 writer lock)。现有 `complete` 布尔仍会把 fwd5/10 冻成缺失。
3. **`decision_approved_at` 仍是发布时刻的估算**,不是 GATE4 实测时点(需要 `post_run observe` 在 GATE4 之后回写一次)。
4. 历史 8 个迟到 run 的账本行**尚未重算**(要跑一次 `outcome fill` 才会带上新四列)。

### 6.4 G2 / G3 已实施(并行,同 session)

| 件 | 落点 | 真跑读数 |
|---|---|---|
| **G2** 运行事实与市场行 | `scan/ledger_views.py` + `scripts/nightly_close.sh`(改造死任务)+ 双引擎 plist 模板 | `runs.csv` 60 行 / `session_calendar.csv` 50 session / `market.csv`;`calendar_source=trade_cal`;`_health.json` 三层可判活 |
| **G3** 反事实人口与逐级 KPI | `scan/populations.py` | `universe/<date>.parquet` 4312 行 84KB · `populations/<run>.parquet` 1000 行 116KB · 各 0.1s;59 run 建成 |

**首次拿到的逐级读数**(observational,小样本,未扣成本,**别当结论**):

| 级 | KPI | 读数 |
|---|---|---|
| L1 | `l1_recall_at_1000` / lift | 25.4% / **1.05**(n=44 日)—— 召回层抓事后赢家的能力≈随机 1000 只 |
| L2 | `l2_keep_rate` / lift | 19.4% / **0.96**(n=43) |
| L3 | `l3_finalist_minus_bench` | **−0.01pp**(n=34)—— 与 08-22 普查「bench 与 finalist 同为 −0.27」一致:无区分力 |
| L4 | `l4_reject_value_gap` | −0.16pp(n=13);`_fwd10` +2.21pp(n=11,**与按停因分组的旧读数符号相反,样本薄不可读**) |
| 整链 | 运行日历分桶 | 0 买日 35 · 没跑 4 · 未批准 1 —— **`NO_RUN` 第一次不再被算成 0 买** |

### 6.5 集成复核逮到的两处(都是**共享 staging** 同一族污染)

1. **`capsule_run_id` 不唯一 → 视图静默吃掉 3 个 run**。`20260729_2105`/`20260730_0116`/`20260730_2132` 共享同一个 `20260729T113300999873Z`(staging 按数据日键,重跑复用 `run_contract.json`),G2 按 capsule id 做主键 → 盘上 60 个 run 在 `runs.csv` 里只剩 57,正是本视图存在的理由要防的那件事。**修法**:主键改 `(engine, report_dir_id)`(报告目录才是一 run 一份),`capsule_run_id` 降为属性,重复时打 `identity_quality=shared_capsule_id` **显式点名**(两个 run 指着同一个法证现场是证据层的真问题,藏起来更危险)。已有测试锁。
2. **gate4 时间戳同源污染**(见 6.2 脚注):`20260730_0116` 与 `20260730_2132` 的 `stage_results/gate4.json` 逐字节相同,直接采信会造出假 `ACTIONABLE`。**修法**:一致性窗(早于发布 12h / 晚于 6h 即弃)+ 退回估算并在 `ready_source` 留痕。

> 同一条教训两次:**实测值也要先自证"我是本 run 的"**。共享 staging 按数据日键这件事,会把任何"看起来是本 run 的"证据复制到下一个 run 头上。

### 6.6 接线(自查逮到的:生产者没接线)

`populations` 写完之后**零调用点** —— 本仓最常复发的缺陷(FN-1 家族:「记进 lessons ≠ 会生效」「新生产者必须 grep 调用链」)。已补两条腿,**都幂等,任一条死了另一条还在**:

- `prelude` 新步 `ledger_views`(排在 `outcome_fill` 之后 —— 三张视图与人口表都是结果账本的下游),同批改 `STEP_NAMES` 与 `test_step_names_inventory` 显式锁(那条锁按设计拦住了这次改动,是它该干的活);
- `scripts/nightly_close.sh` 第 3 步 `populations build`。

边界与 `outcome_fill` 逐字相同:**只记不学**,读数只进汇总屏一行与 `chain_view`,不进 brief、不喂任何 agent、不改任何参数。

---

## 7. 本 session 之后仍未做(**别当已完成读**)

| # | 件 | 为什么没做 |
|---|---|---|
| 1 | **G0 账本地基整体** | canonical identity / append-only revisions / lake hash / 分 horizon 成熟 / 单 writer lock。稿子说它该**排在最前**,本次跳过了 —— 现有 `complete` 布尔仍会把 fwd5/10 冻成缺失 |
| 2 | **G4 真实成交** | 等券商分支三选一(§4 Q6,用户裁决) |
| 3 | **G5 网页证据 citation audit** | 稿子要求「先真跑量覆盖率再裁」(§4 Q7) |
| 4 | **G6 真跑验收** | 需要一次真实 Codex full scan(§4 Q5,真实成本,用户裁决);capsule review 的 HIGH 缺陷仍挂在这里 |
| 5 | **G1 的 brief 那一行** | ≤3,000B 硬预算 + byte 稳定契约 + 4 条 fail 级 lint,且 brief 渲染在 manifest 落盘之前 |
| 6 | **R3 14:45 执行前核对** / **R4a 数据就绪时点测量** / **R4b 数据门降级** | R3/R4a 未做;R4b 是 B 类,须先裁决 |
| 7 | `decision_approved_at` 对 40 个老 run 仍是估算 | 它们没有 `stage_results/gate4.json`;不回写历史 manifest 是刻意的 |
| 8 | **全部改动未提交** | 用户未要求 commit |


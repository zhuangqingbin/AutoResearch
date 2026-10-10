# token 性价比优化方案 + 开发红线(brainstorm,待裁)

基准时间:2026-10-10 14:00(Asia/Shanghai)。分支 `scan-token-cost-20261008`(HEAD `d476db0`,另有 10-09 Codex 实施的 25 改 + 12 新文件未提交)。
本稿所有读数都来自真实产物:Codex rollout(`~/.codex/sessions/2026/10/08`)、Claude capsule
`reports_claude/scan/_failed/20261007T151001404046Z/capsule/usage/_token_usage.json`、九场 FAILED run 的 `failure.json`。
估算一律标「估」。前序:`docs/superpowers/plans/2026-10-08-scan-token-cost-reduction.md`(宿主退出研究回路)、
`docs/superpowers/plans/2026-10-09-scan-efficiency-recovery.md`(计量/恢复/实验入口)、
`docs/research/2026-10-03-research-drift-token-review-brainstorm.md`(别名漂移与行为指纹)。

---

## 0. 一页结论

1. **宿主退出研究回路已经成功**(10-08 Codex 第 1 场主会话扫描期间零调用),但一场 31 线程的研究角色自己吃掉
   Plus 5h 窗口 **84 个百分点**、周额度 13 个百分点。Claude 侧(10-07 第 5 场)研究角色 $37.1,其中决策卡 $22.6,
   卡成本的七成是输出(思考)token。**两边的钱现在都在研究角色,主因不同:Codex 是线程数 × 工具回合数,Claude 是 effort max 的思考输出。**
2. **投研视角的核心判断:token 的性价比分母是「决策增量」,不是报告质量。** 按本仓自己的普查:满卡 ∩ Hold 的 EV 全负、
   零 Overweight、7 笔已发布 BUY 全部来自早停卡、L3 在主尺上 −0.24pp、判断层显著负、唯一稳定结构是执行线(规则)。
   也就是说现行结构「9 只 finalist 每只都买全套(情报 7–9 次网查 + 满卡 + 复核)」是在为 EVI≈0 的深度付钱。
3. **方案分三层,按性价比排序:** 结构层(决策优先、深度按赌注分配、情报按需、前导瘦身)> 档位层(每次一个因素,过等价检验)>
   容量工程层(窗口预算从「告警」升格为「契约」,一夜一引擎,开发与生产分账)。目标:Codex 一场 ≤35 点(X1 验收)、Claude 一场 ≤$15 等价。
4. **开发红线 8 条**(第 4 节),每条配事故、机械执行点、会红的断言量。落点:`docs/dev-redline.md` 唯一真身 + CLAUDE.md/AGENTS.md 各一行指针 +
   零 LLM 的 `redline.json` 每场产出 + PostToolUse lint + `tests/redline/`。**只写进文档不算立了红线**(09-13、08-13 两条记忆都是这么死的)。
5. 10-02 以来 9 场全扫 0 发布,其中 3 场在研究花完之后死于确定性/契约路径,2 场死于额度。**推理之前先零推理回放**是红线第一条,
   因为它省的是整场的钱。

---

## 1. 现状读数(不估算)

### 1.1 Codex · 10-08 第 1 场 `20261008T143002429765Z`(31 headless 线程,gpt-5.6-sol)

| 角色 | 线程 | effort | 输入(含 cache) | 输出 | 每线程工具回合 | 占比 |
|---|---:|---|---:|---:|---|---:|
| L4 情报员 | 9 | xhigh | 4.19M | 62.5k | 7–9 次网查,上下文涨到 72–85k | 42% |
| L4 决策卡 | 9 | xhigh | 3.47M | 101.7k | 5–10 次 | 35% |
| 行业 brief | 9 | high | 1.12M | 15.1k | 2–3 次 | 11% |
| 复核 | 2 | xhigh | 0.69M | 20.9k | 4–8 次 | 7% |
| L3 精排 | 1 | xhigh | 0.26M | 16.3k | 4 次 | 3% |
| 策略师 | 1 | xhigh | 0.14M | 2.6k | 3 次 | 1% |
| **合计** | 31 | | **9.88M**(cache 85%,未缓存 1.50M) | **219k**(推理 112k) | 233 次模型调用,均 42k | |

- 每线程开线程固定 22.9k token;其中每线程都注入了 `AGENTS.md`(5,393 字符,含 superpowers 引导)——研究角色用不到。
- 5h 窗口 3%→87%,周额度 52%→65%。主会话 gpt-6.1-sol 在 14:27Z→16:38Z 之间没有一条 token 事件。
- 对比 10-07:全场输入 41.2M(主会话 34.7M)占窗口 72%;现在 9.88M 占 84%。**Plus 窗口不按原始输入计,xhigh 推理输出权重大;窗口预算只能按「场」记经验值。**
- `token_usage.md` 62 行 = 31 线程 ×(host 行 + headless 行)重复;10-09 已去重。

### 1.2 Claude · 10-07 第 5 场 `20261007T151001404046Z`($53.45,API 牌价等价)

| 角色 | 线程 | model / effort | 消息 | 计费输入 | 输出 | 成本 |
|---|---:|---|---:|---:|---:|---:|
| 主会话(中继) | 1 | opus-5-5 / max | 183 | 46.46M(cache 读 46.0M) | 188.8k | $16.36 |
| l4-card | 9 | opus-5-5 / max | 57 | 3.18M | 787.5k(87.5k/卡) | $22.62 |
| l4-intel | 7 | sonnet-5-5 / max | 59 | 2.22M | 465.5k(66k/只) | $6.79 |
| l3-rank | 1 | opus-5-5 / max | 5 | 0.24M | 213.6k | $5.12 |
| sector-brief | 9 | opus-5-5 / xhigh | 56 | 0.88M | 40.8k | $1.62 |
| macro-brief | 1 | opus-5-5 / max | 3 | 0.08M | 34.1k | $0.94 |

- 研究角色合计 $37.1;headless 之后主会话估 ≤$1(估)。
- **卡成本七成是输出**(787.5k × $20/M ≈ $15.8 / $22.62)。L3 一个线程 5 条消息思考了 213.6k。情报员是 Sonnet 但 effort=max,每只 66k 输出。
- 09-11 同配置单卡 $0.90、输出 18k;10-07 单卡 $2.51、输出 88k:别名漂移到 5.5 + max 的后果(10-03 稿)。

### 1.3 失败史:10-02 以来 9 场 0 发布

| 场 | 引擎 | 死在 | 性质 | 研究是否已花完 |
|---|---|---|---|---|
| 10-02 ×2 | Claude | gate1 / gate2 | 行业买卖误判、复核视图 | 否(早期) |
| 10-03 r3 | Claude | L4 情报阶段 | 5h 额度耗尽 | 一半 |
| 10-03 r4 | Claude | L4 完成后 6 票 BLOCKED | 契约缺陷 R4-1~R4-7 | **是** |
| 10-07 r5 | Claude | review2 | 情景收益精度契约 1e-9 | **是** |
| 10-07 ×2 | Codex | gate2 | 中继壳改写 detach result | 否 |
| 10-08 #1 | Codex | scan.assemble | `invalid manifest identity`(路径未 resolve) | **是** |
| 10-08 #2 | Codex | 行业 brief | Codex 额度耗尽 | 否 |

三场在研究花完之后死于零推理代码;两场死于额度。前者的钱是纯浪费(≈$100 + 一个 Codex 窗口),而且每次都要等下一晚才知道修没修对。

---

## 2. 投研视角:这些 token 买到了什么

**信息价值(EVI)框架。** 一笔研究支出的价值 = 它改变决策的概率 × 改变后的收益差。报告写得再好,如果决策不变,EVI 为零。
本仓的决策只有两个:(a) 当日唯一一笔相对 BUY(E6,`max_buys=1`);(b) 📌 持仓的红线盯梢。其余产出是记录。

用本仓自己的普查对照现行支出:

| 支出项 | 决策消费者 | 已有证据 | 对 EVI 的判断 |
|---|---|---|---|
| 满卡深度(P4–P6 情景/估值/多空) | E6 evidence 面 full=1.0 vs early_stop=0.4;评级 | 满卡 ∩ Hold 八张 EV 全负 RR<1;零 OW;7 笔 BUY 全来自早停卡(09-25) | **≈0**。满卡从未产出一笔 BUY,只在 E6 里多拿 0.6 分证据权重 |
| 早停(P3)的实质判断 | E6 硬门 `no_redflag`(停因 ∈ 基本面恶化/估值透支/涨停追高/数据不足) | 早停 238/239 停在 P3,停因全是实质判断,「约束在菜单不在 L4」(09-25) | **高**,但它是「拒绝」价值;大部分停因可由 slim 的确定性字段预判(待普查,见 3.1 S1) |
| 情报员(6 面盲搜,7–9 次网查) | 卡 P3/P4;E6 evidence +0.2 | intel 死票门 81 卡 0 命中(09-26);没有「情报改变了卡结论」的计数 | **未证明**。Codex 侧 42% 的钱在这里 |
| 行业 brief ×9 | L3 地形、L4 上下文 | 「描述性地形非方向指令」;板块轮动盲区是产品偏好 | 低;9 线程写 9 段描述是线程数问题不是内容问题 |
| L3 精排 | finalist 名单 | L3 对 L2 其余 −0.24pp(10-03);无正 alpha,有效的是拒绝(07-12) | 拒绝有价值;思考 214k 输出没有证据说明更多思考 = 更好拒绝 |
| 主会话中继 | 无 | 10-07 占 62%/84–90% | 0,已清零 |

**结论一:钱要按「决策赌注」分配,而不是按「候选数 × 固定深度」。** 每天只有一个 BUY 席位,9 只 finalist 里最多 2–3 只在硬门之后还站着
(10-03 稿:最近三场硬门后剩 2/3/1 只)。给 9 只都买满卡 + 情报 + 复核,是把 6–7 份研究花在不可能成为决策的票上。

**结论二:在主尺上研究层没有正证据,所以「最便宜的、保留选择权的版本」是理性的。** 这不是说研究无用,而是说现在没有证据支持
为更深的研究付更多钱;等价检验(10-03 规矩)正是用来证明「更便宜的档位决策不变」。

**用户裁定边界(方案在此之内):** 不做任何跨日复用(07-29);E6 是唯一 BUY owner(Wave12);早停只向下;
「不要跌势票」是产品偏好;降档前过等价检验(10-03);两引擎都改都验(09-15);参数只进 `scan_config.jsonc`(08-11/09-27)。

---

## 3. 方案(三层,按性价比排序)

### 3.1 结构层:让 token 跟着决策走

| # | 方案 | 做法 | 省在哪(估) | 风险 / 需要的证据 |
|---|---|---|---|---|
| **S0** | **最小可行扫描**(供裁定的总开关) | 日常扫描 = 确定性漏斗 + lite 卡(单阶段、无情报)+ E6 + 📌 盯梢;满卡只在 `stock-research` 按需跑 | Codex −60~70 点、Claude −70%(估) | 改 E6 evidence 权重(full/early_stop 同分,否则 lite 卡被系统性压分);评级分布会变(OW 本来就是 0);是产品裁定 |
| **S1** | 决策优先的确定性早停 | 把可由 slim 字段判定的停因(涨停追高、数据不足、资金流出、估值透支的量化版)做成 L4 前的零 LLM 筛;命中者直接出「确定性早停卡」,不派情报不派卡 | 取决于普查:早停 70% 里确定性可判的比例 | **先普查(零 LLM)**:回放历史卡,停因 ↔ 确定性字段的可判率、误判率。与「早停只向下」「约束在菜单」一致 |
| **S2** | 深度按赌注分配 | 全体 finalist 先出 lite 卡 → E6 硬门 + 排序在 lite 卡上算 top-k(k=2)→ 只对 top-k 派情报 + 满卡 + 复核 → E6 在满卡上终裁 | 情报 9→2、满卡 9→2、复核 2→2:Codex −35~45 点(估) | 同 S0 的 E6 权重问题;两段式 E6 要进 Wave12 权威;决策等价检验(当日 BUY 一致率) |
| **S3** | 情报按需、有界 | 情报不再默认全派;派时 `l4_intel.max_queries` 20→6(实际用 7–9,软顶没起作用);六面改三面(公告/突发/负面),题材梯队与机构动向由确定性 pack 给 | Codex 情报 −50%(估) | 盲搜防确认偏误的设计要保留(输入仍只有代码/名称/行业/日期);先做「情报被卡引用的条数」普查 |
| **S4** | 行业 brief 合并 | 9 线程 → 1 线程写 9 段(描述性、不判);或行业 pack 的确定性 brief(10-09 实验表已有 deterministic-v1 候选) | Codex −10 点、Claude −$1.5(估) | 行业 brief 是 analytical 档,内容不变只是线程数;需一次等价读数 |
| **S5** | 前导与上下文瘦身(零质量风险) | Codex:研究线程 `-c project_doc_max_bytes=0`(或 `model_instructions_file` 指向角色文件)不吃 AGENTS.md;`skills.max_context_tokens` 走 10-09 的 catalog_1000 探针;`tool_output_token_limit` 裁网查回灌。Claude:核 `--agent` 线程是否仍带 MEMORY.md(09-26 探针留的「待批 2 核」) | Codex −3~5 点、Claude 每线程 −10~15k 前缀(估) | 需要探针读数;`project_doc_max_bytes`/`model_instructions_file`/`tool_output_token_limit` 均为官方 config 登记键(已核) |

### 3.2 档位层:每次一个因素,过等价检验

| 引擎 | 角色 | 现档 | 候选 | 依据 | 入口 |
|---|---|---|---|---|---|
| Claude | l4_card | opus-5-5 / max | high | 输出 87.5k/卡是卡成本七成;09-11 Opus 5 同角色 18k 输出就够发布 | B1(10-03 配方),k=2,20–40 只,`research.noise_floor` 判 EQUIVALENT 才改 |
| Claude | l3_rank | opus-5-5 / max | high | 一个线程 213.6k 思考输出 | 同上,单因素 |
| Claude | l4_intel | sonnet-5-5 / max | medium | 情报采集不是判断,66k 输出/只 | 同上 |
| Codex | l4_intel | sol / xhigh(critical) | terra / high 或 sol / high | Claude 侧情报员早已是中档模型,这是两边最不对称处;占 Codex 42% | 10-09 入口 `intel_high/intel_medium` 已建,**建议加 model 臂** |
| Codex | l4_card | sol / xhigh | sol / high | 35% | 10-09 入口未覆盖 card(当时裁定不接受),按 B1 协议另开 |

实验本身花额度:Codex 20 任务 × 2 臂 × 2 次 = 80 invocation ≈ 一个多窗口;Claude ≈ $260 等价。Q13/Q14 要求每侧 ≥10 场真跑才宣布稳定节省,
太贵 → 先用冻结输入的离线实验拿噪声地板读数,采纳另审(`production_adoption` 恒 NOT_AUTHORIZED 的设计保留)。

### 3.3 容量工程层:窗口预算是契约

| # | 方案 | 做法 |
|---|---|---|
| C1 | 预算升格为门 | `budgets` 现在「不拥有截断权」。新增 `budgets.window`:Codex 读每条 `token_count` 事件自带的 `rate_limits.primary.used_percent`(headless 执行器已在解析事件),Claude 以去重后 weighted input 代理;软线(估 60%)停派可选角色(行业 brief 退回确定性 pack、情报关),硬线(估 90%)容量暂停(10-09 已有)。数字进 config,三件套 |
| C2 | 一夜一引擎 | 两引擎同晚都跑 = 同一产品付两次。生产只跑一边,另一边做等价/验证场;哪边是生产由用户定 |
| C3 | 开发与生产分账 | Codex Plus 同账号做开发 + 扫描必撞(10-09 周额度 93%;10-10 中午 NovelProject 把 5h 窗口用到 80%)。要么 Codex 只扫描,要么升 Pro,要么生产走 Claude |
| C4 | 失败隔离 | 10-08 补丁 04 + 10-09 recover-task / `--resume-run-id` 已落地;红线化为「不得回退到整场重跑」 |

### 3.4 叠加后的目标(估,需真跑验证)

| 引擎 | 现在 | S5 + S4 | + S3 | + 档位 | + S2 / S0 |
|---|---|---|---|---|---|
| Codex 一场(5h 窗口点) | 84 | ~70 | ~50 | ~35(X1 验收线) | ≤25 |
| Claude 一场(研究,API 等价) | $37 | ~$34 | ~$30 | ~$18 | ≤$12 |

不线性叠加;每一格都要用去重后的真计量 + 窗口读数验证,估算不得写进台账。

---

## 4. 开发红线(草案,待裁)

**定义「性价比」**:性价比 = 决策增量 / token。三把尺:
① 生产:每张发布卡、每笔 BUY 决策的 token 与窗口点数(`redline.json`);
② 档位:更便宜设置下的决策一致率(`noise_floor` / `stage_effort_v1`);
③ 开发:每个交付改动的会话 token(两引擎的开发会话按 cwd 汇总,周账本)。

**落点**:`docs/dev-redline.md` 是唯一真身;`CLAUDE.md` 与 `AGENTS.md` 各一行指针(两引擎);机械件三样:
`autoresearch/scan/redline.py`(零 LLM,每场产 `capsule/verification/redline.json`,进 GATE)、PostToolUse lint 扩展、`tests/redline/`(每条一个「删掉就红」的测试)。

| # | 红线 | 事故 | 机械执行 | 会红的断言量 |
|---|---|---|---|---|
| R1 | **推理之前先零推理回放。** 任何在推理之后执行的确定性阶段(assemble / verify-report / publish / metering / E6)改动,必须先在上一场冻结产物上零推理跑绿;`scan_run` readiness 内置此回放门,不绿不派推理 | 10-03 r4、10-07 r5、10-08 #1 三场在研究花完后死于零推理代码 | readiness 新步骤 `replay_postinference`;CI 对 `_acceptance/replays` 跑同一回放 | 回放门 PASS 记录在 `redline.json`;缺 = GATE 拒 |
| R2 | **宿主零研究。** 主会话 / relay 不承载任何研究上下文;headless 是唯一日常入口,mailbox 只做回退 | 10-07 主会话 62% / 84–90% | `redline.json` 的主会话行;mailbox 路径需显式 `--executor mailbox` | 主会话 weighted ≤ 0.1M/场(config 键) |
| R3 | **档位变更必附等价读数。** 改 model / effort / max_turns / 上下文预算 / `max_queries`,同一改动必须带 `equivalence_ref`(readout 路径存在且 verdict=EQUIVALENT),否则 lint exit 2 | 10-03 别名漂移:同 effort 输出 1.9万→5.1万零告警 | PostToolUse lint 扩展:`agent_engines.*`、`agent_tiers.*`、`l4_intel.max_queries`、`session.max_turns` 为受控键 | lint 违规数 = 0;`redline.json` 记实际 model/effort 与 config 一致 |
| R4 | **每一笔推理花费必须有决策消费者。** 新增或保留任何 LLM 角色 / 工具回合,登记「输出被哪个决策点读、改变了什么」并有计数;没有消费者的角色不派 | FN-1 家族(生产者无消费者);intel 死票门 0 命中;force_full_card 零调用点 | `agent_tiers` 每角色加 `consumer` 字段(决策点函数名);lint grep 消费点存在;`redline.json` 记每角色「被引用条数 / 改变决策次数」 | 角色的 `consumer` 缺 = lint 拒;连续 N 场「改变决策次数 = 0」的角色进待裁清单 |
| R5 | **预算是契约不是告警。** 每场 / 每角色的 token 与窗口预算写在 `scan_config.budgets`,runner 读真计量,超软线降级可选角色、超硬线容量暂停;预算只在 config,不在 prompt | 10-08 #2 撞额度 FAILED;`budgets` 块「不拥有截断权」 | `budgets.window`(C1);runner 读 `rate_limits` | 软线 / 硬线触发事件写 events;场后 `used_percent` 增量 ≤ 预算 |
| R6 | **失败隔离到最小单元。** 一个任务的契约错只重做该任务;禁止整场重跑;改码才换 run_id | 10-02~10-07 每次契约错 = 整场重来 | 已落地(补丁 04、recover-task、resume);红线 = 不得回退 | `redline.json`:重试任务数 / 重跑任务数;整场重跑 = 0 |
| R7 | **上下文只传指针与增量。** 派发按引用;角色不得读源码 / 测试 / 文档自验(边界 hook);工具输出有上限;每线程固定前导 ≤ 预算;agent 文件有字符预算,超了要附读数 | 09-15 研究 agent 翻源码自验;AGENTS.md 注入每个研究线程 | `by_reference=true`(已);边界 hook(已);`tool_output_token_limit`;`redline.json` 记每线程 first_ctx | Codex first_ctx ≤ 25k、Claude ≤ 35k(config 键);agent 文件字符数 ≤ 登记预算 |
| R8 | **计量先于优化,开发会话也算钱。** 任何「降本」改动必须附前后真计量(去重后 + 窗口读数),估算不入台账;测量脚本首次使用即进 `autoresearch/research/`(别留 scratchpad);开发任务不派并行 worker 除非任务可分且各 ≥N 文件;agent 不整文件读大文件 / 日志(grep / tail / sed -n);全量测试只看摘要;不重测记忆里已有的读数 | token_usage 62 行重复;10-07 诊断量错对象;前会话三份脚本丢失今天重写;10-09 周额度 93% | 周账本脚本(零 LLM,两引擎按 cwd 汇总);PR 模板「前后计量」栏;SDD 计划头部「token 预算」栏 | 周账本存在且更新;降本 PR 无计量 = 复审拒 |

**红线的红线**:每条必须同时有「事故」「机械执行点」「会红的量」三栏;缺一不立。只写进文档、没有执行点的规则不叫红线(08-13:lesson 注回块喂错层;09-13:台账写已关闭但没建过)。

---

## 5. 顺序与待裁

**先做(不花推理 token)**
1. 提交 10-09 这批(codex 引擎 221 绿;claude 引擎 1 红是引擎环境红)。
2. R1 回放门 + `redline.py` 骨架(读现有 capsule 产物,先只产 `redline.json` 不进 GATE)。
3. 三个零 LLM 普查,决定 S1–S3 的赌注:(a) 历史早停停因 ↔ slim 确定性字段的可判率;(b) 情报被卡 P3/P4 引用的条数与「改变结论」计数;(c) 行业 brief 对 L3 名单的影响计数。
4. S5 探针:Codex `project_doc_max_bytes=0` 的 first_ctx 读数;Claude `--agent` 线程是否带 MEMORY.md。

**然后(花推理 token,每级一场)**
5. 今晚一场生产(建议 Claude C2:Codex 一场 84 点且今晚窗口刚重置给 NovelProject 用过);验收按 10-08 稿:加权输入 ≤15.6M、主会话 ≤0.1M。
6. 档位实验:Codex intel(加 model 臂)、Claude B1(l4_card)。各一个窗口 / $260 等价。
7. S2 / S0 的决策等价检验(当日 BUY 一致率、硬门后名单一致率)。

**待裁**
- D10:S0「最小可行扫描」是否作为日常扫描的默认形态(满卡只按需)。
- D11:S2 两段式 E6(lite 卡硬门 → top-k 满卡终裁)是否进 Wave12 权威;E6 evidence 的 full/early_stop 权重是否同分。
- D12:红线 8 条的取舍与数字(主会话 0.1M、first_ctx 25k/35k、窗口软线 60% / 硬线 90%、并行 worker 门槛 N)。
- D13:一夜一引擎——哪边是生产。
- D14:Codex 容量(只扫描 / 升 Pro / 生产走 Claude)。
- D15:10-09 的「同 run 内情报复用」是否保留(它不是 07-29 退役的跨日 TTL 复用,但边界要你确认)。
- 沿用待裁:D6(B1 时机)、D8(Claude 主会话缺省模型)。

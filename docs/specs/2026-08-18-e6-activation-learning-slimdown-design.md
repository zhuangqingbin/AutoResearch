# 出手线转正 × 学习层大减法 —— 设计稿

- 日期：2026-08-18
- 状态：**设计定稿，零实施**（实施顺序见 §7；P2 裁决表须用户逐项过目后方可动工对应项）
- 调度权威：本稿接替 Wave10/Wave12 成为**现行总调度权威**（Wave12 未触发的 GATED 项去留在 §6 逐项裁定）
- 立案依据：40 个扫描日 / 56 run（2026-06-18→08-17）全量账本审计 + 五路只读侦察（产物盘点 / 账本统计 / 学习环存活度 / token 成本 / E6·删除面前提核查），关键读数见 §2

---

## 0. 一句话

40 个扫描日的账本证明：本系统是一台一流的**拒绝机**（门总量 +3.3pp、错杀 0~13%）和一台停摆的**购买机**（33 个连续 0 买日、历史正式 BUY 已实现胜率 0%），学习层的 LLM 半环冻结 3 周、其唯一自动腿被自己的元评估判为空转（17 版重标定 ΔIC +0.0004）。本波做两件事：**把被 Wave12 指定接班的 E6 相对 BUY 修好并人批转正（P0）**，**把账本判死的学习层机器激进物理删除（P1）**，附一张一次性建议裁决表（P2）。学习环修复（retro 欠账自动化 / 实验自动评估 / lessons→L4 注线）经用户 2026-08-18 裁定**不做**。

## 1. 用户裁定（2026-08-18，本波最高约束）

| # | 裁定 | 含义 |
|---|---|---|
| U1 | 只做「通出手线」+「激进减法」，学习环修复不做 | retro 15 日诊断欠账**一笔勾销**（amnesty），不建自动化补课机制 |
| U2 | E6 修好即**人批提前转正**，不等 ≥20 影子日治理门槛 | 接受 n=9（剔📌后 n=4）的样本风险；依据是**产品裁定**（Wave12 R-E2：成功日必须给一个可执行答案）+ 机制验证跑通，**不是 alpha 证明**（§2.1）。另：G5 原治理在结构上不可满足（§3.2），人批是唯一可行路径 |
| U3 | 删减刀法=**激进真删**（物理删除代码），非只标 RETIRE_ELIGIBLE | 数据文件一律**归档不删**（archive/，见 §8）；代码删除走独立 commit 可 revert |
| U4 | 积压人批项：设计稿附**建议裁决表**，用户一次性过目批 | §6；裁决权在用户，本稿只给建议与证据 |
| U5 | 「自动学习改 skill 的能力去掉」 | 即 08-13 B档（复盘自改 skill 能力下线 + prompt_patch 退役）——**已实施未提交**（190 文件 / 3994 绿），本波 E0 提交为基线；并顺此意扩展：权重自动重标定、experiment_registry、retro/t1 LLM 诊断义务一并退役（B档当时标「保留」的项，本稿以 ⚖ 裁决行请用户显式改判） |
| U6 | E6 转正后的记分册 = **relative_buy 账本本身** | registry 退役后不做双写；旧 buy_ledger / zero_buy_ledger 冻结 legacy |

## 2. 数据诊断（立案证据，全部来自实跑账本）

### 2.1 出手线（P0 立案）

- 正式 BUY 累计 9 笔（最后一笔 07-14 格力），主尺 `gap_c1_o2` 已实现 n=3~4：**胜率 0%、均值 −0.70%**；07-14 后连续 33 个 0 买日；08-10 起 7 个 run 全 0 BUY，评级全挤 Hold/UW。
- abstention 账本 19 日判定：**CORRECT 0 / FALSE 5 / NEUTRAL 14**——弃权从未被证明正确，有 5 天确证错过。
- E6 影子（`_relative_buy_decision.json`，e6.v1.1）：14 决策日 = BUY 10 · BLOCKED 4；成熟 9 笔 `gap_c1_o2` 均值 +1.33% / `rel_gap_market` +1.22%。**诚实拆解**：影子 BUY 6/11 次落在 📌 普冉（688766）上，剔📌后非持仓成熟样本仅 4 笔、均值 **−0.46%**——账面 +1.33% 几乎全由 07-29 普冉 +20% 单日扛起。**转正依据是产品行为（每日一个可执行答案）而非已证 alpha，报告须如实呈现（§3.6）。**
- BLOCKED 4 日根因（本波侦察定案，改写此前「数据源坏」假设）：
  - 08-07/08-10/08-11：`hard_gate.data_a` 日级团灭 12/11/9 只。链条 = self_review 的**文档卫生 lint**（退役符号引用 ×3）与**计量病**（usage_reconcile 把 limit-killed 行误判、streak 双 false）写 fail 行 → gate4 FAILED → `run_health.stage_results.failed` → `_data_contract_ok` 拒（`relative_buy.py:301-305`）。两处病根均已在 0605c14/82a2457 修复，但**结构缺陷仍在：与数据可信度无关的卫生/计量失败能连坐当日 BUY**。
  - 08-12：`hard_gate.contract` 逐票拦 9 只。卡全在盘上、decision_records 正常，但 `_l4_tasks.json` 9 只全停 RUNNING（`mark_success` 未执行）——task-book 收尾断档，contract 门如实拦下。同族：08-11 structural_audit 记 ARTIFACT_HASH_MISMATCH ×27（§4.E1b 并案调查）。
- G5 原治理门槛**结构性不可满足**：五守卫 research 域要求对照 legacy 买单 n≥10，而 legacy 永远只有 9 笔（已冻结）→ 永远 UNMEASURED → UNKNOWN → 不可晋升；且 registry `exp_relative_buy_owner` observations 恒空（无自动喂数腿）。
- 08-13/08-17/08-18 E6 已连续正常出票（688766 / 000779 / 688766；08-13 笔已成熟 +2.19%）。

### 2.2 拒绝侧 = 全系统唯一被证明的 alpha（本波不碰主链的依据）

- 门总量价值 +3.3pp（真实 −0.21% vs 门若不拦 −3.48% vs 市场 −3.58%）；主力真在 37 拦错杀 0%、业绩真兑现 14 拦 0%（v3 口径）。
- 早停 83 张成熟 78：可裁决桶全部 ≤0 → 是纪律非误杀。
- channel 账本 36 日 / 2.6 万票次：value +0.9%/胜率 57.6% 最好；healthy −1.1%、heat −0.9%、momentum −0.7% 持续为负（quota 再平衡 → §6.B1）。

### 2.3 学习层（P1 立案）

- LLM 半环停摆 ~3 周：retro 诊断停在 07-24 复盘日（欠账 15 日）；重标定停在 08-08；proposals 20 条 open（最老 54 天）；registry 5 实验全冻 PREREGISTERED（2 个有 38 obs 但 `promotion.py`/`rollback_watch.py` 无生产调用点=从未评估；3 个 0 obs）。
- 权重自动重标定：17 版累计 ΔIC **+0.0004**（changelog_ledger 自评「校准空转」），多重检验风险；读侧（`common/scoring.py:423 pick_weights` ← `scan/universe.py`）健康保留。
- t1_review：22 日 155 行，够格终判仅 8 行（62.5% 准）且全 UW 侧；LLM 腿 08-11 后零派发（曾静默漂移 fable 2× 价）。
- ensemble 双复核折回：6 次成熟折回 = **折对 0 / 折错 1 / 折平 5**（唯一折错：07-28 688766 UW→Hold 次日 −2.68pp），摊销 ~$2.3/日。
- lessons 7 条全部只注入 L3（l4_intel/l4_card 零消费，第 4 次复发）；lesson_yield 唯一可评估条累计 −0.92pp 已双路提名退役。
- 确定性半环健康且被生产消费（保留）：nightly_close 账本族、cross_calib/t1 校准块注入 L3+L4 prompt、GATE4 硬门、dossier 注入。

### 2.4 成本与冗余（P1 第二立案）

- 日均 $40.77 / 8.62M 加权（清洁基线 $29.6-34.5、目标 ≤$35；5 日 3 超标，超额全在主会话 $7-11.7）。角色占比：l4-card 31% / 主会话 20% / gp壳 19%（385 壳/5日，每壳 ~72k 上下文税回 284 token 中位）/ l4-intel 14%。
- 高成本低消费：macro full 桥断 3 周（launchd 周日 harvest 产孤儿 data.md，`macro_state.json` as_of 07-27 恒过期）；sector-brief 研判段最终只落 summary 一行（`report_sections.py:579-615`）；`L3_evidence/` 每日 203 文件中 **196-201 个 18 字节空壳**；dossier 池 89 只欠 63 建档（07-27 批 24% FAILED 后零新增）。
- 资产风险：precedents.db 650 条、31 dossier、全部 jsonl 账本在 gitignore 外**零备份零版本**。

## 3. P0 · 出手线转正（E6 相对 BUY）

### E0 基线提交（前置，独立 commit）

把工作区 190 文件（+1609/−1498，08-13 B档实施，3994 绿）作为独立 commit 提交。这就是 U5 的「自动学习改 skill 能力去掉」主体：复盘/反馈永久断写 `.claude/**`、prompt_patch 管线退役、毕业改提名制。提交前重跑全量测试确认仍绿。

### E1a gate4 失败分级：卫生/计量病不再连坐当日 BUY

**现状**：`_data_contract_ok`（`relative_buy.py:292-309`）读 `run_health.json`，`stage_results.failed` 非空即日级 data_a=False。gate4 的 fail 行三类混装：数据说谎（价格断言不符、brief↔summary 数字不一致）、文档卫生（退役符号引用）、计量病（usage_reconcile streak）。后两类与「今天的卡能不能信」无关，却能团灭全天 BUY（08-07/10/11 实锤）。

**改法**：
1. `self_review.py` 为每类 check 定义 `FAIL_CLASS ∈ {data, hygiene, metering}` 单一事实源映射（价格断言/数字对账/白名单外取数 → data；product_shape/退役符号 → hygiene；usage_reconcile → metering），gate_fires 行落 `fail_class` 列。
2. `health.py` `stage_results_health` 在 `failed` 旁新增 `failed_data`（仅含「有 data 类 fail 行」的 stage）；`failed` 原义不动（gate4 仍因任何 fail 而 FAILED——发布卫生照旧硬门）。
3. `_data_contract_ok` 的第 4 判改读 `failed_data`（`relative_buy.py:301-305`）；其余四判不动。RULE_VERSION 升 `e6.v1.2`。

**探针（变异测试）**：合成 gate_fires 仅含 hygiene fail → gate4 FAILED 且 data_a 必须为 True；含一条 price_claim data fail → data_a 必须为 False。改错任何一处映射测试必红。

### E1b task-book 收尾自愈 + 08-11 hash 失配并案

**现状**：08-12 卡全在盘、book 全 RUNNING → contract 门逐票团灭；08-11 structural_audit 记 ARTIFACT_HASH_MISMATCH ×27（同族疑点，把回滚杆①的 streak 清零）。`l4-stock.js:329-335` 已有 `task_book_success_failed` 记账口但无自愈。

**改法**：
1. `l4_tasks` 新增 `reconcile <date>` 子命令：对 status=RUNNING 的票，若 `details/` 卡在盘 + 评级可解析 + decision_record 在场 → 以盘上文件现算 content_hash 补记 SUCCEEDED（标 `recovered=true`，落记账行）；卡不在盘 → 保持 RUNNING（contract 门拦得对）。
2. 挂点：`publisher._run_publish` 在 `publish_run_observation`（写 `_relative_buy_decision.json`，`post_run.py:624-632`）**之前**调用 reconcile——即 E6 决策看到的是自愈后的 book。
3. 并案调查 08-11 的 27 连 hash 失配：确认是重跑事故噪音还是 mark_success 写入竞态；若属噪音，为 structural_audit 增加 REBUILDABLE 豁免类别（`structural_audit.py:56-63` 现只豁免 prompt），否则修竞态。调查结论落 `docs/research/`。

**探针**：fixture 造 RUNNING book + 卡在盘 → reconcile 后 relative_buy contract 门必须放行且 book 行带 recovered 标；卡不在盘 → 仍 RUNNING 仍拦。

### E2 转正开关与治理换轨（registry 退役下的激活）

- `scan_config.jsonc` 新增块（走「配置三件套」：白名单 + 消费点 + 测试）：
  ```jsonc
  // 批次 1 落地时先写 "shadow"（行为不变）；裁决表 A1 批 GO 后才翻 "active"
  "relative_buy": { "mode": "shadow", "exclude_pinned": true }
  ```
  代码默认 `shadow`（空配置不改变行为）；`relative_buy.py:509-511` 的 mode 硬拒改为按 config 放行 `{shadow, active}`，翻 active 时 RULE_VERSION 升 `e6.v2.0`。
- **激活记录**：用户在 §6 裁决表批 A1 后，由裁决会话把裁定写入 `changelog.jsonl`（feedback skill 正常通道），config 翻 `active`。无 registry 事件（registry 已退役）。
- **五守卫替代**：一次性 preflight 体检脚本（`python -m autoresearch.scan.relative_buy preflight`）打印 `relative_ledger.summarize()` 全量读数 + contract_error 计数 + 最近 3 日 BUY/BLOCKED 序列，供用户 GO 前过目。不再有自动晋升机器。

### E3 切换包（消费者重指向，file:line 来自侦察实测）

| 消费点 | 现状 | 改后 |
|---|---|---|
| `scan/decision_finalize.py:24-30,264` | proposal/qualified 按 rating ≥OW | active 模式下 BUY proposal 只来自 decision 文件 `buys[]`；rating 降级为 research_rating 证据字段（Wave12 `:466`） |
| `scan/report_sections.py:332,355` | 组合视角/仓位 overlay 数 OW | 读 decision 文件 |
| `scan/report_sections.py:732` + `scan/health.py:245-247` | self_review banner / count_buys 数 OW | 读 decision 文件（legacy OW 计数改名保留为证据行） |
| `scan/brief.py:639-690` | 生产 BUY 行（旧绝对门）+ 影子行 | mode=active 时 🕶→✅ 自动换标（`brief.py:652-653` 已按 mode 分支，实测无需大改）；旧 OW 行降为「研究评级分布」一行 |
| `self_review.py:1281-1301` brief_lint ⑤ | active 契约影子期出 info | mode=active 自动升 fail：成功 run BUY≥1、BLOCKED 不得渲染成成功（**Wave12 R-E2 由 lint 硬锁**） |
| `learning/zero_buy_ledger.py` | 每晚 roll 全历史 | 按 activate 日切断新行 + render 加 legacy 横幅（docstring `:13-17` 预告照做） |
| `learning/buy_ledger.py` | 旧 OW 买单账 | 冻结 legacy（保历史渲染，停新行） |
| `learning/retro.py:126` `bought` 列 | ≥OW | **语义不动**（保 legacy 轨可比性）；relative BUY 的结算走 relative_ledger 既有链（`outcome_for` 读 attribution，已验证 08-13 成熟 +2.19%） |
| 哨兵 | sentinel 日跳 L3/L4 → E6 记 NO_RUN | **本波不改义**（Wave12 `:355` 的哨兵改义 deferred，§9）；NO_RUN 不进 action_coverage 分母，语义诚实 |

### E4 顺序契约验收（只验不改）

侦察证实 publisher 内顺序已是：build_summary → **decision 写盘**（`publisher.py:314`）→ brief 渲染（`:373-376`）→ brief_lint（`:389-397`），且 brief_lint 无 mtime 判据（记忆 `brief-reads-provisional-relative-buy` 所述病灶在现行代码中不成立，疑已被 B档波修复）。本项收一个**内容同源探针**：断言 brief ③ 的 BUY 与 decision 文件一致（进 brief_lint data 类）。

> **2026-08-19 复核 Critical 更正（修复轮 1）**：本节原文曾在上一句之后另收一条 mtime 顺序判据
> （「decision 文件 mtime ≤ brief.md mtime」），已删除，不再实施。理由：`_relative_buy_decision.json`
> 有**两个合法写者**——`publisher._run_publish`（brief 渲染之前）与 `post_run observe`
> （`.claude/skills/scan-market/SKILL.md` STAGES 步骤 5，brief 落盘之后**无条件原子重写**该文件）。
> 立案侦察只查到写者 1，漏了写者 2，故当初误判「mtime 顺序 = 生成先后」。真实归档为证：
> `context_claude/scan/2026-08-17/_relative_buy_decision.json`（mtime 22:15:36）晚于
> `reports_claude/scan/20260817_2215/brief.md`（mtime 22:15:33）整整 3 秒，但两边内容同源
> （代码 000779 一致）——这是 `post_run observe` 跑过之后的**健康日**，mtime 顺序判据会对它
> 误报 fail。内容同源判据不受影响，继续照收。

### E5 转正后的记分册与诚实呈现

- 记分册 = `context_claude/learning/relative_buy.jsonl` + `reports_claude/learning/relative_buy.md`（U6）。**新增分层**：📌/非📌 两栏汇总并排（剔📌均值 −0.46% 的教训固化为永久栏目），MATURE_MIN_OBSERVATIONS=20 的 IMMATURE 横幅保留至满 20 决策日。
- **`exclude_pinned: true`（⚖ 裁决 A2）**：BUY 从非📌合格候选中选 rank1；📌 票仍进候选表但标 `excluded_reason="pinned_holding"`（持仓建议走 pinned 复核路，同「保送不算」判例，防记分册再被单票污染）。若当日非📌合格为 0 → 诚实 BLOCKED。

## 4. P1 · 激进减法（删除地图）

> 通用纪律：① nightly_close/prelude 的账本表是**字符串动态 import**（`nightly_close.py:93-124`、`prelude.py:459-482`），每删一个模块必改表并同步 `test_nightly_close`/`test_prelude` 的表断言；② 双职测试**只摘用例不整删**（下表逐个点名）；③ 数据文件一律移入 `archive/<date>/` 不物理删除；④ workflow js 改动用 AsyncFunction 解析探针验证（`node --check` 假绿灯判例）。

### D1 experiment_registry 家族（⚖ 裁决 A3；~2400 行 + 单职测试）

- **整删**：`learning/experiment_registry.py`(1046) + `promotion.py`(297) + `rollback_watch.py`(226) + `experiment_template.py`(311) + `mainflow5d.py`(486，唯一活跃写者，其使命就是喂 registry)。
- **四个拆点**：① `nightly_close.py` 删 `_exp_observe` 步（七步表→六步，`test_nightly_close` 断言同步）；② `evidence_manifest.py:646` 删 `_add_registry` 段；③ `scan/gate0.py:112-126` 删 `assert_may_block` 及 CLI BLOCKING 模式（结构性不可达，一并退役）；④ `gate_recal.py:398-428` 删 `--register` 分支；`research/consensus.py:255`、`candidates.py:148` 摘 registry 调用。
- **勿误伤同名异物**：`factor_lab.promotion_sortcol/ic_promotion_table`（因子晋升表）、`feedback_store.promotion_candidates`（经验→提案）、`style_spread.PROMOTION_GATE`——与本家族无关，保留。
- **双职测试改造**：`test_l2_regime_wiring_probe.py:43-62` 的治理证据源从「有无 ACTIVE 实验」换成「config regime_aware 键 + 显式裁定注释」；`test_evidence_manifest.py`、`test_gate_recal.py` 摘对应断言。
- 归档：`experiments/registry.json`、`monitors.json`（孤儿数据，零代码引用）、`exp1_mainflow5d.jsonl`。文档：SKILL.md:177、STAGES.md「实验晋升与回滚控制面」整节。
- **治理模型替代**（写进 STAGES）：改名单/评级/门/调度的 challenger = 影子账本直接呈证（shadow/ 既有）→ proposals 人批 → config/代码改动走开发会话。这正是用户实际在用的模式。

### D2 权重自动重标定（⚖ 裁决 A4；函数级摘除）

- 摘：`retro.recalibrate_and_log`（`retro.py:1158-1187`）+ `top_weight_changes`；`prewarm.py:169-188` `--calibrate` flag；`feedback_store.log_change/snapshot_weights/rollback_weights` 三函数；`retro-playbook.md:50` 该步 + scan-retro SKILL description 的 "auto weight recalibration" 字样。
- **整删** `changelog_ledger.py`(174) 及三挂点（prelude `_ledgers` 表 + prelude `_learning_health` heartbeat 行 + nightly `_ledgers` 表）。
- **保留**：`factor_lab.calibrate/extend_plan`（降为人工 CLI，想重算权重时手动跑）；`common/scoring.py:423 pick_weights` 读侧、`context_claude/factor_lab/weights.json`（冻结现值）、replay PIT 快照。`weights.<sha8>.json` 快照 4 份归档。
- 测试：`test_changelog_ledger.py` 单职随删；`test_factor_lab.py:383-401`、`test_feedback_store.py` 只摘对应用例。

### D3 t1-review LLM 腿（⚖ 裁决 A5；确定性侧全保留）

- 删：`.claude/workflows/t1-review.js`(128，两 agent 均内联 prompt，无独立 agent def)；`t1_review.py` 的 `finalize`(655-687)、候选三件 `load/upsert/promote_candidates`(416-451)、`build_and_stage` 打包尾巴（内核 build_scorecard+_stage 保留，backfill_day 在用）；CLI `build --json`/`finalize` verb。
- **role 收口（12→10）5 处同步**：`scan_config.jsonc:50-51` 两行 + 36 行注释；`user_config.py` `_AGENT_ROLES`(146-151)/`_ROLE_FALLBACK`(181-183)/main docstring(457-476)；`usage_reconcile.py:78-80` 注释；scan-retro SKILL 快环编排段改写为「nightly 自动确定性回补，无 LLM 段」；`test_agent_defs.py` 摘 t1-review.js 断言（勿整删，还锁另三张表）。
- **保留**（生产在用）：`build_scorecard/render_scorecard_md/_stage/append_ledger/verdict/pending_pairs/backfill_day/gap_finalize_pending/_industry_neutral_gap/ledger_tail_summary/render_t1_calibration_block/_NON_GENUINE_LANES` + nightly `t1_backfill`/`t1_gap_finalize` 两步 + L3/L4 校准块注入（`l3/prompt.py:367`、`l4/prompts.py:45-46`）。
- 后果如实记账：t1 自动立案 prompt_rule 通道随之关闭——未来同类规则须人工经 feedback skill 立案（§6.D 对现存 4 条候选给出处置）。`t1_candidates.jsonl`(13 行) 归档。

### D4 macro 周度 harvest 空转腿

- `launchctl bootout gui/$UID com.tradingagents.macro-harvest` + 删 `~/Library/LaunchAgents/com.tradingagents.macro-harvest.plist`；repo 删 `scripts/macro-harvest.sh` + `scripts/com.tradingagents.macro-harvest.plist`。
- **保留**：`macro/harvest.py`（full 档手动流程第 1 步）、`state.py` 读写、frame/prelude 的 presence-gated 挂点（零成本）。SKILL/playbook 注明「无周度 cron；full 档全手动」。盘上 3 个孤儿 data.md 目录归档。

### D5 L3_evidence 空壳产线

- 删 `l3/evidence.py` 的 `harvest_l3_evidence`(44-83) + `prompt.py:378` 调用 + import；`test_l3_evidence_lake.py` 单职随删；`test_l3_prepare.py:29-37`/`test_agents.py:29-40` 双职改 fixture。
- **保留** `load_l3_input` presence-gated 读侧（历史目录仍可读）。行为微变如实记账：`_delta_filter`（`prompt.py:149-152`）自然少一路「有证据保留」信号——按空壳率 196-201/203，该信号本就近似不存在。省：每日 203 文件/812KB + 3 个 tushare bulk 端点取数时间。

### D6 sector-brief 研判段（⚖ 裁决 A6；四处联动）

- ① `sector/brief.py` 摘 `VIEW_HDR/extract_view/parse_direction/_DIR_RE`(~15 行)；② `.claude/agents/sector-brief.md:26-27,35-36` 改单段契约 + `sector-playbook.md` lite(27-28,35) 与 **full(47)** 两档同步；③ `report_sections.py:579-615` `_sector_view_section` 整节删 + `:982` 调用点——L5 行业方向叙事由 `market.py:259-267` 确定性 top3 独扛；④ **`sector_ledger.record_calls` 侧同删**（`publisher.py:447-451` 挂点；`record_top3` 侧保留，`test_sector_top3.py` 不动）；⑤ `scan-market.js:285` prompt 文案。
- 收益：每份 brief 少一半生成负担（~$0.9/日）+ 删「行业嘴 MTM」这只没有下游的仪表。

### D7 watchlist 残余（侦察修正：账本 07-17 已删，仅剩一块肉）

- 删 `sector/pack.py:28` `_WS_WATCHLIST` + `select_briefing_sectors` 观察单来源分支(~10-15 行) + `test_pack.py:90` 用例 + STAGES.md:108/206 两处文案；`context_claude/watchlist.csv`(7 行，expiry 最晚 09-30) 归档。
- **勿动**：`watchlist_trigger` lane 常量（`gates.py:34`、`t1_review.py:57`——历史产物读侧语义）；`test_prelude.py:91-113`/`test_journal.py:14-41` 双职测试保留。

### D8 回滚杆处置（Wave10 §B5 三根）

- ③ abstention v1：**本波拔**（判据 v2 ≥10 mature day，实测 19/19 全成熟）。拔前补一次「任意两日重跑一致」复核；删 v1 生产列/代码，保历史报告。
- ①② streaming_l4 旧批量 GATE3 + `_ensemble.json` 旧批量双读：**不可拔**（streak 4/10，被 08-11 hash 失配×27 清零）。E1b 并案调查若判 08-11 为可豁免噪音，杆①分母重算后另行到期。
- 顺带：D1 删 registry 后，杆判据文本中的 registry 表述同稿改写。

### D9 gp 壳合并（≈−4~5 spawn/日 + −2 spawn/股）

- scan-market.js：#1+#2+#4 frame+双 pack 校验三合一；#12 两条 SENTINEL_PINNED 支线合一；#20+#21 l4-prep+dispatch-plan 合一；流式路 #21+#23（python 侧让 `l4_tasks init` 顺带回显 plan JSON）。retry 分支与 #11（变量插值依赖）不并。
- l4-stock.js：intel-guard+intel-status → python 新增 `intel_finish` 一条命令回一行 JSON；task-success+recordL4 合一（失败路同理）；ens-dump 并入同壳（仅当 ⚖A7 裁定保留 ensemble 时该壳仍存在）。preflight 与 slim（并行 barrier）不并。
- 13 股日合计 ≈ −30 spawn ≈ −$3/日 + 主会话唤醒轮次同降。`usage_reconcile`/`test_agent_defs` 无需改（role 不变）。每处改动配 AsyncFunction 解析探针。

### D10 staging purge（按文件类白名单，绝不整日删）

- 新增 `python -m autoresearch.ops.purge [--apply]`（默认 dry-run 报告可清理量）：
  - 顶层 `*_slim*.md` >14 天（硬下限 T+2 的 process_backfill 读者，留余量）；
  - 日目录内仅 4 大件 >10 交易日：`L1_scored_full.csv`(1.6M)/`L1_recall_top1000.csv`/`L3_evidence/`/`L3_news/`（≈3.6M/日 ≈42%；代价=该日不可再 PIT 回放，如实记账）；
  - `t1_review/` 仅 final_verdict 非空的日；
  - **永不触碰**：`retro/`、`_l4_tasks.json`（回滚杆①口粮）、`finalists.csv`/`meta.json`、`details/`、`_ensemble_*.json`、`shadow/`、`_candidate_passport.json`、`stage_results/`、`_token_usage.json`（>30 天另议）。
- 不入自动调度（U1 精神：不添新自动腿）；prelude 汇总屏加一行 dry-run 提示。

### D11 知识资产备份（唯一新增的自动步）

- `nightly_close` 末位 `_backup` 步：tar `context_claude/knowledge/` + `context_claude/learning/*.jsonl` + `factor_lab/weights.json` → `backups/knowledge_<date>.tar.gz`，保留最近 14 份。precedents.db 650 条 / 31 dossier / 全部账本是**不可重算资产**，当前零备份——这是本波唯一允许的新自动化（数据保护性质）。

### D12 dossier 覆盖池收口（⚖ 裁决 A8）

- 执行 cap=30：池 89 → 按「📌 + 近 30 日 finalist 出现频次」修剪至 30；`pending_init` 63 只欠账随池重算（预计余 ~5-10 只真缺口）。
- 缺口补建走既有 dossier-init workflow（$1.92/份，一次性 ≤$20），修复 07-27 批 24% FAILED 无重试的问题（FAILED 后单票重派一次）。δ回写机制不动。

### D13 intel 情报可信度硬化（⚖ 裁决 A9；吸收提案 #4/#5/#20 的确定性部分）

- `intel_guard` 增两条确定性 lint：价格/涨停断言必须带 `source_url`（零 URL 即拒稿该断言段）；**他票**涨停/涨幅断言与湖 OHLCV 对账，不符标〔未核〕（pr_20260714_006 涨停捏造 P0、pr_20260716_003 日期焊接、08-13 5 断言 3 捏造——同族第 3 次复发）。
- `.claude/agents/l4-intel.md:40` 的他票对账豁免句改为对账要求；`l4-card.md` 增三条已验证判断规则（§6.D #17/18/19 的固化，人批设计波改 `.claude/**` 合法，不经学习流程）。
- 注回接线（lessons→L4）**不做**（U1）。

## 5. 不做清单（本波显式排除，防走样）

1. retro 诊断欠账自动化、实验自动评估、proposals 自动裁决——U1；15 日欠账 amnesty，prelude 欠账 nag 摘除。
2. lessons→L4 注回接线——U1（D13 用「直接写进 agent 指令」替代其中已验证的 3 条）。
3. 哨兵改义（Wave12 `:355` 风险预算档）——deferred，E6 在 sentinel 日记 NO_RUN 诚实语义；reopen 条件=转正后实测 sentinel 日频次过高（>20%）。
4. L4 卡任何形式复用 / 菜单 carryover / 观察单复活 / L3.5 / L2 上模型 / 追当日大涨 / 业绩预告事件通道 / northbound / accumulation / OTEL / stable_context_blocks——既有红线全维持。
5. ensemble 对粘性 ≥OW 票逐日重付的「复用式省钱」——R5 红线；本波对 ensemble 的处置只有 ⚖A7 的「整体退役与否」一问。
6. pinned 评级翻转治理（300857 六日四换档、连续两日卖飞）——只观察不动作：E6 转正后 pinned 建议与 BUY 解耦（E5 exclude_pinned），churn 读数在 pinned_ledger 持续累计，攒 ≥20 日再议。
7. 主会话编排税专项——D9 壳合并顺带缓解，不另立项。
8. macro full 修桥（自动派 LLM 节）——保持全手动。
9. codex 引擎侧——空置不动。
10. 新召回路/新因子——channel quota 再平衡（§6.B1）是唯一动召回的项，且只动配额不动路。

## 6. P2 · 建议裁决表（用户一次性过目；裁定权在用户，本表只给建议+证据）

### A. 本稿核心 ⚖（决定 P0/P1 形状）

| # | 事项 | 建议 | 证据/理由 |
|---|---|---|---|
| A1 | **E6 转正 GO/NO-GO**（E2 config 翻 active） | **GO**（E1a/E1b/E4 探针绿 + preflight 体检过目后） | §2.1；U2；G5 结构性不可满足 |
| A2 | E6 BUY 剔除 📌 持仓票 | **是**（exclude_pinned=true） | 影子 6/11 落📌普冉；剔📌 n=4 均值 −0.46%；「保送不算」判例同族 |
| A3 | experiment_registry 家族整删（B档曾标「保留」，请改判） | **删**（D1） | 5 实验全冻、promotion/rollback_watch 零生产调用、observations 无喂数腿 |
| A4 | 权重自动重标定退役（B档曾标「保留」，请改判） | **删自动腿**（D2；factor_lab CLI 保留人工重算） | 17 版 ΔIC +0.0004；DSR 多重检验警告；读侧不动 |
| A5 | t1-review LLM 腿退役（B档曾标「保留」，请改判） | **删**（D3；确定性 scorecard/终判/校准注入全保留） | 22 日仅 8 个合格终判、全 UW 侧；08-11 后已实质停摆；曾漂移 fable 2× 价 |
| A6 | sector-brief 研判段砍除 + sector_ledger.record_calls 同删 | **删**（D6） | 研判段最终只落 summary 一行；行业方向由确定性 top3 独扛；「行业嘴 MTM」无下游 |
| A7 | ensemble 双复核（ens_review role + 折回机制）整体退役 | **删**（历史 `_ensemble_*.json` 读侧保留；role 收口 10→9） | 6 次成熟折回=折对 0/折错 1/折平 5；摊销 ~$2.3/日；注意：sell_review 曾是用户 Wave1 要求，现有 6 折数据不支持其价值，请显式改判 |
| A8 | dossier 池 89→30 修剪 + 缺口补建（≤$20 一次性） | **是**（D12） | 63 只 pending 欠账大于产能；cap=30 本就是池的自定义 |
| A9 | intel 可信度硬化（D13：URL 必填 + 他票对账 lint + 3 条规则固化进 l4-card.md） | **做** | 情报捏造第 3 次复发；确定性 lint 零 LLM 成本 |

### B. 配置/召回（一行 config + 记账）

| # | 事项 | 建议 | 证据 |
|---|---|---|---|
| B1 | channel quota 再平衡：value 250→**312**、momentum 250→**188**、heat 150→**112**、healthy 150→**112**、growth 150→**112** | **批**（accumulation 120→90 为幽灵行**跳过**——该路 07-11 已退役无生效点） | channel_ledger 36 日版：value +0.9%/57.6% vs momentum −0.7%/heat −0.9%/healthy −1.1%；附注：18 日版曾警告单相位覆盖，36 日已跨相位 |
| B2 | R-F1 吸筹死配额 floor 12→0（`l2_stratify.py:36-37`） | **批**（按 Wave12 原文先出 replay VariantSpec 菜单 delta 报告随附，不走 20 日实验） | 08-06 实测菜单 96/203 是配额救回；`l2_lane_reserved` 污染三消费者 |
| B3 | R-F2 reversal_confirm 摘出 `recall_channels`（`scan_config.jsonc:88`）+ 登记 reopen 条件（vol_ratio_20 接入 L1 帧后重开 A/B） | **批** | 名义启用实际恒空 4 周+（硬门因子从未接入生产帧，`common/scoring.py:209-212`） |

### C. lesson 退役（CLI 一条命令 + 记账）

| # | lesson | 建议 | 证据 |
|---|---|---|---|
| C1 | `ls_prioritize_uptrend_with_support`（不要跌势票） | **退役** | lesson_yield 32 命中日累计 Δ−0.92pp + MTM 反驳 34/支持 127→机判 refute + pr_20260725_003 双路提名；注：07-17 裁定「产品偏好非预测主张」——退役的是 lesson 注入，**不是**恢复推荐跌势票（L4 卡判断纪律仍在） |
| C2 | `ls_momentum_recall_quota_swing_horizon` | **退役** | 建于已作废的 T+5 swing 口径（07-10 超短裁定 + 08-05 换尺两度废其根基）；pr_20260808_001 提名 |
| C3 | pr_20260725_002（`ls_l3_main_inflow_overread_reversal` 摘 guard 提名） | **关账**（moot） | 该 lesson 已于 07-28 retired |

### D. 20 条 open proposals 逐条建议（三种裁法：实施(本波)/闭案/留(明示非欠账)）

| id | 挂账 | 建议 | 一句话理由 |
|---|---|---|---|
| pr_20260625_001 L1↔L4 资金口径核对 | 54天 | **闭案** | E6 转正后 BUY 不依赖该口径共振；deferred 前置早已过期无人动，无行动价值 |
| pr_20260702_002 CMF 容差半共振 | 47天 | **闭案** | OW 三门在 E6 下降为研究评级，不再是 BUY 卡点；门重标定证据 IMMATURE |
| pr_20260712_002 L3 菜单分布观测 | 37天 | **闭案** | 本波在做观测减法，22 账本已过剩 |
| pr_20260714_006 情报涨停捏造 P0 | 35天 | **实施** | 并入 D13 |
| pr_20260716_003 日期焊接+零URL | 33天 | **实施** | 并入 D13 |
| pr_20260717_003 六路 quota | 32天 | **实施** | 以 36 日版数字批（B1），本条关账 |
| pr_20260717_005 conviction 标度 [0,100] | 32天 | **实施** | schema range 校验一行，本波顺带 |
| pr_20260721_002 market_pack 当日切面 | 28天 | **留**（低优先，非欠账） | 有读者价值但非本波两主线 |
| pr_20260725_001 event 路取证 | 24天 | **留**（等数据） | 判据已登记：`--variant plus_event` ≥10 日 unique_excess>0 才有资格提启用 |
| pr_20260725_002 lesson 摘 guard | 24天 | **关账**（moot） | C3 |
| pr_20260725_003 uptrend lesson 退休 | 24天 | **实施** | C1 |
| pr_20260727_002 rz 因子退休 | 22天 | **实施** | 三 regime 全不显著；weights.json 冻结前把 rz 置 0 并记账 |
| pr_20260727_003 板块动量议题关闭 | 22天 | **实施**（关账，不建 harness） | 三条独立证据全负 |
| pr_20260728_001 非空门→内容门 | 21天 | **关账**（已达成） | 现行 pack-check 已是 python JSON 解析校验（scan-market.js 实测） |
| pr_20260729_002 beta-stripped 判读 | 20天 | **闭案** | 终判已走 gap 尺中性化该病；t1 自动立案通道随 D3 关闭 |
| pr_20260808_001 momentum lesson 退休 | 10天 | **实施** | C2 |
| pr_20260810_001 商品符号按链位 | 8天 | **实施** | 固化进 l4-card.md（D13），不走 lesson 机制 |
| pr_20260811_001 winner 排除项 | 7天 | **实施** | 同上 |
| pr_20260811_002 具名催化落地检查 | 7天 | **实施** | 同上 |
| pr_20260813_001 注回块只喂 L3 | 5天 | **部分实施** | lint 腿并入 D13；注回接线闭案（U1） |

## 7. 实施顺序与验收

| 批 | 内容 | 验收 |
|---|---|---|
| 0 | E0 提交 B档基线 | 全量测试绿后独立 commit |
| 1 | E1a + E1b + E4 探针（E2 config 仍 shadow） | 变异探针 4 组全红→绿；一个真实扫描日 E6 影子无非数据性 BLOCKED |
| 2 | **裁决会话**：用户过 §6 全表 → 批 A1 后 E2/E3 切换包上线（config 翻 active）+ B/C/D 组一行改动逐项落地 | preflight 体检打印；切换日报告头条=相对 BUY；brief_lint active 契约生效（BUY≥1 或显式 BLOCKED） |
| 3 | D1-D7 + D13 删除与硬化（依批准范围） | 每删除项：grep 消费点零残留 + 双职测试保留清单核对 + 全量测试绿；workflow js 改动过 AsyncFunction 探针 |
| 4 | D8-D12（杆③/壳合并/purge/备份/dossier 收口） | abstention 两日重跑一致；壳合并前后 usage 对账 spawn 数下降；purge dry-run 报告；backups/ 出现首份 tar；池=30 |
| 5 | 活体验收：连续 3 个真实扫描日 | 每成功日恰 1 只相对 BUY（或显式 BLOCKED，禁 0-BUY-成功）；日成本回 ≤$35 带；relative_buy.md 分层栏目在场 |

回归纪律：每批独立 commit；批 3 删除按模块拆 commit 便于单独 revert；改动前先跑本稿探针基线（防「绿灯不等于有灯」）。

## 8. 回滚

| 项 | 回滚杆 |
|---|---|
| E6 转正 | `scan_config.jsonc` `relative_buy.mode` 一行翻回 `shadow`（brief/lint/publisher 全部自动回影子语义）；zero_buy/buy_ledger 冻结行按日期切，回滚后自然续写 |
| E1a 分级 | revert 该 commit；`_data_contract_ok` 回全量 failed 判 |
| 删除各项 | 独立 commit revert；**数据零损失**（registry.json/monitors.json/t1_candidates/watchlist.csv/weights 快照/孤儿 data.md 全在 archive/） |
| quota/floor/config | config 单行 revert（B1/B2/B3 各自独立记账） |
| purge | 不可逆（删的是可重算/过期 staging）；故 --apply 默认关、白名单永不含账本原料 |

## 9. 预期净效果

- **产品**：33 天不出手状态终结——每个成功扫描日恰 1 只可执行相对 BUY 或显式 BLOCKED；「成功的 0 买日」语义消失（R-E2 由 brief_lint 硬锁）。
- **成本**：$40.8/日 → 预估 **$33-35**（t1 LLM 腿 ~$0.7 + sector 半段 ~$0.9 + 壳合并 ~$3 + ensemble ~$2.3[若批 A7] + 杂项；主会话唤醒轮次随 spawn 下降）。
- **代码**：净删 ≈**3500-4000 行**（registry 家族 ~2400 + changelog/recalibrate ~400 + t1 LLM ~400 + 研判段/evidence/watchlist 残余 ~300）+ 12→9 或 10 个 agent role。
- **学习层形态**：从「22 账本 + 冻结注册表 + 5 永冻实验 + 自动重标定」收敛为「拒绝侧有信号的核心账本（gate/channel/earlystop/relative_buy/abstention v2/t1 确定性）+ proposals 人批 inbox + 确定性注入（cross_calib/t1 校准/dossier）」。
- **资产**：不可重算知识资产（判例库/档案/账本）每日自动备份。
- **不变**：L0-L2 漏斗、L3/L4/L5 主链、门与早停语义、dossier 注入、每股 L4 单价（$2.47 健康）。

## 10. 风险与开放问题

1. **E6 样本真相**：剔📌 n=4 均值 −0.46%——转正后前 20 个决策日是真正的检验期；记分册分层栏目让坏消息第一时间可见。若 20 日满时非📌均值仍 <0，reopen「E6 选择函数」议题（faces 权重/expected_abs_gap 接线），而不是回到 0 买常态。
2. **BLOCKED 频率**：E1a/E1b 修后，BLOCKED 应只剩真红旗与真数据故障。若转正后 BLOCKED >30%，按日拆 `excluded[]` 归因（工具已有）。
3. **哨兵日**：sentinel 日 E6 记 NO_RUN 不出票。若实测频次 >20% 则触发 §5.3 的哨兵改义 reopen。
4. **删除误伤**：四类同名异物（factor_lab 晋升表/feedback 晋升/style_spread 门/因子 promotion 列）已点名；双职测试清单已逐个标注——实施时照单核对，不即兴扩大。
5. **回滚杆①的 216 次累计结构失败**：E1b 并案调查可能揭出 task-book 的系统性竞态；若属实，修复优先级升至 P0 尾。

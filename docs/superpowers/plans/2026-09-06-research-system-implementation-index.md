# Research System Full Roadmap Implementation Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement the linked plans task-by-task. Steps use checkbox syntax for tracking. Use inline execution unless delegation is explicitly authorized.

**Goal:** 提供覆盖全部 A–F 工作包的开发入口、依赖、验收和交付边界，避免主设计覆盖全局而实施细节只覆盖首期。

**Architecture:** 现有模块化管线渐进演进；证据、研究事实、决策、执行评价分别拥有真值源。工程迁移与研究行为变更独立提交，不重建交易系统、不恢复学习闭环。

**Tech Stack:** 沿用 Python、pandas、NumPy、pytest、现有 contracts/workspace/capsule/taskbook 与宿主会话研究能力。

**文档状态：** 全阶段开发计划已补齐。A 的源码实施已有仓库记录；B–F 为待实施方案。本文不代表 B–F 已上线、实盘收益已验证或全量测试本轮已执行。**2026-09-06 二轮评审已按条目修订**（记录见 §11）：修订前的版本把首轮评审指出的重复原样带进了 B–F，且把生产零调用者的 B1 当成开发起点。

**主设计：** [研究可信度与系统演进详细开发设计](../specs/2026-09-06-research-reliability-and-system-evolution-design.md)。主设计 §6–§8 尚未按首轮评审改稿；与本索引冲突处以本索引 §9/§10 为准，主设计改稿另行提交。

## 0. Global Constraints（主设计 §2.2 不变量，逐条原文；每个任务的要求都隐含本节）

| 编号 | 必须保持的边界 |
|---|---|
| I01 | Codex 进程先设置 `AUTORESEARCH_ENGINE=codex`；研究产物仅落本引擎 `context_codex/`、`reports_codex/`。开发文档与源码按仓库路径管理。 |
| I02 | 不读写另一个引擎的 context、reports、档案、权重、账本或 transcript。数据湖 `lake/` 是唯一共享的研究数据根。 |
| I03 | 扫描与 lite 的主尺保持 `gap_c1_o2`：数据日 D，D+1 收盘买入、D+2 开盘卖出。full 深研另有自己的估值期限。 |
| I04 | 个股研究评级保持现有 rubric 三门；最终 BUY 仍由 `relative_buy` 所有。工程优化不改评级阈值、不扩大 BUY 数量。 |
| I05 | 零买日合法，不为产出率、成本分母或展示要求放宽门槛。 |
| I06 | 市场、行业输入给 L3/L4 的仍为描述性地形；`sector_healthy_top3` 的操作含义不进入个股判断。 |
| I07 | A 级数据异常阻断且拒绝入湖；B 级缺失显式降级。未知不冒充已验证或零风险。 |
| I08 | 不恢复已退役的自动学习、prompt 战绩回注、自动调权或实验晋升状态机。离线研究只供人判断。 |
| I09 | 不恢复 L4 决策卡跨日 TTL 复用、菜单 carryover、L3.5 或未经新证据支持的 L2 模型路线。 |
| I10 | 冻结 run 及历史报告不原地改写。重算、修复对照与新增事后信息写独立目录。 |
| I11 | 现有 scan 产物名称、评级词表、GATE1/2/4、任务终态和法证完整性义务保持兼容。新增契约按版本迁移。 |
| I12 | 不新增付费 LLM API 路线。跨引擎编排通过会话宿主提供推理结果，普通 Python 进程不假设自己能直接调用会话模型。 |

本索引补充两条：

- **I13（2026-08-29 裁定）**：新产物一律先登记 `contracts.ARTIFACTS` 再写代码，否则 drift 守卫红；改 contracts 后 `emit --write` 重生成。研究根是否纳入登记见 §9 Q-R。
- **引擎判定**：`common/workspace.py` 中显式 `AUTORESEARCH_ENGINE` **恒优先**于 `CLAUDECODE` 检测。I01 只对 Codex 进程成立；**Claude 会话禁止设该变量**，否则产物写进 codex 根、直接违反 I02（A 计划 §4 偏离表第 1 行已记录此教训）。本索引与五份计划的命令一律引擎中立，不含 `export AUTORESEARCH_ENGINE`。

## 1. 从这里阅读

| 工作包 | 详细实施计划 | 任务范围 | 当前状态 |
|---|---|---|---|
| A：统计口径 | [Statistical Estimator Convergence](2026-09-06-statistical-estimator-convergence.md) | 日等权估计单源、core/CLI、seed、输出隔离 | 已实施并合并 main（0712114）；保留原计划中的实施偏离与验证记录 |
| B：事件证据 | [Claim Evidence Verification](2026-09-06-claim-evidence-verification.md) | B1 保守判定；B2 契约；B3 比较；B4 接线；B5 验收 | **B2/B3/B4/B5 仪器全部已实施**（§13/§14）。B1 不做（零生产效果）；**B5 的 80 条人工标注仍未做**（外部依赖）；绑定真来源后同一管线不用改 |
| C：执行评价 | [Execution Evidence and Net Return](2026-09-06-execution-evaluation.md) | C1 时间；C2 快照；C3 成交；C4 成本/权益；C5 导入/读数 | **C1–C5 全部已实施**（§12/§13）；券商分支已合并；真实快照来源与真实成交样本仍是外部依赖 |
| D：结构化卡片 | [Structured Research Cards](2026-09-06-structured-research-cards.md) | D1 schema；D2 嵌套事实；D3 比较；D4 权威切换；D5 完整性 | **D1–D4 已实施**（§13）：候选 JSON 由解析桥派生，生产仍读 md；**D5 生产路由与 agent 原生写 JSON 的模板改动受 Q-冻结约束** |
| E：编排与分层 | [Orchestration Efficiency and Layering](2026-09-06-orchestration-and-layering.md) | E1 当前基线；E2 信封；E3 runner；E4 纯计算；E5 声明注入 | **E1/E2/E4/E5 步 3 已实施**；E3 按 Q-E 不立项；E5 步 4/5 残余边已列台账（12 条未清），未迁移 |
| F：研究方法 | [Research Methods and Stage Value](2026-09-06-research-methods-and-stage-value.md) | F1 实验；F2 full；F3 lite；F4 阶段价值；F5 稳健性；F6 因子；F7 概率；F8 读数 | **F1/F4/F5/F6/F7/F8 已实施，F6 三格已跑出真读数**（§14）；**F2/F3 模板改动受 Q-冻结约束**；真实前向样本仍是外部依赖 |

每份实施计划包含：准确文件路径、Consumes/Produces 接口块、核心代码、回归测试样例、执行命令、失败语义、上线条件、提交与回滚边界。代码段是实现基准与关键接入段；既有函数的机械搬迁以当前源码为准，不把文档示例当作已上线实现。

## 2. 开发次序与依赖

这些是工作包，不是必须串行的六个月历阶段：

| 开发波次 | 内容 | 进入条件 | 可独立推进的内容 |
|---|---|---|---|
| 已完成基础 | A | 仓库已记录实施与合并 | 不重做 core/CLI 收敛和 --out-json |
| 波次 1：不需裁决的 I 类件 | **已完成（§12）**：E4、E5 步 3、C1/C2、F1、F5、F6 统计层、F7、E1。剩 F6 首批家族登记（W3 三格的零 LLM 复算） | 现有代码 | 全部可并行；没有一件改变生产 BUY、评级或 prompt |
| 波次 2：裁决后 | Q-B → B1–B3（若接线）或 B2/B3 改挂 intel_guard；Q-C → C3–C5；Q-D → D1–D4；Q-R → 登记 research 根 | 对应裁决 | 没有实盘数据仍可交付 C 导入器与缺失读数 |
| 波次 3：解冻后的 B 类 | B4 接线、D5 生产路由、F2/F3 模板（与 08-31 D4 v5 合并成一次人批）、E2/E3（仅当 Q-E 立项且 E1 能力报告四项为真） | Q-冻结解除（08-26 A0：09-中攒 20 结果日） | 每件独立回滚杆 |
| 波次 4：研究评价 | F4（扩 populations 读模型）、F8 读数 | A；净执行评价需 C；事实指标需 B | F4 价格代理版无需等实盘成交 |
| 持续验证窗口 | 前向采样、F8 研究结论 | 冻结当时记录与随后真实结果 | 等待结果成熟，不自动修改生产策略 |

可并行是开发排期属性，不表示已授权 agent 自动派发；当前文档任务没有启动子代理或生产研究。

建议下一批实际开发从 **E4** 开始（纯搬迁、golden 对拍、零裁决、可单独回滚），并行 C1/C2 与 F5/F6/F7。**不要从 B1 开始**：它单独提交没有任何生产效果，只有 B4 接线后才生效，而接线待裁且在冻结期内。不要一次将 B–F 所有源码变更打成大补丁。

## 3. 工作量与角色分工

| 工作包 | 工程投入参考 | 非工程依赖 |
|---|---|---|
| B1 | 1–2 工程日（单独提交零生产效果，须与 B4 或 intel_guard 接入同批交付） | 既有 keyword 测试的语义修正 |
| B2–B5 | 3–6 工程日 | 真实来源覆盖、4 类各 20 条 = 80 条人工复核案例 |
| C | 4–7 工程日 | 快照时点证明、broker 分支合并、脱敏订单/成交、费用和公司行动口径 |
| D | 4–7 工程日 | Q-D 裁定、B 引用语义、完整消费者迁移与故障验收 |
| E | 3–6 工程日基础；runner 按宿主能力单独估算 | 当前同引擎计量、可用宿主交接原语 |
| F | 仪器约 3–5 工程日；模板评审及扩展另计 | 足够真实前向样本；统计/研究评审 |

这是熟悉仓库的一名工程师的粗略估算，不是日历承诺；来源授权、人工标注、真实市场采样与 runner 适配不包含在简单总和内。

建议职责：

- 开发者：契约、实现、故障处理、测试与产物可追溯。
- 股票研究者：事件标注、经营机制、证伪条件、样本可比性、结果解释。
- 数据负责人：时点、复权、交易日历、费用和来源可用性。
- 用户/策略负责人：是否改变 BUY、现金比较门、交易窗口与自动化权限。

一个人可兼任前三者，但记录中要分清"实现者自测"与"研究样本独立复核"。

## 4. 覆盖矩阵：主设计到可执行任务

| 主设计要求 | 对应任务 | 完成证据 |
|---|---|---|
| §5.1/5.3 日等权口径与历史不改写 | A 原计划及实施记录 | 同估计对象/seed parity、独立输出 |
| §5.2 时间相关、分年度、样本分割、多重比较 | F5/F6 | block 敏感性（`moving_block_diff` 下沉后的单序列版本）、重叠清除、家族登记（首批 W3 三格） |
| §6.1 不凭关键词作完整支持 | B1 | 指定探针 UNKNOWN |
| §6.2/6.3 事件/主体/金额/状态/时间 | B2/B3 | schema 与字段 verdict、原文 hash/定位 |
| §6.4 真正接通来源与 ledger | B4 | review_draft→绑定→lint→evidence_index 集成，或按 Q-B ③ 接入 intel_guard |
| §6.5 真实功能验收集 | B5 | 80 条逐例记录及错误 PASS/FAIL/未知/缺原文 |
| §7.1–7.3 时点、模式与快照 | C1/C2 | 14:45 不读取 15:00 信息；`decision_at` 从 exec_anchor 派生；LATE run 不进分母；来源可得性 |
| §7.4 费用/部分成交/未退出/公司行动 | C3/C4 | 现金、已实现、未实现、权益分别对账；印花税只收卖腿；封板不成交 |
| §7.5/7.6 导入、独立输出、三模式读数 | C5 | EOD/模拟/实盘分母独立、旧 hash 不变 |
| §8.1 ResearchCard 字段 | D1/D2 | 同源词表、证据/论点/情景契约（字段集按 Q-D 并集） |
| §8.2/8.3 比较、权威切换、兼容 | D3–D5 | 新 run fail-closed，旧 run 可读，消费者 parity |
| §9.1/9.4 当前计量和质量约束 | E1 | 同引擎成熟 cohort，缺计量不冒充零成本，字段以真实账本键为准 |
| §9.2 runner/宿主/taskbook | E2/E3 | 信封绑定、重复/迟到/中断恢复测试（E3 为条件项） |
| §9.3 分层与运行时解耦 | E4/E5 | 真实调用链、兼容转发、棘轮收紧 |
| §10.1 三类研究问题 | F4/F7/F8 + B/C | 事实、选择、执行分开评价 |
| §10.2/10.3 full/lite 方法 | F2/F3 | 经营因果与可证伪机制、隔夜时间传导（解冻后一次人批） |
| §10.4 L3/L4 增量与复核相关 | F4/F7 | 同人口（populations 正交布尔）、缺反事实声明、错误重合 |
| §10.5 因子、概率、训练后见性 | F1/F6/F7 | 冻结/回放属性、定义事件、留出评估、敏感尺并报 |
| §12 测试/发布/回滚 | 各包末节 | 独立提交，局部与集成验证，不能覆盖冻结现场 |

## 5. 跨工作包接口

| 生产者→消费者 | 交换内容 | 不允许交换的东西 |
|---|---|---|
| B→D/F | claim_id、观测/blob、支持结论、规则版本 | "证据支持"自动变成股票 BUY |
| C→F | evidence_mode、fill_rule_version、成本版本、完整性、实现/未实现/窗口状态 | 缺成交的 0 收益、模拟伪装实盘 |
| D→决策/报告 | 研究初判、三门、P4、早停、证据 | 覆盖 relative_buy 的第二终评级 |
| E→宿主 | 当前 run/task/attempt/input hash 的推理请求 | 不存在的会话模型 API 或隐式付费调用 |
| A→F | 日等权原语与统计版本 | 偷换种子/估计对象后宣称严格可比 |
| F→用户 | 离线证据、限制、新策略提案 | 自动改权重、prompt、三门或持仓动作 |
| broker→C | `context_<engine>/broker/trades.csv`（schema/store/reconcile 归 broker 包；只记不学） | C 自造第二套成交格式；券商账号/持有人进研究目录 |
| exec_anchor→B/C | `read_execution(run_dir)` 的 `first_available_session`/`actionability_status`/`exec_decision_cutoff` 派生 `decision_at` | 调用方自由填 `decision_at`；LATE run 进分母 |
| populations→F | `_ledger/populations/<run>.parquet` 正交布尔（in_l2/is_finalist/l4_rejected/is_buy…）+ `views/stage_rulers.csv` | F 另建一张 candidate_audit 审计表 |
| ruler→C/F | `MAIN_RULER/ENTRY_FLAG/EXIT_FLAG/entry_tradable()/REL_MARKET/REL_SECTOR` | 各包自定义可交易性或超额口径 |

运行身份继续沿用现有 run_id/taskbook；实验身份是独立 experiment_id，不能与 scan run 生命周期混用。接口缺失可交付显式 UNKNOWN/IMMATURE，但不能把"全部未知"称为能力已经有效。

## 6. 每批实施的固定检查清单

- [ ] 会话开头读取最新 CLAUDE.md 与相关任务源码；**不设 `AUTORESEARCH_ENGINE`**（Codex 进程按 AGENTS.md 已设；Claude 会话由 CLAUDECODE 判定），命令一律引擎中立。
- [ ] 检查 git status 与最新提交；保留 pinned.jsonc 等用户未提交变更。
- [ ] 先写指定失效模式的 RED 测试，再改代码；对既有逻辑的搬迁先保存 golden。
- [ ] 新产物先进 `contracts.ARTIFACTS`（I13），改 contracts 后 `emit --write`。
- [ ] contracts 不导入上层；不新增 allowlist 豁免掩盖依赖。
- [ ] 新模块必须 grep 出真实调用链：生产者没接线 = 没做完，跑通一次 CLI 不算接线。
- [ ] 执行命令统一 uv run --no-sync；不扫描/读取其他引擎目录或 transcript。
- [ ] 只处理当前任务文件，不混做其他阶段；跨模块变更运行全量测试。
- [ ] 真实扫描才按项目技能跑 GATE1/2/4；文档与离线单测验收不触发全市场取数。
- [ ] 记录代码/契约/统计/成本/prompt 版本、输入身份与尚未完成的验证。
- [ ] git diff --check 后按文件显式 stage；不使用 git add .，不提交用户持仓清单。
- [ ] 发布前核对本包回滚路径；冻结研究产物不覆盖、不回写。

## 7. 交付级别与外部依赖

| 级别 | 可以声称什么 | 还不能声称什么 |
|---|---|---|
| 文档完成 | 任务、接口、测试和验收覆盖 A–F | B–F 源码已实施 |
| 工程完成 | 指定代码和功能测试通过、接线可证 | 真实来源充足、真实收益可靠 |
| 数据验收完成 | 来源/时点/成交/人工标签能支撑评价 | 统计样本已成熟或优势成立 |
| 研究完成 | 对预登记问题给出有范围/不确定性的结论 | 保证盈利、自动扩大策略权限 |

缺盘中来源：先做导入器与缺失状态；缺真实成交：只报告代理/模拟；缺人工标注：B1 可发布，B2 有效覆盖验收未完成；缺宿主能力：保留现有路由；缺成熟样本：F 仪器可完成，研究结论保持 IMMATURE。

以上是分级交付，不把外部依赖伪装为已完成，也不因等待数据而回退到无证据的确信。

## 8. 不包含的策略变化

本轮文档没有批准自动下单、接券商、扩大付费 API、改变 gap_c1_o2、放宽三门、为凑单增加 BUY、现金择时门、盘后执行、跨引擎闭环共享、恢复自动学习。也不改 `EXEC_DECISION_CUTOFF`（14:45 运营截止）与 `EXCHANGE_CUTOFF`（14:57），不改 `ENTRY_FLAG`（buyable_c1）/`EXIT_FLAG`（unsellable_o2）的语义。

若后续证据支持这些方向，应单独提交策略设计与风险边界，由用户选择；不能夹在工程重构或研究读数脚本中悄悄上线。

## 9. 待裁问题（各包进入条件）

**2026-09-07 裁定：用户按本节「建议」列全部采纳。** 逐条后果：

| 编号 | 裁定 | 后果 |
|---|---|---|
| Q-B | ③ 把 v2 语义装进 `intel_guard` | B2/B3 契约与比较器照做；B4 改为情报稿的事件抽取 + 绑定接口，**影子模式**写侧车产物（新增、不改既有 verdict、不进门）；`review_draft` 路径不再扩展；B5 的人工标注改在情报稿上做 |
| Q-C | 本地合并 | `feature/broker-ingest` 已合并 main（`a034acb`）；C3/C5 消费 `broker/trades.csv` |
| Q-D | ① D8 单源 + emit；② 对拍期 D8 策略，20 卡 parity 后切 fail-closed；③ 字段并集 | 词表进 `contracts/agent_output.py`，校验函数在 `contracts/research_card.py`；候选 JSON 先由确定性解析桥从现有 md 派生（不改模板），agent 原生写 JSON 的模板改动与 D4 v5/F3 合并一次人批（解冻后） |
| Q-E | 不立项 | E3 不做；E2 信封可做 |
| Q-F | 并报 | F1 已实现 `sensitivity_rulers`；F4/F8 读数敏感尺与主尺并列 |
| Q-R | ① 登记 | 新增 `research_report` / `research_ctx` / `factor_lab` / `broker` 四个根，既有研究产物与 C5/F8/F6 目录全部登记；漂移守卫扩到 `autoresearch/research` 与 `autoresearch/broker` |
| Q-冻结 | 照旧 | B4 影子侧车、D1–D4 候选双产物为 I 类可做；D5 生产路由、F2/F3 模板、agent 原生写 JSON 等解冻后一次人批 |


| 编号 | 问题 | 选项 | 建议 | 阻塞的任务 |
|---|---|---|---|---|
| Q-B | `news/claim_ledger` 生产零调用者：接线、退役，还是把 v2 字段语义装进真在跑的 `intel_guard.lint_claims`？ | ① 按 08-28 外源稿接线 `review_draft`（B 类）② 退役整包，只保 evidence_index 的引用 ③ B2/B3 契约与比较器不变，接入点改为 intel_guard 的事件行 | ③：真守卫在哪就接哪，不为死码写 80 条验收 | B 全部 |
| Q-C | `feature/broker-ingest`（11 commits，90 绿，复核修补完）三选一 | 本地合并 / PR / 留分支 | 本地合并，C3/C5 消费 trades.csv | C3–C5 |
| Q-D | D 与 08-31 稿 D8（已裁「做」）谁是权威 | ① schema 单源 `agent_output.py` 经 emit（含 Codex `--output-schema`）vs 新建 `research_card.py`；② JSON 优先 + md 对账回退 vs 新 run fail-closed；③ 字段集 | ① 走 D8 单源 + emit；② 对拍期 D8，20 卡 parity 后切 fail-closed；③ 并集 | D 全部 |
| Q-E | runner 是否立项 | 09-04 裁定「扫描留 workflow.js 做减法」；E3 只在 E1 能力报告四项为真时可选 | 不立项；E2 信封可先做 | E2/E3 |
| Q-F | 敏感尺是否进 F | 主尺唯一 vs 主尺 + `fwd_5_oc`/`fwd_10_oc`/`REL_MARKET`/`REL_SECTOR` 并报（永不进 BUY） | 并报（08-21 低位转强周级尺翻正那一课） | F1/F4/F6 |
| Q-R | research 产物根是否登记 ARTIFACTS | 现 ROOTS 无 research，而 overnight_census/C5/F8 都写 `reports_<engine>/research/`；① 加 `research` 根登记 ② 明文豁免 | ① | C5/F8 |
| Q-冻结 | 08-26 A0 冻结（B 类改动到 09-中攒 20 结果日）对 B4/D5/F2/F3 是否照旧 | 照旧 / 提前解冻 | 照旧 | B4、D5、F2、F3 |

## 10. 与既有未合并 / 未实施设计的关系

首轮评审记录的根因：写稿时没读 memory、未合并分支与 08-28/08-31 设计稿，于是 B/C/D/F 各撞一处已有实现。本节是防第三次重复的唯一位置。

| 既有设计 / 分支 | 状态 | 与本索引的关系 |
|---|---|---|
| `feature/broker-ingest`（08-27 稿 a71894f） | 11 commits 未合并，等三选一 | C3/C5 的成交来源；C 不另造 fills 格式 |
| 08-31 stock-research 稿 D8（卡片双写 card.json） | Q1 已裁「做」，排 P2 未实施 | = 本索引 D；三处分歧见 Q-D |
| 08-31 稿 D4（卡 v5：基率行 / 情绪地形 / 题材位 / 接力盘一问 / 检索密度置信门 / 日期腿） | P2，人批 | 与 F3 五问合并成一次人批 |
| 08-31 稿 W3 三格（首板缩量回调低吸 / 晚封板次日溢价 / 机构席位 5–20 日） | 正交立项，零 LLM | F6 首批因子家族 |
| 08-31 稿 D2/D3/D9（盘后增量包 / exec_check / 双引擎叶子） | P2/P3 | 与本索引正交；D3 exec_check 与 C2 快照条件共用执行线阈值常量 |
| 08-28 外源稿 B-1..B-5（claim_ledger 接线 = B 类 Q1） | 未裁 | = Q-B |
| 08-28 ruler 稿 G1/G2/G3 | 已上线（`exec_anchor` / `ledger_views` / `populations`） | B4/C1 的 decision_at 来源；F4 的人口来源 |
| 08-28 ruler 稿 G0/G4/G5/G6 | 未做 | G4 券商 = Q-C；G6 capsule 真跑验收与 E 无依赖 |
| 09-04 token 效率稿 Wave 1 | 已合并 cfd1371 | E1 只读它的账本；其 Q1/Q3/Q5 未裁 → Q-E |
| 08-29 契约层 P0 / I13 | 已合并 | 本索引 §0 补充约束 |

## 11. 本次文档交付验证记录

2026-09-06 初版，对本日期的 7 份计划/索引与主设计共 8 份文档进行检查：

- 40 个本地 Markdown 链接目标存在。
- 66 个 Python 代码块通过语法解析；22 个 shell 代码块通过 bash -n，仅检查语法，未执行其中的开发/提交命令。
- B–F 的 37 个可独立运行的代码样例，加 1 个非有限模拟价格边界检查，在独立 Python 进程内存中通过。检查时将同一计划的代码段组合，并绑定已有常量/函数，不创建拟新增生产模块。
- 1 个"搬迁后旧函数与新函数为同一对象"的测试需要实际源码迁移，本次未执行；B–F 的完整接线、生产回归、人工样本与真实执行评价也未执行。
- 占位标记扫描无遗留。此次只新增/更新开发文档，未修改业务源码、用户持仓清单或研究产物。

2026-09-06 二轮评审修订（Claude 对照代码库逐条核实后落入）。修订后复跑同一自检：40 个本地链接目标存在；69 个 Python 代码块通过语法解析（新增 C1 Step 5、D1 `card_lint_warnings`、F1 漂移锁三块）；22 个 shell 块；`AUTORESEARCH_ENGINE=codex` 全部计划归零。另在独立进程内真跑了改动过的代码段：C4 买腿印花税为 0 / 卖腿计税、C2 涨停报价返回 LIMIT_UP_QUEUE、F5 `block_index` 与 `moving_block_diff` 原抽块在 4 种形状 × 200 次抽样逐位相等、F1 尺子字面量与 `common/ruler`、`scan/populations` 实模块同值、E1 缺键行标 MISSING。修订条目：

- 删除索引 §6 与五份计划共 8 处 `export AUTORESEARCH_ENGINE=codex`，改为引擎中立（§0 引擎判定）。
- 起点从 B1 改为 E4；B 计划写明 `claim_ledger` 生产零调用者与 intel_guard 替代接入；谓语首批补「增持」，验收集 60→80 条；一个特性收敛为两个规则版本串。
- C 计划写明消费 broker 分支 trades.csv；`decision_at` 从 `exec_anchor.read_execution` 派生；三模式共用 `ENTRY_FLAG/EXIT_FLAG`；印花税只收卖腿；收盘集合竞价与盘后固定价格成交规则版本化。
- D 计划写明与 08-31 D8 的三处分歧与建议；持仓票研究规则从 schema 拒绝改为 lint warn；补 D8 点名的 owner 函数与直接读卡者。
- E 计划 E1 改按真实账本键映射；E3 标条件项；E4 补实测调用者清单。
- F 计划 F1 加敏感尺；F4 读 populations 产物不另建审计表；F5 下沉 `moving_block_diff`；F6 落实首批家族；F2/F3 标冻结与合并人批；F8 路径引擎中立。
- 新增 §0 Global Constraints、§9 待裁问题、§10 关系表；§5 加四行接口。

以上记录证明文档链接、示例语法与所列核心样例可用，不替代各包实施时的 RED→GREEN、集成测试、数据验收和研究有效性验证。A 的历史全量测试记录保留在 A 计划中，不冒充本轮重新运行的结果。

## 12. 波次 1 实施记录（2026-09-06）

分支 `feature/research-system-wave1`，9 个提交，全量 **5285 passed / 6 skipped**（会话起点 4985，+300 条测试）。所有改动都是离线件：没有一件改变生产 BUY、评级、prompt、三门或主尺。

| 提交 | 内容 | 关键决定 |
|---|---|---|
| E4 | `common/forward_returns.py` + `data/market_panel.py`；`factor_lab`/`edge_census` 同对象转发；三个 scan 消费者改线 | golden 在搬迁**前**录制（8 用例）；`populations` 仍留一条 research 边（只为 `MIN_CROSS_SECTION`），故 KNOWN_UPWARD 条目不 stale |
| E5 步 3 | `reconcile_with_resolved` 纯入口 + `reconcile(..., resolved_agent_config=)`；`_resolved_via_legacy_bridge` 单一显式旧桥 | 旧桥不删——CP7 命令还在用；`trace → scan` 行数不变但收窄到一个入口 |
| C1/C2 | `contracts/execution.py` + `common/execution_math.py` | `decision_at` 从 `exec_anchor` 派生（非 ACTIONABLE 的 run 不进分母）；封涨停单列 `LIMIT_UP_QUEUE`；计划的 `decision_at_for_run` 会造 `common → scan` 向上边，改为纯函数收执行块 |
| F5 | `moving_block_diff`/`BootResult`/`_diff` 下沉 `common/stats`，新增 `block_index`/`block_mean_ci`；`robustness.py` 的块长敏感性 + purge + 交易日 embargo | golden 9 用例逐字段相等；embargo 数交易日不数自然日 |
| F6 | `common/stats.family_adjustment`（BH/BY，依赖假设无缺省） | `rejected` 按校正后 q 判 |
| F1 | `contracts/research_experiment.py` + `research/experiment_io.py` | `open("xb")` 排他冻结，无 `--force`；`sensitivity_rulers` 白名单可空 |
| F7 | `research/probability_eval.py` | 缺成交/缺费用是**未标注**不是 `y=0`；Brier 与基率、可靠性分组同报 |
| E1 | `research/efficiency_baseline.py` | 计划写的 4 个字段生产查无，改按真实观测键读 + `coverage` 点名；能力没证据一律 `NO_EVIDENCE` |

**每件都跑了变异探针**（共 60 个）。四个当场证明是「没有灯的绿灯」，补了用例才有鉴别力：

1. F5「有效抽样过半」守卫——9 个 golden 里稀疏族 n_valid 是 996/1000，走不到那道门。
2. E1「按 coverage 排除」——原用例缺的字段同时让单票成本变 None，那一腿零鉴别力。
3. C1「发布晚于截止」——两个时点一起挪到未来时，`market` 那腿就兜住了。
4. E4 卖腿旗的 `<=` → `<`——epsilon 让两者语义等价，是**真无差异变异**，不是盲区（同类：F5 的 `% n`，起点上界已保证不绕回）。

产物形状守卫在本波逮到自己该逮的：新建的 `common/forward_returns.py` 与 golden JSON 裸写 `fwd_2_oc` 却无沿革注记 → fail，按判据补注记后过。

**未做，别当已完成读**：B 全部（待 Q-B）、C3–C5（待 Q-C）、D 全部（待 Q-D）、E2/E3（Q-E 条件项）、E5 步 4/5 残余边台账、F2/F3 模板（冻结中）、F4 阶段价值、F6 首批家族登记、F8 读数与 CLI。research 根仍未登记 ARTIFACTS（Q-R）。主设计 §6–§8 仍未按首轮评审改稿。

## 13. 第二轮实施记录（2026-09-07，七项裁决之后）

用户按 §9 建议列裁定全部七项后直接在 `main` 上开发（未另开分支），13 个提交，全量 **5641 passed / 6 skipped**（波次 1 后 5285，券商分支带来 90 条，本轮新增 266 条）。

| 提交 | 内容 | 关键决定 |
|---|---|---|
| 合并 broker | `feature/broker-ingest` 本地合并（Q-C） | 11 commits 零冲突 |
| Q-R | 四个新根 + 既有研究产物登记 + 守卫扩到 research/broker | 扩之前两包 22 个未登记字面量，现全部有主 |
| B2/B3 | `contracts/claim_evidence.py` + `news/claim_support.py` | 谓语首批含增持；时间不一致 UNKNOWN 不许 ±3 天放宽 |
| B4 | `news/claim_extract.py`（regex_v1）+ `news/claim_binding.py` + 情报守卫侧车 `_l4_claims_*.json` | **影子**：不改稿件/verdict/action，没有门读它；无绑定来源时每条 SOURCE_NOT_BOUND |
| C3–C5 | 成交状态/损益/成本规则 + 导入器 + 离线 CLI | 三模式永不合并；印花税只收卖腿；`decision_at` 从时间锚派生；输出排他 |
| D1–D4 | 词表进 agent_output，`research_card` 校验，`card_io` 解析桥，`card_render`，`RunProfile.card_source` | 候选 JSON 由 md 派生不改模板；JSON 权威下缺文件明确失败；持仓规则只 warn |
| E2 | `contracts/inference_task.py` + `scan/deterministic_runner.verify_handoff` | 只校验不派发；E3 不立项 |
| F4/F8 | `research/stage_value.py` + CLI | 读 populations 产物；UNKNOWN 旗不当 False；主尺与敏感尺并列；小样本 IMMATURE |
| F6 | W3 三格家族登记（机器可校验冻结方案）| 已登记未计算 |
| 文档 | E5 残余边台账；主设计 §6–§8 改稿注记 | 15 条向上边逐文件列状态 |

变异探针 32 个，两个逼出用例：B4「他票行也进侧车」（夹具那行他票没有谓语词，是被谓语过滤掉的）；E1 之外本轮无新的「没有灯的绿灯」。一次流程事故：`pytest … | tail` 吞退出码，F4/F8 在 `-W error::FutureWarning` 下 2 条红之上提交，正常跑本就 15 绿，已补修正提交并改用 `set -o pipefail`。

**仍未做，别当已完成读**：B5 人工标注集（80 条）；C 的真实快照来源与真实成交样本（外部依赖）；D5 生产路由切换与 agent 原生写 JSON 的模板改动（冻结）；E3 runner（Q-E 不立项）；E5 步 4/5 的 12 条残余边迁移；F2/F3 模板（冻结）；F6 三格的真面板普查（研究动作）。

## 14. 第三轮实施记录（2026-09-07 续）

5 个提交，全量 **5694 passed / 6 skipped**（第二轮后 5641，本轮 +53）。

| 提交 | 内容 | 结果 |
|---|---|---|
| E5 | 策略师投影名单下沉 `contracts/strategist_view.py` | **`derivatives → scan` 这条向上边消失**，KNOWN_UPWARD 收紧一格（守卫主动逼出的红） |
| B5 | `news/claim_acceptance.py` 验收仪器 | 四项分母各自独立；缺标注 IMMATURE；80 条标注仍是外部依赖 |
| F6 仪器 | `research/w3_grids.py` 三格普查 | 独立预注册，复用同一批原语；**不动 08-28 那份冻结的格表**（它自带「防读完结果再加格」守卫） |
| F6 读数 | 1095 个交易日真跑 | **三格全无正证据**：晚封板可买桶 −1.456pp 显著负、机构席位 fwd_5 −1.983pp 显著负、首板缩量回调按注册阈值只有 12 个事件 |
| 文档 | [三格读数](../../research/2026-09-07-w3-three-grids-readout.md) | 08-31 W3 §8 的三个候选格：两格推翻、一格无法验证 |

变异探针 16 个，三个逼出用例/夹具：G2 不分桶（原夹具没有一只票在 D+1 仍封板，不可买桶恒空）、缺表返回假数据（只验了三个信号源里的一个）、G1 缺 D+1 面板行会静默落空。

**仍未做**：B5 的 80 条人工标注；C 的真实快照与成交样本；D5 生产路由与模板改动（冻结）；E3（Q-E 不立项）；E5 剩余 11 条残余边；F2/F3 模板（冻结）。


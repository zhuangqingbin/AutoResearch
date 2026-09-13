# 全市场 Agent、双引擎验收与切换 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax. 默认顺序开发；真实双引擎验收各自在本引擎执行，本计划不授权读写另一引擎产物。

**Goal:** 将完整扫描链迁入统一任务协议，保留全部研究与发布约束，完成各入口切换与旧编排收敛。

**Architecture:** 新 scan 工作流只持有编排依赖；候选、任务、评级、门和报告继续由现有 owner 决定。动态展开受冻结模板和领域输出约束，模型不能自行扩大任务范围。

**Tech Stack:** 计划一执行桥、计划二研究子流程、scan 原模块、l4_tasks、exec_capture、capsule、pytest。

前置：[基础计划](2026-09-13-session-agent-migration-01-foundation.md)、[研究计划](2026-09-13-session-agent-migration-02-research.md)。协议真值：[架构设计](../specs/2026-09-13-session-agent-migration-design.md)。

---

## C01. 扫描前奏、模式和 L3

**Files**

- Create：autoresearch/session_agent/workflows/scan.py、legacy_scan.py。
- Modify：session_agent/operations.py、roles.py、validation.py、plan.py。
- Reuse：scan/frame.py、strategist_pack.py、prelude.py、universe.py、run_mode.py、gates.py、agents/l3_select.py、l3/triage.py、l3/merge.py。
- Read：.claude/workflows/scan-market.js、.claude/skills/scan-market/SKILL.md、STAGES.md。
- Test：tests/session_agent/test_scan_prelude.py、test_scan_modes.py、test_scan_l3.py、test_scan_expansion.py。

### C01a. 固定前奏

- [ ] begin(scan-market) 在任何取数前创建原 capsule、冻结 scan_config 和 pinned 输入；禁止从当前可变 pinned 文件决定已开始 run 的候选。
- [ ] frame → strategist_pack 保持原同步投影；市场研究角色只得到 strategist_pack 允许字段。
- [ ] prelude 调原 run_prelude，步骤从 STEP_NAMES 派生；不复制一份前奏步骤数组。
- [ ] 宿主支持时 prelude 与 macro-lite 保持原可并行关系；不能通过多次重复启动命令模拟并行。Codex 按当前项目适配规则运行。
- [ ] GATE1 用原 gates gate1 验证；gate 的 JSON 从文件/进程真实结果读取，不再让模型转述。
- [ ] requested_mode=AUTO 只存在于 session plan；原 run_mode.decide 产出四种实际业务模式并冻结 run_mode.json。

### C01b. 分支与展开

| 实际模式 | L3 | L4 | 完成要求 |
|---|---|---|---|
| FULL | 原完整精排 | 原 finalists | 原全链门 |
| FORCED_FULL | 原完整精排 | 原 finalists，保留 pinned | 原全链门 |
| SENTINEL_EMPTY | 按原规则跳过 | 无 | 仍有 assemble、观察、GATE4 和 finalize |
| SENTINEL_PINNED | 原持仓哨兵分支 | 冻结 pinned 票 | 每票研究及适用复核，不能当空哨兵 |

- [ ] run_mode 产生后，注册的 expander 根据已验证 mode/hash 选择模板；不适用模板也记录理由，避免 finish 把未展开误认为完成。
- [ ] sector.reuse/pack 与 L3 evidence 沿原依赖执行，行业角色复用 B04 的 LITE 定义。
- [ ] L3 保持全候选比较与两遍筛选，不把 ~200 只票拆成互不知情的逐股投票。
- [ ] 调原 l3 lint、原有一次有界修复分支和 merge 守卫；不能把字段错误概括为无限“自我修复”。
- [ ] finalists 与 GATE2 均由确定性代码生成/验证。候选顺序、pinned、composite 证据席和 bench/影子名单与旧流程一致。
- [ ] finalists 成功后才展开逐股任务；expansion 记录其 artifact hash、冻结配置、角色模板和原 taskbook 元数据。

operation ID 至少登记 scan.frame、scan.prelude、scan.gate1、scan.run_mode、scan.sector_prepare、scan.l3_prepare、scan.l3_lint、scan.l3_merge、scan.gate2。每项是静态注册的具体 argv/函数，不能从模型提供的 module 字符串反射导入。

必需测试：四模式完整矩阵；pinned 在开跑后改变不影响本 run；GATE1 的 l4_budget 缺失/NaN 立即失败；L3 malformed JSON 阻断；展开后修改 finalists 被拒绝；sector_healthy_top3 无法通过完整 market_pack 进入 L3/L4 地形；零 finalists 按原模式判据处理。

~~~bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q tests/session_agent/test_scan_prelude.py tests/session_agent/test_scan_modes.py tests/session_agent/test_scan_l3.py tests/session_agent/test_scan_expansion.py tests/scan/test_gates.py tests/scan/test_config_knobs.py
~~~

**完成判据：** 原数据与 L3 固定产物在同引擎夹具上可对拍；整个前段无需模型充当 shell/JSON 搬运者。

## C02. L4 taskbook、情报、决策卡与复核

**Files**

- Modify：session_agent/legacy_scan.py、workflows/scan.py、roles.py、validation.py、operations.py、artifacts.py。
- Reuse：scan/l4_tasks.py、stock_stage.py、agents/l4_card.py、l4/producers.py、l4/intel_status.py、l4/intel_guard.py、l4/rubric.py、l4/card_io.py、decision_finalize.py。
- Read：.claude/workflows/l4-stock.js、l4-card.md、l4-intel.md。
- Test：tests/session_agent/test_scan_l4_owner.py、test_scan_l4_intel.py、test_scan_l4_review.py、test_scan_l4_recovery.py。

### C02a. 单一整票 owner

L4_TASKBOOK owner 对应整只股票，不是某一次 intel/card/ens 子动作。整票只在原 l4_tasks 中认领一次、终结一次。内部模型交接可以用 SESSION owner，但必须携带 parent_task={owner:L4_TASKBOOK, subject:code, attempt:整票尝试}，并在接收时重新验证父任务。

子任务 ID 含整票 attempt，例如 l4.600519.a1.card；这样第二次整票尝试不会覆盖第一次的研究现场。子任务完成不等于整票完成。只有原业务要求的全部子动作完成后，legacy_scan 才调用 mark_success。

- [ ] prepare prompts/metadata 后才调用 l4_tasks.initialize；代码必须保留 ticker、name、sector、pinned、dossierSummary 与 cfg 的真实透传。
- [ ] legacy_scan.claim_ticket 调原 preflight(expected_attempt)，不直接写 taskbook JSON。run plan 只保存外部 owner 引用。
- [ ] 在每个子动作接收时验证父票当前 RUNNING attempt；旧 a1 卡不能满足 a2 的任务。
- [ ] 调用旧 verify_handoff 时将已登记整票身份映射成 task_id=code；内部交接使用自己的 TaskSpec validator，不能随意改用户提交的信封过门。
- [ ] 不在新 store 锁内再持有 L4 锁；交接 receipt 与 l4_tasks 状态用幂等恢复协调，不能出现第二份可写的整票终态。

### C02b. 研究链

| 子步骤 | 必須保留的行为 |
|---|---|
| preflight | hash 验证、attempt、已完成跳过、等待/阻断分支 |
| slim | 原单票 prepare_slim 与数据契约、操作级 tushare 帽 |
| intel | 冻结 cfg 开关、原查询额度、盲搜输入与原瞬时重试策略 |
| card | P0–P5、早停、深核读取边界、权威行情冲突处理 |
| OW review | ≥OW 触发、真实独立上下文、原同档早止与中位折回 |
| pinned SELL review | 原 pinned 与卖出触发、原只向温和折回 |
| stage result | 原 source/provisional/final 语义，不把临时评级当最终决策 |
| completion | 原 mark_success 锁内重新检查卡/ensemble/输入证据 |

- [ ] intel 输入只含原允许身份、日期、行业与已知事实去重材料，不泄漏 L3 评级或研究结论。
- [ ] INTEL_RESEARCH 与 TASK_ATTEMPT 保持各自分类与次数；DNS 情报重搜不能扩大整票重试预算。
- [ ] 同一 run 的合法 intel 续传继续调原 resumable/mark_resumed；不恢复已删除的跨日 card TTL 跳研究。
- [ ] 查询预算仍使用原 status/guard，超限/失败显示实际 DEGRADED 原因；模型不能把未查变成“无风险”。
- [ ] 将 card 结果提交给原 validator；旧 MD 权威和候选 JSON 通路按 frozen card_source 处理，D5 不解冻。
- [ ] ensemble 第 2 次与原卡同档时按旧数学规则早止；分歧时再做第 3 次。失败、未获得独立上下文或回执不足不能假装同档。
- [ ] 写原 _ensemble_<code>.json 的 ratings/median/trigger/degraded/n_dispatch 等实际字段；不得由无依据摘要反推派发次数。
- [ ] 使用原 stock_stage 与 taskbook 最终确认；中间卡片存在不构成 CP5 的成功信号。

### 复核回归矩阵

| 原卡 | pinned | 复核结果 | 应有行为 |
|---|---|---|---|
| Hold | false | 无 | 不新增 OW 复核 |
| Overweight | false | 第2次同档 | 原同档早止，不多派一次 |
| Buy | false | 分歧 | 继续第三次，按原中位只向下折 |
| Sell | true | 更温和中位 | 按原持仓卖出复核折回 |
| Sell | false | 无 | 不擅自套用 pinned 复核 |
| ≥OW | 任意 | 独立上下文不可得 | 等待同引擎独立任务完成，不宣称复核通过 |
| 任意需复核 | 任意 | 已派发但失败 | 原 degraded 与报告人裁披露，不隐藏计量 |

故障测试另含：两个 submit 竞争、staging 目录被重命名、卡被符号链接替换、终态前更换卡内容、旧 attempt 回执、执行进程仍活着、taskbook 全终止但含 BLOCKED。

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_scan_l4_owner.py tests/session_agent/test_scan_l4_intel.py tests/session_agent/test_scan_l4_review.py tests/session_agent/test_scan_l4_recovery.py tests/scan/test_l4_tasks.py tests/scan/test_l4_tasks_gate.py tests/scan/test_l4_tasks_caps.py tests/scan/test_l4_tasks_running_visibility.py
~~~

**完成判据：** 新路径的整票状态完全由原 taskbook 判定；复核真实性、卡 hash 与失败恢复没有弱化。

## C03. L5、观察、最终门与用户进度

**Files**

- Modify：session_agent/workflows/scan.py、publication.py、service.py。
- Reuse：scan/assemble.py、decision_finalize.py、decision_record.py、relative_buy.py、post_run.py、publisher.py、self_review.py、render.py、l4_watch.py、brief.py、gates.py；trace usage/finalize。
- Test：tests/session_agent/test_scan_publish.py、test_scan_progress.py、test_scan_finish.py。

- [ ] L4 完成判据继续读取原 taskbook；SUCCEEDED 与“所有票已到终态”区别保留。FAILED/BLOCKED 按原门处理，不由新入口自行豁免。
- [ ] 调原 assemble 及其 self_review。必须区分原卡评级、ensemble 折回、最终 DecisionRecord、relative_buy，不能选一个最高评级写成 BUY。
- [ ] observe、usage_harvest、usage_reconcile 的路径从 active run staging 派生，不能写旧日期根再声称完成计量。
- [ ] 调原 GATE4；任何硬门失败不得 finalize 为业务 SUCCEEDED。允许的 warning 原样展示，不改严重级别。
- [ ] 补全新任务/回执证据后 finalize capsule；先发布后退出失败等中间状态要有恢复路径。
- [ ] 工具返回报告路径、真实业务状态、缺失证据和计量；不要求模型重新撰写另一份最终评级摘要。

### 进度映射

| 检查点 | 新入口信号源 | 用户输出 |
|---|---|---|
| CP0 | market_view 与原 pack | 原 regime/温度/定调素材 |
| CP1 | GATE1 成功 + _prelude_summary.md | 原文转播 |
| CP2/CP3 | 行业任务与 GATE2 | 地形定调、逐只入围、影子名单 |
| CP4 | taskbook/展开记录 | 真实股数、预算、intel、pinned |
| CP5 | l4_watch/taskbook | 仅真实终态及原因，不按卡存在播成功 |
| CP6 | 原 render gate_hist | 评级分布、停因和门柱 |
| CP7 | GATE4 + brief + 计量 | brief 原文、报告路径、耗时、实测或 UNMEASURED |

- [ ] 状态工具读取原 outbox/cursor，恢复不重播所有通知；不把高频轮询塞进模型上下文。
- [ ] 验证空哨兵仍输出正确 brief 与最终报告，不欠 L4 角色证据。

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_scan_publish.py tests/session_agent/test_scan_progress.py tests/session_agent/test_scan_finish.py tests/scan/test_assemble.py tests/scan/test_assemble_slim0buy.py tests/scan/test_assemble_pinned.py tests/scan/test_gates.py
~~~

**完成判据：** 新扫描路径从入口到原 brief/summary 可完成，门和计量缺失的披露与旧路径一致。

## C04. 同引擎对拍与双宿主真实验收

**Files**

- Create：autoresearch/session_agent/evaluation.py、tests/session_agent/test_comparison.py、test_acceptance_matrix.py。
- Create：docs/session-agent/acceptance.md。
- Reuse：research/efficiency_baseline.py、scan/budget.py、trace/replay.py。

### 离线对拍

- [ ] 从同一引擎允许的输入或合成 fixture 冻结 input/config/prompt/code 身份；旧新路径运行不同 run_id，互不覆盖。
- [ ] 先用固定研究产物测试编排迁移：L0/L1/L2、候选和 bench、卡解析、折回、DecisionRecord、最终门应一致。
- [ ] 比较报告时只归一化明确的 run_id/运行时刻/路径元信息；不抹掉评级、数值、警告、来源、候选差异。
- [ ] 再做真实 LLM 对照，评价事实支持、必需覆盖、时间边界和规则一致性，不要求自然语言逐字相同。

对照记录 Comparison v1 字段为 schema_version、engine、workflow、mode、baseline_run_id、candidate_run_id、input_identity_equal、config_identity_equal、deterministic_diffs、research_diffs、missing_evidence、verdict。verdict 仅 PASS / FAIL / INCOMPLETE。

只有 input/config 身份相等且无决定性差异才能 PASS；缺少基线或计量时为 INCOMPLETE，不用“看起来差不多”替代。

### 真实宿主矩阵

| 场景 | Codex | Claude | 判据 |
|---|---|---|---|
| 单股 LITE 早停与满卡 | 本引擎会话 | 本引擎会话 | 各自真实 run + 产物验证 |
| 单股 FULL | 同上 | 同上 | 必需章节与来源 |
| 宏观 FULL/LITE | 同上 | 同上 | 新 kind、状态 freshness、地形输入 |
| 行业 FULL/LITE | 同上 | 同上 | 数据前置、六节/单段边界 |
| 档案 INIT | 同上 | 同上 | 许可分节及并发保护 |
| 扫描 FULL | 同上 | 同上 | 原全链门与最终报告 |
| 三个其他 scan 模式 | 合成分支 + 适用真实运行 | 同左 | 四模式全部有覆盖，真实/合成明确区分 |
| 中断恢复 | 同上 | 同上 | 无重复认领、无旧回执污染 |
| 独立复核 | 有真实能力才验收 | 有真实能力才验收 | 原始上下文身份可验证 |
| 缺证据/额度不足 | 同上 | 同上 | 等待或失败，不伪成功 |

Codex 实施者不能为了填表去读取 Claude 目录。可共享的仅是经各宿主人工提供的验收结论与代码测试结果；其运行产物仍各自保管。两边尚未分别验收时，文档状态必须写“单引擎通过/另一引擎未验收”。

### 性能与学习验收

- [ ] 用 efficiency_baseline 的真实字段构建按引擎、工作流、模式分层的读数；研究覆盖不同的 run 不混比。
- [ ] 检查主会话 token、研究角色 token、重试、耗时、人工介入与缺失比例；缓存 input 与一般 input 不混淆。
- [ ] 扫描性能声明遵循已有至少 10 次真实扫描要求；没有成熟样本就写观察中。合成测试和同日重复回放不能凑真实样本数。
- [ ] 不预设省 token 百分比，不用少做必要研究来达标；性能无改善仍可记录学习和结构收益。

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_comparison.py tests/session_agent/test_acceptance_matrix.py tests/research/test_efficiency_baseline.py tests/scan/test_budget.py
~~~

**完成判据：** 质量与恢复要求先满足；真实性不充分的项保持 INCOMPLETE。双宿主上线结论以两边实际验收为准。

## C05. 入口切换与旧编排退役

**Files**

- Modify：AGENTS.md、CLAUDE.md、README.md；四个 .claude/skills/*/SKILL.md 和关联 playbook 的入口部分。
- Modify：.claude/workflows/scan-market.js、l4-stock.js、dossier-init.js，仅保留必要的宿主薄适配或最终删除。
- Modify：现有 workflow/agent 契约测试，保留有业务保护意义的断言，迁到实际生产新入口。
- Test：tests/session_agent/test_entrypoints.py、test_legacy_compatibility.py、test_cutover.py。

- [ ] 先记录各入口 C04 结果；未验收的入口维持旧默认路径。
- [ ] 新 CLI 显式选择 orchestration=session_v1，旧路径标 legacy。选择写入 run 的 plan/身份附件；不修改研究策略配置。
- [ ] 逐 skill 将执行流程改为 begin → next/claim → execute 或宿主研究 → submit → finish。原研究正文与模板通过角色引用加载。
- [ ] 保留四个 skill 名称和 .codex/skills 的软链方式。变更技能名称需另列迁移映射，本期不做。
- [ ] 从旧 workflow 删除已迁移的业务分支；宿主仍必须使用 native Workflow 时，只留下已登记任务的派发和回执转交。
- [ ] 用 rg 对 .claude、autoresearch、tests、docs 的有效入口做调用者清点；区分历史设计引用和生产调用。
- [ ] 修改旧 workflow 语法测试前，先迁移其保护的 cfg/pinned/重试/门/trace 断言到新工作流测试，禁止通过删除测试消除回归。
- [ ] 旧 CLI 与历史 run 保留可读兼容；不移动历史 context/reports，不重新归因旧使用量。

### 回退演练

1. 新入口创建一个有两步任务的 run，完成第一步。
2. 将“新 run 默认入口”退回 legacy；验证已存在 session_v1 run 的 plan 未改变。
3. 该 run 可继续按原版本运行，或冻结为 INTERRUPTED；不能交给旧 workflow 从中间接管。
4. 创建新的 legacy run，验证业务输出与原版本一致。
5. 使用新只读验证器读取旧和新 capsule，确认 schema 兼容。

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_entrypoints.py tests/session_agent/test_legacy_compatibility.py tests/session_agent/test_cutover.py tests/contracts tests/common/test_workspace.py
~~~

**完成判据：** 已切换入口只存在一份业务编排真值；回滚不需要修改研究规则或历史数据。

## C06. 开发文档、学习演练与最终交付

**Files**

- Create：docs/session-agent/README.md、architecture.md、operations.md、learning-lab.md。
- Update：current-surface.md、developer-guide.md、tool-catalog.md、acceptance.md。
- Test：tests/session_agent/test_docs_examples.py，只验证会执行的 CLI/契约示例，不对自然语言做镜像测试。

- [ ] README 给出两个宿主的启动方式和四个用户入口，明确所有模型调用仍在官方订阅 session 内。
- [ ] architecture 解释任务 owner、计划展开、父 L4 attempt、业务门、证据 profile 与实际 host receipt。
- [ ] operations 给出等待、失败、损坏、恢复、退出、额度不足、证据不全与回滚的具体处理流程。
- [ ] learning-lab 安排五个有结果可验证的练习：增加一个只读工具；增加一项非评分证据；模拟迟到回执；完成独立上下文复核；比较上下文大小与 token。
- [ ] 每个练习指定使用合成数据或本引擎数据，不修改生产评分参数、不接真实交易。
- [ ] 运行项目全量测试一次；对既有失败保留基线证据，不能宣称全绿。性能测试只在有新变更/失败/未解决问题时重复。
- [ ] 核对总计划 A01–C06 完成证据、所有入口与模式、两个宿主的真实验收、旧入口调用者清点和已知限制。

~~~bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q
git diff --check
~~~

**最终完成标准：** 整个研究项目由统一 session agent 协议驱动，两个引擎各自可运行并留存验证结果；所有研究契约与确定性服务边界仍可检查；旧重复业务编排已收敛。缺任一入口或宿主验收时如实列为未完成，不以教程或架构图代替运行软件。

# Agent Skills 第七批开发记录

## 本批范围

继续已批准开发计划 C6 的本地软件部分：推理 attempt 的模型与计量证据、真实验收固定分母的只读状态接口。完整 C6 仍包含真实双宿主运行与研究边界真实性验证，本批不将这两项视为已经完成。

交易主尺保持决策卡与 `gap_c1_o2`。源码在 `.worktrees/agent-skills-reliability` 修改，保留用户和前六批未提交内容。

## 保存基线

- 原目录与工作树 1,305 个源码/文档文件逐字节一致。
- 保全目录：`context_codex/development/20260930-agent-skills-batch7/`。
- 基线归档：`baseline.tar`。
- SHA256：`839ff47e4840239cc44b7f412dca341fc4e780a284eba54ccd83f4aad276c7a4`。
- 未读取实际 Claude 产物目录；portable proof 只接受在本引擎审计根明确导入的文件。

## 已识别缺口

1. `DispatchRequest` 只有解析后的 model/effort；已有冻结配置 `declared_roles` 可作为 requested，真实 observed 需要从任务绑定的 transcript 重算。
2. 旧 usage ledger 丢失 task/attempt/segment 身份；Codex token snapshot 数不能当作真实模型调用次数。
3. `usage_reconcile` 仅接受 `subagent` 标签，跳过 Codex session_v1 的 `stock.card`、`scan.l4.card` 等实际角色。
4. 固定场景和严格 portable proof 门已有实现，缺少能在无记录时清楚展示全部缺项的操作入口。
5. `research_boundary_gate` 仍为常量 PENDING；真实宿主研究访问验证器尚未实现，不通过计量完整或软件测试解除该门。

## 实施约束

计量使用独立 sidecar，复用现有双宿主 transcript adapter 和证据计划的 attempt 分母；旧精确字段 receipt/binding 契约保持兼容。缺计量为 null/UNKNOWN，observed 不从配置或未经验证的 executor usage 回填。模型、缓存、调用次数、派发次数、耗时与代理输入量分别描述来源及覆盖。

验收状态复用 `required_acceptance_scenarios` 与 `accept_workflow`：每宿主 14 个、双宿主 28 个固定场景。软件、真实会话、默认入口分别报告；缺项保留 INCOMPLETE，研究访问门仍 PENDING。

## 操作接口

- `python -m autoresearch.session_agent metering --run-id <RUN_ID>`：只读计算当前 run 的计量视图。
- `python -m autoresearch.session_agent acceptance-status`：无记录也输出双宿主完整固定分母。
- 状态查询可显式指定 `--records-file` 与 `--evidence-root`，限本引擎的 `_acceptance` 审计根。
- 自动最终产物：`capsule/agents/session/metering.json`。

### 模型与计量口径

| 部分 | 来源与限制 |
|---|---|
| `requested` | 冻结配置的 `declared_roles`，未明确指定时为空 |
| `resolved` | 本 attempt 冻结 `DispatchRequest`；旧输入或角色定义缺少冻结证明时保持未知 |
| `observed` | 通过绑定身份与归档哈希验证的实际 transcript segment，附观察集合与来源；混合值保留 `MIXED` |
| `dispatch_count` | 已冻结交接请求的 attempt 数，basis 为 `FROZEN_HANDOFF_NOT_MODEL_EXECUTION`；不是执行成功或模型调用次数的证明 |
| `model_calls` | 需要可靠宿主计量；缺少证据保持未知，不拿 token 快照数替代 |
| `proxy_input_chars` | 冻结 prompt 的字符数，独立于实测 token |
| `estimated_price` | 独立估算字段；本批无可靠价格输入时保持 null |

每个分项汇总分别给 `observed_count`、`expected_count`、`coverage`、`observed_total`；只有分母内全部测到才给完整 `value`。实际观察的部分合计不能当完整总量。token、缓存、reasoning、宿主用量记录数、宿主模型消息数、模型调用次数与宿主区段时长分别解释。`duration_seconds` 是绑定 transcript 首末 timestamp 的跨度，不能当作模型推理净耗时或整个 run 的调度耗时；后者仍见 C5 scheduling 指标。`host_usage_records` 为 adapter 的用量消息/快照数，`host_model_messages` 为可见 assistant 消息数，两者均不等于 API 调用次数。

### 验收状态口径

`records-file` 是实际 `AcceptanceRecord` 的 JSON 列表。状态命令单列 `software_status`、`real_session_status`、`default_status`、`required_count`、`accepted_count`、缺项、非法记录与每工作流的机器结果。软件测试结果不由 proof 数量推定。即使可解引用场景证据齐备，当前研究访问边界门仍阻止默认启用。

## 开发状态

本批计量与验收状态软件实现已冻结，规格与质量审查（含增量）均通过；真实双宿主矩阵和边界真实性验证器仍未完成。首轮新增用例先复现 9 个失败，随后逐项实现。最新定向结果为 104 passed / 6 skipped；跳过项依赖未随仓库分发的历史真实研究产物，不以合成数据补齐。文档入口检查 10 passed。不同测试组可能重叠，不相加为总覆盖数。

已补绑定上下文与冻结交接/回执对齐、重叠区段拒绝重复计数，以及审计根自身 symlink 的路径约束。规格审查还发现读取计量计划时未校验分母；现已对显式参数、磁盘文件与新生成计划统一调用既有 `validate_evidence_plan`，独立新增用例 1 passed。

首次集成在 3 failed / 382 passed 后停止：新只读 CLI 在同进程留下 `AUTORESEARCH_RUN_ID`，影响后续 L4 任务查询。修复为只读查询不赋值，保留原引擎与运行身份冲突校验；按原失败顺序复验 18 passed，未放松未知 run 的业务错误。

规格审查与独立质量审查首轮均 PASS；随后集成暴露的模块依赖问题已修复，两轮增量复核均 PASS。静态检查相对保存基线新增 0 条，原有 1 条保留。无参数 `acceptance-status` 已实际执行：5 个工作流、28 个必需场景、0 个提供记录的接受项，`real_session_status=INCOMPLETE`、`default_status=PILOT`、`software_status=UNKNOWN`。这次查询没有输入真实记录，不代表扫描了本机所有历史 proof。

第二轮集成结果为 1 failed / 1,373 passed / 6 skipped：`usage_reconcile` 读取上层 session 角色映射，引入了 `trace → session_agent` 的违规依赖。修复方式是将原有纯声明下沉到 contracts 并保留 session 模块兼容导出，保持单一映射来源和原分层白名单。分层修复定向 89 passed / 6 skipped；独立 AST 对比确认 30 个逻辑映射与基线一致，兼容导出仍指向同一函数。

## 最终验证与交付

最终受影响集成：**1 failed / 1,373 passed / 6 skipped，214.56 秒**。本批新增功能、证据分母、环境隔离和分层检查均通过；唯一失败为未在本批改动的 `test_timeout_kills_the_whole_process_group`。独立 headless 整文件复跑为 1 failed / 44 passed；随后当前版本该单项复跑 1 passed。完整批前源码副本的同单项 1 passed、整文件 45 passed。保留所有日志，不将这轮整组记录写成全绿。

该测试期望 `KILLED`，实际记录 `CANCEL_UNCONFIRMED`；两个失败记录均 `cancel_confirmed=null`、组长 `exit_code=-15`，表示信号发送或组探测进入 OSError 分支，不能解释成“进程组仍存活”。具体 errno 未记录，根因尚未确定。孙进程随后消失的断言通过，但这不等于较早的整个进程组已被确认清除。本批没有放宽取消语义或该测试断言。

生产 headless executor 与其测试文件均与本批保存基线逐字节一致。上述重复运行差异作为已知验证限制保留；后续单独补取消诊断时，应保留 OSError 的操作、errno 与当时的进程组证据。

最终同步清单包含 17 个文件。同步冲突、原目录冒烟、CLI 实际输出及逐文件 SHA256 对账记录在保全目录 `verification-summary.json`、`implementation-manifest.json`。本批不创建提交；已保存基线并保留前六批及用户原有修改。

### 证据文件

- `integration.log`：首次环境变量泄漏失败与通过项。
- `integration-before-layering-fix.log`：依赖方向修复前的集成结果。
- `integration-final.log`：最终受影响集成，保留唯一进程超时测试失败。
- `headless-recheck.log`、`headless-single-recheck.log`：当前整文件及单项复验。
- `headless-baseline-probe.json`、`headless-baseline-recheck.log`、`headless-baseline-file.log`：确认从完整批前副本导入源码后的基线对照。
- `headless-failure-evidence.json`：已记录的取消状态与时间信息，不推断未保存的 errno。
- `spec-review.json`、`quality-review.json`：独立审查和增量结论。
- `lint-comparison.json`：原有 1 条诊断，新增 0 条。
- `acceptance-empty.json`：未提供真实记录时的 28 格缺项视图。2026-09-14 及前六批测试数字只作为历史记录，本批不复用为当前验证结论。

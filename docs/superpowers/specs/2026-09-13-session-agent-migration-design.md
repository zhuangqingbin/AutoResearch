# 订阅会话研究 Agent：全项目迁移设计

日期：2026-09-13。状态：开发设计稿；尚未实施，未切换生产入口。

代码核查基线：48d1d4e。适用仓库：TradingAgents / AutoResearch。

用户已确定：迁移整个研究项目；同时学习 agent 开发；继续在 Codex 与 Claude Code 的订阅 session 内运行；不新增直接调用模型的付费 LLM API。

开发入口：[总开发计划](../plans/2026-09-13-session-agent-migration.md)。本文规定架构与契约，三个分计划规定实施任务、文件、测试与切换条件。

## 1. 目标、范围与成功定义

### 1.1 目标

把依赖会话阅读长手册、执行 workflow、手工衔接文件的研究流程，迁移成由官方会话托管、具有显式任务协议和确定性工具的研究 agent 系统。

最终覆盖：全 A 股扫描、单股票 FULL/LITE、宏观 FULL/LITE、行业 FULL/LITE、首覆档案；数据、新闻、衍生品、券商导入、离线评估和运维作为确定性服务保留并明确边界。整个项目纳入架构，不要求每个 Python 模块都成为 LLM agent。

成功必须同时满足：

1. 两个引擎都能从各自官方交互会话启动研究，领取任务、运行工具、提交产物并恢复中断。
2. 角色职责、工具输入输出、上下文范围、任务终态和错误处理均有代码可检查的契约。
3. 同一引擎、相同数据与冻结研究配置下，确定性计算与现有规则保持一致。
4. 新架构能追溯实际工具执行和模型研究；未取得的证据、模型信息或 token 计量如实标缺。
5. 各入口有明确迁移终点、切换条件与回退路径；不永久维护两套研究规则。
6. 学习成果可展示：单 agent 工具循环、角色协作、上下文隔离、状态恢复、契约测试、追踪与质量评估。

### 1.2 本次不改变的行为

- A 股数据继续使用 tushare 与已有 lake 契约；A级空帧仍拒绝入湖。
- 扫描和交易卡继续使用 1–2 日研究尺度及 gap_c1_o2 主尺；不能因 agent 化推出 swing 策略。
- FULL 报告可描述长期基本面，但旧模板中的长期仓位例子不得覆盖 AGENTS.md 的交易尺度约束；迁移不新增长周期交易建议。
- 三门评级、早停、保送票、复核折回、relative_buy、行业地形隔离均由原实现和当前配置决定。
- 零买入日是正常成功结果；不得为了出单放松门。
- 已退役的自动学习、账本回注和自动权重调整不恢复。
- ResearchCard 的 card_source 继续读取各 run 冻结的权威选择；迁移不解除 D5/native JSON 模板冻结。
- 本期不扩展全市场扫描到港股、美股，不增加自动下单，也不把券商导入升级为交易执行器。

### 1.3 运行方式

基础运行方式是官方交互 session。用户登录订阅账户后，在会话中调用项目 skill；会话调用本地 CLI 工具，代码返回数据或研究任务，模型在宿主内完成推理。

不构造 Python 调用当前会话模型的伪接口，不读取或转发订阅 OAuth 凭据，不通过代理把订阅模拟为通用模型 API。codex exec、claude -p、Agent SDK、无人值守后台服务均不是本期基础路径；后续若要使用，单独核验宿主能力与计费规则。

## 2. 当前实现盘点与迁移判定

以下为代码核查结果，而不是仅按旧设计稿推断。

| 当前资产 | 当前事实 | 迁移处理 |
|---|---|---|
| .claude/skills 下四个项目技能 | 每个技能已有流程、输出契约、FULL/LITE 路由 | 保留名称与软链，逐步改为调用统一任务工具的薄入口 |
| .claude/workflows/scan-market.js、l4-stock.js | 现有扫描编排，含通过 LLM 壳执行确定性命令 | 作为行为基线；逐段迁入 Python 流程描述与宿主适配 |
| .claude/workflows/dossier-init.js | prefetch、骨架、研究、lint | 迁为档案工作流，保持确定性节不可由模型改写 |
| autoresearch/scan/l4_tasks.py | 已有锁、尝试次数、哈希校验、恢复、资源帽 | 继续独占扫描单股任务的认领和终态，禁止另建同义任务簿 |
| autoresearch/contracts/inference_task.py | 九字段推理信封，纯形状验证 | 原样复用；新任务不能给 v1 信封塞入额外字段 |
| autoresearch/scan/deterministic_runner.py | 只有 verify_handoff，没有执行、派发、恢复器 | 保留兼容校验；本期新执行桥另建模块，不能声称已有完整 runner |
| autoresearch/scan/user_config.py | 已有双引擎配置、resolve_agent_bundle、能力不匹配与声明 fallback | 接入并复用，不能再发明一套模型路由或自动降档 |
| autoresearch/research/efficiency_baseline.py | 已有真实字段映射、能力报告、按引擎区分的效率读数 | 增量补接数据，不能重复造“基线仪表盘” |
| contracts/stages.py、contracts/profiles.py | 当前 run kind 仅 scan-market、stock-research | 为独立宏观、行业、档案增加声明与 profile；作为明确开发任务 |
| analyze/runctl.py、run_bootstrap.py、run_profile.py | 单股已有可选 capsule，harvest/assemble 内部记录阶段 | 沿用；统一入口开启留存，但兼容原无 run 的 CLI |
| macro/harvest.py、assemble.py、state.py | 有分节组装与 macro_state；未有统一 kind 生命周期 | 增加 run 路径适配与 profile，保留报告内容和 freshness 判定 |
| sector/pack.py、reuse.py、brief.py | pack 依赖扫描数据；FULL 与 LITE 输出不同 | 明确前置数据；新建发布验证适配。当前不存在 sector/assemble.py |
| trace/capsule、events、exec_capture、transcripts | 已有现场留存、事件链、原始记录适配与三类验证结论 | 复用并补登记，不能用新日志替代法证链 |
| news、derivatives、broker、research、ops | 数据、证据、导入、实验和运维服务 | 通过受限工具调用；不授予模型调权、校准、备份删除或成交执行权 |

### 2.1 与历史裁定的关系

2026-09-07 Q-E 曾决定不实施完整 runner，当前代码与该裁定一致。本次用户明确要求订阅 session 形态的全项目 agent 迁移，因此本文提出新的 session 执行桥，并在实施计划中要求记录新的架构决定。

这个方向授权不等于旧研究冻结项全部解冻：D5 的 JSON 权威切换、研究实验、评分旋钮、自动学习等继续遵守原限制。不能借“统一架构”夹带研究行为变化。

## 3. 架构决定

### ADR-SA-01：宿主提供模型，项目提供研究协议

选择官方会话托管。项目不实现模型 client。会话持有自然语言交互、工具调用能力和上下文；项目持有业务规则、任务计划、产物身份、校验与发布。

### ADR-SA-02：核心使用普通 Python，CLI 为唯一基础工具入口

不以 LangGraph、LangChain、Agents SDK 或远程队列作为本期依赖。固定阶段用 Python 调用；模型阶段输出待处理任务交给宿主。MCP 以后可包裹相同 application service，不拥有第二套流程或状态。

这能学习 agent 的工具接口与运行协议，同时适配两个会话环境。代价是宿主中断、配额和模型选择仍受各自产品约束，不能保证离开会话后继续自主执行。

### ADR-SA-03：新顶层包 session_agent，只做集成

新建 autoresearch/session_agent。该包可以调用 scan/analyze/macro/sector/dossier/trace；这些下层包不得反向 import session_agent。纯数据契约进入 contracts，纯路径逻辑进入 common。

tests/contracts/test_layering.py 必须把 session_agent 登记在最高集成层；不能因为新包未登记而绕过依赖检查。保持原存量例外表只减不增。

### ADR-SA-04：统一协议，按宿主能力执行

Claude 与 Codex 共用同一份任务定义、研究正文和工具实现；宿主适配只映射工具名称、原生派发方式和观测回执。

Claude 的 Workflow 与子 agent 能力不能假设在 Codex 存在。Codex 默认遵循当前 AGENTS.md，在会话内按顺序执行角色。只有宿主真实支持且项目指令允许时，才启用并行或独立子上下文。

### ADR-SA-05：整体覆盖，按工作流逐步切换

顺序为基础协议 → 单股 LITE → 单股 FULL → 宏观/行业/档案 → 扫描 → 文档与旧编排收敛。每一步生成可运行的软件。没有达到某入口验收条件时，该入口继续使用旧工作流。

## 4. 组件与依赖

~~~mermaid
flowchart TD
  U[用户] --> H[Codex 或 Claude Code 交互会话]
  S[四个技能与角色说明] --> H
  H --> CLI[session_agent CLI]
  CLI --> APP[任务规划、认领、提交、恢复]
  APP --> OPS[确定性工具注册表]
  OPS --> D[scan / analyze / macro / sector / dossier]
  D --> L[lake 与数据契约]
  APP --> T[任务状态与已有 L4 任务簿]
  APP --> C[capsule / events / exec_capture]
  D --> P[原评级、校验门、报告发布]
  APP --> Q[待研究任务与指定证据]
  Q --> H
~~~

角色提示词只声明允许做什么。实际产物接收、路径约束、评级解析、任务状态更新必须由代码验证。宿主具有的广泛文件系统权限不会因提示词自动消失；CLI 的边界检查只能保证经过 CLI 的操作。

### 4.1 新增文件布局

~~~text
autoresearch/contracts/session_task.py       任务、提交回执、工具结果形状
autoresearch/contracts/session_plan.py       计划版本与 DAG 形状
autoresearch/session_agent/__main__.py       CLI 解析及显式 engine 检查
autoresearch/session_agent/service.py        begin/next/claim/execute/submit/resume/finish
autoresearch/session_agent/plan.py           计划冻结、依赖验证、选择就绪任务
autoresearch/session_agent/store.py          非 L4 任务状态、锁与幂等接收
autoresearch/session_agent/artifacts.py      输入输出登记、哈希与受限路径
autoresearch/session_agent/operations.py     op_id 到静态允许命令的映射
autoresearch/session_agent/executor.py       调用 exec_capture 或已有进程内记录
autoresearch/session_agent/roles.py          角色注册及原提示词引用
autoresearch/session_agent/hosts/base.py     宿主能力与回执协议
autoresearch/session_agent/hosts/codex.py    Codex 任务呈现、能力观测
autoresearch/session_agent/hosts/claude.py   Claude 任务呈现、能力观测
autoresearch/session_agent/workflows/stock.py
autoresearch/session_agent/workflows/macro.py
autoresearch/session_agent/workflows/sector.py
autoresearch/session_agent/workflows/dossier.py
autoresearch/session_agent/workflows/scan.py
autoresearch/session_agent/legacy_scan.py    L4 taskbook 与旧阶段唯一桥
autoresearch/session_agent/validation.py     任务输出契约到业务 validator 的绑定
autoresearch/session_agent/publication.py    完成判定与现有发布器衔接
autoresearch/session_agent/evaluation.py     同引擎新旧方案对照读数
tests/session_agent/                        合同、恢复、工作流与入口测试
~~~

这些都是拟新增文件，不是当前可调用模块。复用接口与每个文件的测试见分计划。现有 .claude/agents 正文与四个 skill 名称暂不搬家；先通过角色注册表引用，宿主专属 frontmatter 不作为通用模型参数。

## 5. 不变量与边界

| 编号 | 不变量 | 验收方式 |
|---|---|---|
| I01 | 仅官方交互 session 完成模型推理，无直接 LLM API 依赖 | 静态依赖检查、实际会话演练 |
| I02 | 显式 engine 在导入 workspace 前确定；禁止同进程切换 | 独立子进程测试、缺 engine 测试 |
| I03 | 两引擎不读写对方 context/reports；lake 为唯一共享可变数据区 | 临时目录双根、路径逃逸和交叉 run 测试 |
| I04 | 任务/尝试/输入哈希/run 身份精确匹配 | 迟到回执、旧尝试、串票、串 run 测试 |
| I05 | L4 终态由 l4_tasks 独占，所有成功以其锁内校验为准 | 替换卡片、重命名 staging、并发提交测试 |
| I06 | 研究配置和 card_source 按 run 冻结，不改变评分行为 | 冻结哈希、旧产物回放、配置漂移测试 |
| I07 | 分析日与证据时间锁定；搜索发现不能冒充原文事实 | 前视证据、T4 仅发现、数字冲突测试 |
| I08 | L3/L4 只吃描述性地形，不吃 L5 行业方向榜或持仓盯梢意见 | 输入白名单与污染字段测试 |
| I09 | 零买入正常；必需数据缺失不得被模型补成成功 | 零买入、A级空帧、schema 错误测试 |
| I10 | 既有早停、强制满卡、独立复核与折回语义不变 | LITE 早停矩阵、OW/SELL 复核矩阵 |
| I11 | 缺 token 或 transcript 标未知；完好性、完整性、可重放性分别报告 | 空计量、部分 transcript、失败 run 验证 |
| I12 | 运行预算沿用观察/告警语义，不因省 token 截断必要研究 | 预算超标仍完成契约所需阶段 |
| I13 | 新架构不恢复自动学习、不写调参旋钮、不执行交易 | 工具允许清单、依赖和产物副作用测试 |
| I14 | 两边没有互相授权代跑；Claude 验收由 Claude 宿主独立完成 | 引擎签名回执、验证清单分别存储 |

独立复核特别规定：同一主会话换一个角色名称，不算新的独立上下文。缺乏独立上下文能力时，保持任务待处理并要求在同引擎的新会话中完成该任务；只有真实宿主记录能证明独立性时才标通过。不能靠隐藏上一结论的提示词声明盲法已经成立。

## 6. 状态归属与工作区

### 6.1 单一状态权威

| 状态 | 唯一权威 | 新架构权限 |
|---|---|---|
| run 生命周期与现场冻结 | trace/capsule | 调用；不另建 active/completed 总账 |
| 扫描单股任务 attempt、认领、终态 | scan/l4_tasks | 通过 legacy_scan 委托 |
| 原阶段业务结果 | scan/stage_result、analyze/runctl | 继续写原契约 |
| 非 L4 的会话交接任务 | session_agent/store | 新建；仅拥有交接与依赖完成事实 |
| 评级、最终交易方向 | rubric、decision_finalize、DecisionRecord、relative_buy | 只读取，不另算最终评级 |
| 证据与 token | 现有 trace/UsageLedger | 追加实际观测，不合成缺失事实 |

session 状态不是业务评级账本。非 L4 任务完成只代表“指定业务 validator 已接受这些产物”；扫描 L4 的状态只投影已有 taskbook，不在 session_tasks.json 再保存一份可独立更新的状态。

### 6.2 路径

所有运行路径从 workspace 与 RunHandle 得到。以下 <engine>/<run_id> 仅用于文档示意，生产代码不能拼裸根。

~~~text
context_<engine>/<kind_spool>/<run_id>/
  session/
    plan.json                  冻结计划
    host_profile.json          开场宿主能力快照
    tasks.json                 仅非 L4 任务
    receipts/                  接收回执与幂等键
    requests/                  给宿主的任务包
  staging/                     延续该 kind 的产物布局
reports_<engine>/              延续当前发布目录约定
lake/                          共享确定性数据
~~~

拟新增 kind/spool：macro-research → macro_runs，sector-research → sector_runs，dossier-init → dossier_runs。必须同步扩展 contracts.stages、workspace、profile factory、bootstrap、completeness、retain/finalize 和测试；不得只在路径表里加键。

扫描中调用的宏观 LITE、行业 LITE 和逐股 LITE 均属于父 scan run，不再各建独立 capsule。独立入口才创建自己的 kind。同一任务不能同时被父子两份 usage 分母重复计数。

旧 CLI 无 run 时保留旧路径；新入口优先在 run staging 产出。宏观、行业的日期级最新视图，以及档案正文，只在验证成功后原子发布，同日并发须持有目的路径锁。

## 7. 核心协议

### 7.1 SessionPlan v1

字段全部必须出现；未使用的可空字段写 null。未知字段拒绝，不依靠模型猜默认值。

| 字段 | 类型与含义 |
|---|---|
| schema_version | 整数 1 |
| engine / run_id / run_kind | 与实际 run 契约一致 |
| requested_mode | 用户请求的模式；scan 可为 AUTO，实际模式由原 run_mode.json 决定；其他入口为 FULL/LITE，档案为 INIT |
| analysis_date | ISO 日期，经过现有日期与业务日历验证 |
| orchestration_version | 本期固定 session_v1；旧路径标 legacy |
| input_contract_hash | 现有 run 输入契约哈希；不是提示词摘要 |
| config_hash / host_profile_hash / roles_hash | 对冻结输入规范 JSON 求 SHA-256 |
| tasks | 按 task_id 唯一的 TaskSpec 数组 |
| task_templates | 固定的展开模板：template_id、expander、depends_on、allowed_roles；不含模型生成的执行代码 |
| plan_hash | 对其余全部字段规范序列化后求哈希 |

原 RunContract v3 不塞入不认识的新键。先把 plan.json 纳入 capsule 身份与产物规则，通过 plan_hash 绑定提交；若以后升级 RunContract，必须另升 schema 并保留旧读者。

### 7.2 TaskSpec v1

字段：task_id、kind、role、operation、dependencies、input_artifact_ids、output_artifact_ids、expected_output_contract、owner、subject、independent_context、parent_task。

- kind 仅 DETERMINISTIC / INFERENCE；前者 operation 必填、role 为 null；后者相反。
- owner 仅 SESSION / L4_TASKBOOK。后者 subject 必须是扫描 taskbook 中的六位代码。
- dependencies 是 task_id 列表；拒绝重复 ID、缺依赖和环。次序变化不影响同一 DAG 的语义。
- input/output 仅登记过的 artifact ID，不接受任意文件路径。
- independent_context 为布尔值，只对推理任务有效。
- parent_task 必须出现；无父票时显式为 null。扫描整票内部的交接任务填写 {owner: L4_TASKBOOK, subject: 六位代码, attempt: 整票当前尝试}。此字段属于 TaskSpec，不能加入旧九字段信封。接收子动作前必须检查父票仍处于对应 RUNNING attempt。
- task_id 格式为小写字母、数字、下划线、连字符、句点的组合；领域 subject 单独保存，避免行业中文名、交易所后缀变成路径控制字符。

RoleSpec 的字段为 role_id、instruction_refs、input_policy、output_contract、context_policy、tool_policy、config_role。其中 config_role 可以为 null，表示继承当前会话；不能假装宿主支持不存在的模型切换。

### 7.2.1 候选出现后的任务展开

扫描的行业、finalists 和复核需求在运行中才产生，不能在 begin 时假装已知。plan.json 冻结静态任务与展开模板；确定性 expander 读取已验证的 run_mode.json、行业清单、finalists 或原卡结果后，产生 session/expansions/<expansion_id>.json。

Expansion v1 的字段为 schema_version、expansion_id、plan_hash、template_id、input_artifacts、tasks、expansion_hash。input_artifacts 保存 artifact_id 与 sha256；expansion_hash 对其余字段规范序列化求哈希，expansion_id 为 template_id 与该哈希前 16 位组合。展开记录只写一次，不覆盖。新任务输入哈希在登记时绑定到这些已验证快照；模型没有添加角色、提高并发帽或扩展候选池的权限。

同模板同输入重复展开必须返回相同记录；输入变化不得覆盖已派发任务，必须报冲突或按已登记的后续模板产生新任务。next 只消费静态计划和已验证展开记录。finish 要检查所有应发生的模板都已展开或有原模式判据支持的“不适用”记录，不能因任务列表为空就宣布完成。

实际 scan mode 从 run_mode.json 读取并绑定展开证据，原样传给 scan_profile 与 finalize。AUTO 只是新入口请求值，绝不进入现有四种业务模式枚举。

### 7.3 推理交接与提交

继续使用现有 inference_task.validate_envelope 的九字段：schema_version、engine、run_id、task_id、role、input_artifact_ids、input_contract_hash、expected_output_contract、attempt。

通用提交 TaskSubmission v1 的精确字段为：

~~~json
{
  "schema_version": 1,
  "envelope": {
    "schema_version": 1,
    "engine": "codex",
    "run_id": "20260913T010203000000Z",
    "task_id": "stock.card",
    "role": "stock.card",
    "input_artifact_ids": ["stock.slim"],
    "input_contract_hash": "0000000000000000000000000000000000000000000000000000000000000000",
    "expected_output_contract": "stock.lite.v1",
    "attempt": 1
  },
  "plan_hash": "1111111111111111111111111111111111111111111111111111111111111111",
  "outputs": [{"artifact_id": "stock.card", "sha256": "2222222222222222222222222222222222222222222222222222222222222222"}],
  "host_receipt_id": null
}
~~~

上例为完整合成夹具，固定 hash 仅用于形状测试；真实提交必须使用登记产物和实际 hash。stock.lite.v1 是 B01 拟登记的输出契约名，不是当前已有 validator。host_receipt_id 只指向本引擎实际采集的宿主回执，缺失时为 null；不能提交一句“已执行”作为回执。独立复核任务缺有效回执时不得标为独立完成。

submit 在对应 owner 的事务边界执行：验证信封和 plan_hash → 验证 run 仍 ACTIVE → 验证当前 attempt 与输入哈希 → 安全打开登记输出并重新计算哈希 → 调用领域 validator → 写接收意图 → 更新非 L4 终态，或委托 L4 mark_success → 写已接受回执。接收意图不是成功凭证。崩溃后按 owner 权威状态与产物哈希补齐回执；不能先写 ACCEPTED 再尝试 mark_success。返回原接收回执的重复提交是幂等成功；相同任务提交不同内容必须拒绝。非 L4 store 不持自己的锁再调用 L4 锁，避免两个 owner 互相等待。

输入验证同时检查 run 的 input_contract_hash 和每个实际输入快照的 sha256；前者不能替代后者。claim 冻结任务输入描述，submit 对这些快照重新核验。上游文件被改动时拒绝接收，不能拿新输入解释旧模型产物。输出先登记 ID 与受限位置，未产出时 sha256 为 null；接受成功后绑定真实 hash，后续不能修改。

L4 的 scan.deterministic_runner.verify_handoff 使用 task.code 与 task.attempt，新桥需映射到该旧契约。不得把通用 task_id 直接传进去造成身份错配。

### 7.4 工具接口

拟实现 CLI 为 uv run --no-sync python -m autoresearch.session_agent，所有子命令只处理本引擎。

| 命令 | 参数 | 状态影响 |
|---|---|---|
| begin | 必需 --request-file；可选 --kind、--mode、--date、--subject 一致性断言 | 校验输入和宿主能力后创建 run 与计划 |
| status | --run-id | 只读状态、阻塞原因、缺失证据 |
| next | --run-id | 只读返回就绪任务；空列表不等于完成 |
| claim | --run-id、--task-id、--expected-attempt | 原子认领；返回确定的 attempt 与任务包 |
| execute | --run-id、--task-id、--attempt | 只执行已认领的确定性 operation |
| submit | --run-id、--submission-file | 验证并接收模型产物，不重新评分 |
| resume | --run-id | 核验 run、冻结输入、任务与进程状态；仅处理可安全恢复部分 |
| finish | --run-id | 检查完整任务集合、调用领域发布和 finalize |

begin 创建 run 后输出 run_id。后续子命令在入口、导入 workspace 之前设置并检查该 run；不得要求用户记住所有中间环境变量。AUTORESEARCH_ENGINE 必须显式设置且不写入共享 .env。

工具输出 ToolResult v1 字段：schema_version、command、run_id、state、tasks、result、errors。state 仅 READY / WAITING / BLOCKED / DONE；这是工具界面状态，不替代业务 StageResult。所有字段必须出现；tasks/errors 默认空数组，result 无数据时为 null。

退出码：0 表示命令有效执行，包括正常 WAITING；2 为参数/契约错误；3 为领域校验阻断；4 为宿主能力不足；5 为可重试工具故障；6 为身份冲突或不安全恢复。不同退出码不能统一重试。

### 7.4.1 启动请求与参数归一化

BeginRequest v1 是 --request-file 的精确 JSON 对象；所有字段必须出现，无值写 null。Python 无法自行探测当前会话工具清单，host_profile 必须由宿主适配按实际工具与可验证记录生成；未知能力保留 null。

| 字段 | 类型与约束 |
|---|---|
| schema_version | 整数 1 |
| kind | scan-market / stock-research / macro-research / sector-research / dossier-init |
| requested_mode | scan 为 AUTO；stock/macro/sector 为 FULL 或 LITE；dossier 为 INIT |
| analysis_date | 必需 ISO 日期；自然语言“今天”由宿主先归一化，不能隐式取进程本地日期 |
| subject | stock 为原 ticker；sector 为申万行业标识；dossier 为原支持代码；scan/macro 为 null |
| peers | stock 为原支持的 ticker 数组，无同业为 []；其他入口必须为 [] |
| asset_type | stock 为原 harvest 支持值；其他入口为 null，不在迁移中扩大资产种类 |
| name | 原入口允许的展示名称或 null；不作为证券身份 |
| host_profile | 设计 §9 定义的 HostProfile 对象；engine 必须等于显式环境 |
| predecessor_run_id | 普通新任务为 null；冻结 run 后继任务为可核验的本引擎旧 run_id |

CLI 的可选 --kind/--mode/--date/--subject 仅断言请求文件中的对应值，相同则通过，冲突返回退出码 2；不使用静默覆盖优先级。请求通过验证后冻结为 session/request.json，登记为任务根输入并纳入 capsule 身份，不能在 run 中修改。构建领域请求时显式映射 subject→ticker、analysis_date→date、requested_mode→mode，其余参数沿原入口语义传递。

非 L4 的 claim.expected_attempt 表示希望领取的下一次尝试：初始计数为 0，首次传 1；重试传原计数加 1。同 session_ref 对仍 RUNNING 的同一 attempt 重复 claim 返回原认领回执；其他会话不能接管。交接给新会话必须先经 resume 核验旧执行状态，再释放或重派。L4 继续以原 preflight 的实际 attempt 语义为准，由 legacy_scan 转换。

### 7.5 状态机和恢复

非 L4 任务：PENDING → RUNNING → SUCCEEDED；RUNNING 失败进入 FAILED 或 BLOCKED。FAILED 只有在任务策略允许、确认前一次未完成或产物可安全重用后才回到 PENDING；BLOCKED 不自动重试。

next 不认领；claim 不执行；submit 不改输入；finish 不补研究。这四个边界用于测试与教学。

恢复保证是“尝试可识别、重复结果幂等、业务发布有锁”，不宣称整个 LLM 工作流 exactly-once。已发生的模型调用不能撤销，也不能仅因网络返回丢失就假定未计费。

长命令继续由 exec_capture 跟踪实际进程及输出。恢复时先核验 process identity、退出事实和目标产物；进程仍活着就等待。没有死亡或完成证据时阻塞，不 pkill、不启动副本。已 finalize 的 capsule 不重新激活；恢复冻结 run 时创建后继 run，记录 predecessor_run_id 于新 session plan 的 request 附件，引用经验证的只读旧证据，不能覆盖原现场。

TASK_ATTEMPT 和 INTEL_RESEARCH 两套现有错误分类保持分离。L4 继续使用既有最大尝试次数；情报再搜继续使用自己的上限。新增确定性 operation 必须声明是否幂等，不能套通用无限重试装饰器。

## 8. 研究工作流迁移

### 8.1 单股 LITE

独立入口：analyze.runctl.begin(LITE) → harvest --slim → P0/P1–P3 → 原早停判定 → survivor P4/P5 → 卡验证 → 留存与报告完成。

扫描入口使用父 run、现有 l4_tasks 和 l4-stock 等价链，不创建 standalone run。两条路径共享角色说明和领域校验，保持各自输出位置与完整性分母。

不因分段任务化将 P4 深核提前装入 P1–P3 上下文。不得把早停卡补写成满卡。扫描中 ≥OW 复核、pinned 卖出复核、同档早止、中位折回与 degraded 展示保持原语义。

### 8.2 单股 FULL

按现有 engine-playbook 的先明细、后研究经理、后风险、最后 PM 的依赖顺序迁移。把“角色”与“派发次数”分开：同一宿主可以顺序完成多个角色；只有实际新上下文才计为独立派发。

保留现有 11 个必需文件与 7 个可选 lens。最终报告继续使用 analyze.assemble；不能只返回一份总论冒充完成所有阶段。A 股与美股 company-intel/us-intel 条件路由和 as-of 规则保持一致。

### 8.3 宏观 FULL/LITE

FULL 仍产区域、跨资产、中美专题、中观、综合与两张配置表；macro_state 继续由原 state 模块派生，保留 freshness 与 regime 失效规则。

LITE 使用 strategist_pack 的允许字段投影，不能因为统一工具返回完整 market_pack 而泄漏 sector_healthy_top3、run_contract 或 user_config。扫描场景写父 run 的 market_view.md；独立场景使用 macro kind 的 LITE profile。

### 8.4 行业 FULL/LITE

pack 依赖扫描数据。独立行业入口先验证同引擎、同分析日的扫描输入是否完整；缺失时生成一个明确的确定性“准备行业基础数据”任务，只运行必要 frame/universe，不自动启动 L3/L4 全市场研究。

FULL 保留六节结构、可选 sector-intel 和海外映射的事实边界；LITE 仅发布描述性地形段。FULL 报告不能被当成 brief 注入 L3/L4。复用必须通过现有 sector.reuse，不能恢复已退役的跨日个股卡 TTL 跳过研究。

### 8.5 首覆档案

prefetch → builder 生成候选骨架 → dossier-init 只编辑许可 LLM 节与摘要 → schema.lint_dossier → 发布本引擎档案。季度 reconcile、pool、delta、debt_slo 仍为确定性服务。

候选档案先写 run staging，验证后锁定目标档案原子替换；已有人工修订不得被旧快照覆盖。模型写入的范围由候选前后分节比较验证，不靠指令保证。

### 8.6 扫描

按当前 workflow 实际顺序迁移：begin → frame/strategist_pack → prelude 与市场研判 → GATE1 → run_mode → 行业 brief 与 L3 证据 → L3 分诊/精排/既有修复 → finalists/GATE2 → L4-prep → taskbook → 每股研究与复核 → assemble → observe/usage → GATE4 → finalize。

保留 FULL、FORCED_FULL、SENTINEL_EMPTY、SENTINEL_PINNED 四种模式。只有 SENTINEL_EMPTY 无 L4 义务；SENTINEL_PINNED 必须保留持仓卡及适用复核。

prelude 的步骤集合从 STEP_NAMES 派生，不把本设计日期的 12 项复制成新的运行真值。gate1/gate2/gate4 必须运行；GATE3/slim 的规则沿用当前批量或流式配置。任务全部终止不等于全部成功，BLOCKED/FAILED 必须进入既有失败处理路径。

报告仍由原发布器完成：原始卡评级、复核后评级、DecisionRecord 和 relative_buy 的 BUY 决策分层保持。主会话继续按 CP0–CP7 展示进度，CP7 读 brief 原文；工具不添加另一份主观总结评级。

## 9. 提示词、工具与能力管理

角色正文的原文件继续作为单一来源，先拆出宿主差异再考虑移动。角色注册表声明 instruction_refs 的顺序与哈希。禁止把所有角色、全手册和历史报告注入每一次任务。

任务包只含：当前角色说明、冻结约束、输入 artifact 描述、输出契约、工具范围、停止条件。实际输入证据由安全读取工具按需取得；必须通过字段投影形成地形，不能用模型总结替代隔离规则。

原生 WebSearch/WebFetch 仍由宿主执行。搜索结果属不可信输入，不能下达修改配置、删除文件或调用外部执行器的指令。引用继续经现有 news/claim 与 source lineage 体系；搜索摘要不等于 canonical 原文。

HostProfile 的最小字段：schema_version、engine、session_ref、deterministic_exec、capture_binding、inference_handoff、safe_resume、independent_context、native_dispatch、web_search、web_fetch、observed_model、observed_effort、evidence_refs。能力值为 true/false/null；null 表未知。模型和 effort 为实际观测，不能从配置声明反推。

现有 resolve_agent_bundle 的 UNCHECKED/MISMATCH/显式 fallback 结果直接保留。没有真实能力证据时不静默选择较低模型。模型可用缓存也不证明本次宿主允许派发或已经执行。

工具注册表仅暴露研究必需 operation。不得暴露任意 shell、eval、exec、任意模块名或 agent 提供的 argv。备份删除、factor_lab calibrate、实验 promote、broker 成交执行均不进入研究 agent 工具表；用户显式请求的确定性运维命令仍由原 CLI 处理。

## 10. 留存、计量和发布

- plan、HostProfile、任务包和接收回执纳入同一 capsule 的身份/产物映射；所需事件在现有事件链中追加。
- 不创建第二个 transcript 仓库；复用 trace/transcripts 与现有本引擎绑定机制。无法获得的宿主记录写缺失原因。
- scan 继续通过 exec_capture 留命令证据；stock 已有进程内 checkpoint，不新增“为了记录执行再派一个 LLM”的控制壳。
- 新 macro/sector/dossier profile 只把实际捕获的阶段列为 captured_stages。没有实现捕获就不能声称有完整命令日志。
- 一个 session 主线程内的多个逻辑角色，不能把整段会话 token 分别记到每个角色。无法可靠切分时只报告 run 总量，角色明细为 null。
- 原始 input/output/cache token、weighted_input_proxy、估算美元与真实订阅费用分开。零额外 LLM API 不等于 token 为零。
- 同一批任务中同一工具结果复用必须有身份和哈希依据；跨日重研仍按现有规则执行。
- 业务完成、证据完好、证据完整、确定性可重放各自输出；任何一项不能代表其他三项。

## 11. 验收、上线和回滚

### 11.1 四级验收

1. 离线契约测试：状态、身份、路径、输入投影、失败与幂等。
2. 确定性对拍：同一引擎、冻结配置和数据；候选集、数值、评级解析及组装结构一致。
3. 会话验收：Codex 与 Claude 分别在自己的宿主内完成小样本、恢复、独立复核和失败案例。
4. 运行观察：同引擎同模式的成熟样本，记录耗时、介入、失败、重试、研究覆盖与 token。扫描性能主张沿用至少 10 次真实扫描的已有要求；这不证明交易收益提高。

LLM 文本不要求逐字一致，验收比较事实支持、必需章节、时间边界、评级规则与研究覆盖。单引擎通过不能宣称双引擎已通过。fixture 用人工合成或本引擎允许的数据；Codex 不读取 Claude 产物来做对拍。

### 11.2 切换

新入口默认显式选择 session_v1，旧入口在验收前继续保留。工作流级选择记录于 plan，不添加第二份选股配置。每个入口独立通过后再改 skill 默认调用路径。

扫描最后切换。删除 .claude/workflows 的旧业务编排前，必须完成调用者清单、四模式回归和两个宿主的真实验收。如个别宿主仍需 native dispatch，可保留薄 adapter，但不得包含第二份研究规则或门判据。

### 11.3 回滚

回滚只影响新 run 的入口选择，已创建 session_v1 run 按其冻结版本完成、等待或冻结；不把半个新 run 塞给旧 workflow。

已发布报告不覆盖。失效任务不得清空任务簿重来。旧 capsule 按原版本读取。新增 schema/profile 必须向后兼容；回滚时保留新产物的只读验证能力。

## 12. 外部依据与本地证据

外部文档核验日期：2026-09-13；本设计只依赖交互订阅登录和本地 skill/tool 能力，不据此推导无人值守额度。

- [Codex 身份验证](https://learn.chatgpt.com/docs/auth)：订阅登录与 API key 登录是不同访问方式。
- [Codex Skills](https://learn.chatgpt.com/docs/build-skills)：skill 可组合指令、脚本和参考资料，按需加载。
- [Claude Code 身份验证](https://code.claude.com/docs/en/authentication)：可使用 Claude 订阅账户进入官方会话。
- [LangGraph 架构概览](https://docs.langchain.com/oss/python/langgraph/overview)：固定步骤与模型步骤可以组合；本期采取同样的职责划分，不引入其运行时。

本地真值优先级：用户当前裁定 → AGENTS.md / CLAUDE.md → 当前源码与契约测试 → 项目技能 → 历史设计。历史文档中的已删除模块、旧路径、长期交易示例不作为迁移目标。

本设计只新增开发文档。工作树中原有 pinned.jsonc 修改及另一份 2026-09-13 漏斗讨论稿不属于本次修改范围。

# Session Agent 工具目录与证据边界

本目录是 `session_v1` 可执行面的审计索引。运行时只接受
`autoresearch.session_agent.operations` 静态注册的 operation ID；模型不能提交模块名、shell
字符串、文件路径或凭证来扩大执行面。目录元数据与可执行注册表有集合一致性检查。

## 可执行操作

| Operation | 参数 | 副作用与输出 | 调用者 | 错误与限制 | 幂等 | 旧 CLI |
|---|---|---|---|---|---|---|
| `test.noop` | `message`，最多 200 字符 | 测试捕获进程，无生产写入 | session-agent 测试 | 非法参数、进程失败；仅测试 | 是 | 否 |
| `research.calculate` | 注册 calculator ID、冻结 input artifact ID、精确 parameters | 写内容寻址的 calculation evidence | 任一能力的有界补算支路 | 仅四个纯计算器；无网络、shell、源码或动态 import | 是 | 否 |
| `stock.harvest` | 冻结的 ticker、analysis_date、asset_type、peers、slim | 读供应商/数据湖，写当前 run 的 stock pack | `stock.harvest` | 数据契约及既有供应商重试上限 | 是 | 是 |
| `stock.validate` | 无 | 写 LITE 卡校验 | `stock.validate` | 领域校验、artifact 冲突；无网络 | 是 | 否 |
| `stock.publish` | 无 | 写 run 内发布包 | `stock.publish` | 校验、并发冲突；无网络 | 是 | 否 |
| `stock.full.validate` | 无 | 写 FULL 必需产品校验 | `stock.full.validate` | 缺产品、领域校验；无网络 | 是 | 否 |
| `stock.full.assemble` | 无 | 组装 FULL 报告、manifest、发布包 | `stock.assemble` | 缺产品、组装失败；无网络 | 是 | 是 |
| `macro.harvest` | 无 | 读既有宏观源并写 macro data/global tape | `macro.harvest` | 数据契约；沿用 FRED、yfinance、akshare、tushare 上限 | 是 | 是 |
| `macro.lite.frame` | 无 | 写 market pack 与 strategist pack | `macro.frame` | 数据/投影失败；不运行 L3/L4 | 是 | 是 |
| `macro.lite.validate` | 无 | 写六段市场研判校验 | `macro.lite.validate` | 领域校验；无网络 | 是 | 否 |
| `macro.publish` | 无 | 写宏观发布包 | `macro.publish` | artifact 冲突；无网络 | 是 | 否 |
| `macro.full.validate` | 无 | 校验 FULL 章节和资产配置行 | `macro.full.validate` | 缺产品、领域校验；无网络 | 是 | 否 |
| `macro.full.assemble` | 无 | 组装报告、状态候选和发布包 | `macro.assemble` | 缺产品、组装失败；无网络 | 是 | 是 |
| `sector.prepare` | 无 | 复用同引擎扫描输入或构建一次 market frame，写 sector pack | `sector.*.prepare` | 未知行业、数据契约；不做市场排名/L3/L4 | 是 | 是 |
| `sector.validate` | 无 | 写地形段或 FULL 报告校验 | `sector.*.validate` | 领域校验；无网络 | 是 | 否 |
| `sector.publish` | 无 | 写行业发布包 | `sector.*.publish` | artifact 冲突；无网络 | 是 | 否 |
| `dossier.prefetch` | 无 | 三条既有取数腿写当前 run staging | `dossier.*.prefetch` | 每条腿独立降级 | 是 | 是 |
| `dossier.skeleton` | 无 | 写候选骨架与不可变分节权限 | `dossier.*.skeleton` | 已初始化、骨架非法；无网络 | 是 | 是 |
| `dossier.validate` | 无 | 写 lint 与确定性分节字节保护结果 | `dossier.*.lint` | 领域校验、确定性内容被改；无网络 | 是 | 否 |
| `dossier.publish` | 无 | 写档案发布包 | `dossier.*.publish` | artifact 冲突；无网络 | 是 | 否 |
| `scan.frame` | 无 | 原市场帧与 strategist 投影写活动扫描 staging | `scan.frame` | 数据契约、投影失败；原供应商/湖策略 | 是 | 是 |
| `scan.prelude` | 无 | 按原 `STEP_NAMES` 跑完整确定性前奏 | `scan.prelude` | 数据契约、前奏失败；步骤与重试归原模块 | 是 | 是 |
| `scan.gate1` | 无 | 原 GATE1 并冻结四态 `run_mode` | `scan.gate1` | 门失败、预算非法、模式失败；无网络 | 是 | 是 |
| `scan.sector.prepare` | 无 | 原行业复用与 run 内 sector packs | `scan.sector.prepare` | 行业 pack 失败；受 `sector.max_briefs` 限制 | 是 | 是 |
| `scan.sector.skip` | 无 | 记录哨兵分支行业研究不适用 | `scan.sector.skip` | 模式错误；无网络 | 是 | 否 |
| `scan.l3.prepare` | 无 | 原证据采集、分诊与紧凑表 | `scan.l3.prepare` | L3 输入失败；沿用 pass1/candidate 契约 | 是 | 是 |
| `scan.l3.lint` | 无 | 原 judged JSON lint | `scan.l3.lint` | schema/证据失败；无网络 | 是 | 是 |
| `scan.l3.repair.skip` | 无 | 记录无需局部修复 | `scan.l3.repair.skip` | 无网络 | 是 | 否 |
| `scan.l3.repair.apply` | 无 | 校验并原子合并局部 thesis patch；失败时保留原 judged 并写 `DEGRADED` | `scan.l3.repair.apply` | patch 越权或仍不通过 lint；单次、可选降级 | 是 | 是 |
| `scan.l3.merge` | 无 | 原 finalists merge 与 GATE2 | `scan.gate2` | merge/GATE2 失败；使用冻结的 GATE1 预算 | 是 | 是 |
| `scan.gate2.skip` | 无 | 写 pinned/空 finalists 与“不适用”GATE2 记录 | `scan.gate2` | 模式错误；仅哨兵、无网络 | 是 | 是 |
| `scan.l4.prepare` | 无 | 原 producers/prompts 后初始化 taskbook | `scan.l4.prepare` | dispatch/taskbook 身份失败 | 是 | 是 |
| `scan.l4.skip` | 无 | 空哨兵写 L4/review 不适用记录 | `scan.l4.skip` | 仅 SENTINEL_EMPTY | 是 | 否 |
| `scan.l4.ticket` | subject | L4_TASKBOOK owner 占位；不能由 domain_ops 执行 | `l4.<code>.a<n>` | 必须走原 preflight | 是 | 是 |
| `scan.l4.slim` | subject | 调原 prepare_slim，重试产物按 attempt 隔离 | L4 child | tushare 帽与原数据契约 | 是 | 是 |
| `scan.l4.intel.status` | subject | 原 guard/status 与降级披露 | L4 child | 原查询预算和状态契约 | 是 | 是 |
| `scan.l4.intel.disabled` | subject | 写明确 disabled 状态 | L4 child | 无网络 | 是 | 否 |
| `scan.review.plan` | 无 | 从卡评级、proposal、pinned 生成复核矩阵 | `scan.review.plan` | 卡契约不完整 | 是 | 否 |
| `scan.review.none` | subject | 记录本票无需复核 | review child | 无网络 | 是 | 否 |
| `scan.review.decide` | 无 | 同档早止或声明第三次复核 | `scan.review.decide` | 复核评级不可解析 | 是 | 否 |
| `scan.review.skip` | 无 | 空哨兵写复核跳过 | `scan.reviews.skip` | 仅空哨兵 | 是 | 否 |
| `scan.review3.skip` | 无 | 空哨兵写 L4 完成 | `scan.review3.skip` | 仅空哨兵 | 是 | 否 |
| `scan.l4.finalize` | subject | 写 ensemble/stage result/ticket 并调用原 mark_success | L4 finalizer | 父 attempt 或卡 hash 失败 | 是 | 是 |
| `scan.l4.complete` | 无 | 要求原 taskbook 全部 SUCCEEDED | `scan.l4.complete` | FAILED/BLOCKED 不豁免 | 是 | 否 |
| `scan.assemble` | 无 | 调原 publisher 构建 run 内候选报告 | `scan.assemble` | self-review/必需文件失败 | 是 | 是 |
| `scan.gate4` | 无 | 调原最终硬门 | `scan.gate4` | 硬门失败 | 是 | 是 |
| `scan.usage` | 无 | 实际 usage harvest/reconcile 或 UNMEASURED | `scan.usage` | 不估算缺失 token | 是 | 是 |
| `scan.observe` | 无 | 观察记录、文件 manifest 与 CP7 发布包 | `scan.observe` | 报告被改或观察失败 | 是 | 是 |

代码目录中的每项还声明 `stage`、精确 `outputs`、`callers`、`errors`、`limits`、
`idempotent`、`retained_cli` 和唯一的 `replay_classification`。生产分类只能是
`COMPUTE`、`SOURCE_REPLAY`、`EFFECT_PLAN` 或 `CONTROL_ONLY`；`test.noop` 单独为
`TEST_ONLY`，不进入生产重放分母。新增 operation 没有分类或同时落入两类时，注册表导入即失败。
表中的“旧 CLI”表示确定性实现继续复用原模块；它不是另一套业务编排入口。

## 权限分区

| 分区 | Session agent 可用边界 | 保留在人工或独立系统的动作 |
|---|---|---|
| data / dataflows | 通过上表固定 operation 读供应商和共享 `lake/`，产物只写活动 run | 任意 module 执行、任意 lake 写入 |
| news / claims | 具备已观测 web 能力的角色可提交逐条 News Evidence v1 | 将未知发布时间补写为推测值、绕过时间截止 |
| derivatives | 仅消费原 pack 已允许的确定性字段 | 新增评分字段、扩大研究预算 |
| broker | 无下单、撤单、账户写入 operation | 所有真实交易动作 |
| research state | 只产生当前 run 候选，发布时使用并发 hash 校验 | 自动晋升研究参数、修改 weights、跨引擎借用状态 |
| operations | begin/next/claim/execute/submit/fail/retry-l4/resume/finish 的状态机 | 任意删除备份、修改历史 run、跳过业务 gate |

研究角色只能打开任务声明的输入 artifact，写声明的输出 artifact。网页角色还要通过宿主
`web_search` 或 `web_fetch` 的真实能力观测；缺失时任务保持等待或失败，不能用模型记忆冒充检索。
任何 token、cookie、API key、登录态和请求头都不属于模型输入或证据产物。

## News Evidence v1

每条网页/新闻断言使用精确字段：`schema_version`、`analysis_date`、`title`、`claim`、
`source_url`、`source_tier`、`published_at`、`available_at`、`canonical_status`、
`canonical_url`。时间必须是带时区 ISO 8601；来源没有提供时保留 `null`，不得从抓取时间、归档时间
或文章排序反推。晚于 `analysis_date` 的证据不能进入该日研究。

来源层级为 T1–T4。T4 聚合页必须继续打开权威原文，并记录
`canonical_status=FOLLOWED` 与 `canonical_url`；无法取得原文时，该证据不能作为已验证事实。
证据对象拒绝额外字段，因此凭证、提示词和宿主私有状态无法混入证据包。

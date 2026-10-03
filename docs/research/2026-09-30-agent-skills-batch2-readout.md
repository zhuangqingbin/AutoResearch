# Agent Skills 第二批开发记录

## 授权与基线

用户在第一批交付后要求继续开发。沿用 [开发计划](../superpowers/plans/2026-09-30-agent-skills-reliability-development-plan.md) 与既有隔离工作树；第一批状态见 [第一批记录](2026-09-30-agent-skills-implementation-readout.md)。

- 源工作区基线：第一批已同步版本，共 1,272 个已跟踪或未忽略源码路径。
- 保全目录：`context_codex/development/20260930-agent-skills-batch2/`。
- `baseline.tar` SHA256：`9fc328e76d79c23c9dca9040d5e1f74e8523283b6ef46bd2f90237dc2e02cc80`。
- 开发位置：`.worktrees/agent-skills-reliability`。保留用户修改，不提交 Git。
- 本批实际验证解释器：`uv run --no-sync python --version` 返回 **Python 3.13.11**。

## 任务状态

| 任务 | 当前状态 | 验收重点 |
|---|---|---|
| B2 来源断言绑定 | 绑定核心与诊断通过独立复核；业务硬门尚未闭合 | 来源存在、语义支持、时效分别判定；缺证据保持 UNKNOWN |
| B3 两段初判 | 候选已实现；独立规格与质量复核通过 | 初判输入不含 L3 先验；冻结候选开关；成功初判不随决策重试 |
| C2 局部恢复 | 接线梳理中 | 单票故障隔离；必需研究缺口仍阻断完整发布 |
| C3 attempt 隔离 | 待实施 | 首次尝试也使用私有输出；接受字节不可被迟到写入污染 |
| C4 输入边界 | 待实施 | 任务级读取清单与宿主实际能力一致 |
| C5 调度 | 待实施 | 每票就绪即可复核；状态提交保持串行 |
| D1 阶段评价 | 评价协议已落文；未启动实验 | 使用现有评价接口，真实版本与输入在结果前冻结 |

## 验证记录

本批验证覆盖软件契约与回放，不声明真实宿主验收通过。

- B2 开发验证：首组 6 个反例先失败后通过；来源、断言、报告、回放与分层组 **265 passed，4.76s**。独立复核进行中。
- B2 复核修复：异常/坏 frame 丢分母、合法空引用 UNKNOWN 误阻断完整性、重试重复计数三项均补反例；扩大组 **307 passed，4.71s**。诊断通过已有 `IntelStatus.note` 传入 L4；独立定点重审 **96 passed，0.85s**，未发现新的必须修复项。
- B3 纯契约：缺失接口先失败，实现后与完整 ResearchCard 组 **112 passed，0.50s**。支持 A 股、美股、指数、期货和外汇身份词法，但这不构成全部市场的执行日历或 rubric 验收。
- B3 版本：begin request v2 显式选择 `card_research_profile`；v1 保持旧字段；候选仅适用于 scan 或 stock LITE。冻结、往返、历史模式、重复初始化冲突等测试完成；连同契约与恢复组 **82 passed，1.03s**。
- 恢复窄修：已成功 a2 的卡片再次恢复时，校验原已接受投影后幂等返回；不覆盖投影的后续改写。反例先失败，修复后 L4 恢复组 **7 passed，0.60s**，独立规格与代码审查通过。该修复不等于 C3 的统一输出隔离已完成。
- 根会话集成检查：news 全组、source lineage/receipts/replay、intel status/lint、文档预算、配置标准与模块分层共 **487 passed，10.57s**。这是软件契约回归，不是实际宿主或投资效果验收。
- B3 独立规格审查：**125 项定向通过**，另构造 NVDA 最终改判提交验证通过。审查发现真实 ReplayUnit 缺少任务对象的 subject/output_artifact_ids，已从冻结 operation_request/expected_outputs 恢复；新增真实入口反例先失败后通过，修复组 **33 passed**，规格复核通过。
- 扩大集成：session_agent、contracts、forensics、news、trace 与 intel/config/docs 组得到 **2291 passed、7 skipped、4 failed，405.27s**。跳过源于缺历史产物或平台文件路径能力；失败为新增来源读取路径未登记、stock.news 旧夹具缺 web_fetch、verification 目录已存在、l4-card 文档超预算。
- 上述登记与夹具修复后对应组 **64 passed，5.02s**；l4-card 文档压缩至 **19,935 bytes / 20,000**，文档预算与角色定义组 **47 passed**。没有调高预算或放松宿主能力门。
- B3 独立代码质量复核 **PASS**，定向 **70 passed，1.48s**。复核中的第三次重试疑点已排除：既有 `MAX_ATTEMPTS=2` 使该路径不可达；本批未扩大重试上限。
- 最终修复后集成：双阶段、profile、stock/scan 恢复、独立上下文、扫描/单股回放、host/report 取证、登记契约、完整 ResearchCard、角色与文档预算、news、intel、配置和分层，共 **695 passed，30.65s**。该组覆盖了前次四项失败；未再次运行完整的 2302 项扩大组。

### 文档与入口修正

- 新增 [阶段评价协议](2026-09-30-agent-stage-evaluation-protocol.md)，列明现有接口、人口与主尺、冻结时序、成本/概率口径和上线条件。协议未预注册，也未执行实验；ensemble、B3 和 E6 消融的具体比较适配仍待实验接线。
- `CLAUDE.md` 的档案首覆入口改指既有 `session_v1 dossier-init/INIT` 和请求示例；`AGENTS.md` 项目技能数量修正为实际四个。未据此标记整个 D4 完成。

### B2 的证据边界

宿主工具只提供确定的抓取完成时间时，`published_at` 与 `first_available_at` 保留空值；收到时间不代替公开时间。语义 PASS 需要确定性适配器的结构字段或具名人工复核；本批不包含所有数据供应商的字段与公开时间映射。v1 sidecar 的 `ok` 保持原来的证据完整性含义，不改称语义通过。

v2 合法缺证 UNKNOWN 保持当前分母，不当作文件损坏；已声明引用的丢失或篡改仍令完整性失败。历史侧车保留，当前覆盖按 run/claim_id 聚合；不能确定当前语义版本时展示 UNKNOWN 与版本歧义，不按文件修改时间猜最新。关键断言与评级/入场之间的确定性业务硬门尚未闭合，不能据本批绑定和诊断接线声称已完成该门。

下一步需要版本化“claim → 评分维度/三门/入场条件”的用途与必要性映射，并让卡片文本解析实际填充 theses/evidence_refs；这些字段目前不能承担完整业务绑定。机器应从本 run 的冻结结果读取支持状态，而非信任模型自报。背景 UNKNOWN 仅披露；唯一必要论据 UNKNOWN 保持对应判断待核；FAIL 撤销该主张的论据资格后重算 rubric。PASS 允许使用事实，不自动使门通过；不能因任意背景事实缺证而统一降为 Hold。

## 保持的边界

`session_v1` 继续 PILOT。代码和合成测试不替代双宿主 REAL_SESSION 证明；本批没有投资收益改善结论。两段初判是有新增推理成本的候选，未经既定评价不自动切换默认研究图。

### 后续恢复改造顺序

C3 的只读设计已收敛，但本批未实施：首次和重试输出均按完整 task ID 与 SESSION attempt 隔离；将多输出快照描述与任务成功状态在 `tasks.json` 一次原子提交，receipt 与固定路径投影仅为可恢复派生物；下游只消费已提交快照。新布局需冻结版本并兼容历史 replay，不能原位升级旧 run。该改造完成前不扩大复核自动重试，以免放大迟到写入风险。

当前 runner 已可继续执行无依赖的 READY 任务；C2/C5 的剩余工作集中于复核重试、逐票复核依赖和确定性状态提交，不能简单删除现有屏障或并发锁。

## 交付与审计

代码通过第二批基线同步器从隔离工作树复制到主工作区；每个目标文件须仍匹配保全基线或本批交付哈希，冲突即停止。没有 Git 提交、清理或重置用户已有修改。

审计目录 `context_codex/development/20260930-agent-skills-batch2/` 保存 `baseline.tar`、基线清单、`implementation.patch`、`implementation-manifest.json`、扩大组 `integration.log` 与最终组 `final-targeted.log`。实际复制状态和逐文件哈希以 implementation manifest 为准；主工作区同步后的检查另记该目录。

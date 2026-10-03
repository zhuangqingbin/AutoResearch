# Session Agent 验收状态

更新日期：2026-10-01。五类计划、局部重试、attempt 输出隔离、任务访问边界与逐票复核的软件交付记录见 [C5 开发记录](../research/2026-09-30-agent-skills-batch6-readout.md)。C6 计量与验收状态接口的本轮进度见 [第七批开发记录](../research/2026-09-30-agent-skills-batch7-readout.md)。

## 当前结论

| 宿主 | 自动化协议/领域回归 | 真实订阅会话矩阵 | 上线状态 |
|---|---|---|---|
| Codex | 分批软件结果见开发记录 | INCOMPLETE：工作流 1/14（新 stock LITE 早停已重验）；当前政策所选边界 proof 1/11，见 [本轮开发记录](../research/2026-10-01-real-host-acceptance-development-readout.md) | `session_v1` 显式 PILOT |
| Claude Code | 共享代码测试可用 | INCOMPLETE：共享文档记录原政策工作流 1/14（stock LITE 早停）与边界 11/11，见 [Claude 真实宿主记录](../research/2026-10-01-claude-real-host-acceptance-readout.md) | `session_v1` 显式 PILOT |

本轮开发改变了 boundary policy 指纹。历史边界数量不能直接作为新政策验收；Codex 旧 2/11 保留作 STALE 历史，当前所选 deep_after 为 1/11，完整门仍 PENDING。新真实 run `20261001T134913840836Z` 的 canonical full 核验与离线 replay 已通过，REAL_SESSION proof 已归档。Codex 接收目录指定 records.json 的当前工作流索引实际为 1/28，未导入的其他宿主记录不计入该索引。开发、阻塞与新回归记录见 [本轮开发 readout](../research/2026-10-01-real-host-acceptance-development-readout.md)。

自动化测试和合成 handle 证明契约、状态机与领域接线，不证明真实模型派发、联网、独立上下文或 token 计量。Codex 不读取 `context_claude/`、`reports_claude/`，也不代填 Claude 结果。

当前未闭合事项的开发顺序、宿主证据依赖、11 项边界演练及默认入口分母见 [真实宿主验收剩余开发计划](../superpowers/plans/2026-10-01-real-host-acceptance-remaining-development-plan.md)。该计划不改变本页的机器验收状态。

文中的 `6318 passed, 13 skipped` 与 `209 passed` 是上一迁移基线，不作为本轮法证修复的
测试声明。本轮最终数字只在实际命令完成后写入；软件绿灯仍不等于真实宿主运行通过。

2026-09-14 历史软件结果：离线/故障矩阵 `1477 passed, 7 skipped, 4 subtests passed`；全仓
`6557 passed, 12 skipped, 5 warnings, 4 subtests passed`；目标 Ruff 与 compileall 均通过。
命令、代码摘要、skip 和隔离边界见 [synthetic-acceptance-2026-09-14.md](synthetic-acceptance-2026-09-14.md)。

## 调用者清点

四个项目 skill、`AGENTS.md`、`CLAUDE.md` 和 `README.md` 已登记 `session_v1` 命令入口。三个原 Workflow 保留 `LEGACY_ORCHESTRATION_FALLBACK` 历史标记；C4 已要求任务身份绑定，旧入口当前会在研究启动前返回 `HOST_CAPABILITY_REQUIRED`。仓内其余旧 Workflow 引用属于历史计划、兼容测试或操作记录，不是新的 session_agent 生产调用者。旧入口的最终删除以对应入口在两个宿主均取得真实 PASS 为门槛。

## 真实验收矩阵

每个宿主分别记录单股 LITE 早停/满卡、单股 FULL、宏观 FULL/LITE、行业 FULL/LITE、档案 INIT、扫描 FULL、三个 sentinel/forced 模式、中断恢复、独立复核、缺证据/额度不足。PASS 必须有本宿主真实 run_id 和 REAL_SESSION 证据；合成分支只能标 SYNTHETIC，缺项为 INCOMPLETE。

机器记录由 `contracts.forensic.validate_acceptance_record` 做严格字段校验；没有 `status=PASS`
捷径。portable proof 同时绑定报告核验、完整 ReplayPlan/ReplayResult、代码树 hash、发布
bundle/receipt、ROOT 与 ExecutionOrigin。`evaluation.accept_workflow` 要求 Codex、Claude Code
在固定场景分母上全部有可解引用 proof，且真实研究访问边界门通过，才返回 `ENABLED`。当前仓库没有这组真实 proof，故五类
能力仍为 `INCOMPLETE`；这不是测试失败，而是尚未发生的外部验收事实。

对拍使用 Comparison v1：`schema_version、engine、workflow、mode、baseline_run_id、candidate_run_id、input_identity_equal、config_identity_equal、deterministic_diffs、research_diffs、missing_evidence、verdict`。只归一化 run_id、运行时刻和路径元信息；评级、数值、候选、来源和警告差异必须保留。输入/config 不同或有决定性差异为 FAIL；缺基线或计量为 INCOMPLETE。

## 性能结论

当前不声明固定 token 节省比例。可预期收益来自小输入、确定性步骤外移、早停和局部修复；控制 JSON、回执和任务切换也有成本。真实读数按 engine、workflow、mode、real/synthetic、cache coverage 分层；真实扫描少于 10 次时保持观察中。

## 放行步骤

1. 宿主在自身根显式 PILOT 完成矩阵并保存 run_id、host receipt、comparison 和计量记录；缺少实际能力时保留缺项。
2. 质量、恢复、门和证据先通过；性能不改善不阻断架构学习，但必须如实记录。
3. 单个入口两宿主固定场景 proof 与真实研究访问边界验收均满足 `accept_workflow` 后，才启用该入口默认；历史 Workflow 和 capsule 的只读兼容另行保留。

### 当前固定场景分母

| workflow | 每个宿主必须具备 |
|---|---|
| stock-research | A 股 FULL、美股 FULL、LITE 早停、LITE 满卡 |
| macro-research | FULL、LITE |
| sector-research | FULL、LITE 且真实 reuse |
| dossier-init | INIT、新 session 恢复 |
| scan-market | FULL、FORCED_FULL、SENTINEL_EMPTY、SENTINEL_PINNED；后三项允许明确标 DRILL |

单股其他市场在取得各自真实样本前仍属 PILOT 范围，不因 A 股/美股 proof 自动推广。

## C6 软件与真实验收的边界

`research_boundary_gate` 的当前结果以机器重算为准；缺新政策证据时不能通过，历史证据也可能明确返回 STALE/INVALID。C4 hook 配置、开发角色通过、单元测试拒绝案例、replay sandbox 的 `ENFORCED` 均不能证明研究角色在真实加载宿主中受到限制。真实加载证据验证器已实现；双宿主最终政策的场景 proof 尚待补齐，验收状态命令会保留这些缺项。

固定分母来自 `evaluation.required_acceptance_scenarios`：每宿主 14 个、双宿主共 28 个场景。迟到写入、局部重试、隔离拒绝等额外演练只能补充记录，不能抵扣必需场景。软件测试状态、真实会话状态和 `default_enabled` 分别展示；软件状态未知时不由命令自动填写 PASS。

每个报告仍须以 `finish` 机器返回的 canonical 报告路径和 run_id 调用 `verify-report --level full`。交付只能引用其 `report_covered`、`publication_ok`、`orchestration_verified`、`completeness_ok` 与 `missing`，`UNBOUND_REPORT` 不通过。

# Session Agent 验收状态

记录日期：2026-09-14。代码实现覆盖五类计划、扫描四模式、动态展开、L3 局部修复、L4 taskbook attempt、独立复核约束、恢复、发布、对拍与 usage 接缝。

## 当前结论

| 宿主 | 自动化协议/领域回归 | 真实订阅会话矩阵 | 上线状态 |
|---|---|---|---|
| Codex | PASS（本分支自动化） | INCOMPLETE：未在本文记录真实模型全矩阵 run_id | `session_v1` 显式试跑，legacy 保留 |
| Claude Code | 共享代码测试可用 | INCOMPLETE：必须由 Claude 会话在自身目录独立执行 | legacy 默认保留 |

自动化测试和合成 handle 证明契约、状态机与领域接线，不证明真实模型派发、联网、独立上下文或 token 计量。Codex 不读取 `context_claude/`、`reports_claude/`，也不代填 Claude 结果。

文中的 `6318 passed, 13 skipped` 与 `209 passed` 是上一迁移基线，不作为本轮法证修复的
测试声明。本轮最终数字只在实际命令完成后写入；软件绿灯仍不等于真实宿主运行通过。

本轮实际软件结果：离线/故障矩阵 `1477 passed, 7 skipped, 4 subtests passed`；全仓
`6557 passed, 12 skipped, 5 warnings, 4 subtests passed`；目标 Ruff 与 compileall 均通过。
命令、代码摘要、skip 和隔离边界见 [synthetic-acceptance-2026-09-14.md](synthetic-acceptance-2026-09-14.md)。

## 调用者清点

四个项目 skill、`AGENTS.md`、`CLAUDE.md` 和 `README.md` 已登记 `session_v1` 命令入口。三个原 Workflow 仅作为未通过真实双宿主矩阵时的 `LEGACY_ORCHESTRATION_FALLBACK` 保留；仓内其余旧 Workflow 引用属于历史计划、兼容测试或操作记录，不是新的 session_agent 生产调用者。旧入口的最终删除以对应入口在两个宿主均取得真实 PASS 为门槛。

## 真实验收矩阵

每个宿主分别记录单股 LITE 早停/满卡、单股 FULL、宏观 FULL/LITE、行业 FULL/LITE、档案 INIT、扫描 FULL、三个 sentinel/forced 模式、中断恢复、独立复核、缺证据/额度不足。PASS 必须有本宿主真实 run_id 和 REAL_SESSION 证据；合成分支只能标 SYNTHETIC，缺项为 INCOMPLETE。

机器记录由 `contracts.forensic.validate_acceptance_record` 做严格字段校验；没有 `status=PASS`
捷径。portable proof 同时绑定报告核验、完整 ReplayPlan/ReplayResult、代码树 hash、发布
bundle/receipt、ROOT 与 ExecutionOrigin。`evaluation.accept_workflow` 要求 Codex、Claude Code
在固定场景分母上全部有可解引用 proof 才返回 `ENABLED`。当前仓库没有这组真实 proof，故五类
能力仍为 `INCOMPLETE`；这不是测试失败，而是尚未发生的外部验收事实。

对拍使用 Comparison v1：`schema_version、engine、workflow、mode、baseline_run_id、candidate_run_id、input_identity_equal、config_identity_equal、deterministic_diffs、research_diffs、missing_evidence、verdict`。只归一化 run_id、运行时刻和路径元信息；评级、数值、候选、来源和警告差异必须保留。输入/config 不同或有决定性差异为 FAIL；缺基线或计量为 INCOMPLETE。

## 性能结论

当前不声明固定 token 节省比例。可预期收益来自小输入、确定性步骤外移、早停和局部修复；控制 JSON、回执和任务切换也有成本。真实读数按 engine、workflow、mode、real/synthetic、cache coverage 分层；真实扫描少于 10 次时保持观察中。

## 放行步骤

1. 宿主在自身根完成矩阵并保存 run_id、host receipt、comparison 和 efficiency 记录。
2. 质量、恢复、门和证据先通过；性能不改善不阻断架构学习，但必须如实记录。
3. 单个入口两宿主均 PASS 后，才移除该入口 legacy 默认；历史 Workflow 和 capsule 的只读兼容另行保留。

### 当前固定场景分母

| workflow | 每个宿主必须具备 |
|---|---|
| stock-research | A 股 FULL、美股 FULL、LITE 早停、LITE 满卡 |
| macro-research | FULL、LITE |
| sector-research | FULL、LITE 且真实 reuse |
| dossier-init | INIT、新 session 恢复 |
| scan-market | FULL、FORCED_FULL、SENTINEL_EMPTY、SENTINEL_PINNED；后三项允许明确标 DRILL |

单股其他市场在取得各自真实样本前仍属 PILOT 范围，不因 A 股/美股 proof 自动推广。

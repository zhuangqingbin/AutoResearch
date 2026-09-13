# Session Agent 验收状态

记录日期：2026-09-14。代码实现覆盖五类计划、扫描四模式、动态展开、L3 局部修复、L4 taskbook attempt、独立复核约束、恢复、发布、对拍与 usage 接缝。

## 当前结论

| 宿主 | 自动化协议/领域回归 | 真实订阅会话矩阵 | 上线状态 |
|---|---|---|---|
| Codex | PASS（本分支自动化） | INCOMPLETE：未在本文记录真实模型全矩阵 run_id | `session_v1` 显式试跑，legacy 保留 |
| Claude Code | 共享代码测试可用 | INCOMPLETE：必须由 Claude 会话在自身目录独立执行 | legacy 默认保留 |

自动化测试和合成 handle 证明契约、状态机与领域接线，不证明真实模型派发、联网、独立上下文或 token 计量。Codex 不读取 `context_claude/`、`reports_claude/`，也不代填 Claude 结果。

最终软件回归：全仓 `6318 passed, 13 skipped`；session_agent 专项 `209 passed`；迁移范围 Ruff、compileall、三个 legacy Workflow 的 `node --check` 均通过。跳过项均为缺少历史 gitignored 现场、可选 lightgbm 或文件系统能力，与迁移代码无关。

## 调用者清点

四个项目 skill、`AGENTS.md`、`CLAUDE.md` 和 `README.md` 已登记 `session_v1` 命令入口。三个原 Workflow 仅作为未通过真实双宿主矩阵时的 `LEGACY_ORCHESTRATION_FALLBACK` 保留；仓内其余旧 Workflow 引用属于历史计划、兼容测试或操作记录，不是新的 session_agent 生产调用者。旧入口的最终删除以对应入口在两个宿主均取得真实 PASS 为门槛。

## 真实验收矩阵

每个宿主分别记录单股 LITE 早停/满卡、单股 FULL、宏观 FULL/LITE、行业 FULL/LITE、档案 INIT、扫描 FULL、三个 sentinel/forced 模式、中断恢复、独立复核、缺证据/额度不足。PASS 必须有本宿主真实 run_id 和 REAL_SESSION 证据；合成分支只能标 SYNTHETIC，缺项为 INCOMPLETE。

对拍使用 Comparison v1：`schema_version、engine、workflow、mode、baseline_run_id、candidate_run_id、input_identity_equal、config_identity_equal、deterministic_diffs、research_diffs、missing_evidence、verdict`。只归一化 run_id、运行时刻和路径元信息；评级、数值、候选、来源和警告差异必须保留。输入/config 不同或有决定性差异为 FAIL；缺基线或计量为 INCOMPLETE。

## 性能结论

当前不声明固定 token 节省比例。可预期收益来自小输入、确定性步骤外移、早停和局部修复；控制 JSON、回执和任务切换也有成本。真实读数按 engine、workflow、mode、real/synthetic、cache coverage 分层；真实扫描少于 10 次时保持观察中。

## 放行步骤

1. 宿主在自身根完成矩阵并保存 run_id、host receipt、comparison 和 efficiency 记录。
2. 质量、恢复、门和证据先通过；性能不改善不阻断架构学习，但必须如实记录。
3. 单个入口两宿主均 PASS 后，才移除该入口 legacy 默认；历史 Workflow 和 capsule 的只读兼容另行保留。

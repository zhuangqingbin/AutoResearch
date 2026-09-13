# Session Agent 运维手册

以下命令均要求先设置 `AUTORESEARCH_ENGINE=codex|claude`，并且只能操作该引擎的 active run。

## 正常循环

```bash
uv run --no-sync python -m autoresearch.session_agent status --run-id <RUN_ID>
uv run --no-sync python -m autoresearch.session_agent next --run-id <RUN_ID>
uv run --no-sync python -m autoresearch.session_agent claim --run-id <RUN_ID> --task-id <TASK> --expected-attempt 1
uv run --no-sync python -m autoresearch.session_agent execute --run-id <RUN_ID> --task-id <TASK> --attempt 1 --params-file <PARAMS.json>
uv run --no-sync python -m autoresearch.session_agent submit --run-id <RUN_ID> --submission-file <SUBMISSION.json>
uv run --no-sync python -m autoresearch.session_agent finish --run-id <RUN_ID>
```

`READY` 表示至少有一个 PENDING 节点依赖已满足；`WAITING` 表示仍有运行中任务或待展开模板；`BLOCKED` 会列出阻断节点；`DONE` 只表示任务图完整，仍需 `finish` 发布。

## 失败与恢复

宿主明确观察到推理调用失败时，记录真实错误：

```bash
uv run --no-sync python -m autoresearch.session_agent fail \
  --run-id <RUN_ID> --task-id <TASK> --attempt 1 \
  --error-class TIMEOUT --message "host response ended before output"
```

普通 SESSION 任务只有符合策略的 FAILED 才能用下一 attempt 再 claim；BLOCKED 必须排除数据、身份或能力问题。扫描 L4 的 TIMEOUT、CONNECTION、RATE_LIMIT、STALE_TASK 使用整票恢复：

```bash
uv run --no-sync python -m autoresearch.session_agent retry-l4 \
  --run-id <RUN_ID> --code 600519 --expected-attempt 2
```

该命令只冻结 `a2` 子树，不直接认领；随后按 `next/claim` 继续。错误类型仍使用 `TASK_ATTEMPT`，不会把 intel 的 `ENOTFOUND` 搜索预算混入整票次数。

中断后运行：

```bash
uv run --no-sync python -m autoresearch.session_agent resume --run-id <RUN_ID>
```

resume 会补齐已接受 receipt、重放确定性成功后的合法 expansion，并检查捕获进程。匹配进程仍活着时等待；没有终态证据时不启动副本。旧 attempt 的迟到提交继续按身份冲突拒绝。

## 常见故障

| 现象 | 处理 |
|---|---|
| `EXPLICIT_ENGINE_REQUIRED` | 在新 shell 显式 export 当前宿主引擎 |
| `READY` 但 claim 失败 | 重新 status；核对 task_id、expected_attempt 和 session_ref |
| `HOST_CAPABILITY_REQUIRED` | 在同引擎真实具备该能力的会话完成任务并附 receipt |
| `DOMAIN_VALIDATION_FAILED` | 修正登记输出；不要改 validator 或补造字段过门 |
| `RETRYABLE_TOOL_FAILURE` | 先 resume 检查进程身份，再按同一 attempt 的恢复结果处理 |
| artifact changed / symlink | 停止 run，保存现场；不要重新绑定已消费输入 |
| L3 局部修复断连/应用失败 | `fail` 或 `execute` 写 `DEGRADED`，保留原 judged，将可选修复支路标为 `SUPERSEDED` 后继续 GATE2 |
| 额度不足或证据缺失 | 保持 WAITING/BLOCKED 或明确 degraded；不能填“无风险” |
| GATE4 失败 | 修复上游真实产物后走合法新 attempt；不得直接 finish |

## 发布中断

L4 重试最多到 attempt 2。第二次卡通过后，服务核对首轮卡的旧 hash/inode 和 `WAITING_RETRY` 状态，再原子晋升到原领域路径；旧绑定已变化、任务仍在运行或次数超限都会拒绝。

发布器在 run 内先生成候选 bundle 和文件 hash，再在锁内替换目标目录。同一 bundle 重试幂等，不同内容遇到同一目标会冲突。若发布完成但 finalize 中断，保留 candidate、target 和 capsule，使用只读校验确认目录 hash 后再恢复 finalize，不能重新运行研究。

## 回滚

未通过真实宿主验收的入口继续走 `LEGACY_ORCHESTRATION_FALLBACK`。切回 legacy 只影响新 run：已有 `session_v1` run 的冻结计划不能交给旧 Workflow 从中间接管，可继续按原计划完成或冻结为 INTERRUPTED。历史 `context_*`、`reports_*`、capsule 和 usage 不移动、不改归因。

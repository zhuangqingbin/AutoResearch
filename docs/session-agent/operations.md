# Session Agent 运维手册

以下命令均要求先设置 `AUTORESEARCH_ENGINE=codex|claude`，并且只能操作该引擎的 active run。

## 正常循环

```bash
uv run --no-sync python -m autoresearch.session_agent status --run-id <RUN_ID>
uv run --no-sync python -m autoresearch.session_agent next --run-id <RUN_ID>
uv run --no-sync python -m autoresearch.session_agent claim --run-id <RUN_ID> --task-id <TASK> --expected-attempt 1
uv run --no-sync python -m autoresearch.session_agent execute --run-id <RUN_ID> --task-id <TASK> --attempt 1 --params-file <PARAMS.json>
uv run --no-sync python -m autoresearch.session_agent bind-host-evidence --run-id <RUN_ID> --task-id <TASK> --attempt 1 --transcript-file <TRANSCRIPT.jsonl> --session-ref <SESSION> --context-ref <CONTEXT> --start-ordinal <N> --end-ordinal <N> --context-source MAIN
uv run --no-sync python -m autoresearch.session_agent calculate --run-id <RUN_ID> --task-id <RUNNING_INFERENCE_TASK> --attempt 1 --params-file <CALCULATION.json>
uv run --no-sync python -m autoresearch.session_agent submit --run-id <RUN_ID> --submission-file <SUBMISSION.json>
uv run --no-sync python -m autoresearch.session_agent finish --run-id <RUN_ID>
```

`READY` 表示至少有一个 PENDING 节点依赖已满足；`WAITING` 表示仍有运行中任务或待展开模板；`BLOCKED` 会列出阻断节点；`DONE` 只表示任务图完整，仍需 `finish` 发布。

## 候选预检与最终封存

layout v2 的当前 RUNNING inference attempt 可先使用根所有的预检入口：

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.session_agent precheck \
  --run-id <RUN_ID> --submission-file <SUBMISSION.json>
# 研究角色完成修订后，再绑定最终 transcript 区段并制作匹配的 host receipt。
uv run --no-sync python -m autoresearch.session_agent precheck \
  --run-id <RUN_ID> --submission-file <SUBMISSION_WITH_RECEIPT_ID.json> \
  --host-receipt-file <HOST_RECEIPT.json>
uv run --no-sync python -m autoresearch.session_agent submit \
  --run-id <RUN_ID> --submission-file <SUBMISSION_WITH_RECEIPT_ID.json> \
  --host-receipt-file <HOST_RECEIPT.json>
```

不要用命令退出码替代预检结论。读取机器结果中的 `result`：`domain_status` 为 PASS/FAIL；`host_evidence_status` 为 VERIFIED、PENDING_FINAL_BINDING 或 INVALID；错误按 `domain:` / `host evidence:` 区分。只有领域通过且匹配 receipt 及适用的 deep 实读均已核验时，`can_submit` 才为 true。早停仅免除不适用的 deep DD，仍须真实宿主证据。缺 receipt 保持 pending；损坏归档、身份不符或最终绑定缺完整 deep 读取为 INVALID。

`candidate_sha256` 单输出时是根安全捕获的真实文件 SHA256；多输出时是 `{artifact_id: actual_sha256}` 的 canonical JSON（UTF-8，无末尾换行）摘要。预检把副本留在 `session_outputs/precheck/`，可以保留按卡 hash 寻址的候选断言审计，但不更新 task、registry、accepted outputs、host binding、接受回执或 publication。无法安全捕获、坏请求、终态/过期 attempt 和 layout v1 直接返回错误，不伪造候选摘要。

正确顺序是 **预检 → 修订 → 最终 transcript 封存 → 携 receipt 预检 → submit**。已有 transcript binding 不能延长或替换，预检不会重写它。`can_submit=true` 只描述本次候选快照，正式 submit 必须再次捕获并核对字节；预检后修改输出时旧 submission 将失败，不能把预检当作接受凭证。

## 交付核验

`finish` 成功后只使用其返回的 canonical 路径，不从日期目录或“最新文件”猜版本：

```bash
uv run --no-sync python -m autoresearch.session_agent verify-report \
  --report-path <CANONICAL_REPORT_PATH> \
  --expected-run-id <RUN_ID> --level full
```

该命令不写原报告或 capsule。`report_covered=false`/`UNBOUND_REPORT` 表示这些字节没有发布身份；
`integrity_ok`、`publication_ok`、`orchestration_verified`、`completeness_ok` 分别回答不同问题，
不能用其中一个替代其余项。真正执行离线计算另走 replay，verify 的 `compute_status` 只描述
证据闭包重算，不表示模型重新推理。

`bind-host-evidence` 必须在对应推理 task 仍为 RUNNING 时执行，并给出导出 transcript 的
精确 ordinal 区段。命令返回 `host-binding:<sha256>`；把它放入 host receipt 的
`evidence_refs`。独立复核使用 `--context-source SUBAGENT`，且必须提供不同的
`--context-ref` 与 `--parent-context-ref`。绑定时立即归档该前缀，因此后续会话追加内容不会
改变本 task 的证据字节。

`calculate` 只接受父推理任务已冻结的输入 artifact。财务期间、币种、单位、股数口径、AH
报价时点、基率样本窗/重叠政策和 DCF 网格有任一不可比时，计算 artifact 记录 FAILED 和原因；
父推理任务仍保持 RUNNING，宿主可修正参数后再次调用。所有成功或失败结果都绑定父
`task_id/attempt`、输入 hash 与 calculator code hash。

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

C4 下，旧 `LEGACY_ORCHESTRATION_FALLBACK` 入口缺少任务身份绑定，会在研究启动前返回 `HOST_CAPABILITY_REQUIRED`。未通过真实验收的入口只能显式 PILOT 并满足能力门。未来恢复 legacy 的能力后，切换也只影响新 run：已有 `session_v1` run 的冻结计划不能交给旧 Workflow 从中间接管，可继续按原计划完成或冻结为 INTERRUPTED。历史 `context_*`、`reports_*`、capsule 和 usage 不移动、不改归因。

## 默认放行 proof

`autoresearch.session_agent.evaluation` 提供严格 `AcceptanceRecord` 门。proof 默认存放在本引擎
`reports_<engine>/_acceptance/proofs/<engine>/<workflow>/<run_id>/<scenario>.json`；跨宿主只可把
对方明确导出的 portable proof 导入当前引擎审计根，不读取对方 context/reports 原目录。
`write_acceptance_proof` 会校验并绑定 verification、replay plan/result、publication
bundle/receipt 与 execution origin；`accept_workflow` 再按固定双宿主场景分母复核。缺文件、hash
冲突、非 session_v1、非 FULL/ENFORCED、合成证据或缺任一宿主场景都保持 `INCOMPLETE`。


### C6 计量与验收状态

计量使用本次推理 attempt 的冻结 dispatch 与绑定 transcript，分别展示 requested、resolved、observed model/effort。observed 缺失保留未知；配置值不能冒充宿主实际值。token、缓存、模型调用次数与派发次数各自说明覆盖；估算价格及代理输入量独立于实测 token。

只读查看本引擎 run 的计量：

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.session_agent metering --run-id <RUN_ID>
```

最终证据物化阶段将 sidecar 写入 `capsule/agents/session/metering.json`。读命令按冻结证据派生结果，已封存 capsule 不因查询而改写。字段和最终验证结果见 [第七批开发记录](../research/2026-09-30-agent-skills-batch7-readout.md)。

查看固定场景缺项，或检查本引擎审计根中已经导入的记录与 portable proof：

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.session_agent acceptance-status
uv run --no-sync python -m autoresearch.session_agent acceptance-status --records-file reports_codex/_acceptance/records.json --evidence-root reports_codex/_acceptance/proofs
```

`records.json` 必须来自实际验收记录；没有该文件时先用无参数命令查看缺项。Claude 明确导出的 portable proof 可放在 Codex 审计根的 `proofs/claude/` 子树；这不授权读取实际 `context_claude/` 或 `reports_claude/`。路径、身份、hash、重复场景或 DRILL 不合法都会保留失败信息，不能抵扣必需场景。

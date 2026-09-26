# Subscription Session Agent 使用指南

`session_v1` 把本项目的五类研究统一成可恢复任务图，同时继续在 Codex 或 Claude Code 的官方订阅会话里完成模型推理。Python 只负责数据、计划、任务所有权、校验、证据和发布，不调用 OpenAI、Anthropic 或其他模型 API，也不读取订阅凭据。

## 支持的入口

| 用户意图 | kind / mode | 请求样例 |
|---|---|---|
| 全 A 股扫描 | `scan-market / AUTO` | `examples/scan.request.json` |
| 单股研究或快速卡 | `stock-research / FULL|LITE` | `examples/stock.request.json` |
| 全球宏观或市场研判 | `macro-research / FULL|LITE` | `examples/macro.request.json` |
| 单行业研究或地形段 | `sector-research / FULL|LITE` | `examples/sector.request.json` |
| 首覆档案内部任务 | `dossier-init / INIT` | `examples/dossier.request.json` |

样例中的 `session_ref`、能力和 `evidence_refs` 必须替换成本次会话实际观测值。能力未知写 `null` 或 `false`，不能从旧配置推断为可用。

## 控制环(四个用户 skill 共用;2026-09-26 起只在此处讲一遍,SKILL.md 只留指针)

开发/验收期显式选择新编排时使用 `python -m autoresearch.session_agent begin --orchestration session_v1 --request-file <request.json>`,宿主循环为 `begin → next → claim → execute/宿主研究 → submit → finish`。冻结计划、artifact、attempt、回执和发布由 Python 验证,推理仍发生在订阅会话。宿主能力不足会在创建 run 前返回 `HOST_CAPABILITY_REQUIRED`;`session_agent --orchestration legacy` 只返回 `LEGACY_ENTRYPOINT_REQUIRED`,绝不代跑旧 Workflow——确需回退必须显式进入标为 `LEGACY_ORCHESTRATION_FALLBACK` 的旧入口并记录原因(`--legacy-reason`),不能给旧执行贴 `session_v1` 标签。当前双宿主真实验收为 `INCOMPLETE`,新入口仅作显式 PILOT,默认仍保留 legacy fallback;合成重放通过不等于真实宿主放行。`finish` 后必须对机器返回的 canonical 报告路径运行 `uv run --no-sync python -m autoresearch.session_agent verify-report --report-path <PATH> --expected-run-id <RUN_ID> --level full`,按结果分别声明编排、发布、完整性与重放;未绑定改写返回 `UNBOUND_REPORT`,不得借同一 run_id 或旧 ROOT 归因。

## Codex 与 Claude Code 启动

每个新 shell 第一条命令都要固定引擎：

```bash
export AUTORESEARCH_ENGINE=codex
# Claude Code 会话改成：export AUTORESEARCH_ENGINE=claude
```

Codex 只读写 `context_codex/`、`reports_codex/`；Claude 只读写 `context_claude/`、`reports_claude/`。两边只共享确定性数据湖 `lake/`。

以单股样例启动：

```bash
uv run --no-sync python -m autoresearch.session_agent begin \
  --orchestration session_v1 \
  --request-file docs/session-agent/examples/stock.request.json \
  --kind stock-research --mode LITE --date 2026-09-14 --subject 600519.SS
```

保存返回的 `run_id`，然后重复以下循环：

1. `next --run-id <RUN_ID>` 读取可运行任务，不发生认领。
2. `claim --run-id <RUN_ID> --task-id <TASK> --expected-attempt 1` 冻结本次输入并取得所有权。
3. DETERMINISTIC 任务用 `execute --params-file <JSON>`；INFERENCE 任务由当前宿主读取 claim 返回的角色说明和登记 artifact，写入指定输出后用 `submit` 回交。
4. 推理完成后先用 `bind-host-evidence` 把真实 transcript 区段绑定到
   `task_id/attempt/session/context`，再提交输出。需要独立上下文的复核必须附引用该绑定的
   真实 `host_receipt`；同一主会话换角色名不算独立。
5. `next` 返回 `DONE` 后执行 `finish`。五类能力统一执行可恢复的
   `seal → promote → state views → capsule finalize → commit receipt`；canonical 目录与
   hash-chain 收据是发布真值，日期旧路径只是在提交后生成的兼容视图。
6. 从 `finish.result.publication.canonical_path` 取得真实路径并做只读核验：

```bash
uv run --no-sync python -m autoresearch.session_agent verify-report \
  --report-path <CANONICAL_REPORT_PATH> --expected-run-id <RUN_ID> --level full
```

`integrity` 重算报告、MANIFEST、ROOT 与发布回执；`full` 还会重新计算 EvidencePlan 的任务
证据闭包，并把旧存量结论与重算差异列入 `diffs`。命令按报告实际字节绑定身份；同 run_id 的
未封存改写返回 `UNBOUND_REPORT`，不会借旧 capsule 放行。

完整命令与恢复流程见 [operations.md](operations.md)，对象和依赖关系见 [architecture.md](architecture.md)。

## 为什么可能省 token

收益来自更小且冻结的任务输入、确定性步骤不进模型上下文、LITE 早停、L3 只修失败行、同档复核早止，以及状态轮询不要求模型复述历史。任务图本身会增加少量 JSON、回执和控制提示，因此简单单股任务未必省 token。项目只按真实 transcript 和 usage 记录比较；样本不足时保持“观察中”，不承诺固定百分比。

## 当前切换状态

协议、五类计划、扫描四模式、恢复、发布和离线对拍已有自动化覆盖。`begin` 会在创建 run 前检查
`deterministic_exec`、`capture_binding` 和 `inference_handoff`，缺能力返回
`HOST_CAPABILITY_REQUIRED`。真实 Codex/Claude 宿主仍须分别完成 [acceptance.md](acceptance.md)
的运行矩阵；未验收场景只能显式进入标为 `LEGACY_ORCHESTRATION_FALLBACK` 的旧 Workflow 并记录
原因。`session_agent --orchestration legacy` 返回 `LEGACY_ENTRYPOINT_REQUIRED`，不会静默代跑。
旧 run 和历史 capsule 保持只读兼容。

默认切换由 `evaluation.accept_workflow(records)` 的机器门控制。每个 workflow 的 Codex 与
Claude Code 必须分别覆盖登记场景，记录必须是 `REAL_SESSION`（仅扫描声明的控制场景允许
`REAL_SESSION_DRILL`），且 portable proof 能同时解引用 VerificationResult、ReplayPlan、
ReplayResult、bundle、receipt 和 ExecutionOrigin。`SYNTHETIC`、字符串 `PASS`、任意 run_id
或缺 proof 都只得到 `INCOMPLETE`。当前没有完整双宿主 proof，因此五类默认均未切换。

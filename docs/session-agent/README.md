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

## 扫描 runner + mailbox 宿主循环(host 模式,PILOT,opt-in)

上面的环由 runner 自动转圈:确定性任务(frame / prelude / GATE1 / 行业 pack / L3 prepare+lint+merge /
GATE2 / L4 prep+任务簿+slim+intel 状态 / 复核决策 / finalize / assemble / GATE4 / usage / observe)在
runner 进程内经 `service.execute` 跑(`exec_capture` 留痕,零 agent、零 general-purpose 壳);只有 7 种
判断角色(macro-brief、sector-brief、l3-rank(+repair)、l4-intel、l4-card、l4-card 复核)交给宿主会话。
**默认入口仍是 scan-market SKILL 的 legacy Workflow**;本循环在真实验收(批 2–3 Task 6)通过前只作显式试跑。
已知缺口(单票终失败会让整场停在 BLOCKED/STALLED,legacy 则降级为盲卡)见
`docs/research/2026-09-26-session-plan-vs-workflow-audit.md`。

1. **begin**:同上 `begin --orchestration session_v1 --kind scan-market --mode AUTO`,请求用
   `examples/scan.request.json`;`host_profile.session_ref` 必须是本会话真实 session id(transcript 定位与
   计量都靠它),且如实声明 `independent_context/web_search/web_fetch=true` 并附证据(复核要独立上下文,
   intel 要联网)。
2. **起 runner(后台,脱离壳进程树)**:

   ```bash
   uv run --no-sync python -m autoresearch.trace.detach --run-id "$RUN_ID" --key session-runner \
     --wait-seconds 5 --shell "AUTORESEARCH_ENGINE=claude uv run --no-sync python -m autoresearch.session_agent run --run-id $RUN_ID --executor mailbox --max-parallel 8"
   ```

   即 `session_agent run --executor mailbox`;**永远显式给 `--max-parallel`**(host 模式推荐 8;缺省会取冻结配置的
   `budgets.concurrency.l4_stock`,生产是 64);超帽任务留在 READY、不认领。
   runner 重启后对已认领未提交的 attempt **重新挂接同一请求**,不重复派发。
3. **宿主循环**(重复直到 `RUNNER_EXITED`):

   ```bash
   uv run --no-sync python -m autoresearch.session_agent mailbox wait --run-id "$RUN_ID" --timeout 90
   ```

   `--timeout` 不超过 100 s(宿主 Bash 默认 120 s 上限),IDLE 就再调;不要一次长等。

   - `kind=REQUEST`:原样执行 `Agent(subagent_type=<agent_type>, prompt=<prompt>)`(请求带 `model` 时一并传)。
     model/effort 已由 runner 经 `resolve_agent_bundle` 解释并写在请求里(`model`/`effort`/`agent_spec`),
     不要改 prompt、不要另加指令。注意:Claude Code 的 `Agent` 工具不收 effort,host 模式下生效的是
     agent 定义 frontmatter 的 effort(legacy Workflow 显式传配置值;两者今天有差,见审计 R6);
     headless 执行器(批 4)用 `--effort` 透传。agent 返回后:

     ```bash
     uv run --no-sync python -m autoresearch.session_agent mailbox complete --run-id "$RUN_ID" \
       --task-id <task_id> --attempt <attempt> --context-ref <subagent 的 agentId>
     ```

     `--session-ref` / `--parent-context-ref` 缺省为本会话;transcript 自动推导为
     `<session>/subagents/agent-<agentId>.jsonl`(找不到就显式传 `--transcript-path`),runner 把它绑定进
     capsule —— 复核任务没有绑定会被判 `EVIDENCE_MISSING`。agent 报错时改传
     `--error "<原文>" [--error-class TIMEOUT|CONNECTION|RATE_LIMIT]`(瞬时类会被重试一次)。
   - **Codex 宿主**(`AUTORESEARCH_ENGINE=codex` 的 run):请求里的 `agent_type` 已是 Codex 项目 agent 的
     `.codex/agents/*.toml` **`name` 字段**(如 `L4 card`、`scan strategist`、`ensemble review`;Codex 按它派发,
     hook 里的 `agent_type` 也是它),用 `spawn_agent` 按该名字、干净上下文派发,不要换成 Claude 的
     `l4-card`;`--context-ref` 传子 agent id。transcript 自动推导只认 Claude 布局,Codex 必须显式传
     `--transcript-path <子 agent rollout jsonl>`。
   - **并行**:`wait` 一次只交出一个请求且每个请求只交一次;每领到一个就**后台**派出 Agent(不等它),
     继续 `wait`;哪个 Agent 先返回就立刻 `complete` 哪个 —— **不要攒一批再一起 complete**(整批等最慢的那个,
     快的也会被判超时)。超时从 `wait` 领取(`.taken`)起算,不从签发起算。
   - `complete` 返回 `kind=ABANDONED`:该 attempt 已超时被 runner 放弃,agent 的产出作废(结果只留作该
     attempt 的迟到证据),不要手动重试 —— runner 已按规则开了新 attempt 或停机。
   - `kind=IDLE`:再调 `wait`;`taken_unanswered` 列出已领未答的请求(会话重启后用 `--include-taken` 重新领)。
   - `kind=RUNNER_EXITED`:读 `runner.outcome`(`finished`、`stop_reason`、`finish.canonical_path`)。
   - `kind=RUNNER_DEAD`:runner.json 仍写 RUNNING,但 pid 已不在或心跳超 6 拍(`reason` 说明)——按第 4 步
     用**新 detach key** 重启 runner(它会重新挂接未答请求),再继续 `wait`。
   - CP0–CP7 播报不变(素材路径同 SKILL;CP5 可读 `status --run-id` 的 `l4` 计数)。
4. **收尾**:`finished=true` 时对 `finish.canonical_path` 跑 `verify-report --level full`(上一节命令)。
   `stop_reason=BLOCKED|STALLED` 时 outcome 列出 `errors/orphans/skipped`(每个 orphan 带 `hint`:照做
   `fail --error-class STALE_TASK` 即可让重启后的 runner 重试一次)。**重启 = 第 2 步命令换一个新的
   `--key`**(如 `session-runner-2`、`-3`…):`trace/detach` 对同一 `(run_id, key)` 只等不重跑,用旧 key
   什么也不会发生。重试意图从任务表/任务簿的持久状态推导(不靠进程内存),重启后照常补足那一次重试;
   同一 run 同时只能有一个 runner(`_dispatch/runner.lock`),第二个会带着持锁 pid 直接拒绝启动。
   `REVIEW_FAILED:<类>`(复核超时/断连)不会重跑 intel+card,整场停在 BLOCKED。
   或换新 run_id 走 legacy Workflow(`LEGACY_ORCHESTRATION_FALLBACK`,记录原因)。runner 不替失败 run
   冻结 capsule,需要时显式 `python -m autoresearch.trace.capsule finalize <RUN_ID> --business-status FAILED`。

**邮箱协议**(`<staging>/_dispatch/`,已登记为 run 内产物;驱动器只认 result 文件):
`<task_id>.a<attempt>.request.json`(runner 写,`DispatchRequest`)· `.taken`(`wait` 排他领取)·
`.result.json`(`complete` 写)· `.abandoned`(runner 判超时时在同一把锁下写)· `runner.json`(RUNNING/EXITED +
outcome)· `ledger.jsonl`(每次结算一行)。
全部 tmp+rename 原子写,request/result 在 fcntl 锁下只写一次;runner 只读自己签发的那个 attempt 的
result 且正文 task/attempt 必须对得上。超时(按角色,macro 15m / sector 10m / L3 40m / repair 10m /
intel 15m / card·复核 30m,**从 `.taken` 起算**;没人领的请求 4× 后才超时)记 `TIMEOUT`、写 `.abandoned`
(`wait` 不再交出、`complete` 回 `ABANDONED`),以**新 attempt** 重试一次,旧 attempt 迟到的结果永远不被接收
(但超时的 subagent 不会被杀,别手动留着它继续写)。执行器协议(批 4 headless 复用)见
`autoresearch/session_agent/executors/base.py`。

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

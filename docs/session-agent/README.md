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

## C4 兼容限制与前置检查

旧 Workflow 缺少任务级访问绑定，当前在研究启动前返回 `HOST_CAPABILITY_REQUIRED`。新入口仍为显式 PILOT，默认切换门尚未满足。先在仓库根固定引擎并运行 `uv run --no-sync python -m autoresearch.session_agent.task_access preflight --orchestration session_v1`；`CONFIGURED_UNVERIFIED` 只表示配置可用。身份绑定、broker 与重载步骤见 [文件访问边界](access-boundary.md)。下文旧 fallback 说明是保留的协议语义，不能据此跳过 C4 能力门。

## 控制环(四个用户 skill 共用;2026-09-26 起只在此处讲一遍,SKILL.md 只留指针)

开发/验收期显式选择新编排时使用 `python -m autoresearch.session_agent begin --orchestration session_v1 --request-file <request.json>`,宿主循环为 `begin → next → claim → execute/宿主研究 → submit → finish`。冻结计划、artifact、attempt、回执和发布由 Python 验证,推理仍发生在订阅会话。宿主能力不足会在创建 run 前返回 `HOST_CAPABILITY_REQUIRED`;`session_agent --orchestration legacy` 只返回 `LEGACY_ENTRYPOINT_REQUIRED`,绝不代跑旧 Workflow——确需回退必须显式进入标为 `LEGACY_ORCHESTRATION_FALLBACK` 的旧入口并记录原因(`--legacy-reason`),不能给旧执行贴 `session_v1` 标签。当前双宿主真实验收为 `INCOMPLETE`,新入口仅作显式 PILOT,旧 legacy fallback 当前由 C4 能力门阻断；合成重放通过不等于真实宿主放行。`finish` 后必须对机器返回的 canonical 报告路径运行 `uv run --no-sync python -m autoresearch.session_agent verify-report --report-path <PATH> --expected-run-id <RUN_ID> --level full`,按结果分别声明编排、发布、完整性与重放;未绑定改写返回 `UNBOUND_REPORT`,不得借同一 run_id 或旧 ROOT 归因。

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
3. DETERMINISTIC 任务用 `execute --params-file <JSON>`；INFERENCE 任务派入独立研究上下文，根会话先按 [C4 绑定步骤](access-boundary.md) 绑定真实宿主身份，再交付登记读写命令。研究完成写入指定输出后用 `submit` 回交。
4. 推理输出后先用 `precheck --run-id <RUN_ID> --submission-file <SUBMISSION.json>` 做候选领域预检，按错误在当前活跃 attempt 修订。未最终绑定时 `host_evidence_status=PENDING_FINAL_BINDING`、`can_submit=false` 是预期结果。完成修订后仅封存一次完整 transcript：用 `bind-host-evidence` 绑定到 `task_id/attempt/session/context`，再携带匹配的真实 host receipt 预检及正式 `submit`。独立复核还须不同的真实上下文；同一主会话换角色名不算独立。详见 [预检与封存顺序](operations.md#候选预检与最终封存)。
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

完整命令与恢复流程见 [operations.md](operations.md)，对象和依赖关系见 [architecture.md](architecture.md)。逐票复核、状态提交与调度计量见 [scheduling.md](scheduling.md)。

## 日常全扫:宿主不进研究回路(2026-10-08 起,两个引擎同一条路)

每日全扫的缺省路径是 **headless 执行器**:主会话只负责起一个后台进程、等它结束、读摘要,
研究任务由 runner 直接起子进程(claude 场 `claude -p`,codex 场 `codex exec`),主会话的上下文
里不再出现任何任务 prompt、绑定命令或轮询输出。10-07 两边的计量:mailbox 宿主循环让 Codex
主会话吃掉全场输入的 84–90%、Claude 主会话吃掉加权输入的 62%,就是这条路要去掉的那块。

```bash
# 交互会话里(Claude 会话 --engine claude;Codex 会话 --engine codex),后台起、结束回合,等完成通知:
scripts/scan_run.sh --engine claude --date <分析日> --skip-readiness
# 摘要:$RPT/_ops/scan_run_<日>.json(result / delivery / run_id);日志同目录 .log;计量 token_usage.md
```

`--skip-readiness` 只在湖已灌齐时用(交易日 21:10 后由 launchd 定时场自己等就绪)。进度看日志里的
GATE/CP 行(Claude 宿主可用 Monitor 盯日志文件,不要 90 秒轮询)。失败处置、送达、电源与安装见
`docs/ops/scan-ops.md`「无人值守扫描」。下面的 mailbox 宿主循环只在 headless 不可用时作为回退。

## 扫描 runner + mailbox 宿主循环(host 模式,PILOT,回退路径)

上面的环由 runner 自动转圈:确定性任务(frame / prelude / GATE1 / 行业 pack / L3 prepare+lint+merge /
GATE2 / L4 prep+任务簿+slim+intel 状态 / 复核决策 / finalize / assemble / GATE4 / usage / observe)在
runner 进程内经 `service.execute` 跑(`exec_capture` 留痕,零 agent、零 general-purpose 壳);只有 7 种
判断角色(macro-brief、sector-brief、l3-rank(+repair)、l4-intel、l4-card、l4-card 复核)交给宿主会话。
**默认切换门尚未开启，legacy Workflow 当前也因缺少 C4 绑定而被能力门阻断**；本循环只作显式 PILOT。
已知缺口(单票终失败会让整场停在 BLOCKED/STALLED,legacy 则降级为盲卡)见
`docs/research/2026-09-26-session-plan-vs-workflow-audit.md`。

1. **begin**:同上 `begin --orchestration session_v1 --kind scan-market --mode AUTO`,请求用
   `examples/scan.request.json`;`host_profile.session_ref` 必须是本会话真实 session id(transcript 定位与
   计量都靠它),且如实声明 `independent_context/web_search/web_fetch=true` 并附证据(复核要独立上下文,
   intel 要联网)。主会话只跑机械的 wait → Agent → complete,研究质量不经过它:用 Opus 5.5 即可
   (`/model opus`);Fable 5.1 的单价是它的 2.5 倍,一场多花约 $9。实际用了什么模型由观测附录 E 的身份行记录。
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
   `wait` 缺省只回宿主视图(`task_id` / `attempt` / `agent_tool` / `host_prompt` / 路径与 `prompt_chars`),
   不再回显 `prompt` 全文与 `agent_spec`;排障要整份冻结请求加 `--full`。`by_reference` 现在缺省开:
   `host_prompt` 是一行指针,研究 agent 第一步自己 Read 冻结的任务文件。

   - `kind=REQUEST`:原样执行 `Agent(**<agent_tool>, prompt=<host_prompt>)` —— `agent_tool` 与 `host_prompt` 都是
     `wait` 算好的,照抄,不增不改。`session.mailbox.by_reference` 打开时 `host_prompt` 是一行指针(全文已冻结成
     任务的授权文件,agent 第一步自己 Read),关着时它就是 `prompt` 全文。Claude 下它通常只有 `subagent_type`:请求的 `model` 是全 ID(如 `claude-opus-5-5`)时已钉在
     agent 定义 frontmatter 里(preflight 核对两边一致),**不要**把它传给 Agent 工具(只收别名,会被拒),也
     **不要**换成 `opus`/`sonnet` 别名(别名随 Claude Code 升级改指,2026-09-22/09-28 两次静默换代就是这么来的);
     只有请求模型本来就是别名时 `agent_tool` 才带 `model`。
     C4 要求取得真实 agent ID 后先执行 `mailbox bind-access --run-id <RUN_ID> --task-id <TASK> --attempt <SESSION_ATTEMPT> --context-ref <AGENT_ID>`，将返回的登记命令发送给该 agent；绑定前工具调用会拒绝，不能跳过此步。
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
   复核超时/断连按既有瞬时错误分类重试该复核的 SESSION attempt，保留成功的 intel、主卡和其他复核。
   重试耗尽或历史父票已失败时，报告 `REVIEW_UNAVAILABLE` 及原因；其他 READY 工作继续，必需缺口仍阻止完整发布。
   任务终态、成功主卡、必需深核和复核覆盖分别观察，详见 [局部恢复](local-recovery.md)。
   如需重新开始，建立新的显式 PILOT run；旧 legacy Workflow 仍被 C4 能力门阻断。runner 不替失败 run
   冻结 capsule,需要时显式 `python -m autoresearch.trace.capsule finalize <RUN_ID> --business-status FAILED`。
   **领域校验拒绝不再整场作废**(2026-10-08):产物被确定性校验拒绝记 `DOMAIN_VALIDATION`
   (`contracts.retry.VALIDATION_REPAIR`),该任务(或 L4 票据)带着校验原话重做一次(prompt 末尾
   「修订要求」段),仍受 `session.max_attempts` / 票据 `max_attempts` 封顶;第二次仍被拒才 BLOCKED。

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

## headless 执行器(批 4,无人值守;2026-10-08 起两个引擎)

`session_agent begin … --executor headless` + `session_agent run --run-id <RUN_ID> --executor headless
[--claude-bin <path>] [--codex-bin <path>]`:同一个 runner,推理任务不交宿主会话,按 run 的引擎选传输:

- **claude run**:每个 attempt 起一个 `claude -p --agent <agent_type> --output-format json --permission-mode
  bypassPermissions --session-id <uuid> --max-turns N [--effort] [--model]` 子进程(独立顶级会话 = 独立上下文;
  项目 agent 定义与 hook 照常装载,见 `docs/research/2026-09-26-headless-driver-probes.md`)。
- **codex run**(`executors/headless_codex.py`):每个 attempt 一个 `codex exec` 线程,分三步 —— ① 开线程:
  `codex exec --json -C <仓库根> --sandbox read-only -c approval_policy=never -c model=<角色档>
  -c model_reasoning_effort=<角色档> -c developer_instructions=<.codex/agents/<role>.toml 原文> "<只回复 OK>"`,
  从首个事件 `thread.started` 取 `thread_id`;② 按 `thread_id` 做 session 级 C4 绑定(hook 负载的 `session_id`
  就是线程 id,顶级线程没有 `agent_id`);③ 干活:`codex exec --json -o <末消息> --sandbox workspace-write resume
  -c approval_policy=never -c model=… -c model_reasoning_effort=… [-c web_search=live]
  <thread_id> "<冻结 prompt + broker 命令>"`。绝不 `--ephemeral`(没有 rollout 就没有
  证据与计量)。开线程那一轮的墙钟 `session.timeouts.codex_open_s`(缺省 180 s),不占角色预算;rollout 按
  `~/.codex/sessions/**/rollout-*-<thread_id>.jsonl` 反查并绑定为 transcript。
  两个「为什么这么写」来自 2026-10-08 真实宿主探针(`docs/research/2026-10-08-codex-exec-probes.md`):
  **`developer_instructions` 只在开线程时生效**(resume 上传了等于没传,但开线程给过就一直在),
  **两轮必须同一档模型**(换档会被注入 1.79 万字符的 `<model_switch>` 基础提示词,比开线程省下的多得多);
  同一组探针也确认了 `codex exec` 下项目层 hook 照常装载、`resume` 吃 `-c model` 覆盖、`-c web_search="live"` 可用。

超时按角色(intel 12m / card·复核 25m / L3 30m)杀整个进程组;结果 JSON 非法、`is_error`、或退出 0 但
声明的输出文件不在 = 该 attempt 失败。子进程环境显式构造:`ANTHROPIC_*`、模型/effort/Bedrock/Vertex 路由开关与
`*_API_KEY`/`*_TOKEN`/`*_SECRET`(`CLAUDE_CODE_OAUTH_TOKEN` 除外)不传,记录只列被剥的名字。重试(attempt>1 或
同任务已有调用记录)前,上一次的产物挪到 `_dispatch/headless/stale/`、同任务仍在跑的旧会话先停;正常退出后也扫一遍
进程组。每次调用落 `<staging>/_dispatch/headless/<task>.a<n>.json`(同 attempt 重派时多一段 session 前缀,不覆盖;
argv 脱敏、pid + 启动时刻、usage、`total_cost_usd`、session id、transcript 路径)。runner 把 `~/.claude/projects/<slug>/<session-id>.jsonl`
整份绑定为 `host-binding` 证据(复核的独立上下文由进程边界满足),`usage_harvest` 按调用记录计量
(`dispatcher=headless`)。执行器不能重挂在飞的 `claude -p`:runner 崩了之后已认领的 attempt 报 orphan
(STALLED)。无人值守整场(锁、交易日、湖就绪、begin、送达、FAILED 通知、launchd)见
`docs/ops/scan-ops.md`「无人值守扫描」节;begin 请求由 `autoresearch.scan.scan_run.build_headless_request` 生成。
begin 的 `--executor`(缺省 mailbox)冻结在 `identity/session/role_support.json`,之后的扩展沿用:mailbox / 宿主
派发时生效的是 agent 定义的 frontmatter,所以 preflight 要求「定义 = 冻结配置」(不一致在取数前拦下,同步用
`python -m autoresearch.scan.agent_frontmatter --write`);headless 用 `--model/--effort` 显式透传,不受这条约束
—— 共用一份定义却要不同档位的角色(如 `l4_card` 与 `ens_review`)只能走 headless。

## 为什么可能省 token

收益来自更小且冻结的任务输入、确定性步骤不进模型上下文、LITE 早停、L3 只修失败行、同档复核早止，以及状态轮询不要求模型复述历史。任务图本身会增加少量 JSON、回执和控制提示，因此简单单股任务未必省 token。项目只按真实 transcript 和 usage 记录比较；样本不足时保持“观察中”，不承诺固定百分比。

## 当前切换状态

协议、五类计划、扫描四模式、恢复、发布和离线对拍已有自动化覆盖。`begin` 会在创建 run 前检查
`deterministic_exec`、`capture_binding` 和 `inference_handoff`，缺能力返回
`HOST_CAPABILITY_REQUIRED`。真实 Codex/Claude 宿主仍须分别完成 [acceptance.md](acceptance.md)
的运行矩阵；未验收场景保持显式 PILOT；旧 `LEGACY_ORCHESTRATION_FALLBACK` 当前同样受 C4 能力门阻断，只保留兼容代码。
`session_agent --orchestration legacy` 返回 `LEGACY_ENTRYPOINT_REQUIRED`，不会静默代跑。
旧 run 和历史 capsule 保持只读兼容。

默认切换由 `evaluation.accept_workflow(records)` 的机器门控制。每个 workflow 的 Codex 与
Claude Code 必须分别覆盖登记场景，记录必须是 `REAL_SESSION`（仅扫描声明的控制场景允许
`REAL_SESSION_DRILL`），且 portable proof 能同时解引用 VerificationResult、ReplayPlan、
ReplayResult、bundle、receipt 和 ExecutionOrigin。`SYNTHETIC`、字符串 `PASS`、任意 run_id
或缺 proof 都只得到 `INCOMPLETE`。当前没有完整双宿主 proof，因此五类默认均未切换。

请求字段、宏观六组与行业确定性候选见 [版本化研究请求](research-profiles.md)；样例使用 schema v4，默认候选开关保持原值。

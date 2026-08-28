# scan-market 法证级 Run Capsule 设计

> 日期：2026-08-27
> 状态：**已实施（Task 1–18），待一次真实生产扫描验收**
> 验收记录：`docs/research/2026-08-27-scan-forensic-capsule-acceptance.md`（CLI 端到端 + 12 场景故障矩阵已通过；生产全扫描验收见该文 §7）
> 一期范围：`scan-market` 全 A 股主链（Stage0、L0–L5、宏观 lite、行业 brief、L3、L4、发布、结果账本）
> 二期范围：复用同一基础设施接入单股 full、宏观 full、行业 full；一期不修改三条 full 链的产物契约
> 引擎隔离：本文的 `$CTX`/`$RPT` 始终指当前引擎根；Codex 只写 `context_codex/` 与 `reports_codex/`，不读写 Claude 根；`lake/` 仍是唯一共享数据湖

## 1. 决策摘要

把现有“成功发布后的最终文件镜像”升级为“从运行开始即存在、成功/失败/中断都能冻结的法证级 run capsule”。

每个 run 必须留下五类证据：

1. 当时实际执行的代码、配置、prompt 与运行环境；
2. 每个阶段和每次尝试的命令、时间、状态、stdout、stderr 与异常；
3. 确定性数据的精确读取 lineage，以及实时工具调用的请求/响应证据；
4. Agent 的实际派发 prompt、可见消息、工具调用、最终输出与计量；
5. 每阶段输入/输出快照、最终报告、事后结果的稳定关联。

验证拆成三个互不替代的结论：

- `integrity_ok`：已归档文件是否被改动；
- `completeness_ok`：按本次运行模式和终态，应有证据是否齐全；
- `replayability`：冻结输入是否足以重放确定性阶段。

`MANIFEST.sha256` 只回答第一问，不得再渲染成笼统的“现场完整性 ✓”。

## 2. 立案证据

真实 Codex run `reports_codex/scan/20260826_2000` 的现状：

- `MANIFEST` 覆盖 725 个文件且校验通过；
- `trace/staging/` 有 557 个文件；
- `trace/inputs/` 有 43 个外围输入；
- `trace/transcripts/` 为 0；
- `trace/lake_manifest.json` 不存在；
- `_token_usage.json` 只有一行 `(未标注) / INCOMPLETE / model=—` 的 Codex session 路径；
- `token_usage.md` 因此显示 `$0.0000`，不是实际成本；
- `chain_view` 仍将 725 个已列文件对得上渲染成“现场完整性 ✓”。

源码核验出的根因：

- `retention.write_lake_manifest()` 已实现，但 `retention.retain()` 没有调用；
- transcript 定位和计量仍以 `~/.claude/projects`、Claude agent 名和 Claude 消息 schema 为正典；
- `retain()` 只挂在成功发布与 `post_run observe`，L5 前失败的 run 没有归档终态；
- `StageResult` 与 `_l4_tasks.json` 保存最新快照，不保存完整状态迁移；成功会清空上一轮 `last_error`；
- `RunContract` 记录脏树路径但不记录 dirty patch、未跟踪执行文件或实际依赖环境；
- 湖清单即使生成，也只是日期窗口指纹，不是本次真实读取集合；
- 当前留存测试在强制 `AUTORESEARCH_ENGINE=codex` 下有 4 条因硬编码 `context_claude` 失败，未形成 Codex 生产路径验收。

## 3. 目标与非目标

### 3.1 目标

对任意 `run_id`，在不依赖共享 staging 和 harness 自身保留期的前提下回答：

- 这次运行使用了哪一版代码、配置、prompt、模型和依赖？
- 每个阶段实际执行了什么，何时开始/结束，是否重试，为什么失败或降级？
- 某只股票在哪一层进入、退出、被强留或被否决？
- Agent 实际看见了什么输入、调用了什么工具、输出了什么？
- 某个数字来自哪一个 endpoint/key/blob，内容是否仍与当时一致？
- 失败发生后，哪些阶段完成、哪些没有开始、最后一个可靠检查点是什么？
- 确定性 L0–L2/L5 能否只用 capsule 内输入重放并得到相同输出？
- 发布目录后来是否被修改，证据是否齐全，归档是否持久化？

### 3.2 非目标

- 不恢复已退役的自动学习、prompt 回注、自动调权或 proposal 治理；
- 不保证 LLM 再调用时逐字产生相同输出；模型服务和采样存在非确定性；
- 不保存或展示模型不可见的内部推理；只保存实际可见消息、工具调用、结果和最终产物；
- 不把 outcome 结果回注到当日决策；结果账本继续“只记不学”；
- 一期不重写单股 full、宏观 full、行业 full 的业务流程，只提供以后可接入的公共接口。

## 4. 核心术语与状态

### 4.1 Run 的两个状态机

业务状态和证据状态是两个正交状态机，不能混成一个枚举。

`business_status` 只能是：

- `ACTIVE`：业务主链运行中；
- `SUCCEEDED`：业务主链完成并通过原有业务门；
- `FAILED`：出现有根因的不可继续错误；
- `INTERRUPTED`：进程、会话或机器中断，没有业务层完整终态。

`evidence_status` 只能是：

- `PENDING`：证据仍在采集或尚未执行最终校验；
- `COMPLETE`：满足本运行模式的完整性契约；
- `EVIDENCE_INCOMPLETE`：法证证据不满足完整性契约；
- `LEGACY_PARTIAL`：旧 run，只能按历史已有证据复盘。

一个 run 可以是 `business_status=SUCCEEDED`、`evidence_status=EVIDENCE_INCOMPLETE`。现场归档失败不得删除已经完成的报告，但必须让 CP7、`index.md`、`chain_view` 和验证命令显式报红。

### 4.2 证据结果

```json
{
  "integrity_ok": true,
  "completeness_ok": false,
  "replayability": "PARTIAL",
  "durability": "LOCAL_ONLY",
  "missing_required": ["agents/l4-card/603259/attempt-1.jsonl.gz"],
  "not_expected": ["stages/l4/*: sentinel run"],
  "warnings": []
}
```

`missing_required`、`not_expected` 和 `warnings` 必须分开；“阶段未到达”不能伪装成“文件缺失”，合法空也不能伪装成“已完成”。

## 5. 总体架构

```text
capsule begin
  │  生成 run_id / RunContract v3 / ACTIVE spool
  ▼
每个 CLI / agent / 数据读取
  ├─ append events.jsonl（hash chain）
  ├─ stream stdout/stderr
  ├─ snapshot code/env/prompt/input/output
  └─ write exact-read lineage
  ▼
阶段检查点
  ├─ SUCCEEDED / FAILED / DEGRADED / SKIPPED
  └─ 保存 attempt 与当时产物，不覆盖旧尝试
  ▼
终态
  ├─ SUCCEEDED → 现有发布报告 + capsule
  ├─ FAILED → _failed/<run_id>/ + capsule
  └─ INTERRUPTED → 下次启动恢复器冻结
  ▼
completeness + replay probe + MANIFEST + detached root
  ▼
只读归档；outcome ledger 通过 run_id 外部关联
```

## 6. 路径与兼容性

### 6.1 活跃工作区

每次运行从开始就拥有独立工作区：

```text
$CTX/scan_runs/<run_id>/
├── run_contract.json
├── state.json
├── staging/<analysis_date>/
└── capsule/
```

生产工作流必须显式传递：

```text
AUTORESEARCH_ENGINE=codex
AUTORESEARCH_RUN_ID=<run_id>
AUTORESEARCH_STAGE=<stage>
AUTORESEARCH_INVOCATION_ID=<uuid>
```

`workspace.scan_dir(date)` 在 `AUTORESEARCH_RUN_ID` 存在时解析到 run-scoped staging；缺失时保留旧 `$CTX/scan/<date>` 行为，供历史工具和测试兼容。生产 workflow 缺 `AUTORESEARCH_RUN_ID` 必须在 Stage0 直接拒绝，不能静默退回共享 staging。

同一数据日允许多个 run 并存；run-scoped staging 消除同日重跑覆盖。

### 6.2 成功发布

成功 run 保留现有用户入口：

```text
$RPT/scan/<YYYYMMDD_HHMM>/
├── brief.md
├── summary.md
├── details/
├── trace/                 # 现有兼容层
└── capsule/               # 新法证包
```

现有 `manifest.json` 增加 `capsule_schema_version` 和 `evidence_status`，不删除旧字段。`capsule_root_hash` 在覆盖文件计算完成后才产生，写入不被自身覆盖的 `capsule/verification/ROOT.json` 与 detached root ledger，避免循环哈希。

### 6.3 失败与中断

```text
$RPT/scan/_failed/<run_id>/
├── index.md
├── failure.json
└── capsule/
```

`failure.json` 至少包含最后阶段、最后 invocation、异常类型、退出码或中断原因、最后可靠检查点、业务产物路径和证据状态。

### 6.4 Run 注册表

```text
$RPT/scan/_ledger/run_capsules.jsonl
```

每行是一条不可覆盖的 revision event，记录 `run_id`、递增 `revision`、`analysis_date`、业务状态、证据状态、最终路径、root hash、归档时间、前一 revision hash 与本行 hash。首次 finalize 写 revision 1，证据修复追加 revision 2；读取时按最大合法 revision 归约当前状态。写入使用文件锁，重复写同一 `(run_id, revision, root_hash)` 幂等去重；不做原位 upsert。注册表不参与任何交易判断。

## 7. Capsule 目录契约

```text
capsule/
├── identity/
│   ├── run_contract.json
│   ├── code.patch
│   ├── untracked_sources.tar.zst
│   ├── source_manifest.json
│   ├── environment.json
│   ├── dependencies.txt
│   └── prompts/
├── events/
│   ├── events.jsonl
│   └── invocations.json
├── logs/
│   └── <stage>/<invocation_id>.{stdout,stderr}.log.gz
├── stages/
│   └── <stage>/<attempt>/
│       ├── result.json
│       ├── inputs.json
│       └── outputs.json
├── agents/
│   ├── raw/
│   ├── normalized/
│   └── index.json
├── lineage/
│   ├── reads.jsonl
│   ├── external_tools.jsonl
│   └── coverage.json
├── blobs/
│   └── sha256/<prefix>/<hash>
├── products/
│   ├── staging/
│   └── external_inputs/
├── verification/
│   ├── expected.json
│   ├── completeness.json
│   ├── replay.json
│   ├── MANIFEST.sha256
│   └── ROOT.json
└── capsule.json
```

`blobs/` 内容寻址去重。`products/` 可以用硬链接、reflink 或指针引用 blob，但最终归档校验必须确认引用目标在归档根内；不允许依赖 `$CTX` 或共享 `lake/` 才能读取。

## 8. 组件设计

### 8.1 `autoresearch.trace.capsule`

公共生命周期 API：

```python
begin_run(kind, analysis_date, engine, config) -> RunHandle
load_run(run_id) -> RunHandle
checkpoint(stage, status, artifacts, metrics, error=None) -> Checkpoint
finalize(run_id, business_status, report_dir=None) -> FinalizationResult
recover_stale_runs(now, stale_after) -> list[FinalizationResult]
```

约束：

- `begin_run` 原子创建目录和 `ACTIVE` 状态；
- `checkpoint` 追加而不是覆盖 attempt；
- `finalize` 幂等，同一输入重复执行得到相同 root hash；
- 所有异常写入 capsule 自己的 `events.jsonl` 和 `completeness.json`；
- capsule 控制面故障不得删除业务产物。

### 8.2 `autoresearch.trace.events`

`events.jsonl` 是按时间追加的执行事实，不替代现有业务 outbox。

每行最小 schema：

```json
{
  "schema_version": 1,
  "seq": 42,
  "run_id": "20260827T010203456789Z",
  "ts": "2026-08-27T01:23:45.678901Z",
  "engine": "codex",
  "stage": "l4",
  "invocation_id": "inv-603259-2",
  "attempt": 2,
  "subject": "603259",
  "event_type": "COMMAND_FAILED",
  "payload": {"exit_code": 1, "error_class": "DATA_INTEGRITY"},
  "prev_hash": "6d7602e38f5c2f3d",
  "event_hash": "16c9cead3fd296b0"
}
```

事件至少覆盖：

- `RUN_STARTED`、`RUN_FINALIZED`、`RUN_INTERRUPTED`；
- `STAGE_STARTED`、`STAGE_COMPLETED`、`STAGE_FAILED`、`STAGE_SKIPPED`；
- `COMMAND_STARTED`、`COMMAND_COMPLETED`、`COMMAND_FAILED`；
- `TASK_CLAIMED`、`TASK_RETRY_SCHEDULED`、`TASK_BLOCKED`、`TASK_SUCCEEDED`；
- `AGENT_DISPATCHED`、`AGENT_COMPLETED`、`AGENT_FAILED`；
- `SOURCE_READ`、`SOURCE_FETCHED`、`SOURCE_FAILED`；
- `CHECKPOINT_WRITTEN`、`EVIDENCE_MISSING`。

事件使用递增 `seq` 和 `prev_hash` 形成 hash chain，能发现删除、插入和重排。

### 8.3 `autoresearch.trace.exec_capture`

所有生产 CLI 通过捕获器执行：

```text
python -m autoresearch.trace.exec_capture \
  --stage prelude -- \
  uv run --no-sync python -m autoresearch.scan.prelude <date>
```

捕获器必须：

- 保存 argv 数组而不是拼接后的 shell 字符串；
- 保存 cwd、开始/结束时间、退出码、signal、attempt；
- 流式写 stdout/stderr，SIGKILL 前已经写出的内容仍在；
- 保存异常摘要与 traceback；
- 只保存环境变量白名单和敏感变量的“在场/缺席”，绝不保存 token 值；
- 将 `AUTORESEARCH_RUN_ID/STAGE/INVOCATION_ID` 传给子进程。

现有 workflow 和 SKILL 命令统一改走捕获器。Python 内部再启 subprocess 时继承 trace 环境，并在 lineage 中记录 parent invocation。

### 8.4 `autoresearch.trace.source_lineage`

在 `data.cache.get_or_fetch` 的真实读点追加精确 lineage：

```json
{
  "run_id": "20260827T010203456789Z",
  "invocation_id": "inv-l1-1",
  "stage": "l1",
  "endpoint": "daily",
  "normalized_params": {"trade_date": "20260825"},
  "policy": "eod",
  "access": "CACHE_HIT",
  "path": "lake/daily/20260825.parquet",
  "sha256": "a4dc8c51b3f09e46",
  "bytes": 12345,
  "rows": 4312,
  "columns_hash": "bde90f7c2d8d4451",
  "started_at": "2026-08-27T01:05:00Z",
  "ended_at": "2026-08-27T01:05:01Z",
  "status": "SUCCEEDED"
}
```

规则：

- 记录“本次实际读过”的文件，不扫描一个猜测窗口；
- `key=static`、live 数据、可被重述的数据必须复制进 `blobs/`；
- 已冻结的 date/as_of parquet 优先使用内容寻址硬链接或 reflink，归档前校验 hash；
- 同一 blob 在同一 run 只保存一次，不同调用保留多条 lineage；
- 失败、空帧和 presence-gated 降级也要落记录；
- 直接绕过 `get_or_fetch` 的 yfinance/FRED/akshare/tushare 调用逐步接入同一 `trace_source` 包装器；未接入端点计入 `coverage.json`，不得伪装成 100%。

旧 `lake_manifest.json` 保留为兼容视图，但由 `reads.jsonl` 派生，不再是独立猜测扫描。

### 8.5 Codex/Claude transcript 适配器

新增 engine adapter 协议：

```python
class TranscriptAdapter(Protocol):
    def locate(self, run_identity: RunIdentity) -> list[TranscriptRef]:
        raise NotImplementedError

    def normalize(self, ref: TranscriptRef) -> NormalizedTranscript:
        raise NotImplementedError

    def usage(self, ref: TranscriptRef) -> UsageRecord:
        raise NotImplementedError
```

一期必须实现 Codex adapter，并保留 Claude adapter 的现有行为。Codex adapter 不依赖 Claude agent 名，依赖显式的 `run_id / invocation_id / role / subject` 绑定。

Codex transcript 绑定规则固定为：

1. harness 暴露稳定 thread/session ref 时，`capsule begin --session-ref <ref>` 显式登记；
2. harness 未暴露时，运行控制器只能按 cwd、run 起始时间和进程身份枚举候选，再由 `capsule bind-transcript <run_id> <path>` 写入明确绑定；
3. 候选为 0 写 `GONE`，候选超过 1 写 `AMBIGUOUS`；禁止用“最新 mtime 文件”静默猜一个；
4. LLM full run 存在 `GONE/AMBIGUOUS` 时，`completeness_ok` 必须为 false。

归档两层：

- `agents/raw/`：保留 harness 原始 schema、经过确定性 secret redaction 的 JSONL gzip，供未来 schema 变化后重新解析；不保存未脱敏副本；
- `agents/normalized/`：稳定 schema，只含可见消息、工具请求/结果、最终输出、错误、模型、effort、usage 和时间。

每个 agent invocation 在 `agents/index.json` 中必须有一行，包括 `PRESENT/GONE/UNSUPPORTED/NOT_EXPECTED`。完整性不能靠“目录不存在”推断。

主会话在非 Claude harness 下承担策略师或 L3/L4 角色时，必须通过 role boundary 事件把同一 session 切成可审计片段；不能因为没有 subagent 文件就把判断过程记为 0 个 agent。

`usage_harvest` 改成 adapter 派发；无法解析时写 `UNMEASURED`，禁止把未定价或零消息渲染成 `$0`。

### 8.6 代码、prompt 与环境快照

`identity/` 必须保存：

- Git HEAD、branch、dirty paths；
- `git diff --binary --no-ext-diff`；
- 行为目录中的未跟踪源文件归档：`autoresearch/`、`.claude/agents/`、`.claude/skills/`、`.claude/workflows/`、`pyproject.toml`、`uv.lock`、`AGENTS.md`、`CLAUDE.md`；
- 所有实际 rendered prompt 与其 source dependency 清单；
- Python、uv、OS、architecture、timezone、locale；
- `uv pip freeze` 和 `pyproject.toml`/`uv.lock` hash；
- engine、model、effort、service tier、可取得的 harness/tool 版本；
- 关键环境变量只记录 `present: true/false`，例如 `TUSHARE_TOKEN`，不记录值或值 hash。

Prompt 快照从手写 `PROMPT_SOURCES` 升级为两层：全量保存项目 agent/skill/workflow 小文件；同时保存“本次实际 rendered prompt”。新增 agent 时不需要同步复制白名单才能留下现场。

### 8.7 外部工具与实时网页证据

`external_tools.jsonl` 记录工具名、请求参数的脱敏版本、调用时间、状态、结果 hash、归属 agent/stage。WebSearch/WebFetch 的可见响应从 transcript adapter 提取并进入内容寻址 blob；L4 intel 继续作为整理后的业务产物。

如 harness 只提供摘要且不暴露原始网页，明确标 `capture_level=HARNESS_RESPONSE`，不声称保存了网页原文。URL、标题、抓取时刻和响应 hash 在可获得时必须保存。

## 9. 阶段数据流与检查点

### 9.1 Stage0 / Prelude

1. `capsule begin` 先于任何取数；
2. 保存 RunContract v3、代码/环境/config/pinned 快照；
3. 每个 prelude 子步骤有独立 invocation 和结果；
4. 单步失败的 stderr、异常和 note 全部保存；
5. `prelude` 总体成功不覆盖子步骤失败事实。

### 9.2 L0–L2

- 每个实际 lake 读取进入 lineage；
- L0/L1/L2 输出在各阶段结束时 checkpoint；
- `degraded.json`、重复 code 等降级事件进入事件流；
- 确定性 replay 以冻结输入重新运行，要求业务 CSV/JSON canonical hash 相同。

### 9.3 宏观 lite 与行业 brief

- 保存实际 pack、派发 prompt、agent transcript、最终 brief；
- 复用行业 brief 时记录来源 run、原文件 hash 和复用判据；
- 缺 transcript 时该角色完整性失败，而不是仅凭输出文件判成功。

### 9.4 L3

- pass1 前后、原始 judged、repair 后 judged 分 attempt 保存；
- 不再只有最终 `_l3_judged.json`；
- L3 agent 的实际 prompt、工具调用、错误、修复轮次与最终输出绑定同一 invocation chain。

### 9.5 L4

每票每次尝试独立目录和事件：

```text
stages/l4/603259/attempt-1/
stages/l4/603259/attempt-2/
```

保存 prompt、slim、deep、intel、卡、ensemble、task transition、错误和产物 hash。成功后不清除历史错误；`_l4_tasks.json` 继续服务现有运行控制，但法证事实以追加事件为准。

每个成功任务必须能对账：

- 1 份实际 L4 card invocation；
- intel 开启时 1 份 intel invocation 或显式降级；
- 触发 ensemble 时所需复核 invocation 数；
- prompt/slim/card 三者 hash；
- task book 最终状态与事件归约结果一致。

### 9.6 L5 / CP7

顺序调整为：

1. assemble；
2. gate4；
3. engine-aware usage harvest；
4. usage reconcile；
5. post-run observe；
6. 物化本运行模式的 `expected.json`；
7. deterministic replay probe；
8. 计算最终 `completeness.json`；
9. finalize + MANIFEST + ROOT + detached ledger + archive；
10. post-final verify；
11. CP7 播报业务结果和证据结果。

最终冻结后不再修改成功 run。outcome 继续写 `$RPT/scan/_ledger/`，通过 `run_id` 与 `contract_run_id` 关联，并为 outcome 文档单独计算 hash。

## 10. 失败、中断与恢复

### 10.1 可捕获失败

命令非零退出、Python exception、agent error、数据契约错误立即：

- 关闭当前 invocation 日志；
- 写 `COMMAND_FAILED`/`STAGE_FAILED`；
- 保存 traceback、错误分类和已有产物；
- 按原业务规则决定重试、阻断或继续降级；
- run 最终无法继续时冻结到 `_failed/<run_id>`。

### 10.2 SIGTERM/SIGINT

捕获器写 `RUN_INTERRUPTED`，刷新日志，执行 best-effort finalize。终止处理不得删除 spool。

### 10.3 SIGKILL、崩溃、机器掉电

无法依赖 finally。活跃进程每 30 秒刷新租约 heartbeat；下一次 `prelude`/`prewarm` 开始前运行 `recover_stale_runs`：

- 找出 heartbeat 超过 5 分钟、仍为 `ACTIVE` 且 PID/进程启动时刻已无法对应活进程的 run；
- 根据最后事件标记 `INTERRUPTED`；
- 保存当前 spool 和 staging；
- 生成完整性报告和 MANIFEST；
- 冻结到 `_failed/<run_id>`。

租约包含 PID、hostname、heartbeat 和 invocation；PID 复用时用进程启动时刻共同校验。

### 10.4 归档失败

归档异常不能抹掉业务结果。处理为：

- 持久化 `EVIDENCE_INCOMPLETE`；
- 保留 spool，不做清理；
- CP7 和下次 prelude 显示可执行的修复命令；
- `finalize --repair` 可在原始证据仍在时补齐；
- 修复只允许补缺，不能重写已冻结 run；补件写到 `$RPT/scan/_repairs/<run_id>/revision-<N>/`；
- composite view 按 base capsule + revision overlay 读取，补齐后产生新的 composite root revision，并在 root ledger 保留旧、新 root、overlay hash 与修复原因。

## 11. 完整性契约

新增 `autoresearch.trace.completeness`。`expected.json` 由运行模式、配置和阶段可达性确定，不使用固定全局白名单。

### 11.1 成功 full run 必需项

- RunContract v3、代码/环境/config/prompt 快照；
- prelude、frame、gate1、gate2、assemble、gate4 的 invocation/log/checkpoint；
- L0–L2 实际读取 lineage 与业务产物；
- strategist 和每个实际生成 sector brief 的 agent invocation；
- L3 invocation、pass1、judged、repair 历史；
- task book 中每票的 L4 attempt 历史与最终产物；
- 配置开启 intel/ensemble 时对应 invocation 或结构化降级；
- usage ledger 不得为伪零；无法计量必须 `UNMEASURED`；
- brief、summary、details、decision records、relative BUY、gate fires；
- replay 报告。MANIFEST、ROOT 和 detached ledger 属于 `integrity_ok` 的验证对象，不循环计入 `completeness_ok`。

### 11.2 Sentinel run

L3/L4 产物标为 `NOT_EXPECTED`，不计缺失；必须保存 sentinel 判据、跳过事件和 L5 产物。

### 11.3 Failed/Interrupted run

失败点之前的阶段按成功规则检查；失败点必须有失败事件、日志、错误和最后 checkpoint；下游阶段标 `NOT_REACHED`。

### 11.4 覆盖率

完整性报告至少给出：

- required/present/missing/not_expected 数；
- agent invocation 覆盖率；
- source read 捕获覆盖率；
- command log 覆盖率；
- deterministic replay 覆盖率；
- durability 状态。

任何 required 缺失都令 `completeness_ok=false`。不得用 MANIFEST 的 100% 抵消。

## 12. 重放设计

新增只读命令：

```text
python -m autoresearch.trace.capsule verify <run_id>
python -m autoresearch.trace.capsule replay <run_id> --stages l0,l1,l2,l5
python -m autoresearch.trace.capsule inspect <run_id> [--code 603259]
```

重放必须写临时 scratch，不写原 run、原 staging、共享 lake 或 outcome ledger。

重放级别：

- `FULL`：本次要求的确定性阶段全部使用 capsule blob 重放且 canonical hash 一致；
- `PARTIAL`：只有部分阶段可重放，逐项列缺口；
- `NONE`：身份或输入不足；
- LLM 阶段固定显示 `EVIDENCE_ONLY`，表示可以复核当时调用，不能承诺再次生成相同文本。

## 13. 完整性、不可变性与持久性

### 13.1 MANIFEST

最终 manifest 覆盖 capsule 与报告目录，排除 `MANIFEST.sha256` 与 `ROOT.json`。manifest 生成后计算 `capsule_root_hash = sha256(MANIFEST.sha256)`，再写 `ROOT.json`；这样 root 不参与自己的输入，不形成循环。

### 13.2 Detached root ledger

root hash 以 revision event 追加到 `$RPT/scan/_ledger/run_capsules.jsonl`。ledger 自身也用 `prev_hash` 形成 hash chain。`verify` 同时校验目录内 MANIFEST、`ROOT.json` 和 detached ledger；仅重写 MANIFEST 不能让篡改重新变绿。`LOCAL_ONLY` 状态只能抵抗普通误改，不能声称具有独立 WORM 服务的对抗性防篡改能力。

### 13.3 文件权限

成功或失败 capsule 完成后设为只读。需要补归档时不原位修改，在 `$RPT/scan/_repairs/<run_id>/revision-<N>/` 写 overlay、overlay manifest 和新 composite root，并保留完整修复链。

### 13.4 归档副本

一期在当前引擎根内生成压缩归档：

```text
$RPT/scan/_capsule_archive/<run_id>.tar.zst
```

归档文件 hash 进入 detached ledger。由于仍在同一文件系统，`durability=LOCAL_ONLY`。只有配置并验证独立存储 sink 后才能标 `OFFSITE_VERIFIED`；未配置时不冒充异地备份完成。

## 14. 安全与隐私

- 不复制 `.env`、API token、认证 cookie 或完整环境变量；
- argv、stderr、tool request/response、transcript 在落盘前经过已知 secret key 与 token 格式脱敏；
- 原始 transcript 目录权限 `0700/0600`；
- 脱敏器保存命中计数和规则版本，不保存被删值；
- 高熵字符串探针作为发布前检查，命中疑似 secret 时证据状态报红并隔离归档；
- capsule inspect 默认显示 normalized transcript，不直接打印 raw；
- 删除或缩短保留期必须由显式运维命令执行，不做静默 D10 清理。

## 15. 可观测性与用户界面

CP7 增加固定证据块：

```text
现场：integrity PASS · completeness FAIL(2 missing) · replay PARTIAL · durability LOCAL_ONLY
缺口：l4-card transcript 1 · source lineage 1
修复：python -m autoresearch.trace.capsule finalize --repair <run_id>
```

`chain_view` 的 ① 改为分别显示：

- 业务状态；
- evidence 状态；
- integrity；
- completeness；
- replayability；
- dirty patch 是否在场；
- agent/source/log 覆盖率。

`index.md` 只在三项全部满足时显示绿色“现场可复盘”；不再把 hash 对账等同于现场齐全。

## 16. 向后兼容与历史 run

- 现有 `trace/staging/`、`trace/inputs/`、`trace/reasoning/` 和报告文件继续保留；
- `retention.retain()` 变成 capsule 兼容适配器，老调用不立即删除；
- 2026-08-27 以前的 run 只做只读 audit，不伪造 transcript、lineage 或失败历史；
- 历史 run 的 evidence 状态按实标为 `LEGACY_PARTIAL`；
- 现有 `chain_view <report_dir> <code>` 继续工作，并增加 `run_id` 注册表解析；
- 不回写历史 run 的 MANIFEST；如需补建，写独立 audit 报告到 `$RPT/scan/_audits/`。

## 17. 测试与验收

### 17.1 单元测试

- 事件 hash chain：删除、插入、重排、改字节均失败；
- run 生命周期状态机与幂等 finalize；
- Codex/Claude transcript adapter fixture；
- 未定价/无法解析 usage 必须是 `UNMEASURED`，不能是 `$0`；
- exact-read lineage 对 cache hit、fetch、empty、exception、static overwrite 的覆盖；
- dirty patch、未跟踪执行文件、环境快照；
- completeness 的 full/sentinel/failed/interrupted 四种条件；
- secret redaction 与高熵探针；
- detached root 能发现重写 MANIFEST；
- `AUTORESEARCH_ENGINE=codex` 下所有路径只落 Codex 根。

### 17.2 集成故障注入

至少跑以下合成场景：

1. 成功 full run；
2. prelude 子步骤失败但总体继续；
3. L3 首轮失败、repair 成功；
4. L4 第一次瞬时失败、第二次成功；
5. L4 非瞬时 BLOCKED；
6. L3 完成后、L4 prompt 前异常；
7. assemble 前 SIGTERM；
8. 子进程 SIGKILL，下一次启动恢复；
9. 同一 analysis date 连续两次 run；
10. 归档写入失败但业务报告成功；
11. published 文件被改；
12. required 文件从 MANIFEST 生成前就缺失。

每个场景必须断言最终业务状态、证据状态、最后检查点、缺失分类和修复命令。

### 17.3 真跑验收

下一次真实 Codex full scan 必须满足：

- 独立 run-scoped staging，无共享数据日覆盖；
- strategist、L3、每张 L4 card、启用的 intel/ensemble 都在 `agents/index.json` 有明确状态；
- source lineage 至少覆盖所有 A 级 lake 读取，未接入来源列入 coverage 缺口；
- `_token_usage.json` 有真实 Codex model/usage，或诚实 `UNMEASURED`，不能为伪零；
- `integrity_ok=true`、`completeness_ok=true`；
- L0–L2/L5 replay 为 `FULL`；
- 修改任一归档文件后 integrity 失败；删除一个 required transcript 后 completeness 失败；
- 同日重跑不改变上一 run 的 root hash；
- outcome 回填后原 run root hash 不变。

## 18. 实施边界与顺序原则

具体任务拆分由后续 implementation plan 给出，但顺序必须满足：

1. 先建立 run identity、run-scoped staging 和事件日志；
2. 再接 CLI/StageResult/L4 task 的 attempt 捕获；
3. 再接 exact-read lineage；
4. 再实现 Codex transcript/usage adapter；
5. 再做 completeness 与 replay；
6. 最后切发布、失败恢复、只读归档和 UI；
7. 旧 `retain()` 只有在新链真跑验收通过后才允许降为兼容壳。

任何阶段不得先把旧绿灯改名继续使用。新 `completeness_ok` 必须由独立缺失变异测试证明：删除应有证据时测试必红。

## 19. 二期复用接口

单股 full、宏观 full、行业 full 二期只需提供各自的 `RunProfile`。接口字段固定如下，具体枚举由每条链在二期规格中声明：

```python
@dataclass(frozen=True)
class RunProfile:
    kind: str
    expected_stages: tuple[str, ...]
    agent_roles: tuple[str, ...]
    artifact_rules: tuple[ArtifactRule, ...]
    replayable_stages: tuple[str, ...]
```

生命周期、事件、命令日志、lineage、transcript adapter、身份快照、MANIFEST 和归档机制全部复用，不再各链复制一套 retention 代码。

## 20. 成功定义

本设计完成的标准不是“run 目录文件更多”，而是：

> 给定任意成功、失败或中断的 `run_id`，不读取共享 staging、不依赖 harness 尚未清理的 session、不猜当时版本，即可明确还原已执行步骤、精确输入、每次尝试、可见 agent 证据、最终结论和失败根因；确定性阶段可以从冻结输入重放，任何缺口都被机器标红而不是被 MANIFEST 绿灯掩盖。

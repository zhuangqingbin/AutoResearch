# 法证 run capsule —— 验收记录

> 日期:2026-08-28
> 设计稿:`docs/superpowers/specs/2026-08-27-scan-forensic-run-capsule-design.md`
> 实施计划:`docs/superpowers/plans/2026-08-27-scan-forensic-run-capsule.md`(Task 1–18)
> 引擎:`AUTORESEARCH_ENGINE=codex`;全部命令在 worktree `.worktrees/scan-forensic-capsule` 执行

## 0. 这份文档回答什么

设计稿的判据不是「代码写完了」,而是「**对任意 run_id,能不能真的答出那八个问题**」。
下面每一节都贴真实机器输出,不复述。**第 4 节的生产全扫描尚未跑**(需要一次真实 LLM 扫描),
本文如实标注为待办,不冒充已验收。

## 1. CLI 端到端(真实执行,零 LLM)

### 1.1 `capsule begin` 先于任何取数

```console
$ uv run --no-sync python -m autoresearch.trace.capsule begin scan-market 2026-08-26 \
    --engine codex --config-file .claude/skills/scan-market/scan_config.jsonc
{"analysis_date":"2026-08-26","contract_hash":"c1ddb33c841aae7b4a62229541bdbb89b632c5c68f9be04676fdbf8259b1d477",
 "engine":"codex","run_id":"20260828T073100367303Z","workspace":"context_codex/scan_runs/20260828T073100367303Z"}
```

**run 级隔离 staging**(同日重跑不再原地覆盖):

```console
$ ls -d context_codex/scan_runs/20260828T073100367303Z/staging/2026-08-26
context_codex/scan_runs/20260828T073100367303Z/staging/2026-08-26
```

**身份快照在 run 目录公布之前就已落盘**:

```console
$ ls context_codex/scan_runs/20260828T073100367303Z/capsule/identity/
code.patch  dependencies.txt  environment.json  prompts  run_contract.json
snapshot_inventory.json  snapshot_result.json  source_links.json  source_manifest.json
submodules.json  untracked_sources.tar.zst
prompts: 3 份
```

### 1.2 每个 agent invocation 都有明确一行

五条业务腿(strategist / sector-brief / l3-rank / l4-card / l4-intel)各派发一次并显式绑定 transcript:

```console
$ uv run --no-sync python -m autoresearch.trace.capsule materialize-agents $RUN_ID
agent index coverage: {"expected": 5, "missing": 0, "present": 5}
  l3-rank      market  PRESENT  output=1671
  l4-card      600000  PRESENT  output=1671
  l4-intel     600000  PRESENT  output=1671
  sector-brief market  PRESENT  output=1671
  strategist   market  PRESENT  output=1671
```

### 1.3 Codex 真计量:未定价 ≠ $0

```console
$ uv run --no-sync python -m autoresearch.trace.usage_harvest --engine codex --run-id $RUN_ID --json-out …
transcripts=5 unmeasured=0 unpriced=5 model=gpt-5.6-sol estimated_usd=None
```

`gpt-5.6-sol` 不在 Claude 价格表里 → `estimated_usd=None`、Markdown 里**不出现 `$0.0000`**。
证据缺失(绑定在、文件不在)则整行走 `UNMEASURED`。
🚨 **Codex rollout 的 `total_token_usage.input_tokens` 含 `cached_input_tokens`**(实测
`total_tokens == input_tokens + output_tokens`),adapter 因此上报 `input = input_tokens − cached_input_tokens`,
与 Claude 口径同义;直接用原始字段会把每一段 cache 前缀重复计一遍。

### 1.4 finalize 的固定次序与三个独立结论

```console
$ uv run --no-sync python -m autoresearch.trace.capsule finalize $RUN_ID --business-status SUCCEEDED --report-dir $RPT
{"archive": "reports_codex/scan/_capsule_archive/20260828T073100367303Z.tar.zst",
 "business_status": "SUCCEEDED", "durability": "LOCAL_ONLY",
 "evidence_status": "EVIDENCE_INCOMPLETE",
 "final_path": "reports_codex/scan/20260828T073100367303Z",
 "last_reliable_checkpoint": "gate4", "replayability": "NONE",
 "root_hash": "b1b6ed5f196395cc07cd34351694be5ec4ecf2938c495d7773d596cb3600a576"}

$ uv run --no-sync python -m autoresearch.trace.capsule verify $RUN_ID
  integrity_ok      = True
  completeness_ok   = False
  root_ledger_ok    = True
  event_chain_ok    = True
  replayability     = NONE
  durability        = LOCAL_ONLY
  archive_ok        = True
  missing_required  = ["lineage/reads.jsonl"]
```

**这条 `completeness_ok=False` 正是本波要的行为**:这次 CLI 验收没有真取数,所以没有读取 lineage ——
capsule 如实说「缺 `lineage/reads.jsonl`」,而不是因为 MANIFEST 全对就报绿。
旧展示层在同样的处境下会显示「现场完整性 ✓」。

账本(append-only,首条 `prev_hash` 为创世):

```console
$ tail -1 reports_codex/scan/_ledger/run_capsules.jsonl
{"run_id":"20260828T073100367303Z","revision":1,"business_status":"SUCCEEDED",
 "evidence_status":"EVIDENCE_INCOMPLETE","durability":"LOCAL_ONLY","prev_hash":"000…000"}

$ ls -la reports_codex/scan/_capsule_archive/
-rw-------  138353  20260828T073100367303Z.tar.zst
```

## 2. 负例(全部在**克隆**上做,冻结的原件一字未动)

```console
a) 改动已发布文件            → manifest.ok = False | changed = ['summary.md']
b) 再重写 MANIFEST 想盖住 a) → manifest.ok = True  但 root 锚定 = False
c) 删掉一份必需 transcript   → 新增缺失项 = ['agents/index.json: 1 reached invocation(s) without a transcript',
                                            'agents/l4-card/*']
```

b) 是整套设计的关键:**MANIFEST 可以被重写,脱钩的 ROOT 与账本不能**。三重锚定(文件 hash →
MANIFEST 字节 hash = root → 账本 revision)里,篡改者要同时改掉三处才能不留痕,而账本是
append-only 且逐行 `prev_hash` 链接。

## 3. 不变量

```console
结果账本回填后 root_hash 不变      = b1b6ed5f…a576(integrity_ok 仍为 True)
同一数据日第二次 begin:
  第二次 run_id = 20260828T073225156004Z ≠ 20260828T073100367303Z
  两个 staging 各自独立:context_codex/scan_runs/<run_id>/staging/2026-08-26
  第一次的 root 前后一致 = True
```

## 4. 十二场景故障矩阵(自动化)

`tests/integration/test_scan_capsule_faults.py` 用 3 只票的合成宇宙驱动**真实**控制面
(begin / checkpoint / agent-event / bind / finalize / recover / verify),不碰网络、Tushare、
Claude、Codex。12 个场景 + 5 条不变量断言全绿:

| 场景 | business | evidence | 最后可靠检查点 |
|---|---|---|---|
| success_full | SUCCEEDED | COMPLETE | gate4 |
| prelude_child_fail_continue | SUCCEEDED | COMPLETE | gate4 |
| l3_fail_repair_success | SUCCEEDED | COMPLETE | gate4 |
| l4_transient_retry | SUCCEEDED | COMPLETE | gate4 |
| l4_blocked | SUCCEEDED | COMPLETE | gate4 |
| after_l3_before_l4_prompt | FAILED | COMPLETE | l3 |
| sigterm_before_assemble | INTERRUPTED | COMPLETE | l4 |
| sigkill_then_recover | INTERRUPTED | COMPLETE | l4 |
| same_date_second_run | SUCCEEDED | COMPLETE | gate4 |
| archive_write_fail | SUCCEEDED | EVIDENCE_INCOMPLETE | gate4 |
| published_file_mutated | SUCCEEDED | EVIDENCE_INCOMPLETE | gate4 |
| required_missing_before_manifest | SUCCEEDED | EVIDENCE_INCOMPLETE | gate4 |

## 5. 全仓测试

```console
$ export AUTORESEARCH_ENGINE=codex
$ uv run --no-sync python -m pytest -q
3670 passed, 12 skipped(Task 16 收尾时的读数;Task 17/18 后的完整读数见提交记录)
```

## 6. 实施过程中被真数据/真运行证伪的四处

1. **`_last_reliable_checkpoint` 按文件名字母序取最后一个** —— `stages/*` 的 glob 排序里
   `prelude` 排在 `gate4` 之后,于是一条跑完全程的 run 会报「停在 prelude」。改按 attempt 自己的
   `created_at` 排序。**没有跑完九个阶段的真实验收,这个 bug 在单测里看不出来**(单测只写一两个阶段)。
2. **`capsule.json` 与 `lineage/coverage.json` 都在完整性评估之后才写** —— 检查者跑在被检查者前面,
   于是每次 finalize 都自报缺这两件。改为先写、后评估。
3. **agent 覆盖率只报不判** —— 设计稿 §8.5 规则 4 要求「有 GONE/AMBIGUOUS 则 `completeness_ok=false`」,
   但第一版只把覆盖率写进 coverage 段、没进结论。规则写在文档里、没写在代码里 = 没有这条规则。
4. **`products/staging/` 有消费者没有生产者** —— expected 清单要求它,却没人往里放东西;
   finalize 现在把本 run 的 staging 快照进 capsule(归档自足的前提)。

## 7. 尚未完成:一次真实生产扫描的验收

计划 Task 18 Step 4 要求「跑一次真的 Codex 全扫描并记录证据」。**本次未跑**,因为它需要一次
完整的 LLM 扫描(真实成本与时长),属于用户决定的操作。跑法(已写进 SKILL.md 步骤 0):

```bash
export AUTORESEARCH_ENGINE=codex
RUN_JSON=$(uv run --no-sync python -m autoresearch.trace.capsule begin scan-market <date> \
  --engine codex --config-file .claude/skills/scan-market/scan_config.jsonc)
RUN_ID=$(printf '%s' "$RUN_JSON" | jq -r .run_id)
# …照 SKILL.md 步骤 0.1–5 跑完,CP7 的 post_run observe 会自动 finalize + verify…
uv run --no-sync python -m autoresearch.trace.capsule verify "$RUN_ID"
uv run --no-sync python -m autoresearch.trace.capsule replay "$RUN_ID"
```

真跑后需要补记的三件,本文档现在给不出:
- 真实 lineage 覆盖率(A 级读取是否**全部**有精确读点与 blob);
- Codex transcript 的**真实**绑定路径与计量读数(本文用的是合成 fixture);
- 确定性 L0–L2/L5 的 `replayability`(本文为 `NONE`:没有可重放的真实输入)。

## 8. 与计划的两处偏离

1. **租约字段**:计划说「`state.json` 包含 hostname/pid/进程启动时刻/心跳」。实现把它们放在
   `state.json` 的 `lease` 子对象里(而不是与状态机字段平铺),这样 Task 2 那套被硬化过的
   `RunState` 状态机与其校验完全不用动。行为等价。
2. **重放编排的真跑**:`replay()` 的 runner 在生产走 `uv run … python -m <stage>` 子进程,
   测试走注入的假 runner。**真实子进程重放尚未在生产数据上跑过**(同第 7 节)。

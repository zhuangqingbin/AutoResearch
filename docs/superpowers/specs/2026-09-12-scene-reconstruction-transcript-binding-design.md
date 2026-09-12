# scan-market 现场重建:transcript 绑定与「看到了什么、怎么想的」设计

> 日期:2026-09-12
> 状态:设计五节已由用户逐节批准(2026-09-12),待审稿;**零实施**
> 范围:scan-market 两引擎(Claude subagent transcript / Codex 主线程 rollout 区段)的 transcript 绑定、capsule agents 索引、`chain_view` 渲染、E6 决策记录 schema 2、账本外目录(离线索引 / 抢救 / exec_check 账本腿)
> 不在范围:E6 行为改动(读卡的 EV 与执行线)、brief 报告改动、agent 模板改动(决策日志)、隐藏推理的捕获(harness 不落盘)
> 引擎隔离:`$CTX`/`$RPT` 指当前引擎根;账本目录 `reports_<engine>/scan/_ledger/`;Codex rollout 只读 `~/.codex/sessions/`,Claude transcript 只读 `~/.claude/projects/`;`lake/` 与本稿无关
> 上游设计:`2026-08-27-scan-forensic-run-capsule-design.md`(§8.5 适配器、§11 完整性契约)、`docs/specs/2026-08-26-scene-retention-and-buy-owner-design.md`(retention / chain_view / E6 v3.0)

## 1. 决策摘要

2026-09-12 对四笔亏损 relative BUY 做现场复盘,结论是:现场能答「当时说了什么」(L3 判断、L4 卡、E6 分数),答不全「当时看到了什么、怎么想的」。缺的不是留存量,是**绑定**:09-09 的 run 在 `trace/transcripts/` 里有 37 份 subagent transcript,slim 8332 字节整段在 tool_result 里,但 capsule 的 `agents/index.json` 把 36 个调用全记成 `GONE`(reached dispatch has no bound transcript),`chain_view` 又把已留存的 slim 印成「缺席」。Codex 引擎更彻底:一个 AGENT 事件都没有,期望恒为 0,rollout 从未归档。

本稿只做一件事:**把已经存在的证据绑到已经存在的调用上**,让两引擎的任一 run、任一只票在 `chain_view` 一屏回答四件事——读了什么(文件、字节、哈希)、搜了什么、写了什么、可见推理文本——外加 E6 为什么选它;缺的部分必须有账,写明谁缺、为什么缺。

零件都在:`capsule.bind_transcript` / `materialize_agent_index` / 两个 transcript 适配器 / `usage_harvest` 的 transcript 定位 / `chain_view` 的三级来源。scan 路从未调用过 `bind_transcript`(全仓唯一生产调用者是 `analyze/runctl.py`)。

## 2. 立案证据(2026-09-12,逐条可核)

| # | 事实 | 出处 |
|---|---|---|
| E1 | 09-09 run `trace/transcripts/_index.json` 37 份 PRESENT(l4-card 12、l4-intel 13、sector-brief 9、l3-rank 2、macro-brief 1);`capsule/agents/index.json` coverage `expected 36 / present 0`,每行 `status=GONE, reason="reached dispatch has no bound transcript"` | `reports_claude/scan/20260909-0909_2209/` |
| E2 | 688411 的 l4-card transcript(`l4-card-agent-a5abc4ddcd799ac25.jsonl.gz`,解压 101185 B):Read 3 次(`_l4_prompt_688411.md` 2244 B、`688411.SS_2026-09-09_slim.md` 8332 B、`_l4_intel_688411.md` 2253 B)、Glob 2、Write 1(`staging/2026-09-09/details/688411.md`)、StructuredOutput 1;text 块 6(1303 字);**thinking 块 5 个、字符 0**;model `claude-opus-5` | 同上 |
| E3 | `chain_view.py:302-305` 只在 `trace/inputs/slim/` 找 slim;真身在 `trace/staging/_external_inputs/688411.SS_2026-09-09_slim.md`(及 `_slim_deep.md`)→ ⑦ 段印「缺席(2026-08-26 前未留存)」 | `autoresearch/scan/chain_view.py` |
| E4 | `capsule.bind_transcript`(capsule.py:1562)、CLI `bind-transcript` / `materialize-agents` 存在;生产调用者只有 `autoresearch/analyze/runctl.py:245`;scan 路零调用 | `grep -rn bind_transcript autoresearch/` |
| E5 | `retention.archive_transcripts` 从 `_token_usage.json` 行(`agent`,`path`)定位 transcript;行里无 subject / invocation_id;usage 行 `agent` 取值:general-purpose 160(trace-control 壳)、l4-intel 12、l4-card 12、sector-brief 9、l3-rank 2、macro-brief 1、主会话 1 | `autoresearch/scan/retention.py:227`、09-09 staging `_token_usage.json` |
| E6 | capsule 事件链已有 `AGENT_DISPATCHED/COMPLETED`,`invocation_id` 形如 `l4-card-688411-1`、`l4-intel-688411-1`、`sector-brief-3-1`(subject=行业名)、`l3-rank-market-1`、`l3-repair-market-1`、`strategist-market-1`;由 `.claude/workflows/{scan-market,l4-stock}.js` 经 `capsule agent-event` 写入 | `capsule/agents/index.json`、`capsule/events/events.jsonl` |
| E7 | Codex 09-08 run:events 里 `SOURCE_READ 704 / TASK_CLAIMED 11 / TASK_SUCCEEDED 11 / STAGE_COMPLETED 11`,**零 AGENT_\* 事件**,agents coverage `expected 0`;`token_usage.md` 写「无 transcript」;`run_contract.session_ref=None` | `reports_codex/scan/20260908-0908_2246/` |
| E8 | 09-08 本仓 Codex rollout 恰一份(主线程,980 行,13:43→14:48Z),13 处 response_item 引用 `_l4_prompt_`、18 处引用 `/details/`;每行带 `ordinal`;`session_meta.payload` 含 `cwd`、`session_id`、`timestamp`、`source.subagent.thread_spawn.parent_thread_id`;工具调用是 `custom_tool_call name=exec`,input 为 JS 片段 | `~/.codex/sessions/2026/09/08/` |
| E9 | AGENTS.md 第 3 条:Codex 不派 subagent,按同一顺序**在会话内自己完成**各角色 | `AGENTS.md:26` |
| E10 | 65 个已发布 run:E6 决策在 run 内 6、只在共享 staging 13(08-07~08-25;12 份 mtime 与发布时刻一致,`20260817_2150` 被同日 22:15 重跑覆盖)、两处都无 46 | 本次普查脚本 |
| E11 | `_relative_buy_decision.json` 每候选只记 faces 四面 + 四硬门 + 分数;`relative_buy.py` 全文只读卡的 `proposal`,不读 EV / R:R / `[执行线]` / 「不建仓」;docstring 第 15 行「每个成功完成的交易日至少给出一只」 | `autoresearch/scan/relative_buy.py` |
| E12 | 四笔亏损 BUY 的卡原文:金螳螂 UW·SELL(入场否决「收盘仍 < 5.60 弃买」T+1 已触发)、兴业银锡 Hold「不追」、瑞丰银行 早停 Hold「不建仓」、海博思创 早停 Hold「不新开仓」(L3 `finalist=False`,靠守卫⑨强塞;09-10 又选 #5/5) | 各 run `details/*.md`、`chain_view` |
| E13 | CP7 定序:gate4 → usage_harvest → usage_reconcile → `post_run observe`(护照 → E6 → … → `retain()` → `_finalize_forensic_run`);`finalize` 内部第 1 步 `materialize_agent_index` → `_write_usage` | `SKILL.md:185-195`、`post_run.py:827-918,1116-1118`、`capsule.py finalize` |

## 3. 目标与非目标

### 3.1 目标

1. 两引擎的每个成功 run,`capsule/agents/index.json` 的每个 reached 调用要么 `PRESENT`(绑定到 transcript 或 rollout 区段),要么 `GONE/AMBIGUOUS` **带原因**;不再有「东西在盘上、账上说没有」。
2. `chain_view <run> <code>` 对该票每个调用印出:状态与 reason、model/effort/起止、读了什么(相对路径、字节、sha256 前 12 位、命中 prompt/slim/deep/intel/档案的标注)、**没读什么**、搜了什么(query 与命中 URL 数)、写了什么(产物与发布卡 hash 一致性)、可见推理文本(截断)。
3. 已冻结的 run(09-01 起有 transcript 归档的五次 Claude run,以及 rollout 仍在的 Codex run)通过账本外目录的离线索引得到同样的视图,**不写 run 目录一个字节**。
4. E6 决策记录能自己回答「E6 知不知道卡说别买」:记下卡的 EV/R:R/提案/不建仓/执行线,以及 E6 **没读**它们这一事实。
5. 服务对象是**人读复盘**(用户 2026-09-12 裁定);机器逐字节重放不是本稿目标。

### 3.2 非目标与补不了的

- **隐藏推理**:Claude thinking 块落盘为空(E2),Codex `encrypted_reasoning` 同理;本稿不试图捕获。Codex 明文 `reasoning` 摘要可留(§6.2)。
- **08-26 前 run 的 slim / deep / transcript**:当时未留存,永久缺;抢救(§9.1)只救共享 staging 里还在的 E6 决策与市场研判。
- E6 是否应该读卡的 EV 与执行线、是否允许 0 BUY、是否换池——行为改动,另案裁决。
- brief 是否印「卡面不建仓」——报告预算与 lint 契约,另案。
- agent 模板加「决策日志」块(C 路)——模板冻结中,另案。
- 账本 T+2 取错交易日历(`outcome.market_frame` 用湖文件列表定 T+1/T+2,湖缺日即滑到下一份文件并标 complete)——另一缺陷,**建议并入批 1 作 P0**,但属于用户裁决(§13)。

## 4. 两引擎的「一次调用」与统一 invocation id

| | Claude | Codex |
|---|---|---|
| 一次调用的证据 | 一份 subagent transcript(`~/.claude/projects/<proj>/<session>/subagents/**/agent-*.jsonl`) | 主线程 rollout(`~/.codex/sessions/Y/M/D/rollout-*.jsonl`)的一个 **ordinal 区段**;若 Codex 派了子线程,则子线程 rollout 整份 |
| 谁定位 | `usage_harvest` 已按角色定位(`_token_usage.json` 行) | 无人定位;本稿的 Codex 定位器(§5.4) |
| 调用边界的事实源 | 事件链 `AGENT_DISPATCHED/COMPLETED`(E6) | 事件链 `TASK_CLAIMED/SUCCEEDED`(l4)与产物在场(其它角色);见 §5.5 |
| 绑定形式 | `bind_transcript(path, role, invocation_id, subject)` | 同一函数,加 `start_ordinal/end_ordinal`;`CodexTranscriptAdapter._segment` 已支持 |

统一 invocation id(与现有事件链一致,不造第二套):`l4-card-<code>-<attempt>`、`l4-intel-<code>-<attempt>`、`sector-brief-<n>-<attempt>`(subject=行业名,n 为派发序号)、`l3-rank-market-1`、`l3-repair-market-1`、`strategist-market-1`。绑定器**按 (role, subject) 在期望表里查 id**,不自己拼;期望表缺席(Codex)时按 §5.5 派生。

角色名映射只此一处:usage 行的 `macro-brief` = capsule 的 `strategist`;两份 `l3-rank` transcript 靠产物区分 rank 与 repair,不靠标签。

## 5. 绑定器 `autoresearch/scan/transcript_binder.py`(零 LLM)

### 5.1 生效点与失败纪律

- 生效点:`post_run observe`,在 E6 决策之后、`retain()` 之前、`_finalize_forensic_run` 之前(E13)。此刻 `_token_usage.json` 已在 staging,run 仍 active(`bind_transcript` 要求 `require_active_run`);绑定后 `finalize` 第 1 步 `materialize_agent_index` 自然把 raw / normalized / tool_results 落进 `capsule/agents/`,`_write_usage` 顺带把两引擎的 token 计量变真。
- 失败纪律:与护照、E6 同——任何异常只打一行 stderr,**不阻断发布**;`AUTORESEARCH_RUN_ID` 缺席(legacy 路)→ 跳过并留痕。
- 开关:`scan_config.jsonc` 新键 `retention.bind_transcripts`(默认 `true`),走 `user_config` 白名单三件套:`_TOP_WHITELIST` 加 `retention`、`_SUB_WHITELIST` 加 `{"bind_transcripts"}`、类型表加 `_t_bool`,再加消费点与测试;`false` = 今天的行为。
- `bind_transcript` 加可选形参 `stage`(默认仍读 `AUTORESEARCH_STAGE`),绑定器按角色传与事件链同名的 stage(l3-rank/l3-repair → `l3`,l4-card/l4-intel → `l4`,strategist/sector-brief → 其派发阶段名;须过 capsule 的 `_STAGE_RE`);不改既有调用者。

### 5.2 核心规则(引擎无关)

一份 transcript(或区段)绑到调用 X,当且仅当它含有**一次对 X 的产物路径的写入**,且该路径落在**本 run 的 staging 根**下:`context_<engine>/scan_runs/<RUN_ID>/staging/<date>/`。根校验是防误配的硬条件(前一天同名 `details/<code>.md` 不得匹配)。

| 角色 | 产物标记(绑定判据) | 输入标记(区段起点 / 兜底) |
|---|---|---|
| l4-card | `details/<code>.md` | `_l4_prompt_<code>.md` |
| l4-intel | `_l4_intel_<code>.md` | 无(盲搜,无输入文件) |
| sector-brief | `sector_briefs/<行业>.md` | `context_<engine>/sector/<date>/<行业>.json` |
| l3-rank | `_l3_judged.json` | `_l3_table.md` |
| l3-repair | `_l3_repair_patch.json` | `_l3_repair_prompt.md` |
| strategist | `market_view.md` | `strategist_pack.json` |

结果四态(写进 §5.7 的报告;capsule 侧只见 PRESENT/GONE):

- `BOUND`:产物标记命中 → 调 `bind_transcript`。
- `UNVERIFIED_BY_PRODUCT`:只有输入标记、无产物(agent 失败没写出来)→ 仍绑,报告标注;materialize 后 index 行 PRESENT,但 §7 视图印「产物未写出」。
- `AMBIGUOUS`:同一 (role, subject) 的候选数超过期望的 attempt 数,或 Codex 主线程候选多于一份 → **一份不绑**,报告记全部候选路径(沿用 STAGES.md 第 241 行:候选多个写 AMBIGUOUS,禁止按 mtime 猜)。
- `GONE`:两种标记都没有,或期望的调用找不到任何候选。

同一 (role, subject) 出现多份候选且不超过 attempt 数:按 transcript **首行时间戳**升序对应 attempt 1、2、…。

### 5.3 Claude 定位器

1. 读 staging `_token_usage.json` 的 `rows`,只取 `role=subagent` 且 `agent ∈ retention.ARCHIVE_TRANSCRIPT_AGENTS`(`l3-rank, l4-card, l4-intel, macro-brief, sector-brief`);`general-purpose`(trace-control 壳)与主会话排除。
2. 逐份打开 jsonl(按 `retention.archive_transcripts` 同样的容错:坏行跳过、文件不在记 GONE),收集 `tool_use` 块里 `Write` 的 `file_path`、`Read`/`Glob` 的路径、首行 `timestamp`。
3. 用 §5.2 表匹配 → (role, subject) → 在 `_agent_expectations` 里查 invocation id → 绑定。
4. 不依赖 `agentId` 与 invocation id 的时间对齐——那是猜;产物路径是事实。

### 5.4 Codex 定位器与 ordinal 区段

1. 枚举 `~/.codex/sessions/<Y>/<M>/<D>/rollout-*.jsonl`,日期从 `RUN_STARTED` 事件的日期到当天;保留 `session_meta.payload.cwd == 仓库根` 且行时间戳区间与 `[RUN_STARTED − 5 分钟, 当下]` 重叠的文件。
2. 主线程 = `session_meta.payload.source` 无 `subagent` 的文件;子线程 = `source.subagent.thread_spawn.parent_thread_id == 主线程 session_id` 的文件。主线程**恰一份** → 候选;多份 → 整场 AMBIGUOUS(报告记全部路径,一份不绑);零份 → 整场 GONE。
3. 在候选主线程内按内容锚定每个调用的区段:起点 = 第一行(`response_item` 的 `custom_tool_call`/`function_call` input 或 `message` 文本)含该调用输入标记的 `ordinal`;终点 = 最后一行含其产物标记的 `ordinal`;无输入标记的角色(l4-intel)起点取产物标记首现。绑定 `bind_transcript(path, ..., start_ordinal, end_ordinal)`。
4. 区段允许重叠(模型交错处理多只票);报告记 `segment_quality: exclusive | interleaved`。子线程 rollout 若命中某调用的产物标记,整份绑定(无区段)。
5. `exec` 的 input 是 JS 片段,产物路径按**子串**匹配,不依赖工具形状;根校验同 §5.2。

### 5.5 Codex 期望回退

`_agent_expectations` 只消费 `AGENT_*` 事件(E7:Codex 零事件 → `expected 0`)。加一条回退,**仅当该 run 零 AGENT 事件时生效**:

- l4-card:从 `TASK_CLAIMED`(stage=l4,subject=code,attempt)派生 `l4-card-<code>-<attempt>`;
- l4-intel、sector-brief、l3-rank、l3-repair、strategist:从产物在场派生(`_l4_intel_*.md`、`sector_briefs/*.md`、`_l3_judged.json`、`_l3_repair_patch.json`、`market_view.md`),sector-brief 的 `<n>` 按行业名排序编号。

index 行加字段 `expectation_source: agent_events | task_events | products`(capsule `_TRANSCRIPT_SCHEMA_VERSION` 1 → 2,只增不改)。这样 Codex 也有 GONE 账,且**不依赖 LLM 记得去敲 `capsule agent-event`**;让 Codex 主动发事件只作可选加固(§13)。

### 5.6 歧义、失败、幂等

- `bind_transcript` 对相同身份幂等,重跑 observe 不重复;身份不同而 invocation 相同 → 它抛 `conflicting transcript binding`,绑定器捕获后记 AMBIGUOUS,不覆盖既有绑定。
- 候选文件不可读 / 是符号链接 → GONE 带原因(与 `_archive_bound_transcripts` 同判据)。
- 期望表里有、候选里没有 → GONE(`no candidate transcript`);候选里有、期望表里没有 → 仍绑,materialize 记 `bound without a dispatch event`(现有行为)。

### 5.7 绑定报告 `_transcript_bindings.json`

落 staging,`retain()` 镜像进 `trace/staging/`。形状:

```json
{"schema_version": 1, "run_id": "...", "engine": "claude|codex", "enabled": true,
 "candidates": {"claude_usage_rows": 37, "codex_rollouts": 0},
 "rows": [{"invocation_id": "l4-card-688411-1", "role": "l4-card", "subject": "688411",
           "status": "BOUND|UNVERIFIED_BY_PRODUCT|AMBIGUOUS|GONE", "reason": "...",
           "path": "...", "product": "staging/2026-09-09/details/688411.md",
           "start_ordinal": null, "end_ordinal": null, "segment_quality": null,
           "expectation_source": "agent_events"}],
 "counts": {"bound": 36, "unverified": 0, "ambiguous": 0, "gone": 0}}
```

它是「谁缺、为什么缺」的账;`agents/index.json` 只说 PRESENT/GONE。

### 5.8 回滚杆

`retention.bind_transcripts=false`。批 2–4 各自独立(§12)。

## 6. 索引与归一化

### 6.1 现成链,不新建

绑定一落,`finalize` 第 1 步 `materialize_agent_index` 即:脱敏 gzip 进 `capsule/agents/raw/`;按适配器归一化成 `message / tool_request / tool_result / error` 写 `agents/normalized/`;带 URL 的往返写 `lineage/external_tools.jsonl`;`agents/index.json` 每调用一行(PRESENT / GONE / AMBIGUOUS / UNSUPPORTED / NOT_EXPECTED,带 model、effort、usage、items)。两引擎共用;Codex 走区段(`_segment`),区段内 `token_count` 快照差分得 usage,量不出写 UNMEASURED。

### 6.2 三处小改(两适配器各补一行级别)

1. `tool_result` 项 payload 加 `bytes` 与 `sha256`(内容本身留在 raw);视图靠它回答「读到的 slim 是不是发布时那份」。
2. Codex 适配器:`_SKIPPED_RESPONSE_ITEMS` 里的明文 `reasoning`(摘要文本)改留为 `reasoning_summary` 项;`encrypted_reasoning` 照丢。Claude 无对应物,视图写「无(harness 未落盘)」。
3. 适配器把「读文件」与「归一化行」拆成两步(纯重构,文件版调用行版),供 §6.3 离线模式对 gz 归档在内存里归一化。

### 6.3 已冻结 run 的离线外部索引

同一绑定器核心加 `--offline <run_dir>` 模式:输入 `trace/transcripts/*.jsonl.gz`(Claude)或 §5.4 枚举到的 rollout(Codex),加 `capsule/events/events.jsonl`(期望);输出 `reports_<engine>/scan/_ledger/agents_index/<run_id>.json`,形状与 `agents/index.json` 一致,多两列 `source: trace_transcripts | codex_sessions` 与 `computed_at`。归一化结果落 `_ledger/agents_index/<run_id>/normalized/`。**不写 run 目录**(MANIFEST 不变量)。一次性 CLI,不进 prelude、不进 nightly。

## 7. `chain_view` 改动

1. **⑦ slim/deep 查找**:`trace/inputs/slim/` → `trace/staging/_external_inputs/` → 共享 staging `_external_inputs/`;命中哪级标哪级(复用 `Sources.find` 的三级语义)。E3 的假读数消失。
2. **新增 ⑦b「研究员现场」**:来源顺序 `capsule/agents/index.json`(+`normalized/`)→ `_ledger/agents_index/<run_id>.json` → 缺席(写「未绑定:<reason>」)。对本票的每个调用(l4-intel、l4-card;L3 judged 已在 ⑥)印:
   - 状态行:`PRESENT|GONE|AMBIGUOUS` · reason · `expectation_source` · Codex 加 `ordinal a–b · segment_quality`;
   - model · effort · 起止时间 · items 数;
   - 读了什么:每项一行,相对路径 · 字节 · sha256 前 12 位 · 标注 `[prompt|slim|deep|intel|dossier|其它]`;Codex 从 exec 命令串按正则取路径,tool_result 的 bytes/sha 来自输出;
   - **没读什么**:按角色应读清单(l4-card:prompt/slim/intel 必读,deep 视早停)列出未命中的项;早停卡「deep 未读」在此可见;
   - 搜了什么:query 列表 + 命中 URL 数(读 normalized 的 `tool_request`/`tool_result`,tool_name 为 WebSearch / WebFetch / web_search;capsule 在场时对照 `lineage/external_tools.jsonl`,离线索引无 lineage 也能印);
   - 写了什么:产物路径 · 与发布卡 `details/<名>.md` 的 sha256 是否一致;
   - 可见推理:assistant 文本块每块截 300 字、最多 6 块;Codex 另印 `reasoning_summary`(同截断)。
3. **① 身份段**加一行:`transcript 绑定 N/M · AMBIGUOUS k · 来源 capsule|ledger`。
4. **⑧ E6 段**:渲染 §8 的 `card_context` 与 `why`(缺席时印「schema 1,无卡面上下文」)。

渲染保持确定性文本:排序固定、截断固定、两次渲染逐字节相等(现有 `test_chain_view` 风格加 golden 片段)。

## 8. E6 决策记录 schema 2(只记不学)

`_relative_buy_decision.json` `schema_version` 1 → 2;**选择语义一字不动,`RULE_VERSION` 不变**。

每个候选加 `card_context`,全部从该票的卡 md(staging `details/<code>.md`)确定性解析;早停卡没有的字段写 `null`:

| 字段 | 来源 | 例:688411 @ 09-09 |
|---|---|---|
| `card_kind` | 卡头标记(`〔早停·表面 DD〕` / 满卡) | `earlystop` |
| `proposal` | `FINAL TRANSACTION PROPOSAL:` 行 | `HOLD` |
| `ev_target` / `rr` | 仪表盘 EV 目标带、R:R(`l4/parsers.py` 现有解析) | `null` / `null` |
| `no_new_position` | 仪表盘「仓位」或「触发位」含「不建仓 / 不新开仓 / 0%」 | `true` |
| `exec_lines_present` | 卡尾两行 `[执行线]` 在场且阈值等于 `contracts.agent_output` 常量 | `true` |
| `consulted` | `{"card_ev": false, "card_exec_lines": false, "card_position": false}`(v3.0 规则事实) | 全 `false` |

顶层加 `why`,固定模板:`池=<pool>(席 <n_in_pool>):<n_vetoed> 只被硬门否决(<按 reason 计数>),持仓排除 <n_pinned>;合格 <n_elig>,BUY=#<rank>/<n_elig> <code>;卡面 <rating>·<proposal>·<不新开仓|可建仓>;E6 未读 EV/执行线。`

- 写者与校验者(`safe_write_decision` / `safe_verify_decision`)用同一函数产出,parity 不变;卡文件缺席时 `card_context = null` 并在 `inputs` 留痕,不影响选择。
- 既有消费者(brief、outcome 账本、chain_view ⑧、测试)只读旧键,向后兼容;brief 不动。

## 9. 附加批

### 9.1 A2 老 run 抢救(一次性 CLI `python -m autoresearch.scan.salvage <run_id>|--all`)

- 对象:E10 的 13 个 run(E6 决策只在共享 staging)+ 07-01 起共享 staging 里仍在的 `market_view.md`;Codex 侧仍在 `~/.codex/sessions/` 的 rollout。
- 产物:`_ledger/salvage/<run_id>/{_relative_buy_decision.json, market_view.md, transcripts/rollout-*.jsonl.gz, provenance.json}`;`provenance.json` 记源路径、mtime、sha256、run `generated_at`、判定 `MATCHES_RUN_WINDOW | OVERWRITTEN_BY_LATER_RUN | UNKNOWN`(`20260817_2150` 必判 OVERWRITTEN)。
- 不写 run 目录;幂等;`chain_view` 的 `Sources.find` 加第四级「账本抢救」,命中时标注。

### 9.2 A3 exec_check 账本腿(默认**不进**本波,用户裁)

08-31 稿 D3 已裁、排 P3 未做。此处只提前其落盘腿:T+1 14:30 对当日 BUY 与 📌 持仓跑 `rt_min` 分钟线,现算 `pct_chg` / `pos_in_range`,用 `contracts.agent_output.EXEC_LINE_*` 判执行线,写 `$RPT/scan/_ledger/exec_checks/<anchor_session>/<code>.json`(时刻、价格、判定、快照),append-only,不展示、不改卡。它记的是「工具核过」,不是「人买了」;人的动作仍属 broker 分支。

## 10. 产物登记(ARTIFACTS,先登记再写代码)

| name | path | root | stage | producer | presence |
|---|---|---|---|---|---|
| `transcript_bindings` | `_transcript_bindings.json` | staging | observe | transcript_binder | gated(`retention.bind_transcripts` 且 observe 跑过) |
| `agents_index_ledger` | `agents_index/*.json` | ledger | observe | transcript_binder | conditional(对冻结 run 跑过离线索引) |
| `agents_index_normalized` | `agents_index/*/normalized/`(kind=dir) | ledger | observe | transcript_binder | conditional(同上) |
| `salvage_provenance` | `salvage/*/provenance.json` | ledger | observe | salvage | conditional |
| `exec_checks` | `exec_checks/*/*.json` | ledger | observe | exec_check | conditional(仅 A3 批准后) |

`capsule/agents/bindings.jsonl` 已在 08-27 capsule 契约内,不新登记;`emit --write` 走一遍;不加 prelude 步骤,`STEP_NAMES` 与 `test_step_names_inventory` 不动。

## 11. 验收、变异探针、回滚

### 11.1 验收(两引擎各一遍)

1. 单测(`tests/scan/test_transcript_binder.py`):合成 Claude jsonl(Write 到本 run staging 根)与合成 Codex rollout(exec 写文件、带 ordinal)→ 预期绑定;前一天同名路径不匹配;两份争一个调用 → AMBIGUOUS 且一份不绑;只有输入无产物 → UNVERIFIED_BY_PRODUCT;attempt 按首行时间排序。
2. 集成(仿 `tests/integration/test_scan_capsule_faults.py`):begin → observe → finalize,`agents/index.json` `present == expected`;Codex 零 AGENT 事件时期望从 TASK_CLAIMED 派生,缺的记 GONE,`expectation_source` 正确。
3. 离线索引对 09-09 真 run:36 个调用 ≥ 34 PRESENT;`chain_view 20260909-0909_2209 688411` ⑦b 印出 prompt、slim、intel 三次 Read、「deep 未读」、写出的卡 hash 与发布卡一致。
4. 下一次真扫描(两引擎各一次):当日 BUY 的 `chain_view` 有 ⑦b;`capsule verify` 的 agents 轴转绿。其它轴(步骤 5 不经 exec_capture)不在本波承诺。
5. E6:对 09-09 重算,688411 的 `card_context` 四字段与 `why` 逐字等于预期;writer-1/writer-2 parity 绿。
6. `chain_view` 两次渲染逐字节相等;golden 片段锁 ⑦b 与 ⑧ 的格式。

### 11.2 变异探针(每条先证明会变红)

- 去掉「路径必须在本 run staging 根下」→ 前一天文件误配的测试必须红。
- 区段逻辑换成整文件 → `segment_quality` 测试必须红。
- 删掉 observe 里的绑定调用 → 集成测试 `present` 归零必须红。
- 删 `card_context` → parity 测试必须红。
- 把 `expectation_source` 回退关掉 → Codex 集成测试 `expected 0` 必须红。

### 11.3 回滚

`retention.bind_transcripts=false`(批 1);批 2 无开关,回滚 = revert 该提交;批 3、4 独立 CLI,删即回滚。

## 12. 批次与类别

| 批 | 内容 | 类别 | 依赖 |
|---|---|---|---|
| 1 | 绑定器 + Codex 期望回退 + 适配器三处小改 + `bind_transcript(stage=)` + `chain_view`(⑦ 修查找、⑦b、①、⑧ 渲染)+ ARTIFACTS + 测试 | I | 无 |
| 2 | E6 schema 2(`card_context` + `why` + `consulted`) | I | 无 |
| 3 | 离线索引 + A2 抢救 CLI + `Sources` 第四级来源 | I | 批 1 的核心 |
| 4 | A3 exec_check 账本腿 | I(影子) | 用户裁 |
| P0(另裁) | 账本 T+2 日历修复:`market_frame` 按交易日历取 T+1/T+2,湖缺日即未成熟;`fill --rebuild` | I | 无 |

## 13. 待裁与不在范围

| # | 问题 | 本稿建议 | 不选的后果 |
|---|---|---|---|
| Q1 | 账本 T+2 修复并入批 1? | 并入,作 P0 | `recommendations.csv` 里 09-01/09-07 两 run 21 行继续错,chain_view ⑩ 跟着错 |
| Q2 | A3 exec_check 账本腿进不进本波? | 不进,留 P3 | 「14:45 有没有核过执行线」仍无记录 |
| Q3 | 让 Codex 主动敲 `capsule agent-event`? | 只作可选加固,不作主路 | 无;§5.5 回退已覆盖 |
| Q4 | E6 读卡的 EV 与执行线 / 允许 0 BUY / 换池 | 另案(行为改动) | 「有正期望才叫 BUY」的裁定与 E6 mandate 继续冲突 |
| Q5 | brief 印「卡面不建仓」 | 另案(≤3000B 预算 + lint) | 人读 brief 看不到卡与 BUY 打架 |
| Q6 | agent 模板加决策日志(C 路) | 等模板冻结解除 | 「哪条证据翻转了判断」仍只在卡的散文里 |

## 附录 A 本次复盘的关键读数(引用须带限定:6 笔 / 未扣成本 / 单一 range regime)

- 08-19 E6 转正起 9 次成熟 run、84 行:relative BUY 6 笔 5 负,均 −0.66pp、相对市场 −0.31pp;全部非持仓卡等权 −0.11pp、相对 +0.11(t −0.82);Hold 卡 −0.39(t −2.07)反比 UW 卡 +0.10 差;📌持仓 −0.36 不显著;执行线内 +0.11 vs 线外 −0.74(t −3.77)。
- 四笔亏损 BUY 的现场:四张卡全写不建仓或 UW·SELL;BUY 由 E6 相对层制造;3/4 是📌排除后的第三名;E6 不读 EV/R:R/入场否决。
- 09-01、09-07 两 run 的账本 T+2 取错(湖缺日滑到下一份文件),21/84 行错;`market.csv` 因 09-12 用补齐的湖重建反而正确——同一账本两套日历。

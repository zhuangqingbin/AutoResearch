# C · capsule / contracts / Codex 一致性 只读审计(为 stock-research 优化 brainstorm 供料)

> 日期 2026-08-30 · 只读,零改动 · 所有 `file:line` 为工作树现状(含未提交改动)。
> 标 **UNVERIFIED** 的是读码推断、没有真跑证据。

---

## 0. 三句话先说结论

1. **capsule 是 scan-market 专属**:`begin_run` 硬拒 `kind != "scan-market"`(`autoresearch/trace/capsule.py:428-429`),`scan_profile()` 的 `kind` 写死 `"scan-market"`(`autoresearch/scan/run_profile.py:207`),所有根路径(`scan_runs/`、`reports/scan/_ledger|_failed|_capsule_archive|_repairs`)都带 `scan` 字面(`capsule.py:534,920,1983-1992,2947-2948`)。**没有 analyze / stock-research 的 profile、mode、role、stage 词汇**(`autoresearch/contracts/stages.py:57-70,106,148-161`)。设计稿自己把「单股 full」列为二期(`docs/superpowers/specs/2026-08-27-scan-forensic-run-capsule-design.md:6-7,74,707-721`)。
2. **stock-research 独立跑 = 零留痕**:`analyze/harvest.py` / `analyze/assemble.py` 不 import trace、不开 run、不过 exec_capture、不记 lineage、不写 checkpoint;主会话 Claude 写报告时的工具调用与网查、以及 `company-intel`/`us-intel` 子 agent 的网查,除了稿件里自报的「`网查 N 条`」和「源」列 URL 之外**没有任何机器可读留痕**。为独立技能预留的 adapter `evidence_index.materialize_report_trace(context="stock_full")` 已写好但 **`autoresearch/` 内零调用者**(只有 `tests/trace/test_evidence_index.py` 在调)。
3. **Codex 侧没有 subagent 等价物**:`~/.codex/agents/` 为空目录,repo 无 `.codex/`/`.agents/`;`AGENTS.md:26` 明写「`Workflow`/`Agent(subagent_type=...)` 是 Claude Code 专有,codex 等价做法 = 自己在会话内按同一顺序完成各角色」。设计稿为此预留的「同一 session 切片段」机制(`TranscriptRef.start_ordinal/end_ordinal` + Codex adapter 差分计量)**已实现但无人调用**;而且 Codex adapter **把 `web_search_call` 直接丢弃**(`autoresearch/trace/transcripts/codex.py:37-39,292`),Codex 原生网查在 `external_tools.jsonl` 里会是零行。

---

## 1. capsule 现状

### 1.1 capsule 记录哪些证据(目录 → 写者 → 触发点)

| 证据 | 落点(`capsule/`) | 写者 | 触发/前提 |
|---|---|---|---|
| **run 身份** | `identity/run_contract.json`(与 workspace / staging 三份 byte-identical) | `capsule._write_contract_copies`(`capsule.py:131-141`),契约由 `scan.run_bootstrap.prepare_scan_run` 生成(`:443-453`,**scan 专属**,读 `scan_config.jsonc`) | `begin_run` |
| **代码/环境/prompt 快照** | `identity/{code.patch, untracked_sources.tar.zst, source_manifest.json, source_links.json, submodules.json, environment.json, dependencies.txt, snapshot_result.json, snapshot_inventory.json}` + `identity/prompts/**` | `identity.snapshot_identity`(`identity.py:2388-2457`);快照源目录 `_SOURCE_DIRS`=`autoresearch/ .claude/agents .claude/skills .claude/workflows`(`:90-95`),prompt 目录 `_PROMPT_DIRS`=`.claude/agents .claude/skills .claude/workflows`(`:96-100`),根文件 `pyproject.toml uv.lock AGENTS.md CLAUDE.md`(`:101-105`);prompt 落点 `prompts/<relative to .claude>`(`:1724-1730`,逐文件 `:2186-2236`);由 `capsule._record_identity_snapshot` 调(`capsule.py:292-345`,**只传 `engine`,不传 model/effort**,`:298-301`) | `begin_run` 之后立即(`:497`) |
| **事件 hash 链** | `events/events.jsonl` | `events.append_event`(`events.py:428-505`):`seq`+`prev_hash`+`event_hash`(`:54-56`),八个语义字段(`:23-34`),`verify_event_chain`(`:396-409`)。事件类型:`RUN_STARTED`(`capsule.py:171-186`)、`IDENTITY_SNAPSHOTTED`/`EVIDENCE_MISSING`(`:292-345`)、`STAGE_COMPLETED/FAILED/SKIPPED`+`CHECKPOINT_WRITTEN`(`:70-75,1363-1384`)、`AGENT_DISPATCHED/COMPLETED/FAILED`(`:63-65,752-819`;trace-control 自登记 `:822-913`)、`TRANSCRIPT_BOUND`(`:1565-1579`)、`TRANSCRIPTS_MATERIALIZED`(`:1947-1959`)、`COMMAND_STARTED/COMPLETED/FAILED/EVIDENCE_FAILED`(`exec_capture.py:56,1225-1242,968-979,1109-1129`)、`SOURCE_READ/FETCHED/FAILED`(`source_lineage.py:227-230,416-426`)、`RUN_<终态>`(`capsule.py:2548-2561`) | 各写点 |
| **exec 命令现场** | `events/invocations.json`(`exec_capture.py:294-300,327-386`)+ `logs/<stage>/<invocation>.{stdout,stderr}.log.gz`(`:1164-1167`,gzip `:434-461`,raw 回退 `:464-490`) | `exec_capture.run_captured`(`:1143-1274`):argv 数组、cwd、始末时刻、exit/signal、环境白名单(`:33-42`)、secret 只记 present(`:43-53,201-212`);子进程注入 `AUTORESEARCH_ENGINE/RUN_ID/STAGE/INVOCATION_ID`(`:1176-1184`);长命令期间续租心跳(`:761-763`) | 命令必须被 `python -m autoresearch.trace.exec_capture --run-id … -- <argv>` 包住(JS 的 `PY()` 壳:`.claude/workflows/l4-stock.js:72-75`、`scan-market.js:77-78`) |
| **阶段检查点** | `stages/<stage>/attempt-N/{inputs.json, outputs.json, result.json}` + `products/staging/<stage>/attempt-N/<root>/<path>`(逐产物 copy+sha256) | `capsule.checkpoint`(`capsule.py:1255-1385`;产物解析 `:1080-1105`,复制并验 stat 签名 `:1190-1252`) | python 侧 `scan.stage_result.safe_record_stage_result` 在 `AUTORESEARCH_RUN_ID` 在场时自动转 checkpoint(`autoresearch/scan/stage_result.py:191-217`);JS 不直接调 checkpoint(grep 无) |
| **精确读点 lineage + 内容寻址 blob** | `lineage/reads.jsonl`、`lineage/evidence_gaps.jsonl`、`blobs/sha256/<2>/<hash>` | `source_lineage.SourceAccess._persist`(`source_lineage.py:345-440`:endpoint/normalized_params/policy/access/path/blob_hash/normalized_blob_hash/rows/columns_hash/status);blob `blobs.put_bytes/put_file/put_dataframe`(`blobs.py:205-265`) | **只钩 `data.cache.get_or_fetch` 的返回点**(`autoresearch/data/cache.py:60-68` 的 `_source_trace`,无 `AUTORESEARCH_RUN_ID` 即 no-op;`trace_access` `source_lineage.py:536-595` 还要 `require_active_run` 成功)。绕过 cache 的 yfinance/akshare/tushare 裸调**不记**(设计稿自己承认 `…capsule-design.md:357`) |
| **agent transcript** | `agents/bindings.jsonl`(`capsule.py:1394-1395`)→ `agents/raw/<invocation>.jsonl.gz`(脱敏 gzip,mtime=0)+ `agents/normalized/<invocation>.json`(稳定 schema:message/tool_request/tool_result/error)+ `agents/index.json`(每个 reached invocation 一行 `PRESENT/GONE/UNSUPPORTED/NOT_EXPECTED` + coverage) | `bind_transcript`(`:1497-1580`)→ `_archive_bound_transcripts`(`:1695-1816`)→ `materialize_agent_index`(`:1868-1960`);`_NON_TRANSCRIPT_ROLES = {trace-control, gp-shell}`(`:1821`) | **绑定是唯一入口**:「locators may enumerate candidates but never promote one by mtime」(`:1510-1513`)。**全仓无生产写者**(见 §1.4) |
| **外部工具/网查留痕** | `lineage/external_tools.jsonl`(一行一次工具调用:`tool_name/request/url/result_hash/result_bytes/status/requested_at/completed_at/capture_level=HARNESS_RESPONSE`),响应正文进 blob | `capsule._external_tool_rows`(`:1630-1692`;判「外部工具」= 不在 `transcripts/base.LOCAL_TOOL_NAMES`(`base.py:18-53`);首个 URL 正则 `:1391,1620-1627`;响应 `put_bytes` `:1666`) | 从 **已绑定 transcript 的 normalized items** 抽取 → 前提同上 |
| **网查预算 / 外源证据索引(D-5)** | `lineage/web_budget.json` + `usage/web_budget.json`(同字节两份)、`lineage/external_evidence_index.json` | `web_budget.materialize_web_budget`(`web_budget.py:628-659`)、`evidence_index.materialize_evidence_index`(`evidence_index.py:511-540`);finalize 内 `_materialize_external_evidence`(`capsule.py:2348-2397`,B 级、失败记账不阻断) | 读 `external_tools.jsonl` + `agents/index.json` + `reads.jsonl` + `_claim_ledger.csv` + news_catalog;**finalize 未传 caps/self_reports**(`:2371-2374`)→ scan 侧 `cap=None`、`cap_exceeded=None` |
| **token 真计量** | `usage/_token_usage.json`、`usage/token_usage.md` | `capsule._write_usage`(`:2279-2291`)→ `usage_harvest.collect_run`(`usage_harvest.py:241-265`) | 走 adapter.locate(Claude 需 session_ref;Codex 读 bindings)|
| **业务产物快照** | `products/staging/**`(整个 staging 目录) | finalize `shutil.copytree`(`capsule.py:2482-2487`) | finalize |
| **完整性三件 + 重放** | `verification/{profile,expected,completeness,replay}.json` | `completeness.write_expected/write_completeness`(`completeness.py:336-373`);`replay.replay`(`replay.py:271-393`) | finalize(`capsule.py:2490-2502,2525-2532`) |
| **终态与封存** | `capsule.json`(`:2253-2276`)、`failure.json`(失败/中断,`:2504-2514`)、`verification/MANIFEST.sha256`(`:2007-2015`)、`verification/ROOT.json`(脱钩根,`:2706-2707`)、`$RPT/scan/_ledger/run_capsules.jsonl`(append-only 链,`:2094-2147`)、`$RPT/scan/_capsule_archive/<run>.tar.zst`(`:2150-2175`)、全树只读(`:2234-2242`) | `finalize`(`:2423-2703`,固定次序 docstring `:2433-2440`);中断恢复 `recover_stale_runs`(`:2856-2928`,租约 `process_probe`);补证 `repair` 叠加层 `_repairs/<run>/revision-N`(`:2962-3089`) | CP7 `post_run observe` 末尾自动 finalize+verify(`autoresearch/scan/post_run.py:360-396,1052`) |

### 1.2 三个结论各怎么算(互不替代)

`capsule.verify`(`capsule.py:2725-2780`)一次返回三个独立布尔/枚举:

- **完好性 `integrity_ok`** = `verify_manifest(final_path).ok`(逐文件 sha256 对 `MANIFEST.sha256`,`:2030-2047`)**∧** `root_ok`(当前 MANIFEST 字节 hash == `ROOT.json.root_hash`,`:2742-2745`);`:2763`。另单列 `root_ledger_ok`(账本最后 revision 的 root_hash 一致,`:2746-2751,2765`)与 `archive_ok`(`:2777-2779`)。
- **完整性 `completeness_ok`** = 读 `verification/completeness.json`(`:2752-2757,2768`),由 `completeness.evaluate` 算(`completeness.py:247-316`):`build_expected(profile)`(`:95-175`)把 `_BASE_RULES`(`run_profile.py:128-145`,按 `always/llm_run/failure/replayable`)+ 每个 reached stage 的 `stages/<s>/*/result.json` 与 `logs/<s>/*.{stdout,stderr}.log.gz` + 每个角色的 `agents/<role>/*` 展开成 REQUIRED / NOT_EXPECTED / NOT_REACHED;REQUIRED 逐项 `_capsule_hit`(glob 存在,`:185-188`)或 `_agent_hit`(index 里该 role 有 `PRESENT`,`:191-195`);**任一 REQUIRED 缺 → false**;且 **agent coverage `missing>0` → false**(设计稿 §8.5 规则 4,`:293-296`)。docstring 明写「deliberately never calls MANIFEST verification」(`:9-11`)。
- **可重放性 `replayability`** = 读 `verification/replay.json`(`:2770-2772`),由 `replay.replay` 算(`replay.py:271-393`):按 `contracts.stages.REPLAY_EXEC_UNITS`(`l0 → scan.frame`、`l1l2 → scan.universe`、`l5 → scan.assemble`,`replay.py:201-235`)在 scratch 里以 `AUTORESEARCH_REPLAY_CAPSULE` 环境重跑(`:292-298`),读数只走 `reads.jsonl` + blob(`frame_for` `:105-121`,缺即 `ReplayInputMissing`),产物按 canonical hash 对比(`:145-170`);`FULL`(全部命中)/`PARTIAL`(有 missing blob 或不一致)/`NONE`(没跑任何可重放单元);LLM 阶段恒 `EVIDENCE_ONLY`(`:308-317`)。

### 1.3 run_profile 有哪些 mode;有没有 analyze / stock-research 的 profile

- **词汇全部来自契约层**(`run_profile.py:10-15,21`):`MODES = ("FULL","FORCED_FULL","SENTINEL_EMPTY","SENTINEL_PINNED")`(`contracts/stages.py:106`,`run_profile.py:60`);只有 `SENTINEL_EMPTY` 不派 L4(`stages.py:110,188-190`);流水线阶段 `frame prelude gate1 l3 gate2 l4 l5 observe gate4`(`stages.py:57-84`);角色表 `strategist / sector-brief / l3-rank / l3-repair / l4-card / l4-intel / l4-ensemble`(`stages.py:148-161`);终态 `ACTIVE/SUCCEEDED/FAILED/INTERRUPTED`(`run_profile.py:61-66`)。
- **`scan_profile()` 是唯一 profile 工厂**,`kind="scan-market"` 写死(`run_profile.py:186-216`,`:207`);`completeness.profile_from_capsule` 也只会 `scan_profile()`(`completeness.py:319-333`);`capsule.finalize` 直接 `from autoresearch.scan.run_profile import scan_profile`(`capsule.py:2441,2490-2494`),mode 来源是 `staging/run_mode.json`(`_resolve_run_mode` `:2308-2345`,缺则按最宽 FULL 并记账)。
- **analyze / stock-research profile:无。** 没有 `analyze_profile`、没有 `stock-research` kind、没有 `company-intel/us-intel` role、没有 `harvest/write/assemble` stage;`contracts.stages.STAGES` 超集也不含任何 analyze 阶段(`stages.py:57-70`)。设计稿 §19 只留了接口(`RunProfile(kind, expected_stages, agent_roles, artifact_rules, replayable_stages)`,`…capsule-design.md:707-721`),并明说「一期不重写单股 full…只提供以后可接入的公共接口」(`:74`)。
- 唯一与「独立技能」相关的已实现件:`evidence_index.REPORT_CONTEXTS = ("macro_full","sector_full","stock_full")`(`evidence_index.py:99-102`)与 `materialize_report_trace(run_dir, context=…)`(`:546-613`,写 `<run_dir>/trace/{external_tools.jsonl, web_budget.json, external_evidence_index.json}`)—— **生产零调用者**(grep:仅 `tests/trace/test_evidence_index.py:252-398`)。

### 1.4 scan 内 L4 卡(= stock-research lite)在 capsule 里留下了什么

| 项 | 有没有 | 证据 |
|---|---|---|
| 确定性命令(preflight/prepare/intel-guard/record/failure…)的 argv/stdout/stderr/exit | **有** | 每条经 `PY()` 壳走 exec_capture(`l4-stock.js:72-75,199-229,257-260`)→ `logs/l4/*.log.gz` + `COMMAND_*` 事件 + `invocations.json` |
| agent 边界事件(l4-intel / l4-card 的 DISPATCHED/COMPLETED/FAILED) | **有** | `tracedAgent`(`l4-stock.js:175-195`)→ `emitAgentEvent`(`:158-173`)→ 一个 `gp_shell` agent 跑 `capsule agent-event … --control-invocation-id`(`capsule.record_controlled_agent_boundary` `:822-913`);每个目标 agent 固定 2 次 trace-control 壳(`l4-stock.js:119-123`) |
| 阶段 checkpoint | **有(python 侧)** | `l4_tasks` 等调 `safe_record_stage_result` → `capsule.checkpoint`(`stage_result.py:191-217`)|
| prompt 快照 | **部分** | agent def `l4-card.md`/`l4-intel.md` + `lite-playbook.md` 整目录进 `identity/prompts/`(`identity.py:96-99,2186-2236`);逐票任务包 `_l4_prompt_<code>.md` 是登记产物(`contracts/artifacts.py:124`)→ finalize 时随 staging 进 `products/staging/`(`capsule.py:2482-2487`)。**JS 拼出来交给 Agent 工具的那段 prompt 字符串本身**只存在于(未绑定的)transcript 里 —— 设计稿 §8.6「本次实际 rendered prompt」(`…capsule-design.md:404,410`)只做到任务包这一层 |
| slim / deep / intel / 卡 | **有** | `_slim.md`/`_slim_deep.md` 在 `_external_inputs`(`workspace.py:125-130`),`_l4_intel_<code>.md`、`details/<code>.md` 是登记产物(`artifacts.py:137,141`),随 staging 快照 |
| **subagent transcript 被 adapter 采集** | **机制在,生产接线为零** | 采集靠 `agents/bindings.jsonl`,唯一写者 `bind_transcript`(`capsule.py:1497-1580`);**生产 python / JS 零调用**(grep `bind_transcript\|bind-transcript` 只命中 `capsule.py` 自身 CLI 与 tests;`.claude/workflows/*.js` 无;`scan-market/SKILL.md` 与 `STAGES.md` 只在 `STAGES.md:241` 提「Codex 只认显式绑定」,没有任何操作步骤)。后果:`materialize_agent_index` 把每个 reached dispatch 标 `GONE: reached dispatch has no bound transcript`(`:1896-1897`)→ `completeness_ok=false`(`completeness.py:293-296`);验收文档的「五条业务腿各派发一次并**显式绑定**」是手工 CLI 合成(`docs/research/2026-08-27-scan-forensic-capsule-acceptance.md:60-70`),§7 明写生产全扫描**未跑**(`:118-135`) |
| **网查 URL / 内容落 blobs** | **机制在(`_external_tool_rows` 1630-1692),前提同上** | 无绑定 → `external_tools.jsonl` 零行 → `web_budget` 整份 `UNMEASURED`(`web_budget.py:474-490,559-562`,「没有任何外部工具行 —— 无法证明零查询」)|
| token 真计量(Claude) | **按码读:零行** UNVERIFIED | `usage_harvest.collect_run` 构造 `RunIdentity(run_id, engine)` **不带 session_ref**(`usage_harvest.py:252`),而 `ClaudeTranscriptAdapter.locate` 无 session_ref 直接 `return []`(`transcripts/claude.py:49-51`);`capsule begin` 在 SKILL 里也没传 `--session-ref`(`scan-market/SKILL.md:99-101`;CLI 支持 `:3124`)。SKILL.md:191-193 因此仍让 Claude 用旧口径 `--session <sessionId>` |

小结:L4 卡在 capsule 里**命令层与边界层是实的,判断层(transcript / 网查 / 计量)在两个引擎下都还是空的**——不是模块缺,而是「绑定」这一步没有生产主人。

---

## 2. stock-research 独立跑时的留痕现状 —— **零留痕**

### 2.1 事实

| 环节 | 现状 | 证据 |
|---|---|---|
| `python -m autoresearch.analyze.harvest` | 不开 run、不过 exec_capture、不记 checkpoint;输出 full md 到 `$CTX/<TICKER>_<date>.md`、slim 到 `ws.scan_input_dir(date)`(`harvest.py:1899-1906 _output_dir`,写盘 `:2049-2053`) | `grep trace|capsule|AUTORESEARCH_RUN_ID` 只命中 `import traceback`(`harvest.py:24`);SKILL 的命令是裸 `uv run …`(`.claude/skills/stock-research/SKILL.md:30,38`) |
| 取数 lineage | **即使**有人设了 `AUTORESEARCH_RUN_ID`(需要一个活的 scan-market run),也只有经 `cache.get_or_fetch` 的读会进 `reads.jsonl`(`cache.py:60-68`);harvest 大量块是裸调:`_ak_call`(`harvest.py:1291`)、`options_block`(`:407`)、`edgar_block`(`:576`)、`gnews_block`(`:694`)、yfinance 直取(`analyst_consensus :983`、`ownership_short :1064` 等) | 设计稿承认「直接绕过 get_or_fetch 的 yfinance/FRED/akshare/tushare 调用逐步接入」(`…capsule-design.md:357`);brainstorm K6 记 harvest 17 处手写表(`…brainstorm.md:81`) |
| `python -m autoresearch.analyze.assemble` | 只拼段落 + `parse_rating`(`assemble.py:33,228`),写 `$RPT/analyze/<YYYYMMDD_HHMM>/<name>.md` 与一份 6 键 `manifest.json`(ticker/name/market/analysis_date/generated_at/hhmm,`:214-226`);**无代码 hash、无 prompt hash、无 contract、无 stage_result** | grep `stage_result|record_stage` 在 `analyze/ macro/ sector/` 全空 |
| 主会话 Claude 写 14+ 段的工具调用 / 网查 | **无任何留痕**。主会话 transcript 在 `~/.claude/projects/<slug>/<session>.jsonl`(`usage_harvest.py:40,180-197`),没有任何东西绑定/复制它;`retention.retain()` 只挂 scan 发布(`STAGES.md:243`) | `engine-playbook.md:53` 只要求「WebSearch 实时网查的数字…须显式标注来源/日期」(指令级) |
| full 档 `company-intel` / `us-intel`(sonnet·max,`Write, WebSearch, WebFetch`,`company-intel.md:4-6`,`us-intel.md:4-6`) | 网查落点 = 稿件本身:`$CTX/analyze/<TICKER>_<日期>/_company_intel.md` / `_us_intel.md`(`company-intel.md:21`、`us-intel.md:20`、`engine-playbook.md:199-206`);预算 cap 12 **自报**(`company-intel.md:54`,声明行 `网查 <N> 条 ｜ … ｜ 契约 intel_v2_full` `:100`),源列必落 URL(`:58`);子 agent transcript 落 `~/.claude/projects/<slug>/<session>/subagents/agent-*.jsonl`(`usage_harvest.py:160-176,189-190`)**无人绑定** | brainstorm K8:「sector full / stock full / `_company_intel` / `_us_intel` / `_sector_intel` **零读者**」,v2 时效 lint「生产从不调用」(`…brainstorm.md:83`);§4.4「只有 l4-intel 有 python lint」(`:285`);`web_budget.SPEC_ROLE_CAPS` 有 `company-intel: 12 / us-intel: 12`(`web_budget.py:87-92`)但唯一消费者是零调用的 `materialize_report_trace` |
| claim ledger(`_claim_ledger.csv`) | `materialize_report_trace` 会读 `<run_dir>/_claim_ledger.csv`(`evidence_index.py:597-601`),但 analyze 侧无写者(grep 空);它被列在契约层「不是产物」白名单(`contracts/artifacts.py:261`) | 外源稿 §1 病 3:「claim → observation 的语义账 `claim_ledger` 建成未接线」(`…external-evidence-expansion-design.md:50`) |
| lite 档独立跑(「快速看一眼」) | 同上零留痕;卡落 `$RPT/analyze/<YYYYMMDD>_<HHMM>/<名称>_lite.md`(`SKILL.md:39`),卡内 ≤1/≤3+≤2 有界网查(`lite-playbook.md:15-16`)只有卡面「网查」级断言的引句+来源+日期(`:20`) | — |

### 2.2 要接 capsule 需要碰哪些模块、run_profile 需要加什么

**契约层(先做,零运行器)**
- `contracts/stages.py`:新增 kind 维度(今天没有 `KINDS`)、analyze 的阶段词汇(建议 `harvest → intel → write → assemble → publish`,lite 无 `intel/write` 只有 `harvest → card`)、角色表加 `company-intel`(gated: A 股)、`us-intel`(gated: 美股)、以及一个**主会话写手角色**(`stock-writer`,Claude 下是主会话、Codex 下是 rollout 片段;或按 `_NON_TRANSCRIPT_ROLES` 处理)、模式 `FULL / LITE`;`roles_in_stages/skips_l4` 这类派生要按 kind 分表,否则 `test_stage_vocabulary.py` 的 `is` 断言(`:68-70,83-85,111-112`)会把 scan 的表连带改掉。
- `contracts/artifacts.py`:`ROOTS` 只有 `staging/report/ledger/capsule`(`:66`)且语义全是 scan(`:28-35`)→ 加 `analyze_staging`(`$CTX/analyze/<T>_<D>/`)与 `analyze_report`(`$RPT/analyze/<run>/`);登记 full md、`_slim.md`/`_slim_deep.md`(今天在**非产物白名单** `:263`,要从白名单挪出,`test_allowlist_does_not_shadow_the_registry` `:41-45` 会逼)、`_company_intel.md`/`_us_intel.md`、11 个必需段文件(`engine-playbook.md:40-49`)、报告 md、`manifest.json`、lite 卡;drift 守卫 `_SCAN_ROOTS` 加 `autoresearch/analyze`(`tests/contracts/test_registry_parity.py:187`)。
- `scan/run_profile.py`(或新 `analyze/run_profile.py`):`analyze_profile(mode)`,`kind="stock-research"`,`_BASE_RULES` 可原样复用(选择器都是 capsule 相对路径 `:128-145`),`replayable_stages` = harvest(**前提**是 harvest 走湖,否则 replay 读不到 blob 恒 PARTIAL)。

**trace 层「去 scan 化」(七个硬编码点)**
- `capsule.begin_run` 的 `kind != "scan-market"` 拒绝(`capsule.py:428-429`)与 `prepare_scan_run`(`:443-453`,读 `scan_config.jsonc`、写 `stage_budgets`)→ 按 kind 派发 bootstrap;
- `load_run` 根 `context_root()/"scan_runs"`(`:534`)与 `workspace.scan_run_root`(`workspace.py:98-102`)→ `run_root(kind)`;
- `_validate_report_dir` 只认 `reports_root()/"scan"`(`:920`);
- `ledger_path/failed_root/archive_root/repairs_root`(`:1983-1992,2947-2948`)全带 `scan`;
- `_resolve_run_mode` 读 `staging/run_mode.json` + `run_profile.MODES`(`:2308-2345`)→ analyze 的 mode 来源要另定(建议写进 RunContract);
- `finalize` 的 profile 选择(`:2490-2494`)与 `completeness.profile_from_capsule`(`completeness.py:319-333`)→ 按 `run_contract.run_kind`(`scan/run_contract.py:23` 已存字段)派发;
- `replay.default_stage_specs` 只认 scan 单元(`replay.py:201-235`,与 `vocab.REPLAY_EXEC_UNITS` 对账,多一个不认识的会抛)。

**analyze 层三个接线点**
- `harvest.main`(`harvest.py:1909-2054`):进程内 `safe_record_stage_result`-等价 → checkpoint;SKILL 命令改经 `exec_capture`;裸取数块改走 `cache.get_or_fetch`/`trace_access`(= brainstorm A3 `stock_pack`,`…brainstorm.md:113-117`)。
- `assemble.main`(`assemble.py:146-235`):checkpoint + 把 `$RPT/analyze/<run>` 当 `report_dir` 交 `finalize`;`manifest.json` 加 `run_id/contract_hash/evidence_status`(设计稿 §6.2 对 scan 的同款要求 `…capsule-design.md:180`)。
- **transcript 绑定**成为流程步(§4(d)/§6 详述)—— 没有它,上面全部接完 `completeness_ok` 仍恒假。

> 替代的「轻路」:直接调 `materialize_report_trace(run_dir, context="stock_full", external_tool_rows=…)`(`evidence_index.py:546-613`)只产三件 trace 文件、不建 capsule。但它需要 `external_tool_rows`,而抽取器 `_external_tool_rows` 是 `capsule.py` 私有函数且吃 `RunHandle`(`:1630-1634`),同样绕不开「先绑定 transcript」。

---

## 3. 契约层

### 3.1 ARTIFACTS 登记表里有没有 analyze 产物 —— **没有**

- `ARTIFACTS`(`contracts/artifacts.py:71-199`)全部是 scan:frame / prelude / L0-L2 / 控制面 / sector 旁路 / L3 / L4 prep / L4 / L5 / 发布目录 / observe / 账本 / capsule 七件;root 只有 `staging | report | ledger | capsule`(`:66`),含义写死为 `ws.scan_dir(date)` / `reports_<engine>/scan/<run_id>/` / `reports_<engine>/scan/_ledger/` / capsule(`:28-35`)。
- analyze 相关名字反而出现在**「不是产物」白名单** `NON_ARTIFACT_LITERALS`(`:229-271`):`_slim.md`、`_slim_deep.md`(`:263`)、`_claim_ledger.csv`(`:261`)、`.claude/skills/stock-research/lite-playbook.md`(`:244`)。白名单纪律「只许减不许增」(`:229-233`)。
- `by_name/for_stage/for_root/replayable/paths`(`:204-226`)。

### 3.2 emit 机制怎么用

- 目标:**行内代码块**,不是旁边一个生成文件(初版 `_contracts.generated.js` 因三个 workflow 从不 import、Workflow 运行时无文件系统而零消费者,2026-08-30 删除重做,`contracts/emit.py:20-30`)。
- 标记 `// ── <contracts:begin> ──` / `// ── <contracts:end> ──`(`:45-46`),块内由生成器拥有;当前只有一个目标:`.claude/workflows/l4-stock.js` 的 `rank`(评级名次 JS 反向视图 `agent_output.js_rank_view` `:178-184`)与 `transient`(`retry.INTEL_RESEARCH`)(`WORKFLOW_BLOCKS` `:50-52`,渲染 `:55-79`)。
- 三个命令(`:32-34,131-146`):`python -m autoresearch.contracts.emit`(打印)/`--write`(同步进 workflow)/`--check`(CI:不一致 exit 1)。守卫 `test_workflow_contract_block_is_in_sync`(`tests/contracts/test_registry_parity.py:238-251`)+ 变异自证 `test_block_sync_guard_bites`(`:263-272`)+ 「每个常量恰一份」(`:275-279`)。

### 3.3 新产物登记流程(与 memory「新产物先进 ARTIFACTS 再写代码;改 contracts 要 emit --write」对应的机器判据)

1. 在 `ARTIFACTS` 加一行 `Artifact(name, path, root, stage, producer, kind, presence, required_when?, replayable?)`;`stage ∈ stages.STAGES`、`root ∈ ROOTS`、`kind ∈ KINDS`、gated 必须写 `required_when`(`test_registry_is_internally_consistent` `:26-38`);名字不得与白名单重叠(`:41-45`)。
2. 再写代码。drift 守卫 `test_no_unregistered_artifact_literals`(`:199-219`)grep `autoresearch/scan`、`autoresearch/trace`、`.claude/workflows` 里形如 `"x.csv|json|md|txt"` 的字面量(`:186-196`),不在登记表也不在白名单 → 红;自证 `:222-233`。**注意 `autoresearch/analyze` 不在扫描根内** —— analyze 今天的产物字面量(`decision.md`、`manifest.json`、`_company_intel.md`…)完全不受守卫。
3. 若改的是 `agent_output` / `retry`(会进 JS 块)→ `emit --write`,再 `--check`。
4. parity 兜底:`scan/artifacts.CRITICAL_ARTIFACTS` ⊆ 登记表且路径逐字相等(`:50-58`);`MODES` 与 `run_mode.MODES` 同集(`:61-65`);JS 阶段串全部可归一到 `STAGES`(`:81-89`)。

### 3.4 守卫测试是什么(三文件)

- `tests/contracts/test_layering.py`:AST import 矩阵;层序 `contracts < common/dataflows/agents < data < trace < news/derivatives/dossier < sector/macro/analyze < scan < research/ops`(`:33-42`);`contracts` 一条向上边都不许(`:100-103`);向上边只许减不许增,存量清单 `KNOWN_UPWARD`(`:48-66`,含 `trace→scan` 成环、`data→trace`、`sector/dossier/derivatives→scan`);清单不许有陈旧项(`:118-124`);变异自证(`:127-131`)。**analyze 位于 scan 之下** → 未来 analyze 接 capsule 时 `analyze → trace` 是向上边(trace 在 analyze 之上? 不是:trace 层 3,analyze 层 5,analyze→trace 是向下,合法;`analyze → scan.run_profile` 才是向上,须把 profile 放 contracts 或 analyze 自身)。
- `tests/contracts/test_registry_parity.py`:登记表卫生 + parity + 卡契约与 `l4/parsers` 对拍(`:151-176`)+ drift + JS 块同步 + 两套重试口径「故意不同」(`:284-307`)。
- `tests/contracts/test_stage_vocabulary.py`:`TODAY_*` 是 2026-08-29 四份表的逐字副本(`:29-63`);关键断言用 `is`(`:70,85,91,112,148`)防「等值副本」;变异探针(`:136-141,177-187,196-207`);`_BASE_RULES` 选择器里若出现阶段/角色名必须来自词汇表(`:218-233`,对应 `run_profile.check_rule_vocabulary` `:156-183`)。

### 3.5 状态(计划台账)

`docs/superpowers/plans/2026-08-29-full-coverage-p0-p1.md:30-34`:Wave 1 T1–T7 + Wave 2 T8/T10/T12 已做(4641 绿);契约层四个 commit 已提交,T1–T7 留工作树;**T9(五份登记表全部派生,`:641-642`)与 T11(评级序单源,`:647-648`)未做;A6(配置按引用 + run 分区唯一可写根)不在计划**(`:657`)。与 stock-research 直接相关:A6 提到的 `ws.stage_dir(kind ∈ {scan, sector, analyze, macro, news}, date)`(`…brainstorm.md:134`)就是 analyze 进 run 分区的设计落点,目前没有。

---

## 4. Codex 一致性现状

### (a) 引擎判定与路径分根

- `workspace.detect_engine`(`autoresearch/common/workspace.py:44-58`):① `AUTORESEARCH_ENGINE`(非法值直接 raise)② `CLAUDECODE` → claude ③ 任一 `CODEX_*` → codex ④ 默认 claude;进程级一次定死 `ENGINE`(`:62`);根 `context_<engine>/`、`reports_<engine>/`、共享 `lake/`(`:65-77`)。**不得写进 `.env`**(`:17-19`);Codex 侧保险丝 = `AGENTS.md` 要求会话第一件事 `export AUTORESEARCH_ENGINE=codex`(`AGENTS.md:24`);launchd 脚本显式默认 claude(`scripts/nightly_close.sh:25-30`)。裸根字面量 grep 探针(`workspace.py:23-24` → `tests/common/test_workspace.py`)。
- capsule 侧强制:`begin_run` 要 `engine == ws.ENGINE`(`capsule.py:431-434`),`load_run` 校 contract.engine(`:579-582`),事件校 engine ∈ ENGINES(`events.py:132-134`),replay 透传 `AUTORESEARCH_ENGINE`(`replay.py:298`),exec_capture 注入子进程(`exec_capture.py:1176-1184`)。

### (b) Codex 下 skill 怎么被触发

- **软链**:`~/.codex/skills/{macro-research,scan-market,sector-research,stock-research}` → `.claude/skills/<同名>`(实测 `ls -la`,四条均为 symlink,2026-07-13 建;`AGENTS.md:14-20` 的循环即此)。Codex 原生 skill 发现按 `<name>/SKILL.md`,所以**同一份 SKILL.md 文本**在两边触发;SKILL.md 头部的 `$CTX/$RPT` 约定(`stock-research/SKILL.md:6`、`engine-playbook.md:3`)就是为此写的。
- repo 内 **没有** `.codex/`、`.agents/`、`codex*.md`;只有根 `AGENTS.md`(38 行)作为 Codex 操作手册:技能表(`:7-12`)、软链(`:14-20`)、五条适配规则(`:24-28`)、常用入口(`:32-37`)。`~/.codex/AGENTS.md` 只有 superpowers 一段;`~/.codex/config.toml` 存在(model `gpt-5.6-sol`、`model_reasoning_effort = "xhigh"`、本项目 trusted;**文件内含一条 MCP 凭证,本报告不复制**)。
- 触发路径之外的差异:Codex 没有 `Workflow`/`Agent` 派发原语,`AGENTS.md:26` 规定「按同一顺序自己在会话内完成各角色…门必须跑,产物契约不许改」。

### (c) `.claude/agents/*.md` 在 Codex 下的等价物 —— **没有**

- `~/.codex/agents/` 是**空目录**(实测);10 个 def(`company-intel / us-intel / l4-card / l4-intel / l3-rank / dossier-init / global-intel / macro-brief / sector-brief / sector-intel`)没有任何 Codex 侧镜像、生成物或 emit 目标(`emit.WORKFLOW_BLOCKS` 只有 `l4-stock.js`,`emit.py:50-52`)。Codex 能否派子 agent:按 `AGENTS.md:26` 与本仓设计,**不假定能**;设计稿的处理是「主会话在非 Claude harness 下承担…角色时,必须通过 role boundary 事件把同一 session 切成可审计片段」(`…capsule-design.md:393`)。
- **已实现但无人用的等价机制**:`TranscriptRef.start_ordinal/end_ordinal`(`transcripts/base.py:103-132`)、`CodexTranscriptAdapter._segment`(`codex.py:153-168`)、片段差分计量(`:376-392`)、CLI `bind-transcript --from-ordinal/--to-ordinal`(`capsule.py:3142-3143`)。
- **full 档「情报员 + 写手」在 Codex 下怎么退化**:`company-intel`/`us-intel` 的设计价值在于**独立 context + 无 Read 的结构性盲**(`company-intel.md:9,21`:「盲于报告论点…你没有 Read/Grep/Glob」)。Codex 单会话代偿时:① 盲性消失(同一 context 先查后写,确认偏误防线只剩指令级);② `cap 12` 只剩自报(`:54,100`);③ 时序上「先情报后各棒」仍可保持;④ 产物契约 `_company_intel.md` 可照写(它是 Write 产物),下游 `intel_recency_lint(v2_full)` 本来就零生产调用(`…brainstorm.md:83`),所以**机器可见的差异 = 0,人不可见的差异 = 盲性**。sector/macro full 的 `sector-intel`/`global-intel` 同理(`global-intel` 甚至没有派发文本,`:285`)。
- **Codex 网查留痕的独立缺口**:`CodexTranscriptAdapter.normalize` 把 `response_item.type ∈ {reasoning, web_search_call, encrypted_reasoning}` 整体跳过(`codex.py:37-39,292`),只保留 `function_call/custom_tool_call` 与其 output(`:40-41,308-333`)。Codex 原生 `web_search_call` 因此**永远不会成为 `external_tools.jsonl` 的一行**,`web_budget` 对 Codex 的 `OBSERVED_ONLY`(`web_budget.py:29-33`)实际观测不到原生搜索。外源稿 §6.5 只说「Codex 没有同一 hook 契约…先记 OBSERVED_ONLY」(`…external-evidence-expansion-design.md:279-283`),没提这一层。

### (d) transcripts 两个 adapter 采什么、字段差异

| 维度 | Claude(`transcripts/claude.py`) | Codex(`transcripts/codex.py`) |
|---|---|---|
| 定位 | `~/.claude/projects/<slug>/<session>.jsonl` + `<session>/subagents/**/agent-*.jsonl`,**需要 `session_ref`**(`:22-23,49-81`;无则 `[]` `:50-51`) | **只读 `capsule/agents/bindings.jsonl`**(`:105-134`);候选枚举 `locate_candidates` 0→`GONE`、>1→`AMBIGUOUS`、1→`CANDIDATE`(仍需显式绑定,`:59-92`) |
| 行 schema | 每行 `message.content` 块:`text/tool_use/tool_result`;流式重复按最后一个 `message.id` 去重(`:150-158,188-189`);错误行 `error/isApiErrorMessage`(`:174-183`) | `type ∈ session_meta/turn_context/response_item/event_msg`(`:3-18`);`message/agent_message`、`function_call/custom_tool_call`(+`_output`)(`:40-42,294-333`);**跳过 `reasoning/web_search_call/encrypted_reasoning`**(`:37-39,292`) |
| model / effort / agent | `message.model`(排 `<synthetic>`)、行内 `effort`、`attributionAgent` 或 `.meta.json.agentType`(`:38-47,94-101,120-126`) | `turn_context.payload.model`、`collaboration_mode.settings.reasoning_effort`(`:175-192`);`agent = ref.role`(`:339,362`) |
| 状态 | `stop_reason ∈ {end_turn, stop_sequence}` + error 行 → `SUCCEEDED/FAILED/RETRIED_SUCCEEDED/INCOMPLETE`(`:106-118`) | `event_msg.task_complete`(带 error 即失败)/`error`/`stream_error`(`:206-235`);无快照 → `UNMEASURED`(`:350-370`) |
| usage | 按 `message.id` 取最新 usage 求和:`input_tokens / output_tokens / cache_read_input_tokens / cache_creation_input_tokens`(+`ephemeral_1h/5m` 拆分)(`:262-272`);`messages = len(latest)`;`reasoning_output` 恒 0(`base.py:178` 默认) | 取**累计**快照 `event_msg.token_count.info.total_token_usage` 的最后一个(片段则减去 `start_ordinal` 前最后一个快照)(`:194-204,343-392`);🚨 **`input = input_tokens − cached_input_tokens`**(`:13-18,393-398`,实测 `total == input + output` 且 `cached ≤ input`;测试 `tests/trace/test_transcript_adapters.py:150-167` 锁 408129 真值);`cache_read = cached_input_tokens`、`cache_create = cache_write_input_tokens`、`cache_create_1h = 0`、`reasoning_output = reasoning_output_tokens`(`:399-413`);`messages = len(snapshots)`(`:397`) |
| 工具行关联键 | `tool_use_id`(`:221,231`) | `call_id`(同时写 `tool_call_id`,`:312-313,327-328`);`tool_call_id()` 读 `tool_call_id/call_id/tool_use_id` 三选一(`base.py:56-62`) |
| 限频 | `OBSERVED_ONLY`(`HARD_CLAUDE` 仅 spike 后可标)| `OBSERVED_ONLY`(`web_budget.py:29-33,56-59`) |

夹具:`tests/trace/fixtures/claude/agent-l4-card.jsonl`、`tests/trace/fixtures/codex/rollout.jsonl`;验收文档 §1.3 记 Codex `estimated_usd=None`(未定价不渲染 `$0`,`…acceptance.md:70-78`)。

### (e) `.claude/skills/*` 与 Codex 之间的同步/重复维护

- **skills:软链 = 单份真身,零重复**(`AGENTS.md:14`「软链 = 两边改任一侧即同步,勿复制」)。
- **agent defs / workflows:Claude 独占,无同步、无等价**(见 (c));`identity._PROMPT_DIRS` 不分引擎地快照 `.claude/*`(`identity.py:96-99`),所以 Codex 跑出来的 capsule 里也只有 Claude 的 def(算「代码快照」而非「Codex 实际用的 prompt」)。
- **手写重复**:每份 SKILL/playbook 头部各自重复一段 `$CTX/$RPT` 路径约定(`stock-research/SKILL.md:6`、`engine-playbook.md:3`、`scan-market/SKILL.md` 等),与 `AGENTS.md:24` 第三份;由 `tests/test_skill_docs_refs.py`(计划 T7 Step 8 引用,`…p0-p1.md:583`)做 doc-lint,覆盖范围 UNVERIFIED。契约层 emit 不生成任何 doc 片段(brainstorm A6 的 `emit-doc-snippet` 未做,`…brainstorm.md:134`)。
- **Codex 专属流程文字只在 `AGENTS.md`**(38 行)+ `scan-market/STAGES.md:241,252`、`SKILL.md:191-193` 三处零散句;stock-research 的 SKILL/playbook **没有任何一句 Codex 特有指引**(只有路径约定)。

---

## 5. 08-29 brainstorm 里对 stock-research 直接相关的结论(引原文位置)

| 主题 | 原文位置 | 要点 |
|---|---|---|
| **trace-control 壳开销** | `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md:60-62`(§1.4) | `l4-stock.js` 每条确定性壳被 `tracedAgent` 包住 → 「每条命令 = 3 次 agent spawn,且壳的边界由另外两个壳来记」;`scan-market.js` ≈30–34 壳、`l4-stock.js` 每股 22 壳(+5/+7),10 只 ≈ **250–320 壳/趟**;08-25 实测 47 壳 $5.90(≈$0.125/壳)→ 外推 **$31–40/日 UNVERIFIED**;与 `run_profile.py:25-26`「relay 的证据是命令不是 transcript」自相矛盾。修法 A5(`:125-130`):进程内串行器 + `DISPATCH_INTENT/OUTPUT_OBSERVED` 两个观察事件,真实 `DISPATCHED/COMPLETED` 只由能看见 harness 的 adapter 写;壳数 → ≈40 + 业务 agent;**需用户裁 Q2**(`:421`),前置 Q12 计量跑(`:431`)。→ 对 stock-research 的含义:full 档只有 ~3 条确定性命令 + 1–2 个 agent,若照抄 scan 的 `tracedAgent` 模式每个 agent 再付 2 壳;走 A5 的进程内 API(`stage_result → checkpoint` 那条路已存在,`stage_result.py:191-217`)才不重蹈。 |
| **取数绕湖** | `:56`(§1.3 第一行,生产帧 8/9 条 tushare 裸调,A 级契约只在 prewarm/backfill/doctor 跑过)+ `:81`(K6:`analyze/harvest.py` 17 处手写表、17 处各自解析日期,`_ak_call` ×3 份,`tushare_source._pro/_ts_call/…` 被 14 文件私有 import 含 `common/uzi_lenses.py:258-323`) | 对 stock-research:harvest 的裸取数不仅是重复代码,也是**留痕盲区**(不经 `get_or_fetch` 就没有 `reads.jsonl`、没有 blob、replay 不可能 FULL)。A3 `stock_pack`(`:113-117`):`analyze/harvest` 的 slim/full = 同一构建器两个 profile,`load_<pack>(request, reader)` 读 IO、`build_<pack>(raw, decision_cutoff)` 纯函数,`PackMeta` 时间信封(`data_as_of / available_at / decision_cutoff / sources / degradations / freshness`),golden = 08-26 capsule 冻结 slim 逐字节;`harvest.py` 保留薄 shim 一轮。 |
| **契约优先七构件里覆盖 analyze 的** | A1 `:98-104`(登记表——今天零 analyze 行);A2 `:106-111`(agent 产出语法——`**Rating**`/`FINAL TRANSACTION PROPOSAL` 只登记为 `l4-card` 契约,`agent_output.py:89-108`;full 的 PM `decision.md` 用同一对锚,`assemble.py:228` 走 `parse_rating` 宽松档;`IntelContract(version ∈ {v1, v2_full, v2_macro})` 单源 + def 生成块,修「恒 v1」);A3 `:113-117`(`stock_pack`);A4 `:119-123`(端口表含 `intel(v1|v2)`、新 `stock_state`);A5 `:125-130`;A6 `:132-136`(`ws.stage_dir(kind ∈ {scan, sector, analyze, macro, news}, date)` 把 analyze 收进 run 分区);A7 `:138-142`(分层:analyze 不得 import scan) | 已落地的只有 A1/A2/A7 的 scan 部分与 A1 的 JS 行内块;**analyze 一件都没沾**。 |
| **full 档三技能是死胡同** | §4.7 `:308-320`:「今天三海拔的 full 档全是**用户手触发 + 零机器消费者**(stock full / `_company_intel` / `_us_intel` … 全是死胡同)」;提议「定频 + 端口」:微观 full 频率 = 📌 全部 + 20 日内 ≥2 次 finalist,端口 `stock_state` = 档案 §1–§4 刷新,消费者 = L4 📚 摘要;§4.7.1 `:322-328` 研究队列 `research_queue.json` 与背压;Q10 `:429`「四个 full 档情报 def 无读者则退役」 | 与「保留现场以便复盘」直接相关:**没有端口的现场是没人复盘的现场**。 |
| **情报家族收编** | §4.4 `:285`(五个情报 def 同骨架复制粘贴,契约名拼写不一致,cap 20/12/12/6/8,只有 l4-intel 有 lint);§4.8 `:330-332`(一份模板 + 四组参数,由 A2 生成;`external_tools.jsonl → web_budget.json` 是唯一计量单位) | 对 company-intel/us-intel:cap 与契约要进 A2,不再各写一份。 |
| **K8 零读者** | `:83` | `sector full / stock full / _company_intel / _us_intel / _sector_intel` **零读者**;v2 时效 lint 生产从不调用(`report_sections.py:803,1009` 恒 v1)。 |
| **建成未接线家族** | `:57`(§1.3 第二行) | 「`trace/evidence_index.py`、`trace/web_budget.py`(D-5)在 `autoresearch/` 内零调用者」—— scan 侧已由 T1 接线(`capsule.py:2348-2397`);**独立技能侧的 `materialize_report_trace` 仍是零调用者**。 |
| **矩阵「stock full」列** | §5 `:338-359` | 海外 ◐(代码在、EDGAR 无 UA、映射 32 条全 pending)、公告 ◐、新闻 ✓、日历 ✓、衍生品 ✗(裁定)、盘中 ✗;§5.1 `:363-377` 成熟度 C0–C4,**C3 = 「capsule lineage 能证明消费者读过哪些字节」**—— stock full 今天连 C3 的仪器都没有。 |
| **依赖方向** | `:84`(K9)、`:147-157` 目标分层 | `trace` 只 import `contracts`,不 import `scan`(今天 `trace→scan` 8 行成环,`tests/contracts/test_layering.py:48-52` 记为存量);analyze 若要用 capsule 必须等 profile 声明搬进 `contracts`,否则 `analyze → scan.run_profile` 是新的向上边,`test_no_new_upward_edges` 会红(`:106-115`)。 |

外源扩面稿对 stock-research 的直接约束补两条:`_company_intel`/`_us_intel` 的落点、cap、面(`…external-evidence-expansion-design.md:155-156,294-299`);D-5 验收原话「scan finalize 与 **full-report publish 两个 adapter** 的 `web_budget.json` 均覆盖」(`:427`)—— 第二个 adapter 今天没有 publish 调用点,该验收对 stock full **未满足**(状态行 `:3` 说「D-5 已实施」是只对 scan 成立的说法)。

---

## 6. 一句话结论 + 最短三步

**结论**:stock-research 今天在「现场留存」上是 0(harvest/assemble/主会话/情报员四处都没有机器留痕),在「Codex 一致性」上只有路径与 skill 文本一致、没有 agent 与 transcript 一致;而 capsule 的全部机制(begin/checkpoint/exec_capture/lineage/bind/materialize/finalize/verify)都是 kind-无关的代码 + 七处 `scan` 字面量的壳,**最短路径不是给 stock-research 再造一套 retention,而是把 kind 从 capsule 的字面量里抽出来、把 transcript 绑定做成两个引擎共用的一句流程命令**:

1. **契约先行(零运行器,I 类,今天可做)**:`contracts/stages.py` 加 kind 维度与 analyze 的阶段/角色/模式(`harvest → intel → write → assemble → publish`;`company-intel`/`us-intel` gated by 市场;主会话写手按 `_NON_TRANSCRIPT_ROLES` 或片段绑定;`FULL/LITE`),`contracts/artifacts.py` 加 `analyze_staging/analyze_report` 两个根并把 `_slim*.md` 从白名单挪回登记表、登记 11 段 + intel 稿 + 报告 + manifest,drift 守卫 `_SCAN_ROOTS` 加 `autoresearch/analyze`;`analyze_profile()` 复用 `_BASE_RULES`。守卫会替你逼齐(`test_registry_parity.py:26-58,199-233`;`test_layering.py:106-115`)。
2. **capsule 去 scan 化 + analyze 三个接线点(I 类)**:按 `run_contract.run_kind` 派发 `begin_run/bootstrap`(`capsule.py:428-429,443-453`)、`load_run`/根路径(`:534,920,1983-1992,2947-2948`,`workspace.py:98-107` → `run_root(kind)`)、`_resolve_run_mode`(`:2308-2345`)、`finalize` profile(`:2490-2494`)、`completeness.profile_from_capsule`(`completeness.py:319-333`)、`replay.default_stage_specs`(`replay.py:201-235`);然后 SKILL 三条命令走 `exec_capture`,`harvest.main`/`assemble.main` 进程内 checkpoint(照 `stage_result.py:191-217`),harvest 裸取数改经 `cache.get_or_fetch`(A3 `stock_pack`),assemble 收尾 `finalize(report_dir=$RPT/analyze/<run>)`。**不要**照抄 `tracedAgent` 双壳(brainstorm §1.4/A5,Q2 未裁)。
3. **transcript 绑定成为流程步,双引擎同一句(这一步不做,前两步的 `completeness_ok` 永假)**:`capsule begin … --session-ref <id>`(Claude = session id;Codex = rollout 路径)+ 每个角色收尾一句 `capsule bind-transcript <run> <path> --role company-intel --invocation-id …`(Claude 子 agent 路径来自 `~/.claude/projects/<slug>/<session>/subagents/`;Codex 单会话用 `--from-ordinal/--to-ordinal` 切角色片段,`base.py:103-132` / `codex.py:153-168` 已支持);顺手修两处:`usage_harvest.collect_run` 把 contract 的 `session_ref` 传进 `RunIdentity`(`usage_harvest.py:252` vs `claude.py:50-51`),`CodexTranscriptAdapter` 不再跳过 `web_search_call`(`codex.py:37-39,292`)。做完这一步,已接线的 D-5(`capsule.py:2348-2397`)会自动给 stock full 产出 `web_budget.json` / `external_evidence_index.json`,`company-intel` 的「网查 12 条」第一次有真值而非自报,而 Codex 侧「主会话切片段」的等价性也第一次落到证据上 —— 这也是 scan 自己的 L4 卡今天同样缺的那一块。

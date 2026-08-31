# W4 调研:LLM 股票研究的现场留存(trace / provenance / replay)——标准、Claude Code 与 Codex 采集面、对齐建议

调研日 2026-08-30/31。方法:官方文档 WebFetch 原文核实 + **本机实测探针**(Claude Code 2.1.251 transcript 实拆、Codex CLI 0.151.0 rollout 实拆、`codex features list` 实跑)。凡未核实处显式标注「未核实,需探针」。证据分级:【F】=已 fetch 原文核实;【P】=本机探针实测;【S】=仅搜索摘要。

---

## ① 一页摘要:10 条最有用发现

1. **OTEL GenAI 语义约定 2026-06(v1.42.0)整体迁出主仓**,独立为 `open-telemetry/semantic-conventions-genai` 仓;这是组织性拆分,**不是升 stable——截至 2026-07/08,所有 `gen_ai.*` 属性/span/事件仍全部是 Development 状态**(spans 文档里 287 个 Development 徽章、0 个 Stable)。结论:值得**借它的字段名字典**(唯一厂商中立词表),不值得锚定它的版本稳定性。【F+S】
2. **内容捕获的行业共识已定型**:prompt/completion 不再走独立 event,而是 span 属性 `gen_ai.input.messages` / `gen_ai.output.messages`,结构为 `[{role, parts:[{type:"text"|"tool_call"|"tool_call_response", id, name, arguments, response}]}]`,且**默认不采、opt-in 采**。工具调用三件套 `gen_ai.tool.call.id / .arguments / .result` 也已有属性名和 JSON schema。我们 capsule 的事件字段名可以直接映射到这套词表。【F】
3. **Claude Code 的 OTEL 遥测能拿到工具调用内容**:`claude_code.tool_result` 事件在 `OTEL_LOG_TOOL_DETAILS=1` 下带 `tool_input`(单值>512 字符截断、全文 ~4K 上限);更狠的是 `OTEL_LOG_RAW_API_BODIES=file:<dir>` 把**每次 Messages API 完整请求/响应 JSON 不截断落盘**(`<uuid>.request.json` / `<request_id>.response.json`),等于官方版「API 层现场留存」。Traces(spans)是 beta(`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`),span 含 `claude_code.interaction/llm_request/tool/tool.execution/hook`,并向 Bash 子进程注入 W3C `TRACEPARENT`。【F】
4. **Claude Code hooks 已扩到 33 种事件**(不止 PreToolUse/PostToolUse/Stop:还有 PostToolUseFailure、PostToolBatch、SubagentStart/Stop、PreCompact/PostCompact、InstructionsLoaded、FileChanged、PreModelSwitch 等);PostToolUse 输入含 `tool_response`,且可用 `updatedToolOutput` 改写工具输出;matcher 可精确匹配 `WebFetch`/`WebSearch` 工具名。**用 PostToolUse 落盘 WebFetch 结果可行——但落的是「小模型摘要后」的结果,不是网页原文**(原文在任何采集面都拿不到,见 #5)。【F】
5. **WebFetch 的原始页面内容在 Claude Code 里结构性不可得**:tools-reference 明说 WebFetch 是「取页→截断→小模型按 prompt 提取→Claude 只看到提取结果」,transcript 里只存 `toolUseResult{bytes, code, codeText, durationMs, result, url}`(实测);WebSearch 只存 query + `{title,url}` 列表。要「网查证据在复盘时还能打开」,唯一可靠路线是**自建 fetch 工具(原文进 CAS)或外部存证**(方案见 ③C)。【F+P】
6. **Claude Code transcript 的隐藏采集面(本机实测)**:`~/.claude/projects/<proj>/<sessionId>.jsonl` 行类型 user/assistant/system/attachment/mode/file-history-snapshot…,uuid/parentUuid 成链;**subagent transcript 在 `<proj>/<sessionId>/subagents/agent-<agentId>.jsonl`(+`.meta.json`:agentType/toolUseId/spawnDepth)**;**超大工具结果 spill 到 `<sessionId>/tool-results/<id>.txt|.pdf`**(本 session 实测 WebFetch 的 PDF 原文 1.7MB 落在这里!)——capsule 的 transcripts adapter 必须把这两个目录一起收,否则「现场」缺页。usage 里有 `cache_creation{ephemeral_1h/5m}`、`output_tokens_details.thinking_tokens`、`iterations[]` 细账。【P】
7. **Codex CLI 2026 已有正式 hooks(stable feature)**:11 种事件(SessionStart/SessionEnd/PreToolUse/PostToolUse/PermissionRequest/PreCompact/PostCompact/UserPromptSubmit/SubagentStart/SubagentStop/Stop),配置在 `~/.codex/hooks.json` 或 `config.toml [hooks]`,stdin JSON 含 `session_id/transcript_path/cwd/hook_event_name/model/permission_mode(+turn_id;工具钩子加 tool_name/tool_use_id/tool_input/tool_response)`;需逐 hook 信任(`--dangerously-bypass-hook-trust` 绕过)。**但 hosted 工具(web_search)不触发工具钩子**——Codex 的网查留痕只能事后读 rollout。【F】
8. **Codex rollout(`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`)是比 Claude transcript 更「API 原生」的现场**:`response_item` 原样存 reasoning/message/function_call/custom_tool_call(+output)/web_search_call;`event_msg` 里 `token_count` 带 `cached_input_tokens`(⊂input,坑)与 `cache_write_input_tokens`、`rate_limits`;`web_search_end` 存**结果快照 `{title,url,snippet,domain,ref_id}`**(比 Claude 多存了 snippet,但同样无页面全文);`turn_context` 每 turn 记 model/sandbox/approval——注意 `codex exec --ephemeral` 会**完全不落 rollout**,capsule 模式必须禁用它。【P+F】
9. **重放语义要分层,业界结论与我们「三结论互不替代」同构**:LangGraph time-travel 官方原话——checkpoint 之前的节点不重跑(用存的结果),之后的节点**真重执行,LLM 调用会再次发生、结果可能不同**;langchain-replay 走另一路(只录 LLM 决策、工具真执行);arXiv《Replayable Financial Agents》(2026-01)用 4700+ 次金融 agent 实测证明**决策确定性与准确率不相关(r=-0.11)**,故「可重放性」必须独立度量——这正是监管重放(audit replay)语境。【F】
10. **监管正在把「AI 投研留痕」变成硬要求**:香港 SFC 24EC55 通函(2024-11-12,已核实原文)把「用 AI LM 提供投资建议/研究」列为**高风险用例**,要求模型测试/校准/验证/持续复核**留档**、human-in-the-loop、事前通知;FINRA RN 24-09 + 2026 年度报告强调 prompt/output 日志、版本跟踪、AI agent 动作 audit trail(Rule 3110/4511);MAS 2025-11 出 AI 风险管理指引咨询稿。个人研究不受管,但**capsule 的设计恰好就是这些要求的工程化形态**,方向正确。【F+S】

---

## ② 分节详报

### 1. 标准与规范

#### 1.1 OpenTelemetry GenAI semantic conventions(最新状态)

- **仓库迁移**:opentelemetry.io 上的 `/docs/specs/semconv/gen-ai/*` 各页已变成迁移占位(「moved to the OpenTelemetry GenAI semantic conventions repository」)【F,2026-08-30 fetch】。新仓 `github.com/open-telemetry/semantic-conventions-genai`,自述「Semantic Conventions for Generative AI (GenAI), including spans, metrics, and events for GenAI clients, MCP, and provider-specific conventions」;目录 `docs/gen-ai/`(README/anthropic/aws-bedrock/azure-ai-inference/gen-ai-agent-spans/gen-ai-events/gen-ai-exceptions/gen-ai-metrics/gen-ai-spans/mcp/openai)+ `docs/registry/` + `model/`(YAML)+ `reference/`(参考实现)【F+P:curl 下载原文】。
- **稳定性**:spans 文档 `**Status:** Development`;我 grep 下载的 gen-ai-spans.md,Development 徽章 287 处、Stable 0 处【P】。搜索侧佐证:v1.42.0(2026-06-12)拆仓是组织性变化;截至 2026-07 所有 gen_ai.* 仍 Development【S:john-hodge.com 等】。
- **span 命名规则(原文)**:LLM 调用 span 名 `SHOULD be "{gen_ai.operation.name} {gen_ai.request.model}"`,kind CLIENT;工具执行有独立 **Execute tool span**,`gen_ai.operation.name SHOULD be "execute_tool"`;agent 侧 `create_agent {gen_ai.agent.name}` / `invoke_agent`(client 与 internal 两式)/ `invoke_workflow.internal` / `plan.internal`【F:raw md】。
- **核心属性(全部 Development,均已核实存在)**:`gen_ai.operation.name`(Required;`chat`/`generate_content`/`text_completion`/`embeddings`/`execute_tool`/`create_agent`/`invoke_agent`…)、`gen_ai.provider.name`(Required)、`gen_ai.request.model`、`gen_ai.response.model/.id/.finish_reasons/.status`、`gen_ai.usage.input_tokens/.output_tokens`、**`gen_ai.usage.cache_read.input_tokens` / `gen_ai.usage.cache_write.input_tokens`**(另有 text/image/audio/reasoning 模态细分)、`gen_ai.conversation.id`、`gen_ai.data_source.id`、`gen_ai.agent.id/.name/.description/.version`、`gen_ai.tool.name/.type/.description/.definitions`、**`gen_ai.tool.call.id/.arguments/.result`**(带 JSON schema)、`gen_ai.system_instructions`、**`gen_ai.input.messages` / `gen_ai.output.messages`**、`gen_ai.request.*`(temperature 等)、`gen_ai.prompt.version`、`gen_ai.memory.*`、`gen_ai.evaluation.score.value/.label`【F:raw registry/spans md】。
- **消息结构(原文示例)**:`gen_ai.input.messages` = 数组,元素 `{role, parts:[{type:"text",content} | {type:"tool_call",id,name,arguments} | {type:"tool_call_response",id,response}]}`;「Messages MUST be provided in the order they were sent」,允许实现提供过滤/截断;**内容捕获是 opt-in(敏感)**【F】。
- **事件页现状**:老的 `gen_ai.content.prompt/completion` 一族事件已被 span 属性路线取代(gen-ai-events.md 仍在新仓;细节未逐字核对,标注:事件体逐字段 **未核实,需读 docs/gen-ai/gen-ai-events.md**)。

#### 1.2 OpenInference(Arize)【F】

span kind 10 种:`LLM/EMBEDDING/CHAIN/RETRIEVER/RERANKER/TOOL/AGENT/GUARDRAIL/EVALUATOR/PROMPT`。属性是「扁平索引」风格:`input.value/input.mime_type`、`output.value`、`llm.model_name/llm.provider/llm.system/llm.invocation_parameters`、`llm.input_messages.{i}.message.role/.content/.tool_calls.{j}.tool_call.*`、`llm.token_count.prompt/.completion/.total` + `llm.token_count.prompt_details.cache_read`、`tool.name/.description/.parameters`、`tool_call.function.name/.arguments`、`tool_call.id`、`retrieval.documents.{i}.document.id/.content/.score/.metadata`、`session.id`、`user.id`、`metadata`、`tag.tags`、`llm.prompt_template.template/.variables/.version`、`agent.name`、`graph.node.id/.name/.parent_id`。价值:比 gen_ai.* 多出 **retrieval 文档级属性**(每条召回文档带 id/content/score)——「证据→结论」链在这套词表里表达得最好。

#### 1.3 Langfuse / LangSmith / MLflow / OpenAI Agents SDK / AgentOps

- **Langfuse**【F,部分】:三层 Trace(单次请求)→ Observations(嵌套步骤;含 generations/events 等类型)→ Session(跨 trace 会话);trace 挂 `user_id/session_id/tags/metadata`;自述「built on OpenTelemetry」,OTLP 端点可吞 gen_ai.*/OpenInference/langfuse.* 属性。observation 类型细表与字段(model/usageDetails/costDetails/promptName…):**部分未核实**(observation-types 页 fetch 网络失败)。
- **LangSmith**【F,部分】:Run(最小工作单元)→ Trace(一次操作的 run 集合,**单 trace 上限 25,000 runs**)→ Thread(多轮会话,靠 metadata `thread_id`)→ Trajectory(把 thread 拉平成消息序列);Feedback=对 run 打分(tag+score);`run_type` 枚举(llm/chain/tool/retriever…)在该页未列出,**未核实,需探针**。
- **MLflow 3.x Tracing**【F】:`Trace = TraceInfo + TraceData`;TraceInfo 字段 `trace_id, trace_location, request_time, state, execution_duration, request_preview, response_preview, client_request_id, trace_metadata, tags`(+assessments);TraceData=span 容器;自述「fully OpenTelemetry-compatible」且「natively supports GenAI Semantic Conventions for export and ingestion」;存储:trace_info 表 + spans 表(JSON 序列化)+ 二进制附件走 artifact 存储、span 里放引用 URI(**附件外置+引用**——与我们 blobs 同构)。
- **OpenAI Agents SDK tracing**【F】:Trace{workflow_name, trace_id=`trace_<32hex>`, group_id, disabled, metadata};Span{started_at/ended_at/trace_id/parent_id/span_data};内置 span:agent_span/generation_span(LLM 输入输出)/function_span/guardrail_span/handoff_span/custom_span/transcription/speech;敏感数据开关 `RunConfig.trace_include_sensitive_data`(默认 True)、全局 `OPENAI_AGENTS_DISABLE_TRACING=1`;可加自定义 processor(Langfuse/Braintrust/MLflow/AgentOps 等 20+ 集成)。
- **AgentOps**:仅在上述集成列表出现,**未单独核实**【S】。
- **Anthropic 官方 Claude Code OTEL**:见节 2.5(这是对我们最直接可用的一家)。

#### 1.4 文件型标准:Inspect AI EvalLog(UK AISI)【F】——离线 capsule 的最佳同类

`.eval`(二进制,约 .json 的 1/8)或 `.json`;`EvalLog{version(=2), status, eval, plan, results, stats, error, samples, reductions}`;每个 `EvalSample{id, epoch, input, target, messages, output, scores, metadata, store, events, model_usage, attachments(去重的内容引用!), transcript}`;**transcript 事件 17 类**:`SampleInitEvent/SampleLimitEvent/StateEvent/StoreEvent/ModelEvent/ToolEvent/ApprovalEvent/InputEvent/ScoreEvent/ErrorEvent/LoggerEvent/InfoEvent/SpanBeginEvent/SpanEndEvent/StepEvent/SubtaskEvent/SandboxEvent`;**ModelEvent 连原始 API 请求/响应都存(`call` 字段)**;读取 API `read_eval_log(header_only=True 可只读头)`、`inspect log dump`。这是「无服务端、单文件、事件全量、附件去重引用」的成熟范本——我们 capsule 的 completeness 度量可对标它的 ModelEvent/ToolEvent 粒度。

#### 1.5 谁最适合做「离线、文件型、无服务端」的对齐目标?

结论:**没有哪家标准可整体照搬;组合 =「gen_ai.* 字段名词表(+OpenInference 的 retrieval.documents 补证据链)+ Inspect EvalLog 的文件形态与事件粒度」**。Langfuse/LangSmith 是服务端产品;MLflow 可 file store 但拖库;OTEL 本体是传输/词表而非存档格式。自研 events hash 链保留,外面加一层薄导出器(→OTLP JSON / →Inspect 风格)即可「对齐但不搬家」。

### 2. Claude Code 采集面(2.1.24x/2.1.251 实测 + 官方文档)

#### 2.1 会话 transcript jsonl【P:本机实拆】

- 路径:`~/.claude/projects/<cwd-slug>/<sessionId>.jsonl`;版本字段 `version: 2.1.241`(会话中途升级不改写旧行)。
- **行类型实测分布**:`user / assistant / system(subtype: local_command|informational|turn_duration) / attachment(hook_success|hook_additional_context|skill_listing|total_tokens_reminder 等注入记录) / mode / last-prompt / ai-title / atis-latch / file-history-snapshot(snapshot.trackedFileBackups,文件改动备份)`。
- **公共字段**:`uuid / parentUuid`(消息链)、`sessionId / session_id`、`timestamp`、`cwd`、`gitBranch`、`version`、`isSidechain`、`userType`、`entrypoint`、`promptId / requestId`、`effort`。
- **assistant 行**:`message{id, model("claude-fable-5"), role, content[], stop_reason, stop_details, usage}`;content 块 `text|thinking|tool_use{id,name,input}`;usage 含 `input_tokens / output_tokens / cache_read_input_tokens / cache_creation_input_tokens / cache_creation{ephemeral_1h_input_tokens, ephemeral_5m_input_tokens} / output_tokens_details{thinking_tokens} / server_tool_use{web_search_requests, web_fetch_requests} / service_tier / speed / inference_geo / iterations[]`。
- **user 行(工具结果)**:`message.content[] = tool_result{tool_use_id, content(string|array), is_error}` + **顶层 `toolUseResult`(结构化真身,比 content 全)** + `sourceToolAssistantUUID`(指回发起该调用的 assistant 行)。toolUseResult 按工具而异(实测):Bash=`{stdout, stderr, interrupted, isImage, noOutputExpected}`;WebFetch=`{bytes, code, codeText, durationMs, result, url}`;WebSearch=`{query, results[{tool_use_id, content:[{title,url}...]}], durationSeconds, searchCount}`;Agent=`{agentId, status, prompt, description, outputFile, canReadOutputFile, isAsync, resolvedModel}`。
- **WebSearch/WebFetch 在 transcript 里的形态与截断**:WebSearch 只有标题+URL 列表与引用块(无页面正文);WebFetch 只有小模型提取后的 `result` 文本 + `bytes/code`(无原始 HTML)。tools-reference 官方确认 WebFetch「大页面先按固定字符上限截断再交小模型;结果有损(lossy by design);缓存 15 分钟,`CLAUDE_CODE_WEBFETCH_CACHE_TTL_MS` 可调;跨域跳转不跟」【F】。
- **大结果 spill(关键实测)**:超限的工具结果不进 transcript 正文,写 `<proj>/<sessionId>/tool-results/<toolu_id|随机id>.txt`(WebFetch 拿到 PDF 时整个 PDF 落 `webfetch-<ts>-<rand>.pdf`,实测 1.7MB),transcript/上下文中只留「Output too large... saved to: <路径>」占位。Bash 侧官方数字:成功结果内联 ~30,000 字符,超限落盘(64 MiB 截断)+ 预览;`BASH_MAX_OUTPUT_LENGTH` 默认 30,000、硬顶 150,000;>5GB 杀命令【F+P】。
- **subagent transcript**:`<proj>/<sessionId>/subagents/agent-<agentId>.jsonl` + `agent-<agentId>.meta.json{agentType, description, toolUseId, spawnDepth}`;行格式同主 transcript,另带 `agentId` 且 `isSidechain:true`。主 transcript 的 Agent tool_result 里有 `agentId` 可关联。【P】

#### 2.2 hooks(33 事件,2026 全表)【F:code.claude.com/docs/en/hooks】

- **事件全表**:`SessionStart, Setup, UserPromptSubmit, UserPromptExpansion, PreToolUse, PermissionRequest, PermissionDenied, PostToolUse, PostToolUseFailure, PostToolBatch, Notification, MessageDisplay, SubagentStart, SubagentStop, TaskCreated, TaskCompleted, Stop, StopFailure, TeammateIdle, InstructionsLoaded, ConfigChange, CwdChanged, DirectoryAdded, FileChanged, WorktreeCreate, WorktreeRemove, PreCompact, PostCompact, PreModelSwitch, PostModelSwitch, Elicitation, ElicitationResult, SessionEnd`(33 种)。
- **公共 stdin 输入**:`session_id, prompt_id, transcript_path, cwd, permission_mode, effort{level}, hook_event_name`;subagent 内加 `agent_id, agent_type`。
- **关键事件输入**:PreToolUse=`tool_name, tool_input, tool_use_id`;**PostToolUse 另加 `tool_response{type:"text"|"image", text}`**;PostToolUseFailure 加 `error`;SubagentStop=`agent_id, agent_type, last_assistant_message`(SDK 版另有 **`agent_transcript_path`**【F:agent-sdk/hooks】);Stop=`last_assistant_message, turn_number`;SessionStart=`start_reason(startup|resume|clear|compact|fork)`;PreCompact/PostCompact=`compact_reason(manual|auto)`。
- **matcher**:纯字母数字/管道/逗号=精确或列表(`Edit|Write`);含其他字符=不锚定的 JS 正则;MCP 形如 `mcp__server__tool`;PreToolUse/PostToolUse 按工具名匹配(内建工具名单含 **WebFetch**;SDK 文档明列 `Bash, Read, Write, Edit, Glob, Grep, WebFetch, Agent` 等)。SubagentStart/Stop 按 agent_type;SessionStart 按 start_reason;等等。
- **输出控制**:`hookSpecificOutput{permissionDecision(allow|deny|ask|defer), permissionDecisionReason, additionalContext, updatedInput, **updatedToolOutput**(PostToolUse 改写工具输出), systemMessage}`;exit 2=阻断(stderr 为理由);`async:true` 后台钩子(不阻塞;`asyncRewake` 可回叫);hook 类型五种 `command / http / mcp_tool / prompt / agent`;超时默认 600s(部分事件 30s/10s)。settings 位置:`~/.claude/settings.json` / `.claude/settings.json` / `.claude/settings.local.json` / 插件 hooks.json / skill、subagent frontmatter。
- **用 PostToolUse 把 WebFetch「原文」落盘?** 能落的是 `tool_response`(=小模型提取结果)+ `tool_input.url/prompt`。**页面原始内容不经过 hook**;spill 的 PDF 例外(整文件在 tool-results 目录,可由 hook 按路径收走)。文档未承诺 tool_response 对超大输出不截断——**截断边界未核实,需探针**。

#### 2.3 `-p` 非交互 / stream-json【F:headless 页 + `claude --help` 实测】

- `--output-format text|json|stream-json`(`json` 带 `result/session_id/total_cost_usd/usage/structured_output`,`--json-schema` 强 schema);`--input-format stream-json`;`--include-partial-messages`(细粒度 stream_event);`--verbose` 是 stream-json 前提;**`--include-hook-events`**(hook 生命周期事件入流:hook_started/hook_progress/hook_response);`--replay-user-messages`(把 stdin 的用户消息回显到 stdout 流)。
- 流内消息:`system/init`(model、tools、mcp_servers+`mcp_server_errors`、plugins+`plugin_errors`、capabilities)、`assistant`/`user`(**`parent_tool_use_id` 标注属于哪个 Agent 调用 → 可重建 subagent 嵌套树**)、`system/api_retry{attempt,max_retries,retry_delay_ms,error_status,error}`、`system/plugin_install`、末行 `result`。
- **`--forward-subagent-text`** 或 `CLAUDE_CODE_FORWARD_SUBAGENT_TEXT`(v2.1.211+):默认只转发 subagent 的 tool_use/tool_result,开了才转发 text/thinking,可完整重建各级 subagent transcript(v2.1.219+ 支持任意嵌套深)。
- Agent SDK:hooks 同名注册(Python 只支持 10 种:PreToolUse/PostToolUse/PostToolUseFailure/UserPromptSubmit/Stop/SubagentStart/SubagentStop/PreCompact/Notification/PermissionRequest;TS 全集);`settingSources/setting_sources` 载入 settings 的 shell hooks;`include_hook_events`。【F:agent-sdk/hooks 全文】

#### 2.4 官方 OTEL 遥测(答案:能拿到什么)【F:monitoring-usage 全文】

- 开关:`CLAUDE_CODE_ENABLE_TELEMETRY=1` + `OTEL_METRICS_EXPORTER/OTEL_LOGS_EXPORTER(otlp|prometheus|console|none)` + OTLP 端点族。
- **Metrics 8 个**:`claude_code.session.count / lines_of_code.count / pull_request.count / commit.count / cost.usage / token.usage(type=input|output|cacheRead|cacheCreation) / code_edit_tool.decision / active_time.total`,attribution 维度含 `agent.name, skill.name, plugin.name, mcp_server.name, mcp_tool.name, query_source(main|subagent|auxiliary), effort, speed`。
- **Log events 14 个**:`claude_code.user_prompt / assistant_response / tool_result / api_request / api_error / api_refusal / api_request_body / api_response_body / tool_decision / permission_mode_changed / auth / mcp_server_connection / internal_error / plugin_installed`。
- **内容分级开关(默认全脱敏)**:`OTEL_LOG_USER_PROMPTS=1`(prompt 全文)、`OTEL_LOG_ASSISTANT_RESPONSES=1`、`OTEL_LOG_TOOL_DETAILS=1`(→ tool_result 事件带 `tool_parameters` + **`tool_input`,单值>512 截断、总 ~4K**;还有 error 全文、MCP server 名)、`OTEL_LOG_TOOL_CONTENT=1`(工具输入输出内容,**仅 spans**)、**`OTEL_LOG_RAW_API_BODIES=1|file:<dir>`**(Messages API 完整请求/响应;inline 60KB 截断,file 模式不截断,extended-thinking 脱敏);`CLAUDE_CODE_OTEL_CONTENT_MAX_LENGTH` 调上限。
- 每事件标配 `event.timestamp / event.sequence / session.id / user.* / organization.id / prompt.id / message.uuid(与 transcript 对齐!) / request_id / client_request_id`。**tool_result 默认只有 tool_name/tool_use_id/success/duration_ms/两个 size 字段——不开 flag 拿不到内容**。
- **Traces beta**:`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` + `OTEL_TRACES_EXPORTER=otlp`;spans:`claude_code.interaction / llm_request / tool / tool.blocked_on_user / tool.execution / hook`;Bash 子进程注入 `TRACEPARENT`(W3C);`-p`/SDK 会读入 inbound TRACEPARENT 挂子 span——**我们的 python 取数脚本可以借这个把「确定性层」也挂进同一棵 trace**。

#### 2.5 小结:Claude 侧「采不到」清单

网页原始 HTML(WebFetch 摘要化;spill PDF 例外)、WebSearch 结果页正文、模型看到的最终拼装 system prompt(除非开 RAW_API_BODIES)、hook 之外的权限 UI 过程细节。其余(prompt/thinking/工具入出/子代理/成本/缓存细账)均有至少一条官方采集路。

### 3. Codex CLI 采集面(0.151.0 实测 + 官方文档)

#### 3.1 session/rollout 存储【P:本机实拆】

- `~/.codex/sessions/YYYY/MM/DD/rollout-<ISO时间>-<uuid>.jsonl`;行结构 `{timestamp, type, payload, ordinal}`。
- 行类型:**`session_meta`**(session_id、timestamp、cwd、originator(codex-tui/cli)、cli_version、source、thread_source、model_provider、**base_instructions 全文**——system prompt 自带留档!)/ **`turn_context`**(每 turn:turn_id、cwd、approval_policy、sandbox_policy、permission_profile、model、reasoning effort、collaboration_mode、personality)/ **`response_item`**(payload.type:`message / reasoning / function_call{name,arguments,call_id} / function_call_output{call_id,output} / custom_tool_call / custom_tool_call_output / web_search_call / tool_search_call / tool_search_output / agent_message`——**模型侧原生 item 全量入档,含 reasoning**)/ **`event_msg`**(下)/ `world_state`。
- `event_msg` payload.type(2026-08 实测 union):`item_completed{item{type:Reasoning|CommandExecution|AgentMessage|FileChange|UserMessage|Extension,…}, started_at_ms, completed_at_ms, thread_id, turn_id} / token_count / task_started / task_complete / thread_settings_applied / agent_message / user_message / agent_reasoning / turn_aborted / context_compacted / web_search_end / sub_agent_activity{agent_thread_id, agent_path, kind} / patch_apply_end`。
- **token_count 语义(坑已实测)**:`{total_token_usage,last_token_usage}{input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens, reasoning_output_tokens, total_tokens}` + `model_context_window` + `rate_limits{primary/secondary{used_percent,window_minutes,resets_at}, plan_type}`;**`input_tokens` 含 `cached_input_tokens`**(项目 MEMORY 已有此坑,rollout 证实)。
- **web_search 形态**:`response_item.web_search_call` 只存动作 `action{type:"search",queries[]} | {type:"open_page",url} | {type:"find_in_page",url,pattern}`;`event_msg.web_search_end{call_id, query, action, results[{type:"text_result", title, url, snippet, domain, ref_id}]}`——**存快照 snippet、不存页面全文**。
- MCP 工具调用:`codex exec --json` 有 `mcp_tool_call{server,tool}` item【F】;rollout 内 MCP 调用记录形态**未单独实测(需探针:找一段带 MCP 调用的 rollout 验证 function_call vs 专用 item)**。
- 其他本地存储:`history.jsonl`(仅用户输入:{session_id, ts, text};config `history.persistence=save-all|none`、`history.max_bytes`)、`logs_2.sqlite`(tracing 日志表 logs:ts/level/target/thread_id/process_uuid)、goals/memories/queue sqlite。`codex migrate-rollouts` 提示未来有「paginated thread history」新存储(feature flag `background_paginated_rollout_migration` under development)【P】。
- **`codex exec --ephemeral` = 完全不落 rollout**【F+P:--help】→ capsule 模式必须显式禁用。`codex resume/fork <id>`、`codex exec resume --last` 走 rollout 重建。

#### 3.2 hooks(2026:有,stable)【F:learn.chatgpt.com/docs/hooks;P:`codex features list` 显示 `hooks stable true`】

- **事件 11 种**:`SessionStart, SessionEnd, PreToolUse, PostToolUse, PermissionRequest, PreCompact, PostCompact, UserPromptSubmit, SubagentStart, SubagentStop, Stop`。
- 配置:`~/.codex/hooks.json` 或 `~/.codex/config.toml [hooks]`;项目层 `<repo>/.codex/hooks.json|config.toml`(项目 trusted 才加载);TOML 内联式 `[[hooks.PreToolUse]] matcher=... [[hooks.PreToolUse.hooks]] type="command"`【S:第三方,与官方 config-reference 的 [hooks] 描述一致】;handler 支持 command 与 MCP tool(prompt/agent 型「parsed but skipped」);企业 `requirements.toml` 可 `allow_managed_hooks_only=true`【F:codex repo docs/config.md】。
- stdin 输入:公共 `session_id, transcript_path, cwd, hook_event_name, model, permission_mode`;turn 域加 `turn_id`;工具钩子加 `tool_name, tool_use_id, tool_input, tool_response`。matcher=正则(`Bash`、`^apply_patch$`、`mcp__filesystem__.*`)。输出:JSON `continue:false` 或 exit 2 阻断;`hookSpecificOutput.additionalContext` 注上下文。
- **信任门**:非托管 hook 首次要人工 review+trust 精确定义;`--dangerously-bypass-hook-trust` 一次性绕过(exec 场景)。
- **覆盖面**:官方页称 PreToolUse/PostToolUse「can observe more than shell and MCP calls」,支持 Bash、`apply_patch`、MCP、本地函数工具;**「Not supported: hosted tools like WebSearch」**(网查是服务端 hosted 工具,不过钩子)。第三方文章称「PreToolUse 目前只拦 Bash」【S:agenticcontrolplane.com】——与官方口径冲突,**以探针为准(需实测 apply_patch/MCP 是否触发)**。
- `notify = ["program", args...]`:turn 结束等时机调用外部程序、传 JSON payload(config-reference 确认存在;**payload 字段名未核实,需探针**——本机 config 实测用了 `"turn-ended"` 参数,旧文档流传 `agent-turn-complete` 类型)。

#### 3.3 `codex exec --json` 事件流【F:learn.chatgpt.com/docs/non-interactive-mode;P:--help】

- 事件:`thread.started / turn.started / turn.completed / turn.failed / item.started / item.updated / item.completed / error`;item 类型:`agent_message(text) / reasoning / command_execution{command, aggregated_output, exit_code, status} / file_change{changes} / mcp_tool_call{server, tool} / web_search{query} / todo_list`;`turn.completed` 带 `usage{input_tokens, cached_input_tokens, output_tokens(, reasoning_output_tokens)}`。
- 相关 flags:`--json`、`-o/--output-last-message <file>`、`--output-schema <file>`(结构化终答)、`--ephemeral`、`-s/--sandbox`、`--skip-git-repo-check`、`--cd`。
- **otel 配置**(config.toml)【F:config-reference】:`[otel] environment / exporter(none|otlp-http|otlp-grpc) / log_user_prompt(bool,默认不导出原始 prompt) / metrics_exporter(none|statsig|otlp-http|otlp-grpc) / trace_exporter`,端点/headers/protocol/TLS 可配——Codex 也有官方 OTEL 出口,但事件/属性字典未公开成文,**字段名未核实**。
- `web_search` 配置值:`disabled | cached(默认,OpenAI 自维护索引) | indexed | live`【F】——**cached 模式意味着「当时搜到的内容」可能不是实时网页**,复盘留痕时应记录该配置(turn_context/session_meta 未见此值,**需探针确认 rollout 是否记录 web_search 模式**)。

#### 3.4 小结:Codex 侧「采不到」清单

网页/搜索结果全文(只有 snippet)、hosted 工具的 hook 拦截、notify payload 字典(未公开)、(相对 Claude)没有 tool-results 溢写目录——但 rollout 本身比 Claude transcript 更完整(reasoning、base_instructions、turn_context 全量)。

### 4. 科研数据 provenance 标准

- **W3C PROV-O**(W3C Recommendation,2013-04-30)【F】:三元 `prov:Entity / prov:Activity / prov:Agent`;起点属性 `prov:used, prov:wasGeneratedBy, prov:wasDerivedFrom, prov:wasAttributedTo, prov:wasAssociatedWith, prov:actedOnBehalfOf, prov:wasInformedBy, prov:startedAtTime, prov:endedAtTime`;扩展 `prov:Collection, prov:Bundle, prov:Plan, prov:SoftwareAgent, prov:Revision, prov:Quotation, prov:PrimarySource, prov:specializationOf, prov:alternateOf, prov:generatedAtTime, prov:invalidatedAtTime, prov:atLocation, prov:value`;qualified 模式(`prov:Usage/Generation/Association + prov:hadPlan/hadRole/qualifiedUsage`)。**映射我们 capsule**:source_lineage 的「读点」= qualified `prov:Usage`;blobs 内容寻址实体 = `prov:Entity`+`prov:value`;identity 快照 = `prov:SoftwareAgent`+`prov:Plan`;网页快照相对原 URL = `prov:specializationOf`(某时刻特化)。不必上 RDF,但字段命名照它借词能换来「术语免设计+未来可导出」。
- **RO-Crate 1.2**(2025-06-04 Recommendation)【F】:`ro-crate-metadata.json`(JSON-LD,context `https://w3id.org/ro/crate/1.2/context`)描述整个目录树(File/Dataset 实体+Root Data Entity);行动级 provenance 用 schema.org `CreateAction`(instrument/object/result/agent);有 **Workflow Run RO-Crate / Provenance Run Crate** 专用 profile(细节在子规范,未逐字核实)。**用途**:capsule 若要「交给别人/十年后还能读」,在 capsule 根放一个 ro-crate-metadata.json 当自描述封皮,是最低成本的标准化出口。
- **数据快照与 CAS**:DVC(md5 内容寻址缓存+指针文件)、lakeFS(对象存储上的 git 语义)——概念级【S】;我们已有 blobs CAS,属同族,无需换。
- **网页存证(「复盘时还能打开」)**:
  - **WARC**(ISO 28500)是原教旨格式;**WACZ 1.1.1**【F:specs.webrecorder.net】把 WARC 打包为单 zip:`archive/*.warc.gz + indexes/*.cdx.gz + pages/pages.jsonl{url, ts(RFC3339), title, text} + datapackage.json{profile:"data-package", wacz_version, resources[{name, path, hash(sha256:...), bytes}], created, modified, software} + datapackage-digest.json(可签名)`——**自带 sha256 清单,与 capsule 的 hash 链天然咬合;replayweb.page 可离线回放**。工具:browsertrix-crawler / ArchiveWeb.page【S】。
  - **SingleFile**(单 HTML 内联所有资源)与 **ArchiveBox**(每 URL 多格式:HTML/WARC/PDF/截图)【S,未 fetch】:适合「人看」;WACZ 适合「证据」。
  - **Wayback Save Page Now**:入口 `https://web.archive.org/save`(help 页核实【F】;第三方免存储、URL 公开可引用);**SPN2 API 的参数表(capture_outlinks/if_not_archived_within/status 端点等)未核实,需探针**(官方文档是一份 Google Doc,archive.org 上的镜像 item 现 404)。公共服务有限速,不能当同步路径。
  - **成本/价值**:A 股公告 PDF、交易所页面、财经媒体页消亡/改版极快,「结论引了一个打不开的 URL」是复盘最常见的断链。本地 CAS 原文(方案②)边际成本≈0(已有 blobs);WACZ 只在「要给第三方看/长期保存」时值得;SPN 是免费异步兜底。

### 5. 交易/投研「决策日志」实践

- **decision journal / pre-registration**【S:Farnam Street 一系、Verdoso、koji.so、substack 综述】:成型做法 = 下单前写下 ①论点与机制 ②触发条件 ③**kill criteria(证伪条件,预先量化:如「营收增速 < X」「竞争格局出现 Y」)** ④预期分布/概率与时限,事后按 **Brier score**((p−outcome)² 平均)与校准曲线记分;Klein 的 **pre-mortem**(假设两年后亏 50%,倒推死因)据称提升失败模式识别 ~30%【S】。→ 对照我们:E6 卡片=论点、tripwire_watch=kill criteria、outcome 账本=结果;**缺口 = 预先写下的概率分布 + Brier 记分环**(闭环学习已退役,但「记分」≠「学习」,记分只是把预测钉在墙上)。
- **LLM 研究 agent 的 evidence ledger / claim graph**:无公开标准;工程同类 = OpenInference 的 `retrieval.documents.{i}.*`(每条结论可挂证据文档 id/score)、Inspect 的 attachments 去重引用、我们的 source_lineage。学术侧最贴题:**《Replayable Financial Agents: A Determinism-Faithfulness Assurance Harness for Tool-Using LLM Agents》(arXiv 2601.15322,2026-01,v2 2026-03)**【F:摘要】——动机就是「监管要求重放被标记的交易决策,多数部署做不到一致结果」;提出 DFAH 三指标:**轨迹确定性 / 决策确定性 / 证据条件忠实度(evidence-conditioned faithfulness)**;4,700+ 次运行(7 模型、T=0.0)发现**决策确定性与准确率不相关(r=−0.11, p=0.63)**、无模型两全。→ 直接支持 capsule 的「完好性/完整性/可重放性三结论互不替代」,且提示第四个可加维度:**忠实度(结论是否真被证据支撑)**。
- **监管指引(AI 投研留痕)**:
  - **香港 SFC 通函 24EC55**(2024-11-12,原文已核实【F】):适用于 LC 在受规管活动中用 AI LM;四原则(高管责任/模型风险管理/网络与数据安全/第三方风险);**「用 AI LM 向投资者或客户提供投资建议、投资意见或投资研究」明列为高风险用例**;高风险要求:模型验证与持续复核、**human-in-the-loop 复核输出后才可传达**、prompt 扰动稳健性测试、向用户披露 AI;**留痕原文:「The results of the model testing and calibration…validation and ongoing review and monitoring should be documented.」**;采用前有通知义务。
  - **FINRA**:RN 24-09(2024-06)确认 GenAI 适用既有规则(Rule 3110 监督/2210 通讯/4511 账簿记录);RN 25-07(2025-04)延伸监督义务;2026 年度监管报告强调 **prompt 与 output 日志、版本跟踪、对能行动的「AI agents」要求窄授权+动作 audit trail**【S:finra.org 报告+综述】。
  - **SEC**:2023 预测性数据分析(PDA)提案于 2025-06 撤回;现行依赖既有 recordkeeping(Advisers Act 204-2 等)与反欺诈条款【S,未逐条核实】。
  - **MAS**:2025-11-13 发布《Guidelines on AI Risk Management》咨询文件(AIRG),覆盖全部 FI,咨询期至 2026-01-31【S:mas.gov.sg 链接】。
  - 含义:我们是个人研究不在管辖内,但监管把「重现 AI 建议当时的输入与依据」定为方向——capsule 不是过度工程,是把未来合规形态提前做了。
- **交易所属账本类比**【S】:自营/量化机构的 order audit trail(CAT/OATS)只管订单事件;「研究过程留痕」无统一标准,上述 FINRA 2026 报告的 prompt/output 日志是目前最接近的表述。

### 6. 重放(replay)的工程做法

- **三层重放谱系**:
  1. **HTTP/cassette 层**(VCR.py、pytest-recording、responses):录请求/响应,重放时拦网络【S】。对 LLM:请求体=完整 prompt,匹配策略需自定义(消息序列化不稳定是主要坑)。适合锁「单次 API 调用」。
  2. **决策层重放**:**langchain-replay**(sixty-north)——**只录 LLM 的决策(调哪个工具、什么参数、说什么),重放时把决策灌回、工具真执行**【S:GitHub+博文】。适合「改了确定性工具代码后,验证同样的 LLM 决策会产出什么」——恰是我们 harvest/assemble 改版回归的正确口径。
  3. **checkpoint/时间旅行**:LangGraph【F:官方 use-time-travel】——`get_state_history(config)` 取 checkpoint 列表(StateSnapshot{values, next, config{thread_id, checkpoint_id}, metadata, created_at, parent_config, tasks}),从某 checkpoint 恢复;**官方原话:「Replay re-executes nodes—it doesn't just read from cache. LLM calls, API requests, and interrupts fire again and may return different results.」「Nodes before the checkpoint are not re-executed (results are already saved). Nodes after the checkpoint re-execute.」**;`update_state()` 从旧 checkpoint 分叉、原历史不动。→ 术语警示:LangGraph 的「replay」= 前段读档+后段重执行,不是法证重放。
- **产品侧**:LangSmith 可从 trace 建 dataset 再跑、Playground 重跑单 run;promptfoo 有响应缓存;Braintrust 主打 eval 重跑【S,均未逐字核实产品文档】。OpenAI Agents SDK 的 trace 可导出到这些平台【F】。
- **Claude/Codex 自带的「重放素材」**:Claude `--resume/--fork-session`(从 jsonl 重建上下文分叉)、`--input-format stream-json + --replay-user-messages`(可把录好的用户消息流灌回)、`/rewind`(文件级 file-history-snapshot);Codex `codex resume/fork`、`codex exec resume`。这些都是「续跑/分叉」而非确定性重放。
- **「当时看到了什么」的完整性度量**:对照三家——Inspect 连 ModelEvent 的 raw request/response 都存(**最高标准**);Codex rollout 存全部 response_item(reasoning 在内)但网查只剩 snippet;Claude transcript 存「结果视图」(WebFetch 已摘要)+ spill 文件。建议把 completeness 从「文件在不在」升级为**每类外部输入三级口径:L0 仅存在性(记了 URL/参数)/ L1 摘要可回放(存了模型看到的视图)/ L2 原文可回放(存了原始 bytes)**,按 run_profile 声明每类输入要求达到的级别,报告用「X% at L2」而非布尔。DFAH(节 5)则是**输出侧**的重放度量(同输入重跑 N 次的决策一致率),可作为 capsule replay 的第四个独立结论。

---

## ③ 对我们 capsule 的对齐建议

### A. 字段级:值得直接采用的属性名(全部来自已核实原文;均为 Development 状态,采「名」不采「版本承诺」)

events hash 链里的事件负载,建议加一个 `otel` 命名空间镜像(或导出器映射表),键名照抄:

| capsule 现有概念 | 采用的标准名 | 出处 |
|---|---|---|
| 一次 LLM 调用 | `gen_ai.operation.name`(`chat`)、`gen_ai.provider.name`(`anthropic`/`openai`)、`gen_ai.request.model`、`gen_ai.response.model`、`gen_ai.response.id`、`gen_ai.response.finish_reasons` | gen-ai-spans.md【F】 |
| token 账 | `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` / **`gen_ai.usage.cache_read.input_tokens`** / **`gen_ai.usage.cache_write.input_tokens`**(Codex 的 cached⊂input 换算后再填,换算规则写进 adapter) | 同上【F】 |
| prompt/输出内容 | `gen_ai.system_instructions`、`gen_ai.input.messages`、`gen_ai.output.messages`(role+parts 结构照抄,parts.type ∈ text/tool_call/tool_call_response) | registry【F】 |
| 工具调用 | `gen_ai.tool.name/.type/.description/.definitions`、**`gen_ai.tool.call.id` / `gen_ai.tool.call.arguments` / `gen_ai.tool.call.result`**;span 语义 `execute_tool` | spans+registry【F】 |
| subagent | `gen_ai.agent.id/.name/.description/.version`;操作 `create_agent` / `invoke_agent`;会话 `gen_ai.conversation.id`(=sessionId) | gen-ai-agent-spans.md【F】 |
| 数据湖读点 | `gen_ai.data_source.id`(lake 表/端点 id);证据文档级用 OpenInference `retrieval.documents.{i}.document.id/.content/.score/.metadata` | 【F】 |
| 错误 | `error.type`(OTEL 通用) | 【F】 |
| 评分/复盘 | `gen_ai.evaluation.score.value` / `.label`(把 outcome 账本的尺读数导出时用) | registry【F】 |
| provenance 词 | `prov:used / prov:wasGeneratedBy / prov:wasDerivedFrom / prov:wasAttributedTo / prov:SoftwareAgent / prov:Plan / prov:Quotation / prov:specializationOf`(source_lineage 字段起名参考) | PROV-O【F】 |

不建议采用:OTEL 的 span/trace 传输机制本身(我们是文件型);已废弃路线 `gen_ai.prompt/completion` 独立事件族(业界已转 span 属性)。

### B. Claude / Codex 采集面对照表(每格标【F/P/S/未核实】)

| 采集项 | Claude Code 2.1.25x | Codex CLI 0.151 |
|---|---|---|
| transcript 位置 | `~/.claude/projects/<slug>/<sid>.jsonl`【P】 | `~/.codex/sessions/Y/M/D/rollout-*.jsonl`【P】 |
| system prompt 留档 | 不在 transcript;要 `OTEL_LOG_RAW_API_BODIES`【F】 | **session_meta.base_instructions 全文自带**【P】 |
| 用户 prompt | user 行全文【P】 | response_item.message + event_msg.user_message【P】 |
| thinking/reasoning | assistant content 的 thinking 块(含在 transcript)【P】 | response_item.reasoning 全量【P】 |
| 工具调用入参 | tool_use.input 全量【P】 | function_call.arguments / custom_tool_call【P】 |
| 工具输出 | tool_result + 顶层 toolUseResult;**超限 spill 到 `<sid>/tool-results/`**【P】 | function_call_output / item_completed(CommandExecution 带 aggregated_output)【P】 |
| 网查 query | toolUseResult.query【P】 | web_search_call.action{queries/url/pattern}【P】 |
| 网查结果 | WebSearch:仅 title+url;WebFetch:小模型摘要 + bytes/code【P+F】 | web_search_end.results{title,url,**snippet**,domain,ref_id}【P】 |
| 网页原文 | **不可得**(例外:PDF 落 tool-results)【F+P】 | **不可得**【P】 |
| subagent 现场 | `<sid>/subagents/agent-<id>.jsonl` + meta;stream-json 用 parent_tool_use_id+`--forward-subagent-text` 重建【P+F】 | sub_agent_activity 事件(agent_thread_id 指向另一 rollout)【P;子 rollout 结构需探针】 |
| hooks 事件数 | **33**(全表见 §2.2)【F】 | **11**【F】 |
| hook 拿工具输出 | PostToolUse.tool_response;可 updatedToolOutput 改写【F】 | PostToolUse.tool_response(hosted web_search 除外)【F】 |
| hook 拦网查 | matcher 可写 `WebFetch|WebSearch`(内建工具名)【F】 | **不可(hosted 工具不过钩)**【F】 |
| headless 事件流 | `-p --output-format stream-json`(system/init、api_retry、result、hook 事件)【F】 | `codex exec --json`(thread/turn/item 事件)【F】 |
| token 细账 | usage 含 cache_creation{1h/5m}、thinking_tokens、server_tool_use【P】 | token_count 含 cached_input(⊂input)、cache_write、reasoning_output、rate_limits【P】 |
| 官方 OTEL | metrics 8 + events 14 + spans(beta);内容 flag 分级;RAW_API_BODIES=file 全量【F】 | `[otel]` exporter/trace/metrics/log_user_prompt;字典未公开【F/未核实】 |
| 逃逸口(会丢现场) | `--no-session-persistence`、`--bare`(跳 hooks)【P:--help】 | **`codex exec --ephemeral`**、`history.persistence=none`【F+P】 |
| 完整性坑 | attachment/hook 注入行要一起收;tool-results 与 subagents 目录在 MANIFEST 外 | cached web_search(默认 `cached` 索引而非 live)——复盘时「当时搜到的」可能非实时网页【F;rollout 是否记录该模式:未核实,需探针】 |

### C. 网查证据快照:推荐方案与成本

| 方案 | 做法 | 覆盖 | 成本 | 判定 |
|---|---|---|---|---|
| ① hook 兜底(Claude 侧) | PostToolUse(matcher `WebFetch|WebSearch`)把 `tool_input.url/prompt + tool_response` 追加写 capsule 的 external_tools.jsonl(现有账本);顺带收 `<sid>/tool-results/*` | 模型「实际看到的视图」(L1 级),非原文;Codex 侧无等价物 | 一个 hook 脚本;零运行成本 | **立即做**(补 Claude 侧;Codex 靠 rollout adapter 事后收 web_search_end) |
| ② 自建 fetch 工具(主路) | 网查改走自有工具(脚本/MCP:curl/无头浏览器 → 原始 bytes 进 blobs CAS(sha256)→ 提要给模型);两引擎统一走它 | **L2 原文可回放**;URL+时刻+hash 三元组进 source_lineage | 一次实现;JS 重页面需 headless 浏览器;改 skill 的网查习惯 | **推荐主路**(与既有 blobs/source_lineage 严丝合缝;顺带解决 Codex hosted 工具不可拦的问题) |
| ③ 外部/标准化存证 | 关键证据(公告 PDF、定价页、监管文件)夜批推 Wayback SPN(`web.archive.org/save`,公共限速,SPN2 API 参数**需探针**);或本地打 WACZ(sha256 清单+可签名+replayweb.page 离线回放) | 第三方时间戳/长期可开 | SPN 免费但异步限速;WACZ 需 browsertrix 类爬虫 | 兜底与「给别人看」时用;不做同步依赖 |

### D. 其余落点(按性价比排序)

1. **transcripts adapter 补两个目录**:`<sid>/subagents/*.jsonl(+meta.json)` 与 `<sid>/tool-results/*` 纳入 capsule 收割与 MANIFEST——否则子代理现场与大结果原文(含 WebFetch PDF)在「现场」之外(本 session 实测就有 1.7MB PDF 只存在于此)。
2. **completeness 分级口径**:每类外部输入按 L0 存在性 / L1 摘要视图 / L2 原文,写进 run_profile 声明;报告输出「L2 覆盖率」。呼应「MANIFEST 通过≠现场完整」。
3. **replay 的第四结论**:在 完好性/完整性/可重放性 之外加 **决策一致率**(DFAH 式:同输入重跑 N 次的评级一致性)与(远期)**证据条件忠实度**;LangGraph 教训写进 replay 文档——「重放=前段读档+后段重执行」必须显式声明每段是哪种。
4. **两引擎逃逸口封堵**:capsule 模式下禁 `codex exec --ephemeral`、禁 `--no-session-persistence`;Codex 侧记录 `web_search` 配置值(cached/live)入 identity 快照。
5. **token 归一**:adapter 层统一换算成 `gen_ai.usage.*` 四件(input/output/cache_read/cache_write),Codex 的 cached⊂input 在 adapter 内解开(项目已有此坑的 MEMORY)。
6. **决策日志补「预注册」两件**:卡片增加预先概率/预期分布字段 + 复盘时算 Brier——记分不是学习环,不违 08-21 退役裁定。
7. **对外封皮(远期)**:capsule 根加 `ro-crate-metadata.json` 自描述;若做网页归档用 WACZ 而非裸目录。
8. **Claude OTEL 可选增强**:本地起 OTLP file exporter 收 `claude_code.api_request_body(file 模式)` 可拿到逐次 API 全量现场——与 transcript 互为对账(**注意:hook 子进程会被剥掉 OTEL_* 环境变量**,采集要在主进程侧配)。

---

## ④ 参考 URL 表

**已 fetch 原文核实【F】**(fetch 日 2026-08-30/31):

| # | 来源 | URL |
|---|---|---|
| 1 | Claude Code hooks 参考(33 事件/payload/matcher/输出) | https://code.claude.com/docs/en/hooks |
| 2 | Claude Code OTEL 监控(metrics/events/spans/内容开关) | https://code.claude.com/docs/en/monitoring-usage |
| 3 | Claude Code headless/-p(stream-json 事件、subagent 转发) | https://code.claude.com/docs/en/headless |
| 4 | Claude Agent SDK hooks(SDK 事件表、agent_transcript_path、updatedToolOutput、async) | https://code.claude.com/docs/en/agent-sdk/hooks |
| 5 | Claude Code tools 参考(WebFetch 摘要化/缓存、Bash 溢写数字) | https://code.claude.com/docs/en/tools-reference |
| 6 | Codex hooks(11 事件/hooks.json/trust/hosted 工具不拦) | https://learn.chatgpt.com/docs/hooks (developers.openai.com/codex/hooks 308 至此) |
| 7 | Codex 配置参考([hooks]/[otel]/history/web_search/notify) | https://learn.chatgpt.com/docs/config-file/config-reference |
| 8 | Codex 非交互(exec --json 事件与 item 字典) | https://learn.chatgpt.com/docs/non-interactive-mode |
| 9 | OTEL GenAI 新仓(landing) | https://github.com/open-telemetry/semantic-conventions-genai |
| 10 | OTEL GenAI spans 原文(下载核实) | https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/docs/gen-ai/gen-ai-spans.md |
| 11 | OTEL GenAI agent spans 原文 | .../docs/gen-ai/gen-ai-agent-spans.md |
| 12 | OTEL GenAI 属性 registry 原文(input.messages 结构、tool.call.*) | .../docs/registry/attributes/gen-ai.md |
| 13 | openai/codex 仓 docs/config.md(hooks 托管开关;正文已迁站) | https://github.com/openai/codex/blob/main/docs/config.md |
| 14 | OpenInference 语义约定 | https://arize-ai.github.io/openinference/spec/semantic_conventions.html |
| 15 | Langfuse 数据模型(部分) | https://langfuse.com/docs/observability/data-model |
| 16 | LangSmith observability 概念(部分) | https://docs.langchain.com/langsmith/observability-concepts |
| 17 | MLflow GenAI tracing 首页 | https://mlflow.org/docs/latest/genai/tracing/ |
| 18 | MLflow Trace 数据模型 | https://mlflow.org/docs/latest/genai/concepts/trace/ |
| 19 | OpenAI Agents SDK tracing | https://openai.github.io/openai-agents-python/tracing/ |
| 20 | W3C PROV-O(2013-04-30 Rec) | https://www.w3.org/TR/prov-o/ |
| 21 | RO-Crate 1.2(2025-06-04) | https://www.researchobject.org/ro-crate/specification/1.2/ |
| 22 | LangGraph time-travel(replay 语义原话) | https://docs.langchain.com/oss/python/langgraph/use-time-travel |
| 23 | WACZ 1.1.1 规范 | https://specs.webrecorder.net/wacz/1.1.1/ |
| 24 | Inspect AI eval logs(EvalLog/事件 17 类/ModelEvent.call) | https://inspect.aisi.org.uk/eval-logs.html |
| 25 | 香港 SFC 通函 24EC55 原文(2024-11-12) | https://apps.sfc.hk/edistributionWeb/api/circular/list-content/circular/intermediaries/supervision/doc?refNo=24EC55&lang=EN |
| 26 | Replayable Financial Agents(arXiv 2601.15322) | https://arxiv.org/abs/2601.15322 |
| 27 | Internet Archive Save Page Now help | https://help.archive.org/help/save-pages-in-the-wayback-machine/ |

**本机探针【P】**:`~/.claude/projects/-Users-qingbin-zhuang-Personal-TradingAgents/*.jsonl` 及 `<sid>/subagents/`、`<sid>/tool-results/`(Claude Code 2.1.241/2.1.251);`~/.codex/sessions/**/rollout-*.jsonl`、`codex features list`、`codex exec --help`、`~/.codex/config.toml`、`logs_2.sqlite`(codex-cli 0.151.0)。

**仅搜索摘要【S】**(未 fetch 原文,引用需自行复核):OTEL GenAI 2026 状态综述(john-hodge.com/blog/opentelemetry-genai-semantic-conventions;dev.to/azena-ai …not-stable-yet…);Codex hooks 第三方参考(agenticcontrolplane.com/blog/codex-cli-hooks-reference——「仅拦 Bash」与官方冲突待探针);FINRA(finra.org/rules-guidance/key-topics/artificial-intelligence;RN 24-09;2026 Annual Regulatory Oversight Report GenAI 章);MAS AIRG 咨询(mas.gov.sg/news/media-releases/2025/mas-guidelines-for-artificial-intelligence-risk-management);决策日志/Brier(verdoso.com/en/notes/brier-score-investment-forecasts;koji.so/docs/research-calibration-brier-score);langchain-replay(github.com/sixty-north/langchain-replay);SingleFile/ArchiveBox/browsertrix(各 GitHub);DVC/lakeFS(官网)。

**明确「未核实,需探针」清单**:SPN2 API 参数表与 status 端点字段;Codex notify JSON payload 字段;Codex OTEL 导出的事件/属性字典;Codex rollout 中 MCP 调用与子代理 rollout 的具体形态;Codex hooks 对 apply_patch/MCP 是否实际触发(官方与第三方口径冲突);Claude PostToolUse `tool_response` 对超大输出的截断边界;LangSmith run_type 枚举;Langfuse observation 类型细字段;promptfoo/Braintrust/LangSmith 的 replay 产品细节。

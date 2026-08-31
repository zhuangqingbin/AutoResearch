# W5 跨引擎 Agent Skill 可移植性调研 — Claude Code × OpenAI Codex CLI

- 调研日:2026-08-30 ~ 08-31(北京时间;中途撞 Claude session 限额一次,08-31 续跑)
- 方法:WebFetch 官方文档为主(developers.openai.com/codex 全线 308 跳转到 learn.chatgpt.com/docs/*,已按跳转后地址核实)+ WebSearch 社区佐证。每条结论标注 **[官方已核]**(已 WebFetch 原文)或 **[搜索摘要]**(仅搜索结果摘要,未读原文)。不确定的配置键一律标「未核实,需探针」。
- 本仓背景:`.claude/skills/<name>/SKILL.md`(scan-market / stock-research / sector-research / macro-research / dossier-init / l4-stock)、`.claude/agents/*.md`(company-intel / l4-card / l4-intel / l3-rank / sector-brief 等 11 个 subagent)、Claude Workflow(js 编排,l4-stock 每股一个)、产物按引擎分根(`context_claude` / `context_codex`,`AUTORESEARCH_ENGINE`/`CLAUDECODE` 自动判定)、共享 `lake/`。

---

## ① 一页摘要:10 条最有用发现

1. **Agent Skills 已是真·开放标准,Codex 原生支持**。Anthropic 2025-12 把 SKILL.md 格式开放(agentskills.io,GitHub agentskills/agentskills),截至 2026-08 官网 adopter 走马灯列出 **45+ 家**:Claude Code、**ChatGPT & Codex**、Cursor、Gemini CLI、GitHub Copilot、VS Code、OpenCode、Goose、Amp、JetBrains Junie、Roo Code、Kiro、Factory、OpenHands、Letta、Databricks、Snowflake 等。**[官方已核]**
2. **公共子集只有 6 个 frontmatter 字段**:`name`、`description`、`license`、`compatibility`、`metadata`、`allowed-tools`(最后一个 experimental)。Claude Code 的 `context: fork`、`agent:`、`hooks:`、`model:`、`disable-model-invocation` 等 15+ 字段全是 **Claude 扩展**,超出 6 字段的 SKILL.md 在 claude.ai 上传/打包时直接报错,在其他 harness 里则被忽略。**[官方已核]** → 我们的 skill 想两边跑,frontmatter 必须收敛到 6 字段,Claude 专有行为放 body 或 `.claude/` 侧。
3. **Codex 的 skill 目录是 `.agents/skills`(不是 `.codex/skills`)**:发现顺序 `$CWD/.agents/skills` → `$CWD/../.agents/skills` → `$REPO_ROOT/.agents/skills` → `$HOME/.agents/skills` → `/etc/codex/skills` → 内置;**支持 symlink**(官方明说 follows the symlink target)。显式触发 `$skill-name`,隐式按 description 匹配;`[[skills.config]]` 可按 path 禁用;skill 清单占用 ≤2% 上下文或 8000 字符。**[官方已核]**(早期 `~/.codex/skills` 已是旧路径,**[搜索摘要]**)
4. **Codex 2026-03-16 起有原生 subagent,且默认开启**:内置 `default`/`worker`/`explorer` 三角色;**自定义 agent = `~/.codex/agents/*.toml` 或项目 `.codex/agents/*.toml`**,必填 `name`/`description`/`developer_instructions`,可选 `model`/`model_reasoning_effort`/`sandbox_mode`/`mcp_servers`;编排工具 `spawn_agent`(`fork_turns: none|all`)、`wait_agent`、`send_input`/`followup_task`、`close_agent`、`spawn_agents_on_csv`。**[官方已核 + Simon Willison]** 我们的 company-intel/l4-card 这类「命名 subagent」在 Codex 侧有了直接映射物(md→toml 翻译)。
5. **但 Codex 没有 Claude Workflow 的等价物**:Codex 编排是「模型在环、逐轮调度」,**每个子结果都落回父上下文**,并发默认 `max_threads=6`、`max_depth=1`;Claude Workflow 是「脚本在环」,中间结果留在 js 变量里,16 并发/单跑 1000 agent 上限。**[官方已核(Claude 侧)+ 社区对照(Codex 侧数值,版本细节未核实)]** → 大扇出(全 A 扫描 L4 每股一卡)在 Codex 原生编排下会撑爆父上下文,必须外层编排。
6. **两边都能当「无人值守可编程叶子」且都有 JSON Schema 结构化输出**:`codex exec --json`(JSONL 事件流 thread.started/turn.\*/item.\*)+ `--output-schema <file>`(严格:`additionalProperties:false` 且所有属性进 `required`,否则 400)+ `-o` 落盘末消息 + `CODEX_API_KEY`;`claude -p --output-format stream-json` + `--json-schema`(结果在 `structured_output` 字段)+ `--bare`(跳过 hooks/skills/MCP 装载,CI 推荐)。**[官方已核]**
7. **无人值守跑 Python 取数在 Codex 侧要过两道闸**:exec 默认 `read-only` 沙箱且审批自动降为 `never`;写盘要 `--sandbox workspace-write`;**联网取数还要开 `[sandbox_workspace_write] network_access`(默认关;确切默认值未核实,需探针)** 或 `--yolo`/`danger-full-access`。macOS 用 Seatbelt,Linux 要装 bubblewrap。**[官方已核(模式/审批)+ 默认值需探针]**
8. **Codex 现在也有 hooks 和 plugin,而且 2026-08 已「默认开启」**:hooks.json / config.toml `[hooks]`,事件覆盖 PreToolUse/PostToolUse/UserPromptSubmit/SubagentStart/SubagentStop/PreCompact/PostCompact/Stop/SessionStart/SessionEnd(+0.150.0 新增 Interrupt),PreToolUse 现已覆盖 shell、apply_patch(匹配名 Edit/Write)、MCP 工具;plugin = `.codex-plugin/plugin.json` 打包 skills+MCP+hooks。另有 2026-08-06 五家(OpenAI/Microsoft/AWS/Cursor/Vercel,**无 Anthropic**)发布的 vendor-neutral「Agent Plugins 1.0」(agent-plugins.org):`plugin.json` + `skills/` + `mcp.json`。**[官方已核]**
9. **成本结构完全不同**:Codex CLI 与 ChatGPT 网页/IDE **共享同一个 5 小时窗口配额 + 未公布数字的周限**(我们 08-28 法证波「Codex T1-9 撞配额停」即此;超额可买 credits,或改 API key 按 token 计费);Claude 订阅侧 subagent/workflow 也计入 5h+周窗,但 **fork 共享父 prompt cache、workflow 扇出有 5s stagger 让兄弟 agent 吃同一前缀缓存**,subagent 缓存 TTL 默认仅 5 分钟(`subagentPromptCacheTtl` 可改 1h)。**[官方已核 + 本仓实测]**
10. **「一份 skill 两边跑」业界主流是三招**:(i) `npx skills`(vercel-labs)以 `.agents/skills` 为 canonical、向各 agent 目录 symlink(Claude→`.claude/skills`,Codex→`.agents/skills`);(ii) obra/superpowers 同一 skill 树 + `references/codex-tools.md` 讲清 Codex 侧派发差异(无命名 agent 注册表时代的补偿,现可用 `.codex/agents/`);(iii) stafforini 式「对等双树 + manifest + 提醒 hook + pre-commit 守卫」。没有一家是「一份文件零改动两边全功能」——**公共子集能共享,引擎特性各自落根**。**[官方已核/原文已读]**

---

## ② 能力对照表

| 能力 | Claude Code(2026-08,v2.1.2xx) | Codex CLI(2026-08,0.151.0) | 备注 + 出处 |
| --- | --- | --- | --- |
| **SKILL.md 技能** | `.claude/skills/`(项目)、`~/.claude/skills/`(个人)、plugin、enterprise;自动触发或 `/name`;`.claude/commands/` 已并入 skills | **原生支持**:`.agents/skills`(CWD→父→repo root)、`$HOME/.agents/skills`、`/etc/codex/skills`、内置;`$skill-name` 显式或按 description 隐式;支持 symlink | 两边同一 SKILL.md 格式(Agent Skills 标准)。[官方已核] code.claude.com/docs/en/skills;learn.chatgpt.com/docs/build-skills |
| skill frontmatter | 标准 6 字段 + ~15 个扩展(`context: fork`/`agent`/`hooks`/`model`/`effort`/`paths`/`disable-model-invocation`/`user-invocable`/`arguments`/`shell` 等),`!`cmd`` 动态注入,`$ARGUMENTS`/`${CLAUDE_SKILL_DIR}` 替换 | 标准 6 字段;Codex 专有元数据放**旁车文件 `agents/openai.yaml`**(display_name/icon/`policy.allow_implicit_invocation`/`dependencies.tools: mcp`),不污染 SKILL.md | 超出 6 字段:Claude 外的 harness 忽略或报错。[官方已核] 两家文档 |
| skill 配置/禁用 | settings.json 覆盖可见性;`disableSkillShellExecution` | `[[skills.config]] path=... enabled=false`;`$skill-installer` 装 openai/skills 精选 | [官方已核] |
| **自定义 subagent** | `.claude/agents/*.md`(YAML frontmatter + md 系统提示):`tools`/`disallowedTools`/`model`/`permissionMode`/`maxTurns`/`skills` 预载/`hooks`/`memory`/`background`/`isolation: worktree`/`effort`;并发默认上限 20(`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`);可后台、可恢复(SendMessage) | `~/.codex/agents/*.toml` + `.codex/agents/*.toml`(项目,需 trust):必填 `name`/`description`/`developer_instructions`,可选 `model`/`model_reasoning_effort`/`sandbox_mode`/`mcp_servers`/`skills.config`/`nickname_candidates`;`[agents] enabled=true` 默认开;`agents.max_concurrent_threads_per_session`、`default_subagent_model` | Codex **无 tools 白名单字段**(以 sandbox_mode 粗粒度约束);Claude md↔Codex toml 可机械翻译。[官方已核] code.claude.com/docs/en/sub-agents;learn.chatgpt.com/docs/agent-configuration/subagents |
| 内置 subagent | Explore / Plan / general-purpose | default / worker / explorer | [官方已核] |
| 派发原语 | Agent tool(名字派发,fork 继承全上下文)、@-mention、`claude --agent` | `spawn_agent`(`fork_turns: none|all`)、`wait_agent`(事件订阅)、`send_input`/`followup_task`、`close_agent`、`spawn_agents_on_csv`(每 CSV 行一 worker) | Codex GA 2026-03-16;子 agent 可再生子 agent。[官方已核 + simonwillison.net + superpowers codex-tools.md] |
| **脚本级编排(Workflow)** | **有**:js 脚本(`agent()`/`parallel()`/`pipeline()`/`phase()`/`args`/schema),16 并发、1000 agent/跑,中间结果在脚本变量不进上下文,可暂停/恢复/保存为 `/命令`(`.claude/workflows/`),`-p`/SDK 可用 | **无等价物**:模型在环逐轮编排,子结果落回父上下文;社区对照给出 `max_threads` 默认 6、`max_depth` 默认 1(键名出自社区文章,**未核实,需探针**) | 这是两引擎最大架构差。[官方已核(Claude)] code.claude.com/docs/en/workflows;[搜索摘要+原文(codex.danielvaughan.com 2026-06-08)] |
| **网查** | WebSearch(实时)+ WebFetch(15min 缓存);workflow `/deep-research` | `web_search = disabled\|cached\|indexed\|live`,**默认 `cached`(预索引快照,可能陈旧)**;full-access 自动升 live;`--search` 单次开;`[tools.web_search]` 对象形式(v0.128+):`allowed_domains`/`context_size`/`location` | Codex 想要新闻级实时必须显式 `live`。[官方已核(config-reference)+ danielvaughan 5-09 原文] |
| **MCP 客户端** | `.mcp.json` / `--mcp-config`;工具默认延迟加载(tool search)省上下文 | `config.toml [mcp_servers.<id>]`:`command/args/env/cwd/url/bearer_token_env_var/http_headers/enabled/startup_timeout_sec(10)/tool_timeout_sec(60)/enabled_tools/disabled_tools/required`;`codex mcp add/list/login`;OAuth+DCR | 两边都全功能。[官方已核] |
| 自身作为可被编程调用的服务 | Agent SDK(TS/Python)、`claude -p`;(Claude Code 亦可被 MCP 包装,社区做法) | Codex SDK:TS `@openai/codex-sdk`(`startThread/run/resumeThread`)、Python `openai-codex`(JSON-RPC 连本地 **app-server**);`codex mcp-server` **已弃用**→app-server | [官方已核] learn.chatgpt.com/docs/codex-sdk |
| **无人值守 CLI** | `claude -p` + `--output-format text\|json\|stream-json` + `--json-schema`(structured_output)+ `--allowedTools`/`--permission-mode` + `--bare`(跳 hooks/skills/MCP/CLAUDE.md;CI 推荐,读 `ANTHROPIC_API_KEY`)+ `--continue/--resume`;json 里带 `total_cost_usd`/usage | `codex exec` + `--json`(JSONL:`thread.started`,`turn.started/completed/failed`,`item.started/completed`;item:`agent_message/reasoning/command_execution/file_change/mcp_tool_call/web_search/plan`)+ `--output-schema` + `-o` + `--ephemeral` + `codex exec resume` + stdin `-`;`CODEX_API_KEY` | [官方已核] code.claude.com/docs/en/headless;learn.chatgpt.com/docs/non-interactive-mode |
| 配置覆盖/档案 | `--settings <json>`、env、settings.json 层级 | `-c key=value`(可重复)、`-p/--profile`(`profile-name.config.toml` 叠加)、`--ignore-user-config`、`codex features enable/disable` | [官方已核] |
| 思考/效果档 | `--effort low..max`、`/effort`、ultracode | `model_reasoning_effort = minimal\|low\|medium\|high\|xhigh` | [官方已核] |
| **沙箱/审批** | permission-mode(default/acceptEdits/auto/dontAsk/bypass)、allow/deny 规则、sandboxing、hooks 可拦 | `sandbox_mode = read-only\|workspace-write\|danger-full-access`;`approval_policy = untrusted\|on-request\|on-failure\|never`;exec 默认 read-only 且审批自动降 never;`--full-auto` 已是弃用兼容路径;macOS Seatbelt / Linux 需 bubblewrap / Windows 原生;**workspace-write 联网默认受限**(`[sandbox_workspace_write] network_access`,默认值未核实,需探针) | [官方已核] learn.chatgpt.com/docs/sandboxing、/agent-approvals-security;alexfazio 81 项实测 gist |
| **hooks** | 30+ 事件(PreToolUse/PostToolUse/UserPromptSubmit/Stop/SubagentStart/Stop/SessionStart/End/PreCompact/FileChanged/PreModelSwitch…),5 种 handler(command/http/mcp_tool/prompt/agent),可写进 skill/agent frontmatter | 事件较少但已覆盖核心:PreToolUse/PostToolUse/UserPromptSubmit/PermissionRequest/SubagentStart/SubagentStop/PreCompact/PostCompact/Stop/SessionStart/SessionEnd/Interrupt;handler:command/mcp_tool;**默认开启**(`[features] hooks=false` 关);非托管 hook 首次要 `/hooks` 按 hash 信任;PreToolUse 覆盖 shell+apply_patch+MCP,hosted 工具(WebSearch)不触发 | Codex hooks 2026-03 v0.114 以 experimental 起步(**[搜索摘要]**),08 月官方文档已写默认开。[官方已核] learn.chatgpt.com/docs/hooks |
| **plugin** | `.claude-plugin/` + skills/agents/commands/hooks/workflows/MCP;marketplace | `.codex-plugin/plugin.json` + `skills/`+`.mcp.json`+`.app.json`+hooks;`codex plugin marketplace add`;**manifest 无 agents/commands 字段**;另有跨厂商 Agent Plugins 1.0(agent-plugins.org,OpenAI/MS/AWS/Cursor/Vercel,Anthropic 未列名) | [官方已核] developers.openai.com/plugins/build/plugins;agent-plugins.org |
| **结构化输出** | `--json-schema`(v2.1.205+ 校验 schema,`format` 仅注解不强制) | `--output-schema`(强制 `additionalProperties:false` + 全属性 required,否则 400) | 同一 JSON Schema 文件基本可两用(交集:都写 required+additionalProperties:false)。[官方已核 + gist 实测] |
| **指令文件** | CLAUDE.md(层级,~/.claude + 项目 + 子目录;**不读 AGENTS.md**,可 `@AGENTS.md` 引入或 symlink) | AGENTS.md:`~/.codex/AGENTS(.override).md` → git root→cwd 逐目录(override 优先,每目录取一份),root 在前、近者后写=生效;合并上限 `project_doc_max_bytes` **默认 32 KiB**;`project_doc_fallback_filenames`;**不读 CLAUDE.md** | [官方已核] learn.chatgpt.com/docs/agent-configuration/agents-md;gist(yurukusa) |
| 配额/计费 | 订阅 5h 窗+周窗(subagent/workflow 同计);API key 按 token;企业均值 ~$13/开发者/活跃日 | ChatGPT 各档全含 Codex;**CLI 与 web/IDE 共享同一 local-message 池**(Plus:Sol 10-100/5h…;周限数字不公布);超额买 credits(rate card:Sol 100 in/500 out credits/M tok);或 API key | [官方已核] code.claude.com/docs/en/costs;learn.chatgpt.com/docs/pricing |

---

## ③ 分节详述

### 1. Agent Skills 开放标准(全部 [官方已核],agentskills.io 2026-08-30 fetch)

**规范本体**(agentskills.io/specification):
- 目录 = `SKILL.md`(必须)+ 可选 `scripts/`、`references/`、`assets/`、任意其他文件。
- frontmatter 字段(全表):`name`(必填,≤64 字符,小写字母数字连字符,不得首尾/连续连字符,**必须等于父目录名**)、`description`(必填,≤1024)、`license`、`compatibility`(≤500,仅在有环境要求时写)、`metadata`(string→string map)、`allowed-tools`(空格分隔,**experimental,各实现支持不一**)。
- 渐进加载三段:metadata(~100 tok,启动即载)→ SKILL.md body(激活时,建议 <5000 tok / <500 行)→ resources(按需)。引用文件用相对路径、建议只一层深。
- 验证器:`skills-ref validate ./my-skill`(github.com/agentskills/agentskills/skills-ref)。
- 治理:Anthropic 原创并开放(anthropics/skills 仓 README 明确「标准看 agentskills.io」),GitHub agentskills/agentskills + Discord 公议。2025-10 进 Claude 产品、2025-12 开放为标准([搜索摘要],多篇一致)。

**Adopter 名单**(agentskills.io 首页 clients 数组,45+):Claude Code、Claude(claude.ai/API)、**ChatGPT & Codex(instructionsUrl 即 developers.openai.com/codex/skills)**、Cursor、Gemini CLI、GitHub Copilot、VS Code、OpenCode、Goose(Block)、Amp、JetBrains Junie、Roo Code、Kiro(AWS)、Factory、OpenHands、Letta、Mux(Coder)、TRAE(字节)、Spring AI、Mistral Vibe、Databricks Genie Code、Snowflake Cortex Code、Tabnine、Qodo、Laravel Boost、Emdash、fast-agent、nanobot、Hermes(Nous)、OpenClaw、pi、VT Code、Ona、Firebender、Autohand、ZeroClaw、Deep Code、Pulumi Neo、Google AI Edge Gallery、Command Code、Workshop、Superconductor、Vita、Agentman、Piebald、bub。问题里点名的 **Codex/Cursor/Gemini CLI/Copilot/OpenCode/Goose 全部在列**。

**Codex 原生 skills 细节**(learn.chatgpt.com/docs/build-skills,官方):
- 发现路径(按 scope 顺序):`$CWD/.agents/skills` → `$CWD/../.agents/skills`(嵌套 git)→ `$REPO_ROOT/.agents/skills` → `$HOME/.agents/skills` → `/etc/codex/skills` → OpenAI 内置。**支持 symlink 目录**。
- 触发:CLI/IDE 里 `$skill-name` 显式点名;或按 `description` 隐式匹配(可用旁车 `agents/openai.yaml` 的 `policy.allow_implicit_invocation: false` 关掉隐式)。
- 旁车元数据 `agents/openai.yaml`(Codex 专有,不进 SKILL.md):`interface.display_name/icon_small/icon_large/brand_color`、`policy.allow_implicit_invocation`、`dependencies.tools: [{type: "mcp", value: serverName}]`。
- 配置:`~/.codex/config.toml` 里 `[[skills.config]] path=".../SKILL.md" enabled=false` 按 skill 禁用;安装器 `$skill-installer <name>` 拉 openai/skills 精选仓。
- 上下文预算:skill 清单最多吃 2% 上下文窗或 8000 字符。
- 历史:早期文档用 `~/.codex/skills`/`$CODEX_HOME/skills`,2026 已迁 `.agents/skills` 为标准共享位([搜索摘要]:knightli.com 04-29、itecsonline、agentskillshub;官方现行文档只写 `.agents/skills`)。**若给本仓做适配,建议直接探针验证当前安装版本认哪些路径**。

**Codex AGENTS.md 分层规则**(learn.chatgpt.com/docs/agent-configuration/agents-md,官方):
- 全局:`$CODEX_HOME`(默认 `~/.codex`)下先 `AGENTS.override.md` 后 `AGENTS.md`,取第一个非空。
- 项目:从 git root 走到 cwd,每目录按 `AGENTS.override.md` → `AGENTS.md` → fallback 名单取至多一份;root 在前逐级拼接(空行连接),**离 cwd 越近越靠后=覆盖生效**。
- `project_doc_max_bytes` 默认 **32 KiB**(超限截断、停止发现);`project_doc_fallback_filenames`(如 `TEAM_GUIDE.md`)可扩别名;空文件跳过;每次启动构建一次。**明确不读 CLAUDE.md**(fallback 名单里手工加 `CLAUDE.md` 理论可行,**未核实,需探针**)。

### 2. Codex CLI 的 subagent / 多 agent 能力

**结论:有,默认开,GA 于 2026-03-16**([官方已核] learn.chatgpt.com/docs/agent-configuration/subagents + simonwillison.net/2026/Mar/16/codex-subagents/)。

- 开关:`[agents] enabled = true`(默认);相关键 `agents.max_concurrent_threads_per_session`、`agents.default_subagent_model`、`agents.default_subagent_reasoning_effort`、`agents.interrupt_message`(默认 true)。另有 `features.multi_agent`(config-reference 页列为默认开;与 `[agents]` 的关系未细究——**探针确认哪个键是现行真开关**)。2026-03 时还是 `--enable multi_agent` 手动开、开了以后系统提示 +~1940 tok(alexfazio gist 实测,v0.114)。
- 内置角色:`default`(通用)、`worker`(执行/实现)、`explorer`(读为主探索)。
- 自定义 agent:`~/.codex/agents/*.toml`(个人)/ `.codex/agents/*.toml`(项目,需项目 trust)。字段:必填 `name`(派发匹配的唯一事实源)、`description`、`developer_instructions`(≈系统提示);可选 `model`、`model_reasoning_effort`、`sandbox_mode`(只能收紧不能升权)、`mcp_servers`(agent 专属 MCP)、`skills.config`、`nickname_candidates`。**没有 tools 白名单字段**(对照 Claude 的 `tools:`)——工具约束靠 sandbox_mode + MCP enabled_tools 粗粒度实现。
- 编排工具(模型可调用):`spawn_agent`(`fork_turns: "none"` 干净上下文 / `"all"` 复制父转录,默认 all——superpowers codex-tools.md 实测记录)、`wait_agent`(事件订阅非轮询)、`send_input`/`followup_task`(续聊子 agent)、`close_agent`(V1 需手动清理,V2 自动驱逐)、`spawn_agents_on_csv`(CSV 每行一 worker、结果合并回 CSV)。子 agent 可再 spawn 子 agent。
- 并发与形态:社区对照(codex.danielvaughan.com 2026-06-08)记 Multi-Agent v2(v0.137)默认 `max_threads=6`、`max_depth=1`、可配;**每个子结果都回落父上下文窗**——这是与 Claude Workflow 的本质差异。官方页未给出这两个键名,**键名未核实,需探针**。
- 表面覆盖:CLI(`/agent` 切线程)、IDE 扩展、ChatGPT 桌面端、ChatGPT Work;0.149.0 加了 `codex agents` 交互面板、`codex queue` 向既有会话投递消息([官方已核] changelog)。
- 无 Claude Workflow 等价物:Codex 侧没有「引擎执行用户提供的编排脚本」机制;要脚本级编排只能 (a) 外层进程起多个 `codex exec`(官方 SDK 即此形态的 app-server 版),(b) MCP 包装(见 §4c 的 mgoulart 插件,反向:Claude Code 编排 Codex),(c) tmux 多实例(oh-my-claudecode omc-teams 一类,[搜索摘要])。

### 3. 工具面对照(详表见 ②;此处只记补充事实)

- **codex exec 实测坑**(alexfazio 81 项 gist,v0.114.0,2026-03-14,原文已读):`--output-schema` 必须 `additionalProperties:false` + 全属性 required,否则 400;`--full-auto` 与显式 `--sandbox` 冲突时锁 workspace-write;`-a on-request` 在 exec 下自动降 `never`;stdin 必须用 `-` 占位;`--search` 是全局旗标必须放在 `exec` 之前;`--ephemeral` 会话 resume 会静默开新会话。
- **审批/沙箱官方口径**(learn.chatgpt.com/docs/agent-approvals-security):三预设 Auto(workspace-write + on-request)/ Read-Only(read-only + on-request)/ Full Access(danger-full-access + never,不推荐);CI 无人值守推荐显式声明,`--full-auto` 已标弃用兼容;`approval_policy` 四值 untrusted/on-request/on-failure/never。**Python 取数场景**:`codex exec --sandbox workspace-write` 可写盘跑脚本,但外网(yfinance/tushare HTTP)默认被沙箱拦,需 `-c sandbox_workspace_write.network_access=true`(键名来自官方 config-reference 的 `[sandbox_workspace_write] network_access`;**默认 false 为社区共识,官方页未直接给默认值——探针确认**)。
- **Claude 侧对应**:`claude -p --bare` 是官方 CI 推荐(不读 OAuth/keychain,要 `ANTHROPIC_API_KEY`);stream-json 里 subagent 消息带 `parent_tool_use_id` 可重建嵌套树(`--forward-subagent-text`);`--agents <json>` 可行内注入 subagent 定义(bare 模式亦可)。
- **web 查询语义差异**:Claude WebSearch 恒为实时检索;Codex 默认 `cached` 预索引快照(「trades freshness for safety」),做**盘中/隔夜新闻情报(l4-intel/company-intel 场景)必须 `web_search="live"`**,且 hosted 工具不触发 hooks、不能被 PreToolUse 拦。

### 4. 「一份 skill 两边跑」的工程模式(≥3 个真实仓库/文章)

**(a) 编排下沉确定性 Python,引擎只做叶子**(本仓现状的延长线)
- 佐证:两边官方 headless 面(§3)都为此设计;Codex SDK 官方定位就是「程序化控制本地 Codex agent」(TS `@openai/codex-sdk` 的 `startThread()/thread.run()`,Python `openai-codex` 走 app-server JSON-RPC)。
- 关键事实:Codex 原生编排会把子结果灌回父上下文(§2),Claude Workflow 虽好但 **js 编排文件是 Claude 专有格式**——把循环/分支/合并放进我们自己的 Python(prelude 已是),两引擎只消费「任务包 md → 产物 md/json」,是唯一让两边行为同构的位置。
- 成本/延迟公开数据:Claude 官方——subagent 重工作流开销 200–500%([搜索摘要],costs 相关文章);agent teams ≈7x tokens([官方已核] costs 页);workflow 扇出兄弟共享前缀缓存(5s stagger,[官方已核])。Codex——CLI 与 web 共享 5h 池([官方已核] pricing),每个 `codex exec` 进程是独立 thread、无跨进程 prompt cache 共享的公开承诺(**未核实**);切换 `--model` 会废 ~59% 前缀缓存(gist 实测)。

**(b) MCP server 暴露仓库工具,两引擎共用**
- 两边 MCP 客户端都全功能(§3 表)。适合把 `autoresearch` 的取数/组装做成 MCP 工具让引擎调;但**只统一工具面,不统一 subagent/编排面**,且我们数据层已是 `uv run python -m ...` CLI(Claude 官方 costs 页也说 CLI 比 MCP 省上下文)。反向用法真实存在:**mgoulart/codex-subagents**(原文已读)= Claude Code 插件,经 MCP server 起 `codex exec` 子进程并行(每批 ≤3 个、自动串批、冲突检测+lint/type/test 验收);其 README 记录了「子进程继承 MCP server stdio 导致 stdin 挂死」这个坑(我们若走子进程编排要显式 `stdin=DEVNULL`)。
- Codex 自身当 MCP server 的老路 `codex mcp-server` **已弃用**,官方指向 app-server([官方已核] developer-commands 页)。

**(c) `codex exec` / `claude -p` 作为可编程叶子、外层 Python 派发**
- 真实案例:mgoulart 插件(上)、oh-my-claudecode 的 `/ask codex` 与 omc-teams(tmux 派 claude/codex/gemini worker;本机已装,[本地事实]);codex.danielvaughan.com 2026-04-18「Running Multiple Codex Agent Instances」([搜索摘要])。
- 事件流可法证:`codex exec --json` 的 JSONL(item 粒度到 command_execution/file_change/mcp_tool_call/web_search)与 `claude -p --output-format stream-json --verbose`(带 usage/total_cost_usd/parent_tool_use_id)都能整段落盘进我们的 run capsule(transcripts/ 已有 Claude/Codex 两个 adapter,08-28 波)。注意本仓已踩坑:**Codex `input_tokens` 含 cached**(memory)。
- 上下文隔离:每个 exec/-p 进程天然隔离 = 等价我们现在「一股一 subagent」的隔离语义。

**(d) 双树同步 / 通用格式社区做法(3 个以上)**
1. **vercel-labs/skills(`npx skills`)**(README 已读):76+ agent 目标;canonical 装到 `.agents/skills`,默认 **symlink** 到各 agent 目录(`--copy` 可改拷贝);Claude Code=`.claude/skills`(项目)/`~/.claude/skills`(全局),Codex=`.agents/skills`(项目)/`~/.codex/skills`(全局,**与官方现行 `.agents/skills` 口径略有出入,装前探针**);`add/list/find/update/init/remove`、`-a` 选 agent、`-g` 全局。
2. **obra/superpowers**(codex-tools.md 原文已读):同一 skills 树发布到 Codex(`~/.agents/skills` 被扫描);`references/codex-tools.md` 记录 Codex 派发要点——spawn_agent 用 `fork_turns:"none"` 给干净上下文(默认 all 复制整个转录)、复用 followup_task 而非重 spawn、wait_agent 是事件订阅要用 300-600s 大超时、**spawn 时 model 与 reasoning_effort 必须同时显式设置**(只设 model 会静默重置 effort)。这份文件就是「Claude 语汇 → Codex 语汇」的翻译层样板。
3. **stafforini.com「How I keep Claude Code and Codex in sync」**(2026-05-10,原文已读):版本控制 `agent-config/` 双树(claude/CLAUDE.md ↔ codex/AGENTS.md、skills 对等、hooks 对等、.mcp.json ↔ config.toml),symlink 进各自家目录;比较前**剥掉工具专有 frontmatter 键**(allowed-tools/argument-hint/model);manifest(ai-config-sync.json)登记 claude-only/codex-only/unsupported;post-edit 提醒 hook + pre-commit 守卫拦「只改一边」的提交。
4. 其他:benthamite/agent-sync-template、gitlab knowledge-graph 的 AGENTS.md↔CLAUDE.md CI 同步检查、yurukusa gist(Claude 不读 AGENTS.md 的 5 种落地法:`@AGENTS.md` 引入 / symlink / CI 校验…)([搜索摘要+部分原文])。
5. **Agent Plugins 1.0**(agent-plugins.org 原文已读):OpenAI/Microsoft/AWS/Cursor(Anysphere)/Vercel 五家 TSC,2026-08-06 发布;`plugin.json`(仅 name/version 必填)+ `skills/` + `mcp.json` + 厂商前缀扩展目录;首发客户端 ChatGPT、Codex CLI、Cursor、Copilot、VS Code、Kiro;**Anthropic 不在 TSC**,Claude Code plugin(`.claude-plugin/`)是平行格式;规范明言不管签名/密钥/沙箱。→ 若要做跨引擎分发,skills 层选 agentskills.io(双方都认),plugin 层暂时两套。

### 5. 产物一致性(markdown/JSON 稳定性、评级漂移度量)

- **结构化输出**:Codex `--output-schema`(严格模式,见 §3 坑)与 Claude `--json-schema`(v2.1.205+ 严格校验,`format` 仅注解)都指向同一件事:**把「决策卡/评级」的机器面从 md 约定升级为 JSON Schema 合同,一份 schema 文件两边复用**(写成两边交集:全 required + additionalProperties:false)。本仓 `parse_rating` 五档校验 + assemble 的确定性组装可原样当 schema 的后置校验。
- **漂移现实**:same-prompt variability 是 2026 年 Codex 被报告最多的抱怨、Claude 侧 Opus 4.8 后长会话漂移下降(morphllm 对照页,[搜索摘要]);Composio Golden Eval 用 47 个用例做双引擎金集对照(47 vs 45 完成率,[搜索摘要]);promptfoo 有 `openai-codex-sdk` provider 可把 codex 当被测 harness 跑金集([搜索摘要],**其是否同时有 claude-code provider 未核实**)。
- **对我们的度量方案**(结合本仓资产):金集 = 固定交易日 × 固定 finalist 名单 × 冻结 slim/任务包(PIT 快照,replay 已有);每引擎跑 N≥3 次,度量 (i) 五档评级完全一致率、(ii) 评级序数漂移(±1 档比例)、(iii) schema 合规率、(iv) R:R 数字来源断言通过率(brief_lint 家族);引擎间对照只在「同任务包、同 schema」前提下成立——这又回到 §4(a):任务包必须由确定性 Python 生成,不能依赖引擎侧 skill 展开(Claude 的 `!`cmd`` 注入、$ARGUMENTS 替换在 Codex 侧不存在)。

### 6. 成本与配额

- **Codex**([官方已核] learn.chatgpt.com/docs/pricing):Free/Go/Plus/Pro/Business/Edu/Enterprise 全含;按 **5 小时窗口**计 local messages(Plus:Sol 10-100、Terra 25-200、Luna 250-2,000/窗),**「Additional weekly limits may apply」且数字不公布**;**CLI/SDK/IDE 与 ChatGPT 网页共享同一配额池**——这解释了我们 08-28「Codex T1-9 撞配额停」:9 个实施 task 的重会话把周限打穿。超额:买 credits(rate card:Sol 100 in/500 out、Terra 50/300、Luna 5/30 credits/M tok)或切 API key 按 token 计费(`CODEX_API_KEY`,CI 官方推荐 workload identity)。`/status` 与 usage dashboard 可查余量。
- **Claude Code**([官方已核] costs+prompt-caching+sub-agents 页):订阅 5h+周窗,subagent/workflow/背景任务同计;API 侧企业均值 ~$13/开发者/活跃日、90% 用户 <$30/日;**subagent 是独立上下文独立缓存前缀,首请求不吃父缓存(fork 例外:fork 完整继承、直接读父缓存)**;subagent/workflow 桶默认 5 分钟 TTL(订阅主会话 1h),`subagentPromptCacheTtl: "1h"` 可拉长(1h 写价更高);workflow 扇出对同前缀兄弟做 ≤5s hold 让它们读第一个 agent 写的缓存;subagent 描述总和 ≤15k tok 计入会话启动。启示:我们 11 个 `.claude/agents` 的 description 要控预算;l4 扇出若迁 workflow,同 agent 型同 schema 的每股卡天然吃同一前缀缓存。
- **跨引擎调度含义**:双引擎并行跑同一扫描 = 双份配额池(互不挤占),这是「按引擎分根」意外的正外部性;但 Codex 池与用户日常 ChatGPT 使用互挤,重扫描日要错峰。

---

## ④ 对我们的推荐架构

三条路(与任务书 4(a)(b)(c) 对应):

**路 A|编排全部下沉确定性 Python;引擎只做「读任务包→写产物」的叶子**
- 做法:prelude/assemble 继续持有全部循环与合并;每个 LLM 叶子(l3-rank、l4-intel、l4-card、company-intel…)的输入固化为任务包文件(现已如此:`_l4_prompt_<code>.md`),Claude 侧由 skill/subagent 消费,Codex 侧由外层 Python 起 `codex exec --json --output-schema … -o …`(或 Codex SDK thread)消费同一任务包;评级卡出口统一 JSON Schema。
- 优点:①两引擎行为同构、可金集对照(§5);②与 run capsule 完美契合(JSONL 事件流整段留痕,transcripts adapter 已有);③并发/重试/早停自己控,不受 Codex `max_depth=1`、父上下文回灌限制;④Codex 侧完全绕开「Workflow 无等价物」这个最大缺口;⑤符合本仓 08-29 契约层「产物先登记再写代码」的路 A 哲学。
- 缺点:①放弃 Claude Workflow 的原生进度面板/恢复语义(可接受:我们要的是法证与可重放,capsule 已自建);②Codex 叶子要解决沙箱联网探针(§3)与配额错峰;③`claude -p` 每叶子冷启动,记得 `--bare` + 显式 `--agents`/任务包,避免装载全套 skills。

**路 B|MCP server 暴露仓库工具,两个引擎都当客户端**
- 优点:工具面单点维护;两边 MCP 支持都成熟。
- 缺点:①只统一「工具」不统一「编排/子代理」,我们的核心差距不在工具面(取数已是零 LLM CLI);②多一个常驻进程与故障面;③Claude 官方自己都说 CLI 比 MCP 省上下文。→ **不推荐单独成路**,仅当未来要把 lake 查询开放给 IDE/桌面端时再补。

**路 C|双侧原生各维护一套(.claude/** 全功能 + .codex/agents/*.toml + .agents/skills + AGENTS.md),同步脚本+守卫**
- 优点:每边体验最佳(Codex 用户也能 `$scan-market` 隐式触发、spawn 命名 agent);社区有成熟样板(stafforini 双树守卫、superpowers 翻译层、npx skills symlink)。
- 缺点:①双份真身必然漂移,守卫只能拦提交拦不住语义(本仓 memory 有一整节「五份登记表互不派生」的前科);②Claude 专有 frontmatter(context: fork/agent/hooks/!`cmd` 注入)在 Codex 侧静默失效,「同名 skill 两边行为不同」比「Codex 没有」更危险;③我们 skill 是研究流程不是通用工具,受众就是自己,双份维护纯成本。

**推荐:路 A 为主干,叠一层「C 的最小子集」,不做 B。**
理由:本仓已经站在路 A 上(确定性漏斗 + 任务包 + 分根产物 + capsule),缺的只是 Codex 叶子适配器;而 skills 层面只需做三件低成本的事:
1. `SKILL.md` frontmatter 收敛到标准 6 字段(Claude 扩展字段仅保留在确需处,并意识到它们出 Claude 即失效);repo 根放 `AGENTS.md`(内容=CLAUDE.md 的引擎无关子集 + 指向任务包入口),`.agents/skills` → `.claude/skills` 做 symlink(Codex 官方支持 symlink;先探针本机 Codex 版本认的路径)。
2. Codex 叶子适配器:每类叶子一条 `codex exec` 包装(读同一任务包,`--json -o --output-schema`,`--sandbox workspace-write` + 联网探针,`web_search="live"` 供情报员),产物落 `context_codex/`;11 个 `.claude/agents/*.md` 中真正需要在 Codex 侧命名派发的(情报员/写手),用脚本从 md frontmatter 生成 `.codex/agents/*.toml`(name/description/developer_instructions 三必填直译;`tools:` 白名单无对应物,记进 manifest 的 unsupported 栏)。
3. 金集对照先行(§5 方案):迁移任何叶子前,先在冻结任务包上跑双引擎 N=3,量五档一致率与 schema 合规率,不达标的叶子留在 Claude 单引擎。
先探针清单(动工前必做,均标过「未核实」):① 本机 Codex 版本的 skills 发现路径与 `$skill` 触发;② `[sandbox_workspace_write] network_access` 默认值与 `-c` 覆盖是否让 tushare/yfinance 直连;③ `[agents]` vs `features.multi_agent` 哪个是现行开关、`max_threads/max_depth` 键名;④ `codex exec --output-schema` 对我们评级卡 schema 的 400 边界;⑤ AGENTS.md `project_doc_fallback_filenames` 能否直接挂 CLAUDE.md。

---

## ⑤ 参考 URL 表

**官方(已 WebFetch 核实,2026-08-30/31)**
| # | 内容 | URL |
|---|---|---|
| 1 | Agent Skills 规范全文 | https://agentskills.io/specification |
| 2 | Agent Skills 首页/adopter 名单 | https://agentskills.io/ |
| 3 | Codex Skills(官方) | https://developers.openai.com/codex/skills → https://learn.chatgpt.com/docs/build-skills |
| 4 | Codex AGENTS.md 规则 | https://learn.chatgpt.com/docs/agent-configuration/agents-md |
| 5 | Codex 配置参考(config.toml 全键) | https://learn.chatgpt.com/docs/config-file/config-reference |
| 6 | Codex CLI 命令/旗标 | https://learn.chatgpt.com/docs/developer-commands?surface=cli |
| 7 | codex exec 非交互模式(--json/--output-schema) | https://learn.chatgpt.com/docs/non-interactive-mode |
| 8 | Codex Subagents(官方) | https://learn.chatgpt.com/docs/agent-configuration/subagents |
| 9 | Codex MCP 配置 | https://learn.chatgpt.com/docs/extend/mcp?surface=cli |
| 10 | Codex Hooks(官方) | https://learn.chatgpt.com/docs/hooks |
| 11 | Codex 沙箱 | https://learn.chatgpt.com/docs/sandboxing |
| 12 | Codex 审批模型 | https://learn.chatgpt.com/docs/agent-approvals-security |
| 13 | Codex 定价/配额 | https://learn.chatgpt.com/docs/pricing |
| 14 | Codex changelog(0.151.0 @ 08-29) | https://learn.chatgpt.com/docs/changelog |
| 15 | Codex SDK(TS/Python) | https://learn.chatgpt.com/docs/codex-sdk |
| 16 | Codex/OpenAI 插件打包 | https://developers.openai.com/plugins/build/plugins |
| 17 | openai/codex releases | https://github.com/openai/codex/releases |
| 18 | Claude Code Skills(frontmatter 全表) | https://code.claude.com/docs/en/skills |
| 19 | Claude Code Subagents | https://code.claude.com/docs/en/sub-agents |
| 20 | Claude Code Hooks | https://code.claude.com/docs/en/hooks |
| 21 | Claude Code headless(-p/--json-schema/--bare) | https://code.claude.com/docs/en/headless |
| 22 | Claude Code Workflows | https://code.claude.com/docs/en/workflows |
| 23 | Claude Code 成本 | https://code.claude.com/docs/en/costs |
| 24 | Claude Code prompt caching(subagent 节) | https://code.claude.com/docs/en/prompt-caching |
| 25 | anthropics/skills(spec/ 目录) | https://github.com/anthropics/skills |
| 26 | Agent Plugins 1.0 规范 | https://agent-plugins.org/ |

**社区/第三方(原文已读)**
| # | 内容 | URL |
|---|---|---|
| 27 | superpowers Codex 翻译层 | https://github.com/obra/superpowers/blob/main/skills/using-superpowers/references/codex-tools.md |
| 28 | npx skills 安装器 | https://github.com/vercel-labs/skills |
| 29 | Claude/Codex 双树同步(2026-05-10) | https://stafforini.com/notes/how-i-keep-claude-code-and-codex-in-sync/ |
| 30 | Codex subagents GA 报道(2026-03-16) | https://simonwillison.net/2026/Mar/16/codex-subagents/ |
| 31 | Codex 自定义 agent TOML 详解(2026-04-27) | https://codex.danielvaughan.com/2026/04/27/codex-cli-custom-agent-definitions-toml-specialised-subagents/ |
| 32 | Codex Multi-Agent v2 vs Claude Workflows(2026-06-08) | https://codex.danielvaughan.com/2026/06/08/parallel-subagent-race-codex-cli-multi-agent-v2-claude-dynamic-workflows-architecture-comparison/ |
| 33 | Codex web_search 配置详解(2026-05-09) | https://codex.danielvaughan.com/2026/05/09/codex-cli-web-search-configuration-cached-live-domain-allow-lists-prompt-injection-defence/ |
| 34 | Agent Plugins 1.0 解读(2026-08-08) | https://codex.danielvaughan.com/2026/08/08/agent-plugins-1-0-open-standard-codex-cli-portable-skills-mcp-packaging/ |
| 35 | Claude Code 插件编排 codex exec | https://github.com/mgoulart/codex-subagents |
| 36 | codex exec 81 项旗标实测(2026-03-14) | https://gist.github.com/alexfazio/359c17d84cb6a5af12bac88fa1db9770 |

**仅搜索摘要(未读原文,引用时注意)**
| # | 内容 | URL |
|---|---|---|
| 37 | Codex hooks v0.114 起步/experimental 说法 | https://agenticcontrolplane.com/blog/codex-cli-hooks-reference |
| 38 | Codex vs Claude Code 对照(drift 抱怨) | https://www.morphllm.com/comparisons/codex-vs-claude-code |
| 39 | skills 路径迁移(~/.codex/skills→.agents/skills) | https://knightli.com/en/2026/04/29/difference-between-global-and-project-codex-skills/ |
| 40 | promptfoo Codex SDK provider | https://www.promptfoo.dev/docs/providers/openai-codex-sdk/ |
| 41 | 双引擎 prompt 定量评估(fetch 403) | https://medium.com/@Koukyosyumei/how-to-quantitatively-evaluate-prompt-quality-in-claude-code-and-codex-47f2f27f4bc5 |
| 42 | Claude 不读 AGENTS.md 的 5 种落地法 | https://gist.github.com/yurukusa/d36197848911f025add142abefcde685 |
| 43 | AGENTS.md/CLAUDE.md 同步指南 | https://aq.dev/guides/keep-agents-md-and-claude-md-in-sync/ |
| 44 | Codex CLI subagents TOML/并行(2026-03-26) | https://codex.danielvaughan.com/2026/03/26/codex-cli-subagents-toml-parallelism/ |

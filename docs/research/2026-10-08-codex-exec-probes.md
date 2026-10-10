# `codex exec` 真实宿主探针读数(Task 0)

- 日期:2026-10-08 14:23–14:30(本地),codex-cli **0.160.1**,二进制 `@openai/codex-darwin-x64`(x86_64,Rosetta)。
- 目的:为 `docs/superpowers/plans/2026-10-08-scan-token-cost-reduction.md` 的补丁 03(Codex headless 执行器,`codex exec` 三步传输)取真实读数;P1–P5 任一失败先改设计再打补丁。
- 额度:整组探针 5h 窗口 **31% → 33%**(2 个百分点),周额度 51% → 52%。
- 工作目录一律 `-C /Users/qingbin.zhuang/Personal/TradingAgents`,`--sandbox read-only`,`-c approval_policy="never"`。
- 原始事件流与 hook 负载落在 scratchpad `probes/`(A–H),不入仓。

## 结论一览

| # | 探针 | 结论 | 对设计的影响 |
|---|---|---|---|
| P1 | hooks 在 `codex exec` 下加载 | ✅ 会话层与**项目层**都加载并生效 | 三步传输可用;C4 边界在 headless 下仍是活的 |
| P2 | 线程 id ↔ rollout | ✅ 且 hook 负载直接给 `transcript_path` | 计量可不靠 glob,直接取负载字段 |
| P3 | `resume` 吃 `-c model` / `-c model_reasoning_effort` | ✅ | D2 保持 **a(三步)**,不降级 |
| P4 | `-c developer_instructions=` | ⚠️ **只在开线程时生效**,resume 时传了等于没传 | **设计改动**:角色指令必须在第 1 步(开线程)给 |
| P5 | `-c web_search="live"` | ✅ 真的发出 `web_search` 工具调用 | 盲搜角色可用 |
| N1 | `developer_instructions` **替换** Codex 基础提示词 | 首轮输入 121.9k → 22.5k | 每个角色线程省约 10 万输入 token |
| N2 | 开线程与干活换模型会注入 `<model_switch>`(17.9k 字符的完整基础提示词) | 已验证 | **设计改动**:开线程就用干活那档模型/effort,不要用廉价中继档 |

---

## P1 hooks 在 `codex exec` 下加载

**探针 A(会话层 hook,拿负载形状)**:`-c 'hooks.PreToolUse=[{hooks=[{type="command",command="sh <scratch>/hooklog.sh"}]}]' --dangerously-bypass-hook-trust`,提示词让它跑一次 shell。

- 5 次 shell 调用 → hook 日志 5 条,**hook 在 exec 下真的被调用**。
- 负载字段:`session_id / turn_id / transcript_path / cwd / hook_event_name / model / permission_mode / tool_name / tool_input / tool_use_id`。
- **`session_id` == `thread.started.thread_id`**(`01a11a2e-541c-7243-9902-89196a837795`),**没有 `agent_id`,没有 `agent_type`**。
  → `task_access.has_binding` 会按 `(session_id, '')` 命中,正是三步传输要的 session 级绑定;`decide()` 里「顶级无角色无 agent_id = 放行」的分支在**未绑定**时才命中,一旦绑定就走 `load_bound_access`。
- `tool_name` 为 `Bash`,`tool_input.command` 是整条命令 —— 与 `broker_tool_command` 的 `Bash` 模式一致。
- `cwd` 是仓库根(`path_allowed` 需要)。`permission_mode` 为 `bypassPermissions`。

**探针 B(项目层 hook,不带 `--dangerously-bypass-hook-trust`)**:用 PostToolUse 守卫自己的测试注入口
`SCAN_CONFIG_LINT_PATH=<一个故意违规的 jsonc>`,让模型执行 `ls -l autoresearch/contracts/scan_config.py`。

- 模型回帖里原样出现 `Script failed` + `[R4] HOOKPROBE_UNREGISTERED_KEY: 野块:注册表没有这个块 …` 共 35 条违规。
- 即:`.codex/hooks.json` 的 **PostToolUse 项目层 hook 在 exec 下加载、执行、exit 2 的 stderr 回灌给了模型**;信任来自 `~/.codex/config.toml` 的 `[hooks.state."…/.codex/hooks.json:post_tool_use:0:0"].trusted_hash`,**不需要** `--dangerously-bypass-hook-trust`。
- 两层合起来:PreToolUse 边界 hook 在 headless codex 线程上是活的。

> 仍未验证(留给 X1):绑定后的**拒绝实例**(bound 线程跑普通 shell 应被 `AGENT_INPUT_BOUNDARY` 拒)与**放行实例**(broker 命令应通过)。本组探针不写 `context_codex/` 任何文件。

## P2 线程 id 与 rollout

`thread.started.thread_id` = `01a11a2e-…`,rollout 落
`~/.codex/sessions/2026/10/08/rollout-2026-10-08T14-23-27-01a11a2e-541c-7243-9902-89196a837795.jsonl`。
额外读数:**PreToolUse 负载里直接带 `transcript_path`**,指向同一个 rollout。计量侧可以不靠文件名 glob。

## P3 `resume` 吃 `-c` 覆盖

线程 `01a11a30-…` 第 1 轮 `gpt-5.6-luna`/`low` 开,第 2 轮
`codex exec resume <id> -c 'model="gpt-5.6-sol"' -c 'model_reasoning_effort="high"'`。

- CLI 自己报 `This session was recorded with model gpt-5.6-luna but is resuming with gpt-5.6-sol`。
- rollout 第二个 `turn_context`:`model=gpt-5.6-sol effort=high`。✅
- `resume` 没有 `-m/--model`,只有 `-c`(与计划一致)。

## P4 `developer_instructions`:**只在开线程时生效**

- 在 **resume** 上传 `-c developer_instructions="…MARKER_DEVINSTR_7Q2…"`:rollout 里 **grep 不到** 这个标记,模型答非所问。❌
- 在 **开线程**上传同一条:模型第一句就回 `MARKER_DEVINSTR_7Q2`;rollout 第一条 developer 消息以它开头。✅
- 开线程给过之后,**后续 resume 仍然记得**(探针 E 在 resume 轮复述出了标记)。✅

→ **执行器改动**:`-c developer_instructions=<角色 toml 原文>` 放进第 1 步 `argv_open`,从第 3 步 `argv_work` 去掉。

`developer_instructions` 是 `ConfigToml` 的合法顶层键(二进制字段表里与 `instructions` / `model_instructions_file` 并列)。
顺带:`-c skills.enabled=false` **不是**合法键(CLI 明确回 `session-flags: skills.enabled is ignored`);
`include_apps_instructions` / `include_collaboration_mode_instructions` / `include_environment_context` 是合法键,
三个一起关只省约 270 token,本稿不采用。

## P5 `web_search="live"`

`codex exec resume <id> -c 'web_search="live"'` + 要求查一条当天财经头条 →
事件流出现 `{"type":"web_search","query":"2026-10-08 财经 头条","results":[…finance.sina.com.cn…]}`,模型给出标题与链接。✅

## N1 `developer_instructions` 替换基础提示词(降本读数)

同一个仓库、同样 `--sandbox read-only`、同样只要一句回答:

| 探针 | 配置 | 首轮 input_tokens | 第一条 developer 消息 |
|---|---|---|---|
| A | 默认(无 `developer_instructions`) | **121,859** | Codex 基础人格 + 工具 + skills 前导 |
| D | 带 `developer_instructions` | **19,675** | 我们的角色指令 + `<skills_instructions>`(共 24.5k 字符) |
| F | 带 `developer_instructions`,`gpt-5.6-sol`/`low` | **22,525** | 同上 24.5k 字符 |
| H | F + 三个 `include_*=false` | **22,254** | 少了 2.3k 字符的多 agent 前导 |

即角色指令走 `developer_instructions` 比「基础提示词 + 把角色指令塞进用户消息」**每个线程少约 10 万输入 token**。
剩下的大头是 `<skills_instructions>`(约 24.5k 字符),没有合法开关关掉。

## N2 换模型 resume 会把基础提示词塞回来

- 线程以 `luna`/`low` 开、以 `sol` resume:resume 轮被注入一条 **17,885 字符**的 `<model_switch>` developer 消息,
  内容就是完整的 Codex 基础提示词(`You are Codex, an agent based on GPT-5 … # Personality …`);
  该轮 input 跳到 75,209(探针 E)。
- 线程以 `sol`/`low` 开、以 `sol`/`low` resume(探针 F→G):**没有** `<model_switch>`,
  resume 轮只多一条 593 字符的 `<permissions instructions>`;open 22,525 → work 45,461(其中 29,184 命中缓存)。

→ **执行器改动**:开线程用**干活那一档**的 `model` 与 `model_reasoning_effort`,
放弃「廉价中继档开线程」的想法(省下的那点开销会被 17.9k 字符的 `<model_switch>` 吃回去还倒贴)。
`session.timeouts.codex_open_s` 与 `OPEN_PROMPT`(只回 OK)保留。

## P6 / P7

- **P6**(`--sandbox workspace-write` 下 broker 写 `context_codex/…`):未做,按计划并入 Task 7 的 X1 演练。
- **P7**(`codex -p scanhost` 与桌面 App 选 profile):随 Task 6 建 profile 时验;只影响 mailbox 回退路径(D7)。

# headless 驱动器探针(2026-09-26,claude 2.1.283)

设计稿:`docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md` §6 C0 / §4 A1-0。
全部在本机、本仓库根目录、交互会话的 Bash 工具内实测;session id 可在 `~/.claude/projects/-Users-qingbin-zhuang-Personal-TradingAgents/` 下按 id 找到 transcript。

| # | 问题 | 结论 | 证据 |
|---|---|---|---|
| ① | 交互会话的 Bash 里能否起 `claude -p` | **能**(嵌套无限制) | session `06d99f98-2abe-442a-af8e-75f710a630f4`,`--max-turns 1` 回 OK;`total_cost_usd` 0.70(35k 1h cache 写 = 默认会话的完整系统提示 + 工具 + CLAUDE.md + 记忆) |
| ② | `--agent l4-card` 是否装载项目 agent 定义 | **是**:model 走 frontmatter(`claude-opus-5-5`);PreToolUse hook `AGENT_INPUT_BOUNDARY` 在 headless 下拦截了对 `autoresearch/__init__.py` 的 Read,拒绝文案与交互模式逐字相同 | session `8c8e103f-f801-420e-951c-c1d336c6a69e`,$0.26,cache 写 30.5k / 读 30.0k,2 turns |
| ③ | transcript 落盘与计量 | `~/.claude/projects/<slug>/<session-id>.jsonl`(`--session-id` 指定即得);结果 JSON 自带 `usage`(input / cache_creation / cache_read / output / server_tool_use)、`total_cost_usd`、`modelUsage`、`permission_denials` | 同 ①② |
| ④ | `--agent l4-intel` 能否 WebSearch | **能**(sonnet-5,回了带 URL 的一行) | session `60f9b52d-e5f1-4135-bedd-06eebd115207`,$0.12,cache 写 23.0k |
| ⑤ | session_agent host 模式真跑一场 | **未做**(需干净会话 + 一场真扫;批 2 首任务) | — |

## 含义

- 设计稿 R1(agent/hook 不装载)、R2(嵌套限制)、R3(计量目录约定)三条风险解除。
- 每次 headless 调用的固定成本 ≈ 一次 subagent 的 cache 写(23–35k tokens),与今天的 subagent 相同;同一 agent 类型的前缀在 1h 内可命中 cache。
- 研究角色的 model / effort / tools / hook 全部由 `.claude/agents/*.md` 决定,驱动器不需要再解释一遍;`--effort` 可在命令行覆盖。
- 计量可直接读结果 JSON,不必解析 transcript;transcript 仍在,供 capsule 绑定。

## 待办

- ⑤ session_agent host 模式真跑(批 2 Task 1)。
- `--max-budget-usd` 在订阅额度下是否生效未测;批 2 用 `--max-turns` + 驱动器墙钟兜底。
- 默认会话的 35k 前缀含 MEMORY.md 与 skill 列表;研究角色不应看到记忆(`--agent` 已替换系统提示,②的 30.5k 里是否仍含记忆待批 2 用 transcript 核)。

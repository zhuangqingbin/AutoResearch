# headless 研究线程前导瘦身读数(2026-10-10)

两个引擎的 headless 研究线程都带着研究角色用不到的前导。量法全部零推理或单轮 `只回复 OK`;改动是
`session.preamble` 三个键(注册表 + `session_cfg` + 两个执行器 + 测试),不碰模型、effort、评级、契约。

## Codex(`codex debug prompt-input`,零推理;开线程那一轮的模型可见输入)

角色 l4_intel,生产同款覆盖(approval_policy / model / effort / developer_instructions),cwd = 仓库根。

| 变体 | 条目 | 总字符 | 第一条 developer 消息 | 项目 AGENTS.md 那条 user 消息 |
|---|---:|---:|---:|---:|
| 生产(改前) | 5 | 35,955 | 26,006 | 6,436 |
| `project_doc_max_bytes=0` | 5 | 31,328 | 26,006 | 1,810(剩下的是 `~/.codex/AGENTS.md` 的 superpowers 引导) |
| `skills.max_context_tokens=1000` | 5 | 18,687 | 8,739 | 6,435 |
| 两者(改后生产) | 5 | 14,062 | 8,739 | 1,810 |

- 第一条 developer 消息里 17.3k 字符是 skills 目录(四个项目技能 + superpowers 的描述),研究角色从不调用 skill。
- 10-08 第 1 场 31 线程、233 次模型调用,每次调用都带这份前导;改后每次调用少约 22k 字符。
- **token 实测**(同日一次真实 `codex exec` 开线程,生产同款覆盖):首调输入 16,924 token,10-08 同角色为 22,884 → 每次调用 **−5,960 token(−26%)**。按字符比例估的是 −14k,实际只有它的四成:被删的目录与 AGENTS.md token 密度低于剩下的内容。窗口占比的降幅仍要等下一场真跑。
- 改后用执行器真实的 `open_overrides()` 再渲染一次:5 条、14,062 字符,与上表「两者」一致。
- 配置键叫 `codex_skills_catalog_budget` 而不是照抄 Codex 的 `max_context_tokens`:法证层把含 `token` 的键名一律当密钥(`identity._SECRET_KEY_RE`),RunContract 含它 `begin` 就拒。
- `-c` 的整数必须裸写(`"0"` 会被 Codex 以 `invalid type: string "0", expected usize` 拒掉),`toml_value` 已按类型分流。

## Claude(`claude -p --agent … --max-turns 1`,单轮 `只回复 OK`;首次调用的 cache_creation + cache_read)

| 变体 | agent | token |
|---|---|---:|
| 生产(改前) | l3-repair(705 字符) | 19,013 |
| `--disable-slash-commands` | l3-repair | 19,013(前缀逐字节相同,skills 列表不在 agent 提示里) |
| 临时项目目录(无记忆、无项目 settings) | l3-repair | 8,248 |
| `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` | l3-repair | 8,144 |
| `--settings '{"autoMemoryEnabled": false}'`(改后生产) | l3-repair | 8,144(与上一行同一前缀,cache 命中) |
| `--settings '{"enabledPlugins": {"superpowers@claude-plugins-official": false}}'` | l3-repair | 17,809 |
| 生产(改前) | l4-card(20,021 字符) | 30,604 |

- 自动记忆 = 10.9k token,是 l4-card 前导的 36%。更要紧的是边界:MEMORY.md 里是用户裁定与实跑读数
  (「A 级恒 0」「BUY 不赚」之类),研究角色按 C4 不该看见,之前每张卡都看见了。
- superpowers 插件的 SessionStart 注入约 1.2k token,本次没关(与 hook 装载有牵连,另议)。
- Claude 侧计费大头是输出(10-07 第 5 场卡的 787.5k 输出 ≈ 卡成本七成),前导瘦身省的是 cache 写读,
  每场估 $1–2;主要收益是边界,不是钱。

## 没做的(各自要等价检验或裁定)

- 情报员网查回灌:Codex 的 `web.run` 每次 4 条查询、`response_length:"long"`、回灌 11–16k 字符,9 次后上下文涨到 85k。
  缩 `response_length` 或 `tools.web_search.search_context_size` 改变模型看到的内容,走等价检验。
- effort / model 降档:两边都是研究费用大头(Codex 情报员 42%、Claude 卡输出七成),按 10-03 规矩先过等价检验。
- 行业 brief 9 线程合并、满卡只给 E6 top-k:结构改动,待裁(`docs/superpowers/specs/2026-10-10-token-roi-and-dev-redline-brainstorm.md`)。

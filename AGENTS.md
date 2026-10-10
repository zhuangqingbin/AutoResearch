# AGENTS.md — 给 Codex 等非 Claude agent 的项目操作手册

本仓的完整操作手册在 `CLAUDE.md`(主会话先读它)+ `.claude/skills/*/SKILL.md`(四个项目技能,每个是一份可执行的流程说明书)。本文件只做两件事:告诉你技能在哪、以及非 Claude harness 下怎么适配。**研究子 agent 不适用这句**,见下一节。

## 开发红线:token 性价比(2026-10-10 用户裁定,所有开发遵守)

规则、机器件与命令的唯一真身是 `docs/dev-redline.md`(八条红线,每条有会红的机器件:回放门、场后 redline + 断路器、
静态成本清单、档位锁、预算线只降不升、角色消费者)。动手前的五条守则:

1. 新增 LLM 角色 / 工具回合 / prompt 段落前,先写「线程 × 调用 × 上下文」与消费者;agent 文件先登记字符预算。
2. 量法脚本首次使用就进 `autoresearch/research/`,读数进 `docs/research/`,先查再量。
3. 读大文件用范围,日志看尾,全量测试只看摘要。
4. 子 agent 只派可拆的大块任务并写明理由;复审派一个。
5. 计划稿给实施者的部分一屏以内,推演放附录。

## 研究子 agent 的输入边界（2026-09-30 C4）

研究角色只读编排器冻结的角色指令片段与本阶段任务输入，只写当前 attempt 声明的输出；不自行读 CLAUDE.md、技能说明、源码或未登记材料。`session_v1` 的 DispatchRequest 冻结任务清单，宿主 session/agent 身份由根会话绑定；缺少绑定、清单损坏或过期 attempt 均拒绝。根会话、未绑定的明确开发角色（worker / explorer）与确定性命令壳沿职责例外运行；已有研究绑定始终优先。

`.codex/hooks.json` 的 PreToolUse hook 检查结构化文件路径与固定任务 broker；研究角色不能使用通用 shell 扩大访问。配置存在与本地测试通过均不表示当前宿主已经加载。hook 更新后须新宿主重载，并完成启动时要求的一次性审查；实际允许/拒绝证据尚未验收时保持 `UNVERIFIED` 与 PILOT。绑定步骤、工具协议和已知限制见 `docs/session-agent/access-boundary.md`。


## 统一 Session Agent 入口

全市场、单股、宏观、行业和档案首覆已登记到 `session_v1` 协议。显式使用新编排时运行
`uv run --no-sync python -m autoresearch.session_agent`，控制循环统一为
`begin → next → claim → execute/宿主推理 → submit → finish`。模型推理仍由当前 Codex 或
Claude Code 官方订阅会话完成，Python 不调用模型 API。完整教程见
`docs/session-agent/README.md`。

`finish` 后必须对其机器返回的 canonical 报告路径运行
`uv run --no-sync python -m autoresearch.session_agent verify-report --report-path <PATH> --expected-run-id <RUN_ID> --level full`。
交付结论只能引用该 `VerificationResult`；报告字节未绑定会明确返回 `UNBOUND_REPORT`。
默认入口只由 `session_agent.evaluation.accept_workflow` 的双宿主 REAL_SESSION proof 门决定；
当前矩阵未齐时继续显式标为 PILOT，不能因合成测试通过自行切换默认。

## 项目技能(trigger → 说明书)

| 技能 | 何时用 | 说明书 |
|---|---|---|
| scan-market | 扫描全 A 股 / 全市场选股 / 哪些板块值得买 | `.claude/skills/scan-market/SKILL.md`(流程细节 `STAGES.md`) |
| stock-research | 研究/分析单一股票(full 全量报告;"快速看一眼/出张卡"= lite 决策卡) | `.claude/skills/stock-research/SKILL.md` |
| macro-research | 全球宏观 / 资产配置 / "今天大盘怎么看"(lite=市场研判) | `.claude/skills/macro-research/SKILL.md` |
| sector-research | 研究单个申万行业(景气/格局/龙头映射) | `.claude/skills/sector-research/SKILL.md` |

**全部项目技能一律软链**进 `~/.codex/skills/`(codex 原生 skill 发现同构于 `<name>/SKILL.md`;软链 = 两边改任一侧即同步,勿复制)。换机重建 / 新增技能后把名字补进循环再跑:

```bash
for s in macro-research scan-market sector-research stock-research; do
  ln -sfn "$PWD/.claude/skills/$s" ~/.codex/skills/$s
done
```

## 非 Claude harness 的适配规则

1. **引擎隔离(2026-08-11 用户裁定,最高优先)**:会话第一件事 `export AUTORESEARCH_ENGINE=codex`(必做——沙箱外的自动检测不可靠,忘了会写进 Claude 的目录)。产物根按引擎分:你只写 `context_codex/` + `reports_codex/`,Claude 写 `context_claude/` + `reports_claude/`,**唯一共享的是数据湖 `lake/`**(确定性行情数据,引擎无关)。skill 文档里的 `$CTX`/`$RPT` = `context_${AUTORESEARCH_ENGINE:-claude}` / `reports_${AUTORESEARCH_ENGINE:-claude}`。两边闭环状态(账本/权重/档案/知识库)互不相通——codex 侧首跑 `weights.json`/档案缺失 = 从零积累,属预期,勿去 Claude 目录借。**禁止读写 `context_claude/`、`reports_claude/`。**(保送票单 `pinned.jsonc` 在 skills 树里,随软链共享,是输入不是产物。)
2. **确定性层原样可用**:所有 `uv run --no-sync python -m autoresearch.<...>` 命令(取数/漏斗/组装/校验门/预热)与 harness 无关,照 SKILL.md 跑即可;python 侧路径按 `AUTORESEARCH_ENGINE` 自动落对根。产物落 `$RPT/`、`$CTX/`(已 gitignore)。
3. **LLM 编排层**：优先使用统一 `session_v1` 任务循环。全扫的日常路径是 headless(2026-10-08 起):本会话后台起 `scripts/scan_run.sh --engine codex --date <分析日> --skip-readiness`,runner 以 `codex exec` 线程跑每个推理任务(角色 toml 的 `developer_instructions` 原样注入,模型/档位来自冻结配置),本会话不派发、不中继、不轮询;只有 headless 不可用时才回退到 mailbox 宿主循环,那时每个 INFERENCE task 由 Codex 在当前订阅会话内按登记角色完成(`mailbox wait` 缺省只给宿主视图,`by_reference` 已开)。旧 `Workflow` 只作为真实宿主尚未验收时的 `legacy` 回退，不再作为新编排协议的实现真值。所有输入文件、输出契约和校验门与领域模块一致——**门必须跑,产物契约不许改**。
   ⚠️ GATE1 要带 `--decide-run-mode`(2026-09-13 起与运行模式判定合并成一次调用):`python -m autoresearch.scan.gates gate1 <date> --decide-run-mode`(哨兵档人工拉满再加 `--force-full`)。它同时落 `run_mode.json` —— 四态(FULL / FORCED_FULL / SENTINEL_EMPTY / SENTINEL_PINNED)是下游**唯一**的模式事实源:`gate4`、`assemble`、报告横幅都只读它,不从 finalists 空否反推。不带这个 flag,codex 侧就没有这份文件:`run_mode.load()` 返回 None = 「不知道」,于是哨兵日会被 GATE4 按全扫口径拿「覆盖率不足」毙掉,报告也印不出哨兵横幅。
4. **不变量(与 harness 无关,一律遵守)**:持仓尺度=超短 1~2 日(隔夜主尺 `gap_c1_o2`,T+1收买→T+2开卖,勿推 swing);0 买日 ≠ 门过严,勿松门凑单;喂 L3/L4 的只能是描述性地形(market_pack 里的 `sector_healthy_top3` 是 L5 专用,不得写进地形/卡片);评级只由本股 rubric 三门定。
5. **数据源**:A 股走 tushare(东财 push2 被封),需 `TUSHARE_TOKEN`;湖(`lake/`)命中即零网络,数据契约层(A 级空帧抛异常拒入湖)不得绕过。

## 常用入口速查

```bash
uv run --no-sync python -m autoresearch.scan.prewarm            # 夜间预热(launchd 19:30 亦可手动)
uv run --no-sync python -m autoresearch.scan.prelude <date>     # 确定性前奏一键(L0-L2 等)
uv run --no-sync python -m autoresearch.scan.frame <date> --json # market_pack(策略师输入)
uv run --no-sync python -m autoresearch.scan.gates gate1 <date> --decide-run-mode  # GATE1 + 运行模式(合并)
uv run --no-sync python -m autoresearch.scan.assemble <date>    # L5 整合(内含 self_review 硬门)
uv run --no-sync python -m pytest -q                            # 全量测试
```

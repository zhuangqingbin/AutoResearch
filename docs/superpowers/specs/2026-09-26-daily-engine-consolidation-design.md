# 日报引擎收敛 · 双尺 · 自治 agent —— 三线开发文档

- 日期:2026-09-26(中秋,非交易日;最近数据日 09-24)
- 状态:**设计稿,待用户审阅**;审阅通过后用 writing-plans 出逐任务执行计划
- 起因:用户三问 ——「skill 是否够精炼」「这是不是 agent」「项目怎么规划」;brainstorm 后用户裁定「**全部都做**」(A 收敛降本 · B 换尺 · C 自治)
- 关联稿:`docs/specs/2026-09-24-buyability-realignment-design.md`(批 4 十日真跑 = **规则冻结窗**,本稿所有行为类改动在窗内只能离线/影子)· `docs/superpowers/specs/2026-09-04-token-efficiency-two-lines-design.md`(S2 壳迁移从未做,本稿 A1 取代它)· `docs/superpowers/specs/2026-09-13-session-agent-migration-design.md`(session_v1 PILOT,本稿 C 决定它的去留)· `docs/PANORAMA.md`

---

## 0. 一页摘要

| 问题 | 答案 | 本稿对应 |
|---|---|---|
| skill 够不够精炼 | 不够,但**文档不是 token 大头**(≈3–5%/场);它们是 changelog 冒充操作手册,病在漂移与双源,不在钱 | A2 |
| token 去哪了 | 09-17 场 $40.5 等价:**141 个只跑命令的 Sonnet 壳占 62% 加权输入(≈$13.5)**、主会话 14%(≈$8.5)、真正研究 45% | A1 |
| 这是不是 agent | 是「skill 驱动、人触发、人陪跑」的多 agent 流水线;不是自治 agent(人是调度器,失败要人修,不从结果学) | C |
| 为什么迷茫 | 三个身份打架:隔夜选股器(9 月 7 场 0 买)/ 研究仪器(最成功,产出负结果)/ 工程制品(近两月 609 提交的去处) | §2 |

**三线关系**:A 是地基(把每场做便宜、做稳、把编排从 Workflow JS 搬进 Python 驱动器),C 在 A 之上加 headless 执行器与调度器,B 是与 A/C 正交的研究线(先影子,冻结窗后裁决)。顺序:**A → C,B 并行影子**。

---

## 1. 现状读数(2026-09-26 实测,全部可复算)

### 1.1 使用频率(报告目录数)

| skill | 07 月 | 08 月 | 09 月 | 结论 |
|---|---|---|---|---|
| scan-market(Claude) | 32 | 18 | 7(另 6 场失败) | **唯一每日产品** |
| stock-research full(Claude) | 0 | 2 | 3 | 偶发 |
| macro-research full / sector-research full | 0 | 0 | 0 | 只有 lite 档在扫描里被调 |
| scan-market(Codex) | — | 3 | 4 | 非每日 |

### 1.2 产品结果(9 月全部成功场)

7 场:≥Overweight **0** 只、relative BUY **全部 BLOCKED**;持仓复核 2 只;每场 ~10 张卡(70% 早停,238/239 停在 P3,停因是实质判断不是取数失败)。

### 1.3 一场的钱去哪(09-17,`reports_claude/scan/20260917-0917_2152/token_usage.md`)

| 角色 | 个数 | 加权输入占比 | 估算成本 | 性质 |
|---|---|---|---|---|
| general-purpose 壳(Sonnet low) | 141 | 62% | ~$13.5 | 只跑一条命令回退出码 |
| 主会话(Opus max,67 轮) | 1 | 14% | ~$8.5 | 转播 + 收 170 个通知 |
| l4-intel(Sonnet max) | 11 | 13% | ~$5.5 | 研究 |
| l4-card(Opus max) | 11 | 8% | ~$9.3 | 研究 |
| l3-rank ×2 · sector-brief ×4 · macro-brief ×1 | 7 | 3% | ~$3.4 | 研究 |
| **合计** | 171 | 10.04M 加权 | **$40.48** | 墙钟 53m16s |

壳的根因:Workflow 脚本没有文件系统,每条确定性命令都得包成 `agent()`;`detached()` 轮询每一轮又是一个新壳;`l4-stock.js` 自注「一只票 18 次 agent」。09-24 复盘也独立量到「≈50 个编排壳 ≈$7/场」。

### 1.4 文档体量(`.claude/` 下 ≈16 万 token)

| 文件 | 行 | 估 token | 装载时机 | 历史/沿革行占比(正则粗估) |
|---|---|---|---|---|
| scan-market/SKILL.md | 288 | 13.5k | 主会话每轮 | 14% |
| scan-market/STAGES.md | 426 | 24k | 主会话按需 Read | 15% |
| scan-market/scan_config.jsonc | 376 | 11k | 主会话 Read 后随 args 传 | — |
| agents/l4-card.md | 168 | 9.7k | **每张卡装载**(×11/场) | 8% |
| stock-research/lite-playbook.md | 199 | 9.3k | session_agent roles 引用 | 与 l4-card.md 同一契约的**手抄本**,契约行计数已不齐(早停行 1:2、执行线 2:3) |
| stock-research/engine-playbook.md | 246 | 11.4k | full 档 | — |
| agents/l3-rank.md | 55 | 5.5k | 每场 1–2 次 | 20% |
| 四个 SKILL.md 的 `session_v1 编排入口` 段 | ~12 行 ×4 | ~0.5k ×4 | 逐字重复 4 份 | 100% |

有 24 处测试引用锁着 l4-card.md / lite-playbook.md 的契约字样(`tests/test_agent_defs.py`、`test_skill_docs_refs.py`、`tests/scan/test_retention.py` 等),瘦身有护栏。

### 1.5 研究结论(不重做)

隔夜主尺 `gap_c1_o2` 四年普查六主格零正证据(08-28);L3 无选股 alpha(07-12);否决有效、挑选无效(826 finalist:UW 后 fwd10 −1.91pp、Sell −22.6pp vs Hold −0.59pp,09-24);唯一正读数在 5–10 日尺(低位转强 fwd_5/10 +0.63/+0.99pp,08-21);A 级 BUY 结构性为 0(09-25)。

---

## 2. 目标 / 非目标 / 成功标准

### 2.1 产品定义(用户 09-26 裁定「全部都做」的展开)

每个交易日 23:00 前,**无人值守**产出并送达一份 `brief.md`,内含:① 市场地形 ② 行业地形 ③ 持仓盯梢与持仓卡 ④ ≤10 张研究卡摘要 ⑤ 隔夜 BUY 结论(现有 E6 A/R 分级,不改)⑥ **10 日尺观察席**(B 线,影子起步)。人只读 brief,只在 FAILED 通知时介入。

### 2.2 成功标准(数字,可机检)

| 线 | 指标 | 现值 | 目标 | 测法 |
|---|---|---|---|---|
| A | 每场估算成本 | $40–50 | **≤ $15** | `token_usage.md` 合计 |
| A | 编排壳 agent 数 | 141 | **0** | `token_usage.md` general-purpose 行 |
| A | 研究 agent 数 | 29 | 不变(±复核) | 同上 |
| A | 墙钟 | 53–74 min | ≤ 35 min | `render --view timing` |
| A | brief.md 与改造前 | — | **除计量段外 byte 相同**(同输入回放) | `capsule replay` + diff |
| A | 主会话装载的 skill 文档 | SKILL 36KB + STAGES 63KB | SKILL ≤16KB + STAGES ≤26KB(实施后 15.4KB / 22.6KB) | `tests/test_doc_budgets.py` 字节预算 |
| C | 连续无人值守成功 | 0 | **5 个交易日** | `_ops/scan_run_<date>.log` + capsule `completeness_ok` |
| C | 人工介入次数 / 5 日 | — | 0(FAILED 通知除外) | 日志 |
| B | 10 日尺影子账本 | 无 | ≥40 交易日、预注册假设有读数 | `views/stage_rulers.csv` + 普查稿 |

### 2.3 非目标

- 不恢复 learning 闭环、prompt 自改、L4 TTL 复用(用户裁定不变)。
- 不放松 Hold 四条件、不为「开火」改 rubric(09-25 裁定)。
- 冻结窗内不改任何召回/L3/L4/评级行为(09-24 批 4)。
- 不新增法证层、不新增普查族(B2 除外,它是本稿裁定的研究线)。
- 不做 Codex 侧 headless 执行器(接口留着,见 C4)。

---

## 3. 总体架构变化

### 3.1 现在

```
人开会话 → skill(13.5k) → Workflow scan-market.js(壳×~40)→ 主会话一次性全派 N×l4-stock.js(壳×~13/股)→ 主会话跑 L5 五条 → 人读 brief
```

### 3.2 目标

```
launchd 21:20 ──► scripts/scan_run.sh ──► python -m autoresearch.scan.driver <date> --executor headless
                                              │  确定性步骤:进程内直跑(exec_capture 留痕)
                                              │  判断步骤:邮箱协议 → 执行器兑现
                                              │     headless 执行器:claude -p --agent <role> --effort … --json-schema …
                                              │     host 执行器:交互会话按邮箱派 Agent(零壳)
                                              └─► L5 → brief.md → delivery(推送)→ capsule finalize
交互模式:skill(≤6k)→ 主会话后台起 driver --executor host → Monitor 邮箱 → 派 Agent → 转播 CP → 读 brief
```

**不变的东西**:数据层、L0–L2、`l4_tasks` 任务簿、gates、assemble、E6、capsule、agent 定义、所有机读契约。**变的东西**:谁在调度(JS Workflow → Python 驱动器)、谁跑命令(Sonnet 壳 → 驱动器进程)、谁触发(人 → launchd)。

### 3.3 驱动器与 session_agent 的关系(决策点,A1-0 定)

`session_agent`(1.29 万行)已实现 `begin/next/claim/execute/submit/finish` 环、SESSION/L4_TASKBOOK 双 owner、artifact 注册与回执,但真实宿主验收 INCOMPLETE。本稿**首选复用它当驱动器内核**,只新增 headless 执行器(`session_agent/hosts/headless_claude.py`)。A1-0 用一场 host 模式真跑做判决:

- 跑通 → driver = `session_agent.service` + 执行器;两个 JS Workflow 降为 `LEGACY_ORCHESTRATION_FALLBACK`,C 验收后删除。
- 两场内跑不通或每场返工 > 1 天 → 降级为薄驱动器 `autoresearch/scan/driver.py`(≤800 行,直接调领域模块 + 同一邮箱协议),session_agent 冻结不删。

不允许出现第四个编排对象(Workflow JS / session_agent / driver 三选一收敛到一个)。

### 3.4 冻结窗

`buyability-realignment` 批 4 = 10 个交易日真跑(09-25 起;国庆休市,预计 10 月下旬结束)。窗内:A1/A2/C 属**编排与文档**改动,不改评级行为,可做,但每次真跑必须 `capsule replay` 证明确定性产物 byte 相同;A3(intel 收紧)与 B 全部离线/影子。

---

## 4. 工作流 A:收敛与降本

### A1 编排壳税归零(取代 09-04 稿的 S2)

**设计**

1. **邮箱协议**(`autoresearch/scan/mailbox.py`,新):
   - 驱动器写 `$STAGING/_dispatch/<id>.request.json`:`{id, role, prompt, schema, effort, model, phase, subject, attempt}`;执行器写 `<id>.result.json`:`{id, ok, result|error, session_ref, started_at, ended_at}`。
   - 原子写(临时文件 + rename)、fcntl 锁与 `l4_tasks` 同款;超时与瞬时错误分类复用 `contracts/retry.py`。
   - 驱动器只认 result 文件,不认执行器自报(09-15 壳自报 pending 事故的直接教训)。
2. **确定性步骤进程内直跑**:frame / strategist_pack / prelude / gate1 / sector reuse+pack / l3 prepare+lint+repair-pack+apply+finalists / gate2 / l4 shared+pledge+seats+calendar+consensus+prompts / l4_tasks init+preflight+prepare+success+failure / assemble / gate4 / usage_harvest / usage_reconcile / post_run observe / capsule finalize。全部经 `trace.exec_capture` 子进程留痕(与今天一致),只是调用方从 Sonnet 壳变成驱动器。
3. **判断步骤**七种角色不变:macro-brief、sector-brief ×K、l3-rank(+repair)、l4-intel ×N、l4-card ×N、ens-review ×(0–2N)。effort/model 仍由 `user_config.resolve_agent_bundle` 解释,驱动器把解释结果写进 request。
4. **执行器两种**:
   - `host`:交互会话里主会话 Monitor 邮箱目录,收到 request 就 `Agent(subagent_type=role, prompt)`,把返回写 result。主会话不再跑任何命令、不再一次性全派 N 个 Workflow。
   - `headless`(C1 实现):驱动器内线程池,`claude -p --agent <role> --effort <e> --output-format json --json-schema <s> --permission-mode bypassPermissions --session-id <uuid> "<prompt>"`,并发帽 = `budgets.concurrency.l4_stock`。
5. **L4 每股链**在驱动器内:`preflight → (prepare_slim ∥ intel request) → intel_guard/intel_status → card request → (ow/sell review → 2 个 review request,同档早止) → success/failure`。逻辑逐行搬自 `l4-stock.js:300–561`,任务簿状态机不动。
6. **trace-control 边界事件**:原由壳发,改驱动器进程内 `events.append_event`,事件 schema 不变(`tests/trace/test_completeness.py` 锁)。
7. **usage_harvest**:新增 `--session-refs <file>` 读驱动器记录的 session 列表(headless 每个角色一个顶级 session,transcript 在 `~/.claude/projects/<slug>/<session-id>.jsonl`);host 模式仍走主会话 subagents 目录。计量表新增 `dispatcher` 列(host|headless)。

**任务**

| # | 任务 | 文件 | 验收 |
|---|---|---|---|
| A1-0 | **前提探针**(半天):① `claude -p --agent l4-card` 是否装载项目 agent 定义(model/effort/tools/hook);② 从交互会话的 Bash 里能否起 `claude -p`(嵌套限制);③ `-p` 会话 transcript 落盘路径与 usage 字段;④ WebSearch/WebFetch 在 `-p` 下可用;⑤ `session_agent begin/next/claim/execute/submit/finish` 用当前 legacy 产物走一遍 scan FULL 的 host 模式 | scratch 脚本,结果写 `docs/research/2026-09-2x-driver-probes.md` | 五条各有 PASS/FAIL 与证据;③ 决定 A1-7 的实现;⑤ 决定 §3.3 |
| A1-1 | 邮箱协议 + 契约 | `scan/mailbox.py`、`contracts/artifacts.py` 登记 `_dispatch/`、`contracts/retry.py` | 原子性/锁/超时单测;变异测试:删锁 → 并发写测试红 |
| A1-2 | 驱动器骨架 + 确定性阶段(Prelude→GATE1→L3→GATE2→L4-prep) | `session_agent/service.py` 扩展 或 `scan/driver.py` | 用 09-17 冻结输入回放,staging 产物与原 run byte 相同(`capsule replay`) |
| A1-3 | L4 每股链 + 复核 | 同上 + `scan/l4_tasks.py`(不改状态机) | 与 `l4-stock.js` 逐分支对照表;失败分类一致;任务簿终态一致 |
| A1-4 | L5 收尾链(五条 `&&` 语义进代码) | 同上 | GATE4 fail 时 observe 不执行、capsule 冻结 FAILED(09-15 事故锁) |
| A1-5 | host 执行器(交互模式):skill 改为「起 driver + Monitor 邮箱 + 派 Agent + 转播 CP0–CP7」 | `scan-market/SKILL.md`、`scan/l4_watch.py`(邮箱视图) | 一场真跑:壳 agent = 0、brief 与前一日格式一致 |
| A1-6 | 边界事件进程内化 | `trace/events.py` 调用点 | `test_completeness` 绿;事件条数与旧 run 同 |
| A1-7 | usage_harvest 多 session 绑定 | `trace/usage_harvest.py`、`transcript_binder.py` | headless 试跑的 token_usage.md 覆盖全部角色,`未计量 0 份` |
| A1-8 | JS Workflow 降级为 fallback,文档同步 | `.claude/workflows/*.js` 头注 + `docs/session-agent/acceptance.md` | grep 全仓无生产调用;`node --check` 保留 |
| A1-9 | Codex 镜像:driver 引擎无关性验证(host 模式在 Codex 会话跑一场) | `workspace.py` 已隔离;`.codex/` 说明 | Codex 一场真跑,壳 = 0 |

**预期**:壳 $13.5 → 0;主会话 $8.5 → ≤$1(host 模式只收 ~30 个通知、不读大文件);研究部分不变 ≈$18;**≈$19–20/场**,再叠 A3 到 ≤$15。

### A2 skill / agent 文档瘦身(契约一字不动,砍历史与双源)

**原则**:每份文件只回答「现在怎么跑」;沿革、事故编号、裁定日期、回滚杆一律搬到 `docs/`(git 本身就是沿革)。凡被机器读的字样(机读口径、模板、七词停因、入场行、执行线、标题契约)逐字保留,由现有 24 处测试守。

| # | 文件 | 现 | 目标 | 做法 |
|---|---|---|---|---|
| A2-1 | 四个 SKILL.md 的 `session_v1 编排入口` 段 | 4 × 12 行 | 各 1 行指针 | 指向 `docs/session-agent/README.md` |
| A2-2 | `scan-market/SKILL.md` | 288 行 / 36KB | ≤16KB(实施后 15.4KB) | 留:核心原理表、前置、六段流程(改为 driver 口径)、CP0–CP7、铁律、常见坑 5 条。删:配置大表(jsonc 注释即文档)、每键回滚杆、capsule 核验节(→ `docs/ops/forensics.md`)、L5 路径解析 15 行注释(→ 代码)、所有事故日期 |
| A2-3 | `scan-market/STAGES.md` | 426 行 / 63KB | 现行机制 ≤26KB(实施后 22.6KB) | 「已被实证否决的方向」「历史产物」「开放线头」→ `docs/research/scan-negative-results.md`;「运维细节」→ `docs/ops/scan-ops.md`;「行为变更的入口」压成 10 行 |
| A2-4 | `agents/l4-card.md` | 168 行 / 23.4KB(×11/场) | ≤20KB(模板+机读口径+评级规则 ≈13KB 是契约;实施后 19.1KB) | 删 `pr_2026*`/`fb_2026*` 编号、「由来」段、执行线的 5 行证据叙述(留 2 行规则 + 1 行出处指针)、重复的「读盘边界一毫米不动」×3 → 1 |
| A2-5 | `agents/l3-rank.md` | 55 行 / 13.7KB | ≤8KB(实施后 8.0KB) | 硬约束 A–I 每条保留规则句,沿革理由压成半句或指针 |
| A2-6 | 双源合并 | lite-playbook ↔ l4-card;macro-playbook lite 节 ↔ macro-brief;sector-playbook lite 段 ↔ sector-brief | agent 文件为唯一真身 | playbook 对应节改为「见 `.claude/agents/<x>.md`」;`session_agent/roles.py` 与 `contracts/artifacts.py:566`、`scan/retention.py:72`、`self_review.py:878` 的引用改指 agent 文件;新增测试:playbook 不得再含契约模板正文(变异:粘回去 → 红) |
| A2-7 | `scan_config.jsonc` 注释 | 376 行 | 保留(它是配置的文档) | 只把 SKILL 大表里独有的「生效点」补进对应键注释 |
| A2-8 | Codex 镜像 | `.codex/agents/*.toml` | 同步 A2-4/A2-5 | 双引擎 diff 脚本进测试 |

**验收**:`tests/test_agent_defs.py`、`test_skill_docs_refs.py`、`tests/scan/test_retention.py`、`test_product_shape_lint.py` 全绿;token 估算脚本读数达标;一场真跑卡片 lint 零新增 warn。

### A3 intel 收紧(离线证据门,冻结窗后上线)

- 观察:早停 238/239 停在 P3,停因「资金流出」65 张全可由 slim 数字判;每张卡都先付一份 Sonnet-max intel(≈$0.5)。
- 设计:`l4_intel.skip_when_dead: false`(新旋钮,三件套):谓词在代码(`scan/l4/intel_gate.py`):主力净额 <0 ∧ cmf_20 <0 ∧ obv_mom_20 <0 ∧ 无 📅 催化 ∧ 非 📌 → 不派 intel,任务包标 `intel=skipped_dead`,卡按现规则回退 ≤3 条卡内网查。
- 离线验收(A3-1):对 8 个真跑日 81 张卡回放谓词 —— 命中率、命中卡的最终评级分布、命中卡里 intel 事件段是否曾提供 ≥Hold 的催化。命中卡中出现过 ≥OW 或 T0 负面硬信号 → 谓词收窄或放弃。
- 上线(A3-2):冻结窗后;首场真跑对照 P3 早停率与 intel 数。

### A4 主会话纪律(交互模式)

- 扫描会话只做:`capsule begin` → 起 driver(后台)→ Monitor 邮箱与 `l4_watch` → 派 Agent → 转播 CP → 读 brief。不 Read STAGES.md、不 Read scan_config.jsonc(driver 自己读)。
- 失败:driver 冻结 FAILED,主会话只报路径,不在同会话修代码(09-15 纪律不变)。

### A5 冻结声明

本稿实施期间:不新增 trace/ 法证能力、不新增 research/ 普查族(B2 除外)、不动 E6 规则版本、不做 session_agent 双宿主验收矩阵以外的新协议。

---

## 5. 工作流 B:双尺(隔夜尺留作执行尺,10 日尺升为选股尺候选)

### B0 裁定记录

08-05 裁定「主尺 = 隔夜 `gap_c1_o2`,勿再推 swing」;09-26 用户裁定「全部都做」把 B 重新放上桌。本稿的处理:**不替换主尺**,新增第二把一等尺 `SWING_RULER = "fwd_10_oc"`,两把尺各管各的产品面:隔夜尺管 T+1 尾盘入场/T+2 开盘退出与 E6 BUY;10 日尺管「观察席」与持仓周级判断。谁当 `MAIN_RULER` 是 B4 的裁决,不在本稿预设。

### B1 尺与账本

- `common/ruler.py` 新增 `SWING_RULER`、`swing_entry_flag()`(D+1 开盘可买 = 现有 `buyable`),不动 `MAIN_RULER`/`REL_GAP_RULER`。
- `recommendations.csv` 已有 `fwd_5_oc/fwd_10_oc`;`outcome fill` 成熟判据增加「D+10 已到」第二档(`outcome_status_swing`),nightly_close 第 1 步顺带填。
- `populations rulers` 的 `views/stage_rulers.csv` 已算四把阶段尺 → 增加 fwd_10 列(读现有 parquet,零新取数)。

### B2 预注册普查(离线,冻结窗内可做)

- 面板:与 W3 普查同源(1095 日),人口:L0 / L1 / L2 / finalists / 卡(按评级、lane、早停因、card_kind)。
- 假设(先写后跑):H1 finalists ∩ 评级 ≥Hold 在 fwd_10 相对全市场中位超额 >0 且 t≥2;H2 lowturn lane 的 fwd_10 超额 >0(08-21 已见 +0.99,验证是否稳);H3 否决集合(UW/Sell)在 fwd_10 显著为负(09-24 已见 −1.91/−22.6);H4 E6 R 级 BUY 在 fwd_10 与隔夜尺符号是否一致。
- 停机规则:H1 与 H2 同时不成立 → B 线止于「观察席只展示不推」;不追加第五个假设。
- 产出:`docs/research/2026-1x-swing-ruler-census.md`,读数进 memory。

### B3 影子产品面(零 LLM,冻结窗内可上,不改任何评级/BUY)

- brief 新增 ⑦「10 日观察席」:当日 finalists 中评级 ≥Hold ∧ 非 📌 ∧ 入场行 ≠ 禁止 的票,按 conviction 排,附「10 日尺历史读数:样本不足/±x pp」一行;brief 预算 3,000B 不够则改「⑦ 见 summary §12」,summary 加节。
- 卡片契约不动(隔夜口径);10 日情景段留到 B4。

### B4 裁决(冻结窗后 + 影子 ≥40 交易日)

- 若 H1 成立:提案「`MAIN_RULER` 换 `fwd_10_oc`」——一行改动触发 80 处消费点回归(`grep MAIN_RULER`),E6 相对 BUY 改按 10 日尺、持有期改 10 日、卡片增 `## 10 日情景` 可选节(契约加法)。**这是用户裁决,不在本稿内实施。**
- 若不成立:观察席保留为展示,B 线关闭,memory 记负结果。

---

## 6. 工作流 C:自治 agent

### C0 前提(与 A1-0 合并做)

`claude 2.1.283` 已有 `-p/--agent/--effort/--json-schema/--output-format json/--permission-mode/--session-id/--max-budget-usd`。待验:`--agent` 装载项目 `.claude/agents/*.md` 与 `settings.json` hooks;嵌套调用;transcript 路径;网络工具可用;订阅额度下 `--max-budget-usd` 是否生效(不生效则用 `--max-turns` + 驱动器墙钟兜底)。

### C1 headless 执行器

- `session_agent/hosts/headless_claude.py`(或 `scan/executors/headless_claude.py`):线程池兑现邮箱 request;每个角色一个独立 `claude -p` 进程 = 独立上下文(比 subagent 更强的隔离,HostProfile 的「独立上下文回执」直接由进程边界满足);超时 = 角色档位(intel 12 min / card 25 min / l3 30 min);瞬时错误按 `contracts/retry.py` 重试一次。
- 输出:`--json-schema` 用现有 CARD/INTEL/STAGE 的 schema(从 JS 搬到 `contracts/agent_output.py`)。
- 记录:每个 request 的 session_id 进任务簿/capsule,供 A1-7 计量。
- 接口留给 Codex:`executors/base.py` 抽象 `dispatch(request) -> result`;Codex 实现另立任务,不在本稿。

### C2 调度与守护

- `scripts/scan_run.sh`(新)+ `~/Library/LaunchAgents/com.tradingagents.scan-run.plist`:交易日 21:20 起;先跑 `trade_date` 与 stk_factor_pro 就绪探针(行数连续两次不变且 ≥5300,间隔 5 min,最多等到 22:30),再 `capsule begin` + `driver --executor headless`;日志 `$RPT/_ops/scan_run_<date>.log`;并发锁防与人工会话同跑(`flock $CTX/.scan_run.lock`)。
- 与现有 19:30 prewarm、20:45 nightly_close 的关系:prewarm 保留(缩短取数);nightly_close 改到 23:30 或改为 scan_run 成功后串行触发(避免读写同一账本)。
- 失败策略:阶段级重试一次(driver 内);仍失败 → `capsule finalize FAILED` + 推送「FAILED · 阶段 · 一句原因 · 日志路径」;**不自动改代码、不自动重跑第二场**。

### C3 送达

- `delivery.channel` 旋钮(三件套):`none | bark | mail | file`。首选 Bark(iOS 免费推送,一条 curl,零凭证存仓;token 放 `.env`);mail 走本机 `mail`;`file` 落到用户指定同步目录(iCloud/Obsidian)。推送正文 = `brief.md` 原文(≤3KB 刚好一条通知)+ 报告路径。
- 送达失败不影响 run 状态,只记 `_delivery.json`。

### C4 session_agent 定位

- A1-0 ⑤ 跑通 → driver 内核 = session_agent,C1 的执行器是它第一个真实宿主,**顺便产生它缺的真实 proof**(scan FULL 场景);其余四类入口(stock/macro/sector/dossier)按需接同一执行器,不作为本稿验收。
- 跑不通 → session_agent 冻结(不删、不改、README 标 FROZEN),driver 走薄实现。

### C5 验收

连续 5 个交易日:launchd 触发 → 23:00 前收到 brief 推送 → capsule `integrity_ok ∧ completeness_ok` → 人工介入 0。任一日 FAILED 计数归零重来。之后再看成本目标(≤$15)与 A3。

---

## 7. 任务总表与批次

| 批 | 任务 | 依赖 | 冻结窗 | 估时 |
|---|---|---|---|---|
| 0 | A1-0 + C0 探针;B0 裁定进 memory 与 `common/ruler.py` 注释;A5 冻结声明进 `docs/PANORAMA.md` 头 | — | 可做 | 1 天 |
| 1 | A2-1…A2-8 文档瘦身 | — | 可做(零行为) | 1–2 天 |
| 2 | A1-1…A1-4 驱动器(host 模式)+ A1-6 + A1-7 | 批 0 | 可做,每场 replay byte 对账 | 4–6 天 |
| 3 | A1-5 交互模式接线 + 一场真跑;A1-8 Workflow 降级;A1-9 Codex 一场 | 批 2 | 可做 | 2 天 |
| 4 | C1 headless 执行器 + C2 调度 + C3 送达;5 日无人值守验收 | 批 3 | 可做 | 3–4 天 + 5 交易日 |
| 5 | B1 + B2 普查 + B3 影子席 | 批 0(与批 1–4 并行) | 可做(离线/影子) | 2–3 天 |
| 6 | A3-1 离线验收 → A3-2 上线;B4 裁决 | 冻结窗结束 + B3 ≥40 日 | 窗后 | 各 1 天 + 裁决 |

每批结束都要:两引擎全量测试(`uv run --no-sync pytest -q`)、变异探针(把新守卫删掉测试要红)、真跑读数进 memory。

---

## 8. 风险与前提待验

| # | 风险 | 处置 |
|---|---|---|
| R1 | `claude -p --agent` 不装载项目 agent 或 hooks | 用 `--agents <json>` 内联 agent 定义 + `--settings` 指向项目 settings;A1-0 决定 |
| R2 | 交互会话内不能嵌套起 `claude -p` | host 执行器本就不依赖嵌套;headless 只在 launchd 下跑 |
| R3 | headless 会话不计入 usage_harvest 现有目录约定 | A1-7 显式 session 列表;计量缺席写 `UNMEASURED` 不写 $0(现规则) |
| R4 | session_agent 内核过重、真跑返工 | §3.3 时间盒:两场;超时降级薄驱动器 |
| R5 | 订阅额度(5 小时窗)被夜跑吃掉,白天没额度 | headless 每场 ≤$15 等价;调度时段固定 21:20;`--max-turns` 兜底;超预算旗只告警 |
| R6 | B 线被读成「换主尺已定」 | B0 明写:不替换;B4 单独裁决 |
| R7 | 文档瘦身误删机读契约 | 24 处测试 + A2-6 新增反向测试;瘦身 PR 单独一批,与代码改动不混 |
| R8 | 无人值守场失败静默 | C2 的 FAILED 推送 + `_ops` 日志 + `_health.json`(nightly_close 同法) |
| R9 | 冻结窗内 driver 改变确定性产物 | 每场 `capsule replay` 对账为硬门,不同即回退 fallback |

---

## 9. 回滚

- A1:`--executor` 缺省仍指 legacy Workflow 直到批 3 验收;一行切回。
- A2:文档瘦身单独提交,`git revert` 即回。
- A3:`skip_when_dead:false`。
- B:`SWING_RULER` 只被观察席与账本读;删旋钮 = 观察席消失,其余零影响。
- C:卸载 plist;driver 仍可交互模式跑。

---

## 10. 不做清单(本稿明示)

新法证层;新普查族(B2 除外);session_agent 五入口全矩阵验收;Codex headless 执行器;放松 Hold 四条件;恢复 TTL 复用;恢复 learning;在冻结窗内改召回/L3/L4/评级;LLM 收编 brief。

---

## 11. 开放决策(用户审阅时回答)

1. §3.3 驱动器内核:同意「首选 session_agent,两场时间盒,超时降薄驱动器」?
2. §6 C3 送达渠道:Bark / mail / 同步目录,选一个首发。
3. §5 B0 措辞:确认「新增 10 日尺为第二把一等尺,不替换主尺,B4 再裁」是你说的「全部都做」的意思。
4. 调度时段 21:20 与 nightly_close 挪到 23:30,是否接受。

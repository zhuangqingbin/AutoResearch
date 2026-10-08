# 全扫 token 降本(两引擎)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让一场全 A 扫描在 Claude 与 Codex 两个引擎上都回到「研究角色花的钱 ≈ 全场的钱」:宿主会话不再承担任何研究中继,一张卡的契约错只重做那一张卡而不是整场,研究角色的档位只在等价检验通过后才动。Codex 一场全扫要装进 Plus 计划的一个 5 小时窗口并留余量。

**Architecture:** 不改任务图、评级、三门、E6、冻结计划与证据链。改的是四件事:① 日常全扫的入口从「主会话 mailbox 中继」换成已有的 headless 执行器,并给 Codex 补一个 `codex exec` 传输(开线程 → session 级 C4 绑定 → resume);② mailbox 回退路径瘦身(`by_reference` 打开、`wait` 只回宿主视图);③ 领域校验拒绝从「不可重试的 CONTRACT_ERROR」改成「带校验原话重做一次」的第三类重试;④ 情景收益精度校验按声明精度留容差(10-07 第 5 场的唯一阻断)。研究角色的 effort 不在本稿改,只把 B1 等价检验排进去。

**Tech Stack:** Python 3.13 · uv · pytest;Claude Code 2.1.294(`claude -p --agent`;研究角色的模型是钉死的全 ID `claude-opus-5-5`,版本升级不会静默换代);codex-cli 0.160.1(`codex exec --json / resume`,`-c` 覆盖,项目 hooks);tushare 数据湖。

**Spec:** 没有单独设计稿;本稿第 1–2 节即设计。依据:`docs/research/2026-10-07-scan-session-v1-r5-readout.md`(第 5 场读数与 R5-1~R5-5)、`docs/session-agent/README.md`(宿主循环与 headless)、`docs/superpowers/plans/2026-10-03-review-implementation.md`(B1 配方、B8 两个开关)、`docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md` C 线(headless 自治,当时「不做 Codex headless」)。

基准时间:2026-10-08 12:00(Asia/Shanghai)。主工作树 HEAD `32277ab`,另有 37 个未提交改动(10-04/10-07 的修复);本稿所有事实都是对这棵工作树与 10-07 两边真实产物实测的。

**状态(2026-10-08 15:00 更新)**:用户裁定「开始开发」,D1–D5 按建议走。Task 0 探针已做(读数
`docs/research/2026-10-08-codex-exec-probes.md`,两处设计修订见 2.2 的引用块),**九个补丁已按序打进主工作树**,
Task 1–6 完成;Task 7 真跑阶梯、Task 8 B1、Task 9 收口待做。补丁文件(第 8 节)保留为改动记录与回滚手段。

---

## 0. 一页摘要

| 引擎 | 10-07 一场的钱去哪(实测) | 本稿之后(估算,未真跑) |
|---|---|---|
| Claude r5 `20261007T151001404046Z`(FAILED,$53.5 API 价) | 主会话 $16.4(189 次调用、上下文 61k→461k 不压缩、加权输入 62%);l4-card ×9 $22.6;l3-rank $5.1;l4-intel ×7 $6.8;brief 类 $2.6 | 主会话 ≈ $0.5–1(起 runner、等结束、读摘要,≤10 次调用);研究角色不变 → 一场 ≈ $37–38(−30%)。再往下只能靠 B1(研究角色占 68%) |
| Codex 第 2 场 `20261007T150717722796Z`(停在 L4 中段) | 主会话 34.7M / 全场输入 41.2M(84%;294 次调用、均上下文 122k、含 34 次 sleep 轮询);研究子 agent 只有 6.4M | 主会话 ≈ 0.5M;研究子 agent ≈ 8–10M 一整场(到 GATE2 实测 2.6M + L4 7 票 × ~0.6M)→ 占一个 5h 窗口约 1/4(第 1 场 27M 用掉 72% → 窗口 ≈ 37M) |
| 两边 | 一张卡契约错 → CONTRACT_ERROR 不可重试 → 整场 BLOCKED;Claude 10-02 起 5 场全 FAILED ≈ $333 零发布;Codex 第 1 场因 hook 不认 `webrun` 整场重来 | 校验拒绝 = 这张卡带校验原话重做一次(Claude ≈ $2.5 / Codex ≈ 0.6M),第二次仍拒才 BLOCKED;R5-1 本身已修,10-07 被拒的那张卡回放通过 |

四个病因对应四处改动,顺序与依赖见第 5 节;哪些事只有你能裁见第 3 节。

> **Codex 估算的一次修正(2026-10-08 探针之后)**:上表的「研究子 agent ≈ 8–10M 一整场」是按 10-07 `spawn_agent` 子 agent 的实测推的。改走 `codex exec` 之后,每个角色是一个独立线程,前导不复用上一个角色的缓存,但角色指令走 `developer_instructions` 把 Codex 的基础提示词换掉了:实测开线程首轮 **22.5k** 输入、resume 干活轮 **45.5k**(其中 29.2k 命中缓存),而不给 `developer_instructions` 的裸线程首轮就是 **121.9k**。所以三步传输的固定开销是每个角色约 2 万多输入 token,不是原先担心的十几万。真实数字以 X1 为准。

## 1. 现状与证据

| 事实 | 证据 |
|---|---|
| 全扫只有 session_v1 一条路,主会话是中继 | `.claude/workflows/scan-market.js:71` C4 守卫硬抛 `HOST_CAPABILITY_REQUIRED`;README「宿主循环」每个研究任务 `wait → Agent/spawn_agent → bind-access → complete`,约 7 次工具往返,每次重读整段上下文 |
| Claude 主会话的上下文里装的是什么 | r5 transcript `3a2b5542…`:Bash 结果 25.0 万字符 / 156 次(`mailbox wait` 回显整份请求含 prompt 全文);27 份逐字转发的 Agent prompt 17.7 万字符(均 6.1k);28 条任务通知 5.3 万;主会话自身 effort max 的思考同回合内也被重读 |
| Codex 主会话的上下文里装的是什么 | rollout `01a116e6…`:exec 输出 49.2 万字符 / 190 次(请求回显 11–27k 字符/份)、`followup_task` 7.4 万、`spawn_agent` 0.8 万、34 次 `sleep` 轮询;主会话模型 `gpt-6.1-sol` xhigh(`~/.codex/config.toml` 10-06 改) |
| Codex 的 `token_usage.md` 不含主会话 | `reports_codex/scan/_failed/20261007T113825310166Z/capsule/usage/token_usage.md` 末行「主会话自身的消耗不在内」;`CodexTranscriptAdapter.locate` 只认 `capsule/agents/bindings.jsonl`,主 rollout 从未被绑成 `main` 行 → 报表比真实小一个数量级 |
| Claude 研究角色的涨幅来自 5.5 + max 的思考 | 同角色每份输出 l4-card 18k(09-11 Opus 5)→ 48k(09-28 Opus 5.5)→ 88k(10-07);l3-rank 32k → 214k;单卡 $0.90 → $2.51;10-03 钉版只钉了全 ID 没降档(`.claude/agents/l4-card.md` frontmatter `effort: max`) |
| 减负件早已写好但没打开 | `scan_config.jsonc:483` `by_reference: false`;headless 执行器(`executors/headless_claude.py`,只接 claude)从没跑过一场全扫;B1 工具 `research/noise_floor.py` 就绪、实验没跑(约 $260) |
| 第 5 场唯一阻断是精度契约 | `contracts/execution.py:351-381` 三处 `Decimal('0.000000001')`;被拒卡写 `0.0240/0.0020/-0.0220`、`rr 1.09`,精确值 0.024018…;`capsule/evidence/attempt_records/l4.688578.a1.review2/a1/failure.json` = `CONTRACT_ERROR: DomainValidationError: … scenario return contradicts declared entry/exit` |
| 校验拒绝不可重试是设计出来的 | `contracts/retry.py` 只有 `TASK_ATTEMPT = {RATE_LIMIT, CONNECTION, TIMEOUT, STALE_TASK}`;`runner._settle_inference` 把 submit 的任何异常记 `CONTRACT_ERROR`;`service.fail` 据此写 BLOCKED;`_session_retries` / `_l4_retries` / `l4_tasks.mark_failure` / `service.retry_l4` 四处只认 TASK_ATTEMPT |
| `codex exec` 能当传输 | `codex exec --help`(0.160.1):`--json`(JSONL,首事件 `thread.started.thread_id`)、`-c key=value`(TOML)、`-o`、`--sandbox`、`-p`(`$CODEX_HOME/<name>.config.toml`)、`resume <id> [-c …]`、`--dangerously-bypass-hook-trust`(说明 exec 下 hooks 会加载,需持久信任——本项目 hooks 已信任);官方 config 参考:`developer_instructions`(注入 developer 消息)、`web_search = cached|live|…`、`approval_policy = never`;hooks 文档:PreToolUse 负载含 `session_id`(顶级线程 = 线程 id)、无 `agent_id` |
| C4 绑定按什么找清单 | `task_access.has_binding` 只看 hook 负载的 `session_id` + `agent_id`(`agent_id=''` 的 session 级绑定对该会话全部调用生效);`bind_context(headless=True)` 当前只允许 claude;环境变量与 prompt 按设计**永远**不能选清单(模块首行文档) |

取数方法(别再量):Codex 读 `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` 的 `event_msg/token_count`(`last_token_usage.input_tokens` = 当次上下文,`rate_limits.primary/secondary.used_percent` = 5h/周额度),按 `session_meta.payload.session_id` 归组;Claude 读 capsule `usage/_token_usage.json` 的 rows(`role=main`)与主会话 transcript 的 `message.usage`。两份脚本在本会话 scratchpad(`codex_rollouts.py`、`claude_main_anatomy.py`、`codex_main_anatomy.py`),Task 9 收进 `research/`。

## 2. 设计裁定(为什么是这几处)

**2.1 宿主退出研究回路,两个引擎同一条路。** 研究 prompt、绑定命令、轮询输出在主会话上下文里每出现一次,后面每次调用都要再付一次。headless 执行器已经把 Claude 侧的这笔账清零(每个任务一个 `claude -p` 顶级会话,证据链、计量、超时、重试都齐);缺的只是「交互会话也走它」和「Codex 也有它」。所以日常入口改成 `scripts/scan_run.sh --engine <引擎> --date <日> --skip-readiness`,主会话后台起、结束回合、等完成通知、读摘要。mailbox 宿主循环保留为回退。

**2.2 Codex 传输 = 开线程 → 绑定 → resume(三步)。** hook 只认负载里的 `session_id`;`codex exec` 没有 `--session-id`,线程 id 只在线程存在后才有。备选:(a) 单步 `codex exec --json`、读到 `thread.started` 立刻绑定 —— 与模型第一次工具调用赛跑,实践上几乎总赢(绑定毫秒级、首个模型响应秒级),但形式上不保证;(b) 环境变量带令牌、hook 据此选清单 —— 违反 C4 模块首行的设计前提(环境变量不能选清单),否决;(c) 三步:先用 relay 档模型开一个只回「OK」的线程拿 id,绑定,再 `resume` 带冻结 prompt 与 `-c` 覆盖干活 —— 多一轮几千 token,确定性,选它;(a) 作为 `resume` 不吃 `-c model` 覆盖时的回退(Task 0 探针 P3 定)。角色的 `developer_instructions` 从 `.codex/agents/<role>.toml` 原样读出,用 `-c developer_instructions=` 注入,与 `spawn_agent` 的 developer 消息同源;模型/档位/`web_search` 来自冻结配置的 `agent_spec`。

> **2026-10-08 探针后的两处修订**(读数:`docs/research/2026-10-08-codex-exec-probes.md`)。P3 过,三步方案保留;但 P4 的结果把「哪一步给什么」换了:
> ① **`developer_instructions` 只在开线程那一步生效** —— 传给 `resume` 被静默丢弃(rollout 里 grep 不到),传给开线程则一直留在线程的 developer 上下文里,后续 resume 仍然认。所以角色指令随第 1 步走,第 3 步不再带。
> ② **开线程不用廉价 relay 档,用角色自己那一档** —— 线程中途换模型会被 Codex 注入一条 17,885 字符的 `<model_switch>` developer 消息(内容是它完整的基础提示词),比开线程省下的多得多;同档开同档干活则只多一条 593 字符的 `<permissions instructions>`。执行器因此把 model / effort 的来源收敛成一处:两轮要么都声明同一档,要么都不声明(落到用户配置)。`agent_engines.codex.tiers.relay` 不再参与 headless。
> 顺带一个降本读数:给了 `developer_instructions` 的线程**首轮输入 19.7k–22.5k**,不给的是 **121.9k** —— 它替换掉了 Codex 的基础人格/工具前导。即角色指令走 `developer_instructions` 而不是塞进用户消息,每个角色线程省约 10 万输入 token。

**2.3 mailbox 回退路径瘦身。** `by_reference` 打开后宿主只传一行指针;`wait` 缺省不再回显 `prompt` 全文(它与 `host_prompt` 重复)和 `agent_spec/input_paths/instruction_refs`,`--full` 排障。这不减少 7 次往返,只减每次往返带进上下文的字节,所以它是回退路径的止血,不是主方案。

**2.4 校验拒绝 = 重做这一个任务。** 新增第三类重试 `VALIDATION_REPAIR = {DOMAIN_VALIDATION}`(`contracts/retry.py`,不并进 `TASK_ATTEMPT`,遵守该模块「判据显式选边」的训诫):`runner.submit_error_class` 把 `DomainValidationError`(含 cause 链)分成 `DOMAIN_VALIDATION`,其余 submit 拒绝仍是 `CONTRACT_ERROR`;`service.fail` / `_session_retries` / `_l4_retries` / `l4_tasks.mark_failure` / `service.retry_l4` 六处都认它;新尝试的 prompt 末尾带「修订要求」段(`dispatch.repair_hint`,SESSION 任务读上一 attempt 的冻结失败记录,L4 子树读票据 `last_error`)。封顶仍是 `session.max_attempts=2` / 票据 `max_attempts=2`,第二次仍拒才 BLOCKED。证据上安全:新 attempt 是全新上下文、全新 transcript,与 TIMEOUT 重试同形;不复用任何上一尝试的产物(与 07-29「不要任何复用」裁定无关:那是跨日 TTL 复用)。

**2.5 R5-1 按声明精度留容差。** `SCENARIO_PRECISION = 0.0001`:容差 = 声明末位的一半、封顶半个万分位(声明 2 位小数不放宽,声明 6 位更严);EV 再加各情景收益舍入按概率加权的传播;R:R 再加 `dg/|l| + g·dl/l²` 的传播(两头收益的舍入都放大进比值)。卡面契约补一句「收益与 EV 保留 4 位小数,R:R 2–4 位;校验按声明精度留容差」。方案 ②「scan 用途强制 entry=null」会让 EV/R:R 在扫描卡上全部失真,不采;R5-2(复核员拿分析日收盘假设入场)是契约纪律问题,随 2.4 的重做提示一并解决——被拒原因会写进下一次的 prompt。

**2.6 研究角色档位不动,B1 排进去。** 10-03 的规矩:降档前过等价检验。headless 两边都有之后,B1 的两档都走 headless(`--effort` 显式透传),不必再临时改 agent 定义;成本约 $260 等价额度,由你裁定何时跑(D6)。

**2.7 主会话模型。** headless 之后主会话每场 ≤10 次调用,模型无关紧要。回退到 mailbox 时:Codex 用 profile `~/.codex/scanhost.config.toml`(`gpt-5.6-sol` + `medium`,`codex -p scanhost`),Claude `/model opus` 并把 effort 调到 low。

## 3. 需要你先裁定的事

每项给了建议。D1–D5 不裁则按建议走;D6–D8 不阻塞 Task 0–7。

> **2026-10-08 裁定结果**:用户「开始开发」= D1–D5 全走建议的 a,已实施。
> D2 的降级条件(P3 失败)没有发生,保持三步;但 P4 把第 1 / 第 3 步的分工改了(见 2.2 引用块)。
> D7 的 profile 已建(Task 6)。**D6 裁了:a —— Task 7 两边 headless 跑通后再跑 B1。**
> **仍待你裁**:D8(Claude 主会话缺省模型;headless 场无所谓,只在走 mailbox 回退时才有区别)。

| # | 问题 | 选项 | 建议 |
|---|---|---|---|
| D1 | 日常全扫走 headless(两引擎),mailbox 只作回退? | a. 是;b. 只做 mailbox 瘦身(2.3)不换入口 | **a**。b 只减字节不减往返,Codex 主会话仍占大头 |
| D2 | Codex 传输用三步(开线程 → 绑定 → resume)? | a. 三步;b. 单步 + `thread.started` 后立刻绑定(赛跑);c. app-server | **a**;P3 探针若证明 `resume` 不吃 `-c model` 覆盖,降到 b 并把「首次工具调用前绑定完成」写成监视断言 |
| D3 | 领域校验拒绝给一次带原话的重做? | a. 是(封顶同 max_attempts);b. 保持不可重试,只修 R5-1 | **a**。b 下任何新的契约口径问题都还会让整场作废 |
| D4 | R5-1 修法 | a. 按声明精度留容差(本稿);b. scan 用途 entry 强制 null | **a**,已用 10-07 被拒卡回放验证 |
| D5 | `by_reference` 缺省打开? | a. 开;b. 关 | **a**。只影响 mailbox 回退路径;盲搜角色(无 READ)自动仍传全文 |
| D6 | B1 等价检验何时跑(约 $260 等价额度) | a. Task 7 两边 headless 跑通后;b. 现在;c. 不跑、不降档 | **a**。headless 让两档都不用改 agent 定义 |
| D7 | Codex 回退路径的主会话模型 | a. profile `gpt-5.6-sol`/`medium`;b. 保持 `gpt-6.1-sol`/xhigh | **a**;headless 之后几乎用不到 |
| D8 | Claude 主会话缺省现在是 Fable 5.1·max(`/model`) | a. headless 场照旧(≤10 次调用,无所谓);mailbox 回退时切 Opus 5.5 + low;b. 全局切回 Opus 5.5 | **a** |

## Global Constraints

- 每个新 shell 第一条命令固定引擎:Claude 会话 `export AUTORESEARCH_ENGINE=claude`,Codex 会话 `codex`。只读写本引擎的 `context_<engine>/`、`reports_<engine>/`,不读另一引擎目录;`lake/` 共享。
- 不改评级阈值、三门、主尺 `gap_c1_o2`、E6、冻结计划形状、证据闭包;不恢复任何跨日复用。
- `begin` 之后不改代码;run 中途改码 = 该 run 只算 shakedown,修完起新 run。
- 改 `scan_config.jsonc` 后 `uv run --no-sync python -m autoresearch.scan.config_standard` 必须 0 条违规(新键三件套:注册表 + 消费点 + 测试锁;本稿新增 `session.timeouts.codex_open_s` 已按三件套做)。
- 测试两个引擎都跑(`AUTORESEARCH_ENGINE=codex|claude`),判据是**失败集合不比基线多**,不看绝对数(Claude 引擎下 `tests/session_agent`、`tests/forensics` 有既有基线红)。
- 补丁 03 改了 `autoresearch/session_agent/task_access.py`(`POLICY_FILES` 之一):打上去之后两边已有的边界证明全部 STALE(10-01 的本就已 STALE),按 10-02 矩阵稿 E1 时机重采,先 Codex 后 Claude。`.codex/hooks.json` 与 `scripts/hooks/*` 未动,Codex 不需要重新信任 hook。
- Task 0 的 Codex 探针与 Task 7 的真跑都花订阅额度:在 5h 窗口刚重置时做,先 `--max-parallel 4`。
- 主工作树禁止 `git reset --hard`、`git clean`、对真实索引 `git add -A`。提交要你点头。
- 交付只引用机器结果:`verify-report` 的 `report_covered / publication_ok / orchestration_verified / completeness_ok`,`token_usage.md` 的行,rollout 的 `rate_limits`。

## Review Focus

- 补丁 04 的六个消费点是否都认 `VALIDATION_REPAIR`(漏一处 = 该路径静默回到不可重试);`repair_hint` 对 L4 子树是否取到票据 `last_error`(重试子树是新 task_id,attempt 从 1 起)。
- 补丁 03 的 `bind_context(headless=True)` 放宽到 codex 后,`has_binding` 对 `agent_id` 非空的负载仍只命中 session 级绑定;顶级 `codex exec` 线程负载确实无 `agent_id`(P1 探针)。
- `headless_codex` 绝不带 `--ephemeral`、`--dangerously-bypass-approvals-and-sandbox`;`-c developer_instructions=` 的 TOML 转义(`json.dumps` 子集)在真实 CLI 下落为 developer 消息(P4)。
- `mailbox wait` 宿主视图去掉的四个字段没有任何宿主侧消费者(README/手册里宿主只用 `agent_tool` + `host_prompt` + `task_id/attempt`;10-02 的 autobind 监视器读的是 `_dispatch/*.request.json` 文件,不受影响)。

---

## Task 0:Codex 真实宿主探针(开工前,花少量额度)

**已做,2026-10-08 14:23–14:30;读数全文 `docs/research/2026-10-08-codex-exec-probes.md`;整组花掉 5h 窗口 2 个百分点(31%→33%)。**

- [x] **P1 hooks 在 `codex exec` 下加载**。两层都验了:① 会话层注入一个只记日志的 PreToolUse(`-c 'hooks.PreToolUse=[…]'` + `--dangerously-bypass-hook-trust`)→ 5 次 shell 调用对应 5 条日志,负载为 `session_id / turn_id / transcript_path / cwd / hook_event_name / model / permission_mode / tool_name / tool_input / tool_use_id`,**`session_id` 就是 `thread.started.thread_id`,没有 `agent_id`、没有 `agent_type`**,`tool_name=Bash`、`cwd`=仓库根;② 项目层用 PostToolUse 守卫自己的测试注入口 `SCAN_CONFIG_LINT_PATH=<故意违规的 jsonc>`,**不带** `--dangerously-bypass-hook-trust` 跑 → 模型原样收到 `Script failed` + 35 条 `[R4]` 违规,即 `.codex/hooks.json` 在 exec 下照常加载(信任来自 `~/.codex/config.toml` 的 `hooks.state.…trusted_hash`)。未改动任何 hook 配置,未写 `context_codex/` 任何文件。
- [x] **P2 线程 id 与 rollout**:`thread.started.thread_id` ↔ `~/.codex/sessions/2026/10/08/rollout-*-<id>.jsonl` 一一对应;额外读数 —— PreToolUse 负载直接带 `transcript_path`,计量可不靠文件名 glob。
- [x] **P3 `resume` 吃覆盖**:`resume -c 'model="gpt-5.6-sol"' -c 'model_reasoning_effort="high"'` → CLI 自报换模型,rollout 第二个 `turn_context` = `model=gpt-5.6-sol effort=high`。**D2 不降级,保持三步。**
- [x] **P4 `developer_instructions` 注入** —— **部分不过,已改设计**:传给 `resume` 被静默丢弃(rollout grep 不到标记);传给**开线程**则生效(模型首句复述标记)且**后续 resume 仍然记得**。执行器据此把 `-c developer_instructions=` 移到 `argv_open`。同时发现换模型 resume 会注入 17,885 字符的 `<model_switch>` 基础提示词 → 开线程改用角色自己那一档。两处都已进补丁 03(见 2.2 引用块)。
- [x] **P5 `web_search=live`**:`-c 'web_search="live"'` + 问当天财经头条 → 事件流出现 `{"type":"web_search","query":…,"results":[…]}`,模型给出标题与链接。
- [ ] **P6 沙箱内 broker 写**:未做,按计划并入 Task 7 的 X1 演练(`--sandbox workspace-write` 下 broker python 写 `context_codex/…`)。
- [x] **P7 profile**:`~/.codex/scanhost.config.toml` 已建(Task 6);终端 `codex -p scanhost` 的实跑验证与桌面 App 能否选 profile 留到真要走回退路径时再看(只影响 D7)。
- [x] 读数记进 `docs/research/2026-10-08-codex-exec-probes.md`(与 `2026-09-26-headless-driver-probes.md` 同形)。

> 本组探针**没有**验证「绑定之后的拒绝/放行实例」(bound 线程跑普通 shell 应被 `AGENT_INPUT_BOUNDARY` 拒、broker 命令应通过)。那要写 `context_codex/` 的冻结请求与绑定,属 X1 演练与边界证明重采的范围。

## Task 1:R5-1 情景收益精度(补丁 01)

**Files:** `autoresearch/contracts/execution.py`(`SCENARIO_PRECISION`、`declared_tolerance`、三处比较)、`autoresearch/common/card_decision.py:263`(契约一句)、`tests/common/test_scenario_estimate.py`(+7)。

- [x] `git apply docs/superpowers/plans/2026-10-08-scan-token-patches/01-tests-scenario-precision.patch` → `uv run --no-sync python -m pytest -q tests/common/test_scenario_estimate.py` 红 5。 **实测主工作树:5 failed / 19 passed。**
- [x] `git apply …/01-impl-scenario-precision.patch` → 同一组绿(副本上 24 passed),`tests/common tests/contracts` 全绿(404)。 **实测主工作树:`tests/common tests/contracts` 745 passed / 1 skipped。**
- [x] 回放 10-07 被拒卡:`context_claude/scan_runs/20261007T151001404046Z/staging/2026-09-30/session_outputs/attempts/l4.688578.a1.review2/a0001/outputs/scan.l4.688578.a1.review2.md` 过 `card_from_decision_text(subject="688578", venue="XSHG", analysis_date="2026-09-30")` → ACCEPTED(副本实测 rr=1.09、ev=0.0015;主仓代码 REJECTED)。 **实测:对被拒卡那段 `scenario_estimate` 原文直接跑 `validate_scenario_estimate` —— 旧码(proto_base)`REJECTED: scenario return contradicts declared entry/exit`,新码 `ACCEPTED, rr=1.09, ev=0.0015`。**(review2 整份文档不是单块决策卡,所以回放走校验器而不是 `card_from_decision_text`。)
- [x] 两引擎 agent 定义里引用契约原文的地方不用改(契约文本由 `decision_instruction()` 生成,派发时冻结)。 **确认:两边 agent 定义未改。**

## Task 2:mailbox 回退路径瘦身(补丁 02 + 05 的 `by_reference`)

**Files:** `autoresearch/session_agent/executors/mailbox.py`(`HOST_VIEW_DROPPED`、`host_view`、`wait_request(full=)`)、`autoresearch/session_agent/mailbox_cli.py`(`--full`)、`tests/session_agent/test_mailbox.py`(+4);`scan_config.jsonc:483` `by_reference: true`。

- [x] 打 `02-tests` 红 4 → `02-impl` 绿(`tests/session_agent/test_mailbox.py` 49 passed)。 **实测主工作树:红 4 / 45 → 绿 49。**
- [x] 宿主视图字段:`task_id / attempt / role / agent_type / model / effort / subject / tool_policy / timeout_seconds / output_paths / request_path / access_manifest_path / agent_tool / host_prompt / prompt_chars`;一份 L4 卡请求从 26.7k 字符降到 <2k(`by_reference` 开)。 **由 `test_mailbox.py` 的 4 个新用例锁住。**
- [x] 10-02 的宿主工具(`context_claude/development/20261002-scan-first-run/host-tools/*`)读 `_dispatch/*.request.json` 文件,不受影响;Codex 侧 `context_codex/_ops/scan_20261007_host_control.py` 那类临时脚本若再用,把 `data['host_prompt'].split(…)` 改成直接取 `host_prompt`。 **修正:`show_request.py` / `autobind_watch.py` 读 `_dispatch/*.request.json`,不受影响;但 `wait_event.sh` / `take_all.sh` 从 `wait` 的输出里取 `len(prompt)` 打字数,宿主视图下会打 0 —— 已把两处改成优先读新字段 `prompt_chars`(工具在 `context_claude/development/`,非产品代码)。**

## Task 3:Codex headless 传输 + 两引擎同一入口(补丁 03)

**Files(新):** `autoresearch/session_agent/executors/headless_codex.py`、`tests/session_agent/test_headless_codex.py`(21 用例,假 `codex` 脚本)。
**Files(改):** `task_access.py`(`bind_context(headless=True)` 放宽到 codex)、`roles.py`(`EXECUTOR_CAPABILITIES["headless"]["engines"] += codex`)、`mailbox_cli.py`(按 run 引擎选执行器、`--codex-bin`、relay 档喂开线程)、`__main__.py`(注释)、`scan/scan_run.py`(引擎 = 工作区引擎、`--codex-bin`、`HEADLESS_ENGINES`)、`scripts/scan_run.sh`(`--engine`)、`trace/usage_harvest.py`(headless 行按记录 `engine` 选适配器、`find_codex_rollout`)、`contracts/scan_config.py` + `session_agent/config.py` + `scan_config.jsonc`(`session.timeouts.codex_open_s`,三件套)、七个测试文件。

- [x] 打 `03-tests`(含新测试文件)→ 红(新模块不存在 = 收集错 + 旧断言失败);打 `03-impl` → 绿(副本 8 个文件 361 passed)。 **实测主工作树:红 = `test_headless_codex.py` 收集错(模块不存在)+ 其余 8 族 11 failed / 184 passed;打 impl 后 8 族 217 passed。**
- [x] 执行器契约(与 `headless_claude` 同形):每次调用一份 `<staging>/_dispatch/headless/<task>.a<n>.json`(`engine: codex`、`thread_id`、`open` 子记录、argv 脱敏、`env_stripped` 只列名、usage、`transcript_path`)+ `.open.stdout/.stderr`、`.stdout/.stderr`、`.last_message.md`;超时杀进程组并 `ExecutorTimeout`(TIMEOUT 重试一次);退出 0 但产物缺 = CONTRACT_ERROR;`turn.failed`/`error` 事件 = 失败;overloaded/5xx = CONNECTION 重试一次;开线程超时 = CONNECTION(不占角色预算)。 **由 `test_headless_codex.py` 22 个用例锁住(含「绑定先于干活」的标记文件证明)。**
- [x] 子进程环境:在 `headless_claude.child_env` 之上再剥 `OPENAI_*`、`CODEX_API_KEY`(订阅登录以外的计费/路由开关一个不传);`CODEX_HOME` 保留。 **锁在用例里:断言被剥变量的值不出现、名字出现在 `env_stripped`。**
- [x] `scan_run.sh --engine codex --date <日> --skip-readiness` 在 zsh -f 假 uv 下实测:`--engine` 被吃掉、其余参数原样、`AUTORESEARCH_ENGINE=codex`;非法引擎退出 2。 **由 `tests/scan/test_scan_run_script.py` 在 `zsh -f` + 假 uv 下真跑(绿)。**
- [x] 计量:`usage_harvest.collect_headless` 对 `engine: codex` 的记录按线程 id 反查 rollout,用 Codex 适配器计量(fixture rollout 实测 `status != UNMEASURED`、`cost_source = estimate`);找不到 rollout = UNMEASURED 不计 $0。 **由 `tests/trace/test_usage_harvest_headless.py` 两个用例锁住(有 rollout = measured;无 rollout = UNMEASURED)。**
- [x] `config_standard` 0 条违规(`--fix-headers` 重生成了 session 块头注;R8 把 `OPEN_TIMEOUT_S` 登记为 `session.timeouts.codex_open_s` 的内建缺省)。 **实测主工作树:0 条违规。**
- [x] Task 0 的 P1–P5 任一失败先改设计再打本补丁(见 D2)。 **P1/P2/P3/P5 过,P4 部分不过 → 已按读数改设计后才打补丁(见 2.2 引用块与 Task 0)。**

## Task 4:校验拒绝只重做一个任务(补丁 04)

**Files:** `contracts/retry.py`(`VALIDATION_REPAIR`)、`session_agent/evidence.py`(`read_failure`)、`session_agent/service.py`(`fail` 可重试、`retry_l4` 资格)、`session_agent/runner.py`(`submit_error_class`、两处重试推导)、`scan/l4_tasks.py`(`RETRYABLE_ERRORS`,FAILED 分支与 `mark_failure`、批次列举共 6 处)、`session_agent/dispatch.py`(`repair_hint`,接在 `render_prompt` 之后、C4 边界段之前,所以也进 `by_reference` 冻结文件);测试:`test_runner.py`(+4)、`test_scan_runner_full.py`(+1,改 1)、`test_scan_runner_gaps.py`(改 1)。

- [x] 打 `04-tests` → 红;打 `04-impl` → 绿(副本 runner/scan_runner_full/scan_runner_gaps/l4_tasks 四族 162 passed)。 **实测主工作树:红 9 / 54 passed;打 impl 后 runner + scan_runner_full + scan_runner_gaps + l4_tasks 四族 113 passed。**
- [x] 语义锁(已在测试里):SESSION 任务被拒 → a2 带「修订要求」且含校验原话 → 通过则 FINISHED;两次都拒 → 条目 FAILED(可重试类但预算用尽)、run BLOCKED;L4 票据被拒 → 票据 attempt 2 的新子树、a2 卡 prompt 带原话 → 通过则票据 SUCCEEDED;两次都拒 → 票据 BLOCKED。 **全部绿。**
- [x] 两处既有用例按新语义改:缺节的市场研判 = DOMAIN_VALIDATION 且 attempt 2;坏的 📌 卡 = a1 WAITING_RETRY、a2 FAILED、票据 BLOCKED、仍挡报告(停电要持续到 a2 子树,否则一次重试就救回来了——那是设计行为)。 **已改,绿。**
- [x] 复核任务同样走这条:review2 被拒 → 其 SESSION attempt 2 带原话重做,`REVIEW_UNAVAILABLE` 只在第二次仍拒后出现。 **由新增用例锁住。**

## Task 5:配置与文档(补丁 05)

- [x] `scan_config.jsonc`:`session.mailbox.by_reference = true`;`session.timeouts.codex_open_s = 180`(随补丁 03);lint 0。 **实测:`by_reference: true`(484 行)、`codex_open_s: 180`(475 行),lint 0 条违规。**
- [x] 文档改五处,口径统一为「日常全扫 = headless,mailbox = 回退」:`docs/session-agent/README.md`(新节「日常全扫:宿主不进研究回路」;headless 节加 codex 三步与待探针;宿主循环节加 `wait` 视图与 `by_reference`;恢复节加领域校验重做)、`docs/ops/scan-ops.md`(`--engine`、codex 记录形状)、`.claude/skills/scan-market/SKILL.md`(入口)、`AGENTS.md`(Codex 侧入口)、`CLAUDE.md`(一句)。 **已改。**
- [x] `docs/research/2026-10-07-scan-session-v1-r5-readout.md` 缺陷表 R5-1/R5-2/R5-3 三行补「修法:本稿 Task 1 / Task 4 / Task 4」。 **已补:R5-1「已修,取 ①」、R5-2「未改,原因」、R5-3「已换修法 = 补丁 04」。**

## Task 6:主会话与回退路径的运维(仓外)

- [x] Codex profile:`~/.codex/scanhost.config.toml` 写

  ```toml
  model = "gpt-5.6-sol"
  model_reasoning_effort = "medium"
  ```

  mailbox 回退时用 `codex -p scanhost` 起会话(App 内按 P7 结论)。headless 场不需要。
  **已建(2026-10-08),文件顶部写明只给回退路径用、为什么压档、以及它只叠在基础配置之上。**
- [x] Claude:headless 场 `/model` 无所谓;mailbox 回退时 `/model opus` + effort low。README 已写。 **README 已写;D8 仍待你裁。**
- [x] `~/.codex/config.toml` 不改(它含 MCP 凭证,改动走用户自己的手)。 **未改。**

## Task 7:真跑阶梯(花额度;每级失败就停、修完起新 run)

- [~] **C1 Claude 强制全扫缩卡演练 —— 用户 2026-10-08 裁定跳过**,今晚直接上 C2 生产全扫(它吃的是交互会话同一个 Claude 5h 窗口,演练一场就少一场生产场)。原方案:隔离工作树 + `max_cards=5` + 09-30 数据 + `--skip-readiness`,留作 C2 失败后的回退演练。
- [ ] **C2 Claude 生产全扫**(当晚数据,**用户裁定的第一级**;湖灌齐 ≈ 21:10 之后起)。起法 `scripts/scan_run.sh --engine claude --date <当日> --max-parallel 4`(不加 `--skip-readiness`,让它自己等就绪)。没有演练兜底,所以起之前先确认:主会话这一轮没在占 5h 窗口、`claude --version` 与 agent 定义的钉版模型一致。验收:一场加权输入 ≤ 09-11 水平的 1.3 倍(09-11 为 12.0M)且主会话行 ≤ 0.1M;failures 若有 DOMAIN_VALIDATION,看是否只多了一张卡的钱。
- [ ] **X1 Codex 探针通过后的缩卡演练**:`scripts/scan_run.sh --engine codex --date 2026-09-30 --skip-readiness --max-parallel 4`;验收:每个推理任务一份 `engine: codex` 记录、rollout 全部绑定、`token_usage.md` 的 codex 行全部 measured;用 scratchpad 的 `codex_rollouts.py` 读 `rate_limits`:一场 5h 窗口消耗 ≤ 35%。
- [ ] **X2 Codex 生产全扫**。验收同 X1,外加 `verify-report` 四项。
- [ ] 两边边界证明按 E1 时机重采(补丁 03 改了 policy 文件)。
- [ ] 任一级 FAILED:按 `_ops/scan_run_<日>.log` 的阶段查;修码另开会话;补跑用新 run_id。

## Task 8:B1 等价检验(D6 裁定后)

- [ ] 按 `docs/superpowers/plans/2026-10-03-review-implementation.md`「B1 怎么跑」:两档都走 headless(现在两引擎都能),候选档 `agent_engines.claude.role_overrides.l4_card.effort = "high"`,k=2,20–40 只;`research.noise_floor` 给 `EQUIVALENT` 才改 `l4-card.md` 的 `effort`;`INSUFFICIENT` 加票不放宽。
- [ ] Codex 研究角色不做 B1(一场 6.4M 里研究子 agent不是大头);Codex 侧只看 X2 的窗口占比。

## Task 9:收口

- [ ] `docs/research/2026-10-08-scan-token-cost-readout.md`:C2/X2 两场的 `token_usage.md` 对比 10-07,主会话行、每卡成本、窗口占比三张表。
- [ ] scratchpad 三份取数脚本收进 `autoresearch/research/usage_windows.py`(只读 rollout/transcript,不进生产链路)。
- [ ] 记忆更新;本稿勾选框只在真跑读数到手后勾。

---

## 6. 验证到什么程度

### 交稿时(2026-10-08 12:25,源码副本)

- 五组九个补丁在**全新源码副本**上按「tests → impl」顺序重放:每组先红后绿(重放日志见本稿末「附录 B」);对**主工作树**用临时索引 `git apply --cached --check` 累计检查 9/9 通过,工作树一字未动。
- 全量测试(副本,两引擎):见附录 A 的失败集合对比——判据是打补丁后的失败集合 ⊆ 基线失败集合。
- `scan_config` lint:0 条违规(含新键与重生成的块头注)。
- `scripts/scan_run.sh --engine codex` 在 zsh -f + 假 uv 下真跑过参数转发。

### 开发时(2026-10-08 14:20–15:30,主工作树)

- **Task 0 探针是真的花了额度跑的**(5h 窗口 31%→33%):`codex exec` 在本仓下真实执行、项目层 hook 真实拦截、`resume -c` 真实覆盖、`web_search="live"` 真实检索。两处读数推翻了原设计并已改码(2.2 引用块)。读数全文 `docs/research/2026-10-08-codex-exec-probes.md`。
- 补丁按 `01-tests → 01-impl → … → 05` 顺序打进主工作树,**每组先红后绿**(逐组实测数字记在各 Task 的勾选项里);打之前用临时索引做过一次累计 `git apply --cached --check` 9/9(真实索引未动)。
- 真实产物回放:10-07 阻断全场的那段 `scenario_estimate` 原文(`entry 111.58/111.58`、三档 `0.0240/0.0020/-0.0220`、`ev 0.0015`、`rr 1.09`),旧码 `REJECTED: scenario return contradicts declared entry/exit`,新码 `ACCEPTED`。
- `config_standard` 在主工作树:0 条违规。
- 全量测试(主工作树,两引擎):见附录 C。
- **仍然没做的**:任何真实 `claude -p` 调用;真实一场全扫(Task 7 的 C1/C2/X1/X2);绑定之后的边界拒绝/放行实例(P6)与两边边界证明重采;B1(Task 8)。

## 7. 风险与回滚

| 风险 | 处置 |
|---|---|
| ~~`codex exec` 下项目 hooks 不加载(P1 失败)~~ | **已排除**:P1 实测两层 hook 都加载并生效 |
| ~~`resume` 不吃 `-c model` 覆盖(P3 失败)~~ | **已排除**:P3 实测 `turn_context` 换成了覆盖值 |
| hook 装载是真的,但**绑定之后的拒绝/放行**还没见过实例 | 这是 headless codex 唯一没被观测的一环(P6)。X1 演练必须看到:bound 线程的普通 shell 被 `AGENT_INPUT_BOUNDARY` 拒、broker 命令通过;看不到就当边界失效处理,停场 |
| Codex 换版本后 `developer_instructions` 行为再变(它是 `ConfigToml` 的键,但 resume 丢弃这件事没有文档) | 执行器把两轮的 argv 原样记进 `_dispatch/headless/<task>.a<n>.json`;X1/X2 时抽一份 rollout 核「第一条 developer 消息以角色指令开头、全程无 `<model_switch>`」 |
| 校验重做把一类系统性契约错变成「每张卡多花一次」 | 封顶 2 次;`token_usage.md` 的 `attempt` 列能看出来;若某契约错在 >2 张卡上重复出现,当天就是契约口径问题,停场修契约 |
| headless 并发 8 个 `claude -p` 撞 5h 限 | `--max-parallel 4` 起步;429 走 RATE_LIMIT 重试一次;撞限整场 FAILED 由 `scan_run` 收口推送 |
| 回滚 | 补丁按逆序 `git apply -R`;`by_reference` 一行改回 false;文档五处随补丁 05 逆向 |

## 8. 补丁清单与怎么打

目录 `docs/superpowers/plans/2026-10-08-scan-token-patches/`,按序:

| 序 | 文件 | 内容 |
|---|---|---|
| 01 | `01-tests-scenario-precision.patch` / `01-impl-scenario-precision.patch` | R5-1 容差 + 契约句 |
| 02 | `02-tests-mailbox-host-view.patch` / `02-impl-mailbox-host-view.patch` | `wait` 宿主视图、`--full` |
| 03 | `03-tests-codex-headless.patch` / `03-impl-codex-headless.patch` | Codex headless 执行器、引擎接线、计量、`codex_open_s` 三件套、`scan_run.sh --engine` |
| 04 | `04-tests-validation-repair.patch` / `04-impl-validation-repair.patch` | `VALIDATION_REPAIR`、六处消费点、`repair_hint`、`read_failure` |
| 05 | `05-config-docs-config-and-docs.patch` | `by_reference=true`、五份文档 |

同一文件被两组改的(`mailbox_cli.py` 02→03,`scan_config.jsonc` 03→05)后一组以前一组为基线,**必须按序打**。开工:`for p in 01-tests 01-impl 02-tests 02-impl 03-tests 03-impl 04-tests 04-impl 05-config-docs; do git apply --check <目录>/$p-*.patch && git apply <目录>/$p-*.patch; done`;工作树若已被另一侧改动,用 `--3way`。打完跑 `config_standard` 与两引擎全量。

> **2026-10-08 15:00**:03 与 05 在 Task 0 探针之后按读数**重新生成过**(`developer_instructions` 移到开线程、开线程改用角色档、删掉 relay 档接线与 `open_model/open_effort` 构造参数、README 对应段改写),补丁文件已就地替换;**九个补丁已全部打进主工作树**。
> 它们现在的用途是改动记录与回滚手段:逆序 `git apply -R`。
> 回滚时注意 Task 6 的 `~/.codex/scanhost.config.toml` 在仓外,补丁不含它。

> **附录 A 与 B 记的是交稿那一版补丁(12:00–12:25)**;探针之后 03/05 重新生成,主工作树上的实测见各 Task 的勾选项与附录 C。两版的差别只在 Codex 执行器的 `-c` 分工,不涉及 01/02/04。

## 附录 A:全量测试失败集合对比(副本,两引擎,2026-10-08 12:00–12:25 实测)

未打补丁的全新副本(`proto_base`)与打满九个补丁的副本(`proto`)各跑一遍 `pytest -q`,两个引擎:

| 引擎 | 未打补丁 | 打满补丁 | 失败集合差异 |
|---|---|---|---|
| codex | 1 failed · 9321 passed · 7 errors | 1 failed · **9364** passed · 7 errors | **空**(两边同一条 `tests/trace/test_identity.py::test_real_repository_safe_prompt_corpus_snapshots_completely` + 同 7 个 forensics 收集错 —— 副本不是 git 工作树所致) |
| claude | 195 failed · 9127 passed · 7 errors | 195 failed · **9170** passed · 7 errors | **空**(195 条逐条相同:dossier/forensics/news 的 claude 引擎环境红,主仓同红) |

新增 43 个用例两边全过;`comm` 对比「只在打补丁后红」与「只在基线红」两个方向都为空。

## 附录 B:重放日志(全新副本,codex 引擎,2026-10-08 12:10 实测)

每组:打 tests 补丁 → 跑该组测试(红)→ 打 impl 补丁 → 再跑(绿);03 组的「1 error」是新测试文件 import 不存在的模块,收集即错。

```text
applied 01-tests-scenario-precision.patch
  red  : 5 failed, 19 passed in 0.96s
applied 01-impl-scenario-precision.patch
  green: 24 passed in 0.32s
applied 02-tests-mailbox-host-view.patch
  red  : 4 failed, 45 passed in 6.63s
applied 02-impl-mailbox-host-view.patch
  green: 49 passed in 6.25s
applied 03-tests-codex-headless.patch
  red  : 1 error in 0.33s
applied 03-impl-codex-headless.patch
  green: 216 passed in 23.64s
applied 04-tests-validation-repair.patch
  red  : 9 failed, 54 passed in 119.74s (0:01:59)
applied 04-impl-validation-repair.patch
  green: 63 passed in 141.09s (0:02:21)
applied 05-config-docs-config-and-docs.patch
  lint : scan_config 标准 lint:0 条违规
  cfg  : 157 passed in 8.28s
REPLAY_DONE
```

## 附录 C:全量测试(**主工作树**,两引擎,2026-10-08 15:00–16:00 实测)

九个补丁全部打进主工作树之后各跑一遍 `pytest -q`:

| 引擎 | 读数 | 耗时 |
|---|---|---|
| codex | **9373 passed · 12 skipped · 0 failed · 0 errors** | 18m40s |
| claude | 196 failed · 9183 passed · 6 skipped · **0 errors** | 17m23s |

与附录 A 的副本基线对比(`comm` 逐条):

- codex:副本基线那 1 条红(`tests/trace/test_identity.py::test_real_repository_safe_prompt_corpus_snapshots_completely`)与 7 个收集错**在真树上都不存在** —— 它们是「副本不是 git 工作树」的产物。真树**零红**。
- claude:失败集合与副本基线的差只有三条。少了上面那条 `test_identity`(同样是副本产物);多了两条
  `tests/forensics/test_stock_macro_replay.py::test_real_domain_replay_matches[stock-research-FULL]` 与 `[macro-research-FULL]`
  —— 这个文件在副本里**根本没被收集到**(基线里零次出现),所以「195 → 196」是口径差不是回归。
- 直接证伪「是补丁引起的」:把九个补丁**逆序全部撤掉**,只跑这两条,claude 引擎下**照样两红**(`compute_status` `PARTIAL` ≠ `FULL`,replay 的是 engine=codex 的 capsule,claude 根下真实产物不全);codex 引擎下这两条本来就绿。属 09-26 记录过的「claude 引擎下 session_agent / forensics 恒红」那一族环境红。
- 这次来回也顺带验了**回滚路径**:`git apply -R` 九个 → 跑测试 → 正序 `git apply` 九个,38 个被改文件的 sha256 **38/38 与撤销前完全一致**,`git status` 行数不变。

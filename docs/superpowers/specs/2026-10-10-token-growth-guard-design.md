# 新增开发不让 token 膨胀:防膨胀守卫设计(brainstorm,待裁)

基准时间:2026-10-10 20:00(Asia/Shanghai)。这是「token 优化分两步」的第二步;第一步(现状零风险瘦身)已落,读数在
`docs/research/2026-10-10-headless-context-trim-readout.md`。本稿取代
`2026-10-10-token-roi-and-dev-redline-brainstorm.md` 第 4 节的红线草案:条文不变多少,变的是每条背后**有没有一个会红的机器件**。
降档、情报回灌、结构改动(满卡只给 E6 top-k 等)不在本稿,它们各走等价检验与裁定。

---

## 0. 一页结论

1. **膨胀不是某次改坏了,是结构性的:加功能只要「看起来有用」,减功能却要「证明无害」。** 举证不对称,成本只会单调涨。
   本仓一年的曲线就是这样:情报员(Codex 一场 42%)、双复核、九个行业 brief、档位全开 critical、别名漂移到 5.5·max(单卡输出 18k→88k)、
   插件 hook 每个子 agent 多打 8 次……没有一项是在知道它会花多少的情况下加的。
2. **守卫要堵的是四种膨胀机制**,不是「大家小心点」:① 加法无减法;② 隐藏乘数(每次调用的固定前导 × 调用次数 × 线程数);
   ③ 上游静默漂移(模型别名、CLI 版本注入新段落、插件 hook、自动记忆);④ 失败放大(一场失败 = 研究白花 + 一个诊断会话 + 重跑)。
3. **三件机器件,各堵一类**:**改动时的静态成本清单(BOM)差分**堵 ①②(改一处就把乘出来的总数算给你看,超预算线 exit 2);
   **场后 `redline.json` 真计量 + 开场断路器**堵 ③(漂移最多烧一场,下一场无人值守不再开);**推理前零推理回放门**堵 ④。
   外加两个小件:**每角色运行时信封**(轮数封顶取自实测 p95,不是 60/80 这种等于没封)和**开发会话周账本**(开发和生产花的是同一个钱包)。
4. **预算线先「不涨」再「下压」**:第一版预算线 = 现状实测 × 1.1(Claude 研究 $37 → 线 $41;Codex 一场 84 点 → 线 92 点),
   每落地一项经真跑验证的节省,线下调到实测 × 1.15(棘轮,只降不升;升线必须改 `scan_config.jsonc` 里那一行,diff 可见)。
5. **红线八条**(第 4 节)每条三栏齐:事故 / 机器件 / 会红的量。没有机器件的条文不进红线,进「开发守则」(第 5 节)。

---

## 1. 膨胀是怎么发生的(本仓实证)

| 机制 | 本仓实例 | 为什么现有办法拦不住 |
|---|---|---|
| ① 加法无减法 | 情报员(09 月加,Codex 一场 42%);ens_review ×2;sector brief ×9;company/us/sector/global 四个 intel;tripwire | 加时只问「有没有用」,没人算「花多少、谁消费」;删要证明无害,没人证 |
| ② 隐藏乘数 | 前导 23k × 233 次调用(Codex);MEMORY.md 11k × 每次调用(Claude);`web.run` 4 查询 × long × 9 轮 → 85k 上下文;线程数 = 卡数 + 📌 + 复核 + 行业数 | 改动只碰一个文件,乘积在运行时才出现;单测不量 token |
| ③ 上游静默漂移 | 10-03 别名 opus→5.5 同 effort 输出 1.9万→5.1万;CC 版本给 `-p` 加自动记忆;Codex 0.16x 给每线程注入 3.2k 字符「team of agents」前导;OMC SubagentStop hook 8 次/子 agent | 不是仓库改动,任何改动时的门都看不见;只有场后计量能看见 |
| ④ 失败放大 | 10-02 起 9 场 0 发布;3 场在研究花完后死于零推理代码;每场失败后跟一个 1–3M token 的诊断会话 | 组装 / 校验 / 发布在推理之后才跑,缺陷要花完钱才暴露 |
| ⑤ 开发侧放大 | 10-09 Codex 周额度 93%;三个并行 worker 17M 输入;41k 字符的计划稿每个子 agent 都读;前会话三份量法脚本丢失今天重写 | 开发和生产同一个订阅窗口,但开发从不计量 |

---

## 2. 设计原则

- **P1 预算是 config 里的一行契约,不是告警。** 每引擎每场一条线,单位用引擎真正受限的那个:Claude 用 API 等价美元(`usage_harvest` 已算)+ 输出 token;Codex 用 5h 窗口点数(rollout `rate_limits` 实测)。改线 = 改 `scan_config.jsonc`,diff 可见,过九条标准。
- **P2 两道门一个断路器。** 改动时门(静态 BOM 差分,走现有 PostToolUse lint 的壳)管仓库改动;场后计量(`redline.json`)管一切;断路器(readiness 拒开)管漂移。三者分工,不互相替代。
- **P3 举证对称。** 新增任何 LLM 角色 / 工具回合,要给出和删除同等的证据:决策消费者(谁读它、改变什么)+ 预算行 + 判断类角色的等价/消融读数。没有消费者的不派。
- **P4 封顶取自实测分布,不取整数。** `max_turns` 60/80 不是封顶是没封;封顶 = 近 K 场 p95 × 1.5,由指纹复核。
- **P5 估算只做差分,真相只认计量。** BOM 用来回答「这次改动让总数变了多少」,绝对值以 `redline.json` 为准;估算不进台账。
- **P6 开发会话同一钱包。** 周账本把两引擎、生产与开发、按项目分开列;守则先靠账本曝光,hook 只在账本证明有必要时再加。
- **P7 两引擎同落。** 每个机器件都要有 claude / codex 两个消费点(09-15 裁定)。

---

## 3. 机器件(每件:住哪、何时触发、断言什么、怎么证明它会红)

### M1 静态成本清单 BOM(改动时的乘数计算器)

- **住哪**:`autoresearch/scan/token_bom.py`(零 LLM)。输入 = `scan_config.jsonc`(卡数、行业数、复核数、情报开关、`max_queries`、`max_turns`、档位表)+ agent 文件字节数 + 每引擎前导常数(第一步实测:Codex 开线程 14,062 字符;Claude l4-card 前导 30,604 token 减记忆 10.9k)+ 每角色经验系数(近 K 场去重账本的每角色调用次数中位、输出 token 中位、每次调用上下文中位;首版用 10-07 r5 / 10-08 #1 两场)。
- **算什么**:每角色 线程数 × 调用数 × 上下文 → 加权输入;线程数 × 输出中位 → 输出;Claude 折美元(`usage_harvest` 同一价目);Codex 折窗口点(单系数标定:上一场 实测点数 / 加权输入,标「估」)。
- **触发**:PostToolUse lint(受管路径扩到 `.claude/agents/`、`.codex/agents/`、`scan_config.jsonc` 成本键);输出一行 `BOM Δ:+12%(l4_intel.max_queries 6→20)`。
- **断言**:BOM ≤ `budgets.declared.<engine>`;超线且同一改动没抬线 → exit 2。抬线 = 改那一行 → diff 可见、九条标准照跑。
- **会红的证明**:`tests/redline/test_token_bom.py`:把 `l4.max_cards` 5→10 必红;把 `l4-card.md` 加 5k 字符必红;把 `session.context.claude_auto_memory` 改 true 必红。
- **不做什么**:不预测质量,不代替等价检验;系数来自实测,没有实测的角色标 UNCALIBRATED 并按最贵同档角色计。

### M2 成本键与 agent 文件预算(改动时门的受管面)

- 注册表 `Key(..., cost=True)`:`l4.max_cards`、`sector.*max_sectors`、`l4_intel.max_queries`、`session.max_turns/*`、`agents`、`agent_engines`、`session.context`、`budgets.*`。
- `budgets.agent_chars`:角色 → agent 文件字符上限(首版 = 现值 × 1.1;l4-card.md 20,021 → 22,000)。Codex 的 `.codex/agents/*.toml` 同表。
- hook:`scripts/hooks/scan_config_standard.sh` 的 case 列表加两个 agents 目录;lint 新规则 R10「agent 文件超预算」、R11「成本键改了但 BOM 超线」。
- 会红:`tests/scan/test_config_standard.py` 加 agent 文件超限用例。

### M3 场后真计量 `redline.json`(观测面,进 GATE 的是「有没有」不是「超没超」)

- **住哪**:`autoresearch/scan/redline.py`;输出 `capsule/verification/redline.json`;`run_profile` 把它列为必备证据(完整性检查会查);`$RPT/_ops/scan_run_<日>.json` 多一个 `redline` 字段。
- **读什么**:10-09 去重后的用量账本(每角色输入 / cache / 输出 / 推理输出 / 调用数);派发记录(每线程首次调用上下文 = 前导;Claude 从 transcript 第一条 `usage.cache_creation+cache_read`,Codex 从 rollout 第一条 `token_count`);webtrace hook 的网查条数;Codex rollout `rate_limits.primary.used_percent` 场前场后差;实际 model / effort / CLI 版本。
- **比什么**:`budgets.declared.<engine>`(场线)、`budgets.roles.<role>.{calls_p95, output_p95, prefix_max}`(角色信封)、上一场指纹(漂移)。
- **结论**:PASS / WARN(单项超软线) / FAIL(场线超硬线或前导 ≥2×);FAIL 写 `$RPT/_ops/redline_breaker.json`。**不挡发布**(研究已付,发布不花钱),挡的是下一场(M4)。
- 会红:`tests/redline/test_redline.py` 用假账本:主会话行 0.2M 必 FAIL;前导 2× 必 FAIL;某角色调用数超 p95×1.5 必 WARN。

### M4 断路器(漂移最多烧一场)

- **开场**:`scan_run` readiness 新步骤 `redline_breaker`:存在 `redline_breaker.json` 且未确认 → 拒开(摘要 `result=REFUSED_BUDGET`),无人值守场尤其如此。确认 = 预算行改过(config 哈希变了)或显式 `scan_run --ack-redline <run_id>`。
- **场中**:runner 收到第一个完成线程的派发记录就查前导:≥ `prefix_max` × 2 → 停派新线程(复用 10-09 容量暂停语义,原因 `PREFIX_DRIFT`),一场漂移只烧一线程。
- 会红:`tests/scan/test_scan_run.py` 断路器拒开用例;`tests/session_agent/test_runner.py` 第一线程前导超限停派用例。

### M5 每角色运行时信封(轮数封顶取实测)

| 角色 | 实测(10-07 r5 Claude 消息数 / 10-08 Codex 调用数) | 建议封顶(p95 × 1.5 取整) |
|---|---|---|
| l4-card | 5 / 6 / 8(min/med/max);Codex 7–13 | Claude `max_turns` 16(现 80) |
| l4-intel | 8 / 8 / 9;Codex 9–11 | 14 |
| l3-rank | 5;Codex 6 | 10 |
| sector-brief | 5 / 6 / 7;Codex 4–5 | 10 |
| ens_review | Codex 6–8 | 14 |
| macro-brief | 3 | 8 |

- 写进 `session.max_turns`(键已存在,现在是空表 → 缺省 60/80)。Codex 侧 `codex exec` 没见轮数开关(config 参考里没有),靠已有墙钟 `HEADLESS_TIMEOUTS` + M3 场后信封。
- 超封顶 = 该 attempt 以 BUDGET 类失败结束,走现有重试规则,不整场作废。
- 这是唯一可能碰到质量的小件:封顶取 p95 × 1.5,正常卡碰不到;碰到的是 09-15 那种翻源码自验的失控回路。

### M6 角色登记:消费者 + 影响计数(R4 机器化)

- `agents` 块每角色加 `consumer`(决策点函数路径,如 `l4_intel → autoresearch.scan.relative_buy._faces_table` 与 `l4 card P3/P4`);lint 查函数存在、源码含角色产物 id(同 config 消费者规则)。新角色缺 `consumer` → exit 2。
- M3 附带每角色「产物被消费者读到非空的次数 / 改变决策的次数」(情报员 ↔ 卡 P3/P4 引用条数;行业 brief ↔ L3 名单差异);连续 N 场为 0 的角色列入 `redline.json.idle_roles`,进待裁清单——**不自动删**,删是裁定。

### M7 版本钉与前导指纹

- `redline.json` 记 claude / codex CLI 版本、实际 model / effort(10-03 已钉全 ID)、每引擎前导 token;与上一场比:版本变 + 前导涨 > 20% → WARN;≥ 2× → FAIL(M4 接手)。
- 这是唯一能看见「Codex 0.16x 给每线程塞了 3.2k 字符 team-of-agents 前导」「CC 给 -p 加了记忆」这类事的地方。

### M8 开发会话周账本 + 守则

- `autoresearch/research/token_ledger.py`(零 LLM):按周、按引擎、按 cwd 项目、按「生产 run / 开发会话」分列(Claude 读 `~/.claude/projects/*/*.jsonl` 的 `message.usage`;Codex 读 rollouts 的 `total_token_usage` 与 `rate_limits`),输出 `$RPT/_ops/token_ledger_<周>.md`。生产 run 以 run_id 时间窗归类,其余算开发。
- 守则进 CLAUDE.md / AGENTS.md 各五行(第 5 节);hook 只在账本证明某条守则被反复违反后再加。

### M9 推理前零推理回放门(R1)

- readiness 新步骤 `replay_postinference`:取最近一场 capsule(FAILED 也行),用**当前代码**回放 `scan.report.build.bundle` / `scan.report.used.bundle` / E6 / `verify-report`(`replay_registry.build_replay_plan` + `trace.replay.execute_replay`,scope 限到推理之后的单元);`compute_status != OK` → 不派推理,摘要 `REFUSED_REPLAY`。
- 它只能抓「推理之后的确定性代码在旧产物上坏了」(10-07 r5 精度契约、10-08 #1 路径 resolve 都属此类),抓不到新输入形状;够了——三场里三场都是这类。

---

## 4. 开发红线(定稿草案,八条,每条三栏齐)

| # | 红线 | 事故 | 机器件 | 会红的量 |
|---|---|---|---|---|
| R1 | 推理之前先零推理回放 | 10-03 r4、10-07 r5、10-08 #1 | M9 | readiness `replay_postinference` ≠ OK → 不派 |
| R2 | 宿主零研究 | 10-07 主会话 62% / 84–90% | M3 | 主会话行 > `budgets.roles.host.weighted_max`(0.1M)→ FAIL |
| R3 | 档位变更必附等价读数 | 10-03 别名漂移 | M2 成本键 + 现有 noise_floor 协议 | 改 `agent_engines/agents` 档位且无 `equivalence_ref` → lint exit 2 |
| R4 | 每笔推理花费必须有决策消费者 | FN-1 家族、intel 死票门 0 命中、force_full_card 零调用 | M6 | 角色缺 `consumer` → exit 2;连续 N 场 0 影响 → 待裁清单 |
| R5 | 预算是契约不是告警 | 10-08 #2 撞额度;`budgets` 块无截断权 | M1 + M3 + M4 | BOM > 线 → exit 2;场后 FAIL → 下一场拒开 |
| R6 | 失败隔离到最小单元,不得回退整场重跑 | 10-02~10-07 | 已落(补丁 04、recover-task、resume)+ M3 计数 | `redline.json.whole_run_retries` 必须 = 0 |
| R7 | 上下文只传指针与增量;前导与 agent 文件有预算 | 09-15 自验翻源码;AGENTS.md / MEMORY.md 注入 | M2 + M3 + M5 | agent 文件超 `agent_chars` → exit 2;前导 > `prefix_max` → WARN/FAIL;轮数超封顶 → attempt 失败 |
| R8 | 计量先于优化;估算不入台账;开发会话也算钱 | token_usage 62 行重复、诊断量错对象、脚本丢失重写、周额度 93% | M8 + 复审规则 | 「降本」改动无前后计量 → 复审拒;周账本缺 → 周例行红 |

落点:`docs/dev-redline.md`(唯一真身,就是这张表 + 机器件索引)+ `CLAUDE.md` / `AGENTS.md` 各一行指针 + `tests/redline/`(每条至少一个「删掉就红」的用例)。

---

## 5. 开发守则(进 CLAUDE.md / AGENTS.md,各五行;没有机器件,靠 M8 账本曝光)

1. 新增 LLM 角色 / 工具回合 / prompt 段落:先写 BOM 行(线程 × 调用 × 上下文 → 美元/窗口点)与消费者,再写代码。
2. 量法脚本首次使用即进 `autoresearch/research/`,不留 scratchpad;量过的读数进 `docs/research/`,下次先查再量。
3. 读大文件用范围(grep / sed -n / Read offset),日志看尾;全量测试只看摘要。
4. 子 agent 扇出只给可分、各自 ≥ N 文件的任务,派发前写一句「为什么不在本会话做」;复审派一个,不派一排。
5. 计划稿给实施者看的部分控制在一屏内(任务清单 + 验收),证据与推演放附录,子 agent 只读前者。

---

## 6. 落地顺序(按「省下的浪费 / 工作量」排)

| 序 | 件 | 量级 | 前置 | 验收 |
|---|---|---|---|---|
| 1 | M5 轮数封顶(`session.max_turns` 六行 + 测试) | 小 | 无 | 下一场 `redline.json` 无角色碰顶 |
| 2 | M3 `redline.json` + M7 指纹(先观测不拦) | 中 | 10-09 去重账本 | 对 10-07 r5 与 10-08 #1 两个历史 capsule 回算出表 |
| 3 | M2 成本键 + agent 文件预算 + hook 扩面 | 小中 | 无 | 三个必红用例 |
| 4 | M1 BOM + 预算线首版(现状 × 1.1) | 中 | 2、3 | 把 max_cards 5→10 必红;BOM 对两场历史误差 ≤ 25% |
| 5 | M4 断路器(开场拒 + 首线程停派) | 小 | 2 | 两个必红用例 |
| 6 | M9 回放门 | 中 | 无 | 对 10-08 #1 capsule 用修复前代码回放必红、修复后必绿 |
| 7 | M6 消费者字段 + 影响计数 | 中 | 2 | 新角色缺 consumer 必红;intel 影响计数能算出 |
| 8 | M8 周账本 + 守则五行 | 小 | 无 | 本周账本能分出生产 / 开发 |

全部零推理;只有验收「下一场」需要一场真跑。两引擎同落(每件两个消费点)。

---

## 7. 待裁

- **D16 预算线首版**:现状 × 1.1 起步、棘轮只降不升(建议),还是直接写目标线(Codex 35 点 / Claude $15,等于现在天天红)?
- **D17 改动时门的强度**:BOM 超线 exit 2 硬拦(建议,与九条标准同壳),还是只打印 Δ 不拦?
- **D18 断路器范围**:只拦无人值守场(launchd)还是手动场也拦(建议:都拦,`--ack-redline` 一行可解)?
- **D19 轮数封顶数值**:按上表 p95 × 1.5,还是更松(×2)?
- **D20 开发守则是否上 hook**:先账本(建议),还是现在就给「整文件读 > N 行」「并行 worker」加 PreToolUse 拦截?
- **D21 M6 闲置角色**:连续几场 0 影响进待裁(建议 5)?
- **D22 落地与 10-09 批、第一步一起提交,还是红线件单独一个分支?**

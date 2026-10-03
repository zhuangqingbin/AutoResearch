# 2026-10-03 复盘建议实施台账（钉版 · 评判尺 · 契约与实验）

> 来源：[`docs/research/2026-10-03-research-drift-token-review-brainstorm.md`](../../research/2026-10-03-research-drift-token-review-brainstorm.md) §7（A1–A11 / B1–B9），§8 Q1–Q8 全按建议裁定（用户 10-03「按照你的建议」→「开始开发 直到完成」）。
> 工作方式：主工作树直接实施、**不提交**（与仓内其它未提交波次同口径）；每项先写会红的测试再实现；两个引擎都跑；`scan_config` 标准 lint 保持 0 违规。
> 早期补丁（01–05，scratch 源码副本里红→绿做出来的）在 [`2026-10-03-review-patches/`](2026-10-03-review-patches/STATUS.md)，已全部应用到主树。

## 裁定（照建议）

| # | 裁定 | 落在哪 |
|---|---|---|
| Q1 | b：偏好做资格门，门内按低热度排，**先影子** | P3.4 |
| Q2 | b：加影子腿 `c1→c2`，不换主尺 | P2.2 |
| Q3 | 立刻钉 5.5 全 ID（保持 `max`），随后按 sweep 降档 | P1.4（钉）、P3.3（sweep 工具） |
| Q4 | b：研究层验收看截面尺 + 卡片预测计分，BUY 盈亏只作旁证 | P2.1、P3.7、P2.7 |
| Q5 | 先量噪声地板与截面尺读数，再定 L3 去向 | P2.1、P3.6 |
| Q6 | 情报分层进实验 | P3.8 |
| Q7 | 只做持仓卡 lint 观测，不动「不要任何复用」 | P2.6 |
| Q8 | 批 4 十日窗钉版后重新起算 | P1.8 |

## 计划 1 · 钉版与漂移可见性

- [x] **P1.1 价表按全 ID（A4）** —— `trace/pricing.py`：10-03 官方牌价；别名与未知 ID 不计价（不许子串命中）。补丁 01。
- [x] **P1.2 实际身份（A2）** —— `UsageRecord.models / host_version`；Claude 取每行 `version` 与全部 `message.model`；**Codex 取 `session_meta.cli_version` 与段内全部 `turn_context.model`**（档位 fallback 也留痕）；`usage_harvest.observed_identity`；`token_usage.md` 的「实际身份」行（宿主中立）。补丁 02 + 主树。
- [x] **P1.3 对账按全 ID（A3）** —— `usage_reconcile`：期望是全 ID 就按全 ID 比；`unpinned_roles` 列出没钉版的角色。补丁 03。
- [x] **P1.4 钉版（A1）** —— `scan_config.jsonc` 的 `agent_engines.claude` 推理档写 `claude-opus-5-5`、情报员 `role_overrides` 写 `claude-sonnet-5-5`；14 个 `.claude/agents/*.md` frontmatter 写全 ID；`scan/agent_frontmatter.py --check/--write` **两个引擎**（Codex 查 `.codex/agents/<role>.toml` 的 `model` / `model_reasoning_effort`）；**session preflight 对 mailbox 执行器强制「定义 = 冻结配置」**（取数前拦下；执行器由 `begin --executor` 声明并冻结、扩展沿用 —— headless run 含无人值守 `scan_run` 不受这条约束）；mailbox `wait` 给宿主 `agent_tool` 参数（全 ID 不进 Agent 工具，别名才传），README 与 10-02 首跑手册同步改写（原手册教宿主把全 ID 换成别名，会把钉版打回去）。补丁 04 + 主树。
- [x] **P1.5 跨 run 身份漂移 + 相对预算带（A3、A5）** —— `scan/run_drift.py`；`post_run` 观测发布点写 `identity / usage_shape / drift / identity_canary / relative`，summary「运行事实」一行 + 附录 E 明细；`research.drift` 有了第一个生产调用者（canary 判定入账）；`budgets.relative`（三件套：注册表 + `budget._relative_policy` + 测试）；成熟度历史改读已发布根（此前恒「真实扫描 1/10」）。条件角色（`l3-repair`、Codex 复核）在不在场不进 cohort 键、不算身份变化，两边都在场时模型变化照报。brief ⑤ 不改：它在 GATE4 前被边表 lint 封住，用量还没到。
- [x] **P1.6 契约哈希覆盖两个引擎** —— `retention.PROMPT_SOURCES` 的 agent 部分从角色注册表派生：补上 `l3-repair.md` 与 8 份 `.codex/agents/*.toml`；身份只取本场在场研究 agent 的契约（Codex = toml + 它读的 Claude md）。
- [x] **P1.7 cohort 键进账本（A7）** —— `_ledger/views/runs.csv` 加 `model_cohort / cohort_key / host_version / research_models`；`recommendations.csv` 经 `run_id = report_dir_id` 关联分层。
- [x] **P1.8 Q8 十日窗重新起算** —— 起算规则写进 `2026-09-24-buyability-realignment.md` Task 23 Step 5:钉版后第一场成功扫描记第 1 天,只数钉版 `model_cohort`;并写明 L5($46)在 Opus 5.5 `max` 下预计不达标、按原规则交用户裁。
- [x] **P1.9 主会话模型** —— 附录 E 身份行印主会话模型;`docs/session-agent/README.md` host 模式 begin 一步写明主循环是机械的,用 Opus 5.5 即可。

## 计划 2 · 评判尺与行为指纹

- [x] **P2.1 截面尺进 `stage_rulers`（A8）** —— `populations._cross_sectional_rulers`:`l1_composite_ic`、`l1_n_channels_ic`、`l3_conviction_ic`、`l3_finalist_minus_l2_rest`、`l2_pool_minus_market`,各带 `_fwd10`(block=10);`MIN_IC_NAMES=8` 登记 R8。真实账本复现探针:finalist − L2 其余 −0.26pp/日(CI −0.40…−0.13,52 日),10 日 −2.40pp;conviction IC −0.044;菜单 IC +0.076(大部分是偏好档之前的日子)。
- [x] **P2.2 影子腿 `c1→c2`（Q2）** —— 人口表上的截面尺与 L3 / L4 / E6 家族差各多一行 `_c1c2`;全市场表没有 `ret_c1_c2`,菜单 IC 与池对市场只报两把。
- [x] **P2.3 BUY 读数加成本后口径与同日对照（A9）** —— `observability.round_trip_cost_bp`(12bp,三件套);`ledger_line` 印「扣成本估算 12bp 后」与「对同日 L2 池」(读新 stage 尺 `e6_buy_minus_l2_pool`);brief ③ 实测行加「扣 12bp 后 / 对同日市场」,字节预算测试全绿;仍不许自称净收益(C14)。
- [x] **P2.4 执行线下沿单独记一档（B7）** —— `outcome.exec_band`(读时现算,老行同样适用,不改账本列);阈值 `observability.exec_floor_pct_1d / exec_floor_pos_in_range`;`ledger_line` 印 finalist 三档。真实账本(合并、非日配对、观察性):下沿 n=242 −0.28pp / 线内其余 n=299 −0.36pp / 线外 n=233 −0.22pp —— finalist 人口里执行线不分层,与 4.5 年全市场代理(线内 +0.20pp)不同,只记账。
- [x] **P2.5 行为指纹（A6）** —— `scan/behavior_fingerprint.py`;观测发布点写 `fingerprint / behavior`,summary「行为:…」+ 附录 E;历史从已发布 `trace/staging` 现算(上线即有基线);`observability.fingerprint` 组(三件套)。真实回放:09-29 场被标出持有占比 0.75 vs 0.47、减持占比 0.25 vs 0.52、入场禁止 0.75 vs 0.41、**情报链接数中位 21 vs 6(×3.5,Sonnet 5.5 换代那天)**。情景概率不可机读(p22),常数化用 EV / R:R 离散度 —— 两格是散文(`315.4(−0.3%)·带 310–322`、`0.8/1`),按真实写法解析(`EV ≈ x%` 优先、恰一个百分比才取);09-15 场两张满卡 EV 同为 −0.3%、R:R 同为 0.8/1(离散度 0)。
- [x] **P2.6 持仓卡 lint（B9，只观测）** —— `self_review.pinned_flip_lint`(进 `_review_extras` 第七条,warn):📌 评级翻转而增量节没有晚于上一场、不晚于今天的日期或当日情报/当日事实引用。认得四种真实写法(变化项 / 翻覆增量证据 / vs 昨卡…增量证据 / 昨卡回声对账)。历史回放:3 次翻转都引用了新事实(以价格类为主)→ 0 次告警。
- [x] **P2.7 评价协议改口径（Q4）** —— `2026-09-30-agent-stage-evaluation-protocol.md` 追加 §8(不改原文;冻结的前向观察按自己的哈希走)。

## 计划 3 · 契约与实验（B 类一律影子 / 离线，过门才动默认）

- [x] **P3.1 模板 ↔ 校验器往返（A10）** —— `tests/test_agent_template_roundtrip.py`:l3-rank 输出字段 = 契约 v2;l4-intel 按模板填 → `claimed_queries` / `_event_rows` / `guard_intel` KEPT;l4-card 两种卡形按模板原样填 → 评级/提案校验、恰一条机读入场行、早停行、`[执行线]` / `[价格线]` DSL;dossier-init 写入节 ⊂ 骨架节表。变异探针 4/4 被杀。sector-brief / macro-brief 原有往返测试保留。FULL 研究角色(stock/macro/sector-full、四个 intel)不在每日扫描路径上,未覆盖。
- [x] **P3.2 session_v1 三处浪费（A11）** —— ① 情报正文进卡:intel 状态任务按 attempt 冻结守卫后正文 `intel_doc`,登记为卡任务输入(情报关时不登记);② 派发前缀写明本任务可用工具(`task_access.tool_allowance`,边界 hook 放行规则 ∩ agent 定义所给工具;**按引擎分写**:Codex 没有 Read/Write,文件读写只经 task_file_broker),网查权限仍归 E5 待裁;③ 盲搜角色(无 READ)的派发不再叫它去读溯源输入、不暴露卡任务包路径。
- [x] **P3.3 effort sweep + 噪声地板工具（B1）** —— `research/noise_floor.py`:逐卡决策位(评级 / 提案 / 机读入场 / 早停 / 硬否决)、同配置自一致率(地板)、跨配置交叉一致率、`交叉 ≥ 地板 − margin` 判据(任务数不足 = INSUFFICIENT)。**重跑本身未执行**(要花订阅额度,见下方「B1 怎么跑」)。
- [x] **P3.4 菜单低热度倾斜影子 + 验收门（B2 / Q1）** —— `research/menu_replay.py --variant lowheat_gate`(偏好分位门 + 门内按热度从冷到热)+ 账本标签主尺 IC + `b2_gate`(IC ≥ 0 ∧ 落刀不升 ∧ 健康不降)。**真实回放(10 个成熟日,探索性,试了 gate_q 0.4/0.6/0.8 三档)全部不过门**:IC 由 −0.059 改善到 −0.009~−0.053 但仍为负;冷票里落刀多(0.4 档 L2 落刀 0.05→0.15)、健康占比降(0.48→0.16~0.34)。生产菜单不动。
- [x] **P3.5 E6 召回面消融（B3）** —— `research/e6_ablation.py`:用已发布决策里冻结的四面分重放,先核对能复现真实 BUY(保真 3/3),再去掉 `recall_strength` 重放;另报每个面在当日 eligible 候选内对主尺的秩相关。finalists 池只有 3 天:1 天换人(09-28 300981 +0.61pp → 002911 +0.36pp),n=1 不下结论。
- [x] **P3.6 L3 确定性旗 + 重测 Jaccard（B4 / Q5）** —— `scan/l3/rule_flags.py`:资金三同向 / B 下跌趋势(含 lowturn 例外)/ B 深跌落刀 / H 当日大涨 / ⑤ 获利盘 >90;`agreement` 量模型选择与旗的关系;`retest_jaccard` 给噪声地板用。lowturn 例外按该场 `run_contract.json` 里的 `l3.lowturn` 用 `common.turnup` 同一谓词现算(没记 → B 旗 <NA>);现行配置要求站回 MA20,与 B 的「价在 MA20 下」互斥,所以例外实际不触发、读数不变。11 场回放:硬旗亮了仍入选 2/120(B 下跌 1、B 落刀 1、H 0)—— 数字那一半契约模型几乎全守,可以确定性化而不改选择(Q5 的 b 方向有证据)。
- [x] **P3.7 L4 卡机读影子字段 + 计分（B5）** —— 三条影子行经任务包「本次参数」下发(`l4.shadow_fields`,缺省开;不进 agent 定义 —— 定义在字节预算线上,且影子要能一键关):`**兑现机制**: 成立|不成立|未核`、`[入场否决] close <op> <数>`、`[情景] bull … · base … · bear …`;`scan/l4/shadow_fields.py` 在 observe 落 `_card_shadow_fields.json`(新产物已登记;行首带列表符 `- ` 同样认 —— 卡里的 DSL 行全是这么写的);`research/card_shadow_scoring.py` 事后计分(三态 × 主尺、EV 秩相关、否决触发与否)。下一场扫描起有数据。
- [x] **P3.8 情报分层证据读数（B6 / Q6）** —— `research/intel_tiering.py`:two-stage-v1 的 `changes.json`(不读情报的初评 → 终评)按 E6 硬门幸存 / 被拒分组,数改动字段与情报引用。目前没有任何 two-stage 记录 → 空读数;要证据需先跑 `card_research_profile=two-stage-v1` 的扫描。
- [x] **P3.9 派发省 token（B8）** —— 研究 agent frontmatter 加 `maxTurns`(与 headless `--max-turns` 同一张表,真身下沉到 `contracts.agent_roles`;mailbox 此前没有任何轮数上限)与 `omitClaudeMd: true`(`agent_frontmatter` 镜像、两引擎核对);按引用派发 `session.mailbox.by_reference`(缺省关:全文冻结成授权文件,宿主只传一行 `host_prompt`)与扇出预热 `session.runner.fanout_warmup_s`(缺省 0)两个开关已就绪。Codex 侧无对应的轮数 / CLAUDE.md 开关。

## B1 怎么跑(要花订阅额度,未执行)

两档都走 **headless 执行器**(`session_agent begin … --executor headless` + `session_agent run --run-id <id>
--executor headless`):headless 用 `--model/--effort` 显式透传;mailbox 下 `l4_card` 与 `ens_review` 共用
`l4-card.md`,一份定义只能有一个 effort,preflight 会拦。stock LITE 走 headless 是首次,先 1 只冒烟。

1. 选 20–40 只票(如最近两场的 finalists),同一分析日。基线:按现行配置对每只跑 k=2 次 stock-research LITE
   (`--kind stock-research --mode LITE`,`usage=standalone`,与扫描 L4 同一个 l4-card),把卡存成
   `<dir>/base/<代码>/r<i>.md`。
2. 候选:session run 只读生产 `scan_config.jsonc`(没有换路径的入口),所以**避开无人值守扫描时段**,临时给
   `agent_engines.claude.role_overrides.l4_card` 加 `"effort": "high"`,同样跑 k=2 次 → `<dir>/cand/<代码>/r<i>.md`,
   跑完立刻改回。
3. `uv run --no-sync python -m autoresearch.research.noise_floor --baseline <dir>/base --candidate <dir>/cand --out <dir>/readout`
   → `EQUIVALENT` 才改档位;`INSUFFICIENT` 加票,不放宽边际。
4. 成本:09-29 场 l4-card(opus-5-5·max)约 $1.65/张(官方牌价折算),40 只 × 4 次 ≈ 160 张 ≈ $260 等价额度;候选档位更便宜。

## 独立复审(10-03,只读 reviewer,对开工前快照取 diff)

结论 Critical 0 · Important 5 · Minor 4,**全部已修**,每条都先补会红的测试(I-1/I-2/I-3 另做变异探针,全杀)。

| # | 问题 | 修法 |
|---|---|---|
| I-1 | mailbox 的「定义 = 冻结配置」也套在 `begin` / 扩展上 → headless run(含无人值守扫描、B1 配方)开跑前被拦 | `begin --executor` 声明并冻结在 `role_support.json`,扩展沿用;`scan_run` 传 `headless`;报错文案写明共用定义只能走 headless |
| I-2 | 条件角色(L3 修复、Codex 复核)在不在场让 cohort 翻面、canary 报模型变了 → Q8 十日窗会被无故重置 | `run_drift.CONDITIONAL_ROLES` 不进 cohort 键、不算在场变化;canary 只比两边都在场的;31 场真实历史回放 cohort 稳定 |
| I-3 | 影子行解析只认行首,卡里的 DSL 行全带 `- ` → 否决读成空、情景记成格式错 | 行首可带列表符 |
| I-4 | 「本任务可用工具」是 Claude 口径,也发给了 Codex(Codex 只有 task_file_broker) | `tool_allowance(policy, engine)` 按引擎分写 |
| I-5 | 非法 `observability.fingerprint` 能过加载与 lint,到发布点才炸,决策文件不写 | `prepare_scan_run` 开跑即校验;post_run 两条观测腿失败只记未计量 + advisory,不让发布失败 |
| M-1 | EV / R:R 是散文格,`float()` 永远失败 → 常数化探测从来不响 | 按真实写法解析;真实 5 场全部读得出 |
| M-2 | L1 帧没有 lowturn 列,B 例外被当成「没有」 | 按该场配置现算;未知 → <NA> |
| M-3 | `ledger_line` 每行重读配置,真实账本 11.5 s | 配置只解析一次 → 0.05 s,读数不变 |
| M-4 | 观测腿失败时附录 E 写「本场没有可读的卡」「可比的 0 场」 | 改印「—(未计量)」 |

## 验证记录

| 时点 | 范围 | 结果 |
|---|---|---|
| 10-03 补丁 01–05 应用后 | 两引擎：pricing / usage_harvest / reconcile / frontmatter / agent_defs / user_config_roles / run_drift / headless / panorama | 290 passed（codex，6 skipped）/ 296 passed（claude） |
| 10-03 全部实施后(快照 3,复审前) | 两引擎全量 | codex 1 failed + 7 errors / 9271 passed;claude 195 failed + 7 errors / 9083 passed —— 失败集合与开工前基线逐条相同(8 / 202 条,均为既有红),无新增、无消失 |
| 10-03 复审 9 条修复后(快照 4,终版) | 两引擎全量 | codex 1 failed + 7 errors / **9303 passed**;claude 195 failed + 7 errors / **9115 passed** —— 失败集合仍与开工前基线逐条相同。codex 那 8 条是快照副本缺 gitignored 现场的假红(主树上同三个文件 156 passed);claude 那批是已知「Claude 会话下 forensics / session_agent 引擎环境红」 |
| 10-03 终版 | 静态 | ruff 与开工前逐文件逐规则相同(无新增);`config_standard` 0 违规;`agent_frontmatter --check --engine all` 同源;分层测试绿 |

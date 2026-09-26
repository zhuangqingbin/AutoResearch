# session_v1 扫描计划 vs legacy Workflow 分支审计(批 2–3 Task 2,2026-09-26)

- 计划:`docs/superpowers/plans/2026-09-26-daily-engine-batch2-3-driver.md` Task 2(时间盒);设计稿 §4 A1-2/A1-3/A1-4。
- 对照面:`.claude/workflows/scan-market.js:311–640`、`.claude/workflows/l4-stock.js:300–561`(行号 = 本分支 aa8ed71 起点)
  ↔ `autoresearch/session_agent/workflows/scan.py`(冻结计划 + 展开)、`domain_ops.py` 的 `scan_*`、`service.py`、
  `validation.py`,以及本批新增的 `runner.py` / `executors/`。
- 方法:① 逐分支读两侧代码;② **校验器回放**(只读):把 session_v1 的输出契约校验谓词原样跑在主仓
  `context_claude/scan_runs/` 最近 13 场 legacy 真跑的真实产物上 —— 「legacy 写得出、session_v1 收不下」的
  东西就是真跑必撞的阻断。回放脚本不写盘,结果见 §3。
- 分级:**阻断** = 一场正常真跑必然撞上;**非阻断·高风险** = 不是每场都撞,但撞上即整场停(legacy 会降级继续);
  **降级可接受** = 行为/成本差异,记账即可;**无关** = 当前配置/新 run 不经过。

## 1. `scan-market.js`(确定性前段 → L4 交接)

| # | legacy 分支(行) | session_v1 对应 | 缺口 | 分级 |
|---|---|---|---|---|
| S1 | frame(314,detached)+ pack-check 失败重试一次;仍失败 → **B 级降级继续**(325–342) | `scan.frame` 操作(frame + strategist 投影,产物非法 JSON 即 raise);runner 对失败的幂等确定性任务重试一次 | 两次失败后 session_v1 **BLOCKED**(fail-closed),legacy 带「无市场地形」继续 | 非阻断·高风险 |
| S2 | strategist_pack 缺失 → 补投一次,仍缺记账继续(344–358) | 并入 `scan.frame`(投影失败 = 操作失败 → 同 S1) | 同 S1 | 非阻断·高风险 |
| S3 | prelude(362,detached)∥ market_view;L2 缺 → `--skip consensus` 重跑一次(383–391) | `scan.prelude`(缺 summary/L2 即失败)∥ `scan.market_view`;runner 重试一次(不带 `--skip consensus`) | 重试参数不同(多跑一次 consensus 取数) | 降级可接受 |
| S4 | market_view(macro-brief)失败/缺席 → L3 无地形段、L5 走确定性脉搏回退(B 级) | `scan.market_view` 推理任务;GATE1 依赖它;输出过 `macro.brief.v1` 校验 | **校验器与 agent 模板不一致:12/13 场真实 market_view 被拒 → 整场在 GATE1 前 BLOCKED**(§3);另:agent 失败 → BLOCKED(legacy 降级) | **阻断**(校验器)/ 非阻断·高风险(agent 失败) |
| S5 | GATE1 四态 + l4_budget 守卫(396–422) | `scan.gate1` → `run_mode.json` → `sector_expansion` 分模式展开;`scan_gate1` 对非法 l4_budget 失败 | 一致 | 无缺口 |
| S6 | SENTINEL_EMPTY → 跳 L3/L4,主会话收尾(423–427) | `scan.sector.skip` → `scan.gate2.skip` → `scan.l4.skip` → `scan.reviews.skip` → `scan.review3.skip` → L5 | 链一致,但 `scan.l3.repair` 模板不可达 → 永远到不了 DONE(见 §5 阻断 3,已修) | **阻断**(已修) |
| S7 | SENTINEL_PINNED → gate2 skip + **只跑 prompts**(无四生产者、无任务簿)(428–440) | `scan.gate2.skip` 写持仓 finalists → `scan.l4.prepare`(**跑四生产者** + prompts + 任务簿) | 持仓哨兵档的 L4 任务包多了 pledge/seats/consensus/fund_hold 素材 → prompt 与 legacy 不逐字相同 | 降级可接受(Task 6 replay 时记账) |
| S8 | l3cap 守卫(451–456;控制器分支改为 GATE1 回显 `l3cap`/`max_cards`) | `dispatch.l3_bounds`:读冻结 `gate1.json` 的 `l3cap`,缺则 `min(10, l4_budget)`;`l3lo=min(7,l3cap)` | 一致(控制器口径) | 无缺口 |
| S9 | sector reuse + pack + 待写清单(463–470);brief 派发(478–491) | `scan.sector.prepare`(`select_briefing_sectors(k=sector.max_briefs)` + `find_reusable/apply_reuse` + pack 冻结进 `session_inputs/sectors/`)→ 每个未复用行业一个 `sector.brief` 推理任务 | 选行业口径:legacy = CTX/sector/<date> 下「无 brief 的 pack」;session = `select_briefing_sectors`;brief 读冻结 pack 路径(提示词措辞相同,路径不同) | 降级可接受 |
| S10 | l3 prepare(478,detached)∥ briefs → l3-rank(492–497) | `scan.l3.prepare` → `scan.l3.rank`(依赖全部 brief) | 一致;prompt 逐字同款(测试锁) | 无缺口 |
| S11 | lint 失败 → repair-pack → repair agent(失败只记账继续)→ apply(未完成带原 judged 继续)(498–535) | `scan.l3.lint` → `l3_repair_expansion`:通过 → `scan.l3.repair.skip`;未过 → `scan.l3.repair`(推理)→ `scan.l3.repair.apply`;repair/apply 失败 → `_degrade_optional_l3_repair` 置 SUPERSEDED,带原 judged 继续 | 设计一致,但 `store.claim` 不认 SUPERSEDED 依赖 → GATE2 认领不了(§5 阻断 4 ③,已修) | **阻断**(已修) |
| S12 | finalists + GATE2(537–549) | `scan.l3.merge`(控制器分支:`write_finalists(l3cap)` + `gate2(max_cards)`) | 一致(控制器负责) | 无缺口 |
| S13 | L4-prep 四生产者并行 `|| true`(560–571)→ dispatch-plan(574) | `scan.l4.prepare` 串行跑五个可选生产者,各自降级留痕 `producer_status` | 串行(墙钟) | 降级可接受 |
| S14 | 非流式 GATE3 批量 slim(579–608) | 不实现(只有流式单票 slim) | `performance.streaming_l4=false` 回滚杆在 session_v1 无效 | 无关(生产配置 = true) |
| S15 | 流式:prompts + `l4_tasks init`(caps 取配置)(611–628) | `scan.l4.prepare` → `legacy_scan.initialize_tickets(caps=None)` → `DEFAULT_CAPS` | 帽不读 `budgets.concurrency`(今天两者数值相同 4/4/4/64) | 降级可接受 |
| S16 | 📌 pinned 透传 args.pinned(633–638) | `scan_review_plan` 从 finalists.csv 的 `lane=pinned` 取 | 一致(同源) | 无缺口 |
| S17 | 异常 → `capsule finalize FAILED`(648–668) | runner 停在 BLOCKED/STALLED 时**不**冻结 capsule(保留人工 `retry-l4`/`resume` 余地) | 失败 run 保持 ACTIVE 直到人工 finalize | 降级可接受(README 写明;批 4 调度器负责 FAILED 冻结 + 推送) |

## 2. `l4-stock.js`(每股链)

| # | legacy 分支(行) | session_v1 对应 | 缺口 | 分级 |
|---|---|---|---|---|
| L1 | preflight 无返回 / BLOCKED / WAIT → 只废本股(312–330) | ticket 任务 `l4.<code>.a<n>`(L4_TASKBOOK owner)由 runner `claim`(= taskbook preflight);拒绝 → runner 记 skipped | 本股之后 `scan.review.plan` 依赖其卡 → **整场 STALLED**(见 L9) | 非阻断·高风险 |
| L2 | SKIP(三件产物 hash 验过)→ 复用(321–324) | claim 返回 SKIP 不抛错,但 ticket SUCCEEDED 时子任务永不就绪 | 仅「同一任务簿续跑」触发;新 run 不经过 | 无关(新 run_id) |
| L3 | attempt 与 args 不一致 → throw(330–332) | `claim(expected_attempt)` 同一校验 | 一致 | 无缺口 |
| L4 | intel_resume → 跳过盲搜(333,413–419) | session 不读 `intel_resume`,照派 intel | 同日续跑多付一次 intel | 降级可接受(成本) |
| L5 | intel 三次瞬时重试(INTEL_RESEARCH 口径含 ENOTFOUND)后 **卡回退卡内网查**(367–387) | intel = 推理任务;失败 → `service.fail` → `fail_ticket`;TASK_ATTEMPT 类 → runner `retry_l4` 整棵子树重来一次(含 slim),否则 ticket BLOCKED | ① 无 in-attempt 重试(ENOTFOUND 归 CONNECTION);② **intel 终失败不降级为「卡照出」,而是整票失败 → 整场 STALLED**(L9) | 非阻断·高风险 |
| L6 | intel 关 → `--disabled` 状态落盘(402–412) | `scan.l4.intel.disabled` 操作 | 一致 | 无缺口 |
| L7 | intel_guard REJECTED/TRIMMED → 卡回退(420–437)+ intel_status 三正交字段(438–443) | `scan.l4.intel.status`:`guard_intel` + `normalize` + `from_guard` + 写 status/bundle | 一致 | 无缺口 |
| L8 | slim LOST/TIMEOUT → 可重试 TIMEOUT;slim 不合格 → DATA_INTEGRITY;无返回 → CONTRACT_ERROR(445–461) | `scan.l4.slim` 操作失败 → `OPERATION_FAILED`(非瞬时)→ ticket FAILED 不重试 | slim 超时不再可重试 | 非阻断·高风险 |
| L9 | card 异常 → 分类失败,**只废本股**;card 无返回 → CONTRACT_ERROR(464–482) | card 推理任务;TASK_ATTEMPT 类 → `retry_l4`;其余(含输出缺失/契约不过)→ ticket BLOCKED | **单票终失败 = 整场到不了 L5**:`scan.review.plan` 依赖全部卡、`scan_review_plan` 逐张读卡、`scan_l4_complete` 要求任务簿全 SUCCEEDED;legacy 由 L5 盲卡机制(`_blind_cards.json`)兜底 | 非阻断·高风险(最大的一条) |
| L10 | ow_review / sell_review 触发(491–493) | `scan_review_plan`:严格解析卡面评级 ∈{Buy,Overweight} → ow;pinned 且 FINAL=SELL → sell | 等价(校验器保证 Sell 评级 ⇔ SELL 提案) | 无缺口 |
| L11 | 复核 run2 → 同档早止 → 分歧才 run3(494–516) | `review_expansion`(review2)→ `scan.review.decide` → `review3_expansion`(仅 `same_tier is False`) | 一致 | 无缺口 |
| L12 | 复核 run 失败 → `degraded`、不折回、继续(517–545) | review 推理任务失败 → `fail_ticket` → ticket 失败 → 同 L9 整场停;复核还需真实 transcript 绑定(独立上下文回执) | 无 degraded 路径 | 非阻断·高风险 |
| L13 | ens-dump 走 general-purpose 壳(529–540) | `scan.l4.finalize` 进程内写 `_ensemble_<code>.json`(`ensemble_record`) | 壳归零 ✓ | 无缺口 |
| L14 | task success/failure + recordL4 阶段记录(547–561) | `scan.l4.finalize` → `record_l4_result` + `complete_ticket`;失败路径由 `service.fail/execute` 写任务簿 + capsule failure 证据 | 失败票不写 `recordL4('<reason>')` 阶段记录(整场停,L5 读不到) | 降级可接受 |

## 3. 校验器回放(只读,主仓 13 场 legacy 真跑,2026-09-01 … 09-24)

| session_v1 契约 | 校验谓词 | 真实产物 | 拒收 |
|---|---|---|---|
| `macro.brief.v1`(market_view) | 行首 `N. **` 编号 1–6 齐 | 13 | **12**(第 6 节模板是 `6. 仅供研究,非投资建议。`,**无加粗**;只有 1 场模型多加了粗)→ Task 5 修复后 **0** |
| `sector.terrain.v1` | 有地形段且无方向词 | 106 | 0 |
| `stock.lite.v1`(scan L4 卡) | 严格评级+提案一致+早停/P4+代码在场 | 80 | 0 |
| `scan.l4.intel.v1` | 含「## 事件段」「## 声明行」 | 93 | 0 |
| `scan.l3.v1` | 非空 list、每行布尔 finalist | 12 | 0 |
| `scan.l3.repair.v1` | 只含 code/thesis 且码集 = repair pack | 8 | 0 |
| 复核卡(`scan.l4.review`) | 同 L4 卡 | 6 | 0 |

## 4. runner / 宿主层面的新差异(本批引入,非 JS 分支)

| # | 项 | 说明 | 分级 |
|---|---|---|---|
| R1 | 确定性任务单车道串行 | slim 不再 4 路并行(tushare K 槽仍在 `prepare_slim` 里生效);L4 墙钟略长 | 降级可接受 |
| R2 | 每个推理任务都要 transcript 绑定才算证据完整 | host 模式:`mailbox complete --context-ref <agentId>` 自动推导 `<session>/subagents/agent-<id>.jsonl`,runner 绑定;推导不到 → `completeness_ok=false`;**复核任务缺绑定 → EVIDENCE_MISSING → 同 L12** | 待 Task 6 真跑验证 |
| R3 | 超时后迟到结果 | 只认本 attempt 的 result 文件,迟到结果永不被接收;但超时的 subagent 不会被杀,可能迟到覆写同一产物文件 | 降级可接受(超时给得宽;README 写明) |
| R4 | host_profile 能力声明 | review 需 `independent_context=true`,intel 需 `web_search/web_fetch=true`;声明不足 → claim 期 HostCapabilityError → runner 以 CLAIM_ERROR 释放 → 同 L9 | 非阻断(begin 时一次性声明对即可) |
| R5 | 确定性车道与图读取并发 | execute 先落 expansion 文件、后同步 artifact/store;loop 线程在窗口内读图会看到 store 不认识的 READY 任务(合成 FULL 扫描首跑即崩)| **已修**(Task 5:车道忙时不读图 + 未同步任务跳过本轮;同步失败 → STALLED 并保留根因)|

## 5. 结论与处置

**合成全链(Task 5 新增)**:`tests/session_agent/test_scan_runner_full.py` 用真实 `build_scan_plan`、真实展开/
artifact 登记/任务簿 ticket/生产输出校验器,只把确定性操作(假 operation runner 按登记路径写产物)和模型
(假 executor)换成替身,由 runner 从 `begin` 一路跑到 `finish`:FULL(Hold 卡)与分支版(intel 开 + L3 修补 +
📌 SELL 独立复核回执)各一场。**此前没有任何测试经 service 走过扫描图**,首跑即暴露下面第 2 条阻断。

**阻断 2**:`scan.l4.taskbook`(`_l4_tasks.json`)被声明为 `scan.l4.prepare` 的输出、每个 ticket 的输入,但
`register_scan_expansion_artifacts` 故意不登记它(它是任务簿 owner 的**可变状态**,每次 preflight/success 都改写,
不能做 hash 冻结的 artifact)→ `service.execute("scan.l4.prepare")` 在绑定输出时 `KeyError` → **所有带 L4 票的
模式(FULL/FORCED_FULL/SENTINEL_PINNED)真跑必在 L4-prep 停住**。Task 5 已修:从冻结图的 artifact 清单里拿掉
任务簿(ticket 的认领证据本来就是冻结的 preflight 回执),合成全链先红后绿;变异(放回输出清单)即红。

**阻断 3**:哨兵模式(SENTINEL_EMPTY / SENTINEL_PINNED)的图里没有 `scan.l3.lint`,`scan.l3.repair` 模板永远不会展开,
而 `service._state` 要求**全部**模板展开才给 `DONE` → 哨兵日的 session_v1 run 永远停在 `WAITING/EXPANSION_PENDING`。
Task 5 已修:扫描工作流按冻结的 `run_mode` 声明「本模式不可达的模板」(`workflows.inapplicable_templates`),
`_state` 不再等它们;合成 SENTINEL_EMPTY / SENTINEL_PINNED 全链先红后绿,变异(去掉声明)即红。

**阻断 4(恢复路径,三处连环)**:session_v1 设计里的两条「失败后继续」路径从没被端到端走通过 ——
① L4 瞬时失败 → `retry-l4`:a2 子树的子任务 session attempt 从 1 起,`legacy_scan.verify_child_handoff` 却拿它
比对任务簿的 ticket attempt(2)→ 每个重试子任务提交都「stale or foreign handoff」;修正为比对子任务声明的
`parent_task.attempt`。② a1 卡超时根本没写文件,`_promote_l4_retry_output` → `artifacts.binding_sha256` 却要求
该文件存在 → 崩;未绑定的输出现在直接返回 `None`(`replace_failed_output` 本就支持这种情形)。
③ `plan.ready_tasks` 把 SUPERSEDED 依赖当满足,`store.claim` 却只认 SUCCEEDED → 重试后的 `scan.review.plan`、
L3 修补降级后的 `scan.gate2` 都 READY 却认领不了 → STALLED;store 与 ready_tasks 统一口径。
合成全链两条:「a1 卡 TIMEOUT → 自动 retry-l4 → a2 卡提升 → finish」「L3 修补补丁不合契约 → 降级带原 judged →
finish」均先红后绿,三处各自变异即红。**这意味着 L9/L5/L8 的「瞬时类失败自动重试一次」兜底此前实际上不工作**,
现在才成立(非瞬时终失败仍是 open 的高风险项)。

**阻断 1**:S4 校验器 —— `validation._macro_brief` 要求 6 节全部加粗,而 macro-brief agent 模板第 6 节不加粗。
可合成复现、改动局部(只动 session_v1 校验器,不碰 legacy 路径与评级)→ **Task 5 已修**:
`validation.market_view_complete` 按模板形状判(1–5 节带加粗标题 + 第 6 节免责行),合成 run 测试
`tests/session_agent/test_scan_runner_gaps.py` 先红后绿,回放 13/13 真实 market_view 通过。
同一条过严正则还在 macro-research LITE 的 `domain_ops.macro_lite_validate`(`_MARKET_VIEW_SECTION_RE`)里:
不在扫描 runner 路径上,且位于本批约定只改 `scan_l4_*` 的 domain_ops 区域之外 → **open**(macro LITE
session_v1 真跑前改成调用 `market_view_complete` 即可)。

**非阻断·高风险(Task 5 不修,留 open;Task 6 前必须知情)**:L9/L12/L1/L5/L8 同源 —— session_v1 的扫描图里
**一只票的终失败会让整场到不了 L5**,而 legacy 把它降级成盲卡/degraded 继续。修法需要改冻结计划的依赖形状
(`scan.review.plan` 容忍失败票、`scan_l4_complete` 接受 FAILED/BLOCKED 票并交给 L5 盲卡)与 evidence 分母
(SUPERSEDED/未认领任务的证据要求),属于图结构改动,超出本批时间盒;且冻结窗内每场要 replay 对账,
不宜在没有真跑的情况下改。现状下的兜底:瞬时类失败 runner 自动 `retry-l4` 一次;其余情况 run 停在
BLOCKED/STALLED,操作者可切回 `LEGACY_ORCHESTRATION_FALLBACK`(默认入口本来就还是 legacy)。
S1/S2/S4(agent 失败)同理:fail-closed,不修。

**降级可接受**:S3/S7/S9/S13/S15/S17、L4/L14、R1/R3 —— 记账,Task 6 真跑时对照。

**计划 Task 5 预列候选的去向**:① intel 三次瞬时重试 + `intel_resume` → L5/L4(非阻断,open);② `intel_guard`
REJECTED 后卡仍派 → L7(已等价);③ ow/sell 双复核触发与同档早止 → L10/L11(已等价,合成分支版覆盖 sell_review
早止);④ slim 的 tushare K 槽信号量 → R1(`prepare_slim` 内仍生效,runner 串行更保守)。

**Task 6 前置检查**(给用户):`host_profile` 声明 `independent_context/web_search/web_fetch=true` 并附证据;
`mailbox complete` 传 subagent 的 agentId;观察一场里是否出现任一单票终失败(出现即按上表判定为已知缺口,
换 legacy 回退,不算 runner 缺陷)。

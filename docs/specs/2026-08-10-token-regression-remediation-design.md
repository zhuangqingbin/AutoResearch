# Token 消耗回归分析与降耗设计(方案 B)

- **日期**:2026-08-10 · **状态**:DESIGNED,**未实施**(用户裁定:只出文档不开发)
- **触发**:2026-08-07 全扫真计量 加权 **20.59M** / **$90.00** / 274 subagent,vs 清洁基线 ~7.5M / ~$32 / ~100 subagent(2.7×)
- **用户裁定**(2026-08-10):取**方案 B** = 防呆硬门 + 同日 intel 断点续传;R5(2026-07-29「跨日 L4 卡 TTL 复用退役,不要任何复用」)**原判不动**——本设计的"续传"仅限同一 analysis_date 内的 crash-resume,与任务簿对卡片既有的同日 hash-验证跳过语义同族。
- **约束**:不影响效果 —— 不动评级/rubric/主尺/effort/intel 查询上限/派发模式(fb_20260714_003 一次性全派);目标 = 干净单轮回到基线(≤$35 / ≤8M 加权)。

---

## 0. TL;DR

**没有结构性回归。** 逐 role 单价与 08-06 基线持平(intel $0.69 vs $0.58/次、卡 $0.82 vs $0.87/张、壳 $0.09 vs $0.07/个);$90 里约 **$53 是浪费**,全部来自「同一晚跑了三轮 L4」:

1. 一个 **3.5 秒**的 `l4_card prompts` 步骤没执行(被捆在 20+ 分钟长命令壳里,壳被 harness 后台化、turn 结束时连锅回收),而**编排对它零产物验证**——任务簿 init 明知 12 个 prompt 全 MISSING 照样 `ok:true`,于是 **12 股整轮盲跑**(≈$23,盲卡全废弃);
2. 重派第二轮时撞 session limit,**intel 无同日断点续传** → 第三轮又把 intel 全部重盲搜(34 次 intel 里 **22 次重复**,≈$15;协创数据一晚被六面盲搜 3 遍);
3. 每多一轮 = 主会话多 ~24 次全上下文唤醒,主会话 $5.3 → $25.3(**+$20**)。

修法 = 让 3.5 秒的关键产物有硬门(C1/C2,防整轮盲跑),让事故重派不再重付 intel(C3),外加两件记账/运维卫生(C4)。**全部是编排/护栏改动,不触任何评级语义。**

---

## 1. 事实读数(全部出自各日 `_token_usage.json`,usage_harvest 真计量)

### 1.1 九个 run 横向对比

| run | subagents | 加权 | 输出 | 成本 | 主会话$ (占比) | 主会话消息 | cache% | 备注 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 07-28 | 100 | 11.61M | 979k | $62.68 | $30.50 (49%) | 143 | 91.7% | 事故日(W8-10 下沉的那次) |
| 07-29 | 93 | 6.13M | 764k | $31.82 | $4.37 (14%) | 39 | 86.3% | **净** |
| 07-30 | 139 | 10.05M | 903k | $48.82 | $14.05 (29%) | 87 | 88.3% | 偏高 |
| 07-31 | 93 | 6.91M | 734k | $34.48 | $5.32 (15%) | 51 | 88.6% | **净**(FORCED_FULL) |
| 08-03 | 110 | 8.80M | 745k | $39.25 | $12.82 (33%) | 86 | 90.5% | 偏高 |
| 08-04 | 104 | 7.75M | 498k | $29.56 | $5.30 (18%) | 42 | 90.4% | **净** |
| 08-05 | 123 | 11.43M | 744k | $45.72 | $12.00 (26%) | 87 | 90.6% | 事故日(haiku pkill) |
| 08-06 | 114 | 7.66M | 686k | $33.85 | $5.25 (16%) | 33 | 86.2% | **净** |
| **08-07(本次)** | **274** | **20.59M** | **1810k** | **$90.00** | **$25.26 (28%)** | **74** | 85.8% | 两次废跑 |

> **基线定义**(本设计采用):清洁 run 四次(07-29/07-31/08-04/08-06)的区间 = **$29.6–34.5 / 6.1–7.8M 加权 / 93–114 agents / 主会话 $4.4–5.3(33–51 消息)**。记忆里 07-24 的「50 agent / 5.49M」是换代前口径(滑窗派发 + 部分壳未拆),不作为目标。
>
> **规律**:主会话成本是事故指示器——净日恒 $4-5,事故日 $12-30(07-28 $30.5 / 08-05 $12 / 本次 $25.3)。主会话每次唤醒按全上下文计 cache 读,唤醒次数 ∝ 轮次。

### 1.2 本次三轮分桶(按 subagent transcript mtime;r1<00:22,r2 00:22–00:55,r3≥00:55)

| 轮 | 合计 | 构成 | 定性 |
|---|---:|---|---|
| r1「盲跑」 | $30.87 | l4-card 10 张 $9.00 + intel 12 次 $8.22 + 壳 92 个 $8.30 + **前段(L3 $2.52/sector $2.22/macro $0.62,正常成本)** | L4 段 **≈$23 全废**(盲卡已移 `_blind_cards_20260810/` 留证) |
| r2「限流轮」 | $11.06 | intel 12 次 $8.22 + 卡 4 张 $1.48 + 壳 14 个 + 39 份 `(未标注)` $0 | 救回 2 张卡(000100/600867),**浪费 ≈$9** |
| r3「成功轮」 | $22.80 | 卡 11 张 $10.09 + intel 10 次 $6.94 + 壳 61 个 $5.77 | 有效 |
| 主会话超额 | +$20 | 74 消息 vs 基线 33–51;12.55M cache 读 + 1.71M 1h 写 | 3 轮通知×~24 次唤醒 + 事故处置对话 |

**有效成本 ≈ $90 − $53 ≈ $37 ≈ 基线。** 即:只要不三轮重跑,本次就已经"和之前一样"。

### 1.3 事故时间线(产物 mtime,08-09 深夜)

```
23:57:01  _l4_shared_instructions.md   ← l4-prep 长命令第一步
23:57:02  consensus.csv                ← 四个生产者并行,
23:57:08  pledge.csv                      25 秒内全部跑完
23:57:12  calendar.csv
23:57:26  seats.csv                    ← wait 应在此返回,prompts(3.5s)应在 ~23:57:30 落 12 份
   ———    _l4_prompt_*.md 零文件      ← prompts 从未执行
00:16-17  盲卡落盘(002603 等)        ← l4-card 拿不到任务包,凭 slim 自行拼凑出卡
00:17     首个 BLOCKED(SCHEMA_ERROR) ← mark_success 查产物才喊「prompt MISSING」——太晚了
00:19:32  手工补跑 prompts(3.49s,12 份全落)
```

壳 agent 的最终回报原话:「Still waiting on the l4_card pipeline (PID 9800). No completion event received yet — continuing to hold quietly.」——**壳完全遵守了它的指令**(禁杀、安静等、如实报告拿不到退出码),病不在壳(instruction-vs-check 家训:先读它的指令原文)。病在:harness 把长命令转后台、壳 turn 结束时后台进程被回收,恰好砸在 prompts 这 3.5 秒窗口;而 workflow 拿着「没有退出码」的回报**照样往下走**,后续零产物验证。

### 1.4 反结论(防止砍错地方)

- **不是** intel/卡/壳单价涨了(逐 role 与 08-06 持平);
- **不是** L3/策略师/行业 brief 变贵($2.5/$0.6/$2.2,历史区间内);
- **不是** 结构性 agent 膨胀(净轮 ~110 agents,与 08-04/08-06 相同);
- 07-24 记忆读数(50 agent/5.49M)是旧口径,拿它当目标会推出「砍一半 agent」的错处方(rank 同「按旧估算去砍会砍错地方」家训)。

---

## 2. 根因排名(按 $ 归因)

| # | 根因 | 本次代价 | 病灶定位 |
|---|---|---:|---|
| R1 | **prompts 产物零验证**:`l4_tasks.initialize` 记录了 `artifacts.prompt.status=MISSING` 却仍 `ok:true`;`preflight` 认领时不看 prompt 在不在;l4-card agent 拿到「执行一个不存在的文件」的指令后自行拼凑出卡 | ≈$23(r1 L4 段全废)+ 连带 R3 | `autoresearch/scan/l4_tasks.py`(initialize/preflight)+ `.claude/workflows/scan-market.js` l4-prep 段 |
| R2 | **prompts 被捆在长命令壳里**:3.5s 的关键步骤排在 20+ 分钟命令末尾,壳被后台化回收时最易被误杀(08-05 pkill 事故的镜像:那次是壳杀作业,这次是 harness 收壳) | 同上(R1 的成因) | scan-market.js `l4-prep` bash 壳 |
| R3 | **intel 无同日断点续传**:任务簿对**卡**有 crash-resume(SUCCEEDED + 三产物 hash 验证 → SKIP),intel 腿不在任务簿管辖,每次重派全量重盲搜 | ≈$15(22/34 次 intel 重复) | `.claude/workflows/l4-stock.js` intelLeg |
| R4 | **轮次 × 主会话唤醒**:每轮 ~12 workflow 完成通知 + ~12 monitor 事件,每次唤醒按全上下文计费;叠加事故处置对话 | ≈$20 | 无独立病灶——R1-R3 修掉轮次即修掉它 |
| R5' | 记账/运维小病:39 份 limit-killed transcript 落成 `(未标注)` role;l4_watch 双挂重播;`preflight` 是认领语义却被当只读探针误用(本次抢锁 600276 一次) | <$1 + 噪音 | usage_harvest / l4_watch / 运维文档 |

外生不可修:session limit(外部配额)。可修的是**它砸下来时我们赔多少**——现在赔一整轮,C1+C3 后只赔未完成的卡。

---

## 3. 设计(方案 B,四个组件)

### C1 任务簿硬门:prompt 缺失在**派发前**拒绝,而不是出卡后才喊

**C1a `initialize()` 拒绝初始化**(`autoresearch/scan/l4_tasks.py:178` 起):

- init 现有逻辑已逐票记录 `artifacts.prompt.{path,status}`;在返回前新增:统计本次 codes 中 `prompt.status == MISSING`(或文件 0 字节)的票,**≥1 即返回 `{ok:false, reason:"prompts 缺失", missing:[codes...]}` 且不落盘任务簿**(merge 语义不变:既有账本不受影响,修好 prompts 后重跑 init 即可)。
- 消费端**零改动**:scan-market.js 的 `l4-tasks-init` gate 已有 `if (!tasks || !tasks.ok) throw new Error('L4 task book 初始化失败')`(scan-market.js:395 附近)——现有的门第一次真正拿到可以拦的信号。整条流水线 fail-fast 在**派发前**,一个 LLM token 都还没花。

**C1b `preflight` 一律验 prompt 文件**(同文件 preflight 分支,l4_tasks.py:277–347 区段):

- 在认领(置 RUNNING)**之前**检查 `scan_dir/_l4_prompt_<code>.md` 存在且非空;缺 → 返回 `{ok:true, action:"BLOCK", reason:"PROMPT_MISSING"}`,**不认领、不动状态**。
- 为什么两道都要:init 只覆盖 FULL 路径;`SENTINEL_PINNED` 路径不建任务簿,preflight 是唯一的每股闸口。**实施计划核准后的前提修正(2026-08-10)**:现行 LEGACY 分支在 `taskGate` 的**壳命令**里(`if test -s 任务簿; then …preflight…; else echo LEGACY; fi`,l4-stock.js:70-72)——bookless 时 python 根本不被调用。故 C1b 落地 = preflight 的壳从 `taskGate` 换成 `gpJson` 直调,由 **python 接管 bookless 分支**(壳零判断铁律的正确归位):prompt 缺 → `BLOCKED/PROMPT_MISSING`(无论有无任务簿,且不认领不落盘);prompt 在而任务簿缺 → `LEGACY`(行为与今日逐字节等价);其余分支不动。`prepare/success/failure` 的 taskGate 壳保持原样。l4-stock.js 已有 `['BLOCKED','WAIT']` 分支直接返回错误(l4-stock.js:136 附近),零改动接住。
- 纵深(指令级,可选):l4-card 派发 prompt 文案加一句「任务包文件读不到 → 直接返回 error,禁止用其它输入拼凑决策卡」。结构门(C1a/C1b)是主防线,这句只防"手动绕过一切闸口直接派 agent"的场景。

### C2 prompts 迁出长命令壳,自愈化

`.claude/workflows/scan-market.js` L4-prep 段(≈:356–368 的 `l4-prep` bash + :395 的 `l4-tasks-init` gate):

- **从 l4-prep 长命令里删掉末尾的 `l4_card prompts`**;长命令只剩 `shared; (pledge)& (seats)& (calendar)& (consensus)& wait`。~~每个生产者包 `timeout 600`~~ **已否决(2026-08-10 前提核准)**:macOS 无 GNU `timeout`,`( timeout 600 cmd || true )` 会把「timeout 命令不存在」吞成「生产者悄悄没跑」——比没有超时更坏;且四生产者历史实测 <30s,真正的爆炸半径已由「prompts 迁出 + init 自愈重建」封住(挂死的生产者只损失自己的 advisory 产物,全部 presence-gated)。
- **prompts 挪进 `l4-tasks-init` 同一壳**,命令变为:
  `l4_card prompts <date> && l4_tasks init <date>`
  短壳(≈10s)几乎不可能被后台化回收;且因 prompts 幂等(3.49s 实测),**哪怕 l4-prep 壳整个失败,这里也会自愈重建**。init 的 C1a 验证紧随其后 —— 生产与验证在同一原子壳里,中间无缝隙。
- 壳数**不变**(0 新增 agent);`streaming_l4=false` 的 legacy 回滚路径同理:prompts 前置到 GATE3 壳命令头部。
- dispatch-plan 不动(它读 finalists,与 prompts 无依赖,现状正确)。

### C3 intel 同日断点续传(crash-resume,用户已裁定)

**信号生产**(python 侧,搭已有 preflight 壳,零新增 agent):

- `l4_tasks preflight` 返回体新增可选字段 `intel_resume: bool`。判定(全部确定性、只读):
  1. `scan_dir/_l4_intel_<code>.md` 存在、非空、**非 `.rejected.md`**;
  2. `scan_dir/_l4_intel_status_<code>.json` 存在且 `acquisition=="FULL"` 且 `availability_for_card=="INTEL"` 且 `error_class` 为空;
  3. 状态文件与稿件 mtime 距当前 **≤24h**(墙钟)——目录按 analysis_date 隔离已挡住普通跨日,这条额外挡「隔了几天重放同一历史扫描日」时吃到陈稿(那种场景必须重盲搜或明知故犯地手动保留);
- l4-stock.js 的 `TASK_ACTION` schema 加可选 `intel_resume` 字段;intel 腿改为:
  `if (intelOn && !taskPreflight.intel_resume) → intelLeg()`,否则跳过 intelLeg + intel-guard + intel-status 三步(它们的产物已在盘上),log 一行 `🕵️ intel ♻ 同日续传(稿+status 验证通过,盲搜跳过)`。
- **披露不伪装**:续传时向 `_l4_intel_status_<code>.json` 追加 `"resumed": true`(由 preflight 顺手写,或省略——稿件本身未变,报告读到的 intel 三正交字段与首跑一致;推荐加字段,T1/报告侧可见这是续传稿)。
- **语义边界**(写死在代码注释 + 本文档):
  - 仅同一 analysis_date;跨日重扫必然新目录 → 条件天然不成立;
  - guard 拒稿(`.rejected.md`)不续传 → 重盲搜;
  - 强制重搜杆:删稿或删 status 文件即可(运维一行,无需新 flag);
  - **与 R5 的关系**:R5 退役的是**跨日卡 TTL 复用**(评级冻结+新闻冻结病);本件与任务簿对卡的同日 SKIP(`VERIFIED_SUCCESS`,hash 三验)完全同族——只是把同一套 crash-resume 语义补齐到 intel 腿。用户 2026-08-10 裁定接受。
- 收益:仅在**事故重派**时生效(正常单轮零差异,逐字节 parity);本次场景可省 22 次盲搜 ≈$15 + 相应壳与墙钟。

### C4 卫生件(小,随波顺手)

1. **limit-killed transcript 标注**:usage_harvest 对无 model/role 行的 transcript(本次 39 份 $0.0 `(未标注)`)按父 workflow 上下文回填 role 或标 `limit_killed`,别再让 usage_reconcile 报 `unknown_agent_type` 噪音。
2. **l4_watch 双挂防呆**:游标文件加 pid+心跳;第二个 `--watch` 启动发现活 watcher → 报错退出(本次双 Monitor 重播 4 条事件、多耗 4 次主会话唤醒)。
3. **运维注**(写进 STAGES.md 运维细节节):`l4_tasks preflight` 是**认领**不是只读探针——人工查状态用 `l4_tasks stats <date>` 或直接读 `_l4_tasks.json`;误用 preflight 会抢锁置 RUNNING(本次 600276 被我抢锁一次,靠 `failure --error-class TIMEOUT` 释放)。

---

## 4. 改动点清单

| 文件 | 函数/段 | 改动 | 行为变化 |
|---|---|---|---|
| `autoresearch/scan/l4_tasks.py` | `initialize()`(:178) | 返回前验 prompt MISSING 计数 | ≥1 → `ok:false`+missing 清单,不落盘 |
| 同上 | `preflight` 分支(:277–347) | 认领前验 prompt 文件在且非空 | 缺 → `action:BLOCK, reason:PROMPT_MISSING`,不认领 |
| 同上 | `preflight` 返回体 | 加 `intel_resume` 判定(C3 三条件) | 可选字段,LEGACY/缺稿时恒 false |
| `.claude/workflows/scan-market.js` | `l4-prep` bash(≈:356) | 删末尾 prompts;生产者包 `timeout 600` | 长壳只剩生产者 |
| 同上 | `l4-tasks-init` gate(≈:395) | 命令改 `prompts && init` | 短壳自愈重建 prompts + 原子验证 |
| 同上 | legacy GATE3 分支 | prompts 前置到 GATE3 命令 | streaming=false 同保护 |
| `.claude/workflows/l4-stock.js` | `TASK_ACTION` schema + intel 腿(≈:104,:150–210) | `intel_resume` 消费;跳过三步 + log | 事故重派不重盲搜 |
| `autoresearch/trace/usage_harvest.py` | role 归因 | limit-killed 标注 | 消 `(未标注)` |
| `autoresearch/scan/l4_watch.py` | 游标 | pid 锁 | 拒双挂 |
| `.claude/skills/scan-market/STAGES.md` | 运维细节 | preflight=认领 运维注 | 文档 |
| `tests/` | 新增 | 见 §5 探针 | — |

**明确不改**:`l4_card prompts` 本体、intel prompt/查询 cap、rubric/评级/主尺、派发模式、`AGENT_DEFAULTS`、任何 effort。

## 5. 验收(下次真实扫描 = 活体验收;家训:绿灯不等于有灯)

**A. 事故注入(实施波必做,单测 + 真目录演练)**:
1. 删任一 `_l4_prompt_*.md` → `l4_tasks init` 必须 `ok:false` 且指名缺哪票;workflow 层面 gate 必须 throw(派发前死,零 LLM 已花)。
2. 只走 SENTINEL_PINNED(无任务簿)删 prompt → `preflight` 必须 BLOCK,l4-stock 返回 error 不出卡。
3. **变异探针**:注释掉 init 的 MISSING 检查 → 对应测试必须红(防"永不变红的绿灯")。
4. 同日重派已 SUCCEEDED intel 的票 → intelLeg 零派发,log 出 ♻ 行;删稿再派 → 正常盲搜(续传条件不成立)。
5. `.rejected.md` 场景 → 不续传,重盲搜。

**B. 成本回归线(连续 3 个净 run)**:总成本 ≤ **$35**、加权 ≤ **8M**、subagents ≤ **130**、主会话 ≤ **$6 / ≤50 消息** —— 即「和之前一样」的量化兑现。事故日允许超,但超额上限 = 未完成卡的边际成本(不再是整轮 ×N)。

**C. parity 验证**:正常单轮(无重派)下,C3 代码路径不触发,产物逐字节与现状一致(intel 稿、卡、报告);C1/C2 只在缺产物时改变行为。

## 6. 不做清单(防将来误读为"还有余粮可省")

- ❌ 恢复任何**跨日**复用(R5 原判;卡、intel、slim 一律不跨日)。
- ❌ 降 L3/L4/strategist effort、砍 intel `max_queries`、减 finalist 数 —— 触「性能开关不拥有评级」铁律。
- ❌ 改派发模式(一次性全派是 fb_20260714_003 用户原则)。
- ❌ CP5 digest / intel-status+guard 壳合并:净轮壳合计仅 $5.8,合并省 <$1,却要动直播契约与 prompts 链——省得少动得多。
- ❌ 拿 07-24「50 agent/5.49M」当目标裁员 agent 数(旧口径,见 §1.4)。
- ❌ 主会话专项优化:净日主会话本来就 $4-5,没有独立病灶。

## 7. 风险与回滚杆

| 风险 | 缓解 | 回滚杆 |
|---|---|---|
| C1 硬门误拦(prompts 真身有 bug 写不齐 12 份) | init 报 missing 清单,直接指认缺哪票;prompts 幂等可反复重跑 | 临时:手工 `l4_card prompts` 后重 init;代码杆:init 加 `--allow-missing-prompts`(默认关,仅救火) |
| C3 续传吃到坏稿(status 说 FULL 但稿内容病态) | 续传条件含 guard 结果与 error_class;guard 拒稿不续传 | 删稿/删 status 即强制重搜;或 preflight 恒返 `intel_resume:false` 的一行回滚 |
| C2 改壳命令引入语法错(js 模板串) | 家训:`node --check` 对 workflow js 是假绿灯 → 既有 `tests/test_workflow_js_syntax.py`(AsyncFunction 探针)+ 真跑一次哨兵档冒烟 | git revert 单 commit |

## 8. 开放问题

1. `intel_resume` 要不要同时覆盖「同日 slim」?——slim 已由 `prepare` 壳内 K 槽信号量管理且有 hash 复用,疑似已闭环;实施波先 grep `prepare` 的 slim 跳过语义再定,不在本设计内扩权。
2. 39 份 `(未标注)` 的 role 回填口径(按 workflow journal 反查 vs 标 `limit_killed` 一刀切)——实施时看 harvest 现有归因链哪种改动小。
3. 本次 GATE4 的 3 条 fail(退役符号墓碑标记)与本设计无关,另行处理。

## 附录:原始读数指针

- 本次:`context/scan/2026-08-07/_token_usage.json`(275 rows)、`reports/scan/20260810_0128/token_usage.md`
- 基线:`context/scan/2026-0{7-29,7-31,8-04,8-06}/_token_usage.json`
- 三轮分桶脚本:按 row.path 的 transcript mtime 分桶(r1<00:22 / r2<00:55 / r3≥00:55,2026-08-10 本地时区)
- 盲卡留证:`context/scan/2026-08-07/_blind_cards_20260810/`(4 张,勿删——它们是 R1 的物证)
- 事故任务簿快照:`_l4_tasks.blindrun.json`(r1)、`_l4_tasks.limitrun.json`(r2)

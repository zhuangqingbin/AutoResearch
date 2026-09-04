# Token 使用效率优化:开发会话线 × 扫描线 两线设计

> 日期:2026-09-04
> 状态:**Wave 1(D0 + S1 + S5 代码面)已实施**,其余未实施 —— 见 §10。实施计划:`docs/superpowers/plans/2026-09-04-token-efficiency-metering-wave1.md`
> 证据原件:`docs/research/2026-09-04-token-efficiency/`(两个探针脚本 + 两份 30 天读数;脚本是一次性探针,D0 收编时重写)
> 前序:此前三波 token 优化(07-04 六件套、07-08 P0、08-10 回归定案)与 09-03 根因取证(OMC SubagentStop 尾环 + trace-control 壳三倍派发)全部只打扫描 run;本稿首次把开发会话当靶子
> 引擎隔离:本稿只量 Claude 引擎(`~/.claude/projects/<proj>/` 的主会话与 subagent transcript);Codex 侧 09-03 已定性为「上下文累积、无可一键修的 bug」,本波不碰。Codex 会话不得执行本稿的 Claude transcript 计量 CLI,更不得借道读写 `context_claude/` / `reports_claude/`

## 0. 决策摘要

- **靶子**:两条线都做(用户裁 A)。30 天账:开发会话 580M 加权(58.5%)、扫描 run 410M(41.4%)。
- **开发线**:模型能力不动,用项目本地 `CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000` 把自动压缩尺收进 200k;阶段交接落盘,新阶段开新会话。全局 `~/.claude/settings.json` 不改。
- **扫描线**:先留在 workflow.js 做低风险减法:提交 09-03 两修 → `gp-shell` 自定义壳探针与迁移 → 必要时壳合并 → 接现有 `budgets` 预算告警。情报站预算 S4 涉及研究质量,拆成后续独立行为变更,不进首份实施计划。
- **架构路线**:当前选渐进路线 A;若 S2 后壳合计仍 >1.5M 或壳占 run >20%,自动升级路线 B(Workflow 只派判断 agent,确定性命令交给非 LLM runner)。全 Python 编排仍留 P3。
- **计量先行但不双写真相**:`usage_harvest` / `UsageLedger` 是单 run 权威,`usage_panorama`(D0)只是跨会话聚合读模型,`budget.observe_run` 是唯一红黄绿裁决面。
- **质量优先**:token、成本、墙钟是优化目标;GATE1/2/4、证据覆盖、时效、评级边界与失败率是约束。任何节省都不得靠静默减少公告/T0/负面证据获得。
- **前提**:退出并重启 Claude Code 进程。09-03 关掉的 OMC 插件在 09-03 14:32 起的进程里仍活着(hook 注入 + MCP server 子进程),`/clear` 不重载插件。

## 1. 读数(2026-08-05 → 09-04,52 场会话,按 message.id 去重)

加权口径同 `autoresearch/trace/usage_harvest.py`:生输入 ×1、cache 读 ×0.1、5m cache 写 ×1.25、1h cache 写 ×2.0;输出另计。本账户请求用 1h cache TTL,所以主会话新内容全按 ×2.0 写。**这是一把按 API prompt-cache 价格倍率构造的缓存成本代理尺,不是 Anthropic 未公开的订阅 5 小时额度公式**;后文不得把它称为订阅额度真值。

### 1.1 两条线的分账

| 消费者 | 会话数 | 轮次 | 主会话加权 | subagent 加权 | 合计 | 占比 |
|---|---:|---:|---:|---:|---:|---:|
| 开发会话(SDD 实施 / brainstorm) | 21 | 4,220 | 236M | 344M | 580M | 58.5% |
| 扫描 run | 18 | 1,830 | 102M | 309M | 410M | 41.4% |
| 小会话 | 13 | 53 | 1M | 0.4M | 1.6M | 0.2% |

本次探索性分类规则:subagent ≥ 60 个且每 agent 调用中位 ≤ 12 次 = 扫描;其余 ≥ 30 轮 = 开发。08-24 那场(207 轮、96 subagent、20.3M)是扫描跑在开发会话里,被归为扫描;这正是 D2 要禁的形态。**该启发式只解释冻结的历史基线,不得用于上线后验收**:S1/S2 正会主动减少 subagent 数,继续用 `≥60` 会把成功优化后的扫描错分成开发。上线后扫描只认 capsule/session 显式绑定。

### 1.2 开发会话:钱在「长上下文 × 多轮」

- 主会话 85% 的加权花在 14 场中位上下文 > 300k 的会话;中位 460k–607k,峰值 770k–999k。
- 主会话总账:cache 读重读占 67%,1h cache 写占 33%(55.6M 新 token × 2.0)。
- 三个最大的实施 subagent(`general-purpose` · sonnet):

| 会话 | 描述 | 调用 | ctx 中位 | ctx 峰 | 加权 |
|---|---|---:|---:|---:|---:|
| 08-13 b34d9764 | 实施 T9-T12 批A 收尾四件 | 421 | 518k | 757k | 23.6M |
| 09-01 c5144662 | Batch D: 走湖+拆分+瘦身 | 357 | 579k | 934k | 21.1M |
| 08-20 f8db6873 | Task2.5a 裁定记账+BCD落地 | 241 | 318k | 375k | 7.5M |

  一个实施 agent = 2–3 次全扫。它们继承了 1m 窗,从不压缩;每场前 5 个 agent 占该场 subagent 账 41–65%。
- 开发会话主上下文的填充物:Bash 结果 71%(cat / sed 读文件、测试输出)、Read 24%;tool_use 输入侧 Bash 54%、Write 27%。
- 每场首轮固定基线 51k → 66k(+30%),其中 MEMORY.md ≈ 8.6k token(29.7KB / 113 条)、skill + agent 清单 ≈ 6k、CLAUDE.md ≈ 1.4k,其余是 harness 系统提示与工具 schema。

### 1.3 扫描 run:钱在壳

09-01 run(session 95192fa5,316 个 subagent,18.73M):

| agentType | model | n | 调用 | 加权 | 占比 | 单个 | 首轮 ctx 中位 | ctx 峰 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| general-purpose 壳 | sonnet | 283 | 852 | 14.89M | 79.5% | 53k | 49k | 65k |
| l4-intel | sonnet · max | 11 | 179 | 1.95M | 10.4% | 177k | 23k | 112k |
| l4-card | opus · xhigh | 11 | 58 | 0.87M | 4.7% | 79k | 30k | 71k |
| sector-brief | opus | 8 | 96 | 0.49M | 2.6% | 61k | 22k | 32k |
| l3-rank | opus | 2 | 24 | 0.44M | 2.3% | 219k | 26k | 130k |
| macro-brief | opus | 1 | 13 | 0.09M | 0.5% | 94k | 23k | 40k |

- 壳的钱几乎全是**首轮 49k 固定上下文写进 cache**(整套工具 schema + 114 条 skill 清单 + agent 清单 + CLAUDE.md);Claude Code 官方文档说明普通自定义 subagent 仍会加载 CLAUDE.md 与 memory,所以不能再用首轮数值反推「没注入 MEMORY」。自定义业务 agent 带 19KB 正文而首轮约 23k,只证明自定义 agent 的系统/工具底座比 general-purpose 小,具体差额由 S2 A/B 探针实测。
- 09-03 未提交的 RELAY_ROLES 修(`.claude/workflows/l4-stock.js:184`)把每条确定性命令的 3 个 subagent 收成 1 个,一只票 18 → 8 次调用;memory 投影 09-01 run 18.7M → 7.1M。壳数降到约 1/3,但每个壳 49k 的地板还在。

### 1.4 疑似 OMC 尾环的探索性占比

当前启发式把「最后一条 user 之后连续 assistant ≥2」的尾部调用计为 6,649 次、60.8M 加权 = subagent 账的 9.3%(扫描 10.6%、开发 8.1%)。比 09-03 按「上下文占比」量出的 25–47% 小,因为 cache 读按 ×0.1 计。它是**疑似尾环**而非 OMC 因果证明;正式 D0 必须结合 hook marker / 插件启停 cohort 归因,启发式命中单列 `suspected_tail`,不得直接命名为 `omc_waste`。

### 1.5 一条改变排序的算式

若一个 token 在第 t 轮进入上下文、直到第 T 轮都未被 compact 掉,代理成本 = 2.0(写)+ 0.1 × (T − t)(之后每轮重读)。T − t = 300 时 = 32 倍。它是用于排序的上界近似,不等于真实 cache 分段/压缩行为。推论:

1. 「上下文长 × 轮数多」是主犯;压缩 / 换会话直接砍 T − t。
2. 固定基线(MEMORY.md、skill 清单、插件)按此算式全月 < 2%,只算卫生不算杆。
3. 例外是**每个壳都要重新写一遍的 49k**:壳数 × 49k × 1.25–2.0,这一项在扫描 run 里是 79.5%。

## 2. 目标函数、量尺与验收

### 2.1 多目标而非单 token 目标

本稿不再把未知的「订阅 5 小时额度」当成可观测目标。每次读数固定输出以下向量,不合成一个假精确总分:

| 字段 | 定义 | 用途 |
|---|---|---|
| `weighted_input_proxy` | `input + 1.25×cache_create_5m + 2.0×cache_create_1h + 0.1×cache_read` | 与历史 18.7M / 10.2M 同尺比较 |
| `output_tokens` | 去重后的原始输出 token | 防止只优化输入却放大输出 |
| `estimated_usd` | 按模型、speed 与生效日价格估算;缺价格即 `null` | 比较模型价差,不冒充订阅账单 |
| `model_mix` | 按模型家族列 input / cache / output / 调用;`_MODEL_MULT` 只作排序维度 | 防止把模型倍率混进 token 代理尺 |
| `wall_time_s` | run / stage / agent 的实测墙钟 | 防止省 token 但显著拖慢 |
| `metric_version` | 公式、价格生效日、解析器 schema 的版本 | 保证跨 run 可比 |

优化方向是降低前三项与墙钟;以下质量条件是硬约束,优先级高于成本:

1. `gate1`、`gate2`、`gate4` 与 assemble `self_review` 全过。
2. 在同配置、同证据可得性的可比样本中,T0 / 公告 / 负面证据、启用时的六面情报与引用来源契约不降级,证据时效不变差。
3. 不因证据缺失造成 BUY / OW / WATCH / AVOID / SELL 评级边界翻转。
4. `DEGRADED`、`REJECTED`、失败、重试与遗失 transcript 比例不升。
5. 0 买日仍是合法结果,不得为满足成本或产出率放宽研究门。
6. 投资口径不变:仍以超短 1–2 日、`gap_c1_o2`(T+1 收买→T+2 开卖)为主尺,不把 swing 收益混入;同输入 replay 的候选/基线评级与风险边界必须一致。真实收益只作 D+2 滞后监控,市场状态与样本未对齐前不拿单次盈亏证明工程优化优劣。

### 2.2 样本与可比性

- **冻结基线**:记录真实 transcript 时间戳范围、session/run 清单、git SHA、配置快照 hash、finalist 数、每票情报开关、价格表日期、市场状态标签、时区与排除规则。不得用文件 `mtime` 代替事件时间。
- **显式分组**:`baseline` 与 `candidate` cohort 由清单或 CLI 显式传入;扫描 run 以 capsule/ledger 的 `harness_session_ref` 认领,开发会话以 session 清单认领。§1.1 启发式只复现历史,不参与晋升。
- **归一读数**:同时报告每 run、每个 finalist、每个壳、每个业务 agent 的成本,并列 finalist 数与 agent 数。18.7M 对 10.2M 只能作为两个样本,不能单点定案。
- **两级验收**:一次 smoke run 只证明功能与计量链可用;成本晋升沿用 `autoresearch.scan.budget` 的治理,至少 10 次互不重复、配置可比的真实扫描后才看 p50 / p90。
- **窗口新鲜度**:「新鲜 5 小时窗」只作运行排程条件,不进入 token 公式,也不能补偿样本不足。

### 2.3 成本与行为守卫

| # | 守卫 | 基线 | 验收 |
|---|---|---|---|
| E1 | 单 run `weighted_input_proxy` | 18.7M(09-01)/10.2M(09-02) | `UNMEASURED`=🔴;`>7M`=🔴/DEGRADED;`>5M 且 ≤7M`=🟡;`≤5M`=🟢。只告警,不截断研究、不阻断发布 |
| E2 | 成熟 cohort | 尚无可比候选组 | 至少 10 次真实扫描,p50 `≤5M` 且 p90 `≤7M`;同时满足全部即时质量约束,D+2 `gap_c1_o2` 另作分层滞后读数 |
| E3 | 扫描壳合计 | 14.9M / 283 个 | `≤1.5M` 且占 run `≤20%`;否则从路线 A 升级路线 B |
| E4 | `gp-shell` 首轮上下文 | general-purpose p50 约 49k | 相对同命令 A/B 至少下降 60%,且候选 p50 `≤20k` |
| E5 | 开发主会话上下文 | 中位 460k–607k | 200k 配置 cohort 的 p90 峰值 `≤250k`;超线必须能看到 compact boundary 或明确异常 |
| E6 | 单个实施 subagent | 最大 23.6M / 421 次 | 100 次或 2.5M 告警;到 120 次或 3M 在原子步骤边界 checkpoint + handoff,不得中途强杀 |
| E7 | 疑似尾环 | 6,649 次 / 60.8M | 仅作观察;关闭插件 + 重启 cohort 相对基线显著下降。没有 hook marker / 对照组不得宣称归零或归因 OMC |
| E8 | 开发线月总量 | 580M | 观察项,期望下降 50%;因任务量不可归一,不设发布硬门 |

S1 + S2 后约 6.5M 的数字只保留为容量规划,不是验收证据。5M 是成熟 cohort 的目标,不再依赖本波不实施的 S4 来凑数。

## 3. D0 计量先行:`usage_panorama`

### 3.1 权威边界与数据流

```text
Claude transcript ──adapter──> TranscriptStats / UsageRecord
                                      │
                         usage_harvest / UsageLedger   单 run 权威
                                      │
                         budget.observe_run            唯一红黄绿裁决面
                                      │
                         usage_panorama                跨会话聚合读模型
```

- `usage_panorama` 不另造 token 解析器,不回写历史 transcript,不拥有 run 状态。
- 在 `autoresearch/trace/transcripts/base.py` 增加 `TranscriptStats`(或语义等价的稳定类型),由 Claude adapter 一次解析并提供:去重 usage、逐轮上下文、工具输入/结果组成、首轮、真实时间范围、compact boundary、最后 user 后的 assistant 尾段、重试/失败与 meta。`UsageRecord` 继续承载稳定的成本子集。
- `usage_harvest` / `UsageLedger` 消费同一 adapter 结果;`usage_panorama` 只聚合这些结果。禁止为了 D0 再 raw-read JSONL 写第二套流式去重逻辑。
- `budget.observe_run` 增加 `weighted_input_proxy` 与预算判断,继续是唯一的 `SUCCEEDED` / `DEGRADED` 生产者;`usage_panorama` 只能展示该判断,不能自行改色。

### 3.2 CLI、选择器与 provenance

落点:`autoresearch/trace/usage_panorama.py`。正式 CLI 必须支持显式选择,不能只有 `--since`:

```bash
python -m autoresearch.trace.usage_panorama \
  --engine claude \
  --from 2026-08-05T00:00:00+08:00 \
  --to 2026-09-05T00:00:00+08:00 \
  --cohort baseline \
  --session <session-id> \
  --run-id <run-id> \
  [--write]
```

- `--session` / `--run-id` 可重复;显式清单与时间范围取交集。至少提供 session、run 或完整的 from/to 范围之一,否则 CLI 拒绝全盘扫描。`--cohort baseline|candidate` 是报告标签,不得通过 subagent 数猜。
- `--run-id` 必须经 run capsule/ledger 解析到 `harness_session_ref`;未绑定时输出 `UNCLAIMED`,不得静默退回启发式。
- 输入为 `~/.claude/projects/<project-slug>/` 的主 transcript、该 session 的 `subagents/**/agent-*.jsonl` 与 `.meta.json`;事件时间来自 transcript 字段,没有可靠时间则标 `UNMEASURED_TIME`。
- 每份报告写入:输入 session/run 清单及其 hash、时间边界、项目 slug、probe git SHA、配置 hash、解析/量尺版本、价格生效日、时区、排除项及理由。
- 这是 Claude 专属读数。Codex 侧本波不接入;Codex 会话不得为了跑 D0 切换引擎并写 `reports_claude/`。

### 3.3 产物契约

- `autoresearch/contracts/artifacts.py` 新增 root `metering`,解析到 `$RPT/_metering`;沿用已存在的 stage `observe`,不新增阶段词汇。
- 登记两个 gated 产物:`panorama_md`=`panorama_*.md`,`panorama_json`=`panorama_*.json`;实际文件名使用 UTC 时间戳且不覆盖旧读数,仅 `--write` 时原子写入。JSON 是机器真相,Markdown 只渲染 JSON。
- 不挂单次 run 的 `report` / `staging` root,避免全局回放仪器污染现场;不把它列入扫描 manifest 的必需产物。
- JSON 至少包含:metric/provenance、逐 session 与逐 agent 表、cohort 汇总、上下文分位、工具组成、compact/tail 统计、预算 observation 引用、E1–E8 与质量守卫读数。
- 隐私:描述字段只存角色与稳定 label;prompt、命令正文、tool result 正文一律不落 panorama。写入文件权限 `0600`,产物仅本机保留。

### 3.4 测试

1. 合成 transcript 覆盖流式重复 `message.id`、5m/1h cache、缺 meta、缺时间、compact boundary、失败/重试、最后 user 尾段。
2. adapter contract 测试证明 `usage_harvest` 与 panorama 对同一夹具的 usage 完全相等。
3. CLI 测试覆盖 session/run/time 交集、未知 run=`UNCLAIMED`、无可靠时间=`UNMEASURED_TIME`、engine 隔离与 `--write` gate。
4. artifact registry / drift / 权限 / JSON→Markdown 一致性测试。
5. 预算边界测试覆盖 `UNMEASURED`、5M、7M 与 7M+1;证明告警永远 `truncated=false`。
6. 尾环只命名 `suspected_tail`;没有 marker 的夹具不得输出 OMC 因果结论。

## 4. 开发会话线

### D1 项目本地 200k 自动压缩窗

- 在不入库的 `.claude/settings.local.json` 的 `env` 对象加入 `"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "200000"`;保留文件中其他键与当前模型配置,不编辑全局 `~/.claude/settings.json`,也不靠移除 `[1m]` 改模型。
- 改完必须退出并重启 Claude Code 进程;`/clear` 不会重载插件或环境。先确认新进程环境命中,再启动候选 cohort。
- Claude Code 官方说明 subagent 会自动压缩,transcript 会记录 `compact_boundary.preTokens`;D0 用 boundary 与峰值验证实际行为,不再从模型后缀猜继承关系。
- H1 探针:同一小任务各跑一个主会话与 subagent,记录首个 compact boundary、峰值与是否完成;通过标准是有可靠 boundary 或明确解释为何任务未达到阈值。
- 回滚:删除 local settings 中该环境变量并重启;不动模型与全局用户设置。

### D2 Claude 专属会话纪律

新增 `.claude/CLAUDE.md` 的短节,不改根 `CLAUDE.md`:根文档同时被 Codex 读取,把 Claude harness 的窗口与工作流纪律写进去会污染另一引擎。

1. **按可独立验收的阶段切会话**:brainstorm、计划、实施波、终审各自可切;很小且强耦合的步骤可留在同一会话。扫描 run 必须独立于开发会话。
2. **按内聚任务派 agent**:一个 agent 可做一组强相关、能一起验证的改动,不机械追求「一文件一 agent」。100 次调用或 2.5M 先告警;到 120 次或 3M,完成当前原子步骤后写 checkpoint/handoff 并退出,不得在写文件或测试中途强杀。
3. **交接契约**:至少写明目标、已改文件、未完成项、验证命令与结果、已知风险、工作树状态。新 agent 只从这份交接和必要文件恢复。
4. **目标读取**:先 `rg` / 范围读取;规范、契约或本身需要整体语义的文件允许完整读取,不设「>200 行禁止」这种会损害正确性的硬尺。
5. **输出控量**:测试必须保住退出码,长输出落文件或只取相关段;交接里引用结果,不把全量日志反复塞回上下文。
6. **源码控制**:实现任务必须记录改动文件与 diff 摘要;测试输出不混成源码修改。插件/agent/settings 变更后必须重启进程验证。

### D3 项目本地卫生 A/B(低优先级)

- 本波只测项目本地可逆项,例如压缩项目级 memory 索引与移除失效条目;每项先记首轮基线,改后跑同一任务 A/B。
- 不自动移动或删除 `~/.claude/skills`、全局插件、用户 memory;这些属于跨项目用户状态,需要另行授权。
- stock-deep-analyzer 等插件只允许在候选 cohort 临时关闭并重启做 A/B,不得由本设计永久卸载。
- 若首轮或整场代理成本改善 <5%,不实施卫生项;它不是 5M 目标的依赖。

## 5. 扫描线

### S1 固化 09-03 两修

- 内容:`l4-stock.js` RELAY_ROLES(中继壳不发边界事件、走 `rawAgent`)+ `capsule.begin_run` 自绑 `CLAUDE_CODE_SESSION_ID`(`harness_session_ref`)+ 对应测试。提交前重跑相关测试与全量测试。
- `.claude/skills/scan-market/pinned.jsonc` 是用户持仓输入,必须单独提交或由用户自理;不得混进 S1 代码 commit。
- 活体验收:下一次 smoke run 的 UsageLedger / `token_usage.md` 非 `UNMEASURED`,一只票 agent 调用 18→8,`agents/index.json` 仍只记业务 agent,gate1/2/4 全过。
- S1 不是 7M 晋升证据;只证明边界事件与 session 归属修复有效。

### S2 `gp-shell` 成本实验,不冒充安全边界

`gp-shell` 仍是 LLM agent;`tools: Bash` 只缩工具 schema,不能保证它不改写命令或不执行 `pkill`。安全与字节完整性靠探针、校验和确定性 runner,不靠 prompt 声明。

- 新增 `.claude/agents/gp-shell.md`:`model: sonnet`,`effort: low`,`tools: Bash`,`maxTurns: 2`。正文只放:执行单条给定命令、不自行拼接管道/重定向、不 kill/pkill、返回退出码和受限 stdout/stderr 摘要。结构化场景返回 schema,但原始命令与真实输出仍由 `exec_capture` 留证。
- 一次性迁移仓内 **14 处** `agentType: 'general-purpose'`:`l4-stock.js` 6 处、`scan-market.js` 6 处、`dossier-init.js` 2 处。全部保留 `AG('gp_shell'|'gp_shell_json')` 的显式 model/effort;finalize 兜底补齐默认值。
- `usage_reconcile.AGENTTYPE_ROLES` 把 `gp-shell` 认作壳类,`general-purpose` 保留用于历史 run;核对 capsule 非业务角色集合与 agents index 不变量。
- 定义与 workflow 测试覆盖:frontmatter、仅 Bash、maxTurns、model/effort、14 处无遗漏、壳不进入业务 agent 索引。

**先探针后迁移**(重启后的新 session):

1. 用同一组无副作用命令分别派 general-purpose 与 gp-shell,验证自定义 `agentType`、meta/transcript 路径与 schema。
2. 覆盖空格、引号、Unicode、长参数、非零退出、stdout/stderr、末行 JSON;将原命令 bytes/hash 与 `exec_capture` 记录比对。
3. 同时量首轮上下文:p50 相对 general-purpose 至少下降 60% 且 `≤20k`。
4. 任一命令发生变形、意外副作用、schema 不可靠或单次完整性失败,立即回滚 S2 并触发路线 B,不尝试用更强 prompt 掩盖。
5. 迁移后 smoke run 要求壳合计 `≤1.5M` 且 run 占比 `≤20%`;任一超线同样触发路线 B。

保持 Sonnet:已有 Haiku 壳错误处理长命令的事故证据。若 S2 通过,它只说明这组探针与样本下成本/完整性可接受,不把 Bash agent 宣称为安全沙箱。

### S3 路线 A 下的结构化壳合并

仅在 S2 通过命令完整性、但壳成本仍接近阈值时考虑;若已经触发路线 B,不再投入 S3。

- 相邻确定性步骤以结构化命令列表表达,每项含稳定 id、argv/command、cwd、timeout、fail policy 与预期产物;禁止拼成分号连接的巨型 shell 字符串。
- 每条命令单独走 `exec_capture`,独立记录开始/结束、退出码、stdout/stderr 摘要与 artifact hash,保持法证粒度。
- 默认 fail-fast;只有契约明确标为 `continue_on_error` 才继续。步骤必须可重入或在执行前检测已完成产物。
- 合并不得跨越 LLM 业务 agent、gate、发布或 capsule 边界。
- 预期去掉约 1/3 壳是规划值;是否保留只看实测与质量守卫。

### S4 情报预算:拆为后续独立行为变更

本稿**不修改** `l4_intel.max_queries`,原因:

- `autoresearch/trace/web_budget.py` 明示 `OBSERVED_ONLY`:现有计数是观测器,不是硬拦截器。
- `user_config.py` 虽白名单允许 `l4_intel.max_queries`,但当前缺少该键的类型/范围校验。
- l4-intel 的六面契约要求逐面检索;默认 cap 统计 search query,文档又提 WebFetch,预算单位不一致。
- 压低 intel 可能把缺口转移给 Opus `l4-card` fallback 搜索,总成本未必下降,反而可能降低公告/T0/负面证据覆盖。

后续 spec 必须先定义统一的 search/fetch 单位与 dispatcher/hook 硬执行点,再做配对 shadow A/B。最低样本:≥10 张卡、跨 ≥3 个扫描日;T0/六面/来源契约 100%,`DEGRADED`/`REJECTED` 不升,不因缺证发生评级边界翻转,且 token/美元显著下降。未满足这些条件保持 cap 20。

### S5 接入现有 run 预算控制面

- 在已有复数配置块 `budgets` 增加并白名单/校验:
  - `run_weighted_warn: 7_000_000`
  - `run_weighted_target: 5_000_000`
- `usage_harvest` 先生成 `_token_usage.json` / UsageLedger;assemble 后的 `post_run` 再把它与 timing 传给 `budget.observe_run`。不得新建第二个预算模块或 `budget.run_weighted_max` 单数配置。
- `observe_run` 按 E1 生成唯一的 `budget_band=RED|YELLOW|GREEN`,始终 `truncated=false`:缺计量或 `>7M` 写 warning 并使 budget StageResult `DEGRADED`;`>5M 且 ≤7M` 只写 advisory,不单独降级;`≤5M` 不产生 token 告警。既有 cache/美元/墙钟 warning 仍按原语义决定 StageResult。`UNMEASURED` 绝不能渲染成 0 或绿。
- CP7 位于 assemble 之后,因此不能承诺在尚未计量时修改 brief 首行。post_run 只更新 brief 的托管健康区块,使用原子替换,随后刷新 manifest/index/artifact hash;找不到托管区块则 DEGRADED,不做模糊文本替换。
- `evaluate_history` 延续 `min_real_scans=10`,增加 `weighted_input_proxy` 的 p50/p90 判定。一次 smoke run 只验功能,不触发成本晋升。
- 测试覆盖配置白名单/类型/范围、5M/7M 边界、UNMEASURED、原子更新失败、manifest 刷新、重复 post_run 幂等与「只告警不阻断」。

## 6. 架构路线与升级条件

| 路线 | 形态 | 优点 | 代价 | 本稿裁定 |
|---|---|---|---|---|
| A 渐进 Workflow | 业务 agent 与确定性壳都由 workflow.js 编排 | 改动小,可先吃 RELAY 与 gp-shell 收益 | 壳仍是 LLM,安全/成本地板存在 | 当前首选;必须通过 S2 探针 |
| B 混合 runner | Workflow 只派 macro/sector/L3/L4 等判断 agent;Python/Node runner 执行确定性命令并 `exec_capture` | 去掉壳 LLM 地板,命令语义可测试 | 要设计 workflow↔runner 状态机与恢复契约 | S2 完整性失败,或壳 >1.5M / >20% 时自动升级 |
| C 全 Python 编排 | agent 调度、状态机、命令、恢复都由项目 runner 拥有 | 可观测与可控性最高 | 迁移面最大,回归风险最高 | P3,本波不做 |

路线 B 触发后先写一页增量 ADR/设计补丁,明确状态持久化、幂等、重试、取消、并发、transcript/session 归属与回滚,再写实施计划;不得一边跑生产扫描一边临时改编排。

## 7. 分阶段落地与晋升门

1. **冻结基线**:保存 session/run 清单、事件时间范围、配置/input hash、git SHA、finalist 数、市场状态标签、价格/metric 版本与排除规则。
2. **D0**:先扩 adapter/`TranscriptStats`,再实现 UsageLedger 复用、panorama 读模型、artifact contract 与测试。
3. **进程验证**:确认 09-03 已关闭的 OMC 插件仍为关闭状态,然后完全退出并重启;记录新进程启动时间与 hook marker,只用新 session 验 E7,不混历史会话。
4. **S1**:相关测试 + 全量测试通过后提交代码;`pinned.jsonc` 单独提交/用户自理。
5. **D1/D2**:写项目 local env 和 Claude 专属纪律,重启后跑 compact probe;不改全局 settings 或根 `CLAUDE.md`。
6. **S2 探针**:先验 H2–H5 与 H7;完整性失败直接路线 B。通过后迁移 14 处并跑相关/全量测试。
7. **S5**:把 E1/E2 接入现有 `budget.observe_run`;验证 post_run 托管区块与 manifest 原子刷新。
8. **一次 smoke scan**:独立 session,验证 session 归属、计量非 UNMEASURED、质量门、静态 14 处迁移无遗漏、动态壳类型、失败/重试与发布链。只给功能结论。
9. **成熟候选组**:收集至少 10 次可比真实扫描;E2 与质量门全过才宣布成本目标达成。
10. **后续选择**:路线 A 且只差壳冗余时评估 S3;命中升级条件则做路线 B。S4 另开行为变更 spec;Codex 文档瘦身不在本波。

每一步都独立 commit/可回滚;不把测量基础设施、行为变更、用户持仓数据塞进同一个提交。

## 8. 回滚与止损

| 件 | 回滚杆 | 数据处理 |
|---|---|---|
| D0 | 关闭 `--write`,移除 panorama CLI/registry 增量 | 已生成 `_metering` 文件留作只读证据或人工删除;不改历史 run |
| D1 | 删除 local env 键并重启 | 保留 before/after cohort 与 compact 读数 |
| D2 | revert `.claude/CLAUDE.md` 对应节 | handoff 文档不自动删除 |
| D3 | 恢复项目本地条目/插件开关并重启 | 不触碰全局 skills/memory |
| S1 | `git revert` 独立代码 commit | 不回滚独立的 `pinned.jsonc` 用户数据 |
| S2 | 14 处改回 `general-purpose`,停止使用定义并重启 | 任一命令完整性失败立即止损并升级路线 B |
| S3 | revert 结构化批次,恢复逐命令 `exec_capture` | 保留失败批次法证记录 |
| S4 | 本稿无变更;后续 spec 单独定义 | 默认保持 cap 20 |
| S5 | 删除两个 `budgets` 键和消费点,恢复旧 observation schema | 旧读数按 `metric_version` 保留,不得重解释 |

## 9. 已裁决事项

- **Q1 D3**:只做项目本地 A/B;不自动删除/移动全局 skills、plugins、memory。
- **Q2 预算**:7M 为单 run warn,5M 为成熟 cohort target;只告警不截断、不阻断发布。
- **Q3 agent 阈值**:100 次/2.5M 告警,120 次/3M 在原子边界 checkpoint + handoff。
- **Q4 Codex 文档瘦身**:本波不碰。
- **Q5 pinned 数据**:与 S1 代码 commit 分离。
- **Q6 S4**:拆为后续独立行为变更 spec,默认 cap 20。
- **Q7 架构**:先走 A;S2 命令完整性失败,或壳 >1.5M / >20%,升级 B。

本文至此无待裁实现占位。

## 10. 实施状态(2026-09-05 更新)

分支 `codex/token-efficiency-two-lines`。**只有软件面完成;成本目标一个都还没宣布达成**——
本稿的两道验收门都要 Claude 运行时证据,代码里量不出来。

| 件 | 状态 | 依据 |
|---|---|---|
| D0 `usage_panorama` | **已实施** | `TranscriptStats` 契约 + 显式 cohort 选择器 + `metering` 产物根;CLI 真跑过(见下) |
| S1 09-03 两修 | **已实施** | `RELAY_ROLES` 跳过中继边界事件;Claude 会话自绑 `session_ref` |
| S5 加权预算带 | **已实施(代码面)** | `budgets.run_weighted_warn/target` 进 `normalize_budgets` → `observe_run` → 报告托管区块 |
| D1/D2 会话纪律 | 未实施 | 改的是 Claude 进程配置,不在本波代码里 |
| S2 `gp-shell` 壳探针 | 未实施 | 需先验 H2–H5/H7;完整性失败即升级路线 B |
| S3 结构化壳合并 | 未启动 | 条件件,看 S2 读数 |
| S4 情报预算 | 不在本波 | Q6 已裁:另立行为变更 spec |

**两道仍然敞着的门(不要当已过)**

1. **一次真 smoke scan 没跑**。全部验证都是单测 + CLI 干跑,没有任何一次真实扫描证明
   session 归属、计量非 `UNMEASURED`、发布链在生产路径上成立。
2. **成熟 cohort 没有**。`min_real_scans=10` 的可比真跑一次都还没攒,E2 与质量门无从判定。

**实测读数(2026-09-05,`--from 2026-09-01 --to 2026-09-05`,只读不写)**

15 场会话全 `MEASURED`,加权输入 62.4M;cache 读 304.7M 对生输入 21.8K —— 单场最贵
23.2M(`c5144662`)。`95192fa5`/`9cb73699` 两场各 317/233 个 agent,合计 31.5M,是壳成本
仍在的直接证据(S2 的靶子)。**这是本机 transcript 的横截面,不是 §1 那份 30 天账的复算**,
两者口径不同,不要互相引用当验证。

⚠️ **观察者效应**:从 Claude 会话里跑 panorama,**跑它的那场会话本身在样本里且还在长**。
同一时间窗四分钟内跑两次,加权输入 62.36M → 63.11M,差的 0.75M 就是本会话自己。做
before/after cohort 对照时必须用 `--session` 显式排除或固定量测会话,不能靠时间窗兜。

**接手时修掉的三处(codex 提交后未跑全量套件,两条红是它留下的)**

- `usage_panorama --help` 在 Codex 引擎退 2:引擎守卫写在 argparse 之前,把 `--help` 一起
  拦了。守卫已移到 `parse_args` 之后——仍先于 `build_panorama`,不碰任何 transcript。
- `test_frame_json_clean` 金样本没跟上:两个新 budgets 键会随 `normalize_budgets` 进 run
  contract(值是 float,不是 jsonc 里的 int)。
- 脱敏对任意文本投机 `ast.parse`,把用户文本里的 `\|` `\s` 当自己代码抱怨:一次
  `usage_panorama` 往 stderr 泼 **5456 行** `SyntaxWarning`,把真读数冲没。已在
  `identity._python_credential_ranges` 消音(带锁,因为脱敏会在多线程下跑)。

另:发布线的 budget 账本此前被顺手删掉三个指标(`measurement_status` / `maturity_status` /
`denominators`),已恢复并补上锁住键集的探针——删任一项即红。

## 附录 A · 待验假设与判定

| # | 假设 | 验法 | 失败动作 |
|---|---|---|---|
| H1 | 项目 local env 能把自动压缩窗收至 200k 量级 | 重启;读 `compact_boundary.preTokens` 与峰值 | 回滚 D1,记录该版本不支持/未命中 |
| H2 | Workflow `agent()` 接受 `gp-shell` 且 transcript/meta 可归属 | 最小 workflow 探针 | 不迁移,评估路线 B |
| H3 | `gp-shell` 下 schema 结构化返回稳定 | 正常/非零/坏 JSON 夹具 | 不迁移,评估路线 B |
| H4 | `gp-shell` 命令字节与 `exec_capture` 一致 | bytes/hash 对照矩阵 | 一次失败即止损,升级路线 B |
| H5 | 自定义壳首轮 p50 至少降 60% 且 ≤20k | 与 general-purpose 同命令配对 A/B | 不以 S2 验收;直接比较路线 B 成本 |
| H6 | 关闭插件并重启可减少疑似尾环 | hook marker + 启停 cohort | 无因果证据则只保留 `suspected_tail` 读数 |
| H7 | S1+S2 后壳 ≤1.5M 且 ≤run 20% | smoke 功能通过后看 UsageLedger | 升级路线 B |

普通自定义 subagent 会加载 CLAUDE.md / memory;Explore/Plan 属例外。因此删除原稿「自定义 agent 不注入 MEMORY」假设,不再从首轮 token 反推系统注入内容。

## 附录 B · 探针与重现实务

- 历史 30 天原件:`docs/research/2026-09-04-token-efficiency/token_panorama.py` 与 `token_breakdown.py`;它们仅用于复现冻结基线,不能作为上线 CLI。
- 正式时间边界读取 transcript 事件时间;不得拿 transcript 或 settings 的 `mtime` 切 cohort。
- 疑似尾段:最后一条 user 后的连续 assistant 调用;只统计,结合 hook marker 与插件启停 cohort 才能做因果判断。
- 进程是否重载:记录 Claude 主进程启动时间、候选 session 创建时间与 hook marker;`ps` 只作辅助证据,不以 settings mtime 代替。
- 首轮上下文:`input + cache_read + cache_creation`;同时保留各分量,避免总量掩盖 cache 结构变化。
- 命令完整性:用无副作用 fixtures 覆盖 quoting/Unicode/non-zero/stdout/stderr/JSON,逐项对照原命令 hash 与 `exec_capture`。

## 附录 C · 关键实现位置与外部依据

- `.claude/workflows/l4-stock.js`:trace-control、stage/task/json/bash、ens-dump 共 6 处 general-purpose 壳
- `.claude/workflows/scan-market.js`:前奏、trace-control、壳助手、finalize-failed 共 6 处
- `.claude/workflows/dossier-init.js`:普通壳与 lint JSON 壳共 2 处
- `autoresearch/trace/usage_harvest.py`:cache 权重、`usage_of`、session 定位与 `_MODEL_MULT`
- `autoresearch/trace/transcripts/base.py`:`UsageRecord` / `TranscriptAdapter` 契约
- `autoresearch/trace/web_budget.py`:`OBSERVED_ONLY` 限制
- `autoresearch/scan/budget.py`:`observe_run` 与至少 10 次真实扫描的 `evaluate_history`
- `autoresearch/scan/user_config.py`:`budgets` / `l4_intel` 白名单与类型验证
- `autoresearch/contracts/artifacts.py`:root、stage=`observe` 与计量产物登记
- `.claude/agents/l4-intel.md`:六面与网查声明契约;`.claude/agents/l4-card.md`:情报缺口 fallback
- [Claude Code settings](https://code.claude.com/docs/en/settings)
- [Claude Code environment variables](https://code.claude.com/docs/en/env-vars)
- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents)

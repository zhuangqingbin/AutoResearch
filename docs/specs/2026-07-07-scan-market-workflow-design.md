# scan-market 全流程 Workflow 化设计

- **日期**: 2026-07-07
- **状态**: 设计待审(brainstorming 产出)
- **作者**: Claude(session 内)+ 用户拍板
- **相关**: `.claude/skills/scan-market/SKILL.md`(现行编排)、`STAGES.md`(现状快照)、本次 07-06 提速波(memory `scan-speed-optimization-wave-20260706`)

---

## 1. 背景与问题

现行 scan-market 是一张 **SKILL.md 长清单,由主循环(我)逐条手工执行**:prelude → 哨兵决策 → 市场研判/行业 brief → L3 精排 → L4 决策卡 → 买单 skeptic/红队 → assemble。这套编排是 **model-driven**(每个检查点我读中间产物再决定下一步)。

它有三个结构性痛点:

1. **手工执行 = 会漏步会错**。仅 07-06 一轮就踩了三个"执行时"数据坑:finalists 代码丢前导零(5/20 卡误判缺失)、`.SH` 后缀空 slim、批量 slim 静默失败(603799)。都是我在场逐段读中间产物才抓到——但这依赖"我在场"。
2. **per-agent effort 不可靠**。07-06 用 `.claude/agents/*.md` frontmatter 加 `effort:`(l3-rank=max、l4-card=medium、sector-brief=low)——**harness 是否读它未经验证**,可能被静默忽略。真正契约级的 per-agent effort 只有 **Workflow 的 `agent({effort})`**。
3. **编排全靠我在场逐段驱动**。判断点(哨兵、L3 复核)和数据坑全靠我人肉盯:要么我在(不可复现、占我时间),要么没人管(盲跑出错)。这才是核心——把编排变成"确定的东西"。
   > 注:买单 skeptic(mode A)已于 07-06 按用户决定移除,故 L4 已无"派卡→派 skeptic"的两段串行,本设计不追求 card→skeptic 的 pipeline 重叠;并发就是 `parallel()` 一次扇出。workflow 的真实收益 = **契约级 effort + 确定性/不漏步 + 校验门**,而非墙钟重叠。

**用户决定**(2026-07-07):把**整条漏斗做成一个确定性的 Workflow**,全自动后台跑完,产出成品报告。因为"整个流程应该是一个很确定的,用这种方式是合适的"。correctness 模型选 **全自动 + 程序化校验门**(而非保留人工暂停点)。

---

## 2. 目标 / 非目标

### 目标
- 把 L0→L5 全漏斗编排收进**一个 JS Workflow 脚本**,后台一次跑完(~45–60 min),末尾交付成品 `reports/scan/<run>/`。
- **契约级 per-agent effort/model**:L3=max、L4 卡=medium、sector-brief/校验门=low。
- **L4 卡并发 + effort 分层**:~30 张卡 `parallel()` 一次扇出(与现行"一条消息派全部"同并发),但每张 `{effort:'medium'}` 是契约级。
- **程序化校验门**取代"我在场逐段读"的人工检查点,把 07-06 那三类坑变成**自动拦截/自愈或响亮中止**。
- 把三个数据坑**在源头修死**(而非每轮手工打补丁),让漏斗敢盲跑。
- **可复现**:同日期同输入 → 同编排;`resumeFromRunId` 让"后段出错不必重跑前段"。

### 非目标(本期不做,YAGNI)
- **不改漏斗的判断逻辑**:L2 仍是确定性分层采样、L3 仍 holistic 单 agent、OW 三门/PM 三透镜不动。Workflow 只搬**编排**,不动 alpha。
- **不做 cron/定时**。Workflow 化后未来可挂,但本期只交付"我手动 kick off 一次"的后台 run。
- **不重写 leaf agent**。`l3-rank`/`l4-card`/`buy-skeptic`/`sector-brief` 四个 agent-def 原样复用(人设已烤进)。
- **不废弃 SKILL.md**。它转为"人类可读的规格 + 手工兜底路径"(见 §7)。
- **不重新实现确定性 Python**。prelude/universe/assemble 等原样调用。

---

## 3. 选型:一个整体 Workflow(方案 A)

| | A 单体 Workflow ✅ | B 薄 JS + 胖 Python orchestrator | C 组合子 workflow |
|---|---|---|---|
| 形态 | 一个 `.claude/workflows/scan-market.js`,phase-block 自上而下读如漏斗 | 新 `python -m autoresearch.scan.run_all` 吐"派发清单",JS 薄循环读清单扇出 | 若干小 workflow,顶层 `workflow()` 调子 |
| 优点 | **就是"一个确定的东西"**,契合用户意图;单一真值源 | 逻辑多在可测 Python | 各段独立可测/可 resume |
| 缺点 | 脚本长(~250–350 行);但 `resumeFromRunId` 缓存未变前缀,已缓解"晚段错=全重跑" | Python 不能扇出 agent、JS 不能回调 Python,双向握手比省下的复杂;清单间接层绕 | 文件多;嵌套只 1 层,子不能再组合;线性管道属过度设计 |

**选 A**。直接匹配"整个流程是一个很确定的";`resumeFromRunId` 解决 A 的唯一实质缺点;单一真值源最省心智。脚本按 phase-block 分节、注释成漏斗骨架。数据坑修进 Python 源头(可测),校验门是 JS 里的小 schema'd reader-agent。

---

## 4. 架构

### 4.1 JS 如何编排(三类调用 + 文件即数据总线)

Workflow 脚本**无 Bash/文件系统权限**,只能 `agent()`。故:

- **确定性 `uv run …` 步骤** → `agent(cmd, {agentType:'general-purpose', effort:'low'})`(该 agent 有 Bash)。prompt 收紧:"精确执行此命令,回报末 20 行 stdout + 退出码,别的都不做"。**紧耦合命令合并进一个 agent**(如 prelude 本身已把 8 件事串成一条命令),降 agent 开销。
- **LLM 判断步骤** → 现有 leaf agent + 真 `{effort}`:`agent(prompt, {agentType:'l3-rank', effort:'max'})` 等。
- **控制值**(脚本要分支用的:universe/L2 计数、哨兵档位、finalist 名单、哪些卡 ≥OW) → 小 **schema'd reader-agent** 返回 JSON。脚本据此 `if`/`pipeline`。

**数据本体走文件**(`context/scan/<date>/…`),leaf agent 读写文件,脚本只在 agent 间传小控制量。日期经 `args={date}` 传入(脚本内 `Date.now()` 不可用)。

### 4.2 阶段 DAG(全漏斗一次跑)

```
frame --json  ── 1 general-purpose agent ──  湖派生 market_pack(取数入湖 → prelude 基本命中不重拉)
      │
   [ prelude  ∥  market_view ]   ← parallel() 屏障(SKILL §0.5 Stage 0 并发)
      │   prelude(general-purpose): universe/L0-L2 + calendar + watchlist + menu + catalyst + 全 ledger
      │   market_view(1 macro-lite agent): 读 pack 写 market_view.md —— **两分支都用**(sentinel 进 assemble;full 再进 L3/L4 地形)
      │
   ⟦GATE 1⟧ reader-agent: universe_n>0 · l2_n>0 · L2 codes 全 6 位零填 · 返回 {sentinel_level, l4_budget}
      │
      ├─ 若 sentinel_level == 哨兵 ──▶ [跳过 sector/L3/L4] ──▶ 观察单 express + 机会成本红队×2 ──▶ assemble
      │        (market_view 已就绪 · calendar/watchlist 已在 prelude 内跑过)
      │
   [ sector-briefs(K≤6)  ∥  L3 证据/news/catalyst harvest ]   ← parallel() 屏障(仅 full 模式)
      │       (N sector-brief low)          (1 general-purpose harvest agent)
      │
   L3 表落稿(general-purpose: l3_table_md → _l3_table.md)
      │
   L3-rank  ── 1 agent {agentType:'l3-rank', effort:'max'} ──  写 L3_judged_full.csv
      │
   merge_l3_finalists_v2(budget) → finalists.csv   (general-purpose)
      │
   ⟦GATE 2⟧ reader-agent: finalist codes 全 6 位零填 · count≈budget · 无下降趋势入选 · 返回 finalists[]
      │
   l4_card prompts + 批量 slim-harvest + l4_reuse --carryover + pledge + watchlist express  (general-purpose)
      │
   ⟦GATE 3⟧ reader-agent: 每 slim >10KB · _harvest_list 无 .SH · 逐票核对 → 列出 offenders
      │        └─(有 offender)→ 定向重 harvest 一个 general-purpose agent → 复检
      │
   L4 卡  ══ parallel(finalists.map(card)) ══  (barrier:红队需全部评级才知是否 0 买)
      │   card: agent{l4-card, medium} 渐进深度+早停 → details/<code>.md · schema 返回 {code, rating}
      │   (买单 skeptic mode A 已移除 → 无 card→skeptic 阶段)
      │
   ⟦控制⟧ JS 汇总各卡 rating → buys=≥OW · is_zero_buy = buys.length===0
      │
      └─ 若 is_zero_buy 且 menu.should_run_opportunity_redteam(抽检) ──▶ 机会成本红队
      │        agent{buy-skeptic 模式B}×≤2 → 产出只进观察单(不改评级)
      │
   assemble  ── 1 general-purpose agent ──  写 reports/scan/<run>/
      │
   ⟦GATE 4⟧ reader-agent: 读 summary.md 顶部 self_review banner;若 FAIL → 响亮中止 + 回报失败 lint
      │
   ✅ 交付 reports/scan/<run>/ + 一句话摘要给用户
```

### 4.3 四道校验门(取代"我在场"的机器化质检)

| 门 | 位置 | 检查 | 失败动作 |
|---|---|---|---|
| **GATE 1** | prelude 后 | universe/L2 非空;L2 代码 6 位零填 | 空 → 中止(数据层坏);零填异常 → 中止(暴露源头 bug) |
| **GATE 2** | finalists 定稿后 | finalist 代码全 6 位;count≈budget;无 downtrend 入选(L3 硬约束 B) | 零填异常 → 中止;count 严重偏离 → 记 proposal 但继续 |
| **GATE 3** | slim harvest 后 | 每 slim >10KB(**07-06 教训:>10KB 才可信**);`_harvest_list` 无 `.SH` | offender → 定向重 harvest + 复检;仍失败 → 该票标 NO_DATA 跳过 + 记 proposal(不让盲卡默认 Hold 混入) |
| **GATE 4** | assemble 后 | `self_review` 硬门 banner(现已 fail-to-front) | FAIL → 中止 + 回报失败 lint(如"买单未过 skeptic"——注:买单 skeptic 07-06 已停用,该 lint 已注释) |

校验门本身是 **effort:'low' 的小 reader-agent**,读文件回 JSON,几乎零成本。

### 4.4 并发模型
- **prep 三件并发**(market_view ∥ sector-briefs ∥ L3 证据 harvest):`parallel()` 屏障——L3 表要三者齐备。
- **L4 卡**:`parallel()` 一次扇出全部卡(barrier——红队需全部评级才知是否 0 买)。买单 skeptic 已移除,无 card→skeptic pipeline;红队是 0 买日 post-barrier 的抽检。
- 并发上限 `min(16, cores-2)`;~30 张卡排队跑,自动填满。

---

## 5. 数据完整性:三个坑在源头修死(前置阶段)

| # | 坑 | 根因(已核实) | 源头修法 | 门兜底 |
|---|---|---|---|---|
| 1 | **前导零丢失** | `merge_l3_finalists_v2` **内存里正确**(l3_select.py:291 zfill code、:314 ticker=code);丢失发生在 **CSV 往返**——消费方(assemble/journal/buy_ledger/retro/market/stage_eval/shadow_buys)用 pandas 默认整数推断读 `finalists.csv`/`L*.csv` → `"000062"→62` | 加**共享 zfill-on-read helper**(如 `read_scan_csv(path, code_cols=...)`),替换各处裸 `pd.read_csv`;含代码列的 scan CSV 统一走它 | GATE 1/2 校验 6 位 |
| 2 | **`.SH` 空 slim** | **已在源头修好**:`l4_card prompts` 走 `normalize_symbol`(l4_card.py:415)→ `_harvest_list.txt` 单一后缀 `.SS/.SZ/.BJ`。07-06 的 `.SH` 来自**手工在此路径外 harvest** | **铁律:workflow 永远走 `l4_card prompts` 产的清单,绝不手工 harvest** | GATE 3 校验无 `.SH` |
| 3 | **slim 静默失败** | 批量 slim-harvest 单票失败被吞(603799),下游盲卡默认 Hold | 批量 harvest **失败响亮**(汇总失败票 + 非零退出) | GATE 3 逐票 >10KB,offender 定向重拉;仍败 → 标 NO_DATA 跳过 + proposal |

坑 #1 是本期唯一实质源头改动(#2 已好,#3 是"失败可见 + 门自愈")。

---

## 6. 交付分期(边建边验)

1. **P1 数据坑源头修 + 单测**:共享 zfill-on-read helper 接入各消费方;批量 slim-harvest fail-loud。`uv run --no-sync pytest` 全绿。**小、可独立验收**。
2. **P2 写 workflow 脚本**:`.claude/workflows/scan-market.js`,四道门 + 全 DAG。
3. **P3 便宜冒烟**:mock/tiny universe 验**门与时序**(空 universe 触 GATE 1、伪造 62 触 GATE 2、伪造 <10KB slim 触 GATE 3、伪造 fail banner 触 GATE 4);不烧全量 token。
4. **P4 首次真跑**:下次要扫描时后台跑一次真实端到端(~45–60 min),对 07-06 手工基线核账(墙钟/token/买卖结论一致性)。

---

## 7. 与 SKILL.md 共存

- SKILL.md 转为**人类可读规格 + 手工兜底**:文首加"默认经 workflow `scan-market` 跑;手工分步见下"指针。各阶段语义/铁律/坑仍以 SKILL.md + STAGES.md 为**真值源**(workflow 脚本注释引用它们,不复制人设)。
- 过渡期二者并存:workflow 是默认 runner,手工路径保留兜底(workflow 有坏天可回退)。稳定后再议是否收窄手工路径。

---

## 8. 测试策略
- **P1 Python 修法**走现有 pytest(共享 reader helper 的往返测试:`"000062"` 存活;fail-loud harvest 的失败断言)。契约延续 718 绿。
- **workflow 脚本**是 JS 无单测框架;靠 **P3 冒烟**(合成小 universe 触发四门)+ `resumeFromRunId` 缓存重放调试。
- **门的 reader-agent** 用 schema 强约束返回结构(mismatch 自动重试)。

---

## 9. 风险 / 待定
- **general-purpose agent 跑 bash 的开销**:每确定性步一次 agent spawn(~4–5 次)。可接受;紧耦合命令已合并。
- **reader-agent 读文件回 JSON 的可靠性**:靠 schema + 收紧 prompt("只读只回,不判断")。
- **首次真跑前无法完全验证墙钟/token 收益**——P4 才见真章;P3 只验正确性骨架。
- **effort 真生效**:Workflow 的 `{effort}` 是文档承诺的契约参数;P4 用 `_stage_timing.json` + OTEL 核实(相较 frontmatter 的"可能被忽略"是确定升级)。
- 待定:GATE 2 的"count 偏离阈值"、GATE 3 "仍失败即跳过"的 NO_DATA 呈现细节 → 留 P2/writing-plans 定。

---

## 10. 成功判据
- workflow 一次 kick off,后台跑完,交付与 07-06 手工路径**同构**的 `reports/scan/<run>/`(漏斗数量表 + buy-list + 决策卡 + trace)。
- 四门在 P3 合成用例全部按预期拦截/自愈。
- 三个数据坑源头修后,P4 真跑**不再出现** 07-06 的三类"执行时"错。
- L4 pipeline 重叠 + effort 分层在 P4 墙钟上可测得改善(相对 07-06 的 65m 基线:L3 19m / L4 14m / slim 10m / 红队 9m)。
- SKILL.md/STAGES.md 仍为语义真值源,workflow 不与之漂移。

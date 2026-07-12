# TradingAgents 自学习/闭环系统 —— 事实地图(2026-07-12 勘察)

> 方法论:全文基于源码 grep+读取(`autoresearch/learning/`、`autoresearch/scan/`、`.claude/workflows/scan-market.js`、`.claude/agents/*.md`)+ 真实数据文件现状(`context/`、`reports/`)交叉验证。**只陈述事实,不给建议**。所有数字截至勘察时刻(仓库最新真实数据日 = 2026-07-09;之后只有 2026-07-11 P0+P1 波的确定性件冒烟/单元测试,无新的真实端到端 scan)。

---

## 0. 总体结论(先看这段)

1. **闭环的"确定性骨架"是完整且已生产接线的**:prelude → universe → L3/L4 → assemble 每一步都会自动触碰若干 learning/ 模块(见 §2 图)。这不是"设计了但没接"的空转系统。
2. **但"教训真正改变下次扫描行为"这条链,人工/Claude 决策节点(scan-retro 诊断会话、proposals 人批、weights 重标定)不是自动调度的**——它们需要一次 skill 调用去触发。截至勘察时刻:**2026-07-07、2026-07-08 两个已成熟真实交易日,attribution.csv 已算出但从未跑过 scan-retro 诊断**(无 `retro/done.json`、无 `retro_input.md`),2026-07-09 因 T+2 尚未成熟还排不上号。这是当前"反馈饥饿"的最直接量化:**至少 2 个交易日的复盘欠账**。
3. **2026-07-11 当晚的"P0+P1 波"(12 个 task)已把 07-11 brainstorm 诊断出的四件"仪器坏账"修了 3 件半**:① `bought` 列 ✅ 已修且已回填真实历史;② OW 三门结构化账本 ✅ 已建且已产出真实数字(tail_rate 40%/37%/42%,n 天数 2-3 天);④ `market_pack.json` 日志污染 ✅ 已修;③ **"attribution 用的是卡面评级而非发布终评级"——项目自己的文档承认这一条被有意推迟到下一波**(`docs/specs/2026-07-11-funnel-six-questions-brainstorm.md` 头部状态行 + `.claude/skills/scan-market/STAGES.md` 开放线头 #6),且我用代码走查独立确认了原因:`assemble.py::_publish_details()`(:1005-1025)用 `shutil.copy2` 原样拷贝卡片,ensemble/verify 折回只改内存里的 `rows`,从不回写卡片文件——所以 `retro._buylist()`(读发布卡文本 parse_rating)拿到的仍是折回前的卡面评级。项目文档自己标注"**ensemble 首次真折回前必须完成**"这个修复,当前还没做。
4. **P0+P1 波的全部 LLM 段新功能(温度计消费/L3 指纹+lint/L4 中性前提+基率行/买单 ensemble)都还没有经过一次真实端到端 scan 验证**——`STAGES.md` 自己写着"下次真扫描=正式验收"。这不是我的推测,是项目自己的开放线头 #7。

---

## 1. 模块清单与职责(`autoresearch/learning/` 18 个模块)

逐模块:读什么 / 写什么 / **生产调用点**(真实执行链路会碰到的调用者)/ 是否需要人工·Claude 触发。

| 模块 | 读 | 写 | 生产调用点(自动) | 需人工/Claude 触发的部分 |
|---|---|---|---|---|
| **feedback_store.py**(922行) | `context/knowledge/{lessons,feedback,proposals,changelog}.jsonl` | 同上四个 jsonl + `lessons.md` | `lessons_for()` 被 `assemble.py:544,762`(`_knowledge_note`/`_self_review_banner`)**每次发布真实调用**;`decay_lessons()` 被 `retro.mark_done()`(:794)调用 | `upsert_lesson/adjudicate/add_proposal/add_prompt_patch/mtm_update(非guard)` 只在 `.claude/skills/feedback/feedback-playbook.md`、`retro-playbook.md` 的示例代码里被调用——即**由 Claude 在 feedback/scan-retro 会话里手动执行**,无代码自动触发点(`grep` 全仓库确认,仅 `retro.py:mtm_check_guards` 对**带 guard** 的经验自动调 `fs.mtm_update`,无 guard 的经验永远要人判) |
| **retro.py**(901行) | `L1_scored_full.csv`+市场已实现收益(factor_lab)+发布报告 `details/*.md` | `retro/attribution.csv`、`retro/_retro_pairs.csv`、`retro/retro_input.md`、`retro/done.json` | `refresh_attributions()`/`pending_days()` 被 `prelude.py:63-72` **每次 prelude 自动跑**(但只刷新"已 done 但 fwd 未成熟"的老日,**不会**给新日产生诊断);`recalibrate_and_log()` 只在 scan-retro 会话内手调 | `attribute(date)`+`write_retro_input()`+`mark_done()` 是 scan-retro skill 的核心步骤,**必须由 Claude 会话主动跑一次**才能从"attribution 已算"推进到"诊断已产出"。这是 §2 讲的关键断点。 |
| **self_review.py**(344行) | `finalists`/`L1_scored_full`/lessons/`flow` ctx | `gate_fires.csv`(`dump_gate_fires`)、`gate_fires.csv` 追加行(`dump_ow_gate_fires`) | `review()`+`card_contract_lint()`+`intel_future_dates_lint()`+`dump_gate_fires()`+`dump_ow_gate_fires()` 全部被 `assemble.py::_self_review_banner()`(:741-789)**每次真实 assemble 自动调用**,是 workflow GATE4 的判据来源(`.claude/workflows/scan-market.js:216-220`) | 无(纯确定性硬门) |
| **stage_eval.py**(411行) | `attribution.csv`+`L1/L2/L3/L4` staging | `retro/channel_eval.csv`、`retro/stage_eval.csv` | 被 `retro.write_retro_input()`(:697-701)调用 → **只在 scan-retro 会话跑 `retro attribute <date>` 时才产生**,不在 prelude 自动链路里 | 同上,依附 scan-retro |
| **buy_ledger.py**(359行) | 各日 `retro/attribution.csv`+发布卡目标价 | `reports/learning/buy_ledger.md`;`context/learning/target_calib.json`(`write_target_calib`) | `main()` 被 `prelude.py:_ledgers()`(:144-156)**每日自动跑**;`write_base_rates`/`write_target_calib` 被 `l4_card.py::write_dispatch_pack()`(:672-676)**每次 L4 派发自动跑**(即 workflow `l4_card prompts` 步骤,`scan-market.js:141`) | 无 |
| **channel_ledger.py**(149行) | 各日 `retro/channel_eval.csv` | `reports/learning/channel_ledger.md` | **无自动调用点**——不在 `prelude._ledgers()` 名单里,也不被 assemble/l4_card 引用;只能靠人/Claude 手跑 `python -m autoresearch.learning.channel_ledger`(`retro-playbook.md`/`scan-retro/SKILL.md` 里教这么跑) | 是,纯手动 |
| **cross_calib.py**(212行) | `L3_judged_full.csv`×`final_ratings`(door:`scan.health`)+ `gate_fires`×`attribution` | `reports/learning/cross_calib.md` | `flip_stats/gate_stats/suggestion_lines` 被 `prelude.py:34-44`(`calib_suggestion_lines`,prelude 汇总屏建议行)**自动调用**;`flip_stats`/`buy_ledger.roll` 又被 `l4_card.py::write_base_rates()`(:384-402)**自动调用** | `main()`(报告落盘)本身不在自动链路里,需手跑;但其核心函数被自动消费 |
| **gate_ledger.py**(99行) | 各日 `gate_fires.csv`×`retro/attribution.csv` | `reports/learning/gate_ledger.md` | **无自动调用点**,同 channel_ledger,纯手动 CLI | 是 |
| **journal.py**(124行) | `meta.json`/`L2`/`finalists`/`watchlist_status.csv`/`attribution.csv`+`scan.health.count_buys` | `reports/learning/journal.md` | `main()` 被 `prelude.py:_ledgers()`**每日自动跑** + 被 `assemble.py::run()`(:1138-1140,`is_real` 门控)**每次真实发布再跑一次** | 无 |
| **paper_nav.py**(257行) | 发布报告买单+`shadow_buys.csv`+价格(factor_lab) | `reports/learning/paper_nav.md`+`paper_nav_summary.txt` | `main()` 被 `prelude.py:_ledgers()`**每日自动跑**;summary txt 被 `assemble.py:850-858`(build_summary)**每次发布嵌入摘要行**(仅真实 scan_dir 才嵌入) | 无 |
| **precedents.py**(526行) | `details/*.md`(历史卡)+lessons | `context/knowledge/precedents.db`(sqlite+FTS) | **读侧**`query()` 被 `l4_card.py::_precedent_mark()`(:311-326)**每次派发简报自动调用**;**写侧**`build_index()` **全仓库 grep 无任何生产调用点**,仅 `tests/` 与文档示例调用 | **是,写侧完全手动**——db 会随时间静默过期,除非有人手跑 `precedents build` |
| **sector_ledger.py**(136行) | `sector_briefs/*.md` | `context/knowledge/sector_calls.jsonl`(`record_calls`)、`reports/learning/sector_ledger.md`(`main`/`render_report`) | `record_calls()` 被 `assemble.py::run()`(:1128-1132,`is_real` 门控)**每次真实发布自动调用** | `mature_call/backfill`(成熟度回填)+ `main()` 汇总报告 **无自动调用点**,需人/Claude 手跑 |
| **sector_memo.py**(73行) | `context/knowledge/sector_memos.json`(需确认路径,`load_memos`) | 同上 | `render_memo_line()` 被 `l4_card.py::compose_funnel_brief()`(:504-509,仅当 `sector.brief.render_terrain_block` 无地形段时回退)**自动调用** | `upsert_memo()` 只在 `retro-playbook.md`/`sector-playbook.md` 示例代码里,人工/Claude 手写 |
| **catalyst_ledger.py**(99行) | 各日 `L3_catalyst.csv` | `reports/learning/catalyst_ledger.md` | `main()` 被 `prelude.py:_ledgers()`**每日自动跑** | 无 |
| **changelog_ledger.py**(115行) | `context/knowledge/changelog.jsonl`+`retro/attribution.csv`(日度 IC) | `reports/learning/changelog_ledger.md` | **无自动调用点**,纯手动 CLI | 是 |
| **watchlist_ledger.py**(116行) | `watchlist_status.csv`+`attribution.csv` | `reports/learning/watchlist_ledger.md`(含 `monitoring_section` 的"在监控"节) | `main()` 被 `prelude.py:_ledgers()`**每日自动跑**(`monitoring_section` 由模块内 `main()` 自己拼进 render,非外部调用) | 无 |
| **zero_buy_ledger.py**(83行) | 各日 `retro/attribution.csv` 的 `bought`/`fwd_*` 列 | `reports/learning/zero_buy_ledger.md` | **无自动调用点**——**不在** `prelude._ledgers()` 名单里(容易误以为在,实际 grep 确认没有),纯手动 CLI(SKILL.md 提示"连续 0 买时看…") | 是 |
| **shadow_buys.py**(114行) | finalists 的 rubric 净分排序 top-3 | `context/learning/shadow_buys.csv` | `record()` 被 `assemble.py::run()`(:1133-1137,`is_real` 门控)**每次真实发布自动调用** | `backfill()`(补历史)无自动调用点,手动 |

**小结(职责×生产接线一览)**:
- **自动、天天跑、不需要人**:`journal`、`buy_ledger`(含 target_calib)、`cross_calib`(核心函数)、`catalyst_ledger`、`paper_nav`、`watchlist_ledger`(以上 6 个是 `prelude._ledgers()` 白名单,见 prelude.py:144-156)+ `self_review`(assemble 每次发布)+ `sector_ledger.record_calls`/`shadow_buys.record`/`journal.main()`(assemble.py `is_real` 门控)+ `precedents.query`/`sector_memo.render_memo_line`(l4_card 简报组装)。
- **需要人/Claude 手动执行 CLI 才更新**(即"账本存在但不会自己长大"):`channel_ledger`、`gate_ledger`、`zero_buy_ledger`、`changelog_ledger`、`sector_ledger` 的 `render_report/main`(record_calls 是自动的,但汇总报告不是)、`precedents.build_index`(**明确的写侧死链**,读侧活)、`stage_eval`(依附 scan-retro 才跑)。
- **需要人/Claude 手写代码片段才触发**(无 CLI 入口,只在 playbook 示例里):`feedback_store.upsert_lesson/adjudicate/add_proposal/add_prompt_patch/similar_lessons`。

---

## 2. 反馈回路完整度:从"一次扫描发布"到"教训改变下次扫描行为"

### 2.1 全链路时序图(每一环标注:自动 or 人工/Claude 触发)

```
① scan-market 发布(assemble.py::run)
   ├─ [自动] self_review 硬门 → gate_fires.csv(assemble.py:786-789)
   ├─ [自动] sector_ledger.record_calls / shadow_buys.record / journal.main()(assemble.py:1127-1140, is_real 门控)
   └─ [自动] paper_nav_summary.txt 嵌入摘要(assemble.py:850-858)
        ↓(需等 T+2 交易日实现)
② retro.pending_days() 列出待复盘日 —— [自动,由 prelude 每日打印,不诊断只报警]
        ↓
③ 【人工/Claude 触发点 A】scan-retro skill:retro.attribute(date) → write_retro_input() → retro_input.md
        ↓
④ 【人工/Claude 触发点 B】Claude 读 retro_input.md 做诊断,upsert_lesson / add_proposal / mtm_update(无guard经验)
        ↓
⑤ retro.mark_done() → done.json + decay_lessons()(自动,一旦④完成)
        ↓
⑥ 【人工/Claude 触发点 C】视诊断结果决定是否 recalibrate_and_log()(factor_lab.calibrate 重写 weights.json)
        ↓
⑦ 下次 scan:weights.json 已变 / lessons.jsonl 已变 → L1 打分/L3-L4 简报的 knowledge_note/base_rates 读到新内容(自动,presence-gated)
```

**闭合的环**:①→②(全自动)、⑤←④(自动跟随)、⑦←④⑥(自动消费,只要④⑥发生过)。
**断开/依赖人工的环**:②→③(pending_days 只报警,从不自动跑 attribute)、③→④(retro_input.md 写出后**没有任何机制强制或提醒去读它**——不像 proposals 有 `_proposals_nag()` 在 L5 报告里置顶)、④→⑥(是否重标定纯人工判断,无固定 cadence)。

### 2.2 量化"反馈饥饿"程度(真实文件现状,勘察时刻)

- **17 个已产出真实 scan 报告的交易日**(`context/scan/` 下 2026-06-18…2026-07-09,不含 06-19/06-20 两个数据孤儿——06-19 是端午节假日键,STAGES.md:319 记录在案,永久无法结算)。
- 其中 **14 天** `retro/done.json` 存在(即真正跑完过 scan-retro 诊断闭环:06-18,06-22~07-03,07-06)。
- **2 天有 `attribution.csv`(已可复盘)但从未跑过 scan-retro 诊断**:2026-07-07、2026-07-08 —— 均无 `retro/done.json`、无 `retro/retro_input.md`(但**有** `retro/channel_eval.csv`、`retro/stage_eval.csv`,说明 `stage_eval.evaluate()` 曾被独立调用过,但没有走完整 `write_retro_input`+`mark_done`)。这是当前**最直接的"复盘欠账"数字:至少 2 个交易日**。
- **2026-07-09**:`retro/` 目录完全不存在——T+2(07-13,周一)尚未到达"今天"(2026-07-12),尚不满足 `pending_days()` 的可复盘条件,不计入欠账,是正常排队。
- **lessons.jsonl:5 条,全部 `active`**,置信度 0.5–0.65,**没有一条被 retire**。最近一条通过 `adjudicate("ADD", ...)` 写入的时间戳是 `2026-07-09T21:17:44`(changelog.jsonl 第 7 行,`result_id: ls_momentum_recall_quota_swing_horizon`)——**07-10/07-11 都没有新增或修改任何一条 lesson**(07-11 P0+P1 波是纯代码/确定性件波,没有产出新经验)。
- **feedback.jsonl:5 条**——2 条 `distilled`(已转成经验),**3 条 `open`**(`fb_20260625_001`、`fb_20260704_001` token 消耗投诉、`fb_20260704_002` 报告质量投诉)——**这 3 条从 07-04/06-25 起从未被关闭**。
- **proposals.jsonl:11 条**——6 条 `resolved`、2 条 `rejected`、**3 条 `open`**:`pr_20260624_001`(市值地板 30 亿,06-24 提出,已 18 天)、`pr_20260625_001`(资金口径核对,06-25 提出,已 17 天)、`pr_20260702_002`(OW门①CMF滞后,07-02 提出,已 10 天)。三条都被 07-11 波的 task 8 标注为"deferred:等 Task 2 三门账本 ≥20 日后随雷分级一并裁"——即**明确知道要等账本攒够天数,当前尚未到量**。`assemble.py::_proposals_nag()`(:972-997)会在 L5 报告里持续提醒"满 20 交易日未裁",但目前没有一条已过 20 **交易日**(日历天最长 18 天,交易日更少)。
- **changelog.jsonl:9 条事件**——8 条 `recalibrate`/`calibrate_regimes`/`recalibrate_regimes`(实际改权重的只有 5 条:2026-07-02×2、2026-07-02(06-30 数据)、2026-07-09(07-06 数据)、2026-07-11(rz 组,`before=0.0→after=0.0093`,确认 rz 因子这次真的被写进权重));1 条 `lesson_adjudicate`。**头 3 条(06-23/06-24/06-25 记录)`before_sha==after_sha`**(权重算出来和原来一模一样,大概率是早期面板数据不足)。**重标定 cadence 不是每日/每周固定的,是 scan-retro 诊断顺带触发的人工判断,间隔从 1 天到 7 天不等**。
- **changelog_ledger.md(评估"重标定有没有用"的报告)本身停留在 2026-07-02**,只覆盖 4 条最早的 changelog 事件,**后 5 条(含 07-06、07-11 的 rz 事件)从未被评估过**——这是"账本存在但没人去跑汇总"的具体例子(§1 表格里已标注 changelog_ledger 无自动调用点)。其现有结论也弱:"1 次样本足的重标定,Δ 均值 +0.0027——持续 ≤0 = 校准空转"(即目前唯一一条有效样本的重标定效果,IC 提升幅度只有 +0.0027,接近于噪声)。

### 2.3 三个具体的"消费端"闭环(是否真的接线)

- **lesson 注入**:`feedback_store.lessons_for()` 被 `assemble.py::_knowledge_note()`(:544-576)和 `_self_review_banner()`(:762-763)**真实调用**,报告里有"📌 经验/未决反馈"节(presence-gated:无 lessons 也无 open feedback → 不出现该节)。cap:`_knowledge_note` 里 `lessons[:8]`(:568),`open_fb[:6]`(:574)——**用户问的"cap=8"确认存在,是经验展示上限,不是"只能有8条生效经验"的硬性门槛**。
- **MTM 升降级**:`retro.mtm_check_guards()`(:233-265)只对**带 `guard` 字段**({field,op,value})的 lesson 自动判 support/refute 并调 `fs.mtm_update()`;**5 条现存 lesson 里有几条带 guard 未直接确认**(未逐条读 lessons.jsonl 全文,需要时可另查),但代码路径证实:无 guard 的经验**永远需要人工用 `fs.mtm_update(id, verdict, day)` 手判**,`write_retro_input()`(:659-662)只是把它们列成"待人判"提示,不会自动升降。
- **权重再校准**:`recalibrate_and_log()`(retro.py:732-749)调 `factor_lab.calibrate()` 重写 `weights.json`,全链路证实**只在人工/Claude 显式调用时才执行**,无固定 cadence,无自动触发器。

---

## 3. 数据资产现状(行数/日期范围/最近更新,勘察时刻实测)

| 资产 | 路径 | 行数/条目数 | 覆盖范围 | 最近更新(mtime) |
|---|---|---|---|---|
| lessons | `context/knowledge/lessons.jsonl` | 5(全 active) | — | 2026-07-09(changelog 记录的最后一次 adjudicate) |
| feedback | `context/knowledge/feedback.jsonl` | 5(2 distilled / 3 open) | 2026-06-25 ~ 07-04 | — |
| proposals | `context/knowledge/proposals.jsonl` | 11(6 resolved / 2 rejected / 3 open) | 2026-06-23 ~ 07-11 | 2026-07-11 |
| changelog | `context/knowledge/changelog.jsonl` | 9(8 recalibrate 类 + 1 lesson_adjudicate;5 条真实改了权重) | 2026-06-23 ~ 07-11 | 2026-07-11 19:53(rz 组校准) |
| sector_calls | `context/knowledge/sector_calls.jsonl` | 22 | 2026-07-03 ~ 07-09(7 个真实 scan 日) | — |
| precedents.db | `context/knowledge/precedents.db`(sqlite+FTS) | **406 行**(`precedents` 表) | 2026-06-18 ~ 07-09 | 写侧手动(build_index 无自动调用,见 §1) |
| shadow_buys | `context/learning/shadow_buys.csv` | 45 笔(top-3/日 累计) | 2026-06-18 ~ 07-09 | 2026-07-09 22:10 |
| target_calib.json | `context/learning/target_calib.json` | `all.n=77156`(全 universe 逐票-逐日观测池,非天数);`by_regime`: range n=11030 / risk_off n=16551 | 近 30 scan 日滚动窗口 | 2026-07-12 02:05(`hi2_p60`=+3.72%,`touch8_rate`=14.1%) |
| temperature.csv | `context/learning/temperature.csv` | 124 行(数据日) | 回填 ≥120 日(2026-01 起)~ 07-11 | 2026-07-11 17:45 |
| **reports/learning/*.md 各账本报告 mtime(新鲜度对照,核心发现)** | | | | |
| — journal.md | | 17 scan 日,0买日=14(**注:此计数与 zero_buy_ledger 打法不同,见下方"发现"**) | | **2026-07-11 21:33**(最新) |
| — gate_ledger.md(新,含 tail_rate) | | 5 门:OW三门×3(2-3天/12-20拦次)+ 卡片契约×2(⚠样本少) | | 2026-07-11 16:24 |
| — zero_buy_ledger.md(bought 列修复后重算) | | 14 日:**0买日10 / 有买日4** | | 2026-07-11 15:37 |
| — paper_nav.md / summary.txt | | 14 行(20260618~20260708) | | 2026-07-10 21:50(**停留在 07-08,07-09 因 T+2 未到期未入账**) |
| — buy_ledger.md | | OW n=7(已实现2);目标校准 n=63/成熟37/触达43% | | 2026-07-10 21:44 |
| — catalyst_ledger.md | | 3 日(07-06~07-08) | | 2026-07-10 21:44 |
| — channel_ledger.md | | 13 日 unique 超额;9 路 | | 2026-07-10 21:45 |
| — cross_calib.md | | trend lane n=134/高确信52/翻案率33% | | 2026-07-09 21:40(**未被 07-11 波之后的任何一次动作刷新**) |
| — **changelog_ledger.md** | | **只 4 条重标定事件(06-18~06-24 数据),漏了后续 07-06/07-11 共 5 条** | | **2026-07-02 18:04(全部报告里最陈旧,9+天未刷新)** |

**发现(数据交叉核验,非建议)**:
1. **journal.md 与 zero_buy_ledger.md 对同一天"是否有买单"的计数口径不同且当前互相矛盾**:journal.py 的 `buys` 列来自 `scan.health.count_buys(d)`(journal.py:57-58);zero_buy_ledger.py 的 `n_bought` 来自 `attribution.csv` 的 `bought` 列(rating 判定,zero_buy_ledger.py:32-33)。两者理论上应该一致,但 journal.md(2026-07-11 21:33 生成,晚于 bought 列修复)仍显示 06-18 当天 `买=0`,而 zero_buy_ledger.md(同样在 bought 列修复之后生成)把 06-18 计入"有买日"(4 个有买日之一)。**两本台账目前对"06-18 到底是不是买单日"给出不同答案**,原因是两条独立代码路径(`health.count_buys` vs attribution `bought`),未去深挖 `count_buys` 具体实现差异在哪。
2. **target_calib.json 的 n(77156)不是"天数"而是"attribution 逐票观测数"**(近 30 scan 日 × 每日约 5500+ 只 = 累计观测池),不要和"温度计 124 天"或"precedents 406 条判例"的量纲混淆——三者统计单位不同(观测点 vs 交易日 vs 判例条目)。

---

## 4. 已知诊断存档核验:07-11 brainstorm "仪器坏账四件"逐条验收

来源:`docs/specs/2026-07-11-funnel-six-questions-brainstorm.md` §1"病灶三"原文列出的四件仪器坏账。

| # | 坏账原描述 | 状态 | 证据(file:line) |
|---|---|---|---|
| ① | attribution.csv 无 `bought` 列 → zero_buy_ledger 把 3 个真实买单日也记成 0 买 | **✅ 已修** | `retro.py:66`(`m["bought"] = m["rating"].isin(_BUY)`)、`:444`(`_KEEP` 白名单含 `"bought"`)、`:467-482`(`backfill_bought()` 幂等回填函数,真实执行过一次,历史 `attribution.csv` mtime 全部更新为 2026-07-11 15:37);`zero_buy_ledger.py:32-33` 消费该列。重算后 zero_buy_ledger.md 从旧数(14 个 0买日、fwd_2 −0.66%)变为**新数(10 个 0买日、fwd_2 −1.15%;4 个有买日)** |
| ② | OW 三门击杀没有结构化账本 | **✅ 已修** | `self_review.py:227-257`(`dump_ow_gate_fires`,写 `gate_fires.csv` 追加 `check="OW三门·<门>"`、`level="binding"` 行)、`assemble.py:788`(`_self_review_banner` 里自动调用)、`gate_ledger.py:18,62`(`_COLS` 新增 `tail_rate`,KPI 改为"左尾≤−5% 占比"而非平均超额)。真实产出:`reports/learning/gate_ledger.md` 显示 OW三门·主力真在(3天/20次/tail_rate 40%)、业绩真兑现(3天/19次/37%)、估值不透支(2天/12次/42%) |
| ③ | attribution 里的 rating 是卡面评级而非发布终评级(胜宏 OW→skeptic 降 Hold 未反映) | **❌ 未修,项目自己承认推迟** | brainstorm 文档头部状态行原文:"P0-1③ attribution 终评级(**转下波首件**,STAGES 开放线头 6)";`STAGES.md:322` 开放线头#6 完整复述该缺口并给出待实施修法(`assemble` 落 `_final_ratings.json`,retro 优先 join)、标注"**ensemble 首次真折回前必须完成**"。独立代码验证:`assemble.py::_publish_details()`(:1005-1025)`shutil.copy2(card, dst)` 原样拷贝,`_apply_verify_downgrade`(:89-101)/`_apply_ensemble_fold`(:135-146)只改内存 `rows`,从不回写卡片文件;`retro._buylist()`(:357-372)读的正是这份未回写的发布卡文本 |
| ④ | 07-09 market_pack.json 头部混入日志文本 | **✅ 已修** | `frame.py` main 里三处 `print(f"[frame]...")`/`[sentinel]`/`[macro_state]` 改 `file=sys.stderr`(commit `bd035bc`);同 commit 对 07-09 现场文件做过一次性清洗 |

**净结果:4 件坏账 3 件半修复,1 件(③ 终评级)被有意推迟,且推迟的这一件被项目自己标记为"ensemble 首次真折回前必须完成"的高优先级风险——因为 07-11 波同一晚刚刚上线了买单 ensemble 折回机制(任务11),两者理论上应该配套但没有配套。**

---

## 5. 学习的"消费端":注入到哪些 prompt/表/门,是否 presence-gated

| 消费点 | 注入内容 | 来源模块 | presence-gated? | 证据 |
|---|---|---|---|---|
| L4 简报(`_l4_prompt_<code>.md`)🔁 基率行 | `<lane> lane 高确信历史被 L4 翻案 X%(n)` + `OW 卡历史 T+2 胜率/均值` | `l4_card.py::_base_rate_mark()`(:329-361),数据源 `_l4_base_rates.json` 由 `write_base_rates()`(:364-413)取 `cross_calib.flip_stats` + `buy_ledger.rating_base_rates` | 是——三项各自独立判断有没有该 lane/评级条目,`min_n=10` 已在写盘时过滤,读时只判"有没有条目" | l4_card.py:338-361 |
| L4 简报 📐 目标校准行 | `全体 2 日 MFE p60=X%(n)·同 regime p60=Y%(n)` | `l4_card.py::_target_calib_mark()`(:621-644)读 `target_calib.json`(`buy_ledger.hi2_calibration`/`write_target_calib`) | 是——`n_all<min_n` 或 `p60 is None` 时整行不返回(`buy_ledger.py:264-265`) | buy_ledger.py:253-272 |
| L4 简报前提清单(防锚定) | L3 论点改写成"前提1/前提2(兑现机制)"中性措辞,conviction 移到裁决表之后 | `l4_card.py::compose_funnel_brief()`(:462-465) | 是——`mechanism` 字段缺失时"前提2"整行不出现(:464 `*([...]if mech_ok else [])`) | l4_card.py:444-465 |
| L4 简报 📚 跨票判例块 | 近90日同型(sector+可选门型)top-3 判例 | `l4_card.py::_precedent_mark()`(:288-326)读 `precedents.query()` | 是——`precedents.db` 不存在或查无结果 → 空串 | l4_card.py:307-320 |
| L4 简报 行业地形/备忘录 | 行业 brief 地形段优先,无则退化到 `sector_memo` 备忘录行 | `l4_card.py:495-510` | 是——两级 presence-gate(先试 brief,再试 memo,都无则空) | l4_card.py:495-510 |
| L4 简报 R5 前科卡(个股档案) | 同票历史事件/评级变化 | `dossier.render_dossier()`,`l4_card.py:513-518` | 是 | l4_card.py:513-518 |
| **L3 硬约束 D(注意:静态硬编码,不是本次动态注入)** | "trend lane 高确信(conviction≥70)历史被 L4 翻案 33%(n=52)" | `.claude/agents/l3-rank.md:29` | **否——这是写死在 prompt 文件里的字符串**,不读 `cross_calib.flip_stats()` 的实时结果。当前巧合是 `cross_calib.md` 现算出的数字(trend n=134/高确信52/翻案33%)与硬编码文本一致,**但这只是因为 07-06 之后没有新的 L3_judged 数据**,机制上两者并未打通;brainstorm §4 item 6 把"改由 cross_calib 动态生成"列为**未来待做项**,不在本波范围 | l3-rank.md:29 vs cross_calib.py:36-69 |
| L3 rubric ⑥ 兑现机制维 + conviction 行为化重锚 | "≥70=我能说出D+1谁来买…每日≥70至多~5只" | `.claude/agents/l3-rank.md:23,36-37` | 静态 prompt 文本(设计即如此,非数据注入) | l3-rank.md:23,36-37 |
| L4 铁律"先读数据后读论点" | 盲读微pass:P1先读slim数字块写3行独立初判,再读简报裁决 | `.claude/agents/l4-card.md:16` + `.claude/skills/stock-research/lite-playbook.md:9`(同步锚,`tests/test_agent_defs.py` 契约锁) | 静态 prompt 文本 | l4-card.md:16 |
| L5 报告 📌 经验/未决反馈节 | 生效经验(cap 8)+ 未决反馈(cap 6) | `assemble.py::_knowledge_note()`(:538-576) | 是——无 lessons 且无 open feedback → 返回空串,节不出现 | assemble.py:563-564 |
| L5 报告 🌡 温度行 | 情绪温度分/相位/近5日趋势 | `assemble.py:828-831`(`render_temperature_line`) | 是 | assemble.py:828-831 |
| L5 报告 🧾 纸面法庭行 | 真实vs影子vs市场 NAV | `assemble.py:847-858` | 是(仅真实 scan_dir + `paper_nav_summary.txt` 存在) | assemble.py:850-858 |
| L5 报告 ⏳ 待裁决提案节 | open proposals 清单 | `assemble.py::_proposals_nag()`(:972-997) | 是(仅真实 scan_dir + 有 open 条目) | assemble.py:951-955 |
| L5 报告 self_review banner | fail/warn 清单顶到报告最前 | `assemble.py::_self_review_banner()`+`render_banner()` | 是(`result["failures"]` 为空则返回空串) | self_review.py:260-269 |
| 门柱直方图 / OW三门失守分布 | 逐卡解析 `✗` 计数 | `assemble.py::_gate_histogram()`(:328-346) | 是(无可解析卡 → 空串) | assemble.py:342-343 |
| 买单 ensemble 折回 + 🎭 分歧行 | ≥OW 卡3-run取中位,只向下折回;spread≥2 出人裁提示 | `assemble.py:104-156`(`_load_ensemble`/`_apply_ensemble_fold`/`_ensemble_flag`/`_ensemble_dissent_lines`) | 是(无 `_ensemble.json` → 空 dict,老路不破) | assemble.py:111-113 |

**presence-gated 纪律的唯一例外**:L3 硬约束 D(见上表加粗行)——这是全链路里**唯一**一处"本该动态却仍是静态文本"的注入点,且与本次勘察的 cross_calib/flip_stats 基建功能重叠但未接线。

---

## 附:关键 file:line 索引(便于回查)

- `autoresearch/scan/prelude.py:141-156`(`_ledgers()`,6 个自动刷新的 ledger 白名单)、`:59-72`(refresh/pending 自动步骤)
- `autoresearch/scan/assemble.py:741-789`(`_self_review_banner`)、`:1005-1025`(`_publish_details`,坏账③根因)、`:104-156`(ensemble)、`:89-101`(verify 折回)、`:1126-1140`(`is_real` 门控的三个自动记账)、`:972-997`(`_proposals_nag`)
- `autoresearch/learning/retro.py:50-110`(`attribute_frame` 分桶逻辑)、`:410-436`(`pending_days`)、`:588-716`(`write_retro_input`)、`:732-749`(`recalibrate_and_log`)
- `autoresearch/learning/self_review.py:208-257`(两个 dump_gate_fires 函数)
- `autoresearch/learning/gate_ledger.py:18,58-66`(tail_rate KPI)
- `autoresearch/learning/cross_calib.py:36-69`(flip_stats)
- `autoresearch/learning/buy_ledger.py:189-272`(hi2_calibration/write_target_calib/target_calib_line)
- `autoresearch/scan/agents/l4_card.py:329-413`(基率锚)、`:621-644`(目标价锚)、`:647-728`(write_dispatch_pack)
- `.claude/workflows/scan-market.js:49-83`(Prelude/GATE1)、`:184-208`(ensemble)、`:216-220`(GATE4)
- `.claude/skills/scan-market/STAGES.md:315-324`(开放线头 1-8,项目自述的诚实局限,与本报告高度互证)
- `docs/specs/2026-07-11-funnel-six-questions-brainstorm.md:3`(头部状态行,坏账③推迟的一手来源)

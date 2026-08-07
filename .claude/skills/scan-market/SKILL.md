---
name: scan-market
description: "Use when the user wants to scan the WHOLE A-share market to discover buy-worthy stocks AND strong sectors — 「扫描全A股」「全市场选股」「哪些板块值得买」「find the best A-share buys」. Deterministic L0-L2 funnel + Claude L3/L4/L5; artifacts → reports/scan/<run_id>/. NOT for: one named ticker (→ stock-research; 持仓单票复核走其 lite 档), reviewing a past scan day (→ scan-retro), cross-asset macro (→ macro-research). Project-local."
---

# scan-market — 全 A股六段漏斗扫描(挖掘个股 + 板块,零付费 API)

> 沿革见 git log(docs/specs/ 各 wave 设计稿);本文件 = 编排入口,机制/参数/实证读数快照见 `STAGES.md`,冲突以源码为准。

## 核心原理

对 ~5,500 只逐个跑深度报告 = 几亿 token,不可行。本 skill 用**搜索/推荐系统式六段漏斗**:**确定性层**(零 token)把全市场排序收到 ~200 → Claude **holistic 一次通看、比较着精排**到 ~30 → 只对这 ~30 跑 **stock-research(lite 档)决策卡** → 整合。**token 只跟最终深挖的几十只成正比,与全市场规模无关**。渐进深度+早停:L0/L1/**L2 全确定性(零 LLM)**;**L3 = 1 次 Opus-high holistic**;**L4 = 一只 finalist = 一个 Opus subagent 渐进深度 DD + 早停**。**全程 Opus,省 token 靠早停**。

| 段 | 名称 | 引擎/模型 | 作用 | 进→出 | token |
|---|---|---|---|---|---|
| **L0** | 选集 | 确定性 | 候选池+硬门(ST/退/停牌/次新+市值地板) | 全A→~5,500 | 0 |
| **L1** | 召回 | 确定性·多路策略 | 10 路 channel 各取 top → quota union(floor 保底多样性)+ provenance | →1,000 | 0 |
| **L2** | 粗排 | 确定性·分层采样(ML-free) | sector-neutral composite 排序+风格桶floor+sector cap;**不预测**(实证无稳健alpha,见 STAGES.md) | →200 | 0 |
| **宏观lite** | 市场研判(旁路) | Opus·单agent | 写 `market_view.md`;地形段喂L3/L4(防锚定:只描述不指令) | 旁路·1份 | 小 |
| **L3** | 精排 | Opus-high·holistic单agent | 通看~200比较选+真证据+channel共振+论点/红队/sentiment | →~30 | 中 |
| **L4** | 研究 | 一只=一个Opus subagent渐进深度+早停 | 决策卡(P0简报→P1–P3表面→主早停②→P4陷阱核→P5;`rubric_rating`派生评级) | ~29卡 | 大头 |
| **L5** | 整合 | 确定性 | summary(逐阶段表+token估算)+buy-list+漏斗溯源 | 1份 | 0 |

本 skill 是**编排器**:确定性层(零 LLM)= L0/L1/L2(`scan.universe`,L2 是 ML-free 分层采样)+ L5(`scan.assemble`,内部 `publisher`/`report_sections`/`decision_finalize`/`post_run` 分责),纯 pandas 不编数、不预测;AI 判断层 = L3(holistic 单 agent)+ L4(逐只决策卡,委托 **stock-research lite 档**:P0简报→P1–P3表面→早停②→P4陷阱核→P5满卡),subagent 只回传紧凑结果。

## 何时用 / 不用
- ✅ 用户想**一次扫全市场**、挖"值得买的票 / 强势板块"(A股)。
- ❌ 已知**单个** ticker → **stock-research**(full=全量报告 / lite=快速卡)。
- ❌ 港股/美股全市场:本期不支持。

## 前置
- 在**项目根目录**运行;akshare/tushare/lightgbm 已装(venv-only,**务必 `uv run --no-sync`**);`.env` 有 `TUSHARE_TOKEN`(默认源)+ `FRED_API_KEY`(L4 取数)。默认中文。
- **召回权重**:`weights.json`(`factor_lab calibrate` 产;命令见常见坑节)。**regime 分桶权重**(`--regime-aware` 用):`factor_lab` `harvest` 后 python 里 `fl.calibrate_regimes()` → `weights.json` 增 `regimes` 块;重标定一律走 `retro.recalibrate_and_log`(快照+changelog 可回滚)。L2 不用模型(见铁律 / STAGES.md 核心世界观节)。
- **闭环(开跑前补跑复盘)**:先 `uv run --no-sync python -m autoresearch.learning.retro pending`(慢环,D+2)与 `uv run --no-sync python -m autoresearch.learning.t1_review pending`(**快环,D+1 判断层复盘**,2026-07-17 起);有欠账 → 先用 **scan-retro**(含快环 t1-review workflow)补上再开始今天的扫描。连续 0 买时看对照读数:`uv run --no-sync python -m autoresearch.learning.zero_buy_ledger`。
- **一致预期积累(每日 1 拉)**:`uv run --no-sync python -m autoresearch.research.consensus pull <date>`(tushare `report_rc` 限频 **1次/小时**,历史回补不可行);`status` 看进度。**验证门:积累 ≥60 日后 factor_lab 验 IC(两半稳+符号一致)才谈入 composite**。
- **token 真计量**:无需任何前置(不用配 env、不用特殊启动)——跑完在 CP7 直接跑 `usage_harvest`(命令见下方直播契约)。OTEL 那条路已于 2026-07-27 退役。
- 用户对报告的反馈用 **feedback** skill 记。

## 流程(6 段)

> **编排真身 = 两段 workflow + 主会话收尾**:① `.claude/workflows/scan-market.js`(Prelude→L3→L4-prep;默认流式 L4,返回 `{dispatch, dispatch_batches, task_book, meta}`)→ ② 主会话**一次性全派** `.claude/workflows/l4-stock.js`(`l4_tasks batches` 单批全量 pending,一条消息 N 个 Workflow 调用全部派出——详见步骤 4)；每票先过 `_l4_tasks.json` preflight，再让 slim 与 intel 并行，随后 card→(≥OW)双复核。单票失败只改变本票状态，不重跑已成功票 → ③ **task_book 全 SUCCEEDED** 后跑步骤 5 的 assemble/GATE4/计量回填。`streaming_l4=false` 才回到旧批量 GATE3。**正常跑动直接用 workflow**;以下命令留作调参/单步重跑入口。操作模板分驻:市场研判在 `macro-research/macro-playbook.md` 末节、L4 决策卡在 stock-research 的 `lite-playbook.md`;**各阶段机制/参数/实证读数**见 `STAGES.md`。
>
> **进度可视化(必做,2026-07-12 用户反馈"跑起来主对话一片空白")**:L4 派发后挂一个 Monitor —— 
> ```
> Monitor(command: "uv run --no-sync python -m autoresearch.scan.l4_watch <date> --watch",
>         description: "L4 逐股出卡", timeout_ms: 3600000, persistent: false)
> ```
> `autoresearch.scan.l4_watch`(确定性读盘,零 LLM)**只认 `_l4_tasks.json`**:某票 status 进终态(SUCCEEDED 且 card hash 已记 / FAILED)才播一行 `🃏 k/N 代码 名称 → 评级`;全部终态自动退出,收尾附「ensemble 折回待结算」提示。这**就是 CP5**,不用再自己轮询卡片。
> ⚠️ **它播的是卡片评级,不是终评**:`sell_review`/`ow_review` 的折回发生在 assemble 之后(2026-07-28:688766 卡片 UW、复核中位 Hold)——见到 `↩️` 行就等 assemble,别照卡片下结论。
> **重启只播增量**(Wave10 A8):消费进度记在 watcher 自己的 `outbox/l4_watch_cursor.json`(按 `--consumer-id` 分栏,默认 `l4_watch`),Monitor 被杀后重挂**不会**把已播的票再播一遍。要从头重播必须显式 `--replay-all`;游标损坏时程序**报错退出(rc=2)并要求人工选择**,不静默当空(静默当空 = 悄悄重播一整轮,而你以为那是新事件)。task_book 仍是任务状态的唯一事实源,播报不写它一个字节。
> ⚠️ **L4 之前的阶段没有 Monitor**:靠 CP0-CP4 的主动播报(下表)。前任 `scan.progress` 靠产物存在性猜阶段、分不清「在跑/被跳过/挂了」,累犯误报三次(2026-07-17 两次 + 07-28 GATE1 未过就报「L3 精排中」),已于 Wave8 退役。真信号一律以 workflow 的 `journal.jsonl`(每 agent 一条 `started`/`result`)为准。
>
> ### 过程直播契约(必做,2026-07-25 用户反馈"各环节展示不够优雅完整")
>
> Monitor 只是兜底(见上:它靠存在性反推、有误报前科)。**主会话必须在下列 8 个检查点主动向用户播报**——素材全是零 LLM 的现成产物,只转播不加工,别自己编数:
>
> | # | 时机 | 播什么 | 怎么拿 |
> |---|---|---|---|
> | CP0 | Stage0 完 | regime + 温度 + 策略师定调句 | `market_pack.json` + `market_view.md` §1 首句 |
> | CP1 | GATE1 过 | **前奏汇总屏全文**(12 步 ✓/✗ + 预热状态 + 当日件建议行) | Read `context/scan/<date>/_prelude_summary.md` **全量转播** |
> | CP2 | 行业 brief 齐 | 每个行业一句地形定调 | 各 `sector_briefs/*.md` 地形段首句 |
> | CP3 | GATE2 过 | **入围名单逐只**(代码/名称/行业)+ 被 pass1 切掉的影子名单 | workflow 的 `L3入围` 日志 + `_l3_pass1_cut.csv` |
> | CP4 | L4 派发 | 派发 N 股 + 预算旗 + intel 开关 + 📌保送名单 | workflow 日志(含 `📌 保送票` 行) |
> | CP5 | L4 进行中 | **每出一张卡播一行**:k/N 代码 名称 评级 | `l4_watch` Monitor 自动播(上方),主会话不用管 |
> | CP6 | L4 全完 | 评级分布 + 停因分桶 + OW三门直方图 | `uv run --no-sync python -m autoresearch.scan.render <date> --view gate_hist` |
> | CP7 | GATE4 过 | 买单/0买判词 + 产物路径 + 分段耗时 + **token 真计量** | `summary.md` 摘录 + `--view timing` + `usage_harvest`(下方) |
>
> **CP7 的 token/成本计量**:命令见步骤 5 的五条批次(含配置生效对账 `usage_reconcile`)。表覆盖**主会话 + subagent**(按 message.id 去重,分 input/output、cache read、5m/1h write、模型、effort、失败/重试/废弃);成本按公开计价倍率**加权**——**别拿原始 token 总数判断"贵在哪"**(haiku 壳加权占比高但 $ 是 opus 零头)。播报须带覆盖声明,公开价估算 ≠ 实际账单;缺 JSON 写 `UNMEASURED`,**不能写 `$0`**。
>
> 随时可调(零 LLM,几秒):`uv run --no-sync python -m autoresearch.scan.render <date> --view menu_health|gate_hist|timing|funnel`。
>
> **唤醒纪律(Wave8 A4;07-28 实测主会话独占 $30.50 = 全场 48.7%,双倍击穿 25% 挂账线)**:每次唤醒的 cache 读都按全上下文计费,所以 —— 派发回合一次性全派、收通知回合**只做一件事**(领通知,只领不播),不产出分析文字;CP2 与 CP3 合并为一次播报;workflow 完成通知里的 args 回显(~2KB)不复述。CP0/CP1/CP4/CP6/CP7 照常播。
>
> **CP5 已由 `l4_watch` Monitor 承担,主会话不要再自己轮询卡片。** 旧做法(`ls -t details/*.md` + grep Rating)基于「卡文件存在 = 该股完成」——**该前提已被 2026-07-28 证伪**:601319 的卡先落草稿(UW)后改终稿(Hold),按存在性播报会播出一个从未成立的评级。完成态只由 `_l4_tasks.json` 定义(W8-6/W8-7 同一条纪律)。

0. **前奏一键**(workflow Prelude 相位的确定性部分):
   ```bash
   uv run --no-sync python -m autoresearch.scan.prelude <YYYY-MM-DD>
   ```
   跑完全部确定性前奏(attribution 刷新/retro pending 列出/consensus 拉/universe/日历/菜单·L4预算·哨兵建议/journal 等 ledger 刷新,逐件见 STAGES.md 闭环层表;观察单日检已退役 fb_20260714_002)。各步失败不阻断,末尾汇总屏含 **📐/🔁/🚪 当日件建议行**(含「禁注」的行勿贴)。
   - **夜间预热**:交易日 19:30 launchd 自动跑(湖预拉+温度)。当天跑没跑看汇总屏的「预热(夜间):✓/✗」行;安装/实测见 STAGES.md『运维细节』。
0.5. **市场研判**(workflow Prelude 相位并行调用):`uv run --no-sync python -m autoresearch.scan.frame <日期> --json-out context/scan/<日期>/market_pack.json` 拿湖派生 market_pack → 一个 `Agent(subagent_type='macro-brief')` 写 `market_view.md`(模板见 macro-playbook 末节;地形段喂 L3/L4,操作基调/漏斗读数只进 L5)。
   ⚠️ **配置必传**:`user_config`(真身 `scan_config.jsonc`,**.jsonc 非 .json**;回显落 `user_config_echo.json`)必须随 Workflow `args.config` 传入,并在步骤 4 作为每股 `args.cfg` 原样透传。**传 `{}` = 静默关 intel + 全体 agent 掉回缺省 effort**(07-21 事故详情见 STAGES.md『运维细节』)。Wave11 起为结构性强制:空 config 直接 throw,离线试装用 `allow_empty_config:true`。
1. **L0 选集 + L1 召回 + L2 粗排**(全确定性,零 token;workflow Prelude 相位):
   ```bash
   uv run --no-sync python -m autoresearch.scan.universe [YYYY-MM-DD] --regime-aware [--source tushare] [--recall-n 1000] [--l2-n 200] [--cap-floor 30] [--exclude-bj] [--recall-mode multi|composite] [--recall-channels a,b,c] [--l2-sector-cap 0.20]
   ```
   → `L1_recall_top1000.csv`+`L1_channels.csv`+`L2_gbdt_top200.csv`+`sectors.csv`+`meta.json`(channel/floor 参数见 STAGES.md L1/L2 节)。
2. **过目 + 日历**(单步重跑入口;观察单日检已退役 fb_20260714_002,勿再跑):
   ```bash
   uv run --no-sync python -m autoresearch.scan.calendar <date>
   ```
   菜单体检(`autoresearch.scan.menu.menu_health`)由 L5 自动嵌;出现 ⚠️菜单病 时提前给用户预期。
2.2. **哨兵决策**(确定性建议,人拍板):
   ```bash
   uv run --no-sync python -m autoresearch.scan.menu <date>
   ```
   打印 `[sentinel]` 行(判据见 STAGES.md L2 节);建议哨兵档时只跑日历+步骤 5(跳 L3+L4,省 ~70% token/~35 分钟)。
   - ⚠️ **哨兵只问「今天有没有值得买的」,不含「持仓要不要动」**。`pinned.jsonc` 非空时必传 `force_full: true` 覆盖哨兵,否则持仓拿不到当日卖/持卡(07-17、07-28 两次实测见 STAGES.md『运维细节』)。
2.5. **市场研判兜底**(仅当 0.5 未跑):同 0.5,读 `autoresearch.scan.market.market_pack(scan_dir)` 回退口径(L2 后)。
2.7. **行业 brief**(与步骤 3 证据取数并发;workflow L3 相位):
   ```bash
   uv run --no-sync python -m autoresearch.sector.reuse <date> --apply
   uv run --no-sync python -m autoresearch.sector.pack <date>
   ```
   → 每行业一个 `Agent(subagent_type='sector-brief')`(机制/两段契约见 STAGES.md『旁路 · 行业 brief』节)。
3. **L3 精排**(两遍法;workflow L3 相位):证据取数(`harvest_l3_evidence`+`harvest_l3_news`)→ `l3_table_md(...)` 压紧凑表(内含 pass1 确定性分诊 200→~40,scan_config `pass1_target`;被切的是**影子**落 `_l3_pass1_cut.csv`,不代表判死)→ 一个 `Agent(subagent_type='l3-rank')` 通看 ~40 只深比较,出 **finalist tier 7–10 只**(`finalist:true`,按当天质量,宁缺毋滥不凑数)+ **bench**(`finalist:false`,仍全字段判断)→ `menu <date>` 拿 L4 预算(cap=min(10,预算))→ `merge_l3_finalists_v3`(conviction≥75 误杀保险补入 / <55 剔除 / 健康画像守卫)→ `finalists.csv` + `_l3_bench.csv`。参数/rubric 维度/token 经济见 STAGES.md L3 节。
4. **L4 研究**(token 大头;默认流式、每股独立可恢复)——确定性准备(l4-prep)仍在 scan-market.js 的 L4-prep 相位:质押旗/席位·催化·日历生产者先行(机制见 STAGES.md L4 节;**TTL 复用已于 2026-07-29 用户裁定 R5 整体退役**——不再有任何票跳过研究,评级稳定性改由昨卡回声承接)→ 落稿(单步重跑入口):
   ```bash
   uv run --no-sync python -m autoresearch.scan.agents.l4_card pledge <date>
   uv run --no-sync python -m autoresearch.scan.agents.l4_card prompts <date>
   ```
   → scan-market.js 返回 `{dispatch, dispatch_batches, task_book, meta}` 后，主会话**一次性全派**
   `Workflow({scriptPath: '.claude/workflows/l4-stock.js', args: {date, code, name, sector, cfg, pinned, dossierSummary}})`：
   - **一次性全派**(Wave11-C,恢复 fb_20260714_003「别分 wave」原意):`l4_tasks batches`
     现返回单批全量 pending —— 主会话**一条消息 N 个 Workflow 调用**全部派出;📌 pinned 排
     列表最前只为 watch 可读性,无先后语义。tushare 取数由每票 prepare 内的 K 槽信号量排队
     (K=caps.tushare−限频扣减),intel/card 不排队即刻起跑。
   - **完成判据 = task_book 全 SUCCEEDED**(不变)。`batches` 为空**不是**完成——可能都
     还在飞(见 `running`),也可能有票停在 `BLOCKED`(非瞬时错误直接置该态,
     `batches`/`running` 两边都不放)。**不要**用「两个数组皆空」当完成态的充分
     条件;唯一权威判据是 task_book。收完成通知的回合只领不播(唤醒纪律不变);
     单票失败只改本票状态;重放仍只派 `l4_tasks batches` 返回的未完成票。
   - 回滚杆:`scan_config.jsonc` 设 `budgets.concurrency.l4_stock=4` 只收窄单批容量——
     `l4_tasks batches` 会重新按 4 只一批切,pending 较多时需分批取用逐批派发(回到
     更早的「每批 ≤4、分批派」模型);它**不能**恢复「每完成一只补派一只」的真滑窗
     节奏,那段调度逻辑在主会话侧,不受 config 控制、未随此次改动保留。
   - 首跑后必看:`uv run --no-sync python -m autoresearch.scan.l4_tasks stats <date>`
     (RATE_LIMIT/排队等待读数;429 率 >10% 才考虑 stagger,YAGNI)。
   `pinned` 取自 dispatch-plan 的 `meta[code].pinned`。**派发前对照 workflow 打印的「📌 保送票 N 只」行逐一核对**:名单里的每只必须带 `pinned: true`。漏传 = 持仓 SELL 双复核整段不跑(2026-07-21 实测 300857/601869 中招);probe 9 `sell_review_missing` 只能事后 warn,拦不住。
   `dossierSummary` 取自 dispatch-plan 的 `meta[code].dossier_summary`(无档案=空串);漏传只退化为「intel 无已知底」= Wave3 前行为,不影响正确性。
   (**cfg = 步骤 0.5 frame 回显的 `user_config` 块原样透传,勿传 `{}`**——空 cfg 静默关 intel/降 effort,见 0.5 节 07-21 事故注)(degraded=复核 run 不齐时不折回、报告强制人裁)
   每股链内:**preflight → (本票 slim ∥ intel) → l4-card 决策卡 →(≥OW)2 独立复核 run 取中位只向下折回 → success hash 校验**。`_l4_tasks.json` 保存 prompt/slim/card hash 与尝试次数；只对 `RATE_LIMIT` / `CONNECTION` / `TIMEOUT` 瞬时错误给第 2 次尝试，schema/contract/data-integrity 错误直接阻断本票。重放先跑
   `uv run --no-sync python -m autoresearch.scan.l4_tasks batches <date>`，只派返回的未完成批次；不要删任务簿或重跑成功票。复核落
   `_ensemble_<code>.json`(assemble 合并读)。卡模板/契约烤进 `.claude/agents/l4-card.md`。
   **活体情报站**(config `l4_intel.enabled`):l4-stock 的 Intel 相位,sonnet·max 结构性盲(prompt 只给码/名/行业/日期)盲搜六面落 `_l4_intel_<code>.md`;卡 P3 先读 intel、自发网查降 ≤1 验证,缺文件自动回退卡内网查(presence-gated)。⚠️ **铁律:卡片对 intel 的价格类断言必须与 verified OHLCV 对账后才可采信**(捏造前科见 STAGES.md『运维细节』)。
5. **L5 整合**(全部 l4-stock workflow 完成后,主会话直接跑;哨兵档跳过 L3/L4 后也走这里)。
   **五条在一个 shell 批次跑完再播 CP7**，其中 `<run_id>` 是 assemble 打印的报告目录名:
   ```bash
   uv run --no-sync python -m autoresearch.scan.assemble <date> && \
   uv run --no-sync python -m autoresearch.scan.gates gate4 <date> && \
   uv run --no-sync python -m autoresearch.trace.usage_harvest --session <本次 sessionId> \
     --out reports/scan/<run_id>/token_usage.md \
     --json-out context/scan/<date>/_token_usage.json && \
   uv run --no-sync python -m autoresearch.trace.usage_reconcile <date> \
     --json-out context/scan/<date>/_usage_reconcile.json && \
   uv run --no-sync python -m autoresearch.scan.post_run <date> observe \
     --report-dir reports/scan/<run_id>
   ```
   → **`reports/scan/<YYYYMMDD_HHMM>/`**:`summary.md`(含成本与时延观测)+ `details/`+ `token_usage.md`+ `trace/`。成本/墙钟晋升在 **10 次真实扫描**前恒为 `IMMATURE`；看中位成本与 P50/P90，禁止拿单次最佳 run 宣称达标。预算超线只写 warning/`DEGRADED`，不截断研究、不制造 BUY。
   **汇报(CP7)**:漏斗 + buy-list(评级/目标)+ 分段耗时表(`render --view timing`)+ 诚实局限;0 买日必须播**停因分桶**(早停 N 张〔按停因〕/ 满卡未达 OW M 张),**不要再说「无一过 ≥OW 三门」**——早停卡按定义不写三门段(07-21 实测 12 卡里 6 张早停、仅 2 张可解析三门),那句话不被数据支持。
   **配置生效对账**(Wave11 B4,第五条命令的产物):`usage_reconcile` 把当日 `user_config_echo.json`(期望)× `_token_usage.json`(`usage_harvest` 实测)逐 role 对上,`ok=false` 时把 mismatch/wire_break 直接打进 CP7 播报的 stdout——**这就是当日结论**,不经 `self_review` 转手(`self_review` 的同名 check 时序上跑在 `usage_harvest` 之前,只能读**最近一份既有**结果,通常是上一次 run;三条精度边界——时序 / `general-purpose` 壳类只做集合断言 / effort 是请求参数不是推理深度——写在模块 docstring 与 `_usage_reconcile.json` 报表头,勿在播报时脑补掉)。exit 恒 0,不影响 `post_run` 是否执行。

6. **覆盖档案维护**(盘后,不占扫描窗;presence-gated,池空则整段跳过)
   ```bash
   uv run --no-sync python -m autoresearch.dossier.pool <date> --status        # 看 pending_init 队列
   uv run --no-sync python -m autoresearch.dossier.reconcile <period>          # 季度对账(中报/年报披露后,如 20260630)
   ```
   - **建档队列**:`pending_init` 里的票逐只派 `.claude/workflows/dossier-init.js`(**≤3 只/晚**,每只 ~10-20min);新 agent def 落盘当会话派发会 `not found`(会话启动装载),等热载或换会话。可带 `args.cfg`(scan_config 的 `agents` 回显,透传 dossier_init/gp_shell/gp_shell_json 的 model/effort;省略 = 用 workflow 内 AGENT_DEFAULTS 缺省)。
   - **prelude 会替你催**:📐 = 该报告期未对账、🕰️ = 档案 >90 日未全量刷新;解药是跑一次**成功的季度对账**(唯一写 `last_refresh` 的路径)。重做首覆的正确姿势(`builder --force` 而非不存在的 `dossier-init --force`)与探针语义见 STAGES.md『运维细节』。

## 实验治理(行为变更的唯一生产入口)

涉及召回、L3、门、早停、ensemble、评级、Token 或速度的 challenger，先以
`autoresearch.learning.experiment_registry` 登记不可变定义和**稳定基线**回滚
指针，再由 `autoresearch.learning.promotion` 读取显式 measurement facts 检查
研究、决策、Token、速度、架构五项守卫。状态只允许按
`PREREGISTERED → RECOMMENDED → APPROVED → ACTIVE` 前进；观察窗通过后成为
`STABLE_CANDIDATE`，任一守卫失守成为 `ROLLBACK_RECOMMENDED`。

- `IMMATURE`、`UNKNOWN`、`FAIL` 均不能批准或激活；0 BUY 不是失败，也不是放松门
  的理由。
- `RECOMMENDED` 只是软件建议；`approve` 和 `activate` 必须分别记录**人工批准**
  与操作者身份。观察器 `autoresearch.learning.rollback_watch` 也只推荐接受或
  回滚，不自动改生产配置。
- 每个 trial family 同时最多一个 `ACTIVE`；激活不覆盖稳定基线。人工执行生产
  切换/回滚后，再用 `accept`/`rollback` 记审计事实。
- 当前 challenger 的真实前向样本尚未达到统一晋升门，全部保持影子态；单测通过
  只证明控制面可用，不证明研究效果成熟。

最小操作序列（registry 默认落
`context/learning/experiments/registry.json`，facts/spec 均为 JSON）：

```bash
python -m autoresearch.learning.experiment_registry baseline ...
python -m autoresearch.learning.experiment_registry register --spec <spec.json>
python -m autoresearch.learning.promotion evaluate <id> --facts <facts.json>
python -m autoresearch.learning.experiment_registry approve <id> --approved-by <人>
python -m autoresearch.learning.experiment_registry activate <id> --activated-by <人>
python -m autoresearch.learning.rollback_watch observe <id> --facts <facts.json> --run-id <run>
python -m autoresearch.learning.experiment_registry rollback|accept <id> ...
python -m autoresearch.learning.experiment_registry report
```

完整字段、状态机、成熟门和回滚语义见 STAGES.md「实验晋升与回滚控制面」。

## 铁律
- **确定性层零 LLM**:L0/L1/**L2**/L5 全 pandas,不在筛选里编数、不预测。
- **召回宽、判断深**:L1 高召回 → L2 分层多样性采样收口(给均衡菜单,非 alpha);真正的多空取舍在 L3 holistic 精排 + L4 决策卡。
- **L3/L4 必须 subagent**:L3 一个 holistic agent(独立 context)+ L4 每只独立 context(每股一个 `l4-stock` workflow),只回传紧凑结果,否则撑爆主线;标准编排路径见流程节顶部(scan-market.js 前段 + N×l4-stock.js + 主会话收尾)。
- **每只 finalist 走 stock-research lite 档**——继承其铁律(数字出自 slim context、五档评级、EV/R:R、`FINAL TRANSACTION PROPOSAL`、诚实局限)。
- **中间名单全 staging**(L2_gbdt / L3_evidence / finalists),L5 发布到 `trace/` 留溯源;re-run 友好。
- **诚实收尾**:召回/粗排是启发式 + fwd_2_oc 超短主尺 IC 校准/训练(2026-07-10 裁定;随 regime 漂移);L3/L4 是 Claude 推理产出;"仅供研究,非投资建议"。
- **性能开关不拥有评级**:现仅存 `performance.streaming_l4`(默认 true;回滚设 `false`)。任何开关都不得改 finalist cap、rubric 三门、`fwd_2_oc` 或 BUY 数量。
  Wave10 B4 退役两个:`stable_context_blocks`(离线 benchmark 收益 4.0% < 10% 门 → ABANDONED)、`sector_brief_mode`(它会改变 L3 看到的上下文、**可能改变 finalists**,按本铁律它根本不是性能开关,且无获批 research experiment → ABANDONED)。
- **模块归属(Wave 4)**:`agents/l3_select.py`、`agents/l4_card.py`、`scan/assemble.py` 仅保留旧 import/CLI 兼容；新代码分别直连 `scan/l3/*`、`scan/l4/*`、`decision_finalize`、`report_sections`、`publisher`、`post_run`。不要把业务逻辑重新塞回适配器，也不要让 L3/L4 反向 import reporting。

## 常见坑
- 必须 `uv run --no-sync`(不误删 venv-only 的 akshare/tushare/lightgbm)、仓库根目录。
- **默认 `--source tushare`**(东财 push2 常被网络封锁);需 `TUSHARE_TOKEN`。缺端点权限的富因子自动降级 NaN、打分重归一。
- **召回权重 / L2 采样**:`weights.json` 缺失 → 内置先验(能跑但弱);L2 不用模型。改因子/组后只需重跑 L1 校准:`factor_lab harvest`→`calibrate`(线性权重)→`eval`(复核 IC)。L2 为何不做模型的实证见 STAGES.md 核心世界观节。
- `context/`、`reports/` 已 gitignore;别误提交大文件。

---
name: scan-market
description: "Use when the user wants to scan the WHOLE A-share market to discover buy-worthy stocks AND strong sectors — 「扫描全A股」「全市场选股」「哪些板块值得买」「find the best A-share buys」. Deterministic L0-L2 funnel + Claude L3/L4/L5; artifacts → reports_<engine>/scan/<run_id>/. NOT for: one named ticker (→ stock-research; 持仓单票复核走其 lite 档), cross-asset macro (→ macro-research). Project-local."
---

# scan-market — 全 A股六段漏斗扫描(挖掘个股 + 板块,零付费 API)

## session_v1 编排入口

开发/验收期显式选择新编排时使用
`python -m autoresearch.session_agent begin --orchestration session_v1 --request-file <request.json>`，执行
`begin → next → claim → execute/宿主研究 → submit → finish`。扫描的动态行业、L3、L4 和复核
任务由冻结 expansion 生成；原 gate、taskbook、评级和发布器仍是业务真值。宿主能力不足会在创建
run 前返回 `HOST_CAPABILITY_REQUIRED`。`session_agent --orchestration legacy` 只返回
`LEGACY_ENTRYPOINT_REQUIRED`，绝不代跑旧 Workflow；确需回退必须显式进入标为
`LEGACY_ORCHESTRATION_FALLBACK` 的旧入口并记录原因，不能给旧执行贴 `session_v1` 标签。
当前双宿主真实验收为 `INCOMPLETE`，新入口仅作显式 PILOT，默认仍保留 legacy fallback；
四种 run mode 的合成重放通过不等于真实宿主放行。`finish` 后必须对机器返回的 canonical
报告路径运行 `uv run --no-sync python -m autoresearch.session_agent verify-report --report-path <PATH> --expected-run-id <RUN_ID> --level full`，
按结果分别声明编排、发布、完整性与重放；未绑定改写返回 `UNBOUND_REPORT`。

> 沿革见 git log;本文件 = 编排入口,机制/参数/实证读数快照见 `STAGES.md`(冲突以源码为准)。

## 核心原理

对 ~5,500 只逐个跑深度报告不可行(几亿 token)。本 skill 用**搜索/推荐系统式六段漏斗**:确定性层(零 token)收窄全市场到 ~200 → Claude holistic 精排到 ~30 → 只对这 ~30 跑 **stock-research(lite 档)决策卡** → 整合。**token 只跟最终深挖的几十只成正比**。

| 段 | 名称 | 引擎 | 作用 | 进→出 |
|---|---|---|---|---|
| **L0** | 选集 | 确定性 | 候选池+硬门(ST/退/停牌/次新+市值地板) | 全A→~5,500 |
| **L1** | 召回 | 确定性·多路 | 10 路 channel 各取 top → quota union(floor 保底多样性) | →1,000 |
| **L2** | 粗排 | 确定性·分层采样 | sector-neutral composite 排序+风格桶floor+sector cap;**不预测**(见 STAGES.md) | →200 |
| **宏观lite** | 市场研判(旁路) | Opus·单agent | 写 `market_view.md`;地形段喂L3/L4(防锚定) | 旁路·1份 |
| **L3** | 精排 | Opus-high·holistic | 通看~200比较选+真证据+channel共振+论点/红队/sentiment | →~30 |
| **L4** | 研究 | 一只=一个Opus subagent | 决策卡(P0简报→P1–P3表面→早停②→P4陷阱核→P5;`rubric_rating`派生评级) | ~29卡 |
| **L5** | 整合 | 确定性 | summary+buy-list+漏斗溯源 | 1份 |

## 何时用 / 不用
- ✅ 一次扫全市场,挖"值得买的票 / 强势板块"(A股)。❌ 已知**单个** ticker → **stock-research**。❌ 港股/美股全市场:本期不支持。

## 前置
- 在**项目根目录**运行;akshare/tushare/lightgbm 已装(venv-only,**务必 `uv run --no-sync`**);`.env` 有 `TUSHARE_TOKEN`+`FRED_API_KEY`。默认中文。
- **路径约定(引擎隔离)**:`$CTX`/`$RPT` = 本引擎工作区根(Claude→`context_claude`/`reports_claude`,Codex→`context_codex`/`reports_codex`;bash 块用 `CTX=context_${AUTORESEARCH_ENGINE:-claude}` 一行取值)。数据湖 `lake/` 两引擎共享;Read/Write 工具调用时把 `$CTX`/`$RPT` 代入具体目录名。python -m 命令不带路径参数时自动按引擎解析。
- **召回权重**:`weights.json`(`factor_lab calibrate` 产;命令见常见坑节)。**现在只有显式跑 `factor_lab calibrate` 才会变** —— 原夜跑自动重标定腿随闭环退役。regime 用法见 STAGES.md L1 节;L2 不用模型(见铁律)。
- **闭环已整体退役**(2026-08-21 用户裁定):`autoresearch/learning/` 整包、`scan-retro` 与 `feedback` 两个 skill、以及扫描路径上所有账本记账与回注腿全部删除。开跑前**没有**要补的复盘;L3/L4 的 prompt 不再吃任何历史账本派生的先验。细节与保留件见 STAGES.md「行为变更的入口」节。
- **一致预期**:`autoresearch.research.consensus pull <date>`(限频 1次/小时)。
- **token 真计量**:无需前置——CP7 跑 `usage_harvest`(命令见下方;OTEL 已退役)。

## 配置单一事实源(防流程漂移)

**全部用户可调参数只有一个家:`.claude/skills/scan-market/scan_config.jsonc`**(JSONC;按漏斗阶段排序,每键标【生效点】)。例外仅两个:保送票**清单**在 `pinned.jsonc`(策略 cap/TTL 仍在 scan_config),L1 因子**权重**在 `$CTX/factor_lab/weights.json`(由 `factor_lab calibrate` 产,不手编;自动重标定腿已随闭环退役)。

- **装载链**:`frame --json` 经 `autoresearch/scan/user_config.py` **白名单校验**后回显 → 随 Workflow `args.config` 传入(workflow 无文件系统访问)→ L4 每股 `args.cfg` 原样透传;确定性 CLI(universe/prelude/frame/sector.*)在入口经 `user_config.knob()` 读同一文件兜底。
- **优先级恒为**:CLI 显式 flag / 显式形参 > scan_config > 代码内建默认;删 key = 内建默认(parity)。

| 阶段 | 块 | 键 | 生效点 |
|---|---|---|---|
| 全局 | `budgets` | cache_hit_min·stage_cost_usd·stage_wall_seconds·min_real_scans·baseline_run·concurrency{tushare,web_search,web_fetch,l4_stock} | `scan/budget.py`(只告警不截断);并发帽 `scan/l4_tasks.py init` |
| Stage0 | `pinned` | cap·ttl_days | `scan/frame.py`(load_pinned→run_contract) |
| Stage0 | `agents` + `agent_engines` | 10 role→tier(闭集必须列全)+ Claude/Codex tier profile | `user_config.resolve_agent_bundle` → `_resolved_agent_config.json`(declared/runtime/resolved) → workflow/Codex project agents + `usage_reconcile` 对账 |
| L0 | `l0` | cap_floor_yi·include_bj·source·min_amount_yi·min_list_days | `scan/frame.py build_market_frame`(单一代码路径)+ `universe.run`(meta 记生效值) |
| L1 | `funnel` | regime_aware·recall_n·l2_n·recall_channels(10路,2026-08-21 重开 reversal_confirm、2026-08-22 加 lowturn)·channel_quotas(现值 value312·momentum188·heat112·healthy112·growth112·main_fund150·reversal_confirm150·lowturn120)·channel_floors | `universe.run`(`_funnel_overlay`+`knob`);regime_aware 另生效 `prelude.run_prelude`(生产路缺省 true) |
| L2 | `l2` | sector_cap·(floors) | `universe.run` → `l2_stratify.select_l2` |
| 旁路 | `sector` | reuse_ttl_days·max_briefs | `sector/reuse.py main` / `sector/pack.py main` |
| L3 | `l3` | two_pass·pass1_target·finalist_max·lowturn{enabled,阈值×8,pass1_cap} | `scan/l3/prompt.py prepare_l3_table`(旗列)/ `scan/l3/triage.py`(pass1 强留)/ `scan/l3/merge.py write_finalists`(守卫⑥);谓词真身 `common/turnup.lowturn_flag` |
| L4 | `l4_intel` | enabled·max_queries | `l4-stock.js`(intelOn/maxQ;**缺块=intel 关**)+ `scan/l4/intel_status.py` |
| L4 | `performance` | streaming_l4 | `scan-market.js`(任务簿流式 vs 旧批量 GATE3) |
| 精排 | `l3.composite_seat` | enabled·m | `scan/l3/merge.composite_seat_cfg` → ① `write_finalists` 的 `inject_composite_seats`(守卫⑨:当日 L2 composite 最高的 m 只强制进 finalists,`guard=composite_seat`)② `l3/prompt.prepare_l3_table` → `triage` 的 ①b 强留(让 l3-rank 真判到它们)。回滚 = `enabled:false` |
| 收尾 | `relative_buy.pool` | finalists·composite | `relative_buy.configured_pool` → `post_run.publish_run_observation` → `build_decision(pool=…)`。`composite` = BUY 只在守卫⑨ 的证据席里选、按 composite 分排(2026-08-26 §3 路A)。回滚 = 改回 `finalists`(**只回滚候选池;A2 的 UW/SELL 硬门对两个池都生效**) |
| 收尾 | `relative_buy` | mode·exclude_pinned·activate_date | `scan/post_run.py publish_run_observation` → `relative_buy.write_decision`/`verify_decision`(2026-08-19 裁决表 A1/A2:mode=active 正式接管 BUY、exclude_pinned=true 剔📌;**activate_date 自 2026-08-21 起无消费点** —— 原生效点 `learning/legacy_freeze` 随闭环删除,该键仅作转正日记录) |
| 收尾 | `retention.bind_transcripts` | true/false(默认 true) | `scan/post_run.py publish_run_observation`(决策校验之后、`retain` 镜像 staging 之前)→ `transcript_binder.safe_bind_run`,把本 run 研究 agent 实际读/搜/写过什么绑定进 capsule。**逐条绑定失败都有账**(单条冲突/源不可读只把那一行标 ERROR 并留原因,不挡其余票、不挡业务发布);关掉或无 active run 时仍写一份 `enabled:false`+原因的 `_transcript_bindings.json`,不清除已有证据。**一条 BOUND 不是研究完整的证明**——分母可能是产物推导的下界,区段可能只是 partial,完整性结论仍看 capsule 自己的 completeness 校验,不能拿这份报告的 enabled/BOUND 直接当"证据完好"。回滚 = 改回 `false`(只停止新增采集) |

**防漂移铁律:**
1. **白名单外的键 load 即 raise**(`user_config.py`)——写错键名当场炸,不静默失效;错型同样 raise(`_KNOB_TYPES`)。
2. **新增参数三件套**:进 `user_config.py` 白名单 + 有真实消费点(grep 调用链)+ 测试锁(`tests/scan/test_config_knobs.py` / `test_user_config.py`)。三缺一不许合。
3. **改值 ≠ 无害**:召回/L3/L4/评级类旋钮的取值变更属行为变更 —— 先想清楚、留测试锁再改;config 是放参数的地方,不是绕过判断的后门。
4. **不入 config 的清单**(行为归属,代码持有):L2 风格桶 floors 明细(`l2_stratify.DEFAULT_FLOORS`)、menu 五面旗/L4 预算档 30/22/15(`scan/menu.py`)、conviction 守卫阈 75/55(`l3/merge.py`)、rubric 三门与早停(`l4/rubric`)、intel `hard_cap=30`(`l4/intel_guard.py`)、主尺 `common.ruler.MAIN_RULER`、哨兵档位判据、L1 各路信号定义。

## 流程(6 段)

> **编排真身 = 两段 workflow + 主会话收尾**:① `.claude/workflows/scan-market.js`(Prelude→L3→L4-prep)→ ② 主会话**一次性全派** `.claude/workflows/l4-stock.js`(详见步骤 4)→ ③ 步骤 5 整合。**正常跑动直接用 workflow**;以下命令留作调参/单步重跑入口。
>
> **进度可视化(必做)**:L4 派发后挂一个 Monitor ——
> ```
> Monitor(command: "uv run --no-sync python -m autoresearch.scan.l4_watch <date> --watch",
>         description: "L4 逐股出卡", timeout_ms: 3600000, persistent: false)
> ```
> `l4_watch`(确定性读盘,零 LLM)只认 `_l4_tasks.json`:某票 status 进终态(**SUCCEEDED 且 card hash 已记 / FAILED / BLOCKED**)才播——SUCCEEDED 播评级,FAILED/BLOCKED 播错误类别(**BLOCKED = 非瞬时错误的直接终局,判该票废了,不是「出了卡」**);全部票进终态(含 BLOCKED)才算 done——**判据只认 `_l4_tasks.json`,不是「卡文件存在」**。这**就是 CP5**,不用再自己轮询卡片,也别照卡片评级下结论(`sell_review`/`ow_review` 折回在 assemble 之后)。
> **重启只播增量**:进度记 `outbox/l4_watch_cursor.json`,重挂不重播;要重播须 `--replay-all`;游标损坏报错退出,要求人工选择。
> **L4 之前没有 Monitor**:靠 CP0-CP4 主动播报,真信号以 `journal.jsonl` 为准。
>
> ### 过程直播契约(必做)
>
> Monitor 只是兜底。**主会话必须在下列 8 个检查点主动播报**——素材全是零 LLM 现成产物,只转播不加工:
>
> | # | 时机 | 播什么 | 怎么拿 |
> |---|---|---|---|
> | CP0 | Stage0 完 | regime + 温度 + 策略师定调句 | `market_pack.json` + `market_view.md` §1 首句 |
> | CP1 | GATE1 过 | **前奏汇总屏全文** | Read `_prelude_summary.md` **全量转播** |
> | CP2 | 行业 brief 齐 | 每行业一句地形定调 | 各 `sector_briefs/*.md` 地形段首句 |
> | CP3 | GATE2 过 | **入围名单逐只** + 被切掉的影子名单 | workflow `L3入围` 日志 + `_l3_pass1_cut.csv` |
> | CP4 | L4 派发 | 派发 N 股 + 预算旗 + intel 开关 + 📌保送名单 | workflow 日志 |
> | CP5 | L4 进行中 | 每出一张卡播一行:k/N 代码 名称 评级 | `l4_watch` Monitor 自动播 |
> | CP6 | L4 全完 | 评级分布 + 停因分桶 + OW三门直方图 | `autoresearch.scan.render <date> --view gate_hist` |
> | CP7 | GATE4 过 | **`brief.md` 原文全量转播** + 产物路径 + 分段耗时 + **token 真计量** | Read `$RPT/scan/<run_id>/brief.md`(≤3KB)+ `--view timing` + `usage_harvest` |
>
> **CP7 播报 = 读 brief 原文,不复述**:`brief.md` 是确定性模板产物(零 LLM,六节 ≤3,000B,同 run 重放 byte 稳定)——主会话再总结一遍只会新增编数面,还要多一次对账。原文贴出 + 附 `$RPT/scan/<run_id>/` 路径即可;要展开某一节再读 `summary.md`(详细版)。brief 缺席 = `self_review` 的 `brief·缺失` **warn**(GATE4 照过,但 warn 进 `gate_fires.csv` 且照样播),如实播报,**不要拿 summary 顶替**。
> **CP7 计量**:命令见步骤 5(含 `usage_reconcile`)。覆盖主会话+subagent,成本按公开计价倍率加权;缺 JSON 写 `UNMEASURED`,**不能写 `$0`**。
> **唤醒纪律**(cache 读按全上下文计费,主会话曾独占近半全场成本):派发一次性全派、收通知只领不播,不出分析文字;CP2/CP3 合并播报,CP0/CP1/CP4/CP6/CP7 照常播。

0. **开场:先领 run_id,再取任何一个数**(2026-08-28 法证 capsule):
   ```bash
   export AUTORESEARCH_ENGINE=codex   # Claude 会话下无需设置
   RUN_JSON=$(uv run --no-sync python -m autoresearch.trace.capsule begin scan-market <YYYY-MM-DD> \
     --engine "$AUTORESEARCH_ENGINE" --config-file .claude/skills/scan-market/scan_config.jsonc)
   RUN_ID=$(printf '%s' "$RUN_JSON" | jq -r .run_id)
   export AUTORESEARCH_RUN_ID="$RUN_ID"
   ```
   `begin` **必须先于任何取数**:它先落 RunContract v3 + 代码/环境/prompt 身份快照,再公布 run 目录 ——
   配置写错的 run 因此不会留下一个无名孤儿。`RUN_ID` 随 `Workflow args.run_id` 传给 `scan-market.js`,
   再由它透传给每个 `l4-stock`;staging 从此按 **run** 分区(`$CTX/scan_runs/<run_id>/staging/<date>/`),
   同日重跑不再互相覆盖。
   上一次被 SIGKILL / 断电打断的 run 由 `prelude`/`prewarm` 开头自动冻结(只警告,不阻断);
   也可手动 `python -m autoresearch.trace.capsule recover`。
0.1. **前奏一键**:
   ```bash
   uv run --no-sync python -m autoresearch.scan.prelude <YYYY-MM-DD>
   ```
   跑全部确定性前奏 **12 步**(顺序与去留的单一事实源 = `prelude.STEP_NAMES`,别照抄本行:`consensus` 一致预期 / `temperature` 温度 / `universe` L0-L2 / `calendar` 日历 / `catalyst` 催化 / `menu` 菜单预算哨兵 / `l4_rejection` **L4 拒绝价值日读**(2026-08-22:滚动 40 日评级 rank-IC·≥OW 出现日数·三门 PASS−FAIL·finalist 超额,只给人看不喂 agent)/ `outcome_fill` 结果账本回填(2026-08-26,只记不学,读历史不读当日)/ `ledger_views` 运行日历+市场行+逐级 KPI(2026-08-28 G2/G3,同上)/ `dossier_pool` 覆盖池日检 / `news_catalog` 新闻目录体检 / `overseas` 隔夜窗海外事件日历(2026-08-29 D-2:风险可见性,**不喂判断层**));末尾汇总屏含 **⚡tripwire 持仓盯梢行(仅人看,勿贴给任何 agent)**。
   (2026-08-21 learning 层退役同批删掉 6 步:attribution 刷新 / retro 欠账 / t1 欠账 / 学习环健康三查 / 十本账本刷新 / GATE0 preflight。)
   - **夜间预热**:交易日 19:30 launchd 自动跑;看汇总屏「预热(夜间)」行,安装见 STAGES.md『运维细节』。
0.5. **市场研判**:
   ```bash
   CTX=context_${AUTORESEARCH_ENGINE:-claude}
   uv run --no-sync python -m autoresearch.scan.frame <日期> --json-out $CTX/scan/<日期>/market_pack.json
   ```
   → `Agent(subagent_type='macro-brief')` 写 `market_view.md`(地形段喂 L3/L4;模板见 macro-playbook 末节)。
   ⚠️ **配置必传**:`user_config`(真身 `scan_config.jsonc`,**.jsonc 非 .json**)须随 `args.config` 传入,步骤 4 每股 `args.cfg` 原样透传。**传 `{}` = 静默关 intel + 降 effort**;空 config 现直接 throw(详情见 STAGES.md『运维细节』)。
1. **L0+L1+L2**(全确定性,零 token):
   ```bash
   uv run --no-sync python -m autoresearch.scan.universe [YYYY-MM-DD]
   ```
   (L0/L1/L2 旋钮全部吃 `scan_config.jsonc`——含 `funnel.regime_aware: true`,不再靠记得敲 flag;CLI flag 只作单次覆盖。可选 flag/产物清单见 STAGES.md L1/L2 节)。
2. **过目 + 日历**(观察单日检已退役,勿再跑):
   ```bash
   uv run --no-sync python -m autoresearch.scan.calendar <date>
   ```
   ⚠️菜单病由 L5 自动嵌。
2.2. **哨兵决策**(确定性建议,人拍板):
   ```bash
   uv run --no-sync python -m autoresearch.scan.menu <date>
   ```
   打印 `[sentinel]` 行(判据见 STAGES.md L2 节);建议哨兵档只跑日历+步骤 5。
   - ⚠️ **哨兵只问「今天有没有值得买的」,不含「持仓要不要动」**。`pinned.jsonc` 非空必传 `force_full: true` 覆盖哨兵,否则持仓拿不到当日卖/持卡(见 STAGES.md『运维细节』)。
2.5. **市场研判兜底**(仅当 0.5 未跑):读 `autoresearch.scan.market.market_pack(scan_dir)` 回退口径。
2.7. **行业 brief**:
   ```bash
   uv run --no-sync python -m autoresearch.sector.reuse <date> --apply
   uv run --no-sync python -m autoresearch.sector.pack <date>
   ```
   → 每行业一个 `Agent(subagent_type='sector-brief')`(机制见 STAGES.md『旁路 · 行业 brief』节)。
3. **L3 精排**(两遍法):证据取数 → `l3_table_md` 压紧凑表(pass1 分诊,被切的是**影子**落 `_l3_pass1_cut.csv`,不代表判死)→ `Agent(subagent_type='l3-rank')` 深比较出 **finalist tier**(`finalist:true`)+ **bench**(`finalist:false`)→ `menu <date>` 拿预算 → `merge_l3_finalists_v3` 确定性守卫 → `finalists.csv`+`_l3_bench.csv`。阈值/rubric/token 见 STAGES.md L3 节。
4. **L4 研究**(token 大头;默认流式、每股独立可恢复)——l4-prep 先跑质押旗/席位/催化/日历生产者(**TTL 复用已整体退役,不再有任何票跳过研究**;见 STAGES.md L4 节)→ 落稿:
   ```bash
   uv run --no-sync python -m autoresearch.scan.agents.l4_card pledge <date>
   uv run --no-sync python -m autoresearch.scan.agents.l4_card prompts <date>
   ```
   → scan-market.js 返回 `dispatch_batches`/`task_book` 后,主会话**一次性全派**
   `Workflow({scriptPath: '.claude/workflows/l4-stock.js', args: {date, code, name, sector, cfg, pinned, dossierSummary}})`(一条消息 N 个调用全部派出;回滚杆不能恢复旧滑窗节奏,见 STAGES.md『运维细节』)。

   **完成判据 = task_book 全 SUCCEEDED**;`batches` 为空**不是**完成(可能 `running` 或 `BLOCKED`)。收完成通知只领不播;重放只派未完成票(`l4_tasks batches <date>`)。

   ⚠️ `pinned` 取自 `meta[code].pinned`——**派发前逐一核对「📌保送票」都带 `pinned:true`**,漏传使持仓 SELL 双复核不跑(见 STAGES.md L4 节)。**cfg = 步骤 0.5 的 `user_config` 原样透传,勿传 `{}`**(见 0.5 节;cfg 内含 `engine` 键,workflow 靠它拼 `$CTX` 根)。

   每股链内:preflight→(slim∥intel)→l4-card 决策卡→(≥OW)2 独立复核取中位只向下折回。仅 `RATE_LIMIT`/`CONNECTION`/`TIMEOUT` 重试,schema/contract/data-integrity 直接阻断,不删任务簿。

   **活体情报站**(`l4_intel.enabled`):盲搜六面落 `_l4_intel_<code>.md`,卡 P3 先读。⚠️ **铁律:intel 价格断言须与 verified OHLCV 对账后才可采信**(见 STAGES.md L4 节)。
5. **L5 整合**(全部 l4-stock workflow 完成后,主会话直接跑;哨兵档跳过 L3/L4 后也走这里)。
   **五条在一个 shell 批次跑完再播 CP7**,`<run_id>` 是 assemble 打印的报告目录名:
   ```bash
   CTX=context_${AUTORESEARCH_ENGINE:-claude}; RPT=reports_${AUTORESEARCH_ENGINE:-claude}
   # ⚠️ 两份计量 JSON 的落点由 `AUTORESEARCH_RUN_ID` 决定,**不是**恒为 `$CTX/scan/<date>/`。
   #   读侧真身:`post_run observe` 走 `_resolve_scan(<date>)` → `workspace.scan_root()/<date>`,
   #   `_usage_reconcile.json` 由 `self_review.usage_reconcile_lint(scan_dir.parent)` 扫兄弟日期目录。
   #   `scan_root()` 就在这里分两支,写侧必须跟着分:
   #     ① 有 RUN_ID(步骤 0 已 export,**正常跑动恒走这支**)→ run 分区:
   #        --json-out $CTX/scan_runs/$AUTORESEARCH_RUN_ID/staging/<date>/_token_usage.json
   #     ② 无 RUN_ID(单步重跑 / 老树)→ 历史根:
   #        --json-out $CTX/scan/<date>/_token_usage.json
   #   下面的 `$STAGING` 就是这两支的 shell 取法 —— **别手写死其中一支**:落错根时读侧只是
   #   什么都读不到,CP7 **静默**写 `UNMEASURED`,没有任何报错替你发现。
   STAGING=${AUTORESEARCH_RUN_ID:+$CTX/scan_runs/$AUTORESEARCH_RUN_ID/staging}; STAGING=${STAGING:-$CTX/scan}
   uv run --no-sync python -m autoresearch.scan.assemble <date> && \
   uv run --no-sync python -m autoresearch.scan.gates gate4 <date> && \
   uv run --no-sync python -m autoresearch.trace.usage_harvest --engine $AUTORESEARCH_ENGINE \
     --run-id "$RUN_ID" \
     --out $RPT/scan/<run_id>/token_usage.md \
     --json-out $STAGING/<date>/_token_usage.json && \
   uv run --no-sync python -m autoresearch.trace.usage_reconcile <date> \
     --json-out $STAGING/<date>/_usage_reconcile.json && \
   uv run --no-sync python -m autoresearch.scan.post_run <date> observe \
     --report-dir $RPT/scan/<run_id>
   ```
   `observe` 的最后一步就是 **capsule finalize + 冻结后复验**(CP7 定序:gate4 → 计量 →
   reconcile → observe → expected/replay/completeness → finalize → verify),它的裁决在
   `observe` 的 stdout JSON 里以 `capsule` 段返回。Claude 引擎仍可用 `--session <sessionId>`
   走旧口径;Codex 引擎**必须**走 `--engine codex --run-id`(它没有 Claude 的 subagent 目录,
   `--session` 只会给出一张空表)。
   → `$RPT/scan/<数据日YYYYMMDD>-<发布MMDD_HHMM>/`(2026-08-28 用户裁定:**首段是研究的哪天行情**,尾段是写完的时刻;如 `20260825-0826_2000` = 研究 08-25 的市场、08-26 20:00 写完。旧格式 `<跑动日>_<HHMM>` 只读兼容,历史目录一律不改名):**`brief.md`(≤3KB 速读,入口)**+`summary.md`(**决策层**,11 节)+`appendix.md`(**现场层** A–G:漏斗/研究全文/门柱口径/耗时/方法/局限)+`details/`+`token_usage.md`+`trace/`;`index.md` 首行即指 brief。成本/墙钟成熟门(10 次真实扫描前恒 `IMMATURE`)见 STAGES.md『计量与跨层校准』;预算超线只写 warning/`DEGRADED`,不制造 BUY。
   **汇报(CP7)**:**先原文转播 `brief.md` 全文**(六节:市场/漏斗/BUY 结论/持仓/风险哨/昨日 delta),再补分段耗时(`render --view timing`)+ 产物路径;需要展开细节才引 `summary.md`。0 买日的**停因分桶**已由 brief ③ 自带,照贴即可,**不要说「无一过 ≥OW 三门」**——早停卡按定义不写三门段(见 STAGES.md『运维细节』)。
   **GATE4 拦什么**(控制方裁定):判据 = `gate_fires.csv` 里有任意一行 `severity=fail`。`brief_lint` 的八条按「**报告是不是在说假话**」二分 —— **fail(毙掉本趟)**:`brief·数字对账` / `brief↔summary不一致` / `brief·白名单外取数` / `brief·BUY契约(active 期)`;**warn(放行,但进账 + 播报)**:`brief·缺失` / `brief·超预算` / `brief·边表缺失` / `brief·边表过期`。**一份人类可读摘要排版超限是展示层问题;报告说假话才是硬门该拦的事**——别让 3KB 排版预算毙掉一条 60 分钟的流水线(「GATE3 差 16 字节」同族疤)。播报行 `[brief lint] fail N · warn M / 共 K 条` 两个计数都要念。
   **报告分两层是安全的**:**机器消费者不读、也不解析 `summary.md` 正文**(结论都在 `finalists.csv` / `decision_records.json` / `_final_ratings.json` 等结构化文件里),所以重排/瘦身 summary 不影响任何人;红线文件 `details/*.md`、`finalists.csv`、`decision_records.json` 一字不动。
   **brief 对账**:assemble 收尾自动跑 `self_review.brief_lint`(边表重算 + 正文锚在 + brief↔summary 同源 + active 期 BUY≥1 契约),结果追加进 `gate_fires.csv` 并打一行 `[brief lint] fail N · warn M / 共 K 条`;**有 fail 先修根因再播**,warn 照播不隐去。
   **配置生效对账**:`usage_reconcile`(第四条命令)把配置期望×实测逐 role 对上,`ok=false` 直接打进 CP7 播报,不经 `self_review` 转手(见 STAGES.md『计量与跨层校准』)。
   **法证 run capsule(2026-08-28)**:每次扫描**从启动就有**一个独立、可冻结、可校验的现场
   (`$RPT/scan/<run_id>/capsule/`)。它回答**三个互不替代**的问题,任何一个都不代表其余两个:
   - `integrity_ok` —— 已归档文件有没有被改(MANIFEST + 脱钩 ROOT + 账本三重锚定);
   - `completeness_ok` —— 按本次的模式与终态,**该有的证据齐不齐**(expected 清单逐条比对);
   - `replayability` —— 只用 capsule 里的冻结输入,确定性阶段能否重放出同样的字节。

   ⚠️ **`MANIFEST` 校验通过 ≠ 现场完整**。MANIFEST 只对它列过的文件重算 hash,而**没人写下的文件
   它永远列不到** —— `20260826_2000` 就是这样带着 0 份 transcript、557 个未归档 staging 和
   `$0.0000` 的假成本,在旧展示层里显示「现场完整性 ✓」的。`chain_view` 与 `index.md` 现在分行
   报六个事实(业务/证据/完好/完整/可重放/归档),绿灯「现场可复盘」要求它们**同时**成立。

   旧 `scan/retention.retain`(`trace/staging/`+`trace/inputs/`+`trace/MANIFEST.sha256`)降为
   **兼容路径**:capsule 之前的 run 仍靠它,但它给出的绿灯只代表完好性,**不得**再当作完整性结论。
   核验与复盘(任何时候都能跑,全部只读):
   ```bash
   uv run --no-sync python -m autoresearch.trace.capsule verify "$RUN_ID"              # 三个结论分开报
   uv run --no-sync python -m autoresearch.trace.capsule inspect "$RUN_ID"             # 活跃 spool 概览
   uv run --no-sync python -m autoresearch.trace.capsule replay "$RUN_ID"              # 只用冻结输入重放 L0-L2/L5
   uv run --no-sync python -m autoresearch.scan.chain_view <run_id> <6位码>             # 这只票是怎么被推上来的
   uv run --no-sync python -m autoresearch.scan.retention verify $RPT/scan/<run_id>    # 旧 run 的兼容核验
   ```
   证据事后找回来 → **叠加层**,绝不回头改冻结的现场:
   ```bash
   uv run --no-sync python -m autoresearch.trace.capsule repair "$RUN_ID" \
     --reason "transcript restored" --source <暂存目录>   # 写 _repairs/<run_id>/revision-N/
   ```
   ⚠️ **回放/研究仪器一律写 scratch 或 `$RPT/research/`,禁写 run 目录与 staging** —— 实测 `20260725_1316` 的 `run_health.json`
   被一次回放覆盖成 `cards=0`(该 run 实有 11 张卡)、08-13/08-18 的 `_relative_buy_decision.json` 被影子回放改写成 `buys=[688766]`
   (与当日 brief 的 BLOCKED 直接打架)。`verify` 就是用来发现这类事的。

6. **覆盖档案维护**(盘后,不占扫描窗;presence-gated,池空则整段跳过)
   ```bash
   uv run --no-sync python -m autoresearch.dossier.pool <date> --status        # 看 pending_init 队列
   uv run --no-sync python -m autoresearch.dossier.reconcile <period>          # 季度对账(中报/年报披露后,如 20260630)
   ```
   - **建档队列**:`pending_init` 逐只派 `.claude/workflows/dossier-init.js`(**≤3 只/晚**)。
   - **prelude 会替你催**:📐=未对账、🕰️=>90 日未刷新;解药是跑一次**成功的季度对账**(细节见 STAGES.md『运维细节』)。

## 行为变更的入口

2026-08-21 用户裁定「整个 learning 层退役」后**没有治理链条这回事了**:涉及召回、L3、门、早停、评级、Token 或速度的改动 = 普通开发改动(人判断 → 改 `scan_config.jsonc` 或代码 → 测试锁 → 合入)。无自动学习、无影子账本呈证、无 proposal 裁决通道。保留下来的三件门/尺与同批连带退役的清单见 STAGES.md「行为变更的入口」节。

## 铁律
- **确定性层零 LLM**:L0/L1/**L2**/L5 全 pandas,不在筛选里编数、不预测。
- **召回宽、判断深**:L1 高召回 → L2 分层多样性采样收口(给均衡菜单,非 alpha);多空取舍在 L3 holistic 精排 + L4 决策卡。
- **L3/L4 必须 subagent**(独立 context),只回传紧凑结果,否则撑爆主线。
- **每只 finalist 走 stock-research lite 档**——继承其铁律。
- **中间名单全 staging**,L5 发布到 `trace/` 留溯源。
- **报告双层**:`brief.md` = 入口(确定性模板、零 LLM、≤3,000B、同 run 重放 byte 稳定),`summary.md` = 详细版。**不设收编官 agent**(用户裁定):brief 的内容全是结构化结论/计数/评级/tripwire,让 LLM 再压一遍只增加编数面与对账成本。机器消费者不读 summary 正文(整条链上已经没有解析它的人)——所以重排/瘦身 summary 不动任何契约。
- **诚实收尾**:召回/粗排是启发式 + `gap_c1_o2` 超短主尺 IC 校准(随 regime 漂移);L3/L4 是 Claude 推理产出;"仅供研究,非投资建议"。
- **性能开关不拥有评级**:现仅存 `performance.streaming_l4`(默认 true;回滚设 `false`)。任何开关都不得改 finalist cap、rubric 三门、**主尺**(`common.ruler.MAIN_RULER`,现 `gap_c1_o2`)或 BUY 数量(Wave10 B4 退役两个越权开关,详情见 STAGES.md)。
- **模块归属**:`agents/l3_select.py`、`agents/l4_card.py`、`scan/assemble.py` 仅保留旧 import/CLI 兼容,新代码直连 `scan/l3/*`、`scan/l4/*` 等 owner 模块,不要塞回适配器。
- **引擎隔离**:两引擎除数据湖 `lake/` 外不共享任何可变状态;禁止跨引擎读写对方 `context_*`/`reports_*`。

## 常见坑
- 必须 `uv run --no-sync`(不误删 venv-only 的 akshare/tushare/lightgbm)、仓库根目录。
- **默认 `--source tushare`**(东财 push2 常被网络封锁);需 `TUSHARE_TOKEN`。
- **召回权重 / L2 采样**:`weights.json` 缺失 → 内置先验(弱);改因子后重跑 `factor_lab harvest`→`calibrate`→`eval`。L2 为何不做模型见 STAGES.md 核心世界观节。
- `context_*/`、`reports_*/`、`lake/` 已 gitignore;别误提交大文件。裸 `context/`、`reports/` 若重新出现 = 有代码绕过了 workspace 单一事实源(`autoresearch/common/workspace.py`),按 bug 处理。

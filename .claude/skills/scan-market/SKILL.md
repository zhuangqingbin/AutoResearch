---
name: scan-market
description: "Use when the user wants to scan the WHOLE A-share market to discover buy-worthy stocks AND strong sectors — 「扫描全A股」「全市场选股」「哪些板块值得买」「find the best A-share buys」. Deterministic L0-L2 funnel + Claude L3/L4/L5; artifacts → reports_<engine>/scan/<run_id>/. NOT for: one named ticker (→ stock-research; 持仓单票复核走其 lite 档), cross-asset macro (→ macro-research). Project-local."
---

# scan-market — 全 A股六段漏斗扫描(挖掘个股 + 板块,零付费 API)

> session_v1 编排入口(PILOT,默认仍 legacy):见 `docs/session-agent/README.md`;`finish` 后用 `session_agent verify-report --level full` 的机器结果交付。
> runner 宿主循环(PILOT,opt-in;真跑验收前默认仍走下方 Workflow):`session_agent run --executor mailbox` + 邮箱 wait/complete,见 `docs/session-agent/README.md`。
> 本文件 = **怎么跑**;各阶段机制与参数见 `STAGES.md`;运维细节 `docs/ops/scan-ops.md`;负结果与沿革 `docs/research/scan-negative-results.md`。冲突以源码为准。

## 核心原理

对 ~5,500 只逐个跑深度报告不可行。本 skill 用**搜索/推荐系统式六段漏斗**:确定性层(零 token)收窄到 ~200 → Claude holistic 精排到 7–10 → 只对这几只跑 **stock-research lite 档决策卡** → 整合。**token 只跟最终深挖的几只成正比;省 token 靠早停,不靠降模型。**(A股;港股/美股全市场本期不支持。)

| 段 | 引擎 | 作用 | 进→出 |
|---|---|---|---|
| **L0** 选集 | 确定性 | 候选池+硬门(ST/退/停牌/次新+市值地板) | 全A→~5,500 |
| **L1** 召回 | 确定性·10 路 | 各路 top → quota union(floor 保底多样性) | →1,000 |
| **L2** 粗排 | 确定性·分层采样 | sector-neutral composite + 风格桶 floor + 落刀帽 + 行业席位;**不预测** | →200 |
| 旁路 市场研判 | `macro-brief` ×1 | 写 `market_view.md`,地形段喂 L3/L4 | 1 份 |
| 旁路 行业 brief | `sector-brief` ×K | 单段地形 brief 喂 L3/L4 | ≤6 份 |
| **L3** 精排 | `l3-rank` ×1 | pass1 分诊 ~40 → holistic 比较 → finalist 7–10 + bench | →7–10 |
| **L4** 研究 | 每股 `l4-intel` + `l4-card` | 决策卡(P0→P3 早停②→P4→P5;≥OW 双复核) | ~10 卡 |
| **L5** 整合 | 确定性 | brief + summary + appendix + 账本 | 1 份 |

## 前置

- 项目根目录;**务必 `uv run --no-sync`**;`.env` 有 `TUSHARE_TOKEN` + `FRED_API_KEY`。默认中文。
- **路径约定**:`$CTX`/`$RPT` = 本引擎工作区根(Claude→`context_claude`/`reports_claude`,Codex→`context_codex`/`reports_codex`;bash 里 `CTX=context_${AUTORESEARCH_ENGINE:-claude}`)。数据湖 `lake/` 两引擎共享;python -m 命令不带路径参数时自动按引擎解析。
- **闭环(learning 层)已整体退役**(2026-08-21 用户裁定):开跑前没有要补的复盘;L3/L4 prompt 不吃任何历史账本先验;涉及召回/L3/门/早停/评级/Token 的改动都是普通开发改动。
- **数据日不手算**:`DATE=$(uv run --no-sync python -m autoresearch.scan.trade_date)`(缺省=最近已结算交易日,19:15 前回退上一交易日;显式给非交易日会非零退出)。交易日晚间等 stk_factor_pro 灌齐(~21:10,行数连续两次不变且 ≥5300)再开扫。

## 配置

- **全部用户可调参数只有一个家:`scan_config.jsonc`**(按阶段排序,每键注释 = 文档,标【生效点】)。例外两个:保送票清单 `pinned.jsonc`;L1 校准权重 `$CTX/factor_lab/weights.json`(仅 `weight_profile:"calibrated"` 档读,生产档 `"preference"` 不读)。
- **装载链**:`frame --json` 经 `user_config.py` 白名单校验后回显 → 随 Workflow `args.config` 传入(workflow 无文件系统)→ L4 每股 `args.cfg` 原样透传。**传 `{}` = 静默关 intel + 降 effort**,现直接 throw。
- **防漂移铁律**:白名单外的键 load 即 raise;新增参数三件套 = 白名单 + 真实消费点 + 测试锁;改值 ≠ 无害(行为类旋钮先想清楚、留测试锁);**性能开关不拥有评级**(现仅存 `performance.streaming_l4`,Wave10 B4 退役两个越权开关;任何开关不得改 finalist cap、rubric 三门、主尺 `common.ruler.MAIN_RULER`、BUY 数量)。

## 流程(6 段)

> **编排真身 = 两段 workflow + 主会话收尾**:① `.claude/workflows/scan-market.js`(Prelude→L3→L4-prep)→ ② 主会话**一次性全派** `.claude/workflows/l4-stock.js` → ③ 步骤 5 整合。以下命令留作调参/单步重跑入口。
>
> **会话纪律**:扫描只在干净的新会话里开;run 失败要改代码时,先冻结 FAILED 并结束本会话,在另一个会话里修,重跑再开新会话。改过 hook、agent 定义或 settings 要整个退出 Claude Code 再启动(`/clear` 不重载)。Codex 同理:改过 `.codex/agents` 或 `.codex/hooks.json` 要重开 Codex,新 hook 要在启动审查里批准一次才生效。
>
> ### 过程直播契约(必做)
>
> 主会话在 8 个检查点主动播报,素材全是零 LLM 现成产物,只转播不加工:
>
> | # | 时机 | 播什么 | 怎么拿 |
> |---|---|---|---|
> | CP0 | Stage0 完 | regime + 温度 + 策略师定调句 | `market_pack.json` + `market_view.md` §1 首句 |
> | CP1 | GATE1 过 | **前奏汇总屏全文** | Read `_prelude_summary.md` 全量转播 |
> | CP2 | 行业 brief 齐 | 每行业一句地形定调 | 各 `sector_briefs/*.md` 首句(可与 CP3 合并) |
> | CP3 | GATE2 过 | 入围名单逐只 + 被切影子 | workflow `L3入围` 日志 + `_l3_pass1_cut.csv` |
> | CP4 | L4 派发 | 派发 N 股 + 预算旗 + intel 开关 + 📌保送名单 | workflow 日志 |
> | CP5 | L4 进行中 | 每出一张卡播一行 k/N 代码 名称 评级 | `l4_watch` Monitor 自动播 |
> | CP6 | L4 全完 | 评级分布 + 停因分桶 + OW三门直方图 | `autoresearch.scan.render <date> --view gate_hist` |
> | CP7 | GATE4 过 | **`brief.md` 原文全量转播** + 产物路径 + 分段耗时 + token 真计量 | Read `$RPT/scan/<run_id>/brief.md` + `render --view timing` + `usage_harvest` |
>
> **唤醒纪律**:派发一次性全派、收通知只领不播,不出分析文字。L4 派发后挂 Monitor(`timeout_ms: 3600000, persistent: false`):`uv run --no-sync python -m autoresearch.scan.l4_watch <date> --watch`(只认 `_l4_tasks.json` 终态:SUCCEEDED 播评级,FAILED/BLOCKED 播错误类;**BLOCKED = 该票废了,不是出了卡**;进度记 `outbox/l4_watch_cursor.json`,重挂不重播,重播须 `--replay-all`,游标损坏报错退出要求人工选择;别照卡片评级下结论,`sell_review`/`ow_review` 折回在 assemble 之后)。

0. **开场:先领 run_id,再取任何一个数**:
   ```bash
   export AUTORESEARCH_ENGINE=codex   # Claude 会话下无需设置
   DATE=$(uv run --no-sync python -m autoresearch.scan.trade_date)
   RUN_JSON=$(uv run --no-sync python -m autoresearch.trace.capsule begin scan-market "$DATE" \
     --engine "$AUTORESEARCH_ENGINE" --config-file .claude/skills/scan-market/scan_config.jsonc \
     --legacy-reason "<为何走 legacy workflow,如:session_v1 真实宿主验收 INCOMPLETE>")
   RUN_ID=$(printf '%s' "$RUN_JSON" | jq -r .run_id); export AUTORESEARCH_RUN_ID="$RUN_ID"
   ```
   `begin` 先落 RunContract + 代码/环境/prompt 身份快照再公布 run 目录;`RUN_ID` 随 `Workflow args.run_id` 传给 `scan-market.js`,再透传每个 `l4-stock`;staging 按 run 分区 `$CTX/scan_runs/<run_id>/staging/<date>/`。上一次被打断的 run 由 prelude/prewarm 开头自动冻结,也可手动 `uv run --no-sync python -m autoresearch.trace.capsule recover`。
0.1. **前奏一键**:`uv run --no-sync python -m autoresearch.scan.prelude <YYYY-MM-DD>` —— 全部确定性前奏(顺序与去留的单一事实源 = `prelude.STEP_NAMES`:consensus / temperature / universe(L0-L2)/ calendar / catalyst / menu / l4_rejection / outcome_fill / ledger_views / dossier_pool / news_catalog / overseas);末尾汇总屏含 ⚡tripwire 持仓盯梢行(仅人看,勿贴给 agent)。夜间 19:30 launchd 预热,看汇总屏「预热(夜间)」行。
0.5. **市场研判**:`uv run --no-sync python -m autoresearch.scan.frame <日期> --json-out $CTX/scan/<日期>/market_pack.json` → `Agent(subagent_type='macro-brief')` 写 `market_view.md`。⚠️ **配置必传**:`user_config`(真身 `scan_config.jsonc`,**.jsonc 非 .json**)随 `args.config` 传入,步骤 4 每股 `args.cfg` 原样透传。
1. **L0+L1+L2**:`uv run --no-sync python -m autoresearch.scan.universe [YYYY-MM-DD]`(旋钮全吃 `scan_config.jsonc`,CLI flag 只作单次覆盖)。
2. **日历**:`uv run --no-sync python -m autoresearch.scan.calendar <date>`。菜单病由 L5 自动嵌。
2.2. **哨兵决策**(确定性建议,人拍板):`uv run --no-sync python -m autoresearch.scan.menu <date>` 打印 `[sentinel]` 行。⚠️ 哨兵只问「今天有没有值得买的」,不含「持仓要不要动」:`pinned.jsonc` 非空必传 `force_full: true`,否则持仓拿不到当日卖/持卡。
2.7. **行业 brief**:`uv run --no-sync python -m autoresearch.sector.reuse <date> --apply` → `uv run --no-sync python -m autoresearch.sector.pack <date>` → 每行业一个 `Agent(subagent_type='sector-brief')`。
3. **L3 精排**(两遍法):证据取数 → `l3_table_md` 压紧凑表(pass1 分诊,被切的是影子 `_l3_pass1_cut.csv`)→ `Agent(subagent_type='l3-rank')` 出 finalist tier + bench → `menu <date>` 拿预算 → `merge_l3_finalists_v3` 确定性守卫 → `finalists.csv` + `_l3_bench.csv`。
4. **L4 研究**(token 大头;默认流式、每股独立可恢复)——l4-prep 先跑质押旗/席位/催化/日历生产者 → 落稿:
   ```bash
   uv run --no-sync python -m autoresearch.scan.agents.l4_card pledge <date>
   uv run --no-sync python -m autoresearch.scan.agents.l4_card prompts <date>
   ```
   → scan-market.js 返回 `dispatch_batches`/`task_book` 后,主会话**一次性全派**
   `Workflow({scriptPath: '.claude/workflows/l4-stock.js', args: {date, code, name, sector, cfg, pinned, dossierSummary}})`(一条消息 N 个调用全部派出)。
   **完成判据 = task_book 全 SUCCEEDED**;`batches` 为空不是完成(可能 `running` 或 `BLOCKED`)。重放只派未完成票(`l4_tasks batches <date>`)。
   ⚠️ `pinned` 取自 `meta[code].pinned`——派发前逐一核对「📌保送票」都带 `pinned:true`,漏传使持仓 SELL 双复核不跑。**cfg = 步骤 0.5 的 `user_config` 原样透传,勿传 `{}`**。
   每股链内:preflight→(slim∥intel)→`l4-card` 决策卡→(≥OW)2 独立复核取中位只向下折回。仅 `RATE_LIMIT`/`CONNECTION`/`TIMEOUT` 重试,schema/contract/data-integrity 直接阻断,不删任务簿。**活体情报站**(`l4_intel.enabled`):`l4-intel` 盲搜六面落 `_l4_intel_<code>.md`,卡 P3 先读;intel 价格断言须与 verified OHLCV 对账后才可采信。`performance.streaming_l4=false` 回滚旧批量 GATE3。
5. **L5 整合**(全部 l4-stock 完成后,主会话直接跑;哨兵档跳过 L3/L4 后也走这里)。**五条在一个 shell 批次里 `&&` 链跑完再播 CP7**,`<run_id>` 是 assemble 打印的报告目录名:
   ```bash
   CTX=context_${AUTORESEARCH_ENGINE:-claude}; RPT=reports_${AUTORESEARCH_ENGINE:-claude}
   # 计量 JSON 落点由 AUTORESEARCH_RUN_ID 决定:有 RUN_ID(正常跑动)→ run 分区;无(单步重跑)→ --json-out $CTX/scan/<date>/_token_usage.json;落错根时读侧读不到,CP7 会**静默**写 UNMEASURED,没有报错替你发现
   STAGING=${AUTORESEARCH_RUN_ID:+$CTX/scan_runs/$AUTORESEARCH_RUN_ID/staging}; STAGING=${STAGING:-$CTX/scan}
   uv run --no-sync python -m autoresearch.scan.assemble <date> && \
   uv run --no-sync python -m autoresearch.scan.gates gate4 <date> && \
   uv run --no-sync python -m autoresearch.trace.usage_harvest --engine $AUTORESEARCH_ENGINE --run-id "$RUN_ID" \
     --out $RPT/scan/<run_id>/token_usage.md --json-out $STAGING/<date>/_token_usage.json && \
   uv run --no-sync python -m autoresearch.trace.usage_reconcile <date> --json-out $STAGING/<date>/_usage_reconcile.json && \
   uv run --no-sync python -m autoresearch.scan.post_run <date> observe --report-dir $RPT/scan/<run_id>
   ```
   `observe` 的最后一步是 capsule finalize + 冻结后复验(gate4 → 计量 → reconcile → observe → finalize → verify),裁决在 stdout JSON 的 `capsule` 段;同时写 `_buyability.json`(零 LLM 不可买归因,brief ③ 附一行)。Codex 引擎必须走 `--engine codex --run-id`。
   → `$RPT/scan/<数据日YYYYMMDD>-<发布MMDD_HHMM>/`:**`brief.md`(≤3KB 速读,入口)**+`summary.md`(决策层)+`appendix.md`(现场层)+`details/`+`token_usage.md`+`trace/`+`capsule/`;`index.md` 首行即指 brief。
   **报告分两层是安全的**:机器消费者不读、也不解析 `summary.md` 正文(结论都在 `finalists.csv` / `decision_records.json` / `_final_ratings.json` 等结构化文件里),重排/瘦身 summary 不影响任何人;红线文件 `details/*.md`、`finalists.csv`、`decision_records.json` 一字不动。
   **汇报(CP7)**:**先原文转播 `brief.md` 全文**(六节:市场/漏斗/BUY 结论/持仓/风险哨/昨日 delta),再补分段耗时(`render --view timing`)+ 产物路径;需要展开细节才引 `summary.md`。0 买日的**停因分桶**由 brief ③ 自带,照贴即可,**不要说「无一过 ≥OW 三门」**(早停卡按定义不写三门段)。计量覆盖主会话+subagent,成本按公开计价倍率**加权**;缺 JSON 写 `UNMEASURED`,**不能写 `$0`**;成本/墙钟成熟门 = 10 次真实扫描,此前恒 `IMMATURE`。
   **GATE4 拦什么**:`gate_fires.csv` 里任意一行 `severity=fail`(报告说假话:数字对账 / brief↔summary 不一致 / 白名单外取数 / active 期 BUY 契约);缺失/超预算/边表只 warn(放行但进账 + 播报,两个计数都要念)。有 fail 先修根因再播。`usage_reconcile` 的 `ok=false` 直接打进 CP7 播报。
   **法证 run capsule**:每场从启动就有独立现场 `$RPT/scan/<run_id>/capsule/`,三个结论互不替代——`integrity_ok` / `completeness_ok` / `replayability`;**MANIFEST 通过 ≠ 现场完整**。核验 `uv run --no-sync python -m autoresearch.trace.capsule verify "$RUN_ID"`;单票链路 `uv run --no-sync python -m autoresearch.scan.chain_view <run_id> <6位码>`;**回放/研究仪器一律写 scratch 或 `$RPT/research/`,禁写 run 目录与 staging**。
6. **覆盖档案维护**(盘后,presence-gated,池空整段跳过):`uv run --no-sync python -m autoresearch.dossier.pool <date> --status` 看 pending_init 队列(逐只派 `.claude/workflows/dossier-init.js`,≤3 只/晚);`uv run --no-sync python -m autoresearch.dossier.reconcile <period>` 季度对账(prelude 📐=未对账、🕰️=>90 日未刷新时催)。

## 铁律
- **确定性层零 LLM**:L0/L1/L2/L5 全 pandas,不编数、不预测。
- **召回宽、判断深**:L1 高召回 → L2 分层多样性采样(菜单,非 alpha);多空取舍在 L3 精排 + L4 决策卡。
- **L3/L4 必须 subagent**(独立 context),只回传紧凑结果;每只 finalist 走 stock-research lite 档,契约在 `.claude/agents/l4-card.md`。
- **中间名单全 staging**,L5 发布到 `trace/` 留溯源;`brief.md` 是确定性模板(零 LLM),**不设收编官 agent**;机器消费者不读 `summary.md` 正文。
- **诚实收尾**:召回/粗排是启发式;L3/L4 是 Claude 推理产出;"仅供研究,非投资建议"。
- **模块归属**:`agents/l3_select.py`、`agents/l4_card.py`、`scan/assemble.py` 仅保留兼容,新代码直连 `scan/l3/*`、`scan/l4/*`。
- **引擎隔离**:两引擎除 `lake/` 外不共享任何可变状态。

## 常见坑
- 必须 `uv run --no-sync` + 仓库根目录;默认 `--source tushare`(东财 push2 常被封)。
- `context_*/`、`reports_*/`、`lake/` 已 gitignore;裸 `context/`、`reports/` 重新出现 = 有代码绕过了 `common/workspace.py`,按 bug 处理。
- 壳回报 pending/running 不可信:slim 门读任务簿真值;`ok:true` 的 pending 立刻停该票 workflow、`l4_tasks failure --error-class TIMEOUT` 释放、attempt=2 重派。
- 同一会话失败重跑会把主会话上下文撑爆(实测 61k→494k):新会话。
- **召回权重 / L2 采样**:`weights.json` 缺失 → 内置先验(弱,仅 `weight_profile:"calibrated"` 档读它);改因子后重跑 `uv run --no-sync python -m autoresearch.research.factor_lab` 的 `harvest`→`calibrate`→`eval` 只喂研究/回滚路径,生产档不读。
- **一致预期**:`uv run --no-sync python -m autoresearch.research.consensus pull <date>` 限频 1 次/小时;prelude 的 consensus 步已跑,单步重跑才手动。

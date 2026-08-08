---
name: scan-market
description: "Use when the user wants to scan the WHOLE A-share market to discover buy-worthy stocks AND strong sectors — 「扫描全A股」「全市场选股」「哪些板块值得买」「find the best A-share buys」. Deterministic L0-L2 funnel + Claude L3/L4/L5; artifacts → reports/scan/<run_id>/. NOT for: one named ticker (→ stock-research; 持仓单票复核走其 lite 档), reviewing a past scan day (→ scan-retro), cross-asset macro (→ macro-research). Project-local."
---

# scan-market — 全 A股六段漏斗扫描(挖掘个股 + 板块,零付费 API)

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
- **召回权重**:`weights.json`(`factor_lab calibrate` 产;命令见常见坑节)。`--regime-aware` 用法与重标定见 STAGES.md L1 节;L2 不用模型(见铁律)。
- **闭环(开跑前补跑复盘)**:先 `autoresearch.learning.retro pending`(D+2)与 `autoresearch.learning.t1_review pending`(D+1);有欠账先用 **scan-retro** 补上。
- **一致预期**:`autoresearch.research.consensus pull <date>`(限频 1次/小时)。
- **token 真计量**:无需前置——CP7 跑 `usage_harvest`(命令见下方;OTEL 已退役)。

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
> | CP7 | GATE4 过 | **`brief.md` 原文全量转播** + 产物路径 + 分段耗时 + **token 真计量** | Read `reports/scan/<run_id>/brief.md`(≤3KB)+ `--view timing` + `usage_harvest` |
>
> **CP7 播报 = 读 brief 原文,不复述**:`brief.md` 是确定性模板产物(零 LLM,七节 ≤3,000B,同 run 重放 byte 稳定)——主会话再总结一遍只会新增编数面,还要多一次对账。原文贴出 + 附 `reports/scan/<run_id>/` 路径即可;要展开某一节再读 `summary.md`(详细版)。brief 缺席 = `self_review` 的 `brief·缺失` fail,如实播报,不要拿 summary 顶替。
> **CP7 计量**:命令见步骤 5(含 `usage_reconcile`)。覆盖主会话+subagent,成本按公开计价倍率加权;缺 JSON 写 `UNMEASURED`,**不能写 `$0`**。
> **唤醒纪律**(cache 读按全上下文计费,主会话曾独占近半全场成本):派发一次性全派、收通知只领不播,不出分析文字;CP2/CP3 合并播报,CP0/CP1/CP4/CP6/CP7 照常播。

0. **前奏一键**:
   ```bash
   uv run --no-sync python -m autoresearch.scan.prelude <YYYY-MM-DD>
   ```
   跑全部确定性前奏(账本/日历/菜单/预算/哨兵建议刷新,逐件见 STAGES.md 闭环层表);末尾汇总屏含 **📐/🔁/🚪 当日件建议行**。
   - **夜间预热**:交易日 19:30 launchd 自动跑;看汇总屏「预热(夜间)」行,安装见 STAGES.md『运维细节』。
0.5. **市场研判**:`uv run --no-sync python -m autoresearch.scan.frame <日期> --json-out context/scan/<日期>/market_pack.json` → `Agent(subagent_type='macro-brief')` 写 `market_view.md`(地形段喂 L3/L4;模板见 macro-playbook 末节)。
   ⚠️ **配置必传**:`user_config`(真身 `scan_config.jsonc`,**.jsonc 非 .json**)须随 `args.config` 传入,步骤 4 每股 `args.cfg` 原样透传。**传 `{}` = 静默关 intel + 降 effort**;空 config 现直接 throw(详情见 STAGES.md『运维细节』)。
1. **L0+L1+L2**(全确定性,零 token):
   ```bash
   uv run --no-sync python -m autoresearch.scan.universe [YYYY-MM-DD] --regime-aware
   ```
   (可选 flag/产物清单见 STAGES.md L1/L2 节)。
2. **过目 + 日历**(观察单日检已退役 fb_20260714_002,勿再跑):
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
   `Workflow({scriptPath: '.claude/workflows/l4-stock.js', args: {date, code, name, sector, cfg, pinned, dossierSummary}})`(一条消息 N 个调用全部派出;回滚杆不能恢复旧滑窗节奏,沿革见 STAGES.md L4 节)。

   **完成判据 = task_book 全 SUCCEEDED**;`batches` 为空**不是**完成(可能 `running` 或 `BLOCKED`)。收完成通知只领不播;重放只派未完成票(`l4_tasks batches <date>`)。

   ⚠️ `pinned` 取自 `meta[code].pinned`——**派发前逐一核对「📌保送票」都带 `pinned:true`**,漏传使持仓 SELL 双复核不跑(见 STAGES.md L4 节)。**cfg = 步骤 0.5 的 `user_config` 原样透传,勿传 `{}`**(见 0.5 节)。

   每股链内:preflight→(slim∥intel)→l4-card 决策卡→(≥OW)2 独立复核取中位只向下折回。仅 `RATE_LIMIT`/`CONNECTION`/`TIMEOUT` 重试,schema/contract/data-integrity 直接阻断,不删任务簿。

   **活体情报站**(`l4_intel.enabled`):盲搜六面落 `_l4_intel_<code>.md`,卡 P3 先读。⚠️ **铁律:intel 价格断言须与 verified OHLCV 对账后才可采信**(见 STAGES.md『运维细节』)。
5. **L5 整合**(全部 l4-stock workflow 完成后,主会话直接跑;哨兵档跳过 L3/L4 后也走这里)。
   **五条在一个 shell 批次跑完再播 CP7**,`<run_id>` 是 assemble 打印的报告目录名:
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
   → `reports/scan/<YYYYMMDD_HHMM>/`:**`brief.md`(≤3KB 速读,入口)**+`summary.md`(详细版)+`details/`+`token_usage.md`+`trace/`;`index.md` 首行即指 brief。成本/墙钟成熟门(10 次真实扫描前恒 `IMMATURE`)见 STAGES.md『计量与跨层校准』;预算超线只写 warning/`DEGRADED`,不制造 BUY。
   **汇报(CP7)**:**先原文转播 `brief.md` 全文**(七节:市场/漏斗/BUY 结论/持仓/风险哨/昨日 delta/欠账),再补分段耗时(`render --view timing`)+ 产物路径;需要展开细节才引 `summary.md`。0 买日的**停因分桶**已由 brief ③ 自带,照贴即可,**不要说「无一过 ≥OW 三门」**——早停卡按定义不写三门段(见 STAGES.md『运维细节』)。
   **报告分两层是安全的**:`t1_review` 与 `retro` **不解析 `summary.md` 正文**(它们读 `finalists.csv` / `decision_records.json` / `_final_ratings.json` / `retro/attribution.csv` 等结构化文件),所以重排/瘦身 summary 不影响任何机器消费者;红线文件 `details/*.md`、`finalists.csv`、`decision_records.json`、`shadow_buys.csv` 一字不动。
   **brief 对账**:assemble 收尾自动跑 `self_review.brief_lint`(边表重算 + 正文锚在 + brief↔summary 同源 + active 期 BUY≥1 契约),结果追加进 `gate_fires.csv` 并打一行 `[brief lint] fail N`;**有 fail 先修根因再播**。
   **配置生效对账**:`usage_reconcile`(第四条命令)把配置期望×实测逐 role 对上,`ok=false` 直接打进 CP7 播报,不经 `self_review` 转手(见 STAGES.md『计量与跨层校准』)。

6. **覆盖档案维护**(盘后,不占扫描窗;presence-gated,池空则整段跳过)
   ```bash
   uv run --no-sync python -m autoresearch.dossier.pool <date> --status        # 看 pending_init 队列
   uv run --no-sync python -m autoresearch.dossier.reconcile <period>          # 季度对账(中报/年报披露后,如 20260630)
   ```
   - **建档队列**:`pending_init` 逐只派 `.claude/workflows/dossier-init.js`(**≤3 只/晚**)。
   - **prelude 会替你催**:📐=未对账、🕰️=>90 日未刷新;解药是跑一次**成功的季度对账**(细节见 STAGES.md『运维细节』)。

## 实验治理(行为变更的唯一生产入口)

涉及召回、L3、门、早停、ensemble、评级、Token 或速度的改动,先以 `autoresearch.learning.experiment_registry` 登记不可变定义+**稳定基线**,再由 `autoresearch.learning.promotion` 检查研究/决策/Token/速度/架构五项守卫。状态机 `PREREGISTERED → RECOMMENDED → APPROVED → ACTIVE`,观察窗通过成 `STABLE_CANDIDATE`,守卫失守成 `ROLLBACK_RECOMMENDED`。`approve`/`activate` 须**人工批准**留痕;`autoresearch.learning.rollback_watch` 只推荐,不自动改生产配置。命令序列/完整字段/成熟门见 STAGES.md「实验晋升与回滚控制面」。

## 铁律
- **确定性层零 LLM**:L0/L1/**L2**/L5 全 pandas,不在筛选里编数、不预测。
- **召回宽、判断深**:L1 高召回 → L2 分层多样性采样收口(给均衡菜单,非 alpha);多空取舍在 L3 holistic 精排 + L4 决策卡。
- **L3/L4 必须 subagent**(独立 context),只回传紧凑结果,否则撑爆主线。
- **每只 finalist 走 stock-research lite 档**——继承其铁律。
- **中间名单全 staging**,L5 发布到 `trace/` 留溯源。
- **报告双层**:`brief.md` = 入口(确定性模板、零 LLM、≤3,000B、同 run 重放 byte 稳定),`summary.md` = 详细版。**不设收编官 agent**(2026-08-08 裁定 R-C1):brief 的内容全是结构化结论/计数/评级/tripwire,让 LLM 再压一遍只增加编数面与对账成本。`t1_review`/`retro` **不解析** summary 正文,只读结构化文件——所以重排/瘦身 summary 不动任何机器契约。
- **诚实收尾**:召回/粗排是启发式 + fwd_2_oc 超短主尺 IC 校准(随 regime 漂移);L3/L4 是 Claude 推理产出;"仅供研究,非投资建议"。
- **性能开关不拥有评级**:现仅存 `performance.streaming_l4`(默认 true;回滚设 `false`)。任何开关都不得改 finalist cap、rubric 三门、`fwd_2_oc` 或 BUY 数量(Wave10 B4 退役两个越权开关,详情见 STAGES.md)。
- **模块归属**:`agents/l3_select.py`、`agents/l4_card.py`、`scan/assemble.py` 仅保留旧 import/CLI 兼容,新代码直连 `scan/l3/*`、`scan/l4/*` 等 owner 模块,不要塞回适配器。

## 常见坑
- 必须 `uv run --no-sync`(不误删 venv-only 的 akshare/tushare/lightgbm)、仓库根目录。
- **默认 `--source tushare`**(东财 push2 常被网络封锁);需 `TUSHARE_TOKEN`。
- **召回权重 / L2 采样**:`weights.json` 缺失 → 内置先验(弱);改因子后重跑 `factor_lab harvest`→`calibrate`→`eval`。L2 为何不做模型见 STAGES.md 核心世界观节。
- `context/`、`reports/` 已 gitignore;别误提交大文件。

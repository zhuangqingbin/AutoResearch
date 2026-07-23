---
name: scan-market
description: Use when the user wants to scan the WHOLE A-share market (not one named ticker) to discover buy-worthy stocks AND strong sectors without paying for an LLM API — e.g. "扫描全A股挖掘值得买的票", "全市场选股", "现在哪些板块值得买", "帮我筛一遍A股龙头", "find the best A-share buys / strongest sectors". For a single known ticker use stock-research instead. Project-local skill.
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

本 skill 是**编排器**:确定性层(零 LLM)= L0/L1/L2(`autoresearch.scan.universe`,L2=`l2_stratify.select_l2` 分层多样性采样,ML-free)+ L5(`autoresearch.scan.assemble`),纯 pandas 不编数、不预测;AI 判断层 = L3(holistic 单 agent)+ L4(逐只决策卡,委托 **stock-research lite 档**——一只 finalist = 一个 Opus subagent 渐进深度 DD + 早停,P0简报定向→P1–P3表面→主早停②→P4陷阱核→③击杀→P5满卡),subagent 只回传紧凑结果。

## 何时用 / 不用
- ✅ 用户想**一次扫全市场**、挖"值得买的票 / 强势板块"(A股)。
- ❌ 已知**单个** ticker → **stock-research**(full=全量报告 / lite=快速卡)。
- ❌ 港股/美股全市场:本期不支持。

## 前置
- 在**项目根目录**运行;akshare/tushare/lightgbm 已装(venv-only,**务必 `uv run --no-sync`**);`.env` 有 `TUSHARE_TOKEN`(默认源)+ `FRED_API_KEY`(L4 取数)。默认中文。
- **召回权重**:`weights.json`(`factor_lab calibrate` 产;命令见常见坑节)。**regime 分桶权重**(`--regime-aware` 用):`factor_lab` `harvest` 后 python 里 `fl.calibrate_regimes()` → `weights.json` 增 `regimes` 块;重标定一律走 `retro.recalibrate_and_log`(快照+changelog 可回滚)。L2 不用模型(见铁律 / STAGES.md 核心世界观节)。
- **闭环(开跑前补跑复盘)**:先 `uv run --no-sync python -m autoresearch.learning.retro pending`(慢环,D+2)与 `uv run --no-sync python -m autoresearch.learning.t1_review pending`(**快环,D+1 判断层复盘**,2026-07-17 起);有欠账 → 先用 **scan-retro**(含快环 t1-review workflow)补上再开始今天的扫描。连续 0 买时看对照读数:`uv run --no-sync python -m autoresearch.learning.zero_buy_ledger`。
- **一致预期积累(每日 1 拉)**:`uv run --no-sync python -m autoresearch.research.consensus pull <date>`(tushare `report_rc` 限频 **1次/小时**,历史回补不可行);`status` 看进度。**验证门:积累 ≥60 日后 factor_lab 验 IC(两半稳+符号一致)才谈入 composite**。
- **(可选)token 真计量与 cache 审计**:跑扫描的 Claude Code 会话从带 OTEL env 的 shell 启动(五件 env 见 `STAGES.md`『真实计量与跨层校准』节),跑完 `uv run --no-sync python -m autoresearch.trace.telemetry <原始导出> --out reports/scan/<run>/token_telemetry.md`。生产派发路径零改动,仪器旁路。
- 用户对报告的反馈用 **feedback** skill 记。

## 流程(6 段)

> **编排真身 = 两段 workflow + 主会话收尾**(fb_20260714_003):① `.claude/workflows/scan-market.js`(3 相位:Prelude→L3→L4-prep,GATE1/2/3;GATE3 失败只剔单股,返回 `{dispatch, reused, meta}` 交接)→ ② 主会话把 dispatch 里**每股拉一个 `.claude/workflows/l4-stock.js`**(**一条消息 N 个 Workflow 调用并行**,args=`{date, code, name, sector, cfg}`;每股链内 intel→card→(≥OW)双复核折回落 `_ensemble_<code>.json`,单股失败只废单股、对该股单独重跑即可)→ ③ 全部 l4-stock 完成后主会话直接跑步骤 5 的 `assemble` + `gates gate4` CLI 收尾。**正常跑动直接用 workflow**;以下是其内部调用的同一批命令,留作**调参/单步重跑入口**。操作模板分驻:市场研判在 `macro-research/macro-playbook.md` 末节、L4 决策卡在 stock-research 的 `lite-playbook.md`;**各阶段机制/参数/实证读数**见 `STAGES.md`。各阶段墙钟收尾自动落 `_stage_timing.json`(mtime 推导)。
>
> **进度可视化(必做,2026-07-12 用户反馈"跑起来主对话一片空白")**:workflow 一落地就**同时**挂一个 Monitor 播报进度到主对话 ——
> ```
> Monitor(command: "uv run --no-sync python -m autoresearch.scan.progress <date> --watch",
>         description: "scan 漏斗进度", timeout_ms: 3600000, persistent: false)
> ```
> `autoresearch.scan.progress`(确定性读盘,零 LLM)从产物文件反推阶段+计数,**只在变化时**打一行(不刷屏):`⏳ L4 · finalists 11 · 🕵️ 情报 8 · 卡 7/11 · Hold 5·Overweight 1`。跑完自动退出。用户另可用 `/workflows` 看 spinner 级进度树。
> ⚠️ **播报是「反推」不是「断言」**:它靠产物文件的存在性猜阶段,**不区分「在跑 / 被跳过 / 挂了」,也可能把某阶段的输入产物当成它已完成**(2026-07-17 实测误报两次:哨兵档已提前返回却报「L3 精排中」、把 l3-rank 的输入 `_l3_table.md` 当成「精排 ✓」)。真信号以 workflow 的 `journal.jsonl`(每 agent 一条 `started`/`result`)为准,别拿播报当阶段状态断言。代码侧修复在 `pr_20260717_004`。

0. **前奏一键**(workflow Prelude 相位的确定性部分):
   ```bash
   uv run --no-sync python -m autoresearch.scan.prelude <YYYY-MM-DD>
   ```
   跑完全部确定性前奏(attribution 刷新/retro pending 列出/consensus 拉/universe/日历/菜单·L4预算·哨兵建议/journal 等 ledger 刷新,逐件见 STAGES.md 闭环层表;观察单日检已退役 fb_20260714_002)。各步失败不阻断,末尾汇总屏含 **📐/🔁/🚪 当日件建议行**(含「禁注」的行勿贴)。
   - **夜间预热(可选,spec 2026-07-12 §P1)**:交易日 19:30 launchd 自动 `scripts/prewarm.sh`(= `python -m autoresearch.scan.prewarm`,湖预拉+温度;calibrate 默认不跑防污染 changelog/DSR 计数)。安装:
     `sed "s|__REPO__|$PWD|" scripts/com.tradingagents.scan-prewarm.plist > ~/Library/LaunchAgents/com.tradingagents.scan-prewarm.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.scan-prewarm.plist`;验证 `launchctl list | grep scan-prewarm`。跑过预热的日子,开扫时 universe/L3 evidence 全湖命中。
0.5. **市场研判**(workflow Prelude 相位并行调用):`uv run --no-sync python -m autoresearch.scan.frame <日期> --json` 拿湖派生 market_pack → 一个 `Agent(subagent_type='macro-brief')` 写 `context/scan/<日期>/market_view.md`(模板见 macro-playbook 末节;地形段喂 L3/L4,操作基调/漏斗读数只进 L5)。该命令回显的 `user_config`(真身 **`.claude/skills/scan-market/scan_config.jsonc`**(.jsonc 非 .json!),白名单校验见 `autoresearch/scan/user_config.py`;回显同时落 `context/scan/<日期>/user_config_echo.json`)**必须**随 Workflow `args.config` 传入 `scan-market.js`,并在步骤 4 作为每股 `args.cfg` 原样传入 `l4-stock.js`——**传 `{}` = 静默关 l4_intel + 全体 agent 掉回内建缺省 effort**(2026-07-21 事故:按 .json 旧名查不到→传空→12 只零情报稿+12 卡 xhigh(配置 max);fb_20260721_001,GATE 探针提案 pr_20260721_001)。管控各 stage 的 agent model/effort,优先级 **scan_config > workflow 内建 > agent def frontmatter 默认**(缺配置/缺键 = 现硬编码值,parity)。
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
   - ⚠️ **哨兵判据只问「今天有没有值得买的」,不含「持仓要不要动」**。当 `pinned.jsonc` 有保送持仓时,哨兵档跳 L3/L4 会让持仓拿不到当日卖/持决策卡 → 传 `force_full: true`(Workflow `args`)覆盖哨兵、照常跑 L3/L4(pinned 强注入 L3 → 每只出卡),scan-market.js 会诚实标注「确定性判据判材料枯竭、买单侧期望低」。**2026-07-17 实测**:全市场健康上涨 1.3%(哨兵开火)但 4 只持仓在 192 跌停的崩盘日,靠 `force_full` 才拿到 Sell/UW 决策(协创 Sell·普冉/长飞/北方华创 UW)。哨兵说的「没得买」是对的,它只是不知道你有持仓要判。
2.5. **市场研判兜底**(仅当 0.5 未跑):同 0.5,读 `autoresearch.scan.market.market_pack(scan_dir)` 回退口径(L2 后)。
2.7. **行业 brief**(与步骤 3 证据取数并发;workflow L3 相位):
   ```bash
   uv run --no-sync python -m autoresearch.sector.reuse <date> --apply
   uv run --no-sync python -m autoresearch.sector.pack <date>
   ```
   → 每行业一个 `Agent(subagent_type='sector-brief')`(机制/两段契约见 STAGES.md『旁路 · 行业 brief』节)。
3. **L3 精排**(两遍法:pass1 确定性分诊 200→~40 + holistic 单 agent 深比较出 finalist tier 7–10;workflow L3 相位):`harvest_l3_evidence`+`harvest_l3_news` 补真证据 → `l3_table_md(date, delta=True, sector_terrain=True, dist_flag=True, reg_flag=True, cat_flag=True, misread_flag=True)` 压紧凑表(内含 pass1 分诊:`prepare_l3_table` 先用 `triage_l2_for_l3` 把 ~200 行收到 ~40(scan_config `pass1_target`,2026-07-18 影子验证后 60→40),被切部分是影子,落 `_l3_pass1_cut.csv`,不代表判死)→ 一个 `Agent(subagent_type='l3-rank')` 通看 ~40 只深比较,给出 **finalist tier:7–10 只**(按当天质量,`finalist:true`,宁缺毋滥不凑数)+ 其余判断过但未入选的 **bench**(`finalist:false`)→ `uv run --no-sync python -m autoresearch.scan.menu <date>` 拿 L4 预算(cap = min(10, 预算))→ `merge_l3_finalists_v3(judged, budget=预算)`(conviction≥75 误杀保险强制补入 / <55 剔除 / 健康画像比例守卫)→ `finalists.csv` + bench 落 `_l3_bench.csv`(rubric 维度/推荐旗/token 经济见 STAGES.md L3 节)。**L3.5 闸=passthrough 保留为回测 harness,收窄职能已并入 L3**(用户 2026-07-12 裁定)。
4. **L4 研究**(token 大头;fb_20260714_003:**每股一个独立 `l4-stock` workflow,N 股并行**)——确定性准备(l4-prep)仍在 scan-market.js 的 L4-prep 相位:质押旗/TTL复用/席位·催化·日历生产者先行(机制见 STAGES.md L4 节;观察单直通车已随观察单退役、菜单滞回保席已随 pr_20260716_006 退役)→ 落稿(单步重跑入口):
   ```bash
   uv run --no-sync python -m autoresearch.scan.agents.l4_card pledge <date>
   uv run --no-sync python -m autoresearch.scan.l4_reuse <date> --apply
   uv run --no-sync python -m autoresearch.scan.agents.l4_card prompts <date>
   ```
   → scan-market.js 返回 `{dispatch, meta}` 后,主会话对 dispatch 里每股各拉一个
   `Workflow({scriptPath: '.claude/workflows/l4-stock.js', args: {date, code, name, sector, cfg}})`
   (**cfg = 步骤 0.5 frame 回显的 `user_config` 块原样透传,勿传 `{}`**——空 cfg 静默关 intel/降 effort,见 0.5 节 07-21 事故注;**pinned = dispatch 返回的 `meta[code].pinned` 原样透传**——缺了 SELL 双复核不触发)(degraded=复核 run 不齐时不折回、报告强制人裁)
   ——**一条消息 N 个调用并行**(每股独立并发帽,真并行;单股失败只废单股,单独重跑该 workflow 即可)。
   每股链内:**intel(可关)→ l4-card 决策卡 →(≥OW)2 独立复核 run 取中位只向下折回**,复核落
   `_ensemble_<code>.json`(assemble 合并读)。卡模板/契约烤进 `.claude/agents/l4-card.md`。
   **活体情报站**(config `l4_intel.enabled`):l4-stock 的 Intel 相位,sonnet·max 结构性盲(prompt 只给码/名/行业/日期)盲搜六面落 `_l4_intel_<code>.md`;卡 P3 先读 intel、自发网查降 ≤1 验证,缺文件自动回退卡内网查(presence-gated)。⚠️ 2026-07-14 首跑冒烟:空稿 0/13、中文源可达 ✓,但逮到**捏造涨停断言**(pr_20260714_006 待裁)+ 限频形同虚设/零 URL(pr_20260714_007)——卡片对 intel 的价格类断言必须与 verified OHLCV 对账后才可采信。
5. **L5 整合**(全部 l4-stock workflow 完成后,主会话直接跑;哨兵档跳过 L3/L4 后也走这里):
   ```bash
   uv run --no-sync python -m autoresearch.scan.assemble <date>
   uv run --no-sync python -m autoresearch.scan.gates gate4 <date>
   ```
   → **`reports/scan/<YYYYMMDD_HHMM>/`**(目录名=实际运行时刻,数据日记 `manifest.json`):`summary.md`(漏斗数量/各阶段概览/buy-list/token估算)+ `details/〈股票名称〉.md`+ `trace/`(留溯源)。**汇报**:漏斗 + buy-list(评级/目标)+ 诚实局限。

## 铁律
- **确定性层零 LLM**:L0/L1/**L2**/L5 全 pandas,不在筛选里编数、不预测。
- **召回宽、判断深**:L1 高召回 → L2 分层多样性采样收口(给均衡菜单,非 alpha);真正的多空取舍在 L3 holistic 精排 + L4 决策卡。
- **L3/L4 必须 subagent**:L3 一个 holistic agent(独立 context)+ L4 每只独立 context(每股一个 `l4-stock` workflow),只回传紧凑结果,否则撑爆主线;标准编排路径见流程节顶部(scan-market.js 前段 + N×l4-stock.js + 主会话收尾)。
- **每只 finalist 走 stock-research lite 档**——继承其铁律(数字出自 slim context、五档评级、EV/R:R、`FINAL TRANSACTION PROPOSAL`、诚实局限)。
- **中间名单全 staging**(L2_gbdt / L3_evidence / finalists),L5 发布到 `trace/` 留溯源;re-run 友好。
- **诚实收尾**:召回/粗排是启发式 + fwd_2_oc 超短主尺 IC 校准/训练(2026-07-10 裁定;随 regime 漂移);L3/L4 是 Claude 推理产出;"仅供研究,非投资建议"。

## 常见坑
- 必须 `uv run --no-sync`(不误删 venv-only 的 akshare/tushare/lightgbm)、仓库根目录。
- **默认 `--source tushare`**(东财 push2 常被网络封锁);需 `TUSHARE_TOKEN`。缺端点权限的富因子自动降级 NaN、打分重归一。
- **召回权重 / L2 采样**:`weights.json` 缺失 → 内置先验(能跑但弱);L2 不用模型。改因子/组后只需重跑 L1 校准:`factor_lab harvest`→`calibrate`(线性权重)→`eval`(复核 IC)。L2 为何不做模型的实证见 STAGES.md 核心世界观节。
- `context/`、`reports/` 已 gitignore;别误提交大文件。

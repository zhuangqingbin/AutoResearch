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
- **闭环(开跑前补跑复盘)**:先 `uv run --no-sync python -m autoresearch.learning.retro pending`;列出未复盘日 → 先用 **scan-retro** 补上再开始今天的扫描。连续 0 买时看对照读数:`uv run --no-sync python -m autoresearch.learning.zero_buy_ledger`。
- **一致预期积累(每日 1 拉)**:`uv run --no-sync python -m autoresearch.research.consensus pull <date>`(tushare `report_rc` 限频 **1次/小时**,历史回补不可行);`status` 看进度。**验证门:积累 ≥60 日后 factor_lab 验 IC(两半稳+符号一致)才谈入 composite**。
- **(可选)token 真计量与 cache 审计**:跑扫描的 Claude Code 会话从带 OTEL env 的 shell 启动(五件 env 见 `STAGES.md`『真实计量与跨层校准』节),跑完 `uv run --no-sync python -m autoresearch.trace.telemetry <原始导出> --out reports/scan/<run>/token_telemetry.md`。生产派发路径零改动,仪器旁路。
- 用户对报告的反馈用 **feedback** skill 记。

## 流程(6 段)

> **编排真身 = `.claude/workflows/scan-market.js`**(4 相位/4 GATE:Prelude→L3→L4→Assemble,相位末尾一道 GATE 阻断)。**正常跑动直接用 workflow**;以下是其内部调用的同一批命令,留作**调参/单步重跑入口**。操作模板分驻:市场研判在 `macro-research/macro-playbook.md` 末节、L4 决策卡在 stock-research 的 `lite-playbook.md`;**各阶段机制/参数/实证读数**见 `STAGES.md`。workflow 后台跑时随时用 `/workflows` 看实时进度树(逐卡 spinner + log 计数);各阶段墙钟收尾自动落 `_stage_timing.json`(mtime 推导)。

0. **前奏一键**(workflow Prelude 相位的确定性部分):
   ```bash
   uv run --no-sync python -m autoresearch.scan.prelude <YYYY-MM-DD>
   ```
   跑完全部确定性前奏(attribution 刷新/retro pending 列出/consensus 拉/universe/日历/观察单日检/菜单·L4预算·哨兵建议/journal 等 ledger 刷新,逐件见 STAGES.md 闭环层表)。各步失败不阻断,末尾汇总屏含 **📐/🔁/🚪 当日件建议行**(含「禁注」的行勿贴)。
0.5. **市场研判**(workflow Prelude 相位并行调用):`uv run --no-sync python -m autoresearch.scan.frame <日期> --json` 拿湖派生 market_pack → 一个 `Agent(subagent_type='macro-brief')` 写 `context/scan/<日期>/market_view.md`(模板见 macro-playbook 末节;地形段喂 L3/L4,操作基调/漏斗读数只进 L5)。该命令回显的 `user_config`(`.claude/skills/scan-market/scan_config.json` 白名单校验后,见 `autoresearch/scan/user_config.py`)随 Workflow `args.config` 传入 `scan-market.js`,管控各 stage 的 agent model/effort,优先级 **scan_config > workflow 内建 > agent def frontmatter 默认**(缺配置/缺键 = 现硬编码值,parity)。
1. **L0 选集 + L1 召回 + L2 粗排**(全确定性,零 token;workflow Prelude 相位):
   ```bash
   uv run --no-sync python -m autoresearch.scan.universe [YYYY-MM-DD] --regime-aware [--source tushare] [--recall-n 1000] [--l2-n 200] [--cap-floor 30] [--exclude-bj] [--recall-mode multi|composite] [--recall-channels a,b,c] [--l2-sector-cap 0.20]
   ```
   → `L1_recall_top1000.csv`+`L1_channels.csv`+`L2_gbdt_top200.csv`+`sectors.csv`+`meta.json`(channel/floor 参数见 STAGES.md L1/L2 节)。
2. **过目 + 日历 + 观察单**(单步重跑入口):
   ```bash
   uv run --no-sync python -m autoresearch.scan.calendar <date>
   uv run --no-sync python -c "import autoresearch.scan.watchlist as w; print(w.run_check('<date>','context/scan/<date>'))"
   ```
   菜单体检(`autoresearch.scan.menu.menu_health`)由 L5 自动嵌;出现 ⚠️菜单病 时提前给用户预期。
2.2. **哨兵决策**(确定性建议,人拍板):
   ```bash
   uv run --no-sync python -m autoresearch.scan.menu <date>
   ```
   打印 `[sentinel]` 行(判据见 STAGES.md L2 节);建议哨兵档时只跑观察单+日历+步骤 5(跳 L3+L4,省 ~70% token/~35 分钟)。
2.5. **市场研判兜底**(仅当 0.5 未跑):同 0.5,读 `autoresearch.scan.market.market_pack(scan_dir)` 回退口径(L2 后)。
2.7. **行业 brief**(与步骤 3 证据取数并发;workflow L3 相位):
   ```bash
   uv run --no-sync python -m autoresearch.sector.reuse <date> --apply
   uv run --no-sync python -m autoresearch.sector.pack <date>
   ```
   → 每行业一个 `Agent(subagent_type='sector-brief')`(机制/两段契约见 STAGES.md『旁路 · 行业 brief』节)。
3. **L3 精排**(holistic 单 agent,200→~30;workflow L3 相位):`harvest_l3_evidence`+`harvest_l3_news` 补真证据 → `l3_table_md(date, delta=True, sector_terrain=True, dist_flag=True, reg_flag=True, cat_flag=True, misread_flag=True)` 压紧凑表 → 一个 `Agent(subagent_type='l3-rank')` 通看全表、比较着选 ~30 → `uv run --no-sync python -m autoresearch.scan.menu <date>` 拿 L4 预算 → `merge_l3_finalists_v2(judged, target=预算)` → `finalists.csv`(rubric 维度/推荐旗/token 经济见 STAGES.md L3 节)。
4. **L4 研究**(token 大头,一只=一个 Opus subagent;workflow L4 相位)——helper 在 `autoresearch.scan.agents.l4_card`:派发前四道确定性闸(质押旗/触发直通车/TTL复用+滞回/席位·催化·日历生产者先行,机制见 STAGES.md L4 节)→ 落稿:
   ```bash
   uv run --no-sync python -m autoresearch.scan.agents.l4_card pledge <date>
   uv run --no-sync python -m autoresearch.scan.l4_reuse <date> --apply --carryover
   uv run --no-sync python -m autoresearch.scan.agents.l4_card prompts <date>
   ```
   → 全部 **`Agent(subagent_type='l4-card')` 一条消息并发**(别分 wave;卡模板/契约烤进 `.claude/agents/l4-card.md`)。行业 brief 补漏走 `subagent_type='sector-brief'`。**早停抽检**(opt-in,默认不跑):`l4_card.pick_earlystop_audit(scan_dir, k=2)` 抽样独立复核。
   **活体情报站**(config `l4_intel.enabled`,默认关):dispatch-plan 前移,每只新派票并发一个 `l4-intel`(sonnet·max,结构性盲——prompt 只给码/名/行业/日期)与 slim 预取同窗口盲搜六面,落 `_l4_intel_<code>.md`;卡 P3 先读 intel、自发网查降 ≤1 验证,缺文件自动回退卡内网查(presence-gated;parity 例外仅两处观测面:任务包指针行+summary token 表恒 0 行)。裁决:stage_eval+账本 ≥10–20 日,P1 波验收后才开(design 2026-07-12 §6:冒烟三查=网查限频/中文源可达率/空稿率)。
5. **L5 整合**(workflow Assemble 相位):
   ```bash
   uv run --no-sync python -m autoresearch.scan.assemble <date>
   ```
   → **`reports/scan/<YYYYMMDD_HHMM>/`**(目录名=实际运行时刻,数据日记 `manifest.json`):`summary.md`(漏斗数量/各阶段概览/buy-list/token估算)+ `details/〈股票名称〉.md`+ `trace/`(留溯源)。**汇报**:漏斗 + buy-list(评级/目标)+ 诚实局限。

## 铁律
- **确定性层零 LLM**:L0/L1/**L2**/L5 全 pandas,不在筛选里编数、不预测。
- **召回宽、判断深**:L1 高召回 → L2 分层多样性采样收口(给均衡菜单,非 alpha);真正的多空取舍在 L3 holistic 精排 + L4 决策卡。
- **L3/L4 必须 subagent**:L3 一个 holistic agent(独立 context)+ L4 每只独立 context,只回传紧凑结果,否则撑爆主线;标准编排路径是 `.claude/workflows/scan-market.js`(见流程节顶部)。
- **每只 finalist 走 stock-research lite 档**——继承其铁律(数字出自 slim context、五档评级、EV/R:R、`FINAL TRANSACTION PROPOSAL`、诚实局限)。
- **中间名单全 staging**(L2_gbdt / L3_evidence / finalists),L5 发布到 `trace/` 留溯源;re-run 友好。
- **诚实收尾**:召回/粗排是启发式 + fwd_2_oc 超短主尺 IC 校准/训练(2026-07-10 裁定;随 regime 漂移);L3/L4 是 Claude 推理产出;"仅供研究,非投资建议"。

## 常见坑
- 必须 `uv run --no-sync`(不误删 venv-only 的 akshare/tushare/lightgbm)、仓库根目录。
- **默认 `--source tushare`**(东财 push2 常被网络封锁);需 `TUSHARE_TOKEN`。缺端点权限的富因子自动降级 NaN、打分重归一。
- **召回权重 / L2 采样**:`weights.json` 缺失 → 内置先验(能跑但弱);L2 不用模型。改因子/组后只需重跑 L1 校准:`factor_lab harvest`→`calibrate`(线性权重)→`eval`(复核 IC)。L2 为何不做模型的实证见 STAGES.md 核心世界观节。
- `context/`、`reports/` 已 gitignore;别误提交大文件。

---
name: scan-market
description: Use when the user wants to scan the WHOLE A-share market (not one named ticker) to discover buy-worthy stocks AND strong sectors without paying for an LLM API — e.g. "扫描全A股挖掘值得买的票", "全市场选股", "现在哪些板块值得买", "帮我筛一遍A股龙头", "find the best A-share buys / strongest sectors". For a single known ticker use stock-research instead. Project-local skill.
---

# scan-market — 全 A股六段漏斗扫描(挖掘个股 + 板块,零付费 API)

## 核心原理
对 ~5,500 只逐个跑深度报告 = 几亿 token,不可行。本 skill 用**搜索/推荐系统式六段漏斗**:**确定性层**(零 token)把全市场富因子排序 → GBDT/线性**学习重排**收到 ~200;再让 Claude 当资深投资师 **holistic 一次通看、比较着精排**到 ~30;只对这 ~30 跑 **stock-research(lite 档)决策卡**,最后整合。**token 只跟最终深挖的几十只成正比,与全市场规模无关。**

**渐进深度 + 早停**:L0/L1/**L2 全确定性(零 LLM)**;**L3 精排 = 1 次 Opus-high holistic**(全表一次通看);**L4 = 一只 finalist = 一个 Opus subagent 渐进深度 DD + 早停**(读够真数据才判、判断不好就早停、看着像买点的才深核 P4+P5)。**全程 Opus,省 token 靠早停**。

| 段 | 名称 | 引擎 / 模型 | 作用 | 进→出 | token |
|---|---|---|---|---|---|
| **L0** | 选集 | 确定性 | 全市场候选池 + 硬门(剔 ST/退/停牌/次新 + 市值地板) | 全A→~5,500 | 0 |
| **L1** | 召回 | 确定性 · 多路策略召回 | 10 路 channel(动量/反转/成长/价值/主力/北向/吸筹/**高热成交额**/**健康上涨** + IC 校准复合分)各取 top-Kᶜ → quota union(floor 保底多样性)+ provenance | →1,000 | 0 |
| **L2** | 粗排 | **确定性 · 分层多样性采样(ML-free)** | sector-neutral composite 排序 + 6 风格桶固定 floor(趋势/反转/价值/成长/吸筹/主力,保证每风格不为 0)+ sector cap ≤20%。**不预测、不赌 regime**——实证确定性 L2 无稳健 alpha,只给 L3/L4 建均衡菜单 | →200 | **0** |
| **宏观 lite** | 市场研判(旁路,= macro-research lite 档) | **Opus · 单 agent** | **Stage 0 与 universe 并行**(湖派生 `market_pack_from_frame`;回退=L2 后 `market_pack`)写 `market_view.md`(定调/结构/红黑榜/操作基调)。**地形段喂 L3/L4 做 regime 校准(防锚定:只描述不指令)**、全文进 L5 报告 | 旁路·1 份 | 小 |
| **L3** | 精排 | **Opus-high · holistic 单 agent** | 通看 ~200 比较选 + 增量真证据 + **公告/媒体情感(anns_d+akshare)** + **channel 共振** + 论点/红队/sentiment | →~30 | 中 |
| **L4** | 研究 | **一只=一个 Opus subagent 渐进深度 + 早停** | 决策卡(P0 简报→P1–P3 表面→主早停②→P4 陷阱核→③→P5;`rubric_rating` 派生评级) | ~29 卡 | 大头 || **L5** | 整合 | 确定性 | summary(逐阶段表 + token 估算) + buy-list + 漏斗溯源 | 1 份 | 0 |

> **L2 = 确定性分层多样性采样器(ML-free,`autoresearch.scan.recall.l2_stratify`)**。**为什么不用模型**:实测全 zoo(core/seq/graph 20 模型)× 3 horizon **OOS rank-IC 全负**(champion 只能当"最不伤切"上线),且 4 年回测证**确定性 L2 无稳健 alpha**(composite-top200 ≈ 0、regime 依赖:2022-24 +14~28bps / 2025-26 反转 −24bps);负-IC champion 在反转 regime 把动量/heat 票全压出 L2(实测 0/200)→ L3/L4 只剩落刀。**故 L2 不预测、不赌 regime**,改做三件确定性的事:① **风格桶**(复用 recall_channels:趋势/反转/价值/成长/吸筹/主力)② **固定 floor** 保证每风格不为 0(policy,非模型;趋势 floor 治本"0 趋势")③ **sector-neutral composite**(去行业 beta,回测最优桶内口径)排 merit 核 + 桶内,+ sector cap ≤20%。**分层是免费的**(strat ≈ composite-top200 ≈ 0)→ 多样性零 alpha 代价。zoo/champion 基建留 `models/`(measure-only,不接 L2)。**alpha 只在 L3/L4**。(design: `docs/specs/2026-06-25-l2-stratified-sampler-design.md`)

本 skill 是**编排器**,三类角色分工清楚:
- **确定性层(零 LLM)** = L0/L1/L2(`autoresearch.scan.universe` 一次产出,L2 = `l2_stratify.select_l2` 分层多样性采样,ML-free)+ L5(`autoresearch.scan.assemble`)。纯 pandas,不编数、不预测。
- **AI 判断层** = L3(holistic 单 agent 精排)+ L4(逐只决策卡),`autoresearch.scan.agents.l3_select` / `autoresearch.scan.agents.l4_card` 供紧凑表/取数/合并/级联名单;subagent 只回传紧凑结果。
- **L4 委托 stock-research(lite 档)**:**一只 finalist = 一个 Opus subagent 渐进深度 DD + 早停**(P0 简报定向 → P1–P3 表面 → 主早停② → P4 陷阱核 → ③击杀 → P5 满卡;早停只向下、≥OW 必走 P4+P5)。

## 何时用 / 不用
- ✅ 用户想**一次扫全市场**、挖"值得买的票 / 强势板块"(A股)。
- ❌ 已知**单个** ticker → **stock-research**(full=全量报告 / lite=快速卡)。
- ❌ 港股/美股全市场:本期不支持。

## 前置
- 在**项目根目录**运行;akshare/tushare/lightgbm 已装(venv-only,**务必 `uv run --no-sync`**);`.env` 有 `TUSHARE_TOKEN`(默认源)+ `FRED_API_KEY`(L4 取数)。默认中文。
- **召回权重 + L2 champion**:`weights.json`(`factor_lab calibrate` 产,L1 复合分 + L2 回落基线)+ **lake 历史**(`python -m autoresearch.data.harvest <start> <end>` 落 `context/lake/`)→ **zoo 训练**(`python -m autoresearch.models.zoo train --dates-from … --dates-to …` → `context/factor_lab/zoo_leaderboard.csv` + champion 落 `models/store/l2_<horizon>/`;缺/未胜线性→自动回落)。**校准方法 = `factor_lab` harvest→calibrate→eval(命令见常见坑节);IC 基线由 `feedback_store.render_calibration_block` 注入(store 空时代码内基线回退);附录级明细在 git 历史(screening-playbook 已退役 07-03)**。
  - **regime 分桶权重**(`--regime-aware` 用):`python -m autoresearch.research.factor_lab` 先 `harvest`(密集近期成型日)再 python 里 `fl.calibrate_regimes()` → `weights.json` 增 `regimes` 块。**self_review warn 提示重校准 / regime 明显切换时刷新**;重标定一律走 `retro.recalibrate_and_log`(快照 + changelog 可回滚)。
- **闭环(开跑前补跑复盘)**:先 `uv run --no-sync python -m autoresearch.learning.retro pending`;若列出未复盘日 → 先用 **scan-retro** 把它们补上(权重/经验更到最新)再开始今天的扫描。连续 0 买时看**对照读数**:`uv run --no-sync python -m autoresearch.learning.zero_buy_ledger`(0买日之后市场实际走势 → 纪律 vs 失明)。
- **一致预期积累(每日 1 拉)**:`uv run --no-sync python -m autoresearch.research.consensus pull <date>`(tushare `report_rc` 限频 **1次/小时** → 每天只此一拉,历史回补不可行);`status` 看进度。**验证门:积累 ≥60 日后 factor_lab 验 IC(两半稳+符号一致)才谈入 composite**——盈利修正是事前信息(预告事件研究〔`STAGES.md` 勿重启节,两季对照负结果〕反证:公告后追买无肉,alpha 若有在披露前的预期变化),但不预接未验证 alpha。
  - retro 的 `retro_input.md` 自带 **各阶段 agent edge**(`stage_eval`:L2 重排/L3/L4 各段对已实现收益的 lift/IC)+ **经验升门候选**(`feedback_store.promotion_candidates()`)。
- **(可选)token 真计量与 cache 审计**:跑扫描的 Claude Code 会话从带 OTEL env 的 shell 启动(五件 env 见 `STAGES.md`『真实计量』节),跑完 `uv run --no-sync python -m autoresearch.trace.telemetry <原始导出> --out reports/scan/<run>/token_telemetry.md`(agent×type 分解 + cache 命中率,与落稿估算表对账)。生产派发路径零改动,仪器旁路。
  - L3 的『因子方向经验校准』运行时由 `feedback_store.render_calibration_block(本批申万行业, with_feedback=True)` 注入(近期反馈 + 自学习经验 + IC 基线,三层叠加);用户对报告的反馈用 **feedback** skill 记。

## 流程(6 段)
> 操作模板分驻各能力 skill(2026-07-03 海拔重构):市场研判在 `macro-research/macro-playbook.md` 末节、L4 决策卡在 stock-research 的 `lite-playbook.md`;L3 rubric 要点内联下方步骤与 `STAGES.md`;**各阶段现状快照**(引擎/参数/实证读数/已知局限/勿重启清单)见 `STAGES.md`(as-of 日期标注,冲突以源码为准)。screening-playbook 已退役(历史在 git)。

0. **⚡ 前奏一键(推荐,取代下方 1/2 的人肉串)**:
   ```bash
   uv run --no-sync python -m autoresearch.scan.prelude <YYYY-MM-DD>
   ```
   一条命令跑完全部确定性前奏:attribution 刷新(成熟老日 fwd 补齐)→ retro pending 列出(**只备料,诊断仍走 scan-retro**)→ consensus 拉 → universe(regime-aware+影子)→ 日历 → 观察单日检(**🔔 触发置顶警报**)(v2:提醒(k/n) 分级/⏰by_date 临期/🔥since_born≥+15% 错过旗)→ 菜单/L4 预算/哨兵建议 → journal+buy_ledger+cross_calib+catalyst_ledger+paper_nav 刷新(📈 影子成绩单三线:真实/影子/市场,`真实−影子`=门的价值)。各步失败不阻断,末尾汇总屏(含 **📐/🔁/🚪 当日件建议行**:📐 触价校准贴 `_l4_shared_instructions.md`、🔁 L3 翻案贴 L3 校准块旁、🚪 门柱先验贴校准块旁;**含「禁注」的行勿贴**)。之后从 2.2 哨兵决策接 LLM 段(market_view 已由 0.5 并发产出;未产则按 2.5 补)。下方 1–2 保留作分解说明与调参入口。
0.5. **Stage 0 · 宏观 lite 市场研判(与前奏并发,推荐)**:先 `uv run --no-sync python -m autoresearch.scan.frame <日期> --json` 拿**湖派生 market_pack**(零打分零召回;其取数入湖,随后的 prelude/universe 基本湖命中不重拉)→ **同一条消息并发**:[prelude/universe Bash 后台] + [**1 个 subagent 按 macro-research lite 档**(prompt 模板在其 `macro-playbook.md` 末节「lite 档:市场研判」)读 pack JSON(+ `macro_state.json` 若新鲜)写 `context/scan/<日期>/market_view.md`]。**哨兵拍板前叙事就绪**;此段跑过则 2.5 跳过。(design: 2026-07-03 海拔重构 §5.5)
1. **L0 选集 + L1 召回 + L2 粗排(全确定性,零 token)**:
   ```bash
   uv run --no-sync python -m autoresearch.scan.universe [YYYY-MM-DD] --regime-aware [--source tushare] [--recall-n 1000] [--l2-n 200] [--cap-floor 30] [--exclude-bj] [--recall-mode multi|composite] [--recall-channels a,b,c] [--l2-sector-cap 0.20]
   ```
   → `L1_recall_top1000.csv`(复合分 + 9 子分〔含 volprice〕+ 原始因子 + **多路 provenance `recall_channels`/`n_channels`**)+ **`L1_channels.csv`**(各路召回名单,复盘/学习用)+ **`L2_gbdt_top200.csv`**(分层采样 top200;`l2_rank`=选择序、`gbdt_score`=composite〔显示用〕、`l2_lane_reserved`=floor 救回;`meta.l2_engine`=`stratified(sn_composite)`、`meta.l2_sector_cap`)+ `sectors.csv` + `meta.json`。默认 `--recall-mode multi`(10 路策略召回,含 `heat` 高热与 **`healthy` 质量上涨**〔0<pct60<40∧主力+∧cmf+,与菜单体检同谓词;07-02 取证 261 只该品相 0 进池的结构性空洞修复〕);`composite` 为对拍/回退口径。默认源 tushare、含北交所、日期=今天。**`--regime-aware` 推荐常开**(2026-07-02 起):L1 权重按当日 regime 取 `weights.json` 的 `regimes[trend|range|risk_off]` 块(实证符号结构分化:momentum IC trend −0.055 vs range +0.015;risk_off 下 value 反向、RSI 反转最强)——未知 regime/缺块自动回退 flat,代码默认关=parity。
2. **过目(建议)**:读 `L2_gbdt_top200.csv` 头部 + `sectors.csv`,把粗排概览给用户看一眼。**+ 日历取数**:`uv run --no-sync python -m autoresearch.scan.calendar <date>`(解禁+预约披露,L2∪finalists;→ `calendar.csv`,L4 简报自动带 ⚠️解禁旗/📅披露日,summary 自动嵌两周日历;观察单"中报"类触发用披露日做**确切日期锚**)。**+ 观察单日检**:`uv run --no-sync python -c "import autoresearch.scan.watchlist as w; print(w.run_check('<date>','context/scan/<date>'))"` → `watchlist_status.csv`(L5 自动嵌);**已触发**条目当场呈现(触发≠自动升级,提示按 stock-research lite 档复核)。菜单体检块由 L5 自动嵌(`autoresearch.scan.menu.menu_health`),出现 **⚠️菜单病:健康上涨断供** 时提前给用户预期(该 regime 下大概率 0 买,产物=观察单)。
2.2. **哨兵决策(确定性建议,人拍板)**:`uv run --no-sync python -m autoresearch.scan.menu <date>` 打印 `[sentinel]` 行(判据=**全市场**健康上涨占比 × regime:<3% 材料枯竭 → 建议哨兵;3–5%+risk_off → 建议;≥5% → 全扫)。**哨兵档流程**(用户点头后):只跑 观察单日检(market_view 已由 0.5 产出;未产则按 2.5 补)+ 日历 + 步骤 5 assemble(presence-gated,无 finalists 也出报告:菜单读数/观察单/日历)——**跳过 L3 全表与整轮 L4,省 ~70% token 与 ~35 分钟**。07-02 教训:菜单病在 L2 体检时结局已定,后 40 分钟只是确认。哨兵日 retro 照常归因、影子照算 = 错过率可监控。
2.5. **市场研判兜底(仅当 0.5 未跑)**:L2 后数据已就绪 → 读 `autoresearch.scan.market.market_pack(scan_dir)`(从 `L1_scored_full`+`sectors.csv` 聚合)→ 派**一个 `Agent(model='opus')`** 按 **macro-research lite 档模板**(其 `macro-playbook.md` 末节;原 6 段模板已迁出本 skill)写 `context/scan/<date>/market_view.md`。**地形段(regime/红黑榜/估值分散)前置进 L3 prompt + 每张 L4 卡简报**(`compose_funnel_brief` 已自动注入 `market_context_block`);**操作基调/漏斗读数只进 L5**。铁律:数字出自 `market_pack`(不编数)、**个股评级只由本股 rubric 三门定(大盘看空不压个股、看多不松门)**。
2.7. **Stage 1 · 中观 lite 行业 brief(与 L3 证据取数并发)**:确定性件先行——`uv run --no-sync python -m autoresearch.sector.reuse <date> --apply`(TTL≤5日♻️复用:regime 同+行业中位60日动量位移≤3pp)→ 剩余行业 `uv run --no-sync python -m autoresearch.sector.pack <date>`(自动选:红榜top3∪L2集中度top3∪观察单行业,K≤6 → `context/sector/<date>/<行业>.json`)→ **每行业一个 `Agent(subagent_type='sector-brief')`**(人设/模板已烤进 `.claude/agents/sector-brief.md`,真值源仍 `sector-playbook.md`;prompt 只给 行业名+pack 路径+落点+memo 行;两段契约:地形段喂 L3/L4·研判段仅 L5,含 `**行业方向**` keyed 行)写 `context/scan/<date>/sector_briefs/<行业>.md`。**与步骤 3 的证据取数同一条消息并发 = 零墙钟**;哨兵日跳过。(design: 海拔重构 §5.3/D6)
3. **L3 精排(holistic 单 agent,200→~30)**:`harvest_l3_evidence`(龙虎榜/预告/快报)+ **`harvest_l3_news`(近 ~10 日 anns_d 公告情感,入湖复用)** 补真证据 → `l3_table_md(date, delta=True, sector_terrain=True)` 把 ~200 只压成**一张紧凑表**(前置**全行业地形段**〔申万一级对称覆盖,防"有 brief 行业被高看";参数默认关=parity〕;因子 + 证据 + **公告情感 + 召回 provenance**;**Δ模式**略去"昨判弃且无变化"票省 token,无前日现场自动回退全量,**全量表每 ≤5 个 scan 日至少 1 次**)→ `l3_table_md(..., dist_flag=True, reg_flag=True)` 推荐常开(**主力失真旗**:反号/微量两型确定性标注 + 禁则——07-03 实证抓住 18/30 被 L4 逐卡辟谣的失真占比票;**⚠监管旗**:近10日公告命中 立案/问询/关注函/处罚/诉讼/监管/证监会/交易所 → `news_reg` 列+禁则〔旗票论点必须显式回应监管事项〕,`l3_news.reg_hits` 独立检测器**不动情感口径**;两旗默认关=parity;**📣催化列** `cat_flag=True` 推荐常开(07-05 新:近10日 回购/增持/机构调研/减持 事件计数,prelude `catalyst` 步已按 L2 名单预 harvest `L3_catalyst.csv`;存在性≠方向,减持≥2 的票论点必须显式回应;默认关=parity))→ **一个 `Agent(model='opus')` + high reasoning 通看全表、比较着选**(5 维 rubric:channel 共振/资金/基本面/情感/脆弱;每只出 `论点 + 红队 + 催化 + 确信/脆弱 + lane + sentiment`;**失真旗票不得以主力净流入为核心论点**)→ 落 `L3_judged_full.csv` → **`uv run --no-sync python -m autoresearch.scan.menu <date>` 拿 L4 预算**(病菜单/risk_off 日 target 降到 15–22,省 Opus 于低产日;观察单兜底)→ `merge_l3_finalists_v2(judged, target=预算)`(趋势配额安全网)→ `finalists.csv`。函数在 `autoresearch.scan.agents.l3_select` / `l3_news`。**比较式 > 孤立逐只打分**(后者各看各的、易虚高)。**周频稳定性抽检**(每 ≥5 个 scan 日):`l3_table_md(date, shuffle_seed=<当日YYYYMMDD整数>)` 乱序表再派一个 audit agent,`stability_overlap(正选, 乱序选)['overlap'] < 0.70` → 写 proposal(L3 选择噪声大/rubric 太松)。
4. **L4 研究(token 大头,一只=一个 Opus subagent)**——helper 在 `autoresearch.scan.agents.l4_card`:
   - **🚄 触发直通车(派发前,确定性)**:`uv run --no-sync python -c "from autoresearch.scan.watchlist import append_express; print(append_express('context/scan/<date>'))"` —— 观察单**触发**票若不在今日 finalists,自动追加(lane=watchlist_trigger)直达 L4 再判;防"触发了但当天不在 L2 菜单 → 没人研究"。触发≠升级,评级仍由本卡 rubric 定。
   - **🏭 行业 brief 补漏(派发前,确定性判定)**:finalists 中 ≥2 只同申万一级而该行业无 brief → 按 2.7 补跑该行业(同链论点研究一次、注入全链卡片=摊销)。`compose_funnel_brief` 自动注入 brief 地形段,无 brief 回退行业备忘录行(presence-gated)。
   - **♻️ 卡片 TTL 复用 + 菜单滞回(派发前,确定性)**:`uv run --no-sync python -m autoresearch.scan.l4_reuse <date> --apply --carryover` —— `--carryover` 先做**滞回保席**(昨日 finalist∩今日 L2 但被 L3 换血的 ≤Hold 票追加 lane=carryover,cap 5;churn 90% 架空复用的修法),再判复用:近 4 日已出卡、≤Hold、|Δ价|≤5%、无新公告、regime 未翻、conviction<70 的票**直接复用前卡**(自动拷卡+♻️banner+失效条件),**不 harvest 不派 subagent**;每张省一整次 Opus DD。**前卡 OW三门失守≥2(深否决)豁免 conviction 拦截**——L3 再兴奋也不为失真先验重烧。≥OW 永不复用。
   - **🛡 质押预旗(派发前,确定性;放直通车/复用之后 = finalists 定稿后)**:`uv run --no-sync python -m autoresearch.scan.agents.l4_card pledge <date>` —— finalists 批量 tushare `pledge_stat` → `pledge.csv`(近 7 日其他 scan 日已拉的 code 直接复用,~30 calls 远离限频),简报自动注 **⚠质押旗**(>40% 爆雷红旗 / >20% 偏高,阈值与深核 slim 质押段同源 `scoring.pledge_flag_label`)。**advisory 不动门**——取证 ≥2 周(旗票 × attribution/L4 结局)过硬后升门另走 proposal 人拍板。
   - **L4 · 渐进深度 + 早停**:对**剩余** finalists 每只 `l4_card.compose_funnel_brief(code, scan_dir)` 拼简报前置 slim 顶 → 一个 `Agent(model='opus')` 跑 **stock-research(lite 档)**(`harvest <ticker> <date> --slim` → staging `details/<ticker>.md`)。**P0 简报定向 → P1–P3 表面填 4 维 → 主早停②(非买点 → 早停卡)→ survivor P4 陷阱核(进 P4 前卡片记一行 `进入P4倾向: <Rating>`,供阶段效能计量)→ ③击杀 → P5 满卡;评级由 `l4_card.rubric_rating` 派生(防 gestalt 过度多报)、早停只向下、≥OW 必走 P4+P5**。**派发三步(确定性落稿,2026-07-04)**:① 共享指令写 `_l4_shared_instructions.md` 后跑 `uv run --no-sync python -m autoresearch.scan.agents.l4_card prompts <date>` —— 写 `_harvest_list.txt`(**yfinance 归一后缀,.SH 绝迹**——07-03 空 slim 双跑 10/30 的修法)+ 每卡 `_l4_prompt_<code>.md`(共享块在前+简报在后=prompt cache 前缀;简报自动带 ⚠主力失真标注);② 按 `_harvest_list.txt` 批量预 harvest slim(zero-LLM,烧 Opus 前先验数据完好,**>10KB 才可信**);③ 全部 **`Agent(subagent_type='l4-card')` 一条消息并发**派发——lite 流程/卡模板/契约已烤进 `.claude/agents/l4-card.md`(稳定前缀吃 cache、`进入P4倾向` 等契约不再靠转述),每只 prompt 两行即可:`执行 context/scan/<date>/_l4_prompt_<code>.md(先读任务包再按其指令出卡)`;`_l4_shared_instructions.md` 从此只放**当日件**(市场地形/校准注意/**prelude 给的 📐 触价校准行**——含「禁注」则不贴),机制性内容勿再重复。别分 wave(只徒增 barrier 延迟、不提质量)。
   - **早停抽检(opt-in,默认不跑;07-06 OTEL 成本数据后再定常开)**:0 买日 `l4_card.pick_earlystop_audit(scan_dir, k=2)` 抽 2 张早停卡,各派一个独立 `Agent(model='opus')` 复核员——**只读**该卡 + 漏斗简报 + slim `<!-- P4 深核分界 -->` 之后的块(早停 agent 没读的部分,~10k/张),回答「深核块里有无翻案证据」;verdict 落 `_es_audit_<code>.md`,"误杀嫌疑" 由编排写 proposals。**不改评级**——这是弃单侧的质检(早停卡此前无人看)。
5. **L5 整合**:
   ```bash
   uv run --no-sync python -m autoresearch.scan.assemble <date>
   ```
   → **`reports/scan/<YYYYMMDD_HHMM>/`**(目录名 = **实际运行时刻**;数据日 analysis_date 记 `manifest.json`,解耦,retro 据此定位):`summary.md`(三段:漏斗数量 / 各阶段概览 / **buy-list〔逐阶段结论表 L1→L2→L3→L4〕** + **🏭 行业研判 / 🔗 同链对比**〔sector_briefs 在才嵌,presence-gated;行业方向自动进 sector_ledger〕 + **各阶段 token 估算**)+ `details/〈股票名称〉.md`(决策卡按名称命名)+ `trace/`(每阶段全量数据 + `reasoning/` 留痕 + funnel)。**汇报**:漏斗 + buy-list(评级/目标)+ 诚实局限。

## 铁律
- **确定性层零 LLM**:L0/L1/**L2**/L5 全 pandas,不在筛选里编数、不预测。
- **召回宽、判断深**:L1 高召回(快因子排序)→ L2 分层多样性采样收口(给均衡菜单,非 alpha);真正的多空取舍在 L3 holistic 精排 + L4 决策卡;慢因子(筹码/北向/基本面)在 L3/L4 兑现。
- **L3/L4 必须 subagent**:L3 一个 holistic agent(独立 context)+ L4 每只独立 context,只回传紧凑结果(L3 论点分 / L4 评级目标),否则撑爆主线。量大可选 **workflow** 并行(需用户显式开启)。
- **每只 finalist 走 stock-research lite 档**——继承其铁律(数字出自 slim context、五档评级、EV/R:R、`FINAL TRANSACTION PROPOSAL`、诚实局限)。
- **中间名单全 staging**(L2_gbdt / L3_evidence / finalists),L5 发布到 `trace/` 留溯源;re-run 友好。
- **诚实收尾**:召回/粗排是启发式 + T+1 单 horizon IC 校准/训练(随 regime 漂移);L3/L4 是 Claude 推理产出;"仅供研究,非投资建议"。

## 常见坑
- 必须 `uv run --no-sync`(不误删 venv-only 的 akshare/tushare/lightgbm)、仓库根目录。
- **默认 `--source tushare`**(东财 push2 常被网络封锁);需 `TUSHARE_TOKEN`。富因子(资金结构/筹码集中度/北向/RSI)缺端点权限则自动降级置 NaN、打分重归一。
- **召回权重 / L2 采样**:`weights.json` 缺失 → 内置先验(能跑但弱);L2 不用模型(分层采样按 sector-neutral composite + 风格 floor + sector cap)。改因子/组后只需重跑 L1 校准:`factor_lab harvest`→`calibrate`(线性权重)→`eval`(复核 IC)。
- **L2 为何不做模型**(实证):全 zoo OOS rank-IC 全负 + 4 年回测证确定性 L2 无稳健 alpha、regime 依赖(证据全录 `docs/specs/2026-06-25-l2-stratified-sampler-design.md` 附录 B)。`models/zoo` 基建保留为 **measure-only 研究**(不接 L2);真要 L2 alpha 需换特征(盈利修正/北向变化等),非当前数据能解。
- `context/`、`reports/` 已 gitignore;别误提交大文件。

---
## 设计沿革(可选背景,删除不影响运行)
本 skill 文档 = 本文(编排)+ `STAGES.md`(现状快照);操作模板按海拔分驻 `macro-research/macro-playbook.md`(市场研判)与 stock-research 的 `lite-playbook.md`(L4 决策卡)。screening-playbook 已于 2026-07-03 海拔重构中退役(内容分流至上述 + STAGES,历史在 git)。下列 `docs/specs/` 仅历史设计推演,**删掉不影响运行**,且部分已落后于现实现(以本 skill 为准):
- `2026-06-20-scan-market-v2-design.md` — 六段漏斗 + 召回校准方法母文档
- `2026-06-20-l2-dual-lane-design.md` — L2 旧双赛道 AI keep/cut(**已被 L2 确定性 GBDT 学习重排取代**)
- `2026-06-21-cost-cascade-design.md` — 模型成本级联(Sonnet 宽段 / Opus 顶点)
- `2026-06-21-agent-upgrade-design.md` — C 评分卡 rubric / A 多空辩论 / B 3透镜共识 / E 记忆闭环 / F 各阶段 eval

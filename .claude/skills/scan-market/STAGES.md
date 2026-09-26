# scan-market 各阶段现状快照

> 本文件只记**当前态**;沿革看 git log 与 `docs/specs/`,**冲突以源码为准**。运维细节与计量口径 → `docs/ops/scan-ops.md`;负结果、退役清单、开放线头、E6 沿革 → `docs/research/scan-negative-results.md`。
> 文档分工:`SKILL.md` 讲**怎么跑**;agent 契约的唯一真身在 `.claude/agents/*.md`(市场研判 `macro-brief`、行业 brief `sector-brief`、精排 `l3-rank`、情报 `l4-intel`、决策卡 `l4-card`);`macro-playbook.md` / `lite-playbook.md` 只是指针。
> **路径约定**:`$CTX`/`$RPT` = 本引擎工作区根(Claude→`context_claude`/`reports_claude`,Codex→`context_codex`/`reports_codex`)。数据湖 `lake/` 两引擎共享。

---

## 一、漏斗全景

```
L0 选集  →  L1 召回  →  L2 粗排  →  L3 精排(两遍法)      →  L4 研究     →  L5 整合
全A ~5500    top1000    top200     pass1→~40→finalist 7–10   决策卡×(7–10   1 份报告
(确定性)   (确定性)   (确定性)   (确定性+Opus×1)          +📌保送)    (确定性)
```

**两条旁路**(并行算好后喂进主链):**市场研判**(macro-research lite 档,Stage 0 与 L0 并行,`macro-brief` 写 `market_view.md`,L3/L4/L5 三处复用)+ **行业 brief**(sector-research lite 档,L2 后按行业并发 `sector-brief`)。主链之外无其它层:事后闭环、L1 影子漏斗、L4 复用层均已退役(见 negative-results)。

**两层角色分工**:确定性层(L0/L1/L2/L5 + 全部度量,零 LLM 纯 pandas 不编数)/ AI 判断层(L3/L4/策略师/行业 brief,全 subagent 只回传紧凑结果)。

## 二、核心世界观(实证结论,决定功夫花在哪)

- **确定性层没有 alpha。** L2 全部 zoo 模型 OOS rank-IC 为负;4 年回测 composite-top200 收益 ≈ 0。→ L2 不做预测,只做"菜单"(多样性采样)。
- **判断层已证的 edge 在「拒绝」,不在「挑选」。** L3 真选无正 alpha 证据;拒绝侧:L4 评级 rank-IC +0.55 分档单调、门价值 +4.35pp,L4 推翻 L3 高确信两次全对。
- **0 买的根因在召回线。** 413 只 T+1 赢家 91% 落在打分池、仅 4.8% 越过 top1000 召回线。
- **0 买不等于失灵。** 历史 0 买日市场 fwd_1 −0.48% / fwd_5 −0.60%;哪天 0 买日市场却涨,才是失明预警。

---

## L0 · 选集 —— `autoresearch.scan.universe`(确定性)

- **硬门**:剔 ST/退市/停牌/次新;市值地板(默认 30 亿)+ 北交所默认纳入(旋钮在 `scan_config.jsonc` 的 `l0` 块)。
- **哲学**:只剔"确定不可交易/不可研究"的——**每加一条硬门就是一块永久盲区**(missed_l0 ≈ 赢家 9%,以小盘/次新/北交所为主)。

## L1 · 召回 —— `autoresearch.scan.recall`(确定性,→1000)

多路策略并行:每路"过门 → 按信号排序 → 截 top-quota" → `quota_union` 合并(各路 floor 保底多样性),带 provenance。已注册 14 路,**默认启用 10 路**(`funnel.recall_channels`;停用的 4 路及理由见 negative-results;⚠️ 该 key 缺省 = 全部 14 路,别删整行):

| 通道 | quota/floor | 信号 |
|---|---|---|
| composite | 400/100 | 复合分(生产走偏好档,见下) |
| momentum | 188/50 | 趋势龙头 |
| reversal | 200/50 | 困境反转(旧路,与 reversal_confirm 并存) |
| reversal_confirm | 150/50 | 反转确认四段:低位 + 企稳(D−1 截止缩量、RSI 20~85)+ **放量起爆硬门**(`vol_ratio_20`≥1.5 ∧ 站回 MA20 ∧ MA5>MA10)+ 可交易 |
| lowturn | 120/40 | **低位转强**:门 = `common/turnup.lowturn_mask`(与 L3 旗同一谓词同一阈值,阈值住 `l3.lowturn`),排序 = `reversal_confirm_score`;画像门比 reversal_confirm 宽 |
| value | 312/50 | 行业内低估(胜率 57.6%/+0.9% 全路最优) |
| main_fund | 150/50 | 主力净流入 |
| heat | 112/50 | 成交额量级(捞巨额龙头) |
| growth | 112/40 | 成长加速 |
| healthy | 112/40 | 质量上涨(0<pct60<40 且主力净流入>0 且 cmf>0) |

- **配额覆盖**:`funnel.channel_quotas` 8 键全写(value 312 / momentum 188 / heat 112 / healthy 112 / growth 112 / main_fund 150 / reversal_confirm 150 / lowturn 120);兜底读取在 `universe.run` 本体(`_funnel_overlay`),显式参数/CLI flag 恒优先,任一键缺省回落注册表默认而非维持覆盖值。
- **rz 因子组**:融资买入强度 `rz_buy_intensity` 独立第 10 因子组(情绪接力资金代理,非基本面确认)。
- **权重档**(`funnel.weight_profile`,唯一入口 `common.scoring.resolve_weights`):`"calibrated"` 读 `weights.json` 的 IC 校准权重并按当日 regime 取块(`common/regime.py`:breadth≥0.55 且 pct_60d>0 → trend;breadth≤0.30 且 pct_60d<0 → risk_off;其余 range;缺块回退 flat);**生产现档 `"preference"`**:固定符号「上涨趋势 + 有支撑 + 主力真在 + 散户不拥挤」,量级是产品裁定不是拟合,`regime_aware` 无操作(meta 记 `regime_applied=None`),`weights.json` 自此仅供研究。`weights_used.json`/`meta.json.weights_source` 记 profile + `config_sha256`。

## L2 · 粗排 —— `recall/l2_stratify.select_l2`(确定性分层采样,→200)

**不用机器学习**:① sector-neutral composite 排 merit;② 8 风格桶固定 floor(明细 = `l2_stratify.DEFAULT_FLOORS`;生产 `l2.floors` 覆盖健康 25 / 反转 6 / 低位转强 6),**未启用通道的桶 floor 运行时归零**(`effective_floors`)——新通道的回滚杆只剩一根:从 `recall_channels` 摘掉它;③ 任一 `industry` 标签 ≤20%(`l2.sector_cap`)。产物 `L2_gbdt_top200.csv`:`l2_rank`=选择序、`gbdt_score`=composite、`l2_lane_reserved`=被 floor 救回。
- ⚠️ **「行业」= 东财所处行业(129 个细标签),不是申万一级**——sector-neutral 去均值与 `sector_cap` 用同一列 `industry`;20% 帽在这种粒度下几乎从不触发,真正拦同板块扎堆的是 L3 守卫⑧的 3 席帽。本条只把口径说对,不改判据。
- **落刀帽**(`l2.knife_cap`,生产已开):merit 核/floor 桶/回填三步各自的落刀份额 ≤ 当日 L0 全市场帧的落刀面(`falling_knife_mask`);反转/低位转强两桶豁免;被帽跳过的行由下一个非落刀候选顶上,顶替行打布尔列 **`knife_cap_swap`**(复盘数这一列,不要数 `selection_detail == "knife_cap"`,floor 桶顶替行恒写桶名)。
- **行业席位**(`l2.sector_seats`,生产 `{enabled:true, per_sector:2, max_sectors:3}`):`universe.run` 在 `scored` 就绪后、召回前用 `sector_healthy_top3` 选入围行业,行业内取非落刀健康上涨成员按当日 composite 降序各取 `per_sector` 只(剔 📌/ST/当日涨幅≥9.5%),`selection_reason="sector_seat"` 全程直通(镜像 `pinned`,不占 l2_n 名额、不进 `recall_n`),不净增 L4 卡数。
- **菜单体检**(`scan/menu.py`):行业集中度/落刀面/健康上涨/估值四项,自动嵌 L5;健康上涨=0 打 ⚠️菜单病。
- **哨兵建议**(`menu.sentinel_advice`,按全市场健康占比):<3% 建议哨兵档(跳 L3+L4);3–5% 仅 consider;≥5% 全扫。**由人拍板不自动**。

## 旁路 · S1 情绪温度计(展示先行)

- tushare `limit_list_d` 入湖(勿用 akshare 涨停池);五序列(涨停/跌停家数、连板高度、晋级率、炸板率、昨涨停今溢价)→ `score` 0-100 + 五相位(冰点<20/修复/发酵/高潮≥65/退潮,±3 滞回),幂等增量 `$CTX/learning/temperature.csv`。
- 消费:market_pack `temperature` 块(presence-gated)+ L5 🌡 行 + prelude `temperature` 步;分段校准 `python -m autoresearch.scan.temperature_calib`。**边界**:不接菜单/预算联动;涨停数据只进温度计。

## 旁路 · 市场研判 = macro-research lite 档(`macro-brief` ×1)

Stage 0 与 L0 并行,回退到 L2 之后落盘。
- **机制**:`frame --json-out` 落 `market_pack.json` + 单向投影 `strategist_pack.json`(allowlist 之外的 `sector_healthy_top3` / `run_contract` / `user_config` 进不来 —— 防锚定是数据级的)→ `macro-brief` 读 `pack` 段(+ presence-gated `macro_state`)写六小节 `market_view.md`。三处复用:L3 地形段、L4 `market_context_block`、L5 置顶;缺文件 → L5 回退确定性脉搏。
- **防锚定铁律**:喂 L3/L4 的只能是**描述性地形**(§1–3),不能是方向指令;操作建议(§4–5)只进 L5;**个股评级只由本股 rubric 三门决定**。
- **配置装载链**:见 SKILL.md「配置」节。`agents` 只声明 role→tier,`agent_engines` 分别解释 Claude 的 model/effort 与 Codex 的 model/reasoning_effort;`_resolved_agent_config.json` 分开记录 declared / runtime capability / resolved,`usage_reconcile` 再与 actual 对账。

## 旁路 · 行业 brief = sector-research lite 档(`sector-brief` ×K)

L2 之后、与 L3 证据取数**并发**:`sector.reuse <date> --apply`(TTL ≤5 日 ♻️ 复用)→ 剩余 `sector.pack <date>`(红榜 top3 ∪ L2 集中度 top3 ∪ 存量 watchlist.csv 行业,K≤6)→ 每行业一个 `sector-brief` 写**单段**契约 brief `## 地形段`(`sector/brief.py` 的 `TERRAIN_HDR`,逐字前缀匹配,改一个字 = L3 表头与 L4 简报同时静默丢掉整段)。行业方向叙事走确定性 top3(`market.sector_healthy_top3`)。**消费**:`l3_table_md(sector_terrain=True)` 只渲染 L2 top200 覆盖行业;L4 简报注入该行业地形段;assemble 自动嵌 🔗 同链对比(presence-gated)。**不解决 0 买也不设门。**

---

## L3 · 精排 —— pass1 确定性分诊 + holistic 单 Opus 深比较(200 → ~40 → finalist tier 7–10)

**📌 保送票也走 L3**:pass1「pinned 全入」→ `l3-rank` 照常独立判(`finalist:false` 不占名额)→ `_inject_pinned_finalists` 把这份判断整段带进 finalists.csv。**保送 ≠ 免判**。

**两遍法**(`l3.two_pass` 默认开):
1. `harvest_l3_evidence`(龙虎榜/预告/快报)+ `harvest_l3_news`(公告情感,cninfo 兜底)补证据;
2. **pass1 分诊**(`triage_l2_for_l3`,零 LLM):pinned 全入 + composite 前 5 + lowturn 强留 ≤8 + `sector_seat`/composite 证据席强留 + 各通道 top-K 轮询,~200 行收到 `pass1_target`(现 40);healthy lane 不再全入;被切的落影子 `_l3_pass1_cut.csv`;
3. `l3_table_md` 压紧凑表:`pct_1d` 与 `dist_high_60` 两列 + pf 词「今日大涨」(≥9.5)/「贴顶」;presence-gated `seat` 列(🏭 行业席位)+ 图例;
4. `l3-rank`(max)通看 ~40 只,按 6 维 rubric **比较着选**,给出 finalist tier 7–10 只 + bench(`_l3_bench.csv`);
5. `L3_judged_full.csv` → `merge_l3_finalists_v3` 确定性守卫,**按序** ①`ins75`(conviction≥75 未标 finalist 强制补入)→ ②`lt55`(<55 剔除)→ ③`cap`(=GATE1 回显的 `l3cap` = min(`l4.max_cards`−席位数, 当日 l4_budget),唯一算法 `scan/l4/card_count`)→ ⑦`chase_1d`(`pct_1d`≥9.5 剔除 + bench 回填 `chase_backfill`,≥55 才够格)→ ④`healthy_quota`(现 `HEALTHY_QUOTA_FRAC=0` 不动作)→ ⑤`trend_quota`(soft 2 席)→ ⑥`lowturn_quota`(soft 1 席,qualify 55)→ ⑧`sector_cap`(同 `sector` >3 席剔最弱 + 回填异行业 `sector_backfill`)→ ⑨`composite_seat`(当日 L2 菜单 composite 最高的 M=3 只强制进 finalists,`guard=composite_seat`/`lane=composite`,与 📌 同级,不占 finalist 名额(计入 `l4.max_cards`)、不受②③约束,剔 📌/ST/`pct_1d≥9.5`)→ ⑩`max_cards`(非 📌 行含席位总数 ≤ `l4.max_cards`,超出按席位优先、conviction 截尾进 bench,`guard=max_cards`)。各守卫的列缺 → 整段 no-op(parity);📌 保送在全部守卫之后注入,不受⑦⑧影响;
6. 注入:策略师地形段。

**judged 输出契约**:每元素含 `mechanism`(两日内兑现机制 + 明日买家,写不出不选)与行为化 conviction(≥70 = 能说出 D+1 谁买且愿真金买入,每日 ≥70 限 ~5 只;50-69 = 值得 L4 验不背书)。

**token 经济与预算**:`delta=True` 略去无变化票;L4 卡数由 `l4.max_cards` 定(📌 不占额;`menu.l4_budget` 五面旗只压 finalist 名额:落刀>60% / 相对落刀>40% 且>2×全市场 / 健康涨≤2 / risk_off / 0买连败≥3;1 旗→22、≥2 旗→15);`l3cap`/`max_cards` 由 GATE1 回显(`card_count.effective_caps`):`l3cap` 进 L3 区间与 `l3_select --budget`,`max_cards` 作 GATE2 预算(GATE2 数非豁免 lane 全部行,席位也算)。

**三面旗**(presence-gated,缺=parity):主力失真 `dist_flag`(反号/微量)、监管 `reg_flag`(近 10 日立案/问询/处罚)、误读三预警 `misread_flag`(低基:np_yoy>100 且 roe<8;背离:cmf/obv 正但 main_net_ratio<0;套牢:winner_rate<25 且 ma_bull=0 且 pct_60d>0;L4 简报同步注旗,l3-rank 硬约束 E 强制自证)。

**误读三闸**:行语义指纹 `pf` 列(确定性画像短语);表按 lane 分块渲染;`l3_select lint <date>` thesis 数字机检 → `repair-pack` 只写失败行 → agent 产 `{code, thesis}` patch → `apply-repair` 同一 lint 谓词复验后原子 merge。

**链路**:L3 → GATE2(只读校验:6 位码 + exempt lane 记账)→ L4 派发,中间无收窄层。

---

## L4 · 研究 —— 一只票 = 一个 Opus subagent(渐进深度 + 早停)

### 派发前确定性闸(按序,生产者都跑在 prompts 之前)

- ⓪ **批量质押旗**:`l4_card pledge <date>` → `pledge.csv`(质押>40 爆雷、>20 偏高;advisory 不动门)。
- ① **复用层:无**。
- ② **生产者落稿**:席位/催化/日历/卖方修正(consensus)——都先于 prompts。

### 派发三步

1. 落 `_l4_shared_instructions.md`(只有标头的稳定骨架)→ `l4_card prompts <date>` 落 `_harvest_list.txt` + `_l4_prompt_<code>.md`。
2. 默认 `streaming_l4=true`:初始化 `_l4_tasks.json`(状态 `PENDING/RUNNING/SUCCEEDED/FAILED/BLOCKED`,逐票记 prompt/slim/card hash、attempt、pinned、错误类、时间戳)。**init 前置 prompts 落稿并做硬门校验**(缺 → `ok:false` 整线停在派发前)。任务簿票数 = finalists.csv 行数,发布前 `l4_card_count_lint` 对账(非📌 > `l4.max_cards` → warn)。派发帽 `caps.l4_stock` 一次全派;发生 `RATE_LIMIT` 后下一批降宽一档。
3. 每股 `l4-stock` 先 preflight;本票 slim 与 `l4-intel` 并行,二者终态后出卡(`l4-card`)。成功仅在 prompt/slim/card hash 全验证时复用;只有 `RATE_LIMIT`/`CONNECTION`/`TIMEOUT` 允许第 2 次尝试,schema/contract/data-integrity 失败不重试且不碰其它票。`streaming_l4=false` 回滚旧批量 `harvest-slim` GATE3。子命令语义与单票恢复见 `docs/ops/scan-ops.md`。

**⛔ 强制满卡**(`force_full_card`):逐卡块内插「禁止早停」,两条通路任一成立即触发——① 📌 保送票恒强制;② 强先验:`conviction≥70` ∧(`n_channels≥4` ∨ L2 配额救回)。只保证核得够深,不保证结论向好。

**pinned 透传铁律**:派发前对照 workflow 打印的「📌 保送票 N 只」行逐一核对每只都带 `pinned:true`——漏传使持仓 SELL 双复核整段不跑。

**活体情报**(`l4_intel.enabled` 已开):`l4-intel`(sonnet·max 盲搜六面)与 slim 预取并行,落 `_l4_intel_<code>.md`,卡 P3 先读。**铁律:卡片对 intel 的价格类断言必须与 verified OHLCV 对账后才可采信**(情报站曾捏造涨停断言)。`intel_guard` 拒稿自报条数超硬顶的稿;`intel_status` 逐票落状态。

### 渐进深度 + 早停

```
P0 简报(市场地形+档案+解禁/披露/调样旗+行业备忘+误读预警)
  → P1–P3 表面填 4 维
  → 【主早停②】非买点 → 早停卡(短格式;未核维标「未核」)
  → survivor 读 deep 进 P4 陷阱核(质押/商誉/解禁/审计/现金流,记「进入P4倾向」)
  → ③ 击杀
  → P5 满卡
```

评级由 `rubric_rating` 派生;早停只向下;≥OW 必走完 P4+P5。契约全文在 `.claude/agents/l4-card.md`。
- **防污染**:简报的 L3 论点是**中性前提清单**,conviction 在"L3 元数据"行注明"读完 P1 数字后再看";l4-card 铁律「先读数据后读论点」(P1 盲读:先写 3 行独立初判)。
- **买单 ensemble**:≥OW 新派卡各追加 2 独立 `l4-card` run(复核卡落 `ensemble/`),取中位、**只向下折回**;run2 与 run1 同档即早止跳过 run3;spread≥2 档 → 🎭 badge + 组合视角人裁行;`_ensemble.json` 缺 = parity。📌 持仓的 SELL 卡同样双复核(只向温和折回)。
- **阶段效能**:早停率随 regime 波动大(20%~100%),弱市高早停是纪律不是失灵;早停 238/239 停在 P3,停因是实质判断不是取数失败。**别为凑买单放宽资金/估值门。**

---

## L5 · 整合 —— `scan/assemble.py` 兼容 CLI(确定性,零 LLM 铁律)

实现归属为单向链:`l4/parsers → decision_finalize → report_sections → publisher → post_run`。`assemble.py` 只兼容导出与 CLI;仓内消费者必须直连 owner,禁止从兼容 adapter 反向 import;Workflow 只调度,不持有 rating/gate 规则。

**报告双层 = 一个发布包**(`docs/specs/2026-08-28-summary-slimdown-design.md`):`brief.md`(≤3KB 入口,确定性模板,同 run 重放 byte 稳定)+ `summary.md`(决策层,11 节)+ `appendix.md`(现场层 A–G)。summary/appendix 由一次 `report_sections.prepare_report_model()` 冻结出的 `ReportModel` 纯渲染而来;节序真身 = `report_sections.py` 模块头。**事实归属**:每个事实在 summary 里只有一个展开点(regime/温度/定调 → 仪表盘①;BUY/BLOCKED → 仪表盘③;持仓总动作 → 仪表盘④;单票 → 候选/保送表;无 BUY 的统计 → 节 6;漏斗现场与门柱口径 → appendix)。字节预算展示层 warn 不截断(summary 目标 12KB / warn 16KB,appendix 20KB / 24KB)。机器消费者不读、不解析 `summary.md` 正文。

- **GATE4 severity 口径**:单一事实源 `scan.self_review.BRIEF_LINT_SEVERITY`(fail = 报告说假话:数字对账 / brief↔summary 不一致 / 白名单外取数 / active 期 BUY 契约;warn = 缺失/超预算/边表)。
- **现场完备**:发布同时写 `run_health.json` + `index.md`(第二天回看从 index.md 进);`weights_used.json` + meta.regime 固化。
- **计量时序**:assemble 时报告先写 `UNMEASURED`;CP7 跑 usage_harvest 后由 `post_run observe` 原位替换 managed section。**预算只告警**:超 cache 红线/阶段成本/墙钟只产 `DEGRADED` StageResult,不截候选、查询、卡或阶段;成熟门 = 至少 10 次真实扫描,此前恒 `IMMATURE`。
- **观察单已退役**;存量 `$CTX/watchlist.csv` 保留(sector.pack 行业选择器仍读)。
- **法证 run capsule**(`autoresearch/trace/`):三个结论互不替代——`integrity_ok` / `completeness_ok` / `replayability`;**`MANIFEST` 通过 ≠ 完整**。机制、绑定、核验命令与「回放禁写 run 目录」见 `docs/ops/scan-ops.md`。
- **结果账本**(`scan/outcome.py`,只记不学)与**时间锚**(`scan/exec_anchor.py`,读 BUY 战绩前先看 `actionability`):见 `docs/ops/scan-ops.md`。
- **不可买归因**(`scan/buyability.py`,零 LLM,`post_run.observe` 在 `relative_buy` 决策写完之后跑):`_buyability.json` 的 `wall` 五态之一——`menu`(L2 落刀>L0+6pp 或 L2 健康<L0 健康)/ `cards_silent`(解析成功的卡里没有一张写过机读入场行)/ `cards_refused`(写了入场行但不是允许)/ `gates`(有卡写允许但全被硬门否决)/ `none`(出了一只 A 级)。brief ③ 固定格式转译一行;账本 `runs.csv` 的 `n_buy_a`/`wall`,`stage_rulers.csv` 的 `E6/e6_a_tier_day_share`(只记录不设门)。

---

## 行为变更的入口

2026-08-21 用户裁定「整个 learning 层退役」之后没有治理链条:涉及召回、L3、门、早停、评级、Token 或速度的改动 = **普通开发改动**(人判断 → 改 `scan_config.jsonc` 或代码 → 测试锁 → 合入)。无自动学习、无影子账本呈证、无 proposal 裁决通道。

**保留的三件门/尺**(它们从来不是"学习"):`scan/self_review.py`(发布前机械自检 + `brief_lint` + `gate_fires.csv`,GATE4 判据真身)/ `scan/tripwire_watch.py`(决策卡价格线 vs 今日收盘,进 brief ⑤ 与 prelude ⚡ 行,仅人看)/ `scan/temperature_calib.py` 内联的 `trade_days`/`market_nav`。

**E6 现行(`scan/relative_buy.py`,`RULE_VERSION="e6.v4.1"`)**:

| 件 | 键 | 内容 |
|---|---|---|
| 候选池 | `relative_buy.pool` | `"finalists"`(生产);`"composite"` = 只在守卫⑨证据席里选、按 composite 分排 |
| 硬门 | 代码 frozenset | `data_a`(票级)/ `contract` / `no_redflag`(UW/Sell 评级、FINAL SELL、早停停因 ∈ {基本面恶化, 估值透支, 涨停追高, 数据不足}、卡面 `entry_stance=="PROHIBITED"`)/ `rebalance_close`(调样生效前夜的调样票,`relative_buy.rebalance_gate`) |
| 分级 | `relative_buy.tiering` | A 级 = eligible ∧ ¬pinned ∧ `entry_stance=="ALLOWED"`;A 空退 R 级(CONDITIONAL/UNKNOWN);两级皆空 → 诚实 `blocked`;`buys[0]` 带 `tier`/`basis`,顶层 `tier_counts` |
| 执行线 | 卡片两行 | `[执行线] pct_chg <= 3.0` / `pos_in_range < 0.7`(T+1 尾盘入场条件;`tripwire_watch` 解析不报警;事后计量 `outcome.exec_ok`) |
| 盲卡 | — | task-book `status!="SUCCEEDED"` 或 slim 缺席的票不写 `_final_ratings.json`,落 `_blind_cards.json` |
| 记录 | `stage_rulers.csv` | `E6/e6_a_tier_day_share` 只记录不设门(A 级结构性为 0:早停卡不得写「允许」而 70% 是早停卡) |

用户裁定「成功交易日 ≥1 BUY」由 R 级照常满足。沿革与证据见 negative-results。

---

## 覆盖档案链 —— `autoresearch/dossier`(整条保留)

常备覆盖模型:`coverage_pool.json` 池(prelude 日检:进=pinned/20日真选≥2、退=20日未选、cap30 LRU)→ `$CTX/knowledge/dossiers/<code>.md` 八节档案(`dossier-init` workflow 首覆)→ L4 prompt 注入「📚 覆盖档案摘要」(`schema.injectable_summary` 四门)+ intel prompt 内嵌已知底 → 卡写「档案对账」节 → assemble 尾 `delta.record_scan_deltas` 按终评级回写 §8 + 刷新 §2/§3/§4/§6/§7 → 季度对账 `python -m autoresearch.dossier.reconcile <period>`(prelude 📐 提醒 + 🕰️ 90 日陈旧告警)。全链 presence-gated:无档案 = 注入前行为逐字节不变。**与 `scan/dossier.py` 的「前科卡」(跨日入围史,强制卡内"变化项"节)是两件事,并存不互替。** 档案记的是公司事实,不是系统从历史判断里学到的东西,所以不随 learning 层走。

---

## 数据层要点

- **源**:tushare 默认(push2 被网络封锁;`TUSHARE_TOKEN` 高权限);keyless 可达:同花顺一致预期(L4 fwd-PE)/ 腾讯 / datacenter-web。限频:`report_rc` 1 次/小时。
- 🚨 **数据契约**(`autoresearch/data/contracts.py`):**A 级**(daily/daily_basic/moneyflow/cyq_perf/stk_factor_pro/stock_basic/trade_cal)空/行数腰斩/缺列 → `DataContractError` **阻断整条流程**且拒绝入湖,**不得被任何 `except Exception` 吞掉**;**B 级**(北向/两融/龙虎榜/公告/质押/新闻/宏观)缺失只降级但**必须记账**(`degradations()` → `degraded.json` → 报告一行)。真正的病是**降级不留痕**。湖体检:`python -m autoresearch.data.contracts doctor [--purge]`。
- 🚨 **入湖一律全字段**(`cache._lake_params`):`_cache_key` 不含 `fields`,带窄 `fields` 的首个写入者会把窄表钉成该日快照。**要窄列自己 `df[cols]`。**
- **降级**:缺权限 B 级端点自动 NaN、打分重新归一(且记账)。

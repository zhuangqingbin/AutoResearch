# scan-market 各阶段现状快照

> 本文件只记**当前态**;沿革一律看 git log 与 `docs/specs/`,**冲突时以源码为准**。
> 文档分工:`SKILL.md` 讲**怎么跑**;操作模板分驻各能力 skill —— 市场研判在 `macro-playbook.md` 末节,L4 决策卡在 `stock-research` 的 `lite-playbook.md`。
> **路径约定**:`$CTX`/`$RPT` = 本引擎工作区根(Claude→`context_claude`/`reports_claude`,Codex→`context_codex`/`reports_codex`;shell 里 `CTX=context_${AUTORESEARCH_ENGINE:-claude}`)。数据湖 `lake/` 两引擎共享;工具调用时把 `$CTX`/`$RPT` 代入具体目录名。

---

## 一、漏斗全景

主链六段,从全市场一路收窄到一份报告:

```
L0 选集  →  L1 召回  →  L2 粗排  →  L3 精排(两遍法)      →  L4 研究     →  L5 整合
全A ~5500    top1000    top200     pass1→~40→finalist 7–10   决策卡×(7–10   1 份报告
（确定性）  （确定性）  （确定性）  （确定性+Opus×1）          +📌保送）    （确定性）
```

**两条旁路**(并行算好后喂进主链):**市场研判**(macro-research lite 档,Stage 0 与 L0 并行,Opus×1 写 `market_view.md`,L3/L4/L5 三处复用)+ **行业 brief**(sector-research lite 档,L2 后按行业并发)。

**主链之外**:无。**事后闭环与 L1 影子漏斗已整体退役**(2026-08-21 用户裁定,见「行为变更的入口」节)。L4 派发前没有任何复用层(TTL 复用已退役,用户裁定「不要任何复用」;观察单直通车随观察单一并退役)。

**两层角色分工**:确定性层(L0/L1/L2/L5+全部度量,零 LLM 纯 pandas 不编数)/ AI 判断层(L3/L4/策略师,全 Opus subagent 只回传紧凑结果)。原第三层「闭环层」(`autoresearch/learning`)已于 2026-08-21 整体退役。

---

## 二、核心世界观(实证结论,决定功夫花在哪)

- **确定性层没有 alpha。** L2 全部 zoo 模型 OOS rank-IC 为负;4 年回测 composite-top200 收益 ≈ 0。→ L2 不做预测,只做"菜单"(多样性采样)。
- **判断层已证的 edge 在「拒绝」,不在「挑选」。** L3 真选无正 alpha 证据(去📌保送污染后 −3.8%);已证有效的是拒绝侧:L4 评级 rank-IC **+0.55** 分档单调、门价值 **+4.35pp**(纸面 NAV 三线),L4 推翻 L3 高确信两次全对。
- **0 买的根因在召回线。** 413 只 T+1 赢家 91% 落在打分池、仅 4.8% 越过 top1000 召回线;composite IC **−0.11**。→ 修法是 regime 分桶权重(见 L1)。
- **0 买不等于失灵。** 历史 0 买日市场 fwd_1 −0.48% / fwd_5 −0.60% → 空仓方向是对的;哪天 0 买日市场却涨,才是失明预警。

---

## L0 · 选集 —— `autoresearch.scan.universe`(确定性)

- **硬门**:剔 ST/退市/停牌/次新;市值地板(默认 30 亿)+ 北交所默认纳入(旋钮全在 `scan_config.jsonc` 的 `l0` 块)。
- **哲学**:只剔"确定不可交易/不可研究"的——**每加一条硬门就是一块永久盲区**(missed_l0 ≈ 赢家 9%,以小盘/次新/北交所为主)。

---

## L1 · 召回 —— `autoresearch.scan.recall`(确定性,→1000)

多路策略并行:每路"过门 → 按信号排序 → 截 top-quota" → `quota_union` 合并(各路 floor 保底多样性),带 provenance。

**已注册 14 路,当前默认启用 10 路**(由 `scan_config.jsonc` 的 `funnel.recall_channels` 决定;⚠️ **该 key 缺省 = 用全部 14 路** —— `config.py:30` 的 `recall_channels: list[str] | None = None` 注得明明白白「None=全注册」,**不是**回落到今天这 10 路。删掉整行会把 4 路默认停用的 `accumulation` / `northbound` / `sector_momentum` / `event` 一并上线,其中 `event` 直接违反其入场纪律 `pr_20260725_001`。真值现取:`uv run --no-sync python -c "import autoresearch.scan.recall.channels as _c; from autoresearch.scan.recall.registry import registered_channels; print(len(registered_channels()))"`):

| 通道 | quota/floor | 信号 |
|---|---|---|
| composite | 400/100 | IC 校准复合分 |
| momentum | 250→188/50 | 趋势龙头(36 日版 quota 下调,unique 超额持续为负) |
| reversal | 200/50 | 困境反转(旧路;与 reversal_confirm 的 A/B 已于 2026-08-19 结束,见下方 reversal_confirm 行) |
| **reversal_confirm** | 150/50 | 反转确认四段:低位 + 企稳(**D−1 截止**缩量、RSI 20~85)+ **放量起爆硬门**(`vol_ratio_20`≥1.5 ∧ 站回 MA20 ∧ MA5>MA10)+ 可交易;无量突破不召回。**2026-08-21 重开**(08-19 曾因恒空 4 周摘出;数据腿 = `frame` 60 日面板 + `common/turnup.py` 十列,门修法与理由见 design 2026-08-21 §2.3/§5.1 —— 旧③代理 `ma_bull` 与①前置低位定义互斥,只接列不改门仍近空)。与旧 `reversal` 同时活体 A/B,裁决见「开放线头」 |
| **lowturn** | 120/40 | **低位转强(2026-08-22 新增)**:门=`common/turnup.lowturn_mask`(与 L3 旗**同一谓词同一阈值**,阈值住 `l3.lowturn`),排序=`reversal_confirm_score`。与 `reversal_confirm` 是「同模块两档」——那路门严(60 日跌≥25% ∧ vol_ratio_20≥1.5 起爆硬门),本路画像门宽。**立案**:2026-08-21 低位转强波把 L3 侧全接好了(旗列/pass1 强留/守卫⑥/硬约束 G),首跑实测全帧 **120** 亮旗 → L1 **17** → L2 **0**,整条特性是「没有生产者的消费者」;昨稿 R5「并入反转桶」的前提同日被证伪(两谓词交集仅 2 只)。**证据边界**:决策尺 −0.24pp(t=−6.36)与现任 healthy 同负,正超额在 5~10 日尺 —— 接生产者是为「打开候选池形状 + 让 L3/L4 有机会判」,不是「隔夜能赚」。design 2026-08-22-funnel-shape-after-lowturn-first-run |
| value | 200→312/50 | 行业内低估(36 日版 quota 上调,胜率 57.6%/+0.9% 全路最优) |
| main_fund | 200→150/50 | 主力净流入 |
| heat | 200→112/50 | 成交额量级(捞巨额龙头;36 日版 quota 下调,unique 超额持续为负) |
| growth | 150→112/40 | 成长加速(36 日版 quota 下调,unique 超额持续为负) |
| healthy | 150→112/40 | 质量上涨(0<pct60<40 且主力净流入>0 且 cmf>0;36 日版 quota 下调,unique 超额持续为负) |
| accumulation | 120/30 | 底部吸筹 —— **默认停用**(unique 超额 −0.21%,原并入 reversal_confirm;reversal_confirm 本身已于 2026-08-19 停用) |
| northbound | 120/30 | 北向持股 —— **默认停用**(hk_ratio T+2 IC −0.108,信息已在 L4 简报行) |
| sector_momentum | 150/**0** | 板块动量上涨侧(同行业 `pct_60d` 中位 >0 硬门,按板块排序、板块内 composite 决胜;**绝不用当日涨幅**——追当日大涨实证为负价值)—— **默认停用**(EXP-2 `exp_20260801_recall_sector_momentum` 的 challenger 数据腿,`floor=0` 影子专用;唯一消费者随 2026-08-21 闭环退役删除,现在无尺可裁) |
| **event** | 80/20 | 公告事件(回购/增持按公告去重、调研只作有无;信号来自 `scan/events.py`,排序键 `ev_hard`+composite 决胜,**不用当日涨幅**——追当日大涨实证为负价值)。**默认停用**(2026-08-21 起取证渠道也没了:影子变体与 `channel_audit` 随闭环删除);L2「事件」桶 floor **=0**(未启用通道不得改生产 L2 分布) |

- **配额覆盖**(36 日版,2026-08-19 拍板,取代 07-11 的 18 日版):`funnel.channel_quotas` 现生效 value 312 / momentum 188 / heat 112 / healthy 112 / growth 112 / main_fund 150;兜底读取在 `universe.run` 本体(`_funnel_overlay`,prelude 与 CLI 直调同源),显式参数/CLI flag 恒优先,缺文件=注册表默认。六键全写(逐一核对目标值均不等于各路 `@channel` 注册表默认,任一键缺省会回落注册表默认而非维持原覆盖值)。
- **rz 因子组**:融资买入强度 `rz_buy_intensity` 独立第 10 因子组,语义=情绪接力资金代理,非基本面确认。

**regime-aware(默认开,`funnel.regime_aware`)**:按当日 regime 取 `weights.json` 的 `regimes[trend|range|risk_off]` 权重块,缺块回退 flat。regime 判定(`common/regime.py`):breadth≥0.55 且 pct_60d>0 → trend;breadth≤0.30 且 pct_60d<0 → risk_off;其余 range。当前面板(107 成型日):trend 43 / range 53 / risk_off 11;momentum IC 在 trend −0.055、range +0.015。

**已知局限**:

- risk_off 样本薄(11 日);horizon 之争未决(`pr_20260702_001`)。
- ~~影子漏斗~~:5 变体(`nostrat`/`nocap`/`pre_healthy`/`plus_event`/`capfloor20`)与 `--no-shadow` 开关**已于 2026-08-21 整段删除** —— 它们存在的唯一理由是喂 retro 对照与 `channel_audit --variant` 的 `unique_excess_t2`,两个消费者都随闭环没了;其中 `capfloor20` 还是唯一重取数变体,留着 = 每跑一次白付一次全市场取数换一堆没人读的 CSV。
- ⚠️ `pre_healthy` 语义在 **2026-07-25 有定义断层**(基准从"全注册路"改"当日实际启用路"):跨该日读 `L2_pre_healthy.csv` 趋势线不能直接连线。
- ⚠️ momentum 勘误:早期「unique +0.75%」已过期且符号翻负(26 日 −1.07%);相位条件 quota 属 B 类须 registry,quota 维持不动(`docs/research/2026-08-04-momentum-phase-conditional-ic.md`)。

---

## L2 · 粗排 —— `recall/l2_stratify.select_l2`(确定性分层采样,→200)

**不用机器学习**:① sector-neutral composite 排 merit;② 8 风格桶固定 floor(趋势20/健康15/反转12/价值12/成长12/吸筹12/主力10/**低位转强8**,明细=行为归属留在 `l2_stratify.DEFAULT_FLOORS`);**未启用通道的桶 floor 运行时归零**(`effective_floors`,2026-08-22)——此前靠「记得手工把该桶 floor 写 0」维持,是指令级约束;现在按当日启用集在运行时归零,于是新通道的**回滚杆只剩一根**:从 `recall_channels` 摘掉它,桶随之消失、逐字 parity;③ 任一 `industry` 标签 ≤20%(`l2.sector_cap`;⚠️ **不是申万一级**,见下条)。产物 `L2_gbdt_top200.csv`:`l2_rank`=选择序、`gbdt_score`=composite、`l2_lane_reserved`=被 floor 救回。
- **⚠️ 「行业」= 东财所处行业,不是申万一级**(上面 ① 的 sector-neutral 去均值与 ③ 的 `sector_cap` 用的是**同一列** `industry`):它来自 akshare `stock_yjbb_em` 的「所处行业」(`data/tushare_source.py:154`;`common/sw_sector_map.py:4-5` 记着同一件事:「不是规整的申万一级」),粒度细到「证券Ⅱ / 半导体 / 消费电子 / 工业金属」这一级。**08-26 实测**:全帧 **129** 个标签、L1 top1000 里 120 个、L2 200 只里 67 个。
  **后果 1:20% 的 `sector_cap` 在这种粒度下几乎从不触发** —— 08-26 L2 最大单行业 13/201 = **6.5%**,离 20% 差三倍,那天**剔 0 行**。别把它当成「已有行业分散保护」来读:真正在拦同板块扎堆的是 **L3 守卫⑧的 3 席帽**(`l3/merge.py:557` 把同一个 `industry` 直接赋成 `sector`,所以 08-21 贵金属 4 席拦得住,而 L2 的 20% 帽对同一天完全没动作)。
  **后果 2**:① 的 sector-neutral 去均值是在这种细标签的小组内做的,组内样本一少,去掉的更多是标签噪声而非行业 beta。
  要让 L2 的帽真起作用,得先把标签收缩到申万一级那种粗度(`common/sw_sector_map.py` 的 ~7 大类是现成的中间层)。**本条只是把口径说对,不是改判据** —— `l2.sector_cap` 的值与守卫⑧的 3 席都不动。

**菜单体检**(`scan/menu.py`):行业集中度/落刀面/健康上涨/估值四项,自动嵌 L5;健康上涨=0 打 ⚠️菜单病。

**哨兵建议**(`menu.sentinel_advice`,按全市场健康占比):<3% 建议哨兵档(跳 L3+L4 省 ~70% token);3–5% 仅 consider;≥5% 全扫。**由人拍板不自动**。

---

## 旁路 · S1 情绪温度计(展示先行)

- tushare `limit_list_d` 入湖(勿用 akshare 涨停池);五序列(涨停/跌停家数、连板高度、晋级率、炸板率、昨涨停今溢价)→ `score` 0-100 + 五相位(冰点<20/修复/发酵/高潮≥65/退潮,±3 滞回),幂等增量 `$CTX/learning/temperature.csv`。
- 消费:market_pack `temperature` 块(presence-gated)+ L5 🌡 行 + prelude `temperature` 步;分段校准 `python -m autoresearch.scan.temperature_calib`。
- **边界**:不接菜单/预算联动(相位判定质量复审后再议);涨停数据只进温度计,不做打板/隔日溢价信号(负结果)。

## 旁路 · 市场研判 = macro-research lite 档(Opus×1)

Stage 0 与 L0 并行,回退到 L2 之后落盘。模板在 `macro-playbook.md` 末节。

- **机制**:确定性 `market_pack(scan_dir)`(regime/宽度/估值分散/资金/红黑榜,只读 `L1_scored_full`)→ `macro-brief` agent 写六小节 `market_view.md`。三处复用:L3 地形段、L4 `market_context_block`、L5 置顶。
- **防锚定铁律**:喂 L3/L4 的只能是**描述性地形**,不能是方向指令;操作建议只进 L5;**个股评级只由本股 rubric 三门决定**。缺文件 → L5 回退确定性脉搏。
- **配置装载链**:见 SKILL.md「配置单一事实源」节。`agents` 只声明 role→tier，`agent_engines` 分别解释 Claude 的 model/effort 与 Codex 的 model/reasoning_effort；`_resolved_agent_config.json` 分开记录 declared、runtime capability、resolved，`usage_reconcile` 再与 actual 对账。Claude profile 与迁移前档位等价；Codex 当前核心判断档为 gpt-5.6-sol/xhigh，能力不支持时只按已声明 fallback 降级并留 mismatch。

---

## 旁路 · 行业 brief = sector-research lite 档

L2 之后、与 L3 证据取数**并发**:

- `sector.reuse <date> --apply`(TTL ≤5 日 ♻️ 复用;已复用行业从 fan-out 排除)→ 剩余 `sector.pack <date>`(红榜 top3 ∪ L2 集中度 top3 ∪ 存量 watchlist.csv 行业,K≤6)→ 每行业一个 `sector-brief` agent 写**单段**契约 brief:`## 地形段`(喂 L3/L4)——`## 研判段` 与 `**行业方向**` keyed 行已于 2026-08-19 D6 整段砍除(用户裁定),`sector/brief.py` 只认 `TERRAIN_HDR`,行业方向叙事改走确定性 top3 (`market.sector_healthy_top3`)。L4 派发前对 ≥2 只同行业 finalist 的行业补漏。
- **只有这一条路**:原 `performance.sector_brief_mode` A/B 开关已退役——`finalist_only` 会让 L3 看不到判断型行业 brief、可能改变 finalists,按「性能开关不拥有评级」铁律它不是性能开关。
- **消费与价值**:`l3_table_md(sector_terrain=True)` 只渲染 L2 top200 覆盖行业(~110 行压 30–50);assemble 自动嵌 🏭 行业研判 + 🔗 同链对比(presence-gated)。价值 = 同链论点摊销 + 行业相对估值锚,**不解决 0 买也不设门**。

---

## L3 · 精排 —— pass1 确定性分诊 + holistic 单 Opus 深比较(200 → ~40 → finalist tier 7–10)

**📌 保送票也走 L3**:pass1「pinned 全入」保证进表 → l3-rank 照常独立判(写 thesis/风险/催化/conviction,`finalist:false` 不占名额)→ `_inject_pinned_finalists` 把这份判断整段带进 finalists.csv(lookup 优先级 `fin`→`judged`→L2→占位)。**保送 ≠ 免判,更 ≠ 判了不要**(漏带会让 L4 拿不到持仓票的 L3 前提清单)。

**两遍法**(`l3.two_pass` 默认开):

1. `harvest_l3_evidence`(龙虎榜/预告/快报)+ `harvest_l3_news`(公告情感)补证据;
2. **pass1 分诊**(`triage_l2_for_l3`,零 LLM):pinned 全入 + 多路共振 top-5(`RESONANCE_CAP`,2026-08-22)+ lowturn 强留 ≤8 + 各通道 top-K 轮询,~200 行收到 `pass1_target`(现 40);**healthy lane 自 2026-08-22 不再全入**(`HEALTHY_MANDATORY=False`,证据=edge 普查三尺全负;08-21 它一项占 14/40 席),与其他 lane 同等轮询;被切的落影子 `_l3_pass1_cut.csv`(不代表判死);
3. `l3_table_md` 压紧凑表(表头注明「pass1 分诊 n→n」);**2026-08-22 加两列** `pct_1d`(当日涨幅)与 `dist_high_60`(距 60 日高,≤0)+ pf 词「今日大涨」(≥9.5)/「贴顶」(dist_high_60≥−2 ∧ pct_60d>0)—— 此前 L3 看不见当日涨幅却被要求替 L4 避开「涨停追高」,2026-08-21 两只入围票双双在 L4 早停该因;
4. 一个 Opus(`l3-rank`,max)通看 ~40 只,按 6 维 rubric(channel 共振/资金/基本面/情感/脆弱/T+2 兑现机制)**比较着选**(比较式 > 逐只打分),给出 **finalist tier 7–10 只**(`finalist:true`,宁缺毋滥不凑数)+ 其余 **bench**(`finalist:false`,落 `_l3_bench.csv`,防漏影子);
5. `L3_judged_full.csv`(全量判断)→ `merge_l3_finalists_v3` 确定性守卫,**按序** ①`ins75`(conviction≥75 未标 finalist 强制补入,误杀保险)→ ②`lt55`(<55 剔除)→ ③`cap`(=min(`finalist_max`,当日 l4_budget) 按 conviction 截尾)→ **⑦`chase_1d`**(当日 `pct_1d`≥9.5 剔除 + 从 bench 回填 `chase_backfill`,conviction≥55 才够格、不硬凑;2026-08-22)→ ④`healthy_quota`(**2026-08-22 起 `HEALTHY_QUOTA_FRAC=0` 不动作**;回滚改 1/3 即恢复「健康画像不足 ceil(n/3) 从 bench 补」,⑤⑥⑧ 的保护集随同一常量联动)→ ⑤`trend_quota`(soft 2 席)→ ⑥`lowturn_quota`(soft 1 席,qualify 55)→ **⑧`sector_cap`**(同 `sector` >3 席则剔最弱 + 回填异行业 `sector_backfill`;2026-08-22)→ **⑨`composite_seat`**(2026-08-26 §3 路A:当日 L2 菜单 composite 最高的 M=3 只**强制进 finalists**,`guard=composite_seat`/`lane=composite`,**与 📌 同级** —— 不占名额、不受 ②lt55/③cap 约束;剔 📌/ST/`pct_1d≥9.5`;pass1 有配套 ①b 强留,让 l3-rank 真判到它们)。缺 `finalist` 字段(旧 judged)→ 按 conviction 排序取 cap 同守卫;各守卫的**列缺 → 整段 no-op**(parity);📌 保送在全部守卫**之后**由 `_inject_pinned_finalists` 注入,不受⑦⑧影响(持仓涨停/同行业照样出卡);
6. 注入:策略师地形段。(**因子方向经验校准块与 T+1 快环校准块已于 2026-08-21 随闭环退役** —— 那是「把历史账本学到的东西塞回今天的判断 prompt」的回注腿。)

**judged 输出契约**:每元素含 `mechanism`(两日内兑现机制+明日买家,写不出不选)与行为化 conviction(**≥70 = 能说出 D+1 谁买且愿真金买入,每日 ≥70 限 ~5 只**;50-69 = 值得 L4 验不背书)。

**token 经济与预算**:`delta=True` 略去无变化票;L4 派发数由 `menu.l4_budget` 控(五面旗:落刀>60% / 相对落刀>40% 且>2×全市场 / 健康涨≤2 / risk_off / 0买连败≥3;1 旗→22、≥2 旗→15);`l3cap = min(10, l4_budget)` 由 workflow 传作 `--budget`。

**三面旗**(presence-gated,缺=parity):主力失真 `dist_flag`(反号/微量)、监管 `reg_flag`(近 10 日立案/问询/处罚)、误读三预警 `misread_flag`(低基:np_yoy>100 且 roe<8;背离:cmf/obv 正但 main_net_ratio<0;套牢:winner_rate<25 且 ma_bull=0 且 pct_60d>0;L4 简报同步注旗,l3-rank 硬约束 E 强制自证)。

**误读三闸**(治误读自见数据):行语义指纹 `pf` 列(确定性画像短语,LLM 读词不读裸浮点);表按 lane 分块渲染(块内 composite 降序);`l3_select lint <date>` thesis 数字机检 → `repair-pack` 只写失败行 → agent 产 `{code, thesis}` patch → `apply-repair` 同一 lint 谓词复验后原子 merge。

**稳定性与验尸**:周频 `shuffle_seed` 乱序重跑 audit,overlap<0.70 提 proposal;错杀验尸(L2-keep 且非 finalist 且 T+5 赢家)实证错杀=0——**病在召回线,别冤枉判断层**。

**L3.5 已完全移除**(用户裁定"完全移除、直接 L3 输出"):链路 = L3 → GATE2(只读校验:6 位码 + exempt lane 记账)→ L4 派发,中间无收窄层。回测结论「只有 conviction≥70 有 T+2 edge」已内化为上面的行为化定义;将来复验"收窄闸"类假设需要重新造一把尺(原**漏斗回放器** `research/replay` 已随 2026-08-21 闭环退役删除 —— 它的归因腿读的是 `retro.attribute`),但不复活 L3.5。

---

## L4 · 研究 —— 一只票 = 一个 Opus subagent(渐进深度 + 早停)

### 派发前确定性闸(按序,生产者都跑在 prompts 之前)

- ⓪ **批量质押旗**:`l4_card pledge <date>` → `pledge.csv`(质押>40 爆雷、>20 偏高;advisory 不动门)。
- ① **复用层:无**。TTL 复用已退役(用户裁定「不要任何复用」;复用票不跑 intel 会新闻冻结,评级稳定性由昨卡回声承接)。**菜单滞回(carryover)已退役,勿再加回**:token 会计坐实"保席从不省 token,只会 0 成本或 +1 Opus"(carryover 票按定义是今日 L3 没选的票),且它系统性推翻 L3 的拒绝——拒绝恰是本机器唯一被证明有效的功能。
- ② **生产者落稿**:席位/催化/日历/卖方修正(consensus)——都先于 prompts。

### 派发三步

1. 落 `_l4_shared_instructions.md` → `l4_card prompts <date>` 落 `_harvest_list.txt` + `_l4_prompt_<code>.md`。只有 legacy 字节路径(`stable_context_blocks` 共享块置前方案已退役:预估节省 4% < 10% 门,不值得双路维护)。
2. 默认 `streaming_l4=true`:初始化 `_l4_tasks.json`(状态 `PENDING/RUNNING/SUCCEEDED/FAILED/BLOCKED`,逐票记 prompt/slim/card hash、attempt、pinned、错误类、时间戳)。**init 前置 prompts 落稿并做硬门校验**(缺 → `ok:false` 整线停在派发前)。四类并发帽 `tushare/web_search/web_fetch/l4_stock` 取显式最小值形成 `dispatch_batches`;发生 `RATE_LIMIT` 后下一批降宽一档(最低 1),只改调度不改查询 cap 或评级。
3. 每股 `l4-stock` 先 preflight;本票 slim 与 intel 并行,二者终态后出卡。成功仅在 prompt/slim/card hash 全验证时复用;只有 `RATE_LIMIT`/`CONNECTION`/`TIMEOUT` 允许第 2 次尝试,schema/contract/data-integrity 失败不重试且不碰其它票。`streaming_l4=false` 回滚旧批量 `harvest-slim` GATE3。

单票恢复入口:`python -m autoresearch.scan.l4_tasks batches <date>` 只返回未完成批次,按原 args 重派对应 `l4-stock`。**不要删 `_l4_tasks.json`**,否则丢失已验证成功的跳过事实。

**⛔ 强制满卡**(`force_full_card`):逐卡块内插「禁止早停」,两条独立通路任一成立即触发——① 📌 保送票(`lane=="pinned"`)恒强制:持仓票的「盈利质量」「偿付(爆雷)」不允许标『未核』;② 强先验:`conviction≥70` ∧(`n_channels≥4` ∨ L2 配额救回)。**只保证核得够深,不保证结论向好**(照样可 UW/Sell),评级仍由 rubric 三门定。

**pinned 透传铁律**:派发前对照 workflow 打印的「📌 保送票 N 只」行逐一核对每只都带 `pinned:true`——漏传使持仓 SELL 双复核整段不跑(SKILL.md 步骤 4 同一条纪律)。

**活体情报**(`l4_intel.enabled` 已开):`l4-intel`(sonnet·max 盲搜六面)与 slim 预取并行,落 `_l4_intel_<code>.md`,卡 P3 先读。**铁律:卡片对 intel 的价格类断言必须与 verified OHLCV 对账后才可采信**(情报站曾捏造涨停断言)。

### 渐进深度 + 早停

```
P0 简报（市场地形+档案+解禁/披露旗+行业备忘+误读预警）
  → P1–P3 表面填 4 维
  → 【主早停②】非买点 → 早停卡（短格式 ≤36 行;未核维标「未核」）
  → survivor 读 deep 进 P4 陷阱核（质押/商誉/解禁/审计/现金流,记「进入P4倾向」）
  → ③ 击杀
  → P5 满卡
```

评级由 `rubric_rating` 派生;早停只向下;≥OW 必走完 P4+P5。

- **防污染**:简报的 L3 论点是**中性前提清单**(前提逐条核真,前提 2=兑现机制),conviction 在"L3 元数据"行注明"读完 P1 数字后再看";l4-card 铁律「先读数据后读论点」(P1 盲读微 pass:先写 3 行独立初判)。
- ~~补基率~~:🔁 基率行与 📐 目标价锚已于 2026-08-21 随闭环退役(两者都由 `buy_ledger`/`cross_calib` 派生)。同批删的还有 📚 跨票判例块与行业备忘录回退行;`_l4_shared_instructions.md` 退成只有标头的稳定骨架(消费侧按「文件在就读」接线,留空骨架比让 prompt 前缀随文件有无而变更安全)。
- **买单 ensemble**(替代常设 skeptic):≥OW 新派卡各追加 2 独立 l4-card run(复核卡落 `ensemble/`),取中位、**只向下折回**;spread≥2 档 → 🎭 badge + 组合视角人裁行;`_ensemble.json` 缺 = parity。
- **阶段效能**:早停率随 regime 波动大(20%~100%),弱市高早停是纪律不是失灵;错杀率 ≈10% 与满卡组持平。纪律实证:紫光国微三度被 CFO/FCF 门封顶 Hold——**别为凑买单放宽资金/估值门**。

---

## L5 · 整合 —— `scan/assemble.py` 兼容 CLI(确定性,零 LLM 铁律)

实现归属为单向链:`l4/parsers → decision_finalize → report_sections → publisher → post_run`。`assemble.py` 只兼容导出与 CLI;L3/L4 的机制件由各自 `scan/l3/*`、`scan/l4/*` 持有,仓内消费者必须直连 owner,禁止从兼容 adapter 反向 import;Workflow 只调度,不持有 rating/gate 规则。

**报告双层 = 一个发布包**(2026-08-29 B+ 重构,design `docs/specs/2026-08-28-summary-slimdown-design.md`):
`summary.md` **决策层**(11 节,典型 7–10KB)+ `appendix.md` **现场层**(A–G 七节)。
两份由**一次** `report_sections.prepare_report_model()` 冻结出的 `ReportModel` 纯渲染而来
(`render_summary` / `report_appendix.render_appendix` 都是纯函数:不读盘、不写盘、不 fold 评级)。
真身 = `report_sections.py` 模块头的节序 + 事实归属注释块与 `report_model.py`,改序先改那里。

```
summary.md(决策层)
  自检 banner(**聚合版**:同 key 折成 `×n`;明细 → appendix A)
  → # A股扫描 · <数据日>(run <id>) + 身份行(不放结论)
  → 🧭 决策仪表盘(managed,brief ①②③④ 逐字)
  → ## 行动(overlay 仓位 managed · 组合集中度 managed · 🔗 同链一行 · 复核分歧 · 哨兵 banner)
  → ## 候选(N 只)  # | 名称 | 板块 | 评级 | 目标(EV) | 一句依据 | L1→L2
  → ## 📌 保送持仓(同表 + 保送理由列)
  → ## 为什么没有 BUY / ## BUY 资格与约束(**单源** decision_records:早停分桶 + 三门 ✗)
  → ## 市场地形(策略师 **2/3/5 小节切片**;解析失败只留一行链接,不整段回退)
  → ## 行业 top3 → ## 📅 未来 14 天(含 D-2 隔夜窗海外事件)
  → ## 运行事实(managed 紧凑一行)→ ## 诚实局限(锚字面勿动)

appendix.md(现场层;标题恒在,缺席印 `无 / NOT_EXPECTED`)
  A 自检明细全文 · B 漏斗现场(数量/降级/卡点/菜单/0买机制)· C 研究全文(策略师/L3 逐票/Tier-3)
  · D 门柱与资格(自由文本口径直方图 + 两口径说明)· E 运行观测(耗时全表 + managed detail 块)
  · F 方法与口径(语义锚 `#method-*`)· G 诚实局限全文
```

**事实归属**(每个事实在 summary 里只有**一个**展开点;这是本次重构的核心不变量):
regime/温度/定调 → 仪表盘①;BUY/BLOCKED 结论 → 仪表盘③;持仓总动作 → 仪表盘④;
仓位与动作 → 行动节;单票 → 候选/保送表;无 BUY 的**统计** → 节 6;漏斗现场与门柱自由文本口径 → appendix。
**字节预算**(展示层 warn,不截断、不改评级、不毙 GATE4):summary 目标 12KB / warn 16KB,
appendix 目标 20KB / warn 24KB。08-26 真 staging 离线重渲实测 **27,008B → 7,400B**。

- **入口是 `brief.md` 不是本表**:CP7 转播 brief 全文;要展开才按节序进 `summary.md`。
- **GATE4 severity 口径**:判据/fail-warn 二分见 SKILL.md 步骤 5「GATE4 拦什么」;单一事实源 = `scan.self_review.BRIEF_LINT_SEVERITY`,勿在别处另写一份。
- **现场完备**:发布同时写 `run_health.json` + `index.md` 导航页(**第二天回看从 index.md 进**);`weights_used.json` + meta.regime 固化,漏斗可复现。
- **计量时序**:assemble 时 `_token_usage.json` 通常尚未生成,报告先写 `UNMEASURED`;CP7 跑 usage_harvest `--json-out` 后由 `post_run observe` 原位替换 managed section,并刷新 `_budget_observation.json`、budget StageResult 与 ArtifactIndex。
- **观察单已退役**(用户裁定):日检/触发/直通车全无;存量 `$CTX/watchlist.csv` 保留(sector.pack 行业选择器仍直接读)。发布落 `$RPT/scan/<数据日>-<发布MMDD_HHMM>/`(2026-08-28 用户裁定;真身 `scan/run_naming.py`。旧 `<跑动日>_<HHMM>` 只读兼容——那个格式里目录名首段是跑动日,`20260826_2000` 研究的其实是 08-25,61 个 run 里 19 个数据日≠跑动日)。
- **法证 run capsule**(`autoresearch/trace/`,2026-08-28,设计稿 `docs/superpowers/specs/2026-08-27-scan-forensic-run-capsule-design.md`):
  run 从 `capsule begin` 起就拥有独立工作区 `$CTX/scan_runs/<run_id>/`(`state.json` 带**租约**:hostname/pid/进程启动时刻/心跳),
  每条确定性命令经 `trace.exec_capture` 捕获 argv/stdout/stderr/信号,每个 agent 边界经 `capsule agent-event` 进
  **hash 链** `events/events.jsonl`,每次 lake 读取经 `trace.source_lineage` 落 `lineage/reads.jsonl` + 内容寻址 blob。
  CP7 的 `post_run observe` 末尾调 `capsule finalize`,按固定次序:transcript 物化 → 真计量 → 产物快照 →
  expected/replay/completeness → `capsule.json` → MANIFEST → root → 脱钩 `ROOT.json` → `.tar.zst` 归档 →
  `_ledger/run_capsules.jsonl` 追加一条 revision → 全树只读 → 复验。
  **三个结论互不替代**:`integrity_ok`(已归档文件被改没)/ `completeness_ok`(该有的证据齐不齐)/
  `replayability`(冻结输入能否重放出同样字节)。**`MANIFEST` 通过 ≠ 完整** —— 它列不到没人写下的文件,
  `20260826_2000` 正是这样带着 0 transcript / 557 未归档 staging / `$0.0000` 假成本显示「完整性 ✓」的。
  失败与中断也冻结:`$RPT/scan/_failed/<run_id>/`,带 `failure.json`(含最后一个可靠检查点)。
  事后找回的证据走**叠加层** `_repairs/<run_id>/revision-N/`,base root 永不变动。
  Codex transcript **只认显式绑定**(`capsule bind-transcript`),候选 0 个写 `GONE`、多个写 `AMBIGUOUS`,
  **禁止按最新 mtime 猜**;计量走 `usage_harvest --engine codex --run-id`,量不到写 `UNMEASURED`(不是 `$0`)。
  **2026-09-12 起(scene-reconstruction Task 4)这条绑定真正接进了生产**(此前 `capsule bind-transcript`
  只有 `analyze/runctl.py` 一个调用者,scan 路零调用,每条 invocation 恒读 `GONE`):`post_run
  publish_run_observation` 在决策校验(相对 BUY 现算/比对)完成之后、`retain` 镜像 staging 之前、capsule
  冻结之前,调 `autoresearch.scan.transcript_binder.safe_bind_run`——按 `agent_expectations` 算出的每个
  已知期望(AGENT 事件/TASK 事件/产物回退三路合并)配真实候选,写五态之一
  (`BOUND`/`UNVERIFIED_BY_PRODUCT`/`AMBIGUOUS`/`GONE`/`ERROR`)进 `_transcript_bindings.json`
  (staging,`retention.bind_transcripts` 开关,默认 true,见 SKILL.md 配置表)。**逐条绑定失败都有
  账**——单条冲突(同一 invocation 已绑定过一份不同来源)或源不可读,只把那一行标 `ERROR` 并留原因,
  不阻断其余票、不阻断业务发布;整场故障(期望集合读不动、报告写不进去)走既有证据降级通道 +
  stderr,报告仍标 `enabled:true, status:"ERROR"`,不会安静地报成"证据完整"。开关关闭或没有 active
  run 时同样写一份 `enabled:false` + 原因的报告,不清除任何已有证据。**一条 `BOUND` 不是研究完整的
  证明**:它只说明这段证据载体被找到并归档了,分母可能是产物推导的下界(`denominator_quality=
  lower_bound`),区段可能只是 `partial`/`unknown`;完整性结论仍以 capsule 自己的
  `completeness_ok`/`materialize_agent_index` 为准,不能拿这份报告的 `enabled`/`BOUND` 直接当"证据
  完好"或"研究做完整了"。CLI:`python -m autoresearch.scan.transcript_binder --run-id <contract_run_id>`
  (staging 取自 run handle 自身,不在日期目录里猜最后一份)。
- **现场留存**(`scan/retention.py`,2026-08-26,**已降为兼容路径**:capsule 在场时它的 MANIFEST 绿灯只代表完好性,不再是完整性结论):发布收尾 + `post_run observe` 各跑一次 `retain()` → `trace/staging/`(**整目录镜像** staging,含子目录)+ `trace/inputs/{slim,sector_packs,prompts,temperature_row}`(staging 之外的输入:逐票 slim/深核、行业 pack、agent def/playbook/config 本体、当日温度计行)+ `trace/transcripts/*.jsonl.gz`(判断腿 subagent 推理链,≈1.3MB/run)+ `trace/lake_manifest.json`(窗口内湖指纹,~1s)+ `trace/MANIFEST.sha256`。**判据:run 目录自足到 staging 可弃**(staging 按数据日键,同日重跑原地覆盖 —— 实测 64 个已发布 run 只剩 49 个 staging)。**必须是发布的最后一步**:早一行就镜像到半成品。核验 `python -m autoresearch.scan.retention verify <run_dir>`;链路复盘 `python -m autoresearch.scan.chain_view <run_id> <code>`。
- **run_contract v2**:加 `git_dirty`/`dirty_paths`/`prompt_hashes` —— `git_sha` 只说 HEAD 在哪,而 **agent def 未提交也会生效**(会话启动装载工作树那份)。v1 契约仍可读(`_hash_payload` 按 `schema_version` 排除 v2 三键,历史 run 身份不丢)。
- **结果账本**(`scan/outcome.py`,**只记不学**):prelude 的 `outcome_fill` 步逐日回填已发布 run 的推荐票事后读数 → `$RPT/scan/_ledger/outcome/<run_id>.json` + `_ledger/recommendations.csv`。口径与 `research.edge_census` 逐字同源(同一 `forward_returns`/`entry_tradable`/`GAP_CLIP`),两边可直接对表。**必读两列**:`mode`(shadow 期的 BUY 明写「不执行」)与 `src`(`shared` = 读自共享 staging,未必是本 run 那份)。消费者只有 `chain_view` ⑩ 段与汇总屏一行;**不进 brief、不喂任何 agent、不改任何参数**。落 `_ledger/` 而非 run 目录内,是因为 run 目录刚立了「发布后不再变」的 MANIFEST 不变量。
- **时间锚**(`scan/exec_anchor.py`,2026-08-28 §2.4 G1):manifest 新增 `execution` 块(`decision_approved_at`/`first_available_session`/`exec_lag`/`actionability_status`),账本新增同名四列。**读 BUY 战绩前先看 `actionability`**:报告在 T+1 收盘之后才就绪的 run(实测 8/61),主尺买腿是**已经过去的价格**——它们不进 `ledger_line` 的均值,单列「迟到 n 笔不计」;反事实收益走独立列 `exec_gap_c1_o2`(同一把尺、买腿改到第一个真正来得及的尾盘),**两列绝不混算**。运营截止 14:45(不是交易所的 14:57——人读完还要下单)。历史 manifest 一个字不改(run 发布后不再变是 MANIFEST/ROOT 的不变量),老 run 由 `read_execution` 按 `generated_at` 估算并标 `ready_quality=estimated`。

---

## 计量与跨层校准(usage_harvest = 唯一正典)

- **token/成本真计量**(Codex 引擎走 `--engine codex --run-id "$RUN_ID"`,读 capsule 里的显式 transcript 绑定;量不到写 `UNMEASURED`,**绝不渲染成 `$0.0000`**):`python -m autoresearch.trace.usage_harvest --session <sessionId> --out $RPT/scan/<run>/token_usage.md --json-out $CTX/scan/<date>/_token_usage.json`。覆盖可定位的主会话+subagent;按 `message.id` 去重,分 input/output/cache read/write/model/effort/失败/重试,按公开计价倍率加权估算。补账 `--transcripts <glob>`。
  - **回填**:`python -m autoresearch.scan.post_run <date> observe --report-dir $RPT/scan/<run>`;缺成本/墙钟写未计量 warning,**绝不写 `$0`**。
  - **预算只告警**:超 cache 红线/阶段成本/墙钟只产 `DEGRADED` StageResult,`truncated=false`;不能截候选、查询、卡或阶段。
  - **成熟门**:至少 **10 次真实扫描**且基线已定价、成本/墙钟/cache 齐全,才报中位成本与 P50/P90 并判 PASS/FAIL;此前恒 `IMMATURE`。效率分母(USD/成熟 DecisionRecord、USD/最终 BUY、USD/已验证正确拒绝)分母 0 显示 `—`,不制造 BUY。
  - ⚠️ 旧「落盘字节÷2.8」估算曾低估 30 倍且分布相反——**按估算砍成本会砍错地方**,估算列已退役;信 usage_harvest。
- **配置生效对账**(`usage_reconcile`,scan 步骤 5 第四条命令):当日 `user_config_echo.json`(期望)× `_token_usage.json`(实测)逐 role 对账,`ok=false` 直接进 CP7 播报 stdout——**这就是当日结论**,不经 `self_review` 转手(`self_review` 同名 check 时序落后一轮,只能读上一次结果)。三条精度边界(时序滞后 / `general-purpose` 壳只做集合断言 / effort 是请求参数非实测)写在模块 docstring 与报表头,播报时勿脑补掉。exit 恒 0。
- **OTEL 遥测已删除**:勿再配那五件 env,照旧文档跑直接 ModuleNotFoundError。
- **账本列名断层(读历史产物必知)**:落盘列名 `*_t2`/`ex2`/`fwd_2` 等沿自旧主尺 `fwd_2_oc` 年代,**值随 `common.ruler.MAIN_RULER`(现 `gap_c1_o2`)现算**。逐日文件(`retro/attribution.csv` 等)可能在扫描日之后被重算刷新——**分界不是扫描日期,是该文件最近一次被(重)算的时刻**;读侧认每份文件自带的 `ruler` 列,缺列按 `res.get("ruler","fwd_2_oc")` 兜底(诚实标旧尺)。渲染层已加当前主尺提示;**历史产物不回改,展示层现算**。
- **注入分层铁律**:python 只产读数;prelude 打三条当日件建议行(📐/🔁/🚪),n<10 的 thin 行标「禁注」勿贴。**校准不改门/权重/评级。**

---

## 行为变更的入口(原「实验治理」)

D1(2026-08-19,用户裁决 A3)删掉了预注册状态机(`experiment_registry`/`promotion`/`rollback_watch`/`mainflow5d`);**D2(2026-08-21,用户裁定「整个 learning 层退役」)删掉了它剩下的全部证据来源** —— `autoresearch/learning/` 整包、`scan-retro` 与 `feedback` 两个 skill、以及扫描路径上所有账本记账与回注腿。

现在没有"治理链条"这回事了。涉及召回、L3、门、早停、评级、Token 或速度的改动 = **普通开发改动**:人判断 → 改 `scan_config.jsonc` 或代码 → 测试锁 → 合入。没有自动学习、没有影子账本呈证、没有 proposal 裁决通道。

**保留下来的三件门/尺**(它们从来不是"学习",只是历史上住在 `learning/` 里):

| 现址 | 干什么 | 为什么留 |
|---|---|---|
| `scan/self_review.py` | 发布前机械自检 + `brief_lint` + 写 `gate_fires.csv` | **GATE4 的判据真身** —— 检的是「报告有没有说假话」,与"从历史里学到什么"无关 |
| `scan/tripwire_watch.py` | 决策卡价格线 vs 今日收盘的冲突检测 | 确定性风控尺,进 brief ⑤ 风险哨与 prelude ⚡ 建议行(**仅人看,勿贴给 agent**) |
| `scan/temperature_calib.py` 内联的 `trade_days`/`market_nav` | 湖交易日历 + 全市场等权 NAV | 纯读湖工具,原与影子 NAV 同居 `paper_nav` |

**同批连带退役**(不是遗漏 —— 判据只有一条:**输入没人生产了,就不留**):

| 类别 | 删了什么 |
|---|---|
| 门 | GATE0 preflight(`scan/gate0.py`;唯一输入是 retro/t1 欠账,闭环一走恒 PASS) |
| 报告节 | `scan/near_miss.py`(「差一点/弃权」,三个数据源全是学习账本)、brief ⑦ 欠账节 + 「旧 OW 基率」分账行 + ① 的**两尺分歧**、summary 的经验/未决反馈节 + 影子 NAV 行 |
| prompt 注入 | L3 表尾两个校准块、L4 的 🔁 基率 / 📐 目标价锚 / 📚 判例 / 行业备忘录 |
| 离线研究仪器 | `research/replay`(漏斗回放器)、`research/ruler_compare`(两尺对照)、`research/channel_audit`(通道整编)、`research/overnight_evidence`、`research/candidates`(立项账本)、`research/l2_grid`、`scan/l2_slo`(winner-capture SLO)、`lowturn_precheck --live` 腿 —— **全部以 `retro/attribution.csv` 为输入** |
| L1 | 影子漏斗 5 变体 + `write_shadow_variants` + `--no-shadow`(唯一消费者是 retro 对照与 channel_audit) |
| 档案 | `dossier/ledger.py`(t1 快环战绩 + retro 归因桶),§7 只剩确定性入围史 |
| 体检/清单 | `run_health` 的 `ledger_freshness`/`retro` 两键、`artifacts` 的 5 个 retro/shadow 产物条目、成本效率的 `mature_decision_records`/`verified_correct_rejections` 两个分母、`self_review.dump_ow_gate_fires` |
| 开关/入口 | `prewarm --with-calibrate`、`relative_buy preflight` verb、`scan_config.jsonc` 的 `learning` 块 |

**E6 不受影响**:相对 BUY 的所有权在 `scan/relative_buy.py` 的 `write_decision`/`verify_decision`,它们从不读账本。`scan_config.jsonc` 的 `relative_buy.mode=active` 照旧。

**E6 v3.0(2026-08-26 §3 路A)** —— 两件事,各自可单独回滚:
- **A2 硬门扩集**(对**两个池都生效**):`research_rating ∈ {Sell, Underweight}` 或卡面 `FINAL TRANSACTION PROPOSAL: SELL` 或早停停因 ∈ {基本面恶化, 估值透支, 涨停追高, 数据不足} → 否决。立案:08-20 金螳螂、08-25 天味食品**两次**把提议 SELL 的 UW 卡发成当日 BUY(v1 已知问题 #6 原话:「提议卖出的票可以当相对 BUY 出这条通路是敞开的」)。**留在集合外的是 {其他, 题材透支, 资金流出}** —— 它们在隔夜尺上对 Hold/UW 无区分力(L4·Hold −0.20 vs UW −0.35 不显著),把它们也当红灯 = 拿没证据的判断否决有证据的候选。回滚 = 改 `relative_buy.py` 的两个 frozenset(代码改动,不是配置)。
- **A1/A3 候选池切换**(`scan_config.relative_buy.pool`):`composite` = BUY 只在守卫⑨ 的证据席里选、**排序改按 composite 分**(`target_align`),另三面降为记录列。理由是尺:L3·finalist 一族 40 日隔夜相对超额 **−0.27pp(t=−3.94)显著为负**,而 composite 是全表唯一正证据(+0.14pp t=3.05;L2 top20/50 +0.14/+0.17,t 1.96/2.78)。**期望 ≈ 一次往返成本量级,不承诺隔夜赚钱**。席位为空 → 诚实 `blocked`,**不静默退回全体池**。回滚 = 该键改回 `finalists`。
- **执行线(A4)**:卡片写两条机读行 `[执行线] pct_chg <= 3.0` / `[执行线] pos_in_range < 0.7`(T+1 尾盘**入场**条件,与三型盯梢线不同族;`tripwire_watch` 解析但**不报警**,事后计量在 `scan/outcome.exec_ok`)。证据:四年全湖 1086 日,收在当日区间上 30% 的票隔夜比全体差 0.13~0.27pp,**逐年同号**。

---

## 覆盖档案链 —— `autoresearch/dossier`(**不属于闭环,整条保留**)

常备覆盖模型:`coverage_pool.json` 池(prelude 日检:进=pinned/20日真选≥2、退=20日未选、cap30 LRU)→ `$CTX/knowledge/dossiers/<code>.md` 八节档案(`dossier-init` workflow 首覆)→ L4 prompt 注入「📚 覆盖档案摘要」(`schema.injectable_summary` 四门 = 注入器与卡 lint 单一事实源)+ intel prompt 内嵌已知底(情报员无 Read = 结构性盲)→ 卡写「档案对账」节(`self_review` 分档探针)→ assemble 尾 `delta.record_scan_deltas` 按**终评级**回写 §8 + 刷新 §2/§3/§4/§6/§7 与摘要机算行 → 季度对账 `python -m autoresearch.dossier.reconcile <period>`(express 优先/forecast 兜底/未披露也落痕;prelude 📐 提醒 + 🕰️ 90 日陈旧告警)。

全链 presence-gated:无档案 = 注入前行为逐字节不变。**与 `scan/dossier.py` 的「前科卡」(跨日入围史,强制卡内"变化项"节)是两件事,并存不互替。**

**为什么它不随 learning 层走**:档案记的是**这家公司的事实**(业务模型/盈利驱动/风险矩阵/披露对账),不是"系统从自己的历史判断里学到了什么"。它不改权重、不改门、不往 prompt 里塞历史胜率 —— 注入的是公司事实,和 slim/intel 同类。

---

## 历史产物(只读,无人再生产)

按用户裁定,盘上已有的账本文件**原样保留不动**:`$CTX/learning/*.jsonl`、`$CTX/scan/*/retro/*.csv`、`$RPT/learning/*.md`、`$CTX/knowledge/`(lessons/proposals/precedents.db)。**代码侧零读侧、零写侧** —— 没有任何生产路径写它们,也没有任何模块读它们(2026-08-21 二轮清理后,brief 的两尺分歧腿与 replay 的 R3 也删了)。它们是纯归档。

`$CTX/learning/` 这个目录名是历史遗留,里面仍有两样**活的**东西,与闭环无关:`temperature.csv`(S1 情绪温度计,prelude 增量落盘)与 `usage_reconcile.jsonl`(token 计量 streak)。目录不改名——改了历史文件就跟路径失联。

覆盖档案链(`dossier/*`)**不属于 learning,整条保留**:池日检在 prelude、首覆走 `dossier-init`、L4 注入「📚 覆盖档案摘要」、收尾 δ 回写、季度对账 —— 一件没动。

---

## 数据层要点

- **源**:tushare 默认(push2 被网络封锁;`TUSHARE_TOKEN` 高权限);keyless 可达:同花顺一致预期(L4 fwd-PE)/ 腾讯 / datacenter-web。限频:`report_rc` 1 次/小时。
- 🚨 **数据契约**(`autoresearch/data/contracts.py`,用户裁定"取数后全面校验,为空抛异常阻断"):
  - **A 级(地基:daily/daily_basic/moneyflow/cyq_perf/stk_factor_pro/stock_basic/trade_cal)**——空/行数腰斩/缺列 → `DataContractError` **阻断整条流程**且**拒绝入湖**。校验挂三条路径(取数后写湖前 / 湖命中 / 未结算日只查空不查列)+ 因子帧出口门(`check_market_frame`)+ `_harvest_vol_series` 失败即抛。**`DataContractError` 不得被任何 `except Exception` 吞掉。**
  - **B 级(增强:北向/两融/龙虎榜/公告/质押/新闻/宏观)**——缺失只降级,但**必须记账**(`degradations()` → `degraded.json` → 报告一行);不走 cache 的降级点用 `record_degradation()`。
  - **为什么分级**:真实的空是合法的(presence-gated 降级是设计);真正的病是**降级不留痕**——打分对整组 NaN 会剔分母放大其余组权重,漏斗照样跑完退出码 0。湖体检:`python -m autoresearch.data.contracts doctor [--purge]`(A 级毒源必清;B 级空帧多为真实的空,不删)。
- 🚨 **入湖一律全字段**(`cache._lake_params`):`_cache_key` 不含 `fields` → 一个 key 只有一个 parquet;带窄 `fields` 的查询若成为某 key 首个写入者,就把窄表钉成该日快照,后来者只能读到缺列表,且失败多被上游吞成静默降级(实证:两列窄表 → volprice 组整组 NaN → 全市场 composite 失真 98.8%)。**要窄列自己 `df[cols]`——多几列无害,少一列是灾难。**
- **降级**:缺权限 B 级端点自动 NaN、打分重新归一(且记账)。

---

## 已被实证否决的方向(勿重启)

- **L2 上模型**:全 zoo 负 IC + 回测无稳健 alpha;新特征 IC 过硬之前不复活。
- **业绩预告做 L1 事件通道**:强制披露季 T+5 超额 −0.27%/胜率 35%,追缺口 −2.92%——公告后追买无肉;alpha 若有,在披露前的预期变化里。

---

## 开放线头(诚实局限)

1. regime 块 horizon 之争(`pr_20260702_001`)待 T+5 数据裁决;risk_off 块样本薄(11 日)。
2. healthy 通道反事实、capfloor20 —— **取证渠道随 2026-08-21 闭环退役整个消失**(影子变体、`channel_ledger`、`channel_audit` 全删)。要重开得先重新造一把前向尺。**reversal_confirm 与旧 reversal 的 A/B 于 2026-08-19 结束、2026-08-21 重开**:08-19 摘出的原因是名义启用实际恒空 4 周+(起爆硬门 `vol_ratio_20` 从未接入生产 L1 帧,缺列即整段判 False);08-21 低位转强波把两件事都做了 —— ① `vol_ratio_20` 等十列经 `frame._harvest_vol_series` 60 日面板 + `common/turnup.py` 接入 L1 帧并入 `keep` 白名单;② 起爆硬门③由 `ma_bull` 改为 `above_ma20 ∧ ma5_gt_ma10`(旧代理含 MA20>MA60,与①「60 日跌 ≥25%」定义互斥,只接列不改门通道仍会近空)。**A/B 的裁决腿已随 2026-08-21 闭环退役删除**(`channel_ledger` 与 `channel_audit` 都没了);配额 150 保留,但目前无尺可裁。`self_review.channel_liveness_lint` 逐日盯「启用通道 0 行」,防同族复发。36 日版新配额(value312/momentum188/heat112/healthy112/growth112/main_fund150)已于 2026-08-21 前拍板生效,不再是开放线头。
4. consensus 积累 <60 日不入线上;anns_d 无接口权限 → 公告情感列空、监管旗恒空(`anns_empty_rate`=1.0 即该态,index/L3 表头有显式标注)。
5. 温度计菜单/预算联动待相位判定质量复审。(三门账本/tail_rate 雷分级随 `gate_ledger` 于 2026-08-21 退役。)
6. 仅供研究,非投资建议。

---

## 运维细节(SKILL 只留指针;跑动时不需要逐字读)

- **低位转强 Gate 0 回测**(只读,手动):`uv run --no-sync python -m autoresearch.research.lowturn_precheck [--cap-floor 30]` → `reports_claude/research/lowturn_precheck.md`。前向收益由 `factor_lab` 面板**现算**,不依赖任何账本,故不受闭环退役影响。**参考尺只观察,决策尺不变**(2026-07-10 / 08-05 裁定)。(原 `--live` 活体双尺观察腿读 `retro/attribution.csv`,已随闭环删除。)

- **漏斗形状对照实验**(只读,手动,**预注册**,不接 prelude):`uv run --no-sync python -m autoresearch.research.funnel_variants --spec <已冻结方案 spec.json> --scan-dir <冻结日目录> [--scan-dir …] --outcomes <收益表>` → `$RPT/research/funnel_variants/<experiment_id>/`(`spec.json` / `membership.csv` / `daily_metrics.csv` / `paired_summary.json` / `manifest.json`;实验目录排他创建,已存在即拒 —— **没有 `--force`**,想改假设就换 `experiment_id` 开新实验)。
  逐日重建三个**同预算**漏斗(`current` 读冻结产物 / `composite_only` / `composite_plus_diversifiers`),同 L1/L2/pass1 名额、同主尺 `gap_c1_o2`、同可交易定义,按 date 等权配对。收益表需 `date`/`code`/`gap_c1_o2` 三列(有 `status_gap_c1_o2` 则按 MATURE 判成熟);**缺收益留空不填 0**,未成熟/空选择/缺行情是不同状态。
  **旋钮全在 spec 里,命令行上一个都没有**:80/20 core、行业帽、style floor 写死在 `selection_rule`,bootstrap 的 `n_boot`/`seed` 与最小共同日(`maturity_policy`)同理 —— 留在 CLI 上,预注册就只是句口号。开跑前逐项验:引擎、evidence/cost 模式、`code_sha` 对 behavior roots 无漂移且工作区干净、输入清单 sha256 逐文件对得上、分析日全部落在冻结的 test 区间内;任一条不过就拒跑且**一个字节都不落盘**。家族登记见 `docs/research/2026-09-13-funnel-shape-family.spec.json`(过 F1 契约,`tests/research/test_family_registry.py` 守)。
  读法只有一条:`paired_summary.json` 的 `evidence_status` —— `PROMOTION_EVIDENCE`(≥20 个共同成熟日 ∧ bootstrap 90% CI 下界 > 0)才够资格**另开**设计与回滚计划;`INSUFFICIENT_EVIDENCE` / `NO_SWITCH_EVIDENCE` 都是「尚无证据切换」,**不是「证明两者相等」**。本命令不写任何 run 目录、不改任何生产参数(召回通道、pass1=25、knife 硬门、finalist 上限 5、composite BUY seats 一律照旧)。设计:`docs/superpowers/specs/2026-09-13-funnel-shape-gates-dual-engine-design.md` §3。

### 夜间预热(launchd)

交易日 19:30 自动 `scripts/prewarm.sh`(= `python -m autoresearch.scan.prewarm`,湖预拉+温度)。跑过预热的日子开扫全湖命中(L0-L2 ~6.5m);**当天有没有预热看汇总屏「预热(夜间):✓/✗」行**。安装:

```bash
sed "s|__REPO__|$PWD|" scripts/com.tradingagents.scan-prewarm.plist \
  > ~/Library/LaunchAgents/com.tradingagents.scan-prewarm.plist \
  && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.scan-prewarm.plist
launchctl list | grep scan-prewarm          # 验证
launchctl kickstart -p gui/$(id -u)/com.tradingagents.scan-prewarm   # 手动触发
```

### user_config 传参铁律

`frame --json` 回显的 `user_config` 必须随 Workflow `args.config` 传入,L4 逐股 `args.cfg` 原样透传。**传 `{}` = 静默关 l4_intel + 全体 agent 掉回内建缺省 effort**(配置真身是 `scan_config.jsonc`,**.jsonc 非 .json**,按旧名查无传空就是事故形状;现 workflow 对空 config 直接 throw)。新 run 优先消费 `resolved_agents`；Claude 老 workflow 的内建表只服务离线/历史兜底，Codex project agent TOML 与 production profile 由测试锁定同值。

### 哨兵 vs 持仓

哨兵判据只问「今天有没有值得买的」,**不含「持仓要不要动」**。有 pinned 持仓时哨兵档跳 L3/L4 会让持仓拿不到当日决策卡 → 必传 `force_full: true` 覆盖(实测:哨兵开火日 4 只持仓身处崩盘日,靠 force_full 才拿到 Sell/UW)。哨兵说「没得买」可以是对的,它只是不知道你有持仓要判。

### L4 派发节奏(现行)

**一次性全派**:`l4_tasks batches` 返回单批全量 pending,`effective_cap`=`caps.l4_stock`(默认 64);tushare 并发由 `prepare_slim` 内 K 槽信号量控制,不靠派发节奏限流。回滚杆 `budgets.concurrency.l4_stock=4`。

### 活体情报站

铁律见 L4 节(价格断言须与 verified OHLCV 对账)。已知线头:限频自报仍会超 cap(warn 信号已按实测中位对齐 `max_queries=20`)。

### 覆盖档案:重做首覆的正确姿势

📐 = 该报告期未对账、🕰️ = 档案 >90 日未全量刷新。解药是该票跑一次**成功的季度对账**(`dossier.reconcile <period>`,唯一写 `last_refresh` 的路径)。要重做首覆须先 `builder --force`(**不是** `dossier-init --force`,该 flag 不存在;对已建档票重派 `dossier-init` 是 no-op,清不掉 🕰️);注意 `builder --force` 清空 `initiated`/`last_refresh`,该票期间同时退出 🕰️ 与 `pending_init` 视野。未披露也落痕,所以 📐 计数应随对账动作**下降**;天天恒定 = 探针坏了。

### l4_tasks 子命令语义

- **`preflight` 是认领不是只读探针**:调用即可能把该票置 RUNNING 抢锁(误用作"验证"会抢走别人的票,靠 `failure --error-class TIMEOUT` 释放)。人工看状态用 `l4_tasks stats <date>`(纯读)或直接读 `_l4_tasks.json`。
- **prompt 硬门**:`init` 见任一 `_l4_prompt_*.md` 缺失/空 → `ok:false` 拒建任务簿(修复=重跑 `l4_card prompts` 再 init,幂等 ~3.5s);`preflight` 缺 prompt → `BLOCKED/PROMPT_MISSING` 不认领。**没有 `--allow-missing-prompts` 这种口子——修产物,别修门。**
- **intel 同日续传**:事故重派时 preflight 对「稿+status 完好且 ≤24h」的票返回 `intel_resume:true`,l4-stock 跳过重盲搜并在 status 落 `resumed:true` 披露。跨日/重放历史日不满足 → 照常盲搜;强制重搜 = 删该票 `_l4_intel_*` 两个文件。

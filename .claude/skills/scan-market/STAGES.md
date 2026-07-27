# scan-market 各阶段现状(as-of 2026-07-12)

> 本文件只记**当前态快照**。沿革见 git log 与 `docs/specs/` 下各设计稿;**冲突时以源码为准**。
> 文档分工:`SKILL.md` 讲**怎么跑**;操作模板分驻各能力 skill —— 市场研判在 `macro-playbook.md` 末节,L4 决策卡在 `stock-research` 的 `lite-playbook.md`。

---

## 一、漏斗全景

主链六段,从全市场一路收窄到一份报告:

```
L0 选集  →  L1 召回  →  L2 粗排  →  L3 精排(两遍法)      →  L4 研究     →  L5 整合
全A ~5500    top1000    top200     pass1→~40→finalist 7–10   决策卡×(7–10   1 份报告
（确定性）  （确定性）  （确定性）  （确定性+Opus×1）          +♻️复用/📌保送） （确定性）
```

**两条旁路**(不在主链上,并行算好后喂进主链):

- **市场研判** = macro-research 的 lite 档。Stage 0 与 L0 并行,一个 Opus 产出 `market_view.md`,L3 / L4 / L5 三处复用。
- **行业 brief** = sector-research 的 lite 档。L2 之后按行业并发,喂 L3 / L4 / L5。

**主链之外的三个确定性动作:**

- L1 旁支:影子漏斗产 2–3 个变体(免费 A/B,喂 retro 做对照)。
- L4 派发前:卡片 TTL 复用(♻️ 没变化的 Hold 票不重派)。(观察单触发直通车已随观察单退役,fb_20260714_002。)
- 事后闭环:retro 归因 → 自动重标定权重 + 人工批准的建议/经验 → 注回 L1 权重与 L3 校准块。

**三层角色分工:**

- **确定性层**(L0 / L1 / L2 / L5 + 全部度量):零 LLM,纯 pandas,不编数。
- **AI 判断层**(L3 / L4 / 策略师):全部是 Opus subagent,只回传紧凑结果。
- **闭环层**(`autoresearch/learning`):用已兑现的涨跌批改前两层。

---

## 二、核心世界观(实证结论,决定功夫花在哪)

- **确定性层没有 alpha。** L2 全部 zoo 模型 OOS rank-IC 为负;4 年回测 composite-top200 收益 ≈ 0(依赖 regime,2025–26 反转段 −24bps)。→ 所以 L2 不做预测,只做"菜单"(多样性采样)。
- **判断层已证的 edge 在「拒绝」,不在「挑选」。** L3 真选无正 alpha 证据(07-12 复盘:finalists 11 日 −0.39pp/2日,t≈−1.2;07-16 去📌保送污染后真选 −3.8%,方向仍负)。已证有效的是拒绝侧:L4 评级 rank-IC **+0.55** 分档单调、门价值 **+4.35pp**(纸面 NAV 三线),L4 推翻 L3 高确信两次全对。
- **0 买的根因在召回线。** 413 只 T+1 赢家里,91% 落在打分池、但只有 4.8% 越过 top1000 召回线;composite IC **−0.11**。→ 修法是 regime 分桶权重(见 L1)。
- **0 买不等于失灵。** 历史 0 买日,市场 fwd_1 −0.48% / fwd_5 −0.60% → **空仓方向是对的**;哪天 0 买日市场却涨,才是失明预警。

---

## L0 · 选集 —— `autoresearch.scan.universe`(确定性)

全 A(~5,500)过硬门,只留"可交易、可研究"的票。

- **硬门**:剔 ST / 退市 / 停牌 / 次新;市值地板(默认 30 亿,`--cap-floor` 调);北交所默认纳入(`--exclude-bj` 剔)。
- **哲学**:只剔"确定不可交易 / 不可研究"的 —— **每加一条硬门就是一块永久盲区**。
- **已知局限**:missed_l0 ≈ 赢家的 9%(以小盘 / 次新 / 北交所为主);"软化市值地板"提案已提、未批。

---

## L1 · 召回 —— `autoresearch.scan.recall`(确定性,→1000)

多路策略并行,每路都"过门 → 按信号排序 → 截 top-quota",再 `quota_union` 合并(各路 floor 保底多样性),带 provenance。默认 `--recall-mode multi`。

**已注册 12 路通道**(含 Wave4 新增、**默认停用·取证中**的 `event`;启用哪些由 `scan_config.jsonc` 的 `funnel.recall_channels` 决定,当前默认启用 9 路——**该 key 缺省 = 用全部 12 路**,删掉整行会把 `event` 一并上线,违反 `pr_20260725_001` 的入场纪律):

| 通道 | quota/floor | 信号 |
|---|---|---|
| composite | 400/100 | IC 校准复合分 |
| momentum | 250/50 | 趋势龙头 |
| reversal | 200/50 | 困境反转(边际改善 或 超跌 或 资金);旧路,与 reversal_confirm 并跑 A/B |
| **reversal_confirm** | 200/50 | 反转确认四段:低位 + 企稳缩量 + **放量突破硬门** + 可交易;无量突破一律不召回 |
| value | 200/50 | 行业内低估 |
| main_fund | 200/50 | 主力净流入 |
| heat | 200/50 | 成交额量级(捞巨额龙头) |
| growth | 150/40 | 成长加速 |
| healthy | 150/40 | 质量上涨(0<pct60<40 且 主力净流入>0 且 cmf>0);补的是旧 composite 把这类品相排到 4000+ 名外的空洞 |
| accumulation | 120/30 | 底部吸筹 —— **默认停用**(累计 unique 超额 −0.21%,并入 reversal_confirm) |
| northbound | 120/30 | 北向持股 —— **默认停用**(hk_ratio T+2 IC −0.108,信息已在 L4 简报行) |
| **event** | 80/20 | 公告事件(近 10 日回购实施/预案与增持均按公告去重:回购 `(ts_code,ann_date,proc)`、增持 `nunique(ann_date)` · 调研只作有无)—— **默认停用·取证中**(`pr_20260725_001`)。信号来自 `scan/events.py` 全市场事件计数(复用 `l3_catalyst.catalyst_counts`,湖优先),排序键 `ev_hard` + composite 决胜;**不用当日涨幅**(07-21 实证:当日 ≥9.5% 的 350 只 fwd_2_oc −2.06% vs 市场 +1.60%,超额 −3.67pp t=−11.91 = 追涨为负价值)。L2「事件」桶 floor **=0**(未启用通道不得改生产 L2 分布——加 floor 会改 `merit_need` 进而每天翻 10 行 `l2_lane_reserved` 标签,而该标签喂 L3 表/`force_full_card`/`floor_experiment`)。首读(07-21,n=1 不裁决):会员面零 edge、**边际面 unique 40 只超额 −1.01pp(t=−1.83)**。判据:`channel_audit --variant plus_event` 的 `unique_excess_t2` 累计 ≥10 日 >0 才提启用,维持为负则退役 |

**配额覆盖已接线生效(2026-07-11)**:`scan_config.jsonc` 的 `funnel.channel_quotas` 当前生效 **value 250 / heat 150 / main_fund 150**(channel_ledger advisory 档,拍板 5);兜底读取在 `universe.run` 本体(`_funnel_overlay`,FN-1 第三修——prelude/`universe.main` 直调路径同样生效,显式参数/CLI flag 恒优先,缺文件=注册表默认 parity)。影子变体(`pre_healthy`/`capfloor20`)同口径透传,反事实不受配额差异污染。

**rz 入组(2026-07-11,pr_20260710_001 resolved)**:融资买入强度 `rz_buy_intensity` 独立第 10 因子组(自然朝向+,权重 calibrate@fwd_2_oc 重校,changelog 可回滚);语义=情绪接力资金代理(文献 A9),非基本面确认。**capfloor20 第 4 影子变体**上线(cap_floor_yi=20,验 pr_20260624_001 市值地板漏判)。

**regime-aware(推荐常开):**

- `--regime-aware` 会按当日 regime 取 `weights.json` 里 `regimes[trend|range|risk_off]` 对应的权重块,缺块回退 flat。
- regime 判定(`common/regime.py`):breadth ≥ 0.55 且 pct_60d>0 → trend;breadth ≤ 0.30 且 pct_60d<0 → risk_off;其余 → range。
- 当前块(107 个成型日):trend 43 / range 53 / risk_off 11;momentum IC 在 trend −0.055、range +0.015。

**已知局限:**

- risk_off 样本薄(仅 11 日);horizon 之争未决(`pr_20260702_001`,T+1 与 fwd_5 待数据裁决)。
- 影子漏斗:`--no-shadow` 不加时产 **5** 个变体(`nostrat` / `nocap` / `pre_healthy` / `plus_event` / `capfloor20`)落 staging,由 retro 对照捕获,累计 ≥10 日才提 proposal。**Wave4 起影子也落逐路长表** `L1_channels_<variant>.csv`(列同主 `L1_channels.csv`),由 `channel_audit --variant <name>` 消费算 `unique_excess_t2`(此前影子只落 L2 粗读数,新召回路无法按 accumulation 当年被裁同口径裁决,本波头号交付物)。`pre_healthy` 语义 **2026-07-25 起变更**(cd31179):基准从"全注册路"改为"当日实际启用路"(修复反事实混入已停用的 accumulation/northbound)——跨该日期读 `L2_pre_healthy.csv` 趋势线有定义断层,复盘时别直接连线。

---

## L2 · 粗排 —— `recall/l2_stratify.select_l2`(确定性分层采样,→200)

**确定性分层多样性采样器,不用机器学习。** 三步:

1. 按 sector-neutral composite 排 merit(核内 + 桶内);
2. 7 个风格桶各有固定 floor(趋势 20 / 健康 15 / 反转 12 / 价值 12 / 成长 12 / 吸筹 12 / 主力 10);
3. 任一申万一级 sector 占比 ≤ 20%。

产物 `L2_gbdt_top200.csv`:`l2_rank` = 选择序、`gbdt_score` = composite、`l2_lane_reserved` = 被 floor 救回。**不做预测**(分层是免费的,strat ≈ composite-top200 ≈ 0)。

**菜单体检**(`scan/menu.py`):

- 看四项:行业集中度 / 落刀面 / 健康上涨 / 估值;自动嵌进 L5。健康上涨 = 0 就打 **⚠️ 菜单病**。
- 实证:某日落刀票 L2 占 70% vs 全市场 32%。

**哨兵建议**(`menu.sentinel_advice`)—— 按全市场健康占比给档:

- < 3%:建议走哨兵档(跳过 L3+L4,省 ~70% token);
- 3–5%:仅 consider(2026-07-08 放宽:删掉原 risk_off 升级档,不再 auto-skip,regime 只写进文案);
- ≥ 5%:全扫。
- **由人拍板,不自动**(workflow 只对 `sentinel` 档自动跳)。retro 侧做 floor 自然实验(救回组 vs merit 组 vs 被挤组 fwd 对照),持续弱才复审。

---

## 旁路 · S1 情绪温度计(2026-07-11 上线,展示先行)

- **数据**:tushare `limit_list_d` 入湖(勿用 akshare 涨停池,push2ex 被封);五序列 = 涨停/跌停家数、连板高度、晋级率、炸板率、昨涨停今溢价 → `score` 0-100 + 五相位(冰点<20 / 修复 / 发酵 / 高潮≥65 / 退潮=带内下行,±3 滞回)。已回填 124 交易日(2026-01-05 起),幂等增量 `context/learning/temperature.csv`。
- **消费**:market_pack `temperature` 块(两条 pack 路径,presence-gated 缺→键不出现)+ L5 🌡 行;prelude 新增 `temperature` 步(当日 fetch+rollup,失败不阻断);分段校准报告 `python -m autoresearch.scan.temperature_calib`(phase×regime 交叉表,n<10 ⚠)。
- **边界**:**本波不接菜单/预算联动**(拍板:相位判定质量复审后下一波);涨停数据只进温度计,不做打板/隔日溢价信号(负结果清单)。

## 旁路 · 市场研判 = macro-research lite 档(Opus×1)

Stage 0 与 L0 并行,回退到 L2 之后落盘。模板在 `macro-playbook.md` 末节。

- **机制**:确定性 `market_pack(scan_dir)`(regime / 宽度 / 估值分散 / 资金 / 红黑榜,只读 `L1_scored_full`)→ Opus subagent(`macro-brief`)写六小节 `market_view.md`。三处复用:L3 地形段、L4 `market_context_block`、L5 置顶。
- **防锚定铁律**:喂 L3/L4 的只能是**描述性地形**,不能是方向指令;操作建议只进 L5;**个股评级只由本股 rubric 三门决定**。缺文件 → L5 回退确定性脉搏。
- **配置装载链**(Plan A3):
  - `scan_config.jsonc`(真 JSON 直接生效、支持 `//` 注释;白名单加载见 `autoresearch/scan/user_config.py`)经 `frame --json` 校验后,回显进 `market_pack` 与 `user_config_echo.json`。
  - 由调用方随 Workflow 的 `args.config` 传进 `scan-market.js`(脚本本身无文件系统访问,不能自己读文件)。
  - 各 stage(`strategist` / `sector_brief` / `l3_rank` / `l4_card` / `l4_intel` / `redteam`)的 agent model / effort **优先级:scan_config > workflow 内建 > agent def frontmatter 默认**;缺配置或缺键 = workflow 内建现值(parity)。当前档位(07-12 用户拍板):`strategist` effort=max、`sector_brief` effort=xhigh(sonnet 下沉试点**已回滚**,恢复 agent def 默认 opus)、`l3_rank` max、`l4_card` xhigh、`l4_intel` max(sonnet)。

---

## 旁路 · 行业 brief = sector-research lite 档

L2 之后、与 L3 证据取数**并发**:

- `sector.reuse <date> --apply`(TTL ≤ 5 日 ♻️ 复用;已复用的行业从 fan-out 里排除,不再被重派覆盖)
- → 剩余的 `sector.pack <date>`(红榜 top3 ∪ L2 集中度 top3 ∪ 存量 watchlist.csv 行业,K ≤ 6;观察单日检已退役,此处只读存量文件)
- → 每个行业派一个 `Agent(subagent_type='sector-brief')`,写两段契约 brief:`## 地形段`(喂 L3/L4)、`## 研判段`(仅 L5,含 `**行业方向**` 这一 keyed 行)。
- L4 派发前,对 ≥2 只同行业 finalist 的行业补漏。

**消费与价值:**

- `l3_table_md(sector_terrain=True)` 固定只渲染 L2 top200 覆盖的行业(`top200_only`,约 110 行压到 30–50 行);assemble 自动嵌 🏭 行业研判 + 🔗 同链对比(presence-gated);发布时 `sector_ledger.record_calls` 记方向(MTM,n<10 时 ⚠ 只记账)。
- 价值 = 同链论点摊销 + 行业相对估值锚。**不解决 0 买,也不设门。**

---

## L3 · 精排 —— pass1 确定性分诊 + holistic 单 Opus 深比较(200 → ~40 → finalist tier 7–10)

**📌 保送票也走 L3**(2026-07-12 修复):pinned 由 pass1 的「① pinned 全入」规则保证进 L3 表 → l3-rank **照常独立判**(写 thesis/风险/催化/conviction,`finalist:false` 不占 7–10 名额)→ `_inject_pinned_finalists` 把这份判断**整段带进 finalists.csv**(lookup 优先级 `fin` → `judged` → L2 → 占位)。
> 🐛 修复前:该函数只在 `fin`(=`finalist:true`)里找 pinned,找不到就退回 L2 表(**没有** thesis/risk/catalyst 列)→ L3 对持仓票的判断被**整段丢弃** → finalists.csv 空 thesis → summary 渲染成「风险:;催化:」→ L4 prompt 告诉卡片「pinned 无 L3 前提清单」,卡只好自己从 L1 重建前提。07-10 实跑 4/4 持仓中招。**保送 ≠ 免判,更 ≠ 判了不要。**

**两遍法**(2026-07-12,用户裁定 L3.5 收窄职能并入 L3):pass1 是确定性分诊(零 LLM,`triage_l2_for_l3`)——pinned/多路共振/healthy lane 全入 + 各召回通道 top-K 轮询,把 L2 的 ~200 行收到 `pass1_target`(scan_config 现 40;2026-07-18 线A 影子回放 20 日验证后 60→40:mandatory 恒保 0 漏、two-pass 时代仅丢 1 只非赢家 Hold 卡、delta 行无赢家富集);被切部分不代表判死,落影子 `_l3_pass1_cut.csv` 供 attribution 验证分诊没吃赢家。pass2 由一个 Opus 通看这 ~40 只、比较着选(而不是孤立逐只打分,比较式 > 逐只),深比较后给出 **finalist tier:7–10 只**(按当天质量,`finalist:true`,宁缺毋滥不凑数)+ 其余判断过但未入选的 **bench**(`finalist:false`,落 `_l3_bench.csv`,防漏影子——账本会追踪 bench 里有没有藏该进 finalist 的够格票)。

**流程:**

1. `harvest_l3_evidence`(龙虎榜 / 预告 / 快报)+ `harvest_l3_news`(公告情感)补证据;
2. **pass1 分诊**(`prepare_l3_table` 内接线,`two_pass` 默认开):`triage_l2_for_l3` 把 ~200 行收到 ~40,cut 落 `_l3_pass1_cut.csv`;
3. `l3_table_md` 压成紧凑表(表头注明「pass1 分诊 n→n」);
4. 一个 Opus-high 通看 ~40 只,按 6 维 rubric(channel 共振 / 资金 / 基本面 / 情感 / 脆弱 / T+2 兑现机制)比较着深比较,给出 finalist tier:7–10 只(`finalist:true`)+ 其余 bench(`finalist:false`);
5. 写 `L3_judged_full.csv`(finalist+bench 全量判断)→ `merge_l3_finalists_v3`(finalist 标记消费 + conviction≥75 误杀保险 + <55 剔除 + 健康画像比例守卫 + cap=min(`finalist_max`,预算))→ `finalists.csv`,bench 落 `_l3_bench.csv`;
6. 校准注入:因子方向经验校准块 + 策略师地形段 + 行业备忘录块。

**token 经济与预算:**

- `delta=True` 略去无变化的票。
- L4 派发数由 `menu.l4_budget` 控(五面旗:落刀>60% / 相对落刀>40% 且 >2× 全市场 / 健康涨≤2 / risk_off / 0买连败≥3;命中 1 旗 → 22,≥2 旗 → 15);finalist tier 上限 `l3cap = min(10, l4_budget)`,由 workflow 传作 `finalists`/`gate2` 的 `--budget`。

**推荐常开三面旗**(presence-gated,默认关 = parity):

- **主力失真** `dist_flag`:反号 / 微量;命中 18/30,被 L4 辟谣。
- **监管** `reg_flag`:近 10 日立案 / 问询 / 处罚等(未实跑)。
- **误读三预警** `misread_flag`(`misread` 列,谓词 `scoring.l3_misread_flags`;L4 简报同步注旗,l3-rank 硬约束 E 强制自证;回放命中 12/20):
  - 低基:np_yoy>100 且 roe<8;
  - 背离:cmf_20 或 obv_mom_20 为正,但 main_net_ratio<0;
  - 套牢:winner_rate<25 且 ma_bull=0 且 pct_60d>0(反弹撞上套牢盘)。

**误读三闸(2026-07-11,直击误读自见数据 22/31):**

- 行语义指纹 `pf` 列(确定性画像短语,如 `高位·放量·主力+·PE低`——LLM 读词不读裸浮点);
- 表按 lane 分块渲染(`l2_lane_reserved` 非空值分块、其余按 `recall_channels` 首通道,块内 composite 降序,meta 记 render_order);
- `l3_select lint <date>` thesis 数字机检(引用数字须能在该票行值/催化字段容差匹配)→ workflow L3 后**一次打回自修**(不二检)。

**输出契约增强(2026-07-11)**:judged 增 `mechanism` 字段(两日内兑现机制+明日买家,写不出不选);conviction 行为化定义(**≥70 = 能说出 D+1 谁买且愿真金买入,每日 ≥70 限 ~5 只**;50-69 = 值得 L4 验不背书)——L3.5 回测(唯 ≥70 有 T+2 edge)的语义落地。

**输出契约增强(2026-07-12,finalist tier 落地)**:judged 每元素加 `finalist`(true/false)——`true` 者即 finalist tier(7–10 只,数量看当天质量,宁缺毋滥不凑数),`false` 即 bench(仍全字段判断,落 `_l3_bench.csv`,防漏影子)。确定性守卫(`merge_l3_finalists_v3`):conviction≥75 未标 finalist → 强制补入(误杀保险,`guard=ins75`);conviction<55 → 剔除(`guard=lt55`);超 `finalist_max`(默认 10)→ 按 conviction 截尾;健康画像不足 ceil(n/3) 且 bench 有够格 → 从 bench 补(`guard=healthy_quota`)。缺 `finalist` 字段(向后兼容旧 judged)→ 全体按 conviction 排序取 cap,同守卫。

**稳定性与验尸:**

- 周频抽检:`shuffle_seed` 乱序再跑 audit agent,overlap<0.70 → 提 proposal。
- 错杀验尸(retro 侧):L2-keep 且非 finalist 且 T+5 赢家,join 红队理由写 lesson。实证错杀 = 0 —— **病在召回线,别冤枉判断层**。

**(L3.5 已完全移除——2026-07-12 用户二次裁定"完全移除、直接 L3 输出";design `2026-07-12-funnel-replay-l35-removal-design.md` §1)**:收窄职能由上面的 finalist tier 承接(merge v3 cap=min(`finalist_max`, `l4_budget`)),链路 = L3 → GATE2(只读校验:6 位码 + exempt lane 不占名额记账,`gates._EXEMPT_LANES`)→ L4 派发,中间无任何收窄层。`scan/l35_gate.py`、`research.gate_backtest`、`l4_gate` 配置键、retro/assemble 的闸影子节**全部删除**;13 日回测结论(「只有 conviction≥70 有 T+2 edge、中间 band 是噪声」)已内化为 conviction 行为化定义与 finalist tier 质量门,历史报告留存 `reports/research/gate_backtest_2026-07-11.md`;将来复验"收窄闸"类假设用**漏斗回放器**(同设计稿 Part B),不复活 L3.5。

---

## L4 · 研究 —— 一只票 = 一个 Opus subagent(渐进深度 + 早停)

### 派发前四道确定性闸(按序,生产者都跑在 prompts 之前)

- ⓪ **批量质押旗**:`l4_card pledge <date>` → `pledge.csv`(质押>40 标爆雷、>20 偏高;advisory,不动门)。
- ① ~~观察单触发直通车~~(已随观察单日检退役,fb_20260714_002;`append_express` 随 watchlist 模块一并删除)。
- ② **卡片 TTL 复用**:`l4_reuse <date> --apply`。近 4 日已出卡、评级 ≤Hold、|Δ价|≤5%、无新公告、regime 未翻、conviction<70 → 直接 ♻️ 复用,不派 subagent(OW 三门失守 ≥2 的深否决豁免 conviction 拦截;≥OW 永不复用;复用率约 20%)。
  - ⚠️ **复用门判据是「这只票变了吗」,不含「今天是不是极端日 / 它是不是持仓」**。持仓票(pinned)在崩盘日想要新鲜判断时,复用门可能按 |Δ价|≤5% 挡掉重研 → 手动删 `details/<code>.md` 复用卡再 `prompts` 重落稿 + 重派该股 l4-stock。**2026-07-17**:北方华创(持仓)被复用挡(Δ价 −3.7%<5%、无公告、regime 未变),强制重研结论未变(仍 UW)= 那次复用**判对了**;记此非为推翻复用门,而是崩盘日持仓「卡过期」的误判代价不对称(错的一侧是持仓在流血却拿旧卡),值得对 pinned 强制刷新。
  - **⛔ 菜单滞回(carryover)已于 2026-07-16 退役**(用户裁定,`pr_20260716_006`)——**勿再加回**。它自称 token 经济件,但用它自己的 KPI 量:全历史 18 只次里 ♻️复用 7 / 🔄重研 **11** = **净多烧 11 个 Opus**,且 **0 个买单**(Hold 15/UW 3)。根因是立论错了:carryover 票按定义是**今日 L3 没选的**票,没有保席它们本就不在名单上(= 0 卡 = 0 token)——**保席从不省 token,只会 0 成本或 +1 Opus**;「救活复用率」是把分母做大让比率好看。且它在**系统性推翻 L3 的拒绝**,而拒绝恰是本机器唯一被证明有效的功能。收益侧 n=13<20 不足以裁决(超额 −1.91%、同日对照混杂),**裁决依据是 token 会计事实不是收益**。
- ③ **生产者落稿**:席位 / 催化 / 日历 / 卖方修正(consensus)—— 都先于 prompts。

### 派发三步

1. 落 `_l4_shared_instructions.md`(只放当日件)→ `l4_card prompts <date>` 落 `_harvest_list.txt`(`.SH`→`.SS`)+ 每卡一个 `_l4_prompt_<code>.md`(固定标头 → 共享块 → 逐卡简报;顺序被契约测试锁死 byte-identical,防 cache 前缀断裂)。**pinned 票**逐卡块带 📌保送标记 + 📌持仓管理要求行(卡片须含『持仓管理』节:D+1/D+2 卖出纪律+触发位,评级独立;07-12 W2)。
2. 预 harvest slim —— **二段式**:`_slim.md`(表面,P0–P3)+ `_slim_deep.md`(深核:盈利质量 / 偿付 / 利润表,仅 P4 读、早停卡永不读)。**合格判据 = 结构+内容**(`l4_card._slim_defect`:四道结构锚 ∧ OHLCV Close 真数值;体积只兜 <4KB 真垃圾)——2026-07-14 教训:旧 8KB 体积门槛把差 16 字节的完整 slim 误杀、毙掉整条流水线,且该门槛此前已因同类误杀从 10KB 降过一次,**规模检查与结构检查必须分开**。
3. **每股一个 `l4-stock` workflow**(fb_20260714_003):主会话一条消息 N 个 Workflow 并行,每股链内 intel→card→(≥OW)双复核;单股失败只废单股。行业 brief 补漏走 `subagent_type='sector-brief'`。

**⛔ 强制满卡**(`force_full_card`,2026-07-12 接线):逐卡块内插「禁止早停」指令,两条独立通路任一成立即触发 —— ① **📌 保送票**(`lane == "pinned"`)恒强制:你真金白银持有的票,「盈利质量」「偿付(爆雷)」**不允许**标『未核』;② **强先验**:`conviction ≥ 70` ∧(`n_channels ≥ 4` ∨ L2 配额救回)。**强制满卡只保证核得够深,不保证结论向好**(照样可以 UW/Sell),评级仍由 rubric 三门定。
> ⚠️ FN-1 史(第五例):本函数 2026-06-27 建成后**零生产调用点**(只有单测 + 从未勾选的 plan 复选框 T12),这道早停安全网**从没跑过** —— 07-10 实跑 11 张卡里 10 张早停,含 4 张持仓卡爆雷维「未核」。**新生产者必须 grep 调用链 + 真实命令冒烟。**

**活体情报**(与步骤 2 slim 预取并行派发):`l4-intel`(sonnet·max 盲搜六面)∥ slim 预取。**config `l4_intel.enabled` 2026-07-12 已开**(P1 波 07-10 实跑验收通过:38 agent / 0 error / 4 GATE 全绿)。首跑冒烟三查:① WebSearch 并发限频 ② 中文源可达率 ③ intel 空稿率。代价:每天多几百次网查、L4 段墙钟 ~14m → ~30m+;裁决走单变量 A/B、账本 ≥10–20 日。

### 渐进深度 + 早停

```
P0 简报（市场地形+档案+解禁/披露旗+行业备忘+误读预警）
  → P1–P3 表面填 4 维
  → 【主早停②】非买点 → 早停卡（短格式 ≤36 行:决策仪表盘 / 一段话研判 / L3 裁决表;未核维标「未核」）
  → survivor 读 deep 进 P4 陷阱核（质押/商誉/解禁/审计/现金流,记「进入P4倾向」）
  → ③ 击杀
  → P5 满卡
```

评级由 `rubric_rating` 派生;早停只向下;≥OW 必走完 P4+P5。

**2026-07-11 波(B1/B2/B4/B10 落地)**:
- **防污染**:简报的 L3 论点改**中性前提清单**(前提 N 逐条核真,前提 2=兑现机制),conviction 挪到"L3 元数据"行且注明"读完 P1 数字后再看";l4-card 铁律加"先读数据后读论点"(P1 盲读微 pass:先写 3 行独立初判再读前提)。
- **补基率**:逐卡块新增 🔁 基率行(`write_base_rates`:lane 翻案率 + 评级历史 T+2 胜率,n<10 ⚠禁注)+ 📐 目标价锚(`target_calib.json`:全市场 hi_2_oc p60≈+3.7%、+8% 目标历史触达仅 14%——目标超 p60 须写硬理由)。均在逐卡块,cache 前缀契约不破。
- **买单 ensemble(拍板 2,替代常设 skeptic)**:≥OW 新派卡各追加 2 独立 l4-card run(复核卡落 `ensemble/` 不进 details/),取中位、**只向下折回**;spread≥2 档 → 🎭 badge + 组合视角人裁行;`_ensemble.json` 缺 = parity。

**阶段效能**:早停率随 regime 波动大(20%~100%),弱市高早停是纪律不是失灵,错杀率 ≈10% 与满卡组持平;P4 翻盘率零积累。纪律实证:紫光国微三度被 CFO/FCF 门封顶 Hold —— **别为了凑买单放宽资金 / 估值门**。

---

## L5 · 整合 —— `scan/assemble.py`(确定性,零 LLM 铁律)

**summary.md 节序**(所有新节都 presence-gated):

```
self_review 硬门 banner → regime+drift 行(+🌡情绪温度行) → 📈市场研判 → 漏斗数量
→ 📈影子组合成绩单行(即"纸面法庭":真实 vs 影子[若门不拦最想买3只] vs 市场,hold=2 主尺)
→ 各阶段卡点&概览（+🍱菜单体检）→ 投资建议表(🎭复核分歧 badge) → 📅两周日历
→ 组合视角（买单同板块告警 + 🎭人裁行 + 仓位 overlay:risk_off 0–2 成 / range 3–5 / trend 5–8）
→ 经验浮出 → token 估算 → ⏳待裁决提案(open 清单,20 日节奏 nag) → 诚实局限
```

- **现场完备**:发布同时写 `run_health.json` + `index.md` 导航页(**第二天复盘从 index.md 进**);`weights_used.json` + meta.regime 固化,漏斗可复现。
- **观察单已退役**(fb_20260714_002,2026-07-14 用户裁定):prelude 日检步骤、summary 渲染节、watchlist_ledger 刷新全部摘除;`scan/watchlist.py` 模块已删除(死码清理·零生产调用),存量 `context/watchlist.csv` 保留(sector.pack 行业选择器仍直接读该 CSV 文件),不再有日检/触发/直通车。发布仍落 `reports/scan/<运行时刻>/`(数据日在 manifest.json,retro 据此定位)。

---

## 计量与跨层校准(usage_harvest 已实跑;OTEL 路已退役)

- **token 真计量(唯一正典)**:`python -m autoresearch.trace.usage_harvest --session <sessionId> --out reports/scan/<run>/token_usage.md`。逐 subagent 真 usage:**按 `message.id` 去重**(流式会让同一条 usage 重复落行,实测不去重把 cache_read 从 4.81M 虚报成 9.83M)、按**计价倍率加权**(cache读 ×0.1 / 5m写 ×1.25 / 1h写 ×2)+ **按模型汇总**(加权口径不含模型价差,壳降 haiku 只在这一维看得出来)。补账用 `--transcripts <glob>`(计量代码晚于某次 run 落地时)。
  - **覆盖声明**:只覆盖 subagent,**主会话自身不在内**;表里没有的不等于没花钱。
  - **2026-07-24 追溯首读**:50 agent · billed 22.4M / **加权 5.49M** / 输出 716.6k · cache 命中 85.6%。同一份报告的旧「落盘字节÷2.8」估算写 ~183.6k = **低估 30 倍**,且分布相反(L3 真占 7.8% 而非 37%;大头是主会话编排 27% / l4-card 23% / intel 20% / gp 壳 14.5%)。**按旧估算去砍会砍错地方** —— 报告侧估算列已随此发现退役。
- **OTEL 遥测**:`trace/telemetry.py` 自 2026-07-05 建成起零生产调用点、全仓无一个 `token_telemetry.md`,已于 2026-07-27 **删除**。不要再配那五件 env(照旧文档跑会直接 ModuleNotFoundError)。
- **跨层校准**:`python -m autoresearch.learning.cross_calib` → `reports/learning/cross_calib.md`。① L3→L4 翻案率 per lane(高确信 = conviction≥70、翻案 = L4≤UW);② rubric 门柱级拦对 / 错杀(错杀 = ex5>0 且 hi_10 触达目标)。
- **触价校准**:`target_calibration` 统计全卡目标触达率 → `buy_ledger.md` 新节;首证 = 东方财富 hi10 6.3% vs 目标 28.8%(过乐观)。
- **注入分层(铁律)**:python 只产读数;prelude 打三条当日件建议行(📐 / 🔁 / 🚪),n<10 的 thin 行标「禁注」勿贴。**校准不改门 / 权重 / 评级。**

---

## 闭环层 —— `autoresearch/learning`(确定性度量 + Claude 诊断)

| 件 | 现状 |
|---|---|
| `retro` | 归因 → 诊断 → 权重重标定(可回滚)→ 建议 → 经验;归因桶细分 `l3_bench`/`pass1_cut`+漏检两行读数(bench top-5 vs finalists / pass1_cut 赢家数);attribution 优先 join `_final_ratings.json`(终评级)与 process_score 列(均 presence-gated);retro_input 未读 → prelude nag |
| `prelude` 账本白名单 | 每日自动刷:attribution + journal/buy_ledger/cross_calib/catalyst/paper_nav + **07-12 增 channel/gate/zero_buy/changelog 四账本**(watchlist_ledger 已随观察单退役);失败不阻断 |
| `stage_eval` | 逐段 edge 量尺;现状读数:L2 −1.1%;L3 真选无正 alpha(07-12 −0.39pp/2日、07-16 去保送污染后 −3.8%);L4 评级 rank-IC +0.55 分档单调 —— edge 在「拒绝」侧 |
| `channel_ledger` | 边际 alpha → quota 提议;momentum unique +9.2% |
| `zero_buy_ledger` | 0买日 vs 有买日对照;买单口径 = attribution `bought` **单一事实源**(07-12 与 journal 统一,run_health 逐日核一致性) |
| `temperature` | S1 情绪温度计五序列+五相位;回填 124 日,展示先行(菜单/预算联动=下一波) |
| `target_calib` | hi_2_oc 分位校准 json(📐 目标锚);全市场 p60≈+3.7%,+8% 目标触达仅 14% |
| `shrink` / `shrink_replay` | **基率收缩原语**(P0-3):四消费点(🔁基率/翻案率/📐细分格/tail_rate)注入收缩值 p̂=(n·p+k·p_g)/(n+k),n<3 仍禁注;`learning` 配置回滚杆;留一日回放 CLI 首读=翻案率 shrunk 优5.5%/左尾 raw 微优→默认开续攒 |
| `process_score` / `process_backfill` | **过程分机检**(P0-4):逐卡 6 项确定性 checklist → `process_scores.csv` + attribution 列——0 买日也有日 n=10-30 过程标签;历史回填 355 卡,分布 {1:27,2:158,3:134,4:20,5:16} |
| `lesson_yield` | **教训证伪器**(P0-5):逐条带 guard 教训的反事实 Δpp 累计 + MTM 计数;命中 n≥20 且累计 Δ≤0 自动提名 retire(只提名人批) |
| `feedback_store` | lessons(regime 域 + MTM,cap=8)/ proposals / changelog / 权重回滚;`ls_reversal_regime_low_composite_trust` ×4 |
| `gate_ledger` | 门 MTM 拦对率;**2026-07-11 OW 三门建账**(assemble 逐满卡解析失守→gate_fires binding 行;gate_status 容错空格+取最后可解析段,补记 12 行漏记)+ `tail_rate` 左尾 ≤−5% KPI(拍板 3:门=避雷器);首读:三门 mean_ex2 为正但 tail_rate 36-46% |
| `watchlist_ledger` | 已退役(fb_20260714_002);模块 2026-07-17 删除(宿主没了,量的是不再产生的触发行) |
| `t1_review` | **快环**(2026-07-17,fb_20260717_001):T 报告真选票 vs T+1 收盘(保送不算/只相邻交易日;判定尺 v2=行业中性超额÷截面稳健σ 的 z,双门+分诊+一字板剔除);确定性层 CLI + `t1-review.js` workflow(**2 agent:合诊**——一个 context 通读全部真选卡对比诊断,禁网查禁编造,用户裁定勿每票 fan-out——**+ 综合官**;model/effort 由 scan_config `agents.t1_diag/t1_synth` 管控,经 `python -m autoresearch.scan.user_config` 随 args.cfg 传入);prelude `t1_pending` 催办;账本 `context/learning/t1_review.jsonl`。**自我迭代腿**:candidates.json(稳定 key)→ 候选账本 → **次日 L3 表自动注入 🔄 校准块**(`prepare_l3_table`,数据非指令)→ 同 key ≥2 T 日自动立案(人批)→ 批后经 `render_calibration_block` 注入(pr_20260716_005 同日接线) |
| `changelog_ledger.heartbeat` | **自动腿心跳探针**(2026-07-17,pr_20260716_001):连续 3 次重标定 sha 不变 → 🚨 进 prelude 汇总屏;同波 `recalibrate_and_log` 前置 `factor_lab.extend_plan()` 增量续面板(F 推进到 last−2 对齐 fwd_2_oc;勿重跑 harvest——会按 form_span 重造小面板冲掉历史累积) |
| `scan/dossier.py` | **前科卡**(跨日入围史)注入 L4,强制"变化项"节;紫光国微 4 次入围。**与下面的覆盖档案是两回事**,并存不互替 |
| `dossier/*`(覆盖档案链) | **常备覆盖模型**(Wave2/3,spec `2026-07-22-research-depth-dossier-design.md`):`coverage_pool.json` 池(prelude 日检:进=pinned/20日真选≥2、退=20日未选、cap30 LRU(按 last_selected))→ `context/knowledge/dossiers/<code>.md` 八节档案(`dossier-init` workflow 首覆;`builder` 确定性骨架 + `prefetch` 三腿湖零接触)→ L4 prompt 注入「📚 覆盖档案摘要」(`schema.injectable_summary` **四门 = 注入器与卡 lint 的单一事实源**)+ intel prompt **内嵌**已知底(内嵌代替授权,情报员无 Read=结构性盲回工具级)→ 卡写「**档案对账**」节(`self_review` 分档探针:有覆盖档案查它、仅前科卡查"变化项")→ assemble 尾 `delta.record_scan_deltas` 按**终评级**(`_final_ratings.json`,非卡面)回写 §8 + 刷新 §2/§3/§4/§6/§7 与摘要机算行 → 季度对账 `python -m autoresearch.dossier.reconcile <period>`(express 优先/forecast 兜底/**未披露也落痕**;prelude 📐 提醒 + 🕰️ 90 日陈旧告警)。**全链 presence-gated:无档案 = Wave2 前行为逐字节不变** |
| `factor_lab` | harvest → calibrate(_regimes)→ eval;107 成型日 |
| `research/replay` | **漏斗历史回放器**(2026-07-12,design `2026-07-12-funnel-replay-l35-removal-design.md` Part B):逐日调**生产真身** `universe.run(outdir=…)` 重放 L0→L1→L2 + `retro.attribute` 归因 + 温度相位 → R1 相位×fwd / R2 通道×相位 / R3 赢家验尸。**把裁决样本从"每月 20 日"换成"一次 250–500 日"**,且覆盖前向窗从未出现的修复/发酵/高潮相位。**PIT 六条**(权重 PIT 最隐蔽:weights.json 由含未来收益的 retro 校准而来 → 默认 `weights=prior` 零泄漏);M1 可信度门已过(见下)。CLI:`replay m1/run/attr/report` |
| `consensus` | 一致预期前向积累(限频 1 次/小时);<60 日不入线上 IC 门 |
| `journal` | 扫描日记;11 日已回填,9/11 为 0 买日 |
| `changelog_ledger` | 重标定前后 composite IC 对比 + **trial 计数与 DSR-lite 两行**(多重检验提醒 + C18 红灯);07-12 复活首读:6 版、样本足 3 次 Δ 均值 +0.0614、**最新一次 Δ≤0 红灯亮**(该出「recalibrate 仅 regime 切换时触发」提案而非继续调) |
| `buy_ledger` | 买后管理 → 评级基率(n≥10);6 笔历史 OW |
| `sector_memo` | 行业事实月度蒸馏;空(待 ≥20 scan 日) |
| `scan/health.py` | run_health + index.md 导航 + **账本新鲜度行**(复盘欠账日数/账本 mtime 滞后/买单口径一致 ✓✗;07-12 P0-1);churn 16% / 早停率 20% |
| `scan/calendar.py` | 解禁 + 披露日历;216 披露 + 1 大解禁 |
| 影子漏斗 | universe 变体 L2 免费 A/B;积累中 |
| `paper_nav` | 真实 / 影子 / 市场 三线 NAV;回填起 06-18;**+sized 双轨**(分数Kelly×vol目标×流动性cap,纯纸面,缺数据回退等权;07-12 W1) |
| `shadow_buys` | conviction top-3 纸面记账(三门证伪法庭);~45 笔;等权+sized 双轨 |
| `catalyst_ledger` | 催化旗 fwd_5 对照(n≥30);零积累 |

---

## 数据层要点

- **源**:tushare 默认(push2 被网络封锁;`TUSHARE_TOKEN` 高权限);keyless 可达的有 —— 同花顺一致预期(L4 fwd-PE)/ 腾讯 / datacenter-web。
- **限频**:`report_rc` 1 次/小时。
- 🚨 **数据契约**(`autoresearch/data/contracts.py`,2026-07-12 用户裁定"取数后要全面校验,为空抛异常阻断";design `2026-07-12-data-contracts-design.md`):
  - **A 级(地基:daily/daily_basic/moneyflow/cyq_perf/stk_factor_pro/stock_basic/trade_cal)** —— 空 / 行数腰斩 / 缺列 → `DataContractError` **阻断整条流程**,且**拒绝入湖**(脏数据一旦落盘就被钉死,重跑也自愈不了)。校验挂在**三条路径**上(取数后写湖前 / **湖命中** / 未结算日只查空不查列)+ **因子帧出口门**(`check_market_frame`)+ `_harvest_vol_series` 失败即抛。**`DataContractError` 不得被任何 `except Exception` 吞掉**(已修 frame/temperature)。
  - **B 级(增强:北向/两融/龙虎榜/公告/质押/新闻/宏观)** —— 缺失只降级,但**必须记账**(`degradations()` → `degraded.json` → 报告一行)。不走 cache 的降级点用 `record_degradation()`(如 `_fetch_hk_hold`)。
  - **为什么分级**:真实的空是合法的(无北向额度的日子 hk_hold 就是空),presence-gated 降级是设计。真正的病是**降级不留痕**——`composite_score` 对整组 NaN 的因子会把它从分母剔除、放大其余组权重,**打分照样输出 0–100、漏斗照样跑完、退出码 0**(2026-07-12 实证:volprice 组静默丢失 → 全市场失真 98.8%、L2 jaccard 0.36)。系统有降级能力,曾经没有「我降级了」的传达能力。
  - 湖体检:`python -m autoresearch.data.contracts doctor [--purge]`(A 级毒源必清;B 级空帧不删——多为真实的空)。
- **降级**:缺权限的 B 级端点自动降级为 NaN、打分重新归一(**且记账**,见上)。盘中跑 retro 时,当日 EOD 未发布 → fwd 降级 NaN(不抛异常)。
- 🚨 **入湖一律全字段**(`cache._lake_params`,2026-07-12 事故后加):`_cache_key` **不含 `fields`** → 湖里一个 key 只有一个 parquet,带窄 `fields` 的查询若成为某 key 的**首个写入者**,就把窄表钉成该日快照,后来要别的列的调用方只会读到缺列的表 —— 而这类失败大多被上游 try/except **静默吞成降级**。真实事故:`temperature.rollup` 用 `fields='ts_code,pct_chg'` 回填 07-09/07-10 的 daily(那两天在扫描当天尚未结算、按"date>=today 拉新但不写"故未入湖)→ 钉成两列窄表 → `_harvest_vol_series` 拿不到 high/low/amount → **volprice 组整组 NaN → 全市场 composite 失真 98.8%、L2 名单 jaccard 0.36**,而这两天正落在下次扫描的近 20 日窗口里(生产本会静默中招,唯一信号是一行淹没的 warn)。**教训:静默降级 + 共享缓存键 = 数据毒化**;要窄列自己 `df[cols]`,多几列无害,少一列是灾难。同族坑见记忆 `scan-cache-emptypickle-and-0702`。

---

## 已被实证否决的方向(勿重启;关键数字在此,附录级明细在 git 历史)

- **L2 上模型**(附录 D):全 zoo 负 IC + 回测无稳健 alpha;新特征(盈利修正等)IC 过硬之前不复活。
- **业绩预告做 L1 事件通道**(附录 E):两季对照,强制披露季 T+5 超额 −0.27% / 胜率 35%,追缺口 −2.92% —— 公告后追买无肉;alpha 若有,在披露前的预期变化里。

---

## 开放线头(诚实局限)

1. regime 块 horizon 之争(`pr_20260702_001`)待 T+5 数据裁决;risk_off 块样本薄(11 日)。
2. **多数 LLM 流程段还没在真实 skill 跑动中实测**(行业 brief 同链对比 / 观察单补 conds / 档案"变化项" / 经验人判 MTM / P4 倾向行 / 复用后编排 / L3 误读旗 / L4 slim 二段式与短格式早停卡):确定性件全测试全绿,LLM 段只是脚手架就位;早停抽检、卡模板 v2 未实跑;MTM / gate_fires / 触发 ledger / 影子对照 / P4 翻盘率样本仍薄,别过度反应。
3. attribution 孤儿:06-19 端午假日键是非交易日,fwd 永远无法结算,保持 "—"。Δ 表省幅随日况;卡片复用省幅 = churn;评级基率 n<10 禁注。
4. reversal_confirm 上线但与旧 reversal 的 A/B 未裁决(channel_eval 按 lane 计量,≥10 日再切/留);healthy 通道 alpha / 捕获增量也待 `pre_healthy` 影子反事实 + retro 裁决;哨兵档未实跑;token 真实计费只有 `/usage` 或 OTEL 落稿可见。
5. consensus 首拉待限频窗,积累 <60 日前盈利修正不入线上;anns_d 无接口权限 → 公告情感列空,监管旗(news_reg)现**仅扫 L3_news(anns_d 公告)**、**无 webnews 回退**(该回退随 producer 已于 2026-07-13 移除,`L3_webnews` 目录从未有生产写入者)——anns_d 退役后监管旗同样恒空,`anns_empty_rate`=1.0 即该态(Wave4 Task1:index.md/L3 表头现会对此渲染显式标注,不再静默);northbound hk_ratio NaN=100% 时空转,已默认停用。
6. ~~attribution 终评级缺口~~ **已修(07-12 P0-2)**:assemble 发布落 `_final_ratings.json`(两个 fold 循环后的终评级),retro 优先 join、缺文件回退卡面(presence-gated);仍欠=首次真实折回日人工核对一次。
7. **2026-07-12 三波全部未实跑**:L3 两遍法+finalist tier(pass1 影子/bench 账本/守卫)、L4 情报站(config 默认关,启用即换 sonnet·max 盲搜)、自学习 P0 仪器(新鲜度行/过程分/收缩注入/lesson_yield/C18 红灯)——**下次真扫描=三波联合验收**,清单=`.superpowers/sdd/final-review-l3-merge.md`+`final-review-l4-intel.md`+各设计稿;07-07/08 复盘欠账由 nag 浮出;自学习 P0 波欠一轮正式终审(速审模式)。
8. **2026-07-11 P0+P1 波新开线头**:温度计菜单/预算联动待相位判定质量复审(下一波);L3 pf 指纹/lint 打回/L4 中性前提/盲读/基率行/📐锚/ensemble 全部**未实跑**(确定性件测试绿,LLM 段脚手架就位,下次真扫描=正式验收);capfloor20 影子/新配额(value250/heat150/main_fund150)攒 channel_ledger 前向读数 ≥10 日再复盘;三门账本/tail_rate 攒 ≥20 日才裁雷分级(P2);07-09 冒烟发现 reversal_confirm/healthy 当日 0 召回(数据条件性,非接线故障——起爆硬门无人过/健康谓词依赖 cmf 列,留意后续真跑读数)。
8. 仅供研究,非投资建议。

# scan-market 各阶段现状(as-of 2026-07-11)

> 本文件只记**当前态快照**。沿革见 git log 与 `docs/specs/` 下各设计稿;**冲突时以源码为准**。
> 文档分工:`SKILL.md` 讲**怎么跑**;操作模板分驻各能力 skill —— 市场研判在 `macro-playbook.md` 末节,L4 决策卡在 `stock-research` 的 `lite-playbook.md`。

---

## 一、漏斗全景

主链六段,从全市场一路收窄到一份报告:

```
L0 选集  →  L1 召回  →  L2 粗排  →  L3 精排  →  L4 研究  →  L5 整合
全A ~5500    top1000    top200     ~15–30 只   决策卡×N    1 份报告
（确定性）  （确定性）  （确定性）  （Opus×1）  （Opus×N）  （确定性）
```

**两条旁路**(不在主链上,并行算好后喂进主链):

- **市场研判** = macro-research 的 lite 档。Stage 0 与 L0 并行,一个 Opus 产出 `market_view.md`,L3 / L4 / L5 三处复用。
- **行业 brief** = sector-research 的 lite 档。L2 之后按行业并发,喂 L3 / L4 / L5。

**主链之外的三个确定性动作:**

- L1 旁支:影子漏斗产 2–3 个变体(免费 A/B,喂 retro 做对照)。
- L4 派发前:观察单触发直通车(🚄 触发票直达 L4)+ 卡片 TTL 复用(♻️ 没变化的 Hold 票不重派)。
- 事后闭环:retro 归因 → 自动重标定权重 + 人工批准的建议/经验 → 注回 L1 权重与 L3 校准块。

**三层角色分工:**

- **确定性层**(L0 / L1 / L2 / L5 + 全部度量):零 LLM,纯 pandas,不编数。
- **AI 判断层**(L3 / L4 / 策略师):全部是 Opus subagent,只回传紧凑结果。
- **闭环层**(`autoresearch/learning`):用已兑现的涨跌批改前两层。

---

## 二、核心世界观(实证结论,决定功夫花在哪)

- **确定性层没有 alpha。** L2 全部 zoo 模型 OOS rank-IC 为负;4 年回测 composite-top200 收益 ≈ 0(依赖 regime,2025–26 反转段 −24bps)。→ 所以 L2 不做预测,只做"菜单"(多样性采样)。
- **判断层有 edge。** L3 净 IC **+0.144**,L4 评级单调 IC **+0.075**。
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

**已注册 11 路通道**(启用哪些由 `scan_config.jsonc` 的 `funnel.recall_channels` 决定;当前默认启用 9 路):

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

**配额覆盖已接线生效(2026-07-11)**:`scan_config.jsonc` 的 `funnel.channel_quotas` 当前生效 **value 250 / heat 150 / main_fund 150**(channel_ledger advisory 档,拍板 5);兜底读取在 `universe.run` 本体(`_funnel_overlay`,FN-1 第三修——prelude/`universe.main` 直调路径同样生效,显式参数/CLI flag 恒优先,缺文件=注册表默认 parity)。影子变体(`pre_healthy`/`capfloor20`)同口径透传,反事实不受配额差异污染。

**rz 入组(2026-07-11,pr_20260710_001 resolved)**:融资买入强度 `rz_buy_intensity` 独立第 10 因子组(自然朝向+,权重 calibrate@fwd_2_oc 重校,changelog 可回滚);语义=情绪接力资金代理(文献 A9),非基本面确认。**capfloor20 第 4 影子变体**上线(cap_floor_yi=20,验 pr_20260624_001 市值地板漏判)。

**regime-aware(推荐常开):**

- `--regime-aware` 会按当日 regime 取 `weights.json` 里 `regimes[trend|range|risk_off]` 对应的权重块,缺块回退 flat。
- regime 判定(`common/regime.py`):breadth ≥ 0.55 且 pct_60d>0 → trend;breadth ≤ 0.30 且 pct_60d<0 → risk_off;其余 → range。
- 当前块(107 个成型日):trend 43 / range 53 / risk_off 11;momentum IC 在 trend −0.055、range +0.015。

**已知局限:**

- risk_off 样本薄(仅 11 日);horizon 之争未决(`pr_20260702_001`,T+1 与 fwd_5 待数据裁决)。
- 影子漏斗:`--no-shadow` 不加时产 3 个变体(`nostrat` / `nocap` / `pre_healthy`)落 staging,由 retro 对照捕获,累计 ≥10 日才提 proposal。

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
  - 各 stage(`strategist` / `sector_brief` / `l3_rank` / `l4_card` / `redteam`)的 agent model / effort **优先级:scan_config > workflow 内建 > agent def frontmatter 默认**;缺配置或缺键 = workflow 内建现值(parity)。

---

## 旁路 · 行业 brief = sector-research lite 档

L2 之后、与 L3 证据取数**并发**:

- `sector.reuse <date> --apply`(TTL ≤ 5 日 ♻️ 复用;已复用的行业从 fan-out 里排除,不再被重派覆盖)
- → 剩余的 `sector.pack <date>`(红榜 top3 ∪ L2 集中度 top3 ∪ 观察单行业,K ≤ 6)
- → 每个行业派一个 `Agent(subagent_type='sector-brief')`,写两段契约 brief:`## 地形段`(喂 L3/L4)、`## 研判段`(仅 L5,含 `**行业方向**` 这一 keyed 行)。
- L4 派发前,对 ≥2 只同行业 finalist 的行业补漏。

**消费与价值:**

- `l3_table_md(sector_terrain=True)` 固定只渲染 L2 top200 覆盖的行业(`top200_only`,约 110 行压到 30–50 行);assemble 自动嵌 🏭 行业研判 + 🔗 同链对比(presence-gated);发布时 `sector_ledger.record_calls` 记方向(MTM,n<10 时 ⚠ 只记账)。
- 价值 = 同链论点摊销 + 行业相对估值锚。**不解决 0 买,也不设门。**

---

## L3 · 精排 —— holistic 单 Opus(200 → ~30)

一个 Opus 通看全表、比较着选,而不是孤立逐只打分(比较式 > 逐只)。

**流程:**

1. `harvest_l3_evidence`(龙虎榜 / 预告 / 快报)+ `harvest_l3_news`(公告情感)补证据;
2. `l3_table_md` 压成紧凑表;
3. 一个 Opus-high 通看全表,按 5 维 rubric(channel 共振 / 资金 / 基本面 / 情感 / 脆弱)比较着选 ~30;
4. 写 `L3_judged_full.csv` → `merge_l3_finalists_v2`(趋势配额安全网)→ `finalists.csv`。
5. 校准注入:因子方向经验校准块 + 策略师地形段 + 行业备忘录块。

**token 经济与预算:**

- `delta=True` 略去无变化的票。
- L4 派发数由 `menu.l4_budget` 控(五面旗:落刀>60% / 相对落刀>40% 且 >2× 全市场 / 健康涨≤2 / risk_off / 0买连败≥3;命中 1 旗 → 22,≥2 旗 → 15)。

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

**稳定性与验尸:**

- 周频抽检:`shuffle_seed` 乱序再跑 audit agent,overlap<0.70 → 提 proposal。
- 错杀验尸(retro 侧):L2-keep 且非 finalist 且 T+5 赢家,join 红队理由写 lesson。实证错杀 = 0 —— **病在召回线,别冤枉判断层**。

### L3.5 · 可插拔闸(finalists → L4 收窄到 6~10)

在 `scan/gates.py` 的 GATE2 之后、L4 派发之前,把 finalists 收窄。**默认 passthrough(= 不收窄 = parity)。**

- **选策略**:`scan_config.jsonc` 的 `l4_gate:{name, params}`。三个已注册策略(`scan/l35_gate.py` 的 `@gate`):`passthrough` / `topk_simple` / `conviction_floor_quota`。
- **豁免直通**:lane ∈ {pinned, carryover, watchlist} 的票恒直通、不占配额。
- **预算收编**:`l4_budget` 作为上限(setdefault)并进策略。
- **影子账本**:被闸掉的票落 `_l35_cut.csv` → retro 补它们的 fwd_2 → L5 出「🚪 L3.5 闸影子」行(picked 均值 vs cut 均值 = 闸的日常体检)。
- **回测迭代**:`python -m autoresearch.research.gate_backtest --gate <name> [--params-json ...]` 重放历史(L3_judged × fwd_2_oc,出入选收益 + 落选赢家错杀审计),攒够数据再调参。
- **当前裁决 = 保 passthrough 不切**(2026-07-11 的 13 日回测):conviction floor 55–65 都比 passthrough 更差,只有 floor=70 跑赢、但每天只出 ~3 只;结论是只有 conviction≥70 的极高确信在 T+2 有正 edge,中间 band 是噪声 / 反预测(确信度是为 swing 校准的残留)。

---

## L4 · 研究 —— 一只票 = 一个 Opus subagent(渐进深度 + 早停)

### 派发前四道确定性闸(按序,生产者都跑在 prompts 之前)

- ⓪ **批量质押旗**:`l4_card pledge <date>` → `pledge.csv`(质押>40 标爆雷、>20 偏高;advisory,不动门)。
- ① **观察单触发直通车**:触发的票补进 finalists。
- ② **卡片 TTL 复用 + 滞回**:`l4_reuse <date> --apply --carryover`。近 4 日已出卡、评级 ≤Hold、|Δ价|≤5%、无新公告、regime 未翻、conviction<70 → 直接 ♻️ 复用,不派 subagent(OW 三门失守 ≥2 的深否决豁免 conviction 拦截;≥OW 永不复用;复用率约 20%)。
- ③ **生产者落稿**:席位 / 催化 / 日历 / 卖方修正(consensus)—— 都先于 prompts。

### 派发三步

1. 落 `_l4_shared_instructions.md`(只放当日件)→ `l4_card prompts <date>` 落 `_harvest_list.txt`(`.SH`→`.SS`)+ 每卡一个 `_l4_prompt_<code>.md`(固定标头 → 共享块 → 逐卡简报;顺序被契约测试锁死 byte-identical,防 cache 前缀断裂)。
2. 预 harvest slim —— **二段式**:`_slim.md`(表面,P0–P3,**>8KB 才可信**)+ `_slim_deep.md`(深核:盈利质量 / 偿付 / 利润表,仅 P4 读、早停卡永不读)。
3. 全部 `Agent(subagent_type='l4-card')` **一条消息并发**(别分 wave);行业 brief 走 `subagent_type='sector-brief'`。

**活体情报**(与步骤 2 slim 预取并行派发):`l4-intel`(sonnet·max 盲搜六面)∥ slim 预取,config `l4_intel.enabled` 默认关。

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
→ 各阶段卡点&概览（+🍱菜单体检）→ 投资建议表(🎭复核分歧 badge) → 👀观察单日检 → 📅两周日历
→ 组合视角（买单同板块告警 + 🎭人裁行 + 仓位 overlay:risk_off 0–2 成 / range 3–5 / trend 5–8）
→ 经验浮出 → token 估算 → ⏳待裁决提案(open 清单,20 日节奏 nag) → 诚实局限
```

- **现场完备**:发布同时写 `run_health.json` + `index.md` 导航页(**第二天复盘从 index.md 进**);`weights_used.json` + meta.regime 固化,漏斗可复现。
- **观察单**(`scan/watchlist.py`):`context/watchlist.csv` 存跨日活状态;词表 v2 = close_above / close_below / ma_bull / money_pos / by_date + manual;`run_check` 判触发 / 提醒(k/n)/ 临近 / 待触发 / 失效。**触发 ≠ 自动升级**,提示按 lite 档复核。发布落 `reports/scan/<运行时刻>/`(数据日在 manifest.json,retro 据此定位)。

---

## 计量与跨层校准(报表就绪、样本积累中;OTEL 未实跑)

- **OTEL 遥测**:落稿估算下界 ~75k vs 真实量级 ~1M(主因 L4 输入未计)。带 env 启动会话:
  `CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_METRICS_EXPORTER=console OTEL_LOGS_EXPORTER=console OTEL_METRIC_EXPORT_INTERVAL=30000 OTEL_LOG_TOOL_DETAILS=1`;
  解析 `python -m autoresearch.trace.telemetry <raw> --out reports/scan/<run>/token_telemetry.md` → agent×type 表 + cache 命中率。判读:l4-card 行 cacheRead≈0 → 前缀契约已锁死仍 miss,要查 TTL 窗口;显著>0 → 命中率可信落账。
- **跨层校准**:`python -m autoresearch.learning.cross_calib` → `reports/learning/cross_calib.md`。① L3→L4 翻案率 per lane(高确信 = conviction≥70、翻案 = L4≤UW);② rubric 门柱级拦对 / 错杀(错杀 = ex5>0 且 hi_10 触达目标)。
- **触价校准**:`target_calibration` 统计全卡目标触达率 → `buy_ledger.md` 新节;首证 = 东方财富 hi10 6.3% vs 目标 28.8%(过乐观)。
- **注入分层(铁律)**:python 只产读数;prelude 打三条当日件建议行(📐 / 🔁 / 🚪),n<10 的 thin 行标「禁注」勿贴。**校准不改门 / 权重 / 评级。**

---

## 闭环层 —— `autoresearch/learning`(确定性度量 + Claude 诊断)

| 件 | 现状 |
|---|---|
| `retro` | 归因 → 诊断 → 权重重标定(可回滚)→ 建议 → 经验;根因已坐实,后续按 fwd_5 自动补跑 |
| `stage_eval` | 逐段 edge:L2 −1.1%、L3 +0.144、L4 +0.075 |
| `channel_ledger` | 边际 alpha → quota 提议;momentum unique +9.2% |
| `zero_buy_ledger` | 0买日 vs 有买日对照;**2026-07-11 修 attribution `bought` 列落盘+幂等回填**(此前 3 个真实买单日被记 0 买,汇总待重读) |
| `temperature` | S1 情绪温度计五序列+五相位;回填 124 日,展示先行(菜单/预算联动=下一波) |
| `target_calib` | hi_2_oc 分位校准 json(📐 目标锚);全市场 p60≈+3.7%,+8% 目标触达仅 14% |
| `feedback_store` | lessons(regime 域 + MTM,cap=8)/ proposals / changelog / 权重回滚;`ls_reversal_regime_low_composite_trust` ×4 |
| `gate_ledger` | 门 MTM 拦对率;**2026-07-11 OW 三门建账**(assemble 逐满卡解析失守→gate_fires binding 行;gate_status 容错空格+取最后可解析段,补记 12 行漏记)+ `tail_rate` 左尾 ≤−5% KPI(拍板 3:门=避雷器);首读:三门 mean_ex2 为正但 tail_rate 36-46% |
| `watchlist_ledger` | 观察单触发 → 后市度量;待首样本 |
| `scan/dossier.py` | 个股档案注入 L4,强制"变化项"节;紫光国微 4 次入围 |
| `factor_lab` | harvest → calibrate(_regimes)→ eval;107 成型日 |
| `consensus` | 一致预期前向积累(限频 1 次/小时);<60 日不入线上 IC 门 |
| `journal` | 扫描日记;11 日已回填,9/11 为 0 买日 |
| `changelog_ledger` | 重标定前后 composite IC 对比;4 条入账 |
| `buy_ledger` | 买后管理 → 评级基率(n≥10);6 笔历史 OW |
| `sector_memo` | 行业事实月度蒸馏;空(待 ≥20 scan 日) |
| `scan/health.py` | run_health + index.md 导航;churn 16% / 早停率 20% |
| `scan/calendar.py` | 解禁 + 披露日历;216 披露 + 1 大解禁 |
| 影子漏斗 | universe 变体 L2 免费 A/B;积累中 |
| `paper_nav` | 真实 / 影子 / 市场 三线 NAV;回填起 06-18 |
| `shadow_buys` | conviction top-3 记账;历史回填 ~30 行 |
| `catalyst_ledger` | 催化旗 fwd_5 对照(n≥30);零积累 |

---

## 数据层要点

- **源**:tushare 默认(push2 被网络封锁;`TUSHARE_TOKEN` 高权限);keyless 可达的有 —— 同花顺一致预期(L4 fwd-PE)/ 腾讯 / datacenter-web。
- **限频**:`report_rc` 1 次/小时。
- **降级**:缺权限的端点自动降级为 NaN、打分重新归一。盘中跑 retro 时,当日 EOD 未发布 → fwd 降级 NaN(不抛异常)。

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
5. consensus 首拉待限频窗,积累 <60 日前盈利修正不入线上;anns_d 无接口权限 → 公告情感列空、监管旗走 L3_webnews 回退,`anns_empty_rate`=1.0 即该态;northbound hk_ratio NaN=100% 时空转,已默认停用。
6. **attribution 终评级缺口(终审 I-2,下波首件)**:retro 的 rating 取 staging 卡面,verify/ensemble 折回只改 summary → 被折回的 OW 会以卡面 OW 进 attribution 再污染 bought/评级基率(胜宏 06-30 实证)。修法已定:assemble 落 `_final_ratings.json`,retro 优先 join、缺文件回退卡面(presence-gated);**ensemble 首次真折回前必须完成**。
7. **2026-07-11 P0+P1 波新开线头**:温度计菜单/预算联动待相位判定质量复审(下一波);L3 pf 指纹/lint 打回/L4 中性前提/盲读/基率行/📐锚/ensemble 全部**未实跑**(确定性件测试绿,LLM 段脚手架就位,下次真扫描=正式验收);capfloor20 影子/新配额(value250/heat150/main_fund150)攒 channel_ledger 前向读数 ≥10 日再复盘;三门账本/tail_rate 攒 ≥20 日才裁雷分级(P2);07-09 冒烟发现 reversal_confirm/healthy 当日 0 召回(数据条件性,非接线故障——起爆硬门无人过/健康谓词依赖 cmf 列,留意后续真跑读数)。
8. 仅供研究,非投资建议。

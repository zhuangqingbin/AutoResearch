# scan-market 各阶段现状(as-of 2026-07-08)

> 沿革见 git log(docs/specs/ 各 wave 设计稿);本文件只保留当前态快照,冲突以源码为准。
> 分工:`SKILL.md`=怎么跑;操作模板分驻能力skill(市场研判=macro-playbook末节/L4卡=stock-research lite-playbook)。

## 漏斗一图(含旁路与闭环)

```
L0 选集 ──→ L1 召回 ──→ L2 粗排 ──┬→ L3 精排 ──→ L4 研究 ──→ L5 整合
全A~5500     top1000      top200    │  ~15–30 只   卡(复用+新)  1 份报告
(确定性)    (确定性)   (确定性)   │  (Opus×1)   (Opus×N)   (确定性)
                        └影子变体×2 └─★ 宏观 lite·市场研判旁路(=macro-research lite 档;Stage 0 可并行,Opus×1,三处复用)
L4 派发前(确定性):🚄 观察单触发直通车(触发票直达 L4)→ ♻️ 卡片 TTL 复用(无变化 Hold 票不派)
闭环(事后):retro 归因 → 权重重标定(自动)+ 建议/经验(人批)→ 注回 L1 权重与 L3 校准块
```

**角色分工三层**:确定性层(L0/L1/L2/L5 + 全部度量,零 LLM、纯 pandas、不编数)/ AI 判断层(L3/L4/策略师,全 Opus subagent、只回传紧凑结果)/ 闭环层(`autoresearch/learning`,用已实现涨跌批改前两层)。

## 核心世界观(实证,决定功夫花在哪)

- **确定性层无 alpha**:L2 全 zoo OOS rank-IC 全负;4 年回测 composite-top200 ≈ 0(regime 依赖,2025-26 反转段 −24bps)→ L2 不预测,只做菜单。
- **判断层有 edge**:L3 净 IC **+0.144**、L4 评级单调 IC **+0.075**。
- **0 买根因在召回线**:413 只 T+1 赢家 **91% 在打分池、仅 4.8% 过 top1000 线**,composite IC **−0.11**——修法 = regime 分桶权重(见 L1)。
- **0 买对照**:历史 0 买日市场 fwd_1 −0.48% / fwd_5 −0.60% → **空仓方向正确**;若转正 = 失明预警。

---

## L0 · 选集(`autoresearch.scan.universe`,确定性)

- 全A(~5,500)+硬门:剔ST/退市/停牌/次新+市值地板(默认**30亿**,`--cap-floor`);北交所默认**纳入**(`--exclude-bj`剔)。**哲学**:只剔"确定不可交易/不可研究"的,**每加一条硬门就是一块永久盲区**。
- **已知局限**:missed_l0≈赢家的9%(小盘/次新/北交所为主);软化地板proposal open未批。

## L1 · 召回(`autoresearch.scan.recall`,确定性,→1000)

10 路策略 channel"过门 + 按信号排序 + 截 top-quota",`quota_union` 合并(floor 保底多样性)+ provenance;默认 `--recall-mode multi`。

| channel | quota/floor | 信号 |
|---|---|---|
| composite | 400/100 | IC 校准复合分 |
| momentum | 250/50 | 趋势龙头 |
| reversal | 200/50 | 困境反转 |
| value | 200/50 | 行业内低估 |
| main_fund | 200/50 | 主力净流入 |
| heat | 200/50 | 成交额量级(捞巨额龙头) |
| growth | 150/40 | 成长加速 |
| northbound | 120/30 | 北向持股 |
| accumulation | 120/30 | 底部吸筹 |
| **healthy** | 150/40 | 质量上涨(0<pct60<40∧主力+∧cmf+;修复旧 composite 把该品相排到 4000+ 名外的空洞) |

- **regime-aware(推荐常开)**:`--regime-aware` 按当日 regime 取 `weights.json` 的 `regimes[trend|range|risk_off]` 块,缺块回退 flat。判定(`common/regime.py`):breadth≥0.55∧pct_60d>0→trend;≤0.30∧<0→risk_off;否则range。当前块(107成型日):trend43/range53/risk_off11;momentum IC trend−0.055 vs range+0.015。
- **已知局限**:risk_off样本薄(11日);horizon之争未决(`pr_20260702_001`,T+1 vs fwd_5待裁决)。影子漏斗:`--no-shadow`关时产3变体(`nostrat`/`nocap`/`pre_healthy`)落staging,retro对照捕获,≥10日累计才提proposal。

## L2 · 粗排(`recall/l2_stratify.select_l2`,确定性分层采样,→200)

**确定性分层多样性采样器,ML-free**。①sector-neutral composite排merit核与桶内;②7风格桶固定floor(趋势20/健康15/反转12/价值12/成长12/吸筹12/主力10);③sector cap≤20%。产物`L2_gbdt_top200.csv`:`l2_rank`=选择序、`gbdt_score`=composite、`l2_lane_reserved`=floor救回。**不预测**(分层免费,strat≈composite-top200≈0)。

- **菜单体检**(`scan/menu.py`):行业集中度/落刀面/健康上涨/估值,自动嵌L5;健康=0打**⚠️菜单病**。实证:落刀L2 70% vs市场32%。
- **哨兵建议**(`menu.sentinel_advice`):全市场健康占比:<3%建议哨兵档(跳L3+L4,省~70% token);3–5%仅consider(**2026-07-08放宽**:删掉原risk_off升级档,3–5%不再auto-skip、regime只进文案);≥5%全扫。**人拍板不自动**(workflow仅对`sentinel`自动跳)。floor自然实验(retro侧):救回组vs merit组vs被挤组fwd对照,持续弱才复审。

## 旁路 · 市场研判 = macro-research lite 档(Stage 0 与 universe 并行,回退 L2 后;Opus×1)

模板在`macro-research/macro-playbook.md`末节;`market_pack_from_frame`(湖派生)不依赖L2;`macro-brief` leaf agent产出六小节market_view.md(前3描述性地形喂L3/L4、后2仅L5)。

- **机制**:确定性`market_pack(scan_dir)`(regime/宽度/估值分散/资金/红黑榜,只读`L1_scored_full`)→Opus subagent写`market_view.md`。三处复用:L3地形段、L4 `market_context_block`、L5置顶。
- **防锚定不变量**:喂L3/L4只能是**描述性地形**,不是方向指令;操作建议只进L5;**个股评级只由本股rubric三门决定**。缺文件→L5回退确定性脉搏。
- **配置装载链**(Plan A3):`scan_config.json`(白名单加载见`autoresearch/scan/user_config.py`)经`frame --json`校验回显进`market_pack`/`user_config_echo.json`,由调用方随 Workflow `args.config` 传入`scan-market.js`(脚本本身无文件系统访问,不能自己读文件);各 stage(`strategist/sector_brief/l3_rank/l4_card/redteam`)的 agent model/effort 优先级:**scan_config > workflow 内建 > agent def frontmatter 默认**,缺配置/缺键=workflow 内建现值(parity)。

## 旁路 · 行业 brief(sector-research lite)

L2后与L3证据取数**并发**——`sector.reuse <date> --apply`(TTL≤5日♻️复用;**已复用行业从fan-out排除**,不再被重派覆盖)→剩余`sector.pack <date>`(红榜top3∪L2集中度top3∪观察单行业,K≤6)→每行业一个`Agent(subagent_type='sector-brief')`写两段契约brief(`## 地形段`喂L3/L4、`## 研判段`仅L5,含`**行业方向**`keyed行);L4派发前对≥2只同行业finalist补漏。

- **消费**:`l3_table_md(sector_terrain=True)`固定只渲染L2 top200覆盖行业(`top200_only`,≈110→30-50行);assemble自动嵌🏭行业研判+🔗同链对比(presence-gated);发布时`sector_ledger.record_calls`记方向(MTM,n<10⚠只记账)。**价值**:同链论点摊销+行业相对估值锚。**不解决0买、不设门**。

## L3 · 精排(holistic 单 Opus,200→~30)

`harvest_l3_evidence`(龙虎榜/预告/快报)+`harvest_l3_news`(公告情感)补证据→`l3_table_md`压紧凑表→**一个Opus-high通看全表、比较着选~30**(5维rubric:channel共振/资金/基本面/情感/脆弱)→`L3_judged_full.csv`→`merge_l3_finalists_v2`(趋势配额安全网)→`finalists.csv`。校准注入:因子方向经验校准块+策略师地形段+行业备忘录块。**比较式>孤立逐只打分**。

- **token经济与预算**:`delta=True`略去无变化票。**L4预算**`menu.l4_budget`(五旗:落刀>60%/相对落刀>40%且>2×全市场/健康涨≤2/risk_off/0买连败≥3→权重1旗=22、≥2=15)控派发数。
- **推荐常开三旗**(presence-gated,默认关=parity):**主力失真**`dist_flag=True`(反号/微量;命中18/30被L4辟谣);**监管**`reg_flag=True`(近10日立案/问询/处罚等,未实跑);**误读三预警**`misread_flag=True`(`misread`列:低基〔np_yoy>100∧roe<8〕/背离〔cmf_20或obv_mom_20正但main_net_ratio<0〕/套牢〔winner_rate<25∧ma_bull=0∧pct_60d>0=反弹撞套牢盘〕,谓词=`scoring.l3_misread_flags`;L4简报同步注旗;l3-rank硬约束E强制自证;回放命中12/20)。
- **周频稳定性抽检**:`shuffle_seed` 乱序再跑 audit agent,overlap<0.70 → proposal。**错杀验尸**(retro侧):L2-keep∧非finalist∧T+5赢家 join 红队理由写 lesson;实证:错杀=0——**病在召回线,别冤枉判断层**。
- **L3.5 可插拔闸(finalists→L4 收窄到 6~10;`scan/gates.py` GATE2 后;默认 passthrough=parity)**:`scan_config.json` 的 `l4_gate:{name,params}` 选策略(`passthrough`/`topk_simple`/`conviction_floor_quota`,`scan/l35_gate.py` @gate 注册);**exempt=lane∈{pinned/carryover/watchlist} 恒直通不占配额**;预算旗 `l4_budget` 收编为上限(setdefault);cut 落 `_l35_cut.csv` → retro 补 fwd_2 → L5「🚪L3.5 闸影子」行(picked vs cut 均值=闸日常体检)。**回测迭代**:`python -m autoresearch.research.gate_backtest --gate <name> [--params-json ...]` 重放历史 L3_judged×fwd_2_oc(入选收益+落选赢家错杀审计),数据累积后调参。**当前裁决=保 passthrough 不切**(2026-07-11 13日回测:conviction floor 55-65 比 passthrough 更差、唯 floor=70 跑赢但仅~3只/日;只有 conviction≥70 极高确信在 T+2 有正 edge,中间band 噪声/反预测=确信度为 swing 校准的残留)。

## L4 · 研究(一只 = 一个 Opus subagent,渐进深度 + 早停)

- **派发前四道确定性闸(按序,生产者先于 prompts)**:⓪ 批量质押旗(`l4_card pledge <date>` → `pledge.csv`,>40 爆雷/>20 偏高,advisory 不动门)→① 观察单触发直通车(触发票补进 finalists)→② `l4_reuse <date> --apply --carryover` 卡片 TTL 复用+滞回(近4日已出卡/≤Hold/|Δ价|≤5%/无新公告/regime未翻/conviction<70→直接复用,♻️不派subagent;OW三门失守≥2深否决豁免conviction拦截;≥OW永不复用;复用率约20%)→③ 席位/催化/日历/**卖方修正(consensus)**生产者先行落稿。
- **派发三步**:① 落 `_l4_shared_instructions.md`(只放当日件)→ `l4_card prompts <date>` 落 `_harvest_list.txt`(`.SH`→`.SS`)+ 每卡 `_l4_prompt_<code>.md`(固定标头→共享块→逐卡简报,顺序契约测试锁死 byte-identical,防 cache 前缀断裂);② 预 harvest slim——**二段式**:`_slim.md`(表面,P0–P3,**>8KB 才可信**)+ `_slim_deep.md`(深核:盈利质量/偿付/利润表,仅P4读,早停卡永不读);③ 全部 `Agent(subagent_type='l4-card')` 一条消息并发(别分wave)。行业brief走`subagent_type='sector-brief'`。
- **渐进深度**:P0简报(市场地形+档案+解禁/披露旗+行业备忘+误读预警)→P1–P3表面填4维→**主早停②**(非买点→早停卡,**短格式**≤36行:决策仪表盘/一段话研判/L3裁决表,未核维标「未核」)→survivor读deep进P4陷阱核(质押/商誉/解禁/审计/现金流,记`进入P4倾向`)→③击杀→P5满卡。评级由`rubric_rating`派生;早停只向下;≥OW必走P4+P5。
- **阶段效能**:早停率随regime波动大(20%~100%),弱市高早停是纪律非失灵,错杀率≈10%与满卡组持平;P4翻盘率零积累。**纪律实证**:紫光国微三度被CFO/FCF门封顶Hold——**别放宽资金/估值门凑买单**。

## L5 · 整合(`scan/assemble.py`,确定性,零 LLM 铁律)

**summary.md 节序**:self_review 硬门banner→regime+drift行→**📈市场研判**→漏斗数量→各阶段卡点&概览(+🍱菜单体检)→投资建议表→**👀观察单日检**→**📅两周日历**→组合视角(买单同板块告警+仓位overlay:risk_off 0–2成/range 3–5/trend 5–8)→经验浮出→token估算→诚实局限。所有新节**presence-gated**。

- **现场完备**:发布同时写 `run_health.json`+`index.md` 导航页——**第二天复盘从 index.md 进**;`weights_used.json`+meta.regime 固化,漏斗可复现。
- **观察单**(`scan/watchlist.py`):`context/watchlist.csv` 跨日活状态;词表v2 `close_above/close_below/ma_bull/money_pos/by_date+manual`;`run_check` 判触发/提醒(k/n)/临近/待触发/失效。**触发≠自动升级**,提示按 lite 档复核。发布:`reports/scan/<运行时刻>/`(数据日在manifest.json,retro据此定位)。

## 真实计量与跨层校准(报表就绪,样本积累中;OTEL 未实跑)

- **OTEL 遥测**:落稿估算下界~75k vs真实量级~1M(主因L4输入未计)。带env启动会话:`CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_METRICS_EXPORTER=console OTEL_LOGS_EXPORTER=console OTEL_METRIC_EXPORT_INTERVAL=30000 OTEL_LOG_TOOL_DETAILS=1`;解析 `python -m autoresearch.trace.telemetry <raw> --out reports/scan/<run>/token_telemetry.md` → agent×type表+cache命中率。判读:l4-card行cacheRead≈0→前缀契约已锁死仍miss需查TTL窗口;显著>0→命中率可信落账。
- **跨层校准**:`python -m autoresearch.learning.cross_calib` → `reports/learning/cross_calib.md`:①L3→L4翻案率per lane(高确信=conviction≥70、翻案=L4≤UW);②rubric门柱级拦对/错杀(错杀=ex5>0且hi_10触达目标)。
- **触价校准**:`target_calibration` 统计全卡10日目标触达率 → `buy_ledger.md`新节;首证=东方财富hi10 6.3% vs目标28.8%过乐观。
- **注入分层(铁律)**:python只产读数;prelude打三条当日件建议行(📐/🔁/🚪),n<10 thin行「禁注」勿贴。**校准不改门/权重/评级**。

## 闭环层(`autoresearch/learning`,确定性度量 + Claude 诊断)

| 件 | 现状 |
|---|---|
| `retro` | 归因→诊断→权重重标定(可回滚)→建议→经验;根因已坐实,后续按 fwd_5 自动补跑 |
| `stage_eval` | 逐段 edge:L2 −1.1%、L3 +0.144、L4 +0.075 |
| `channel_ledger` | 边际 alpha → quota 提议;momentum unique +9.2% |
| `zero_buy_ledger` | 0买日vs有买日对照;7日fwd_5−0.60%=空仓正确 |
| `feedback_store` | lessons(regime域+MTM,cap=8)/proposals/changelog/权重回滚;`ls_reversal_regime_low_composite_trust`×4 |
| `gate_ledger` | 门MTM拦对率→松阈/退役建议;积累中 |
| `watchlist_ledger` | 观察单触发→后市度量;待首样本 |
| `scan/dossier.py` | 个股档案注入L4,强制"变化项"节;紫光国微4次入围 |
| `factor_lab` | harvest→calibrate(_regimes)→eval;107成型日 |
| `consensus` | 一致预期前向积累(限频1次/小时);0日,≥60日过IC门 |
| `journal` | 扫描日记;11日已回填,9/11为0买日 |
| `changelog_ledger` | 重标定前后composite IC对比;4条入账 |
| `buy_ledger` | 买后管理→评级基率(n≥10);6笔历史OW |
| `sector_memo` | 行业事实月度蒸馏;空(待≥20scan日) |
| `scan/health.py` | run_health+index.md导航;churn16%/早停率20% |
| `scan/calendar.py` | 解禁+披露日历;216披露+1大解禁 |
| 影子漏斗 | universe变体L2免费A/B;积累中 |
| `paper_nav` | 真实/影子/市场三线NAV;回填起06-18 |
| `shadow_buys` | conviction top-3记账;历史回填~30行 |
| `catalyst_ledger` | 催化旗fwd_5对照(n≥30);零积累 |

## 数据层要点

tushare默认源(push2被网络封锁;`TUSHARE_TOKEN`高权限);keyless可达:同花顺一致预期(L4 fwd-PE)/腾讯/datacenter-web。限频:`report_rc` 1次/小时。缺权限端点自动降级NaN、打分重归一。**盘中跑retro**:当日EOD未发布→fwd降级NaN不抛。

## 已被实证否决的方向(勿重启;关键数字已录本节,附录级明细在 git 历史)

- **L2上模型**(附录D):全zoo负IC+回测无稳健alpha;新特征(盈利修正等)IC过硬前不复活。
- **业绩预告L1事件通道**(附录E):两季对照,强制披露季T+5超额−0.27%/胜率35%,追缺口−2.92%——公告后追买无肉;alpha若有,在披露前的预期变化。

## 开放线头(诚实局限)

1. regime 块 horizon 之争(`pr_20260702_001`)待 T+5 数据裁决;risk_off 块样本薄(11日)。
2. **多数 LLM 流程段**(行业brief同链对比/观察单补conds/档案"变化项"/经验人判MTM/P4倾向行/复用后编排/L3误读旗/L4 slim二段式与短格式早停卡)**未在真实skill跑动中实测**;确定性件全测试全绿,LLM段是脚手架就位;早停抽检/卡模板v2未实跑;MTM/gate_fires/触发ledger/影子对照/P4翻盘率样本仍薄,别过度反应。
3. attribution 孤儿:06-19 端午假日键非交易日,fwd 永远无法结算,保持 "—"。Δ表省幅随日况;卡片复用省幅=churn;评级基率 n<10 禁注。
4. healthy 通道已上线但 alpha/捕获增量未验(由 `pre_healthy` 影子反事实+retro 裁决,≥10日);哨兵档未实跑;token 真实计费仍只有 `/usage` 或 OTEL 落稿可见。
5. consensus 首拉待限频窗,积累 <60 日前盈利修正不入线上;anns_d 无接口权限:公告情感列空、监管旗走 L3_webnews 回退,`anns_empty_rate`=1.0 即该态;northbound hk_ratio NaN=100% 空转,quota 待 proposal。
6. 仅供研究,非投资建议。

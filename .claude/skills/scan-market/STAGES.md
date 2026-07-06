# scan-market 各阶段现状(as-of 2026-07-03)

> **本文定位**:漏斗每一段"现在是什么"的**现状快照**——引擎/输入输出/机制/默认参数/实证读数/已知局限。
> 与其余文档的分工:`SKILL.md` = 怎么跑(编排入口);操作模板分驻能力 skill(市场研判=macro-playbook 末节 / L4 卡=stock-research lite-playbook;screening-playbook 已退役 07-03,历史在 git)。
> 冲突时**以源码为准**,本文标注日期,过期就重写。

## 漏斗一图(含旁路与闭环)

```
L0 选集 ──→ L1 召回 ──→ L2 粗排 ──┬→ L3 精排 ──→ L4 研究 ──→ 买单skeptic ──→ L5 整合
全A~5500     top1000      top200    │  ~15–30 只   卡(复用+新)  ≥OW 0–4 只      1 份报告
(确定性)    (确定性)   (确定性)   │  (Opus×1)  (Opus×N)  (Opus×N独立)  (确定性)
                        └影子变体×2 └─★ 宏观 lite·市场研判旁路(=macro-research lite 档;Stage 0 可并行,Opus×1,三处复用)
L4 派发前(确定性):🚄 观察单触发直通车(触发票直达 L4)→ ♻️ 卡片 TTL 复用(无变化 Hold 票不派)
0 买日追加:机会成本红队(top-Hold ×2,Opus 独立 bull 方,产出只进观察单)
闭环(事后):retro 归因 → 权重重标定(自动)+ 建议/经验(人批)→ 注回 L1 权重与 L3 校准块
```

**角色分工三层**:确定性层(L0/L1/L2/L5 + 全部度量,零 LLM、纯 pandas、不编数)/ AI 判断层(L3/L4/skeptic/策略师,全 Opus subagent、只回传紧凑结果)/ 闭环层(`autoresearch/learning`,用已实现涨跌批改前两层)。

## 核心世界观(实证,决定功夫花在哪)

- **确定性层无 alpha**:L2 全 zoo(20 模型×3 horizon)OOS rank-IC 全负;4 年回测 composite-top200 ≈ 0 且 regime 依赖(2025-26 反转段 −24bps)。→ L2 不预测,只做菜单。
- **判断层有 edge**(06-24 复盘):L3 净 IC **+0.144**、L4 评级单调 IC **+0.075**(Sell −5.3% < Hold −1.2%);买单 skeptic 曾把胜宏从 OW 压回 Hold(事后正确)。
- **0 买的根因在召回线**(06-24 定量):413 只 T+1 赢家 **91% 在打分池、仅 4.8% 过 top1000 线**(劣于随机 18%),当日 composite IC **−0.11**。修法 = regime 分桶权重(已上线,见 L1)。
- **0 买对照**(zero_buy_ledger,7 个 0 买日):市场 fwd_1 −0.48% / fwd_5 −0.60% → **空仓方向正确**;若持续为正 = 失明预警。

---

## L0 · 选集(`autoresearch.scan.universe`,确定性)

- **作用**:全 A(~5,500)+ 硬门:剔 ST/退市/停牌/次新 + 市值地板(默认 **30 亿**,`--cap-floor`);北交所默认**纳入**(`--exclude-bj` 剔)。leaf 轻门(可选):`--l0-min-amount/--l0-min-list-days`。
- **哲学**:只剔"确定不可交易/不可研究"的;**每加一条硬门就是一块永久盲区**。
- **已知局限**:missed_l0 ≈ 赢家的 9%(06-24:37/413,多为小盘/次新/北交所);`pr_20260624_001`(软化地板)open 未批。

## L1 · 召回(`autoresearch.scan.recall`,确定性,→1000)

- **机制**:10 路策略 channel 各自"过门 + 按信号排序 + 截 top-quota",`quota_union` 合并(floor 保底多样性)+ provenance(`recall_channels`/`n_channels` 随行);默认 `--recall-mode multi`,`composite` 为对拍口径。

| channel | quota/floor | 信号 |
|---|---|---|
| composite | 400/100 | IC 校准复合分(全样本或 regime 块) |
| momentum | 250/50 | 趋势龙头(lens 过门) |
| reversal | 200/50 | 困境反转 |
| value | 200/50 | 行业内低估 |
| main_fund | 200/50 | 主力净流入 |
| heat | 200/50 | 成交额量级主轴(捞巨额龙头,免疫 froth 惩罚) |
| growth | 150/40 | 成长加速 |
| northbound | 120/30 | 北向持股 |
| accumulation | 120/30 | 底部吸筹(投机高召回,交下游证伪) |
| **healthy**(07-03) | 150/40 | **质量上涨**(0<pct60<40∧主力+∧cmf+,=菜单体检谓词;按主力×cmf 共振排序。07-02 取证:261 只该品相 0 进池、通道 top10 被旧 composite 排在 4095–4201 名——结构性空洞修复) |

- **composite 权重**:`weights.json`(factor_lab T+1 IC 校准 + 申万层级收缩 k=200);**regime-aware(2026-07-02 起推荐常开)**:`--regime-aware` → `classify_regime(当日帧)` 选 `regimes[trend|range|risk_off]` 权重块;未知 regime/缺块**自动回退 flat**(parity 锚,代码默认关)。
  - regime 判定(`common/regime.py`):breadth(站上MA60占比)≥0.55 ∧ 中位 pct_60d>0 → **trend**;≤0.30 ∧ <0 → **risk_off**;否则 **range**;空帧安全退 range。
  - 当前块(107 成型日,T+1 口径):**trend 43 日 / range 53 日 / risk_off 11 日**。关键符号:momentum IC **trend −0.055 vs range +0.015**(单一 flat 权重结构性不可行);risk_off 下 **value −0.033(抄便宜=接刀)、RSI −0.067(超跌反弹最强)、主力流入 −0.020(反向指标)**。
- **已知局限**:① risk_off 块仅 11 成型日(样本薄,收缩兜底,随积累增厚);② **horizon 之争未决**(`pr_20260702_001`):现行 T+1 块 vs 06-27 实证 fwd_5 口径 regime 信号更强(trend momentum −0.094)——用 retro T+5 盲区数据裁决,勿拍脑袋切;③ T+5 口径下漏斗更盲(06-24:swing 赢家 550,missed_l1 448,有研硅 fwd_5 +87% 被压 rank 3172)。
- **影子漏斗(07-02 新,免费 A/B)**:universe 默认(`--no-shadow` 关)产 3 套变体 L2 只落 staging(`nostrat` 纯 composite 序 / `nocap` 无行业上限 / **`pre_healthy` 旧 9 路反事实**〔07-03,healthy 通道的捕获增量直接可测〕),retro `shadow_compare` 逐变体对照赢家捕获 → 从验尸变成**常态前向实验**;≥10 日累计才提 proposal。

## L2 · 粗排(`recall/l2_stratify.select_l2`,确定性分层采样,→200)

- **是什么**:**确定性分层多样性采样器,ML-free**(旧 GBDT/champion 已弃用,`models/` 只留 measure-only)。三件事:① sector-neutral composite(composite − 申万一级组均值)排 merit 核与桶内;② 7 风格桶**固定 floor** 保底(趋势20〔momentum+heat〕/**健康15〔healthy,07-03〕**/反转12/价值12/成长12/吸筹12/主力10;northbound/composite 不单列桶);③ sector cap ≤20%(`--l2-sector-cap`)。产物 `L2_gbdt_top200.csv`(列名历史遗留):`l2_rank`=选择序、`gbdt_score`=composite(显示)、`l2_lane_reserved`=floor 救回标记。
- **为什么不预测**:见"核心世界观";分层实测免费(strat ≈ composite-top200 ≈ 0)→ 多样性零 alpha 代价。
- **菜单体检(07-02 新,`scan/menu.py`)**:L2 vs 全市场的行业集中度/落刀面/健康上涨(0<pct60<40∧主力+∧cmf+)/估值/floor 救回数,自动嵌 L5;健康=0 打 **⚠️菜单病** 预警。实证(06-30):**落刀 L2 70% vs 市场 32%、健康 3/200 vs 242/4184**——召回错配的当天即时读数。
- **哨兵建议(07-03 新,`menu.sentinel_advice`)**:判据=**全市场**健康上涨占比 × regime(healthy 桶上线后 L2 健康数被 floor"治愈",不能再当判据):<3% 材料枯竭 → 建议哨兵档(只跑观察单/日历/红队×2〔market_view 已由 Stage 0 宏观 lite 产出〕,跳 L3+L4,省 ~70% token 与 ~35 分钟);3–5%+risk_off → 建议;≥5% → 全扫。**人拍板不自动**;07-02(6.2%,range)→ full = healthy 通道修好后该日应全扫。哨兵日 retro/影子照算 = 错过率可监控。
- **floor 自然实验(07-02 新,retro 侧)**:救回组 vs merit 组 vs 被挤掉组的 fwd 对照,持续弱才复审 floor(数据从 06-27 分层器上线后的日子开始积累)。

## 旁路 · 市场研判 = macro-research lite 档(07-03 起;推荐 Stage 0 与 universe 并行,回退 L2 后;Opus×1)

> 07-03 海拔重构:人格/prompt 模板已迁 `macro-research/macro-playbook.md` 末节;帧入口
> `market_pack_from_frame`(`scan/frame.py` 湖派生)使其不再依赖 L2 完成——本节其余描述为 L2 后回退口径。

- **机制**:确定性 `market_pack(scan_dir)`(`scan/market.py`:regime/宽度/估值分散/资金/板块红黑榜,只读 `L1_scored_full`——全市场真宽度,不用 recall 子集)→ 一个 Opus subagent 以资深投资大师口吻写 `market_view.md`(staging)。**一次产出三处复用**:L3 prompt 前置地形段、L4 每卡简报注入本股板块地形(`market_context_block`)、L5 置顶嵌入 + 确定性漏斗读数尾注。
- **防锚定不变量(易违反,务必守)**:喂 L3/L4 的只能是**描述性地形**(数字),不是方向指令;操作建议/漏斗读数只进 L5;**个股评级只由本股 rubric 三门决定,大盘看空不压个股、看多不松门**。缺 `market_view.md` → L5 回退确定性脉搏(parity 不破)。

## 旁路 · 行业 brief(sector-research lite,07-03 新)

- **机制**:L2 后与 L3 证据取数**并发**——`sector.reuse <date> --apply`(TTL≤5日:regime 同 + 行业中位60日动量位移≤3pp,♻️banner)→ 剩余行业 `sector.pack <date>`(选择 = 红榜top3∪L2集中度top3∪观察单行业,K≤6;pack 全部聚合 staging 既有产物,零新端点)→ 每行业一个 subagent 写 `sector_briefs/<行业>.md`(**两段契约**:`## 地形段` 喂 L3 表头/L4 简报〔无 brief 回退 sector_memo 行〕、`## 研判段` 仅 L5,含 `**行业方向**` keyed 行);L4 派发前对 ≥2 只同行业 finalist 的未覆盖链**补漏**。
- **消费**:`l3_table_md(sector_terrain=True)` 全行业确定性地形行(对称覆盖,防"有 brief 行业被系统性高看");assemble 自动嵌 🏭 行业研判节 + 🔗 同链对比表(presence-gated);发布时 `sector_ledger.record_calls` 自动记方向(MTM,已成熟 n<10 ⚠只记账)。
- **价值主张**:同链论点摊销(同链 3 张卡不再各自重建行业论点)+ 行业相对估值锚 + L5 择链上最佳表达。**明确不解决 0 买、不设门**(行业弱 ≠ 该行业的票不研究——每加一条硬门就是一块永久盲区)。

## L3 · 精排(holistic 单 Opus,200→~30)

- **机制**:`harvest_l3_evidence`(龙虎榜/预告/快报,近 10 交易日)+ `harvest_l3_news`(anns_d 公告情感)补真证据 → `l3_table_md` 压一张紧凑表(因子+证据+情感+召回 provenance)→ **一个 Opus-high 通看全表、比较着选 ~30**(5 维 rubric:channel 共振/资金/基本面/情感/脆弱)→ `L3_judged_full.csv`(每只 thesis/risk/catalyst/conviction/fragility/triage_lean/lane/sentiment)→ `merge_l3_finalists_v2`(趋势配额安全网:trend lane 保底,一半按 conviction 一半按 pct_60d)→ `finalists.csv`。
- **校准注入**:『因子方向经验校准』块(`feedback_store.render_calibration_block`:近期反馈+经验+IC 基线)+ 策略师地形段 + **行业备忘录块**(`sector_memo.render_memo_block`,月度蒸馏的行业级事实)。**比较式 > 孤立逐只打分**。
- **token 经济(07-02 新)**:① 紧凑表建议 `delta=True`(略去"昨判弃且无变化"票 + prev_l3 标记 + 防锚定令;无前日回退全量;全量表每 ≤5 scan 日 1 次;实测省幅随日况——L2 轮换大的日子略 0 也无成本);② **L4 预算** `menu.l4_budget`(**五旗,07-04**:落刀>60% / **相对落刀**>40%且>2×全市场〔07-03 病灶:45% vs 20%,绝对门抓不住〕/ 健康涨≤2 / risk_off / **0买连败≥3**〔≥5 计重旗;源=`menu.zero_buy_streak`,details 卡口径含 verify 折回〕→ 权重 1 旗=22、≥2=15,只降不升)传 `merge_l3_finalists_v2(target=…)`;②b **主力失真旗** `l3_table_md(dist_flag=True)` 推荐常开(加 `main_inflow_yi`+`main_dist`〔反号/微量〕列+图例禁则,谓词=`scoring.main_net_distortion_label` 单一事实源;07-03 实证精确命中 18/30 被 L4 逐卡辟谣的失真票;默认关=parity);②c **监管旗** `l3_table_md(reg_flag=True)` 推荐常开(07-05 新,未实跑:近10日公告命中 立案/问询/关注函/处罚/违规/诉讼/监管/证监会/交易所 → `news_reg` 列+禁则〔旗票论点必须显式回应监管事项〕;`l3_news.reg_hits` 独立检测器——**不动 `_EVENT_TAGS`/`news_digest`,情感列口径不变**〔digest key 集合被契约测试冻结〕;默认关=parity);③ **周频稳定性抽检**:`shuffle_seed` 乱序表再跑 audit agent,`stability_overlap<0.70` → proposal(L3 噪声大)。
- **错杀验尸(07-02 新,retro 侧)**:L2-keep ∧ 非 finalist ∧ T+5 赢家 → join 当时的红队理由(risk 文本),共性 = L3 系统性偏见候选 → 写 lesson 注回校准块。实证(06-24):错杀 = 0——赢家根本没进 L2,**病在召回线,别冤枉判断层**。

## L4 · 研究(一只 = 一个 Opus subagent,渐进深度 + 早停)

- **派发前三道确定性闸(07-02 新;07-04/07-05 扩)**:⓪(07-05 新,finalists 定稿后)**🛡 质押预旗** `python -m autoresearch.scan.agents.l4_card pledge <date>`——finalists 批量 `pledge_stat` → `pledge.csv`(近 7 日跨 scan 日复用),简报自动注 ⚠质押旗(阈值=`scoring.pledge_flag_label` 单一事实源,>40 爆雷/>20 偏高,与深核 slim 质押段同);**advisory 不动门**,取证 ≥2 周后升门另走 proposal(最小侵入点已探明=构造 `gates` dict 强制置 False);① **🚄 触发直通车** `watchlist.append_express`——观察单触发票不在 finalists 则追加(lane=watchlist_trigger),防"触发了但不在菜单 → 没人研究";② **♻️ 卡片 TTL 复用 + 滞回** `python -m autoresearch.scan.l4_reuse <date> --apply --carryover`——`--carryover` 先滞回保席(昨日 finalist∩今日 L2 的 ≤Hold 换血票追加 lane=carryover,cap 5;07-03 churn 90%〔repeat 3/30〕把复用架空只救 2 张的修法);复用门:近 4 日已出卡、≤Hold、|Δ价|≤5%、无新公告、regime 未翻、conviction<70 → 直接复用前卡(♻️banner+失效条件),**不派 subagent**;**前卡 OW三门失守≥2=深否决 → 豁免 conviction 拦截**(07-03:conv 82/80/78 全被 L4 翻案,失真先验不值得重烧)。≥OW 永不复用、禁链式。实测 07-01:5/25 可复用(20%)。
- **机制(07-04 派发三步)**:① 共享指令落 `_l4_shared_instructions.md` → `python -m autoresearch.scan.agents.l4_card prompts <date>` 确定性落稿:`_harvest_list.txt`(**yfinance 归一后缀,`.SH` 绝迹**;`normalize_symbol` 同步支持 `.SH→.SS`——07-03 上交所空 slim 双跑 10/30 的修法)+ 每卡 `_l4_prompt_<code>.md`(共享块前置吃 cache 前缀,简报自动带 ⚠主力失真标注);② 按清单批量预 harvest slim(零 LLM、验数据完好,**>10KB 才可信**);③ **全部 `Agent(subagent_type='l4-card')` 一条消息并发**(别分 wave;07-05 起 lite 流程/卡模板/契约烤进 `.claude/agents/l4-card.md`,每只 prompt 两行=指向 `_l4_prompt_<code>.md` 任务包;`_l4_shared_instructions.md` 只放当日件),落稿即派发稿 → token 表输入侧可计。skeptic/红队同理走 `subagent_type='buy-skeptic'`(模式 A=空方证伪/模式 B=红队,自动落 `_v_*`);行业 brief 走 `subagent_type='sector-brief'`。每只:P0 简报定向(`compose_funnel_brief`,自动前置市场地形 + 📁 档案 + **⚠️解禁旗/📅披露日**〔calendar.csv〕+ **🏭 行业备忘录行**)→ P1–P3 表面填 4 维 → **主早停②**(非买点 → 早停卡)→ survivor P4 陷阱核(质押/商誉/解禁/审计/现金流;**进 P4 前记一行 `进入P4倾向: <Rating>`**,供 P4 翻盘率计量)→ ③击杀 → P5 满卡。**评级由 `rubric_rating` 评分卡派生**(防 gestalt 过度多报);早停只向下;≥OW 必走 P4+P5。
- **阶段效能读数**(`health.l4_phase_stats`):早停率实测仅 20%(5/25,07-01)= 早停省得有限,复用+预算才是大头;P4 翻盘率零积累中(持续≈0 → P4 可条件化,先测量后动刀)。
- **纪律实证**:紫光国微三度被 CFO/FCF"业绩真兑现"门封顶 Hold;胜宏满卡过三门后被 skeptic 降级(见下)。**别放宽资金/估值门凑买单**(06-25 的学费)。

## 买单 skeptic(≥OW,独立 Opus)+ 机会成本红队(0 买日,07-02 新)

- **skeptic**:每只 ≥OW 派一个没参与过该票分析的独立 Opus 专职证伪(共用攻击面:估值/解禁质押/主力背离/业绩雷/前视/派发),主线 PM 三透镜(估值/资金/毁灭风险)投票 → `verify.csv`;assemble **折回评级**(降级=降一档、否决=至少 Hold)。与 self_review 机械硬门叠加且正交。**评级基率先验(07-02 新)**:buy_ledger 某评级已实现 n≥10 后,把"本系统 OW 历史 T+5 胜率 X%"注入 skeptic/PM prompt(n<10 禁注,薄先验比没有更坏);**流程 lint**:买单>0 而 verify.csv 空 = self_review **fail**(最后防线不可静默跳过)。
- **机会成本红队(对称性修复:空仓也要红队)**:verify 折回后**今日 0 买**才跑;`pick_opportunity_candidates`(conviction 最高的 Hold top-2)每只派独立 Opus **bull 方**攻"压评级的那道 binding gate",PM 三透镜裁判。**产出只进观察单(结构化 conds,source=opp_redteam)与校准数据,评级一个字不动**——这是"门是否太紧"的证据流,不是翻案通道。
- **skeptic 落定后**:`watchlist.ingest_verify` 把降级条目草拟进观察单 + 编排层补结构化 conds。

## L5 · 整合(`scan/assemble.py`,确定性,零 LLM 铁律)

- **summary.md 节序(当前)**:self_review 硬门 banner(fail 顶置;含**编排 lint**:买单未过 skeptic=fail、finalists≥5 无策略师=warn)→ regime+drift 行 → **📈 市场研判**(market_view 或回退脉搏 + 📉 漏斗读数)→ 漏斗数量 → 各阶段卡点&概览(+ **🍱 菜单体检**)→ 投资建议表(逐阶段结论 + 🛡️ 红队徽标)→ 红队明细 → **👀 观察单日检** → **📅 两周日历**(finalists 披露日+大解禁)→ 组合视角(**买单同板块=1 个 bet 告警** + **仓位 overlay**:regime 档位 risk_off 0–2 成/range 3–5/trend 5–8、菜单病取下沿、0 买=空仓一致)→ 经验浮出 → token 估算 → 诚实局限。所有新节 **presence-gated**(staging 缺 → 不加节,parity 不破)。
- **现场完备(07-02 新)**:发布同时写 `run_health.json`(staging+trace;NaN 降级/产物在位/churn/L4 阶段效能/买单数)+ 报告目录 `index.md` 导航页(卡片/trace/staging/上一 run/健康一行)——**第二天复盘从 index.md 进**。universe 侧固化 `weights_used.json` + meta.regime = 重放输入齐备(漏斗确定性,同输入可复现)。
- **观察单(07-02 新,`scan/watchlist.py`)**:`context/watchlist.csv` 跨日活状态;机判词表 v2 `close_above/close_below/ma_bull/money_pos/by_date(日期锚⏰)+manual`;每日 `run_check` 对 `L1_scored_full` 判 **触发/触发(待人工项)/提醒(k/n)/临近/待触发/失效**(invalidation 或过期 → 失效)。状态梯度:触发>提醒(k/n)>临近>待触发;since_born≥+15% 未触发标🔥(错过审计)、🆕=较前日新达成。**触发≠自动升级**,只提示按 stock-research lite 档复核。种子:胜宏 300476(当前状态"临近":314 上方 ✓、多头排列 ✗、中报待人工;失效线 298.5)。
- 发布:`reports/scan/<运行时刻>/`(数据日在 manifest.json,retro 据此定位)。

## 真实计量(OTEL 遥测,07-05 新;未实跑)

- **为什么**:落稿估算下界 ~75k vs 真实 ~1M(07-03 实证,主因 L4 输入 28卡×~25k);07-05 叶子 agent 化的 cache 收益假设未验——30 卡同一条消息并发存在 **cache 写入竞态**,可能全 miss。
- **仪器(旁路,生产派发路径零改动)**:跑扫描的会话从带 env 的 shell 启动:`CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_METRICS_EXPORTER=console OTEL_LOGS_EXPORTER=console OTEL_METRIC_EXPORT_INTERVAL=30000 OTEL_LOG_TOOL_DETAILS=1`;metric=`claude_code.token.usage`(`type∈{input,output,cacheRead,cacheCreation}` × `agent.name` × `query_source`,文档确认——可按 l4-card/buy-skeptic/sector-brief 聚合)。console 出 stdout 与交互 TUI 可能互扰 → 冒烟三选一(console 重定向 / prometheus 本地轮询 / otelcol file exporter),验收不变=拿到分解表。
- **解析**:`python -m autoresearch.trace.telemetry <raw> --out reports/scan/<run>/token_telemetry.md` → agent×type 表 + cache命中率(=cacheRead/(input+cacheRead));多形态容错、累计/增量按 key 单调性自动判别;与当次落稿估算表对账。
- **判读**:l4-card 行 cacheRead≈0 → 并发竞态坐实 → 修法(1+29 暖 cache/前缀重排)另走 proposal,**勿预做**;显著 >0 → 07-05 假设成立,落账真实命中率。

## 跨层校准环 + 触价校准(07-05 新;报表就绪,样本积累中)

- `python -m autoresearch.learning.cross_calib` → `reports/learning/cross_calib.md`(prelude 自动刷新):① **L3→L4 翻案率 per lane**(`L3_judged_full` × `health.final_ratings`,高确信=conviction≥70、翻案=L4 ≤UW);② **rubric 门柱级拦对/错杀**(binding=唯一✗门,`assemble.gate_status` 共享解析防口径漂移;ex 口径同 `gate_ledger`,**错杀=ex5>0 且 hi_10 触达卡内目标**,拦对/错杀不互补——中间地带=拦了但未触达)。
- **触价校准(buy_ledger 扩)**:`target_calibration` 统计**全卡**(全评级——0 买连败下样本不断供,解 ≥OW-only 永久 thin 困局)10 日目标触达率 → `buy_ledger.md` 新节;首证=东方财富 hi10 6.3% vs 目标 28.8% 过乐观。
- **注入分层(铁律)**:python 只产读数;prelude 汇总屏打三条**当日件建议行**(📐→`_l4_shared_instructions.md`;🔁→L3 校准块旁;🚪→skeptic/PM 先验),n<10 thin 行自带「禁注」**勿贴**。**校准不改门/权重/评级**。

## 闭环层(`autoresearch/learning`,确定性度量 + Claude 诊断)

| 件 | 作用 | 现状读数 |
|---|---|---|
| `retro` | 6 步复盘:归因(T+1 **+ T+5 盲区**〔07-02 新〕)→ 诊断 → 权重自动重标定(快照+changelog 可回滚)→ 建议(人批)→ 经验 → mark_done | 06-24 已复盘(根因坐实);06-25/26/29/30 **成熟度门控中**(fwd_5 07-02晚/03/06/07 到期,scan 前置自动补跑) |
| `stage_eval` | 逐段 edge:L2 keep-cut lift / L3 net IC / L4 评级单调 / 辩论差 | 06-24:L2 −1.1%、L3 +0.144、L4 +0.075 |
| `channel_ledger` | 跨日每路 `unique_excess_t5`(边际 alpha);n_days≥3 才下结论 → quota 提议(±25%,advisory) | momentum 路 06-24 unique +9.2%×31(路对,旧权重杀之) |
| `zero_buy_ledger`(新) | 0 买日 vs 有买日市场后市对照 | 7 个 0 买日 fwd_5 −0.60% = 空仓正确 |
| `feedback_store` | lessons(反复强化可升 self_review 硬门;**regime 作用域** + **MTM 计数/机械 confidence**〔support +0.03/refute −0.08,达阈自动提名摘门/退休,人批〕;注入 cap=8)/ proposals(**看板**:retro_input 列 open+天龄)/ changelog / 权重快照回滚;`decay_lessons` 已接 `mark_done` 节奏 | `ls_reversal_regime_low_composite_trust` ×4;open:cap_floor 软化、main_net 口径、horizon 之争 |
| `gate_ledger`(新) | **门的 MTM**:assemble 落 `gate_fires.csv`(failure 带 code)× retro fwd → 每门拦对率;持续 ex>0 → 松阈/退役建议(人批) | 数据从 07-02 起积累 |
| `watchlist_ledger`(新) | 观察单**触发→后市**度量(触发单准不准) | prelude 每日刷新(born→今 巡检节);触发主表待首个触发样本 |
| `scan/dossier.py`(新) | **个股档案(前科卡)**:近 10 日入围史(评级/binding 理由/skeptic verdict)注入 L4 简报 → **增量研究**(卡片须含"变化项 vs 档案"节);防锚定铁律同策略师 | 紫光国微 4 次入围/三度 CFO 门史自动聚出 |
| `factor_lab` | harvest(成型日面板)→ calibrate(flat)/**calibrate_regimes**(分桶)→ eval | 面板 107 成型日(至 07-01);重标定一律走 `retro.recalibrate_and_log`(审计) |
| `consensus`(新) | 卖方一致预期**前向积累**(`report_rc` 限频 **1次/小时** → 每日 1 拉,scan 前置)| 积累 0 日;**≥60 日过 factor_lab IC 门才谈入 composite** |
| `journal`(07-02) | **扫描日记**:每日一行(regime/菜单/漏斗/买/触发/市场 fwd 回填)——按日叙事主干,与各 ledger(按仪器)正交 | 11 日已回填;9/11 为 0 买日 |
| `changelog_ledger`(07-02) | **重标定效果**:采纳日前后各 ≤5 个 retro 日的日度 composite IC 对比——**自学习的元评估** | 4 条重标定入账(前期样本薄) |
| `buy_ledger`(07-02) | **买后管理**:≥OW 买单 × fwd_1/5/10 + 开盘 gap + 目标命中 → **评级基率**(n≥10 才当先验) | 6 笔历史 OW 入账(fwd 待 attribute 刷新) |
| `sector_memo`(07-02) | **记忆中层**:行业级事实月度蒸馏(lessons ↔ 档案之间),注入 L3 块 + L4 简报行 | 空(首次蒸馏 ≥20 scan 日后) |
| `scan/health.py`(07-02) | **run_health**(NaN 降级/在位/churn/L4 阶段效能/买单数)+ `index.md` 导航;retro 读之防"数据病当因子病" | 07-01:hk_ratio 降级、churn 16%、早停率 20% |
| `scan/calendar.py`(07-02) | **解禁+预约披露日历**(share_float 分块 + disclosure_date)→ L4 旗 + summary 日历 + 触发日期锚 | 07-01:216 披露+1 大解禁;紫光国微中报=08-15 |
| 影子漏斗(07-02) | universe 变体 L2(nostrat/nocap)→ retro 捕获对照(**免费确定性 A/B**) | 数据从下一真实 scan 起积累 |
| `paper_nav`(07-05 wave) | **影子组合成绩单**:真实(≥OW)/影子(top-3 Hold)/市场等权 三线 NAV(10% 槽·持10日·次日开盘进出);`真实−影子`=门的价值;summary 置顶一行 | 回填起 06-18;06-19 孤儿键跳过 |
| `shadow_buys`(07-05 wave) | 每日 conviction top-3 Hold 确定性记账(assemble 自动)→ NAV 影子线 + 评级基率样本池;与机会成本红队正交 | 历史回填 ~30 行 |
| `catalyst_ledger`(07-05 wave) | 催化旗票 vs 无旗票 fwd_5 对照(**n≥30 才读数**);IC 过硬前不入 composite | 零积累起步 |

## 数据层要点

tushare 默认源(push2 被网络封锁;`TUSHARE_TOKEN` 高权限);keyless 可达:同花顺一致预期(L4 fwd-PE)/腾讯/datacenter-web。限频要点:`report_rc` 1次/小时(其余常用端点宽松)。缺权限端点自动降级 NaN、打分重归一。**盘中跑 retro**:当日 EOD 未发布 → fwd 降级 NaN 不抛(07-02 修)。

## 已被实证否决的方向(勿重启;关键数字已录本节,附录级明细在 git 历史)

- **L2 上模型**(附录 D):全 zoo 负 IC + 回测无稳健 alpha;新特征(盈利修正等)IC 过硬前不复活。
- **业绩预告 L1 事件通道**(附录 E):两季对照,强制披露季(中报窗)T+5 超额 −0.27%/胜率 35%,追缺口 −2.92%——公告后追买无肉;alpha 若有,在披露前的预期变化(= consensus 积累的方向)。

## 开放线头(诚实局限)

1. 06-25/26/29/30 retro 待 fwd 成熟(07-03~07-07 陆续),补跑后 T+5 盲区/错杀/floor/影子对照数据自动变厚;
2. regime 块 horizon 之争(`pr_20260702_001`)待 T+5 数据裁决;risk_off 块样本薄(11 日);
3. **全部新 LLM 流程段(宏观 lite 新输入〔macro_state 捆绑〕/行业 brief 与同链对比/机会成本红队/观察单补 conds/档案"变化项"/经验人判 MTM/P4 倾向行/复用后编排)未在真实 skill 跑动中实测**——确定性件全有测试(**07-03 海拔重构后 640 绿**),LLM 段是脚手架就位;**A3 早停抽检 / D1 卡模板 v2 未实跑**;
3b. MTM/gate_fires/触发 ledger/影子对照/P4 翻盘率全部从 2026-07-02 起零积累——头两周读数样本薄,别过度反应;
3c. attribution 一次性落账问题**已修**(07-03):`retro refresh`(prelude 首步自动跑)对成熟老日重写 attribution 补 fwd/hi;实测刷 8 日、东方财富现真身(hi10 +6.3% vs 目标 +28.8% = 首条"目标价过乐观"实证)。遗留孤儿:06-19 为端午假日跑动(非交易日键),fwd 永远无法结算,保持 "—";
3d. Δ表省幅随日况(L2 轮换大的日子略 0 只);卡片复用省幅=churn(07-01 实测 20%,窗口而异);评级基率 n<10 禁注;
3e. **07-02 首航后新增(07-03;07-04 落稿契约确定性化)**:healthy 通道/健康桶已上线但其 alpha/捕获增量未验——由 `pre_healthy` 影子反事实 + retro 裁决(≥10 日);哨兵档未实跑;落稿契约中 `_l4_prompt_*`+`_harvest_list` 已由 `l4_card prompts` 确定性产出(`_l3_table`/`_v_*` 仍靠编排手落;token 表新增 **L4 输入·slim** 行 + prompt 缺稿可执行提示;07-03 实证:落盘下界 ~75k vs 真实量级 ~1M,主因 L4 输入侧 28 卡 × ~25k 未计);token 真实计费仍只有 Claude Code `/usage` 可见;
4. consensus 首拉待限频窗;积累 <60 日前盈利修正不入线上;
5. 仅供研究,非投资建议。
6. anns_d 无接口权限(07-05 实测):公告情感列空、监管旗走 L3_webnews 回退(`reg_hits_for_code`);run_health `anns_empty_rate`=1.0 即该态。权限开通/替代公告端点待核;northbound 通道 hk_ratio NaN=100% 空转读数(`northbound_probe`)取证中,quota 不动待 proposal。

# scan-market screening-playbook — 六段漏斗操作参考

> **本文 + `SKILL.md` 自足:跑全流程、改因子、校准权重所需的一切都在这两份里,无需任何 `docs/specs/`。**
> 逐段写清 **引擎/模型 · 输入 · 规则 · 产物**;重点在 **L2 粗排 / L3 精排 / L4 三层级联**(你在 session 内扮演资深投资师与 PM 的判断步)。
> 召回因子菜单 + 权重校准方法 + IC 实证基线见**文末附录 A/B/C**。

## 漏斗一图
```
全A ~5,500 →(L0 选集·硬门)~4,300 →(L1 召回·复合分 top)1,000
   →(L2 粗排·GBDT 学习重排·零 LLM)200 →(L3 精排·holistic 单 agent 通看比较选 + 增量证据/论点/红队)~30
   →(L4 研究·级联卡:Tier-1 Sonnet 全判〔rubric 派生〕·~10 agent 并发 / Tier-2 Opus 条件平反)~30 张
   →(Tier-3 买点候选多空辩论·多头⚔空头 + PM 3透镜裁判,定级+证伪)~8 →(L5 整合)<运行时刻YYYYMMDD_HHMM>/{summary.md〔逐阶段表 + token 估算〕 + details/〈名称〉 + trace/ + manifest.json〔记数据日〕}
```
**成本级联**:模型曲线弯成跟漏斗一致——**越宽越便宜,Opus 只留刀尖**。
**L2 确定性(GBDT,零 LLM)**;L3 / L4-Tier1 走 **Sonnet**;**Opus 只在 L4 顶点出场**(Tier-2 条件平反 ~0–3 + Tier-3 多空辩论 ~8×2 多/空)。**能力提升也只压顶点**:C 评分卡(`rubric_rating`)把 Sonnet 过度多报压在 Tier-1、买点候选变少;买点候选直接进 Tier-3 辩论(辩论既定级又证伪,一次 Opus 顶过去『单遍复核 + 对抗』两步)。L0/L1/L2/L5 零 token。

## L0 选集 + L1 召回(`screen_market.py`,确定性,零 token)
```bash
uv run --no-sync python scripts/screen_market.py <date> --source tushare
```
- **L0 选集**:tushare 全市场富因子(daily_basic/daily×3/moneyflow 结构/stk_factor_pro/cyq_perf/hk_hold + yjbb 基本面)→ canonical 列;硬门 = 剔 ST/退/停牌/次新 + 市值地板(默认 30 亿)+ **含北交所**。
- **L1 召回**:Step A 轻门(只去不可交易/无核心数据,尽量不误杀)→ Step B **行业条件化复合分**(9 因子组 × 申万/东财行业的 IC 校准权重,读 `weights.json`)→ 全市场排序 top `--recall-n`(默认 1000)。
- 9 因子组:①动量/趋势 ②资金·主力(净占比) ③资金·散户(小单净) ④筹码(集中度/相对成本) ⑤北向 ⑥技术(RSI/MACD) ⑦成长 ⑧价值(行业内) **⑨ volprice(多日量价资金流:CMF 买卖压 + OBV 资金方向;`_harvest_vol_series` 拉 ~20 日序列算,IC 实证 decile +40bps/t≈2、calibrate 全市场权重 0.0276=并列最高组)**。**因子→端点映射见附录 A、权重校准方法见附录 B**(符号由 T+1 IC 决定)。
- 产物:`L1_recall_top1000.csv`(复合分 + 9 子分 + 原始因子〔含 cmf_20/obv_mom_20〕)、`sectors.csv`(板块概览)、`meta.json`(漏斗计数)。
- **召回宽**:T+1 校准下复合分由快因子(动量/技术)主导,会把强动量/甚至过热票放进来——**这是故意的**(高召回),过热透支由 L2 剔。
- **两个确定性量价叠加(不改 IC 权重,风险调整)**:**过热抑制**(高动量 + 超买/获利盘满 = 见顶 leader → 复合分 −8)+ **吸筹加成**(低位〔获利盘<40/破成本〕+ 放量〔量比≥1.5〕+ 主力未撤 = 底部疑似吸筹 → +5,小幅**保召回**)。后者是"底部放量"在 L1 的落点——只保证被召回进 top,**真伪交 L2/L3/L4 三维验证**(研究:底部放量 >70% 无基本面会败)。`vol_ratio` 已随召回 CSV 落地、贯穿 L2/L3/L4。

## ⚠️ 因子方向经验校准(L3/L4 通用,**务必写进每个 subagent prompt**)
> **运行时由闭环记忆生成**:构造 L3 subagent prompt 前,调
> `feedback_store.render_calibration_block(本批申万行业 scopes, with_feedback=True)` 取本块——三层叠加(优先级从高到低):**①近期同域未蒸馏反馈(E1·刚被你标错的坑,别再犯)→ ②自学习经验(retro 复盘;带 `〖硬门〗` 的已是 self_review 确定性拦截)→ ③IC 基线**;`context/knowledge/` 空 + 无反馈时**逐字回退**基线,老路径不破。取法:`uv run --no-sync python -c "import autoresearch.learning.feedback_store as fs;print(fs.render_calibration_block([('industry','电子'),('industry','医药')], with_feedback=True))"`。下面是**基线**(人读参考):

来自 `factor_lab` 的 T+1 IC 回测(完整基线见**附录 C**),几条**与直觉相反**、上一轮测试中 L2/L3 误读、被 L4 反向打脸的:
- **高获利盘 winner_rate(>90)= 抛压/见顶风险,不是"筹码健康/顶配"**(十分位 −42bps)。低获利盘=套牢盘多=有上行空间。
- **高量比 / 高 RSI(超买)= T+1 偏弱**(vol_ratio −15bps);`pct_60d 极高 + RSI 高 + winner 满` = **抛物线顶 → 回避**,别当"强势延续"。
- **量价要分位置(关键)**:裸量比对 T+1 负(rank-IC t=−2.31 已剔出召回打分),**因为没分位置**——放量在**顶部=派发(空)**、在**底部=吸筹(多)**。`uzi_lenses.volume_price_signals(L1行)` 已按位置条件化:`量比↑ + 低位(获利盘<40/破成本)+ 主力未撤`=**底部放量吸筹→留/加分**;`量比↓地量 + 低位`=地量见地价;`高位放量 + 主力净出`=派发→砍。**警示:底部放量 >70% 无基本面会败 → 必须 L3/L4 三维验证(基本面+主力真在+估值),别只凭量价。**
- **多日资金流(已进 recall + L2 表)**:`cmf_20`(Chaikin 买/卖压)、`obv_mom_20`(OBV 资金方向)是**多日序列**指标,IC 实证比单日量比强得多(decile +40bps/t≈2,已是 volprice 组、calibrate 权重 0.0276)。读表时 **>0=买压/资金净进=吸筹侧、<0=卖压/派发侧**;与位置(获利盘/相对成本)共振更可信,仍须基本面背书。
- **主力**看 `main_net_ratio`(大单+特大单净占比),**散户**看 `retail_net_yi`(小单);主力净流入是 **1–2 周 swing** 信号,非 T+1。
- **价值(低 PE)在 T+1 反而偏弱**(成长/动量续涨);价值用于"不追高",非"次日动量"。
- **优先留**:涨幅适中(未过热)+ 主力真实进场(main_net_ratio 正)+ 筹码有空间(获利盘不满)+ 基本面干净;纯动量抛物线顶,L4 大概率 Underweight,别堆到精排顶端。

## L2 粗排(GBDT 学习重排,确定性零 LLM,1000→200)
> **从 AI keep/cut 改成确定性学习重排**(对症旧 L2 的 token 成本 + 主观漂移):用 GBDT 学每日横截面 T+1 收益,把召回的 1000 重排成 200。"cheap 线性召回 → learned 重排"正是搜索排序的级联,**不和 L1 冗余**。**已在 `screen_market.run()` 内自动产出**(见上节命令),无需单独 subagent 步;AI 判断从此**只在 L3/L4**。

**引擎**(`factor_lab.py` + `screen_market.py`,全确定性):
- **模型**:LightGBM 横截面排序(`factor_lab.train_gbdt`)。**特征** = 8 因子组分位(去 growth,factor_lab 帧无季度基本面)+ 20 个双侧都有的原始因子 + **线性 composite 锚定特征**(GBDT 至少能复刻线性,再叠非线性 → 不该弱于线性)。**标签** = 每日横截面 rank-norm 的 `fwd_1_oo`(T+1 开到开,可交易、无前视;Qlib CSRankNorm 思路)。
- **自保门(铁律:不自欺)**:`train_gbdt` 时序留出末尾成型日做 oos,比 GBDT vs **线性复合分**的 rank-IC;**oos 未胜线性 → 模型 meta 标 `beats_linear=False`,`predict_scores` 默认回落 `None` → L2 用 composite top200**。绝不部署比线性差的模型。`meta.l2_engine` 记 `gbdt` 或 `composite-linear(回落)`。
- **薄面板常态**:成型日少(~1 季度)时 GBDT 多半只复刻线性(composite 锚定特征 gain 占绝对大头)、加不出稳健非线性 → 门关、用线性。要它真启用:`factor_lab harvest` 更多成型日(更广 regime)再 `train`,**一旦 oos 胜线性即自动启用,无需改代码**。

**产物**:`L2_gbdt_top200.csv`(`l2_rank` + `gbdt_score`〔回落时空〕+ 召回因子列);`meta.l2_engine` 标引擎。**无 LLM 中间件、无 reasoning 留痕**(确定性层)。

> **L2 已无 subagent / prompt 模板**——keep/cut 的主观判断上移到 L3 holistic 精排(那里一个 agent 通看 ~200 比较着选,把旧 L2 双赛道的"信号共振 / 排陷阱 / 趋势 vs 回归"判断一次做掉)。旧『因子方向经验校准』仍在 L3 注入(见上)。

## L3 精排(holistic 单 agent:一次通看 ~200、比较着选 ~30)
> **holistic > 逐只孤立打分**:一个 agent 通看整张 ~200 行表、横向比较着选,把旧 L2 双赛道的"信号共振/排陷阱/趋势 vs 回归"判断 + 精排一次做掉。孤立逐只打分各看各的、易虚高;比较式天然控总量、强制相对排序。
> **多 persona 对抗(UZI 思维,可选增强)**:必要时对**入围候选**可再用多个 subagent 扮不同流派(价值/成长/游资/quant/风险官)各自引因子复核,**分歧大就把分歧本身写进结论、不取均值抹平**(「矛盾必须呈现」)。`uzi_lenses.trap_signals(L1因子行)` 做风险官的机械底(获利盘满/过热/派发命中即压 conviction);`uzi_lenses.volume_price_signals(L1因子行)` 做游资/技术派的机械底(底部放量吸筹/地量企稳/缩量回调=量价转多→`bias=吸筹` 抬 conviction,但**须基本面背书**;`bias=派发` 压 conviction)。
> **发布前硬门**:`autoresearch.scan.assemble` 已接 `self_review` —— 买单若踩经验红线(winner_rate>88 无 override)/ 覆盖不足 / 评级-因子矛盾 / 行业过度集中 / 空泛话术,summary 顶部出 🛑 banner,**先修根因再信报告**。结构化经验(`lessons.jsonl` 带 `guard:{field,op,value}`)自动并入硬门。
**目标**:对 200 补 L1 没有的**真证据**,一次通看比较着选 ~30 并红队压测。慢因子在此兑现。

**步骤**:
1. 增量取数:`harvest_l3_evidence(date, l2_top200_codes)` → 每只 `context/scan/<date>/L3_evidence/<code>.json`(龙虎榜席位 / 业绩预告 / 快报;无权限端点降级标注)。
2. **一个 holistic subagent,`Agent(model='sonnet')`**:`l3_table_md(date)` 把 ~200 只(因子 + 证据摘要 `lhb_n/has_forecast/has_express`)压成**一张紧凑表**喂它,**通看全表、横向比较着选 ~30**(每只入选出 论点/红队/催化/确信度/脆弱度/lane),落 `L3_judged_full.csv`。量大可拆 2–3 个 holistic 片(每片通看一截、各给配额),但**每片仍是"比较着选"而非逐只孤立**。
3. **`merge_l3_finalists_v2(judged_df, target=30, trend_quota=10, hybrid=True)`** → `context/scan/<date>/finalists.csv`(把 holistic 入选排成 finalists + 趋势配额安全网)。
   - judged_df 需含列:`code,name,sector,lenses,conviction,fragility,thesis,risk,catalyst,triage_lean,lane,pct_60d`(`lane`/`pct_60d` 配额用,源自 L2 表)。
   - **趋势配额(安全网)**:纯 `conviction−fragility` 会把高 fragility 的强势票挤出(实测:生益+205%/亨通+158% conv 高但 frag 高 → 进不了 top30)。`merge_l3_finalists_v2` 给 trend lane 保底 `trend_quota` 席,**一半按 conviction(质量趋势:健康强势)+ 一半按 pct_60d(动量龙头:最热的票)**(hybrid)——高 fragility 是 T+1 概念,swing 不该一票否决。捞进来后由 **L4 做估值/解禁尽调定级**(实证:抛物线顶 PE160~440 + CFO负 + 解禁 多半 Underweight/Sell,质量强势如胜宏 PE77 才 Overweight)。

**L3 holistic 选股 prompt(模板)**:
> 你是资深 A股投资人 + 风险官 + PM。下面是 L2 粗排出的 ~200 只紧凑表(因子 + 龙虎榜/预告/快报摘要)。**先内化『因子方向经验校准』**(上节,`render_calibration_block` 注入)。**一次通看全表、横向比较**,选出最值得深研的 ~30 只——**趋势 + 回归兼顾,别全堆抛物线顶**。
> **比较着选**:同板块/同因子画像的票互相比、只留最强的;陷阱直接弃(高位放量派发 / winner满主力撤 / 低PE但 np<0 / 抛物线无主力承接);**底部放量吸筹 + 基本面背书**的优先(`volume_price_signals`/`trap_signals` 机械底辅助);趋势票**不因"涨多"误杀健康强势**(主力还在+业绩跟得上),回归票看低位空间(低获利盘=空间)。
> **内化校准**:满仓获利盘/winner>90 在主力撤/业绩证伪时=见顶,主力还在则不是。
> **每只入选输出**(CSV `code,name,sector,lenses,conviction,fragility,thesis,risk,catalyst,triage_lean,lane,pct_60d`):thesis≤25字多头论点(落因子/证据)、risk≤25字最大证伪点(**必须真,不许橡皮图章**)、catalyst≤15字时点(无则"无明确催化")、conviction/fragility 0–100、triage_lean 看多/中性/回避、lane trend/reversion。
> 紧凑表:`<l3_table_md(date)>`

## L4 研究(委托 analyze-ticker-lite,三层成本级联)
对 `finalists.csv` 跑**三层级联**:**Tier-1** Sonnet 全判 → **Tier-2** Opus 只平反被 Sonnet 误压的高 conviction → **Tier-3** Opus 对买点候选跑多空辩论(定级+证伪)——把 frontier 模型收敛到唯一真花钱的决策点,且买点候选只过一次 Opus(辩论),不再双重复核。

**Tier-1 · 全 ~30 只 · Sonnet · 并发**:`batch_finalists(finalists_df, size=3)` 切 ~10 批,**在一条消息里并发派发这 ~10 个 `Agent(model='sonnet')`(并行启动,非顺序逐批 → wall-clock ≈ 单批,不是 10×单批)**;每批 3 只/独立 context,逐只跑 analyze-ticker-lite(读其 `lite-playbook.md`):
```bash
uv run --no-sync python scripts/harvest_context.py <ticker> <date> --slim   # slim 取数,每只 ~13KB(≈全量 20%)
# → 决策卡 staging 到 context/scan/<date>/details/<ticker>.md
```
> **批内逐只独立判、卡片之间不交叉引用**;每张卡仍带完整尽调 rubric(trap 信号 / 估值纪律 / **抛物线顶→压级**),保住「L4 反向打脸 L3」。
> **⚠️ Tier-1 评级由评分卡派生(`rubric_rating`),不是 gestalt——防过度多报**(实测 6-18 Sonnet 10 OW vs Opus 3 OW,撑大了 Tier-2 复核量)。每张卡:填 6 维评分卡(强+1/中0/弱−1)算**净分**,再过 **3 道 OW 硬门**;`l4_card.rubric_rating(dims, gates)`(`autoresearch.scan.agents.l4_card`)给**建议评级**,卡片 `**Rubric建议**` + `**Rating**` 必须等于它,否则显式写 `**偏离**:<硬理由>`(发布层 `self_review` 抓『评级超 rubric』)。**净分定档**(≥+4 Buy／≥+2 OW／−1~+1 Hold／≤−2 UW／≤−4 Sell);**任一 OW 门未过 → ≥OW 一律压 Hold**。三道门:
> ① **主力真在**:净占比为正 **且** 绝对净额(亿)同向为正——占比+但绝对净出、或微盘(<0.3亿)占比放大 = 占比假象(`trap_signals` 已机械标注『主力占比绝对额背离/微盘放大』);
> ② **业绩真兑现**:预增先看基数——ROE 仍低(<8%)的『预增 X 倍』是近零基数幻觉(`低基数幻觉` flag);营收同比为负(丢单)即便净利增也不算兑现;
> ③ **估值不透支**:fwd PE 远低于 TTM 时**核实预告是否全年口径**(『+200%』只覆盖单季 → 年化真实 PE 翻几倍,标称 14.7x 可能实为 46x);CFO/NI<0 的『盈利』先打折。
> 拿不准就给 Hold——**Buy/OW 直接进 Tier-3 辩论;Sonnet 宁可漏,也别滥报撑大 Tier-3 辩论量**。

**Tier-2 · Opus 平反(瘦,唯一职责=防假阴性)**:Tier-1 全部回卡后,主线 `ratings = parse_ratings_from_details('context/scan/<date>/details')`。**买点候选(Buy/OW)不在这里确认**——它们直接进 **Tier-3 辩论**(辩论既定级又证伪,比单遍复核强,省掉一次 Opus)。Tier-2 **只**救 Sonnet 误杀:`pick_downgrade_reviews(ratings, finalists_df, conv_floor=80, top_k=3)`——把 L3 极高确信的趋势票被 Sonnet 判到 ≤Hold 的,**派一个 `Agent(model='opus')`** 在**同 slim 证据**上**单遍平反 / 确认**,**覆盖**原 `details/<code>.md`,稿存 `context/scan/<date>/_l4_tier2_<code>.md`(归档 reasoning/l4/)。平反到 Buy/OW 的**并入下面 Tier-3 买点候选**。
> **实测(6-18 v2)**:Tier-1 收紧后 Sonnet 降级多半判对(菱电/亚翔 Opus 也认 Hold),故默认收紧(conv_floor=80、top_k=3)——**是防"误杀真买点"的保险、不是常态;Tier-1 越可信越该调高 conv_floor 或跳过**。
> **K1 旋钮**:想拉 live 证据(新闻/DCF/席位/10日资金)→ 改跑该票**全量 analyze-ticker**(更贵更有 edge)。默认 lite-on-Opus 有界可复现。

- **复用召回因子,不重算(已落到代码层)**:`harvest_context --slim` 在 scan 目录(`context/scan/<date>/`)能找到该只的 L1 行时,**自动**用 L1 因子(主力净占比/散户/筹码/北向/技术/复合分+8子分)重建『主力/技术/筹码/北向』块 —— **零 tushare 重复取数、与召回数字一致**;`harvest_context` 只 live 取 L1 没有的深块(个股新闻/利润表/偿付/卖方目标/解禁,及 L4 才增量的 股东户数·质押/业绩预告·快报)。判断 subagent 仍把该 L1 行塞进 prompt 供推理。**A股价格真值走 tushare(`load_ohlcv` 对 .SS/.SZ/.BJ 前复权),北交所可用、与召回同源,不走 yfinance。** 想要 10 日资金序列/MACD 明细 → 对该票跑**全量 analyze-ticker**(非 slim,live 重取更全)。
- subagent 独立 context、**只回传 评级/目标/R:R**;主线只收小结果。量大可选 **workflow** 并行(需用户显式开启)。
- 某只想下重注 → 再单独跑**全量 analyze-ticker**(模型 **Opus**,live 重取最全)。

## Tier-3 买点候选多空辩论 + PM 裁判(对抗验证闸,~8 只)
把级联省下的预算重投到**最贵的决策点**:**所有买点候选(Buy/OW)**每只跑一场**多空辩论**——独立的多头/空头研究员各自尽调,主线当**组合经理(PM)裁判**用 3 透镜投票**定级 + 证伪**(买点候选只过这一次 Opus:辩论既定级又证伪,不再单遍复核)。错一个买点 = 真金白银。这是从单边 skeptic → 真·多 agent 辩论的升级(借鉴上游 TradingAgents 的 Bull-vs-Bear → PM 结构)。

**为什么辩论而非单 skeptic**:单边 skeptic 只从一个角度挑刺——它的特定框架既可能漏掉真风险,也可能拿弱空头论点**错杀好买点**。独立多头(steelman 买点)⚔ 独立空头(证伪)+ 中立 PM 裁判 = 两面都被最强论证压过;再叠 **3 透镜共识**收掉单样本评级方差(self-consistency,治『同一票 Opus 复核两次结论飘』)。

**步骤**:Tier-1/Tier-2 回卡后,主线 `candidates = pick_buy_candidates(ratings)`(Buy/OW 买点候选,含 Tier-2 平反进来的)。每只候选:
1. **多头研究员**(`Agent(model='opus')`):steelman 买点——最强多头论证落到数字(预期差/催化/资金承接/估值锚),产物 `context/scan/<date>/_v_bull_<code>.md`,**只回传一句最强多头**。
2. **空头研究员**(`Agent(model='opus')`,**与多头独立、不互看稿**):证伪买点(攻击面见下),产物 `context/scan/<date>/_v_<code>.md`,**只回传一句最强空头 + 触发位**。
3. **PM 裁判(主线你自己,非另起 subagent——省 1 次 Opus/只,且你是天然 orchestrator)**:读多空两句,**3 透镜各投一票**:① **估值透镜**(空头估值证伪是否成立)② **资金面透镜**(主力承接 vs 派发,谁的证据硬)③ **毁灭风险透镜**(解禁/质押/业绩雷的尾部概率)。多数票定 verdict、记票型;写/追加一行到 `context/scan/<date>/verify.csv`(表头 `code,verdict,bull,bear,trigger,consensus`)。

> **verify.csv 一行**:`<code>,<维持|降级|否决>,"<≤20字最强多头·禁英文逗号>","<≤20字最强空头·禁英文逗号>","<触发位:价/指标/事件>","<共识:如 维持3/3 或 降级2/3(估值/资金)>"`。降级/否决 → 在 `details/<code>.md` 顶部加一行 `> ⚠️多空辩论:<bear>`。
> **verdict 口径(按 3 透镜票)**:维持=≥2 透镜判多头赢(空头无证伪买点的硬证据);降级=2:1 偏空、有真实下行但不致命(评级降一档);否决=≥2 透镜判否、买点被实锤推翻(估值透支/解禁砸盘/业绩证伪)。

**两研究员共用攻击面**(空头逐条找最强反面、多头逐条防守):① 估值(PE/PEG 分位、Bear 情景概率)② 解禁/质押(时点+比例)③ 主力背离(承接是否消失、`main_net_ratio` 转负)④ 业绩雷(预告/快报/应收·存货·商誉)⑤ **前视偏差**(证据严格 ≤ 分析日,无未来信息泄漏)⑥ 筹码派发(获利盘满 + 放量滞涨)。

`autoresearch.scan.assemble` 据此:**① 折回评级**——`降级`→降一档(OW→Hold,踢出买单)、`否决`→至少 Hold(买单不挂系统自己都不信的评级);**② 归档** reasoning/verify/(多空两稿 + verify.csv);**③ summary** 买单行带徽标(✅维持/⚠️降级/🛑否决)+ 多空辩论明细块(多/空/触发/共识)。

> 与既有 `self_review` 机械硬门**叠加且正交**:self_review 是确定性红线(winner>88 无 override / 覆盖 / 评级-因子矛盾 / **评级超 rubric** / 行业集中 / 空泛),本闸是 LLM 临场多空对抗找**新** bear/bull 证据;summary 同时呈现。

## L5 整合(`autoresearch.scan.assemble`,确定性)
```bash
uv run --no-sync python -m autoresearch.scan.assemble <date>
```
读 `meta.json` + `L1_recall_top1000.csv` + `L1_scored_full.csv` + `L2_gbdt_top200.csv` + `finalists.csv` + `details/<ticker>.md`(用 `parse_rating` 提五档 + 仪表盘),发布到 **`reports/scan/<YYYYMMDD_HHMM>/`**(目录名 = **实际运行时刻**;数据日 analysis_date 落 `manifest.json`,与目录名解耦,`retro._report_dir_for` 据此定位):
- `summary.md` 三段:**①漏斗数量(带引擎列)②各阶段卡点+股票概览 ③投资建议**——buy-list 是**逐阶段结论宽表**(每只 `名称/板块 | L1召回〔#名次·复合分〕| L2粗排〔#重排名次·gbdt〕| L3论点·确信 | 评级 | 目标 | 置信度 | Tier-3 徽标 ✅维持/⚠️降级/🛑否决`,**已删 代码/R:R/提案 列**)+ 组合视角 + 局限。
- **`## 各阶段 token 消耗(估算)`**:分阶段引擎/LLM 调用数/输出字节/~token(L0/L1/L2 确定性=0;L3/L4/Tier-2/3 按落盘推理稿字节 ÷2.8 粗估)。**口径诚实**:输入侧(slim 上下文)未全留痕→真实数倍于此表,为可测下界。
- `details/〈名称〉.md`:决策卡(**按股票名称命名**,非 ticker;staging 仍 `<code>.md`,发布层改名,retro 从卡内标题取 code)——仅当前 finalists。
- `trace/`(与 details 同级):**每阶段全量数据**(L0计数 / **L1_scored_full 全打分排序(4000+,非仅1000)** / L1_weights / **L2_gbdt_top200 重排** / **L3_judged_full 全判断** / L3最终入选 / reasoning 推理留痕〔l3/l4/verify,L2 确定性无留痕〕/ funnel.md 溯源)。
- 缺卡的 finalist 标 `⚠️卡片缺失`。

## 数据坑
- **默认 `--source tushare`**(东财 push2 常被网络封锁)。富因子缺端点权限 → 该列 NaN、打分重归一。**北向 hk_ratio 仅覆盖 ~5% 个股**(北向只持一部分),小盘多为 NaN(north 组只对有北向的票生效)。
- **召回权重非拍脑袋**:`factor_lab.py`(tushare 全市场 rank-IC 回测,T+1 校准 + 申万行业层级收缩)产 `weights.json`;**实证结论 + 校准/训练命令见附录 B/C**(符号随窗口/regime 漂移:近季动量+技术+volprice 主导,近年转 reversal)。改因子/组后必须 `harvest`(一次)→`calibrate`→`train`(L2 模型)→`eval` 复核再上线。
- **业绩披露滞后** → 用最近可得报告期(脚本按分析日推算)。**L3 增量** top_list/forecast/express 若无 token 权限 → evidence 标"未取到",thesis 据 L1 因子写。
- L4 slim 砍掉的块(OHLCV原始/全球宏观/做空/8×FRED/资产负债+现金流全表/期权/同业全表)**决策卡不得引用**——要它们就对该票跑全量。

---
# 附录(自足:以下内容不依赖 `docs/specs/`)

## 附录 A · 召回因子菜单(L1 内部,9 组 → tushare 端点)
L1 复合分 = Σ_组(组内因子 IC 加权 × 组权重),按申万一级条件化。9 组及其原始因子/端点:

| 组 | 原始因子(代表) | tushare 端点 | T+1 性质 |
|---|---|---|---|
| ① 动量/趋势 | pct_60d、pct_ytd、ma_bull(多头排列)、above_ma60 | daily / stk_factor_pro | **最强组(正)** |
| ② 资金·主力 | main_net_ratio=(大单+特大单 买−卖)/amount、main_inflow_yi | moneyflow | 1–2 周 swing,非 T+1 |
| ③ 资金·散户 | retail_net_yi=(小单 买−卖)、散户买卖比 | moneyflow | 反向参考 |
| ④ 筹码 | winner_rate、集中度=(cost85−cost15)/cost50、现价/cost50 | cyq_perf | 高 winner=抛压(**负**) |
| ⑤ 北向 | hk_hold ratio、近 N 日 ratio 变化 | hk_hold | 仅覆盖 ~5% 个股 |
| ⑥ 技术 | rsi6/rsi12、macd、vol_ratio、turnover | stk_factor_pro / daily_basic | 正;但单日量比超买偏弱 |
| ⑦ 成长 | np_yoy、rev_yoy、加速度、roe、cfo/毛利质量 | yjbb | 慢因子,L2/L3 兑现 |
| ⑧ 价值 | 行业内 PE/PB 低分位、dv_ratio 股息 | daily_basic / yjbb | 低 PE 在 T+1 反偏弱 |
| ⑨ **volprice** | **cmf_20(Chaikin 买卖压)、obv_mom_20(OBV 资金方向)** | daily ~20 日序列(`_harvest_vol_series`) | **多日量价资金流;decile +40bps/t≈2(正)** |

- 慢因子(④⑤⑦⑧大部)T+1 IC 小、权重自然低——价值在 L2/L3/L4 兑现。**全部仍随 top1000 带下去**(子分 + 原始列)喂粗排/精排。
- 缺端点权限 → 该列 NaN,打分按"有值子因子"重归一(降级不致命)。
- **两个确定性量价叠加**(`composite_score` 内,**不改 IC 权重**,只调召回顺序):**过热抑制 −8**(高动量 + 超买/获利盘满 = 见顶 leader)+ **吸筹加成 +5**(低位〔获利盘<40/破成本〕+ 放量〔量比≥1.5〕+ 主力未撤 = 底部疑似吸筹,小幅保召回)。+5 < |−8|:只保召回、不越级多报,真伪交 L2/L3/L4 三维验证。

## 附录 B · 召回权重校准 + L2 GBDT 训练(`factor_lab.py`,自足)
**目标 = T+1 远期收益**(用户选定)。四命令闭环(`harvest` 缓存供 `calibrate`/`train` 离线复用):
```bash
uv run --no-sync python scripts/factor_lab.py harvest     # 拉+缓存全市场面板(一次,慢;成型日越多 regime 越广)
uv run --no-sync python scripts/factor_lab.py calibrate   # L1:T+1 IC + 申万一级层级收缩 → weights.json
uv run --no-sync python scripts/factor_lab.py train       # L2:LightGBM 横截面排序 → gbdt_model.pkl(打印 oos vs 线性)
uv run --no-sync python scripts/factor_lab.py eval        # 复核 IC/十分位多空,确认再上线
```
**`train`(L2 粗排引擎)**:特征 = 8 因子组分位 + 20 原始因子 + 线性 composite 锚定;标签 = 每日横截面 rank-norm 的 fwd_1_oo;时序留 oos 比 **GBDT vs 线性 composite** 的 rank-IC。**`beats_linear=False` → `predict_scores` 回落线性,L2 用 composite top200**(自保,绝不比线性差)。`composite` 锚定特征让 GBDT 至少能复刻线性;薄面板上它多半只复刻、加不出稳健非线性 → 门关属常态,`harvest` 更多成型日再 `train` 才可能翻盘启用。
1. **无前视面板**:D 收盘出信号 → D+1 **开盘**买入,剔 D+1 一字板。
2. **逐因子 rank-IC**:每因子对 T+1 横截面 rank-IC,跨成型日聚合 → IC 均值 / IC-IR / t 值 / 十分位多空价差;两半样本稳定性分割。
3. **层级收缩**(解决申万一级样本少的噪声):`w(行业,因子)=λ·IC(行业)+(1−λ)·[λ₂·IC(大类板块)+(1−λ₂)·IC(全市场)]`,`λ=n/(n+k)`(k≈200);样本足/稳的行业更个性化,小行业回落基准。
4. **纪律**:只留**两半样本都稳、符号一致**的因子(据此历史砍掉 vol_ratio / winner_rate 进打分)。
5. **产物**:`weights.json`(`{行业:{因子:权重}}` + as-of/样本期/horizon/k),L1 读它打分,**权重与代码解耦**。改因子/组后必须重跑本闭环再上线。
> 改 `weights.json` 前先 `feedback_store.snapshot_weights()` 留快照,出问题可 `rollback_weights(sha)` 回滚。

## 附录 C · IC 实证基线(读校准块 / 写 prompt 的依据)
> **⚠️ 窗口 = regime,符号会翻**:`render_calibration_block` 注入的是 **live `weights.json`**,随校准窗口漂移。**近季(23 日)momentum/tech/volprice 为正**(下方详表);**近年(84 日)它们转负**(全市场组 IC:动量 −0.035、技术 −0.046、volprice −0.035、价值 +0.009、散户 +0.006)= **reversal regime**。用近季(动量延续)还是近年(均值回归)窗口是 **regime 选择**——这恰是『召回随 regime 漂移』的活样本,不是 bug。`weights.84d.json` 存了近年快照;`snapshot_weights()` 留每次校准。

下方为**近季(23 成型日 / ~10万行 / 110 行业,T+1 开到开)**详表(动量延续 regime):
- **组 IC(全市场)**:动量 +0.026、技术 +0.026 领先;**volprice +0.0276 并列最高**;北向 +0.014、散户 +0.012;主力净占比 −0.008、价值 −0.010 轻微负。
- **逐因子十分位多空(T+1,买得到)**:pct_60d **+68bps(t=2.6)**、above_ma60 +46bps(**t=3.7**)、ma_bull +39bps、rsi6 +49bps、**cmf_20 +40.8bps(t=2.0)**、**obv_mom_20 +44.3bps(t=2.0)** 为正;**winner_rate −42bps、vol_ratio −15bps、price_to_cost −37bps、低 PE/PB/股息 ≈ −50bps 为负**。
- **结论**:T+1 **动量 + 技术 + 多日量价(volprice)主导**;筹码/价值/单日量比弱或反向 → 复合分由快因子排序、符号 IC 驱动。**上面『因子方向经验校准』那几条反直觉结论就源自这里。**
- **诚实边界**:T+1 单 horizon、A股某段 regime;动量/资金类 regime 依赖。`weights.json` 带 as-of,建议定期重拟合;跨牛熊样本是 future work。

---
## 设计沿革(可选背景,删除不影响运行)
本文 + `SKILL.md` 自足。`docs/specs/` 仅存历史设计推演,供追溯**为什么**这么设计,**删掉不影响运行**(部分已落后于现实现,以本 skill 为准):
- `2026-06-20-scan-market-v2-design.md` — 六段漏斗 + 召回校准方法母文档
- `2026-06-20-l2-dual-lane-design.md` — L2 双赛道(趋势/回归)分桶
- `2026-06-21-cost-cascade-design.md` — 模型成本级联(Sonnet 宽段 / Opus 顶点)
- `2026-06-21-agent-upgrade-design.md` — C 评分卡 rubric / A 多空辩论 / B 3透镜共识 / E 记忆闭环 / F 各阶段 eval

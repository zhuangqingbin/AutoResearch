# scan-market screening-playbook — 六段漏斗操作参考

> **本文 + `SKILL.md` 自足:跑全流程、改因子、校准权重所需的一切都在这两份里,无需任何 `docs/specs/`。**
> 逐段写清 **引擎/模型 · 输入 · 规则 · 产物**;重点在 **L2 粗排 / L3 精排 / L4 三层级联**(你在 session 内扮演资深投资师与 PM 的判断步)。
> 召回因子菜单 + 权重校准方法 + IC 实证基线见**文末附录 A/B/C**。

## 漏斗一图
```
全A ~5,500 →(L0 选集·硬门)~4,300 →(L1 召回·复合分 top)1,000
   →(L2 粗排·分层多样性采样·零 LLM)200 →(L3 精排·holistic 单 agent 通看比较选 + 增量证据/论点/红队)~30
   →(L4 研究·一只=一个 Opus subagent 渐进深度 DD + 早停〔P0 简报→P1–P3 表面→主早停②→P4 陷阱核→③击杀→P5 满卡〕·~29 并发)~29 张
   →(买单独立 skeptic·≥OW 每只一个 Opus 证伪 + PM 3透镜裁判)~0–4 →(L5 整合)<运行时刻YYYYMMDD_HHMM>/{summary.md + details/〈名称〉 + trace/ + manifest.json〔记数据日〕}
```
**渐进深度 + 早停**:L4 = 一只 finalist = 一个 Opus subagent 跑 analyze-ticker-lite——**读够真数据才判,判断不好就早停**(出早停卡、不深挖),看着像买点的才走 P4 陷阱核 + P5 满卡。**全程 Opus 质量;省 token 靠早停**(多数 finalist 在 P3 主早停跳过深核 + 精雕)。**漏斗简报只定向不判**(信息薄,据它早停=误杀);**评级由 `rubric_rating` 评分卡派生**(防 gestalt 过度多报)。买点(≥OW)再过一道**独立 Opus skeptic** 证伪(发布前红队)。L0/L1/L2/L5 零 token。

## L0 选集 + L1 召回(`autoresearch.scan.universe`,确定性,零 token)
```bash
uv run --no-sync python -m autoresearch.scan.universe <date> --source tushare
```
- **L0 选集**:tushare 全市场富因子(daily_basic/daily×3/moneyflow 结构/stk_factor_pro/cyq_perf/hk_hold + yjbb 基本面)→ canonical 列;硬门 = 剔 ST/退/停牌/次新 + 市值地板(默认 30 亿)+ **含北交所**。
- **L1 召回(多路策略召回)**:Step A 轻门(只去不可交易/无核心数据,尽量不误杀)→ Step B **9 路 channel 各取 top-Kᶜ**(动量/反转/成长/价值 lens + 主力/北向/吸筹/**高热〔成交额量级〕** + **IC 校准复合分** channel〔9 因子组 × 行业条件化权重,读 `weights.json`〕;全复用 `common.scoring`,零新因子)→ **quota union 合并**(每路 floor 保底多样性 → 裁到 `--recall-n`〔默认 1000〕,不足从 composite backfill)。带 provenance `recall_channels`/`n_channels`(几路共振)。`--recall-mode composite` 回退单复合分(对拍/回退口径)。
- **`heat` 高热路(成交额主导,正交 composite)**:composite 是 T+1 IC 校准、**故意压抑**抛物线龙头(过热 −8/−15 + 主力出逃拖累)——像**中际旭创**(成交额全市场第 2、composite 仅 32)在召回近乎隐形。heat 只看『钱在哪』:`amount_yi × (1+0.15·pct换手+0.10·pct量比)`,`floor=50` 把成交额最大的票无条件送进 L2。**坑(实测)**:百分位混合不行(rank 把 386亿压成 0.9998、换手却 0→1 全摆 → surfaces 全是小盘换手股,龙头进不来);**成交额量级当乘法主轴**才稳锁龙头(实测全市场 heat rank #1)。捞回的是热门龙头,froth 真伪交 L3/L4。
- 9 因子组:①动量/趋势 ②资金·主力(净占比) ③资金·散户(小单净) ④筹码(集中度/相对成本) ⑤北向 ⑥技术(RSI/MACD) ⑦成长 ⑧价值(行业内) **⑨ volprice(多日量价资金流:CMF 买卖压 + OBV 资金方向;`_harvest_vol_series` 拉 ~20 日序列算,IC 实证 decile +40bps/t≈2、calibrate 全市场权重 0.0276=并列最高组)**。**因子→端点映射见附录 A、权重校准方法见附录 B**(符号由 T+1 IC 决定)。
- 产物:`L1_recall_top1000.csv`(复合分 + 9 子分 + 原始因子〔含 cmf_20/obv_mom_20〕+ **provenance `recall_channels`/`n_channels`**)、**`L1_channels.csv`(各路召回名单,复盘/学习用)**、`sectors.csv`(板块概览)、`meta.json`(漏斗计数)。
- **召回宽**:T+1 校准下复合分由快因子(动量/技术)主导,会把强动量/甚至过热票放进来——**这是故意的**(高召回),过热透支由 L2 剔。
- **多样性的去向**:多路召回保证 1000 池策略多样;**L2 分层采样按风格 floor 把这份多样性显式带到 200**(每风格保底,见 L2 节)——而非靠一个 champion 自由重排(那会把弱 regime 的票全压掉)。L3 holistic 拿到的是均衡菜单 + provenance(`n_channels` 几路共振)+ 各路命中供 retro 学习。
- **两个确定性量价叠加(不改 IC 权重,风险调整)**:**过热抑制**(高动量 + 超买/获利盘满 = 见顶 leader → 复合分 −8)+ **吸筹加成**(低位〔获利盘<40/破成本〕+ 放量〔量比≥1.5〕+ 主力未撤 = 底部疑似吸筹 → +5,小幅**保召回**)。后者是"底部放量"在 L1 的落点——只保证被召回进 top,**真伪交 L2/L3/L4 三维验证**(研究:底部放量 >70% 无基本面会败)。`vol_ratio` 已随召回 CSV 落地、贯穿 L2/L3/L4。

## ⚠️ 因子方向经验校准(L3/L4 通用,**务必写进每个 subagent prompt**)
> **运行时由闭环记忆生成**:构造 L3 subagent prompt 前,调
> `feedback_store.render_calibration_block(本批申万行业 scopes, with_feedback=True)` 取本块——三层叠加(优先级从高到低):**①近期同域未蒸馏反馈(E1·刚被你标错的坑,别再犯)→ ②自学习经验(retro 复盘;带 `〖硬门〗` 的已是 self_review 确定性拦截)→ ③IC 基线**;`context/knowledge/` 空 + 无反馈时**逐字回退**基线,老路径不破。取法:`uv run --no-sync python -c "import autoresearch.learning.feedback_store as fs;print(fs.render_calibration_block([('industry','电子'),('industry','医药')], with_feedback=True, regime='<当日regime>'))"`——**`regime` 传当日 `classify_regime` 标签**(meta.json/market_pack 有):带 regime 标注的经验只在适用 regime 注入(R1,防翻转中毒);经验条目 cap=8(R6)。下面是**基线**(人读参考):

来自 `factor_lab` 的 T+1 IC 回测(完整基线见**附录 C**),几条**与直觉相反**、上一轮测试中 L2/L3 误读、被 L4 反向打脸的:
- **高获利盘 winner_rate(>90)= 抛压/见顶风险,不是"筹码健康/顶配"**(十分位 −42bps)。低获利盘=套牢盘多=有上行空间。
- **高量比 / 高 RSI(超买)= T+1 偏弱**(vol_ratio −15bps);`pct_60d 极高 + RSI 高 + winner 满` = **抛物线顶 → 回避**,别当"强势延续"。
- **量价要分位置(关键)**:裸量比对 T+1 负(rank-IC t=−2.31 已剔出召回打分),**因为没分位置**——放量在**顶部=派发(空)**、在**底部=吸筹(多)**。`uzi_lenses.volume_price_signals(L1行)` 已按位置条件化:`量比↑ + 低位(获利盘<40/破成本)+ 主力未撤`=**底部放量吸筹→留/加分**;`量比↓地量 + 低位`=地量见地价;`高位放量 + 主力净出`=派发→砍。**警示:底部放量 >70% 无基本面会败 → 必须 L3/L4 三维验证(基本面+主力真在+估值),别只凭量价。**
- **多日资金流(已进 recall + L2 表)**:`cmf_20`(Chaikin 买/卖压)、`obv_mom_20`(OBV 资金方向)是**多日序列**指标,IC 实证比单日量比强得多(decile +40bps/t≈2,已是 volprice 组、calibrate 权重 0.0276)。读表时 **>0=买压/资金净进=吸筹侧、<0=卖压/派发侧**;与位置(获利盘/相对成本)共振更可信,仍须基本面背书。
- **主力**看 `main_net_ratio`(大单+特大单净占比),**散户**看 `retail_net_yi`(小单);主力净流入是 **1–2 周 swing** 信号,非 T+1。
- **价值(低 PE)在 T+1 反而偏弱**(成长/动量续涨);价值用于"不追高",非"次日动量"。
- **优先留**:涨幅适中(未过热)+ 主力真实进场(main_net_ratio 正)+ 筹码有空间(获利盘不满)+ 基本面干净;纯动量抛物线顶,L4 大概率 Underweight,别堆到精排顶端。

## L2 粗排(确定性分层多样性采样器,ML-free,1000→200)
> **`autoresearch.scan.recall.l2_stratify.select_l2`**,`universe.run` 与 `L2Rank` stage 共用(golden parity)。**已在 `universe.run()` 内自动产出**,无 subagent、无 reasoning 留痕。**为什么不是模型**:回测坐实**确定性 L2 无稳健 alpha**——别让它预测,只让它给 L3/L4 建均衡菜单;AI 判断**只在 L3/L4**。design:`docs/specs/2026-06-25-l2-stratified-sampler-design.md`。

**三件确定性的事**:
- **① 风格桶**(复用 `recall_channels` provenance,零新增):趋势(momentum/heat)· 反转(reversal)· 价值(value)· 成长(growth)· 吸筹(accumulation)· 主力(main_fund)。一只票可属多桶。
- **② 固定 floor(policy,非模型)**:`DEFAULT_FLOORS` 趋势20/反转12/价值12/成长12/吸筹12/主力10(Σ78,merit 核 122)。保证**每风格不为 0**(治本反转 regime 下"0 趋势"的落刀池)。`channel_ledger` 的 `unique_excess_t5` 持续为负且 n_days≥3 → 人工下调该桶 floor(写 proposals,不自动)。
- **③ sector-neutral composite 排序**:`sn = composite − 申万一级组均值`(去行业 beta)。merit 核 + 桶内都按 sn 排;`l2_sector_cap` 默认 0.20(任一申万一级 ≤40/200)。
**算法**:sn 排序 → merit 核取 top(200−Σfloor)过 cap → 逐风格(floor 大先)把不足 floor 的从线下按 sn 补 → 回填到 200。`floors={}`+`cap=1.0` → 退化纯 sn top200(parity 锚)。

> **回测实证(83 成型日 × 2022-2026,fwd_5,`scratchpad/bt_*.py`;附录 D)**:① **桶内 sector-neutral composite 最优**(每桶 per-bucket IC 最高;**风格自己的分反预测**——趋势按动量排 IC −0.076;cmf/obv/volprice 全桶负 → **不要落刀守卫**)② **确定性 L2 无稳健 alpha**:composite-top200 全样本 ≈ 0(t<1),**regime 依赖**(2022熊+20/2023+14/2024含动量+28/**2025-26反转 −24bps**)——"负-IC"是当前 regime 现象 ③ **分层免费**:strat ≈ composite-top200 ≈ 0 → 多样性零 alpha 代价。**结论**:L2 = 免费的多样性采样器,alpha 在 L3/L4。

> **为何彻底弃 ML(2026-06-25)**:全 zoo 20 模型 × 3 horizon **OOS rank-IC 全负**,xgb champion 也只是 −0.023("最不伤切");它在反转 regime 把**动量/heat 票全压出 L2**(实测 0/200,健康图形 1%)= 用户体感"形态全很差"。换模型种类无解(xgb 已最优)、扩**同 regime** 样本无解(训练窗已含 2024 动量仍负)。真要正-IC 需**换特征**(盈利修正/北向变化/行业动量)——大 R&D,非当前数据能解。`models/zoo` 基建保留为 **measure-only 研究**,**不接 L2**。

**产物**:`L2_gbdt_top200.csv`(`l2_rank`=分层选择序 + `gbdt_score`=composite〔显示用,旧列名〕 + `l2_lane_reserved`=floor 救回 + 召回因子列);`meta.l2_engine`=`stratified(sn_composite)`、`meta.l2_sector_cap`。**确定性层,无 LLM、无留痕**。

> **L2 已无 subagent / prompt 模板**——keep/cut 的主观判断上移到 L3 holistic 精排(那里一个 agent 通看 ~200 比较着选,把旧 L2 双赛道的"信号共振 / 排陷阱 / 趋势 vs 回归"判断一次做掉)。旧『因子方向经验校准』仍在 L3 注入(见上)。

## 首席策略师市场研判(L2 后,L3 前 · `autoresearch.scan.market`)
> **一次产出、三处复用**:L2 完(`L1_scored_full`+`sectors.csv` 就绪)派**一个 `Agent(model='opus')`** 读确定性数据包 `market_pack(scan_dir)`(regime/宽度/估值分散/资金/板块红黑榜)写 `context/scan/<date>/market_view.md`。**地形段(`market_context_block`)喂 L3 prompt + 每张 L4 卡简报做 regime 校准(防锚定:只描述不指令)、操作基调/漏斗读数只进 L5 报告**。数字全出自 pack(不编数)。
> **产出分层**:1–3 节=描述性地形(L3/L4 读);4–5 节=规范性+前瞻(仅 L5)。**为什么这样切**:一段"避险别追"的 house view 会把 20 张 L4 卡带成集体附和 → 破坏"每只独立自下而上 DD + rubric 防 gestalt 多报";喂卡片的必须是**数字地形**(校准估值/资金门严格度),不是方向指令。

**首席策略师 prompt(模板)**:
> 你是一名**资深 A 股投资大师 / 首席策略师**。下面是今日全市场确定性数据包(`market_pack`,数字不可编造)。写一段 ~300–400 字的市场研判 `market_view.md`,**6 小节**:
> 1. **一句话定调**(regime + 结构 + 情绪,如"避险哑铃:AI 半导体极致拥挤 + 宽基超跌落刀");
> 2. **市场结构**(宽度〔多少票站上 MA60〕/ 主力资金净流向 / 估值分散〔哑铃两端〕);
> 3. **板块红黑榜**(强 top3 / 弱 bottom3,各一句 why);
> 4. **操作基调**(基于 regime 的整体仓位姿态;**规范性,仅 L5 用**);
> 5. **关注**(催化日历:中报窗口 / 政策会议 / 解禁);
> 6. 收尾"仅供研究,非投资建议"。
> **铁律**:前 3 节是**描述性地形**(会喂 L3/L4 校准,**不得含个股买卖指令 / 不得对具体票定方向**);第 4–5 节才是规范性 + 前瞻。**个股评级只由 L4 rubric 三门决定,你的研判不改判、不锚定卡片**。
> 数据包:`<market_pack(scan_dir) 的 JSON>`

## L3 精排(holistic 单 agent:一次通看 ~200、比较着选 ~30)
> **holistic > 逐只孤立打分**:一个 agent 通看整张 ~200 行表、横向比较着选,把旧 L2 双赛道的"信号共振/排陷阱/趋势 vs 回归"判断 + 精排一次做掉。孤立逐只打分各看各的、易虚高;比较式天然控总量、强制相对排序。
> **多 persona 对抗(UZI 思维,可选增强)**:必要时对**入围候选**可再用多个 subagent 扮不同流派(价值/成长/游资/quant/风险官)各自引因子复核,**分歧大就把分歧本身写进结论、不取均值抹平**(「矛盾必须呈现」)。`uzi_lenses.trap_signals(L1因子行)` 做风险官的机械底(获利盘满/过热/派发命中即压 conviction);`uzi_lenses.volume_price_signals(L1因子行)` 做游资/技术派的机械底(底部放量吸筹/地量企稳/缩量回调=量价转多→`bias=吸筹` 抬 conviction,但**须基本面背书**;`bias=派发` 压 conviction)。
> **发布前硬门**:`autoresearch.scan.assemble` 已接 `self_review` —— 买单若踩经验红线(winner_rate>88 无 override)/ 覆盖不足 / 评级-因子矛盾 / 行业过度集中 / 空泛话术,summary 顶部出 🛑 banner,**先修根因再信报告**。结构化经验(`lessons.jsonl` 带 `guard:{field,op,value}`)自动并入硬门。
**目标**:对 200 补 L1 没有的**真证据**,一次通看比较着选 ~30 并红队压测。慢因子在此兑现。

**步骤**:
1. 增量取数:`harvest_l3_evidence(date, l2_top200_codes)` → `L3_evidence/<code>.json`(龙虎榜席位 / 业绩预告 / 快报);**`harvest_l3_news(date, l2_top200_codes)`**(`autoresearch.scan.agents.l3_news`)→ `L3_news/<code>.json`(近 ~10 日 `anns_d` 公告标题,**入湖按 ann_date、L4/analyze 复用**;无权限降级空)。**`harvest_l3_web_news(date, l2_top200_codes)`** → `L3_webnews/<code>.json`(akshare 个股新闻 `stock_news_em`,**入湖 as_of、免费 keyless、逐股降级隔离**)。
2. **一个 holistic subagent,`Agent(model='opus')` + high reasoning**(全表一次通看,非逐只 → 成本仍小):`l3_table_md(date)` 把 ~200 只(因子 + 证据摘要 `lhb_n/has_forecast/has_express` + **公告情感 `news_*` + 媒体情感 `med_n/med_tags/med_head`〔akshare〕** + **召回 provenance `n_channels/recall_channels`〔几路共振〕**)压成**一张紧凑表**喂它,**通看全表、横向比较着选 ~30**(每只入选出 论点/红队/催化/确信度/脆弱度/lane/**sentiment**),落 `L3_judged_full.csv`。量大可拆 2–3 个 holistic 片,但**每片仍是"比较着选"而非逐只孤立**。
3. **`merge_l3_finalists_v2(judged_df, target=30, trend_quota=10, hybrid=True)`** → `context/scan/<date>/finalists.csv`(把 holistic 入选排成 finalists + 趋势配额安全网)。
   - judged_df 需含列:`code,name,sector,lenses,conviction,fragility,thesis,risk,catalyst,triage_lean,lane,pct_60d`(`lane`/`pct_60d` 配额用,源自 L2 表)。
   - **趋势配额(安全网)**:纯 `conviction−fragility` 会把高 fragility 的强势票挤出(实测:生益+205%/亨通+158% conv 高但 frag 高 → 进不了 top30)。`merge_l3_finalists_v2` 给 trend lane 保底 `trend_quota` 席,**一半按 conviction(质量趋势:健康强势)+ 一半按 pct_60d(动量龙头:最热的票)**(hybrid)——高 fragility 是 T+1 概念,swing 不该一票否决。捞进来后由 **L4 做估值/解禁尽调定级**(实证:抛物线顶 PE160~440 + CFO负 + 解禁 多半 Underweight/Sell,质量强势如胜宏 PE77 才 Overweight)。
   - **`l2_lane_reserved=True`(L2 风格 floor 救回的票)**:被 sn_composite 排在 merit 核之外、靠风格 floor 塞进 200 的票(趋势/价值/成长等各风格的保底席)。judge **倾向打 `lane="trend"`**(若是趋势/题材龙头),让 `trend_quota` 在 200→30 接住;但**照常用 rubric 诚实定级**——多数应在 L4 被三门否掉,留的是尾部真龙头。floor 的意义=**保证 L3 永远看得到每种风格**(治本反转 regime 下"0 趋势"的落刀池),不是替它们背书。

**L3 holistic 选股 prompt(模板)**:
> 你是资深 A股投资人 + 风险官 + PM。下面是 L2 粗排出的 ~200 只紧凑表(因子 + 龙虎榜/预告/快报摘要 + **近期公告情感 + 召回 provenance**)。**先内化『因子方向经验校准』**(上节,`render_calibration_block` 注入)。**再读顶部『市场地形』段(`market_context_block`,首席策略师研判)——按 regime 加权资金确认/避落刀、校准估值容忍度;但选股仍由下方 5 维 rubric 定,不因大盘定调整体偏多偏空。****一次通看全表、横向比较**,选出最值得深研的 ~30 只——**趋势 + 回归兼顾,别全堆抛物线顶,别只挑 composite 顶(反羊群)**。
> **比较 rubric(5 维,逐只权衡、给"为何此刻选它")**:① **channel 共振**(`n_channels`/`recall_channels`:多路召回=多策略确认,3+ 路共振优先;`l2_lane_reserved=True`=L2 风格 floor 救回的票,倾向 lane=trend)② **资金确认**(main_net_ratio/lhb_n:主力真在)③ **基本面支撑**(growth/value 子分 + np_yoy/roe)④ **情感**(公告 `news_tags` + 媒体 `med_tags`/`med_head`:回购/增持/中标=利多;减持/质押/问询/立案=利空、压 conviction;公告与媒体两路共振更可信)⑤ **脆弱度**(过热/见顶/利空公告)。
> **比较着选**:同板块/同因子画像的票互相比、只留最强的;陷阱直接弃(高位放量派发 / winner满主力撤 / 低PE但 np<0 / 抛物线无主力承接 / 近期减持·问询·立案);**底部放量吸筹 + 基本面背书 + 多路共振**优先(`volume_price_signals`/`trap_signals` 机械底辅助);趋势票**不因"涨多"误杀健康强势**(主力还在+业绩跟得上),回归票看低位空间(低获利盘=空间)。
> **内化校准**:满仓获利盘/winner>90 在主力撤/业绩证伪时=见顶,主力还在则不是。
> **每只入选输出**(CSV `code,name,sector,lenses,conviction,fragility,thesis,risk,catalyst,triage_lean,lane,pct_60d,sentiment`):thesis≤25字多头论点(落因子/证据)、risk≤25字最大证伪点(**必须真,不许橡皮图章**)、catalyst≤15字时点(无则"无明确催化")、conviction/fragility 0–100、triage_lean 看多/中性/回避、lane trend/reversion、**sentiment 利多/中性/利空 + 一句依据(据公告标题)**。
> 紧凑表:`<l3_table_md(date)>`
> **FinGPT 借鉴(记录)**:采纳「情感即特征」(公告 digest 喂 holistic);**不**跑 FinGPT 模型(Claude 即情感引擎,更强且零 API);`anns_d` = FinNLP 新闻连接器的免费等价;FinGPT 的 market-feedback(情感 vs 价格验证)→ 留 `learning/` retro 用前瞻收益验证 L3 情感判断(后续 phase)。

## L4 研究(一只 = 一个 Opus subagent · 渐进深度 + 早停)
对 `finalists.csv` **每只一个 `Agent(model='opus')`** 跑 analyze-ticker-lite:`l4_card.compose_funnel_brief(code, scan_dir)` 拼漏斗简报 → **前置到该票 `harvest --slim` 产出的 slim 顶部** → subagent **渐进深度 DD + 早停**(读够真数据才判,判断不好就早停、不深挖)。**~29 个 subagent 一条消息并发派发**;每只独立 context、只回传 评级/目标/R:R/早停与否。**全程 Opus,省 token 靠早停**(多数 finalist 在 P3 主早停跳过深核+精雕);无 Tier-2 平反(base 已是 Opus,无 Sonnet 误杀要救)。

**取数 + 简报**:每只 `harvest <ticker> <date> --slim`(~13KB,已重排表面前/深核后 + `<!-- P4 深核分界 -->`)→ `compose_funnel_brief` 拼简报前置 slim 顶 → 决策卡 staging `context/scan/<date>/details/<ticker>.md`:
```bash
uv run --no-sync python -m autoresearch.analyze.harvest <ticker> <date> --slim
```
> **推荐操作姿势(2026-06-24 实跑验证)·确定性数据层与 LLM 分析层解耦**:① **先**用一个**确定性批脚本**把全部 finalist 的 slim 一次性 harvest 好(zero-LLM,`xargs -P 6` 控并发、逐只验 ≥8KB;harvest 自动复用 scan 目录的 L1 因子、只 live 取深块)—— 既对齐"取数=脚本、判断=Claude"的项目铁律,又能在烧 Opus 前先验证数据完好。② **再**把全部 ~30 个**分析 subagent 一条消息并发派发**(读预建 slim + `compose_funnel_brief` 简报,**不再各自并发打 tushare/akshare → 无限频风险**)。**别分 wave**:slim 既已预建,拆 wave 只多几个同步 barrier 的墙钟延迟、不提质量(各卡独立、同输入同输出);reviewability 用抽检 1–2 张回卡换,不用串行 wave 换。
> **渐进深度 + 早停(详见 `lite-playbook.md`)**:P0 读简报定向 → P1–P3 表面 DD 填 4 表面维(技术资金/基本面/估值/催化)→【**主早停②**:加不起买点 → 早停卡止】→ survivor 读 P4 深核分界后做陷阱核(CFO/质押/商誉/周期顶)【③击杀】→ P5 满卡。**评级由 `l4_card.rubric_rating` 评分卡派生(防 gestalt 过度多报)+ 3 道 OW 硬门(主力真在/业绩真兑现/估值不透支)**;**早停只向下、任何 ≥OW 必走 P4+P5**(绝不在早停点发买单)。
> **📁 档案增量研究(R5)**:简报若带『个股档案』块(该票近日入围过,`dossier.render_dossier` 自动注入),卡片**必须含"变化项(vs 档案)"一小节**——逐条对已知证伪点答【已变/未变/新证据】;**未变的门引档案一句带过不再长篇重证,变了的才展开**(增量研究,省 token 靠这里)。铁律:档案是历史事实非预判,本次评级仍由本卡 rubric 三门独立定。
> **防误杀铁律**:不在读到翻盘牌〔催化/forward PE/吸筹〕前早停 → 主早停 = P3 后;漏斗简报只定向不判(信息薄,据它早停=误杀)。
> **📐 阶段效能契约**:survivor 进 P4 前,卡片记一行 `进入P4倾向: <五档Rating>`(P3 时点的倾向评级)。`health.l4_phase_stats` 据此测 **P4 翻盘率**——若长期≈0,陷阱核就可条件化(先测量后动刀);写了不白写,一行而已。

> **🔎 web 外源催化(P3/P5,finalists 专属)**:做催化核(P3)/ 终判(P5)时对该股 **WebSearch**(`<名称> <代码> 最新 研报/突发/政策/订单`)→ 提炼 **1–2 条真·催化 + 时效**(日期/来源)纳入 `催化`/`风险`。**边界**:仅定性佐证——评级/目标/数字仍出自 slim;无网/无结果 → 跳过。**早停卡不做深 WebSearch**(省 token)。

**回卡后**:主线 `ratings = parse_ratings_from_details('context/scan/<date>/details')`(无 Tier-2 平反——base 已是 Opus)。

- **复用召回因子,不重算(已落到代码层)**:`autoresearch.analyze.harvest --slim` 在 scan 目录(`context/scan/<date>/`)能找到该只的 L1 行时,**自动**用 L1 因子(主力净占比/散户/筹码/北向/技术/复合分+8子分)重建『主力/技术/筹码/北向』块 —— **零 tushare 重复取数、与召回数字一致**;`autoresearch.analyze.harvest` 只 live 取 L1 没有的深块(个股新闻/利润表/偿付/卖方目标/解禁,及 L4 才增量的 股东户数·质押/业绩预告·快报)。判断 subagent 仍把该 L1 行塞进 prompt 供推理。**A股价格真值走 tushare(`load_ohlcv` 对 .SS/.SZ/.BJ 前复权),北交所可用、与召回同源,不走 yfinance。** 想要 10 日资金序列/MACD 明细 → 对该票跑**全量 analyze-ticker**(非 slim,live 重取更全)。
- subagent 独立 context、**只回传 评级/目标/R:R**;主线只收小结果。量大可选 **workflow** 并行(需用户显式开启)。
- 某只想下重注 → 再单独跑**全量 analyze-ticker**(模型 **Opus**,live 重取最全)。

## 买单独立 skeptic(发布前红队,~0–4 只)
把省下的预算重投到**最贵的决策点**:**最终评级 ≥OW 的发布买单**每只派一个**独立** `Agent(model='opus')` 专职**证伪**——它没参与过该票分析、对多头故事零包袱,比 subagent 自压更抗自我合理化。主线当**组合经理(PM)裁判**用 3 透镜投票定 verdict。错一个买点 = 真金白银。

> **为何独立 skeptic 而非自压**:survivor 的 P5 多空对撞已是**多头自己 steelman**(它建的论点);这里只需一个**独立空头**把它拆了 + 中立 PM 裁判,比同一个 agent 自辩稳(自辩易轻描淡写自己的 bear case)。这不是「tier」(不对全 29 铺一层),是发布前最后一道闸。

**步骤**:回卡后主线 `candidates = pick_buy_candidates(ratings)`(最终 Buy/OW)。每只:
1. **独立空头 skeptic**(`Agent(model='opus')`,**不看 subagent 的满卡多头稿**):证伪买点(攻击面见下),产物 `context/scan/<date>/_v_<code>.md`,**只回传一句最强空头 + 触发位**。(subagent 满卡里的多头论点即 bull 方,无需另起多头 agent。)
2. **PM 裁判(主线你自己,非另起 subagent)**:读 subagent 满卡的多头 + skeptic 的空头,**3 透镜各投一票**:① **估值透镜**(空头估值证伪是否成立)② **资金面透镜**(主力承接 vs 派发,谁的证据硬)③ **毁灭风险透镜**(解禁/质押/业绩雷的尾部概率)。多数票定 verdict、记票型;写/追加一行到 `context/scan/<date>/verify.csv`(表头 `code,verdict,bull,bear,trigger,consensus`)。

> **verify.csv 一行**:`<code>,<维持|降级|否决>,"<≤20字最强多头·禁英文逗号>","<≤20字最强空头·禁英文逗号>","<触发位:价/指标/事件>","<共识:如 维持3/3 或 降级2/3(估值/资金)>"`。降级/否决 → 在 `details/<code>.md` 顶部加一行 `> ⚠️多空辩论:<bear>`。
> **verdict 口径(按 3 透镜票)**:维持=≥2 透镜判多头赢(空头无证伪买点的硬证据);降级=2:1 偏空、有真实下行但不致命(评级降一档);否决=≥2 透镜判否、买点被实锤推翻(估值透支/解禁砸盘/业绩证伪)。

**两研究员共用攻击面**(空头逐条找最强反面、多头逐条防守):① 估值(PE/PEG 分位、Bear 情景概率)② 解禁/质押(时点+比例)③ 主力背离(承接是否消失、`main_net_ratio` 转负)④ 业绩雷(预告/快报/应收·存货·商誉)⑤ **前视偏差**(证据严格 ≤ 分析日,无未来信息泄漏)⑥ 筹码派发(获利盘满 + 放量滞涨)。

`autoresearch.scan.assemble` 据此:**① 折回评级**——`降级`→降一档(OW→Hold,踢出买单)、`否决`→至少 Hold(买单不挂系统自己都不信的评级);**② 归档** reasoning/verify/(多空两稿 + verify.csv);**③ summary** 买单行带徽标(✅维持/⚠️降级/🛑否决)+ 多空辩论明细块(多/空/触发/共识)。

> 与既有 `self_review` 机械硬门**叠加且正交**:self_review 是确定性红线(winner>88 无 override / 覆盖 / 评级-因子矛盾 / **评级超 rubric** / 行业集中 / 空泛),本闸是 LLM 临场多空对抗找**新** bear/bull 证据;summary 同时呈现。

## 机会成本红队(0买日,skeptic 之后 · 对称性修复)
**为什么**:买单有 skeptic,空仓从来没有——连续 0 买后系统无法自证"门太紧还是市场真没货"。空仓也要红队。
**何时**:verify 折回后**今日 0 买**(无 ≥OW)才跑;`l4_card.pick_opportunity_candidates(ratings, scan_dir, k=2)` 取 rubric/conviction 最高的 Hold top-2。
**怎么跑**:每只派一个**独立** `Agent(model='opus')` 当 **bull 方**(镜像 skeptic:没参与过该票分析、只演多头),prompt 要点:
- 输入:该股决策卡(details/<code>.md)+ 漏斗简报;人设=错过成本审计员。
- 任务:**攻"把它压在 Hold 的那道 binding gate"**——这道门(主力/估值/业绩兑现)的反证是什么?什么**可核实**证据出现时该门会翻转?
- 铁律:**不改评级、不喊单**;数字出自卡片与 slim,不编数。
- 输出(紧凑回传):最强多头 3 条 + `binding_gate` + **翻转触发**(用观察单词表:`close_above/close_below/ma_bull/money_pos/manual`)+ 一句风险自认。
**PM 裁判(主线,3 透镜:估值/资金/毁灭风险)**:采纳 → 写进 `context/watchlist.csv`(结构化 conds,source=opp_redteam);不采纳 → verify reasoning 归档记一句 why。**产出只进观察单与校准数据,评级一个字不动**(这是证据流,不是翻案通道)。

## L5 整合(`autoresearch.scan.assemble`,确定性)
```bash
uv run --no-sync python -m autoresearch.scan.assemble <date>
```
读 `meta.json` + `L1_recall_top1000.csv` + `L1_scored_full.csv` + `L2_gbdt_top200.csv` + `finalists.csv` + `details/<ticker>.md`(用 `parse_rating` 提五档 + 仪表盘),发布到 **`reports/scan/<YYYYMMDD_HHMM>/`**(目录名 = **实际运行时刻**;数据日 analysis_date 落 `manifest.json`,与目录名解耦,`retro._report_dir_for` 据此定位):
- `summary.md` 三段:**①漏斗数量(带引擎列)②各阶段卡点+股票概览 ③投资建议**——buy-list 是**逐阶段结论宽表**(每只 `名称/板块 | L1召回〔#名次·命中队列〕| L2粗排〔#重排名次·gbdt〕| L3精排〔论点·conviction〕| L4研究·结论〔深核定级依据:≥OW 取多头驱动 / 否则取空头·早停因,`_l4_brief`〕| 评级 | 目标 | 置信度 | 买单 skeptic 徽标 ✅维持/⚠️降级/🛑否决`,**已删 代码/R:R/提案 列**)+ 组合视角 + 局限。表头四阶段并列 **L1召回→L2粗排→L3精排→L4研究·结论**,把"看多论点(L3)→ 为何定级(L4)"的转折显式呈现。**注:命中队列等多路 provenance 在单元格内用 `/` 连接(非 `|`),否则劈裂 markdown 表格列**。
- **`## 各阶段 token 消耗(估算)`**:分阶段引擎/LLM 调用数/输出字节/~token(L0/L1/L2 确定性=0;L3/L4/买单 skeptic 按落盘推理稿字节 ÷2.8 粗估)。**口径诚实**:输入侧(slim 上下文)未全留痕→真实数倍于此表,为可测下界。
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

## 附录 B · 召回权重校准(L1 live)+ zoo 训练(measure-only)(`autoresearch.research.factor_lab` / `models.zoo`,自足)
> **`calibrate` 仍 live**(产 `weights.json`,L1 复合分 + L2 分层采样的 sn_composite 底分都读它)。**`train`/zoo champion 现为 measure-only 研究,L2 不再用模型**(见 L2 节:回测证确定性 L2 无 alpha → 改分层采样)。下列闭环仍可跑(复核因子 IC、做 zoo 研究),只是产物不接 L2。
**目标 = T+1 远期收益**。四命令闭环(`harvest` 缓存供 `calibrate`/`train` 离线复用):
```bash
uv run --no-sync python -m autoresearch.research.factor_lab harvest     # 拉+缓存全市场面板(一次,慢;成型日越多 regime 越广)
uv run --no-sync python -m autoresearch.research.factor_lab calibrate   # L1:T+1 IC + 申万一级层级收缩 → weights.json
uv run --no-sync python -m autoresearch.research.factor_lab train       # L2:LightGBM 横截面排序 → gbdt_model.pkl(打印 oos vs 线性)
uv run --no-sync python -m autoresearch.research.factor_lab eval        # 复核 IC/十分位多空,确认再上线
```
**`train`(L2 粗排引擎)**:特征 = 8 因子组分位 + 20 原始因子 + 线性 composite 锚定;标签 = 每日横截面 rank-norm 的 fwd_1_oo;时序留 oos 比 **GBDT vs 线性 composite** 的 rank-IC。**`beats_linear=False` → `predict_scores` 回落线性,L2 用 composite top200**(自保,绝不比线性差)。`composite` 锚定特征让 GBDT 至少能复刻线性;薄面板上它多半只复刻、加不出稳健非线性 → 门关属常态,`harvest` 更多成型日再 `train` 才可能翻盘启用。
1. **无前视面板**:D 收盘出信号 → D+1 **开盘**买入,剔 D+1 一字板。
2. **逐因子 rank-IC**:每因子对 T+1 横截面 rank-IC,跨成型日聚合 → IC 均值 / IC-IR / t 值 / 十分位多空价差;两半样本稳定性分割。
3. **层级收缩**(解决申万一级样本少的噪声):`w(行业,因子)=λ·IC(行业)+(1−λ)·[λ₂·IC(大类板块)+(1−λ₂)·IC(全市场)]`,`λ=n/(n+k)`(k≈200);样本足/稳的行业更个性化,小行业回落基准。
4. **纪律**:只留**两半样本都稳、符号一致**的因子(据此历史砍掉 vol_ratio / winner_rate 进打分)。
5. **产物**:`weights.json`(`{行业:{因子:权重}}` + as-of/样本期/horizon/k),L1 读它打分,**权重与代码解耦**。改因子/组后必须重跑本闭环再上线。
> 改 `weights.json` 前先 `feedback_store.snapshot_weights()` 留快照,出问题可 `rollback_weights(sha)` 回滚。

- **per-channel 前向归因(`stage_eval` L1 段 + `channel_ledger`)**:retro 评估每只召回票的 T+5 **截面超额**(个股 fwd − 全市场中位),按 `recall_channels` provenance 归到各路 → `context/scan/<date>/retro/channel_eval.csv`。头条看 **`unique_excess_t5`**(仅此一路独占票的超额 = 边际 alpha:这路有没有找到别人没找到的赢家),buyable-aware(D+1 买不进的剔出 + `n_unbuyable` 计数);另带 `n_channels` 共振 rank-IC(多路共振是否预测,验证 merge tiebreak)。**单日是噪声**;跨日滚动:`uv run --no-sync python -m autoresearch.learning.channel_ledger` → `reports/learning/channel_ledger.md`(`n_days<3` 标 ⚠样本少)。**measure-only**:据此人/scan-retro 决定调不调某路 quota,不自动改。

## 附录 C · IC 实证基线(读校准块 / 写 prompt 的依据)
> **⚠️ 窗口 = regime,符号会翻**:`render_calibration_block` 注入的是 **live `weights.json`**,随校准窗口漂移。**近季(23 日)momentum/tech/volprice 为正**(下方详表);**近年(84 日)它们转负**(全市场组 IC:动量 −0.035、技术 −0.046、volprice −0.035、价值 +0.009、散户 +0.006)= **reversal regime**。用近季(动量延续)还是近年(均值回归)窗口是 **regime 选择**——这恰是『召回随 regime 漂移』的活样本,不是 bug。`weights.84d.json` 存了近年快照;`snapshot_weights()` 留每次校准。

下方为**近季(23 成型日 / ~10万行 / 110 行业,T+1 开到开)**详表(动量延续 regime):
- **组 IC(全市场)**:动量 +0.026、技术 +0.026 领先;**volprice +0.0276 并列最高**;北向 +0.014、散户 +0.012;主力净占比 −0.008、价值 −0.010 轻微负。
- **逐因子十分位多空(T+1,买得到)**:pct_60d **+68bps(t=2.6)**、above_ma60 +46bps(**t=3.7**)、ma_bull +39bps、rsi6 +49bps、**cmf_20 +40.8bps(t=2.0)**、**obv_mom_20 +44.3bps(t=2.0)** 为正;**winner_rate −42bps、vol_ratio −15bps、price_to_cost −37bps、低 PE/PB/股息 ≈ −50bps 为负**。
- **结论**:T+1 **动量 + 技术 + 多日量价(volprice)主导**;筹码/价值/单日量比弱或反向 → 复合分由快因子排序、符号 IC 驱动。**上面『因子方向经验校准』那几条反直觉结论就源自这里。**
- **诚实边界**:T+1 单 horizon、A股某段 regime;动量/资金类 regime 依赖。`weights.json` 带 as-of,建议定期重拟合;跨牛熊样本是 future work。

## 附录 E · 事件研究:业绩预告 L1 通道(**负结果**,2026-07-02,勿重启)

**动机**:7 月中报预告窗(深市触线 7/15 前强制)把"正面预告(预增/略增/扭亏/续盈)"升为 L1 第 10 路召回。**方法**:tushare `forecast(ann_date)` 全市场事件,入场 = ann 次日开盘,fwd_1_oo / fwd_5_oc 对全市场同口径均值取超额(`scratchpad/forecast_event_study*.py`)。**两季对照**:

| 季 | n | T+1 超额 | T+5 超额 | T+5 胜率 | 钝反应预增(gap<3%) |
|---|---|---|---|---|---|
| **2025-07 中报季**(强制为主) | 566 | −0.05% | **−0.27%** | **35%** | +0.26% / 胜率 38%(右偏彩票) |
| 2026-04 一季季(自愿为主) | 113 | +0.45% | +1.63% | 52% | +1.25% / 胜率 52% |

- 跳空组(gap≥3%)两季皆负(2025-07:T+5 **−2.92%**)——**公告后追缺口必亏**。扭亏类 T+5 −1.05%(卖事实)。幅度(p_change_min≥50%)无增量。
- **判读**:A股预告是瞬时定价/提前泄露;**强制披露季(即中报窗)无 edge**,自愿早鸟季的正信号来自披露自选择(好公司才抢先报)= **预期/盈利修正信息**,不是"事件后追买"信息。
- **裁决**:**不建 L1 事件通道**。预告 awareness 留在既有位置:L3 证据表 `has_forecast/has_express` + L4 催化日历。自愿披露的信息量并入**盈利修正因子**(一致预期 EPS Δ)设计——那是事前信息,理论上站得住。

---
## 设计沿革(可选背景,删除不影响运行)
本文 + `SKILL.md` 自足。`docs/specs/` 仅存历史设计推演,供追溯**为什么**这么设计,**删掉不影响运行**(部分已落后于现实现,以本 skill 为准):
- `2026-06-20-scan-market-v2-design.md` — 六段漏斗 + 召回校准方法母文档
- `2026-06-20-l2-dual-lane-design.md` — L2 双赛道(趋势/回归)分桶
- `2026-06-21-cost-cascade-design.md` — 模型成本级联(Sonnet 宽段 / Opus 顶点)
- `2026-06-21-agent-upgrade-design.md` — C 评分卡 rubric / A 多空辩论 / B 3透镜共识 / E 记忆闭环 / F 各阶段 eval

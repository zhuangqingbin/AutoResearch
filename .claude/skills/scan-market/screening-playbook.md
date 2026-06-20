# scan-market screening-playbook — 六段漏斗操作参考

> 读这一份就能跑 scan-market 全流程。完整规格在 `docs/specs/2026-06-20-scan-market-v2-design.md`;
> 本文是操作向蒸馏 + **L2 粗排 / L3 精排**(你在 session 内扮演资深投资师的两步)的具体规则。

## 漏斗一图
```
全A ~5,500 →(L0 选集·硬门)~4,300 →(L1 召回·复合分 top)1,000
   →(L2 粗排·AI keep/cut)200 →(L3 精排·增量+论点/红队)~30
   →(L4 研究·analyze-ticker-lite 卡)~30 张 →(L5 整合)<HHMM>_summary.md + A_pipeline/
```
token 花在 L4 的 ~30 张卡(每只 ~20%)+ L2/L3 的扇出判断;L0/L1/L5 零 token。

## L0 选集 + L1 召回(`screen_market.py`,确定性,零 token)
```bash
uv run --no-sync python scripts/screen_market.py <date> --source tushare
```
- **L0 选集**:tushare 全市场富因子(daily_basic/daily×3/moneyflow 结构/stk_factor_pro/cyq_perf/hk_hold + yjbb 基本面)→ canonical 列;硬门 = 剔 ST/退/停牌/次新 + 市值地板(默认 30 亿)+ **含北交所**。
- **L1 召回**:Step A 轻门(只去不可交易/无核心数据,尽量不误杀)→ Step B **行业条件化复合分**(8 因子组 × 申万/东财行业的 IC 校准权重,读 `weights.json`)→ 全市场排序 top `--recall-n`(默认 1000)。
- 8 因子组:①动量/趋势 ②资金·主力(净占比) ③资金·散户(小单净) ④筹码(集中度/相对成本) ⑤北向 ⑥技术(RSI/MACD) ⑦成长 ⑧价值(行业内)。权重符号由 T+1 IC 决定(spec §9)。
- 产物:`L1_recall_top1000.csv`(复合分 + 8 子分 + 原始因子)、`sectors.csv`(板块概览)、`meta.json`(漏斗计数)。
- **召回宽**:T+1 校准下复合分由快因子(动量/技术)主导,会把强动量/甚至过热票放进来——**这是故意的**(高召回),过热透支由 L2 剔。

## ⚠️ 因子方向经验校准(L2/L3/L4 通用,**务必写进每个 subagent prompt**)
> **运行时由闭环记忆生成**:构造 L2/L3 subagent prompt 前,调
> `feedback_store.render_calibration_block(本批申万行业 scopes)` 取本块——你的反馈 + retro 复盘学到的
> **自学习经验会叠加在下面的 IC 基线之上**(优先级更高);`context/knowledge/` 空时**逐字回退**下面的基线,
> 老路径不破。取法:`uv run --no-sync python -c "import sys;sys.path.insert(0,'scripts');import feedback_store as fs;print(fs.render_calibration_block([('industry','电子'),('industry','医药')]))"`。下面是**基线**(人读参考):

来自 `factor_lab` 的 T+1 IC 回测(spec §实证),几条**与直觉相反**、上一轮测试中 L2/L3 误读、被 L4 反向打脸的:
- **高获利盘 winner_rate(>90)= 抛压/见顶风险,不是"筹码健康/顶配"**(十分位 −42bps)。低获利盘=套牢盘多=有上行空间。
- **高量比 / 高 RSI(超买)= T+1 偏弱**(vol_ratio −15bps);`pct_60d 极高 + RSI 高 + winner 满` = **抛物线顶 → 回避**,别当"强势延续"。
- **主力**看 `main_net_ratio`(大单+特大单净占比),**散户**看 `retail_net_yi`(小单);主力净流入是 **1–2 周 swing** 信号,非 T+1。
- **价值(低 PE)在 T+1 反而偏弱**(成长/动量续涨);价值用于"不追高",非"次日动量"。
- **优先留**:涨幅适中(未过热)+ 主力真实进场(main_net_ratio 正)+ 筹码有空间(获利盘不满)+ 基本面干净;纯动量抛物线顶,L4 大概率 Underweight,别堆到精排顶端。

## L2 粗排 v2(确定性分桶 → 双赛道 LLM → 配额合并,1000→200)
> v2 设计:`docs/specs/2026-06-20-l2-dual-lane-design.md`。解决两实测问题:① **强势股被一刀切**(过热红线误杀健康强势)② **token 大**(Opus 判全 1000)。
**目标**:确定性层先分掉"明显留/明显砍"(零 LLM),只让**模糊带**进 LLM;强势股走**趋势 lane**(不砍强势、只辨健康 vs 衰竭),回归股走**回归 lane**,合并时给趋势 lane **保底席位**。

**步骤**(`scan_pipeline.py` 供分桶/切片/合并):
1. **L2a 确定性分桶(零 LLM)**:`l2_pre_bucket(recall)` → 每只加 `regime/resonance/healthy_strong/exhausted/l2a_action/l2_lane`,落 `context/scan/<date>/L2a_bucketed.csv`。
   - `auto_keep`(强共振无衰竭)+ `auto_cut`(衰竭破/平庸)**免 LLM**;`llm` 桶按 `l2_lane` 进 L2b。典型 1000 → auto_keep ~150 / auto_cut ~450 / llm ~400。
2. **L2b 双赛道 LLM(只判 llm 桶,模型用 Sonnet)**:
   - `for lane in ('trend','reversion'): slice_l2_llm(bucketed, lane, batch_size=100)` 切片,`compact_table(batch, lean=True)`(12 列省 token)出表。
   - **每批一个 subagent,`Agent(model='sonnet')`**(粗筛不需 Opus);趋势 lane 注入 `render_calibration_block(scopes, lane='trend')`,回归 lane 注入默认校准块。**只回传 keep CSV**。
3. **配额合并**:`merge_l2_keeps_v2(auto_keep_df, trend_keeps, reversion_keeps, recall, target=200, trend_quota=50)` → `L2_coarse_keep200.csv`(含 `l2_lane/l2a_action/l2_score`);并落全量 `L2_scored_full.csv`(召回 1000 + 全标签 + `l2_kept` 布尔)。
   - 中间件(`_l2_prompt_*.md / _l2_batch_*.md / _l2_keep_*.csv / _calib*.md / L2a_bucketed.csv`)由 `assemble_scan` 归档到 `A_pipeline/reasoning/l2/`。

**趋势 lane subagent prompt(模板)**:
> 你是资深 A股投资人,判一批**强势/趋势票**(已确定性预筛为趋势/疑似过热)。**先内化『趋势延续 lane 校准』**(`render_calibration_block lane='trend'`):动量为正、强势延续是默认、**winner 满/超买在主力还在时不是卖点**,只砍衰竭顶。
> **rubric**:keep = 健康强势(`main_net_ratio≥0` 主力还在 + `np_yoy>0` 业绩跟得上 + 板块共振 / 龙虎榜接力);cut = 衰竭顶(放量滞涨 `main_net_ratio` 深负 / 业绩证伪 `np<0` / 满获利盘且主力流出 / 抛物线且主力不在)、纯题材无主力承接、量价背离。**不要因"涨多了"就砍**。
> **输出**:keep 的 CSV `code,l2_score,l2_reason`(l2_score 0–100,reason ≤15 字,禁英文逗号)。约留本批 top 40–60%(趋势 lane 召回更宽,交精排核)。
> 紧凑表(lean):`<compact_table(batch, lean=True)>`

**回归 lane subagent prompt(模板)**:
> 你是资深 A股投资人,判一批**低位/回归票**。**先内化默认校准块**(高获利盘=抛压、低获利盘=有空间、主力看 `main_net_ratio`)。
> **rubric**:① 信号共振(多组子分一致看多→keep);② 排陷阱:放量滞涨/派发、价值陷阱(低 PE 但 `np_yoy<0`)、筹码松散+高获利盘抛压、北向持续流出;③ 不确定但高潜 → keep(召回优先,交精排核)。
> **输出**:keep 的 CSV `code,l2_score,l2_reason`(reason ≤15 字,禁英文逗号)。约留本批 top 30–40%。
> 紧凑表(lean):`<compact_table(batch, lean=True)>`

## L3 精排(你扮演资深投资师 + 风险官,subagent 扇出,200→~30)
> **多 persona 对抗(UZI 思维,可选增强)**:对每只 finalist 可用多个 subagent 扮不同流派(价值/成长/游资/quant/风险官)各自引因子下判断,**分歧大就把分歧本身写进结论、不取均值抹平**(「矛盾必须呈现」)。`uzi_lenses.trap_signals(L1因子行)` 做风险官的机械底(获利盘满/过热/派发命中即压 conviction)。
> **发布前硬门**:`assemble_scan` 已接 `self_review` —— 买单若踩经验红线(winner_rate>88 无 override)/ 覆盖不足 / 评级-因子矛盾 / 行业过度集中 / 空泛话术,summary 顶部出 🛑 banner,**先修根因再信报告**。结构化经验(`lessons.jsonl` 带 `guard:{field,op,value}`)自动并入硬门。
**目标**:对 200 补 L1 没有的**真证据**,逐只形成观点并红队压测,精排出 ~30。慢因子在此兑现。

**步骤**:
1. 增量取数:`harvest_l3_evidence(date, keep200_codes)` → 每只 `context/scan/<date>/L3_evidence/<code>.json`(龙虎榜席位 / 业绩预告 / 快报;无权限端点降级标注)。
2. **每只(或小批)一个 subagent**,给该只的 L1 因子行 + evidence json,用下面 prompt 出判断。
3. 主线收齐 → **先把所有 judged 按 确信度−脆弱度 排序落 `L3_judged_full.csv`**,再 **`merge_l3_finalists_v2(judged_df, target=30, trend_quota=10, hybrid=True)`** → `context/scan/<date>/finalists.csv`(top30)。
   - judged_df 需含列:`code,name,sector,lenses,conviction,fragility,thesis,risk,catalyst,triage_lean,triage_reason`,**并 merge 进 L2 的 `lane`(trend/reversion)+ `pct_60d`**(配额用)。
   - **趋势配额(v2,对症 L3 瓶颈)**:纯 `conviction−fragility` 会把高 fragility 的强势票挤出(实测:生益+205%/亨通+158% conv 高但 frag 高 → 进不了 top30)。`merge_l3_finalists_v2` 给 trend lane 保底 `trend_quota` 席,**一半按 conviction(质量趋势:健康强势)+ 一半按 pct_60d(动量龙头:最热的票)**(hybrid)——高 fragility 是 T+1 概念,swing 不该一票否决。捞进来后由 **L4 做估值/解禁尽调定级**(实证:抛物线顶 PE160~440 + CFO负 + 解禁 多半 Underweight/Sell,质量强势如胜宏 PE77 才 Overweight)。

**L3 subagent prompt(模板)**:
> 你是资深 A股投资人 + 风险官。标的 `<code> <name>`(`<sector>`)。L1 因子:`<该只 recall 行关键字段>`。增量证据:`<evidence json:龙虎榜席位/业绩预告/快报>`。
> **输出**(严格)::
> - `thesis`:一句多头论点(≤25 字,落到因子/证据);
> - `risk`:红队——最大单一风险 / 证伪点(≤25 字,**必须真,不许橡皮图章**);
> - `catalyst`:近期催化时点(≤15 字;无则"无明确催化");
> - `conviction`:0–100 多头确信度;
> - `fragility`:0–100 脆弱度(被证伪的容易程度);
> - `lean`:看多/中性/回避。
> **按 lane 选口径**:`trend` lane(强势票)用**趋势延续**判(主力还在+业绩跟得上=健康强势,winner满/超买不因涨多扣分,只压衰竭顶);`reversion` lane(低位票)用**低位反转**判(低获利盘=空间)。注入对应 `render_calibration_block(lane=...)`。
> **内化校准**:满仓获利盘/winner>90 在主力撤/业绩证伪时=见顶,主力还在则不是;据此定 conviction/fragility,别把抛物线顶当顶配、也别把健康强势一票否决。
> 精排合并见上(`merge_l3_finalists_v2` 趋势配额 + hybrid),非纯 `确信度−脆弱度`。

## L4 研究(委托 analyze-ticker-lite,~20% token/只)
对 `finalists.csv` 每只,**逐只 subagent** 跑 analyze-ticker-lite(读其 `lite-playbook.md`):
```bash
uv run --no-sync python scripts/harvest_context.py <ticker> <date> --slim   # slim 取数,每只 ~13KB(≈全量 20%)
# → 决策卡 staging 到 context/scan/<date>/details/<ticker>.md
```
- **复用召回因子,不重算(已落到代码层)**:`harvest_context --slim` 在 scan 目录(`context/scan/<date>/`)能找到该只的 L1 行时,**自动**用 L1 因子(主力净占比/散户/筹码/北向/技术/复合分+8子分)重建『主力/技术/筹码/北向』块 —— **零 tushare 重复取数、与召回数字一致**;`harvest_context` 只 live 取 L1 没有的深块(个股新闻/利润表/偿付/卖方目标/解禁,及 L4 才增量的 股东户数·质押/业绩预告·快报)。判断 subagent 仍把该 L1 行塞进 prompt 供推理。**A股价格真值走 tushare(`load_ohlcv` 对 .SS/.SZ/.BJ 前复权),北交所可用、与召回同源,不走 yfinance。** 想要 10 日资金序列/MACD 明细 → 对该票跑**全量 analyze-ticker**(非 slim,live 重取更全)。
- subagent 独立 context、**只回传 评级/目标/R:R**;主线只收 ~30 条小结果。量大可选 **workflow** 并行(需用户显式开启)。
- 某只想下重注 → 再单独跑**全量 analyze-ticker**。模型建议 **Opus**。

## L5 整合(`assemble_scan.py`,确定性)
```bash
uv run --no-sync python scripts/assemble_scan.py <date>
```
读 `meta.json` + `L1_recall_top1000.csv` + `L2_coarse_keep200.csv` + `finalists.csv` + `details/<ticker>.md`(用 `parse_rating` 提五档 + 仪表盘),发布到 `reports/scan/<YYYYMMDD>/`:
- `<HHMM>_summary.md` 三段:**①漏斗数量 ②各阶段卡点+股票概览 ③L4 投资建议(buy-list + 组合视角 + 局限)**。
- `<HHMM>_detail/`:决策卡 + `A_pipeline/` **每阶段全量数据**(L0计数 / **L1_scored_full 全打分排序(4000+,非仅1000)** / L1_weights / **L2_scored_full 全 keep-cut** / L2保留 / **L3_judged_full 全判断** / L3最终入选 / funnel.md 溯源)。
- 缺卡的 finalist 标 `⚠️卡片缺失`。

## 数据坑
- **默认 `--source tushare`**(东财 push2 常被网络封锁)。富因子缺端点权限 → 该列 NaN、打分重归一。**北向 hk_ratio 仅覆盖 ~5% 个股**(北向只持一部分),小盘多为 NaN(north 组只对有北向的票生效)。
- **召回权重非拍脑袋**:`factor_lab.py`(tushare 全市场 rank-IC 回测,T+1 校准 + 申万行业层级收缩)产 `weights.json`。**实证结论**(spec §实证):T+1 上动量(pct_60d 十分位多空 +68bps/t=2.6、above_ma60 t=3.7)+ 技术(RSI)为正;量比/winner_rate/价值(低PE)/price_to_cost 为负或噪声;故复合分动量+技术主导。改因子/组后跑 `harvest`(一次)→`calibrate`→`eval` 复核再上线。
- **业绩披露滞后** → 用最近可得报告期(脚本按分析日推算)。**L3 增量** top_list/forecast/express 若无 token 权限 → evidence 标"未取到",thesis 据 L1 因子写。
- L4 slim 砍掉的块(OHLCV原始/全球宏观/做空/8×FRED/资产负债+现金流全表/期权/同业全表)**决策卡不得引用**——要它们就对该票跑全量。

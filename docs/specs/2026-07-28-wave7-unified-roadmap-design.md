# Wave7:统一 roadmap —— 新闻时效 × token/速度第三刀 × 保送持仓自进化 × 修缮批(design)

> **已被取代（2026-07-28）**：当前统一方向、优先级、0-BUY 根因模型与实施波次，以
> `docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md` 为准。
> 本文保留为 Wave7 事故证据、机制细节和已实施事项的历史记录，不再承担当前 roadmap 职责。

> 2026-07-28 定稿(07-27 夜 brainstorm,**只落文档,不实施**)。起因:用户五问——①最新报告之后的遗留优化;②活体新闻以近 24h 为主(尤其收盘→跑报时段),面要够大不漏蛛丝马迹;③token 再省(不伤流程);④速度再快;⑤自进化能力(L4 推的 + 保送的)。
> 证据基线:run `20260727_2140`(数据日 2026-07-27,含 CP7 真计量首读 + 一次 frame 事故全记录)。所有数字实测,估算处显式标注。
> 上游:`docs/specs/2026-07-27-wave6-unified-roadmap-design.md`(Wave6;批 A/C 已实施、批 B 部分已实施)。历史上 Wave7 曾取代 Wave6 承担调度；当前调度已由页首所链统一总纲接管，本文与 Wave6 均退居机制参考。

## 用户已拍板(2026-07-27/28 brainstorm)

1. **只落文档**:本波不写代码;文中批次是下次开工的实施蓝本。
2. **题② 新闻面:只重构 intel,不建新确定性快讯层**(wire 层记入附录 B「未选路径」,防将来重提时丢上下文)。蛛丝马迹的覆盖面因此停在 finalist ~10 只——这是用户知情后的取舍。
3. **题⑤ 保送侧:全套**(pinned_ledger 对错账本 + tripwire 机器盯梢),与 07-17「保送不算」裁定不冲突——那条是防污染**选股复盘**口径(t1_review/retro 的 L3 edge),本文的持仓账本独立分表,选股复盘永不读它。

## 0. 定位与红线

### 0.1 一句话诊断

Wave6 的主脊(跑动裁决/壳降 haiku/同档早止/预热前置)已生效并在 07-27 run 活体验证;但 **R1 retro 欠账不仅没清、还在恶化**(07-16/17/21 备料未收尾 + 新增 07-24 对),同时 07-27 实跑逮到 **7 件新修缮实锤**(其中 4 件是"探针自己坏了"型:计量正门、两处 lint 假阳、早停账本空转)。本波三件事:修缮批止血、intel 时效重构(用户点名)、保送持仓自进化补腿——并把"腿没人踢"的节律病用夜间自动化根治。

### 0.2 红线(沿 Wave6 §0.2 全部 + 本波新增)

Wave6 红线原文不重抄,全部继续有效:不放松买入门 / 不动早停机制 / 不建当日大涨召回 / 超短 T+2 主尺 / L2 不用模型 / 52 周高不复活 / 反思注入克制(ATLAS)/ 不建 FTS5/向量库/常驻回测 / 改生产行为 = 读数触发 + 用户点头。

本波新增:

| 红线 | 出处 |
|---|---|
| **不建 7×24 快讯确定性层**(wire) | 本波用户裁定(附录 B 记未选路径与解锁条件) |
| **sector-brief 的 model/effort 不动** | 07-12 用户拍板(sonnet 回滚 + xhigh),无新数据不重提 |
| **pinned_ledger 与选股复盘物理分表** | 07-17「保送不算」裁定的防火墙;retro/t1_review 的任何口径不得读它 |
| **lint 假阳修法排序:补指令 > 给合法情形标记 > 才是加严检查** | instruction-vs-check-mismatch(2026-07-27 归档,3/3 是自己没写要求) |

## 1. 现状账本(run 20260727_2140 读数)

### 1.1 漏斗与战绩

- L0 4,039 → L1 1,000 → L2 203 → pass1 40 → L3 finalist 7(+3 保送)→ 10 卡 → **0 买**(Hold 6 / UW 3→4 / Sell 1→折回 UW)。连续 0 买第 6 日。
- OW 三门失守分布(10 卡):**主力真在 ✗ 7** · 业绩真兑现 ✗ 3 · 估值不透支 ✗ 3——与策略师地形(反弹日主力净额为正仅 47.12%、CMF 为正 38.65%)互证:无主力跟随的情绪反弹。
- 停因分桶:早停 4(基本面恶化 2 / 题材透支 1 / 其他 1)· 满卡未达 OW 6。
- 影子组合(起 20260618):真实 −0.24%(9 笔)vs 无门影子 −3.69%(69 笔)vs sized −6.83% vs 市场等权 −11.01% → 门价值 ≈+3.45pp,空仓相对市场 +10.8pp。
- 保送三持仓:300857 卡判 Sell → sell_review 三票 [Sell,UW,?] 取中位折回 **UW**;601869 UW(sell_review 两票同档早止确认,FINAL=SELL);688766 Hold。双复核机制(Wave5 ②D + 07-21 教训)全程正常。

### 1.2 token 真计量(CP7 首读,glob 后门)

45 subagent:原始 19.52M → **加权 4.26M**(cache读×0.1/5m写×1.25/1h写×2)· 输出 613.7k · cache 命中 **89.7%**。较 07-24 追溯值(5.49M/50 agent)**降 22%**(T1 壳降 haiku + T2 早止 + pass1 40 的合成效果)。

| 桶 | 加权 | 占比 | 备注 |
|---|---:|---:|---|
| gp 壳(haiku)~14 只 | ~1.13M | 26% | **$ 口径是 opus 零头**——分模型计价列(T8)未出前勿再按加权裁它 |
| l4-card(opus·max)×13 张 | ~1.32M | 31% | 含 300857 三跑 ensemble |
| l4-intel(sonnet·max)×8 | ~0.81M | 19% | |
| l3-rank ×2 | 332k | 7.8% | 含 lint-fix 断连白烧 56.9k |
| sector-brief ×7 | ~0.46M | 10.8% | TTL 复用 2/9 |
| macro-brief ×2 | 206k | 4.8% | **双跑 = frame 事故连带**(首跑 BLOCKED 120k) |

⚠️ 主会话不在表内(--session 正门坏,见 §3 B'-a);覆盖声明:下界语义。

### 1.3 耗时

总 89m38s(常规段;含事故实际墙钟 ~106m):L0L1L2 2m16s / 策略师 4m08s / 行业 brief 9m44s(∥)/ **L3 精排 14m50s**(vs 07-24 21m33s,pass1=40 生效)/ L4slim 1m06s / **L4 研究 22m35s** / ensemble 14m20s / assemble 1m35s / 预热 14m50s(**launchd 19:30 真前置成功**,扫描 19:54 起全湖命中)。

### 1.4 事故与探针失效(本波修缮的证据源)

1. **frame 裸奔事故**:东财 `stock_yjbb_em` 断流(`_ak_call` 3 重试全败)→ 退出码 1 + `>` 留 0 字节 `market_pack.json` → macro-brief 正确拒写(BLOCKED),但 workflow 不查返回,若非人工摁停,L3 将无地形段静默开跑。已在 `scan-market.js` 补 `pack-check` 门(test -s + 重试 + 仍空则响亮记账降级)——**已落盘未提交**。同族前科:空 pickle/空 slim。
2. **usage_harvest 正门坏**:`--session` 只 glob `subagents/agent-*.jsonl`(非递归),workflow transcript 在 `subagents/workflows/wf_*/` → 报"无 transcript"。glob 后门(`--transcripts '**/agent-*.jsonl'`)可用。降级是响亮的(明说无),但 CP7 正门失效。
3. **price_claims 审计器假阳 2 型**:①卡内**引用并拒绝**的 intel 断言被当卡片自己的断言(601211 卡明写「与本股 verified −1.4% 未对账,不采信」仍被告);②EPS 隐含增速(601869 fwd-EPS「+637% 跃升」)被当价格断言;另〔转引标题〕《601869,涨停!》被计数。真捏造与假阳混在同一 warn 流里,狼来了效应会磨掉 P0 探针的公信力。
4. **l3_select lint 假阳 3 型**:15 处告警中大部分为——派生算术(100−73.92→「26%」)/ 窗口标签(「20 日吸筹」「60 日中位」)/ 跨文件引用(market_view 的 −17.68%、+489 亿)。修法排序见红线。
5. **earlystop_ledger 空转**:今晚 4 张早停卡,账本仍「无早停记录」——ledger 期待的卡头 `**早停**` 行没人写,而 gate_hist 用另一套解析(标题〔早停·…〕)能读出停因 = **同一事实两套解析器,一套瞎**。Wave6 R3 验收④ 正式记 **✗**。
6. **workflow 单 agent 断连无重试**:L3-lint-fix『API Error: Connection closed』即死,56.9k 加权白烧,数字修复未执行(15 处告警本身多为假阳,故本次无实害;机制在)。
7. **l4_reuse 双盲区**(memory 已归档):`pinned` 零出现(持仓被 TTL 复用旧卡 → 绕过 sell_review;`force_full` 只管哨兵不管 reuse);`price_tol=0.05` 量绝对涨跌幅——普涨日 688766 跑输市场 7.3pp 仍被判「没动」。07-27 人工绕过(重研 2 只),实证:重研评级与复用卡一致(本次复用其实判对了)——**机制缺陷坐实,当日无实害**;裁决要靠账本不靠单例。
8. **progress.py 三次误报**(累犯,pr_20260717_004):把 l3-rank 的输入当「精排中」、把复用卡计进新卡数、9/8·10/8 溢出。E1 退役条件已在(直播验收后删)。
9. **intel 捏造复发 + 限频超限**:赤峰黄金 intel 3 条 07-17 价格断言全错(实为 −8.32%)——「行情数字不自报(本票)」铁律在 def 里但没拦住;2 稿自报超限(18/16 > cap 15),对账探针工作、无强制力(指令级,pr_20260714_007)。601918 的 07-20「涨停」warn 为 ♻️复用卡从 07-24 携带的历史断言二次报警(去重缺失)。

### 1.5 遗留欠账(跑动侧)

- **retro 慢环**:07-16/17/21 备料未收尾(retro_input 有、done.json 无 = 诊断烂尾)+ 07-24 于 07-28 收盘成熟待 attribute。
- **t1 快环**:07-24→07-27 对未跑;07-27→07-28 对将于 07-28 收盘产生。
- 档案池:28/30 已建(余 600285/600535);📐 季度对账 25 只(period=20251231)待跑;`dossier.reconcile 20260630` 8 月中报季首跑(日历项)。
- open proposals 14 条(目标 ≤10,E2 顺延):其中 pr_20260721_002(当日切面)**已实现可关**;pr_20260727_003(板块动量关闭)已裁待关账;intel 三连(_006/_007/16_003)保持 open 至 Q1 收口;event 路(pr_20260725_001)~08-08 裁决。

## 2. 主线 R′:跑动清偿(P0,零开发,排在一切开发前)

- **R′1 retro 三日诊断收尾 + 新账**:07-16/17/21 的 retro_input 已备料,只差 scan-retro 诊断段 + mark_done;07-28 收盘后 attribute 07-24,随后 t1-review 补 07-24→07-27 与 07-27→07-28 两对。**07-27 对特别有料**:0 买 + 三持仓 UW/Hold 判断 vs 07-28 实现,是 pinned_ledger(§6)未来账本的第一笔手工对照样本。
- **R′2 E2 关账一批**(零代码,记 resolution 不悄改):pr_20260721_002(today_slice 已上线)、pr_20260727_002/_003(批 C 已裁)、cap_floor 家族三条(Wave6 已定合并关账)→ open 14 → ≤9。
- **R′3 档案收尾**:600285/600535 两只 dossier-init;25 只 20251231 对账择夜分批(≤5 只/晚,📐 计数应逐晚下降——恒定 = 探针坏)。

## 3. 批 B′:修缮七件(小刀,~1-2 天,每件独立可回滚)

> 纪律:TDD + 变异探针(绿灯必须会变红);`node --check` 对 workflow js 零鉴别力,一律 AsyncFunction 探针。

- **B′-a usage_harvest 正门修复**(§1.4-2):`collect_dir()`(usage_harvest.py:124)`d.glob("agent-*.jsonl")` → 兼收 `d.glob("workflows/*/agent-*.jsonl")`(或 `rglob`,注意排除非本 session 混入;`--dir` 语义同步)。验收:`--session <id>` 与 `--transcripts` glob 输出同表;CP7 runbook 撤掉 glob 后门。回滚:还原 glob。
- **B′-b price_claims 审计器去假阳**(§1.4-3):`audit_card_text` 豁免——①**元话语整句不认领**(转述/否决/负判:`转引标题`/`非本票行情自陈`/`intel 称`/`未对账`/`不采信`/`该价格断言`/`非涨停`/`非跌停`/`未见于`);②`_FUND` 补一致预期派生量(`隐含`/`一致预期`/`预期差`/`fwd`)当第二道独立防线;③同一 (日期,类型,值,dir) 断言在卡内**只报一次**;④self_review 措辞按 `dir` 分涨/跌停(原先 limit 一律播「称 涨停」,600988 的跌停断言被播成涨停 = 方向讲反)。**先加豁免再谈升 fail**(红线排序)。
  - ⚠️ **本条的实施前归因已被数据推翻,验收标准据此改写**:动工前查实 07-27 那 4 条 warn 是 **4/4 假阳**,不是原文以为的「2 真 2 假」。原归因把**两个不同审计层**混为一谈——(i) self_review 探针读**卡片正文**,(ii) assemble 发布层的 🔎 块读 **intel 稿**。真捏造在 intel 稿里且 (ii) 一直工作正常;(i) 报的 4 条全是元话语误判,其中 600988/601211 两张卡**正在拿 OHLCV 对账并否决 intel 的断言**,却被判成自己捏造。
  - 验收(已跑通):07-27 十张卡重放 → 卡片正文不符 **4 → 0**;intel 稿口径**仍逮到 2 条真捏造**(600988 `20260717` 实 −8.32%、688766 `20260709` 实 9.81%)= 探针没被关掉。变异探针:守卫置空后 4/4 向量变红。
- **B′-c l3_select lint 白名单**(§1.4-4):三类合法引用给标记而非加严——l3-rank 人设补一句「引用表外数字须带源注(如 (market_view))」;lint 豁免:带源注的数字、`\d+日` 窗口标签、表内两列可推导的派生算术(100−x、差值)。验收:07-27 的 15 处重放,假阳清零、真错(若有)保留。
- **B′-d earlystop_ledger 看不见「刚跑完那一天」**(§1.4-5):在 assemble 收尾(`is_real` 块,journal 刷新旁)补刷 `earlystop_ledger` 与 `gate_ledger`。
  - ⚠️ **本条的实施前归因同样被数据推翻**:动工前查实 `_early_stop.json` 一直正常落盘、`roll()` 当场就能读出与 gate_hist 逐条一致的 4 行(基本面恶化 2 / 其他 1 / 题材透支 1)——**解析器没坏,也不存在「两套解析器」**。真病是**时序**:账本刷新挂在 prelude(07-27 20:11,跑前),而它的输入由 assemble 在 21:40(跑后)写 → 账本恒定落后一个 run。原文写的「改解析器复用 gate_hist」是修错了地方。
  - 同族扫描结论(「发现一处必查同族」):prelude 刷的 10 个账本里,`gate_ledger`(读 `gate_fires.csv`,同由本次 assemble 写)是同一个病,一并修;`journal`/`buy_ledger` 读 `finalists.csv` 属另一类——它们本就是聚合历史,跑前刷拿到截至昨日的全量,**设计如此,不动**。
  - 验收(已跑通):补刷后 `earlystop_ledger.md` 出 4 行、停因与 `render --view gate_hist` 一致;契约测试锁调用点存在性(删掉那段 for 循环即变红)。
- **B′-e workflow 断连轻重试**:`scan-market.js` 的 lint-fix 调用包 try/catch:失败 → log 降级继续(现状已不阻断,只是白烧+静默);可选单次重试(medium)。同款检查 l4-stock.js 各 agent()(card 失败已有显式 error 返回,intel 失败已回退——仅 lint-fix 裸奔)。验收:AsyncFunction 探针 + 模拟 null 返回路径。
- **B′-f l4_reuse 持仓豁免 + 相对阈值**(§1.4-7):`reuse_decision()` 加 `pinned` 形参(调用侧从 `load_pinned()` 取)——pinned → 永不复用(与「≥OW 永不复用」对称:买点必须重研,**持仓判断也必须重研**);`price_tol` 判据从 |Δ价| 改 |Δ价 − 市场中位 1d|(`today_slice.median_pct_1d`,缺则回退绝对口径并记账)。验收:07-27 场景重放 → 688766/601869 判「不复用」;非持仓 601918/600018 仍复用。回滚:形参缺省 False = 现行为。
- **B′-g frame pack-check 守卫收编**:07-27 已落盘的 `scan-market.js` edit(pack-check 门 + 重试 + 响亮降级)补契约说明后提交;连带把 macro-brief 双跑(BLOCKED 首跑 120k)记为该守卫的止损案例。验收:AsyncFunction 探针过 + 下次实跑 journal 出现 pack-check 行。

**顺延收编(Wave6 批 B 未做部分)**:Q6 剩余(conviction 回传契约钉死 0-100 整数 + lint(workflow 侧 normConviction 是创可贴)、601869 slim_deep 1,280B 离群查 harvest deep 分支、run_health 时序 quirk、L3_news 203×2B 空文件 producer 侧不落盘)、E1 退役(progress.py:R3 直播已验收 ✓ → 本波可删,删前 grep 消费者;OTEL 已退役 ✓;bytes÷2.8 估算表已由 CP7 取代,summary 模板撤估算行)。

## 4. 批 N:intel 时效重构(题②,纯 prompt/契约/lint,零新端点)

### 4.1 动机

现契约「新闻近 3-5 日、公告近 5 交易日」与超短 T+2 主尺错位:>1 周的事件大概率已 price-in(07-27 活体:300857 intel 自己把 07-19 预告标「已消化超 1 周」净分仍 +1),而**收盘→跑报时段(15:00→~21:00)的增量**——次日唯一可交易的新信息——没有任何显式优先级。

### 4.2 契约改动(`.claude/agents/l4-intel.md`)

1. **面 2 三窗化**:
   - **T0(收盘→跑报时刻)= 必查面**:当日 15:00 后的公告/快讯/异动说明,查不到明写「盘后无增量」(合法输出,不许静默跳);
   - **T1(近 24h)= 主力窗**;
   - **T2(1-5 日)= 背景窗**(降格,每面 1 轮无料即收)。
   - 查询预算显式倾斜:**≥60% 花在 T0+T1**(15 条 cap 下 ≈9-10 条);公告面(面 1)同步改「近 5 交易日,T0 增量优先」。
2. **净分时效衰减**:事件行净分 × 时效系数——T0/T1 ×1、2-5 日 ×0.5(衰减后保留一位小数,如 +2→+1.0)、**>1 周 ×0(默认已 price-in)**;唯一例外:**未兑现催化**(将来时点事件,如「8/25 中报披露」)不衰减,标 `催化挂`。
3. **「2日内可发酵?」列升格「时效窗」列**:取值 `T0/24h/背景/催化挂`——事件日期 vs 分析日可机检。
4. **蛛丝马迹加深(面不加,深度加)**:现有「产业链价格异动」例外条款扩为:每票允许 1-2 条**上下游/同题材 24h 异动**定向查(仍禁写本票行情数字)。
5. **声明行扩展**:`T0面=<有增量/盘后无增量>` 进声明行,供机检。

### 4.3 lint 配套(advisory 起步,攒 3 跑误报率再议升 fail——Wave6 惯例)

- 时效窗列 vs 事件日期自动对账(窗标错 → warn);
- T0 面缺失(既无事件行也无「盘后无增量」)→ warn;
- 净分未衰减(>1 周事件净分非 0 且无 `催化挂`)→ warn。
- Q1 四件套收口不变:`intel零URL` 与 `price_claim_mismatch` 在 **B′-b 去假阳之后**再走 warn→fail 升格(否则假阳会把 fail 变成狼来了);限频超限(07-27 两稿 18/16>15)保持对账 warn,强制力升格挂「连续 3 跑超限」触发。

### 4.4 下游衔接

- l4-card 的 P3 读 intel 时,`催化挂` 行进决策卡催化段;`T0` 行若为负面(盘后利空)必须进陷阱核对(P4)。
- intel A/B 账本(~08-08 结算)裁决口径同步更新:A/B 定义 = 「三窗 intel」vs「无 intel」(旧 A/B 中的旧版 intel 数据作废起点重计,或按上线日分段——实施时择一并记录)。

## 5. 批 T:token/速度第三刀(题③④,两把尺分开裁)

### 5.1 token 尺

- **T8 收口(最优先,B′-a 的孪生)**:CP7 正门修好后,token_usage 表增**分模型计价列**(haiku/sonnet/opus 单价折算 $)——今晚 haiku 壳加权占 26% 但 $ 是 opus 零头,**不出 $ 列就裁下一刀 = 大概率重演 30× 方向错误**;主会话窗口计入 CP7(触发「SKILL.md 精简第二期」与否的读数,Wave6 挂账条件 >25% 沿用)。
- **ensemble spread 账本落地**(Wave6 T2 挂账):`_ensemble_*.json` 已有 spread/n_runs/early_stopped/trigger,建 `learning/ensemble_ledger.py` 聚合(与 §6 pinned_ledger 共用折回对错判定)。攒 **n≥5** 裁「SELL 复核 3 跑→1 跑」;攒买单 **n≥10**(现 9,下一单即触发)裁「OW 复核降档」。
- **不动**:sector-brief model/effort(用户拍板);intel 开关(A/B ~08-08);L3 紧凑表再瘦(Wave6 已判大概率关闭)。

### 5.2 wall 尺

- **T9 intel 前置(最大剩余杠杆,−4~6min)**:GATE2 出 finalists 即在 `scan-market.js` L4-prep 相位与确定性生产者**并行**派 intel(intel 输入只需 code/name/sector/date+档案摘要,不依赖 prompts/slim);`l4-stock.js` Intel 相位改 presence-check(`_l4_intel_<code>.md` 已存在且非空 → 跳过)。代价:GATE3 剔股时该股 intel 白跑(~90k 加权/只;GATE3 剔股率历史极低);dossier_summary 在 dispatch-plan 之前需从 pool 直取(实施时核对 `dossier.pool` 接口,标注:此处是本设计唯一未验证假设)。回滚:删并行块,intel 回 l4-stock 内。
- **pass1 40→35**:挂影子验证(07-18 方法复用,mandatory 0 漏才动)——L3 已 14m50s,此刀降级为「有空再做」。
- **ensemble 尾巴不动**:card→run2→run3 串行是 T2 的 token 优先设计;分歧(跑满 3)场景今晚 1/2,样本不足不重开。
- 预期:常规日 89m → **~78-82m**;frame 类事故不复发另省 ~15m(B′-g 已堵)。

## 6. 批 P:保送持仓自进化全套(题⑤,用户已选)

> 防火墙红线:本批所有产物**只进自己的账本与汇总屏提醒**,retro/t1_review/L3 edge/权重重标定一概不读——「保送不算」裁定原样成立。

- **P1 pinned_ledger(持仓判断对错账本)**:新模块 `autoresearch/learning/pinned_ledger.py`(确定性,零 LLM)。
  - 记账时点:assemble 后,对 `finalists.csv` lane=pinned 行,记 `date/code/rating/proposal(FINAL 行)/ensemble 折回(原判→终评, trigger, spread, early_stopped)/conviction`;
  - 裁决时点:T+2 收盘成熟后(与 retro 同节律),按 **fwd_2_oc 主尺**自动判:
    - `SELL/UW 判对` = fwd_2 < 市场中位(躲过相对下跌);`判错(卖飞)` = fwd_2 − 市场中位 ≥ **+2pp**(卖飞线,首版定值,写入模块 docstring 可调);两线之间记 `中性`;
    - `Hold 判对/错` 同理镜像;
    - **折回对错** = 终评 vs 原判谁更接近实现方向(300857 的 Sell→UW:若 T+2 大跌则「折错(原判对)」,记入 ensemble_ledger 同一行);
  - 输出 `reports/learning/pinned_ledger.md`:逐笔 + 累计(卖对率/卖飞率/折回救对率);n≥10 后把「持仓判断基率」以 📐 同款格式进 `_l4_shared_instructions.md` 当日件(shrink 收缩口径复用 `learning/shrink.py`,n<3 禁注不变)。
- **P2 tripwire 机器可解化 + 日检**:
  - `l4-card.md` 模板 tripwire 段改**三型结构化**(向后兼容,解析不了的行照旧人眼):
    `- [价格线] close < 220`(触发条件为可比较表达式)/ `- [日期线] 2026-08-25 中报披露` / `- [事件旗] 减持|质押|问询`;
  - 新模块 `autoresearch/learning/tripwire_watch.py`:prelude 内日检——取每只持仓**最新一张卡**的结构化 tripwires,价格线对湖 close、日期线对 today(≤3 交易日预警)、事件旗对 news_em 标题关键词(anns 无权限的面明写「事件旗仅 news_em 覆盖」,不假装全覆盖);
  - 触发 → `_prelude_summary.md` 当日件行亮 `⚡tripwire`(格式同 📐/🔁/🚪),同步进 pinned_ledger 备注列。
- **P3 conviction 校准腿(L4 推的侧)**:t1_review 的 build 增 conviction 分桶(≥70 / <70 × 准/不准),报表行进 t1 账本;攒 n 后喂 🔁 校准行(现 🔁 只有 L3 lane 翻案率,补上 L4 自己的确信度-命中率曲线)。
- **P4 intel→档案回流**:`dossier.reconcile` 素材清单加当期 `_l4_intel_<code>.md` 事件段(结构化表直取,确定性);07-27 活体案例:intel 逮到 300857 档案漏「2026-04 增资控股光为科技 51%」第六业务——回流后该缺口在下次对账补上。不新建 agent。
- **P5 欠账自动化(节律病根治)**:`scripts/nightly_close.sh` + launchd plist(交易日 **20:45**,错开 19:30 prewarm)——自动跑**确定性部分**:`retro refresh`(幂等补成熟日 attribute)+ `t1_review backfill`(确定性回补)+ tripwire 日检 + 账本刷新;**LLM 诊断段(scan-retro/t1 合诊)仍人工**,prelude 汇总屏的提醒行从「欠 N 天」改为「诊断段待跑 N 天(确定性已补)」。安装/验证命令同 prewarm 模板。验收:装载一周后 `retro pending` 的「备料未收尾」不再累积新条目。

## 7. 批次与裁决点日历 v3

### 7.1 批次

| 批 | 内容 | 预估 |
|---|---|---|
| **R′(即刻,零开发)** | retro 三日诊断收尾 + 07-24/07-27 两对补账 → E2 关账(14→≤9)→ 档案 2 只 + 对账分批 | 纯跑动 |
| **批 B′(修缮,~1-2 天)** | a 计量正门 · b price_claims 去假阳 · c l3 lint 白名单 · d earlystop 单一事实源 · e 断连轻重试 · f reuse 持仓豁免+相对阈值 · g pack-check 收编提交 + Q6 剩余 + E1 退役(progress.py 可删) | 每件独立回滚 |
| **批 N(intel 重构,~1 天)** | §4 三窗/衰减/时效窗列/lint 三条 + 声明行扩展 | prompt+lint |
| **批 P(持仓全套,~1-2 天)** | P1 pinned_ledger · P2 tripwire · P3 conviction 腿 · P4 档案回流 · P5 夜间自动化 | 新模块 2 个 |
| **批 T(触发式)** | T8 分模型 $ 列(随 B′-a)· T9 intel 前置 · ensemble/OW 复核降档(账本触发)· pass1 35(影子验证)· SKILL 精简二期(主会话>25%) | 读数触发 |

推荐次序:**R′ → B′ → N → P → T**(B′ 在 N 前:lint 去假阳是 intel 升 fail 的前置;B′-a 在 T8 前:正门是 $ 列的载体)。

### 7.2 裁决点日历 v3(取代 Wave6 §6.2)

| 时点 | 裁决/动作 | 输入 |
|---|---|---|
| 07-28 收盘后 | attribute 07-24;t1 补 07-24→07-27、07-27→07-28;三日诊断收尾 | R′1 |
| 批 N 上线后 3 跑 | intel 时效 lint 误报率 → warn 升 fail 与否;限频连续超限 → 强制力升格 | lint 账本 |
| ~08-08 | event 路裁决(pr_20260725_001);intel A/B 结算(口径按 §4.4 重定义);②C 强势票停因桶(earlystop 账本修通后 ≥10 日) | 各账本 |
| 8 月中报季 | `dossier.reconcile 20260630` 首跑;20251231 对账清尾 | 披露日历 |
| 买单 n≥10(现 9) | OW 复核降档;评级基率注入 | buy_ledger |
| ensemble n≥5 | SELL 复核 3 跑→1 跑裁决 | ensemble_ledger |
| pinned n≥10 | 持仓判断基率进当日件(📐 同款) | pinned_ledger |
| P5 装载 +1 周 | 「备料未收尾」增量 = 0 验收 | retro pending |
| ~09 月底 | 一致预期 EPS 修正 ≥60 日 → factor_lab 验收 | consensus 积累 |
| 每周日 | macro harvest(已排程)+ 日历巡检 + 池日检 | 既有 |

### 7.3 本波「完成」的定义

1. retro/t1 欠账清零且 P5 装载后一周无新欠账累积。
2. `usage_harvest --session` 正门出全表(含主会话),$ 列在表。
3. 07-27 四条 price_claim warn 重放:2 假阳消失、2 真错保留;l3 lint 15 处重放假阳清零。
4. earlystop_ledger 非空且与 gate_hist 同数。
5. 下次扫描:intel 稿全部带时效窗列、T0 面 100% 显式(有增量或「盘后无增量」)、>1 周非催化事件净分为 0。
6. pinned_ledger 对 07-27 三持仓卡完成首笔记账与 T+2 裁决;tripwire 日检在 prelude 汇总屏出现(触发或「无触发」均显式)。
7. 持仓在 reuse 层永不复用(重放 07-27 场景验证)。
8. open proposals ≤9。

## 附录 A:07-27 事故复盘(frame 0 字节)完整时间线

19:56 frame 首跑 `stock_yjbb_em` 断流(11/12 端点处)→ 0 字节 pack;20:09 macro-brief 读空 pack 正确拒写(BLOCKED,120k 加权);20:23 人工摁停 workflow(赶在 L3 前)→ 零网络重建 pack(`market_pack(scan_dir)` staging 回退口径,7,155B 全字段)+ 补 `_macro_cn.json` → `scan-market.js` 补 pack-check 守卫 → AsyncFunction 探针验语法 → resume(frame 走缓存,market_view 20:26 带真数据落盘)。**机制结论**:①`bash()` 壳不看退出码 + `>` 重定向 = 0 字节文件型静默失败的固定形状(空 pickle/空 slim/空 pack 三案同族);②macro-brief 的「空壳比缺文件更坏」论证(文件存在会压掉 L5 回退 + 注入每张卡)值得进 STAGES.md 机制注。

## 附录 B:未选路径(负结果/延迟决策档案)

| 路径 | 内容 | 不选原因 | 解锁条件 |
|---|---|---|---|
| 确定性 7×24 快讯层(wire) | akshare `stock_info_global_cls`(财联社电报)/`_em`/`_sina`/`_ths`/`_futu` 五端点可用未接;设想:收盘→跑报全量快讯按代码/关键词匹配 L2-200,喂 L3 表旗/intel 已知底/策略师 | 用户裁定只重构 intel(2026-07-27);蛛丝马迹覆盖面停在 finalist ~10 只是知情取舍 | 若批 N 上线后仍出现「L2 菜单内非 finalist 票盘后重大事件漏读」实例 ≥2 起,可重提 |
| finalist+保送逐票 stock_news_em 补齐 | 13 次端点调用/2-4min,个股定向新闻流 | 同上收窄;且 intel 三窗已覆盖个股面 | 同上 |
| ensemble run2‖run3 并行(wall 优先) | 分歧场景 wall −7min | T2 token 优先已裁;分歧场景样本 1 例 | ensemble_ledger 显示分歧率 >40% 且 wall 成瓶颈 |
| l4-card 拆两段(P0-P2 / P3-P5)实现 intel 并行 | 股内 wall −5min | 多一次 agent spawn + context 重载 ~60k+;T9 intel 前置更便宜 | T9 落地后仍需提速时评估 |

## 附录 C:证据索引

- run 取证:`reports/scan/20260727_2140/`(summary/token_usage/details×10/trace 41 件);`context/scan/2026-07-27/`(`_prelude_summary.md`/`market_pack.json` 重建版/`_ensemble_601869.json`/finalists.csv/`_stage_timing.json`)。
- 事故一手记录:本 session workflow journal(`wf_39a95f6b-3a2`,frame 空返回 + BLOCKED 全文);`agent-a0e12a1adac9ef984.jsonl`(ChunkedEncodingError 栈)。
- 探针失效证据:earlystop_ledger.md(空)vs gate_hist(4 张早停);price_claims 四 warn 与卡原文对照(601211「不采信」句/601869 fwd-EPS 句);l3 lint 15 处告警 reason 串。
- 代码定位:`usage_harvest.py:124,191`;`l4_reuse.py:95-156`(pinned 零出现,`price_tol` 绝对口径);`l3_news.py`(anns 残渣);`l4-intel.md`(现行契约全文);`market.py:139-167`(staging 回退口径)。
- 账本:`context/knowledge/proposals.jsonl`(open 14 逐条);`reports/learning/`(earlystop/buy/zero_buy/paper_nav);memory `l4-reuse-blind-to-pinned-and-absolute-threshold.md`。
- 上游:Wave6 spec(红线/挂账刀/日历 v2)、Wave5 spec、`instruction-vs-check-mismatch-20260727` 归档。

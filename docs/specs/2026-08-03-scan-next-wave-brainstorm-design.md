# 2026-08-03 扫描漏斗下一波候选优化 · brainstorm 设计稿

> **性质:brainstorm 产物、候选池,非调度权威。** 现行总调度权威仍是 Wave10 设计稿
> (`docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md`);Wave9 C/D/E/F
> 未清债仍归 Wave9 稿。本稿是对 Wave9/Wave10 的 **delta appendix**,只记录新增候选、
> 勘误和需重新裁决的替代项,不重复授权既有 Wave。本稿不授权任何行为变更 —— 一切生产接线走实验治理
> (`experiment_registry → promotion → 人工 approve/activate`),文中「方案」均指
> 待立项候选,落地时另立 wave plan。
>
> 用户裁定(2026-08-03 会话):**只落文档、不做开发**;深写范围 = 新闻(§1)、
> 衍生品(§2)、L2(§3)+ 全景四件(§4.1–4.4:门审计 / 闭环债自动化 / L4 派发下沉 /
> L3 边际实验);期权线 F1+F2+F3 全写;单文件总纲。
>
> **⚠️ 2026-08-04 用户裁定取代上一条:「按本稿全部开发完,在 main 分支」。**
> 实施计划与落地记录见 `docs/specs/2026-08-04-nextwave-implementation-plan.md`;
> 候选状态的**单一事实源**是 `autoresearch/research/candidates.py`
> (`python -m autoresearch.research.candidates --check`),不是本稿正文。
> 落地形态:M 类 7/7、I 类 8/10 已实现且消费者默认关闭;**B 类 0 件激活**
> —— 本稿 §5-1 的状态机未被绕过。
>
> 本稿「实测」标注以 2026-08-03 晚探针/读码为基准;2026-08-04 复核有更新的地方显式
> 标注。所有统计结论必须带 cohort/definition/as-of,不得只引用 prelude 摘要数字。

---

## 0. 边界:铁律、既有裁定、负结果(写给未来的自己)

### 0.1 不可违背(用户裁定 + 铁律)

| 条 | 内容 | 出处 |
|---|---|---|
| 主尺 | `fwd_2_oc` 超短 1–2 日;勿再推 swing/T+5 | 2026-07-10 用户裁定 |
| 零 LLM 层 | L0/L1/L2/L5 全确定性,不编数、不预测 | SKILL 铁律 |
| 性能开关 | 不得拥有评级/finalist/rubric 语义 | SKILL 铁律(B4 判例) |
| L4 粒度 | 每股一个 l4-stock workflow,不分 wave | fb_20260714_003 |
| L4 复用 | TTL 复用整体退役,勿再提 | 2026-07-29 用户裁定 R5 |
| 观察单 | 日检已退役,勿复活 | fb_20260714_002 |
| 板块偏好 | 「不要跌势票」= 产品偏好非预测主张;上涨板块侧未被数据否决 | 2026-07-17 用户裁定 |
| 数据契约 | A 级空即抛异常阻断;B 级降级必须记账 | data-contracts 裁定 |
| intel | 只拒稿不拒票;情报是辅助面不得反噬决策主链 | scan_config 注 |

### 0.2 负结果清单(勿重启;数字为裁决读数)

- **L2 上模型**:全 zoo OOS rank-IC 负;composite-top200 四年回测 ≈0(STAGES 附录D)。
- **业绩预告做 L1 事件通道**:强制披露季 T+5 超额 −0.27%/胜率 35%,追缺口 −2.92%(附录E)。
- **追当日大涨**:当日 ≥9.5% 的 350 只 fwd_2_oc −2.06% vs 市场 +1.60%,超额 −3.67pp
  t=−11.91(2026-07-21 真湖实证)。任何新召回/事件设计不得用当日涨幅做入场。
- **northbound 通道**:hk_ratio T+2 IC −0.108,已停用;**accumulation 通道**:unique 超额
  −0.21%,已停用。
- **event 通道**:默认停用取证中(pr_20260725_001,首读边际 unique 40 只 −1.01pp t=−1.83)。
  旧「≥10 日累计>0」启用口径由统一成熟门取代(§1.2);事件类型学**不预设其结论**。
- **裸事件计数**:`ev_pos` 裸加总=「被调研机构家数排行」(surv_n 占 64%,top10 全是纯调研),
  已用 `ev_hard` 修正(`scan/events.py` 注)。

### 0.3 方法论条款(每案强制)

1. **最小证伪步先行**:每案先列一个零/低成本步骤,能证伪就不开工(判例:事件端点自带
   ts_code → 整条 LLM 路免建)。
2. **默认不启用 = 连副作用一起不启用**(判例:event 桶 floor>0 时 `merit_need` 被改、
   每天 10 行 `l2_lane_reserved` 翻标签,三个真消费者被污染)。
3. **注入类改动必查行数/格式预算**(判例:升格新契约会把既有预算挤掉)。
4. **自动腿必须有会变的量做断言**(判例:权重重标定连续 4 次 NO-OP 空转两周无人察觉)。
5. 收紧/放宽类改动先测误放行率;拆特性必须连 helper/接线一起搬(2026-08-03 当天判例:
   `gpJson`/`bash` 两个 workflow helper 漏搬,每次全扫必崩,commit 1ce5470 修)。

### 0.4 权威、证据与变更分类

每个候选立项前必须补齐两张表,缺任一项不得转 implementation plan:

1. **继承矩阵**:`Wave9/Wave10/STAGES` × `继承 | 替代 | 新增 | 需用户重裁`。F1/F2 与
   L4 workflow 下沉和 Wave9 有直接重叠,不能再写「与 Wave10/Wave9 无重叠」;若改变
   Tushare-first、CFFEX 首发范围或 L4 调度语义,必须显式标为 supersede。
2. **evidence_manifest**:每个数字写 `artifact_path / cohort_version / numerator /
   denominator / as_of / definition_hash`;跨 cohort 的数只可并列,不可拼成一个比例。

同时把候选拆成三类,优先级与风险等级分开:

- **M 测量**:只新增 ledger/报表/探针,不进入提示词、名单或阻断路径;
- **I 数据基建**:写湖/索引/回放契约,消费者默认关闭;
- **B 生产行为**:改变名单、证据输入、评级、阻断或调度语义,必须走 registry + shadow
  + rollback。所谓「展示/证据列」一旦进入 L3/L4 prompt 也属于 B,不是零行为变更。

---

## 1. 新闻能力强化(话题 2)

### 1.0 现状接线图(读码 + 当日实测)

| 层 | 现状 | 病 |
|---|---|---|
| L3 公告 | tushare `anns_d` 已退役(2026-07-18 起无权限)→ `anns_fallback`(cninfo)对空桶逐票补(Wave9 C-1;2026-08-03 实测兜底承载 3,119 行);标题关键词打方向 tag:利多/利空词表 + 否定词中性化 + 监管旗(`scan/agents/l3_news.py:29-44`) | 只有标题无正文;fallback 主要是逐票近窗,默认生产调用并非全市场历史湖 |
| L4 情报 | l4-intel(sonnet·max)六面盲搜 WebSearch/WebFetch;自报 cap 20 指令级天天超(08-03 实测 21–34 条);现 `intel_guard` 对可解析超额稿件是 trim,仅无法解析事件表时拒稿;价格断言已有 OHLCV 对账(`price_claims.py`) | 搜索数依赖稿件自报,不是工具调用事实;捏造与日期焊接有 P0 前科;搜索结果一次性使用 |
| 事件端 | 回购/增持/调研三端点入湖(`scan/events.py` 复用 `l3_catalyst.catalyst_counts`);催化旗 24/203(08-03) | 覆盖窄(仅三类);event 通道停用取证中 |
| 宏观/策略师 | 『实时网查』带 as-of 标注 | 同样不留存 |
| 持久化现状(08-04 复核) | `stock_news_em` 已注册为 as-of lake,工作区已有约 1,891 个 parquet;公告另有通用 `anns_d` lake/fallback cache;快讯族三源可达,财联社接口 404 | 存储分散、字段和时间语义不统一;现有快照缺统一 `first_seen/stage/run` 清单,不能精确重放阶段输入 |

**核心诊断修正**:问题不是「全仓无新闻存储」,而是 ①既有快照/公告/实时搜索之间没有统一
观测清单,无法回答「某阶段截止时系统实际看到了什么」;②选择性逐票采集不能充当全市场
新闻量;③claim→source 缺少可核验链路;④实时搜索缺实际调用 telemetry,复用与成本无法计量。

### 1.1 D1 · 统一新闻观测目录 news_catalog(核心件)

**目标**:复用 `stock_news_em`、`anns_d` 与 fallback 既有存储,增加统一、只增的事件/观测
目录;夜间零 LLM 补快讯流与缺失元数据,供阶段级回放、intel 去重和 L3 第二源。
**非目标**:不重造平行湖、不进 L0–L2 打分、不把选择性逐票抓取解释成市场新闻量、
不做盘中流/全文情感模型。

**数据模型**(原始内容仍留各自湖;统一目录只保存身份、版本和可用性):

```
canonical_event:
  canonical_event_id, event_ts?, title_norm, codes[]

source_observation:
  source_observation_id, canonical_event_id, source_item_id?, source, url
  published_ts?, first_seen_ts, fetched_ts, content_hash, revision
  first_seen_basis, scan_run_id?, available_stage, raw_artifact_path

code_link:
  source_observation_id, code, method, confidence, rule_version
```

- **PIT 纪律**:回放必须传 `decision_cutoff_ts + stage`;只允许
  `first_seen_ts ≤ decision_cutoff_ts` 且 `available_stage ≤ stage` 的观测。`published_ts`
  只是来源陈述,不能替代 first-seen。D4 在 finalist 后抓到的全文不得进入同日 L3 回放。
- **身份纪律**:`url|title|date` hash 只能当 observation 候选键,不能同时承担事件身份;
  更正、转载、URL 变化保留 revision/来源观测,不可覆盖旧版本。
- **既有资产优先**:先为约 1,891 个 `stock_news_em` 分片、`anns_d` 和 fallback 建 manifest,
  再决定是否迁移物理文件;不得为同一内容另写第二份宽表。历史分片没有真实抓取时间时,
  用持久化快照时间并标 `first_seen_basis=snapshot_inferred`,且不得回放到该快照之前;
  新抓取必须标 `observed`。
- **拉取范围**:全市场公告 + 固定口径的全局快讯 + `L2 ∪ dossier ∪ pinned` 逐票补充。
  后者有选择偏差,只能做个股证据,不能计算市场「新闻热度」;全市场热度只能来自固定
  feed 并按有效覆盖/freshness 归一。
- **保留与成本**:热层 365 日可行,但 append-only 的实验/retro 清单不得随热层删除;
  原文/PDF 走压缩对象冷存储与 hash 引用。容量以实测字节报表为准,不再写「忽略不计」。
- **源契约**:B 级降级记账;记录限频、条款、freshness、非空率、schema hash。函数存在或
  HTTP 200 不等于数据可用,必须做关键列非空与日期单调验证。

**消费三接口**(各自独立立项,目录先行):

1. **复盘重放**:`news_catalog.replay(code, decision_cutoff_ts, stage)` → retro/t1 增加
   「当时已可见新闻面」。仅写诊断 artifact 时是 M;一旦进入判断 prompt 就是 B。
2. **intel 先读目录**:l4-intel prompt 注入近 10 日已知摘要,指令改为「先复用,对更正/
   缺口再查」。这是 B 类变更→ registry 影子;网查数必须来自真实 tool-call telemetry,
   不能使用稿件自报。比较调用数、耗时、覆盖、错引与终局质量。
3. **L3 公告第二源**:`l3_news` 空桶兜底顺序变为目录 → cninfo 实时(命中免网络)。

**最小证伪步**:先对既有分片生成 1 日 manifest,用同一条新闻的转载/更正 fixture 验证
canonical/observation 分离,并验证 L3 与 L4 两个 cutoff 不互相污染。逐票 query code 可单独
记关联,全局快讯允许 unmapped;不得用统一「codes 命中率 70%」阻断整个目录。
**验收探针**:manifest 与原始分片逐源对账;first_seen 缺失率=0;同一 run+stage 回放稳定;
late-arrival fixture 不泄漏;源非空率/freshness 和 intel 实际调用数有时序。
**回滚**:目录消费者关闭即可;原始湖不迁毁、不覆盖。

### 1.2 D2 · 确定性事件类型学(typed events)

现状词表只产方向 tag。升级为**多标签事件记录**,主键引用 `source_observation_id`,至少包含
`event_types[] / lifecycle_status / direction / strength / matched_spans / confidence /
ambiguous / rule_version / first_seen_ts`。同一标题可同时是「问询回复+澄清」;回购、减持、
重组必须区分预案/实施/完成/终止,不能用一个 `type` 覆盖生命周期。

- **用途分层**:①落结构化 artifact 是 I;②渲染到 L3/L4 prompt 已是 B;③每个 type 的
  候选因子分别走 factor_lab。信号日期按扫描 cutoff/下一可交易日滚动,不能只用 ann_date。
- **统一成熟门**:不得沿用局部「≥10 日累计>0」。统一使用 STAGES 当前门:≥20 个真实
  扫描日、关键细分 ≥10、召回 unique ≥30、覆盖 ≥2 regime,并报告 date-cluster LCB、
  多重检验/FDR 与锁定 OOS;不足即 `IMMATURE`。
- **假设纪律**:业绩预告披露后追买已被否;业绩类不直接做追涨信号。非业绩类也一律
  不用当日涨幅,必须单独预注册方向、窗口和 no-harm。
- **最小证伪步**:先做 PIT 覆盖矩阵。当前 fallback 是逐票近窗,不能假设已有全市场历史
  湖、更不能承诺「零新数据一晚回填」。只有覆盖和 first_seen 合格的 type 才进 factor_lab。

### 1.3 D3 · 情报治理 v2

1. **先建 claim ledger**:每个原子 claim 有 `claim_id / subject / predicate / value /
   effective_date / status / citation_observation_ids[]`;自然语言 `[source|date|url]` 只能作为
   展示格式,不能充当真实性校验。
2. **lint 分层**:先验 URL/date/主体/状态字段和抓取 artifact/hash,再记录「可访问、字段匹配、
   内容支撑」三个 verdict。缺引用/错引只拒 intel 稿、不拒票;回退路径必须独立可测。
3. **日期焊接检测**:claim 日期同时和 `published_ts/first_seen_ts/effective_date` 对账;
   对不上先标 `UNVERIFIED`,覆盖不足时不自动判假。
4. **源/模型责任分离**:retro 回写 claim-source edge 的 verdict 与原因,区分来源原文错误、
   LLM 错引和过期事实。样本稀疏/选择性证伪时不发布伪精确的 source precision。
5. **cap 改为可观测预算**:当前 guard 是 trim 而非「超 30 必拒」;用真实工具 telemetry
   约束 search/fetch 次数和耗时,自报字段只作诊断。cap 调整和先读目录都属于 B 类实验。

### 1.4 D4 · finalist 公告正文(≤10 只/日)

对当日 finalist(≤10)拉近 10 日关键公告全文,保存 PDF/HTML hash、页码和抽取版本;
l4-card P2/P4 只注入带 `source_observation_id + page + excerpt_hash` 的限额摘录。

- **预算按模型输入计量**:限额使用格式化后的字符/token,不使用「2,000 字节≈2KB token」;
  先测中文 PDF 抽取后的实际 prompt delta,超限按证据优先级截断并显式标记。
- **假设边界**:D4 是「L4 证据增强」,不是门直接读取全文。不能继续引用错误的「错杀 60%」。
- **实验**:同一候选、同一门版本做有全文/无全文 paired shadow 或盲双卡;比较 claim
  正确率、评级/门分歧及成熟 T+2,避免用上线前后两个时期作因果比较。
- **核验分工**:`price_claims` 只核验价格/日期/涨跌幅;公告事实另建 excerpt/page/hash
  verifier,不得复用价格对账器。

---

## 2. 期权/衍生品线(话题 3)

### 2.0 当前产品集裁定(非永久结构断言)

截至 2026-08-04 当前公开可用产品集,本项目能稳定获取的是 ETF/指数期权,未找到可用于
全 A 个股召回的交易所个股期权数据。因此本 Wave **不做个股期权召回**;期权只作为市场
地形/风格风险候选。该结论是「当前产品集不可行」,不是制度上永久钉死:上交所规则允许
股票或 ETF 成为期权标的,故应按季度重检产品清单。

官方核验:[上交所期权规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/option/c/c_20250610_10781448.shtml)、
[深交所期权产品页](https://www.szse.cn/option/)、
[中金所 2026 合约挂牌](https://www.cffex.com.cn/jystz/20260320/47214.html)。

**明确不做**:HK 个股期权代理、场外期权、期权直开 L1 通道。可转债是另一类证券,
只能作为独立个股级候选信号,不能称为「期权替身」。

### 2.1 数据可行性(2026-08-04 复核)

| 探针 | 结果 | 裁定 |
|---|---|---|
| tushare `opt_daily(20260731)` | SSE 752 / SZSE 492 / CFFEX 720 行 | CFFEX 权限已证,OPEN-Q-3 关闭;无 IV/Greeks,必须联结元数据 |
| tushare `opt_basic` | SSE 首页恰 12,000;offset=12,000 仍有行;SZSE 7,944;CFFEX 10,114 | 12,000 是分页上限,不是完整;强制分页、去重、coverage 对账 |
| AKShare QVIX | 50ETF/300ETF 有历史;科创接口可达;1000 指数末行关键列全 NaN | 函数存在≠有效覆盖;逐序列做日期、非空和极值契约 |
| tushare `cb_basic/cb_daily` | 1,156 只基础表 / 20260731 日行情 308 行 | 行情可达,但 `conv_price` 是当前值,不能证明历史 PIT 转股价可得 |
| tushare `cb_call` | 20260731 可返回强赎相关记录 | 强赎状态应走该端点/公告,不是 `cb_basic` |
| tushare `cb_price_chg` | 端点存在,当前 token 无权限 | F3 历史溢价因子暂被数据权限阻断 |

### 2.2 F1 · 市场地形(PCR/持仓结构 → `market_pack.derivatives`)

**一期是 I 类数据基建,不直接接 prompt**:分页拉 `opt_basic`,按 as-of 联结 `opt_daily`,先
按 underlying/product/expiry bucket 计算成交量 PCR、持仓 PCR、成交额和换月调整 OI。
不同 ETF/指数、合约乘数和期限不得直接裸加总;跨产品展示需名义金额/delta 归一,否则
保持分品种面板。PCR 只能解释为「对冲/持仓压力」,不能区分买 put 与卖 put,不得直接标
「看空」。

**二期(IV 分位 / 25Δ skew / 期限结构)**:QVIX 只能提供波动率指数/分位,不能替代 25Δ
skew 或期限结构。自算 IV 时优先用 put-call parity 推隐含远期/分红,不得固定 `q=0`;
同时做 no-arbitrage、到期天数、流动性、solver 失败率和极值过滤。

- **消费**:进入 `market_pack.derivatives` 只算 I;加入 `strategist_pack` allowlist 或温度/
  regime 即为 B,必须 registry。L3/L4 prompt diff 必须保持 0 直到行为实验批准。
- **最小证伪步**:用所有满足稳定契约的历史,检验 lagged `PCR/ΔPCR/zscore` 对次日 breadth
  或 regime transition 的增量价值;同期相关不算领先证据。按扫描日 walk-forward,
  date-cluster/HAC 区间,对 breadth+momentum 基线报告增量。
- **成熟门**:60 日不是自动充分条件;按转折事件数、到期周期和 regime 覆盖判成熟。

### 2.3 F2 · 风格温差(小票 vs 大票风险偏好)

MO(中证1000)与 IO(沪深300)优先分别构建期限匹配、换月调整的指标,再比较 IV/skew 或
PCR z-score;原始 PCR 差和成交额比只能是探索特征。CFFEX 数据权限已证,当前风险转为
元数据分页、期限对齐、名义归一和样本长度。只有对次日 breadth/regime transition 有稳定
增量且 no-harm 过线,才提 regime 第四信号;此前仅展示,不进入策略师判断。

### 2.4 F3 · 可转债个股候选(先过 capability gate)

当前只能确认日行情和正股映射可得,不能确认 120 日 PIT 历史转股价。`cb_basic.conv_price`
是当前截面;拿它重算历史转股价值会泄漏未来调整。`cb_basic` 也不能判断实际强赎状态。
因此「数据全通、一晚跑三因子」的原结论撤销,F3 先标 `BLOCKED_BY_DATA`。

**capability gate**:

1. 历史转股价调整序列或公告解析可得,并能按 `first_seen_ts` 回放;
2. `cb_call`/公告能标强赎、到期、上市/退市和暂停窗口;
3. 多债映射一只正股有确定聚合规则,成交额单位/合约口径完成对账;
4. 新债、低流动性和妖债过滤预注册;缺失不与无转债股票直接横比。

过 gate 后再研究三条**可分解**信号:CB return、underlying return、premium change。
`premium_delta` 的符号并不天然代表「转债资金抢跑」,它可能只是正股上涨的机械结果;
优先研究控制 underlying return、期限/转股价值和债底后的 residual。`CB mom−stock mom`
与 premium change 高度重叠,不得同时以两个独立发现计数。统一走 factor_lab → 锁定 OOS
→ replay → registry;能力门不过则整线停在数据可行性,不回填伪 PIT 因子。

---

## 3. L2 粗排(话题 4)

### 3.0 现状精述(读码,`autoresearch/scan/recall/l2_stratify.py`)

三步确定性采样(零 ML):① sector-neutral composite(composite − 申万一级组均值,
`:38`)排 merit 核 = top(200−Σfloor),申万一级 cap ≤20%;② 7 风格桶固定 floor 保底
(趋势20/健康15/反转12/价值12/成长12/吸筹12/主力10;事件桶 0),floor 救回打
`l2_lane_reserved`(三个真消费者:`l3_select._row_lane` / `l4_card.force_full_card` /
`retro.floor_experiment`);③ 不足回填(必要时松 cap)。

**为什么长这样**(STAGES §二实证):L2 全 zoo 模型 OOS rank-IC 负、composite-top200
四年 ≈0 → L2 不预测只做菜单;已证 alpha 在拒绝侧(L4 评级 rank-IC +0.55、门价值
+4.35pp);**0 买根因在召回线**(413 只赢家 91% 在打分池、仅 4.8% 过 top1000 线,
composite IC −0.11)。

**坦白(本章天花板)**:实证「分层免费」(strat ≈ top200)→ O 系列全部是**守菜单质量**
的工程,不承诺 alpha;0 买根因的修复主战场在 L1 权重/召回线(regime 分桶权重已在跑,
trend 43/range 53/risk_off 11 日,risk_off 块样本仍薄)。

### 3.1 O1 · winner-capture@200 升格 SLO(纯计量,零风险)

先固定 winner 定义:主尺 `fwd_2_oc`,同时满足全市场 top-decile、绝对收益阈值和 D+1
可买/可交易;明确并列、停牌/涨停和 pinned 口径。不得把现有 retro 的复合 winner 和「纯
top-decile」混叫一个标签。

同时落三层曲线,用实际 K(含/不含 pinned 分列),不再写死 L1-1001/L2-203:

- 端到端 `WC_L1_all`、`WC_L2_all`;
- 条件召回 `WC_L1_given_L0`、`WC_L2_given_L1`,避免把 L0/L1 漏失归责给 L2;
- SLO 守卫:lane/sector 覆盖、集中度、selection_reason、日间稳定性和结果成熟度。

报警线使用当日之前的 expanding history P25,并设最小历史/成熟日门、按 regime 展示;
不得用全期分位造成未来泄漏。winner capture 是主 SLO,不是唯一指标,否则可通过扩大 K
或集中追热点被动做高。

### 3.2 O2 · 52 周高距离族:维持否决,仅保留可重开条件

`docs/research/2026-07-18-dist-high-252-ic.md` 的正式结论是「不过线」:全样本 rank-IC
−0.0023、两半反号、risk_off IC −0.082(t=−2.29)。`t≈+2.62` 只表示它优于更差的
`pct_60d`,不表示绝对有效。故删除「替换判据/新增 floor 桶」的近期候选资格。

未来只有满足以下条件才可作为 **reopen rejected hypothesis** 重启:预注册 top-decile
阈值旗而非线性因子;使用新增 OOS;单列 risk_off no-harm;通过统一成熟门。未满足前 O2
状态=`REJECTED`,不进 P1/replay 网格。

### 3.3 O3 · regime 化 —— 先清算既有半特性

**读码发现**:`stratified_l2/select_l2` 的 `regime`/`regime_caps` 形参**已建未接线**
(`l2_stratify.py:57,81,135` 语义=按 regime 调 sector cap;`universe.py` 全部调用点
`:283-346` 均未传)。这正是 §0.3-5 的「拆半个特性」形态 —— 挂着参数没人喂,下一个
读代码的人会以为 regime 化已生效。

**P0 只能删除未使用形参或补文档**,因为这不改变名单。真正把 `_regime`/caps 接入会改变
L2 构成,属于 B 类 challenger:先补 variant contract,再 registry/replay,禁止把示例 cap
当默认参数。risk_off 只有 11 日时只能 `IMMATURE`,不得通过全样本调参后声称分 regime 稳定。

### 3.4 O4 · cap/floor 参数扫描(replay 网格)

先补 replay `variant_spec + definition_hash + 独立输出根`,防止 staging 幂等逻辑误复用
baseline。网格只可用于探索;正式裁决用 nested walk-forward/锁定 holdout,并对多重比较
修正。目标 = §3.1 SLO 向量,守卫 = 行业集中度/健康占比/落刀面/稳定性。

`capfloor20` 测的是 L0 市值 floor,只能复用对照框架和产物契约,不能当 L2 sector cap/
style floor 的近亲实证或参数先验。regime 子样本不足时明确报 `IMMATURE`,不要求伪造
「全段同向」。

### 3.5 O5 · 新特征统一闸门(治理条款)

任何新特征入 composite/新通道,唯一入口:capability/PIT gate → factor_lab `harvest →
calibrate`(锁定 OOS、符号稳定、date-cluster 区间和 no-harm)→ replay 对照 → registry
challenger。排队中只有数据门已通过者;CB 当前被 capability gate 阻断,typed-event 先补
覆盖矩阵。**没有第二条路**。

---

## 4. 全景四件(话题 1 升格深写)

### 4.1 门审计 · 「业绩真兑现」门重标定

**口径勘误**:08-03 prelude 的「拦 11/拦对 25%/错杀 60%」来自 `cross_calib` 的另一
分组与 winner 条件,不是 A11 v3 单门 attribution,不得标成 `gate_attribution` 结论。
正式 v3 当前是「业绩真兑现」单门归因 `n=6`:CORRECT=2、NEUTRAL=2、FALSE_KILL=2,
拦对率与错杀率均 33.3%;另有 participation cohort 49 条,但 participation 不能直接充当
单门因果分母。legacy 口径也只能并列展示,不可混算。

**当前裁定=`IMMATURE`。** `learning/shrink.py` 只用于 LLM 注入锚点,明确不能用来决定
机制/门去留;「收缩后回均值」不是证伪。先把 manifest 固定为 A11 v3、tradable mature
中位基线、multi-gate collapse 和 FALSE_KILL=`ex2≥+2pp`,再谈实验。

实验拆成两个问题,避免把证据增强与门松紧混成一个 treatment:

1. `gate_true_delivery_evidence`:同一候选、同一门版本做 D4 有/无全文 paired shadow,
   只裁证据是否改变 claim 正确率、门分歧和成熟结果;
2. `gate_true_delivery_recal`:现门不变,在完整 gate participation cohort 上记录每个
   binding/counterfactual 结果;禁止直接放宽,只生成 shadow verdict。

**裁决**:预注册 hard minimum、等价/no-harm margin、Beta-Binomial 或 date-cluster 区间;
至少 20 个真实 binding 成熟事件且功效足够才可 RECOMMENDED。样本不足或区间跨 margin
均为 `IMMATURE/UNKNOWN`,不是「门有效」或「门无效」。门总量价值只能作为背景守卫,
不能替代本门归因。

### 4.2 闭环债自动化(retro/t1/档案债)

**证据现状**(08-03 活证):retro 欠 4 天(07-27~30)、其中 3 天备料 71h 烂尾超 48h
SLO;t1 欠 1 天;档案 SLO 三红(最老待建档 7d/≤2d、季度对账债 19、7 日消化 0<新增 7)。
prelude 会列债但**不阻断**,还债靠人肉记得。

**设计修正(加固现有 runner,不是从零新建)**:

1. **确定性腿**:复用 `autoresearch.learning.nightly_close`,安装/加固 launchd;明确每个
   subtask 的输入债务、period、开始/结束时间、状态、产出数和 error hash。`0 rows` 可能
   是合法 NOOP,不能单靠行数判活。
2. **运行安全**:交易日历/catch-up、互斥锁、幂等键、原子 heartbeat、超时、重试退避、
   cwd/env/TUSHARE_TOKEN 校验;避免与当日 scan/retro 同时写 task book、T1 或 dossier。
3. **LLM 诊断腿**仍保留 OPEN-Q-4 三选一,先验证环境、权限、成本和质量;不得因为备料
   自动化成功就默认 LLM 诊断也可无人值守。
4. **债务策略分级**:数据完整性/成熟标签污染可考虑 hard block;研究/LLM 诊断债先告警或
   degraded policy。若确需「启动前阻断」,必须在 frame/universe/LLM 之前增 GATE0/preflight;
   当前 GATE1 在 L2 之后,不能声称「不开始扫描」。override 记录 actor/reason/expiry。

「债务导致权重停在旧教训、扫得越多错得越贵」目前无因果证据,从设计依据中删除。硬闸
属于 B 类可用性变更,需要 availability SLO、故障演练和回滚,不进入零风险 P0。

### 4.3 L4 派发下沉(workflow 嵌套滑窗)

**证据现状**(08-03 实测):主会话 $12.82 = 全场 32.7%(挂账线 25%);滑窗每股补派
= 一次主会话唤醒回合(全上下文 cache 读计费),10 只 = 10+ 回合纯调度开销。

**当前裁定**:嵌套 workflow 的基本调用、恢复和故障语义均未在本仓得到验证,不能先写成
既定工具契约;主会话份额降至 15% 和每日节省 `$5–8` 也只是待测假设。该项与 Wave9 的
L4 单工作流全链直接重叠,必须在继承矩阵标 `替代/需重裁`,不能并行拥有两套权威方案。

**最小 capability/chaos probe**:玩具父+两个子依次验证基本调用、args/result、并发上限、
父取消、子失败、timeout、parent death、`resumeFromRunId`、task-book lease/heartbeat 和重复
执行幂等。随后用固定候选集做 A/B telemetry,测 end-to-end prompt/cache/token/墙钟,而不是
只看父会话账单。子返回必须有紧凑 result contract,防止结果重新灌满父上下文。

**失败语义**:保持「每股一个可独立恢复单元」。单票失败先按 task book 重试;仍失败时默认
阻断 assemble。若未来允许 degraded publication,必须另立策略并在报告显式列缺失票,
不能 `catch` 后静默少一票。只有 capability、恢复性不劣和实测成本三门都过,才可进入
registry challenger;否则保留现行主会话滑窗。

### 4.4 L3 边际价值实验(排序价值计量)

**证据现状**:L3 真选无正 alpha(07-16 去📌污染后真选 −3.8%;finalists 11 日 −0.39pp/2日
t≈−1.2);但 L4 深核推翻 L3 高确信两次全对、trend lane 高确信被翻案 21% —— L3 的价值
可能在**证据组装与防呆**,不在排序。值得计量,不值得拍脑袋撤。

**先补可复原性**:pass1 是 union/floor/round-robin 选择集,不是单一确定性排名;现有
`_l3_pass1_cut.csv` 不能天然定义 top-K 反事实。新增 `_l3_pass1_kept.csv` 和
`selection_reason=merit|lane|sector|pinned|conviction_guard|backfill`,固定当日 K、quota、
pinned 和强制补入语义。

**两层设计**:

- **tier-1(历史配对估计)**:在同一 pass1 choice set 内,以相同 K 做 lane/sector 匹配的
  deterministic baseline,并用多次分层随机抽样形成参考分布;actual 与 baseline 按扫描日
  paired/date-cluster bootstrap。主尺是 excess_2 和可买 winner capture;Jaccard 只量暴露
  差异,不量价值。pinned/forced rows 分层或剔除。
- **tier-2(live shadow)**:当 tier-1 有足够 divergence 但区间仍不确定时,对 divergent
  picks 补 L4 卡并等待成熟 T+2。tier-2 用于补功效/验证分歧,不应只在 tier-1 已显著时触发。

**判定规则**:预注册等价 margin 与 power gate。只有整个置信区间落入等价区间才可说
「排序无增量」;未显著但区间宽=`UNKNOWN/IMMATURE`,绝不是「价值≈0 实锤」。历史比较
称关联/反事实估计,不称因果实验。结论仍只裁排序价值,不自动撤销 L3 的证据组装、红队
和压缩职责。

### 4.5 一段带过件

- **consensus 预注册**:积累中(report_rc 限频 1 次/小时,<60 日);写死触发条款 ——
  `n≥60 且 两半 IC 同号 且 |IC|>0.02` → 自动生成 PREREGISTERED spec(人批才往前走)。
  自动腿断言:status 命令输出的 n 必须周周增长。
- **档案债清偿排期**:季度对账 20251231 × 19 只 = 一次批跑(确定性);18 只待建档按
  ≤3/晚 ≈ 6 晚消化;不新建机制,纯排期(§4.2 确定性腿捎带)。
- **温度 v2**:F1 只能使用归一、换月调整且有领先性证据的衍生品读数;D1 只有固定全局
  feed、覆盖/freshness 归一后的新闻量可作候选。选择性 L2 逐票抓取禁止进入市场温度。

---

## 5. 公共治理与验收

1. **一切行为变更走状态机**:`PREREGISTERED → RECOMMENDED → APPROVED → ACTIVE`,人工
   approve/activate 记操作者;IMMATURE/UNKNOWN/FAIL 不批;0 BUY 不是放松门的理由。
2. **统一实验模板**:H0/H1、数据 cutoff、paired unit、聚类方式、最小样本/功效、等价或
   no-harm margin、多重检验、停止规则、rollback。未显著与等价结论严格分开。
3. **每案至少一个会变红的探针**;不能只断言「行数>0」,还要验证非空率、freshness、
   definition hash、late-arrival、幂等和输出随真实输入债务变化。
4. **成本分栏**:工程/审查成本、数据与存储、网络限频、LLM input/output/cache、墙钟、
   运行失败风险分别估算。「确定性脚本=零成本」「20KB≈token 可忽略」均删除。4.3 的
   `$5–8` 仅作为 A/B 待测假设;D4 用实测 tokenizer/prompt delta;PDF 冷存储单列。
5. **权威冲突显式化**:4.1 依赖 Wave10 A11 口径;F1/F2 和 4.3 与 Wave9 期权/L4 调度
   直接重叠。本稿只能通过 §0.4 继承矩阵引用、替代或申请重裁,不得再声明「无重叠」。

## 6. 建议优先序(优先级 × 变更类别,仅建议)

### P0 · 高信息增益的 M 类测量/勘误

1. 修正 4.1 gate v3 evidence_manifest,删除 shrink 裁门路径;
2. 补 L2 `selection_reason`、条件/端到端 winner-capture SLO;
3. 补 replay `variant_spec + definition_hash + 独立输出根`;
4. 为既有新闻/公告湖生成 inventory + 1 日 manifest/PIT fixture;
5. 固化期权分页/CFFEX/QVIX 非空契约,把 F3 标 `BLOCKED_BY_DATA`;
6. 加固现有 nightly runner 的 heartbeat/lock/幂等观测,暂不加债务硬闸;
7. 补 L3 pass1 kept/provenance 和等价性 power 设计。

### P1 · I 类数据基建与影子取证

- news_catalog 三时间+stage cutoff、claim ledger 与真实 tool telemetry;
- 分品种/期限/名义归一的 options lake;
- D4 同票 paired shadow;4.4 tier-1/tier-2 按功效触发;
- O4 在锁定 variant contract 后做探索 replay;
- 4.3 仅做 capability/chaos probe,不改生产调度。

### P2 · B 类生产行为(registry 全流程)

- intel 先读目录、typed-event/L3 证据注入;
- regime cap/floor、PCR/F2 进入策略师或 regime;
- 门重校准、债务 hard GATE0、nested L4 调度;
- 任何新 composite/召回通道。O2 保持 REJECTED;F3 在历史转股价 PIT 门通过前不排产。

## 7. OPEN-Q 汇总

| # | 问题 | 归属 |
|---|---|---|
| 1 | 快讯各源的增量键、条款、限频、first_seen 和 schema/freshness 契约 | D1 |
| 2 | QVIX 各序列历史深度、关键列非空和异常值;自算 IV 的 forward/dividend/solver 契约 | F1 二期 |
| 3 | ~~CFFEX opt_daily 覆盖~~ **已解决**(20260731:720 行);残留 opt_basic 分页与产品/期限映射 | F1/F2 |
| 4 | LLM 诊断腿:schedule 云 vs headless vs 人工;环境、权限、成本和质量如何验收 | 4.2 |
| 5 | nested workflow 的基本可调用性、取消/失败/恢复、task-book lease 与缓存语义 | 4.3 |
| 6 | 东财逐票源长期稳定性及与 push2 同宿主风险;失败时只降级该源 | D1 |
| 7 | 历史转股价 PIT 数据从何获得;`cb_price_chg` 权限或公告解析是否可行 | F3 |
| 8 | degraded publication 是否允许缺失 L4 卡;默认答案仍是“不允许,assemble 阻断” | 4.3 |

---

_仅供研究,非投资建议。本稿为候选池;任何生产变更须经实验治理流程与人工批准。_

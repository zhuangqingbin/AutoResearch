# 2026-08-03 扫描漏斗下一波候选优化 · brainstorm 设计稿

> **性质:brainstorm 产物、候选池,非调度权威。** 现行总调度权威仍是 Wave10 设计稿
> (`docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md`);Wave9 C/D/E/F
> 未清债仍归 Wave9 稿。本稿不授权任何行为变更 —— 一切生产接线走实验治理
> (`experiment_registry → promotion → 人工 approve/activate`),文中「方案」均指
> 待立项候选,落地时另立 wave plan。
>
> 用户裁定(2026-08-03 会话):**只落文档、不做开发**;深写范围 = 新闻(§1)、
> 衍生品(§2)、L2(§3)+ 全景四件(§4.1–4.4:门审计 / 闭环债自动化 / L4 派发下沉 /
> L3 边际实验);期权线 F1+F2+F3 全写;单文件总纲。
>
> 本稿所有「实测」标注均为 2026-08-03 晚探针/读码结果;所有引用读数注明出处
> (STAGES.md / gate_ledger / 当日 prelude / token_usage)。

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
- **event 通道**:默认停用取证中(pr_20260725_001,首读边际 unique 40 只 −1.01pp t=−1.83,
  ≥10 日 `unique_excess_t2` 累计 >0 才提启用)。本稿 §1.2 的事件类型学**不预设其结论**。
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

---

## 1. 新闻能力强化(话题 2)

### 1.0 现状接线图(读码 + 当日实测)

| 层 | 现状 | 病 |
|---|---|---|
| L3 公告 | tushare `anns_d` 已退役(2026-07-18 起无权限)→ `anns_fallback`(cninfo)对空桶逐票补(Wave9 C-1;2026-08-03 实测兜底承载 3,119 行);标题关键词打方向 tag:利多/利空词表 + 否定词中性化 + 监管旗(`scan/agents/l3_news.py:29-44`) | 只有标题无正文;兜底源单点;无历史留存 |
| L4 情报 | l4-intel(sonnet·max)六面盲搜 WebSearch/WebFetch;自报 cap 20 指令级天天超(08-03 实测 21–34 条);`hard_cap=30` 有强制力(`scan/l4/intel_guard.py`,超硬顶拒稿 `.rejected.md`);价格断言已有 OHLCV 对账(`price_claims.py`) | 捏造(pr_20260714_006)与日期焊接(pr_20260716_003)两条 P0 前科;每天几百次网查一次性用完即弃,无留存无复用 |
| 事件端 | 回购/增持/调研三端点入湖(`scan/events.py` 复用 `l3_catalyst.catalyst_counts`);催化旗 24/203(08-03) | 覆盖窄(仅三类);event 通道停用取证中 |
| 宏观/策略师 | 『实时网查』带 as-of 标注 | 同样不留存 |
| 数据源探针(08-03 实测) | tushare `news` **无权限**;akshare `stock_news_em`(东财逐票搜索)**可达**(10 行/票);快讯族:`stock_info_global_em` ✅200 行(标题/摘要/发布时间/链接)、`stock_info_global_sina` ✅20 行、`stock_info_cjzc_em` ✅400 行、财联社 `stock_info_global_cls` ❌404;本版 akshare 无 `stock_telegraph_cls` | 全仓库**没有任何持久化新闻存储** |

**核心诊断**:新闻面三个结构性缺陷 —— ①**不可重放**(retro/t1 永远无法回答「当时新闻面
有什么」,而「产物能证明跑过什么、不能证明没跑过什么」);②**每日重复劳动**(intel 网查
数百次,次日全部重来);③**无源治理**(捏造/焊接靠事后 pr,无源级信誉记账)。

### 1.1 D1 · 新闻湖 news_lake(核心件)

**目标**:夜间零 LLM 把「公告 + 逐票新闻 + 快讯流」拉进带双时间戳的只增湖;供复盘重放、
intel 先读、L3 第二源。**非目标**:不进 L0–L2 打分(零 LLM 铁律)、不做盘中实时流、
不做全文 NLP、不做情感模型。

**数据模型**(`context/lake/news/` 下按 ingest 日分片,只增不改):

```
news_items:
  id            # sha1(url_norm | title | publish_date) —— 去重键
  publish_ts    # 源标注的发布时间(可缺,缺则 NULL 并计数)
  ingest_ts     # 我们拉到的时间(夜间批 = 全批一致;PIT 唯一可信锚)
  source        # cninfo | em_search | global_* | ...(可插拔)
  url, title, body(可空), codes[](ticker 链接), event_tags[](§1.2 复用词表)
```

- **PIT 纪律**:复盘/回放/任何反事实只认 `ingest_ts ≤ 截点` 的行 —— `publish_ts` 是源
  自报、可被回填篡改,只作展示。夜间批意味着「D 日湖」在 D 晚才齐,**当日扫描用的是
  D−1 及以前的湖 + intel 实时网查增量**,两者角色写死:湖=底仓,网查=增量。
- **lake 教训继承**:写湖剥掉窄 fields(lake-narrow-fields-poisoning);cache key 含全参。
- **拉取范围**(关键设计选择,不追求全市场):全市场公告列表(cninfo,已是生产事实)+
  逐票新闻仅拉「L2-203 ∪ dossier 池 ∪ pinned」(~250 只/日 × 10 条)+ 快讯流(实测
  可达三源:`stock_info_global_em` 200 行 / `stock_info_global_sina` 20 行 /
  `stock_info_cjzc_em` 400 行;财联社 404 不可达,放弃)。估算 <5MB/日、~100MB/月,
  忽略不计;保留期默认 365 日。
- **源契约分级**:全部 B 级(降级记账不阻断);每源记限频/增量键/当日行数,行数骤降
  →prelude 汇总屏 ⚠(源可插拔,单源死不伤湖)。

**消费三接口**(各自独立立项,湖先行):

1. **复盘重放**:`news_lake.replay(code, date)` → retro/t1 的诊断输入多一节「当时新闻面」。
   纯增量,零行为变更。
2. **intel 先读湖**:l4-intel prompt 注入「湖内该票近 10 日条目摘要」,指令改为「湖里已有
   的不再网查」。**这是行为变更**(改 intel 输入)→ registry 影子:对比启用前后网查数/
   耗时/卡质量,预期 cap 超限从「天天喊」变结构性缓解。
3. **L3 公告第二源**:`l3_news` 空桶兜底顺序变为 湖 → cninfo 实时(湖命中免网络)。

**最小证伪步**:用已实测可达的 3 个快讯源 + cninfo + `stock_news_em` 各拉 1 日真数据落
一个原型分片,verify 去重率与 codes 链接命中率 —— 链接命中率 <70% 就先修词典再谈湖。
**验收探针(会变红)**:湖日行数>0;`publish_ts` 缺失率<50%;重放接口对已知日吐已知行
(fixture);intel 影子期网查中位数下降(会变的量)。
**回滚**:湖是只读消费,拆接线即回退;湖本身留着无害。

### 1.2 D2 · 确定性事件类型学(typed events)

现状词表已在 `l3_news.py`(利多/利空/`_NEGATORS`/`_STRONG`/`_REG_WORDS`),但只产出
「方向 tag」。升级为 **typed event 记录**:`(code, ann_date, type, direction, strength)`,
type ∈ {预增, 预亏, 回购实施, 回购预案, 增持, 减持, 中标/订单, 问询/关注函, 立案,
重组/收购, 定增, 澄清}(词表起点=现 `_EVENT_TAGS`,规则纯确定性)。

- **用途分层**:①L3/L4 证据列(展示,无行为变更,先行);②候选因子 —— **每 type 单独**
  走 factor_lab(D 日信号 = 近 N 日该型事件有无/计数,forward=fwd_2_oc),入召回前置门
  = 与 event 通道同一裁决框架(`channel_audit unique_excess_t2` 累计 ≥10 日 >0),
  **不另立更松的标准**。
- **假设纪律**(继承 §0.2):预告披露后追买已被否 → 业绩类事件的 H0 只能设在「披露前
  预期变化」或直接不做;非业绩类(回购实施/增持/中标)才是主战场;一律不用当日涨幅。
- **最小证伪步**:直接用湖内 cninfo 历史标题 + factor_lab 回填各 type 的 IC —— 零新数据、
  零 LLM,一晚出结论;IC 全平则 D2 止步于「证据列」。

### 1.3 D3 · 情报治理 v2

1. **引用契约 lint**(结构性,进 `intel_guard`):intel 稿每条事实 claim 行必须带
   `[source|date|url]`;缺引用率超阈值(建议 30%)→ 拒稿改名 `.rejected.md`,card 侧
   presence-gate 自动回退卡内网查(与 hard_cap 同通道,**只拒稿不拒票**)。
2. **日期焊接检测**(依赖 D1):claim 内日期 × 湖 `publish_ts/ingest_ts` 对账,对不上打
   ⚠ 注记(不拒稿 —— 湖覆盖不全时保守);治 pr_20260716_003「原子数字全真、组合为假」。
3. **源信誉台账**:t1_review/retro 证伪某条 claim 时回写 `(source, verdict)`;月度聚合
   per-source precision;低信誉源进 intel prompt 的「需二源确认」名单。**自动腿断言**:
   台账行数必须随证伪事件增长(会变的量)。
4. cap 数值不再动(15→20 已做过一次):结构性解法是 §1.1 消费②(先读湖少网查),
  `hard_cap=30` 保持唯一牙齿。

### 1.4 D4 · finalist 公告正文(≤10 只/日)

对当日 finalist(≤10)拉近 10 日**关键公告全文**(预告/问询/回购/增持;cninfo PDF/HTML
→ 文本入湖),l4-card P2/P4 注入**限额摘录**(注入 cap 明确写死:每票 ≤2,000 字节、
超限截断加 `[截断]` 标记 —— §0.3-3 预算条款)。

- **直接假设**:治「业绩真兑现门」错杀率 60%(§4.1)的证据薄之病 —— 门员现在只看得到
  标题与预告数字,看不到扣非口径/有效期/前提条件。
- **验收**:挂 §4.1 的错杀率曲线(D4 上线前后对比);卡片价格/事实断言对账通道复用现有
  `price_claims`。
- 成本:10 票 × ~3 份 PDF,取数分钟级;token 增量 = 注入 ≤2KB×10。

---

## 2. 期权/衍生品线(话题 3)

### 2.0 结构性裁定

**A 股没有个股场内期权。** 场内衍生品 = ~10 只 ETF 期权(50/300/500/创业板/科创50 等)
+ 中金所指数期权(IO 沪深300 / MO 中证1000 / HO 上证50);券商场外个股期权数据不公开
(中证报价系统仅月度聚合)。→ **「期权信息做个股召回」结构性不可行,钉死。** 期权
信息能落的三个真形态:市场地形(F1)、风格温差(F2)、以及**可转债作为个股级替身**(F3)。

**明确不做**:HK 个股期权代理(覆盖窄+数据费+映射稀)、场外期权、期权直开 L1 通道。

### 2.1 数据可行性(2026-08-03 实测)

| 探针 | 结果 | 备注 |
|---|---|---|
| tushare `opt_basic`(SSE) | ✅ 12,000 张合约 | 元数据全 |
| tushare `opt_daily`(20260731, SSE) | ✅ 752 行 | **无 IV/Greeks 列**(close/settle/vol/oi);全市场单日不带 exchange 会连接重置,按交易所分次拉 |
| tushare `cb_basic` | ✅ 1,156 只 | 含 `stk_code` 正股映射 |
| tushare `cb_daily`(20260731) | ✅ 308 行 | 转债日行情全通 |
| akshare `option_finance_board` | ✅ 28 行(50ETF 单月) | keyless 兜底源 |
| tushare `news` | ❌ 无权限 | 付费新闻接口不可用(§1 已绕开) |

### 2.2 F1 · 市场地形(PCR/持仓结构 → `market_pack.derivatives`)

**一期(零模型,推荐先做)**:每晚从 `opt_daily` 算 —— 全市场及分品种 **PCR(成交量/
持仓量两口径)**、总持仓 Δ、成交额结构(认沽/认购分布)。落
`market_pack.derivatives` 新块(`frame.py` 生产,B 级契约:缺了不阻断、记账)。

**二期(IV 分位 / 25Δ skew / 期限结构)**:探针意外发现本版 akshare **有现成波指接口族**
(`index_option_kcb_qvix` / `index_option_kcb_min_qvix` 实存于函数表;50ETF/300ETF 同族
待探)—— 二期优先探 qvix 族,**可达则免 IV 自算**;不可达才落 BS 反解(r 取 SHIBOR-3M
或固定 2%,q=0,假设写进代码注释 —— OPEN-Q-2)。**YAGNI:二期不预排,一期读数攒 ≥60 日
且与 regime 转换有对齐迹象才立项。**

- **消费**:①温度 v2 的候选输入(展示先行,不碰决策);②策略师地形段。⚠ **接线注意**:
  Wave10 A4 后策略师只读 `strategist_pack`(allowlist 投影,新键默认进不来)—— F1 要进
  策略师视野必须显式加白名单,这是特性不是坑,加名单时同步核对防锚定条款(不得含
  个股/行业方向指令性字段)。
- **验收探针**:derivatives 块日日非空;PCR 时序与 regime 标签的对齐读数报表(≥60 日);
  上线首月 L3/L4 prompt 字节 diff = 0(证明未泄漏进判断层)。
- **最小证伪步**:拉 60 个历史交易日 opt_daily(湖化),离线算 PCR 与既有 regime 序列的
  相关 —— 相关全无则 F1 降级为纯观赏,不接温度。

### 2.3 F2 · 风格温差(小票 vs 大票风险偏好)

MO(中证1000)与 IO(沪深300)的 IV 差是理想口径,但依赖二期 IV;**一期代理**:两品种
PCR 差 + 成交额比。定位 = regime 判定的**第四信号候选**(现 regime 只有 breadth+动量,
`common/regime.py`)。路径:factor_lab 检验其对 regime 转换/次日 breadth 的先行性 →
有据才提 regime 判定改版实验(regime 判定改版会改权重选块,是行为变更,registry 全流程)。
**风险**:中金所数据源可达性未单测(opt_daily 覆盖 CFFEX 与否待验,OPEN-Q-3)。

### 2.4 F3 · 可转债个股因子(期权信息落到个股的唯一真形态)

**探针已证数据全通**(tushare cb_basic 1,156 / cb_daily 308 行·日,`stk_code` 映射自带;
keyless 兜底同样实测可达:akshare `bond_cb_jsl` 集思录截面含正股代码、`bond_zh_cov`
1,047 只转债列表 —— 双源冗余,单点故障不伤线)。候选因子(均为「正股的衍生品市场信号」):

| 因子 | 定义 | 假设 |
|---|---|---|
| `cb_premium_delta` | 转股溢价率 5 日 Δ(需转股价,`cb_basic` 有) | 溢价率骤降=转债资金抢跑正股预期 |
| `cb_turnover_ratio` | CB 成交额 / 正股成交额 | 转债端异动先于正股 |
| `cb_mom` | CB 价格 5 日动量 − 正股 5 日动量 | 跨市场背离 |

- **覆盖诚实**:~300–500 只正股有存续 CB(全市场 ~5,500 的 6–9%)→ 因子只对子集有定义,
  缺失=NaN 重归一(现有机制:「缺端点权限的富因子自动降级 NaN、打分重归一」同款)。
- **风险**:覆盖偏中小盘(样本选择偏差);妖债炒作噪声(T+0、无涨跌停的转债投机盘);
  强赎博弈期溢价率失真(强赎公告期需剔除,`cb_basic` 赎回条款字段可判)。
- **入线路径**(与 consensus 同一门,不走后门):harvest 入 factor_lab → calibrate IC
  @fwd_2_oc(两半稳 + 符号一致)→ 有据才二选一:入 composite 第 11 因子组(权重重校)
  或注册新通道 `@channel("cb_signal", ...)` **默认停用**(与 event 同纪律,floor=0)。
- **最小证伪步**:回填 120 个交易日 cb_daily(湖化,~4 万行),factor_lab 跑三因子 IC
  —— 一晚出结论,IC 全平则整线止步,只留湖。

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

定义两条日曲线:`WC_L1 = |D日 fwd_2_oc 全市场 top-decile ∩ L1-1001| / |top-decile|`、
`WC_L2 = ... ∩ L2-203`。数据源 = retro attribution 已有分桶(抓到/漏在L1/L0),缺的只是
**命名指标 + 时序落盘 + 报警线**(20 日均线跌破自身历史 P25 → prelude 汇总屏 ⚠)。
落 `learning/` 新 ledger 或并入 channel_ledger(实现时二选一,倾向后者少一个文件)。
**这是 L2 唯一该被考核的指标** —— 菜单的职责是别把赢家漏掉,不是预测谁赢。

### 3.2 O2 · 桶维度换代:52 周高距离族

`docs/research/2026-07-18-dist-high-252-ic.md` 已证:52 周高距离族因子家族内胜现用
pct_60d(t≈+2.62),是 momentum 判据换代候选。两案:

- **(a) 换判据**:趋势桶成员资格从「momentum|heat 通道命中」改为 dist_high_252 门;
- **(b) 增桶**:新增「新高邻域」桶 floor=10,趋势桶 20→15(Σfloor 不变,少动 merit_need)。

**一律走漏斗回放器**(`research/replay.py`,权重 PIT 六条已建;`current` 权重有泄漏
只准对拍)。裁决尺 = WC_L2(§3.1)+ 菜单健康四项;两半稳定才提 registry。倾向 (b)
—— (a) 改变趋势桶语义,三个 `l2_lane_reserved` 消费者全被波及。

### 3.3 O3 · regime 化 —— 先清算既有半特性

**读码发现**:`stratified_l2/select_l2` 的 `regime`/`regime_caps` 形参**已建未接线**
(`l2_stratify.py:57,81,135` 语义=按 regime 调 sector cap;`universe.py` 全部调用点
`:283-346` 均未传)。这正是 §0.3-5 的「拆半个特性」形态 —— 挂着参数没人喂,下一个
读代码的人会以为 regime 化已生效。

**先做二选一(P0,清算债)**:接线(prelude 把当日 regime 传入,caps 表如
`{risk_off: 0.15, trend: 0.25, range: 0.20}`,replay 验)**或**删参退役(git 可考古)。
**再谈增量**:floors 的 regime 化(risk_off 日健康+5/趋势−5 之类)—— replay 对照
winner-capture 与菜单健康,两半稳才提。风险:risk_off 样本 11 日太薄,floors regime 化
的裁决可能长期 IMMATURE,**预期管理:这是慢变量**。

### 3.4 O4 · cap/floor 参数扫描(replay 网格)

`cap ∈ {0.15, 0.20, 0.25} × floor 全体缩放 ∈ {0.5, 1.0, 1.5}` 九格 replay(现值全是
拍的);目标 = WC_L2,守卫 = 行业集中度/健康占比/落刀面;**两半 + 分 regime 段**分别报,
只有全段同向才动参。已有近亲证据:`capfloor20` 影子变体在跑(cap_floor_yi=20 验市值
地板),复用其对照框架。

### 3.5 O6 · 新特征统一闸门(治理条款)

任何新特征入 composite/新通道,唯一入口:factor_lab `harvest → calibrate`(IC 两半稳 +
符号一致)→ replay 对照 → registry challenger。排队中:consensus(积累 ≥60 日自动触发,
§4.5)、CB 三因子(§2.4)、typed-event 因子(§1.2)。**没有第二条路**(判例:rz 入组
走的就是这条,pr_20260710_001)。

---

## 4. 全景四件(话题 1 升格深写)

### 4.1 门审计 · 「业绩真兑现」门重标定

**证据现状**(gate_ledger 双表,主尺 ex2=fwd_2_oc−市场中位;错杀率表来自
`gate_attribution`,档位 CORRECT/NEUTRAL/FALSE_KILL/UNMEASURED):08-03 prelude 读数
**拦 11 次、拦对率 25%、错杀率 60%** —— 全漏斗数据最难看的单件。但 n=11 极小。

**最小证伪步(立即可做,零成本)**:按 `learning/shrink.py` 口径(shrink_k=15)重算
—— p̂=(11×0.60+15×p_全局)/(11+15),若收缩后错杀率落回 ~40% 均值带,则「60%」是小样本
噪声,本案降级为继续观察,**省掉整个实验**。

**若收缩后仍离群,实验设计**(registry family=`gate_true_delivery_recal`):

- A0 现门(基线);
- A1 证据增强:D4 公告正文注入后同门重跑(先修证据薄,再谈门松紧 —— §0.3-5:收紧
  放宽类先测误放行率);
- A2 影子降级:门判 ✗ 时**影子记录**「若不拦会怎样」(shadow_buys 已有同款机制),
  不改真实评级。
- **裁决尺**:被拦票 ex2 分布 + FALSE_KILL 率,≥20 拦次才裁;**红线**:门总量价值
  +4.35pp 与 Wave10 结论(主力门单独否决 17 次、被拦票平均仅跑赢 +0.63%,总量仍成立)
  在案 —— **禁止直接放宽任何一门**,只允许影子取证后走 registry。

### 4.2 闭环债自动化(retro/t1/档案债)

**证据现状**(08-03 活证):retro 欠 4 天(07-27~30)、其中 3 天备料 71h 烂尾超 48h
SLO;t1 欠 1 天;档案 SLO 三红(最老待建档 7d/≤2d、季度对账债 19、7 日消化 0<新增 7)。
prelude 会列债但**不阻断**,还债靠人肉记得。

**设计(两腿 + 一闸)**:

1. **确定性腿 → launchd**(本机已有 prewarm 先例,安装惯例见 STAGES『运维细节·夜间
   预热』):夜间任务跑 `retro refresh` + t1 备料 + `dossier.reconcile` 批(全零 LLM)。
   **自动腿断言**(§0.3-4):任务写心跳文件带产出行数,prelude 读心跳 —— 静默死亡
   ≠ 没债。
2. **LLM 诊断腿 → 三选一**(OPEN-Q-4):(a) schedule 云 routine 定时跑 scan-retro
   (需验云环境有无 repo/TUSHARE_TOKEN);(b) 本机 cron + `claude -p` headless 跑
   `/scan-retro`(需验 headless 权限模式);(c) 保持人肉但配下面的硬闸。**推荐先 (c)
   +确定性腿,(a)(b) 作为增强探索** —— 诊断质量依赖判断,自动化收益弱于备料自动化。
3. **债务硬闸**:`retro_pending > 3 日 或 备料烂尾 > 48h` → GATE1 从 ⚠ 升级为**拦断**
   (`--force-debt` 可越,越闸记账进 run_contract)。一行判据,纯确定性。**债不还就
   别开新扫描 —— 权重停在旧教训上,扫得越多错得越贵。**

### 4.3 L4 派发下沉(workflow 嵌套滑窗)

**证据现状**(08-03 实测):主会话 $12.82 = 全场 32.7%(挂账线 25%);滑窗每股补派
= 一次主会话唤醒回合(全上下文 cache 读计费),10 只 = 10+ 回合纯调度开销。

**设计**:scan-market.js L4 相位内直接 `workflow({scriptPath:'l4-stock.js'}, args)` 逐股
拉起(工具契约:嵌套恰好允许一层,父=scan-market、子=l4-stock,**每股一个 workflow 的
fb_20260714_003 粒度不变**);JS 内 promise pool 维持 `effective_cap` 在飞,pinned/最长
者先行排序照旧;单子失败 `catch` 住只废单股(07-14「16 字节毙 60min/1.6M token 全流水
线」教训 —— 父绝不因单子 throw)。主会话职责缩为:起父 → 收一次完成通知 → assemble。
预期主会话份额 32.7% → ~15%,并消掉 10+ 唤醒回合。

**风险 / 最小证伪步(先桌演再立项)**:

- **OPEN-Q-5**:父 `resumeFromRunId` 对 `workflow()` 子调用的缓存语义未文档化(只讲了
  agent() 缓存)→ 玩具父 + 两个玩具子,杀父重续,看子是否重跑。**重跑=断点恢复退化,
  是本案最大反对票**(现行每股独立 workflow 的单股可恢复性是 07-14 事故换来的)。
- 并发帽语义变化:现在 N 股 = N 个 workflow 各自有帽;下沉后共享父帽(min(16, cores−2))
  —— 与 `l4_tasks` 的 `effective_cap=4` 对齐即可,反而更准。
- `l4_watch`/CP5 不受影响(读 task_book,不读派发方式)。
- 兜底:`dispatch_batches` 返回契约保留,失败随时退回现行主会话滑窗。

### 4.4 L3 边际价值实验(排序价值计量)

**证据现状**:L3 真选无正 alpha(07-16 去📌污染后真选 −3.8%;finalists 11 日 −0.39pp/2日
t≈−1.2);但 L4 深核推翻 L3 高确信两次全对、trend lane 高确信被翻案 21% —— L3 的价值
可能在**证据组装与防呆**,不在排序。值得计量,不值得拍脑袋撤。

**两层设计(先零成本后花钱)**:

- **tier-1(零 LLM,历史回填,立即可做)**:对过去 N 个扫描日,反事实集 = pass1 分诊
  确定性序 top-K(K=当日 finalist 数)vs 实际 L3 finalist 集;比两集合的 fwd_2_oc 均值
  / top-decile 命中率 / Jaccard。**产物已齐**(`_l3_pass1_cut.csv` + finalists.csv +
  attribution),一晚出结论。若两集合统计不可分 → L3 排序价值≈0 实锤,tier-2 都省了,
  后续动作变成「L3 瘦身」而非「L3 取舍」。
- **tier-2(仅当 tier-1 显示差异,registry family=`l3_marginal_value`)**:live 影子日
  对 divergent picks(L3 选而序没选/反之)各补 L4 卡比终局;token ≈ 每影子日 +2–6 张卡。

**防误读条款**:实验只裁「排序价值」;L3 还承担压缩(200→40 表)、红队、误杀保险
(conviction≥75 补入)、论点生成 —— 结论不自动 = 撤 L3。

### 4.5 一段带过件

- **consensus 预注册**:积累中(report_rc 限频 1 次/小时,<60 日);写死触发条款 ——
  `n≥60 且 两半 IC 同号 且 |IC|>0.02` → 自动生成 PREREGISTERED spec(人批才往前走)。
  自动腿断言:status 命令输出的 n 必须周周增长。
- **档案债清偿排期**:季度对账 20251231 × 19 只 = 一次批跑(确定性);18 只待建档按
  ≤3/晚 ≈ 6 晚消化;不新建机制,纯排期(§4.2 确定性腿捎带)。
- **温度 v2**:F1(PCR)与 D1(新闻量)落地后自然多两个候选输入 —— 跟随件,不单列。

---

## 5. 公共治理与验收

1. **一切行为变更走状态机**:`PREREGISTERED → RECOMMENDED → APPROVED → ACTIVE`,人工
   approve/activate 记操作者;IMMATURE/UNKNOWN/FAIL 不批;0 BUY 不是放松门的理由。
2. **每案至少一个会变红的探针**(§0.3-4);纯观测件也要有「行数在涨」类断言。
3. **成本汇总**(估算,加权 token 口径):

| 案 | 一次性 | 日常增量 |
|---|---|---|
| D1 湖 | 源探针+原型 ~0 LLM | 取数分钟级,0 LLM;intel 影子期 +0 |
| D2 类型学 | factor_lab 回填 0 LLM | 0(证据列渲染忽略) |
| D3 治理 | lint 开发 0 LLM | 0 |
| D4 正文 | — | +≤20KB 注入/日 ≈ 忽略 |
| F1/F3 | 历史回填 0 LLM | 夜间取数 0 LLM |
| O1–O4 | replay 跑批 0 LLM | 0 |
| 4.1 门审计 | shrink 重算 0 | 影子记录 0 |
| 4.2 闭环债 | launchd 装载 | 0 LLM(诊断腿另计) |
| 4.3 派发下沉 | 桌演 ~$1 | **省** ~$5–8/扫描日 |
| 4.4 L3 tier-1 | 回填 0 LLM | tier-2 每影子日 +2–6 卡(~$5–15) |

4. **与 Wave10 无重叠**(Wave10=报告优化×激进退役×0买归因×4影子实验;本稿全部新增面,
   引用其结论不改其调度)。

## 6. 建议优先序(仅建议,不排产;立项时另立 wave plan)

- **P0(零风险,一晚一件)**:4.1 shrink 重算 → 4.4 tier-1 回填 → 3.1 O1 SLO →
  4.2 债务硬闸 + 确定性腿 → 3.3 regime_caps 半特性清算 → D1 原型分片(链接命中率验证)。
  ——P0 六件全部零 LLM、零行为变更,却各自可能**直接终结**一条更贵的路(证伪即省)。
- **P1(replay/影子/湖,1–2 周节奏)**:D1 新闻湖 → F3 CB 回填 IC → O2/O4 replay →
  D3 引用契约 → F1 一期。
- **P2(行为变更,registry 全流程)**:D1-消费②(intel 先读湖)→ D2 入召回 → O3 floors
  → 4.3 派发下沉 → 4.1 A1/A2 → 4.4 tier-2 → F2。

## 7. OPEN-Q 汇总

| # | 问题 | 归属 |
|---|---|---|
| 1 | ~~快讯族可达性~~ **已解决**(em 200 行/sina 20 行/cjzc 400 行 ✅,财联社 404 ❌);残留:各源增量键与限频细化 | D1 |
| 2 | IV 口径:优先探 akshare qvix 族(kcb 已证实存),不可达才 BS 自算(r/q 假设) | F1 二期 |
| 3 | tushare opt_daily 对 CFFEX(IO/MO)的覆盖与权限未单测 | F2 |
| 4 | LLM 诊断腿自动化路径:schedule 云(repo/token 可达?)vs `claude -p` headless vs 保持人肉 | 4.2 |
| 5 | 父 workflow `resumeFromRunId` 对 `workflow()` 子调用的缓存/恢复语义 | 4.3 |
| 6 | 新闻湖逐票源的长期稳定性(东财搜索接口与被封的 push2 同宿主风险) | D1 |
| 7 | CB 因子的强赎期剔除规则细化(赎回条款字段口径) | F3 |

---

_仅供研究,非投资建议。本稿为候选池;任何生产变更须经实验治理流程与人工批准。_

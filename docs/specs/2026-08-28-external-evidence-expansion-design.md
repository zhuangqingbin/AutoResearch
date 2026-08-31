# 外源实时信息扩面设计稿(2026-08-28):宏观 / 中观 / 单公司 × 美股 × 期权

> **状态(2026-08-29)**:**I 类已实施**(D-1 源模块 / D-2 隔夜窗日历 / D-3 analyze / D-4 macro / D-5 留痕 / D-6 sector)。**B 类(B-1…B-5)一件未做**,按 08-26 A0 冻结到 09-中;`tests/scan/test_frozen_b_class_boundary.py` 把这条边界做成了会变红的守卫(指令级约束的失败率不为零,数据级为零)。**D-0 已跑**(`docs/research/2026-08-29-overseas-event-window-census.md`):**H0 未被推翻,四主族零正证据** —— CPI/NFP「未证」(Holm p=1.00,|effect|≤0.16),FOMC/mapped earnings「样本不足」(上游名录空,不是功效不足);唯一过门的探索族 GDP 被后验证伪为 A 股长假窗的影子。**按 Q7 没有任何一族够格进影子实验**,B-2/B-3 不因 D-0 获得放行理由;D-2 的日历/哨兵不受影响(风险可见性不要求 alpha)。§9 的 **7 天 source soak 是运营前置,代码无法代跑**:源模块已就位且全部 B 级降级,但「确定性主源」的资格要等 soak 读数。
> **性质**:设计稿(原为零实施稿;实施后保留原文,只加本状态行)。条目分 **I 类**(数据基建 / 展示层 / 独立技能,零 L3-L4-策略师 prompt diff,现在可做)与 **B 类**(改变 scan 判断层输入,受 08-26 A0 冻结,09-中攒 20 结果日后带开关做)。
> **证据**:12 个外源触点盘点(§1);11 个免费 / 已配置凭证源的单次探针于 2026-08-28 22:57 CST **全通**(附录 A,抛弃型脚本,读网不写盘;FRED 使用既有 `FRED_API_KEY`,并非 keyless);衍生品普查读数(08-24)与隔夜集中信号普查(08-28)作为约束;`file:line` 为工作树现状。标 **UNVERIFIED** 的是推断。
> **用户诉求原文**:「研究时候实时的外源信息要考虑扩大面,特别是美股的,在做宏观中观单公司的时候都需要考虑;还有期权市场信息的引入(先考虑在哪个阶段)然后考虑怎么引入」。
> **姊妹稿**:`2026-08-28-summary-slimdown-design.md`(本稿 D-2 的日历行落在其 §4.1 节 9)。

---

## 0. 边界(既有裁定,不重提)

| 裁定 | 对本稿的约束 |
|---|---|
| 防锚定三层同律:策略师 §1–3 描述性 / 行业 brief 只有地形段 / 个股评级只由 L4 rubric 三门决定 | 任何海外 / 期权信息进 L3/L4 只能是**数字地形与事实日历**,不得含方向 |
| BUY owner = E6;主尺 `gap_c1_o2`(T+1 尾盘买 → T+2 开盘卖)三次裁不换 | 本稿不做 alpha 主张,不改主尺、不改 E6、不新增 L0–L3 硬门(「每加一条硬门 = 一块永久盲区」) |
| 08-24 衍生品普查:QVIX / PCR / 到期日历 三族 **零正证据**;IC/IM 基差唯一正读数但换 close 口径即消失、近两年衰减;「别再提波指温度计 / PCR 进 regime / A2 哨兵」;个股期权召回不可行;max-pain / HK / OTC 代理不做;PCR 不得渲染为看空、不得跨品种加总 | A 股期权**不进** scan 任何阶段(§5.1) |
| 08-28 隔夜普查:R 族(报告能用)全负逐年同号;H0 未被推翻 | 给 scan 加信息的理由只能是**风险 / 事件可见性**,不是选股 |
| learning 层退役(08-21);L4 复用不恢复;`scan_config.jsonc` 唯一流程参数事实源(新参数 = 白名单 + 消费点 + 测试);双引擎隔离、只共享 `lake/` | 新参数按三件套;新湖表进 `lake/` 走 `cache.get_or_fetch`(不学普查绕湖) |
| 数据契约:A 级空即抛异常,B 级降级但必须记账 | **本稿所有新源一律 B 级**(缺失永不阻断 run,`record_degradation` 记账) |
| 08-26 A8 新闻层裁决未决:「接成 l4-intel 已知底 + cron」或「整包退役」 | 本稿按「接线」方向设计(§6),Q1 待裁 |
| 08-13:复盘 / 反馈流程不得改 `.claude/**` | 本稿的 agent def 改动全部是**人批设计稿**改动,不是自动流程 |
| 事件 / 海外链用途 = 风险可见性 | 日历、映射、期权量级均为**人审提示**,不得自动否决入场、改仓位、改评级或推导方向 |

---

## 1. 现状:12 个触点,四种病

触点(读码,详表见工作树 `self_review.py` / agent defs):

| 层 | 触点 | 机制 | 面 | 上限(全部指令级) |
|---|---|---|---|---|
| 宏观 lite(策略师) | `macro-brief.md:31` | LLM 网查 | 宏观 / 政策头条 | ≤2 |
| 宏观 full | `macro/harvest.py:52-76` | 确定性 FRED 16 + INTL 3 + yfinance 13(含 `^VIX` 点值) | 数字 | 取数失败 → 网查指令串,无上限 |
| 中观 lite / full | `sector-brief.md:12`;`sector-playbook.md:35` | LLM 网查 | 行业头条 / 产业证据 | ≤2 / 无 |
| 微观 lite:情报站 | `l4-intel.md` | sonnet 盲搜 六面 | 公告 / 突发 / 题材 / 机构 / 互动易 / 负面 | cap 20(`scan_config.jsonc:182`);**实测 22–37**(08-26 八稿超限) |
| 微观 lite:卡 P3/P4 | `l4-card.md:33-34` | LLM 网查 | 催化 / 机构 | ≤1(有情报)/ ≤3+≤2,硬上界 5 |
| 微观 full | `analyze/harvest.py:417-467,1157-1243` | 确定性 yfinance `.news` / 东财 `stock_news_em`;美股加 SPY regime / RSP 宽度 / 行业 ETF / `^VIX` / `options_iv_summary`(`:152-180`,最近一个到期的 ATM IV + 跨式隐含幅度 + PCR OI,slim 关) | 数字 + 14 日新闻 | 网查无结构、无上限 |
| 首覆 | `dossier-init.md:23` | LLM 网查 | 年报 / 产业链 | ≤4 |
| L3 公告情感 | `scan/agents/l3_news.py` | tushare `anns_d`(07-18 起无权限)→ cninfo 兜底 | 标题 | — |
| 持仓哨兵 | `tripwire_watch.py:126` | `stock_news_em` 标题扫描 | 事件旗 | — |
| 夜间预热 | `prewarm.py` / `eastmoney_hot_rank.py` | 东财人气榜快照 | 热度 | — |
| 新闻目录 | `autoresearch/news/`(catalog / claim_ledger / fulltext / typed_events,2,167 行) | 确定性 | — | **建成未接线**:三个 B 类消费口本波全关(`prelude.py:434-470`) |
| 法证留痕 | `trace/capsule.py:1629-1691 _external_tool_rows` | 每次 WebSearch/WebFetch 落 `capsule/lineage/external_tools.jsonl`(role / stage / subject / url / result_hash / blob) | — | 需 transcript 绑定;`source_lineage.py` 只钩数据湖返回点,**不含网查** |

四种病:

1. **面窄**:美股只在 stock-research full 的 harvest 里以「美股市场语境 + 一个到期的期权摘要」出现;macro 的 13 个跨资产只落 markdown,不进任何机读 pack;`market_pack` / `strategist_pack` 里**零**全球键(`scan/market.py` grep `vix|us_|global|overnight|hsi` 无命中);A 股公司 / 行业与海外链的映射不存在。
2. **限频无强制**:五处 cap 全是指令级,事后靠稿件**自报**对账(`self_review.py:988`「限频是指令级、无强制力」);真实调用次数其实已在 `external_tools.jsonl` 里,没人读。
3. **留痕两套不汇合**:确定性网络源走 `source_lineage`;LLM 网查走 capsule external_tools(harness 级:有 URL / hash,无「哪条 claim 引了它」);claim → observation 的语义账 `claim_ledger` 建成未接线。
4. **时效契约只有情报站有**:三窗(T0 / 24h / 背景)+ 净分衰减 + `intel_window_mismatch` 机检只覆盖 `_l4_intel`;策略师 / 行业 / full 档网查只有一句「标日期」。

---

## 2. 一个结构性事实:主尺窗口横跨一整个美股交易日

主尺 = T+1 15:00 CST 买 → T+2 09:30 CST 卖。美股常规时段 09:30–16:00 ET = 21:30–04:00 CST(夏令;冬令 22:30–05:00),盘后财报到 20:00 ET = 08:00 CST(冬令 09:00)。**T+1 的美股常规时段、T+1 盘后财报、T+1 FOMC(14:00 ET = 02:00 CST T+2)全部落在持仓窗内,且在 A 股 T+2 开盘前结束。** 08-26 run 的例子(yfinance 核实):NVDA 2026-08-26 16:00 ET 盘后财报 = CST 08-27 04:00,次日开盘 +6.3%(209.66 → 222.86);对 08-26 21:20 发布的 run,它落在「入场前」窗(T+1 14:45 买入决定之前就已知),summary 📅 日历里没有它,📌 持仓 300857(消费电子 / 算力链)也没被提醒。这个例子只说明**漏报风险**,不构成映射股存在可交易因果的证据。

所以「美股信息」对本项目不是宏观背景,而是**隔夜窗内的已知事件**。按 T 晚报告的时点分两窗:

| 窗 | 区间(CST) | 内含美股事件 | 用途 |
|---|---|---|---|
| **入场前** | (报告时刻, T+1 14:45] | 美股 T 常规时段(21:30 T → 04:00 T+1)+ T 盘后财报 | 既有「入场否决(T+1 尾盘前)」栏的人审核对项;持仓哨兵 |
| **持仓隔夜** | (T+1 15:00, T+2 09:30] | 美股 T+1 常规 + T+1 盘后 + T+1 FOMC / CPI / NFP(08:30 ET = 20:30 CST T+1) | 隔夜风险可见性;📌 持仓 tripwire |

T / T+1 / T+2 按 A 股交易日历(`trade_days`)以**数据日**为锚;报告迟到的 run 由 `exec_anchor` 的 actionability 另算,日历本身不变。所有换算以 IANA 时区 `Asia/Shanghai` / `America/New_York` 与各自交易所日历计算,禁止写死 UTC±8 / ET 偏移;必须覆盖 DST、美国休市、A 股休市。

生产日历还必须区分「发生了」和「当时已知」。统一事件契约:

```yaml
event_id: string
event_type: macro_release | fomc | earnings | regulator | other
subject: string
scheduled_at_utc: datetime | null
local_date: date
timezone: IANA timezone
time_quality: TIMED | DATE_ONLY | UNKNOWN
source_url: https://...
first_seen_ts: datetime
revision: string
status: scheduled | rescheduled | cancelled | released
mapped_symbols: [string]
window: pre_entry | holding_overnight | date_risk | outside
```

只有 `TIMED` 且 `first_seen_ts <= decision_cutoff` 的事件可进入两个窄窗;`DATE_ONLY` 只能显示为「当日风险」,不得猜 AMC / BMO 或触发精确窗口 tripwire。改期保留旧 revision,同一 `event_id` 只展示 cutoff 时点可见的最新版本。历史 D-0 的真实发生日只用于**事后事件研究**,不能倒推生产时点一定可知。

展示先按 `event_id + revision` 去重,再按「映射到 📌 / 当日候选的官方定时事件 > 官方宏观定时事件 > 行业 / 主题事件 > DATE_ONLY」排序;summary 最多 4 行、brief 最多 1 句、每个持仓哨兵最多 3 条,超出写 `+N`。任何一行都只是**人工复核提示**,不自动产生否决 / 仓位 / 评级动作。

---

## 3. 总体设计:两层、两本源账、一个审计索引

```
确定层(零 LLM,接口 / 已配置凭证)                    判断层(LLM 网查)
  源模块 data/sources/*  ──→ lake(B 级契约)          外源情报契约 v2(沿用 l4-intel 骨架:
        │                                             窗 / 面 / URL 必落 / 净分衰减 / 声明行)
        ▼                                             按海拔定义「面」;cap 由派发 prompt 注入
  派生 pack:global_tape.json / overseas_calendar.csv          │
           / harvest 的 us_evidence & readthrough 块          │
        │                                                     │
        ▼                                                     ▼
  lake + source_lineage                               capsule external_tools
        │                                                     │
        ├──────────── news / event ─→ news_catalog ───────────┤
        │                                  │                  │
        │                                  └→ claim_ledger    │
        └──────────────────────┬──────────────────────────────┘
                               ▼
          capsule/lineage/external_evidence_index.json(只读引用索引)
```

不把三类异构证据硬塞进 `news_catalog`:

- 数值行情 / 期权 / 结构化监管数据继续以 `lake + source_lineage` 为事实源;否则 `available_stage` 等新闻语义会污染数值表。
- 新闻 / 事件使用 `news_catalog + claim_ledger`,保持其既有 `L0…L5` PIT 语义;**不新增** `standalone` stage。独立技能在自己的 trace 中记录 `decision_cutoff` / context,不参与 scan stage 比较。
- LLM 搜索 / 抓取保留为 capsule `external_tools.jsonl`;审计索引只引用三类现有记录的 row id / hash,不复制 raw blob。

四条原则:**能确定性取的不网查**(RSS 只负责发现,期权链 / EDGAR / 日历走接口);**网查只做接口拿不到的事**(电话会要点、政策解读、产业链传闻);**原始证据各归其账,审计时统一索引**;**冻结后的 capsule 不再回填或追加文件**。

### 3.1 来源权威与 claim 消费纪律

| 层级 | 例子 | 可否支持 material claim |
|---|---|---|
| T1 原始 / 官方 | 监管、交易所、公司 IR、政府 / 央行、官方结构化接口 | 可,仍需时点与字段匹配 |
| T2 高质量二手 | 主流财经媒体、持牌数据商 | 可,必须落原文 URL / 时间 / 摘要 |
| T3 发行人 / 行业二手 | 供应商、行业协会、公司博客 | 可作主体陈述,不得伪装成独立验证 |
| T4 聚合 / 发现 | Google / Bing RSS、搜索结果摘要 | **不可单独支持**;必须跟到 canonical 原文 |
| 禁用 | 匿名搬运、无法定位原文、社交媒体传闻 | 不进 material claim |

`claim_ledger` 状态的消费口径固定为: `VERIFIED` 可进正文判断;`UNVERIFIED` 仅进证据附录 / 待核,不得影响评级;`REFUTED` / `UNPARSED` 从正文排除并记诊断。无效外证只淘汰或降级**该条情报稿 claim**,绝不淘汰股票。搜索结果 snippet 只能证明「搜到过」,不能令 `content_supports=true`;必须有抓取到的 canonical 页面或官方结构化 blob。

URL 入账前统一 canonicalize:去追踪参数 / fragment、跟随允许域重定向、仅接受公网 `http(s)`,拒绝 localhost / 私网 / `file:`。受版权约束的新闻默认只存 URL、元数据、短摘要与内容 hash;raw blob 仅留本地私有 trace,不提交仓库。

### 3.2 已排除的替代方案

| 方案 | 裁定 | 原因 |
|---|---|---|
| 数值、新闻、网查全塞 `news_catalog` | 排除 | stage / revision 语义不相容,会制造 `standalone` 假 stage 与 raw 重复 |
| 三类证据各留各处、不建统一索引 | 排除 | 复盘无法从 claim 一跳定位 source / blob / cutoff |
| finalize 后由 post-run 回填统计 | 排除 | 破坏 freeze / MANIFEST / replay,且实现时序上读不到刚物化的 external tools |
| Google / Bing RSS 直接作为 material evidence | 排除 | 聚合标题 / snippet 不是原文,可达性也未经长期验证 |
| 为全部映射票每日抓四条期权链 | 排除 | 成本、限流与稀疏质量不成比例;改为 30D 窄快照 + full 按需 |

---

## 4. 按海拔落位(核心表)

| 海拔 | 入口 | 确定层新增 | 判断层新增(面) | 消费点 | 类别 |
|---|---|---|---|---|---|
| 宏观 full | macro-research | `global_tape`(19 标的隔夜 tape)+ 波动率与仓位地形(§5.3)+ 未来 7 日海外日历(FRED releases + FOMC 表)| `global-intel` 六面(§7.1),cap 8 | `data.md` 新三段 + `context/macro/<date>/global_tape.json` + `macro_state` 新字段(只写) | I |
| 宏观 lite(策略师) | scan Stage 0 | `market_pack.global_tape`(帧口径) | 不变(≤2) | `strategist_pack` allowlist 加 `global_tape` 一行描述性数字 | **B**(B-1) |
| 中观 full | sector-research | 行业 ↔ 美股映射(§8):对应 ETF / 龙头隔夜、财报日 | `sector-intel` 四面(§7.4),cap 6 | pack 新键 `readthrough` | I |
| 中观 lite | scan Stage 1 brief | 同上 | 不变 | 地形段 +1 行「海外映射(事实)」 | **B**(B-4) |
| 微观 full · 美股 | stock-research(NVDA) | options v2(§5.2)+ EDGAR 近 90 日 + 分析师行动 30 日 + Google News en 14 日 + 财报历史实际波动 | `us-intel` 六面(§7.2),cap 12 | 报告新节「期权与仓位地形」「外源事件账」;催化 & 定位分析师消费纪律 | I |
| 微观 full · A 股 | stock-research(600519) | 海外映射块(≤4 个美股名:上一完整交易日 % / 5 日 % / 下次财报日 + 隐含波动)+ Google News zh 14 日候选 | `company-intel` 六面(§7.3),cap 12 | 报告催化 / 风险节 | I |
| 微观 lite(L4 卡) | scan L4 | slim +「海外映射事实」2 行 | intel 第七面(cap +3) | 卡 P3 催化维 | **B**(B-2 与 B-3 分开灰度) |
| L5 / brief | scan | `overseas_calendar.csv`(两窗事件行) | — | summary 📅 +「隔夜窗海外事件」;brief ⑤ 风险哨 +1 句 | I |
| 📌 持仓哨兵 | `tripwire_watch` | 同上作为事件旗 | — | prelude 汇总屏 | I |

**为什么 scan 侧几乎全是 B**:任何进 L3 表 / L4 简报 / 策略师 pack 的键都改变判断层输入(08-03 §2.2 的分类:「加入 strategist_pack allowlist 或温度 / regime 即为 B」)。L5 日历与 brief 是纯展示 → I。

---

## 5. 期权信息:哪一阶段、怎么进

### 5.1 阶段裁定

| 市场 | 筛选(L0–L3)| 评级(L4)| regime / 温度 / 策略师 | 研究报告(full)| L5 展示 |
|---|---|---|---|---|---|
| A 股 ETF / 指数期权 | ✗(普查零证据,08-24) | ✗ | ✗(「别再提」) | ✗ 默认不展示;IC/IM 基差仅当 settle/close 依赖与衰减解决后再议(Q2) | ✗ |
| 美股个股期权 | —(不适用) | ✗ | ✗ | **✓ stock-research full(美股票)**:options v2 | 只作日历行的「隐含波动 ±x%」量级注 |
| 美股指数波动率(VIX 族) | ✗ | ✗ | B-1 后仅作描述性一行 | **✓ macro full**:波动率与仓位地形 | ✓ `global_tape` 一行(描述) |

结论:**期权信息只在「研究 / 描述」阶段进,永远不在「筛选 / 评级 / regime」阶段进**;进入点是 stock-research full(美股票)与 macro-research full 两个独立技能 —— 都是 I 类,不碰 scan 判断层。普查证伪的是「A 股衍生品作隔夜领先信号」;美股期权作**研究语境**是未探索区,不是重开旧案。

### 5.2 options v2(`analyze/harvest.py`,替换 `options_iv_summary`)

分两档,避免为了一个日历量级把映射池每天全链抓爆:

- **窄快照(日更)**:仅持仓 / 当日研究票 / 映射票,保存一致口径的 30D ATM IV;由包围 30D 的两个到期插值,不足则明确 `UNMEASURED`。
- **全快照(按需)**:stock-research full 才抓最近 4 个到期 + 财报后首个到期,输出完整 v2。

| 字段 | 算法 | 来源 |
|---|---|---|
| `spot` / `as_of` | spot 与 quote 各自时间戳;同时写 `chain_fetched_at`,`market_state`,`session_complete`,`method_version` | yfinance |
| 数据质量 | 每到期写 bid / ask 是否非负且未 crossed、quote age、OI 覆盖率、有效合约数;质量不够的点不插值 | `option_chain(exp)` |
| 30D ATM IV | 在同一 moneyness / method 下用包围 30D 的有效到期插值;不得把不同 tenor 直接串成「历史」 | `option_chain(exp)` |
| 期限结构 | 全档最近 4 个有效到期各取 ATM IV(call/put 中值)+ DTE;`slope = IV_near − IV_far` | `option_chain(exp)` |
| 财报隐含波动 | 仅在确认下次财报时段且到期日**严格晚于**财报时,取 ATM call / put 有效 bid-ask midpoint 的跨式 / spot;任一腿 crossed / stale / 缺报价即 `UNMEASURED`,不得退回 last price | 期权链 + 财报日历 |
| 财报历史实际波动 | 按时段对齐最近 8 次:AMC D 用 D close→D+1 open / close;BMO D 用 D−1 close→D open / close;时段未知不纳入 | `earnings_dates` + 交易日线 |
| 波动率溢价 | `ATM IV − 20 日已实现波动(年化)` | 日线自算 |
| 偏度代理 | `IV(put, K≈0.9·spot) − IV(call, K≈1.1·spot)`,标「10% 价外偏度代理」(无 Greeks,不能算 25Δ) | 链 |
| PCR | 每到期 OI put/call + 合计;**只写数字与「对冲 / 持仓压力」语义,不写方向** | 链 |
| IV 分位 | 只比较相同 `method_version` 的 30D tenor;≥40 个完整交易时段有效观测才印,否则「样本不足(n)」 | `lake/us_options/<sym>@<as_of>` |
| 明确不做 | max-pain、OI 集中价位磁吸、异动期权(unusual activity)网查 | 普查裁定 + 噪声源 |

降级:无挂牌期权 → 现有一句(B 级);任何取数异常 → `record_degradation("us_options", ...)`,报告降级行;**不抛异常**。链取数成功但报价质量不合格属于 `UNMEASURED` 而不是 0,并保留质量原因。

消费纪律(写进 `engine-playbook.md` 催化剂 & 定位分析师):隐含波动 = **预期量级**,不是方向;与历史实际波动对照写一句「市场定价 ±x% vs 过去 8 次中位 ±y%」;偏度 / PCR 只描述仓位结构;**不得**由期权推出评级。

### 5.3 波动率与仓位地形(`macro/harvest.py` 新段;`global_tape.json` 机读)

| 项 | 内容 | 来源(探针 ✓) |
|---|---|---|
| 指数波动率 | `^VIX` 点值 + 1 年分位(自算,历史用 yfinance / CBOE csv 备源)、`^VIX3M`、**期限比 VIX/VIX3M**(>1 = 倒挂 / 压力)、`^SKEW`、`^MOVE` | yfinance;CBOE `VIX_History.csv` |
| 利率路径 | `ZQ=F` 的 `100−价` 只标作**合约月平均有效利率**;不得用单一前月合约直接声称「下次会议隐含变动」。若要会议概率,另做基于当前 EFFR、会议日前后天数与相邻月合约的加权求解并独立验算 | yfinance + FRED |
| 隔夜 tape | `^GSPC ^NDX ^SOX ^HSI DX-Y.NYB ^TNX CL=F GC=F HG=F USDCNH=X KWEB FXI ASHR SMH XLK` 各 1 日 / 5 日 % | yfinance 19/19 ✓ |
| 未来 7 日海外日历 | FRED `releases/dates`(CPI / Employment Situation / PCE / GDP / FOMC 相关 release)+ FOMC 年度日程表(静态 yaml,年维护) | FRED ✓ |
| 候选(未探针,UNVERIFIED) | CBOE 全市场 PCR 日表、CFTC COT 仓位 | 后续探针 |

消费:`us.md` / `global.md` / `equities.md` / `variant.md` 作者读;`macro_state.json` 新增 `global_tape_asof` 与 ≤8 个数(`vix, vix_term_ratio, skew, move, ust10y, dxy, usdcnh, zq_front_month_avg_rate`)——**只写**,策略师是否读属 B-1。会议隐含变动只有在加权求解实现且通过 fixture 后才可新增字段。

---

## 6. 留痕、冻结与限频(病 2 / 病 3 / 病 4 的修法,全部 I 类)

### 6.1 冻结事务与统一审计索引

现实现中 `post_run publish_run_observation` 发生在 `_finalize_forensic_run` **之前**,而 `external_tools.jsonl` 要到 finalize 内 `materialize_agent_index` 归档 transcript 时才产生。因此不能设计成「post_run 在 finalize 后回读并回填」;冻结后追加 `_web_calls.json` 或 `gate_fires.csv` 也会破坏 manifest / replay 完整性。

调整为一个幂等的 finalize 子步骤,顺序固定:

1. 归档 / 绑定 transcripts,物化 `capsule/lineage/external_tools.jsonl`。
2. 解析 batch 请求并生成 `capsule/lineage/web_budget.json`。
3. 汇合 `source_lineage`、`news_catalog / claim_ledger` 与 `external_tools` 的引用,生成 `capsule/lineage/external_evidence_index.json`。
4. 再跑 expected / completeness / replay,最后写 MANIFEST 与 freeze。

索引项统一带 `evidence_kind`、`row_id_or_hash`、`role/stage/subject`、`requested_at`、canonical URL、`decision_cutoff`、`verification_status`;只存引用,不复制 raw 内容。重复执行输入 hash 相同必须字节级同产物。`post_run observe` 保持原业务观察职责,不读取一个尚未生成的 lineage 文件。

上述路径是 scan adapter。macro / sector / stock full 当前不走 scan capsule,由同一 materializer 在报告发布前写 `$CTX/<skill>/<run>/trace/{external_tools.jsonl,web_budget.json,external_evidence_index.json}`;它记录 `context=macro_full|sector_full|stock_full`,**不新增** news stage。若当前 harness 无法提供 transcript,预算明确为 `UNMEASURED`,但确定层 source lineage 与稿件 claim 仍可索引。

证据不完整是**法证诊断**,不是 GATE4 后补业务门:写入 completeness / verification artifact,不得在 gate 已过后追加 `gate_fires.csv`;更不得据此淘汰股票。

### 6.2 网查预算的计量单位

`external_tools.jsonl` 一行是一次工具调用,不等于一次查询——一个调用可能包含多条 batch query。`web_budget.json` 按 `(role,stage,subject)` 同时输出:

```json
{
  "measurement": "MEASURED | UNMEASURED",
  "tool_calls": 0,
  "search_queries": 0,
  "fetched_urls": 0,
  "failed_units": 0,
  "duplicate_urls": 0,
  "wall_s": 0.0,
  "cap_unit": "search_queries",
  "cap": 0,
  "cap_enforcement": "OBSERVED_ONLY | HARD_CLAUDE | UNMEASURED",
  "self_report_delta": null
}
```

- cap 默认约束 `search_queries`;若角色另有 fetch cap 则单列,不得用 tool row 数冒充 query 数。
- transcript 未绑定、请求 payload 无法解析或 batch 计数不完整 → `UNMEASURED`,**不是 0**,也不得判「未超限」。
- 自报 `网查 N 条` 只保留为 `self_report_delta` 诊断;真值取解析后的 query / fetch unit。
- 查询去重按规范化 query hash;URL 去重按 canonical URL,但重复调用仍计预算。
- 姊妹稿的 `ReportModel` 运行事实只读 `web_budget.json`;`UNMEASURED` 原样展示,不得回退到稿件自报或显示 0。

### 6.3 三类证据分别入账

1. 数值 / 结构化源在 fetch 时进入 `lake`,并由 `source_lineage` 记录 endpoint、key、fetch / as-of、revision / hash、降级状态。
2. 新闻 / 事件进入 `news_catalog`;scan 内 `available_stage` 只能使用现有 `L0…L5`,并以 `first_seen_ts <= decision_cutoff` 做 PIT。独立技能不发明 `standalone` stage,在 run trace 记录 context / cutoff。
3. LLM 工具行保留在 `external_tools`;稿件 URL 经 canonicalize 后由 `claim_ledger` 对齐。可访问只证明 artifact 存在,`fields_match` 与 `content_supports` 仍要分别判。

### 6.4 时效契约 v2

推广情报站三窗,规则不变:已定价即衰减、未兑现催化不衰减。

| 海拔 | 窗 | 净分系数 |
|---|---|---|
| 微观 lite(现) | T0 / 24h / 背景 2–5 日 / >1 周 | 1 / 1 / 0.5 / 0;催化挂不衰减 |
| 微观 full · 中观 full | 24h / 本周 / 本月 / 背景 | 1 / 1 / 0.5 / 0;催化挂不衰减 |
| 宏观 full | 48h / 本周 / 本月 / 背景 | 同上 |

每条事件行仍是 `日期时间 / time_quality | 窗 | 事件 | canonical 源(http) | 净分`;`intel_recency_lint` 的 `_INTEL_WINDOW_SPAN` 按契约名参数化(`intel_v1` / `intel_v2_full` / `intel_v2_macro`)。`DATE_ONLY` 事件不得伪装成 T0 精确时点。

### 6.5 工具级强制的引擎边界(可选 spike,UNVERIFIED)

`.claude/settings.json` `hooks.PreToolUse` matcher `WebSearch|WebFetch` 只能证明 **Claude harness** 可做硬 cap:脚本按 transcript / agent 身份和 batch unit 计数,超 cap 返回拒绝 + 原因。未知:子 agent 调用是否触发主会话 hook、输入是否足以可靠识别 role / subject。1 小时 spike 定生死;成则 Claude 记 `HARD_CLAUDE`,否则 `OBSERVED_ONLY`。

Codex 没有同一 hook 契约,本稿不宣称双引擎硬限频等价;Codex 先记 `OBSERVED_ONLY`。若未来要跨引擎 `HARD_SHARED`,必须把预算放到共同 dispatcher / orchestration 层,另立设计,不能靠解析 transcript 冒充强制。

---

## 7. 外源情报契约 v2(判断层,沿用 `l4-intel.md` 骨架)

共同骨架不变:盲于上游论点;每个**适用面** ≥1 条定向查询、查不到写「无」(presence-gated 面可写「不适用」而不浪费查询);URL 必落且遵守 §3.1 来源分级,聚合页必须追到 canonical 原文;本票行情数字不自报;净分按窗衰减;声明行 `网查 N 条 ｜ 面覆盖 ｜ as-of`;最终回传一行。差异只在「面」与「窗」。

### 7.1 `global-intel`(macro full;sonnet;cap 8;落 `$CTX/macro/<date>/_global_intel.md`)
① 央行(Fed / PBoC / ECB / BOJ 最新表态、纪要要点)② 数据发布(本周已出:实际 vs 预期;下周将出)③ 地缘 / 关税 / 制裁(中美优先)④ 美股龙头财报与指引(本周)⑤ 中国政策(国常会 / 政治局 / 部委)⑥ 资金与仓位报道(EPFR / AAII / COT 的媒体转述,标转引)。消费:`us.md` / `china.md` / `global.md` / `calendar.md` / `variant.md`。

### 7.2 `us-intel`(stock-research full · 美股票;sonnet;cap 12;落 `$CTX/analyze/<TICKER>_<date>/_us_intel.md`)
① 财报 / 指引 / 电话会要点(确定性块给了数字,这里要**管理层原话与市场解读**)② 分析师行动(确定性 `upgrades_downgrades` 已列 30 日;这里查动作背后的论点)③ 监管 / 诉讼 / 政策(出口管制、反垄断)④ 产品 / 供应链 / 大客户(订单、产能、竞品发布)⑤ 内部人与大股东(EDGAR Form 4 已确定性;这里查 13F 报道与大宗)⑥ 负面增量(空头报告、事故、召回)。消费:催化剂 & 定位分析师、风险分析师、多空辩论。

### 7.3 `company-intel`(stock-research full · A 股;cap 12;落 `$CTX/analyze/<TICKER>_<date>/_company_intel.md`)

① 官方公告 / 互动口径增量 ② 行业价格、订单、排产 ③ 政策 / 监管 ④ 机构观点与预期变化 ⑤ 负面 / 诉讼 / 事故 ⑥ 海外客户、供应商、同业的财报 / 指引。第⑥面只查询 §8 有效映射,无映射写「不适用」;RSS 只给候选,必须跟到原文。它属于 full 独立技能,不复用 L4 卡、不输出评级。

### 7.4 `sector-intel`(sector-research full;cap 6;落 `$CTX/sector/<date>/_sector_intel_<行业>.md`)
① 产业价格 / 排产 / 订单(中文源)② 海外同业最新财报 / 指引(映射表给名单)③ 政策 / 监管 ④ 龙头事件。lite brief 不派(仍 ≤2)。

### 7.5 A 股 `l4-intel` 第七面「海外链」(B-3,冻结后)
输入加映射名单(≤4 美股名);查「本票主要客户 / 同业在 T0+24h 的财报、指引、砍单、扩产」;cap +3;净分同衰减;不写他票行情数字(映射名的涨跌由确定性 slim 供给,机检 `price_claim_mismatch` 同标准)。

---

## 8. 映射表 `readthrough_map.yaml`(入库,人工维护)

```yaml
version: 2
reviewed_at: 2026-08-28
owner: research
industries:
  消费电子:
    - symbol: AAPL
      kind: company       # company | etf | index
      relation: customer  # customer | supplier | peer | theme
      direction: downstream
      rationale: 终端需求与供应链披露的读透对象
      evidence_url: https://...
      effective_from: 2026-08-28
      effective_to: null
  半导体:
    - symbol: SMH
      kind: etf
      relation: theme
      direction: peer
      rationale: 海外半导体板块地形
      evidence_url: https://...
      effective_from: 2026-08-28
      effective_to: null
codes:
  "300857":
    - symbol: NVDA
      kind: company
      relation: customer
      direction: downstream
      rationale: 以公开供应链证据为准
      evidence_url: https://...
      effective_from: 2026-08-28
      effective_to: null
```

规则:

- 映射表示「值得观察的关系」,**不表示因果方向、涨跌传导方向或评级方向**;渲染时禁止写「A 涨所以 B 应涨」。
- 入库 lint 不只验证 yfinance 可解析,还验证 ticker 类型 / 交易所 / 币种 / 当前可交易状态、关系枚举、证据 URL 与有效期。ETF / 指数不得伪装成公司或财报主体。
- 个股映射优先于行业;空名单 = 整块省略;单层 ≤4 项,超出由 owner 人工取舍。
- 季度复核并让过期项自动停止消费;变更走 PR,不走自动学习。初版覆盖 ≤10 个行业 + 持仓 codes(Q4)。

---

## 9. 数据源规格(已探针源见附录 A;全部 B 级)

| 源 | 端点 | lake 键 | 刷新 | 用途 |
|---|---|---|---|---|
| yfinance tape | 19 标的 + `ZQ=F` `SR3=F` 日线 | `global_tape/<us_date>`(key=date;行带 `session_complete`,`fetched_at`) | 夜间 prewarm(19:30 CST,上一美股日已收)+ 盘前帧 | §5.3 / market_pack / L5 |
| yfinance option_chain | 日更 30D 窄快照;full 才取最近 4 到期 + 财报后首到期 | `us_options/<sym>@<as_of>`(as_of 快照,`snapshot: True`,带 method / quality) | 持仓 / 研究 / 映射名单夜间窄快照 + 按需 full | §5.2 |
| yfinance 票面 | `.news` / `earnings_dates` / `upgrades_downgrades` / `institutional_holders` / `insider_transactions` / `info(shortRatio, targetMean…)` | `us_ticker/<sym>@<as_of>` | 按需 | 微观 full |
| Google News RSS | `news.google.com/rss/search?q=…&hl=zh-CN&gl=CN&ceid=CN:zh-Hans` 与 en 版;100 条 / 查询 | `news_catalog`(source=`gnews_rss`)+ URL / 元数据 / hash | 按需;映射名单夜间 | **发现候选**,跟到原文后才能验证 claim |
| Bing News RSS | `bing.com/news/search?q=…&format=rss` | 同上(source=`bing_rss`) | 发现备源 | 同上;不是 T1/T2 证据 |
| FRED releases | `fred/releases/dates`(既有 `FRED_API_KEY`;未来 14 日;支持历史 → D-0 普查) | `fred_calendar/<date>` | 夜间 | 海外日历;若只有日期则 `DATE_ONLY` |
| FOMC 日程 | 官方年度页物化 yaml,带 source URL / fetched_at / content hash / revision | `fomc_calendar/<year>@<as_of>` | 周检 + 跨年预热 | 海外日历 |
| 官方事件时刻(待探针) | Fed / BLS / BEA 官方日历与发行人 IR;只为 FRED / yfinance 候选补准确时刻 | `official_event_calendar/<source>@<as_of>` | 夜间 + 入场前刷新 | 能确认时刻才升为 `TIMED`;否则保持 `DATE_ONLY` |
| SEC EDGAR | `data.sec.gov/submissions/CIK##########.json`(需 UA 头;CIK 由 `ticker→CIK` 官方表) | `edgar/<cik>@<as_of>` | 按需 | 微观 full 美股 |
| CBOE VIX csv | `VIX_History.csv` | `cboe_vix`(date) | 夜间 | VIX 分位备源 |
| tushare `opt_daily` | 已有 `lake/derivatives/` | 已有 | — | **不新增消费**(§5.1) |

湖纪律:写湖一律剥 `fields`(窄表毒化前科);`snapshot: True` 端点 as_of 必须等于真实今天(`SnapshotDateError`);所有新键进 `data/endpoints.py` + `data/contracts.py` B 级 + `record_degradation` 记账;时间戳带时区(`fetched_at` UTC),美股日与 A 股日分列,不混。B 级必须区分「源成功但真空」与「请求 / 解析失败」,二者不可都落空数组。每个 endpoint 还要声明 `freshness_slo`、`max_stale`、`stale_on_error`;消费端打印 `age / stale_reason`,超过 `max_stale` 整块省略而不是静默沿用旧值。

单次探针只证明 2026-08-28 可达,不证明可生产。D-1 前置 7 天 canary/soak:逐源记录成功率、p95 latency、空结果率、schema hash 漂移、限流 / UA 行为与 `empty_vs_failure`;生产建议门槛为成功率 ≥95%、无未解释 schema 漂移、失败路径能稳定 B 级降级。未达标的 RSS 只能留发现备源,不得称「确定性主源」。EDGAR 遵守官方 UA / 速率要求;第三方内容另做条款 / robots 人审记录。

---

## 10. 分类与排期

**D-0 · 海外事件窗普查(仪器,I 类,先于一切 B 类)**:用 FRED 历史 release dates + FOMC 表 + 七家美股龙头历史财报日(`earnings_dates` 历史)给 2022-03 → 2026-08 每个 A 股交易日打旗「持仓隔夜窗含海外事件」;同时保留 `time_quality`,不把 date-only 伪装成精确窗。

统计口径预注册,避免多重检验捞显著:

1. 主事件族先限定 `FOMC / CPI / NFP / mapped earnings`,其余只探索性展示。
2. 先把股票行聚合为**日期级等权组合**再比较 `gap_c1_o2` 的均值与 `|gap|` 离散度;股票行不能当独立样本。`oc_t1` 仅作对照。
3. 主检验用 5 个交易日 moving-block bootstrap(固定 seed,10,000 次),NW lag 5 仅作敏感性;每类写 n_dates、标准化 effect size、95% CI、原始 p 与四个主事件族的 Holm 校正 p。探索项另报 FDR,不得混入主门;预设最小事件日数 20,并做 2022–23 / 2024–26 子期稳定性。
4. 全市场可买截面与 finalist / 📌 分开报告;后者有选择偏差,不得外推全市场。
5. D-0 只决定某事件族是否值得进入**影子判断实验**,不直接启用 B-2 / B-3。L5 日历 / 持仓哨兵是风险可见性,不要求存在 alpha;判断层则需后续证明增量决策价值。

读数落 `docs/research/`,不进生产。

**I 类(现在可做,零 prompt diff)**:

| 编号 | 内容 | 落点 |
|---|---|---|
| D-1 | 源模块先以无消费者 canary 模式运行 7 天;过 soak 后再接 endpoints / contracts / consumers;同时入 `readthrough_map.yaml` + lint | `data/sources/{yf_tape,yf_options,gnews_rss,edgar,fred_calendar,official_event_calendar}.py` |
| D-2 | `scan/overseas.py` → `overseas_calendar.csv`(两窄窗 + date-risk)作为 prelude 新步(改 `STEP_NAMES` + `test_step_names_inventory`);summary 📅 行 + brief ⑤ 一句(brief `INPUT_WHITELIST` 加文件);`tripwire_watch` 事件旗;`frame.py` `market_pack.global_tape`(**不进** strategist allowlist) | scan L5 / 哨兵 |
| D-3 | stock-research full:options v2 + EDGAR + 分析师行动 + 财报历史波动 + gnews en(美股);海外映射块 + gnews zh(A 股);`us-intel` / `company-intel` agent def + 契约;`engine-playbook.md` 消费纪律与数据坑 #17–20 | analyze |
| D-4 | macro full:§5.3 三段 + `global_tape.json` + `global-intel` agent def + `macro-playbook.md`;`macro_state` 新字段只写 | macro |
| D-5 | 留痕统一(§6):scan finalize 与 full-report publish adapter 生成 `web_budget.json` + `external_evidence_index.json`;三类记录汇合;claim 状态消费纪律;时效契约参数化 | trace / news / self_review |
| D-6 | sector full:pack `readthrough` 键 + `sector-intel` 契约 | sector |

**B 类(冻结解除后;每项一个 `scan_config.jsonc` 开关,默认关,「不启用连副作用一起不启用」)**:

| 编号 | 内容 | 开关 |
|---|---|---|
| B-1 | `strategist_pack` allowlist 加 `global_tape`(策略师 §2 可引一行隔夜 tape,描述性) | `external.strategist_global_tape` |
| B-2 | L4 slim「海外映射事实」2 行(映射名上一日 % / 下次财报日 + 隐含波动) | `external.l4_readthrough_facts` |
| B-3 | `l4-intel` 第七面(cap +3) | `external.l4_intel_overseas` |
| B-4 | sector brief 地形段 +1 行 | `external.sector_readthrough` |
| B-5 | 工具级限频(视 6.5 spike;仅 Claude) | `external.tool_cap_enforced` |

B 类上线前置:D-0 读数 + 至少一轮 shadow / offline 增量决策评估(Q7)+ 20 结果日账本 + `test_agent_defs.py` 锚同步 + 派发 prompt hash 进 `run_contract.prompt_hashes`。B-2 与 B-3 独立灰度,允许先开零额外搜索的事实块,不得用一个总开关混淆价值 / 成本。

推荐依赖顺序:`D-1 canary + D-5 通用 trace 骨架 + D-0 fixture` → source soak 通过 → `D-2 风险展示` → `D-3/D-4/D-6 独立技能` → 冻结解除后的 B 类 shadow。D-2 不等待 D-0 alpha 结论,但必须先满足 PIT / time-quality / source soak。

---

## 11. 验收(活体)与回滚

| 项 | 验收 |
|---|---|
| 引擎隔离 | 全程 `AUTORESEARCH_ENGINE=codex`;活体 / fixture 只能读写 `context_codex/`、`reports_codex/` 与共享 `lake/`,测试断言 `context_claude/` / `reports_claude/` 零访问 |
| D-1 | 7 天 source soak 达 §9 门槛;断网 / `AUTORESEARCH_OFFLINE=1` 时所有新源走 B 级降级,`degraded.json` 记账,prelude / harvest 不抛;成功空与失败可区分;fresh / stale-allowed / max-stale 三态可测;湖键契约一致;窄表毒化探针(带 `fields` 写湖 → 红) |
| D-2 | 用 **Codex 侧临时 fixture** 离线重跑 prelude + assemble(NVDA 2026-08-26 AMC):summary 📅 出现入场前事件,brief ⑤ 同句,`brief_lint` fail 0;📌 300857 哨兵旗亮。另测夏 / 冬 DST、美国休市、A 股休市、BMO / AMC、DATE_ONLY、改期 revision、`first_seen_ts > cutoff` 不可见 |
| D-3 | `harvest NVDA` 的 full v2 与有效 bid-ask midpoint 手算一致;crossed / stale 一腿 → `UNMEASURED`;BMO / AMC 实际波动锚正确;30D 分位拒绝混 method / 不完整 session;映射渲染不出现因果措辞;`us-intel` / `company-intel` URL 零缺失,`intel_recency_lint(v2_full)` 通过 |
| D-4 | `macro.harvest` 三段在 `data.md`;`global_tape.json` 与 `macro_state` 新字段同 as_of;策略师 pack **不含**新键(`strategist_pack.ALLOWED_KEYS` 测试锁) |
| D-5 | scan finalize 与 full-report publish 两个 adapter 的 `web_budget.json` 均覆盖;batch query / fetch / duplicate / failed unit 与 transcript fixture 一致;未绑定 / 不可解析为 `UNMEASURED`;搜索 snippet 不得变 VERIFIED;canonical 原文可验证;索引引用三类记录且无 raw 重复;二次 materialize 字节不变;MANIFEST / publish 后零写入 |
| D-6 | sector full 只读有效期内映射;company / ETF / index 类型渲染正确;过期、无证据 URL、不可交易 ticker 被 lint 拒绝 |
| D-0 | 读数文档按预注册事件族写日期级 n / effect / CI / 校正 p / 子期;不足 20 日只报样本不足;finalist / 📌 与全市场分表 |
| 回归 | 覆盖旧 artifact 缺新键的兼容降级、B 开关默认全关、`global_tape` 在 market_pack 但不得泄漏 strategist allowlist;跑相关测试后再跑 `uv run --no-sync python -m pytest -q` |

回滚:scan 侧按开关;独立技能 `harvest --no-external`;湖表可留(B 级,无消费者不伤);agent def 改动 revert 即回。日历 / 哨兵关闭只移除展示,不得改历史账或删除证据 blob。

成本(UNVERIFIED):确定性层 ≈ 零 token;`us-intel` / `company-intel` / `global-intel` 只在用户触发的独立技能里跑;B-3 估 +15% intel 网查(≈ +$0.08/票,按 08-26 $0.56/票外推),真值以 `usage_harvest` 为准。

---

## 12. 待裁

| # | 问题 | 推荐 |
|---|---|---|
| Q1 | `autoresearch/news/` 包:接线(本稿 §6)vs 退役(08-26 A8) | **有限接线**——只承载 news / event 与 claim,不承载期权 / tape / EDGAR 数值;统一性由只读审计索引提供 |
| Q2 | 期权范围:① 美股 options v2 进 stock-research full;② A 股 IC/IM 基差在 macro full 展示一行;③ OI 集中价位 / max-pain | ① 做;② **不做**(settle/close 依赖与两年衰减未解,展示即误导,读数留普查文档);③ 不做 |
| Q3 | B-1 策略师读 `global_tape` 的时点 | 冻结解除后;先只进 market_pack / L5 |
| Q4 | 映射表:初版行业数与维护归属 | ≤10 行业 + 持仓 codes;人工 yaml、季度复核、走 PR |
| Q5 | 6.5 工具级限频 spike 是否做 | 做(1 小时),但只裁 Claude `HARD_CLAUDE`;Codex 保持 `OBSERVED_ONLY`,共享硬 cap 另立 dispatcher 设计 |
| Q6 | Google / Bing News RSS 的身份 | 只作**发现源**;7 天 soak 后决定主 / 备顺序,跟不到 canonical T1/T2 原文的条目保持 UNVERIFIED,不靠回退搜索摘要升格 |
| Q7 | D-0 后怎样裁 B-2 / B-3 | 事件族须 n≥20、Holm p<0.05、标准化 \|effect\|≥0.2、两子期同号才进入 shadow;再做 offline 增量决策评估,D-0 不直接上线。即使无 alpha,L5 日历 + 持仓哨兵仍可保留为风险可见性 |
| Q8 | 是否为 scan 建 `l4-intel` 之外的美股情报 agent(而非第七面) | 不建;面比 agent 便宜,且盲搜设计已成熟 |

---

## 附录 A · 源单次可达性探针(2026-08-28 22:57 CST;脚本 `scratchpad/probe_feeds.py`,抛弃型,读网不写盘)

| 源 | 结果 | 读数 |
|---|---|---|
| yfinance `option_chain("NVDA")` | OK 1.4s | 21 个到期;首到期 2026-08-28;spot 226.74;ATM IV 16.5%(08-26 AMC 财报后,IV 已压缩);PCR OI 0.73;calls 81 / puts 70 |
| yfinance 19 标的日线(`^VIX ^VIX3M ^SKEW ^MOVE ^GSPC ^NDX ^SOX ^HSI 000001.SS DX-Y.NYB ^TNX CL=F GC=F USDCNH=X KWEB FXI ASHR SMH XLK`) | OK 0.6s | 19/19,末日 2026-08-28 |
| yfinance NVDA 票面 | OK 2.3s | news 10;earnings_dates 25;upgrades_downgrades 988;inst_holders 10;insider_tx 150;shortRatio 2.35;targetMean 305.8;58 位分析师 |
| Google News RSS zh「歌尔股份」 | OK 0.8s | 100 条;最新 08-23「上半年营收突破 400 亿元…」 |
| Google News RSS en「NVIDIA earnings」 | OK 0.8s | 100 条;最新 08-27 |
| Bing News RSS「中金公司」 | OK 0.3s | 11 条 |
| FRED `releases/dates`(未来) | OK 1.1s | 50 条待发布 |
| SEC EDGAR `submissions/CIK0001045810.json` | OK 0.2s | 10-Q 08-26、8-K 08-26、Form 4 08-24、13F-HR 08-14 |
| CBOE `VIX_History.csv` | OK 0.4s | 9,261 行;末行 08/27 收 14.51 |
| yfinance `ZQ=F` / `SR3=F` | OK 0.1s | ZQ 96.22 → 隐含 3.78% |
| tushare `opt_daily(20260827, SSE)` | OK 1.9s | 656 行(仅证可达;§5.1 不新增消费)|

未探针(候选):CBOE 全市场 PCR 日表、CFTC COT。Google / Bing RSS 的限流、空结果与 schema 稳定性不由本表证明,按 §9 的 7 天 soak 补。

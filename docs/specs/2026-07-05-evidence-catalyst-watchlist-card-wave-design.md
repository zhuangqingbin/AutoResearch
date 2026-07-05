# 证据流 + 催化 + 观察单 + 卡片丰富化 wave — 设计(2026-07-05)

> 背景:3 周实跑(13 scan 日)暴露四个结构性问题;本 wave 四个 workstream 全部 **advisory
> 先行、默认关/presence-gated = parity 不破**,不动 OW 三门与评级链路。
> 证据基线:07-04 报告(数据日 07-03)+ journal/buy_ledger/zero_buy_ledger + 端点实测。

## 0. 证据(为什么是这四件)

| 信号 | 读数 | 含义 |
|---|---|---|
| 0 买常态 | 13 日 / 11 个 0 买;可结算买单仅 1 笔(东方财富 T+5 −2.7%) | 所有校准环(评级基率/触价/gate/watchlist ledger)**样本饥饿** |
| 催化盲 | 07-03:15/15 条 L3 论点、30/30 张卡"无明确催化" | 不是判断弱,是**看不见催化事件** |
| 公告线断链(本设计新发现) | `anns_d` **无接口权限**(实测);湖 anns_d 分区 0 个;07-03 `L3_news/` 200 文件 **0 条公告** | L3 公告情感列上线起为空;**监管旗(reg_flag)在现权限下永远不亮**;best-effort 静默降级掩盖了这一切 |
| 观察单 0 触发 | 13 日累计 0 次;6 条 conds 全为 money_pos∧ma_bull∧close_above∧manual **all-of** | 高价值产物烂在"待触发";无"错过审计"证据流 |
| 卡片太薄(用户反馈 07-05) | 早停卡 ~0.6k 输出,每维一句话;23/30 早停 | 输入侧 25k 已付,输出侧只写 0.6k = 已读证据大量浪费 |
| 催化端点权限(实测 07-05) | `stk_holdertrade`(增减持)/`repurchase`(回购)/`stk_surv`(调研)**全部 OK**(36/61/74 行) | 修复+扩展的原料现成 |

## WS-A 影子组合成绩单(纯确定性,零 token)

**目标**:给系统一条用户可感的成绩单曲线,统一 precision(误买)与 recall(错过);
同时让评级基率 n≥10 的解锁从"几个月"缩到"两周"。

### A1 `autoresearch/learning/paper_nav.py` — 三条净值线
- **规则(零判断可复现)**:每笔买单在**信号日次日开盘**建仓,**固定占 10% NAV 槽位**
  (并发上限 10 槽,超出忽略并记一行;固定槽位防单买单日全仓失真,与"评级×置信度分仓"精神一致),
  **持有 10 个交易日**后次日开盘平仓(窗口与 hi_10/fwd_10 同源);无持仓日 = 现金(0%)。
  持有期内权重随净值漂移不再平衡;停牌用最后可得 close 估值。
- **三条线**:①**真实线** = ≥OW 买单(buy_ledger 同源);②**影子线** = A2 shadow_buys 同规则
  —— `真实线 − 影子线` = **门的价值**的日频读数;③**市场线** = 全市场等权
  (与 zero_buy_ledger/attribution 同口径;hs300 指数线见开放问题 2)。
- **数据**:逐日 OHLC 从湖 `daily` 分区取(factor_lab cache 兜底);起点 2026-06-18 NAV=1;
  06-19 假日孤儿键跳过(与 buy_ledger 同);涨跌停可成交性不模拟(诚实局限行标注)。
- **落点**:`reports/learning/paper_nav.md`(三线日频表 + 一行结论),**prelude 刷新**;
  summary「组合视角」节嵌一行(presence-gated:文件缺 → 不加行)。

### A2 影子买单 `shadow_buys.csv`(每日,不只 0 买日)
- assemble 后确定性记账:final ratings 中 **Hold 按 L3 conviction 降序 top-3**
  (`pick_opportunity_candidates` 泛化 k 参数,同一事实源),
  落 `context/learning/shadow_buys.csv`(date,code,name,conviction,binding_gate,close)。
- 语义:"如果门不拦,系统最想买的 3 只"。与机会成本红队**正交**(红队仍 0 买日 2 只 LLM 深核,
  产出进观察单;影子买单是纯记账广度,产出进 NAV 影子线与评级基率样本池)。

### A3 早停抽检复核(LLM 件,**opt-in,默认关**;07-06 OTEL 数据后再定开关)
- 痛点:买单有 skeptic,23 张早停弃单无人看 = 单边质检。
- 机制:0 买日随机(seed=date)抽 2 张早停卡,派 1 个独立复核 agent:
  **只读** 早停卡 + 漏斗简报 + slim **深核分界后的块**(早停 agent 没读的部分,≈10k 输入/张
  ——增量信息恰恰在那里,表面块不重读),回答"深核块里有无翻案证据"
  → verdict 落 `_es_audit_<code>.md`;"误杀嫌疑"由编排写 proposals。**不改评级**。

## WS-B 催化数据面:修复 + 扩展

### B0 修公告线(先修再扩)
- `l3_news` 情感/`reg_hits` 监管词表增加**回退源**:anns_d 空/无权限时扫湖里已有的
  `stock_news_em` 标题(slim 个股新闻同源;anns_d 恢复权限则仍优先)。
- `run_health` 加 **`anns_empty_rate`**(L3_news 全空文件占比):=1.0 → warn,
  数据病显性化,不再静默降级。
- 词表/digest 契约不动(`_EVENT_TAGS`/`news_digest` key 集合被测试冻结——沿用 07-05
  监管旗"独立检测器"姿势)。

### B1 催化三端点入湖
- `endpoints.py` 注册:`stk_holdertrade`(key=ann_date)/`repurchase`(key=ann_date)/
  `stk_surv`(key=surv_date,实施时核)。全市场按日拉、湖复用;
  prelude/`harvest_l3_evidence` 顺手补近 10 交易日(湖命中后每日增量 1–3 calls/端点,限频宽松)。

### B2 确定性催化列(advisory,默认关 = parity)
- `l3_table_md(cat_flag=True)`:新列 `cat` 徽标式渲染近 10 日事件计数——
  `回购2(实施)·增持1·调研5·减持1`(回购区分 预案/实施;增减持按 in_de;调研计事件数)。
- 图例 + 禁则:**催化列 = 事件存在性,非方向确认**,须与资金/基本面共振才可作论点支柱;
  `减持≥2` 的票论点必须显式回应。
- L4 简报(`compose_funnel_brief`)注入同一行;slim 块清单不动(lite P3 已扫新闻块)。

### B3 取证环(先测量后动刀)
- `catalyst_ledger`:催化旗票 vs 无旗票 fwd_5 对照,落 `reports/learning/catalyst_ledger.md`
  (prelude 刷新);**n≥30 且 ≥10 日才读数**;IC 过硬(factor_lab 两半稳+符号一致)前
  **不入 composite、不设门**(与 consensus 同姿势)。

## WS-C 观察单最后一公里

### C1 分级触发(解 all-of 卡死)
- `check()` 状态机升级:机判条件满足计数 k/n → 新状态 **`提醒(k/n)`**(k≥1 且含至少一条
  价格/资金类达成;全满足才"触发")。排序:触发 < 触发(待人工项) < **提醒** < 临近 < 待触发 < 失效。
- 噪声控制:L5 只对 **Δ 新达成**(今日新增达成条目)置顶播报,持续满足常规行显示。

### C2 披露日锚(conds 词表 v2)
- 新 kind:`{"kind":"by_date","date":"YYYY-MM-DD","text":"中报扭亏"}`。日检:
  today ≥ date−3 → ⏰临期;today > date → 转"待人工确认"(机器知道何时催人)。
- 存量 6 条的中报 manual 全有确切日(08-12/20/20/27/29/31),编排一次性迁移。

### C3 错过审计
- `watchlist.csv` 加 `born_price` 列(ingest 时从 L1_scored_full close 取;存量从湖回填;
  `load_watchlist` 兼容缺列)。
- `run_check` 输出加 `since_born`(现价/born_price−1);**since_born ≥ +15% 且未触发 → 🔥 标记**
  = "触发条件太保守"的机器证据,进 proposals 候选。
- `watchlist_ledger` 从"待首个触发样本"扩为**每日 born-to-date 刷新**(不再饿着)。

### C4 触发复核升档
- 触发日提示语:"按 stock-research lite 复核;**拟下重注可对该票升 full 档**"
  (现有路由,零新机制;full 档自 06-21 起零使用 = 激活死路径)。SKILL/STAGES 文案行改动。

## WS-D 决策卡丰富化(用户反馈:太简单;原则 = **多写不多读**)

**成本论证**:token 大头在输入侧(slim ~25k/卡,已读已付);丰富化全落**输出侧**。
早停卡 0.6k → ~1.5–1.8k、满卡 ~2k → ~3k,30 卡日输出增量 ≈ +30k tokens
(对比真实日用量 ~1M ≈ **+3%**,满足"不大增"约束)。

### D1 卡模板 v2(lite-playbook.md + `.claude/agents/l4-card.md` 同步)
早停卡新增/升级(满卡同理,①②同加):
1. **「一段话研判」**(120–200 字叙事,置于仪表盘后):这是什么生意 / L3 为什么选它 /
   实读推翻或确认了什么 / 为什么停在这一档——给读者连贯的"研究故事"而非表格碎片;
2. **「L3 论点裁决」小表**:L3 thesis 拆 2–4 个前提,逐条 ✓/✗ + 一句实读证据
   (顺带喂 cross_calib"哪类前提最常被翻"的定性面);
3. **维度评分卡 bullet 化**:每维从"一句话挤一格"→ **2–3 条证据 bullet**
   (量价形态轮廓/主力·CMF·OBV 三线各自读数/筹码与户数趋势/fwd 估值 vs TTM——全部是已读数字);
4. **「已核数字摘录」表**(8–12 行,纯誊写):价/PE/fwd-PE/PB/np_yoy/rev_yoy/ROE/主力/CMF/OBV/
   winner/户数——防编数、便复查,推理零成本。

### D2 读盘边界一毫米不动(铁律)
P4 分界纪律 / WebSearch 只给 survivor / slim 块清单 / 早停点②③——全不变。
丰富化**只发生在写卡时**,禁止以"写丰富"为由多读深核块或加检索。

### D3 契约不变量
- 机器行一字不动:`**Rating**` / `FINAL TRANSACTION PROPOSAL` / `**Rubric建议**` /
  `进入P4倾向` / 变化项节(parse_rating/assemble/卡片 lint 零影响)。
- 新增段落是**推荐模板段,非硬契约**——lint 不加新规则(契约膨胀已有 4 warn/日教训)。
- 落点:`lite-playbook.md` 模板 + `.claude/agents/l4-card.md`(`test_agent_defs` 同源 lint 同步);
  agents 会话启动装载 → **下 session 生效**。

## 顺带修(一行级)
- journal 07-03 行 finalists/卡数未回填 → 核 assemble→journal upsert 链路,补。
- northbound 通道疑似空转(hk_ratio NaN 率 100%):**只加读数取证**(run_health 或 menu 一行:
  northbound 召回票 hk_ratio NaN 率),不动 quota 结构;坐实后另走 proposal。

## 不做(YAGNI)
- 不做实盘对接/仓位管理系统(NAV 是研究仪器);不动 OW 三门/评级链路(新信号全 advisory);
- 不上公告/研报全文 NLP(标题词表够取证);不做 L3 conviction 语义改革(B5 想法,等
  07-06 cross_calib 实跑数据回来再议);不建 prompt A/B harness(R9 边界)。

## 测试与 parity 姿势(项目惯例)
- 确定性件(paper_nav/shadow_buys/endpoints/cat 谓词/催化 ledger/watchlist v2/anns_empty_rate)
  全带单测;新参数默认关、新节 presence-gated——现有 686 测试语义不破。
- LLM 件(A3 复核员/D1 卡模板实跑效果)标"未实跑"进 STAGES 开放线头,下一真实 scan 日验收。

## 验收(下一真实 scan 日)
1. `paper_nav.md` 三线出数,summary 组合视角现一行;shadow_buys 每日 +3;
2. L3 表出现 `cat` 列且禁则被论点引用;`anns_empty_rate` 出数(修复前应 =1.0 → 回退源接上后 <1);
3. 观察单出现 `提醒(k/n)`/`since_born` 列;存量 6 条 by_date 迁移完成;
4. 决策卡呈 v2 模板(一段话研判/L3 裁决表/bullet 评分卡/数字摘录),单卡输出 ≤2k(早停)/≤3.5k(满卡);
5. 卡片 lint 0 新增 warn 类型;686+ 测试绿。

## 开放问题
1. anns_d 权限能否开通(积分档)或存在替代公告端点——B0 回退源先行,不阻塞;
2. hs300 指数对照线(`index_daily` 权限待核;v1 全市场等权已一致可用);
3. 提醒态噪声阈值(实跑一周按 Δ播报量再调);
4. A3 opt-in 开关时机(07-06 OTEL 成本数据后定);
5. `stk_surv` 日期键口径(surv_date vs ann_date,实施时以真实返回为准)。

---
_设计:Claude(Fable 5)× 用户拍板,2026-07-05;实施计划另出(writing-plans)。_

# scan 计量·校准四件套 —— OTEL 真计量 + 陷阱预旗 + 触价校准注回 + 跨层校准环

> 2026-07-05。双轴 brainstorm(token 效率 × 报告质量)定稿。一波四件,共同主题:**这套系统当前缺的
> 不是新机制,是真实计量与校准对账面**——token 侧 92% 盲区里做优化是盲狙,质量侧"错杀/目标价过
> 乐观"只有轶事没有统计。四件全部遵循既有分层铁律:**python 产确定性读数,编排层(skill)手贴
> prompt**;新旗/新节默认关或 presence-gated,parity 不破。升门等取证后另发 proposal。

## 1. 问题(为什么改)

1. **token 92% 盲区**:落稿估算下界 ~75k vs 真实量级 ~1M(07-03 实证,主因 L4 输入侧 28 卡 ×
   ~25k 未计;`STAGES.md` token 落稿契约节)。07-05 叶子 agent 化的核心收益假设("稳定 system
   prompt 前缀吃 cache")**从未实测**——30 卡同一条消息并发存在 cache 写入竞态,可能全 miss。
2. **实跑欠账**:07-03 海拔重构、07-05 三叶子 agent、哨兵档,全部"落地未实跑"。质量侧最大的
   即期敞口不是缺机制,是上线机制未验证。
3. **目标价系统性过乐观已有首证,但校准数据面断供**:东方财富 hi_10 触达 6.3% vs 卡内目标
   28.8%;`buy_ledger` 的触达率只统计 ≥OW 买单(`buy_ledger.py:51-100`),0 买连败下 n<10 永久
   thin 禁注——校准线永远喂不进 L4。
4. **错杀无对账面**:8 连败 0 买,当下真风险是错杀不是错买。L4 rubric 三门(主力真在/业绩真兑现/
   估值不透支,`l4_card.py:213`)是"压评级的 binding gate",却只活在卡文里——`gate_fires.csv`
   只记 self_review 硬门;L3 高确信被 L4 翻案也无 lane 级统计。机会成本红队的对抗证据流没有
   确定性对账。
5. **陷阱旗成功模板未复制**:主力失真旗(07-03)精确命中 18/30 被 L4 逐卡辟谣票,证明"机械可查
   项确定性前置"路径有效;质押(端点已通、enrich 有阈值)与监管事项(l3_news 词表缺独立词)
   仍靠每张 L4 卡自己重新发现。

## 2. 目标 / 非目标

**目标**:① OTEL 真计量:per agent.name × token 分型 + cache 命中率,与落稿估算表对账,周一
(07-06)实跑验收;② 陷阱预旗 v1(质押旗 + 监管旗,advisory 标注+禁则,照 dist_flag 模板,
默认关);③ 全卡目标触达统计 + prelude 打印当日件建议行(治目标价过乐观);④ 跨层校准环两报表
(L3→L4 翻案率 per lane、rubric 门柱级拦对/错杀率)+ 建议行;⑤ 全部 parity 不破,契约测试齐。

**非目标**:❌ 升门动作(预旗机械置 gate=False / finalists 剔除——取证 ≥2 周后另发 proposal 人
拍板);❌ 暖 cache / 1+29 错峰修法(等 cache 读数坐实竞态再议);❌ Opus→Sonnet 降级、分 wave
(既有判定);❌ 新 prompt A/B harness(R9 边界;取证一律骑 retro/attribution 既有轨);❌ CMF
滞后 / horizon 之争提前裁决(前向数据未熟);❌ 机械改卡内目标价(assemble 零-LLM,不动分析师
数字);❌ fina_audit / 应收增速 / 减持计划新端点接线(v2 候选,见 §5.1);❌ MTM 加权复制
(feedback_store 已有,勿重复)。

## 3. 分层铁律(贯穿四件)

"注入 prompt"机制在本仓库**全部**走编排层手工装配,python 侧只产确定性读数/报告——评级基率
(`buy_ledger.rating_base_rates` 只落 `buy_ledger.md`,技能读后手贴 skeptic/PM prompt,
`STAGES.md` 明文)、校准块(`feedback_store.render_calibration_block`,`autoresearch/scan/` 内
零调用点)、共享指令(`_l4_shared_instructions.md` 编排手写,`l4_card.write_dispatch_pack`
只读取前置)皆同构。**本波不建 python 侧 prompt 渲染器**;所有"注入"交付形态 = prelude/CLI 打印
**建议行** + SKILL/STAGES 补操作行。

## 4. 件一:OTEL 计量三件套(仪器 + 解析器 + 实跑对账)

### 4.1 仪器(文档确认,零派发路径改动)

跑扫描的 Claude Code 会话从带 env 的 shell 启动:

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=console      # 冒烟后可改 prometheus/otlp,见 4.4
export OTEL_LOGS_EXPORTER=console
export OTEL_METRIC_EXPORT_INTERVAL=30000  # 30s,40min 会话 ~80 批
export OTEL_LOG_TOOL_DETAILS=1            # 显示真实 agent/skill 名
```

metric = `claude_code.token.usage`,维度:`type∈{input,output,cacheRead,cacheCreation}` ×
`agent.name`(l4-card / buy-skeptic / sector-brief / …)× `query_source∈{main,subagent,auxiliary}`
× `model`。**subagent 用量含在同进程遥测且可按 agent 类型区分**(文档确认;不能按单卡区分,够用
——我们要的是"L4 卡这一类"的真实成本与 cache 命中)。

### 4.2 解析器 `autoresearch/trace/telemetry.py`

CLI:`python -m autoresearch.trace.telemetry <raw-export-file> [--out <md>]`。输入=console/scrape
原始导出;输出=markdown 表:按 `agent.name × type` 聚合总量、**cache 命中率 =
cacheRead/(input+cacheRead)**、与当次落稿估算 token 表的对账列(估算/真实/缺口)。编排层把 `--out`
指到当次 `reports/scan/<run>/token_telemetry.md`(与既有 token 估算表同处)。解析容错:未知行
跳过不抛(exporter 输出混杂 TUI 噪声时仍可解析)。

### 4.3 实跑验收(2026-07-06,下一交易日)

带 env 启动 session,**正常走 scan-market skill 全流程**(生产路径零改动)——顺带验收 07-03
海拔重构、07-05 三叶子 agent 的实跑欠账(哨兵档除外:等自然枯竭日,不造假数据)。跑完出
`token_telemetry.md`,把 75k vs ~1M 缺口按 agent 类型分解落账,补进 `STAGES.md` token 经济节
("真实计量"小节)。

### 4.4 cache 判读(读数先行,修法另议)

l4-card 的 cacheRead≈0 → 并发 cache 写入竞态坐实 → 修法(首卡先行暖 cache 的 1+29 派发、或
prompt 前缀重排)**另发 proposal,本波不预做**;cacheRead 显著 >0 → 07-05 假设成立,落账真实
命中率。已知风险:console exporter 写 stdout,交互 TUI 可能互相污染——实现期冒烟三选一
(console 重定向 / `OTEL_METRICS_EXPORTER=prometheus` + 本地 curl 轮询落文件 / otelcol file
exporter),**验收标准不变**:拿到 agent.name × type 分解表。

## 5. 件二:陷阱预旗 v1(质押旗 + 监管旗,advisory 档)

### 5.1 范围裁定(按数据可得性,2026-07-05 探查结论)

| 候选旗 | 数据现状 | v1 裁定 |
|---|---|---|
| **质押** | `pledge_stat` 端点已登记(`endpoints.py:51`);`tushare_enrich.py:137-142` 已有阈值(>40% 爆雷红旗 / >20% 偏高),但只进个股深核 slim,scan 漏斗未接 | ✅ 做,finalists 级 |
| **监管(问询/立案等)** | `l3_news.py:18-27` 词表已含 问询/关注函/立案/处罚/诉讼/违规,已入 L3 表 `news_tags`;缺"监管/证监会/交易所"独立词;无"旗+禁则"地位 | ✅ 做,升格为旗 |
| CFO 连负 | `cfo_ps` 已是 scan 列且为"业绩真兑现"门数据源(`scoring.py:125-136`);紫光国微三度被该门封顶 | ❌ 已被门覆盖,不重复 |
| 审计意见 | `fina_audit` 完全未接线;年频,覆盖窗口有限 | ❌ v2 候选 |
| 应收增速 vs 营收 | 全库无应收字段,需新接 balancesheet | ❌ v2 候选 |
| 减持计划 | 仅 l3_news 关键词,无结构化源 | ❌ v2 候选 |

### 5.2 质押旗(挂 L4 简报,finalists 级)

派发前批量拉 finalists(~30 只)`pledge_stat`,入湖 as_of 缓存 TTL 7 日(周频数据;30 calls/日
远离限频,`report_rc` 1次/小时是特例、常用端点宽松)。阈值与文案复用 `tushare_enrich` 现有常量
(抽为共享常量,单一事实源)。注入照 `_dist_mark` 模板(`l4_card.py:113-124`):
`compose_funnel_brief` 内联 `·⚠高质押(x%:P4 必核平仓线与补充质押公告)`;缺值 = 空串。

### 5.3 监管旗(挂 L3 表,`reg_flag` 参数)

`l3_news` 词表扩"监管/证监会/交易所"三词(利空组);`l3_table_md(reg_flag=True)` 把强信号 tag
(立案/问询/关注函/监管/处罚)升格为 `⚠监管` 旗列 + 图例禁则("旗票论点必须显式回应监管事项,
不得无视");**默认 `reg_flag=False` = 逐字 parity**。SKILL.md 步骤 3 推荐常开(与 dist_flag
并列)。

### 5.4 取证与升门路径(本波只铺取证,不升门)

advisory 跑 ≥2 周 → 取证 = 旗票 × `attribution.csv`(fwd_5/hi_10)+ L4 结局(≤UW 率/早停率),
用现有 join,不建新 harness。命中率过硬后另发 proposal 人拍板;升门最小侵入点已探明:构造
`gates` dict 时预旗强制置 False(`rubric_rating` 只认传入 dict,`l4_card.py:249`),或
`merge_l3_finalists_v2` 剔除钩子(`l3_select.py:269` triage_lean 旁)。

### 5.5 测试

照 `test_l3_dist_flag.py` 三例式:开旗含列+图例;默认关 parity(旗字符串不得出现);简报带/不带
标注各一例。另:质押 mark 阈值边界与缺值单测、l3_news 新词分类单测。

## 6. 件三:触价校准注回(治目标价过乐观)

### 6.1 读数:全卡目标触达统计(解 thin 困局)

`buy_ledger.py` 新函数 `target_calibration(window=30, min_n=10)`:把 `roll()` 的 rebase+hit
逻辑(目标幅 rebase 到 D+1 开盘基 `t_entry`,`hit = hi_10_oc ≥ t_entry`,`buy_ledger.py:88-95`)
**扩到全部有目标价的卡**(全评级,非只 ≥OW;卡内 target 已由 `assemble._parse_dashboard` 解析,
`attribution.csv` 的 `hi_10_oc` 覆盖全市场)→ `{n, hit_rate, med_target, med_mfe, thin}`。只统计
已成熟行(`hi_10_oc` 非 NaN,口径同 roll)。**实现期修正(07-05 真数据冒烟发现)**:只统计
**看多目标(tr>0)**——UW 向下目标负幅任何上涨都"触达",混入会把 hit_rate 稀释到失真
(全评级混合 72% vs 看多口径 39%);过乐观校准的对象本来就是向上目标。每天 ~25-30 张卡 ×
成熟日,样本迅速过 min_n(实测 30 日窗 n=36)。

### 6.2 渲染与注入

`buy_ledger.md` 新节"📐 全卡目标校准(近 30 scan 日)";**prelude 汇总屏打印一行建议当日件**:
`📐 目标价校准:近30日全卡10日触达率 X%(中位目标 +Y% vs 中位MFE +Z%)——目标幅>Z% 需给出
超额理由`(thin 时打 ⚠样本少并禁注,照基率模板)。编排层贴进 `_l4_shared_instructions.md`
当日件(`write_dispatch_pack` 自动前置每卡)。SKILL.md 步骤 4 派发三步①补半句。

### 6.3 边界与测试

不改卡内目标价、不改 rubric、不加卡片新必填行(建议行是先验非契约)。测试:rebase 口径、全卡 vs
买单口径区分、thin 门、md 节渲染契约、prelude 打印行。

## 7. 件四:跨层校准环(两张 join 报表)

新模块 `autoresearch/learning/cross_calib.py`(单一职责:层间一致性读数),CLI + 
`reports/learning/cross_calib.md`,prelude 挂刷新(失败不阻断,照 prelude 风格)。run 定位复用
retro 的 manifest(`analysis_date`)方式,跨日 glob `context/scan/*/`。

### 7.1 报表 a:L3→L4 翻案率(per lane)

跨日 join `L3_judged_full.csv`(lane/conviction/triage_lean)× `health.final_ratings(scan_dir)`
(现成同口径解析,`health.py:102-121`)→ 每 lane:n、**高确信翻案率**(conviction≥70 但 L4
≤Underweight)、triage_lean 命中率。窗口 30 scan 日,per-lane min_n=10 thin 旗。建议行贴 L3
prompt 校准块旁(如"吸筹 lane 近30日高确信被翻案 x%——该 lane 论点请先过资金真实性")。

### 7.2 报表 b:rubric 门柱级拦对/错杀

把 `assemble` 的门柱解析(`_GATESEG_RE`,`assemble.py:217`)**抽为共享函数**(防口径漂移),
跨日解析 `details/*.md` 三门状态(早停卡无门柱段,自然剔除)→ **binding gate** = 唯一 False 门
(≥2 False 计"多门")× `attribution`(fwd_5_oc, hi_10_oc)→ 每门:`{n_blocked, mean_ex5,
拦对率(ex5<0), 错杀率(ex5>0 且 hi_10 ≥ 卡内目标;缺目标价的票剔除该列)}`。口径对齐
`gate_ledger.roll`(`gate_ledger.py:43-46`,ex = 被拦票 fwd − 全市场均值);拦对/错杀不互补
(中间地带 = 拦了但未触达目标)。建议行贴 skeptic/PM 先验旁。

### 7.3 边界与测试

不改门/权重/评级——只给判断层"你自己的历史倾向"数字。测试:gateseg 共享函数单测(assemble 与
cross_calib 双消费同源)、翻案率计算(L3_judged + details fixture)、binding 判定(0/1/多 False)、
thin、md 渲染契约。

## 8. 实施顺序与验收

**顺序**:件三(最小,~1 函数+渲染)→ 件四 → 件二 → 件一解析器 → **07-06 实跑**(件一的验收
同时兜四件的"上线未实跑"欠账:三条 prelude 建议行、新旗、遥测表一次全验)。

**验收清单**:
1. 全测试绿(存量 665 + 新增);
2. parity:默认关跑 07-03 现场,对拍产物逐字同;
3. 07-06 实跑产出 `token_telemetry.md`(agent.name × type + cache 命中率 + 对账节);
4. `buy_ledger.md` 全卡目标校准节、`cross_calib.md` 两报表有数字(thin 旗如实);
5. prelude 汇总屏出现三条建议行(触价/L3 翻案/门柱);
6. SKILL.md / STAGES.md 补行完成(编辑前重读,防外部改动)。

## 9. 开放问题

- exporter 选型:console 与交互 TUI 的互扰待冒烟;prometheus 轮询 / otelcol 为备选(§4.4)。
- 会话结束时 OTEL 是否强制最后一次导出未文档化——30s 间隔 + 收尾等待 ≥1 个导出周期规避。
- `pledge_stat` 对 finalists 逐票调用的实际限频待首拉验证(预期宽松)。
- 错杀率的"触达率"列依赖卡内目标价解析完好率(dashboard 契约 lint 已保);缺失占比高则回退
  固定阈值(hi_10 ≥15%)并在报表标注口径。
- 哨兵档实跑:等自然枯竭日,不在本波验收内。

# Wave6:统一 roadmap —— 跑动裁决 × token/速度第二刀 × 研究能力 × 工程债(design)

> 2026-07-27 · brainstorm 定稿(只落文档,不实施)。起因:用户两问——①整个项目架构/功能/研究能力是否有优化空间;②最近一次 report 的 token/速度优化空间。
> 侦察三路:架构与提示面盘点 / run `20260725_1316` 取证(**含追溯真计量**,方法见附录 A)/ 账本与欠账簿清点。所有数字实测,估算处显式标注。
> 上游:`docs/specs/2026-07-25-scan-wave5-live-mainruler-macro-metering-design.md`(Wave5,批1/批2 已实施)。

## 用户已拍板(2026-07-27)

1. **统一 roadmap**:Wave5 余项(批3、④C 第二刀、裁决点日历)全部吸收进本文重排优先级;**此后调度以本文为准,Wave5 spec 退居机制参考**。
2. **只落文档**:本波不写代码;文中「批 B/批 C」是下次开工的实施蓝本。

## 0. 定位与红线

### 0.1 一句话诊断

Wave5 批2 的 spike 已经预警(4 个侦察 agent 加权 1.78M > 全场自估 1M),本次对最近一次真实扫描做完整追溯计量后坐实:**一次扫描的真实成本是报告自估的 30 倍(加权口径),且分布与旧假设相反**——原第二刀头号目标 L3 只占加权 8%,真正的大头是 L4(卡+情报 43%)、主会话编排(27%)和纯过路费的 step-wrapper(5%)。同时,**跑动/裁决赤字仍是全项目最贵的病**:retro 欠 3 天(含 Wave5 标「立即」的上涨侧第一证据)、macro full 产出 0 份、档案池 30 只只建了 4 份、replay 回放器从未用于裁决。判断力基建大面积闲置,扫描主脊是唯一真跑的东西。

因此本波排序:**主线 R(跑动与裁决,零开发)> 主线 T(token/速度第二刀,小刀+触发条件)> 主线 Q(研究能力质量)> 主线 E(工程债清理)**。

### 0.2 红线(既有裁定与负结果,本文一律不碰)

| 红线 | 出处/读数 |
|---|---|
| **不放松买入门**(≥OW 唯一门槛、OW 三门) | paper NAV:真实 −0.24%(9笔) vs 无门影子 −3.57%(66笔) vs 市场 −13.78% → 门价值 ≈+3.3pp;07-09 裁定「别再怀疑买入门」 |
| **不动早停机制与「早停只向下」** | L4 拒绝侧 `ic_rating_t2 +0.318` 是全系统最好的判断信号;只允许 ②C 路线(记账→取证→强势票子桶修订) |
| **不建当日大涨/event 类召回** | 追当日大涨 −4.85pp(t=−13.6)已证伪;event 路取证中(pr_20260725_001,~08-08 裁决),裁决前默认关且**连副作用一起不启用** |
| **超短 T+2 主尺不动** | 07-10 用户裁定,全尺对齐 fwd_2_oc,swing/T+5 作废 |
| **L2 不用模型;菜单内确定性分数无选股 alpha** | L2 模型 zoo OOS 全负;「机器已证有效的是拒绝不是挑选」 |
| **52 周高线性因子不复活** | 2026-07-18 否决(IC −0.0023,risk_off 反噬 t=−2.29);家族内换代候选是 pct_60d(t=+2.62,走批3 裁决) |
| **反思注入克制** | ATLAS「反思可能有害」;lesson 注入 cap=8、MTM 降级在跑 |
| **不建 FTS5/向量库/第二套运行时/常驻回测 harness** | 项目铁律;replay.py 是钦定回放器 |
| **改生产行为的刀一律「读数触发 + 用户点头」** | Wave5 纪律沿用;「默认不启用=连副作用一起不启用」(Wave4 floor 教训) |

## 1. 现状账本(2026-07-27 侦察读数)

### 1.1 架构面

- **代码**:`autoresearch/` 28,294 行 / 138 个 .py / 12 子包;scan(9,267)与 learning(7,518)占 59%。>800 行大文件 7 个(assemble.py 1323 / l4_card.py 1264 / analyze/harvest.py 1233 / l3_select.py 1120 / factor_lab.py 1116 / retro.py 1072 / feedback_store.py 954)。全仓 TODO/FIXME 标记 **0**。
- **测试**:197 个测试文件 / 1,551 个 `def test_`(参数化后 1,602 passed,批2 记录)。
- **提示面**:25 件共 231,065 B。scan-market skill 一家 69,918 B(SKILL.md 21,818 + STAGES.md **40,776**(全仓最大单件,引用式消费不进每跑上下文)+ config);agents 6 件 33,056 B(l4-card 12,670 最大);workflows 4 件 35,009 B;stock-research 43,900 B(两本 playbook 37,893)。
- **结构杂项**:`trace/` 零 Python 引用方(纯 `python -m` CLI,合理但 OTEL 半边已判退役,见 E1);空目录 `autoresearch/scan/stages/`、`tests/models/`;`.claude/skills/.omc/` 混入 stray 状态文件。
- **一次扫描的 agent 面**:实测 50 个 = 11 intel + 15 card(11 主卡 + 2 只 SELL 复核股 ×2 张复核卡)+ 2 l3-rank + 7 sector-brief + 1 macro-brief + 13 general-purpose(步骤壳/门/ensemble 综合官)+ 主会话。

### 1.2 run 真经济学首读(20260725_1316,数据日 2026-07-24)⭐ 本次最大新闻

计量代码(`73749b1`)比该 run 晚 4h40m 落地,但 harness transcripts 存活——用 `usage_harvest.usage_of`(按 message.id 去重)对 49 个 workflow agent + 主会话窗口做**追溯真计量**(方法与覆盖率声明见附录 A):

| agent 型 | n | billed input | 加权* | output | 占加权 |
|---|---:|---:|---:|---:|---:|
| 主会话(编排,61 消息) | 1 | 7.29M | **1.48M** | 34.8k | **27.0%** |
| l4-card(opus·xhigh,11 主卡+4 复核跑) | 15 | 5.08M | **1.27M** | 301.8k | 23.1% |
| l4-intel(sonnet·max) | 11 | 4.03M | **1.10M** | 233.7k | 20.0% |
| general-purpose(壳/门) | 13 | 2.66M | 798k | 27.8k | 14.5% |
| l3-rank(opus·max) | 2 | 1.28M | 429k | 85.6k | **7.8%** |
| sector-brief(opus)×7 | 7 | 1.90M | 367k | 26.6k | 6.7% |
| macro-brief(opus) | 1 | 166k | 49k | 6.3k | 0.9% |
| **合计** | 50 | **22.40M** | **5.49M** | **716.6k** | 100% |

\* 加权 = raw + 1.25×cache写(5m) + 2×cache写(1h) + 0.1×cache读(仓内计价倍率);wf agents cache 命中率 85.6%,cache写全 5m-TTL,主会话全 1h-TTL(harness 行为,项目侧不可调,仅记录)。

**关键对照与个案**:

- 报告自己的「token 消耗(估算)」表写 **~183.6k**:对 output 低估 3.9×、对加权 **30×**、对 billed 122×。「先仪表化再精准砍」彻底坐实——按旧估算砍会砍错整个方向。
- **原④C 排序被推翻**:旧侦察按落盘字节推「L3 占 37% 是头号杠杆」;真数据 L3 加权仅 7.8%。但 L3 **wall 21m33s(26%)仍是速度主犯**——「省 token」和「省时间」从此分两把尺裁(§3.0)。
- 纯浪费三处:**7 个 2-消息 general-purpose step-wrapper ≈287k 加权**(每个 ~60k billed 换 ~0.5k 输出,纯过路费);**ensemble 边际 ≈1.50M billed/≈0.4M 加权**,其中 601869 三票全 UW(spread=0)零改判;**sector-brief 1.90M billed 换 19,041 B 文本**(7 篇,~265k billed/篇)。
- 单最大 agent:600236 满卡 1.19M billed(44.6k 输出);L3 holistic 判官 1.16M billed / 68.4k 输出 / 15 消息,**12:18→12:38 主线 20.5 分钟零落盘静默**。
- 每股 L4 全链 billed:300857 1.91M(含 ensemble)· 600236 1.51M · 601869 1.49M · … · 600012 327k(早停)。

**耗时**(`_stage_timing.json`,总 4,900s=81m40s,含预热全程 100m40s):

| 段 | wall | 备注 |
|---|---|---|
| 预热 | 959s | **launchd 已装载但周六补扫仍在 run 内同步付**(见 T7) |
| L0L1L2 | 648s | |
| 策略师 | 203s | 与 universe 并行 |
| 行业 brief | 841s | 7 篇,vs 07-21 的 243s(当日 TTL 复用低) |
| **L3 精排** | **1,293s** | 判官单 agent 20.5min 静默 |
| L4slim | 187s | |
| **L4 研究** | **1,485s** | 11 股并行,intel 开 |
| ensemble | 464s | 2 股 SELL 双复核 |
| assemble | 88s | |

(分段存在并行/重叠——策略师与 L0L1L2 并行、intel 与卡尾部交叠——各行相加不等于总计 4,900s。)

### 1.3 战绩与漏斗(该 run + 累计)

- 该 run:L0 3,975 → L1 1,000 → L2 203 → L3 入围 11(8 真选 + 3 保送)→ 11 卡 → **0 买**(7 Hold + 4 UW);早停 7/11、满卡 4;OW 三门 binding 12 行(7 只);pinned SELL 双复核对 300857/601869 正常触发(**07-21 漏传 `args.pinned` 的 bug 未复发**,Wave5 ②D 生效)。
- 累计:25 个 scan 日 **18 日 0 买**;买单 n=9(已实现 4),T+2 胜率 50%、均值 −0.32%;0 买日市场 fwd_2 −1.48% → 空仓方向仍正确;门价值 ≈+3.3pp(§0.2)。
- 质量旗(gate_fires 26 行):**11× `intel零URL` warn(11/11 情报稿零引用,不可审计)**;2× `price_claim_mismatch`(601869「涨停」实际 +6.31%、601918「涨停」实际 +5.98%);12× OW三门 binding;1× anns 去伪 info。process_scores **11/11 全是 4/6**,统一 fail 在 `chk_blind_pass` 与 `chk_slim_size`(后者确认是检查线过时,见 Q6)。

### 1.4 能力闲置审计(跑动赤字数字化)

| 能力 | 建成 | 生产使用 |
|---|---|---|
| macro-research full(20 节报告) | 06-20 | **0 份**(reports/macro 空;批2 已解耦 `macro.state` CLI,只差跑) |
| stock-research full 深报告 | 06-21 | **1 份**(20260621) |
| 常备档案(dossier)池 | 07-23 | **4/30 建档**(池 69 只:30 active/39 retired,cap=30;26 只无档案) |
| 漏斗回放器 replay(PIT 六防线) | 07-12 | **0 次用于裁决**(批3 ②B 的钦定工具) |
| retro 慢环 | 06 月 | **欠 07-16/17/21 三天**——07-21 恰是 Wave5 标「立即」的 6 只 pass1-cut 票裁决(上涨侧盲区第一个直接证据),悬空 6 天 |
| earlystop_ledger(批1 ②C) | 07-25 | 账本已建**为空**(新卡头 agent def 下 session 生效,待下次扫描首读) |
| 过程直播 8 检查点(批1 ①) | 07-25 | 07-24 run 无 `_prelude_summary.md` → **尚未活体验收** |
| CP7 token_usage(批2 ④A) | 07-25 | 接线完成,**待下次扫描首读** |
| macro 周日 harvest 排程(③B) | spec 有 | **launchd 未装**(launchctl 只有 scan-prewarm) |
| OTEL telemetry | 07-05 | 0 次实跑,已被 usage_harvest 胜出 → 退役(E1) |

### 1.5 存量债登记处

- `proposals.jsonl` 36 条:**open 19** / resolved 6 / rejected 5 / applied 6。open 中 P0 族 = intel 可信度三连(pr_20260714_006 捏造涨停、_007 限频虚设、pr_20260716_003 日期焊接)——**本次 run 活体复发**(§1.3 的零 URL + 价格错断言)。
- `feedback.jsonl` 12 条:open 9(fb_20260704_001 token 过大——本文 §3 即答案;_002 报告质量基线——是 L3 降档的前置)。
- `lessons.jsonl` 仅 6 条,其中 2 条已被 MTM 提退役(pr_20260725_002/003)→ 有效 4 条,写侧瓶颈依旧(Q5)。
- Wave5 余项:批2 收尾 2 项(macro full 首跑、CP7 首读)+ 批3 全部 + 触发式 7 项 + ①③ 验收——**全部收编进 §2/§6**,原 spec 不再单独调度。

## 2. 主线 R:跑动与裁决(P0,零开发)

> 本主线没有一行新代码,却是全 roadmap 期望值最高的部分:每一项都是「已建成机器的第一次(或欠下的)运转」。

- **R1 retro 欠账清偿(本周,先 07-21)**:`python -m autoresearch.learning.retro <date>` 依次补 07-16/17/21。07-21 产出 6 只 pass1-cut 票的 fwd_2_oc 裁决 = **上涨侧盲区第一个直接证据**,同时是批3 ②B replay 的先导输入;顺带刷新 journal 的 fwd 空列(buy_ledger 自带幂等刷新法)。07-24 的 fwd_2 于 07-28 收盘成熟,当晚一并 attribute。
- **R2 macro full 首跑 + 周排程装载**:新 session 跑 macro full 的 LLM 节(骨架/数据 06-22 起就位,批2 已把 `macro_state` 从 20 文件门解耦——只需 `1_spine/decision.md` 一节)→ `python -m autoresearch.macro.state <dir>` 落 `macro_state.json` → presence-gate 首次激活。同时把 ③B 的周日 harvest plist 真正 `launchctl load`(现状未装)。验收:下次 market_view 开篇不再写「无新鲜宏观视图」;prelude 汇总屏「宏观 full 摘要」行 ✓。
- **R3 下次扫描 = 四重活体验收日(07-28)**:一次真实扫描同时验收:①批1 直播 8 检查点(`_prelude_summary.md` 出现、GATE2 逐只播、CP5 滚动表);②**CP7 `token_usage.md` 首读**(第一份生产真计量,§3 全部触发条件的裁决基础);③Wave3 档案注入 9 条活体验收;④earlystop 卡头首读(`early_stop: {phase, reason}` 入 `_final_ratings.json`,earlystop_ledger 非空)。**验收清单落 checklist 表,逐条 ✓/✗ 记录,✗ 即开修单**——这是把「绿灯不等于有灯」纪律用在 Wave5 自己身上。
- **R4 档案池消化(26 只)**:
  - 方案 A(推荐):每晚 3–5 只 `dossier-init` workflow,~1 周清完;**首晚即用 usage_harvest 计量单档案真实成本**,若单价过高(>300k 加权/只)降速或转方案 B。
  - 方案 B:cap 30→15,只保 L2 高频常客(池有自愈换血机制,retired 39 只已证)。
  - trade-off:A 全覆盖但一周 ~百万级加权 token;B 便宜但 L4 注入覆盖率减半。先 A 首晚实测再定,不预设。
- **R5 批3 执行窗(离线研究,不占交易日,原 Wave5 定义不变)**:②A `ic_by_regime` 报表 + pct_60d 换代裁决;②B 板块动量路阶段1 replay(输入含 R1 的 07-21 裁决);**新增第三件**:两融余额变化因子 30 日冒烟(factor-backlog 排队多时,批2 `macro_cn.py` 已把 margin 端点打通,边际成本降到最低)。三件各出一份裁决报告,**正负结论都算完成**。
- **R6 节律制度化**:裁决点日历 v2(§6.2)进 prelude 汇总屏「当日件」行(现有 📐/🔁/🚪 机制,零新概念);每周日 = macro harvest + 日历巡检。

## 3. 主线 T:token/速度第二刀(真分布重排)

### 3.0 两把尺分开裁

| 尺 | 头号目标(真数据) | 旧假设(已推翻) |
|---|---|---|
| 加权 token | 主会话 27% > l4-card 23% > intel 20% > gp 壳 14.5% | 「L3 37% 是头号」 |
| wall-clock | L4 研究 24.8min > L3 21.5min > 预热 16min > 行业 brief 14min | (大体一致) |

每刀标注:动哪把尺、触发条件、回滚。**红线沿 §0.2;凡改生产行为→读数+用户点头。**

### 3.1 小刀(批 B 可实施,低风险)

- **T1 gp step-wrapper 降载**(成本尺):7 个 2-消息壳 agent 只为跑一条 Bash/门命令,却各背 ~60k billed 的 opus 系统前缀(合计 ≈287k 加权)。改法:workflow 中纯 Bash/gate 步骤的 `agent()` 调用加 `opts.model='haiku'` + `effort='low'`(**真 per-agent 降档只在 Workflow 可靠**——既有裁定)。gate 本体是确定性 CLI,agent 只是壳,判断零损失。注意:现行加权尺只含 cache 倍率**不含模型价差**,改 haiku 后加权 token 数变化不大、真实成本降一个量级——验收因此定为:CP7 分模型列中 gp 壳全部落 haiku 行(opus-gp 桶≈0)且 GATE 行为逐字节不变(分模型计价列见 T8)。回滚:删 opts 一行。
- **T2 ensemble 同档早止**(token 尺,出现日最多省 1/3):现状 SELL 复核固定 3 跑取中位;601869 三票全 UW(spread=0)白烧第三跑。改法:**仅当 run2 与 run1 评级严格同档**→ 跳 run3(两票同档,中位数=该档,结果数学上不变);任何分歧(如 300857 的 [Sell, UW])仍跑第三跑当裁决票——**行为零改变,只砍确定冗余**。同时给 ensemble 落 spread 账本,攒 n≥5 再裁「SELL 复核是否降为 1 跑」。回滚:config 开关。
- **T3 主会话瘦身·第一期**(token 尺,目标 61→~40 消息):27% 加权来自编排本身(每消息重读 ~113k cache)。第一期只动**零信息损失**的两处:①收尾链(assemble→gate4→usage_harvest→CP7 播报)合并为单消息批调用;②CP5 滚动表轮询改事件驱动/加大间隔(Monitor until 已是既有机制),消灭空轮询消息。**不动直播内容本身**(批1 刚建,先让它跑满一次验收)。SKILL.md 精简列第二期(触发条件:CP7 证实主会话占比仍 >25%)。
- **T8 计量常态化 + 叙事纠偏**(防再误导):summary 的「token 消耗(估算)」表**退役**,替换为 CP7 真表(或在真表缺席时显式写「本 run 无计量」——「文件不存在是弱证据」纪律);`STAGES.md` 计量节改写(删 OTEL 五件 env 说明,记 usage_harvest 为唯一正典);(P2)usage_harvest 加 `--transcripts <glob>` 追溯模式,把本文附录 A 的手工驱动固化成官方入口;(P2)token_usage 表增**分模型计价列**(haiku/sonnet/opus 价差入表——T1 验收与真实成本口径都需要它)。

### 3.2 速度侧(wall 尺)

- **T7 预热真前置**(免费 −16min):launchd 已装载,但 07-25(周六)补扫仍在 run 内同步付 959s——触发时刻/新鲜度判定与「非交易日上午补扫」场景不匹配。修法(先诊断后动):核对 plist 的 StartCalendarInterval 与 prelude 的 staleness 判定,目标 = 扫描任何时刻启动,`_stage_timing` 预热行 ≈0(已提前完成)或明确显示「内联补跑原因」。
- **T6 L3 的刀改由速度尺立案**(挂触发条件,不动结构):token 理由已消失(7.8%),但 20.5min 单 agent 静默思考仍值得治。候选保持 Wave5 ④C 原样:拆两段/降 effort(max→xhigh),前置条件不变(fb_20260704_002 卡质量基线 + 影子 parity);新增轻候选:pass1_target 40→35(复用 07-18 那套影子验证法,先证 0 漏再动)。
- **行业 brief 段(841s)与 intel 段观察**:brief 是 7 agent 并行,wall 被最慢者 + 前置 pack 构建拖住;TTL 复用命中率纳入 CP2 播报(读数化),复用窗/K 上限调整挂读数。intel 的 wall/token 双治理并入 Q1+④C(A/B 账本 08-08 结算)。
- 汇总(常规日预期):100min → **~65–75min**(T7 −16min + T3 收尾合并 −3~5min + T2 出现日 −2~4min;L3 拆分若过裁决再 −8~14min)。

### 3.3 第二刀触发条件总表(吸收④C,按真数据重排)

| 刀 | 动哪把尺 | 触发条件(裁决输入) | 状态 |
|---|---|---|---|
| T1 gp 降载 | token | 无(纯壳,判断零损失) | **批 B 直接做** |
| T2 ensemble 早止 | token | 无(语义保持);SELL 复核降 1 跑另需 spread 账本 n≥5 | **批 B 直接做**(降跑挂账) |
| T3① 收尾合并/轮询事件化 | token | 无 | **批 B 直接做** |
| T7 预热前置 | wall | 诊断先行 | **批 B 直接做** |
| intel 降默认/限频压实 | 双 | intel A/B 账本 ≥10 日(~08-08)+ CP7 真成本 | 挂 |
| L3 拆分/降 effort/40→35 | wall | fb_20260704_002 质量基线 + 影子 parity 0 漏 | 挂 |
| ≥OW 双复核降档 | token | 买单 n≥10(现 n=9)+ 翻案率读数 | 挂 |
| SKILL.md 精简 | token | CP7 证实主会话占比仍 >25% | 挂 |
| sector-brief 降档/复用窗 | token | CP2 复用命中率读数 ≥3 跑 | 挂 |
| L3 紧凑表再瘦 | token | CP7 证实 L3 输入占比(预期低,末位) | 挂(大概率关闭) |

## 4. 主线 Q:研究能力质量

- **Q1 intel 可信度硬契约(P0 族清偿,批 B)**:三条 open 债 + 本次 11/11 零 URL、2 条价格错断言的活体复发。四件套:
  1. **URL 契约**:每条可查证事实断言须带来源 URL 或显式标「未核实」;`intel零URL` lint 从 warn 攒 3 跑误报率后升 fail(advisory→enforced 惯例)。
  2. **限频真执行**:cap=15 在 agent def 里只是指令(07-14 实测 24 查询/上限 15,pr_20260714_007);改为稿头自报查询数 + lint 对账,超限 warn。
  3. **价格断言禁区**:涨跌幅/涨停等行情数字一律由确定性 slim 供给,intel 只做定性增量——从源头消灭 price_claim_mismatch 族(比逐条校对便宜且彻底)。
  4. **日期焊接抽查制度化**:pr_20260716_003 的对账法(原子数字全真、组合为假)固化为每跑抽 2 稿的 lint 抽样。
  - 验收:下次扫描 `intel零URL`=0、`price_claim_mismatch`=0;情报可审计率(带 URL 断言占比)进 CP 播报。
- **Q2 档案体系「从建成到有用」**:R4 消化 + R3 注入验收之外,两个读数补齐:档案注入对卡质量的 δ(t1_review 对「有档案 vs 无档案」股的准确度分桶,攒 ≥10 卡);8 月中报季跑首次 `dossier.reconcile 20260630`(prelude 📐 提醒已接线)。
- **Q3 宏观纵深收尾**:R2 首跑后复检 market_view 洞见密度(07-25 侦察:差距 = 缺数据 70%(批2 已补:北向 5 日 +183.6 亿 vs 两融 −1485.8 亿的背离、上证 28% vs 创业板 76% 分位的哑铃证据)+ 缺框架 30%(macro_state 注入后复评));pr_20260721_002 当日切面块(三大指数当日涨跌/涨停家数/板块当日 top3)列批 B 小件——frame 现有截面直接可算,零新端点。
- **Q4 因子队列推进(全走 factor-backlog 统一验收,不散接)**:两融冒烟(R5);一致预期 EPS 修正(07-02 起积累,≥60 日 → ~09 月底裁决,挂日历);pct_60d 换代(批3);回购/增持事件研究排队(与 event 召回路严格区隔,后者取证未决前不动)。
- **Q5 lessons 写侧**:现 6 条、2 条退役中(MTM 流程在跑=健康信号,不是病)。推荐**维持克制**(ATLAS 先验)+ 一个零成本改动:retro 收尾强制输出「lesson 候选:1 条或显式 0 条+理由」,把「没写」从静默变成显式决定。配额制加压不做。
- **Q6 小契约修缮包(批 B,全部有本次实锤)**:
  | 项 | 实锤 | 修法 |
  |---|---|---|
  | slim-size 检查线过时 | 瘦身后 8.7–10.1KB 成新常态,10KB 线判 11/11 假阳 | 线降至 7.5KB 或改分位自适应;顺带把 `chk_slim_size` 从卡分里摘出重算历史 |
  | `chk_blind_pass` 11/11 fail | 未诊断 | 先诊断是检查坏还是流程坏(绿灯纪律:别顺手改阈值) |
  | 601869 slim_deep 1,280B 离群 | 兄弟档 5.4–6.0KB | 按 .SH/.SS 前科家族查 harvest deep 分支;补「slim_deep 尺寸 lint」 |
  | conviction 标度不一 | pr_20260717_005(0.62 vs 0–100) | l4-stock 回传契约钉死 0–100 整数 + lint |
  | run_health 时序 quirk | `missing: gate_fires.csv` 但文件同刻存在 | 健康快照挪到 gate 账本写盘后 |
  | L3_news 203×2B 空文件 | anns 退役残渣 | producer 侧空结果不落盘(或单文件汇总) |

## 5. 主线 E:工程债

- **E1 退役清单**(每项遵守「删 test 先读 docstring 查双职」纪律):
  | 件 | 依据 | 动作 |
  |---|---|---|
  | `trace/telemetry.py` OTEL 路 | usage_harvest 对拍胜出(批2 spike:transcript 自带 usage 且能给 cache 命中率) | 删模块+16 个测试里属它的部分;SKILL.md:36 与 STAGES.md OTEL 节改写(并入 T8) |
  | `scan/progress.py` 存在性推断 | pr_20260717_004(一晚两次误报);批1 直播用「产物即完成」语义替代 | 直播首验收(R3)通过后删;删前 grep 消费者 |
  | 报告 token 估算表(bytes÷2.8) | 本次证伪 30× | 并入 T8 |
  | 空目录 ×2、`.omc` stray | 盘点实锤 | 直接清 |
- **E2 open register 大扫除(19 open proposals → 目标 ≤10)**:cap_floor 家族三条(pr_20260624_001/0714_003/0717_002)合并关账记负结果档案;pr_20260712_001+0714_002(OW 三门)按已裁「不松门」关账;pr_20260717_004 并 E1;pr_20260721_001(config-echo)因 `user_config_echo` 非空验证 + 批1 pinned 硬化已弱化,降级并入 product_shape_lint 或直接关;intel 三连与 event 取证保持 open 至各自裁决;fb_20260704_001 以本文 §3 为答案关账。**每条在 retro 流程里正式记 resolution,不悄悄改状态**。
- **E3 文档面**:PANORAMA.md 基线刷新(07-16 → 本文 §1 读数,尤其「真 token 经济学」节全新);README 架构节核对。
- **E4 显式非目标**:>800 行大文件不为拆而拆;不加第 7 个 skill;不建 HTML 看板(Wave5 已否);harness cache TTL 不可项目侧调,不折腾。

## 6. 批次与裁决点日历 v2

### 6.1 批次

| 批 | 内容 | 性质/预估 |
|---|---|---|
| **批 A(本周,零开发)** | R1 retro×3 → R2 macro full 首跑+周排程装载 → R3 下次扫描四重验收(07-28)→ R4 档案首晚 3–5 只实测 | 纯跑动;token 预算:档案首晚由 usage_harvest 定价后决定后续 |
| **批 B(小刀,~1–2 天开发)** | T1 gp 降载 · T2 ensemble 早止 · T3① 收尾合并/轮询事件化 · T7 预热前置诊断修 · T8 估算表退役+STAGES 改写 · Q1 intel 四件套 · Q6 修缮包 · Q3 当日切面块 · E1 退役 · E2 关账 | 每项独立可回滚;TDD+变异探针纪律(绿灯必须会变红) |
| **批 C(离线研究,~1–2 天)** | R5:ic_by_regime + pct_60d 裁决 · ②B 阶段1 replay · 两融 30 日冒烟 | 三份裁决报告,正负皆完成 |
| **触发式** | §3.3 表全部挂账项 + ②B 阶段2→3 + ②C playbook 修订 + ②A 权重条件化 + Q5(b) | 每项读数到位→提案→用户点头 |

### 6.2 裁决点日历 v2(收编 Wave5 全部+新增)

| 时点 | 裁决/动作 | 输入 |
|---|---|---|
| 本周即刻 | retro 补 07-16/17/21(先 21);macro full 首跑;macro 周排程装载 | R1/R2 |
| 07-28(下一交易日) | **扫描四重验收 + CP7 真计量首读**;当晚 07-24 fwd_2 成熟→attribute | R3 |
| CP7 首读后 | §3.3 各挂账刀逐条对读数;主会话占比复核(T3 第二期立案与否) | token_usage.md |
| ~08-08(+10 交易日) | event 路裁决(pr_20260725_001);intel A/B 结算→降默认裁决;②C 强势票停因桶(earlystop 账本 ≥10 日);②B 影子(若阶段1 过) | 各账本 |
| 8 月中报季 | `dossier.reconcile 20260630` 首跑 | 披露日历 |
| 买单 n≥10(现 9) | ≥OW 双复核降档;评级基率注入 skeptic/PM | buy_ledger |
| ~09 月底 | 一致预期 EPS 修正因子 ≥60 日 → factor_lab 验收 | consensus 积累 |
| 每周日 | macro harvest(排程)+ 日历巡检 + 池日检 | R6 |

### 6.3 本波「完成」的定义

1. CP7 真表首读落盘且覆盖率行明确(未覆盖 agent 显式列缺)。
2. retro 欠账清零;07-21 六只 pass1-cut 裁决有结论。
3. `macro_state.json` 非空、market_view 开篇不再「无新鲜宏观视图」。
4. 下次扫描 `intel零URL`=0、`price_claim_mismatch`=0(Q1 生效)。
5. CP7 分模型列中 gp 壳全部为 haiku、GATE 行为不变(T1 生效)。
6. open proposals 19 → ≤10(E2)。
7. 批 C 三份裁决报告落盘(正负结论均可)。

## 附录 A:run 真计量的追溯方法(可复现)

- 该 run(20260725_1316)结束于 07-25 13:16,计量代码 `73749b1` 落地于同日 17:56 → run 目录无 token 产物。但 harness transcripts 存活于 `~/.claude/projects/-Users-qingbin-zhuang-Personal-TradingAgents/<session>/subagents/workflows/wf_*/agent-*.jsonl`。
- 方法:复用 `autoresearch.trace.usage_harvest.usage_of`(按 message.id 去重取末条,防流式重复虚报——批2 实测重复会虚报一倍)遍历 49 个 wf agent transcript(meta.json 提供 agentType,journal.jsonl 提供股票码),主会话按 run 窗口(local 11:35–13:20)过滤;计价倍率 cache读×0.1 / 5m写×1.25 / 1h写×2。
- **覆盖率声明**:transcripts 只能证明「跑过的」,不能证明「没跑过的」;凡未落 transcript 的调用不在合计内(下界语义)。并行的 Wave5 侦察 session(e205a3cb,4 个 Explore)已识别并**排除**在 run 账外。
- 固化建议见 T8(`usage_harvest --transcripts` 模式);正式产线自 07-28 起走 CP7,无需追溯。

## 附录 B:Wave5 余项 → 本文归属映射

| Wave5 条目 | 本文归属 |
|---|---|
| 批2 剩余:macro full 首跑 / CP7 首读 | R2 / R3 |
| 批3:②A ic_by_regime+pct_60d、②B 阶段1 replay | R5(批 C) |
| ④C 第二刀四项 | §3.3 表(排序按真数据重排,L3 项改速度尺立案) |
| ②B 阶段2→3、②C playbook 修订、②A 权重条件化 | §6.1 触发式 |
| ① 直播验收、③ 两周验收 | R3 / §6.2 |
| 裁决点日历 | §6.2 v2(本文取代) |

## 附录 C:本次侦察证据索引

- 架构盘点:LOC/文件/测试/提示面/引用图谱——盘点 agent 实测(wc/grep),关键点:trace/ 零 import、STAGES.md 40,776B、agents 33KB、workflows 35KB。
- run 取证:`reports/scan/20260725_1316/`(manifest/summary 39,796B/trace/)、`context/scan/2026-07-24/`(分组字节表、mtime 时间线、`_stage_timing.json`、gate_fires.csv 26 行、process_scores.csv、run_health l4_phases、`_prewarm.json`);token 表见附录 A 方法。
- 账本:`reports/learning/{journal,buy_ledger,zero_buy_ledger,paper_nav_summary,earlystop_ledger}.md`;`context/knowledge/{proposals,feedback,lessons}.jsonl`、`coverage_pool.json`;`retro pending` CLI 输出(07-16/17/21)。
- 上游文档:Wave5 spec、批2 record、`docs/research/2026-07-13-next-optimization-survey.md`(线 A/C 已于 07-18 波落地)、`docs/research/factor-backlog.md`、PANORAMA.md。

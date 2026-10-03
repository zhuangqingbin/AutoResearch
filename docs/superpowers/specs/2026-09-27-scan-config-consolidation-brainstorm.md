# scan_config.jsonc 精简 × 生效审计 × 扩容 × 分类 brainstorm(2026-09-27)

> **状态(2026-09-27 晚)**:P0–P4 与 Q1–Q10 已按本稿推荐实施(Q11 主尺不进 config;Q7 只预留 `l4.review` 键位,两宿主失败语义未统一)。配置标准的真身见 `autoresearch/contracts/scan_config.py` + `autoresearch/scan/config_standard.py` + SKILL.md「配置」节;本稿留作沿革。
>
> **性质**:设计讨论稿,**零实施**。用户四问原文:① 「注释太多了 精简 但还是要说清楚每个参数的用处」② 「确保所有参数都是能够按预期生效」③ 「我是想把尽可能多的可控参数都放在这个文件(让 skill 更灵活) 你看有什么遗漏的参数吗」④ 「整个分类是否合理」。
> **本 session 已收两条裁定**:① 注释只留「作用 + 生效点」,历史一概不要,也不迁去别的文档;③ 维持「尽可能多的可控参数进文件」—— 08-11 那份「不入 config 清单」及其依据(实验治理,已随 08-21 learning 层退役)作废。
> **证据来源**(零 LLM 之外只有读码):(a) 我逐键追到消费代码的审计(§2,每条都亲自核过);(b) 两只 Explore agent 的静态清点——Python 管道约 100 条、编排层(两 JS workflow + session_agent 宿主 + agent 定义 + 无人值守脚本)约 40 条,行号是它们报的,**头牌项我复核过,其余实施前逐条复核**;(c) `docs/flow-handbook.md` 第 7.10 节那张「块 | 键 | 生效点」表(untracked,抄了同一个错的生效点)。
> **与既有稿的关系**:不改 09-26 三线设计稿(A 编排壳税 / B SWING_RULER 影子 / C launchd)的任何裁定;09-25 起的十日真跑规则冻结窗内,本稿所有「加键」都以「默认 = 现值、逐字 parity」落地,不改行为。

---

## 0. 边界(既有裁定,全部遵守)

| 裁定 | 对本稿的约束 |
|---|---|
| scan_config.jsonc = 唯一参数事实源;新键三件套 = 白名单 + 真实消费点 + 测试锁(08-11) | 本稿每个候选键都标接线点;没有消费点的键不加 |
| 修 agent 行为/成本,Claude 与 Codex(legacy JS 宿主与 session_agent 宿主)两边都修都验(09-15) | 每个新键都写两宿主的接线;§2 单独列「一侧宿主吃、另一侧不吃」 |
| 引擎隔离只共享 lake/(08-11) | 不涉及 |
| 主尺 gap_c1_o2 不换(07-10 / 08-05);L4 不复用(07-29);learning 层退役(08-21) | 主尺不进 config(§4.C);L4 卡复用类键一个不加;不提「实验治理」 |
| 十日真跑规则冻结窗(09-25 起) | 冻结窗内只加「默认 = 现值」的键;`skip_when_dead` 接线仍排冻结窗后 |
| 09-26:agent 定义文件是契约唯一真身 | prompt 内数字的处理只做「注入参数块」,不把 .md 改成模板引擎(§4.D) |

---

## 1. 现状一页

| 量 | 值 |
|---|---|
| 文件 | 402 行 · 34.4KB · 16 个顶层块 · **119 个叶子键**(去掉 agents/agent_engines 是 87) |
| 注释 | 约 330 行是证据读数 / 沿革 / 终审补记 / 回滚杆讨论;真正的「作用」句不到 80 行 |
| 白名单有、文件没写(暗键) | `funnel.channel_floors`、`calendar.index_rebalance_flow` |
| 读该文件的生产模块 | 34 个(py + js + sh);读它的测试文件 26 个,其中 4 条测试锁生产值(l2 形状 / intel 死票门关 / 调样两杆同开 / delivery=none) |
| 改块名的波及面 | `"funnel"` 生产 27 处 + 测试 40 处;`"l4_intel"` 31 + 45;`performance` 共 12 处 |
| 散落在代码里、config 够不着的可调常量 | Python 侧约 100 条 + 编排层约 40 条(§4 已去重归块) |

---

## 2. 问②:现有键生效审计

图例:✅ 真生效 · ⚠️ 生效但不按预期 · 🟡 只有一侧宿主吃 / 被别的键短路 · ❌ 死键 · ⚪ 暗键

| 键 | 状态 | 证据(本次亲自核) | 处置(P0) |
|---|---|---|---|
| `pinned.cap` / `ttl_days` | ⚠️ **分裂脑** | 只有 `scan/run_bootstrap.py:128` 把 config 值传进 `load_pinned` 烤进 RunContract;真正做 L1 强注的 `universe.py:427`、L3 的 `l3/prompt.py:343`、`l3/merge.py:620`、`report_sections.py:624`、`prelude.py:111`、`tripwire_watch.py`、`dossier/pool.py` 全部 `load_pinned(date, path=...)` 不传 → 用代码默认 5/10。今天 config 恰好也是 5/10 所以看不出;改成 8 就是「契约说 8、L1 只注 5」 | `load_pinned` 的 `cap/ttl_days` 形参默认改 `None`,函数内自己 `knob("pinned", …)` 兜底;调用方一行不改。注释生效点改成 `user_config.load_pinned`(不是 frame.py) |
| `budgets.concurrency.web_search` / `web_fetch` | ⚠️ **声明无执行** | `l4_tasks._normalize_caps` 要求四键齐全(缺了 raise),但两个 web 帽在 py/js 里零执行点;`trace/web_budget.py` 只做工具名归类计量 | 从 `REQUIRED_CAPS` 与文件里删掉(推荐);或真接一个执行点(无现成载体,不推荐)—— **Q3** |
| `budgets.concurrency.l4_stock` | 🟡 **两宿主语义不同 + 三处缺省** | JS 宿主 = L4 派发宽度(缺省 64,`l4_tasks.py:37`);Python 宿主 = 所有角色的并发上限(缺省 4,`mailbox_cli.py:32`;`budget.py:27` 又是 4);`docs/session-agent/README.md` 推荐 `--max-parallel 8`。Python 宿主 `domain_ops.py:1734` 建任务簿时**不传 caps** → tushare/web 帽在该宿主全被忽略 | 一处常量 64 三处共用;Python 宿主建任务簿时传 config caps;注释写明两宿主语义 |
| `sector.reuse_ttl_days` | 🟡 **Python 宿主不吃** | `sector/reuse.py` CLI 经 `knob` 读到;`session_agent/domain_ops.py:575,1485` 调 `find_reusable(...)` 不传 ttl → 恒 5 | 两处传 `knob("sector","reuse_ttl_days",None,5)` |
| `sector.max_briefs` | ⚠️ **不是真上限** | `sector/pack.py:397-402`:「top3 看多」行业**追加且不占 K**,实际 brief 数可到 K+3 | 改成真上限(推荐,行为小变),或加 `sector.healthy_top3_extra: true` 显式旋钮 —— **Q3** |
| `l4.budget_flags` | ⚠️ **算术空转** | `card_count.py:28`:finalist 名额 = 13 − 3 = 10;`menu.l4_budget` 最低档 = max(8, 30//3) = 10 → `min(10, ≥10)` 恒 10。只有 max_cards > 13 或改 base/floor 时旗才咬得到 | 注释一句写清条件;§4 把 base/floor/五面旗阈值一起放进 `l4.budget` 块后它就真有用了 |
| `l4_intel.max_queries` | ⚠️ **四处缺省 + 软顶只有一侧执行** | 缺省:`l4-stock.js:338` 15、`dispatch.py:123` 15、`intel_guard.py:74` 20、`self_review.py:1273` 15。确定性软顶裁稿(`configured_soft_cap`)只在 Python 宿主 `domain_ops.py:1891` 执行,JS 宿主跑 guard 不带软顶,只剩硬顶 30 | 一处 `DEFAULT_MAX_QUERIES = 20`;两宿主都过软顶 |
| `l4_intel.skip_when_dead` | ❌ **比自述更死** | 文件说「true → 落 `_intel_gate.json`」;实际 `intel_gate.decide` **生产零调用点**(只有 artifacts 注册表与一条注释提到它),开了什么都不发生 | 冻结窗内不接线(批 6 Task 3 原计划);注释改成「尚未接线,开关暂无效果」;或先删键 —— **Q3** |
| `performance.streaming_l4` | 🟡 **仅 legacy JS** | `scan-market.js:58` 读;session_agent 宿主零处读(它恒逐票任务簿) | 保留,注释标「仅 legacy workflow」;分类上归运行时 |
| `funnel.regime_aware` | 🟡 **被 `weight_profile` 短路** | `scoring.resolve_weights`:preference 档不看 regime;文件已写明 | 保留,一句话 |
| `l3.pass1_target` | 🟡 **值生效,文案不跟** | `triage` 吃 40;但 L3 prompt 文案 `scan-market.js:505`、`dispatch.py:163` 写死「~40 表」,`l3-rank.md` 写「~60」,代码缺省 60 | 文案从 config 渲染(§4.D);代码缺省与 config 对齐 |
| `agents.*.tier` 的 effort | 🟡 **mailbox 模式丢失** | Claude Code `Agent` 工具传不了 effort(`docs/session-agent/README.md:99`)→ 吃 agent .md frontmatter:macro-brief high(config max)、sector-brief high(xhigh)、l4-card/ens_review xhigh(max)、l3_repair 用 l3-rank 文件 max(medium)。JS 与 headless 正常 | 把五个 .md 的 frontmatter effort 对齐 config 解析值,三条路径就一致 —— **Q6** |
| `relative_buy.activate_date` | ❌ | 零消费点(文件自认) | 删 |
| `funnel.channel_floors` | ⚪ | `universe._funnel_overlay` 真读 → `recall_select(channel_floors=)` | 写进文件(`{}`)+ 一句作用 |
| `calendar.index_rebalance_flow` | ⚪ | `index_events.py:338` 真读 | 写进文件(`false`)+ 一句作用 |
| 其余 ~95 键 | ✅ | l0 五键 / funnel 其余 / l2 四键 / calendar / sector / l3 全部(lowturn 九子键逐个核到 `common/turnup.py:121-147`)/ l4 / relative_buy 五键 / retention / delivery / agents / agent_engines | 只改注释 |

**注释里指错的生效点(四处)**:pinned 写 frame.py(真身 run_bootstrap → load_pinned);budgets 写 `resolve_budget_policy`(不存在;真身 `budget.normalize_budgets` → `RunContract.stage_budgets` → `post_run.observe_run/evaluate_history`);头部「STAGES.md 控制面 / 实验治理」(08-21 起不存在);头部「SKILL.md 阶段×键×生效点全表」(09-26 瘦身后只剩四行)。

**两宿主的非 config 差异(顺带记下,不在本题里修,但 §4 给它们预留键位)**:复核失败 JS 标 degraded 继续、Python `REVIEW_FAILED` 停整场(`runner.py:770-784`);intel 失败 JS 照出卡、Python 该票失败;≥OW 复核触发 JS 用正则 `/(overweight|\bbuy\b|增持|买入)/i`、Python 用集合 {Buy, Overweight};JS 逐步读**活**的 config 文件(跑到一半改文件会生效)、Python 宿主前奏后冻结(GATE1 仍读活的);`dossier-init.js` 没有空 config 守卫、也不读 `cfg.engine`。

---

## 3. 问①:注释新格式

**原则**:每键一行尾注 = 作用;每块一行头注 = 名字 + 一句话 + 生效点(模块.函数,一侧宿主才吃的标宿主);块内某键生效点与块不同时才在该键尾注补一个。**不写**:证据数字、日期沿革、终审补记、回滚讨论、幽灵行解释。缺省值不写(「删 key = 内建默认」是文件头契约,不逐键重复)。文件头 ≤ 6 行:优先级、白名单位置、三件套、装载链、三个例外文件(pinned.jsonc / weights.json / .env 凭证)、两分区各一句。

**样例(l3 块,现 43 行 → 14 行)**:

```jsonc
  // ── l3 · 精排(pass1 确定性分诊 + pass2 holistic)── 生效点 scan/l3/prompt.prepare_l3_table · l3/triage.triage_l2_for_l3 · l3/merge.write_finalists
  "l3": {
    "two_pass": true,                 // 两遍法;false = 单遍读全表
    "pass1_target": 40,               // pass1 留给 l3-rank 的行数(同时渲染进 L3 prompt 文案)
    "composite_seat": { "enabled": true, "m": 3 },   // composite 证据席:强送 m 只进 finalists,计入 l4.max_cards(merge.inject_composite_seats)
    "lowturn": {                      // 低位转强旗 + pass1 强留;谓词 common/turnup.lowturn_flag(L1 lowturn 路同阈值)
      "enabled": true,
      "max_dist_high_60": -15.0,      // 距 60 日高 ≥15%
      "max_pct_60d": 10.0,            // 60 日涨幅 <10
      "min_vol_ratio_20": 1.2,        // 20 日量比 ≥
      "min_pct_5d": 0.0,              // 近 5 日涨幅 >
      "require_above_ma20": true,     // 站回 MA20
      "require_ma5_gt_ma10": true,    // MA5 > MA10
      "fund": "main_or_cmf",          // 资金转正判据:main | cmf | main_or_cmf
      "knife_pct_60d": -35.0,         // 60 日跌超此值且主力不为正 = 落刀,不接
      "pass1_cap": 8                  // pass1 强留上限
    }
  },
```

**预算**:现 119 键 → 约 150 行;§4 扩容后约 280 键 → 约 330 行,全是一行一键,没有段落。`docs/flow-handbook.md` 7.10 那张表改成一句指针(「键与生效点见 scan_config.jsonc 本身」),不再维护第二份。

---

## 4. 问③:遗漏的可控参数

清点原料约 140 条,去掉:已经过 `knob()` 的、纯 schema/枚举/列名、未使用常量(`l4/context._BASE_RATE_THIN_N`、`parsers.pick_opportunity_candidates`)、agent .md 里的纯文案数字(字数/行数)。剩下按「进哪个块」归并如下。**每条的接线 = 形参默认改 `None` + 入口 `knob()`;两宿主共用的 Python 模块接一次即两边生效,只有标「JS / SA」的要各接一次**(SA = session_agent)。**冻结窗内全部以默认 = 现值落地**。

### 4.A 分区一「漏斗行为」:改了会变选股 / 评级 / BUY

| 目标块.键 | 现值 | 来源(file:line) | 备注 |
|---|---|---|---|
| **`signals`(新块,跨阶段谓词,L2/L3/E6/档案共用)** | | | |
| `signals.knife_pct_60d` | −20.0 | `common/scoring.py:293` | 落刀 = 60 日跌超此值;L2 落刀帽、席位剔刀、buyability 都用它 |
| `signals.healthy_pct60_range` | [0, 40] | `scoring.py:306` | 健康上涨带 |
| `signals.main_flow_distortion` | {abs 0.02, ratio 0.5} | `scoring.py:320` | 主力净流「失真」判据 |
| `signals.pledge_pct` | {warn 40, note 20} | `scoring.py:373` | 质押旗阈 |
| `signals.regime_thresholds` | {risk_on 0.55, risk_off 0.30} | `common/regime.py:41` | regime 分类线 |
| `signals.temperature_bands` | {冰点 20, 发酵 40, 高潮 65, 滞回 3} | `scan/temperature.py:136-143` | 进 market_pack,喂策略师与 L4 |
| **`funnel`(L1)** | | | |
| `funnel.recall_mode` | "multi" | `universe.py:342` | multi / composite 对拍;今天只有 CLI 能改 |
| `funnel.heat_weights` | {turnover 0.15, vol_ratio 0.10} | `recall/channels.py:192-194` | heat 路加成 |
| `funnel.panel_lookback_days` | 60 | `frame.py:46` | 量价面板回看(lowturn / vol_ratio_20 的数据) |
| `funnel.panel_min_days` | {vol 10, turnup 40} | `frame.py:45,47` | 不够则整列 NaN |
| `funnel.event_lookback_days` | 10 | `scan/events.py:100` | event 路(默认停用) |
| **`l2`** | | | |
| `l2.knife_cap_exempt_styles` | ["反转","低位转强"] | `recall/l2_stratify.py:49` | 不受落刀帽的桶 |
| `l2.sector_seats.{min_members, knife_median_min, mom_center, mom_half, main_pos_min}` | 8 / −20 / 10 / 15 / 0.5 | `scan/market.py:24-27,235` | healthy top3 行业资格(席位与 sector brief 共用) |
| **`sector`** | | | |
| `sector.reuse_mom_shift_pp` | 3.0 | `sector/reuse.py:52` | 行业动量位移超此值不复用 brief |
| `sector.sources` | {red_top 3, l2_conc_top 3, healthy_top3_extra true} | `sector/pack.py:380-402` | brief 来源;`healthy_top3_extra` 就是 §2 那个「不占 K」的显式化 |
| `sector.leaders_n` / `terrain_max_rows` / `readthrough_max` | 5 / 40 / 4 | `pack.py:326,410,84` | 喂 sector-brief / L3 地形段的体量 |
| **`l3`** | | | |
| `l3.guards.chase_1d_pct` | 9.5 | `l3/merge.py:17` | 当日涨幅 ≥ 此值剔除(守卫⑦;composite 席位、行业席位共用) |
| `l3.guards.sector_cap` | 3 | `merge.py:21` | 同行业 finalist 上限(守卫⑧) |
| `l3.guards.healthy_quota_frac` | 0.0 | `merge.py:28` | healthy 配额(守卫④;0 = 关) |
| `l3.guards.conv_force_in` / `conv_min` | 75 / 55 | `merge.py:357,361` | ins75 / lt55(守卫②) |
| `l3.guards.qualify_conv` | {backfill 55, lane 65} | `merge.py:128,215` | 剔除后回填 / lane 置换的够格线 |
| `l3.guards.lane_floors` | {trend 2, lowturn 1} | `merge.py:175,394-396` | 行业帽剔票时不可击穿的 lane 下限 |
| `l3.composite_seat.exclude_knife` | true | `merge.py:97`(无条件) | 09-24 加的席位剔刀,**今天没有任何旋钮**;加键即补上回滚杆 |
| `l3.pass1.resonance_cap` / `resonance_min_channels` / `healthy_mandatory` | 5 / 3 / false | `l3/triage.py:29,174,37` | 共振强留;healthy 全留 |
| `l3.table.delta` / `delta_tol` | true / {comp 2.0, mom 2.0} | `l3/prompt.py:403,148` | Δ 模式略「昨判弃且无变化」票 |
| `l3.table.sections` | 七个 true | `prompt.py:478-480` | 表内证据列与地形段开关 |
| `l3.profile.*` | pct60 40/10/−10 · pct1 9.5 · 贴顶 −2 · 量比 2 · PE 20/60 · 获利盘 90/25 · RSI 80/20 | `prompt.py:56-111` | 喂 l3-rank 的画像词阈值 |
| `l3.lookback_days` | {evidence 10, news 10, catalyst 10} | `l3/evidence.py:78`、`agents/l3_news.py:128`、`agents/l3_catalyst.py:68` | catalyst 那个也是 intel 死票门的「近 10 日事件」 |
| `l3.finalist_min` | 7 | `scan-market.js:466`、`dispatch.py:101`(**JS + SA 各接**) | L3 prompt 的 finalist 区间下限;上限已由 `l4.max_cards` 算出 |
| **`l4`** | | | |
| `l4.budget.{base, floor}` | 30 / 12 | `scan/menu.py:151` | 菜单感知预算基准 / 下限 |
| `l4.budget.flags.{knife_share_max, knife_rel_min, knife_rel_mult, healthy_min, risk_off, streak_warn, streak_heavy, streak_hard, streak_lookback}` | 0.60 / 0.40 / 2 / 2 / true / 3 / 5 / 7 / 10 | `menu.py:176-200,104` | 五面旗阈值与连败梯度 |
| `l4.budget.tiers` | {one_flag 0.75, multi 0.5, hard_min 8, hard_div 3} | `menu.py:198-200` | 降档比例 |
| `l4.rubric.rating_bands` | {Buy ≥4, Overweight ≥2, Hold ≥−1, Underweight ≥−3} | `l4/rubric.py:34-43` | 六维净分 → 建议评级;**同时写在 `l4-card.md:60`**(§4.D) |
| `l4.rubric.force_full` | {conviction_min 70, channels_min 4} | `rubric.py:50`(调用点 `l4/prompts.py:257`,已接线) | 强先验票强制满卡;`l4-card.md:57` 有对应文案 |
| `l4.review.{ow_ratings, sell_review_pinned_only, max_runs, spread_escalate}` | [Buy, Overweight] / true / 3 / 2 | `l4-stock.js:491-511`、`domain_ops.py:1966-2019`、`decision_finalize.py:134`(**JS + SA**) | 双复核触发与折回;顺手统一两宿主的正则 vs 集合 —— **Q7** |
| `l4.brief.dossier_days` / `echo_lookback_days` | 10 / 5 | `scan/dossier.py:98`、`l4/prompts.py:54` | 前科卡回看 / 昨卡回声窗 |
| `l4.producers.{lhb_reuse_days, lhb_window_days, lhb_tail, consensus_window_days, consensus_min_days, pledge_reuse_days}` | 7 / 20 / 15 / 30 / 10 / 7 | `l4/producers.py:103-104,155,184,197,32` | 卡片输入数据窗 |
| `l4.slim.min_bytes` | 4096 | `l4_tasks.py:1430`、`producers.py:336` | slim 合格地板;`l4-card.md:26` 写的是「>8KB」,要对齐(§4.D) |
| **`l4_intel`** | | | |
| `l4_intel.hard_cap` / `soft_trim_keep` | 30 / 10 | `l4/intel_guard.py:59,67` | 超顶拒稿 / 裁到 N 行 |
| `l4_intel.stale_gap_days` | 7 | `l4/intel_status.py:179` | 超龄事件净分归零(`l4-intel.md:49-51` 文案是 ×1/×0.5/×0,要对齐) |
| `l4_intel.max_attempts` | 3(JS)/ 2(SA) | `intel_status.py:47`、`l4-stock.js:368`、`workflows/scan.py:17`(**JS + SA**) | 盲搜重试;两宿主今天就不同 |
| `l4_intel.resume_max_age_s` | 86400 | `intel_status.py:367`(只有 JS 用) | 同日续传 |
| `l4_intel.dead_gate.{main_inflow_max, cmf_max, obv_max}` | 0 / 0 / 0 | `l4/intel_gate.py:46-48` | 死票三线;随 `skip_when_dead` 接线一起上 |
| **`relative_buy`(E6)** | | | |
| `relative_buy.max_buys` / `second_buy_threshold` | 1 / null | `relative_buy.py:197`(`SECOND_BUY_THRESHOLD = None` = 第 2 只恒不出) | 每日 BUY 上限 |
| `relative_buy.liquidity_pctl_floor` | 0.10 | `:202` | tradable 门 |
| `relative_buy.redflag.{early_stop_reasons, ratings, proposals}` | 4 停因 / [Sell, Underweight] / [SELL] | `:212-219` | no_redflag 硬门否决集 |
| `relative_buy.risk_early_stop_reasons` | 5 停因 | `:232` | 风险面停因 |
| `relative_buy.scoring.{recall_weights, evidence, risk_bonus, missing_fill}` | 0.5/0.5 · 满卡 1.0/早停 0.4/+0.2×3 · 1.0 · 0.5 | `:600-651` | 四面记分 |
| `relative_buy.ranking` | ["borda", "target_align", "amount", "code"] | `:1346-1348` | 排序键顺序 |
| `execution.entry_line` | {pct_chg_max 3.0, pos_in_range_max 0.7} | `contracts/agent_output.py:63-64`;`l4-card.md:154`「勿改阈值」;`tripwire_watch` 解析 | 执行线阈值;要同时喂 prompt + parser + tripwire(§4.D) |
| **`calendar`** | | | |
| `calendar.horizon_days` | 35 | `scan/calendar.py:42` | 解禁往前看 |
| `calendar.unlock_flag` | {within_days 30, min_ratio_pct 2.0} | `calendar.py:155-156` | L4 简报 ⚠️ 解禁旗 |
| `calendar.section` | {window_days 14, big_ratio_pct 5.0, show 8} | `calendar.py:184-185,252` | summary 📅 块 |
| `calendar.index_post_window` | 3 | `scan/index_events.py:49` | 调样生效后继续展示的交易日 |
| **`sentinel`(新块;决定 L3/L4 跑不跑)** | | | |
| `sentinel.auto_below` / `consider_below` | 0.03 / 0.05 | `menu.py:205-206`(调用点 `gates.py:56`、`prelude.py:448`、`menu.py:281` 全用默认) | 全市场健康上涨占比 <3% 自动哨兵档(跳 L3/L4);3–5% 提示人裁 |

**E6 进 config 的一个技术前提**:`relative_buy.py:195` 写着「改常量 = 改规则 = 换 RULE_VERSION」。规则进 config 后,决策文件在 `rule_version` 旁再落一个 `rule_params_sha256`(relative_buy 块的规范化哈希),账本读数按 (rule_version, params_sha) 分组,历史行才可比 —— **Q4**。

### 4.B 分区二「运行时」:改了只变成本 / 速度 / 时序 / 通知 / 留存

| 目标块.键 | 现值 | 来源 | 备注 |
|---|---|---|---|
| `prelude.skip_steps` | [] | `scan/prelude.py:36` STEP_NAMES(只能 `--skip`) | 步骤去留进 config |
| `prelude.announcement_lookback` | 5 | `prelude.py:248` | 公告源盲区回查天数 |
| `runner.{window_start, hard_stop, min_runner_minutes, run_timeout_minutes, live_run_window_min, kill_grace_s, subprocess_timeout_s, force_full}` | 21:10 / 01:00 / 10 / 180 / 90 / 5 / 1800 / false | `scan/scan_run.py:60-68,122,452-496` | 无人值守场全部时钟 |
| `runner.streaming_l4` | true | 现 `performance.streaming_l4`(仅 JS) | 块级迁移 —— **Q2** |
| `readiness.{min_rows, stable_polls, interval_s, deadline, late_recheck_s, settle_hhmm}` | 5300 / 2 / 300 / 22:30 / 30 / 19:15 | `scan/readiness.py:37-42`、`prewarm.py:33` | 湖就绪门 |
| `l4_tasks.{max_attempts, stale_after_s, slim_retries, slim_workers, slot_poll_s, slot_heartbeat_s}` | 2 / 3600 / 1 / 4 / 5 / 60 | `l4_tasks.py:29,724,1429,469-470`、`producers.py:337-338` | 任务簿与 tushare 槽 |
| `session.timeouts.{mailbox, headless}` | 按角色 900/600/2400/600/900/1800/1800 与 900/600/1800/600/720/1500/1500 | `session_agent/executors/base.py:104-113`、`headless_claude.py:56-64` | 只 SA |
| `session.max_turns` | {critical 80, analytical 40, repair 30, relay 20, default 60} | `headless_claude.py:71-84` | 按 tier 名取值但数值不在 config,可能截断 agent |
| `session.mailbox.{never_taken_factor, wait_s, dead_heartbeats, poll_s}` | 4.0 / 90 / 6 / 5.0 | `executors/mailbox.py:54-58`、`mailbox_cli.py:143-150` | |
| `session.max_attempts` | 2 | `session_agent/runner.py:65` | |
| `shells.{detached_max_rounds, wait_seconds, misses_lost, trace_calls_per_target, tail_lines}` | 40(scan)/20(l4) / 100 / 3 / 2 / 15 | `scan-market.js:126-142,209,99`、`l4-stock.js:264-280,243` | 只 JS;中继壳参数 |
| `shells.retries.{frame, prelude, l3_lint, intel_transient}` | 1 / 1 / 1 / 3 | `scan-market.js:328-341,385-390,510-545`、`l4-stock.js:361-368` | |
| `l4_watch.{stale_min, interval_s, monitor_timeout_ms}` | 30 / 5 / 3600000 | `scan/l4_watch.py:44,313`、`SKILL.md:62` | 第三个今天只写在 SKILL 文档里 |
| `self_review.{coverage_min, winner_rate_max, overheat_pct60, overheat_rsi6, composite_floor, sector_max, liveness_escalate_streak, citation_min, intel_window_days}` | 0.8 / 88 / 50 / 80 / 30 / 0.6 / 3 / 6 / {v1 7, v2 31} | `scan/self_review.py:211-230,983,1328,428-453` | 发布门(GATE4 判据真身);`citation_min` 同时在 `l4-card.md:73` |
| `report.{brief_max_bytes, summary_bytes, appendix_bytes, evidence_max_chars, tone_chars}` | 3000 / 12K–16K / 20K–24K / 80 / 34 | `scan/brief.py:65,264`、`report_model.py:50-59` | 体积预算 |
| `delivery.{bark_body_limit, http_timeout_s, mail_timeout_s}` | 3000 / 15 / 60 | `scan/delivery.py:40-41,91` | |
| `retention.{archive_transcript_agents, lake_window_days, capsule_stale_after_min, salvage_window_min, salvage_max_hours}` | 5 role / 70 / 5 / 20 / 8 | `scan/retention.py:224,282`、`trace/capsule.py:3310`、`salvage.py:106,114` | |
| `execution.{entry_cutoff, expiry_lag_sessions}` | 14:45 / 3 | `scan/exec_anchor.py:47,52` **与** `overseas.py:52`(同一概念两处常量) | 一键喂两处 |
| `overseas.{horizon_days, rows_summary, rows_brief, rows_tripwire}` | 14 / 4 / 1 / 3 | `scan/overseas.py:119,61-63` | |
| `tripwire.date_lead_days` | 3 | `scan/tripwire_watch.py:44` | |
| `dossier.{pool_cap, recent_days, entry_min, init_per_night}` | 30 / 20 / 2 / 3 | `dossier/pool.py:30,45,179`(cap 今天在 coverage_pool.json)、`SKILL.md:110` | 最后一个只在文档里 |
| `observability.{min_ledger_n, min_session_n, winner_decile, expected_abs_gap_min_n, swing_readout_min_days, swing_readout_min_clusters, menu_knife_tolerance, price_max_daily_move, unknown_rate_tolerance, nan_warn}` | 20 / 20 / 0.9 / 20 / 40 / 10 / 0.06 / 32 / 0.05 / 0.30 | `outcome.py:1497`、`ledger_views.py:138`、`populations.py:157`、`relative_buy.py:200`、`swing_seat.py:39-47`、`buyability.py:97`、`price_claims.py:541,622`、`health.py:86` | 读数样本门与 lint 阈;**低价值,可后置** —— **Q9** |
| `budgets.maturity` | {phase1 cost −15%/P50 ≤75/P90 ≤100, phase2 −25%/65/90} | `scan/budget.py:307-324` | 成熟门目标 |

### 4.C 不建议进 config(各一句理由)

| 项 | 理由 |
|---|---|
| `common.ruler.MAIN_RULER` / `SWING_RULER` | 账本口径;改它历史行不可比;用户裁定主尺不换 —— **Q11**(要不要只读回显) |
| `$CTX/factor_lab/weights.json` | factor_lab 校准产物,不是手调 |
| `BARK_TOKEN` / `DELIVERY_MAIL_TO` | 凭证只在 .env(现契约) |
| L1 各路信号定义(`common/uzi_lenses.py` 等) | 代码即定义;参数化 = 重写召回层 |
| `l2_stratify` 的 `regime/regime_caps`、`temperature_v2_guard` | 未接线特性,不是参数;有探针测试守着 |
| `intel_gate.STOP_*` 预注册停机规则 | 离线回放的预注册判据,改 = 重新立案 |
| agent .md 里的纯文案数字(字数、行数、格式) | 契约文本,不是运行参数 |
| `_BASE_RATE_THIN_N`、`pick_opportunity_candidates(k)` | 未使用,该删不该配 |

### 4.D agent prompt 里的数字怎么办

清点到 `l3-rank.md` / `l4-card.md` / `l4-intel.md` / `sector-brief.md` / `macro-brief.md` 五个契约文件里约 60 处数字,今天只有三处来自 config(finalist 上限 `l3cap`、intel `max_queries`、lowturn 图例)。**原则**:只处理**有代码孪生**的数字(评级映射、force_full、执行线、引用 ≥6、slim 阈、pass1 行数、finalist 区间、intel 衰减窗),它们必须单源,否则代码和 prompt 各说各的;做法是派发时在任务包 / prompt 前置一个「本次参数」块(值来自 config),对应 .md 句子改成「以任务包参数为准」。纯文案数字留在 .md。

**顺带要修的三处矛盾**:`sector-brief.md:3,12`「≤2 有界 WebSearch」vs 两宿主派发文案「零新取数」;`l4-card.md:26`「slim >8KB 才可信」vs 真门 4096B;`l3-rank.md:3,9`「~60 只」vs config 40。—— **Q5**

---

## 5. 问④:分类方案

| 方案 | 结构 | 代价 | 评价 |
|---|---|---|---|
| **A 现状延伸** | 平铺,按漏斗阶段排,新键各归各块,新增 sentinel / signals / runner / readiness / session / shells / self_review / report / execution / overseas / tripwire / dossier / observability 块 | 零迁移 | 24 个块一屏扫不完;运行类和行为类混排,「改了会不会变选股」看不出来 |
| **B 两级嵌套** | `behavior:{l0,funnel,l2,…}` + `runtime:{budgets,agents,…}` | 白名单从平铺改两级;`funnel` 一词 27+40 处、`l4_intel` 31+45 处全改路径;26 个测试文件 | 语义最清楚,但是一次纯搬家的大重构,风险与收益不成比例 |
| **C 平铺 + 两分区(推荐)** | 键名与块名一个不改;文件分两个分区,分区一「漏斗行为」按阶段排:pinned → signals → l0 → funnel → l2 → sector → l3 → l4 → l4_intel → relative_buy → execution → calendar → sentinel;分区二「运行时」:agents → agent_engines → budgets → prelude → runner → readiness → session → shells → l4_tasks → l4_watch → self_review → report → delivery → retention → overseas → tripwire → dossier → observability | 只动文件顺序与注释;白名单只加不改 | 「改这个会不会变选股」= 看它在哪个分区;现有 4 条生产值测试、26 个读文件的测试零改动 |

**C 的两个可选小改(各自独立,可不做)**:① `performance.streaming_l4` → `runner.streaming_l4`(12 处引用;`performance` 块只剩这一键且仅 JS 吃);② `agents` + `agent_engines` 合成 `agents.{roles, engines}`(动 `resolve_agent_bundle`、`test_user_config_roles.py`、Codex toml 锁测试)。—— **Q2**

**C 下 `calendar` 的位置**:它既是 prelude 步(取数)又是 E6 第五门(`rebalance_gate` 在 relative_buy 块)的事实源,归行为分区、排在 relative_buy 后。

---

## 6. 分期(依赖在前;P0/P1 是 bounded,P2–P4 各自一波)

| 期 | 内容 | 类型 | 冻结窗 |
|---|---|---|---|
| **P0 修生效病(不加行为键)** | §2 全部处置:pinned 分裂脑 / web 帽删 / l4_stock·max_queries·pass1 缺省统一 / SA 宿主接 reuse_ttl 与 caps / max_briefs 真上限 / activate_date 删 / 两暗键写入 / skip_when_dead 注释诚实 / 四处生效点改正 / dossier-init.js 空 config 守卫;每条一个变异探针(改值 → 消费点读到 → 还原) | bounded | 只有 max_briefs 真上限是行为小变,可标默认保留旧行为 |
| **P1 注释精简 + 重排分区** | §3 格式 + §5 方案 C;flow-handbook 7.10 改指针;`tests/scan/test_scan_config_live.py` 类的生产值测试不受影响 | bounded | 纯文本 |
| **P2 行为类扩容** | §4.A 按块分批,每批三件套 + 两宿主;推荐顺序:l3.guards → sentinel + l4.budget → l4.rubric/force_full → l4_intel → relative_buy(+ params 哈希)→ signals → l2.sector_seats/sector → calendar/producers/brief | 每块一波 | 全部默认 = 现值 |
| **P3 运行类扩容** | §4.B;runner/readiness/session/shells 优先(无人值守场最常调),observability 最后或不做 | 每块一波 | 不涉及 |
| **P4 prompt 参数块** | §4.D:任务包前置参数块 + 五个 .md 改引用 + 三处矛盾 | 一波 | 文案对齐不改行为 |

---

## 7. 待裁(Q1–Q11;每条给推荐与不裁的后果)

| # | 问题 | 推荐 | 不裁的后果 |
|---|---|---|---|
| Q1 | 分类方案 A / B / C? | **C** | 按 A 做,新块一多就回到今天「扫不完」 |
| Q2 | C 的两个小改:`performance.streaming_l4` → `runner.streaming_l4`;`agents`+`agent_engines` 合并? | 第一个做(12 处),第二个不做 | 不裁 = 都不做,零风险 |
| Q3 | 死键/暗键处置:web 两帽删还是接;`skip_when_dead` 留键写「未接线」还是先删;`max_briefs` 改真上限还是加 `healthy_top3_extra` 旋钮 | 删 / 留键写明 / 加旋钮默认 true(逐字 parity) | 不裁 = 按推荐 |
| Q4 | E6 规则进 config 的前提:决策文件加 `rule_params_sha256`,账本按 (rule_version, params_sha) 分组 | 接受 | 不接受 = relative_buy 那 6 组键不做 |
| Q5 | prompt 内数字:只做有代码孪生的(推荐)还是全部参数化 | 只做有孪生的 | 全做 = .md 变模板,违反「agent 文件是契约真身」 |
| Q6 | mailbox 模式 effort 丢失:把五个 .md 的 frontmatter effort 对齐 config 解析值? | 对齐 | 不对齐 = session_v1 mailbox 路永远跑在低一档 |
| Q7 | 两宿主的复核触发 / 复核失败 / intel 失败三处行为差异,借 `l4.review.*`、`l4_intel.on_failure` 顺手统一? | 本波只预留键位,统一另立一波 | 不裁 = 差异继续 |
| Q8 | P2 范围:全部 ≈90 键,还是先做核心 ≈30 键(l3.guards / sentinel / l4.budget / rubric / intel / relative_buy 主门 / signals)? | 先核心 30,其余按块跟进 | 全做 = 一波过大,复审吃不下 |
| Q9 | `observability` 样本门(MIN_N 等)进不进? | 后置到 P3 末,可不做 | — |
| Q10 | config 冻结语义:JS 逐步读活文件、SA 前奏后冻结 → 统一「run 开始即冻结」? | 统一冻结(可复现) | 不裁 = 跑到一半改文件,两宿主结果不同 |
| Q11 | 主尺以只读键回显(`ruler.main`)? | 不进 | — |

---

## 8. 不做什么 / 诚实局限

- 本稿零实施;§4 的行号来自两只 Explore agent 的静态读码,**我只复核了头牌项**(pinned、budgets 路径、web 帽、streaming、max_queries 四缺省、skip_when_dead 零调用、reuse_ttl、max_briefs 溢出、两处 14:45、force_full_card 已接线、席位剔刀无旋钮、budget_flags 空转、sentinel 缺省、E6 常量、card_count 算术),其余实施前逐条复核。
- 未跑测试、未读 `reports_codex/`、未碰 Codex 侧 hooks/toml(P2/P3 每块都要在 Codex 会话再验一次,09-15 裁定)。
- 扩容后约 280 键;「尽可能多」是用户要求,但 §4.B 的 observability 与 shells 两块价值最低,可以直接砍。
- 本稿不改任何行为:冻结窗内所有新键默认 = 现值,`skip_when_dead` 接线仍等冻结窗后。

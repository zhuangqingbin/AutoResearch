# 全覆盖研究系统 brainstorm(2026-08-29):阶段耦合 × 召回整编 × 各阶段研究优化

> **性质**:设计讨论稿,**零实施**(用户原话「brainstorm 不开发只落详细开发 md」)。不是调度权威;与 08-26 全项目 brainstorm(候选池 A–E / Q1–Q8)、08-28 三问 brainstorm(每级一尺 / G0–G6)、08-28 外源扩面设计稿(I 类已落 / B 类冻结)**互补不重复**,只在本稿改变其优先级处引用。
> **用户三问原文**:① 「你是一个资深的系统架构师,各个阶段的设计的耦合等系统情况如何优化得更好」② 「召回策略的整理和优化」③ 「每个阶段的研究是不是有优化点」;总目标「打造一个全面覆盖的研究系统」。
> **证据来源**:本 session 五路只读审计(A 阶段契约与耦合 / B 召回线 / C LLM 角色与流向 / D 数据层覆盖 / E 工作树对三份前稿的落地状态),原件在本次 session scratchpad(`audit_A_coupling.md` … `audit_E_worktree_status.md`,抛弃型),关键 `file:line` 已抄进正文;全量测试 **4551 绿 / 6 skip / 8:47 墙钟**(工作树含 ≈22.8k 行未提交)。凡本稿没有亲手复核的推断标 **UNVERIFIED**。
> **「全面覆盖」在本稿的定义**(先定义再谈优化):覆盖 = **三海拔**(宏观/中观/微观)× **证据面**(价量/资金/基本面/估值/筹码/事件/公告/新闻/卖方/衍生品/海外/日历)× **人口**(不只 finalist,L0 全体到 BUY 每一级都有事后读数)× **时间**(日频 scan + 周频/季频 full 档 + 盘后账本)× **现场**(每个结论可回溯到它读过的字节)。五个维度**今天各缺一块**,§5 的矩阵逐格标出。
> **2026-08-29 Codex review 增补边界**:本轮只审正文契约与当前工作树,不复读 `context_claude/` / `reports_claude/`,不新增或改写任何历史读数。增补集中在五个容易被「有文件」掩盖的系统属性:**PIT 时间真相、法证事件的可观察性、影子实验晋级纪律、有效覆盖而非打勾覆盖、研究队列背压**。

---

## 0. 边界(既有裁定,不重提)

| 裁定 | 对本稿的约束 |
|---|---|
| 主尺 `gap_c1_o2`(T+1 尾盘买 → T+2 开盘卖),5–10 日窗三次裁不换 | 所有 KPI 在主尺内;5–10 日尺只出现在拒绝价值 / 避雷 / L3 形状 |
| learning 层整体退役且真删(08-21);L4 复用不恢复(07-29);不设收编官 agent | 本稿不重开任何「从历史学 → 回注 prompt」;所有账本**只记不学** |
| `scan_config.jsonc` 唯一参数事实源;双引擎隔离只共享 `lake/` | 新参数三件套;新根一律经 `workspace.py` |
| **B 类改动冻结到 09-中攒 20 个结果日**(08-26 A0;`tests/scan/test_frozen_b_class_boundary.py` 六条守卫) | 本稿把每条建议标 **M / I / B**;冻结窗内只做 M/I —— **架构重构恰好全是 I 类,冻结窗就是做它的窗口** |
| 08-24 衍生品普查三族零证据;08-28 隔夜集中信号普查 **H0 未被推翻**(报告能用的族全负逐年同号,收益随可成交性单调递减);08-29 海外事件窗普查四主族零正证据 | 召回整编**不做 alpha 主张**;外源/期权只做风险可见性 |
| 防锚定三层同律:策略师 §1–3 描述性 / 行业 brief 只有地形段 / 个股评级只由 L4 rubric 三门决定 | 任何新注入只能是数字地形与事实日历 |
| 复盘/反馈流程不得改 `.claude/**`(08-13) | 本稿对 agent def 的改动全是人批设计稿改动 |
| 08-28 法证 capsule 设计:三个结论互不替代;`MANIFEST` 通过 ≠ 完整;真实生产验收未跑 | 本稿对 trace 层的结构建议**不动**三结论语义,只动「证据由谁写」 |

---

## 1. 一页现状(全部是本次量到的数)

### 1.1 体量与分层

| 包 | 行数(含未提交) | 角色 | 备注 |
|---|---:|---|---|
| `scan/`(含 agents/l3/l4/recall) | ≈30.2k | 阶段逻辑 + 报告 + 账本 + 视图 | 58 个顶层模块,≥60 个 `__main__` 入口;最大 `self_review` 1785 / `l4_tasks` 1668 / `report_sections` 1388 / `populations` 1233(新) |
| `trace/` | ≈13.0k | 法证 capsule | **全仓最大的单一子系统**(`capsule.py` 3247 + `identity.py` 2457);它反向 import `scan.artifacts/run_contract/run_bootstrap/run_profile/user_config`(`trace/capsule.py:23-24,443,2335`) |
| `research/` | ≈8.3k | 离线仪器 | 与 scan 互相 import(`scan/populations.py:94`、`outcome.py:218` ← research;`research/edge_census.py:193,207` → scan) |
| `data/` + `dataflows/` | ≈9.1k | 湖 / 契约 / 源 | 63 个登记端点;**生产帧 8/9 条 tushare 取数绕过湖**(§1.3) |
| `news/` 2.3k · `dossier/` 1.8k · `analyze/` 2.3k · `macro/` 1.4k · `sector/` 0.7k · `derivatives/` 0.9k · `common/` 2.1k | | | `news/` 无生产写者;`analyze/macro/sector` 三家 harvest 各自取数各自渲染 |
| `.claude/workflows/*.js` | 1185 | **编排真身** | `scan-market.js` 619 + `l4-stock.js` 492 + `dossier-init.js` 74 |
| 全仓 python(含 research、未提交) | ≈73k | | 测试 4551 绿,8:47(08-26 为 2974 绿 4:17) |

### 1.2 四把阶段尺首读(`reports_claude/scan/_ledger/views/stage_rulers.csv`,G3 已落地,pooled `ALL` 行)

| 级 | KPI(08-28 三问稿 §1.4 定义) | 读数 | 一句话 |
|---|---|---|---|
| L1 | Recall@1000(事后 top-decile 赢家 ∧ buyable_c1) | **0.254**(43 日,17,994 赢家名;CI 0.241–0.267);**lift 1.047**(CI 0.994–1.101) | 召回赢家的比例≈随机 1000/4300 |
| L2 | 召回保持率(L1 赢家活到 L2) | 0.194(42 日);**lift 0.959**(CI 0.893–1.022) | 略低于随机 200/1000 |
| L3 | finalist − bench(同日配对) | **−0.006pp**(33 日,CI ±0.25) | 无区分力 |
| L4 | 拒绝价值(被否决 − 可比满卡) | −0.16pp(12 日,CI −0.83…+0.37) | 样本太薄 |
| E6 / 执行线 / 整链 | | UNAVAILABLE / PENDING | 账本未成熟 |

> 这是**第一次**用「这一级做的事」来量 L1/L2。结论比 08-22「判断层显著负」更根本:**召回线不是在召回,它是一台按 composite 重排的机器**——composite 同时是通道(400/100)、`quota_union` 的平手裁决(`recall/merge.py:47-48`)、L2 的 sector-neutral 排序、pass1 每条队列的排序键、共振 top-5 的排序、席位⑨的挑选、E6 池的排序;一个标定到 **2026-08-05** 面板的分数贯穿全链。

### 1.3 三类「接线断裂」(FN-1 家族,本次新逮到的)

| 类 | 事实 | 后果 |
|---|---|---|
| **数据契约不在生产路径上** | `frame.build_market_frame` → `tushare_source.fetch_universe_tushare`(`tushare_source.py:354-454`)**裸 `pro.X()`** 拉 `daily_basic / daily×3 / stock_basic / moneyflow / margin_detail / stk_factor_pro / cyq_perf / hk_hold`;只有 60 日 `daily` 面板走湖(`frame.py:93`)。A 级契约(`contracts.py:113-120`)**只在 prewarm / backfill / doctor 里跑过** | ① `north`/`rz` 两个 composite 组自 07-13 起**每次扫描非空率 0.0**(`L1_scored_full.csv` 07-13/07-29/08-13/08-21/08-26),`margin_detail` 失败只 `print`(`tushare_source.py:349-351`)零记账;② 08-26 与 07-29 `chip`/`tech` 组非空率 **0.547**(21:xx 的 tushare 半载快照),`check_market_frame`(`contracts.py:374-376`)不覆盖这些列 → composite 静默重归一;③ 08-28 普查回填对同样的 `hk_hold 20260825/26/27` 拿到 958 行 → 生产的「空返回」是 T 晚取数撞上发布滞后,不是真空(滞后时长 UNVERIFIED) |
| **建成未接线** | `trace/evidence_index.py`、`trace/web_budget.py`(D-5)在 `autoresearch/` 内**零调用者**;`self_review.intel_query_cap_lint(web_budget_path=…)` 生产从不传 path;`data/endpoints.freshness_state`(时效契约 v2)只有测试消费;`cboe_vix` 无读者;`stage_rulers.csv` 只有 prelude 内 `contextlib.suppress` 里的一个生产者,`nightly_close.sh` 跑的 `populations build` 不写它 | 设计稿状态行「D-5 已实施」**言过其实**:下一次真跑不会有 `web_budget.json` / `external_evidence_index.json`;没扫描的日子阶段尺不更新 |
| **消费者无生产者 / 生产者无消费者** | `verify.csv`(读者 `decision_finalize.py:46`、`health.py:34`、`dossier.py:53`、`report_sections.py:102-115`,写者 0);`_l3_calibration.md`(`l3-rank.md:16` 写成「硬约束」,产者 0);`rubric_rating`(`l4/rubric.py:18-46`)**生产零调用**——评级是 agent 自己算的算术被 parse 出来;`stratified_l2(regime, regime_caps)` 无生产调用;`sector_momentum` 通道的唯一消费者 08-21 删了 | 每一处都是「文档/代码说有一道门,现场没有」 |

### 1.4 一个新出现的成本形状(capsule 合并后未量过)

`l4-stock.js` 里**每一条**确定性壳(`bash` / `gpJson` / `taskGate` / `recordL4`)都被 `tracedAgent` 包住(`l4-stock.js:174-245`):派发前起一个 `gp_shell_json` 壳跑 `capsule agent-event AGENT_DISPATCHED`,完成后再起一个跑 `AGENT_COMPLETED`——**每条命令 = 3 次 agent spawn,且「壳的边界」由另外两个壳来记**。`scan-market.js` 只包业务 agent(`emitBoundary`,`:215-234`),每个业务 agent +2 壳。按现行 JS 数:`scan-market.js` 一趟 ≈30–34 壳,`l4-stock.js` 每股 happy path **22** 壳(+5/+7 若复核),10 只 finalist ≈ **250–320 壳/趟**。08-25 实测 47 壳 $5.90(≈$0.125/壳,成本与命令内容几乎无关,是每次 spawn 重付的前缀 cache 写)→ 线性外推 **$31–40/日 UNVERIFIED**——若成立,壳将超过 L4 卡成为第一大成本项。设计稿自己写着「确定性 relay 的证据是被捕获的命令而不是 transcript」(`run_profile.py:25-26`),用两个壳去记一个壳的边界,与它自己的原则相悖。**这是 P0**(§6)。

---

## 2. 第一问:架构 —— 耦合诊断与目标形态

### 2.1 为什么会长成这样(史,一段)

06-22 架构稿承诺的核心抽象是 `Stage(inputs/outputs/run(ctx))` + `pipeline.py` + `RunContext` + typed `trace/schema.py`,「段间只经 trace 产物通信」。它被**平行**实现了(`scan/stages/`、`pipeline.py`、`parity.py`、`trace/store.py`),但生产真身一直是 `prelude → universe.run` 直调,该簇零生产调用,于是在 `174ffd7` 作为死代码整簇删除(≈921 行源码 + 756 行测试)。此后编排真身落到 **JS workflow + SKILL.md 散文 + ≥60 个 python CLI** 三张皮上,阶段契约退化成「约定文件名 + 约定 markdown 标题」;08-28 法证 capsule 因为找不到一个可以问「这趟该有什么」的 pipeline 对象,只好从**外面**再声明一遍(`run_profile.ArtifactRule` + `completeness.build_expected`)。**本稿的第一个判断:不要再造一个 pipeline 运行器**——运行器就是 harness(subagent 由 Claude Code 派发,python 永远拿不到那个句柄),再造一次会以同样的方式死掉;python 能拥有、也必须拥有的是**契约**。

### 2.2 耦合的九种形态(每种附本次量到的证据)

| 形态 | 证据(A/C/D 审计) | 伤在哪 |
|---|---|---|
| **K1** 文件名字面契约横跨三种语言 | `finalists.csv` 出现在 **43** 个生产文件(40 py / 3 md,只有 `artifacts.read_finalists` 做 zfill,其余 39 处各自 `read_csv`);`L2_gbdt_top200.csv` 33;`_ledger` 33;`summary.md` 19;`decision_records.json` 18;`_l4_tasks.json` 15;`market_view.md` 15。全仓只有 4 个名字有常量;`artifacts.CRITICAL_ARTIFACTS`(`artifacts.py:35-73`)把 21 个名字又写了一遍,**没人 import 它的 path** | 改一个名字要改 40 个文件;JS 与 python 各拼各的路径(`scan-market.js:74-75` vs `workspace.py:105-107`) |
| **K2** markdown / keyed-line 契约:agent 说,python 猜 | `**Rating**` 的解析器是泛正则 `rating.*?[:\-]` 且**兜底取全文第一个评级词**(`rating.py:27,41-45`);`market_view.md` 六节有**两个**解析器(`report_sections.py:876` / `brief.py:199`);`_l3_judged.json` 15 个键只在 def 散文里(`l3-rank.md:40-41`),python 只校 5 个;`[执行线]` 阈值在 playbook 散文与 `outcome.EXEC_MAX_*` 各写一份,**无测试对齐**;评级序表 JS `RANK{sell:0…buy:4}`(`l4-stock.js:430`)与 python `RATINGS_5_TIER` Buy=0…Sell=4(`rating.py:17`)**方向相反**;conviction 只在 JS 归一(`l4-stock.js:415`) | 指令级约束失败率非零、数据级为零(项目已记多次);这里是全仓最大的指令级面 |
| **K3** 五份「该有什么」登记表,互不派生 | ① `prelude.STEP_NAMES`(12 步)② `artifacts.CRITICAL_ARTIFACTS`(21 项)③ `run_profile.SCAN_STAGES/ROLE_STAGES/_BASE_RULES` → `completeness.build_expected` 展开成第三套词汇 ④ `health._ARTIFACTS`(含无产者 `verify.csv`)/ `brief.py:81-93` 白名单 / `publisher.py:247,439` / `replay.default_stage_specs`(第四套阶段词汇 `l0,l1,l2,l5`)⑤ JS 阶段串 `'l4-prep'`、`'finalize'`(`SCAN_STAGES` 没有)。**具体打架**:`run_profile.MODES` 3 个 vs `run_mode.MODES` 4 个(多 `SENTINEL_PINNED`);`capsule.finalize` 调 `scan_profile(business_status, last_stage)` **从不传 mode**(`capsule.py:2383-2386`),哨兵趟的 `l4-card/l4-intel` 会被标 REQUIRED;`replay.py:180` 跑 `python -m autoresearch.scan.l2_stratify`——**该模块不存在**(真身 `scan/recall/l2_stratify.py`,无 `main`),测试注入假 runner 所以从未红 | 「完整性」结论的分母本身有四个版本 |
| **K4** 日期键共享 staging 与 run 分区并存 | 分区由**进程环境变量**决定(`workspace.py:105-107,121-122`);`$CTX/sector/<date>/`(`sector/pack.py:34`)、`$CTX/analyze/<code>_<date>/`(slim)、`$CTX/macro/`、`$CTX/news_catalog` **从不分区**;三个跨日读者遍历 `scan_root()` 的兄弟目录——`sector/reuse.py:57`(TTL 复用)、`l3/prompt.py:128-135`(Δ 模式)、`menu.py:92-99`(0 买连败)——在 run 分区下**只看得到本 run 的日期**(运行时效果 UNVERIFIED,但形状就是回归);`SKILL.md:169-174` 让 `usage_harvest` 写 `$CTX/scan/<date>/_token_usage.json` 而 `post_run` 读 `ws.scan_dir(date)`(run 分区)——**两处不同路径**;19 处散文仍教日期键路径 | capsule 波把「同日重跑互相覆盖」修好的同时,把三个跨日读者悄悄弄瞎 |
| **K5** 配置三路装载 + 6 次整包序列化 | `knob()` 8 文件;`load_user_config().get(...)` 直读 10 处(`l4_tasks.py:1620`、`self_review.py:864`、`universe.py:283`、`l3/merge.py:606`、`relative_buy.py:846`、`l3/prompt.py:419-422`、`recall/channels.py:65`、`run_bootstrap.py:94,163-174`);冻结回显 `user_config_echo.json` 5 处;`sector/pack.py:463`、`sector/reuse.py:106` **向上** import `scan.user_config`;整份 `args.config` 经主会话序列化 6 次(08-26 B4);传 `{}` 曾静默关 intel | 三个真值入口 + 一个可以传错的 blob |
| **K6** 三个 harvest / pack 家族各取各的数、各渲各的表 | `_ak_call` ×3(`analyze/harvest.py:1291`、`macro/harvest.py:90` 自注「Verbatim from」、`data/akshare_universe.py:29`);`_section` ×2;`_num` ×3;`external_sections`/`readthrough_block` ×2;`analyze/harvest.py` 17 处手写表、17 处各自解析日期;`tushare_source._pro/_ts_call/_trade_days/_code6` 被 **14 个文件、5 个包**私有 import(`common/uzi_lenses.py:258-323`、`macro/tushare_macro.py:23`、`research/*`、`scan/calendar.py:39`、`frame.py:76`、`l3/evidence.py:52`、`l4/producers.py:93-220`、`prewarm.py:35,57`、`temperature.py:223`) | 全仓私有符号跨包 import **78 处**;`trade_cal` 在 ≥9 个模块里裸调 → 08-28 一次 DNS 瞬断杀掉整晚预热 |
| **K7** JS ↔ python 双实现,壳 agent 当控制流 | model/effort 解析(JS `AGENT_DEFAULTS+AG()` vs `user_config.resolve_agent_config` vs `usage_reconcile.py:84` 第三份)、ensemble 中位/折回(`l4-stock.js:449-475` vs `decision_finalize.py:107-119`,JS 注释说 python 权威但 JS 先算一遍并把结果写进 `_ensemble_<code>.json` 的 heredoc 字面量 `:461-469`)、瞬时错误分类、任务动作枚举 `SKIP/RUN/BLOCKED/WAIT/LEGACY`、派发分批(`scan-market.js:527,548` vs `l4_tasks.dispatch_batches`)、pack 有效性门(JS 内联 `python -c json.load`)、行业待写清单(JS 内联 glob);`dispatch-plan` 的 snake→camel 翻译**住在 SKILL 散文里**(`SKILL.md:154`) | 每个概念两份真身;§1.4 的壳翻倍是同一病的成本面 |
| **K8** 跨海拔端口各有各的鲜度规则 | `macro_state` 7 天 + regime 同(`macro/state.py:226-238`);行业 brief TTL 5 日 + 动量位移(`sector/reuse.py:9-12`);档案 90 日只告警;consensus ≥10 缓存日;情报时效窗 **v1**(T0/24h/背景)与 **v2_full / v2_macro** 并存,v2 的 lint **生产从不调用**(`report_sections.py:803,1009` 恒 v1);sector full / stock full / `_company_intel` / `_us_intel` / `_sector_intel` **零读者**;`global-intel` def **没有任何派发文本**(macro-playbook/SKILL 都没提);策略师 §4「操作基调」只到 appendix(`report_sections.py:879` 注释说行动节消费,`:1275-1289` 并没有) | 「全覆盖」的三海拔今天是**单向、无鲜度、多数死胡同** |
| **K9** 依赖方向倒挂 | `trace → scan`(8 行)、`scan → trace`(15 行)成环;`scan ⇄ research`、`scan ⇄ dossier`(自注「防环」的惰性 import)、`scan ⇄ sector`、`common ⇄ data`、`data → trace`(`data/cache.py:66,279`)、`derivatives → scan`;第二写者:`run_health.json` **5 处**写(`publisher.py:319,408,469,510` + post_run)、`summary.md` 3 写者、发布后的 `details/<code>.md` 被原地改 3–4 次(`publisher.py:159-206`)、`_final_ratings.json`/`decision_records.json` 由**渲染器**写出(`report_sections.py:1141-1142`) | 观察层依赖被观察者;渲染器拥有决策事实 |

### 2.3 三条路(裁决权在用户)

| 路 | 一句话 | 代价 | 风险 |
|---|---|---|---|
| **路 A · 契约优先,运行器不换**(**推荐**) | 把「契约」从散文和字面量里**抽出来**成为 python 单一事实源(产物登记表 / agent 产出语法 / 端口 / 分层规则),JS 与 def 只消费**生成物**;运行器仍是 harness;确定性子步骤在进程内串行 | 全是 I/M 类,可在冻结窗内分批做,每批 byte-parity 验收 | 需要纪律:新产物**必须**先登记(测试逼) |
| 路 B · 重建 pipeline 对象(06-22 路线) | `Stage` ABC + `RunContext` + python 内运行器 | 大 | **已经死过一次**:LLM 段由 harness 派发,python 运行器天然是平行实现,零生产调用 → 再删 |
| 路 C · 只修点 | 修 §1.3/§1.4 的断裂与 P0,不动结构 | 小 | K1–K9 原样;下一波(例如 broker 接线、B 类解冻)再各造一份登记表 |

**推荐路 A 的理由只有一条**:仓里已经有了它的所有半成品——`artifacts.CRITICAL_ARTIFACTS`、`run_profile.ArtifactRule`、`stage_result`、`strategist_pack.ALLOWED_KEYS`、`sector/brief.TERRAIN_HDR`、`l4/parsers` 的正则、`macro/state` 的鲜度门、capsule 的 `replay`(它就是 06-22 想要的 golden 对拍器)。路 A 是把这些**已存在的碎片提升为唯一真身并让其余派生**,不是新建。

### 2.4 路 A 的七个构件(每件:现状 → 目标 → 落点 → 验收 → 回滚;类别全是 I/M)

**A1 · 产物登记表 `autoresearch/contracts/artifacts.py`(单一事实源)**
- 现状:§2.2 K1/K3。
- 目标:一张表 `Artifact(name, path, root, stage, producer, kind, schema_id, schema_version, compatibility, presence, required_when, replayable)`;一套阶段词汇 `STAGES = (frame, prelude, gate1, sector, l3, gate2, l4_prep, l4, l5, observe, gate4, finalize)`(JS 串的超集);`MODES` 只有一份(并入 `run_mode.MODES` 的 4 个)。**派生**:`run_profile.SCAN_STAGES/_BASE_RULES`、`completeness.build_expected`、`health._ARTIFACTS`、`brief` 白名单、`publisher` 两张 trace 映射表、`replay.default_stage_specs`(`replayable=True` 的产物 → 修掉死 argv)、`artifacts.CRITICAL_ARTIFACTS`(退成别名)。`prelude.STEP_NAMES` 保留(步 ≠ 产物),但每步声明 `outputs=[artifact names]`。schema 默认只保证当前版与上一版读兼容;破坏性升级必须新 `schema_id` 或显式迁移,不能靠 parser 猜。
- JS:`python -m autoresearch.contracts emit-js` 生成 `.claude/workflows/_contracts.generated.js`(`ARTIFACTS / STAGES / TASK_ACTIONS / RATING_ORDER / TRANSIENT_ERRORS`),两个 workflow 只从它取名;`capsule.finalize` 把 `run_mode.json` 的 mode 传进 `scan_profile`。
- 验收:`tests/scan/test_contracts_registry.py` —— ① 每张派生表与今日硬编码表**逐项相等**(parity);② 全仓生产 py/js 里出现的产物字面量集合 ⊆ 登记表(drift 守卫,新名字不登记就红);③ 生成的 JS 与登记表 hash 一致;④ 哨兵趟 `completeness` 不再要求 `l4-card`。
- 边界:**单一事实源不等于单一巨文件**。`artifacts / agent_output / ports / vocabulary` 分表、同包、单向依赖;这里只放声明和纯校验,不得反向 import `scan` 或塞入业务分支,否则 `contracts/` 会成为新的 `common/`。
- 回滚:登记表只是「多一份真身」,派生失败可逐表切回硬编码。

**A2 · agent 产出语法 `autoresearch/contracts/agent_output.py`**
- 现状:§2.2 K2。
- 目标:`OutputContract(role, fields=[Field(key, pattern, required, parser)])`,一个 `parse_keyed()`;`**Rating**`、`FINAL TRANSACTION PROPOSAL`、`**一行多空**`、`**早停**:停于 Px｜停因:`、`OW三门`、`进入P4倾向:`、`[执行线]`、`## 地形段`、market_view 六节头、intel 声明行与表头、`_l3_judged` 15 键——全部在这里各**一份**;`rating.py` 的兜底「全文第一个评级词」改为显式 `strict=True/False` 两档,scan 用 strict;评级序表 `RATINGS_5_TIER` 单源并生成 JS `RANK`;情报契约 `IntelContract(version ∈ {v1, v2_full, v2_macro}, windows, caps, faces)` 单源,生产 lint 按稿头声明的版本选契约(修掉「恒 v1」)。
- def 侧:每个 agent def 的「机器契约」段由 `emit-def-block` **生成**,`tests/test_agent_defs.py` 的锚从「手写字面串」改为「def 包含生成块」——**它第一次有真值源**(08-26 A6 逮到 l3-rank 锚集无真值源)。
- 验收:所有解析器测试改走 `OutputContract`;JS `CARD/INTEL` schema 由同源生成;变异探针「删掉 def 里的一个键 → 测试红」。
- 回滚:旧正则保留一轮作对照断言。

**A3 · 证据包层 `autoresearch/evidence/`(三海拔共用的取数与渲染)**
- 现状:§2.2 K6 + §1.3 第一行。
- 目标:证据包拆成两层,不把 IO 冒充纯函数:`load_<pack>(request, reader: EvidenceReader) -> RawInputs` 负责湖/源读取;`build_<pack>(raw, decision_cutoff) -> Pack` 才是可重放的纯转换。`PackMeta(schema_version, data_as_of, available_at, received_at, decision_cutoff, sources=[(endpoint,key,hash)], degradations, freshness)` 是所有海拔的统一时间信封;任何 `available_at > decision_cutoff` 的输入不得进入正文,只能落 `late_evidence` 供盘后审计。md 渲染仍是 `render_md(pack, profile)`。包族:`frame_pack`(L0 帧,**改走 `cache.get_or_fetch`** 让 A 级契约回到生产路径;加**半载快照守卫**:行数 < `stock_basic` 的 95% 或任一 A 级列非空率 < 90% → A 级抛、B 级 `record_degradation`;`stock_basic` static 键**每周刷新**——现湖副本 06-22 起没动过;`trade_cal` 入湖为 static 并三源兜底)、`market_pack/strategist_pack`(现有,搬家)、`sector_pack`(现有,搬家)、`stock_pack`(`analyze/harvest` 的 slim/full = 同一构建器两个 profile,块 = 函数)、`macro_pack`、`global_tape`、`calendar_pack`(外源稿 §2 统一事件契约)、`consensus_pack`、`news_pack`。公共件下沉 `data/`:`ak_call / section / num / pct / md_table / as_of` + `tushare_source` **公开门面** `pro() / ts_call() / trade_days() / code6()`(结束 14 文件私有 import)。
- 验收:三家 harvest 的 md 对冻结日期 **byte-identical**(golden = 08-26 capsule 冻结 staging/slim);`frame_pack` 对 08-26 冻结帧 L1/L2 集合与名次一致(capsule `replay` 就是对拍器);新增「半载快照」合成用例(删 45% cyq 行 → 红)与「cutoff 后才发布的数据即使已在湖中也不得入包」红用例。
- 回滚:`analyze/harvest.py` 等保留为薄 shim 调用 `evidence`,一轮后删。

**A4 · 端口与鲜度 `autoresearch/contracts/ports.py`(跨海拔的唯一接口)**
- 现状:§2.2 K8。
- 目标:`Port(name, locate(date), schema_id, accepted_versions, data_as_of, available_at, decision_cutoff, ttl, null_policy, stale_policy, extra_gate, consumers)` 一张表:`market_view / sector_brief / macro_state / global_tape / dossier / consensus / news_catalog / overseas_calendar / intel(v1|v2) / sector_state(新) / stock_state(新)`;一个 `inject_if_usable(port, date, decision_cutoff)` 取代散落的 presence-gate。**存在 ≠ 可注入**:schema 不兼容、真空与请求失败未区分、`available_at` 越过 cutoff、超过 `max_stale` 任一成立都拒绝注入并写 `stale_reason`;允许 stale-on-error 的端口必须把 age 明印给消费者。`ports.health(date)` 在 prelude 汇总屏印**一行**:`端口:✓market_view ✓sector×6(♻2) ✗macro_state(29d) ✗global_tape(never) ✗consensus(0行×4日)`。跨日读者(行业复用 / Δ 模式 / 0 买连败)改为 **`RPT` 侧的「上一发布日」解析器**(读 G2 `runs.csv` 的 `selected_run_id`),不再遍历 `scan_root()` 兄弟目录(修 K4 回归)。
- 验收:三条跨日读者在 run 分区下与旧行为逐日一致(对 61 个已发布 run 回放);prelude 行对 `macro_state` 过期、`global_tape` 缺席各有一条合成红用例;端口再加「N−1 schema 可读 / N−2 拒绝」「源成功真空 / 请求失败不同状态」「cutoff 后到达不注入」三组契约测试。
- 回滚:端口表只是读侧包装,逐端口切回。

**A5 · 确定性子步骤串行器 `scan/stage_runner.py` + 边界事件进程内发出**
- 现状:§1.4;`run_profile.py:25-26` 已写「relay 的证据是命令,不是 transcript」。
- 目标:`python -m autoresearch.scan.stage <stage> --run-id` 在**一个进程**内按登记表顺序执行该阶段的确定性子步骤(调用的就是 prelude / l4_tasks / gates 今天调用的同一批函数——**不是平行实现**),每步经 `exec_capture` 的进程内 API 留 argv-等价 / stdout / rc / 信号;JS 每阶段只起 **1** 个壳:`Prelude(frame+prelude+gate1+run_mode)`、`Sector-prep`、`L3-prep`、`L3-post(lint→[repair agent]→apply→gate2)`、`L4-prep(pledge+prompts+init+plan)`,每股 `preflight+prepare` / `intel-guard+status` / `record` 三壳。确定性步骤只记录自己能观察到的事实:前一步写 `AGENT_DISPATCH_INTENT(attempt_id, role, prompt_hash)`,后一步在确实读到产物后写 `AGENT_OUTPUT_OBSERVED(attempt_id, output_hash)`;若 transcript adapter 能看到真实工具边界,再由 adapter 写 `AGENT_DISPATCHED / COMPLETED`。**不得由相邻步骤代写未观察到的 dispatched/completed**,否则省了壳却把法证现场写假。`tracedAgent / emitBoundary / validate*Ack` 可删,壳数:10 只 finalist 从 ≈250–320 → **≈40 + 业务 agent**。
- 边界:这**改变 capsule 的证据形状**,三结论语义不变;事件采用 at-least-once + 幂等归并,每次重试新 `attempt_id`,同一 attempt 的重复事件按 `(run_id, role, ticker, attempt_id, event_type)` 去重。不能再要求「每个业务 agent 恰一对」:崩溃在 intent 后、产物观察前时,缺 output 正是完整性应报告的现场。完整性期望表由 A1 派生所以同步。**需用户裁(Q2)**。
- 验收:capsule `verify` 对 happy path / intent 后崩溃 / 重试成功 / 重复事件四条合成趟分别给出预期的 integrity/completeness/replay 结论;reducer 只选最后一个契约有效 attempt 的产物;`usage_reconcile` 的壳计数断言改为「≤ 阶段数×1 + 每股 3」。
- 回滚:保留 `tracedAgent` 一轮在 `performance.trace_control_shells=true` 开关后面(默认 false)。

**A6 · run 分区唯一可写根 + 配置按引用**
- 现状:§2.2 K4/K5。
- 目标:`ws.stage_dir(kind ∈ {scan, sector, analyze, macro, news}, date)` 在 run 活跃时一律落 `scan_runs/<run_id>/staging/<date>/<kind>/`;日期键 `$CTX/scan/<date>/` 退为**发布后只读镜像**(或不再产生,Q3);`args.config` → `{run_id, engine, cfg_hash, resolved_agents}`(≤1KB),每股 `cfg` 同;python 从 run 内 `user_config_echo.json` 解析,JS 校验 hash 不符即 throw(比「空 config throw」更早);SKILL/def 里 19 处日期键路径由 `emit-doc-snippet` 生成 + doc-lint 锁。
- 验收:`tests/common/test_workspace.py` 的裸根守卫扩到 `.md/.js`;主会话 token 计量在下次真跑对比 08-25 的 `$5.35`(预期下降,量级 UNVERIFIED)。
- 回滚:`cfg_hash` 缺失时回退整包(一轮兼容)。

**A7 · 依赖方向修复 + 分层测试**
- 现状:§2.2 K9。
- 目标:`trace` 只 import `contracts`(把 `artifacts/run_contract/run_bootstrap/run_profile/user_config` 里被 trace 用到的**声明部分**搬进 `contracts/`);`research ↔ scan` 共用件(`forward_returns / _board_limit / EXEC_MAX_* / entry_tradable`)下沉 `common/rulers.py`;`dossier/sector/derivatives → scan` 改经 `contracts/common`;`common/uzi_lenses` 搬 `evidence/`;`_final_ratings/decision_records` 的写出从渲染器搬到 `decision_finalize` CLI 步;`run_health.json`、`summary.md` 单写者(经 `ReportModel`,08-29 已把 render 做成纯函数,只差把最后两处 rewrite 收口)。
- 验收:`tests/test_layering.py` 用 AST 算 import 矩阵,断言**无向上边**(允许清单显式列出,初始 = 今日矩阵,只许减不许增);第二写者探针:同一 run 每个登记产物恰一个 writer 调用点。
- 回滚:允许清单回填。

### 2.5 目标分层与依赖方向

```
.claude/{workflows,agents,skills}     编排 / 角色 / 操作文档 —— 只读 contracts 的生成物(_contracts.generated.js、def 机器契约块、doc 片段)
autoresearch/contracts                产物登记表 · 阶段/模式词汇 · agent 产出语法 · 端口与鲜度 · 配置 schema(零业务逻辑,零 IO 之外的依赖)
autoresearch/trace                    只观察:exec_capture / events / capsule / completeness / replay —— import contracts,不 import scan
autoresearch/{scan, analyze, sector, macro, dossier}   阶段逻辑(纯函数 + 薄 CLI);彼此不 import,经 contracts.ports 与 evidence 通信
autoresearch/evidence                 证据包(frame / market / sector / stock / macro / global / calendar / consensus / news)
autoresearch/data                     湖 · 契约 · 源 · 公开取数门面(pro/ts_call/trade_days/code6/ak_call)
autoresearch/common                   纯算法(scoring / ruler / rulers / regime / turnup / stats / text / md_table)
research/                             离线仪器 —— 只 import 以上,永不被生产 import
```

三条硬规则(都做成测试):**下层不 import 上层**;**每个登记产物一个 writer**;**JS / def / SKILL 里不得出现未登记的产物名或未生成的契约文本**。

### 2.6 迁移纪律

- 全部 I/M 类;每批的验收是 **byte-parity**:确定性产物用 capsule `replay`(它已经能对 L0–L2/L5 重放到字节)与 08-26 冻结 staging 对拍;LLM 段只做结构校验(契约解析通过率 100%)。
- 顺序服从「先量再砍」:§1.4 的壳成本与 §1.3 的半载快照都**先在下一次真跑里量到数**(Q12),再决定 A5 的开关默认值。
- 冻结窗(至 09-中)内 A1→A7 可分 4 批(§6),**不重置结果日样本钟**——它们不改任何用户可见输出、门、权重、prompt 语义或执行时点;A2 的 def 生成块是**文本重排**,prompt hash 会变,须人批并在 `run_contract.prompt_hashes` 留痕。

---

## 3. 第二问:召回策略整编

### 3.1 首读数(全部本次量到)

- **L1 Recall@1000 = 0.254,lift 1.047(CI 0.994–1.101);L2 keep lift 0.959(CI 0.893–1.022)**(§1.2)。
- 08-26 活体 L1 成员(top-1000 内带该标签的行):composite 400 · value 257 · reversal 147 · momentum 107 · main_fund 106 · growth 86 · heat 78 · lowturn 71 · healthy 63 · reversal_confirm 23;`n_channels` 1:703 / 2:260 / 3:33 / 4:4。**quota 总和 1856 vs `recall_n` 1000;floor 保护 520 行**(`recall/merge.py:35-36,50-52`),其余 ≈480 席按 `(n_channels desc, composite desc)`。
- 08-26 L2(201 行):merit 111 · lane 50(趋势 18 健康 13 成长 7 低位转强 7 反转 5)· backfill 39 · pinned 1;行业帽 0 行触发(≈110 个**东财**行业标签,不是文档写的申万,`tushare_source.py:154`、`sw_sector_map.py:4-5` vs `l2_stratify.py:84`、`scan_config.jsonc:121`)。
- composite 组权重(`weights.json`,mtime 08-19,面板 132 日止 **08-05**,仅 `range` 一块;`trend/risk_off` 在 `regimes_pending`,split-half 不可测):momentum −0.058 / tech −0.089 / volprice −0.067 / chip −0.029 / fund_main −0.025 / value +0.038 / fund_retail +0.012 / north +0.006 / **growth 0(面板无基本面)** / **rz 0**。净效果 = 一个短线反转分(低 RSI、负动量、负 CMF/OBV、行业内便宜)。

### 3.2 诊断(七条,每条附证据)

| # | 诊断 | 证据 |
|---|---|---|
| **R-D1 尺子错位** | 各路 quota 按 36 日「unique 超额」拍板(08-19)——那是**选择尺**;L1 的产品是**覆盖**,从没被按召回量过。value 312 是在旧尺(`fwd_2_oc`,+1.04% rank1)上升的配额,换主尺后它 **−0.07% rank5 符号翻转**(`2026-08-07-ruler-gap-vs-oc-baseline.md` §②) | 配额是用一把不量这件事的尺定的 |
| **R-D2 单分贯穿** | §1.2 注:composite 是通道、平手裁决、L2 排序、pass1 队列排序、共振排序、席位⑨、E6 池排序 | 每一级都是 composite 的重排;「多路召回」的多样性只剩 520 个 floor 席 |
| **R-D3 家族重叠与失活** | 反转族四路:`reversal`(基本面∨当日资金流入,**无价格结构项**,08-26 200/200 满额)、`reversal_confirm`(深跌∧起爆硬门,产出 **23/150**)、`lowturn`(中度低位∧站回,与 rc 共享 `reversal_confirm_score`,交集 2/120)、`accumulation`(退役,但谓词作为 composite **+5 bonus** 活着,`scoring.py:527-536`);`heat` **无门**(每天 50 只最大成交额无条件保护,fwd_10 −5.72 显著负);`healthy` 全尺最差(EC −0.37 t −5.58)却仍 quota 112 / L2 floor 15;`northbound` quota 120 + `north` 组权重保留而 `hk_hold` 数据腿死;`event` 默认关但 `ev_*` 每跑都算(`universe.py:352-354`);rc ↔ reversal 的 A/B **没有裁判**(`channel_ledger` 08-21 删) | 14 路里活着且各干各事的不到 6 路 |
| **R-D4 数据腿死** | `north`/`rz` 组 07-13 起恒 NaN(§1.3);`chip`/`tech` 08-26 非空率 0.547;`growth` 组权重 0;`l0.min_list_days` 需要 `list_days` 列——**无生产者**(`frame.py:40`);rc 第④段 `buyable` 列生产帧没有 → 退化为只剔「退」 | 配置里写着的门有三道在现场不存在 |
| **R-D5 算了不用** | 帧算出并丢弃 `price_vs_vwap_20`(ICIR 0.537,t 6.2,**三门全过**)、`dist_low_60`(ICIR 0.61)、`pct_5d`(ICIR 0.56,标定符号 −1,却只在 lowturn 里当 `>0` 用——**方向相反**)、`breakout_vol_20`(ICIR −1.18)、`macd`(无消费者)(`2026-08-08-factor-regroup-gap.md` §1);`eastmoney_hot_rank`/`stock_hot_follow_xq` 08-09 起每晚入湖,**零读者** | 主尺上唯一过门的一族因子(短反转/筹码)没有排任何一条通道 |
| **R-D6 regime 名存实亡** | `regime_aware=true` 但只有 `range` 块;trend/risk_off 日回落 flat 并记降级(`scoring.py:422-434`);`stratified_l2(regime, regime_caps)` 无人调用 | 07-02 校准开启的 regime-aware 实际每 2 天里 1 天是 flat |
| **R-D7 L0 盲区在尺外** | `missed_l0 ≈ 赢家 9%`(小盘/次新/北交所,`scan_config.jsonc:61`,是否仍为现值 UNVERIFIED);populations 的分母是 `L1_scored_full.csv`(L0 过门 ≈4315)而不是湖全体 | Recall@1000 的分母把最大的一块盲区排除了 |

### 3.3 召回的产品定义与 KPI(先定再改)

- **产品**:把「值得在主尺上被评估的票」以**可交易、可研究、有代表性**的形状装进 1000 → 200 → 40。三个形容词各一把尺:
  - **primary**:`l1_recall_at_1000_lift` 与 `l2_keep_rate_lift`(已在 `stage_rulers.csv`),分母**扩到 decision cutoff 时点可得的湖全体**(A 级端点覆盖的全部非 ST 股),让 L0 硬门的盲区进尺(修 R-D7)。赢家同时报两种、禁止二选一挑好看的:`winner_rank = 主尺 top-decile ∧ buyable_c1`;`winner_econ = winner_rank ∧ net_gap_c1_o2 > 0`(`assumed_cost_bps` 与 `cost_model_version` 随行)。前者量相对覆盖,后者防止深熊日把「跌得较少」叫成可交易赢家;
  - **guardrail 1 · 代表性**:家族/行业/市值/可交易性四维覆盖率(每维的赢家占比 vs 池内占比,KL 或简单比值),防「刷 Recall 就全押一族」;
  - **guardrail 2 · 可交易性**:召回行携带 `tradability` 列(封板概率代理:`pct_1d` 距涨停、一字、`amount` 分位、`turnover` z)——**不作门**(每加一条门 = 一块永久盲区),只作下游与 E6 的可见变量;普查证明收益随可成交性单调递减,召回不能只装买不到的票;
  - **liveness**:`channel_liveness_lint` 现有,扩到「每组 composite 非空率 ≥ 90%」。
- **pass1 归召回**:200 → 40 的 `triage_l2_for_l3` 是第三段确定性召回(L3 真正看到的菜单是 40),搬到 `scan/recall/`,并在 `stage_rulers` 加 `pass1_keep_lift`(populations 已有 `pass1_kept` 旗)。

### 3.4 三条路(裁决权在用户)

| 路 | 内容 | 类 | 何时 |
|---|---|---|---|
| **R-B · 只修死腿**(**现在就做,无争议**) | 帧走湖 + 半载守卫(A3);`hk_hold` 改取 **T−1** 快照(或摘 `north` 组);`margin_detail` 失败记账并修 `rz` 腿;`list_days` 生产者;`accumulation` +5 bonus 明示或摘;`event` 的 `ev_*` 只在通道启用或 L3 catalyst 需要时算;`northbound` quota → 0(通道已关,quota 白占);`recall_channels` 键缺省 = **当前启用的 10 路**而不是全部 14 路(`universe.py:245,404`);文档「12 路」「申万」勘误 | M/I | 冻结窗内 |
| **R-A · 家族化 + 贡献配额**(**推荐,先影子**) | 14 路 → **5 族**(§3.5),每族一个模块、一份带 IC 符号的族分、若干档;quota 由**测得的召回贡献**定(`unique_winner_capture_per_seat`:滚动 40 交易日内「只被该路召回的赢家数 / 该路席位」,populations 已有 `L1_channels.csv` 成员旗);L2 的 `DEFAULT_FLOORS` 从同一族表**派生**(删第二份分类学);`quota_union` 的平手裁决改「族内名次和」而非 composite;pass1 队列按族内名次轮询。**先做 `research/recall_lab.py`**:读冻结 `L1_scored_full.csv`(各路 `score_*` 都持久化了)按备选 quota/族分重算 top-1000 并对 populations 的赢家算反事实 Recall@1000 lift,逐日写影子行 | I(影子)→ **B**(上线) | 影子:冻结窗内;上线:09-中后按影子读数裁 |
| R-C · composite-only + 多样性采样 | 既然 L1 ≈ 随机且 composite 是唯一正家族,砍掉通道,L1 = composite top-1000 + 族 floor 只保多样性 | B | 若 R-A 影子 20 日 lift 仍 ≈1 则退到此路 |

### 3.5 家族化整编表(14 → 5;每族一个模块,档位是参数不是通道)

| 族 | 今日通道 | 整编后 | 门(族内共用) | 排序分 | 备注 |
|---|---|---|---|---|---|
| **复合** | composite | 保留;**退出**全局平手裁决角色 | 无 | composite | 唯一正家族(+0.14pp t 3.05,30/39 日样本内) |
| **延续** | momentum · healthy · heat · (growth 的动量部分) | `continuation`:档 C1 趋势(旧 momentum)、C2 健康延续(旧 healthy = C1 ∧ 资金正,**quota 降到 floor 只保多样性**)、C3 流动性龙头(旧 heat,**加门**:turnover z ≥1 ∧ 非当日 ≥9.5)| `pct_60d>0 ∨ pct_ytd>0` + 档内条件 | `momentum_score` 族内统一 | momentum/healthy/heat 三尺全负或未证,它们在这里的价值是**覆盖上涨侧**(用户 07-17 裁定「上涨板块侧未被否」),不是 alpha |
| **反转** | reversal · reversal_confirm · lowturn · accumulation | `reversal`:档 R0 改善(旧 reversal,**补价格结构项**)、R1 低位画像(lowturn)、R2 起爆确认(rc)、R3 吸筹(accumulation,显式档而非 composite 暗桩)、**R4 短反转(新,影子)** = `pct_5d(−)` + `price_vs_vwap_20` + `dist_low_60` | 低位 ∧ 非退 | `reversal_confirm_score` + R4 子分 | R1/R2 交集 2/120 说明它们是**互补档**不是重复路;R4 是主尺上唯一三门全过的因子族(R-D5) |
| **价值成长** | value · growth | `quality_value`:V1 行业内低估(旧 value,**quota 回到注册表 200 直到贡献配额定**)、G1 成长加速(旧 growth;composite `growth` 组权重 0 需补面板基本面或承认) | `pe>0 ∧ roe>0 ∧ 非 ST` | `value_score` / `growth_score` | value 的 312 是旧尺遗产(R-D1) |
| **资金事件** | main_fund · (rz 未成通道)· northbound(死)· event(关)· LHB(无) | `flow_event`:F1 主力净流入(旧 main_fund,**加门**排除反号/微量失真,今天只在 L3 打旗)、F2 融资强度(rz 腿修好后)、F3 事件(回购/增持/调研,默认关不变)、**北向摘除直到腿活** | `main_inflow_yi>0` ∧ 非失真 | `main_net_ratio` | F2a/F2b(游资/机构席)普查 **显著有害** → 永不作召回,只作避雷单材料 |

配额与 floor 的**唯一表**:`recall/families.py` 一张 `Family(name, tiers, quota, floor, l2_floor)`,`scan_config.funnel.channel_quotas` 改按族键;L2 `DEFAULT_FLOORS` 删除改派生;`effective_floors` 语义不变(族内全关 → 0)。

### 3.6 覆盖扩面候选(只进影子,每条附普查判决)

| 候选 | 数据 | 普查判决 | 影子怎么做 | 类 |
|---|---|---|---|---|
| R4 短反转档 | 帧已算 | 三门全过(`factor-regroup-gap` §1) | recall_lab 反事实 Recall lift | I |
| 行业相对强度 / 行业动量 | `sector_mom` 在 L2 算过(`l2_stratify.py:129-131`);`sw_daily` **从未探针**(5000 积分,`sector/pack.py:7` 自注「待权限核实」) | 06-24 sector RS IC ≤0.018(旧尺) | 先探 `sw_daily` 权限;成分股中位数版本可零新端点做影子 | I |
| 卖方修正(`report_rc`) | 湖有;`consensus.py` 0 行日**永久缓存**(`consensus.py:38-41`,限频拒绝疑似,UNVERIFIED) | 未测 | 修 0 行缓存;≥60 日后作族分 | I |
| 热度(`eastmoney_hot_rank` / 雪球关注) | 08-09 起每晚入湖,零读者 | **从未量过** | 做 IC 探针再谈 | I |
| 事件(回购/增持/解禁) | `events.py` 每跑算 | 首读 −1.01pp(旧尺);解禁前夜 −0.32 显著有害 | 保持关;解禁只进日历/避雷 | — |
| 涨停梯队 / 连板 | `limit_list_d` 只喂温度计 | F3 族全正但 **X_ORACLE**(D+1 EOD 才知道,买不到);`limit_ladder` IC −0.110 | **不作召回**;只作「可见性/避雷」列 | — |
| 大宗 / 龙虎榜 / 超大单 | 湖有 | F2a −1.81 / F2b −1.10 / F2d/F2e 全负显著 | **避雷单材料**(08-26 A1),不作召回 | B(避雷单) |
| 可交易性轴 | 帧已有 `pct_1d/amount/turnover` | 收益随可成交性单调递减 | 加 `tradability` 列(不设门) | I |

### 3.7 影子实验的晋级协议(防止「影子」变成换名字的样本内调参)

1. **先登记再读数**:`experiment_id / hypothesis / candidate variants / point-in-time feature set / winner_rank 与 winner_econ 定义 / cost_model_version / 样本起止 / 最小成熟日 / primary / guardrails / 晋级与停止规则` 先落冻结 manifest;看过结果后新增变体必须新 `experiment_id`,不能改原假设。
2. **历史只作体检,前推才作晋级**:现有 43 日深熊样本只排接线错误与定方向,不独立授予上线资格;至少再攒 20 个前推结果日,按交易日 block bootstrap 报 CI。样本不足或 regime 单一时结论只能是 `INCONCLUSIVE`,不得把阈值放宽到过门。
3. **一批多试要如实计数**:同一族同时试多个 tier / score / quota 时,报告全部试验数并对 primary 做 Holm 校正(或在 manifest 预先指定唯一 primary variant);不能只展示胜者。赢家定义、成本模型、buyable 口径任何一项变化都重置样本钟。
4. **晋级是显式 B 类裁决,不是在线学习**:系统不按滚动 40 日读数自动改 quota/floor/权重;`recall_lab` 只写影子比较。上线逐项 feature flag,一次只晋级一个可归因变更,并保留旧策略同日反事实与一键回滚。
5. **收益不是唯一门**:primary 至少不劣且 family/行业/市值覆盖、tradability、liveness、成本与墙钟均不过 guardrail 才可晋级。任一 A 级 PIT 失败的日期从效果统计剔除并单列「不可判」,不能按 0 收益或 0 召回填充。

### 3.8 不做清单(负结果与裁定)

L2 上模型;预告事件通道;52 周高;北向作通道(腿活了也先影子);任何用 5–10 日尺定召回配额;把涨停族/席位族当召回;为凑 BUY 放宽任何门。

---

## 4. 第三问:各阶段研究优化点

### 4.0 三条总原则

1. **指令级 → 数据级**:C 审计列了 20 条约束,其中 **9 条只有散文**(网查 cap ×5、数字必出 pack、只读 §1–3、独立初判、持仓管理节、执行线存在、v2 时效窗)。原则:能 lint 的先 lint(warn),能投影的投影(看不见就写不出),最后才是加严。
2. **每级一尺已落(G3),现在让每级的 prompt 目标函数与它的尺一致**:L3 的 def 让它做「T+2 兑现机制:明天谁买」,尺量的却是 finalist−bench(≈0)与 conviction↔fwd10(−0.25);L4 的产品是否决,def 的重心却在评级。
3. **端口化**:任何研究产物要么进一个端口(有 as_of、有鲜度、有消费者),要么诚实标「仅人读」——不再有「写了没人读」的第三态。

### 4.1 Stage 0 · 市场研判(策略师)

| 现状 / 证据 | 优化点 | 类 |
|---|---|---|
| 输入只有 `strategist_pack` 投影(数据级防锚定 ✓);§4 操作基调**只到 appendix**(K8) | 要么行动节真读 §4(它本来就只给 L5),要么 prompt 删 §4 少写 100 字 | I |
| 「数字必出 pack」只有散文 | `market_view` 数字 lint:正文里每个百分数/计数须能在 `strategist_pack` 找到(同 L3 thesis lint 的做法) | I |
| 策略师 **看不到**行业 brief(它先跑),行业 brief 看不到 market_view | 保持(防锚定);但 L5 的「行业 top3」与策略师 §3 红黑榜同源自 `market_pack.sectors`,两处口径已一致,只需一条 lint 锁 | M |
| `global_tape` 进投影 | **B-1 冻结**;D-4 的 `global_tape.json` 至今**从未产出**(D 审计:无 lake 分区、无文件),解冻前先让它每周真跑出来 | I(产出)/ B(注入) |

### 4.2 Stage 1 · 行业 brief

| 现状 / 证据 | 优化点 | 类 |
|---|---|---|
| brief 只剩地形段,**≈90% 是 pack 数字的复述**(模板 `sector-playbook.md:158-167` 六行里五行是 pack 字段);opus·xhigh ×6 ≈ $1.92/日;`sector-research/SKILL.md:22-23,38` 仍写已退役的 `## 研判段`/`**行业方向**`/ledger 与「L5 嵌 🏭 行业研判节」(L5 并不嵌,只数字节数 `report_sections.py:226-278`) | ① 文档勘误;② **影子**:确定性渲染同一地形段(`sector_terrain_md` 已存在)与 LLM brief 并排落盘 20 日,人读差异;若 LLM 只多了 ≤2 条头条,则解冻后把 LLM 腿改成「只写头条一行」 | M / I(影子)/ B |
| 行业 pack 落 `$CTX/sector/<date>/`,不进 run 分区;capsule 只快照 staging | A6 | I |
| `sw_daily` 未探 | 探针;有则 pack 加行业指数位置 | I |

### 4.3 L3 · 精排

| 现状 / 证据 | 优化点 | 类 |
|---|---|---|
| def 要 `_l3_calibration.md`「硬约束」(无产者);「宁缺毋滥禁止凑数」但输出会被席位⑨填充;`SKILL.md` 写 prelude「9 步」实为 12 | def/doc 勘误(08-26 A6 未做) | M |
| `rc`(卖方修正)列在 `l3/prompt.py:310-325` 写好了但**未启用**,因为 `consensus.csv` 在 L4-prep 才生产(`scan-market.js:519`)——**顺序错** | consensus 生产者搬到 prelude(它已是 prelude 步)并启用列 | I(搬)/ **B**(列进表) |
| 目标函数与尺不一致(§4.0-2):finalist−bench −0.006;conviction↔fwd10 −0.25 | **解冻后重述 L3 的产品**:「分诊 + 否决理由 + 差异化假设」——输出每票 `why_l4_should_look`(可证伪的一句)与 `red_flags`,conviction 改为「值得研究的确信」而非「愿真金买入」;评价尺 = `l3_finalist_minus_bench` + L4 早停率(分诊质量) | B |
| L3 看到的 40 行按 composite 排(R-D2);无可交易性列 | 表加族内名次 + `tradability`;pass1 队列按族轮询(§3.4 R-A) | B(影子先) |
| 第二意见:只有数字 repair;Tier-3 `verify.csv` 无产者但 4 个读者 | 删 `verify.csv` 读者(死码),或明确复活 Tier-3——二选一,不留幽灵 | M |

### 4.4 L4 · 活体情报(l4-intel)

| 现状 / 证据 | 优化点 | 类 |
|---|---|---|
| cap 20 指令级,hard cap 30 数据级;`web_budget`(D-5)**未接线**;08-26 八稿实测 22–37 超限 | 接 D-5(`capsule finalize` 调 `materialize_web_budget/evidence_index`;`intel_query_cap_lint` 传 path)→ 先量真实调用数 → B-5 硬帽解冻后再裁 | I |
| 六面盲搜每票 ≈$0.56,其中公告/互动易面是**接口能拿的**(`anns_d` 无权限,cninfo fallback 缓存目录 **0 文件**) | 08-26 A8 二选一:新闻目录接成「已知底」并给 `ingest_flash` 一个 cron 主人(nightly-close 现在活了,可挂)→ 情报站只查接口拿不到的;否则整包退役 | I(Q11) |
| 五个情报 def(l4/company/us/sector/global)是同一骨架复制粘贴,契约名 `intel v1 / intel_v2_full / intel v2_macro`(标头拼写还不一致),cap 20/12/12/6/8,表头 5/7/7/7/6 列;**只有 l4-intel 有 python lint**;`global-intel` 没有任何派发文本 | A2 的 `IntelContract` 单源 + def 生成块;四个 full 档情报稿**必须有读者**(§4.7 端口)或删 def | I |
| 价格断言对账 ✓(数据级) | 保持 | — |

### 4.5 L4 · 决策卡 / rubric / ensemble / sell_review

| 现状 / 证据 | 优化点 | 类 |
|---|---|---|
| `rubric_rating` 零调用:评级是 agent 自算的算术,python 只 parse `Rubric建议`/`OW三门` 并 warn 「评级超 rubric」(`self_review.py:205-213`) | python 从卡面 6 维 + 三门**重算**评级并与卡面对账(先 warn 进 `gate_fires`;差异率 20 日读数后再裁是否 python 权威) | I(lint)→ B(权威) |
| 执行线两行、持仓管理节、独立初判(`chk_blind_pass`)**无任何 python 检查** | 三条存在性 lint(warn) | I |
| 卡与卡之间无任何比较(E6 只排一个 BUY;🔗 同链只列同行业名) | appendix 加确定性「同日卡对照表」(评级/EV/R:R/三门/早停/tradability 并排,从 `decision_records` 现算)——**不进 prompt** | I |
| 非席位 finalist 的满卡(08-26 A2):评级对 5–10 日无排序力,有信息的是早停停因 | 20 结果日后按 A2 裁:降为「P1–P3 否决卡」 | B |
| `sell_review` 与原卡同档时空转(08-25 615s);ensemble 串在每股 workflow 内使整趟等最慢一股 | 只在复核**能改变动作**时派(`isSellish` 且原卡非 SELL 提案);ensemble 中位/折回**只在 python 算**(删 JS 那份,K7) | B / I |
| 卡面 `**Rating**` 解析兜底取全文第一个评级词 | A2 strict 档 | I |

### 4.6 E6 · 相对 BUY / L5 · 报告

| 现状 / 证据 | 优化点 | 类 |
|---|---|---|
| A2 硬门与 `pool=composite` 数据级 ✓;`_final_ratings/decision_records` 由渲染器写(K9) | 写出搬 `decision_finalize` CLI 步;`summary.md`×3 / `run_health.json`×5 / `details`×4 改单写者 | I |
| 「拒绝」仍不是产物(08-26 A1) | 避雷单先影子 20 日:早停名单 + 停因 + 席位族/大宗族命中(普查显著有害的族终于有了正当用途) | I(影子)→ B |
| brief ⑥ 只有昨日 delta,无「昨日推荐票 T+1 实际」(E3);`stage_rulers` 不进任何报告 | brief 加一行(数据在 `outcome`/`ledger_views` 已有);summary「运行事实」加四把阶段尺的 20 日值 | I |
| 时间锚 G1 已落,但 brief 首行不印「数据日/批准时刻/可执行状态」(E 审计) | 印 | I |

### 4.7 full 档三技能 + 档案 + 持仓 → 「研究日历」(这是「全覆盖」的时间维)

今天三海拔的 full 档全是**用户手触发 + 零机器消费者**(stock full / sector full / `_company_intel` / `_us_intel` / `_sector_intel` / `global-intel` 全是死胡同;macro full 只剩每周日 20:00 的确定性 harvest,LLM 腿无人跑 → `macro_state` 停在 07-27、08-23 目录只有 `data.md`)。一个「全面覆盖的研究系统」需要的不是更多 agent,而是**定频 + 端口**:

| 海拔 | 频率 | 触发 | 产出端口(机读,as_of + regime_at_run + keyed 评级) | 消费者 |
|---|---|---|---|---|
| 宏观 full | 周(周日 harvest 已 cron;LLM 腿在下一个 session 补跑) | prelude 端口行 `✗macro_state(Nd)` 提醒 | `macro_state.json`(已有) | 策略师(已接) |
| 中观 full | 双周,K = 当周 L2 集中度 top3 ∪ 📌 所在行业 | 同上 | **`sector_state.json`**(新端口:景气位置/格局/链事实,**无方向**) | L5 appendix(I)→ 行业 brief 已知底(B) |
| 微观 full | 📌 全部 + 20 日内 ≥2 次 finalist | 同上 | **`stock_state`** = 档案 §1–§4 刷新(dossier 已是端口) | L4 📚 摘要(已接) |
| 档案首覆 | ≤3 只/晚(08-26 A9) | nightly-close 排队 | dossier | L4 |
| 持仓 | 日 | tripwire(已有)+ 事件旗 | — | 人 |

规则:**cron 只能跑确定性腿**(harvest / pack / 排队 / 端口鲜度行),LLM 腿由下一个 session 按 prelude 端口行的提醒补跑——这与 08-21「学习环退役」无冲突,这里没有任何回注,只有「事实包的定期刷新」。四个 full 档情报 def 的产出必须进对应 state 文件,否则退役 def(Q10)。

#### 4.7.1 研究队列与背压(没有这层,「定频」只会变成无限欠账)

- deterministic 腿只产 `research_queue.json`,不在 cron 内派 LLM。任务幂等键 = `(research_kind, subject_key, evidence_period, output_schema_version)`;状态只允许 `QUEUED → CLAIMED → SUCCEEDED | DEFERRED | EXPIRED`,同键成功后不得重复排队,CLAIM 超时可回到 QUEUED 并递增 `attempt`。
- 优先级不是 FIFO:P0=📌 持仓 tripwire / 披露后对账;P1=当天消费者需要但已 stale 的端口;P2=20 日内 ≥2 次 finalist 与集中度 top3 行业;P3=探索性 full intel。优先级只决定先研究谁,**不进入评级、召回或 BUY**。
- 每个 session 在开跑前声明 `max_items / max_tokens / wall_clock_budget`;超额任务保持 DEFERRED,prelude 明印 backlog 数、最老 age、各优先级数量。不得静默丢队列,也不得为了「补齐覆盖」超预算派发。
- `max_age` 到期后任务 EXPIRED 并重建到最新 evidence period;禁止拿旧 pack 补写新 state。某端口没补上时按 A4 的 stale policy 省略或带 age 降级,不能把「排队中」冒充「覆盖」。
- 队列只调度事实包刷新,不根据研究结果自动改 prompt、权重或下一次优先级;因此仍符合「只记不学」。

### 4.8 情报家族收编(一句话)

五个情报 def → 一份模板 + 四组参数(海拔 / 面 / cap / 契约版本),由 A2 生成;lint 一套;`external_tools.jsonl` → `web_budget.json` 是唯一的计量单位(「一行 ≠ 一次查询」已在 `web_budget.py` 写明)。

---

## 5. 全覆盖矩阵(海拔 × 证据 × 阶段;✓ 消费 · ◐ 降级/部分 · ✗ 缺 · L 湖里有没人用)与补面计划

| 证据面 | Stage0 | L1 | L3 | L4 | L5 | stock full | sector full | macro full | 持仓 | 补面(类) |
|---|---|---|---|---|---|---|---|---|---|---|
| 价量 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| 资金流 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | 持仓卡加主力 5 日(I) |
| 两融 | ✓ 市场级 | **◐ rz 恒 NaN** | ✗ | ✓ | ✗ | ✓ | ✗ | ✓ | ✗ | 修腿(R-B) |
| 北向 | ✓ 市场级 | **◐ north 恒 NaN** | ✗ | ◐ | ✗ | ◐ | ✗ | ✓ | ✗ | 取 T−1 或摘组(Q8) |
| 龙虎榜/席位 | ✗ | L | ✓ 计数 | ✓ | ✓ | ✓ | ✗ | ◐ | ✗ | 只进避雷(B) |
| 大宗 | ✗ | L | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | 只进避雷(B) |
| 增减持/回购/解禁 | ✗ | ✓ 计数(通道关) | ✓ | ✓ 日历 | ✓ | ✓ | ✗ | ✗ | ✗ | 解禁进持仓哨兵(I) |
| 财务 | ✗ | ◐ 上季失败则降级 | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | 面板补基本面让 growth 组有权重(I) |
| 估值 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — |
| 筹码 | ✗ | **◐ 0.547** | ◐ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | 半载守卫(A3) |
| 一致预期/卖方 | ✗ | ✗ | **◐ 列写好未启用** | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | 顺序修 + 0 行缓存修(I);进 L3 表(B) |
| 公告 | ✗ | ✗ | ◐ cninfo 兜底(缓存 0 文件) | ◐ 网查 | ◐ | ◐ | ✗ | ✗ | ✗ 自陈已退役 | A8 裁决(Q11) |
| 新闻文本 | ◐ ≤2 网查 | ✗ | ✗ | ◐ 网查 | ✗ | ✓ | ◐ | ✓ | **◐ `stock_news_em` 07-24 后无写者** | 目录接 cron 或退役(Q11) |
| 情绪/热度 | ✓ 温度计 | L(hot_rank 零读者) | ✗ | ✗ | ✓ | ◐ | ✗ | ✓ | ✗ | 热度 IC 探针(I) |
| 行业指数/板块 | ✓ 资金 | ✗(标签=东财) | ✓ 地形 | ✓ brief | ✓ | ✗ | **✗ sw_daily 未探** | ◐ | ✗ | 探针(I) |
| 衍生品(A 股) | ✗ 裁定 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | 不做 |
| 海外 | ✗ 拒键 | ✗ | ✗ | ✗ 冻结 | ◐ 从未产出 | ◐ 代码在、EDGAR 无 UA、映射 32 条全 pending | ◐ 0 项 | ◐ 从未产出 | ◐ 从未产出 | 先 soak 7 天真产出(运营,I);注入等解冻 |
| 宏观 | ◐ macro_state 08-23 | ✗ | ✗ | ✗ | ✓ | ✓ | ✗ | ✓ | ✗ | LLM 腿定频(§4.7) |
| 日历 | ✗ | ✗ | ✓ 解禁/披露 | ✓ | ◐ 海外行从未产出 | ✓ | ✗ | ◐ | ◐ | 同海外行 |
| 盘中/竞价/分钟 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | R3 影子(14:45 point-in-time,三问稿)—— 不在本稿 |

**读法**:矩阵里没有一格是「加个 agent」能补的;缺的格子分三类——**腿死**(两融/北向/筹码半载/新闻写者)、**建成未接**(卖方列/海外/D-5/热度)、**裁定不做**(衍生品/盘中)。前两类全是 I 类,是冻结窗内的活。

### 5.1 「有一格」不等于有效覆盖:成熟度与覆盖债

上表的 `✓` 只表示**结构上有消费者**,不能单独证明数据在决策时可得、契约有效或产生了可审计作用。每个「证据面 × 阶段」再按同一成熟度记一档:

| 档 | 含义 | 过档条件 |
|---|---|---|
| C0 · 登记 | 知道源与用途 | endpoint / producer / intended consumer 已登记 |
| C1 · 可得 | 决策时点真能拿到 | PIT 通过、freshness 通过、成功真空与失败可区分 |
| C2 · 可用 | 机器契约可信 | schema/version/coverage/null 语义通过,降级显式 |
| C3 · 已消费 | 生产路径真实读取 | capsule lineage 能证明消费者读过哪些字节 |
| C4 · 可归因 | 知道它有没有用 | 有对应 stage ruler / shadow 对照 / 成本,能判保留或退役 |

「全面覆盖」的交付口径不是把所有格补成 `✓`,而是:**所有保留证据面至少 C3;会改变动作的证据面必须 C4;明确负结果或裁定不做的格子记 `EXCLUDED(reason, decision_id)`，不是债。** 这样衍生品「有意不做」不会被误报缺口,热度「湖里有但没人量」也不会被误报已覆盖。

新增一份由 A1/A4 派生的 `coverage_debt.json`(以及一行人读摘要),每条只含 `cell / maturity / blocker / producer / consumer / freshness / pit_status / evidence_status / next_probe / class(M|I|B) / decision_id`;它是架构欠账与排程输入,**不进 L3/L4 prompt、不按数量当 KPI**。优先级固定为「持仓/当日动作缺口 > 生产链 C1/C2 断裂 > 建成未接 C3 > 无效果证据 C4 > 探索扩面」,避免被最容易补的格子带着跑。

---

## 6. 批次与优先序(仅建议;I/M 现在,B 等 09-中)

| 批 | 内容 | 类 | 前置 |
|---|---|---|---|
| **P0 · 下一次真跑之前** | ① §1.4 壳:先量(Q12)——若确认量级,A5 最小版(删 `tracedAgent` 包装;确定性步只记 `DISPATCH_INTENT/OUTPUT_OBSERVED`,真实 harness 边界仅由可观察它的 adapter 写);② `capsule.finalize` 传 mode;③ `replay.py:180` 死 argv;④ 帧走湖 + 半载守卫 + north/rz 腿(R-B);⑤ D-5 接线 + `stage_rulers` 进 nightly;⑥ 三个跨日读者改 `RPT` 侧解析(K4);⑦ SKILL `_token_usage.json` 路径分歧;⑧ prewarm 三处(08-26 B1,仍未做,08-28 又死一次);⑨ 文档勘误(9→12 步、`_l3_calibration.md`、12 路、申万、sector SKILL 研判段);⑩ `.pyc` 与根目录 `task_plan/findings/progress.md` 不入库 | M/I | 无 |
| **P1 · 契约(冻结窗)** | A1 登记表 + schema version/兼容窗 + JS 生成 + drift 守卫;A2 产出语法 + def 生成块 + 评级序单源;A7 分层测试(允许清单 = 今日矩阵);A6 配置按引用 + run 分区可写根 | I | P0 |
| **P2 · 证据包(冻结窗)** | A3:`frame_pack` 先(它修 §1.3 第一行),再 `stock_pack`(slim/full 合一),再 sector/macro;统一 PIT 时间信封与 cutoff 红用例;公共件下沉;`tushare_source` 公开门面;A4 端口 + prelude 端口行;由 A1/A4 派生 `coverage_debt` | I | P1 |
| **P3 · 召回与研究影子(冻结窗)** | `recall_lab`(R-A 反事实 lift)+冻结 experiment manifest、R4 短反转影子、热度 IC 探针、`sw_daily` 探针、行业 brief 确定性影子、rubric 重算 lint、三条 L4 存在性 lint、卡对照表、避雷单影子、brief 两行、端口 state 文件 schema + `research_queue` 背压(§4.7) | I | P2(部分可与 P1 并行) |
| **P4 · 解冻后(09-中,逐项带开关 + 活体验收 + 各自重置样本钟)** | R-A 上线(族通道 + 贡献配额 + floors 派生)或 R-C;L3 目标函数重述 + 族名次/可交易性列 + `rc` 列;L4 非席位否决卡;sell_review 条件派;避雷单出报告;`sector_state` 进行业 brief 已知底;B-1..B-5(外源) | B | P3 影子读数 |

**08-26 稿的 P0/P1 有 9 项仍未做**(E 审计:B1 prewarm、B7 计量误报、C5 三条假 skip、C6 ruff 61 条、B2 步时长、B3 壳合并、C3 任务健康行、C4 北向 quota、A6 def 漂移)——本稿不重列,但 B1/C4/A6 已被本稿 P0 吸收,B3 被 A5 取代(它比「合并壳」省得多)。

### 6.1 每批退出门(完成任务数不算完成批次)

| 批 | 退出门 | 不能冒充通过的东西 |
|---|---|---|
| P0 | Q12 计量跑拿到每阶段墙钟、壳/agent 数、token/$、端点 freshness/coverage、三个法证结论;所有 UNVERIFIED 量级替换为实测或明确不可测 | 单元测试绿、MANIFEST 单独绿 |
| P1 | 登记表对现状 parity;生成物 hash 锁;每产物单写者;分层允许清单只减不增;旧/新 schema 兼容窗测试通过 | 只是把五份硬编码复制到第六份 registry |
| P2 | 冻结日期 byte-parity;A 级 PIT/cutoff/半载守卫有红用例;端口对成功真空、失败、stale、schema 不兼容分别给出诚实状态 | 文件存在、湖命中、fallback 后有非空表 |
| P3 | 每个影子都有冻结 manifest、成本与 guardrail;研究队列能显示 backlog/deferred/expired;20 日未成熟时状态写 `INCONCLUSIVE` | 样本内最好的一条、单一深熊 regime 的显著值 |
| P4 | 每个 B 类逐项 decision record + feature flag + 回滚演练;只晋级一个可归因变化;策略/成本/schema 版本与样本钟同时重置 | 多项一起上线后「总收益看起来更好」 |

跨批统一保留一个 budget envelope:`approved_at 距 operational cutoff / wall_clock / agent+shell 数 / token+$ / A级可得率 / degraded 端口数`。Q12 前只量 baseline 不拍阈值;拿到实测后再把上限写进配置和 run health,不能在本稿凭外推虚构 SLO。

---

## 7. 待裁(用户)

### 7.0 裁决顺序与无回复默认值

- **先裁 Q1 / Q4 / Q12**:分别锁架构路线、召回标签、计量事实;三者不需要等 20 日。
- **Q12 → Q2**:先看到真实壳成本与 trace 形状,再决定 A5 默认开关。Q2 未裁时继续旧 trace,不得先删壳。
- **Q1 → Q3**:只有选路 A 才需要裁 staging 镜像迁移;先做读侧兼容,再删旧写根。
- **Q4 → Q5 → Q6/Q7**:先锁 rank/economic 双赢家口径,再读 R-A 影子;若最终选 R-C,Q6/Q7 的通道级裁决自然失效。Q8 是死腿修复,可在 P0 独立做恢复率计量。
- **Q10 → Q9/Q11**:先确认 full 研究端口与消费者,再决定行业 brief、新闻层给谁供料;否则会继续造无读者产物。
- 无明确裁决时的默认值:**M/I 可继续、所有 B 关闭、影子只记不展示、样本钟不重置、不为赶批次放宽门**。

| # | 问题 | 本稿建议 | 不选的后果 |
|---|---|---|---|
| Q1 | 架构走路 A(契约优先)/ B(重建 pipeline)/ C(只修点)? | **A** | B 会再死一次;C 让下一波再造第六份登记表 |
| Q2 | A5:删 `tracedAgent` 双壳;确定性步只写 `DISPATCH_INTENT/OUTPUT_OBSERVED`,真实 `DISPATCHED/COMPLETED` 仅由能观察 harness 的 adapter 写(改变 capsule 证据形状,三结论不变)? | 先量(Q12)再默认开 | 每趟 250–320 壳 ≈ $30+/日 UNVERIFIED;若让相邻步代写真边界,则省成本但法证失真 |
| Q3 | A6:日期键 `$CTX/scan/<date>/` 保留为发布后只读镜像,还是彻底不再产生? | 只读镜像一轮,下一波删 | 19 处散文与 3 个跨日读者继续两套路径 |
| Q4 | 召回 KPI 同时报 `winner_rank=主尺 top-decile∧buyable` 与 `winner_econ=winner_rank∧净成本后>0`,分母扩到 decision cutoff 时点可得的湖全体? | **是,两者并报** | 只报 rank 会把跌得较少叫赢家;只报 econ 在深熊/小样本日会失去稳定的覆盖尺;L0 盲区仍在尺外 |
| Q5 | 召回整编走 R-A(家族化 + 贡献配额,先影子)/ R-C(composite-only)?R-B 无争议先做 | **R-A 影子 → 09-中裁** | 14 路继续各干各的、配额继续用错尺 |
| Q6 | `heat`(无门、fwd_10 −5.72)处置:加门 / 降为 guardrail / 退役? | 加门(turnover z)并入延续族 C3 | 每天 50 只无条件保护席 |
| Q7 | `healthy`(全尺最差)quota 112 → floor 40 只保多样性? | 是(上涨侧覆盖保留,席位缩) | 一族显著负的票每天占 L2 15 席 |
| Q8 | `north`/`rz` 死腿:修(hk_hold 取 T−1 / margin_detail 记账重试)还是摘组? | 先修一轮量恢复率,两周内不活则摘 | composite 静默少两组,权重归一无人知 |
| Q9 | 行业 brief 确定性影子 20 日;若 LLM 只多 ≤2 条头条则解冻后改「只写头条」? | 做影子 | $1.92/日买 pack 数字复述 |
| Q10 | 研究日历(§4.7):立 `sector_state` / `stock_state` 端口 + 定频;四个 full 档情报 def 无读者则退役? | 立端口;def 二选一 | 三海拔 full 档继续是死胡同,「全覆盖」只在文档里 |
| Q11 | A8 新闻层:接成情报站已知底(nightly-close 现活,可挂 `ingest_flash`)还是整包退役? | 接线(前提:`stock_news_em` 写者复活) | 2.3k 行无主 + 持仓哨兵读的是 07-24 的标题 |
| Q12 | 下一次真跑作为「计量跑」(接受成本,只为量 §1.3/§1.4 与 G6 验收)? | 是,且用 Codex 引擎(三问稿 Q5) | A5/A3 的开关默认值只能靠外推定 |

---

## 8. 局限

- 五路审计是**只读静态审计 + 磁盘产物**,没有新的真跑;§1.4 的成本外推、K4 的跨日读者回归、`hk_hold` 发布滞后、`report_rc` 限频原因均 UNVERIFIED。
- 阶段尺读数(§1.2)来自 43 个数据日、单一深熊 regime;L4 拒绝价值 n=12;E6/执行线/整链 UNAVAILABLE。lift 的 CI 已给,别把 1.047 读成「有一点点」——CI 跨 1。
- §3.7 的「20 个前推结果日」只是与既有冻结窗一致的**最小观察门**,不是统计功效保证;CI 仍宽、regime 仍单一就继续记 `INCONCLUSIVE`,不能因日历到点自动晋级。`available_at` 若源不提供,只能用保守的首次观测 `received_at` 代理并标 `availability_inferred=true`,不得反推一个更早时点。
- 本稿的架构建议全部按 I/M 类设计,但 **A2 的 def 生成块会改 prompt 字节**(语义不变),按 08-13 裁定属人批改动,须在 `run_contract.prompt_hashes` 留痕;A5 改变 capsule 事件的写者,需要用户作为法证设计的甲方确认(Q2)。
- 「全覆盖」矩阵按 grep 与产物目录核对,没有逐格跑数据;`sw_daily / cyq_chips / ths_* / eco_cal` 等端点标「从未探针」,权限未知。
- 仅供研究,非投资建议。

---

## 附录 A · 审计原件与关键索引

- 审计原件(抛弃型,本次 session scratchpad):`audit_A_coupling.md`(阶段 I/O 契约表 + 九类耦合证据 + 依赖矩阵 + 五份登记表比对)、`audit_B_recall.md`(14 路注册表 + composite 组权重 + 帧列清单 + 普查判决表 + 13 条不一致)、`audit_C_research_roles.md`(16 个 LLM 角色表 + 跨层流向 + L4 注入清单 + rubric/E6 规则 + 情报家族对照 + 15 条覆盖缺口 + 指令级/数据级清单)、`audit_D_data_coverage.md`(63 端点注册表 + 湖旁路清单 + 9 新源状态 + 覆盖矩阵 + 未用 tushare 端点 + 运维状态)、`audit_E_worktree_status.md`(G0–G6 / 08-26 P0 落地状态 + 冻结 B 类清单 + 测试结果 + 普查读数)。
- 本稿引用最多的真身:`scan/recall/merge.py:16-67`(quota_union)、`scan/recall/l2_stratify.py:41-64,159-195`、`common/scoring.py:467-537`(composite)、`data/tushare_source.py:354-454`(生产帧取数)、`scan/artifacts.py:35-73`、`scan/run_profile.py:17-144`、`trace/capsule.py:2383-2386`(mode 未传)、`trace/replay.py:180`(死 argv)、`.claude/workflows/l4-stock.js:174-245`(tracedAgent)、`scan/l4/rubric.py:18-46`(零调用)、`scan/l3/prompt.py:310-325,454-458`(rc 列未启用)、`macro/state.py:226-238`(端口鲜度样板)、`scan/populations.py:1001-1089`(阶段尺)。

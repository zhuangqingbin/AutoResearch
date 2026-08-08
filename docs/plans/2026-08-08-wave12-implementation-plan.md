# Wave12 实施计划(七问 · 统一相对 BUY)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/specs/2026-08-08-wave12-seven-topics-design.md`(修订版 c390bea)落地七问:尺尾差收口、粗排实债与候选护照、影子实验接线、gap 证据重验、**统一相对决策层 E6(影子起账)**、确定性 brief 双层报告、编排与新闻配套。**正式 BUY 所有权在 E6 人批 activate 前不切换。**

**Architecture:** 三层推进——①地基(快照数据积累 + 批A 把「裁决的尺」全部对到 `gap_c1_o2`);②核心闭环(F 修债→候选护照→E1/E2 仪器与证据→E6 影子 finalizer→C 确定性 brief→E3 因子);③配套(B 编排、D 新闻、gated B 类)。E6 是纯确定性 L5 组件(零 LLM、零联网),影子期只写 `context/scan/<date>/_relative_buy_decision.json` 与影子账本,不碰生产 buy ledger。

**Tech Stack:** Python 3(uv,无新依赖)、pandas、pytest;workflow js(AsyncFunction 探针,禁 `node --check`);tushare/akshare 免费端点。

## Global Constraints(每个 task 隐含,逐字执行)

- 主尺 `gap_c1_o2`(`autoresearch/common/ruler.py:12`);所有新读数列显式绑定 `gap_c1_o2 / rel_gap_market / rel_gap_sector` 之一;文案与产物一律标尺。
- 零 LLM 铁律:L0/L1/L2、E6 finalizer、brief 生成器全确定性;不新增任何 LLM 调用点(本波裁定:不设收编官 agent)。
- B 类(改名单/评级/prompt/调度语义)只走 `experiment_registry`(PREREGISTERED→人批 activate);影子件必须「默认不启用连副作用一起不启用」:不碰 `DEFAULT_FLOORS`/`merit_need`/lane 标签/生产 finalists/prompt 字节。
- 每 task 开工先 premise-check(引用的 file:line 与行为先复核,诊断错就改 task 不硬写);历史产物不改写(展示层现算);账本追加列不清零。
- 测试纪律:每个行为修复配**变异探针**(把修复反向/错腿,断言测试变红);测试禁挂真网(patch `_trade_days_for`/fetch 层);t1/生产账本类测试必须显式传 `ledger_path`(C4 家训,`tests/learning/conftest.py` 已有隔离 fixture)。
- 命令:全测 `uv run --no-sync python -m pytest -q`;单测 `uv run --no-sync python -m pytest -q <path>::<name>`。
- 提交:一 task 一 commit,只含本 task 文件;**不得触碰用户工作树中未提交的 `.claude/skills/scan-market/pinned.jsonc`**。
- 工期外挂账:E4b/F2-B/F4/D3-5/E6-activate 为 **GATED task**——本 plan 写全设计与触发条件,但不随本 plan 自动执行。

---

## Wave 0 · 运营即办(零开发,开工当晚)

- [ ] **T0.1** `uv run --no-sync python -m autoresearch.learning.retro 2026-08-06`(或 nightly 正门)补 08-06 归因 → `context/scan/2026-08-06/retro/attribution.csv` 出现;event 路 pr_20260725_001 凑满 ≥10 配对日,提醒用户走 `/feedback 裁决提案`(16 条 open 一并)。
- [ ] **T0.2** 重跑 `uv run --no-sync python -m autoresearch.scan.l2_slo` 与 `python -m autoresearch.research.channel_audit --days 30`(C1 换旗后旧报表口径失真)。
- [ ] **T0.3** 核验今晚 nightly 后 `context/scan/2026-08-05/t1_review/scorecard.csv` 是否出现 `gap_c1_o2/z_gap/final_verdict` 三列;结果记入 T13 的 premise 输入。

---

## Wave 1 · 地基(E4a 快照 + 批A 尺收口)

### T1 · E4a-0 热度端点活体探针(先证伪再写码)

**Files:** Create: `docs/research/2026-08-XX-hot-rank-probe.md`(XX=实施日)
**Interfaces:** Produces: 探针结论表(可用函数名/关键列/行数/失败模式),T2 的唯一依据。

- [ ] Step 1: 写一次性探针脚本(scratch,不入库)依次调 akshare `stock_hot_rank_em()`、`stock_hot_rank_detail_em(symbol)`、`stock_hot_follow_xq(symbol="最热门")`、`stock_hot_rank_latest_em()`(存在哪个用哪个,以当前安装版 `dir(akshare)` 为准),记录:返回行数、列名、是否含代码列、日期语义(快照 or 历史)。
- [ ] Step 2: 结论落 `docs/research/` 探针稿:每源一行「函数/行数/关键列非空率/裁定(可用|不可用)」。**两源全不可用 → T2/T3 改标 BLOCKED_BY_DATA,报告用户,批E4a 止步**(R-E5 边界:实施前确认端点)。
- [ ] Step 3: Commit `research: Wave12-T1 热度榜端点活体探针(东财人气/雪球关注)`。

### T2 · E4a-1 热度快照入湖(endpoints+contracts+lake)

**Files:** Modify: `autoresearch/data/endpoints.py`、`autoresearch/data/contracts.py`;Create: `tests/data/test_hot_rank_endpoints.py`
**Interfaces:** Produces: lake 表 `hot_rank_em`(date, rank, code, …)与 `hot_follow_xq`(date, code, follow_delta?, …——列名以 T1 实测为准);`first_seen_basis="observed"`;均 B 级契约(空→降级记账不阻断)。

- [ ] Step 1(test first): 写 `test_hot_rank_registered_as_asof_lake`——断言两端点在 endpoints 注册表内、契约级别为 B、lake 分区按日;mock fetch 返回 T1 实测列的假帧,断言写湖后可按 as-of 读回且**剥 fields**(lake 窄表毒化家训:cache key 不含 fields → 写湖一律全列)。
- [ ] Step 2: 跑测试确认红(端点未注册)。
- [ ] Step 3: 按 `stock_news_em` 先例(`contracts.py:133`、`endpoints.py:78` 模式)注册两端点;快照语义:每晚一份全量榜单,不回填历史。
- [ ] Step 4: 测试绿;变异探针:把契约级别改 A,断言「空返回抛异常」测试路径与 B 级「降级记账」测试路径能区分(两条断言各自锁死)。
- [ ] Step 5: Commit `feat(data): Wave12-T2 人气榜/雪球关注快照入湖(B级契约,observed first_seen)`。

### T3 · E4a-2 夜间采集接线 + 断采可见

**Files:** Modify: `autoresearch/scan/prewarm.py`(或 `autoresearch/learning/nightly_runner.py` 的采集位——premise-check 现有夜间取数挂点,与 limit_list_d 预热同位);`autoresearch/scan/prelude.py`(B 级降级汇总行已有机制,确认两新源纳入)
**Interfaces:** Consumes: T2 端点。Produces: 每晚快照;prelude 降级行可见。

- [ ] Step 1(test): mock 夜间入口,断言两源被调用且失败时写降级记账(不 raise);连续断采仅损失当日。
- [ ] Step 2: 接线(与既有夜间预热同一循环,勿新建调度器)。
- [ ] Step 3: 真跑一晚(或手动触发一次)验收:两湖分区出现当日文件、行数>0、日期单调;prelude 无该源报警。
- [ ] Step 4: Commit `feat(scan): Wave12-T3 热度快照夜间采集接线(断采可见)`。

### T4 · A2 因子晋升面切主尺(批A 最重,E3/E6 前置)

**Files:** Modify: `autoresearch/research/factor_lab.py:634,644-653,703,709,932`;`tests/research/test_factor_lab.py`
**Interfaces:** Produces: `eval()` 对 `ruler.MAIN_RULER` 产 `IC_<ruler>/ICIR_<ruler>/t/hit/IC_h1/IC_h2` 全判据族并作主排序 `sortcol=f"ICIR_{ruler.MAIN_RULER}"`;旧尺列保留降参考。E3(T24)与 E6 目标面读它。

- [ ] Step 1(premise): 复核 `:634` 分支与 `:703` sortcol 现值;确认 gap decile 并列表(`:670-694`)现状仅 decile 无 IC 族。
- [ ] Step 2(test first): `test_eval_promotion_family_on_main_ruler`——合成面板双尺列,断言 eval 输出含 `ICIR_gap_c1_o2` 且主排序按它;`test_eval_sortcol_follows_main_ruler`——monkeypatch `ruler.MAIN_RULER="fwd_2_oc"` 断言 sortcol 跟随(**补上现状 docstring 声称锁了但没锁的 sortcol 断言**,`:198`)。
- [ ] Step 3: 跑测试确认红。
- [ ] Step 4: 实现:判据族生产从 `fwdcol=="fwd_2_oc"` 硬编码改为 `fwdcol == ruler.MAIN_RULER`(旧尺列继续并列产出但不做主位);十分位主表用 `entry_tradable(ruler_name=MAIN_RULER)`+`GAP_CLIP`;`render_ic_by_regime:932` 标题死字符串改 f-string 插值。
- [ ] Step 5: 变异探针:临时把 gap 腿算成 `close[D+2]/close[D+1]`(错腿),断言 Step2 测试红;还原。
- [ ] Step 6: 双尺对照人工核验:对真实面板跑一次 eval,抽 3 因子手算 gap ICIR 对上;结论(哪些因子在 gap 尺下换位)落 `docs/research/<实施日>-factor-regroup-gap.md`——**只记录不改 `_GROUPS`**(改组=B 类另案)。
- [ ] Step 7: 全测绿;Commit `feat!(research): Wave12-T4 因子晋升判据族切主尺(sortcol 跟随 MAIN_RULER,旧尺降参考)`。

### T5 · A1 ensemble 折回账本资格旗

**Files:** Modify: `autoresearch/learning/ensemble_ledger.py:153-155,185`;`tests/learning/test_ensemble_ledger.py`

- [ ] Step 1(test first): 构造 attribution 假帧:某票 `buyable=False`(D+1 开盘一字)但 `buyable_c1=True`(收盘未封)——断言该票**进入** `_market_fwd2` 分母与逐票样本。
- [ ] Step 2: 红 → 两处改 `ruler.entry_tradable(frame)`(单点选旗,C1 手法)→ 绿。
- [ ] Step 3: 变异:把修复改回裸读 `"buyable"`,断言 Step1 红;还原。
- [ ] Step 4: Commit `fix(learning): Wave12-T5 ensemble 折回资格旗过 entry_tradable(C1 唯一遗漏)`。

### T6 · A3 0买裁决面切主尺(+冻结预告)

**Files:** Modify: `autoresearch/learning/zero_buy_ledger.py:45-54,80-84`、`autoresearch/learning/journal.py:24-25,81`;`tests/learning/test_zero_buy_ledger.py`、`tests/learning/test_journal.py`

- [ ] Step 1(test first): ①zero_buy:合成某日市场 gap 为负、fwd_2 为正,断言 verdict 按 **gap** 出「空仓方向正确」;②journal:断言 `_COLS` 含 `mkt_gap` 且回填路径写值。
- [ ] Step 2: 红 → 实现:zero_buy 增 `mkt_gap` 列为主判据(render 的「(主尺)」标签挂它),`fwd_2/fwd_5` 降参考列保留;journal 增 `mkt_gap` 列(历史回填,旧行 n 不清零);表补 08-06 起行。
- [ ] Step 3: 重放验收:对 §1.2 四个 0买日(07-31/08-03/08-04/08-05)跑 render,断言与两尺对照报告 ④ 一致(07-29 那类日按 gap 翻 FALSE 的逻辑走新列)。
- [ ] Step 4: 模块头加注:「E6 activate 日起本账本冻结为 legacy,新主账=relative_ledger(见 T22)」——只加注,不建冻结逻辑(activate 是 GATED)。
- [ ] Step 5: Commit `fix(learning): Wave12-T6 0买裁决与 journal 补主尺列(verdict 按 gap 出)`。

### T7 · A4 心跳 IC 切主尺

**Files:** Modify: `autoresearch/learning/changelog_ledger.py:36-45`;`tests/learning/test_changelog_ledger.py`

- [ ] Step 1(test first): 断言 `_day_ic` 用 `ruler.MAIN_RULER` 列且心跳文案含尺名(会变的量)。
- [ ] Step 2: 红 → `fwd_1_oo` 改 `MAIN_RULER`,文案 f-string 带尺名 → 绿;变异(改回 fwd_1_oo)红;还原。
- [ ] Step 3: Commit `fix(learning): Wave12-T7 重标定心跳 IC 切主尺(fwd_1_oo→MAIN_RULER,文案带尺名)`。

### T8 · A5 evidence_manifest 定义串对齐

**Files:** Modify: `autoresearch/learning/evidence_manifest.py:42,69-71,84,593`;`tests/learning/test_evidence_manifest.py`

- [ ] Step 1(test first): 断言 manifest 每指标含 `ruler` 字段,且定义串由 `MAIN_RULER` 插值(grep 输出无裸 "fwd_2_oc" 定义句);注入「定义串与取值源不一致」映射 → schema test 必红(A0 同款)。
- [ ] Step 2: 红 → 实现(历史 `docs/research/2026-08-01-wave10-gate0-evidence.json` **不改写**,新产出用新串)→ 绿。
- [ ] Step 3: Commit `fix(learning): Wave12-T8 evidence_manifest 定义串插值主尺+每指标 ruler 字段`。

### T9 · A6 EXIT_FLAG 接消费(unsellable_o2)

**Files:** Modify: `autoresearch/learning/retro.py`(渲染层)、`autoresearch/learning/paper_nav.py`(gap 模式卖腿)、任一账本聚合行(`zero_buy_ledger` 或 `journal` 增 `unsellable_n`);`tests/learning/test_paper_nav.py` 等

- [ ] Step 1(test first): 构造 T+2 一字跌停开假票(open≈跌停价∧open==low):①retro 渲染出 ⚠️卖不出旗;②paper_nav gap 模式该票按「顺延到下一可卖开盘价」结算(实现口径就此锁定,报表注明);③聚合行 `unsellable_n=1`。
- [ ] Step 2: 红 → 实现(读侧统一经 `ruler.EXIT_FLAG`)→ 绿;变异(把旗读反)红;还原。
- [ ] Step 3: Commit `feat(learning): Wave12-T9 EXIT_FLAG 三处消费(标旗不剔:渲染旗/顺延结算/聚合计数)`。

### T10 · A7 lessons 打尺标+旧尺经验重验

**Files:** Modify: `autoresearch/learning/feedback_store.py`(schema+注入渲染);Data: `context/knowledge/lessons.jsonl`(6 条逐条);`tests/learning/test_feedback_store.py`

- [ ] Step 1(test first): 断言 lesson 行支持 `ruler` 字段、注入渲染带 `〔尺:<ruler>〕` 标;无字段旧行按写入日期推断(<2026-08-05 → fwd_2_oc)。
- [ ] Step 2: 红 → schema+渲染实现 → 绿。
- [ ] Step 3: 数据侧逐条处置(走正门,不硬删):`ls_momentum_recall_quota_swing_horizon`(建于 t5 尺)提名 retire;其余 4 条含旧尺读数的,能 gap 复算的复算改写 evidence、翻转的降 confidence 并注「gap 尺待重验」。处置表落 commit message。
- [ ] Step 4: Commit `fix(learning): Wave12-T10 lessons ruler 字段+旧尺经验重验处置(1 提名退休/4 标注)`。

### T11 · A8 shadow_buys 入 nightly + t1 gap 首验

**Files:** Modify: `autoresearch/learning/nightly_close.py:94-103`(names 表);`tests/learning/test_nightly_close.py`

- [ ] Step 1(premise): 结合 T0.3 结果查 t1 账本 15 行 `ruler=fwd_2_oc` 来源——若 `gap_finalize_pending` 未被 nightly 真调/写错列,先修接线再谈其余(修法以实查为准,不预写)。
- [ ] Step 2(test first): 断言 `_ledgers()` names 含 `"shadow_buys"` 且排在 `gate_attribution` 之后;mock 全链跑一遍 19+1 账本全 ok。
- [ ] Step 3: 红 → names 表加一行(注释:近 miss 节 5/6 run 静默缺席的根因修复)→ 绿。
- [ ] Step 4: 活体验收:对 2026-08-05/08-06 真跑 `gap_finalize_pending`,断言 scorecard 三列出现、`t1_review.jsonl` 幂等整替、新行 ruler 标注正确;记录首验读数进 commit message。
- [ ] Step 5: Commit `fix(learning): Wave12-T11 shadow_buys 入夜链 + t1 gap 终判首验(附实测)`。

### T12 · A9 名实不符与断层记档

**Files:** Modify: `autoresearch/research/channel_audit.py`(render 标题注)、`autoresearch/learning/stage_eval.py`(channel_eval.csv 增 `ruler` 列)、`autoresearch/research/sector_top3_backtest.py:2,61`、`autoresearch/learning/precedents.py`+`autoresearch/scan/l4/context.py:197-200`(显示标签)、`autoresearch/learning/cross_calib.py:9,151,315`(docstring)、`.claude/skills/scan-market/STAGES.md`(断层条目);测试各自就近补断言

- [ ] Step 1: channel_audit/stage_eval:**不改列名**;render 标题加「列名沿革 `*_t2`,值=MAIN_RULER(2026-08-05 起)」;channel_eval.csv 写 `ruler` 列(test 锁)。
- [ ] Step 2: 三处标签/文案修正;STAGES.md 比照 `pre_healthy` 先例记「2026-08-05 账本定义断层」。
- [ ] Step 3: Commit `docs+fix: Wave12-T12 账本列名沿革记档+ruler 列+三处旧尺标签修正`。

### T13 · A10 文档大扫(活指令改/沿革标注两分)

**Files:** Modify: `docs/PANORAMA.md`(:682/:704/:710/:729/:873/:971/:1043)、`.claude/skills/scan-market/SKILL.md:147-148`、`STAGES.md:259/307/308/368/376`、`.claude/skills/scan-retro/SKILL.md:13,53`、`retro-playbook.md:7,34,97`、`.claude/agents/l3-rank.md:24`(**含批D-D2:新闻文案按近 3 个扫描日 `L3_news` 实测非零率改写**);15 处代码 docstring(T12 未覆盖部分:`pinned_ledger.py:8,52`、`channel_ledger.py:4-6,103`、`retro.py:4,60`、`l2_slo.py:12`、`l3_marginal.py:105`、`gate_attribution.py:15,17`、`candidates.py:356`、`common/regime.py:6`、`common/scoring.py:105,196-197`、`news/typed_events.py:29,118`)

- [ ] Step 1(premise): 编辑前重读每个 skill 文件(skill 文档会被外部改的家训);D2 部分先跑真数据核实 news_sent 非零率。
- [ ] Step 2: 逐处改:活指令→gap 口径;纯沿革→保留并加「(沿革,现主尺=gap_c1_o2)」注。
- [ ] Step 3: 一次性审计脚本(scratch):`grep -rn "fwd_2_oc" docs/ .claude/` 每命中归入「沿革注记|参考尺注记|历史 research 报告」三类,归不进=本 task 未完。
- [ ] Step 4: 锚测试重跑(`test_l4_prompt_cache_prefix` 家族与 skill 锚测试若受 STAGES/SKILL 编辑影响,按新文本重锚一次)。
- [ ] Step 5: Commit `docs: Wave12-T13 主尺文档大扫(PANORAMA/SKILL/STAGES/scan-retro/l3-rank+15 处 docstring)`。

### T14 · A11 防复发 lint

**Files:** Modify: `autoresearch/learning/self_review.py`(product_shape_lint 家族);`tests/learning/test_product_shape_lint.py`

- [ ] Step 1(test first): 变异用例——新建裸写 `"fwd_2_oc"` 且无「参考尺」注记的假模块(tmp path 模拟 git 新增),lint 必 fail;带注记/存量文件不追溯必 pass;`.claude/` 文本出现「主尺 fwd_2_oc」措辞必 fail。
- [ ] Step 2: 红 → 实现(git diff 粒度判「新增文件」)→ 绿。
- [ ] Step 3: Commit `feat(lint): Wave12-T14 旧尺裸写防复发 lint(新增文件粒度)`。

### T15 · A12 报表重跑与断链修(半运营)

**Files:** Modify: `docs/research/2026-08-07-ruler-gap-vs-oc-baseline.md`(三处引用)

- [ ] Step 1: `task-15-report.md` 找回落库(若在会话产物中已不可得)→ 三处引用改「review 记录见 git log f58c3cd/23ed1b2」。
- [ ] Step 2: T0.2 已重跑的报表人工核对一次(l2_slo winner 定义串=新旗;08-06 报告旧门归因行不回写,下次 run 现算)。
- [ ] Step 3: Commit `docs(research): Wave12-T15 两尺对照报告断链引用处置`。

---

## Wave 2 · 核心闭环(F 修债 → 护照 → 仪器 → E6 影子 → brief → 因子)

### T16 · F1-3 selection_reason 落盘投影

**Files:** Modify: `autoresearch/scan/universe.py:459`;`tests/scan/test_universe_l2_cols.py`(新)与 `tests/scan/test_l2_slo.py` 补活转断言

- [ ] Step 1(test first): 断言 `L2_gbdt_top200.csv` 表头含 `selection_reason/selection_detail`;`l2_slo._guards` 在含该列的输入上产出分布 guard(31 天死分支活转)。
- [ ] Step 2: 红 → `l2_cols` 补两列 → 绿(纯新增列,零名单影响;断言行数与旧 fixture 一致)。
- [ ] Step 3: Commit `fix(scan): Wave12-T16 selection_reason/detail 投影落盘(l2_slo guards 活转)`。

### T17 · F1-1 吸筹死配额 replay delta 报告(人批门)

**Files:** Create: `docs/research/<实施日>-l2-floor-accumulation-removal-delta.md`;(人批后)Modify: `autoresearch/scan/recall/l2_stratify.py:36-37`

- [ ] Step 1: 用 `research/replay.py` VariantSpec 跑 `吸筹 floor 12→0` 变体对近 10 个真实扫描日:菜单 delta(换入/换出票数、lane_reserved True 数变化 96→?、三个消费者视角对照:L3 分块/force_full_card 命中/floor_experiment 分组)。
- [ ] Step 2: 报告落盘,**呈用户批 R-F1**;未批不改生产。
- [ ] Step 3(人批后): `DEFAULT_FLOORS` 吸筹 12→0(注释引事件桶同款纪律);测试:断言 Σfloor=81、merit_need=119、吸筹键=0;全测绿。
- [ ] Step 4: Commit `fix(scan): Wave12-T17 吸筹死配额 floor 12→0(replay delta 已人批,附报告)`。

### T18 · F1-2 reversal_confirm 摘除(人批 R-F2)

**Files:** Modify: `.claude/skills/scan-market/scan_config.jsonc`(funnel.recall_channels 删一项+行注 reopen 条件);`autoresearch/common/scoring.py:209-212` 加注

- [ ] Step 1(premise): 复核近 5 日 `L1_channels.csv` 该路仍 0 行。
- [ ] Step 2(人批后): recall_channels 摘除 `reversal_confirm`;行注「reopen 条件=vol_ratio_20 接入生产 L1 帧后重开 A/B(2026-08-08)」;scoring 硬门 docstring 同步注。测试:config 装载后有效通道=8 且校验通过。
- [ ] Step 3: Commit `chore(config): Wave12-T18 reversal_confirm 显式停用(恒空 4 周;登记 reopen 条件)`。

### T19 · F2-I 候选护照 builder(信息保真主件)

**Files:** Create: `autoresearch/scan/passport.py`、`tests/scan/test_passport.py`
**Interfaces:** Produces: `context/scan/<date>/_candidate_passport.json` —— `{code: {name, sector, recall: {channels: [..], per_channel: {ch: {score, rank, pctl}}, unique: bool, n_channels}, l2: {l2_rank, selection_reason, selection_detail, lane_reserved}, pass1: {kept, reason}, l3: {finalist, tier, conviction, mechanism, bench_reason}, l4: {research_rating, card_kind: full|earlystop, earlystop_reason, intel_avail, risk_flags: [..]}, versions: {rule: "passport.v1", date}}`。E6(T22)/F3(T21)/brief(T25)只读它。**纯派生视图:只读既有产物,不改任何上游;prompt byte diff=0。**

- [ ] Step 1(test first): 用 2026-08-06 真产物(或最小 fixture 复刻其 schema)跑 `build_passport(scan_dir)`:断言①每个 L2 票有护照行;②per_channel 名次与 `L1_channels.csv` 对得上;③`l4.research_rating` 与 `decision_read_model.read_final_ratings` 一致;④backfill 票 `selection_reason="backfill"` 不冒充 lane;⑤重复构建 byte 稳定(确定性)。
- [ ] Step 2: 红 → 实现:输入=`L1_channels.csv`+`L1_scored_full.csv`(名称/行业/composite pctl)+`L2_gbdt_top200.csv`(T16 后含 selection_reason)+`_l3_pass1_kept/cut.csv`+`_l3_judged.json`/`finalists.csv`+`decision_read_model`+earlystop/intel 状态(task-book/stage_results 结构化字段);缺源字段写 `null` 并记 `missing[]`,不猜。
- [ ] Step 3: CLI `python -m autoresearch.scan.passport <date>`;接入 `autoresearch/scan/post_run.py`(premise-check 其调用时点在 assemble/publish 后)自动产出;nightly_close 对缺失日补建(names 表加 `passport`?——premise-check:它在 scan 包不在 learning 包,若不入 names 则在 nightly_runner 挂点,取其一并写测试)。
- [ ] Step 4: 全测绿;Commit `feat(scan): Wave12-T19 候选护照 builder(L1→L4 派生视图,prompt diff=0)`。

### T20 · E1 影子实验数据腿接线(EXP-2 + EXP-1)

**Files:** Modify: `autoresearch/scan/recall/channels.py`(sector_momentum 影子);Create: `autoresearch/learning/mainflow5d.py`、`tests/scan/test_sector_momentum_shadow.py`、`tests/learning/test_mainflow5d.py`
**Interfaces:** Produces: `shadow/L1_channels_plus_sectormom.csv` 长表(channel_audit `--variant plus_sectormom` 可裁);`context/learning/exp1_mainflow5d.jsonl` 逐行 shadow verdict;registry 两实验 observations 逐日 +1。

- [ ] Step 1(premise): 读 `context/learning/experiments/registry.json` 两实验 spec 与 challenger_pointer 原文;读 `learning/experiment_registry.py` 的 observation 追加 API(有则用之,无则本 task 补一个幂等 append 函数并测试)。
- [ ] Step 2(EXP-2,test first): 断言:①`@channel("sector_momentum", quota=0, floor=0)` 注册但**不在** `scan_config.recall_channels`(生产 9→9 不变);②影子变体跑出长表且生产 `L1_recall_top1000.csv`/`DEFAULT_FLOORS`/`merit_need`/lane 标签 byte 不变(「默认不启用连副作用一起不启用」五点自查逐条断言);③通道逻辑=按 L1 帧的 `sector_mom`(板块 5 日动量)排上涨侧 top-k,不用当日个股涨幅(铁律)。
- [ ] Step 3: 红 → 实现 → 绿;真跑一日影子变体,`channel_audit --variant plus_sectormom` 出表。
- [ ] Step 4(EXP-1,test first): PIT loader:moneyflow 湖分区取 T-4..T 五交易日 `main_net_yi`,缺任一日 → 该票 `UNMEASURED`(**assemble 不联网补**);verdict 行=`{date, code, challenger_pass: sum>0 ∧ positive_days≥3 ∧ ¬main_distortion, ruler: "gap_c1_o2"}` 对 `gate_participation_v3.csv` 人口逐行落。变异:窗口改错一天,UNMEASURED 计数必变(断言)。
- [ ] Step 5: 红 → 实现 → 绿;两实验各 append 首条 observation;registry note 写「成熟门(20 fwd days/50 events/2 regimes)自首条观测起算」。
- [ ] Step 6: Commit `feat(learning): Wave12-T20 EXP-1/EXP-2 数据腿接线(FN-1 修复;registry 观测起账)`。

### T21 · F3 per-channel 端到端 capture

**Files:** Modify: `autoresearch/scan/l2_slo.py`;`tests/scan/test_l2_slo.py`
**Interfaces:** Consumes: T19 护照(recall/l2/pass1/l3/l4 六跳位置)。Produces: `reports/scan/l2_slo.md` 增每通道六跳漏斗表:`recall→L2→pass1→finalist→L4-qualified→E6-top1/BUY`(E6 跳在影子期读 shadow 决策文件,缺文件该跳记 `—`),每跳条件 capture+相对赢家 capture+通道重叠矩阵,分子/分母/as-of/ruler 同屏。

- [ ] Step 1(test first): fixture 三通道小宇宙,断言六跳分母正确(条件口径:上一跳存活集合)、winner 定义引 `MAIN_RULER`+`entry_flag_for()`、报警线用当日之前 expanding P25。
- [ ] Step 2: 红 → 实现 → 绿;真跑出表人查一次。
- [ ] Step 3: Commit `feat(scan): Wave12-T21 per-channel 端到端 capture 六跳漏斗(l2_slo 扩展)`。

### T22 · E6-0 相对标签列(rel_gap_market / rel_gap_sector)

**Files:** Modify: `autoresearch/common/ruler.py`(常量)、`autoresearch/learning/retro.py`(realized_returns/attribute 落列+历史回填);`tests/common/test_ruler.py`、`tests/learning/test_retro_rel_cols.py`(新)
**Interfaces:** Produces: `ruler.REL_MARKET="rel_gap_market"`、`ruler.REL_SECTOR="rel_gap_sector"`;attribution.csv 增两列:`rel_gap_market = gap_c1_o2 − 当日 L0 可交易(entry_tradable)全集等权均值`;`rel_gap_sector = gap_c1_o2 − 同申万一级可交易等权均值`(行业列名以 L1 面板实测为准——premise-check,预期 `industry`)。E6/relative_ledger/F3 引用。

- [ ] Step 1(test first): 合成两行业×四票帧,手算两列断言逐票相等;缺行业票 `rel_gap_sector=NaN` 不猜;`ruler` 常量存在性断言。
- [ ] Step 2: 红 → 实现(基准分母=当日全集,**不含**被 entry 旗剔除票;历史回填追加列不改旧值,与 A3 回填同手法)→ 绿。
- [ ] Step 3: 变异:基准误算成「全市场含不可交易」,断言测试红;还原。
- [ ] Step 4: Commit `feat(learning): Wave12-T22 相对标签两列入账(市场等权主/行业中性辅,历史回填)`。

### T23 · E6-1 统一相对决策层 finalizer(影子核心)

**Files:** Create: `autoresearch/scan/relative_buy.py`、`tests/scan/test_relative_buy.py`
**Interfaces:** Consumes: T19 护照 + `decision_read_model.read_final_ratings` + run_mode + L1 面板(流动性分位/ST 旗)。Produces: `context/scan/<date>/_relative_buy_decision.json`:

```json
{"schema_version": 1, "rule_version": "e6.v1", "mode": "shadow",
 "date": "…", "benchmark": {"market": "L0 可交易全集等权 gap_c1_o2", "sector": "申万一级可交易等权"},
 "candidates": [{"code": "600018", "eligible": true,
   "hard_gate": {"tradable": true, "data_a": true, "contract": true, "no_redflag": true},
   "faces": {"target_align": 0.82, "recall_strength": 0.65, "evidence": 0.74, "risk_safety": 0.55},
   "relative_decision_score": 0.69, "rank": 1, "research_rating": "Hold",
   "expected_abs_gap": {"value": null, "status": "UNMEASURED", "n": 0}}],
 "buys": [{"code": "600018", "basis": "relative", "rank": 1}],
 "blocked": false, "blocked_reasons": [], "excluded": [{"code": "…", "reason": "hard_gate.no_redflag", "detail": "…"}]}
```

**v1 规则(观察前锁定,写进模块头;任何改动=新 rule_version+registry):**
- 硬资格四类:①`tradable`=入场可交易(`entry_tradable`,T+1 收盘可买语义);②`data_a`=当日 A 级数据契约无未解决异常(run_health/StageResult 结构化读);③`contract`=该票 slim/卡/价格断言契约完整(task-book SUCCEEDED ∧ price_claim 无 fail);④`no_redflag` v1 判定=非 ST ∧ `research_rating≠Sell` ∧ 早停原因∉{基本面恶化, 监管/审计红灯} ∧ 当日成交额分位≥P10(L0 可交易内)。
- 四面(各自转当日候选内百分位,等权 Borda 平均;**面内缺失=该面 0.5 并记 `missing`**):
  - `target_align` v1 = composite 分当日百分位(依据:gap 尺下唯一正 unique 超额通道;E3 因子过统一门后按 `rule_version` 增量替换,靠 registry);
  - `recall_strength` = 0.5×pctl(n_channels) + 0.5×max(per_channel pctl)(护照字段);
  - `evidence` = 满卡 1.0 / 早停 0.4 基础分 + intel 在场 +0.2 + 档案在场 +0.2 + price_claim 干净 +0.2,截到 [0,1] 后转百分位;
  - `risk_safety` = 1 − 归一风险分(risk_flags 计数 + 早停风险类 + tripwire 严重度),转百分位。
- 出单:eligible 最高分=第 1 只 `BUY(basis=relative)`;并列决胜 target_align→流动性→代码字典序。第 2 只起:`relative_decision_score` 达已验证阈值 ∧ 同 bucket×regime OOS 历史绝对 gap 扣成本为正——**v1 影子期该门恒不满足(无已验证阈值),第 2 只恒不出**,写死并测试。
- `expected_abs_gap`:同 score bucket×regime 的 OOS 历史均值/CI;样本 <20 → `UNMEASURED`(v1 影子期恒 UNMEASURED,禁止拍数)。
- 全部候选被硬资格否决 → `blocked=true` + 分桶 `blocked_reasons`(影子期只记录,不影响生产发布)。

- [ ] Step 1(test first,契约面): fixture 护照+评级:①普通日出恰 1 只 BUY 且为 eligible 最高分;②Sell/ST/早停-基本面恶化票被硬门排除且 `excluded` 有行;③全否决日 `blocked=true` 零 BUY;④同输入重跑 byte 稳定;⑤四面缺失票记 missing 且取 0.5;⑥v1 第 2 只恒不出。
- [ ] Step 2(test first,变异面): ①把 Borda 平均改成乘积 → 排名断言红;②把硬门放行 Sell → 排除断言红;③把基准写错(含不可交易票)→ benchmark 定义断言红。
- [ ] Step 3: 红 → 实现(纯函数 `build_decision(scan_dir, date, mode="shadow") -> dict` + `main()` CLI;零 LLM/零联网/只读结构化产物)→ 全绿。
- [ ] Step 4: Commit `feat(scan): Wave12-T23 统一相对决策层 finalizer v1(影子;四硬门+四面 Borda;规则观察前锁定)`。

### T24 · E6-2 历史回放 + 前向影子接线 + 影子账本

**Files:** Modify: `autoresearch/scan/post_run.py`(挂点)、`autoresearch/learning/nightly_close.py`(names 增 `relative_ledger`);Create: `autoresearch/learning/relative_ledger.py`、`tests/learning/test_relative_ledger.py`;Create: `docs/research/<实施日>-e6-replay-baseline.md`
**Interfaces:** Consumes: T23 决策文件 + T22 两列。Produces: `context/learning/relative_buy.jsonl`(逐日:BUY code/rank/faces/成熟后回填 `gap_c1_o2`/`rel_gap_market`/`rel_gap_sector`)+ `reports/learning/relative_buy.md`(action coverage=非 BLOCKED 日 BUY_n≥1 占比、影子收益三尺、左尾、旧 OW 基率分账并列——**不与旧 buy ledger 混算**)。

- [ ] Step 1: 历史回放:对 07-31/08-03/08-04/08-05/08-06(+08-07 若有)每日跑 finalizer,断言每日恰 1 只影子 BUY 或 BLOCKED;回放结果+逐面分布落 `docs/research/<实施日>-e6-replay-baseline.md`(认知底片:影子 BUY 的 gap/rel 首读,n 小照实标)。
- [ ] Step 2(test first): relative_ledger 幂等整替、成熟回填(D+2 后)、`basis=relative` 全行、绝对 gap 为负行渲染带「弱市相对最优」。
- [ ] Step 3: 红 → 实现 → 绿;post_run 挂点(每次成功 scan 自动产护照+决策文件——与 T19 Step3 同点确认);nightly names 追加 `relative_ledger`(动态调用面注释同款提醒)。
- [ ] Step 4: registry 登记 `exp_relative_buy_owner`(family=decision,PREREGISTERED;guards 五域:研究=影子 20 日 rel_gap_market 均值与左尾不劣于旧线、决策=action coverage=100%(非 BLOCKED)∧ 契约零错、token=0 增量(纯确定性)、速度=秒级、架构=生产 artifact diff=0;成熟门 ≥20 真实扫描日自首条观测起算)。
- [ ] Step 5: Commit `feat(learning): Wave12-T24 E6 影子前向接线+relative 账本+registry 登记(附历史回放底片)`。

### T25 · C1 确定性 brief 生成器

**Files:** Create: `autoresearch/scan/brief.py`、`tests/scan/test_brief.py`;Modify: `autoresearch/scan/report_sections.py`(publisher 落 `brief.md`)
**Interfaces:** Consumes: 白名单=`decision_records.json`/`finalists.csv`/`run_mode.json`/`market_view.md` 首段/menu_health 行/`_relative_buy_decision.json`/near_miss facts/pinned 结构化结论行(确定性预抽,禁读 details 全文与 trace 大文件)。Produces: `reports/scan/<run>/brief.md` ≤3,000 字节,七节:①市场一句(regime+温度+两尺分歧日提示 R-X1);②漏斗一行;③BUY 结论区(影子期=旧生产结论+「影子 relative BUY」行,显式标非正式;activate 后=正式 BUY 行,含 basis/横截面名次/两类相对基准口径/绝对 gap 口径或 UNMEASURED/硬否决状态;**常驻「旧 OW 基率:9 笔 T+2 胜率 0%」分账行,样本随 buy_ledger 自动更新,与 relative 账分列不连线**——spec E5①);④持仓动作表;⑤风险哨;⑥昨日 delta;⑦欠账红行。

- [ ] Step 1(test first): ①同输入重复生成 hash 一致;②字节 ≤3,000;③相对 BUY 行禁词断言(不得出现「预计上涨/看涨」,绝对 gap 为负必含「弱市相对最优」);④每个数字可在白名单输入中逐项找到(生成器输出附 `sources` 边表供 lint 用);⑤影子期 BUY 区双行显示且影子行带「非正式」标。
- [ ] Step 2: 红 → 实现(纯模板拼装,零 LLM)→ 绿。
- [ ] Step 3: 对 07-31~08-06 六个历史 run 回放渲染 6 份 brief,人工读判「30 秒明白今天买谁/为什么只是相对/何时不能买」;不达标改模板再回放。
- [ ] Step 4: Commit `feat(scan): Wave12-T25 确定性 brief.md 生成器(≤3KB,双模式 BUY 区)`。

### T26 · C2 summary 重排(减层不减料)

**Files:** Modify: `autoresearch/scan/report_sections.py:617 build_summary` 节序与两节改造;`tests/scan/test_report_sections.py`

- [ ] Step 1(test first): ①节序断言:仪表盘(brief ①②③④同源渲染)→投资建议→保送持仓→差一点/弃权 banner 前置;②行业研判节=每行业一行地形首句+链接 `trace/sector_briefs/<行业>.md`,不再嵌研判段全文(字节上限断言:该节 ≤2,000B);③经验节=表格化(id/一句话/guard 状态);④`near_miss` 附录/诚实局限/成本观测原样保留,组合视角节增「旧 OW 基率」分账行(与 T25 ③同源渲染,定义断层不连线)。
- [ ] Step 2: 红 → 实现 → 绿;对 08-06 run 回放:summary 总字节较 47,814B 下降 ≥10KB(断言 ≤38KB)。
- [ ] Step 3: 机器契约回归:`details/`、`finalists.csv`、`decision_records.json`、`shadow_buys.csv` byte 不变;t1/retro 测试全绿(它们不读 summary,§1.4 已证,测试兜底)。
- [ ] Step 4: Commit `refactor(scan): Wave12-T26 summary 决策主线前置+行业节降链接+经验节表格化(−10KB+)`。

### T27 · C3 brief 一致性 lint(self_review)

**Files:** Modify: `autoresearch/learning/self_review.py`;`tests/learning/test_self_review_brief.py`(新)

- [ ] Step 1(test first): ①brief 缺失=fail;②>3,000B=fail;③brief 数字与 `sources` 边表逐项对账,篡改一个评级/基准读数必红(变异验收);④brief 与 summary 的 BUY 数/code/basis/基准读数不一致=fail;⑤**active 模式下**成功 run BUY_n<1=fail、BLOCKED run 不得渲染成成功(mode 从 `_relative_buy_decision.json` 读;影子期该检查跳过并注明)。
- [ ] Step 2: 红 → 实现 → 绿。
- [ ] Step 3: Commit `feat(learning): Wave12-T27 brief 对账 lint(含 active 期 BUY≥1 契约检查)`。

### T28 · C4 入口切换

**Files:** Modify: `autoresearch/scan/publisher.py`(index.md 首行指 brief——premise-check index 生成点)、`.claude/skills/scan-market/SKILL.md`(CP7 播报=读 brief.md 原文+附路径;两处写明 t1/retro 不读 summary)

- [ ] Step 1: 实现+skill 文案(编辑前重读 SKILL.md);锚测试若受影响重锚。
- [ ] Step 2: Commit `feat(scan): Wave12-T28 index/CP7 入口切 brief`。

### T29 · E3 gap 因子工厂第一批(依赖 T4)

**Files:** Modify: `autoresearch/research/factor_lab.py`(harvest 侧新列)、`autoresearch/data/tushare_source.py`(如需 top_list 聚合列);Create: `tests/research/test_overnight_factors.py`;Create: `docs/research/<实施日>-overnight-factors-batch1.md`

- [ ] Step 1(逐因子 capability/PIT 前提): `lhb_net_ratio`(top_list 净买额/成交额,**分「机构席位/其余营业部」两列**——机构反指先例)、`limit_ladder`(limit_list_d 连板高度/首板/晋级,个股化)、`sealed_strength`(湖 OHLC:收盘=最高∧涨停价,`_board_limit` 原语)、`rz_buy_intensity`(已在库,只重验)。每因子先写 PIT 断言测试(信号日=披露日当晚可得,次日不可回看)。
- [ ] Step 2: harvest 落列 → `factor_lab eval`(T4 后即 gap 判据族)出 IC/ICIR/t/两半/decile;**方向假设预注册写进报告头,先写后看**。
- [ ] Step 3: 结论落 `docs/research/<实施日>-overnight-factors-batch1.md`(每因子:判据读数+成熟度+过/不过统一门;负结果照记)。**不改 `_GROUPS`/权重**(过门因子的入组=B 类,经 registry;过门者同时按 e6 rule_version 流程排队进 `target_align` 面)。
- [ ] Step 4: Commit `research: Wave12-T29 隔夜因子第一批 harvest+gap 晋升判据读数(零生产变更)`。

### T30 · E2 三个旧尺结论 gap 重验

**Files:** Modify/Create: `autoresearch/research/ruler_compare.py` 扩三节(或 `research/overnight_evidence.py` 复用其湖原语);`tests/research/test_overnight_evidence.py`;Create: `docs/research/<实施日>-overnight-evidence-gap.md`

- [ ] Step 1(test first): 合成数据锁三节算法:①当日涨幅分桶(≥9.5%/5–9.5%/2–5%/其余)× 次日 `gap_c1_o2` 超额(date-cluster CI);②通道×相位×gap 两侧区间(复用 08-04 复现脚本口径,尺换 gap);③温度计五相位×次日市场 gap 条件分布。变异:分桶边界写错必红。
- [ ] Step 2: 红 → 实现 → 绿;真跑全窗口,报告落盘,顶行写死「影子取证专用,生产铁律未变;启用唯一路径=registry」。
- [ ] Step 3: 报告尾逐条给三个下游触发器状态:E4b 通道设计空间(追涨 gap 结论)、F4 预注册条件(相位 gap 结论)、E3 相位条件特征与否(温度计结论)。
- [ ] Step 4: Commit `research: Wave12-T30 追涨/相位/温度计三结论 gap 重验(带 CI 与下游触发器)`。

---

## Wave 3 · 配套(B 编排 · D 新闻 · GATED B 类)

### T31 · B1 nested_probe 实跑裁决(B2 前置)

**Files:** Run: `autoresearch/research/nested_probe.py`;Create: `docs/research/<实施日>-nested-probe-verdict.md`

- [ ] Step 1: 十项探针逐项真跑(玩具父子 workflow),三态账本落盘;**任一 FAIL/UNTESTED → T32 整条标 SKIPPED(维持主会话派发),不是失败**。
- [ ] Step 2: 结论报告落盘;Commit `research: Wave12-T31 嵌套 workflow 十项探针裁决`。

### T32 · B2 L4 派发下沉(GATED:T31 全 PASS)

**Files:** Modify: `.claude/workflows/scan-market.js`(L4-prep 后 `workflow('l4-stock', …)` × N)、`.claude/skills/scan-market/SKILL.md`(步骤 4 改述+回滚附录)、`.claude/skills/scan-market/scan_config.jsonc`(`performance.l4_dispatch`,缺省 `main_session`);registry 登记 `exp_l4_dispatch_nested`(family=speed;先关 `exp_l4_full_parallel` 再开——同 family 串行)

- [ ] Step 1(test first): js AsyncFunction 解析探针(禁 `node --check`);`l4_dispatch` 开关三值校验(`main_session|nested`,未知值 raise);task book/失败重放语义断言(子失败→重放→仍失败 assemble 阻断)。
- [ ] Step 2: 实现;首跑 N=10 真实扫描对照:完成率 N/N、评级 schema 零错、主会话份额(usage_harvest)、总 token、墙钟 五数记 registry observation。
- [ ] Step 3: **回滚杆(两处联动,写准)**:①config `performance.l4_dispatch="main_session"` ②SKILL.md 步骤 4 回滚附录段;文档明注「config 单独拉不动主会话行为」。
- [ ] Step 4: Commit `feat(workflow): Wave12-T32 L4 派发下沉(nested;registry speed 实验;两处联动回滚杆)`。

### T33 · B0.5+B3 配置 materialize 与尾差清零

**Files:** Modify: `autoresearch/scan/user_config.py`(materialize→`context/scan/<date>/_resolved_agent_config.json`:闭集校验+缺生产必填 role fail-fast+空 `{}` fail)、`autoresearch/scan/frame.py`(echo 携 resolved)、`.claude/workflows/t1-review.js`(空 cfg throw,比照 scan-market.js:22-24)、三 workflow(AG() 消费 resolved 值;AGENT_DEFAULTS 降兜底)、`autoresearch/trace/usage_reconcile.py`(对 resolved 对账;缺任一实际派发 role → `ok=false`)、`autoresearch/learning/self_review.py`(workflow_literal_lint 扩到 `.claude/skills/**/*.md` 的 `Agent(model=`/`effort=` 字面量,豁免注记语法沿用)、`tests/test_agent_defs.py`(gp_shell 三文件 SHELL_DEFAULTS AST 相等断言)
**并含:** `apply_to_scan_config()` 处置——premise-check 后倾向删除+docstring 更正(生产零调用,08-08 复核)。

- [ ] Step 1(test first): ①resolved 文件 schema:显式列全 12+ role、未知 role/字段 raise、空 `{}` raise;②t1-review.js 忘传 cfg → throw(现状静默吃 'high' 的假阴转正);③reconcile:造 ens_review 与 l4_card effort 不同的 echo 必须分行报(配合 T34);④lint:lite-playbook.md:178 型字面量必红、加豁免注记必绿;⑤AST 相等断言:三文件 SHELL_DEFAULTS 改一处必红。
- [ ] Step 2: 红 → 实现 → 绿(逐项小 commit 亦可,总提交 ≤3 个)。
- [ ] Step 3: Commit `feat(config): Wave12-T33 resolved agent config 单一事实源+配置尾差清零(t1 守卫/lint 扩面/gp_shell AST 锁)`。

### T34 · B4 role 归因修

**Files:** Modify: `.claude/workflows/l4-stock.js`、`.claude/workflows/scan-market.js`(ens_review/l3_repair 派发时 task book 子记录写 `role`)、`autoresearch/trace/usage_reconcile.py`(优先按 task book role 归行);`tests/trace/test_usage_reconcile.py`

- [ ] Step 1(test first): 同 agentType 双 role 场景,断言按 task book role 分行;缺 role 字段退回集合断言(兜底)。
- [ ] Step 2: 红 → 实现 → 绿;Commit `fix(trace): Wave12-T34 usage_reconcile 按 task book role 归因(ens_review 蒙混转正)`。

### T35 · D1 catalog 通电(I 类)

**Files:** Run+Modify: `autoresearch/news/catalog.py`(inventory 真跑修出的实际问题就地修)、`autoresearch/learning/nightly_runner.py`(快讯 ingest:东财/新浪/财经早餐三源,B 级)、`autoresearch/scan/prelude.py`(覆盖/freshness/非空率报表行);`tests/news/test_catalog_live_shapes.py`(新,离线 fixture)

- [ ] Step 1: `python -m autoresearch.news.catalog inventory` 首跑(~1,891 stock_news_em 分片+anns_d/fallback),历史分片 `first_seen_basis=snapshot_inferred`;manifest 与分片逐源对账断言。
- [ ] Step 2: 夜间 ingest 接线(消费者继续关闭;late-arrival fixture 不泄漏断言已有,补 ingest 幂等断言)。
- [ ] Step 3: prelude 报表行;连续 5 晚非空验收开始计数。
- [ ] Step 4: Commit `feat(news): Wave12-T35 catalog 通电(inventory+夜间快讯 ingest+prelude 可见;消费者仍关)`。

### GATED 组(设计冻结在 spec,触发条件到达才开工;每件独立 registry)

- [ ] **G1 · E4b 热度影子通道注册**(触发:T30 追涨 gap 结论落盘 ∧ T29 首读):`youzi_lhb`/`limit_ladder`(quota=0,floor=0 影子)+`pop_surge`(另需 E4a ≥20 日);与 heat 通道 Jaccard≥0.3 二选一;E1 同款五点零副作用自查断言照抄。
- [ ] **G2 · F4 相位 quota spec 生成器**(触发:T30 相位 gap 结论满足「上涨侧整区间<0」):自动产 `recall_quota_regime` PREREGISTERED spec(consensus 触发器手法,含会变的量断言),人批激活。
- [ ] **G3 · F2-B 护照摘要进 L3 prompt**(触发:现存 `exp_20260729_l3_hard_constraint_f` 生命周期处置完毕——同 family 串行):影子对照,I 步已保证 byte diff=0。
- [ ] **G4 · D3/D4/D5**(触发:T35 连续 ≥5 晚快讯非空):intel 先读目录(真实 tool telemetry 计数)→L3 第二源→宏/中观 pack 摘要行,逐件 registry。
- [ ] **G5 · E6-activate 切换包**(触发:`exp_relative_buy_owner` ≥20 真实扫描日影子 ∧ 五守卫 PASS ∧ 人工 approve/activate 记录在案):一揽子 commit——①publisher 只认 `_relative_buy_decision.json`(缺失/与 decision_records 不一致=fail);②`mode=active`:成功 run `BUY_n≥1`,全否决=run BLOCKED(GATE4 语义,不得发布 0 BUY 成功);③sentinel 改义=风险预算档(至少 3 只非 pinned 候选完成横向比较;pinned 照常全做);④L4 `rubric_rating` 降 `research_rating` 证据字段(scan 内不再独立渲染为买入信号;持仓 Sell/UW/Hold 语义不变);⑤`decision_schema_version` 断层落 ruler.py 常量;⑥zero_buy_ledger 冻结为 legacy,relative_ledger 转正为日级主账。**回滚杆:config `decision.owner="legacy_ow"`(新开关)+ publisher 双认路径保留一个版本窗;两处联动写进 SKILL。**

---

## 排序与依赖(照 spec §9 两层)

```
T0.* → T1→T2→T3(E4a,最先)
     ∥ T4..T15(批A;T4 优先)
T16→T17/T18(人批)→T19→T21
     ∥ T20、T30(可与批A 并行)
T22→T23→T24(E6 影子起账,依赖 T4+T19)
T24→T25→T26→T27→T28(批C,依赖 T11 的 near-miss 修)
T4→T29(因子);T30→G1/G2
T31→T32(GATED);T33/T34/T35 随后;G3/G4/G5 按触发
```

## 计划级验收(全部 task 完成后一次跑)

- [ ] 全测绿(基线 2509+,新增测试全部纳入);`git status` 干净(pinned.jsonc 除外,不碰)。
- [ ] 变异探针清单复核:T4 错腿/T5 旧旗/T14 lint/T20 窗口/T23 三变异/T25 禁词/T27 篡改——逐条「反向必红」记录在各 commit message。
- [ ] 一次真实扫描冒烟:护照+影子决策文件+brief+新 summary+relative 账本首行全部出现;prompt/生产 finalists/DEFAULT_FLOORS byte 与改前一致(影子零副作用总检)。
- [ ] spec §10 验收总表逐条对号(A/B/C/D/E/F 六段)。
- [ ] registry 快照:`exp_relative_buy_owner`/`exp_l4_dispatch_nested`(如开)/EXP-1/EXP-2 观测计数 >0;`candidates.py --check` 通过。

---

_依据:`docs/specs/2026-08-08-wave12-seven-topics-design.md`(c390bea 修订版,§13 已裁六条);证据底片见 spec §1。实施中发现 spec 与源码冲突以源码为准并回写 spec 勘误。仅供研究,非投资建议。_

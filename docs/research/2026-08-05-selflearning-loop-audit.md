# 自我学习闭环全景审计(2026-08-05)

> 回答用户问题「自我学习的反馈现在是怎么做的」。全部机制描述带文件锚点,实测读数取自
> 2026-08-05 当日全扫(run `20260805_2251`)与当晚账本。本文只审计**现状**;改造方案见
> `docs/specs/2026-08-05-wave11-ruler-config-l4concurrency-skills-design.md` 批A。

## 0. 一句话总览

自学习 = **两条复盘环**(快环 t1_review D+1 · 慢环 retro D+2)把已实现收益对齐回报告,
产出**一条自动腿**(权重重标定)+ **一组人批腿**(lessons/proposals/实验注册表),
再经 **注入面**(📐🔁🚪 当日件、L3 校准块、档案前科卡、判例索引)流回下一天的 prompt;
外圈由 **prelude 建议行 + nightly 跑批** 负责"腿有人踢",由 **GATE4 self_review +
各账本** 负责"学了有没有用"。

```
        ┌─────────────── T 日全扫(scan-market)────────────────┐
        │  L0-L2 确定性 → L3 精排 → L4 决策卡 → L5 报告        │
        └──────┬────────────────────────────────────┬─────────┘
               │ T+1 晚(nightly_close 自动备料)      │ T+2 晚(自动备料)
        ┌──────▼──────┐                       ┌──────▼──────┐
        │ 快环 t1_review│ 判断层:真选票准不准    │ 慢环 retro   │ 召回层:全市场谁涨了没进池
        │ 尺=cc1/oc1   │ → t1-review workflow  │ 尺=fwd_2_oc │ → scan-retro skill(LLM 诊断)
        └──────┬──────┘   (LLM 诊断,人工触发)   └──────┬──────┘
               │                                      │
               │                    ┌─────────────────┼──────────────────┐
               │                    ▼                 ▼                  ▼
               │             自动腿:权重重标定   lessons.jsonl      proposals.jsonl
               │             (recalibrate_and_log) (语义经验,人批)   (结构提案,人批)
               │                    │                 │                  │
               ▼                    ▼                 ▼                  ▼
        ┌────────────────────────────────────────────────────────────────────┐
        │ 注入面(下一天生效):weights.json → L1 排序;📐🔁🚪 当日件 →        │
        │ _l4_shared_instructions.md;L3 校准块;档案前科卡;precedents FTS   │
        └────────────────────────────────────────────────────────────────────┘
```

## 1. 评判尺现状(本次改造的靶心)

**主尺 `fwd_2_oc`**(2026-07-10 用户裁定,已于 **2026-08-05 被新裁定取代**,见设计稿批A):

- 定义单点:`autoresearch/research/factor_lab.py:270 forward_returns()`;
  `fwd_2_oc = close[D+2]/open[D+1] − 1`(D+1 开盘买 → D+2 收盘卖),`:291`。
- 同族列(`FWDS`,`factor_lab.py:551`):`fwd_1_cc`(T 收→T+1 收)、`fwd_1_oo`(T+1 开→T+2 开)、
  `fwd_2_oc`(主)、`fwd_5_oc`/`fwd_10_oc`(降参考不删);另有 `hi_2_oc`(D+1/D+2 最高价触价尺,
  与 fwd_2_oc 成熟配对)与 `buyable`(D+1 一字涨停=买不到,剔除)。
- IC 计算时 `fwd_2_oc.clip(-0.30, 0.30)`(2 日容 10cm 两连板,`:623`);校准面板
  `_build_calib_panel(label_col="fwd_2_oc")`(`:691`)。
- **消费面 = 30+ 文件字符串散写**(`grep -rl fwd_2_oc autoresearch --include='*.py'`):
  research 侧 consensus/channel_audit/factor_lab/sector_top3_backtest/candidates;learning 侧
  channel/pinned/shrink_replay/evidence_manifest/l3_audit/catalyst/stage_eval/abstention/
  ensemble/buy_ledger/sentinel_audit/rejection_attribution/l3_marginal/gate_attribution/
  lesson_yield/zero_buy/t1_review/earlystop/cross_calib/precedents/gate_ledger/retro;
  scan 侧 l2_slo/report_sections/recall/channels。**没有中央常量,换尺=摸 30 个文件**。

**快环自有两把尺**(`t1_review.py` 模块头,明示"勿混,项目有尺子错配的疤"):

- 主尺 `cc1` = T 收盘 → T+1 收盘(判断对错的信息尺);参考尺 `oc1` = T+1 开→收(可实现口径)。
- v2 判定(2026-07-17 调研落地):行业中性超额 + 截面稳健 z(1.4826×MAD,盖帽 ±3);
  `_Z_DIR=0.5` 且 |行业超额|≥0.8pp 双门,`_Z_SURPRISE=1.5` 必诊,绝对地板 `_MIN_EXCESS=0.008`。
  固定 pp 阈的病(暴跌日满地"准"/平静日太钝)已治。
- 模块头原话:「持仓/权重校准主尺仍 = fwd_2_oc,本环**不动它**」——即现状是 **cc1/oc1/fwd_2_oc
  三尺并存**,各喂各的腿。

**2026-08-05 新裁定**(本次 brainstorm 问答):评判尺改**隔夜尺 `gap_c1_o2 = open[T+2]/close[T+1] − 1`**
(T+1 收盘买 → T+2 开盘卖)。该列**目前不存在**,全链切换方案见设计稿批A。

## 2. 快环 t1_review(D+1,判断层)

- **裁定边界**(fb_20260717_001):只考 **T 报告的真选票**;`_NON_GENUINE_LANES =
  {pinned, watchlist_trigger, carryover}` 一律不进成绩(后两种 lane 已退役,留集合兼容存量)。
  只做 T→T+1 相邻交易日;`_EPOCH="2026-07-10"` 之前的旧 swing 卡不回补(尺子语义不同)。
- **确定性段**(`build_scorecard`,零 LLM):逐票 `cc1/oc1/hi_oc/excess/excess_ind/z/verdict
  (准|不准|中性)/surprise/needs_diag`;`needs_diag` = 不准 ∪ 惊奇 ∪(准但 |z|≥1.0)。
  产物 `context/scan/<T>/t1_review/{scorecard.csv,scorecard.md,diagnoses.json,report.md,done.json}`,
  账本 `context/learning/t1_review.jsonl`(按 T 日幂等整替)。
- **LLM 段**(`.claude/workflows/t1-review.js`,2 agent,07-17 裁定勿每票 fan-out):
  合诊(`t1_diag`,≤13 卡跨票对比诊断)+ 综合官(`t1_synth`)。产出喂 **prompt 侧**经验/提案。
- **与慢环分工**:retro 喂**权重**(唯一自动腿),t1 喂**经验**(人批);两者复用 factor_lab
  取数/日历原语,无平行实现。

## 3. 慢环 retro(D+2,召回归因)

- **归因**(`retro.py attribute_frame`,纯函数):全市场已实现收益 × L1 全打分面板 × 报告买单
  → 每票一桶:`hit / recalled_cut(L2-L3 切掉) / missed_l1(召回线外) / missed_l0(选集外) /
  误买`(`bucket()` `:79`,五桶版 `bucket_5` `:105`)。产物 `attribution.csv + retro_input.md`。
- **成熟判据**(`pending_days` `:429`):有 L1 面板 + 有报告 + 无 `retro/done.json` + **D 的 fwd
  已实现**(fwd_2_oc → D+2 收盘后)。`refresh_attributions` 对已 done 但 fwd_5/10 未熟的老日
  幂等补列(治"买单 ledger 永远 —")。
- **闭环收尾**:`mark_done`(`:1053`)写 `done.json` 并触发 `decay_lessons`(经验时间衰减)。
  ⚠️ **pending 的语义 = 诊断段欠账**:归因可以自动补(nightly),但只有 scan-retro(LLM 诊断)
  走完才 mark_done——所以 `retro pending` 列表在归因补完后**仍然列着**。08-05 实测:6 日
  (07-27..08-03)归因全补齐、`retro_input.md` 备好,但诊断烂尾,prelude 汇总屏原话
  「已备料但未收尾(无 done.json)…别让欠账攒着」。
- **LLM 段**(scan-retro skill):读 retro_input.md 做系统性病因诊断 + 触发自动重标定 + 蒸馏
  经验/提案。

## 4. 唯一自动腿:权重重标定

`retro.recalibrate_and_log`(`retro.py:988`):

1. `factor_lab.extend_plan()` 增量续面板(修 pr_20260716_001「calibrate 只吃冻结 plan.pkl
   → 连续 4 次 NO-OP 空转两周」的老病;失败打 stderr 不阻断,退化为冻结面板校准);
2. `feedback_store.snapshot_weights()` 快照旧权重(`weights.<sha>.json`,供回滚);
3. `factor_lab.calibrate()`(多日滚动+收缩,绝非单日)重写 `context/factor_lab/weights.json`;
4. `log_change` 记 changelog(前后 sha + top 权重变化)。

**心跳探针**:`changelog_ledger.heartbeat`,prelude 每日打(08-05 实测:「权重自动腿心跳 ✓:
最近 2026-07-28 f3440ac5→543d67f8(面板 124 日)」)。这是 07-16 事故(「自动学习的腿必须有
一个会变的量做断言,否则它死了也像活着」)的直接产物。

⚠️ **regime 分桶权重当前空转**:08-05 prelude 实测 B 级降级
「weights_regime[range]:请求 regime='range' 但 weights.json 无该块(现有 regimes=空)→ 回落
flat;补法:factor_lab calibrate-regimes(注意先过两半符号一致门)」。`--regime-aware` 旗开着、
分桶块不存在,每天回落 flat——最近一次提交 fd573d3 已把这个回落从静默改成记账,但**块本身
仍未生成**。

## 5. 记忆库与注入面

**存储**(`feedback_store.py`):

- `context/knowledge/lessons.jsonl` —— 语义记忆(策展后的经验规则,带 confidence/guard 谓词/
  退休机制;`mark_done→decay_lessons` 时间衰减)。08-05 实测:5 条 active,1 条带 guard。
- `context/knowledge/proposals.jsonl` —— 结构性改动提案(新因子/门槛/prompt 规则),满 20
  交易日未裁在报告尾持续提醒。08-05 实测:**16 条 open,`pr_20260714_006`(P0 intel 捏造)已挂 22 天**。
- `context/precedents.db`(`precedents.py`)—— sqlite FTS5 判例索引:几百张历史决策卡
  (`card:<date>:<code>`)+ lessons(`lesson:<id>`),供 L4 检索"这类 setup 历史上怎么样";
  fts5 不可用自动降级 LIKE,幂等按日增量。

**注入点**(学习流回生产的所有通道):

| 通道 | 生产者 | 消费者 | 实测(08-05) |
|---|---|---|---|
| `weights.json`(+regimes) | recalibrate 自动腿 | L1 复合分排序 | 心跳 ✓;regimes 空转 |
| 📐🔁🚪 当日件 → `_l4_shared_instructions.md` | `l4_card shared`(`scan/l4/prompts.py`,Wave5 ④B 修「有读者无生产者」) | 全部 L4 卡 | 📐触达率55%/🔁trend翻案22%/🚪主力真在 拦32对47%错杀38% |
| L3 校准块 | `cross_calib`(lanes=9 × gates=5) | l3-rank prompt | 「OW-lean 确认率暂无鉴别力(基准 1.9%)先积累」 |
| 档案前科卡 | `precedents` 判例聚合 + dossier δ 回写 | L4 卡 P0 简报 | 300857/603893/688766 δ 回写 ✓ |
| lessons 注入 | `feedback_store`(`_LESSON_CAP` 截断,cap=8) | L5 报告「经验」节 + 卡 | 5 条 active 注入 |
| 哨兵/菜单建议行 | menu/zero_buy/gate ledgers | prelude 汇总屏(人) | 「重旗+连败≥7硬压→L4 预算10」 |

## 6. 评估仪器(「学了有没有用」的账本层,全确定性)

| 仪器 | 量什么 | 08-05 读数 |
|---|---|---|
| `stage_eval` | 逐阶段 edge:L2 keep/cut lift、L3 finalist lift(真选口径,pr_20260716_002 剔保送)、L4 评级单调 rank-IC、Tier-3 辩论差 | 喂 retro_input |
| `process_score` | 过程分/结果分分离:逐 finalist 6 项机检(数字回环/盲读微pass 标签…);结果分=fwd_2 | 07-24 教训:0/11 命中→把散文铁律升级成机器契约标签行 |
| `lesson_yield` | guard 型经验的反事实 Δpp 累计(复用 `retro.mtm_check_guards`,apply=False 重放);n≥20 且 cum_delta≤0 → 提名 retire(人批) | check#5 有弹药 |
| `paper_nav` | 真实 vs 影子(门不拦最想买3只) vs sized vs 市场等权;主表 hold=2,副表 hold=10 对照 | 真实 −0.24% vs 影子 −4.98% = 门价值 +4.7pp |
| `abstention_ledger` | 日级弃权裁决(0买对不对) | 滚动 10:FALSE 3 · NEUTRAL 7 · **CORRECT 0** |
| `zero_buy_ledger` | 0买连败对照 | 29 日 |
| `gate_ledger`/`gate_attribution` | 每道门拦对率/错杀率(v3 outcome) | 业绩真兑现:拦13 对36% 错杀43% |
| `ensemble_ledger` | ≥OW/SELL 双复核折回的事后对错 | 18 folds |
| `channel_ledger` | 召回路 unique 超额(相位条件性见 docs/research/2026-08-04) | momentum 26日 −1.07% 混合物 |
| `l3_audit_ledger`/`l3_marginal`/`rejection_attribution`/`earlystop_ledger`/`pinned_ledger`/`catalyst_ledger`/`sentinel_audit`/`buy_ledger`(📐)/`journal` | L3 翻案审计/边际/拒绝归因/早停/保送/催化/哨兵/触价校准/流水总账 | 各自 reports/learning/*.md |
| `self_review`(GATE4) | 25 项报告自检(评级超rubric/违背经验/行业过度集中/空泛话术/卡片契约/价格断言对账…),warn 两次升 binding | 08-05:fail 0 / warn 13 |

## 7. 治理面(行为变更的唯一生产入口)

`experiment_registry`(`context/learning/experiments/registry.json`)+ `promotion`(研究/决策/
Token/速度/架构五守卫)+ `rollback_watch`(只推荐不自动改生产)。状态机
`PREREGISTERED → RECOMMENDED → APPROVED(人) → ACTIVE(人) → STABLE_CANDIDATE | ROLLBACK_RECOMMENDED`;
`IMMATURE/UNKNOWN/FAIL` 不可批;0 BUY 不是失败也不是松门理由;每 family 同时至多一个 ACTIVE。
08-05 现状:**全部 challenger 影子态**;run observation `IMMATURE(real_scans 7/10)`。

## 8. 调度面(腿谁来踢)

- **prelude 建议行**(每日,确定性):t1_pending / retro 备料未收尾 / 📐🔁🚪 当日件 / 档案 SLO /
  提案满期提醒——「欠账从『什么都没做』变成『数据齐了,就差你看一眼』」。
- **nightly_close**(Wave7 §6 P5):收盘后确定性欠账补跑(retro 归因+备料**成对**落、t1 记分卡
  backfill、账本刷新);「治全项目最贵的病:腿没人踢」。单步 suppress 不连坐。
- **nightly_runner**(下一波批5,08-04 刚上):在 nightly_close 外面套运行安全——互斥锁(stale
  回收)/原子心跳/幂等键/交易日历 catch-up/超时退避/run ledger。
- **LLM 诊断段仍人工**:scan-retro / t1-review workflow 要人触发——这是设计边界不是遗漏
  (「"为什么错、怎么改"要人在场」),但实践里成为最大欠账源(见 §9-①)。

## 9. 病灶清单(按优先级,全部带当日证据)

1. **诊断段烂尾结构性发生**:确定性备料全自动了,LLM 诊断 6 天没人跑(07-27..08-03)。
   `pending` 语义混淆加剧(归因已补仍列 pending)。→ 设计稿 A7:pending 拆两条 + scan-retro
   批量补诊断模式。
2. **regime 权重块空转**:`--regime-aware` 开着、`weights.json.regimes` 为空,天天 B 级降级
   回落 flat。要么生成(过两半符号一致门)要么显式退役旗子。→ A4。
3. **弃权账本 CORRECT 0/10**:弃权从未被证明弃对过(FALSE 3 次,最近 07-21 600188 +5.6pp)。
   连续 0 买 11 日背景下,这是「门总量救命 ∧ 尾部漏肉」两真并立(Wave10 裁定)里"漏肉"侧
   唯一持续报警的仪器,但它只有裁决没有下游动作。
4. **三尺并存,馈路不同**:cc1(快环判断)/fwd_2_oc(权重+全账本)/oc1(参考)各喂各的腿;
   08-05 新裁定 gap_c1_o2 之后若只改一处,尺子错配的疤会复发。→ A1/A2 中央常量。
5. **注入面广、对账窄**:lessons/📐🔁🚪/前科卡注进 prompt,但「注入是否改变行为」只有
   guard 型经验有证伪器(lesson_yield);非 guard 经验、校准行、前科卡均无效力度量。
6. **提案裁决积压**:16 条 open,P0(intel 捏造)挂 22 天;满期提醒有了,裁决通道(人)仍堵。
7. **换尺成本高**:30+ 文件字符串散写(§1),无中央常量——本次换尺必须顺手把这个结构病治掉,
   否则下次还是摸 30 个文件。

## 10. 本审计的边界

- 未审计 dossier 学习子环(季度对账/δ 回写细节)——机制在 Wave2/3 设计稿,与评判尺无关。
- 未做 lessons 全量逐条效力回放(只核对了仪器存在性与当日读数)。
- `sector_memo`/`sector_ledger` 行业方向记账在场但未展开(sector_calls.jsonl 今日记 8+3 条)。

# intel 死票门 · 离线回放(A3 证据门)

- 日期:2026-09-26 · 设计:`docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md` §4 A3 · 计划:`docs/superpowers/plans/2026-09-26-daily-engine-batch6-intel-gate.md` Task 2
- 仪器:`python -m autoresearch.scan.l4.intel_gate replay`(提交 `b9c4552`);谓词 `intel_gate.decide_row`(提交 `91ad59e`)
- 性质:只读冻结 staging,零 LLM、零网络;旋钮 `l4_intel.skip_when_dead` 生产仍为 `false`

## 1. 停机规则(预注册;与回放代码同一提交 `b9c4552` 写死,早于任何真跑回放)

判死 = 主力净额 `main_inflow_yi` < 0 ∧ `cmf_20` < 0 ∧ `obv_mom_20` < 0(L1 因子行)∧ 无 📅 日历催化 ∧ 无近 10 日事件(回购 / 增减持 / 调研)∧ 非 📌 ∧ 非 composite 证据席;任一因子缺 → 不判死。

| 规则 | 来源 | 过门条件 |
|---|---|---|
| 计划规则 | plan Task 2 | 被判死票里 intel 事件段出现净分 ≤ −1 的 T0 行的占比 ≤ 10%(**最坏情况**:无法测的票算进分子)∧ 终评 ≥Hold 的占比 ≤ 5% |
| 设计规则 | spec §4 A3 | 被判死票里**一次也没有** ≥OW 终评,也没有 T0 负面(含无法测) |
| 无命中 | — | 回放期一只票都没判死 → 上线无收益,同样不上线 |

两条都过才 `launch_eligible`,才进批 6 Task 3(派发接线,冻结窗后)。改任何阈值 = 重新立案、重新回放,不在看完读数后调。

## 2. 回放集

与 spec 的「8 个真跑日 81 张卡」逐一对上:冻结 staging 同时带 `finalists.csv`、`L1_scored_full.csv`、`calendar.csv`、`L3_catalyst.csv`、`_final_ratings.json` 与 intel 稿的全部 run。

| run | finalist | 判死 |
|---|---|---|
| 20260826_2120 | 10 | 0 |
| 20260901-0902_0154 | 11 | 0 |
| 20260907-0907_2233 | 10 | 0 |
| 20260909-0909_2209 | 12 | 0 |
| 20260910-0910_2211 | 10 | 0 |
| 20260911-0912_1248 | 11 | 0 |
| 20260915-0915_2242 | 6 | 0 |
| 20260917-0917_2152 | 11 | 0 |
| **合计** | **81** | **0** |

81 份 intel 稿全部是带「时效窗」列的新 schema(T0 可测)。

## 3. 读数

```json
{"n_runs": 8, "n_checked": 81, "n_dead": 0, "n_dead_t0_negative": 0, "n_dead_t0_unmeasured": 0,
 "n_dead_rated_ge_hold": 0, "n_dead_rated_ge_ow": 0, "saved_intel_per_run": 0.0,
 "plan_rule_pass": false, "spec_rule_pass": false, "launch_eligible": false,
 "stop_reason": "回放期一只票都没判死 —— 上线无收益"}
```

逐票判决原因:有一线不为负 38 · 📌/证据席恒派 37 · 近 10 日事件 4 · 📅 日历催化 2 · 判死 **0**。

**结论:按登记规则不上线 —— 批 6 Task 3(派发接线)不做;旋钮保持 `false`。** 预计每场省 intel 张数 = 0 / 8 = 0。

## 4. 为什么一张都没命中(描述,不是调参)

对 38 只「非 📌、非证据席、无催化」的 finalist 数负号:

- 三线里为负的条数:0 条 20 只 · 1 条 12 只 · 2 条 6 只 · **3 条 0 只**;
- 各线「不为负」的只数:`main_inflow_yi` **36/38** · `cmf_20` 27/38 · `obv_mom_20` 27/38 —— 主力净额这一线几乎总是非负(finalist 本身就是 L2/L3 按资金面挑上来的),三线合取在 finalist 人口上结构性地凑不齐;
- 这 38 张卡里 32 张早停,其中 8 张的早停因含「资金」;这 8 张在 L1 因子行上只有 1–2 条线为负。

也就是说,spec 的动机观察(「停因『资金流出』可由 slim 数字判」)说的是**卡读的 slim 资金数字**,而本谓词按计划只读 **L1 因子行**(当日截面),两者不是同一组数:卡判「资金流出」的票,在 L1 行上并不三线同负。

要让这道门有收益,只能换口径(例如改读 slim 的多日资金字段、或改成 2-of-3),那是**新的预注册设计 + 新的回放**,不在本波、也不在看完读数后临时改。停机规则把这件事留给用户裁。

## 5. 复现

```bash
uv run --no-sync python -m autoresearch.scan.l4.intel_gate replay \
    reports_claude/scan/20260826_2120/trace/staging \
    reports_claude/scan/20260901-0902_0154/trace/staging \
    reports_claude/scan/20260907-0907_2233/trace/staging \
    reports_claude/scan/20260909-0909_2209/trace/staging \
    reports_claude/scan/20260910-0910_2211/trace/staging \
    reports_claude/scan/20260911-0912_1248/trace/staging \
    reports_claude/scan/20260915-0915_2242/trace/staging \
    reports_claude/scan/20260917-0917_2152/trace/staging \
    > intel_gate_replay.csv 2> intel_gate_replay.summary.json
```

逐票 CSV 列:`run, code, dead, note, final_rating, intel_schema, intel_events, intel_t0_negative, intel_t0_min`;汇总 JSON 在 stderr。

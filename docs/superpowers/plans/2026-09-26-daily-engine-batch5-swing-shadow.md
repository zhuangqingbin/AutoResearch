# 日报引擎 · 批 5:10 日尺(SWING_RULER)预注册普查 + brief 观察席(影子)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改任何评级、BUY、主尺的前提下,让 10 日尺(`common.ruler.SWING_RULER = "fwd_10_oc"`)有一份**预注册**普查读数(H1–H4)和一条每日**影子**产品面(观察席),为冻结窗后的 B4 裁决攒 ≥40 个交易日证据。

**Architecture:** 账本与阶段尺已经算 `fwd_10_oc`(`outcome.py` 的 `t10/fwd10_verified`、`populations.UNIVERSE_RULERS`、`stage_rulers.csv` 的 `RULER_BLOCK[fwd_10_oc]=10`),所以 B1 只剩「把 10 日成熟状态显式化」。B2 是一台新的只读普查仪器 `research/swing_ruler_census.py`,复用 `overnight_census.core` 的 `cell_stats/judge`、`common.forward_returns`、`common.stats.family_adjustment`,预注册 spec JSON 过 `contracts.research_experiment.validate_spec`。B3 在 summary 加 §12「10 日观察席」(brief 3KB 预算只加一行指针),零 LLM,从 finalists + 卡评级 + 入场行现算。

**Tech Stack:** pandas、pytest、既有研究原语;不新增数据源。

**Spec:** `docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md` §5 B1–B4。

## Global Constraints

- **不动 `MAIN_RULER`、不动 E6、不动卡片契约、不动 L3/L4 prompt**(冻结窗)。观察席只展示,不进任何门、账本 role、或 prompt。
- 预注册**先写后跑**:spec JSON 提交后才能跑普查;看完读数不许改阈值(要改 = 新 experiment_id)。
- 读数写 `$RPT/research/swing_ruler/<experiment_id>/`,**不写 run 目录与 staging**。
- 观察席的每个数字都能在 `finalists.csv` / `_final_ratings.json` / `stage_rulers.csv` 里找到出处(brief 边表同款 `_src` 记账)。
- 提交末尾 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

## Review Focus

1. **窗口重叠**:相邻分析日的 fwd_10 窗口重叠 9 个 session,逐日 delta 不独立 → 普查的显著性必须用块 bootstrap(`research.robustness.block_sensitivity`,块长 10)而不是逐日 t 检(Task 2)。
2. **未成熟行**:D+10 未到的行必须从分母剔除并计数,不能当 0 或 NaN 静默参与均值(Task 1/2)。
3. **📌 污染**:普查人口剔 📌 保送票(`edge_census.pinned_codes`),否则持仓票会把「finalist」格污染(历史教训 `retro-l3-edge-contaminated-by-pinned-20260716`)(Task 2)。
4. **观察席空日**:无 ≥Hold 非 📌 finalist 的日子,§12 必须写「无」且 brief 指针行照出,不能整节消失(否则读者分不清「没跑」与「没有」)(Task 3)。
5. **主尺字样**:观察席文案不得出现「BUY/买入/可买」——它是 10 日尺影子,不是决策;lint 锚(Task 3)。

---

### Task 1: 10 日尺成熟状态显式化(B1)

**Files:**
- Modify: `autoresearch/scan/outcome.py`(`compute_outcome` 输出 `outcome_status_swing ∈ {MATURE_10, PENDING_10, MISSING_MARKET_DATA}` 与 `t10`;`recommendations.csv` 追加两列,旧行缺列按空处理)
- Modify: `autoresearch/scan/ledger_views.py`(runs 视图加 `n_mature_10`)
- Test: `tests/scan/test_outcome.py`

- [ ] **Step 1: 写失败测试**

```python
def test_outcome_marks_swing_maturity_separately(tmp_lake_10_sessions):
    """D+2 成熟不等于 D+10 成熟:主尺 MATURE 而 10 日尺 PENDING_10 必须同时成立。"""
    fr, meta = outcome.market_frame("2026-09-10", lake_daily=tmp_lake_10_sessions, today="2026-09-15")
    assert meta["outcome_status"] == outcome.MATURE
    assert meta["outcome_status_swing"] == "PENDING_10" and meta["t10"] is not None
    fr2, meta2 = outcome.market_frame("2026-09-10", lake_daily=tmp_lake_10_sessions, today="2026-09-30")
    assert meta2["outcome_status_swing"] == "MATURE_10" and meta2["fwd10_verified"] is True
```

- [ ] **Step 2: RED**(`KeyError: outcome_status_swing`);**Step 3: 实现**(在 `market_frame` 已有 `t10`/`fwd10_verified` 的基础上派生状态字段;`compute_outcome` 行级写入;`nightly_close` 第 1 步无需改——`fill` 对已 MATURE 行在 D+10 到期时**再补一次**:现有「D+2 一到就永久跳过重算」的 `complete` 判据改为「主尺 complete ∧ swing complete」两枚,旧行按 swing 未完成回填);**Step 4: GREEN**(`tests/scan/test_outcome.py tests/scan/test_ledger_views.py`);**Step 5: 提交** — `feat(ledger): explicit D+10 maturity for the swing ruler; fill revisits rows until both rulers mature`

---

### Task 2: 预注册普查 `research/swing_ruler_census.py`(B2)

**Files:**
- Create: `docs/research/2026-09-2x-swing-ruler-family.spec.json`(先提交)
- Create: `autoresearch/research/swing_ruler_census.py`
- Test: `tests/research/test_swing_ruler_census.py`、`tests/research/test_family_registry.py`(登记新家族)

**Interfaces:**
- spec 的四条假设(`hypothesis_id / mechanism / expected_direction / available_at / metric_definition / population / label / rejection_condition`,与 W3 家族同 schema):
  - `swing_h1_hold_plus_finalists`:非 📌 finalist ∩ 卡评级 ≥Hold,`fwd_10_oc − 全市场可交易中位` 日等权均值 >0 且块 bootstrap 90% CI 下界 >0;拒绝条件:n_days<40 或下界 ≤0。
  - `swing_h2_lowturn_lane`:`lane=="lowturn"` 的 finalist,同上。
  - `swing_h3_rejection_negative`:卡评级 ∈{UW,Sell} 的 finalist,fwd_10 相对超额 <0 且 CI 上界 <0(否决有效性)。
  - `swing_h4_e6_r_tier_sign`:E6 已发布 BUY(R 级)在 fwd_10 与 gap_c1_o2 的符号一致率;描述性,无拒绝条件(`expected_direction: "descriptive"`)。
- `run_census(*, since=None, scan_root=None, reports_root=None, spec_path) -> Path`:读 `_ledger/recommendations.csv`(Task 1 后带 `outcome_status_swing`),只取 `MATURE_10` 行,剔 📌,按假设分格,`overnight_census.core.cell_stats` + `judge` + `family_adjustment(dependence="arbitrary")` + `block_sensitivity(block=10)`;落 `readout.md` + `cells.csv` + `manifest.json`(code_sha、输入 sha256、n_days、n_rows)。

- [ ] **Step 1: 写 spec JSON 并提交**(`validate_spec` 通过;`experiment_id = "FAM_SWING_RULER_2026xxxx"`)。
- [ ] **Step 2: 写失败测试**(合成 30 日账本:H1 格人造 +1pp、H3 格 −2pp、10 行 PENDING_10 必须被剔除并在 manifest 计数;`run_census` 输出 `cells.csv` 的 `n_days`、`mean_pp`、`ci_lo`、`verdict` 列;`judge` 对 H1 给 `POSITIVE`、H3 给 `NEGATIVE`;pending 行数 = 10)。
- [ ] **Step 3: RED**;**Step 4: 实现**(复用原语,零新口径;`main()` 提供 `--since`、`--spec`,写 `$RPT/research/swing_ruler/<experiment_id>/`,目录存在即拒——同 funnel_variants 纪律);**Step 5: GREEN**;**Step 6: 提交** — `feat(research): preregistered swing-ruler census (H1–H4) over the recommendations ledger`
- [ ] **Step 7: 首跑**(真账本,只读):`uv run --no-sync python -m autoresearch.research.swing_ruler_census --spec docs/research/2026-09-2x-swing-ruler-family.spec.json` → 读数贴进 `docs/research/2026-09-2x-swing-ruler-census.md`;n_days<40 时 verdict 恒 `INSUFFICIENT`,照实写。

---

### Task 3: 观察席影子面(B3)

**Files:**
- Create: `autoresearch/scan/swing_seat.py`(`build_swing_seat(scan_dir) -> dict`:非 📌 finalist ∩ 卡评级 ≥Hold ∩ 入场行 ≠ 禁止,按 conviction 降序,附 `stage_rulers.csv` 里 `fwd_10_oc` 的 finalist 格最近读数一行或「样本不足」)
- Modify: `autoresearch/scan/report_sections.py`(summary 新节 §12「10 日观察席(影子)」,在「诚实局限」之前;`ReportModel` 加字段)
- Modify: `autoresearch/scan/brief.py`(⑥ 之后加一行 `**⑦ 10 日观察席(影子)**:N 只 → summary §12`,计入 3KB 预算;超预算时该行退成 `⑦ 见 summary §12`)
- Modify: `autoresearch/scan/self_review.py`(`brief_lint` 白名单允许 ⑦;新探针:§12 文案不得含「BUY/买入/可买」)
- Test: `tests/scan/test_swing_seat.py`、`tests/scan/test_brief.py`、`tests/scan/test_report_sections.py`

- [ ] **Step 1: 写失败测试**(三例:有席 / 无席写「无」/ 文案含「买入」被 lint 抓)
- [ ] **Step 2: RED**;**Step 3: 实现**(全部现算,零 LLM;数字带 `_src` 出处;§12 固定模板:`| 代码 | 名称 | 评级 | 入场 | conviction |` + 一行读数);**Step 4: GREEN**(含 `tests/scan/test_product_shape_lint.py`,brief byte 预算测试);**Step 5: 提交** — `feat(report): §12 swing-ruler observation seat (shadow) + brief pointer line`

---

### Task 4: B4 裁决包(文档,冻结窗后填)

**Files:**
- Create: `docs/research/2026-1x-swing-ruler-b4-decision.md`(模板:普查读数表 / 影子席 40 日命中 / 若换主尺的 80 处消费点清单(`grep -rn MAIN_RULER autoresearch`)/ 卡片契约需加的 `## 10 日情景` 可选节 / 回滚杆)
- 不实施换尺;由用户在窗后据此裁。

---

## 自检

- 覆盖 B1 ✓ T1、B2 ✓ T2、B3 ✓ T3、B4(裁决包)✓ T4;Review Focus 1–5 各在 T1/T2/T3 有测试。
- 不做:任何主尺切换、E6 改动、卡片契约改动。

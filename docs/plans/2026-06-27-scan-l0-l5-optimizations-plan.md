# scan-market L0–L5 优化套件 — 实施计划

> Spec: `docs/specs/2026-06-27-scan-l0-l5-optimizations-design.md`。TDD,每 Task 单独 commit。
> 全局铁律:行为变更默认关 → golden parity 不破;合成 fixture 无网络;`uv run --no-sync python -m pytest`。

## Phase 1 — 地基 + 最高杠杆(regime + 权重) ✅ 完成
- [x] **T1 `regime.py`**:`RegimeState` + `classify_regime`(trend/range/risk_off + 退化 range)。
      测试:`tests/common/test_regime.py`(三态 + 空帧 + 缺 above_ma60 用 pct_60d 代理 + 阈值边界)。
- [x] **T2 `_load_weights(regime=None)`**:weights.json `regimes` 块选择;无块退 flat(parity)。
      测试:`tests/common/test_weights_regime.py`(选 trend / 缺 regimes 退 flat / regime=None 恒 flat)。
- [x] **T3 `factor_lab.calibrate_regimes` + `label_col`**:逐日 regime 分桶 IC,写 `regimes` + 保留 flat。
      测试:`tests/research/test_calibrate_regimes.py`(合成 2-regime panel → 两桶 IC 异号;flat 仍在)。
- [x] **T4 L1 接线**:`ScanConfig.regime_aware=False`;L1Recall/universe.run on 时按帧 regime 选权重。
      测试:`tests/scan/test_regime_wiring.py`(off → 与现行同;on → 调 classify + load(regime))。

## Phase 2 — 闭环半自动
- [ ] **T5 ledger propose/apply**:`propose_quota_adjustments` + `apply_proposals`(幅度封顶,audit)。
      测试:`tests/learning/test_ledger_propose.py`(持续负→降、正→升、封顶 25%、样本<min_days 不提议)。
- [ ] **T6 regime drift**:`regime.detect_drift` + self_review warn + assemble banner 行。
      测试:`tests/common/test_regime_drift.py` + `tests/learning/test_self_review_drift.py`(drift→warn,同 regime→无)。

## Phase 3 — 情感
- [ ] **T7 `score_title` + `*_sent`**:否定/反讽消解 + 强度 → 数值净情感分进 digest。
      测试:`tests/scan/test_l3_sentiment.py`(利多/利空/否定翻转/中性/强度单调)。

## Phase 4 — L2 champion A/B
- [ ] **T8 `l2_eval.forward_compare` + CLI + `l2_engine` flag**:stratified vs champion 前向对照。
      测试:`tests/research/test_l2_eval.py`(合成 panel delta 符号;engine=stratified 默认 parity)。

## Phase 5 — Leaf
- [ ] **T9 L0 门**:`_recall_gate_a` 增 `min_list_days`/`min_amount_yi`(默认 0=关)。
      测试:`tests/scan/test_l0_gates.py`(开启剔次新/低额;默认不剔=parity)。
- [ ] **T10 L2 regime cap + sector_mom 透传**:`stratified_l2` regime cap(默认 None=现行)+ 输出行业动量列。
      测试:`tests/scan/test_l2_regime_cap.py`(trend 放宽、risk_off 收紧;默认同现行;sector_mom 存在)。
- [ ] **T11 L5 regime 标注 + 得分轨迹 + fp 钩子**:summary regime 行;A_pipeline 段轨迹;self_review fp 检查。
      测试:`tests/scan/test_assemble_regime.py`(已有 assemble 测试扩;无 regime 数据退现行)。
- [ ] **T12 L4 helpers**:`force_full_card` 白名单 + `audit_rubric_gates` 一致性抽检。
      测试:`tests/scan/test_l4_helpers.py`(强先验入白名单;gates 与正文矛盾→flag)。

## 收尾
- [x] 全量 `uv run --no-sync python -m pytest`(485 passed,warning-free)+ 新增/改动文件 `ruff check` 通过。
      (注:`tests/scan/test_l2_stratify.py` 有 2 处 pre-existing F401,在 main 上已存在、非本分支引入,未扫入。)
- [x] 本地合并 main + 更新 memory。
- [~] `scan run` 真数据烟测:需 TUSHARE_TOKEN + 网络,留作合并后手动跑(off 默认即 parity,逻辑已被 parity 单测锁)。

## 完成状态(2026-06-27)
T1–T12 全部 TDD 落地,每项单独 commit,**行为变更默认关 → golden parity 不破**。
- **已交付机制**:regime 地基 + regime-aware 权重(默认关)+ calibrate_regimes 多 horizon + 闭环 quota 提议/应用
  + regime drift(self_review warn + assemble banner)+ 情感 score_title/数值净情感 + L2 champion A/B harness
  + L0 流动性/次新门 + L2 sector_mom/regime cap + L5 regime 标注 + L4 force_full_card/audit_rubric_gates。
- **需真数据跑(机制已交付,结论另跑)**:`calibrate_regimes`(产 regimes 权重块)、`l2_eval`(champion vs stratified verdict)。
- **诚实记账**:L5 的 "self_review false_positive 反推钩子" 判定为低置信/投机项,**未实现**(不交付空壳);
  per-finalist 得分轨迹已由 §3 表满足,未另起冗余产物。

# scan-market L0–L5 优化套件 — 实施计划

> Spec: `docs/specs/2026-06-27-scan-l0-l5-optimizations-design.md`。TDD,每 Task 单独 commit。
> 全局铁律:行为变更默认关 → golden parity 不破;合成 fixture 无网络;`uv run --no-sync python -m pytest`。

## Phase 1 — 地基 + 最高杠杆(regime + 权重)
- [ ] **T1 `regime.py`**:`RegimeState` + `classify_regime`(trend/range/risk_off + 退化 range)。
      测试:`tests/common/test_regime.py`(三态 + 空帧 + 缺 above_ma60 用 pct_60d 代理 + 阈值边界)。
- [ ] **T2 `_load_weights(regime=None)`**:weights.json `regimes` 块选择;无块退 flat(parity)。
      测试:`tests/common/test_weights_regime.py`(选 trend / 缺 regimes 退 flat / regime=None 恒 flat)。
- [ ] **T3 `factor_lab.calibrate_regimes` + `label_col`**:逐日 regime 分桶 IC,写 `regimes` + 保留 flat。
      测试:`tests/research/test_calibrate_regimes.py`(合成 2-regime panel → 两桶 IC 异号;flat 仍在)。
- [ ] **T4 L1 接线**:`ScanConfig.regime_aware=False`;L1Recall/universe.run on 时按帧 regime 选权重。
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
- [ ] 全量 `uv run --no-sync python -m pytest` + `ruff check` 通过。
- [ ] `scan run` 烟测(若 token/网络可用):off 默认跑通且 parity check 绿。
- [ ] 本地合并 main(finishing-a-development-branch),更新 memory。

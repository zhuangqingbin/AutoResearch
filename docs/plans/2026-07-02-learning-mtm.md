# 记忆与门 mark-to-market 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。TDD,合成 fixture,无网络。
> 紧凑版:API/字段/阈值/parity 锚详见 spec `docs/specs/2026-07-02-learning-mtm-design.md`(同日执行,spec 即详细设计)。

**Goal:** 语义记忆获得 regime 作用域与被证伪能力;硬门留痕并被后市问责;proposals 有看板;注入有 cap。

**Global Constraints:** `uv run --no-sync`;所有降级动作只提名走 proposals(人批);parity 锚(regime=None / 命中≤K / 无命中回退基线 → 逐字不变);缺列缺文件降级不抛。

### Task 1: feedback_store — R1 regime 域 + R2/R7 mtm_update + R4 看板 + R6 注入治理
- Files: Modify `autoresearch/learning/feedback_store.py`;Test: `tests/learning/test_feedback_mtm.py`(新)
- [ ] 红:R1 三态过滤/兼容、R2 幂等+conf 升降 clip+达阈提名、R4 天龄、R6 cap+排序+parity
- [ ] 实现:`upsert_lesson(regimes=)`、`lessons_for(regime=)`+排序 tiebreak、`mtm_update`、`open_proposals`、`render_calibration_block(regime=)`+K=8 cap
- [ ] 绿 + ruff → commit `feat(learning): lesson regime域+MTM+看板+注入cap`

### Task 2: self_review code 字段 + assemble 落 gate_fires.csv
- Files: Modify `learning/self_review.py`、`scan/assemble.py`;Test: 增 `tests/learning/test_self_review.py` 断言 + `tests/scan/test_assemble_watchlist_menu.py` 增 gate_fires 两态
- [ ] 红 → failure dict 加 `code`(全局检查 None);`_self_review_banner` 幂等写 `<scan_dir>/gate_fires.csv`(无 fail 也写表头)
- [ ] 绿 + 既有 self_review/assemble 测试全绿 → commit `feat(scan): self_review 留痕 gate_fires.csv(门审计地基)`

### Task 3: retro — mtm_check_guards + gate_audit + proposals/MTM 节 + decay 接入
- Files: Modify `learning/retro.py`;Test: `tests/learning/test_gate_audit.py`(新)
- [ ] 红:mtm_check_guards(n≥5 判/skip 两态)、gate_audit join、mark_done 触发 decay(monkeypatch 断言)
- [ ] 实现 + `write_retro_input` 增三节(经验 MTM / 门审计 / 待裁决 proposals)
- [ ] 绿 → commit `feat(learning): retro 接 MTM 机判+门审计+看板+decay 节奏`

### Task 4: gate_ledger 跨日 + playbook 接线
- Files: Create `learning/gate_ledger.py`;Test: `tests/learning/test_gate_ledger.py`;Modify `.claude/skills/scan-retro/retro-playbook.md`
- [ ] 红:roll 聚合两日/render/空目录 → 实现(镜像 zero_buy_ledger)→ 绿
- [ ] playbook:第 2 步读 MTM/门审计节 + 无 guard 经验人判 `mtm_update`;第 4 步含 lesson 降级提名与门退役建议;L3 注入传 regime
- [ ] 全量 pytest + ruff → commit → merge 回 main(本地)

# Phase C — UZI 思维 → 增强 L3 判断机制

**Goal:** L2/L3 单一"资深投资师" → 多 persona 对抗面板 +「矛盾必须呈现」+ self-review 硬门 + 证据分级。
**Spec:** `docs/specs/2026-06-20-uzi-integration-design.md` §5。
**前置:** Phase 1 闭环(`feedback_store.lessons_for`)。

## 文件
- 改 `.claude/skills/scan-market/screening-playbook.md`(L3 多 persona + 分歧呈现)
- 新 `scripts/self_review.py`(发布前硬门检查 + 自测)
- 改 `scripts/assemble_scan.py`(分歧呈现 + 接 self_review 门)

## Task 1 — L3 多 persona 对抗面板
screening-playbook L3:每只 finalist 用 N 个 subagent 扮不同流派(价值/成长/游资/quant/风险官),各引因子下判断;主线汇总**保留分歧**(不取均值抹平)。每 persona 复用 `render_calibration_block`(经验注入)。

## Task 2 —「矛盾必须呈现」
`assemble_scan`:finalist 若 persona 分歧大(看多/看空票数接近)→ 报告**显式标注分歧 + 各方核心论据**,不和稀泥。新增 `_divergence_note(rows)`。

## Task 3 — self-review 硬门(UZI 13 检本地版)
`self_review.py::review(report_ctx) -> (ok, failures)`,机械检查:
- 行业分类冲突 / 覆盖率不足(finalist 缺卡 > 阈值)
- **违背已学经验**:买单里有票踩了 `lessons_for` 的红线(如 winner_rate 满却给 Overweight 且无特批理由)
- 数据缺口过大(关键块缺失比例)
- 评级与因子方向明显矛盾(composite 极低却 Overweight)
→ 返回 failures;assemble **不达标不发布**,先列问题让 Claude 修。

## Task 4 — 证据分级
卡片/finalist 数据标置信层级:硬披露(财报/龙虎榜/tushare)= strong;tushare 衍生 = medium;websearch/爬虫 = weak。self_review 对 weak 主导的结论降权告警。

## Task 5 — 测试 + 验收
- `self_review.py --selftest`(各硬门触发/放行 合成用例);ruff 绿。
- assemble:分歧呈现 + self_review 门接通(合成不达标 → 拦截)。
- 不碰排除文件;不 commit。

## 验收
L3 产出多流派分歧(非单一视角);self-review 硬门能拦下"违背 lessons / 覆盖不足 / 评级-因子矛盾"的报告,迫使先修。

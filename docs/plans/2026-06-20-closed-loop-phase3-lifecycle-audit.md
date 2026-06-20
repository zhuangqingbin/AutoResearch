# Phase 3 — 经验生命周期 + 审计 + 打磨(实现 plan)

**Goal:** 让闭环长期不腐烂、可审计、可回滚,并把"会话触发自动补跑"接进 scan-market。
**Spec:** `docs/specs/2026-06-20-closed-loop-learning-design.md` §6 §8(Phase 3)。
**前置:** Phase 1 + Phase 2。

## 文件
- 改 `scripts/feedback_store.py`(生命周期 + 回滚 + lessons.md 渲染落盘)
- 改 `scripts/retro.py`(消息脉冲过滤细化 + 经验退休触发)
- 改 `.claude/skills/scan-market/SKILL.md`(开跑前补跑 retro)
- 改 `.claude/skills/scan-retro/retro-playbook.md`(退休 + 回滚 + 建议审批流)

## Task 1 — 经验生命周期(防腐烂)
- `feedback_store.decay_lessons(today, max_stale_retros=N, min_conf=0.3)`:对 active 经验,若 `reinforce_count` 在最近 N 个 retro 未增 → `confidence -= 0.1`;`confidence < min_conf` → `retire_lesson`。
- `upsert_lesson` 强化时 confidence 上限 0.95;新建默认 0.6。
- retro 末尾调 `decay_lessons` 并在报告列"退休经验"。
- 测:构造一条久未强化经验 → decay 后 retired;`lessons_for` 不再返回。

## Task 2 — 审计 + 回滚
- 自动重标定前:`feedback_store.snapshot_weights()` → 存 `context/factor_lab/weights.<sha8>.json` 并返回 sha。
- `log_change` 记 before/after sha + top_changes。
- `feedback_store.rollback_weights(sha)`:把 `weights.<sha>.json` 覆盖回 `weights.json`,append 一条 changelog(`kind="rollback"`)。
- 测:snapshot→改→rollback round-trip,weights.json 复原。

## Task 3 — `lessons.md` 人读视图落盘
- `render_lessons_md()` 输出按 scope 分组、conf 排序的 markdown → 写 `context/knowledge/lessons.md`。
- retro / feedback skill 写经验后刷新该文件(便于手工策展)。
- 测:渲染含 active、不含 retired。

## Task 4 — 会话触发自动补跑接线
- `scan-market/SKILL.md`:开跑前先 `retro.pending_days(today)`;若非空 → 先提示并按 `scan-retro` 把未复盘日补上(再开始今天的扫描),使权重/经验在本次扫描前就是最新。
- `retro-playbook.md`:补充
  - **建议审批流**:`proposals.jsonl` open 项 → 用户 approve → 落地(prompt 规则改动须按 writing-skills 测过)→ `set_proposal_status(applied)`。
  - **回滚指引**:权重异常时 `rollback_weights(sha)`。

## Task 5 — 消息脉冲过滤细化(retro)
- `retro.flag_news_pop(attr, date)`:对赢家用确定性信号标 `news_pop`(当日涨停/一字、停复牌、量比极端);诊断与重标定样本剔除这些(避免拿不可预测脉冲惩罚打分)。
- 测:构造一只涨停脉冲赢家 → 被标 news_pop、不计入"系统性漏判"。

## Task 6 — 测试 + 验收
- `feedback_store.py --selftest` / `retro.py --selftest` 扩充全过;ruff clean。
- 端到端:一条经验久未强化→退休并从校准块消失;一次重标定→snapshot+changelog,rollback 复原;scan-market 开跑前自动补跑未复盘日。
- 不碰排除文件;不 commit;`uv run --no-sync`。

## 验收
闭环长期可用:经验自动腐烂/退休、权重改动可审计可回滚、scan 开跑前自动把欠的复盘补齐 —— 半自动闭环完整成型。

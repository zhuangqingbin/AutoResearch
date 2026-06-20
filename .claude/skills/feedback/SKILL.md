---
name: feedback
description: Use when the user reacts to a research report / scan result with a correction, complaint, praise, or "记住/下次注意/这个评级错了/你漏了X/为什么没选到Y", or types /feedback — capture it into the closed-loop knowledge store and distil reusable lessons that flow back into future reports. Works across scan-market / analyze-ticker / macro-research. Project-local.
---

# feedback — 把你对研报的反馈记住,并蒸馏成经验注回下一份研报

## 核心原理
研报技能(scan/analyze/macro)是无状态的;这个 skill 给它们补上**记忆**。你对某份报告的每次反馈(纠正/抱怨/认可),被结构化落进 `context/knowledge/feedback.jsonl`(情节记忆);若可泛化,再蒸馏升成 `lessons.jsonl`(语义经验)。经验下次由 `feedback_store.render_calibration_block()` **自动注回** scan-market 的 L2/L3 prompt 和报告骨架——你这次的纠正,下次不再犯。

判断/蒸馏由你(Claude)在 session 内做(**零付费 LLM**);存取是确定性的 `scripts/feedback_store.py`。

## 何时触发
- ✅ 用户对刚出的报告说"这个评级错了 / 你把 X 看反了 / 为什么没筛到 Y / 这条很好下次保持 / 记住这个"。
- ✅ 用户显式 `/feedback ...`。
- ✅ 用户对 retro 复盘报告再反馈(闭环二次校正)。
- ❌ 与研报无关的闲聊;❌ 一次性的事实订正(无泛化价值)→ 可记 feedback 但不升经验。

## 流程
读 `feedback-playbook.md` 跑完整 5 步:定位报告 → 判 verdict/scope → 蒸馏 root_cause + corrective_rule → `record_feedback` → 决定是否 `upsert_lesson` 升语义 → 回执。

## 前置
- 项目根目录运行;`context/knowledge/` 随 context 自动 gitignore。
- store API:`scripts/feedback_store.py`(`record_feedback / upsert_lesson / lessons_for / render_calibration_block`)。

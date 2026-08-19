---
name: feedback
description: Capture user reactions to research output (correction/complaint/praise/「记住」「这个评级错了」「你漏了X」) into the closed-loop store, and adjudicate pending pr_* proposals (「裁决提案」) — distils lessons that feed future scans. Works across scan-market / stock-research / macro-research. Project-local.
---

> **路径约定**:`$CTX`/`$RPT` = 本引擎工作区根(Claude→`context_claude`/`reports_claude`,Codex→`context_codex`/`reports_codex`;shell 里 `CTX=context_${AUTORESEARCH_ENGINE:-claude}`,`RPT=reports_${AUTORESEARCH_ENGINE:-claude}`)。数据湖 `lake/` 两引擎共享。Read/Write 工具调用时把 `$CTX`/`$RPT` 代入具体目录名。

# feedback — 把你对研报的反馈记住,并蒸馏成经验注回下一份研报

## 核心原理
研报技能(scan/analyze/macro)是无状态的;这个 skill 给它们补上**记忆**。你对某份报告的每次反馈(纠正/抱怨/认可),被结构化落进 `$CTX/knowledge/feedback.jsonl`(情节记忆);若可泛化,再蒸馏升成 `lessons.jsonl`(语义经验)。经验下次由 `feedback_store.render_calibration_block()` **自动注回** scan-market 的 L2/L3 prompt 和报告骨架——你这次的纠正,下次不再犯。

判断/蒸馏由你(Claude)在 session 内做(**零付费 LLM**);存取是确定性的 `autoresearch/learning/feedback_store.py`。

## 何时触发
- ✅ 用户对刚出的报告说"这个评级错了 / 你把 X 看反了 / 为什么没筛到 Y / 这条很好下次保持 / 记住这个"。
- ✅ 用户显式 `/feedback ...`。
- ✅ 用户对 retro 复盘报告再反馈(闭环二次校正)。
- ❌ 与研报无关的闲聊;❌ 一次性的事实订正(无泛化价值)→ 可记 feedback 但不升经验。

## 流程
读 `feedback-playbook.md` 跑完整 5 步:定位报告 → 判 verdict/scope → 蒸馏 root_cause + corrective_rule → `record_feedback` → 决定是否升语义(新 slug 先 `similar_lessons` 召回→`adjudicate` 裁决,已有 slug 直接 `upsert_lesson` 强化)→ 回执。

## 边界:复盘不动刀(2026-08-13 用户裁定)
**复盘/反馈流程一律不得编辑 .claude/ 与 CLAUDE.md/AGENTS.md;skill/prompt/agent/workflow 文本只在用户显式发起的开发会话中修改。** 本 skill 的产出止于:feedback/lessons/memo 落库 + 提案起草/裁决。裁决 `pr_*` 只改**提案状态**与(逐条人批的)`scan_config.jsonc` 参数,**不改 skill 文本**;经验毕业只 `add_graduation_nomination` 起草提名,施工与 `retire_lesson` 归开发会话。spec:`docs/specs/2026-08-13-retro-skill-selfmodify-removal-design.md`。

## 前置
- 项目根目录运行;`$CTX/knowledge/` 随 context 自动 gitignore。
- store API:`autoresearch/learning/feedback_store.py`(`record_feedback / upsert_lesson / similar_lessons / adjudicate / lessons_for / render_calibration_block`)。

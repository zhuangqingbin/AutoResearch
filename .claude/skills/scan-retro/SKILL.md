---
name: scan-retro
description: Use when reviewing how a prior trading day's scan-market report actually played out — triggered by /retro, by the user asking "复盘昨天的扫描/为什么没选到涨的那些", or automatically when scan-market finds unreviewed days. Attributes missed winners across the funnel (L0 gate / L1 weight / L2-L3 AI), auto-recalibrates factor weights, proposes structural fixes, and distils lessons. scan-market only. Project-local.
---

# scan-retro — 用实际涨跌复盘 scan 报告,自迭代权重与经验

## 核心原理
scan-market 出的报告是"事前判断";retro 用**当日已实现 T+1 涨跌**(`fwd_1_oo`,与 factor_lab 校准同口径)做"事后批改":把每只赢家分桶——**抓到 / L2-L3 误判 / 漏在 L1 / 漏在 L0 / 误买**——再回答你最想知道的"**涨得好的为什么没筛出来**"。诊断分三段药(门槛/权重/AI),并把**可归因的因子病因**与**不可预测的消息脉冲**分开。

**半自动闭环**(你已定调):
- **自动落地**:IC 权重重标定(`factor_lab.calibrate`,多日滚动 + 收缩,绝非单日翻权重)→ 写 `changelog.jsonl` 可审计/回滚。
- **出建议待批**:新因子 / 改 L0 门槛 / 改 L2-L3 prompt 规则 → `proposals.jsonl`。
- **写经验**:反复出现的诊断 → `lessons.jsonl`,下次自动注回 L2/L3 校准块。

确定性归因在 `scripts/retro.py`(纯函数已自测);诊断/写经验由你(Claude)在 session 内做(**零付费 LLM**)。

## 何时触发
- ✅ `/retro` 或"复盘昨天的扫描"。
- ✅ scan-market 开跑前发现未复盘日(自动补跑,见 scan-market SKILL)。
- ✅ 用户问"为什么没选到 X(涨了的)"。
- ❌ 当日报告 fwd 未实现(D+2 交易日没到)→ `retro.pending_days()` 不会返回它,跳过。

## 流程
读 `retro-playbook.md` 跑完整 6 步:`pending_days` → `attribute`+`write_retro_input` → Claude 诊断(三段药 + 分离消息脉冲)→ 自动重标定 + changelog → 建议/经验 → retro 报告 + `mark_done`。

## 前置
- 项目根目录;`.env` 有 `TUSHARE_TOKEN`;factor_lab cache 在(retro 会按需补拉 D+1/D+2 的 daily)。
- 依赖 Phase 1 的 `feedback_store`(写经验/建议/审计)。模型建议 **Sonnet**(结构化对比,便宜)。

---
name: scan-retro
description: "Two review loops for prior scan-market days: FAST t1_review (D+1 initial + D+2 gap final verdict, per-card judgment accuracy; nightly automatic deterministic backfill, no LLM diagnosis leg since 2026-08-19) and SLOW retro (D+2, funnel recall attribution + auto weight recalibration + lessons). Triggers: /retro, 「复盘昨天的扫描」「为什么没选到X」, scan-market finding unreviewed days, or 「补复盘欠账」(batch diagnosis, ≤5 days/run). scan-market only. Project-local."
---

> **路径约定**:`$CTX`/`$RPT` = 本引擎工作区根(Claude→`context_claude`/`reports_claude`,Codex→`context_codex`/`reports_codex`;shell 里 `CTX=context_${AUTORESEARCH_ENGINE:-claude}`,`RPT=reports_${AUTORESEARCH_ENGINE:-claude}`)。数据湖 `lake/` 两引擎共享。Read/Write 工具调用时把 `$CTX`/`$RPT` 代入具体目录名。

# scan-retro — 用实际涨跌复盘 scan 报告,自迭代权重与经验

## 双环结构(2026-07-17 起;**快环 LLM 段 2026-08-19 用户裁定 A5 退役**)

| 环 | 成熟期 | 量什么 | 尺 | 喂什么 |
|---|---|---|---|---|
| **快环 t1_review** | **D+1**(初判)/**D+2**(gap 终判) | **判断层精度**:T 报告真选票次日兑现如何(**保送 pinned 不算**,用户裁定 2026-07-17) | **z**(行业中性超额/截面稳健σ,盖帽±3;cc1=D+1 初判尺,gap_c1_o2=D+2 终判尺) | 🔄 校准块注入 L3/L4 prompt(账本派生数据,非指令;**不再产出新经验/提案**) |
| **慢环 retro(下述 6 步)** | D+2 | 漏斗召回:全市场谁涨了没进池 | **`gap_c1_o2`**(主尺,`common.ruler.MAIN_RULER` 单点);`fwd_1_oo` / `fwd_2_oc` 降参考尺 | 权重重标定(**唯一自动腿**)+ 提案 |

**快环 = nightly 自动确定性回补(`t1_backfill` / `t1_gap_finalize`),无人工 LLM 段**(2026-08-19 用户裁定 A5:t1-review LLM workflow 与自动立案链已整体退役——22 日仅 8 个合格终判、全 UW 侧,08-11 后已实质停摆)。`nightly_close` 每晚自动补 D+1 记分卡+账本行、回填 D+2 隔夜 gap 终判(`final_verdict`),**不需要人工触发,也不再有逐票诊断 agent**。判定尺 v2(2026-07-17 调研落地):行业中性超额(cc1/gap_c1_o2 − 同业均值,先剥 β/板块共振)÷ 截面稳健σ(1.4826×MAD)= z,方向判定双门 |z|≥0.5 且 |超额|≥0.8pp,惊奇 |z|≥1.5;🔒一字开盘板不计可实现。

按需人工查看(deep-diagnosis 靠人自己读盘,不再有 workflow 自动派发):
```bash
uv run --no-sync python -m autoresearch.learning.t1_review pending    # 待复盘 (T,T+1) 对(nightly 已自动清,恒空是常态)
uv run --no-sync python -m autoresearch.learning.t1_review report     # 账本累计视图(准率/机制直方图/conviction·置信度校准)
uv run --no-sync python -m autoresearch.learning.t1_review build <T>  # 手动补一日记分卡(离线核对用,不写诊断)
```
逐票「为什么准/不准」的诊断叙事已不再自动产出;需要深挖某日,人工对照 `$CTX/scan/<T>/t1_review/scorecard.md` 与当日 `details/*.md` 自行比对。**自我迭代腿(候选账本 → 自动立案 proposals.jsonl)随 LLM 段一并退役**:`t1_candidates.jsonl` 已归档(`archive/20260819/`);同类规则的沉淀今后一律改经 **feedback skill** 人工立案。spec:`docs/specs/2026-08-18-e6-activation-learning-slimdown-design.md` §4 D3。

> **复盘不动刀(2026-08-13 用户裁定)**:复盘/反馈流程一律不得编辑 .claude/ 与 CLAUDE.md/AGENTS.md;skill/prompt/agent/workflow 文本只在用户显式发起的开发会话中修改。本 skill 两环的产出止于账本 / 经验 / 权重 / 提案 / 复盘报告。spec:`docs/specs/2026-08-13-retro-skill-selfmodify-removal-design.md`。

## 核心原理(慢环)
scan-market 出的报告是"事前判断";retro 用**当日已实现 T+1 涨跌**(`fwd_1_oo`,与 factor_lab 校准同口径)做"事后批改":把每只赢家分桶——**抓到 / L2-L3 误判 / 漏在 L1 / 漏在 L0 / 误买**——再回答你最想知道的"**涨得好的为什么没筛出来**"。诊断分三段药(门槛/权重/AI),并把**可归因的因子病因**与**不可预测的消息脉冲**分开。

**半自动闭环**(你已定调):
- **自动落地**:IC 权重重标定(`factor_lab.calibrate`,多日滚动 + 收缩,绝非单日翻权重)→ 写 `changelog.jsonl` 可审计/回滚。
- **出建议待批**:新因子 / 改 L0 门槛 / 改 L2-L3 prompt 规则 → `proposals.jsonl`。
- **写经验**:反复出现的诊断 → `lessons.jsonl`,下次自动注回 L2/L3 校准块。

确定性归因在 `autoresearch/learning/retro.py`(纯函数已自测);诊断/写经验由你(Claude)在 session 内做(**零付费 LLM**)。

## 何时触发
- ✅ `/retro` 或"复盘昨天的扫描"。
- ✅ scan-market 开跑前发现未复盘日(自动补跑,见 scan-market SKILL)。
- ✅ 用户问"为什么没选到 X(涨了的)"。
- ✅ 用户说"补复盘欠账"(诊断欠账 ≥2 日的批量清账,见下「流程」)。
- ❌ 当日报告 fwd 未实现(D+2 交易日没到)→ `retro.pending_days()` 不会返回它,跳过。

## 流程
读 `retro-playbook.md` 跑完整 6 步:`pending_days` → `attribute`+`write_retro_input` → Claude 诊断(三段药 + 分离消息脉冲)→ 自动重标定 + changelog → 建议/经验 → retro 报告 + `mark_done`。

**欠账 ≥2 日(触发词"补复盘欠账")**:`retro pending` 拆两段(Wave11-A7)——**归因欠账**(确定性计算未跑,`nightly_close` 每晚自动补,不用人管)与**诊断欠账(已备料)**(`retro_input.md` 已生成,只差这步的 Claude 诊断)。批量补诊断只清后者:见 `retro-playbook.md` §批量补诊断(≤5 日/次合诊,跨日看系统性病因;逐日诊断完立即 `mark_done`,不要攒到整批完了再一起标)。清账判据 = `retro pending` 的「诊断欠账」段为空。

- **per-channel edge(L1 段)**:`stage_eval.evaluate` 已落 `retro/channel_eval.csv`(每路 T+2 截面**边际超额** `unique_excess_t2` = 这路独占票有没有赢;主尺现为 `gap_c1_o2`(2026-08-05 裁定,取代 2026-07-10 的 `fwd_2_oc`),t5 列已退位为参考展示、不再驱动决策;列名里的 `_t2` 字样是沿自旧尺的**历史命名**,取值随主尺现算,见 STAGES.md「账本定义断层」);跨日看 `uv run --no-sync python -m autoresearch.learning.channel_ledger`(→ `$RPT/learning/channel_ledger.md`)。某路 `unique_excess_t2` 持续为负且 `n_days≥3` → 建议下调其 quota(写 `proposals.jsonl`,**人工决定,不自动改**;提议基线自动读 scan_config.jsonc 的 channel_quotas,已实施的改动不会重复提议)。`n_days<3` 标 ⚠样本少,不下结论。

## 前置
- 项目根目录;`.env` 有 `TUSHARE_TOKEN`;factor_lab cache 在(retro 会按需补拉 D+1/D+2 的 daily)。
- 依赖 Phase 1 的 `feedback_store`(写经验/建议/审计)。模型建议 **Sonnet**(结构化对比,便宜)。

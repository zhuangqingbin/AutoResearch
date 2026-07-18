---
name: scan-retro
description: Use when reviewing how a prior trading day's scan-market report actually played out — triggered by /retro, by the user asking "复盘昨天的扫描/为什么没选到涨的那些/昨天推的票准不准", or automatically when scan-market finds unreviewed days. Two loops - fast t1_review (D+1, per-card accuracy of genuine picks vs T+1 close, agent diagnosis via t1-review workflow) and slow retro (D+2, funnel recall attribution, auto-recalibrates factor weights, proposes structural fixes, distils lessons). scan-market only. Project-local.
---

# scan-retro — 用实际涨跌复盘 scan 报告,自迭代权重与经验

## 双环结构(2026-07-17 起)

| 环 | 成熟期 | 量什么 | 尺 | 喂什么 |
|---|---|---|---|---|
| **快环 t1_review** | **D+1**(T+1 收盘当晚) | **判断层精度**:T 报告真选票次日兑现如何、为什么(**保送 pinned 不算**,用户裁定 2026-07-17) | **z**(行业中性超额/截面稳健σ,盖帽±3;cc1 为底,oc1 参考)| prompt 侧经验/提案候选(**人批**) |
| **慢环 retro(下述 6 步)** | D+2 | 漏斗召回:全市场谁涨了没进池 | fwd_1_oo / fwd_2_oc | 权重重标定(**唯一自动腿**)+ 提案 |

**快环用法**(只做 T→T+1 相邻交易日间隔,周末/节假日顺延;不看更长 horizon)。**判定尺 v2(2026-07-17 调研落地)**:行业中性超额(cc1 − 同业均值,先剥 β/板块共振)÷ 截面稳健σ(1.4826×MAD)= z,方向判定双门 |z|≥0.5 且 |超额|≥0.8pp,惊奇 |z|≥1.5;🔒一字开盘板不计可实现;needs_diag 分诊(不准/惊奇/|z|≥1 才烧诊断 token,ERL 实证失败样本教训价值>成功样本)。期望值口径 = 胜率×均赢/均亏 + conviction 校准桶(Tetlock)。

```bash
uv run --no-sync python -m autoresearch.learning.t1_review pending    # 待复盘 (T,T+1) 对
```
→ **最新一对**跑(先读配置再拉 workflow,两条命令):
```bash
uv run --no-sync python -m autoresearch.scan.user_config     # 白名单校验后的配置 JSON(含 agents.t1_*)
```
→ `Workflow({scriptPath: '.claude/workflows/t1-review.js', args: {date: '<T>', cfg: <上面的 JSON>}})`。
**2 个 agent**(2026-07-17 用户裁定勿每票 fan-out):**合诊**(跑 build CLI + 一个 context 通读全部真选卡对比诊断——真选 ≤13 只装得下,且「4/5 随大盘」这类跨票模式只有合诊看得见,同 L3 holistic 哲学)+ **综合官**(独立复核合诊、写候选/report、finalize 落账)。产出 `context/scan/<T>/t1_review/report.md` + 账本。**更早的对**逐日 `... t1_review backfill <T>`(确定性回补,只进账本不烧诊断 token)。累计视图:`... t1_review report`。T+1 当晚 daily 未发布(~17:00 前)build 会诚实报错,晚点再跑。

**agent 配置**:合诊/综合官的 model/effort 由 `scan_config.jsonc` 的 `agents.t1_diag` / `agents.t1_synth` 管控,经上面 user_config CLI 随 `args.cfg` 传入(综合官另有 pack.agents_cfg 兜底;都缺 = 内建默认 继承会话模型·high)。

**自我迭代腿(2026-07-17,不止步于复盘文档)**:综合官写 `candidates.json`(稳定 key,跨日复用)→ `finalize` 记入候选账本 `context/learning/t1_candidates.jsonl` → **次日 L3 表自动注入**「🔄 T+1 快环校准」块(`prepare_l3_table` 表尾,账本派生数据非指令,含准率/机制直方图/复盘观察)→ 同 key 累计 **≥2 个 T 日自动立案** `proposals.jsonl`(prompt_rule,一键人批)→ 人批成 lesson 后经 `feedback_store.render_calibration_block` 注入(同日已接线,pr_20260716_005 闭)。**半自动边界不变:自动的是观察注入与提案起草,改规则/prompt 文件仍人批。**

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
- ❌ 当日报告 fwd 未实现(D+2 交易日没到)→ `retro.pending_days()` 不会返回它,跳过。

## 流程
读 `retro-playbook.md` 跑完整 6 步:`pending_days` → `attribute`+`write_retro_input` → Claude 诊断(三段药 + 分离消息脉冲)→ 自动重标定 + changelog → 建议/经验 → retro 报告 + `mark_done`。

- **per-channel edge(L1 段)**:`stage_eval.evaluate` 已落 `retro/channel_eval.csv`(每路 T+2 截面**边际超额** `unique_excess_t2` = 这路独占票有没有赢;2026-07-10 裁定 fwd_2_oc 主尺,t5 列已退位为参考展示、不再驱动决策);跨日看 `uv run --no-sync python -m autoresearch.learning.channel_ledger`(→ `reports/learning/channel_ledger.md`)。某路 `unique_excess_t2` 持续为负且 `n_days≥3` → 建议下调其 quota(写 `proposals.jsonl`,**人工决定,不自动改**;提议基线自动读 scan_config.jsonc 的 channel_quotas,已实施的改动不会重复提议)。`n_days<3` 标 ⚠样本少,不下结论。

## 前置
- 项目根目录;`.env` 有 `TUSHARE_TOKEN`;factor_lab cache 在(retro 会按需补拉 D+1/D+2 的 daily)。
- 依赖 Phase 1 的 `feedback_store`(写经验/建议/审计)。模型建议 **Sonnet**(结构化对比,便宜)。

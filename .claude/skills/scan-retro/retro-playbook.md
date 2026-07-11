# scan-retro playbook — 6 步复盘自迭代

> **本文 + `retro.py` / `feedback_store.py` / `factor_lab.py` 自足,无需 `docs/specs/`。** 确定性归因:`retro.py`;知识库:`feedback_store.py`;重标定:`factor_lab.py`。本文是 6 步操作手册。

## 漏斗复盘一图
```
D 的报告(事前) ──对──> D 当日已实现 fwd_2_oc(事后,超短主尺;fwd_1_oo 仍留作参考)
  每只赢家分桶:caught / recalled_cut(L2-L3误判) / missed_l1(权重压低) / missed_l0(门槛误杀) / false_positive(误买)
  → 三段药:门槛 / 权重 / AI;消息脉冲单独标,不入重标定样本
  → ① 自动重标定(权重) ② 出建议(结构) ③ 写经验
```

## 6 步

**1. 找未复盘日 + 归因(确定性)**
```bash
uv run --no-sync python - <<'PY'
import sys; sys.path.insert(0, "scripts")
import autoresearch.learning.retro as retro
for d in retro.pending_days():           # 有报告+有面板+fwd已实现+未done 的 scan 日
    attr = retro.attribute(d)            # 写 context/scan/<d>/retro/attribution.csv
    retro.write_retro_input(d, attr)     # 写 retro_input.md(stage_stats 各段命中率 + 漏判赢家因子行 + 对照 + F·stage_eval 各阶段 agent edge + E2·promotion_candidates 经验升门候选)
    print("ready:", d)
PY
```
对每个待复盘日 D,读 `context/scan/<D>/retro/retro_input.md`。

**2. Claude 诊断:三段药 + 分离消息脉冲**(核心,就是"涨得好的为什么没筛出来")
对 `missed_l0 / missed_l1 / recalled_cut` 三桶的赢家,**成群**(非逐只)对比 caught 样本,落到因子说清**系统性病因**:
- **missed_l0(门槛误杀)**:被市值地板/ST/次新/北交所剔了?群体特征(如"普遍 20–30亿次新成长")→ 病因=门槛过严。
- **missed_l1(权重压低)**:在召回池但 composite 排到召回线外。它们共有什么被低估的因子?(如"低获利盘+主力进场+低动量的反转票,被动量主导的复合分压住")→ 病因=权重/因子方向。
- **recalled_cut(L2-L3 误判)**:召回了却被 AI cut。当时的 L2/L3 理由错在哪?(对照『因子方向经验校准』,是不是又踩了 winner_rate/过热的坑)→ 病因=判断规则。
- **分离消息脉冲**:涨停/一字/停复牌复牌/巨量异动驱动的赢家 ≠ 选股失败 → 标 `news_pop`,**排除出重标定样本与"系统性漏判"结论**(不可预测,别拿去惩罚打分)。
- **T+5 盲区节(swing 口径,长线参考非主尺)**:与主尺(T+2/`fwd_2_oc`)节并排读——L3/L4 现行主尺是超短 `fwd_2_oc`(2026-07-10 用户裁定,非 swing);若 T+5 missed_l1 持续显著多于主尺,仅记录供长线参考,horizon 之争(pr_20260702_001)已裁定 rejected,不再作切 horizon 依据。
- **L3 错杀验尸节**:错杀群体的 `risk` 文本共性 = L3 系统性偏见候选(如反转市对"获利盘满"的过度恐惧);反复出现 → 第 5 步写 lesson(自动注回 L3 校准块)。**错杀=0 且主尺(T+2)missed_l1 很大 → 病在召回线不在 L3,别冤枉判断层;T+5 missed_l1 仅作长线参考,不替代主尺判断。**
- **同日配对节(M1·ExpeL 控制变量)**:读 `context/scan/<D>/retro/_retro_pairs.csv`(T+2 口径,D+2 即产出,不再等 T+5)。每行 = **同一天**的一对:`fail`(评级最高档但 T+2 跌)vs `win`(同日被门拦/漏召回但 T+2 涨),同 industry 最近邻优先(`matched_on`)。同日 = regime/地形/注入 lessons/漏斗参数全恒定 → diff 只剩标的特征与判断,`d_*` 因子差(fail − win:如 `d_winner_rate>0` = 我们买的获利盘更满、`d_momentum>0` = 追了动量)**直接指向判断偏差**。把反复出现的差蒸馏成 lesson candidate → 第 5 步走 M2 `adjudicate` 落库。0 买日也有(fail 侧用当日最高评级档代理),别因没买单就跳过。
- **floor 自然实验节**:救回组 ≈ merit 组 → floor 免费维持;救回组持续显著弱于被挤掉组 → 第 4 步提 floor 参数复审建议(人批)。
- **经验 MTM 节**:带 guard 的经验已被机判自动记账(support/refute + confidence 机械升降);**无 guard 的经验由你逐条判**——今天的归因数据支持还是打脸这条 rule?`fs.mtm_update(id, 'support'|'refute', day)` 记账,拿不准就跳过(别硬判)。refute 达阈会自动出"摘 guard/退休"提名(人批)。**写新经验时标 regime**:`upsert_lesson(..., regimes=['risk_off'])`——regime 条件真理别让它在翻转后毒害全域。
- **门审计节**:被拦票的 ex<0 = 拦对;跨日看 `uv run --no-sync python -m autoresearch.learning.gate_ledger`(→ `reports/learning/gate_ledger.md`)。某门持续 ex>0 且样本 ≥5 → 第 4 步提松阈/退役建议。**门也要 mark-to-market,别让它无问责地累积成保守棘轮。**
- **待裁决 proposals 节**:>14 天 ⚠ 的逐条给用户裁决建议(采纳/拒绝/再观察),别让看板变摆设。

**3. 自动落地:权重重标定 + 审计**(仅这一项自动改线上)
```bash
uv run --no-sync python - <<'PY'
import sys; sys.path.insert(0, "scripts")
import autoresearch.learning.retro as retro
r = retro.recalibrate_and_log("2026-06-19")   # 快照旧权重 → factor_lab.calibrate(多日滚动+收缩) → changelog.jsonl
print("recalibrated:", r["before_sha"], "→", r["after_sha"], "| n_dates", r["n_dates"])
print("top 变化:", r["top_changes"][:5])
PY
```
> **绝非单日翻权重**:calibrate 跑的是多日面板 + 申万层级收缩;单日只是把样本并进去让权重平滑漂移。weights 异常可 `weights.<sha>.json` 回滚(Phase 3)。

**4. 出建议(结构性,待你批准——不自动改)**
门槛/新因子/prompt 规则类改动 → 写 `proposals.jsonl`。**quota 接线**:`channel_ledger` 某路 `n_days≥3` 且 `unique_excess_t5` 持续负 → 用 `channel_ledger.propose_quota_adjustments` 写降 quota 提议(单步±25%,advisory);持续强的路(如 momentum)同理提升 quota。floor 复审建议同此步。示例:
```bash
uv run --no-sync python - <<'PY'
import autoresearch.learning.feedback_store as fs
fs.add_proposal("gate", "cap_floor 30→20 亿",
                rationale="""本日 missed_l0 中 N 只为 20–30亿 成长次新,门槛误杀""",
                diff_sketch="""screen_market 硬门 cap_floor 默认 30 → 20""")
PY
```
prompt 规则改动须按 **writing-skills** 测过再上线。

**4.5 起草 prompt_patch(经验 → 提示词补丁,结构性建议的一种)**
判断层(L2/L3/L4)反复踩同一坑、且病因是 prompt/playbook 文案本身(不是权重/门槛能治)才起草——
门槛:**同型失误 ≥2 次**(同一诊断连续多日重现,如「同日配对节」或「L3 错杀验尸节」连续两次指向
同一段文案)**+ 账本读数支撑**(gate_ledger/channel_ledger 等确定性账本能量化这坑的代价,不是拍脑袋)。
`fs.add_prompt_patch` 自带三重校验(target_file 必须存在;**契约锚字符串一个都不能被删**——
`_CONTRACT_ANCHORS`=卡契约 v3/超短口径/机构面网查/FINAL TRANSACTION PROPOSAL/Rubric建议/进入P4倾向
等 l4-card 机器契约锚,proposed_text 让任一消失直接 raise;open 状态 prompt_patch 计数≤5,超了先
清积压),只出建议不自动改文件:
```bash
uv run --no-sync python - <<'PY'
import autoresearch.learning.feedback_store as fs
fs.add_prompt_patch(
    target_file=".claude/skills/scan-retro/retro-playbook.md",
    anchor_text="""<定位旧文案的短锚句>""",
    current_text="""<现状文案原文>""",
    proposed_text="""<改写后文案,不得删契约锚>""",
    evidence=["同型失误1:07-05 诊断……", "同型失误2:07-09 诊断再现……", "gate_ledger ex>0 n=6"],
)
PY
```
施工(实际改文件)永远走人批——起草只落一条待审提案,不自动动文件;若目标是契约文件(agent 定义/
lite-playbook/SKILL/STAGES),施工后务必重跑 `tests/test_agent_defs.py`(锚同步)与 doc-lint 复检。

**5. 写经验(语义,自动注回下次)**
反复出现的诊断 → **已有 slug 直接 `upsert_lesson` 强化**;**起新 slug 前先 M2 裁决**(`similar_lessons` 召回 → 判 op → `adjudicate`),防 retro 日复日堆出重复/矛盾条:
```bash
uv run --no-sync python - <<'PY'
import autoresearch.learning.feedback_store as fs
cand = dict(slug="low_winner_reversal", scope=("global","*"),
            rule="""低获利盘(winner_rate<25)+主力净流入+低动量=反转候选,别因动量低就压在召回线外""",
            evidence=["retro 2026-06-19 missed_l1 群体特征","fwd_1_oo +X%"], confidence=0.6,
            regimes=["risk_off"])                             # 写新经验标 regime,防翻转后毒害全域
for s in fs.similar_lessons(cand["rule"], cand["scope"], regimes=cand["regimes"])[:3]:
    print("  近似:", s["id"], "|", s["rule"][:40])
fs.adjudicate("ADD", cand)     # 无强相似→ADD;精化→UPDATE(target_id=,保id/MTM);矛盾→DELETE(取代);已表达→NOOP
PY
```

**5.5 行业备忘录(月度蒸馏,记忆中层)**
每 ≥20 个 scan 日(或月末)一次:通读当月 details 卡片,把**行业级反复出现的事实**
(估值区间常态/哪道 OW 门高频触发/共性坑位)蒸馏成 1–2 句/行业,`upsert_memo` 落
`sector_memos.jsonl`(覆盖式,别累积成散文)→ 下月该行业出现时自动注入 L4 简报
(`render_memo_line`)与 L3 prompt(`render_memo_block`)。**铁律同档案:历史事实非方向。**
```bash
uv run --no-sync python - <<'PY'
from autoresearch.learning.sector_memo import upsert_memo
upsert_memo("半导体", "fwd PE 常年 100+;CFO/FCF 门高频(紫光国微三度);解禁潮 Q3", "2026-07-31")
PY
```

**6. retro 报告 + 标记完成**
写 retro 报告到**被复盘扫描的运行目录**(`retro._report_dir_for(date)` 据 manifest.analysis_date 定位,与该次 `summary.md` 同级):`reports/scan/<YYYYMMDD>_<HHMM>/retro_<复盘HHMM>.md`,含:① 漏斗各段对赢家命中率(引 stage_stats)② 漏判赢家 top + **系统性病因**(第2步)③ 已自动落地的权重变化(引 changelog)④ 待批建议 ⑤ 新增/强化经验。然后:
```bash
uv run --no-sync python -c "import sys;sys.path.insert(0,'scripts');import autoresearch.learning.retro as retro;retro.mark_done('2026-06-19')"
```
用户可对 retro 报告再 `/feedback` → 二次校正(闭环)。

## 边界
- 仅权重自动落地;门槛/因子/prompt **只出建议**。
- 消息脉冲赢家不计入系统性结论与重标定。
- 量大(多日积压)可用 **workflow** 并行各日(需用户显式开启);否则逐日 in-session。

---
> 设计沿革(可选背景,删除不影响运行):`docs/specs/2026-06-20-closed-loop-learning-design.md` §3.2(复盘归因闭环)/ §5(半自动边界)。

# scan-retro playbook — 6 步复盘自迭代

> 规格:`docs/specs/2026-06-20-closed-loop-learning-design.md` §3.2 §5。
> 确定性归因:`scripts/retro.py`;知识库:`scripts/feedback_store.py`;重标定:`scripts/factor_lab.py`。

## 漏斗复盘一图
```
D 的报告(事前) ──对──> D 当日已实现 fwd_1_oo(事后)
  每只赢家分桶:caught / recalled_cut(L2-L3误判) / missed_l1(权重压低) / missed_l0(门槛误杀) / false_positive(误买)
  → 三段药:门槛 / 权重 / AI;消息脉冲单独标,不入重标定样本
  → ① 自动重标定(权重) ② 出建议(结构) ③ 写经验
```

## 6 步

**1. 找未复盘日 + 归因(确定性)**
```bash
uv run --no-sync python - <<'PY'
import sys; sys.path.insert(0, "scripts")
import retro
for d in retro.pending_days():           # 有报告+有面板+fwd已实现+未done 的 scan 日
    attr = retro.attribute(d)            # 写 context/scan/<d>/retro/attribution.csv
    retro.write_retro_input(d, attr)     # 写 retro_input.md(stage_stats + 漏判赢家因子行 + 对照)
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

**3. 自动落地:权重重标定 + 审计**(仅这一项自动改线上)
```bash
uv run --no-sync python - <<'PY'
import sys; sys.path.insert(0, "scripts")
import retro
r = retro.recalibrate_and_log("2026-06-19")   # 快照旧权重 → factor_lab.calibrate(多日滚动+收缩) → changelog.jsonl
print("recalibrated:", r["before_sha"], "→", r["after_sha"], "| n_dates", r["n_dates"])
print("top 变化:", r["top_changes"][:5])
PY
```
> **绝非单日翻权重**:calibrate 跑的是多日面板 + 申万层级收缩;单日只是把样本并进去让权重平滑漂移。weights 异常可 `weights.<sha>.json` 回滚(Phase 3)。

**4. 出建议(结构性,待你批准——不自动改)**
门槛/新因子/prompt 规则类改动 → 写 `proposals.jsonl`:
```bash
uv run --no-sync python - <<'PY'
import sys; sys.path.insert(0, "scripts")
import feedback_store as fs
fs.add_proposal("gate", "cap_floor 30→20 亿",
                rationale="""本日 missed_l0 中 N 只为 20–30亿 成长次新,门槛误杀""",
                diff_sketch="""screen_market 硬门 cap_floor 默认 30 → 20""")
PY
```
prompt 规则改动须按 **writing-skills** 测过再上线。

**5. 写经验(语义,自动注回下次)**
反复出现的诊断 → `upsert_lesson`(同 slug 自动强化):
```bash
uv run --no-sync python - <<'PY'
import sys; sys.path.insert(0, "scripts")
import feedback_store as fs
fs.upsert_lesson("low_winner_reversal", ("global","*"),
                 rule="""低获利盘(winner_rate<25)+主力净流入+低动量=反转候选,别因动量低就压在召回线外""",
                 evidence=["retro 2026-06-19 missed_l1 群体特征","fwd_1_oo +X%"], confidence=0.6)
PY
```

**6. retro 报告 + 标记完成**
写 `reports/scan/<YYYYMMDD>/retro_<HHMM>.md`,含:① 漏斗各段对赢家命中率(引 stage_stats)② 漏判赢家 top + **系统性病因**(第2步)③ 已自动落地的权重变化(引 changelog)④ 待批建议 ⑤ 新增/强化经验。然后:
```bash
uv run --no-sync python -c "import sys;sys.path.insert(0,'scripts');import retro;retro.mark_done('2026-06-19')"
```
用户可对 retro 报告再 `/feedback` → 二次校正(闭环)。

## 边界
- 仅权重自动落地;门槛/因子/prompt **只出建议**。
- 消息脉冲赢家不计入系统性结论与重标定。
- 量大(多日积压)可用 **workflow** 并行各日(需用户显式开启);否则逐日 in-session。

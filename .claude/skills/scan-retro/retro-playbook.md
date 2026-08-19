# scan-retro playbook — 6 步复盘自迭代

> **路径约定**:`$CTX`/`$RPT` = 本引擎工作区根(Claude→`context_claude`/`reports_claude`,Codex→`context_codex`/`reports_codex`;shell 里 `CTX=context_${AUTORESEARCH_ENGINE:-claude}`,`RPT=reports_${AUTORESEARCH_ENGINE:-claude}`)。数据湖 `lake/` 两引擎共享。Read/Write 工具调用时把 `$CTX`/`$RPT` 代入具体目录名。

> **本文 + `retro.py` / `feedback_store.py` / `factor_lab.py` 自足,无需 `docs/specs/`。** 确定性归因:`retro.py`;知识库:`feedback_store.py`;重标定:`factor_lab.py`。本文是 6 步操作手册。

## 漏斗复盘一图
```
D 的报告(事前) ──对──> D 当日已实现 gap_c1_o2(事后,超短隔夜主尺;fwd_2_oc/fwd_1_oo 降参考尺)
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
    attr = retro.attribute(d)            # 写 $CTX/scan/<d>/retro/attribution.csv
    retro.write_retro_input(d, attr)     # 写 retro_input.md(stage_stats 各段命中率 + 漏判赢家因子行 + 对照 + F·stage_eval 各阶段 agent edge + E2·promotion_candidates 经验升门候选)
    print("ready:", d)
PY
```
对每个待复盘日 D,读 `$CTX/scan/<D>/retro/retro_input.md`。

**2. Claude 诊断:三段药 + 分离消息脉冲**(核心,就是"涨得好的为什么没筛出来")
对 `missed_l0 / missed_l1 / recalled_cut` 三桶的赢家,**成群**(非逐只)对比 caught 样本,落到因子说清**系统性病因**:
- **missed_l0(门槛误杀)**:被市值地板/ST/次新/北交所剔了?群体特征(如"普遍 20–30亿次新成长")→ 病因=门槛过严。
- **missed_l1(权重压低)**:在召回池但 composite 排到召回线外。它们共有什么被低估的因子?(如"低获利盘+主力进场+低动量的反转票,被动量主导的复合分压住")→ 病因=权重/因子方向。
- **recalled_cut(L2-L3 误判)**:召回了却被 AI cut。当时的 L2/L3 理由错在哪?(对照『因子方向经验校准』,是不是又踩了 winner_rate/过热的坑)→ 病因=判断规则。
- **分离消息脉冲**:涨停/一字/停复牌复牌/巨量异动驱动的赢家 ≠ 选股失败 → 标 `news_pop`,**排除出重标定样本与"系统性漏判"结论**(不可预测,别拿去惩罚打分)。
- **T+5 盲区节(swing 口径,长线参考非主尺)**:与主尺(T+2/`gap_c1_o2`)节并排读——L3/L4 现行主尺是超短隔夜 `gap_c1_o2`(2026-08-05 用户裁定,取代 2026-07-10 裁定的 `fwd_2_oc`;两者都非 swing);若 T+5 missed_l1 持续显著多于主尺,仅记录供长线参考,horizon 之争(pr_20260702_001)已裁定 rejected,不再作切 horizon 依据。
- **L3 错杀验尸节**:错杀群体的 `risk` 文本共性 = L3 系统性偏见候选(如反转市对"获利盘满"的过度恐惧);反复出现 → 第 5 步写 lesson(自动注回 L3 校准块)。**错杀=0 且主尺(T+2)missed_l1 很大 → 病在召回线不在 L3,别冤枉判断层;T+5 missed_l1 仅作长线参考,不替代主尺判断。**
- **同日配对节(M1·ExpeL 控制变量)**:读 `$CTX/scan/<D>/retro/_retro_pairs.csv`(T+2 口径,D+2 即产出,不再等 T+5)。每行 = **同一天**的一对:`fail`(评级最高档但 T+2 跌)vs `win`(同日被门拦/漏召回但 T+2 涨),同 industry 最近邻优先(`matched_on`)。同日 = regime/地形/注入 lessons/漏斗参数全恒定 → diff 只剩标的特征与判断,`d_*` 因子差(fail − win:如 `d_winner_rate>0` = 我们买的获利盘更满、`d_momentum>0` = 追了动量)**直接指向判断偏差**。把反复出现的差蒸馏成 lesson candidate → 第 5 步走 M2 `adjudicate` 落库。0 买日也有(fail 侧用当日最高评级档代理),别因没买单就跳过。
- **floor 自然实验节**:救回组 ≈ merit 组 → floor 免费维持;救回组持续显著弱于被挤掉组 → 第 4 步提 floor 参数复审建议(人批)。
- **经验 MTM 节**:带 guard 的经验已被机判自动记账(support/refute + confidence 机械升降);**无 guard 的经验由你逐条判**——今天的归因数据支持还是打脸这条 rule?`fs.mtm_update(id, 'support'|'refute', day)` 记账,拿不准就跳过(别硬判)。refute 达阈会自动出"摘 guard/退休"提名(人批)。**写新经验时标 regime**:`upsert_lesson(..., regimes=['risk_off'])`——regime 条件真理别让它在翻转后毒害全域。
- **门审计节**:被拦票的 ex<0 = 拦对;跨日看 `uv run --no-sync python -m autoresearch.learning.gate_ledger`(→ `$RPT/learning/gate_ledger.md`)。某门持续 ex>0 且样本 ≥5 → 第 4 步提松阈/退役建议。**门也要 mark-to-market,别让它无问责地累积成保守棘轮。**
- **待裁决 proposals 节**:>14 天 ⚠ 的逐条给用户裁决建议(采纳/拒绝/再观察),别让看板变摆设。
- **裁决 checklist 新增(P0-7/C20)**:凡三门/买侧提案,除 n≥20 外还须覆盖 ≥2 温度相位(冰点/修复/发酵/高潮/退潮,`$CTX/learning/temperature.csv` 124 日回填在手,相位标签免费——查 `uv run --no-sync python -m autoresearch.scan.temperature show <date>`);单 regime/单相位攒再多 n 也裁不动三门(近 6 判定日全 risk_off 就是反例)——相位覆盖不足,即便 n 达标也只标"证据不足,再观察",不算可裁。

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
门槛/新因子/prompt 规则类改动 → 写 `proposals.jsonl`。**quota 接线**:`channel_ledger` 某路 `n_days≥3` 且 `unique_excess_t2` 持续负(主尺 T+2,2026-07-10 裁定;t5 仅参考列)→ 用 `channel_ledger.propose_quota_adjustments` 写降 quota 提议(单步±25%,advisory,基线读 scan_config.jsonc 的 channel_quotas);持续强的路(如 momentum)同理提升 quota。floor 复审建议同此步。示例:
```bash
uv run --no-sync python - <<'PY'
import autoresearch.learning.feedback_store as fs
fs.add_proposal("gate", "cap_floor 30→20 亿",
                rationale="""本日 missed_l0 中 N 只为 20–30亿 成长次新,门槛误杀""",
                diff_sketch="""screen_market 硬门 cap_floor 默认 30 → 20""")
PY
```
prompt 规则改动须按 **writing-skills** 测过再上线。

**4.5 判断层文案病(prompt/playbook 本身该改)→ 写进诊断正文与提案,不动手改文件**
判断层(L2/L3/L4)反复踩同一坑、且病因是 prompt/playbook 文案本身(不是权重/门槛能治)——
门槛照旧:**同型失误 ≥2 次**(同一诊断连续多日重现,如「同日配对节」或「L3 错杀验尸节」连续两次
指向同一段文案)**+ 账本读数支撑**(gate_ledger/channel_ledger 等确定性账本能量化这坑的代价,
不是拍脑袋)。达门槛就用第 4 步的 `fs.add_proposal("prompt_rule", ...)` 起草一条提案,**证据里点名
是哪个文件的哪段文案、建议怎么改**,留给用户在开发会话里落地。
> 2026-08-13 用户裁定:此前的 `prompt_patch` 补丁载体(target_file + 施工处方)已**退役**——
> 复盘不动刀,skill/prompt 文本只在用户显式发起的开发会话中修改。

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
写 retro 报告到**被复盘扫描的运行目录**(`retro._report_dir_for(date)` 据 manifest.analysis_date 定位,与该次 `summary.md` 同级):`$RPT/scan/<YYYYMMDD>_<HHMM>/retro_<复盘HHMM>.md`,含:① 漏斗各段对赢家命中率(引 stage_stats)② 漏判赢家 top + **系统性病因**(第2步)③ 已自动落地的权重变化(引 changelog)④ 待批建议 ⑤ 新增/强化经验。然后:
```bash
uv run --no-sync python -c "import sys;sys.path.insert(0,'scripts');import autoresearch.learning.retro as retro;retro.mark_done('2026-06-19')"
```
用户可对 retro 报告再 `/feedback` → 二次校正(闭环)。

## 批量补诊断(欠账 ≥2 日时)

`retro.attribution_pending()`(欠归因)与 `retro.pending_days()`(欠诊断——`retro_input.md`
已备料只差这步)是两笔不同的账(Wave11-A7,详见 `retro.py` 两函数 docstring 与 CLI
`retro pending` 的两段输出)。**本节只管后者**:欠归因是确定性计算,`nightly_close` 每晚
自动补,人不该也不用手动介入;真正要人(Claude session)接手的,是 `retro pending` 报出来的
「诊断欠账(已备料)」段。

```bash
uv run --no-sync python -m autoresearch.learning.retro pending   # 先看「诊断欠账」段有几天
```

**合诊而非逐日重复**:一次诊断吃 **≤5 日**的 `retro_input.md`(同一个 context 通读,跨日
对比更容易看出系统性病因——同一批门槛/权重/prompt 问题往往连续多日重现,拆开单日看只会把
同一个病因诊断 N 遍;>5 日先分批,不要一次塞爆 context)。诊断内容仍是第 2 步的三段药(门槛/
权重/AI)+ 分离消息脉冲,只是证据来源从 1 天变成 ≤5 天,与 t1-review 快环"合诊"同一哲学
(跨卡模式只有通读全部才看得见)。

**逐日收尾,不是批量收尾**:合诊归合诊,但落盘与 `mark_done` 仍按**每日**走——诊断完一天就
把该日 retro 报告落到 `$RPT/scan/<该日 run_id>/retro_<HHMM>.md`(第 6 步同款),立刻对
该日调用 `retro.mark_done(<该日>)`(触发 `decay_lessons` 记忆防腐),再处理批次里下一天。
不要攒到整批诊断完再一次性 `mark_done`——那样任何一天中途出岔子都会连累已经诊断完的日子
一起没留痕,`decay_lessons` 的幂等防腐节奏也会被平白拖后。

**清账判据**:批量补完后重跑上面的 CLI,「诊断欠账(已备料)」段为空 = 清完;「归因欠账」段
不是本流程的验收对象(那是 `nightly_close` 的活,见上)。

**触发词**:「补复盘欠账」。

## 边界
- **复盘不动刀(2026-08-13 用户裁定)**:复盘/反馈流程一律不得编辑 .claude/ 与 CLAUDE.md/AGENTS.md;skill/prompt/agent/workflow 文本只在用户显式发起的开发会话中修改。本 playbook 六步的产出止于**账本 / 经验 / 权重 / 提案 / 复盘报告**——诊断出 prompt 文案该改,写进提案与诊断正文,不要动手改文件。spec:`docs/specs/2026-08-13-retro-skill-selfmodify-removal-design.md`。
- **开发会话守则**(不属本流程,写在这里供交接):skill 契约文件(agent 定义 / lite-playbook / SKILL.md / STAGES.md)在开发会话改动后必跑 `uv run --no-sync python -m pytest tests/test_agent_defs.py tests/test_skill_docs_refs.py`(锚同步 + doc-lint)。
- 仅权重自动落地;门槛/因子/prompt **只出建议**。
- 消息脉冲赢家不计入系统性结论与重标定。
- 欠账 ≥2 日的批量诊断见上「批量补诊断」节(≤5 日/次合诊 + 逐日 `mark_done`);否则逐日 in-session。

## 双轨语义:注入锚用收缩值 / 裁决门槛用硬 n
**两套语义不混**(P0-3/P0-7 拍板):
- **注入锚**——渲染进卡片/简报给 LLM 读的数字(`l4_card.write_base_rates`、`cross_calib.flip_stats`、`buy_ledger.write_target_calib` 的 regime×lane 细分格、`gate_ledger` tail_rate)用**收缩值**(shrinkage,见 `autoresearch/learning/shrink.py`)——小样本桶不再裸给 LLM 一个虚高/虚低的原始比率,往全局先验收缩后再标注样本量。
- **裁决门槛**——改不改机制的人批标准(proposals 裁决、lesson retire 提名、重标定 cadence,含上面「待裁决 proposals 节」的 n≥20)仍用**硬 n**;收缩值只是让注入的数字更稳,不能反过来当成"样本变多了"去降裁决门槛。
- **n<3 绝对禁注**:分母小到这个地步连收缩值都不可信,直接不渲染该行——这条底线两套语义都认,与裁决门槛高低无关。

读 retro 报告或审 proposals 时先分清手上的数字是哪一套:给 LLM 看的收缩估计,还是给人裁决用的硬计数。

---
> 设计沿革(可选背景,删除不影响运行):`docs/specs/2026-06-20-closed-loop-learning-design.md` §3.2(复盘归因闭环)/ §5(半自动边界)。

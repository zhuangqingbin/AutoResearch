# 记忆与门的 mark-to-market + 注入治理 — 设计

- 状态:设计定稿(待实现)
- 日期:2026-07-02
- 触及:`autoresearch/learning/feedback_store.py`、`learning/self_review.py`、`learning/retro.py`、`learning/gate_ledger.py`(新)、`scan/assemble.py`、`.claude/skills/scan-retro/retro-playbook.md`
- 关联:brainstorm R1/R2+R7/R3/R4/R6;[[2026-07-02-scan-retro-depth-metrics]](retro 节模式先例)

## 1. 背景(修正后的现状)

- 语义记忆(lessons)**有时间衰减**(`decay_lessons`:>30 天未强化 conf −0.1、<0.3 退休)但**从未被任何节奏调用**(只在 selftest);且**没有证据型反驳**——经验只被时间稀释,从不被市场打脸。
- lessons scope 只有 kind/value 一维(global/sector/industry/ticker),**无 regime 维**——"主力流入在避险市反向"这类 regime 条件真理,在 regime 翻转后会整体变毒药(权重已 regime 分桶,语义记忆还没有)。
- self_review 硬门(固定红线 + lesson guard)**拦了不留痕**:failures 只进 banner 文本,无结构化日志 → 永远无法度量"门拦得对不对";门只积累不退役(保守棘轮的机械版)。
- proposals 无看板:open 条目没人看见(07-02 当天 4 条 open 里 2 条早该 resolve)。
- 注入检索 = scope 命中 + confidence 降序,**无 cap**——经验库长大后校准块必然膨胀成 prompt 噪声。

## 2. 设计

### R1 · lesson regime 作用域

- lesson 记录加**可选** `regimes: list[str]`(如 `["risk_off","range"]`;缺省/空 = 全 regime,老记录兼容)。
- `upsert_lesson(..., regimes=None)` 透传;`lessons_for(query_scopes, regime=None)`:`regime` 给定且 lesson 带 `regimes` → 不含则过滤掉;`regime=None` → 行为不变(parity)。
- `render_calibration_block(..., regime=None)` 新增 kwarg 透传;编排层(L3 prompt 组装)传 `classify_regime(当日帧).label`(playbook 注明)。

### R2+R7 · 经验 mark-to-market(证据型反驳 + 机械 confidence)

- lesson 记录加 `mtm: {support:int, refute:int, last:str}`(缺省无,首次更新创建)。
- **`mtm_update(lesson_id, verdict, day, note="") -> dict`**(feedback_store):verdict ∈ {support, refute};幂等(同 lesson 同 day 同 verdict 只记一次);confidence 机械更新:support +0.03、refute **−0.08**,clip [0.20, 0.95](反驳惩罚 > 支持奖励:记忆宁可谦逊)。
- **降级只提名不自动**(与"仅权重自动落地"哲学一致):`refute≥3 ∧ refute>support` → 自动 `add_proposal("lesson", "摘除 guard/退休提名: <id>", rationale=mtm 计数)`,**人批**后才 retire/摘 guard。
- **机判部分**(retro 侧,`retro.mtm_check_guards(attr, lessons) -> list[dict]`):对每条带 guard 的 active lesson,把 guard 条件 `{field,op,value}` 应用到当日 attribution 全帧 → 满足条件组的 `fwd_1_oo` 均值对全市场均值的 excess;**n≥5** 才判:excess<0 → support(拦得对),>0 → refute;n<5 → skip。判定自动调 `mtm_update`。
- **人判部分**(playbook):无 guard 的 lesson 列进 retro_input"经验 MTM"节,retro 的 Claude 步骤逐条 support/refute/跳过(调 `mtm_update`)。
- **decay 接入节奏**:`retro.mark_done()` 尾部调 `fs.decay_lessons()`(幂等,每完成一次复盘做一次防腐)。

### R3 · 门审计(gate fires 留痕 + 后市对照)

- `self_review.review()` 的每条 failure **加可选 `code` 字段**(逐 finalist 检查知道 code;全局检查〔覆盖/空泛〕code=None)。additive,老消费者不破。
- `assemble._self_review_banner` 拿到 review 结果后**幂等落** `context/scan/<date>/gate_fires.csv`(`date,code,check,severity,detail`;每次 assemble 覆写——staging 模式)。无 failures → 写空表头文件(区分"没拦"与"没跑")。
- **retro 侧**:`retro.gate_audit(attr, scan_dir) -> pd.DataFrame`(join gate_fires × fwd:被拦票的 fwd_1/fwd_5 vs 市场)→ retro_input 节"门审计(被拦的后来怎么走)":excess<0 = 拦对了。
- **跨日**:`learning/gate_ledger.py`(镜像 zero_buy_ledger):`roll()` 聚合各日 gate_fires×attribution → 每门 n_fires/mean_excess_1/5/命中率 → `reports/learning/gate_ledger.md`;某门持续 excess>0 → 建议(人批)松阈或退役。

### R4 · proposals 看板

- `fs.open_proposals(today) -> list[{id, age_days, kind, summary}]`(按 age 降序)。
- `write_retro_input` 增节"待裁决 proposals"(>14 天标 ⚠;空 → 不加节)。

### R6 · 注入治理(top-K + 排序细化)

- `lessons_for` 排序细化:confidence 降序 → `last_reinforced` 降序 → scope 精度(ticker/industry > sector > global)。
- `render_calibration_block` 经验条目 **cap K=8**(反馈已有 k=3);超出打一行"(另有 N 条低置信经验,见 lessons.jsonl)"。**parity**:当前库 4 条 ≤ K → 输出逐字不变;无命中回退基线逐字不变。

## 3. 测试(合成,无网络)

- `tests/learning/test_feedback_mtm.py`:R1 regime 过滤(带/不带/None 三态)+ 老记录兼容;R2 mtm_update 幂等/confidence 升降 clip/达阈自动提名 proposal;R4 open_proposals 天龄;R6 cap(9 条→8+溢出行)与 ≤K parity、排序 tiebreak。
- `tests/learning/test_gate_audit.py`:mtm_check_guards(n≥5 判/ n<5 skip/ support 与 refute 两态);gate_audit join;gate_ledger roll/render 空目录优雅。
- `tests/scan/` 增:assemble 跑完落 gate_fires.csv(有 fail 与无 fail 两态);self_review failure 带 code。
- 既有 test_self_review/test_feedback_store/test_assemble 全绿(additive 兼容)。

## 4. 非目标

- **不自动** retire/摘 guard(只提名,人批);不动评级逻辑。
- 不建 prompt_rule A/B harness(R9:日频样本饥饿,靠 stage_eval 趋势 + 定性)。
- 不上 embedding 检索(scope+confidence 对这个量级够用)。
